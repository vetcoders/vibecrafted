"""Prose is not telemetry.

A transcript is mostly text an agent wrote: source code, reports, quoted logs.
``Total cost: $0.0000`` printed by a TUI became a provider-reported cost of 0, and
``pub fn model: &str) -> bool`` became the model ``&str)``. A model or a cost now
comes from a structured record, a closed run's measurement, or stays unknown.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from vibecrafted_core import spawn, usage_reporting
from vibecrafted_core.agent_stream import AgentStreamParser
from vibecrafted_core.spawn import _extract_cost, _resolve_model
from vibecrafted_core.telemetry import run_telemetry_from_meta

AGENTS = ("agy", "claude", "codex", "copilot", "cursor", "grok", "junie", "kimi", "omp")

PROSE = (
    "Total cost: $0.0000\n",
    "cost_usd: 0.0\n",
    "cost_usd: $0.045\n",
    "cost: $0.25\n",
    "It came to $12.50 USD\n",
    "pub fn model: &str) -> bool\n",
    "model: &str)\n",
    "model: claude-sonnet-4-5\n",
    "the fallback model: gpt-6.1-sol stays\n",
)

NOT_A_MODEL = (
    "&str)",
    "grok-4.7-build-fastmodel_source:clieffort:xhigh",
    "<synthetic>",
    "codex-cli-default",
    "gemini-cli-default",
    "None",
    "unknown",
    "pending",
)


@pytest.fixture(autouse=True)
def _no_model_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in spawn.MODEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# Live stream parser
# --------------------------------------------------------------------------


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("line", PROSE)
def test_prose_is_never_a_model_or_a_cost(agent: str, line: str) -> None:
    parser = AgentStreamParser(agent)
    parser.feed_line(line.encode())
    assert parser.cost_usd is None
    assert parser.cost_source != "provider_reported"
    assert not parser.model_id


def test_a_line_that_only_looks_like_json_is_prose() -> None:
    parser = AgentStreamParser("agy")
    parser.feed_line(b'{"cost": 5, model: &str) oops\n')
    assert parser.cost_usd is None
    assert not parser.model_id


def test_prose_does_not_overwrite_a_known_model() -> None:
    parser = AgentStreamParser("agy", default_model="agy-pro")
    parser.feed_line(b"model: &str)\n")
    assert parser.model_id == "agy-pro"


@pytest.mark.parametrize(
    "line",
    (
        b"[12:00:00] model: agy-pro\n",
        b"\x1b[36m[12:00:00] model: agy-pro\x1b[0m\n",
    ),
)
def test_a_timestamped_provider_header_names_the_model(line: bytes) -> None:
    parser = AgentStreamParser("agy")
    parser.feed_line(line)
    assert parser.model_id == "agy-pro"


@pytest.mark.parametrize("value", ("claude-opus-5-5[1m]", "provider/some-model"))
def test_a_timestamped_header_keeps_real_labels(value: str) -> None:
    parser = AgentStreamParser("agy")
    parser.feed_line(f"[12:00:00] model: {value}\n".encode())
    assert parser.model_id == value


@pytest.mark.parametrize("value", NOT_A_MODEL)
def test_a_timestamped_header_rejects_what_is_not_a_model(value: str) -> None:
    parser = AgentStreamParser("agy")
    parser.feed_line(b"[12:00:00] model: agy-pro\n")
    parser.feed_line(f"[12:00:01] model: {value}\n".encode())
    assert parser.model_id == "agy-pro"


def test_a_structured_result_is_still_a_provider_cost() -> None:
    parser = AgentStreamParser("claude")
    parser.feed_line(
        b'{"type":"result","total_cost_usd":0.045,'
        b'"modelUsage":{"claude-opus-5-5":{"inputTokens":1}}}\n'
    )
    assert parser.cost_usd == 0.045
    assert parser.cost_source == "provider_reported"
    assert parser.model_id == "claude-opus-5-5"


# --------------------------------------------------------------------------
# Close path: transcript + report text
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        *PROSE,
        # The closure footer this runtime writes into reports, read back.
        "run_closure:\n  cost_usd: 0.03\n",
        'the log said "total_cost_usd": 12.5 somewhere\n',
        'an earlier "cost": 5 was mentioned\n',
        '> {"type":"result","total_cost_usd":3.5}\n',
        # An agent quoting a cost inside its own message.
        '{"type":"assistant","text":"\\"total_cost_usd\\": 99.0"}\n',
    ),
)
def test_extract_cost_ignores_prose(text: str) -> None:
    assert _extract_cost(text) is None


def test_extract_cost_reads_structured_records_and_skips_prose_around_them() -> None:
    transcript = (
        "Total cost: $0.0000\n"
        '{"type":"result","total_cost_usd":0.045}\n'
        "cost_usd: 9.99\n"
    )
    assert _extract_cost(transcript) == 0.045


def test_extract_cost_prefers_the_last_structured_total() -> None:
    transcript = (
        '{"type":"result","total_cost_usd":0.01}\n'
        '{"type":"result","total_cost_usd":0.02}\n'
    )
    assert _extract_cost(transcript) == 0.02


def test_extract_cost_still_sums_per_call_model_usage() -> None:
    transcript = (
        '{"modelUsage":[{"model":"gpt-5.5","cost":0.045821}]}\n'
        '{"modelUsage":[{"model":"gpt-4.1-mini","cost":0.0002904}]}\n'
        "Total cost: $7.00\n"
    )
    assert _extract_cost(transcript) == 0.046111


@pytest.mark.parametrize(
    "text",
    (
        *PROSE,
        'the config says "model": "gpt-9" here\n',
        "---\nrunner: vibecrafted\nmodel: &str)\n",
        "[12:00:00] model: &str)\n",
    ),
)
def test_resolve_model_falls_back_when_text_only_has_prose(text: str) -> None:
    assert _resolve_model({"agent": "codex"}, text) == "codex-cli-default"


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("[12:00:00] model: codex-model\n", "codex-model"),
        (
            "model: &str)\n---\nrunner: vibecrafted\nmodel: gemini-3.1-pro-preview\n",
            "gemini-3.1-pro-preview",
        ),
        ('{"type":"system","model":"claude-opus-5-5"}\n', "claude-opus-5-5"),
        ('{"modelUsage":{"claude-opus-5-5":{"inputTokens":1}}}\n', "claude-opus-5-5"),
    ),
)
def test_resolve_model_reads_structured_sources(text: str, expected: str) -> None:
    assert _resolve_model({"agent": "codex"}, text) == expected


@pytest.mark.parametrize("value", NOT_A_MODEL)
def test_resolve_model_rejects_a_recorded_non_model(value: str) -> None:
    resolved = _resolve_model(
        {"agent": "codex", "model": value}, '{"type":"system","model":"gpt-6.1-sol"}\n'
    )
    assert resolved == "gpt-6.1-sol"


# --------------------------------------------------------------------------
# Closed runs
# --------------------------------------------------------------------------


def _recorded_usage() -> dict[str, object]:
    return {
        "schema": "vibecrafted.usage.v1",
        "unit": "tokens",
        "source": "provider_stream",
        "events": 1,
        "tokens_input": 1,
        "tokens_cached_input": 0,
        "tokens_cache_write": 0,
        "tokens_output": 0,
        "tokens_total": 1,
    }


def _measured_usage() -> dict[str, int]:
    return {
        "input_tokens": 3974,
        "cache_creation_input_tokens": 4802133,
        "cache_read_input_tokens": 439996799,
        "output_tokens": 1428316,
        "total_tokens": 446231222,
        "messages": 1824,
        "foreign_workspace_events": 228,
    }


def _measured_meta(
    *, model: str = "claude-opus-5-5", measured: dict[str, int] | None = None
) -> dict[str, object]:
    """Shape of a real closed run: ``usage`` and ``cost`` null, tokens measured."""
    return {
        "run_id": "measured",
        "agent": "claude",
        "status": "completed",
        "exit_code": 0,
        "completed_at": "2026-11-01T12:00:00Z",
        "model_effective": model,
        "model_requested": model,
        "usage": None,
        "cost": None,
        "measured_usage": measured or _measured_usage(),
        "usage_measurement": {
            "source": "claude-transcript-jsonl-v1",
            "attribution": "fixture",
            "monotonic": True,
        },
    }


def _prose_transcript(tmp_path: Path) -> Path:
    path = tmp_path / "prose-transcript.log"
    path.write_text("".join(PROSE), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("fields", "expected"),
    (
        (
            {
                "model_effective": "claude-opus-5-5",
                "model_requested": "claude-sonnet-5-5",
                "agent_model": "other",
            },
            "claude-opus-5-5",
        ),
        ({"model_requested": "gpt-6.1-sol", "model": "unknown"}, "gpt-6.1-sol"),
        ({"model_effective": "&str)", "model_requested": "gpt-6.1-sol"}, "gpt-6.1-sol"),
        ({"agent_model": "grok-4.7-build-fast"}, "grok-4.7-build-fast"),
        ({"agent_model": "claude-opus-5-5[1m]"}, "claude-opus-5-5[1m]"),
        (
            {"agent_model": "Grok 4.7 256K Extra High Fast"},
            "Grok 4.7 256K Extra High Fast",
        ),
    ),
)
def test_a_closed_run_names_its_model_by_precedence(
    fields: dict[str, str], expected: str
) -> None:
    meta = {
        "run_id": "r",
        "agent": "codex",
        "status": "completed",
        "exit_code": 0,
        "usage": _recorded_usage(),
        **fields,
    }
    assert run_telemetry_from_meta(meta, transcript=None)["model"] == expected


@pytest.mark.parametrize("value", NOT_A_MODEL)
def test_a_closed_run_never_reports_what_is_not_a_model(value: str) -> None:
    meta = {
        "run_id": "r",
        "agent": "codex",
        "status": "completed",
        "exit_code": 0,
        "usage": _recorded_usage(),
        "agent_model": value,
    }
    model = run_telemetry_from_meta(meta, transcript=None)["model"]
    assert isinstance(model, dict) and model["value"] == "unknown", model


def test_measured_usage_is_reported_as_recorded(tmp_path: Path) -> None:
    telemetry = run_telemetry_from_meta(
        _measured_meta(), transcript=_prose_transcript(tmp_path)
    )

    assert telemetry["model"] == "claude-opus-5-5"
    assert telemetry["telemetry_source"] == "meta"
    usage = telemetry["usage"]
    assert usage["source"] == "claude-transcript-jsonl-v1"
    assert usage["events"] == 1824
    assert usage["tokens_input"] == 3974
    assert usage["tokens_cached_input"] == 439996799
    assert usage["tokens_cache_write"] == 4802133
    assert usage["tokens_output"] == 1428316
    assert usage["tokens_total"] == 446231222
    cost = telemetry["cost"]
    # 3974*4e-6 + 4802133*5e-6 + 439996799*2e-7 + 1428316*2e-5
    assert cost["amount"] == pytest.approx(140.592241, abs=1e-6)
    assert cost["source"].startswith("estimated:")


def test_measured_usage_of_an_unpriced_model_keeps_an_unknown_cost() -> None:
    telemetry = run_telemetry_from_meta(
        _measured_meta(model="vendor-unreleased-99"), transcript=None
    )
    assert telemetry["usage"]["tokens_total"] == 446231222
    assert telemetry["cost"]["amount"]["value"] == "unknown"
    assert telemetry["unpricedModels"] == ["vendor-unreleased-99"]


def test_a_whole_run_aggregate_is_priced_at_base_rates() -> None:
    telemetry = run_telemetry_from_meta(
        _measured_meta(
            model="gpt-6.1-sol",
            measured={
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 1_000_000,
                "output_tokens": 100_000,
                "total_tokens": 2_100_000,
                "messages": 10,
            },
        ),
        transcript=None,
    )
    assert telemetry["cost"]["amount"] == pytest.approx(3.1, abs=1e-6)


def test_prose_in_a_legacy_transcript_yields_no_cost_and_no_model(
    tmp_path: Path,
) -> None:
    meta = {
        "run_id": "legacy",
        "agent": "agy",
        "status": "completed",
        "exit_code": 0,
        "completed_at": "2026-11-01T12:00:00Z",
    }
    telemetry = run_telemetry_from_meta(meta, transcript=_prose_transcript(tmp_path))
    assert telemetry["model"]["value"] == "unknown"
    assert telemetry["cost"]["amount"]["value"] == "unknown"


def test_the_report_builder_projects_both_kinds_of_closed_run(tmp_path: Path) -> None:
    for run_id, meta in (
        ("measured", _measured_meta()),
        (
            "legacy",
            {
                "run_id": "legacy",
                "agent": "agy",
                "status": "completed",
                "exit_code": 0,
                "completed_at": "2026-11-01T12:00:00Z",
            },
        ),
    ):
        run = tmp_path / run_id
        run.mkdir()
        (run / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (run / "transcript.log").write_text("".join(PROSE), encoding="utf-8")

    report = usage_reporting.build_usage_report(
        since="24h",
        now=dt.datetime(2026, 11, 1, 13, tzinfo=dt.UTC).timestamp(),
        runtime_runs_dir=tmp_path,
    )

    rows = {row["run_id"]: row for row in report["runs"]}
    assert rows["measured"]["model"] == "claude-opus-5-5"
    assert rows["measured"]["tokens"]["tokens_total"] == 446231222
    assert rows["measured"]["cost"]["amount"] == pytest.approx(140.592241, abs=1e-6)
    assert rows["legacy"]["model"]["value"] == "unknown"
    assert rows["legacy"]["cost"]["amount"]["value"] == "unknown"
