"""A product command failure must not destroy the terminal's login shell."""

import os
import pty
import select
import shutil
import signal
import subprocess
import sys
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
    shutil.copy2(
        root / "config/vc-terminal/interactive.zsh", product / "interactive.zsh"
    )
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
            f"cd {str(work)!r}; "
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


def test_reload_applies_updated_installed_profile_without_duplicating_hooks(
    tmp_path: Path,
) -> None:
    _stage_product_profile(tmp_path)
    work = tmp_path / "keep-cwd"
    work.mkdir()
    patcher = tmp_path / "patch_installed_profile.py"
    patcher.write_text(
        "from pathlib import Path\n"
        "path = Path.home() / '.config/vibecrafted/vc-terminal/interactive.zsh'\n"
        "text = path.read_text()\n"
        "text = text.replace(\n"
        "    \"print -r -- 'navigation'\",\n"
        "    \"print -r -- 'navigation-reloaded-from-disk'\",\n"
        "    1,\n"
        ")\n"
        "needle = '_vc_terminal_owned_alias_names='\n"
        "insert = (\n"
        "    '_vc_terminal_disk_reload_marker() { print -r -- DISK_RELOAD_APPLIED; }\\n'\n"
        "    + needle\n"
        ")\n"
        "if '_vc_terminal_disk_reload_marker' not in text:\n"
        "    text = text.replace(needle, insert, 1)\n"
        "path.write_text(text)\n"
    )
    result = _zsh_profile(
        tmp_path,
        (
            'source "$HOME/.config/vibecrafted/vc-terminal/launch-primary-shell.zsh"; '
            f"cd {str(work)!r}; "
            'before_hooks="${precmd_functions[*]}|${preexec_functions[*]}"; '
            'print -r -- "HIST=$HISTFILE"; '
            'print -r -- "CWD=$PWD"; '
            'aliases | grep -q "^navigation$" || exit 51; '
            "(( $+functions[_vc_terminal_disk_reload_marker] )) && exit 52; "
            f"{sys.executable!r} {str(patcher)!r}; "
            "reload; "
            f'[[ "$PWD" == {str(work)!r} ]] || exit 53; '
            '[[ "$HISTFILE" == "$HOME/.vibecrafted/shell/zsh_history" ]] || exit 54; '
            '[[ "$before_hooks" == "${precmd_functions[*]}|${preexec_functions[*]}" ]] || exit 55; '
            "(( $+functions[_vc_terminal_disk_reload_marker] )) || exit 56; "
            "_vc_terminal_disk_reload_marker; "
            'aliases | grep -q "^navigation-reloaded-from-disk$" || exit 57; '
            'print -r -- "CWD=$PWD"; '
            "print -r -- READY"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    assert "DISK_RELOAD_APPLIED" in result.stdout
    assert result.stdout.count("DISK_RELOAD_APPLIED") == 1
    assert f"CWD={work}" in result.stdout
    assert f"HIST={tmp_path / '.vibecrafted/shell/zsh_history'}" in result.stdout
    assert "Your terminal is ready" not in result.stdout


def test_product_tool_env_is_set_before_tool_init(tmp_path: Path) -> None:
    _stage_product_profile(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorder = tmp_path / "tool-init.env"
    for name in ("zoxide", "atuin", "starship"):
        tool = bin_dir / name
        tool.write_text(
            "#!/bin/sh\n"
            f'printf "%s STARSHIP_CONFIG=%s\\n" "{name}" "${{STARSHIP_CONFIG-}}" >> {str(recorder)!r}\n'
            f'printf "%s ATUIN_CONFIG_DIR=%s\\n" "{name}" "${{ATUIN_CONFIG_DIR-}}" >> {str(recorder)!r}\n'
            f'printf "%s ATUIN_DATA_DIR=%s\\n" "{name}" "${{ATUIN_DATA_DIR-}}" >> {str(recorder)!r}\n'
            f'printf "%s ATUIN_DB_PATH=%s\\n" "{name}" "${{ATUIN_DB_PATH-}}" >> {str(recorder)!r}\n'
            f'printf "%s _ZO_DATA_DIR=%s\\n" "{name}" "${{_ZO_DATA_DIR-}}" >> {str(recorder)!r}\n'
            'if [ "$1" = init ]; then\n'
            "  printf ':\\n'\n"
            "fi\n"
        )
        tool.chmod(0o755)
    result = _zsh_profile(
        tmp_path,
        (
            'source "$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"; '
            "print -r -- READY"
        ),
        path=f"{bin_dir}:/usr/bin:/bin",
    )
    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    recorded = recorder.read_text()
    product_starship = tmp_path / ".config/vibecrafted/starship.toml"
    product_atuin = tmp_path / ".config/vibecrafted/atuin"
    product_atuin_data = tmp_path / ".vibecrafted/shell/atuin"
    product_atuin_db = tmp_path / ".vibecrafted/shell/history.db"
    product_zoxide = tmp_path / ".vibecrafted/shell/zoxide"
    for name in ("zoxide", "atuin", "starship"):
        assert f"{name} STARSHIP_CONFIG={product_starship}" in recorded
        assert f"{name} ATUIN_CONFIG_DIR={product_atuin}" in recorded
        assert f"{name} ATUIN_DATA_DIR={product_atuin_data}" in recorded
        assert f"{name} ATUIN_DB_PATH={product_atuin_db}" in recorded
        assert f"{name} _ZO_DATA_DIR={product_zoxide}" in recorded


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
        'if [ "$1" = init ]; then\n'
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
            "(( $+functions[starship_precmd] )) || exit 31; "
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
            "(( $+aliases[gl] )) || exit 41; "
            "(( $+functions[reload] )) || exit 42; "
            "(( $+functions[vcf-lp] )) || exit 43; "
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
    frame.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {str(captured)!r}\n")
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


def test_interactive_login_zdotdir_loads_product_profile_not_host(
    tmp_path: Path,
) -> None:
    """Login+interactive zsh reads ZDOTDIR/.zshrc. `zsh -l -c` does not."""
    _stage_product_profile(tmp_path)
    (tmp_path / ".zshrc").write_text(
        'touch "$HOME/PRIVATE_PROFILE_EXECUTED"\nprint PRIVATE_PROFILE_EXECUTED\n'
    )
    product_zdot = tmp_path / ".config/vibecrafted/vc-terminal"
    restored = subprocess.run(
        [
            "/bin/zsh",
            "-lic",
            'print -r -- "GL=${aliases[gl]}"; print -r -- READY',
        ],
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
    assert not (tmp_path / "PRIVATE_PROFILE_EXECUTED").exists()

    host = subprocess.run(
        ["/bin/zsh", "-lic", "print -r -- HOST_READY"],
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
    assert (tmp_path / "PRIVATE_PROFILE_EXECUTED").exists()


def _installed_frame() -> Path | None:
    for candidate in (
        os.environ.get("VIBECRAFTED_VC_FRAME_BIN", ""),
        os.environ.get("VIBECRAFTED_RUNTIME_ROOT", "")
        and os.path.join(os.environ["VIBECRAFTED_RUNTIME_ROOT"], "libexec", "vc-frame"),
    ):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return Path(candidate)
    releases = Path.home() / ".local" / "share" / "vibecrafted" / "releases"
    if releases.is_dir():
        found = sorted(
            releases.glob("*/libexec/vc-frame"), key=lambda p: p.stat().st_mtime
        )
        if found:
            return found[-1]
    return None


_REAL_FRAME = _installed_frame()

_FRAME_IDENTITY_ENV = (
    "VC_FRAME",
    "VC_FRAME_PANE_ID",
    "VC_FRAME_SESSION_NAME",
    "ZELLIJ",
    "ZELLIJ_PANE_ID",
    "ZELLIJ_SESSION_NAME",
    "VIBECRAFTED_OPERATOR_SESSION",
    "VIBECRAFTED_WORKSPACE_ID",
    "VIBECRAFTED_SESSION_ID",
    "VIBECRAFTED_WORKSPACE_INSTANCE_ID",
    "VIBECRAFTED_PREFER_REPO_VC_FRAME",
    "VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME",
)


def _wait_owned_evidence(path: Path, timeout: float = 25) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return path.read_text()
        time.sleep(0.1)
    pytest.fail(f"owned Frame pane did not write {path}")


def _write_frame_probe(home: Path, name: str) -> Path:
    probe = home / f"{name}.probe.zsh"
    evidence = home / f"{name}.evidence"
    probe.write_text(
        "{\n"
        f'  print -r -- "PROBE={name}"\n'
        '  print -r -- "GL=${aliases[gl]}"\n'
        '  print -r -- "CWD=$PWD"\n'
        '  print -r -- "ZDOTDIR=$ZDOTDIR"\n'
        '  print -r -- "STARSHIP_CONFIG=${STARSHIP_CONFIG:-}"\n'
        "} > "
        f"{str(evidence)!r}\n"
    )
    return probe


def _isolated_frame_env(
    sandbox: Path, frame: Path, extra: dict[str, str] | None = None
) -> dict[str, str]:
    env = os.environ.copy()
    for key in _FRAME_IDENTITY_ENV:
        env.pop(key, None)
    env.update(
        {
            "HOME": str(sandbox / "home"),
            "VIBECRAFTED_HOME": str(sandbox / "home" / ".vibecrafted"),
            "XDG_CONFIG_HOME": str(sandbox / "home" / ".config"),
            "VC_FRAME_SOCKET_DIR": str(sandbox / "sock"),
            "VC_FRAME_CONFIG_DIR": str(sandbox / "cfg"),
            "VC_FRAME_CONFIG_FILE": str(sandbox / "cfg" / "config.kdl"),
            "VIBECRAFTED_VC_FRAME_BIN": str(frame),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "TERM": "dumb",
        }
    )
    if extra:
        env.update(extra)
    return env


@pytest.mark.skipif(_REAL_FRAME is None, reason="no installed vc-frame engine")
def test_real_frame_new_pane_and_restore_use_product_profile(tmp_path: Path) -> None:
    """Real engine, isolated socket/home. Not a host-shell imitation."""
    assert tmp_path.is_dir()
    assert _REAL_FRAME is not None
    tag = f"vcsh{os.getpid() % 100000}"
    sandbox = Path("/tmp") / tag
    if sandbox.exists():
        shutil.rmtree(sandbox)
    (sandbox / "sock").mkdir(parents=True)
    (sandbox / "cfg" / "layouts").mkdir(parents=True)
    home = sandbox / "home"
    home.mkdir()
    _stage_product_profile(home)
    (home / ".zshrc").write_text(
        'touch "$HOME/PRIVATE_PROFILE_EXECUTED"\nprint PRIVATE_PROFILE_EXECUTED\n'
    )
    work = home / "restore-cwd"
    work.mkdir()
    (sandbox / "cfg" / "config.kdl").write_text(
        'keybinds clear-defaults=true {}\ndefault_shell "zsh"\n',
        encoding="utf-8",
    )
    (sandbox / "cfg" / "layouts" / "empty.kdl").write_text(
        "layout {\n  pane\n}\n", encoding="utf-8"
    )
    product_zdot = home / ".config/vibecrafted/vc-terminal"
    new_probe = _write_frame_probe(home, "new-pane")
    restore_probe = _write_frame_probe(home, "restore")
    env = _isolated_frame_env(
        sandbox,
        _REAL_FRAME,
        extra={"ZDOTDIR": str(product_zdot)},
    )
    session = tag

    def frame(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(_REAL_FRAME), *args],
            check=False,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        created = frame(
            "--new-session-with-layout",
            str(sandbox / "cfg" / "layouts" / "empty.kdl"),
            "attach",
            "--create-background",
            session,
        )
        assert created.returncode == 0, created.stderr
        deadline = time.monotonic() + 15
        while session not in frame("ls").stdout and time.monotonic() < deadline:
            time.sleep(0.25)
        assert session in frame("ls").stdout

        new_pane = frame(
            "--session",
            session,
            "action",
            "new-pane",
            "--cwd",
            str(work),
            "--",
            "/bin/zsh",
            "-lic",
            f"source {str(new_probe)!r}",
        )
        assert new_pane.returncode == 0, new_pane.stderr
        new_text = _wait_owned_evidence(home / "new-pane.evidence")
        assert "PROBE=new-pane" in new_text
        assert "git log --oneline --graph --decorate -20" in new_text
        assert f"CWD={work}" in new_text
        assert f"ZDOTDIR={product_zdot}" in new_text
        assert not (home / "PRIVATE_PROFILE_EXECUTED").exists()

        killed = frame("kill-session", session)
        assert killed.returncode == 0, killed.stderr
        deadline = time.monotonic() + 10
        listing = ""
        while time.monotonic() < deadline:
            listing = frame("list-sessions", "--no-formatting").stdout
            if session in listing and "(EXITED" in listing:
                break
            time.sleep(0.2)
        assert session in listing
        assert "(EXITED" in listing, listing

        restored = frame("attach", "--create-background", session)
        assert restored.returncode == 0, restored.stderr
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            live = frame("list-sessions", "--no-formatting").stdout
            if session in live and "(EXITED" not in live:
                break
            time.sleep(0.25)
        restore_pane = frame(
            "--session",
            session,
            "action",
            "new-pane",
            "--cwd",
            str(work),
            "--",
            "/bin/zsh",
            "-lic",
            f"source {str(restore_probe)!r}",
        )
        assert restore_pane.returncode == 0, restore_pane.stderr
        restore_text = _wait_owned_evidence(home / "restore.evidence")
        assert "PROBE=restore" in restore_text
        assert "git log --oneline --graph --decorate -20" in restore_text
        assert f"CWD={work}" in restore_text
        assert f"ZDOTDIR={product_zdot}" in restore_text
        assert not (home / "PRIVATE_PROFILE_EXECUTED").exists()
    finally:
        frame("kill-session", session)
        frame("delete-session", session, "--force")
        leftover = frame("list-sessions", "--no-formatting").stdout
        shutil.rmtree(sandbox, ignore_errors=True)
    assert session not in leftover, leftover


@pytest.mark.skipif(_REAL_FRAME is None, reason="no installed vc-frame engine")
def test_real_frame_live_server_keeps_zdotdir_when_new_client_differs(
    tmp_path: Path,
) -> None:
    """A later client env does not rewrite an already-live server."""
    assert tmp_path.is_dir()
    assert _REAL_FRAME is not None
    tag = f"vcso{os.getpid() % 100000}"
    sandbox = Path("/tmp") / tag
    if sandbox.exists():
        shutil.rmtree(sandbox)
    (sandbox / "sock").mkdir(parents=True)
    (sandbox / "cfg" / "layouts").mkdir(parents=True)
    home = sandbox / "home"
    home.mkdir()
    _stage_product_profile(home)
    (home / ".zshrc").write_text(
        'touch "$HOME/PRIVATE_PROFILE_EXECUTED"\nprint PRIVATE_PROFILE_EXECUTED\n'
    )
    old_zdot = home / "old-live-zdot"
    old_zdot.mkdir()
    (old_zdot / ".zshrc").write_text(
        'touch "$HOME/OLD_LIVE_SERVER"\nprint OLD_LIVE_SERVER\n'
    )
    product_zdot = home / ".config/vibecrafted/vc-terminal"
    work = home / "live-cwd"
    work.mkdir()
    (sandbox / "cfg" / "config.kdl").write_text(
        'keybinds clear-defaults=true {}\ndefault_shell "zsh"\n',
        encoding="utf-8",
    )
    (sandbox / "cfg" / "layouts" / "empty.kdl").write_text(
        "layout {\n  pane\n}\n", encoding="utf-8"
    )
    old_probe = _write_frame_probe(home, "old-server")
    client_probe = _write_frame_probe(home, "new-client")
    server_env = _isolated_frame_env(
        sandbox,
        _REAL_FRAME,
        extra={"ZDOTDIR": str(old_zdot)},
    )
    client_env = _isolated_frame_env(
        sandbox,
        _REAL_FRAME,
        extra={"ZDOTDIR": str(product_zdot)},
    )
    session = tag

    def run_frame(
        environment: dict[str, str], *args: str, timeout: int = 30
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(_REAL_FRAME), *args],
            check=False,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        created = run_frame(
            server_env,
            "--new-session-with-layout",
            str(sandbox / "cfg" / "layouts" / "empty.kdl"),
            "attach",
            "--create-background",
            session,
        )
        assert created.returncode == 0, created.stderr
        deadline = time.monotonic() + 15
        while (
            session not in run_frame(server_env, "ls").stdout
            and time.monotonic() < deadline
        ):
            time.sleep(0.25)
        assert session in run_frame(server_env, "ls").stdout

        first = run_frame(
            server_env,
            "--session",
            session,
            "action",
            "new-pane",
            "--cwd",
            str(work),
            "--",
            "/bin/zsh",
            "-lic",
            f"source {str(old_probe)!r}",
        )
        assert first.returncode == 0, first.stderr
        old_text = _wait_owned_evidence(home / "old-server.evidence")
        assert "PROBE=old-server" in old_text
        assert f"ZDOTDIR={old_zdot}" in old_text
        assert "git log --oneline --graph --decorate -20" not in old_text
        assert (home / "OLD_LIVE_SERVER").exists()

        listing = run_frame(client_env, "list-sessions", "--no-formatting")
        assert listing.returncode == 0, listing.stderr
        assert session in listing.stdout

        later = run_frame(
            client_env,
            "--session",
            session,
            "action",
            "new-pane",
            "--cwd",
            str(work),
            "--",
            "/bin/zsh",
            "-lic",
            f"source {str(client_probe)!r}",
        )
        assert later.returncode == 0, later.stderr
        later_text = _wait_owned_evidence(home / "new-client.evidence")
        assert "PROBE=new-client" in later_text
        assert f"ZDOTDIR={old_zdot}" in later_text
        assert "git log --oneline --graph --decorate -20" not in later_text
        assert not (home / "PRIVATE_PROFILE_EXECUTED").exists()
    finally:
        run_frame(server_env, "kill-session", session)
        run_frame(server_env, "delete-session", session, "--force")
        leftover = run_frame(server_env, "list-sessions", "--no-formatting").stdout
        shutil.rmtree(sandbox, ignore_errors=True)
    assert session not in leftover, leftover
