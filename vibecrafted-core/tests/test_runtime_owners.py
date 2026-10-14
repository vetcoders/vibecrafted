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


def test_native_host_isolates_environment_from_foreign_children_and_preserves_semantics(
    tmp_path,
):
    spec = importlib.util.spec_from_file_location(
        "native_renderer", ROOT / "scripts/render-python-entrypoint-launchers.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "generation"
    (root / "bin").mkdir(parents=True)

    configured_library = str(sysconfig.get_config_var("LDLIBRARY"))
    if sys.platform == "darwin" and sysconfig.get_config_var("PYTHONFRAMEWORK"):
        library_source = (
            Path(sysconfig.get_config_var("PYTHONFRAMEWORKPREFIX")) / configured_library
        )
    else:
        library_source = Path(sysconfig.get_config_var("LIBDIR")) / configured_library
    assert library_source.is_file(), library_source
    library = library_source.name

    if sys.platform == "darwin":
        library_root = root / "python/lib"
        library_root.mkdir(parents=True)
        shutil.copyfile(library_source, library_root / library)
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
    (source / "owned_core_mod.py").write_text("MAGIC = 42\n")

    python_host = root / "bin/vc-python"
    module.render_native_host(
        python_host, "owned_core_mod:main", library, python_only=True
    )

    # 1. Native ordinary core import passes
    core_check = subprocess.run(
        [
            str(python_host),
            "-c",
            "import owned_core_mod; assert owned_core_mod.MAGIC == 42",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert core_check.returncode == 0, core_check.stderr

    # 2. Native -I stdlib and identity, required stdlib modules pass from selected generation
    stdlib_check = subprocess.run(
        [
            str(python_host),
            "-I",
            "-c",
            (
                "import sys, encodings, binascii, ssl, sqlite3, datetime; "
                f"assert sys.executable == {str(python_host)!r}"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert stdlib_check.returncode == 0, stdlib_check.stderr

    # 3. Hostile cwd and hostile PYTHONPATH negative under isolated mode (-I)
    hostile_dir = tmp_path / "hostile"
    hostile_dir.mkdir()
    (hostile_dir / "hostile_payload.py").write_text(
        "raise RuntimeError('hostile code executed')\n"
    )

    hostile_cwd_check = subprocess.run(
        [str(python_host), "-I", "-c", "import hostile_payload"],
        cwd=hostile_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert hostile_cwd_check.returncode != 0
    assert (
        "ModuleNotFoundError: No module named 'hostile_payload'"
        in hostile_cwd_check.stderr
    )

    hostile_env_check = subprocess.run(
        [str(python_host), "-I", "-c", "import hostile_payload"],
        env={**os.environ, "PYTHONPATH": str(hostile_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert hostile_env_check.returncode != 0
    assert (
        "ModuleNotFoundError: No module named 'hostile_payload'"
        in hostile_env_check.stderr
    )

    # 3b. Actual native option handling and parent counterexamples with ambient canary
    ambient_dir = tmp_path / "ambient"
    ambient_dir.mkdir()
    (ambient_dir / "ambient_ISOLATION_canary.py").write_text("IS_LOADED = True\n")
    ambient_env = {**os.environ, "PYTHONPATH": str(ambient_dir)}

    # Counterexample 1: -I direct find_spec => False (control PASS)
    check_i = subprocess.run(
        [
            str(python_host),
            "-I",
            "-c",
            "import importlib.util; print(bool(importlib.util.find_spec('ambient_ISOLATION_canary')))",
        ],
        env=ambient_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_i.returncode == 0, check_i.stderr
    assert check_i.stdout.strip() == "False"

    # Counterexample 2: -W ignore -E -c find_spec => False (MUST False)
    check_we = subprocess.run(
        [
            str(python_host),
            "-W",
            "ignore",
            "-E",
            "-c",
            "import importlib.util; print(bool(importlib.util.find_spec('ambient_ISOLATION_canary')))",
        ],
        env=ambient_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_we.returncode == 0, check_we.stderr
    assert check_we.stdout.strip() == "False"

    # Counterexample 3: -X dev -I -c find_spec => False (MUST False)
    check_xi = subprocess.run(
        [
            str(python_host),
            "-X",
            "dev",
            "-I",
            "-c",
            "import importlib.util; print(bool(importlib.util.find_spec('ambient_ISOLATION_canary')))",
        ],
        env=ambient_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_xi.returncode == 0, check_xi.stderr
    assert check_xi.stdout.strip() == "False"

    # Permutations: combined flags and attached option values
    for flag_group in (
        ["-Wignore", "-E"],
        ["-Xdev", "-I"],
        ["-EI"],
        ["-IE"],
    ):
        check_comb = subprocess.run(
            [
                str(python_host),
                *flag_group,
                "-c",
                "import importlib.util; print(bool(importlib.util.find_spec('ambient_ISOLATION_canary')))",
            ],
            env=ambient_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert check_comb.returncode == 0, check_comb.stderr
        assert check_comb.stdout.strip() == "False"

    # Counterexample 4: ordinary -c code beginning `-1; __import__('ambient_ISOLATION_canary'); print('loaded')` => loaded
    check_ordinary = subprocess.run(
        [
            str(python_host),
            "-c",
            "-1; __import__('ambient_ISOLATION_canary'); print('loaded')",
        ],
        env=ambient_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_ordinary.returncode == 0, check_ordinary.stderr
    assert check_ordinary.stdout.strip() == "loaded"

    # Ordinary -c with non-isolated options (-u, -B)
    check_ub = subprocess.run(
        [
            str(python_host),
            "-u",
            "-B",
            "-c",
            "-1; __import__('ambient_ISOLATION_canary'); print('loaded')",
        ],
        env=ambient_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_ub.returncode == 0, check_ub.stderr
    assert check_ub.stdout.strip() == "loaded"

    # Delimiter -- stops option scanning
    check_delim = subprocess.run(
        [
            str(python_host),
            "-I",
            "--",
            "-c",
            "import sys; print('ignored')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_delim.returncode != 0

    # 4. Owned module doors -m and -c preserved
    m_check = subprocess.run(
        [str(python_host), "-m", "timeit", "-n", "1", "-r", "1", "1 + 1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert m_check.returncode == 0, m_check.stderr

    c_check = subprocess.run(
        [str(python_host), "-c", "print('preserved_c_door')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert c_check.returncode == 0
    assert c_check.stdout.strip() == "preserved_c_door"

    # 5. Foreign Python and screenscribe child isolation
    foreign_pythons = [
        Path("/Users/polyversai/.local/share/uv/tools/screenscribe/bin/python3"),
    ]
    foreign_python = next((p for p in foreign_pythons if p.is_file()), None)
    if foreign_python is None:
        foreign_venv = tmp_path / "foreign_venv"
        subprocess.run([sys.executable, "-m", "venv", str(foreign_venv)], check=True)
        foreign_python = foreign_venv / "bin/python3"

    direct_foreign = subprocess.run(
        [str(foreign_python), "-B", "-c", "import encodings"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert direct_foreign.returncode == 0, direct_foreign.stderr

    spawned_foreign = subprocess.run(
        [
            str(python_host),
            "-c",
            (
                "import subprocess, sys; "
                f"r = subprocess.run([{str(foreign_python)!r}, '-B', '-c', 'import encodings'], capture_output=True); "
                "sys.exit(r.returncode)"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert spawned_foreign.returncode == 0, spawned_foreign.stderr

    screenscribe_bin = Path("/Users/polyversai/.local/bin/screenscribe")
    if not screenscribe_bin.is_file():
        found = shutil.which("screenscribe")
        if found:
            screenscribe_bin = Path(found)
    if screenscribe_bin.is_file():
        direct_ss = subprocess.run(
            [str(screenscribe_bin), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert direct_ss.returncode == 0, direct_ss.stderr

        spawned_ss = subprocess.run(
            [
                str(python_host),
                "-c",
                (
                    "import subprocess, sys; "
                    f"r = subprocess.run([{str(screenscribe_bin)!r}, '--help'], capture_output=True); "
                    "sys.exit(r.returncode)"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert spawned_ss.returncode == 0, spawned_ss.stderr

    # 6. Fail-closed invalid payload: host refuses when stdlib is missing
    empty_root = tmp_path / "empty_generation"
    (empty_root / "bin").mkdir(parents=True)
    empty_lib = empty_root / "python/lib"
    empty_lib.mkdir(parents=True)
    shutil.copyfile(library_source, empty_lib / library)
    if sys.platform == "darwin":
        subprocess.run(
            [
                "install_name_tool",
                "-id",
                f"@rpath/{library}",
                str(empty_lib / library),
            ],
            check=True,
        )
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(empty_lib / library)], check=True
        )
    bad_host = empty_root / "bin/vc-bad-host"
    module.render_native_host(bad_host, "any:main", library, python_only=True)
    bad_check = subprocess.run(
        [str(bad_host), "--version"], capture_output=True, text=True, check=False
    )
    assert bad_check.returncode == 78
    assert "stdlib unavailable in this generation" in bad_check.stderr

    # 6b. Deterministic stdlib selection: ignores bogus python3.bad and fails closed when mismatched
    bogus_dir = library_root / "python3.bad"
    bogus_dir.mkdir()
    try:
        check_det = subprocess.run(
            [str(python_host), "-c", "import sys; print('stdlib_ok')"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert check_det.returncode == 0, check_det.stderr
        assert check_det.stdout.strip() == "stdlib_ok"
    finally:
        bogus_dir.rmdir()

    mismatched_root = tmp_path / "mismatched_generation"
    (mismatched_root / "bin").mkdir(parents=True)
    mismatched_lib = mismatched_root / "python/lib"
    mismatched_lib.mkdir(parents=True)
    shutil.copyfile(library_source, mismatched_lib / library)
    (mismatched_lib / "python3.bad").mkdir()
    (mismatched_lib / "python3.99").mkdir()
    if sys.platform == "darwin":
        subprocess.run(
            [
                "install_name_tool",
                "-id",
                f"@rpath/{library}",
                str(mismatched_lib / library),
            ],
            check=True,
        )
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(mismatched_lib / library)],
            check=True,
        )
    mismatched_host = mismatched_root / "bin/vc-mismatched"
    module.render_native_host(mismatched_host, "any:main", library, python_only=True)
    mismatched_check = subprocess.run(
        [str(mismatched_host), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert mismatched_check.returncode == 78
    assert "stdlib unavailable in this generation" in mismatched_check.stderr


def test_native_host_with_real_pinned_pbs314_fixture(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("pinned PBS 3.14 fixture verification is macOS only")
    spec = importlib.util.spec_from_file_location(
        "native_renderer", ROOT / "scripts/render-python-entrypoint-launchers.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Discover pinned PBS 3.14 in read-only releases
    pbs_lib = None
    pbs_stdlib = None
    candidates = [
        Path.home() / ".local/share/vibecrafted/releases",
        Path("/Users/polyversai/.local/share/vibecrafted/releases"),
    ]
    for candidate in candidates:
        if candidate.is_dir():
            for release in sorted(candidate.glob("*/python"), reverse=True):
                candidate_lib = release / "lib/libpython3.14.dylib"
                candidate_stdlib = release / "lib/python3.14"
                if candidate_lib.is_file() and candidate_stdlib.is_dir():
                    pbs_lib = candidate_lib
                    pbs_stdlib = candidate_stdlib
                    break
            if pbs_lib:
                break
    if not pbs_lib:
        pytest.skip("pinned PBS 3.14 not available on this host")

    root = tmp_path / "pbs314_generation"
    (root / "bin").mkdir(parents=True)
    library_root = root / "python/lib"
    library_root.mkdir(parents=True)
    library = "libpython3.14.dylib"

    # Sealed product closure: private copied fixture with NO symlinks
    shutil.copyfile(pbs_lib, library_root / library)
    shutil.copytree(pbs_stdlib, library_root / "python3.14")

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

    source = root / "vibecrafted-core"
    source.mkdir()
    (source / "owned_core_mod.py").write_text("PBS_MAGIC = 314\n")

    python_host = root / "bin/vc-python"
    module.render_native_host(
        python_host, "owned_core_mod:main", library, python_only=True
    )

    # 1. Version and identity check
    ver_check = subprocess.run(
        [str(python_host), "--version"], capture_output=True, text=True, check=True
    )
    assert ver_check.stdout.startswith("Python 3.14.")

    # 2. Ordinary core + ssl, sqlite, binascii, encodings, datetime imports
    core_check = subprocess.run(
        [
            str(python_host),
            "-c",
            (
                "import owned_core_mod, sys, encodings, binascii, ssl, sqlite3, datetime; "
                "assert owned_core_mod.PBS_MAGIC == 314; "
                f"assert sys.executable == {str(python_host)!r}"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert core_check.returncode == 0, core_check.stderr

    # 3. Parent counterexamples and flag permutations with ambient hostile canary
    ambient_dir = tmp_path / "ambient"
    ambient_dir.mkdir()
    (ambient_dir / "ambient_ISOLATION_canary.py").write_text("IS_LOADED = True\n")
    ambient_env = {**os.environ, "PYTHONPATH": str(ambient_dir)}

    # -I direct find_spec => False
    check_i = subprocess.run(
        [
            str(python_host),
            "-I",
            "-c",
            "import importlib.util; print(bool(importlib.util.find_spec('ambient_ISOLATION_canary')))",
        ],
        env=ambient_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_i.returncode == 0, check_i.stderr
    assert check_i.stdout.strip() == "False"

    # -W ignore -E -c find_spec => False
    check_we = subprocess.run(
        [
            str(python_host),
            "-W",
            "ignore",
            "-E",
            "-c",
            "import importlib.util; print(bool(importlib.util.find_spec('ambient_ISOLATION_canary')))",
        ],
        env=ambient_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_we.returncode == 0, check_we.stderr
    assert check_we.stdout.strip() == "False"

    # -X dev -I -c find_spec => False
    check_xi = subprocess.run(
        [
            str(python_host),
            "-X",
            "dev",
            "-I",
            "-c",
            "import importlib.util; print(bool(importlib.util.find_spec('ambient_ISOLATION_canary')))",
        ],
        env=ambient_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_xi.returncode == 0, check_xi.stderr
    assert check_xi.stdout.strip() == "False"

    # Permutations: -Wignore, -Xdev, -EI, -IE
    for flag_group in (["-Wignore", "-E"], ["-Xdev", "-I"], ["-EI"], ["-IE"]):
        check_comb = subprocess.run(
            [
                str(python_host),
                *flag_group,
                "-c",
                "import importlib.util; print(bool(importlib.util.find_spec('ambient_ISOLATION_canary')))",
            ],
            env=ambient_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert check_comb.returncode == 0, check_comb.stderr
        assert check_comb.stdout.strip() == "False"

    # Ordinary -c code beginning `-1; __import__('ambient_ISOLATION_canary'); print('loaded')` => loaded
    check_ord = subprocess.run(
        [
            str(python_host),
            "-c",
            "-1; __import__('ambient_ISOLATION_canary'); print('loaded')",
        ],
        env=ambient_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_ord.returncode == 0, check_ord.stderr
    assert check_ord.stdout.strip() == "loaded"

    # 4. Foreign uv Python and screenscribe child isolation
    foreign_pythons = [
        Path("/Users/polyversai/.local/share/uv/tools/screenscribe/bin/python3"),
    ]
    foreign_python = next((p for p in foreign_pythons if p.is_file()), None)
    if foreign_python:
        spawned_foreign = subprocess.run(
            [
                str(python_host),
                "-c",
                (
                    "import subprocess, sys; "
                    f"r = subprocess.run([{str(foreign_python)!r}, '-B', '-c', 'import encodings'], capture_output=True); "
                    "sys.exit(r.returncode)"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert spawned_foreign.returncode == 0, spawned_foreign.stderr

    screenscribe_bin = Path("/Users/polyversai/.local/bin/screenscribe")
    if screenscribe_bin.is_file():
        spawned_ss = subprocess.run(
            [
                str(python_host),
                "-c",
                (
                    "import subprocess, sys; "
                    f"r = subprocess.run([{str(screenscribe_bin)!r}, '--help'], capture_output=True); "
                    "sys.exit(r.returncode)"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert spawned_ss.returncode == 0, spawned_ss.stderr

    # 5. Mismatched stdlib negative in PBS 3.14 geometry
    mismatched_root = tmp_path / "mismatched_pbs314"
    (mismatched_root / "bin").mkdir(parents=True)
    mismatched_lib = mismatched_root / "python/lib"
    mismatched_lib.mkdir(parents=True)
    shutil.copyfile(pbs_lib, mismatched_lib / library)
    (mismatched_lib / "python3.bad").mkdir()
    (mismatched_lib / "python3.12").mkdir()
    subprocess.run(
        [
            "install_name_tool",
            "-id",
            f"@rpath/{library}",
            str(mismatched_lib / library),
        ],
        check=True,
    )
    subprocess.run(
        ["codesign", "--force", "--sign", "-", str(mismatched_lib / library)],
        check=True,
    )
    mismatched_host = mismatched_root / "bin/vc-mismatched"
    module.render_native_host(mismatched_host, "any:main", library, python_only=True)
    mismatched_check = subprocess.run(
        [str(mismatched_host), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert mismatched_check.returncode == 78
    assert "stdlib unavailable in this generation" in mismatched_check.stderr


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
