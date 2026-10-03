from __future__ import annotations

from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
TERMINAL_POLICY = REPO_ROOT / "config/vc-terminal/vibecrafted.toml"


def _binding_for_native_event(
    policy: dict[str, object], *, logical_key: str, modifiers: str
) -> dict[str, object] | None:
    """Select the binding as vc-terminal's non-Alt matcher sees a key event."""
    keyboard = policy["keyboard"]
    assert isinstance(keyboard, dict)
    bindings = keyboard["bindings"]
    assert isinstance(bindings, list)
    for binding in bindings:
        assert isinstance(binding, dict)
        if (
            binding.get("key", "").lower() == logical_key.lower()
            and binding.get("mods") == modifiers
        ):
            return binding
    return None


def test_native_cmd_shift_period_emits_quick_command_csi_u_sequence() -> None:
    """Keep the shipped policy aligned with the macOS key event vc-terminal matches.

    The native event is physical Period but logical `>` while Command and Shift
    are held.  vc-terminal only uses key_without_modifiers for Alt bindings, so
    a `Period`/`.` config trigger cannot dispatch this command.
    """
    policy = tomllib.loads(TERMINAL_POLICY.read_text(encoding="utf-8"))

    binding = _binding_for_native_event(
        policy, logical_key=">", modifiers="Command|Shift"
    )

    assert binding is not None
    assert binding["chars"] == "\x1b[46;10u"
