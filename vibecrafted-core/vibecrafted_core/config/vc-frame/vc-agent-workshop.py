#!/usr/bin/env python3
"""Agent Workspaces dashboard and interactive Agent launcher.

This is deliberately a terminal surface, not a second control plane.  vc-frame
owns the panes, Vibecrafted owns the launch command, and the User chooses which
interactive Agent is born in the current workspace.
"""

from __future__ import annotations

import argparse
import curses
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _generation_python_candidates() -> list[str]:
    """Interpreters that can import vibecrafted_core without host PYTHONPATH.

    vc-frame ``bash -lc`` panes do not inherit the deck wrapper.  Match
    ``spawn_python_bin`` in runtime/scripts/lib/meta.sh: env override, then the
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
    """Re-exec a generation/uv interpreter when host python3 lacks core.

    vc-frame panes often run ``#!/usr/bin/env python3`` (Homebrew 3.14 on this
    host).  Generation ``bin/python3`` is a wrapper that sets PYTHONPATH onto
    the receipted ``vibecrafted-core``.  Source-lane tools installs have no
    ``bin/python3``; the uv tool venv does.
    """
    try:
        import vibecrafted_core  # noqa: F401
    except ImportError:
        pass
    else:
        return
    here = os.path.realpath(sys.executable)
    for wanted in _generation_python_candidates():
        if not os.access(wanted, os.X_OK):
            continue
        if os.path.realpath(wanted) == here:
            continue
        os.execv(wanted, [wanted, *sys.argv])
    raise SystemExit(
        "vc-agent-workshop: no module named 'vibecrafted_core'; "
        "set VIBECRAFTED_PYTHON to the generation python3 "
        "(or run through vc-start so the Runtime Pack is on PATH)"
    )


# Self-consistency: when this script runs from inside a core source tree
# (worktree or installed generation), the core must come from the SAME tree.
# Ambient PYTHONPATH can otherwise resolve an older/newer installed generation
# while the launcher UI is this tree's — the "unsupported provider" crash class.
# Materialized frame-config copies fail the guard and keep the re-exec path.
_SCRIPT_CORE_TREE = Path(__file__).resolve().parents[3]
if (_SCRIPT_CORE_TREE / "vibecrafted_core" / "__init__.py").is_file():
    sys.path.insert(0, str(_SCRIPT_CORE_TREE))

ensure_generation_python()

from vibecrafted_core.aicx_session_chain import (
    CliSessionChain,
    SessionChain,
    SessionChainError,
    SessionRecord,
    project_filter_for_root,
)
from vibecrafted_core.spawn import (
    CONTINUITY_MODES,
    OPERATOR_POLICIES,
    PERMISSION_POLICIES,
    RUNTIME_POLICIES,
    continuity_policy_capabilities,
    resolve_operator_agent_policy,
    resolve_provider_policy,
    runtime_policy_capabilities,
)

AGENTS = ("agy", "claude", "codex", "cursor", "grok", "junie")
LAUNCH_MODES = ("init", "resume", "partner", "operator")
MODE_PROMPTS = {"partner": "/vc-partner", "operator": "/vc-operator"}
RUNTIME_HELP = {
    "local-native": (
        "Direct selected checkout; no isolation; full disk scope per provider permissions.",
        "Shared checkout, no worktrees — for deliberate control.",
    ),
    "local-worktrees": (
        "Safe recommended local default; one canonical worktree per Agent launch.",
        "Maximum local concurrency; unattended pipelines require an Operator Agent via --operator auto or claude.",
    ),
    "local-vm": (
        "Coming in H2b3; disabled until selected-workspace container launch and live proof exist.",
        "",
    ),
    "cloud-soon": ("Coming soon; disabled.", ""),
}


def launch_argv(
    agent: str,
    mode: str,
    runtime: str = "local-native",
    permissions: str = "bypass",
    operator: str = "none",
    continuity: str = "fresh",
    continuity_parent: str = "",
    workspace: str | os.PathLike[str] = "",
) -> list[str]:
    """Return the one canonical interactive command for a launcher choice."""
    if agent not in AGENTS:
        raise ValueError(f"unsupported agent: {agent}")
    if mode not in LAUNCH_MODES:
        raise ValueError(f"unsupported interactive mode: {mode}")
    if continuity not in CONTINUITY_MODES:
        raise ValueError(f"unsupported continuity policy: {continuity}")
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
        # is stricter: this exact floating panel becomes the Agent TTY.
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
        if mode in MODE_PROMPTS:
            command.extend(["--prompt", MODE_PROMPTS[mode]])
        return command
    if runtime != "local-native":
        raise ValueError(
            "worktree resume supervision belongs to H2b2 and is not configured yet"
        )
    command = ["vibecrafted", "resume", agent]
    if root:
        command.extend(["--root", root])
    return command


def mode_capabilities(
    agent: str, runtime: str, permissions: str
) -> dict[str, dict[str, Any]]:
    """Describe every launch mode without hiding unsupported combinations."""
    decision = resolve_provider_policy(agent, runtime, permissions, "interactive")
    resume_available = runtime == "local-native"
    return {
        "init": {"available": decision.supported, "reason": decision.reason},
        "resume": {
            "available": resume_available,
            "reason": (
                ""
                if resume_available
                else "resume is supported only in local-native runtime"
            ),
        },
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
    if chain is None:
        aicx = shutil.which("aicx")
        if not aicx:
            return [], "aicx executable not found; parent sessions unavailable"
        chain = CliSessionChain(aicx)
    try:
        result = chain.list_sessions(
            project=project_filter_for_root(root_path),
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


def _pane_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("panes", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def agent_faces_from_payload(payload: Any) -> list[str]:
    """Project vc-frame's pane JSON into human-facing Agents-tab faces."""
    faces: list[str] = []
    for pane in _pane_rows(payload):
        if pane.get("is_plugin"):
            continue
        tab_name = str(pane.get("tab_name") or pane.get("tab") or "")
        if tab_name and tab_name.casefold() != "agents":
            continue
        title = str(
            pane.get("pane_title") or pane.get("title") or pane.get("name") or ""
        )
        command = str(pane.get("command") or pane.get("pane_command") or "")
        label = title.strip() or Path(command).name.strip()
        if not label or label.casefold() in {"agent workspaces", "new agent"}:
            continue
        if label not in faces:
            faces.append(label)
    return faces


