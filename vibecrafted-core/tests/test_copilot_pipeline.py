"""Copilot's private prompt, policy, stream, and inbox contracts."""

from __future__ import annotations

import json

import pytest
from vibecrafted_core.agent_stream import AgentStreamParser
from vibecrafted_core.effort_overrides import _with_effort_override
from vibecrafted_core.execution_controls import (
    ExecutionControlsError,
    resolve_execution_controls,
)
from vibecrafted_core.model_overrides import _with_model_override
from vibecrafted_core.research_config import SUPPORTED_RESEARCH_AGENTS
from vibecrafted_core.spawn import _stdin_command
from vibecrafted_core.workflow_runtime import native_resume_argv


def test_headless_command_reads_private_stdin_and_enforces_policy() -> None:
    prompt = "private prompt only on stdin"
    command = _stdin_command("copilot")
    assert command == [
        "copilot",
        "--allow-all",
        "--no-ask-user",
        "--no-auto-update",
        "--output-format",
        "json",
    ]
    assert prompt not in " ".join(command)
    assert "-p" not in command

    restricted = _stdin_command(
        "copilot", resolve_execution_controls("copilot", permissions="read-only")
    )
    assert "--available-tools=read" in restricted
    assert "--allow-all" not in restricted
    with pytest.raises(ExecutionControlsError, match="cannot enforce"):
        resolve_execution_controls("copilot", permissions="auto")


def test_model_effort_and_native_resume_use_copilot_flags() -> None:
    base = _stdin_command("copilot")
    modeled = _with_model_override("copilot", base, "gpt-5.4")
    assert modeled[1:3] == ["--model", "gpt-5.4"]
    effort = _with_effort_override("copilot", modeled, "high")
    assert effort[1:3] == ["--reasoning-effort", "high"]
    resumed = native_resume_argv("copilot", "native-session-id")
    assert resumed[:3] == ["copilot", "--resume", "native-session-id"]
    assert "--allow-all" in resumed
    assert "-p" not in resumed


def test_research_lane_accepts_copilot() -> None:
    assert "copilot" in SUPPORTED_RESEARCH_AGENTS


def test_jsonl_stream_retains_session_and_answer_without_claiming_cumulative_usage() -> (
    None
):
    parser = AgentStreamParser("copilot")
    message = parser.feed_line(
        json.dumps(
            {
                "type": "assistant.message",
                "data": {"content": "Done.", "model": "gpt-5.4"},
            }
        ).encode()
    )
    assert "Done." in message
    assert parser.final_response == "Done."
    assert parser.model_id == "gpt-5.4"
    parser.feed_line(
        json.dumps(
            {
                "type": "session.shutdown",
                "data": {"modelMetrics": {"gpt-5.4": {"usage": {"inputTokens": 900}}}},
            }
        ).encode()
    )
    assert parser.usage_events == 0
    assert parser.tokens_input == 0
    result = parser.feed_line(
        json.dumps(
            {
                "type": "result",
                "sessionId": "copilot-session",
                "exitCode": 0,
                "usage": {"premiumRequests": 1},
            }
        ).encode()
    )
    assert "copilot-session" in result
    assert parser.session_id == "copilot-session"
