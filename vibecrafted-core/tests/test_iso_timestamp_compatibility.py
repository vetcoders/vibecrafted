"""Timestamp compatibility at lifecycle, session selection, and telemetry boundaries."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from vibecrafted_core import (
    control_plane,
    harness_usage,
    repository_claims,
    run_board,
    spawn,
    workflow,
)

INSTANT = datetime(2026, 10, 9, 10, tzinfo=UTC)
CASES = [
    pytest.param("2026-10-09T10:00:00Z", INSTANT, id="z"),
    pytest.param(
        "2026-10-09T12:00:00+02:00",
        datetime(2026, 10, 9, 12, tzinfo=timezone(timedelta(hours=2))),
        id="positive-offset",
    ),
    pytest.param(
        "2026-10-09T04:30:00-05:30",
        datetime(2026, 10, 9, 4, 30, tzinfo=timezone(-timedelta(hours=5, minutes=30))),
        id="negative-offset",
    ),
    pytest.param("2026-10-09T10:00:00", datetime(2026, 10, 9, 10), id="naive"),
    pytest.param("2026-10-09Z", datetime(2026, 10, 9), id="legacy-date-only-z"),
    pytest.param("2026-10-09Z10:00:00Z", None, id="embedded-z-separator"),
    pytest.param("2026-10-09T10:00:00ZZ", None, id="duplicate-z"),
    pytest.param("2026-10-09T10:00:00+02:00Z", None, id="offset-and-z"),
    pytest.param("not-a-timestamp", None, id="malformed"),
    pytest.param("", None, id="empty"),
    pytest.param(" 2026-10-09T10:00:00Z ", None, id="whitespace"),
]


@pytest.mark.parametrize("raw,expected", CASES)
@pytest.mark.parametrize(
    "parser,naive_policy,strips",
    [
        (control_plane._parse_iso, "preserve", False),
        (repository_claims._parse_time, "local", False),
        (run_board._parse_iso, "utc", True),
        (spawn._parse_dt, "normalize-utc", False),
    ],
    ids=["control-plane", "repository-claims", "run-board", "spawn"],
)
def test_lifecycle_timestamp_compatibility(raw, expected, parser, naive_policy, strips):
    if strips and raw.startswith(" "):
        expected = INSTANT
    if expected is not None:
        if naive_policy in {"utc", "normalize-utc"} and expected.tzinfo is None:
            expected = expected.replace(tzinfo=UTC)
        if naive_policy in {"local", "normalize-utc"}:
            expected = expected.astimezone(UTC)
    actual = parser(raw)
    assert actual == expected
    if actual is not None:
        assert actual.tzinfo == expected.tzinfo


@pytest.mark.parametrize("raw,expected", CASES)
def test_harness_evidence_requires_aware_timestamp(raw, expected):
    epoch = expected.timestamp() if expected and expected.tzinfo is not None else None
    assert harness_usage.epoch(raw) == epoch


@pytest.mark.parametrize("raw,expected", CASES)
def test_last_session_selection_uses_only_valid_aware_timestamps(
    tmp_path, monkeypatch, raw, expected
):
    monkeypatch.setattr(workflow, "control_plane_home", lambda: tmp_path)
    monkeypatch.setattr(workflow, "lookup_run", lambda _: None)
    monkeypatch.setattr(
        workflow,
        "resolve_fork_source",
        lambda agent, *, session, require_native_fork: {
            "accepted": True,
            "agent_session_id": session,
        },
    )
    for name, stamp in [("earlier", "2026-10-09T09:00:00Z"), ("candidate", raw)]:
        path = tmp_path / "runtime_runs" / name / "meta.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "agent": "codex",
                    "agent_session_id": name,
                    "root": str(tmp_path),
                    "started_at": stamp,
                }
            )
        )
    selected = workflow.resolve_session_selection("codex", "last", tmp_path)
    winner = "candidate" if expected and expected.tzinfo is not None else "earlier"
    assert selected["agent_session_id"] == winner


def _monitor(name, monkeypatch):
    # Kimi imports its adjacent usage_archive module. Restore its temporary
    # search path mutation when the test finishes.
    monkeypatch.setattr(sys, "path", list(sys.path))
    path = (
        Path(__file__).resolve().parents[1]
        / "vibecrafted_core"
        / "runtime"
        / "telemetry"
        / f"{name}-monitor"
        / f"{name}_monitor.py"
    )
    spec = importlib.util.spec_from_file_location(f"{name}_timestamp_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("raw,expected", CASES)
def test_kimi_reset_uses_offsets_and_refuses_invalid_or_naive_values(
    monkeypatch, raw, expected
):
    kimi = _monitor("kimi", monkeypatch)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 9, 9, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(kimi, "datetime", FrozenDatetime)
    countdown = "1:00" if expected and expected.tzinfo is not None else "?"
    assert kimi._reset_in(raw) == countdown


@pytest.mark.parametrize("raw,expected", CASES)
def test_agy_transcript_event_timestamp_and_invalid_fallback(
    tmp_path, monkeypatch, raw, expected
):
    agy = _monitor("agy", monkeypatch)
    monkeypatch.setattr(agy.time, "time", lambda: 1234)
    monkeypatch.setattr(agy, "expand", lambda _: tmp_path / "absent-conversation.db")
    monkeypatch.setattr(
        agy,
        "Path",
        lambda value: (
            tmp_path / "cache"
            if str(value).startswith("/tmp/agy_statusline_cache_")
            else Path(value)
        ),
    )
    transcript = (
        tmp_path / "fixture" / "surface" / "sessions" / "one" / "transcript.jsonl"
    )
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps({"type": "PLANNER_RESPONSE", "created_at": raw, "content": ""})
        + "\n"
    )
    result = agy.AgyTranscriptTracker(transcript, "fixture", {}).update()
    expected_millis = (
        int(expected.timestamp() * 1000) if expected else (1_234_000 if raw else 0)
    )
    assert result["last_success_ts"] == expected_millis
