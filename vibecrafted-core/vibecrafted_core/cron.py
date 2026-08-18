"""LOOP heartbeat cron helper: idle-aware tick execution and crontab line generation."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from . import ui
from .autonomy_surface import destructive_remote_push
from .runtime_paths import vibecrafted_home

HARD_STOP_NEEDLES = (
    "git reset --hard",
    "git checkout --",
    "rm -rf",
    "npm publish",
    "pnpm publish",
    "cargo publish",
    "gh release",
    "deploy",
    "publish",
    "release",
)


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Return the current UTC time as an ISO-8601 string with a trailing ``Z``."""
    return utc_now().isoformat().replace("+00:00", "Z")


def parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse a simple ``---``-delimited key: value frontmatter block from a file.

    Returns an empty dict when the file is missing, unreadable, or lacks a
    frontmatter block. Not a full YAML parser — one ``key: value`` per line.
    """
    if not path.is_file():
        return {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        # Frontmatter is optional launch metadata. A transient permission or
        # filesystem failure must not turn model discovery into a hard stop.
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        values[key.strip()] = raw.strip().strip('"')
    return values


def parse_time(raw: str) -> datetime | None:
    """Parse an ISO-8601 timestamp (``Z`` suffix accepted) into a UTC datetime.

    Returns None for empty or unparseable input rather than raising.
    """
    if not raw:
        return None
    normalized = raw.strip().strip('"')
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def idle_minutes(state: dict[str, str]) -> float | None:
    """Return minutes since ``updated_at`` (falling back to ``started_at``), or None."""
    anchor = parse_time(state.get("updated_at", "")) or parse_time(
        state.get("started_at", "")
    )
    if anchor is None:
        return None
    return max((utc_now() - anchor).total_seconds() / 60, 0)


def default_state_file(root: Path) -> Path:
    """Return the default operator-loop state file path for a repo root."""
    return root / ".vibecrafted" / "operator-loop.local.md"


def default_journal() -> Path:
    """Return the default location for the loop-cron JSONL journal."""
    return vibecrafted_home() / "runtime" / "loop-cron.jsonl"


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    """Append one JSON payload as a line to ``path``, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        )


def hard_stop_reason(command: str) -> str:
    """Return the matched HARD_STOP_NEEDLES substring if ``command`` is dangerous, else ""."""
    destructive = destructive_remote_push(command)
    if destructive is not None:
        return destructive
    lowered = command.lower()
    for needle in HARD_STOP_NEEDLES:
        if needle in lowered:
            return needle
    return ""


