"""Operator interactive loop (continue/complete) plus await-run and spanko recovery CLI verbs."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import control_plane, cron, ui
from .clock import utc_now_z


def repo_root(start: Path | None = None) -> Path:
    """Resolve the git repository root from ``start`` (or cwd); falls back to that
    resolved directory itself when git is unavailable or the path isn't a repo."""
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start or Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip())
    return (start or Path.cwd()).resolve()


def default_state_file(root: Path | None = None) -> Path:
    """Default path of the operator loop's local state file within the repo."""
    return repo_root(root) / ".vibecrafted" / "operator-loop.local.md"


@dataclass
class LoopState:
    """Parsed operator-loop state file: YAML-ish frontmatter fields plus the prompt body."""

    path: Path
    fields: dict[str, str]
    prompt: str

    @property
    def active(self) -> bool:
        """True when the loop is still running (has not been cancelled/completed)."""
        return self.fields.get("active") == "true"

    @property
    def iteration(self) -> int:
        """Current iteration counter (0 if absent/unparsable)."""
        return int(self.fields.get("iteration") or "0")

    @property
    def max_iterations(self) -> int:
        """Configured iteration ceiling; 0 means unlimited."""
        return int(self.fields.get("max_iterations") or "0")


def quote_yaml(value: str | None) -> str:
    """JSON-quote a string for embedding as a scalar frontmatter value, or "null"."""
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False)


def parse_state(path: Path) -> LoopState:
    """Read and parse the operator loop's state file; raises FileNotFoundError if missing."""
    if not path.is_file():
        raise FileNotFoundError(f"no active operator loop state: {path}")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    fields: dict[str, str] = {}
    prompt_start = 0
    if lines[:1] == ["---"]:
        for idx, line in enumerate(lines[1:], start=1):
            if line == "---":
                prompt_start = idx + 1
                break
            if ":" in line:
                key, raw = line.split(":", 1)
                fields[key.strip()] = raw.strip().strip('"')
    prompt = "\n".join(lines[prompt_start:]).lstrip("\n")
    return LoopState(path=path, fields=fields, prompt=prompt)


def write_state(path: Path, fields: dict[str, str], prompt: str) -> None:
    """Write the operator loop's state file: frontmatter fields plus the prompt body."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in fields.items():
        lines.append(f"{key}: {value}")
    lines.extend(["---", "", prompt])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def update_field(state: LoopState, key: str, value: str) -> None:
    """Set one field on the loop state and rewrite the state file."""
    fields = dict(state.fields)
    fields[key] = value
    write_state(state.path, fields, state.prompt)


def resolve_state_file(raw: str = "") -> Path:
    """Resolve the loop state file path: explicit arg, then env var, then default."""
    if raw:
        return Path(raw).expanduser()
    env = os.environ.get("VIBECRAFTED_LOOP_STATE_FILE")
    if env:
        return Path(env).expanduser()
    return default_state_file()


def command_deck() -> str:
    """The ``vibecrafted`` CLI entrypoint name/path, overridable via env var."""
    return os.environ.get("VIBECRAFTED_CMD") or "vibecrafted"


def _framework_heartbeat(*, root: Path, run_id: str, then_cmd: str = "") -> int:
    """Fire one immediate ``vibecrafted cron tick`` heartbeat for a running worker."""
    argv = [
        "tick",
        "--root",
        str(root),
        "--after-idle-minutes",
        "0",
    ]
    if then_cmd:
        argv.extend(["--then-cmd", then_cmd])
    return cron.main(argv)


def _run_report_path(run: dict[str, Any]) -> Path | None:
    """Extract the run's report path from a run payload, if present."""
    raw = str(run.get("latest_report") or run.get("report") or "")
    return Path(raw).expanduser() if raw else None


