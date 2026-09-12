"""Resolve which research agents/models/synthesizer a run uses, layered from
legacy TOML, ``research.yaml``, environment variables, and explicit overrides.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

from .runtime_paths import vibecrafted_home

SUPPORTED_RESEARCH_AGENTS = ("claude", "codex", "agy", "junie", "grok", "cursor")
DEFAULT_RESEARCH_AGENTS = ("claude", "codex", "agy")


@dataclass(frozen=True)
class ResearchAgentSelection:
    """Resolved research-agent roster plus per-lane/synthesizer model overrides."""

    agents: tuple[str, ...]
    source: str
    ignored: tuple[str, ...] = ()
    lane_models: Mapping[str, str] | None = None
    synthesizer: str = ""
    synthesizer_model: str = ""
    synthesizer_source: str = ""

    def lane_model(self, agent: str, global_model: str = "") -> str:
        """Model for one lane: ``global_model`` wins, else the lane's own model."""
        if global_model:
            return global_model
        return str((self.lane_models or {}).get(agent, "")).strip()

    def synthesis_model(self, global_model: str = "") -> str:
        """Model for the synthesis step: ``global_model`` wins, else configured."""
        return global_model or self.synthesizer_model


def research_yaml_path() -> Path:
    """Resolve the ``research.yaml`` path: env override, else vibecrafted home."""
    raw = os.environ.get("VIBECRAFTED_RESEARCH_CONFIG", "").strip()
    if raw:
        return Path(raw).expanduser()
    return vibecrafted_home() / "config" / "research.yaml"


def _strip_comment(line: str) -> str:
    """Strip a trailing ``#`` comment from one YAML-ish line, honoring quotes."""
    in_quote = ""
    for index, char in enumerate(line):
        if char in {"'", '"'}:
            in_quote = "" if in_quote == char else char if not in_quote else in_quote
        if char == "#" and not in_quote:
            return line[:index]
    return line


def _scalar(value: str) -> Any:
    """Coerce a raw YAML-ish scalar string into str/bool/int/list."""
    value = value.strip()
    if not value:
        return ""
    if value[0:1] in {"'", '"'} and value[-1:] == value[0]:
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part.strip()) for part in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        return value


def _read_runtime_yaml(path: Path) -> dict[str, Any]:
    """Hand-rolled minimal YAML reader for ``research.yaml``'s known shape.

    Parses only the ``lanes`` (list of agent/model rows), ``models``, and
    ``synthesizer`` sections plus top-level scalars; not a general YAML parser.
    Returns ``{}`` when the file is absent or unreadable.
    """
    if not path.is_file():
        return {}
    result: dict[str, Any] = {"lanes": [], "models": {}, "synthesizer": {}}
    section = ""
    current_lane: dict[str, Any] | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"Failed to read research runtime config: {path}: {exc}", file=sys.stderr)
        return {}
    for raw_line in lines:
        line = _strip_comment(raw_line).rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if not raw_line.startswith((" ", "\t")):
            current_lane = None
            if stripped.endswith(":"):
                section = stripped[:-1].strip()
                continue
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                key = key.strip()
                parsed = _scalar(value)
                if key == "synthesizer" and isinstance(parsed, str):
                    result["synthesizer"] = {"agent": parsed}
                else:
                    result[key] = parsed
                section = ""
            continue
        if section == "lanes" and stripped.startswith("-"):
            current_lane = {}
            result["lanes"].append(current_lane)
            body = stripped[1:].strip()
            if body and ":" in body:
                key, value = body.split(":", 1)
                current_lane[key.strip()] = _scalar(value)
            continue
        if section == "lanes" and current_lane is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current_lane[key.strip()] = _scalar(value)
            continue
        if section in {"models", "synthesizer"} and ":" in stripped:
            key, value = stripped.split(":", 1)
            bucket = result.get(section)
            if isinstance(bucket, dict):
                bucket[key.strip()] = _scalar(value)
    return result


def _split_agent_tokens(raw: object) -> list[str]:
    """Split a comma/space-separated agent-list string, or an iterable, into tokens."""
    if isinstance(raw, str):
        return [
            token.strip() for token in raw.replace(",", " ").split() if token.strip()
        ]
    if isinstance(raw, Iterable):
        return [str(token).strip() for token in raw if str(token).strip()]
    return []


