from __future__ import annotations

from vibecrafted_core.agent_stream import AgentStreamParser, resolve_default_model
from vibecrafted_core.telemetry import estimate_cost_usd


def test_agent_stream_parser_extracts_claude_session_usage_cost_and_text() -> None:
    parser = AgentStreamParser("claude")

    rendered = [
        parser.feed_line(
            b'{"type":"system","subtype":"init","session_id":"claude-sess",'
            b'"model":"claude-opus-4-8"}\n'
        ),
        parser.feed_line(
            b'{"type":"stream_event","event":{"type":"content_block_delta",'
            b'"delta":{"type":"text_delta","text":"hello"}}}\n'
        ),
        parser.feed_line(
            b'{"type":"result","result":"done","usage":{"input_tokens":10,'
            b'"cache_read_input_tokens":3,"cache_creation_input_tokens":2,'
            b'"output_tokens":5},'
            b'"total_cost_usd":0.0123}\n'
        ),
    ]

    assert "session: claude-sess" in "".join(rendered)
    assert "model: claude-opus-4-8" in "".join(rendered)
    assert "hello" in "".join(rendered)
    assert parser.session_id == "claude-sess"
    assert parser.model_id == "claude-opus-4-8"
    assert parser.tokens_input == 10
    assert parser.tokens_cached_input == 3
    assert parser.tokens_cache_write == 2
    assert parser.tokens_output == 5
    assert parser.cost_usd == 0.0123


def test_agent_stream_parser_extracts_codex_thread_usage_and_text() -> None:
    parser = AgentStreamParser("codex")
    parser.model_id = "gpt-5.3-codex"

    rendered = [
        parser.feed_line(b'{"type":"thread.started","thread_id":"codex-thread"}\n'),
        parser.feed_line(
            b'{"type":"item.completed","item":{"type":"agent_message",'
            b'"text":"codex body"}}\n'
        ),
        parser.feed_line(
            b'{"type":"turn.completed","usage":{"input_tokens":11,'
            b'"cached_input_tokens":4,"output_tokens":6}}\n'
        ),
    ]

    text = "".join(rendered)
    assert "session: codex-thread" in text
    assert "model: gpt-5.3-codex" in text
    assert "codex body" in text
    assert parser.session_id == "codex-thread"
    assert parser.model_id == "gpt-5.3-codex"
    assert parser.tokens_input == 11
    assert parser.tokens_cached_input == 4
    assert parser.tokens_cache_write is None
    assert parser.tokens_output == 6


def test_agent_stream_parser_extracts_gemini_session_stats_and_text() -> None:
    parser = AgentStreamParser("gemini")

    rendered = [
        parser.feed_line(
            b'{"type":"init","session_id":"gem-sess","model":"gemini-pro"}\n'
        ),
        parser.feed_line(b'{"type":"gemini","content":"gemini body"}\n'),
        parser.feed_line(
            b'{"type":"result","status":"done","stats":{"input_tokens":12,'
            b'"output_tokens":7,"duration_ms":42}}\n'
        ),
    ]

    text = "".join(rendered)
    assert "session: gem-sess" in text
    assert "model: gemini-pro" in text
    assert "gemini body" in text
    assert parser.session_id == "gem-sess"
    assert parser.model_id == "gemini-pro"
    assert parser.tokens_input == 12
    assert parser.tokens_output == 7
    # Regression: the result event MUST also render a human-readable token line
    # in TOKEN_PATTERN shape, so the regex token extractor used by the
    # research-swarm meta writer (spawn.py) recovers gemini usage from the
    # transcript instead of landing on 0 / "unknown".
    from vibecrafted_core.spawn import _extract_tokens

    assert "tokens: 12 in" in text
    assert "7 out" in text
    extracted = _extract_tokens(text)
    assert extracted["input"] == 12
    assert extracted["output"] == 7
    assert extracted["total"] == 19


def test_agent_stream_parser_renders_grok_thought_text_and_session() -> None:
    parser = AgentStreamParser("grok")

    rendered = [
        parser.feed_line(
            b"\x1b[31mERROR\x1b[0m worker quit with fatal: Transport channel "
            b"closed, when Auth(AuthorizationRequired)\n"
        ),
        parser.feed_line(b'{"type":"thought","data":"thinking"}\n'),
        parser.feed_line(b'{"type":"text","data":"Ok"}\n'),
        parser.feed_line(b'{"type":"text","data":"."}\n'),
        parser.feed_line(
            b'{"type":"end","stopReason":"EndTurn",'
            b'"sessionId":"019ec430-9888-78e3-8ca0-b29387444fdb"}\n'
        ),
    ]

    text = "".join(rendered)
    assert "thinking" in text
    assert "Ok." in text
    assert "Transport channel" not in text
    assert "None" not in text
    assert parser.session_id == "019ec430-9888-78e3-8ca0-b29387444fdb"


