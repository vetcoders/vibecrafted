"""FastMCP server exposing vibecrafted-core as the third sense (ground truth).

The server is intentionally a thin wrapper. v0.1 closes the cold-start
contract:

    mcp__loctree-mcp__context()        # perception (external)
    mcp__aicx-mcp__aicx_intents()      # intentions (external)
    mcp__vibecrafted__vc_repo_full()   # ground truth (this server)

It also surfaces a slim ``vc_init`` synthesis call so a single tool
invocation can hand an agent a usable cold-start brief without dragging
in heavyweight context. v0.2 will grow ``vc_init`` into the full
synthesis layer (live failure score, unmade decisions, unverified
claims, cross-machine drift) — v0.1 ships the wiring and stubs.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from vibecrafted_core import (
    capabilities as _capabilities,
)
from vibecrafted_core import (
    control_plane as _control_plane,
)
from vibecrafted_core import (
    doctor as _doctor,
)
from vibecrafted_core import (
    git as _git,
)
from vibecrafted_core import (
    lifecycle_control as _lifecycle_control,
)
from vibecrafted_core import (
    server_observation as _server_observation,
)
from vibecrafted_core import (
    workflow as _workflow,
)
from vibecrafted_core.lifecycle_runner import (
    LIFECYCLE_SCHEMA_ID as _LIFECYCLE_SCHEMA_ID,
)
from vibecrafted_core.lifecycle_runner import (
    LifecycleSupervisor as _LifecycleSupervisor,
)
from vibecrafted_core.package_resources import resource_path as _core_resource_path

from .synthesis import (
    live_failure_score as _live_failure_score,
)
from .synthesis import (
    unmade_decisions as _unmade_decisions,
)
from .synthesis import (
    unverified_claims as _unverified_claims,
)

SLIM_MAX_COMMITS = 5
SLIM_MAX_DOCTOR_FINDINGS = 8
SLIM_BUDGET_BYTES = 5 * 1024
OBSERVE_MAX_BYTES = 64 * 1024
OBSERVE_RESULT_MAX_BYTES = 64 * 1024
OBSERVE_MAX_EVENTS = 100


@contextmanager
def _override_vibecrafted_home(home: str | None) -> Iterator[None]:
    """Temporarily set VIBECRAFTED_HOME for the wrapped call.

    vibecrafted-core resolves the operator home through the
    ``VIBECRAFTED_HOME`` env var, so this is the single supported way to
    point the control plane at an alternate home directory from the MCP
    surface.
    """
    if not home:
        yield
        return
    previous = os.environ.get("VIBECRAFTED_HOME")
    os.environ["VIBECRAFTED_HOME"] = str(Path(home).expanduser())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("VIBECRAFTED_HOME", None)
        else:
            os.environ["VIBECRAFTED_HOME"] = previous


def _trim_recent_commits(state: dict[str, Any], limit: int) -> dict[str, Any]:
    """Return ``state`` with ``recent_commits`` capped to ``limit`` entries.

    Leaves ``state`` untouched (returns it as-is) when already within limit.
    """
    commits = state.get("recent_commits") or []
    if len(commits) > limit:
        state = dict(state)
        state["recent_commits"] = commits[:limit]
    return state


def _doctor_payload(slim: bool) -> dict[str, Any]:
    """Return a doctor summary, degrading gracefully when unavailable.

    ``doctor_run`` reaches into ``scripts/vetcoders_install.py`` which is
    only present in a vibecrafted source checkout. When the package is
    consumed standalone (e.g. installed from PyPI in a foreign repo) the
    import fails — we surface that as a structured ``unavailable`` record
    rather than crashing the whole tool call.
    """
    try:
        findings = _doctor.doctor_run()
    except (ImportError, ModuleNotFoundError) as exc:
        return {
            "ok": 0,
            "warnings": 0,
            "failures": 0,
            "healthy": True,
            "unavailable": True,
            "reason": f"{type(exc).__name__}: {exc}",
            "findings": [],
        }
    payload = _doctor.doctor_summary(findings)
    if slim:
        payload = dict(payload)
        payload["findings"] = payload["findings"][:SLIM_MAX_DOCTOR_FINDINGS]
    return payload


def _filter_events_by_run(
    events: list[dict[str, Any]], run_id: str, limit: int
) -> list[dict[str, Any]]:
    """Return the first ``limit`` events in ``events`` matching ``run_id``."""
    matched: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("run_id") or "") == run_id:
            matched.append(event)
            if len(matched) >= limit:
                break
    return matched


def _read_run_event_tail(
    run_id: str, home: str | None, limit: int = 50
) -> list[dict[str, Any]]:
    """Walk the global event stream and return events for a single run.

    The core helper exposes a global tail without a ``run_id`` filter, so
    we read a generous window and filter manually. The stream is
    append-only and small in practice (operator-scale, not telemetry-
    scale), so this stays cheap.
    """
    with _override_vibecrafted_home(home):
        stream = _control_plane.event_stream_path()
        if not stream.exists():
            return []
        try:
            text = stream.read_text(encoding="utf-8")
        except OSError:
            return []
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return _filter_events_by_run(list(reversed(events)), run_id, limit)


def _clamp_int(value: int | None, default: int, ceiling: int) -> int:
    """Coerce ``value`` to int (falling back to ``default``), clamped to ``[0, ceiling]``."""
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, 0), ceiling)


def _bounded_text_read(
    path_value: str,
    *,
    offset: int | None = 0,
    max_bytes: int = OBSERVE_MAX_BYTES,
) -> dict[str, Any]:
    """Read at most ``max_bytes`` from ``path_value`` starting at byte offset."""
    start = _clamp_int(offset, 0, 2**63 - 1)
    limit = _clamp_int(max_bytes, OBSERVE_MAX_BYTES, OBSERVE_MAX_BYTES)
    payload: dict[str, Any] = {
        "path": path_value,
        "offset": start,
        "next_offset": start,
        "bytes": 0,
        "truncated": False,
        "text": "",
    }
    if not path_value:
        return payload

    path = Path(path_value)
    try:
        size = path.stat().st_size
    except OSError:
        return payload

    if offset is None:
        start = max(size - limit, 0)
    start = min(start, size)
    payload["offset"] = start
    payload["next_offset"] = start
    if limit == 0 or start >= size:
        payload["truncated"] = start < size
        return payload

    to_read = min(limit, size - start)
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read(to_read)
    except OSError:
        return payload

    next_offset = start + len(chunk)
    payload.update(
        {
            "next_offset": next_offset,
            "bytes": len(chunk),
            "truncated": start > 0 or next_offset < size,
            "text": chunk.decode("utf-8", errors="replace"),
        }
    )
    return payload


def _event_to_payload(event: Any) -> dict[str, Any]:
    """Flatten a control-plane event object into a JSON-serializable dict."""
    return {
        "cursor": str(getattr(event, "cursor", "0") or "0"),
        "ts": str(getattr(event, "ts", "") or ""),
        "run_id": str(getattr(event, "run_id", "") or ""),
        "kind": str(getattr(event, "kind", "") or ""),
        "message": str(getattr(event, "message", "") or ""),
        "payload": dict(getattr(event, "payload", {}) or {}),
    }


def _bound_observe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the complete serialized MCP result below the advertised budget."""

    def payload_size() -> int:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    events = payload.get("events")
    if isinstance(events, list):
        while events and payload_size() > OBSERVE_RESULT_MAX_BYTES:
            events.pop(0)
    transcript = payload.get("transcript")
    if isinstance(transcript, dict) and payload_size() > OBSERVE_RESULT_MAX_BYTES:
        text = str(transcript.get("text") or "")
        transcript["text"] = ""
        overhead = payload_size()
        allowance = max(OBSERVE_RESULT_MAX_BYTES - overhead - 64, 0)
        encoded = text.encode("utf-8")
        clipped = encoded[-allowance:] if allowance else b""
        while clipped and (clipped[0] & 0xC0) == 0x80:
            clipped = clipped[1:]
        transcript["text"] = clipped.decode("utf-8", errors="replace")
        transcript["bytes"] = len(clipped)
        transcript["truncated"] = True
    payload["result_bytes"] = payload_size()
    return payload


