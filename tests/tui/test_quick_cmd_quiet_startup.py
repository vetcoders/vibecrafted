"""Quick cmd is one quiet, lasting command shell; help stays on request."""

from __future__ import annotations

import signal
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "config"
    / "vc-frame"
    / "vc-quick-cmd.sh"
)
PROFILE = REPO_ROOT / "config" / "vc-terminal" / "interactive.zsh"


def test_quick_cmd_wrapper_exports_quiet_start_and_prints_no_banner(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "probe-shell"
    probe.write_text(
        '#!/bin/sh\nprintf "QUIET=%s\\n" "${VIBECRAFTED_QUIET_START-}"\nexit 0\n'
    )
    probe.chmod(0o755)
    result = subprocess.run(
        ["bash", str(WRAPPER)],
        input=":\n",
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "USER": "op",
            "SHELL": str(probe),
            "PATH": "/usr/bin:/bin",
        },
        cwd=tmp_path,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "QUIET=1" in result.stdout
    assert "in ~" not in result.stdout
    assert "op@" not in result.stdout
    assert "Explore commands" not in result.stdout
    assert "Vibecrafted --help" not in result.stdout


def test_quick_cmd_wrapper_closes_own_pane_by_id_when_input_ends(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    frame = bin_dir / "vc-frame"
    log = tmp_path / "frame.log"
    frame.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexit 0\n')
    frame.chmod(0o755)
    probe = tmp_path / "probe-shell"
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    result = subprocess.run(
        ["bash", str(WRAPPER)],
        input="true\n",
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "USER": "op",
            "SHELL": str(probe),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "VC_FRAME_PANE_ID": "terminal_18",
        },
        cwd=tmp_path,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    recorded = log.read_text(encoding="utf-8")
    assert "action close-pane --pane-id terminal_18" in recorded
    assert "action close-pane\n" not in recorded


def test_quick_cmd_wrapper_does_not_close_focus_when_pane_id_is_absent(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    frame = bin_dir / "vc-frame"
    log = tmp_path / "frame.log"
    frame.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexit 0\n')
    frame.chmod(0o755)
    probe = tmp_path / "probe-shell"
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    result = subprocess.run(
        ["bash", str(WRAPPER)],
        input="true\n",
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "USER": "op",
            "SHELL": str(probe),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
        },
        cwd=tmp_path,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not log.exists()


def _run_quick_cmd(
    tmp_path: Path, typed: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Drive the wrapper with a probe shell and a vc-frame stub sharing one log."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "events.log"
    frame = bin_dir / "vc-frame"
    frame.write_text(f'#!/bin/sh\nprintf "frame %s\\n" "$*" >> "{log}"\nexit 0\n')
    frame.chmod(0o755)
    probe = tmp_path / "probe-shell"
    # Invoked as: probe -l -c "<command>"; prints its output and records the run.
    probe.write_text(
        f'#!/bin/sh\nprintf "output of %s\\n" "$3"\nprintf "ran %s\\n" "$3" >> "{log}"\n'
        'case "$3" in fail) exit 3 ;; esac\nexit 0\n'
    )
    probe.chmod(0o755)
    result = subprocess.run(
        ["bash", str(WRAPPER)],
        input=typed,
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "USER": "op",
            "SHELL": str(probe),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "VC_FRAME_PANE_ID": "terminal_18",
        },
        cwd=tmp_path,
        timeout=10,
        check=False,
    )
    events = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, events


def test_quick_cmd_keeps_its_pane_after_a_command_so_the_output_stays_readable(
    tmp_path: Path,
) -> None:
    """2026-09-24, Founder: "not multiplying" is one thing; killing the quick
    shell right after a command that returns output is another. The pane stays
    for the next command and closes only when the operator leaves it."""

    result, events = _run_quick_cmd(tmp_path, "git status\nfail\nls\n")

    assert result.returncode == 0, result.stderr
    assert events == [
        "ran git status",
        "ran fail",
        "ran ls",
        "frame action close-pane --pane-id terminal_18",
    ]
    assert "output of git status" in result.stdout
    assert "output of ls" in result.stdout
    # A failing command is reported and does not end the shell.
    assert "exit 3" in result.stdout


def test_quick_cmd_exit_leaves_and_closes_only_its_own_pane(tmp_path: Path) -> None:
    result, events = _run_quick_cmd(tmp_path, "pwd\nexit\nnever\n")

    assert result.returncode == 0, result.stderr
    assert events == ["ran pwd", "frame action close-pane --pane-id terminal_18"]


def test_ctrl_c_stops_the_command_not_the_quick_cmd_shell(tmp_path: Path) -> None:
    """Ctrl-C reaches the whole foreground group: the running command and the
    wrapper. The command stops; the shell and its pane stay for the next one."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "events.log"
    frame = bin_dir / "vc-frame"
    frame.write_text(f'#!/bin/sh\nprintf "frame %s\\n" "$*" >> "{log}"\nexit 0\n')
    frame.chmod(0o755)
    probe = tmp_path / "probe-shell"
    probe.write_text(
        f'#!/bin/sh\nprintf "ran %s\\n" "$3" >> "{log}"\n'
        'case "$3" in tail*) kill -INT "$PPID"; kill -INT $$ ;; esac\nexit 0\n'
    )
    probe.chmod(0o755)
    result = subprocess.run(
        ["bash", str(WRAPPER)],
        input="tail -f log\nls\n",
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "USER": "op",
            "SHELL": str(probe),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "VC_FRAME_PANE_ID": "terminal_18",
        },
        cwd=tmp_path,
        timeout=10,
        check=False,
    )

    events = log.read_text(encoding="utf-8").splitlines()
    assert result.returncode == 0, result.stderr
    assert events == [
        "ran tail -f log",
        "ran ls",
        "frame action close-pane --pane-id terminal_18",
    ]


def test_ctrl_c_at_an_empty_prompt_keeps_the_quick_cmd_shell(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "events.log"
    frame = bin_dir / "vc-frame"
    frame.write_text(f'#!/bin/sh\nprintf "frame %s\\n" "$*" >> "{log}"\nexit 0\n')
    frame.chmod(0o755)
    probe = tmp_path / "probe-shell"
    probe.write_text(f'#!/bin/sh\nprintf "ran %s\\n" "$3" >> "{log}"\nexit 0\n')
    probe.chmod(0o755)
    process = subprocess.Popen(
        ["bash", str(WRAPPER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            "HOME": str(tmp_path),
            "USER": "op",
            "SHELL": str(probe),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "VC_FRAME_PANE_ID": "terminal_18",
        },
        cwd=tmp_path,
    )
    time.sleep(0.5)  # let the wrapper reach its read
    process.send_signal(signal.SIGINT)
    _, stderr = process.communicate(input="ls\n", timeout=10)

    assert process.returncode == 0, stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "ran ls",
        "frame action close-pane --pane-id terminal_18",
    ]


def test_quick_cmd_wrapper_is_a_command_loop_not_an_exec_login_shell() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "VIBECRAFTED_QUIET_START=1" in text
    assert "close-pane --pane-id" in text
    assert 'exec "${SHELL:-/bin/zsh}" -l' not in text
    assert "action close-pane\n" not in text


def test_product_profile_skips_help_banner_when_quiet_start_is_set(
    tmp_path: Path,
) -> None:
    product = tmp_path / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    text = PROFILE.read_text(encoding="utf-8")
    (product / "interactive.zsh").write_text(text, encoding="utf-8")
    result = subprocess.run(
        [
            "/bin/zsh",
            "-dfc",
            'source "$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"; print READY',
        ],
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "TERM": "xterm",
            "VIBECRAFTED_HOME": str(tmp_path / ".vibecrafted"),
            "VIBECRAFTED_QUIET_START": "1",
        },
        cwd=tmp_path,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    assert "Your terminal is ready" not in result.stdout
    assert "Explore commands" not in result.stdout
    assert "vc-start --repo" not in result.stdout


def _profile_on_pty(tmp_path: Path, extra_env: dict[str, str]) -> str:
    """Source the product profile on a real pty; the deck only prints to a tty."""
    import os
    import pty
    import select

    product = tmp_path / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True, exist_ok=True)
    (product / "interactive.zsh").write_text(
        PROFILE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "TERM": "xterm",
        "VIBECRAFTED_HOME": str(tmp_path / ".vibecrafted"),
        **extra_env,
    }
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(tmp_path)
        os.execve(
            "/bin/zsh",
            [
                "/bin/zsh",
                "-dfic",
                (
                    'source "$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"; '
                    "print PROFILE_READY"
                ),
            ],
            env,
        )
    output = bytearray()
    while True:
        ready, _, _ = select.select([fd], [], [], 15)
        if not ready:
            break
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        output += chunk
    os.waitpid(pid, 0)
    return output.decode("utf-8", "replace")


def test_product_profile_prints_the_deck_on_a_plain_terminal_tty(
    tmp_path: Path,
) -> None:
    text = _profile_on_pty(tmp_path, {})
    assert "PROFILE_READY" in text
    assert "Your terminal is ready" in text


def test_product_profile_skips_the_deck_inside_a_frame_pane(tmp_path: Path) -> None:
    """A new Frame pane already shows the product chrome; the deck is noise."""
    text = _profile_on_pty(
        tmp_path,
        {"VC_FRAME_PANE_ID": "2", "VC_FRAME_SESSION_NAME": "workspace"},
    )
    assert "PROFILE_READY" in text
    assert "Your terminal is ready" not in text
    assert "vc-start --repo" not in text


def test_product_profile_keeps_on_request_help_behind_quiet_gate() -> None:
    text = PROFILE.read_text(encoding="utf-8")
    assert "VIBECRAFTED_QUIET_START" in text
    assert "vibecrafted --help" in text
    assert "Your terminal is ready" in text
