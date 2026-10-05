"""Quick cmd is one shot unless the pane is pinned; help stays on request.

The pane is a real interactive zsh (decyzja Macieja 2026-10-05: the Quick cmd
lane speaks the product shell's completion and highlighting base, no Atuin),
so commands here run inside that zsh and the tests drive it with a `probe`
stub on PATH instead of a substituted $SHELL.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BANNER_LINES = (
    "This is one shot ephemeral shell unless you PIN ● it. Type command and forget.",
    "You can open a real shell by pressing [+] in the tab bar anytime.",
)
WRAPPER = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "config"
    / "vc-frame"
    / "vc-quick-cmd.sh"
)
PROFILE = REPO_ROOT / "config" / "vc-terminal" / "interactive.zsh"


def _assert_banner_once(stdout: str) -> None:
    # Whole lines, exactly as the Founder wrote them: no numbering, no prefix.
    lines = stdout.splitlines()
    for line in BANNER_LINES:
        assert lines.count(line) == 1, stdout
    first = stdout.index(BANNER_LINES[0])
    second = stdout.index(BANNER_LINES[1])
    assert first < second
    assert "Ctrl+N" not in stdout
    assert "op@" not in stdout
    assert "Explore commands" not in stdout
    assert "Vibecrafted --help" not in stdout


def _install_frame(
    bin_dir: Path,
    log: Path,
    panes: object | None,
    *,
    exit_code: int = 0,
) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    listed = ""
    if panes is not None:
        payload = bin_dir / "panes.json"
        payload.write_text(json.dumps(panes, indent=2) + "\n", encoding="utf-8")
        listed = f'  *" list-panes "*) cat "{payload}" ;;\n'
    frame = bin_dir / "vc-frame"
    frame.write_text(
        "#!/bin/sh\n"
        f'printf "frame %s\\n" "$*" >> "{log}"\n'
        'case " $* " in\n'
        f"{listed}"
        "esac\n"
        f"exit {exit_code}\n"
    )
    frame.chmod(0o755)


def _install_probe(stub_dir: Path, log: Path, *, interrupt: bool = False) -> None:
    """A `probe` command the typed lines call inside the Quick cmd zsh."""
    stub_dir.mkdir(parents=True, exist_ok=True)
    killer = ""
    if interrupt:
        killer = 'case "$1" in tail) kill -INT "$PPID"; kill -INT $$ ;; esac\n'
    probe = stub_dir / "probe"
    probe.write_text(
        "#!/bin/sh\n"
        'printf "output of %s\\n" "$*"\n'
        f'printf "ran %s\\n" "$*" >> "{log}"\n'
        f"{killer}"
        'case "$1" in fail) exit 3 ;; esac\n'
        "exit 0\n"
    )
    probe.chmod(0o755)


def _run_quick_cmd(
    tmp_path: Path,
    typed: str,
    *,
    panes: object | None = None,
    pane_id: str | None = "terminal_18",
    pane_env: str = "VC_FRAME_PANE_ID",
    install_frame: bool = True,
    frame_exit: int = 0,
    interrupt: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Drive the wrapper with a `probe` PATH stub and a vc-frame stub."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_dir = tmp_path / "stubs"
    log = tmp_path / "events.log"
    if install_frame:
        _install_frame(bin_dir, log, panes, exit_code=frame_exit)
    _install_probe(stub_dir, log, interrupt=interrupt)
    env = {
        "HOME": str(tmp_path),
        "USER": "op",
        "TERM": "xterm",
        "PATH": (
            f"{stub_dir}:{bin_dir}:/usr/bin:/bin"
            if install_frame
            else f"{stub_dir}:/usr/bin:/bin"
        ),
    }
    if pane_id is not None:
        env[pane_env] = pane_id
    result = subprocess.run(
        ["bash", str(WRAPPER)],
        input=typed,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=20,
        check=False,
    )
    events = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, events


def test_quick_cmd_wrapper_exports_quiet_start_and_prints_the_banner(
    tmp_path: Path,
) -> None:
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    quiet = stub_dir / "quietprobe"
    quiet.write_text(
        '#!/bin/sh\nprintf "QUIET=%s LANE=%s\\n" '
        '"${VIBECRAFTED_QUIET_START-}" "${VC_QUICK_CMD-}"\nexit 0\n'
    )
    quiet.chmod(0o755)
    result = subprocess.run(
        ["bash", str(WRAPPER)],
        input="quietprobe\nquietprobe\n",
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "USER": "op",
            "TERM": "xterm",
            "PATH": f"{stub_dir}:/usr/bin:/bin",
        },
        cwd=tmp_path,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # No vc-frame on PATH: unpinned one-shot — the second command never runs.
    assert result.stdout.count("QUIET=1 LANE=1") == 1
    _assert_banner_once(result.stdout)
    assert result.stdout.index(BANNER_LINES[0]) < result.stdout.index("QUIET=1")


def test_quick_cmd_wrapper_closes_own_pane_by_id_when_input_ends(
    tmp_path: Path,
) -> None:
    result, events = _run_quick_cmd(tmp_path, "probe true\n", panes=None)

    assert result.returncode == 0, result.stderr
    _assert_banner_once(result.stdout)
    recorded = "\n".join(events) + "\n"
    assert "frame action list-panes --json --state" in recorded
    assert "frame action close-pane --pane-id terminal_18" in recorded
    assert "action close-pane\n" not in recorded


def test_quick_cmd_wrapper_does_not_close_focus_when_pane_id_is_absent(
    tmp_path: Path,
) -> None:
    result, events = _run_quick_cmd(
        tmp_path,
        "probe true\nprobe second\n",
        panes=[{"id": "terminal_18", "is_pinned": True}],
        pane_id=None,
    )

    assert result.returncode == 0, result.stderr
    _assert_banner_once(result.stdout)
    assert events == ["ran true"]
    assert "close-pane" not in "\n".join(events)
    assert "list-panes" not in "\n".join(events)


def test_unpinned_quick_cmd_closes_after_one_command(tmp_path: Path) -> None:
    panes = [{"id": "terminal_18", "is_pinned": False, "is_plugin": False}]
    result, events = _run_quick_cmd(
        tmp_path, "probe git status\nprobe ls\n", panes=panes
    )

    assert result.returncode == 0, result.stderr
    _assert_banner_once(result.stdout)
    assert events == [
        "ran git status",
        "frame action list-panes --json --state",
        "frame action close-pane --pane-id terminal_18",
    ]
    assert "output of git status" in result.stdout
    assert "output of ls" not in result.stdout
    assert "action close-pane\n" not in "\n".join(events) + "\n"


def test_pinned_quick_cmd_keeps_two_commands(tmp_path: Path) -> None:
    panes = [{"id": "terminal_18", "is_pinned": True, "is_plugin": False}]
    result, events = _run_quick_cmd(
        tmp_path, "probe git status\nprobe ls\n", panes=panes
    )

    assert result.returncode == 0, result.stderr
    _assert_banner_once(result.stdout)
    assert result.stdout.index(BANNER_LINES[1]) < result.stdout.index(
        "output of git status"
    )
    assert events == [
        "ran git status",
        "frame action list-panes --json --state",
        "ran ls",
        "frame action list-panes --json --state",
        "frame action close-pane --pane-id terminal_18",
    ]
    assert "output of ls" in result.stdout


def test_missing_is_pinned_field_is_unpinned(tmp_path: Path) -> None:
    panes = [{"id": "terminal_18", "is_plugin": False, "title": "shell"}]
    result, events = _run_quick_cmd(tmp_path, "probe one\nprobe two\n", panes=panes)

    assert result.returncode == 0, result.stderr
    assert events == [
        "ran one",
        "frame action list-panes --json --state",
        "frame action close-pane --pane-id terminal_18",
    ]
    assert "ran two" not in events


def test_duplicate_pane_id_follows_the_terminal_not_the_plugin(
    tmp_path: Path,
) -> None:
    """Live list-panes repeats an id: a plugin first, then the shell pane."""

    panes = [
        {"id": "terminal_18", "is_plugin": True, "is_pinned": True, "title": "bar"},
        {"id": "terminal_18", "is_plugin": False, "is_pinned": False, "title": "sh"},
    ]
    _result, events = _run_quick_cmd(tmp_path, "probe one\nprobe two\n", panes=panes)

    assert events == [
        "ran one",
        "frame action list-panes --json --state",
        "frame action close-pane --pane-id terminal_18",
    ]


def test_numeric_pane_id_matches_list_panes_and_stays_when_pinned(
    tmp_path: Path,
) -> None:
    panes = [
        {"id": 0, "is_plugin": True, "is_pinned": False, "title": "link"},
        {"id": 0, "is_plugin": False, "is_pinned": True, "title": "shell"},
    ]
    _result, events = _run_quick_cmd(
        tmp_path,
        "probe one\nprobe two\n",
        panes=panes,
        pane_id="0",
    )

    assert events == [
        "ran one",
        "frame action list-panes --json --state",
        "ran two",
        "frame action list-panes --json --state",
        "frame action close-pane --pane-id 0",
    ]


def test_quick_cmd_exit_on_a_pinned_pane_closes_only_its_own_pane(
    tmp_path: Path,
) -> None:
    panes = [{"id": "terminal_18", "is_pinned": True, "is_plugin": False}]
    result, events = _run_quick_cmd(
        tmp_path, "probe pwd\nexit\nprobe never\n", panes=panes
    )

    assert result.returncode == 0, result.stderr
    assert events == [
        "ran pwd",
        "frame action list-panes --json --state",
        "frame action close-pane --pane-id terminal_18",
    ]


def test_failing_command_prints_exit_status_then_unpinned_pane_closes(
    tmp_path: Path,
) -> None:
    panes = [{"id": "terminal_18", "is_pinned": False}]
    result, events = _run_quick_cmd(tmp_path, "probe fail\nprobe ls\n", panes=panes)

    assert result.returncode == 0, result.stderr
    assert "[exit 3]" in result.stdout
    assert events == [
        "ran fail",
        "frame action list-panes --json --state",
        "frame action close-pane --pane-id terminal_18",
    ]


def test_missing_vc_frame_is_unpinned_and_skips_the_second_command(
    tmp_path: Path,
) -> None:
    result, events = _run_quick_cmd(
        tmp_path,
        "probe one\nprobe two\n",
        install_frame=False,
    )

    assert result.returncode == 0, result.stderr
    _assert_banner_once(result.stdout)
    assert events == ["ran one"]


def test_zellij_pane_id_closes_that_pane_when_vc_frame_pane_id_is_unset(
    tmp_path: Path,
) -> None:
    panes = [{"id": "terminal_9", "is_pinned": False, "is_plugin": False}]
    _result, events = _run_quick_cmd(
        tmp_path,
        "probe one\nprobe two\n",
        panes=panes,
        pane_id="terminal_9",
        pane_env="ZELLIJ_PANE_ID",
    )

    assert events == [
        "ran one",
        "frame action list-panes --json --state",
        "frame action close-pane --pane-id terminal_9",
    ]


def test_ctrl_c_stops_the_command_and_an_unpinned_pane_closes(
    tmp_path: Path,
) -> None:
    """Ctrl-C stops the running command. It does not kill the shell; the
    one-shot rule then closes an unpinned pane instead of reading the next line."""

    panes = [{"id": "terminal_18", "is_pinned": False, "is_plugin": False}]
    result, events = _run_quick_cmd(
        tmp_path,
        "probe tail -f log\nprobe ls\n",
        panes=panes,
        interrupt=True,
    )

    assert result.returncode == 0, result.stderr
    assert events == [
        "ran tail -f log",
        "frame action list-panes --json --state",
        "frame action close-pane --pane-id terminal_18",
    ]


def test_ctrl_c_on_a_pinned_pane_stops_only_the_running_command(
    tmp_path: Path,
) -> None:
    panes = [{"id": "terminal_18", "is_pinned": True, "is_plugin": False}]
    result, events = _run_quick_cmd(
        tmp_path,
        "probe tail -f log\nprobe ls\n",
        panes=panes,
        interrupt=True,
    )

    assert result.returncode == 0, result.stderr
    assert events == [
        "ran tail -f log",
        "frame action list-panes --json --state",
        "ran ls",
        "frame action list-panes --json --state",
        "frame action close-pane --pane-id terminal_18",
    ]


def test_ctrl_c_at_an_empty_prompt_keeps_the_quick_cmd_shell(tmp_path: Path) -> None:
    """Ctrl-C at the empty prompt clears the line and keeps the shell. The
    real pane always has a pty (ZLE handles the interrupt), so this case is
    driven on one — over a pipe zsh treats the abort as end of input."""
    import os
    import pty
    import select

    bin_dir = tmp_path / "bin"
    stub_dir = tmp_path / "stubs"
    log = tmp_path / "events.log"
    panes = [{"id": "terminal_18", "is_pinned": False, "is_plugin": False}]
    _install_frame(bin_dir, log, panes)
    _install_probe(stub_dir, log)
    env = {
        "HOME": str(tmp_path),
        "USER": "op",
        "TERM": "xterm",
        "PATH": f"{stub_dir}:{bin_dir}:/usr/bin:/bin",
        "VC_FRAME_PANE_ID": "terminal_18",
    }
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(tmp_path)
        os.execve("/bin/bash", ["/bin/bash", str(WRAPPER)], env)

    def _read_until(marker: bytes, budget: bytearray) -> bool:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if marker in budget:
                return True
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return marker in budget
            if not chunk:
                return marker in budget
            budget += chunk
        return marker in budget

    output = bytearray()
    assert _read_until("❯_".encode(), output), output.decode("utf-8", "replace")
    os.write(fd, b"\x03")  # Ctrl-C at the empty prompt
    time.sleep(0.3)
    os.write(fd, b"probe ls\r")
    _read_until(b"ran-never-appears", output)  # drain until the pane exits
    try:
        os.close(fd)
    except OSError:
        pass
    os.waitpid(pid, 0)

    assert log.read_text(encoding="utf-8").splitlines() == [
        "ran ls",
        "frame action list-panes --json --state",
        "frame action close-pane --pane-id terminal_18",
    ]


def test_quick_cmd_wrapper_is_the_product_zsh_lane_with_one_shot_hooks() -> None:
    """Contract: a real interactive zsh with the product completion base and
    the one-shot rule in hooks — never a bash read loop, never a bare
    close-pane, never Atuin in this lane."""
    text = WRAPPER.read_text(encoding="utf-8")
    assert "VIBECRAFTED_QUIET_START=1" in text
    assert "VC_QUICK_CMD=1" in text
    assert "close-pane --pane-id" in text
    assert "list-panes --json --state" in text
    assert "is_pinned" in text
    assert "interactive.zsh" in text
    assert "add-zsh-hook preexec" in text
    assert "add-zsh-hook precmd" in text
    assert "zshexit" in text
    assert "exec zsh -l -i" in text
    assert "ZDOTDIR=" in text
    assert 'exec "${SHELL:-/bin/zsh}" -l' not in text
    assert "action close-pane\n" not in text
    assert "atuin init" not in text.lower()


def test_product_profile_quick_lane_skips_tool_chrome_keeps_completion_base() -> None:
    """VC_QUICK_CMD=1 must gate zoxide/atuin/starship init, never compinit or
    the highlighting plugins."""
    text = PROFILE.read_text(encoding="utf-8")
    assert "VC_QUICK_CMD" in text
    lane = text.index("vc_quick_lane=0")
    assert lane < text.index("for vc_tool in zoxide atuin starship")
    # The completion and highlighting base stays unconditional.
    assert text.index("compinit -i") < lane
    assert "zsh-autosuggestions zsh-syntax-highlighting" in text


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


def test_product_profile_quick_lane_never_inits_atuin_or_starship(
    tmp_path: Path,
) -> None:
    """With VC_QUICK_CMD=1 the profile must not even probe the tools."""
    product = tmp_path / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    (product / "interactive.zsh").write_text(
        PROFILE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    log = tmp_path / "tool-calls.log"
    for tool in ("atuin", "starship", "zoxide"):
        stub = stub_dir / tool
        stub.write_text(f'#!/bin/sh\nprintf "{tool} %s\\n" "$*" >> "{log}"\nexit 0\n')
        stub.chmod(0o755)
    result = subprocess.run(
        [
            "/bin/zsh",
            "-dfic",
            'source "$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"; print READY',
        ],
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{stub_dir}:/usr/bin:/bin",
            "TERM": "xterm",
            "VIBECRAFTED_HOME": str(tmp_path / ".vibecrafted"),
            "VIBECRAFTED_QUIET_START": "1",
            "VC_QUICK_CMD": "1",
        },
        cwd=tmp_path,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    assert not log.exists(), log.read_text(encoding="utf-8")


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


def test_product_profile_refuses_the_deck_after_a_failed_entry(tmp_path: Path) -> None:
    """A non-zero workspace entry must not wear the success banner."""
    text = _profile_on_pty(
        tmp_path,
        {
            "VIBECRAFTED_TERMINAL_ENTRY_STATUS": "4",
            "VIBECRAFTED_TERMINAL_ENTRY_REASON": "workspace entry failed (exit 4)",
            "VIBECRAFTED_TERMINAL_ENTRY_LOG": str(
                tmp_path / ".vibecrafted" / "logs" / "terminal-startup.log"
            ),
        },
    )
    assert "PROFILE_READY" in text
    assert "Workspace entry refused" in text
    assert "workspace entry failed (exit 4)" in text
    assert "terminal-startup.log" in text
    assert "recovery: vc-frame attach" in text
    assert "list sessions: vc-frame list-sessions" in text
    assert "Your terminal is ready" not in text
    assert "Explore commands" not in text


def test_product_profile_keeps_on_request_help_behind_quiet_gate() -> None:
    text = PROFILE.read_text(encoding="utf-8")
    assert "VIBECRAFTED_QUIET_START" in text
    assert "vibecrafted --help" in text
    assert "Your terminal is ready" in text