def test_filter_stream_renders_grok_and_tees_raw(tmp_path) -> None:
    from io import BytesIO

    from vibecrafted_core.agent_stream import filter_stream

    raw = tmp_path / "raw.jsonl"
    payload = (
        b'{"type":"thought","data":"thinking"}\n'
        b'{"type":"text","data":"hello pane"}\n'
        b'{"type":"end","sessionId":"sess-1"}\n'
    )
    out = BytesIO()
    status = filter_stream(
        "grok",
        stdin=BytesIO(payload),
        stdout=out,
        raw_file=raw,
    )
    assert status == 0
    text = out.getvalue().decode("utf-8")
    assert "hello pane" in text
    assert "thinking" in text
    assert '"type":"text"' not in text
    assert raw.read_bytes() == payload


def test_agent_stream_parser_maps_real_grok_end_telemetry() -> None:
    parser = AgentStreamParser("grok")
    parser.feed_line(
        b'{"type":"end","sessionId":"grok-session","usage":'
        b'{"input_tokens":34113,"cache_read_input_tokens":2752,'
        b'"output_tokens":151,"total_tokens":37016},"modelUsage":'
        b'{"grok-build":{"inputTokens":34113,"outputTokens":151,'
        b'"cacheReadInputTokens":2752,"modelCalls":1}}}\n'
    )

    assert parser.model_id == "grok-build"
    assert parser.tokens_input == 34113
    assert parser.tokens_cached_input == 2752
    assert parser.tokens_output == 151
    assert parser.cost_usd == 0.034965
    assert parser.cost_source == "estimated:xai-api-2026-07"


def test_agent_stream_parser_renders_junie_steps_without_none_noise() -> None:
    parser = AgentStreamParser("junie")

    rendered = [
        parser.feed_line(
            b'{"type":"session","timestamp":1,"sessionId":"session-junie-1"}\n'
        ),
        parser.feed_line(
            b'{"type":"step","name":"Thinking","details":"reviewing the docs"}\n'
        ),
        parser.feed_line(b'{"type":"step","name":"Read skill","details":"vc-audit"}\n'),
        parser.feed_line(b'{"type":"heartbeat","timestamp":2}\n'),
    ]

    text = "".join(rendered)
    assert "reviewing the docs" in text
    assert "Read skill: vc-audit" in text
    assert "None" not in text
    assert parser.session_id == "session-junie-1"


def test_agent_stream_parser_maps_real_junie_nested_model_usage() -> None:
    parser = AgentStreamParser("junie")
    parser.feed_line(
        b'{"kind":"SessionA2uxEvent","event":{"state":"IN_PROGRESS",'
        b'"agentEvent":{"kind":"LlmResponseMetadataEvent","modelUsage":['
        b'{"model":"gpt-5.5","cost":0.045821,"inputTokens":807,'
        b'"cacheInputTokens":49792,"cacheCreateTokens":0,'
        b'"outputTokens":563,"time":0}]}}}\n'
    )

    assert parser.model_id == "gpt-5.5"
    assert parser.tokens_input == 807
    assert parser.tokens_cached_input == 49792
    assert parser.tokens_cache_write == 0
    assert parser.tokens_output == 563
    assert parser.cost_usd == 0.045821
    assert parser.cost_source == "provider_reported"


def test_agent_stream_parser_treats_agy_as_claude_family_text_stream() -> None:
    parser = AgentStreamParser("agy")

    assert parser.feed_line(b"[12:00:00] session: agy-sess\n") == (
        "[12:00:00] session: agy-sess\n"
    )
    assert parser.feed_line(b"[12:00:00] model: agy-pro\n") == (
        "[12:00:00] model: agy-pro\n"
    )
    assert parser.feed_line(b"[12:00:01] tokens: 20 in (8 cached) / 9 out\n") == (
        "[12:00:01] tokens: 20 in (8 cached) / 9 out\n"
    )
    assert parser.session_id == "agy-sess"
    assert parser.model_id == "agy-pro"
    assert parser.resume_command("/repo") == "cd /repo && agy --conversation agy-sess"
    assert parser.tokens_input == 20
    assert parser.tokens_cached_input == 8
    assert parser.tokens_output == 9


def test_resolve_default_model_reads_codex_config(tmp_path, monkeypatch) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text('model = "gpt-5.5"\n', encoding="utf-8")
    monkeypatch.delenv("CODEX_MODEL", raising=False)

    assert (
        resolve_default_model("codex", env={"CODEX_HOME": str(codex_home)}) == "gpt-5.5"
    )
    assert (
        resolve_default_model(
            "codex",
            command=["codex", "exec", "--model", "gpt-5.3-codex", "-"],
            env={"CODEX_HOME": str(codex_home)},
        )
        == "gpt-5.3-codex"
    )


