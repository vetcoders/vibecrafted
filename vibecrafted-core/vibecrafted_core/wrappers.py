"""CLI entrypoint wrappers: supervised-skill dispatch, lifecycle launch, resume/stop."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import control_plane
from .events import append_event
from .package_resources import deck_path, package_root, runtime_path
from .spawn import Supervisor

AGENTS = {"claude", "codex", "agy", "junie", "grok"}
SUCCESS_STATES = {"report_validated", "completed", "closed"}
SKILL_PREFIX = {
    "agents": "agnt",
    "followup": "fwup",
    "implement": "just",
    "marbles": "marb",
    "prune": "prun",
    "review": "rvew",
    "scaffold": "scaf",
}


def invocation_root() -> Path:
    """The directory the CLI was invoked from — the target repo as the operator sees it.

    Not ``loop.repo_root`` (git toplevel); the two answer different questions and
    carried the same name until 2026-08-23."""
    return Path.cwd()


def runtime_root() -> Path:
    return runtime_path()


def _print_workflow_help(workflow_id: str) -> int:
    """Print rendered help text for a workflow and return the CLI exit code 0."""
    from .help_surface import render_workflow_help

    print(render_workflow_help(workflow_id), end="")
    return 0


def _has_flag(args: Sequence[str], name: str) -> bool:
    """True if `name` appears bare or as `name=value` among `args`."""
    return name in args or any(arg.startswith(f"{name}=") for arg in args)


def _help_requested(args: Sequence[str]) -> bool:
    """True when args ask for help: leading `help`, or `-h`/`--help` anywhere."""
    return (
        bool(args) and args[0] == "help" or any(arg in {"-h", "--help"} for arg in args)
    )


def _consume_sandbox_flags(args: Sequence[str]) -> tuple[list[str], bool, str | None]:
    """Strip `--sandbox`/`--sandbox-policy` from args, returning (rest, sandbox, policy)."""
    cleaned: list[str] = []
    sandbox = False
    policy: str | None = None
    iterator = iter(args)
    for arg in iterator:
        if arg == "--sandbox":
            sandbox = True
            continue
        if arg == "--sandbox-policy":
            policy = next(iterator, None)
            continue
        if arg.startswith("--sandbox-policy="):
            policy = arg.split("=", 1)[1]
            continue
        cleaned.append(arg)
    return cleaned, sandbox, policy


def _run_id(prefix: str) -> str:
    """Generate a run id: `<prefix>-<HHMMSS>-<pid>`. Not guaranteed globally unique."""
    return f"{prefix}-{time.strftime('%H%M%S')}-{os.getpid()}"


def _env_for_run(run_id: str, skill_code: str) -> dict[str, str]:
    """Base child-process env: run id, skill code, root/python defaults, PYTHONPATH."""
    env = os.environ.copy()
    env["VIBECRAFTED_RUN_ID"] = run_id
    env["VIBECRAFTED_SKILL_CODE"] = skill_code
    env.setdefault("VIBECRAFTED_ROOT", str(runtime_root()))
    env.setdefault("VIBECRAFTED_PYTHON", sys.executable)
    env.setdefault("VETCODERS_SPAWN_RUNTIME", "headless")
    core_path = str(package_root().parent)
    env["PYTHONPATH"] = f"{core_path}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(
        os.pathsep
    )
    return env


def _dispatcher_command(
    run_id: str,
    root: Path,
    worker_command: Sequence[str],
) -> list[str]:
    """Build the argv for launching `vibecrafted_core.dispatcher run` around a worker."""
    return [
        sys.executable,
        "-m",
        "vibecrafted_core.dispatcher",
        "run",
        "--run-id",
        run_id,
        "--root",
        str(root),
        "--no-require-report",
        "--quiet",
        "--",
        *worker_command,
    ]


def _env_for_dispatcher(run_id: str, skill_code: str, agent: str) -> dict[str, str]:
    """`_env_for_run` plus the `VIBECRAFTED_AGENT` the dispatched worker runs as."""
    env = _env_for_run(run_id, skill_code)
    env["VIBECRAFTED_AGENT"] = agent
    return env


def _call_dispatcher(
    *,
    run_id: str,
    skill_code: str,
    agent: str,
    root: Path,
    worker_command: Sequence[str],
) -> int:
    """Run the dispatcher synchronously (blocking) and return its exit code."""
    return subprocess.call(
        _dispatcher_command(run_id, root, worker_command),
        cwd=str(root),
        env=_env_for_dispatcher(run_id, skill_code, agent),
    )


def _popen_dispatcher(
    *,
    run_id: str,
    skill_code: str,
    agent: str,
    root: Path,
    worker_command: Sequence[str],
) -> subprocess.Popen[bytes]:
    """Launch the dispatcher non-blocking (for parallel research-lane fan-out)."""
    return subprocess.Popen(
        _dispatcher_command(run_id, root, worker_command),
        cwd=str(root),
        env=_env_for_dispatcher(run_id, skill_code, agent),
    )


def _print_completed(run_id: str, payload: dict[str, Any]) -> int:
    """Print a completed run's summary and derive the CLI's own exit code from it.

    Distinguishes a clean terminal success from a "report delivered but worker
    still alive" state and from an unresolved non-terminal disagreement (exit 3).
    """
    run = payload.get("run") or {}
    if run:
        print(
            f"run_id={run_id} status={run.get('state')} exit_code={run.get('exit_code')}"
        )
        if run.get("latest_report"):
            print(f"report={run['latest_report']}")
        if run.get("latest_transcript"):
            print(f"transcript={run['latest_transcript']}")
        if run.get("session_id"):
            print(f"session_id={run['session_id']}")
        state = str(run.get("state") or "")
        errors = [str(item) for item in (run.get("artifact_errors") or []) if str(item)]
        worker_alive = bool(payload.get("worker_alive"))
        delivered = str(payload.get("reason") or "") == "report_delivered"
        terminal = control_plane._run_is_terminal(run) and not worker_alive
        succeeded = (
            state in SUCCESS_STATES
            and run.get("artifact_ok") is not False
            and not errors
        )
        if terminal and succeeded:
            return int(run.get("exit_code") or 0)
        if delivered and not worker_alive:
            exit_code = int(run.get("exit_code") or 0)
            if run.get("artifact_ok") is False or errors:
                return exit_code or 3
            return exit_code
        print(
            "run_id="
            f"{run_id} non-terminal completion disagreement "
            f"reason={payload.get('reason')}",
            file=sys.stderr,
        )
        return 3
    print(
        f"run_id={run_id} completed without control-plane payload",
        file=sys.stderr,
    )
    return 3


def _await_run_forever(run_id: str, interval: float = 5.0) -> dict[str, Any]:
    """Poll the control plane until a run completes, printing a heartbeat each poll."""
    while True:
        payload = control_plane.await_run(
            run_id,
            timeout_seconds=interval,
            interval_seconds=max(min(interval, 1.0), 0.1),
        )
        if payload.get("completed"):
            return payload
        print(f"waiting run_id={run_id}", flush=True)


def supervised_skill_main(skill: str, argv: Sequence[str] | None = None) -> int:
    """Shared CLI entry for supervised single-agent skills: parses agent/flags,
    dispatches (raw sandboxed command or `cli.py` launch), then awaits and prints
    the terminal result. Backs every `vibecrafted <skill>` wrapper that is not a
    lifecycle-manifest stage.
    """
    args, sandbox, sandbox_policy = _consume_sandbox_flags(
        list(sys.argv[1:] if argv is None else argv)
    )
    if _help_requested(args):
        return _print_workflow_help(skill)
    if sandbox and args and args[0] not in AGENTS:
        skill_code = SKILL_PREFIX.get(skill, skill[:4])
        run_id = os.environ.get("VIBECRAFTED_RUN_ID") or _run_id(skill_code)
        handle = Supervisor().spawn(
            "command",
            " ".join(args),
            skill=skill,
            mode="raw",
            root=invocation_root(),
            command=args,
            env=_env_for_run(run_id, skill_code),
            run_id=run_id,
            sandbox=True,
            sandbox_policy=sandbox_policy,
        )
        return handle.wait()
    if not args or args[0] not in AGENTS:
        print(
            f"Usage: vc-{skill} <claude|codex|agy|junie|grok> [--prompt <text>|--file <path>]",
            file=sys.stderr,
        )
        return 2

    agent = args[0]
    rest = args[1:]
    skill_code = SKILL_PREFIX.get(skill, skill[:4])
    run_id = os.environ.get("VIBECRAFTED_RUN_ID") or _run_id(skill_code)
    # Use direct -m vibecrafted_core.cli (the python path that owns --file/--prompt via _add_launch_parser)
    # instead of deck bash script. This retires the deck delegation for the launch surface (the siódemka
    # + other supervised) so flags never land in legacy positional <mode> parser.
    command = [sys.executable, "-m", "vibecrafted_core.cli", skill, agent, *rest]
    if not _has_flag(rest, "--runtime"):
        command.extend(["--runtime", "headless"])

    root = invocation_root()
    if sandbox:
        handle = Supervisor().spawn(
            agent,
            " ".join(rest),
            skill=skill,
            mode="microsandbox",
            root=root,
            command=command,
            env=_env_for_dispatcher(run_id, skill_code, agent),
            run_id=run_id,
            sandbox=True,
            sandbox_policy=sandbox_policy,
        )
        launch_code = handle.wait()
    else:
        launch_code = _call_dispatcher(
            run_id=run_id,
            skill_code=skill_code,
            agent=agent,
            root=root,
            worker_command=command,
        )
    if launch_code != 0:
        return launch_code
    payload = _await_run_forever(run_id)
    return _print_completed(run_id, payload)


def agents_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted agents`."""
    return supervised_skill_main("agents", argv)


