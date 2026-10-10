#!/usr/bin/env python3
"""Actionable first screen for the shipped Vibecrafted workspace.

This pane is a view and launcher only. vc-frame owns navigation, the native app
owns VC Console, and the existing Vibecrafted deck owns diagnostics and server
truth.

Theme ownership: this pane paints nothing of its own. The host terminal palette
(``vc-theme`` publishes ``terminal-theme.toml``; the tab-bar switcher calls it)
owns background and foreground, so every cell here is drawn with the terminal
default colour pair. Emphasis uses bold/reverse only — never dim, never a
hardcoded colour — so light and moon modes stay readable without a restart.
"""

from __future__ import annotations

import curses
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, NamedTuple


def _generation_python_candidates() -> list[str]:
    """Interpreters that can import vibecrafted_core without host PYTHONPATH."""
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
        "vc-start-here: incompatible Python or Runtime Pack. "
        "Repair: set VIBECRAFTED_PYTHON to the selected Runtime Pack's bin/python3 "
        "and reopen this project through vc-start resume --repo <project-path>. "
        "Existing sessions can remain open."
    )


PRODUCT_LINE = (
    "Vibecrafted is a workspace where you start and coordinate AI Agents "
    "that do real work with runtime continuity and visible proof."
)

ACTIONS = (
    ("Open project", "Choose a folder and enter or return to its workspace", "project"),
    ("Agents", "Start or return to an Agent in this workspace", "agents"),
    ("Shell", "Open the installed work shell", "shell"),
    ("VC Console", "Open native run status and reports", "console"),
    ("Help & diagnostics", "Check this installed runtime and its owner", "help"),
    ("Recent projects", "Return to a workspace, conversation or work shell", "recent"),
)

HELP_LINE = "↑↓ / j k select   Enter open   click open   r refresh   q close"
HELP_LINE_SHORT = "Enter open   r refresh   q close"

# Deliberate reading width: long prose wraps here instead of running to the
# pane edge or being clipped behind an ellipsis.
READABLE_WIDTH = 76
MIN_CANVAS = 20
DETAIL_INDENT = 6


def project_chooser_argv() -> list[str]:
    """Return the host folder picker used by Open project."""
    if sys.platform == "darwin" and os.access("/usr/bin/osascript", os.X_OK):
        return [
            "/usr/bin/osascript",
            "-e",
            'POSIX path of (choose folder with prompt "Open a Vibecrafted project")',
        ]
    zenity = shutil.which("zenity")
    if zenity:
        return [
            zenity,
            "--file-selection",
            "--directory",
            "--title=Open a Vibecrafted project",
        ]
    return [
        "bash",
        "-lc",
        'printf "Project folder path: "; read -r -e path; printf "%s" "$path"',
    ]


def action_argv(action: str, *, project_path: str | None = None) -> list[str]:
    """Return the existing product owner command for a Launchpad action."""
    if action == "project":
        if project_path:
            return ["vc-start", "resume", "--repo", project_path]
        return project_chooser_argv()
    if action == "agents":
        session = next(
            (
                os.environ[key].strip()
                for key in (
                    "VC_FRAME_SESSION_NAME",
                    "ZELLIJ_SESSION_NAME",
                    "VIBECRAFTED_FRAME_SESSION",
                )
                if os.environ.get(key, "").strip()
            ),
            "",
        )
        if os.environ.get("VIBECRAFTED_WORKSPACE_ROOT") and not session:
            raise ValueError(
                "Workspace session is unknown — reopen this project through vc-start"
            )
        target = ["--session", session] if session else []
        return ["vc-frame", *target, "action", "go-to-tab-name", "Agents"]
    if action == "shell":
        return ["vc-frame", "action", "go-to-tab-name", "Shell"]
    if action == "console":
        return ["/usr/bin/open", "vibecrafted://console/open"]
    if action == "help":
        return [
            "vc-frame",
            "action",
            "new-pane",
            "--floating",
            "--name",
            "Vibecrafted Help & diagnostics",
            "--width",
            "72%",
            "--height",
            "70%",
            "--",
            "bash",
            "-lc",
            "vibecrafted doctor; printf '\\nPress Enter to close diagnostics…'; read -r _",
        ]
    raise ValueError(f"unknown Launchpad action: {action}")


