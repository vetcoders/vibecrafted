"""Read-only product paths and installer-only unpublished host adaptation."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from vibecrafted_core.frontier_assets import vc_frame_config_source
from vibecrafted_core.vc_frame_delivery import vc_frame_user_config_dir
from vibecrafted_core.vc_frame_staging import (
    materialize_vc_frame_config,
    resolve_clipboard_command,
    resolve_pane_shell,
    substitute_pane_shell,
)


def test_substitute_pane_shell_only_exact_command_zsh() -> None:
    text = 'pane command="zsh"\npane command="zsh-lookalike"\n'
    out = substitute_pane_shell(text, "bash")
    assert 'command="bash"' in out
    assert 'command="zsh-lookalike"' in out
    assert 'command="zsh"' not in out.replace("zsh-lookalike", "")


def test_substitute_noop_when_shell_is_zsh() -> None:
    text = 'command="zsh"'
    assert substitute_pane_shell(text, "zsh") == text


@pytest.mark.parametrize("present", [False, True])
def test_product_path_resolution_is_read_only_and_ignores_alternate_roots(
    tmp_path: Path, monkeypatch, present: bool
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = home / ".config/vibecrafted/vc-frame"
    if present:
        config.mkdir(parents=True)
        (config / "config.kdl").write_text("copy_on_select true\n")
    monkeypatch.setenv("HOME", str(home))
    for name in ("XDG_CONFIG_HOME", "VC_FRAME_CONFIG_DIR", "VIBECRAFTED_RUNTIME_ROOT"):
        monkeypatch.setenv(name, str(tmp_path / "foreign"))
    before = {p: p.stat().st_mtime_ns for p in home.rglob("*")}
    for _ in range(3):
        assert vc_frame_user_config_dir() == config
        assert vc_frame_user_config_dir(home) == config
    assert {p: p.stat().st_mtime_ns for p in home.rglob("*")} == before
    assert not (tmp_path / "foreign").exists()


def test_materialize_complete_physical_tree_preserves_source(tmp_path: Path) -> None:
    source = vc_frame_config_source()
    before = {
        p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    destination = tmp_path / "candidate/generated/vc-frame"
    materialize_vc_frame_config(
        source, destination, pane_shell="zsh", clipboard_command="pbcopy"
    )
    assert {
        p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
    } == before
    assert {
        p.relative_to(destination): p.read_bytes()
        for p in destination.rglob("*")
        if p.is_file()
    } == before
    assert not any(p.is_symlink() for p in destination.rglob("*"))
    for name in (
        "vc-composer.sh",
        "pane-python",
        "vc-agent-workshop.py",
        "vc-start-here.py",
    ):
        assert os.access(destination / name, os.X_OK)
    assert (destination / "layouts/operator.kdl").is_file()
    assert (destination / "themes").is_dir()


@pytest.mark.parametrize("blocked", ["existing", "sealed", "symlink"])
def test_materializer_refuses_published_or_aliased_destination(
    tmp_path: Path, blocked: str
) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    destination = root / "generated/vc-frame"
    if blocked == "existing":
        destination.mkdir(parents=True)
        (destination / "config.kdl").write_text("user bytes\n")
    elif blocked == "sealed":
        (root / "runtime-manifest.json").write_text("{}\n")
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "generated").symlink_to(outside, target_is_directory=True)
    before = {
        p: (p.readlink() if p.is_symlink() else p.read_bytes() if p.is_file() else None)
        for p in tmp_path.rglob("*")
    }
    with pytest.raises(OSError, match="already exists|sealed runtime|symlink"):
        materialize_vc_frame_config(
            vc_frame_config_source(),
            destination,
            pane_shell="bash",
            clipboard_command=None,
        )
    assert {
        p: (p.readlink() if p.is_symlink() else p.read_bytes() if p.is_file() else None)
        for p in tmp_path.rglob("*")
    } == before


@pytest.mark.parametrize("missing", ["source", "config"])
def test_materializer_missing_source_leaves_destination_absent(
    tmp_path: Path, missing: str
) -> None:
    source = tmp_path / "source"
    if missing == "config":
        source.mkdir()
    destination = tmp_path / "candidate/generated/vc-frame"
    with pytest.raises(OSError, match="source"):
        materialize_vc_frame_config(
            source, destination, pane_shell="sh", clipboard_command=None
        )
    assert not destination.exists()


def test_pane_shell_substitution_without_zsh(tmp_path: Path, monkeypatch) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    bash = shutil.which("bash")
    assert bash is not None
    (fake_bin / "bash").symlink_to(bash)
    monkeypatch.delenv("SHELL", raising=False)
    shell = resolve_pane_shell(str(fake_bin))
    clipboard = resolve_clipboard_command(str(fake_bin))
    assert shell == "bash"
    assert clipboard is None
    destination = tmp_path / "candidate/generated/vc-frame"
    materialize_vc_frame_config(
        vc_frame_config_source(),
        destination,
        pane_shell=shell,
        clipboard_command=clipboard,
    )
    kdl = "\n".join(p.read_text() for p in destination.rglob("*.kdl"))
    assert 'command="bash"' in kdl
    for token in (
        'command="zsh"',
        'default_shell "zsh"',
        "exec zsh -l",
        "exec /bin/zsh -l",
        'copy_command "pbcopy"',
        "pbcopy <",
    ):
        assert token not in kdl
