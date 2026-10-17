"""The runtime python pin at every process boundary.

A process that enters the runtime pins one interpreter (VIBECRAFTED_PYTHON,
the absolute bin/python3 of its generation) and puts the generation's python
door (config/runtime-pin/bin) first on PATH. Shells only inherit it. These
tests cross the real boundaries -- public launcher, terminal entry, headless
worker gate, spawn launcher, hook, nested sh/bash/zsh in every startup mode,
`#!/usr/bin/env python3` -- against a personal zsh profile that prepends a
host python the way Homebrew does, and check that each one reaches the same
interpreter. Outside the runtime python3 stays the user's; an update does not
move a live pin; a missing pin is a clear error.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest
from vibecrafted_core.env_allowlist import filter_headless_worker_env
from vibecrafted_core.runtime_paths import pin_runtime_python

from scripts import vetcoders_install as installer
from tests._runtime_pack_fixture import (
    bind_private_terminal_wrapper,
    private_child_command,
    seed_runtime_pack,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
UTIL_SH = REPO_ROOT / "vibecrafted-core/vibecrafted_core/runtime/scripts/lib/util.sh"
TERMINAL_ENTRY = REPO_ROOT / "scripts/vc-terminal-product-entry.sh"
PROBE = "import sys; print('used=' + sys.executable)"

pytestmark = [
    pytest.mark.skipif(
        sys.version_info < (3, 11),
        reason="needs a 3.11+ interpreter to stand in for generation CPython",
    ),
    pytest.mark.skipif(
        not Path("/bin/zsh").is_file(), reason="zsh is the runtime's shell"
    ),
]


def _hostile_python(directory: Path) -> Path:
    """The user's own python3: it answers HOST and exits 79."""

    directory.mkdir(parents=True, exist_ok=True)
    for name in ("python3", "python"):
        tool = directory / name
        tool.write_text("#!/bin/sh\nprintf 'HOST_PYTHON_SELECTED\\n'\nexit 79\n")
        tool.chmod(0o755)
    return directory / "python3"


def _generation(home: Path, version: str = "4.4.0") -> Path:
    """A generation as the Runtime Pack installs it."""

    generation = home / ".local/share/vibecrafted/releases" / version
    # Framework CPython reports its host path through a bare symlink. Give
    # each fixture its own interpreter identity, preserving exact pin checks.
    venv.EnvBuilder(with_pip=False, symlinks=True).create(generation)
    shutil.copytree(REPO_ROOT / "config/runtime-pin", generation / "config/runtime-pin")
    return generation


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    """HOME whose personal zsh startup puts a host python first at every stage,
    the way ~/.zshenv on the Founder's Mac prepends Homebrew's python."""

    home = tmp_path / "home"
    home.mkdir()
    host_bin = home / "host-python"
    _hostile_python(host_bin)
    for stage in (".zshenv", ".zprofile", ".zshrc", ".zlogin"):
        (home / stage).write_text(
            f'export PATH="{host_bin}:$PATH"\n'
            f"export PERSONAL_{stage[1:].upper()}_RAN=1\n"
        )
    (home / ".bashrc").write_text(f'export PATH="{host_bin}:$PATH"\n')
    return {"home": home, "host_bin": host_bin, "generation": _generation(home)}


def _base_env(world: dict[str, Path], **extra: str) -> dict[str, str]:
    env = {
        "HOME": str(world["home"]),
        "USER": os.environ.get("USER", "runtime"),
        "PATH": f"{world['host_bin']}:/usr/bin:/bin",
        "TERM": "dumb",
        "SHELL": "/bin/zsh",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "VIBECRAFTED_HOME": str(world["home"] / ".vibecrafted"),
    }
    env.update(extra)
    return env


def _entered(world: dict[str, Path], **extra: str) -> dict[str, str]:
    """What a runtime entry exports: the selected generation and its pin."""

    generation = world["generation"]
    return _base_env(
        world,
        VIBECRAFTED_RUNTIME_ROOT=str(generation),
        VIBECRAFTED_PYTHON=str(generation / "bin/python3"),
        **extra,
    )


def _run(
    argv: list[str], env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )


