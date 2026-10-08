"""The promised fallback shell survives a failed workspace entry (F9).

The 2026-09-28 Silver report: when ``vc-start`` exits non-zero, the old entry
script ran the product command through an *interactive* intermediate zsh
(``-lic``); after that child exited, the final ``exec /bin/zsh -l`` was left in
an orphaned foreground group and died on ``can't set tty pgrp: Input/output
error`` — the terminal window closed with no message. The probes only fail
with the script as the pty session leader, so this test reproduces the
alacritty shape with ``pty.fork()`` and drives a real controlling terminal.
"""

from __future__ import annotations

import errno
import os
import pty
import select
import shutil
import time
from pathlib import Path

import pytest

ENTRY = (
    Path(__file__).resolve().parents[2] / "config/alacritty/launch-primary-shell.zsh"
)

pytestmark = pytest.mark.skipif(
    shutil.which("zsh") is None, reason="fallback shell is zsh"
)


def _read_until(fd: int, markers: tuple[bytes, ...], deadline: float) -> bytes:
    buffer = b""
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError as error:  # EIO = child side of the pty closed
            if error.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        buffer += chunk
        if any(marker in buffer for marker in markers):
            return buffer
    return buffer


def test_failed_workspace_entry_leaves_a_live_shell(tmp_path: Path) -> None:
    home = tmp_path / "home"
    product_zdot = home / ".config/vibecrafted/vc-terminal"
    product_zdot.mkdir(parents=True)
    # Minimal isolated profile: the entry script exports ZDOTDIR here.
    (product_zdot / ".zshrc").write_text("PS1='fallback%% '\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    failing_entry = bin_dir / "vc-start"
    failing_entry.write_text(
        "#!/bin/sh\necho 'vc-start: probe refusal' >&2\nexit 4\n", encoding="utf-8"
    )
    failing_entry.chmod(0o755)

    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "TERM": "xterm",
        "SHELL": "/bin/zsh",
        "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
    }

    pid, master = pty.fork()
    if pid == 0:  # child replaced by the entry script
        os.chdir(str(tmp_path))
        os.execve(
            "/bin/bash",
            ["bash", str(ENTRY), str(failing_entry)],
            env,
        )
        os._exit(127)

    try:
        deadline = time.monotonic() + 20
        banner = _read_until(master, (b"workspace entry failed (exit 4)",), deadline)
        assert b"workspace entry failed (exit 4)" in banner, banner.decode(
            "utf-8", "replace"
        )
        assert b"refused the workspace entry" in banner, banner.decode(
            "utf-8", "replace"
        )
        assert b"List sessions: vc-frame list-sessions" in banner

        # The regression: with an interactive intermediate child the final
        # shell died on tty pgrp EIO before it could execute anything. A live
        # fallback shell must run a command and echo its output back.
        os.write(master, b"print -r -- FALLBACK_SHELL_ALIVE_$((40+2))\r")
        probe = _read_until(master, (b"FALLBACK_SHELL_ALIVE_42",), deadline)
        assert b"FALLBACK_SHELL_ALIVE_42" in probe, (banner + probe).decode(
            "utf-8", "replace"
        )
        assert b"can't set tty pgrp" not in banner + probe
        combined = banner + probe
        assert b"Your terminal is ready" not in combined
        assert b"terminal is still available" not in combined
        assert b"Log:" in combined, combined.decode("utf-8", "replace")
        assert b"Recovery: vc-frame attach" in combined

        os.write(master, b"exit\r")
        _read_until(master, (b"\x00-never-\x00",), time.monotonic() + 5)
    finally:
        os.close(master)
        _, status = os.waitpid(pid, 0)

    # The failure was logged for later diagnosis, not only printed.
    log = home / ".vibecrafted/logs/terminal-startup.log"
    assert log.is_file()
    assert "workspace entry failed (exit 4)" in log.read_text(encoding="utf-8")
    assert os.waitstatus_to_exitcode(status) == 0