def _event_cursor_from_payload(cursor: dict[str, Any] | None) -> str:
    """Extract the event cursor from a client-supplied cursor payload.

    Prefers the current ``event_cursor`` key; falls back to the legacy
    ``event_offset`` key for one-release compatibility, else ``"0"``.
    """
    payload = dict(cursor or {})
    if "event_cursor" in payload:
        value = str(payload.get("event_cursor") or "0").strip()
        return value or "0"
    # Explicit one-release compatibility bridge. The core subscriber upgrades
    # legacy zero or emits a resnapshot gap for an ambiguous non-zero offset.
    if "event_offset" in payload:
        return str(_clamp_int(payload.get("event_offset"), 0, 2**63 - 1))
    return "0"


def _read_run_events_delta(
    run_id: str,
    *,
    event_cursor: str | int | None = "0",
    max_events: int = OBSERVE_MAX_EVENTS,
) -> tuple[list[dict[str, Any]], str]:
    """Pull up to ``max_events`` new events for ``run_id`` since ``event_cursor``.

    Returns ``(events, next_cursor)``; ``next_cursor`` advances even when no
    event matches ``run_id`` so callers can keep polling without re-reading
    the whole stream.
    """
    target = str(run_id or "").strip()
    limit = _clamp_int(max_events, OBSERVE_MAX_EVENTS, OBSERVE_MAX_EVENTS)
    cursor = str(event_cursor or "0")

    events: list[dict[str, Any]] = []
    next_cursor = cursor
    for event in _control_plane.subscribe_events(since_cursor=cursor):
        payload = _event_to_payload(event)
        next_cursor = str(payload["cursor"])
        if limit > 0 and payload["run_id"] == target and len(events) < limit:
            events.append(payload)
        if limit > 0 and len(events) >= limit:
            break
    return events, next_cursor


