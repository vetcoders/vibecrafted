"""Vibecrafted core entrypoint: routes ``vibecrafted``/shell-wrapper argv to the
Python launch/observe/await surface or falls back to the legacy bash deck."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import doctor as doctor_module
from .agent_stream import ANSI_PATTERN, AgentStreamParser, resolve_default_model
from .control_plane import (
    RunNotResolved,
    await_run,
    lookup_run,
    resolve_run,
    sync_state,
)
from .package_resources import deck_path, package_root
from .runtime_paths import is_operator_home_root, resolve_operator_launch_root
from .workflow import (
    await_launch_truth,
    classify_resume_identity,
    find_run_for_identity_token,
    launch_workflow,
    looks_like_control_plane_run_id,
    manual_resume_session,
    normalize_launch_spec,
    operator_continue_run,
)

AGENTS = {"claude", "codex", "agy", "junie", "grok", "swarm"}
RESEARCH_ARITY = {"uno": 1, "duo": 2, "trio": 3}
LAUNCHERS = (
    "audit",
    "canary",
    "decorate",
    "delegate",
    "dou",
    "followup",
    "hydrate",
    "implement",
    "intents",
    "justdo",
    "marbles",
    "ownership",
    "partner",
    "paste",
    "polarize",
    "prune",
    "release",
    "research",
    "review",
    "scaffold",
    "trust",
    "guard",
    "workflow",
)
# No skill aliases: each LAUNCHERS name is its own skill id (ADR-0001: justdo
# is not implement). Keep the map only for legacy shell-wrapper renames if any.
LAUNCH_ALIASES: dict[str, str] = {}
# These installed names are symlinks to the ``vibecrafted`` Python entrypoint,
# but their behavior is still owned by the shell deck. Preserve the invoked
# name as an explicit deck verb instead of silently treating the first user
# argument as the command.
SHELL_WRAPPER_VERBS = {
    "telemetry": "telemetry",
    "vc-dashboard": "dashboard",
    "vc-dispatch": "dispatch",
    "vc-doctor": "doctor",
    "vc-help": "help",
    "vc-init": "init",
    "vc-justdo": "justdo",
    "vc-receipt": "receipt",
    "vc-resume": "resume",
    "vc-start": "start",
    "vc-status": "status",
    "vc-update": "update",
}
SUCCESS_STATES = {"report_validated", "completed", "closed"}
TERMINAL_STATES = {
    "blocked",
    "closed",
    "completed",
    "contract_failed",
    "failed",
    "ghost",
    "report_invalid",
    "report_missing",
    "report_validated",
    "stopped",
    "timed_out",
}
_INSTALLER_LEASE_FD_ENV = "VIBECRAFTED_INSTALL_LEASE_FD"
_INSTALLER_LOCK_NAME = ".vibecrafted-install.lock"
_EX_TEMPFAIL = 75


def _normalize_research_arity_args(args: Sequence[str]) -> list[str]:
    """Expand the stable uno/duo/trio contract before argparse sees agents."""
    normalized = list(args)
    if len(normalized) < 2 or normalized[0] != "research":
        return normalized
    keyword = normalized[1]
    expected = RESEARCH_ARITY.get(keyword)
    if expected is None:
        return normalized

    agents: list[str] = []
    cursor = 2
    while cursor < len(normalized) and not normalized[cursor].startswith("-"):
        agents.append(normalized[cursor])
        cursor += 1
    if len(agents) != expected:
        raise ValueError(
            f"{keyword} expects exactly {expected} agent(s), got {len(agents)}"
        )
    unsupported = [agent for agent in agents if agent not in AGENTS - {"swarm"}]
    if unsupported:
        raise ValueError(f"Unsupported research agent: {unsupported[0]}")
    return ["research", *agents, *normalized[cursor:]]


def _installer_lease_pass_fds(tools_home: Path) -> tuple[int, ...]:
    """Validate an inherited installer coordination fd and return it to pass through.

    Returns an empty tuple when no lease fd is present. Raises ``OSError`` if the
    fd is set but does not verifiably own ``tools_home``'s install lock file
    (regular file, same uid, single hardlink, same device/inode) — a forged or
    stale descriptor must never be forwarded to the deck subprocess.
    """
    raw_descriptor = os.environ.get(_INSTALLER_LEASE_FD_ENV)
    if not raw_descriptor:
        return ()
    if os.name != "posix":
        raise OSError("installer coordination descriptors require POSIX")
    try:
        descriptor = int(raw_descriptor)
    except ValueError as exc:
        raise OSError("invalid installer coordination descriptor") from exc
    if descriptor < 0:
        raise OSError("invalid installer coordination descriptor")

    lock_path = tools_home.resolve(strict=False) / _INSTALLER_LOCK_NAME
    try:
        opened = os.fstat(descriptor)
        named = os.stat(lock_path, follow_symlinks=False)
    except OSError as exc:
        raise OSError(
            f"installer coordination lease is unavailable at {lock_path}"
        ) from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or opened.st_uid != os.geteuid()
        or named.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or named.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise OSError(f"installer coordination descriptor does not own {lock_path}")
    return (descriptor,)


def _add_launch_parser(sub: argparse._SubParsersAction, name: str) -> None:
    """Register one LAUNCHERS subcommand with its shared and per-skill flags."""
    run = sub.add_parser(name, help=f"launch vc-{name} through core runtime")
    if name == "research":
        run.add_argument("agent", nargs="*")
    else:
        run.add_argument("agent", nargs="?")
    if name == "paste":
        run.add_argument("--skill", default="workflow")
        run.add_argument("--root", default="")
        run.add_argument("--print-prompt", action="store_true")
        run.add_argument("--dry-run", action="store_true")
        run.add_argument("--json", action="store_true")
        return
    run.add_argument("-p", "--prompt", default="")
    run.add_argument("-f", "--file", default="")
    run.add_argument(
        "--prompt-stdin",
        action="store_true",
        help="read the prompt from stdin and keep it out of argv/temp files",
    )
    run.add_argument("--runtime", default="")
    run.add_argument("--root", default="")
    run.add_argument("--mode", default="")
    run.add_argument("--count", type=int)
    run.add_argument("--depth", type=int)
    run.add_argument("--model", default="")
    if name == "research":
        run.add_argument("--synthesizer", default="")
        run.add_argument("--synthesizer-model", default="")
    run.add_argument("--source-dir", default="")
    run.add_argument("--json", action="store_true")


def _build_parser() -> argparse.ArgumentParser:
    """Assemble the full argparse tree for every python-owned subcommand."""
    parser = argparse.ArgumentParser(
        prog="vibecrafted",
        description="Vibecrafted core command surface.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("dispatch", help="run or validate a dispatch plan")
    doctor = sub.add_parser("doctor", help="verify installed Vibecrafted runtime")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument(
        "--release",
        action="store_true",
        help=(
            "probe GitHub Latest (gh release view --json tagName) and the "
            "latest Release source gate conclusion against the local VERSION "
            "file; mismatch is red and names the tag/publish operator button"
        ),
    )
    doctor.add_argument(
        "--quarantine-legacy-runs",
        action="store_true",
        help=(
            "one-shot migration: mark terminal runs without worker_pgid as "
            "reaper_ownership=legacy; best-effort recover pgid for live runs "
            "only when SPAWN_RUN_ID is positively visible"
        ),
    )
    receipt = sub.add_parser(
        "receipt",
        help=(
            "delivery/runtime receipt for fleet tools "
            "(source ↔ installed chain; never guesses from cwd)"
        ),
    )
    receipt.add_argument(
        "--json",
        action="store_true",
        help="machine-readable vibecrafted.delivery_receipt.v1",
    )
    capabilities = sub.add_parser(
        "capabilities",
        help="describe workflow execution contracts (versioned, machine-readable)",
    )
    capabilities.add_argument("--json", action="store_true")
    config = sub.add_parser(
        "config",
        help="install/wire packaged vc-frame config into the tools store and ~/.config/vc-frame",
    )
    config_sub = config.add_subparsers(dest="config_action")
    config_install = config_sub.add_parser(
        "install",
        help="stage package config → tools store + wire ~/.config/vc-frame view",
    )
    config_install.add_argument(
        "--dry-run",
        action="store_true",
        help="print the wiring plan without mutating the filesystem",
    )
    config_install.add_argument(
        "--force",
        action="store_true",
        help="replace healthy view links (default: leave healthy wiring alone)",
    )
    config_install.add_argument(
        "--prefer-repo",
        action="store_true",
        help="wire view to checkout config/vc-frame (dev mode)",
    )
    config_zshrc = config_sub.add_parser(
        "ensure-zshrc",
        help="idempotent host ~/.zshrc onboarding (create or fenced append)",
    )
    config_zshrc.add_argument("--dry-run", action="store_true")
    reap = sub.add_parser(
        "reap",
        help="terminate processes that outlived their run (survivors of terminal runs)",
    )
    reap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the would-kill table with ownership evidence, signal nothing",
    )
    reap.add_argument("--json", action="store_true")
    reap.add_argument(
        "--resettle",
        action="store_true",
        help=(
            "re-run settlement over retained control_plane/runs snapshots "
            "(honest: automatic FINALIZED only from a sealed delivery or an "
            "explicit worker attestation; a traced operator waive remains an "
            "override; never from bare exit 0)"
        ),
    )
    settle = sub.add_parser(
        "settle",
        help="settlement board maintenance (resettle retained snapshots)",
    )
    settle.add_argument(
        "--resettle",
        action="store_true",
        help="re-classify retained snapshots from existing axes (no invented f)",
    )
    settle.add_argument(
        "--dry-run",
        action="store_true",
        help="count would-rewrite only; do not write snapshots",
    )
    settle.add_argument("--json", action="store_true")
    workspace = sub.add_parser(
        "workspace",
        help=(
            "canonical Vibecrafted Workspace catalog "
            "(create|list|select|show|bury|recover|migrate|materialize|"
            "settlement-counts)"
        ),
    )
    workspace.add_argument(
        "workspace_argv",
        nargs=argparse.REMAINDER,
        help="workspace subcommand args (see vibecrafted workspace --help)",
    )
    settlements = sub.add_parser(
        "settlements",
        help=(
            "read-only settlement ledger query "
            "(summary | list | inspect); never invents f"
        ),
    )
    settlements_sub = settlements.add_subparsers(dest="settlements_action")
    settlements_summary = settlements_sub.add_parser(
        "summary",
        help="durable f/x/n lower-bound plus revalidation inventory",
    )
    settlements_summary.add_argument("--json", action="store_true")
    settlements_summary.add_argument(
        "--workspace-id",
        default="",
        help="scope f/x/n projection to one workspace_id (Cut A)",
    )
    settlements_list = settlements_sub.add_parser(
        "list",
        help="list or group latest-by-run settlements",
    )
    settlements_list.add_argument(
        "--bucket",
        choices=("f", "x", "n"),
        help="filter to TUI bucket f, x, or n",
    )
    settlements_list.add_argument(
        "--revalidatable",
        action="store_true",
        help="only runs with report+transcript still on disk",
    )
    settlements_list.add_argument(
        "--group",
        default="",
        help="comma-separated fields: agent,skill,reason,root,state,verdict",
    )
    settlements_list.add_argument("--limit", type=int, default=None)
    settlements_list.add_argument("--json", action="store_true")
    settlements_inspect = settlements_sub.add_parser(
        "inspect",
        help="inspect one run_id from the ledger + control-plane enrichment",
    )
    settlements_inspect.add_argument("run_id")
    settlements_inspect.add_argument("--json", action="store_true")
    procs = sub.add_parser(
        "procs",
        help="identity-qualified process snapshot/terminate for vc-procs TUI",
    )
    procs_sub = procs.add_subparsers(dest="procs_action")
    procs_sub.add_parser("snapshot", help="JSON process snapshot")
    term = procs_sub.add_parser("terminate", help="TERM→KILL with identity proof")
    term.add_argument("--pid", type=int, required=True)
    term.add_argument("--expected-start", required=True)
    term.add_argument("--expected-command-sha256", required=True)
    term.add_argument("--expected-run-id", default="")
    resume = sub.add_parser(
        "resume-session",
        help="continue one explicit provider session as a tracked headless run",
    )
    resume.add_argument(
        "agent",
        choices=sorted(AGENTS - {"swarm"}),
        help="provider owning the explicit session id",
    )
    resume.add_argument("--agent-session-id", required=True)
    prompt_input = resume.add_mutually_exclusive_group(required=True)
    prompt_input.add_argument("-p", "--prompt", default="")
    prompt_input.add_argument("-f", "--prompt-file", default="")
    prompt_input.add_argument(
        "--prompt-stdin",
        action="store_true",
        help="read the continuation prompt from stdin (keeps it out of argv)",
    )
    resume.add_argument("--root", default="")
    resume.add_argument("--source-dir", default="")
    resume.add_argument("--model", default="")
    resume.add_argument("--json", action="store_true")
    for name in LAUNCHERS:
        _add_launch_parser(sub, name)
    return parser


def _default_runtime(explicit_runtime: str, root: str = "") -> str:
    """Resolve launch surface: explicit > real operator TTY > headless.

    DELIBERATE REVERSAL of 141a19d / 3d794af (July 2026): those commits made
    dispatched workers prefer a visible ``terminal`` tab — either by
    inheriting an in-frame session env (``VC_FRAME_SESSION_NAME`` /
    ``ZELLIJ_SESSION_NAME``) or by discovering a live repo-bound vc-frame
    session — because headless dispatch left the operator blind. That
    visibility gap is now closed by the LIVE bucket viewer opened alongside
    every headless launch (see the ``Live runs`` bucket wiring / commit
    7be422aa, cut c1-live-bucket-viewer): the operator watches a
    ``tail -F``/``observe`` viewer tab instead of the worker itself owning a
    pane. Do NOT restore the env/live-session branches as a "fix" — that
    would resurrect worker tabs landing in the operator's own session
    (the exact bug Cut A / c1 closed). ``root`` is kept in the signature for
    call-site compatibility even though this function no longer consults it.
    """
    runtime = str(explicit_runtime or "").strip()
    if runtime:
        return runtime
    if sys.stdin.isatty() and sys.stdout.isatty():
        return "terminal"
    return "headless"


def _argv_names_stopped_run(argv: Sequence[str]) -> bool:
    """True when argv names a control-plane run (``--run-id`` / ``--last``)."""
    return any(
        token == "--last" or token == "--run-id" or token.startswith("--run-id=")
        for token in argv
    )


def _normalize_raw_args(raw_args: list[str]) -> list[str]:
    """Canonicalize leading pairs so later dispatch sees one shape.

    ``<agent> <launcher>`` becomes ``<launcher> <agent>``.
    ``resume <agent> --run-id|--last`` becomes ``<agent> resume …`` so the
    stopped-run flag is not delegated to the deck (which historically had no
    ``--run-id`` and swallowed it) or to ``_build_parser`` (no ``resume``
    subcommand). ``--session`` / bare resume stay resume-first for the deck.
    """
    if len(raw_args) >= 2 and raw_args[0] in AGENTS and raw_args[1] in LAUNCHERS:
        return [raw_args[1], raw_args[0], *raw_args[2:]]
    if (
        len(raw_args) >= 2
        and raw_args[0] == "resume"
        and raw_args[1] in AGENTS
        and _argv_names_stopped_run(raw_args[2:])
    ):
        return [raw_args[1], "resume", *raw_args[2:]]
    return raw_args


def _field(payload: dict[str, Any], name: str, default: str = "") -> str:
    """Coerce a payload value to ``str``, substituting ``default`` when falsy."""
    return str(payload.get(name) or default)


def _clip_line(line: str, *, max_chars: int = 500) -> str:
    """Truncate a display line to ``max_chars``, appending an ellipsis when cut."""
    if len(line) <= max_chars:
        return line
    return line[: max_chars - 1] + "…"


# Per-token streamers (grok emits one JSON event per token, e.g.
# {"type":"thought","data":"Good"}) render to far fewer lines than raw events:
# whole sentences coalesce into a single rendered line. Cutting the raw tail to
# `max_lines` BEFORE rendering would leave ~40 tokens (~1.5 sentences) of
# visibility, so we feed the parser a much wider raw tail and window to
# `max_lines` only AFTER rendering. 2000 raw lines comfortably covers 40
# rendered lines even at one-token-per-event rates while capping the
# render-feed cost on huge transcripts.
RAW_TAIL_LINES = 2000


def _tail_lines(
    path: str, *, agent: str = "", max_lines: int = 40
) -> tuple[list[str], str]:
    """Return the last ``max_lines`` of a transcript, rendered through the agent's
    stream parser when ``agent`` is given. Second element is an error code
    (``""`` on success) rather than a raised exception."""
    if not path:
        return [], "missing_path"
    transcript = Path(path).expanduser()
    try:
        if not transcript.is_file():
            return [], "missing_file"
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [], f"read_error:{type(exc).__name__}"
    if not lines:
        return [], "empty"
    if not agent:
        return [_clip_line(line) for line in lines[-max_lines:]], ""
    tail = lines[-RAW_TAIL_LINES:]
    parser = AgentStreamParser(agent, default_model=resolve_default_model(agent))
    chunks: list[str] = []
    saw_json = False
    for line in tail:
        if line.lstrip().startswith("{"):
            saw_json = True
        chunks.append(parser.feed_line((line + "\n").encode("utf-8")))
    # Coalesce first: parser fragments without trailing newlines (grok thought
    # tokens) must merge into full lines before any windowing happens.
    rendered: list[str] = []
    for rendered_line in "".join(chunks).splitlines():
        clean = rendered_line.strip()
        if clean and ANSI_PATTERN.sub("", clean).strip():
            rendered.append(_clip_line(clean))
    if rendered:
        return rendered[-max_lines:], ""
    if saw_json:
        return [], "no_renderable_events"
    return [_clip_line(line) for line in lines[-max_lines:]], ""


def _run_succeeded(run: dict[str, Any]) -> bool:
    """True when the run reached a success state with a clean artifact gate."""
    state = str(run.get("state") or "")
    errors = [str(item) for item in (run.get("artifact_errors") or []) if str(item)]
    return (
        state in SUCCESS_STATES and run.get("artifact_ok") is not False and not errors
    )


def _run_terminal(run: dict[str, Any]) -> bool:
    """True when the run's state, liveness, or exit code marks it finished."""
    if str(run.get("state") or "") in TERMINAL_STATES:
        return True
    if str(run.get("liveness") or "") == "terminal":
        return True
    return run.get("exit_code") is not None


