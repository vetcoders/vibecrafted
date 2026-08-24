"""CLI entrypoint for ``vibecrafted dispatch``: validate, dry-run, or execute a plan."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vibecrafted_core.workflow import reserve_run_id

from .doctor import diagnose_file
from .model import STATE_VERIFIED, Dispatch
from .receipts import ReceiptContractError
from .schema import render_cell_prompt
from .supervisor import DispatchResult, cleanup_settled_run, run_dispatch
from .worktrees import canonical_artifact_root


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``vibecrafted dispatch`` subcommand."""
    parser = argparse.ArgumentParser(
        prog="vibecrafted dispatch",
        description="Run or validate a vibecrafted.dispatch.v1 TOML plan.",
    )
    parser.add_argument("dispatch_file", help="Path to a .dispatch.toml file")
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="validate only; exits non-zero when the dispatch is unsafe",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and render every prompt without launching workers",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print dispatch-result.json payload to stdout",
    )
    parser.add_argument(
        "--resume",
        metavar="RUN_ID",
        help="continue from the first non-verified cut recorded in the tracker",
    )
    parser.add_argument(
        "--cleanup-settled",
        metavar="RUN_ID",
        help="remove only settled per-cut worktrees/targets; retain branches and evidence",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse argv and run doctor / dry-run / full dispatch; return the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    source = Path(args.dispatch_file).expanduser()

    report = diagnose_file(source)
    if args.doctor and not args.dry_run:
        return _print_doctor(report, json_output=args.json)
    if not report.ok:
        _print_doctor(report, json_output=args.json)
        return 1
    assert report.dispatch is not None

    dispatch = _with_runtime_baseline(report.dispatch)
    if args.cleanup_settled:
        try:
            outcomes = cleanup_settled_run(dispatch, args.cleanup_settled)
        except ReceiptContractError as exc:
            print(f"cleanup refused: {exc}")
            return 1
        print(
            json.dumps(outcomes, ensure_ascii=False, indent=2)
            if args.json
            else "\n".join(f"{key}: {value}" for key, value in outcomes.items())
        )
        return (
            0
            if all(value not in {"receipt-incomplete"} for value in outcomes.values())
            else 1
        )

    if args.dry_run:
        dry_result = _dry_run(source, dispatch, run_id=args.resume)
        if args.json:
            print(json.dumps(dry_result, ensure_ascii=False, indent=2))
        else:
            print(f"dispatch dry-run: rendered {len(dispatch.cuts)} prompt(s)")
            print(f"dry_run_dir: {dry_result['artifacts']['dry_run_dir']}")
        return 0

    run_id = args.resume or reserve_run_id("dispatch")
    artifacts_dir = _artifacts_dir(dispatch, run_id=run_id)
    _copy_validated_source(source, artifacts_dir)
    # The supervisor is silent on stdout until the whole DAG settles; its live
    # surface is tracker.md/journal.md, rewritten from second zero. Say so
    # BEFORE launching, or every observer concludes the launch hung
    # (measured: an agent waited on a mute handshake, 2026-08-24).
    tracker_path = (
        Path(dispatch.meta.tracker).expanduser()
        if dispatch.meta.tracker
        else artifacts_dir / "tracker.md"
    )
    if not args.json:
        print(
            f"dispatch launched: run_id={run_id}\n"
            f"live state: tracker={tracker_path}\n"
            f"live journal: {artifacts_dir / 'journal.md'}\n"
            "stdout stays silent until the run settles — watch the tracker, "
            "not this stream.",
            flush=True,
        )
    try:
        dispatch_result = run_dispatch(
            dispatch,
            artifacts_dir=artifacts_dir,
            run_id=run_id,
            manage_worktrees=True,
            resume=bool(args.resume),
        )
    except ReceiptContractError as exc:
        print(f"dispatch refused: {exc}")
        return 1
    if args.json:
        print(json.dumps(dispatch_result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"dispatch run: {dispatch.meta.name or source.stem}")
        print(f"tracker: {dispatch_result.artifacts.get('tracker', '')}")
        print(f"journal: {dispatch_result.artifacts.get('journal', '')}")
        print(f"result: {dispatch_result.artifacts.get('result', '')}")
        print(
            f"dou_index: {dispatch_result.baton.verified}/{dispatch_result.baton.total}"
        )
    return 0 if _result_ok(dispatch_result) else 1


def _print_doctor(report: Any, *, json_output: bool) -> int:
    """Render a doctor report to stdout (JSON or human-readable) and return its exit code."""
    if json_output:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for error in report.errors:
            print(f"{error.path}: {error.message}")
        for warning in report.warnings:
            print(f"warning: {warning.path}: {warning.message}")
        if report.ok:
            print("dispatch-doctor: ok")
    return 0 if report.ok else 1


def _with_runtime_baseline(dispatch: Dispatch) -> Dispatch:
    """Stamp the dispatch's meta.baseline with the live repo's current branch/HEAD."""
    baseline = dict(dispatch.meta.baseline)
    baseline["branch"] = _git(dispatch.meta.repo, ["branch", "--show-current"])
    baseline["head"] = _git(dispatch.meta.repo, ["rev-parse", "HEAD"])
    baseline["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return replace(dispatch, meta=replace(dispatch.meta, baseline=baseline))


def _dry_run(
    source: Path, dispatch: Dispatch, *, run_id: str | None = None
) -> dict[str, Any]:
    """Render every cut's prompt without launching workers; write a dry-run bundle to disk."""
    dry_run_dir = _dry_run_dir(dispatch)
    prompts_dir = dry_run_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    _copy_validated_source(source, dry_run_dir)
    _write_dry_run_tracker(dispatch, dry_run_dir, run_id=run_id)

    baton = dispatch.empty_baton()
    prompt_paths: dict[str, str] = {}
    for cut in dispatch.cuts:
        prompt = render_cell_prompt(dispatch, cut, baton=baton)
        prompt_path = prompts_dir / f"{cut.id}.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        prompt_paths[cut.id] = str(prompt_path)

    payload = {
        "schema": "vibecrafted.dispatch-dry-run.v1",
        "dry_run": True,
        "run_id": run_id or "",
        "cuts": [cut.id for cut in dispatch.cuts],
        "prompts": prompt_paths,
        "artifacts": {
            "dry_run_dir": str(dry_run_dir),
            "tracker": str(dry_run_dir / "tracker.md"),
            "validated_copy": str(dry_run_dir / "validated-dispatch.toml"),
            "result": str(dry_run_dir / "dispatch-result.json"),
        },
        "baseline": dispatch.meta.baseline,
    }
    (dry_run_dir / "dispatch-result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def _write_dry_run_tracker(
    dispatch: Dispatch, dry_run_dir: Path, *, run_id: str | None = None
) -> None:
    """Write a placeholder tracker.md for a dry-run (all cuts pending, no workers)."""
    tracker = dry_run_dir / "tracker.md"
    baseline = dispatch.meta.baseline
    lines = [
        f"# dispatch dry-run tracker - {dispatch.meta.name or 'unnamed'}",
        "",
        f"- repo: {dispatch.meta.repo}",
        f"- run_id: {run_id or ''}",
        f"- baseline_branch: {baseline.get('branch', '')}",
        f"- baseline_head: {baseline.get('head', '')}",
        f"- validated_copy: {dry_run_dir / 'validated-dispatch.toml'}",
        "- mode: dry-run (no workers launched)",
        "",
        "| Cut | Phase | Agent | State | Scheduler | Supervisor evidence |",
        "|---|---|---|---:|---|---|",
    ]
    for cut in dispatch.cuts:
        lines.append(
            f"| {cut.id} | {cut.phase} | {cut.agent} | [ ] | queued | prompt rendered |"
        )
    tracker.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy_validated_source(source: Path, artifacts_dir: Path) -> Path:
    """Copy the validated TOML source into the artifacts dir for provenance."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    target = artifacts_dir / "validated-dispatch.toml"
    shutil.copyfile(source, target)
    return target


def _dry_run_dir(dispatch: Dispatch) -> Path:
    """Resolve the directory a dry-run's artifacts are written into."""
    return canonical_artifact_root(dispatch.meta.repo) / "plans" / "dry-run"


def _artifacts_dir(dispatch: Dispatch, *, run_id: str = "dispatch") -> Path:
    """Resolve the directory a real dispatch run's artifacts are written into."""
    return canonical_artifact_root(dispatch.meta.repo) / "plans" / "dispatch" / run_id


def _result_ok(result: DispatchResult) -> bool:
    """True only when every cut in the result ended supervisor-verified."""
    return all(state == STATE_VERIFIED for state in result.states.values())


def _git(repo: str, args: list[str]) -> str:
    """Run a git command in ``repo``; return trimmed stdout, or "" on any failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
