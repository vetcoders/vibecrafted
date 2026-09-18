"""PTY admission verifier for W1-02 shared Home console."""

from __future__ import annotations

import json
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TUI = REPO / "vibecrafted-app" / "tui-agent"
TARGET = TUI / "target"
ANSI = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*\x07)")
NAV_BUDGET_S = 0.250


def _collapsed(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _has(text: str, needle: str) -> bool:
    return needle in text or needle.replace(" ", "") in _collapsed(text)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _run_json(
    run_id: str,
    agent: str,
    state: str,
    root: str,
    panel: str | None,
    extra: dict | None = None,
) -> dict:
    now = _now()
    payload = {
        "run_id": run_id,
        "agent": agent,
        "skill": "implement",
        "state": state,
        "started_at": now,
        "updated_at": now,
        "last_heartbeat": now,
        "root": root,
        "latest_transcript": f"{root}/{run_id}.log",
    }
    if panel:
        payload["operator_session"] = panel
        payload["panel"] = panel
    if extra:
        payload.update(extra)
    return payload


def _write_fixtures(state_root: Path, alpha: Path, beta: Path) -> None:
    runs = state_root / "runs"
    runs.mkdir(parents=True)
    alpha.mkdir(parents=True, exist_ok=True)
    beta.mkdir(parents=True, exist_ok=True)
    (alpha / "ask-1.log").write_text(
        "operator asked a question that still needs an answer\n"
    )
    (alpha / "work-1.log").write_text(
        "claude is implementing the shared home console without wrapping mid-word\n"
    )
    snapshots = [
        _run_json(
            "ask-1",
            "kimi",
            "waiting",
            str(alpha),
            "pane-1",
            {"attention_reason": "waiting on operator", "needs_attention": True},
        ),
        _run_json("work-1", "claude", "running", str(alpha), "pane-2", None),
        _run_json(
            "hist-1",
            "cursor",
            "completed",
            str(alpha),
            "pane-old",
            {"exit_code": 0, "liveness": "terminal"},
        ),
        _run_json(
            "ask-beta", "grok", "unknown", str(beta), None, {"needs_attention": True}
        ),
        _run_json(
            "headless-1", "codex", "running", str(alpha), None, {"mode": "headless"}
        ),
    ]
    for row in snapshots:
        (runs / f"{row['run_id']}.json").write_text(json.dumps(row, indent=2))


def _write_deck(path: Path, launch_log: Path, capabilities: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{launch_log}"\n'
        'case " $* " in\n'
        f"  *' capabilities '*) cat \"{capabilities}\" ;;\n"
        "  *' dashboard attach '*|*' workflow '*|*' implement '*|*' resume '*)\n"
        f'    printf "%s\\n" "LAUNCH $*" >> "{launch_log}.launches"; exit 0 ;;\n'
        "esac\n"
    )
    path.chmod(0o755)


def _build_voc() -> Path:
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(TARGET)
    subprocess.run(
        ["cargo", "build", "--manifest-path", str(TUI / "Cargo.toml"), "--bin", "voc"],
        check=True,
        env=env,
        cwd=REPO,
    )
    binary = TARGET / "debug" / "voc"
    assert binary.is_file(), binary
    return binary


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    import fcntl

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _strip(raw: bytes) -> str:
    return ANSI.sub("", raw.decode("utf-8", "replace"))


class VocPty:
    def __init__(self, binary: Path, env: dict[str, str], rows: int, cols: int) -> None:
        self.buf = bytearray()
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(env["HOME"])
            os.execve(
                str(binary),
                [
                    str(binary),
                    "--view",
                    "home",
                    "--state-root",
                    env["VOC_STATE_ROOT"],
                    "--repo",
                    env["VOC_REPO"],
                    "--deck",
                    env["VOC_DECK"],
                    "--tick-ms",
                    "50",
                    "--no-verify-gate",
                ],
                env,
            )
        _set_winsize(self.fd, rows, cols)
        os.kill(self.pid, signal.SIGWINCH)

    def drain(self, seconds: float = 0.4) -> str:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.buf.extend(chunk)
        return _strip(bytes(self.buf))

    def send(self, data: bytes) -> None:
        os.write(self.fd, data)

    def resize(self, rows: int, cols: int) -> None:
        _set_winsize(self.fd, rows, cols)
        os.kill(self.pid, signal.SIGWINCH)

    def close(self) -> None:
        try:
            os.write(self.fd, b"q")
        except OSError:
            pass
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


@pytest.fixture(scope="module")
def voc_binary() -> Path:
    return _build_voc()


def _prepare(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "control_plane"
    alpha = tmp_path / "ws-alpha"
    beta = tmp_path / "ws-beta"
    _write_fixtures(state, alpha, beta)
    deck = tmp_path / "deck.sh"
    launch_log = tmp_path / "deck.log"
    _write_deck(deck, launch_log, TUI / "tests" / "fixtures" / "capabilities.json")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "TERM": "xterm-256color",
            "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
            "VOC_STATE_ROOT": str(state),
            "VOC_REPO": str(alpha),
            "VOC_DECK": str(deck),
            "VOC_LAUNCH_LOG": str(launch_log),
            "VOC_SOURCE_DELAY_MS": "2000",
        }
    )
    for name in ("VIBECRAFTED_RUNTIME_HOME", "VIBECRAFTED_RUNTIME_ROOT", "PYTHONPATH"):
        env.pop(name, None)
    return env


