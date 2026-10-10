"""Settlement layer contract — typed terminals, gc gate, await persistence, orphans."""

from __future__ import annotations

import datetime as dt
import json
import multiprocessing
import os
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import control_plane
from vibecrafted_core.settlement import (
    SETTLEMENT_EVENT_KIND,
    SETTLEMENT_EVENT_SCHEMA,
    BareMarkdownError,
    Settlement,
    SettlementVerdict,
    board_fxn_counts,
    can_archive,
    claim_digest_from_payload,
    is_untitled_markdown,
    orphan_markdown_paths,
    orphan_settlement_payloads,
    persist_await_verdict,
    persist_settlement_to_meta,
    require_bound_markdown,
    settle_payload,
    tui_key_for,
)


def _write_meta(home: Path, payload: dict[str, object]) -> Path:
    reports = home / "artifacts" / "Vetcoders" / "vibecrafted" / "2026_0721" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{payload['run_id']}.meta.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _settlement_events(home: Path) -> list[dict[str, object]]:
    path = home / "control_plane" / "events.jsonl"
    if not path.is_file():
        return []
    return [
        event
        for event in (
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        )
        if event.get("kind") == SETTLEMENT_EVENT_KIND
    ]


def _sync_stale_projection_after_release(
    home: str,
    run_id: str,
    ready: Any,
    release: Any,
    sender: Connection,
) -> None:
    os.environ["VIBECRAFTED_HOME"] = home
    original_write = control_plane._write_run_snapshot
    paused = False

    def _barrier_write(
        path: Path,
        previous: dict[str, Any] | None,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal paused
        if not paused and candidate.get("run_id") == run_id:
            paused = True
            ready.set()
            if not release.wait(10):
                raise RuntimeError("stale settlement writer was never released")
        return original_write(path, previous, candidate)

    control_plane._write_run_snapshot = _barrier_write
    board = control_plane.sync_state(only_run_id=run_id)
    projected = next(run for run in board["recent_runs"] if run.get("run_id") == run_id)
    sender.send(projected)
    sender.close()


def test_tui_key_mapping() -> None:
    assert tui_key_for(SettlementVerdict.FINALIZED) == "f"
    assert tui_key_for(SettlementVerdict.FAILED) == "x"
    assert tui_key_for(SettlementVerdict.INVALID) == "x"
    assert tui_key_for(SettlementVerdict.NEEDS_ATTENTION) == "n"
    assert tui_key_for("unknown") == "n"


def test_exit_zero_without_report_is_needs_attention() -> None:
    settlement = settle_payload(
        {
            "state": "completed",
            "exit_code": 0,
            "report": "",
            "agent": "codex",
            "skill": "workflow",
        }
    )
    assert settlement is not None
    assert settlement.verdict is SettlementVerdict.NEEDS_ATTENTION
    assert settlement.tui_key == "n"
    assert "without_report" in settlement.reason


def test_unsealed_kernel_axes_reason_is_not_rewritten(tmp_path: Path) -> None:
    """Polarized: kernel receipt present → axes_* reason stays; no legacy rewrite."""
    report = tmp_path / "report.md"
    report.write_text("# done\n", encoding="utf-8")
    settlement = settle_payload(
        {
            "state": "report_validated",
            "exit_code": 0,
            "report": str(report),
            "agent": "codex",
            "skill": "workflow",
            "prompt": "settle the layer",
            "proof_state": "undeclared",
            "delivery_state": "unverified",
        }
    )
    assert settlement is not None
    assert settlement.verdict is SettlementVerdict.NEEDS_ATTENTION
    assert settlement.reason == "axes_e=none_p=undeclared_d=unverified"
    assert settlement.claim_digest


def test_legacy_report_without_seal_when_no_kernel_axes(tmp_path: Path) -> None:
    """No kernel receipt: legacy finalize demotes with report_without_seal."""
    report = tmp_path / "report.md"
    report.write_text("# done\n", encoding="utf-8")
    settlement = settle_payload(
        {
            "state": "report_validated",
            "exit_code": 0,
            "report": str(report),
            "agent": "codex",
            "skill": "workflow",
            "prompt": "settle the layer",
            # deliberately no proof_state / delivery_state / delivery_axes
        }
    )
    assert settlement is not None
    assert settlement.verdict is SettlementVerdict.NEEDS_ATTENTION
    assert settlement.reason == "report_without_seal"
    assert settlement.claim_digest


def test_claim_report_and_seal_is_finalized(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text(
        "---\nrun_id: sealed-1\nagent: codex\nskill: workflow\n"
        "status: completed\nfinalized: true\nclaim: sealed delivery succeeded\n"
        "---\nbody\n",
        encoding="utf-8",
    )
    settlement = settle_payload(
        {
            "run_id": "sealed-1",
            "state": "report_validated",
            "exit_code": 0,
            "report": str(report),
            "agent": "codex",
            "skill": "workflow",
            "prompt": "settle the layer",
            "proof_state": "passed",
            "delivery_state": "sealed",
        }
    )
    assert settlement is not None
    assert settlement.verdict is SettlementVerdict.FINALIZED
    assert settlement.tui_key == "f"
    assert settlement.reason == "claim_report_and_seal"
    assert settlement.source == "sealed"


def test_validated_report_can_self_attest_without_kernel_seal(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text(
        "---\nrun_id: attested-1\nagent: codex\nskill: marbles\n"
        "status: completed\nfinalized: true\nclaim: settlement pipe is truthful\n"
        "---\nbody\n",
        encoding="utf-8",
    )
    settlement = settle_payload(
        {
            "run_id": "attested-1",
            "state": "report_validated",
            "exit_code": 0,
            "report": str(report),
            "proof_state": "undeclared",
            "delivery_state": "unverified",
        }
    )
    assert settlement is not None
    assert settlement.verdict is SettlementVerdict.FINALIZED
    assert settlement.source == "self_attested"
    assert settlement.reason == "report_self_attested"
    assert settlement.claim_digest


@pytest.mark.parametrize(
    ("report_digest", "expected_verdict"),
    (
        ("9e0d59e1dc48bc42", SettlementVerdict.FINALIZED),
        ("deadbeefdeadbeef", SettlementVerdict.NEEDS_ATTENTION),
        ("", SettlementVerdict.NEEDS_ATTENTION),
    ),
)
def test_lifecycle_self_attestation_must_match_machine_bound_claim(
    tmp_path: Path,
    report_digest: str,
    expected_verdict: SettlementVerdict,
) -> None:
    report = tmp_path / "report.md"
    digest_line = f"claim_digest: {report_digest}\n" if report_digest else ""
    report.write_text(
        "---\nrun_id: attested-bound\nagent: codex\nskill: polarize\n"
        "status: completed\nfinalized: true\nclaim: exact mission completed\n"
        f"{digest_line}---\nbody\n",
        encoding="utf-8",
    )

    settlement = settle_payload(
        {
            "run_id": "attested-bound",
            "state": "report_validated",
            "exit_code": 0,
            "report": str(report),
            "claim_digest": "9e0d59e1dc48bc42",
            "proof_state": "undeclared",
            "delivery_state": "unverified",
        }
    )

    assert settlement is not None
    assert settlement.verdict is expected_verdict
    if expected_verdict is SettlementVerdict.FINALIZED:
        assert settlement.source == "self_attested"
        assert settlement.claim_digest == "9e0d59e1dc48bc42"


def test_machine_binding_refutes_persisted_self_attestation_for_another_claim(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.md"
    report.write_text(
        "---\nrun_id: rebound\nagent: codex\nskill: polarize\n"
        "finalized: true\nclaim: wrong mission\nclaim_digest: deadbeefdeadbeef\n"
        "---\nbody\n",
        encoding="utf-8",
    )
    payload = {
        "run_id": "rebound",
        "state": "report_validated",
        "exit_code": 0,
        "report": str(report),
        "claim_digest": "9e0d59e1dc48bc42",
        "settlement_verdict": "finalized",
        "settlement_reason": "report_self_attested",
        "settlement_source": "self_attested",
        "settlement_claim_digest": "deadbeefdeadbeef",
    }

    settlement = settle_payload(payload)

    assert settlement is not None
    assert settlement.verdict is SettlementVerdict.NEEDS_ATTENTION
    assert settlement.claim_digest == "9e0d59e1dc48bc42"


@pytest.mark.parametrize(
    ("finalized", "claim", "report_run_id"),
    (
        ("false", "settlement pipe is truthful", "attested-2"),
        ("true", "", "attested-2"),
        ("true", "settlement pipe is truthful", "different-run"),
    ),
)
def test_incomplete_self_attestation_stays_needs_attention(
    tmp_path: Path, finalized: str, claim: str, report_run_id: str
) -> None:
    report = tmp_path / "report.md"
    report.write_text(
        f"---\nrun_id: {report_run_id}\nagent: codex\nskill: marbles\n"
        f"status: completed\nfinalized: {finalized}\nclaim: {claim}\n---\nbody\n",
        encoding="utf-8",
    )
    settlement = settle_payload(
        {
            "run_id": "attested-2",
            "state": "report_validated",
            "exit_code": 0,
            "report": str(report),
            "prompt": "settle the layer",
            "proof_state": "undeclared",
            "delivery_state": "unverified",
        }
    )
    assert settlement is not None
    assert settlement.verdict is SettlementVerdict.NEEDS_ATTENTION


def test_kernel_failure_refutes_self_attestation(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text(
        "---\nrun_id: attested-3\nagent: codex\nskill: marbles\n"
        "status: completed\nfinalized: true\nclaim: claimed success\n---\nbody\n",
        encoding="utf-8",
    )
    settlement = settle_payload(
        {
            "run_id": "attested-3",
            "state": "report_validated",
            "exit_code": 0,
            "report": str(report),
            "proof_state": "failed",
            "delivery_state": "unverified",
        }
    )
    assert settlement is not None
    assert settlement.verdict is SettlementVerdict.FAILED


def test_operator_waive_finalizes_without_seal(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("# done\n", encoding="utf-8")
    settlement = settle_payload(
        {
            "state": "completed",
            "exit_code": 0,
            "report": str(report),
            "agent": "codex",
            "skill": "workflow",
            "operator_waive": True,
        }
    )
    assert settlement is not None
    assert settlement.verdict is SettlementVerdict.FINALIZED
    assert settlement.waived is True
    assert settlement.source == "operator_waive"


def test_proof_failed_is_failed_or_invalid() -> None:
    failed = settle_payload(
        {
            "state": "completed",
            "exit_code": 0,
            "proof_state": "failed",
            "delivery_state": "unverified",
            "agent": "x",
            "skill": "y",
        }
    )
    assert failed is not None
    assert failed.verdict is SettlementVerdict.FAILED
    assert failed.tui_key == "x"

    invalid = settle_payload(
        {
            "state": "completed",
            "exit_code": 0,
            "proof_state": "invalid",
            "agent": "x",
            "skill": "y",
        }
    )
    assert invalid is not None
    assert invalid.verdict is SettlementVerdict.INVALID
    assert invalid.tui_key == "x"


def test_live_run_has_no_settlement() -> None:
    assert settle_payload({"state": "running", "agent": "codex"}) is None


def test_unsettled_terminal_counts_as_n_on_board() -> None:
    counts = board_fxn_counts(
        [
            {"settlement_verdict": "finalized"},
            {"settlement_verdict": "failed"},
            {"settlement_verdict": "invalid"},
            {"settlement_verdict": "needs_attention"},
            {"state": "completed"},  # unsettled terminal → n, never silence
            {"state": "running"},  # live ignored
        ]
    )
    assert counts == {"f": 1, "x": 2, "n": 2}


def test_can_archive_requires_settlement() -> None:
    assert can_archive({}) is False
    assert can_archive({"state": "completed"}) is False
    assert can_archive({"settlement_verdict": "needs_attention"}) is True
    assert can_archive({"settlement_verdict": "finalized"}) is True


def test_orphan_markdown_scan(tmp_path: Path) -> None:
    (tmp_path / "Untitled.md").write_text("", encoding="utf-8")
    (tmp_path / "Untitled 1.md").write_text("x", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "Untitled-final.md").write_text("y", encoding="utf-8")
    (tmp_path / "real-report.md").write_text("ok", encoding="utf-8")
    orphans = orphan_markdown_paths(tmp_path)
    names = {p.name for p in orphans}
    assert "Untitled.md" in names
    assert "Untitled 1.md" in names
    assert "Untitled-final.md" in names
    assert "real-report.md" not in names


def test_require_bound_markdown_refuses_untitled(tmp_path: Path) -> None:
    bare = tmp_path / "Untitled.md"
    assert is_untitled_markdown(bare) is True
    with pytest.raises(BareMarkdownError, match="bare markdown"):
        require_bound_markdown(bare, run_id="work-1")
    with pytest.raises(BareMarkdownError, match="run_id"):
        require_bound_markdown(tmp_path / "ok-report.md", run_id="")
    bound = require_bound_markdown(tmp_path / "ok-report.md", run_id="work-1")
    assert bound.name == "ok-report.md"


def test_orphan_settlement_payloads_are_needs_attention(tmp_path: Path) -> None:
    (tmp_path / "Untitled.md").write_text("", encoding="utf-8")
    payloads = orphan_settlement_payloads(tmp_path, now="2026-07-21T00:00:00+00:00")
    assert len(payloads) == 1
    assert payloads[0]["settlement_verdict"] == "needs_attention"
    assert payloads[0]["settlement_tui"] == "n"
    assert payloads[0]["settlement_reason"] == "orphan_untitled_markdown"
    assert payloads[0]["run_id"].startswith("orphan-md-")


def test_claim_digest_stable() -> None:
    a = claim_digest_from_payload(
        {"prompt": "same", "skill": "workflow", "agent": "codex"}
    )
    b = claim_digest_from_payload(
        {"prompt": "same", "skill": "workflow", "agent": "codex"}
    )
    assert a and a == b
    c = claim_digest_from_payload(
        {"prompt": "other", "skill": "workflow", "agent": "codex"}
    )
    assert a != c


def test_sync_state_writes_settlement_on_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "999999999")
    report = home / "artifacts" / "r.md"
    report.parent.mkdir(parents=True)
    report.write_text("# report\n", encoding="utf-8")
    _write_meta(
        home,
        {
            "run_id": "work-settle-1",
            "status": "report_validated",
            "agent": "grok",
            "mode": "workflow",
            "root": str(tmp_path),
            "updated_at": "2026-07-21T00:00:00+00:00",
            "skill_code": "wflw",
            "exit_code": 0,
            "report": str(report),
            "prompt": "implement settlement",
            "liveness": "terminal",
        },
    )
    runtime_meta = (
        home / "control_plane" / "runtime_runs" / "work-settle-1" / "meta.json"
    )
    runtime_meta.parent.mkdir(parents=True)
    runtime_meta.write_text(
        json.dumps(
            {
                "run_id": "work-settle-1",
                "await_outcome": "completed",
                "await_rc": 0,
            }
        ),
        encoding="utf-8",
    )

    emitted_after_snapshot: list[int] = []
    original_emit = control_plane.emit_settlement_event

    def _assert_snapshot_is_durable(event):
        path = home / "control_plane" / "runs" / f"{event.run_id}.json"
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["settlement_revision"] == event.revision
        emitted_after_snapshot.append(event.revision)
        return original_emit(event)

    monkeypatch.setattr(
        control_plane, "emit_settlement_event", _assert_snapshot_is_durable
    )
    snapshot = control_plane.sync_state()
    run = next(r for r in snapshot["recent_runs"] if r["run_id"] == "work-settle-1")

    assert run["settlement_verdict"] == "needs_attention"
    # sync_state projects kernel axes onto the board even when meta omitted them;
    # polarized dialect keeps the axes reason (no rewrite to report_without_seal).
    assert run["settlement_reason"].startswith("axes_"), run["settlement_reason"]
    assert (
        "unverified" in run["settlement_reason"]
        or "undeclared" in run["settlement_reason"]
    )
    assert run["settlement_tui"] == "n"
    assert "settlement_counts" in snapshot
    assert snapshot["settlement_counts"]["n"] >= 1
    assert snapshot["settlement_counts"]["f"] == 0
    assert emitted_after_snapshot == [1]
    assert run["settlement_revision"] == 1
    persisted_meta = json.loads(runtime_meta.read_text(encoding="utf-8"))
    assert persisted_meta["settlement_revision"] == 1
    assert persisted_meta["settlement_verdict"] == "needs_attention"
    assert persisted_meta["settlement_tui"] == "n"
    assert persisted_meta["settlement"]["revision"] == 1

    events = _settlement_events(home)
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload == {
        "schema": SETTLEMENT_EVENT_SCHEMA,
        "run_id": "work-settle-1",
        "previous": None,
        "current": {"verdict": "needs_attention", "tui": "n"},
        "reason": run["settlement_reason"],
        "source": run["settlement_source"],
        "settled_at": run["settlement_at"],
        "claim_digest": run["settlement_claim_digest"],
        "waived": False,
        "revision": 1,
    }

    # A replayed sync adopts the existing revision and emits no duplicate.
    control_plane.sync_state()
    assert emitted_after_snapshot == [1]
    assert len(_settlement_events(home)) == 1


def test_stale_scoped_writer_cannot_overwrite_newer_settlement_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """rev1/n pauses, rev2/x lands, then the stale scoped sync loses its CAS."""

    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "race-settlement-revision"
    runtime_meta = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    runtime_meta.parent.mkdir(parents=True)
    runtime_meta.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "await_outcome": "completed",
                "await_rc": 0,
            }
        ),
        encoding="utf-8",
    )
    artifact_meta = _write_meta(
        home,
        {
            "run_id": run_id,
            "status": "completed",
            "agent": "codex",
            "mode": "workflow",
            "root": str(tmp_path),
            "updated_at": "2026-07-26T10:00:00+00:00",
            "skill_code": "wflw",
            "exit_code": 0,
            "liveness": "terminal",
        },
    )
    seeded = control_plane.sync_state(only_run_id=run_id)
    rev1 = next(run for run in seeded["recent_runs"] if run["run_id"] == run_id)
    assert rev1["settlement_verdict"] == "needs_attention"
    assert rev1["settlement_tui"] == "n"
    assert rev1["settlement_revision"] == 1

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    receiver, sender = context.Pipe(duplex=False)
    stale_writer = context.Process(
        target=_sync_stale_projection_after_release,
        args=(
            str(home),
            run_id,
            ready,
            release,
            sender,
        ),
    )
    try:
        stale_writer.start()
        sender.close()
        assert ready.wait(5), "rev1/n writer did not reach the commit barrier"

        artifact_meta.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": "failed",
                    "agent": "codex",
                    "mode": "workflow",
                    "root": str(tmp_path),
                    "updated_at": "2026-07-26T10:00:02+00:00",
                    "skill_code": "wflw",
                    "exit_code": 9,
                    "liveness": "terminal",
                }
            ),
            encoding="utf-8",
        )
        board = control_plane.sync_state(only_run_id=run_id)
        committed = next(
            run for run in board["recent_runs"] if run.get("run_id") == run_id
        )
        assert committed["settlement_verdict"] == "failed"
        assert committed["settlement_tui"] == "x"
        assert committed["settlement_revision"] == 2

        stale_settlement = Settlement(
            verdict=SettlementVerdict.NEEDS_ATTENTION,
            reason=rev1["settlement_reason"],
            settled_at=rev1["settlement_at"],
            source=rev1["settlement_source"],
            claim_digest=rev1["settlement_claim_digest"],
        )
        assert not persist_settlement_to_meta(
            runtime_meta,
            stale_settlement,
            control_plane_root=home / "control_plane",
            run_id=run_id,
            revision=1,
        )
        assert not persist_settlement_to_meta(
            runtime_meta,
            stale_settlement,
            control_plane_root=home / "control_plane",
            run_id=run_id,
            revision=2,
        )

        release.set()
        assert receiver.poll(10), "stale rev1/n writer did not finish"
        stale_result = receiver.recv()
        stale_writer.join(timeout=10)
        assert stale_writer.exitcode == 0
    finally:
        release.set()
        if stale_writer.is_alive():
            stale_writer.terminate()
            stale_writer.join(timeout=5)
        receiver.close()

    snapshot_path = home / "control_plane" / "runs" / f"{run_id}.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    meta = json.loads(runtime_meta.read_text(encoding="utf-8"))
    for persisted in (stale_result, snapshot, meta):
        assert persisted["settlement_verdict"] == "failed"
        assert persisted["settlement_tui"] == "x"
        assert persisted["settlement_revision"] == 2
        assert persisted["settlement"]["revision"] == 2

    events = _settlement_events(home)
    assert [event["payload"]["revision"] for event in events] == [1, 2]


