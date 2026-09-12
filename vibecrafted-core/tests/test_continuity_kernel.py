"""F05 parity skeleton — provider capability cannot drift by surface.

Full F05 generates the continuity decision through core, CLI alias, MCP and
TUI projection and asserts identical capability + strategy. Those surfaces
land in later W-waves; this skeleton pins the first two truths NOW:

* the registry (`continuity.capabilities`) and the core dispatch surface
  (`spawn`) may never disagree about resume/fork support;
* the surfaces that do not exist yet are named here as explicit skips, so
  the parity matrix is visible from day one instead of appearing ad hoc.
"""

from __future__ import annotations

import pytest
from vibecrafted_core import spawn, workflow_runtime
from vibecrafted_core.continuity import capabilities as continuity

EXECUTABLE_AGENTS = ("claude", "codex", "agy", "junie", "grok", "cursor")
VERIFIED_HEADLESS_RESUME_AGENTS = ("claude", "codex", "grok")


def _spawn_accepts_headless_resume(agent: str) -> bool:
    try:
        workflow_runtime._resume_stdin_command(agent, "sess-parity-check")
    except ValueError:
        return False
    return True


@pytest.mark.parametrize("agent", VERIFIED_HEADLESS_RESUME_AGENTS)
def test_core_spawn_and_registry_agree_on_headless_resume(agent: str) -> None:
    cap = continuity.capability_for(agent)
    spawn_says = _spawn_accepts_headless_resume(agent)
    # Fail-closed rule: spawn may only build a headless resume for agents the
    # registry marks SUPPORTED; unverified/unsupported agents must be
    # rejected, never silently downgraded to a fresh session.
    registry_says = cap.noninteractive_resume == continuity.SUPPORTED
    assert spawn_says == registry_says, (
        f"{agent}: spawn headless-resume={spawn_says} but registry "
        f"declares {cap.noninteractive_resume!r}"
    )


def test_gemini_rejected_by_both_registry_and_spawn() -> None:
    cap = continuity.capability_for("gemini")
    assert cap.execution == continuity.EVIDENCE_ONLY
    with pytest.raises(ValueError, match="deprecated"):
        spawn._stdin_command("gemini")
    with pytest.raises(ValueError):
        workflow_runtime._resume_stdin_command("gemini", "sess-parity-check")


def test_every_executable_agent_has_a_fresh_launch_lane() -> None:
    for agent in EXECUTABLE_AGENTS:
        command = spawn._stdin_command(agent)
        assert command, f"{agent} lost its fresh headless launch lane"
        assert continuity.capability_for(agent).execution == continuity.EXECUTABLE


@pytest.mark.parametrize("surface", ["cli_alias", "mcp", "tui"])
def test_parity_projection_placeholder(surface: str) -> None:
    pytest.skip(
        f"F05 {surface} projection lands with the kernel resolver (W0-03+); "
        "core↔registry parity is enforced above"
    )
