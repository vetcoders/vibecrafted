"""Versioned, machine-readable workflow capability surface.

Clients (GUIs, MCP servers, downstream planners) need to know what a
``vibecrafted <workflow>`` invocation actually executes WITHOUT parsing help
text or user configs: runtime kind, execution target, how requested agents are
honored, where the research lane selection came from, and which configured
tokens were dropped as unsupported. This module serializes the already-typed
registry (:mod:`.workflows.registry`) plus the live research picking policy
(:mod:`.research_config`) into one stable JSON payload — it does not fork the
policy, it exposes it.

Read-only by contract: composing the payload launches nothing, mutates no
config, and writes nothing to the control plane.

Naming note: ``vibecrafted.capabilities.v1`` (see :mod:`.capabilities`) is the
foundation-binary probe schema (loct/aicx). This surface is a different
concern and carries its own schema id.
"""

from __future__ import annotations

from shutil import which
from typing import Any

from .execution_controls import (
    PERMISSION_POLICIES,
    ExecutionControlsError,
    default_permissions,
    resolve_execution_controls,
)
from .model_overrides import MODEL_OVERRIDE_FLAGS
from .research_config import (
    DEFAULT_RESEARCH_AGENTS,
    SUPPORTED_RESEARCH_AGENTS,
    ResearchAgentSelection,
    resolve_research_runtime_config,
)
from .runtime_paths import agent_tool_search_path
from .spawn import RUNTIME_POLICIES, agent_cli_name, host_substrate_capabilities
from .workflow import SUPPORTED_AGENTS
from .workflows import registry as workflow_registry

WORKFLOW_CAPABILITIES_SCHEMA = "vibecrafted.workflow_capabilities.v1"
WORKFLOW_CAPABILITIES_SCHEMA_VERSION = 1

# Mirror of build_launch_command's supervised_marbles substitution
# (`spec.agent if spec.agent != "swarm" else "codex"`): a swarm request on a
# single-agent supervised loop is honored as codex, and that truth belongs in
# the declared surface, not only in the launch path.
MARBLES_SWARM_FALLBACK_AGENT = "codex"

# Human labels for the canonical runtime-policy ids (``spawn.RUNTIME_POLICIES``).
# The ids are the contract; the labels are the words the product surfaces use
# for the same four environments, owned here so no client re-invents them.
ENVIRONMENT_LABELS: dict[str, str] = {
    "local-native": "Living Tree",
    "local-worktrees": "Fleet Worktrees",
    "local-vm": "Fleet VM local",
    "cloud-soon": "Fleet VM cloud",
}

# How a skill launcher (``vibecrafted <skill> <agent>``) realizes each
# environment. Only the two local policies have a launcher surface today:
# Living Tree is the default checkout, Fleet Worktrees is ``--worktree true``
# (a fresh linked checkout of the selected repository root). The VM policies
# have no skill-launcher entrypoint; ``environment_capabilities_payload``
# reports them unavailable with the same reasons the interactive picker gives.
SKILL_LAUNCHER_ENVIRONMENT_FLAGS: dict[str, tuple[str, ...]] = {
    "local-native": (),
    "local-worktrees": ("--worktree", "true"),
}

# Sandbox words a client may declare; ``""`` is "provider default" (flag omitted).
_SANDBOX_WORDS: tuple[str, ...] = ("", "true", "false")


def _sandbox_value(word: str) -> bool | None:
    if word == "true":
        return True
    if word == "false":
        return False
    return None


def _control_cells(provider: str) -> list[dict[str, Any]]:
    """Every (permissions, sandbox) declaration a client can make for ``provider``.

    Each cell is resolved through :func:`resolve_execution_controls`, the same
    pre-launch gate ``launch_workflow`` applies, so "supported" here means the
    launcher will accept the declaration and "reason" is the exact refusal it
    would print. Omitted words are reported as the provider default the
    launcher would apply, never as a guess.
    """
    cells: list[dict[str, Any]] = []
    for permissions in ("", *PERMISSION_POLICIES):
        for sandbox in _SANDBOX_WORDS:
            cell: dict[str, Any] = {
                "permissions": permissions,
                "sandbox": sandbox,
            }
            try:
                controls = resolve_execution_controls(
                    provider,
                    permissions=permissions,
                    sandbox=_sandbox_value(sandbox),
                )
            except ExecutionControlsError as exc:
                cell.update(
                    {
                        "supported": False,
                        "permissions_effective": "",
                        "sandbox_effective": "",
                        "provider_flags": [],
                        "reason": str(exc),
                    }
                )
            else:
                cell.update(
                    {
                        "supported": True,
                        "permissions_effective": controls.permissions_effective,
                        "sandbox_effective": controls.sandbox_effective,
                        "provider_flags": list(controls.provider_flags),
                        "reason": "",
                    }
                )
            cells.append(cell)
    return cells