def _used(result: subprocess.CompletedProcess) -> Path:
    assert "HOST_PYTHON_SELECTED" not in result.stdout + result.stderr, (
        result.stdout,
        result.stderr,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("used=")]
    assert lines, (result.returncode, result.stdout, result.stderr)
    return Path(lines[-1].split("used=", 1)[1])


def _assert_pinned(result: subprocess.CompletedProcess) -> None:
    assert _used(result).resolve() == Path(sys.executable).resolve()


def _shell_matrix(home: Path) -> dict[str, list[str]]:
    script = home / "shebang.py"
    script.write_text(f"#!/usr/bin/env python3\n{PROBE}\n")
    script.chmod(0o755)
    probe = home / "probe.py"
    probe.write_text(f"{PROBE}\n")
    typed = f"python3 {probe}"
    return {
        "sh -c": ["/bin/sh", "-c", typed],
        "bash -c": ["/bin/bash", "-c", typed],
        "zsh -c": ["/bin/zsh", "-c", typed],
        "zsh -lc": ["/bin/zsh", "-lc", typed],
        "zsh -ic": ["/bin/zsh", "-ic", typed],
        "zsh -lic": ["/bin/zsh", "-lic", typed],
        "nested zsh -lc in zsh -c": ["/bin/zsh", "-c", f"/bin/zsh -lc '{typed}'"],
        "python name": ["/bin/zsh", "-lc", f"python {probe}"],
        "typed -c": [
            "/bin/zsh",
            "-lc",
            "python3 -c 'import sys; print(\"used=\" + sys.executable)'",
        ],
        "env python3": ["/usr/bin/env", "python3", "-c", PROBE],
        "shebang": [str(script)],
        "shebang from zsh -lc": ["/bin/zsh", "-lc", str(script)],
        # A hook is a command string the agent CLI hands to a shell.
        "hook command": ["/bin/sh", "-c", f"cd / && {typed}"],
    }


def test_headless_worker_env_reaches_the_pin_in_every_shell_mode(
    world: dict[str, Path],
) -> None:
    """The headless gate drops the inherited ZDOTDIR (a secret-free allowlist)
    and installs the guest one: personal startup still runs, then the door is
    back in front of path_helper and of the personal host python."""

    source = _entered(world, ZDOTDIR="/somewhere/else", SECRET_TOKEN="x")
    env = filter_headless_worker_env(source)
    generation = world["generation"]
    door = generation / "config/runtime-pin/bin"
    assert env["PATH"].split(":")[0] == str(door)
    assert env["ZDOTDIR"] == str(generation / "config/runtime-pin/zsh")
    assert env["VIBECRAFTED_USER_ZDOTDIR"] == str(world["home"])
    assert env["VIBECRAFTED_PYTHON"] == str(generation / "bin/python3")
    assert "SECRET_TOKEN" not in env

    for label, argv in _shell_matrix(world["home"]).items():
        result = _run(argv, env, world["home"])
        assert result.returncode == 0, (label, result.stdout, result.stderr)
        _assert_pinned(result)

    personal = _run(
        [
            "/bin/zsh",
            "-lic",
            (
                "print -r -- $PERSONAL_ZSHENV_RAN$PERSONAL_ZPROFILE_RAN"
                "$PERSONAL_ZSHRC_RAN$PERSONAL_ZLOGIN_RAN"
            ),
        ],
        env,
        world["home"],
    )
    assert personal.stdout.strip().splitlines()[-1] == "1111", personal.stderr


def test_guest_zdotdir_follows_a_personal_zdotdir(world: dict[str, Path]) -> None:
    """A user whose ~/.zshenv moves ZDOTDIR keeps their .zshrc loading."""

    home = world["home"]
    personal = home / ".config/zsh"
    personal.mkdir(parents=True)
    (home / ".zshenv").write_text(f'export ZDOTDIR="{personal}"\n')
    (personal / ".zshrc").write_text("export PERSONAL_RELOCATED_ZSHRC_RAN=1\n")
    env = filter_headless_worker_env(_entered(world))
    result = _run(
        [
            "/bin/zsh",
            "-ic",
            f'print -r -- "relocated=$PERSONAL_RELOCATED_ZSHRC_RAN"; python3 -c "{PROBE}"',
        ],
        env,
        home,
    )
    assert "relocated=1" in result.stdout, result.stderr
    _assert_pinned(result)


