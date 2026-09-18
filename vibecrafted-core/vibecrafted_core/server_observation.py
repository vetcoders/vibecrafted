"""Run observation clients.

Dashboard/observe reads stay on vc-server. ``await`` is a local UDS subscriber:
the dispatcher owns wake delivery and durable control-plane files own truth.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .server_config import load_server_config

# The /observe endpoint can take >3s when the server revalidates a busy run
# (measured 5.94s). A 3s client timeout turned a slow-but-healthy eye into a
# false "vc-server unavailable" while the worker was still writing.
DEFAULT_OBSERVE_TIMEOUT_SECONDS = 15.0
OBSERVE_TIMEOUT_ENV = "VIBECRAFTED_OBSERVE_TIMEOUT_S"
_WRITER_OK_REASONS = frozenset({"ok", "disabled_for_test"})
_KNOWN_PROCESS_TRUTH = frozenset({"live", "ghost", "unknown"})


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


def _named_lifecycle(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    """Overlay order: nonempty ``status`` wins over ``state``."""
    status = str(payload.get("status") or "").strip()
    state = str(payload.get("state") or "").strip()
    return status, state, status or state


def _optional_bool(value: Any) -> bool | None:
    """Preserve missing evidence. Only an explicit bool is proof."""
    if isinstance(value, bool):
        return value
    return None


def _has_terminal_residue(payload: Mapping[str, Any]) -> bool:
    raw = payload.get("exit_code")
    if raw is not None and raw != "":
        try:
            int(raw)
        except (TypeError, ValueError):
            pass
        else:
            return True
    return bool(str(payload.get("completed_at") or "").strip())


def _named_class(named: str, *, active: set[str], final: set[str]) -> str:
    if named in final:
        return "final"
    if named in active:
        return "active"
    return "unknown"


def _consistently_terminal(payload: Mapping[str, Any], *, named_class: str) -> bool:
    """Same gate as control-core ``runtime_meta_is_consistently_terminal``.

    An active named state keeps leftover ``exit_code`` / ``completed_at`` from
    sealing finality. A coherent final name, explicit ``terminal``, or
    ``liveness=terminal`` still resolves as terminal.
    """
    if named_class == "final":
        return True
    if named_class == "active":
        return False
    if str(payload.get("liveness") or "").strip() == "terminal":
        return True
    if payload.get("terminal") is True:
        return True
    return _has_terminal_residue(payload)


def _observation_from_payload(
    run_id: str,
    payload_run: dict[str, Any] | None,
    *,
    reason: str,
) -> dict[str, Any]:
    """Legacy classifier retained for unit characterization only.

    Production observe never calls this. Fallback is ``control-observe``
    (``compute_view``) or an explicit ``control_core_observe_unavailable``
    payload with no Python classification.
    """
    from .control_plane import ACTIVE_STATES, FINAL_STATES

    payload = payload_run or {}
    status, state, named = _named_lifecycle(payload)
    named_cls = _named_class(named, active=ACTIVE_STATES, final=FINAL_STATES)
    status_cls = (
        _named_class(status, active=ACTIVE_STATES, final=FINAL_STATES)
        if status
        else named_cls
    )
    state_cls = (
        _named_class(state, active=ACTIVE_STATES, final=FINAL_STATES)
        if state
        else named_cls
    )
    disagreement: list[str] = []
    named_conflict = bool(status and state and status != state)
    if named_conflict:
        disagreement.append("conflicting_status_and_state")
    class_conflict = named_conflict and {"final", "active"} <= {
        status_cls,
        state_cls,
    }
    persisted_truth = str(payload.get("process_truth") or "").strip()
    persisted_worker = _optional_bool(payload.get("worker_alive"))
    persisted_live = persisted_truth == "live" or persisted_worker is True
    terminal = (
        False
        if class_conflict
        else _consistently_terminal(payload, named_class=named_cls)
    )
    if terminal and persisted_live:
        disagreement.append("terminal_state_with_live_worker")
        terminal = False
    if named_cls == "active" and (
        _has_terminal_residue(payload) or payload.get("terminal") is True
    ):
        disagreement.append("conflicting_active_state_and_terminal_evidence")
    if (
        named_cls == "unknown"
        and payload.get("terminal") is not True
        and str(payload.get("liveness") or "").strip() != "terminal"
    ):
        disagreement.append("unknown_control_plane_state")
    if terminal:
        worker_alive: bool | None = False
    elif persisted_truth == "live":
        worker_alive = True
    else:
        worker_alive = persisted_worker
    liveness = str(payload.get("liveness") or "").strip()
    if terminal:
        process_truth = "terminal"
    elif persisted_truth in _KNOWN_PROCESS_TRUTH:
        process_truth = persisted_truth
    elif worker_alive is True:
        process_truth = "live"
    elif liveness == "pid_gone" or persisted_truth == "ghost":
        process_truth = "ghost"
    else:
        process_truth = "unknown"
    if (
        not terminal
        and liveness == "pid_alive"
        and worker_alive is not True
        and persisted_truth != "live"
    ):
        disagreement.append("persisted_pid_alive_without_current_proof")
    writer_unavailable = reason not in _WRITER_OK_REASONS
    if writer_unavailable:
        found_currently_live = (not terminal) and (
            persisted_truth == "live" or worker_alive is True
        )
        if not found_currently_live and not terminal:
            disagreement.append("canonical_writer_revalidation_unavailable")
    # Stable unique reasons; first occurrence wins.
    seen: set[str] = set()
    reasons: list[str] = []
    for item in disagreement:
        if item not in seen:
            seen.add(item)
            reasons.append(item)
    return {
        "schema": "vibecrafted.run-observation.v1",
        "run_id": run_id,
        "found": True,
        "terminal": terminal,
        "worker_alive": worker_alive,
        "process_truth": process_truth,
        "evidence_disagreement": bool(reasons),
        "disagreement_reasons": reasons,
        "run": payload_run,
        "writer_revalidation": reason,
        "source": "local_control_plane_fallback",
    }


def _control_observe_bin() -> Path | None:
    """Locate the control-core observe binary. Never a second classifier."""
    raw = str(os.environ.get("VIBECRAFTED_CONTROL_OBSERVE") or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    which = shutil.which("control-observe")
    if which:
        return Path(which)
    scaffold = shutil.which("scaffold-doctor")
    if scaffold:
        sibling = Path(scaffold).with_name("control-observe")
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return sibling
    root = str(os.environ.get("VIBECRAFTED_ROOT") or "").strip()
    if root:
        for profile in ("release", "debug"):
            candidate = (
                Path(root)
                / "vibecrafted-server"
                / "target"
                / profile
                / "control-observe"
            )
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def _observation_from_control_core(run_id: str) -> dict[str, Any] | None:
    """Ask the same crate voc uses. No Python classification."""
    binary = _control_observe_bin()
    if binary is None:
        return None
    home = str(os.environ.get("VIBECRAFTED_HOME") or Path.home() / ".vibecrafted")
    try:
        completed = subprocess.run(
            [
                str(binary),
                "--home",
                home,
                "--run-id",
                run_id,
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=observe_timeout_seconds(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    payload["source"] = "control_core_compute_view"
    return payload


def _local_observation(run_id: str, *, reason: str) -> dict[str, Any] | None:
    """Fallback when vc-server is slow or fail-closed.

    Classification belongs to control-core (`compute_view`). Python only locates
    the binary or admits that the eye is unavailable.
    """
    target = str(run_id or "").strip()
    if not target:
        return None
    derived = _observation_from_control_core(target)
    if derived is not None:
        derived["writer_revalidation"] = reason
        return derived
    from .control_plane import RunNotResolved, lookup_run, resolve_run

    run = lookup_run(target)
    resolved = None
    if run is None:
        try:
            resolved = resolve_run(target)
        except (RunNotResolved, OSError):
            resolved = None
    if run is None and resolved is None:
        return None
    return {
        "schema": "vibecrafted.run-observation.v1",
        "run_id": target,
        "found": True,
        "terminal": False,
        "worker_alive": None,
        "process_truth": "unknown",
        "evidence_disagreement": True,
        "disagreement_reasons": [
            "control_core_observe_unavailable",
            reason,
        ],
        "run": {
            "run_id": target,
            "source": "control_core_observe_unavailable",
        },
        "writer_revalidation": reason,
        "source": "control_core_observe_unavailable",
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
    payload.setdefault("source", "vc-server-compute_view")
    return payload


def _ensure_source(payload: dict[str, Any], default: str) -> dict[str, Any]:
    if not str(payload.get("source") or "").strip():
        payload["source"] = default
    return payload


def observe_run(run_id: str) -> dict[str, Any]:
    """Perform one bounded observation; fall back to control-core if HTTP fails."""
    encoded = urllib.parse.quote(run_id, safe="")
    try:
        payload = _request_json(
            f"/api/control/runs/{encoded}/observe",
            timeout=observe_timeout_seconds(),
        )
    except ServerObservationError as exc:
        local = _local_observation(
            run_id, reason=f"local_fallback_after_server_error:{type(exc).__name__}"
        )
        if local is not None:
            return _ensure_source(local, "control_core_observe_unavailable")
        raise
    if not isinstance(payload, dict):
        raise ServerObservationError("vc-server observe omitted object")
    return _ensure_source(payload, "vc-server-compute_view")


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
