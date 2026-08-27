from __future__ import annotations

import json
import os
import subprocess
from argparse import Namespace
from contextlib import nullcontext
from pathlib import Path

import pytest

from scripts import vetcoders_install as installer


@pytest.fixture(autouse=True)
def _isolate_uninstall_from_live_runtime(monkeypatch) -> None:
    """A unit-test HOME must never inspect or mutate the host's real LaunchAgent."""
    monkeypatch.setattr(installer, "_runtime_loaded_service_home", lambda: None)
    monkeypatch.setattr(installer, "_runtime_service_snapshot", lambda _home: None)
    monkeypatch.setattr(installer, "_darwin_process_ids", tuple)

    def materialize(root: Path) -> None:
        source = root / "vibecrafted-core/vibecrafted_core/config/vc-frame"
        for destination in (
            root / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame",
        ):
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "config.kdl").write_text(
                (source / "config.kdl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (destination / "layouts").mkdir()
            (destination / "themes").mkdir()
            (destination / "vc-composer.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(installer, "_materialize_vc_frame_generation", materialize)
    monkeypatch.setattr(
        installer,
        "load_source_provenance",
        lambda _root: {
            "schema": "vibecrafted.source-provenance.v2",
            "owner_repo": "vetcoders/vibecrafted",
            "source_revision": "1" * 40,
            "payload": {
                "schema": "vibecrafted.distribution-tree.v1",
                "algorithm": "sha256-path-mode-content-v1",
                "tree_sha256": "2" * 64,
                "entry_count": 1,
            },
        },
    )
    monkeypatch.setattr(
        installer,
        "_write_runtime_generation_manifest",
        lambda root, **_kwargs: (root / "runtime-manifest.json").write_text(
            "{}\n", encoding="utf-8"
        ),
    )
    monkeypatch.setattr(
        installer, "_runtime_generation_payload_errors", lambda _root: []
    )


def _write_executable(path: Path, body: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body or "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def _runtime_pack_fixture(root: Path) -> tuple[Path, Path, Path]:
    payload = root / "runtime-pack"
    for name in (
        "vibecrafted",
        "loct",
        "loctree-mcp",
        "aicx",
        "aicx-mcp",
        "prview",
        "screenscribe",
        "vc-server",
        "vc-server-supervisor",
        "vc-start",
        "vc-workflow",
    ):
        _write_executable(payload / "bin" / name)
    _write_executable(payload / "vibecrafted-core/vibecrafted_core/deck/vibecrafted")
    (payload / "VERSION").write_text("9.9.9+g12345678\n", encoding="utf-8")
    terminal_root = payload / "config/vc-terminal"
    (terminal_root / "themes").mkdir(parents=True)
    (terminal_root / "vibecrafted.toml").write_text("[window]\n", encoding="utf-8")
    (terminal_root / "themes/dark.toml").write_text(
        "[colors.primary]\nbackground = '#000000'\n", encoding="utf-8"
    )
    frame_config = payload / "vibecrafted-core/vibecrafted_core/config/vc-frame"
    frame_config.mkdir(parents=True)
    (frame_config / "config.kdl").write_text("// frame\n", encoding="utf-8")
    _write_executable(
        payload / "scripts/vc-frame-product-entry.sh",
        "#!/usr/bin/env bash\npin_darwin_socket_dir() { :; }\n"
        'exec "$VIBECRAFTED_VC_FRAME_BIN" "$@"\n',
    )
    shell = payload / "vibecrafted-core/vibecrafted_core/runtime/shell"
    shell.mkdir(parents=True)
    (shell / "vetcoders.sh").write_text("# shell\n", encoding="utf-8")
    skills = payload / "vibecrafted-core/vibecrafted_core/skills"
    for name in ("vc-audit", "vc-implement"):
        skill = skills / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    (skills / "VERIFICATION_RULE.md").write_text(
        "# Verification rule\n", encoding="utf-8"
    )
    _write_executable(payload / "config/alacritty/launch-primary-shell.zsh")
    terminal_host = root / "Vibecrafted.app/Contents/Helpers/vc-terminal"
    frame_helper = root / "Vibecrafted.app/Contents/Helpers/vc-frame"
    _write_executable(terminal_host)
    _write_executable(frame_helper)
    return payload, terminal_host, frame_helper


def test_runtime_pack_installer_and_uninstaller_round_trip_from_one_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    runtime_home = home / ".local/share/vibecrafted"
    launcher_home = home / ".local/bin"
    crafted_home = home / ".vibecrafted"
    config_home = home / ".config"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher_home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    teardown_calls: list[tuple[bool, Path | None]] = []

    def teardown(
        _shared_home: Path, *, dry_run: bool, app_root: Path | None = None
    ) -> tuple[str, ...]:
        teardown_calls.append((dry_run, app_root))
        return ("terminate receipted app",)

    monkeypatch.setattr(installer, "_teardown_owned_runtime_for_uninstall", teardown)
    payload, terminal_host, frame_helper = _runtime_pack_fixture(tmp_path)

    launcher_home.mkdir(parents=True)
    original_launcher = launcher_home / "vc-start"
    original_launcher.write_text("operator-owned\n", encoding="utf-8")
    original_screenscribe_target = (
        home / ".local/share/uv/tools/screenscribe/bin/screenscribe"
    )
    _write_executable(original_screenscribe_target, "#!/bin/sh\necho original\n")
    original_screenscribe = launcher_home / "screenscribe"
    original_screenscribe.symlink_to(original_screenscribe_target)
    operator_skill = home / ".codex/skills/vc-audit"
    operator_skill.parent.mkdir(parents=True)
    operator_skill.write_text("operator-owned skill\n", encoding="utf-8")
    unrelated_skill = home / ".codex/skills/operator-private"
    unrelated_skill.write_text("preserve me\n", encoding="utf-8")
    app_root = terminal_host.parents[2]
    install_args = Namespace(
        payload_root=str(payload),
        app_root=str(app_root),
        terminal_host=str(terminal_host),
        frame_helper=str(frame_helper),
    )

    assert installer.cmd_runtime_install(install_args) == 0
    installed = json.loads(capsys.readouterr().out)
    generation = runtime_home / "releases/9.9.9+g12345678"
    assert Path(installed["root"]) == generation
    assert Path(installed["frame"]) == generation / "libexec/vc-frame"
    assert "pin_darwin_socket_dir" in (generation / "bin/vc-frame").read_text(
        encoding="utf-8"
    )
    assert (generation / "libexec/vc-frame").read_bytes() == frame_helper.read_bytes()
    assert (generation / "bin/vc-terminal").read_bytes() == terminal_host.read_bytes()
    assert (runtime_home / installer.RUNTIME_INSTALL_RECEIPT).is_file()
    current = runtime_home / "tools/vibecrafted-current"
    assert current.is_symlink()
    assert current.resolve() == generation.resolve()
    assert "VIBECRAFTED_RUNTIME_ROOT=" in original_launcher.read_text(encoding="utf-8")
    product_config = config_home / "vibecrafted"
    assert (product_config / "vc-frame/config.kdl").is_file()
    assert (product_config / "terminal-policy.toml").read_text(encoding="utf-8") == (
        "[window]\n"
    )
    terminal_entry = (product_config / "terminal-entry.toml").read_text(
        encoding="utf-8"
    )
    assert str(product_config / "terminal-policy.toml") in terminal_entry
    assert str(product_config / "terminal-theme.toml") in terminal_entry
    assert str(generation / "config/vc-terminal/vibecrafted.toml") not in terminal_entry
    for runtime in installer.STANDARD_VIEW_RUNTIMES:
        for skill_name in ("vc-audit", "vc-implement"):
            view = home / f".{runtime}/skills/{skill_name}"
            assert view.is_symlink()
            assert view.resolve() == (
                generation / "vibecrafted-core/vibecrafted_core/skills" / skill_name
            )
        assert (home / f".{runtime}/skills/VERIFICATION_RULE.md").is_file()
    for runtime, commands in installer.MARBLES_COMMANDS_BY_RUNTIME.items():
        for command in commands:
            assert installer.AGENT_COMMAND_MARKER in (
                home / f".{runtime}/commands/{command}"
            ).read_text(encoding="utf-8")
    state = json.loads(
        (crafted_home / installer.STATE_FILE).read_text(encoding="utf-8")
    )
    assert state["framework_version"] == "9.9.9+g12345678"
    assert state["skills"] == ["vc-audit", "vc-implement"]
    assert state["runtimes"] == installer.STANDARD_VIEW_RUNTIMES

    # The app calls the installer on every launch. Reconciliation must retain
    # first-install ownership so a later reset still returns to baseline.
    assert installer.cmd_runtime_install(install_args) == 0
    capsys.readouterr()

    (crafted_home / "runtime-created-state").write_text("owned\n", encoding="utf-8")
    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=True, emit_result=True)) == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "dry-run"
    assert "terminate receipted app" in preview["actions"]
    assert generation.is_dir()
    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True)) == 0
    )
    removed = json.loads(capsys.readouterr().out)
    assert removed["status"] == "removed"
    assert "terminate receipted app" in removed["actions"]
    assert teardown_calls == [
        (True, app_root.resolve()),
        (False, app_root.resolve()),
    ]
    assert original_launcher.read_text(encoding="utf-8") == "operator-owned\n"
    assert original_screenscribe.is_symlink()
    assert original_screenscribe.readlink() == original_screenscribe_target
    assert operator_skill.read_text(encoding="utf-8") == "operator-owned skill\n"
    assert unrelated_skill.read_text(encoding="utf-8") == "preserve me\n"
    assert not (home / ".agents").exists()
    assert not (home / ".claude").exists()
    assert not (home / ".codex/commands").exists()
    assert not runtime_home.exists()
    assert not crafted_home.exists()
    assert not config_home.exists()
    # .local predated the install in this fixture because it carries an
    # operator-owned ScreenScribe target. The installer must preserve it.
    assert (home / ".local").is_dir()
    assert app_root.exists()


