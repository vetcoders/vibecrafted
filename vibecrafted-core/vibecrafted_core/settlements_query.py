"""Read-only public settlements query surface.

Ledger authority remains ``settlement_ledger.jsonl`` via
``read_settlement_ledger``. This module projects that immutable lower-bound
history for operators: summary, filtered lists, pathology groups, and
per-run inspect. It never mutates the ledger, never invents ``f``, and never
pretends pre-ledger history is complete.

``--revalidatable`` means *evidence still on disk for a deliberate
verifier/trust campaign* (report + transcript present). It is not Guardian
auto-resume eligibility — historical pre-ledger ``n`` rows lack trust
receipts and ``native_resume_candidate``.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .control_plane import control_plane_home, run_snapshot_dir
from .settlement_ledger import read_settlement_ledger, settlement_ledger_path

SETTLEMENTS_QUERY_SCHEMA = "vibecrafted.settlements-query.v1"
_TUI_BUCKETS = frozenset({"f", "x", "n"})
_GROUP_FIELDS = frozenset({"agent", "skill", "reason", "root", "state", "verdict"})
# Inventory signal only: exact operator forensic token ``completed``.
# Broader success states remain visible on the row via ``state``.
_COMPLETED_STATES = frozenset({"completed"})


class SettlementsQueryError(ValueError):
    """Invalid settlements query argument or missing subject."""


def _path_is_file(value: object) -> bool:
    """True when ``value`` is a non-empty path string pointing at an existing file."""
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    try:
        return Path(text).is_file()
    except OSError:
        return False


def _path_is_dir(value: object) -> bool:
    """True when ``value`` is a non-empty path string pointing at an existing directory."""
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    try:
        return Path(text).is_dir()
    except OSError:
        return False


def _read_json_object(path: Path) -> dict[str, Any] | None:
    """Read a JSON object file, returning None on any read/decode/shape failure."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    return dict(payload)


def _runtime_run_dir(run_id: str) -> Path:
    """Return the runtime run directory for ``run_id`` under the control plane."""
    return control_plane_home() / "runtime_runs" / run_id


