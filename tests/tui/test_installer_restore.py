from __future__ import annotations

import json
import shlex
import subprocess
from argparse import Namespace
from pathlib import Path

from scripts import vetcoders_install as installer


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def test_bundle_uninstall_removes_owned_payload_and_printed_restore_works(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    tools_home = runtime_home / "tools"
    payload = tools_home / "vibecrafted-3.4.0"
    stale_payload = tools_home / "vibecrafted-stale"
    incoming = tools_home / ".incoming-old"
    unrelated = tools_home / "third-party-tool"
    current = tools_home / "vibecrafted-current"
    runtime_bin = runtime_home / "bin"
    uv_tool = home / ".local" / "share" / "uv" / "tools" / "vibecrafted"
    launcher = home / ".local" / "bin" / "vibecrafted"
    helper = home / ".config" / "vibecrafted" / "shell" / "vc-skills.sh"
    zshrc = home / ".zshrc"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools_home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(runtime_bin))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer, "_IS_TTY", False)
    monkeypatch.setattr(installer, "_known_bundle_names", lambda: ["vc-init"])

    skill = payload / "skills" / "vc-init"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# init\n", encoding="utf-8")
    (payload / "VERSION").write_text("3.4.0\n", encoding="utf-8")
    installer.InstallState(
        framework_version="3.4.0",
        skills=["vc-init"],
        runtimes=[],
        launcher_entries=["local-bin/vibecrafted"],
        helper_files=[str(helper)],
    ).save(payload / "skills")
    stale_payload.mkdir(parents=True)
    incoming.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    current.symlink_to(payload)

    _write_executable(launcher)
    helper.parent.mkdir(parents=True)
    helper.write_text("# helper\n", encoding="utf-8")
    zshrc.write_text(
        f"# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher\n{installer._launcher_path_line()}\n",
        encoding="utf-8",
    )
    runtime_bin.mkdir(parents=True)
    _write_executable(runtime_bin / "vc-frame")
    uv_tool.mkdir(parents=True)
    (uv_tool / "receipt.toml").write_text("managed by uv\n", encoding="utf-8")
    crafted_home.mkdir(parents=True, exist_ok=True)
    (crafted_home / "install.log").write_text("log\n", encoding="utf-8")
    (crafted_home / installer.START_HERE_FILE).write_text("guide\n", encoding="utf-8")

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0
    output = capsys.readouterr().out

    assert not current.exists() and not current.is_symlink()
    assert not payload.exists()
    assert not stale_payload.exists()
    assert not incoming.exists()
    assert unrelated.is_dir()
    assert (runtime_bin / "vc-frame").is_file()
    assert uv_tool.is_dir()
    assert not launcher.exists()
    assert not helper.exists()
    assert installer._launcher_path_line() not in zshrc.read_text(encoding="utf-8")
    assert "Removed managed paths:" in output
    assert "Preserved intentionally:" in output
    assert str(unrelated) in output
    assert str(runtime_bin / "vc-frame") in output
    assert str(uv_tool) in output
    assert "make restore" not in output

    restore_line = next(
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("python3 ")
    )
    restore_script = Path(shlex.split(restore_line)[1])
    assert restore_script.is_file()
    restore_manifest = json.loads(
        (restore_script.parent / "restore-manifest.json").read_text(encoding="utf-8")
    )
    assert any(Path(item["path"]) == payload for item in restore_manifest["items"])

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0
    second_output = capsys.readouterr().out
    assert "Nothing to uninstall" in second_output
    assert "Removed managed paths:" not in second_output

    subprocess.run(
        shlex.split(restore_line), check=True, text=True, capture_output=True
    )

    assert payload.is_dir()
    assert current.is_symlink()
    assert current.resolve() == payload
    assert launcher.is_file()
    assert helper.is_file()
    assert installer._launcher_path_line() in zshrc.read_text(encoding="utf-8")
    assert unrelated.is_dir()


def test_uninstall_never_recurses_through_current_link_into_checkout(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    tools_home = home / ".local" / "share" / "vibecrafted" / "tools"
    checkout = tmp_path / "checkout"
    checkout_skill = (
        checkout / "vibecrafted-core" / "vibecrafted_core" / "skills" / "vc-init"
    )
    current = tools_home / "vibecrafted-current"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / ".vibecrafted"))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools_home))
    monkeypatch.setattr(installer, "_IS_TTY", False)
    monkeypatch.setattr(installer, "_known_bundle_names", lambda: ["vc-init"])

    checkout_skill.mkdir(parents=True)
    (checkout_skill / "SKILL.md").write_text("# source checkout\n", encoding="utf-8")
    tools_home.mkdir(parents=True)
    current.symlink_to(checkout)

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0
    output = capsys.readouterr().out

    assert checkout_skill.is_dir()
    assert (checkout_skill / "SKILL.md").is_file()
    assert not current.exists() and not current.is_symlink()
    assert str(checkout / "vibecrafted-core" / "vibecrafted_core" / "skills") in output
    assert "outside the managed tools root" in output
