"""Run observation clients.

Dashboard/observe reads stay on vc-server. ``await`` is a local UDS subscriber:
the dispatcher owns wake delivery and durable control-plane files own truth.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from .server_config import load_server_config

# The /observe endpoint can take >3s when the server revalidates a busy run
# (measured 5.94s). A 3s client timeout turned a slow-but-healthy eye into a
# false "vc-server unavailable" while the worker was still writing.
DEFAULT_OBSERVE_TIMEOUT_SECONDS = 15.0
OBSERVE_TIMEOUT_ENV = "VIBECRAFTED_OBSERVE_TIMEOUT_S"


class ServerObservationError(RuntimeError):
    """The configured vc-server could not satisfy an observation request."""


def _origin() -> str:
    return load_server_config().public_url.rstrip("/")


def observe_timeout_seconds() -> float:
    """Client timeout for one HTTP observe/list call."""
    raw = str(os.environ.get(OBSERVE_TIMEOUT_ENV) or "").strip()
    if raw:
        try:
            parsed = float(raw)
        except ValueError:
            parsed = DEFAULT_OBSERVE_TIMEOUT_SECONDS
        if parsed > 0:
            return parsed
    return DEFAULT_OBSERVE_TIMEOUT_SECONDS


def _local_observation(run_id: str, *, reason: str) -> dict[str, Any] | None:
    """Read-follows-write fallback when vc-server is slow or fail-closed.

    Durable truth lives in ``runtime_runs/`` and the projected snapshot. A
    timeout or 503 on the HTTP eye must not report disappearance of a run
    that is still on disk.
    """
    from .control_plane import RunNotResolved, lookup_run, resolve_run

    target = str(run_id or "").strip()
    if not target:
        return None
    run = lookup_run(target)
    resolved = None
    if run is None:
        try:
            resolved = resolve_run(target)
        except (RunNotResolved, OSError):
            resolved = None
    if run is None and resolved is None:
        return None
    payload_run = dict(run) if isinstance(run, Mapping) else None
    if payload_run is None and resolved is not None and resolved.meta is not None:
        try:
            meta_payload = json.loads(resolved.meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            meta_payload = {}
        if isinstance(meta_payload, dict):
            payload_run = dict(meta_payload)
            payload_run["run_id"] = target
            if resolved.transcript is not None:
                payload_run.setdefault("latest_transcript", str(resolved.transcript))
    return {
        "schema": "vibecrafted.run-observation.v1",
        "run_id": target,
        "found": True,
        "terminal": bool(
            payload_run
            and str(payload_run.get("state") or "")
            in {
                "settled",
                "report_validated",
                "completed",
                "closed",
                "failed",
                "stopped",
                "gc",
            }
        ),
        "worker_alive": bool((payload_run or {}).get("worker_alive")),
        "process_truth": str((payload_run or {}).get("process_truth") or "unknown"),
        "evidence_disagreement": False,
        "run": payload_run,
        "writer_revalidation": reason,
        "source": "local_control_plane_fallback",
    }


def _request_json(path: str, *, timeout: float | None) -> dict[str, Any]:
    request = urllib.request.Request(  # nosemgrep: dynamic-urllib-use-detected
        f"{_origin()}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(  # nosemgrep: dynamic-urllib-use-detected
            request, timeout=timeout
        ) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            body = {}
        if exc.code == 404 and isinstance(body, dict):
            return body
        # 503 used to mean "server down". After writer-revalidation fail-closed
        # it also meant "projection lag on a live run". If the body still names
        # the run, return it; otherwise let the caller fall back locally.
        if isinstance(body, dict) and (
            body.get("found") is True or isinstance(body.get("run"), dict)
        ):
            return body
        raise ServerObservationError(
            f"vc-server observation endpoint returned HTTP {exc.code}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ServerObservationError(
            f"vc-server unavailable at {_origin()}: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ServerObservationError("vc-server returned a non-object observation")
    return payload


def observe_run(run_id: str) -> dict[str, Any]:
    """Perform one bounded observation; fall back to local files if HTTP fails."""
    encoded = urllib.parse.quote(run_id, safe="")
    try:
        return _request_json(
            f"/api/control/runs/{encoded}/observe",
            timeout=observe_timeout_seconds(),
        )
    except ServerObservationError as exc:
        local = _local_observation(
            run_id, reason=f"local_fallback_after_server_error:{type(exc).__name__}"
        )
        if local is not None:
            return local
        raise


def list_runs() -> list[dict[str, Any]]:
    """Read the server run list for ``--last`` selection."""
    try:
        payload = _request_json("/api/control/runs", timeout=observe_timeout_seconds())
    except ServerObservationError:
        from .control_plane import sync_state

        board = sync_state()
        runs = list(board.get("active_runs") or []) + list(
            board.get("recent_runs") or []
        )
        return [dict(run) for run in runs if isinstance(run, Mapping)]
    runs = payload.get("runs")
    if not isinstance(runs, list):
        raise ServerObservationError("vc-server run list omitted runs")
    return [dict(run) for run in runs if isinstance(run, Mapping)]


def resolve_run_id(agent: str, run_id: str, *, last: bool) -> str:
    """Resolve explicit or most-recent agent run identity through vc-server."""
    target = str(run_id or "").strip()
    if target:
        return target
    if not last:
        return ""
    for run in list_runs():
        if str(run.get("agent") or "") == agent:
            return str(run.get("run_id") or "")
    return ""


def await_run(
    run_id: str,
    *,
    idle_timeout_seconds: float,
    hard_cap_seconds: float | None,
    interval_seconds: float,
) -> dict[str, Any]:
    """Subscribe directly to the dispatcher signal channel."""
    from .control_plane import await_run as await_control_plane_run

    return await_control_plane_run(
        run_id,
        timeout_seconds=idle_timeout_seconds,
        interval_seconds=interval_seconds,
        hard_cap_seconds=hard_cap_seconds,
    )
