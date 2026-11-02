"""Delivery probes for the shipped pane commands, with no billable providers.

Frame navigation is a recorded double; the materialized pane, interpreter,
terminal input, curses renderer and workspace catalog are real.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import pty
import select
import shlex
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

import pytest
from vibecrafted_core.vc_frame_staging import materialize_vc_frame_config

CONFIG = Path(__file__).resolve().parents[1] / "vibecrafted_core/config/vc-frame"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, CONFIG / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pane_command(config: Path, script: str) -> str:
    # Decode the actual KDL string, rather than reconstructing its shell route.
    for line in (config / "layouts/operator.kdl").read_text().splitlines():
        if 'args "-lc"' in line and f"/{script}" in line:
            return json.loads(line.split('args "-lc" ', 1)[1])
    raise AssertionError(f"no shipped pane command for {script}")


class Terminal:
    def __init__(self, command: str, env: dict[str, str], cwd: Path):
        self.master, slave = pty.openpty()
        self.initial_mode = termios.tcgetattr(slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 160, 0, 0))

        self.process = subprocess.Popen(
            ["bash", "-c", command],
            env=env,
            cwd=cwd,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
        )
        os.close(slave)
        self.output = b""

    def expect(self, text: str, *, timeout: float = 12) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if text.encode() in self.output:
                return
            if select.select([self.master], [], [], 0.1)[0]:
                try:
                    self.output += os.read(self.master, 65536)
                except OSError:
                    break
        raise AssertionError(
            f"missing {text!r}: {self.output.decode(errors='replace')}"
        )

    def send(self, keys: bytes) -> None:
        self.output = b""
        os.write(self.master, keys)

    def finish(self) -> int:
        # Keep the terminal consumer alive through tcdrain/endwin. Waiting
        # without reading can block macOS PTY shutdown even after q/Esc.
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return self.process.returncode
            if select.select([self.master], [], [], 0.1)[0]:
                try:
                    self.output += os.read(self.master, 65536)
                except OSError:
                    pass
        raise AssertionError(
            f"pane did not close after terminal input: {self.output.decode(errors='replace')}"
        )

    def close(self) -> None:
        os.close(self.master)
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


@pytest.fixture
def pane_runtime(tmp_path: Path):
    config = tmp_path / "frame-config"
    materialize_vc_frame_config(
        CONFIG, config, pane_shell="bash", clipboard_command=None
    )
    runtime = tmp_path / "generation"
    binary = runtime / "bin"
    binary.mkdir(parents=True)
    # Real selected interpreter and core; only provider and Frame IPC are doubles.
    python = binary / "python3"
    python.write_text(
        "#!/bin/sh\nexport PYTHONPATH="
        + shlex.quote(str(CONFIG.parents[2]))
        + "\nexec "
        + shlex.quote(sys.executable)
        + ' "$@"\n'
    )
    python.chmod(0o755)
    navigation = tmp_path / "navigation.json"
    frame = binary / "vc-frame"
    frame.write_text(
        f"#!{sys.executable}\nimport json,sys,pathlib\n"
        f"pathlib.Path({str(navigation)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    frame.chmod(0o755)
    deck = binary / "vibecrafted"
    deck.write_text("#!/bin/sh\nprintf '{\"installed\":false}\\n'\n")
    deck.chmod(0o755)
    for name in ("codex", "kimi"):
        provider = binary / name
        provider.write_text("#!/bin/sh\nexit 93\n")
        provider.chmod(0o755)
    short_project = tempfile.TemporaryDirectory(prefix="vcfw-project-", dir="/tmp")
    project = Path(short_project.name).resolve()
    env = dict(os.environ)
    env.update(
        TERM="xterm-256color",
        PATH=f"{binary}:/usr/bin:/bin",
        VC_FRAME_CONFIG_DIR=str(config),
        VIBECRAFTED_PYTHON=str(python),
        VIBECRAFTED_WORKSPACE_ROOT=str(project),
        VC_FRAME_SESSION_NAME="project-seat",
    )
    # This source rehearsal uses the source-owned interpreter, not an installed
    # generation identity. A generation-skew probe is separate below.
    env.pop("VIBECRAFTED_RUNTIME_ROOT", None)
    try:
        yield config, env, project, navigation
    finally:
        short_project.cleanup()


@pytest.mark.parametrize("entry", ["layout", "start-here"])
def test_agents_entry_opens_interactive_launcher(
    pane_runtime, tmp_path: Path, entry: str
):
    config, env, project, navigation = pane_runtime
    if entry == "start-here":
        start = Terminal(_pane_command(config, "vc-start-here.py"), env, tmp_path)
        try:
            start.expect("Agents")
            start.send(b"2")
            deadline = time.monotonic() + 5
            while not navigation.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert json.loads(navigation.read_text()) == [
                "--session",
                "project-seat",
                "action",
                "go-to-tab-name",
                "Agents",
            ]
            start.send(b"q")
            assert start.finish() == 0
        finally:
            start.close()
    terminal = Terminal(_pane_command(config, "vc-agent-workshop.py"), env, tmp_path)
    try:
        terminal.expect("Choose a provider, then Launch.")
        terminal.expect(str(project))
        # The PTY starts with XON/XOFF enabled, as a normal terminal does.
        assert terminal.initial_mode[0] & termios.IXON
        # Ctrl+Q must reach curses without disabling the existing Ctrl+C signal.
        mode = termios.tcgetattr(terminal.master)
        assert not mode[0] & termios.IXON
        assert mode[3] & termios.ISIG
        terminal.send(b"a")
        terminal.expect("Runtime")
        terminal.send(b"a")
        terminal.expect("▸")
        # ncurses requests X10 mouse input at the shipped Advanced target.
        terminal.send(b"\x1b[M\x20\x48\x2e\x1b[M\x23\x48\x2e")
        terminal.expect("Runtime")
        terminal.send(b"\x11")
        terminal.expect("Unsaved form changes")
        terminal.send(b"y")
        assert terminal.finish() == 0
        assert termios.tcgetattr(terminal.master)[0] & termios.IXON
    finally:
        terminal.close()


@pytest.mark.parametrize("script", ["vc-agent-workshop.py", "vc-start-here.py"])
@pytest.mark.parametrize("host_python", [sys.executable, "/usr/bin/python3"])
def test_startup_pins_generation_python(tmp_path: Path, script: str, host_python: str):
    materialized = tmp_path / script
    materialized.write_text((CONFIG / script).read_text())
    stale = tmp_path / "stale"
    package = stale / "vibecrafted_core"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# importable, wrong generation\n")
    runtime = tmp_path / "selected-generation"
    binary = runtime / "bin"
    binary.mkdir(parents=True)
    receipt = tmp_path / "python.json"
    python = binary / "python3"
    python.write_text(
        f"#!{sys.executable}\nimport json,sys,pathlib\n"
        f"pathlib.Path({str(receipt)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    python.chmod(0o755)
    env = dict(os.environ, PYTHONPATH=str(stale), VIBECRAFTED_RUNTIME_ROOT=str(runtime))
    env.pop("VIBECRAFTED_PYTHON", None)
    result = subprocess.run(
        [host_python, str(materialized), "--help"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(receipt.read_text()) == [str(materialized), "--help"]


@pytest.mark.parametrize("script", ["vc-agent-workshop.py", "vc-start-here.py"])
def test_generation_skew_has_bounded_repair(tmp_path: Path, script: str):
    materialized = tmp_path / script
    materialized.write_text((CONFIG / script).read_text())
    runtime = tmp_path / "broken-generation"
    binary = runtime / "bin"
    binary.mkdir(parents=True)
    stale = tmp_path / "stale"
    package = stale / "vibecrafted_core"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    python = binary / "python3"
    python.write_text(
        "#!/bin/sh\nexport PYTHONPATH="
        + shlex.quote(str(stale))
        + "\nexec "
        + shlex.quote(sys.executable)
        + ' "$@"\n'
    )
    python.chmod(0o755)
    env = dict(os.environ, PYTHONPATH=str(stale), VIBECRAFTED_RUNTIME_ROOT=str(runtime))
    env.pop("VIBECRAFTED_PYTHON", None)
    result = subprocess.run(
        [sys.executable, str(materialized)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "Repair:" in result.stderr and "vc-start resume --repo" in result.stderr
    assert "Traceback" not in result.stderr


def test_terminal_probe_rejects_home_regression(pane_runtime, tmp_path: Path):
    config, env, _project, _navigation = pane_runtime
    command = _pane_command(config, "vc-agent-workshop.py").replace(
        '"$launcher" launcher', '"$launcher" home'
    )
    terminal = Terminal(command, env, tmp_path)
    try:
        terminal.expect("Agents in this session")
        with pytest.raises(AssertionError, match="missing"):
            terminal.expect("Choose a provider, then Launch.", timeout=0.3)
    finally:
        terminal.close()
