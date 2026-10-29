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
        if not in_tree and selected:
            if Path(selected).resolve() not in core_path.parents:
                raise ImportError("core belongs to a different Runtime Pack")
            # Keep the executable's lexical path: a generation venv may
            # symlink CPython outside its root while retaining its own identity.
            if (
                Path(os.path.abspath(selected))
                not in Path(os.path.abspath(sys.executable)).parents
            ):
                raise ImportError("Python belongs to a different Runtime Pack")
    except (ImportError, SyntaxError):
        pass
    else:
        return
    # A wrapper can exec the same underlying Python. Bound the retry rather
    # than comparing executable paths and looping on a broken generation.
    attempt = os.environ.get("VIBECRAFTED_PANE_PYTHON_ATTEMPT", "")
    if attempt != str(Path(__file__).resolve()):
        here = os.path.abspath(sys.executable)
        for wanted in _generation_python_candidates():
            if not os.access(wanted, os.X_OK) or os.path.abspath(wanted) == here:
                continue
            if (
                selected
                and not in_tree
                and Path(os.path.abspath(selected))
                not in Path(os.path.abspath(wanted)).parents
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
from vibecrafted_core.repo_selection import (
    RepoSelectionError,
    git_toplevel,
    validate_workspace_root,
)
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
    _parse_dt,
    continuity_policy_capabilities,
    interactive_resume_support,
    native_catalog_session_candidates,
    resolve_operator_agent_policy,
    resolve_provider_policy,
    resumable_interactive_runs,
    runtime_policy_capabilities,
)
from vibecrafted_core.workspace_catalog import (
    WORKSPACE_STATUS_ACTIVE,
    WorkspaceCatalogError,
    _runtime_workspace_identity,
    current_workspace_context,
    read_catalog,
    read_workspace_session,
    resolve_operator_place_session,
    resolve_project_destination,
    resolve_run_workspace_identity,
    runtime_session_owner_root,
)

AGENTS = ("agy", "claude", "codex", "cursor", "grok", "junie", "kimi", "copilot", "omp")
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
# Compared against title.casefold(): entries must be lowercase. The old
# "Voc" entry never matched for that reason, and the Voc tab itself left the
# layout (Voc is the compact-bar button beside Composer) — removed 2026-10-10.
_WORKSHOP_TITLES = frozenset(
    {"agent workspaces", "new agent", "launchpad", "start here"}
)
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
    """Resolve the hosting identity before falling back to its exported name."""
    environ = env if env is not None else os.environ
    current = ""
    for key in (
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ_SESSION_NAME",
        "VIBECRAFTED_FRAME_SESSION",
    ):
        value = str(environ.get(key) or "").strip()
        if value:
            current = value
            break
    if not current or current == "vc-host":
        return current
    try:
        session = read_workspace_session(environ.get("VIBECRAFTED_SESSION_ID", ""))
        if (
            session.workspace_id != environ.get("VIBECRAFTED_WORKSPACE_ID")
            or session.workspace_instance_id
            != environ.get("VIBECRAFTED_WORKSPACE_INSTANCE_ID")
            or not any(item.session_incarnation for item in session.attachments)
        ):
            return current
        root = read_catalog().workspaces[session.workspace_id].canonical_root
        socket_dir = environ.get("VC_FRAME_SOCKET_DIR") or environ.get(
            "ZELLIJ_SOCKET_DIR", ""
        )
        names, error = list_live_frame_sessions(
            env=selected_runtime_environment(environ)
        )
        if error:
            return current
        matching = []
        for name in names:
            if name == "vc-host":
                continue
            try:
                identity = resolve_run_workspace_identity(
                    root=root,
                    env=environ,
                    runtime_session_id=name,
                    socket_dir=socket_dir,
                    runtime_state="live",
                )
            except WorkspaceCatalogError:
                continue
            if (
                identity.workspace_id == session.workspace_id
                and identity.vibecrafted_session_id == session.session_id
                and identity.workspace_instance_id == session.workspace_instance_id
            ):
                matching.append(name)
        return matching[0] if len(matching) == 1 else ""
    except (OSError, KeyError, ValueError, WorkspaceCatalogError):
        return current


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
    if not current or current == "vc-host" or not session_id:
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
                and (
                    item.runtime_session_id == current
                    or item.session_incarnation is not None
                )
                and item.state == "live"
                for item in receipt.attachments
            )
            and runtime_session_owner_root(
                current,
                environ.get("VC_FRAME_SOCKET_DIR")
                or environ.get("ZELLIJ_SOCKET_DIR", ""),
                runtime_state="live",
            )
            == str(workspace)
        ):
            return current
    except (OSError, WorkspaceCatalogError):
        pass
    return ""


