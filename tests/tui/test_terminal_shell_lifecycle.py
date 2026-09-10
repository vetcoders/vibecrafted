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


def _stage_product_profile(tmp_path: Path) -> Path:
    root = ENTRY.parents[2]
    product = tmp_path / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    shutil.copy2(root / "config/vc-terminal/interactive.zsh", product / "interactive.zsh")
    shutil.copy2(ENTRY, product / "launch-primary-shell.zsh")
    (product / ".zshrc").write_text(
        'source "$HOME/.config/vibecrafted/vc-terminal/launch-primary-shell.zsh"\n'
    )
    aliases = tmp_path / ".config/vibecrafted/shell/aliases"
    shutil.copytree(
        root / "vibecrafted-core/vibecrafted_core/runtime/shell/aliases",
        aliases,
    )
    return product


def _zsh_profile(
    tmp_path: Path,
    script: str,
    *,
    path: str = "/usr/bin:/bin",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {
        "HOME": str(tmp_path),
        "PATH": path,
        "TERM": "dumb",
        "VIBECRAFTED_HOME": str(tmp_path / ".vibecrafted"),
    }
    if extra_env:
        environment.update(extra_env)
    return subprocess.run(
        ["/bin/zsh", "-dfi", "-c", script, "profile-test"],
        capture_output=True,
        text=True,
        env=environment,
        cwd=tmp_path,
        timeout=15,
        check=False,
    )


def test_aliases_catalog_reload_keeps_cwd_and_hook_identity(tmp_path: Path) -> None:
    _stage_product_profile(tmp_path)
    work = tmp_path / "keep-cwd"
    work.mkdir()
    result = _zsh_profile(
        tmp_path,
        (
            'source "$HOME/.config/vibecrafted/vc-terminal/launch-primary-shell.zsh"; '
            f'cd {str(work)!r}; '
            'before_hooks="${precmd_functions[*]}|${preexec_functions[*]}"; '
            'print -r -- "CWD=$PWD"; '
            'print -r -- "GL=${aliases[gl]}"; '
            'aliases | grep -q "^git$" || exit 21; '
            "reload; "
            f'[[ "$PWD" == {str(work)!r} ]] || exit 22; '
            '[[ "$before_hooks" == "${precmd_functions[*]}|${preexec_functions[*]}" ]] || exit 23; '
            "print -r -- 'alias gl=\"git log --oneline -1\"' > "
            '"$HOME/.config/vibecrafted/shell/aliases/git.zsh"; '
            "reload; "
            'print -r -- "GL_RELOADED=${aliases[gl]}"; '
            "print -r -- READY"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    assert f"CWD={work}" in result.stdout
    assert "git log --oneline --graph --decorate -20" in result.stdout
    assert "GL_RELOADED=git log --oneline -1" in result.stdout
    assert not (tmp_path / ".vibecrafted" / "control_plane").exists()


def test_two_line_prompt_without_starship_and_with_fake_starship(
    tmp_path: Path,
) -> None:
    _stage_product_profile(tmp_path)
    offline = _zsh_profile(
        tmp_path,
        (
            'source "$HOME/.config/vibecrafted/vc-terminal/launch-primary-shell.zsh"; '
            'print -r -- "PROMPT=$PROMPT"; print -r -- READY'
        ),
    )
    assert offline.returncode == 0, offline.stderr
    assert "READY" in offline.stdout
    assert "❯" in offline.stdout
    assert "%~" in offline.stdout
    prompt = offline.stdout.split("PROMPT=", 1)[1]
    assert "\n" in prompt.split("READY", 1)[0]

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    starship = bin_dir / "starship"
    starship.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = init ]; then\n"
        "cat <<'EOF'\n"
        "starship_precmd() { : }\n"
        "precmd_functions+=(starship_precmd)\n"
        "PROMPT='STARSHIP>'\n"
        "EOF\n"
        "fi\n"
    )
    starship.chmod(0o755)
    with_starship = _zsh_profile(
        tmp_path,
        (
            'source "$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"; '
            'print -r -- "PROMPT=$PROMPT"; '
            '(( $+functions[starship_precmd] )) || exit 31; '
            "print -r -- READY"
        ),
        path=f"{bin_dir}:/usr/bin:/bin",
    )
    assert with_starship.returncode == 0, with_starship.stderr
    assert "PROMPT=STARSHIP>" in with_starship.stdout


def test_offline_startup_keeps_native_shell_without_optional_integrations(
    tmp_path: Path,
) -> None:
    _stage_product_profile(tmp_path)
    (tmp_path / ".zshrc").write_text("print PRIVATE_PROFILE_EXECUTED\n")
    result = _zsh_profile(
        tmp_path,
        (
            'source "$HOME/.config/vibecrafted/vc-terminal/launch-primary-shell.zsh"; '
            '(( $+aliases[gl] )) || exit 41; '
            '(( $+functions[reload] )) || exit 42; '
            '(( $+functions[vcf-lp] )) || exit 43; '
            'print -r -- "PROMPT=$PROMPT"; '
            "print -r -- READY"
        ),
        extra_env={"VC_TERMINAL_PLUGIN_PREFIXES": str(tmp_path / "missing-plugins")},
    )
    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    assert "PRIVATE_PROFILE_EXECUTED" not in result.stdout + result.stderr
    log = (tmp_path / ".vibecrafted/shell/startup.log").read_text()
    assert "starship is not installed" in log
    assert "atuin is not installed" in log
    assert "zoxide is not installed" in log
    assert "zsh-autosuggestions is not installed" in log
    assert not (tmp_path / ".vibecrafted" / "control_plane").exists()


def test_frame_conveniences_forward_native_argv_without_force(
    tmp_path: Path,
) -> None:
    _stage_product_profile(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    captured = tmp_path / "vc-frame-argv"
    frame = bin_dir / "vc-frame"
    frame.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {str(captured)!r}\n"
    )
    frame.chmod(0o755)
    result = _zsh_profile(
        tmp_path,
        (
            'source "$HOME/.config/vibecrafted/vc-terminal/launch-primary-shell.zsh"; '
            "vcf-lp --help; "
            "vcf-da --dry-run; "
            "print -r -- READY"
        ),
        path=f"{bin_dir}:/usr/bin:/bin",
    )
    assert result.returncode == 0, result.stderr
    argv = captured.read_text()
    assert "action list-panes --help" in argv
    assert "delete-all-sessions --dry-run" in argv
    assert "--force" not in argv


def test_restored_and_new_pane_follow_server_zdotdir_not_host_dotfiles(
    tmp_path: Path,
) -> None:
    _stage_product_profile(tmp_path)
    (tmp_path / ".zshrc").write_text("print PRIVATE_PROFILE_EXECUTED\n")
    product_zdot = tmp_path / ".config/vibecrafted/vc-terminal"
    restored = subprocess.run(
        ["/bin/zsh", "-l", "-c", 'print -r -- "GL=${aliases[gl]}"; print -r -- READY'],
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "TERM": "dumb",
            "ZDOTDIR": str(product_zdot),
            "VIBECRAFTED_HOME": str(tmp_path / ".vibecrafted"),
        },
        cwd=tmp_path,
        timeout=15,
        check=False,
    )
    assert restored.returncode == 0, restored.stderr
    assert "READY" in restored.stdout
    assert "git log --oneline --graph --decorate -20" in restored.stdout
    assert "PRIVATE_PROFILE_EXECUTED" not in restored.stdout + restored.stderr

    host = subprocess.run(
        ["/bin/zsh", "-l", "-c", "print -r -- HOST_READY"],
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "TERM": "dumb",
        },
        cwd=tmp_path,
        timeout=15,
        check=False,
    )
    assert host.returncode == 0, host.stderr
    assert "PRIVATE_PROFILE_EXECUTED" in host.stdout
    assert "git log --oneline --graph --decorate -20" not in host.stdout
