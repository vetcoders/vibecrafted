"""Inject an operator-requested model pin into an agent's launch command."""

from __future__ import annotations

import shlex
from collections.abc import Sequence

MODEL_OVERRIDE_FLAGS = {
    "agy": "--model",
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

    requested = "" if model_requested is None else str(model_requested)
    if not requested.strip():
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


def _existing_model_value(command: Sequence[str], flag: str) -> tuple[bool, str | None]:
    """Return the existing value for ``flag`` without accepting a second pin."""

    for index, argument in enumerate(command):
        if argument == flag:
            return True, command[index + 1] if index + 1 < len(command) else None
        if flag.startswith("--") and argument.startswith(f"{flag}="):
            return True, argument.removeprefix(f"{flag}=")
    return False, None


def _with_agy_model_override(
    command: Sequence[str], flag: str, requested: str
) -> list[str]:
    """Inject Agy's pin into its verified ``bash -c`` stdin wrapper.

    Agy's print-mode adapter is intentionally a shell wrapper because the CLI
    requires ``--print`` to receive the prompt as an argument.  A top-level
    flag would be interpreted by ``bash`` rather than Agy, so only the exact
    canonical wrapper is accepted here.  Unknown wrappers fail closed instead
    of producing a receipt that says a model was applied when it was not.
    """

    command_list = list(command)
    if command_list and command_list[0] == "agy":
        return _with_direct_model_override(command_list, flag, requested)
    if len(command_list) != 3 or command_list[:2] != ["bash", "-c"]:
        raise ValueError("model_override_unsupported_agy_command_shape")

    script = command_list[2]
    try:
        script_argv = shlex.split(script)
    except ValueError as exc:
        raise ValueError("model_override_malformed_agy_command") from exc
    if not script_argv or script_argv[0] != "agy" or "--print" not in script_argv:
        raise ValueError("model_override_unsupported_agy_command_shape")

    already_pinned, existing_model = _existing_model_value(script_argv, flag)
    if already_pinned:
        if existing_model == requested:
            return command_list
        raise ValueError("model_override_conflicts_with_existing_model")

    # Keep the canonical prompt substitution byte-for-byte in the shell script;
    # only the model argv is rendered with shell-safe quoting.
    return ["bash", "-c", f"agy {shlex.join([flag, requested])}{script[3:]}"]


def _with_direct_model_override(
    command: Sequence[str], flag: str, requested: str
) -> list[str]:
    """Inject one direct argv pin, rejecting a conflicting existing pin."""

    command_list = list(command)
    already_pinned, existing_model = _existing_model_value(command_list, flag)
    if already_pinned:
        if existing_model == requested:
            return command_list
        raise ValueError("model_override_conflicts_with_existing_model")
    if command_list[0] == "codex" and len(command_list) > 1 and command_list[1] == "exec":
        return [command_list[0], command_list[1], flag, requested, *command_list[2:]]
    return [command_list[0], flag, requested, *command_list[1:]]


def _with_model_override(
    agent: str, command: Sequence[str], model_requested: str | None
) -> list[str]:
    """Splice the agent's model flag + value into ``command`` when supported.

    Returns ``command`` unchanged (as a list) when no model was requested, the
    agent has no known flag, or the command is empty. ``codex exec`` gets the
    flag inserted after the ``exec`` subcommand rather than at the head. Agy's
    special shell wrapper is rewritten inside ``bash -c``; unsupported wrapper
    shapes fail closed so its receipt cannot claim a pin that never reached Agy.
    """
    requested = "" if model_requested is None else str(model_requested)
    command_list = list(command)
    flag = MODEL_OVERRIDE_FLAGS.get(agent)
    if not requested.strip() or not flag or not command_list:
        return command_list
    if agent == "agy":
        return _with_agy_model_override(command_list, flag, requested)
    return _with_direct_model_override(command_list, flag, requested)
