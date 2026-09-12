#!/usr/bin/env python3
"""Actionable first screen for the shipped Vibecrafted workspace.

This pane is a view and launcher only. vc-frame owns navigation, the native app
owns VC Console, and the existing Vibecrafted deck owns diagnostics and server
truth.
"""

from __future__ import annotations

import curses
import json
import os
import shutil
import subprocess
from typing import Any

PRODUCT_LINE = (
    "Vibecrafted is a workspace where you start and coordinate AI Agents "
    "that do real work with runtime continuity and visible proof."
)

ACTIONS = (
    ("Agent Workspaces", "Start or resume an Agent in the current workspace", "agents"),
    ("Shell", "Open the installed work shell", "shell"),
    ("VC Console", "Open native run status and reports", "console"),
    ("Help & diagnostics", "Check this installed runtime and its owner", "help"),
)


def action_argv(action: str) -> list[str]:
    """Return the existing product owner command for a Start Here action."""
    if action == "agents":
        return ["vc-frame", "action", "go-to-tab-name", "Agents"]
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
    raise ValueError(f"unknown Start Here action: {action}")


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


class StartHere:
    def __init__(self, window: curses.window) -> None:
        self.window = window
        self.selected = 0
        self.readiness = probe_readiness()
        self.error = ""
        self.targets: list[tuple[int, int, int, str]] = []

    def configure(self) -> None:
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
                return
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
        self.targets.clear()
        height, width = self.window.getmaxyx()
        canvas = min(82, max(40, width - 4))
        left = max(2, (width - canvas) // 2)
        top = max(1, min(5, (height - 24) // 2))
        _put(self.window, top, left, "START HERE", curses.A_BOLD)
        _put(self.window, top + 2, left, PRODUCT_LINE)
        state, message = self.readiness
        readiness_attr = curses.A_BOLD if state == "ready" else curses.A_DIM
        _put(self.window, top + 5, left, f"RUNTIME · {message}", readiness_attr)
        _put(self.window, top + 7, left, "Choose where to begin:", curses.A_BOLD)
        first = top + 9
        for index, (title, detail, action) in enumerate(ACTIONS):
            row = first + index * 3
            marker = "▶" if index == self.selected else " "
            label = f"{marker} [{index + 1}] {title}"
            attr = (
                curses.A_REVERSE | curses.A_BOLD
                if index == self.selected
                else curses.A_BOLD
            )
            _put(self.window, row, left, label, attr)
            _put(self.window, row + 1, left + 6, detail, curses.A_DIM)
            self.targets.append((row, left, left + len(label), action))
            self.targets.append((row + 1, left, left + canvas, action))
        footer = first + len(ACTIONS) * 3 + 1
        _put(
            self.window,
            footer,
            left,
            "↑↓ / j k select   Enter open   click open   r refresh   q close",
            curses.A_DIM,
        )
        if self.error:
            _put(self.window, footer + 2, left, self.error, curses.A_BOLD)
        self.window.refresh()

    def activate(self, action: str) -> None:
        try:
            result = subprocess.run(
                action_argv(action),
                check=False,
                capture_output=True,
                text=True,
                timeout=8.0,
            )
            if result.returncode != 0:
                self.error = (result.stderr or result.stdout).strip() or (
                    f"{action} exited {result.returncode}"
                )
            else:
                self.error = ""
        except (OSError, subprocess.TimeoutExpired) as error:
            self.error = f"Could not open {action}: {error}"

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
    raise SystemExit(main())