def current_faces() -> list[str]:
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
            return []
        return agent_faces_from_payload(json.loads(result.stdout))
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return []


# Curses pair 0 is COLOR_BLACK. Signed palettes put purple-navy `#26233a` in
# `colors.normal.black`, which is not dark paper (`#0b0b12`) and becomes a dark
# rectangle on light paper (`#fafafa`). Pair 1 at default/default follows the
# vc-frame / alacritty theme in both modes.
_PAPER_PAIR = 1
_PAPER = 0


def bind_terminal_paper(window: curses.window) -> int:
    """Paint with the host terminal paper; never ANSI black."""
    global _PAPER
    curses.use_default_colors()
    curses.init_pair(_PAPER_PAIR, -1, -1)
    _PAPER = curses.color_pair(_PAPER_PAIR)
    window.bkgd(" ", _PAPER)
    window.bkgdset(" ", _PAPER)
    return _PAPER


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def _safe_addstr(
    window: curses.window, row: int, col: int, text: str, attr: int = 0
) -> None:
    height, width = window.getmaxyx()
    if row < 0 or row >= height or col < 0 or col >= width:
        return
    try:
        window.addstr(row, col, _clip(text, width - col), attr | _PAPER)
    except curses.error:
        pass


def _dim_unavailable_choices(
    window: curses.window,
    row: int,
    col: int,
    choices: tuple[str, ...],
    available: tuple[bool, ...],
) -> None:
    """Redraw disabled choice tokens with terminal-native dim styling."""
    for token, enabled in zip(
        _choice_tokens(choices, selected=-1, available=available),
        available,
        strict=True,
    ):
        if not enabled:
            _safe_addstr(window, row, col, token, curses.A_DIM)
        col += len(token) + 1


