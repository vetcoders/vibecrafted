"""Inject an operator-requested model pin into an agent's launch command."""

from __future__ import annotations

from collections.abc import Sequence

MODEL_OVERRIDE_FLAGS = {
    "claude": "--model",
    "codex": "-m",
    "cursor": "--model",
}


def _model_override_receipt(
    agent: str, model_requested: str | None
) -> dict[str, object]:
    """Receipt fields for an operator-requested model pin.

    The model value is intentionally not validated: agent model catalogs change
    faster than this runtime. We only record whether this runner knows how to
    carry the request as a CLI flag.
    """

    requested = str(model_requested or "").strip()
    if not requested:
        return {}
    supported = agent in MODEL_OVERRIDE_FLAGS
    receipt: dict[str, object] = {
        "model_requested": requested,
        "model_override_supported": supported,
        "model_override_skipped": not supported,
    }
    if not supported:
        receipt["model_override_skip_reason"] = "unsupported_agent_model_flag"
    return receipt


def _with_model_override(
    agent: str, command: Sequence[str], model_requested: str | None
) -> list[str]:
    """Splice the agent's model flag + value into ``command`` when supported.

    Returns ``command`` unchanged (as a list) when no model was requested, the
    agent has no known flag, or the command is empty. ``codex exec`` gets the
    flag inserted after the ``exec`` subcommand rather than at the head.
    """
    requested = str(model_requested or "").strip()
    command_list = list(command)
    flag = MODEL_OVERRIDE_FLAGS.get(agent)
    if not requested or not flag or not command_list:
        return command_list
    if agent == "codex" and len(command_list) > 1 and command_list[1] == "exec":
        return [command_list[0], command_list[1], flag, requested, *command_list[2:]]
    return [command_list[0], flag, requested, *command_list[1:]]
