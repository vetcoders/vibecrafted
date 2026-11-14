from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from vibecrafted_core import cache_lifecycle as cache
from vibecrafted_core.marbles_reception import readonly_command, tree_receipt
from vibecrafted_core.server_supervisor import _atomic_json
from vibecrafted_core.settlement import TrustReceiptV1


def owned(tmp_path: Path, *, accepted: bool = True):
    home = tmp_path / "vc"
    root = home / "worktrees" / "task"
    path = root / "target"
    path.mkdir(parents=True)
    (path / "artifact").write_bytes(b"generated")
    run = {"run_id": "fixture-run", "root": str(root), "worker_alive": False}
    if accepted:
        trust = TrustReceiptV1.issue(
            repo_root=str(root),
            run_id="fixture-run",
            commit_sha="a" * 40,
            trust_verdict="pass",
            settlement_verdict="finalized",
            settlement_tui="f",
            settlement_revision=1,
            claim_digest="b" * 64,
        )
        run.update(
            settlement_source="trust",
            settlement_tui="f",
            settlement_verdict="finalized",
            settlement_revision=1,
            trust_receipt=trust.to_payload(),
        )
    _atomic_json(home / "control_plane/runs/fixture-run.json", run)
    with cache.build_lease(path):
        cache.register_cache(
            home, path, "fixture-run", created_identity=cache.identity(path)
        )
    return home, path


def test_owned_cache_retires_only_after_trust_acceptance(tmp_path):
    home, path = owned(tmp_path, accepted=False)
    with pytest.raises(cache.CacheDeferred, match="not-settled-and-accepted"):
        cache.clean_cache(home, path, observe=lambda _: None)
    assert (path / "artifact").read_bytes() == b"generated"
    assert (
        cache.mark_cache_eligible(
            home,
            path,
            run_id="fixture-run",
            dispatch_run_id="dispatch-fixture",
            cut_id="one",
        )
        == "eligible-pending-final-acceptance"
    )
    with pytest.raises(cache.CacheDeferred):
        cache.clean_cache(home, path, observe=lambda _: None)


def test_accepted_owned_cache_deleted_receipts_retained(tmp_path):
    home, path = owned(tmp_path)
    assert cache.clean_cache(home, path, observe=lambda _: None) == "deleted"
    assert not path.exists()
    assert cache.owner_path(home, path).exists()
    assert (home / "control_plane/runs/fixture-run.json").exists()
    assert path.parent.exists()


def test_chain_gap_and_containing_lease_veto(tmp_path):
    home, path = owned(tmp_path)
    # The containing owner holds SH across two commands and their idle gap.
    with cache.build_lease(path.parent):
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        with pytest.raises(BlockingIOError):
            cache.clean_cache(home, path, observe=lambda _: None)
        subprocess.run([sys.executable, "-c", "pass"], check=True)
    assert cache.clean_cache(home, path, observe=lambda _: None) == "deleted"


@pytest.mark.parametrize("veto", ["open-file", "unknown-lsof", "unknown-ps"])
def test_uncertain_or_live_observation_restores_staging(tmp_path, veto):
    home, path = owned(tmp_path)
    calls = []

    def observe(candidate):
        calls.append(candidate)
        if candidate != path:
            raise cache.CacheDeferred(veto)

    with pytest.raises(cache.CacheDeferred, match=veto):
        cache.clean_cache(home, path, observe=observe)
    assert len(calls) == 2
    assert (path / "artifact").read_bytes() == b"generated"
    assert not list(path.parent.glob(".vc-clean-*"))


def test_cleaner_holds_exclusive_lease_through_staging(tmp_path):
    home, path = owned(tmp_path)

    def observe(candidate):
        with pytest.raises(BlockingIOError), cache.build_lease(candidate):
            pass

    assert cache.clean_cache(home, path, observe=observe) == "deleted"


def test_competing_cleaner_is_excluded(tmp_path):
    home, path = owned(tmp_path)

    def observe(candidate):
        if candidate == path:
            with pytest.raises(BlockingIOError):
                cache.clean_cache(home, path, observe=lambda _: None)

    assert cache.clean_cache(home, path, observe=observe) == "deleted"


