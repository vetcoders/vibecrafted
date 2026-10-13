from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
import sysconfig
import threading
import time
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from vibecrafted_core import server_supervisor as supervisor

ROOT = Path(__file__).resolve().parents[2]


def config(tmp_path, port):
    launcher = tmp_path / "vibecrafted"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o755)
    return supervisor.SupervisorConfig(
        paths=supervisor.SupervisorPaths.create(
            home=tmp_path / "vc",
            runtime_home=tmp_path / "runtime",
            operator_home=tmp_path / "user",
        ),
        launcher=launcher,
        host="127.0.0.1",
        port=port,
        interval=0.05,
        maximum_backoff=0.2,
        command_timeout=2,
    )


def test_guardian_sse_runs_inside_supervisor_and_stops(tmp_path, monkeypatch):
    server_stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            assert self.path == "/api/control/events"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                while not server_stop.is_set():
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    server_stop.wait(0.05)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = config(tmp_path, server.server_port)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(cfg.paths.home))
    stop = threading.Event()
    guardian = supervisor.EmbeddedGuardian(cfg, stop)
    try:
        with supervisor._SupervisorLease(
            cfg.paths,
            service_managed=False,
            identity=supervisor._supervisor_identity(launcher=cfg.launcher),
        ):
            guardian.start()
            assert guardian.ready.wait(5), guardian.error
            assert guardian.publish()
            assert supervisor.embedded_guardian_healthy(
                cfg.paths, server_url=cfg.endpoint
            )
            payload = json.loads(
                (cfg.paths.server_dir / "guardian.execution.json").read_text()
            )
            assert payload["owner_pid"] == os.getpid()
            assert not (cfg.paths.server_dir / "guardian.pid").exists()
            guardian.stop()
            assert not supervisor.embedded_guardian_healthy(cfg.paths)
            assert not guardian.thread.is_alive()
    finally:
        stop.set()
        server_stop.set()
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_guardian_thread_failure_revokes_readiness(tmp_path, monkeypatch):
    from vibecrafted_core import guardian as module

    cfg = config(tmp_path, 1)

    def fail(*args, **kwargs):
        kwargs["ready_callback"]()
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(module, "main", fail)
    worker = supervisor.EmbeddedGuardian(cfg, threading.Event())
    with supervisor._SupervisorLease(
        cfg.paths,
        service_managed=False,
        identity=supervisor._supervisor_identity(launcher=cfg.launcher),
    ):
        worker.start()
        worker.thread.join(3)
        assert not worker.publish()
        assert not supervisor.embedded_guardian_healthy(cfg.paths)
        assert worker.error == "guardian failed: RuntimeError"


