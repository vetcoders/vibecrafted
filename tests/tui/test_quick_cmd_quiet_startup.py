"""Quick cmd wrapper is a one-shot quiet composer; help stays on request."""

from __future__ import annotations

import subprocess
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


def test_quick_cmd_wrapper_closes_own_pane_by_id_after_the_command(
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


def test_quick_cmd_wrapper_is_one_shot_not_an_exec_login_shell() -> None:
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
