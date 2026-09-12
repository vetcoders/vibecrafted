from __future__ import annotations

import json
import logging
import os
import queue
import stat
import subprocess
import threading
import time
import urllib.error
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import vibecrafted_core.guardian as guardian_module
from vibecrafted_core.guardian import (
    NATIVE_APP_BUNDLE_ID,
    BoundedBackoff,
    CompletionRecord,
    GuardianAlreadyRunning,
    GuardianLockSecurityError,
    GuardianNotification,
    GuardianProtocolError,
    GuardianRecoveryAdapter,
    GuardianState,
    GuardianStateLimitError,
    GuardianWorker,
    PendingRecord,
    ReconcileDecision,
    SettlementRevision,
    SSEControlFrame,
    SSEFrame,
    SSEHeartbeat,
    iter_sse,
    native_app_is_alive,
    notification_for,
    notify_operator,
    parse_settlement_revision,
    single_instance_lock,
)
from vibecrafted_core.settlement import TrustReceiptV1


class FakeResponse:
    def __init__(
        self,
        lines: Iterable[str],
        *,
        content_type: str = "text/event-stream; charset=utf-8",
        status: int = 200,
    ) -> None:
        self._lines = [line.encode("utf-8") for line in lines]
        self.headers = {"Content-Type": content_type}
        self.status = status
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class FakeJSONResponse:
    def __init__(
        self,
        payload: object,
        *,
        content_type: str = "application/json",
        status: int = 200,
    ) -> None:
        self._body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )
        self.headers = {"Content-Type": content_type}
        self.status = status
        self.closed = False

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        self.closed = True


class QueueOpener:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, request: Any, *, timeout: float) -> Any:
        self.calls.append(
            (
                request.full_url,
                {key.lower(): value for key, value in request.header_items()},
                timeout,
            )
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, (FakeResponse, FakeJSONResponse))
        return outcome


def settlement_data(
    run_id: str,
    revision: int,
    tui: str,
    *,
    verdict: str | None = None,
    reason: str = "proof checked",
    source: str = "await",
    repo_root: str | Path = "/tmp/vibecrafted-guardian-tests",
    commit_sha: str = "a" * 40,
) -> str:
    verdict_by_tui = {
        "f": "finalized",
        "n": "needs_attention",
        "x": "failed",
    }
    current_verdict = verdict or verdict_by_tui[tui]
    claim_digest = "c" * 64
    payload: dict[str, object] = {
        "schema": "vibecrafted.settlement-event.v1",
        "run_id": run_id,
        "previous": None,
        "current": {
            "verdict": current_verdict,
            "tui": tui,
        },
        "reason": reason,
        "source": source,
        "settled_at": "2026-07-26T06:00:00+00:00",
        "claim_digest": claim_digest,
        "waived": False,
        "revision": revision,
    }
    if source == "trust":
        receipt = TrustReceiptV1.issue(
            repo_root=str(repo_root),
            run_id=run_id,
            commit_sha=commit_sha,
            trust_verdict="pass-with-gaps",
            settlement_verdict=current_verdict,
            settlement_tui=tui,
            settlement_revision=revision,
            claim_digest=claim_digest,
        )
        payload.update(
            {
                "schema": "vibecrafted.settlement-event.v2",
                "event_key": f"{run_id}:{revision}:{receipt.receipt_id}",
                "trust_receipt": receipt.to_payload(),
            }
        )
    return json.dumps(
        {
            "ts": "2026-07-26T06:00:00+00:00",
            "run_id": run_id,
            "kind": "settlement.changed",
            "message": f"settlement revision {revision}",
            "payload": payload,
        },
        separators=(",", ":"),
    )


TEST_STREAM_EPOCH = "019f-stream-epoch"


def v2_cursor(offset: int, *, generation: int = 0) -> str:
    return f"v2:{TEST_STREAM_EPOCH}:{generation}:{offset}"


def frame(cursor: int | str, data: str) -> list[str]:
    return [f"id: {cursor}\n", f"data: {data}\n", "\n"]


def control_frame(event: str, cursor: int | str, payload: object) -> list[str]:
    return [
        f"event: {event}\n",
        f"id: {cursor}\n",
        f"data: {json.dumps(payload, separators=(',', ':'))}\n",
        "\n",
    ]


def boundary(
    from_cursor: int | str,
    to_cursor: int | str | None = None,
    *,
    reason: str = "connection_start",
) -> list[str]:
    target = from_cursor if to_cursor is None else to_cursor
    return control_frame(
        "stream.boundary",
        target,
        {
            "schema": "vibecrafted.stream-boundary.v1",
            "kind": "stream.boundary",
            "from": str(from_cursor),
            "to": str(target),
            "reason": reason,
        },
    )


def caught_up(
    cursor: int | str,
    high_watermark: int | str | None = None,
) -> list[str]:
    target = cursor if high_watermark is None else high_watermark
    return control_frame(
        "stream.caught-up",
        cursor,
        {
            "schema": "vibecrafted.stream-caught-up.v1",
            "kind": "stream.caught-up",
            "cursor": str(cursor),
            "high_watermark": str(target),
        },
    )


def stream_gap(
    *,
    requested: str,
    resumed_at: int | str,
    reason: str = "generation_expired_or_unknown",
) -> list[str]:
    return control_frame(
        "stream.gap",
        resumed_at,
        {
            "schema": "vibecrafted.stream-gap.v1",
            "kind": "stream.gap",
            "requested": requested,
            "resumed_at": str(resumed_at),
            "reason": reason,
            "action": "resnapshot",
        },
    )


def heartbeat() -> list[str]:
    return [": ping\n", "\n"]


def settlement_event(
    run_id: str,
    revision: int,
    tui: str,
    *,
    source: str = "await",
    repo_root: str | Path = "/tmp/vibecrafted-guardian-tests",
) -> SettlementRevision:
    event = parse_settlement_revision(
        settlement_data(
            run_id,
            revision,
            tui,
            source=source,
            repo_root=repo_root,
        )
    )
    assert event is not None
    return event


def resumable_projection(
    event: SettlementRevision,
    root: Path,
) -> dict[str, object]:
    projection: dict[str, object] = {
        "run_id": event.run_id,
        "state": "recovery_required",
        "agent": "codex",
        "skill": "implement",
        "root": str(root),
        "exit_code": -9,
        "liveness": "terminal",
        "worker_alive": False,
        "recovery_required": True,
        "stop_reason": "signal_exit",
        "attempt": 0,
        "settlement_tui": event.tui,
        "settlement_verdict": event.verdict,
        "settlement_source": event.source,
        "settlement_revision": event.revision,
        "commit_sha": "a" * 40,
        "controls": {
            "native_resume_candidate": {
                "agent": "codex",
                "agent_session_id": "019-native-session",
            }
        },
    }
    if event.receipt_id:
        receipt = TrustReceiptV1.issue(
            repo_root=str(root),
            run_id=event.run_id,
            commit_sha="a" * 40,
            trust_verdict="pass-with-gaps",
            settlement_verdict=event.verdict,
            settlement_tui=event.tui,
            settlement_revision=event.revision,
            claim_digest="c" * 64,
        )
        assert receipt.receipt_id == event.receipt_id
        projection["trust_receipt"] = receipt.to_payload()
    return projection


def action_result(
    *,
    accepted: bool,
    retryable: bool,
    terminal: bool,
    reason: str,
) -> dict[str, object]:
    return {
        "accepted": accepted,
        "retryable": retryable,
        "terminal": terminal,
        "reason": reason,
    }


def write_checked_v2(path: Path, body: dict[str, object]) -> None:
    checksum = guardian_module.hashlib.sha256(
        guardian_module._canonical_json(body)
    ).hexdigest()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    guardian_module._atomic_private_write(
        path, guardian_module._canonical_json({**body, "checksum": checksum}) + b"\n"
    )


def test_iter_sse_only_emits_complete_numeric_frames_and_ping() -> None:
    items = list(
        iter_sse(
            [
                "id: nope\n",
                "data: ignored\n",
                "\n",
                "id: 12\n",
                "data: first\n",
                "data: second\n",
                "\n",
                ": ping\n",
                "\n",
                "id: 13\n",
                "data: incomplete\n",
            ]
        )
    )

    assert items == [
        SSEFrame(cursor=12, data="first\nsecond"),
        SSEHeartbeat(),
    ]


def test_parse_settlement_revision_rejects_schema_or_tui_contradiction() -> None:
    valid = settlement_data("run-a", 3, "n")
    event = parse_settlement_revision(valid)
    assert event == SettlementRevision(
        run_id="run-a",
        revision=3,
        verdict="needs_attention",
        tui="n",
        reason="proof checked",
        source="await",
        settled_at="2026-07-26T06:00:00+00:00",
    )

    contradiction = settlement_data("run-a", 3, "n", verdict="failed")
    assert parse_settlement_revision(contradiction) is None

    wrong_schema = json.loads(valid)
    wrong_schema["payload"]["schema"] = "vibecrafted.settlement-event.v0"
    assert parse_settlement_revision(json.dumps(wrong_schema)) is None


def test_parse_trust_settlement_v2_binds_exact_receipt(tmp_path: Path) -> None:
    encoded = settlement_data(
        "run-trust-v2",
        4,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    event = parse_settlement_revision(encoded)

    assert event is not None
    assert event.source == "trust"
    assert len(event.receipt_id) == 64

    wrong_key = json.loads(encoded)
    wrong_key["payload"]["event_key"] = "run-trust-v2:4:wrong"
    assert parse_settlement_revision(json.dumps(wrong_key)) is None

    mutated_receipt = json.loads(encoded)
    mutated_receipt["payload"]["trust_receipt"]["receipt_id"] = "f" * 64
    assert parse_settlement_revision(json.dumps(mutated_receipt)) is None


def test_caught_up_opens_gate_then_f_n_x_are_exactly_once(
    tmp_path: Path,
) -> None:
    start = v2_cursor(0)
    historical = v2_cursor(100)
    opener = QueueOpener(
        FakeResponse(
            [
                *boundary(start),
                *frame(historical, settlement_data("historical", 1, "x")),
                *caught_up(historical),
            ]
        ),
        FakeResponse(
            [
                *boundary(historical),
                *caught_up(historical),
                *frame(v2_cursor(200), settlement_data("run-f", 1, "f")),
                *frame(
                    v2_cursor(300),
                    settlement_data(
                        "run-n",
                        1,
                        "n",
                        source="trust",
                        repo_root=tmp_path,
                    ),
                ),
                *frame(
                    v2_cursor(301),
                    settlement_data(
                        "run-n",
                        1,
                        "n",
                        source="trust",
                        repo_root=tmp_path,
                    ),
                ),
                *frame(v2_cursor(400), settlement_data("run-x", 1, "x")),
            ]
        ),
    )
    notifications: list[GuardianNotification] = []
    reconciled: list[tuple[str, int]] = []
    resumed: list[tuple[str, int]] = []
    resume_keys: list[str] = []

    def reconcile(event: SettlementRevision) -> ReconcileDecision:
        reconciled.append(event.key)
        return ReconcileDecision(request_resume=True, reason="test request")

    def resume(event: SettlementRevision, key: str) -> Mapping[str, object]:
        resumed.append(event.key)
        resume_keys.append(key)
        return {
            "accepted": True,
            "retryable": False,
            "terminal": True,
            "reason": "accepted",
        }

    state_path = tmp_path / "guardian" / "state.json"
    state = GuardianState.load(state_path)
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=notifications.append,
        reconciler=reconcile,
        resume=resume,
        opener=opener,
    )

    baseline = worker.consume_connection()
    active = worker.consume_connection()

    assert baseline.frames == 1
    assert baseline.heartbeats == 0
    assert baseline.claimed == 1
    assert baseline.completed_baseline is True
    assert active.frames == 4
    assert active.claimed == 3
    assert active.completed_actions == 3
    assert [(notice.event.run_id, notice.severity) for notice in notifications] == [
        ("run-f", "info"),
        ("run-n", "warning"),
        ("run-x", "critical"),
    ]
    assert reconciled == [("run-n", 1)]
    assert resumed == [("run-n", 1)]
    assert resume_keys == ["settlement:run-n:1"]
    assert state.cursor == v2_cursor(400)
    assert state.baseline_complete is True

    reloaded = GuardianState.load(state_path)
    assert reloaded.cursor == v2_cursor(400)
    assert reloaded.baseline_complete is True
    assert set(reloaded.processed) == {
        ("historical", 1),
        ("run-f", 1),
        ("run-n", 1),
        ("run-x", 1),
    }
    url, headers, timeout = opener.calls[0]
    assert url == "http://127.0.0.1:3024/api/control/events"
    assert "last-event-id" not in headers
    assert headers["accept"] == "text/event-stream"
    assert timeout == 30.0
    assert opener.calls[1][0].endswith(
        "/api/control/events?since=v2%3A019f-stream-epoch%3A0%3A100"
    )
    assert opener.calls[1][1]["last-event-id"] == historical


