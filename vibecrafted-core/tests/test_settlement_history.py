from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import control_plane, events, settlement_ledger
from vibecrafted_core.settlement import (
    SettlementEventStateV1,
    SettlementEventV1,
    SettlementEventV2,
    TrustReceiptV1,
    emit_settlement_event,
)
from vibecrafted_core.settlement_history import (
    MAX_U64,
    SETTLEMENT_COUNT_SEMANTICS_EXACT,
    SETTLEMENT_COUNT_SEMANTICS_LOWER_BOUND,
    SETTLEMENT_COUNTS_PIPE,
    SETTLEMENT_HISTORY_AUTHORITY,
    SETTLEMENT_HISTORY_GENERATION_SCHEMA,
    SETTLEMENT_HISTORY_SCHEMA,
    SETTLEMENT_HISTORY_WIRE_SCHEMA,
    SETTLEMENT_REPLAY_INTERVAL_SECONDS,
    SettlementCounts,
    SettlementHistoryError,
    SettlementHistoryPublisher,
    SettlementHistorySnapshot,
    reconcile_settlement_history,
)

GENERATION = "019f9c72-6ed3-7a41-8cf0-df0b7bff80fe"
NEXT_GENERATION = "019f9c72-6ed3-7a41-8cf0-df0b7bff80ff"
PRELEDGER_GAP: dict[str, object] = {
    "kind": "preledger_history_unknown",
    "backfill_status": "not_performed",
    "facts_invented": False,
}
LEDGER_NOT_STARTED_GAP: dict[str, object] = {
    "kind": "ledger_not_started",
    "backfill_status": "not_performed",
    "facts_invented": False,
}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "crafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    return home


def write_generation(
    root: Path,
    generation: str = GENERATION,
    *,
    continuity_gaps: int = 0,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "settlement_history_generation.json").write_text(
        json.dumps(
            {
                "schema": SETTLEMENT_HISTORY_GENERATION_SCHEMA,
                "authority": SETTLEMENT_HISTORY_AUTHORITY,
                "generation": generation,
                "continuity_gaps": continuity_gaps,
            }
        ),
        encoding="utf-8",
    )


def settled(run_id: str, revision: int, tui: str) -> dict[str, Any]:
    verdict = {
        "f": "finalized",
        "x": "failed",
        "n": "needs_attention",
    }[tui]
    return {
        "run_id": run_id,
        "state": "completed",
        "settlement_verdict": verdict,
        "settlement_tui": tui,
        "settlement_revision": revision,
        "settlement_reason": "test",
        "settlement_at": "2026-07-26T06:00:00+00:00",
        "settlement_source": "auto",
        "settlement_claim_digest": "",
        "settlement_waived": False,
        "settlement": {
            "verdict": verdict,
            "tui": tui,
            "reason": "test",
            "settled_at": "2026-07-26T06:00:00+00:00",
            "source": "auto",
            "claim_digest": "",
            "waived": False,
        },
    }


def trust_event(
    *,
    run_id: str,
    revision: int,
    verdict: str,
    tui: str,
    previous_verdict: str | None = None,
    previous_tui: str | None = None,
    reason_suffix: str = "",
) -> SettlementEventV2:
    trust_verdict = {
        "finalized": "pass",
        "failed": "block",
        "needs_attention": "pass-with-gaps",
    }[verdict]
    claim_digest = hashlib.sha256(f"{run_id}:{revision}:{verdict}".encode()).hexdigest()
    receipt = TrustReceiptV1.issue(
        repo_root="/tmp/vibecrafted-history-test",
        run_id=run_id,
        commit_sha="a" * 40,
        trust_verdict=trust_verdict,
        settlement_verdict=verdict,
        settlement_tui=tui,
        settlement_revision=revision,
        claim_digest=claim_digest,
    )
    previous = (
        SettlementEventStateV1(
            verdict=previous_verdict,
            tui=str(previous_tui),
        )
        if previous_verdict is not None
        else None
    )
    return SettlementEventV2(
        run_id=run_id,
        previous=previous,
        current=SettlementEventStateV1(verdict=verdict, tui=tui),
        reason=f"trust_{trust_verdict.replace('-', '_')}:{revision}{reason_suffix}",
        source="trust",
        settled_at=f"2026-07-26T12:00:{revision:02d}+00:00",
        claim_digest=claim_digest,
        waived=False,
        revision=revision,
        trust_receipt=receipt,
    )


