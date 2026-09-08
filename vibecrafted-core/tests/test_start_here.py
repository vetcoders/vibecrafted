from __future__ import annotations

import curses
import importlib.util
import os
import stat
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

from vibecrafted_core.vc_frame_staging import materialize_vc_frame_config

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "vibecrafted_core"
    / "config"
    / "vc-frame"
    / "vc-start-here.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vc_start_here", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_here_is_shipped_and_keeps_executable_mode(tmp_path: Path) -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111

    destination = tmp_path / "vc-frame"
    materialize_vc_frame_config(
        SCRIPT.parent,
        destination,
        pane_shell="bash",
        clipboard_command=None,
    )

    installed = destination / SCRIPT.name
    assert installed.is_file()
    assert installed.stat().st_mode & 0o111
    pane_python = destination / "pane-python"
    assert pane_python.is_file()
    assert pane_python.stat().st_mode & 0o111


def test_pane_python_reexecs_generation_interpreter(tmp_path: Path) -> None:
    runner = (
        Path(__file__).resolve().parents[1]
        / "vibecrafted_core"
        / "config"
        / "vc-frame"
        / "pane-python"
    )
    log = tmp_path / "ran.log"
    stub = tmp_path / "generation-python"
    stub.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$0" "$@" > "{log}"\nexit 0\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    script = tmp_path / "vc-start-here.py"
    script.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    env = os.environ.copy()
    env["VIBECRAFTED_PYTHON"] = str(stub)
    result = subprocess.run(
        ["bash", str(runner), str(script), "home"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    recorded = log.read_text(encoding="utf-8")
    assert str(stub) in recorded
    assert str(script) in recorded
    assert "home" in recorded


def test_pane_python_reexecs_uv_tools_python_when_env_unset(tmp_path: Path) -> None:
    runner = (
        Path(__file__).resolve().parents[1]
        / "vibecrafted_core"
        / "config"
        / "vc-frame"
        / "pane-python"
    )
    log = tmp_path / "ran.log"
    uv_bin = tmp_path / "data" / "uv" / "tools" / "vibecrafted" / "bin"
    uv_bin.mkdir(parents=True)
    stub = uv_bin / "python"
    stub.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$0" "$@" > "{log}"\nexit 0\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    script = tmp_path / "vc-start-here.py"
    script.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    env = os.environ.copy()
    env.pop("VIBECRAFTED_PYTHON", None)
    env.pop("VIBECRAFTED_RUNTIME_ROOT", None)
    env.pop("VIBECRAFTED_ROOT", None)
    env["HOME"] = str(tmp_path)
    env["XDG_DATA_HOME"] = str(tmp_path / "data")
    env["PATH"] = "/usr/bin:/bin"
    result = subprocess.run(
        ["bash", str(runner), str(script), "home"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    recorded = log.read_text(encoding="utf-8")
    assert str(stub) in recorded
    assert str(script) in recorded
    assert "home" in recorded


def test_start_here_routes_to_existing_product_owners() -> None:
    start_here = _load()

    assert start_here.action_argv("agents") == [
        "vc-frame",
        "action",
        "go-to-tab-name",
        "Agents",
    ]
    assert start_here.action_argv("shell") == [
        "vc-frame",
        "action",
        "go-to-tab-name",
        "Shell",
    ]
    assert start_here.action_argv("console") == [
        "/usr/bin/open",
        "vibecrafted://console/open",
    ]
    assert start_here.action_argv("help")[:5] == [
        "vc-frame",
        "action",
        "new-pane",
        "--floating",
        "--name",
    ]


def test_start_here_readiness_is_truthful_and_actionable() -> None:
    start_here = _load()
    healthy = {
        "installed": True,
        "loaded": True,
        "supervisor_live": True,
        "supervisor_verified": True,
        "supervisor_service_managed": True,
        "build_current": True,
        "pair_healthy": True,
    }

    assert start_here.readiness_from_service_payload(healthy) == (
        "ready",
        "VC Server is healthy — this workspace is ready",
    )
    assert start_here.readiness_from_service_payload(
        {"installed": True, "loaded": False}
    ) == (
        "stopped",
        "VC Server is stopped — use the Vibecrafted menu bar to start it",
    )
    assert start_here.readiness_from_service_payload(None, deck_available=False) == (
        "missing",
        "Vibecrafted launcher is missing — reinstall the Runtime Pack",
    )


def test_start_here_does_not_leak_parent_launcher_claim_to_status(
    monkeypatch,
) -> None:
    start_here = _load()
    healthy = {
        "installed": True,
        "loaded": True,
        "supervisor_live": True,
        "supervisor_verified": True,
        "supervisor_service_managed": True,
        "build_current": True,
        "pair_healthy": True,
    }
    observed_environment = None

    monkeypatch.setenv("VIBECRAFTED_DECLARED_LAUNCHER", "/managed/bin/vc-start")
    monkeypatch.setattr(start_here.shutil, "which", lambda name: f"/managed/bin/{name}")

    def fake_run(argv, **kwargs):
        nonlocal observed_environment
        observed_environment = kwargs["env"]
        return SimpleNamespace(stdout=__import__("json").dumps(healthy))

    monkeypatch.setattr(start_here.subprocess, "run", fake_run)

    assert start_here.probe_readiness() == (
        "ready",
        "VC Server is healthy — this workspace is ready",
    )
    assert observed_environment is not None
    assert "VIBECRAFTED_DECLARED_LAUNCHER" not in observed_environment


def test_start_here_mouse_targets_the_same_actions_as_keyboard() -> None:
    start_here = _load()
    targets = [(10, 4, 28, "agents"), (13, 4, 28, "shell")]

    assert start_here.action_for_mouse_row(10, targets, 9) == "agents"
    assert start_here.action_for_mouse_row(13, targets, 28) == "shell"
    assert start_here.action_for_mouse_row(12, targets, 9) is None


# ── Theme and responsive layout ───────────────────────────────────────────────

READY = ("ready", "VC Server is healthy — this workspace is ready")


def _rows_text(rows) -> str:
    return "\n".join(row.text for row in rows)


def _action_rows(rows):
    return [row for row in rows if row.action is not None]


def test_layout_wraps_product_line_inside_reading_width_at_80x24() -> None:
    start_here = _load()
    rows = start_here.layout_rows(24, 80, selected=0, readiness=READY)
    left, canvas = start_here.canvas_geometry(80)

    assert rows[-1].row < 24
    assert max(row.col + len(row.text) for row in rows) <= left + canvas
    prose = [row.text for row in rows if row.action is None]
    assert " ".join(prose).count(start_here.PRODUCT_LINE) == 1
    assert "RUNTIME [ready] VC Server is healthy — this workspace is ready" in prose
    assert "…" not in _rows_text(rows)
    assert {row.action for row in _action_rows(rows)} == {
        "agents",
        "shell",
        "console",
        "help",
    }
    assert start_here.HELP_LINE in prose


def test_layout_is_left_anchored_and_bounded_on_wide_panes() -> None:
    start_here = _load()
    rows = start_here.layout_rows(50, 220, selected=0, readiness=READY)
    left, canvas = start_here.canvas_geometry(220)

    assert left == 4
    assert canvas == start_here.READABLE_WIDTH
    assert min(row.col for row in rows) == left
    assert max(row.col + len(row.text) for row in rows) <= left + canvas


def test_layout_keeps_every_action_and_help_visible_in_compact_panes() -> None:
    start_here = _load()
    # 40x12 is the smallest pane this layout promises to serve completely.
    for height, width in ((16, 50), (12, 40), (24, 60), (20, 44)):
        rows = start_here.layout_rows(height, width, selected=2, readiness=READY)
        left, canvas = start_here.canvas_geometry(width)
        text = _rows_text(rows)
        assert max(row.col + len(row.text) for row in rows) <= left + canvas, (
            height,
            width,
        )
        for index, (title, _, action) in enumerate(start_here.ACTIONS):
            titled = [
                row
                for row in rows
                if row.action == action and f"[{index + 1}] {title}" in row.text
            ]
            assert titled and titled[0].row < height, (height, width, title)
        assert "Enter open" in text and "q close" in text
        help_rows = [row for row in rows if "q close" in row.text]
        assert help_rows[0].row < height, (height, width)
        assert "RUNTIME [ready]" in text


def test_layout_never_relies_on_dim_or_colour_for_meaning() -> None:
    start_here = _load()
    for readiness in (
        READY,
        ("attention", "VC Server needs attention — open Help & diagnostics"),
    ):
        rows = start_here.layout_rows(24, 80, selected=3, readiness=readiness)
        assert all(not row.attr & curses.A_DIM for row in rows)
        assert all(not row.attr & curses.A_COLOR for row in rows)
        assert f"RUNTIME [{readiness[0]}]" in _rows_text(rows)
        selected = [row for row in rows if row.action == "help" and "▶" in row.text]
        assert len(selected) == 1
        assert selected[0].attr & curses.A_REVERSE
        others = [
            row for row in rows if row.action not in (None, "help") and "[" in row.text
        ]
        assert others and all(not row.attr & curses.A_REVERSE for row in others)


def test_mouse_targets_follow_wrapped_rows_and_resize() -> None:
    start_here = _load()
    wide = start_here.layout_rows(24, 80, selected=0, readiness=READY)
    wide_targets = start_here.mouse_targets(wide, 80)
    assert {row for row, *_ in wide_targets} == {row.row for row in _action_rows(wide)}
    detail = next(
        row
        for row in wide
        if row.action == "shell" and "▶" not in row.text and "[" not in row.text
    )
    assert (
        start_here.action_for_mouse_row(detail.row, wide_targets, detail.col + 3)
        == "shell"
    )

    narrow = start_here.layout_rows(16, 50, selected=0, readiness=READY)
    narrow_targets = start_here.mouse_targets(narrow, 50)
    console_rows = [row.row for row in narrow if row.action == "console"]
    assert len(console_rows) == 2, (
        "console detail wraps onto a second clickable row at 50 columns"
    )
    for row in console_rows:
        assert start_here.action_for_mouse_row(row, narrow_targets, 10) == "console"
    assert narrow_targets != wide_targets


def _run_in_pty(script: Path, cols: int, rows: int, home: Path) -> bytes:
    import fcntl
    import pty
    import select
    import struct
    import sys
    import termios
    import time

    stubs = home / "bin"
    stubs.mkdir(parents=True)
    healthy = {
        "installed": True,
        "loaded": True,
        "supervisor_live": True,
        "supervisor_verified": True,
        "supervisor_service_managed": True,
        "build_current": True,
        "pair_healthy": True,
    }
    payload = __import__("json").dumps(healthy)
    (stubs / "vibecrafted").write_text(
        f"#!/bin/sh\nprintf '%s' '{payload}'\n", encoding="utf-8"
    )
    (stubs / "vc-frame").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for stub in (stubs / "vibecrafted", stubs / "vc-frame"):
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "TERM": "xterm-256color",
        "HOME": str(home),
        "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
    }
    env["LINES"] = str(rows)
    env["COLUMNS"] = str(cols)
    pid, master = pty.fork()
    if pid == 0:  # child: size the controlling terminal, then become the pane
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.execve(sys.executable, [sys.executable, str(script)], env)
    captured = b""
    quit_sent = False
    deadline = time.monotonic() + 15
    status = None
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.25)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                captured += chunk
            elif captured and not quit_sent:
                os.write(master, b"q")
                quit_sent = True
            else:
                finished, status = os.waitpid(pid, os.WNOHANG)
                if finished:
                    break
        if status is None:
            _, status = os.waitpid(pid, 0)
    finally:
        os.close(master)
    assert os.waitstatus_to_exitcode(status) == 0, captured[-400:]
    return captured


def _sgr_params(output: bytes) -> set[str]:
    import re

    params: set[str] = set()
    for sequence in re.findall(rb"\x1b\[([0-9;]*)m", output):
        params.update(sequence.decode("ascii").split(";") if sequence else {"0"})
    return params


def test_start_here_draws_with_terminal_default_colours_in_a_real_pty(
    tmp_path: Path,
) -> None:
    """Source-binary contract: the pane inherits the host palette live.

    ``vc-theme`` (the tab-bar switcher) republishes the terminal palette; the
    pane must therefore emit only the default colour pair (SGR 39/49) and
    never palette black/white or dim, otherwise light and moon modes both show
    the opaque purple block the founder rejected.
    """
    output = _run_in_pty(SCRIPT, 80, 24, tmp_path)
    params = _sgr_params(output)

    assert b"\x1b[39;49m" in output
    forbidden = {
        "2",
        "38",
        "48",
        *(str(n) for n in range(30, 38)),
        *(str(n) for n in range(40, 48)),
    }
    assert not params & forbidden, sorted(params)
    assert {"1", "7"} <= params, sorted(params)
    text = output.decode("utf-8", "replace")
    assert "visible proof." in text
    assert "RUNTIME [ready] VC Server is healthy — this workspace is ready" in text
    assert "q close" in text


def test_start_here_wraps_into_a_narrow_real_pty_without_clipping(
    tmp_path: Path,
) -> None:
    output = _run_in_pty(SCRIPT, 50, 16, tmp_path)
    text = output.decode("utf-8", "replace")

    assert "…" not in text
    for title in ("Agent Workspaces", "Shell", "VC Console", "Help & diagnostics"):
        assert title in text
    assert "q close" in text
    assert not _sgr_params(output) & {"2", "37", "40"}
