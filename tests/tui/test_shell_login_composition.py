"""Product shells compose the user's startup environment; nothing is gated.

The product terminal, Frame panes and Quick cmd run `zsh -d` with a product
ZDOTDIR: zsh itself then reads neither the global login files nor the user's
own startup. The product stages therefore restore the native composition --
the global baseline only where `-d` skipped it, then the user's own
.zshenv/.zprofile/.zshrc exactly as zsh would run them without Vibecrafted
(the runtime-pin guest stages), then the pinned generation's python door back
in front. The user's PATH tools work without `personal-shell` in both the
login and the interactive lane; personal-shell stays the explicit release of
the product's interactive pins, and the python pin owns python/python3 inside
product shells only.
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

from scripts import vetcoders_install as installer
from tests._runtime_pack_fixture import seed_runtime_pack

REPO_ROOT = Path(__file__).resolve().parents[2]
ZSH_PRODUCT = REPO_ROOT / "config/runtime-pin/zsh-product"
GUEST_ZSH = REPO_ROOT / "config/runtime-pin/zsh"
USER_RC = REPO_ROOT / "vibecrafted-core/vibecrafted_core/runtime/shell/rc.sh"
QUICK_CMD = (
    REPO_ROOT / "vibecrafted-core/vibecrafted_core/config/vc-frame/vc-quick-cmd.sh"
)
PROBE = "import sys; print('used=' + sys.executable)"

pytestmark = [
    pytest.mark.skipif(
        sys.version_info < (3, 11),
        reason="needs a 3.11+ interpreter to stand in for generation CPython",
    ),
    pytest.mark.skipif(
        not Path("/bin/zsh").is_file(), reason="zsh is the product shell"
    ),
]

LOGIN_STAGES = (".zshenv", ".zprofile", ".zlogin")
PRODUCT_STAGES = (".zshenv", ".zprofile", ".zshrc", ".zlogin")


def _fake_tool(directory: Path, name: str, marker: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tool = directory / name
    tool.write_text(f"#!/bin/sh\nprintf '{marker}=%s\\n' \"$1\"\n")
    tool.chmod(0o755)
    return tool


def _hostile_python(directory: Path) -> Path:
    """The user's own python3: it answers HOST and exits 79."""

    directory.mkdir(parents=True, exist_ok=True)
    for name in ("python3", "python"):
        tool = directory / name
        tool.write_text("#!/bin/sh\nprintf 'HOST_PYTHON_SELECTED\\n'\nexit 79\n")
        tool.chmod(0o755)
    return directory / "python3"


def _generation(home: Path, version: str = "9.9.9") -> Path:
    generation = home / ".local/share/vibecrafted/releases" / version
    venv.EnvBuilder(with_pip=False, symlinks=True).create(generation)
    shutil.copytree(REPO_ROOT / "config/runtime-pin", generation / "config/runtime-pin")
    return generation


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    """HOME whose login startup composes PATH the way a real Mac does: tool
    dirs from .zshenv and .zprofile (with a Homebrew-style host python
    prepend), plus an interactive .zshrc layer that must stay personal."""

    home = tmp_path / "home"
    home.mkdir()
    host_bin = home / "host-python"
    _hostile_python(host_bin)
    login_bin = home / "user-login-bin"
    _fake_tool(login_bin, "user-login-tool", "USER_LOGIN_TOOL_RAN")
    env_bin = home / "user-zshenv-bin"
    _fake_tool(env_bin, "user-env-tool", "USER_ENV_TOOL_RAN")
    interactive_bin = home / "user-interactive-bin"
    _fake_tool(interactive_bin, "user-interactive-tool", "USER_INTERACTIVE_TOOL_RAN")
    (home / ".zshenv").write_text(
        f'export PATH="{env_bin}:$PATH"\nexport PERSONAL_ZSHENV_RAN=1\n'
    )
    (home / ".zprofile").write_text(
        f'export PATH="{login_bin}:{host_bin}:$PATH"\nexport PERSONAL_ZPROFILE_RAN=1\n'
    )
    (home / ".zshrc").write_text(
        f'export PATH="{interactive_bin}:$PATH"\n'
        "export PERSONAL_ZSHRC_RAN=1\n"
        "print -r -- PERSONAL_ZSHRC_EXECUTED\n"
    )
    return {"home": home, "host_bin": host_bin, "generation": _generation(home)}