def test_disconnect_before_caught_up_keeps_suppressing_after_reconnect(
    tmp_path: Path,
) -> None:
    start = v2_cursor(0)
    old_cursor = v2_cursor(90)
    opener = QueueOpener(
        FakeResponse(
            [
                *boundary(start),
                *frame(old_cursor, settlement_data("old", 1, "n")),
            ]
        ),
        FakeResponse([*boundary(old_cursor), *caught_up(old_cursor)]),
        FakeResponse(
            [
                *boundary(old_cursor),
                *caught_up(old_cursor),
                *frame(v2_cursor(180), settlement_data("new", 1, "n")),
            ]
        ),
    )
    notifications: list[GuardianNotification] = []
    state = GuardianState.load(tmp_path / "state.json")
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=notifications.append,
        opener=opener,
    )

    worker.consume_connection()
    assert state.baseline_complete is False
    assert notifications == []

    worker.consume_connection()
    assert state.baseline_complete is True
    assert notifications == []

    worker.consume_connection()
    assert [notice.event.run_id for notice in notifications] == ["new"]
    assert opener.calls[1][0].endswith(
        "/api/control/events?since=v2%3A019f-stream-epoch%3A0%3A90"
    )
    assert opener.calls[1][1]["last-event-id"] == old_cursor
    assert opener.calls[2][0].endswith(
        "/api/control/events?since=v2%3A019f-stream-epoch%3A0%3A90"
    )


def test_generation_boundary_keeps_revision_dedupe(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    state = GuardianState(
        path=state_path,
        cursor=v2_cursor(999),
        baseline_complete=True,
        highwater={
            "run-a": CompletionRecord(
                revision=1,
                outcome="terminal",
                reason="already handled",
            )
        },
    )
    state.persist()
    state = GuardianState.load(state_path)
    opener = QueueOpener(
        FakeResponse(
            [
                *boundary(
                    v2_cursor(999),
                    v2_cursor(0, generation=1),
                    reason="generation_change",
                ),
                *caught_up(v2_cursor(0, generation=1)),
                *frame(
                    v2_cursor(120, generation=1),
                    settlement_data(
                        "new-prefix",
                        1,
                        "n",
                        source="trust",
                        repo_root=tmp_path,
                    ),
                ),
                *frame(
                    v2_cursor(240, generation=1),
                    settlement_data("run-a", 1, "n"),
                ),
                *frame(
                    v2_cursor(1_200, generation=1),
                    settlement_data("run-a", 2, "f"),
                ),
            ]
        )
    )
    notifications: list[GuardianNotification] = []
    reconciled: list[tuple[str, int]] = []

    def reconcile(event: SettlementRevision) -> ReconcileDecision:
        reconciled.append(event.key)
        return ReconcileDecision()

    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=notifications.append,
        reconciler=reconcile,
        opener=opener,
    )

    worker.consume_connection()

    assert state.cursor == v2_cursor(1_200, generation=1)
    assert [notice.event.key for notice in notifications] == [
        ("new-prefix", 1),
        ("run-a", 2),
    ]
    assert reconciled == [("new-prefix", 1)]
    assert opener.calls[0][0].endswith(
        "/api/control/events?since=v2%3A019f-stream-epoch%3A0%3A999"
    )
    assert GuardianState.load(state_path).cursor == v2_cursor(
        1_200,
        generation=1,
    )


def test_non_settlement_and_malformed_frames_only_advance_cursor(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    non_settlement = json.dumps({"run_id": "run-a", "kind": "progress", "payload": {}})
    opener = QueueOpener(
        FakeResponse(
            [
                *caught_up(0),
                *frame(10, non_settlement),
                *frame(20, "{not-json"),
                *frame(30, settlement_data("run-a", 1, "n", verdict="failed")),
            ]
        )
    )
    notifications: list[GuardianNotification] = []
    state = GuardianState(
        path=tmp_path / "state.json",
        baseline_complete=True,
    )
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=notifications.append,
        opener=opener,
    )

    with caplog.at_level(logging.CRITICAL, logger="vibecrafted_core.guardian"):
        stats = worker.consume_connection()

    assert stats.frames == 3
    assert stats.claimed == 0
    assert state.cursor == 30
    assert notifications == []
    assert "quarantined invalid settlement frame" in caplog.text
    dead_letter = json.loads(
        (state.path.parent / "dead_letters.json").read_text(encoding="utf-8")
    )
    assert dead_letter["schema"] == guardian_module.GUARDIAN_DEAD_LETTER_SCHEMA
    assert dead_letter["entries"][0]["sse_cursor"] == 30
    assert dead_letter["entries"][0]["reason"] == "invalid settlement.changed contract"


def test_processed_history_compacts_to_max_revision_per_run(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = GuardianState(path=path)

    assert state.suppress(10, ("a", 1)) is True
    assert state.suppress(20, ("a", 2)) is True
    assert state.suppress(30, ("b", 1)) is True
    assert state.processed == [("a", 2), ("b", 1)]

    loaded = GuardianState.load(path)
    assert loaded.cursor == 30
    assert loaded.processed == [("a", 2), ("b", 1)]


def test_failed_claim_persist_does_not_poison_in_memory_dedupe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = GuardianState(
        path=tmp_path / "state.json",
        baseline_complete=True,
    )
    event = parse_settlement_revision(settlement_data("run-loss", 1, "n"))
    assert event is not None

    def fail_write(_path: Path, _payload: bytes) -> None:
        raise OSError("disk unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(guardian_module, "_atomic_private_write", fail_write)
        with pytest.raises(OSError, match="disk unavailable"):
            state.claim(10, event)

    assert state.cursor == 0
    assert state.processed == []
    assert state.pending == {}
    assert state.claim(10, event) is True
    assert state.pending == {
        ("run-loss", 1): PendingRecord(event=event, stream_cursor=10)
    }


def test_pending_action_survives_kill_window_and_is_recovered_once(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    event = parse_settlement_revision(
        settlement_data(
            "run-pending",
            2,
            "n",
            source="trust",
            repo_root=tmp_path,
        )
    )
    assert event is not None
    before_crash = GuardianState(
        path=path,
        cursor=v2_cursor(40),
        baseline_complete=True,
    )
    assert before_crash.claim(v2_cursor(44), event) is True

    recovered = GuardianState.load(path)
    notifications: list[GuardianNotification] = []
    reconciled: list[str] = []

    def reconcile(item: SettlementRevision) -> ReconcileDecision:
        reconciled.append(item.idempotency_key)
        return ReconcileDecision()

    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=recovered,
        notifier=notifications.append,
        reconciler=reconcile,
        opener=QueueOpener(FakeResponse(caught_up(v2_cursor(44)))),
    )

    worker.consume_connection()

    assert [item.event.key for item in notifications] == [("run-pending", 2)]
    assert reconciled == ["settlement:run-pending:2"]
    completed = GuardianState.load(path)
    assert completed.pending == {}
    assert completed.processed == [("run-pending", 2)]

    duplicate_notifications: list[GuardianNotification] = []
    duplicate_worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=completed,
        notifier=duplicate_notifications.append,
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(completed.cursor),
                    *frame(
                        v2_cursor(88),
                        settlement_data(
                            "run-pending",
                            2,
                            "n",
                            source="trust",
                            repo_root=tmp_path,
                        ),
                    ),
                ]
            )
        ),
    )
    duplicate_worker.consume_connection()
    assert duplicate_notifications == []


def test_resume_failure_stays_pending_and_retries_with_same_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    state = GuardianState(
        path=path,
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    opener = QueueOpener(
        FakeResponse(
            [
                *caught_up(v2_cursor(0)),
                *frame(
                    v2_cursor(50),
                    settlement_data(
                        "run-retry",
                        1,
                        "n",
                        source="trust",
                        repo_root=tmp_path,
                    ),
                ),
            ]
        ),
        FakeResponse(caught_up(v2_cursor(50))),
    )
    resume_keys: list[str] = []
    now = [0.0]

    def resume(_event: SettlementRevision, key: str) -> object:
        resume_keys.append(key)
        if len(resume_keys) == 1:
            raise OSError("transient resume failure")
        return {
            "accepted": True,
            "retryable": False,
            "terminal": True,
            "reason": "accepted",
            "idempotency_key": key,
        }

    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda _notification: None,
        reconciler=lambda _event: ReconcileDecision(request_resume=True),
        resume=resume,
        opener=opener,
        clock=lambda: now[0],
    )

    failed = worker.consume_connection()
    assert failed.action_failures == 1
    assert state.pending.keys() == {("run-retry", 1)}
    assert state.processed == []

    now[0] = 2.0
    recovered = worker.consume_connection()
    assert recovered.completed_actions == 1
    assert recovered.action_failures == 0
    assert resume_keys == [
        "settlement:run-retry:1",
        "settlement:run-retry:1",
    ]
    assert state.pending == {}
    assert state.processed == [("run-retry", 1)]


def test_recovery_adapter_accepts_once_and_deduplicates_replayed_revision(
    tmp_path: Path,
) -> None:
    event = settlement_event(
        "run-native",
        7,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    projection_opener = QueueOpener(
        FakeJSONResponse(resumable_projection(event, tmp_path))
    )
    guard_calls: list[dict[str, object]] = []
    resume_calls: list[dict[str, object]] = []

    def enforce_guard(**kwargs: object) -> object:
        guard_calls.append(dict(kwargs))
        return SimpleNamespace(allowed=True)

    def native_resume(
        run_id: str,
        source_dir: str | Path,
        *,
        expected_agent: str,
        expected_agent_session_id: str,
        expected_settlement_revision: int,
        expected_receipt_id: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        resume_calls.append(
            {
                "run_id": run_id,
                "source_dir": source_dir,
                "expected_agent": expected_agent,
                "expected_agent_session_id": expected_agent_session_id,
                "expected_settlement_revision": expected_settlement_revision,
                "expected_receipt_id": expected_receipt_id,
                "idempotency_key": idempotency_key,
            }
        )
        return {
            "accepted": True,
            "retryable": False,
            "terminal": True,
            "reason": "accepted",
            "deduplicated": True,
            "idempotency_key": idempotency_key,
        }

    recovery = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=projection_opener,
        guard_enforcer=enforce_guard,
        native_resumer=native_resume,
    )
    state = GuardianState(
        path=tmp_path / "guardian-state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda _notification: None,
        reconciler=recovery.reconcile,
        resume=recovery.resume,
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(v2_cursor(0)),
                    *frame(
                        v2_cursor(70),
                        settlement_data(
                            event.run_id,
                            event.revision,
                            event.tui,
                            source=event.source,
                            repo_root=tmp_path,
                        ),
                    ),
                    *frame(
                        v2_cursor(71),
                        settlement_data(
                            event.run_id,
                            event.revision,
                            event.tui,
                            source=event.source,
                            repo_root=tmp_path,
                        ),
                    ),
                ]
            )
        ),
    )

    stats = worker.consume_connection()

    assert stats.frames == 2
    assert stats.claimed == 1
    assert stats.completed_actions == 1
    assert projection_opener.calls == [
        (
            "http://127.0.0.1:3024/api/control/runs/run-native",
            {"accept": "application/json", "cache-control": "no-cache"},
            5.0,
        )
    ]
    assert guard_calls == [
        {
            "repo": tmp_path,
            "sha": "a" * 40,
            "skill": "implement",
        }
    ]
    assert resume_calls == [
        {
            "run_id": "run-native",
            "source_dir": tmp_path,
            "expected_agent": "codex",
            "expected_agent_session_id": "019-native-session",
            "expected_settlement_revision": 7,
            "expected_receipt_id": event.receipt_id,
            "idempotency_key": "settlement:run-native:7",
        }
    ]
    assert state.pending == {}
    assert state.processed == [("run-native", 7)]