def catalog_owns_destination(workspace: Path, session: str) -> bool:
    """A fallback basename is not evidence that a live seat belongs to this root."""
    if session == "vc-host":
        return False
    if (
        current_frame_session() == session
        and os.environ.get("VIBECRAFTED_SESSION_ID")
        and owned_current_destination(workspace) != session
    ):
        return False
    try:
        socket_dir = os.environ.get("VC_FRAME_SOCKET_DIR") or os.environ.get(
            "ZELLIJ_SOCKET_DIR", ""
        )
        owner = runtime_session_owner_root(session, socket_dir, runtime_state="live")
        return owner is not None and Path(owner).resolve() == workspace
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
    workspace: Path,
    session: str,
    live_names: list[str],
    *,
    current_project_hint: str = "",
    layout: Path | None = None,
    workspace_only: bool = False,
) -> LiveDestination:
    """Prepare a missing project in the selected runtime, then admit its WES seat.

    The shell workspace owner creates and resurrects in the background. Names and command
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
    try:
        # A Git cwd is a useful path hint, never permission to adopt its Frame.
        # Fence the hinted current project before collision routing can choose
        # a second name, including when the current seat has a custom label.
        current = current_frame_session()
        socket_dir = os.environ.get("VC_FRAME_SOCKET_DIR") or os.environ.get(
            "ZELLIJ_SOCKET_DIR", ""
        )
        if (
            current
            and current != "vc-host"
            and current in live_names
            and str(workspace) == current_project_hint
            and runtime_session_owner_root(current, socket_dir, runtime_state="live")
            is None
        ):
            raise WorkspaceCatalogError(
                "The current Frame session has no verified project binding. "
                "Continue in the existing session on the Sessions rail; "
                f"vc-frame attach {current} opens that same session from a terminal. "
                "Automatic project preparation cannot adopt it or create a replacement."
            )
        route = resolve_project_destination(
            root=workspace,
            requested=session,
            live_names=live_names,
            occupied_names=live_names,
            socket_dir=os.environ.get("VC_FRAME_SOCKET_DIR")
            or os.environ.get("ZELLIJ_SOCKET_DIR", ""),
        )
        if route["VIBECRAFTED_FRAME_DESTINATION_REASON"] in {
            "owned-current-seat",
            "owned-live-seat",
        }:
            return LiveDestination(route["VIBECRAFTED_OPERATOR_SESSION"], None)
    except WorkspaceCatalogError as exc:
        raise ValueError(f"Project could not be opened: {exc}") from exc
    if session in live_names and catalog_owns_destination(workspace, session):
        return LiveDestination(session, None)
    try:
        env = selected_runtime_environment()
        root = env.get("VIBECRAFTED_RUNTIME_ROOT", "")
        if not root:
            raise ValueError("a selected Runtime Pack is required to open this project")
        runtime_bin = Path(env["VIBECRAFTED_RUNTIME_BIN"])
        frame = runtime_bin / "vc-frame"
        if not frame.is_file() or not os.access(frame, os.X_OK):
            raise ValueError("vc-frame is missing from the selected Runtime Pack")
        env["VIBECRAFTED_VC_FRAME_BIN"] = str(frame)
        env["VIBECRAFTED_WORKSPACE_ROOT"] = str(workspace)
        # The selected generation owns both the internal facade and its engine.
        env["PATH"] = os.pathsep.join((str(runtime_bin), env.get("PATH", "")))
        result = subprocess.run(
            workspace_shell_argv(
                env,
                workspace,
                session,
                prepare=True,
                layout=layout,
                workspace_only=workspace_only,
            ),
            cwd=str(workspace),
            env=env,
            stdin=subprocess.DEVNULL,
            check=False,
            capture_output=True,
            text=True,
            timeout=PROJECT_OPEN_TIMEOUT,
        )
        if result.returncode != 0:
            reason = launch_failure_reason(
                result, "workspace preparation refused the project"
            )
            raise ValueError(reason)
        # The selected shell owns observation and materialization. Its existing
        # CLI JSON projection carries the exact resulting pair back to Python.
        identity = json.loads(result.stdout)
        keys = (
            "VIBECRAFTED_WORKSPACE_ID",
            "VIBECRAFTED_SESSION_ID",
            "VIBECRAFTED_WORKSPACE_INSTANCE_ID",
            "VIBECRAFTED_BUILD_ID",
            "VIBECRAFTED_OPERATOR_SESSION",
            "VIBECRAFTED_WORKSPACE_ROOT",
        )
        if (
            not isinstance(identity, dict)
            or any(
                not isinstance(identity.get(key), str) or not identity[key]
                for key in keys
            )
            or identity["VIBECRAFTED_WORKSPACE_ROOT"] != str(workspace)
        ):
            raise ValueError("workspace preparation returned invalid identity")
        env.update({key: identity[key] for key in keys})
        for key in (
            "VIBECRAFTED_REQUESTED_FRAME_SESSION",
            "VIBECRAFTED_FRAME_DESTINATION_REASON",
        ):
            if isinstance(identity.get(key), str):
                env[key] = identity[key]
        deadline = time.monotonic() + PROJECT_LIVE_TIMEOUT
        while True:
            names, error = list_live_frame_sessions(env=env)
            if error:
                raise ValueError(error)
            try:
                receipt = read_workspace_session(identity["VIBECRAFTED_SESSION_ID"])
            except WorkspaceCatalogError:
                receipt = None
            if receipt is not None:
                if (
                    receipt.workspace_id != identity["VIBECRAFTED_WORKSPACE_ID"]
                    or receipt.workspace_instance_id
                    != identity["VIBECRAFTED_WORKSPACE_INSTANCE_ID"]
                    or receipt.session_id != identity["VIBECRAFTED_SESSION_ID"]
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
                    destination = destinations.pop()
                    env["VIBECRAFTED_OPERATOR_SESSION"] = destination
                    return LiveDestination(destination, env)
                if len(destinations) > 1:
                    raise ValueError(
                        "multiple live Frame destinations belong to this project"
                    )
            if time.monotonic() >= deadline:
                raise ValueError(
                    "workspace preparation returned without an owned live Frame destination"
                )
            time.sleep(0.1)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            "Project could not be opened: workspace preparation timed out"
        ) from exc
    except (OSError, ValueError, WorkspaceCatalogError) as exc:
        raise ValueError(f"Project could not be opened: {exc}") from exc


def workspace_shell_argv(
    environment: Mapping[str, str],
    workspace: Path,
    session: str,
    *,
    prepare: bool,
    layout: Path | None = None,
    workspace_only: bool = False,
) -> list[str]:
    """Address existing workspace owners with data argv, never a public GUI entry."""
    root = Path(environment["VIBECRAFTED_RUNTIME_ROOT"])
    facade = root / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
    if not facade.is_file() or any(
        path.is_symlink() for path in (facade, *facade.parents)
    ):
        raise ValueError(
            "physical shell facade is missing from the selected Runtime Pack"
        )
    script = 'source "$1" || exit $?; '
    if prepare:
        if workspace_only:
            script += (
                "if ! _vetcoders_vc_frame_developer_mode; then "
                '_vetcoders_product_runtime_admit "$VIBECRAFTED_RUNTIME_ROOT" || exit $?; fi; '
                "_vetcoders_pin_vc_frame_config_dir || exit $?; "
            )
        owner = (
            "_vetcoders_product_workspace_prepare"
            if workspace_only
            else "_vetcoders_product_entry_prepare"
        )
        script += (
            '_vetcoders_start_validate_workspace_name "$3" || exit $?; '
            '_vetcoders_prepare_project_destination "$2" "$3" || exit $?; '
            'target="$VIBECRAFTED_OPERATOR_SESSION"; '
            '_vetcoders_start_read_inventory_state "$target"; '
            'state="$_vetcoders_start_inventory_state"; '
            'if [[ "$state" == error ]]; then _vetcoders_start_refuse_inventory "$target"; exit $?; fi; '
            'if [[ "$state" == live || "$state" == dead ]]; then '
            '_vetcoders_start_check_existing_owner "$target" "$state" "$2" || exit $?; fi; '
            f'{owner} "$2" "$target" "$state" || exit $?; '
            '_vetcoders_start_launch_workspace "$target" "$2" background "${5:-}" >&2 || exit $?; '
            '_vetcoders_product_core_cli workspace resolve --root "$2" --json '
            '--runtime-session-id "$target" --socket-dir "$(_vetcoders_vc_frame_socket_dir)" --runtime-state live'
        )
    else:
        script += '_vetcoders_start_enter_workspace_session "$4" "$3"'
    command = [
        "/bin/bash",
        "--noprofile",
        "--norc",
        "-c",
        script,
        "workshop-workspace",
        str(facade),
        str(workspace),
        session,
        str(Path(environment["VIBECRAFTED_RUNTIME_BIN"]) / "vc-frame"),
    ]
    if layout is not None:
        command.append(str(layout))
    return command


def prepare_project_destination(
    root: Path, *, session: str = "", layout: Path | None = None
) -> dict[str, str]:
    """Noninteractive selected-runtime entry to the existing workspace owner.

    The layout is creation input only. Reusing a live seat does not replace its
    panes or workloads. The final packet comes from the incarnation-aware WES
    reader, never from the requested name or the launcher's progress output.
    """
    workspace = validate_workspace_root(root)
    session = session.strip()
    if session == "vc-host":
        raise ValueError("vc-host is the Operator host, not a project destination")
    if layout is not None and (
        not layout.is_absolute()
        or not layout.is_file()
        or any(path.is_symlink() for path in (layout, *layout.parents))
    ):
        raise ValueError("layout must be an existing absolute physical file")
    selected_root = str(os.environ.get("VIBECRAFTED_RUNTIME_ROOT") or "")
    if selected_root:
        runtime = Path(selected_root)
        if (
            selected_root != selected_root.strip()
            or not runtime.is_absolute()
            or any(path.is_symlink() for path in (runtime, *runtime.parents))
            or runtime != runtime.resolve()
        ):
            raise ValueError(
                "selected Runtime Pack root must be an absolute physical path"
            )
    env = selected_runtime_environment()
    if not env.get("VIBECRAFTED_RUNTIME_ROOT"):
        raise ValueError("a selected Runtime Pack is required to prepare a project")
    socket_dir = env.get("VC_FRAME_SOCKET_DIR") or env.get("ZELLIJ_SOCKET_DIR", "")
    if not socket_dir:
        raise ValueError("Frame must declare its socket namespace for preparation")
    namespace = Path(socket_dir)
    if not namespace.is_absolute():
        raise ValueError("Frame socket namespace must be an absolute path")
    names, error = list_live_frame_sessions(env=env)
    if error:
        raise ValueError(error)
    requested = session or destination_session_for_workspace(workspace, env=env)
    current_project_hint = ""
    inherited_root = str(env.get("VIBECRAFTED_WORKSPACE_ROOT") or "").strip()
    if inherited_root:
        try:
            current_project_hint = str(validate_workspace_root(inherited_root))
        except (OSError, RepoSelectionError):
            pass
    admitted = ensure_live_destination(
        workspace,
        requested,
        names,
        current_project_hint=current_project_hint,
        layout=layout,
        workspace_only=True,
    )
    env = admitted.environment or env
    names, error = list_live_frame_sessions(env=env)
    if error or admitted.session == "vc-host" or admitted.session not in names:
        raise ValueError(error or "prepared destination is not a live project seat")
    seat = _runtime_workspace_identity(
        root=workspace, runtime_session_id=admitted.session, socket_dir=socket_dir
    )
    if seat is None or not seat[2].session_incarnation:
        raise ValueError("prepared destination has no verified live Frame incarnation")
    receipt, instance, attachment = seat
    return {
        "native_session_name": admitted.session,
        "session_incarnation": attachment.session_incarnation,
        "socket_dir": attachment.socket_dir,
        "VIBECRAFTED_OPERATOR_SESSION": admitted.session,
        "VIBECRAFTED_WORKSPACE_ROOT": str(workspace),
        "VIBECRAFTED_WORKSPACE_ID": receipt.workspace_id,
        "VIBECRAFTED_SESSION_ID": receipt.session_id,
        "VIBECRAFTED_WORKSPACE_INSTANCE_ID": receipt.workspace_instance_id,
        "VIBECRAFTED_BUILD_ID": instance.build_id.rendered,
    }


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
    if "native session catalog" in low:
        return "Earlier sessions are unavailable right now"
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
        if result.returncode == 1 and (result.stderr or result.stdout).strip() in {
            "No active vc-frame sessions found.",
            "No active zellij sessions found.",
        }:
            return [], ""
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


command_bridge_onboarding = __import__(
    "vibecrafted_core.command_bridge_onboarding", fromlist=["*"]
)

PATH_PLACEHOLDER = "<select or type project path — Tab to complete>"


def _provider_interactive_decision(
    agent: str,
    runtime: str | None = None,
    permissions: str | None = None,
) -> Any:
    capabilities = runtime_policy_capabilities(agent)
    runtimes = (runtime,) if runtime else RUNTIME_POLICIES
    perms = (permissions,) if permissions else PERMISSION_POLICIES
    last_decision = None
    for rt in runtimes:
        cap = capabilities.get(rt, {})
        if not cap.get("available"):
            continue
        for perm in perms:
            decision = resolve_provider_policy(agent, rt, perm, "interactive")
            last_decision = decision
            if decision.supported:
                return decision
    if last_decision is not None:
        return last_decision
    return resolve_provider_policy(
        agent,
        runtime or RUNTIME_POLICIES[0],
        permissions or PERMISSION_POLICIES[0],
        "interactive",
    )


def _provider_available(agent: str) -> bool:
    capabilities = runtime_policy_capabilities(agent)
    if not any(
        bool(capabilities.get(name, {}).get("available")) for name in RUNTIME_POLICIES
    ):
        return False
    return bool(_provider_interactive_decision(agent).supported)


def _provider_unavailable_reason(
    agent: str,
    runtime: str | None = None,
    permissions: str | None = None,
) -> str:
    capabilities = runtime_policy_capabilities(agent)
    if not any(
        bool(capabilities.get(name, {}).get("available")) for name in RUNTIME_POLICIES
    ):
        first_reason = next(
            (
                str(capabilities.get(name, {}).get("reason") or "")
                for name in RUNTIME_POLICIES
                if capabilities.get(name, {}).get("reason")
            ),
            "",
        )
        return (
            public_reason(first_reason)
            or first_reason
            or "That provider is not available"
        )
    decision = _provider_interactive_decision(agent, runtime, permissions)
    reason = str(getattr(decision, "reason", "") or "").strip()
    if reason:
        return f"That provider is not available ({reason})"
    return "That provider is not available"


def _compute_path_suggestions(
    raw: str,
    *,
    cwd: Path | None = None,
    limit: int = 8,
) -> list[str]:
    """Bounded local directory-only path completion with zero subprocesses."""
    if limit <= 0:
        return []
    text = str(raw or "")
    base_cwd = Path(cwd) if cwd is not None else Path.cwd()
    tilde_prefix = False
    home_str = str(Path.home())

    if not text:
        search_dir = base_cwd
        prefix = ""
        display_base = str(base_cwd).rstrip("/") + "/"
    elif text == "~":
        search_dir = Path.home()
        prefix = ""
        display_base = "~/"
    elif text.startswith("~/"):
        tilde_prefix = True
        expanded = os.path.expanduser(text)
        if text.endswith("/"):
            search_dir = Path(expanded)
            prefix = ""
            display_base = text
        else:
            expanded_path = Path(expanded)
            search_dir = expanded_path.parent
            prefix = expanded_path.name
            parent_rel = text[: len(text) - len(prefix)]
            display_base = parent_rel if parent_rel.endswith("/") else parent_rel + "/"
    elif text.startswith("/"):
        if text.endswith("/"):
            search_dir = Path(text)
            prefix = ""
            display_base = text
        else:
            p = Path(text)
            search_dir = p.parent
            prefix = p.name
            parent_str = str(search_dir)
            display_base = "/" if parent_str == "/" else parent_str.rstrip("/") + "/"
    else:
        if text.endswith("/"):
            search_dir = (base_cwd / text).resolve()
            prefix = ""
            display_base = text
        elif "/" in text:
            rel_parent, _, prefix = text.rpartition("/")
            search_dir = (base_cwd / rel_parent).resolve()
            display_base = rel_parent + "/"
        else:
            search_dir = base_cwd
            prefix = text
            display_base = ""

    try:
        entries: list[str] = []
        with os.scandir(search_dir) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=True):
                        continue
                except OSError:
                    continue
                name = entry.name
                if name.startswith(".") and not prefix.startswith("."):
                    continue
                entries.append(name)
    except (PermissionError, OSError):
        return []

    entries.sort(key=lambda item: (item.lower(), item))
    exact = [name for name in entries if name.startswith(prefix)]
    lower_prefix = prefix.lower()
    ci = [
        name
        for name in entries
        if not name.startswith(prefix) and name.lower().startswith(lower_prefix)
    ]
    matched = (exact + ci)[:limit]
    results: list[str] = []
    for name in matched:
        candidate = f"{display_base}{name}/"
        if tilde_prefix and candidate.startswith(home_str + "/"):
            candidate = "~" + candidate[len(home_str) :]
        results.append(candidate)
    return results


class Workshop:
    def __init__(
        self,
        window: curses.window,
        *,
        mode: str = "home",
        onboarding: bool = False,
        font: bool = False,
        onboarding_policy_path: Path | None = None,
        onboarding_font_dirs: tuple[Path, ...] | list[Path] | None = None,
        onboarding_home: Path | None = None,
    ) -> None:
        self._initialized = False
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

        # Recover the physical seat's complete context before loading project
        # choices. A pane can lose its exports while its WES binding survives.
        # Neither its cwd nor its session label is ownership evidence.
        context_error = ""
        current = current_frame_session()
        if current and current != "vc-host":
            try:
                live, listing_error = list_live_frame_sessions()
                if not listing_error:
                    context = current_workspace_context(
                        runtime_session_id=current,
                        socket_dir=os.environ.get("VC_FRAME_SOCKET_DIR")
                        or os.environ.get("ZELLIJ_SOCKET_DIR", ""),
                        live_names=live,
                    )
                    os.environ.update(context)
            except (OSError, WorkspaceCatalogError) as exc:
                context_error = str(exc)
        declared_root = os.environ.get("VIBECRAFTED_WORKSPACE_ROOT", "").strip()
        resolved_default = ""
        if declared_root and current != "vc-host" and not context_error:
            try:
                candidate = validate_workspace_root(declared_root)
                if candidate.is_dir():
                    resolved_default = str(candidate)
            except (OSError, RepoSelectionError):
                resolved_default = ""
        # A real repository is a usable default even in an unbound legacy
        # pane. Launch admission remains separate and fails closed there.
        project_hint = (
            git_toplevel(Path.cwd())
            if mode == "launcher"
            and current != "vc-host"
            and not context_error
            and not resolved_default
            else ""
        )
        self.current_project_hint = resolved_default or project_hint
        self._path = resolved_default or project_hint
        self.path_explicit = bool(resolved_default)
        self.path_cursor = len(self._path)
        self.path_scroll = 0
        self.path_suggestions: list[str] = [self._path + "/"] if self._path else []
        self.path_suggestions_open = False
        self.path_suggestion_idx = 0
        self._utf8_buf = bytearray()

        self.error = context_error
        self.error_stage = "Restoring project context" if context_error else ""
        self.error_details = False
        self.error_scroll = 0
        self.notice = ""
        self.launch_stage = ""
        self.progress_stages: list[str] = []
        self.confirm_quit = False
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
        self.choices_root = self._path
        self.agent_slots: list[dict[str, Any]] = [{}]
        self.launch_results: list[dict[str, str]] = []

        # ZenEntry Onboarding & Monaspace Typography scene state
        self.onboarding_policy_path = onboarding_policy_path
        self.onboarding_font_dirs = (
            tuple(Path(p) for p in onboarding_font_dirs)
            if onboarding_font_dirs is not None
            else None
        )
        self.onboarding_home = onboarding_home
        self.onboarding_active = bool(
            onboarding
            or os.environ.get("VIBECRAFTED_WORKSHOP_ONBOARDING", "").strip() == "1"
        )
        self.font_only = font
        self.font_catalogue_note = ""
        self.onboarding_font_scroll = 0
        self.onboarding_step = 1
        self.onboarding_provider_idx = 0
        self.onboarding_font_idx = 0
        self.onboarding_variants: list[Any] = []
        self.configured_font_family = ""
        if font:
            self.open_onboarding(step=2)
        elif self.onboarding_active:
            self.open_onboarding(step=1)

        self._load_choices(restore_slots=True)
        self._initial_path = self._path
        self._initial_slot_snapshot = self._snapshot()
        self._initialized = True

    @property
    def path(self) -> str:
        return self._path

    @path.setter
    def path(self, value: str) -> None:
        new_val = str(value or "")
        changed = new_val != self._path
        self._path = new_val
        self.path_cursor = len(new_val)
        self.path_scroll = 0
        self.path_suggestions_open = False
        if changed and getattr(self, "_initialized", False):
            self._invalidate_stale_results()

    @property
    def workspace(self) -> str:
        return self._path

    @workspace.setter
    def workspace(self, value: str) -> None:
        self.path = value

    @property
    def agent_count(self) -> int:
        return len(self.agent_slots)

    @agent_count.setter
    def agent_count(self, count: int) -> None:
        target = max(1, min(3, int(count)))
        while len(self.agent_slots) < target:
            self.add_agent()
        while len(self.agent_slots) > target:
            self.remove_agent()

    @property
    def _click_zones(self) -> list[tuple[int, int, int, Any, str]]:
        return self.mouse_targets

    def _is_dirty(self) -> bool:
        if self._path != getattr(self, "_initial_path", ""):
            return True
        if len(self.agent_slots) > 1:
            return True
        return self._snapshot() != getattr(self, "_initial_slot_snapshot", {})

    def _slot_request_key(
        self,
        slot_values: Mapping[str, Any] | None = None,
        workspace_str: str | None = None,
    ) -> str:
        values = slot_values if slot_values is not None else self._snapshot()
        ws = str(workspace_str if workspace_str is not None else self._path).strip()
        try:
            ws_norm = str(Path(ws).expanduser().resolve()) if ws else ""
        except OSError:
            ws_norm = ws
        agent_idx = int(values.get("agent", self.agent))
        mode_idx = int(values.get("launch_mode", self.launch_mode))
        rt_idx = int(values.get("runtime", self.runtime))
        perm_idx = int(values.get("permissions", self.permissions))
        cont_idx = int(values.get("continuity", self.continuity))
        return "|".join(
            (
                AGENTS[agent_idx] if 0 <= agent_idx < len(AGENTS) else str(agent_idx),
                ws_norm,
                LAUNCH_MODES[mode_idx]
                if 0 <= mode_idx < len(LAUNCH_MODES)
                else str(mode_idx),
                RUNTIME_POLICIES[rt_idx]
                if 0 <= rt_idx < len(RUNTIME_POLICIES)
                else str(rt_idx),
                PERMISSION_POLICIES[perm_idx]
                if 0 <= perm_idx < len(PERMISSION_POLICIES)
                else str(perm_idx),
                CONTINUITY_MODES[cont_idx]
                if 0 <= cont_idx < len(CONTINUITY_MODES)
                else str(cont_idx),
                str(values.get("model", self.model)),
                str(values.get("effort", self.effort)),
                str(values.get("session", self.session)),
            )
        )

    def _invalidate_stale_results(self) -> None:
        if not self.launch_results:
            return
        if self.active_slot < len(self.agent_slots):
            self.agent_slots[self.active_slot] = self._snapshot()
        any_opened = any(r.get("status") == "opened" for r in self.launch_results)
        if not any_opened:
            for idx, res in enumerate(self.launch_results):
                if idx < len(self.agent_slots):
                    current_key = self._slot_request_key(
                        self.agent_slots[idx], self._path
                    )
                    if res.get("request_key") and res.get("request_key") != current_key:
                        self.launch_results = []
                        if not self.error_details:
                            self.error = ""
                            self.error_stage = ""
                        return
            return
        for idx, res in enumerate(self.launch_results):
            if res.get("status") == "opened":
                continue
            if idx < len(self.agent_slots):
                current_key = self._slot_request_key(self.agent_slots[idx], self._path)
                if res.get("request_key") and res.get("request_key") != current_key:
                    self.launch_results[idx] = {
                        "status": "pending",
                        "reason": "",
                        "provider": AGENTS[
                            self.agent_slots[idx].get("agent", self.agent)
                        ],
                        "workspace": self._path,
                        "request_key": current_key,
                    }

    # ─── ZenEntry Onboarding & Monaspace Typography Methods ──────────────────

    def _sync_onboarding_typography(self) -> None:
        self.configured_font_family = ""
        try:
            self.configured_font_family = (
                command_bridge_onboarding.read_configured_font_family(
                    self.onboarding_policy_path
                )
                or ""
            )
            variants = command_bridge_onboarding.inspect_font_variants(
                policy_path=self.onboarding_policy_path,
                font_dirs=self.onboarding_font_dirs,
            )
            self.font_catalogue_note = (
                "Installed fixed-width fonts · Monaspace NF first"
                if variants
                and variants[0].catalogue_source == "CoreText fixed-pitch traits"
                else "Native catalogue unavailable; font files cannot verify fixed width"
                if variants
                else "No fixed-width families available"
            )
        except (OSError, ValueError, command_bridge_onboarding.OnboardingError) as exc:
            variants = []
            self.font_catalogue_note = str(exc)
        self.onboarding_variants = variants
        self.onboarding_font_idx = next(
            (
                idx
                for idx, item in enumerate(variants)
                if item.active and item.selectable
            ),
            0,
        )
        self.onboarding_font_scroll = 0

    def _onboarding_font_viewport(self) -> tuple[int, int, int]:
        height, _ = self.window.getmaxyx()
        top = 1
        start = top + 5
        capacity = max(0, height - start - 5)
        count = len(self.onboarding_variants)
        self.onboarding_font_idx = max(
            0, min(self.onboarding_font_idx, max(0, count - 1))
        )
        scroll = min(self.onboarding_font_scroll, max(0, count - capacity))
        if capacity:
            scroll = min(scroll, self.onboarding_font_idx)
            scroll = max(scroll, self.onboarding_font_idx - capacity + 1)
        self.onboarding_font_scroll = max(0, scroll)
        return start, capacity, self.onboarding_font_scroll

    def open_onboarding(self, *, step: int = 1) -> None:
        self.onboarding_active = True
        self.onboarding_step = 1 if step not in (1, 2) else step
        self.error = ""
        self.notice = ""
        self._sync_onboarding_typography()
        if self.onboarding_step == 2:
            return
        try:
            st = command_bridge_onboarding.status(self.onboarding_home)
            if st.state == "pending":
                command_bridge_onboarding.begin(home=self.onboarding_home)
            configured = command_bridge_onboarding.configured_providers()
            if configured and configured[0] in command_bridge_onboarding.PROVIDERS:
                self.onboarding_provider_idx = (
                    command_bridge_onboarding.PROVIDERS.index(configured[0])
                )
        except (OSError, ValueError, command_bridge_onboarding.OnboardingError):
            pass

    def close_onboarding(self) -> None:
        if self.font_only:
            raise SystemExit(0)
        try:
            st = command_bridge_onboarding.status(self.onboarding_home)
            if st.state == "in_progress":
                command_bridge_onboarding.cancel(home=self.onboarding_home)
        except (OSError, ValueError, command_bridge_onboarding.OnboardingError):
            pass
        self.error = ""
        self.onboarding_active = False

    def _onboarding_confirm_provider(self) -> None:
        providers = command_bridge_onboarding.PROVIDERS
        if not providers:
            self.onboarding_step = 2
            return
        idx = max(0, min(self.onboarding_provider_idx, len(providers) - 1))
        chosen = providers[idx]
        try:
            probe = command_bridge_onboarding.probe_provider(chosen)
            if probe.configured:
                command_bridge_onboarding.complete(
                    chosen,
                    home=self.onboarding_home,
                )
                self.error = ""
                self.notice = f"Provider {chosen} verified ready."
                self.onboarding_step = 2
            else:
                self.error = f"{chosen}: no local auth signal found; configure auth or press Tab/s to skip to Typography"
        except (
            OSError,
            ValueError,
            command_bridge_onboarding.OnboardingError,
        ) as exc:
            self.error = str(exc)
            self.notice = ""

    def _onboarding_apply_font(self, index: int | None = None) -> None:
        if not self.onboarding_variants:
            self.error = self.font_catalogue_note or "No fixed-width families available"
            return
        if index is not None and 0 <= index < len(self.onboarding_variants):
            self.onboarding_font_idx = index
        chosen = self.onboarding_variants[self.onboarding_font_idx]
        if not chosen.selectable:
            self.error = (
                "Current policy is preserved. Choose an available fixed-width family."
            )
            return
        try:
            command_bridge_onboarding.select_font_family(
                chosen.family,
                policy_path=self.onboarding_policy_path,
                font_dirs=self.onboarding_font_dirs,
            )
        except (
            OSError,
            ValueError,
            command_bridge_onboarding.FontNotAvailableError,
            command_bridge_onboarding.OnboardingError,
        ) as exc:
            self.error = str(exc)
            self.notice = ""
            return
        # Refresh the active marker without jumping the list back to the top.
        # The catalogue can reorder while the policy is written, so keep the
        # selected family by identity, not by its previous numerical index.
        previous_scroll = self.onboarding_font_scroll
        self._sync_onboarding_typography()
        self.onboarding_font_idx = next(
            (
                idx
                for idx, variant in enumerate(self.onboarding_variants)
                if variant.family == chosen.family
            ),
            self.onboarding_font_idx,
        )
        self.onboarding_font_scroll = previous_scroll
        self._onboarding_font_viewport()
        self.configured_font_family = chosen.family
        self.error = ""
        self.notice = f"Saved terminal font {chosen.family}. Choose another or close."

    def draw_onboarding(self) -> None:
        height, width = self.window.getmaxyx()
        left = max(1, (width - min(width - 2, 84)) // 2)
        inner = max(20, width - left - 2)
        top = 1 if self.onboarding_step == 2 else max(1, (height - 18) // 2)

        header = (
            "Terminal font"
            if self.onboarding_step == 2
            else "ZenEntry Setup · Step 1 of 2"
        )
        _safe_addstr(self.window, top, left, header, curses.A_BOLD | _ACCENT)

        if self.onboarding_step == 1:
            _safe_addstr(
                self.window,
                top + 1,
                left,
                "Step 1: Provider Readiness — confirm a configured provider or press s to skip.",
                curses.A_DIM,
            )
            providers = command_bridge_onboarding.PROVIDERS
            row_y = top + 3
            for idx, name in enumerate(providers):
                try:
                    probe = command_bridge_onboarding.probe_provider(name)
                    configured = probe.configured
                    signals = (
                        ", ".join(probe.evidence)
                        if probe.evidence
                        else "no auth signal detected"
                    )
                except (OSError, ValueError, command_bridge_onboarding.OnboardingError):
                    configured = False
                    signals = "probe unavailable"
                selected = idx == self.onboarding_provider_idx
                _paint_row_focus(self.window, row_y, left, selected)
                badge = "ready" if configured else "unconfigured"
                line = f"{idx + 1}. {name:10} [{badge}]  {signals}"
                attr = (
                    curses.A_BOLD | _ACCENT
                    if selected and configured
                    else curses.A_BOLD
                    if selected
                    else 0
                    if configured
                    else curses.A_DIM
                )
                _safe_addstr(self.window, row_y, left, _clip(line, inner), attr)
                self.mouse_targets.append(
                    (
                        row_y,
                        left,
                        left + min(len(line), inner),
                        idx,
                        "onboarding_provider",
                    )
                )
                row_y += 1
            btn_row = row_y + 1
            confirm_txt = "[ Confirm & Next (Enter) ]"
            skip_txt = "[ Skip to Typography (s) ]"
            close_txt = "[ Close (Esc) ]"
            _safe_addstr(self.window, btn_row, left, confirm_txt, curses.A_BOLD)
            self.mouse_targets.append(
                (btn_row, left, left + len(confirm_txt), 0, "onboarding_confirm")
            )
            col2 = left + len(confirm_txt) + 2
            _safe_addstr(self.window, btn_row, col2, skip_txt, 0)
            self.mouse_targets.append(
                (btn_row, col2, col2 + len(skip_txt), 0, "onboarding_next")
            )
            col3 = col2 + len(skip_txt) + 2
            _safe_addstr(self.window, btn_row, col3, close_txt, curses.A_DIM)
            self.mouse_targets.append(
                (btn_row, col3, col3 + len(close_txt), 0, "onboarding_close")
            )
        else:
            _safe_addstr(
                self.window,
                top + 1,
                left,
                _clip(
                    f"Current: {self.configured_font_family or 'terminal default'}",
                    inner,
                ),
                curses.A_DIM,
            )
            preview_row = top + 2
            preview_line = (
                f"Glyph sample: {command_bridge_onboarding.FONT_GLYPH_PREVIEW}"
            )
            _safe_addstr(
                self.window,
                preview_row,
                left,
                _clip(preview_line, inner),
                curses.A_BOLD,
            )
            _safe_addstr(
                self.window,
                top + 3,
                left,
                _clip(self.font_catalogue_note, inner),
                curses.A_DIM,
            )
            row_y, capacity, scroll = self._onboarding_font_viewport()
            visible = self.onboarding_variants[scroll : scroll + capacity]
            for idx, variant in enumerate(visible, start=scroll):
                selected = idx == self.onboarding_font_idx
                _paint_row_focus(self.window, row_y, left, selected)
                line = f"{idx + 1}. {variant.label:28} · {variant.status_label} · {variant.description}"
                if not variant.selectable:
                    attr = curses.A_DIM
                elif selected:
                    attr = curses.A_BOLD | _ACCENT
                elif variant.active:
                    attr = curses.A_BOLD
                else:
                    attr = 0
                _safe_addstr(self.window, row_y, left, _clip(line, inner), attr)
                self.mouse_targets.append(
                    (row_y, left, left + min(len(line), inner), idx, "onboarding_font")
                )
                row_y += 1
            end = min(scroll + capacity, len(self.onboarding_variants))
            _safe_addstr(
                self.window,
                height - 5,
                left,
                f"{scroll + 1 if end else 0}-{end} / {len(self.onboarding_variants)}",
                curses.A_DIM,
            )
            btn_row = height - 4
            apply_txt = "[ Apply Font (Enter) ]"
            back_txt = "" if self.font_only else "[ Back (b) ]"
            close_txt = "[ Close (Esc) ]"
            _safe_addstr(self.window, btn_row, left, apply_txt, curses.A_BOLD)
            self.mouse_targets.append(
                (btn_row, left, left + len(apply_txt), 0, "onboarding_apply_font")
            )
            col2 = left + len(apply_txt) + 2
            _safe_addstr(self.window, btn_row, col2, back_txt, 0)
            self.mouse_targets.append(
                (btn_row, col2, col2 + len(back_txt), 0, "onboarding_back")
            )
            col3 = col2 + len(back_txt) + 2
            _safe_addstr(self.window, btn_row, col3, close_txt, curses.A_DIM)
            self.mouse_targets.append(
                (btn_row, col3, col3 + len(close_txt), 0, "onboarding_close")
            )

        hint = (
            "↑/↓ · PgUp/PgDn · Home/End · wheel · Enter apply · Esc close"
            if self.onboarding_step == 2
            else "1..9 select · ↑/↓ navigate · Enter confirm · s next · Esc close"
        )
        _safe_addstr(self.window, height - 2, left, _clip(hint, inner), curses.A_DIM)
        if self.error:
            _safe_addstr(
                self.window,
                height - 1,
                left,
                _clip(self.error, inner),
                curses.A_BOLD,
            )
        elif self.notice:
            _safe_addstr(
                self.window,
                height - 1,
                left,
                _clip(self.notice, inner),
                curses.A_BOLD,
            )

    def handle_onboarding_key(self, key: int) -> None:
        if key == 27:
            if self.font_only:
                self.close_onboarding()
                return
            if self.onboarding_step == 2:
                self.onboarding_step = 1
                self.error = ""
                return
            self.close_onboarding()
            return
        if self.onboarding_step == 1:
            count = len(command_bridge_onboarding.PROVIDERS)
            if key in (curses.KEY_UP, curses.KEY_LEFT):
                self.onboarding_provider_idx = (self.onboarding_provider_idx - 1) % max(
                    1, count
                )
            elif key in (curses.KEY_DOWN, curses.KEY_RIGHT):
                self.onboarding_provider_idx = (self.onboarding_provider_idx + 1) % max(
                    1, count
                )
            elif ord("1") <= key <= ord("9"):
                idx = key - ord("1")
                if idx < count:
                    self.onboarding_provider_idx = idx
            elif key in (ord("s"), ord("S"), ord("\t")):
                self.error = ""
                self.onboarding_step = 2
            elif key in (10, 13, curses.KEY_ENTER):
                self._onboarding_confirm_provider()
        else:
            count = len(self.onboarding_variants)
            if key in (curses.KEY_UP, curses.KEY_LEFT):
                self.onboarding_font_idx = (self.onboarding_font_idx - 1) % max(
                    1, count
                )
            elif key in (curses.KEY_DOWN, curses.KEY_RIGHT):
                self.onboarding_font_idx = (self.onboarding_font_idx + 1) % max(
                    1, count
                )
            elif key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
                _, capacity, _ = self._onboarding_font_viewport()
                delta = max(1, capacity) * (-1 if key == curses.KEY_PPAGE else 1)
                self.onboarding_font_idx = max(
                    0, min(count - 1, self.onboarding_font_idx + delta)
                )
            elif key in (curses.KEY_HOME, curses.KEY_END):
                self.onboarding_font_idx = (
                    0 if key == curses.KEY_HOME else max(0, count - 1)
                )
            elif ord("1") <= key <= ord("9"):
                idx = self.onboarding_font_scroll + key - ord("1")
                if idx < count:
                    self.onboarding_font_idx = idx
            elif key in (ord("b"), ord("B")) and not self.font_only:
                self.error = ""
                self.onboarding_step = 1
            elif key in (10, 13, curses.KEY_ENTER):
                self._onboarding_apply_font()

    def _choice_path(self) -> Path:
        # UI preferences belong to this launcher, not the workspace identity
        # catalog or server configuration. Each canonical root owns one file.
        raw_target = (
            str(Path(self._path).expanduser().resolve())
            if self._path.strip()
            else "__empty_workspace__"
        )
        digest = hashlib.sha256(raw_target.encode()).hexdigest()
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
        if not self._path.strip():
            return {"version": 1, "providers": {}}
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
        if self.active_slot < len(self.launch_results):
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
        # cbreak retains IXON: the terminal would consume the advertised Ctrl+Q
        # shortcut as XON before get_wch can receive it. Keep ISIG/Ctrl+C intact;
        # curses.endwin restores the saved shell terminal mode on exit.
        if os.name == "posix" and sys.stdin.isatty():
            import termios

            mode = termios.tcgetattr(sys.stdin.fileno())
            mode[0] &= ~termios.IXON
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, mode)
            curses.def_prog_mode()
        self.window.keypad(True)
        self.window.timeout(500)
        try:
            # Report raw presses: no click resolution delay, and a press is
            # the single activation of one physical click.
            curses.mouseinterval(0)
        except curses.error:
            pass
        if not _provider_available(AGENTS[self.agent]):
            for idx, name in enumerate(AGENTS):
                if _provider_available(name):
                    self.agent = idx
                    break
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
            wch_char: str | None = None
            if hasattr(self.window, "get_wch"):
                try:
                    ch_or_key = self.window.get_wch()
                    if isinstance(ch_or_key, str):
                        wch_char = ch_or_key
                        key = ord(ch_or_key) if ord(ch_or_key) < 128 else -2
                    else:
                        key = ch_or_key
                except curses.error:
                    key = -1
            else:
                key = self.window.getch()

            if key == -1:
                self._utf8_buf.clear()
                continue
            if key == curses.KEY_MOUSE:
                self._utf8_buf.clear()
                self.handle_mouse()
                continue
            self.presence_schedule.request()
            if key == curses.KEY_RESIZE:
                self._utf8_buf.clear()
                continue
            if self.mode == "home":
                self._utf8_buf.clear()
                self.handle_home_key(key)
            else:
                self.handle_launcher_key(key, wch_char=wch_char)

    def draw(self) -> None:
        self.window.erase()
        self.mouse_targets.clear()
        try:
            self._drawn_size = tuple(self.window.getmaxyx())
        except (AttributeError, curses.error):
            self._drawn_size = None
        if self.onboarding_active:
            self.draw_onboarding()
        elif self.mode == "home":
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
        workspace_name = Path(self._path).name or self._path or "(no project selected)"
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

    def _draw_project_row(
        self, path_row: int, left: int, inner: int, width: int
    ) -> None:
        focused = self.row == 1
        path_col = _paint_row_focus(self.window, path_row, left, focused)
        prefix = "Project  "
        field_cells = max(0, min(inner, width - path_col) - len(prefix))
        badge = " [editing]" if focused and inner >= 28 else ""
        widths = [_char_cells(char) for char in self._path]
        path_cells = sum(widths)
        # Do not let the decorative badge hide a path which fits the field.
        # At the end, reserve a cell for the insertion caret when possible.
        if (
            self._path
            and path_cells <= field_cells
            and (path_cells + 1 > field_cells - len(badge))
        ):
            badge = ""
        available_text_cells = max(0, field_cells - len(badge))
        cur = max(0, min(self.path_cursor, len(self._path)))
        if self._path:
            if path_cells <= available_text_cells:
                self.path_scroll = 0
            else:
                # Reclaim blank space on resize or after deletion: the start of
                # the last full page is the furthest useful scroll position.
                last_page = len(self._path)
                tail_cells = 0
                while (
                    last_page
                    and tail_cells + widths[last_page - 1] <= available_text_cells
                ):
                    last_page -= 1
                    tail_cells += widths[last_page]
                self.path_scroll = min(max(0, self.path_scroll), last_page)
                if focused:
                    self.path_scroll = min(self.path_scroll, cur)
                    caret_cells = max(1, widths[cur] if cur < len(widths) else 1)
                    before_caret = sum(widths[self.path_scroll : cur])
                    while (
                        self.path_scroll < cur
                        and before_caret + caret_cells > available_text_cells
                    ):
                        before_caret -= widths[self.path_scroll]
                        self.path_scroll += 1
            visible: list[str] = []
            used = 0
            for char, cells in zip(
                self._path[self.path_scroll :], widths[self.path_scroll :]
            ):
                if used + cells > available_text_cells:
                    break
                visible.append(char)
                used += cells
            visible_segment = "".join(visible)
            line = f"{prefix}{visible_segment}"
            _safe_addstr(self.window, path_row, path_col, _clip(line, inner), 0)
        else:
            self.path_scroll = 0
            line = f"{prefix}{PATH_PLACEHOLDER}"
            _safe_addstr(self.window, path_row, path_col, _clip(line, inner), 0)

        if focused:
            rel_cursor = _cell_width(self._path[self.path_scroll : cur])
            caret_col = path_col + len(prefix) + rel_cursor
            caret_char = (
                self._path[cur] if self._path and cur < len(self._path) else " "
            )
            # If the row reaches the terminal edge, keep the entire fitting
            # path visible and mark its final glyph for the end position.
            if caret_col >= width and cur == len(self._path) and self._path:
                caret_char = self._path[-1]
                caret_col = max(path_col, width - max(1, _char_cells(caret_char)))
            if available_text_cells and caret_col < width:
                _safe_addstr(
                    self.window,
                    path_row,
                    caret_col,
                    caret_char,
                    curses.A_REVERSE | curses.A_BOLD,
                )
            if badge:
                badge_col = min(width - len(badge), path_col + inner - len(badge))
                if badge_col > path_col + len(prefix):
                    _safe_addstr(
                        self.window,
                        path_row,
                        badge_col,
                        badge,
                        curses.A_BOLD | _ACCENT,
                    )
        self.mouse_targets.append(
            (path_row, left, min(width, left + inner), 1, "project")
        )

    def draw_launcher(self) -> None:
        if self.onboarding_active:
            self.draw_onboarding()
            return
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
        if not compact and inner >= 36:
            setup_chip = "[ Setup / Font (f) ]"
            chip_col = left + inner - len(setup_chip)
            if chip_col > left + 12:
                _safe_addstr(self.window, top, chip_col, setup_chip, curses.A_DIM)
                self.mouse_targets.append(
                    (top, chip_col, chip_col + len(setup_chip), 0, "onboarding_open")
                )
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
        remove_label = "[- agent]"
        remove_attr = curses.A_BOLD if len(self.agent_slots) > 1 else curses.A_DIM
        if slot_col + len(remove_label) < left + inner:
            _safe_addstr(self.window, top + 2, slot_col, remove_label, remove_attr)
            self.mouse_targets.append(
                (top + 2, slot_col, slot_col + len(remove_label), 0, "remove")
            )
            slot_col += len(remove_label) + 1
        add_label = "[+ agent]"
        add_attr = curses.A_BOLD if len(self.agent_slots) < 3 else curses.A_DIM
        if slot_col + len(add_label) <= left + inner:
            _safe_addstr(self.window, top + 2, slot_col, add_label, add_attr)
            self.mouse_targets.append(
                (top + 2, slot_col, slot_col + len(add_label), 0, "add")
            )
            slot_col += len(add_label) + 1
        count_badge = f"Agents: {len(self.agent_slots)}/3"
        if slot_col + len(count_badge) <= left + inner:
            _safe_addstr(self.window, top + 2, slot_col, count_badge, curses.A_DIM)

        self._draw_providers(top + 3, left, inner)
        path_row = top + 5
        self._draw_project_row(path_row, left, inner, width)
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
        row_after_launch = cursor + 1
        if self.path_suggestions_open and self.path_suggestions:
            for s_idx, suggestion in enumerate(self.path_suggestions[:4]):
                s_row = row_after_launch + s_idx
                if s_row >= height - 2:
                    break
                s_selected = s_idx == self.path_suggestion_idx
                s_prefix = "▸ " if s_selected else "  "
                s_text = _clip(f"{s_prefix}{suggestion}", inner)
                s_attr = curses.A_BOLD | _ACCENT if s_selected else curses.A_DIM
                _safe_addstr(self.window, s_row, left, s_text, s_attr)
                self.mouse_targets.append(
                    (s_row, left, left + len(s_text), s_idx, "path_suggestion")
                )
            row_after_launch += min(4, len(self.path_suggestions))
        for offset, result in enumerate(self.launch_results):
            if result.get("status") == "pending":
                continue
            status = "tab created" if result["status"] == "opened" else result["status"]
            label = f"{offset + 1} {result.get('provider', '')}: {status}"
            if result["reason"]:
                reason = public_reason(result["reason"]) or result["reason"]
                label += f" · {reason.splitlines()[0]}"
            _safe_addstr(
                self.window,
                row_after_launch + offset,
                left,
                _clip(label, inner),
                curses.A_BOLD,
            )
        hint = (
            "Enter launch  Esc back"
            if compact
            else "←/→ provider · +/- agent · [/] slot · c choices · a advanced · f font · Enter launch · Ctrl+Q quit"
        )
        if self.error:
            hint = "r retry · e error details · Esc dismiss · " + hint
        _safe_addstr(self.window, height - 2, left, hint, curses.A_DIM)
        if self.error:
            summary_raw = (public_reason(self.error) or self.error).splitlines()[0]
            if self.error_stage:
                summary_raw = f"[{self.error_stage}] {summary_raw}"
            err_row = max(0, height - 3) if height >= 6 else max(0, height - 2)
            _safe_addstr(
                self.window,
                err_row,
                left,
                _clip(summary_raw, inner),
                curses.A_BOLD,
            )
            act_row = height - 1
            layout = _choice_layout(
                ("[ Retry (r) ]", "[ Back (Esc) ]", "[ Details (e) ]"),
                left,
                min(width, left + inner),
            )
            action_kinds = ("retry", "dismiss_error", "error_details")
            for idx, start, end, fragment in layout:
                if fragment:
                    _safe_addstr(self.window, act_row, start, fragment)
                    self.mouse_targets.append(
                        (act_row, start, end, 0, action_kinds[idx])
                    )
        elif self.notice:
            _safe_addstr(self.window, height - 1, left, self.notice, curses.A_BOLD)

    def draw_launch_error(self) -> None:
        height, width = self.window.getmaxyx()
        header = (
            f"Launch error · {self.error_stage}" if self.error_stage else "Launch error"
        )
        lines = [
            wrapped
            for line in self.error.splitlines()
            for wrapped in (textwrap.wrap(line, max(1, width - 3)) or [""])
        ]
        available = max(1, height - 3)
        self.error_scroll = min(self.error_scroll, max(0, len(lines) - available))
        _safe_addstr(self.window, 0, 1, "Launch error", curses.A_BOLD)
        if self.error_stage and width > 24:
            _safe_addstr(self.window, 0, 15, f"({self.error_stage})", curses.A_DIM)
        _ = header
        for row, line in enumerate(
            lines[self.error_scroll : self.error_scroll + available], start=1
        ):
            _safe_addstr(self.window, row, 1, line)
        _safe_addstr(
            self.window,
            height - 1,
            1,
            "↑/↓ scroll · r retry · Esc back to launcher",
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
        effective_root = root or self._path or Path.cwd()
        caps = continuity_policy_capabilities(
            AGENTS[self.agent],
            root=effective_root,
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
            self.session
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
        catalog_error = ""
        try:
            self.resume_candidates = resumable_interactive_runs(
                provider, self.path, runtime_name
            )
            if runtime_name == "local-native":
                recorded = {item["session_id"]: item for item in self.resume_candidates}
                try:
                    native = native_catalog_session_candidates(provider, self.path)
                except (OSError, ValueError) as exc:
                    catalog_error = f"native session catalog unavailable: {exc}"
                    native = []
                for item in native:
                    if item["session_id"] in recorded:
                        # Preserve a proven run's route while using native activity
                        # for recency; catalog existence does not prove a run.
                        recorded[item["session_id"]] = {
                            **recorded[item["session_id"]],
                            "updated_at": item["updated_at"],
                        }
                    else:
                        recorded[item["session_id"]] = item
                self.resume_candidates = sorted(
                    recorded.values(),
                    key=lambda item: (
                        _parse_dt(item.get("updated_at"))
                        or _parse_dt("1970-01-01T00:00:00Z")
                    ),
                    reverse=True,
                )
        except (OSError, ValueError) as exc:
            self.resume_candidates = []
            self.resume_error = f"session catalog unavailable: {exc}"
            return
        self.resume_index = -1
        label = RUNTIME_LABELS.get(runtime_name, runtime_name)
        self.resume_error = catalog_error or (
            ""
            if self.resume_candidates
            else f"no earlier {provider} session in {label} for {Path(self.path).name}"
        )
        if catalog_error:
            self.error = public_reason(catalog_error) or catalog_error

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

    def _on_path_mutated(self) -> None:
        self.parent_sessions = []
        self.parent_index = -1
        self.path_suggestions_open = False
        if self._path != self.choices_root:
            self.choices_root = self._path
            self.active_slot = 0
            self.agent_slots = [{"agent": self.agent}]
            self.launch_results = []
            self.session = ""
            self.continuity_parent = ""
            self._load_choices(restore_slots=True)
        else:
            self._invalidate_stale_results()

    def _insert_path_text(self, text: str) -> None:
        cleaned = "".join(ch for ch in str(text or "") if ch >= " " and ch != "\x7f")
        if not cleaned:
            return
        cur = max(0, min(self.path_cursor, len(self._path)))
        self._path = self._path[:cur] + cleaned + self._path[cur:]
        self.path_cursor = cur + len(cleaned)
        self._on_path_mutated()

    def paste_path_text(self, text: str) -> None:
        """Insert a burst/pasted path string at the current cursor without firing hotkeys."""
        self.row = 1
        self._insert_path_text(text)

    def _backspace_path(self) -> None:
        cur = max(0, min(self.path_cursor, len(self._path)))
        if cur <= 0:
            return
        self._path = self._path[: cur - 1] + self._path[cur:]
        self.path_cursor = cur - 1
        self._on_path_mutated()

    def _delete_path(self) -> None:
        cur = max(0, min(self.path_cursor, len(self._path)))
        if cur >= len(self._path):
            return
        self._path = self._path[:cur] + self._path[cur + 1 :]
        self.path_cursor = cur
        self._on_path_mutated()

    def _trigger_path_autocomplete(self) -> None:
        suggestions = _compute_path_suggestions(self._path)
        self.path_suggestions = suggestions
        if not suggestions:
            self.path_suggestions_open = False
            self.path_suggestion_idx = 0
            return
        if self.path_suggestions_open:
            self.path_suggestion_idx = (self.path_suggestion_idx + 1) % len(suggestions)
        else:
            self.path_suggestions_open = True
            self.path_suggestion_idx = 0
            if len(suggestions) == 1:
                self._accept_path_suggestion(0)

    def _accept_path_suggestion(self, index: int) -> None:
        if not self.path_suggestions or not (0 <= index < len(self.path_suggestions)):
            self.path_suggestions_open = False
            return
        chosen = self.path_suggestions[index]
        self._path = chosen
        self.path_cursor = len(chosen)
        self.path_scroll = 0
        self._on_path_mutated()
        self.path_suggestions_open = False

    def handle_home_key(self, key: int) -> None:
        if self.onboarding_active:
            self.handle_onboarding_key(key)
            return
        if key == 17:
            raise SystemExit(0)
        if key in (ord("f"), ord("F")):
            self.open_onboarding(step=2)
            return
        if key in (ord("n"), ord("N")):
            self.open_launcher()
        elif key in (ord("o"), ord("O")):
            self._toggle_other_sessions()
        elif key in (10, 13, curses.KEY_ENTER):
            if self.show_other and self.other_sessions:
                self._attach_session(self.other_sessions[0])
            else:
                self.open_launcher()

    def _decode_key_char(self, key: int) -> str | None:
        if 32 <= key <= 126:
            self._utf8_buf.clear()
            return chr(key)
        if 194 <= key <= 244:
            self._utf8_buf = bytearray([key])
            return None
        if 128 <= key <= 191:
            if not self._utf8_buf:
                return None
            self._utf8_buf.append(key)
            try:
                ch = self._utf8_buf.decode("utf-8")
                self._utf8_buf.clear()
                return ch
            except UnicodeDecodeError:
                if len(self._utf8_buf) >= 4:
                    self._utf8_buf.clear()
                return None
        self._utf8_buf.clear()
        return None

    def handle_launcher_key(self, key: int, wch_char: str | None = None) -> None:
        if wch_char is not None or not (128 <= key <= 191) and not (194 <= key <= 244):
            self._utf8_buf.clear()

        if self.onboarding_active:
            self.handle_onboarding_key(key)
            return
        if self.confirm_quit:
            if key in (17, ord("y"), ord("Y"), 10, 13, curses.KEY_ENTER):
                raise SystemExit(0)
            self.confirm_quit = False
            self.notice = ""
            return
        if key == 17:
            if self._is_dirty():
                self.confirm_quit = True
                self.notice = (
                    "Unsaved form changes — press Ctrl+Q or y to close, Esc to stay."
                )
                return
            raise SystemExit(0)
        if self.error_details:
            if key in (27, ord("e")):
                self.error_details = False
            elif key in (ord("r"), ord("R")):
                self.error_details = False
                self._attempt_launch()
            elif key in (curses.KEY_UP, curses.KEY_PPAGE):
                self.error_scroll = max(0, self.error_scroll - 1)
            elif key in (curses.KEY_DOWN, curses.KEY_NPAGE):
                self.error_scroll += 1
            return

        editing_control = self.edit_controls and self.row in (7, 8, 10)
        editing_parent = self.advanced and self.row == 6
        editing_path = self.row == 1
        actively_typing = editing_path or editing_parent or editing_control

        target_char: str | None = None
        if actively_typing:
            if wch_char is not None and wch_char.isprintable():
                target_char = wch_char
            elif 128 <= key <= 244:
                target_char = self._decode_key_char(key)
                if target_char is None:
                    return

            if target_char is not None:
                if editing_path:
                    self._insert_path_text(target_char)
                    return
                if editing_control:
                    name = {7: "model", 8: "effort", 10: "session"}[self.row]
                    if (
                        name == "effort"
                        and AGENTS[self.agent] not in EFFORT_OVERRIDE_STYLES
                    ):
                        self.error = "Effort is unavailable for this provider"
                        return
                    setattr(self, name, getattr(self, name) + target_char)
                    self._invalidate_stale_results()
                    return
                if editing_parent and self._resume_mode():
                    self.session += target_char
                    self.resume_run_id = ""
                    self.resume_index = -1
                    self._invalidate_stale_results()
                    return
                if editing_parent:
                    self.continuity_parent += target_char
                    self.parent_index = -1
                    self._invalidate_stale_results()
                    return
        else:
            self._utf8_buf.clear()

        if not actively_typing and key == ord("e") and self.error:
            self.error_details = True
            self.error_scroll = 0
            return
        if not actively_typing and key in (ord("r"), ord("R")) and self.error:
            self.error = ""
            self.error_stage = ""
            self._attempt_launch()
            return

        if key == 27:
            if self.path_suggestions_open:
                self.path_suggestions_open = False
                return
            if self.error:
                self.error = ""
                self.error_stage = ""
                return
            if self.edit_controls:
                self.edit_controls = False
                self.row = min(self.row, 1)
                return
            if self.advanced:
                self.advanced = False
                self.row = min(self.row, 1)
                return
            if self.standalone_launcher:
                self.notice = "Press Ctrl+Q to close Workshop."
                return
            self.mode = "home"
            return

        self.error = ""
        self.error_stage = ""
        self.notice = ""

        if editing_path:
            if key == ord("\t"):
                self._trigger_path_autocomplete()
                return
            if self.path_suggestions_open and self.path_suggestions:
                if key == curses.KEY_DOWN:
                    self.path_suggestion_idx = (self.path_suggestion_idx + 1) % len(
                        self.path_suggestions
                    )
                    return
                if key == curses.KEY_UP:
                    self.path_suggestion_idx = (self.path_suggestion_idx - 1) % len(
                        self.path_suggestions
                    )
                    return
                if key in (10, 13, curses.KEY_ENTER):
                    self._accept_path_suggestion(self.path_suggestion_idx)
                    return
            if key == curses.KEY_LEFT:
                self.path_cursor = max(0, min(self.path_cursor, len(self._path)) - 1)
                self.path_suggestions_open = False
                return
            if key == curses.KEY_RIGHT:
                self.path_cursor = min(len(self._path), max(0, self.path_cursor) + 1)
                self.path_suggestions_open = False
                return
            if key in (curses.KEY_HOME, 1):
                self.path_cursor = 0
                self.path_suggestions_open = False
                return
            if key in (curses.KEY_END, 5):
                self.path_cursor = len(self._path)
                self.path_suggestions_open = False
                return
            if key in (curses.KEY_BACKSPACE, 127, 8):
                self._backspace_path()
                return
            if key == curses.KEY_DC:
                self._delete_path()
                return
            if 32 <= key <= 126:
                self._insert_path_text(chr(key))
                return

        if not actively_typing:
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
            if key in (ord("1"), ord("2"), ord("3")):
                slot_idx = key - ord("1")
                if 0 <= slot_idx < len(self.agent_slots):
                    self.select_slot(slot_idx)
                    return
            if key in (ord("c"), ord("C")):
                self.edit_controls = not self.edit_controls
                self.advanced = False
                self.row = 7 if self.edit_controls else 0
                return
            if key in (ord("f"), ord("F")):
                self.open_onboarding(step=2)
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
        if key in (ord("a"), ord("A")) and not actively_typing:
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
                self._invalidate_stale_results()
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
            self._attempt_launch()
            return
        if editing_control:
            name = {7: "model", 8: "effort", 10: "session"}[self.row]
            if name == "effort" and AGENTS[self.agent] not in EFFORT_OVERRIDE_STYLES:
                self.error = "Effort is unavailable for this provider"
                return
            if key in (curses.KEY_BACKSPACE, 127, 8):
                setattr(self, name, getattr(self, name)[:-1])
                self._invalidate_stale_results()
            elif 32 <= key <= 126:
                setattr(self, name, getattr(self, name) + chr(key))
                self._invalidate_stale_results()
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
            self._invalidate_stale_results()
            return
        if editing_parent:
            if key in (curses.KEY_BACKSPACE, 127, 8):
                self.continuity_parent = self.continuity_parent[:-1]
                self.parent_index = -1
                self._invalidate_stale_results()
            elif 32 <= key <= 126:
                self.continuity_parent += chr(key)
                self.parent_index = -1
                self._invalidate_stale_results()

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
        self._invalidate_stale_results()

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
                self._invalidate_stale_results()
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
        self._invalidate_stale_results()

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
                self._invalidate_stale_results()
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
                self._invalidate_stale_results()
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
                self._invalidate_stale_results()
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
        self._utf8_buf.clear()
        try:
            _, x, y, _, state = curses.getmouse()
        except curses.error:
            return
        if self.onboarding_active and self.onboarding_step == 2:
            if state & getattr(curses, "BUTTON4_PRESSED", 0):
                self.handle_onboarding_key(curses.KEY_UP)
                return
            if state & getattr(curses, "BUTTON5_PRESSED", 0):
                self.handle_onboarding_key(curses.KEY_DOWN)
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
            if kind == "onboarding_open":
                self.open_onboarding(step=2)
                return
            if kind == "onboarding_provider":
                self.onboarding_provider_idx = int(index)
                return
            if kind == "onboarding_confirm":
                self._onboarding_confirm_provider()
                return
            if kind == "onboarding_next":
                self.error = ""
                self.onboarding_step = 2
                return
            if kind == "onboarding_font":
                self.onboarding_font_idx = int(index)
                return
            if kind == "onboarding_apply_font":
                self._onboarding_apply_font()
                return
            if kind == "onboarding_back" and not self.font_only:
                self.error = ""
                self.onboarding_step = 1
                return
            if kind == "onboarding_close":
                self.close_onboarding()
                return
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
            if kind == "remove":
                self.remove_agent()
                return
            if kind == "project":
                self.row = 1
                return
            if kind == "path_suggestion":
                self._accept_path_suggestion(int(index))
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
                    self.error = _provider_unavailable_reason(
                        AGENTS[index],
                        RUNTIME_POLICIES[self.runtime],
                        PERMISSION_POLICIES[self.permissions],
                    )
                return
            if kind == "choice":
                self.error = ""
                offset, value = index
                self._choose_advanced(offset, value)
                self._invalidate_stale_results()
                return
            if kind == "parent":
                self.error = ""
                self.row = 6
                if self._resume_mode():
                    self._cycle_resume(1)
                else:
                    self._cycle_parent(1)
                self._invalidate_stale_results()
                return
            if kind == "launch":
                self._attempt_launch()
                return
            if kind == "retry":
                self.error = ""
                self.error_stage = ""
                self._attempt_launch()
                return
            if kind == "dismiss_error":
                self.error = ""
                self.error_stage = ""
                return
            if kind == "error_details":
                if self.error:
                    self.error_details = True
                    self.error_scroll = 0
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
        self.error_stage = ""

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

    def _attempt_launch(self) -> None:
        """Single launch entrypoint shared by keyboard Enter and mouse [ Launch ]."""
        self.path_suggestions_open = False
        self.launch()

    def launch(self) -> None:
        self.progress_stages = ["Validating configuration…"]
        self.launch_stage = "Validating configuration…"
        self.error_stage = ""
        try:
            if not self._path.strip():
                self.error_stage = "Validating configuration"
                raise ValueError(
                    "Project path is required — select or type an existing project directory"
                )
            workspace = normalized_workspace(self.path)
            if self.launch_results and any(
                r["status"] == "opened" and r["workspace"] != str(workspace)
                for r in self.launch_results
            ):
                self.error_stage = "Validating configuration"
                raise ValueError(
                    "Tabs are already open in the previous project; reopen the launcher for a new batch"
                )
            self.save_choices()
        except (ValueError, OSError) as exc:
            if not self.error_stage:
                self.error_stage = "Validating configuration"
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
            current_req_key = self._slot_request_key(values, str(workspace))
            # Opening a tab is the duplicate boundary, even when showing that
            # session afterwards fails. It is not a provider-health receipt.
            if result["status"] == "opened":
                continue
            self._restore(values)
            self.error = ""
            self._opened = False
            self._launch_one()
            selections[index] = self._snapshot()
            self.agent_slots[index] = dict(selections[index])
            self.launch_results[index] = {
                "status": "opened" if self._opened else "failed",
                "reason": self.error,
                "provider": AGENTS[self.agent],
                "workspace": str(workspace),
                "request_key": current_req_key,
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
            if typed in {"current", "last"} and runtime_name == "local-native":
                from vibecrafted_core.workflow import resolve_session_selection

                selection = resolve_session_selection(
                    provider, typed, normalized_workspace(self.path)
                )
                typed = selection["agent_session_id"]
                self.session = typed  # pin before passing the target into Frame
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
        provider_name = AGENTS[self.agent]
        try:
            workspace = normalized_workspace(self.path)
            runtime_name = RUNTIME_POLICIES[self.runtime]
            perm_name = PERMISSION_POLICIES[self.permissions]
            capability = runtime_policy_capabilities(provider_name)[runtime_name]
            if not capability["available"]:
                raise ValueError(str(capability["reason"]))
            interactive_decision = resolve_provider_policy(
                provider_name, runtime_name, perm_name, "interactive"
            )
            if not interactive_decision.supported:
                raise ValueError(
                    str(
                        interactive_decision.reason
                        or f"{provider_name} does not support interactive Workshop launch"
                    )
                )
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
                provider_name,
                "resume" if resuming else LAUNCH_MODES[self.launch_mode],
                runtime_name,
                perm_name,
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
            self.error_stage = "Validating configuration"
            self.error = public_reason(str(exc)) or str(exc)
            return
        executable = shutil.which(argv[0])
        if executable is None:
            self.error_stage = "Validating configuration"
            self.error = "vibecrafted launcher is missing from PATH"
            return
        environment = {
            "local-worktrees": " · worktree",
            "local-vm": " · container",
        }.get(runtime_name, "")
        title = (
            f"{provider_name} · "
            f"{LAUNCH_MODES[self.launch_mode]} · {workspace.name}{environment}"
        )
        try:
            destination = destination_session_for_workspace(workspace)
        except ValueError as exc:
            self.error_stage = "Opening project workspace"
            reason = f"Project could not be opened: {exc}"
            self.error = public_reason(reason) or reason
            return
        live, listing_error = list_live_frame_sessions()
        if listing_error:
            self.error_stage = "Opening project workspace"
            self.error = listing_error
            return
        opened_new_workspace = destination not in live or not catalog_owns_destination(
            workspace, destination
        )
        if opened_new_workspace:
            self.launch_stage = "Opening project workspace…"
            self.progress_stages.append("Opening project workspace…")
            self.error = ""
            self.notice = "Opening project…"
            self.draw()
        try:
            admitted = ensure_live_destination(
                workspace,
                destination,
                live,
                current_project_hint=self.current_project_hint,
            )
            destination = admitted.session
            pane = launch_pane_argv(title, workspace, argv, session=destination)
            frame_kwargs = {"env": admitted.environment} if admitted.environment else {}
            if admitted.environment:
                pane[0] = str(
                    Path(admitted.environment["VIBECRAFTED_RUNTIME_BIN"]) / "vc-frame"
                )
        except ValueError as exc:
            self.error_stage = "Opening project workspace"
            self.error = str(exc)
            return
        finally:
            self.notice = ""
        tab_stage = f"Opening {provider_name} tab…"
        self.launch_stage = tab_stage
        self.progress_stages.append(tab_stage)
        try:
            result = subprocess.run(
                pane, check=False, capture_output=True, text=True, **frame_kwargs
            )
        except FileNotFoundError:
            self.error_stage = f"Opening {provider_name} tab"
            self.error = "vc-frame is not available in this Runtime Pack"
            return
        if result.returncode != 0:
            self.error_stage = f"Opening {provider_name} tab"
            base_reason = launch_failure_reason(
                result, "cannot open a tab for that Agent"
            )
            if opened_new_workspace:
                self.error = (
                    f"Project workspace opened in {destination}, "
                    f"but opening {provider_name} tab failed: {base_reason}"
                )
            else:
                self.error = base_reason
            return
        self._opened = True
        current = current_frame_session()
        if current != destination:
            try:
                switch = (
                    workspace_shell_argv(
                        admitted.environment, workspace, destination, prepare=False
                    )
                    if admitted.environment
                    else [pane[0], *attach_session_argv(destination)[1:]]
                )
                attached = subprocess.run(
                    switch,
                    check=False,
                    capture_output=True,
                    text=True,
                    **frame_kwargs,
                    **(
                        {"cwd": str(workspace), "timeout": PROJECT_OPEN_TIMEOUT}
                        if admitted.environment
                        else {}
                    ),
                )
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                self.error_stage = "Showing destination workspace"
                self.error = f"Agent opened in {destination}, but that session could not be shown: {exc}"
                return
            if attached.returncode != 0:
                self.error_stage = "Showing destination workspace"
                self.error = launch_failure_reason(
                    attached,
                    f"Agent opened in {destination}, but that session could not be shown",
                )
                return
        # Product entry remains a launcher when Launchpad focuses it again.
        self.mode = "launcher" if self.standalone_launcher else "home"
        self.presence_schedule.last_at = None
        self.presence_schedule.request()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vibecrafted Agent Workspaces")
    parser.add_argument(
        "mode", choices=("home", "launcher", "prepare"), nargs="?", default="home"
    )
    parser.add_argument("--root", type=Path, help="Declared project root for prepare")
    parser.add_argument("--session", default="", help="Requested native project seat")
    parser.add_argument(
        "--layout", type=Path, help="Physical selected layout for creation"
    )
    parser.add_argument(
        "--onboarding",
        action="store_true",
        help="Open ZenEntry setup and terminal typography selector",
    )
    parser.add_argument(
        "--font", action="store_true", help="Open the terminal font chooser directly"
    )
    args = parser.parse_args(argv)
    if args.mode == "prepare":
        if args.root is None or args.font or args.onboarding:
            parser.error("prepare requires --root and cannot open onboarding or fonts")
    elif args.root is not None or args.session or args.layout is not None:
        parser.error("--root, --session and --layout require prepare")
    if args.font and (args.mode != "launcher" or args.onboarding):
        parser.error(
            "--font requires launcher and cannot be combined with --onboarding"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "prepare":
        try:
            payload = prepare_project_destination(
                args.root, session=args.session, layout=args.layout
            )
        except (OSError, ValueError, RepoSelectionError, WorkspaceCatalogError) as exc:
            print(f"vc-agent-workshop: {exc}", file=sys.stderr)
            return 4
        print(json.dumps(payload))
        return 0
    try:
        curses.wrapper(
            lambda window: Workshop(
                window,
                mode=args.mode,
                onboarding=bool(getattr(args, "onboarding", False)),
                font=args.font,
            ).run()
        )
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