def _stage_product_zdotdir(home: Path) -> Path:
    """What the installer writes into ~/.config/vibecrafted/vc-terminal.

    The startup stages are the generation's zsh-product files; the product
    interactive profile sits beside them as interactive.zsh (stubbed here
    with a marker, the way the installed copy stands in for the profile).
    """

    product = home / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    for stage in PRODUCT_STAGES:
        shutil.copy2(ZSH_PRODUCT / stage, product / stage)
    (product / "interactive.zsh").write_text("print -r -- PRODUCT_INTERACTIVE_RAN\n")
    return product


def _product_env(world: dict[str, Path], product: Path) -> dict[str, str]:
    """A fresh product shell's inheritance: the bare launchd PATH plus the
    entry exports. No user login PATH has been composed anywhere yet.
    XDG_CONFIG_HOME is pinned into the fixture home: this host exports it
    globally, and it must never redirect the product config tree."""

    generation = world["generation"]
    return {
        "HOME": str(world["home"]),
        "USER": os.environ.get("USER", "composition"),
        "PATH": "/usr/bin:/bin",
        "TERM": "dumb",
        "SHELL": "/bin/zsh",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "XDG_CONFIG_HOME": str(world["home"] / ".config"),
        "ZDOTDIR": str(product),
        "VIBECRAFTED_HOME": str(world["home"] / ".vibecrafted"),
        "VIBECRAFTED_RUNTIME_ROOT": str(generation),
        "VIBECRAFTED_PYTHON": str(generation / "bin/python3"),
    }


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


def _assert_pinned(world: dict[str, Path], result: subprocess.CompletedProcess) -> None:
    assert "HOST_PYTHON_SELECTED" not in result.stdout + result.stderr, (
        result.stdout,
        result.stderr,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("used=")]
    assert lines, (result.returncode, result.stdout, result.stderr)
    assert (
        Path(lines[-1].split("used=", 1)[1]).resolve() == Path(sys.executable).resolve()
    )
    door = world["generation"] / "config/runtime-pin/bin"
    assert result.stdout.splitlines()[0] == f"door={door}"


def test_product_login_shell_composes_the_users_login_path(
    world: dict[str, Path],
) -> None:
    """A user-PATH tool works right after product entry, without personal-shell."""

    home = world["home"]
    product = _stage_product_zdotdir(home)
    result = _run(
        [
            "/bin/zsh",
            "-d",
            "-l",
            "-c",
            (
                "command -v user-login-tool; user-login-tool run; "
                "command -v user-env-tool; user-env-tool run; "
                'print -r -- "zshenv=$PERSONAL_ZSHENV_RAN zprofile=$PERSONAL_ZPROFILE_RAN"'
            ),
        ],
        _product_env(world, product),
        home,
    )
    assert result.returncode == 0, result.stderr
    assert f"{home}/user-login-bin/user-login-tool" in result.stdout
    assert "USER_LOGIN_TOOL_RAN=run" in result.stdout
    assert f"{home}/user-zshenv-bin/user-env-tool" in result.stdout
    assert "USER_ENV_TOOL_RAN=run" in result.stdout
    assert "zshenv=1 zprofile=1" in result.stdout
    # The lane boundary is native: a non-interactive product shell never
    # reads .zshrc, so the personal interactive layer stays out of it.
    assert "PERSONAL_ZSHRC_EXECUTED" not in result.stdout + result.stderr


def test_product_login_shell_keeps_the_door_in_front_of_user_prepends(
    world: dict[str, Path],
) -> None:
    """Composition is not a takeover: after every user prepend the pinned
    door still owns python3 inside the product shell."""

    home = world["home"]
    product = _stage_product_zdotdir(home)
    result = _run(
        [
            "/bin/zsh",
            "-d",
            "-l",
            "-c",
            f'print -r -- "door=${{PATH%%:*}}"; python3 -c "{PROBE}"',
        ],
        _product_env(world, product),
        home,
    )
    assert result.returncode == 0, result.stderr
    _assert_pinned(world, result)