def _select_supported_agents(
    tokens: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Filter tokens to supported, deduplicated research agents.

    Returns ``(agents, ignored)`` — unsupported tokens are reported, not raised.
    """
    agents: list[str] = []
    ignored: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        agent = token.strip().lower()
        if not agent:
            continue
        if agent not in SUPPORTED_RESEARCH_AGENTS:
            ignored.append(agent)
            continue
        if agent in seen:
            continue
        seen.add(agent)
        agents.append(agent)
    return tuple(agents), tuple(ignored)


def _read_legacy_toml_agents(path: Path) -> tuple[str, ...]:
    """Read ``runtime.picking.research.default_agents`` from a legacy TOML config."""
    if not path.is_file():
        return ()
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"Failed to read research agent config: {path}: {exc}", file=sys.stderr)
        return ()
    raw = (
        data.get("runtime", {})
        .get("picking", {})
        .get("research", {})
        .get("default_agents", [])
    )
    return tuple(_split_agent_tokens(raw))


def _legacy_toml_paths() -> tuple[Path, ...]:
    """Candidate legacy ``install.toml``/``config.toml`` paths, in lookup order."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    paths: list[Path] = [config_home / "vibecrafted" / "config.toml"]
    for candidate in (
        os.environ.get("VIBECRAFTED_ROOT", ""),
        str(Path.cwd()),
        str(Path(os.environ.get("VIBECRAFTED_TOOLS_HOME", "")) / "vibecrafted-current")
        if os.environ.get("VIBECRAFTED_TOOLS_HOME")
        else "",
    ):
        if candidate:
            paths.append(Path(candidate).expanduser() / "install.toml")
    return tuple(paths)


def _yaml_lanes(
    data: Mapping[str, Any],
) -> tuple[tuple[str, ...], dict[str, str], tuple[str, ...]]:
    """Derive (agents, per-agent models, ignored tokens) from parsed YAML data.

    Prefers the ``lanes`` list form (each row an agent + optional model,
    ``enabled: false`` rows skipped); falls back to a flat ``agents`` list.
    ``lane_count`` truncates the final roster when present.
    """
    models: dict[str, str] = {}
    ignored: list[str] = []
    agents: list[str] = []
    lane_rows = data.get("lanes")
    if isinstance(lane_rows, list):
        for row in lane_rows:
            if not isinstance(row, Mapping):
                continue
            if row.get("enabled") is False:
                continue
            agent = str(row.get("agent") or "").strip().lower()
            selected, bad = _select_supported_agents([agent])
            ignored.extend(bad)
            if not selected:
                continue
            agent = selected[0]
            if agent not in agents:
                agents.append(agent)
            model = str(row.get("model") or "").strip()
            if model:
                models[agent] = model
    else:
        selected, bad = _select_supported_agents(
            _split_agent_tokens(data.get("agents", []))
        )
        agents = list(selected)
        ignored.extend(bad)
    raw_models = data.get("models")
    if isinstance(raw_models, Mapping):
        for agent, model in raw_models.items():
            key = str(agent).strip().lower()
            if key in SUPPORTED_RESEARCH_AGENTS and str(model).strip():
                models[key] = str(model).strip()
    lane_count = data.get("lane_count")
    if isinstance(lane_count, int) and lane_count > 0:
        agents = agents[:lane_count]
    return tuple(agents), models, tuple(ignored)


def _yaml_synthesizer(data: Mapping[str, Any]) -> tuple[str, str]:
    """Derive (synthesizer agent, synthesizer model) from parsed YAML data."""
    raw = data.get("synthesizer")
    if isinstance(raw, str):
        return raw.strip().lower(), ""
    if isinstance(raw, Mapping):
        return (
            str(raw.get("agent") or "").strip().lower(),
            str(raw.get("model") or "").strip(),
        )
    return (
        str(data.get("synthesizer_agent") or "").strip().lower(),
        str(data.get("synthesizer_model") or "").strip(),
    )


def resolve_research_runtime_config(
    *,
    override_agents: Iterable[str] = (),
    synthesizer: str = "",
    synthesizer_model: str = "",
) -> ResearchAgentSelection:
    """Resolve the full research runtime config, layering in precedence order:

    built-in default -> legacy TOML -> ``research.yaml`` -> env vars ->
    explicit ``override_agents``/``synthesizer``/``synthesizer_model`` args.
    Each layer only applies when it has something to say; unsupported agent
    tokens are collected into ``ignored`` rather than raising.
    """
    agents: tuple[str, ...] = DEFAULT_RESEARCH_AGENTS
    models: dict[str, str] = {}
    ignored: tuple[str, ...] = ()
    source = "builtin-default"
    synth_agent = ""
    synth_model = ""
    synth_source = ""

    for path in _legacy_toml_paths():
        legacy_agents = _read_legacy_toml_agents(path)
        if legacy_agents:
            agents, ignored = _select_supported_agents(legacy_agents)
            source = str(path)
            break

    yaml_path = research_yaml_path()
    yaml_data = _read_runtime_yaml(yaml_path)
    if yaml_data:
        yaml_agents, yaml_models, yaml_ignored = _yaml_lanes(yaml_data)
        if yaml_agents:
            agents = yaml_agents
            source = str(yaml_path)
        models.update(yaml_models)
        ignored = (*ignored, *yaml_ignored)
        synth_agent, synth_model = _yaml_synthesizer(yaml_data)
        if synth_agent or synth_model:
            synth_source = str(yaml_path)

    env_agents = os.environ.get("VIBECRAFTED_RESEARCH_AGENTS", "").strip()
    if env_agents:
        agents, env_ignored = _select_supported_agents(_split_agent_tokens(env_agents))
        ignored = (*ignored, *env_ignored)
        source = "env:VIBECRAFTED_RESEARCH_AGENTS"

    override_tokens = tuple(
        str(agent).strip() for agent in override_agents if str(agent).strip()
    )
    if override_tokens:
        agents, override_ignored = _select_supported_agents(override_tokens)
        ignored = (*ignored, *override_ignored)
        source = "positional-override"
        if not synthesizer:
            synthesizer = agents[0] if agents else ""

    env_synth = os.environ.get("VIBECRAFTED_RESEARCH_SYNTHESIZER", "").strip()
    if env_synth:
        synthesizer = env_synth
        synth_source = "env:VIBECRAFTED_RESEARCH_SYNTHESIZER"
    if synthesizer:
        selected, bad = _select_supported_agents([synthesizer])
        ignored = (*ignored, *bad)
        synth_agent = selected[0] if selected else ""
        synth_source = synth_source or "explicit"
    if synthesizer_model:
        synth_model = synthesizer_model
    env_synth_model = os.environ.get(
        "VIBECRAFTED_RESEARCH_SYNTHESIZER_MODEL", ""
    ).strip()
    if env_synth_model:
        synth_model = env_synth_model

    return ResearchAgentSelection(
        agents=agents,
        source=source,
        ignored=tuple(dict.fromkeys(ignored)),
        lane_models=models,
        synthesizer=synth_agent,
        synthesizer_model=synth_model,
        synthesizer_source=synth_source,
    )
