"""Product shells compose the user's login environment; personal rc stays opt-in.

The product terminal, Frame panes and Quick cmd run `zsh -d` with a product
ZDOTDIR: zsh itself then reads neither the global /etc stages nor the user's
own startup files. The product login stages therefore restore the native login
composition -- the global baseline `-d` skips, then the user's own
.zshenv/.zprofile exactly as zsh would without Vibecrafted (the runtime-pin
guest stages), then the pinned generation's python door back in front. The
user's PATH tools work without `personal-shell`; the interactive ~/.zshrc
stays behind that explicit command, and the python pin owns python/python3
inside product shells only.
"""

from __future__ import annotations

import os
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

PRODUCT_STAGES = (".zshenv", ".zprofile", ".zlogin")


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
    """What the installer writes into ~/.config/vibecrafted/vc-terminal."""

    product = home / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    for stage in PRODUCT_STAGES:
        shutil.copy2(ZSH_PRODUCT / stage, product / stage)
    (product / ".zshrc").write_text("print -r -- PRODUCT_ZSHRC_RAN\n")
    return product


def _product_env(world: dict[str, Path], product: Path) -> dict[str, str]:
    """A fresh product shell's inheritance: the bare launchd PATH plus the
    entry exports. No user login PATH has been composed anywhere yet."""

    generation = world["generation"]
    return {
        "HOME": str(world["home"]),
        "USER": os.environ.get("USER", "composition"),
        "PATH": "/usr/bin:/bin",
        "TERM": "dumb",
        "SHELL": "/bin/zsh",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
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


def test_product_interactive_shell_never_runs_the_personal_zshrc(
    world: dict[str, Path],
) -> None:
    """The interactive layer stays product-owned: personal ~/.zshrc (and the
    PATH it adds) loads only through the explicit personal-shell command."""

    home = world["home"]
    product = _stage_product_zdotdir(home)
    result = _run(
        [
            "/bin/zsh",
            "-d",
            "-l",
            "-i",
            "-c",
            (
                "command -v user-login-tool; "
                "command -v user-interactive-tool || print -r -- INTERACTIVE_TOOL_ABSENT; "
                'print -r -- "zshrc=${PERSONAL_ZSHRC_RAN-UNSET}"'
            ),
        ],
        _product_env(world, product),
        home,
    )
    assert "PRODUCT_ZSHRC_RAN" in result.stdout
    assert "PERSONAL_ZSHRC_EXECUTED" not in result.stdout + result.stderr
    assert "zshrc=UNSET" in result.stdout
    assert f"{home}/user-login-bin/user-login-tool" in result.stdout
    assert "INTERACTIVE_TOOL_ABSENT" in result.stdout


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
    assert "PRODUCT_ZSHRC_RAN" in result.stdout
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
    for stage in PRODUCT_STAGES:
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


def test_user_rc_dotfile_parses_as_posix_sh_and_zsh() -> None:
    for parser in ("/bin/sh", "/bin/bash", "/bin/zsh"):
        if not Path(parser).exists():
            continue
        result = subprocess.run(
            [parser, "-n", str(USER_RC)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, (parser, result.stderr)


def test_runtime_pack_preserves_hidden_product_login_stages(tmp_path: Path) -> None:
    """The recursive Runtime Pack config copy and source seal retain the
    dotfile login stages the product ZDOTDIR is staged from."""

    payload = seed_runtime_pack(tmp_path / "pack")
    for stage in PRODUCT_STAGES:
        relative = Path("config/runtime-pin/zsh-product") / stage
        assert (payload / relative).read_bytes() == (REPO_ROOT / relative).read_bytes()
        assert installer._distribution_manifest.path_is_included(relative)
        assert not installer._distribution_manifest.path_is_forbidden(relative)