def test_restore_refuses_a_path_created_in_the_publication_gap(tmp_path, monkeypatch):
    home, path = owned(tmp_path)
    publish = cache._publish_directory_noreplace

    def collision(parent_fd, source, destination):
        if destination == path.name:
            path.mkdir()
            (path / "foreign").write_text("keep")
        publish(parent_fd, source, destination)

    def veto(candidate):
        if candidate != path:
            raise cache.CacheDeferred("veto-stage")

    monkeypatch.setattr(cache, "_publish_directory_noreplace", collision)
    with pytest.raises(cache.CacheDeferred, match="veto-stage"):
        cache.clean_cache(home, path, observe=veto)
    assert (path / "foreign").read_text() == "keep"
    assert (next(path.parent.glob(".vc-clean-*")) / "artifact").exists()


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_replacement_cannot_inherit_cache_identity(tmp_path, replacement):
    home, path = owned(tmp_path)
    original = path.with_name("old")
    path.rename(original)
    if replacement == "symlink":
        path.symlink_to(original, target_is_directory=True)
    else:
        path.mkdir()
        (path / cache.LEASE_NAME).touch(mode=0o600)
    with pytest.raises((cache.CacheDeferred, OSError)):
        cache.clean_cache(home, path, observe=lambda _: None)
    assert (original / "artifact").exists()


def test_source_inserted_during_staging_is_restored(tmp_path):
    home, path = owned(tmp_path)

    def observe(candidate):
        if candidate != path:
            (candidate / "Cargo.toml").write_text("source")

    with pytest.raises(cache.CacheDeferred):
        cache.clean_cache(home, path, observe=observe)
    assert (path / "Cargo.toml").read_text() == "source"


def test_symlink_lease_refused_without_touching_target(tmp_path):
    home, path = owned(tmp_path)
    external = tmp_path / "external"
    external.write_text("preserve")
    (path / cache.LEASE_NAME).unlink()
    (path / cache.LEASE_NAME).symlink_to(external)
    with pytest.raises(OSError):
        cache.clean_cache(home, path, observe=lambda _: None)
    assert external.read_text() == "preserve"


def test_staging_recreation_keeps_both_paths_and_durable_leftover(tmp_path):
    home, path = owned(tmp_path)

    def observe(candidate):
        if candidate != path:
            path.mkdir()
            (path / "foreign").write_text("keep")

    with pytest.raises(cache.CacheDeferred):
        cache.clean_cache(home, path, observe=observe)
    assert (path / "foreign").exists()
    stage = next(path.parent.glob(".vc-clean-*"))
    assert (stage / "artifact").exists()
    assert str(stage) in (home / "store/housekeeping/leftovers.txt").read_text()


def test_lease_command_keeps_lock_across_child_chain(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    script = """import fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDWR)
try:
 fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
 sys.exit(0)
sys.exit(1)
"""
    assert (
        cache.main(
            [
                "--root",
                str(target),
                "--",
                sys.executable,
                "-c",
                script,
                str(target / cache.LEASE_NAME),
            ]
        )
        == 0
    )


def test_missing_owner_is_retained_even_if_target_looks_generated(tmp_path):
    home, path = owned(tmp_path)
    cache.owner_path(home, path).unlink()
    with pytest.raises(cache.CacheDeferred):
        cache.clean_cache(home, path, observe=lambda _: None)
    assert path.exists()


def test_capacity_measures_each_filesystem_without_summing(tmp_path, monkeypatch):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    real = Path.stat

    def stat_path(path, **kwargs):
        info = real(path, **kwargs)
        values = list(info)
        values[2] = 11 if path == one else 22
        return os.stat_result(values)

    monkeypatch.setattr(Path, "stat", stat_path)
    result = cache.capacity([one, two])
    assert len(result["volumes"]) == 2
    assert result["capacity_errors"] == []


def test_real_open_file_veto(tmp_path):
    home, path = owned(tmp_path)
    with (
        (path / "artifact").open("rb"),
        pytest.raises(cache.CacheDeferred, match="live-open-file"),
    ):
        cache.clean_cache(home, path)


def test_lease_identity_replacement_during_observation_vetoes(tmp_path):
    home, path = owned(tmp_path)

    def replace_lease(candidate):
        if candidate == path:
            (path / cache.LEASE_NAME).unlink()
            (path / cache.LEASE_NAME).touch(mode=0o600)

    with pytest.raises(cache.CacheDeferred, match="lease-identity-changed"):
        cache.clean_cache(home, path, observe=replace_lease)
    assert (path / "artifact").exists()


def test_unknown_process_observation_is_not_empty_observation(tmp_path, monkeypatch):
    home, path = owned(tmp_path)
    monkeypatch.setattr(
        cache.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "unavailable"),
    )
    with pytest.raises(cache.CacheDeferred, match="process-observation-uncertain"):
        cache.clean_cache(home, path)
    assert path.exists()