def _run_terminal(run: dict[str, Any] | None) -> bool:
    """Return True when ``run`` has reached a terminal operator/health/liveness state."""
    if not run:
        return False
    operator_state = str(run.get("operator_state") or "")
    return (
        operator_state in {"completed", "blocked", "failed", "stopped"}
        or str(run.get("health") or "") == "final"
        or str(run.get("liveness") or "") == "terminal"
    )


def _report_ready(run: dict[str, Any] | None) -> bool:
    """Return True when ``run`` names a report path that exists and is non-empty."""
    if not run:
        return False
    report = str(run.get("latest_report") or run.get("report") or "")
    if not report:
        return False
    try:
        return Path(report).exists() and Path(report).stat().st_size > 0
    except OSError:
        return False


def _resolve_run_location(run_id: str) -> Any | None:
    """Read-follows-write fallback to where the runtime actually wrote a run.

    ``lookup_run`` reads the merged ``runs/<id>.json`` snapshots, which lag a
    still-launching run. ``control_plane.resolve_run`` probes ``runtime_runs/``
    (where the core runtime writes) first, then legacy ``artifacts/``. Returns
    the resolved :class:`control_plane.ResolvedRun`, or ``None`` when the run is
    not on disk yet (``RunNotResolved`` — the "still launching → await" case) so
    observe surfaces that loudly instead of a silent transcript miss. This is
    the MCP eye reading the same contract as observe/await/CLI (Niezmiennik 3).
    """
    target = str(run_id or "").strip()
    if not target:
        return None
    try:
        return _control_plane.resolve_run(target)
    except _control_plane.RunNotResolved:
        return None


def _observe_run_once(
    run_id: str,
    *,
    home: str | None = None,
    cursor: dict[str, Any] | None = None,
    max_bytes: int = OBSERVE_MAX_BYTES,
    max_events: int = OBSERVE_MAX_EVENTS,
) -> dict[str, Any]:
    """Single non-blocking snapshot of one run's events + transcript delta.

    Composes cursor resolution, control-plane lookup, the read-follows-write
    fallback for a not-yet-synced run, and a bounded transcript read into the
    payload shape returned by the ``vc_run_observe`` tool and the
    ``vibecrafted://runs/{run_id}/transcript`` resource.
    """
    target = str(run_id or "").strip()
    cursor_payload = dict(cursor or {})
    event_cursor = _event_cursor_from_payload(cursor_payload)
    transcript_offset = (
        None
        if "transcript_offset" not in cursor_payload
        else _clamp_int(cursor_payload.get("transcript_offset"), 0, 2**63 - 1)
    )
    with _override_vibecrafted_home(home):
        observation = _server_observation.observe_run(target)
        run = observation.get("run")
        if not isinstance(run, dict):
            run = None
        events, next_event_cursor = _read_run_events_delta(
            target,
            event_cursor=event_cursor,
            max_events=max_events,
        )
        transcript_path = str(
            (run or {}).get("latest_transcript") or (run or {}).get("transcript") or ""
        )
        resolved = None
        if not transcript_path:
            # Snapshot has no transcript yet — read-follows-write: probe where
            # the runtime wrote (runtime_runs/), then legacy artifacts/. Closes
            # the observe split-brain so a fresh run is not a silent miss.
            resolved = _resolve_run_location(target)
            if resolved is not None and resolved.transcript is not None:
                transcript_path = str(resolved.transcript)
        transcript = _bounded_text_read(
            transcript_path,
            offset=transcript_offset,
            max_bytes=max_bytes,
        )

    if run is not None:
        state = (run or {}).get("health") or (run or {}).get("state") or "missing"
    elif resolved is not None:
        # Run dir exists where the runtime writes, but the snapshot sync has not
        # merged it yet — report it as launching, never a silent "missing".
        state = "launching"
    else:
        state = "missing"

    return _bound_observe_payload(
        {
            "run_id": target,
            "found": run is not None or resolved is not None,
            "state": state,
            "operator_state": (run or {}).get("operator_state", "") if run else "",
            "cursor": {
                "event_cursor": next_event_cursor,
                "transcript_offset": int(transcript["next_offset"]),
            },
            "events": events,
            "transcript": transcript,
            "terminal": _run_terminal(run),
            "report_ready": _report_ready(run),
            "report_uri": f"vibecrafted://runs/{target}/report",
            "observation": observation,
        }
    )