def test_cost_estimates_lock_user_reported_grok_and_codex_regressions() -> None:
    assert estimate_cost_usd(
        "grok-build",
        tokens_input=123064,
        tokens_cached_input=4200256,
        tokens_output=21463,
    ) == (1.006041, "estimated:xai-api-2026-07")
    assert estimate_cost_usd(
        "gpt-5.6-sol",
        tokens_input=20901518,
        tokens_cached_input=20536960,
        tokens_output=48906,
    ) == (116.24325, "estimated:openai-api-2026-07")


def test_grok_event_with_keyless_dict_message_does_not_recurse() -> None:
    parser = AgentStreamParser("grok")

    rendered = parser.feed_line(
        b'{"type":"error","error":{"code":429,"retriable":true}}\n'
    )

    assert rendered is not None
    assert "429" in rendered


def test_grok_tool_call_update_renders_nested_content_text() -> None:
    """grok 0.2.x tool_call_update: content is a list of {"type":"content",
    "content":{"type":"text","text":...}} blocks; render the text, not the
    JSON envelope, and never surface the rawOutput byte array."""
    parser = AgentStreamParser("grok")

    rendered = parser.feed_line(
        b'{"type":"tool_call_update","toolCallId":"call-1","status":"completed",'
        b'"content":[{"type":"content","content":{"type":"text",'
        b'"text":"Exit code 0"}}],'
        b'"rawOutput":{"type":"Bash","output":[84,114,97]}}\n'
    )

    assert "Exit code 0" in rendered
    assert '{"type"' not in rendered
    assert "rawOutput" not in rendered
    assert "84" not in rendered


def test_agent_stream_parser_renders_agy_stream_json_events(tmp_path) -> None:
    """agy stream-json: init banner, streamed answer, one usage record from result."""
    parser = AgentStreamParser("agy")
    init = parser.feed_line(
        b'{"event":"init","conversation_id":"conv-1","init":{"model":"gemini-3.8-flash-low","cwd":"/repo","tools":["run_command"]}}\n'
    )
    assert "session: conv-1" in init
    assert "model: gemini-3.8-flash-low" in init
    assert parser.session_id == "conv-1"
    assert parser.model_id == "gemini-3.8-flash-low"
    assert (
        parser.feed_line(
            b'{"event":"step_update","step_update":{"conversation_id":"conv-1","step_index":0,"state":"DONE","step_type":"user_input"}}\n'
        )
        == ""
    )
    assert (
        parser.feed_line(
            b'{"event":"step_update","step_update":{"conversation_id":"conv-1","step_index":1,"state":"ACTIVE","step_type":"agent_response","text_delta":"OK"}}\n'
        )
        == "OK"
    )
    tool = parser.feed_line(
        b'{"event":"step_update","step_update":{"conversation_id":"conv-1","step_index":2,"state":"ACTIVE","step_type":"run_command"}}\n'
    )
    assert "run_command" in tool
    result = parser.feed_line(
        b'{"event":"result","result":{"conversation_id":"conv-1","status":"SUCCESS","response":"OK\\n","duration_seconds":1.5,"num_turns":1,"usage":{"input_tokens":31345,"output_tokens":20,"thinking_tokens":19,"cache_read_tokens":128,"total_tokens":31365}}}\n'
    )
    assert "tokens: 31345 in (128 cached) / 20 out" in result
    assert "SUCCESS" in result
    assert parser.tokens_input == 31345
    assert parser.tokens_cached_input == 128
    assert parser.tokens_output == 20
    assert parser.final_response == "OK\n"
    assert parser.resume_command("/repo") == "cd /repo && agy --conversation conv-1"

    failed = AgentStreamParser("agy").feed_line(
        b'{"event":"result","result":{"conversation_id":"conv-2","status":"ERROR","response":"","error":"stream input message is missing the \\"event\\" field","usage":{"input_tokens":0,"output_tokens":0}}}\n'
    )
    assert "error" in failed and "missing the" in failed and "ERROR" in failed


def test_filter_stream_writes_agy_last_message(tmp_path) -> None:
    import io

    from vibecrafted_core.agent_stream import filter_stream

    stream = io.BytesIO(
        b'{"event":"init","conversation_id":"conv-9","init":{"model":"m"}}\n'
        b'{"event":"step_update","step_update":{"step_type":"agent_response","state":"DONE","text_delta":"done"}}\n'
        b'{"event":"result","result":{"status":"SUCCESS","response":"final answer","usage":{"input_tokens":1,"output_tokens":1}}}\n'
    )
    out = io.BytesIO()
    last = tmp_path / "last-message.md"
    assert filter_stream("agy", stdin=stream, stdout=out, last_message_file=last) == 0
    assert last.read_text(encoding="utf-8") == "final answer"
    assert b"done" in out.getvalue()

    empty_out = io.BytesIO()
    missing = tmp_path / "absent.md"
    filter_stream(
        "agy",
        stdin=io.BytesIO(b'{"event":"init","conversation_id":"c"}\n'),
        stdout=empty_out,
        last_message_file=missing,
    )
    assert not missing.exists()
