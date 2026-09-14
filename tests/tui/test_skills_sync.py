from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_SYNC = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "skills_sync.sh"
)
INSTALL_SHELL = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "install-shell.sh"
)


def _write_stub_command(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\n{body}" + "\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_skills_sync_dry_run_targets_staged_store_and_touches_no_config(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_file = tmp_path / "sync.log"

    _write_stub_command(fake_bin, "ssh", f'printf "ssh:%s\\n" "$*" >> "{log_file}"')
    _write_stub_command(fake_bin, "rsync", f'printf "rsync:%s\\n" "$*" >> "{log_file}"')

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    result = subprocess.run(
        [
            "bash",
            str(SKILLS_SYNC),
            "fakehost",
            "--source",
            str(REPO_ROOT),
            "--dry-run",
            "--no-verify",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    stdout = result.stdout
    assert (
        "$HOME/.local/share/vibecrafted/tools/vibecrafted-current/"
        "vibecrafted-core/vibecrafted_core/skills" in stdout
    )
    assert (
        "$HOME/.local/share/vibecrafted/tools/vibecrafted-local/"
        "vibecrafted-core/vibecrafted_core/skills" in stdout
    )
    assert "$HOME/.vibecrafted/skills" not in stdout
    assert "_template" not in stdout
    # Skills sync never writes configuration or shell startup files.
    assert ".config" not in stdout
    assert ".zshrc" not in stdout
    assert ".bashrc" not in stdout
    assert not log_file.exists(), "dry-run must not execute ssh or rsync"


@pytest.mark.parametrize("flag", ["--with-shell", "--no-zshrc", "--no-bashrc"])
def test_skills_sync_refuses_retired_shell_helper_flags(
    tmp_path: Path, flag: str
) -> None:
    """Host-shell helper sourcing is retired, and configuration lives only in
    ~/.config/vibecrafted: skills-sync neither pushes a helper into a remote
    config directory nor appends source lines to remote rc files."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_file = tmp_path / "sync.log"

    _write_stub_command(fake_bin, "ssh", f'printf "ssh:%s\\n" "$*" >> "{log_file}"')
    _write_stub_command(fake_bin, "rsync", f'printf "rsync:%s\\n" "$*" >> "{log_file}"')

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    result = subprocess.run(
        [
            "bash",
            str(SKILLS_SYNC),
            "fakehost",
            "--source",
            str(REPO_ROOT),
            flag,
            "--no-verify",
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert f"{flag} is retired" in result.stderr
    assert not log_file.exists(), "a refused flag must not reach ssh or rsync"


def test_install_shell_shim_prefers_current_control_plane_before_home_store(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    config = tmp_path / "config"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(config)

    subprocess.run(
        [
            "bash",
            str(INSTALL_SHELL),
            "--source",
            str(REPO_ROOT),
            "--no-zshrc",
            "--no-bashrc",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    shim = (config / "vibecrafted" / "shell" / "vc-skills.sh").read_text(
        encoding="utf-8"
    )
    tools_path = (
        '"$crafted_tools_home/vibecrafted-current/vibecrafted-core/'
        'vibecrafted_core/runtime/shell/vetcoders.sh"'
    )
    home_path = '"$crafted_home/runtime/shell/vetcoders.sh"'

    assert shim.index(tools_path) < shim.index(home_path)
    assert "vibecrafted-current/runtime/shell/vetcoders.sh" not in shim
    assert str(REPO_ROOT) not in shim
    assert "DEV MODE OPT-IN: live repo override via VIBECRAFTED_ROOT" in shim


def test_install_shell_does_not_write_rc_files_without_consent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = tmp_path / "config"
    home.mkdir()
    zshrc = home / ".zshrc"
    bashrc = home / ".bashrc"
    zshrc.write_text("# zsh user config\n", encoding="utf-8")
    bashrc.write_text("# bash user config\n", encoding="utf-8")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(config)

    result = subprocess.run(
        ["bash", str(INSTALL_SHELL), "--source", str(REPO_ROOT)],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert (config / "vibecrafted" / "shell" / "vc-skills.sh").exists()
    # The shim has one home: no sibling or private config directory is created.
    assert sorted(child.name for child in config.iterdir()) == ["vibecrafted"]
    assert zshrc.read_text(encoding="utf-8") == "# zsh user config\n"
    assert bashrc.read_text(encoding="utf-8") == "# bash user config\n"
    assert "Shell rc files were not changed automatically." in result.stdout


def test_install_shell_writes_rc_files_with_consent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = tmp_path / "config"
    home.mkdir()
    zshrc = home / ".zshrc"
    bashrc = home / ".bashrc"
    legacy_source = (
        '[[ -r "${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh" ]] '
        '&& source "${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh"'
    )
    legacy_block = (
        "# >>> vibecrafted >>>\n"
        'export VETCODERS_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders"\n'
        'if [ -f "$VETCODERS_CONFIG_DIR/vc-skills.sh" ]; then\n'
        f"  {legacy_source}\n"
        "fi\n"
        "# <<< vibecrafted <<<\n"
    )
    zshrc.write_text(f"# zsh user config\n{legacy_block}", encoding="utf-8")
    bashrc.write_text(f"# bash user config\n{legacy_block}", encoding="utf-8")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(config)

    subprocess.run(
        ["bash", str(INSTALL_SHELL), "--source", str(REPO_ROOT), "--write-rc"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    for rcfile in (zshrc, bashrc):
        text = rcfile.read_text(encoding="utf-8")
        assert "vetcoders/vc-skills.sh" not in text
        assert "VETCODERS_CONFIG_DIR" not in text
        assert "# >>> vibecrafted >>>" not in text
        assert text.count("$HOME/.local/bin") == 2
        assert "# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher" in text


def test_install_shell_preserves_unclosed_managed_block(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = tmp_path / "config"
    home.mkdir()
    zshrc = home / ".zshrc"
    original = (
        "# user config\n"
        "# >>> vibecrafted >>>\n"
        'source "$HOME/.config/vetcoders/vc-skills.sh"\n'
        "export KEEP_ME=1\n"
        "alias keep-me=true\n"
    )
    zshrc.write_text(original, encoding="utf-8")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(config)
    env["SHELL"] = "/bin/zsh"

    result = subprocess.run(
        [
            "bash",
            str(INSTALL_SHELL),
            "--source",
            str(REPO_ROOT),
            "--write-rc",
            "--no-bashrc",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert zshrc.read_text(encoding="utf-8") == original
    assert "unclosed Vibecrafted block" in result.stdout
