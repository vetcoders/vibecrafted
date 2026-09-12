"""Process-level concurrency for release single-flight ownership.

Holders are a test-only driver that sources the real lock, selection,
snapshot and keychain libraries and follows production acquire / claim /
materialize / require_clean_repo / cleanup order. Refusals and --help drive
the real ``scripts/build-vibecrafted-release.sh``. There is no production
ready-status bypass and no FAKE_STAGES / FAKE_KEYCHAIN escape.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_BUILDER = REPO_ROOT / "scripts/build-vibecrafted-release.sh"
DRIVER = REPO_ROOT / "tests/tui/fixtures/release_single_flight_driver.sh"
SELECTION_LIBRARY = REPO_ROOT / "scripts/lib/runtime-pack-selection.sh"
FLIGHT_LIBRARY = REPO_ROOT / "scripts/lib/release-single-flight.sh"
DONOR_LIBRARY = REPO_ROOT / "scripts/lib/donor-snapshot.sh"
KEYCHAIN_LIBRARY = REPO_ROOT / "scripts/lib/keychain-session.sh"
VERSION = "4.3.1"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _stage_repo(tmp_path: Path, name: str = "checkout") -> tuple[Path, str]:
    """A clean checkout the real builder and the fixture driver can lock."""

    repo = tmp_path / name
    (repo / "scripts/lib").mkdir(parents=True)
    for src in (
        RELEASE_BUILDER,
        SELECTION_LIBRARY,
        FLIGHT_LIBRARY,
        DONOR_LIBRARY,
        KEYCHAIN_LIBRARY,
        REPO_ROOT / "scripts/lib/payload-hygiene.sh",
        REPO_ROOT / "scripts/lib/macho-signing.sh",
    ):
        if src == RELEASE_BUILDER:
            shutil.copy2(src, repo / "scripts" / src.name)
        else:
            shutil.copy2(src, repo / "scripts/lib" / src.name)
    (repo / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
    # Living-tree dirt check ignores /build the same way the real checkout does.
    (repo / ".gitignore").write_text("/build\n/dist\n", encoding="utf-8")
    fake_bin = repo / "fake-bin"
    fake_bin.mkdir()
    rustup = fake_bin / "rustup"
    rustup.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    rustup.chmod(0o755)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "agents@vetcoders.io")
    _git(repo, "config", "user.name", "fixture")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, head


def _driver_env(
    repo: Path,
    stage: Path,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    env = {
        **os.environ,
        "PATH": f"{repo / 'fake-bin'}:{os.environ['PATH']}",
        "VIBECRAFTED_TERMINAL_REPO": str(repo),
        "VIBECRAFTED_FRAME_REPO": str(repo),
        "RELEASE_SINGLE_FLIGHT_STAGE": str(stage),
        "PRODUCTION_BUILDER": str(RELEASE_BUILDER),
    }
    if extra:
        env.update(extra)
    return env


def _builder_env(
    repo: Path,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    env = {
        **os.environ,
        "PATH": f"{repo / 'fake-bin'}:{os.environ['PATH']}",
        "VIBECRAFTED_TERMINAL_REPO": str(repo),
        "VIBECRAFTED_FRAME_REPO": str(repo),
    }
    if extra:
        env.update(extra)
    return env


def _start_driver(
    repo: Path,
    stage: Path,
    *arguments: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["bash", str(DRIVER), *arguments],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_driver_env(repo, stage, extra_env),
    )


def _run_builder(
    repo: Path,
    *arguments: str,
    extra_env: dict[str, str] | None = None,
    timeout: float = 20,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "scripts" / RELEASE_BUILDER.name), *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=_builder_env(repo, extra_env),
    )


def _wait_for(path: Path, timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


def _drain(proc: subprocess.Popen[str], timeout: float = 2) -> tuple[str, str]:
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=timeout)
    return stdout or "", stderr or ""


def _reap_own_child(proc: subprocess.Popen[str] | None) -> None:
    """Signal only this test's child. Never another process."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=2)


def _wait_for_or_report(
    proc: subprocess.Popen[str], path: Path, timeout: float = 8
) -> None:
    try:
        _wait_for(path, timeout=timeout)
    except AssertionError as exc:
        rc = proc.poll()
        stderr = ""
        if rc is not None:
            _stdout, stderr = _drain(proc, timeout=1)
        raise AssertionError(f"{exc}; driver rc={rc} stderr={stderr!r}") from exc


