"""Cursor fleet adapter — key `cursor` maps to binary `cursor-agent`."""

from __future__ import annotations

import pytest
from vibecrafted_core import spawn, workflow, workflow_runtime
from vibecrafted_core.agent_stream import AgentStreamParser
from vibecrafted_core.continuity import capabilities as continuity
from vibecrafted_core.model_overrides import MODEL_OVERRIDE_FLAGS, _with_model_override


def test_cursor_capability_key_and_binary_decoupled() -> None:
    cap = continuity.capability_for("cursor")
    assert cap.agent == "cursor"
    assert cap.execution == continuity.EXECUTABLE
    assert cap.prompt_transport == "stdin"
    assert cap.interactive_resume == continuity.SUPPORTED
    assert cap.noninteractive_resume == continuity.UNVERIFIED
    assert cap.native_fork == continuity.UNSUPPORTED
    assert cap.probe_recipe is not None
    assert cap.probe_recipe.cli == "cursor-agent"
    assert spawn.agent_cli_name("cursor") == "cursor-agent"
    assert spawn.agent_cli_name("claude") == "claude"


def test_cursor_headless_stdin_and_default_commands() -> None:
    stdin = spawn._stdin_command("cursor")
    assert stdin[0] == "cursor-agent"
    assert "-p" in stdin
    assert "stream-json" in stdin
    assert "--force" in stdin
    assert "--trust" in stdin

    default = spawn._default_command("cursor", "hello from fleet")
    assert default[0] == "cursor-agent"
    assert default[-1] == "hello from fleet"
    assert "stream-json" in default


def test_cursor_native_headless_resume_fails_closed() -> None:
    with pytest.raises(ValueError, match="native_resume_unsupported:cursor"):
        workflow_runtime.native_resume_argv("cursor", "sess-cursor-1")
    assert "cursor" not in workflow_runtime.NATIVE_RESUME_AGENTS


def test_cursor_accepted_by_workflow_supported_agents() -> None:
    assert "cursor" in workflow.SUPPORTED_AGENTS
    assert "cursor" in spawn.POLICY_PROVIDERS


def test_cursor_model_override_flag() -> None:
    assert MODEL_OVERRIDE_FLAGS["cursor"] == "--model"
    cmd = _with_model_override(
        "cursor",
        ["cursor-agent", "-p", "--output-format", "stream-json"],
        "gpt-5",
    )
    assert cmd[:3] == ["cursor-agent", "--model", "gpt-5"]


def test_cursor_stream_parser_reads_init_assistant_thinking_result() -> None:
    parser = AgentStreamParser("cursor")
    rendered = [
        parser.feed_line(
            b'{"type":"system","subtype":"init","session_id":"abc-123","model":"gpt-5"}\n'
        ),
        parser.feed_line(
            b'{"type":"thinking","subtype":"delta","text":"hmm","timestamp_ms":1}\n'
        ),
        parser.feed_line(
            b'{"type":"assistant","message":{"content":[{"type":"text","text":"pong"}]},'
            b'"session_id":"abc-123"}\n'
        ),
        parser.feed_line(
            b'{"type":"result","subtype":"success","result":"done",'
            b'"usage":{"inputTokens":10,"outputTokens":3,'
            b'"cacheReadTokens":2,"cacheWriteTokens":1}}\n'
        ),
    ]
    joined = "".join(rendered)
    assert parser.session_id == "abc-123"
    assert parser.model_id == "gpt-5"
    assert "pong" in joined
    assert "hmm" in joined
    assert parser.tokens_input == 10
    assert parser.tokens_output == 3
    assert parser.tokens_cached_input == 2
    assert parser.tokens_cache_write == 1
    assert "cursor-agent --resume abc-123" in parser.resume_command("/tmp/ws")


def test_cursor_interactive_bare_fork_fails_closed() -> None:
    with pytest.raises(ValueError, match="native fork is unsupported"):
        spawn.interactive_policy_command(
            "cursor",
            "hi",
            "local-native",
            "bypass",
            continuity_policy=spawn.ContinuityPolicy(
                "bare-fork",
                "bare-fork:test",
                parent_provider_session_id="parent-sess",
            ),
        )


def test_cursor_resolve_pins_cursor_agent_binary(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "cursor-agent"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(
        spawn,
        "agent_tool_search_path",
        lambda _env=None: str(tmp_path),
    )
    pinned = spawn._resolve_agent_command(
        "cursor",
        ["cursor-agent", "-p", "--output-format", "stream-json"],
    )
    assert pinned[0] == str(fake)