def test_runtime_pack_uninstall_prunes_only_created_empty_xdg_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    runtime_home = home / ".local/share/vibecrafted"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / ".local/bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / ".vibecrafted"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setattr(
        installer, "_teardown_owned_runtime_for_uninstall", lambda *_args, **_kwargs: []
    )
    payload, terminal_host, frame_helper = _runtime_pack_fixture(tmp_path)
    args = Namespace(
        payload_root=str(payload),
        app_root=str(terminal_host.parents[2]),
        terminal_host=str(terminal_host),
        frame_helper=str(frame_helper),
    )

    assert installer.cmd_runtime_install(args) == 0
    capsys.readouterr()
    assert (home / ".local/share/vibecrafted").is_dir()
    assert (home / ".config/vibecrafted").is_dir()

    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True)) == 0
    )
    capsys.readouterr()

    assert not (home / ".local").exists()
    assert not (home / ".config").exists()
    assert not (home / ".agents").exists()
    assert not (home / ".claude").exists()
    assert not (home / ".codex").exists()


def test_runtime_pack_refuses_missing_required_agent_foundation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(home / "runtime"))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    payload, terminal_host, frame_helper = _runtime_pack_fixture(tmp_path)
    (payload / "bin/prview").unlink()

    with pytest.raises(RuntimeError, match=r"Runtime Pack is incomplete: .*bin/prview"):
        installer.cmd_runtime_install(
            Namespace(
                payload_root=str(payload),
                app_root=str(terminal_host.parents[2]),
                terminal_host=str(terminal_host),
                frame_helper=str(frame_helper),
            )
        )

    assert not (home / "bin/vibecrafted").exists()


def test_runtime_pack_uninstall_preserves_locally_modified_managed_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(home / "runtime"))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setattr(
        installer, "_teardown_owned_runtime_for_uninstall", lambda *_args, **_kwargs: []
    )
    payload, terminal_host, frame_helper = _runtime_pack_fixture(tmp_path)
    args = Namespace(
        payload_root=str(payload),
        app_root=str(terminal_host.parents[2]),
        terminal_host=str(terminal_host),
        frame_helper=str(frame_helper),
    )
    assert installer.cmd_runtime_install(args) == 0
    capsys.readouterr()
    launcher = home / "bin/vc-start"
    launcher.write_text("operator changed this after install\n", encoding="utf-8")

    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True)) == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "conflict"
    assert str(launcher) in result["conflicts"]
    assert (
        launcher.read_text(encoding="utf-8") == "operator changed this after install\n"
    )
    assert (home / "runtime/releases/9.9.9+g12345678").is_dir()
    assert (home / "runtime" / installer.RUNTIME_INSTALL_RECEIPT).is_file()


def test_runtime_pack_uninstall_refuses_modified_agent_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    runtime_home = home / "runtime"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setattr(
        installer, "_teardown_owned_runtime_for_uninstall", lambda *_args, **_kwargs: []
    )
    payload, terminal_host, frame_helper = _runtime_pack_fixture(tmp_path)
    args = Namespace(
        payload_root=str(payload),
        app_root=str(terminal_host.parents[2]),
        terminal_host=str(terminal_host),
        frame_helper=str(frame_helper),
    )
    assert installer.cmd_runtime_install(args) == 0
    capsys.readouterr()

    projection = home / ".codex/skills/vc-audit"
    projection.unlink()
    projection.symlink_to(home / "operator-owned-target")

    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True)) == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "conflict"
    assert str(projection) in result["conflicts"]
    assert projection.is_symlink()
    assert runtime_home.is_dir()


def test_runtime_pack_install_refuses_symlinked_agent_projection_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "outside-agent-home"
    runtime_home = home / "runtime"
    home.mkdir()
    outside.mkdir()
    (home / ".agents").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    payload, terminal_host, frame_helper = _runtime_pack_fixture(tmp_path)
    launcher = home / "bin/vc-start"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("operator-owned\n", encoding="utf-8")
    args = Namespace(
        payload_root=str(payload),
        app_root=str(terminal_host.parents[2]),
        terminal_host=str(terminal_host),
        frame_helper=str(frame_helper),
    )

    with pytest.raises(
        RuntimeError, match="runtime projection ancestor must not be a symlink"
    ):
        installer.cmd_runtime_install(args)

    assert not any(outside.iterdir())
    assert (home / ".agents").is_symlink()
    assert (runtime_home / installer.RUNTIME_INSTALL_RECEIPT).is_file()