def provider_capabilities_payload() -> dict[str, dict[str, Any]]:
    """Per-agent launcher contract: executable, model pin, permission/sandbox cells.

    Read-only: ``which`` lookups on the agent tool search path plus pure
    contract resolution. ``swarm`` is the research execution agent, not a
    provider a client can declare, so it is not listed here.
    """
    providers: dict[str, dict[str, Any]] = {}
    search_path = agent_tool_search_path()
    for agent in sorted(SUPPORTED_AGENTS - {"swarm"}):
        executable = which(agent_cli_name(agent), path=search_path)
        providers[agent] = {
            "binary": agent_cli_name(agent),
            "executable": executable or "",
            "available": executable is not None,
            "reason": ""
            if executable
            else f"{agent_cli_name(agent)} executable not found",
            "model_override": {
                "supported": agent in MODEL_OVERRIDE_FLAGS,
                "flag": MODEL_OVERRIDE_FLAGS.get(agent, ""),
                "reason": (
                    ""
                    if agent in MODEL_OVERRIDE_FLAGS
                    else "unsupported_agent_model_flag"
                ),
            },
            "permissions_default": default_permissions(agent),
            "controls": _control_cells(agent),
        }
    return providers


def environment_capabilities_payload() -> dict[str, dict[str, Any]]:
    """Availability of the four runtime-policy environments for skill launchers.

    Substrate facts come from :func:`spawn.host_substrate_capabilities` (the
    same probe the interactive picker uses). Unavailable environments carry the
    reason a client must show instead of silently falling back.
    """
    substrate = host_substrate_capabilities()
    payload: dict[str, dict[str, Any]] = {}
    for policy in RUNTIME_POLICIES:
        flags = SKILL_LAUNCHER_ENVIRONMENT_FLAGS.get(policy)
        if policy == "local-native":
            available, reason = True, ""
        elif policy == "local-worktrees":
            available = bool(substrate["worktree_substrate"])
            reason = "" if available else "git/dispatch manage_worktrees unavailable"
        elif policy == "local-vm":
            available = False
            reason = (
                "no canonical VM entrypoint"
                if substrate["vm"]
                else "Docker/Colima is not detected"
            )
        else:
            available, reason = False, "coming soon"
        payload[policy] = {
            "label": ENVIRONMENT_LABELS.get(policy, policy),
            "available": available,
            "reason": reason,
            "skill_launcher_supported": flags is not None,
            "skill_launcher_flags": list(flags or ()),
        }
    return payload


def _synthesizer_payload(selection: ResearchAgentSelection) -> dict[str, Any]:
    """Project a research selection's synthesizer fields into the capability payload."""
    return {
        "agent": selection.synthesizer,
        "model": selection.synthesizer_model,
        "source": selection.synthesizer_source,
        # With no explicit synthesizer the runtime resumes the last surviving
        # research lane (see workflow_runtime._run_research_synthesis).
        "fallback": "last_surviving_lane",
    }


def _selection_payload(selection: ResearchAgentSelection) -> dict[str, Any]:
    """Project a live research agent selection into its JSON-serializable form."""
    return {
        "source": selection.source,
        "agents": list(selection.agents),
        "unsupported_configured": list(selection.ignored),
        "lane_models": dict(selection.lane_models or {}),
        "synthesizer": _synthesizer_payload(selection),
    }


