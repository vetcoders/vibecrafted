"""Client for the vc-server-owned run observation contract.

The CLI is deliberately a thin HTTP client.  It never falls back to
``control_plane.await_run``: a missing server is an explicit product failure,
not permission to create another private filesystem poller.
"""

from __future__ import annotations

import http.client
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
    """Subscribe to the server's shared monitor and return its verdict."""
    encoded = urllib.parse.quote(run_id, safe="")
    query: dict[str, str] = {
        "idle_timeout": str(max(float(idle_timeout_seconds), 0.0)),
        "interval": str(max(float(interval_seconds), 0.1)),
    }
    if hard_cap_seconds is not None:
        query["hard_cap"] = str(max(float(hard_cap_seconds), 0.0))
    path = f"/api/control/runs/{encoded}/await?{urllib.parse.urlencode(query)}"
    # Connect with a short explicit budget, then hand deadline ownership to the
    # server.  ``urllib`` has one timeout for both phases, which would turn the
    # idle window into an accidental client-side wall-clock cap.
    origin = urllib.parse.urlsplit(_origin())
    if origin.scheme not in {"http", "https"} or not origin.hostname:
        raise ServerObservationError(f"invalid vc-server origin: {_origin()}")
    connection_type = (
        http.client.HTTPSConnection
        if origin.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(origin.hostname, origin.port, timeout=3.0)
    try:
        connection.connect()
        if connection.sock is not None:
            read_timeout = (
                max(float(hard_cap_seconds), 0.0) + 5.0
                if hard_cap_seconds is not None
                else None
            )
            connection.sock.settimeout(read_timeout)
        prefix = origin.path.rstrip("/")
        connection.request(
            "GET", f"{prefix}{path}", headers={"Accept": "application/json"}
        )
        response = connection.getresponse()
        body = response.read()
    except (TimeoutError, OSError, http.client.HTTPException) as exc:
        raise ServerObservationError(
            f"vc-server unavailable at {_origin()}: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        connection.close()
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServerObservationError(
            "vc-server returned invalid observation JSON"
        ) from exc
    if response.status in {404, 408, 409, 503} and isinstance(payload, dict):
        return payload
    if response.status >= 400:
        raise ServerObservationError(
            f"vc-server observation endpoint returned HTTP {response.status}"
        )
    if not isinstance(payload, dict):
        raise ServerObservationError("vc-server returned a non-object observation")
    return payload
