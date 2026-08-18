"""Traced operator verbs (approve/interrupt/force-audit/...) over lifecycle run state."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .control_plane import await_run as control_plane_await_run
from .control_plane import control_plane_home
from .lifecycle_runner import (
    LifecycleRunSpec,
    LifecycleSupervisor,
    run_lifecycle,
    write_lifecycle_report,
    write_lifecycle_state,
)
from .stage_cast import primary_stage_agent

RunLifecycle = Callable[[LifecycleRunSpec], dict[str, Any]]
StopRun = Callable[..., dict[str, Any]]

# Operator verbs shared by every lifecycle CLI (vc-ship, vc-dou, vc-audit, ...).
# They must never collide with agent names or the marbles deck control verbs
# (pause/stop/resume/session/inspect/delete/gc).
CONTROL_VERBS = frozenset(
    {
        "runs",
        "status",
        "await",
        "approve",
        "interrupt",
        "force-audit",
        "accept-dou",
        "fallback",
    }
)


def lifecycle_runs_home() -> Path:
    """Directory under the control-plane home where lifecycle run state.json files live."""
    return control_plane_home() / "lifecycle_runs"


def _now_iso() -> str:
    """Current local time as an ISO-8601 string with numeric UTC offset."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _load_state(state_path: Path) -> dict[str, Any]:
    """Parse a lifecycle run's state.json from disk."""
    return json.loads(state_path.read_text(encoding="utf-8"))