def _choice_tokens(
    choices: tuple[str, ...],
    *,
    selected: int,
    available: tuple[bool, ...] | None = None,
) -> tuple[str, ...]:
    """Use one canonical bullet and leave available unselected choices plain."""
    enabled = available or tuple(True for _ in choices)
    return tuple(
        f"• {choice}"
        if index == selected and enabled[index]
        else (choice if enabled[index] else f"× {choice}")
        for index, choice in enumerate(choices)
    )


class Workshop:
    def __init__(self, window: curses.window, *, mode: str) -> None:
        self.window = window
        self.mode = mode
        self.home_choice = 0
        self.row = 0
        self.agent = 2  # codex is the least surprising neutral default here
        self.launch_mode = 0
        self.runtime = 1  # safe recommended local default when the provider supports it
        self.permissions = 0
        self.continuity = 0
        self.continuity_parent = ""
        self.parent_sessions: list[SessionRecord] = []
        self.parent_index = -1
        self.parent_error = ""
        self.path = str(Path.cwd())
        self.error = ""
        self.mouse_targets: list[tuple[int, int, int, int, str]] = []
        self.last_faces_at = 0.0
        self.faces: list[str] = []

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
        self._normalize_runtime_choice()
        self._normalize_permission_choice()
        self._normalize_mode_choice()
        self._normalize_continuity_choice()
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except curses.error:
            pass

    def run(self) -> None:
        self.configure()
        while True:
            self.draw()
            key = self.window.getch()
            if key == -1:
                continue
            if key == curses.KEY_RESIZE:
                continue
            if key == curses.KEY_MOUSE:
                self.handle_mouse()
                continue
            if self.mode == "home":
                self.handle_home_key(key)
            else:
                self.handle_launcher_key(key)

    def draw(self) -> None:
        self.window.erase()
        self.mouse_targets.clear()
        if self.mode == "home":
            self.draw_home()
        else:
            self.draw_launcher()
        self.window.refresh()

    def draw_home(self) -> None:
        height, width = self.window.getmaxyx()
        left = max(2, (width - min(width - 4, 78)) // 2)
        top = max(1, min(6, (height - 18) // 2))
        _safe_addstr(self.window, top, left, "AGENT WORKSPACES", curses.A_BOLD)
        _safe_addstr(
            self.window,
            top + 2,
            left,
            "One workspace. Many interactive Agents. One shared context.",
        )
        _safe_addstr(self.window, top + 4, left, "Workspace", curses.A_DIM)
        _safe_addstr(self.window, top + 5, left, self.path, curses.A_BOLD)

        buttons = ("New agent", "voc")
        col = left
        for index, label in enumerate(buttons):
            text = f"[ {label} ]"
            attr = curses.A_REVERSE if index == self.home_choice else curses.A_BOLD
            _safe_addstr(self.window, top + 7, col, text, attr)
            self.mouse_targets.append((top + 7, col, col + len(text), index, "home"))
            col += len(text) + 2

        now = time.monotonic()
        if now - self.last_faces_at > 2:
            self.faces = current_faces()
            self.last_faces_at = now
        _safe_addstr(
            self.window,
            top + 10,
            left,
            f"Agents here ({len(self.faces)})",
            curses.A_DIM,
        )
        if self.faces:
            for offset, face in enumerate(self.faces[: max(1, height - top - 14)]):
                _safe_addstr(self.window, top + 11 + offset, left + 2, f"• {face}")
        else:
            _safe_addstr(
                self.window,
                top + 11,
                left + 2,
                "No Agent faces yet — New agent opens the first interactive TTY.",
                curses.A_DIM,
            )
        _safe_addstr(
            self.window,
            height - 2,
            left,
            "←/→ choose · Enter open · n New agent · v voc · PANE+arrows switch faces",
            curses.A_DIM,
        )
        if self.error:
            _safe_addstr(self.window, height - 1, left, self.error, curses.A_BOLD)

    def draw_launcher(self) -> None:
        height, width = self.window.getmaxyx()
        card_width = min(max(58, width - 4), 92)
        left = max(1, (width - card_width) // 2)
        top = max(1, (height - 15) // 2)
        inner = max(20, card_width - 4)
        _safe_addstr(
            self.window,
            top,
            left,
            "┌ ❯ New agent " + "─" * max(1, card_width - 29) + " [Cancel] ┐",
        )
        agent_line = "  agent    " + " ".join(
            _choice_tokens(AGENTS, selected=self.agent)
        )
        provider = AGENTS[self.agent]
        capabilities = runtime_policy_capabilities(provider)
        runtime_available = tuple(
            bool(capabilities[name]["available"]) for name in RUNTIME_POLICIES
        )
        runtime_line = "  runtime  " + " ".join(
            _choice_tokens(
                RUNTIME_POLICIES,
                selected=self.runtime,
                available=runtime_available,
            )
        )
        permission_available = tuple(
            resolve_provider_policy(
                provider, RUNTIME_POLICIES[self.runtime], name, "interactive"
            ).supported
            for name in PERMISSION_POLICIES
        )
        permission_line = "  permits  " + " ".join(
            _choice_tokens(
                PERMISSION_POLICIES,
                selected=self.permissions,
                available=permission_available,
            )
        )
        mode_caps = mode_capabilities(
            provider,
            RUNTIME_POLICIES[self.runtime],
            PERMISSION_POLICIES[self.permissions],
        )
        mode_available = tuple(
            bool(mode_caps[name]["available"]) for name in LAUNCH_MODES
        )
        mode_line = "  mode     " + " ".join(
            _choice_tokens(
                LAUNCH_MODES,
                selected=self.launch_mode,
                available=mode_available,
            )
        )
        continuity_caps = continuity_policy_capabilities(
            provider,
            root=self.path,
            explicit_parent=self.continuity_parent,
        )
        continuity_available = tuple(
            bool(continuity_caps[name]["available"]) for name in CONTINUITY_MODES
        )
        continuity_line = "  memory   " + " ".join(
            _choice_tokens(
                CONTINUITY_MODES,
                selected=self.continuity,
                available=continuity_available,
            )
        )
        rows = (
            agent_line,
            mode_line,
            runtime_line,
            permission_line,
            continuity_line,
            f"  parent   {self.continuity_parent or '(none)'}",
            f"  path     {self.path}",
        )
        for index, line in enumerate(rows):
            attr = curses.A_REVERSE if index == self.row else 0
            _safe_addstr(
                self.window,
                top + index + 1,
                left,
                "│ "
                + _clip(line, inner)
                + " " * max(0, inner - len(_clip(line, inner)))
                + " │",
                attr,
            )
        _dim_unavailable_choices(
            self.window,
            top + 2,
            left + 2 + len("  mode     "),
            LAUNCH_MODES,
            mode_available,
        )
        _dim_unavailable_choices(
            self.window,
            top + 3,
            left + 2 + len("  runtime  "),
            RUNTIME_POLICIES,
            runtime_available,
        )
        _dim_unavailable_choices(
            self.window,
            top + 5,
            left + 2 + len("  memory   "),
            CONTINUITY_MODES,
            continuity_available,
        )
        _dim_unavailable_choices(
            self.window,
            top + 4,
            left + 2 + len("  permits  "),
            PERMISSION_POLICIES,
            permission_available,
        )
        runtime_help = RUNTIME_HELP[RUNTIME_POLICIES[self.runtime]]
        _safe_addstr(
            self.window,
            top + 8,
            left,
            ("│ " + _clip(runtime_help[0], inner)).ljust(card_width - 1) + "│",
            curses.A_DIM,
        )
        _safe_addstr(
            self.window,
            top + 9,
            left,
            ("│ " + _clip(runtime_help[1], inner)).ljust(card_width - 1) + "│",
            curses.A_DIM,
        )
        _safe_addstr(
            self.window,
            top + 10,
            left,
            "│ Enter = interactive TTY on this Agents tab".ljust(card_width - 1) + "│",
            curses.A_DIM,
        )
        _safe_addstr(
            self.window,
            top + 11,
            left,
            "└─ ↑/↓ row · ←/→ choose parent · type/edit path · Enter launch · Esc "
            + "─" * max(0, card_width - 67)
            + "┘",
        )
        unavailable = [
            f"{name}: {capabilities[name]['reason']}"
            for name in RUNTIME_POLICIES
            if not capabilities[name]["available"]
        ]
        _safe_addstr(
            self.window,
            top + 12,
            left,
            "Unavailable — "
            + " · ".join(
                unavailable
                + [
                    f"{name}: {mode_caps[name]['reason']}"
                    for name in LAUNCH_MODES
                    if not mode_caps[name]["available"]
                ]
                + [
                    f"{name}: {continuity_caps[name]['reason']}"
                    for name in CONTINUITY_MODES
                    if not continuity_caps[name]["available"]
                ]
            ),
            curses.A_DIM,
        )
        if self.error:
            _safe_addstr(
                self.window, min(height - 1, top + 13), left, self.error, curses.A_BOLD
            )

    def handle_home_key(self, key: int) -> None:
        if key in (curses.KEY_LEFT, ord("h")):
            self.home_choice = (self.home_choice - 1) % 2
        elif key in (curses.KEY_RIGHT, ord("l"), ord("\t")):
            self.home_choice = (self.home_choice + 1) % 2
        elif key in (ord("n"), ord("N")):
            self.open_launcher()
        elif key in (ord("v"), ord("V")):
            self.open_voc()
        elif key in (10, 13, curses.KEY_ENTER):
            (self.open_launcher, self.open_voc)[self.home_choice]()

    def handle_launcher_key(self, key: int) -> None:
        self.error = ""
        if key == 27:
            raise SystemExit(0)
        if key == curses.KEY_UP:
            self.row = (self.row - 1) % 7
            return
        if key in (curses.KEY_DOWN, ord("\t")):
            self.row = (self.row + 1) % 7
            return
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord(" ")):
            delta = -1 if key == curses.KEY_LEFT else 1
            if self.row == 0:
                self.agent = (self.agent + delta) % len(AGENTS)
                self._normalize_runtime_choice()
                self._normalize_permission_choice()
                self._normalize_mode_choice()
                self._normalize_continuity_choice()
                self.parent_sessions = []
                self.parent_index = -1
            elif self.row == 1:
                self._cycle_mode(delta)
            elif self.row == 2:
                self._cycle_runtime(delta)
            elif self.row == 3:
                self._cycle_permissions(delta)
            elif self.row == 4:
                self._cycle_continuity(delta)
            elif self.row == 5:
                self._cycle_parent(delta)
            return
        if key in (10, 13, curses.KEY_ENTER):
            self.launch()
            return
        if self.row in (5, 6):
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if self.row == 5:
                    self.continuity_parent = self.continuity_parent[:-1]
                    self.parent_index = -1
                else:
                    self.path = self.path[:-1]
                    self.parent_sessions = []
                    self.parent_index = -1
            elif 32 <= key <= 126:
                if self.row == 5:
                    self.continuity_parent += chr(key)
                    self.parent_index = -1
                else:
                    self.path += chr(key)
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
                return
        self.error = "No interactive mode is available for this provider/runtime"

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
        self._normalize_continuity_choice()

    def _cycle_runtime(self, delta: int) -> None:
        capabilities = runtime_policy_capabilities(AGENTS[self.agent])
        for _ in RUNTIME_POLICIES:
            self.runtime = (self.runtime + delta) % len(RUNTIME_POLICIES)
            name = RUNTIME_POLICIES[self.runtime]
            if capabilities[name]["available"]:
                self._normalize_permission_choice()
                self._normalize_mode_choice()
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
        self.error = "No permission policy is available for this provider/runtime"

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
        capabilities = continuity_policy_capabilities(
            AGENTS[self.agent], root=self.path, explicit_parent=self.continuity_parent
        )
        for _ in CONTINUITY_MODES:
            self.continuity = (self.continuity + delta) % len(CONTINUITY_MODES)
            if capabilities[CONTINUITY_MODES[self.continuity]]["available"]:
                return
        self.error = "No continuity policy is currently materializable"

    def _normalize_continuity_choice(self) -> None:
        capabilities = continuity_policy_capabilities(
            AGENTS[self.agent], root=self.path, explicit_parent=self.continuity_parent
        )
        if capabilities["full-lineage"]["available"]:
            self.continuity = CONTINUITY_MODES.index("full-lineage")
        else:
            self.continuity = CONTINUITY_MODES.index("fresh")

    def handle_mouse(self) -> None:
        try:
            _, x, y, _, state = curses.getmouse()
        except curses.error:
            return
        if not state:
            return
        for row, start, end, index, kind in self.mouse_targets:
            if kind == "home" and y == row and start <= x < end:
                self.home_choice = index
                (self.open_launcher, self.open_voc)[index]()
                return

    def open_launcher(self) -> None:
        script = str(Path(__file__).resolve())
        command = [
            "vc-frame",
            "action",
            "new-pane",
            "--floating",
            "--name",
            "New agent",
            "--width",
            "72%",
            "--height",
            "32%",
            "--cwd",
            self.path,
            "--",
            sys.executable,
            script,
            "launcher",
        ]
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True
            )
        except FileNotFoundError:
            self.error = "vc-frame is not available in this Runtime Pack"
            return
        if result.returncode != 0:
            self.error = (
                result.stderr or result.stdout or "cannot open launcher"
            ).strip()

    def open_voc(self) -> None:
        try:
            result = subprocess.run(
                ["vc-frame", "action", "go-to-tab-name", "voc"],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            self.error = "vc-frame is not available in this Runtime Pack"
            return
        if result.returncode != 0:
            self.error = (
                result.stderr or result.stdout or "voc tab is unavailable"
            ).strip()

    def launch(self) -> None:
        try:
            workspace = normalized_workspace(self.path)
            runtime_name = RUNTIME_POLICIES[self.runtime]
            capability = runtime_policy_capabilities(AGENTS[self.agent])[runtime_name]
            if not capability["available"]:
                raise ValueError(str(capability["reason"]))
            continuity_name = CONTINUITY_MODES[self.continuity]
            continuity_capability = continuity_policy_capabilities(
                AGENTS[self.agent],
                root=workspace,
                explicit_parent=self.continuity_parent,
            )[continuity_name]
            if not continuity_capability["available"]:
                raise ValueError(str(continuity_capability["reason"]))
            argv = launch_argv(
                AGENTS[self.agent],
                LAUNCH_MODES[self.launch_mode],
                runtime_name,
                PERMISSION_POLICIES[self.permissions],
                continuity=continuity_name,
                continuity_parent=self.continuity_parent,
                workspace=workspace,
            )
        except ValueError as exc:
            self.error = str(exc)
            return
        executable = shutil.which(argv[0])
        if executable is None:
            self.error = "vibecrafted launcher is missing from PATH"
            return
        title = (
            f"{AGENTS[self.agent]} · "
            f"{LAUNCH_MODES[self.launch_mode]} · {workspace.name}"
        )
        subprocess.run(
            ["vc-frame", "action", "rename-pane", title],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        curses.endwin()
        os.chdir(workspace)
        os.execvpe(executable, argv, os.environ.copy())


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
