from __future__ import annotations

import curses
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "vibecrafted_core/config/vc-frame/vc-agent-workshop.py"
)


@pytest.fixture
def workshop(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("workshop_project_viewport", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "current_frame_session", lambda **_: "")
    monkeypatch.setattr(module, "git_toplevel", lambda *_: "")
    return module


class Screen:
    def __init__(self, width: int) -> None:
        self.width = width
        self.cells: dict[int, str] = {}
        self.caret: list[tuple[int, str]] = []

    def getmaxyx(self) -> tuple[int, int]:
        return 24, self.width

    def addstr(self, row: int, col: int, text: str, attr: int) -> None:
        assert row == 2
        if attr & curses.A_REVERSE:
            self.caret.append((col, text))
        for char in text:
            self.cells[col] = char
            col += 2 if char in "日本語" else 1

    def text(self) -> str:
        return "".join(self.cells.get(i, " ") for i in range(self.width))


def draw(form, screen: Screen, *, inner: int) -> str:
    screen.cells.clear()
    screen.caret.clear()
    form.mouse_targets.clear()
    form._draw_project_row(2, 1, inner, screen.width)
    return screen.text()


def test_fitting_path_recovers_from_stale_scroll_and_refocus(workshop) -> None:
    screen = Screen(84)
    form = workshop.Workshop(screen, mode="launcher")
    path = "/Volumes/vc-workspace/Loctree/loctree-suite"
    form.path = path
    form.path_scroll = len(path) - 10

    for focus in (False, True, False, True):
        form.row = 1 if focus else 0
        line = draw(form, screen, inner=75)
        assert path in line
        assert form.path_scroll == 0
        assert form.path == path
        if focus:
            assert screen.caret == [(1 + len("Project  ") + len(path), " ")]


def test_fitting_path_uses_room_occupied_by_editing_badge(workshop) -> None:
    screen = Screen(40)
    form = workshop.Workshop(screen, mode="launcher")
    form.row = 1
    form.path = "/project/long-enough-to-fill"
    line = draw(form, screen, inner=37)
    assert form.path in line
    assert form.path_scroll == 0
    assert screen.caret


@pytest.mark.parametrize(
    ("path", "caret_col"),
    [("/" + "x" * 79, 89), ("/" + "x" * 77 + "語", 88)],
)
def test_exact_right_edge_keeps_full_path_and_visible_end_caret(
    workshop, path: str, caret_col: int
) -> None:
    screen = Screen(90)
    form = workshop.Workshop(screen, mode="launcher")
    form.row = 1
    form.path = path
    form.path_scroll = 20

    line = draw(form, screen, inner=89)

    assert path in line
    assert form.path_scroll == 0
    assert screen.caret == [(caret_col, path[-1])]
    assert form.path == path
    assert form.path_cursor == len(path)


def test_opened_result_reports_tab_creation_without_changing_status(workshop) -> None:
    from test_agent_workshop_form import RecordingWindow

    screen = RecordingWindow()
    form = workshop.Workshop(screen, mode="launcher")
    result = {"status": "opened", "provider": "claude", "reason": ""}
    form.launch_results = [result]

    form.draw_launcher()

    assert any("1 claude: tab created" in text for _, _, text, _ in screen.drawn)
    assert not any("claude: opened" in text for _, _, text, _ in screen.drawn)
    assert result["status"] == "opened"


def test_long_path_scrolls_with_caret_and_reflows_after_resize_and_shortening(
    workshop,
) -> None:
    screen = Screen(40)
    form = workshop.Workshop(screen, mode="launcher")
    form.row = 1
    path = "/long/project/path/with/a/checkout/inside"
    form.path = path
    line = draw(form, screen, inner=37)
    assert path not in line
    assert form.path_scroll > 0
    assert screen.caret and screen.caret[0][1] == " "
    assert form.path == path

    form.handle_launcher_key(curses.KEY_HOME)
    line = draw(form, screen, inner=37)
    assert line.startswith("›Project  /long/")
    assert screen.caret == [(1 + len("Project  "), "/")]

    form.handle_launcher_key(curses.KEY_END)
    draw(form, screen, inner=37)
    assert form.path_scroll > 0
    screen.width = 90
    line = draw(form, screen, inner=80)
    assert path in line and form.path_scroll == 0
    assert screen.caret == [(1 + len("Project  ") + len(path), " ")]

    screen.width = 40
    draw(form, screen, inner=37)
    assert form.path_scroll > 0
    for _ in range(25):
        form.handle_launcher_key(curses.KEY_BACKSPACE)
    line = draw(form, screen, inner=37)
    assert form.path in line and form.path_scroll == 0
    assert form.path == path[:-25]


def test_wide_characters_use_terminal_cells_for_caret_and_scrolling(workshop) -> None:
    screen = Screen(36)
    form = workshop.Workshop(screen, mode="launcher")
    form.row = 1
    form.path = "/日本語/abcdefghijklmnopqrstuvwx"
    form.path_cursor = 4
    draw(form, screen, inner=33)
    assert form.path_scroll == 0
    assert screen.caret == [(1 + len("Project  ") + 7, "/")]
    assert form.path == "/日本語/abcdefghijklmnopqrstuvwx"


@pytest.mark.skipif(__import__("os").name != "posix", reason="requires a POSIX PTY")
def test_project_path_reappears_in_real_terminal_after_resize_and_refocus(
    tmp_path: Path,
) -> None:
    import fcntl
    import os
    import pty
    import select
    import signal
    import struct
    import subprocess
    import sys
    import termios
    import time

    project = tmp_path / "long-name-for-workspace-path"
    project.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("VIBECRAFTED_", "VC_FRAME_", "ZELLIJ_"))
        and key not in ("PYTHONPATH", "PYTHONHOME")
    }
    env.update(
        VIBECRAFTED_HOME=str(tmp_path / "vc-home"),
        VIBECRAFTED_WORKSPACE_ROOT=str(project),
        TERM="xterm-256color",
    )
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 40, 0, 0))
    child = subprocess.Popen(
        [sys.executable, str(SCRIPT), "launcher"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        start_new_session=True,
    )
    os.close(slave)

    def read_until(needle: bytes, timeout: float = 10) -> bytes:
        data = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if child.poll() is not None:
                break
            if select.select([master], [], [], 0.1)[0]:
                try:
                    data.extend(os.read(master, 65536))
                except OSError:
                    break
                if needle in data:
                    return bytes(data)
        pytest.fail(f"terminal did not render {needle!r}; exit={child.poll()}")

    try:
        read_until(b"Project")
        os.write(master, b"\x1bOB")  # Down: edit Project.
        read_until(b"[editing]")
        width = max(180, 2 * len(str(project)) + 80)
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, width, 0, 0))
        os.kill(child.pid, signal.SIGWINCH)
        read_until(str(project).encode())

        os.write(master, b"\x1bOA")  # Leave Project.
        time.sleep(0.2)
        os.write(master, b"\x1bOB")  # Enter it again.
        read_until(b"[editing]")
        fcntl.ioctl(
            master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, width + 2, 0, 0)
        )
        os.kill(child.pid, signal.SIGWINCH)
        read_until(str(project).encode())

        # Drain terminal output while quitting so a full PTY buffer cannot
        # prevent curses from completing its final redraw.
        os.write(master, b"\x11")
        time.sleep(0.2)
        os.write(master, b"\x11")
        deadline = time.monotonic() + 5
        while child.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    os.read(master, 65536)
                except OSError:
                    break
        assert child.wait(timeout=2) == 0
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
        os.close(master)