def followup_main(argv: Sequence[str] | None = None) -> int:
    # One path with `vibecrafted followup` (lifecycle stages live under `ship`).
    """CLI entry for `vibecrafted followup`."""
    return supervised_skill_main("followup", argv)


def implement_main(argv: Sequence[str] | None = None) -> int:
    # One path with `vibecrafted implement` / shell `vc-implement`.
    """CLI entry for `vibecrafted implement`."""
    return supervised_skill_main("implement", argv)


def _lifecycle_main(workflow_id: str, argv: Sequence[str] | None = None) -> int:
    """Shared CLI entry for lifecycle-manifest workflows: help short-circuit,
    then delegate to `lifecycle_runner.lifecycle_main`.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if _help_requested(args):
        return _print_workflow_help(workflow_id)

    from .lifecycle_runner import lifecycle_main

    return lifecycle_main(workflow_id, args)


def audit_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted audit` (lifecycle manifest `vc-audit`)."""
    return _lifecycle_main("vc-audit", argv)


def dou_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted dou` (lifecycle manifest `vc-dou`)."""
    return _lifecycle_main("vc-dou", argv)


def hydrate_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted hydrate` (lifecycle manifest `vc-hydrate`)."""
    return _lifecycle_main("vc-hydrate", argv)


def marbles_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted marbles`: control subcommands (pause/stop/resume/
    session/inspect/delete/gc) route to the legacy deck script; everything else
    runs the `vc-marbles` lifecycle manifest.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {
        "pause",
        "stop",
        "resume",
        "session",
        "inspect",
        "delete",
        "gc",
    }:
        return subprocess.call([str(deck_path()), "marbles", *args])
    return _lifecycle_main("vc-marbles", args)


def polarize_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted polarize` (lifecycle manifest `vc-polarize`)."""
    return _lifecycle_main("vc-polarize", argv)