def test_lease_outside_scan_root_does_not_claim_whole_home(tmp_path):
    home, path = owned(tmp_path)
    with cache.build_lease(home):
        assert cache.clean_cache(home, path, observe=lambda _: None) == "deleted"
    assert cache.owner_path(home, path).is_file()


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS READ guard")
@pytest.mark.parametrize("existing", [True, False])
def test_native_read_guard_holds_existing_build_leases(tmp_path, existing):
    root = tmp_path / "repo"
    shared = tmp_path / "shared"
    shared.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(shared), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "agents@vetcoders.io")
    git("config", "user.name", "Lease fixture")
    (shared / "source.txt").write_text("preserve source\n")
    (shared / ".gitignore").write_text(".venv/\n")
    git("add", "--", "source.txt", ".gitignore")
    git("commit", "-qm", "private lease fixture")
    git("worktree", "add", "-b", "reader", str(root), "HEAD")
    targets = [root / ".venv", root / "vibecrafted-core/.venv"]
    leases = [target / cache.LEASE_NAME for target in targets]
    for target, path in zip(targets, leases, strict=True):
        target.mkdir(parents=True)
        if existing:
            path.write_bytes(b"")
            subprocess.run(
                ["git", "-C", str(root), "check-ignore", "-q", "--", str(path)],
                check=True,
                timeout=15,
            )
    before = tree_receipt(str(root))
    inodes = [path.stat().st_ino for path in leases] if existing else []
    # Each contender opens its own descriptor/process, never inherits the holder.
    contender = """import fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDONLY)
try:
 fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
 sys.exit(23)
sys.exit(0)
"""
    canary = """import errno,fcntl,json,os,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]); contender=sys.argv[2]; observations=[]
for relative in ['.venv/.vc-build-lease','vibecrafted-core/.venv/.vc-build-lease']:
 path=root/relative; expected=path.stat(); matches=[]
 for fd in range(3,256):
  try: info=os.fstat(fd)
  except OSError: continue
  if (info.st_dev,info.st_ino)==(expected.st_dev,expected.st_ino): matches.append(fd)
 assert len(matches)==1, matches
 fd=matches[0]
 assert fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE==os.O_RDONLY
 if relative.startswith('.venv/'):
  assert fd==int(os.environ['VIBECRAFTED_BUILD_LEASE_FD'])
 for _ in range(2):
  result=subprocess.run([sys.executable,'-I','-B','-c',contender,str(path)],timeout=10,check=False)
  assert result.returncode==23, result.returncode
 observations.append({'lease':relative,'mode':'O_RDONLY','exclusive_blocked':True})
for path in [root/'source.txt',Path(sys.argv[3])/'config']:
 try: fd=os.open(path,os.O_RDWR)
 except OSError as exc: assert exc.errno==errno.EPERM
 else: os.close(fd); raise AssertionError('source/Git write admitted')
print(json.dumps(observations))
"""
    worker = (
        "import sys; sys.path.insert(0,sys.argv.pop(1)); "
        "from vibecrafted_core.cache_lifecycle import main; raise SystemExit(main())"
    )
    command, guard = readonly_command(
        str(root),
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            worker,
            str(Path(cache.__file__).resolve().parents[1]),
            "--root",
            str(targets[0]),
            "--lease-root",
            str(targets[1]),
            "--",
            sys.executable,
            "-I",
            "-B",
            "-c",
            canary,
            str(root),
            contender,
            str(shared / ".git"),
        ],
    )
    result = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=30
    )
    assert tree_receipt(str(root)) == before
    assert guard["kind"] == "macos_source_write_deny"
    assert guard["git_common_dir"] == str(shared / ".git")
    if existing:
        assert result.returncode == 0, result.stderr
        assert len(json.loads(result.stdout)) == 2
        assert [path.stat().st_ino for path in leases] == inodes
        for path in leases:
            subprocess.run(
                [sys.executable, "-I", "-B", "-c", contender, str(path)],
                check=True,
                timeout=10,
            )
    else:
        assert result.returncode == 75, result.stderr
        assert not result.stdout
        assert "Operation not permitted" in result.stderr
        assert all(not path.exists() for path in leases)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor access")
