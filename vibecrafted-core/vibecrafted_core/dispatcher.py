"""CLI over the async lifecycle supervisor: spawn, await, and validate one worker run."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .run_signal import RunSignalServer
from .supervisor_async import AsyncSupervisor, transcript_human_path


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for ``python -m vibecrafted_core.dispatcher run``."""
    parser = argparse.ArgumentParser(
        prog="python -m vibecrafted_core.dispatcher",
        description="Run one command under the Vibecrafted async lifecycle supervisor.",
    )
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="spawn, observe, validate, and close one run")
    run.add_argument("--run-id", required=True)
    run.add_argument("--root", default=".")
    run.add_argument("--meta")
    run.add_argument("--report")
    run.add_argument("--transcript")
    run.add_argument(
        "--prompt-file",
        help="deliver this prompt file to the worker on stdin and VIBECRAFTED_PROMPT_PATH",
    )
    run.add_argument(
        "--lifecycle-state",
        default="",
        help=(
            "lifecycle state.json this run belongs to; on worker failure the "
            "dispatcher records the terminal truth there (push-side "
            "report-on-death)"
        ),
    )
    run.add_argument(
        "--timeout",
        type=float,
        help="wall-clock cap; unset means a productive worker may run as long as it talks",
    )
    run.add_argument(
        "--silence-timeout",
        type=float,
        help=(
            "seconds of unbroken worker silence that mark the run STALLED; "
            "unset uses the supervisor default (VIBECRAFTED_SILENCE_TIMEOUT_SECONDS), "
            "0 disables the bound"
        ),
    )
    run.add_argument(
        "--no-require-report",
        action="store_true",
        help="do not fail the artifact contract when the report file is absent",
    )
    run.add_argument(
        "--require-transcript-output",
        action="store_true",
        help="require at least one byte of captured stdout/stderr",
    )
    run.add_argument(
        "--salvage-report-from-stream",
        action="store_true",
        help=(
            "for verified native resume adapters, salvage a successful "
            "provider stream into the canonical report"
        ),
    )
    run.add_argument(
        "--tee-output",
        action="store_true",
        help="also write worker stdout/stderr to this terminal while capturing transcript",
    )
    run.add_argument(
        "--json",
        action="store_true",
        help="print the final lifecycle summary as JSON",
    )
    run.add_argument(
        "--quiet",
        action="store_true",
        help="do not print the final lifecycle summary",
    )
    run.add_argument("worker", nargs=argparse.REMAINDER)
    return parser


def _normalize_worker(argv: Sequence[str]) -> list[str]:
    """Strip a leading ``--`` separator from the worker argv; require a non-empty command."""
    worker = list(argv)
    if worker and worker[0] == "--":
        worker = worker[1:]
    if not worker:
        raise ValueError("missing worker command after --")
    return worker


def _maybe_record_lifecycle_worker_exit(
    state_path: str, summary: dict[str, Any]
) -> None:
    """Push terminal truth into the lifecycle that launched this worker.

    Failures retain the report-on-death alarm. Healthy default-mode exits are
    reconciled through the lifecycle proof/seal/settlement path here because
    no synchronous ``LifecycleRunner`` await exists to do it later.
    """
    from .lifecycle_runner import record_stage_worker_completion

    summary["lifecycle_reconciled"] = record_stage_worker_completion(
        state_path,
        str(summary.get("run_id") or ""),
        summary,
    )