def test_product_interactive_shell_composes_the_personal_zshrc(
    world: dict[str, Path],
) -> None:
    """The Founder's original defect, closed: a PATH declared only in the
    user's ~/.zshrc works right after product entry, without personal-shell.
    The user's file runs first via the guest-ZDOTDIR composition, the product
    interactive profile still runs after it, and nothing VC adds reorders the
    user's own layering -- only the pinned python door stays in front."""

    home = world["home"]
    # A same-named tool in two user dirs: the layer the user put last (their
    # .zshrc) must win, exactly as a native interactive zsh would order it.
    _fake_tool(home / "user-login-bin", "duo-tool", "DUO_FROM_LOGIN")
    _fake_tool(home / "user-interactive-bin", "duo-tool", "DUO_FROM_INTERACTIVE")
    product = _stage_product_zdotdir(home)
    result = _run(
        [
            "/bin/zsh",
            "-d",
            "-l",
            "-i",
            "-c",
            (
                "command -v user-interactive-tool; user-interactive-tool run; "
                "command -v duo-tool; duo-tool probe; "
                'print -r -- "zshrc=$PERSONAL_ZSHRC_RAN"; '
                f'print -r -- "door=${{PATH%%:*}}"; python3 -c "{PROBE}"'
            ),
        ],
        _product_env(world, product),
        home,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    # The user's .zshrc-declared PATH is composed in (the falsifier's bar).
    assert f"{home}/user-interactive-bin/user-interactive-tool" in out
    assert "USER_INTERACTIVE_TOOL_RAN=run" in out
    assert "zshrc=1" in out
    # Native user layering is preserved: the user's interactive dir outranks
    # the user's login dir; nothing VC adds reorders the user's entries.
    assert f"{home}/user-interactive-bin/duo-tool" in out
    assert "DUO_FROM_INTERACTIVE=probe" in out
    assert "DUO_FROM_LOGIN" not in out
    # User file first, product interactive profile after it.
    assert "PERSONAL_ZSHRC_EXECUTED" in out
    assert "PRODUCT_INTERACTIVE_RAN" in out
    assert out.index("PERSONAL_ZSHRC_EXECUTED") < out.index("PRODUCT_INTERACTIVE_RAN")
    # The declared exception, unchanged: the pinned door still owns python
    # inside the product shell, and the foreign python never runs.
    assert "HOST_PYTHON_SELECTED" not in out + result.stderr
    lines = [line for line in out.splitlines() if line.startswith("used=")]
    assert lines, (result.returncode, out, result.stderr)
    assert (
        Path(lines[-1].split("used=", 1)[1]).resolve() == Path(sys.executable).resolve()
    )
    door = world["generation"] / "config/runtime-pin/bin"
    assert f"door={door}" in out


def _path_helper_invocations(result: subprocess.CompletedProcess) -> int:
    """xtrace lines of the /etc/zprofile body's own path_helper call.

    zsh prefixes each traced line with the file it is currently executing, so
    one `+/etc/zprofile:N> /usr/libexec/path_helper ...` line is one run of
    the global login phase, whoever triggered it (native startup or a stage).
    """

    return len(
        re.findall(
            r"^\+/etc/zprofile:\d+> /usr/libexec/path_helper ",
            result.stderr,
            re.MULTILINE,
        )
    )


@pytest.mark.parametrize("dash_d", [True, False], ids=["zsh -d -l", "plain zsh -l"])
def test_product_login_shell_runs_the_global_profile_exactly_once(
    world: dict[str, Path], dash_d: bool
) -> None:
    """The reviewer's measurement, pinned: under `zsh -d -l` (the product
    entry) the global baseline comes from the product stage alone; under a
    plain `zsh -l` on the same ZDOTDIR native zsh already ran /etc/zprofile,
    and the stage must not replay it. Exactly one path_helper run either way,
    and the login PATH floor it provides is present in both modes."""

    etc_zprofile = Path("/etc/zprofile")
    if not etc_zprofile.is_file() or "path_helper" not in etc_zprofile.read_text():
        pytest.skip("this host's /etc/zprofile does not run path_helper")
    paths = [
        line.strip()
        for line in Path("/etc/paths").read_text().splitlines()
        if line.strip()
    ]
    if not paths:
        pytest.skip("this host's /etc/paths is unreadable")
    home = world["home"]
    product = _stage_product_zdotdir(home)
    argv = ["/bin/zsh", "-x"]
    if dash_d:
        argv.append("-d")
    argv += ["-l", "-c", 'print -r -- "path=$PATH"']
    result = _run(argv, _product_env(world, product), home)
    assert result.returncode == 0, result.stderr
    assert _path_helper_invocations(result) == 1, result.stderr
    path_line = next(
        line for line in result.stdout.splitlines() if line.startswith("path=")
    )
    assert paths[0] in path_line.split(":")


def test_product_zshenv_never_replays_the_global_environment() -> None:
    """zsh reads /etc/zshenv on every startup before any ZDOTDIR file, and
    `-d` (GLOBAL_RCS) does not gate it -- only `-f` (RCS) does, and that
    suppresses the product stages too. So whenever the product .zshenv runs,
    /etc/zshenv already ran; sourcing it could only replay it. Stock macOS
    ships no /etc/zshenv, which is why no double-run was ever measured; the
    audit result is pinned structurally so a future host file stays
    single-run."""

    executable = [
        line.split("#", 1)[0]
        for line in (ZSH_PRODUCT / ".zshenv").read_text().splitlines()
    ]
    assert not any("/etc/zshenv" in line for line in executable)


def test_product_composition_follows_a_personal_zdotdir(
    world: dict[str, Path],
) -> None:
    """A user whose ~/.zshenv moves ZDOTDIR keeps native semantics: later
    login stages read from the moved directory, the product profile stays."""

    home = world["home"]
    personal = home / "personal-zsh"
    relocated_bin = home / "relocated-bin"
    _fake_tool(relocated_bin, "relocated-tool", "RELOCATED_TOOL_RAN")
    (home / ".zshenv").write_text(f'export ZDOTDIR="{personal}"\n')
    personal.mkdir()
    (personal / ".zprofile").write_text(f'export PATH="{relocated_bin}:$PATH"\n')
    product = _stage_product_zdotdir(home)
    result = _run(
        [
            "/bin/zsh",
            "-d",
            "-l",
            "-i",
            "-c",
            (
                "command -v relocated-tool; relocated-tool run; "
                'print -r -- "user=$VIBECRAFTED_USER_ZDOTDIR"'
            ),
        ],
        _product_env(world, product),
        home,
    )
    assert "PRODUCT_INTERACTIVE_RAN" in result.stdout
    assert f"{relocated_bin}/relocated-tool" in result.stdout
    assert "RELOCATED_TOOL_RAN=run" in result.stdout
    assert f"user={personal}" in result.stdout


def test_quick_cmd_regenerates_the_composing_login_stages(
    world: dict[str, Path],
) -> None:
    """The Quick cmd lane rebuilds its ZDOTDIR from the same zsh-product
    stages, so an emergency command sees the user's login PATH too."""

    home = world["home"]
    generation = world["generation"]
    # The script resolves the runtime core inside the generation; point the
    # fixture generation at the real core like an installed pack does.
    os.symlink(REPO_ROOT / "vibecrafted-core", generation / "vibecrafted-core")
    env = _product_env(world, home / ".config/vibecrafted/vc-terminal")
    env.pop("ZDOTDIR")
    result = _run(["/bin/bash", str(QUICK_CMD)], env, home)
    assert result.returncode == 0, (result.stdout, result.stderr)
    quick_zdot = home / ".vibecrafted/shell/quick-cmd"
    for stage in LOGIN_STAGES:
        assert (quick_zdot / stage).read_bytes() == (ZSH_PRODUCT / stage).read_bytes()
    probe_env = {**env, "ZDOTDIR": str(quick_zdot)}
    probe = _run(
        ["/bin/zsh", "-d", "-l", "-c", "command -v user-login-tool"],
        probe_env,
        home,
    )
    assert f"{home}/user-login-bin/user-login-tool" in probe.stdout, (
        probe.stdout,
        probe.stderr,
    )


def test_user_rc_dotfile_composes_without_restricting(tmp_path: Path) -> None:
    """The user's own shell stays the user's: with the one managed source
    line, a foreign python/uv still resolves to the user's binary, no
    ZDOTDIR/PYTHON* appears, and the launcher directory joins PATH."""

    home = tmp_path / "home"
    home.mkdir()
    host_bin = home / "host-python"
    _hostile_python(host_bin)
    _fake_tool(home / ".local/bin", "user-local-tool", "USER_LOCAL_TOOL_RAN")
    (home / ".zprofile").write_text(f'export PATH="{host_bin}:$PATH"\n')
    (home / ".zshrc").write_text(f'source "{USER_RC}"\n')
    env = {
        "HOME": str(home),
        "USER": os.environ.get("USER", "composition"),
        "PATH": "/usr/bin:/bin",
        "TERM": "dumb",
        "SHELL": "/bin/zsh",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    result = _run(
        [
            "/bin/zsh",
            "-l",
            "-i",
            "-c",
            (
                "command -v python3; python3 --version || true; "
                "command -v user-local-tool; "
                'print -r -- "zdot=${ZDOTDIR-UNSET} pyhome=${PYTHONHOME-UNSET} '
                'pypath=${PYTHONPATH-UNSET}"'
            ),
        ],
        env,
        home,
    )
    assert result.returncode == 0, result.stderr
    assert f"{host_bin}/python3" in result.stdout
    assert "HOST_PYTHON_SELECTED" in result.stdout
    assert f"{home}/.local/bin/user-local-tool" in result.stdout
    assert "zdot=UNSET pyhome=UNSET pypath=UNSET" in result.stdout


def test_user_rc_never_outranks_the_users_own_resolution(tmp_path: Path) -> None:
    """The reviewer's counterexample, closed: the user's own python3/uv live in
    a user directory that PATH already resolves first, and ~/.local/bin carries
    its own copies. Sourcing rc.sh must leave the user's winner untouched —
    the launcher bin joins only at the END of PATH, and a VC-only tool still
    becomes available through it (composition, not takeover)."""

    home = tmp_path / "home"
    home.mkdir()
    preferred_bin = home / "preferred"
    _fake_tool(preferred_bin, "python3", "PREFERRED_PYTHON3")
    _fake_tool(preferred_bin, "uv", "PREFERRED_UV")
    local_bin = home / ".local" / "bin"
    _fake_tool(local_bin, "python3", "LOCAL_BIN_PYTHON3")
    _fake_tool(local_bin, "uv", "LOCAL_BIN_UV")
    _fake_tool(local_bin, "vc-guest-tool", "VC_GUEST_TOOL_RAN")
    (home / ".zprofile").write_text(f'export PATH="{preferred_bin}:$PATH"\n')
    (home / ".zshrc").write_text(f'source "{USER_RC}"\n')
    env = {
        "HOME": str(home),
        "USER": os.environ.get("USER", "composition"),
        "PATH": "/usr/bin:/bin",
        "TERM": "dumb",
        "SHELL": "/bin/zsh",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    result = _run(
        [
            "/bin/zsh",
            "-l",
            "-i",
            "-c",
            (
                "command -v python3; python3 probe; command -v uv; uv probe; "
                "command -v vc-guest-tool; "
                'print -r -- "path=$PATH"'
            ),
        ],
        env,
        home,
    )
    assert result.returncode == 0, result.stderr
    assert f"{preferred_bin}/python3" in result.stdout
    assert "PREFERRED_PYTHON3=probe" in result.stdout
    assert "LOCAL_BIN_PYTHON3" not in result.stdout
    assert f"{preferred_bin}/uv" in result.stdout
    assert "PREFERRED_UV=probe" in result.stdout
    assert "LOCAL_BIN_UV" not in result.stdout
    assert f"{local_bin}/vc-guest-tool" in result.stdout
    path_line = next(
        line for line in result.stdout.splitlines() if line.startswith("path=")
    )
    assert path_line.index(str(preferred_bin)) < path_line.index(str(local_bin))


def test_user_rc_dotfile_parses_as_posix_sh_and_zsh() -> None:
    for parser in ("/bin/sh", "/bin/bash", "/bin/zsh"):
        if not Path(parser).exists():
            continue
        result = subprocess.run(
            [parser, "-n", str(USER_RC)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, (parser, result.stderr)


def test_runtime_pack_preserves_hidden_product_startup_stages(tmp_path: Path) -> None:
    """The recursive Runtime Pack config copy and source seal retain the
    dotfile startup stages the product ZDOTDIR is staged from."""

    payload = seed_runtime_pack(tmp_path / "pack")
    for stage in PRODUCT_STAGES:
        relative = Path("config/runtime-pin/zsh-product") / stage
        assert (payload / relative).read_bytes() == (REPO_ROOT / relative).read_bytes()
        assert installer._distribution_manifest.path_is_included(relative)
        assert not installer._distribution_manifest.path_is_forbidden(relative)