def test_guest_zdotdir_runs_personal_logout(world: dict[str, Path]) -> None:
    """Guest login shells delegate exit cleanup in the user's own directory."""

    home = world["home"]
    (home / ".zlogout").write_text('print -r -- "logout=$ZDOTDIR"\n')
    env = filter_headless_worker_env(_entered(world))
    # Native zsh runs .zlogout for an interactive login shell.
    result = _run(["/bin/zsh", "-lic", f'python3 -c "{PROBE}"; exit'], env, home)
    assert result.returncode == 0, result.stderr
    assert f"logout={home}" in result.stdout
    _assert_pinned(result)


def test_runtime_pack_preserves_hidden_guest_logout(tmp_path: Path) -> None:
    """The recursive Runtime Pack config copy and source seal retain .zlogout."""

    relative = Path("config/runtime-pin/zsh/.zlogout")
    payload = seed_runtime_pack(tmp_path / "pack")
    assert (payload / relative).read_bytes() == (REPO_ROOT / relative).read_bytes()
    assert installer._distribution_manifest.path_is_included(relative)
    assert not installer._distribution_manifest.path_is_forbidden(relative)


@pytest.mark.parametrize("stage", [".zprofile", ".zshrc", ".zlogin"])
@pytest.mark.parametrize("relocation", ["directory", "unset"])
def test_guest_zdotdir_preserves_native_stage_relocation(
    world: dict[str, Path], stage: str, relocation: str
) -> None:
    """A personal stage owns ZDOTDIR; later stages and logout follow native zsh."""

    home = world["home"]
    initial = home / "initial-zsh"
    relocated = home / "relocated-zsh"
    initial.mkdir()
    relocated.mkdir()
    stages = [".zshenv", ".zprofile", ".zshrc", ".zlogin", ".zlogout"]
    for directory in (initial, relocated, home):
        for filename in stages:
            (directory / filename).write_text(
                f'print -r -- "{filename}=${{ZDOTDIR-$HOME}}"\n'
            )
    change = (
        f'export ZDOTDIR="{relocated}"'
        if relocation == "directory"
        else "unset ZDOTDIR"
    )
    with (initial / stage).open("a") as personal:
        personal.write(f'export PATH="{world["host_bin"]}:$PATH"\n{change}\n')

    native = _run(
        ["/bin/zsh", "-lic", 'print -r -- "final=${ZDOTDIR-$HOME}"'],
        _base_env(world, ZDOTDIR=str(initial)),
        home,
    )
    env = pin_runtime_python(_entered(world, ZDOTDIR=str(initial)))
    pinned = _run(
        [
            "/bin/zsh",
            "-lic",
            f'print -r -- "final=${{ZDOTDIR-$HOME}}"; python3 -c "{PROBE}"; exit',
        ],
        env,
        home,
    )
    assert native.returncode == 0, native.stderr
    assert pinned.returncode == 0, pinned.stderr
    assert [
        line for line in pinned.stdout.splitlines() if not line.startswith("used=")
    ] == native.stdout.splitlines()
    _assert_pinned(pinned)


def test_spawn_launcher_pins_agent_shells(world: dict[str, Path]) -> None:
    """The bash spawn launcher (launcher.sh) carries the same pin as the
    Python gate: door first, guest ZDOTDIR, inherited pin."""

    probe = world["home"] / "probe.py"
    probe.write_text(f"{PROBE}\n")
    script = (
        f"source {str(UTIL_SH)!r}\n"
        "spawn_prepend_agent_tool_paths\n"
        "spawn_pin_runtime_python\n"
        'printf "first=%s\\nzdotdir=%s\\nuser=%s\\n" "${PATH%%:*}" "$ZDOTDIR" '
        '"$VIBECRAFTED_USER_ZDOTDIR"\n'
        f"exec /bin/zsh -lc 'python3 {probe}'\n"
    )
    result = _run(["/bin/bash", "-c", script], _entered(world), world["home"])
    assert result.returncode == 0, result.stderr
    generation = world["generation"]
    assert f"first={generation / 'config/runtime-pin/bin'}" in result.stdout
    assert f"zdotdir={generation / 'config/runtime-pin/zsh'}" in result.stdout
    assert f"user={world['home']}" in result.stdout
    _assert_pinned(result)

    # A product shell ZDOTDIR already restores the door itself and stays.
    product = world["home"] / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    kept = _run(
        [
            "/bin/bash",
            "-c",
            (
                f"source {str(UTIL_SH)!r}; spawn_pin_runtime_python; "
                'printf "zdotdir=%s\\n" "$ZDOTDIR"'
            ),
        ],
        _entered(world, ZDOTDIR=str(product)),
        world["home"],
    )
    assert f"zdotdir={product}\n" in kept.stdout, kept.stderr
    twin = pin_runtime_python(_entered(world, ZDOTDIR=str(product)))
    assert twin["ZDOTDIR"] == str(product)


