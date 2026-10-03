"""Ledger exclusion and claim-admission hold time.

Finding 1 forces the nullable host-lock branch (the non-POSIX shape) and
requires both cross-process writes to survive. Finding 2 times a scheduler
write against a slow git observation. Finding 3 records the local-trust
admission behavior without changing it.
"""

from __future__ import annotations

import inspect
import json
import multiprocessing
import subprocess
import threading
import time
from pathlib import Path

import pytest
import vibecrafted_core.portable_lock as ledger_lock
from vibecrafted_core.delivery.store import atomic_write_json
from vibecrafted_core.dispatch import receipts
from vibecrafted_core.dispatch.claims import submit_claim
from vibecrafted_core.dispatch.model import Cut
from vibecrafted_core.dispatch.receipts import DispatchReceiptStore

_HOLD_S = 0.45
_GIT_SLEEP_S = 0.7
_GIT_HOLD_LIMIT_S = 0.3


def _cut(cut_id: str = "w1") -> Cut:
    return Cut(
        id=cut_id,
        phase="build",
        agent="codex",
        workflow="implement",
        resolved_workflow="implement",
    )


def _store(root: Path, run_id: str, *, create: bool) -> DispatchReceiptStore:
    return DispatchReceiptStore(run_id, (_cut(),), root=root, create=create)


def _apply_missing_host_lock(receipts) -> None:
    """Blank the lock only while the ledger still treats it as optional."""
    source = inspect.getsource(receipts.DispatchReceiptStore._locked_ledger)
    if "fcntl is not None" in source:
        receipts.fcntl = None


def _cross_process_writer(
    root: str,
    run_id: str,
    cut_id: str,
    field: str,
    value: str,
    barrier,
    queue,
) -> None:
    _apply_missing_host_lock(receipts)
    real_write = atomic_write_json

    def slow_write(path, payload):
        time.sleep(_HOLD_S)
        real_write(path, payload)

    receipts.atomic_write_json = slow_write
    store = _store(Path(root), run_id, create=False)
    barrier.wait(5)
    store.update(cut_id, **{field: value})
    queue.put("ok")


def test_cross_process_ledger_writes_survive_without_host_fcntl(tmp_path: Path):
    """Two processes merge distinct fields. A missing flock keeps only one."""
    run_id = "ledger-lock"
    root = tmp_path / "dispatch"
    _store(root, run_id, create=True)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    writers = (
        ("writer_a", "a"),
        ("writer_b", "b"),
    )
    processes = [
        ctx.Process(
            target=_cross_process_writer,
            args=(str(root), run_id, "w1", field, value, barrier, queue),
        )
        for field, value in writers
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    outcomes = [queue.get(timeout=5) for _ in processes]
    assert outcomes == ["ok", "ok"]
    payload = json.loads((root / "receipts.json").read_text(encoding="utf-8"))
    entry = payload["cuts"]["w1"]
    assert entry["writer_a"] == "a"
    assert entry["writer_b"] == "b"

    assert receipts.fcntl is ledger_lock


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _claim_repo(tmp_path: Path) -> tuple[DispatchReceiptStore, dict[str, str], str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "claim fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "source.txt").write_text("checkpoint\n")
    _git(repo, "add", "source.txt")
    _git(repo, "commit", "-qm", "fixture checkpoint")
    report = tmp_path / "report.md"
    report.write_text("Measured the actual checkpoint.\n")
    store = _store(tmp_path / "ledger", "dispatch-claim-lock", create=True)
    store.update(
        "w1",
        "active",
        worktree_path=str(repo),
        report_path=str(report),
        provider_run_id="worker-1",
        attempt="initial",
    )
    claim = {
        "run_id": store.run_id,
        "cut_id": "w1",
        "commit_sha": _git(repo, "rev-parse", "HEAD"),
        "report_path": str(report),
        "measurements": ["measured source.txt at checkpoint"],
    }
    return store, claim, _git(repo, "rev-parse", "HEAD")


def test_slow_claim_git_does_not_hold_the_ledger_lock(tmp_path, monkeypatch):
    """A scheduler write must proceed while claim git observation is still running."""
    store, claim, sha = _claim_repo(tmp_path)
    started = threading.Event()

    def slow_git(_root: str) -> tuple[str, str]:
        started.set()
        time.sleep(_GIT_SLEEP_S)
        return sha, ""

    monkeypatch.setattr(
        "vibecrafted_core.dispatch.claims.claim_git_state",
        slow_git,
    )
    elapsed: dict[str, float] = {}

    def scheduler() -> None:
        assert started.wait(2)
        began = time.perf_counter()
        store.update("w1", scheduler_probe="landed")
        elapsed["seconds"] = time.perf_counter() - began

    thread = threading.Thread(target=scheduler)
    thread.start()
    submit_claim(claim, store=store)
    thread.join(5)
    assert not thread.is_alive()
    assert elapsed["seconds"] < _GIT_HOLD_LIMIT_S
    entry = store.cut("w1")
    assert entry["scheduler_probe"] == "landed"
    assert entry["claim_marker"] == "[~]"


def test_local_caller_can_supersede_an_inflight_verification(tmp_path: Path):
    """Any local process that can read the ledger can bump claim_sequence.

    record_verification then refuses the in-flight proof. This is the current
    single-user trust model. It is pinned here so a later admission gate has
    a regression, and this test does not add that gate.
    """
    store, claim, sha = _claim_repo(tmp_path)
    submit_claim(claim, store=store)
    sequence = store.cut("w1")["claim_sequence"]
    other = DispatchReceiptStore(store.run_id, (), root=store.root, create=False)
    submit_claim(claim, store=other)
    assert store.cut("w1")["claim_sequence"] == sequence + 1
    recorded = store.record_verification(
        "w1",
        {
            "rule": "VERIFICATION_RULE.md",
            "passed": True,
            "commit_sha": sha,
            "claim_sequence": sequence,
        },
        [{"ok": True, "matcher_result": "pass"}],
    )
    assert recorded is False
    entry = store.cut("w1")
    assert entry["claim_sequence"] == sequence + 1
    assert entry.get("state") != "verified"
    assert not entry.get("verification_rule")


def test_nullable_lock_branch_is_the_precondition_for_the_race():
    """The optional host-lock branch must stay gone after the ledger cut."""
    source = inspect.getsource(receipts.DispatchReceiptStore._locked_ledger)
    if "fcntl is not None" in source:
        pytest.fail("host lock is optional; cross-process merge is not excluded")