def test_interrupted_runtime_pack_install_leaves_receipt_for_clean_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    runtime_home = home / "runtime"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setattr(
        installer, "_teardown_owned_runtime_for_uninstall", lambda *_args, **_kwargs: []
    )
    payload, terminal_host, frame_helper = _runtime_pack_fixture(tmp_path)
    args = Namespace(
        payload_root=str(payload),
        app_root=str(terminal_host.parents[2]),
        terminal_host=str(terminal_host),
        frame_helper=str(frame_helper),
    )
    original = installer._write_runtime_owned_file

    def interrupt_during_agent_projection(
        path: Path, *args: object, **kwargs: object
    ) -> None:
        if path == home / ".claude/skills/VERIFICATION_RULE.md":
            raise RuntimeError("injected onboarding interruption")
        original(path, *args, **kwargs)

    monkeypatch.setattr(
        installer, "_write_runtime_owned_file", interrupt_during_agent_projection
    )
    with pytest.raises(RuntimeError, match="injected onboarding interruption"):
        installer.cmd_runtime_install(args)

    receipt = runtime_home / installer.RUNTIME_INSTALL_RECEIPT
    assert receipt.is_file()
    monkeypatch.setattr(installer, "_write_runtime_owned_file", original)
    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True)) == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "removed"
    assert not runtime_home.exists()
    assert not (home / ".agents").exists()
    assert not (home / ".claude").exists()
    assert not (home / ".codex").exists()


def test_interrupted_projection_publish_restores_checkpointed_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    runtime_home = home / "runtime"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setattr(
        installer, "_teardown_owned_runtime_for_uninstall", lambda *_args, **_kwargs: []
    )
    payload, terminal_host, frame_helper = _runtime_pack_fixture(tmp_path)
    current = runtime_home / "tools/vibecrafted-current"
    current.parent.mkdir(parents=True)
    current.write_text("operator-owned current marker\n", encoding="utf-8")
    args = Namespace(
        payload_root=str(payload),
        app_root=str(terminal_host.parents[2]),
        terminal_host=str(terminal_host),
        frame_helper=str(frame_helper),
    )
    original_symlink = installer._atomic_symlink

    def interrupt_publish(_target: Path, path: Path) -> None:
        if path == current:
            raise RuntimeError("injected projection publication interruption")
        original_symlink(_target, path)

    monkeypatch.setattr(installer, "_atomic_symlink", interrupt_publish)
    with pytest.raises(
        RuntimeError, match="injected projection publication interruption"
    ):
        installer.cmd_runtime_install(args)

    receipt_path = runtime_home / installer.RUNTIME_INSTALL_RECEIPT
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    backup = Path(receipt["backups"][str(current)])
    assert backup.read_text(encoding="utf-8") == "operator-owned current marker\n"
    assert not current.exists()

    monkeypatch.setattr(installer, "_atomic_symlink", original_symlink)
    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True)) == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "removed"
    assert current.read_text(encoding="utf-8") == "operator-owned current marker\n"


def test_runtime_pack_uninstall_rejects_projection_path_injected_into_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    runtime_home = home / "runtime"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    payload, terminal_host, frame_helper = _runtime_pack_fixture(tmp_path)
    args = Namespace(
        payload_root=str(payload),
        app_root=str(terminal_host.parents[2]),
        terminal_host=str(terminal_host),
        frame_helper=str(frame_helper),
    )
    assert installer.cmd_runtime_install(args) == 0
    capsys.readouterr()

    receipt_path = runtime_home / installer.RUNTIME_INSTALL_RECEIPT
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["owned_symlinks"][str(home / ".ssh/config")] = next(
        iter(receipt["owned_symlinks"].values())
    )
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(RuntimeError, match="receipt symlink escapes managed roots"):
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True))
    assert receipt_path.is_file()


def test_runtime_pack_uninstall_rejects_tampered_backup_before_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    runtime_home = home / "runtime"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    teardown_called = False

    def mark_teardown(*_args, **_kwargs) -> list[str]:
        nonlocal teardown_called
        teardown_called = True
        return []

    monkeypatch.setattr(
        installer, "_teardown_owned_runtime_for_uninstall", mark_teardown
    )
    payload, terminal_host, frame_helper = _runtime_pack_fixture(tmp_path)
    launcher = home / "bin/vc-start"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("operator-owned\n", encoding="utf-8")
    args = Namespace(
        payload_root=str(payload),
        app_root=str(terminal_host.parents[2]),
        terminal_host=str(terminal_host),
        frame_helper=str(frame_helper),
    )
    assert installer.cmd_runtime_install(args) == 0
    capsys.readouterr()

    receipt_path = runtime_home / installer.RUNTIME_INSTALL_RECEIPT
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["backups"][str(launcher)] = str(tmp_path / "outside-backup")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(RuntimeError, match="backup path escapes backup root"):
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True))

    assert not teardown_called
    assert launcher.read_text(encoding="utf-8") != "operator-owned\n"
    assert (runtime_home / "releases/9.9.9+g12345678").is_dir()
    assert receipt_path.is_file()


def test_runtime_pack_uninstall_rejects_symlinked_backup_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    runtime_home = home / "runtime"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    payload, terminal_host, frame_helper = _runtime_pack_fixture(tmp_path)
    launcher = home / "bin/vc-start"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("operator-owned\n", encoding="utf-8")
    args = Namespace(
        payload_root=str(payload),
        app_root=str(terminal_host.parents[2]),
        terminal_host=str(terminal_host),
        frame_helper=str(frame_helper),
    )
    assert installer.cmd_runtime_install(args) == 0
    capsys.readouterr()

    receipt_path = runtime_home / installer.RUNTIME_INSTALL_RECEIPT
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    destination = next(iter(receipt["backups"]))
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped_parent = runtime_home / ".installer-backups/escaped"
    escaped_parent.symlink_to(outside)
    receipt["backups"][destination] = str(escaped_parent / "payload")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(RuntimeError, match="backup path escapes backup root"):
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True))
    assert receipt_path.is_file()