def test_product_shell_zdotdir_restores_the_door_in_nested_login_shells(
    world: dict[str, Path],
) -> None:
    """Terminal, Frame panes and Quick cmd run with a product ZDOTDIR; the
    staged login files compose the user's own login startup and bring the
    door back in front of it."""

    product = world["home"] / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    product_stages = REPO_ROOT / "config/runtime-pin/zsh-product"
    for stage in (".zshenv", ".zprofile", ".zlogin"):
        shutil.copy2(product_stages / stage, product / stage)
    env = _entered(world, ZDOTDIR=str(product))
    env["PATH"] = f"{world['generation'] / 'config/runtime-pin/bin'}:{env['PATH']}"
    for argv in (
        ["/bin/zsh", "-c", f'python3 -c "{PROBE}"'],
        ["/bin/zsh", "-lc", f'python3 -c "{PROBE}"'],
    ):
        result = _run(argv, env, world["home"])
        assert result.returncode == 0, result.stderr
        _assert_pinned(result)


def test_public_launcher_enters_the_runtime_with_the_door_first(
    world: dict[str, Path],
) -> None:
    generation = world["generation"]
    target = generation / "bin/probe-target"
    target.write_text(
        "#!/bin/bash\n"
        'printf "first=%s\\npin=%s\\n" "${PATH%%:*}" "$VIBECRAFTED_PYTHON"\n'
        f'python3 -c "{PROBE}"\n'
    )
    target.chmod(0o755)
    launcher = world["home"] / ".local/bin/vc-probe"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        installer._runtime_launcher_body(
            generation=generation,
            config_home=world["home"] / ".config",
            crafted_home=world["home"] / ".vibecrafted",
            runtime_home=generation.parent.parent,
            frame_config=world["home"] / ".config/vibecrafted/vc-frame",
            executable=target,
        )
    )
    launcher.chmod(0o755)
    stale_door = (
        world["home"] / ".local/share/vibecrafted/releases/old/config/runtime-pin/bin"
    )
    result = _run(
        [str(launcher)],
        _base_env(
            world,
            PATH=f"{stale_door}:{world['host_bin']}:/usr/bin:/bin",
            VIBECRAFTED_PYTHON="/stale/pin/python3",
        ),
        world["home"],
    )
    assert result.returncode == 0, result.stderr
    assert f"first={generation / 'config/runtime-pin/bin'}" in result.stdout
    assert f"pin={generation / 'bin/python3'}" in result.stdout
    assert str(stale_door) not in result.stdout
    _assert_pinned(result)


