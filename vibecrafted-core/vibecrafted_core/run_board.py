"""`vibecrafted status` — today's runs as the control plane sees them.

One full board sync (``control_plane.sync_state``) reconciles dead workers
against their reports, then the recent runs are printed newest first with the
one path a human wants next: the report. No server, cockpit, or dashboard is
required — this is the stranger's dashboard.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Sequence
from typing import Any

from .control_plane import ControlPlaneStorageError, sync_state

_STATE_GLYPH = {
    "completed": "ok",
    "active": "run",
    "running": "run",
    "launching": "run",
    "paused": "pause",
    "stalled": "stall",
    "failed": "fail",
    "stopped": "stop",
    "gc": "gc",
    "blocked": "block",
    "report_missing": "fail",
    "report_invalid": "fail",
}


def _parse_iso(raw: Any) -> dt.datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _local_clock(raw: Any) -> str:
    parsed = _parse_iso(raw)
    if parsed is None:
        return "--:--"
    return parsed.astimezone().strftime("%H:%M")


def _is_today(run: dict[str, Any], today: dt.date) -> bool:
    for key in ("started_at", "updated_at", "completed_at"):
        parsed = _parse_iso(run.get(key))
        if parsed is not None and parsed.astimezone().date() == today:
            return True
    return False


def _sort_key(run: dict[str, Any]) -> str:
    return str(run.get("started_at") or run.get("updated_at") or "")


def collect_board(*, all_days: bool, limit: int) -> dict[str, Any]:
    """Sync the board and return the runs to show plus the counts."""
    board = sync_state()
    seen: set[str] = set()
    runs: list[dict[str, Any]] = []
    for bucket in ("active_runs", "stalled_runs", "recent_runs"):
        for run in board.get(bucket) or []:
            run_id = str(run.get("run_id") or "")
            if not run_id or run_id in seen:
                continue
            seen.add(run_id)
            runs.append(dict(run))
    today = dt.datetime.now(tz=dt.timezone.utc).astimezone().date()
    if not all_days:
        runs = [run for run in runs if _is_today(run, today)]
    runs.sort(key=_sort_key, reverse=True)
    return {
        "runs": runs[:limit] if limit > 0 else runs,
        "hidden": max(0, len(runs) - limit) if limit > 0 else 0,
        "settlement_counts": dict(board.get("settlement_counts") or {}),
        "warnings": list(board.get("warnings") or []),
    }


def render_board(result: dict[str, Any], *, all_days: bool) -> str:
    runs = result["runs"]
    lines: list[str] = []
    scope = (
        "all days"
        if all_days
        else dt.datetime.now(tz=dt.timezone.utc).astimezone().date().isoformat()
    )
    lines.append(f"Runs — {scope}")
    if not runs:
        lines.append(
            "  No runs yet. Start one: "
            'vibecrafted implement claude --prompt "describe this repo"'
        )
        lines.append("  Then: vibecrafted await claude --last")
        return "\n".join(lines)
    for run in runs:
        state = str(run.get("state") or "unknown")
        glyph = _STATE_GLYPH.get(state, state)
        liveness = str(run.get("liveness") or "")
        when = _local_clock(run.get("started_at") or run.get("updated_at"))
        skill = str(run.get("skill") or run.get("mode") or "?")
        agent = str(run.get("agent") or "?")
        run_id = str(run.get("run_id") or "?")
        live = (
            " (worker alive)"
            if liveness == "pid_alive"
            and state
            in {
                "active",
                "running",
                "launching",
            }
            else ""
        )
        lines.append(f"  [{glyph:<5}] {when}  {skill:<10} {agent:<7} {run_id}{live}")
        report = str(run.get("latest_report") or "").strip()
        if report:
            lines.append(f"          report: {report}")
        error = str(run.get("last_error") or "").strip()
        if error and state not in {"completed"}:
            first = error.split(";")[0].strip()
            lines.append(f"          note:   {first}")
        if state in {"active", "running", "launching"}:
            lines.append(
                f"          watch:  vibecrafted await {agent} --run-id {run_id}"
            )
    hidden = int(result.get("hidden") or 0)
    if hidden:
        lines.append(f"  … {hidden} more — vibecrafted status --all --limit 0")
    counts = result.get("settlement_counts") or {}
    if counts:
        lines.append(
            "  settled: finalized={f} failed={x} needs-attention={n}".format(
                f=counts.get("f", 0), x=counts.get("x", 0), n=counts.get("n", 0)
            )
        )
    for warning in result.get("warnings") or []:
        lines.append(f"  warning: {warning}")
    return "\n".join(lines)


def status_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vibecrafted status",
        description="Show today's runs (a run = one dispatched agent job with a report and transcript).",
    )
    parser.add_argument(
        "--all", action="store_true", help="every retained run, not only today"
    )
    parser.add_argument(
        "--limit", type=int, default=12, help="rows to show (0 = no limit)"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = collect_board(all_days=bool(args.all), limit=int(args.limit))
    except ControlPlaneStorageError as exc:
        print(f"status: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    print(render_board(result, all_days=bool(args.all)))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(status_main())