def _setup_installed_surface(
    tmp_path: Path, monkeypatch
) -> tuple[Path, Path, Path, Path, Path]:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    codex_skills = home / ".codex" / "skills"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    monkeypatch.setattr(installer, "_IS_TTY", False)

    store_path.mkdir(parents=True)
    codex_skills.mkdir(parents=True)

    skill_dir = store_path / "vc-init"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# test\n", encoding="utf-8")

    state = installer.InstallState(
        framework_version="9.9.9",
        skills=["vc-init"],
        runtimes=["codex"],
        shell_helpers=True,
    )
    state.save(store_path)

    (codex_skills / "vc-init").symlink_to(skill_dir)

    helper_file = installer._helper_target_path()
    helper_file.parent.mkdir(parents=True, exist_ok=True)
    helper_file.write_text("# helper shim\n", encoding="utf-8")

    zshrc = home / ".zshrc"
    path_line = installer._launcher_path_line()
    zshrc.write_text(
        f"# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher\n{path_line}\n",
        encoding="utf-8",
    )

    for launcher_bin_dir in installer._launcher_bin_dirs():
        launcher_bin_dir.mkdir(parents=True, exist_ok=True)
        _write_executable(
            launcher_bin_dir / "vibecrafted",
            "#!/usr/bin/env bash\nprintf 'launcher\\n'\n",
        )
        _write_executable(
            launcher_bin_dir / "vibecraft",
            "#!/usr/bin/env bash\nprintf 'compat\\n'\n",
        )
        for wrapper_name in (
            *installer.LAUNCHER_WRAPPERS,
            *installer.LEGACY_LAUNCHER_NAMES,
        ):
            (launcher_bin_dir / wrapper_name).symlink_to("vibecrafted")
        _write_executable(
            launcher_bin_dir / "unrelated-tool",
            "#!/usr/bin/env bash\nprintf 'keep\\n'\n",
        )

    return home, crafted_home, store_path, helper_file, zshrc


def test_cmd_uninstall_removes_launchers_and_compat_pack_wrappers(
    tmp_path: Path, monkeypatch
) -> None:
    home, _crafted_home, store_path, helper_file, zshrc = _setup_installed_surface(
        tmp_path, monkeypatch
    )
    retired_frame = home / ".local" / "bin" / "vc-frame.real"
    _write_executable(retired_frame)

    exit_code = installer.cmd_uninstall(Namespace(dry_run=False))

    assert exit_code == 0
    assert not helper_file.exists()
    assert not retired_frame.exists()
    assert installer._launcher_path_line() not in zshrc.read_text(encoding="utf-8")
    assert not (store_path / "vc-init").exists()
    assert not (home / ".codex" / "skills" / "vc-init").exists()

    backup_root = installer._backup_root(store_path)
    latest = (backup_root / "latest").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        (backup_root / latest / "restore-manifest.json").read_text(encoding="utf-8")
    )
    backed_paths = {Path(item["path"]).name for item in manifest["items"]}
    assert {"marble-pack", "aicx-pack"} <= backed_paths

    for launcher_bin_dir in installer._launcher_bin_dirs():
        for removed_name in (
            "vibecrafted",
            "vibecraft",
            *installer.LAUNCHER_WRAPPERS,
            *installer.LEGACY_LAUNCHER_NAMES,
        ):
            assert not (launcher_bin_dir / removed_name).exists()
            assert not (launcher_bin_dir / removed_name).is_symlink()
        assert (launcher_bin_dir / "unrelated-tool").exists()

    assert not collect_names(installer.collect_installed_launchers())


def test_cmd_uninstall_tears_down_runtime_before_removing_files(
    tmp_path: Path, monkeypatch
) -> None:
    home, _crafted_home, store_path, _helper_file, _zshrc = _setup_installed_surface(
        tmp_path, monkeypatch
    )
    events: list[str] = []

    def teardown(_shared_home: Path, *, dry_run: bool) -> tuple[str, ...]:
        assert not dry_run
        assert (store_path / "vc-init").exists()
        events.append("runtime")
        return ("stop owned runtime",)

    monkeypatch.setattr(installer, "_teardown_owned_runtime_for_uninstall", teardown)

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0
    assert events == ["runtime"]
    assert not (home / ".local" / "bin" / "vibecrafted").exists()


def test_cmd_uninstall_aborts_file_removal_when_runtime_teardown_fails(
    tmp_path: Path, monkeypatch
) -> None:
    home, _crafted_home, store_path, helper_file, _zshrc = _setup_installed_surface(
        tmp_path, monkeypatch
    )

    def teardown(_shared_home: Path, *, dry_run: bool) -> tuple[str, ...]:
        assert not dry_run
        raise OSError("owned supervisor remains")

    monkeypatch.setattr(installer, "_teardown_owned_runtime_for_uninstall", teardown)

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 1
    assert (store_path / "vc-init").exists()
    assert helper_file.exists()
    assert (home / ".local" / "bin" / "vibecrafted").exists()


def test_cmd_uninstall_treats_unlinked_retired_process_as_runtime_work(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    record = installer._RetiredVcFrameProcess(
        101,
        ("darwin:1:1", os.geteuid(), 8),
        (str(home / ".local" / "bin" / "vc-frame.real"), "--server", "/tmp/old"),
    )
    calls: list[tuple[Path, bool]] = []

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    monkeypatch.setattr(installer, "_IS_TTY", False)
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "_runtime_service_has_evidence", lambda _home: False)
    monkeypatch.setattr(
        installer, "_retired_vc_frame_process_census", lambda: (record,)
    )

    def teardown(shared_home: Path, *, dry_run: bool) -> tuple[str, ...]:
        calls.append((shared_home, dry_run))
        return ("terminate 1 retired vc-frame.real process(es)",)

    monkeypatch.setattr(installer, "_teardown_owned_runtime_for_uninstall", teardown)

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0
    assert calls == [(crafted_home, False)]


def test_retired_vc_frame_census_requires_exact_stable_same_user_argv0(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    retired = home / ".local" / "bin" / "vc-frame.real"
    _write_executable(retired)
    births = {
        101: ("darwin:1:1", os.geteuid(), 8),
        102: ("darwin:2:2", os.geteuid(), 8),
        103: ("darwin:3:3", os.geteuid() + 1, 8),
    }
    arguments = {
        101: (str(retired), "--server", "/tmp/Finalized runs"),
        102: (str(retired.with_name("vc-frame")), "--server", "/tmp/live"),
        103: (str(retired), "--server", "/tmp/foreign"),
    }

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "_darwin_process_ids", lambda: (101, 102, 103))
    monkeypatch.setattr(installer, "_darwin_process_birth", births.__getitem__)
    monkeypatch.setattr(
        installer,
        "_darwin_process_arguments",
        lambda pid, *, pointer_size: arguments[pid],
    )

    assert installer._retired_vc_frame_process_census() == (
        installer._RetiredVcFrameProcess(101, births[101], arguments[101]),
    )