def _load_run_snapshot(run_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """Prefer the live control-plane snapshot; fall back to archive."""

    live = run_snapshot_dir() / f"{run_id}.json"
    archive = run_snapshot_dir() / "archive" / f"{run_id}.json"
    for path, source in ((live, "live"), (archive, "archive")):
        if not path.is_file():
            continue
        payload = _read_json_object(path)
        if payload is not None:
            return payload, source
    return None, None


def _pick_path(*candidates: object) -> str:
    """Return the first non-empty stripped string among ``candidates``, else ""."""
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _discover_runtime_artifact(run_dir: Path, *name_globs: str) -> str:
    """Return the first non-empty file matching any glob pattern in ``run_dir``."""
    if not run_dir.is_dir():
        return ""
    for pattern in name_globs:
        for path in sorted(run_dir.glob(pattern)):
            try:
                if path.is_file() and path.stat().st_size > 0:
                    return str(path)
            except OSError:
                continue
    return ""


def _resolve_artifacts(
    run_id: str, snapshot: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Resolve report/transcript paths for a run from snapshot, meta, then glob discovery.

    Falls back to on-disk discovery only when the recorded path is missing or
    stale (does not point at an existing file).
    """
    snap = snapshot or {}
    artifacts = (
        snap.get("artifacts") if isinstance(snap.get("artifacts"), Mapping) else {}
    )
    report = _pick_path(
        snap.get("report_path"),
        snap.get("latest_report"),
        snap.get("report"),
        artifacts.get("report_path") if isinstance(artifacts, Mapping) else "",
    )
    transcript = _pick_path(
        snap.get("transcript_path"),
        snap.get("latest_transcript"),
        snap.get("transcript"),
        artifacts.get("transcript_path") if isinstance(artifacts, Mapping) else "",
    )
    run_dir = _runtime_run_dir(run_id)
    meta_path = run_dir / "meta.json"
    meta = _read_json_object(meta_path) if meta_path.is_file() else None
    if meta:
        report = _pick_path(
            report,
            meta.get("report_path"),
            meta.get("latest_report"),
            meta.get("report"),
        )
        transcript = _pick_path(
            transcript,
            meta.get("transcript_path"),
            meta.get("latest_transcript"),
            meta.get("transcript"),
        )
    if not _path_is_file(report):
        report = (
            _discover_runtime_artifact(run_dir, "*report*", "report.md", "REPORT.md")
            or report
        )
    if not _path_is_file(transcript):
        transcript = (
            _discover_runtime_artifact(
                run_dir, "transcript*", "transcript.log", "*.log"
            )
            or transcript
        )
    report_on_disk = _path_is_file(report)
    transcript_on_disk = _path_is_file(transcript)
    return {
        "report_path": report if report else None,
        "transcript_path": transcript if transcript else None,
        "report_on_disk": report_on_disk,
        "transcript_on_disk": transcript_on_disk,
        "runtime_run_dir": str(run_dir) if run_dir.is_dir() else None,
        "runtime_meta_path": str(meta_path) if meta_path.is_file() else None,
    }


def _truthy_mapping(value: object) -> bool:
    """True when ``value`` is a non-empty mapping."""
    return isinstance(value, Mapping) and bool(value)


def _enrich_record(
    run_id: str,
    ledger_record: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge one ledger row with control-plane snapshot/artifact context for display.

    Read-only best-effort enrichment: a missing snapshot degrades fields to
    None/False rather than raising.
    """
    snapshot, snapshot_source = _load_run_snapshot(run_id)
    artifacts = _resolve_artifacts(run_id, snapshot)
    snap = snapshot or {}
    settlement = (
        snap.get("settlement") if isinstance(snap.get("settlement"), Mapping) else {}
    )
    agent = str(snap.get("agent") or "").strip()
    skill = str(snap.get("skill") or "").strip()
    reason = str(
        snap.get("settlement_reason")
        or (settlement.get("reason") if isinstance(settlement, Mapping) else "")
        or ""
    ).strip()
    root = str(snap.get("root") or snap.get("source_dir") or "").strip()
    state = str(snap.get("state") or snap.get("status") or "").strip()
    verdict = str(
        snap.get("settlement_verdict")
        or (settlement.get("verdict") if isinstance(settlement, Mapping) else "")
        or ledger_record.get("settlement_verdict")
        or ""
    ).strip()
    exit_code = snap.get("exit_code")
    launcher_pid = snap.get("launcher_pid")
    last_error = str(snap.get("last_error") or "").strip()
    trust_receipt = snap.get("trust_receipt") or snap.get("trust_receipt_v2")
    native_resume = snap.get("native_resume_candidate")
    if native_resume is None and isinstance(snap.get("lifecycle"), Mapping):
        native_resume = snap["lifecycle"].get("native_resume_candidate")
    revalidatable = bool(
        artifacts["report_on_disk"] and artifacts["transcript_on_disk"]
    )
    checkout_exists = _path_is_dir(root)
    completed_state = state.lower() in _COMPLETED_STATES
    exit_0 = exit_code == 0
    return {
        "run_id": run_id,
        "settlement_tui": str(ledger_record.get("settlement_tui") or ""),
        "settlement_revision": ledger_record.get("settlement_revision"),
        "ledger_record_type": ledger_record.get("record_type"),
        "agent": agent or None,
        "skill": skill or None,
        "reason": reason or None,
        "root": root or None,
        "state": state or None,
        "verdict": verdict or None,
        "exit_code": exit_code,
        "launcher_pid": launcher_pid,
        "last_error": last_error or None,
        "snapshot_source": snapshot_source,
        "has_snapshot": snapshot is not None,
        "checkout_exists": checkout_exists,
        "exit_0": exit_0,
        "completed_state": completed_state,
        "revalidatable": revalidatable,
        "native_resume_candidate": bool(native_resume),
        "trust_receipt_present": _truthy_mapping(trust_receipt)
        or bool(str(trust_receipt or "").strip()),
        "report_path": artifacts["report_path"],
        "transcript_path": artifacts["transcript_path"],
        "report_on_disk": artifacts["report_on_disk"],
        "transcript_on_disk": artifacts["transcript_on_disk"],
        "runtime_run_dir": artifacts["runtime_run_dir"],
        "runtime_meta_path": artifacts["runtime_meta_path"],
        "settlement_source": snap.get("settlement_source")
        or (settlement.get("source") if isinstance(settlement, Mapping) else None),
        "settled_at": snap.get("settlement_at")
        or (settlement.get("settled_at") if isinstance(settlement, Mapping) else None),
    }


def _iter_enriched(
    *,
    bucket: str | None = None,
    revalidatable: bool = False,
) -> list[dict[str, Any]]:
    """Return enriched latest-by-run ledger rows, optionally filtered by bucket/revalidatable."""
    ledger = read_settlement_ledger()
    latest = ledger.get("latest_by_run") or {}
    if not isinstance(latest, Mapping):
        return []
    rows: list[dict[str, Any]] = []
    for run_id in sorted(str(key) for key in latest):
        record = latest[run_id]
        if not isinstance(record, Mapping):
            continue
        tui = str(record.get("settlement_tui") or "")
        if bucket is not None and tui != bucket:
            continue
        enriched = _enrich_record(run_id, record)
        if revalidatable and not enriched["revalidatable"]:
            continue
        rows.append(enriched)
    return rows


def _parse_group_fields(group: str | Sequence[str] | None) -> tuple[str, ...]:
    """Parse a comma-string or sequence of group-by field names, validating against the allowlist.

    Raises ``SettlementsQueryError`` for any field outside ``_GROUP_FIELDS``.
    """
    if group is None or group == "":
        return ()
    if isinstance(group, str):
        fields = [part.strip() for part in group.split(",") if part.strip()]
    else:
        fields = [str(part).strip() for part in group if str(part).strip()]
    if not fields:
        return ()
    unknown = [field for field in fields if field not in _GROUP_FIELDS]
    if unknown:
        raise SettlementsQueryError(
            "unknown group field(s): "
            + ", ".join(unknown)
            + f" (allowed: {', '.join(sorted(_GROUP_FIELDS))})"
        )
    return tuple(fields)


def _group_rows(
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
    *,
    sample_limit: int = 5,
) -> list[dict[str, Any]]:
    """Group rows by the given field tuple, largest group first, with a sample of run_ids."""
    buckets: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = tuple(row.get(field) for field in fields)
        buckets.setdefault(key, []).append(row)
    grouped: list[dict[str, Any]] = []
    for key, members in sorted(
        buckets.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        key_obj = {field: key[index] for index, field in enumerate(fields)}
        grouped.append(
            {
                "key": key_obj,
                "count": len(members),
                "sample_run_ids": [str(m["run_id"]) for m in members[:sample_limit]],
            }
        )
    return grouped


def settlements_summary() -> dict[str, Any]:
    """Durable f/x/n lower-bound summary plus revalidation inventory signals."""

    ledger = read_settlement_ledger()
    counts = ledger.get("counts") or {}
    latest_counts = counts.get("latest_by_run") or {"f": 0, "x": 0, "n": 0, "total": 0}
    historical = counts.get("historical_transitions") or {
        "f": 0,
        "x": 0,
        "n": 0,
        "total": 0,
    }
    integrity = ledger.get("integrity") or {}
    rows = _iter_enriched()
    by_bucket = Counter(str(row.get("settlement_tui") or "") for row in rows)
    revalidatable_n = sum(
        1
        for row in rows
        if row.get("settlement_tui") == "n" and row.get("revalidatable")
    )
    n_signals = {
        "with_snapshot": sum(
            1
            for row in rows
            if row.get("settlement_tui") == "n" and row.get("has_snapshot")
        ),
        "report_and_transcript_on_disk": revalidatable_n,
        "exit_0": sum(
            1 for row in rows if row.get("settlement_tui") == "n" and row.get("exit_0")
        ),
        "completed_state": sum(
            1
            for row in rows
            if row.get("settlement_tui") == "n" and row.get("completed_state")
        ),
        "checkout_exists": sum(
            1
            for row in rows
            if row.get("settlement_tui") == "n" and row.get("checkout_exists")
        ),
        "native_resume_candidate": sum(
            1
            for row in rows
            if row.get("settlement_tui") == "n" and row.get("native_resume_candidate")
        ),
        "trust_receipt_present": sum(
            1
            for row in rows
            if row.get("settlement_tui") == "n" and row.get("trust_receipt_present")
        ),
    }
    x_signals = {
        "with_snapshot": sum(
            1
            for row in rows
            if row.get("settlement_tui") == "x" and row.get("has_snapshot")
        ),
        "report_and_transcript_on_disk": sum(
            1
            for row in rows
            if row.get("settlement_tui") == "x" and row.get("revalidatable")
        ),
        "skill_marbles": sum(
            1
            for row in rows
            if row.get("settlement_tui") == "x"
            and "marble" in str(row.get("skill") or "").lower()
        ),
        "launcher_pid_missing": sum(
            1
            for row in rows
            if row.get("settlement_tui") == "x"
            and (
                row.get("launcher_pid") in (None, "", 0)
                or "launcher_pid is missing" in str(row.get("last_error") or "")
            )
        ),
    }
    return {
        "schema": SETTLEMENTS_QUERY_SCHEMA,
        "authority": "settlement_ledger",
        "read_only": True,
        "ledger_path": str(settlement_ledger_path()),
        "count_semantics": "known_v2_lower_bound",
        "history_complete": bool(integrity.get("history_complete")),
        "backfill_status": integrity.get("backfill_status"),
        "historical_coverage": integrity.get("historical_coverage"),
        "counts": {
            "historical_transitions": dict(historical),
            "latest_by_run": dict(latest_counts),
        },
        "latest_bucket_scan": {
            "f": int(by_bucket.get("f", 0)),
            "x": int(by_bucket.get("x", 0)),
            "n": int(by_bucket.get("n", 0)),
            "total": len(rows),
        },
        "n_inventory": n_signals,
        "x_inventory": x_signals,
        "revalidatable_n": revalidatable_n,
        "guardian_auto_resume_note": (
            "Historical pre-ledger n rows are not Guardian auto-resume candidates. "
            "Guardian requires a fresh V2 settlement with /vc-trust receipt, dead "
            "worker, recovery_required, unused attempt, and /vc-guard consent. "
            "Use settlements list --bucket n --revalidatable for a deliberate "
            "revalidation campaign that writes a new revision, not a blind resume."
        ),
        "history_gaps": list(ledger.get("history_gaps") or []),
        "integrity": dict(integrity) if isinstance(integrity, Mapping) else integrity,
    }


def list_settlements(
    *,
    bucket: str | None = None,
    revalidatable: bool = False,
    group: str | Sequence[str] | None = None,
    limit: int | None = None,
    sample_limit: int = 5,
) -> dict[str, Any]:
    """List latest-by-run settlements, optionally filtered and grouped."""

    if bucket is not None:
        bucket = str(bucket).strip().lower()
        if bucket not in _TUI_BUCKETS:
            raise SettlementsQueryError(
                f"invalid bucket {bucket!r}; expected one of f, x, n"
            )
    if limit is not None and (type(limit) is not int or limit < 0):
        raise SettlementsQueryError("limit must be a non-negative integer")
    fields = _parse_group_fields(group)
    rows = _iter_enriched(bucket=bucket, revalidatable=revalidatable)
    payload: dict[str, Any] = {
        "schema": SETTLEMENTS_QUERY_SCHEMA,
        "authority": "settlement_ledger",
        "read_only": True,
        "bucket": bucket,
        "revalidatable": bool(revalidatable),
        "matched": len(rows),
        "revalidatable_definition": (
            "report_on_disk AND transcript_on_disk "
            "(evidence for a deliberate verifier/trust campaign; not Guardian auto-resume)"
        ),
    }
    if fields:
        payload["group"] = list(fields)
        payload["groups"] = _group_rows(rows, fields, sample_limit=sample_limit)
        payload["runs"] = []
    else:
        limited = rows if limit is None else rows[:limit]
        payload["limit"] = limit
        payload["returned"] = len(limited)
        payload["runs"] = limited
        payload["groups"] = []
    return payload


def inspect_settlement(run_id: str) -> dict[str, Any]:
    """Inspect one run's ledger baseline plus control-plane enrichment."""

    rid = str(run_id or "").strip()
    if not rid:
        raise SettlementsQueryError("run_id is required")
    ledger = read_settlement_ledger()
    latest = ledger.get("latest_by_run") or {}
    if not isinstance(latest, Mapping) or rid not in latest:
        raise SettlementsQueryError(f"run not present in settlement ledger: {rid}")
    record = latest[rid]
    if not isinstance(record, Mapping):
        raise SettlementsQueryError(f"corrupt ledger row for {rid}")
    enriched = _enrich_record(rid, record)
    snapshot, snapshot_source = _load_run_snapshot(rid)
    transitions = [
        dict(item)
        for item in (ledger.get("historical_transitions") or [])
        if isinstance(item, Mapping) and str(item.get("run_id") or "") == rid
    ]
    return {
        "schema": SETTLEMENTS_QUERY_SCHEMA,
        "authority": "settlement_ledger",
        "read_only": True,
        "run_id": rid,
        "ledger": dict(record),
        "enriched": enriched,
        "snapshot_source": snapshot_source,
        "snapshot": snapshot,
        "historical_transitions_for_run": transitions,
        "history_gaps": [
            dict(gap)
            for gap in (ledger.get("history_gaps") or [])
            if isinstance(gap, Mapping)
            and (
                gap.get("run_id") in (None, rid)
                or gap.get("kind") == "preledger_history_unknown"
            )
        ],
    }


def render_settlements_summary_text(payload: Mapping[str, Any]) -> str:
    """Render ``settlements_summary`` payload as plain human-readable text."""
    counts = payload.get("counts") or {}
    latest = counts.get("latest_by_run") or {}
    historical = counts.get("historical_transitions") or {}
    n_inv = payload.get("n_inventory") or {}
    x_inv = payload.get("x_inventory") or {}
    lines = [
        "settlements summary (read-only, ledger lower-bound)",
        f"ledger: {payload.get('ledger_path')}",
        (
            f"semantics: {payload.get('count_semantics')} "
            f"history_complete={payload.get('history_complete')} "
            f"backfill={payload.get('backfill_status')}"
        ),
        (
            "latest_by_run: "
            f"f={latest.get('f', 0)} x={latest.get('x', 0)} "
            f"n={latest.get('n', 0)} total={latest.get('total', 0)}"
        ),
        (
            "historical_transitions: "
            f"f={historical.get('f', 0)} x={historical.get('x', 0)} "
            f"n={historical.get('n', 0)} total={historical.get('total', 0)}"
        ),
        (
            "n inventory: "
            f"snapshot={n_inv.get('with_snapshot', 0)} "
            f"revalidatable(report+transcript)={n_inv.get('report_and_transcript_on_disk', 0)} "
            f"exit_0={n_inv.get('exit_0', 0)} "
            f"completed={n_inv.get('completed_state', 0)} "
            f"checkout={n_inv.get('checkout_exists', 0)} "
            f"trust_receipt={n_inv.get('trust_receipt_present', 0)} "
            f"native_resume={n_inv.get('native_resume_candidate', 0)}"
        ),
        (
            "x inventory: "
            f"snapshot={x_inv.get('with_snapshot', 0)} "
            f"report+transcript={x_inv.get('report_and_transcript_on_disk', 0)} "
            f"skill_marbles={x_inv.get('skill_marbles', 0)} "
            f"launcher_pid_missing={x_inv.get('launcher_pid_missing', 0)}"
        ),
        f"note: {payload.get('guardian_auto_resume_note')}",
    ]
    return "\n".join(lines)


def render_settlements_list_text(payload: Mapping[str, Any]) -> str:
    """Render ``list_settlements`` payload (grouped or flat) as plain human-readable text."""
    lines = [
        (
            "settlements list "
            f"bucket={payload.get('bucket') or '*'} "
            f"revalidatable={payload.get('revalidatable')} "
            f"matched={payload.get('matched', 0)}"
        )
    ]
    groups = payload.get("groups") or []
    if groups:
        lines.append(f"group={','.join(payload.get('group') or [])}")
        for group in groups:
            key = group.get("key") or {}
            key_bits = " ".join(f"{k}={v!r}" for k, v in key.items())
            samples = ",".join(group.get("sample_run_ids") or [])
            lines.append(
                f"  count={group.get('count', 0)} {key_bits} samples={samples}"
            )
        return "\n".join(lines)
    lines.append(
        f"returned={payload.get('returned', 0)}"
        + (f" limit={payload.get('limit')}" if payload.get("limit") is not None else "")
    )
    for row in payload.get("runs") or []:
        lines.append(
            "  "
            f"{row.get('run_id')} "
            f"tui={row.get('settlement_tui')} "
            f"agent={row.get('agent') or '-'} "
            f"skill={row.get('skill') or '-'} "
            f"reason={row.get('reason') or '-'} "
            f"revalidatable={row.get('revalidatable')} "
            f"exit_0={row.get('exit_0')} "
            f"checkout={row.get('checkout_exists')}"
        )
    return "\n".join(lines)


def render_settlements_inspect_text(payload: Mapping[str, Any]) -> str:
    """Render ``inspect_settlement`` payload as plain human-readable text."""
    enriched = payload.get("enriched") or {}
    ledger = payload.get("ledger") or {}
    lines = [
        f"settlements inspect {payload.get('run_id')}",
        (
            f"ledger: tui={ledger.get('settlement_tui')} "
            f"revision={ledger.get('settlement_revision')} "
            f"type={ledger.get('record_type')}"
        ),
        f"snapshot_source: {payload.get('snapshot_source') or 'missing'}",
        (
            f"agent={enriched.get('agent') or '-'} "
            f"skill={enriched.get('skill') or '-'} "
            f"state={enriched.get('state') or '-'} "
            f"verdict={enriched.get('verdict') or '-'} "
            f"reason={enriched.get('reason') or '-'}"
        ),
        (
            f"root={enriched.get('root') or '-'} "
            f"checkout_exists={enriched.get('checkout_exists')} "
            f"exit_code={enriched.get('exit_code')} "
            f"launcher_pid={enriched.get('launcher_pid')}"
        ),
        (
            f"revalidatable={enriched.get('revalidatable')} "
            f"report_on_disk={enriched.get('report_on_disk')} "
            f"transcript_on_disk={enriched.get('transcript_on_disk')} "
            f"trust_receipt={enriched.get('trust_receipt_present')} "
            f"native_resume={enriched.get('native_resume_candidate')}"
        ),
        f"report_path: {enriched.get('report_path') or '-'}",
        f"transcript_path: {enriched.get('transcript_path') or '-'}",
    ]
    if enriched.get("last_error"):
        lines.append(f"last_error: {enriched.get('last_error')}")
    return "\n".join(lines)


__all__ = [
    "SETTLEMENTS_QUERY_SCHEMA",
    "SettlementsQueryError",
    "inspect_settlement",
    "list_settlements",
    "render_settlements_inspect_text",
    "render_settlements_list_text",
    "render_settlements_summary_text",
    "settlements_summary",
]
