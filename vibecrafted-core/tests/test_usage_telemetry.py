"""Per-run usage, cost source and provider session truth.

Zero is a measurement, never a default: a run whose provider emitted no usage
events reports ``unknown`` with a reason. Cost has exactly three forms —
``provider_reported``, ``estimated:<price-table-id>`` or ``unknown(reason)`` —
and currencies/units are never summed into one field.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import shutil
from pathlib import Path

import pytest
from vibecrafted_core import cli, runtime_receipt, spawn, telemetry, usage_reporting
from vibecrafted_core.agent_stream import AgentStreamParser
from vibecrafted_core.control_plane import control_plane_home
from vibecrafted_core.report_contract import parse_report_text
from vibecrafted_core.supervisor_async import AsyncSupervisor

FIXTURES = Path(__file__).parent / "fixtures" / "telemetry"
QUOTA_RUN = "just-260918-060432-54797"
COMPLETED_RUN = "just-260918-060907-73299"
KIMI_SESSION = "session_62a8ce73-ebee-481a-b220-94eb471f047c"
NO_EVENTS = {"value": "unknown", "reason": "provider emitted no usage events"}
FOUNDER_FORM = "exit_code=1 (provider's code 403 quota exhausted)"


def _runtime_runs() -> Path:
    return control_plane_home() / "runtime_runs"


def _install_fixture_run(run_id: str) -> Path:
    run_dir = _runtime_runs() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("meta.json", "transcript.log"):
        shutil.copyfile(FIXTURES / run_id / name, run_dir / name)
    return run_dir


def _replaying_kimi(tmp_path: Path, run_id: str, *, exit_code: int = 0) -> Path:
    """A kimi stand-in that replays a recorded transcript byte for byte."""
    fixture = FIXTURES / run_id / "transcript.log"
    kimi = tmp_path / "kimi"
    kimi.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write(open({str(fixture)!r}, encoding='utf-8').read())\n"
        "sys.stdout.flush()\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    kimi.chmod(0o755)
    return kimi


# --------------------------------------------------------------------------
# Usage
# --------------------------------------------------------------------------


def test_parser_counts_usage_events_so_zero_can_be_measured() -> None:
    kimi = AgentStreamParser("kimi")
    for line in (
        (FIXTURES / COMPLETED_RUN / "transcript.log").read_bytes().splitlines(True)
    ):
        kimi.feed_line(line)
    assert kimi.usage_events == 0
    assert kimi.session_id == KIMI_SESSION

    codex = AgentStreamParser("codex")
    codex.feed_line(
        b'{"type":"turn.completed","usage":{"input_tokens":0,"output_tokens":0}}\n'
    )
    assert codex.usage_events == 1
    record = telemetry.usage_record(
        codex.usage_events,
        tokens_input=codex.tokens_input,
        tokens_cached_input=codex.tokens_cached_input,
        tokens_cache_write=codex.tokens_cache_write,
        tokens_output=codex.tokens_output,
        source="provider_stream",
    )
    # A provider that *reported* zero tokens measured zero.
    assert record.as_dict()["tokens_total"] == 0


def test_run_without_usage_events_is_unknown_never_zero() -> None:
    record = telemetry.usage_record(
        0,
        tokens_input=0,
        tokens_cached_input=0,
        tokens_cache_write=None,
        tokens_output=0,
        source="provider_stream",
    )

    payload = record.as_dict()
    assert payload["unit"] == "tokens"
    assert payload["events"] == 0
    for key in (
        "tokens_input",
        "tokens_cached_input",
        "tokens_output",
        "tokens_total",
    ):
        assert payload[key] == NO_EVENTS
    flat = record.flat()
    assert flat["tokens_total"] == "unknown"
    assert 0 not in flat.values()


def test_run_with_usage_has_numbers_and_provider_stream_source() -> None:
    record = telemetry.usage_record(
        2,
        tokens_input=1200,
        tokens_cached_input=200,
        tokens_cache_write=None,
        tokens_output=300,
        source="provider_stream",
    )

    payload = record.as_dict()
    assert payload["source"] == "provider_stream"
    assert payload["events"] == 2
    assert payload["tokens_input"] == 1200
    assert payload["tokens_cached_input"] == 200
    assert payload["tokens_output"] == 300
    assert payload["tokens_total"] == 1500
    assert payload["tokens_cache_write"]["value"] == "unknown"
    assert payload["tokens_cache_write"]["reason"]
    assert record.flat()["tokens_total"] == 1500


def test_unknown_projection_matches_the_runtime_receipt_contract() -> None:
    reason = "provider emitted no usage events"
    assert (
        telemetry.Unknown(reason=reason).as_dict()
        == runtime_receipt.Unknown(reason=reason).as_dict()
    )


def test_junie_top_level_usage_is_counted_once() -> None:
    parser = AgentStreamParser("junie")
    parser.feed_line(
        b'{"type":"result","usage":{"input_tokens":100,"output_tokens":10}}\n'
    )
    assert (parser.tokens_input, parser.tokens_output) == (100, 10)
    assert parser.usage_events == 1


def test_plain_text_cost_carries_its_source() -> None:
    parser = AgentStreamParser("agy")
    parser.feed_line(b"cost: $0.25\n")
    assert parser.cost_usd == 0.25
    assert parser.cost_source == "provider_reported"


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------


def _known_usage() -> telemetry.UsageRecord:
    return telemetry.usage_record(
        1,
        tokens_input=1200,
        tokens_cached_input=200,
        tokens_cache_write=None,
        tokens_output=300,
        source="provider_stream",
    )


def _no_usage() -> telemetry.UsageRecord:
    return telemetry.usage_record(
        0,
        tokens_input=0,
        tokens_cached_input=0,
        tokens_cache_write=None,
        tokens_output=0,
        source="provider_stream",
    )


def test_cost_provider_reported_wins() -> None:
    cost = telemetry.resolve_cost(
        "claude-opus-5",
        _known_usage(),
        reported_amount=0.42,
        reported_source="provider_reported",
    )
    assert cost.as_dict() == {
        "amount": 0.42,
        "currency": "USD",
        "source": "provider_reported",
    }
    assert cost.flat() == {"cost_usd": 0.42, "cost_source": "provider_reported"}


def test_cost_estimated_names_its_price_table() -> None:
    cost = telemetry.resolve_cost(
        "gpt-5.5", _known_usage(), reported_amount=None, reported_source=None
    )
    payload = cost.as_dict()
    assert payload["source"] == "estimated:openai-api-2026-07"
    assert payload["currency"] == "USD"
    assert payload["amount"] == round((1200 * 5.0 + 200 * 0.5 + 300 * 30.0) / 1e6, 6)


@pytest.mark.parametrize(
    "model,usage,reason",
    [
        ("kimi-k2", "known", "no price table entry for model 'kimi-k2'"),
        ("", "known", "model unknown; no price table lookup"),
        (
            "gpt-5.5",
            "none",
            "provider emitted no usage events and reported no cost",
        ),
    ],
)
def test_cost_unknown_has_a_reason_and_no_default_price(
    model: str, usage: str, reason: str
) -> None:
    record = _known_usage() if usage == "known" else _no_usage()

    cost = telemetry.resolve_cost(
        model, record, reported_amount=None, reported_source=None
    )

    payload = cost.as_dict()
    assert payload["source"] == "unknown"
    assert payload["amount"] == {"value": "unknown", "reason": reason}
    assert cost.flat() == {"cost_usd": "unknown", "cost_source": "unknown"}
    assert telemetry.model_price("kimi-k2") is None


# --------------------------------------------------------------------------
# Production close path (AsyncSupervisor → meta.json + report frontmatter)
# --------------------------------------------------------------------------


def test_supervisor_closes_run_without_usage_as_unknown_with_provider_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = "01a0-parent-vibecrafted-session"
    monkeypatch.setenv("VIBECRAFTED_SESSION_ID", parent)
    monkeypatch.setenv("VIBECRAFTED_OPERATOR_SESSION", "parent-operator-frame")
    monkeypatch.delenv("VIBECRAFTED_AGENT", raising=False)
    run_id = "w3-kimi-no-usage"
    meta = _runtime_runs() / run_id / "meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "transcript.log"

    handle = asyncio.run(
        AsyncSupervisor().run(
            run_id=run_id,
            command=[str(_replaying_kimi(tmp_path, COMPLETED_RUN))],
            root=tmp_path,
            meta_path=meta,
            report_path=report,
            transcript_path=transcript,
        )
    )

    assert handle.exit_code == 0
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["tokens_total"] == "unknown"
    assert payload["tokens_input"] == "unknown"
    assert payload["tokens_output"] == "unknown"
    assert payload["usage"]["tokens_total"] == NO_EVENTS
    assert payload["usage"]["source"] == "provider_stream"
    assert payload["usage"]["events"] == 0
    assert payload["cost"]["source"] == "unknown"
    assert payload["cost"]["amount"] == {
        "value": "unknown",
        "reason": "provider emitted no usage events and reported no cost",
    }
    assert payload["cost_usd"] == "unknown"
    assert "failure" not in payload
    assert payload["provider_session_id"] == KIMI_SESSION
    assert payload["provider_session_source"] == "provider_stream"
    # never the parent's identity
    assert payload["provider_session_id"] not in {parent, "parent-operator-frame"}
    assert payload["provider_session_id"] != payload["runtime_session_id"]

    fields, _body, _fm = parse_report_text(report.read_text(encoding="utf-8"))
    assert fields["provider_session_id"] == KIMI_SESSION
    assert fields["session_id"] == KIMI_SESSION
    assert fields["tokens_total"] == "unknown"
    assert fields["usage_reason"] == "provider emitted no usage events"
    assert fields["usage_source"] == "provider_stream"
    assert fields["cost_source"] == "unknown"
    assert "failure" not in fields


def test_supervisor_refuses_a_provider_session_equal_to_the_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = "session_parent_leak"
    monkeypatch.setenv("VIBECRAFTED_PARENT_PROVIDER_SESSION_ID", parent)
    monkeypatch.delenv("VIBECRAFTED_AGENT", raising=False)
    kimi = tmp_path / "kimi"
    kimi.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'role': 'assistant', 'content': 'ok'}))\n"
        "print(json.dumps({'role': 'meta', 'type': 'session.resume_hint',"
        f" 'session_id': {parent!r}}}))\n",
        encoding="utf-8",
    )
    kimi.chmod(0o755)
    run_id = "w3-parent-leak"
    meta = _runtime_runs() / run_id / "meta.json"
    report = tmp_path / "report.md"

    asyncio.run(
        AsyncSupervisor().run(
            run_id=run_id,
            command=[str(kimi)],
            root=tmp_path,
            meta_path=meta,
            report_path=report,
            transcript_path=tmp_path / "transcript.log",
        )
    )

    payload = json.loads(meta.read_text(encoding="utf-8"))
    provider = payload["provider_session_id"]
    assert provider != parent
    assert provider["value"] == "unknown"
    assert "VIBECRAFTED_PARENT_PROVIDER_SESSION_ID" in provider["reason"]
    fields, _body, _fm = parse_report_text(report.read_text(encoding="utf-8"))
    assert fields["provider_session_id"] == "unknown"
    assert parent not in fields["provider_session_id"]


def test_supervisor_run_with_usage_reports_numbers_and_provider_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIBECRAFTED_AGENT", raising=False)
    claude = tmp_path / "claude"
    claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'type': 'system', 'subtype': 'init',"
        " 'session_id': 'claude-sess-1', 'model': 'claude-opus-5'}))\n"
        "print(json.dumps({'type': 'assistant', 'session_id': 'claude-sess-1',"
        " 'message': {'model': 'claude-opus-5', 'content':"
        " [{'type': 'text', 'text': 'done'}]}}))\n"
        "print(json.dumps({'type': 'result', 'session_id': 'claude-sess-1',"
        " 'result': 'done', 'usage': {'input_tokens': 1000,"
        " 'cache_read_input_tokens': 400, 'output_tokens': 250},"
        " 'total_cost_usd': 0.1234}))\n",
        encoding="utf-8",
    )
    claude.chmod(0o755)
    run_id = "w3-claude-usage"
    meta = _runtime_runs() / run_id / "meta.json"

    asyncio.run(
        AsyncSupervisor().run(
            run_id=run_id,
            command=[str(claude)],
            root=tmp_path,
            meta_path=meta,
            report_path=tmp_path / "report.md",
            transcript_path=tmp_path / "transcript.log",
        )
    )

    payload = json.loads(meta.read_text(encoding="utf-8"))
    usage = payload["usage"]
    assert usage["source"] == "provider_stream"
    assert usage["events"] == 1
    assert usage["tokens_input"] == 1000
    assert usage["tokens_cached_input"] == 400
    assert usage["tokens_output"] == 250
    assert usage["tokens_total"] == 1250
    assert payload["tokens_total"] == 1250
    assert payload["cost"] == {
        "amount": 0.1234,
        "currency": "USD",
        "source": "provider_reported",
    }
    assert payload["cost_source"] == "provider_reported"
    assert payload["provider_session_id"] == "claude-sess-1"


def test_supervisor_salvaged_report_and_footer_do_not_print_fake_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.delenv("VIBECRAFTED_AGENT", raising=False)
    grok = tmp_path / "grok"
    grok.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'type': 'text', 'data': 'Ok.'}))\n"
        "print(json.dumps({'type': 'end', 'sessionId': 'grok-session'}))\n",
        encoding="utf-8",
    )
    grok.chmod(0o755)
    report = tmp_path / "report.md"

    asyncio.run(
        AsyncSupervisor().run(
            run_id="w3-grok-salvage",
            command=[str(grok)],
            root=tmp_path,
            meta_path=_runtime_runs() / "w3-grok-salvage" / "meta.json",
            report_path=report,
            transcript_path=tmp_path / "transcript.log",
            tee_output=True,
        )
    )

    out = capsys.readouterr().out
    assert "tokens_total: unknown" in out
    assert "tokens_total: 0" not in out
    report_text = report.read_text(encoding="utf-8")
    assert "tokens_total: unknown" in report_text
    assert "tokens_total: 0" not in report_text


# --------------------------------------------------------------------------
# Launcher close path (spawn.finalize_artifacts)
# --------------------------------------------------------------------------


def test_finalize_artifacts_writes_usage_cost_and_failure(tmp_path: Path) -> None:
    transcript = tmp_path / "run.transcript.log"
    shutil.copyfile(FIXTURES / QUOTA_RUN / "transcript.log", transcript)
    report = tmp_path / "run.md"
    report.write_text(
        "---\nrun_id: w3-finalize\nstatus: failed\n---\n# failed\n", encoding="utf-8"
    )
    meta = tmp_path / "run.meta.json"
    meta.write_text(
        json.dumps(
            {
                "run_id": "w3-finalize",
                "agent": "kimi",
                "skill": "justdo",
                "status": "failed",
                "exit_code": 1,
                "root": str(tmp_path),
                "report": str(report),
                "transcript": str(transcript),
            }
        ),
        encoding="utf-8",
    )

    final_meta = spawn.finalize_artifacts(meta, report, transcript)

    assert final_meta is not None
    payload = json.loads(final_meta.read_text(encoding="utf-8"))
    assert payload["tokens_total"] == "unknown"
    assert payload["usage"]["tokens_total"] == NO_EVENTS
    assert payload["usage"]["source"] == "transcript"
    assert payload["cost"]["source"] == "unknown"
    failure = payload["failure"]
    assert failure["kind"] == "quota_exhausted"
    assert failure["provider_code"] == 403
    assert failure["summary"] == FOUNDER_FORM
    final_transcript = Path(payload["transcript"])
    evidence_line = final_transcript.read_text(encoding="utf-8").splitlines()[
        failure["evidence"]["line"] - 1
    ]
    assert "403" in evidence_line and "monthly usage limit" in evidence_line
    fields, _body, _fm = parse_report_text(report.read_text(encoding="utf-8"))
    assert fields["tokens_total"] == "unknown"
    assert fields["failure_kind"] == "quota_exhausted"
    assert "tokens_total: 0" not in report.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# vibecrafted usage
# --------------------------------------------------------------------------


def _usage_json(capsys: pytest.CaptureFixture, *argv: str) -> tuple[str, dict]:
    assert cli.main(["usage", *argv, "--json"]) == 0
    raw = capsys.readouterr().out
    return raw, json.loads(raw)


def test_usage_report_builder_is_reusable_without_cli() -> None:
    for run_id in (QUOTA_RUN, COMPLETED_RUN):
        _install_fixture_run(run_id)

    report = usage_reporting.build_usage_report(
        run_ids=(COMPLETED_RUN, QUOTA_RUN, COMPLETED_RUN)
    )

    assert report["schema"] == usage_reporting.USAGE_REPORT_SCHEMA
    assert report["filter"] == {"run_ids": [QUOTA_RUN, COMPLETED_RUN]}
    assert [row["run_id"] for row in report["runs"]] == [
        QUOTA_RUN,
        COMPLETED_RUN,
    ]
    assert [row["provider"] for row in report["runs"]] == ["kimi", "kimi"]
    assert report["totals"]["runs_tokens_unknown"] == 2


def test_usage_report_builder_rejects_conflicting_filters() -> None:
    with pytest.raises(
        usage_reporting.UsageReportQueryError,
        match="pass --run-id or --since, not both",
    ):
        usage_reporting.build_usage_report(run_ids=(QUOTA_RUN,), since="1h")


def test_usage_report_accepts_explicit_external_cost_adapter() -> None:
    _install_fixture_run(QUOTA_RUN)

    class FixtureCostAdapter:
        adapter_id = "fixture-cost"

        def cost_for_run(self, **_context: object) -> dict[str, object]:
            return {
                "amount": 3.25,
                "unit": "credits",
                "source": "external:fixture-cost",
            }

    report = usage_reporting.build_usage_report(
        run_ids=(QUOTA_RUN,), cost_adapters=(FixtureCostAdapter(),)
    )

    assert report["runs"][0]["cost"] == {
        "amount": 3.25,
        "unit": "credits",
        "source": "external:fixture-cost",
    }
    assert report["totals"]["cost_by_unit"] == {"credits": 3.25}
    assert report["totals"]["runs_cost_unknown"] == 0


def test_usage_report_never_overrides_provider_reported_cost() -> None:
    run_id = "provider-cost-wins"
    run_dir = _runtime_runs() / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "agent": "codex",
                "status": "completed",
                "exit_code": 0,
                "usage": telemetry.usage_record(
                    1,
                    tokens_input=10,
                    tokens_cached_input=0,
                    tokens_cache_write=None,
                    tokens_output=5,
                    source="provider_stream",
                ).as_dict(),
                "cost": {
                    "amount": 0.125,
                    "currency": "USD",
                    "source": "provider_reported",
                },
            }
        ),
        encoding="utf-8",
    )

    class MustNotRunAdapter:
        adapter_id = "must-not-run"

        def cost_for_run(self, **_context: object) -> dict[str, object]:
            raise AssertionError("provider-reported cost must win before adapters")

    report = usage_reporting.build_usage_report(
        run_ids=(run_id,), cost_adapters=(MustNotRunAdapter(),)
    )

    assert report["runs"][0]["cost"]["amount"] == 0.125
    assert report["runs"][0]["cost"]["source"] == "provider_reported"


def test_usage_json_for_the_two_fixture_runs_is_deterministic(
    capsys: pytest.CaptureFixture,
) -> None:
    for run_id in (QUOTA_RUN, COMPLETED_RUN):
        _install_fixture_run(run_id)
    argv = ("--run-id", COMPLETED_RUN, "--run-id", QUOTA_RUN)

    first_raw, report = _usage_json(capsys, *argv)
    second_raw, _ = _usage_json(capsys, *argv)

    assert first_raw == second_raw
    assert report["schema"] == "vibecrafted.usage-report.v1"
    assert [row["run_id"] for row in report["runs"]] == [QUOTA_RUN, COMPLETED_RUN]
    rows = {row["run_id"]: row for row in report["runs"]}

    quota = rows[QUOTA_RUN]
    assert quota["agent"] == "kimi"
    assert quota["exit_code"] == 1
    assert quota["failure_kind"] == "quota_exhausted"
    assert quota["failure"] == FOUNDER_FORM
    assert quota["tokens"]["tokens_total"] == NO_EVENTS
    assert quota["cost"]["source"] == "unknown"
    assert quota["cost"]["amount"]["value"] == "unknown"
    assert quota["telemetry_source"] == "transcript(lazy)"

    done = rows[COMPLETED_RUN]
    assert done["failure_kind"] is None
    assert done["failure"] is None
    assert done["tokens"]["tokens_total"] == NO_EVENTS
    assert done["provider_session_id"] == KIMI_SESSION

    # legacy meta said tokens_total=0; the report must not repeat that lie
    assert all(row["tokens"]["tokens_total"] != 0 for row in report["runs"])
    assert report["totals"] == {
        "runs": 2,
        "tokens_total_known": 0,
        "runs_tokens_unknown": 2,
        "cost_by_unit": {},
        "runs_cost_unknown": 2,
    }


def test_usage_totals_never_add_credits_to_dollars(
    capsys: pytest.CaptureFixture,
) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for run_id, cost in (
        (
            "w3-usd",
            {"amount": 0.5, "currency": "USD", "source": "provider_reported"},
        ),
        (
            "w3-credits",
            {"amount": 12, "unit": "credits", "source": "provider_reported"},
        ),
    ):
        run_dir = _runtime_runs() / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "meta.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "agent": "junie",
                    "status": "completed",
                    "exit_code": 0,
                    "completed_at": now,
                    "usage": telemetry.usage_record(
                        1,
                        tokens_input=10,
                        tokens_cached_input=0,
                        tokens_cache_write=None,
                        tokens_output=5,
                        source="provider_stream",
                    ).as_dict(),
                    "cost": cost,
                    "provider_session_id": f"{run_id}-session",
                }
            ),
            encoding="utf-8",
        )

    _raw, report = _usage_json(capsys, "--since", "1h")

    assert [row["run_id"] for row in report["runs"]] == ["w3-credits", "w3-usd"]
    assert report["totals"]["cost_by_unit"] == {"USD": 0.5, "credits": 12.0}
    assert report["totals"]["tokens_total_known"] == 30
    assert report["totals"]["runs_cost_unknown"] == 0
    assert {row["telemetry_source"] for row in report["runs"]} == {"meta"}
    assert set(report["totals"]) == {
        "runs",
        "tokens_total_known",
        "runs_tokens_unknown",
        "cost_by_unit",
        "runs_cost_unknown",
    }


def test_usage_since_excludes_old_runs_and_rejects_garbage(
    capsys: pytest.CaptureFixture,
) -> None:
    _install_fixture_run(QUOTA_RUN)  # completed 2026-09-18T04:04Z

    _raw, report = _usage_json(capsys, "--since", "1s")
    assert QUOTA_RUN not in {row["run_id"] for row in report["runs"]}

    assert cli.main(["usage", "--since", "yesterday"]) == 2


def test_usage_table_names_unknowns_and_failure(
    capsys: pytest.CaptureFixture,
) -> None:
    _install_fixture_run(QUOTA_RUN)

    assert cli.main(["usage", "--run-id", QUOTA_RUN]) == 0

    out = capsys.readouterr().out
    assert "run_id" in out and "tokens" in out and "cost" in out
    assert QUOTA_RUN in out
    assert "quota_exhausted" in out
    assert "unknown" in out