def test_owned_runtime_census_matches_product_processes_without_killing_editors(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    runtime_releases = home / "runtime/releases"
    app_executable = Path("/Applications/Vibecrafted.app/Contents/MacOS/Vibecrafted")
    custom_app = home / "Applications/Vibecrafted-Recovery-Test.app"
    births = {pid: (f"darwin:{pid}:1", os.geteuid(), 8) for pid in range(101, 108)}
    births[106] = ("darwin:106:1", os.geteuid() + 1, 8)
    arguments = {
        101: (str(app_executable),),
        102: (str(runtime_releases / "current/bin/vc-frame"), "--session", "test"),
        103: ("/bin/bash", str(runtime_releases / "current/start.sh")),
        104: (
            "/Applications/Visual Studio Code.app/Contents/MacOS/Electron",
            str(runtime_releases / "current/README.md"),
        ),
        105: (str(runtime_releases / "current/bin/vc-terminal"),),
        106: (str(runtime_releases / "current/bin/vc-server"),),
        107: (str(custom_app / "Contents/MacOS/Vibecrafted"),),
    }

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(home / "runtime"))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "_darwin_process_ids", lambda: tuple(births))
    monkeypatch.setattr(
        installer, "_darwin_caller_ancestor_pids", lambda: frozenset({105})
    )
    monkeypatch.setattr(installer, "_darwin_process_birth", births.__getitem__)
    monkeypatch.setattr(
        installer,
        "_darwin_process_arguments",
        lambda pid, *, pointer_size: arguments[pid],
    )

    assert tuple(
        record.pid for record in installer._owned_runtime_process_census()
    ) == (
        101,
        102,
        103,
    )
    assert tuple(
        record.pid
        for record in installer._owned_runtime_process_census(app_root=custom_app)
    ) == (101, 102, 103, 107)


def test_darwin_parent_pid_uses_strict_ps_fallback_on_remote_login_eperm(
    monkeypatch,
) -> None:
    class DeniedLibproc:
        @staticmethod
        def proc_pidinfo(*_args) -> int:
            installer.ctypes.set_errno(installer.errno.EPERM)
            return 0

    monkeypatch.setattr(
        installer, "_darwin_process_libraries", lambda: (DeniedLibproc(), object())
    )
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "  42\n", ""),
    )

    assert installer._darwin_process_parent_pid(101) == 42


def test_terminate_retired_vc_frame_reproves_identity_before_signal(
    monkeypatch,
) -> None:
    record = installer._RetiredVcFrameProcess(
        101,
        ("darwin:1:1", os.geteuid(), 8),
        ("/Users/test/.local/bin/vc-frame.real", "--server", "/tmp/finalized"),
    )
    alive = {101: True}
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr(
        installer,
        "_retired_vc_frame_process_still_matches",
        lambda observed: alive[observed.pid],
    )

    def kill(pid: int, sent_signal: int) -> None:
        signals.append((pid, sent_signal))
        alive[pid] = False

    monkeypatch.setattr(installer.os, "kill", kill)

    installer._terminate_retired_vc_frame_processes((record,), timeout_seconds=0)

    assert signals == [(101, installer.signal.SIGTERM)]


def test_terminate_retired_vc_frame_escalates_stubborn_exact_process(
    monkeypatch,
) -> None:
    record = installer._RetiredVcFrameProcess(
        101,
        ("darwin:1:1", os.geteuid(), 8),
        ("/Users/test/.local/bin/vc-frame.real", "--server", "/tmp/failed"),
    )
    alive = {101: True}
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr(
        installer,
        "_retired_vc_frame_process_still_matches",
        lambda observed: alive[observed.pid],
    )

    def kill(pid: int, sent_signal: int) -> None:
        signals.append((pid, sent_signal))
        if sent_signal == installer.signal.SIGKILL:
            alive[pid] = False

    monkeypatch.setattr(installer.os, "kill", kill)

    installer._terminate_retired_vc_frame_processes((record,), timeout_seconds=0)

    assert signals == [
        (101, installer.signal.SIGTERM),
        (101, installer.signal.SIGKILL),
    ]


def test_terminate_retired_vc_frame_does_not_signal_reused_pid(monkeypatch) -> None:
    record = installer._RetiredVcFrameProcess(
        101,
        ("darwin:1:1", os.geteuid(), 8),
        ("/Users/test/.local/bin/vc-frame.real", "--server", "/tmp/old"),
    )
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr(
        installer,
        "_retired_vc_frame_process_still_matches",
        lambda _observed: False,
    )
    monkeypatch.setattr(
        installer.os,
        "kill",
        lambda pid, sent_signal: signals.append((pid, sent_signal)),
    )

    installer._terminate_retired_vc_frame_processes((record,), timeout_seconds=0)

    assert signals == []


def test_runtime_teardown_uninstalls_owned_service_and_proves_quiescence(
    tmp_path: Path, monkeypatch
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    launcher = tmp_path / "bin" / "vibecrafted"
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=42,
    )
    quiescent = installer._RuntimeServiceStatus(
        installed=False,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    snapshots = iter(((launcher, healthy, "running"), (launcher, quiescent, "stopped")))
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer, "_current_tools_link", lambda _home: tmp_path / "current"
    )
    monkeypatch.setattr(
        installer,
        "_tools_install_lease",
        lambda _link, *, operation: nullcontext(9),
    )
    monkeypatch.setattr(
        installer, "_inherited_tools_install_lease", lambda _descriptor: nullcontext()
    )
    monkeypatch.setattr(installer.os, "set_inheritable", lambda _fd, _value: None)
    monkeypatch.setattr(
        installer, "_assert_runtime_loaded_service_owner", lambda _home: shared_home
    )
    monkeypatch.setattr(installer, "_runtime_service_has_evidence", lambda _home: True)
    monkeypatch.setattr(
        installer, "_runtime_service_snapshot", lambda _home: next(snapshots)
    )
    monkeypatch.setattr(installer, "_retired_vc_frame_process_census", tuple)

    def run_command(
        _launcher: Path, _home: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(installer, "_run_runtime_service_command", run_command)

    actions = installer._teardown_owned_runtime_for_uninstall(
        shared_home, dry_run=False
    )

    assert actions == ("stop and uninstall owned runtime service",)
    assert commands == [("service", "uninstall")]


def test_runtime_teardown_terminates_owned_runtime_processes_and_proves_zero(
    tmp_path: Path, monkeypatch
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    record = installer._RetiredVcFrameProcess(
        101,
        ("darwin:1:1", os.geteuid(), 8),
        (str(tmp_path / "runtime/releases/current/bin/vc-frame"),),
    )
    censuses = iter(((record,), ()))
    terminated: list[tuple[installer._RetiredVcFrameProcess, ...]] = []

    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer, "_current_tools_link", lambda _home: tmp_path / "current"
    )
    monkeypatch.setattr(
        installer,
        "_tools_install_lease",
        lambda _link, *, operation: nullcontext(9),
    )
    monkeypatch.setattr(
        installer, "_inherited_tools_install_lease", lambda _descriptor: nullcontext()
    )
    monkeypatch.setattr(installer.os, "set_inheritable", lambda _fd, _value: None)
    monkeypatch.setattr(installer, "_runtime_service_has_evidence", lambda _home: False)
    monkeypatch.setattr(installer, "_retired_vc_frame_process_census", tuple)
    monkeypatch.setattr(
        installer,
        "_owned_runtime_process_census",
        lambda *, app_root=None: next(censuses),
    )
    monkeypatch.setattr(
        installer,
        "_terminate_owned_runtime_processes",
        lambda records: terminated.append(tuple(records)),
    )

    actions = installer._teardown_owned_runtime_for_uninstall(
        shared_home, dry_run=False
    )

    assert actions == ("terminate 1 owned runtime process(es)",)
    assert terminated == [(record,)]


def test_runtime_teardown_does_not_probe_launcher_without_service_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    shared_home = tmp_path / ".vibecrafted"

    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer, "_current_tools_link", lambda _home: tmp_path / "current"
    )
    monkeypatch.setattr(
        installer,
        "_tools_install_lease",
        lambda _link, *, operation: nullcontext(9),
    )
    monkeypatch.setattr(
        installer, "_inherited_tools_install_lease", lambda _descriptor: nullcontext()
    )
    monkeypatch.setattr(installer.os, "set_inheritable", lambda _fd, _value: None)
    monkeypatch.setattr(installer, "_runtime_service_has_evidence", lambda _home: False)
    monkeypatch.setattr(installer, "_retired_vc_frame_process_census", tuple)

    def forbidden_snapshot(_home: Path):
        raise AssertionError("service status must not run without service evidence")

    monkeypatch.setattr(installer, "_runtime_service_snapshot", forbidden_snapshot)

    assert (
        installer._teardown_owned_runtime_for_uninstall(shared_home, dry_run=False)
        == ()
    )


