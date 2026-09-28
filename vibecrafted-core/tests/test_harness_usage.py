"""Redacted native record shapes; no prompts or private session payloads."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest
from vibecrafted_core import harness_usage as h
from vibecrafted_core import telemetry as t
from vibecrafted_core.supervisor_async import AsyncSupervisor

START = "2026-09-28T10:00:00+00:00"
END = "2026-09-28T11:00:00+00:00"
BEFORE = "2026-09-28T09:00:00+00:00"
STAMP = "2026-09-28T10:30:00+00:00"
SESSION = "fixture-session"


def write(home, path, rows):
    target = home / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return target


def codex(usage, stamp=STAMP):
    return {
        "type": "event_msg",
        "timestamp": stamp,
        "payload": {"type": "token_count", "info": {"total_token_usage": usage}},
    }


def codex_file(home, rows, created=START):
    return write(
        home,
        f".codex/sessions/2026/09/28/rollout-{SESSION}.jsonl",
        [
            {"type": "session_meta", "timestamp": created, "payload": {"id": SESSION}},
            {
                "type": "turn_context",
                "timestamp": START,
                "payload": {"model": "gpt-6-astra"},
            },
            *rows,
        ],
    )


def resolve(home, agent="codex"):
    return h.resolve_harness_usage(
        agent=agent, session_id=SESSION, started_at=START, completed_at=END, home=home
    )


def test_codex_cumulative_cache_subset_duplicates_window_and_first_identity(tmp_path):
    row = codex(
        {
            "input_tokens": 1000,
            "cached_input_tokens": 900,
            "output_tokens": 50,
            "reasoning_output_tokens": 20,
            "total_tokens": 1050,
        }
    )
    path = codex_file(
        tmp_path,
        [
            row,
            row,
            codex({"input_tokens": 5000, "output_tokens": 100}, "2026-09-28T12:00:00Z"),
        ],
    )
    result = resolve(tmp_path)
    assert result.totals() == {
        "fresh_input": 100,
        "cache_read": 900,
        "cache_creation": 0,
        "output": 50,
        "reasoning": 20,
    }
    assert result.events == 1
    # A subsequent session_meta is inherited history, never another identity.
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    rows[0]["payload"]["id"] = "other"
    rows.insert(1, {"type": "session_meta", "payload": {"id": SESSION}})
    write(tmp_path, str(path.relative_to(tmp_path)), rows)
    assert resolve(tmp_path) is None


def test_codex_resume_subtracts_baseline(tmp_path):
    codex_file(
        tmp_path,
        [
            codex(
                {"input_tokens": 500, "cached_input_tokens": 400, "output_tokens": 20},
                BEFORE,
            ),
            codex(
                {"input_tokens": 800, "cached_input_tokens": 600, "output_tokens": 40}
            ),
        ],
        BEFORE,
    )
    assert resolve(tmp_path).totals()["fresh_input"] == 100
    assert resolve(tmp_path).totals()["output"] == 20


def test_codex_resume_without_baseline_is_unknown(tmp_path):
    codex_file(tmp_path, [codex({"input_tokens": 800, "output_tokens": 40})], BEFORE)
    assert resolve(tmp_path) is None


@pytest.mark.parametrize("bad", [True, -1, 1.5, "10", None, h.MAX_COUNT + 1])
@pytest.mark.parametrize(
    "field",
    ["input_tokens", "cached_input_tokens", "reasoning_output_tokens", "total_tokens"],
)
def test_present_invalid_field_rejects_entire_codex_line(tmp_path, bad, field):
    usage = {"input_tokens": 10, "output_tokens": 2, field: bad}
    codex_file(tmp_path, [codex(usage)])
    assert resolve(tmp_path) is None


def test_zero_is_measurement_and_overflow_is_not(tmp_path):
    codex_file(tmp_path, [codex({"input_tokens": 0, "output_tokens": 0})])
    assert resolve(tmp_path).events == 1
    codex_file(tmp_path, [codex({"input_tokens": h.MAX_COUNT, "output_tokens": 1})])
    assert resolve(tmp_path) is None


def test_claude_message_updates_and_multiple_models(tmp_path):
    def message(out, model="claude-sonnet-5", mid="message-1", stamp=STAMP):
        return {
            "timestamp": stamp,
            "sessionId": SESSION,
            "message": {
                "id": mid,
                "model": model,
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 400,
                    "cache_creation_input_tokens": 30,
                    "output_tokens": out,
                },
            },
        }

    write(
        tmp_path,
        f".claude/projects/project/{SESSION}.jsonl",
        [
            message(10),
            message(20),
            message(20),
            message(5, "kimi-code/k3", "message-2"),
        ],
    )
    result = resolve(tmp_path, "claude")
    assert result.totals() == {
        "fresh_input": 200,
        "cache_read": 800,
        "cache_creation": 60,
        "output": 25,
        "reasoning": 0,
    }
    assert len(result.models) == 2


def test_kimi_wire_turn_usage_only_and_duplicates(tmp_path):
    row = {
        "type": "usage.record",
        "time": 1790591400000,
        "agentId": "agent-0",
        "model": "kimi-code/k3",
        "usageScope": "turn",
        "usage": {
            "inputOther": 12,
            "inputCacheRead": 100,
            "inputCacheCreation": 3,
            "output": 4,
        },
    }
    # Generate epoch from the known window rather than rely on the fixture day.
    row["time"] = int(dt.datetime.fromisoformat(STAMP).timestamp() * 1000)
    write(
        tmp_path,
        f".kimi-code/sessions/wd_project/session_{SESSION}/agents/agent-0/wire.jsonl",
        [row, row, {**row, "usageScope": "session"}],
    )
    assert resolve(tmp_path, "kimi").totals() == {
        "fresh_input": 12,
        "cache_read": 100,
        "cache_creation": 3,
        "output": 4,
        "reasoning": 0,
    }


def test_grok_shared_log_requires_session_and_window(tmp_path):
    row = {
        "msg": "shell.turn.inference_done",
        "ts": STAMP,
        "sid": SESSION,
        "ctx": {
            "loop_index": 1,
            "prompt_tokens": 100,
            "cached_prompt_tokens": 80,
            "completion_tokens": 20,
            "reasoning_tokens": 5,
        },
    }
    write(
        tmp_path,
        ".grok/logs/unified.jsonl",
        [row, row, {**row, "sid": "other"}, {**row, "ts": BEFORE}],
    )
    result = resolve(tmp_path, "grok")
    assert result.totals() == {
        "fresh_input": 20,
        "cache_read": 80,
        "cache_creation": 0,
        "output": 20,
        "reasoning": 5,
    }
    assert result.events == 1


def test_junie_nested_native_usage_is_measured(tmp_path):
    row = {
        "kind": "SessionA2uxEvent",
        "timestampMs": int(dt.datetime.fromisoformat(STAMP).timestamp() * 1000),
        "event": {
            "agentEvent": {
                "kind": "LlmResponseMetadataEvent",
                "modelUsage": [
                    {
                        "model": "claude-sonnet-5",
                        "inputTokens": 10,
                        "cacheInputTokens": 100,
                        "cacheCreateTokens": 20,
                        "outputTokens": 3,
                        "cost": 0.01,
                    }
                ],
            }
        },
    }
    write(tmp_path, f".junie/sessions/{SESSION}/events.jsonl", [row, row])
    assert resolve(tmp_path, "junie").totals() == {
        "fresh_input": 10,
        "cache_read": 100,
        "cache_creation": 20,
        "output": 3,
        "reasoning": 0,
    }


def test_cursor_text_is_never_a_measurement(tmp_path):
    write(
        tmp_path,
        f".cursor/projects/project/agent-transcripts/{SESSION}/{SESSION}.jsonl",
        [{"role": "assistant", "message": {"content": "hello"}}],
    )
    assert resolve(tmp_path, "cursor") is None


def test_copilot_shutdown_summary_requires_entire_session_window(tmp_path):
    row = {
        "type": "session.shutdown",
        "timestamp": STAMP,
        "data": {
            "sessionStartTime": int(
                dt.datetime.fromisoformat(START).timestamp() * 1000
            ),
            "modelMetrics": {
                "gpt-6-astra": {
                    "usage": {
                        "inputTokens": 100,
                        "cacheReadTokens": 80,
                        "outputTokens": 20,
                        "reasoningTokens": 5,
                    }
                }
            },
        },
    }
    path = f".copilot/session-state/{SESSION}/events.jsonl"
    write(tmp_path, path, [row])
    assert resolve(tmp_path, "copilot").totals()["fresh_input"] == 20
    row["data"]["sessionStartTime"] = int(
        dt.datetime.fromisoformat(BEFORE).timestamp() * 1000
    )
    write(tmp_path, path, [row])
    assert resolve(tmp_path, "copilot") is None


def no_usage():
    return t.usage_record(
        0,
        tokens_input=0,
        tokens_cached_input=0,
        tokens_cache_write=None,
        tokens_output=0,
        source="provider_stream",
    )


def test_fallback_prices_cache_once_preserves_source_and_refuses_parent(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    codex_file(
        tmp_path,
        [
            codex(
                {"input_tokens": 1000, "cached_input_tokens": 900, "output_tokens": 50}
            )
        ],
    )
    args = {
        "usage": no_usage(),
        "model": "gpt-6-astra",
        "reported_cost": None,
        "reported_cost_source": None,
        "session_candidate": SESSION,
        "session_source": "provider_stream",
        "parents": {},
        "failure": None,
        "agent": "codex",
        "started_at": START,
        "completed_at": END,
    }
    result = t.build_run_telemetry(**args)
    assert result.usage.source == "harness_log"
    assert result.usage.tokens_total == 1050
    assert result.usage.as_dict()["counting_version"] == 2
    assert result.cost.amount == pytest.approx((100 * 10 + 900 * 1 + 50 * 50) / 1e6)
    assert result.frontmatter_fields()["usage_source"] == "harness_log"
    assert not t.build_run_telemetry(
        **{**args, "parents": {SESSION: "parent"}}
    ).usage.known
    measured_zero = t.usage_record(
        1,
        tokens_input=0,
        tokens_cached_input=0,
        tokens_cache_write=None,
        tokens_output=0,
        source="provider_stream",
    )
    assert (
        t.build_run_telemetry(**{**args, "usage": measured_zero}).usage is measured_zero
    )


def test_lazy_projection_recovers_unknown_but_preserves_provider_cost(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    codex_file(tmp_path, [codex({"input_tokens": 100, "output_tokens": 5})])
    meta = {
        "agent": "codex",
        "provider_session_id": SESSION,
        "started_at": START,
        "completed_at": END,
        "usage": no_usage().as_dict(),
        "cost": {"amount": 0.12, "currency": "USD", "source": "provider_reported"},
    }
    row = t.run_telemetry_from_meta(meta, transcript=None)
    assert row["usage"]["tokens_total"] == 105
    assert row["cost"]["amount"] == 0.12
    assert meta["usage"]["events"] == 0


def test_price_lookup_does_not_guess_future_model_or_substring():
    assert t.model_price("gpt-6-astra") is not None
    assert t.model_price("kimi-code/k3") is not None
    assert t.model_price("gpt-5-imaginary") is None
    assert t.model_price("not-k3") is None


def test_supervisor_settle_reads_log_and_persists_dashboard_contract(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("VIBECRAFTED_AGENT", raising=False)
    native = tmp_path / f".codex/sessions/2026/09/28/rollout-{SESSION}.jsonl"
    native.parent.mkdir(parents=True)
    worker = tmp_path / "codex"
    worker.write_text(
        "#!/usr/bin/env python3\nimport json,datetime\nfrom pathlib import Path\n"
        + "stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()\n"
        + f"rows=[{{'type':'session_meta','timestamp':stamp,'payload':{{'id':{SESSION!r}}}}},{{'type':'event_msg','timestamp':stamp,'payload':{{'type':'token_count','info':{{'total_token_usage':{{'input_tokens':100,'cached_input_tokens':80,'output_tokens':10}}}}}}}}]\n"
        + f"Path({str(native)!r}).write_text(''.join(json.dumps(r)+'\\n' for r in rows))\n"
        + f"print(json.dumps({{'type':'thread.started','thread_id':{SESSION!r}}}))\n"
    )
    worker.chmod(0o755)
    meta = tmp_path / "meta.json"
    asyncio.run(
        AsyncSupervisor().run(
            run_id="fallback-fixture",
            command=[str(worker)],
            root=tmp_path,
            meta_path=meta,
            report_path=tmp_path / "report.md",
            transcript_path=tmp_path / "transcript.log",
        )
    )
    saved = json.loads(meta.read_text())
    assert saved["usage"]["source"] == "harness_log"
    assert saved["tokens_total"] == 110
    assert saved["provider_session_id"] == SESSION


def test_agy_gemini_chat_counts_measured_thoughts_once(tmp_path):
    p = tmp_path / ".gemini/tmp/project/chats/session-date.json"
    p.parent.mkdir(parents=True)
    p.write_text(
        json.dumps(
            {
                "sessionId": SESSION,
                "messages": [
                    {
                        "id": "m1",
                        "timestamp": STAMP,
                        "type": "gemini",
                        "model": "gemini-3.1-pro-preview",
                        "tokens": {
                            "input": 100,
                            "cached": 80,
                            "output": 10,
                            "thoughts": 5,
                            "tool": 0,
                            "total": 115,
                        },
                    }
                ],
            }
        )
    )
    result = resolve(tmp_path, "agy")
    assert result.totals()["output"] == 15
    assert result.totals()["reasoning"] == 5
    assert result.totals()["fresh_input"] == 20


def test_malformed_and_partial_lines_do_not_become_measurements(tmp_path):
    path = codex_file(tmp_path, [])
    with path.open("a") as f:
        f.write("{broken}\n")
        f.write(json.dumps(codex({"input_tokens": 10, "output_tokens": 2})))
    assert resolve(tmp_path) is None


def test_unpriced_model_is_explicit_and_no_partial_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    write(
        tmp_path,
        f".claude/projects/project/{SESSION}.jsonl",
        [
            {
                "timestamp": STAMP,
                "sessionId": SESSION,
                "message": {
                    "id": "m",
                    "model": "future-model",
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                },
            }
        ],
    )
    row = t.run_telemetry_from_meta(
        {
            "agent": "claude",
            "provider_session_id": SESSION,
            "started_at": START,
            "completed_at": END,
            "usage": no_usage().as_dict(),
        },
        transcript=None,
    )
    assert row["unpricedModels"] == ["future-model"]
    assert row["cost"]["source"] == "unknown"
    assert row["usage"]["tokens_total"] == 110


def test_settle_stream_pricing_uses_provider_cache_semantics():
    usage = t.usage_record(
        1,
        tokens_input=1000,
        tokens_cached_input=900,
        tokens_cache_write=0,
        tokens_output=50,
        source="provider_stream",
    )
    kwargs = {
        "usage": usage,
        "model": "gpt-6-astra",
        "reported_cost": 1.0,
        "reported_cost_source": "estimated:old",
        "session_candidate": SESSION,
        "session_source": "provider_stream",
        "parents": {},
        "failure": None,
        "agent": "codex",
    }
    result = t.build_run_telemetry(**kwargs)
    assert result.cost.amount == 0.0044
    assert result.usage is usage
    assert (
        t.build_run_telemetry(
            **{**kwargs, "reported_cost_source": "provider_reported"}
        ).cost.amount
        == 1.0
    )


def test_historical_stream_usage_gets_new_price_without_changing_tokens():
    usage = t.usage_record(
        1,
        tokens_input=1000,
        tokens_cached_input=900,
        tokens_cache_write=0,
        tokens_output=50,
        source="provider_stream",
    )
    row = t.run_telemetry_from_meta(
        {
            "agent": "codex",
            "model": "gpt-6-astra",
            "usage": usage.as_dict(),
            "cost": {"source": "unknown", "amount": {"value": "unknown"}},
        },
        transcript=None,
    )
    assert row["cost"]["amount"] == 0.0044
    assert row["usage"] == usage.as_dict()


def test_kimi_prefixed_session_uses_native_directory(tmp_path):
    row = {
        "type": "usage.record",
        "sessionId": "session_" + SESSION,
        "time": int(dt.datetime.fromisoformat(STAMP).timestamp() * 1000),
        "usageScope": "turn",
        "model": "kimi-code/k3",
        "usage": {"inputOther": 1, "output": 2},
    }
    write(
        tmp_path,
        f".kimi-code/sessions/wd_project/session_{SESSION}/agents/agent-0/wire.jsonl",
        [row],
    )
    result = h.resolve_harness_usage(
        agent="kimi",
        session_id="session_" + SESSION,
        started_at=START,
        completed_at=END,
        home=tmp_path,
    )
    assert result.totals()["output"] == 2


def test_historical_start_comes_only_from_matching_canonical_snapshot(
    tmp_path, monkeypatch
):
    from vibecrafted_core import control_plane

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    codex_file(tmp_path, [codex({"input_tokens": 100, "output_tokens": 5})])
    snapshot = {"run_id": "fixture", "agent_session_id": SESSION, "started_at": START}
    monkeypatch.setattr(control_plane, "lookup_run_snapshot", lambda _: snapshot)
    meta = {
        "run_id": "fixture",
        "agent": "codex",
        "agent_session_id": SESSION,
        "completed_at": END,
    }
    assert (
        t.run_telemetry_from_meta(meta, transcript=None)["usage"]["tokens_total"] == 105
    )
    snapshot["agent_session_id"] = "other"
    assert t.run_telemetry_from_meta(meta, transcript=None)["usage"]["events"] == 0


def test_workflow_child_returns_settled_usage_instead_of_silent_stream(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from vibecrafted_core import workflow_runtime as w

    measured = t.UsageRecord(
        tokens_input=100,
        tokens_cached_input=80,
        tokens_cache_write=0,
        tokens_output=10,
        tokens_total=110,
        source="harness_log",
        events=1,
    )
    settled = t.RunTelemetry(
        usage=measured,
        cost=t.CostRecord(amount=0.05, source="estimated:fixture"),
        failure=None,
        provider_session_id=SESSION,
        provider_session_source="provider_stream",
    )
    handle = SimpleNamespace(
        artifact_validation=None,
        agent_session_id=SESSION,
        agent_model="gpt-6-astra",
        model_requested="",
        model_override_supported=False,
        model_override_skipped=False,
        model_override_skip_reason="",
        exit_code=0,
        tokens_input=0,
        tokens_cached_input=0,
        tokens_cache_write=None,
        tokens_output=0,
        cost_usd=None,
        resume_command="",
        completed_at=dt.datetime.fromisoformat(END),
        telemetry=settled,
    )

    async def completed(*args, **kwargs):
        return handle

    monkeypatch.setattr(w.AsyncSupervisor, "run", completed)
    monkeypatch.setattr(
        w,
        "_child_artifact_paths",
        lambda **kwargs: tuple(
            tmp_path / name
            for name in ("report.md", "transcript.log", "meta.json", "prompt.md")
        ),
    )
    monkeypatch.setattr(
        w, "_resolve_agent_command", lambda command_agent, command, env: command
    )
    result = asyncio.run(
        w._run_child(
            kind="workflow",
            label="measured",
            agent="codex",
            root=str(tmp_path),
            prompt="fixture",
            command=["true"],
        )
    )
    assert (
        result.tokens_input,
        result.tokens_cached_input,
        result.tokens_output,
        result.cost_usd,
    ) == (100, 80, 10, 0.05)


def test_malformed_copilot_data_is_not_a_settlement_failure(tmp_path):
    write(
        tmp_path,
        f".copilot/session-state/{SESSION}/events.jsonl",
        [{"type": "session.shutdown", "timestamp": STAMP, "data": "broken"}],
    )
    assert resolve(tmp_path, "copilot") is None


def test_legacy_grok_stream_input_is_fresh_not_the_native_prompt_total():
    usage = t.usage_record(
        1,
        tokens_input=123064,
        tokens_cached_input=4200256,
        tokens_cache_write=None,
        tokens_output=21463,
        source="provider_stream",
    )
    result = t.build_run_telemetry(
        usage=usage,
        model="grok-build",
        reported_cost=1.006041,
        reported_cost_source="estimated:xai-api-2026-07",
        session_candidate=SESSION,
        session_source="provider_stream",
        parents={},
        failure=None,
        agent="grok",
    )
    assert result.cost.amount == 1.006041
    assert result.usage is usage
