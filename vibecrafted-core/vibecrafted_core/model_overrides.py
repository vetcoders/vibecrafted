"""Inject an operator-requested model pin into an agent's launch command."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

MODEL_OVERRIDE_FLAGS = {
    "agy": "--model",
    "claude": "--model",
    "codex": "-m",
    "cursor": "--model",
    "junie": "--model",
    # grok 1.0.21 documents both `-m` and `--model`; inject the long form to
    # match grok_spawn.sh, and treat `-m` as an existing pin.
    "grok": "--model",
}

MODEL_OVERRIDE_FLAG_ALIASES: dict[str, tuple[str, ...]] = {
    "grok": ("--model", "-m"),
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


def _existing_model_value(
    command: Sequence[str], flags: str | Sequence[str]
) -> tuple[bool, str | None]:
    """Return one unambiguous existing model value before ``--``.

    ``flags`` may be one injection flag or a set of aliases (Grok's
    ``--model`` / ``-m``). A request with multiple pins is deliberately not
    idempotent, even when the values happen to match.  The provider owns
    duplicate-flag semantics, so the adapter must reject that ambiguous
    command instead of reporting a pin as applied.  Likewise, a bare flag or
    an empty ``--model=`` is malformed rather than an invitation to insert
    another flag.
    """

    flag_list = (flags,) if isinstance(flags, str) else tuple(flags)
    values: list[str] = []
    index = 0
    while index < len(command):
        argument = command[index]
        if argument == "--":
            break
        matched = False
        for flag in flag_list:
            if argument == flag:
                if index + 1 >= len(command) or command[index + 1].startswith("-"):
                    raise ValueError("model_override_missing_existing_model")
                values.append(command[index + 1])
                index += 2
                matched = True
                break
            if flag.startswith("--") and argument.startswith(f"{flag}="):
                value = argument.removeprefix(f"{flag}=")
                if not value:
                    raise ValueError("model_override_missing_existing_model")
                values.append(value)
                index += 1
                matched = True
                break
        if not matched:
            index += 1

    if not values:
        return False, None
    if len(values) != 1:
        raise ValueError("model_override_ambiguous_existing_model")
    return True, values[0]


def _with_agy_model_override(
    command: Sequence[str], flag: str, requested: str
) -> list[str]:
    """Inject Agy's pin into its direct argv, refusing any shell wrapper.

    Agy's headless command is a plain argv (``agy … --print= --input-format
    stream-json --output-format stream-json``; the prompt travels on stdin).
    The retired ``bash -c … --print "$(cat)"`` wrapper is rejected instead of
    rewritten: a flag spliced after ``bash`` would be swallowed by the shell
    while the receipt claimed the pin reached Agy.
    """

    command_list = list(command)
    if not command_list or Path(command_list[0]).name != "agy":
        raise ValueError("model_override_unsupported_agy_command_shape")
    return _with_direct_model_override(command_list, flag, requested)


def _with_direct_model_override(
    command: Sequence[str],
    flag: str,
    requested: str,
    *,
    existing_flags: Sequence[str] | None = None,
) -> list[str]:
    """Inject one direct argv pin, rejecting a conflicting existing pin."""

    command_list = list(command)
    already_pinned, existing_model = _existing_model_value(
        command_list, existing_flags if existing_flags is not None else (flag,)
    )
    if already_pinned:
        if existing_model == requested:
            return command_list
        raise ValueError("model_override_conflicts_with_existing_model")
    if (
        Path(command_list[0]).name == "codex"
        and len(command_list) > 1
        and command_list[1] == "exec"
    ):
        return [command_list[0], command_list[1], flag, requested, *command_list[2:]]
    return [command_list[0], flag, requested, *command_list[1:]]


def _with_model_override(
    agent: str, command: Sequence[str], model_requested: str | None
) -> list[str]:
    """Splice the agent's model flag + value into ``command`` when supported.

    Returns ``command`` unchanged (as a list) when no model was requested or
    the command is empty. An explicit pin without a known model flag refuses. ``codex exec`` gets the
    flag inserted after the ``exec`` subcommand rather than at the head. Agy
    accepts only its direct argv; a shell wrapper fails closed so its receipt
    cannot claim a pin that never reached Agy.
    """
    requested = "" if model_requested is None else str(model_requested)
    command_list = list(command)
    flag = MODEL_OVERRIDE_FLAGS.get(agent)
    if requested.strip() and not flag:
        raise ValueError("requested model is unsupported by this provider adapter")
    if not requested.strip() or not command_list:
        return command_list
    aliases = MODEL_OVERRIDE_FLAG_ALIASES.get(agent, (flag,))
    if agent == "agy":
        return _with_agy_model_override(command_list, flag, requested)
    return _with_direct_model_override(
        command_list, flag, requested, existing_flags=aliases
    )
