"""Public Runtime Pack rescue wrapper: plan/apply/resume share one verified extract."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tarfile
import time
from pathlib import Path

import pytest
from _runtime_pack_fixture import seed_runtime_pack

from scripts import vetcoders_install as installer
from tests.tui.test_runtime_pack_rescue import (
    _apply,
    _install,
    _leave_interrupted_pending,
    _load_receipt,
    _plant_missing_historical,
    _receipt,
    _seal_runtime_pack_for_admission,
    _write_receipt,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts/install-runtime-pack.sh"
CARRIER = "Vibecrafted_RuntimePack_rescue-fixture.tar.gz"
ARCHIVE_ROOT = "VibecraftedRuntime"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "foreign-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv(
        "VIBECRAFTED_RUNTIME_HOME", str(home / ".local/share/vibecrafted")
    )
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / ".local/bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / ".vibecrafted"))
    monkeypatch.setenv("VC_FRAME_SOCKET_DIR", str(tmp_path / "frame-sockets"))
    monkeypatch.setattr(
        installer, "_teardown_owned_runtime_for_uninstall", lambda *_a, **_k: ()
    )
    (tmp_path / "xdg-cache").mkdir()
    return installer._runtime_install_paths()


@pytest.fixture
def installed(tmp_path: Path, roots, capsys):
    payload = seed_runtime_pack(tmp_path / "pack-a", version="9.9.9+a")
    _seal_runtime_pack_for_admission(payload)
    result = _install(payload, capsys)
    return roots, payload, result


def _sign_payload_archive(
    dest: Path,
    payload: Path,
    *,
    name: str = CARRIER,
    archive_root: str = ARCHIVE_ROOT,
    keys: tuple[Path, Path] | None = None,
) -> tuple[Path, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / name
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(payload, arcname=archive_root)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{checksum}  {archive.name}\n", encoding="utf-8"
    )
    if keys is not None:
        private_key, public_key = keys
    else:
        private_key = dest / "signing.key"
        public_key = dest / "signing.pub"
    if not private_key.exists():
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-out", str(private_key)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "openssl",
                "pkey",
                "-in",
                str(private_key),
                "-pubout",
                "-out",
                str(public_key),
            ],
            check=True,
            capture_output=True,
        )
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(private_key),
            "-out",
            str(archive) + ".sig",
            str(archive),
        ],
        check=True,
        capture_output=True,
    )
    return archive, public_key


def _wrapper_env(
    public_key: Path, extra: dict[str, str] | None = None
) -> dict[str, str]:
    env = {
        **os.environ,
        "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
    }
    if extra:
        env.update(extra)
    return env


def _wrapper_flags(version: str) -> list[str]:
    return [
        "--expected-version",
        version,
        "--expected-platform",
        "darwin-arm64",
        "--expected-architecture",
        "arm64",
    ]


def _run_wrapper(
    *arguments: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(WRAPPER), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _json_out(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.stdout.strip(), (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _expected_payload_root(cache_home: Path, archive: Path) -> Path:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return cache_home / "vibecrafted" / "runtime-pack-rescue" / digest / ARCHIVE_ROOT


def _cache_home() -> Path:
    return Path(os.environ["XDG_CACHE_HOME"])


def _wait_exists(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def _lock_holder_script(lock: Path, ready: Path, child_pid: Path | None = None) -> str:
    inherit = ""
    if child_pid is not None:
        inherit = f'sleep 86400 &\nprintf \'%s\' "$!" > "{child_pid}"\n'
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'source "{WRAPPER}"\n'
        f'_acquire_rescue_lock "{lock}"\n'
        f"{inherit}"
        f"printf 'acquired\\n' > \"{ready}\"\n"
        "read -r _ || true\n"
    )


def _start_lock_holder(
    work: Path,
    lock: Path,
    env: dict[str, str],
    *,
    inherit_child: bool = False,
) -> tuple[subprocess.Popen[str], Path | None]:
    work.mkdir(parents=True, exist_ok=True)
    ready = work / "lock-ready"
    child_pid = work / "lock-child.pid" if inherit_child else None
    script = work / "lock-holder.sh"
    script.write_text(_lock_holder_script(lock, ready, child_pid), encoding="utf-8")
    script.chmod(0o700)
    holder = subprocess.Popen(
        ["bash", str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )
    try:
        _wait_exists(ready)
    except AssertionError:
        stdout, stderr = holder.communicate(timeout=2)
        raise AssertionError(
            f"holder failed rc={holder.returncode} stdout={stdout!r} stderr={stderr!r}"
        ) from None
    return holder, child_pid


def _acquire_once(
    work: Path, lock: Path, env: dict[str, str], name: str = "retry"
) -> subprocess.CompletedProcess[str]:
    work.mkdir(parents=True, exist_ok=True)
    script = work / f"{name}.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'source "{WRAPPER}"\n'
        f'_acquire_rescue_lock "{lock}"\n',
        encoding="utf-8",
    )
    script.chmod(0o700)
    return subprocess.run(
        ["bash", str(script)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _release_holder(holder: subprocess.Popen[str]) -> None:
    if holder.poll() is None and holder.stdin is not None:
        holder.stdin.write("\n")
        holder.stdin.close()
    try:
        holder.wait(timeout=5)
    except subprocess.TimeoutExpired:
        holder.kill()
        holder.wait(timeout=5)


def _kill_fixture_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)


_INSTALLER_MAIN_GUARD = 'if __name__ == "__main__":\n    sys.exit(main())\n'
_PUBLICATION_HOLD_REQUEST = "publication-hold.request"
_PUBLICATION_HOLD_READY = "publication-hold.ready"
_PUBLICATION_HOLD_INSTALLER_SUFFIX = """
_VC_TEST_ORIG_CHECKPOINT_RUNTIME_INSTALL_RECEIPT = _checkpoint_runtime_install_receipt