def projected_snapshot(
    *,
    generation: str = GENERATION,
    historical: SettlementCounts | None = None,
    latest: SettlementCounts | None = None,
    gap: dict[str, object] = PRELEDGER_GAP,
) -> SettlementHistorySnapshot:
    historical = historical or SettlementCounts(f=1)
    latest = latest or SettlementCounts(f=1)
    return SettlementHistorySnapshot(
        generation=generation,
        sequence=historical.total,
        historical_transitions=historical,
        latest_by_run=latest,
        gaps=1,
        complete_from=None,
        count_semantics=SETTLEMENT_COUNT_SEMANTICS_LOWER_BOUND,
        history_complete=False,
        history_gaps=(dict(gap),),
    )


def test_v1_event_is_not_transition_but_snapshot_enters_observed_lower_bound(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events, "append_event", lambda *_args, **_kwargs: {})
    emit_settlement_event(
        SettlementEventV1(
            run_id="run-v1",
            previous=None,
            current=SettlementEventStateV1(
                verdict="needs_attention",
                tui="n",
            ),
            reason="automatic settlement",
            source="auto",
            settled_at="2026-07-26T12:00:00+00:00",
            claim_digest="",
            waived=False,
            revision=1,
        )
    )

    path = control_plane.run_snapshot_dir() / "run-auto.json"
    payload = settled("run-auto", 1, "x")
    payload["settlement_history"] = {"old_snapshot_authority": True}
    persisted = control_plane._write_run_snapshot(path, None, payload)

    assert "settlement_history" not in persisted
    assert "settlement_history" not in json.loads(path.read_text(encoding="utf-8"))
    projection = reconcile_settlement_history(
        control_plane_root=isolated_home / "control_plane"
    )
    assert projection.sequence == 1
    assert projection.historical_transitions == SettlementCounts(x=1)
    assert projection.latest_by_run == SettlementCounts(x=1)
    assert projection.count_semantics == SETTLEMENT_COUNT_SEMANTICS_LOWER_BOUND
    assert projection.history_gaps == (
        {
            "kind": "preledger_history_unknown",
            "backfill_status": "observed_snapshot_lower_bound",
            "facts_invented": False,
        },
    )
    ledger = settlement_ledger.read_settlement_ledger(
        isolated_home / "control_plane" / "settlement_ledger.jsonl"
    )
    assert ledger["metadata"]["backfill"]["observation_count"] == 1
    assert ledger["metadata"]["backfill"]["facts_invented"] is False


def test_v2_is_counted_exactly_once_with_explicit_preledger_lower_bound(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events, "append_event", lambda *_args, **_kwargs: {})
    event = trust_event(
        run_id="run-v2",
        revision=1,
        verdict="needs_attention",
        tui="n",
    )
    emit_settlement_event(event)
    emit_settlement_event(event)

    projection = reconcile_settlement_history(
        control_plane_root=isolated_home / "control_plane"
    )

    assert projection.sequence == 1
    assert projection.historical_transitions == SettlementCounts(n=1)
    assert projection.latest_by_run == SettlementCounts(n=1)
    assert projection.gaps == 1
    assert projection.complete_from is None
    assert projection.count_semantics == SETTLEMENT_COUNT_SEMANTICS_LOWER_BOUND
    assert projection.history_complete is False
    assert projection.history_gaps == (PRELEDGER_GAP,)
    assert projection.to_payload()["authority"] == SETTLEMENT_HISTORY_AUTHORITY


