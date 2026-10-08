"""Identity-qualified process control (vc-procs authority)."""

from __future__ import annotations

import copy
import signal

import pytest
from vibecrafted_core import process_control as pc
from vibecrafted_core import run_reaper


def entry(
    pid: int, ppid: int = 1, pgid: int | None = None, command: str = "node agent"
):
    return run_reaper.ProcessEntry(
        pid=pid, ppid=ppid, pgid=pid if pgid is None else pgid, command=command
    )


TERMINAL_RUN = {
    "run_id": "impl-test-0001",
    "state": "completed",
    "exit_code": 0,
    "worker_pgid": 4242,
}


def test_terminate_tree_captures_recursive_ppid_targets_before_signalling(monkeypatch):
    table = [entry(100), entry(200, ppid=100), entry(300, ppid=200), entry(400)]
    monkeypatch.setattr(pc, "build_process_table", lambda: tuple(table))
    monkeypatch.setattr(pc, "process_start_token", lambda pid, command: f"start:{pid}")
    signals = []

    def signal_pid(pid, sig):
        signals.append((pid, sig))
        if pid == 100:
            # TERM kills the root; descendants are reparented to init and
            # have unrelated PGIDs. The pre-signal capture must survive this.
            table[:] = [entry(200), entry(300), entry(400)]
        return "signalled"

    monkeypatch.setattr(pc, "_signal_pid", signal_pid)
    sleeps = []
    monkeypatch.setattr(
        pc.time, "sleep", lambda window: sleeps.append((window, list(signals)))
    )

    outcome = pc.terminate_process_tree(100, grace=0.25)

    assert outcome.ok
    assert signals == [
        (300, signal.SIGTERM),
        (200, signal.SIGTERM),
        (100, signal.SIGTERM),
        (300, signal.SIGKILL),
        (200, signal.SIGKILL),
    ]
    assert sleeps == [(0.25, signals[:3])]
    assert outcome.receipt["pids"] == [100, 200, 300]


def test_terminate_tree_does_not_kill_reused_pid_after_grace(monkeypatch):
    monkeypatch.setattr(
        pc, "build_process_table", lambda: [entry(100), entry(200, ppid=100)]
    )
    generation = {100: "original", 200: "original"}
    monkeypatch.setattr(pc, "process_start_token", lambda pid, command: generation[pid])
    signals = []
    monkeypatch.setattr(
        pc, "_signal_pid", lambda pid, sig: signals.append((pid, sig)) or "signalled"
    )
    monkeypatch.setattr(
        pc.time, "sleep", lambda window: generation.update({200: "reused"})
    )

    outcome = pc.terminate_process_tree(100, grace=0.1)

    assert outcome.ok
    assert (200, signal.SIGTERM) in signals
    assert (200, signal.SIGKILL) not in signals
    assert (100, signal.SIGKILL) in signals


@pytest.mark.parametrize("table", [[], [entry(100), entry(999, ppid=100)]])
def test_terminate_tree_refuses_missing_table_or_own_ancestor(monkeypatch, table):
    monkeypatch.setattr(pc, "build_process_table", lambda: table)
    monkeypatch.setattr(pc.os, "getpid", lambda: 999)
    signals = []
    monkeypatch.setattr(pc, "_signal_pid", lambda pid, sig: signals.append((pid, sig)))

    assert not pc.terminate_process_tree(100, grace=0).ok
    assert signals == []


def test_terminate_tree_reports_signal_permission_failure(monkeypatch):
    monkeypatch.setattr(pc, "build_process_table", lambda: [entry(100)])
    monkeypatch.setattr(pc, "process_start_token", lambda pid, command: "start:100")
    monkeypatch.setattr(pc, "_signal_pid", lambda pid, sig: "permission_denied")

    outcome = pc.terminate_process_tree(100, grace=0)

    assert not outcome.ok
    assert outcome.outcome == "signal_failed"
    assert "permission_denied" in outcome.detail


def test_snapshot_marks_owned_process_killable():
    table = [entry(900, pgid=4242, command="node worker")]
    snap = pc.snapshot_processes(
        table=table,
        runs=[TERMINAL_RUN],
        env_index={900: TERMINAL_RUN["run_id"]},
        self_pid=1000,
        env={},
    )
    assert snap["schema"] == pc.SCHEMA_VERSION
    rows = {r["pid"]: r for r in snap["processes"]}
    assert 900 in rows
    assert rows[900]["killable"] is True
    assert rows[900]["ownership"] == "proven"
    assert rows[900]["run_id"] == TERMINAL_RUN["run_id"]
    assert len(rows[900]["command_sha256"]) == 64
    assert rows[900]["start_token"]


