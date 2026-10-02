"""Pre-baked iTerm2 / locterm Triggers that auto-tag panes by agent + skill.

Each trigger is a regex that matches text emitted by a running agent
(claude, codex, gemini, ...) and an iTerm2 action that runs in response.
The installer writes them into the additive ``Vibecrafted`` Dynamic
Profile; runtime code must not mutate the user's default profile.

iTerm2 Triggers schema reference (stable since iTerm2 3.4):
https://iterm2.com/documentation-triggers.html
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_LOG = logging.getLogger("vibecrafted.iterm2.triggers")

# iTerm2 trigger action identifiers (stable keys in the profile dict).
ACTION_SET_USER_VAR = "SetUserVarTrigger"
ACTION_HIGHLIGHT_LINE = "HighlightLineTrigger"
ACTION_BOUNCE = "BounceTrigger"


@dataclass(frozen=True)
class VibecraftedTrigger:
    """A trigger row understood by both iTerm2 and the test fixtures."""

    name: str
    regex: str
    action: str
    parameter: str

    def to_iterm2_dict(self) -> dict[str, Any]:
        """Encode the trigger in iTerm2's profile dict format."""
        return {
            "name": self.name,
            "regex": self.regex,
            "action": self.action,
            "parameter": self.parameter,
            "partial": True,
            "enabled": True,
        }


VIBECRAFTED_TRIGGERS: tuple[VibecraftedTrigger, ...] = (
    VibecraftedTrigger(
        name="vibecrafted: codex started",
        regex=r"Codex\s+(?P<skill>\S+)\s+started",
        action=ACTION_SET_USER_VAR,
        parameter="vc_agent=codex/\\1",
    ),
    VibecraftedTrigger(
        name="vibecrafted: claude started",
        regex=r"Claude\s+(?P<skill>\S+)\s+started",
        action=ACTION_SET_USER_VAR,
        parameter="vc_agent=claude/\\1",
    ),
    VibecraftedTrigger(
        name="vibecrafted: gemini started",
        regex=r"Gemini\s+(?P<skill>\S+)\s+started",
        action=ACTION_SET_USER_VAR,
        parameter="vc_agent=gemini/\\1",
    ),
    VibecraftedTrigger(
        name="vibecrafted: agent done",
        regex=r"(?P<agent>Codex|Claude|Gemini)\s+.*exit\s+0",
        action=ACTION_SET_USER_VAR,
        parameter="vc_agent=\\1/done",
    ),
    VibecraftedTrigger(
        name="vibecrafted: marbles iteration",
        regex=r"marbles\s+loop\s+(?P<n>\d+)",
        action=ACTION_HIGHLIGHT_LINE,
        parameter="0xffff00ff",
    ),
    VibecraftedTrigger(
        name="vibecrafted: gate failed",
        regex=r"(FAIL|FAILED|error:)",
        action=ACTION_BOUNCE,
        parameter="bounce",
    ),
)


def triggers_as_iterm2_payload(
    triggers: Iterable[VibecraftedTrigger],
) -> list[dict[str, Any]]:
    """Encode a sequence of triggers as the iTerm2 profile ``Triggers`` list."""
    return [t.to_iterm2_dict() for t in triggers]


async def apply_triggers_to_default_profile(
    connection: Any,
    triggers: Iterable[VibecraftedTrigger],
) -> bool:
    """Replace the vibecrafted-managed triggers on the default profile.

    We only own triggers whose ``name`` starts with ``vibecrafted:``; any
    other triggers the operator added by hand are preserved verbatim.
    Returns True on success, False on a best-effort fallback (the
    AutoLaunch should keep going either way).
    """
    try:
        import iterm2
    except ImportError as exc:  # sandbox guard
        raise RuntimeError("iterm2 package unavailable") from exc

    if iterm2 is None:  # type narrow for unusual import hooks
        raise RuntimeError("iterm2 package unavailable")

    try:
        profiles = await iterm2.PartialProfile.async_query(connection)
    # iTerm profile RPC exceptions vary by API version; profile query failure must return False and
    # retain a debug traceback rather than terminate the plugin.
    except Exception:  # noqa: BLE001
        _LOG.debug("could not query iTerm2 profiles", exc_info=True)
        return False

    default_profile = None
    for partial in profiles:
        if getattr(partial, "default_profile", False):
            default_profile = partial
            break
    if default_profile is None and profiles:
        default_profile = profiles[0]
    if default_profile is None:
        _LOG.info("no iTerm2 profiles found; skipping trigger install")
        return False

    try:
        full_profile = await default_profile.async_get_full_profile()
    # Loading the default profile crosses iTerm RPC versions; an API exception must return False
    # with a debug traceback before any trigger replacement.
    except Exception:  # noqa: BLE001
        _LOG.debug("full profile load failed", exc_info=True)
        return False

    existing = list(getattr(full_profile, "triggers", None) or [])
    preserved = [
        row
        for row in existing
        if not str(row.get("name") or "").startswith("vibecrafted:")
    ]
    new_payload = preserved + triggers_as_iterm2_payload(triggers)

    try:
        await full_profile.async_set_triggers(new_payload)
    # The iTerm trigger-write RPC may raise API-specific errors; failure must return False with a
    # debug traceback instead of claiming trigger installation.
    except Exception:  # noqa: BLE001
        _LOG.debug("async_set_triggers failed", exc_info=True)
        return False

    _LOG.info(
        "installed %d vibecrafted triggers on profile %r",
        len(VIBECRAFTED_TRIGGERS),
        getattr(full_profile, "name", "<unknown>"),
    )
    return True
