"""Reusable read-only usage report projection for Vibecrafted runs.

The projection keeps unknown measurements explicit, preserves cost units, and
derives telemetry for legacy runs without writing the derived record back to
``meta.json``.  CLI, server, and UI adapters should render this contract rather
than rebuilding aggregation rules independently.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from .control_plane import control_plane_home, resolve_run
from .telemetry import run_telemetry_from_meta

USAGE_REPORT_SCHEMA = "vibecrafted.usage-report.v1"
_SINCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class UsageReportQueryError(ValueError):
    """The requested usage-report filter is invalid."""


class ExternalCostAdapter(Protocol):
    """Optional provider adapter that can improve one run's cost truth.

    Adapters receive the already-normalized report row plus the source files.
    They return a cost mapping in the existing telemetry shape, or ``None``
    when they have no evidence for the run. Registration is explicit per
    query: importing this module never scans provider state or performs I/O.
    """

    adapter_id: str

    def cost_for_run(
        self,
        *,
        meta: Mapping[str, Any],
        transcript: Path | None,
        row: Mapping[str, Any],
    ) -> Mapping[str, Any] | None: ...


def parse_usage_window(value: str) -> int | None:
    """Return seconds for ``<N><s|m|h|d|w>``, or ``None`` if invalid."""
    text = str(value or "").strip().lower()
    if len(text) < 2 or text[-1] not in _SINCE_UNITS or not text[:-1].isdigit():
        return None
    return int(text[:-1]) * _SINCE_UNITS[text[-1]]


def _epoch(stamp: object) -> float | None:
    """Return POSIX seconds for an ISO-8601 timestamp, or ``None``."""
    try:
        parsed = dt.datetime.fromisoformat(str(stamp or "").strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def _load_meta(path: Path) -> dict[str, Any]:
    """Load one run's metadata, treating absent or malformed files as empty."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def usage_report_row(meta: dict[str, Any], transcript: Path | None) -> dict[str, Any]:
    """Project one run into the stable usage-report row contract."""
    telemetry = run_telemetry_from_meta(meta, transcript=transcript)
    failure = telemetry.get("failure")
    provider: object = meta.get("provider") or telemetry["agent"]
    if not provider:
        provider = {"kind": "unknown", "reason": "provider not recorded"}
    return {
        "run_id": telemetry["run_id"],
        "provider": provider,
        "agent": telemetry["agent"],
        "model": telemetry["model"],
        "status": telemetry["status"],
        "exit_code": telemetry["exit_code"],
        "tokens": telemetry["usage"],
        "cost": telemetry["cost"],
        "failure_kind": failure.get("kind") if isinstance(failure, dict) else None,
        "failure": failure.get("summary") if isinstance(failure, dict) else None,
        "provider_session_id": telemetry["provider_session_id"],
        "telemetry_source": telemetry["telemetry_source"],
    }