def _observe_run(
    run_id: str,
    *,
    home: str | None = None,
    cursor: dict[str, Any] | None = None,
    max_bytes: int = OBSERVE_MAX_BYTES,
    max_events: int = OBSERVE_MAX_EVENTS,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    """Return one server-owned observation plus bounded local cursor deltas.

    ``wait_seconds`` remains accepted for wire compatibility but no longer arms
    an MCP-private poller. Blocking consumers use ``vc_await_run`` and join the
    server hub.
    """
    payload = _observe_run_once(
        run_id,
        home=home,
        cursor=cursor,
        max_bytes=max_bytes,
        max_events=max_events,
    )
    if wait_seconds > 0:
        payload["wait_semantics"] = "one_shot; use vc_await_run for blocking"
    return _bound_observe_payload(payload)


def _run_status_resource_payload(run_id: str) -> dict[str, Any]:
    """Build the ``vibecrafted://runs/{run_id}/status`` resource payload.

    Falls back to ``_resolve_run_location`` (state "launching") when the run
    is not yet present in the synced control-plane snapshot.
    """
    run = _control_plane.lookup_run(run_id)
    resolved = _resolve_run_location(run_id) if run is None else None
    return {
        "run_id": run_id,
        "found": run is not None or resolved is not None,
        "run": run,
        "state": "launching" if (run is None and resolved is not None) else "",
        "operator_state": (run or {}).get("operator_state", "") if run else "",
        "artifact_gate": (run or {}).get("artifact_gate", "") if run else "",
        "terminal": _run_terminal(run),
        "report_ready": _report_ready(run),
    }


def _run_report_resource_payload(run_id: str) -> dict[str, Any]:
    """Build the ``vibecrafted://runs/{run_id}/report`` resource payload.

    Falls back to ``_resolve_run_location`` when the synced snapshot has no
    report path yet but ``runtime_runs/`` already carries one.
    """
    run = _control_plane.lookup_run(run_id)
    report_path = str(
        (run or {}).get("latest_report") or (run or {}).get("report") or ""
    )
    resolved = None
    if not report_path:
        # Read-follows-write: a run not yet in the snapshots may already have a
        # report under runtime_runs/ (resolve_run reads it from meta.json there).
        resolved = _resolve_run_location(run_id)
        if resolved is not None and resolved.report is not None:
            report_path = str(resolved.report)
    return {
        "run_id": run_id,
        "found": run is not None or resolved is not None,
        "report": _bounded_text_read(report_path, max_bytes=OBSERVE_MAX_BYTES),
    }


def _lifecycle_state_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Bounded projection of a lifecycle run state for MCP payloads.

    Full lifecycle ``state.json`` carries prompts, manifests, and git
    snapshots — too heavy for a tool result. This mirrors the CLI
    ``status`` verb: supervisor projection plus operator-relevant extras.
    """
    payload = _LifecycleSupervisor().status(state)
    payload["parent_run_id"] = str(state.get("parent_run_id") or "")
    payload["operator_actions"] = len(state.get("operator_actions") or [])
    payload["human_controls"] = [
        str(item) for item in (state.get("human_controls") or [])
    ]
    baton = dict(state.get("baton") or {})
    payload["previous_reports"] = [
        str(path) for path in (baton.get("previous_reports") or [])
    ]
    return payload


def _lifecycle_schema_resource_payload() -> dict[str, Any]:
    """Load the packaged lifecycle JSON Schema, stamping ``$id`` if absent."""
    path = _core_resource_path("schemas", "lifecycle.schema.v1.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("$id", _LIFECYCLE_SCHEMA_ID)
    return payload


def _lifecycle_verb(
    run_id: str,
    workflow_id: str,
    home: str | None,
    action: Any,
) -> dict[str, Any]:
    """Resolve a lifecycle run and apply one traced operator verb.

    The whole resolve→load→act sequence runs under the ``home`` override
    because the verbs write state/report/transcript and may launch
    continuation runs through the control plane.
    """
    try:
        with _override_vibecrafted_home(home):
            state_path = _lifecycle_control.resolve_lifecycle_state_path(
                run_id, workflow_id="" if run_id else workflow_id
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            return {"ok": True, "result": action(state_path, state)}
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def build_server() -> Any:
    """Construct and return the FastMCP server instance.

    Kept as a builder function so tests can instantiate a fresh server
    per case without relying on import-time global state.
    """
    from fastmcp import FastMCP

    from .version import __version__

    mcp = FastMCP("vibecrafted", version=__version__)

    @mcp.tool(annotations={"readOnlyHint": True})
    def vc_repo_full(project: str = ".") -> dict[str, Any]:
        """Git ground truth for ``project``.

        Returns branch, ahead/behind vs upstream, dirt counts, stashes,
        worktrees, remotes, and the most recent commits. Read-only.

        Token budget: ~3-6k tokens for typical repos. Dominated by
        ``recent_commits``; trim with ``vc_init(slim=True)`` when calling
        as part of a cold-start brief.
        """
        return _git.repo_full(project)

    @mcp.tool(annotations={"readOnlyHint": True})
    def vc_doctor(project: str | None = None) -> dict[str, Any]:
        """Runtime health summary from the vibecrafted installer doctor.

        ``project`` is accepted for forward compatibility with v0.2
        (per-project doctor scopes); v0.1 always reports the operator-
        global state derived from ``$VIBECRAFTED_HOME``.

        Token budget: ~2-4k tokens. Returns ``unavailable=true`` when the
        installer module is not reachable from the current import path.
        """
        del project  # v0.1 ignores; preserved for forward compatibility
        return _doctor_payload(slim=False)

    @mcp.tool(annotations={"readOnlyHint": True})
    def vc_board_status(home: str | None = None) -> dict[str, Any]:
        """Operator control-plane snapshot: active runs, recent runs, events.

        ``home`` overrides ``$VIBECRAFTED_HOME`` for this call so an
        operator with multiple frameworks installed can probe a specific
        one without mutating their shell.

        Token budget: ~3-10k tokens depending on event tail and run
        count. The shape is shared with ``vc_init`` for consistency.
        """
        with _override_vibecrafted_home(home):
            return _control_plane.sync_state()

    def _launch_workflow(
        skill: str = "workflow",
        agent: str | None = None,
        prompt: str = "",
        file: str = "",
        runtime: str = "headless",
        root: str | None = None,
        source_dir: str = ".",
        mode: str | None = None,
        home: str | None = None,
    ) -> dict[str, Any]:
        """Launch a workflow through the Vibecrafted core runtime.

        This is intentionally a thin remote button: launch validation and
        process creation stay in ``vibecrafted_core.workflow``.
        """
        payload: dict[str, Any] = {
            "skill": skill,
            "prompt": prompt,
            "file": file,
            "runtime": runtime,
        }
        if agent is not None:
            payload["agent"] = agent
        if root is not None:
            payload["root"] = root
        if mode is not None:
            payload["mode"] = mode
        with _override_vibecrafted_home(home):
            spec = _workflow.normalize_launch_spec(payload, source_dir)
            return _workflow.launch_workflow(spec, source_dir, env=dict(os.environ))

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": True,
        }
    )
    def vc_launch(
        skill: str = "workflow",
        agent: str | None = None,
        prompt: str = "",
        file: str = "",
        runtime: str = "headless",
        root: str | None = None,
        source_dir: str = ".",
        mode: str | None = None,
        home: str | None = None,
    ) -> dict[str, Any]:
        """Mutating: launch a workflow through the Vibecrafted core runtime.

        This spawns an agent process and writes control-plane artifacts.
        Launch validation and process creation stay in
        ``vibecrafted_core.workflow``.
        """
        return _launch_workflow(
            skill=skill,
            agent=agent,
            prompt=prompt,
            file=file,
            runtime=runtime,
            root=root,
            source_dir=source_dir,
            mode=mode,
            home=home,
        )

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": True,
        }
    )
    def vc_run_launch(
        skill: str = "workflow",
        agent: str | None = None,
        prompt: str = "",
        file: str = "",
        runtime: str = "headless",
        root: str | None = None,
        source_dir: str = ".",
        mode: str | None = None,
        home: str | None = None,
    ) -> dict[str, Any]:
        """Mutating alias of ``vc_launch`` for run-lifecycle naming symmetry.

        This spawns an agent process and writes control-plane artifacts.
        """
        return _launch_workflow(
            skill=skill,
            agent=agent,
            prompt=prompt,
            file=file,
            runtime=runtime,
            root=root,
            source_dir=source_dir,
            mode=mode,
            home=home,
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    def vc_run_status(run_id: str, home: str | None = None) -> dict[str, Any]:
        """Perform one vc-server-owned run observation."""
        with _override_vibecrafted_home(home):
            return _server_observation.observe_run(run_id)

    @mcp.tool(annotations={"readOnlyHint": True})
    def vc_await_run(
        run_id: str,
        timeout_seconds: float = 300,
        interval_seconds: float = 5,
        hard_cap_seconds: float | None = None,
        home: str | None = None,
    ) -> dict[str, Any]:
        """Join the vc-server shared monitor for one run."""
        with _override_vibecrafted_home(home):
            return _server_observation.await_run(
                run_id,
                idle_timeout_seconds=timeout_seconds,
                interval_seconds=interval_seconds,
                hard_cap_seconds=hard_cap_seconds,
            )

    @mcp.tool(annotations={"readOnlyHint": True})
    def vc_run_observe(
        run_id: str,
        home: str | None = None,
        cursor: dict[str, Any] | None = None,
        max_bytes: int = OBSERVE_MAX_BYTES,
        max_events: int = OBSERVE_MAX_EVENTS,
        wait_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Bounded cursor pull for run events and transcript deltas.

        This delegates event cursors and run projection to the control plane,
        reads transcript bytes from the projected artifact path, and never
        returns more than ``max_bytes`` (capped at 64 KiB) in one tool result.
        """
        return _observe_run(
            run_id,
            home=home,
            cursor=cursor,
            max_bytes=max_bytes,
            max_events=max_events,
            wait_seconds=wait_seconds,
        )

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "openWorldHint": True,
        }
    )
    def vc_run_stop(
        run_id: str,
        reason: str = "mcp operator stop",
        home: str | None = None,
    ) -> dict[str, Any]:
        """Mutating: request graceful stop of an active run with an audit event."""
        with _override_vibecrafted_home(home):
            return _workflow.stop_run(run_id, reason=reason)

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": True,
        }
    )
    def vc_run_retry(
        run_id: str,
        source_dir: str = ".",
        home: str | None = None,
    ) -> dict[str, Any]:
        """Mutating: retry a run using stored launch metadata and preconditions.

        This may spawn a replacement agent process and writes control-plane
        artifacts for the retry.
        """
        with _override_vibecrafted_home(home):
            return _workflow.retry_run(
                run_id,
                source_dir=source_dir,
                env=dict(os.environ),
            )

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "openWorldHint": True,
        }
    )
    def vc_run_blocked(
        run_id: str,
        reason: str = "mcp operator block",
        note: str = "",
        home: str | None = None,
    ) -> dict[str, Any]:
        """Mutating: mark an active run as blocked with an audit trail."""
        with _override_vibecrafted_home(home):
            return _workflow.block_run(run_id, reason=reason, note=note)

    @mcp.tool(annotations={"readOnlyHint": True})
    def vc_lifecycle_runs(
        workflow_id: str = "",
        limit: int = 10,
        home: str | None = None,
    ) -> dict[str, Any]:
        """List lifecycle runs (vc-ship and stage wrappers), newest first.

        Read-only mirror of the ``runs`` operator verb over
        ``control_plane/lifecycle_runs/``. ``workflow_id`` filters to one
        manifest (e.g. ``vc-ship``); empty lists all workflows.
        """
        with _override_vibecrafted_home(home):
            runs = _lifecycle_control.list_lifecycle_runs(
                workflow_id=workflow_id, limit=max(int(limit), 0)
            )
        return {"count": len(runs), "runs": runs}

    @mcp.tool(annotations={"readOnlyHint": True})
    def vc_lifecycle_status(
        run_id: str = "",
        workflow_id: str = "",
        home: str | None = None,
    ) -> dict[str, Any]:
        """One lifecycle run's status: stage, baton, controls, cargo.

        Read-only mirror of the ``status`` operator verb. Empty ``run_id``
        resolves the newest run (optionally scoped to ``workflow_id``).
        """
        return _lifecycle_verb(
            run_id,
            workflow_id,
            home,
            lambda _path, state: _lifecycle_state_summary(state),
        )

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": True,
        }
    )
    def vc_lifecycle_approve(
        run_id: str = "",
        workflow_id: str = "",
        force: bool = False,
        home: str | None = None,
    ) -> dict[str, Any]:
        """Mutating: approve_transition — launch the baton's next_stage.

        Spawns a parent-linked continuation run carrying the baton cargo
        (previous stage reports). Refuses while cargo reports are missing
        or empty unless ``force=True`` (the override is traced in
        ``operator_actions``). Validated against the run's human_controls.
        """
        return _lifecycle_verb(
            run_id,
            workflow_id,
            home,
            lambda path, state: _lifecycle_state_summary(
                _lifecycle_control.approve_transition(path, state, force=force)
            ),
        )

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "openWorldHint": True,
        }
    )
    def vc_lifecycle_interrupt(
        run_id: str = "",
        workflow_id: str = "",
        home: str | None = None,
    ) -> dict[str, Any]:
        """Mutating: interrupt_workflow — stop the live stage, mark the run."""
        return _lifecycle_verb(
            run_id,
            workflow_id,
            home,
            lambda path, state: _lifecycle_control.interrupt_workflow(path, state),
        )

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": True,
        }
    )
    def vc_lifecycle_force_audit(
        run_id: str = "",
        workflow_id: str = "",
        home: str | None = None,
    ) -> dict[str, Any]:
        """Mutating: force_audit — make an audit the next lifecycle move.

        Steers the baton to the manifest's audit stage, or dispatches a
        parent-linked standalone vc-audit run for single-stage manifests.
        """

        def _act(path: Path, state: dict[str, Any]) -> dict[str, Any]:
            outcome = _lifecycle_control.force_audit(path, state)
            if isinstance(outcome.get("run"), dict):
                outcome = dict(outcome)
                outcome["run"] = _lifecycle_state_summary(outcome["run"])
            return outcome

        return _lifecycle_verb(run_id, workflow_id, home, _act)

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": True,
        }
    )
    def vc_lifecycle_accept_dou(
        finding: str,
        run_id: str = "",
        workflow_id: str = "",
        home: str | None = None,
    ) -> dict[str, Any]:
        """Mutating: accept_dou — consciously accept a DoU gap with a trace."""
        return _lifecycle_verb(
            run_id,
            workflow_id,
            home,
            lambda path, state: _lifecycle_control.accept_dou(path, state, finding),
        )

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": True,
        }
    )
    def vc_lifecycle_fallback(
        stage: str,
        run_id: str = "",
        workflow_id: str = "",
        home: str | None = None,
    ) -> dict[str, Any]:
        """Mutating: choose_fallback_stage — steer the baton to an earlier stage.

        Manifest-validated; unknown stage ids are rejected with the known
        stage list in the error.
        """
        return _lifecycle_verb(
            run_id,
            workflow_id,
            home,
            lambda path, state: _lifecycle_control.choose_fallback_stage(
                path, state, stage
            ),
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    def vc_loct_capabilities(timeout: float = 5.0) -> dict[str, Any]:
        """Discover live capabilities of the loctree/aicx product foundations.

        Returns the ``vibecrafted.capabilities.v1`` schema: per-tool presence,
        real-execution ``runnable`` status (distinguishing ``product_missing``
        from ``product_broken``), install provenance (canonical ``~/.local/bin``
        vs. ghost roots), the live ``--version`` string, and the subcommands the
        currently installed binary exposes. Re-running after a foundation
        upgrade reflects the new capability surface — the runtime never owns or
        mutates these external binaries.
        """
        return _capabilities.foundation_capabilities(timeout=timeout)

    @mcp.tool(annotations={"readOnlyHint": True})
    def vc_init(project: str = ".", slim: bool = True) -> dict[str, Any]:
        """Cold-start synthesis: 3 senses + v0.1 insight stubs.

        Composes git ground truth, doctor health, control-plane state,
        and synthesis hints (live failure score, unmade decisions,
        unverified claims). When ``slim=True`` (default) the response is
        kept under ~5KB by trimming recent commits and doctor findings.

        Use this as the first call when bootstrapping an agent; follow
        up with ``mcp__loctree-mcp__context`` and
        ``mcp__aicx-mcp__aicx_intents`` for the perception and
        intentions senses.

        Token budget: ~5KB slim (default), ~15-20KB full.
        """
        repo_state = _git.repo_full(project)
        doctor_state = _doctor_payload(slim=slim)
        with _override_vibecrafted_home(None):
            board = _control_plane.sync_state()
        if slim:
            repo_state = _trim_recent_commits(repo_state, SLIM_MAX_COMMITS)
            board = {
                "generated_at": board.get("generated_at"),
                "active_run_count": len(board.get("active_runs") or []),
                "recent_run_count": len(board.get("recent_runs") or []),
                "warnings": (board.get("warnings") or [])[:5],
            }
        payload: dict[str, Any] = {
            "ground_truth": repo_state,
            "doctor": doctor_state,
            "board": board,
            "perception_hint": "use mcp__loctree-mcp__context() for full perception",
            "intentions_hint": "use mcp__aicx-mcp__aicx_intents() for full intentions",
            "synthesis": {
                "live_failure_score": _live_failure_score(repo_state, doctor_state),
                "unmade_decisions": _unmade_decisions(repo_state),
                "unverified_claims": _unverified_claims(),
            },
        }
        if slim:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            payload["_slim"] = {
                "bytes": len(encoded),
                "budget_bytes": SLIM_BUDGET_BYTES,
                "within_budget": len(encoded) <= SLIM_BUDGET_BYTES,
            }
        return payload

    @mcp.resource("vibecrafted://board/runs")
    def board_runs() -> dict[str, Any]:
        """Snapshot of the operator board: active + recent runs."""
        snapshot = _control_plane.sync_state()
        return {
            "generated_at": snapshot.get("generated_at"),
            "active_runs": snapshot.get("active_runs") or [],
            "recent_runs": snapshot.get("recent_runs") or [],
            "warnings": snapshot.get("warnings") or [],
        }

    @mcp.resource("vibecrafted://lifecycle/schema")
    def lifecycle_schema_resource() -> dict[str, Any]:
        """Packaged JSON Schema for the lifecycle state/report contract."""
        return _lifecycle_schema_resource_payload()

    @mcp.resource("vibecrafted://control-plane/events/{run_id}")
    def event_stream(run_id: str) -> list[dict[str, Any]]:
        """Last 50 events for a specific run from the operator stream."""
        return _read_run_event_tail(run_id, home=None, limit=50)

    @mcp.resource("vibecrafted://runs/{run_id}/transcript")
    def run_transcript(run_id: str) -> dict[str, Any]:
        """Bounded transcript read for one run from the current control plane."""
        return _observe_run_once(
            run_id,
            cursor={"event_cursor": "0", "transcript_offset": 0},
            max_bytes=OBSERVE_MAX_BYTES,
            max_events=0,
        )["transcript"]

    @mcp.resource("vibecrafted://runs/{run_id}/events")
    def run_events(run_id: str) -> dict[str, Any]:
        """Bounded event read for one run from the current control plane."""
        events, next_cursor = _read_run_events_delta(
            run_id,
            event_cursor="0",
            max_events=OBSERVE_MAX_EVENTS,
        )
        return {
            "run_id": run_id,
            "cursor": {"event_cursor": next_cursor},
            "events": events,
        }

    @mcp.resource("vibecrafted://runs/{run_id}/status")
    def run_status(run_id: str) -> dict[str, Any]:
        """Current control-plane projection for one run."""
        return _run_status_resource_payload(run_id)

    @mcp.resource("vibecrafted://runs/{run_id}/report")
    def run_report(run_id: str) -> dict[str, Any]:
        """Bounded report read for one run from the current control plane."""
        return _run_report_resource_payload(run_id)

    @mcp.resource("vibecrafted://capabilities/foundations")
    def foundation_capabilities_resource() -> dict[str, Any]:
        """Live ``vibecrafted.capabilities.v1`` discovery for product foundations."""
        return _capabilities.foundation_capabilities()

    return mcp


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``vibecrafted-mcp`` (stdio FastMCP server)."""
    parser = argparse.ArgumentParser(
        prog="vibecrafted-mcp",
        description=(
            "MCP server exposing vibecrafted ground truth (git), runtime "
            "doctor, and operator board state to agents. Speaks stdio by "
            "default — wire it into your agent's mcp.config."
        ),
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=("stdio",),
        help="Transport to expose the server on (only stdio in v0.1).",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the package version and exit.",
    )
    args = parser.parse_args(argv)

    if args.version:
        from .version import __version__

        print(__version__)
        return 0

    server = build_server()
    server.run(transport=args.transport)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