def _print_launch_receipt(payload: dict[str, Any]) -> None:
    """Print the human-readable launch receipt block for a freshly spawned run."""
    run_id = _field(payload, "run_id")
    agent = _field(payload, "agent")
    print("==================== VIBECRAFTED LAUNCH RECEIPT ====================")
    print(f"run_id:     {run_id}")
    print(f"agent:      {agent}")
    print(f"skill:      {_field(payload, 'skill')}")
    print(f"root:       {_field(payload, 'root')}")
    print(f"dispatch:   {_field(payload, 'dispatch', '0')}")
    print(f"status:     {_field(payload, 'status', 'launching')}")
    reasons = _launch_receipt_reasons(payload)
    if reasons:
        print(f"reasons:    {'; '.join(reasons)}")
    print(f"control:    {_field(payload, 'control')}")
    print(f"report:     {_field(payload, 'report')}")
    print(f"transcript: {_field(payload, 'transcript')}")
    print(f"observe:    vibecrafted observe {agent} --run-id {run_id}")
    print(
        f"await (ARM NOW, supervisor-side): vibecrafted await {agent} --run-id {run_id}"
    )
    print("=====================================================================")


# Parity contract with the shell launcher's `spawn_watch_startup`
# (runtime/scripts/lib/launcher_watch.sh): same markers, same short window.
# The core dispatch path carried no such guard, so a fresh machine's first
# run — always unauthenticated — printed a receipt that reads like success
# while the worker had already died on "Not logged in".
STARTUP_FAILURE_HINTS: tuple[tuple[str, str], ...] = (
    ("Not logged in", "run `{agent}` once and complete the login, then retry"),
    ("Please run /login", "run `{agent}` once and complete the login, then retry"),
    ("Permission denied", "check file permissions for the run root"),
    ("command not found", "the agent CLI is not on PATH — vibecrafted doctor"),
)
STARTUP_HEALTHY_MARKERS: tuple[str, ...] = ('"type":"user"', '"type":"assistant"')