def test_stale_supervisor_lock_is_not_runtime_service_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    lock_path = shared_home / "server/supervisor.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.touch(mode=0o600)
    monkeypatch.setenv("HOME", str(home))

    assert installer._runtime_service_has_evidence(shared_home) is False


def test_held_supervisor_lock_is_runtime_service_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    lock_path = shared_home / "server/supervisor.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.touch(mode=0o600)
    monkeypatch.setenv("HOME", str(home))

    def held(_descriptor: int, operation: int) -> None:
        if operation & installer.fcntl.LOCK_NB:
            raise BlockingIOError

    monkeypatch.setattr(installer.fcntl, "flock", held)

    assert installer._runtime_service_has_evidence(shared_home) is True


def test_cmd_uninstall_removes_release_contract_assets_with_managed_payload(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _setup_installed_surface(tmp_path, monkeypatch)

    tools_root = installer.vibecrafted_tools_home()
    managed_payload = tools_root / "vibecrafted-9.9.9"
    package_root = managed_payload / "vibecrafted-core" / "vibecrafted_core"
    contract_assets = [
        package_root / relative
        for relative in installer.RELEASE_CONTRACT_PACKAGE_ASSETS
    ]
    expected_bytes: dict[Path, bytes] = {}
    for asset in contract_assets:
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text(f"fixture for {asset.name}\n", encoding="utf-8")
        expected_bytes[asset] = asset.read_bytes()

    unmanaged_sibling = tools_root / "operator-owned-tools"
    unmanaged_sibling.mkdir(parents=True)
    (unmanaged_sibling / "keep.txt").write_text("keep\n", encoding="utf-8")

    captured_inventory: list[installer.ManagedPath] = []
    build_inventory = installer._build_uninstall_inventory

    def capture_inventory(**kwargs) -> list[installer.ManagedPath]:
        inventory = build_inventory(**kwargs)
        captured_inventory[:] = inventory
        return inventory

    monkeypatch.setattr(installer, "_build_uninstall_inventory", capture_inventory)
    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0

    output = capsys.readouterr().out
    assert f"staged-payload: {managed_payload}" in output
    assert f"tools-sibling: {unmanaged_sibling}" in output
    payload_owners = [
        record
        for record in captured_inventory
        if record.kind == "staged-payload" and record.path == managed_payload
    ]
    assert len(payload_owners) == 1
    owner = payload_owners[0]
    assert owner.action == "remove"
    for asset in contract_assets:
        assert (
            asset.relative_to(owner.path)
            .as_posix()
            .startswith("vibecrafted-core/vibecrafted_core/")
        )
    assert not managed_payload.exists()
    assert all(not asset.exists() for asset in contract_assets)
    assert (unmanaged_sibling / "keep.txt").read_text(encoding="utf-8") == "keep\n"

    store_path = installer.vibecrafted_home() / "skills"
    backup_root = installer._backup_root(store_path)
    latest = (backup_root / "latest").read_text(encoding="utf-8").strip()
    restore_manifest = json.loads(
        (backup_root / latest / "restore-manifest.json").read_text(encoding="utf-8")
    )
    owner_records = [
        item for item in restore_manifest["items"] if Path(item["path"]) == owner.path
    ]
    assert len(owner_records) == 1

    assert installer.cmd_restore(Namespace(dry_run=False)) == 0
    assert {asset: asset.read_bytes() for asset in contract_assets} == expected_bytes
    assert (unmanaged_sibling / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_cmd_uninstall_prefers_manifest_tracked_launchers_and_helpers(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    local_bin = home / ".local" / "bin"
    runtime_skills = home / ".codex" / "skills"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    monkeypatch.setattr(installer, "_IS_TTY", False)

    store_path.mkdir(parents=True)
    runtime_skills.mkdir(parents=True)
    local_bin.mkdir(parents=True)

    skill_dir = store_path / "vc-init"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# test\n", encoding="utf-8")
    (runtime_skills / "vc-init").symlink_to(skill_dir)

    helper_file = installer._helper_target_path()
    helper_file.parent.mkdir(parents=True, exist_ok=True)
    helper_file.write_text("# helper shim\n", encoding="utf-8")
    manual_helper = installer._helper_legacy_path()
    manual_helper.parent.mkdir(parents=True, exist_ok=True)
    manual_helper.write_text("# user helper\n", encoding="utf-8")

    for launcher in ("vibecrafted", "vc-help", "vc-workflow", "telemetry"):
        if launcher == "vibecrafted":
            _write_executable(
                local_bin / launcher,
                "#!/usr/bin/env bash\nprintf 'launcher\\n'\n",
            )
        else:
            (local_bin / launcher).symlink_to("vibecrafted")

    (local_bin / "unrelated-tool").write_text("echo keep\n", encoding="utf-8")

    zshrc = home / ".zshrc"
    zshrc.write_text(
        f"# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher\n{installer._launcher_path_line()}\n",
        encoding="utf-8",
    )

    manifest_state = installer.InstallState(
        framework_version="9.9.9",
        skills=["vc-init"],
        runtimes=["codex"],
        launcher_entries=[f"{installer._launcher_dir_key(local_bin)}/vibecrafted"],
        helper_files=[str(helper_file)],
        shell_helpers=True,
    )
    manifest_state.save(store_path)

    exit_code = installer.cmd_uninstall(Namespace(dry_run=False))

    assert exit_code == 0
    assert not helper_file.exists()
    assert manual_helper.exists()
    assert not (local_bin / "vibecrafted").exists()
    assert (local_bin / "vc-help").is_symlink()
    assert (local_bin / "vc-workflow").is_symlink()
    assert installer._launcher_path_line() not in zshrc.read_text(encoding="utf-8")
    assert not (runtime_skills / "vc-init").exists()
    assert (local_bin / "unrelated-tool").exists()

    backup_root = installer._backup_root(store_path)
    latest = (backup_root / "latest").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        (backup_root / latest / "restore-manifest.json").read_text(encoding="utf-8")
    )
    backed_paths = {Path(item["path"]).name for item in manifest["items"]}
    assert "vc-skills.sh" in backed_paths
    assert "vc-help" not in backed_paths


def test_restore_roundtrip_recovers_launchers_and_runtime_symlinks(
    tmp_path: Path, monkeypatch
) -> None:
    home, _crafted_home, store_path, helper_file, zshrc = _setup_installed_surface(
        tmp_path, monkeypatch
    )

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0
    assert installer.cmd_restore(Namespace(dry_run=False)) == 0

    assert helper_file.exists()
    assert installer._launcher_path_line() in zshrc.read_text(encoding="utf-8")

    runtime_link = home / ".codex" / "skills" / "vc-init"
    assert runtime_link.is_symlink()
    assert runtime_link.readlink() == store_path / "vc-init"

    for launcher_bin_dir in installer._launcher_bin_dirs():
        assert (launcher_bin_dir / "vibecrafted").exists()
        assert (launcher_bin_dir / "vibecraft").exists()
        for restored_name in (
            "vc-help",
            "vc-workflow",
            "telemetry",
            "marble-pack",
            "aicx-pack",
        ):
            restored = launcher_bin_dir / restored_name
            assert restored.is_symlink()
            assert restored.readlink() == Path("vibecrafted")
        assert (launcher_bin_dir / "unrelated-tool").exists()

    restored_launchers = collect_names(installer.collect_installed_launchers())
    assert "marble-pack" in restored_launchers
    assert "aicx-pack" in restored_launchers
    assert "vibecrafted" in restored_launchers


def test_cmd_uninstall_cleans_launcher_only_surface_without_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    monkeypatch.setattr(installer, "_IS_TTY", False)
    monkeypatch.setattr(installer, "_known_bundle_names", list)

    for launcher_bin_dir in installer._launcher_bin_dirs():
        launcher_bin_dir.mkdir(parents=True, exist_ok=True)
        _write_executable(
            launcher_bin_dir / "vibecrafted",
            "#!/usr/bin/env bash\nprintf 'launcher\\n'\n",
        )
        (launcher_bin_dir / "vc-help").symlink_to("vibecrafted")
        (launcher_bin_dir / "vc-workflow").symlink_to("vibecrafted")

    zshrc = home / ".zshrc"
    zshrc.write_text(
        f"# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher\n{installer._launcher_path_line()}\n",
        encoding="utf-8",
    )

    exit_code = installer.cmd_uninstall(Namespace(dry_run=False))

    assert exit_code == 0
    for launcher_bin_dir in installer._launcher_bin_dirs():
        assert not (launcher_bin_dir / "vibecrafted").exists()
        assert not (launcher_bin_dir / "vc-help").exists()
        assert not (launcher_bin_dir / "vc-workflow").exists()
    assert installer._launcher_path_line() not in zshrc.read_text(encoding="utf-8")

    backup_root = installer._backup_root(store_path)
    latest = (backup_root / "latest").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        (backup_root / latest / "restore-manifest.json").read_text(encoding="utf-8")
    )
    assert any(Path(item["path"]).name == "vibecrafted" for item in manifest["items"])


def collect_names(entries: list[tuple[Path, Path]]) -> set[str]:
    return {entry.name for _, entry in entries}


def test_deck_launcher_forwards_uninstall_argv(tmp_path, monkeypatch) -> None:
    """`vibecrafted uninstall --dry-run` must reach the installer with the flag intact.

    The deck once called `python3 "$installer" uninstall` without `"$@"`, so a
    dry-run request executed a real, unconfirmed teardown (2026-08-19 incident).
    """
    repo_root = Path(__file__).resolve().parents[2]
    deck = repo_root / "vibecrafted-core" / "vibecrafted_core" / "deck" / "vibecrafted"
    capture = tmp_path / "argv.txt"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$CAPTURE_FILE"\nexit 0\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["CAPTURE_FILE"] = str(capture)
    result = subprocess.run(
        ["bash", str(deck), "uninstall", "--dry-run"],
        check=False,
        env=env,
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    argv = capture.read_text(encoding="utf-8").splitlines()
    assert argv[-2:] == ["uninstall", "--dry-run"], argv


def _latest_backup_paths() -> set[Path]:
    """Absolute paths captured by the newest teardown restore manifest."""
    backup_root = installer._backup_root(installer.vibecrafted_home() / "skills")
    latest = (backup_root / "latest").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        (backup_root / latest / "restore-manifest.json").read_text(encoding="utf-8")
    )
    return {Path(item["path"]) for item in manifest["items"]}


def test_cmd_uninstall_removes_framework_runtime_payload_by_name(
    tmp_path: Path, monkeypatch
) -> None:
    """releases/providers/server/active.json are framework-written, not operator data.

    Until 2026-08-19 the whole runtime home was blanket-preserved as "not proven
    installer-owned", so a 3.6 G payload survived every teardown. Ownership is
    decided by name so installs whose manifest predates these payloads still come
    off cleanly.
    """
    _setup_installed_surface(tmp_path, monkeypatch)
    runtime_home = installer.vibecrafted_runtime_home()

    owned: list[Path] = []
    for relative in (
        Path("releases") / "4.1.0" / "payload.txt",
        Path("providers") / "vc-slack-agent" / "agent",
        Path("server") / "site" / "index.html",
    ):
        target = runtime_home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{relative}\n", encoding="utf-8")
        owned.append(runtime_home / relative.parts[0])
    active = runtime_home / "active.json"
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text('{"release": "4.1.0"}\n', encoding="utf-8")
    owned.append(active)

    stranger = runtime_home / "operator-notes"
    stranger.mkdir(parents=True)
    (stranger / "keep.txt").write_text("keep\n", encoding="utf-8")

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0

    for path in owned:
        assert not path.exists()
    assert (stranger / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    # Backup-before-remove: every newly owned path must be restorable.
    assert set(owned) <= _latest_backup_paths()

    assert installer.cmd_restore(Namespace(dry_run=False)) == 0
    assert (runtime_home / "releases" / "4.1.0" / "payload.txt").is_file()
    assert active.is_file()


def test_cmd_uninstall_removes_installer_staging_siblings(
    tmp_path: Path, monkeypatch
) -> None:
    """Atomic-staging dirs, the handoff receipt and the install lease are ours."""
    _setup_installed_surface(tmp_path, monkeypatch)
    tools_home = installer.vibecrafted_tools_home()
    tools_home.mkdir(parents=True, exist_ok=True)

    staging = tools_home / "..vibecrafted-current.staging-59509-abcdef"
    staging.mkdir()
    (staging / "half-written.txt").write_text("staged\n", encoding="utf-8")
    handoff = tools_home / ".vibecrafted-current-handoff.json"
    handoff.write_text('{"generation": "4.1.0"}\n', encoding="utf-8")
    lease = installer._tools_install_lease_path(
        installer._current_tools_link(installer.vibecrafted_home())
    )
    lease.write_text("", encoding="utf-8")

    finder_metadata = tools_home / ".DS_Store"
    finder_metadata.write_text("finder\n", encoding="utf-8")

    stranger = tools_home / "third-party-tool"
    stranger.mkdir()
    (stranger / "keep.txt").write_text("keep\n", encoding="utf-8")

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0

    assert not staging.exists()
    assert not handoff.exists()
    assert not lease.exists()
    assert not finder_metadata.exists()
    assert (stranger / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert {staging, handoff, lease} <= _latest_backup_paths()


def test_cmd_uninstall_keeps_operator_secrets_in_framework_config(
    tmp_path: Path, monkeypatch
) -> None:
    """`~/.config/vibecrafted` is framework-generated, but `*.env` holds live tokens."""
    _setup_installed_surface(tmp_path, monkeypatch)
    config_root = Path(os.environ["XDG_CONFIG_HOME"])
    vib_config = config_root / "vibecrafted"
    (vib_config / "themes").mkdir(parents=True)
    (vib_config / "themes" / "dark.toml").write_text("theme\n", encoding="utf-8")
    secret = vib_config / "slack.env"
    secret.write_text("SLACK_BOT_TOKEN=xoxb-live\n", encoding="utf-8")

    vc_frame = config_root / "vc-frame"
    vc_frame.mkdir(parents=True)
    (vc_frame / "config.toml").write_text("frame\n", encoding="utf-8")
    frontier = config_root / "vetcoders" / "frontier"
    frontier.mkdir(parents=True)
    (frontier / "starship.toml").write_text("prompt\n", encoding="utf-8")

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0

    assert secret.read_text(encoding="utf-8") == "SLACK_BOT_TOKEN=xoxb-live\n"
    assert vib_config.is_dir()
    assert not (vib_config / "themes").exists()
    assert not vc_frame.exists()
    assert not frontier.exists()

    backed_up = _latest_backup_paths()
    assert {vib_config / "themes", vc_frame, frontier} <= backed_up
    assert secret not in backed_up


def test_cmd_uninstall_removes_darwin_library_surfaces(
    tmp_path: Path, monkeypatch
) -> None:
    """Plists, dynamic profiles, app-support, caches and prefs come off too."""
    home, _crafted, _store, _helper, _zshrc = _setup_installed_surface(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer,
        "_teardown_owned_runtime_for_uninstall",
        lambda _home, *, dry_run: (),
    )

    library = home / "Library"
    plist = library / "LaunchAgents" / "com.vetcoders.vibecrafted-slack-bridge.plist"
    profile = (
        library
        / "Application Support"
        / "iTerm2"
        / "DynamicProfiles"
        / "vibecrafted.json"
    )
    app_support = library / "Application Support" / "io.vetcoders.vc-frame"
    cache = library / "Caches" / "io.vetcoders.vc-frame"
    preference = library / "Preferences" / "com.vibecrafted.vc-board.plist"
    for owned_file in (plist, profile, preference):
        owned_file.parent.mkdir(parents=True, exist_ok=True)
        owned_file.write_text("framework\n", encoding="utf-8")
    for owned_dir in (app_support, cache):
        owned_dir.mkdir(parents=True, exist_ok=True)
        (owned_dir / "state.db").write_text("state\n", encoding="utf-8")

    foreign_profile = profile.with_name("someone-else.json")
    foreign_profile.write_text("keep\n", encoding="utf-8")
    foreign_pref = preference.with_name("com.apple.finder.plist")
    foreign_pref.write_text("keep\n", encoding="utf-8")

    captured: list[installer.ManagedPath] = []
    build_inventory = installer._build_uninstall_inventory

    def capture_inventory(**kwargs) -> list[installer.ManagedPath]:
        inventory = build_inventory(**kwargs)
        captured[:] = inventory
        return inventory

    monkeypatch.setattr(installer, "_build_uninstall_inventory", capture_inventory)

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0

    owned = {plist, profile, app_support, cache, preference}
    for path in owned:
        assert not path.exists()
    assert foreign_profile.read_text(encoding="utf-8") == "keep\n"
    assert foreign_pref.read_text(encoding="utf-8") == "keep\n"
    assert owned <= _latest_backup_paths()

    # The .app itself ships from the DMG; teardown never deletes it.
    applications = [
        record
        for record in captured
        if installer._is_subpath(record.path, Path("/Applications"))
    ]
    assert all(record.action == "preserve" for record in applications)


def test_second_uninstall_after_full_teardown_is_a_no_op(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Teardown must converge: nothing the first run leaves behind counts as work.

    The runtime teardown takes the cross-process install lease, so it creates
    `.vibecrafted-install.lock` after the inventory is built. Discovery alone
    never saw that file, which kept the tools root non-empty and made every
    later uninstall claim work forever.
    """
    _setup_installed_surface(tmp_path, monkeypatch)
    runtime_home = installer.vibecrafted_runtime_home()
    tools_home = installer.vibecrafted_tools_home()
    tools_home.mkdir(parents=True, exist_ok=True)
    (tools_home / "vibecrafted-9.9.9").mkdir()
    (runtime_home / "releases").mkdir(parents=True, exist_ok=True)
    (runtime_home / "active.json").write_text("{}\n", encoding="utf-8")
    stranger = runtime_home / "operator-notes"
    stranger.mkdir()
    (stranger / "keep.txt").write_text("keep\n", encoding="utf-8")
    config_root = Path(os.environ["XDG_CONFIG_HOME"])
    (config_root / "vibecrafted").mkdir(parents=True)
    (config_root / "vibecrafted" / "slack.env").write_text("T=1\n", encoding="utf-8")

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0
    capsys.readouterr()

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0
    second = capsys.readouterr().out
    assert "Nothing to uninstall" in second
    assert (stranger / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert (config_root / "vibecrafted" / "slack.env").is_file()


def test_dry_run_uninstall_leaves_no_new_install_lease(
    tmp_path: Path, monkeypatch
) -> None:
    """A dry run must not write the lease file its own runtime teardown opens."""
    _setup_installed_surface(tmp_path, monkeypatch)
    tools_home = installer.vibecrafted_tools_home()
    tools_home.mkdir(parents=True, exist_ok=True)
    (tools_home / "vibecrafted-9.9.9").mkdir()
    lease = installer._tools_install_lease_path(
        installer._current_tools_link(installer.vibecrafted_home())
    )

    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "_runtime_service_has_evidence", lambda _home: False)

    assert installer.cmd_uninstall(Namespace(dry_run=True)) == 0

    assert not lease.exists()
    assert (tools_home / "vibecrafted-9.9.9").is_dir()


def test_cmd_uninstall_names_every_uv_environment_it_will_not_touch(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """uv owns its environments; the plan must still name all of them to remove."""
    _setup_installed_surface(tmp_path, monkeypatch)
    uv_tools_root = tmp_path / "uv-tools"
    monkeypatch.setenv("UV_TOOL_DIR", str(uv_tools_root))
    environments = [
        uv_tools_root / name
        for name in ("vibecrafted", "vibecrafted-mcp", "vibecrafted-iterm2")
    ]
    for environment in environments:
        environment.mkdir(parents=True)
        (environment / "uv-receipt.toml").write_text("managed\n", encoding="utf-8")

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0

    output = capsys.readouterr().out
    for environment in environments:
        assert (environment / "uv-receipt.toml").is_file()
        assert str(environment) in output
    assert "uv tool uninstall" in output
