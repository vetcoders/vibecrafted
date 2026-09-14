"""Per-stage crew casting: primary agent plus an ordered failover list.

Mission frontmatter used to accept one name per stage. That is still valid.
A failover list is now also valid:

    stage_agents: audit=claude,grok
    stage_agents:
      audit: [claude, grok]

The parse/validate API returns ``{stage_id: [primary, ...failover]}``. Dispatch
still launches the primary; it does not change fleet-per-cut behaviour.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .workflow import SUPPORTED_AGENTS
from .workflows.model import WorkflowManifest

StageCast = dict[str, list[str]]

# Split inline ``k=v`` / ``k: v`` pairs on commas that introduce the next key,
# so ``audit=claude,grok, review=codex`` is two stages, not four tokens.
_INLINE_PAIR_SPLIT = re.compile(r",\s*(?=[A-Za-z_][\w-]*\s*[=:])")


def parse_agent_names(raw: Any) -> list[str]:
    """Normalize a single name or failover list into an ordered unique list."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        names: list[str] = []
        seen: set[str] = set()
        for item in raw:
            for name in parse_agent_names(item):
                if name not in seen:
                    seen.add(name)
                    names.append(name)
        return names
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
        if not text:
            return []
    names = []
    seen = set()
    for part in text.split(","):
        name = part.strip().strip("\"'")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def primary_stage_agent(cast: Mapping[str, Any] | None, stage: str) -> str:
    """First name in the stage's failover list, or ``""`` if the stage is uncast."""
    names = parse_agent_names(dict(cast or {}).get(str(stage or "").strip()))
    return names[0] if names else ""


def primary_stage_map(cast: Mapping[str, Any] | None) -> dict[str, str]:
    """Project a cast to ``{stage: primary}`` for launch and schema persist."""
    primaries: dict[str, str] = {}
    for stage_id, value in dict(cast or {}).items():
        names = parse_agent_names(value)
        if names:
            primaries[str(stage_id)] = names[0]
    return primaries


def encode_stage_cast(cast: Mapping[str, Any] | None) -> dict[str, str]:
    """Schema-safe persist form: one name, or ``claude,grok`` for a failover list."""
    encoded: dict[str, str] = {}
    for stage_id, value in dict(cast or {}).items():
        names = parse_agent_names(value)
        if names:
            encoded[str(stage_id)] = ",".join(names)
    return encoded


def _mission_frontmatter_map(mission_text: str, field: str) -> dict[str, str]:
    """Walk YAML frontmatter for ``field:`` as an inline map or a nested block."""
    prefix = f"{field}:"
    lines = str(mission_text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    values: dict[str, str] = {}
    in_block = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if not in_block:
            if not stripped.startswith(prefix):
                continue
            inline = stripped.split(":", 1)[1].strip()
            if inline:
                for pair in _INLINE_PAIR_SPLIT.split(inline):
                    pair = pair.strip()
                    separator = "=" if "=" in pair else ":"
                    if separator not in pair:
                        continue
                    key, value = pair.split(separator, 1)
                    stage_key = key.strip()
                    raw = value.strip().strip('"')
                    if stage_key and raw:
                        values[stage_key] = raw
                break
            in_block = True
            continue
        if not line.startswith((" ", "\t")) or ":" not in stripped:
            break
        key, value = stripped.split(":", 1)
        stage_key = key.strip()
        raw = value.strip().strip('"')
        if stage_key:
            values[stage_key] = raw
    return values


def _mission_stage_agents(mission_text: str) -> StageCast:
    """Per-stage casting declared in the mission file's YAML frontmatter.

    Accepted shapes:

        ---
        stage_agents: scaffold=claude, review=codex
        ---

        ---
        stage_agents: audit=claude,grok
        ---

        ---
        stage_agents:
          scaffold: claude
          audit: [claude, grok]
        ---
    """
    parsed: StageCast = {}
    for stage_id, raw in _mission_frontmatter_map(mission_text, "stage_agents").items():
        names = parse_agent_names(raw)
        if names:
            parsed[stage_id] = names
    return parsed


def _mission_stage_models(mission_text: str) -> dict[str, str]:
    """Per-stage model pins declared in mission YAML frontmatter.

    Accepted shapes mirror stage_agents keys. Model names stay a single string
    (no failover list); the runtime only knows whether a runner has a model flag.
    """
    return _mission_frontmatter_map(mission_text, "stage_models")


def _validated_stage_agents(
    raw: Mapping[str, Any] | None, manifest: WorkflowManifest
) -> StageCast:
    """Fail fast at launch: a typo in any primary or failover name must not fly."""
    stage_ids = {stage.id for stage in manifest.stages}
    resolved: StageCast = {}
    for stage_id, agent in dict(raw or {}).items():
        stage_key = str(stage_id).strip()
        names = parse_agent_names(agent)
        if not stage_key or not names:
            continue
        if stage_key not in stage_ids:
            raise ValueError(
                f"stage_agents names unknown stage '{stage_key}' "
                f"for workflow {manifest.id}"
            )
        for agent_name in names:
            if agent_name not in SUPPORTED_AGENTS:
                raise ValueError(
                    f"stage_agents names unsupported agent '{agent_name}' "
                    f"for stage '{stage_key}' (supported: "
                    + ", ".join(sorted(SUPPORTED_AGENTS))
                    + ")"
                )
        resolved[stage_key] = names
    return resolved


def _validated_stage_models(
    raw: Mapping[str, Any] | None, manifest: WorkflowManifest
) -> dict[str, str]:
    """Manifest-gate optional model pins without whitelisting model names."""
    stage_ids = {stage.id for stage in manifest.stages}
    resolved: dict[str, str] = {}
    for stage_id, model in dict(raw or {}).items():
        stage_key = str(stage_id).strip()
        model_name = str(model or "").strip()
        if not stage_key or not model_name or stage_key not in stage_ids:
            continue
        resolved[stage_key] = model_name
    return resolved