class ReturnAction(NamedTuple):
    key: str
    title: str
    detail: str
    commands: tuple[tuple[str, ...], ...] = ()
    recovery: bool = False


def _workshop_module():
    """Use the admitted Workshop command owners; no second resume recipe."""
    name = "vc_return_workshop"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("vc-agent-workshop.py")
        )
        if spec is None or spec.loader is None:
            raise ValueError("Agent Workshop is unavailable in this Runtime Pack")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def recent_project_actions() -> list[ReturnAction]:
    from vibecrafted_core.workspace_catalog import list_workspaces

    records = sorted(list_workspaces(), key=lambda r: r.updated_at, reverse=True)
    return [
        ReturnAction(
            "project:" + record.workspace_id,
            record.display_label,
            f"{record.canonical_root} · last catalog update {record.updated_at[:16]} · "
            + (
                "Last saved context: " + record.notes[:240]
                if record.notes.strip()
                else "No reliable last context."
            ),
        )
        for record in records
    ]


def _return_environment(socket_dir: str) -> dict[str, str]:
    from vibecrafted_core.runtime_paths import selected_runtime_environment

    env = selected_runtime_environment()
    if not env.get("VIBECRAFTED_RUNTIME_BIN"):
        frame = shutil.which("vc-frame", path=env.get("PATH"))
        if not frame:
            raise ValueError(
                "vc-frame is unavailable — repair the selected Runtime Pack"
            )
        env["VIBECRAFTED_RUNTIME_BIN"] = str(Path(frame).parent)
    # A lobby's socket is not the selected project's socket.
    for key in ("VC_FRAME_SOCKET_DIR", "ZELLIJ_SOCKET_DIR"):
        if socket_dir:
            env[key] = socket_dir
        else:
            env.pop(key, None)
    return env