def test_build_lease_creates_readonly_cloexec_holder(tmp_path):
    import fcntl

    path = tmp_path / cache.LEASE_NAME
    with cache.build_lease(tmp_path) as fd:
        assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        assert not os.get_inheritable(fd)
        with pytest.raises(BlockingIOError), cache.lease(path, exclusive=True):
            pass
    with cache.lease(path, exclusive=True):
        pass
    with pytest.raises(OSError) as closed:
        os.fstat(fd)
    assert closed.value.errno == errno.EBADF


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "foreign"])
def test_shared_lease_rejects_unsafe_identity(tmp_path, monkeypatch, kind):
    path = tmp_path / cache.LEASE_NAME
    external = tmp_path / "external"
    external.write_bytes(b"preserve")
    if kind == "symlink":
        path.symlink_to(external)
    elif kind == "hardlink":
        os.link(external, path)
    elif kind == "directory":
        path.mkdir()
    else:
        path.touch()
        monkeypatch.setattr(cache.os, "getuid", lambda: external.stat().st_uid + 1)
    with (
        pytest.raises((OSError, cache.CacheDeferred)),
        cache.lease(path, exclusive=False, create=True),
    ):
        pytest.fail("unsafe shared lease admitted")
    assert external.read_bytes() == b"preserve"


@pytest.mark.parametrize("phase", ["open", "flock", "hardlink-after-flock"])
def test_shared_lease_rejects_acquisition_identity_drift(tmp_path, monkeypatch, phase):
    path = tmp_path / cache.LEASE_NAME
    path.touch()
    original_open, original_flock = os.open, cache.lock.flock

    def change():
        if phase == "hardlink-after-flock":
            os.link(path, tmp_path / "alias")
        else:
            path.rename(tmp_path / "old")
            os.close(original_open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))

    def open_changed(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        change()
        return fd

    def flock_changed(*args):
        original_flock(*args)
        change()

    if phase == "open":
        monkeypatch.setattr(cache.os, "open", open_changed)
    else:
        monkeypatch.setattr(cache.lock, "flock", flock_changed)
    with pytest.raises(cache.CacheDeferred), cache.build_lease(tmp_path):
        pytest.fail("changed identity admitted")


def test_missing_shared_lease_never_retries_a_creation_race(tmp_path, monkeypatch):
    path = tmp_path / cache.LEASE_NAME
    original_open = os.open
    calls = []

    def competing_open(name, flags, mode=0o777):
        calls.append(flags)
        if len(calls) == 1:
            path.write_text("competitor")
            raise FileNotFoundError(errno.ENOENT, "absent at first open", name)
        return original_open(name, flags, mode)

    monkeypatch.setattr(cache.os, "open", competing_open)
    with pytest.raises(FileExistsError), cache.build_lease(tmp_path):
        pytest.fail("creation race admitted")
    assert len(calls) == 2
    assert path.read_text() == "competitor"


def test_windows_shared_lease_retains_writable_lock_byte_contract(
    tmp_path, monkeypatch
):
    # Exercise the platform choice on Darwin; this is not Windows execution.
    proxy = SimpleNamespace(**vars(os))
    proxy.name = "nt"
    monkeypatch.setattr(cache, "os", proxy)

    def writable_lock_byte(fd, operation):
        assert operation == cache.lock.LOCK_SH | cache.lock.LOCK_NB
        os.write(fd, b"\n")  # portable_lock's Windows empty-region requirement

    monkeypatch.setattr(cache.lock, "flock", writable_lock_byte)
    with cache.build_lease(tmp_path):
        pass
    assert (tmp_path / cache.LEASE_NAME).read_bytes() == b"\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS READ guard")
def test_native_read_guard_rejects_fifo_with_finite_admission(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, timeout=15)
    target = root / ".venv"
    target.mkdir()
    path = target / cache.LEASE_NAME
    os.mkfifo(path, 0o600)
    before = path.lstat()
    worker = (
        "import sys; sys.path.insert(0,sys.argv.pop(1)); "
        "from vibecrafted_core.cache_lifecycle import main; raise SystemExit(main())"
    )
    command, _ = readonly_command(
        str(root),
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            worker,
            str(Path(cache.__file__).resolve().parents[1]),
            "--root",
            str(target),
            "--",
            sys.executable,
            "-c",
            "print('not admitted')",
        ],
    )
    result = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 75, result.stderr
    assert "unsafe-build-lease" in result.stderr
    assert not result.stdout
    assert path.lstat().st_ino == before.st_ino
