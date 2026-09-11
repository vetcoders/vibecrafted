"""Core workflow runtime: launch, stop, retry, block, and native-resume a run."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import guard as guard_mod
from .artifacts import validate_artifacts
from .continuity.capabilities import (
    PROBE_CONFIRMED,
    SUPPORTED,
    UNSUPPORTED,
    UNVERIFIED,
    capability_for,
    probe_provider,
)
from .control_plane import (
    RunNotResolved,
    await_run,
    control_plane_home,
    ensure_session_id,
    lookup_run,
    normalize_run_root,
    record_stop_transition,
    resolve_run,
    run_snapshot_dir,
    sync_state,
)
from .cron import parse_frontmatter
from .delivery.store import atomic_write_json
from .events import append_event
from .execution_controls import (
    SUPERVISED_RUNTIME_KINDS,
    ExecutionControls,
    ExecutionControlsError,
    parse_permissions_word,
    parse_sandbox_word,
    resolve_execution_controls,
)
from .init_resume import init_resume_block
from .model_overrides import _model_override_receipt, _with_model_override
from .package_resources import deck_path as package_deck_path
from .process_control import process_identity_receipt, validate_process_identity
from .repo_selection import (
    git_toplevel,
    parse_worktree_flag,
    resolve_repository_base,
    resolve_repository_identity,
    select_repository,
)
from .report_contract import CLAIM_DIGEST_ENV, reserve_launcher_report_template
from .research_config import ResearchAgentSelection, resolve_research_runtime_config
from .run_mutation import mutate_run_meta, run_mutation_locks
from .runtime_paths import agent_tool_search_path, selected_runtime_environment
from .spawn import _resolve_agent_command, _stdin_command
from .workflow_runtime import WORKER_SIGNAL_DISCIPLINE, native_resume_argv
from .workflows import registry as workflow_registry

SUPPORTED_WORKFLOWS = workflow_registry.SUPPORTED_WORKFLOWS
WORKFLOW_ALIASES = workflow_registry.WORKFLOW_ALIASES
SUPPORTED_AGENTS = {"claude", "codex", "agy", "junie", "grok", "cursor", "swarm"}
SUPPORTED_RUNTIMES = {"headless", "terminal", "visible"}
_TERMINAL_ORIGIN_ENV = {
    "VIBECRAFTED_WORKER_SESSION",
    "VIBECRAFTED_OPERATOR_SESSION",
    "VC_FRAME_SESSION_NAME",
    "VC_FRAME_TAB_NAME",
    "VC_FRAME_PANE_ID",
    "ZELLIJ_SESSION_NAME",
    "ZELLIJ_PANE_ID",
}
TERMINAL_STATES = {
    "completed",
    "failed",
    "report_validated",
    "report_missing",
    "report_invalid",
    "contract_failed",
    "closed",
    "settled",
    "stopped",
    "timed_out",
    "ghost",
}
LAUNCH_IDEMPOTENCY_SCHEMA = "vibecrafted.launch-idempotency.v1"
LAUNCH_IDEMPOTENCY_KEY_ENV = "VIBECRAFTED_LAUNCH_IDEMPOTENCY_KEY"
LAUNCH_IDEMPOTENCY_MAX_TERMINAL_RECORDS = 2048
LAUNCH_IDEMPOTENCY_TERMINAL_TTL_SECONDS = 30 * 24 * 60 * 60
LAUNCH_RECEIPT_SCHEMA = "vibecrafted.launch_receipt.v1"


@dataclass(frozen=True)
class WorkflowLaunchSpec:
    """Normalized, validated launch parameters passed to :func:`launch_workflow`."""

    agent: str
    mode: str
    skill: str
    prompt: str
    file: str
    runtime: str
    root: str
    count: int | None = None
    depth: int | None = None
    model: str = ""
    repo_requested: str = ""
    repo_kind: str = "path"
    base: str = "HEAD"
    baseline_sha: str = ""
    resolved_ref: str = ""
    runtime_class: str = "living-tree"
    source_digest: str = ""
    plan_source: str | None = None
    source_path: str = ""
    research_model_agent: str = ""
    model_source: str = "provider_default"
    research_agents: tuple[str, ...] = ()
    research_synthesizer: str = ""
    research_synthesizer_model: str = ""
    # Push-side report-on-death (docs/runtime/AGENT_OPS.md, Class 2): when the
    # launch belongs to a lifecycle run, this carries the lifecycle state.json
    # path so the dispatcher can write the worker's terminal truth into it.
    lifecycle_state_path: str = ""
    # Machine-owned mission binding for lifecycle stage reports. The worker may
    # attest success, but it cannot choose which mission that attestation closes.
    claim_digest: str = ""
    # Adapters that must expose the execution identity before launch (ACP's
    # session/new) may reserve one through reserve_run_id(). Empty keeps the
    # historical launch-time allocation path.
    run_id: str = ""
    # ``--worktree true``: the worker runs in a fresh linked checkout prepared
    # by the canonical WorktreeManager (dispatch/worktrees.py). ``root`` then
    # names that checkout and ``parent_root`` keeps the selected repository.
    worktree: bool = False
    parent_root: str = ""
    # Public execution controls (``--permissions`` / ``--sandbox``). Empty /
    # ``None`` keeps the historical provider default (bypass; auto for junie;
    # sandbox left to the provider). Both are resolved through
    # execution_controls.resolve_execution_controls, which refuses anything the
    # installed provider CLI cannot enforce before any process is launched.
    permissions: str = ""
    sandbox: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        """Serialize the spec to a plain dict for launch logs and events."""
        return {**asdict(self), "prompt": "", "plan_source": None}


def vibecrafted_launcher(source_dir: str | Path) -> Path:
    """Return the packaged vc-frame deck launcher path (``source_dir`` unused)."""
    return package_deck_path()


def reserve_run_id(skill: str) -> str:
    """Return a safe control-plane run id without creating runtime state."""
    stamp = time.strftime("%y%m%d-%H%M%S")
    code = (skill or "run")[:4].ljust(4, "x")
    # CSPRNG entropy: clock-derived time_ns % 100000 collides when the OS
    # quantizes the clock (macOS CI ticks in whole ms → identical remainder
    # within one second, observed as duplicate run ids).
    entropy = int.from_bytes(os.urandom(3), "big") % 100000
    return f"{code}-{stamp}-{entropy:05d}"


def _run_id(skill: str) -> str:
    """Backward-compatible internal alias for the run-id allocator."""
    return reserve_run_id(skill)


def _artifact_org_repo(root: str | Path) -> tuple[str, str] | None:
    """Derive (org, repo) from the git origin remote, falling back to dir name."""
    root_path = Path(root).expanduser()
    remote = _origin_remote_url(root_path)
    match = re.search(r"[:/]([^/]+)/([^/.]+)(?:\.git)?$", remote)
    if match:
        return match.group(1), match.group(2)
    fallback = root_path.name.strip()
    return ("local", fallback) if fallback else None


def _git_config_path(root: Path) -> Path | None:
    """Resolve the git config file for ``root``, following worktree gitdir/commondir."""
    git_entry = root / ".git"
    if git_entry.is_dir():
        return git_entry / "config"
    if not git_entry.is_file():
        return None
    try:
        raw = git_entry.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.startswith("gitdir:"):
        return None
    git_dir = Path(raw.removeprefix("gitdir:").strip()).expanduser()
    if not git_dir.is_absolute():
        git_dir = (root / git_dir).resolve()
    config = git_dir / "config"
    if config.is_file():
        return config
    common_dir_file = git_dir / "commondir"
    if common_dir_file.is_file():
        try:
            common = Path(common_dir_file.read_text(encoding="utf-8").strip())
        except OSError:
            return None
        if not common.is_absolute():
            common = (git_dir / common).resolve()
        return common / "config"
    return None


def _origin_remote_url(root: Path) -> str:
    """Read the ``[remote "origin"] url`` value from the resolved git config."""
    config_path = _git_config_path(root)
    if config_path is None or not config_path.is_file():
        return ""
    try:
        lines = config_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    in_origin = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_origin = stripped == '[remote "origin"]'
            continue
        if not in_origin or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "url":
            return value.strip()
    return ""


def _run_artifact_paths(run_id: str) -> dict[str, Path]:
    """Create (if needed) and return the run's meta/prompt/transcript paths."""
    run_dir = control_plane_home() / "runtime_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "meta": run_dir / "meta.json",
        "prompt": run_dir / "prompt.md",
        "transcript": run_dir / "transcript.log",
    }


def _canonical_report_dir(root: str | Path, skill: str) -> Path:
    """Resolve (and create) the canonical per-repo/day report dir for ``skill``."""
    org_repo = _artifact_org_repo(root)
    if org_repo is None:
        base = Path(root).expanduser() / ".vibecrafted"
    else:
        org, repo = org_repo
        base = (
            control_plane_home().parent
            / "artifacts"
            / org
            / repo
            / time.strftime("%Y_%m%d")
        )
    path = base / "reports" / skill
    path.mkdir(parents=True, exist_ok=True)
    return path


def _artifact_slug(text: str, fallback: str) -> str:
    """Derive a short filesystem-safe slug from frontmatter title/slug or text."""
    frontmatter = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text, re.DOTALL)
    if frontmatter:
        for key in ("slug", "title"):
            match = re.search(
                rf"(?im)^\s*{key}\s*:\s*[\"']?(.+?)[\"']?\s*$",
                frontmatter.group(1),
            )
            if match:
                words = _slug_words(match.group(1))
                if words:
                    return "-".join(words[:3])
    words = _slug_words(text)
    return "-".join(words[:3]) if words else fallback


def _slug_words(text: str) -> list[str]:
    """Tokenize text into lowercase alnum words, dropping common boilerplate."""
    boilerplate = {
        "a",
        "an",
        "and",
        "for",
        "on",
        "perform",
        "please",
        "research",
        "run",
        "skill",
        "task",
        "the",
        "this",
        "workflow",
    }
    return [
        word
        for word in re.findall(r"[A-Za-z0-9]+", text.lower())
        if word not in boilerplate
    ]


def _canonical_report_path(
    *,
    canonical_report_dir: Path,
    artifact_ts: str,
    agent: str,
    artifact_slug: str,
    run_id: str,
) -> Path:
    """Assemble a run-id-addressed report path; no shared suffix allocator."""
    safe_agent = "-".join(_slug_words(agent)) or "agent"
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip(".-") or "run"
    return (
        canonical_report_dir
        / f"{artifact_ts}_{safe_agent}_{artifact_slug}_{safe_run_id}_report.md"
    )


def _core_package_root() -> Path:
    """Return the vibecrafted-core package root (parent of this module's dir)."""
    return Path(__file__).resolve().parents[1]


def _prepend_pythonpath(env: dict[str, str], path: Path) -> None:
    """Prepend ``path`` to ``env``'s PYTHONPATH, deduplicating existing entries."""
    existing = env.get("PYTHONPATH", "")
    entries = [str(path)]
    entries.extend(item for item in existing.split(os.pathsep) if item)
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(entries))


def _dispatcher_command(
    *,
    run_id: str,
    root: str,
    meta_path: Path,
    prompt_path: Path | None = None,
    report_path: Path,
    transcript_path: Path,
    worker_command: list[str],
    tee_output: bool = False,
    emit_json: bool = True,
    quiet: bool = False,
    lifecycle_state_path: str = "",
    salvage_report_from_stream: bool = False,
) -> list[str]:
    """Build the ``vibecrafted_core.dispatcher run`` argv wrapping ``worker_command``."""
    command = [
        sys.executable,
        "-m",
        "vibecrafted_core.dispatcher",
        "run",
        "--run-id",
        run_id,
        "--root",
        root,
        "--meta",
        str(meta_path),
        "--report",
        str(report_path),
        "--transcript",
        str(transcript_path),
    ]
    if prompt_path is not None:
        command.extend(
            [
                "--prompt-file",
                str(prompt_path),
            ]
        )
    if lifecycle_state_path:
        command.extend(["--lifecycle-state", lifecycle_state_path])
    if salvage_report_from_stream:
        command.append("--salvage-report-from-stream")
    if tee_output:
        command.append("--tee-output")
    if quiet:
        command.append("--quiet")
    if emit_json:
        command.append("--json")
    command.append("--")
    command.extend(worker_command)
    return command


def _write_command_script(
    path: Path, command: list[str], exports: dict[str, str] | None = None
) -> Path:
    """Write an executable bash wrapper: export ``exports`` then ``exec command``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    export_lines = "".join(
        f"export {key}={shlex.quote(value)}\n" for key, value in (exports or {}).items()
    )
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"{export_lines}"
        f"exec {shlex.join(command)}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _runtime_script_exports(
    *,
    run_id: str,
    prompt_path: Path,
    report_path: Path,
    transcript_path: Path,
    meta_path: Path,
    agent: str,
    skill: str,
    runtime: str,
    canonical_report_dir: Path | None = None,
    artifact_slug: str = "",
    artifact_ts: str = "",
    artifact_suffix: str = "",
    claim_digest: str = "",
    worker_session: str = "",
) -> dict[str, str]:
    """Build the env-var export map embedded in generated dispatcher/lane scripts."""
    pythonpath = os.pathsep.join(
        dict.fromkeys(
            [str(_core_package_root())]
            + [
                item
                for item in os.environ.get("PYTHONPATH", "").split(os.pathsep)
                if item
            ]
        )
    )
    exports = {
        "VIBECRAFTED_RUN_ID": run_id,
        "VIBECRAFTED_REPORT_PATH": str(report_path),
        "VIBECRAFTED_TRANSCRIPT_PATH": str(transcript_path),
        "VIBECRAFTED_META_PATH": str(meta_path),
        "VIBECRAFTED_PROMPT_PATH": str(prompt_path),
        "VIBECRAFTED_AGENT": agent,
        "VIBECRAFTED_SKILL": skill,
        "VIBECRAFTED_RUNTIME": runtime,
        # vc-frame starts this script from its long-lived server environment,
        # not from launch_workflow's Popen(env=...). Keep the generated
        # dispatcher self-contained and keep installed payloads bytecode-clean.
        "PYTHONPATH": pythonpath,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if canonical_report_dir is not None:
        exports["VIBECRAFTED_CANONICAL_REPORT_DIR"] = str(canonical_report_dir)
    if artifact_slug:
        exports["VIBECRAFTED_ARTIFACT_SLUG"] = artifact_slug
    if artifact_ts:
        exports["VIBECRAFTED_ARTIFACT_TS"] = artifact_ts
    if artifact_suffix:
        exports["VIBECRAFTED_ARTIFACT_SUFFIX"] = artifact_suffix
    if claim_digest:
        exports[CLAIM_DIGEST_ENV] = claim_digest
    if worker_session:
        # vc-frame launches this script from the host server's long-lived
        # environment. That environment can still describe the human
        # dispatcher seat, so pin the actual worker host for durable triage.
        exports["VIBECRAFTED_WORKER_SESSION"] = worker_session
        exports["VIBECRAFTED_OPERATOR_SESSION"] = worker_session
    if runtime in {"terminal", "visible"}:
        exports["VIBECRAFTED_TEE_OUTPUT"] = "1"
    return exports


def _kdl_string(value: str | Path) -> str:
    """Encode ``value`` as a KDL string literal (JSON quoting is a valid subset)."""
    return json.dumps(str(value), ensure_ascii=False)


def _write_research_lane_scripts(
    *,
    launch_dir: Path,
    run_id: str,
    root: str,
    prompt_path: Path,
    report_path: Path,
    transcript_path: Path,
    meta_path: Path,
    canonical_report_dir: Path,
    artifact_slug: str,
    artifact_ts: str,
    artifact_suffix: str,
    research_selection: ResearchAgentSelection,
    model_requested: str = "",
    claim_digest: str = "",
    worker_session: str = "",
) -> dict[str, Path]:
    """Write one executable launcher script per selected research-lane agent."""
    scripts: dict[str, Path] = {}
    for agent in research_selection.agents:
        path = launch_dir / f"{run_id}-research-{agent}.sh"
        command = [
            sys.executable,
            "-m",
            "vibecrafted_core.workflow_runtime",
            "research-lane",
            "--agent",
            agent,
            "--root",
            root,
            "--prompt-file",
            str(prompt_path),
        ]
        lane_model = research_selection.lane_model(agent, model_requested)
        if lane_model:
            command.extend(["--model", lane_model])
        exports = _runtime_script_exports(
            run_id=run_id,
            prompt_path=prompt_path,
            report_path=report_path,
            transcript_path=transcript_path,
            meta_path=meta_path,
            agent=agent,
            skill="research",
            runtime="terminal",
            canonical_report_dir=canonical_report_dir,
            artifact_slug=artifact_slug,
            artifact_ts=artifact_ts,
            artifact_suffix=artifact_suffix,
            claim_digest=claim_digest,
            worker_session=worker_session,
        )
        export_lines = "".join(
            f"export {key}={shlex.quote(value)}\n" for key, value in exports.items()
        )
        path.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"{export_lines}"
            f"exec {shlex.join(command)}\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        scripts[agent] = path
    return scripts


def _write_research_layout(
    *,
    path: Path,
    synthesis_script: Path,
    lane_scripts: dict[str, Path],
) -> Path:
    """Write the vc-frame KDL layout wiring the synthesis pane + lane panes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lane_panes = []
    for agent, script in lane_scripts.items():
        lane_panes.append(
            f"""                pane name={_kdl_string(agent)} command="bash" {{
                    args {_kdl_string(script)}
                }}"""
        )
    lane_panes_text = "\n".join(lane_panes)
    path.write_text(
        f"""layout {{
    default_tab_template {{
        pane size=1 borderless=true {{
            plugin location="compact-bar"
        }}
        children
        pane size=1 borderless=true {{
            plugin location="status-bar"
        }}
    }}

    tab name="Vibecrafted Research" {{
        pane split_direction="vertical" {{
            pane name="synthesis" size="55%" focus=true command="bash" {{
                args {_kdl_string(synthesis_script)}
            }}
            pane split_direction="horizontal" size="45%" {{
{lane_panes_text}
            }}
        }}
    }}
}}
""",
        encoding="utf-8",
    )
    return path


_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")
_SESSION_NOT_FOUND_RE = re.compile(
    r"Session ['\"][^'\"]+['\"] not found|There is no active session!",
    re.IGNORECASE,
)
# G3b: ambiguous critical-action ACK (NewTab under load). Parity with
# vc-frame triage `is_ambiguous_new_tab_failure` + bash spawn path.
_AMBIGUOUS_ACTION_ACK_RE = re.compile(
    r"did not acknowledge completion|completion channel closed before acknowledgement|timed out after",
    re.IGNORECASE,
)


def _vc_frame_stderr_is_session_not_found(text: str) -> bool:
    """True when stderr carries vc-frame's missing-host-session diagnostic.

    Some builds exit 0 while still printing this message — exit code alone is
    not sufficient (G3 recon, 2026-07-21).
    """
    return bool(_SESSION_NOT_FOUND_RE.search(text or ""))


def _vc_frame_stderr_is_ambiguous_action_ack(text: str) -> bool:
    """True when stderr looks like a timed-out action ACK that may still have applied."""
    return bool(_AMBIGUOUS_ACTION_ACK_RE.search(text or ""))


def _vc_frame_action_name_arg(command: list[str]) -> str:
    """Extract ``--name VALUE`` from a vc-frame action argv, if present."""
    for index, token in enumerate(command):
        if token == "--name" and index + 1 < len(command):
            return str(command[index + 1] or "")
    return ""


def _vc_frame_tab_present(vc_frame: str, session: str, tab_name: str) -> bool:
    """True when ``tab_name`` is enumerable via ``action list-tabs --json``."""
    if not vc_frame or not tab_name:
        return False
    cmd = [vc_frame]
    if session:
        cmd.extend(["--session", session])
    cmd.extend(["action", "list-tabs", "--json"])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    raw = (result.stdout or "").strip()
    if not raw:
        return False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Best-effort substring fallback for stub/fake binaries in tests.
        return f'"{tab_name}"' in raw or tab_name in raw

    def visit(node: Any) -> bool:
        """Recursively search a decoded JSON node for a matching tab name."""
        if isinstance(node, dict):
            name = node.get("name")
            if name in (None, ""):
                name = node.get("tab_name")
            if str(name or "") == tab_name:
                return True
            return any(visit(value) for value in node.values())
        if isinstance(node, list):
            return any(visit(item) for item in node)
        return False

    return visit(payload)


def _vc_frame_subprocess_env() -> dict[str, str]:
    """Env for vc-frame subprocesses.

    Claude / CLI paths do not inherit AppDelegate's short macOS socket root.
    TMPDIR + contract_version_N already fills sockaddr_un (104 bytes) before
    a workspace-bound session name is appended.
    """

    env = selected_runtime_environment()
    if (
        sys.platform == "darwin"
        and not str(env.get("VC_FRAME_SOCKET_DIR") or "").strip()
        and not str(env.get("ZELLIJ_SOCKET_DIR") or "").strip()
    ):
        socket = f"/tmp/vc-frame-{os.getuid()}"
        env["VC_FRAME_SOCKET_DIR"] = socket
        env["ZELLIJ_SOCKET_DIR"] = socket
    env["PATH"] = agent_tool_search_path(env)
    return env


