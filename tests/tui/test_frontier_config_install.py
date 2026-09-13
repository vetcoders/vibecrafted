"""W2-B: frontier parity + zombie cleanup."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = (
    REPO
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "install-frontier-config.sh"
)


def test_frontier_install_leaves_vc_frame_to_delivery_owner(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_PREFER_REPO_VC_FRAME"] = "1"
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--source", str(REPO)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    root = home / ".config" / "vetcoders" / "frontier"
    assert (root / "starship.toml").is_symlink()
    assert (root / "atuin" / "config.toml").is_symlink()
    assert not (root / "vc-frame").exists()


def test_frontier_install_never_traverses_vc_frame_parent_symlinks(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_PREFER_REPO_VC_FRAME"] = "1"
    sentinel = tmp_path / "checkout-config"
    layouts = sentinel / "layouts"
    themes = sentinel / "themes"
    layouts.mkdir(parents=True)
    themes.mkdir()
    dashboard = layouts / "dashboard.kdl"
    theme = themes / "vibecrafted-ivory.kdl"
    dashboard.write_text("tracked dashboard\n", encoding="utf-8")
    theme.write_text("tracked theme\n", encoding="utf-8")
    frontier = home / ".config" / "vetcoders" / "frontier" / "vc-frame"
    frontier.mkdir(parents=True)
    (frontier / "layouts").symlink_to(layouts)
    (frontier / "themes").symlink_to(themes)

    proc = subprocess.run(
        ["bash", str(SCRIPT), "--source", str(REPO)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert dashboard.is_file() and not dashboard.is_symlink()
    assert dashboard.read_text(encoding="utf-8") == "tracked dashboard\n"
    assert theme.is_file() and not theme.is_symlink()
    assert theme.read_text(encoding="utf-8") == "tracked theme\n"
    assert list(sentinel.rglob("*.bak.*")) == []


def test_frontier_removes_dangling_zellij_links(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_PREFER_REPO_VC_FRAME"] = "1"
    zellij = home / ".config" / "vetcoders" / "frontier" / "zellij" / "layouts"
    zellij.mkdir(parents=True)
    zombie = zellij / "operator.kdl"
    zombie.symlink_to("./config/zellij/layouts/operator.kdl")  # dangling relative
    healthy_dir = home / ".config" / "vetcoders" / "frontier" / "keep"
    healthy_dir.mkdir(parents=True)
    healthy = healthy_dir / "ok.txt"
    healthy.write_text("keep\n", encoding="utf-8")
    mtime = healthy.stat().st_mtime
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--source", str(REPO)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert not zombie.exists()
    assert healthy.is_file()
    assert healthy.stat().st_mtime == mtime


def test_frontier_materializes_legacy_backup_symlinks(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_PREFER_REPO_VC_FRAME"] = "1"
    frontier = home / ".config" / "vetcoders" / "frontier"
    frontier.mkdir(parents=True)
    readable_source = tmp_path / "checkout-starship.toml"
    readable_source.write_text("format = 'legacy'\n", encoding="utf-8")
    readable_backup = frontier / "starship.toml.bak.20260730"
    readable_backup.symlink_to(readable_source)
    dangling_backup = frontier / "atuin.toml.bak.20260730"
    dangling_backup.symlink_to(tmp_path / "deleted-checkout.toml")

    proc = subprocess.run(
        ["bash", str(SCRIPT), "--source", str(REPO)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert readable_backup.is_file() and not readable_backup.is_symlink()
    assert readable_backup.read_text(encoding="utf-8") == "format = 'legacy'\n"
    assert dangling_backup.is_file() and not dangling_backup.is_symlink()
    assert "legacy_symlink_target=" in dangling_backup.read_text(encoding="utf-8")
