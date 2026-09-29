"""The --effort pin reaches every provider CLI that has an effort control.

Effort is a cost control (Founder order 2026-09-29 after codex exec silently
ran gpt-6-astra at high): the launch parameter exists for every agent, the
carriage is per-provider, and an agent without a knob records a receipted skip
instead of refusing the launch or dropping the request silently.
"""

from __future__ import annotations

import pytest
from vibecrafted_core.effort_overrides import (
    EFFORT_OVERRIDE_STYLES,
    _effort_override_receipt,
    _with_effort_override,
)
from vibecrafted_core.workflow import WorkflowLaunchSpec


def test_codex_effort_rides_the_exec_config_override() -> None:
    command = ["codex", "exec", "--json", "-"]
    assert _with_effort_override("codex", command, "low") == [
        "codex",
        "exec",
        "-c",
        "model_reasoning_effort=low",
        "--json",
        "-",
    ]


def test_codex_existing_equal_pin_is_idempotent_and_conflict_refuses() -> None:
    pinned = ["codex", "exec", "-c", "model_reasoning_effort=low", "-"]
    assert _with_effort_override("codex", pinned, "low") == pinned
    with pytest.raises(ValueError, match="conflicts_with_existing_effort"):
        _with_effort_override("codex", pinned, "high")


def test_claude_and_grok_take_two_token_flags() -> None:
    assert _with_effort_override("claude", ["claude", "-p"], "medium") == [
        "claude",
        "--effort",
        "medium",
        "-p",
    ]
    assert _with_effort_override("grok", ["grok", "--json"], "low") == [
        "grok",
        "--reasoning-effort",
        "low",
        "--json",
    ]


def test_junie_takes_the_single_assign_token() -> None:
    assert _with_effort_override("junie", ["junie", "run"], "high") == [
        "junie",
        "--effort=high",
        "run",
    ]


def test_agy_direct_argv_is_pinned_and_wrappers_fail_closed() -> None:
    assert _with_effort_override("agy", ["agy", "--print"], "max") == [
        "agy",
        "--effort",
        "max",
        "--print",
    ]
    with pytest.raises(ValueError, match="unsupported_agy_command_shape"):
        _with_effort_override("agy", ["bash", "-c", "agy --print"], "max")


def test_flag_conflict_refuses_and_equal_pin_is_idempotent() -> None:
    pinned = ["claude", "--effort", "high", "-p"]
    assert _with_effort_override("claude", pinned, "high") == pinned
    with pytest.raises(ValueError, match="conflicts_with_existing_effort"):
        _with_effort_override("claude", pinned, "low")


def test_agents_without_a_knob_skip_with_a_receipt_not_a_refusal() -> None:
    command = ["kimi", "-p", "prompt"]
    assert _with_effort_override("kimi", command, "low") == command
    receipt = _effort_override_receipt("kimi", "low")
    assert receipt["effort_requested"] == "low"
    assert receipt["effort_override_supported"] is False
    assert receipt["effort_override_skipped"] is True
    assert receipt["effort_override_skip_reason"] == "unsupported_agent_effort_flag"


def test_supported_receipt_and_empty_request_shapes() -> None:
    receipt = _effort_override_receipt("codex", "low")
    assert receipt["effort_override_supported"] is True
    assert receipt["effort_override_skipped"] is False
    assert _effort_override_receipt("codex", "") == {}
    assert _effort_override_receipt("codex", None) == {}
    assert _with_effort_override("codex", ["codex", "exec", "-"], "") == [
        "codex",
        "exec",
        "-",
    ]


def test_every_styled_agent_has_a_deterministic_spelling() -> None:
    # The table is the contract; a new provider must land here consciously.
    assert EFFORT_OVERRIDE_STYLES == {
        "claude": ("flag", "--effort"),
        "agy": ("flag", "--effort"),
        "grok": ("flag", "--reasoning-effort"),
        "junie": ("assign", "--effort"),
        "codex": ("codex_config", "model_reasoning_effort"),
    }


def test_launch_spec_carries_the_effort_field() -> None:
    spec = WorkflowLaunchSpec(
        agent="codex",
        mode="implement",
        skill="workflow",
        prompt="p",
        file="",
        runtime="headless",
        root="/tmp",
        effort="low",
    )
    assert spec.effort == "low"
    assert spec.to_payload()["effort"] == "low"