def _vc_frame_session_active(vc_frame: str, session: str) -> bool:
    """True only if `session` is a live (non-EXITED) vc-frame session.

    A terminal-runtime launch opens a new tab in an existing operator session
    (`vc-frame --session <name> action new-tab`). If that session does not
    exist, vc-frame prints "Session '<name>' not found" and the tab — and the
    dispatcher inside it — never runs. EXITED sessions are not spawn targets
    (parity with bash ``spawn_session_is_live``).
    """
    if not session:
        return False
    try:
        result = subprocess.run(
            [vc_frame, "list-sessions"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=_vc_frame_subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        clean = _ANSI_SGR.sub("", line).strip()
        if not clean:
            continue
        if "EXITED" in clean.upper():
            continue
        # Strip trailing status tags so a host line (G7: "<repo>-workers") matches.
        name = re.sub(r"\s+\[.*$", "", clean)
        name = re.sub(r"\s+\([^)]*\)$", "", name).rstrip()
        if name == session:
            return True
    return False


def _vc_frame_create_background(vc_frame: str, session: str) -> tuple[bool, str]:
    """One-shot ``attach --create-background`` for a missing host session."""
    try:
        result = subprocess.run(
            [vc_frame, "attach", "--create-background", session],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=_vc_frame_subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    combined = "\n".join(
        part for part in (result.stderr or "", result.stdout or "") if part
    ).strip()
    if _vc_frame_session_active(vc_frame, session):
        return True, combined
    if result.returncode == 0:
        # Some builds report success before the session is listable; accept
        # zero exit when the message is not a hard failure.
        return True, combined
    return False, combined or f"attach --create-background exit {result.returncode}"


@dataclass(frozen=True)
class _HostActionResult:
    """Outcome of one ``_vc_frame_run_host_action`` attempt, including recovery state."""

    ok: bool
    pid: int | None
    error: str
    stderr: str
    resurrected: bool = False


def _vc_frame_run_host_action(
    command: list[str],
    *,
    operator_session: str,
    timeout: float = 45.0,
) -> _HostActionResult:
    """Run a vc-frame host action with host-resurrect + ambiguous-ACK recovery.

    G3: treats "Session 'X' not found" as failure even when the binary exits 0,
    then one ``attach --create-background`` + retry.

    G3b: on ambiguous NewTab ACK timeouts, probe for ``--name`` presence before
    retrying so a late-ACK success does not open a duplicate worker tab.
    Default timeout is 45s (above the 25s critical ACK budget) so one full
    ACK wait still fits a single ``_run_once``.
    """
    if not command:
        return _HostActionResult(False, None, "empty vc-frame command", "", False)

    resurrected = False
    tab_name = _vc_frame_action_name_arg(command)
    vc_frame_bin = command[0]

    def _run_once() -> subprocess.CompletedProcess[str]:
        """Run the vc-frame host action subprocess once with the configured timeout."""
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_vc_frame_subprocess_env(),
        )

    def _combined(result: subprocess.CompletedProcess[str]) -> str:
        """Join stderr+stdout into one stripped diagnostic string."""
        return "\n".join(
            part for part in (result.stderr or "", result.stdout or "") if part
        ).strip()

    def _presence_ok() -> bool:
        """After a short settle delay, confirm the target tab is actually present."""
        if not tab_name:
            return False
        time.sleep(1)
        return _vc_frame_tab_present(vc_frame_bin, operator_session, tab_name)

    # CompletedProcess has no .pid; host actions are short-lived, so pid is
    # informational only. Use os.getpid() of the parent as a stable handle for
    # receipt fields that require an int.
    action_pid = os.getpid()

    try:
        result = _run_once()
    except (OSError, subprocess.SubprocessError) as exc:
        return _HostActionResult(False, None, f"{type(exc).__name__}: {exc}", "", False)

    combined = _combined(result)

    if _vc_frame_stderr_is_session_not_found(combined):
        if not operator_session:
            return _HostActionResult(False, action_pid, combined, combined, False)
        ok_create, create_err = _vc_frame_create_background(
            vc_frame_bin, operator_session
        )
        resurrected = True
        if not ok_create:
            err = "\n".join(
                part
                for part in (combined, create_err, "attach --create-background failed")
                if part
            )
            return _HostActionResult(False, action_pid, err, err, True)
        try:
            result = _run_once()
        except (OSError, subprocess.SubprocessError) as exc:
            return _HostActionResult(
                False, None, f"{type(exc).__name__}: {exc}", "", True
            )
        combined = _combined(result)
        if _vc_frame_stderr_is_session_not_found(combined) or result.returncode != 0:
            err = (
                combined
                or f"vc-frame action failed after host resurrect (exit {result.returncode})"
            )
            return _HostActionResult(False, action_pid, err, err, True)

    elif result.returncode != 0:
        if _vc_frame_stderr_is_ambiguous_action_ack(combined):
            if _presence_ok():
                return _HostActionResult(True, action_pid, "", combined, resurrected)
            time.sleep(2)
            try:
                result = _run_once()
            except (OSError, subprocess.SubprocessError) as exc:
                return _HostActionResult(
                    False, None, f"{type(exc).__name__}: {exc}", "", resurrected
                )
            combined = _combined(result)
            if result.returncode == 0:
                return _HostActionResult(True, action_pid, "", combined, resurrected)
            if _vc_frame_stderr_is_ambiguous_action_ack(combined) and _presence_ok():
                return _HostActionResult(True, action_pid, "", combined, resurrected)
        err = combined or f"vc-frame action exit {result.returncode}"
        return _HostActionResult(False, action_pid, err, err, resurrected)

    return _HostActionResult(True, action_pid, "", combined, resurrected)


def _launch_transport_command(
    *,
    spec: WorkflowLaunchSpec,
    run_id: str,
    operator_session: str,
    dispatch_command: list[str],
    launch_dir: Path,
    prompt_path: Path,
    report_path: Path,
    transcript_path: Path,
    meta_path: Path,
    canonical_report_dir: Path | None,
    artifact_slug: str,
    artifact_ts: str,
    artifact_suffix: str,
    research_selection: ResearchAgentSelection | None = None,
) -> tuple[list[str], str, Path | None]:
    """Build the transport-specific launch argv: headless dispatch or vc-frame host action.

    Returns ``(command, transport, command_script)`` where ``transport`` is
    ``"headless"`` (direct dispatch command, no script) or ``"vc-frame"``
    (a ``new-tab`` action wrapping a generated command script).
    """
    if spec.runtime not in {"terminal", "visible"}:
        return dispatch_command, "headless", None

    vc_frame = shutil.which("vc-frame") or ""
    if not vc_frame:
        return dispatch_command, "headless", None

    # G3: missing host sessions are handled at launch time via one-shot
    # `attach --create-background` + action retry (`_vc_frame_run_host_action`).
    # Do not silently degrade to headless here — that path left runs in
    # process_spawned→stalled with no last_error. Always emit the vc-frame
    # transport when the binary exists; the spawn step fails loud on double-fail.

    command_script = _write_command_script(
        launch_dir / f"{run_id}-dispatcher.sh",
        dispatch_command,
        exports=_runtime_script_exports(
            run_id=run_id,
            prompt_path=prompt_path,
            report_path=report_path,
            transcript_path=transcript_path,
            meta_path=meta_path,
            agent=spec.agent,
            skill=spec.skill,
            runtime=spec.runtime,
            canonical_report_dir=canonical_report_dir,
            artifact_slug=artifact_slug,
            artifact_ts=artifact_ts,
            artifact_suffix=artifact_suffix,
            claim_digest=spec.claim_digest,
            worker_session=operator_session,
        ),
    )
    definition = workflow_registry.workflow_definition(spec.skill)
    if spec.skill == "research":
        selection = research_selection or resolve_research_runtime_config(
            override_agents=spec.research_agents,
            synthesizer=spec.research_synthesizer,
            synthesizer_model=spec.research_synthesizer_model,
        )
        lane_scripts = _write_research_lane_scripts(
            launch_dir=launch_dir,
            run_id=run_id,
            root=spec.root,
            prompt_path=prompt_path,
            report_path=report_path,
            transcript_path=transcript_path,
            meta_path=meta_path,
            canonical_report_dir=canonical_report_dir
            or _canonical_report_dir(spec.root, spec.skill),
            artifact_slug=artifact_slug,
            artifact_ts=artifact_ts,
            artifact_suffix=artifact_suffix,
            research_selection=selection,
            model_requested=spec.model,
            claim_digest=spec.claim_digest,
            worker_session=operator_session,
        )
        layout_file = _write_research_layout(
            path=launch_dir / f"{run_id}-research.kdl",
            synthesis_script=command_script,
            lane_scripts=lane_scripts,
        )
        return (
            [
                vc_frame,
                "--session",
                operator_session,
                "action",
                "new-tab",
                "--layout",
                str(layout_file),
                "--name",
                run_id,
                "--cwd",
                spec.root,
            ],
            "vc-frame",
            command_script,
        )

    terminal_command = [
        vc_frame,
        "--session",
        operator_session,
        "action",
        "new-tab",
    ]
    if definition is not None and definition.terminal_layout:
        terminal_command.extend(["--layout", definition.terminal_layout])
    terminal_command.extend(
        [
            "--name",
            run_id,
            "--cwd",
            spec.root,
            "--",
            str(command_script),
        ]
    )
    return (
        terminal_command,
        "vc-frame",
        command_script,
    )


def _effective_operator_session(*, root: str, run_id: str, env: dict[str, str]) -> str:
    """Resolve the vc-frame session that hosts worker tabs (G7).

    Python mirror of bash ``spawn_effective_operator_session``
    (``runtime/scripts/lib/vc_frame.sh``). The launch-log field
    ``operator_session`` records this host (truthful worker target), not the
    human operator's interactive seat.

    Rules (exact order):

    1. ``VIBECRAFTED_WORKER_SESSION`` if set — explicit override wins.
    2. Else workspace-bound host from the control-plane catalog
       (``{label}-{workspace_short} workers``). Two workspaces that share the
       same root basename never share a worker host.
    3. Emergency fallback only: ``"<basename(root)> workers"`` when the
       catalog cannot be opened.

    2026-08-09: the suffix used to be conditional on ``basename(root)``
    colliding with the dispatcher seat (``VC_FRAME_SESSION_NAME`` /
    ``ZELLIJ_SESSION_NAME``). That guarded only the seat==repo case, so a
    dispatch fired from any other seat routed worker tabs straight into the
    operator's interactive card (20 runs stamped ``operator_session:
    "vibecrafted"``). The declared invariant is unconditional, so the rule is
    too — the dispatcher seat no longer participates in host resolution.

    2026-08-10 Cut A: basename-only hosts collide across checkouts with the
    same name. Worker ownership is now bound to ``workspace_id``.

    Missing hosts are resurrected by G3 (``attach --create-background``). The
    ``run_id`` argument is retained for call-site compatibility only.
    """
    _ = run_id  # call-site compatibility; not part of G7 host rules
    from .workspace_catalog import resolve_worker_host_session

    return resolve_worker_host_session(root=root or ".", env=env)


def _run_is_terminal(run: dict[str, Any]) -> bool:
    """True when a run's projected state/liveness/exit_code marks it terminal."""
    if str(run.get("state") or "") in TERMINAL_STATES:
        return True
    if str(run.get("liveness") or "") == "terminal":
        return True
    return run.get("exit_code") is not None


def _path_exists(path: str) -> bool:
    """Safely check a string path exists as a file; False on any OSError."""
    if not path:
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False


def _report_frontmatter_value(report_path: str, key: str) -> str:
    """Read one frontmatter key from a worker's report file, or '' if absent."""
    if not _path_exists(report_path):
        return ""
    values = parse_frontmatter(Path(report_path))
    return str(values.get(key) or "").strip()


def _report_requested_next_stage(report_path: str) -> str:
    """Worker-requested lifecycle steering read from report frontmatter.

    A stage worker may steer the lifecycle runner (umbrella forward/backward)
    by writing ``next_stage: <stage-id>`` in its report frontmatter. Unknown
    stage ids are ignored downstream by the runner's manifest validation.
    """
    return _report_frontmatter_value(report_path, "next_stage")


def _report_requested_next_agent(report_path: str) -> str:
    """Worker-requested baton handoff read from report frontmatter.

    A stage worker may hand the lifecycle baton to another agent by writing
    ``next_agent: <agent-id>`` in its report frontmatter. Unknown agents are
    ignored downstream by the runner's SUPPORTED_AGENTS validation.
    """
    return _report_frontmatter_value(report_path, "next_agent")


def report_dou_index(report_path: str) -> int | None:
    """Worker-reported DoU index read from report frontmatter.

    A DoU stage worker measures the launch gap by writing
    ``dou_index: <int>`` — the count of open Definition-of-Undone findings —
    in its report frontmatter; 0 is the launch-ready target (ZERO DoU index).
    Absent or invalid values read as ``None``, never as a fake zero.

    Public (unlike the ``next_stage``/``next_agent`` readers) because the
    lifecycle status surface also reads it live in no-await mode.
    """
    raw = _report_frontmatter_value(report_path, "dou_index")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _read_json_object(path: Path) -> dict[str, Any]:
    """Best-effort read of a JSON object file; {} on any failure or non-dict payload."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _terminal_meta_payload(
    *,
    run_id: str,
    run: dict[str, Any],
    report_path: str,
    transcript_path: str,
    meta_path: str,
) -> dict[str, Any]:
    """Assemble the flat terminal-state summary merged into run meta on completion."""
    return {
        "run_id": run_id,
        "state": str(run.get("state") or ""),
        "liveness": str(run.get("liveness") or ""),
        "operator_state": str(run.get("operator_state") or ""),
        "terminal": _run_is_terminal(run),
        "exit_code": run.get("exit_code"),
        "session_id": str(run.get("session_id") or ""),
        "root": str(run.get("root") or ""),
        "agent": str(run.get("agent") or ""),
        "skill": str(run.get("skill") or ""),
        "mode": str(run.get("mode") or ""),
        "report": report_path,
        "transcript": transcript_path,
        "meta": meta_path,
        "artifact_ok": bool(run.get("artifact_ok")),
        "artifact_errors": list(run.get("artifact_errors") or []),
        "updated_at": str(run.get("updated_at") or ""),
        "completed_at": str(run.get("completed_at") or ""),
    }


def _write_terminal_meta(
    *,
    run_id: str,
    run: dict[str, Any],
    report_path: str,
    transcript_path: str,
    meta_path: str,
) -> dict[str, Any]:
    """Merge the run's terminal state into its meta.json under mutation locking."""
    if not meta_path:
        return {}
    path = Path(meta_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    terminal = _terminal_meta_payload(
        run_id=run_id,
        run=run,
        report_path=report_path,
        transcript_path=transcript_path,
        meta_path=meta_path,
    )
    written: dict[str, Any] = {}

    def _merge(payload: dict[str, Any]) -> dict[str, Any]:
        """Merge the computed terminal fields into the existing meta payload in place."""
        payload.update(terminal)
        written.update(payload)
        return payload

    mutate_run_meta(
        control_plane_home(),
        meta_path=path,
        run_id=run_id,
        mutator=_merge,
        create=True,
    )
    return written


def await_launch_truth(
    launch: dict[str, Any] | str,
    *,
    timeout_seconds: float = 300,
    interval_seconds: float = 5,
    hard_cap_seconds: float | None = None,
    require_report: bool = True,
    require_transcript_output: bool = False,
) -> dict[str, Any]:
    """Await a launched workflow and verify its announced artifact paths.

    This is intentionally separate from :func:`launch_workflow` so callers can
    keep launch acceptance asynchronous while dispatch engines can later prove
    terminal run truth for returned run ids. ``timeout_seconds`` is forwarded as
    :func:`await_run`'s liveness-aware idle deadline (resets on real activity);
    ``hard_cap_seconds`` is the optional absolute ceiling for a live-but-wedged
    worker.
    """
    launch_payload: dict[str, Any]
    if isinstance(launch, dict):
        launch_payload = dict(launch)
        run_id = str(launch_payload.get("run_id") or "").strip()
    else:
        launch_payload = {}
        run_id = str(launch or "").strip()
    if not run_id:
        raise ValueError("run_id is required")

    awaited = await_run(
        run_id,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        hard_cap_seconds=hard_cap_seconds,
    )
    run = dict(awaited.get("run") or {})
    await_reason = str(awaited.get("reason") or "")
    worker_alive = bool(awaited.get("worker_alive"))
    report_path = str(launch_payload.get("report") or run.get("latest_report") or "")
    transcript_path = str(
        launch_payload.get("transcript") or run.get("latest_transcript") or ""
    )
    meta_path = str(launch_payload.get("meta") or run.get("meta") or "")
    # The await verdict outranks the returned projection: finalize snapshots
    # can lag a just-written terminal meta (the documented "meta stuck
    # active/stalled after real completion" skew), so an await that settled on
    # reason == "terminal" is terminal even when the projection has not
    # caught up yet.
    terminal = (
        bool(awaited.get("completed"))
        and not worker_alive
        and (await_reason == "terminal" or _run_is_terminal(run))
    )
    terminal_evidence = terminal or (
        bool(awaited.get("completed"))
        and await_reason == "report_delivered"
        and not worker_alive
    )

    meta_payload: dict[str, Any] = {}
    if terminal:
        if str(run.get("liveness") or "") != "terminal":
            # The event-stream projection lags the launcher-written runtime
            # meta (it only saw process_spawned/heartbeat), so once the await
            # verdict is terminal, close the run through the canonical
            # success reconciler before persisting terminal meta.
            from .control_plane import (
                _has_success_evidence,
                _reconcile_successful_terminal,
            )

            if _has_success_evidence(run):
                run = _reconcile_successful_terminal(run)
        meta_payload = _write_terminal_meta(
            run_id=run_id,
            run=run,
            report_path=report_path,
            transcript_path=transcript_path,
            meta_path=meta_path,
        )

    validation = validate_artifacts(
        meta_path=meta_path or None,
        report_path=report_path or None,
        transcript_path=transcript_path or None,
        require_report=require_report,
        require_transcript_output=require_transcript_output,
    )
    paths_exist = {
        "report": _path_exists(report_path),
        "transcript": _path_exists(transcript_path),
        "meta": _path_exists(meta_path),
    }
    path_errors = [
        f"{name}_missing" for name, exists in paths_exist.items() if not exists
    ]

    return {
        "run_id": run_id,
        "found": bool(awaited.get("found")),
        "completed": bool(awaited.get("completed")),
        "timed_out": bool(awaited.get("timed_out")),
        "terminal": terminal,
        "terminal_evidence": terminal_evidence,
        "await_reason": await_reason,
        "worker_alive": worker_alive,
        "attempts": awaited.get("attempts"),
        "run": run,
        "report": report_path,
        "transcript": transcript_path,
        "meta": meta_path,
        "paths_exist": paths_exist,
        "artifact_ok": validation.ok and not path_errors,
        "artifact_errors": list(validation.errors) + path_errors,
        "artifact_warnings": list(validation.warnings),
        "meta_payload": meta_payload or validation.meta_payload,
        "next_stage": _report_requested_next_stage(report_path),
        "next_agent": _report_requested_next_agent(report_path),
        "dou_index": report_dou_index(report_path),
    }


def _stop_signal_target(run: dict[str, Any]) -> tuple[str, int] | None:
    """Pick the best (kind, pid) to signal for stopping a run, or None if unknown."""
    # The dispatcher and worker deliberately live in separate process groups.
    # Stop the actual worker tree first; launcher_pid is only a pre-seed/legacy
    # fallback before the supervisor has published worker identity.
    for key in ("worker_pgid", "worker_pid", "launcher_pid"):
        raw = run.get(key)
        if isinstance(raw, int) and raw > 0:
            return key, raw
        if isinstance(raw, str) and raw.strip().isdigit():
            return key, int(raw.strip())
    return None


@dataclass(frozen=True)
class _QualifiedStopSignal:
    """A stop target whose process identity has just been verified against its receipt."""

    kind: str
    target_pid: int
    identity_pid: int
    target_pgid: int | None


def _pid_is_alive(pid: int) -> bool:
    """True if ``pid`` responds to signal 0 (permission-denied counts as alive)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pgid_is_alive(pgid: int) -> bool:
    """True if the process group ``pgid`` responds to signal 0."""
    if pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _stop_target_is_alive(target: _QualifiedStopSignal) -> bool:
    """Liveness check for a qualified stop target, preferring its process group."""
    if target.target_pgid is not None:
        return _pgid_is_alive(target.target_pgid)
    return _pid_is_alive(target.identity_pid)


def _wait_for_stop_target_exit(
    target: _QualifiedStopSignal, grace_seconds: float
) -> bool:
    """Poll until the target exits or ``grace_seconds`` elapses; return still-alive."""
    deadline = time.monotonic() + max(float(grace_seconds), 0.0)
    while time.monotonic() < deadline:
        if not _stop_target_is_alive(target):
            return False
        time.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))
    return _stop_target_is_alive(target)


def _legacy_stop_target_alive(target_kind: str, target_pid: int) -> bool:
    """Liveness probe for a stop target with no identity receipt, by recorded kind."""
    # All dispatcher/worker groups created by this runtime are new sessions, so
    # their leader PID is also the PGID. A live legacy number is never enough
    # authority to signal; this probe only distinguishes a safely-gone target.
    if target_kind == "launcher_pid":
        return _pid_is_alive(target_pid) or _pgid_is_alive(target_pid)
    if target_kind == "worker_pgid":
        return _pgid_is_alive(target_pid)
    return _pid_is_alive(target_pid)


def _qualify_stop_signal(
    run_id: str,
    run: dict[str, Any],
    *,
    target_kind: str,
    target_pid: int,
) -> tuple[_QualifiedStopSignal | None, str, bool]:
    """Resolve a current signal target without ever trusting a number alone."""

    worker_target = target_kind.startswith("worker_")
    receipt_key = "worker_identity" if worker_target else "launcher_identity"
    receipt = run.get(receipt_key)
    if not isinstance(receipt, dict):
        gone = not _legacy_stop_target_alive(target_kind, target_pid)
        return (
            None,
            "pid_gone_before_stop" if gone else "process_identity_unavailable",
            gone,
        )

    identity_pid = _coerce_positive_int(receipt.get("pid"))
    if identity_pid is None:
        return None, "process_identity_invalid", False

    if worker_target:
        recorded_worker_pid = _coerce_positive_int(run.get("worker_pid"))
        if recorded_worker_pid is not None and recorded_worker_pid != identity_pid:
            return None, "process_identity_mismatch", False
        expected_pgid = _coerce_positive_int(run.get("worker_pgid"))
        if target_kind == "worker_pgid" and expected_pgid != target_pid:
            return None, "process_identity_mismatch", False
    else:
        if identity_pid != target_pid:
            return None, "process_identity_mismatch", False
        expected_pgid = _coerce_positive_int(receipt.get("pgid"))

    current, reason, _identity = validate_process_identity(
        receipt,
        expected_pid=identity_pid,
        expected_pgid=expected_pgid,
        expected_run_id=run_id,
    )
    if not current:
        if reason == "process_identity_gone":
            group_alive = bool(expected_pgid and _pgid_is_alive(expected_pgid))
            pid_alive = _pid_is_alive(identity_pid)
            if not group_alive and not pid_alive:
                return None, "pid_gone_before_stop", True
            return None, "process_identity_unavailable", False
        return None, reason, False

    signal_pgid: int | None
    if target_kind == "worker_pid":
        signal_pgid = None
    else:
        signal_pgid = expected_pgid
        if signal_pgid is None:
            return None, "process_identity_invalid", False
    return (
        _QualifiedStopSignal(
            kind=target_kind,
            target_pid=target_pid,
            identity_pid=identity_pid,
            target_pgid=signal_pgid,
        ),
        "process_identity_current",
        False,
    )


def _normalized_runtime(raw: str) -> str:
    """Coerce a raw runtime string to a supported runtime, default "headless"."""
    if raw not in SUPPORTED_RUNTIMES:
        raise ValueError(f"Unsupported runtime: {raw}; no host adapter is available")
    return raw


def _coerce_positive_int(value: Any, default: int | None = None) -> int | None:
    """Parse ``value`` as a positive int, else ``default`` (empty/invalid/<=0)."""
    if value in (None, ""):
        return default
    try:
        result = int(str(value))
    except ValueError:
        return default
    return result if result > 0 else default


def _workflow_metadata(skill: str) -> dict[str, Any]:
    """Project a workflow definition's phase/tooling metadata for event payloads."""
    definition = workflow_registry.workflow_definition(skill)
    if definition is None:
        return {}
    return {
        "id": definition.id,
        "phase": definition.cadence,
        "can_modify_code": definition.can_modify_code,
        "runtime_kind": definition.runtime_kind,
        "tooling": list(definition.tooling),
        "lifecycle_order": definition.lifecycle_order,
    }


def read_prompt_stream(stream: Any) -> str:
    """Read UTF-8 bytes without universal-newline translation (16 MiB maximum)."""
    source = getattr(stream, "buffer", stream)
    data = source.read(16 * 1024 * 1024 + 1)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("prompt exceeds the 16 MiB input limit")
    return raw.decode("utf-8")


def select_plan_model(
    agent: str, text: str, *, model: str = "", previous: str = ""
) -> tuple[str, str]:
    """Select an exact provider identifier without altering the source document."""
    fields = parse_frontmatter(text=text, strict=True)
    if fields.get("agent") and fields["agent"] != agent:
        raise ValueError("frontmatter agent conflicts with selected provider")
    if model:
        if (
            not isinstance(model, str)
            or not model.strip()
            or model != model.strip()
            or model.startswith("-")
            or any(ord(c) < 32 for c in model)
        ):
            raise ValueError("CLI model must be a non-empty provider identifier")
        return model, "cli"
    if "model" in fields:
        return fields["model"], "plan_frontmatter"
    if previous:
        return previous, "resume_previous"
    return "", "provider_default"


def normalize_launch_spec(
    payload: dict[str, Any], source_dir: str | Path
) -> WorkflowLaunchSpec:
    """Validate and normalize a raw launch payload into a :class:`WorkflowLaunchSpec`.

    Resolves workflow/agent aliasing, research-agent selection, model overrides
    (flag or brief frontmatter), and root/runtime defaults; raises ``ValueError``
    on any unsupported workflow, agent, or missing required input.
    """
    requested_skill = str(payload.get("skill") or "workflow").strip()
    skill = WORKFLOW_ALIASES.get(requested_skill, requested_skill)
    definition = workflow_registry.workflow_definition(skill)
    if definition is None:
        raise ValueError(f"Unsupported workflow: {skill}")

    raw_agent = payload.get("agent")
    raw_research_agents = payload.get("research_agents") or ()
    if isinstance(raw_agent, (list, tuple)):
        positional_agents = tuple(
            str(item).strip() for item in raw_agent if str(item).strip()
        )
        agent = positional_agents[0] if positional_agents else definition.default_agent
    else:
        positional_agents = ()
        agent = str(raw_agent or definition.default_agent).strip()
    model_agent = agent
    if definition.runtime_kind == "supervised_research":
        if isinstance(raw_agent, str) and raw_agent and raw_agent != "swarm":
            positional_agents = (raw_agent,)
        if not positional_agents and raw_research_agents:
            positional_agents = tuple(
                str(item).strip() for item in raw_research_agents if str(item).strip()
            )
        unsupported = [
            item for item in positional_agents if item not in SUPPORTED_AGENTS
        ]
        if unsupported:
            raise ValueError(f"Unsupported research agent: {unsupported[0]}")
        model_agent = positional_agents[0] if positional_agents else ""
        agent = "swarm"
    if agent not in SUPPORTED_AGENTS:
        raise ValueError(f"Unsupported agent: {agent}")

    prompt = str(payload.get("prompt") or "")
    file_path = str(payload.get("file") or "").strip()
    if not prompt and not file_path and not payload.get("input_explicit"):
        prompt = workflow_registry.workflow_default_prompt(skill)
    root = normalize_run_root(payload.get("root"), source_dir)
    runtime = _normalized_runtime(str(payload.get("runtime") or "headless").strip())
    mode = str(payload.get("mode") or skill).strip() or skill
    count = _coerce_positive_int(
        payload.get("count"), 3 if definition.supports_count else None
    )
    depth = _coerce_positive_int(
        payload.get("depth"), 3 if definition.supports_depth else None
    )
    if file_path and not Path(file_path).expanduser().is_file():
        raise ValueError(f"Prompt file does not exist or is not a file: {file_path}")
    plan_text = (
        Path(file_path).expanduser().read_bytes().decode("utf-8")
        if file_path
        else prompt
    )
    if (file_path or payload.get("input_explicit")) and not plan_text.strip():
        raise ValueError("explicit prompt input must not be empty")
    model, model_source = select_plan_model(
        model_agent,
        plan_text,
        model=payload.get("model") or payload.get("model_requested") or "",
    )
    if model and definition.runtime_kind == "supervised_research" and not model_agent:
        raise ValueError("research --model requires an explicit provider role")
    research_agents: tuple[str, ...] = ()
    research_synthesizer = ""
    research_synthesizer_model = str(
        payload.get("synthesizer_model")
        or payload.get("research_synthesizer_model")
        or ""
    ).strip()
    if definition.runtime_kind == "supervised_research":
        explicit_synthesizer = str(
            payload.get("synthesizer") or payload.get("research_synthesizer") or ""
        ).strip()
        if len(positional_agents) > 1:
            research_agents = positional_agents
            research_synthesizer = explicit_synthesizer or positional_agents[0]
        elif positional_agents:
            research_synthesizer = explicit_synthesizer or positional_agents[0]
        elif explicit_synthesizer:
            research_synthesizer = explicit_synthesizer
        if research_synthesizer and research_synthesizer not in SUPPORTED_AGENTS:
            raise ValueError(
                f"Unsupported research synthesizer: {research_synthesizer}"
            )

    if definition.requires_input and not prompt and not file_path:
        raise ValueError("Launch requires either --prompt text or --file path.")
    if file_path and not Path(file_path).expanduser().is_file():
        raise ValueError(f"Prompt file does not exist or is not a file: {file_path}")
    permissions = parse_permissions_word(payload.get("permissions"))
    sandbox = parse_sandbox_word(payload.get("sandbox"))
    if permissions or sandbox is not None:
        if definition.runtime_kind in SUPERVISED_RUNTIME_KINDS:
            raise ValueError(
                f"--permissions/--sandbox are not carried into the {skill} "
                "supervised runtime yet; omit them or launch a direct agent skill."
            )
        # Refuse before any control-plane write: the resolver raises with the
        # provider's exact supported alternative (never a silent downgrade).
        resolve_execution_controls(agent, permissions=permissions, sandbox=sandbox)

    execution = str(payload.get("runtime_class") or "")
    if execution and execution not in {"living-tree", "local-worktrees"}:
        raise ValueError(f"Unsupported execution runtime: {execution}; no host adapter")
    worktree = parse_worktree_flag(payload.get("worktree"))
    if execution:
        if payload.get("worktree") not in (None, "") and worktree != (
            execution == "local-worktrees"
        ):
            raise ValueError("--worktree conflicts with execution runtime")
        worktree = execution == "local-worktrees"
    requested_repo = str(payload.get("repo") or payload.get("root") or "")
    repo_kind = "path"
    identity_default = ""
    if payload.get("repo_selector"):
        raw_repo, raw_root = (
            str(payload.get("repo") or ""),
            str(payload.get("root") or ""),
        )
        if (
            raw_repo
            and raw_root
            and raw_repo != raw_root
            and Path(raw_repo).expanduser().resolve()
            != Path(raw_root).expanduser().resolve()
        ):
            raise ValueError("conflicting --repo and --root")
        chosen = raw_repo or raw_root
        if (
            chosen
            and not Path(chosen).expanduser().exists()
            and re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", chosen
            )
        ):
            repo_kind = "identity"
            root, identity_default = resolve_repository_identity(chosen)
        else:
            selected = select_repository(
                raw_repo, raw_root, fallback=Path.cwd, require_git=True
            )
            root = selected.git_toplevel
    top = git_toplevel(Path(root))
    if top and root:
        root = top
    base = str(payload.get("base") or "HEAD")
    effective_base = base
    if repo_kind == "identity":
        if base == "HEAD":
            effective_base = identity_default
        elif base.startswith("refs/heads/"):
            effective_base = base.replace("refs/heads/", "refs/remotes/origin/", 1)
        elif base.startswith("refs/tags/"):
            effective_base = base.replace("refs/tags/", "refs/vibecrafted/tags/", 1)
        elif not base.startswith("refs/"):
            candidates = []
            for candidate in (
                f"refs/remotes/origin/{base}",
                f"refs/vibecrafted/tags/{base}",
            ):
                try:
                    resolve_repository_base(root, candidate)
                    candidates.append(candidate)
                except ValueError:
                    pass
            if len(candidates) > 1 or (
                not candidates and not re.fullmatch(r"[0-9a-fA-F]{4,40}", base)
            ):
                raise ValueError(
                    "remote --base missing or ambiguous; use refs/heads/ or refs/tags/"
                )
            effective_base = candidates[0] if candidates else base
    resolved_ref, baseline_sha = (
        resolve_repository_base(root, effective_base) if top else ("", "")
    )
    if repo_kind == "identity" and baseline_sha:
        advertised = subprocess.run(
            [
                "git",
                "-C",
                root,
                "for-each-ref",
                f"--contains={baseline_sha}",
                "--format=%(refname)",
                "refs/remotes/origin/",
                "refs/vibecrafted/tags/",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if advertised.returncode or not advertised.stdout.strip():
            raise ValueError(
                "remote --base commit is not reachable from the refreshed source branches or tags; cached local objects are not a source baseline"
            )
    if payload.get("base") and not top:
        raise ValueError("--base requires a Git repository")
    if baseline_sha and not worktree and baseline_sha != _git_head(Path(root)):
        raise ValueError(
            "Living Tree --base differs from HEAD; select --execution-runtime local-worktrees"
        )
    if worktree and not root:
        raise ValueError("--worktree requires a selected repository (--repo <path>).")

    return WorkflowLaunchSpec(
        agent=agent,
        mode=mode,
        skill=skill,
        prompt=prompt,
        file=file_path,
        runtime=runtime,
        root=root,
        count=count,
        depth=depth,
        model=model,
        model_source=model_source,
        source_digest=hashlib.sha256(plan_text.encode("utf-8")).hexdigest(),
        repo_requested=requested_repo,
        repo_kind=repo_kind,
        base=base,
        baseline_sha=baseline_sha,
        resolved_ref=resolved_ref,
        runtime_class="local-worktrees" if worktree else "living-tree",
        research_model_agent=model_agent
        if definition.runtime_kind == "supervised_research"
        else "",
        research_agents=research_agents,
        research_synthesizer=research_synthesizer,
        research_synthesizer_model=research_synthesizer_model,
        run_id=str(payload.get("run_id") or "").strip(),
        worktree=worktree,
        permissions=permissions,
        sandbox=sandbox,
    )


def launch_execution_controls(spec: WorkflowLaunchSpec) -> ExecutionControls | None:
    """Resolved controls for a direct-agent launch; ``None`` for supervised kinds.

    Always computed (also when both flags were omitted) so every launch receipt
    states the effective permission policy and sandbox state next to what was
    requested. Raises :class:`ExecutionControlsError` for a refused request.
    """
    runtime_kind = workflow_registry.workflow_runtime_kind(spec.skill)
    if runtime_kind in SUPERVISED_RUNTIME_KINDS or spec.agent == "swarm":
        if spec.permissions or spec.sandbox is not None:
            raise ExecutionControlsError(
                f"--permissions/--sandbox are not carried into the {spec.skill} "
                "supervised runtime yet; omit them or launch a direct agent skill."
            )
        return None
    return resolve_execution_controls(
        spec.agent, permissions=spec.permissions, sandbox=spec.sandbox
    )


def _execution_controls_receipt(
    controls: ExecutionControls | None,
) -> dict[str, Any]:
    """Launch-event / meta / receipt projection of the resolved controls."""
    if controls is None:
        return {}
    return {"execution_controls": controls.receipt()}


def _git_head(repo: Path) -> str:
    """Current HEAD of ``repo`` or ``""`` when Git cannot answer."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _prepare_launch_worktree(
    spec: WorkflowLaunchSpec, run_id: str
) -> tuple[WorkflowLaunchSpec, dict[str, Any]]:
    """Materialize the ``--worktree`` checkout through the canonical manager.

    Reuses the same substrate as dispatch plans and interactive Agent
    Workspaces (``WorktreeManager.prepare_agent_launch``): a clean selected
    repository, a linked checkout under ``~/.vibecrafted/worktrees``, branch
    ``cut/<agent>-<run_id>`` at the selected HEAD. The receipt names both the
    checkout and the repository it was cut from. Raises ``ValueError`` with an
    operator-readable reason; the caller turns that into a refused launch.
    """
    from .dispatch.worktrees import WorktreeContractError, WorktreeManager

    parent = Path(spec.root or "").expanduser().resolve()
    toplevel = git_toplevel(parent)
    if not toplevel:
        raise ValueError(
            f"--worktree requires a Git repository; {parent} is not inside one"
        )
    if Path(toplevel) != parent:
        raise ValueError(
            "--worktree requires the selected repository root, not a "
            f"subdirectory: pass --repo {toplevel}"
        )
    baseline = spec.baseline_sha or _git_head(parent)
    if not baseline:
        raise ValueError(f"--worktree needs at least one commit in {parent}")
    manager = WorktreeManager(parent)
    try:
        geometry = manager.prepare_agent_launch(spec.agent, run_id, baseline)
    except WorktreeContractError as exc:
        raise ValueError(f"--worktree: {exc}") from exc
    worktree_path = str(Path(geometry.worktree_path).resolve())
    receipt: dict[str, Any] = {
        "worktree": True,
        "worktree_path": worktree_path,
        "worktree_branch": geometry.branch,
        "worktree_baseline_sha": geometry.baseline_sha,
        "parent_root": str(parent),
    }
    return (
        replace(spec, root=worktree_path, parent_root=str(parent)),
        receipt,
    )


def _source_prompt(spec: WorkflowLaunchSpec) -> str:
    """Read exact input, refusing a file changed since model admission."""
    text = (
        spec.plan_source
        if spec.plan_source is not None
        else (
            Path(spec.file).expanduser().read_bytes().decode("utf-8")
            if spec.file
            else spec.prompt
        )
    )
    if (
        spec.source_digest
        and hashlib.sha256(text.encode("utf-8")).hexdigest() != spec.source_digest
    ):
        raise ValueError("plan source changed after admission; submit a new launch")
    return text


def _runtime_prompt(
    spec: WorkflowLaunchSpec, *, source_prompt: str | None = None
) -> str:
    """Wrap the source prompt in the runtime contract instructions given to the worker."""
    report_hint = "${VIBECRAFTED_REPORT_PATH}"
    transcript_hint = "${VIBECRAFTED_TRANSCRIPT_PATH}"
    if source_prompt is None:
        source_prompt = _source_prompt(spec)
    # Resume is a payload of the init pass, not a verb someone has to remember.
    # The block is empty on a clean checkout, so it costs nothing when there is
    # no unfinished work; `init_resume_block` never raises.
    resume_block = init_resume_block(spec.root)
    resume_section = f"\n{resume_block}\n" if resume_block else ""
    return f"""You are running under Vibecrafted core runtime.

Contract:
- Work in repository root: {spec.root}
- Skill: vc-{spec.skill}
- Agent request: {spec.agent}
- Mode: {spec.mode}
- Runtime request: {spec.runtime}
- Count: {spec.count or ""}
- Depth: {spec.depth or ""}
- Do not launch or delegate to external agent fleets.
- Do not call legacy Vibecrafted skill launchers or runtime/scripts launchers.
- Write your final report to the path in VIBECRAFTED_REPORT_PATH ({report_hint}).
- The launcher pre-seeds that report's YAML frontmatter with machine-owned
  `run_id` and `session_id`; preserve those values and never copy or guess identity.
- Edit the existing frontmatter, keeping `finalized: false` until you deliberately
  attest success with `finalized: true` plus a non-empty `claim`.
- Keep non-empty `agent`, `skill`, and `status` keys.
- Preserve an honest blocked/partial/failed status.
- Let stdout/stderr form the transcript captured at VIBECRAFTED_TRANSCRIPT_PATH ({transcript_hint}).
- Do not create, overwrite, or summarize run metadata yourself. The runtime owns VIBECRAFTED_META_PATH.
{WORKER_SIGNAL_DISCIPLINE.rstrip()}

Step 0 — orient before you touch (the vc-init pass).
The operator prompt below is one framing — a hypothesis, not the ground truth. Reading a
couple of files and feeling oriented is the trap: you'd cut from a partial, self-picked slice,
and that is where silent drift gets in. So before any skill-specific work, run the vc-init
due-diligence right here in this thread, because it is what makes every later move land:
- Map: materialize the Loctree context atlas for {spec.root} and read it to the END
  (entrypoints, blast radius, twins, dead surfaces, the real shape).
- Intent: recover the AICX history — why the code became this, what was already tried.
- Ground truth + risk: git/security sanity, then grade the blast radius before changing behavior.
Your skill (vc-{spec.skill}) carries its own orientation gate — this is it. Not a rule barked at
you: it is simply the move that separates a real cut from a confident guess, and it pays for
itself in the very next step.
{resume_section}
Operator prompt:
{source_prompt}
"""


def _write_prompt_file(path: Path, body: str) -> Path:
    """Write the assembled prompt body to disk and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(body.encode("utf-8"))
    return path


def build_launch_command(
    spec: WorkflowLaunchSpec,
    _source_dir: str | Path,
    *,
    prompt_file: str | Path | None = None,
) -> list[str]:
    """Build the worker argv for a launch spec, branching on the workflow's runtime kind."""
    prompt_path = str(prompt_file or spec.file or "")
    runtime_kind = workflow_registry.workflow_runtime_kind(spec.skill)
    if runtime_kind == "supervised_research":
        command = (
            "research-synthesis"
            if spec.runtime in {"terminal", "visible"}
            else "research"
        )
        launch_command = [
            sys.executable,
            "-m",
            "vibecrafted_core.workflow_runtime",
            command,
            "--root",
            spec.root,
            "--prompt-file",
            prompt_path,
        ]
        if spec.model:
            launch_command.extend(["--model", spec.model])
        if spec.research_synthesizer:
            launch_command.extend(["--synthesizer", spec.research_synthesizer])
        if spec.research_synthesizer_model:
            launch_command.extend(
                ["--synthesizer-model", spec.research_synthesizer_model]
            )
        return launch_command
    if runtime_kind == "supervised_marbles":
        launch_command = [
            sys.executable,
            "-m",
            "vibecrafted_core.workflow_runtime",
            "marbles",
            "--workflow",
            spec.skill,
            "--agent",
            spec.agent if spec.agent != "swarm" else "codex",
            "--root",
            spec.root,
            "--prompt-file",
            prompt_path,
            "--count",
            str(spec.count or 3),
            "--depth",
            str(spec.depth or 3),
        ]
        if spec.model:
            launch_command.extend(["--model", spec.model])
        return launch_command

    worker_agent = spec.agent
    controls = launch_execution_controls(spec)
    # The default shape stays byte-identical to the historical command; the
    # resolved argv is injected only when the caller asked for a control.
    if controls is not None and controls.requested:
        worker_command = _stdin_command(worker_agent, controls=controls)
    else:
        worker_command = _stdin_command(worker_agent)
    return _with_model_override(worker_agent, worker_command, spec.model)


def _sweep_stale_runs() -> None:
    """Reap survivors of terminal runs. Never raises, never blocks a launch."""
    try:
        from .run_reaper import sweep_quietly

        sweep_quietly()
    except Exception as _sweep_exc:  # noqa: BLE001
        _ = _sweep_exc  # best-effort reaper sweep


def _launch_tracking_payload(
    launch_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the lineage/identity fields safe to project into launch events."""

    if not launch_meta:
        return {}
    return {
        key: launch_meta[key]
        for key in (
            "agent_session_id",
            "runtime_session_id",
            "parent_runtime_session_id",
            "resume_of",
            "resume_root",
            "attempt",
            "native_resume",
            "native_fork",
            "fork_source_session_id",
            "session_selection",
            "parent_run_id",
            "resume_idempotency_key",
            "dispatch_run_id",
            "dispatch_cut_id",
            "dispatch_branch",
            "dispatch_baseline_sha",
            "dispatch_attempt",
            "dispatch_idempotency_key",
        )
        if launch_meta.get(key) not in (None, "")
    }


def _json_plain(value: Any) -> Any:
    """Reduce a launch payload to JSON-serializable builtins."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_plain(item) for item in value]
    return str(value)


def machine_launch_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the one stdout-safe launch receipt operators and agents parse."""
    accepted = bool(payload.get("accepted"))
    status = str(
        payload.get("status")
        or ("launching" if accepted else payload.get("reason") or "rejected")
    )
    agent = str(payload.get("agent") or "")
    receipt: dict[str, Any] = {
        "schema": LAUNCH_RECEIPT_SCHEMA,
        "run_id": str(payload.get("run_id") or ""),
        "agent": agent,
        "skill": str(payload.get("skill") or ""),
        "root": str(payload.get("root") or ""),
        "accepted": accepted,
        "status": status,
        "replayed": bool(payload.get("replayed")),
        "idempotency_key": str(payload.get("idempotency_key") or ""),
    }
    if payload.get("worktree"):
        receipt["worktree"] = True
        for key in (
            "worktree_path",
            "worktree_branch",
            "worktree_baseline_sha",
            "parent_root",
        ):
            receipt[key] = str(payload.get(key) or "")
    if isinstance(payload.get("execution_controls"), dict):
        receipt["execution_controls"] = dict(payload["execution_controls"])
    for key in (
        "model_requested",
        "model_effective",
        "model_source",
        "repo_requested",
        "repo_kind",
        "base_requested",
        "resolved_ref",
        "baseline_sha",
        "runtime_class",
        "presentation",
        "source_path",
        "source_origin",
        "source_snapshot",
        "source_digest",
    ):
        receipt[key] = payload.get(key, "")
    for key in (
        "report",
        "transcript",
        "meta",
        "parent_root",
        "effective_worker_root",
        "parent_run_id",
        "agent_session_id",
        "requires_pty",
        "execution_host",
        "repository_identity",
        "remote",
        "source_ref",
        "reason",
        "error",
        "launch_phase",
    ):
        if key in payload:
            receipt[key] = _json_plain(payload[key])
    return receipt


def _launch_idempotency_enabled() -> bool:
    """Whether one logical launch may reuse a live run instead of minting a sibling."""
    raw = str(os.environ.get("VIBECRAFTED_LAUNCH_IDEMPOTENCY", "1")).strip().lower()
    return raw not in {"0", "false", "off", "no"}


def launch_idempotency_key(
    spec: WorkflowLaunchSpec, *, env: dict[str, str] | None = None
) -> str:
    """Return caller-supplied launch identity, or ``""`` for a fresh invocation.

    Byte-identical content is never operator-intention identity. Transports may
    supply the existing ``VIBECRAFTED_LAUNCH_IDEMPOTENCY_KEY`` across retries;
    an explicit ``spec.run_id`` is also a stable caller-owned identity.
    """
    source = os.environ if env is None else env
    override = str(source.get(LAUNCH_IDEMPOTENCY_KEY_ENV) or "").strip()
    if override:
        return override
    run_id = str(spec.run_id or "").strip()
    return f"run-id:{run_id}" if run_id else ""


def _launch_spec_digest(spec: WorkflowLaunchSpec) -> str:
    """Bind one explicit invocation identity to secret-safe launch semantics."""
    prompt = _source_prompt(spec)
    material = {
        **spec.to_payload(),
        "prompt": "",
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "root": str(Path(spec.root or "").expanduser().resolve(strict=False)),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _legacy_preassembly_spec(
    stored_spec: WorkflowLaunchSpec, *, expected_digest: str
) -> WorkflowLaunchSpec | None:
    """Recover a prompt-origin spec from an old redacted launch receipt.

    Pre-dispatch runtimes calculated the idempotency digest before wrapping the
    caller's prompt, but persisted only ``safe_spec`` afterwards.  That shape
    has an empty ``prompt`` and a generated ``prompt.md`` in ``file``.  The
    generated wrapper retains the original prompt after its unambiguous final
    ``Operator prompt:`` delimiter.  Rebuild the old material only when its
    digest still exactly matches the immutable stored digest.  File-origin
    launches cannot safely recover their original file path and remain denied.
    """
    if stored_spec.prompt or not stored_spec.file:
        return None
    try:
        rendered = (
            Path(stored_spec.file)
            .expanduser()
            .read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return None
    marker = "Operator prompt:\n"
    if not rendered.startswith("You are running under Vibecrafted core runtime.\n"):
        return None
    _wrapper, separator, source_with_newline = rendered.partition(marker)
    # _runtime_prompt always appends precisely one newline after the source.
    # Do not normalize whitespace: it is part of the authenticated source.
    if not separator or not source_with_newline.endswith("\n"):
        return None
    recovered = replace(
        stored_spec,
        prompt=source_with_newline[:-1],
        file="",
    )
    return recovered if _launch_spec_digest(recovered) == expected_digest else None


def _launch_idempotency_registry() -> Path:
    """Directory for launch-idempotency records under the control-plane home."""
    registry = control_plane_home() / "launch_idempotency"
    registry.mkdir(parents=True, exist_ok=True)
    return registry


def _launch_idempotency_path(key: str) -> Path:
    """Content-addressed path for one launch fingerprint."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return _launch_idempotency_registry() / f"{digest}.json"


def _archive_launch_idempotency_record(
    key: str,
    record: dict[str, Any],
    *,
    replacement_spec_digest: str,
) -> Path:
    """Preserve a terminal stale claim before reserving its identity again."""
    key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    record_digest = hashlib.sha256(
        json.dumps(_json_plain(record), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    archived_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    target = (
        _launch_idempotency_registry()
        / "archive"
        / f"{key_digest}-{time.time_ns()}-{record_digest[:16]}.json"
    )
    atomic_write_json(
        target,
        {
            **_json_plain(record),
            "archived_at": archived_at,
            "archive_reason": "terminal_stale_spec_replaced",
            "replacement_spec_digest": replacement_spec_digest,
        },
    )
    return target


def _read_launch_idempotency_record(key: str) -> dict[str, Any]:
    """Read one launch-idempotency record, or ``{}`` when absent/invalid."""
    if not key:
        return {}
    path = _launch_idempotency_path(key)
    payload = _read_json_object(path)
    if not payload:
        return {}
    if payload.get("schema") != LAUNCH_IDEMPOTENCY_SCHEMA:
        return {}
    if str(payload.get("idempotency_key") or "") != key:
        return {}
    return payload


def _write_launch_idempotency_record(key: str, payload: dict[str, Any]) -> None:
    """Atomically persist a launch-idempotency record for ``key``."""
    if not key:
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record = {
        "schema": LAUNCH_IDEMPOTENCY_SCHEMA,
        "idempotency_key": key,
        "run_id": str(payload.get("run_id") or ""),
        "agent": str(payload.get("agent") or ""),
        "skill": str(payload.get("skill") or ""),
        "root": str(payload.get("root") or ""),
        "state": str(payload.get("state") or "reserved"),
        "accepted": bool(payload.get("accepted")),
        "owner_pid": int(payload.get("owner_pid") or os.getpid()),
        "owner_identity": _json_plain(payload.get("owner_identity")),
        "spec_digest": str(payload.get("spec_digest") or ""),
        "receipt": _json_plain(payload.get("receipt") or {}),
        "updated_at": now,
    }
    with run_mutation_locks(control_plane_home(), run_id="launch-idempotency-registry"):
        if payload.get("created_at"):
            record["created_at"] = str(payload["created_at"])
        else:
            existing = _read_launch_idempotency_record(key)
            record["created_at"] = str(existing.get("created_at") or now)
        atomic_write_json(_launch_idempotency_path(key), record)


def _prune_launch_idempotency_registry(*, now: float | None = None) -> int:
    """Bound failed/terminal history without deleting live or ambiguous claims."""
    with run_mutation_locks(control_plane_home(), run_id="launch-idempotency-registry"):
        registry = _launch_idempotency_registry()
        current_time = time.time() if now is None else now
        eligible: list[tuple[float, Path]] = []
        for path in registry.glob("*.json"):
            payload = _read_json_object(path)
            state = str(payload.get("state") or "")
            if state == "failed":
                pass
            elif state == "dispatched":
                run_id = str(payload.get("run_id") or "")
                run = lookup_run(run_id) if run_id else None
                if run is None or not _run_is_terminal(run):
                    continue
            else:
                continue
            try:
                modified_at = path.stat().st_mtime
            except OSError:
                continue
            eligible.append((modified_at, path))

        eligible.sort(key=lambda item: item[0], reverse=True)
        removed = 0
        for index, (modified_at, path) in enumerate(eligible):
            expired = (
                current_time - modified_at > LAUNCH_IDEMPOTENCY_TERMINAL_TTL_SECONDS
            )
            over_limit = index >= LAUNCH_IDEMPOTENCY_MAX_TERMINAL_RECORDS
            if not expired and not over_limit:
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                continue
            removed += 1
        return removed


def _replay_launch_payload(record: dict[str, Any], *, status: str) -> dict[str, Any]:
    """Rebuild a launch receipt from a stored idempotency record."""
    stored = dict(record.get("receipt") or {})
    run_id = str(stored.get("run_id") or record.get("run_id") or "")
    payload = {
        **stored,
        "run_id": run_id,
        "agent": str(stored.get("agent") or record.get("agent") or ""),
        "skill": str(stored.get("skill") or record.get("skill") or ""),
        "root": str(stored.get("root") or record.get("root") or ""),
        "accepted": bool(stored.get("accepted", record.get("accepted"))),
        "status": str(stored.get("status") or status),
        "replayed": True,
        "idempotency_key": str(record.get("idempotency_key") or ""),
        "message": str(
            stored.get("message") or f"Replayed launch for {run_id or 'existing run'}"
        ),
    }
    return payload


def _retryable_launch_payload(
    record: dict[str, Any], *, status: str, reason: str
) -> dict[str, Any]:
    """Return a structured refusal for an identity that lacks launch proof."""
    stored = dict(record.get("receipt") or {})
    return {
        **stored,
        "run_id": str(stored.get("run_id") or record.get("run_id") or ""),
        "agent": str(stored.get("agent") or record.get("agent") or ""),
        "skill": str(stored.get("skill") or record.get("skill") or ""),
        "root": str(stored.get("root") or record.get("root") or ""),
        "accepted": False,
        "status": status,
        "retryable": True,
        "replayed": False,
        "idempotency_key": str(record.get("idempotency_key") or ""),
        "reason": reason,
        "message": reason,
    }


def _canonical_run_launch_payload(
    record: dict[str, Any], run: dict[str, Any]
) -> dict[str, Any]:
    """Recover acceptance only from a canonical run with qualified liveness."""
    stored = dict(record.get("receipt") or {})
    run_id = str(run.get("run_id") or record.get("run_id") or "")
    payload = {
        **stored,
        "run_id": run_id,
        "agent": str(run.get("agent") or record.get("agent") or ""),
        "skill": str(run.get("skill") or record.get("skill") or ""),
        "root": str(run.get("root") or record.get("root") or ""),
        "accepted": True,
        "status": str(run.get("state") or "launching"),
        "replayed": True,
        "recovered": True,
        "idempotency_key": str(record.get("idempotency_key") or ""),
        "message": f"Recovered canonical launch for {run_id}",
    }
    for field in (
        "report",
        "transcript",
        "meta",
        "launcher_pid",
        "launcher_identity",
        "worker_pid",
        "worker_identity",
    ):
        if run.get(field) is not None:
            payload[field] = _json_plain(run[field])
    return payload


def _classify_record_owner(record: dict[str, Any]) -> tuple[str, str]:
    """Classify reservation ownership without turning ambiguity into staleness."""
    run_id = str(record.get("run_id") or "")
    try:
        owner_pid = int(record.get("owner_pid") or 0)
    except (TypeError, ValueError):
        return "ambiguous", "process_identity_invalid"
    receipt = record.get("owner_identity")
    if owner_pid <= 0 or not isinstance(receipt, dict):
        return "ambiguous", "process_identity_unavailable"
    expected_pgid = receipt.get("pgid")
    try:
        pgid = int(expected_pgid) if expected_pgid is not None else None
    except (TypeError, ValueError):
        return "ambiguous", "process_identity_invalid"
    current, reason, _identity = validate_process_identity(
        receipt,
        expected_pid=owner_pid,
        expected_pgid=pgid,
        expected_run_id=run_id,
    )
    if current:
        return "current", reason or "process_identity_current"
    if reason in {
        "process_identity_gone",
        "process_identity_mismatch",
        "process_run_id_mismatch",
    }:
        return "stale", reason
    return "ambiguous", reason or "process_identity_unknown"


def _run_has_current_process_proof(run_id: str, run: dict[str, Any]) -> bool:
    """Require a current canonical worker/launcher identity for active replay."""
    for prefix in ("worker", "launcher"):
        receipt = run.get(f"{prefix}_identity")
        if not isinstance(receipt, dict):
            continue
        raw_pid = run.get(f"{prefix}_pid") or receipt.get("pid")
        try:
            pid = int(raw_pid or 0)
            pgid = int(receipt.get("pgid") or 0)
        except (TypeError, ValueError):
            continue
        current, _reason, _identity = validate_process_identity(
            receipt,
            expected_pid=pid,
            expected_pgid=pgid or None,
            expected_run_id=run_id,
        )
        if current:
            return True
    return False


def _replay_launch_if_current(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return a replay receipt when retrying would mint a sibling of a live launch."""
    if not record:
        return None
    run_id = str(record.get("run_id") or "")
    if not run_id:
        return None
    state = str(record.get("state") or "")
    if state == "reserved":
        owner_state, owner_reason = _classify_record_owner(record)
        if owner_state == "current":
            return _retryable_launch_payload(
                record,
                status="reservation_in_progress",
                reason="launch reservation is owned by another live invocation",
            )
        run = lookup_run(run_id)
        if run is not None and (
            _run_is_terminal(run) or _run_has_current_process_proof(run_id, run)
        ):
            return _canonical_run_launch_payload(record, run)
        if owner_state == "ambiguous":
            return _retryable_launch_payload(
                record,
                status="retryable_reservation_owner_ambiguous",
                reason=owner_reason,
            )
        if run is None:
            return None
        return _retryable_launch_payload(
            record,
            status="retryable_unproven_liveness",
            reason="reserved run exists but has no current canonical process proof",
        )
    if state != "dispatched":
        return None
    run = lookup_run(run_id)
    if run is None:
        return _retryable_launch_payload(
            record,
            status="retryable_unknown_run",
            reason="idempotent run is unknown to control_plane",
        )
    if _run_is_terminal(run):
        return _replay_launch_payload(
            record, status=str(run.get("state") or "completed")
        )
    stored_receipt = dict(record.get("receipt") or {})
    if not (
        _run_has_current_process_proof(run_id, run)
        or _run_has_current_process_proof(run_id, stored_receipt)
    ):
        return _retryable_launch_payload(
            record,
            status="retryable_unproven_liveness",
            reason="idempotent run has no current canonical process proof",
        )
    return _replay_launch_payload(record, status=str(run.get("state") or "launching"))


def _claim_launch_idempotency(
    spec: WorkflowLaunchSpec, key: str, *, spec_digest: str
) -> tuple[dict[str, Any] | None, str]:
    """Under the caller lock: replay a live launch or reserve one run id."""
    existing = _read_launch_idempotency_record(key)
    existing_digest = str(existing.get("spec_digest") or "")
    if existing and not existing_digest:
        raise ValueError("idempotency identity conflicts with a different launch spec")
    if existing and existing_digest != spec_digest:
        run_id = str(existing.get("run_id") or "")
        run = lookup_run(run_id) if run_id else None
        owner_state, owner_reason = _classify_record_owner(existing)
        run_is_current = bool(
            run is not None and _run_has_current_process_proof(run_id, run)
        )
        if owner_state == "current" or run_is_current:
            raise ValueError(
                "idempotency identity conflicts with a different launch spec"
            )
        if run is not None and _run_is_terminal(run) and owner_state == "stale":
            _archive_launch_idempotency_record(
                key,
                existing,
                replacement_spec_digest=spec_digest,
            )
            existing = {}
        else:
            return (
                _retryable_launch_payload(
                    existing,
                    status="retryable_idempotency_spec_conflict_unproven",
                    reason=(
                        "idempotency spec changed without terminal stale ownership proof: "
                        f"{owner_reason}"
                    ),
                ),
                run_id,
            )
    replay = _replay_launch_if_current(existing)
    if replay is not None:
        if replay.get("accepted") and str(existing.get("state") or "") == "reserved":
            replay = _finish_launch_idempotency(key, replay, spec_digest=spec_digest)
        return replay, str(existing.get("run_id") or "")
    reuse = ""
    if str(existing.get("state") or "") == "reserved":
        reuse = str(existing.get("run_id") or "")
    run_id = str(spec.run_id or reuse or "") or reserve_run_id(spec.skill)
    _write_launch_idempotency_record(
        key,
        {
            "run_id": run_id,
            "agent": spec.agent,
            "skill": spec.skill,
            "root": spec.root,
            "state": "reserved",
            "accepted": False,
            "owner_pid": os.getpid(),
            "owner_identity": process_identity_receipt(os.getpid(), run_id=run_id),
            "spec_digest": spec_digest,
            "receipt": {
                "run_id": run_id,
                "agent": spec.agent,
                "skill": spec.skill,
                "root": spec.root,
                "accepted": False,
                "status": "reserved",
            },
        },
    )
    return None, run_id


def _finish_launch_idempotency(
    key: str, payload: dict[str, Any], *, spec_digest: str = ""
) -> dict[str, Any]:
    """Persist the launch receipt onto the fingerprint, then return it."""
    if not key:
        return payload
    accepted = bool(payload.get("accepted"))
    result = {**payload, "idempotency_key": key}
    _write_launch_idempotency_record(
        key,
        {
            "run_id": str(result.get("run_id") or ""),
            "agent": str(result.get("agent") or ""),
            "skill": str(result.get("skill") or ""),
            "root": str(result.get("root") or ""),
            "state": "dispatched" if accepted else "failed",
            "accepted": accepted,
            "owner_pid": os.getpid(),
            "owner_identity": process_identity_receipt(
                os.getpid(), run_id=str(result.get("run_id") or "")
            ),
            "spec_digest": spec_digest,
            "receipt": _json_plain(result),
        },
    )
    _prune_launch_idempotency_registry()
    return result


def recover_launch_receipt(
    spec: WorkflowLaunchSpec, *, env: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Return a stored receipt for ``spec`` when a prior launch already reserved a run."""
    if not _launch_idempotency_enabled():
        return None
    key = launch_idempotency_key(spec, env=env)
    if not key:
        return None
    record = _read_launch_idempotency_record(key)
    expected_digest = _launch_spec_digest(spec)
    if record and str(record.get("spec_digest") or "") != expected_digest:
        return _retryable_launch_payload(
            record,
            status="idempotency_conflict",
            reason="idempotency identity conflicts with a different launch spec",
        )
    if not record.get("run_id"):
        return None
    replay = _replay_launch_if_current(record)
    if replay is not None:
        return replay
    stored = dict(record.get("receipt") or {})
    run_id = str(stored.get("run_id") or record.get("run_id") or "")
    if not run_id:
        return None
    stored["run_id"] = run_id
    stored["replayed"] = True
    stored["idempotency_key"] = key
    dispatched = str(record.get("state") or "") == "dispatched"
    stored["accepted"] = bool(record.get("accepted")) and dispatched
    stored.setdefault("status", "launching" if stored["accepted"] else "failed")
    return stored


def _legacy_dispatch_receipt_matches(
    *,
    key: str,
    provider_run_id: str,
    cut_id: str,
    root: str,
    branch: str,
    baseline_sha: str,
) -> bool:
    """Verify legacy dispatch identity from its exact durable scheduler receipt.

    Old run projections can lack the dispatch fields, but their launch key is
    still namespaced by the parent dispatch.  Never search receipts by a loose
    provider id: derive one path from that authenticated key and require every
    topology field to agree before using it as the missing projection.
    """
    suffix = f":cut:{cut_id}:attempt:initial"
    if not key.startswith("dispatch:") or not key.endswith(suffix):
        return False
    dispatch_run_id = key[len("dispatch:") : -len(suffix)]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", dispatch_run_id):
        return False
    ledger = _read_json_object(
        control_plane_home() / "dispatches" / dispatch_run_id / "receipts.json"
    )
    cuts = ledger.get("cuts")
    receipt = cuts.get(cut_id) if isinstance(cuts, dict) else None
    if not isinstance(receipt, dict):
        return False
    if not (
        ledger.get("schema") == "vibecrafted.dispatch-receipts.v1"
        and str(ledger.get("run_id") or "") == dispatch_run_id
        and str(receipt.get("cut_id") or "") == cut_id
        and str(receipt.get("provider_run_id") or "") == provider_run_id
        and str(receipt.get("branch") or "") == branch
        and str(receipt.get("baseline_sha") or "") == baseline_sha
    ):
        return False
    try:
        return Path(str(receipt.get("worktree_path") or "")).resolve(
            strict=False
        ) == Path(root).resolve(strict=False)
    except OSError:
        return False


def _legacy_worktree_matches(*, root: str, branch: str, baseline_sha: str) -> bool:
    """Confirm a recovered receipt still names the original linked checkout.

    A worker can legitimately commit its owned work after the dispatch baseline.
    The baseline is therefore an ancestry floor, not an exact ``HEAD`` value;
    staged and unstaged progress must remain untouched by recovery as well.
    """
    try:
        top = subprocess.run(
            ["git", "-C", root, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        observed_branch = subprocess.run(
            ["git", "-C", root, "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        baseline_is_ancestor = (
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "merge-base",
                    "--is-ancestor",
                    baseline_sha,
                    "HEAD",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return (
        Path(top).resolve(strict=False) == Path(root).resolve(strict=False)
        and observed_branch == branch
        and baseline_is_ancestor
    )


def recover_legacy_dispatch_identity(
    spec: WorkflowLaunchSpec,
    *,
    env: dict[str, str],
    provider_run_id: str,
    cut_id: str,
    branch: str,
    baseline_sha: str,
) -> tuple[dict[str, Any] | None, str]:
    """Authenticate a pre-dispatch-metadata launch from its durable record.

    Older core runtimes persisted the launch idempotency record before they
    projected ``dispatch_*`` fields into ``meta.json``.  Those fields must not
    be guessed on resume: this recovery path accepts the old shape only when
    the exact caller-owned key, immutable stored spec digest, stored receipt,
    and current canonical run projection all describe the same launch.
    """
    key = launch_idempotency_key(spec, env=env)
    if not key:
        return None, "legacy dispatch recovery has no idempotency key"
    record = _read_launch_idempotency_record(key)
    if not record:
        return None, "legacy dispatch idempotency record is absent"
    receipt = record.get("receipt")
    if not isinstance(receipt, dict):
        return None, "legacy dispatch idempotency receipt is invalid"
    stored_spec = receipt.get("spec")
    if not isinstance(stored_spec, dict):
        return None, "legacy dispatch idempotency spec is invalid"
    try:
        historical = WorkflowLaunchSpec(
            agent=str(stored_spec["agent"]),
            mode=str(stored_spec["mode"]),
            skill=str(stored_spec["skill"]),
            prompt=str(stored_spec.get("prompt") or ""),
            file=str(stored_spec.get("file") or ""),
            runtime=str(stored_spec["runtime"]),
            root=str(stored_spec["root"]),
            count=stored_spec.get("count"),
            depth=stored_spec.get("depth"),
            model=str(stored_spec.get("model") or ""),
            research_agents=tuple(stored_spec.get("research_agents") or ()),
            research_synthesizer=str(stored_spec.get("research_synthesizer") or ""),
            research_synthesizer_model=str(
                stored_spec.get("research_synthesizer_model") or ""
            ),
            lifecycle_state_path=str(stored_spec.get("lifecycle_state_path") or ""),
            claim_digest=str(stored_spec.get("claim_digest") or ""),
            run_id=str(stored_spec.get("run_id") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None, "legacy dispatch idempotency spec is incomplete"
    stored_digest = str(record.get("spec_digest") or "")
    bound_spec = historical
    if _launch_spec_digest(bound_spec) != stored_digest:
        recovered_spec = _legacy_preassembly_spec(
            historical, expected_digest=stored_digest
        )
        if recovered_spec is None:
            return None, "legacy dispatch idempotency record does not bind its launch"
        bound_spec = recovered_spec
    if (
        record.get("schema") != LAUNCH_IDEMPOTENCY_SCHEMA
        or str(record.get("idempotency_key") or "") != key
        or str(record.get("state") or "") != "dispatched"
        or record.get("accepted") is not True
        or not stored_digest
        or _launch_spec_digest(bound_spec) != stored_digest
    ):
        return None, "legacy dispatch idempotency record does not bind its launch"
    root = str(Path(spec.root).expanduser().resolve(strict=False))
    historical_root = str(Path(historical.root).expanduser().resolve(strict=False))
    if not (
        str(record.get("run_id") or "") == provider_run_id
        and str(receipt.get("run_id") or "") == provider_run_id
        and str(receipt.get("idempotency_key") or "") == key
        and str(record.get("agent") or "").lower() == spec.agent.lower()
        and str(record.get("skill") or "") == spec.skill
        and str(record.get("root") or "") == root
        and historical.agent.lower() == spec.agent.lower()
        and historical.skill == spec.skill
        and historical_root == root
    ):
        return None, "legacy dispatch record conflicts with the requested cut"
    if not _legacy_dispatch_receipt_matches(
        key=key,
        provider_run_id=provider_run_id,
        cut_id=cut_id,
        root=root,
        branch=branch,
        baseline_sha=baseline_sha,
    ):
        return None, "legacy dispatch receipt identity is incomplete or conflicts"
    if not _legacy_worktree_matches(
        root=root, branch=branch, baseline_sha=baseline_sha
    ):
        return None, "legacy dispatch worktree identity is incomplete or conflicts"
    canonical = lookup_run(provider_run_id)
    observed_root = str(
        (canonical or {}).get("resolved_worktree_path")
        or (canonical or {}).get("root")
        or ""
    )
    worker_identity = (canonical or {}).get("worker_identity")
    worker_pid = (canonical or {}).get("worker_pid")
    if not (
        isinstance(canonical, dict)
        and str(canonical.get("run_id") or "") == provider_run_id
        and observed_root
        and Path(observed_root).resolve(strict=False)
        == Path(root).resolve(strict=False)
        and (not canonical.get("branch") or str(canonical["branch"]) == branch)
        and (
            not canonical.get("baseline_sha")
            or str(canonical["baseline_sha"]) == baseline_sha
        )
        and (not canonical.get("cut_id") or str(canonical["cut_id"]) == cut_id)
        and str(canonical.get("agent") or "").lower() == spec.agent.lower()
        and str(canonical.get("skill") or "") == spec.skill
        and canonical.get("worker_alive") is False
        and isinstance(worker_pid, int)
        and isinstance(worker_identity, dict)
        and worker_identity.get("run_id") == provider_run_id
        and worker_identity.get("pid") == worker_pid
        and worker_identity.get("pgid")
        and worker_identity.get("start_token")
        and worker_identity.get("command_sha256")
    ):
        return None, "legacy dispatch canonical identity is incomplete or conflicts"
    return {
        "provider_run_id": provider_run_id,
        "idempotency_key": key,
        "spec_digest": str(record["spec_digest"]),
    }, ""


def launch_workflow(
    spec: WorkflowLaunchSpec,
    source_dir: str | Path,
    *,
    env: dict[str, str] | None = None,
    retry_of: str = "",
    worker_command_override: list[str] | None = None,
    launch_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Launch one workflow run end-to-end and return its launch acceptance receipt.

    Sweeps stale runs, enforces vc-guard continuation, allocates a run id and
    artifact paths, writes the prompt/dispatcher scripts, spawns the transport
    (headless subprocess or a vc-frame host action tab), and records the launch
    lifecycle events. Never blocks on the spawned run reaching a terminal state
    — control-plane reconciliation is deliberately deferred to observe/await.
    """
    _normalized_runtime(spec.runtime)
    if spec.runtime_class not in {"living-tree", "local-worktrees"}:
        raise ValueError("unsupported execution runtime; no host adapter")
    _source_prompt(spec)  # refuse source drift before any mutation
    # Opportunistic pre-flight: before adding a run to the machine, take the dead
    # ones' survivors off it. Every spawn is the natural sweep point — it needs no
    # daemon, and it is exactly when the residue starts costing the new run cores.
    # Silent and best-effort; a reaper problem must never block a launch.
    _sweep_stale_runs()

    # vc-guard proof path: refuse continuation when trust has block on HEAD.
    # Guard never invents settlement; only consumes trust journal. Opt-out via
    # VIBECRAFTED_GUARD=0 for hermetic tests that are not about enforcement.
    if str(os.environ.get("VIBECRAFTED_GUARD", "1")).strip() not in {
        "0",
        "false",
        "off",
        "no",
    }:
        try:
            from . import guard as guard_mod

            root = Path(spec.root or source_dir or Path.cwd())
            decision = guard_mod.enforce_continuation(
                repo=root,
                skill=str(spec.skill or ""),
            )
            if not decision.allowed:
                raise ValueError(decision.remedium or "vc-guard refused continuation")
        except ImportError:
            pass
        except (ValueError, OSError) as exc:
            # Non-git fixtures, sandbox roots, and hermetic tests that stub
            # subprocess: do not hard-fail launch unless the error is an
            # explicit guard refusal (remedium text).
            message = str(exc)
            if (
                "vc-guard" in message
                or "Remedium" in message
                or "trust recorded block" in message
            ):
                raise
            # not a git repository / stubbed subprocess / unreadable context → allow

    idem_key = ""
    idem_spec_digest = ""
    claimed_run_id = ""
    if _launch_idempotency_enabled():
        effective_identity_env = dict(os.environ)
        if env:
            effective_identity_env.update(env)
        idem_key = launch_idempotency_key(spec, env=effective_identity_env)
    if idem_key:
        idem_spec_digest = _launch_spec_digest(spec)
        digest = hashlib.sha256(idem_key.encode("utf-8")).hexdigest()
        with run_mutation_locks(
            control_plane_home(),
            run_id=f"lidem-{digest[:24]}",
            idempotency_key=idem_key,
        ):
            replay, claimed_run_id = _claim_launch_idempotency(
                spec, idem_key, spec_digest=idem_spec_digest
            )
            if replay is not None:
                return replay

    run_id = spec.run_id or claimed_run_id or reserve_run_id(spec.skill)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise ValueError("run_id must be a safe 1-128 character identifier")
    worktree_receipt: dict[str, Any] = {}
    if spec.worktree:
        try:
            spec, worktree_receipt = _prepare_launch_worktree(spec, run_id)
        except ValueError as exc:
            return _finish_launch_idempotency(
                idem_key,
                {
                    "accepted": False,
                    "message": f"Failed to launch {spec.skill}: {exc}",
                    "error": f"{type(exc).__name__}: {exc}",
                    "reason": "worktree_rejected",
                    "run_id": run_id,
                    "agent": spec.agent,
                    "skill": spec.skill,
                    "root": spec.root,
                    "parent_root": spec.root,
                    "worktree": True,
                    "status": "failed",
                    "control_plane": {"sync": "deferred", "run_id": run_id},
                },
                spec_digest=idem_spec_digest,
            )
    artifacts = _run_artifact_paths(run_id)
    runtime_kind = workflow_registry.workflow_runtime_kind(spec.skill)
    research_selection = (
        resolve_research_runtime_config(
            override_agents=spec.research_agents,
            synthesizer=spec.research_synthesizer,
            synthesizer_model=spec.research_synthesizer_model,
        )
        if runtime_kind == "supervised_research"
        else None
    )
    if research_selection is not None:
        research_selection = replace(
            research_selection, model_agent=spec.research_model_agent
        )
    source_prompt = _source_prompt(spec)
    runtime_source = spec.prompt if spec.plan_source is not None else source_prompt
    prompt_body = (
        runtime_source
        if runtime_kind in {"supervised_research", "supervised_marbles"}
        else _runtime_prompt(spec, source_prompt=runtime_source)
    )
    canonical_report_dir = _canonical_report_dir(spec.root, spec.skill)
    artifact_ts = time.strftime("%Y-%m-%d")
    artifact_slug = spec.skill  # filenames/receipts must not disclose prompt text
    report_path = _canonical_report_path(
        canonical_report_dir=canonical_report_dir,
        artifact_ts=artifact_ts,
        agent=spec.agent,
        artifact_slug=artifact_slug,
        run_id=run_id,
    )
    reserve_launcher_report_template(
        report_path,
        run_id=run_id,
        agent=spec.agent,
        skill=spec.skill,
        claim_digest=str(spec.claim_digest or "").strip(),
    )
    source_snapshot = _write_prompt_file(
        artifacts["prompt"].with_name("plan-source.md"), source_prompt
    )
    source_receipt = {
        "source_path": spec.source_path
        or (str(Path(spec.file).expanduser().resolve()) if spec.file else ""),
        "source_origin": "file" if spec.file or spec.source_path else "inline",
        "source_snapshot": str(source_snapshot),
        "source_digest": hashlib.sha256(source_prompt.encode("utf-8")).hexdigest(),
        "model_source": spec.model_source,
    }
    prompt_path = _write_prompt_file(artifacts["prompt"], prompt_body)
    claim_digest = str(spec.claim_digest or "").strip()
    try:
        controls_receipt = _execution_controls_receipt(launch_execution_controls(spec))
    except ExecutionControlsError as exc:
        return _finish_launch_idempotency(
            idem_key,
            {
                "accepted": False,
                "message": f"Failed to launch {spec.skill}: {exc}",
                "error": f"{type(exc).__name__}: {exc}",
                "reason": "execution_controls_rejected",
                "run_id": run_id,
                "agent": spec.agent,
                "skill": spec.skill,
                "root": spec.root,
                "permissions_requested": spec.permissions,
                "sandbox_requested": ""
                if spec.sandbox is None
                else str(spec.sandbox).lower(),
                "status": "failed",
                "control_plane": {"sync": "deferred", "run_id": run_id},
            },
            spec_digest=idem_spec_digest,
        )
    initial_meta: dict[str, Any] = dict(launch_meta or {})
    initial_meta["run_id"] = run_id
    initial_meta["runtime"] = spec.runtime
    initial_meta.update(
        {
            "repo_requested": spec.repo_requested,
            "repo_kind": spec.repo_kind,
            "base_requested": spec.base,
            "resolved_ref": spec.resolved_ref,
            "baseline_sha": spec.baseline_sha,
            "runtime_class": spec.runtime_class,
            "presentation": "headless" if spec.runtime == "headless" else "visible",
        }
    )
    if worktree_receipt:
        initial_meta["root"] = spec.root
        initial_meta.update(worktree_receipt)
    initial_meta.update(controls_receipt)
    initial_meta.update(source_receipt)
    if claim_digest:
        initial_meta["claim_digest"] = claim_digest
    if len(initial_meta) > 1:
        atomic_write_json(
            artifacts["meta"],
            initial_meta,
        )
    safe_spec = {**spec.to_payload(), "prompt": "", "file": str(prompt_path)}
    worker_command = (
        list(worker_command_override)
        if worker_command_override is not None
        else build_launch_command(spec, source_dir, prompt_file=prompt_path)
    )
    try:
        merged_env = selected_runtime_environment()
        if env:
            merged_env.update(env)
        merged_env = selected_runtime_environment(merged_env)
        merged_env["PATH"] = agent_tool_search_path(merged_env)
        worker_command = _resolve_agent_command(spec.agent, worker_command, merged_env)
    except (FileNotFoundError, ValueError) as exc:
        return _finish_launch_idempotency(
            idem_key,
            {
                "accepted": False,
                "message": f"Failed to launch {spec.skill}: {exc}",
                "error": f"{type(exc).__name__}: {exc}",
                "worker_command": worker_command,
                "run_id": run_id,
                "agent": spec.agent,
                "skill": spec.skill,
                "root": spec.root,
                "status": "failed",
                "report": str(report_path),
                "transcript": str(artifacts["transcript"]),
                "meta": str(artifacts["meta"]),
                "prompt_file": str(prompt_path),
                "control_plane": {"sync": "deferred", "run_id": run_id},
            },
            spec_digest=idem_spec_digest,
        )
    launch_tracking = _launch_tracking_payload(launch_meta)
    model_receipt = _model_override_receipt(spec.agent, spec.model)
    if spec.model and runtime_kind == "supervised_research":
        model_receipt = {"model_requested": spec.model}
    # Execution controls ride the same receipt channel as the model pin: every
    # accepted/refused payload that spreads model_receipt carries them too.
    model_receipt = {
        **model_receipt,
        **controls_receipt,
        **source_receipt,
        "model_requested": spec.model,
        "model_effective": spec.model,
        "repo_requested": spec.repo_requested,
        "repo_kind": spec.repo_kind,
        "base_requested": spec.base,
        "resolved_ref": spec.resolved_ref,
        "baseline_sha": spec.baseline_sha,
        "runtime_class": spec.runtime_class,
        "presentation": "headless" if spec.runtime == "headless" else "visible",
    }
    dispatch_command = _dispatcher_command(
        run_id=run_id,
        root=spec.root,
        meta_path=artifacts["meta"],
        prompt_path=prompt_path,
        report_path=report_path,
        transcript_path=artifacts["transcript"],
        worker_command=worker_command,
        tee_output=spec.runtime in {"terminal", "visible"},
        emit_json=spec.runtime not in {"terminal", "visible"},
        quiet=spec.runtime in {"terminal", "visible"},
        lifecycle_state_path=spec.lifecycle_state_path,
        salvage_report_from_stream=bool(
            (launch_meta or {}).get("native_resume")
            or (launch_meta or {}).get("native_fork")
        ),
    )
    launch_dir = control_plane_home() / "launches"
    launch_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    launch_log = launch_dir / f"{stamp}_{spec.skill}.log"
    merged_env.pop(CLAIM_DIGEST_ENV, None)
    _prepend_pythonpath(merged_env, _core_package_root())
    session_id = ensure_session_id(merged_env.get("VIBECRAFTED_SESSION_ID"))
    merged_env["VIBECRAFTED_RUN_ID"] = run_id
    merged_env["SPAWN_RUN_ID"] = run_id
    merged_env["VIBECRAFTED_SESSION_ID"] = session_id
    merged_env["VIBECRAFTED_REPORT_PATH"] = str(report_path)
    merged_env["VIBECRAFTED_TRANSCRIPT_PATH"] = str(artifacts["transcript"])
    merged_env["VIBECRAFTED_META_PATH"] = str(artifacts["meta"])
    merged_env["VIBECRAFTED_PROMPT_PATH"] = str(prompt_path)
    merged_env["VIBECRAFTED_AGENT"] = spec.agent
    merged_env["VIBECRAFTED_SKILL"] = spec.skill
    merged_env["VIBECRAFTED_RUNTIME"] = spec.runtime
    if claim_digest:
        merged_env[CLAIM_DIGEST_ENV] = claim_digest
    if spec.model:
        merged_env["VIBECRAFTED_MODEL_REQUESTED"] = spec.model
    if research_selection is not None:
        merged_env["VIBECRAFTED_RESEARCH_MODEL_AGENT"] = spec.research_model_agent
        if spec.research_agents:
            merged_env["VIBECRAFTED_RESEARCH_AGENTS"] = ",".join(
                research_selection.agents
            )
        if research_selection.synthesizer:
            merged_env["VIBECRAFTED_RESEARCH_SYNTHESIZER"] = (
                research_selection.synthesizer
            )
        if research_selection.synthesizer_model:
            merged_env["VIBECRAFTED_RESEARCH_SYNTHESIZER_MODEL"] = (
                research_selection.synthesizer_model
            )
    if "model_override_supported" in model_receipt:
        merged_env["VIBECRAFTED_MODEL_OVERRIDE_SUPPORTED"] = str(
            bool(model_receipt["model_override_supported"])
        ).lower()
    if "model_override_skipped" in model_receipt:
        merged_env["VIBECRAFTED_MODEL_OVERRIDE_SKIPPED"] = str(
            bool(model_receipt["model_override_skipped"])
        ).lower()
    if model_receipt.get("model_override_skip_reason"):
        merged_env["VIBECRAFTED_MODEL_OVERRIDE_SKIP_REASON"] = str(
            model_receipt["model_override_skip_reason"]
        )
    merged_env["VIBECRAFTED_CANONICAL_REPORT_DIR"] = str(canonical_report_dir)
    merged_env["VIBECRAFTED_ARTIFACT_SLUG"] = artifact_slug
    merged_env["VIBECRAFTED_ARTIFACT_TS"] = artifact_ts
    if spec.runtime == "headless":
        # A detached run has no terminal origin. Ambient Zellij/vc-frame
        # variables belong to the operator shell and must not manufacture a
        # tab that triage later captures, transfers, or closes.
        for key in _TERMINAL_ORIGIN_ENV:
            merged_env.pop(key, None)
        operator_session = ""
    else:
        operator_session = _effective_operator_session(
            root=spec.root,
            run_id=run_id,
            env=merged_env,
        )
    command, transport, command_script = _launch_transport_command(
        spec=spec,
        run_id=run_id,
        operator_session=operator_session,
        dispatch_command=dispatch_command,
        launch_dir=launch_dir,
        prompt_path=prompt_path,
        report_path=report_path,
        transcript_path=artifacts["transcript"],
        meta_path=artifacts["meta"],
        canonical_report_dir=canonical_report_dir,
        artifact_slug=artifact_slug,
        artifact_ts=artifact_ts,
        artifact_suffix="",
        research_selection=research_selection,
    )
    if operator_session:
        merged_env["VIBECRAFTED_OPERATOR_SESSION"] = operator_session
    else:
        merged_env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    append_event(
        kind="launch",
        run_id=run_id,
        message=f"launch accepted for {spec.skill}",
        payload={
            "state": "created",
            "agent": spec.agent,
            "skill": spec.skill,
            "mode": spec.mode,
            "runtime": spec.runtime,
            "root": spec.root,
            **worktree_receipt,
            "operator_session": operator_session,
            "session_id": session_id,
            "identity_required": True,
            "source_dir": str(Path(source_dir).resolve()),
            "prompt": "",
            "file": str(prompt_path),
            "prompt_file": str(prompt_path),
            "report": str(report_path),
            "transcript": str(artifacts["transcript"]),
            "meta": str(artifacts["meta"]),
            **({"claim_digest": claim_digest} if claim_digest else {}),
            "workflow": _workflow_metadata(spec.skill),
            **(
                {
                    "research_agents": list(research_selection.agents),
                    "research_agent_source": research_selection.source,
                    "research_synthesizer": research_selection.synthesizer,
                    "research_synthesizer_model": research_selection.synthesizer_model,
                    "research_synthesizer_source": research_selection.synthesizer_source,
                    "research_ignored_agents": list(research_selection.ignored),
                }
                if research_selection is not None
                else {}
            ),
            **model_receipt,
            "worker_command": worker_command,
            "dispatch_command": dispatch_command,
            "command": command,
            "transport": transport,
            "command_script": str(command_script or ""),
            **launch_tracking,
            "retry_of": retry_of,
        },
    )
    with launch_log.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "ts": stamp,
                    "run_id": run_id,
                    "spec": safe_spec,
                    "workflow": _workflow_metadata(spec.skill),
                    **model_receipt,
                    "worker_command": worker_command,
                    "dispatch_command": dispatch_command,
                    "command": command,
                    "transport": transport,
                    "command_script": str(command_script or ""),
                    **launch_tracking,
                    "retry_of": retry_of,
                    "session_id": session_id,
                    "operator_session": operator_session,
                    **model_receipt,
                }
            )
            + "\n"
        )
        launcher_pid: int | None = None
        launcher_identity: dict[str, Any] | None = None
        try:
            if transport == "vc-frame":
                # G3: run action synchronously so "Session not found" cannot
                # leave a silent process_spawned receipt. One create-background
                # retry lives inside _vc_frame_run_host_action.
                host = _vc_frame_run_host_action(
                    command, operator_session=operator_session
                )
                handle.write(
                    json.dumps(
                        {
                            "ts": stamp,
                            "event": "vc_frame_host_action",
                            "ok": host.ok,
                            "resurrected": host.resurrected,
                            "error": host.error,
                            "stderr": host.stderr[:2000] if host.stderr else "",
                        }
                    )
                    + "\n"
                )
                if not host.ok:
                    append_event(
                        kind="launch",
                        run_id=run_id,
                        message=f"vc-frame host session launch failed: {host.error}",
                        payload={
                            "state": "failed",
                            "agent": spec.agent,
                            "skill": spec.skill,
                            "mode": spec.mode,
                            "runtime": spec.runtime,
                            "root": spec.root,
                            "operator_session": operator_session,
                            "session_id": session_id,
                            "error": host.error,
                            "last_error": host.error,
                            **launch_tracking,
                            "retry_of": retry_of,
                            **model_receipt,
                        },
                    )
                    return _finish_launch_idempotency(
                        idem_key,
                        {
                            "accepted": False,
                            "message": f"Failed to launch {spec.skill}: {host.error}",
                            "command": command,
                            "worker_command": worker_command,
                            "dispatch_command": dispatch_command,
                            "transport": transport,
                            "command_script": str(command_script or ""),
                            "launch_log": str(launch_log),
                            "spec": safe_spec,
                            "error": host.error,
                            "last_error": host.error,
                            "run_id": run_id,
                            "agent": spec.agent,
                            "skill": spec.skill,
                            "root": spec.root,
                            "status": "failed",
                            "operator_session": operator_session,
                            **launch_tracking,
                            "retry_of": retry_of,
                            **model_receipt,
                            "control_plane": sync_state(),
                        },
                        spec_digest=idem_spec_digest,
                    )
                launcher_pid = host.pid
            else:
                proc = subprocess.Popen(
                    command,
                    cwd=Path(source_dir).resolve(),
                    env=merged_env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    text=True,
                )
                launcher_pid = proc.pid
                # The launch is intentionally asynchronous, but dropping the
                # Popen object without waiting leaves a completed launcher as a
                # zombie.  ``kill(pid, 0)`` then reports it alive and canonical
                # await cannot distinguish finalization from stale OS state.
                wait_for_launcher = getattr(proc, "wait", None)
                if callable(wait_for_launcher):
                    threading.Thread(
                        target=wait_for_launcher,
                        name=f"vibecrafted-reap-{run_id}",
                        daemon=True,
                    ).start()
            if launcher_pid is not None:
                launcher_identity = process_identity_receipt(
                    launcher_pid,
                    run_id=run_id,
                )
        except OSError as exc:
            handle.write(
                json.dumps(
                    {
                        "ts": stamp,
                        "event": "spawn_error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                + "\n"
            )
            # The "launch accepted" event above already recorded state="created".
            # Without a terminal event here, the run is stranded as "created"
            # forever — sync_state() shows a phantom active run that never
            # progressed. Record the failure so reconciliation marks it failed.
            append_event(
                kind="launch",
                run_id=run_id,
                message=f"dispatcher spawn failed: {type(exc).__name__}: {exc}",
                payload={
                    "state": "failed",
                    "agent": spec.agent,
                    "skill": spec.skill,
                    "mode": spec.mode,
                    "runtime": spec.runtime,
                    "root": spec.root,
                    "operator_session": operator_session,
                    "session_id": session_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "last_error": f"{type(exc).__name__}: {exc}",
                    **launch_tracking,
                    "retry_of": retry_of,
                    **model_receipt,
                },
            )
            return _finish_launch_idempotency(
                idem_key,
                {
                    "accepted": False,
                    "message": f"Failed to launch {spec.skill}: {exc}",
                    "command": command,
                    "worker_command": worker_command,
                    "dispatch_command": dispatch_command,
                    "transport": transport,
                    "command_script": str(command_script or ""),
                    "launch_log": str(launch_log),
                    "spec": safe_spec,
                    "error": f"{type(exc).__name__}: {exc}",
                    "run_id": run_id,
                    "agent": spec.agent,
                    "skill": spec.skill,
                    "root": spec.root,
                    "status": "failed",
                    **launch_tracking,
                    "retry_of": retry_of,
                    **model_receipt,
                    "control_plane": sync_state(),
                },
                spec_digest=idem_spec_digest,
            )
        append_event(
            kind="launch",
            run_id=run_id,
            message="dispatcher process spawned",
            payload={
                "state": "process_spawned",
                "launcher_pid": launcher_pid,
                **(
                    {"launcher_identity": launcher_identity}
                    if launcher_identity is not None
                    else {}
                ),
                "agent": spec.agent,
                "skill": spec.skill,
                "mode": spec.mode,
                "runtime": spec.runtime,
                "root": spec.root,
                "operator_session": operator_session,
                "session_id": session_id,
                "identity_required": True,
                "source_dir": str(Path(source_dir).resolve()),
                "prompt": "",
                "file": str(prompt_path),
                "prompt_file": str(prompt_path),
                "report": str(report_path),
                "transcript": str(artifacts["transcript"]),
                "meta": str(artifacts["meta"]),
                "workflow": _workflow_metadata(spec.skill),
                **model_receipt,
                "worker_command": worker_command,
                "dispatch_command": dispatch_command,
                "command": command,
                "transport": transport,
                "command_script": str(command_script or ""),
                **launch_tracking,
                "retry_of": retry_of,
            },
        )
        handle.write(
            json.dumps({"ts": stamp, "event": "spawned", "pid": launcher_pid}) + "\n"
        )

    return _finish_launch_idempotency(
        idem_key,
        {
            "accepted": True,
            "message": f"Launched {spec.skill} via Vibecrafted core runtime.",
            "command": command,
            "dispatch_command": dispatch_command,
            "worker_command": worker_command,
            "transport": transport,
            "command_script": str(command_script or ""),
            "pid": launcher_pid,
            "launcher_identity": launcher_identity,
            "run_id": run_id,
            "agent": spec.agent,
            "skill": spec.skill,
            "root": spec.root,
            **worktree_receipt,
            "dispatch": 0,
            "status": "launching",
            "control": str(run_snapshot_dir() / f"{run_id}.json"),
            "report": str(report_path),
            "transcript": str(artifacts["transcript"]),
            "meta": str(artifacts["meta"]),
            "prompt_file": str(prompt_path),
            "session_id": session_id,
            "operator_session": operator_session,
            "control_plane_identity": {
                "run_id": run_id,
                "session_id": session_id,
                "operator_session": operator_session,
            },
            "workflow": _workflow_metadata(spec.skill),
            **model_receipt,
            **launch_tracking,
            "retry_of": retry_of,
            "launch_log": str(launch_log),
            "spec": safe_spec,
            # Launch acceptance is already durable in the event stream, run meta,
            # and dispatcher process. A global board reconciliation here can block
            # on an unrelated run and turn a successful launch into a traceback.
            # Reconciliation belongs to observe/await/board readers, never the
            # launch acknowledgement path.
            "control_plane": {"sync": "deferred", "run_id": run_id},
        },
        spec_digest=idem_spec_digest,
    )


def stop_run(
    run_id: str,
    *,
    reason: str = "operator stop request",
    grace_seconds: float = 2.0,
) -> dict[str, Any]:
    """Public locked entrypoint: stop a run by id under its run-mutation lock."""
    target = str(run_id or "").strip()
    if not target:
        raise ValueError("run_id is required")
    with run_mutation_locks(control_plane_home(), run_id=target):
        return _stop_run_locked(
            target,
            reason=reason,
            grace_seconds=grace_seconds,
        )


def _stop_run_locked(
    target: str,
    *,
    reason: str,
    grace_seconds: float,
) -> dict[str, Any]:
    """Stop implementation assuming the caller already holds the run's mutation lock.

    Qualifies the signal target's live process identity before signaling, sends
    SIGTERM to the process group when available, waits up to ``grace_seconds``,
    and records the stop transition audit trail regardless of outcome.
    """
    run = lookup_run(target)
    if run is None:
        record_stop_transition(
            target,
            accepted=False,
            reason="run_not_found",
        )
        return {"accepted": False, "run_id": target, "reason": "run_not_found"}

    if _run_is_terminal(run):
        record_stop_transition(
            target,
            run=run,
            accepted=False,
            reason="run_terminal",
        )
        return {
            "accepted": False,
            "run_id": target,
            "reason": "run_terminal",
            "run": run,
        }

    signal_target = _stop_signal_target(run)
    if signal_target is None:
        record_stop_transition(
            target,
            run=run,
            accepted=False,
            reason="missing_signal_target",
        )
        return {
            "accepted": False,
            "run_id": target,
            "reason": "missing_signal_target",
            "run": run,
        }

    target_kind, target_pid = signal_target
    qualified, qualification, already_dead = _qualify_stop_signal(
        target,
        run,
        target_kind=target_kind,
        target_pid=target_pid,
    )
    if qualified is None and not already_dead:
        record_stop_transition(
            target,
            run=run,
            accepted=False,
            reason=qualification,
            target=target_kind,
            target_pid=target_pid,
        )
        return {
            "accepted": False,
            "run_id": target,
            "target": target_kind,
            "target_pid": target_pid,
            "target_pgid": None,
            "signal_sent": False,
            "already_dead": False,
            "alive_after_grace": None,
            "reason": qualification,
            "error": "",
            "run": lookup_run(target),
        }

    target_pgid = qualified.target_pgid if qualified is not None else None
    if qualified is None and target_kind in {"launcher_pid", "worker_pgid"}:
        target_pgid = target_pid
    signal_sent = False
    alive_after_grace: bool | None = None
    stop_reason = "pid_gone_before_stop" if already_dead else reason
    stop_error = ""
    if qualified is not None:
        try:
            if qualified.target_pgid is not None:
                os.killpg(qualified.target_pgid, signal.SIGTERM)
            else:
                os.kill(qualified.identity_pid, signal.SIGTERM)
            signal_sent = True
        except ProcessLookupError:
            already_dead = True
            stop_reason = "pid_gone_before_stop"
        except OSError as exc:
            stop_error = f"{type(exc).__name__}: {exc}"

    accepted = not stop_error
    if signal_sent and qualified is not None:
        alive_after_grace = _wait_for_stop_target_exit(qualified, grace_seconds)
    elif already_dead:
        alive_after_grace = False

    record_stop_transition(
        target,
        run=run,
        accepted=accepted,
        reason=stop_reason if accepted else "signal_failed",
        signal_name="SIGTERM",
        target=target_kind,
        target_pid=target_pid,
        target_pgid=target_pgid,
        signal_sent=signal_sent,
        already_dead=already_dead,
        alive_after_grace=alive_after_grace,
        grace_seconds=grace_seconds,
        exit_code=143 if signal_sent else None,
        error=stop_error,
    )
    return {
        "accepted": accepted,
        "run_id": target,
        "target": target_kind,
        "target_pid": target_pid,
        "target_pgid": target_pgid,
        "signal_sent": signal_sent,
        "already_dead": already_dead,
        "alive_after_grace": alive_after_grace,
        "reason": stop_reason if accepted else "signal_failed",
        "identity_qualification": qualification,
        "error": stop_error,
        "run": lookup_run(target),
    }


def retry_run(
    run_id: str,
    source_dir: str | Path = ".",
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Public locked entrypoint: retry a terminal run under lineage-consistent locks.

    Acquires the run's and its resume-lineage's mutation locks together (they
    must serialize with Guardian-native resume), then rejects if the lineage
    root changed between the preliminary and locked reads before delegating to
    :func:`_retry_run_locked`.
    """
    target = str(run_id or "").strip()
    if not target:
        raise ValueError("run_id is required")

    preliminary_run = lookup_run(target)
    preliminary_resume_root = target
    if preliminary_run is not None:
        preliminary_meta = _native_resume_meta(target, preliminary_run)
        preliminary_resume_root = _native_resume_root(
            target,
            preliminary_meta,
            preliminary_run,
        )

    # Manual retry and Guardian-native resume must serialize on the same parent
    # and lineage boundary. Acquire the complete ordered set once; nesting a
    # second parent lock below this point would deadlock cross-process flock.
    with run_mutation_locks(
        control_plane_home(),
        run_id=target,
        resume_root=preliminary_resume_root,
    ):
        run = lookup_run(target)
        if run is not None:
            locked_meta = _native_resume_meta(target, run)
            locked_resume_root = _native_resume_root(target, locked_meta, run)
            if locked_resume_root != preliminary_resume_root:
                payload = {
                    "accepted": False,
                    "reason": "retry_lineage_changed",
                    "retryable": False,
                    "terminal": True,
                }
                append_event(
                    kind="audit:retry",
                    run_id=target,
                    message="retry rejected: resume lineage changed",
                    payload=payload,
                )
                return {
                    "accepted": False,
                    "run_id": target,
                    **payload,
                    "run": run,
                }
        return _retry_run_locked(
            target,
            source_dir,
            env=env,
            run=run,
        )


def _retry_run_locked(
    target: str,
    source_dir: str | Path,
    *,
    env: dict[str, str] | None,
    run: dict[str, Any] | None,
) -> dict[str, Any]:
    """Retry after the caller acquired the parent and resume-lineage locks."""

    if run is None:
        append_event(
            kind="audit:retry",
            run_id=target,
            message="retry rejected: run not found",
            payload={"accepted": False, "reason": "run_not_found"},
        )
        return {"accepted": False, "run_id": target, "reason": "run_not_found"}

    if not _run_is_terminal(run):
        append_event(
            kind="audit:retry",
            run_id=target,
            message="retry rejected: run not terminal",
            payload={
                "accepted": False,
                "reason": "run_not_terminal",
                "state": run.get("state"),
            },
        )
        return {
            "accepted": False,
            "run_id": target,
            "reason": "run_not_terminal",
            "run": run,
        }

    settlement_tui, settlement_verdict, settlement_source = _native_resume_settlement(
        run
    )
    if (
        run.get("recovery_required") is True
        and settlement_tui == "n"
        and settlement_verdict == "needs_attention"
        and settlement_source == "trust"
    ):
        rejection = {
            "accepted": False,
            "reason": "recovery_owned_by_guardian",
            "retryable": False,
            "terminal": True,
        }
        append_event(
            kind="audit:retry",
            run_id=target,
            message="retry rejected: recovery owned by Guardian",
            payload=rejection,
        )
        return {
            "accepted": False,
            "run_id": target,
            **rejection,
            "run": run,
        }

    payload: dict[str, Any] = {
        "skill": str(run.get("skill") or "workflow"),
        "agent": str(run.get("agent") or "claude"),
        "prompt": str(run.get("prompt") or ""),
        "file": str(run.get("file") or ""),
        "runtime": str(run.get("runtime") or "headless"),
        "root": str(run.get("root") or Path(source_dir).resolve()),
    }
    mode = str(run.get("mode") or "").strip()
    if mode:
        payload["mode"] = mode
    model_requested = str(run.get("model_requested") or "").strip()
    if model_requested:
        payload["model_requested"] = model_requested

    try:
        spec = normalize_launch_spec(payload, source_dir)
    except ValueError as exc:
        append_event(
            kind="audit:retry",
            run_id=target,
            message="retry rejected: launch spec invalid",
            payload={
                "accepted": False,
                "reason": "invalid_retry_spec",
                "error": str(exc),
            },
        )
        return {
            "accepted": False,
            "run_id": target,
            "reason": "invalid_retry_spec",
            "error": str(exc),
            "run": run,
        }

    launched = launch_workflow(spec, source_dir, env=env, retry_of=target)
    append_event(
        kind="audit:retry",
        run_id=target,
        message="retry dispatched" if launched.get("accepted") else "retry failed",
        payload={
            "accepted": bool(launched.get("accepted")),
            "new_run_id": launched.get("run_id"),
            "error": launched.get("error", ""),
        },
    )
    return {
        "accepted": bool(launched.get("accepted")),
        "run_id": target,
        "retry_run_id": str(launched.get("run_id") or ""),
        "launch": launched,
    }


DEFAULT_NATIVE_RESUME_PROMPT = (
    "Resume this interrupted Vibecrafted run in its existing provider session. "
    "Inspect the live repository and prior session context, continue only the "
    "unfinished scoped work, run the relevant verification gates, and write "
    "the required handoff report."
)
NATIVE_RESUME_IDEMPOTENCY_SCHEMA = "vibecrafted.native-resume-idempotency.v1"
NATIVE_RESUME_IDEMPOTENCY_STATES = frozenset(
    {"reserved", "dispatched", "launch_failed"}
)
NATIVE_RESUME_IDEMPOTENCY_MAX_LENGTH = 512
NATIVE_RESUME_AUTOMATIC_ATTEMPT_BUDGET = 1
_MISSING_NATIVE_IDENTITIES = {"", "pending", "none", "null", "unknown"}
_ACTIVE_NATIVE_RESUME_LEASES: set[str] = set()
_ACTIVE_NATIVE_RESUME_LEASES_LOCK = threading.Lock()
_NATIVE_RESUME_TRANSIENT_REJECTIONS = {
    "attempt_reservation_failed",
    "idempotency_claim_failed",
    "idempotency_in_progress",
    "launch_failed",
    "launch_rejected",
    "recovery_not_required",
    "run_not_found",
    "run_not_terminal",
    "worker_not_confirmed_dead",
}


class _AutomaticResumeBudgetExhausted(ValueError):
    """Raised when a lineage's single automatic native-resume attempt is already claimed."""


class _NativeResumeCommandRejected(ValueError):
    """Raised when a provider's native resume command cannot be verified or built."""

    def __init__(
        self,
        reason: str,
        *,
        detail: str = "",
        retryable: bool = False,
    ) -> None:
        """Store rejection ``reason``/``detail``/``retryable`` alongside the message."""
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.retryable = retryable


def _native_resume_rejection(
    run_id: str,
    reason: str,
    *,
    run: dict[str, Any] | None = None,
    detail: str = "",
    idempotency_key: str = "",
    retryable: bool | None = None,
) -> dict[str, Any]:
    """Record and return the standard rejected-native-resume payload shape."""
    if retryable is None:
        should_retry = (
            reason in _NATIVE_RESUME_TRANSIENT_REJECTIONS
            or reason.startswith("native_resume_probe_")
        )
    else:
        should_retry = bool(retryable)
    payload: dict[str, Any] = {
        "accepted": False,
        "reason": reason,
        "retryable": should_retry,
        "terminal": not should_retry,
    }
    if detail:
        payload["detail"] = detail
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    append_event(
        kind="audit:native_resume",
        run_id=run_id,
        message=f"native resume rejected: {reason}",
        payload=payload,
    )
    result: dict[str, Any] = {"accepted": False, "run_id": run_id, **payload}
    if run is not None:
        result["run"] = run
    return result


def _native_resume_meta(run_id: str, run: dict[str, Any]) -> dict[str, Any]:
    """Load and merge the parent run's meta.json from resolved + announced paths."""
    candidates: list[Path] = []
    try:
        resolved = resolve_run(run_id)
    except (RunNotResolved, ValueError):
        resolved = None
    if resolved is not None and resolved.meta is not None:
        candidates.append(resolved.meta)
    announced = str(run.get("meta") or "").strip()
    if announced:
        announced_path = Path(announced).expanduser()
        if announced_path not in candidates:
            candidates.append(announced_path)

    payload: dict[str, Any] = {}
    for path in candidates:
        loaded = _read_json_object(path)
        for key, value in loaded.items():
            if key not in payload or payload[key] in (None, ""):
                payload[key] = value
    return payload


def _manual_stop_or_cancel(run: dict[str, Any]) -> bool:
    """True when a run's state/stop_reason indicates an operator-initiated stop."""
    state = str(run.get("state") or "").strip().lower()
    if state in {"stopped", "cancelled", "canceled"}:
        return True
    stop_reason = str(run.get("stop_reason") or "").strip().lower()
    return bool(
        stop_reason
        and any(token in stop_reason for token in ("operator", "manual", "cancel"))
    )


def _native_resume_settlement(run: dict[str, Any]) -> tuple[str, str, str]:
    """Read (tui, verdict, source) settlement fields from a run's flat or nested keys."""
    settlement = run.get("settlement")
    nested = settlement if isinstance(settlement, dict) else {}
    tui = (
        str(
            run.get("settlement_tui")
            or nested.get("tui")
            or nested.get("settlement_tui")
            or ""
        )
        .strip()
        .lower()
    )
    verdict = (
        str(
            run.get("settlement_verdict")
            or nested.get("verdict")
            or nested.get("settlement_verdict")
            or ""
        )
        .strip()
        .lower()
    )
    source = (
        str(
            run.get("settlement_source")
            or nested.get("source")
            or nested.get("settlement_source")
            or ""
        )
        .strip()
        .lower()
    )
    return tui, verdict, source


def _explicit_native_identity(value: Any) -> str:
    """Normalize a raw identity value, treating placeholder tokens as empty."""
    candidate = str(value or "").strip()
    return "" if candidate.lower() in _MISSING_NATIVE_IDENTITIES else candidate


def _native_resume_root(
    parent_run_id: str,
    parent_meta: dict[str, Any],
    parent_run: dict[str, Any],
) -> str:
    """Resolve the resume-lineage root run id for a parent run."""
    return str(
        parent_meta.get("resume_root") or parent_run.get("resume_root") or parent_run_id
    ).strip()


def _native_resume_eligibility(
    run: dict[str, Any],
    parent_meta: dict[str, Any],
) -> tuple[str, str, str, int]:
    """Return candidate identity and settlement revision, or raise policy error."""

    if _manual_stop_or_cancel(run):
        raise ValueError("manual_stop")
    state = str(run.get("state") or "").strip().lower()
    if state == "blocked" or str(run.get("operator_state") or "").lower() == "blocked":
        raise ValueError("blocked")
    if not _run_is_terminal(run):
        raise ValueError("run_not_terminal")

    tui, verdict, source = _native_resume_settlement(run)
    if tui == "x":
        raise ValueError("trust_x")
    if tui != "n" or verdict != "needs_attention":
        raise ValueError(f"settlement_{tui or 'unknown'}_not_resumable")
    if source != "trust":
        raise ValueError("vc_trust_authority_missing")
    if run.get("recovery_required") is not True:
        raise ValueError("recovery_not_required")
    if run.get("worker_alive") is not False:
        raise ValueError("worker_not_confirmed_dead")

    revision = run.get("settlement_revision")
    if type(revision) is not int or revision <= 0:
        raise ValueError("settlement_revision_missing")
    agent = _explicit_native_identity(parent_meta.get("agent") or run.get("agent"))
    agent_session_id = _explicit_native_identity(
        parent_meta.get("agent_session_id") or run.get("agent_session_id")
    )
    if not agent or not agent_session_id:
        raise ValueError("native_resume_candidate_missing")
    return agent.lower(), agent_session_id, source, revision


def _native_resume_reservation_dir(resume_root: str) -> Path:
    """Path to the per-lineage resume-attempt reservation directory, hash-keyed."""
    lineage_key = hashlib.sha256(resume_root.encode("utf-8")).hexdigest()[:24]
    return control_plane_home() / "resume_attempts" / lineage_key


def _next_native_resume_attempt(
    *,
    resume_root: str,
    parent_meta: dict[str, Any],
    parent_run: dict[str, Any],
) -> int:
    """Compute the next attempt number for a resume lineage from runs + reservation markers."""
    floor = max(
        _coerce_positive_int(parent_meta.get("attempt"), 1) or 1,
        _coerce_positive_int(parent_run.get("attempt"), 1) or 1,
    )
    runtime_root = control_plane_home() / "runtime_runs"
    if runtime_root.is_dir():
        for meta_path in runtime_root.glob("*/meta.json"):
            payload = _read_json_object(meta_path)
            if str(payload.get("resume_root") or "").strip() != resume_root:
                continue
            floor = max(floor, _coerce_positive_int(payload.get("attempt"), 1) or 1)

    reservation_dir = _native_resume_reservation_dir(resume_root)
    reservation_dir.mkdir(parents=True, exist_ok=True)
    for marker in reservation_dir.glob("*.json"):
        if marker.stem == "automatic":
            continue
        floor = max(floor, _coerce_positive_int(marker.stem, 1) or 1)
    return floor + 1


def _write_native_resume_attempt(
    *,
    resume_root: str,
    parent_run_id: str,
    child_run_id: str,
    attempt: int,
    resume_mode: str,
    settlement_revision: int,
    trust_receipt_id: str = "",
    idempotency_key: str = "",
) -> None:
    """Atomically create the exclusive attempt-number reservation marker file."""
    reservation_dir = _native_resume_reservation_dir(resume_root)
    reservation_dir.mkdir(parents=True, exist_ok=True)
    marker = reservation_dir / f"{attempt}.json"
    reservation = {
        "schema": "vibecrafted.native-resume-attempt.v1",
        "resume_root": resume_root,
        "resume_of": parent_run_id,
        "run_id": child_run_id,
        "attempt": attempt,
        "resume_mode": resume_mode,
        "automatic_attempt_budget": NATIVE_RESUME_AUTOMATIC_ATTEMPT_BUDGET,
        "automatic_attempt_number": 1 if resume_mode == "automatic" else 0,
        "settlement_revision": settlement_revision,
        **({"trust_receipt_id": trust_receipt_id} if trust_receipt_id else {}),
        "reserved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **({"resume_idempotency_key": idempotency_key} if idempotency_key else {}),
    }
    if marker.exists():
        existing = _read_json_object(marker)
        if (
            str(existing.get("run_id") or "") == child_run_id
            and int(existing.get("attempt") or 0) == attempt
        ):
            return
        raise ValueError(f"resume attempt {attempt} already belongs to another run")
    encoded = (
        json.dumps(reservation, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


def _reserve_native_resume_attempt(
    *,
    parent_run_id: str,
    child_run_id: str,
    parent_meta: dict[str, Any],
    parent_run: dict[str, Any],
    idempotency_key: str = "",
    resume_mode: str = "manual",
    settlement_revision: int = 0,
    trust_receipt_id: str = "",
) -> tuple[str, int]:
    """Reserve one manual lineage attempt while the caller holds its lock."""

    resume_root = _native_resume_root(parent_run_id, parent_meta, parent_run)
    attempt = _next_native_resume_attempt(
        resume_root=resume_root,
        parent_meta=parent_meta,
        parent_run=parent_run,
    )
    _write_native_resume_attempt(
        resume_root=resume_root,
        parent_run_id=parent_run_id,
        child_run_id=child_run_id,
        attempt=attempt,
        resume_mode=resume_mode,
        settlement_revision=settlement_revision,
        trust_receipt_id=trust_receipt_id,
        idempotency_key=idempotency_key,
    )
    return resume_root, attempt


def _normalize_native_resume_idempotency_key(value: str) -> str:
    """Validate and trim an idempotency key (length + no control characters)."""
    key = str(value or "").strip()
    if not key:
        return ""
    if len(key) > NATIVE_RESUME_IDEMPOTENCY_MAX_LENGTH:
        raise ValueError(
            f"idempotency_key exceeds {NATIVE_RESUME_IDEMPOTENCY_MAX_LENGTH} characters"
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise ValueError("idempotency_key contains control characters")
    return key


def _native_resume_idempotency_registry() -> Path:
    """Path to (and ensure) the native-resume idempotency records directory."""
    registry = control_plane_home() / "native_resume_idempotency"
    registry.mkdir(parents=True, exist_ok=True)
    return registry


def _native_resume_idempotency_path(registry: Path, key: str) -> Path:
    """Compute the idempotency record file path from its content-hashed key."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return registry / f"{digest}.json"


def _read_native_resume_idempotency_record(
    path: Path,
    key: str,
) -> dict[str, Any] | None:
    """Read and strictly validate one idempotency record, raising on any inconsistency."""
    if not path.exists():
        return None
    payload = _read_json_object(path)
    if not payload:
        raise ValueError(f"unreadable idempotency record: {path}")
    if payload.get("schema") != NATIVE_RESUME_IDEMPOTENCY_SCHEMA:
        raise ValueError(f"invalid idempotency record schema: {path}")
    if str(payload.get("idempotency_key") or "") != key:
        raise ValueError(f"idempotency hash collision or key mismatch: {path}")
    required = (
        "parent_run_id",
        "agent",
        "child_run_id",
        "runtime_session_id",
        "resume_root",
        "attempt",
        "state",
    )
    missing = [name for name in required if payload.get(name) in (None, "")]
    if missing:
        raise ValueError(
            f"idempotency record missing fields {','.join(missing)}: {path}"
        )
    state = payload.get("state")
    if not isinstance(state, str) or state not in NATIVE_RESUME_IDEMPOTENCY_STATES:
        raise ValueError(f"invalid idempotency record state {state!r}: {path}")
    return payload


def _lookup_native_resume_idempotency(key: str) -> dict[str, Any] | None:
    """Lockless preliminary read; writers publish records with atomic replace."""

    registry = _native_resume_idempotency_registry()
    path = _native_resume_idempotency_path(registry, key)
    return _read_native_resume_idempotency_record(path, key)


def _native_resume_automatic_budget_path(resume_root: str) -> Path:
    """Path to the per-lineage automatic-resume budget ledger file."""
    directory = _native_resume_reservation_dir(resume_root)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "automatic.json"


def _new_native_resume_lease() -> tuple[str, int]:
    """Mint a fresh (session_id, pid) in-memory lease token pair."""
    return ensure_session_id(), os.getpid()


def _native_resume_owner_active_here(record: dict[str, Any]) -> bool:
    """Return whether this process still owns the in-memory launch lease.

    Cross-process ownership is established only by the ordered filesystem
    locks. A PID in a durable receipt is diagnostic metadata, never authority:
    it may already identify an unrelated process after PID reuse.
    """

    token = str(record.get("owner_token") or "")
    if not token:
        return False
    with _ACTIVE_NATIVE_RESUME_LEASES_LOCK:
        return token in _ACTIVE_NATIVE_RESUME_LEASES


def _activate_native_resume_lease(token: str) -> None:
    """Register a lease token as owned by this process."""
    with _ACTIVE_NATIVE_RESUME_LEASES_LOCK:
        _ACTIVE_NATIVE_RESUME_LEASES.add(token)


def _release_native_resume_lease(token: str) -> None:
    """Drop a lease token from this process's active-lease set."""
    with _ACTIVE_NATIVE_RESUME_LEASES_LOCK:
        _ACTIVE_NATIVE_RESUME_LEASES.discard(token)


def _claim_native_resume_idempotency(
    *,
    key: str,
    parent_run_id: str,
    agent: str,
    agent_session_id: str,
    parent_runtime_session_id: str,
    parent_meta: dict[str, Any],
    parent_run: dict[str, Any],
    settlement_revision: int,
    trust_receipt_id: str = "",
) -> tuple[dict[str, Any], bool]:
    """Return/create one automatic claim while ordered locks are held."""

    registry = _native_resume_idempotency_registry()
    path = _native_resume_idempotency_path(registry, key)
    existing = _read_native_resume_idempotency_record(path, key)
    if existing is not None:
        return existing, False

    resume_root = _native_resume_root(parent_run_id, parent_meta, parent_run)
    budget_path = _native_resume_automatic_budget_path(resume_root)
    budget = _read_json_object(budget_path)
    if not budget:
        # Adopt pre-ledger keyed receipts from the v1 implementation. A rollout
        # must not reset a lineage's automatic budget merely because its first
        # reservation predates ``automatic.json``.
        for receipt_path in registry.glob("*.json"):
            receipt = _read_json_object(receipt_path)
            if (
                receipt.get("schema") == NATIVE_RESUME_IDEMPOTENCY_SCHEMA
                and str(receipt.get("resume_root") or "") == resume_root
            ):
                budget = receipt
                atomic_write_json(budget_path, budget)
                break
    if budget:
        budget_state = budget.get("state")
        if (
            not isinstance(budget_state, str)
            or budget_state not in NATIVE_RESUME_IDEMPOTENCY_STATES
        ):
            raise ValueError(
                f"invalid automatic resume ledger state {budget_state!r}: {budget_path}"
            )
        if (
            str(budget.get("idempotency_key") or "") != key
            or str(budget.get("parent_run_id") or "") != parent_run_id
            or str(budget.get("agent") or "").lower() != agent.lower()
        ):
            raise _AutomaticResumeBudgetExhausted(
                f"automatic attempt already reserved by "
                f"{budget.get('idempotency_key') or 'unknown'}"
            )
        record = dict(budget)
        record["schema"] = NATIVE_RESUME_IDEMPOTENCY_SCHEMA
        atomic_write_json(path, record)
        _write_native_resume_attempt(
            resume_root=resume_root,
            parent_run_id=parent_run_id,
            child_run_id=str(record["child_run_id"]),
            attempt=int(record["attempt"]),
            resume_mode="automatic",
            settlement_revision=int(record.get("settlement_revision") or 0),
            trust_receipt_id=str(record.get("trust_receipt_id") or ""),
            idempotency_key=key,
        )
        return record, False

    child_run_id = reserve_run_id("rsme")
    attempt = _next_native_resume_attempt(
        resume_root=resume_root,
        parent_meta=parent_meta,
        parent_run=parent_run,
    )
    runtime_session_id = ensure_session_id()
    owner_token, owner_pid = _new_native_resume_lease()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record = {
        "schema": NATIVE_RESUME_IDEMPOTENCY_SCHEMA,
        "idempotency_key": key,
        "parent_run_id": parent_run_id,
        "agent": agent,
        "agent_session_id": agent_session_id,
        "parent_runtime_session_id": parent_runtime_session_id,
        "child_run_id": child_run_id,
        "runtime_session_id": runtime_session_id,
        "resume_root": resume_root,
        "attempt": attempt,
        "resume_mode": "automatic",
        "automatic_attempt_budget": NATIVE_RESUME_AUTOMATIC_ATTEMPT_BUDGET,
        "automatic_attempt_number": 1,
        "settlement_revision": settlement_revision,
        "trust_receipt_id": trust_receipt_id,
        "state": "reserved",
        "launch_accepted": False,
        "owner_token": owner_token,
        "owner_pid": owner_pid,
        "lease_generation": 1,
        "created_at": now,
        "updated_at": now,
    }
    # The lineage ledger is the budget and recovery source of truth. Publishing
    # it first means a kill before the idempotency mirror is still recoverable
    # with the exact child/runtime identity and attempt.
    atomic_write_json(budget_path, record)
    _write_native_resume_attempt(
        resume_root=resume_root,
        parent_run_id=parent_run_id,
        child_run_id=child_run_id,
        attempt=attempt,
        resume_mode="automatic",
        settlement_revision=settlement_revision,
        trust_receipt_id=trust_receipt_id,
        idempotency_key=key,
    )
    atomic_write_json(path, record)
    return record, True


def _take_over_native_resume_idempotency(
    *,
    key: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    """CAS one exclusively locked reservation without changing launch identity."""

    registry = _native_resume_idempotency_registry()
    path = _native_resume_idempotency_path(registry, key)
    current = _read_native_resume_idempotency_record(path, key)
    if current is None:
        raise ValueError("idempotency record disappeared before takeover")
    expected_generation = int(record.get("lease_generation") or 0)
    if int(current.get("lease_generation") or 0) != expected_generation:
        raise ValueError("idempotency lease changed before takeover")
    token, owner_pid = _new_native_resume_lease()
    current["owner_token"] = token
    current["owner_pid"] = owner_pid
    current["lease_generation"] = expected_generation + 1
    current["state"] = "reserved"
    current["launch_accepted"] = False
    current["takeover_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    current["updated_at"] = current["takeover_at"]
    atomic_write_json(path, current)
    budget_path = _native_resume_automatic_budget_path(
        str(current.get("resume_root") or "")
    )
    budget = _read_json_object(budget_path)
    if str(budget.get("idempotency_key") or "") == key:
        budget.update(
            {
                "owner_token": token,
                "owner_pid": owner_pid,
                "lease_generation": current["lease_generation"],
                "takeover_at": current["takeover_at"],
                "updated_at": current["updated_at"],
            }
        )
        atomic_write_json(budget_path, budget)
    return current


def _update_native_resume_idempotency(
    *,
    key: str,
    child_run_id: str,
    state: str,
    launch_accepted: bool,
    launch: dict[str, Any] | None = None,
    error: str = "",
    owner_token: str = "",
    release_owner: bool = False,
) -> dict[str, Any]:
    """Update an idempotency record's state/launch outcome and mirror it into the ledger."""
    if state not in NATIVE_RESUME_IDEMPOTENCY_STATES:
        raise ValueError(f"invalid idempotency record state: {state!r}")
    registry = _native_resume_idempotency_registry()
    path = _native_resume_idempotency_path(registry, key)
    record = _read_native_resume_idempotency_record(path, key)
    if record is None:
        raise ValueError("idempotency record disappeared before launch receipt")
    if str(record.get("child_run_id") or "") != child_run_id:
        raise ValueError("idempotency record child_run_id changed")
    if owner_token and str(record.get("owner_token") or "") != owner_token:
        raise ValueError("idempotency lease owner changed before launch receipt")
    record["state"] = state
    record["launch_accepted"] = bool(launch_accepted)
    record["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if launch:
        record["launch_pid"] = launch.get("pid")
        record["launch_status"] = str(launch.get("status") or "")
        record["launch_error"] = str(
            launch.get("error") or launch.get("last_error") or ""
        )
    if error:
        record["launch_error"] = error
    if release_owner:
        record["owner_pid"] = 0
        record["owner_released_at"] = record["updated_at"]
    atomic_write_json(path, record)
    budget_path = _native_resume_automatic_budget_path(
        str(record.get("resume_root") or "")
    )
    budget = _read_json_object(budget_path)
    if str(budget.get("idempotency_key") or "") == key:
        budget.update(record)
        atomic_write_json(budget_path, budget)
    return record


def _native_resume_child_was_dispatched(child: dict[str, Any] | None) -> bool:
    """True when a resumed child run already reached a dispatched/terminal state."""
    if not child:
        return False
    state = str(child.get("state") or "").strip().lower()
    if state in {
        "process_spawned",
        "first_output_seen",
        "active",
        "running",
        "completed",
        "report_validated",
        "report_missing",
        "report_invalid",
        "contract_failed",
        "closed",
        "timed_out",
        "ghost",
    }:
        return True
    return any(
        _coerce_positive_int(child.get(field), 0)
        for field in ("launcher_pid", "worker_pid", "worker_pgid")
    )


def _native_resume_idempotency_result(
    *,
    target: str,
    agent: str,
    requested_agent: str,
    key: str,
    record: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    """Build the replay result payload for an existing native-resume idempotency record."""
    recorded_parent = str(record.get("parent_run_id") or "")
    recorded_agent = str(record.get("agent") or "").lower()
    if (
        recorded_parent != target
        or recorded_agent != agent
        or (requested_agent and requested_agent != recorded_agent)
    ):
        return _native_resume_rejection(
            target,
            "idempotency_conflict",
            run=run,
            idempotency_key=key,
            detail=(
                f"recorded_parent={recorded_parent or 'unknown'} "
                f"recorded_agent={recorded_agent or 'unknown'} "
                f"requested_parent={target} "
                f"requested_agent={requested_agent or agent or 'unknown'}"
            ),
        )

    child_run_id = str(record.get("child_run_id") or "")
    state = str(record.get("state") or "reserved")
    accepted = state == "dispatched" and bool(record.get("launch_accepted"))
    child: dict[str, Any] | None = None
    if state == "reserved":
        candidate = lookup_run(child_run_id)
        if (
            candidate is not None
            and str(candidate.get("run_id") or child_run_id) == child_run_id
        ):
            child = candidate
            accepted = _native_resume_child_was_dispatched(child)

    if accepted:
        reason = "idempotent_replay"
    elif state == "launch_failed":
        reason = "launch_failed"
    else:
        reason = "idempotency_in_progress"
    retryable = not accepted and reason in {
        "idempotency_in_progress",
        "launch_failed",
    }
    payload = {
        "accepted": accepted,
        "reason": reason,
        "retryable": retryable,
        "terminal": not retryable,
        "deduplicated": True,
        "idempotency_key": key,
        "new_run_id": child_run_id,
        "agent": recorded_agent,
        "agent_session_id": str(record.get("agent_session_id") or ""),
        "runtime_session_id": str(record.get("runtime_session_id") or ""),
        "parent_runtime_session_id": str(record.get("parent_runtime_session_id") or ""),
        "resume_of": recorded_parent,
        "resume_root": str(record.get("resume_root") or ""),
        "attempt": _coerce_positive_int(record.get("attempt"), 0),
        "resume_mode": str(record.get("resume_mode") or "automatic"),
        "automatic_attempt_budget": int(
            record.get("automatic_attempt_budget")
            or NATIVE_RESUME_AUTOMATIC_ATTEMPT_BUDGET
        ),
        "automatic_attempt_number": int(record.get("automatic_attempt_number") or 1),
        "settlement_revision": int(record.get("settlement_revision") or 0),
        "trust_receipt_id": str(record.get("trust_receipt_id") or ""),
        "lease_generation": int(record.get("lease_generation") or 0),
        "idempotency_state": state,
    }
    append_event(
        kind="audit:native_resume",
        run_id=target,
        message=f"native resume idempotency replay: {reason}",
        payload=payload,
    )
    return {
        "accepted": accepted,
        "run_id": target,
        "resume_run_id": child_run_id,
        "reason": reason,
        "retryable": retryable,
        "terminal": not retryable,
        "deduplicated": True,
        "idempotency_key": key,
        "attempt": payload["attempt"],
        "resume_of": recorded_parent,
        "resume_root": payload["resume_root"],
        "agent": recorded_agent,
        "agent_session_id": payload["agent_session_id"],
        "runtime_session_id": payload["runtime_session_id"],
        "parent_runtime_session_id": payload["parent_runtime_session_id"],
        "resume_mode": payload["resume_mode"],
        "automatic_attempt_budget": payload["automatic_attempt_budget"],
        "automatic_attempt_number": payload["automatic_attempt_number"],
        "settlement_revision": payload["settlement_revision"],
        "trust_receipt_id": payload["trust_receipt_id"],
        "lease_generation": payload["lease_generation"],
        "idempotency_state": state,
        "launch": {
            "accepted": accepted,
            "run_id": child_run_id,
            "status": str(record.get("launch_status") or state),
            "error": str(record.get("launch_error") or ""),
            "deduplicated": True,
        },
        **({"child": child} if child is not None else {}),
    }


def _verified_native_resume_command(
    agent: str,
    agent_session_id: str,
) -> tuple[list[str], str, str]:
    """Return one live-probed provider resume argv with no shell boundary."""

    normalized_agent = str(agent or "").strip().lower()
    try:
        capability = capability_for(normalized_agent)
    except ValueError as exc:
        raise _NativeResumeCommandRejected(
            "native_resume_unsupported",
            detail=str(exc),
        ) from exc
    if capability.noninteractive_resume != SUPPORTED:
        reason = (
            "native_resume_unverified"
            if capability.noninteractive_resume == UNVERIFIED
            else "native_resume_unsupported"
        )
        raise _NativeResumeCommandRejected(
            reason,
            detail=capability.notes,
        )

    provider_probe = probe_provider(normalized_agent)
    if provider_probe.state != PROBE_CONFIRMED or not provider_probe.executable:
        reason = (
            "native_resume_probe_failed"
            if provider_probe.state == "probe_failed"
            else f"native_resume_probe_{provider_probe.state}"
        )
        raise _NativeResumeCommandRejected(
            reason,
            detail=provider_probe.detail,
            retryable=True,
        )
    try:
        command = native_resume_argv(normalized_agent, agent_session_id)
    except ValueError as exc:
        raise _NativeResumeCommandRejected(
            "native_resume_unsupported",
            detail=str(exc),
        ) from exc
    command[0] = provider_probe.executable
    if any(flag in command for flag in capability.forbidden_flags):
        raise _NativeResumeCommandRejected("native_resume_forbidden_flag")
    return (
        command,
        str(provider_probe.state),
        str(provider_probe.version or ""),
    )


def _manual_explicit_resume_rejection(
    *,
    agent: str,
    agent_session_id: str,
    reason: str,
    detail: str = "",
    retryable: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    """Build the standard rejection payload for :func:`manual_resume_session`."""
    payload: dict[str, Any] = {
        "schema": "vibecrafted.manual_explicit_resume.v1",
        "accepted": False,
        "reason": reason,
        "retryable": retryable,
        "terminal": not retryable,
        "resume_mode": "manual_explicit",
        "agent": agent,
        "agent_session_id": agent_session_id,
    }
    if run_id:
        payload["run_id"] = run_id
    if detail:
        payload["detail"] = detail
    return payload


def manual_resume_session(
    agent: str,
    agent_session_id: str,
    source_dir: str | Path = ".",
    *,
    prompt: str,
    root: str | Path = "",
    model: str = "",
    model_source: str = "",
    source_text: str | None = None,
    source_path: str = "",
    skill: str = "workflow",
    launch_meta: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Launch an explicit provider-session continuation as its own tracked run.

    This operator boundary deliberately has no parent-run authority semantics:
    it does not claim settlement, consume the Guardian automatic budget, or
    manufacture trust preconditions. It only proves that the requested
    provider has a verified noninteractive resume contract, then delegates the
    detached lifetime and prompt-stdin transport to the normal core launcher.
    """

    normalized_agent = str(agent or "").strip().lower()
    native_id = _explicit_native_identity(agent_session_id)
    prompt_body = str(prompt or "")
    if not normalized_agent:
        return _manual_explicit_resume_rejection(
            agent="",
            agent_session_id=native_id,
            reason="missing_agent",
        )
    if not native_id:
        return _manual_explicit_resume_rejection(
            agent=normalized_agent,
            agent_session_id="",
            reason="missing_agent_session_id",
        )
    if not prompt_body.strip():
        return _manual_explicit_resume_rejection(
            agent=normalized_agent,
            agent_session_id=native_id,
            reason="missing_prompt",
        )
    try:
        model_requested, selected_source = select_plan_model(
            normalized_agent, prompt_body, model=model
        )
    except ValueError as exc:
        return _manual_explicit_resume_rejection(
            agent=normalized_agent,
            agent_session_id=native_id,
            reason="launch_spec_invalid",
            detail=str(exc),
        )
    try:
        command, _probe_state, _probe_version = _verified_native_resume_command(
            normalized_agent,
            native_id,
        )
    except _NativeResumeCommandRejected as exc:
        return _manual_explicit_resume_rejection(
            agent=normalized_agent,
            agent_session_id=native_id,
            reason=exc.reason,
            detail=exc.detail,
            retryable=exc.retryable,
        )
    command = _with_model_override(
        normalized_agent,
        command,
        model_requested,
    )

    resolved_source_dir = Path(source_dir).expanduser().resolve()
    resolved_root = normalize_run_root(root, resolved_source_dir)
    child_run_id = reserve_run_id("rsme")
    child_runtime_session_id = ensure_session_id()
    child_env = dict(env or {})
    child_env["VIBECRAFTED_SESSION_ID"] = child_runtime_session_id
    child_env["VIBECRAFTED_AGENT_SESSION_ID"] = native_id
    launch_meta = {
        **(launch_meta or {}),
        "run_id": child_run_id,
        "agent": normalized_agent,
        "agent_session_id": native_id,
        "runtime_session_id": child_runtime_session_id,
        "native_resume": True,
        "resume_mode": "manual_explicit",
        "manual_explicit": True,
    }
    try:
        admitted = normalize_launch_spec(
            {
                "agent": normalized_agent,
                "skill": str(skill or "workflow").strip() or "workflow",
                "prompt": source_text if source_text is not None else prompt_body,
                "root": resolved_root,
                "repo_selector": True,
                "runtime": "headless",
                "model": model_requested,
            },
            resolved_source_dir,
        )
    except ValueError as exc:
        return _manual_explicit_resume_rejection(
            agent=normalized_agent,
            agent_session_id=native_id,
            reason="launch_spec_invalid",
            detail=str(exc),
        )
    spec = replace(
        admitted,
        mode="manual_explicit",
        prompt=prompt_body,
        model_source=model_source or selected_source,
        runtime_class=str(launch_meta.get("runtime_class") or admitted.runtime_class),
        baseline_sha=str(
            launch_meta.get("baseline_sha")
            or launch_meta.get("worktree_baseline_sha")
            or admitted.baseline_sha
        ),
        run_id=child_run_id,
        plan_source=source_text,
        source_path=source_path,
        source_digest=hashlib.sha256(
            (source_text if source_text is not None else prompt_body).encode("utf-8")
        ).hexdigest(),
    )
    try:
        launched = launch_workflow(
            spec,
            resolved_source_dir,
            env=child_env,
            worker_command_override=command,
            launch_meta=launch_meta,
        )
    except (OSError, ValueError) as exc:
        return _manual_explicit_resume_rejection(
            agent=normalized_agent,
            agent_session_id=native_id,
            reason="launch_rejected",
            detail=f"{type(exc).__name__}: {exc}",
            retryable=isinstance(exc, OSError),
            run_id=child_run_id,
        )
    return {
        **launched,
        "schema": "vibecrafted.manual_explicit_resume.v1",
        "resume_mode": "manual_explicit",
        "agent": normalized_agent,
        "agent_session_id": native_id,
        "runtime_session_id": child_runtime_session_id,
    }


def manual_fork_session(
    agent: str,
    session: str,
    source_dir: str | Path,
    *,
    prompt: str,
    root: str | Path,
    model: str | None = None,
    base: str = "",
    worktree: str | bool | None = None,
    execution_runtime: str = "",
    source_path: str = "",
    parent_run_id: str = "",
    permissions: str = "",
    session_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Admit a native task fork using the existing private tracked launcher."""
    source = resolve_session_selection(
        agent, session, root, selection=session_selection
    )
    session = source["agent_session_id"]
    if not source.get("accepted"):
        return source
    try:
        command, probe_state, version = _verified_native_resume_command(agent, session)
        if agent == "codex":
            # The resume probe alone is insufficient evidence of exec fork.
            probe = subprocess.run(
                [command[0], "exec", "fork", "--help"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if (
                probe.returncode
                or b"<SESSION_ID>" not in probe.stdout
                or b"stdin" not in probe.stdout
            ):
                raise ValueError(
                    "installed Codex does not confirm exec fork stdin contract"
                )
        elif agent not in {"claude", "grok"}:
            raise ValueError("native task fork is unverified for this provider")
        spec = normalize_launch_spec(
            {
                "agent": agent,
                "skill": "workflow",
                "prompt": prompt,
                "root": str(root),
                "repo_selector": True,
                "runtime": "headless",
                "model": model,
                "base": base,
                "worktree": worktree,
                "execution_runtime": execution_runtime,
                "permissions": permissions,
            },
            source_dir,
        )
        # Reuse the normal permission owner; only native continuation differs.
        executable = command[0]
        command = _stdin_command(agent, controls=launch_execution_controls(spec))
        if agent == "codex":
            command = [*command[:-1], "fork", session, "-"]
        else:
            command.extend(["--resume", session, "--fork-session"])
        command[0] = executable
        command = _with_model_override(agent, command, spec.model)
        spec = replace(
            spec,
            run_id=reserve_run_id("fork"),
            mode="native_fork",
            source_path=source_path,
            plan_source=prompt,
        )
        runtime_session = ensure_session_id()
        return launch_workflow(
            spec,
            source_dir,
            env={
                "VIBECRAFTED_SESSION_ID": runtime_session,
                "VIBECRAFTED_AGENT_SESSION_ID": "",
                "VIBECRAFTED_FORK_SOURCE_SESSION_ID": session,
            },
            worker_command_override=command,
            launch_meta={
                "native_fork": True,
                "session_selection": source,
                "fork_source_session_id": session,
                "parent_run_id": parent_run_id or source.get("source_run_id", ""),
                "runtime_session_id": runtime_session,
                "provider_probe_state": probe_state,
                "provider_version": version,
            },
        )
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        return {
            "accepted": False,
            "reason": "native_fork_admission_failed",
            "detail": str(exc),
            "agent": agent,
        }


CONTROL_PLANE_RUN_PREFIXES = frozenset(
    {
        "work",
        "impl",
        "wflw",
        "rsme",
        "fork",
        "marb",
        "just",
        "scaf",
        "rese",
        "revi",
        "plan",
        "ship",
        "loop",
        "init",
        "hydr",
        "deco",
        "folw",
        "prun",
        "trus",
        "ownr",
        "polr",
        "audt",
        "canr",
        "delg",
        "intn",
        "part",
        "relz",
        "wflo",
        "guar",
        "guard",
    }
)
OPERATOR_CONTINUABLE_STATES = frozenset(
    {
        "stopped",
        "cancelled",
        "canceled",
        "failed",
        "timed_out",
        "interrupted",
        "ghost",
        "report_missing",
        "contract_failed",
        "blocked",
        "stalled",
    }
)
OPERATOR_DONE_STATES = frozenset({"completed", "report_validated", "closed"})


def looks_like_control_plane_run_id(value: str) -> bool:
    """True when *value* has the ``<skill4>-YYMMDD-HHMMSS-entropy`` run shape."""
    text = str(value or "").strip()
    if not text or text.startswith("-"):
        return False
    prefix, _, rest = text.partition("-")
    if prefix not in CONTROL_PLANE_RUN_PREFIXES or not rest:
        return False
    return any(part.isdigit() for part in rest.split("-"))


def classify_resume_identity(token: str) -> str:
    """Classify a resume token as run_id, provider session, or Vibecrafted session."""
    text = str(token or "").strip()
    if not text:
        return "empty"
    if looks_like_control_plane_run_id(text) or lookup_run(text) is not None:
        return "run_id"
    found = find_run_for_identity_token(text)
    if found is None:
        return "unknown"
    runtime_ids = {
        str(found.get("runtime_session_id") or "").strip(),
        str(found.get("vibecrafted_session_id") or "").strip(),
    }
    runtime_ids.discard("")
    agent_session = str(
        found.get("agent_session_id") or found.get("session_id") or ""
    ).strip()
    if text in runtime_ids and text != agent_session:
        return "vibecrafted_session"
    if text == agent_session and text not in runtime_ids:
        return "provider_session"
    if text in runtime_ids:
        return "vibecrafted_session"
    if text == agent_session:
        return "provider_session"
    return "unknown"


def find_run_for_identity_token(token: str) -> dict[str, Any] | None:
    """Resolve a token against run ids and recorded session fields."""
    text = str(token or "").strip()
    if not text:
        return None
    direct = lookup_run(text)
    if direct is not None:
        return direct
    root = control_plane_home() / "runtime_runs"
    if not root.is_dir():
        return None
    try:
        metas = sorted(
            root.glob("*/meta.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    for path in metas[:300]:
        payload = _read_json_object(path)
        for key in (
            "agent_session_id",
            "session_id",
            "runtime_session_id",
            "vibecrafted_session_id",
        ):
            if str(payload.get(key) or "").strip() != text:
                continue
            run_id = str(payload.get("run_id") or path.parent.name)
            return lookup_run(run_id) or payload
    return None


FORK_SOURCE_SCHEMA = "vibecrafted.fork_source.v1"


def _fork_source_rejection(
    agent: str,
    reason: str,
    *,
    detail: str = "",
    hint: str = "",
    run_id: str = "",
    session: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": FORK_SOURCE_SCHEMA,
        "accepted": False,
        "agent": agent,
        "reason": reason,
        "agent_session_id": "",
        "source_run_id": run_id,
        "requested_session": session,
    }
    if detail:
        payload["detail"] = detail
    if hint:
        payload["hint"] = hint
    return payload


def resolve_session_selection(
    agent: str,
    selector: str,
    root: str | Path,
    *,
    environment: dict[str, str] | None = None,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve public current/last without global recency or implicit cloning.

    Last is the newest recorded native session in this exact checkout/provider;
    equal timestamps for different sessions are ambiguous, never arbitrary.
    Current requires explicit process context and agrees with recorded ownership.
    """
    provider = agent.strip().lower()
    selected_root = str(Path(root).expanduser().resolve())
    token = selector.strip()
    if selection is not None:
        # Transport the original resolution, never reinterpret current/last
        # after handoff. Revalidate the exact target against current ownership.
        if (
            not isinstance(selection, dict)
            or selection.get("agent") != provider
            or selection.get("agent_session_id") != token
            or selection.get("selection_root") != selected_root
            or not selection.get("session_selector")
            or not selection.get("identity_source")
        ):
            raise ValueError("session selection receipt does not match admission")
        resolve_session_selection(provider, token, selected_root)
        return dict(selection)
    if token == "previous":
        raise ValueError(
            "--session previous is retired; use current, last or an exact ID"
        )
    provenance = "explicit_session"
    if token == "current":
        context = os.environ if environment is None else environment
        identities: set[str] = set()
        keys = {
            "codex": ("CODEX_THREAD_ID", "CODEX_SESSION_ID"),
            "claude": ("CLAUDE_CODE_SESSION_ID",),
            "grok": ("GROK_SESSION_ID",),
        }.get(provider, ())
        for key in keys:
            if context.get(key):
                identities.add(context[key])
        if context.get("VIBECRAFTED_AGENT") == provider and context.get(
            "VIBECRAFTED_AGENT_SESSION_ID"
        ):
            identities.add(context["VIBECRAFTED_AGENT_SESSION_ID"])
        parent_id = context.get("VIBECRAFTED_RUN_ID", "")
        parent = lookup_run(parent_id) if parent_id else None
        if parent and parent.get("agent") == provider:
            native = _provider_session_for_continue(parent)
            if native:
                identities.add(native)
        if len(identities) != 1:
            raise ValueError(
                "current requires one explicit provider session in parent context"
            )
        token = identities.pop()
        provenance = "explicit_parent_context"
        if (
            not find_run_for_identity_token(token)
            and str(Path.cwd().resolve()) != selected_root
        ):
            raise ValueError(
                "unrecorded current session belongs to caller checkout; explicit repo differs"
            )
    elif token == "last":
        candidates: dict[str, tuple[float, dict[str, Any]]] = {}
        for path in (control_plane_home() / "runtime_runs").glob("*/meta.json"):
            row = _read_json_object(path)
            if row.get("agent") != provider or not row.get("root"):
                continue
            if str(Path(row["root"]).resolve()) != selected_root:
                continue
            native = _provider_session_for_continue(row)
            stamp = str(row.get("started_at") or row.get("created_at") or "")
            try:
                instant = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    continue
                timestamp = instant.astimezone(timezone.utc).timestamp()
            except ValueError:
                continue
            if native and timestamp > candidates.get(native, (float("-inf"), {}))[0]:
                candidates[native] = (timestamp, row)
        if not candidates:
            raise ValueError(
                "no recorded native session for selected repository/provider"
            )
        newest = max(value[0] for value in candidates.values())
        winners = [native for native, value in candidates.items() if value[0] == newest]
        if len(winners) != 1:
            raise ValueError(
                "last is ambiguous: equal timestamps for different native sessions"
            )
        token = winners[0]
        provenance = "repository_provider_latest_started"
    result = resolve_fork_source(provider, session=token, require_native_fork=False)
    if not result.get("accepted"):
        raise ValueError(str(result.get("detail") or result.get("reason")))
    recorded_root = result.get("source_root")
    if recorded_root and str(Path(recorded_root).resolve()) != selected_root:
        raise ValueError("selected session belongs to a different repository checkout")
    return {
        **result,
        "session_selector": selector,
        "identity_source": provenance,
        "selection_root": selected_root,
    }


def resolve_fork_source(
    agent: str,
    *,
    run_id: str = "",
    session: str = "",
    require_native_fork: bool = True,
) -> dict[str, Any]:
    """Resolve the stable provider identity a ``vibecrafted fork`` branches from.

    One identity per call: ``--session <provider-session-id>`` names it
    directly, ``--run-id <control-plane-run>`` reads the provider session the
    run recorded. The source is only ever read; forking happens in the
    provider and must leave that session untouched. Providers without a native
    fork surface are refused here, with the capability table as evidence, so
    no caller can present a plain resume as a fork.
    """
    normalized_agent = str(agent or "").strip().lower()
    target_run = str(run_id or "").strip()
    target_session = str(session or "").strip()
    if not normalized_agent:
        return _fork_source_rejection("", "missing_agent")
    try:
        capability = capability_for(normalized_agent)
    except ValueError as exc:
        return _fork_source_rejection(
            normalized_agent, "unknown_agent", detail=str(exc)
        )
    if require_native_fork and capability.native_fork == UNSUPPORTED:
        return _fork_source_rejection(
            normalized_agent,
            "native_fork_unsupported",
            detail=capability.fork_runtime_restrictions,
            hint=(
                f"vibecrafted resume {normalized_agent} --session <id> continues the "
                "original session (that is a resume, not a fork)"
            ),
        )
    if target_run and target_session:
        return _fork_source_rejection(
            normalized_agent,
            "conflicting_identity",
            detail="--session and --run-id cannot be combined; use one identity",
            run_id=target_run,
            session=target_session,
        )
    if not target_run and not target_session:
        return _fork_source_rejection(
            normalized_agent,
            "missing_identity",
            hint=(
                f"vibecrafted fork {normalized_agent} --session <provider-session-id> | "
                "--run-id <work-...>"
            ),
        )

    if target_session:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", target_session):
            return _fork_source_rejection(
                normalized_agent,
                "invalid_session_id",
                detail="use a native session ID, not flags, paths or a title",
            )
        kind = classify_resume_identity(target_session)
        if kind == "run_id" or looks_like_control_plane_run_id(target_session):
            return _fork_source_rejection(
                normalized_agent,
                "run_id_not_session",
                detail="that token is a control-plane run id, not a provider session",
                hint=f"vibecrafted fork {normalized_agent} --run-id {target_session}",
                session=target_session,
            )
        if kind == "vibecrafted_session":
            found = find_run_for_identity_token(target_session) or {}
            provider = _provider_session_for_continue(found)
            return _fork_source_rejection(
                normalized_agent,
                "vibecrafted_session_not_provider_session",
                detail=(
                    "that token is VIBECRAFTED_SESSION_ID / runtime_session_id, "
                    "not a provider session"
                ),
                hint=(
                    f"vibecrafted fork {normalized_agent} --session {provider}"
                    if provider
                    else ""
                ),
                session=target_session,
            )
        found = find_run_for_identity_token(target_session) or {}
        recorded_agent = str(found.get("agent") or "").strip().lower()
        if recorded_agent and recorded_agent != normalized_agent:
            return _fork_source_rejection(
                normalized_agent,
                "agent_mismatch",
                detail=f"recorded={recorded_agent} requested={normalized_agent}",
                session=target_session,
            )
        return {
            "schema": FORK_SOURCE_SCHEMA,
            "accepted": True,
            "agent": normalized_agent,
            "agent_session_id": target_session,
            "source_run_id": str(found.get("run_id") or ""),
            "source_root": str(found.get("root") or ""),
            "identity_source": "recorded_session" if found else "explicit_session",
            "native_fork": capability.native_fork,
            "fork_runtime_restrictions": capability.fork_runtime_restrictions,
        }

    kind = classify_resume_identity(target_run)
    if kind == "provider_session":
        return _fork_source_rejection(
            normalized_agent,
            "provider_session_not_run_id",
            detail="that token is a provider session, not a control-plane run id",
            hint=f"vibecrafted fork {normalized_agent} --session {target_run}",
            run_id=target_run,
        )
    if kind == "vibecrafted_session":
        found = find_run_for_identity_token(target_run) or {}
        found_id = str(found.get("run_id") or "")
        return _fork_source_rejection(
            normalized_agent,
            "vibecrafted_session_not_run_id",
            detail=(
                "that token is VIBECRAFTED_SESSION_ID / runtime_session_id, "
                "not a control-plane run and not a provider session"
            ),
            hint=f"vibecrafted fork {normalized_agent} --run-id {found_id}"
            if found_id
            else "",
            run_id=target_run,
        )
    if kind == "unknown" and not looks_like_control_plane_run_id(target_run):
        return _fork_source_rejection(
            normalized_agent,
            "not_a_run_id",
            detail="pass a control-plane run id such as work-YYMMDD-HHMMSS-xxxxx",
            run_id=target_run,
        )
    run = lookup_run(target_run)
    if run is None:
        return _fork_source_rejection(
            normalized_agent, "run_not_found", run_id=target_run
        )
    parent = _merge_run_and_meta(run, _native_resume_meta(target_run, run))
    recorded_agent = str(parent.get("agent") or "").strip().lower()
    if recorded_agent and recorded_agent != normalized_agent:
        return _fork_source_rejection(
            normalized_agent,
            "agent_mismatch",
            detail=f"recorded={recorded_agent} requested={normalized_agent}",
            run_id=target_run,
        )
    native_session = _provider_session_for_continue(parent)
    if not native_session:
        return _fork_source_rejection(
            normalized_agent,
            "no_provider_session",
            detail=(
                "the run recorded no provider session id; a fork needs a native "
                "session to branch from and never replays the prompt as a fork"
            ),
            hint=f"vibecrafted resume {normalized_agent} --run-id {target_run}",
            run_id=target_run,
        )
    return {
        "schema": FORK_SOURCE_SCHEMA,
        "accepted": True,
        "agent": normalized_agent,
        "agent_session_id": native_session,
        "source_run_id": target_run,
        "source_root": str(parent.get("root") or ""),
        "identity_source": "run_meta",
        "native_fork": capability.native_fork,
        "fork_runtime_restrictions": capability.fork_runtime_restrictions,
    }


def _operator_continue_rejection(
    run_id: str,
    reason: str,
    *,
    detail: str = "",
    run: dict[str, Any] | None = None,
    hint: str = "",
) -> dict[str, Any]:
    """Build the fail-closed operator-continue receipt."""
    payload: dict[str, Any] = {
        "schema": "vibecrafted.operator_continue.v1",
        "accepted": False,
        "reason": reason,
        "run_id": run_id,
        "resume_mode": "operator_continue",
    }
    if detail:
        payload["detail"] = detail
    if hint:
        payload["hint"] = hint
    if run is not None:
        payload["run"] = run
    return payload


def _merge_run_and_meta(run: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Prefer live projection values, then fill blanks from on-disk meta."""
    merged = dict(meta)
    for key, value in run.items():
        if value not in (None, ""):
            merged[key] = value
    if (
        str(meta.get("agent") or "") in SUPPORTED_AGENTS
        and str(run.get("agent") or "") not in SUPPORTED_AGENTS
    ):
        merged["agent"] = meta["agent"]
    return merged


def _operator_continue_state(run: dict[str, Any]) -> str:
    """Projected status/state for an operator-continue decision."""
    return str(run.get("state") or run.get("status") or "").strip().lower()


def _worker_process_alive(run: dict[str, Any]) -> bool:
    """True when a recorded worker/launcher pid still exists."""
    for key in ("worker_pgid", "worker_pid", "launcher_pid"):
        raw = run.get(key)
        if raw in (None, ""):
            continue
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            return True
        except OSError:
            continue
        else:
            return True
    return False


def _provider_session_for_continue(run: dict[str, Any]) -> str:
    """Return a provider session id that is not just the Vibecrafted runtime id."""
    if run.get("native_identity_status") == "pending":
        return ""
    agent_session = _explicit_native_identity(
        run.get("agent_session_id") or run.get("session_id") or ""
    )
    runtime_session = _explicit_native_identity(
        run.get("runtime_session_id") or run.get("vibecrafted_session_id") or ""
    )
    if not agent_session:
        return ""
    if runtime_session and agent_session == runtime_session:
        return ""
    return agent_session


def _operator_continue_prompt(
    run_id: str,
    run: dict[str, Any],
    extra_prompt: str,
    *,
    native_session: str,
) -> str:
    """Build the continuation prompt for a stopped/failed parent run."""
    extra = str(extra_prompt or "")
    original = ""
    try:
        resolved = resolve_run(run_id)
    except (RunNotResolved, ValueError, OSError):
        resolved = None
    if resolved is not None:
        prompt_path = resolved.run_dir / "prompt.md"
        try:
            if prompt_path.is_file():
                original = prompt_path.read_text(encoding="utf-8")
        except OSError:
            original = ""
    if not original:
        original = str(run.get("prompt") or "").strip()

    if native_session:
        header = (
            f"The tracked Vibecrafted run {run_id} was stopped "
            "(process group killed). Continue that job from the last honest "
            "point. Do not restart from zero unless the work is actually "
            "unfinished at the start."
        )
        if extra:
            return f"{header}\n\n{extra}"
        return header

    header = (
        f"This Vibecrafted run ({run_id}) was stopped or interrupted. "
        "Continue the same job from the last honest point. "
        "Do not treat this as a greenfield task."
    )
    if original and extra:
        return f"{header}\n\n{original}\n\n---\nOperator continuation:\n\n{extra}"
    if extra:
        return f"{header}\n\n{extra}"
    if original:
        return f"{header}\n\n{original}"
    return header


def operator_continue_run(
    run_id: str,
    source_dir: str | Path = ".",
    *,
    prompt: str = "",
    expected_agent: str = "",
    root: str | Path = "",
    model: str = "",
    plan_text: str = "",
    source_path: str = "",
    base: str = "",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Continue a stopped/failed control-plane run as a new tracked job.

    Guardian ``native_resume_run`` refuses operator stops on purpose. This is
    the public operator path: the old PGID is dead, so a new run is launched.
    A stored provider session (not ``VIBECRAFTED_SESSION_ID``) is resumed
    natively; otherwise the original prompt is replayed as ``resume-new-session``.
    """
    target = str(run_id or "").strip()
    if not target:
        return _operator_continue_rejection("", "missing_run_id")
    kind = classify_resume_identity(target)
    if kind == "provider_session":
        found = find_run_for_identity_token(target)
        found_id = str((found or {}).get("run_id") or "")
        return _operator_continue_rejection(
            target,
            "provider_session_not_run_id",
            detail="that token is a provider session, not a control-plane run id",
            hint=(
                "vibecrafted resume <agent> --session "
                f"{target}" + (f"  (or --run-id {found_id})" if found_id else "")
            ),
            run=found,
        )
    if kind == "vibecrafted_session":
        found = find_run_for_identity_token(target)
        found_id = str((found or {}).get("run_id") or "")
        provider = _provider_session_for_continue(found or {})
        return _operator_continue_rejection(
            target,
            "vibecrafted_session_not_run_id",
            detail=(
                "that token is VIBECRAFTED_SESSION_ID / runtime_session_id, "
                "not a control-plane run and not a provider session"
            ),
            hint=(
                (f"vibecrafted resume <agent> --run-id {found_id}" if found_id else "")
                + (f"  (provider session: --session {provider})" if provider else "")
            ).strip(),
            run=found,
        )
    if kind == "unknown" and not looks_like_control_plane_run_id(target):
        return _operator_continue_rejection(
            target,
            "not_a_run_id",
            detail="pass a control-plane run id such as work-YYMMDD-HHMMSS-xxxxx",
            hint="vibecrafted resume <agent> --run-id <work-...>   or   --session <provider-uuid>",
        )

    run = lookup_run(target)
    if run is None:
        return _operator_continue_rejection(target, "run_not_found")
    parent = _merge_run_and_meta(run, _native_resume_meta(target, run))
    requested_agent = str(expected_agent or "").strip().lower()
    recorded_agent = str(parent.get("agent") or "").strip().lower()
    if requested_agent and recorded_agent and requested_agent != recorded_agent:
        return _operator_continue_rejection(
            target,
            "agent_mismatch",
            detail=f"recorded={recorded_agent} requested={requested_agent}",
            run=parent,
        )
    agent = requested_agent or recorded_agent
    if not agent:
        return _operator_continue_rejection(target, "missing_agent", run=parent)

    state = _operator_continue_state(parent)
    if _worker_process_alive(parent) and not _manual_stop_or_cancel(parent):
        return _operator_continue_rejection(
            target,
            "still_running",
            detail="worker process is still alive; observe or stop first",
            hint=f"vibecrafted observe {agent} --run-id {target}",
            run=parent,
        )
    if state in OPERATOR_DONE_STATES and not _manual_stop_or_cancel(parent):
        return _operator_continue_rejection(
            target,
            "already_complete",
            detail=f"state={state} — observe the report instead of launching a twin",
            hint=f"vibecrafted observe {agent} --run-id {target}",
            run=parent,
        )
    if (
        state
        and state not in OPERATOR_CONTINUABLE_STATES
        and state not in OPERATOR_DONE_STATES
        and not _manual_stop_or_cancel(parent)
        and not _run_is_terminal(parent)
    ):
        return _operator_continue_rejection(
            target,
            "not_continuable",
            detail=f"state={state}",
            hint=f"vibecrafted observe {agent} --run-id {target}",
            run=parent,
        )

    native_session = _provider_session_for_continue(parent)
    prompt_body = _operator_continue_prompt(
        target, parent, prompt, native_session=native_session
    )
    resolved_source_dir = Path(source_dir).expanduser().resolve()
    resolved_root = normalize_run_root(
        root or parent.get("root") or "",
        resolved_source_dir,
    )
    if (
        root
        and parent.get("root")
        and Path(root).resolve() != Path(str(parent["root"])).resolve()
    ):
        return _operator_continue_rejection(
            target,
            "repository_conflict",
            detail="resume preserves the original checkout; use fork",
            run=parent,
        )
    if base:
        try:
            _ref, requested_sha = resolve_repository_base(resolved_root, base)
        except ValueError as exc:
            return _operator_continue_rejection(
                target, "base_conflict", detail=str(exc), run=parent
            )
        recorded_sha = (
            parent.get("baseline_sha")
            or parent.get("worktree_baseline_sha")
            or parent.get("dispatch_baseline_sha")
        )
        if not recorded_sha or requested_sha != recorded_sha:
            return _operator_continue_rejection(
                target,
                "base_conflict",
                detail="resume preserves recorded baseline; use a new run or fork",
                run=parent,
            )
    try:
        model_requested, model_source = select_plan_model(
            agent,
            plan_text,
            model=model,
            previous=str(
                parent.get("model_effective")
                or parent.get("agent_model")
                or parent.get("model_requested")
                or ""
            ),
        )
    except ValueError as exc:
        return _operator_continue_rejection(
            target, "launch_spec_invalid", detail=str(exc), run=parent
        )
    continuation_meta = {
        "parent_run_id": target,
        "resume_of": target,
        "model_source": model_source,
        **{
            key: parent[key]
            for key in (
                "runtime_class",
                "baseline_sha",
                "worktree_baseline_sha",
                "worktree_branch",
                "parent_root",
            )
            if key in parent
        },
    }

    if native_session:
        launched = manual_resume_session(
            agent,
            native_session,
            resolved_source_dir,
            prompt=prompt_body,
            root=resolved_root,
            model=model_requested,
            model_source=model_source,
            source_text=plan_text or prompt or None,
            source_path=source_path,
            launch_meta=continuation_meta,
            env=env,
        )
        return {
            **launched,
            "schema": "vibecrafted.operator_continue.v1",
            "resume_mode": launched.get("resume_mode") or "manual_explicit",
            "resume_of": target,
            "parent_run_id": target,
            "operator_continue": True,
        }

    skill = str(parent.get("skill") or "workflow").strip() or "workflow"
    try:
        spec = normalize_launch_spec(
            {
                "skill": skill,
                "agent": agent,
                "prompt": prompt_body,
                "file": "",
                "runtime": "headless",
                "root": str(resolved_root),
                "mode": "resume-new-session",
                "model": model_requested,
            },
            resolved_source_dir,
        )
    except ValueError as exc:
        return _operator_continue_rejection(
            target,
            "launch_spec_invalid",
            detail=str(exc),
            run=parent,
        )
    spec = replace(
        spec,
        model_source=model_source,
        plan_source=plan_text or prompt or None,
        source_path=source_path,
        source_digest=hashlib.sha256(
            (plan_text or prompt or prompt_body).encode("utf-8")
        ).hexdigest(),
    )
    try:
        launched = launch_workflow(
            spec,
            resolved_source_dir,
            env=env,
            launch_meta={
                **continuation_meta,
                "resume_of": target,
                "resume_root": target,
                "resume_mode": "operator_continue",
                "parent_runtime_session_id": _explicit_native_identity(
                    parent.get("runtime_session_id")
                    or parent.get("vibecrafted_session_id")
                    or ""
                ),
            },
        )
    except (OSError, ValueError) as exc:
        return _operator_continue_rejection(
            target,
            "launch_rejected",
            detail=f"{type(exc).__name__}: {exc}",
            run=parent,
        )
    return {
        **launched,
        "schema": "vibecrafted.operator_continue.v1",
        "resume_mode": "resume_new_session",
        "resume_of": target,
        "parent_run_id": target,
        "operator_continue": True,
        "agent": agent,
    }


def native_resume_run(
    run_id: str,
    source_dir: str | Path = ".",
    *,
    prompt: str = "",
    expected_agent: str = "",
    expected_agent_session_id: str = "",
    expected_settlement_revision: int | None = None,
    expected_receipt_id: str = "",
    env: dict[str, str] | None = None,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Resume one explicit trust-settled recovery candidate.

    The public boundary is fail-closed even when called without the Guardian:
    only a terminal trust ``n`` with a dead worker, explicit native identity,
    recovery requirement, stable settlement revision, and one exact v2 trust
    receipt may launch. A keyed call is an automatic Guardian attempt (one per
    lineage); an unkeyed call is a separate explicit manual attempt. Guardian
    callers may pin the provider-session identity, settlement revision, and
    receipt id; all are revalidated under the live run mutation locks.
    """

    target = str(run_id or "").strip()
    if not target:
        raise ValueError("run_id is required")
    requested_agent = str(expected_agent or "").strip().lower()
    requested_receipt_id = str(expected_receipt_id or "").strip()
    raw_expected_agent_session = str(expected_agent_session_id or "").strip()
    requested_agent_session = _explicit_native_identity(raw_expected_agent_session)
    if raw_expected_agent_session and not requested_agent_session:
        return _native_resume_rejection(
            target,
            "invalid_expected_agent_session_id",
        )
    requested_settlement_revision: int | None = None
    if expected_settlement_revision is not None:
        if (
            type(expected_settlement_revision) is not int
            or expected_settlement_revision <= 0
        ):
            return _native_resume_rejection(
                target,
                "invalid_expected_settlement_revision",
            )
        requested_settlement_revision = expected_settlement_revision
    try:
        resume_idempotency_key = _normalize_native_resume_idempotency_key(
            idempotency_key
        )
    except ValueError as exc:
        return _native_resume_rejection(
            target,
            "invalid_idempotency_key",
            detail=str(exc),
        )

    existing_idempotency: dict[str, Any] | None = None
    if resume_idempotency_key:
        try:
            existing_idempotency = _lookup_native_resume_idempotency(
                resume_idempotency_key
            )
        except (OSError, ValueError) as exc:
            return _native_resume_rejection(
                target,
                "idempotency_record_invalid",
                detail=f"{type(exc).__name__}: {exc}",
                idempotency_key=resume_idempotency_key,
            )
        if existing_idempotency is not None and (
            str(existing_idempotency.get("parent_run_id") or "") != target
            or (
                requested_agent
                and str(existing_idempotency.get("agent") or "").lower()
                != requested_agent
            )
        ):
            return _native_resume_rejection(
                target,
                "idempotency_conflict",
                detail=(
                    "recorded_parent="
                    f"{existing_idempotency.get('parent_run_id') or 'unknown'} "
                    f"recorded_agent={existing_idempotency.get('agent') or 'unknown'} "
                    f"requested_parent={target} "
                    f"requested_agent={requested_agent or 'unspecified'}"
                ),
                idempotency_key=resume_idempotency_key,
            )
        # A prior dispatched idempotency receipt is not resume authority.
        # Replay is resolved only after the live parent and exact trust receipt
        # have been revalidated under the ordered mutation locks below.
        if (
            existing_idempotency is not None
            and not requested_agent_session
            and requested_settlement_revision is None
            and not requested_receipt_id
            and _native_resume_owner_active_here(existing_idempotency)
        ):
            return _native_resume_idempotency_result(
                target=target,
                agent=str(existing_idempotency.get("agent") or "").lower(),
                requested_agent=requested_agent,
                key=resume_idempotency_key,
                record=existing_idempotency,
                run={"run_id": target},
            )

    run = lookup_run(target)
    if run is None:
        return _native_resume_rejection(
            target,
            "run_not_found",
            idempotency_key=resume_idempotency_key,
        )

    parent_meta = _native_resume_meta(target, run)
    try:
        agent, agent_session_id, _settlement_source, settlement_revision = (
            _native_resume_eligibility(run, parent_meta)
        )
    except ValueError as exc:
        return _native_resume_rejection(
            target,
            str(exc),
            run=run,
            idempotency_key=resume_idempotency_key,
        )
    if requested_agent and requested_agent != agent:
        return _native_resume_rejection(
            target,
            "agent_mismatch",
            run=run,
            detail=f"recorded={agent or 'unknown'} requested={requested_agent}",
            idempotency_key=resume_idempotency_key,
        )
    parent_runtime_session_id = _explicit_native_identity(
        parent_meta.get("runtime_session_id") or run.get("runtime_session_id") or ""
    )
    if not parent_runtime_session_id:
        return _native_resume_rejection(
            target,
            "missing_runtime_session_id",
            run=run,
            idempotency_key=resume_idempotency_key,
        )

    try:
        command, provider_probe_state, provider_probe_version = (
            _verified_native_resume_command(agent, agent_session_id)
        )
    except _NativeResumeCommandRejected as exc:
        return _native_resume_rejection(
            target,
            exc.reason,
            run=run,
            detail=exc.detail,
            idempotency_key=resume_idempotency_key,
            retryable=exc.retryable,
        )

    preliminary_resume_root = _native_resume_root(target, parent_meta, run)
    cas_revision = settlement_revision
    if existing_idempotency is not None:
        recorded_revision = existing_idempotency.get("settlement_revision")
        if type(recorded_revision) is not int or recorded_revision <= 0:
            return _native_resume_rejection(
                target,
                "idempotency_record_revision_missing",
                run=run,
                idempotency_key=resume_idempotency_key,
            )
        cas_revision = recorded_revision

    with run_mutation_locks(
        control_plane_home(),
        run_id=target,
        resume_root=preliminary_resume_root,
        idempotency_key=resume_idempotency_key,
    ):
        locked_run = lookup_run(target)
        if locked_run is None:
            return _native_resume_rejection(
                target,
                "run_not_found",
                idempotency_key=resume_idempotency_key,
            )
        locked_meta = _native_resume_meta(target, locked_run)
        try:
            locked_agent, locked_agent_session, _source, locked_revision = (
                _native_resume_eligibility(locked_run, locked_meta)
            )
        except ValueError as exc:
            return _native_resume_rejection(
                target,
                str(exc),
                run=locked_run,
                idempotency_key=resume_idempotency_key,
            )
        locked_resume_root = _native_resume_root(target, locked_meta, locked_run)
        if locked_resume_root != preliminary_resume_root:
            return _native_resume_rejection(
                target,
                "resume_lineage_changed",
                run=locked_run,
                idempotency_key=resume_idempotency_key,
            )
        if requested_agent_session and locked_agent_session != requested_agent_session:
            return _native_resume_rejection(
                target,
                "expected_agent_session_mismatch",
                run=locked_run,
                detail=(
                    f"expected={requested_agent_session} "
                    f"current={locked_agent_session or 'unknown'}"
                ),
                idempotency_key=resume_idempotency_key,
            )
        if (
            requested_settlement_revision is not None
            and locked_revision != requested_settlement_revision
        ):
            return _native_resume_rejection(
                target,
                "expected_settlement_revision_mismatch",
                run=locked_run,
                detail=(
                    f"expected={requested_settlement_revision} "
                    f"current={locked_revision}"
                ),
                idempotency_key=resume_idempotency_key,
            )
        if locked_revision != cas_revision:
            return _native_resume_rejection(
                target,
                "settlement_revision_changed",
                run=locked_run,
                detail=f"expected={cas_revision} current={locked_revision}",
                idempotency_key=resume_idempotency_key,
            )
        if locked_agent != agent or locked_agent_session != agent_session_id:
            return _native_resume_rejection(
                target,
                "native_resume_identity_changed",
                run=locked_run,
                idempotency_key=resume_idempotency_key,
            )
        locked_parent_runtime_session_id = _explicit_native_identity(
            locked_meta.get("runtime_session_id")
            or locked_run.get("runtime_session_id")
        )
        if not locked_parent_runtime_session_id:
            return _native_resume_rejection(
                target,
                "missing_runtime_session_id",
                run=locked_run,
                idempotency_key=resume_idempotency_key,
            )
        if locked_parent_runtime_session_id != parent_runtime_session_id:
            return _native_resume_rejection(
                target,
                "runtime_session_identity_changed",
                run=locked_run,
                idempotency_key=resume_idempotency_key,
            )
        authority = guard_mod.authorize_guardian_resume(
            run_id=target,
            repo=Path(
                str(locked_run.get("root") or locked_meta.get("root") or source_dir)
            ),
            meta=locked_meta,
            projection=locked_run,
            expected_receipt_id=requested_receipt_id,
        )
        if not authority.allowed:
            return _native_resume_rejection(
                target,
                authority.reason,
                run=locked_run,
                detail=authority.detail,
                idempotency_key=resume_idempotency_key,
                retryable=authority.retryable,
            )
        locked_receipt_id = authority.receipt_id

        resume_mode = "automatic" if resume_idempotency_key else "manual"
        owner_token = ""
        if resume_idempotency_key:
            try:
                idempotency_record, created = _claim_native_resume_idempotency(
                    key=resume_idempotency_key,
                    parent_run_id=target,
                    agent=agent,
                    agent_session_id=agent_session_id,
                    parent_runtime_session_id=parent_runtime_session_id,
                    parent_meta=locked_meta,
                    parent_run=locked_run,
                    settlement_revision=locked_revision,
                    trust_receipt_id=locked_receipt_id,
                )
            except _AutomaticResumeBudgetExhausted as exc:
                return _native_resume_rejection(
                    target,
                    "automatic_resume_budget_exhausted",
                    run=locked_run,
                    detail=str(exc),
                    idempotency_key=resume_idempotency_key,
                )
            except (OSError, ValueError) as exc:
                return _native_resume_rejection(
                    target,
                    "idempotency_claim_failed",
                    run=locked_run,
                    detail=f"{type(exc).__name__}: {exc}",
                    idempotency_key=resume_idempotency_key,
                    retryable=isinstance(exc, OSError),
                )
            recorded_parent = str(idempotency_record.get("parent_run_id") or "")
            recorded_agent = str(idempotency_record.get("agent") or "").lower()
            if recorded_parent != target or recorded_agent != agent:
                return _native_resume_rejection(
                    target,
                    "idempotency_conflict",
                    run=locked_run,
                    idempotency_key=resume_idempotency_key,
                )
            if (
                int(idempotency_record.get("settlement_revision") or 0)
                != locked_revision
            ):
                return _native_resume_rejection(
                    target,
                    "settlement_revision_changed",
                    run=locked_run,
                    idempotency_key=resume_idempotency_key,
                )
            if (
                str(idempotency_record.get("trust_receipt_id") or "")
                != locked_receipt_id
            ):
                return _native_resume_rejection(
                    target,
                    "trust_receipt_id_changed",
                    run=locked_run,
                    idempotency_key=resume_idempotency_key,
                )
            if not created:
                child = lookup_run(str(idempotency_record.get("child_run_id") or ""))
                idempotency_state = str(idempotency_record.get("state") or "reserved")
                if idempotency_state not in {
                    "reserved",
                    "launch_failed",
                } or _native_resume_child_was_dispatched(child):
                    return _native_resume_idempotency_result(
                        target=target,
                        agent=agent,
                        requested_agent=requested_agent,
                        key=resume_idempotency_key,
                        record=idempotency_record,
                        run=locked_run,
                    )
                idempotency_record = _take_over_native_resume_idempotency(
                    key=resume_idempotency_key,
                    record=idempotency_record,
                )
            child_run_id = str(idempotency_record["child_run_id"])
            resume_root = str(idempotency_record["resume_root"])
            attempt = int(idempotency_record["attempt"])
            child_runtime_session_id = str(idempotency_record["runtime_session_id"])
            owner_token = str(idempotency_record.get("owner_token") or "")
        else:
            child_run_id = reserve_run_id("rsme")
            try:
                resume_root, attempt = _reserve_native_resume_attempt(
                    parent_run_id=target,
                    child_run_id=child_run_id,
                    parent_meta=locked_meta,
                    parent_run=locked_run,
                    resume_mode=resume_mode,
                    settlement_revision=locked_revision,
                    trust_receipt_id=locked_receipt_id,
                )
            except (OSError, ValueError) as exc:
                return _native_resume_rejection(
                    target,
                    "attempt_reservation_failed",
                    run=locked_run,
                    detail=f"{type(exc).__name__}: {exc}",
                    retryable=isinstance(exc, OSError),
                )
            child_runtime_session_id = ensure_session_id()

        automatic_attempt_number = 1 if resume_mode == "automatic" else 0
        child_env = dict(env or {})
        child_env["VIBECRAFTED_SESSION_ID"] = child_runtime_session_id
        child_env["VIBECRAFTED_AGENT_SESSION_ID"] = agent_session_id
        launch_meta = {
            "run_id": child_run_id,
            "agent": agent,
            "agent_session_id": agent_session_id,
            "runtime_session_id": child_runtime_session_id,
            "parent_runtime_session_id": parent_runtime_session_id,
            "resume_of": target,
            "resume_root": resume_root,
            "attempt": attempt,
            "native_resume": True,
            "resume_mode": resume_mode,
            "automatic_attempt_budget": NATIVE_RESUME_AUTOMATIC_ATTEMPT_BUDGET,
            "automatic_attempt_number": automatic_attempt_number,
            "resume_settlement_revision": locked_revision,
            "resume_trust_receipt_id": locked_receipt_id,
            **(
                {"resume_idempotency_key": resume_idempotency_key}
                if resume_idempotency_key
                else {}
            ),
        }
        claim_digest = str(
            locked_meta.get("claim_digest") or locked_run.get("claim_digest") or ""
        ).strip()
        spec = WorkflowLaunchSpec(
            agent=agent,
            mode="native_resume",
            skill=str(
                locked_run.get("skill") or locked_meta.get("skill") or "workflow"
            ),
            prompt=str(prompt or "").strip() or DEFAULT_NATIVE_RESUME_PROMPT,
            file="",
            runtime="headless",
            root=str(
                locked_run.get("root")
                or locked_meta.get("root")
                or Path(source_dir).resolve()
            ),
            model=str(
                locked_run.get("model_requested")
                or locked_meta.get("model_requested")
                or ""
            ),
            claim_digest=claim_digest,
            run_id=child_run_id,
        )
        resume_command = _with_model_override(agent, command, spec.model)
        if owner_token:
            _activate_native_resume_lease(owner_token)
        try:
            launched = launch_workflow(
                spec,
                source_dir,
                env=child_env,
                worker_command_override=resume_command,
                launch_meta=launch_meta,
            )
        except BaseException as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if resume_idempotency_key:
                try:
                    _update_native_resume_idempotency(
                        key=resume_idempotency_key,
                        child_run_id=child_run_id,
                        state="reserved",
                        launch_accepted=False,
                        error=detail,
                        owner_token=owner_token,
                        release_owner=True,
                    )
                except (OSError, ValueError) as receipt_exc:
                    detail += (
                        "; idempotency receipt update failed: "
                        f"{type(receipt_exc).__name__}: {receipt_exc}"
                    )
                finally:
                    _release_native_resume_lease(owner_token)
            if not isinstance(exc, (OSError, ValueError)):
                raise
            return _native_resume_rejection(
                target,
                "launch_rejected",
                run=locked_run,
                detail=detail,
                idempotency_key=resume_idempotency_key,
            )

        accepted = bool(launched.get("accepted"))
        idempotency_receipt_error = ""
        if resume_idempotency_key:
            try:
                _update_native_resume_idempotency(
                    key=resume_idempotency_key,
                    child_run_id=child_run_id,
                    state="dispatched" if accepted else "reserved",
                    launch_accepted=accepted,
                    launch=launched,
                    owner_token=owner_token,
                    release_owner=not accepted,
                )
            except (OSError, ValueError) as exc:
                idempotency_receipt_error = f"{type(exc).__name__}: {exc}"
            finally:
                _release_native_resume_lease(owner_token)
        payload = {
            "accepted": accepted,
            "reason": "dispatched" if accepted else "launch_failed",
            "retryable": not accepted,
            "terminal": accepted,
            "new_run_id": child_run_id,
            "agent": agent,
            "agent_session_id": agent_session_id,
            "runtime_session_id": child_runtime_session_id,
            "parent_runtime_session_id": parent_runtime_session_id,
            "resume_of": target,
            "resume_root": resume_root,
            "attempt": attempt,
            "resume_mode": resume_mode,
            "automatic_attempt_budget": NATIVE_RESUME_AUTOMATIC_ATTEMPT_BUDGET,
            "automatic_attempt_number": automatic_attempt_number,
            "settlement_revision": locked_revision,
            "trust_receipt_id": locked_receipt_id,
            "probe_state": provider_probe_state,
            "probe_version": provider_probe_version,
            "deduplicated": False,
            **(
                {"idempotency_key": resume_idempotency_key}
                if resume_idempotency_key
                else {}
            ),
            **(
                {"idempotency_receipt_error": idempotency_receipt_error}
                if idempotency_receipt_error
                else {}
            ),
        }
        append_event(
            kind="audit:native_resume",
            run_id=target,
            message=(
                "native resume dispatched"
                if accepted
                else "native resume launch failed"
            ),
            payload=payload,
        )
        return {
            "accepted": accepted,
            "run_id": target,
            "resume_run_id": child_run_id,
            "launch": launched,
            **payload,
        }


def block_run(
    run_id: str,
    *,
    reason: str = "operator block request",
    note: str = "",
) -> dict[str, Any]:
    """Mark an in-flight run as ``blocked`` with an audited lifecycle event.

    Blocking is an operator lever, not a signal: it records that the run needs
    human intervention and pins it to the terminal ``blocked`` state so the
    control plane stops treating it as active. A blocked run can later be
    resumed through :func:`retry_run` (which requires a terminal state).
    """
    target = str(run_id or "").strip()
    if not target:
        raise ValueError("run_id is required")
    with run_mutation_locks(control_plane_home(), run_id=target):
        return _block_run_locked(target, reason=reason, note=note)


def _block_run_locked(
    target: str,
    *,
    reason: str,
    note: str,
) -> dict[str, Any]:
    """Block implementation assuming the caller already holds the run's mutation lock."""
    run = lookup_run(target)
    if run is None:
        append_event(
            kind="audit:block",
            run_id=target,
            message="block rejected: run not found",
            payload={"accepted": False, "reason": "run_not_found"},
        )
        return {"accepted": False, "run_id": target, "reason": "run_not_found"}

    if _run_is_terminal(run):
        append_event(
            kind="audit:block",
            run_id=target,
            message="block rejected: run already terminal",
            payload={
                "accepted": False,
                "reason": "run_terminal",
                "state": run.get("state"),
            },
        )
        return {
            "accepted": False,
            "run_id": target,
            "reason": "run_terminal",
            "run": run,
        }

    append_event(
        kind="audit:block",
        run_id=target,
        message="run marked blocked",
        payload={
            "accepted": True,
            "reason": reason,
            "note": note,
            "state": "blocked",
        },
    )
    return {
        "accepted": True,
        "run_id": target,
        "reason": reason,
        "note": note,
        "run": lookup_run(target),
    }
