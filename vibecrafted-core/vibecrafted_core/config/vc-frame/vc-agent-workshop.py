#!/usr/bin/env python3
"""Agent Workspaces dashboard and interactive Agent launcher.

This is deliberately a terminal surface, not a second control plane.  vc-frame
owns the panes and tabs, Vibecrafted owns the launch command, and the User
chooses which project's live Frame session receives a new Agent tab.
"""

from __future__ import annotations

import argparse
import curses
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple


def _generation_python_candidates() -> list[str]:
    """Interpreters that can import vibecrafted_core without host PYTHONPATH.

    vc-frame ``bash -lc`` panes do not inherit the deck wrapper.  Match
    ``spawn_python_bin`` in runtime/scripts/lib/util.sh: env override, then the
    uv tool venv, then generation ``bin/python3``.  Never fall through to
    Homebrew ``python3`` — that is the 3.14 ModuleNotFoundError class.
    """
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share"))
    ordered: list[str] = []
    wanted = os.environ.get("VIBECRAFTED_PYTHON", "").strip()
    if wanted:
        ordered.append(wanted)
    for key in ("VIBECRAFTED_RUNTIME_ROOT", "VIBECRAFTED_ROOT"):
        root = os.environ.get(key, "").strip()
        if root:
            ordered.append(str(Path(root) / "bin" / "python3"))
    ordered.extend(
        (
            str(data / "uv" / "tools" / "vibecrafted" / "bin" / "python3"),
            str(data / "uv" / "tools" / "vibecrafted" / "bin" / "python"),
            str(data / "uv" / "tools" / "vibecrafted-core" / "bin" / "python3"),
            str(
                data
                / "vibecrafted"
                / "tools"
                / "vibecrafted-current"
                / "bin"
                / "python3"
            ),
        )
    )
    seen: set[str] = set()
    unique: list[str] = []
    for item in ordered:
        if not item or item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def ensure_generation_python() -> None:
    """Admit Python 3.11+ and core from the selected generation before imports.

    An importable ambient core is not generation evidence. A materialized pane
    must re-enter the selected interpreter; an in-tree pane owns its source core.
    """
    source_tree = Path(__file__).resolve().parents[3]
    in_tree = (source_tree / "vibecrafted_core" / "__init__.py").is_file()
    if in_tree:
        sys.path.insert(0, str(source_tree))
    selected = os.environ.get("VIBECRAFTED_RUNTIME_ROOT") or os.environ.get(
        "VIBECRAFTED_ROOT", ""
    )
    # Bootstrap can run on older host Python despite the package's 3.11 floor.
    minimum_python = (3, 11)
    try:
        if sys.version_info < minimum_python:
            raise ImportError("Python 3.11+ with tomllib is required")
        __import__("tomllib")
        core = __import__("vibecrafted_core")
        core_path = Path(core.__file__).resolve()
        if (
            not in_tree
            and selected
            and Path(selected).resolve() not in core_path.parents
        ):
            raise ImportError("core belongs to a different Runtime Pack")
    except (ImportError, SyntaxError):
        pass
    else:
        return
    # A wrapper can exec the same underlying Python. Bound the retry rather
    # than comparing executable paths and looping on a broken generation.
    attempt = os.environ.get("VIBECRAFTED_PANE_PYTHON_ATTEMPT", "")
    if attempt != str(Path(__file__).resolve()):
        here = os.path.realpath(sys.executable)
        for wanted in _generation_python_candidates():
            if not os.access(wanted, os.X_OK) or os.path.realpath(wanted) == here:
                continue
            if (
                selected
                and not in_tree
                and Path(selected).resolve() not in Path(wanted).resolve().parents
            ):
                continue
            os.environ["VIBECRAFTED_PANE_PYTHON_ATTEMPT"] = str(
                Path(__file__).resolve()
            )
            os.execv(wanted, [wanted, *sys.argv])
    raise SystemExit(
        "vc-agent-workshop: incompatible Python or Runtime Pack. "
        "Repair: set VIBECRAFTED_PYTHON to the selected Runtime Pack's bin/python3 "
        "and reopen this project through vc-start resume --repo <project-path>. "
        "Existing sessions can remain open."
    )


ensure_generation_python()

from vibecrafted_core.aicx_session_chain import (
    CliSessionChain,
    SessionChain,
    SessionChainError,
    SessionRecord,
    project_filter_for_root,
)
from vibecrafted_core.effort_overrides import EFFORT_OVERRIDE_STYLES
from vibecrafted_core.model_overrides import MODEL_OVERRIDE_FLAGS
from vibecrafted_core.repo_selection import RepoSelectionError, validate_workspace_root
from vibecrafted_core.runtime_paths import (
    selected_runtime_environment,
    vibecrafted_home,
)
from vibecrafted_core.server_config import load_agent_launch_config, validate_agent_pin
from vibecrafted_core.spawn import (
    CONTINUITY_MODES,
    OPERATOR_POLICIES,
    PERMISSION_POLICIES,
    RUNTIME_POLICIES,
    continuity_policy_capabilities,
    interactive_resume_support,
    resolve_operator_agent_policy,
    resolve_provider_policy,
    resumable_interactive_runs,
    runtime_policy_capabilities,
)
from vibecrafted_core.workspace_catalog import (
    WORKSPACE_STATUS_ACTIVE,
    WorkspaceCatalogError,
    operator_session_name,
    read_catalog,
    read_workspace_session,
    resolve_operator_place_session,
    resolve_run_workspace_identity,
)

AGENTS = ("agy", "claude", "codex", "cursor", "grok", "junie", "kimi", "copilot")
LAUNCH_MODES = ("init", "resume", "partner", "operator")
PURPOSES = ("Quick script", "Think with me", "Fix and verify")
PURPOSE_PROMPTS = (
    "Help me write a quick script. Agree on its scope before running it.",
    "Think through this project with me. Discuss options before making changes.",
    "Help me diagnose, fix and verify a problem. First agree on the problem and scope.",
)
MODE_PROMPTS = {"partner": "/vc-partner", "operator": "/vc-operator"}
# The configuration key stays `local-vm`; what runs is a local container.
RUNTIME_LABELS = {"local-vm": "local-container"}
RUNTIME_HELP = {
    "local-native": ("This checkout, shared with you.", ""),
    "local-worktrees": (
        "A separate branch-backed working copy under ~/.vibecrafted/worktrees.",
        "",
    ),
    "local-vm": (
        "A persistent local Docker container (not a VM); the project is mounted at /workspace.",
        "",
    ),
    "cloud-soon": ("Not available yet.", ""),
}
ADVANCED_ROWS = ("mode", "runtime", "permissions", "continuity")
# Ambient agent identities a launcher pane may inherit. They describe whoever
# opened this pane, never the parent the User chose for a new Agent.
_AMBIENT_LINEAGE_KEYS = (
    "VIBECRAFTED_RUN_ID",
    "CODEX_SESSION_ID",
    "CLAUDE_CODE_SESSION_ID",
    "VIBECRAFTED_OPERATOR_SESSION_ID",
)
_WORKSHOP_TITLES = frozenset({"agent workspaces", "new agent", "Voc", "start here"})
_AGENT_BINARIES = {
    "agy": "agy",
    "gemini": "agy",
    "claude": "claude",
    "claude-code": "claude",
    "codex": "codex",
    "cursor": "cursor",
    "cursor-agent": "cursor",
    "grok": "grok",
    "junie": "junie",
    "kimi": "kimi",
    "copilot": "copilot",
}
PRESENCE_SCOPE = "this session"
PRESENCE_REFRESH_SECONDS = 15.0
PRESENCE_MAX_REFRESH_SECONDS = 60.0
PRESENCE_ON_DEMAND_FLOOR_SECONDS = 2.0


def launch_argv(
    agent: str,
    mode: str,
    runtime: str = "local-native",
    permissions: str = "bypass",
    operator: str = "none",
    continuity: str = "fresh",
    continuity_parent: str = "",
    workspace: str | os.PathLike[str] = "",
    *,
    model: str = "",
    effort: str = "",
    purpose: int | None = None,
    session: str = "",
    run_id: str = "",
) -> list[str]:
    """Return the one canonical interactive command for a launcher choice."""
    if agent not in AGENTS:
        raise ValueError(f"unsupported agent: {agent}")
    if mode not in LAUNCH_MODES:
        raise ValueError(f"unsupported interactive mode: {mode}")
    if continuity not in CONTINUITY_MODES:
        raise ValueError(f"unsupported continuity policy: {continuity}")
    if model:
        validate_agent_pin(model, "model")
        if agent not in MODEL_OVERRIDE_FLAGS:
            raise ValueError("Model selection is unavailable for this provider")
    if purpose is not None and purpose not in range(len(PURPOSES)):
        raise ValueError("unsupported purpose")
    if effort:
        validate_agent_pin(effort, "effort")
        if agent not in EFFORT_OVERRIDE_STYLES:
            raise ValueError("Effort is unavailable for this provider")
    root = str(Path(workspace).expanduser().resolve()) if workspace else ""
    if mode != "resume":
        decision = resolve_provider_policy(agent, runtime, permissions, "interactive")
        if not decision.supported:
            raise ValueError(decision.reason)
        if operator not in OPERATOR_POLICIES:
            raise ValueError(f"unsupported Operator Agent policy: {operator}")
        operator_decision = resolve_operator_agent_policy(operator, runtime=runtime)
        if not operator_decision.supported:
            raise ValueError(operator_decision.reason)
        # `init` defaults to opening another vc-frame tab.  The workshop's law
        # is stricter: the destination pane this launcher opens becomes the
        # Agent TTY.  `--runtime plain` is that pane, not a nested composer.
        command = [
            "vibecrafted",
            "init",
            agent,
            "--runtime",
            "plain",
            "--policy-runtime",
            runtime,
            "--permissions",
            permissions,
            "--operator",
            operator_decision.selection,
            "--continuity",
            continuity,
        ]
        if root:
            command.extend(["--root", root])
        if continuity == "bare-fork":
            if not continuity_parent:
                raise ValueError(
                    "bare-fork requires an explicit parent provider-session id"
                )
            command.extend(["--parent-session", continuity_parent])
        elif continuity == "full-lineage" and continuity_parent:
            command.extend(["--continuity-parent", continuity_parent])
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["--effort", effort])
        if purpose is not None:
            command.extend(["--prompt", PURPOSE_PROMPTS[purpose]])
        elif mode in MODE_PROMPTS:
            command.extend(["--prompt", MODE_PROMPTS[mode]])
        return command
    supported, reason = interactive_resume_support(agent, runtime)
    if not supported:
        raise ValueError(reason)
    decision = resolve_provider_policy(agent, runtime, permissions, "interactive")
    if not decision.supported:
        raise ValueError(decision.reason)
    if session in {"current", "last"}:
        raise ValueError("Choose a concrete provider session")
    if not session and not run_id:
        raise ValueError("Resume needs a concrete session: choose one in Session (←/→)")
    # `--runtime plain`: this Frame tab is the Agent TTY. The recorded run
    # owns its checkout/container, so a run id never carries --root.
    command = [
        "vibecrafted",
        "resume",
        agent,
        "--runtime",
        "plain",
        "--permissions",
        permissions,
    ]
    if run_id:
        command.extend(["--run-id", run_id])
    else:
        if root:
            command.extend(["--root", root])
        command.extend(["--session", session])
    if runtime != "local-native":
        command.extend(["--policy-runtime", runtime])
    if model:
        command.extend(["--model", model])
    if effort:
        command.extend(["--effort", effort])
    return command