def _watch_launch_startup(
    payload: dict[str, Any], seconds: float | None = None
) -> None:
    """Report a worker that died in its first seconds instead of leaving the
    receipt as the last word. Silent when the worker starts working."""
    if seconds is None:
        try:
            seconds = float(os.environ.get("VIBECRAFTED_SPAWN_WATCH_SECONDS", "6"))
        except ValueError:
            seconds = 6.0
    transcript = str(payload.get("transcript") or "")
    # `accepted` may be absent OR present-but-null; only an explicit False
    # means the launch was refused and there is nothing to watch.
    if seconds <= 0 or not transcript or payload.get("accepted") is False:
        return
    agent = str(payload.get("agent") or "the agent")
    path = Path(transcript)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for marker, hint in STARTUP_FAILURE_HINTS:
            if marker in text:
                # Flush the receipt first: piped stdout is block-buffered while
                # stderr is not, so the warning would otherwise surface above
                # the receipt it is meant to qualify.
                sys.stdout.flush()
                print(
                    f"\n⚠  Worker stopped right after launch: {marker}",
                    file=sys.stderr,
                )
                print(f"   Fix: {hint.format(agent=agent)}", file=sys.stderr)
                print(f"   Transcript: {transcript}", file=sys.stderr)
                return
        if any(marker in text for marker in STARTUP_HEALTHY_MARKERS):
            return  # the worker is producing turns — leave it alone
        time.sleep(0.25)