def test_recovery_contexts_are_single_call_capabilities(
    tmp_path: Path,
) -> None:
    terminal_events = [
        settlement_event(
            f"terminal-context-{index}",
            1,
            "n",
            source="trust",
            repo_root=tmp_path,
        )
        for index in range(64)
    ]
    terminal = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=QueueOpener(
            *[
                FakeJSONResponse(resumable_projection(event, tmp_path))
                for event in terminal_events
            ]
        ),
        guard_enforcer=lambda **_kwargs: SimpleNamespace(allowed=True),
        native_resumer=lambda *_args, **_kwargs: action_result(
            accepted=False,
            retryable=False,
            terminal=True,
            reason="expected_agent_session_mismatch",
        ),
    )

    for event in terminal_events:
        assert terminal.reconcile(event).request_resume is True
        result = terminal.resume(event, event.idempotency_key)
        assert result["terminal"] is True

    assert terminal._contexts == {}

    retry_event = settlement_event(
        "retryable-context",
        1,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    retryable = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=QueueOpener(
            FakeJSONResponse(resumable_projection(retry_event, tmp_path))
        ),
        guard_enforcer=lambda **_kwargs: SimpleNamespace(allowed=True),
        native_resumer=lambda *_args, **_kwargs: action_result(
            accepted=False,
            retryable=True,
            terminal=False,
            reason="provider_temporarily_unavailable",
        ),
    )

    assert retryable.reconcile(retry_event).request_resume is True
    retryable.resume(retry_event, retry_event.idempotency_key)
    assert retryable._contexts == {}

    exception_event = settlement_event(
        "exception-context",
        1,
        "n",
        source="trust",
        repo_root=tmp_path,
    )

    def fail_resume(*_args: object, **_kwargs: object) -> Mapping[str, object]:
        raise OSError("provider launch failed")

    exceptional = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=QueueOpener(
            FakeJSONResponse(resumable_projection(exception_event, tmp_path))
        ),
        guard_enforcer=lambda **_kwargs: SimpleNamespace(allowed=True),
        native_resumer=fail_resume,
    )
    assert exceptional.reconcile(exception_event).request_resume is True
    with pytest.raises(OSError, match="provider launch failed"):
        exceptional.resume(exception_event, exception_event.idempotency_key)
    assert exceptional._contexts == {}


@pytest.mark.parametrize(
    ("tui", "overrides", "reason"),
    [
        ("n", {"settlement_revision": 8}, "stale_or_mismatched_settlement"),
        (
            "n",
            {"settlement_verdict": "failed"},
            "stale_or_mismatched_settlement",
        ),
        ("n", {"settlement_tui": "x"}, "stale_or_mismatched_settlement"),
        ("n", {"worker_alive": True}, "worker_not_confirmed_dead"),
        ("n", {"recovery_required": False}, "recovery_not_required"),
        ("n", {"stop_reason": "manual_stop"}, "manual_stop_or_cancel"),
        ("x", {}, "settlement_x_not_resumable"),
        ("f", {}, "settlement_f_not_resumable"),
        (
            "n",
            {"controls": {"native_resume_candidate": None}},
            "native_resume_candidate_missing",
        ),
        (
            "n",
            {
                "controls": {
                    "native_resume_candidate": {
                        "agent": "codex",
                        "agent_session_id": "unknown",
                    }
                }
            },
            "native_resume_identity_missing",
        ),
        ("n", {"attempt": 1}, "automatic_attempt_budget_exhausted"),
        (
            "n",
            {"state": "running", "liveness": "active", "exit_code": None},
            "run_not_terminal",
        ),
    ],
    ids=[
        "stale-revision",
        "verdict-mismatch",
        "tui-mismatch",
        "worker-live",
        "recovery-false",
        "manual-stop",
        "x-critical",
        "f-finalized",
        "candidate-missing",
        "candidate-identity-missing",
        "attempt-budget",
        "non-terminal",
    ],
)
def test_recovery_adapter_blocks_unsafe_projection(
    tmp_path: Path,
    tui: str,
    overrides: dict[str, object],
    reason: str,
) -> None:
    event = settlement_event(
        f"run-{tui}",
        7,
        tui,
        source="trust",
        repo_root=tmp_path,
    )
    projection = resumable_projection(event, tmp_path)
    projection.update(overrides)
    opener = QueueOpener(FakeJSONResponse(projection))

    def must_not_guard(**_kwargs: object) -> object:
        raise AssertionError("vc-guard must not run before projection policy passes")

    recovery = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=opener,
        guard_enforcer=must_not_guard,
        native_resumer=lambda *_args, **_kwargs: {
            "accepted": True,
        },
    )

    decision = recovery.reconcile(event)

    assert decision == ReconcileDecision(request_resume=False, reason=reason)
    assert len(opener.calls) == 1


def test_recovery_adapter_never_resumes_auto_needs_attention(
    tmp_path: Path,
) -> None:
    event = settlement_event("run-auto", 1, "n", source="auto")
    recovery = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=QueueOpener(FakeJSONResponse(resumable_projection(event, tmp_path))),
        guard_enforcer=lambda **_kwargs: pytest.fail(
            "vc-guard must not authorize an automatic settlement"
        ),
        native_resumer=lambda *_args, **_kwargs: pytest.fail(
            "native resume must not run for an automatic settlement"
        ),
    )

    assert recovery.reconcile(event) == ReconcileDecision(
        request_resume=False,
        reason="vc_trust_authority_missing",
    )


@pytest.mark.parametrize("tui", ["f", "x", "n"])
def test_terminal_event_schedules_triage_before_notification_only_gate(
    tmp_path: Path,
    tui: str,
) -> None:
    run_id = f"run-{tui}"
    event = settlement_event(run_id, 3, tui)
    state = GuardianState(
        path=tmp_path / "guardian-state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    order: list[str] = []

    def schedule(scheduled: str) -> bool:
        order.append(f"triage:{scheduled}")
        return True

    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda _notification: order.append("notify"),
        reconciler=lambda _event: pytest.fail(
            "V1 notification-only settlement must not reach resume reconciliation"
        ),
        triage_scheduler=schedule,
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(v2_cursor(0)),
                    *frame(
                        v2_cursor(1),
                        settlement_data(
                            event.run_id,
                            event.revision,
                            event.tui,
                            source=event.source,
                        ),
                    ),
                ]
            )
        ),
    )

    stats = worker.consume_connection()

    assert stats.completed_actions == 1
    assert order == [f"triage:{run_id}", "notify"]
    assert state.highwater[run_id].reason == "legacy_notification_only"


def test_triage_persistence_failure_preserves_stream_cursor_for_replay(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    event = settlement_event("run-finalized", 4, "f")
    state = GuardianState(
        path=tmp_path / "guardian-state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda _notification: pytest.fail(
            "unscheduled event must remain replayable before notification"
        ),
        triage_scheduler=lambda _run_id: False,
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(v2_cursor(0)),
                    *frame(
                        v2_cursor(1),
                        settlement_data(
                            event.run_id,
                            event.revision,
                            event.tui,
                            source=event.source,
                        ),
                    ),
                ]
            )
        ),
    )

    with caplog.at_level(logging.ERROR, logger="vibecrafted_core.guardian"):
        stats = worker.consume_connection()

    assert stats.action_failures == 1
    assert state.cursor == v2_cursor(0)
    assert state.pending == {}
    assert "was not durably queued" in caplog.text


