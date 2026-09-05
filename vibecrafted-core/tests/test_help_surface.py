from __future__ import annotations

from vibecrafted_core.cli import LAUNCHERS
from vibecrafted_core.help_surface import (
    WORKFLOW_HELP,
    render_resume_session_help,
    render_root_help,
    render_workflow_help,
)
from vibecrafted_core.workflows.registry import workflow_definition


def test_help_surface_covers_every_public_launcher() -> None:
    assert set(WORKFLOW_HELP) == set(LAUNCHERS)

    for launcher in LAUNCHERS:
        output = render_workflow_help(launcher)
        assert "Usage:" in output
        assert "Flow:" in output
        assert "Options:" in output
        assert "Examples:" in output
        assert f"launch vc-{launcher} through core runtime" not in output


def test_worker_help_declares_headless_as_the_default_surface() -> None:
    output = render_workflow_help("implement")

    assert "--runtime <terminal|headless>" in output
    assert "Worker surface (default: headless)" in output


def test_partner_help_is_interactive_only() -> None:
    output = render_workflow_help("partner")

    assert "Worker surface (default: headless)" not in output
    assert "--runtime <terminal|headless>" not in output
    assert "--runtime <terminal|visible|plain>" in output
    assert "vibecrafted partner codex" in output
    assert "not a job" in output


def test_research_help_exposes_swarm_alias() -> None:
    output = render_workflow_help("research")

    assert "vibecrafted swarm [agents...] [flags]" in output


def test_root_help_uses_the_registered_ship_cycle() -> None:
    output = render_root_help("test-version")

    assert "release engine for AI-developed software" in output
    assert (
        "scaffold → implement → review → workflow → followup → marbles → "
        "audit → polarize → dou → hydrate → release"
    ) in output
    assert "More workflows: vibecrafted help --all" in output
    assert "Vibecrafted core command surface" not in output
    assert "resume-session" in output
    assert "--run-id" in output
    assert "uninstall            Remove runtime" in output
    assert "vibecrafted uninstall --dry-run" in output


def test_resume_session_help_matches_the_tracked_headless_contract() -> None:
    output = render_resume_session_help()

    assert "--agent-session-id <id>" in output
    assert "--prompt-stdin" in output
    assert "always headless" in output
    assert "Guardian-visible process identity" in output


def test_marbles_help_locks_the_sequential_orchestrator_contract() -> None:
    output = render_workflow_help("marbles")

    assert "one dedicated orchestrator tab" in output
    assert "--count N runs sequential L1…LN rounds inside it" in output
    assert "no per-round child tabs" in output
    assert "L1 fix and verify → L2…LN repeat" in output
    assert "--count <n>" in output
    assert "--depth <n>" in output


def test_registry_controls_count_and_depth_visibility() -> None:
    for launcher in LAUNCHERS:
        if launcher == "paste":
            continue
        definition = workflow_definition(launcher)
        assert definition is not None
        output = render_workflow_help(launcher)
        assert ("--count <n>" in output) is definition.supports_count
        assert ("--depth <n>" in output) is definition.supports_depth


def test_implement_and_justdo_are_distinct_skills() -> None:
    implement = render_workflow_help("implement")
    justdo = render_workflow_help("justdo")

    assert "Not the same skill as justdo" in implement
    assert "Not implement" in justdo or "Not a VC-ship stage" in justdo
    assert "vibecrafted justdo" in justdo
    assert "vibecrafted implement" in implement
    assert "Alias: vibecrafted justdo" not in implement
    assert "Runs the same workflow as implement" not in justdo
