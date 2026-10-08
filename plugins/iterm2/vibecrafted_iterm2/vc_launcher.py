"""AutoLaunch entry script for the vibecrafted iTerm2 / locterm plugin.

This script is symlinked into
``~/Library/Application Support/iTerm2/Scripts/AutoLaunch/vc_launcher.py``
by :mod:`vibecrafted_iterm2.install_autolaunch` and is
executed by iTerm2 at startup inside iTerm2's vendored Python sandbox.
That sandbox is guaranteed to vendor ``iterm2`` and the Python stdlib,
nothing else — so this module imports nothing from the rest of
``vibecrafted_core`` at runtime.

What it does:

* Connects to the running iTerm2 / locterm daemon via the documented
  iTerm2 Python WebSocket API (``iterm2.async_get_app``).
* Tails ``$VIBECRAFTED_HOME/control_plane/events.jsonl`` over a
  long-lived ``tail -F`` subprocess and decodes ``spawn-update`` events.
* Updates a registered ``StatusBarComponent`` with the live active-run
  count and the latest finished agent/skill string.
* Posts iTerm2 native notifications on terminal spawn lifecycle
  transitions (launching → running → completed/failed).
* Leaves profile configuration alone at runtime. The installer writes an
  additive DynamicProfile with the managed triggers instead.
* Survives iTerm2 restarts and broken connections via exponential
  backoff reconnect.

GPL discipline: this process is separate from iTerm2 / locterm itself
and only talks to the daemon over the public WebSocket API. No static
linking, no source mixing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

try:
    import iterm2
except ImportError:  # sandbox path
    iterm2 = None

from . import (
    DEFAULT_RECONNECT_BACKOFF,
    EVENTS_JSONL_RELPATH,
    SPAWN_UPDATE_KIND,
    STDIO_LIMIT_BYTES,
)
from .vc_status_bar import VcStatusBarState, register_status_bar

_LOG = logging.getLogger("vibecrafted.iterm2")


def vibecrafted_home() -> Path:
    """Resolve the vibecrafted control-plane root.

    Mirrors :func:`vibecrafted_core.runtime_paths.vibecrafted_home` without
    importing it — the AutoLaunch sandbox cannot see vibecrafted_core.
    """
    override = os.environ.get("VIBECRAFTED_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".vibecrafted"


def events_jsonl_path() -> Path:
    """Full path to the control-plane events.jsonl tailed by this plugin."""
    return vibecrafted_home() / EVENTS_JSONL_RELPATH


def _parse_event_line(line: str) -> dict[str, Any] | None:
    """Decode one JSON-lines row; returns None on blank or malformed input."""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _is_spawn_update(event: dict[str, Any]) -> bool:
    """True if `event["kind"]` matches the spawn-update event kind."""
    return str(event.get("kind") or "") == SPAWN_UPDATE_KIND


def _format_completion_label(payload: dict[str, Any]) -> str:
    """Render an ``agent/skill state (exit N)`` label for a finished run."""
    agent = str(payload.get("agent") or "?")
    skill = str(payload.get("skill") or "?")
    state = str(payload.get("state") or "?")
    exit_code = payload.get("exit_code")
    suffix = f" (exit {exit_code})" if exit_code is not None else ""
    return f"{agent}/{skill} {state}{suffix}"


def _is_active_state(state: str) -> bool:
    """True for spawn states that count toward the live active-run tally."""
    return state in {"launching", "running"}


def _is_final_state(state: str) -> bool:
    """True for spawn states that terminate a run (success, failure, or stop)."""
    return state in {"completed", "failed", "stopped", "timed_out"}


async def _tail_events(
    path: Path,
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Long-lived ``tail -F`` over events.jsonl decoded as JSON lines.

    Uses asyncio.subprocess to stay non-blocking next to the iTerm2
    WebSocket loop. We deliberately avoid ``aiofiles`` because the iTerm2
    Python sandbox does not vendor third-party packages.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    proc = await asyncio.create_subprocess_exec(
        "tail",
        "-n",
        "0",
        "-F",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=STDIO_LIMIT_BYTES,
    )
    assert proc.stdout is not None
    try:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            event = _parse_event_line(raw.decode("utf-8", errors="replace"))
            if event is None:
                continue
            await on_event(event)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()


async def _post_notification(
    connection: Any,
    title: str,
    body: str,
) -> None:
    """Best-effort iTerm2 native notification.

    Different iterm2 package versions expose different notification
    surfaces; we degrade silently instead of crashing the AutoLaunch.
    """
    try:
        await iterm2.async_post_notification(connection, title=title, body=body)
        return
    except AttributeError:
        pass
    try:
        app = await iterm2.async_get_app(connection)
        if app is not None and getattr(app, "current_terminal_window", None):
            window = app.current_terminal_window
            tab = window.current_tab if window else None
            session = tab.current_session if tab else None
            if session is not None:
                await session.async_inject(f"\x1b]9;{title}: {body}\x07".encode())
    # iTerm RPC and session injection can raise API-specific exceptions; notification failure must
    # leave the plugin running and be recorded in its debug log.
    except Exception:  # noqa: BLE001
        _LOG.debug("notification fallback failed", exc_info=True)


class VcPluginRuntime:
    """Owns the plugin state shared between event tail + status bar.

    The runtime is intentionally cheap to construct and reset; on every
    reconnect we throw it away and rebuild so iTerm2 daemon restarts
    cannot leave us with stale state.
    """

    def __init__(self) -> None:
        """Construct empty state; `connection` is set by the caller after connect."""
        self.state = VcStatusBarState()
        self.connection: Any = None

    async def handle_event(self, event: dict[str, Any]) -> None:
        """Fold one decoded events.jsonl row into status-bar state.

        Non spawn-update events are ignored. Active states populate
        `active_runs`; final states clear the run, record the completion
        label, and fire a best-effort iTerm2 notification.
        """
        if not _is_spawn_update(event):
            return
        payload = dict(event.get("payload") or {})
        state = str(payload.get("state") or "")
        run_id = str(event.get("run_id") or "")
        if _is_active_state(state):
            self.state.active_runs[run_id] = payload
        elif _is_final_state(state):
            self.state.active_runs.pop(run_id, None)
            self.state.last_completion = _format_completion_label(payload)
            self.state.last_completion_payload = payload
            if self.connection is not None:
                await _post_notification(
                    self.connection,
                    title="vibecrafted",
                    body=self.state.last_completion,
                )
        await self.state.refresh()


async def _main_loop(connection: Any) -> None:
    """Register the status bar then tail events.jsonl forever (per connection)."""
    runtime = VcPluginRuntime()
    runtime.connection = connection

    await register_status_bar(connection, runtime.state)

    path = events_jsonl_path()
    _LOG.info("tailing %s", path)
    await _tail_events(path, runtime.handle_event)


async def _run_with_reconnect(
    backoff: Iterable[float] = DEFAULT_RECONNECT_BACKOFF,
) -> None:
    """Run `_main_loop` under `iterm2.async_main`, reconnecting on any failure.

    Loops forever with exponential backoff (per `backoff`, holding at the
    last value once exhausted); a clean iteration resets the attempt count.
    """
    if iterm2 is None:  # sandbox import guard
        raise RuntimeError(
            "iterm2 package is not available; this script must run inside "
            "iTerm2's vendored Python sandbox."
        )
    delays = list(backoff)
    attempt = 0
    while True:
        try:
            await iterm2.async_main(_main_loop)
        except KeyboardInterrupt:
            raise
        # The iTerm connection loop must reconnect after any RPC implementation failure;
        # KeyboardInterrupt is re-raised separately and reconnect failures retain tracebacks.
        except Exception:  # noqa: BLE001
            attempt += 1
            delay = delays[min(attempt - 1, len(delays) - 1)]
            _LOG.warning(
                "iTerm2 connection lost (attempt %d), reconnecting in %.1fs",
                attempt,
                delay,
                exc_info=True,
            )
            await asyncio.sleep(delay)
        else:
            attempt = 0


def main() -> int:
    """Entry point invoked by iTerm2 AutoLaunch."""
    logging.basicConfig(
        level=os.environ.get("VIBECRAFTED_PLUGIN_LOGLEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if iterm2 is None:
        sys.stderr.write(
            "vibecrafted iterm2 plugin: iterm2 package missing — "
            "run inside iTerm2's Python sandbox.\n"
        )
        return 2
    try:
        iterm2.run_until_complete(_run_with_reconnect)
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # script entry point
    raise SystemExit(main())