def usage_report_totals(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate known tokens and costs while counting unknown measurements.

    Cost units remain separate, so provider credits can never be added to USD.
    """
    tokens_known = 0
    tokens_unknown = 0
    cost_unknown = 0
    by_unit: dict[str, float] = {}
    for row in rows:
        total = row["tokens"].get("tokens_total")
        if isinstance(total, int) and not isinstance(total, bool):
            tokens_known += total
        else:
            tokens_unknown += 1
        amount = row["cost"].get("amount")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool):
            unit = str(row["cost"].get("unit") or row["cost"].get("currency") or "USD")
            by_unit[unit] = round(by_unit.get(unit, 0.0) + float(amount), 6)
        else:
            cost_unknown += 1
    return {
        "runs": len(rows),
        "tokens_total_known": tokens_known,
        "runs_tokens_unknown": tokens_unknown,
        "cost_by_unit": dict(sorted(by_unit.items())),
        "runs_cost_unknown": cost_unknown,
    }


def _apply_external_cost_adapters(
    row: dict[str, Any],
    *,
    meta: Mapping[str, Any],
    transcript: Path | None,
    adapters: Sequence[ExternalCostAdapter],
) -> dict[str, Any]:
    """Apply the first evidence-bearing adapter unless actual cost is stored."""
    source = str(row["cost"].get("source") or "unknown")
    if source == "provider_reported":
        return row
    for adapter in adapters:
        candidate = adapter.cost_for_run(meta=meta, transcript=transcript, row=row)
        if candidate is None:
            continue
        amount = candidate.get("amount")
        unit = candidate.get("unit") or candidate.get("currency")
        candidate_source = str(candidate.get("source") or "").strip()
        if (
            not isinstance(amount, (int, float))
            or isinstance(amount, bool)
            or not unit
            or not candidate_source
        ):
            raise ValueError(
                f"external cost adapter {adapter.adapter_id!r} returned "
                "an invalid cost record"
            )
        projected = dict(row)
        projected["cost"] = dict(candidate)
        return projected
    return row


def build_usage_report(
    *,
    run_ids: Sequence[str] = (),
    since: str = "",
    now: float | None = None,
    runtime_runs_dir: Path | None = None,
    run_resolver: Callable[[str], Any] = resolve_run,
    cost_adapters: Sequence[ExternalCostAdapter] = (),
) -> dict[str, Any]:
    """Build the stable read-only usage report for explicit runs or a window.

    ``runtime_runs_dir``, ``now`` and ``run_resolver`` are injectable so other
    adapters and tests can consume the projection without mutating global state.
    ``RunNotResolved`` is intentionally allowed through for explicit IDs; the
    CLI converts it into its established exit contract.
    """
    selected = sorted(
        {str(run_id).strip() for run_id in run_ids if str(run_id).strip()}
    )
    requested_since = str(since or "").strip()
    if selected and requested_since:
        raise UsageReportQueryError("pass --run-id or --since, not both")

    effective_since = "" if selected else (requested_since or "24h")
    window = parse_usage_window(effective_since) if effective_since else None
    if effective_since and window is None:
        raise UsageReportQueryError(
            f"--since must look like 30m, 24h or 7d (got {effective_since!r})"
        )

    rows: list[dict[str, Any]] = []
    if selected:
        for run_id in selected:
            resolved = run_resolver(run_id)
            meta = _load_meta(resolved.meta) if resolved.meta is not None else {}
            meta.setdefault("run_id", run_id)
            row = usage_report_row(meta, resolved.transcript)
            rows.append(
                _apply_external_cost_adapters(
                    row,
                    meta=meta,
                    transcript=resolved.transcript,
                    adapters=cost_adapters,
                )
            )
    else:
        root = runtime_runs_dir or (control_plane_home() / "runtime_runs")
        cutoff = (time.time() if now is None else float(now)) - float(window or 0)
        for meta_path in sorted(root.glob("*/meta.json")):
            meta = _load_meta(meta_path)
            stamp = _epoch(meta.get("completed_at") or meta.get("updated_at"))
            if stamp is None or stamp < cutoff:
                continue
            meta.setdefault("run_id", meta_path.parent.name)
            transcript = meta_path.parent / "transcript.log"
            transcript_path = transcript if transcript.is_file() else None
            row = usage_report_row(meta, transcript_path)
            rows.append(
                _apply_external_cost_adapters(
                    row,
                    meta=meta,
                    transcript=transcript_path,
                    adapters=cost_adapters,
                )
            )

    rows.sort(key=lambda row: str(row["run_id"]))
    return {
        "schema": USAGE_REPORT_SCHEMA,
        "filter": {"run_ids": selected} if selected else {"since": effective_since},
        "runs": rows,
        "totals": usage_report_totals(rows),
    }


__all__ = [
    "USAGE_REPORT_SCHEMA",
    "ExternalCostAdapter",
    "UsageReportQueryError",
    "build_usage_report",
    "parse_usage_window",
    "usage_report_row",
    "usage_report_totals",
]