def _checkpoint_runtime_install_receipt(runtime_home, receipt):
    _VC_TEST_ORIG_CHECKPOINT_RUNTIME_INSTALL_RECEIPT(runtime_home, receipt)
    if not isinstance(receipt, dict) or not receipt.get("config_transaction"):
        return
    cache = Path(os.environ.get("XDG_CACHE_HOME", "")).expanduser()
    request = cache / "vibecrafted" / "publication-hold.request"
    if not request.is_file():
        return
    ready = cache / "vibecrafted" / "publication-hold.ready"
    ready.parent.mkdir(parents=True, exist_ok=True)
    ready.write_text(
        json.dumps({"pid": os.getpid(), "ppid": os.getppid()}, sort_keys=True)
        + "\\n",
        encoding="utf-8",
    )
    while request.is_file():
        time.sleep(0.05)


"""


def _publication_hold_paths() -> tuple[Path, Path]:
    cache = Path(os.environ["XDG_CACHE_HOME"]) / "vibecrafted"
    return cache / _PUBLICATION_HOLD_REQUEST, cache / _PUBLICATION_HOLD_READY


def _instrument_pack_installer_publication_hold(payload: Path) -> None:
    """Pause the fixture pack installer after the real publication lease.

    This is test-private source inside the signed tiny pack, not a production
    flag or API. The first ``config_transaction`` receipt checkpoint is the
    mid-publication phase documented for SIGKILL recovery.
    """
    installer_path = payload / "scripts/vetcoders_install.py"
    text = installer_path.read_text(encoding="utf-8")
    if "_VC_TEST_ORIG_CHECKPOINT_RUNTIME_INSTALL_RECEIPT" in text:
        return
    if _INSTALLER_MAIN_GUARD not in text:
        raise AssertionError("fixture installer is missing the main guard")
    installer_path.write_text(
        text.replace(
            _INSTALLER_MAIN_GUARD,
            _PUBLICATION_HOLD_INSTALLER_SUFFIX + _INSTALLER_MAIN_GUARD,
            1,
        ),
        encoding="utf-8",
    )


def _start_public_wrapper(
    *arguments: str, env: dict[str, str], logs: Path
) -> tuple[subprocess.Popen[str], Path, Path]:
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / "stdout.log"
    stderr_path = logs / "stderr.log"
    stdout_file = stdout_path.open("w", encoding="utf-8")
    stderr_file = stderr_path.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            ["bash", str(WRAPPER), *arguments],
            cwd=str(REPO_ROOT),
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
            env=env,
        )
    except Exception:
        stdout_file.close()
        stderr_file.close()
        raise
    proc._vc_log_handles = stdout_file, stderr_file
    return proc, stdout_path, stderr_path


def _close_wrapper_logs(proc: subprocess.Popen[str]) -> None:
    handles = getattr(proc, "_vc_log_handles", (proc.stdout, proc.stderr))
    for handle in handles:
        if handle is not None and not handle.closed:
            handle.close()


def _read_hold_identity(ready: Path) -> tuple[int, int]:
    payload = json.loads(ready.read_text(encoding="utf-8"))
    return int(payload["pid"]), int(payload["ppid"])


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_public_wrapper_names_beta_xcode_and_does_not_refuse_install(
    tmp_path: Path, roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installing prebuilt binaries needs no toolchain, so a beta (or absent)
    Xcode must never abort the installer. The App's own reinstall bricked on a
    host whose xcode-select pointed at a beta (Founder, 2026-09-29), and end
    user machines often carry no Xcode at all. The stable-only gate stays a
    build contract; the installer only names the channel and proceeds until it
    fails on the actually-missing pack."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uname = fake_bin / "uname"
    uname.write_text("#!/bin/sh\necho Darwin\n")
    uname.chmod(0o755)
    beta = tmp_path / "Xcode-beta.app" / "Contents" / "Developer"
    beta.mkdir(parents=True)
    monkeypatch.delenv("VIBECRAFTED_ALLOW_BETA_XCODE", raising=False)
    result = _run_wrapper(
        "--pack",
        str(tmp_path / "unused.tar.gz"),
        env={
            **os.environ,
            "DEVELOPER_DIR": str(beta),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )
    assert "refusing beta Xcode" not in result.stderr
    assert f"Xcode: beta ({beta})" in result.stderr
    assert "Xcode:" not in result.stdout
    # The run still fails — downstream of the channel line (this fixture's
    # fake uname breaks architecture resolution first), never on Xcode.
    assert result.returncode != 0
    lock = installer._tools_install_lease_path(
        roots["runtime_home"] / "tools" / "vibecrafted-current"
    )
    assert not lock.exists()
    with installer._tools_install_lease(
        lock.parent / "vibecrafted-current", timeout_seconds=0
    ):
        pass


def test_public_wrapper_installs_without_any_xcode_developer_dir(
    tmp_path: Path, roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A machine with no Xcode and no CLT still installs: the channel line
    reports 'none' and never exits."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uname = fake_bin / "uname"
    uname.write_text("#!/bin/sh\necho Darwin\n")
    uname.chmod(0o755)
    # xcode-select resolving nothing == host without any developer dir.
    xcode_select = fake_bin / "xcode-select"
    xcode_select.write_text("#!/bin/sh\nexit 2\n")
    xcode_select.chmod(0o755)
    monkeypatch.delenv("VIBECRAFTED_ALLOW_BETA_XCODE", raising=False)
    env = {k: v for k, v in os.environ.items() if k != "DEVELOPER_DIR"}
    result = _run_wrapper(
        "--pack",
        str(tmp_path / "unused.tar.gz"),
        env={**env, "PATH": f"{fake_bin}:{env['PATH']}"},
    )
    assert "refusing beta Xcode" not in result.stderr
    assert "no usable Xcode developer dir" not in result.stderr
    assert "Xcode: none (not required for install)" in result.stderr
    assert "Xcode:" not in result.stdout
    assert result.returncode != 0  # the missing pack, not the toolchain


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_public_wrapper_signal_releases_install_lease(
    tmp_path: Path, roots, stop_signal: int
) -> None:
    payload = seed_runtime_pack(
        tmp_path / "pack-signal",
        version="9.9.9+signal",
        before_source_seal=_instrument_pack_installer_publication_hold,
    )
    _seal_runtime_pack_for_admission(payload)
    archive, public_key = _sign_payload_archive(tmp_path / "signed-signal", payload)
    request, ready = _publication_hold_paths()
    request.parent.mkdir(parents=True, exist_ok=True)
    request.write_text("hold\n")
    wrapper, stdout, stderr = _start_public_wrapper(
        "--pack",
        str(archive),
        *_wrapper_flags("9.9.9+signal"),
        env=_wrapper_env(public_key),
        logs=tmp_path / "signal-logs",
    )
    child_pid = None
    try:
        _wait_exists(ready, timeout=60)
        child_pid, parent_pid = _read_hold_identity(ready)
        assert parent_pid == wrapper.pid
        current = roots["runtime_home"] / "tools" / "vibecrafted-current"
        lock = installer._tools_install_lease_path(current)
        assert json.loads(lock.read_text())["pid"] == child_pid
        inode = lock.stat().st_ino
        wrapper.send_signal(stop_signal)
        assert wrapper.wait(timeout=10) != 0, (stdout.read_text(), stderr.read_text())
        assert not _pid_alive(child_pid)
        with installer._tools_install_lease(current, timeout_seconds=0):
            assert lock.stat().st_ino == inode
        assert lock.read_text() == ""
    finally:
        request.unlink(missing_ok=True)
        _reap_owned_pids(child_pid, wrapper.pid)
        wrapper.wait(timeout=5)
        _close_wrapper_logs(wrapper)


def test_public_wrapper_installs_over_dead_owner_metadata(
    tmp_path: Path, roots
) -> None:
    payload = seed_runtime_pack(tmp_path / "pack-stale", version="9.9.9+stale")
    _seal_runtime_pack_for_admission(payload)
    archive, public_key = _sign_payload_archive(tmp_path / "signed-stale", payload)
    previous = subprocess.Popen(["/usr/bin/true"])
    assert previous.wait(timeout=5) == 0
    lock = installer._tools_install_lease_path(
        roots["runtime_home"] / "tools" / "vibecrafted-current"
    )
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps(
            {
                "pid": previous.pid,
                "operation": "interrupted",
                "started_at": "2026-09-28T00:00:00Z",
            }
        )
    )
    inode = lock.stat().st_ino
    result = _run_wrapper(
        "--pack",
        str(archive),
        *_wrapper_flags("9.9.9+stale"),
        env=_wrapper_env(public_key),
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "Recovering stale installer lease" in result.stderr
    assert f"pid={previous.pid}" in result.stderr
    assert lock.stat().st_ino == inode
    assert lock.read_text() == ""
    receipt = _load_receipt(roots)
    assert not receipt.get("install_pending")
    assert not receipt.get("config_transaction")


def _reap_owned_pids(*pids: int | None) -> None:
    seen: set[int] = set()
    for pid in pids:
        if pid is None or pid in seen or pid <= 1:
            continue
        seen.add(pid)
        _kill_fixture_pid(pid)


def test_public_wrapper_plan_then_apply_same_verified_pack(
    tmp_path: Path, installed, capsys
) -> None:
    paths, payload, _ = installed
    planted = _plant_missing_historical(paths)
    receipt_before = _receipt(paths).read_bytes()
    selector = paths["runtime_home"] / "tools/vibecrafted-current"
    selector_before = selector.readlink()
    archive, public_key = _sign_payload_archive(tmp_path / "signed", payload)
    env = _wrapper_env(public_key)
    flags = _wrapper_flags(payload.joinpath("VERSION").read_text().strip())
    expected = _expected_payload_root(_cache_home(), archive)

    planned = _run_wrapper(
        "--pack",
        str(archive),
        "--rescue",
        "--plan",
        "--bootstrap-installer",
        str(Path(installer.__file__).resolve()),
        "--bootstrap-installer-sha256",
        hashlib.sha256(Path(installer.__file__).read_bytes()).hexdigest(),
        *flags,
        env=env,
    )
    assert planned.returncode == 0, planned.stderr
    plan = _json_out(planned)
    assert plan["schema"] == installer.RUNTIME_RESCUE_PLAN_SCHEMA
    assert plan["status"] == "rescueable"
    assert plan["target"]["payload_root"] == str(expected.resolve())
    assert plan["target"]["inventory_verified"] is True
    assert plan["target"]["provenance_verified"] is True
    assert plan["target"]["usable"] is True
    assert _receipt(paths).read_bytes() == receipt_before
    assert selector.readlink() == selector_before
    assert expected.is_dir()
    assert not expected.is_symlink()

    applied = _run_wrapper(
        "--pack",
        str(archive),
        "--rescue",
        "--apply",
        "--plan-digest",
        plan["plan_digest"],
        *flags,
        env=env,
    )
    assert applied.returncode == 0, (applied.stdout, applied.stderr)
    result = _json_out(applied)
    assert result["schema"] == installer.RUNTIME_RESCUE_RESULT_SCHEMA
    assert result["status"] == "rescued"
    assert result["healthy_restorepoint"] is True
    assert result["plan_digest"] == plan["plan_digest"]
    finalized = _load_receipt(paths)
    assert finalized["rescue"]["verified"] is True
    assert not finalized.get("rescue_pending")
    for backup in planted:
        assert not Path(backup).exists()
    assert not expected.exists()


def test_public_wrapper_apply_refuses_tampered_retained_payload(
    tmp_path: Path, installed
) -> None:
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    receipt_before = _receipt(paths).read_bytes()
    archive, public_key = _sign_payload_archive(tmp_path / "signed", payload)
    env = _wrapper_env(public_key)
    flags = _wrapper_flags(payload.joinpath("VERSION").read_text().strip())

    planned = _run_wrapper(
        "--pack", str(archive), "--rescue", "--plan", *flags, env=env
    )
    assert planned.returncode == 0, planned.stderr
    plan = _json_out(planned)
    payload_root = Path(plan["target"]["payload_root"])
    (payload_root / "VERSION").write_bytes(
        (payload_root / "VERSION").read_bytes() + b"#tamper\n"
    )

    applied = _run_wrapper(
        "--pack",
        str(archive),
        "--rescue",
        "--apply",
        "--plan-digest",
        plan["plan_digest"],
        *flags,
        env=env,
    )
    assert applied.returncode != 0
    assert "stale or tampered" in applied.stderr
    assert _receipt(paths).read_bytes() == receipt_before


def test_public_wrapper_apply_refuses_changed_archive(
    tmp_path: Path, installed
) -> None:
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    receipt_before = _receipt(paths).read_bytes()
    archive_a, public_key = _sign_payload_archive(tmp_path / "signed-a", payload)
    private_key = tmp_path / "signed-a" / "signing.key"
    other = seed_runtime_pack(tmp_path / "pack-b", version="9.9.9+b")
    _seal_runtime_pack_for_admission(other)
    archive_b, _ = _sign_payload_archive(
        tmp_path / "signed-b",
        other,
        keys=(private_key, public_key),
    )
    env = _wrapper_env(public_key)
    flags = _wrapper_flags(payload.joinpath("VERSION").read_text().strip())

    planned = _run_wrapper(
        "--pack", str(archive_a), "--rescue", "--plan", *flags, env=env
    )
    assert planned.returncode == 0, planned.stderr
    plan = _json_out(planned)

    applied = _run_wrapper(
        "--pack",
        str(archive_b),
        "--rescue",
        "--apply",
        "--plan-digest",
        plan["plan_digest"],
        "--expected-version",
        "9.9.9+b",
        "--expected-platform",
        "darwin-arm64",
        "--expected-architecture",
        "arm64",
        env=env,
    )
    assert applied.returncode == 2, (applied.stdout, applied.stderr)
    result = _json_out(applied)
    assert result["status"] == "refused"
    assert "input drift" in result["reason"]
    assert _receipt(paths).read_bytes() == receipt_before


def test_public_wrapper_interrupted_resume_same_verified_pack(
    tmp_path: Path, installed, capsys, monkeypatch
) -> None:
    paths, payload, _ = installed
    planted = _plant_missing_historical(paths)
    archive, public_key = _sign_payload_archive(tmp_path / "signed", payload)
    env = _wrapper_env(public_key)
    flags = _wrapper_flags(payload.joinpath("VERSION").read_text().strip())
    planned = _run_wrapper(
        "--pack", str(archive), "--rescue", "--plan", *flags, env=env
    )
    assert planned.returncode == 0, planned.stderr
    plan = _json_out(planned)
    payload_root = Path(plan["target"]["payload_root"])
    assert payload_root == _expected_payload_root(_cache_home(), archive).resolve()

    def boom(*args, **kwargs):
        raise RuntimeError("injected interrupt")

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", boom)
    code, first = _apply(payload_root, capsys, plan["plan_digest"])
    assert code == 2
    assert first["healthy_restorepoint"] is False
    journal = json.loads(
        installer._runtime_rescue_journal_path(paths["runtime_home"]).read_text(
            encoding="utf-8"
        )
    )
    assert journal["binding"]["payload_root"] == str(payload_root)
    assert journal["plan_digest"] == plan["plan_digest"]
    _leave_interrupted_pending(paths, plan["plan_digest"])
    sanitized = installer._runtime_receipt_without_missing_historical(
        _load_receipt(paths), set(planted)
    )
    sanitized["install_pending"] = True
    sanitized["rescue_pending"] = {
        "schema": installer.RUNTIME_RESCUE_PENDING_SCHEMA,
        "plan_digest": plan["plan_digest"],
    }
    _write_receipt(paths, sanitized)

    applied = _run_wrapper(
        "--pack",
        str(archive),
        "--rescue",
        "--apply",
        "--plan-digest",
        plan["plan_digest"],
        *flags,
        env=env,
    )
    assert applied.returncode == 0, (applied.stdout, applied.stderr)
    result = _json_out(applied)
    assert result["status"] == "rescued"
    assert result["healthy_restorepoint"] is True
    assert result["plan_digest"] == plan["plan_digest"]
    finalized = _load_receipt(paths)
    assert finalized["rescue"]["verified"] is True
    assert not finalized.get("rescue_pending")


def test_public_wrapper_sigkill_mid_publication_resumes_same_verified_pack(
    tmp_path: Path, roots, capsys
) -> None:
    """Public plan/apply + SIGKILL at the real publication lease, then resume.

    The sibling in-process test injects ``RuntimeError`` and writes pending by
    hand. This one must keep the journal/receipt the real wrapper+installer
    emitted, or fail on those records.
    """
    paths = roots
    payload = seed_runtime_pack(
        tmp_path / "pack-hold",
        version="9.9.9+hold",
        before_source_seal=_instrument_pack_installer_publication_hold,
    )
    _seal_runtime_pack_for_admission(payload)
    _install(payload, capsys)
    planted = _plant_missing_historical(paths)
    archive, public_key = _sign_payload_archive(tmp_path / "signed-hold", payload)
    env = _wrapper_env(public_key)
    flags = _wrapper_flags("9.9.9+hold")
    expected = _expected_payload_root(_cache_home(), archive).resolve()
    hold_request, hold_ready = _publication_hold_paths()
    hold_request.parent.mkdir(parents=True, exist_ok=True)
    if hold_ready.exists():
        hold_ready.unlink()

    planned = _run_wrapper(
        "--pack", str(archive), "--rescue", "--plan", *flags, env=env
    )
    assert planned.returncode == 0, planned.stderr
    plan = _json_out(planned)
    assert plan["schema"] == installer.RUNTIME_RESCUE_PLAN_SCHEMA
    assert plan["status"] == "rescueable"
    assert plan["target"]["payload_root"] == str(expected)
    assert plan["target"]["inventory_verified"] is True
    assert plan["target"]["provenance_verified"] is True
    assert Path(plan["target"]["payload_root"]) == expected

    hold_request.write_text("hold\n", encoding="utf-8")
    apply_args = (
        "--pack",
        str(archive),
        "--rescue",
        "--apply",
        "--plan-digest",
        plan["plan_digest"],
        *flags,
    )
    wrapper: subprocess.Popen[str] | None = None
    installer_pid: int | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    try:
        wrapper, stdout_path, stderr_path = _start_public_wrapper(
            *apply_args, env=env, logs=tmp_path / "apply-hold"
        )
        wrapper_pid = wrapper.pid
        try:
            _wait_exists(hold_ready, timeout=60)
        except AssertionError:
            stdout = stdout_path.read_text(encoding="utf-8")
            stderr = stderr_path.read_text(encoding="utf-8")
            raise AssertionError(
                "public apply never reached mid-publication "
                f"rc={wrapper.poll()} stdout={stdout!r} stderr={stderr!r}"
            ) from None
        installer_pid, parent_pid = _read_hold_identity(hold_ready)
        assert parent_pid == wrapper_pid
        assert installer_pid != wrapper_pid
        assert _pid_alive(installer_pid)
        assert os.getpgid(installer_pid) == os.getpgid(wrapper_pid)

        os.kill(wrapper_pid, signal.SIGKILL)
        assert wrapper.wait(timeout=5) == -signal.SIGKILL
        assert not _pid_alive(wrapper_pid)

        journal_path = installer._runtime_rescue_journal_path(paths["runtime_home"])
        assert journal_path.is_file(), "SIGKILL left no rescue journal"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        assert journal["plan_digest"] == plan["plan_digest"]
        assert journal["binding"]["payload_root"] == str(expected)
        assert journal["binding"]["payload_sha256"]
        interrupted = _load_receipt(paths)
        assert interrupted.get("install_pending") is True
        assert interrupted.get("config_transaction"), (
            "SIGKILL did not leave the real publication lease; "
            f"receipt keys={sorted(interrupted)}"
        )
        pending = interrupted.get("rescue_pending")
        assert isinstance(pending, dict), (
            "SIGKILL did not leave real rescue_pending; "
            f"receipt keys={sorted(interrupted)}"
        )
        assert pending.get("plan_digest") == plan["plan_digest"]
        assert pending.get("binding", {}).get("payload_root") == str(expected)
        assert pending.get("binding", {}).get("payload_sha256")

        if _pid_alive(installer_pid):
            concurrent = _run_wrapper(*apply_args, env=env)
            assert concurrent.returncode != 0, (
                concurrent.stdout,
                concurrent.stderr,
            )
            assert "concurrent" in concurrent.stderr
            assert _pid_alive(installer_pid)
            _kill_fixture_pid(installer_pid)
            installer_pid = None
        hold_request.unlink(missing_ok=True)

        applied = _run_wrapper(*apply_args, env=env)
        assert applied.returncode == 0, (applied.stdout, applied.stderr)
        result = _json_out(applied)
        assert result["schema"] == installer.RUNTIME_RESCUE_RESULT_SCHEMA
        assert result["status"] == "rescued"
        assert result["healthy_restorepoint"] is True
        assert result["plan_digest"] == plan["plan_digest"]
        finalized = _load_receipt(paths)
        assert finalized["rescue"]["verified"] is True
        assert finalized["rescue"]["healthy_restorepoint"] is True
        assert not finalized.get("rescue_pending")
        assert not finalized.get("install_pending")
        assert not finalized.get("config_transaction")
        for backup in planted:
            assert not Path(backup).exists()
        assert not expected.exists()
    finally:
        hold_request.unlink(missing_ok=True)
        if wrapper is not None:
            _reap_owned_pids(installer_pid, wrapper.pid)
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait(timeout=5)
            _close_wrapper_logs(wrapper)
        else:
            _reap_owned_pids(installer_pid)


def test_public_wrapper_interrupted_resume_refuses_altered_pending_identity(
    tmp_path: Path, installed, capsys, monkeypatch
) -> None:
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    archive, public_key = _sign_payload_archive(tmp_path / "signed", payload)
    env = _wrapper_env(public_key)
    flags = _wrapper_flags(payload.joinpath("VERSION").read_text().strip())
    planned = _run_wrapper(
        "--pack", str(archive), "--rescue", "--plan", *flags, env=env
    )
    plan = _json_out(planned)
    payload_root = Path(plan["target"]["payload_root"])

    def boom(*args, **kwargs):
        raise RuntimeError("injected interrupt")

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", boom)
    assert _apply(payload_root, capsys, plan["plan_digest"])[0] == 2
    journal_path = installer._runtime_rescue_journal_path(paths["runtime_home"])
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["binding"]["payload_root"] = "/tmp/foreign-pack"
    journal_path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
    _leave_interrupted_pending(paths, plan["plan_digest"])

    applied = _run_wrapper(
        "--pack",
        str(archive),
        "--rescue",
        "--apply",
        "--plan-digest",
        plan["plan_digest"],
        *flags,
        env=env,
    )
    assert applied.returncode == 2, (applied.stdout, applied.stderr)
    result = _json_out(applied)
    assert result["status"] == "refused"
    assert "input drift" in result["reason"]


def test_public_wrapper_refuses_foreign_symlink_extract(
    tmp_path: Path, installed
) -> None:
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    receipt_before = _receipt(paths).read_bytes()
    archive, public_key = _sign_payload_archive(tmp_path / "signed", payload)
    env = _wrapper_env(public_key)
    flags = _wrapper_flags(payload.joinpath("VERSION").read_text().strip())
    planned = _run_wrapper(
        "--pack", str(archive), "--rescue", "--plan", *flags, env=env
    )
    assert planned.returncode == 0, planned.stderr
    plan = _json_out(planned)
    extract = Path(plan["target"]["payload_root"]).parent
    foreign = tmp_path / "foreign-extract"
    shutil.copytree(extract, foreign)
    shutil.rmtree(extract)
    extract.symlink_to(foreign)

    applied = _run_wrapper(
        "--pack",
        str(archive),
        "--rescue",
        "--apply",
        "--plan-digest",
        plan["plan_digest"],
        *flags,
        env=env,
    )
    assert applied.returncode != 0
    assert "symlink" in applied.stderr
    assert _receipt(paths).read_bytes() == receipt_before


def test_public_wrapper_refuses_concurrent_extract_lock(
    tmp_path: Path, installed
) -> None:
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    archive, public_key = _sign_payload_archive(tmp_path / "signed", payload)
    env = _wrapper_env(public_key)
    flags = _wrapper_flags(payload.joinpath("VERSION").read_text().strip())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    lock = _cache_home() / "vibecrafted" / "runtime-pack-rescue" / f"{digest}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.parent.chmod(0o700)
    holder, _ = _start_lock_holder(tmp_path / "holder-work", lock, env)
    try:
        planned = _run_wrapper(
            "--pack", str(archive), "--rescue", "--plan", *flags, env=env
        )
        assert planned.returncode != 0
        assert "concurrent" in planned.stderr
        assert lock.is_dir()
        assert (lock / "held").is_file()
        assert holder.poll() is None
    finally:
        _release_holder(holder)


def test_public_wrapper_bootstraps_source_installer_without_rewriting_pack(
    tmp_path: Path, installed
) -> None:
    paths, _, _ = installed
    _plant_missing_historical(paths)
    payload = seed_runtime_pack(tmp_path / "old-pack", version="9.9.9+old")
    old_installer = payload / "scripts/vetcoders_install.py"
    old_installer.write_text(
        "#!/usr/bin/env python3\n# historical installer without rescue\n",
        encoding="utf-8",
    )
    _seal_runtime_pack_for_admission(payload)
    archive, public_key = _sign_payload_archive(tmp_path / "signed", payload)
    env = _wrapper_env(public_key)
    flags = _wrapper_flags("9.9.9+old")

    planned = _run_wrapper(
        "--pack",
        str(archive),
        "--rescue",
        "--plan",
        "--bootstrap-installer",
        str(Path(installer.__file__).resolve()),
        "--bootstrap-installer-sha256",
        hashlib.sha256(Path(installer.__file__).read_bytes()).hexdigest(),
        *flags,
        env=env,
    )
    assert planned.returncode == 0, planned.stderr
    assert "Explicit bootstrap installer" in planned.stderr
    assert "signed payload remains immutable" in planned.stderr
    plan = _json_out(planned)
    expected = _expected_payload_root(_cache_home(), archive).resolve()
    assert plan["target"]["payload_root"] == str(expected)
    assert plan["target"]["installer_supports_rescue"] is False
    assert plan["bootstrap"]["required"] is True
    published = expected / "scripts/vetcoders_install.py"
    assert "RUNTIME_RESCUE_PLAN_SCHEMA" not in published.read_text(encoding="utf-8")
    assert archive.read_bytes() == (tmp_path / "signed" / CARRIER).read_bytes()


def test_stat_helpers_return_numeric_owner_and_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GNU `stat -f` is --file-system; the helper must not swallow that dump."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    target = tmp_path / "owned"
    target.mkdir(mode=0o700)
    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'source "{WRAPPER}"\n'
        f'uid="$(_stat_uid "{target}")"\n'
        f'mode="$(_stat_mode "{target}")"\n'
        'printf "uid=%s mode=%s\\n" "$uid" "$mode"\n'
        '[[ "$uid" == "$EUID" ]]\n'
        '[[ "$uid" =~ ^[0-9]+$ ]]\n'
        '[[ "$mode" == "700" || "$mode" == "0700" ]]\n',
        encoding="utf-8",
    )
    script.chmod(0o700)
    probed = subprocess.run(
        ["bash", str(script)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ},
    )
    assert probed.returncode == 0, probed.stderr
    assert probed.stdout.startswith("uid="), probed.stdout
    assert "\n" not in probed.stdout.strip()


def test_rescue_lock_recovers_after_killed_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    env = {**os.environ}
    lock = tmp_path / "archive.lock"
    holder, _ = _start_lock_holder(tmp_path / "owner-work", lock, env)
    assert lock.is_dir()
    assert (lock / "held").is_file()
    assert holder.poll() is None
    holder.send_signal(signal.SIGKILL)
    assert holder.wait(timeout=5) == -signal.SIGKILL
    assert holder.poll() is not None
    assert lock.is_dir()
    retry = _acquire_once(tmp_path / "retry-work", lock, env)
    assert retry.returncode == 0, retry.stderr
    assert lock.is_dir()
    assert (lock / "held").is_file()


def test_rescue_lock_refuses_concurrent_live_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    env = {**os.environ}
    lock = tmp_path / "archive.lock"
    holder, _ = _start_lock_holder(tmp_path / "live-work", lock, env)
    try:
        assert holder.poll() is None
        refused = _acquire_once(tmp_path / "contender-work", lock, env)
        assert refused.returncode == 1, refused.stdout
        assert "concurrent" in refused.stderr
        assert holder.poll() is None
        assert lock.is_dir()
        assert (lock / "held").is_file()
    finally:
        _release_holder(holder)


def test_rescue_lock_holds_while_inherited_child_lives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    env = {**os.environ}
    lock = tmp_path / "archive.lock"
    holder, child_path = _start_lock_holder(
        tmp_path / "parent-work", lock, env, inherit_child=True
    )
    assert child_path is not None
    child_pid = int(child_path.read_text(encoding="utf-8"))
    os.kill(child_pid, 0)
    holder.send_signal(signal.SIGKILL)
    assert holder.wait(timeout=5) == -signal.SIGKILL
    os.kill(child_pid, 0)
    refused = _acquire_once(tmp_path / "while-child", lock, env, name="while-child")
    try:
        assert refused.returncode == 1, refused.stdout
        assert "concurrent" in refused.stderr
        assert lock.is_dir()
    finally:
        _kill_fixture_pid(child_pid)
    retry = _acquire_once(tmp_path / "after-child", lock, env, name="after-child")
    assert retry.returncode == 0, retry.stderr


def test_rescue_lock_refuses_foreign_malformed_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    env = {**os.environ}
    as_file = tmp_path / "file.lock"
    as_file.write_text("not-a-lock-dir\n", encoding="utf-8")
    refused_file = _acquire_once(tmp_path / "file-work", as_file, env, name="file")
    assert refused_file.returncode == 1, refused_file.stdout
    assert "malformed" in refused_file.stderr
    assert as_file.is_file()

    as_link = tmp_path / "link.lock"
    as_link.symlink_to(tmp_path / "foreign-target")
    refused_link = _acquire_once(tmp_path / "link-work", as_link, env, name="link")
    assert refused_link.returncode == 1, refused_link.stdout
    assert "symlink" in refused_link.stderr
    assert as_link.is_symlink()


def test_public_rescue_plan_never_reconciles_installed_service(tmp_path, installed):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    marker = tmp_path / "service-called"
    launcher = paths["launcher_home"] / "vibecrafted"
    launcher.write_text(f'#!/bin/sh\nprintf called > "{marker}"\n')
    launcher.chmod(0o755)
    receipt = _load_receipt(paths)
    receipt["owned_files"][str(launcher)] = hashlib.sha256(
        launcher.read_bytes()
    ).hexdigest()
    _write_receipt(paths, receipt)
    plist = Path.home() / "Library/LaunchAgents/io.vetcoders.vibecrafted.server.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("fixture")
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    uname = host_bin / "uname"
    uname.write_text('#!/bin/sh\nprintf "Darwin\\n"\n')
    uname.chmod(0o755)
    archive, key = _sign_payload_archive(tmp_path / "carrier", payload)
    result = _run_wrapper(
        "--pack",
        str(archive),
        *_wrapper_flags("9.9.9+a"),
        "--rescue",
        "--plan",
        env=_wrapper_env(key, {"PATH": f"{host_bin}{os.pathsep}{os.environ['PATH']}"}),
    )
    assert result.returncode == 0, (
        _json_out(result).get("reason"),
        _json_out(result).get("collisions"),
        result.stderr,
    )
    assert _json_out(result)["mode"] == "plan"
    assert not marker.exists(), "read-only plan executed the installed service launcher"


def test_renamed_download_uses_explicit_sidecars_and_carrier_identity(tmp_path, roots):
    payload = seed_runtime_pack(tmp_path / "pack", version="9.9.9+a")
    _seal_runtime_pack_for_admission(payload)
    archive, key = _sign_payload_archive(tmp_path / "signed", payload)
    renamed = archive.with_name(archive.name.replace(".tar.gz", " 2.tar.gz"))
    archive.rename(renamed)
    checksum = Path(str(archive) + ".sha256")
    signature = Path(str(archive) + ".sig")
    result = _run_wrapper(
        "--pack",
        str(renamed),
        "--checksum",
        str(checksum),
        "--signature",
        str(signature),
        "--carrier-basename",
        CARRIER,
        "--verify-only",
        *_wrapper_flags("9.9.9+a"),
        env=_wrapper_env(key),
    )
    assert result.returncode == 0, result.stderr
    assert not installer._runtime_receipt_path(roots["runtime_home"]).exists()
    checksum.write_text("0" * 64 + "  " + CARRIER + "\n")
    refused = _run_wrapper(
        "--pack",
        str(renamed),
        "--checksum",
        str(checksum),
        "--signature",
        str(signature),
        "--carrier-basename",
        CARRIER,
        "--verify-only",
        *_wrapper_flags("9.9.9+a"),
        env=_wrapper_env(key),
    )
    assert refused.returncode != 0 and "checksum" in refused.stderr


@pytest.mark.parametrize("choice", ["explicit", "wrong-hash", "alias", "implicit"])
def test_old_signed_pack_requires_explicit_bound_bootstrap(tmp_path, installed, choice):
    roots, _, _ = installed
    _plant_missing_historical(roots)
    receipt_before = installer._runtime_receipt_path(roots["runtime_home"]).read_bytes()
    payload = seed_runtime_pack(tmp_path / "old-pack", version="9.9.10+b")
    packed = payload / "scripts/vetcoders_install.py"
    packed.write_text("# historical installer without rescue\n")
    _seal_runtime_pack_for_admission(payload)
    archive, key = _sign_payload_archive(tmp_path / "signed", payload)
    original = archive.read_bytes()
    source = Path(installer.__file__).resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    flags = []
    if choice == "alias":
        alias = tmp_path / "aliased-installer.py"
        alias.symlink_to(source)
        source = alias
    if choice != "implicit":
        flags = [
            "--bootstrap-installer",
            str(source),
            "--bootstrap-installer-sha256",
            "0" * 64 if choice == "wrong-hash" else digest,
        ]
    result = _run_wrapper(
        "--pack",
        str(archive),
        "--rescue",
        "--plan",
        *flags,
        *_wrapper_flags("9.9.10+b"),
        env=_wrapper_env(key),
    )
    assert archive.read_bytes() == original
    assert (
        installer._runtime_receipt_path(roots["runtime_home"]).read_bytes()
        == receipt_before
    )
    if choice == "explicit":
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "bootstrap" in result.stderr


def test_public_bootstrap_install_reports_selected_owner(tmp_path, roots):
    payload = seed_runtime_pack(tmp_path / "old-pack", version="9.9.9+a")
    (payload / "scripts/vetcoders_install.py").write_text("# old owner\n")
    _seal_runtime_pack_for_admission(payload)
    archive, key = _sign_payload_archive(tmp_path / "signed", payload)
    source = Path(installer.__file__).resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    original = archive.read_bytes()
    result = _run_wrapper(
        "--pack",
        str(archive),
        "--bootstrap-installer",
        str(source),
        "--bootstrap-installer-sha256",
        digest,
        *_wrapper_flags("9.9.9+a"),
        env=_wrapper_env(key),
    )
    assert result.returncode == 0, result.stderr
    outcome = _json_out(result)
    assert outcome["installer"] == {
        "mode": "explicit-bootstrap",
        "path": str(source),
        "sha256": digest,
        "signed_archive_sha256": hashlib.sha256(original).hexdigest(),
    }
    assert Path(outcome["root"]).name == "9.9.9+a"
    assert archive.read_bytes() == original