def prune_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted prune`."""
    return supervised_skill_main("prune", argv)


def review_main(argv: Sequence[str] | None = None) -> int:
    # One path with `vibecrafted review` (lifecycle stages live under `ship`).
    """CLI entry for `vibecrafted review`."""
    return supervised_skill_main("review", argv)


def scaffold_main(argv: Sequence[str] | None = None) -> int:
    # One launch authority with `vibecrafted scaffold` / shell `vc-scaffold`.
    # Lifecycle-stage flags used to diverge here (second CLI brain); skill
    # delivery is the cli + dispatcher path. Use `vibecrafted ship` for staged
    # lifecycle orchestration, not a private second scaffold parser.
    """CLI entry for `vibecrafted scaffold`."""
    return supervised_skill_main("scaffold", argv)


def decorate_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted decorate`."""
    return supervised_skill_main("decorate", argv)


def delegate_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted delegate`."""
    return supervised_skill_main("delegate", argv)


def intents_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted intents`."""
    return supervised_skill_main("intents", argv)


def ownership_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted ownership`."""
    return supervised_skill_main("ownership", argv)


def partner_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted partner`."""
    return supervised_skill_main("partner", argv)


def release_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted release` (lifecycle manifest `vc-release`)."""
    return _lifecycle_main("vc-release", argv)


def workflow_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted workflow` (lifecycle manifest `vc-workflow`)."""
    return _lifecycle_main("vc-workflow", argv)


def trust_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted trust` / shell `vc-trust`."""
    return supervised_skill_main("trust", argv)


