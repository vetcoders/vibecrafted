"""Run observation clients.

Dashboard/observe reads stay on vc-server. ``await`` is a local UDS subscriber:
the dispatcher owns wake delivery and durable control-plane files own truth.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from .server_config import load_server_config


class ServerObservationError(RuntimeError):
    """The configured vc-server could not satisfy an observation request."""


def _origin() -> str:
    return load_server_config().public_url.rstrip("/")


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
    """Perform one bounded server-side observation without arming a monitor."""
    encoded = urllib.parse.quote(run_id, safe="")
    return _request_json(
        f"/api/control/runs/{encoded}/observe",
        timeout=3.0,
    )


def list_runs() -> list[dict[str, Any]]:
    """Read the server run list for ``--last`` selection."""
    payload = _request_json("/api/control/runs", timeout=3.0)
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