def test_revision_gap_is_added_to_explicit_lower_bound(
    isolated_home: Path,
) -> None:
    settlement_ledger._append_settlement_fact(
        trust_event(
            run_id="run-gap",
            revision=3,
            verdict="failed",
            tui="x",
        )
    )

    projection = reconcile_settlement_history(
        control_plane_root=isolated_home / "control_plane"
    )

    assert projection.historical_transitions == SettlementCounts(x=1)
    assert projection.gaps == 3
    assert projection.history_gaps == (
        PRELEDGER_GAP,
        {
            "kind": "missing_settlement_revisions",
            "run_id": "run-gap",
            "from_revision": 1,
            "to_revision": 2,
            "count": 2,
        },
    )


def test_content_collision_fails_closed_and_keeps_first_projection(
    isolated_home: Path,
) -> None:
    original = trust_event(
        run_id="run-collision",
        revision=1,
        verdict="needs_attention",
        tui="n",
    )
    settlement_ledger._append_settlement_fact(original)
    first = reconcile_settlement_history(
        control_plane_root=isolated_home / "control_plane"
    )
    history_path = isolated_home / "control_plane" / "settlement_history.json"
    before = history_path.read_bytes()

    collision = trust_event(
        run_id="run-collision",
        revision=1,
        verdict="needs_attention",
        tui="n",
        reason_suffix=":different-content",
    )
    assert collision.event_key == original.event_key
    with pytest.raises(
        settlement_ledger.SettlementLedgerCollision,
        match="event key collision",
    ):
        settlement_ledger._append_settlement_fact(collision)

    assert (
        reconcile_settlement_history(control_plane_root=isolated_home / "control_plane")
        == first
    )
    assert history_path.read_bytes() == before


def test_snapshot_deletion_never_decreases_or_breaks_ledger_projection(
    isolated_home: Path,
) -> None:
    for event in (
        trust_event(
            run_id="run-one",
            revision=1,
            verdict="finalized",
            tui="f",
        ),
        trust_event(
            run_id="run-two",
            revision=1,
            verdict="failed",
            tui="x",
        ),
    ):
        settlement_ledger._append_settlement_fact(event)

    root = isolated_home / "control_plane"
    active = root / "runs" / "run-one.json"
    archived = root / "runs" / "archive" / "run-two.json"
    active.parent.mkdir(parents=True, exist_ok=True)
    archived.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(json.dumps(settled("run-one", 1, "f")), encoding="utf-8")
    archived.write_text(json.dumps(settled("run-two", 1, "x")), encoding="utf-8")
    first = reconcile_settlement_history(control_plane_root=root)

    active.unlink()
    archived.unlink()
    replay = reconcile_settlement_history(control_plane_root=root)

    assert replay == first
    assert replay.sequence == 2
    assert replay.historical_transitions == SettlementCounts(f=1, x=1)
    assert replay.latest_by_run == SettlementCounts(f=1, x=1)


