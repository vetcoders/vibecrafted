"""iTerm2 / locterm StatusBarComponent that surfaces vibecrafted activity.

The component renders ``vc: N`` for the live active-spawn count and, when
a run finishes, appends a short ``last: agent/skill state`` tag. Clicking
the component opens the most recently finished run's transcript via
``open(1)``.

This module is loaded by :mod:`vc_launcher` inside iTerm2's vendored
Python sandbox, so its only third-party dependency is the ``iterm2``
package. Stdlib only otherwise.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

try:
    import iterm2
except ImportError:  # sandbox path
    iterm2 = None

from . import STATUS_BAR_COMPONENT_ID

_LOG = logging.getLogger("vibecrafted.iterm2.status_bar")


@dataclass
class VcStatusBarState:
    """Shared rendering state between the event tail and the component.

    The component callback reads ``active_runs`` and ``last_completion``;
    the event tail mutates them. ``refresh`` is wired to the iTerm2
    coroutine that re-evaluates the visible label.
    """

    active_runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_completion: str = ""
    last_completion_payload: dict[str, Any] = field(default_factory=dict)
    _refresh: Callable[[], Any] | None = None

    def render(self) -> str:
        """Build the status bar label: active count plus last-completion tag."""
        count = len(self.active_runs)
        head = f"vc: {count}"
        tail = self.last_completion
        if tail:
            return f"{head} · last: {tail}"
        return head

    async def refresh(self) -> None:
        """Trigger the iTerm2 status bar redraw via the registered callback.

        No-op if a component has not registered `_refresh` yet; swallows
        redraw errors so a transient iTerm2 API hiccup cannot crash the tail.
        """
        if self._refresh is None:
            return
        try:
            result = self._refresh()
            if hasattr(result, "__await__"):
                await result
        # The injected redraw coroutine can raise arbitrary plugin errors after a pane unmount;
        # refreshing the status bar must preserve the live session and log the failure.
        except Exception:  # noqa: BLE001
            _LOG.debug("status bar refresh failed", exc_info=True)


def _open_transcript(state: VcStatusBarState) -> bool:
    """Open the last completed run's transcript with `open(1)`.

    Returns False (no crash) when there is no transcript path or the file
    does not exist, or if the ``open`` subprocess fails to launch.
    """
    transcript = str(state.last_completion_payload.get("transcript") or "").strip()
    if not transcript or not os.path.exists(transcript):
        return False
    try:
        subprocess.Popen(["open", transcript])
        return True
    except OSError:
        _LOG.warning("could not open transcript %s", transcript, exc_info=True)
        return False


async def register_status_bar(connection: Any, state: VcStatusBarState) -> Any:
    """Register the vibecrafted StatusBarComponent against the iTerm2 daemon.

    Returns the underlying component handle so callers (typically the
    event tail) can keep a reference and trigger refreshes.
    """
    if iterm2 is None:  # sandbox guard
        raise RuntimeError("iterm2 package unavailable")

    component = iterm2.StatusBarComponent(
        short_description="vibecrafted",
        detailed_description="Live vibecrafted spawn activity",
        knobs=[],
        exemplar="vc: 0",
        update_cadence=1,
        identifier=STATUS_BAR_COMPONENT_ID,
    )

    @iterm2.StatusBarRPC
    async def coroutine(
        knobs: dict[str, Any],
    ) -> str:
        """Status bar RPC callback: current rendered label for this component."""
        return state.render()

    @iterm2.RPC
    async def on_click(session_id: str) -> None:
        """Status bar click handler: open the last completed run's transcript."""
        opened = _open_transcript(state)
        if not opened:
            _LOG.info(
                "status bar click: no transcript to open (last=%s)",
                state.last_completion or "<none>",
            )

    state._refresh = coroutine.async_redraw
    await component.async_register(
        connection,
        coroutine,
        onclick=on_click,
    )
    _LOG.info("registered status bar component %s", STATUS_BAR_COMPONENT_ID)
    return component