def run_capture(
    command: list[str], cwd: Path, output: Path, timeout: int
) -> dict[str, object]:
    """Run one command, write combined stdout+stderr to ``output``, and report exit status.

    Swallows OSError and TimeoutExpired into the result dict (exit_code 127/124)
    rather than raising, so a caller can journal a failed capture attempt.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    started = iso_now()
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        output.write_text(proc.stdout + proc.stderr, encoding="utf-8")
        return {
            "command": command,
            "path": str(output),
            "exit_code": proc.returncode,
            "started_at": started,
            "finished_at": iso_now(),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        output.write_text(str(exc), encoding="utf-8")
        return {
            "command": command,
            "path": str(output),
            "exit_code": 124 if isinstance(exc, subprocess.TimeoutExpired) else 127,
            "started_at": started,
            "finished_at": iso_now(),
            "error": str(exc),
        }


def run_capture_candidates(
    commands: list[list[str]],
    cwd: Path,
    output: Path,
    timeout: int,
) -> dict[str, object]:
    """Try each command in order via run_capture, stopping at the first exit_code 0.

    Returns the last attempted result (with all attempts recorded) if none succeed.
    """
    attempts: list[dict[str, object]] = []
    last: dict[str, object] | None = None
    for command in commands:
        result = run_capture(command, cwd, output, timeout)
        attempts.append(
            {
                "command": command,
                "exit_code": result.get("exit_code"),
                "error": result.get("error", ""),
            }
        )
        last = result
        if result.get("exit_code") == 0:
            result["attempts"] = attempts
            return result
    if last is None:
        last = {
            "command": [],
            "path": str(output),
            "exit_code": 127,
            "error": "no commands supplied",
        }
    last["attempts"] = attempts
    return last


def capture_context(root: Path, run_id: str, timeout: int) -> list[dict[str, object]]:
    """Capture a loct structural snapshot and an aicx intents snapshot for one tick.

    Output files are stamped under vibecrafted_home()/runtime/cron-context/ using
    a sanitized run_id. aicx capture tries intents, then search, then a bare list.
    """
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    safe_run = "".join(
        ch if ch.isalnum() or ch in "._-" else "-" for ch in (run_id or "loop")
    )
    base = vibecrafted_home() / "runtime" / "cron-context" / f"{stamp}-{safe_run}"
    project = root.name
    return [
        run_capture(
            ["loct", "context", "--full", "--markdown"],
            root,
            base.with_suffix(".loct.md"),
            timeout,
        ),
        run_capture_candidates(
            [
                [
                    "aicx",
                    "intents",
                    "-p",
                    project,
                    "--limit",
                    "20",
                    "--emit",
                    "markdown",
                ],
                [
                    "aicx",
                    "search",
                    "--no-semantic",
                    "-p",
                    project,
                    "--limit",
                    "10",
                    "recent intent agent claims verified outcomes unresolved human decisions",
                ],
                ["aicx", "list"],
            ],
            root,
            base.with_suffix(".aicx.txt"),
            timeout,
        ),
    ]


def tick(args: argparse.Namespace) -> int:
    """Run one LOOP heartbeat tick: read state, optionally capture context, optionally
    run ``--then-cmd`` (refused if it matches a HARD_STOP_NEEDLES pattern or the loop
    is inactive/too-recently-idle), journal the result, and print a summary.
    """
    root = Path(args.root or Path.cwd()).expanduser().resolve()
    state_file = (
        Path(args.state_file).expanduser()
        if args.state_file
        else default_state_file(root)
    )
    journal = Path(args.journal).expanduser() if args.journal else default_journal()
    state = parse_frontmatter(state_file)
    idle = idle_minutes(state)
    active = state.get("active") == "true"
    run_id = state.get("run_id") or state.get("session_id") or "loop"

    context_results: list[dict[str, object]] = []
    if args.context:
        context_results = capture_context(root, run_id, args.context_timeout)

    command_result: dict[str, object] | None = None
    should_run = bool(args.then_cmd) and active
    if should_run and idle is not None and idle < args.after_idle_minutes:
        should_run = False
    if should_run and args.then_cmd:
        reason = hard_stop_reason(args.then_cmd)
        if reason:
            command_result = {
                "status": "refused",
                "reason": f"hard-stop command contains: {reason}",
                "command": args.then_cmd,
            }
        else:
            proc = subprocess.run(
                shlex.split(args.then_cmd),
                cwd=root,
                text=True,
                check=False,
            )
            command_result = {
                "status": "ran",
                "command": args.then_cmd,
                "exit_code": proc.returncode,
            }

    payload: dict[str, object] = {
        "ts": iso_now(),
        "event": "loop-cron-tick",
        "root": str(root),
        "state_file": str(state_file),
        "state_exists": state_file.is_file(),
        "active": active,
        "iteration": state.get("iteration", ""),
        "run_id": run_id,
        "idle_minutes": idle,
        "after_idle_minutes": args.after_idle_minutes,
        "context": context_results,
        "then": command_result,
    }
    append_jsonl(journal, payload)
    if getattr(args, "json", False):
        # Machine consumers (crontab logs, agents) opt in to the raw payload.
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        status = "active" if active else "inactive"
        idle_part = f" · idle {idle:.0f}m" if idle is not None else ""
        ui.ok(f"loop tick · {run_id} · {status}{idle_part}")
        if command_result is not None:
            if command_result.get("status") == "refused":
                ui.warn(f"then-cmd refused — {command_result.get('reason')}")
            else:
                exit_code = command_result.get("exit_code")
                code = (
                    exit_code
                    if isinstance(exit_code, int)
                    else int(str(exit_code or 0))
                )
                if code == 0:
                    ui.ok(f"then-cmd ok · {args.then_cmd}")
                else:
                    ui.err(f"then-cmd exited {code}", log=str(journal))
    if command_result and command_result.get("status") == "ran":
        exit_code = command_result.get("exit_code")
        return exit_code if isinstance(exit_code, int) else int(str(exit_code or 0))
    return 0


def cron_line(args: argparse.Namespace) -> int:
    """Print a ready-to-paste crontab line that invokes ``vibecrafted cron tick``."""
    every = max(args.every_minutes, 1)
    root = Path(args.root or Path.cwd()).expanduser().resolve()
    script = args.deck_command or "vibecrafted"
    parts = [
        shlex.quote(script),
        "cron",
        "tick",
        "--json",
        "--root",
        shlex.quote(str(root)),
        "--after-idle-minutes",
        str(args.after_idle_minutes),
    ]
    if not args.context:
        parts.append("--no-context")
    if args.then_cmd:
        parts.extend(["--then-cmd", shlex.quote(args.then_cmd)])
    log = (
        Path(args.log).expanduser()
        if args.log
        else vibecrafted_home() / "runtime" / "loop-cron.log"
    )
    schedule = f"*/{every} * * * *"
    path_prefix = 'PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH";'
    print(
        f"{schedule} {path_prefix} cd {shlex.quote(str(root))} && {' '.join(parts)} >> {shlex.quote(str(log))} 2>&1"
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the ``tick`` and ``line`` cron subcommands."""
    parser = argparse.ArgumentParser(prog="vibecrafted cron")
    sub = parser.add_subparsers(dest="action")

    tick_parser = sub.add_parser(
        "tick", help="append one LOOP heartbeat and optional context snapshot"
    )
    tick_parser.add_argument("--root", default="")
    tick_parser.add_argument("--state-file", default="")
    tick_parser.add_argument("--journal", default="")
    tick_parser.add_argument(
        "--json",
        action="store_true",
        help="print the raw tick payload (default: one-line human summary)",
    )
    tick_parser.add_argument("--after-idle-minutes", type=int, default=10)
    tick_parser.add_argument("--then-cmd", default="")
    tick_parser.add_argument(
        "--context", dest="context", action="store_true", default=True
    )
    tick_parser.add_argument("--no-context", dest="context", action="store_false")
    tick_parser.add_argument("--context-timeout", type=int, default=60)

    line_parser = sub.add_parser("line", help="print a crontab line for LOOP heartbeat")
    line_parser.add_argument("--root", default="")
    line_parser.add_argument("--every-minutes", type=int, default=10)
    line_parser.add_argument("--after-idle-minutes", type=int, default=10)
    line_parser.add_argument("--then-cmd", default="")
    line_parser.add_argument("--command", dest="deck_command", default="")
    line_parser.add_argument("--log", default="")
    line_parser.add_argument(
        "--context", dest="context", action="store_true", default=True
    )
    line_parser.add_argument("--no-context", dest="context", action="store_false")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: dispatch to ``tick`` or ``line``, else print help and return 2."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.action == "tick":
        return tick(args)
    if args.action == "line":
        return cron_line(args)
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