def _run_from_resolved(run_id: str) -> dict[str, Any] | None:
    """Load a run's meta payload via control_plane.resolve_run, merged with report paths.

    Returns None on any resolution/parse failure so callers can fall back safely."""
    try:
        resolved = control_plane.resolve_run(run_id)
    except (
        control_plane.RunNotResolved,
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ):
        return None
    payload: dict[str, Any] = {}
    meta = getattr(resolved, "meta", None)
    if meta is not None and Path(meta).is_file():
        try:
            payload.update(json.loads(Path(meta).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None
    payload["run_id"] = str(payload.get("run_id") or run_id)
    report = getattr(resolved, "report", None)
    if report and not payload.get("latest_report"):
        payload["latest_report"] = str(report)
    if report and not payload.get("report"):
        payload["report"] = str(report)
    if not payload.get("state"):
        payload["state"] = str(payload.get("status") or "")
    return payload


def _runtime_run_terminal(run: dict[str, Any]) -> bool:
    """True when a resolved run payload shows the worker has reached a terminal state."""
    state = str(run.get("state") or run.get("status") or "")
    liveness = str(run.get("liveness") or "")
    return (
        state in {"report_validated", "completed", "closed", "failed", "stopped"}
        or liveness == "terminal"
        or run.get("exit_code") is not None
    )


def _report_gate_lines(report: Path | None) -> list[str]:
    """First up to 6 report lines that mention gate/check/test/verify keywords."""
    if report is None or not report.is_file():
        return []
    needles = ("gate", "check", "test", "verify", "verification", "pytest", "cargo")
    lines: list[str] = []
    for line in report.read_text(encoding="utf-8", errors="replace").splitlines():
        lowered = line.lower()
        if any(needle in lowered for needle in needles):
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
        if len(lines) >= 6:
            break
    return lines


def _commit_from_run_or_report(run: dict[str, Any], report: Path | None) -> str:
    """Best-effort commit sha: prefer run payload fields, else scan the report text."""
    for key in ("commit", "commit_sha", "sha", "head_sha"):
        value = str(run.get(key) or "").strip()
        if value:
            return value
    if report is None or not report.is_file():
        return ""
    for line in report.read_text(encoding="utf-8", errors="replace").splitlines():
        lowered = line.lower().strip()
        if lowered.startswith(("commit:", "sha:", "commit sha:")):
            return line.split(":", 1)[1].strip()
    return ""


def _artifact_green(run: dict[str, Any]) -> bool:
    """True unless the run explicitly flags artifact_ok=False or lists artifact errors."""
    errors = [str(item) for item in (run.get("artifact_errors") or []) if str(item)]
    return run.get("artifact_ok") is not False and not errors


def _evidence(run: dict[str, Any], *, verifier: str) -> str:
    """One-line evidence string (sha, gate hits, verifier) for a tracker-flip annotation."""
    report = _run_report_path(run)
    sha = _commit_from_run_or_report(run, report) or "sha:unknown"
    gate_lines = _report_gate_lines(report)
    gates = "; ".join(gate_lines) if gate_lines else "gates:artifact_ok"
    return f"sha={sha}; {gates}; verified_by={verifier}"


def _replace_tracker_state(
    tracker: Path,
    *,
    cut_id: str,
    from_state: str,
    to_state: str,
    evidence: str,
) -> bool:
    """Flip a cut's state marker in a tracker file's first matching line and append
    evidence; returns whether a matching line was found and changed."""
    if not tracker.is_file():
        raise FileNotFoundError(f"tracker not found: {tracker}")
    lines = tracker.read_text(encoding="utf-8", errors="replace").splitlines()
    changed = False
    for idx, line in enumerate(lines):
        if changed or cut_id not in line or from_state not in line:
            continue
        next_line = line.replace(from_state, to_state, 1)
        if next_line.lstrip().startswith("|") and next_line.rstrip().endswith("|"):
            parts = next_line.split("|")
            if len(parts) >= 4:
                current = parts[-2].strip()
                parts[-2] = f" {current}; {evidence} " if current else f" {evidence} "
                next_line = "|".join(parts)
        else:
            next_line = f"{next_line} <!-- evidence: {evidence} -->"
        lines[idx] = next_line
        changed = True
    if changed:
        tracker.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def _stop_stalled_run(run_id: str) -> dict[str, Any]:
    """Route recovery through the identity-qualified stop authority."""
    from .workflow import stop_run

    return stop_run(
        run_id,
        reason="spanko stall recovery",
        grace_seconds=0.5,
    )


def _run_then(command: str, *, root: Path, baton: str) -> int:
    """Run an operator-approved ``--then`` command with the baton in its environment."""
    if not command:
        return 0
    env = os.environ.copy()
    env["VIBECRAFTED_BATON"] = baton
    return subprocess.run(
        shlex.split(command), cwd=root, env=env, check=False
    ).returncode


def _baton(run_id: str, run: dict[str, Any], *, phase: str, evidence: str) -> str:
    """Render the fixed-shape BATON text block printed/handed off between spanko phases."""
    return "\n".join(
        [
            f"BATON phase={phase}",
            f"run_id={run_id}",
            f"state={run.get('state') or ''}",
            f"operator_state={run.get('operator_state') or ''}",
            evidence,
        ]
    )


def cmd_start(args: argparse.Namespace) -> int:
    """``loop start``: create/overwrite the operator loop state with a fresh prompt."""
    state_file = resolve_state_file(args.state_file)
    prompt = args.prompt or ""
    if args.file:
        prompt = Path(args.file).expanduser().read_text(encoding="utf-8")
    if not prompt:
        ui.err("loop needs a prompt", fix='vibecrafted loop start -p "<prompt>"')
        return 1
    if args.max_iterations < 0:
        ui.err("--max-iterations must be >= 0")
        return 1
    session_id = (
        os.environ.get("CODEX_SESSION_ID")
        or os.environ.get("CLAUDE_CODE_SESSION_ID")
        or os.environ.get("VIBECRAFTED_OPERATOR_SESSION_ID")
        or ""
    )
    now = utc_now_z()
    fields = {
        "active": "true",
        "runtime": "operator-interactive",
        "iteration": "1",
        "session_id": quote_yaml(session_id),
        "max_iterations": str(args.max_iterations),
        "completion_promise": quote_yaml(args.completion_promise)
        if args.completion_promise
        else "null",
        "started_at": quote_yaml(now),
        "updated_at": quote_yaml(now),
        "root": quote_yaml(str(repo_root())),
    }
    write_state(state_file, fields, prompt)
    max_desc = str(args.max_iterations) if args.max_iterations else "unlimited"
    promise = args.completion_promise or "none"
    ui.ok(f"operator loop activated · iteration 1 · max {max_desc}")
    print(f"  state: {state_file}")
    print(f"  promise: {promise}")
    print()
    print("Protocol for the active Agent-Operator:")
    print("1. Work normally in this same interactive session.")
    print("2. Before final answer, run: vibecrafted loop next")
    print("3. If it prints CONTINUE, continue with the printed prompt.")
    print('4. Stop only with: vibecrafted loop complete --promise "<text>"')
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """``loop status``: print the current loop state's frontmatter fields."""
    state = parse_state(resolve_state_file(args.state_file))
    for key, value in state.fields.items():
        print(f"{key}: {value}")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    """``loop cancel``: mark the loop inactive with stop_reason=cancel."""
    state = parse_state(resolve_state_file(args.state_file))
    fields = dict(state.fields)
    fields["active"] = "false"
    fields["stopped_at"] = quote_yaml(utc_now_z())
    fields["stop_reason"] = "cancel"
    write_state(state.path, fields, state.prompt)
    ui.ok(f"cancelled operator loop at iteration {state.fields.get('iteration', '0')}")
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    """``loop complete``: stop the loop, requiring the completion promise to match if set."""
    state = parse_state(resolve_state_file(args.state_file))
    expected = state.fields.get("completion_promise", "null").strip('"')
    if expected not in {"", "null"} and args.promise != expected:
        ui.err(f"promise mismatch — expected: {expected}")
        return 3
    fields = dict(state.fields)
    fields["active"] = "false"
    fields["stopped_at"] = quote_yaml(utc_now_z())
    fields["stop_reason"] = "promise"
    write_state(state.path, fields, state.prompt)
    if expected not in {"", "null"}:
        print(f"Completed operator loop with <promise>{expected}</promise>.")
    else:
        print("Completed operator loop.")
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    """``loop next``: advance one iteration, printing CONTINUE with the prompt or STOP."""
    state = parse_state(resolve_state_file(args.state_file))
    if not state.active:
        print("STOP: operator loop inactive.")
        return 0
    if state.max_iterations > 0 and state.iteration >= state.max_iterations:
        fields = dict(state.fields)
        fields["active"] = "false"
        fields["stopped_at"] = quote_yaml(utc_now_z())
        fields["stop_reason"] = "max_iterations"
        write_state(state.path, fields, state.prompt)
        print(f"STOP: max iterations reached ({state.max_iterations}).")
        return 0
    next_iteration = state.iteration + 1
    fields = dict(state.fields)
    fields["iteration"] = str(next_iteration)
    fields["updated_at"] = quote_yaml(utc_now_z())
    write_state(state.path, fields, state.prompt)
    print(f"CONTINUE: operator loop iteration {next_iteration}")
    promise = state.fields.get("completion_promise", "null").strip('"')
    if promise not in {"", "null"}:
        print(
            f"Completion promise: <promise>{promise}</promise> only when completely true."
        )
    print("\n--- PROMPT ---")
    print(state.prompt, end="" if state.prompt.endswith("\n") else "\n")
    return 0


def cmd_await_run(args: argparse.Namespace) -> int:
    """``loop await-run``: block (or background-spawn) an await of one run via the deck."""
    if args.agent not in {
        "claude",
        "codex",
        "gemini",
        "agy",
        "junie",
        "grok",
        "opencode",
    }:
        ui.err(
            f"unknown agent: {args.agent}",
            fix="use one of: claude · codex · gemini · agy · junie · grok · opencode",
        )
        return 1
    if not args.run_id:
        ui.err("await-run requires --run-id")
        return 1

    def run_wait() -> int:
        """Foreground await of the run via the agent deck, then run --then-cmd on success."""
        print(f"[{utc_now_z()}] awaiting {args.run_id} via {args.agent}")
        proc = subprocess.run(
            [command_deck(), args.agent, "await", "--run-id", args.run_id], check=False
        )
        print(f"[{utc_now_z()}] await finished rc={proc.returncode} for {args.run_id}")
        if proc.returncode == 0 and args.then_cmd:
            print(
                f"[{utc_now_z()}] running operator-approved next command via argv: {args.then_cmd}"
            )
            return subprocess.run(shlex.split(args.then_cmd), check=False).returncode
        return proc.returncode

    if args.foreground:
        return run_wait()

    log_path = (
        Path(args.log).expanduser()
        if args.log
        else repo_root()
        / ".vibecrafted"
        / "reports"
        / f"operator-loop-await-{args.run_id}.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "from vibecrafted_core.loop import main; "
        f"raise SystemExit(main(['await-run','--foreground','--agent',{args.agent!r},'--run-id',{args.run_id!r}"
    )
    if args.then_cmd:
        script += f",'--then-cmd',{args.then_cmd!r}"
    script += "]))"
    with log_path.open("ab") as out:
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=repo_root(),
            env=os.environ.copy(),
        )
    ui.ok(f"await armed · {args.run_id} · pid {proc.pid}")
    print(f"  log: {log_path}")
    if args.then_cmd:
        print(f"  then: {args.then_cmd}")
    return 0


def cmd_spanko(args: argparse.Namespace) -> int:
    """``loop spanko``: heartbeat + await a run to completion, verify, and flip the tracker
    on green; on stall or a failed artifact gate, hand off a recovery baton instead."""
    if args.agent not in {
        "claude",
        "codex",
        "gemini",
        "agy",
        "junie",
        "grok",
        "opencode",
    }:
        ui.err(
            f"unknown agent: {args.agent}",
            fix="use one of: claude · codex · gemini · agy · junie · grok · opencode",
        )
        return 1
    if not args.run_id:
        ui.err("spanko requires --run-id")
        return 1

    root = Path(args.root or Path.cwd()).expanduser().resolve()
    print(f"[{utc_now_z()}] spanko heartbeat via vibecrafted cron tick")
    heartbeat_rc = _framework_heartbeat(root=root, run_id=args.run_id)
    if heartbeat_rc != 0:
        ui.warn(
            "framework heartbeat failed; harness /loop is a last resort only if the vibecrafted CLI is unavailable"
        )

    print(f"[{utc_now_z()}] awaiting {args.run_id} via control-plane await_run")
    payload = control_plane.await_run(
        args.run_id,
        timeout_seconds=float(args.timeout_seconds),
        interval_seconds=float(args.interval_seconds),
    )
    run = dict(payload.get("run") or {})

    if payload.get("timed_out"):
        resolved_run = _run_from_resolved(args.run_id)
        if resolved_run is not None and _runtime_run_terminal(resolved_run):
            payload = {
                "run_id": args.run_id,
                "completed": True,
                "timed_out": False,
                "run": resolved_run,
            }
            run = resolved_run
        else:
            return _cmd_spanko_stall(args, root=root, run=run)

    if not payload.get("completed"):
        ui.err(f"run did not reach terminal state: {args.run_id}")
        return 3

    if not _artifact_green(run):
        evidence = "artifact gate failed: " + ", ".join(
            str(item) for item in (run.get("artifact_errors") or ["artifact_not_ok"])
        )
        print(_baton(args.run_id, run, phase="blocked", evidence=evidence))
        return 3

    if not args.verify:
        ui.err("spanko requires --verify before tracker flip")
        return 1
    print(f"[{utc_now_z()}] sprawdzenie: {args.verify}")
    verify = subprocess.run(shlex.split(args.verify), cwd=root, check=False)
    if verify.returncode != 0:
        ui.err(f"sprawdzenie failed rc={verify.returncode}; tracker not flipped")
        return int(verify.returncode)

    evidence = _evidence(run, verifier=args.agent)
    if args.tracker and args.cut_id:
        flipped = _replace_tracker_state(
            Path(args.tracker).expanduser(),
            cut_id=args.cut_id,
            from_state="[~]",
            to_state="[x]",
            evidence=evidence,
        )
        if not flipped:
            ui.warn(f"tracker cut not flipped: {args.cut_id}")

    baton = _baton(args.run_id, run, phase="next", evidence=evidence)
    print(baton)
    if args.then:
        print(f"[{utc_now_z()}] baton then: {args.then}")
        return _run_then(args.then, root=root, baton=baton)
    return 0


def _cmd_spanko_stall(
    args: argparse.Namespace, *, root: Path, run: dict[str, Any]
) -> int:
    """Recovery path for cmd_spanko when the await times out: stop the stalled worker,
    flip the tracker to the recovery marker, and emit a recovery-phase baton."""
    evidence = (
        "stall: control-plane await timed out; follow "
        "skills/vc-dispatch/references/pulse-and-stall.md before blind restart"
    )
    stop = _stop_stalled_run(args.run_id)
    if stop.get("accepted"):
        evidence = (
            f"{evidence}; stop=accepted"
            f"; target_pid={stop.get('target_pid') or 'already-gone'}"
        )
    else:
        refusal = stop.get("error") or stop.get("reason") or "unproven"
        evidence = f"{evidence}; stop=refused:{refusal}"
    if args.tracker and args.cut_id:
        _replace_tracker_state(
            Path(args.tracker).expanduser(),
            cut_id=args.cut_id,
            from_state="[~]",
            to_state="[!]",
            evidence=evidence,
        )
    baton = _baton(args.run_id, run, phase="recovery", evidence=evidence)
    if args.then:
        _run_then(args.then, root=root, baton=baton)
    print(baton)
    return 4


def _build_parser() -> argparse.ArgumentParser:
    """Construct the ``vibecrafted loop`` argparse CLI with all subcommands wired."""
    parser = argparse.ArgumentParser(prog="vibecrafted loop")
    sub = parser.add_subparsers(dest="command")

    start = sub.add_parser("start")
    start.add_argument("--state-file", default="")
    start.add_argument("-p", "--prompt", default="")
    start.add_argument("-f", "--file", default="")
    start.add_argument("--max-iterations", type=int, default=0)
    start.add_argument("--completion-promise", default="")
    start.set_defaults(func=cmd_start)

    for name, func in {
        "status": cmd_status,
        "next": cmd_next,
        "cancel": cmd_cancel,
    }.items():
        item = sub.add_parser(name)
        item.add_argument("--state-file", default="")
        item.set_defaults(func=func)

    complete = sub.add_parser("complete")
    complete.add_argument("--state-file", default="")
    complete.add_argument("--promise", default="")
    complete.set_defaults(func=cmd_complete)

    await_run = sub.add_parser("await-run")
    await_run.add_argument("--run-id", default="")
    await_run.add_argument("--agent", default="codex")
    await_run.add_argument("--then-cmd", default="")
    await_run.add_argument("--foreground", action="store_true")
    await_run.add_argument("--log", default="")
    await_run.set_defaults(func=cmd_await_run)

    spanko = sub.add_parser("spanko")
    spanko.add_argument("--run-id", default="")
    spanko.add_argument("--agent", default="codex")
    spanko.add_argument("--verify", default="")
    spanko.add_argument("--tracker", default="")
    spanko.add_argument("--cut-id", default="")
    spanko.add_argument("--then", default="")
    spanko.add_argument("--root", default="")
    spanko.add_argument("--timeout-seconds", type=float, default=300)
    spanko.add_argument("--interval-seconds", type=float, default=5)
    spanko.set_defaults(func=cmd_spanko)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """``vibecrafted loop`` CLI entrypoint: parse argv and dispatch to the matched subcommand."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        ui.err(str(exc), fix='vibecrafted loop start -p "<prompt>"')
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
