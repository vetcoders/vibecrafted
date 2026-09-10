"""A product command failure must not destroy the terminal's login shell."""

import contextlib
import json
import os
import pty
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
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

# Ambient identity that must never reach an owned engine. `os.environ.copy()`
# minus a deny-list is the wrong shape here: `VC_FRAME_SERVER_IDLE_EXIT_SECS`
# alone lets the operator's shell reap this test's background session mid-run,
# and every future engine variable would leak in unnoticed. The environment
# below is therefore built from nothing; this tuple only guards the result.
_AMBIENT_FRAME_ENV = (
    "VC_FRAME",
    "VC_FRAME_PANE_ID",
    "VC_FRAME_SESSION_NAME",
    "VC_FRAME_AUTO_ATTACH",
    "VC_FRAME_AUTO_EXIT",
    "VC_FRAME_CALLER",
    "VC_FRAME_SERVER_IDLE_EXIT_SECS",
    "VC_FRAME_WORKSPACE_SESSION_ID",
    "ZELLIJ",
    "ZELLIJ_AUTO_ATTACH",
    "ZELLIJ_AUTO_EXIT",
    "ZELLIJ_CONFIG_DIR",
    "ZELLIJ_CONFIG_FILE",
    "ZELLIJ_PANE_ID",
    "ZELLIJ_SESSION_NAME",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "STARSHIP_CONFIG",
    "VIBECRAFTED_TERMINAL_ENTRY",
    "VIBECRAFTED_OPERATOR_SESSION",
    "VIBECRAFTED_WORKSPACE_ID",
    "VIBECRAFTED_SESSION_ID",
    "VIBECRAFTED_WORKSPACE_INSTANCE_ID",
    "VIBECRAFTED_PREFER_REPO_VC_FRAME",
    "VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME",
)

_PRODUCT_GL = "git log --oneline --graph --decorate -20"
_EVIDENCE_END = "PROBE_END"

# Two panes, two working directories. A pane's cwd is part of what the engine
# writes into its resurrection cache, so a cache naming both directories can
# only have been written after both panes existed. That is what makes the wait
# before `kill-session` a wait for a real, current write instead of a sleep.
_STARTUP_DIR = "pane-alpha"
_ADDED_DIR = "pane-beta"


