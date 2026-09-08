"""Admit Cursor permission flags via the shared Cursor CLI surface probe.

Marbles and other G3-owned launchers must not hardcode ``--force --trust``.
Flag admission is owned by Cursor commit ``7597d881``
(``probe_cursor_cli_surface`` / ``require_cursor_flags``). This module is the
caller, not a second probe. Integrator must land that Cursor baton together
with the G3 wiring.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

CURSOR_PROBE_COMMIT = "7597d88195dcf1cf17f9f36996e5f29e6dcafde5"
REQUIRED_CURSOR_FLAGS: tuple[str, ...] = ("--force", "--trust")


def cursor_permission_flags(*, timeout: float | None = None) -> tuple[str, ...]:
    """Return Cursor bypass flags only after the shared probe confirms them.

    Raises ``RuntimeError`` when the Cursor probe is not yet integrated, and
    propagates ``ValueError`` when the probe refuses (nonzero/empty ``--help``,
    error prose that happens to name the flags, missing flags).
    """
    try:
        from vibecrafted_core.continuity.capabilities import (
            probe_cursor_cli_surface,
            require_cursor_flags,
        )
    except ImportError as exc:
        raise RuntimeError(
            "cursor permission flags require the shared Cursor capability probe "
            f"({CURSOR_PROBE_COMMIT[:8]}): {exc}. Integrator must land Cursor "
            "admission with this G3 wiring; refusing to hardcode --force/--trust."
        ) from exc
    if timeout is None:
        timeout = float(
            os.environ.get("VIBECRAFTED_CURSOR_PROBE_TIMEOUT_S", "10") or "10"
        )
    surface = probe_cursor_cli_surface(timeout=timeout, refresh=True)
    admitted = require_cursor_flags(
        REQUIRED_CURSOR_FLAGS, surface, permissions="bypass"
    )
    return tuple(admitted)


def cursor_permission_flag_string(*, timeout: float | None = None) -> str:
    """Shell-facing form of :func:`cursor_permission_flags`."""
    flags: Sequence[str] = cursor_permission_flags(timeout=timeout)
    return " ".join(flags)
