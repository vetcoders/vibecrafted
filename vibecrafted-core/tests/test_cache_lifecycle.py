from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import cache_lifecycle as cache
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