def guard_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted guard` / shell `vc-guard`."""
    return supervised_skill_main("guard", argv)


def _prepare_research(args: Sequence[str], run_id: str) -> tuple[int, str]:
    """Run the deck's research-preparation step and capture its combined output.

    Preparation announces per-agent launcher script paths on stdout, which
    `_launcher_paths` later parses; it does not itself spawn the swarm.
    """
    command = [str(deck_path()), "research", *args]
    if not _has_flag(args, "--runtime"):
        command.extend(["--runtime", "headless"])
    proc = subprocess.run(
        command,
        cwd=str(invocation_root()),
        env=_env_for_run(run_id, "rsch"),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(proc.stdout, end="")
    return proc.returncode, proc.stdout


def _launcher_paths(output: str) -> dict[str, Path]:
    # Must recognise every supported agent: the default swarm is configurable
    # (claude+codex+junie today) and uno mode can pick any single agent.
    """Parse `<agent>: <path>.sh` lines from research-prep output into a dict."""
    launchers: dict[str, Path] = {}
    agent_alternation = "|".join(sorted(AGENTS))
    pattern = re.compile(rf"\s*({agent_alternation}):\s+(.+\.sh)\s*$")
    for line in output.splitlines():
        match = pattern.match(line)
        if match:
            launchers[match.group(1)] = Path(match.group(2)).expanduser()
    return launchers


def research_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted research`: prepares per-agent launcher scripts
    then spawns exactly the agents that were actually prepared (not a fixed
    six-agent set) under the dispatcher or microsandbox, and waits for all of them.
    """
    args, sandbox, sandbox_policy = _consume_sandbox_flags(
        list(sys.argv[1:] if argv is None else argv)
    )
    if _help_requested(args):
        return _print_workflow_help("research")
    run_id = os.environ.get("VIBECRAFTED_RUN_ID") or _run_id("rsch")
    code, output = _prepare_research(args, run_id)
    if code != 0:
        return code
    launchers = _launcher_paths(output)
    if not launchers:
        # The old check demanded launchers for ALL six agents while the swarm
        # prepares only the configured ones (three by default) — vc-research
        # could never start. The honest contract: at least one prepared
        # launcher, spawn exactly what was prepared.
        print(
            "vc-research: research preparation announced no launcher paths; "
            "cannot spawn the swarm.",
            file=sys.stderr,
        )
        return 1

    root = invocation_root()
    if sandbox:
        supervisor = Supervisor()
        handles = [
            supervisor.spawn(
                agent,
                str(path),
                skill="research",
                mode="microsandbox",
                root=root,
                command=["bash", str(path)],
                env=_env_for_dispatcher(run_id, "rsch", agent),
                run_id=run_id,
                sandbox=True,
                sandbox_policy=sandbox_policy,
            )
            for agent, path in sorted(launchers.items())
        ]
        exit_codes = [handle.wait() for handle in handles]
    else:
        processes = [
            _popen_dispatcher(
                run_id=run_id,
                skill_code="rsch",
                agent=agent,
                root=root,
                worker_command=["bash", str(path)],
            )
            for agent, path in sorted(launchers.items())
        ]
        exit_codes = [process.wait() for process in processes]
    append_event(
        "research-finished",
        run_id,
        "research swarm finished",
        {"exit_codes": dict(zip(sorted(launchers), exit_codes))},
    )
    return 0 if all(code == 0 for code in exit_codes) else 1


def research_await_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted research-await`: shells out to `scripts/await.sh`
    in research mode.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    script = runtime_root() / "scripts" / "await.sh"
    return subprocess.call(
        ["bash", str(script), "--research", *args], cwd=str(Path.cwd())
    )


def _load_meta_files(run_id: str) -> list[dict[str, Any]]:
    """Load every research lane's `*.meta.json` for a run id, tagging each with
    its source path under `_meta_path`. Malformed/unreadable files are skipped.
    """
    home = control_plane.vibecrafted_home()
    metas: list[dict[str, Any]] = []
    for path in home.glob(f"artifacts/**/research/{run_id}/**/*.meta.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload["_meta_path"] = str(path)
        metas.append(payload)
    return metas


def research_synthesize_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry that spawns last-finisher synthesis for a research run once at
    least 3 lane metas exist; refuses (exit 1) below that quorum.
    """
    parser = argparse.ArgumentParser(
        description="Spawn last-finisher synthesis for a research run."
    )
    parser.add_argument("--run-id", required=True)
    ns = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    metas = _load_meta_files(ns.run_id)
    if len(metas) < 3:
        append_event(
            "synthesize-skipped",
            ns.run_id,
            "not enough research metas for synthesis",
            {"count": len(metas)},
        )
        return 1
    last = max(
        metas,
        key=lambda item: str(item.get("completed_at") or item.get("updated_at") or ""),
    )
    agent = str(last.get("agent") or "codex")
    reports = [str(item.get("report") or "") for item in metas if item.get("report")]
    prompt = "Synthesize the completed research swarm.\n\nReports:\n" + "\n".join(
        f"- {p}" for p in reports
    )
    append_event(
        "synthesize-trigger",
        ns.run_id,
        "last-finisher synthesis triggered",
        {"agent": agent, "reports": reports},
    )
    return supervised_skill_main("implement", [agent, "--prompt", prompt])