def _launch_receipt_reasons(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("reasons") or payload.get("block_reasons")
    if isinstance(raw, str):
        reasons = [raw]
    elif isinstance(raw, (list, tuple)):
        reasons = [str(item) for item in raw if str(item).strip()]
    else:
        reasons = []
    direct = str(payload.get("reason") or "").strip()
    if direct and direct not in reasons:
        reasons.append(direct)
    failure_card = payload.get("failure_card")
    if isinstance(failure_card, dict):
        card_reason = str(
            failure_card.get("reason") or failure_card.get("message") or ""
        ).strip()
        if card_reason and card_reason not in reasons:
            reasons.append(card_reason)
    return reasons


def _print_resume_session_receipt(payload: dict[str, Any]) -> None:
    """Print the manual explicit-resume receipt, or a rejection notice to stderr."""
    if not payload.get("accepted"):
        reason = _field(payload, "reason", "launch_rejected")
        print(
            f"error: explicit session continuation rejected: {reason}", file=sys.stderr
        )
        detail = _field(payload, "detail")
        if detail:
            print(f"detail: {detail}", file=sys.stderr)
        return
    run_id = _field(payload, "run_id")
    agent = _field(payload, "agent")
    print("=============== MANUAL EXPLICIT RESUME RECEIPT ===============")
    print(f"run_id:             {run_id}")
    print(f"agent:              {agent}")
    print(f"agent_session_id:   {_field(payload, 'agent_session_id')}")
    print(f"runtime_session_id: {_field(payload, 'runtime_session_id')}")
    print(f"resume_mode:        {_field(payload, 'resume_mode')}")
    print("runtime:            headless")
    print(f"root:               {_field(payload, 'root')}")
    print(f"status:             {_field(payload, 'status', 'launching')}")
    print(f"control:            {_field(payload, 'control')}")
    print(f"transcript:         {_field(payload, 'transcript')}")
    print(f"observe:            vibecrafted observe {agent} --run-id {run_id}")
    print(f"await:              vibecrafted await {agent} --run-id {run_id}")
    print("===============================================================")


def _print_launch_input_error(*, command: str, agent: str | None, message: str) -> None:
    """Print a launch-spec validation failure with usage hints to stderr."""
    base = f"vibecrafted {command}"
    if agent:
        base = f"{base} {agent}"
    print(f"error: {message}", file=sys.stderr)
    print(file=sys.stderr)
    print("Provide work for the agent with one of:", file=sys.stderr)
    print(f"  {base} --prompt 'what to do'", file=sys.stderr)
    print(f"  {base} --file /path/to/brief.md", file=sys.stderr)


def _pid_alive(pid: object) -> bool:
    """True when a pid exists (signal-0 probe); ``PermissionError`` still counts as alive."""
    if not isinstance(pid, (str, int)):
        return False
    try:
        resolved = int(pid)
        if resolved <= 0:
            return False
        os.kill(resolved, 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True  # exists but owned by another user
    return True


def _pgid_alive(pgid: object) -> bool:
    """True when a process group exists (signal-0 probe via ``killpg``)."""
    if not isinstance(pgid, (str, int)):
        return False
    try:
        resolved = int(pgid)
        if resolved <= 0:
            return False
        os.killpg(resolved, 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True
    return True


def _apply_live_liveness(run: dict[str, Any] | None) -> dict[str, Any] | None:
    """Override stale liveness with the detached worker's real OS identity.

    A headless worker survives its short-lived dispatcher by design. Prefer its
    process group/pid and consult launcher_pid only before worker identity has
    been seeded. This avoids both false death after dispatcher exit and stale
    "active" output after the actual worker disappears.
    """
    if not run:
        return run
    if _run_terminal(run):
        return run

    worker_probes = []
    if run.get("worker_pgid") not in (None, ""):
        worker_probes.append(_pgid_alive(run.get("worker_pgid")))
    if run.get("worker_pid") not in (None, ""):
        worker_probes.append(_pid_alive(run.get("worker_pid")))
    if worker_probes:
        if any(worker_probes):
            return run
    else:
        launcher_pid = run.get("launcher_pid")
        if not launcher_pid or _pid_alive(launcher_pid):
            return run

    run = dict(run)
    run["liveness"] = "pid_gone"
    run["liveness_note"] = (
        "process proof is gone while metadata remains non-terminal; "
        "projection is not yet reconciled — inspect transcript/report before recovery"
    )
    return run


def _run_for_agent(
    agent: str, run_id: str, *, last: bool = False
) -> dict[str, Any] | None:
    """Resolve one agent's run: by explicit id, or its most recent run when ``last``."""
    # With an explicit run id the scoped, lockless lookup is the whole answer.
    # The old unconditional full sync_state() here queued every await/observe
    # behind the global board lock — during an install/doctor full sync that
    # meant ControlPlaneLockBusy on every await inside the sync window.
    if run_id:
        return _apply_live_liveness(lookup_run(run_id))
    if not last:
        return None
    snapshot = sync_state()
    for key in ("active_runs", "recent_runs"):
        for run in snapshot.get(key) or []:
            if str(run.get("agent") or "") == agent:
                return _apply_live_liveness(dict(run))
    return None


def _print_run_status(run: dict[str, Any], *, include_tail: bool = True) -> None:
    """Print the standard multi-line run status block, optionally with transcript tail."""
    state = str(run.get("state") or "")
    print(f"run_id:     {run.get('run_id') or ''}")
    print(f"state:      {state}")
    print(f"agent:      {run.get('agent') or ''}")
    print(f"skill:      {run.get('skill') or ''}")
    print(f"root:       {run.get('root') or ''}")
    print(f"liveness:   {run.get('liveness') or ''}")
    if run.get("liveness_note"):
        print(f"note:       {run.get('liveness_note')}")
    if run.get("last_error") and state not in {"completed", "report_validated"}:
        print(f"last_error: {run.get('last_error')}")
    print(f"report:     {run.get('latest_report') or run.get('report') or ''}")
    transcript = str(run.get("latest_transcript") or run.get("transcript") or "")
    print(f"transcript: {transcript}")
    if not include_tail:
        return
    tail, tail_error = _tail_lines(transcript, agent=str(run.get("agent") or ""))
    if tail:
        print("transcript_tail:")
        for line in tail:
            print(f"  {line}")
    else:
        print(f"transcript_tail: unavailable ({tail_error})")


def _print_identity_mixup(kind: str, token: str) -> None:
    """Explain a --run-id/--session mixup in operator language."""
    found = find_run_for_identity_token(token)
    found_id = str((found or {}).get("run_id") or "")
    provider = str((found or {}).get("agent_session_id") or "").strip()
    runtime = str(
        (found or {}).get("runtime_session_id")
        or (found or {}).get("vibecrafted_session_id")
        or ""
    ).strip()
    if kind == "run_id":
        print(
            f"That is a control-plane run id, not a provider session: {token}",
            file=sys.stderr,
        )
        print(
            f"  Use: vibecrafted resume <agent> --run-id {token}",
            file=sys.stderr,
        )
        return
    if kind == "provider_session":
        print(
            f"That is a provider session, not a control-plane run id: {token}",
            file=sys.stderr,
        )
        print(
            f"  Use: vibecrafted resume <agent> --session {token}",
            file=sys.stderr,
        )
        if found_id:
            print(
                f"  Or resume the tracked run: vibecrafted resume <agent> --run-id {found_id}",
                file=sys.stderr,
            )
        return
    if kind == "vibecrafted_session":
        print(
            "That is a Vibecrafted runtime session id "
            "(VIBECRAFTED_SESSION_ID), not a provider session and not a run id:",
            file=sys.stderr,
        )
        print(f"  {token}", file=sys.stderr)
        if found_id:
            print(
                f"  Use: vibecrafted resume <agent> --run-id {found_id}",
                file=sys.stderr,
            )
        if provider and provider != runtime:
            print(
                f"  Provider session: vibecrafted resume <agent> --session {provider}",
                file=sys.stderr,
            )
        return
    print(
        f"That token is not a control-plane run id: {token}",
        file=sys.stderr,
    )
    print(
        "  Control-plane run:  vibecrafted resume <agent> --run-id work-YYMMDD-HHMMSS-xxxxx",
        file=sys.stderr,
    )
    print(
        "  Provider session:   vibecrafted resume <agent> --session <provider-uuid>",
        file=sys.stderr,
    )


def _agent_resume(agent: str, argv: Sequence[str]) -> int:
    """``vibecrafted resume <agent>``: continue a stopped control-plane run."""
    parser = argparse.ArgumentParser(prog=f"vibecrafted resume {agent}")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--last", action="store_true")
    parser.add_argument("--session", default="")
    parser.add_argument("-p", "--prompt", default="")
    parser.add_argument("-f", "--file", dest="prompt_file", default="")
    parser.add_argument("--root", default="")
    parser.add_argument("--source-dir", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv))

    session = str(args.session or "").strip()
    run_id = str(args.run_id or "").strip()
    if session and not run_id:
        kind = classify_resume_identity(session)
        if kind in {"run_id", "vibecrafted_session"} or looks_like_control_plane_run_id(
            session
        ):
            _print_identity_mixup("run_id" if kind == "run_id" else kind, session)
            if kind == "run_id" or looks_like_control_plane_run_id(session):
                print(
                    f"  Hint: vibecrafted resume {agent} --run-id {session}",
                    file=sys.stderr,
                )
            return 2
        print(
            "Provider-session resume lives on the deck:",
            file=sys.stderr,
        )
        print(
            f"  vibecrafted resume {agent} --session {session}",
            file=sys.stderr,
        )
        print(
            f"Stopped-run resume: vibecrafted resume {agent} --run-id <work-...>",
            file=sys.stderr,
        )
        return 2

    if run_id:
        kind = classify_resume_identity(run_id)
        if kind in {"provider_session", "vibecrafted_session"}:
            _print_identity_mixup(kind, run_id)
            return 2
    elif args.last:
        run = _run_for_agent(agent, "", last=True)
        if run is None:
            print(
                f"No recent {agent} run to resume. Pass --run-id.",
                file=sys.stderr,
            )
            return 1
        run_id = str(run.get("run_id") or "")
    else:
        print(
            "Resume a stopped run with --run-id <work-...> or --last.",
            file=sys.stderr,
        )
        print(
            f"  vibecrafted resume {agent} --run-id <work-...>",
            file=sys.stderr,
        )
        print(
            f"  Provider session: vibecrafted resume {agent} --session <provider-uuid>",
            file=sys.stderr,
        )
        return 2

    prompt = str(args.prompt or "")
    if args.prompt_file:
        try:
            prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot read --file: {exc}", file=sys.stderr)
            return 2

    result = operator_continue_run(
        run_id,
        source_dir=args.source_dir or package_root(),
        prompt=prompt,
        expected_agent=agent,
        root=args.root,
        model=args.model,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif result.get("accepted"):
        child = str(result.get("run_id") or "")
        print("=============== OPERATOR CONTINUE RECEIPT ===============")
        print(f"resume_of:          {result.get('resume_of') or run_id}")
        print(f"run_id:             {child}")
        print(f"agent:              {result.get('agent') or agent}")
        print(f"resume_mode:        {result.get('resume_mode') or ''}")
        print(f"root:               {result.get('root') or ''}")
        if result.get("agent_session_id"):
            print(f"agent_session_id:   {result.get('agent_session_id')}")
        print(f"observe:            vibecrafted observe {agent} --run-id {child}")
        print(f"await:              vibecrafted await {agent} --run-id {child}")
        print("=========================================================")
        _watch_launch_startup(result)
    else:
        reason = str(result.get("reason") or "refused")
        print(f"error: cannot resume run {run_id}: {reason}", file=sys.stderr)
        detail = str(result.get("detail") or "").strip()
        if detail:
            print(f"detail: {detail}", file=sys.stderr)
        hint = str(result.get("hint") or "").strip()
        if hint:
            print(f"hint: {hint}", file=sys.stderr)
    return 0 if result.get("accepted") else 1


def _agent_observe(agent: str, argv: Sequence[str]) -> int:
    """``vibecrafted observe <agent>`` verb: print/emit one run's current status."""
    parser = argparse.ArgumentParser(prog=f"vibecrafted observe {agent}")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--last", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv))
    run = _run_for_agent(agent, args.run_id, last=args.last)
    if run is None:
        if args.run_id:
            # Control-plane projection missed it; resolve read-follows-write
            # against runtime_runs/ (where the runtime writes) before giving up.
            return _observe_resolved(args.run_id, json_output=args.json)
        print("No run found. Pass --run-id or --last.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _print_run_status(run)
    return 0


def _observe_resolved(run_id: str, *, json_output: bool) -> int:
    """Fallback observe path: resolve a run directly from runtime_runs/artifacts
    on disk when the control-plane projection has no record of it yet."""
    try:
        resolved = resolve_run(run_id)
    except RunNotResolved as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if json_output:
        print(
            json.dumps(
                {
                    "run_id": resolved.run_id,
                    "source": resolved.source,
                    "run_dir": str(resolved.run_dir),
                    "meta": str(resolved.meta) if resolved.meta else "",
                    "transcript": str(resolved.transcript)
                    if resolved.transcript
                    else "",
                    "report": str(resolved.report) if resolved.report else "",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    print(f"run_id:     {resolved.run_id}")
    print(f"source:     {resolved.source}")
    print(f"run_dir:    {resolved.run_dir}")
    print(f"report:     {resolved.report or ''}")
    print(f"transcript: {resolved.transcript or ''}")
    if resolved.transcript:
        tail, tail_error = _tail_lines(str(resolved.transcript))
        if tail:
            print("transcript_tail:")
            for line in tail:
                print(f"  {line}")
        else:
            print(f"transcript_tail: unavailable ({tail_error})")
    return 0


def _agent_await(agent: str, argv: Sequence[str]) -> int:
    """``vibecrafted await <agent>`` verb: block on control_plane.await_run and
    print/emit the terminal outcome. Never implement a private polling loop here."""
    # ONE await loop lives in control_plane.await_run — this verb must never
    # grow a private wall-clock loop again. The old inline loop here treated
    # --timeout as an absolute deadline and abandoned demonstrably-working
    # runs at 300s, which taught supervising agents to distrust await and
    # hedge with manual sleep/ps monitors.
    parser = argparse.ArgumentParser(prog=f"vibecrafted await {agent}")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--last", action="store_true")
    parser.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="idle window in seconds — resets on movement or a live worker",
    )
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--status-interval", type=float, default=60)
    parser.add_argument(
        "--stale-after",
        type=float,
        default=600,
        help="deprecated: superseded by the liveness-aware idle window",
    )
    parser.add_argument("--hard-cap", type=float, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv))
    run = _run_for_agent(agent, args.run_id, last=args.last)
    if run is None:
        print("No run found. Pass --run-id or --last.", file=sys.stderr)
        return 1
    run_id = str(run.get("run_id") or "")
    if args.json:
        result = await_launch_truth(
            run_id,
            timeout_seconds=args.timeout,
            interval_seconds=args.interval,
            hard_cap_seconds=args.hard_cap,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return (
            0
            if result.get("completed")
            and result.get("artifact_ok")
            and result.get("terminal_evidence")
            else 1
        )

    print("await: initial status")
    _print_run_status(run)

    interval = max(float(args.interval), 0.1)
    status_interval = max(float(args.status_interval), interval)
    next_status = {"at": time.monotonic() + status_interval}

    def _print_progress(current: dict[str, Any] | None) -> None:
        """on_poll callback for await_run: print a status line every status_interval."""
        now = time.monotonic()
        if current is not None and now >= next_status["at"]:
            print("await: still running")
            _print_run_status(current)
            next_status["at"] = now + status_interval

    result = await_run(
        run_id,
        timeout_seconds=args.timeout,
        interval_seconds=interval,
        hard_cap_seconds=args.hard_cap,
        on_poll=_print_progress,
    )
    final_run = dict(result.get("run") or {})
    reason = str(result.get("reason") or "")
    if result.get("completed"):
        worker_alive = bool(result.get("worker_alive"))
        terminal_evidence = bool(
            final_run and _run_terminal(final_run) and not worker_alive
        )
        delivered_evidence = reason == "report_delivered" and not worker_alive
        if terminal_evidence and final_run and not _run_succeeded(final_run):
            print(f"await: terminal failure ({reason})")
            _print_run_status(final_run)
            return 1
        if not (terminal_evidence or delivered_evidence):
            print(
                f"await: non-terminal completion disagreement ({reason})",
                file=sys.stderr,
            )
            if final_run:
                _print_run_status(final_run)
            return 3
        print(f"await: completed ({reason})")
        if final_run:
            _print_run_status(final_run)
        return 0
    if not result.get("found"):
        print(f"await: run disappeared: {run_id}", file=sys.stderr)
        return 1
    print(f"await: timed out ({reason})")
    if final_run:
        _print_run_status(final_run)
    return 1


def _cmd_resettle(args: argparse.Namespace) -> int:
    """Honest re-settlement of retained control_plane/runs snapshots."""
    from .lifecycle_delivery import resettle_retained_snapshots

    result = resettle_retained_snapshots(
        force=True,
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    before = result.get("before") or {}
    after = result.get("after") or {}
    print(
        "resettle "
        f"scanned={result.get('scanned', 0)} "
        f"rewritten={result.get('rewritten', 0)} "
        f"unchanged={result.get('unchanged', 0)} "
        f"skipped={result.get('skipped', 0)}"
        + (" (dry-run)" if result.get("dry_run") else "")
    )
    print(
        f"before: f={before.get('f', 0)} x={before.get('x', 0)} "
        f"n={before.get('n', 0)} invalid={before.get('invalid', 0)}"
    )
    print(
        f"after:  f={after.get('f', 0)} x={after.get('x', 0)} "
        f"n={after.get('n', 0)} invalid={after.get('invalid', 0)}"
    )
    print(
        "note: automatic FINALIZED comes only from a sealed delivery or an "
        "explicit worker attestation (finalized: true + claim); a traced "
        "operator waive remains an explicit override; never from bare exit 0"
    )
    return 0 if result.get("ok") else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Top-level ``vibecrafted`` entrypoint: dispatches to python subcommands,
    the legacy bash deck, or the acp/dispatch/stop/observe/await verbs based
    on the invoked name and leading argv token."""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    invoked_as = Path(sys.argv[0]).name if argv is None else "vibecrafted"
    shell_wrapper_verb = SHELL_WRAPPER_VERBS.get(invoked_as) if argv is None else None
    if shell_wrapper_verb:
        raw_args = [shell_wrapper_verb, *raw_args]
    raw_args = _normalize_raw_args(raw_args)

    # `--version` / `-v` / `version` report the INSTALLED runtime version — the
    # one `vibecrafted start` / `vc-start` actually runs — read straight from the
    # package. Never delegate to the legacy bash deck: its `_version()` resolves
    # VERSION from the current working directory (`repo_root/VERSION`), so invoked
    # from inside a checkout it reports that checkout's version, not the installed
    # one. The package is what executes, so its `__version__` is the honest answer.
    if raw_args and raw_args[0] in {"-v", "--version", "version"}:
        from . import __version__

        print(f"vibecrafted {__version__}")
        return 0

    # Help is a product surface, not argparse fallout.  Resolve it before the
    # shell-deck compatibility router or any workflow runtime is imported so
    # every installed entrypoint teaches the same contract.  ``help --all``
    # deliberately stays with the deck: it is the long operational reference.
    from . import __version__

    help_version = os.environ.get("VIBECRAFTED_HELP_VERSION", "").strip() or __version__
    from .help_surface import (
        has_workflow_help,
        render_resume_session_help,
        render_root_help,
        render_workflow_help,
    )

    if not raw_args or raw_args[0] in {"-h", "--help"}:
        print(render_root_help(help_version), end="")
        return 0
    if raw_args[0] == "help":
        if len(raw_args) == 1:
            print(render_root_help(help_version), end="")
            return 0
        topic = raw_args[1].removeprefix("vc-")
        if topic == "resume-session":
            print(render_resume_session_help(), end="")
            return 0
        if topic not in {"--all", "--full"} and has_workflow_help(topic):
            print(render_workflow_help(topic), end="")
            return 0
    if raw_args[0] == "resume-session" and any(
        arg in {"-h", "--help"} for arg in raw_args[1:]
    ):
        print(render_resume_session_help(), end="")
        return 0
    if raw_args[0] in LAUNCHERS:
        workflow_args = raw_args[1:]
        help_requested = (
            bool(workflow_args)
            and workflow_args[0] == "help"
            or any(arg in {"-h", "--help"} for arg in workflow_args)
        )
        if help_requested:
            print(render_workflow_help(raw_args[0]), end="")
            return 0

    try:
        raw_args = _normalize_research_arity_args(raw_args)
    except ValueError as exc:
        print(f"vibecrafted research: {exc}", file=sys.stderr)
        return 2

    python_commands = {
        "acp",
        "capabilities",
        "config",
        "dispatch",
        "doctor",
        "paste",
        "procs",
        "reap",
        "receipt",
        "resume-session",
        "settle",
        "settlements",
        "stop",
    } | set(LAUNCHERS)
    agent_python_verbs = {"observe", "await", "stop", "resume"}
    is_lifecycle = shell_wrapper_verb is not None
    if raw_args and shell_wrapper_verb is None:
        first = raw_args[0]
        second = raw_args[1] if len(raw_args) > 1 else ""
        if first in AGENTS and second in agent_python_verbs:
            # Core owns agent observe/await/stop/resume (read-follows-write via
            # resolve_run); never delegate these to the legacy deck/observe.sh.
            is_lifecycle = False
        elif first not in python_commands and not first.startswith("-"):
            is_lifecycle = True

    if is_lifecycle:
        from .runtime_paths import vibecrafted_tools_home

        tools_home = vibecrafted_tools_home()
        deck = tools_home / "vibecrafted-current" / "scripts" / "vibecrafted"
        if not deck.is_file():
            deck = deck_path()
        if deck.is_file():
            try:
                lease_pass_fds = _installer_lease_pass_fds(tools_home)
            except OSError as exc:
                print(
                    f"error: cannot preserve installer coordination lease: {exc}",
                    file=sys.stderr,
                )
                return _EX_TEMPFAIL
            if lease_pass_fds:
                res = subprocess.run(
                    [str(deck), *raw_args],
                    check=False,
                    pass_fds=lease_pass_fds,
                )
            else:
                res = subprocess.run([str(deck), *raw_args], check=False)
            return res.returncode
        if shell_wrapper_verb is not None:
            print(
                f"error: {invoked_as} cannot find the runtime deck at {deck}",
                file=sys.stderr,
            )
            return 1

    if raw_args and raw_args[0] == "acp":
        try:
            from vibecrafted_acp.server import main as acp_main
        except ModuleNotFoundError:
            print(
                "error: vibecrafted-acp is not installed; install the "
                "vibecrafted-acp workspace package",
                file=sys.stderr,
            )
            return 1
        return acp_main(raw_args[1:])

    if raw_args and raw_args[0] == "dispatch":
        from .dispatch.cli import main as dispatch_main

        return dispatch_main(raw_args[1:])
    if raw_args and raw_args[0] == "stop":
        from .wrappers import stop_main

        return stop_main(raw_args[1:])
    if len(raw_args) >= 2 and raw_args[0] in AGENTS and raw_args[1] == "stop":
        from .wrappers import stop_main

        return stop_main(["--agent", raw_args[0], *raw_args[2:]])
    if len(raw_args) >= 2 and raw_args[0] in AGENTS and raw_args[1] == "resume":
        return _agent_resume(raw_args[0], raw_args[2:])
    if len(raw_args) >= 2 and raw_args[0] in AGENTS and raw_args[1] == "observe":
        return _agent_observe(raw_args[0], raw_args[2:])
    if len(raw_args) >= 2 and raw_args[0] in AGENTS and raw_args[1] == "await":
        return _agent_await(raw_args[0], raw_args[2:])

    parser = _build_parser()
    args = parser.parse_args(raw_args)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "config":
        from .vc_frame_delivery import ensure_zshrc, stage_vc_frame_config

        action = getattr(args, "config_action", None)
        if action == "ensure-zshrc":
            result = ensure_zshrc(dry_run=bool(getattr(args, "dry_run", False)))
            print(f"ensure-zshrc: {result['action']} -> {result['path']}")
            return 0
        if action != "install":
            print(
                "usage: vibecrafted config install [--dry-run] [--force] [--prefer-repo]\n"
                "       vibecrafted config ensure-zshrc [--dry-run]",
                file=sys.stderr,
            )
            return 2
        plan = stage_vc_frame_config(
            dry_run=bool(getattr(args, "dry_run", False)),
            force=bool(getattr(args, "force", False)),
            prefer_repo=True if getattr(args, "prefer_repo", False) else None,
        )
        print(plan.render(), end="")
        return 0
    if args.command == "receipt":
        from .runtime_receipt import receipt_main

        return receipt_main(["--json"] if args.json else [])
    if args.command == "doctor":
        if getattr(args, "quarantine_legacy_runs", False):
            from .run_reaper import quarantine_legacy_runs

            quarantine = quarantine_legacy_runs()
            payload = quarantine.as_dict()
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(
                    "quarantine-legacy-runs: "
                    f"changed={payload['changed']} "
                    f"marked_legacy={len(payload['marked_legacy'])} "
                    f"recovered_pgid={len(payload['recovered_pgid'])} "
                    f"skipped_live={len(payload['skipped_live'])} "
                    f"skipped_has_pgid={len(payload['skipped_has_pgid'])} "
                    f"already_legacy={len(payload['already_legacy'])} "
                    f"parse_errors={len(payload['parse_errors'])}"
                )
                for run_id in payload["marked_legacy"]:
                    print(f"  legacy: {run_id}")
                for row in payload["recovered_pgid"]:
                    print(
                        f"  recovered: {row.get('run_id')} "
                        f"worker_pgid={row.get('worker_pgid')}"
                    )
                for err in payload["parse_errors"]:
                    print(f"  parse_error: {err}")
            return 0
        findings = doctor_module.doctor_run(
            release=bool(getattr(args, "release", False))
        )
        summary = doctor_module.doctor_summary(findings)
        from .runtime_receipt import build_receipt, render_receipt_text

        delivery_receipt = build_receipt()
        summary["delivery_receipt"] = delivery_receipt
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            for finding in summary["findings"]:
                print(
                    f"{finding['level']}: {finding['component']} - {finding['message']}"
                )
            print(
                f"summary: {summary['ok']} ok, {summary['warnings']} warnings, "
                f"{summary['failures']} failures"
            )
            print()
            print(render_receipt_text(delivery_receipt), end="")
        return 0 if summary["failures"] == 0 else 1
    if args.command == "reap":
        if getattr(args, "resettle", False):
            return _cmd_resettle(args)
        from .run_reaper import main as reap_main

        reap_argv: list[str] = []
        if args.dry_run:
            reap_argv.append("--dry-run")
        if args.json:
            reap_argv.append("--json")
        return reap_main(reap_argv)
    if args.command == "settle":
        if not getattr(args, "resettle", False):
            print(
                "usage: vibecrafted settle --resettle [--dry-run] [--json]",
                file=sys.stderr,
            )
            return 2
        return _cmd_resettle(args)
    if args.command == "workspace":
        from .workspace_catalog import workspace_cli_main

        argv = list(getattr(args, "workspace_argv", []) or [])
        # argparse REMAINDER may keep a leading "--".
        if argv and argv[0] == "--":
            argv = argv[1:]
        return workspace_cli_main(argv)
    if args.command == "settlements":
        from .settlements_query import (
            SettlementsQueryError,
            inspect_settlement,
            list_settlements,
            render_settlements_inspect_text,
            render_settlements_list_text,
            render_settlements_summary_text,
            settlements_summary,
        )

        action = getattr(args, "settlements_action", None)
        if action is None:
            print(
                "usage: vibecrafted settlements summary [--json] [--workspace-id UUID]\n"
                "       vibecrafted settlements list "
                "[--bucket f|x|n] [--revalidatable] "
                "[--group agent,skill,reason,root] [--limit N] [--json]\n"
                "       vibecrafted settlements inspect <run_id> [--json]",
                file=sys.stderr,
            )
            return 2
        try:
            if action == "summary":
                workspace_id = str(getattr(args, "workspace_id", "") or "").strip()
                if workspace_id:
                    from .workspace_catalog import settlement_counts_for_workspace

                    payload = settlement_counts_for_workspace(workspace_id)
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    return 0
                payload = settlements_summary()
                if getattr(args, "json", False):
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                else:
                    print(render_settlements_summary_text(payload))
                return 0
            if action == "list":
                payload = list_settlements(
                    bucket=getattr(args, "bucket", None),
                    revalidatable=bool(getattr(args, "revalidatable", False)),
                    group=getattr(args, "group", None) or None,
                    limit=getattr(args, "limit", None),
                )
                if getattr(args, "json", False):
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                else:
                    print(render_settlements_list_text(payload))
                return 0
            if action == "inspect":
                payload = inspect_settlement(str(args.run_id))
                if getattr(args, "json", False):
                    print(
                        json.dumps(payload, ensure_ascii=False, indent=2, default=str)
                    )
                else:
                    print(render_settlements_inspect_text(payload))
                return 0
        except SettlementsQueryError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(
            "usage: vibecrafted settlements {summary|list|inspect}",
            file=sys.stderr,
        )
        return 2
    if args.command == "procs":
        from .process_control import main as procs_main

        action = getattr(args, "procs_action", None) or "snapshot"
        if action == "snapshot":
            return procs_main(["snapshot", "--json"])
        if action == "terminate":
            return procs_main(
                [
                    "terminate",
                    "--pid",
                    str(args.pid),
                    "--expected-start",
                    args.expected_start,
                    "--expected-command-sha256",
                    args.expected_command_sha256,
                    "--expected-run-id",
                    args.expected_run_id or "",
                    "--json",
                ]
            )
        print("usage: vibecrafted procs {snapshot|terminate}", file=sys.stderr)
        return 2
    if args.command == "capabilities":
        from .workflow_capabilities import (
            render_capabilities_lines,
            workflow_capabilities_payload,
        )

        capabilities_payload = workflow_capabilities_payload()
        if args.json:
            print(
                json.dumps(
                    capabilities_payload, ensure_ascii=False, indent=2, sort_keys=True
                )
            )
        else:
            for line in render_capabilities_lines(capabilities_payload):
                print(line)
        return 0
    if args.command == "resume-session":
        prompt = str(args.prompt or "")
        if args.prompt_stdin:
            prompt = sys.stdin.read()
        elif args.prompt_file:
            prompt_path = Path(args.prompt_file).expanduser()
            try:
                prompt = prompt_path.read_text(encoding="utf-8")
            except OSError as exc:
                resume_result: dict[str, Any] = {
                    "schema": "vibecrafted.manual_explicit_resume.v1",
                    "accepted": False,
                    "reason": "prompt_file_unreadable",
                    "retryable": False,
                    "terminal": True,
                    "resume_mode": "manual_explicit",
                    "agent": args.agent,
                    "agent_session_id": args.agent_session_id,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
                if args.json:
                    print(json.dumps(resume_result, ensure_ascii=False, indent=2))
                else:
                    _print_resume_session_receipt(resume_result)
                return 2
        resume_root = args.root or resolve_operator_launch_root()
        if is_operator_home_root(resume_root):
            print(
                "error: refusing to launch against the home directory; "
                "open a workspace in Vibecrafted or pass --root",
                file=sys.stderr,
            )
            return 2
        resume_result = manual_resume_session(
            args.agent,
            args.agent_session_id,
            args.source_dir or package_root(),
            prompt=prompt,
            root=resume_root,
            model=args.model,
        )
        if args.json:
            print(json.dumps(resume_result, ensure_ascii=False, indent=2))
        else:
            _print_resume_session_receipt(resume_result)
        return 0 if resume_result.get("accepted") else 1
    if args.command == "paste":
        from .paste import run_namespace

        return run_namespace(args, source_dir=package_root())

    source_dir = args.source_dir or package_root()
    prompt = str(args.prompt or "")
    if args.prompt_stdin:
        if prompt or args.file:
            parser.error("--prompt-stdin cannot be combined with --prompt or --file")
        prompt = sys.stdin.read()
    agent_arg = args.agent
    research_agents = ()
    if args.command == "research" and isinstance(agent_arg, list):
        research_agents = tuple(agent_arg) if len(agent_arg) > 1 else ()
    launch_root = args.root or str(resolve_operator_launch_root())
    if is_operator_home_root(launch_root):
        print(
            "error: refusing to launch against the home directory; "
            "open a workspace in Vibecrafted or pass --root",
            file=sys.stderr,
        )
        return 2
    payload = {
        "skill": LAUNCH_ALIASES.get(args.command, args.command),
        "agent": args.agent,
        "prompt": prompt,
        "file": args.file,
        "runtime": _default_runtime(args.runtime, launch_root),
        "root": launch_root,
        "mode": args.mode or args.command,
        "count": args.count,
        "depth": args.depth,
        "model": args.model,
        "research_agents": research_agents,
        "synthesizer": getattr(args, "synthesizer", ""),
        "synthesizer_model": getattr(args, "synthesizer_model", ""),
    }
    try:
        spec = normalize_launch_spec(payload, source_dir)
    except ValueError as exc:
        _print_launch_input_error(
            command=str(args.command), agent=args.agent, message=str(exc)
        )
        return 2
    result = launch_workflow(spec, source_dir)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_launch_receipt(result)
        _watch_launch_startup(result)
    return 0 if result.get("accepted") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