def test_claim_failure_preserves_cursor_and_replays_after_capacity_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    event = settlement_event("run-outbox-full", 1, "x")
    state = GuardianState(
        path=tmp_path / "guardian-state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    notifications: list[str] = []

    def make_worker() -> GuardianWorker:
        return GuardianWorker(
            server_url="http://127.0.0.1:3024",
            state=state,
            notifier=lambda notification: notifications.append(
                notification.event.run_id
            ),
            triage_scheduler=lambda _run_id: True,
            opener=QueueOpener(
                FakeResponse(
                    [
                        *caught_up(v2_cursor(0)),
                        *frame(
                            v2_cursor(1),
                            settlement_data(
                                event.run_id,
                                event.revision,
                                event.tui,
                                source=event.source,
                            ),
                        ),
                    ]
                )
            ),
        )

    monkeypatch.setattr(guardian_module, "MAX_PENDING_RECORDS", 0)
    first = make_worker().consume_connection()

    assert first.action_failures == 1
    assert state.cursor == v2_cursor(0)
    assert state.pending == {}
    assert notifications == []

    monkeypatch.setattr(guardian_module, "MAX_PENDING_RECORDS", 1)
    second = make_worker().consume_connection()

    assert second.claimed == 1
    assert second.completed_actions == 1
    assert state.cursor == v2_cursor(1)
    assert notifications == ["run-outbox-full"]


def test_durable_triage_outbox_survives_crash_before_worker_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    monkeypatch.setattr(
        guardian_module,
        "_enqueue_terminal_triage_job",
        lambda _key, _run_id: True,
    )

    assert guardian_module._schedule_terminal_triage_run("run-crash-window") is True
    outbox = guardian_module._terminal_triage_outbox_path("run-crash-window")
    assert outbox.is_file()

    attempted: list[str] = []

    def reconcile(run_id: str) -> bool:
        attempted.append(run_id)
        return True

    monkeypatch.setattr(
        guardian_module,
        "_reconcile_terminal_triage_run",
        reconcile,
    )
    monkeypatch.setattr(
        guardian_module,
        "_terminal_triage_failure_reason",
        lambda _run_id: "triage_incomplete",
    )
    guardian_module._recover_terminal_triage_outbox()

    assert attempted == ["run-crash-window"]
    assert not outbox.exists()


def test_terminal_triage_outbox_recovery_scan_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    for index in range(5):
        guardian_module._persist_terminal_triage_outbox(f"run-{index}")
    monkeypatch.setattr(guardian_module, "TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT", 2)
    monkeypatch.setattr(guardian_module, "TERMINAL_TRIAGE_OUTBOX_ATTEMPT_LIMIT", 2)

    reads: list[Path] = []
    original_read = guardian_module._read_terminal_triage_outbox

    def recording_read(path: Path) -> guardian_module.TerminalTriageOutboxRecord:
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(
        guardian_module,
        "_read_terminal_triage_outbox",
        recording_read,
    )
    monkeypatch.setattr(
        guardian_module,
        "_reconcile_terminal_triage_run",
        lambda _run_id: False,
    )
    monkeypatch.setattr(
        guardian_module,
        "_terminal_triage_failure_reason",
        lambda _run_id: "temporary_failure",
    )

    guardian_module._recover_terminal_triage_outbox()

    # Two scan reads plus one generation-CAS refresh for each selected job.
    assert len(reads) <= 4


@pytest.mark.parametrize("run_id", [".", ".."])
def test_terminal_triage_outbox_rejects_relative_path_segments(run_id: str) -> None:
    with pytest.raises(guardian_module.GuardianStateError):
        guardian_module._canonical_triage_run_id(run_id)


def test_in_memory_queue_full_leaves_durable_triage_outbox(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    jobs: queue.Queue[tuple[str, str | None]] = queue.Queue(maxsize=1)
    jobs.put_nowait(("occupied", None))
    monkeypatch.setattr(guardian_module, "_TERMINAL_TRIAGE_JOBS", jobs)
    monkeypatch.setattr(guardian_module, "_TERMINAL_TRIAGE_PENDING", {"occupied"})
    monkeypatch.setattr(guardian_module, "_TERMINAL_TRIAGE_LOCK", threading.Lock())
    monkeypatch.setattr(guardian_module, "_TERMINAL_TRIAGE_THREAD", None)

    assert guardian_module._schedule_terminal_triage_run("run-queue-full") is False
    assert guardian_module._terminal_triage_outbox_path("run-queue-full").is_file()


def test_terminal_triage_outbox_hard_capacity_backpressures_new_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    monkeypatch.setattr(guardian_module, "TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT", 2)
    monkeypatch.setattr(
        guardian_module,
        "_enqueue_terminal_triage_job",
        lambda _key, _run_id: True,
    )

    assert guardian_module._schedule_terminal_triage_run("run-one") is True
    assert guardian_module._schedule_terminal_triage_run("run-two") is True
    assert guardian_module._schedule_terminal_triage_run("run-three") is False

    jobs = sorted(guardian_module._terminal_triage_outbox_root().glob("*.json"))
    assert len(jobs) == 2
    assert not guardian_module._terminal_triage_outbox_path("run-three").exists()


def test_stale_worker_cannot_clear_newer_triage_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    path = guardian_module._persist_terminal_triage_outbox("run-generation")
    first = guardian_module._read_terminal_triage_outbox(path)
    guardian_module._persist_terminal_triage_outbox("run-generation")
    second = guardian_module._read_terminal_triage_outbox(path)

    assert second.queued_at_ns > first.queued_at_ns
    assert (
        guardian_module._clear_terminal_triage_outbox(
            "run-generation",
            expected_generation=first.queued_at_ns,
        )
        is False
    )
    assert path.is_file()
    assert guardian_module._clear_terminal_triage_outbox(
        "run-generation",
        expected_generation=second.queued_at_ns,
    )
    assert not path.exists()


def test_terminal_triage_outbox_reads_v1_and_migrates_on_schedule(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    path = guardian_module._terminal_triage_outbox_path("run-v1")
    guardian_module._atomic_private_write(
        path,
        b'{"queued_at_ns":1,"run_id":"run-v1","schema":"vibecrafted.terminal-triage-outbox.v1"}\n',
    )

    legacy = guardian_module._read_terminal_triage_outbox(path)
    assert legacy.attempts == 0
    guardian_module._persist_terminal_triage_outbox("run-v1")
    migrated = guardian_module._read_terminal_triage_outbox(path)

    assert migrated.queued_at_ns > legacy.queued_at_ns
    assert migrated.attempts == 0
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == (
        guardian_module.TERMINAL_TRIAGE_OUTBOX_SCHEMA
    )


def test_terminal_triage_retry_has_backoff_and_preserves_budget_on_reschedule(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    path = guardian_module._persist_terminal_triage_outbox("run-backoff")
    first = guardian_module._read_terminal_triage_outbox(path)

    assert guardian_module._refresh_terminal_triage_outbox(
        "run-backoff",
        expected_generation=first.queued_at_ns,
        reason="temporary_failure",
    )
    backed_off = guardian_module._read_terminal_triage_outbox(path)
    assert backed_off.attempts == 1
    assert backed_off.next_retry_at_ns > time.time_ns()

    attempted: list[str] = []
    monkeypatch.setattr(
        guardian_module,
        "_reconcile_terminal_triage_run",
        lambda run_id: attempted.append(run_id) or True,
    )
    guardian_module._recover_terminal_triage_outbox()
    assert attempted == []

    guardian_module._persist_terminal_triage_outbox("run-backoff")
    rescheduled = guardian_module._read_terminal_triage_outbox(path)
    assert rescheduled.attempts == 1
    assert rescheduled.next_retry_at_ns == backed_off.next_retry_at_ns


def test_terminal_triage_retry_reason_is_bounded_by_utf8_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    path = guardian_module._persist_terminal_triage_outbox("run-unicode-reason")
    first = guardian_module._read_terminal_triage_outbox(path)
    reason = "x" * (guardian_module.MAX_STATE_TEXT_BYTES - 1) + "€tail"

    assert guardian_module._refresh_terminal_triage_outbox(
        "run-unicode-reason",
        expected_generation=first.queued_at_ns,
        reason=reason,
    )

    refreshed = guardian_module._read_terminal_triage_outbox(path)
    assert refreshed.last_reason == "x" * (guardian_module.MAX_STATE_TEXT_BYTES - 1)
    assert len(refreshed.last_reason.encode("utf-8")) <= (
        guardian_module.MAX_STATE_TEXT_BYTES
    )


def test_permanent_terminal_triage_failure_dead_letters_without_invoking_vc_frame(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    path = guardian_module._persist_terminal_triage_outbox("run-poison")
    monkeypatch.setattr(
        guardian_module,
        "_terminal_triage_failure_reason",
        lambda _run_id: "transfer_proof_invalid: receipt identity mismatch",
    )
    monkeypatch.setattr(
        guardian_module,
        "_reconcile_terminal_triage_run",
        lambda _run_id: pytest.fail("permanent poison must not invoke vc-frame"),
    )

    guardian_module._recover_terminal_triage_outbox()

    assert not path.exists()
    dead_letters = list(
        guardian_module._terminal_triage_quarantine_root().glob("*.dead-letter.json")
    )
    assert len(dead_letters) == 1
    payload = json.loads(dead_letters[0].read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-poison"
    assert payload["reason"].startswith("transfer_proof_invalid:")


def test_terminal_triage_exhausts_bounded_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    monkeypatch.setattr(guardian_module, "TERMINAL_TRIAGE_MAX_ATTEMPTS", 1)
    path = guardian_module._persist_terminal_triage_outbox("run-exhausted")
    record = guardian_module._read_terminal_triage_outbox(path)

    assert guardian_module._refresh_terminal_triage_outbox(
        "run-exhausted",
        expected_generation=record.queued_at_ns,
        reason="temporary_failure",
    )

    assert not path.exists()
    assert (
        len(
            list(
                guardian_module._terminal_triage_quarantine_root().glob(
                    "*.dead-letter.json"
                )
            )
        )
        == 1
    )


def test_full_terminal_triage_quarantine_archives_oldest_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    monkeypatch.setattr(guardian_module, "TERMINAL_TRIAGE_QUARANTINE_CAPACITY", 2)
    quarantine = guardian_module._terminal_triage_quarantine_root()
    oldest = quarantine / "oldest.dead-letter.json"
    newest = quarantine / "newest.dead-letter.json"
    oldest.write_bytes(b"oldest evidence\n")
    newest.write_bytes(b"newer evidence\n")
    os.utime(oldest, ns=(1, 1))
    os.utime(newest, ns=(2, 2))

    outbox = guardian_module._persist_terminal_triage_outbox("run-overflow")
    record = guardian_module._read_terminal_triage_outbox(outbox)
    assert guardian_module._dead_letter_terminal_triage_outbox_locked(
        record,
        "runtime_meta_unavailable",
    )

    assert not outbox.exists()
    assert not oldest.exists()
    assert newest.exists()
    assert len(list(quarantine.glob("*.dead-letter.json"))) == 2
    archived = list(
        guardian_module._terminal_triage_quarantine_archive_root().iterdir()
    )
    assert len(archived) == 1
    assert archived[0].read_bytes() == b"oldest evidence\n"


def test_terminal_triage_scheduler_uses_one_coalescing_background_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    stop = threading.Event()
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    monkeypatch.setattr(
        guardian_module,
        "_TERMINAL_TRIAGE_JOBS",
        guardian_module.queue.Queue(maxsize=4),
    )
    monkeypatch.setattr(guardian_module, "_TERMINAL_TRIAGE_PENDING", set())
    monkeypatch.setattr(guardian_module, "_TERMINAL_TRIAGE_LOCK", threading.Lock())
    monkeypatch.setattr(guardian_module, "_TERMINAL_TRIAGE_THREAD", None)
    monkeypatch.setattr(guardian_module, "_TERMINAL_TRIAGE_STOP", stop)

    def reconcile(_run_id: str) -> bool:
        entered.set()
        assert release.wait(5)
        return True

    monkeypatch.setattr(
        guardian_module,
        "_reconcile_terminal_triage_run",
        reconcile,
    )
    monkeypatch.setattr(
        guardian_module,
        "_terminal_triage_failure_reason",
        lambda _run_id: "triage_incomplete",
    )

    assert guardian_module._schedule_terminal_triage_run("run-coalesced") is True
    assert entered.wait(1)
    first_thread = guardian_module._TERMINAL_TRIAGE_THREAD
    assert first_thread is not None
    assert guardian_module._schedule_terminal_triage_run("run-coalesced") is True
    assert guardian_module._TERMINAL_TRIAGE_THREAD is first_thread

    stop.set()
    release.set()
    guardian_module._TERMINAL_TRIAGE_JOBS.join()
    first_thread.join(timeout=1)
    assert not first_thread.is_alive()


def test_corrupt_triage_outbox_is_quarantined_before_capacity_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    monkeypatch.setattr(guardian_module, "TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT", 1)
    monkeypatch.setattr(guardian_module, "TERMINAL_TRIAGE_QUARANTINE_CAPACITY", 2)
    monkeypatch.setattr(
        guardian_module,
        "_enqueue_terminal_triage_job",
        lambda _key, _run_id: True,
    )
    outbox_root = guardian_module._terminal_triage_outbox_root()
    poison = outbox_root / "poison.json"
    poison.mkdir()

    assert guardian_module._schedule_terminal_triage_run("run-real") is True

    quarantine = list(guardian_module._terminal_triage_quarantine_root().iterdir())
    assert not poison.exists()
    assert len(quarantine) == 1
    assert quarantine[0].is_dir()
    assert guardian_module._terminal_triage_outbox_path("run-real").is_file()


def test_full_quarantine_backpressures_corrupt_triage_outbox(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(guardian_module, "vibecrafted_home", lambda: tmp_path)
    monkeypatch.setattr(guardian_module, "TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT", 1)
    monkeypatch.setattr(guardian_module, "TERMINAL_TRIAGE_QUARANTINE_CAPACITY", 0)
    poison = guardian_module._terminal_triage_outbox_root() / "poison.json"
    poison.mkdir()

    with pytest.raises(
        GuardianStateLimitError,
        match="capacity could not be established",
    ):
        guardian_module._persist_terminal_triage_outbox("run-real")

    assert poison.is_dir()
    assert not guardian_module._terminal_triage_outbox_path("run-real").exists()


def test_server_settlement_board_publish_runs_for_caught_up_and_settlement(
    tmp_path: Path,
) -> None:
    state = GuardianState(
        path=tmp_path / "guardian-state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    published: list[str] = []
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda _notification: None,
        reconciler=lambda _event: ReconcileDecision(request_resume=False),
        board_publisher=lambda: published.append("publish"),
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(v2_cursor(0)),
                    *frame(
                        v2_cursor(1),
                        settlement_data("run-publish", 1, "f"),
                    ),
                ]
            )
        ),
    )

    stats = worker.consume_connection()

    assert stats.completed_actions == 1
    assert published == ["publish", "publish"]


def test_server_settlement_board_publish_failure_never_blocks_settlement(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = GuardianState(
        path=tmp_path / "guardian-state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )

    def publish() -> None:
        raise OSError("vc-frame unavailable")

    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda _notification: None,
        reconciler=lambda _event: ReconcileDecision(request_resume=False),
        board_publisher=publish,
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(v2_cursor(0)),
                    *frame(
                        v2_cursor(1),
                        settlement_data("run-publish-failure", 1, "f"),
                    ),
                ]
            )
        ),
    )

    with caplog.at_level(logging.ERROR, logger="vibecrafted_core.guardian"):
        stats = worker.consume_connection()

    assert stats.completed_actions == 1
    assert "server settlement-board publication failed" in caplog.text


def test_recovery_adapter_obeys_vc_guard_block(tmp_path: Path) -> None:
    event = settlement_event(
        "run-guarded",
        2,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    recovery = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=QueueOpener(FakeJSONResponse(resumable_projection(event, tmp_path))),
        guard_enforcer=lambda **_kwargs: SimpleNamespace(allowed=False),
        native_resumer=lambda *_args, **_kwargs: pytest.fail(
            "native resume must not run after a vc-guard block"
        ),
    )

    assert recovery.reconcile(event) == ReconcileDecision(
        request_resume=False,
        reason="vc_guard_blocked",
    )


def test_v2_needs_attention_reaches_resume_only_after_triage_and_vc_guard(
    tmp_path: Path,
) -> None:
    data = settlement_data(
        "run-guarded-resume",
        2,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    event = parse_settlement_revision(data)
    assert event is not None
    order: list[str] = []

    def enforce_guard(**_kwargs: object) -> object:
        order.append("guard")
        return SimpleNamespace(allowed=True)

    def native_resume(*_args: object, **_kwargs: object) -> Mapping[str, object]:
        order.append("resume")
        return action_result(
            accepted=True,
            retryable=False,
            terminal=True,
            reason="accepted",
        )

    def schedule_triage(_run_id: str) -> bool:
        order.append("triage")
        return True

    recovery = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=QueueOpener(FakeJSONResponse(resumable_projection(event, tmp_path))),
        guard_enforcer=enforce_guard,
        native_resumer=native_resume,
    )
    state = GuardianState(
        path=tmp_path / "guardian-state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda _notification: order.append("notify"),
        reconciler=recovery.reconcile,
        resume=recovery.resume,
        triage_scheduler=schedule_triage,
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(v2_cursor(0)),
                    *frame(v2_cursor(1), data),
                ]
            )
        ),
    )

    stats = worker.consume_connection()

    assert stats.completed_actions == 1
    assert order == ["triage", "notify", "guard", "resume"]