def test_terminal_entry_pins_the_terminal_it_opens(world: dict[str, Path]) -> None:
    """vc-terminal entry exports the pin and the door for the native host and
    the shell it starts. A fake native host stands in and reports."""

    generation = world["generation"]
    (generation / "scripts").mkdir()
    entry = generation / "scripts/vc-terminal-product-entry.sh"
    shutil.copy2(TERMINAL_ENTRY, entry)
    (generation / "libexec").mkdir()
    host = generation / "libexec/vc-terminal"
    host.write_text(
        "#!/bin/bash\n"
        'printf "first=%s\\npin=%s\\n" "${PATH%%:*}" "$VIBECRAFTED_PYTHON"\n'
        f'python3 -c "{PROBE}"\n'
    )
    host.chmod(0o755)
    selected_host = bind_private_terminal_wrapper(entry, host)
    config = world["home"] / ".config/vibecrafted/vc-terminal/vc-terminal.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[general]\n")
    result = _run(
        private_child_command(
            ["/bin/bash", str(entry), "-e", "/usr/bin/true"],
            allowed=(selected_host, Path("/usr/libexec/PlistBuddy")),
            fixture_root=generation,
        ),
        _base_env(world),
        world["home"],
    )
    assert result.returncode == 0, result.stderr
    assert f"first={generation / 'config/runtime-pin/bin'}" in result.stdout
    assert f"pin={generation / 'bin/python3'}" in result.stdout
    _assert_pinned(result)


def test_outside_the_runtime_python3_stays_the_users(world: dict[str, Path]) -> None:
    """No selected generation: the gate, the launcher helper and every shell
    leave python3 alone -- no door, no guest ZDOTDIR."""

    plain = _base_env(world, ZDOTDIR=str(world["home"]))
    assert pin_runtime_python(plain) == plain
    env = filter_headless_worker_env(plain)
    assert "ZDOTDIR" not in env
    assert env["PATH"] == plain["PATH"]
    for argv in (
        ["/bin/zsh", "-lc", "python3 --version"],
        ["/bin/bash", "-c", "python3 --version"],
    ):
        result = _run(argv, env, world["home"])
        assert result.returncode == 79, (argv, result.stdout, result.stderr)
        assert "HOST_PYTHON_SELECTED" in result.stdout

    helper = _run(
        [
            "/bin/bash",
            "-c",
            (
                f"source {str(UTIL_SH)!r}; spawn_pin_runtime_python; "
                'printf "path=%s\\nzdotdir=%s\\n" "$PATH" "${ZDOTDIR-}"'
            ),
        ],
        _base_env(world),
        world["home"],
    )
    assert f"path={_base_env(world)['PATH']}\n" in helper.stdout, helper.stderr
    assert "zdotdir=\n" in helper.stdout


def test_an_update_does_not_move_a_live_pin(world: dict[str, Path]) -> None:
    """A newer generation selected later does not replace the interpreter a
    running session already carries; a child without a pin gets its own
    generation's, never 'whatever is active'."""

    old = world["generation"]
    new = _generation(world["home"], "4.5.0")
    (new / "bin/python3").unlink()
    (new / "bin/python3").write_text("#!/bin/sh\necho NEW_GENERATION_PYTHON\n")
    (new / "bin/python3").chmod(0o755)
    live = _entered(world)
    live["VIBECRAFTED_RUNTIME_ROOT"] = str(new)
    env = pin_runtime_python(live)
    assert env["VIBECRAFTED_PYTHON"] == str(old / "bin/python3")
    result = _run(["/bin/zsh", "-lc", f'python3 -c "{PROBE}"'], env, world["home"])
    assert "NEW_GENERATION_PYTHON" not in result.stdout
    assert _used(result) == old / "bin/python3"

    fresh = _base_env(world, VIBECRAFTED_RUNTIME_ROOT=str(old))
    assert pin_runtime_python(fresh)["VIBECRAFTED_PYTHON"] == str(old / "bin/python3")


def test_a_missing_pin_is_a_clear_error_in_every_mode(world: dict[str, Path]) -> None:
    """The door on PATH without its pin refuses with the reason instead of
    falling back to the host 3.9 or to another generation."""

    env = filter_headless_worker_env(_entered(world))
    env.pop("VIBECRAFTED_PYTHON")
    for argv in (
        ["/bin/zsh", "-lc", "python3 --version"],
        ["/bin/sh", "-c", "python3 --version"],
        ["/usr/bin/env", "python3", "--version"],
    ):
        result = _run(argv, env, world["home"])
        assert result.returncode == 127, (argv, result.stdout, result.stderr)
        assert "HOST_PYTHON_SELECTED" not in result.stdout
        assert "no interpreter is pinned: VIBECRAFTED_PYTHON is unset" in result.stderr