def _workflow_record(
    definition: Any, selection: ResearchAgentSelection
) -> dict[str, Any]:
    """Build one workflow's capability record, adding runtime-kind-specific fields.

    Research workflows get positional-agent semantics and the live lane
    selection; marbles-kind workflows get the swarm-agent fallback note.
    """
    is_research = definition.runtime_kind == "supervised_research"
    record: dict[str, Any] = {
        "name": definition.id,
        "aliases": list(definition.aliases),
        "cadence": definition.cadence,
        "lifecycle_order": definition.lifecycle_order,
        "can_modify_code": definition.can_modify_code,
        "input_policy": definition.input_policy,
        "runtime_kind": definition.runtime_kind,
        "execution_target": "swarm" if is_research else "single_agent",
        "default_agent": definition.default_agent,
        # Post-56975f0b truth: research rejects unknown positional agents at
        # launch (fail-closed) and honors known ones as synthesizer/lanes;
        # every other workflow runs the requested agent as-is.
        "requested_agent_policy": "fail_closed" if is_research else "honored",
        "supports_count": definition.supports_count,
        "supports_depth": definition.supports_depth,
        "terminal_layout": definition.terminal_layout,
        "tooling": list(definition.tooling),
    }
    if is_research:
        record["positional_agent_semantics"] = {
            "single": "synthesizer_override",
            "multiple": "lanes_with_first_as_synthesizer",
            "unsupported_token": "launch_rejected",
            "execution_agent": "swarm",
        }
        record["selection_source"] = selection.source
        record["effective_agents"] = list(selection.agents)
        record["unsupported_configured"] = list(selection.ignored)
        record["synthesizer"] = _synthesizer_payload(selection)
    if definition.runtime_kind == "supervised_marbles":
        record["swarm_agent_fallback"] = MARBLES_SWARM_FALLBACK_AGENT
    return record


def workflow_capabilities_payload() -> dict[str, Any]:
    """Describe every workflow's execution contract as one versioned payload.

    The research selection reflects the live env/config/manifest/builtin
    precedence at call time; unsupported configured tokens (e.g. a dead
    ``gemini`` still listed in an operator config) are reported in
    ``unsupported_configured`` instead of being silently dropped.
    """
    selection = resolve_research_runtime_config()
    return {
        "schema": WORKFLOW_CAPABILITIES_SCHEMA,
        "schema_version": WORKFLOW_CAPABILITIES_SCHEMA_VERSION,
        "agents": sorted(SUPPORTED_AGENTS),
        "research": {
            "supported_agents": list(SUPPORTED_RESEARCH_AGENTS),
            "default_agents": list(DEFAULT_RESEARCH_AGENTS),
            "selection": _selection_payload(selection),
        },
        "workflows": [
            _workflow_record(definition, selection)
            for definition in workflow_registry.workflow_lifecycle()
        ],
        "providers": provider_capabilities_payload(),
        "environments": environment_capabilities_payload(),
    }


def render_capabilities_lines(payload: dict[str, Any]) -> list[str]:
    """Human-readable projection of the capability payload for the bare CLI."""
    selection = payload["research"]["selection"]
    lines = [
        f"schema:   {payload['schema']} (v{payload['schema_version']})",
        f"agents:   {', '.join(payload['agents'])}",
        (
            "research: "
            f"lanes={', '.join(selection['agents']) or 'none'} "
            f"source={selection['source']} "
            f"synthesizer={selection['synthesizer']['agent'] or 'last-survivor'}"
        ),
    ]
    if selection["unsupported_configured"]:
        lines.append(
            "research: unsupported configured tokens: "
            + ", ".join(selection["unsupported_configured"])
        )
    lines.append("")
    header = f"{'workflow':<12} {'runtime_kind':<20} {'target':<13} policy"
    lines.append(header)
    lines.append("-" * len(header))
    for record in payload["workflows"]:
        lines.append(
            f"{record['name']:<12} {record['runtime_kind']:<20} "
            f"{record['execution_target']:<13} {record['requested_agent_policy']}"
        )
    providers = payload.get("providers") or {}
    if providers:
        lines.append("")
        header = f"{'provider':<8} {'exe':<5} {'model':<6} refused controls"
        lines.append(header)
        lines.append("-" * len(header))
        for name, record in providers.items():
            refused = sum(1 for cell in record["controls"] if not cell["supported"])
            lines.append(
                f"{name:<8} {'yes' if record['available'] else 'no':<5} "
                f"{'yes' if record['model_override']['supported'] else 'no':<6} "
                f"{refused}/{len(record['controls'])}"
            )
    environments = payload.get("environments") or {}
    if environments:
        lines.append("")
        for policy, record in environments.items():
            state = (
                "available"
                if record["available"]
                else f"unavailable: {record['reason']}"
            )
            lines.append(f"{record['label']:<16} {policy:<16} {state}")
    return lines
