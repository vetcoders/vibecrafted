from __future__ import annotations

from pathlib import Path

from vibecrafted_core import workflow
from vibecrafted_core.agent_dispatch import sandbox_supported
from vibecrafted_core.spawn import _default_command


def test_workflow_launch_spec_accepts_agy_junie_and_grok(tmp_path: Path) -> None:
    for agent in ("agy", "junie", "grok"):
        spec = workflow.normalize_launch_spec(
            {"skill": "workflow", "agent": agent, "prompt": "go"},
            tmp_path,
        )
        assert spec.agent == agent


def test_build_launch_command_passes_new_agent_to_command_deck(tmp_path: Path) -> None:
    spec = workflow.WorkflowLaunchSpec(
        agent="junie",
        mode="workflow",
        skill="workflow",
        prompt="go",
        file="",
        runtime="headless",
        root=str(tmp_path),
    )

    command = workflow.build_launch_command(spec, tmp_path / "src")

    assert command[:2] == ["junie", "--project"]
    assert "--input-format" in command
    assert "Skill: vc-workflow" not in command


def test_supervisor_defaults_and_sandbox_support_cover_agy_junie_grok() -> None:
    assert sandbox_supported("agy") is True
    assert sandbox_supported("junie") is True
    assert sandbox_supported("grok") is True
    assert _default_command("claude", "go")[:3] == ["claude", "--print", "--verbose"]
    assert _default_command("codex", "go")[:2] == ["codex", "exec"]
    # gemini deprecated: must raise actionable migration error, never launch
    try:
        _ = _default_command("gemini", "go")
        assert False, "gemini must raise deprecation"
    except ValueError as e:
        assert "deprecated" in str(e).lower() or "agy" in str(e).lower()
    # agy >= 1.1: --print takes the prompt as its value; flags precede it.
    # Default permissions are auto for the whole fleet; agy auto needs no flag.
    assert _default_command("agy", "go") == [
        "agy",
        "--add-dir",
        ".",
        "--print-timeout",
        "30m",
        "--print",
        "go",
    ]
    assert _default_command("junie", "go")[:2] == ["junie", "--task"]
    assert _default_command("grok", "go")[:2] == ["grok", "--cwd"]
    assert "--single" in _default_command("grok", "go")
