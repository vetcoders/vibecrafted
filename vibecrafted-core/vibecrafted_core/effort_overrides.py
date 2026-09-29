"""Inject an operator-requested reasoning-effort pin into an agent's launch command.

Reasoning effort is a COST control, not a tuning nicety: on 2026-09-29 a six-run
fleet dispatched through ``codex exec`` silently ran gpt-6-astra at ``high``
because the provider CLI picked its own default (the ``model_reasoning_effort``
config key only governs interactive sessions). Founder order from the same
session: every agent takes ``--effort`` as a launch parameter.

Flag spellings verified against the installed CLIs' ``--help`` on 2026-09-29:

======  =============================  ==========================================
agent   spelling                       notes
======  =============================  ==========================================
claude  ``--effort <level>``           native session flag
agy     ``--effort <level>``           low|medium|high|max
grok    ``--reasoning-effort <level>``
junie   ``--effort=<level>``           single ``=`` token; low|medium|high
codex   ``-c model_reasoning_effort=`` config override on ``exec``; the config
                                       file default does NOT reach exec runs
======  =============================  ==========================================

kimi exposes no reasoning-effort control in its CLI, and cursor only spells
effort inside a composite ``--model`` bracket; both stay honest, receipted
skips instead of silent drops or hard refusals — a mixed-agent wave must not
die because one provider lacks the knob.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .model_overrides import _existing_model_value

# style -> injection shape:
#   flag          two argv tokens after the binary: FLAG VALUE
#   assign        one argv token after the binary: FLAG=VALUE
#   codex_config  after the ``exec`` subcommand: -c model_reasoning_effort=VALUE
EFFORT_OVERRIDE_STYLES: dict[str, tuple[str, str]] = {
    "claude": ("flag", "--effort"),
    "agy": ("flag", "--effort"),
    "grok": ("flag", "--reasoning-effort"),
    "junie": ("assign", "--effort"),
    "codex": ("codex_config", "model_reasoning_effort"),
    "copilot": ("flag", "--reasoning-effort"),
}

_CODEX_EFFORT_PREFIX = "model_reasoning_effort="


def _effort_override_receipt(
    agent: str, effort_requested: str | None
) -> dict[str, object]:
    """Receipt fields for an operator-requested effort pin.

    The effort value is intentionally not validated: provider level catalogs
    (low/medium/high/max/xhigh/…) drift faster than this runtime. We only
    record whether this runner knows how to carry the request as a CLI flag.
    """

    requested = "" if effort_requested is None else str(effort_requested)
    if not requested.strip():
        return {}
    supported = agent in EFFORT_OVERRIDE_STYLES
    receipt: dict[str, object] = {
        "effort_requested": requested,
        "effort_override_supported": supported,
        "effort_override_skipped": not supported,
    }
    if not supported:
        receipt["effort_override_skip_reason"] = "unsupported_agent_effort_flag"
    return receipt


def _existing_codex_effort(command: Sequence[str]) -> tuple[bool, str | None]:
    """Return one unambiguous existing ``-c model_reasoning_effort=`` value."""

    values: list[str] = []
    for argument in command:
        if argument == "--":
            break
        # The override only ever travels as the VALUE token of ``-c``/
        # ``--config``; scanning for the ``model_reasoning_effort=`` prefix
        # alone keeps one hit per pin (a flag lookahead would double-count).
        if argument.startswith(_CODEX_EFFORT_PREFIX):
            values.append(argument.removeprefix(_CODEX_EFFORT_PREFIX))
    if not values:
        return False, None
    if len(values) != 1 or not values[0]:
        raise ValueError("effort_override_ambiguous_existing_effort")
    return True, values[0]


def _with_codex_effort_override(command: Sequence[str], requested: str) -> list[str]:
    """Insert ``-c model_reasoning_effort=<v>`` after ``codex exec``."""

    command_list = list(command)
    already_pinned, existing = _existing_codex_effort(command_list)
    if already_pinned:
        if existing == requested:
            return command_list
        raise ValueError("effort_override_conflicts_with_existing_effort")
    override = f"{_CODEX_EFFORT_PREFIX}{requested}"
    if (
        Path(command_list[0]).name == "codex"
        and len(command_list) > 1
        and command_list[1] == "exec"
    ):
        return [command_list[0], command_list[1], "-c", override, *command_list[2:]]
    return [command_list[0], "-c", override, *command_list[1:]]


def _with_effort_override(
    agent: str, command: Sequence[str], effort_requested: str | None
) -> list[str]:
    """Splice the agent's effort flag + value into ``command`` when supported.

    Returns ``command`` unchanged (as a list) when no effort was requested,
    the command is empty, or the provider has no reasoning-effort control —
    the skip is receipted by :func:`_effort_override_receipt`, never silent
    at the launch-meta level and never a refusal that kills a mixed wave.
    A conflicting existing pin refuses; the provider owns duplicate-flag
    semantics, so an ambiguous command must not claim the pin as applied.
    Agy accepts only its direct argv (same contract as its model pin): a
    shell wrapper fails closed so the receipt cannot claim a pin that never
    reached the agent.
    """

    requested = "" if effort_requested is None else str(effort_requested).strip()
    command_list = list(command)
    if not requested or not command_list:
        return command_list
    style_entry = EFFORT_OVERRIDE_STYLES.get(agent)
    if style_entry is None:
        return command_list
    style, flag = style_entry
    if style == "codex_config":
        return _with_codex_effort_override(command_list, requested)
    if agent == "agy" and Path(command_list[0]).name != "agy":
        raise ValueError("effort_override_unsupported_agy_command_shape")
    already_pinned, existing = _existing_model_value(command_list, (flag,))
    if already_pinned:
        if existing == requested:
            return command_list
        raise ValueError("effort_override_conflicts_with_existing_effort")
    if style == "assign":
        return [command_list[0], f"{flag}={requested}", *command_list[1:]]
    return [command_list[0], flag, requested, *command_list[1:]]