def test_recovery_adapter_rejects_projection_receipt_mismatch(
    tmp_path: Path,
) -> None:
    event = settlement_event(
        "run-receipt-mismatch",
        2,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    projection = resumable_projection(event, tmp_path)
    other_receipt = TrustReceiptV1.issue(
        repo_root=str(tmp_path),
        run_id=event.run_id,
        commit_sha="b" * 40,
        trust_verdict="pass-with-gaps",
        settlement_verdict=event.verdict,
        settlement_tui=event.tui,
        settlement_revision=event.revision,
        claim_digest="c" * 64,
    )
    projection["trust_receipt"] = other_receipt.to_payload()
    recovery = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=QueueOpener(FakeJSONResponse(projection)),
        guard_enforcer=lambda **_kwargs: pytest.fail(
            "vc-guard must not run after receipt mismatch"
        ),
        native_resumer=lambda *_args, **_kwargs: pytest.fail(
            "native resume must not run after receipt mismatch"
        ),
    )

    assert recovery.reconcile(event) == ReconcileDecision(
        request_resume=False,
        reason="trust_receipt_mismatch",
    )


@pytest.mark.parametrize("failure_stage", ["http", "guard"])
def test_recovery_truth_or_guard_error_keeps_event_pending(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    event = settlement_event(
        f"run-{failure_stage}-error",
        1,
        "n",
        source="trust",
        repo_root=tmp_path,
    )

    def guard_enforcer(**_kwargs: object) -> object:
        if failure_stage == "guard":
            raise OSError("trust journal unavailable")
        return SimpleNamespace(allowed=True)

    if failure_stage == "http":
        projection_opener = QueueOpener(urllib.error.URLError("projection down"))
    else:
        projection_opener = QueueOpener(
            FakeJSONResponse(resumable_projection(event, tmp_path))
        )

    recovery = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=projection_opener,
        guard_enforcer=guard_enforcer,
        native_resumer=lambda *_args, **_kwargs: {"accepted": True},
    )
    state = GuardianState(
        path=tmp_path / "guardian-state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda _notification: None,
        reconciler=recovery.reconcile,
        resume=recovery.resume,
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(v2_cursor(0)),
                    *frame(
                        v2_cursor(50),
                        settlement_data(
                            event.run_id,
                            1,
                            "n",
                            source=event.source,
                            repo_root=tmp_path,
                        ),
                    ),
                ]
            )
        ),
    )

    stats = worker.consume_connection()

    assert stats.action_failures == 1
    assert state.pending[event.key].event == event
    assert state.pending[event.key].notification_done is True
    assert state.pending[event.key].attempts == 1
    assert state.processed == []


def test_reconnect_backoff_is_nonzero_and_bounded(tmp_path: Path) -> None:
    opener = QueueOpener(
        urllib.error.URLError("down-1"),
        urllib.error.URLError("down-2"),
        urllib.error.URLError("down-3"),
        urllib.error.URLError("down-4"),
    )
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=GuardianState(path=tmp_path / "state.json"),
        opener=opener,
    )
    delays: list[float] = []

    worker.run_forever(
        backoff=BoundedBackoff(initial=0.1, maximum=0.2),
        sleep=delays.append,
        max_connections=4,
    )

    assert delays == [0.1, 0.2, 0.2]
    assert len(opener.calls) == 4


def test_single_ping_flapping_does_not_reset_backoff(tmp_path: Path) -> None:
    opener = QueueOpener(
        FakeResponse(heartbeat()),
        FakeResponse(heartbeat()),
        FakeResponse(heartbeat()),
        FakeResponse(heartbeat()),
    )
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=GuardianState(
            path=tmp_path / "state.json",
            baseline_complete=True,
        ),
        opener=opener,
    )
    delays: list[float] = []

    worker.run_forever(
        backoff=BoundedBackoff(initial=0.1, maximum=0.2),
        sleep=delays.append,
        max_connections=4,
    )

    assert delays == [0.1, 0.2, 0.2]


def test_armed_connection_periodically_reattaches_on_heartbeat(
    tmp_path: Path,
) -> None:
    response = FakeResponse(
        [
            *heartbeat(),
            *heartbeat(),
            *frame(10, settlement_data("after-boundary", 1, "n")),
        ]
    )
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=GuardianState(
            path=tmp_path / "state.json",
            baseline_complete=True,
        ),
        opener=QueueOpener(response),
        replay_heartbeats=2,
    )

    stats = worker.consume_connection()

    assert stats.heartbeats == 2
    assert stats.frames == 0
    assert response.closed is True


def test_single_instance_lock_refuses_second_owner(tmp_path: Path) -> None:
    lock = tmp_path / "guardian.lock"

    with (
        single_instance_lock(lock),
        pytest.raises(GuardianAlreadyRunning),
        single_instance_lock(lock),
    ):
        pass

    with single_instance_lock(lock):
        pass


def test_protocol_rejects_non_sse_response(tmp_path: Path) -> None:
    response = FakeResponse([], content_type="application/json")
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=GuardianState(path=tmp_path / "state.json"),
        opener=QueueOpener(response),
    )

    with pytest.raises(GuardianProtocolError):
        worker.consume_connection()
    assert response.closed is True


def test_readiness_is_announced_only_after_valid_sse_response(tmp_path: Path) -> None:
    announced: list[str] = []
    valid = FakeResponse([])
    valid_reconnect = FakeResponse([])
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=GuardianState(path=tmp_path / "valid-state.json"),
        opener=QueueOpener(valid, valid_reconnect),
        ready_callback=lambda: announced.append("ready"),
    )

    worker.consume_connection()
    worker.consume_connection()

    assert announced == ["ready"]

    rejected: list[str] = []
    invalid = FakeResponse([], status=404)
    invalid_worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=GuardianState(path=tmp_path / "invalid-state.json"),
        opener=QueueOpener(invalid),
        ready_callback=lambda: rejected.append("ready"),
    )
    with pytest.raises(GuardianProtocolError, match="HTTP 404"):
        invalid_worker.consume_connection()
    assert rejected == []


def test_ready_receipt_is_atomic_and_removed_only_by_owner(tmp_path: Path) -> None:
    ready_file = tmp_path / "guardian.ready.json"

    guardian_module.write_ready_receipt(
        ready_file,
        nonce="owner-nonce",
        server_url="http://127.0.0.1:3024",
        pid=4321,
    )

    payload = json.loads(ready_file.read_text(encoding="utf-8"))
    assert payload == {
        "schema": guardian_module.GUARDIAN_READY_SCHEMA,
        "nonce": "owner-nonce",
        "pid": 4321,
        "server_url": "http://127.0.0.1:3024",
    }
    assert (
        guardian_module.remove_ready_receipt_if_owned(
            ready_file,
            nonce="other-nonce",
            pid=4321,
        )
        is False
    )
    assert ready_file.is_file()
    assert (
        guardian_module.remove_ready_receipt_if_owned(
            ready_file,
            nonce="owner-nonce",
            pid=4321,
        )
        is True
    )
    assert not ready_file.exists()