def return_project_actions(workspace_id: str) -> tuple[list[ReturnAction], str]:
    from vibecrafted_core.spawn import (
        interactive_resume_support,
        resumable_interactive_runs,
    )
    from vibecrafted_core.workspace_catalog import (
        show_workspace,
        workspace_return_attachment,
    )

    record = show_workspace(workspace_id)
    root = Path(record.canonical_root)
    if not root.is_dir():
        raise ValueError("Project folder is missing — choose Open project to locate it")
    seat = workspace_return_attachment(workspace_id)
    if seat is None:
        return [
            ReturnAction(
                "recover-workspace",
                "Reopen workspace",
                "No live workspace attachment. Reopen through Open project; conversation history stays intact.",
                (("vc-start", "resume", "--repo", str(root)),),
                True,
            )
        ], ""
    workshop = _workshop_module()
    env = _return_environment(seat.socket_dir)
    names, error = workshop.list_live_frame_sessions(env=env)
    if error:
        raise ValueError(error + " — refresh to retry; existing work is preserved")
    if seat.runtime_session_id not in names:
        return [
            ReturnAction(
                "recover-workspace",
                "Reopen workspace",
                "Workspace terminal is closed or missing. Reopen explicitly; no Agent is started.",
                (("vc-start", "resume", "--repo", str(root)),),
                True,
            )
        ], seat.socket_dir
    prefix = ("vc-frame", "--session", seat.runtime_session_id, "action")
    result = subprocess.run(
        [*prefix, "list-panes", "--json", "--state", "--tab", "--command"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=2.0,
    )
    if result.returncode != 0:
        raise ValueError("Workspace terminals are unavailable — refresh to retry")
    payload = json.loads(result.stdout)
    if not isinstance(payload, list) and not (
        isinstance(payload, dict)
        and any(isinstance(payload.get(k), list) for k in ("panes", "items", "data"))
    ):
        raise ValueError(
            "Workspace terminal inventory is unavailable — refresh to retry"
        )
    current = workshop.current_frame_session()
    source_socket = os.environ.get("VC_FRAME_SOCKET_DIR") or os.environ.get(
        "ZELLIJ_SOCKET_DIR", ""
    )
    if (
        source_socket
        and seat.socket_dir
        and Path(source_socket).resolve() != Path(seat.socket_dir).resolve()
    ):
        raise ValueError(
            "Workspace belongs to another Frame runtime — return through that runtime's workspace rail"
        )
    # switch-session's source socket is this pane's socket, handled on execute.
    switch = (
        "vc-frame",
        "--session",
        current,
        "action",
        "switch-session",
        seat.runtime_session_id,
    )
    if not current:
        raise ValueError(
            "Current Frame client is unknown — open this picker inside Frame"
        )
    actions = [ReturnAction("workspace", "Return to workspace", str(root), (switch,))]
    live_agents: set[str] = set()
    shell_open = False
    unclassified_terminal = False
    for pane in workshop._pane_rows(payload):
        if pane.get("is_plugin") or workshop._pane_liveness(pane) == "inactive":
            continue
        identity = str(
            pane.get("id") if pane.get("id") is not None else pane.get("pane_id", "")
        )
        number = identity.removeprefix("terminal_")
        if not number.isdecimal():
            continue
        command = str(pane.get("command") or pane.get("pane_command") or "")
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = []
        agent = workshop._agent_from_command(command)
        is_shell = bool(
            tokens and Path(tokens[0]).name in {"bash", "zsh", "sh", "fish"}
        )
        # Scripts in a shell (including Start Here/Workshop) are tools, not work shells.
        if is_shell and any(flag in tokens for flag in ("-c", "-lc")):
            try:
                script = shlex.split(tokens[-1])
                exec_index = script.index("exec")
                is_shell = Path(script[exec_index + 1]).name in {
                    "bash",
                    "zsh",
                    "sh",
                    "fish",
                }
            except (ValueError, IndexError):
                is_shell = False
        if not agent and any(
            "vc-start-here.py" in token or "vc-agent-workshop.py" in token
            for token in tokens
        ):
            continue
        if agent:
            live_agents.add(agent)
        shell_open |= is_shell
        unclassified_terminal |= not agent and not is_shell
        label = str(
            pane.get("pane_title")
            or pane.get("title")
            or pane.get("name")
            or agent
            or "Shell"
        )
        actions.append(
            ReturnAction(
                "pane:" + number,
                "Open " + label,
                f"{'Conversation' if agent else 'Work terminal'} · existing terminal_{number} · {workshop._pane_liveness(pane)}",
                ((*switch, "--pane-id", "terminal_" + number),),
            )
        )
    if not shell_open:
        actions.append(
            ReturnAction(
                "recover-shell",
                "Open a work shell",
                "Work shell is closed or missing. Create one explicitly; existing conversations stay open.",
                (
                    tuple(
                        workshop.launch_pane_argv(
                            "Shell",
                            root,
                            ["zsh", "-l"],
                            session=seat.runtime_session_id,
                        )
                    ),
                ),
                True,
            )
        )
    if unclassified_terminal:
        # An open terminal with an unclassified command may already host a
        # provider. Focus it first; never guess that a stopped receipt is safe
        # to resume beside it.
        actions[0] = actions[0]._replace(
            detail=str(root)
            + " · Conversation identity is unavailable in one terminal; open it before resuming in Agents."
        )
        return actions, seat.socket_dir
    for provider in workshop.AGENTS:
        # A live provider pane takes precedence over a stopped run receipt.
        # Without a proven pane-to-native-session binding, never auto-resume
        # another conversation beside it. Closed sessions remain in Workshop.
        if provider in live_agents:
            continue
        for runtime in ("local-native", "local-worktrees", "local-vm"):
            if not interactive_resume_support(provider, runtime)[0]:
                continue
            for candidate in resumable_interactive_runs(provider, root, runtime):
                run_id = candidate["run_id"]
                command = workshop.launch_argv(
                    provider, "resume", runtime=runtime, run_id=run_id
                )
                pane = workshop.launch_pane_argv(
                    f"{provider} · resume",
                    candidate["root"],
                    command,
                    session=seat.runtime_session_id,
                )
                actions.append(
                    ReturnAction(
                        "resume:" + run_id,
                        f"Resume {provider} · {candidate['session_id']}",
                        f"Closed conversation · {candidate['updated_at'] or 'last activity unknown'} · {candidate['root']} · {runtime}. No reliable last conversation context.",
                        (tuple(pane),),
                        True,
                    )
                )
    return actions, seat.socket_dir


def project_readiness(
    *,
    workspace_root: str | None = None,
    cwd: str | None = None,
    home: str | None = None,
) -> tuple[str, str] | None:
    """Return an actionable line when no usable project is open.

    Home and ``/`` as cwd are not a project. Do not send those at the
    control-plane backend — Open project is the product choice.
    """
    if workspace_root is None:
        workspace_root = os.environ.get("VIBECRAFTED_WORKSPACE_ROOT", "")
    root = workspace_root.strip()
    if root:
        if not Path(root).is_dir():
            return "attention", "Workspace path is missing — choose Open project"
        return None
    here = Path(cwd or os.getcwd()).resolve()
    home_path = Path(home or Path.home()).resolve()
    if here == home_path or here == Path("/"):
        return "attention", "No project is open — choose Open project"
    return None


def readiness_from_service_payload(
    payload: Any,
    *,
    deck_available: bool = True,
    frame_available: bool = True,
) -> tuple[str, str]:
    """Project canonical service JSON into one concise first-run readiness line."""
    if not deck_available:
        return "missing", "Vibecrafted launcher is missing — reinstall the Runtime Pack"
    if not frame_available:
        return "missing", "vc-frame is missing — reinstall the Runtime Pack"
    if not isinstance(payload, dict):
        return "attention", "VC Server status is unavailable — open Help & diagnostics"
    if not payload.get("installed"):
        return "attention", "VC Server is not installed — open Help & diagnostics"
    if not payload.get("loaded"):
        return (
            "stopped",
            "VC Server is stopped — use the Vibecrafted menu bar to start it",
        )
    healthy = all(
        bool(payload.get(key))
        for key in (
            "supervisor_live",
            "supervisor_verified",
            "supervisor_service_managed",
            "build_current",
            "pair_healthy",
        )
    )
    if healthy:
        return "ready", "VC Server is healthy — this workspace is ready"
    return "attention", "VC Server needs attention — open Help & diagnostics"


def probe_readiness() -> tuple[str, str]:
    project_state = project_readiness()
    if project_state is not None:
        return project_state
    deck = shutil.which("vibecrafted")
    frame = shutil.which("vc-frame")
    if deck is None or frame is None:
        return readiness_from_service_payload(
            None, deck_available=deck is not None, frame_available=frame is not None
        )
    child_environment = os.environ.copy()
    # This pane is launched below the generated vc-start wrapper.  Its launcher
    # declaration belongs to vc-start, not to the nested vibecrafted status
    # command; inheriting it makes an otherwise current server pair look stale.
    child_environment.pop("VIBECRAFTED_DECLARED_LAUNCHER", None)
    try:
        result = subprocess.run(
            [deck, "server", "service", "status", "--json"],
            check=False,
            capture_output=True,
            env=child_environment,
            text=True,
            timeout=2.0,
        )
        return readiness_from_service_payload(json.loads(result.stdout))
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return readiness_from_service_payload(None)


def action_for_mouse_row(
    y: int, targets: list[tuple[int, int, int, str]], x: int
) -> str | None:
    for row, left, right, action in targets:
        if y == row and left <= x <= right:
            return action
    return None


class Row(NamedTuple):
    """One rendered screen row; ``action`` marks a mouse target."""

    row: int
    col: int
    text: str
    attr: int
    action: str | None = None


def canvas_geometry(width: int) -> tuple[int, int]:
    """Return ``(left, canvas)`` — a left-anchored reading column.

    The column keeps a small gutter and never grows past ``READABLE_WIDTH``;
    it is not centred, so a wide pane does not float the text far right.
    """
    left = 2 if width < 60 else 4
    canvas = max(MIN_CANVAS, min(READABLE_WIDTH, width - left - 2))
    return left, canvas


def _wrap(text: str, width: int, *, hanging: int = 0) -> list[str]:
    """Wrap ``text`` to ``width``; continuation rows indent by ``hanging``."""
    return textwrap.wrap(text, max(1, width), subsequent_indent=" " * hanging) or [""]


def _compose(
    *,
    left: int,
    canvas: int,
    density: int,
    selected: int,
    readiness: tuple[str, str],
    error: str,
) -> list[Row]:
    """Lay rows out from row 0 at one density.

    0 airy · 1 tight · 2 compact (title and detail share a row) ·
    3 essentials (no prose, one clipped row per action) for tiny panes.
    """
    rows: list[Row] = []
    cursor = 0

    def emit(
        lines: list[str], attr: int, col: int = 0, action: str | None = None
    ) -> None:
        nonlocal cursor
        for line in lines:
            rows.append(Row(cursor, left + col, line, attr, action))
            cursor += 1

    def gap(minimum_density: int = 1) -> None:
        nonlocal cursor
        if density < minimum_density:
            cursor += 1

    essentials = density >= 3
    emit(["LAUNCHPAD"], curses.A_BOLD)
    gap(2)
    if not essentials:
        emit(_wrap(PRODUCT_LINE, canvas), curses.A_NORMAL)
        gap(2)
    state, message = readiness
    # The state word is spelled out: readiness must not rely on colour or dim.
    readiness_text = (
        f"RUNTIME [{state}]" if essentials else f"RUNTIME [{state}] {message}"
    )
    emit(
        _wrap(readiness_text, canvas),
        curses.A_BOLD if state == "ready" else curses.A_NORMAL,
    )
    gap(2)
    emit(["Choose where to begin:"], curses.A_BOLD)
    gap(1)
    for index, (title, detail, action) in enumerate(ACTIONS):
        marker = "▶" if index == selected else " "
        attr = curses.A_REVERSE | curses.A_BOLD if index == selected else curses.A_BOLD
        if essentials:
            emit(
                [_clip(f"{marker} [{index + 1}] {title}", canvas)], attr, action=action
            )
        elif density >= 2:
            emit(
                _wrap(
                    f"{marker} [{index + 1}] {title} — {detail}",
                    canvas,
                    hanging=DETAIL_INDENT,
                ),
                attr,
                action=action,
            )
        else:
            emit([f"{marker} [{index + 1}] {title}"], attr, action=action)
            emit(
                _wrap(detail, canvas - DETAIL_INDENT),
                curses.A_NORMAL,
                col=DETAIL_INDENT,
                action=action,
            )
            gap(1)
    gap(2)
    emit(_wrap(HELP_LINE_SHORT if essentials else HELP_LINE, canvas), curses.A_NORMAL)
    if error:
        gap(2)
        emit(_wrap(error, canvas), curses.A_BOLD)
    return rows


def layout_rows(
    height: int,
    width: int,
    *,
    selected: int,
    readiness: tuple[str, str],
    error: str = "",
) -> list[Row]:
    """Compute every visible row for a pane of ``height`` × ``width``.

    Prose wraps inside the reading column; the airiest layout that still shows
    all actions and the help line wins; a little top slack is added only when
    the pane is tall enough to afford it.
    """
    left, canvas = canvas_geometry(width)
    rows: list[Row] = []
    for density in (0, 1, 2, 3):
        rows = _compose(
            left=left,
            canvas=canvas,
            density=density,
            selected=selected,
            readiness=readiness,
            error=error,
        )
        if rows[-1].row < height:
            break
    used = rows[-1].row + 1
    top = max(0, min(2, (height - used) // 2))
    return [row._replace(row=row.row + top) for row in rows]


def mouse_targets(rows: list[Row], width: int) -> list[tuple[int, int, int, str]]:
    """Every rendered action row is clickable across the reading column."""
    left, canvas = canvas_geometry(width)
    return [
        (row.row, left, left + canvas, row.action)
        for row in rows
        if row.action is not None
    ]


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def _put(window: curses.window, row: int, col: int, text: str, attr: int = 0) -> None:
    height, width = window.getmaxyx()
    if not (0 <= row < height and 0 <= col < width):
        return
    try:
        window.addstr(row, col, _clip(text, width - col), attr)
    except curses.error:
        pass


def adopt_terminal_colors() -> None:
    """Draw with the terminal default pair instead of palette black/white.

    ``curses.wrapper`` calls ``start_color()``; without this ncurses paints
    pair 0 as palette 7 on palette 0 and the pane turns into an opaque block
    of the theme's "black" regardless of the selected light/moon mode.
    """
    try:
        if curses.has_colors():
            curses.use_default_colors()
    except curses.error:
        pass


class StartHere:
    def __init__(self, window: curses.window) -> None:
        self.window = window
        self.selected = 0
        self.readiness = probe_readiness()
        self.error = ""
        self.targets: list[tuple[int, int, int, str]] = []
        self.return_actions: list[ReturnAction] | None = None
        self.return_workspace = ""
        self.return_socket = ""
        self.pending_recovery: ReturnAction | None = None

    def configure(self) -> None:
        adopt_terminal_colors()
        curses.curs_set(0)
        curses.noecho()
        curses.cbreak()
        self.window.keypad(True)
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except curses.error:
            pass

    def run(self) -> None:
        self.configure()
        while True:
            self.draw()
            key = self.window.getch()
            if key in (ord("q"), 27):
                if self.return_actions is not None:
                    self.return_actions = None
                    self.return_workspace = ""
                    self.selected = 0
                    self.error = ""
                    continue
                return
            if self.return_actions is not None:
                if key == ord("r"):
                    if self.return_workspace:
                        self.open_return_project(self.return_workspace)
                    else:
                        self.open_recent_projects()
                elif self.return_actions:
                    if key in (curses.KEY_UP, ord("k")):
                        self.selected = (self.selected - 1) % len(self.return_actions)
                        self.pending_recovery = None
                    elif key in (curses.KEY_DOWN, ord("j"), 9):
                        self.selected = (self.selected + 1) % len(self.return_actions)
                        self.pending_recovery = None
                    elif key in (10, 13, curses.KEY_ENTER):
                        self.activate_return(self.return_actions[self.selected].key)
                    elif key == curses.KEY_MOUSE:
                        self.handle_return_mouse()
                continue
            if key == curses.KEY_RESIZE:
                continue
            if key in (curses.KEY_UP, ord("k")):
                self.selected = (self.selected - 1) % len(ACTIONS)
            elif key in (curses.KEY_DOWN, ord("j"), 9):
                self.selected = (self.selected + 1) % len(ACTIONS)
            elif key in (10, 13, curses.KEY_ENTER):
                self.activate(ACTIONS[self.selected][2])
            elif key == ord("r"):
                self.readiness = probe_readiness()
                self.error = ""
            elif ord("1") <= key <= ord(str(len(ACTIONS))):
                self.selected = key - ord("1")
                self.activate(ACTIONS[self.selected][2])
            elif key == curses.KEY_MOUSE:
                self.handle_mouse()

    def draw(self) -> None:
        self.window.erase()
        height, width = self.window.getmaxyx()
        if self.return_actions is not None:
            self.draw_return(height, width)
            return
        rows = layout_rows(
            height,
            width,
            selected=self.selected,
            readiness=self.readiness,
            error=self.error,
        )
        for row in rows:
            _put(self.window, row.row, row.col, row.text, row.attr)
        self.targets = mouse_targets(rows, width)
        self.window.refresh()

    def activate(self, action: str) -> None:
        if action == "recent":
            self.open_recent_projects()
            return
        try:
            argv = action_argv(action)
            timeout = 8.0
            if action == "project":
                chosen = subprocess.run(
                    argv,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120.0,
                )
                path = (chosen.stdout or "").strip().rstrip("/")
                if chosen.returncode != 0 or not path:
                    self.error = "No project selected — choose a folder to open"
                    return
                if not Path(path).is_dir():
                    self.error = "That folder is missing — choose Open project again"
                    return
                argv = action_argv("project", project_path=path)
                timeout = 30.0
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                self.error = (result.stderr or result.stdout).strip() or (
                    f"{action} exited {result.returncode}"
                )
            else:
                self.error = ""
                if action == "project":
                    self.readiness = probe_readiness()
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            self.error = f"Could not open {action}: {error}"

    def open_recent_projects(self) -> None:
        self.return_workspace = ""
        self.pending_recovery = None
        self.selected = 0
        try:
            self.return_actions = recent_project_actions()
            self.error = (
                ""
                if self.return_actions
                else "No recent projects — choose Open project"
            )
        except (OSError, ValueError, RuntimeError) as exc:
            self.return_actions = []
            self.error = str(exc)

    def open_return_project(self, workspace_id: str) -> None:
        self.return_workspace = workspace_id
        self.pending_recovery = None
        self.selected = 0
        try:
            self.return_actions, self.return_socket = return_project_actions(
                workspace_id
            )
            self.error = ""
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.return_actions = []
            self.error = str(exc)

    def activate_return(self, key: str) -> None:
        if key.startswith("project:"):
            self.open_return_project(key.split(":", 1)[1])
            return
        try:
            # Re-read root, WES ownership and actual inventory before each action.
            actions, socket = return_project_actions(self.return_workspace)
            chosen = next((a for a in actions if a.key == key), None)
            if chosen is None:
                self.pending_recovery = None
                self.return_actions = actions
                self.error = "That resource is no longer open — choose an explicit recovery action"
                return
            displayed = next(
                (a for a in self.return_actions or [] if a.key == key), None
            )
            if displayed != chosen:
                self.pending_recovery = None
                self.return_actions = actions
                self.error = "That resource changed — review its current identity and choose again"
                return
            if chosen.recovery and self.pending_recovery != chosen:
                self.pending_recovery = chosen
                self.error = chosen.detail + " Enter again to confirm; Esc cancels."
                return
            self.pending_recovery = None
            for command in chosen.commands:
                env = _return_environment(socket)
                if "switch-session" in command:
                    # switch-session is sent to the hosting client, which may
                    # live on another socket than the destination project.
                    from vibecrafted_core.runtime_paths import (
                        selected_runtime_environment,
                    )

                    env = selected_runtime_environment()
                result = subprocess.run(
                    command,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30.0,
                )
                if result.returncode:
                    raise ValueError(
                        (result.stderr or result.stdout).strip()
                        or "Could not open resource — refresh to retry"
                    )
            self.error = "Opened " + chosen.title
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.error = str(exc)

    def draw_return(self, height: int, width: int) -> None:
        left, canvas = canvas_geometry(width)
        _put(
            self.window,
            0,
            left,
            "RETURN TO WORK" if self.return_workspace else "RECENT PROJECTS",
            curses.A_BOLD,
        )
        page_size = max(1, height - 10)
        first = (self.selected // page_size) * page_size
        rows: list[Row] = []
        for index in range(
            first, min(first + page_size, len(self.return_actions or []))
        ):
            action = self.return_actions[index]
            marker = "▶" if index == self.selected else " "
            attr = (
                curses.A_REVERSE | curses.A_BOLD
                if index == self.selected
                else curses.A_NORMAL
            )
            rows.append(
                Row(
                    index - first + 2,
                    left,
                    _clip(marker + " " + action.title, canvas),
                    attr,
                    action.key,
                )
            )
        for row in rows:
            _put(self.window, row.row, row.col, row.text, row.attr)
        if self.return_actions:
            detail = self.return_actions[self.selected].detail
            for index, line in enumerate(_wrap(detail, canvas)[:4]):
                _put(self.window, max(0, height - 7) + index, left, line)
        if self.error:
            for index, line in enumerate(_wrap(self.error, canvas)[:2]):
                _put(self.window, max(0, height - 3) + index, left, line, curses.A_BOLD)
        _put(
            self.window,
            max(0, height - 1),
            left,
            "↑↓ select · Enter open · r refresh · Esc/q back",
        )
        self.targets = mouse_targets(rows, width)
        self.window.refresh()

    def handle_return_mouse(self) -> None:
        try:
            _, x, y, _, button = curses.getmouse()
        except curses.error:
            return
        if button & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED):
            key = action_for_mouse_row(y, self.targets, x)
            if key:
                self.selected = next(
                    i for i, a in enumerate(self.return_actions or []) if a.key == key
                )
                self.activate_return(key)

    def handle_mouse(self) -> None:
        try:
            _, x, y, _, button = curses.getmouse()
        except curses.error:
            return
        if not button & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED):
            return
        action = action_for_mouse_row(y, self.targets, x)
        if action is None:
            return
        self.selected = [item[2] for item in ACTIONS].index(action)
        self.activate(action)


def main() -> int:
    curses.wrapper(lambda window: StartHere(window).run())
    return 0


if __name__ == "__main__":
    ensure_generation_python()
    raise SystemExit(main())