def test_process_identity_receipt_rejects_reused_pid_or_wrong_run():
    original = [entry(904, pgid=5004, command="python worker.py")]
    receipt = pc.process_identity_receipt(
        904,
        run_id="impl-owned",
        table=original,
    )

    assert receipt is not None
    current, reason, identity = pc.validate_process_identity(
        receipt,
        expected_pid=904,
        expected_pgid=5004,
        expected_run_id="impl-owned",
        table=original,
        env_index={904: "impl-owned"},
    )
    assert current is True
    assert reason == "process_identity_current"
    assert identity is not None

    reused, reason, recaptured_identity = pc.validate_process_identity(
        receipt,
        expected_pid=904,
        expected_pgid=5004,
        expected_run_id="impl-owned",
        table=[entry(904, pgid=5004, command="python unrelated.py")],
        env_index={904: "impl-owned"},
    )
    assert reused is False
    assert reason == "process_identity_mismatch"
    assert recaptured_identity is not None

    wrong_run, reason, _identity = pc.validate_process_identity(
        receipt,
        expected_pid=904,
        expected_pgid=5004,
        expected_run_id="impl-owned",
        table=original,
        env_index={904: "some-other-run"},
    )
    assert wrong_run is False
    assert reason == "process_run_id_mismatch"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda receipt: receipt.pop("start_token"),
            "process_identity_receipt_invalid",
        ),
        (lambda receipt: receipt.pop("run_id"), "process_identity_receipt_invalid"),
        (
            lambda receipt: receipt.__setitem__("command_sha256", "g" * 64),
            "process_identity_receipt_invalid",
        ),
    ],
)
def test_process_identity_rejects_malformed_receipt_before_recapture(
    monkeypatch, mutation, reason
):
    original = [entry(905, pgid=5005, command="python worker.py")]
    receipt = pc.process_identity_receipt(
        905,
        run_id="impl-malformed",
        table=original,
    )
    assert receipt is not None
    malformed = copy.deepcopy(receipt)
    mutation(malformed)
    captures: list[int] = []
    monkeypatch.setattr(
        pc,
        "capture_process_identity",
        lambda pid, **_kwargs: captures.append(pid),
    )

    current, actual_reason, identity = pc.validate_process_identity(
        malformed,
        expected_pid=905,
        expected_pgid=5005,
        expected_run_id="impl-malformed",
        table=original,
        env_index={905: "impl-malformed"},
    )

    assert current is False
    assert actual_reason == reason
    assert identity is None
    assert captures == []


def test_snapshot_protects_vc_frame():
    table = [entry(901, pgid=4242, command="/usr/local/bin/vc-frame attach foo")]
    snap = pc.snapshot_processes(
        table=table,
        runs=[TERMINAL_RUN],
        env_index={901: TERMINAL_RUN["run_id"]},
        self_pid=1000,
        env={},
    )
    row = next(r for r in snap["processes"] if r["pid"] == 901)
    assert row["killable"] is False
    assert row["ownership"] == "protected"


def test_terminate_rejects_stale_start_token():
    table = [entry(902, pgid=4242, command="node worker")]
    live = pc.process_start_token(902, "node worker")
    live_hash = pc.command_sha256("node worker")
    signals: list[tuple[int, int]] = []

    def signaller(pid: int, sig: int) -> str:
        signals.append((pid, sig))
        return "signalled"

    outcome = pc.terminate_process(
        pid=902,
        expected_start="start:WRONG",
        expected_command_sha256=live_hash,
        expected_run_id=TERMINAL_RUN["run_id"],
        table=table,
        runs=[TERMINAL_RUN],
        env_index={902: TERMINAL_RUN["run_id"]},
        self_pid=1000,
        env={},
        signaller=signaller,
        alive_check=lambda _pid: True,
        sleeper=lambda _s: None,
        grace=0,
    )
    assert outcome.ok is False
    assert outcome.outcome == "stale_selection"
    assert signals == []
    _ = live  # document that live token differs


def test_terminate_allows_owned_process():
    cmd = "node worker"
    table = [entry(903, pgid=4242, command=cmd)]
    start = pc.process_start_token(903, cmd)
    cmd_hash = pc.command_sha256(cmd)
    signals: list[tuple[int, int]] = []

    def signaller(pid: int, sig: int) -> str:
        signals.append((pid, sig))
        return "signalled"

    alive = {903: True}

    def alive_check(pid: int) -> bool:
        return alive.get(pid, False)

    def sleeper(_s: float) -> None:
        alive[903] = False

    outcome = pc.terminate_process(
        pid=903,
        expected_start=start,
        expected_command_sha256=cmd_hash,
        expected_run_id=TERMINAL_RUN["run_id"],
        table=table,
        runs=[TERMINAL_RUN],
        env_index={903: TERMINAL_RUN["run_id"]},
        self_pid=1000,
        env={},
        signaller=signaller,
        alive_check=alive_check,
        sleeper=sleeper,
        grace=0.01,
    )
    assert outcome.ok is True
    assert outcome.outcome in {"terminated", "killed"}
    assert signals[0] == (903, signal.SIGTERM)