def test_notification_mapping_and_macos_log_fallback(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    event = SettlementRevision(
        run_id="run-x",
        revision=4,
        verdict="failed",
        tui="x",
        reason='bad "proof"\nnow',
        source="trust",
        settled_at="2026-07-26T06:00:00+00:00",
    )
    notification = notification_for(event)
    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with caplog.at_level(logging.INFO, logger="vibecrafted_core.guardian"):
        notify_operator(
            notification,
            platform="darwin",
            which=lambda _name: "/usr/bin/osascript",
            runner=runner,
            app_alive=lambda: False,
            home=tmp_path,
        )

    assert notification.severity == "critical"
    assert "Vibecrafted x: failed" in caplog.text
    assert "osascript degradation" in caplog.text
    assert calls[0][:2] == ["/usr/bin/osascript", "-e"]
    assert calls[0][3:] == [
        "--",
        "Vibecrafted x: failed",
        'run-x · r4 · bad "proof"\nnow',
    ]
    logged = (tmp_path / "control_plane" / "events.jsonl").read_text(encoding="utf-8")
    assert '"kind":"spawn-update"' in logged
    assert '"state":"settled"' in logged
    assert '"run_id":"run-x"' in logged


def test_notify_operator_skips_osascript_when_native_app_alive(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    notification = notification_for(
        SettlementRevision(
            run_id="run-live",
            revision=1,
            verdict="pass",
            tui="f",
            reason="done",
            source="trust",
            settled_at="2026-08-19T06:00:00+00:00",
        )
    )
    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with caplog.at_level(logging.INFO, logger="vibecrafted_core.guardian"):
        notify_operator(
            notification,
            platform="darwin",
            which=lambda _name: "/usr/bin/osascript",
            runner=runner,
            app_alive=lambda: True,
            home=tmp_path,
        )

    assert calls == []
    assert "skipping osascript" in caplog.text
    assert (tmp_path / "control_plane" / "events.jsonl").is_file()


def test_native_app_is_alive_requires_live_vibecrafted_pid(tmp_path: Path) -> None:
    assert NATIVE_APP_BUNDLE_ID == "io.vetcoders.vibecrafted"
    assert native_app_is_alive(home=tmp_path) is False
    pid_path = tmp_path / "control_plane" / "native_app.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text(f"{os.getpid()}\n{NATIVE_APP_BUNDLE_ID}\n", encoding="utf-8")
    assert (
        native_app_is_alive(
            home=tmp_path,
            comm_reader=lambda _pid: "Vibecrafted",
        )
        is True
    )
    assert (
        native_app_is_alive(
            home=tmp_path,
            comm_reader=lambda _pid: "Script Editor",
        )
        is False
    )
    pid_path.write_text(f"{os.getpid()}\ncom.apple.ScriptEditor2\n", encoding="utf-8")
    assert (
        native_app_is_alive(
            home=tmp_path,
            comm_reader=lambda _pid: "Vibecrafted",
        )
        is False
    )
    pid_path.write_text(f"99999999\n{NATIVE_APP_BUNDLE_ID}\n", encoding="utf-8")
    assert (
        native_app_is_alive(
            home=tmp_path,
            comm_reader=lambda _pid: "Vibecrafted",
        )
        is False
    )


def test_invalid_server_origin_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="origin"):
        GuardianWorker(
            server_url="http://127.0.0.1:3024/api/control/events",
            state=GuardianState(path=tmp_path / "state.json"),
        )


def test_iter_sse_keeps_opaque_wire_data_behind_explicit_parsers() -> None:
    named = list(
        iter_sse(
            [
                "event: control.v2\n",
                "id: opaque-7\n",
                "data: payload\n",
                "\n",
            ]
        )
    )
    assert named == [
        SSEControlFrame(
            event="control.v2",
            raw_cursor="opaque-7",
            data="payload",
        )
    ]

    assert list(iter_sse(["id: opaque-8\n", "data: payload\n", "\n"])) == []
    assert list(
        iter_sse(
            ["id: opaque-8\n", "data: payload\n", "\n"],
            cursor_parser=lambda raw: f"v2:{raw}",
        )
    ) == [SSEFrame(cursor="v2:opaque-8", data="payload")]

    parsed_control = list(
        iter_sse(
            [
                "event: control.v2\n",
                "id: opaque-9\n",
                "data: payload\n",
                "\n",
            ],
            control_parser=lambda control: SSEFrame(
                cursor=f"control:{control.raw_cursor}",
                data=control.data,
            ),
        )
    )
    assert parsed_control == [SSEFrame(cursor="control:opaque-9", data="payload")]


def test_iter_sse_rejects_oversized_lines_and_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as scoped:
        scoped.setattr(guardian_module, "MAX_SSE_LINE_BYTES", 8)
        with pytest.raises(GuardianProtocolError, match="line exceeds"):
            list(iter_sse(["data: payload\n"]))

    with monkeypatch.context() as scoped:
        scoped.setattr(guardian_module, "MAX_SSE_LINE_BYTES", 32)
        scoped.setattr(guardian_module, "MAX_SSE_FRAME_BYTES", 12)
        with pytest.raises(GuardianProtocolError, match="frame exceeds"):
            list(iter_sse(["id: 1\n", "data: x\n", "\n"]))


def test_protocol_requires_exact_base_media_types(tmp_path: Path) -> None:
    bad_sse = FakeResponse([], content_type="text/event-streaming")
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=GuardianState(path=tmp_path / "sse-state.json"),
        opener=QueueOpener(bad_sse),
    )
    with pytest.raises(GuardianProtocolError, match="content type"):
        worker.consume_connection()

    event = settlement_event(
        "run-json-media",
        1,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    adapter = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=QueueOpener(
            FakeJSONResponse(
                resumable_projection(event, tmp_path),
                content_type="application/jsonp",
            )
        ),
    )
    with pytest.raises(GuardianProtocolError, match="content type"):
        adapter.reconcile(event)


def test_retryable_pending_does_not_starve_f_x_and_notifies_once(
    tmp_path: Path,
) -> None:
    state = GuardianState(
        path=tmp_path / "state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    pending_event = settlement_event(
        "run-n",
        1,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    assert state.claim(v2_cursor(10), pending_event)
    notifications: list[tuple[str, int]] = []
    resume_keys: list[str] = []
    resume_results = [
        action_result(
            accepted=False,
            retryable=True,
            terminal=False,
            reason="provider_temporarily_unavailable",
        ),
        action_result(
            accepted=True,
            retryable=False,
            terminal=True,
            reason="accepted",
        ),
    ]
    now = [0.0]

    def reconcile(event: SettlementRevision) -> ReconcileDecision:
        return ReconcileDecision(
            request_resume=event.tui == "n",
            reason=f"{event.tui}_observed",
        )

    def resume(_event: SettlementRevision, key: str) -> Mapping[str, object]:
        resume_keys.append(key)
        return resume_results.pop(0)

    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda notice: notifications.append(notice.event.key),
        reconciler=reconcile,
        resume=resume,
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(v2_cursor(10)),
                    *frame(v2_cursor(20), settlement_data("run-f", 1, "f")),
                    *frame(v2_cursor(30), settlement_data("run-x", 1, "x")),
                ]
            ),
            FakeResponse(caught_up(v2_cursor(30))),
        ),
        clock=lambda: now[0],
        pending_pass_limit=1,
    )

    first = worker.consume_connection()

    assert first.frames == 2
    assert first.completed_actions == 2
    assert first.action_failures == 1
    assert notifications == [("run-n", 1), ("run-f", 1), ("run-x", 1)]
    assert set(state.pending) == {("run-n", 1)}
    assert state.pending[("run-n", 1)].notification_done is True
    assert state.processed == [("run-f", 1), ("run-x", 1)]

    now[0] = 2.0
    second = worker.consume_connection()

    assert second.completed_actions == 1
    assert state.pending == {}
    assert notifications == [("run-n", 1), ("run-f", 1), ("run-x", 1)]
    assert resume_keys == [
        "settlement:run-n:1",
        "settlement:run-n:1",
    ]


def test_terminal_resume_rejection_completes_without_retry(tmp_path: Path) -> None:
    resume_calls: list[str] = []
    state = GuardianState(
        path=tmp_path / "state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda _notice: None,
        reconciler=lambda _event: ReconcileDecision(request_resume=True),
        resume=lambda _event, key: (
            resume_calls.append(key)
            or action_result(
                accepted=False,
                retryable=False,
                terminal=True,
                reason="expected_agent_session_mismatch",
            )
        ),
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(v2_cursor(0)),
                    *frame(
                        v2_cursor(10),
                        settlement_data(
                            "run-terminal",
                            1,
                            "n",
                            source="trust",
                            repo_root=tmp_path,
                        ),
                    ),
                ]
            )
        ),
    )

    stats = worker.consume_connection()

    assert stats.completed_actions == 1
    assert stats.action_failures == 0
    assert state.pending == {}
    assert state.highwater["run-terminal"] == CompletionRecord(
        revision=1,
        outcome="terminal",
        reason="expected_agent_session_mismatch",
    )
    assert resume_calls == ["settlement:run-terminal:1"]


def test_invalid_action_result_exhausts_bounded_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    notifications: list[tuple[str, int]] = []
    state = GuardianState(
        path=tmp_path / "state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda notice: notifications.append(notice.event.key),
        reconciler=lambda _event: ReconcileDecision(request_resume=True),
        resume=lambda _event, _key: {"accepted": False},
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(v2_cursor(0)),
                    *frame(
                        v2_cursor(10),
                        settlement_data(
                            "run-invalid",
                            1,
                            "n",
                            source="trust",
                            repo_root=tmp_path,
                        ),
                    ),
                ]
            ),
            FakeResponse(caught_up(v2_cursor(10))),
        ),
        clock=lambda: now[0],
    )

    with monkeypatch.context() as scoped:
        scoped.setattr(guardian_module, "MAX_PENDING_ATTEMPTS", 2)
        first = worker.consume_connection()
        now[0] = 2.0
        second = worker.consume_connection()

    assert first.action_failures == 1
    assert second.completed_actions == 1
    assert state.pending == {}
    assert state.highwater["run-invalid"].outcome == "retry_exhausted"
    assert state.highwater["run-invalid"].reason.endswith("invalid_resume_result")
    assert notifications == [("run-invalid", 1)]


def test_notifier_failure_is_durable_and_not_silently_completed(
    tmp_path: Path,
) -> None:
    now = [0.0]
    calls = [0]
    state = GuardianState(
        path=tmp_path / "state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )

    def notifier(_notice: GuardianNotification) -> None:
        calls[0] += 1
        if calls[0] == 1:
            raise OSError("notification bus down")

    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=notifier,
        opener=QueueOpener(
            FakeResponse(
                [*caught_up(0), *frame(10, settlement_data("run-notify", 1, "f"))]
            ),
            FakeResponse(caught_up(10)),
        ),
        clock=lambda: now[0],
    )

    first = worker.consume_connection()
    assert first.action_failures == 1
    assert state.pending[("run-notify", 1)].notification_done is False
    assert state.processed == []

    now[0] = 2.0
    second = worker.consume_connection()
    assert second.completed_actions == 1
    assert calls == [2]
    assert state.pending == {}
    assert state.processed == [("run-notify", 1)]


def test_v2_checksum_backup_and_private_modes(tmp_path: Path) -> None:
    path = tmp_path / "guardian" / "state.json"
    state = GuardianState(
        path=path,
        cursor=17,
        baseline_complete=True,
        highwater={
            "run-a": CompletionRecord(
                revision=3,
                outcome="terminal",
                reason="done",
            )
        },
    )
    state.persist()

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.backup_path.stat().st_mode) == 0o600
    document = json.loads(path.read_text(encoding="utf-8"))
    checksum = document.pop("checksum")
    assert (
        checksum
        == guardian_module.hashlib.sha256(
            guardian_module._canonical_json(document)
        ).hexdigest()
    )

    path.write_bytes(path.read_bytes().replace(b'"cursor":17', b'"cursor":18'))
    recovered = GuardianState.load(path)
    assert recovered.recovered_from_backup is True
    assert recovered.cursor == 17
    assert recovered.processed == [("run-a", 3)]


