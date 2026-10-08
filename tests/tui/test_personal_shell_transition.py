"""Deliberate personal-shell restores user Atuin/Starship without wrapping source."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ENTRY = (
    Path(__file__).resolve().parents[2] / "config/alacritty/launch-primary-shell.zsh"
)

_PROBE = (
    'print -r -- "ATUIN_CONFIG_DIR=${ATUIN_CONFIG_DIR-UNSET}"; '
    'print -r -- "ATUIN_DATA_DIR=${ATUIN_DATA_DIR-UNSET}"; '
    'print -r -- "ATUIN_DB_PATH=${ATUIN_DB_PATH-UNSET}"; '
    'print -r -- "STARSHIP_CONFIG=${STARSHIP_CONFIG-UNSET}"; '
    'print -r -- "_ZO_DATA_DIR=${_ZO_DATA_DIR-UNSET}"; '
    'print -r -- "STARSHIP_CACHE=${STARSHIP_CACHE-UNSET}"; '
    'print -r -- "ZDOTDIR=${ZDOTDIR-UNSET}"; '
    'print -r -- "HISTFILE=${HISTFILE-UNSET}"; '
    'print -r -- "KEYMAP=${_VC_ATUIN_KEYMAP-UNSET}"; '
    'print -r -- "PERSONAL_FLAG=${_VC_TERMINAL_PERSONAL_SHELL-UNSET}"; '
    'print -r -- "VC_FRAME_CONFIG_DIR=${VC_FRAME_CONFIG_DIR-UNSET}"; '
    "print -r -- READY"
)


def _stage_product_profile(tmp_path: Path) -> Path:
    root = ENTRY.parents[2]
    product = tmp_path / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    shutil.copy2(
        root / "config/vc-terminal/interactive.zsh", product / "interactive.zsh"
    )
    shutil.copy2(ENTRY, product / "launch-primary-shell.zsh")
    shutil.copy2(
        root / "config/starship.toml",
        tmp_path / ".config/vibecrafted/starship.toml",
    )
    (product / ".zshrc").write_text(
        'source "$HOME/.config/vibecrafted/vc-terminal/launch-primary-shell.zsh"\n'
    )
    aliases = tmp_path / ".config/vibecrafted/shell/aliases"
    shutil.copytree(
        root / "vibecrafted-core/vibecrafted_core/runtime/shell/aliases",
        aliases,
    )
    product_atuin = tmp_path / ".config/vibecrafted/atuin"
    product_atuin.mkdir(parents=True)
    (product_atuin / "config.toml").write_text(
        "enter_accept = false\n# product keymap: enter=edit\n"
    )
    personal_atuin = tmp_path / ".config/atuin"
    personal_atuin.mkdir(parents=True)
    (personal_atuin / "config.toml").write_text("enter_accept = true\n")
    (tmp_path / ".zshrc").write_text(
        "print -r -- PERSONAL_PROFILE_EXECUTED\n"
        'eval "$(atuin init zsh)"\n'
        'eval "$(starship init zsh)"\n'
        'eval "$(zoxide init zsh)"\n'
    )
    return product


def _write_fake_tools(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorder = tmp_path / "tool-init.env"
    atuin = bin_dir / "atuin"
    atuin.write_text(
        "#!/bin/sh\n"
        f'printf "atuin ATUIN_CONFIG_DIR=%s\\n" "${{ATUIN_CONFIG_DIR-UNSET}}" >> {str(recorder)!r}\n'
        f'printf "atuin ATUIN_DATA_DIR=%s\\n" "${{ATUIN_DATA_DIR-UNSET}}" >> {str(recorder)!r}\n'
        f'printf "atuin ATUIN_DB_PATH=%s\\n" "${{ATUIN_DB_PATH-UNSET}}" >> {str(recorder)!r}\n'
        'if [ "$1" = init ]; then\n'
        '  cfg="${ATUIN_CONFIG_DIR:-$HOME/.config/atuin}/config.toml"\n'
        '  if [ -f "$cfg" ] && grep -q \'enter_accept *= *true\' "$cfg"; then\n'
        "    printf 'typeset -g _VC_ATUIN_KEYMAP=enter-accept\\n'\n"
        "  else\n"
        "    printf 'typeset -g _VC_ATUIN_KEYMAP=enter-edit\\n'\n"
        "  fi\n"
        "fi\n"
    )
    atuin.chmod(0o755)
    for name in ("starship", "zoxide"):
        tool = bin_dir / name
        tool.write_text(
            "#!/bin/sh\n"
            f'printf "{name} STARSHIP_CONFIG=%s\\n" "${{STARSHIP_CONFIG-UNSET}}" >> {str(recorder)!r}\n'
            f'printf "{name} _ZO_DATA_DIR=%s\\n" "${{_ZO_DATA_DIR-UNSET}}" >> {str(recorder)!r}\n'
            'if [ "$1" = init ]; then\n'
            "  printf ':\\n'\n"
            "fi\n"
        )
        tool.chmod(0o755)
    return bin_dir


def _zsh_profile(
    tmp_path: Path,
    script: str,
    *,
    path: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    product_zdot = tmp_path / ".config/vibecrafted/vc-terminal"
    environment = {
        "HOME": str(tmp_path),
        "PATH": path,
        "TERM": "dumb",
        "VIBECRAFTED_HOME": str(tmp_path / ".vibecrafted"),
        "ZDOTDIR": str(product_zdot),
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


def test_isolated_default_keeps_product_atuin_and_skips_personal_zshrc(
    tmp_path: Path,
) -> None:
    _stage_product_profile(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    result = _zsh_profile(
        tmp_path,
        ('source "$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"; ' + _PROBE),
        path=f"{bin_dir}:/usr/bin:/bin",
    )
    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    assert "PERSONAL_PROFILE_EXECUTED" not in result.stdout + result.stderr
    assert f"ATUIN_CONFIG_DIR={tmp_path / '.config/vibecrafted/atuin'}" in result.stdout
    assert f"ATUIN_DATA_DIR={tmp_path / '.vibecrafted/shell/atuin'}" in result.stdout
    assert (
        f"ATUIN_DB_PATH={tmp_path / '.vibecrafted/shell/history.db'}" in result.stdout
    )
    assert (
        f"STARSHIP_CONFIG={tmp_path / '.config/vibecrafted/starship.toml'}"
        in result.stdout
    )
    assert f"_ZO_DATA_DIR={tmp_path / '.vibecrafted/shell/zoxide'}" in result.stdout
    assert f"ZDOTDIR={tmp_path / '.config/vibecrafted/vc-terminal'}" in result.stdout
    assert f"HISTFILE={tmp_path / '.vibecrafted/shell/zsh_history'}" in result.stdout
    assert "KEYMAP=enter-edit" in result.stdout
    assert "PERSONAL_FLAG=UNSET" in result.stdout
    recorded = (tmp_path / "tool-init.env").read_text()
    assert (
        f"atuin ATUIN_CONFIG_DIR={tmp_path / '.config/vibecrafted/atuin'}" in recorded
    )
    assert "atuin ATUIN_CONFIG_DIR=UNSET" not in recorded


def test_source_home_zshrc_is_not_the_personal_shell_transition(
    tmp_path: Path,
) -> None:
    """Bare source runs the file; product pins still win. Do not wrap source."""

    _stage_product_profile(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    result = _zsh_profile(
        tmp_path,
        (
            'source "$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"; '
            'source "$HOME/.zshrc"; ' + _PROBE
        ),
        path=f"{bin_dir}:/usr/bin:/bin",
    )
    assert result.returncode == 0, result.stderr
    assert "PERSONAL_PROFILE_EXECUTED" in result.stdout
    assert f"ATUIN_CONFIG_DIR={tmp_path / '.config/vibecrafted/atuin'}" in result.stdout
    assert f"ATUIN_DATA_DIR={tmp_path / '.vibecrafted/shell/atuin'}" in result.stdout
    assert "KEYMAP=enter-edit" in result.stdout
    assert "PERSONAL_FLAG=UNSET" in result.stdout
    assert f"ZDOTDIR={tmp_path / '.config/vibecrafted/vc-terminal'}" in result.stdout


def test_personal_shell_releases_product_pins_and_loads_personal_keymap(
    tmp_path: Path,
) -> None:
    _stage_product_profile(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    result = _zsh_profile(
        tmp_path,
        (
            'source "$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"; '
            "(( $+functions[personal-shell] )) || exit 61; "
            "personal-shell; " + _PROBE
        ),
        path=f"{bin_dir}:/usr/bin:/bin",
    )
    assert result.returncode == 0, result.stderr
    assert "PERSONAL_PROFILE_EXECUTED" in result.stdout
    assert "ATUIN_CONFIG_DIR=UNSET" in result.stdout
    assert "ATUIN_DATA_DIR=UNSET" in result.stdout
    assert "ATUIN_DB_PATH=UNSET" in result.stdout
    assert "STARSHIP_CONFIG=UNSET" in result.stdout
    assert "_ZO_DATA_DIR=UNSET" in result.stdout
    assert "STARSHIP_CACHE=UNSET" in result.stdout
    assert "ZDOTDIR=UNSET" in result.stdout
    assert f"HISTFILE={tmp_path / '.zsh_history'}" in result.stdout
    assert "KEYMAP=enter-accept" in result.stdout
    assert "PERSONAL_FLAG=1" in result.stdout
    assert (
        f"VC_FRAME_CONFIG_DIR={tmp_path / '.config/vibecrafted/vc-frame'}"
        in result.stdout
    )
    recorded = (tmp_path / "tool-init.env").read_text().splitlines()
    product_config = f"atuin ATUIN_CONFIG_DIR={tmp_path / '.config/vibecrafted/atuin'}"
    personal_config = "atuin ATUIN_CONFIG_DIR=UNSET"
    assert product_config in recorded
    assert personal_config in recorded
    assert recorded.index(product_config) < recorded.index(personal_config)


def test_personal_shell_without_zshrc_still_releases_pins(tmp_path: Path) -> None:
    _stage_product_profile(tmp_path)
    (tmp_path / ".zshrc").unlink()
    bin_dir = _write_fake_tools(tmp_path)
    result = _zsh_profile(
        tmp_path,
        (
            'source "$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"; '
            "personal-shell; " + _PROBE
        ),
        path=f"{bin_dir}:/usr/bin:/bin",
    )
    assert result.returncode == 0, result.stderr
    assert "product tool pins were released" in result.stderr
    assert "ATUIN_CONFIG_DIR=UNSET" in result.stdout
    assert "ZDOTDIR=UNSET" in result.stdout
    assert "KEYMAP=enter-edit" in result.stdout
    assert "PERSONAL_PROFILE_EXECUTED" not in result.stdout