def _write_fake_security(path: Path) -> Path:
    """A substitute ``security`` that lives outside the tracked fixture root.

    create-keychain is ``-p <password> <path>``; $2 is the flag, not the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        'state="${FAKE_SECURITY_STATE:?}"\n'
        'mkdir -p "$state"\n'
        'printf \'%s\\n\' "$*" >> "$state/argv.log"\n'
        'cmd="$1"\n'
        "shift\n"
        'case "$cmd" in\n'
        "  list-keychains) : ;;\n"
        "  default-keychain) : ;;\n"
        "  create-keychain)\n"
        '    path=""\n'
        "    while [ $# -gt 0 ]; do\n"
        '      case "$1" in\n'
        "        -p) [ $# -ge 2 ] || exit 64; shift 2 ;;\n"
        '        *) path="$1"; shift ;;\n'
        "      esac\n"
        "    done\n"
        '    [ -n "$path" ] || exit 64\n'
        '    mkdir -p "$(dirname "$path")"\n'
        "    printf 'fake-keychain\\n' > \"$path\"\n"
        "    ;;\n"
        "  set-keychain-settings) : ;;\n"
        "  unlock-keychain)\n"
        '    path=""\n'
        "    while [ $# -gt 0 ]; do\n"
        '      case "$1" in\n'
        "        -p) [ $# -ge 2 ] || exit 64; shift 2 ;;\n"
        '        *) path="$1"; shift ;;\n'
        "      esac\n"
        "    done\n"
        '    [ -e "$path" ] || exit 1\n'
        "    ;;\n"
        "  delete-keychain)\n"
        '    path="$1"\n'
        '    [ -n "$path" ] || exit 64\n'
        '    rm -f "$path"\n'
        "    ;;\n"
        "  *) ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _finish(
    proc: subprocess.Popen[str], timeout: float = 8
) -> subprocess.CompletedProcess[str]:
    stdout, stderr = proc.communicate(timeout=timeout)
    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode if proc.returncode is not None else -1,
        stdout,
        stderr,
    )


def _selection(repo: Path) -> dict[str, str]:
    return json.loads((repo / "build/runtime-pack-selection.json").read_text("utf-8"))


def test_help_does_not_create_lock_or_selection(tmp_path: Path) -> None:
    repo, _head = _stage_repo(tmp_path)
    before = list(repo.rglob("*"))

    result = _run_builder(repo, "--help")

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stderr
    assert not (repo / "build/release.lock").exists()
    assert not (repo / "build/runtime-pack-selection.json").exists()
    assert not (repo / "build/unified-release/donor-snapshots").exists()
    after = {path.relative_to(repo) for path in repo.rglob("*")}
    before_rel = {path.relative_to(repo) for path in before}
    created = after - before_rel
    assert not any(str(path).startswith("build") for path in created)


def test_second_builder_is_refused_and_leaves_first_sentinels(
    tmp_path: Path,
) -> None:
    repo, head = _stage_repo(tmp_path)
    stage = tmp_path / "stage-a"
    first = _start_driver(repo, stage, "--runtime-pack-only")
    try:
        _wait_for(stage / "snapshot")
        selection_before = (repo / "build/runtime-pack-selection.json").read_bytes()
        snapshot_before = (stage / "snapshot").read_text(encoding="utf-8")
        snapshot_path = Path(
            (stage / "snapshot-path").read_text(encoding="utf-8").strip()
        )
        assert snapshot_path.is_dir()
        assert snapshot_before.strip() == head
        assert (
            _git(snapshot_path, "status", "--porcelain", "--untracked-files=normal")
            == ""
        )
        stamp = Path((stage / "owner-stamp-path").read_text(encoding="utf-8").strip())
        assert stamp.is_file()
        assert stamp.parent == snapshot_path.parent
        assert not (snapshot_path / ".vibecrafted-release-owner").exists()

        second = _run_builder(repo, "--runtime-pack-only")
        assert second.returncode != 0, second.stderr
        assert "another release is already running" in second.stderr
        assert (
            repo / "build/runtime-pack-selection.json"
        ).read_bytes() == selection_before
        assert (stage / "snapshot").read_text(encoding="utf-8") == snapshot_before
        assert snapshot_path.is_dir()
        assert _git(snapshot_path, "rev-parse", "HEAD") == head
    finally:
        (stage / "continue").write_text("1\n", encoding="utf-8")
        finished = _finish(first)
    assert finished.returncode == 0, finished.stderr
    assert (stage / "continued-after-wait").exists()
    assert _selection(repo)["status"] == "pending"


def test_termination_then_successor_keeps_its_snapshot(
    tmp_path: Path,
) -> None:
    repo, head = _stage_repo(tmp_path)
    first_stage = tmp_path / "stage-first"
    first = _start_driver(repo, first_stage, "--runtime-pack-only")
    _wait_for(first_stage / "snapshot")
    first_snapshot = Path(
        (first_stage / "snapshot-path").read_text(encoding="utf-8").strip()
    )
    first.send_signal(signal.SIGTERM)
    first_done = _finish(first)
    assert first_done.returncode == 143, first_done.stderr
    assert not first_snapshot.exists()
    assert not (first_stage / "continued-after-wait").exists()

    successor_stage = tmp_path / "stage-successor"
    successor = _start_driver(repo, successor_stage, "--runtime-pack-only")
    _wait_for(successor_stage / "snapshot")
    successor_snapshot = Path(
        (successor_stage / "snapshot-path").read_text(encoding="utf-8").strip()
    )
    assert successor_snapshot.is_dir()
    assert _git(successor_snapshot, "rev-parse", "HEAD") == head
    assert (
        _git(successor_snapshot, "status", "--porcelain", "--untracked-files=normal")
        == ""
    )
    (successor_stage / "continue").write_text("1\n", encoding="utf-8")
    successor_done = _finish(successor)
    assert successor_done.returncode == 0, successor_done.stderr
    record = _selection(repo)
    assert record["status"] == "pending"
    assert record["source_revision"] == head


def test_killed_owner_releases_kernel_lock_without_deleting_inode(
    tmp_path: Path,
) -> None:
    repo, _head = _stage_repo(tmp_path)
    stage = tmp_path / "stage-killed"
    holder = _start_driver(repo, stage, "--runtime-pack-only")
    _wait_for(stage / "locked")
    lock = repo / "build/release.lock"
    assert lock.is_file()
    holder.kill()
    holder.wait(timeout=5)
    successor_stage = tmp_path / "stage-after-kill"
    successor = _start_driver(repo, successor_stage, "--runtime-pack-only")
    _wait_for(successor_stage / "locked")
    (successor_stage / "continue").write_text("1\n", encoding="utf-8")
    done = _finish(successor)
    assert done.returncode == 0, done.stderr
    assert lock.is_file()


def test_notarize_only_takes_the_lock_and_leaves_selection(
    tmp_path: Path,
) -> None:
    repo, _head = _stage_repo(tmp_path)
    pack = tmp_path / "prior.tar.gz"
    pack.write_bytes(b"exact-owned-pack-bytes")
    digest = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    (repo / "build").mkdir()
    (repo / "build/runtime-pack-selection.json").write_text(
        "{\n"
        '  "schema": "vibecrafted.runtime-pack-selection.v1",\n'
        '  "status": "ready",\n'
        '  "attempt": "prior-ready",\n'
        f'  "pack": "{pack.resolve()}",\n'
        f'  "carrier_basename": "{pack.name}",\n'
        f'  "sha256": "{digest}",\n'
        f'  "size": "{pack.stat().st_size}",\n'
        '  "version": "4.3.1",\n'
        '  "platform": "darwin-arm64",\n'
        '  "architecture": "arm64",\n'
        '  "source_revision": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",\n'
        '  "terminal_revision": "cccccccccccccccccccccccccccccccccccccccc",\n'
        '  "frame_revision": "dddddddddddddddddddddddddddddddddddddddd",\n'
        '  "completed_at": "2026-01-01T00:00:00Z"\n'
        "}\n",
        encoding="utf-8",
    )
    before = (repo / "build/runtime-pack-selection.json").read_bytes()
    stage = tmp_path / "stage-notarize"
    first = _start_driver(repo, stage, "--notarize-only")
    try:
        _wait_for(stage / "notarize-locked")
        assert not (stage / "selection").exists()
        assert not (stage / "snapshot").exists()
        second = _run_builder(repo, "--notarize-only")
        assert second.returncode != 0, second.stderr
        assert "another release is already running" in second.stderr
        assert (repo / "build/runtime-pack-selection.json").read_bytes() == before
    finally:
        (stage / "continue").write_text("1\n", encoding="utf-8")
        done = _finish(first)
    assert done.returncode == 0, done.stderr
    assert (repo / "build/runtime-pack-selection.json").read_bytes() == before


def test_keychain_trap_still_chains_in_front_of_reap_and_unlock(
    tmp_path: Path,
) -> None:
    repo, _head = _stage_repo(tmp_path)
    # Outside the committed fixture so require_clean_repo still sees a clean tree.
    security = _write_fake_security(tmp_path / "untracked-security" / "security")
    state = tmp_path / "security-state"
    state.mkdir()
    stage = tmp_path / "stage-keychain"
    first: subprocess.Popen[str] | None = None
    try:
        first = _start_driver(
            repo,
            stage,
            "--runtime-pack-only",
            extra_env={
                "RELEASE_SINGLE_FLIGHT_KEYCHAIN": "1",
                "KEYCHAIN_SESSION_SECURITY_BIN": str(security),
                "KEYCHAIN_SESSION_STATE_DIR": str(tmp_path / "keychain-state"),
                "FAKE_SECURITY_STATE": str(state),
                "HOME": str(tmp_path / "home"),
            },
        )
        _wait_for_or_report(first, stage / "trap-exit")
        trap_text = (stage / "trap-exit").read_text(encoding="utf-8")
        assert "_ks_trap_cleanup" in trap_text
        assert "cleanup" in trap_text
        first.send_signal(signal.SIGTERM)
        done = _finish(first)
        assert done.returncode == 143, done.stderr
        assert not (stage / "continued-after-wait").exists()
        argv = (state / "argv.log").read_text(encoding="utf-8")
        assert "create-keychain" in argv
        assert "delete-keychain" in argv
        created = [
            line for line in argv.splitlines() if line.startswith("create-keychain")
        ]
        assert created, argv
        assert " -p " in f" {created[0]} "
        assert not created[0].endswith(" -p")
        assert not (repo / "-p").exists()
    finally:
        _reap_own_child(first)


def test_shared_output_dir_refuses_a_second_checkout(
    tmp_path: Path,
) -> None:
    first_repo, _head = _stage_repo(tmp_path, "checkout-a")
    second_repo, _second_head = _stage_repo(tmp_path, "checkout-b")
    shared = tmp_path / "shared-dist"
    extra = {"VIBECRAFTED_RELEASE_DIR": str(shared)}
    first_stage = tmp_path / "stage-shared-a"
    first = _start_driver(
        first_repo, first_stage, "--runtime-pack-only", extra_env=extra
    )
    try:
        _wait_for(first_stage / "locked")
        second_stage = tmp_path / "stage-shared-b"
        second = subprocess.run(
            ["bash", str(DRIVER), "--runtime-pack-only"],
            cwd=second_repo,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
            env=_driver_env(second_repo, second_stage, extra),
        )
        assert second.returncode != 0, second.stderr
        assert "already owns this output directory" in second.stderr
        assert not (second_stage / "locked").exists()
        assert not (second_repo / "build/runtime-pack-selection.json").exists()
        assert (shared.parent / f"{shared.name}.release.lock").is_file()
    finally:
        (first_stage / "continue").write_text("1\n", encoding="utf-8")
        done = _finish(first)
    assert done.returncode == 0, done.stderr


def test_in_root_symlink_and_dotdot_share_one_physical_output_lock(
    tmp_path: Path,
) -> None:
    """Two roots, one physical dist: lexical in-repo prefix must not skip the lock."""

    first_repo, _head = _stage_repo(tmp_path, "checkout-a")
    second_repo, _second_head = _stage_repo(tmp_path, "checkout-b")
    shared = tmp_path / "shared-dist"
    # First root reaches the shared dir through .. ; second through an in-root symlink.
    first_dist = first_repo / ".." / shared.name
    (second_repo / "dist").symlink_to(shared)
    first_stage = tmp_path / "stage-phys-a"
    first = _start_driver(
        first_repo,
        first_stage,
        "--runtime-pack-only",
        extra_env={"VIBECRAFTED_RELEASE_DIR": str(first_dist)},
    )
    try:
        _wait_for_or_report(first, first_stage / "locked")
        second_stage = tmp_path / "stage-phys-b"
        second = subprocess.run(
            ["bash", str(DRIVER), "--runtime-pack-only"],
            cwd=second_repo,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
            env=_driver_env(second_repo, second_stage),
        )
        assert second.returncode != 0, second.stderr
        assert "already owns this output directory" in second.stderr
        assert not (second_stage / "locked").exists()
        assert not (second_repo / "build/runtime-pack-selection.json").exists()
        assert (shared.parent / f"{shared.name}.release.lock").is_file()
        assert first_dist.resolve() == shared.resolve()
        assert (second_repo / "dist").resolve() == shared.resolve()
    finally:
        (first_stage / "continue").write_text("1\n", encoding="utf-8")
        if first.poll() is None:
            done = _finish(first)
        else:
            done = None
        _reap_own_child(first)
    if done is not None:
        assert done.returncode == 0, done.stderr


def test_production_entrypoint_has_no_fake_stage_escape() -> None:
    builder = RELEASE_BUILDER.read_text(encoding="utf-8")
    assert "VIBECRAFTED_RELEASE_FAKE_STAGES" not in builder
    assert "VIBECRAFTED_RELEASE_FAKE_KEYCHAIN" not in builder
    assert "release_run_fake_bounded_stages" not in builder
    assert "runtime_pack_selection_publish" in builder
    flight = FLIGHT_LIBRARY.read_text(encoding="utf-8")
    assert "never unlink" in flight
    assert "release_single_flight_acquire_output" in flight
    assert "release_single_flight_physical_identity" in flight
    assert 'case "$dist_abs" in' not in flight