def test_command_bridge_home_console_pty(voc_binary: Path, tmp_path: Path) -> None:
    env = _prepare(tmp_path)
    launch_side = Path(str(Path(env["VOC_LAUNCH_LOG"])) + ".launches")
    session = VocPty(voc_binary, env, 24, 80)
    try:
        first = session.drain(2.0)
        assert "Needs attention" in first, first
        assert "In progress" in first, first
        assert "History" in first, first
        assert "[Global]" in first, first
        assert "waiting on operator" in first, first
        assert "cost 0" not in first.replace("cost —", ""), first

        session.send(b"f")
        scoped = session.drain(0.6)
        assert "[Local]" in scoped, scoped
        session.send(b"f")
        session.drain(0.4)

        # Global sort: ask-beta, ask-1, work-1 (pane-2), headless-1, hist-1.
        session.send(b"jj")
        session.drain(0.2)
        session.send(b"r")
        started = time.monotonic()
        session.send(b"\r")
        elapsed = None
        screen = ""
        for _ in range(20):
            screen = session.drain(0.05)
            if "navigate existing panel" in screen or "Conversation" in screen:
                elapsed = time.monotonic() - started
                break
        assert elapsed is not None, session.drain(0.2)
        assert elapsed <= NAV_BUDGET_S, f"home navigation took {elapsed:.3f}s"
        assert "no launch" in screen, screen
        assert not launch_side.exists()

        session.send(b"\x1b")
        home_again = session.drain(0.6)
        assert "returned to Home" in home_again or "Needs attention" in home_again, (
            home_again
        )

        session.resize(20, 40)
        compact = session.drain(0.8)
        assert _has(compact, "Needs attention"), compact
        assert _has(compact, "History"), compact
        session.send(b"\r")
        compact_open = session.drain(0.6)
        assert _has(compact_open, "Conversation") or _has(
            compact_open, "navigate existing panel"
        ), compact_open
        assert _has(compact_open, "implementing the shared") or _has(
            compact_open, "without wrapping"
        ), compact_open
        session.send(b"H")
        assert _has(session.drain(0.6), "Needs attention")

        session.resize(24, 80)
        session.drain(0.4)
        session.send(b"j\r")
        missing = session.drain(0.8)
        assert _has(missing, "no existing panel") or _has(missing, "not launching"), (
            missing
        )
        assert not launch_side.exists()
    finally:
        session.close()