async def _run(args: argparse.Namespace) -> int:
    """Run one worker under ``AsyncSupervisor``, then triage/summarize/report the outcome."""
    worker = _normalize_worker(args.worker)
    signal_server = RunSignalServer(args.run_id).start()
    try:
        handle = await AsyncSupervisor(
            signal_sink=lambda _run_id, state: signal_server.heartbeat(state)
        ).run(
            run_id=args.run_id,
            command=worker,
            root=args.root,
            meta_path=args.meta,
            report_path=args.report,
            transcript_path=args.transcript,
            prompt_file_path=args.prompt_file,
            timeout=args.timeout,
            silence_timeout=args.silence_timeout,
            require_report=not args.no_require_report,
            require_transcript_output=args.require_transcript_output,
            tee_output=args.tee_output,
            salvage_report_from_stream=args.salvage_report_from_stream,
        )
        validation = handle.artifact_validation
        artifact_errors = list(validation.errors if validation is not None else ())
        summary = {
            "run_id": handle.run_id,
            "state": handle.state.value,
            "states": [state.value for state in handle.states],
            "exit_code": handle.exit_code,
            "root": str(handle.root),
            "report": str(handle.report_path or ""),
            "transcript": str(handle.transcript_path or ""),
            "artifact_ok": bool(validation.ok if validation is not None else False),
            "artifact_errors": artifact_errors,
        }
        human_transcript = transcript_human_path(handle.transcript_path)
        if human_transcript is not None and human_transcript.exists():
            summary["transcript_human"] = str(human_transcript)

        settlement = ""
        if args.meta:
            try:
                meta_body = json.loads(Path(args.meta).read_text(encoding="utf-8"))
                raw_settlement = meta_body.get("settlement")
                settlement = str(
                    meta_body.get("settlement_verdict")
                    or (
                        raw_settlement.get("verdict")
                        if isinstance(raw_settlement, dict)
                        else raw_settlement
                    )
                    or ""
                )
            except (OSError, json.JSONDecodeError):
                pass
        signal_server.terminal(
            state=str(summary["state"]),
            settlement=settlement,
            report=str(summary.get("report") or ""),
            exit_code=handle.exit_code,
        )

        lifecycle_state = str(getattr(args, "lifecycle_state", "") or "")
        if lifecycle_state:
            _maybe_record_lifecycle_worker_exit(lifecycle_state, summary)

        # Shell spawners call spawn_triage_run after finalize. The Python dispatcher
        # path historically skipped it — SESSIONS rail f/x/n stayed at · 0 forever
        # for scaffold/workflow/codex runs. Triage is fail-open decoration.
        if handle.meta_path is not None and handle.exit_code is not None:
            try:
                from .run_triage import triage_finished_run

                triage_outcome = triage_finished_run(handle.meta_path)
                summary["triage"] = triage_outcome.outcome
                if triage_outcome.bucket:
                    summary["triage_bucket"] = triage_outcome.bucket
                if triage_outcome.reason:
                    summary["triage_reason"] = triage_outcome.reason
            except Exception as exc:  # noqa: BLE001 — never fail a finished run on triage
                summary["triage"] = "error"
                summary["triage_reason"] = (
                    f"dispatcher_hook: {type(exc).__name__}: {exc}"
                )

        # A client that starts as the worker exits can still connect and receive
        # the replayed terminal line. File truth handles later callers.
        await asyncio.sleep(0.15)

        if args.quiet:
            pass
        elif args.json:
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        else:
            print(f"run_id: {summary['run_id']}")
            print(f"state: {summary['state']}")
            print(f"exit_code: {summary['exit_code']}")
            print(f"artifact_ok: {str(summary['artifact_ok']).lower()}")
            if artifact_errors:
                print("artifact_errors: " + ",".join(artifact_errors))
            if summary.get("triage"):
                line = f"triage: {summary['triage']}"
                if summary.get("triage_bucket"):
                    line += f" → {summary['triage_bucket']}"
                print(line)

        exit_code = handle.exit_code
        if isinstance(exit_code, int) and exit_code != 0:
            return exit_code
        return 0 if summary["artifact_ok"] else 2
    finally:
        signal_server.close()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: parse argv, run the ``run`` subcommand, return the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.print_help(sys.stderr)
        return 2
    if "--tee-output" in (argv or sys.argv[1:]):
        os.environ["VIBECRAFTED_TEE_OUTPUT"] = "1"
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _exit_on_sigterm(_signum: int, _frame: object) -> None:
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    try:
        try:
            return asyncio.run(_run(args))
        except ValueError as exc:
            print(f"dispatcher: {exc}", file=sys.stderr)
            return 2
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