def test_newer_backup_generation_wins_after_primary_write_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "guardian" / "state.json"
    state = GuardianState(
        path=path,
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    state.persist()
    assert state.state_generation == 1
    event = settlement_event(
        "crash-window",
        1,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    real_write = guardian_module._atomic_private_write
    calls = [0]

    def crash_before_primary(target: Path, payload: bytes) -> None:
        calls[0] += 1
        if calls[0] == 2:
            raise OSError("simulated crash before primary replacement")
        real_write(target, payload)

    with monkeypatch.context() as scoped:
        scoped.setattr(
            guardian_module,
            "_atomic_private_write",
            crash_before_primary,
        )
        with pytest.raises(OSError, match="simulated crash"):
            state.claim(v2_cursor(10), event)

    primary = json.loads(path.read_text(encoding="utf-8"))
    backup = json.loads(state.backup_path.read_text(encoding="utf-8"))
    assert primary["state_generation"] == 1
    assert backup["state_generation"] == 2
    assert state.state_generation == 2
    assert list(state.pending) == [event.key]
    crash_recovered = GuardianState.load(path)
    assert crash_recovered.recovered_from_backup is True
    assert crash_recovered.state_generation == 2
    assert list(crash_recovered.pending) == [event.key]

    state.checkpoint(v2_cursor(20))

    recovered = GuardianState.load(path)
    assert recovered.recovered_from_backup is False
    assert recovered.state_generation == 3
    assert recovered.cursor == v2_cursor(20)
    assert list(recovered.pending) == [event.key]


def test_equal_generation_divergence_enters_safe_fresh_baseline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "guardian" / "state.json"
    common: dict[str, object] = {
        "schema": guardian_module.GUARDIAN_STATE_SCHEMA,
        "state_generation": 7,
        "baseline_complete": True,
        "highwater": [],
        "pending": [],
    }
    write_checked_v2(path, {**common, "cursor": v2_cursor(10)})
    write_checked_v2(
        path.with_name(f"{path.name}.bak"),
        {**common, "cursor": v2_cursor(20)},
    )

    recovered = GuardianState.load(path)

    assert recovered.degraded is True
    assert recovered.baseline_complete is False
    assert recovered.cursor == 0
    assert recovered.pending == {}


def test_semantically_invalid_checked_state_enters_fresh_safe_baseline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "guardian" / "state.json"
    write_checked_v2(
        path,
        {
            "schema": guardian_module.GUARDIAN_STATE_SCHEMA,
            "cursor": 99,
            "baseline_complete": True,
            "highwater": [
                {
                    "run_id": "must-not-survive",
                    "settlement_revision": 4,
                    "outcome": "terminal",
                    "reason": "old",
                }
            ],
            "pending": [{"malformed": True}],
        },
    )

    state = GuardianState.load(path)
    assert state.degraded is True
    notifications: list[GuardianNotification] = []
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=notifications.append,
        opener=QueueOpener(
            FakeResponse(
                [
                    *boundary(v2_cursor(0)),
                    *frame(
                        v2_cursor(10),
                        settlement_data("historical", 1, "x"),
                    ),
                    *caught_up(v2_cursor(10)),
                ]
            )
        ),
    )

    stats = worker.consume_connection()

    assert state.degraded is False
    assert stats.completed_baseline is True
    assert notifications == []
    assert "must-not-survive" not in state.highwater
    assert state.processed == [("historical", 1)]


def test_v1_migration_suppresses_unbound_pending_and_rebaselines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "guardian" / "state.json"
    legacy_pending = settlement_event("legacy-run", 2, "n")
    legacy_pending_payload = legacy_pending.to_state_payload()
    legacy_pending_payload.pop("receipt_id")
    path.parent.mkdir(mode=0o700, parents=True)
    guardian_module._atomic_private_write(
        path,
        json.dumps(
            {
                "schema": guardian_module.GUARDIAN_STATE_SCHEMA_V1,
                "cursor": 800,
                "baseline_complete": True,
                "processed": [
                    {
                        "run_id": "legacy-run",
                        "settlement_revision": 1,
                    }
                ],
                "pending": [legacy_pending_payload],
            }
        ).encode("utf-8"),
    )

    migrated = GuardianState.load(path)

    assert migrated.cursor == 0
    assert migrated.baseline_complete is False
    assert migrated.pending == {}
    assert migrated.highwater["legacy-run"] == CompletionRecord(
        revision=2,
        outcome="legacy_authority_unbound",
        reason="legacy_authority_unbound",
    )
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == (
        guardian_module.GUARDIAN_STATE_SCHEMA
    )


def test_pending_and_state_size_caps_fail_without_partial_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = GuardianState(
        path=tmp_path / "state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    first = settlement_event("run-one", 1, "n")
    second = settlement_event("run-two", 1, "n")

    with monkeypatch.context() as scoped:
        scoped.setattr(guardian_module, "MAX_PENDING_RECORDS", 1)
        assert state.claim(10, first)
        with pytest.raises(GuardianStateLimitError, match="outbox is full"):
            state.claim(20, second)
    assert set(state.pending) == {first.key}
    assert state.cursor == 10

    oversized = GuardianState(path=tmp_path / "small-state.json")
    with monkeypatch.context() as scoped:
        scoped.setattr(guardian_module, "MAX_STATE_BYTES", 100)
        with pytest.raises(GuardianStateLimitError, match="state exceeds"):
            oversized.persist()
    assert not oversized.path.exists()


def test_dead_letters_are_one_bounded_aggregate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = GuardianState(
        path=tmp_path / "guardian" / "state.json",
        baseline_complete=True,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(guardian_module, "MAX_DEAD_LETTER_ENTRIES", 2)
        scoped.setattr(guardian_module, "MAX_DEAD_LETTER_DATA_BYTES", 8)
        for cursor in range(3):
            assert state.quarantine(cursor, f"invalid-{cursor}-payload")

    target = state.path.parent / "dead_letters.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert len(payload["entries"]) == 2
    assert all(entry["truncated"] is True for entry in payload["entries"])
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_single_instance_lock_rejects_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "guardian"
    directory.mkdir(mode=0o700)
    target = tmp_path / "operator-owned"
    target.write_text("unchanged", encoding="utf-8")
    lock = directory / "guardian.lock"
    lock.symlink_to(target)

    with (
        pytest.raises(GuardianLockSecurityError, match="symlink"),
        single_instance_lock(lock),
    ):
        pytest.fail("symlink lock must never be acquired")

    assert target.read_text(encoding="utf-8") == "unchanged"


def test_single_instance_lock_rejects_inode_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = tmp_path / "guardian" / "guardian.lock"
    displaced = lock.with_suffix(".displaced")
    original_flock = guardian_module.fcntl.flock
    swapped = [False]

    def swapping_flock(descriptor: int, operation: int) -> object:
        if operation & guardian_module.fcntl.LOCK_EX and not swapped[0]:
            swapped[0] = True
            os.replace(lock, displaced)
            replacement = os.open(
                lock,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.close(replacement)
        return original_flock(descriptor, operation)

    monkeypatch.setattr(guardian_module.fcntl, "flock", swapping_flock)

    with (
        pytest.raises(GuardianLockSecurityError, match="opened inode"),
        single_instance_lock(lock),
    ):
        pytest.fail("swapped lock must never be yielded")


def test_same_agent_wrong_session_is_terminal_and_never_launches(
    tmp_path: Path,
) -> None:
    event = settlement_event(
        "run-session-swap",
        5,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    projection = resumable_projection(event, tmp_path)
    launched: list[str] = []

    def native_resume(
        run_id: str,
        source_dir: str | Path,
        *,
        expected_agent: str,
        expected_agent_session_id: str,
        expected_settlement_revision: int,
        expected_receipt_id: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        assert run_id == event.run_id
        assert source_dir == tmp_path
        assert expected_agent == "codex"
        assert expected_agent_session_id == "019-native-session"
        assert expected_settlement_revision == 5
        assert expected_receipt_id == event.receipt_id
        assert idempotency_key == event.idempotency_key
        current_same_agent_session = "019-different-session"
        if expected_agent_session_id != current_same_agent_session:
            return action_result(
                accepted=False,
                retryable=False,
                terminal=True,
                reason="expected_agent_session_mismatch",
            )
        launched.append(run_id)
        return action_result(
            accepted=True,
            retryable=False,
            terminal=True,
            reason="accepted",
        )

    recovery = GuardianRecoveryAdapter(
        server_url="http://127.0.0.1:3024",
        opener=QueueOpener(FakeJSONResponse(projection)),
        guard_enforcer=lambda **_kwargs: SimpleNamespace(allowed=True),
        native_resumer=native_resume,
    )
    state = GuardianState(
        path=tmp_path / "state.json",
        cursor=v2_cursor(0),
        baseline_complete=True,
    )
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda _notice: None,
        reconciler=recovery.reconcile,
        resume=recovery.resume,
        opener=QueueOpener(
            FakeResponse(
                [
                    *caught_up(v2_cursor(0)),
                    *frame(
                        v2_cursor(50),
                        settlement_data(
                            event.run_id,
                            event.revision,
                            event.tui,
                            source=event.source,
                            repo_root=tmp_path,
                        ),
                    ),
                ]
            )
        ),
    )

    stats = worker.consume_connection()

    assert stats.completed_actions == 1
    assert launched == []
    assert state.pending == {}
    assert state.highwater[event.run_id].reason == "expected_agent_session_mismatch"


def test_heartbeat_cannot_open_a_fresh_v2_baseline(tmp_path: Path) -> None:
    notifications: list[GuardianNotification] = []
    state = GuardianState(path=tmp_path / "state.json")
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=notifications.append,
        opener=QueueOpener(
            FakeResponse(
                [
                    *boundary(v2_cursor(0)),
                    *frame(
                        v2_cursor(10),
                        settlement_data("historical", 1, "x"),
                    ),
                    *heartbeat(),
                ]
            )
        ),
    )

    stats = worker.consume_connection()

    assert stats.heartbeats == 1
    assert stats.completed_baseline is False
    assert state.baseline_complete is False
    assert notifications == []


def test_busy_stream_acts_immediately_after_caught_up_without_heartbeat(
    tmp_path: Path,
) -> None:
    notifications: list[tuple[str, int]] = []
    resumed: list[str] = []
    state = GuardianState(path=tmp_path / "state.json")
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda notice: notifications.append(notice.event.key),
        reconciler=lambda _event: ReconcileDecision(request_resume=True),
        resume=lambda _event, key: (
            resumed.append(key)
            or action_result(
                accepted=True,
                retryable=False,
                terminal=True,
                reason="accepted",
            )
        ),
        opener=QueueOpener(
            FakeResponse(
                [
                    *boundary(v2_cursor(0)),
                    *frame(
                        v2_cursor(10),
                        settlement_data("historical", 1, "n"),
                    ),
                    *caught_up(v2_cursor(10)),
                    *frame(
                        v2_cursor(20),
                        settlement_data(
                            "live",
                            1,
                            "n",
                            source="trust",
                            repo_root=tmp_path,
                        ),
                    ),
                ]
            )
        ),
    )

    stats = worker.consume_connection()

    assert stats.heartbeats == 0
    assert stats.completed_baseline is True
    assert stats.completed_actions == 1
    assert notifications == [("live", 1)]
    assert resumed == ["settlement:live:1"]
    assert state.baseline_complete is True
    assert state.degraded is False


def test_stream_gap_revokes_pending_authority_until_fresh_caught_up(
    tmp_path: Path,
) -> None:
    old_cursor = v2_cursor(50)
    resumed_at = v2_cursor(100, generation=2)
    pending_event = settlement_event(
        "pending-before-gap",
        1,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    state = GuardianState(
        path=tmp_path / "state.json",
        cursor=old_cursor,
        baseline_complete=True,
    )
    assert state.claim(v2_cursor(60), pending_event)
    assert (
        state.retry(
            pending_event.key,
            reason="deferred",
            now=0.0,
            bounded=True,
        )
        is False
    )
    resume_calls: list[str] = []
    notifications: list[tuple[str, int]] = []
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda notice: notifications.append(notice.event.key),
        reconciler=lambda _event: ReconcileDecision(request_resume=True),
        resume=lambda _event, key: (
            resume_calls.append(key)
            or action_result(
                accepted=True,
                retryable=False,
                terminal=True,
                reason="accepted",
            )
        ),
        opener=QueueOpener(
            FakeResponse(
                [
                    *boundary(old_cursor),
                    *stream_gap(requested=old_cursor, resumed_at=resumed_at),
                ]
            ),
            FakeResponse(
                [
                    *boundary(resumed_at),
                    *caught_up(resumed_at),
                    *frame(
                        v2_cursor(120, generation=2),
                        settlement_data(
                            "live-after-gap",
                            1,
                            "n",
                            source="trust",
                            repo_root=tmp_path,
                        ),
                    ),
                ]
            ),
        ),
        clock=lambda: 0.0,
    )

    first = worker.consume_connection()

    assert first.completed_baseline is False
    assert state.degraded is True
    assert state.baseline_complete is False
    assert state.pending[pending_event.key].resume_authorized is False
    assert resume_calls == []

    second = worker.consume_connection()

    assert second.completed_baseline is True
    assert second.completed_actions == 2
    assert state.degraded is False
    assert state.baseline_complete is True
    assert notifications == [
        ("pending-before-gap", 1),
        ("live-after-gap", 1),
    ]
    assert resume_calls == ["settlement:live-after-gap:1"]
    assert state.highwater["pending-before-gap"].reason == "legacy_notification_only"


def test_due_pending_waits_for_current_stream_barrier_before_gap(
    tmp_path: Path,
) -> None:
    old_cursor = v2_cursor(50)
    resumed_at = v2_cursor(100, generation=2)
    event = settlement_event(
        "due-before-gap",
        1,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    state = GuardianState(
        path=tmp_path / "state.json",
        cursor=old_cursor,
        baseline_complete=True,
    )
    assert state.claim(v2_cursor(60), event)
    order: list[str] = []

    response = FakeResponse(
        [
            *boundary(old_cursor),
            *stream_gap(requested=old_cursor, resumed_at=resumed_at),
        ]
    )

    def open_stream(_request: Any, *, timeout: float) -> FakeResponse:
        assert timeout > 0
        order.append("open_sse")
        return response

    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda _notice: order.append("notify"),
        reconciler=lambda _event: (
            order.append("reconcile") or ReconcileDecision(request_resume=True)
        ),
        resume=lambda _event, _key: (
            order.append("resume")
            or action_result(
                accepted=True,
                retryable=False,
                terminal=True,
                reason="accepted",
            )
        ),
        opener=open_stream,
        clock=lambda: 0.0,
    )

    worker.consume_connection()

    assert order == ["open_sse"]
    assert state.baseline_complete is False
    assert state.degraded is True
    assert state.pending[event.key].resume_authorized is False


def test_gap_write_failure_poison_is_live_before_fresh_caught_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_cursor = v2_cursor(50)
    resumed_at = v2_cursor(100, generation=2)
    event = settlement_event(
        "gap-write-failure",
        1,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    state = GuardianState(
        path=tmp_path / "state.json",
        cursor=old_cursor,
        baseline_complete=True,
    )
    assert state.claim(v2_cursor(60), event)
    real_write = guardian_module._atomic_private_write
    calls = [0]

    def fail_first_write(target: Path, payload: bytes) -> None:
        calls[0] += 1
        if calls[0] == 1:
            raise OSError("gap state unavailable")
        real_write(target, payload)

    resume_calls: list[str] = []
    notifications: list[tuple[str, int]] = []
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda notice: notifications.append(notice.event.key),
        reconciler=lambda _event: ReconcileDecision(request_resume=True),
        resume=lambda _event, key: (
            resume_calls.append(key)
            or action_result(
                accepted=True,
                retryable=False,
                terminal=True,
                reason="accepted",
            )
        ),
        opener=QueueOpener(
            FakeResponse(
                [
                    *boundary(old_cursor),
                    *stream_gap(requested=old_cursor, resumed_at=resumed_at),
                    *caught_up(resumed_at),
                ]
            )
        ),
        clock=lambda: 0.0,
    )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            guardian_module,
            "_atomic_private_write",
            fail_first_write,
        )
        worker.consume_connection()

    assert calls[0] > 1
    assert resume_calls == []
    assert notifications == [event.key]
    assert state.pending == {}
    assert state.highwater[event.run_id].reason == "legacy_notification_only"


def test_boundary_disconnect_cannot_advance_authoritative_pending_cursor(
    tmp_path: Path,
) -> None:
    old_cursor = v2_cursor(50)
    next_generation = v2_cursor(0, generation=2)
    event = settlement_event(
        "pending-at-boundary",
        1,
        "n",
        source="trust",
        repo_root=tmp_path,
    )
    state = GuardianState(
        path=tmp_path / "state.json",
        cursor=old_cursor,
        baseline_complete=True,
    )
    assert state.claim(v2_cursor(60), event)
    notifications: list[GuardianNotification] = []
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=notifications.append,
        reconciler=lambda _event: ReconcileDecision(request_resume=True),
        resume=lambda _event, _key: pytest.fail("resume crossed an unproved boundary"),
        opener=QueueOpener(
            FakeResponse(
                boundary(
                    state.cursor,
                    next_generation,
                    reason="generation_change",
                )
            )
        ),
        clock=lambda: 0.0,
    )

    worker.consume_connection()

    assert state.cursor == v2_cursor(60)
    assert state.baseline_complete is True
    assert state.pending[event.key].resume_authorized is True
    assert notifications == []


def test_numeric_legacy_stream_is_notification_only_after_caught_up(
    tmp_path: Path,
) -> None:
    notifications: list[tuple[str, int]] = []
    reconciled: list[tuple[str, int]] = []
    resumed: list[str] = []
    triaged: list[str] = []

    def schedule_triage(run_id: str) -> bool:
        triaged.append(run_id)
        return True

    state = GuardianState(path=tmp_path / "state.json")
    worker = GuardianWorker(
        server_url="http://127.0.0.1:3024",
        state=state,
        notifier=lambda notice: notifications.append(notice.event.key),
        reconciler=lambda event: (
            reconciled.append(event.key) or ReconcileDecision(request_resume=True)
        ),
        resume=lambda _event, key: (
            resumed.append(key)
            or action_result(
                accepted=True,
                retryable=False,
                terminal=True,
                reason="accepted",
            )
        ),
        triage_scheduler=schedule_triage,
        opener=QueueOpener(
            FakeResponse(
                [
                    *boundary(0),
                    *frame(10, settlement_data("legacy-history", 1, "n")),
                    *caught_up(10),
                    *frame(20, settlement_data("legacy-live", 1, "n")),
                ]
            )
        ),
    )

    stats = worker.consume_connection()

    assert stats.completed_baseline is True
    assert stats.completed_actions == 1
    assert notifications == [("legacy-live", 1)]
    assert reconciled == []
    assert resumed == []
    assert triaged == ["legacy-history", "legacy-live"]
    assert state.cursor == 20
    assert state.baseline_complete is True
    assert state.degraded is True
    assert state.highwater["legacy-live"].reason == "legacy_notification_only"


def test_parser_resolves_server_url_from_typed_config_and_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "vibecrafted" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "[server]\n"
        'bind_host = "100.82.232.70"\n'
        "port = 3025\n"
        'public_url = "http://100.82.232.70:3025"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("VC_SERVER_URL", raising=False)
    monkeypatch.delenv("VIBECRAFTED_SERVER_URL", raising=False)

    assert (
        guardian_module.build_parser().parse_args([]).server_url
        == "http://100.82.232.70:3025"
    )

    monkeypatch.setenv("VC_SERVER_URL", "http://override.example:9")
    assert (
        guardian_module.build_parser().parse_args([]).server_url
        == "http://override.example:9"
    )


def test_main_recovers_trust_outbox_under_lock_before_sse_attach(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    order: list[str] = []

    @contextmanager
    def fake_lock(_path: Path) -> Iterator[None]:
        order.append("lock")
        yield

    class StubWorker:
        server_url = "http://127.0.0.1:3024"

        def run_forever(self, *, backoff: object) -> None:
            assert backoff is not None
            order.append("attach")

    class StubSettlementBoardPublisher:
        def __init__(self, *, server_url: str) -> None:
            assert server_url == "http://127.0.0.1:3024"

        def request_refresh(self) -> bool:
            return True

        def start_periodic_refresh(self) -> bool:
            order.append("history-start")
            return True

        def stop_periodic_refresh(self) -> bool:
            order.append("history-stop")
            return True

    def schedule_triage_startup() -> bool:
        order.append("triage-startup")
        return True

    monkeypatch.setattr(
        guardian_module,
        "GuardianState",
        SimpleNamespace(load=lambda _path: object()),
    )
    monkeypatch.setattr(
        guardian_module,
        "GuardianRecoveryAdapter",
        lambda **_kwargs: SimpleNamespace(
            reconcile=lambda _event: None,
            resume=lambda _event, _key: None,
        ),
    )
    monkeypatch.setattr(
        guardian_module,
        "GuardianWorker",
        lambda **_kwargs: StubWorker(),
    )
    monkeypatch.setattr(
        guardian_module,
        "BoundedBackoff",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        guardian_module,
        "SettlementBoardPublisher",
        StubSettlementBoardPublisher,
    )
    monkeypatch.setattr(guardian_module, "single_instance_lock", fake_lock)
    monkeypatch.setattr(
        guardian_module,
        "_recover_pending_trust_before_attach",
        lambda: order.append("recover"),
    )
    monkeypatch.setattr(
        guardian_module,
        "_schedule_triage_startup_sweep",
        schedule_triage_startup,
    )

    result = guardian_module.main(
        [
            "--server-url",
            "http://127.0.0.1:3024",
            "--state",
            str(tmp_path / "state.json"),
            "--lock",
            str(tmp_path / "guardian.lock"),
            "--no-desktop",
        ]
    )

    assert result == 0
    assert order == [
        "lock",
        "recover",
        "triage-startup",
        "history-start",
        "attach",
        "history-stop",
    ]


def test_trust_recovery_sweep_reports_success_and_preserved_failures(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from vibecrafted_core import trust as trust_module

    report = SimpleNamespace(
        scanned=2,
        recovered=(
            SimpleNamespace(
                run_id="run-recovered",
                settlement_revision=4,
                receipt_id="a" * 64,
            ),
        ),
        errors=(
            SimpleNamespace(
                run_id="run-corrupt",
                outbox_path="/private/outbox.json",
                error_type="ValueError",
                message="outbox remains durable",
            ),
        ),
        truncated=True,
        ok=False,
    )
    monkeypatch.setattr(
        trust_module,
        "recover_pending_trust_settlements",
        lambda: report,
    )

    with caplog.at_level(logging.INFO, logger="vibecrafted_core.guardian"):
        guardian_module._recover_pending_trust_before_attach()

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "recovered pending trust settlement run-recovered r4" in m for m in messages
    )
    assert any(
        "pending trust settlement recovery failed for run-corrupt" in m
        for m in messages
    )
    assert any("recovery hit its bounded limit after 2 outboxes" in m for m in messages)


def test_terminal_triage_startup_uses_current_reconciliation_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from vibecrafted_core import run_triage

    control_plane = tmp_path / "control_plane"
    observed: list[tuple[Path, int, int]] = []
    report = SimpleNamespace(
        scanned=5,
        attempted=2,
        errors=(
            SimpleNamespace(
                meta_path="/runtime_runs/broken/meta.json",
                reason="proof missing",
            ),
        ),
        truncated=True,
    )

    def reconcile(
        root: Path,
        *,
        attempt_limit: int,
        scan_limit: int,
    ) -> object:
        observed.append((root, attempt_limit, scan_limit))
        return report

    monkeypatch.setattr(
        guardian_module,
        "_recover_terminal_triage_outbox",
        lambda: None,
    )
    monkeypatch.setattr(
        guardian_module,
        "vibecrafted_home",
        lambda: tmp_path,
    )
    monkeypatch.setattr(run_triage, "reconcile_untriaged_runs", reconcile)

    with caplog.at_level(logging.INFO, logger="vibecrafted_core.guardian"):
        guardian_module._recover_untriaged_runs_background()

    assert observed == [
        (
            control_plane,
            guardian_module.TERMINAL_TRIAGE_OUTBOX_ATTEMPT_LIMIT,
            guardian_module.TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT,
        )
    ]
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "terminal triage recovery failed for "
        "/runtime_runs/broken/meta.json: proof missing" in message
        for message in messages
    )
    assert any(
        "terminal triage recovery complete: scanned=5 attempted=2 "
        "errors=1 truncated=True" in message
        for message in messages
    )
