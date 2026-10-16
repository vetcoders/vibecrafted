"""Disposable real Git/dispatch targets; no Cargo or live runtime mutation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import cache_lifecycle as cache
from vibecrafted_core.dispatch.schema import parse_dispatch
from vibecrafted_core.dispatch.supervisor import CellRun, DispatchSupervisor
from vibecrafted_core.dispatch.worktrees import WorktreeManager
from vibecrafted_core.server_supervisor import _atomic_json
from vibecrafted_core.settlement import TrustReceiptV1

pytestmark = pytest.mark.usefixtures("worker_claims")


def fixture(tmp_path, monkeypatch):
    home = tmp_path / "vc"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "agents@vetcoders.io"],
        ["config", "user.name", "fixture"],
        ["remote", "add", "origin", "https://github.com/vetcoders/fixture.git"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / ".gitignore").write_text("target/\n")
    (repo / "README.md").write_text("source\n")
    for args in (["add", "-A"], ["commit", "-qm", "seed"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    baseline = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    return home, repo, baseline


def record_run(home, root, run_id="provider-fixture", *, accepted=False):
    run = {"run_id": run_id, "root": str(root), "worker_alive": False}
    if accepted:
        trust = TrustReceiptV1.issue(
            repo_root=str(root),
            run_id=run_id,
            commit_sha="a" * 40,
            trust_verdict="pass",
            settlement_verdict="finalized",
            settlement_tui="f",
            settlement_revision=1,
            claim_digest="b" * 64,
        )
        run.update(
            settlement_source="trust",
            settlement_verdict="finalized",
            settlement_tui="f",
            settlement_revision=1,
            trust_receipt=trust.to_payload(),
        )
    _atomic_json(home / "control_plane/runs" / f"{run_id}.json", run)


def test_new_dispatch_target_binds_then_settles_before_retirement(
    tmp_path, monkeypatch
):
    home, repo, _ = fixture(tmp_path, monkeypatch)
    dispatch = parse_dispatch(f'''schema = "vibecrafted.dispatch.v1"
[meta]
name = "owned-target"
repo = "{repo}"
[policy]
repair_rounds = 0
await = {{ poll_s = 0.01, timeout_min = 1 }}
[[cuts]]
id = "one"
agent = "codex"
workflow = "implement"
prompt = "fixture"
  [[cuts.verify]]
  run = "echo accepted"
  expect = {{ contains = "accepted" }}
''')
    target = None

    def launch(cut, _prompt, kind):
        nonlocal target
        target = Path(cut.target_path)
        (target / "output").write_bytes(b"reconstructible")
        record_run(home, Path(cut.runtime_root))
        report = tmp_path / "report.md"
        report.write_text("fixture completed")
        proc = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id="provider-fixture",
            pid=proc.pid,
            report_path=str(report),
            proc=proc,
        )

    supervisor = DispatchSupervisor(
        dispatch,
        launcher=launch,
        artifacts_dir=tmp_path / "artifacts",
        run_id="dispatch-fixture",
        manage_worktrees=True,
    )
    result = supervisor.run()
    assert result.states == {"one": "[x]"}
    assert target is not None
    owner = json.loads(cache.owner_path(home, target).read_text())
    assert owner["run_id"] == "provider-fixture"
    assert owner["settled_dispatch"] == {"run_id": "dispatch-fixture", "cut_id": "one"}
    with pytest.raises(cache.CacheDeferred, match="not-settled-and-accepted"):
        cache.clean_cache(home, target, observe=lambda _: None)
    record_run(home, target.parent, accepted=True)
    with cache.build_lease(target), pytest.raises(BlockingIOError):
        cache.clean_cache(home, target, observe=lambda _: None)
    assert cache.clean_cache(home, target, observe=lambda _: None) == "deleted"
    assert target.parent.is_dir() and (target.parent / "README.md").is_file()
    assert Path(owner["registration"]).is_file()


@pytest.mark.parametrize("replacement", ["missing-proof", "directory", "symlink"])
def test_creation_proof_loss_or_replacement_retains(tmp_path, monkeypatch, replacement):
    home, repo, baseline = fixture(tmp_path, monkeypatch)
    geometry = WorktreeManager(repo).prepare("one", baseline)
    target = Path(geometry.target_path)
    proof = Path(geometry.cache_creation_receipt)
    record_run(home, target.parent)
    token = cache.reserve_dispatch_cache_attempt(home, geometry, "initial")
    if replacement == "missing-proof":
        proof.unlink()
    else:
        target.rename(target.with_name("original"))
        if replacement == "symlink":
            target.symlink_to(target.with_name("original"), target_is_directory=True)
        else:
            target.mkdir()
    assert cache.bind_dispatch_cache(
        home,
        geometry,
        "provider-fixture",
        attempt_token=token,
        dispatch_run_id="dispatch-fixture",
    ).startswith("retained-")
    assert not cache.owner_path(home, target).exists()


def test_reuse_and_repair_never_transfer_or_revive_old_owner(tmp_path, monkeypatch):
    home, repo, baseline = fixture(tmp_path, monkeypatch)
    manager = WorktreeManager(repo)
    geometry = manager.prepare("one", baseline)
    target = Path(geometry.target_path)
    record_run(home, target.parent, accepted=True)
    token = cache.reserve_dispatch_cache_attempt(home, geometry, "initial")
    assert (
        cache.bind_dispatch_cache(
            home,
            geometry,
            "provider-fixture",
            attempt_token=token,
            dispatch_run_id="dispatch-fixture",
        )
        == "bound"
    )
    # A later launch attempt first fences the previous acceptance, even if the
    # new launch is refused. Its original creation proof is single-use.
    cache.retain_cache_for_launch(home, target)
    record_run(home, target.parent, "repair-fixture", accepted=True)
    assert cache.bind_dispatch_cache(
        home, geometry, "repair-fixture", dispatch_run_id="dispatch-fixture"
    ).startswith("retained-")
    with pytest.raises(cache.CacheDeferred, match="later-launch"):
        cache.clean_cache(home, target, observe=lambda _: None)
    reused = manager.prepare("one", baseline, allow_reuse=True)
    assert not reused.cache_creation_receipt
    assert cache.bind_dispatch_cache(
        home, reused, "repair-fixture", dispatch_run_id="dispatch-fixture"
    ).startswith("retained-")
    assert (
        json.loads(cache.owner_path(home, target).read_text())["run_id"]
        == "provider-fixture"
    )


@pytest.mark.parametrize(
    "case", ["refused", "missing-id", "wrong-root", "replayed-initial"]
)
def test_failed_initial_attempt_cannot_later_claim_target(tmp_path, monkeypatch, case):
    home, repo, baseline = fixture(tmp_path, monkeypatch)
    geometry = WorktreeManager(repo).prepare("one", baseline)
    target = Path(geometry.target_path)
    token = cache.reserve_dispatch_cache_attempt(home, geometry, "initial")
    assert token
    record_run(home, repo if case == "wrong-root" else target.parent)
    if case not in {"refused", "replayed-initial"}:
        run_id = "" if case == "missing-id" else "provider-fixture"
        assert cache.bind_dispatch_cache(
            home,
            geometry,
            run_id,
            attempt_token=token,
            dispatch_run_id="dispatch-fixture",
        ).startswith("retained-")
    assert not cache.reserve_dispatch_cache_attempt(home, geometry, "repair1")
    assert not cache.reserve_dispatch_cache_attempt(home, geometry, "initial")
    assert cache.bind_dispatch_cache(
        home, geometry, "provider-fixture", dispatch_run_id="dispatch-fixture"
    ).startswith("retained-")
    assert not cache.owner_path(home, target).exists()


def test_existing_target_and_integrator_have_no_creation_authority(
    tmp_path, monkeypatch
):
    home, repo, baseline = fixture(tmp_path, monkeypatch)
    manager = WorktreeManager(repo)
    geometry = manager.prepare("one", baseline)
    reused = manager.prepare("one", baseline, allow_reuse=True)
    integrator = manager.prepare("integrator", baseline, integrator=True)
    record_run(home, Path(geometry.worktree_path))
    assert not reused.cache_creation_receipt and not integrator.cache_creation_receipt
    for candidate in (reused, integrator):
        assert cache.bind_dispatch_cache(
            home, candidate, "provider-fixture", dispatch_run_id="dispatch-fixture"
        ).startswith("retained-")
    assert not cache.owner_path(home, Path(geometry.target_path)).exists()


@pytest.mark.parametrize("accepted,run_id", [(False, "refused"), (True, "")])
def test_real_dispatch_refusal_or_absent_id_cannot_bind(
    tmp_path, monkeypatch, accepted, run_id
):
    home, repo, _ = fixture(tmp_path, monkeypatch)
    dispatch = parse_dispatch(f'''schema = "vibecrafted.dispatch.v1"
[meta]
name = "refused"
repo = "{repo}"
[policy]
repair_rounds = 0
await = {{ poll_s = 0.01, timeout_min = 1 }}
[[cuts]]
id = "one"
agent = "codex"
workflow = "implement"
prompt = "fixture"
  [[cuts.verify]]
  run = "echo ok"
  expect = {{ contains = "ok" }}
''')

    def launch(cut, _prompt, kind):
        report = tmp_path / "report.md"
        report.write_text("done")
        proc = subprocess.Popen(["/bin/sh", "-c", "exit 0"]) if accepted else None
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=accepted,
            run_id=run_id,
            report_path=str(report),
            pid=proc.pid if proc else None,
            proc=proc,
            error="fixture refusal",
        )

    supervisor = DispatchSupervisor(
        dispatch,
        launcher=launch,
        artifacts_dir=tmp_path / "artifacts",
        run_id="dispatch-refused",
        manage_worktrees=True,
    )
    supervisor.run()
    geometry = supervisor._geometries["one"]
    assert not cache.owner_path(home, Path(geometry.target_path)).exists()
    assert not cache.reserve_dispatch_cache_attempt(home, geometry, "initial")


def test_final_trust_without_admitted_dispatch_is_retained(tmp_path, monkeypatch):
    home, repo, baseline = fixture(tmp_path, monkeypatch)
    geometry = WorktreeManager(repo).prepare("one", baseline)
    target = Path(geometry.target_path)
    record_run(home, target.parent, accepted=True)
    token = cache.reserve_dispatch_cache_attempt(home, geometry, "initial")
    assert (
        cache.bind_dispatch_cache(
            home,
            geometry,
            "provider-fixture",
            attempt_token=token,
            dispatch_run_id="dispatch-fixture",
        )
        == "bound"
    )
    with pytest.raises(cache.CacheDeferred, match="dispatch-not-admitted"):
        cache.clean_cache(home, target, observe=lambda _: None)
    cache.mark_cache_eligible(
        home,
        target,
        run_id="provider-fixture",
        dispatch_run_id="dispatch-fixture",
        cut_id="one",
    )
    with pytest.raises((cache.CacheDeferred, OSError)):
        cache.clean_cache(home, target, observe=lambda _: None)
    assert target.is_dir()


def test_target_raced_in_before_exclusive_mkdir_is_never_claimed(tmp_path, monkeypatch):
    from vibecrafted_core.dispatch import worktrees

    home, repo, baseline = fixture(tmp_path, monkeypatch)
    original_run = worktrees._run

    def raced_in(parent, command, description):
        result = original_run(parent, command, description)
        root = Path(command[-2])
        (root / "target").mkdir()
        (root / "target/foreign").write_text("retain")
        return result

    monkeypatch.setattr(worktrees, "_run", raced_in)
    geometry = WorktreeManager(repo).prepare("one", baseline)
    assert not geometry.cache_creation_receipt
    assert (Path(geometry.target_path) / "foreign").read_text() == "retain"
    assert not cache.owner_path(home, Path(geometry.target_path)).exists()


def test_replacement_between_public_mkdir_and_identity_is_not_claimed(
    tmp_path, monkeypatch
):
    home, repo, baseline = fixture(tmp_path, monkeypatch)
    original_mkdir = Path.mkdir
    replaced = []

    def replace_after_mkdir(path, *args, **kwargs):
        existed = path.exists()
        result = original_mkdir(path, *args, **kwargs)
        if path.name == "target" and not existed and not replaced:
            old = path.with_name("original-target")
            path.rename(old)
            original_mkdir(path)
            (path / "foreign-output").write_text("foreign")
            replaced.append(old)
        return result

    monkeypatch.setattr(Path, "mkdir", replace_after_mkdir)
    geometry = WorktreeManager(repo).prepare("one", baseline)
    target = Path(geometry.target_path)
    if replaced:
        # The original implementation must not claim the replacement.
        assert not geometry.cache_creation_receipt
        assert (target / "foreign-output").read_text() == "foreign"
    else:
        # A held-object publication must bypass the public mkdir interval.
        assert geometry.cache_creation_receipt
    if geometry.cache_creation_receipt:
        creation = json.loads(Path(geometry.cache_creation_receipt).read_text())
        assert (creation["device"], creation["inode"]) == cache.identity(target)
    assert not cache.owner_path(home, target).exists()


def test_publication_replacement_cannot_claim_held_object(tmp_path, monkeypatch):
    home, repo, baseline = fixture(tmp_path, monkeypatch)
    publish = cache._publish_directory_noreplace

    def replace(parent_fd, source, destination):
        publish(parent_fd, source, destination)
        # Swap after atomic publication but before its object check.
        import os

        os.rename(
            destination, "original-target", src_dir_fd=parent_fd, dst_dir_fd=parent_fd
        )
        os.mkdir(destination, dir_fd=parent_fd)
        fd = os.open(
            destination + "/foreign-output",
            os.O_CREAT | os.O_WRONLY,
            0o600,
            dir_fd=parent_fd,
        )
        os.write(fd, b"foreign")
        os.close(fd)

    monkeypatch.setattr(cache, "_publish_directory_noreplace", replace)
    geometry = WorktreeManager(repo).prepare("one", baseline)
    target = Path(geometry.target_path)
    assert not geometry.cache_creation_receipt
    assert (target / "foreign-output").read_bytes() == b"foreign"
    assert not cache.owner_path(home, target).exists()


def test_exclusive_publication_unavailable_retains_unclaimed_target(
    tmp_path, monkeypatch
):
    home, repo, baseline = fixture(tmp_path, monkeypatch)

    def unavailable(*_args):
        raise cache.CacheDeferred("exclusive-directory-publication-unavailable")

    monkeypatch.setattr(cache, "_publish_directory_noreplace", unavailable)
    geometry = WorktreeManager(repo).prepare("one", baseline)
    assert not geometry.cache_creation_receipt
    assert Path(geometry.target_path).is_dir()
    assert not cache.owner_path(home, Path(geometry.target_path)).exists()
    assert not list(Path(geometry.worktree_path).glob(".vc-target-*"))


def test_build_command_registration_uses_held_publication(tmp_path, monkeypatch):
    home, repo, baseline = fixture(tmp_path, monkeypatch)
    geometry = WorktreeManager(repo).prepare("one", baseline)
    target = Path(geometry.worktree_path) / "task-output"
    record_run(home, target.parent)
    original_mkdir = Path.mkdir

    def replace_public_create(path, *args, **kwargs):
        existed = path.exists()
        result = original_mkdir(path, *args, **kwargs)
        if path == target and not existed:
            path.rename(path.with_name("original-output"))
            original_mkdir(path)
            (path / "foreign-output").write_text("preserve")
        return result

    monkeypatch.setattr(Path, "mkdir", replace_public_create)
    assert (
        cache.main(
            [
                "--root",
                str(target),
                "--home",
                str(home),
                "--run-id",
                "provider-fixture",
                "--",
                sys.executable,
                "-c",
                "import sys;from pathlib import Path;Path(sys.argv[1]).write_text('generated')",
                str(target / "output"),
            ]
        )
        == 0
    )
    owner = json.loads(cache.owner_path(home, target).read_text())
    assert (owner["device"], owner["inode"]) == cache.identity(target)
    assert not (target / "foreign-output").exists()