def current_frame_session(*, env: Mapping[str, str] | None = None) -> str:
    """Name of the Frame session hosting this pane, if the runtime exported one."""
    environ = env if env is not None else os.environ
    for key in (
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ_SESSION_NAME",
        "VIBECRAFTED_FRAME_SESSION",
    ):
        value = str(environ.get(key) or "").strip()
        if value:
            return value
    return ""


def destination_session_for_workspace(
    workspace: str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Prefer the hosting seat only with an exact project WES binding.

    Catalog names are the fallback for another project, never authority to
    displace an owned custom seat with an older same-project session.
    """
    current = owned_current_destination(Path(workspace).resolve(), env=env)
    if current:
        return current
    name = str(resolve_operator_place_session(root=workspace, env=env) or "").strip()
    if not name:
        raise ValueError("could not resolve a Frame session for that project")
    return name


def owned_current_destination(
    workspace: Path, *, env: Mapping[str, str] | None = None
) -> str:
    """Read-only admission of the physical seat attached to this logical session."""
    environ = env if env is not None else os.environ
    current = current_frame_session(env=environ)
    session_id = str(environ.get("VIBECRAFTED_SESSION_ID") or "").strip()
    if not current or not session_id:
        return ""
    try:
        receipt = read_workspace_session(session_id)
        record = read_catalog().workspaces.get(receipt.workspace_id)
        if (
            record is not None
            and record.status == WORKSPACE_STATUS_ACTIVE
            and Path(record.canonical_root).resolve() == workspace
            and receipt.session_id == session_id
            and receipt.workspace_id == environ.get("VIBECRAFTED_WORKSPACE_ID")
            and receipt.workspace_instance_id
            == environ.get("VIBECRAFTED_WORKSPACE_INSTANCE_ID")
            and any(
                item.runtime == "vc-frame"
                and item.runtime_session_id == current
                and item.state == "live"
                for item in receipt.attachments
            )
        ):
            return current
    except (OSError, WorkspaceCatalogError):
        pass
    return ""


def catalog_owns_destination(workspace: Path, session: str) -> bool:
    """A fallback basename is not evidence that a live seat belongs to this root."""
    if session and owned_current_destination(workspace) == session:
        return True
    try:
        catalog = read_catalog()
        return any(
            record.status == WORKSPACE_STATUS_ACTIVE
            and Path(record.canonical_root).resolve() == workspace
            and operator_session_name(
                record.workspace_id, display_label=record.display_label, catalog=catalog
            )
            == session
            for record in catalog.workspaces.values()
        )
    except (OSError, WorkspaceCatalogError):
        return False


PROJECT_OPEN_TIMEOUT = 45
PROJECT_LIVE_TIMEOUT = 3.0


class LiveDestination(NamedTuple):
    session: str
    environment: dict[str, str] | None


def launch_failure_reason(result: Any, fallback: str) -> str:
    """Keep both diagnostic streams, putting causes before startup progress."""
    lines = []
    progress = []
    for output in (result.stdout, result.stderr):
        for line in (output or "").splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("vc-start: ") and line.endswith(("...", "…")):
                progress.append(line)
            else:
                lines.append(line)
    return "\n".join(dict.fromkeys([*lines, *progress])) or fallback


def ensure_live_destination(
    workspace: Path, session: str, live_names: list[str]
) -> LiveDestination:
    """Let the selected product entry open a missing project, then admit its WES seat.

    vc-start owns creation, resurrection and host projection. Names and command
    output alone are never destination authority. Bind its child to one WES
    identity so recovery can be read from that exact receipt.
    """
    try:
        workspace = validate_workspace_root(workspace)
    except RepoSelectionError as exc:
        raise ValueError(f"Project could not be opened: {exc}") from exc
    session = str(session or "").strip()
    if not session:
        raise ValueError("could not resolve a Frame session for that project")
    if session in live_names:
        if catalog_owns_destination(workspace, session):
            return LiveDestination(session, None)
        raise ValueError(
            "Project could not be opened: a live Frame session has this name, "
            "but the workspace catalog does not bind it to this project"
        )
    try:
        env = selected_runtime_environment()
        root = env.get("VIBECRAFTED_RUNTIME_ROOT", "")
        if not root:
            raise ValueError("a selected Runtime Pack is required to open this project")
        creator = Path(root) / "bin" / "vc-start"
        if not creator.is_file() or not os.access(creator, os.X_OK):
            raise ValueError("vc-start is missing from the selected Runtime Pack")
        frame = creator.parent / "vc-frame"
        if not frame.is_file() or not os.access(frame, os.X_OK):
            raise ValueError("vc-frame is missing from the selected Runtime Pack")
        identity = resolve_run_workspace_identity(root=workspace, env=env)
        env.update(identity.to_env())
        env["VIBECRAFTED_WORKSPACE_ROOT"] = str(workspace)
        # The selected generation owns both its public entry and the Frame it probes.
        env["PATH"] = os.pathsep.join((str(creator.parent), env.get("PATH", "")))
        result = subprocess.run(
            [str(creator), "resume", "--repo", str(workspace)],
            cwd=str(workspace),
            env=env,
            stdin=subprocess.DEVNULL,
            check=False,
            capture_output=True,
            text=True,
            timeout=PROJECT_OPEN_TIMEOUT,
        )
        if result.returncode != 0:
            reason = launch_failure_reason(result, "vc-start refused the project")
            raise ValueError(reason)
        deadline = time.monotonic() + PROJECT_LIVE_TIMEOUT
        while True:
            names, error = list_live_frame_sessions(env=env)
            if error:
                raise ValueError(error)
            try:
                receipt = read_workspace_session(identity.vibecrafted_session_id)
            except WorkspaceCatalogError:
                receipt = None
            if receipt is not None:
                if (
                    receipt.workspace_id != identity.workspace_id
                    or receipt.workspace_instance_id != identity.workspace_instance_id
                    or receipt.session_id != identity.vibecrafted_session_id
                ):
                    raise ValueError(
                        "Frame receipt does not belong to the selected project"
                    )
                destinations = {
                    item.runtime_session_id
                    for item in receipt.attachments
                    if item.runtime == "vc-frame"
                    and item.state == "live"
                    and item.runtime_session_id in names
                }
                if len(destinations) == 1:
                    return LiveDestination(destinations.pop(), env)
                if len(destinations) > 1:
                    raise ValueError(
                        "multiple live Frame destinations belong to this project"
                    )
            if time.monotonic() >= deadline:
                raise ValueError(
                    "vc-start returned without an owned live Frame destination"
                )
            time.sleep(0.1)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Project could not be opened: vc-start timed out") from exc
    except (OSError, ValueError, WorkspaceCatalogError) as exc:
        raise ValueError(f"Project could not be opened: {exc}") from exc


def launch_pane_argv(
    title: str,
    workspace: str | os.PathLike[str],
    command: list[str],
    *,
    session: str,
) -> list[str]:
    """Open a new tab in the selected project's live Frame session.

    cwd does not route the workspace. ``--session`` is required so a launch
    from project A into project B cannot land beside the source workshop.
    """
    dest = str(session or "").strip()
    if not dest:
        raise ValueError("destination Frame session is missing")
    return [
        "vc-frame",
        "--session",
        dest,
        "action",
        "new-tab",
        "--name",
        title,
        "--cwd",
        str(workspace),
        "--",
        *command,
    ]


def attach_session_argv(session: str) -> list[str]:
    """Switch the attached client to the destination session's rail."""
    dest = str(session or "").strip()
    if not dest:
        raise ValueError("destination Frame session is missing")
    return ["vc-frame", "attach", dest]


def mode_capabilities(
    agent: str, runtime: str, permissions: str
) -> dict[str, dict[str, Any]]:
    """Describe every launch mode without hiding unsupported combinations."""
    decision = resolve_provider_policy(agent, runtime, permissions, "interactive")
    resume_available, resume_reason = interactive_resume_support(agent, runtime)
    if resume_available and not decision.supported:
        resume_available, resume_reason = False, decision.reason
    return {
        "init": {"available": decision.supported, "reason": decision.reason},
        "resume": {"available": resume_available, "reason": resume_reason},
        "partner": {"available": decision.supported, "reason": decision.reason},
        "operator": {"available": decision.supported, "reason": decision.reason},
    }


def parent_session_choices(
    agent: str,
    root: str | os.PathLike[str],
    *,
    chain: SessionChain | None = None,
) -> tuple[list[SessionRecord], str]:
    """Read parent choices from the canonical AICX session catalog."""
    root_path = Path(root).expanduser().resolve()
    # Parent choices are an exact-project answer. Without a canonical
    # owner/repo there is nothing to ask the catalog; a basename guess would
    # offer sessions from any same-named repository.
    project = project_filter_for_root(root_path)
    if not project:
        return [], (
            f"no canonical owner/repo for {root_path.name}; "
            "parent sessions need a git origin"
        )
    if chain is None:
        aicx = shutil.which("aicx")
        if not aicx:
            return [], "aicx executable not found; parent sessions unavailable"
        chain = CliSessionChain(aicx)
    try:
        result = chain.list_sessions(
            project=project,
            root=root_path,
            agent=agent,
            hours=24 * 30,
            limit=40,
        )
    except SessionChainError as exc:
        return [], f"session catalog {exc.kind}: {exc.message}"
    except OSError as exc:
        return [], f"session catalog unavailable: {exc}"
    sessions = sorted(result.sessions, key=lambda item: item.updated_at, reverse=True)
    if sessions:
        return sessions, ""
    reason = next(
        (warning for warning in result.warnings if warning),
        f"no {agent} sessions for {root_path.name}",
    )
    return [], reason


def normalized_workspace(raw: str, *, base: Path | None = None) -> Path:
    """Resolve and validate the full workspace path entered by the User."""
    root = (base or Path.cwd()).expanduser()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if not candidate.is_dir():
        raise ValueError(f"workspace does not exist: {candidate}")
    return candidate


def public_reason(reason: str) -> str:
    """Map internal policy text to a short User-facing sentence."""
    text = reason.strip()
    if not text:
        return ""
    low = text.casefold()
    if low.startswith("project could not be opened:"):
        return text
    # Environment causes are written for the User and carry the next step
    # (start Colima, share a path, rebuild); never fold them into a bucket.
    if any(
        token in low for token in ("docker", "colima", "local container", "/workspace")
    ):
        return text
    if "full-lineage needs a parent" in low or "parent lineage id" in low:
        return "Memory full-lineage needs a parent: pick one in Parent (←/→) or choose fresh"
    if "token limit" in low and "metering" in low:
        return "This token limit needs live usage metering; choose no limit"
    if low.startswith("resume needs") or "resume of an exact session" in low:
        return text
    # Parent-session and project reasons are not provider policy; keep them
    # out of the provider buckets below (an origin-less checkout is not an
    # uninstalled provider).
    if "owner/repo" in low or "git origin" in low:
        return "Parent sessions need a git origin"
    if "session catalog" in low or "aicx" in low:
        return "Parent sessions are unavailable right now"
    if "workspace does not exist" in low:
        return "That project folder does not exist"
    # Host VM unavailability is not a provider limit.  The token "canonical"
    # in "no canonical VM entrypoint" used to fall through to the provider
    # bucket and lie; keep this branch ahead of that gate.
    if "no canonical vm" in low or "docker/colima" in low:
        return "VM runtime is not available on this host"
    if any(
        token in low
        for token in (
            "child-usage",
            "child-attributable",
            "usage side channel",
            "monotonic usage",
        )
    ):
        if "coming" in low or "h2b" in low:
            return "Not available yet"
        return "Needs live usage metering"
    if any(token in low for token in ("canonical", "admission")):
        if "coming" in low or "h2b" in low:
            return "Not available yet"
        return "Not available for this provider"
    if "executable not found" in low:
        return "This provider is not installed"
    if "coming soon" in low or "coming in" in low or "h2b" in low:
        return "Not available yet"
    if "resume is supported only" in low:
        return "Resume works only in this checkout"
    if "expert-only" in low:
        return "Needs a parent session"
    if "no inherited memory" in low:
        return "Starts without earlier memory"
    if "git/dispatch manage_worktrees" in low:
        return "Separate working copies are not available here"
    if "could not resolve a frame session" in low:
        return "Could not resolve a Frame session for that project"
    if "destination frame session is missing" in low:
        return "Could not resolve a Frame session for that project"
    if len(text) > 72:
        return "Not available"
    return text


def _pane_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("panes", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


class AgentFace(NamedTuple):
    """One discoverable interactive Agent in the declared Frame session."""

    label: str
    tab: str
    liveness: str


class AgentPresence(NamedTuple):
    """Pane-state projection. Unknown is never counted live. Status is honest."""

    active: tuple[str, ...]
    unknown: tuple[str, ...]
    status: str = "ok"
    scope: str = PRESENCE_SCOPE
    faces: tuple[AgentFace, ...] = ()


def _pane_liveness(pane: dict[str, Any]) -> str:
    # vc-frame's public list-panes schema explicitly carries `exited` even
    # when it does not serialize a separate lifecycle/state field.  Terminal
    # exit evidence is authoritative if fields conflict: `exited: false` then
    # describes neither an open pane nor a live provider child.  A false value
    # without terminal evidence is only pane truth, not a provider-liveness
    # claim.
    if pane.get("exit_status") is not None or pane.get("exited") is True:
        return "inactive"
    if pane.get("exited") is False:
        return "active"
    state = (
        str(pane.get("state") or pane.get("lifecycle") or pane.get("status") or "")
        .strip()
        .casefold()
    )
    if state in {"running", "active", "alive", "connected"}:
        return "active"
    if state in {"exited", "closed", "dead", "terminated", "failed"}:
        return "inactive"
    return "unknown"


def _agent_from_command(command: str) -> str:
    tokens = command.split()
    joined = " ".join(tokens).casefold()
    if "vc-agent-workshop" in joined:
        return ""
    for agent in AGENTS:
        if (
            f"vibecrafted init {agent}" in joined
            or f"vibecrafted resume {agent}" in joined
        ):
            return agent
    if not tokens:
        return ""
    binary = Path(tokens[0]).name.casefold()
    return _AGENT_BINARIES.get(binary, "")


def _agent_face(pane: dict[str, Any]) -> AgentFace | None:
    if pane.get("is_plugin"):
        return None
    title = str(
        pane.get("pane_title") or pane.get("title") or pane.get("name") or ""
    ).strip()
    if title.casefold() in _WORKSHOP_TITLES:
        return None
    command = str(pane.get("command") or pane.get("pane_command") or "")
    tab = str(pane.get("tab_name") or pane.get("tab") or "").strip()
    label = ""
    if " · " in title:
        identity = title.split(" · ", 1)[0].casefold()
        if identity in AGENTS:
            label = title
    if not label:
        agent = _agent_from_command(command)
        if not agent:
            return None
        label = title or (f"{agent} · {tab}" if tab else agent)
    liveness = _pane_liveness(pane)
    if liveness == "inactive":
        return None
    if liveness == "unknown" and _agent_from_command(command):
        # An Agent command in an open pane is session-scope presence. It is
        # not a provider-health claim; missing `exited` must not hide it.
        liveness = "active"
    return AgentFace(label=label, tab=tab, liveness=liveness)


def agent_presence_from_payload(payload: Any) -> AgentPresence:
    """Project live interactive Agents in the current Frame session.

    Sidebar plugin labels are not liveness.  Titles without a provider
    contract or Agent command are not counted.  Every tab in this session
    is in scope — not only the Agents tab.
    """
    faces: list[AgentFace] = []
    for pane in _pane_rows(payload):
        face = _agent_face(pane)
        # Two open panes with one title are two Agents: count panes, never
        # collapse them by label.
        if face is not None:
            faces.append(face)
    active = tuple(face.label for face in faces if face.liveness == "active")
    unknown = tuple(face.label for face in faces if face.liveness == "unknown")
    return AgentPresence(
        active=active,
        unknown=unknown,
        status="ok",
        scope=PRESENCE_SCOPE,
        faces=tuple(faces),
    )


def agent_faces_from_payload(payload: Any) -> list[str]:
    """Active-agent labels for the dashboard list."""
    return list(agent_presence_from_payload(payload).active)


def presence_headline(
    status: str,
    count: int,
    *,
    stale: bool = False,
    scope: str = PRESENCE_SCOPE,
) -> str:
    if status == "unavailable" and count == 0 and not stale:
        return f"Agents in {scope} — unavailable"
    marker = ", stale" if stale else ""
    return f"Agents in {scope} ({count}{marker})"


def current_agent_presence() -> AgentPresence:
    try:
        result = subprocess.run(
            [
                "vc-frame",
                "action",
                "list-panes",
                "--json",
                "--state",
                "--tab",
                "--command",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if result.returncode != 0:
            return AgentPresence((), (), status="unavailable")
        return agent_presence_from_payload(json.loads(result.stdout))
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return AgentPresence((), (), status="unavailable")


def session_names_from_listing(text: str) -> list[str]:
    """Parse `vc-frame list-sessions --no-formatting` into live session names."""
    names: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        if "EXITED" in raw.upper():
            continue
        name = raw.split(" [", 1)[0].strip()
        name = name.split(" (", 1)[0].strip()
        if name and name not in names:
            names.append(name)
    return names


def list_live_frame_sessions(
    *, env: Mapping[str, str] | None = None
) -> tuple[list[str], str]:
    """Live Frame session names. Destination routing never guesses this list."""
    try:
        result = subprocess.run(
            [
                str(Path(env["VIBECRAFTED_RUNTIME_BIN"]) / "vc-frame")
                if env is not None
                else "vc-frame",
                "list-sessions",
                "--no-formatting",
            ],
            **({"env": dict(env)} if env is not None else {}),
            check=False,
            capture_output=True,
            text=True,
            timeout=1.5,
        )
    except FileNotFoundError:
        return [], "vc-frame is not available in this Runtime Pack"
    except subprocess.TimeoutExpired:
        return [], "Frame sessions are unavailable"
    if result.returncode != 0:
        return [], (
            result.stderr or result.stdout or "Frame sessions are unavailable"
        ).strip()
    return session_names_from_listing(result.stdout), ""


def list_other_sessions() -> tuple[list[str], str]:
    """On-demand Frame session names. Never polled from the redraw loop."""
    names, error = list_live_frame_sessions()
    if error:
        if error == "Frame sessions are unavailable":
            return [], "other sessions — unavailable"
        return [], error
    current = current_frame_session()
    if current:
        names = [name for name in names if name != current]
    return names, ""


class PresenceSchedule:
    """Decide when the dashboard may spawn a pane-state probe.

    Each probe is a short-lived `vc-frame action` CLI client.  Each attach
    makes the server broadcast Tab/Pane/Session updates to every plugin, so
    a 2 s poll flickered Plugin Manager, rail and bars.  The 500 ms redraw
    spawns nothing; probes run on first draw, on User input (2 s floor),
    and otherwise every 15 s doubling to 60 s while the answer is unchanged.
    """

    def __init__(
        self,
        *,
        base: float = PRESENCE_REFRESH_SECONDS,
        ceiling: float = PRESENCE_MAX_REFRESH_SECONDS,
        floor: float = PRESENCE_ON_DEMAND_FLOOR_SECONDS,
    ) -> None:
        self.base = base
        self.ceiling = ceiling
        self.floor = floor
        self.interval = base
        self.last_at: float | None = None
        self.requested = False

    def request(self) -> None:
        """Ask for a probe on the next draw that clears the on-demand floor."""
        self.requested = True

    def due(self, now: float) -> bool:
        if self.last_at is None:
            return True
        elapsed = now - self.last_at
        return elapsed >= (self.floor if self.requested else self.interval)

    def record(self, now: float, *, changed: bool) -> None:
        self.last_at = now
        self.requested = False
        self.interval = self.base if changed else min(self.ceiling, self.interval * 2)


# Curses pair 0 is COLOR_BLACK. Signed palettes put purple-navy `#26233a` in
# `colors.normal.black`, which is not dark paper (`#0b0b12`) and becomes a dark
# rectangle on light paper (`#fafafa`). Pair 1 at default/default follows the
# vc-frame / alacritty theme in both modes.
_PAPER_PAIR = 1
_PAPER = 0
_ACCENT = 0


def bind_terminal_paper(window: curses.window) -> int:
    """Paint with the host terminal paper; never ANSI black."""
    global _PAPER, _ACCENT
    curses.use_default_colors()
    curses.init_pair(_PAPER_PAIR, -1, -1)
    _PAPER = curses.color_pair(_PAPER_PAIR)
    window.bkgd(" ", _PAPER)
    window.bkgdset(" ", _PAPER)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    _ACCENT = curses.color_pair(2)
    return _PAPER


def _char_cells(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def _cell_width(text: str) -> int:
    """Terminal cells *text* occupies (wide CJK = 2, combining = 0)."""
    return sum(_char_cells(char) for char in text)


def _slice_cells(text: str, start: int, end: int) -> str:
    """The characters of *text* that occupy cells [start, end)."""
    out: list[str] = []
    cell = 0
    for char in text:
        width = _char_cells(char)
        if cell >= end:
            break
        if cell >= start:
            out.append(char)
        cell += width
    return "".join(out)


def _clip(text: str, width: int) -> str:
    """Clip to *width* terminal cells, marking a cut with an ellipsis."""
    if width <= 0:
        return ""
    if _cell_width(text) <= width:
        return text
    kept: list[str] = []
    used = 0
    for char in text:
        cells = _char_cells(char)
        if used + cells > width - 1:
            break
        kept.append(char)
        used += cells
    return "".join(kept) + "…"


def _choice_layout(
    tokens: tuple[str, ...], start_col: int, end_col: int
) -> list[tuple[int, int, int, str]]:
    """Visible span of every token as ``(index, start, end, fragment)``.

    Painting and mouse hitboxes both come from this one layout: the joined
    tokens are clipped once to ``end_col`` and each token keeps exactly the
    cells it occupies in that clipped line, so a click lands on what is shown
    after a resize, a cut, or wide characters.
    """
    visible = _clip(" ".join(tokens), end_col - start_col)
    visible_cells = _cell_width(visible)
    layout: list[tuple[int, int, int, str]] = []
    offset = 0
    for index, token in enumerate(tokens):
        cells = _cell_width(token)
        if offset >= visible_cells:
            break
        stop = min(offset + cells, visible_cells)
        layout.append(
            (
                index,
                start_col + offset,
                start_col + stop,
                _slice_cells(visible, offset, stop),
            )
        )
        offset += cells + 1
    return layout


def _safe_addstr(
    window: curses.window, row: int, col: int, text: str, attr: int = 0
) -> None:
    height, width = window.getmaxyx()
    if row < 0 or row >= height or col < 0 or col >= width:
        return
    try:
        color = 0 if attr & curses.A_COLOR else _PAPER
        window.addstr(row, col, _clip(text, width - col), attr | color)
    except curses.error:
        pass


def _dim_unavailable_choices(
    window: curses.window,
    row: int,
    col: int,
    choices: tuple[str, ...],
    available: tuple[bool, ...],
    selected: int,
    end_col: int,
    base: int = 0,
) -> list[tuple[int, int, int, str]]:
    """Paint unavailable tokens dim and the selected token bold in the accent color.

    Focus is a chevron beside the row,
    never an underline or strike through the glyphs, and never a reverse-video
    block. Returns the layout the caller turns into mouse hitboxes.
    """
    tokens = _choice_tokens(choices, selected=selected, available=available)
    # The base line clips the *whole* token sequence.  Slice that same rendered
    # sequence before applying token attributes so a trailing disabled token
    # cannot overwrite its ellipsis or drift relative to the selected token.
    layout = _choice_layout(tokens, col, end_col)
    for index, start, _end, fragment in layout:
        if not available[index]:
            _safe_addstr(window, row, start, fragment, curses.A_DIM | base)
        elif index == selected:
            _safe_addstr(window, row, start, fragment, curses.A_BOLD | _ACCENT | base)
    return layout


def _choice_tokens(
    choices: tuple[str, ...],
    *,
    selected: int,
    available: tuple[bool, ...] | None = None,
) -> tuple[str, ...]:
    """Bare option labels. The selected one is painted bold; unavailable is dim."""
    _ = (selected, available)
    return tuple(choices)


def _paint_row_focus(window: curses.window, row: int, col: int, focused: bool) -> int:
    """Chevron in the gutter. Option glyphs stay unmarked and unshifted."""
    if col > 0:
        _safe_addstr(
            window,
            row,
            col - 1,
            "›" if focused else " ",
            curses.A_BOLD if focused else 0,
        )
    return col


def _provider_available(agent: str) -> bool:
    capabilities = runtime_policy_capabilities(agent)
    return any(bool(capabilities[name]["available"]) for name in RUNTIME_POLICIES)


class Workshop:
    def __init__(self, window: curses.window, *, mode: str) -> None:
        self.window = window
        self.mode = mode
        self.standalone_launcher = mode == "launcher"
        self.home_choice = 0
        self.row = 0
        self.agent = 2  # codex is the least surprising neutral default here
        self.launch_mode = 0
        self.runtime = (
            0  # start in the selected project; isolation is an explicit choice
        )
        self.permissions = 0
        self.continuity = 0
        # Memory is normalized to what is available until the User picks one;
        # an explicit pick is never silently replaced (full-lineage ≠ fresh).
        self.continuity_explicit = False
        self.continuity_parent = ""
        self.resume_candidates: list[dict[str, Any]] | None = None
        self.resume_index = -1
        self.resume_run_id = ""
        self.resume_error = ""
        self._mouse_down: tuple[int, int] | None = None
        self._drawn_size: tuple[int, int] | None = None
        self.parent_sessions: list[SessionRecord] = []
        self.parent_index = -1
        self.parent_error = ""
        self.path = str(
            Path(os.environ.get("VIBECRAFTED_WORKSPACE_ROOT") or Path.cwd())
            .expanduser()
            .resolve()
        )
        self.error = ""
        self.error_details = False
        self.error_scroll = 0
        self.notice = ""
        self.mouse_targets: list[tuple[int, int, int, Any, str]] = []
        self.presence_schedule = PresenceSchedule()
        self.faces: list[str] = []
        self.unknown_faces: list[str] = []
        self.face_records: tuple[AgentFace, ...] = ()
        self.presence_status = "ok"
        self.presence_stale = False
        self.advanced = False
        self.other_sessions: list[str] = []
        self.other_error = ""
        self.show_other = False
        self.model = ""
        self.effort = ""
        self.purpose = 1
        self.session = ""
        self.edit_controls = False
        self.active_slot = 0
        self.choices_root = self.path
        self.agent_slots: list[dict[str, Any]] = [{}]
        self.launch_results: list[dict[str, str]] = []
        self._load_choices(restore_slots=True)

    def _choice_path(self) -> Path:
        # UI preferences belong to this launcher, not the workspace identity
        # catalog or server configuration. Each canonical root owns one file.
        digest = hashlib.sha256(
            str(Path(self.path).expanduser().resolve()).encode()
        ).hexdigest()
        return vibecrafted_home() / "store" / "agent-workshop" / f"{digest}.json"

    def _snapshot(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in (
                "agent",
                "launch_mode",
                "runtime",
                "permissions",
                "continuity",
                "continuity_explicit",
                "continuity_parent",
                "model",
                "effort",
                "purpose",
                "session",
                "resume_run_id",
            )
        }

    def _restore(self, values: dict[str, Any]) -> None:
        for name, value in values.items():
            if name in self._snapshot():
                setattr(self, name, value)
        self.parent_sessions = []
        self.parent_index = -1
        self.resume_candidates = None
        self.resume_index = -1

    def _read_choices(self) -> dict[str, Any]:
        try:
            data = json.loads(self._choice_path().read_text())
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("unsupported preferences version")
            if not isinstance(data.get("providers", {}), dict):
                raise TypeError("invalid provider preferences")
            return data
        except FileNotFoundError:
            return {"version": 1, "providers": {}}
        except (OSError, ValueError, TypeError) as exc:
            self.error = f"Saved choices unavailable: {exc}"
            return {"version": 1, "providers": {}}

    def _load_choices(self, *, restore_slots: bool = False) -> None:
        data = self._read_choices()
        if restore_slots:
            slots = data.get("slots", [])
            if isinstance(slots, list) and 1 <= len(slots) <= 3:
                restored = []
                for slot in slots:
                    if not isinstance(slot, dict) or slot.get("provider") not in AGENTS:
                        break
                    values = {"agent": AGENTS.index(slot["provider"])}
                    for name in ("model", "effort"):
                        value = slot.get(name, "")
                        if not isinstance(value, str):
                            break
                        if value:
                            try:
                                validate_agent_pin(value, name)
                            except ValueError:
                                break
                        values[name] = value
                    else:
                        purpose = slot.get("purpose", 1)
                        if isinstance(purpose, int) and purpose in range(len(PURPOSES)):
                            values["purpose"] = purpose
                            # Each slot keeps its own launch semantics; an
                            # unknown or retired name restores the default.
                            for name, choices in (
                                ("runtime", RUNTIME_POLICIES),
                                ("permissions", PERMISSION_POLICIES),
                                ("launch_mode", LAUNCH_MODES),
                                ("continuity", CONTINUITY_MODES),
                            ):
                                chosen = slot.get(name)
                                if isinstance(chosen, str) and chosen in choices:
                                    values[name] = choices.index(chosen)
                            if slot.get("continuity_explicit") is True:
                                values["continuity_explicit"] = True
                            restored.append(values)
                            continue
                    break
                if len(restored) == len(slots):
                    self.agent_slots = restored
                    self.agent = restored[0]["agent"]
        provider = AGENTS[self.agent]
        try:
            defaults = load_agent_launch_config(provider)
            self.model, self.effort = defaults.model, ""
            # Empty effort leaves the existing runtime config default intact;
            # only an explicit UI pin needs the missing admission extension.
        except ValueError as exc:
            self.model, self.effort = "", ""
            self.error = str(exc)
        self.purpose = 1
        saved = data.get("providers", {}).get(provider, {})
        if not isinstance(saved, dict):
            self.error = "Saved provider choices are invalid"
            return
        for name in ("model", "effort"):
            value = saved.get(name, "")
            if isinstance(value, str) and value:
                try:
                    validate_agent_pin(value, name)
                except ValueError:
                    self.error = f"Saved {name} is invalid"
                else:
                    setattr(self, name, value)
        purpose = saved.get("purpose", 1)
        if isinstance(purpose, int) and purpose in range(len(PURPOSES)):
            self.purpose = purpose
        if restore_slots:
            self._restore(self.agent_slots[0])
        if provider not in EFFORT_OVERRIDE_STYLES:
            self.effort = ""

    def save_choices(self) -> None:
        self.agent_slots[self.active_slot] = self._snapshot()
        data = self._read_choices()
        data.setdefault("providers", {})[AGENTS[self.agent]] = {
            "model": self.model,
            "effort": self.effort,
            "purpose": self.purpose,
        }
        data["slots"] = [
            {
                "provider": AGENTS[s.get("agent", self.agent)],
                **{
                    key: s.get(key, getattr(self, key))
                    for key in ("model", "effort", "purpose")
                },
                "runtime": RUNTIME_POLICIES[s.get("runtime", self.runtime)],
                "permissions": PERMISSION_POLICIES[
                    s.get("permissions", self.permissions)
                ],
                "launch_mode": LAUNCH_MODES[s.get("launch_mode", self.launch_mode)],
                "continuity": CONTINUITY_MODES[s.get("continuity", self.continuity)],
                "continuity_explicit": bool(
                    s.get("continuity_explicit", self.continuity_explicit)
                ),
            }
            for s in self.agent_slots
        ]
        path = self._choice_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic publication; sessions and launch status are intentionally not
        # remembered, because another visit is a new explicit launch batch.
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False
        ) as stream:
            json.dump(data, stream)
            temporary = Path(stream.name)
        temporary.replace(path)

    def select_slot(self, index: int) -> None:
        if not 0 <= index < len(self.agent_slots):
            return
        self.agent_slots[self.active_slot] = self._snapshot()
        self.active_slot = index
        values = self.agent_slots[index]
        self.agent = values.get("agent", self.agent)
        self._load_choices()
        self._restore(values)
        self.row = 0

    def add_agent(self) -> None:
        if len(self.agent_slots) == 3:
            self.error = "Choose at most 3 agents"
            return
        self.agent_slots[self.active_slot] = self._snapshot()
        self.agent_slots.append({"agent": self.agent})
        self.select_slot(len(self.agent_slots) - 1)
        self.session = ""
        self.continuity_parent = ""

    def remove_agent(self) -> None:
        if len(self.agent_slots) == 1:
            self.error = "Keep at least one agent"
            return
        self.agent_slots.pop(self.active_slot)
        if self.launch_results:
            self.launch_results.pop(self.active_slot)
        self.active_slot = min(self.active_slot, len(self.agent_slots) - 1)
        self._restore(self.agent_slots[self.active_slot])

    def configure(self) -> None:
        try:
            bind_terminal_paper(self.window)
        except curses.error:
            pass
        curses.curs_set(0)
        curses.noecho()
        curses.cbreak()
        self.window.keypad(True)
        self.window.timeout(500)
        try:
            # Report raw presses: no click resolution delay, and a press is
            # the single activation of one physical click.
            curses.mouseinterval(0)
        except curses.error:
            pass
        self._normalize_runtime_choice()
        self._normalize_permission_choice()
        self._normalize_mode_choice()
        self._normalize_continuity_choice()
        try:
            # Presses and releases only: hover/motion reports are not choices.
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
        except curses.error:
            pass

    def run(self) -> None:
        self.configure()
        while True:
            self.draw()
            key = self.window.getch()
            if key == -1:
                continue
            if key == curses.KEY_MOUSE:
                self.handle_mouse()
                continue
            self.presence_schedule.request()
            if key == curses.KEY_RESIZE:
                continue
            if self.mode == "home":
                self.handle_home_key(key)
            else:
                self.handle_launcher_key(key)

    def draw(self) -> None:
        self.window.erase()
        self.mouse_targets.clear()
        try:
            self._drawn_size = tuple(self.window.getmaxyx())
        except (AttributeError, curses.error):
            self._drawn_size = None
        if self.mode == "home":
            self.draw_home()
        else:
            self.draw_launcher()
        self.window.refresh()

    def _refresh_presence(self) -> None:
        now = time.monotonic()
        if not self.presence_schedule.due(now):
            return
        presence = current_agent_presence()
        if presence.status == "unavailable":
            if self.faces or self.unknown_faces:
                self.presence_stale = True
            else:
                self.presence_status = "unavailable"
            self.presence_schedule.record(now, changed=False)
            return
        faces = list(presence.active)
        unknown = list(presence.unknown)
        changed = self.presence_schedule.last_at is None or (
            faces,
            unknown,
            presence.status,
        ) != (self.faces, self.unknown_faces, self.presence_status)
        self.faces = faces
        self.unknown_faces = unknown
        # Home rows index `self.faces` (active only); keep the tab records
        # aligned with them so a click opens the tab of the row it hit.
        self.face_records = tuple(
            face for face in presence.faces if face.liveness == "active"
        )
        self.presence_status = "ok"
        self.presence_stale = False
        self.presence_schedule.record(now, changed=changed)

    def draw_home(self) -> None:
        height, width = self.window.getmaxyx()
        compact = height < 14 or width < 48
        left = 1 if compact else max(2, (width - min(width - 4, 78)) // 2)
        top = 0 if compact else max(1, min(4, (height - 16) // 2))
        self._refresh_presence()
        _safe_addstr(self.window, top, left, "Agents", curses.A_BOLD)
        workspace_name = Path(self.path).name or self.path
        if not compact:
            _safe_addstr(
                self.window,
                top + 1,
                left,
                f"{workspace_name} · {PRESENCE_SCOPE}",
                curses.A_DIM,
            )
        headline = presence_headline(
            self.presence_status,
            len(self.faces),
            stale=self.presence_stale,
            scope=PRESENCE_SCOPE,
        )
        headline_row = top + (1 if compact else 3)
        _safe_addstr(self.window, headline_row, left, headline, curses.A_BOLD)
        list_row = headline_row + 1
        if self.presence_status == "unavailable" and not self.faces:
            _safe_addstr(
                self.window,
                list_row,
                left,
                "Pane list is unavailable — not zero.",
                curses.A_DIM,
            )
            list_row += 1
        elif self.faces:
            budget = max(1, height - list_row - (4 if compact else 6))
            for offset, face in enumerate(self.faces[:budget]):
                tab = ""
                if offset < len(self.face_records):
                    tab = self.face_records[offset].tab
                suffix = f"  [{tab}]" if tab and tab.casefold() != "agents" else ""
                line = f"  {face}{suffix}"
                _safe_addstr(self.window, list_row + offset, left, line)
                self.mouse_targets.append(
                    (
                        list_row + offset,
                        left,
                        left + min(len(line), max(1, width - left)),
                        offset,
                        "face",
                    )
                )
            list_row += min(len(self.faces), budget)
        else:
            _safe_addstr(
                self.window,
                list_row,
                left,
                "No live Agents in this session yet.",
                curses.A_DIM,
            )
            list_row += 1
        if self.unknown_faces and not compact:
            _safe_addstr(
                self.window,
                list_row,
                left,
                f"Unverified ({len(self.unknown_faces)}) — titles only, not counted live",
                curses.A_DIM,
            )
            list_row += 1
        # Voc has one global entry beside Composer in the topbar; the Agents
        # home offers only what belongs to this project.
        buttons = ("New agent",)
        col = left
        button_row = min(height - 3, list_row + (0 if compact else 1))
        for index, label in enumerate(buttons):
            text = f"[ {label} ]"
            attr = curses.A_REVERSE if index == self.home_choice else curses.A_BOLD
            _safe_addstr(self.window, button_row, col, text, attr)
            self.mouse_targets.append((button_row, col, col + len(text), index, "home"))
            col += len(text) + 2
        if self.show_other:
            other_row = min(height - 2, button_row + 1)
            if self.other_error:
                _safe_addstr(
                    self.window, other_row, left, self.other_error, curses.A_DIM
                )
            elif self.other_sessions:
                _safe_addstr(
                    self.window,
                    other_row,
                    left,
                    "Other sessions (Enter opens): "
                    + ", ".join(self.other_sessions[:4]),
                    curses.A_DIM,
                )
            else:
                _safe_addstr(
                    self.window,
                    other_row,
                    left,
                    "No other Frame sessions listed.",
                    curses.A_DIM,
                )
        hint = (
            "n New  o other sessions"
            if compact
            else "n New agent · o other sessions · click a row to open its tab"
        )
        _safe_addstr(self.window, height - 2, left, hint, curses.A_DIM)
        if self.error:
            _safe_addstr(self.window, height - 1, left, self.error, curses.A_BOLD)

    def _draw_providers(self, row: int, col: int, width: int) -> None:
        x = _paint_row_focus(self.window, row, col, self.row == 0)
        for index, name in enumerate(AGENTS):
            token = f" {name} "
            if x + len(token) >= col + width:
                row += 1
                x = col
            available = _provider_available(name)
            if index == self.agent and available:
                attr = curses.A_BOLD | _ACCENT
            elif available:
                attr = 0
            else:
                attr = curses.A_DIM
            _safe_addstr(self.window, row, x, token, attr)
            self.mouse_targets.append((row, x, x + len(token), index, "provider"))
            x += len(token) + 1

    def draw_launcher(self) -> None:
        if self.error_details:
            self.draw_launch_error()
            return
        height, width = self.window.getmaxyx()
        compact = height < 16 or width < 52
        left = 1 if compact else max(1, (width - min(width - 2, 84)) // 2)
        top = (
            0
            if compact
            else max(
                1,
                (
                    height
                    - (
                        19
                        if self.advanced
                        else 18
                        if self.edit_controls or self.launch_results
                        else 11
                    )
                )
                // 2,
            )
        )
        inner = max(12, width - left - 2)
        _safe_addstr(self.window, top, left, "New agent", curses.A_BOLD)
        _safe_addstr(
            self.window,
            top + 1,
            left,
            "Choose a provider, then Launch.",
            curses.A_DIM,
        )
        slot_col = left
        for index, slot in enumerate(self.agent_slots):
            provider = (
                AGENTS[self.agent]
                if index == self.active_slot
                else AGENTS[slot.get("agent", self.agent)]
            )
            label = f"[{index + 1} {provider}] "
            _safe_addstr(
                self.window,
                top + 2,
                slot_col,
                label,
                curses.A_BOLD if index == self.active_slot else 0,
            )
            self.mouse_targets.append(
                (top + 2, slot_col, slot_col + len(label), index, "slot")
            )
            slot_col += len(label)
        if len(self.agent_slots) < 3:
            _safe_addstr(self.window, top + 2, slot_col, "[+ agent]", curses.A_BOLD)
            self.mouse_targets.append((top + 2, slot_col, slot_col + 9, 0, "add"))
        self._draw_providers(top + 3, left, inner)
        path_row = top + 5
        path_col = _paint_row_focus(self.window, path_row, left, self.row == 1)
        _safe_addstr(
            self.window,
            path_row,
            path_col,
            _clip(f"Project  {self.path}", inner),
            0,
        )
        controls = f"Choices · {self.model or 'provider model'} · {self.effort or 'default effort'} · {PURPOSES[self.purpose]}"
        _safe_addstr(
            self.window, path_row + 1, left, _clip(controls, inner), curses.A_DIM
        )
        self.mouse_targets.append(
            (path_row + 1, left, left + min(len(controls), inner), 0, "controls")
        )
        toggle = (
            "▾" if self.advanced else "▸"
        ) + " Advanced options · click or press a"
        toggle_row = path_row + 2
        _safe_addstr(self.window, toggle_row, left, _clip(toggle, inner), curses.A_BOLD)
        self.mouse_targets.append(
            (toggle_row, left, left + min(len(toggle), inner), 0, "advanced")
        )
        cursor = toggle_row + 1
        if self.advanced:
            rows = self._advanced_model()
            last_row = height - 3
            drawn_rows = 0
            for offset, row in enumerate(rows):
                y = cursor + offset
                if y >= last_row:
                    break
                focused = self.row == offset + 2
                text_col = _paint_row_focus(self.window, y, left, focused)
                end_col = min(text_col + inner, width)
                line = row["label"] + " ".join(row["labels"])
                _safe_addstr(
                    self.window, y, text_col, _clip(line, end_col - text_col), 0
                )
                layout = _dim_unavailable_choices(
                    self.window,
                    y,
                    text_col + _cell_width(row["label"]),
                    row["labels"],
                    row["available"],
                    row["selected"],
                    end_col,
                    base=0,
                )
                for index, start, stop, _fragment in layout:
                    self.mouse_targets.append(
                        (y, start, stop, (offset, index), "choice")
                    )
                drawn_rows += 1
            parent_y = cursor + len(rows)
            if drawn_rows == len(rows) and parent_y < last_row:
                parent_col = _paint_row_focus(
                    self.window, parent_y, left, self.row == 6
                )
                parent_text = _clip(self._parent_row_text(), inner)
                _safe_addstr(self.window, parent_y, parent_col, parent_text, 0)
                self.mouse_targets.append(
                    (
                        parent_y,
                        parent_col,
                        min(width, parent_col + _cell_width(parent_text)),
                        0,
                        "parent",
                    )
                )
            if not compact:
                runtime_name = RUNTIME_POLICIES[self.runtime]
                help_lines = self._runtime_help_lines(runtime_name)
                for extra, text in enumerate(help_lines[:2]):
                    _safe_addstr(
                        self.window,
                        cursor + 5 + extra,
                        left,
                        _clip(text, inner),
                        curses.A_DIM,
                    )
                # Disabled choices keep a visible reason in plain words.  The
                # admission gates themselves live in spawn; only wording is public.
                # Environment first: in a narrow pane it is the reason that
                # decides whether anything else matters.
                ordered = sorted(
                    rows,
                    key=lambda row: (
                        "runtime",
                        "mode",
                        "permissions",
                        "continuity",
                    ).index(row["key"]),
                )
                unavailable = [
                    f"{row['labels'][index]}: "
                    f"{public_reason(row['reasons'][index]) or 'Not available'}"
                    for row in ordered
                    for index, ok in enumerate(row["available"])
                    if not ok
                ]
                if unavailable:
                    _safe_addstr(
                        self.window,
                        cursor + 7,
                        left,
                        _clip("Unavailable — " + " · ".join(unavailable), inner),
                        curses.A_DIM,
                    )
                cursor += 9
            else:
                cursor += min(len(rows) + 1, max(0, last_row - cursor))
        if self.edit_controls:
            fields = (
                ("Model", self.model or "(provider default)", 7),
                (
                    "Effort",
                    self.effort
                    or (
                        "(provider default)"
                        if AGENTS[self.agent] in EFFORT_OVERRIDE_STYLES
                        else "unavailable"
                    ),
                    8,
                ),
                ("Purpose", PURPOSES[self.purpose], 9),
                ("Session", self.session or "(fresh; exact ID to resume)", 10),
            )
            if compact:
                fields = (
                    tuple(field for field in fields if field[2] == self.row)
                    or fields[:1]
                )
            for offset, (label, value, focus_row) in enumerate(fields):
                text_col = _paint_row_focus(
                    self.window, cursor + offset, left, self.row == focus_row
                )
                _safe_addstr(
                    self.window,
                    cursor + offset,
                    text_col,
                    _clip(f"{label:10}{value}", inner),
                )
                self.mouse_targets.append(
                    (cursor + offset, left, left + inner, focus_row, "field")
                )
            cursor += len(fields)
        launch_label = "[ Launch ]"
        launch_attr = curses.A_BOLD
        _safe_addstr(self.window, cursor, left, launch_label, launch_attr)
        self.mouse_targets.append((cursor, left, left + len(launch_label), 0, "launch"))
        for offset, result in enumerate(self.launch_results):
            label = f"{offset + 1} {result.get('provider', '')}: {result['status']}"
            if result["reason"]:
                reason = public_reason(result["reason"]) or result["reason"]
                label += f" · {reason.splitlines()[0]}"
            _safe_addstr(
                self.window,
                cursor + 1 + offset,
                left,
                _clip(label, inner),
                curses.A_BOLD,
            )
        hint = (
            "Enter launch  Esc back"
            if compact
            else "←/→ provider · +/- agent · [/] slot · c choices · a advanced · Enter launch · Esc back"
        )
        if self.error:
            hint = "e error details · " + hint
        _safe_addstr(self.window, height - 2, left, hint, curses.A_DIM)
        if self.error:
            _safe_addstr(
                self.window,
                height - 1,
                left,
                (public_reason(self.error) or self.error).splitlines()[0],
            )
        elif self.notice:
            _safe_addstr(self.window, height - 1, left, self.notice, curses.A_BOLD)

    def draw_launch_error(self) -> None:
        height, width = self.window.getmaxyx()
        lines = [
            wrapped
            for line in self.error.splitlines()
            for wrapped in (textwrap.wrap(line, max(1, width - 3)) or [""])
        ]
        available = max(1, height - 3)
        self.error_scroll = min(self.error_scroll, max(0, len(lines) - available))
        _safe_addstr(self.window, 0, 1, "Launch error", curses.A_BOLD)
        for row, line in enumerate(
            lines[self.error_scroll : self.error_scroll + available], start=1
        ):
            _safe_addstr(self.window, row, 1, line)
        _safe_addstr(
            self.window,
            height - 1,
            1,
            "↑/↓ scroll · Esc back to launcher",
            curses.A_DIM,
        )

    def _launcher_env(self) -> dict[str, str]:
        """This pane's environment without inherited agent identities."""
        return {
            key: value
            for key, value in os.environ.items()
            if key not in _AMBIENT_LINEAGE_KEYS
        }

    def _continuity_caps(
        self, root: str | os.PathLike[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        caps = continuity_policy_capabilities(
            AGENTS[self.agent],
            root=root or self.path,
            explicit_parent=self.continuity_parent,
            env=self._launcher_env(),
        )
        if RUNTIME_POLICIES[self.runtime] == "local-vm":
            caps["bare-fork"] = {
                **caps["bare-fork"],
                "available": False,
                "reason": "bare-fork is not available in the local container",
            }
        return caps

    def _resume_mode(self) -> bool:
        return LAUNCH_MODES[self.launch_mode] == "resume"

    def _advanced_model(self) -> list[dict[str, Any]]:
        """One description of the four choice rows: drawing, clicks and reasons."""
        provider = AGENTS[self.agent]
        runtime_name = RUNTIME_POLICIES[self.runtime]
        runtime_caps = runtime_policy_capabilities(provider)
        permission_decisions = [
            resolve_provider_policy(provider, runtime_name, name, "interactive")
            for name in PERMISSION_POLICIES
        ]
        mode_caps = mode_capabilities(
            provider, runtime_name, PERMISSION_POLICIES[self.permissions]
        )
        continuity_caps = self._continuity_caps()
        resume_reason = (
            "Resume continues the chosen session; memory applies to new sessions"
        )
        rows = [
            {
                "key": "mode",
                "label": "Mode      ",
                "values": LAUNCH_MODES,
                "selected": self.launch_mode,
                "available": tuple(
                    bool(mode_caps[n]["available"]) for n in LAUNCH_MODES
                ),
                "reasons": tuple(str(mode_caps[n]["reason"]) for n in LAUNCH_MODES),
            },
            {
                "key": "runtime",
                "label": "Runtime   ",
                "values": RUNTIME_POLICIES,
                "selected": self.runtime,
                "available": tuple(
                    bool(runtime_caps[n]["available"]) for n in RUNTIME_POLICIES
                ),
                "reasons": tuple(
                    str(runtime_caps[n].get("reason") or "") for n in RUNTIME_POLICIES
                ),
            },
            {
                "key": "permissions",
                "label": "Permits   ",
                "values": PERMISSION_POLICIES,
                "selected": self.permissions,
                "available": tuple(d.supported for d in permission_decisions),
                "reasons": tuple(d.reason for d in permission_decisions),
            },
            {
                "key": "continuity",
                "label": "Memory    ",
                "values": CONTINUITY_MODES,
                "selected": self.continuity,
                "available": tuple(
                    False
                    if self._resume_mode()
                    else bool(continuity_caps[n]["available"])
                    for n in CONTINUITY_MODES
                ),
                "reasons": tuple(
                    resume_reason
                    if self._resume_mode()
                    else str(continuity_caps[n]["reason"])
                    for n in CONTINUITY_MODES
                ),
            },
        ]
        for row in rows:
            row["labels"] = tuple(RUNTIME_LABELS.get(v, v) for v in row["values"])
        return rows

    def _runtime_help_lines(self, runtime_name: str) -> list[str]:
        first = RUNTIME_HELP[runtime_name][0]
        caps = runtime_policy_capabilities(AGENTS[self.agent]).get(runtime_name, {})
        notes: list[str] = []
        if runtime_name == "local-vm" and caps.get("available"):
            notes.append(
                "Image ready."
                if caps.get("image_ready")
                else "First Launch builds the image in the new tab (several minutes)."
            )
        metering = caps.get("metering") or {}
        if caps.get("available") and metering:
            notes.append(
                "Usage: live metering."
                if metering.get("state") == "live"
                else "Usage: no data (not metered); no token limit is applied."
            )
        return [first, " ".join(notes)] if notes else [first]

    def _session_label(self, candidate: dict[str, Any]) -> str:
        stamp = str(candidate.get("updated_at") or "")[:16].replace("T", " ")
        branch = str(candidate.get("branch") or "")
        where = f" · {branch}" if branch else ""
        return f"{candidate['session_id']} · {stamp}{where}"

    def _parent_row_text(self) -> str:
        if not self._resume_mode():
            return f"Parent    {self.continuity_parent or '(none)'}"
        if (
            self.resume_run_id
            and self.resume_candidates
            and 0 <= self.resume_index < len(self.resume_candidates)
        ):
            return "Session   " + self._session_label(
                self.resume_candidates[self.resume_index]
            )
        if self.session:
            return f"Session   {self.session}"
        if self.resume_candidates == []:
            return f"Session   (none: {self.resume_error})"
        return "Session   (choose with ←/→ or click — earlier sessions here)"

    def _refresh_resume_candidates(self) -> None:
        provider = AGENTS[self.agent]
        runtime_name = RUNTIME_POLICIES[self.runtime]
        try:
            self.resume_candidates = resumable_interactive_runs(
                provider, self.path, runtime_name
            )
        except (OSError, ValueError) as exc:
            self.resume_candidates = []
            self.resume_error = f"session catalog unavailable: {exc}"
            return
        self.resume_index = -1
        label = RUNTIME_LABELS.get(runtime_name, runtime_name)
        self.resume_error = (
            ""
            if self.resume_candidates
            else f"no earlier {provider} session in {label} for {Path(self.path).name}"
        )

    def _cycle_resume(self, delta: int) -> None:
        if self.resume_candidates is None:
            self._refresh_resume_candidates()
        if not self.resume_candidates:
            self.error = f"Resume needs a session: {self.resume_error}"
            return
        self.resume_index = (
            self.resume_index + delta
            if self.resume_index >= 0
            else (0 if delta > 0 else len(self.resume_candidates) - 1)
        ) % len(self.resume_candidates)
        chosen = self.resume_candidates[self.resume_index]
        self.resume_run_id = chosen["run_id"]
        self.session = chosen["session_id"]

    def _reset_resume(self) -> None:
        self.resume_candidates = None
        self.resume_index = -1
        self.resume_run_id = ""
        self.resume_error = ""

    def _choose_advanced(self, offset: int, index: int) -> None:
        """Apply one clicked value; an unavailable one explains itself."""
        rows = self._advanced_model()
        if not 0 <= offset < len(rows):
            return
        row = rows[offset]
        if not 0 <= index < len(row["values"]):
            return
        self.row = offset + 2
        if not row["available"][index]:
            reason = public_reason(row["reasons"][index]) or "Not available"
            self.error = f"{row['labels'][index]}: {reason}"
            return
        if row["selected"] == index:
            return
        if row["key"] == "mode":
            self.launch_mode = index
            if self._resume_mode():
                self._reset_resume()
        elif row["key"] == "runtime":
            self.runtime = index
            self._reset_resume()
            self.session = ""
            self._normalize_permission_choice()
            self._normalize_mode_choice()
            self._normalize_continuity_choice()
        elif row["key"] == "permissions":
            self.permissions = index
            self._normalize_mode_choice()
        elif row["key"] == "continuity":
            self.continuity = index
            self.continuity_explicit = True

    def handle_home_key(self, key: int) -> None:
        if key in (ord("n"), ord("N")):
            self.open_launcher()
        elif key in (ord("o"), ord("O")):
            self._toggle_other_sessions()
        elif key in (10, 13, curses.KEY_ENTER):
            if self.show_other and self.other_sessions:
                self._attach_session(self.other_sessions[0])
            else:
                self.open_launcher()

    def handle_launcher_key(self, key: int) -> None:
        if self.error_details:
            if key in (27, ord("e")):
                self.error_details = False
            elif key in (curses.KEY_UP, curses.KEY_PPAGE):
                self.error_scroll = max(0, self.error_scroll - 1)
            elif key in (curses.KEY_DOWN, curses.KEY_NPAGE):
                self.error_scroll += 1
            return
        if key == ord("e") and self.error:
            self.error_details = True
            self.error_scroll = 0
            return
        self.error = ""
        if key == 27:
            if self.standalone_launcher:
                raise SystemExit(0)
            self.mode = "home"
            return
        editing_control = self.edit_controls and self.row in (7, 8, 10)
        editing_parent = self.advanced and self.row == 6
        editing_path = self.row == 1
        if not editing_path and not editing_parent and not editing_control:
            if key == ord("+"):
                self.add_agent()
                return
            if key == ord("-"):
                self.remove_agent()
                return
            if key in (ord("["), ord("]")):
                self.select_slot(
                    (self.active_slot + (-1 if key == ord("[") else 1))
                    % len(self.agent_slots)
                )
                return
            if key in (ord("c"), ord("C")):
                self.edit_controls = not self.edit_controls
                self.advanced = False
                self.row = 7 if self.edit_controls else 0
                return
        if (
            self.active_slot < len(self.launch_results)
            and self.launch_results[self.active_slot]["status"] == "opened"
            and key not in (10, 13, curses.KEY_ENTER)
        ):
            self.error = (
                "This agent tab is already open; add a new agent to launch another"
            )
            return
        if (
            key in (ord("a"), ord("A"))
            and not editing_path
            and not editing_parent
            and not editing_control
        ):
            self.advanced = not self.advanced
            self.edit_controls = False
            if not self.advanced:
                self.row = min(self.row, 1)
            return
        rows = (
            list(range(7))
            if self.advanced
            else [0, 1, 7, 8, 9, 10]
            if self.edit_controls
            else [0, 1]
        )
        if self.row not in rows:
            self.row = rows[0]
        if key == curses.KEY_UP:
            self.row = rows[(rows.index(self.row) - 1) % len(rows)]
            return
        if key in (curses.KEY_DOWN, ord("\t")):
            self.row = rows[(rows.index(self.row) + 1) % len(rows)]
            return
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT) or (
            key == ord(" ") and not editing_path and not editing_parent
        ):
            delta = -1 if key == curses.KEY_LEFT else 1
            if self.row == 9 and self.edit_controls:
                self.purpose = (self.purpose + delta) % len(PURPOSES)
            elif self.row == 0:
                self._cycle_agent(delta)
            elif self.advanced and self.row == 2:
                self._cycle_mode(delta)
            elif self.advanced and self.row == 3:
                self._cycle_runtime(delta)
            elif self.advanced and self.row == 4:
                self._cycle_permissions(delta)
            elif self.advanced and self.row == 5:
                self._cycle_continuity(delta)
            elif self.advanced and self.row == 6:
                if self._resume_mode():
                    self._cycle_resume(delta)
                else:
                    self._cycle_parent(delta)
            return
        if key in (10, 13, curses.KEY_ENTER):
            self.launch()
            return
        editing_parent = self.advanced and self.row == 6
        editing_path = self.row == 1
        if editing_control:
            name = {7: "model", 8: "effort", 10: "session"}[self.row]
            if name == "effort" and AGENTS[self.agent] not in EFFORT_OVERRIDE_STYLES:
                self.error = "Effort is unavailable for this provider"
                return
            if key in (curses.KEY_BACKSPACE, 127, 8):
                setattr(self, name, getattr(self, name)[:-1])
            elif 32 <= key <= 126:
                setattr(self, name, getattr(self, name) + chr(key))
            return
        if editing_parent and self._resume_mode():
            # An exact provider session typed by hand; the recorded run (and
            # its environment) is resolved again at Launch.
            if key in (curses.KEY_BACKSPACE, 127, 8):
                self.session = self.session[:-1]
            elif 32 <= key <= 126:
                self.session += chr(key)
            self.resume_run_id = ""
            self.resume_index = -1
            return
        if editing_path or editing_parent:
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if editing_parent:
                    self.continuity_parent = self.continuity_parent[:-1]
                    self.parent_index = -1
                else:
                    self.path = self.path[:-1]
                    self.parent_sessions = []
                    self.parent_index = -1
            elif 32 <= key <= 126:
                if editing_parent:
                    self.continuity_parent += chr(key)
                    self.parent_index = -1
                else:
                    self.path += chr(key)
                    self.parent_sessions = []
                    self.parent_index = -1
            if editing_path and self.path != self.choices_root:
                self.choices_root = self.path
                self.active_slot = 0
                self.agent_slots = [{"agent": self.agent}]
                self.launch_results = []
                self.session = ""
                self.continuity_parent = ""
                self._load_choices(restore_slots=True)

    def _cycle_agent(self, delta: int) -> None:
        index = self.agent
        for _ in AGENTS:
            index = (index + delta) % len(AGENTS)
            if _provider_available(AGENTS[index]):
                self._select_agent(index)
                return
        self.error = "No provider is available"

    def _select_agent(self, index: int) -> None:
        """Switch provider by key or click; parent sessions belong to the old one."""
        if (
            self.active_slot < len(self.launch_results)
            and self.launch_results[self.active_slot]["status"] == "opened"
        ):
            self.error = (
                "This agent tab is already open; add a new agent to launch another"
            )
            return
        try:
            self.save_choices()
        except (OSError, ValueError) as exc:
            self.error = f"Cannot remember choices: {exc}"
            return
        self.agent = index
        self.session = ""
        self.continuity_parent = ""
        self._reset_resume()
        self._load_choices()
        self._normalize_runtime_choice()
        self._normalize_permission_choice()
        self._normalize_mode_choice()
        self._normalize_continuity_choice()
        self.parent_sessions = []
        self.parent_index = -1

    def _cycle_mode(self, delta: int) -> None:
        capabilities = mode_capabilities(
            AGENTS[self.agent],
            RUNTIME_POLICIES[self.runtime],
            PERMISSION_POLICIES[self.permissions],
        )
        for _ in LAUNCH_MODES:
            self.launch_mode = (self.launch_mode + delta) % len(LAUNCH_MODES)
            if capabilities[LAUNCH_MODES[self.launch_mode]]["available"]:
                if self._resume_mode():
                    self._reset_resume()
                return
        self.error = "No start mode is available for this provider"

    def _refresh_parent_sessions(self) -> None:
        self.parent_sessions, self.parent_error = parent_session_choices(
            AGENTS[self.agent], self.path
        )
        self.parent_index = -1

    def _cycle_parent(self, delta: int) -> None:
        if not self.parent_sessions:
            self._refresh_parent_sessions()
        if not self.parent_sessions:
            self.error = self.parent_error
            return
        self.parent_index = (
            self.parent_index + delta
            if self.parent_index >= 0
            else (0 if delta > 0 else len(self.parent_sessions) - 1)
        ) % len(self.parent_sessions)
        self.continuity_parent = self.parent_sessions[self.parent_index].session_id
        if not self.continuity_explicit:
            self._normalize_continuity_choice()

    def _cycle_runtime(self, delta: int) -> None:
        capabilities = runtime_policy_capabilities(AGENTS[self.agent])
        for _ in RUNTIME_POLICIES:
            self.runtime = (self.runtime + delta) % len(RUNTIME_POLICIES)
            name = RUNTIME_POLICIES[self.runtime]
            if capabilities[name]["available"]:
                self._reset_resume()
                self.session = ""
                self._normalize_permission_choice()
                self._normalize_mode_choice()
                self._normalize_continuity_choice()
                return
        self.error = "No runtime is available for this provider"

    def _cycle_permissions(self, delta: int) -> None:
        provider = AGENTS[self.agent]
        runtime = RUNTIME_POLICIES[self.runtime]
        for _ in PERMISSION_POLICIES:
            self.permissions = (self.permissions + delta) % len(PERMISSION_POLICIES)
            if resolve_provider_policy(
                provider, runtime, PERMISSION_POLICIES[self.permissions], "interactive"
            ).supported:
                self._normalize_mode_choice()
                return
        self.error = "No permission policy is available for this provider"

    def _normalize_runtime_choice(self) -> None:
        capabilities = runtime_policy_capabilities(AGENTS[self.agent])
        current = RUNTIME_POLICIES[self.runtime]
        if capabilities[current]["available"]:
            return
        for index, runtime in enumerate(RUNTIME_POLICIES):
            if capabilities[runtime]["available"]:
                self.runtime = index
                return

    def _normalize_permission_choice(self) -> None:
        provider = AGENTS[self.agent]
        runtime = RUNTIME_POLICIES[self.runtime]
        current = PERMISSION_POLICIES[self.permissions]
        if resolve_provider_policy(provider, runtime, current, "interactive").supported:
            return
        for index, permissions in enumerate(PERMISSION_POLICIES):
            if resolve_provider_policy(
                provider, runtime, permissions, "interactive"
            ).supported:
                self.permissions = index
                return

    def _normalize_mode_choice(self) -> None:
        capabilities = mode_capabilities(
            AGENTS[self.agent],
            RUNTIME_POLICIES[self.runtime],
            PERMISSION_POLICIES[self.permissions],
        )
        if capabilities[LAUNCH_MODES[self.launch_mode]]["available"]:
            return
        for index, mode in enumerate(LAUNCH_MODES):
            if capabilities[mode]["available"]:
                self.launch_mode = index
                return

    def _cycle_continuity(self, delta: int) -> None:
        if self._resume_mode():
            self.error = (
                "Resume continues the chosen session; memory applies to new sessions"
            )
            return
        capabilities = self._continuity_caps()
        for _ in CONTINUITY_MODES:
            self.continuity = (self.continuity + delta) % len(CONTINUITY_MODES)
            if capabilities[CONTINUITY_MODES[self.continuity]]["available"]:
                self.continuity_explicit = True
                return
        self.error = "No memory option is available"

    def _normalize_continuity_choice(self) -> None:
        if self.continuity_explicit:
            # The User's memory choice stands; Launch reports what it needs.
            return
        capabilities = self._continuity_caps()
        if capabilities["full-lineage"]["available"]:
            self.continuity = CONTINUITY_MODES.index("full-lineage")
        else:
            self.continuity = CONTINUITY_MODES.index("fresh")

    def handle_mouse(self) -> None:
        try:
            _, x, y, _, state = curses.getmouse()
        except curses.error:
            return
        # Hover/drag, release and secondary buttons are never an activation.
        # One physical click is one press; a CLICKED that ncurses still
        # synthesizes for the same press is that click, not a second one.
        if state & getattr(curses, "REPORT_MOUSE_POSITION", 0):
            return
        if state & curses.BUTTON1_RELEASED:
            self._mouse_down = None
            return
        if state & curses.BUTTON1_PRESSED:
            self._mouse_down = (x, y)
        elif state & curses.BUTTON1_CLICKED:
            if self._mouse_down == (x, y):
                self._mouse_down = None
                return
        else:
            return
        try:
            size = tuple(self.window.getmaxyx())
        except (AttributeError, curses.error):
            size = None
        if (
            size is not None
            and self._drawn_size is not None
            and size != self._drawn_size
        ):
            # The terminal resized after the last paint: hit-test the layout
            # the User sees now, not the stale one.
            self.draw()
        self.presence_schedule.request()
        for row, start, end, index, kind in self.mouse_targets:
            if y != row or not (start <= x < end):
                continue
            if kind == "home":
                self.home_choice = index
                self.open_launcher()
                return
            if kind == "face":
                self._focus_face(index)
                return
            if kind == "slot":
                self.select_slot(index)
                return
            if kind == "add":
                self.add_agent()
                return
            if kind == "controls":
                self.edit_controls = not self.edit_controls
                self.advanced = False
                self.row = 7 if self.edit_controls else 0
                return
            if kind == "field":
                self.row = index
                return
            if kind == "provider":
                self.row = 0
                if _provider_available(AGENTS[index]):
                    self._select_agent(index)
                else:
                    self.error = "That provider is not available"
                return
            if kind == "choice":
                self.error = ""
                offset, value = index
                self._choose_advanced(offset, value)
                return
            if kind == "parent":
                self.error = ""
                self.row = 6
                if self._resume_mode():
                    self._cycle_resume(1)
                else:
                    self._cycle_parent(1)
                return
            if kind == "launch":
                self.launch()
                return
            if kind == "advanced":
                self.advanced = not self.advanced
                self.edit_controls = False
                if not self.advanced:
                    self.row = min(self.row, 1)
                return

    def open_launcher(self) -> None:
        """Inline compose view in this pane. Never spawn a nested floating form."""
        self.mode = "launcher"
        self.row = 0
        self.advanced = False
        self.error = ""

    def _focus_face(self, index: int) -> None:
        if index < 0 or index >= len(self.face_records):
            return
        tab = self.face_records[index].tab
        if not tab:
            self.error = "That Agent has no tab name to open"
            return
        try:
            result = subprocess.run(
                ["vc-frame", "action", "go-to-tab-name", tab],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            self.error = "vc-frame is not available in this Runtime Pack"
            return
        if result.returncode != 0:
            self.error = (
                result.stderr or result.stdout or f"cannot open tab {tab}"
            ).strip()

    def _toggle_other_sessions(self) -> None:
        self.show_other = not self.show_other
        if not self.show_other:
            return
        self.other_sessions, self.other_error = list_other_sessions()

    def _attach_session(self, name: str) -> None:
        try:
            result = subprocess.run(
                ["vc-frame", "attach", name],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            self.error = "vc-frame is not available in this Runtime Pack"
            return
        if result.returncode != 0:
            self.error = (
                result.stderr or result.stdout or f"cannot open session {name}"
            ).strip()

    def launch(self) -> None:
        try:
            workspace = normalized_workspace(self.path)
            if self.launch_results and any(
                r["status"] == "opened" and r["workspace"] != str(workspace)
                for r in self.launch_results
            ):
                raise ValueError(
                    "Tabs are already open in the previous project; reopen the launcher for a new batch"
                )
            self.save_choices()
        except (ValueError, OSError) as exc:
            self.error = str(exc)
            return
        active = self.active_slot
        selections = [dict(s) for s in self.agent_slots]
        while len(self.launch_results) < len(selections):
            self.launch_results.append({"status": "pending", "reason": ""})
        if self.launch_results and all(
            r["status"] == "opened" for r in self.launch_results
        ):
            self.notice = "All selected agent tabs are already open"
        else:
            self.notice = ""
        for index, values in enumerate(selections):
            result = self.launch_results[index]
            # Opening a tab is the duplicate boundary, even when showing that
            # session afterwards fails. It is not a provider-health receipt.
            if result["status"] == "opened":
                continue
            self._restore(values)
            self.error = ""
            self._opened = False
            self._launch_one()
            self.launch_results[index] = {
                "status": "opened" if self._opened else "failed",
                "reason": self.error,
                "provider": AGENTS[self.agent],
                "workspace": str(workspace),
            }
        self._restore(selections[active])
        self.error = next((r["reason"] for r in self.launch_results if r["reason"]), "")
        if len(selections) > 1 or self.error:
            self.mode = "launcher"

    def _resolve_resume_target(self, runtime_name: str) -> tuple[str, str]:
        """``(run_id, session)`` of the exact conversation to re-open, or refuse."""
        provider = AGENTS[self.agent]
        supported, reason = interactive_resume_support(provider, runtime_name)
        if not supported:
            raise ValueError(reason)
        if self.resume_run_id:
            return self.resume_run_id, self.session
        if self.resume_candidates is None:
            self._refresh_resume_candidates()
        typed = self.session.strip()
        if typed:
            for candidate in self.resume_candidates or []:
                if candidate["session_id"] == typed:
                    return candidate["run_id"], typed
            if runtime_name != "local-native":
                label = RUNTIME_LABELS.get(runtime_name, runtime_name)
                raise ValueError(
                    f"Resume needs a session recorded in {label}: {typed} is not one; "
                    "choose a session in Session (←/→)"
                )
            return "", typed
        raise ValueError(
            "Resume needs a concrete session: "
            + (self.resume_error or "choose one in Session (←/→)")
        )

    def _launch_one(self) -> None:
        try:
            workspace = normalized_workspace(self.path)
            runtime_name = RUNTIME_POLICIES[self.runtime]
            capability = runtime_policy_capabilities(AGENTS[self.agent])[runtime_name]
            if not capability["available"]:
                raise ValueError(str(capability["reason"]))
            continuity_name = CONTINUITY_MODES[self.continuity]
            resuming = bool(self.session) or self._resume_mode()
            run_id = ""
            if resuming:
                run_id, session = self._resolve_resume_target(runtime_name)
            else:
                session = ""
                continuity_capability = self._continuity_caps(workspace)[
                    continuity_name
                ]
                if not continuity_capability["available"]:
                    raise ValueError(str(continuity_capability["reason"]))
            argv = launch_argv(
                AGENTS[self.agent],
                "resume" if resuming else LAUNCH_MODES[self.launch_mode],
                runtime_name,
                PERMISSION_POLICIES[self.permissions],
                continuity=continuity_name,
                continuity_parent=self.continuity_parent,
                workspace=workspace,
                model=self.model,
                effort=self.effort,
                purpose=self.purpose if self.launch_mode == 0 else None,
                session=session,
                run_id=run_id,
            )
        except ValueError as exc:
            self.error = public_reason(str(exc)) or str(exc)
            return
        executable = shutil.which(argv[0])
        if executable is None:
            self.error = "vibecrafted launcher is missing from PATH"
            return
        environment = {
            "local-worktrees": " · worktree",
            "local-vm": " · container",
        }.get(runtime_name, "")
        title = (
            f"{AGENTS[self.agent]} · "
            f"{LAUNCH_MODES[self.launch_mode]} · {workspace.name}{environment}"
        )
        try:
            destination = destination_session_for_workspace(workspace)
        except ValueError as exc:
            self.error = public_reason(str(exc)) or str(exc)
            return
        live, listing_error = list_live_frame_sessions()
        if listing_error:
            self.error = listing_error
            return
        if destination not in live:
            self.error = ""
            self.notice = "Opening project…"
            self.draw()
        try:
            admitted = ensure_live_destination(workspace, destination, live)
            destination = admitted.session
            pane = launch_pane_argv(title, workspace, argv, session=destination)
            frame_kwargs = {"env": admitted.environment} if admitted.environment else {}
            if admitted.environment:
                pane[0] = str(
                    Path(admitted.environment["VIBECRAFTED_RUNTIME_BIN"]) / "vc-frame"
                )
        except ValueError as exc:
            self.error = str(exc)
            return
        finally:
            self.notice = ""
        try:
            result = subprocess.run(
                pane, check=False, capture_output=True, text=True, **frame_kwargs
            )
        except FileNotFoundError:
            self.error = "vc-frame is not available in this Runtime Pack"
            return
        if result.returncode != 0:
            self.error = launch_failure_reason(
                result, "cannot open a tab for that Agent"
            )
            return
        self._opened = True
        current = current_frame_session()
        if current != destination:
            try:
                attached = subprocess.run(
                    [pane[0], *attach_session_argv(destination)[1:]],
                    check=False,
                    capture_output=True,
                    text=True,
                    **frame_kwargs,
                )
            except FileNotFoundError:
                self.error = "vc-frame is not available in this Runtime Pack"
                return
            if attached.returncode != 0:
                self.error = launch_failure_reason(
                    attached,
                    f"Agent opened in {destination}, but that session could not be shown",
                )
                return
        # Product entry remains a launcher when Start here focuses it again.
        self.mode = "launcher" if self.standalone_launcher else "home"
        self.presence_schedule.last_at = None
        self.presence_schedule.request()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vibecrafted Agent Workspaces")
    parser.add_argument("mode", choices=("home", "launcher"), nargs="?", default="home")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        curses.wrapper(lambda window: Workshop(window, mode=args.mode).run())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