def test_sync_state_gc_parks_with_settlement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "60")
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "3600")
    now = dt.datetime(2026, 5, 19, 6, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(control_plane, "_now", lambda: now)
    _write_meta(
        home,
        {
            "run_id": "just-old-stalled-settle",
            "status": "stalled",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "heartbeat_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "just",
            "launcher_pid": 999999999,
            "liveness": "pid_alive",
        },
    )

    snapshot = control_plane.sync_state()
    run = next(
        r for r in snapshot["recent_runs"] if r["run_id"] == "just-old-stalled-settle"
    )

    assert run["state"] == "gc"
    assert run["settlement_verdict"] == "needs_attention"
    assert run["settlement_tui"] == "n"
    assert "settlement parks as needs_attention" in run["last_error"]


def test_archive_settles_then_archives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUN_SNAPSHOT_RETENTION_SECONDS", "3600")
    monkeypatch.setenv("VIBECRAFTED_RUN_SNAPSHOT_RETENTION_COUNT", "100")
    now = dt.datetime(2026, 5, 19, 6, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(control_plane, "_now", lambda: now)
    runs_dir = home / "control_plane" / "runs"
    runs_dir.mkdir(parents=True)
    terminal_path = runs_dir / "old-terminal.json"
    terminal_path.write_text(
        json.dumps(
            {
                "run_id": "old-terminal",
                "state": "completed",
                "health": "final",
                "exit_code": 0,
                "updated_at": "2026-05-19T00:00:00+00:00",
                "completed_at": "2026-05-19T00:00:00+00:00",
                "agent": "codex",
                "skill": "workflow",
            }
        ),
        encoding="utf-8",
    )

    control_plane.sync_state()

    archived = runs_dir / "archive" / "old-terminal.json"
    assert archived.exists()
    body = json.loads(archived.read_text(encoding="utf-8"))
    assert body["settlement_verdict"] in {
        "finalized",
        "failed",
        "needs_attention",
        "invalid",
    }
    assert not terminal_path.exists()


def test_await_persists_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "work-await-settle"
    run_dir = home / "control_plane" / "runtime_runs" / run_id
    run_dir.mkdir(parents=True)
    meta = run_dir / "meta.json"
    meta.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "completed",
                "exit_code": 0,
                "agent": "grok",
                "skill_code": "wflw",
                "liveness": "terminal",
            }
        ),
        encoding="utf-8",
    )
    _write_meta(
        home,
        {
            "run_id": run_id,
            "status": "completed",
            "agent": "grok",
            "mode": "workflow",
            "root": str(tmp_path),
            "updated_at": "2026-07-21T00:00:00+00:00",
            "skill_code": "wflw",
            "exit_code": 0,
            "liveness": "terminal",
            "meta": str(meta),
        },
    )

    payload = control_plane.await_run(run_id, timeout_seconds=0, interval_seconds=0.05)

    assert payload["completed"] is True
    assert payload["await_outcome"] == "completed"
    assert payload["await_rc"] == 0
    meta_body = json.loads(meta.read_text(encoding="utf-8"))
    assert meta_body["await_outcome"] == "completed"
    assert meta_body["await_rc"] == 0
    assert "await_settled_at" in meta_body
    events = _settlement_events(home)
    assert events
    assert events[-1]["payload"]["source"] == "await"
    assert events[-1]["payload"]["revision"] >= 1
    revisions = [event["payload"]["revision"] for event in events]
    assert revisions == sorted(set(revisions))

    # Re-awaiting unchanged evidence must not create another logical revision.
    before = len(events)
    control_plane.await_run(run_id, timeout_seconds=0, interval_seconds=0.05)
    assert len(_settlement_events(home)) == before


def test_persist_await_verdict_standalone(tmp_path: Path) -> None:
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({"run_id": "x"}), encoding="utf-8")
    fields = persist_await_verdict(
        meta,
        control_plane_root=tmp_path,
        run_id="x",
        rc=1,
        outcome="timed_out",
        worker_alive=False,
        reason="idle_stall",
    )
    body = json.loads(meta.read_text(encoding="utf-8"))
    assert body["await_rc"] == 1
    assert body["await_outcome"] == "timed_out"
    assert fields["await_reason"] == "idle_stall"


def test_sync_state_surfaces_orphan_untitled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "999999999")
    art = home / "artifacts" / "Vetcoders" / "vibecrafted" / "2026_0721" / "reports"
    art.mkdir(parents=True)
    (art / "Untitled.md").write_text("", encoding="utf-8")

    snapshot = control_plane.sync_state()

    assert snapshot["settlement_counts"]["orphans"] >= 1
    assert snapshot["settlement_counts"]["n"] >= 1
    assert any(
        path.endswith("Untitled.md") for path in snapshot.get("orphan_artifacts") or []
    )
