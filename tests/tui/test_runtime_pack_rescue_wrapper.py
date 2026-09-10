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


def _wrapper_env(public_key: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
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


def _run_wrapper(*arguments: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(WRAPPER), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _json_out(result: subprocess.CompletedProcess[str]) -> dict:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, (result.returncode, result.stdout, result.stderr)
    return json.loads(lines[-1])


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


def _lock_holder_script(
    lock: Path, ready: Path, child_pid: Path | None = None
) -> str:
    inherit = ""
    if child_pid is not None:
        inherit = f'sleep 86400 &\nprintf \'%s\' "$!" > "{child_pid}"\n'
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'source "{WRAPPER}"\n'
        f'_acquire_rescue_lock "{lock}"\n'
        f"{inherit}"
        f'printf \'acquired\\n\' > "{ready}"\n'
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
        "--pack", str(archive), "--rescue", "--plan", *flags, env=env
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
    lock = (
        _cache_home() / "vibecrafted" / "runtime-pack-rescue" / f"{digest}.lock"
    )
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
        "--pack", str(archive), "--rescue", "--plan", *flags, env=env
    )
    assert planned.returncode == 0, planned.stderr
    assert "bootstrapping with source installer" in planned.stderr
    assert "Do not rewrite the signed payload" in planned.stderr
    plan = _json_out(planned)
    expected = _expected_payload_root(_cache_home(), archive).resolve()
    assert plan["target"]["payload_root"] == str(expected)
    assert plan["target"]["installer_supports_rescue"] is False
    assert plan["bootstrap"]["required"] is True
    published = expected / "scripts/vetcoders_install.py"
    assert "RUNTIME_RESCUE_PLAN_SCHEMA" not in published.read_text(encoding="utf-8")
    assert archive.read_bytes() == (tmp_path / "signed" / CARRIER).read_bytes()


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