def test_native_host_has_real_product_process_identity(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "native_renderer", ROOT / "scripts/render-python-entrypoint-launchers.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "generation"
    (root / "bin").mkdir(parents=True)
    # A fixture borrows the local dev interpreter read-only. Production embeds
    # the pinned library/stdlib under the signed generation, with no symlinks.
    configured_library = str(sysconfig.get_config_var("LDLIBRARY"))
    if sys.platform == "darwin" and sysconfig.get_config_var("PYTHONFRAMEWORK"):
        library_source = (
            Path(sysconfig.get_config_var("PYTHONFRAMEWORKPREFIX")) / configured_library
        )
    else:
        library_source = Path(sysconfig.get_config_var("LIBDIR")) / configured_library
    assert library_source.is_file(), library_source
    # The payload owns a flat library filename even when the dev interpreter
    # reports its library relative to a Python.framework prefix.
    library = library_source.name
    if sys.platform == "darwin":
        library_root = root / "python/lib"
        library_root.mkdir(parents=True)
        shutil.copyfile(library_source, library_root / library)
        # Borrow only the stdlib; native code is copied into this generation.
        (
            library_root / f"python{sys.version_info.major}.{sys.version_info.minor}"
        ).symlink_to(sysconfig.get_path("stdlib"), target_is_directory=True)
        subprocess.run(
            [
                "install_name_tool",
                "-id",
                f"@rpath/{library}",
                str(library_root / library),
            ],
            check=True,
        )
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(library_root / library)],
            check=True,
        )
    else:
        (root / "python").symlink_to(Path(sys.base_prefix), target_is_directory=True)
    source = root / "vibecrafted-core"
    source.mkdir()
    (source / "vc_fixture.py").write_text("""import json,os,sys,time
def main():
 with open(sys.argv[1], 'w') as out:
  json.dump({'pid':os.getpid(),'executable':sys.executable,'root':os.environ['VIBECRAFTED_RUNTIME_ROOT']},out)
 time.sleep(20)
 return 0
""")
    binary = root / "bin/vc-server-supervisor"
    module.render_native_host(binary, "vc_fixture:main", library)
    # Internal helpers can reuse sys.executable as an actual interpreter.
    compatibility = subprocess.run(
        [str(binary), "-c", "import sys; print(sys.executable)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert compatibility.stdout.strip() == str(binary)
    python_host = root / "bin/vc-python"
    module.render_native_host(python_host, "vc_fixture:main", library, python_only=True)
    version = subprocess.run(
        [str(python_host), "--version"], capture_output=True, text=True, check=True
    )
    assert version.stdout.startswith("Python ")
    for flags in (["-B"], ["-I"], ["-E"], ["-u"]):
        check = subprocess.run(
            [
                str(python_host),
                *flags,
                "-c",
                "import sys; print('zażółć'); raise SystemExit(7)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert check.returncode == 7
        assert check.stdout.strip() == "zażółć"
    recursion = subprocess.run(
        [
            str(binary),
            "-c",
            "import subprocess,sys; raise SystemExit(subprocess.run([sys.executable,'-c','raise SystemExit(9)']).returncode)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert recursion.returncode == 9
    if sys.platform == "darwin":
        # dyld search overrides must never select the host's ambient library,
        # even when it exposes the same ABI and the payload copy is present.
        overridden = subprocess.run(
            [str(binary), "-c", "raise SystemExit(0)"],
            env={**os.environ, "DYLD_LIBRARY_PATH": str(library_source.parent)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert overridden.returncode == 78
        assert "CPython must load from this generation" in overridden.stderr
    ready = root / "ready.json"
    process = subprocess.Popen(
        [str(binary), str(ready)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 5
        while (
            not ready.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert ready.exists(), process.communicate(timeout=1)
        payload = json.loads(ready.read_text())
        assert payload == {
            "pid": process.pid,
            "executable": str(binary),
            "root": str(root),
        }
        observed = subprocess.run(
            ["/bin/ps", "-p", str(process.pid), "-o", "comm="],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert observed.endswith("vc-server-supervisor")
        assert binary.read_bytes()[:2] != b"#!"
        parents = subprocess.run(
            ["/bin/ps", "-ax", "-o", "ppid="],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert str(process.pid) not in parents
    finally:
        process.terminate()
        process.communicate(timeout=3)
    relocated = tmp_path / "relocated"
    root.rename(relocated)
    relocated_binary = relocated / "bin/vc-server-supervisor"
    relocated_probe = subprocess.run(
        [str(relocated_binary), "-c", "import sys; print(sys.executable)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert relocated_probe.stdout.strip() == str(relocated_binary)


def test_fixed_terminal_host_refuses_generation_app_identity(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "installer_runtime_owners", ROOT / "scripts/vetcoders_install.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if sys.platform != "darwin":
        pytest.skip("fixed app identity is macOS only")
    module._FIXED_TERMINAL_HOST = (
        tmp_path / "Applications/Vibecrafted.app/Contents/MacOS/vc-terminal"
    )
    with pytest.raises(
        module.RuntimeInstallRefusal, match="pending paired notarized app handoff"
    ):
        module._product_terminal_host(
            generation=tmp_path / "releases/version", app_root=tmp_path / "other.app"
        )
    assert not module._is_product_bundle_terminal_host(
        tmp_path / "libexec/vc-terminal.app/Contents/MacOS/alacritty"
    )


@pytest.mark.parametrize("damage", ["missing", "malformed", "symlink", "unmatched"])
def test_fixed_host_admission_preserves_active_runtime_and_config(
    tmp_path, monkeypatch, damage
):
    spec = importlib.util.spec_from_file_location(
        "installer_admission_" + damage, ROOT / "scripts/vetcoders_install.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    host = tmp_path / "Applications/Vibecrafted.app/Contents/MacOS/vc-terminal"
    host.parent.mkdir(parents=True)
    shutil.copyfile("/usr/bin/true", host)
    host.chmod(0o755)
    contents = host.parent.parent
    (contents / "Resources").mkdir()
    (contents / "Resources/Vibecrafted.icns").write_bytes(b"icon")
    info = contents / "Info.plist"
    info.write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "io.vetcoders.vibecrafted",
                "CFBundleExecutable": "Vibecrafted",
                "CFBundleName": "Vibecrafted",
                "CFBundleDisplayName": "Vibecrafted",
                "CFBundleIconFile": "Vibecrafted.icns",
            }
        )
    )
    monkeypatch.setattr(module, "_FIXED_TERMINAL_HOST", host)

    def signer(path):
        return hashlib.sha256(
            (host if path.is_dir() else path).read_bytes()
        ).hexdigest(), "FIXTURE_TEAM"

    monkeypatch.setattr(module, "_signed_terminal_identity", signer)
    payload = tmp_path / "payload"
    (payload / "libexec").mkdir(parents=True)
    candidate = payload / "libexec/vc-terminal"
    shutil.copyfile(host, candidate)
    candidate.chmod(0o755)
    (payload / "VERSION").write_text("4.4.0+gfixture\n")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    active = runtime / "active.json"
    active.write_text('{"prior":"usable"}\n')
    config_file = tmp_path / "home/.config/vibecrafted/config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('[server]\nbind_host="127.0.0.1"\n')
    before = active.read_bytes(), config_file.read_bytes()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if damage == "missing":
        host.unlink()
    elif damage == "malformed":
        info.write_text("not a plist")
    elif damage == "symlink":
        host.unlink()
        host.symlink_to(candidate)
    else:
        candidate.write_bytes(candidate.read_bytes() + b"different native code")
    with pytest.raises(
        module.RuntimeInstallRefusal, match="pending paired notarized app handoff"
    ):
        module._install_runtime_pack(
            Namespace(
                payload_root=str(payload), app_root=None, runtime_home=str(runtime)
            )
        )
    assert (active.read_bytes(), config_file.read_bytes()) == before
    assert not list(runtime.glob("releases/*"))


def test_secondary_terminal_executable_uses_main_bundle_identity(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("Foundation bundle semantics are macOS only")
    bundle = tmp_path / "Applications/Vibecrafted.app"
    macos = bundle / "Contents/MacOS"
    macos.mkdir(parents=True)
    (bundle / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "io.vetcoders.vibecrafted",
                "CFBundleExecutable": "Vibecrafted",
                "CFBundleName": "Vibecrafted",
                "CFBundlePackageType": "APPL",
            }
        )
    )
    source = tmp_path / "bundle-probe.m"
    source.write_text("""#import <Foundation/Foundation.h>
int main(void) { @autoreleasepool {
 NSBundle *b = [NSBundle mainBundle];
 printf("%s\\n%s\\n", [[b bundleIdentifier] UTF8String], [[b bundlePath] UTF8String]);
 return 0;
} }
""")
    subprocess.run(
        [
            "/usr/bin/clang",
            "-fobjc-arc",
            "-framework",
            "Foundation",
            str(source),
            "-o",
            str(macos / "vc-terminal"),
        ],
        check=True,
        capture_output=True,
    )
    # Both physical executable paths exist; no application/window is opened.
    shutil.copyfile(macos / "vc-terminal", macos / "Vibecrafted")
    result = subprocess.run(
        [str(macos / "vc-terminal")], check=True, capture_output=True, text=True
    )
    lines = result.stdout.splitlines()
    assert lines[0] == "io.vetcoders.vibecrafted"
    assert Path(lines[1]).resolve() == bundle.resolve()