def test_both_door_names_are_one_file() -> None:
    door = REPO_ROOT / "config/runtime-pin/bin"
    assert (door / "python").read_bytes() == (door / "python3").read_bytes()
    assert os.access(door / "python", os.X_OK)
    assert os.access(door / "python3", os.X_OK)
    assert sorted(path.name for path in door.iterdir()) == ["python", "python3"]


@pytest.fixture
def aliased_profile(tmp_path: Path) -> dict[str, Path]:
    """Real interactive zsh, inert tools, and only private profile state."""

    home = tmp_path / "home"
    home.mkdir()
    generation = home / "generation"
    (generation / "bin").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "config/runtime-pin", generation / "config/runtime-pin")
    interpreter = generation / "bin/python3"
    interpreter.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$VIBECRAFTED_PYTHON" "$@" >> "$PIN_CAPTURE"\n'
    )
    interpreter.chmod(0o755)
    profile = home / "interactive.zsh"
    shutil.copy2(REPO_ROOT / "config/vc-terminal/interactive.zsh", profile)
    return {"home": home, "generation": generation, "profile": profile}


@pytest.mark.parametrize(
    "alias_case", ["none", "reload", "aliases", "personal-shell", "public", "all"]
)
def test_profile_declarations_parse_with_preexisting_aliases(
    aliased_profile: dict[str, Path], alias_case: str
) -> None:
    """Parse the complete profile before any of its alias cleanup can run."""

    home = aliased_profile["home"]
    generation = aliased_profile["generation"]
    profile = aliased_profile["profile"]
    declarations = re.findall(
        r"(?m)^\s*(?:function\s+)?([A-Za-z_][-A-Za-z0-9_.]*)"
        r"(?:\s*\(\))?\s*\{",
        profile.read_text(),
    )
    public = ["reload", "aliases", "personal-shell"]
    assert set(public + ["python", "python3"]) <= set(declarations)
    names = (
        declarations
        if alias_case == "all"
        else public
        if alias_case == "public"
        else []
        if alias_case == "none"
        else [alias_case]
    )
    # Exercise both conditional declarations with inert init output. Nothing
    # here consults the real user's rc, tools, plugins or runtime generation.
    tools = home / "tools"
    tools.mkdir()
    for name, output in {
        "atuin": "function _atuin_search { :; }; function atuin-search { :; }",
        "zoxide": "function z { :; }",
        "starship": "function starship_precmd { :; }",
    }.items():
        tool = tools / name
        tool.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
        tool.chmod(0o700)
    config = home / ".config/vibecrafted"
    config.mkdir(parents=True)
    (config / "starship.toml").write_text("add_newline = true\n")
    (home / ".zshrc").write_text(
        "(( PERSONAL_LOAD_COUNT += 1 ))\n"
        "alias python='print WRONG_PERSONAL_PYTHON'\n"
        "alias python3='print WRONG_PERSONAL_PYTHON3'\n"
    )
    script = home / "declarations.zsh"
    aliases = "".join(
        f"alias {name}='print -r -- USER_ALIAS_{name}'\n"
        if name in public + ["python", "python3"]
        # Helpers keep forwarding so this test isolates declaration safety
        # from the independent policy for aliases at command invocation.
        else f"alias {name}='\\{name}'\n"
        for name in names
    )
    script.write_text(
        aliases
        + "alias ordinary='print -r -- ORDINARY_ALIAS_SURVIVED'\n"
        + 'source "$PROFILE" || exit 91\n'
        + "[[ $_VC_TERMINAL_PROFILE_LOADED == 1 && -o aliases ]] || exit 92\n"
        + "(( $+functions[_vc_terminal_start_completion] )) || exit 93\n"
        + "(( $+functions[_vc_terminal_atuin_up_or_history] )) || exit 94\n"
        + "initial_cwd=$PWD\ninitial_history=$HISTFILE\n"
        + "'aliases' Profile || exit 95\n"
        # Reload must re-read definitions from disk, twice, while aliases
        # remain present. Quoting deliberately reaches the public function.
        + "function aliases { print STALE_DEFINITION; }\n"
        + "'reload' || exit 96\n'aliases' Profile || exit 97\n"
        + "'reload' || exit 98\n"
        + '[[ $PWD == "$initial_cwd" && $HISTFILE == "$initial_history" ]] || exit 99\n'
        + "python3 'space argument' '' '*?[literal]'\n"
        + "'personal-shell' || exit 100\n"
        + "[[ $PERSONAL_LOAD_COUNT == 1 && -z ${STARSHIP_CONFIG:-} "
        "&& -z ${ATUIN_CONFIG_DIR:-} && -z ${ZDOTDIR:-} ]] || exit 101\n"
        + '[[ $HISTFILE == "$HOME/.zsh_history" ]] || exit 102\n'
        + "python 'after personal'\n"
        + "'reload' || exit 103\n"
        + '[[ $STARSHIP_CONFIG == "$HOME/.config/vibecrafted/starship.toml" ]] || exit 104\n'
        + "ordinary\n"
        + "".join(
            f"[[ ${{aliases[{name}]-}} == 'print -r -- USER_ALIAS_{name}' ]] || exit 105\n"
            if name in public
            else f"[[ ${{aliases[{name}]-}} == '\\{name}' ]] || exit 106\n"
            for name in names
            if name not in ["python", "python3"]
        )
        # An unquoted public command still follows the user's alias. This
        # repair must not silently decide the command-precedence policy.
        + "".join(f"{name}\n" for name in names if name in public)
        + "[[ -o aliases ]] || exit 107\nprint PROFILE_DECLARATIONS_OK\n"
    )
    result = _run(
        ["/bin/zsh", "-dfi", str(script)],
        {
            "HOME": str(home),
            "PATH": f"{tools}:/usr/bin:/bin",
            "TERM": "dumb",
            "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
            "VIBECRAFTED_RUNTIME_ROOT": str(generation),
            "VIBECRAFTED_PYTHON": str(generation / "bin/python3"),
            "VC_TERMINAL_PLUGIN_PREFIXES": str(home / "no-plugins"),
            "PROFILE": str(profile),
            "PIN_CAPTURE": str(home / "calls"),
        },
        home,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "parse error" not in result.stderr
    assert "defining function based on alias" not in result.stderr
    assert "PROFILE_DECLARATIONS_OK" in result.stdout
    assert "ORDINARY_ALIAS_SURVIVED" in result.stdout
    assert "STALE_DEFINITION" not in result.stdout
    assert "WRONG_PERSONAL" not in result.stdout
    assert "re-read the installed product profile" in result.stdout
    for name in names:
        if name in public:
            assert f"USER_ALIAS_{name}" in result.stdout
    assert (home / "calls").read_bytes().split(b"\0") == [
        str(generation / "bin/python3").encode(),
        b"space argument",
        b"",
        b"*?[literal]",
        str(generation / "bin/python3").encode(),
        b"after personal",
        b"",
    ]


def test_profile_declarations_require_alias_safe_syntax() -> None:
    """Guard every declaration, including future nested/conditional helpers."""

    profile = (REPO_ROOT / "config/vc-terminal/interactive.zsh").read_text()
    unsafe = re.findall(
        r"(?m)^\s*(?:function\s+)?[A-Za-z_][-A-Za-z0-9_.]*\s*\(\s*\)",
        profile,
    )
    assert not unsafe, f"Use alias-safe `function name {{ ... }}`: {unsafe}"


def test_aliased_profile_binds_owned_python_and_reloads_exact_arguments(
    aliased_profile: dict[str, Path],
) -> None:
    """Aliases exist before the entire enclosing function is parsed."""

    home = aliased_profile["home"]
    generation = aliased_profile["generation"]
    capture = home / "calls"
    script = home / "probe.zsh"
    shebang = home / "shebang"
    shebang.write_text("#!/usr/bin/env python3\n")
    shebang.chmod(0o755)
    script.write_text(
        "alias python='print -r -- WRONG_PYTHON_ALIAS'\n"
        "alias python3='print -r -- WRONG_PYTHON3_ALIAS'\n"
        "alias ordinary='print -r -- ORDINARY_ALIAS_SURVIVED'\n"
        'source "$PROFILE" || exit 91\n'
        "[[ $_vc_terminal_python_bound == 1 ]] || exit 92\n"
        "[[ $+functions[python] == 1 && $+functions[python3] == 1 ]] || exit 93\n"
        'python "$@"\npython3 "$@"\ncommand python3 "$@"\n'
        '/usr/bin/env python "$@"\n"$HOME/shebang" "$@"\n'
        # Personal aliases may have been restored before a reload.
        "alias python='print -r -- WRONG_RELOADED_ALIAS'\n"
        "alias python3='print -r -- WRONG_RELOADED_ALIAS'\n"
        "reload || exit 94\n"
        'python "$@"\npython3 "$@"\n'
        'ordinary\nprint -r -- "pin=$VIBECRAFTED_PYTHON"\n'
    )
    args = ["-c", "space argument", "", "*?[literal]", "line\nbreak", "$literal"]
    result = _run(
        ["/bin/zsh", "-dfi", str(script), *args],
        {
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "TERM": "dumb",
            "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
            "VIBECRAFTED_RUNTIME_ROOT": str(generation),
            "VIBECRAFTED_PYTHON": str(generation / "bin/python3"),
            "VC_TERMINAL_PLUGIN_PREFIXES": str(home / "no-plugins"),
            "VC_QUICK_CMD": "1",
            "PROFILE": str(aliased_profile["profile"]),
            "PIN_CAPTURE": str(capture),
        },
        home,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "parse error" not in result.stderr
    assert "WRONG_" not in result.stdout + result.stderr
    assert "ORDINARY_ALIAS_SURVIVED" in result.stdout
    assert f"pin={generation / 'bin/python3'}" in result.stdout
    call = [str(generation / "bin/python3"), *args]
    expected = (
        call * 4 + [str(generation / "bin/python3"), str(shebang), *args] + call * 2
    )
    assert capture.read_bytes().split(b"\0") == [s.encode() for s in expected] + [b""]


@pytest.mark.parametrize("selected_root", [False, True])
def test_aliased_profile_without_pin_preserves_aliases_or_refuses_selected_door(
    aliased_profile: dict[str, Path], selected_root: bool
) -> None:
    """No root leaves personal aliases intact; a selected door never guesses."""

    home = aliased_profile["home"]
    generation = aliased_profile["generation"]
    # A usable active generation must not become an implicit replacement pin.
    tools = home / ".local/share/vibecrafted/tools"
    tools.mkdir(parents=True)
    (tools / "vibecrafted-current").symlink_to(generation)
    script = home / "probe.zsh"
    script.write_text(
        "alias python='print -r -- PERSONAL_PYTHON'\n"
        "alias python3='print -r -- PERSONAL_PYTHON3'\n"
        "alias ordinary='print -r -- ORDINARY_ALIAS_SURVIVED'\n"
        'source "$PROFILE" || exit 91\n'
        "reload || exit 92\n"
        "[[ -z ${VIBECRAFTED_PYTHON:-} ]] || exit 93\n"
        "ordinary\npython --version\n"
        'print -r -- "python_status=$?"\n'
        'python3 --version\nprint -r -- "python3_status=$?"\n'
    )
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "TERM": "dumb",
        "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
        "VC_TERMINAL_PLUGIN_PREFIXES": str(home / "no-plugins"),
        "VC_QUICK_CMD": "1",
        "PROFILE": str(aliased_profile["profile"]),
        "PIN_CAPTURE": str(home / "calls"),
    }
    if selected_root:
        env["VIBECRAFTED_RUNTIME_ROOT"] = str(generation)
    result = _run(["/bin/zsh", "-dfi", str(script)], env, home)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "parse error" not in result.stderr
    assert "ORDINARY_ALIAS_SURVIVED" in result.stdout
    assert not (home / "calls").exists()
    if selected_root:
        assert "PERSONAL_PYTHON" not in result.stdout
        assert "python_status=127" in result.stdout
        assert "python3_status=127" in result.stdout
        assert result.stderr.count("no interpreter is pinned") == 2
    else:
        assert "PERSONAL_PYTHON --version" in result.stdout
        assert "PERSONAL_PYTHON3 --version" in result.stdout
        assert "python_status=0" in result.stdout
        assert "python3_status=0" in result.stdout
        assert "no interpreter is pinned" not in result.stderr
