"""One caretaker truth: server identity, observability, resume backlog, upkeep.

The tray used to answer "how is my Vibecrafted doing?" by fusing three
independent sources in Swift: a raw read of ``server/supervisor.status.json``
with no freshness check, a subprocess ``vibecrafted server service status
--json`` payload that carries no schema key at all, and a third subprocess for
log locations. Any drift between them was invisible, and a supervisor that died
mid-write left a receipt still saying ``running`` — which the menu happily
rendered next to a live-looking endpoint.

Three sources, three shapes, no version, no freshness, and the fusion rule
living in a view layer is four ways to be wrong about the same question.

This module is the single owner of that question. It fuses the sources **once,
inside the runtime**, and publishes one versioned envelope
(:data:`CARETAKER_SCHEMA`) into the control plane. The `vibecrafted server`
serves that envelope as its first-violin caretaker surface
(``GET /api/control/caretaker``); the tray and any other reader consume the
same bytes and render the already-derived verdict instead of re-deriving it.

Four properties drive every decision here:

**One verdict, computed once.** :func:`derive_verdict` owns the health call and
emits the header/detail a menu can render verbatim. A consumer that ANDs its
own booleans has forked the truth again.

**Freshness is part of the truth.** Every timestamped input carries its age and
an explicit staleness flag. A receipt nobody refreshed is not evidence of a
running server, and this envelope says so rather than letting the reader assume.

**Fail-open, never fail-silent.** The caretaker answers when the control plane
is corrupt, when the supervisor never ran, when the ledger is unreadable. Each
section carries ``available`` plus a ``reason``; a missing section is reported
as missing, never rendered as healthy.

**Reuse the owners.** Resume classification stays in
:mod:`vibecrafted_core.init_resume`, settlement authority stays in
:mod:`vibecrafted_core.settlements_query`, endpoint config stays in
:mod:`vibecrafted_core.server_config`. This module composes them; it never
re-implements them. A second classifier would be exactly the truth competition
the framework exists to prevent.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CARETAKER_SCHEMA = "vibecrafted.caretaker.v1"

#: Filename of the published envelope inside the control-plane home.
CARETAKER_SNAPSHOT_NAME = "caretaker.json"

#: The supervisor rewrites its receipt on roughly a one-second cadence. Two
#: minutes of silence is not a slow loop, it is a process that stopped.
RECEIPT_STALE_SECONDS = 120.0

#: How long a published caretaker envelope stays worth rendering unqualified.
SNAPSHOT_STALE_SECONDS = 300.0

#: Bounded liveness probe. The caretaker is called from menus and status lines;
#: it may never inherit the supervisor's 60s command budget.
HEALTH_PROBE_TIMEOUT_SECONDS = 1.5

#: Event stream size past which retention deserves an operator decision.
EVENT_STREAM_PRESSURE_BYTES = 16 * 1024 * 1024

#: Runtime run directory count above which evidence retention needs a decision.
RUNTIME_RUN_PRESSURE = 750

#: Upper bound on directories stat-ed during one maintenance pass.
MAINTENANCE_SCAN_CAP = 4000

HEALTHY = "healthy"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"

INFO = "info"
WARN = "warn"
ERROR = "error"

_SEVERITY_ORDER = {INFO: 0, WARN: 1, ERROR: 2}

#: Terminal colour escapes a supervisor error line may carry into a menu row.
_ANSI_NOISE = ("\x1b[31m", "\x1b[0m")


class CaretakerError(RuntimeError):
    """Raised only by the publish path; every read path degrades instead."""


def _utc_now() -> datetime:
    """Timezone-aware now, isolated so tests can reason about one clock."""
    return datetime.now(UTC)


def _age_seconds(path: Path, *, now: float | None = None) -> float | None:
    """Seconds since ``path`` was last modified, or None when unstattable."""
    try:
        modified = path.stat().st_mtime
    except OSError:
        return None
    reference = time.time() if now is None else now
    return max(0.0, reference - modified)


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Read a JSON object, returning (payload, reason-when-absent).

    A corrupt file is a finding, not an exception: the caretaker's whole job is
    to still answer when the plane is damaged.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"not published: {path}"
    except OSError as exc:
        return None, f"unreadable: {type(exc).__name__}: {exc}"
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f"corrupt JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "payload is not a JSON object"
    return payload, ""


def _finding(code: str, severity: str, detail: str, **extra: Any) -> dict[str, Any]:
    """Build one machine-readable finding row."""
    row = {"code": code, "severity": severity, "detail": detail}
    row.update(extra)
    return row


def _worst(findings: Iterable[Mapping[str, Any]]) -> str:
    """Highest severity present, or ``info`` for an empty finding set."""
    worst = INFO
    for finding in findings:
        severity = str(finding.get("severity") or INFO)
        if _SEVERITY_ORDER.get(severity, 0) > _SEVERITY_ORDER.get(worst, 0):
            worst = severity
    return worst


def _concise(value: object, *, width: int = 96) -> str:
    """First line of a message, escape-stripped and clipped for one menu row."""
    text = str(value or "").strip()
    if not text:
        return ""
    line = text.splitlines()[0]
    for noise in _ANSI_NOISE:
        line = line.replace(noise, "")
    line = line.strip()
    if not line:
        return ""
    return line if len(line) <= width else f"{line[: width - 1]}…"


# --------------------------------------------------------------------------
# Homes
# --------------------------------------------------------------------------


def _crafted_home() -> Path:
    """``~/.vibecrafted`` (or ``VIBECRAFTED_HOME``) without importing the writer."""
    from .runtime_paths import vibecrafted_home

    return vibecrafted_home()


def _control_plane_home() -> Path:
    """Control-plane root the server and the Python runtime share."""
    from .control_plane import control_plane_home

    return control_plane_home()


def caretaker_snapshot_path(*, control_plane: Path | None = None) -> Path:
    """Canonical path of the published caretaker envelope."""
    root = control_plane if control_plane is not None else _control_plane_home()
    return root / CARETAKER_SNAPSHOT_NAME


def supervisor_receipt_path(*, home: Path | None = None) -> Path:
    """Canonical path of the supervisor receipt this envelope folds in."""
    root = home if home is not None else _crafted_home()
    return root / "server" / "supervisor.status.json"


# --------------------------------------------------------------------------
# Section: server
# --------------------------------------------------------------------------


def probe_health(
    origin: str, *, timeout: float = HEALTH_PROBE_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Bounded ``GET /api/health`` probe against a declared origin.

    This is the one authoritative liveness fact available on every platform: a
    launchd label can be loaded while the process behind it is wedged, and a
    receipt can claim ``running`` long after its writer died. An HTTP answer
    cannot be faked by a stale file.
    """
    target = f"{str(origin or '').rstrip('/')}/api/health"
    probe: dict[str, Any] = {
        "origin": origin,
        "reachable": False,
        "reason": "",
        "version": "",
    }
    request = urllib.request.Request(  # nosemgrep: dynamic-urllib-use-detected
        target,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(  # nosemgrep: dynamic-urllib-use-detected
            request, timeout=timeout
        ) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        probe["reason"] = f"HTTP {exc.code}"
        return probe
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        probe["reason"] = f"{type(exc).__name__}: {exc}"
        return probe
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        probe["reason"] = f"non-JSON health body: {exc}"
        return probe
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        probe["reason"] = "health endpoint did not report status=ok"
        return probe
    probe["reachable"] = True
    probe["version"] = str(payload.get("version") or "")
    probe["schema"] = str(payload.get("schema") or "")
    return probe


def build_server_section(
    *,
    home: Path | None = None,
    probe: bool = True,
    service: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Fuse endpoint config, supervisor receipt, liveness, and service facts.

    ``service`` is the optional ``vibecrafted server service status --json``
    payload. It is injected rather than recomputed because obtaining it means a
    launchctl query plus a pair-health command run: real facts, but far too
    heavy for a surface a menu polls. Absent, the section says so honestly
    instead of guessing installation state.
    """
    section: dict[str, Any] = {
        "available": False,
        "reason": "",
        "endpoint": None,
        "state": "",
        "supervisor_pid": None,
        "managed_pair": None,
        "last_error": None,
        "receipt": {
            "path": "",
            "present": False,
            "age_seconds": None,
            "stale": True,
            "reason": "",
        },
        "liveness": {
            "probed": False,
            "reachable": False,
            "reason": "not probed",
            "version": "",
        },
        "service": None,
    }

    try:
        from .server_config import load_server_config

        config = load_server_config()
        section["endpoint"] = {
            "host": config.bind_host,
            "port": config.port,
            "url": config.public_url,
        }
    except Exception as exc:  # noqa: BLE001 - a bad config must not blind the menu
        section["reason"] = f"server config unreadable: {type(exc).__name__}: {exc}"

    receipt_path = supervisor_receipt_path(home=home)
    section["receipt"]["path"] = str(receipt_path)
    receipt, receipt_reason = _read_json(receipt_path)
    if receipt is None:
        section["receipt"]["reason"] = receipt_reason
        if not section["reason"]:
            section["reason"] = receipt_reason
    else:
        age = _age_seconds(receipt_path, now=now)
        section["available"] = True
        section["receipt"]["present"] = True
        section["receipt"]["age_seconds"] = age
        section["receipt"]["stale"] = age is None or age > RECEIPT_STALE_SECONDS
        section["receipt"]["schema"] = str(receipt.get("schema") or "")
        section["state"] = str(receipt.get("state") or "")
        section["supervisor_pid"] = receipt.get("supervisor_pid")
        section["service_managed"] = receipt.get("service_managed")
        section["last_error"] = receipt.get("last_error")
        section["started_at"] = receipt.get("started_at")
        section["updated_at"] = receipt.get("updated_at")
        section["consecutive_failures"] = receipt.get("consecutive_failures")
        pair = receipt.get("managed_pair")
        section["managed_pair"] = dict(pair) if isinstance(pair, Mapping) else None
        endpoint = receipt.get("endpoint")
        if isinstance(endpoint, Mapping) and section["endpoint"] is None:
            section["endpoint"] = {
                "host": endpoint.get("host"),
                "port": endpoint.get("port"),
                "url": endpoint.get("public_url") or endpoint.get("url"),
            }

    if probe:
        origin = ""
        endpoint = section["endpoint"]
        if isinstance(endpoint, Mapping):
            origin = str(endpoint.get("url") or "")
        if origin:
            result = probe_health(origin)
            section["liveness"] = {
                "probed": True,
                "reachable": bool(result.get("reachable")),
                "reason": str(result.get("reason") or ""),
                "version": str(result.get("version") or ""),
            }
        else:
            section["liveness"]["reason"] = "no endpoint origin to probe"

    if service is not None:
        section["service"] = {
            key: service.get(key)
            for key in (
                "installed",
                "loaded",
                "supervisor_live",
                "supervisor_verified",
                "supervisor_service_managed",
                "build_current",
                "pair_healthy",
                "supervisor_pid",
            )
        }
    return section


# --------------------------------------------------------------------------
# Section: observability
# --------------------------------------------------------------------------


def build_observability_section(
    *, control_plane: Path | None = None, now: float | None = None
) -> dict[str, Any]:
    """Count what the control plane actually holds for a reader to observe."""
    root = control_plane if control_plane is not None else _control_plane_home()
    section: dict[str, Any] = {
        "available": False,
        "reason": "",
        "control_plane": str(root),
        "run_snapshots": 0,
        "runtime_run_dirs": 0,
        "lifecycle_runs": 0,
        "event_stream_bytes": 0,
        "event_stream_age_seconds": None,
    }
    if not root.is_dir():
        section["reason"] = f"control plane home is not a directory: {root}"
        return section

    section["available"] = True
    for key, child in (
        ("run_snapshots", root / "runs"),
        ("runtime_run_dirs", root / "runtime_runs"),
        ("lifecycle_runs", root / "lifecycle_runs"),
    ):
        try:
            with os.scandir(child) as entries:
                section[key] = sum(1 for _ in entries)
        except OSError:
            section[key] = 0

    events = root / "events.jsonl"
    try:
        section["event_stream_bytes"] = events.stat().st_size
    except OSError:
        section["event_stream_bytes"] = 0
    section["event_stream_age_seconds"] = _age_seconds(events, now=now)
    return section


# --------------------------------------------------------------------------
# Section: resumeability
# --------------------------------------------------------------------------


def build_resumeability_section(
    *, roots: Iterable[str | Path] | None = None, limit: int = 5
) -> dict[str, Any]:
    """Project the ``n`` settlement bucket onto the resume classes.

    Classification is delegated to :func:`init_resume.classify_resume_row`
    verbatim. The caretaker's contribution is reach — the same backlog init
    prints into an agent prompt becomes visible to a tray, a status line, and
    an HTTP reader without any of them re-deriving what "resumable" means.
    """
    section: dict[str, Any] = {
        "available": False,
        "reason": "",
        "matched": 0,
        "counts": {},
        "classes": {},
        "roots": [],
        "command": "vibecrafted settlements list --bucket n --revalidatable",
    }
    try:
        from .init_resume import RESUME_CLASSES, classify_resume_row, resume_command
        from .settlements_query import list_settlements
    except Exception as exc:  # noqa: BLE001 - import faults must not brick status
        section["reason"] = f"{type(exc).__name__}: {exc}"
        return section

    section["counts"] = dict.fromkeys(RESUME_CLASSES, 0)
    section["classes"] = {name: [] for name in RESUME_CLASSES}

    wanted: set[str] = set()
    for root in roots or ():
        text = str(root or "").strip()
        if not text:
            continue
        wanted.add(_resolved(text))
    section["roots"] = sorted(wanted)

    try:
        rows = list_settlements(bucket="n").get("runs") or []
    except Exception as exc:  # noqa: BLE001 - a corrupt ledger is a finding
        section["reason"] = f"{type(exc).__name__}: {exc}"
        return section

    section["available"] = True
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if wanted and _resolved(row.get("root")) not in wanted:
            continue
        bucket = classify_resume_row(row)
        section["counts"][bucket] = section["counts"].get(bucket, 0) + 1
        section["matched"] += 1
        entries = section["classes"].setdefault(bucket, [])
        if len(entries) < max(0, int(limit)):
            entries.append(
                {
                    "run_id": str(row.get("run_id") or ""),
                    "agent": str(row.get("agent") or ""),
                    "skill": str(row.get("skill") or ""),
                    "state": str(row.get("state") or ""),
                    "reason": str(row.get("reason") or ""),
                    "root": str(row.get("root") or ""),
                    "settled_at": str(row.get("settled_at") or ""),
                    "command": resume_command(row),
                }
            )
    return section


def _resolved(raw: object) -> str:
    """Best-effort absolute path string; '' when the value is unusable."""
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return text


# --------------------------------------------------------------------------
# Section: maintenance
# --------------------------------------------------------------------------


def build_maintenance_section(*, control_plane: Path | None = None) -> dict[str, Any]:
    """Findings about the control plane itself, not about the runs inside it.

    Retention pressure is the failure mode this catches earliest: run
    directories accumulate for months while snapshots are pruned, and the first
    symptom an operator sees is an unrelated command getting slow.
    """
    root = control_plane if control_plane is not None else _control_plane_home()
    section: dict[str, Any] = {
        "available": False,
        "reason": "",
        "control_plane": str(root),
        "scanned": 0,
        "capped": False,
        "orphan_runtime_runs": 0,
        "corrupt_run_snapshots": 0,
        "findings": [],
    }
    if not root.is_dir():
        section["reason"] = f"control plane home is not a directory: {root}"
        section["findings"] = [
            _finding("control_plane_missing", ERROR, section["reason"])
        ]
        return section

    section["available"] = True
    findings: list[dict[str, Any]] = []

    orphans = 0
    scanned = 0
    try:
        with os.scandir(root / "runtime_runs") as entries:
            for entry in entries:
                if scanned >= MAINTENANCE_SCAN_CAP:
                    section["capped"] = True
                    break
                if not entry.is_dir():
                    continue
                scanned += 1
                if not (Path(entry.path) / "meta.json").exists():
                    orphans += 1
    except OSError:
        pass
    section["scanned"] = scanned
    section["orphan_runtime_runs"] = orphans

    corrupt = 0
    try:
        with os.scandir(root / "runs") as entries:
            for entry in entries:
                if not entry.name.endswith(".json"):
                    continue
                payload, _ = _read_json(Path(entry.path))
                if payload is None:
                    corrupt += 1
    except OSError:
        pass
    section["corrupt_run_snapshots"] = corrupt

    if corrupt:
        findings.append(
            _finding(
                "corrupt_run_snapshots",
                ERROR,
                f"{corrupt} run snapshot(s) cannot be parsed; those runs read as absent",
                count=corrupt,
            )
        )
    if orphans:
        findings.append(
            _finding(
                "orphan_runtime_runs",
                WARN,
                f"{orphans} runtime run directory(ies) carry no meta.json; "
                "evidence exists but identity does not",
                count=orphans,
            )
        )
    if scanned >= RUNTIME_RUN_PRESSURE:
        findings.append(
            _finding(
                "runtime_run_retention",
                WARN,
                f"{scanned} runtime run directories retained; retention deserves an "
                "operator decision before the plane gets slow",
                count=scanned,
            )
        )

    try:
        size = (root / "events.jsonl").stat().st_size
    except OSError:
        size = 0
    if size >= EVENT_STREAM_PRESSURE_BYTES:
        findings.append(
            _finding(
                "event_stream_pressure",
                WARN,
                f"events.jsonl is {size // (1024 * 1024)} MiB; rotation is overdue",
                byte_size=size,
            )
        )

    section["findings"] = findings
    return section


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------


def derive_verdict(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """The single health call, rendered ready for a menu to print verbatim.

    Every consumer that re-derives this from raw booleans has forked the truth.
    The header and detail are deliberately surface-agnostic strings: a tray
    item, a status line, and an HTTP client all show the same sentence.
    """
    server = snapshot.get("server") if isinstance(snapshot, Mapping) else None
    server = server if isinstance(server, Mapping) else {}
    endpoint = server.get("endpoint")
    suffix = ""
    if isinstance(endpoint, Mapping) and endpoint.get("host") and endpoint.get("port"):
        suffix = f" · {endpoint['host']}:{endpoint['port']}"

    liveness = server.get("liveness")
    liveness = liveness if isinstance(liveness, Mapping) else {}
    receipt = server.get("receipt")
    receipt = receipt if isinstance(receipt, Mapping) else {}
    service = server.get("service")

    findings: list[dict[str, Any]] = []
    maintenance = snapshot.get("maintenance") if isinstance(snapshot, Mapping) else None
    if isinstance(maintenance, Mapping):
        findings.extend(
            dict(row)
            for row in maintenance.get("findings") or ()
            if isinstance(row, Mapping)
        )

    if bool(liveness.get("reachable")):
        health = HEALTHY
        header = f"VC Server: HEALTHY{suffix}"
        pid = server.get("supervisor_pid")
        detail = f"Supervisor PID {pid}" if pid else "Answering /api/health"
        if receipt.get("present") and receipt.get("stale"):
            health = DEGRADED
            header = f"VC Server: SERVING, RECEIPT STALE{suffix}"
            detail = (
                "The endpoint answers but the supervisor receipt stopped "
                "refreshing — the server is not being supervised"
            )
            findings.append(
                _finding(
                    "supervisor_receipt_stale",
                    WARN,
                    detail,
                    age_seconds=receipt.get("age_seconds"),
                )
            )
    elif not liveness.get("probed"):
        health = UNKNOWN
        header = f"VC Server: UNKNOWN{suffix}"
        detail = "Liveness was not probed for this snapshot"
    elif isinstance(service, Mapping) and service.get("installed") is False:
        health = UNAVAILABLE
        header = f"VC Server: NOT INSTALLED{suffix}"
        detail = "Install the canonical VC Server service first"
    elif isinstance(service, Mapping) and service.get("loaded") is False:
        health = UNAVAILABLE
        header = f"VC Server: STOPPED{suffix}"
        detail = "Service is intentionally stopped"
    else:
        health = UNAVAILABLE
        header = f"VC Server: UNREACHABLE{suffix}"
        detail = (
            _concise(server.get("last_error"))
            or _concise(liveness.get("reason"))
            or "The declared endpoint did not answer"
        )
        findings.append(_finding("server_unreachable", ERROR, detail))

    # A serving endpoint over a groaning control plane is not "healthy": the
    # caretaker owns observability, resumeability and upkeep, not just a port.
    # Demoting the verdict while leaving the header saying HEALTHY would fork
    # this envelope's own truth, so the header carries the demotion too.
    # The server leg's own verdict, before control-plane upkeep is folded in.
    # Kept separate so "the port answers" and "the plane is well kept" stay
    # distinguishable to a reader that has to act on one of them.
    server_health = health

    # A serving endpoint over a groaning control plane is not "healthy": the
    # caretaker owns observability, resumeability and upkeep, not just a port.
    # Demoting the verdict while leaving the header saying HEALTHY would fork
    # this envelope's own truth, so the header carries the demotion too.
    if health == HEALTHY and _worst(findings) != INFO:
        health = DEGRADED
        plural = "" if len(findings) == 1 else "s"
        header = f"{header} · {len(findings)} upkeep item{plural}"
        detail = _concise(findings[0].get("detail")) or detail

    findings.sort(key=lambda row: -_SEVERITY_ORDER.get(str(row.get("severity")), 0))
    return {
        "health": health,
        "server_health": server_health,
        "header": header,
        "detail": detail,
        "findings": findings,
    }


# --------------------------------------------------------------------------
# Envelope
# --------------------------------------------------------------------------


def build_caretaker_snapshot(
    *,
    home: Path | None = None,
    control_plane: Path | None = None,
    roots: Iterable[str | Path] | None = None,
    probe: bool = True,
    service: Mapping[str, Any] | None = None,
    resume_limit: int = 5,
) -> dict[str, Any]:
    """Compose the whole caretaker envelope. Never raises."""
    plane = control_plane if control_plane is not None else _control_plane_home()
    snapshot: dict[str, Any] = {
        "schema": CARETAKER_SCHEMA,
        "generated_at": _utc_now().isoformat(),
        "control_plane": str(plane),
        "server": build_server_section(home=home, probe=probe, service=service),
        "observability": build_observability_section(control_plane=plane),
        "resumeability": build_resumeability_section(roots=roots, limit=resume_limit),
        "maintenance": build_maintenance_section(control_plane=plane),
    }
    snapshot["verdict"] = derive_verdict(snapshot)
    return snapshot


def publish_caretaker_snapshot(
    snapshot: Mapping[str, Any] | None = None,
    *,
    control_plane: Path | None = None,
    **kwargs: Any,
) -> Path:
    """Atomically publish the envelope so the server can serve it.

    Publication is durable for the same reason run receipts are: an envelope
    truncated by a crash would read as "no sections available", and a reader
    cannot distinguish that from a genuinely empty plane.
    """
    plane = control_plane if control_plane is not None else _control_plane_home()
    payload = dict(
        snapshot
        if snapshot is not None
        else build_caretaker_snapshot(control_plane=plane, **kwargs)
    )
    target = caretaker_snapshot_path(control_plane=plane)
    try:
        from .control_plane import _write_json_durable

        _write_json_durable(target, payload)
    except Exception as exc:
        raise CaretakerError(
            f"cannot publish caretaker snapshot to {target}: {exc}"
        ) from exc
    return target


def read_caretaker_snapshot(
    *, control_plane: Path | None = None, now: float | None = None
) -> dict[str, Any]:
    """Read the published envelope with its freshness attached.

    The returned view is the same shape the HTTP route serves, so a reader that
    falls back to the file when the server is down parses one thing, not two.
    """
    plane = control_plane if control_plane is not None else _control_plane_home()
    target = caretaker_snapshot_path(control_plane=plane)
    payload, reason = _read_json(target)
    age = _age_seconds(target, now=now)
    return {
        "schema": CARETAKER_SCHEMA,
        "path": str(target),
        "published": payload is not None,
        "reason": reason,
        "age_seconds": age,
        "stale": payload is None or age is None or age > SNAPSHOT_STALE_SECONDS,
        "snapshot": payload,
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m vibecrafted_core.caretaker`` — build, print, or publish."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="vibecrafted-caretaker",
        description="One caretaker truth for server, observability, resume, and upkeep.",
    )
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="focus the resume backlog on one checkout (repeatable)",
    )
    parser.add_argument(
        "--no-probe", action="store_true", help="skip the bounded liveness probe"
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="write the envelope into the control plane",
    )
    parser.add_argument(
        "--read",
        action="store_true",
        help="read the published envelope instead of building",
    )
    parser.add_argument("--json", action="store_true", help="print the payload as JSON")
    args = parser.parse_args(argv)

    if args.read:
        payload: dict[str, Any] = read_caretaker_snapshot()
        published = payload.get("snapshot")
        verdict = published.get("verdict") if isinstance(published, Mapping) else {}
    else:
        payload = build_caretaker_snapshot(
            roots=args.root or None, probe=not args.no_probe
        )
        verdict = payload.get("verdict") or {}
        if args.publish:
            publish_caretaker_snapshot(payload)

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    verdict = verdict if isinstance(verdict, Mapping) else {}
    print(verdict.get("header") or "VC Server: UNKNOWN")
    detail = verdict.get("detail")
    if detail:
        print(f"  {detail}")
    for finding in verdict.get("findings") or ():
        if isinstance(finding, Mapping):
            print(
                f"  [{finding.get('severity')}] "
                f"{finding.get('code')}: {finding.get('detail')}"
            )
    return 0


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