def resolve_lifecycle_state_path(run_id: str = "", *, workflow_id: str = "") -> Path:
    """Resolve a lifecycle run id (or ``latest``/empty) to its state.json path."""
    home = lifecycle_runs_home()
    target = str(run_id or "").strip()
    if target and target != "latest":
        path = home / target / "state.json"
        if not path.is_file():
            raise ValueError(f"unknown lifecycle run: {target}")
        return path
    candidates = sorted(
        home.glob("*/state.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        if not workflow_id:
            return path
        try:
            state = _load_state(path)
        except (OSError, json.JSONDecodeError):
            continue
        if str(state.get("workflow") or "") == workflow_id:
            return path
    scope = f" for workflow {workflow_id}" if workflow_id else ""
    raise ValueError(f"no lifecycle runs found{scope} under {home}")


def list_lifecycle_runs(
    *, workflow_id: str = "", limit: int = 0
) -> list[dict[str, Any]]:
    """List lifecycle runs newest-first, optionally filtered by workflow and capped."""
    supervisor = LifecycleSupervisor()
    summaries: list[dict[str, Any]] = []
    home = lifecycle_runs_home()
    candidates = sorted(
        home.glob("*/state.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            state = _load_state(path)
        except (OSError, json.JSONDecodeError):
            continue
        if workflow_id and str(state.get("workflow") or "") != workflow_id:
            continue
        summaries.append(supervisor.status(state))
        if limit and len(summaries) >= limit:
            break
    return summaries


def _require_control(state: dict[str, Any], action: str) -> None:
    """Raise ValueError unless ``action`` is one of the run's declared human_controls."""
    allowed = [str(item) for item in (state.get("human_controls") or [])]
    if action not in allowed:
        raise ValueError(
            f"operator action '{action}' is not part of this run's human "
            f"controls: {', '.join(allowed) or 'none'}"
        )


def record_operator_action(
    state_path: Path,
    state: dict[str, Any],
    action: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    """Persist a traced operator move: state.json, report.md, transcript line."""
    entry = {"action": action, "at": _now_iso(), "details": details}
    state.setdefault("operator_actions", []).append(entry)
    write_lifecycle_state(state_path, state)
    report_path = str(state.get("report_path") or "")
    if report_path:
        write_lifecycle_report(Path(report_path), state)
    transcript_path = str(state.get("transcript_path") or "")
    if transcript_path:
        try:
            with Path(transcript_path).open(
                "a", encoding="utf-8", errors="replace"
            ) as handle:
                handle.write(
                    json.dumps({"kind": "operator_action", **entry}, ensure_ascii=False)
                    + "\n"
                )
        except OSError:
            pass
    return entry


def _baton_previous_reports(state: dict[str, Any]) -> tuple[str, ...]:
    """Non-empty previous-report paths carried in the run's current baton."""
    baton = dict(state.get("baton") or {})
    return tuple(
        str(path).strip()
        for path in (baton.get("previous_reports") or [])
        if str(path).strip()
    )


def _missing_previous_reports(state: dict[str, Any]) -> list[str]:
    """Baton cargo validity: report paths that do not exist or are empty.

    In the primary no-await mode the baton records the launched stage's
    report path while the worker is still writing it — approving before the
    file lands would prompt the continuation with evidence that isn't there.
    """
    missing: list[str] = []
    for path in _baton_previous_reports(state):
        try:
            target = Path(path).expanduser()
            if not target.is_file() or target.stat().st_size == 0:
                missing.append(path)
        except OSError:
            missing.append(path)
    return missing


def _continuation_spec(
    state: dict[str, Any],
    *,
    workflow_id: str,
    start_stage: str,
    agent: str,
) -> LifecycleRunSpec:
    """Build the LifecycleRunSpec for a parent-linked continuation run from saved state."""
    spec_data = dict(state.get("spec") or {})
    return LifecycleRunSpec(
        workflow_id=workflow_id,
        agent=agent,
        prompt=str(spec_data.get("prompt") or ""),
        file=str(spec_data.get("file") or ""),
        root=str(state.get("root") or ""),
        runtime=str(spec_data.get("runtime") or "headless"),
        await_stages=bool(spec_data.get("await_stages")),
        start_stage=start_stage,
        count=spec_data.get("count"),
        depth=spec_data.get("depth"),
        parent_run_id=str(state.get("run_id") or ""),
        previous_reports=_baton_previous_reports(state),
        stage_agents=dict(spec_data.get("stage_agents") or {}),
        stage_models=dict(spec_data.get("stage_models") or {}),
    )


def _baton_agent(state: dict[str, Any], stage: str = "") -> str:
    """Resolve the agent for ``stage``: mission-declared casting wins over baton default."""
    baton = dict(state.get("baton") or {})
    spec_data = dict(state.get("spec") or {})
    # Operator-declared casting (mission frontmatter stage_agents) wins for a
    # stage it names; worker-requested next_agent steers only the un-cast rest.
    stage_agents = dict(spec_data.get("stage_agents") or {})
    cast = primary_stage_agent(stage_agents, stage)
    if cast:
        return cast
    return str(baton.get("next_agent") or spec_data.get("agent") or "codex")


def await_stage(
    state: dict[str, Any],
    *,
    idle_seconds: float = 300,
    interval_seconds: float = 5,
    hard_cap_seconds: float | None = None,
) -> dict[str, Any]:
    """The lifecycle-level observability contract: block on the CURRENT stage.

    Wraps ``control_plane.await_run`` (liveness-aware idle window that resets
    on real movement, optional hard cap) so supervisors — human or agent —
    wait through the runtime contract instead of ad-hoc sleep/poll loops, and
    get back the one truth that decides the next verb: the stage delivered
    its report, died without one, or genuinely stalled.
    """
    stages = list(state.get("stages") or [])
    if not stages:
        raise ValueError("nothing to await: no stage launched yet")
    last_stage = stages[-1]
    launch = dict(last_stage.get("launch") or {})
    stage_run_id = str(launch.get("run_id") or "").strip()
    if not stage_run_id:
        raise ValueError("nothing to await: current stage has no worker run")
    report_path = str(launch.get("report") or "").strip()

    # The stage report IS the no-await handoff: with report_path forwarded,
    # await_run returns `report_delivered` on its first poll instead of idling
    # out a full window on a worker that already delivered and exited.
    result = control_plane_await_run(
        stage_run_id,
        timeout_seconds=idle_seconds,
        interval_seconds=interval_seconds,
        hard_cap_seconds=hard_cap_seconds,
        report_path=report_path or None,
    )

    report_written = False
    if report_path:
        try:
            report_written = Path(report_path).stat().st_size > 0
        except OSError:
            report_written = False
    settled = bool(result.get("completed")) or bool(result.get("timed_out"))
    worker_alive = bool(result.get("worker_alive"))
    baton = dict(state.get("baton") or {})
    return {
        "run_id": str(state.get("run_id") or ""),
        "stage": str(last_stage.get("id") or ""),
        "stage_run_id": stage_run_id,
        "report": report_path,
        "report_written": report_written,
        "completed": bool(result.get("completed")),
        "timed_out": bool(result.get("timed_out")),
        "reason": str(result.get("reason") or ""),
        "worker_alive": worker_alive,
        "worker_dead_without_report": settled
        and not worker_alive
        and not report_written,
        "next_stage": str(baton.get("next_stage") or ""),
        "next_agent": str(baton.get("next_agent") or ""),
    }


def approve_transition(
    state_path: Path,
    state: dict[str, Any],
    *,
    run_lifecycle_fn: RunLifecycle | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Launch the baton's pending next_stage as a parent-linked continuation run."""
    _require_control(state, "approve_transition")
    baton = dict(state.get("baton") or {})
    next_stage = str(baton.get("next_stage") or "").strip()
    if not next_stage:
        raise ValueError(
            "nothing to approve: the baton has no pending next_stage "
            "(run is complete or was never handed off)"
        )
    missing = _missing_previous_reports(state)
    if missing and not force:
        raise ValueError(
            "baton cargo not ready — report(s) missing or empty: "
            + ", ".join(missing)
            + "; wait for the stage worker to finish writing, or approve "
            "--force to continue without the evidence trail"
        )
    launcher = run_lifecycle_fn or run_lifecycle
    spec = _continuation_spec(
        state,
        workflow_id=str(state.get("workflow") or ""),
        start_stage=next_stage,
        agent=_baton_agent(state, next_stage),
    )
    child = launcher(spec)
    details: dict[str, Any] = {
        "approved_stage": next_stage,
        "agent": spec.agent,
        "continuation_run_id": str(child.get("run_id") or ""),
    }
    if force and missing:
        # A forced approve over missing cargo must leave a trace of what
        # evidence the continuation ran without.
        details["forced_missing_reports"] = missing
    record_operator_action(state_path, state, "approve_transition", details)
    return child


def interrupt_workflow(
    state_path: Path,
    state: dict[str, Any],
    *,
    stop_run_fn: StopRun | None = None,
) -> dict[str, Any]:
    """Stop the live stage's worker (if any), mark the run interrupted, and record it."""
    _require_control(state, "interrupt_workflow")
    stages = list(state.get("stages") or [])
    last_stage = stages[-1] if stages else {}
    stage_run_id = str((last_stage.get("launch") or {}).get("run_id") or "")
    stop_result: dict[str, Any] = {}
    if stage_run_id:
        stop: StopRun
        if stop_run_fn is None:
            from .workflow import stop_run as _default_stop_run

            stop = _default_stop_run
        else:
            stop = stop_run_fn
        stop_result = stop(stage_run_id, reason="lifecycle operator interrupt")
    state["status"] = "interrupted"
    entry = record_operator_action(
        state_path,
        state,
        "interrupt_workflow",
        {
            "stage_run_id": stage_run_id,
            "stop_accepted": bool(stop_result.get("accepted")),
            "stop_reason": str(stop_result.get("reason") or ""),
        },
    )
    return {"status": "interrupted", "stage_run_id": stage_run_id, "action": entry}


def _manifest_stages(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The run's cached workflow manifest stage list."""
    manifest = dict(state.get("manifest") or {})
    return [dict(stage) for stage in manifest.get("stages") or []]


def force_audit(
    state_path: Path,
    state: dict[str, Any],
    *,
    run_lifecycle_fn: RunLifecycle | None = None,
) -> dict[str, Any]:
    """Make an audit the next thing that happens.

    Manifests that carry an audit stage get their baton re-steered to it
    (the umbrella walks there on the next approve). Single-stage manifests
    without one get a parent-linked standalone vc-audit lifecycle run —
    the audit agent has its own lifecycle.
    """
    _require_control(state, "force_audit")
    audit_stage = ""
    for stage in _manifest_stages(state):
        if str(stage.get("workflow") or "") == "audit" or stage.get("id") == "audit":
            audit_stage = str(stage.get("id") or "")
            break
    if audit_stage:
        baton = state.setdefault("baton", {})
        displaced = str(baton.get("next_stage") or "")
        baton["next_stage"] = audit_stage
        baton["reason"] = "operator_forced_audit"
        entry = record_operator_action(
            state_path,
            state,
            "force_audit",
            {
                "mode": "steered_baton",
                "next_stage": audit_stage,
                "displaced_next_stage": displaced,
            },
        )
        return {"mode": "steered_baton", "next_stage": audit_stage, "action": entry}
    launcher = run_lifecycle_fn or run_lifecycle
    spec = _continuation_spec(
        state,
        workflow_id="vc-audit",
        start_stage="",
        agent=_baton_agent(state, "audit"),
    )
    child = launcher(spec)
    entry = record_operator_action(
        state_path,
        state,
        "force_audit",
        {
            "mode": "dispatched_vc_audit",
            "continuation_run_id": str(child.get("run_id") or ""),
            "agent": spec.agent,
        },
    )
    return {"mode": "dispatched_vc_audit", "run": child, "action": entry}


def accept_dou(state_path: Path, state: dict[str, Any], finding: str) -> dict[str, Any]:
    """Record an operator's conscious acceptance of one open DoU finding."""
    _require_control(state, "accept_dou")
    text = str(finding or "").strip()
    if not text:
        raise ValueError("--finding is required to accept a DoU gap consciously")
    state.setdefault("accepted_dou_findings", []).append(
        {"finding": text, "at": _now_iso()}
    )
    return record_operator_action(state_path, state, "accept_dou", {"finding": text})


def choose_fallback_stage(
    state_path: Path, state: dict[str, Any], stage_id: str
) -> dict[str, Any]:
    """Steer the baton's next_stage to an operator-chosen (manifest-known) stage id."""
    _require_control(state, "choose_fallback_stage")
    target = str(stage_id or "").strip()
    known = [str(stage.get("id") or "") for stage in _manifest_stages(state)]
    if target not in known:
        raise ValueError(
            f"unknown stage '{target}' for workflow {state.get('workflow')}; "
            f"stages: {', '.join(known) or 'none'}"
        )
    baton = state.setdefault("baton", {})
    displaced = str(baton.get("next_stage") or "")
    baton["next_stage"] = target
    baton["reason"] = "operator_chose_fallback"
    return record_operator_action(
        state_path,
        state,
        "choose_fallback_stage",
        {"next_stage": target, "displaced_next_stage": displaced},
    )


def _print_payload(payload: Any, *, as_json: bool) -> None:
    """Print a control-verb result either as pretty JSON or as sorted key: value lines."""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if isinstance(payload, list):
        for item in payload:
            _print_payload(item, as_json=False)
            print()
        return
    if isinstance(payload, dict):
        for key in sorted(payload):
            print(f"{key}: {payload[key]}")
        return
    print(payload)


def lifecycle_control_main(
    argv: Sequence[str] | None = None, *, workflow_id: str = ""
) -> int:
    """CLI entrypoint for the lifecycle control-verb subcommands (runs/status/await/...)."""
    parser = argparse.ArgumentParser(
        prog=f"{workflow_id or 'vc-lifecycle'} <control>",
        description=(
            "Traced operator moves over lifecycle run state "
            "(human_controls made runtime-real)."
        ),
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    runs_parser = sub.add_parser("runs", help="list lifecycle runs (newest first)")
    runs_parser.add_argument("--all", action="store_true", dest="all_workflows")
    runs_parser.add_argument("--limit", type=int, default=10)
    runs_parser.add_argument("--json", action="store_true")

    def _run_parser(verb: str, help_text: str) -> argparse.ArgumentParser:
        """Register a run_id-taking subcommand shared by most control verbs."""
        verb_parser = sub.add_parser(verb, help=help_text)
        verb_parser.add_argument("run_id", nargs="?", default="")
        verb_parser.add_argument("--json", action="store_true")
        return verb_parser

    _run_parser("status", "show one lifecycle run's status")
    await_parser = _run_parser(
        "await", "await_stage: block on the current stage via the runtime contract"
    )
    await_parser.add_argument(
        "--idle",
        type=float,
        default=300,
        help="idle window in seconds; resets on real movement (default 300)",
    )
    await_parser.add_argument("--interval", type=float, default=5)
    await_parser.add_argument(
        "--hard-cap",
        type=float,
        default=None,
        help="absolute ceiling in seconds (default: none, liveness governs)",
    )
    approve_parser = _run_parser(
        "approve", "approve_transition: launch the baton's next_stage"
    )
    approve_parser.add_argument(
        "--force",
        action="store_true",
        help="continue even when baton report files are missing or empty",
    )
    _run_parser("interrupt", "interrupt_workflow: stop the live stage, mark run")
    _run_parser("force-audit", "force_audit: make an audit the next move")
    accept_parser = _run_parser("accept-dou", "accept_dou: accept a DoU gap")
    accept_parser.add_argument("--finding", required=True)
    fallback_parser = _run_parser(
        "fallback", "choose_fallback_stage: steer the baton to an earlier stage"
    )
    fallback_parser.add_argument("--stage", required=True)

    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    as_json = bool(getattr(args, "json", False))
    try:
        if args.verb == "runs":
            scope = "" if args.all_workflows else workflow_id
            payload: Any = list_lifecycle_runs(
                workflow_id=scope, limit=max(args.limit, 0)
            )
            _print_payload(payload, as_json=as_json)
            return 0
        state_path = resolve_lifecycle_state_path(
            args.run_id, workflow_id="" if args.run_id else workflow_id
        )
        state = _load_state(state_path)
        if args.verb == "status":
            payload = LifecycleSupervisor().status(state)
            payload["parent_run_id"] = str(state.get("parent_run_id") or "")
            payload["operator_actions"] = len(state.get("operator_actions") or [])
        elif args.verb == "await":
            payload = await_stage(
                state,
                idle_seconds=args.idle,
                interval_seconds=args.interval,
                hard_cap_seconds=args.hard_cap,
            )
        elif args.verb == "approve":
            payload = approve_transition(
                state_path, state, force=bool(getattr(args, "force", False))
            )
        elif args.verb == "interrupt":
            payload = interrupt_workflow(state_path, state)
        elif args.verb == "force-audit":
            payload = force_audit(state_path, state)
        elif args.verb == "accept-dou":
            payload = accept_dou(state_path, state, args.finding)
        else:
            payload = choose_fallback_stage(state_path, state, args.stage)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_payload(payload, as_json=as_json)
    return 0