def _process_identity(pid: int) -> str | None:
    """What makes this pid *this* process: its start time together with its argv.

    A bare pid is not an identity, it is a slot. Between a pane reporting its
    shell and this world being torn down the kernel may hand that number to an
    unrelated process, and signalling it would be this test killing a stranger.
    """
    try:
        probe = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    identity = " ".join(probe.stdout.split())
    return identity or None


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _OwnedFrameSandbox:
    """One exclusively allocated engine world: socket dir, config, HOME, sessions.

    `mkdtemp` is the ownership proof. A predictable `/tmp/vcsh<PID>` root that
    starts with `rmtree` proves the opposite: it deletes whatever a stranger
    (or a concurrent run whose PID shares the modulus) left there, and then
    shares one socket namespace with it. The kernel either hands this process a
    fresh directory or the allocation fails.

    The root is resolved because `/tmp` is a symlink on macOS: a pane shell
    reports the physical `$PWD`, so an unresolved root makes every cwd
    assertion compare `/tmp/...` against `/private/tmp/...`.
    """

    def __init__(self, frame: Path, prefix: str) -> None:
        self.binary = frame
        # Short by construction: the session socket path lives under this root
        # and must stay inside sun_path (104 bytes on macOS).
        self.root = Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp")).resolve()
        self.home = self.root / "home"
        self.socket_dir = self.root / "s"
        self.config_dir = self.root / "cfg"
        self.tmp = self.root / "t"
        for path in (self.home, self.socket_dir, self.config_dir / "layouts", self.tmp):
            path.mkdir(parents=True)
        # `default_shell "zsh"` is the shipped value on a zsh host — the product
        # only rewrites it when zsh is missing (`vc_frame_staging`) — so the
        # engine resolves its pane shell here exactly as an install does.
        # `serialization_interval` is the single fixture-only deviation: the
        # engine serializes a session for resurrection every 60s by default,
        # which outlives this whole test, so restore would otherwise be asked
        # about state that was never written. One second brings that write
        # inside the test's life without anyone hand-writing a cache.
        (self.config_dir / "config.kdl").write_text(
            "keybinds clear-defaults=true {}\n"
            'default_shell "zsh"\n'
            "serialization_interval 1\n",
            encoding="utf-8",
        )
        (self.config_dir / "layouts" / "empty.kdl").write_text(
            "layout {\n  pane\n}\n", encoding="utf-8"
        )
        self.sessions: list[str] = []
        # pid -> the fingerprint that pid carried while this test watched it.
        self.pane_processes: dict[int, str] = {}
        self.leftover = ""
        self.teardown_errors: list[str] = []

    @property
    def layout(self) -> Path:
        return self.config_dir / "layouts" / "empty.kdl"

    def session_name(self, suffix: str) -> str:
        """Unique per allocation, so a stale session can never be adopted."""
        return f"{self.root.name}-{suffix}"

    def remember_pane_process(self, pid: int) -> None:
        """Record a pane shell together with the fingerprint identifying it.

        A pid that cannot be fingerprinted while it is demonstrably alive is
        never registered: teardown would have nothing to compare against, and
        an unverifiable pid is exactly the one that must not be signalled.
        """
        identity = _process_identity(pid)
        if identity is None:
            self.teardown_errors.append(
                f"pane shell {pid} could not be fingerprinted while alive"
            )
            return
        self.pane_processes[pid] = identity

    def session_layout_cache(self, session: str) -> Path | None:
        """The engine's own resurrection cache for this session, inside this HOME.

        Discovered rather than reconstructed: the engine derives the path from
        a HOME-relative project cache dir and a client/server contract version
        folder, and a contract bump must not quietly turn this proof into a
        lookup that always misses.
        """
        found = sorted(self.home.glob(f"**/session_info/{session}/session-layout.kdl"))
        return found[0] if found else None

    def env(self, **extra: str) -> dict[str, str]:
        environment = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            # A real daily terminal: the pane shell gets a working line editor
            # instead of the degraded `dumb` path.
            "TERM": "xterm-256color",
            "LANG": "en_US.UTF-8",
            "TMPDIR": str(self.tmp),
            # The pane shell must come from the product's `default_shell`, not
            # from an ambient hint. Pointing SHELL at a non-shell makes that
            # difference visible instead of accidentally correct on a zsh host.
            "SHELL": "/usr/bin/false",
            "VIBECRAFTED_HOME": str(self.home / ".vibecrafted"),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "VC_FRAME_SOCKET_DIR": str(self.socket_dir),
            "ZELLIJ_SOCKET_DIR": str(self.socket_dir),
            "VC_FRAME_CONFIG_DIR": str(self.config_dir),
            "VC_FRAME_CONFIG_FILE": str(self.config_dir / "config.kdl"),
            "VIBECRAFTED_VC_FRAME_BIN": str(self.binary),
        }
        environment.update(extra)
        leaked = sorted(set(_AMBIENT_FRAME_ENV) & environment.keys())
        assert not leaked, f"ambient Frame identity reached the owned engine: {leaked}"
        return environment

    def run(
        self,
        environment: dict[str, str],
        *args: str,
        cwd: Path | None = None,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.binary), *args],
            check=False,
            env=environment,
            cwd=str(cwd or self.root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def create_session(
        self, environment: dict[str, str], session: str, *, cwd: Path
    ) -> None:
        # Registered before the engine runs: a session that half-started still
        # has to be torn down.
        self.sessions.append(session)
        # `--layout`, not `--new-session-with-layout`. Both name a layout for
        # the session `attach --create-background` is about to create, and only
        # one of them arrives: the engine dispatches `Sessions::Attach` in an
        # `else if` arm above the arm that folds `new_session_with_layout` into
        # `opts.layout`, so under an `attach` subcommand that fold is
        # unreachable and the flag is dropped without a word. What answers then
        # is the engine's built-in default layout, whose tabs open command
        # panes ("Shell", "voc") in place of the single pane asked for here.
        # `--layout` is already in `opts` when the attach arm starts the
        # client, so it survives that arm and reaches the new session.
        created = self.run(
            environment,
            "--layout",
            str(self.layout),
            "attach",
            "--create-background",
            session,
            cwd=cwd,
        )
        assert created.returncode == 0, created.stderr
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            listed = self.run(
                environment, "list-sessions", "--no-formatting", "--short"
            )
            if session in listed.stdout.split():
                return
            time.sleep(0.2)
        pytest.fail(f"session {session} never became live")

    def screen(
        self, environment: dict[str, str], session: str, pane_id: str | None
    ) -> str:
        """Best-effort pane contents, used only to explain a failure."""
        target = ["--pane-id", pane_id] if pane_id else []
        dump = self.tmp / "screen.dump"
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            self.run(
                environment,
                "--session",
                session,
                "action",
                "dump-screen",
                "--full",
                *target,
                "--path",
                str(dump),
                timeout=15,
            )
            if dump.exists():
                return dump.read_text(errors="replace")
        return "<no pane dump available>"

    def _quiet(self, environment: dict[str, str], *args: str) -> str:
        try:
            return self.run(environment, *args, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def close(self) -> None:
        """Tear down whatever exists, including a partially started world.

        Never raises: the sandbox has to disappear even when the engine is
        wedged, otherwise a failed run leaks a live server into /tmp. What it
        refuses to do is hide a failure — every refusal and every surprise is
        recorded on `teardown_errors` for the caller to assert on.
        """
        try:
            self._teardown()
        except Exception as error:  # noqa: BLE001 - teardown must not mask a failure
            self.teardown_errors.append(f"teardown raised {error!r}")

    def _teardown(self) -> None:
        environment = dict(self.env())
        for session in reversed(self.sessions):
            self._quiet(environment, "kill-session", session, "--force")
            self._quiet(environment, "delete-session", session, "--force")
        # A partial start can leave a session this test never named. The socket
        # dir belongs to this allocation alone, so clearing it wholesale cannot
        # reach the operator's own Frame.
        self._quiet(environment, "delete-all-sessions", "--yes", "--force")
        self.leftover = self._quiet(environment, "list-sessions", "--no-formatting")
        self._reap_pane_processes()
        live = [
            name
            for name in self.sessions
            if any(
                name in line and "(EXITED" not in line
                for line in self.leftover.splitlines()
            )
        ]
        if live:
            # Deleting a live server's socket directory would strand that
            # server and destroy the evidence of why it survived. Leave the
            # world standing and let the recorded error speak.
            self.teardown_errors.append(f"sessions still live after teardown: {live}")
            return
        try:
            shutil.rmtree(self.root)
        except OSError as error:
            self.teardown_errors.append(f"cannot remove {self.root}: {error}")

    def _reap_pane_processes(self) -> None:
        """Killing the sessions owns these shells; a signal is the last resort.

        Every pane shell here is a child of this world's own server, so tearing
        the sessions down is what should end them, and they are given a bounded
        chance to do exactly that. Whatever is still running afterwards is
        signalled only while it carries the fingerprint recorded when this test
        saw it alive: a pid whose start time or argv has changed is a different
        process wearing a reused number, and it is not this test's to kill.
        """
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(
            _process_is_alive(pid) for pid in self.pane_processes
        ):
            time.sleep(0.2)
        for pid, identity in self.pane_processes.items():
            if _process_identity(pid) != identity:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError) as error:
                self.teardown_errors.append(f"cannot signal pane shell {pid}: {error}")
            else:
                self.teardown_errors.append(
                    f"pane shell {pid} outlived its session and needed SIGKILL"
                )


def _write_frame_probe(home: Path, name: str) -> tuple[Path, Path]:
    """A line a human could type, answered by the pane's own shell.

    The staging file plus rename keeps the reader from ever seeing a half
    written record, and the closing marker makes "complete" explicit.
    """
    probe = home / f"{name}.probe.zsh"
    evidence = home / f"{name}.evidence"
    staging = home / f"{name}.evidence.part"
    probe.write_text(
        "{\n"
        f'  print -r -- "PROBE={name}"\n'
        '  print -r -- "SHELL_PID=$$"\n'
        '  print -r -- "ZSH_VERSION=${ZSH_VERSION:-}"\n'
        '  print -r -- "INTERACTIVE=${options[interactive]}"\n'
        '  print -r -- "TTY=${TTY:-}"\n'
        '  print -r -- "ZDOTDIR=${ZDOTDIR:-}"\n'
        '  print -r -- "CWD=$PWD"\n'
        '  print -r -- "GL=${aliases[gl]}"\n'
        '  print -r -- "STARSHIP_CONFIG=${STARSHIP_CONFIG:-}"\n'
        f'  print -r -- "{_EVIDENCE_END}={name}"\n'
        f"}} > {str(staging)!r}\n"
        f"command mv -f {str(staging)!r} {str(evidence)!r}\n",
        encoding="utf-8",
    )
    return probe, evidence


def _evidence_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            fields[key.strip()] = value
    return fields


def _read_owned_evidence(evidence: Path, timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if evidence.exists():
            text = evidence.read_text(errors="replace")
            if _EVIDENCE_END in text:
                return text
        time.sleep(0.1)
    return None


def _terminal_panes(
    sandbox: _OwnedFrameSandbox, environment: dict[str, str], session: str
) -> dict[str, dict[str, object]]:
    """Every addressable terminal pane the engine itself reports, keyed by pane id.

    `list-panes` is answered straight from the screen thread without a client
    id — unlike `list-tabs` — so it can be read in a session nobody has
    attached to. That is the only reason this test can address the pane the
    session opened for itself.
    """
    listed = sandbox.run(
        environment, "--session", session, "action", "list-panes", "--json"
    )
    if listed.returncode != 0:
        return {}
    document = listed.stdout
    start, end = document.find("["), document.rfind("]")
    if start < 0 or end < start:
        return {}
    try:
        entries = json.loads(document[start : end + 1])
    except json.JSONDecodeError:
        return {}
    panes: dict[str, dict[str, object]] = {}
    for entry in entries:
        if entry.get("is_plugin") or entry.get("is_suppressed"):
            continue
        if entry.get("is_selectable") is False:
            continue
        panes[f"terminal_{entry['id']}"] = entry
    return panes


def _wait_for_panes(
    sandbox: _OwnedFrameSandbox,
    environment: dict[str, str],
    session: str,
    *,
    count: int,
    timeout: float = 30,
) -> list[str]:
    deadline = time.monotonic() + timeout
    panes: dict[str, dict[str, object]] = {}
    while time.monotonic() < deadline:
        panes = _terminal_panes(sandbox, environment, session)
        if len(panes) == count:
            return sorted(panes)
        time.sleep(0.25)
    pytest.fail(
        f"{session} never reported {count} terminal panes; last saw {sorted(panes)}"
    )


def _assert_engine_chose_the_shell(
    sandbox: _OwnedFrameSandbox,
    environment: dict[str, str],
    session: str,
    pane_id: str,
) -> None:
    """Nothing told this pane what to run, so the shell inside it is the engine's.

    `default_shell` is only under test while the pane was left to resolve it.
    A pane carrying a `terminal_command` was handed its program by a layout,
    and a product profile proven on that pane would be a fact about the layout
    instead. This is also how a foreign layout announces itself here: the
    engine's built-in default opens command panes, so it arrives as a command
    where there should be none — a named difference rather than a pane count
    this test would otherwise have to guess at.
    """
    entry = _terminal_panes(sandbox, environment, session).get(pane_id)
    assert entry is not None, f"{pane_id} left the pane list of {session}"
    assert not entry.get("terminal_command"), (
        f"{pane_id} was handed a command by a layout: {entry.get('terminal_command')!r}"
    )


def _new_default_pane(
    sandbox: _OwnedFrameSandbox,
    environment: dict[str, str],
    session: str,
    cwd: Path,
) -> str:
    """Open a pane the daily way: no command, no shell argv.

    Passing `-- /bin/zsh -lic ...` would replace the very thing under test —
    the engine's own default shell and the profile that shell loads. The new
    pane's id comes from the engine's own pane list rather than from parsing
    the command's stdout, so the addressed pane is the pane that appeared.
    """
    before = set(_terminal_panes(sandbox, environment, session))
    created = sandbox.run(
        environment, "--session", session, "action", "new-pane", "--cwd", str(cwd)
    )
    assert created.returncode == 0, created.stderr
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        added = set(_terminal_panes(sandbox, environment, session)) - before
        if len(added) == 1:
            return added.pop()
        time.sleep(0.25)
    pytest.fail(f"new-pane in {session} never reached the engine's pane list")


def _type_in_pane(
    sandbox: _OwnedFrameSandbox,
    environment: dict[str, str],
    session: str,
    probe: Path,
    evidence: Path,
    pane_id: str,
    *,
    attempts: int = 8,
    per_attempt: float = 6.0,
) -> str:
    """Type one line into a named live pane and read back what that shell reports.

    The pane is always named. Input without `--pane-id` is delivered to the
    pane focused *by a client*: the engine resolves it through the requesting
    client's active tab, falls back to the first connected client, and when a
    session has no client at all it logs the keystrokes away. A session created
    with `attach --create-background` has never had a client, so focus-targeted
    input here would be typing into nobody, and retrying it would only take
    longer to prove nothing. `--pane-id` is routed as a write to that pane id
    and never consults a client.

    The engine acknowledges the write request, not the shell's consumption of
    it, so keystrokes aimed at a pane whose shell is still starting are simply
    dropped. Only the input is retried; no assertion is retried or relaxed.
    The leading Return also submits a half-line left by a dropped keystroke and
    resumes a restored pane that came back waiting for one.
    """
    target = ["--pane-id", pane_id]
    line = f"source {shlex.quote(str(probe))}"
    for _ in range(attempts):
        sandbox.run(environment, "--session", session, "action", "write", *target, "13")
        sandbox.run(
            environment, "--session", session, "action", "write-chars", *target, line
        )
        sandbox.run(environment, "--session", session, "action", "write", *target, "13")
        text = _read_owned_evidence(evidence, per_attempt)
        if text is not None:
            return text
    pytest.fail(
        f"pane {pane_id} in {session} never ran {probe.name}; "
        f"screen was:\n{sandbox.screen(environment, session, pane_id)}"
    )


def _wait_for_serialized_panes(
    sandbox: _OwnedFrameSandbox,
    session: str,
    *,
    markers: tuple[str, ...],
    timeout: float = 60,
) -> str:
    """Block until the engine's own resurrection cache names every pane.

    Restore is a claim about durable state, so killing a session before the
    engine has written that state proves nothing: what comes back is whatever
    the last write — or no write at all — happened to hold. The engine
    serializes from its session-metadata loop, every `serialization_interval`,
    on a tick that slows to 5s while no client is attached; each pane's cwd is
    part of what it writes. Waiting for both cwds to appear in that file is
    therefore a wait for an actual current write, not a sleep long enough to
    hope for one.
    """
    deadline = time.monotonic() + timeout
    seen = ""
    while time.monotonic() < deadline:
        cache = sandbox.session_layout_cache(session)
        if cache is not None:
            with contextlib.suppress(OSError):
                seen = cache.read_text(errors="replace")
            if all(marker in seen for marker in markers):
                return seen
        time.sleep(0.25)
    pytest.fail(
        f"{session} was never serialized with {list(markers)}; "
        f"last cache contents were:\n{seen}"
    )


def _assert_live_pane_shell(text: str, *, name: str) -> dict[str, str]:
    """The pane is a real interactive zsh on a real terminal, not an imitation."""
    fields = _evidence_fields(text)
    assert fields.get("PROBE") == name, text
    assert fields.get("ZSH_VERSION"), f"default pane shell is not zsh:\n{text}"
    assert fields.get("INTERACTIVE") == "on", text
    assert fields.get("TTY", "").startswith("/dev/"), text
    assert fields.get("SHELL_PID", "").isdigit(), text
    return fields


def _assert_persistent_pane_process(fields: dict[str, str], *, text: str) -> int:
    """A daily pane keeps its shell; a `zsh -c` payload would already be gone."""
    pid = int(fields["SHELL_PID"])
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pytest.fail(f"pane shell {pid} did not outlive its own report:\n{text}")
    return pid


def _wait_process_gone(pid: int, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_is_alive(pid):
            return True
        time.sleep(0.2)
    return False


def _assert_product_profile(
    fields: dict[str, str], *, home: Path, zdotdir: Path, cwd: Path, text: str
) -> None:
    assert fields.get("ZDOTDIR") == str(zdotdir), text
    assert fields.get("CWD") == str(cwd), text
    assert fields.get("GL") == _PRODUCT_GL, text
    assert fields.get("STARSHIP_CONFIG") == str(
        home / ".config" / "vibecrafted" / "starship.toml"
    ), text


def _stage_frame_home(sandbox: _OwnedFrameSandbox) -> tuple[Path, Path]:
    """Product profile plus the private canary that must never run."""
    home = sandbox.home
    _stage_product_profile(home)
    (home / ".zshrc").write_text(
        'touch "$HOME/PRIVATE_PROFILE_EXECUTED"\nprint PRIVATE_PROFILE_EXECUTED\n'
    )
    return home, home / ".config/vibecrafted/vc-terminal"


@pytest.mark.skipif(_REAL_FRAME is None, reason="no installed vc-frame engine")
def test_real_frame_default_and_restored_panes_run_product_profile() -> None:
    """The real engine's own default shell, in a real PTY, before and after restore.

    Every pane a person actually meets: the one the session opens, the one
    `new-pane` adds, and both of them again once the session has been killed
    and resurrected. None of them is handed a shell argv by this test, and the
    kill only happens after the engine has durably written both panes down.
    """
    assert _REAL_FRAME is not None
    sandbox = _OwnedFrameSandbox(_REAL_FRAME, "vcsh")
    try:
        home, product_zdot = _stage_frame_home(sandbox)
        startup_cwd = home / _STARTUP_DIR
        added_cwd = home / _ADDED_DIR
        startup_cwd.mkdir()
        added_cwd.mkdir()
        environment = sandbox.env(ZDOTDIR=str(product_zdot))
        session = sandbox.session_name("panes")
        sandbox.create_session(environment, session, cwd=startup_cwd)

        # 1. The pane the session itself opened — the daily default shell.
        startup_pane = _wait_for_panes(sandbox, environment, session, count=1)[0]
        _assert_engine_chose_the_shell(sandbox, environment, session, startup_pane)
        startup_probe, startup_evidence = _write_frame_probe(home, "startup")
        startup_text = _type_in_pane(
            sandbox, environment, session, startup_probe, startup_evidence, startup_pane
        )
        startup = _assert_live_pane_shell(startup_text, name="startup")
        _assert_product_profile(
            startup,
            home=home,
            zdotdir=product_zdot,
            cwd=startup_cwd,
            text=startup_text,
        )
        startup_pid = _assert_persistent_pane_process(startup, text=startup_text)
        sandbox.remember_pane_process(startup_pid)
        assert not (home / "PRIVATE_PROFILE_EXECUTED").exists()

        # 2. A pane added to the live session, still without a named shell.
        added_pane = _new_default_pane(sandbox, environment, session, added_cwd)
        added_probe, added_evidence = _write_frame_probe(home, "new-pane")
        added_text = _type_in_pane(
            sandbox, environment, session, added_probe, added_evidence, added_pane
        )
        added = _assert_live_pane_shell(added_text, name="new-pane")
        _assert_product_profile(
            added, home=home, zdotdir=product_zdot, cwd=added_cwd, text=added_text
        )
        added_pid = _assert_persistent_pane_process(added, text=added_text)
        sandbox.remember_pane_process(added_pid)
        assert added_pid != startup_pid, added_text

        # 3. Only now may the session die: the engine has written a cache that
        # names both panes, so what comes back is the work that existed here.
        _wait_for_serialized_panes(sandbox, session, markers=(_STARTUP_DIR, _ADDED_DIR))

        killed = sandbox.run(environment, "kill-session", session)
        assert killed.returncode == 0, killed.stderr
        deadline = time.monotonic() + 15
        listing = ""
        while time.monotonic() < deadline:
            listing = sandbox.run(
                environment, "list-sessions", "--no-formatting"
            ).stdout
            if session in listing and "(EXITED" in listing:
                break
            time.sleep(0.2)
        assert session in listing, listing
        assert "(EXITED" in listing, listing
        # The killed panes really are gone, so what answers next is restored
        # work rather than a survivor of the old session.
        assert _wait_process_gone(startup_pid), startup_pid
        assert _wait_process_gone(added_pid), added_pid

        # 4. The restored panes themselves. Adding a fresh pane here would only
        # re-prove step 2 and leave the restored shells untested.
        restored = sandbox.run(environment, "attach", "--create-background", session)
        assert restored.returncode == 0, restored.stderr
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            live = sandbox.run(environment, "list-sessions", "--no-formatting").stdout
            if session in live and "(EXITED" not in live:
                break
            time.sleep(0.25)
        reports: dict[str, tuple[dict[str, str], str]] = {}
        for index, pane_id in enumerate(
            _wait_for_panes(sandbox, environment, session, count=2), start=1
        ):
            name = f"restore-{index}"
            probe, evidence = _write_frame_probe(home, name)
            text = _type_in_pane(
                sandbox, environment, session, probe, evidence, pane_id
            )
            fields = _assert_live_pane_shell(text, name=name)
            pid = _assert_persistent_pane_process(fields, text=text)
            sandbox.remember_pane_process(pid)
            assert pid not in {startup_pid, added_pid}, text
            reports[fields.get("CWD", "")] = (fields, text)

        assert set(reports) == {str(startup_cwd), str(added_cwd)}, sorted(reports)
        for cwd, (fields, text) in reports.items():
            _assert_product_profile(
                fields,
                home=home,
                zdotdir=product_zdot,
                cwd=Path(cwd),
                text=text,
            )
            assert "PRIVATE_PROFILE_EXECUTED" not in text
        assert not (home / "PRIVATE_PROFILE_EXECUTED").exists()
    finally:
        sandbox.close()
    assert not sandbox.teardown_errors, sandbox.teardown_errors
    assert session not in sandbox.leftover, sandbox.leftover


@pytest.mark.skipif(_REAL_FRAME is None, reason="no installed vc-frame engine")
def test_real_frame_client_env_updates_no_server_but_a_product_server_does() -> None:
    """Two claims that only mean something together.

    A live server keeps the profile it was started with, whatever a later
    client carries — and a server started as the product does hand its panes
    the product profile. Without the second half, the first is equally
    satisfied by a mechanism that never works at all.
    """
    assert _REAL_FRAME is not None
    sandbox = _OwnedFrameSandbox(_REAL_FRAME, "vcso")
    try:
        home, product_zdot = _stage_frame_home(sandbox)
        legacy_zdot = home / "legacy-zdot"
        legacy_zdot.mkdir()
        (legacy_zdot / ".zshrc").write_text('touch "$HOME/LEGACY_LIVE_SERVER"\n')
        work = home / "work"
        work.mkdir()
        legacy_env = sandbox.env(ZDOTDIR=str(legacy_zdot))
        product_env = sandbox.env(ZDOTDIR=str(product_zdot))

        # The server that was already running when the product arrived.
        legacy_session = sandbox.session_name("legacy")
        sandbox.create_session(legacy_env, legacy_session, cwd=work)
        legacy_pane = _wait_for_panes(sandbox, legacy_env, legacy_session, count=1)[0]
        _assert_engine_chose_the_shell(sandbox, legacy_env, legacy_session, legacy_pane)
        legacy_probe, legacy_evidence = _write_frame_probe(home, "legacy-server")
        legacy_text = _type_in_pane(
            sandbox,
            legacy_env,
            legacy_session,
            legacy_probe,
            legacy_evidence,
            legacy_pane,
        )
        legacy = _assert_live_pane_shell(legacy_text, name="legacy-server")
        assert legacy.get("ZDOTDIR") == str(legacy_zdot), legacy_text
        assert legacy.get("GL") == "", legacy_text
        assert legacy.get("STARSHIP_CONFIG") == "", legacy_text
        assert (home / "LEGACY_LIVE_SERVER").exists()
        sandbox.remember_pane_process(
            _assert_persistent_pane_process(legacy, text=legacy_text)
        )

        # A client carrying the product environment into that live server.
        # Its env reaches the engine's argv, never the running server's panes.
        late_pane = _new_default_pane(sandbox, product_env, legacy_session, work)
        late_probe, late_evidence = _write_frame_probe(home, "legacy-late-client")
        late_text = _type_in_pane(
            sandbox,
            product_env,
            legacy_session,
            late_probe,
            late_evidence,
            late_pane,
        )
        late = _assert_live_pane_shell(late_text, name="legacy-late-client")
        assert late.get("ZDOTDIR") == str(legacy_zdot), late_text
        assert late.get("GL") == "", late_text
        assert late.get("STARSHIP_CONFIG") == "", late_text
        sandbox.remember_pane_process(
            _assert_persistent_pane_process(late, text=late_text)
        )

        # The server the product itself starts, in the same owned world.
        product_session = sandbox.session_name("product")
        sandbox.create_session(product_env, product_session, cwd=work)
        product_pane = _wait_for_panes(sandbox, product_env, product_session, count=1)[
            0
        ]
        _assert_engine_chose_the_shell(
            sandbox, product_env, product_session, product_pane
        )
        product_probe, product_evidence = _write_frame_probe(home, "product-server")
        product_text = _type_in_pane(
            sandbox,
            product_env,
            product_session,
            product_probe,
            product_evidence,
            product_pane,
        )
        product = _assert_live_pane_shell(product_text, name="product-server")
        _assert_product_profile(
            product, home=home, zdotdir=product_zdot, cwd=work, text=product_text
        )
        sandbox.remember_pane_process(
            _assert_persistent_pane_process(product, text=product_text)
        )

        assert not (home / "PRIVATE_PROFILE_EXECUTED").exists()
        assert "PRIVATE_PROFILE_EXECUTED" not in legacy_text + late_text + product_text
    finally:
        sandbox.close()
    assert not sandbox.teardown_errors, sandbox.teardown_errors
    for name in (legacy_session, product_session):
        assert name not in sandbox.leftover, sandbox.leftover