def test_corrupt_ledger_fails_closed_without_overwriting_projection(
    isolated_home: Path,
) -> None:
    settlement_ledger._append_settlement_fact(
        trust_event(
            run_id="run-corrupt",
            revision=1,
            verdict="failed",
            tui="x",
        )
    )
    root = isolated_home / "control_plane"
    reconcile_settlement_history(control_plane_root=root)
    history_path = root / "settlement_history.json"
    before = history_path.read_bytes()

    ledger_path = settlement_ledger.settlement_ledger_path()
    lines = ledger_path.read_bytes().splitlines(keepends=True)
    corrupt = json.loads(lines[-1])
    corrupt["settlement_tui"] = "f"
    lines[-1] = (
        json.dumps(corrupt, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    ledger_path.write_bytes(b"".join(lines))

    with pytest.raises(settlement_ledger.SettlementLedgerCorrupt):
        reconcile_settlement_history(control_plane_root=root)
    assert history_path.read_bytes() == before


def test_replaced_snapshot_projection_is_overwritten_even_if_sequence_decreases(
    isolated_home: Path,
) -> None:
    settlement_ledger._append_settlement_fact(
        trust_event(
            run_id="run-ledger",
            revision=1,
            verdict="finalized",
            tui="f",
        )
    )
    root = isolated_home / "control_plane"
    write_generation(root)
    old_projection = {
        "schema": "vibecrafted.settlement-history.v1",
        "generation": GENERATION,
        "sequence": 99,
        "historical_transitions": {"f": 99, "x": 0, "n": 0, "total": 99},
        "latest_by_run": {"f": 1, "x": 0, "n": 0, "total": 1},
        "gaps": 0,
        "complete_from": 1,
    }
    (root / "settlement_history.json").write_text(
        json.dumps(old_projection),
        encoding="utf-8",
    )

    projection = reconcile_settlement_history(control_plane_root=root)

    assert projection.generation != GENERATION
    assert projection.sequence == 1
    assert projection.historical_transitions == SettlementCounts(f=1)
    assert projection.count_semantics == SETTLEMENT_COUNT_SEMANTICS_LOWER_BOUND


def test_generation_reset_changes_delivery_fence_not_ledger_counts(
    isolated_home: Path,
) -> None:
    settlement_ledger._append_settlement_fact(
        trust_event(
            run_id="run-generation",
            revision=1,
            verdict="finalized",
            tui="f",
        )
    )
    root = isolated_home / "control_plane"
    first = reconcile_settlement_history(control_plane_root=root)
    replay = reconcile_settlement_history(control_plane_root=root)
    assert replay == first

    (root / "settlement_history_generation.json").unlink()
    reset = reconcile_settlement_history(control_plane_root=root)
    reset_replay = reconcile_settlement_history(control_plane_root=root)

    assert reset.generation != first.generation
    assert reset_replay == reset
    assert reset.sequence == first.sequence
    assert reset.historical_transitions == first.historical_transitions
    assert reset.latest_by_run == first.latest_by_run
    assert reset.history_gaps == first.history_gaps
    assert reset.gaps == first.gaps


def test_legacy_generation_rotates_even_without_legacy_projection(
    isolated_home: Path,
) -> None:
    root = isolated_home / "control_plane"
    root.mkdir(parents=True)
    (root / "settlement_history_generation.json").write_text(
        json.dumps(
            {
                "schema": "vibecrafted.settlement-history-generation.v1",
                "generation": GENERATION,
                "continuity_gaps": 0,
            }
        ),
        encoding="utf-8",
    )

    projection = reconcile_settlement_history(control_plane_root=root)
    persisted = json.loads(
        (root / "settlement_history_generation.json").read_text(encoding="utf-8")
    )

    assert projection.generation != GENERATION
    assert persisted["schema"] == SETTLEMENT_HISTORY_GENERATION_SCHEMA
    assert persisted["authority"] == SETTLEMENT_HISTORY_AUTHORITY
    assert persisted["generation"] == projection.generation
    assert persisted["continuity_gaps"] == 1


def test_public_schema_rejects_latest_bucket_without_historical_transition() -> None:
    payload = projected_snapshot().to_payload()
    payload["historical_transitions"] = {"f": 0, "x": 0, "n": 1, "total": 1}
    with pytest.raises(SettlementHistoryError, match="bucket exceeds"):
        SettlementHistorySnapshot.from_payload(payload)


def test_public_schema_is_exactly_u64_width() -> None:
    maximum = SettlementHistorySnapshot.from_payload(
        {
            "schema": SETTLEMENT_HISTORY_SCHEMA,
            "authority": SETTLEMENT_HISTORY_AUTHORITY,
            "generation": GENERATION,
            "sequence": MAX_U64,
            "historical_transitions": {
                "f": MAX_U64,
                "x": 0,
                "n": 0,
                "total": MAX_U64,
            },
            "latest_by_run": {
                "f": MAX_U64,
                "x": 0,
                "n": 0,
                "total": MAX_U64,
            },
            "gaps": 0,
            "complete_from": 1,
            "count_semantics": SETTLEMENT_COUNT_SEMANTICS_EXACT,
            "history_complete": True,
            "history_gaps": [],
        }
    )
    assert maximum.sequence == MAX_U64

    too_wide = maximum.to_payload()
    too_wide["gaps"] = MAX_U64 + 1
    with pytest.raises(SettlementHistoryError, match="gaps are invalid"):
        SettlementHistorySnapshot.from_payload(too_wide)

    with pytest.raises(SettlementHistoryError, match="total exceeds u64"):
        SettlementCounts(f=MAX_U64, x=1)


def test_wire_projection_stays_compatible_with_strict_vc_frame_v1() -> None:
    snapshot = projected_snapshot()
    payload = snapshot.to_wire_payload()

    assert payload == {
        "schema": SETTLEMENT_HISTORY_WIRE_SCHEMA,
        "generation": GENERATION,
        "sequence": 1,
        "historical_transitions": {"f": 1, "x": 0, "n": 0, "total": 1},
        "latest_by_run": {"f": 1, "x": 0, "n": 0, "total": 1},
        "gaps": 1,
        "complete_from": None,
    }
    assert "authority" not in payload
    assert "history_gaps" not in payload


def test_custom_projection_root_reads_its_own_ledger(
    isolated_home: Path,
) -> None:
    custom_root = isolated_home / "other-control-plane"
    custom_root.mkdir(parents=True)
    (custom_root / "settlement_ledger.jsonl").write_text(
        "{not-json}\n",
        encoding="utf-8",
    )

    with pytest.raises(settlement_ledger.SettlementLedgerCorrupt):
        reconcile_settlement_history(control_plane_root=custom_root)
    assert not (custom_root / "settlement_history.json").exists()


def test_publisher_broadcasts_only_to_running_sessions_without_plugin_launch(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    calls: list[list[str]] = []

    def runner(
        argv: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        assert timeout == 2.0
        calls.append(argv)
        if "list-sessions" in argv:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    "live-one [Created 1s ago] \n"
                    "dead-one [Created 2s ago] (EXITED - attach to resurrect)\n"
                    "live-two [Created 3s ago] \n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    publisher = SettlementHistoryPublisher(
        control_plane_root=tmp_path / "control_plane",
        runner=runner,
        env={"VIBECRAFTED_VC_FRAME_BIN": str(binary)},
        timeout=2.0,
    )
    write_generation(publisher.root)
    snapshot = projected_snapshot()
    publisher.stage(snapshot)

    report = publisher.flush()

    assert report.delivered_sessions == ("live-one", "live-two")
    assert not publisher.outbox_path.exists()
    pipe_calls = calls[1:]
    assert [call[2] for call in pipe_calls] == ["live-one", "live-two"]
    assert all(
        call[3:8]
        == [
            "pipe",
            "--name",
            SETTLEMENT_COUNTS_PIPE,
            "--",
            snapshot.to_wire_json(),
        ]
        for call in pipe_calls
    )
    assert all("--plugin" not in call for call in pipe_calls)


def test_publisher_excludes_output_only_triage_bucket_sessions(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    calls: list[list[str]] = []

    def runner(
        argv: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "list-sessions" in argv:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    "Failed runs [Created now]\n"
                    "Finalized runs [Created now]\n"
                    "Needs attention [Created now]\n"
                    "zippy-cymbal [Created now]\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    publisher = SettlementHistoryPublisher(
        control_plane_root=tmp_path / "control_plane",
        runner=runner,
        env={"VIBECRAFTED_VC_FRAME_BIN": str(binary)},
    )
    write_generation(publisher.root)
    publisher.stage(projected_snapshot())

    report = publisher.flush()

    pipe_calls = calls[1:]
    assert report.attempted_sessions == ("zippy-cymbal",)
    assert report.delivered_sessions == ("zippy-cymbal",)
    assert len(pipe_calls) == 1
    assert pipe_calls[0][2] == "zippy-cymbal"


def test_publisher_failure_retains_only_newest_ledger_projection(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()

    def runner(
        argv: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        if "list-sessions" in argv:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout="live [Created 1s ago] \n",
                stderr="",
            )
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="busy")

    publisher = SettlementHistoryPublisher(
        control_plane_root=tmp_path / "control_plane",
        runner=runner,
        env={"VIBECRAFTED_VC_FRAME_BIN": str(binary)},
    )
    write_generation(publisher.root)
    first = projected_snapshot()
    second = projected_snapshot(
        historical=SettlementCounts(f=1, x=1),
        latest=SettlementCounts(f=1, x=1),
    )

    publisher.stage(first)
    assert publisher.flush().pending is True
    publisher.stage(second)

    pending = json.loads(publisher.outbox_path.read_text(encoding="utf-8"))
    assert pending["sequence"] == 2
    assert pending["payload"] == second.to_payload()


def test_publisher_backs_off_an_incompatible_live_session_without_losing_outbox(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    now = [100.0]
    pipe_attempts: list[list[str]] = []
    pipe_succeeds = [False]

    def runner(
        argv: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        if "list-sessions" in argv:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout="legacy-live [Created 1s ago] \n",
                stderr="",
            )
        pipe_attempts.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0 if pipe_succeeds[0] else 2,
            stdout="",
            stderr="" if pipe_succeeds[0] else "channel closed",
        )

    publisher = SettlementHistoryPublisher(
        control_plane_root=tmp_path / "control_plane",
        runner=runner,
        env={"VIBECRAFTED_VC_FRAME_BIN": str(binary)},
        retry_backoff=300.0,
        clock=lambda: now[0],
    )
    write_generation(publisher.root)
    publisher.stage(projected_snapshot())

    first = publisher.flush()
    immediate_replay = publisher.flush()

    assert first.failed_sessions == ("legacy-live",)
    assert immediate_replay.failed_sessions == ()
    assert immediate_replay.deferred_sessions == ("legacy-live",)
    assert immediate_replay.pending is True
    assert len(pipe_attempts) == 1
    assert publisher.outbox_path.exists()

    now[0] += 301.0
    pipe_succeeds[0] = True
    recovered = publisher.flush()

    assert recovered.delivered_sessions == ("legacy-live",)
    assert recovered.deferred_sessions == ()
    assert recovered.pending is False
    assert len(pipe_attempts) == 2
    assert not publisher.outbox_path.exists()


def test_stage_replaces_snapshot_derived_legacy_outbox(
    tmp_path: Path,
) -> None:
    root = tmp_path / "control_plane"
    write_generation(root)
    publisher = SettlementHistoryPublisher(control_plane_root=root)
    old_payload = {
        "schema": "vibecrafted.settlement-history.v1",
        "generation": GENERATION,
        "sequence": 99,
        "historical_transitions": {"f": 99, "x": 0, "n": 0, "total": 99},
        "latest_by_run": {"f": 1, "x": 0, "n": 0, "total": 1},
        "gaps": 0,
        "complete_from": 1,
    }
    publisher.outbox_path.write_text(
        json.dumps(
            {
                "schema": "vibecrafted.settlement-history-delivery.v1",
                "sequence": 99,
                "payload": old_payload,
            }
        ),
        encoding="utf-8",
    )

    current = projected_snapshot()
    publisher.stage(current)

    pending = json.loads(publisher.outbox_path.read_text(encoding="utf-8"))
    assert pending["sequence"] == 1
    assert pending["payload"] == current.to_payload()


def test_publisher_fences_retired_generation_after_reset(tmp_path: Path) -> None:
    root = tmp_path / "control_plane"
    write_generation(root)
    publisher = SettlementHistoryPublisher(control_plane_root=root)
    first = projected_snapshot()
    reset = projected_snapshot(
        generation=NEXT_GENERATION,
        historical=SettlementCounts(),
        latest=SettlementCounts(),
        gap=LEDGER_NOT_STARTED_GAP,
    )
    publisher.stage(first)

    write_generation(root, NEXT_GENERATION, continuity_gaps=1)
    assert publisher.flush().reason == "stale settlement history generation"
    publisher.stage(reset)
    with pytest.raises(SettlementHistoryError, match="stale"):
        publisher.stage(first)

    pending = json.loads(publisher.outbox_path.read_text(encoding="utf-8"))
    assert pending["payload"] == reset.to_payload()


def test_background_refresh_is_nonblocking_and_coalesces_latest_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = SettlementHistoryPublisher(
        control_plane_root=tmp_path / "control_plane"
    )
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def refresh() -> object:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            started.set()
            assert release.wait(timeout=2)
        return object()

    monkeypatch.setattr(publisher, "refresh_and_flush", refresh)

    assert publisher.request_refresh() is True
    assert started.wait(timeout=2)
    assert publisher.request_refresh() is False
    assert publisher.request_refresh() is False
    release.set()

    assert publisher.wait_for_idle(timeout=2)
    assert calls == [1, 2]


def test_periodic_refresh_replays_for_new_plugins_within_five_seconds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = SettlementHistoryPublisher(
        control_plane_root=tmp_path / "control_plane"
    )
    refreshed = threading.Event()
    calls: list[int] = []

    def refresh() -> object:
        calls.append(len(calls) + 1)
        if len(calls) >= 2:
            refreshed.set()
        return object()

    monkeypatch.setattr(publisher, "refresh_and_flush", refresh)

    assert 0 < SETTLEMENT_REPLAY_INTERVAL_SECONDS <= 5
    try:
        assert publisher.start_periodic_refresh(interval=0.01) is True
        assert publisher.start_periodic_refresh(interval=0.01) is False
        assert refreshed.wait(timeout=0.5)
    finally:
        assert publisher.stop_periodic_refresh(timeout=1)
        assert publisher.wait_for_idle(timeout=1)

    delivered = len(calls)
    assert delivered >= 2
    threading.Event().wait(0.03)
    assert len(calls) == delivered


def test_publisher_deduplicates_unchanged_payload(tmp_path: Path) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    pipe_calls: list[list[str]] = []

    def runner(
        argv: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        if "list-sessions" in argv:
            return subprocess.CompletedProcess(
                argv, 0, stdout="live-session [Created now]\n", stderr=""
            )
        pipe_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    publisher = SettlementHistoryPublisher(
        control_plane_root=tmp_path / "control_plane",
        runner=runner,
        env={"VIBECRAFTED_VC_FRAME_BIN": str(binary)},
    )
    write_generation(publisher.root)
    snapshot = projected_snapshot()
    publisher.stage(snapshot)

    report1 = publisher.flush()
    assert report1.delivered_sessions == ("live-session",)
    assert len(pipe_calls) == 1

    # Re-stage identical snapshot and flush: pipe should be skipped (deduplicated!)
    publisher.stage(snapshot)
    report2 = publisher.flush()
    assert report2.delivered_sessions == ("live-session",)
    assert len(pipe_calls) == 1


def test_publisher_default_runner_socket_discovery_idle(tmp_path: Path) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    sockets_root = tmp_path / "sockets"
    sock_dir = sockets_root / "contract_version_2"
    sock_dir.mkdir(parents=True)

    publisher = SettlementHistoryPublisher(
        control_plane_root=tmp_path / "control_plane",
        env={
            "VIBECRAFTED_VC_FRAME_BIN": str(binary),
            "VC_FRAME_SOCKET_DIR": str(sockets_root),
        },
    )
    write_generation(publisher.root)
    publisher.stage(projected_snapshot())

    report = publisher.flush()
    assert report.delivered_sessions == ()
    assert report.reason == "no eligible vc-frame plugin sessions"
    assert report.pending is True
