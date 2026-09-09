"""A product command failure must not destroy the terminal's login shell."""

import os
import pty
import select
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest
import tomllib

ENTRY = (
    Path(__file__).resolve().parents[2] / "config/alacritty/launch-primary-shell.zsh"
)


@pytest.mark.parametrize("failed_entry", [False, True])
def test_terminal_remains_a_shell_without_frame(
    tmp_path: Path, failed_entry: bool
) -> None:
    command = tmp_path / "vc-test-failure"
    command.write_text("#!/bin/sh\nprintf 'workspace rejected\\n' >&2\nexit 2\n")
    command.chmod(0o700)
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path),
        "ZDOTDIR": str(tmp_path),
        "TERM": "dumb",
        "VIBECRAFTED_HOME": str(tmp_path / "state"),
    }
    result = subprocess.run(
        ["/bin/bash", str(ENTRY), *([str(command)] if failed_entry else [])],
        input="printf 'SHELL_READY\\n'; exit 0\n",
        text=True,
        capture_output=True,
        env=environment,
        cwd=tmp_path,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "SHELL_READY" in result.stdout
    if failed_entry:
        assert "workspace rejected" in result.stderr
        assert "exit 2" in result.stderr
    assert not (tmp_path / "state").exists()


def test_terminal_policy_isolates_startup_before_user_login_files(
    tmp_path: Path,
) -> None:
    """Exercise the shipped shell argv, including its path expansion bootstrap."""
    root = ENTRY.parents[2]
    product = tmp_path / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    shutil.copy2(ENTRY, product / ENTRY.name)
    private_files = {}
    for name in (
        ".zshenv",
        ".zprofile",
        ".zshrc",
        ".zlogin",
        ".bashrc",
        ".bash_profile",
    ):
        body = "printf 'PRIVATE_PROFILE_EXECUTED\\n'\n"
        (tmp_path / name).write_text(body)
        private_files[name] = body
    policy = tomllib.loads((root / "config/vc-terminal/vibecrafted.toml").read_text())
    shell = policy["terminal"]["shell"]
    result = subprocess.run(
        [shell["program"], *shell["args"]],
        input="printf 'ISOLATED_SHELL_READY\\n'; exit 0\n",
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "TERM": "dumb"},
        cwd=tmp_path,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ISOLATED_SHELL_READY" in result.stdout
    assert "PRIVATE_PROFILE_EXECUTED" not in result.stdout + result.stderr
    assert {
        name: (tmp_path / name).read_text() for name in private_files
    } == private_files


def test_product_profile_survives_broken_completion_and_repeated_source(
    tmp_path: Path,
) -> None:
    root = ENTRY.parents[2]
    product = tmp_path / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    shutil.copy2(
        root / "config/vc-terminal/interactive.zsh", product / "interactive.zsh"
    )
    broken = tmp_path / "foreign-completions"
    broken.mkdir()
    dangling = broken / "_missing_tool"
    dangling.symlink_to(broken / "absent")
    result = subprocess.run(
        [
            "/bin/zsh",
            "-dfi",
            "-c",
            (
                'fpath=("$1" $fpath); source "$2"; '
                'before_hooks="${precmd_functions[*]}|${preexec_functions[*]}"; '
                'source "$2"; '
                '[[ "$before_hooks" == "${precmd_functions[*]}|${preexec_functions[*]}" ]] || exit 11; '
                "(( $+functions[compdef] )) || exit 12; "
                '[[ "${fpath[(Ie)$1]}" == 0 ]] || exit 13; '
                'print -r -- "HISTORY=$HISTFILE" "ATUIN=$ATUIN_DATA_DIR" "READY"'
            ),
            "profile-test",
            str(broken),
            str(ENTRY),
        ],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "TERM": "dumb"},
        cwd=tmp_path,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    assert f"HISTORY={tmp_path}/.vibecrafted/shell/zsh_history" in result.stdout
    assert f"ATUIN={tmp_path}/.vibecrafted/shell/atuin" in result.stdout
    assert (
        "skipped a completion directory"
        in (tmp_path / ".vibecrafted/shell/startup.log").read_text()
    )
    assert dangling.is_symlink()
    assert not (tmp_path / ".zsh_history").exists()
    assert not (tmp_path / ".local/share/atuin").exists()


def test_tab_completes_workspace_option_without_launching_workspace(
    tmp_path: Path,
) -> None:
    product = tmp_path / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    shutil.copy2(
        ENTRY.parents[2] / "config/vc-terminal/interactive.zsh",
        product / "interactive.zsh",
    )
    (product / ".zshrc").write_text(f'source "{ENTRY}"\nPROMPT="VC_PROMPT> "\n')
    commands = tmp_path / ".local/bin"
    commands.mkdir(parents=True)
    command = commands / "vc-start"
    command.write_text('#!/bin/sh\ntouch "$HOME/WORKSPACE_STARTED"\nexit 99\n')
    command.chmod(0o700)
    captured = tmp_path / "completed-buffer"
    pid, descriptor = pty.fork()
    if pid == 0:
        os.chdir(tmp_path)
        os.execve(
            "/bin/bash",
            ["/bin/bash", str(ENTRY)],
            {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "TERM": "xterm"},
        )

    def wait_for_prompt() -> None:
        output = bytearray()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if select.select([descriptor], [], [], 0.1)[0]:
                output.extend(os.read(descriptor, 65536))
                if b"VC_PROMPT> " in output:
                    return
        pytest.fail("interactive shell did not reach its prompt")

    try:
        wait_for_prompt()
        os.write(
            descriptor,
            (
                b'function capture_buffer() { print -r -- "$BUFFER" > "$HOME/completed-buffer"; '
                b"BUFFER=''; zle reset-prompt; }; zle -N capture_buffer; "
                b"bindkey '^X^V' capture_buffer\n"
            ),
        )
        wait_for_prompt()
        os.write(descriptor, b"vc-start --re\t\x18\x16")
        deadline = time.monotonic() + 15
        while not captured.exists() and time.monotonic() < deadline:
            if select.select([descriptor], [], [], 0.1)[0]:
                os.read(descriptor, 65536)
        assert captured.exists(), "completion widget did not return a buffer"
        assert captured.read_text().strip() == "vc-start --repo"
        assert not (tmp_path / "WORKSPACE_STARTED").exists()
    finally:
        # Interactive zsh ignores SIGTERM. Reap only this forked test child,
        # including on an assertion failure, so the acceptance test is bounded.
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        os.close(descriptor)