def resume_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted resume`: dispatches a tracked provider-native
    resume via `workflow.native_resume_run` and prints/returns its verdict.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Create a tracked provider-native resume attempt from explicit "
            "agent_session_id evidence."
        )
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--agent", required=True, choices=sorted(AGENTS))
    parser.add_argument("--prompt", default="")
    parser.add_argument("--source-dir", default=".")
    parser.add_argument("--idempotency-key", default="")
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    from .workflow import native_resume_run

    result = native_resume_run(
        ns.run_id,
        source_dir=ns.source_dir,
        prompt=ns.prompt,
        expected_agent=ns.agent,
        idempotency_key=ns.idempotency_key,
    )
    if ns.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    elif result.get("accepted"):
        print(
            "native resume dispatched "
            f"run_id={result.get('resume_run_id')} "
            f"resume_of={result.get('run_id')} "
            f"attempt={result.get('attempt')}"
        )
    else:
        detail = str(result.get("detail") or "").strip()
        suffix = f": {detail}" if detail else ""
        print(
            f"vibecrafted-resume: refused {result.get('reason')}{suffix}",
            file=sys.stderr,
        )
    return 0 if result.get("accepted") else 1


def stop_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted stop`: terminates a run's launcher process
    group via `workflow.stop_run` and reports the outcome.
    """
    parser = argparse.ArgumentParser(
        description="Stop a Vibecrafted run by terminating its launcher process group."
    )
    parser.add_argument("--run-id", default="")
    parser.add_argument("--last", action="store_true")
    parser.add_argument("--agent", choices=sorted(AGENTS))
    parser.add_argument("--reason", default="operator stop request")
    parser.add_argument("--grace-seconds", type=float, default=2.0)
    ns = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    from .control_plane import lookup_run, sync_state
    from .workflow import stop_run

    run_id = str(ns.run_id or "").strip()
    if not run_id:
        if not ns.last:
            parser.error("required: --run-id or --last")
        snapshot = sync_state()
        agent = str(ns.agent or "").strip()
        for key in ("active_runs", "recent_runs"):
            for run in snapshot.get(key) or []:
                if agent and str(run.get("agent") or "") != agent:
                    continue
                run_id = str(run.get("run_id") or "").strip()
                if run_id:
                    break
            if run_id:
                break
        if not run_id:
            print(
                "No run found for --last. Pass --run-id.",
                file=sys.stderr,
            )
            return 1
        if lookup_run(run_id) is None:
            print(f"run_id={run_id} stop failed reason=run_not_found", file=sys.stderr)
            return 1

    result = stop_run(
        run_id,
        reason=ns.reason,
        grace_seconds=ns.grace_seconds,
    )
    run = dict(result.get("run") or {})
    reason = str(result.get("reason") or "")
    if result.get("accepted"):
        target = result.get("target") or "unknown"
        target_pid = result.get("target_pid") or ""
        group = result.get("target_pgid")
        group_suffix = f" pgid={group}" if group else ""
        note = (
            "already dead; recorded stopped"
            if result.get("already_dead")
            else "TERM sent"
        )
        print(
            f"run_id={run_id} state={run.get('state', 'stopped')} "
            f"target={target}:{target_pid}{group_suffix} {note}"
        )
        return 0

    if reason == "run_terminal":
        print(
            f"run_id={run_id} already terminal "
            f"state={run.get('state', 'unknown')}; no-op"
        )
        return 0

    print(f"run_id={run_id} stop failed reason={reason}", file=sys.stderr)
    if result.get("error"):
        print(str(result["error"]), file=sys.stderr)
    return 1


def sandbox_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for `vibecrafted sandbox`: status/start/stop the msbserver
    lifecycle, or print the resolved sandbox policy.
    """
    from vibecrafted_core.sandbox import MsbserverLifecycle, SandboxPolicy
    from vibecrafted_core.sandbox.policy import default_policy_path

    parser = argparse.ArgumentParser(description="Manage Vibecrafted microsandbox.")
    parser.add_argument("command", choices=("status", "start", "stop", "policy"))
    parser.add_argument("--policy", dest="policy_path")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    lifecycle = MsbserverLifecycle()
    if args.command == "status":
        state = "running" if lifecycle.is_running() else "not running"
        print(f"msbserver: {state}")
        if lifecycle.pid_file.exists():
            print(f"pid_file: {lifecycle.pid_file}")
        return 0
    if args.command == "start":
        ok = lifecycle.ensure_running()
        print(f"msbserver: {'running' if ok else 'not running'}")
        if not ok:
            print("hint: install microsandbox or set MSBSERVER_EXE", file=sys.stderr)
        return 0 if ok else 1
    if args.command == "stop":
        lifecycle.stop()
        print("msbserver: stopped")
        return 0

    policy = SandboxPolicy.load(args.policy_path, root=Path.cwd())
    path = (
        Path(args.policy_path).expanduser()
        if args.policy_path
        else default_policy_path()
    )
    print(f"policy_file: {path}")
    print(f"cpu: {policy.cpu}")
    print(f"memory_mb: {policy.memory_mb}")
    print(f"network: {policy.network}")
    print(f"filesystem_root_readonly: {policy.filesystem_root_readonly}")
    print(f"tmp_writable: {policy.tmp_writable}")
    print("allow_hosts: " + ", ".join(policy.allow_hosts))
    print("mounts:")
    for mount in policy.mounts:
        print(f"  - {mount}")
    return 0
