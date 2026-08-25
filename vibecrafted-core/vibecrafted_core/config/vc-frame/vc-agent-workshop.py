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

from vibecrafted_core.spawn import (
    OPERATOR_POLICIES,
    PERMISSION_POLICIES,
    RUNTIME_POLICIES,
    resolve_operator_agent_policy,
    resolve_provider_policy,
    runtime_policy_capabilities,
)

AGENTS = ("agy", "claude", "codex", "grok", "junie")
# The accepted design leaves operator/partner unresolved.  Do not expose them
# until their CLI contracts can guarantee an interactive TTY on this tab.
RITUALS = ("init", "resume")
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
    ritual: str,
    runtime: str = "local-native",
    permissions: str = "bypass",
    operator: str = "none",
) -> list[str]:
    """Return the one canonical interactive command for a launcher choice."""
    if agent not in AGENTS:
        raise ValueError(f"unsupported agent: {agent}")
    if ritual not in RITUALS:
        raise ValueError(f"unsupported interactive ritual: {ritual}")
    if ritual == "init":
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
        return [
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
        ]
    if runtime != "local-native":
        raise ValueError(
            "worktree resume supervision belongs to H2b2 and is not configured yet"
        )
    return ["vibecrafted", "resume", agent]


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
        window.addstr(row, col, _clip(text, width - col), attr)
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
    for choice, enabled in zip(choices, available, strict=True):
        token = f"[{choice}]" if enabled else f"({choice})"
        if not enabled:
            _safe_addstr(window, row, col, token, curses.A_DIM)
        col += len(token) + 1


class Workshop:
    def __init__(self, window: curses.window, *, mode: str) -> None:
        self.window = window
        self.mode = mode
        self.home_choice = 0
        self.row = 0
        self.agent = 2  # codex is the least surprising neutral default here
        self.ritual = 0
        self.runtime = 1  # safe recommended local default when the provider supports it
        self.permissions = 0
        self.path = str(Path.cwd())
        self.error = ""
        self.mouse_targets: list[tuple[int, int, int, int, str]] = []
        self.last_faces_at = 0.0
        self.faces: list[str] = []

    def configure(self) -> None:
        curses.curs_set(0)
        curses.noecho()
        curses.cbreak()
        self.window.keypad(True)
        self.window.timeout(500)
        self._normalize_runtime_choice()
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
        top = max(1, (height - 12) // 2)
        inner = max(20, card_width - 4)
        _safe_addstr(
            self.window,
            top,
            left,
            "┌ ❯ New agent " + "─" * max(1, card_width - 29) + " [Cancel] ┐",
        )
        agent_line = "  agent    " + " ".join(
            f"«{name}»" if index == self.agent else f"[{name}]"
            for index, name in enumerate(AGENTS)
        )
        ritual_line = "  ritual   " + " ".join(
            f"«{name}»" if index == self.ritual else f"[{name}]"
            for index, name in enumerate(RITUALS)
        )
        provider = AGENTS[self.agent]
        capabilities = runtime_policy_capabilities(provider)
        runtime_line = "  runtime  " + " ".join(
            (f"«{name}»" if index == self.runtime else f"[{name}]")
            if capabilities[name]["available"]
            else f"({name})"
            for index, name in enumerate(RUNTIME_POLICIES)
        )
        permission_line = "  permits  " + " ".join(
            (f"«{name}»" if index == self.permissions else f"[{name}]")
            if resolve_provider_policy(
                provider, RUNTIME_POLICIES[self.runtime], name, "interactive"
            ).supported
            else f"({name})"
            for index, name in enumerate(PERMISSION_POLICIES)
        )
        rows = (
            agent_line,
            ritual_line,
            runtime_line,
            permission_line,
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
            top + 3,
            left + 2 + len("  runtime  "),
            RUNTIME_POLICIES,
            tuple(bool(capabilities[name]["available"]) for name in RUNTIME_POLICIES),
        )
        _dim_unavailable_choices(
            self.window,
            top + 4,
            left + 2 + len("  permits  "),
            PERMISSION_POLICIES,
            tuple(
                resolve_provider_policy(
                    provider,
                    RUNTIME_POLICIES[self.runtime],
                    name,
                    "interactive",
                ).supported
                for name in PERMISSION_POLICIES
            ),
        )
        runtime_help = RUNTIME_HELP[RUNTIME_POLICIES[self.runtime]]
        _safe_addstr(
            self.window,
            top + 6,
            left,
            ("│ " + _clip(runtime_help[0], inner)).ljust(card_width - 1) + "│",
            curses.A_DIM,
        )
        _safe_addstr(
            self.window,
            top + 7,
            left,
            ("│ " + _clip(runtime_help[1], inner)).ljust(card_width - 1) + "│",
            curses.A_DIM,
        )
        _safe_addstr(
            self.window,
            top + 8,
            left,
            "│ Enter = interactive TTY on this Agents tab".ljust(card_width - 1) + "│",
            curses.A_DIM,
        )
        _safe_addstr(
            self.window,
            top + 9,
            left,
            "└─ ↑/↓ row · ←/→ choice · type path · Enter launch · Esc cancel "
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
            top + 10,
            left,
            "Unavailable — " + " · ".join(unavailable),
            curses.A_DIM,
        )
        if self.error:
            _safe_addstr(
                self.window, min(height - 1, top + 11), left, self.error, curses.A_BOLD
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
            self.row = (self.row - 1) % 5
            return
        if key in (curses.KEY_DOWN, ord("\t")):
            self.row = (self.row + 1) % 5
            return
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord(" ")):
            delta = -1 if key == curses.KEY_LEFT else 1
            if self.row == 0:
                self.agent = (self.agent + delta) % len(AGENTS)
                self._normalize_runtime_choice()
                self._normalize_permission_choice()
            elif self.row == 1:
                self.ritual = (self.ritual + delta) % len(RITUALS)
            elif self.row == 2:
                self._cycle_runtime(delta)
            elif self.row == 3:
                self._cycle_permissions(delta)
            return
        if key in (10, 13, curses.KEY_ENTER):
            self.launch()
            return
        if self.row == 4:
            if key in (curses.KEY_BACKSPACE, 127, 8):
                self.path = self.path[:-1]
            elif 32 <= key <= 126:
                self.path += chr(key)

    def _cycle_runtime(self, delta: int) -> None:
        capabilities = runtime_policy_capabilities(AGENTS[self.agent])
        for _ in RUNTIME_POLICIES:
            self.runtime = (self.runtime + delta) % len(RUNTIME_POLICIES)
            name = RUNTIME_POLICIES[self.runtime]
            if capabilities[name]["available"]:
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
            argv = launch_argv(
                AGENTS[self.agent],
                RITUALS[self.ritual],
                runtime_name,
                PERMISSION_POLICIES[self.permissions],
            )
        except ValueError as exc:
            self.error = str(exc)
            return
        executable = shutil.which(argv[0])
        if executable is None:
            self.error = "vibecrafted launcher is missing from PATH"
            return
        title = f"{AGENTS[self.agent]} · {RITUALS[self.ritual]} · {workspace.name}"
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
