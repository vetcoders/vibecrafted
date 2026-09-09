"""Exercise the installer owner with sealed generations and disposable roots."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
import sys
import tomllib
from argparse import Namespace
from pathlib import Path

import pytest
from _runtime_pack_fixture import seed_runtime_pack

from scripts import vetcoders_install as installer


# This observer is deliberately independent of the installer's digest/receipt code.
# Reads may affect atime, so we compare names, kinds, bytes, modes and mtime only.
def _snapshot(root: Path) -> dict[str, tuple]:
    if not root.exists():
        return {}
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        metadata = path.lstat()
        value = (
            str(path.readlink())
            if path.is_symlink()
            else path.read_bytes()
            if path.is_file()
            else None
        )
        result[str(path.relative_to(root))] = (
            stat.S_IFMT(metadata.st_mode),
            stat.S_IMODE(metadata.st_mode),
            metadata.st_mtime_ns,
            value,
        )
    return result


@pytest.fixture
def roots(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "foreign-config"))
    monkeypatch.setenv(
        "VIBECRAFTED_RUNTIME_HOME", str(home / ".local/share/vibecrafted")
    )
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / ".local/bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / ".vibecrafted"))
    monkeypatch.setenv("VC_FRAME_SOCKET_DIR", str(tmp_path / "frame-sockets"))
    # Model the external lifecycle boundary only. Filesystem and validators run real.
    monkeypatch.setattr(
        installer, "_teardown_owned_runtime_for_uninstall", lambda *_a, **_k: ()
    )
    return installer._runtime_install_paths()


def _install(payload: Path, capsys, **choice) -> dict:
    assert (
        installer.cmd_runtime_install(
            Namespace(
                payload_root=str(payload),
                app_root=None,
                terminal_host=None,
                frame_helper=None,
                resolve_preference=choice.get("resolve_preference"),
                preference_current_sha256=choice.get("preference_current_sha256"),
                preference_incoming_sha256=choice.get("preference_incoming_sha256"),
                preference_path=choice.get("preference_path"),
            )
        )
        == 0
    )
    return json.loads(capsys.readouterr().out.splitlines()[-1])


def _install_conflict(payload: Path, capsys, **choice):
    with pytest.raises(installer.PreferenceConflict) as caught:
        installer.cmd_runtime_install(
            Namespace(
                payload_root=str(payload),
                app_root=None,
                terminal_host=None,
                frame_helper=None,
                resolve_preference=choice.get("resolve_preference"),
                preference_current_sha256=choice.get("preference_current_sha256"),
                preference_incoming_sha256=choice.get("preference_incoming_sha256"),
                preference_path=choice.get("preference_path"),
            )
        )
    capsys.readouterr()
    return caught.value


def _resolve(paths: dict, capsys, *, status: str) -> dict:
    before = _snapshot(Path.home())
    for _ in range(3):
        code = installer.cmd_runtime_resolve(
            Namespace(runtime_home=str(paths["runtime_home"]))
        )
        envelope = json.loads(capsys.readouterr().out)
        assert code == (2 if status == "unusable" else 0)
        assert envelope["schema"] == "vibecrafted.runtime-resolution.v1"
        assert envelope["status"] == status, envelope
        assert (envelope["runtime"] is None) == (status != "ready")
        assert _snapshot(Path.home()) == before
    return envelope


@pytest.fixture
def installed(tmp_path: Path, roots, capsys):
    payload = seed_runtime_pack(tmp_path / "pack-a", version="9.9.9+a")
    result = _install(payload, capsys)
    assert installer._runtime_generation_payload_errors(Path(result["root"])) == []
    return roots, payload, result


def test_absent_resolution_does_not_create_roots_or_lease(roots, capsys):
    _resolve(roots, capsys, status="absent")
    assert not roots["runtime_home"].exists()
    assert not roots["product_config"].exists()


def test_reinstall_preserves_shell_preferences_and_private_shell_state(
    tmp_path, roots, capsys
):
    private = Path.home() / ".config/atuin"
    private.mkdir(parents=True)
    (private / "config.toml").write_text('style = "compact"\n')
    private_rc = Path.home() / ".zshrc"
    private_rc.write_text("# My terminal\nexport PERSONAL_SHELL=1\n")
    private_history = Path.home() / ".zsh_history"
    private_history.write_text("private history sentinel\n")
    before = _snapshot(private)
    pack = seed_runtime_pack(tmp_path / "shell-pack", version="9.9.9+shell")
    _install(pack, capsys)
    product = roots["product_config"]
    preferences = {
        "starship.toml": "add_newline = false\n",
        "atuin/config.toml": 'style = "compact"\n',
    }
    for name, body in preferences.items():
        (product / name).write_text(body)
    _install(pack, capsys)
    for name, body in preferences.items():
        assert (product / name).read_text() == body
    assert (product / "vc-terminal/interactive.zsh").read_bytes() == (
        pack / "config/vc-terminal/interactive.zsh"
    ).read_bytes()
    assert "launch-primary-shell.zsh" in (product / "vc-terminal/.zshrc").read_text()
    assert _snapshot(private) == before
    assert private_rc.read_text() == "# My terminal\nexport PERSONAL_SHELL=1\n"
    assert private_history.read_text() == "private history sentinel\n"


@pytest.mark.parametrize("identity", ["active.json", installer.RUNTIME_INSTALL_RECEIPT])
def test_partial_identity_is_unusable_without_repair(roots, capsys, identity):
    roots["runtime_home"].mkdir(parents=True)
    (roots["runtime_home"] / identity).write_text("{}\n")
    envelope = _resolve(roots, capsys, status="unusable")
    assert "partial" in envelope["reason"]


def test_ready_resolution_uses_generation_host_and_one_physical_config(
    installed, capsys
):
    paths, _, result = installed
    envelope = _resolve(paths, capsys, status="ready")
    assert envelope["runtime"]["root"] == result["root"]
    assert envelope["runtime"]["terminal_host"] == str(
        Path(result["root"]) / "libexec/vc-terminal"
    )
    assert envelope["runtime"]["frame_config"] == str(
        Path.home() / ".config/vibecrafted/vc-frame"
    )
    assert not any(p.is_symlink() for p in paths["product_config"].rglob("*"))
    assert not (Path.home().parent / "foreign-config").exists()


def test_runtime_install_projects_quick_cmd_binding_to_active_compact_bar(
    installed, capsys
):
    """The install projection must retain the shared Cmd+Shift+. route."""
    paths, _, result = installed
    projected = paths["product_config"] / "vc-frame/config.kdl"
    generated = (
        Path(result["root"])
        / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    )
    assert projected.read_bytes() == generated.read_bytes()
    shared = projected.read_text(encoding="utf-8")
    shared = shared[shared.index("    shared {") : shared.index("    shared_except")]
    assert 'bind "Super Shift ."' in shared
    assert 'MessagePlugin "compact-bar"' in shared
    assert 'name "vc_quick_cmd"' in shared
    _resolve(paths, capsys, status="ready")


@pytest.mark.parametrize(
    "mutation",
    [
        "malformed-active",
        "symlink-active",
        "version",
        "pointer",
        "pending-install",
        "pending-config",
        "pending-uninstall",
        "transaction",
        "managed-layout",
        "manifest",
        "lineage",
        "launcher",
    ],
)
def test_resolution_rejects_corrupt_or_pending_install_without_writes(
    installed, capsys, mutation
):
    paths, _, result = installed
    runtime = paths["runtime_home"]
    receipt_path = runtime / installer.RUNTIME_INSTALL_RECEIPT
    receipt = json.loads(receipt_path.read_text())
    if mutation == "malformed-active":
        (runtime / "active.json").write_text("{")
    elif mutation == "symlink-active":
        active = runtime / "active.json"
        saved = runtime / "saved-active.json"
        active.rename(saved)
        active.symlink_to(saved)
    elif mutation == "version":
        receipt["version"] = "different"
    elif mutation == "pointer":
        current = runtime / "tools/vibecrafted-current"
        current.unlink()
        current.symlink_to(runtime / "releases/missing")
    elif mutation.startswith("pending-"):
        receipt[
            {
                "pending-install": "install_pending",
                "pending-config": "config_pending",
                "pending-uninstall": "uninstall_pending",
            }[mutation]
        ] = True
    elif mutation == "transaction":
        receipt["config_transaction"] = {}
    elif mutation == "managed-layout":
        (paths["product_config"] / "vc-frame/layouts/operator.kdl").write_text(
            "// edited\n"
        )
    elif mutation == "manifest":
        (Path(result["root"]) / "runtime-manifest.json").write_text("{}\n")
    elif mutation == "lineage":
        receipt["config_defaults"] = {}
    elif mutation == "launcher":
        (paths["launcher_home"] / "vibecrafted").write_text("#!/bin/sh\nexit 0\n")
    receipt_path.write_text(json.dumps(receipt))
    _resolve(paths, capsys, status="unusable")


def test_upgrade_preserves_user_kdl_policy_and_exact_theme_bytes(
    installed, tmp_path, capsys
):
    paths, _, result = installed
    product = paths["product_config"]
    config = product / "vc-frame/config.kdl"
    user_config = config.read_bytes() + b"\ncopy_on_select true\n"
    config.write_bytes(user_config)
    policy = product / "terminal-policy.toml"
    user_policy = policy.read_bytes().replace(b"opacity = 0.9", b"opacity = 0.75")
    assert user_policy != policy.read_bytes()
    policy.write_bytes(user_policy)
    theme = product / "terminal-theme.toml"
    user_theme = (
        b"# Exact personal theme formatting\n[colors.primary]\nbackground = '#112233'\n"
    )
    theme.write_bytes(user_theme)
    _resolve(paths, capsys, status="ready")
    old_generation = _snapshot(Path(result["root"]))
    payload_b = seed_runtime_pack(tmp_path / "pack-b", version="9.9.10+b")
    _install(payload_b, capsys)
    assert config.read_bytes() == user_config
    assert policy.read_bytes() == user_policy
    assert theme.read_bytes() == user_theme
    assert _snapshot(Path(result["root"])) == old_generation
    _resolve(paths, capsys, status="ready")


def test_unchanged_kdl_default_preserves_custom_keybinds_exactly(
    installed, tmp_path, capsys
):
    paths, _, _result = installed
    config = paths["product_config"] / "vc-frame/config.kdl"
    user_config = config.read_bytes().replace(b'bind "Ctrl n"', b'bind "Ctrl b"')
    assert user_config != config.read_bytes()
    config.write_bytes(user_config)

    _install(seed_runtime_pack(tmp_path / "pack-b", version="9.9.10+b"), capsys)

    assert config.read_bytes() == user_config
    _resolve(paths, capsys, status="ready")


def test_non_overlapping_kdl_upgrade_merges_user_preference_and_new_defaults(
    tmp_path, roots, capsys
):
    incoming = (
        Path(__file__).resolve().parents[2]
        / "vibecrafted-core/vibecrafted_core/config/vc-frame/config.kdl"
    ).read_text()
    previous = incoming.replace(
        "// compact chowal rail SESSIONS z nowego default.kdl (2026-07-07)\n"
        "// The native default and dashboard aliases resolve to the one shipped,\n"
        "// regular file: layouts/operator.kdl.  Runtime Packs reject symlinks, so do\n"
        "// not point this at an unmaterialized built-in `vibecrafted` layout.\n"
        'default_layout "operator"',
        '// Previous layout default.\ndefault_layout "vibecrafted"',
    )
    assert previous != incoming
    initial = _install(
        seed_runtime_pack(
            tmp_path / "pack-a", version="9.9.9+a", frame_config=previous
        ),
        capsys,
    )
    config = roots["product_config"] / "vc-frame/config.kdl"
    config.write_bytes(
        config.read_bytes().replace(
            b'copy_command "pbcopy"',
            b'copy_command "pbcopy"\ncopy_on_select true',
        )
    )
    payload_b = seed_runtime_pack(tmp_path / "pack-b", version="9.9.10+b")
    upgraded = _install(payload_b, capsys)
    expected = incoming.replace(
        'copy_command "pbcopy"', 'copy_command "pbcopy"\ncopy_on_select true'
    ).encode()
    assert config.read_bytes() == expected
    assert upgraded["root"] != initial["root"]
    _install(payload_b, capsys)
    assert config.read_bytes() == expected
    _resolve(roots, capsys, status="ready")


def test_kdl_upgrade_merges_user_scalar_with_shipped_nested_keybinds_and_retries(
    tmp_path, roots, capsys
):
    """The 4.3.0 -> 4.3.1 incident: scalar user preference plus new binds."""
    reproducer = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "fixtures/kdl-runtime-preference-upgrade.json"
        ).read_text(encoding="utf-8")
    )
    incoming = (
        Path(__file__).resolve().parents[2]
        / "vibecrafted-core/vibecrafted_core/config/vc-frame/config.kdl"
    ).read_text(encoding="utf-8")
    shipped_keybinds = reproducer["shipped_keybinds"]
    assert shipped_keybinds in incoming
    previous = incoming.replace(shipped_keybinds, "")
    initial = _install(
        seed_runtime_pack(
            tmp_path / "pack-a", version="9.9.9+a", frame_config=previous
        ),
        capsys,
    )
    config = roots["product_config"] / "vc-frame/config.kdl"
    config.write_bytes(
        config.read_bytes().replace(
            reproducer["user_anchor"].encode(),
            f"{reproducer['user_anchor']}\n{reproducer['user_scalar']}".encode(),
        )
    )
    payload_b = seed_runtime_pack(
        tmp_path / "pack-b", version="9.9.10+b", frame_config=incoming
    )

    upgraded = _install(payload_b, capsys)
    expected = incoming.replace(
        reproducer["user_anchor"],
        f"{reproducer['user_anchor']}\n{reproducer['user_scalar']}",
    ).encode()
    assert config.read_bytes() == expected
    assert b'bind "Super n" {' in config.read_bytes()
    assert b'bind "Super Shift ." {' in config.read_bytes()
    assert upgraded["root"] != initial["root"]

    _install(payload_b, capsys)
    assert config.read_bytes() == expected
    _resolve(roots, capsys, status="ready")


def test_kdl_upgrade_rejects_malformed_user_structure_without_publication(
    installed, tmp_path, capsys
):
    paths, _, result = installed
    config = paths["product_config"] / "vc-frame/config.kdl"
    config.write_bytes(config.read_bytes() + b"\n}\n")
    before = _snapshot(paths["product_config"])
    active = (paths["runtime_home"] / "active.json").read_bytes()
    source = (
        Path(result["root"])
        / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    )
    payload_b = seed_runtime_pack(
        tmp_path / "pack-b",
        version="9.9.10+b",
        frame_config=source.read_text(encoding="utf-8").replace(
            "mouse_mode true", "mouse_mode false"
        ),
    )
    with pytest.raises(RuntimeError, match="KDL structure is unbalanced"):
        _install(payload_b, capsys)
    capsys.readouterr()
    assert _snapshot(paths["product_config"]) == before
    assert (paths["runtime_home"] / "active.json").read_bytes() == active


def test_same_setting_kdl_conflict_refuses_publication_and_preserves_evidence(
    installed, tmp_path, capsys
):
    paths, _, result = installed
    product = paths["product_config"]
    config = product / "vc-frame/config.kdl"
    config.write_bytes(
        config.read_bytes().replace(
            b"mouse_mode true", b"copy_on_select true\nmouse_mode true"
        )
    )
    before = _snapshot(product)
    active = (paths["runtime_home"] / "active.json").read_bytes()
    source = (
        Path(result["root"])
        / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    )
    payload_b = seed_runtime_pack(
        tmp_path / "pack-b",
        version="9.9.10+b",
        frame_config=source.read_text().replace(
            "mouse_mode true", "mouse_mode true\ncopy_on_select false"
        ),
    )
    with pytest.raises(installer.PreferenceConflict) as caught:
        _install(payload_b, capsys)
    conflict = caught.value
    assert conflict.envelope["schema"] == installer.PREFERENCE_CONFLICT_SCHEMA
    assert conflict.envelope["status"] == "conflict"
    settings = [
        setting
        for item in conflict.envelope.get("files", [])
        for setting in item.get("settings", [])
    ]
    assert "copy_on_select" in settings
    assert "keep-current" in conflict.envelope["choices"]
    assert "use-incoming" in conflict.envelope["choices"]
    capsys.readouterr()
    assert _snapshot(product) == before
    assert (paths["runtime_home"] / "active.json").read_bytes() == active
    receipt = json.loads(
        (paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT).read_text()
    )
    assert "config_conflicts" not in receipt
    conflict = receipt["candidate_conflicts"][0]
    assert Path(conflict["backup"]).read_bytes() == config.read_bytes()
    assert Path(conflict["previous_defaults"]).is_file()
    assert Path(conflict["incoming_defaults"]).is_file()
    _resolve(paths, capsys, status="ready")


def test_nested_kdl_edit_refuses_publication_and_preserves_evidence(
    installed, tmp_path, capsys
):
    paths, _, result = installed
    product = paths["product_config"]
    config = product / "vc-frame/config.kdl"
    config.write_bytes(
        config.read_bytes().replace(b"themes {", b"themes {\n    copy_on_select true")
    )
    before = _snapshot(product)
    active = (paths["runtime_home"] / "active.json").read_bytes()
    source = (
        Path(result["root"])
        / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    )
    payload_b = seed_runtime_pack(
        tmp_path / "pack-b",
        version="9.9.10+b",
        frame_config=source.read_text().replace("mouse_mode true", "mouse_mode false"),
    )
    with pytest.raises(
        RuntimeError, match="KDL edit changes nested or structural content"
    ):
        _install(payload_b, capsys)
    capsys.readouterr()
    assert _snapshot(product) == before
    assert (paths["runtime_home"] / "active.json").read_bytes() == active
    _resolve(paths, capsys, status="ready")


def test_unsupported_changed_kdl_scalar_syntax_refuses_publication(
    installed, tmp_path, capsys
):
    paths, _, result = installed
    product = paths["product_config"]
    config = product / "vc-frame/config.kdl"
    config.write_bytes(
        config.read_bytes().replace(
            b'copy_command "pbcopy"', b'copy_command "pbcopy"; copy_on_select true'
        )
    )
    before = _snapshot(product)
    active = (paths["runtime_home"] / "active.json").read_bytes()
    source = (
        Path(result["root"])
        / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    )
    payload_b = seed_runtime_pack(
        tmp_path / "pack-b",
        version="9.9.10+b",
        frame_config=source.read_text().replace(
            'default_layout "operator"', 'default_layout "changed-upstream"'
        ),
    )
    with pytest.raises(
        RuntimeError, match="KDL edit uses unsupported changed scalar syntax"
    ):
        _install(payload_b, capsys)
    capsys.readouterr()
    assert _snapshot(product) == before
    assert (paths["runtime_home"] / "active.json").read_bytes() == active
    _resolve(paths, capsys, status="ready")


def _crash_install(payload: Path, paths: dict, cut: str) -> None:
    # Kill only this synthetic child, after an actual filesystem publication step.
    script = r"""
import os, sys
from pathlib import Path
from argparse import Namespace
from scripts import vetcoders_install as owner
payload, runtime, product, launcher, cut = map(str, sys.argv[1:])
real_replace = os.replace
rollback = False

def crash(source, destination):
    global rollback
    dst, src = Path(destination), Path(source)
    if cut == "rollback" and dst == Path(runtime) / "tools/vibecrafted-current" and not rollback:
        rollback = True
        raise OSError("injected pointer publication failure")
    real_replace(source, destination)
    if ((cut == "directory-gap" and src == Path(product))
        or (cut == "launcher" and dst == Path(launcher) / "vc-terminal")
        or (cut == "selector" and dst == Path(runtime) / "active.json")
        or (cut == "rollback" and rollback and dst == Path(product))):
        os._exit(86)
os.replace = crash
owner.cmd_runtime_install(Namespace(payload_root=payload, app_root=None, terminal_host=None, frame_helper=None))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            script,
            str(payload),
            str(paths["runtime_home"]),
            str(paths["product_config"]),
            str(paths["launcher_home"]),
            cut,
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 86, result.stdout + result.stderr


@pytest.mark.parametrize("cut", ["directory-gap", "launcher", "selector", "rollback"])
def test_publication_interruption_and_repeat_recovery(installed, tmp_path, capsys, cut):
    paths, _, old = installed
    payload_b = seed_runtime_pack(tmp_path / "pack-b", version="9.9.10+b")
    _crash_install(payload_b, paths, cut)
    _resolve(paths, capsys, status="unusable")
    receipt_path = paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT
    pending = json.loads(receipt_path.read_text())
    assert "config_transaction" in pending
    snapshots = {
        Path(e["before"]): _snapshot(Path(e["before"]))
        if Path(e["before"]).is_dir()
        else Path(e["before"]).read_bytes()
        if Path(e["before"]).is_file() and not Path(e["before"]).is_symlink()
        else None
        for e in pending["config_transaction"]["entries"]
    }
    installed_b = _install(payload_b, capsys)
    _resolve(paths, capsys, status="ready")
    assert installed_b["root"] != old["root"]
    _install(payload_b, capsys)
    _resolve(paths, capsys, status="ready")
    for path, content in snapshots.items():
        if isinstance(content, dict):
            assert _snapshot(path) == content
        elif content is not None:
            assert path.read_bytes() == content


def test_user_edit_after_interruption_stops_recovery_before_config_writes(
    installed, tmp_path, capsys
):
    paths, _, _ = installed
    payload_b = seed_runtime_pack(tmp_path / "pack-b", version="9.9.10+b")
    _crash_install(payload_b, paths, "launcher")
    config = paths["product_config"] / "vc-frame/config.kdl"
    config.write_bytes(config.read_bytes() + b"\n// user edit during interruption\n")
    before = _snapshot(paths["product_config"])
    active = (paths["runtime_home"] / "active.json").read_bytes()
    with pytest.raises(RuntimeError, match="configuration changed after interruption"):
        _install(payload_b, capsys)
    capsys.readouterr()
    assert _snapshot(paths["product_config"]) == before
    assert (paths["runtime_home"] / "active.json").read_bytes() == active
    _resolve(paths, capsys, status="unusable")


def test_snapshots_survive_two_upgrades_and_uninstall(installed, tmp_path, capsys):
    paths, _, _ = installed
    snapshots = {}
    for version in ("9.9.10+b", "9.9.11+c"):
        payload = seed_runtime_pack(tmp_path / version, version=version)
        _install(payload, capsys)
        receipt = json.loads(
            (paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT).read_text()
        )
        history = receipt["drift_backup_history"][str(paths["product_config"])]
        for raw in history:
            snapshots.setdefault(raw, _snapshot(Path(raw)))
    assert len(snapshots) >= 2
    user_extra = paths["product_config"] / "personal-notes.txt"
    user_extra.write_text("retain this addition\n")
    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True)) == 0
    )
    capsys.readouterr()
    for raw, before in snapshots.items():
        assert _snapshot(Path(raw)) == before
    backups = paths["runtime_home"] / ".installer-backups"
    archives = list(backups.glob("uninstalled-*.json"))
    assert len(archives) == 1
    archived = json.loads(archives[0].read_text())
    assert archived["status"] == "removed"
    assert any(
        (Path(raw) / "personal-notes.txt").read_text() == "retain this addition\n"
        for raw in archived["drift_backup_history"][str(paths["product_config"])]
        if (Path(raw) / "personal-notes.txt").is_file()
    )
    _resolve(paths, capsys, status="absent")


def test_independent_toml_changes_merge_user_preference_with_new_default(
    installed, tmp_path, capsys
):
    paths, _, _ = installed
    policy = paths["product_config"] / "terminal-policy.toml"
    policy.write_text(policy.read_text().replace("opacity = 0.9", "opacity = 0.75"))
    original = installed[1] / "config/vc-terminal/vibecrafted.toml"
    payload = seed_runtime_pack(
        tmp_path / "pack-b",
        version="9.9.10+b",
        terminal_policy=original.read_text().replace(
            "history = 50000", "history = 60000"
        ),
    )
    _install(payload, capsys)
    assert "opacity = 0.75" in policy.read_text()
    assert "history = 60000" in policy.read_text()
    _resolve(paths, capsys, status="ready")


def test_custom_frame_asset_refuses_upgrade_and_keeps_snapshot(
    installed, tmp_path, capsys
):
    paths, _, _ = installed
    custom = paths["product_config"] / "vc-frame/layouts/personal.kdl"
    custom.write_text("layout { pane }\n")
    before = _snapshot(paths["product_config"])
    active = (paths["runtime_home"] / "active.json").read_bytes()
    payload = seed_runtime_pack(tmp_path / "pack-b", version="9.9.10+b")
    with pytest.raises(RuntimeError, match="frame layouts/themes/scripts changed"):
        _install(payload, capsys)
    capsys.readouterr()
    assert _snapshot(paths["product_config"]) == before
    assert (paths["runtime_home"] / "active.json").read_bytes() == active
    receipt = json.loads(
        (paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT).read_text()
    )
    preserved = Path(
        receipt["drift_backups"][str(paths["product_config"] / "vc-frame")]
    )
    assert (preserved / "layouts/personal.kdl").read_bytes() == custom.read_bytes()


@pytest.mark.parametrize("preference", ["vc-frame/config.kdl", "terminal-policy.toml"])
def test_uninstall_preserves_accepted_user_preferences_in_recovery(
    installed, capsys, preference
):
    paths, _, _ = installed
    config = paths["product_config"] / preference
    expected = (
        config.read_bytes() + b"\ncopy_on_select true\n"
        if preference.endswith(".kdl")
        else config.read_bytes().replace(b"opacity = 0.9", b"opacity = 0.75")
    )
    assert expected != config.read_bytes()
    config.write_bytes(expected)
    # This is a valid, ready user preference according to the same owner.
    _resolve(paths, capsys, status="ready")
    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True)) == 0
    )
    capsys.readouterr()
    backup_root = paths["runtime_home"] / ".installer-backups"
    archive = json.loads(next(backup_root.glob("uninstalled-*.json")).read_text())
    assert any(
        (Path(raw) / preference).read_bytes() == expected
        for raw in archive["drift_backup_history"][str(paths["product_config"])]
        if (Path(raw) / preference).is_file()
    )


@pytest.mark.parametrize(
    "preference",
    ["vc-frame/config.kdl", "terminal-policy.toml", "terminal-theme.toml"],
)
def test_uninstall_preference_dry_run_and_final_snapshot(installed, capsys, preference):
    paths, _, _ = installed
    config = paths["product_config"] / preference
    expected = config.read_bytes() + (
        b"\ncopy_on_select true\n"
        if preference.endswith(".kdl")
        else b"\n# user preference\n"
    )
    config.write_bytes(expected)
    _resolve(paths, capsys, status="ready")
    before = _snapshot(Path.home())
    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=True, emit_result=True)) == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "dry-run"
    assert result["conflicts"] == []
    assert _snapshot(Path.home()) == before

    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True)) == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "removed"
    assert result["conflicts"] == []
    archive = json.loads(
        next(
            (paths["runtime_home"] / ".installer-backups").glob("uninstalled-*.json")
        ).read_text()
    )
    snapshot = Path(archive["drift_backups"][str(paths["product_config"])])
    assert (snapshot / preference).read_bytes() == expected
    assert archive["status"] == "removed"
    assert archive["uninstall_pending"] is False


@pytest.mark.parametrize(
    "preference,body",
    [
        ("vc-frame/config.kdl", b""),
        ("vc-frame/config.kdl", b"copy_on_select true\0"),
        ("terminal-policy.toml", b"opacity ="),
        ("terminal-policy.toml", b"[colors"),
        ("terminal-policy.toml", b"# \xff"),
    ],
)
def test_uninstall_invalid_preference_stays_conflicted(
    installed, capsys, monkeypatch, preference, body
):
    paths, _, _ = installed
    config = paths["product_config"] / preference
    installed_receipt = json.loads(
        (paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT).read_text()
    )
    assert str(config) in installed_receipt["owned_files"]
    config.write_bytes(body)
    _resolve(paths, capsys, status="unusable")
    monkeypatch.setattr(
        installer,
        "_teardown_owned_runtime_for_uninstall",
        lambda *_a, **_k: pytest.fail("conflict must precede runtime teardown"),
    )
    before = _snapshot(paths["product_config"])
    backups = _snapshot(paths["runtime_home"] / ".installer-backups")
    receipt = (paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT).read_bytes()
    for dry_run in (True, False):
        assert (
            installer.cmd_runtime_uninstall(
                Namespace(dry_run=dry_run, emit_result=True)
            )
            == 1
        )
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "conflict"
        assert result["actions"] == []
        assert str(config) in result["conflicts"]
        assert _snapshot(paths["product_config"]) == before
        assert _snapshot(paths["runtime_home"] / ".installer-backups") == backups
        assert (
            paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT
        ).read_bytes() == receipt


def test_uninstall_keeps_unreceipted_theme_bytes_in_recovery(installed, capsys):
    paths, _, _ = installed
    theme = paths["product_config"] / "terminal-theme.toml"
    receipt = json.loads(
        (paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT).read_text()
    )
    assert str(theme) not in receipt["owned_files"]
    expected = b"# unfinished user theme\n[colors"
    theme.write_bytes(expected)
    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True)) == 0
    )
    capsys.readouterr()
    archive = json.loads(
        next(
            (paths["runtime_home"] / ".installer-backups").glob("uninstalled-*.json")
        ).read_text()
    )
    snapshot = Path(archive["drift_backups"][str(paths["product_config"])])
    assert (snapshot / "terminal-theme.toml").read_bytes() == expected


def test_uninstall_preference_snapshot_failure_precedes_removal(
    installed, capsys, monkeypatch
):
    paths, _, _ = installed
    config = paths["product_config"] / "vc-frame/config.kdl"
    config.write_bytes(config.read_bytes() + b"\ncopy_on_select true\n")
    before = _snapshot(paths["product_config"])
    receipt_path = paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT
    receipt = receipt_path.read_bytes()

    def fail_copy(*_a, **_k):
        raise OSError("snapshot write failed")

    monkeypatch.setattr(installer, "_copy_path_to_backup", fail_copy)
    monkeypatch.setattr(
        installer,
        "_teardown_owned_runtime_for_uninstall",
        lambda *_a, **_k: pytest.fail("snapshot must precede runtime teardown"),
    )
    with pytest.raises(OSError, match="snapshot write failed"):
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True))
    assert _snapshot(paths["product_config"]) == before
    assert receipt_path.read_bytes() == receipt
    assert not list(
        (paths["runtime_home"] / ".installer-backups").glob("uninstalled-*.json")
    )


@pytest.mark.parametrize(
    "alias", ["symlink", "parent-symlink", "hardlink", "directory"]
)
def test_uninstall_aliased_preference_stays_conflicted(installed, capsys, alias):
    paths, _, _ = installed
    config = paths["product_config"] / "vc-frame/config.kdl"
    config.write_bytes(config.read_bytes() + b"\ncopy_on_select true\n")
    saved = paths["product_config"] / "saved-preference"
    if alias == "parent-symlink":
        config.parent.rename(saved)
        config.parent.symlink_to(saved, target_is_directory=True)
    else:
        config.rename(saved)
        if alias == "hardlink":
            config.hardlink_to(saved)
        elif alias == "directory":
            config.mkdir()
        else:
            config.symlink_to(saved)
    before = _snapshot(Path.home())
    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=True, emit_result=True)) == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "conflict"
    assert result["actions"] == []
    assert str(config) in result["conflicts"]
    assert _snapshot(Path.home()) == before


@pytest.mark.parametrize(
    "managed",
    [
        "vc-terminal/vc-terminal.toml",
        "vc-terminal/launch-primary-shell.zsh",
        "vc-frame/layouts/operator.kdl",
    ],
)
def test_uninstall_preference_does_not_excuse_managed_drift(installed, capsys, managed):
    paths, _, _ = installed
    config = paths["product_config"] / "vc-frame/config.kdl"
    config.write_bytes(config.read_bytes() + b"\ncopy_on_select true\n")
    other = paths["product_config"] / managed
    other.write_bytes(other.read_bytes() + b"\n# unknown drift\n")
    before = _snapshot(Path.home())
    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=True, emit_result=True)) == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "conflict"
    assert result["actions"] == []
    assert result["conflicts"] == [str(other)]
    assert _snapshot(Path.home()) == before


@pytest.mark.parametrize(
    "arguments,accepted",
    [
        (["--config-file", "foreign.toml"], False),
        (["--config-file=foreign.toml"], False),
        (["--title", "-e", "--config-file=foreign.toml"], False),
        (["-t", "-e", "--config-file", "foreign.toml"], False),
        (["-e", "program", "--config-file", "payload.toml"], True),
        (["--command", "program", "--config-file=payload.toml"], True),
        (["--command=program", "--config-file=payload.toml"], True),
        (["-veprogram", "--config-file=payload.toml"], True),
        (["--", "--config-file=payload.toml"], True),
    ],
)
def test_terminal_wrapper_pins_physical_owner_and_preserves_payload_argv(
    tmp_path, monkeypatch, arguments, accepted
):
    import shutil

    home = tmp_path / "home"
    entry = home / ".config/vibecrafted/vc-terminal/vc-terminal.toml"
    entry.parent.mkdir(parents=True)
    entry.write_text("[general]\n")
    generation = tmp_path / "generation with spaces"
    (generation / "bin").mkdir(parents=True)
    (generation / "libexec").mkdir()
    wrapper = generation / "bin/vc-terminal"
    shutil.copy2(
        Path(__file__).resolve().parents[2] / "scripts/vc-terminal-product-entry.sh",
        wrapper,
    )
    host = generation / "libexec/vc-terminal"
    host.write_text(
        f"#!{sys.executable}\nimport json, os, sys\nprint(json.dumps({{'argv':sys.argv[1:], 'env':{{k:v for k,v in os.environ.items() if k in ('VIBECRAFTED_RUNTIME_ROOT','VIBECRAFTED_RUNTIME_BIN','VIBECRAFTED_ROOT','VIBECRAFTED_TERMINAL_HOST','VIBECRAFTED_VC_FRAME_BIN','VIBECRAFTED_PYTHON','XDG_CONFIG_HOME','VC_FRAME_CONFIG_DIR','VC_FRAME_CONFIG_FILE','PYTHONPATH')}}}}))\n"
    )
    host.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    for key in (
        "VIBECRAFTED_RUNTIME_ROOT",
        "VIBECRAFTED_ROOT",
        "VIBECRAFTED_TERMINAL_HOST",
        "VIBECRAFTED_PYTHON",
        "VIBECRAFTED_VC_FRAME_BIN",
        "VC_FRAME_CONFIG_DIR",
        "VC_FRAME_CONFIG_FILE",
        "XDG_CONFIG_HOME",
        "PYTHONPATH",
    ):
        monkeypatch.setenv(key, str(tmp_path / "foreign"))
    before = _snapshot(home)
    result = subprocess.run(
        [str(wrapper), *arguments], capture_output=True, text=True, check=False
    )
    assert result.returncode == (0 if accepted else 2), result.stderr
    assert _snapshot(home) == before
    if accepted:
        capture = json.loads(result.stdout)
        assert capture["argv"] == ["--config-file", str(entry), *arguments]
        environment = capture["env"]
        for key in ("VIBECRAFTED_RUNTIME_ROOT", "VIBECRAFTED_ROOT"):
            assert environment[key] == str(generation)
        assert environment["VIBECRAFTED_RUNTIME_BIN"] == str(generation / "bin")
        assert environment["VIBECRAFTED_TERMINAL_HOST"] == str(host)
        assert environment["VIBECRAFTED_VC_FRAME_BIN"] == str(
            generation / "libexec/vc-frame"
        )
        assert environment["VIBECRAFTED_PYTHON"] == str(generation / "bin/python3")
        assert environment["XDG_CONFIG_HOME"] == str(home / ".config")
        assert environment["VC_FRAME_CONFIG_DIR"] == str(
            home / ".config/vibecrafted/vc-frame"
        )
        assert "VC_FRAME_CONFIG_FILE" not in environment
        assert "PYTHONPATH" not in environment
    else:
        assert "--config-file is product-owned" in result.stderr
        assert not result.stdout


# MARK: - Configuration self-repair
#
# `runtime-repair` is the owner the App calls at launch and behind Repair
# Runtime. It shares `_reconcile_runtime_preference`, the publication
# transaction and the install lease with `runtime-install`, so these tests
# assert the two verbs converge rather than that a second engine also works.


def _repair(paths: dict, capsys, *, plan: bool, status: str) -> dict:
    code = installer.cmd_runtime_repair(
        Namespace(runtime_home=str(paths["runtime_home"]), plan=plan)
    )
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema"] == installer.CONFIG_REPAIR_SCHEMA
    assert envelope["status"] == status, envelope
    assert envelope["mode"] == ("plan" if plan else "apply")
    assert code == (2 if status in {"unusable", "conflict"} else 0)
    # A repair envelope is printed into receipts and dialogs: it carries paths,
    # actions and reasons, never preference values.
    for entry in envelope["files"]:
        assert set(entry) == {"path", "action", "reason", "backup"}
    return envelope


def _backups(paths: dict) -> dict[str, tuple]:
    return _snapshot(paths["runtime_home"] / ".installer-backups")


def test_config_repair_reports_absent_without_creating_roots(roots, capsys):
    """Nothing installed is not something to repair: onboarding owns that."""
    envelope = _repair(roots, capsys, plan=True, status="absent")
    assert envelope["files"] == []
    assert not roots["runtime_home"].exists()
    assert not roots["product_config"].exists()
    _repair(roots, capsys, plan=False, status="absent")
    assert not roots["runtime_home"].exists()
    assert not roots["product_config"].exists()


def test_config_repair_leaves_valid_configuration_untouched_on_every_launch(
    installed, capsys
):
    """Acceptance 1: a healthy install is never rewritten, first launch or tenth."""
    paths, _, _ = installed
    product = _snapshot(paths["product_config"])
    receipt_path = paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT
    receipt = receipt_path.read_bytes()
    active = (paths["runtime_home"] / "active.json").read_bytes()
    backups = _backups(paths)

    # The read-only plan is held to the resolver's standard: nothing under the
    # whole home may move, not even a lock file.
    home = _snapshot(Path.home())
    for _ in range(2):
        envelope = _repair(paths, capsys, plan=True, status="healthy")
        assert {entry["action"] for entry in envelope["files"]} == {"unchanged"}
        assert envelope["repaired"] == 0 and envelope["conflicts"] == 0
        assert _snapshot(Path.home()) == home

    for _ in range(2):
        envelope = _repair(paths, capsys, plan=False, status="healthy")
        assert envelope["repaired"] == 0
        assert _snapshot(paths["product_config"]) == product
        assert receipt_path.read_bytes() == receipt
        assert (paths["runtime_home"] / "active.json").read_bytes() == active
        assert _backups(paths) == backups
    _resolve(paths, capsys, status="ready")


def test_config_repair_seeds_a_missing_preference_from_the_selected_generation(
    installed, capsys
):
    """Acceptance 4: seeding goes through the canonical store, with provenance."""
    paths, _, result = installed
    policy = paths["product_config"] / "terminal-policy.toml"
    shipped = (
        Path(result["root"]) / "config/vc-terminal/vibecrafted.toml"
    ).read_bytes()
    policy.unlink()
    # Acceptance 8: repair publishes configuration and nothing else. Launcher
    # ownership and the selected generation are the installer's to move.
    launchers = _snapshot(paths["launcher_home"])
    selector = (paths["runtime_home"] / "tools/vibecrafted-current").readlink()

    home = _snapshot(Path.home())
    plan = _repair(paths, capsys, plan=True, status="repairable")
    seed = next(entry for entry in plan["files"] if entry["path"] == str(policy))
    assert seed["action"] == "seed"
    assert seed["reason"] == "product preference is missing"
    assert _snapshot(Path.home()) == home, "a plan must not seed anything"

    envelope = _repair(paths, capsys, plan=False, status="repaired")
    assert _snapshot(paths["launcher_home"]) == launchers
    assert (paths["runtime_home"] / "tools/vibecrafted-current").readlink() == selector
    assert envelope["repaired"] == 1
    assert envelope["generation"] == Path(result["root"]).name
    assert policy.read_bytes() == shipped
    receipt = json.loads(
        (paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT).read_text()
    )
    lineage = receipt["config_defaults"][str(policy)]
    assert lineage["generation"] == result["root"]
    # A second repair is a no-op, and the runtime is usable again.
    _repair(paths, capsys, plan=False, status="healthy")
    _resolve(paths, capsys, status="ready")


def test_config_repair_merges_stale_lineage_preserving_scalar_and_shipped_binds(
    tmp_path, roots, capsys
):
    """Acceptance 2+3: the 4.3.0 -> 4.3.1 merge, reached through repair.

    Models an operator who restored their pre-upgrade configuration and its
    receipt lineage from `.installer-backups` while the new generation stayed
    selected. Repair must carry the user's scalar onto the shipped keybinds —
    the same merge the reinstall performs, not a second interpretation of it.
    """
    reproducer = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "fixtures/kdl-runtime-preference-upgrade.json"
        ).read_text(encoding="utf-8")
    )
    incoming = (
        Path(__file__).resolve().parents[2]
        / "vibecrafted-core/vibecrafted_core/config/vc-frame/config.kdl"
    ).read_text(encoding="utf-8")
    shipped_keybinds = reproducer["shipped_keybinds"]
    assert shipped_keybinds in incoming
    previous = incoming.replace(shipped_keybinds, "")
    old = _install(
        seed_runtime_pack(
            tmp_path / "pack-a", version="9.9.9+a", frame_config=previous
        ),
        capsys,
    )
    new = _install(
        seed_runtime_pack(
            tmp_path / "pack-b", version="9.9.10+b", frame_config=incoming
        ),
        capsys,
    )
    config = roots["product_config"] / "vc-frame/config.kdl"
    user = previous.replace(
        reproducer["user_anchor"],
        f"{reproducer['user_anchor']}\n{reproducer['user_scalar']}",
    )
    config.write_text(user, encoding="utf-8")
    receipt_path = roots["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT
    receipt = json.loads(receipt_path.read_text())
    old_source = (
        Path(old["root"])
        / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    )
    assert old_source.is_file(), "the previous generation must survive to be a baseline"
    receipt["config_defaults"][str(config)] = {
        "generation": old["root"],
        "sha256": installer._sha256_path(old_source),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    plan = _repair(roots, capsys, plan=True, status="repairable")
    entry = next(item for item in plan["files"] if item["path"] == str(config))
    assert entry["action"] == "repair"
    assert "never merged" in entry["reason"]
    assert config.read_text(encoding="utf-8") == user, "a plan must not merge"

    envelope = _repair(roots, capsys, plan=False, status="repaired")
    assert envelope["repaired"] == 1
    expected = incoming.replace(
        reproducer["user_anchor"],
        f"{reproducer['user_anchor']}\n{reproducer['user_scalar']}",
    )
    assert config.read_text(encoding="utf-8") == expected
    assert reproducer["user_scalar"] in config.read_text(encoding="utf-8")
    assert 'bind "Super n" {' in config.read_text(encoding="utf-8")
    assert 'bind "Super Shift ." {' in config.read_text(encoding="utf-8")
    # Retry is idempotent and the generation is the one already selected.
    assert envelope["generation"] == Path(new["root"]).name
    _repair(roots, capsys, plan=False, status="healthy")
    assert config.read_text(encoding="utf-8") == expected
    _resolve(roots, capsys, status="ready")


def test_config_repair_refuses_malformed_user_preference_with_typed_conflict(
    installed, capsys
):
    """Acceptance 5: preserve, back up, name the conflict — never reset to defaults."""
    paths, _, _ = installed
    config = paths["product_config"] / "vc-frame/config.kdl"
    damaged = config.read_bytes() + b"\n}\n"
    config.write_bytes(damaged)

    home = _snapshot(Path.home())
    plan = _repair(paths, capsys, plan=True, status="conflict")
    entry = next(item for item in plan["files"] if item["path"] == str(config))
    assert entry["action"] == "conflict"
    assert "unbalanced" in entry["reason"]
    assert _snapshot(Path.home()) == home, "a plan must not back anything up"

    envelope = _repair(paths, capsys, plan=False, status="conflict")
    assert envelope["conflicts"] == 1
    conflict = next(item for item in envelope["files"] if item["path"] == str(config))
    assert conflict["action"] == "conflict"
    backup = Path(conflict["backup"])
    assert backup.is_file() and backup.read_bytes() == damaged
    # The user's bytes are still theirs: nothing was reset to shipped defaults.
    assert config.read_bytes() == damaged
    # And the envelope stays a typed structure, not a rendered exception.
    assert "Traceback" not in json.dumps(envelope)


def test_config_repair_reports_managed_tree_drift_as_a_conflict(installed, capsys):
    """Acceptance 5: a custom layout is evidence, not something to silently discard."""
    paths, _, _ = installed
    layout = paths["product_config"] / "vc-frame/layouts/operator.kdl"
    layout.write_text("// operator's own layout\n")
    plan = _repair(paths, capsys, plan=True, status="conflict")
    entry = next(
        item
        for item in plan["files"]
        if item["path"] == str(paths["product_config"] / "vc-frame")
    )
    assert entry["action"] == "conflict"
    assert "selected generation" in entry["reason"]
    assert layout.read_text() == "// operator's own layout\n"


def test_config_repair_rolls_back_an_interrupted_publication(
    installed, tmp_path, capsys
):
    """Acceptance 9: recovery uses the installer's own rollback, not a new one."""
    paths, _, _ = installed
    payload_b = seed_runtime_pack(tmp_path / "pack-b", version="9.9.10+b")
    _crash_install(payload_b, paths, "launcher")
    receipt_path = paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT
    assert "config_transaction" in json.loads(receipt_path.read_text())
    _resolve(paths, capsys, status="unusable")

    plan = _repair(paths, capsys, plan=True, status="repairable")
    assert [entry["action"] for entry in plan["files"]] == ["rollback"]
    assert "config_transaction" in json.loads(receipt_path.read_text())

    envelope = _repair(paths, capsys, plan=False, status="healthy")
    assert envelope["rolled_back"] is True
    receipt = json.loads(receipt_path.read_text())
    assert "config_transaction" not in receipt
    # Recovery is work, and it leaves the same durable trace a merge does.
    assert receipt["config_repairs"][-1]["rolled_back"] is True
    _resolve(paths, capsys, status="ready")


def test_config_repair_refuses_while_a_publication_holds_the_lease(installed, capsys):
    """Acceptance 6: one lease, so a launch cannot race a reinstall."""
    paths, _, _ = installed
    current = paths["runtime_home"] / "tools/vibecrafted-current"
    lock = installer._tools_install_lease_path(current)
    descriptor = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        envelope = _repair(paths, capsys, plan=True, status="unusable")
        assert "publication is in progress" in envelope["reason"]
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    _repair(paths, capsys, plan=True, status="healthy")


def test_config_repair_records_one_redacted_receipt(installed, capsys):
    """Acceptance 7: durable evidence with fields and reasons, never values."""
    paths, _, result = installed
    policy = paths["product_config"] / "terminal-policy.toml"
    secret = policy.read_text(encoding="utf-8")
    policy.unlink()
    _repair(paths, capsys, plan=False, status="repaired")
    receipt = json.loads(
        (paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT).read_text()
    )
    repairs = receipt["config_repairs"]
    assert len(repairs) == 1
    record = repairs[0]
    assert record["generation"] == result["root"]
    assert [entry["action"] for entry in record["files"]] == ["seeded"]
    assert record["files"][0]["path"] == str(policy)
    blob = json.dumps(receipt)
    assert secret.strip() not in blob
    assert "Traceback" not in blob


_REPO_TERMINAL_POLICY = (
    Path(__file__).resolve().parents[2] / "config/vc-terminal/vibecrafted.toml"
)
_INCOMING_SHELL = (
    'shell = { program = "/bin/sh", args = ["-c", "exec \\"$HOME/.config/vibecrafted/vc-terminal/launch-primary-shell.zsh\\""] }'
)
_PREVIOUS_SHELL = (
    'shell = { program = "/bin/zsh", args = ["-lc", "exec \\"${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/vc-terminal/launch-primary-shell.zsh\\" \\"$VIBECRAFTED_RUNTIME_ROOT/bin/vc-start\\" operator"] }'
)
_USER_STRIPPED_SHELL = (
    'shell = { program = "/bin/zsh", args = ["-lc", "exec \\"${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/vc-terminal/launch-primary-shell.zsh\\""] }'
)
_EXPLICIT_FISH_SHELL = (
    'shell = { program = "/usr/bin/fish", args = ["-l"] }'
)


def _previous_terminal_policy() -> str:
    return (
        _REPO_TERMINAL_POLICY.read_text(encoding="utf-8")
        .replace(_INCOMING_SHELL, _PREVIOUS_SHELL)
        .replace("padding = { x = 8, y = 24 }", "padding = { x = 0, y = 0 }")
    )


def _user_terminal_policy(*, shell: str = _USER_STRIPPED_SHELL) -> str:
    return (
        _previous_terminal_policy()
        .replace(_PREVIOUS_SHELL, shell)
        .replace('family = "Spot Mono"', 'family = "User Mono"', 1)
        .replace('background = "#0b0b12"', 'background = "#111111"')
        # Trailing [[keyboard.bindings]] group, split from the first by other tables.
        .replace('key = "Enter"\nmods = "Shift"', 'key = "Enter"\nmods = "Control"')
    )


def test_three_way_shell_correction_keeps_user_chrome_and_accepts_new_defaults(
    tmp_path, roots, capsys
):
    """Exact prior/current/incoming shell: user stripped operator args only."""
    previous = _previous_terminal_policy()
    incoming = _REPO_TERMINAL_POLICY.read_text(encoding="utf-8")
    assert _PREVIOUS_SHELL in previous
    assert _INCOMING_SHELL in incoming
    _install(
        seed_runtime_pack(
            tmp_path / "pack-a", version="9.9.9+a", terminal_policy=previous
        ),
        capsys,
    )
    policy = roots["product_config"] / "terminal-policy.toml"
    user = _user_terminal_policy()
    policy.write_text(user, encoding="utf-8")
    selector = (roots["runtime_home"] / "tools/vibecrafted-current").readlink()
    _install(
        seed_runtime_pack(
            tmp_path / "pack-b", version="9.9.10+b", terminal_policy=incoming
        ),
        capsys,
    )
    text = policy.read_text(encoding="utf-8")
    assert _INCOMING_SHELL in text
    assert "padding = { x = 8, y = 24 }" in text
    assert 'family = "User Mono"' in text
    assert 'background = "#111111"' in text
    assert 'mods = "Control"' in text
    assert "vc-start" not in text or "operator" not in text.split("shell =", 1)[1].split("\n", 1)[0]
    assert (roots["runtime_home"] / "tools/vibecrafted-current").readlink() != selector
    _resolve(roots, capsys, status="ready")


def test_disjoint_toml_edits_still_merge(installed, tmp_path, capsys):
    paths, _, _ = installed
    policy = paths["product_config"] / "terminal-policy.toml"
    policy.write_text(policy.read_text().replace("opacity = 0.9", "opacity = 0.75"))
    original = installed[1] / "config/vc-terminal/vibecrafted.toml"
    _install(
        seed_runtime_pack(
            tmp_path / "pack-b",
            version="9.9.10+b",
            terminal_policy=original.read_text().replace(
                "history = 50000", "history = 60000"
            ),
        ),
        capsys,
    )
    merged = policy.read_text()
    assert "opacity = 0.75" in merged
    assert "history = 60000" in merged
    _resolve(paths, capsys, status="ready")


def test_explicit_shell_preference_stays_a_bound_choice(tmp_path, roots, capsys):
    previous = _previous_terminal_policy()
    incoming = _REPO_TERMINAL_POLICY.read_text(encoding="utf-8")
    _install(
        seed_runtime_pack(
            tmp_path / "pack-a", version="9.9.9+a", terminal_policy=previous
        ),
        capsys,
    )
    policy = roots["product_config"] / "terminal-policy.toml"
    user = _user_terminal_policy(shell=_EXPLICIT_FISH_SHELL)
    policy.write_text(user, encoding="utf-8")
    current_sha = installer._sha256_path(policy)
    incoming_source = tmp_path / "pack-b/config/vc-terminal/vibecrafted.toml"
    payload = seed_runtime_pack(
        tmp_path / "pack-b", version="9.9.10+b", terminal_policy=incoming
    )
    incoming_sha = installer._sha256_path(incoming_source)
    selector = (roots["runtime_home"] / "tools/vibecrafted-current").readlink()
    active = (roots["runtime_home"] / "active.json").read_bytes()
    conflict = _install_conflict(payload, capsys)
    assert conflict.envelope["schema"] == installer.PREFERENCE_CONFLICT_SCHEMA
    assert conflict.envelope["previous_runtime_available"] is True
    assert "terminal.shell" in json.dumps(conflict.envelope)
    assert "/usr/bin/fish" not in json.dumps(conflict.envelope)
    assert "Traceback" not in json.dumps(conflict.envelope)
    assert "^^^^" not in json.dumps(conflict.envelope)
    receipt = json.loads(
        (roots["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT).read_text()
    )
    assert "config_conflicts" not in receipt
    assert receipt["candidate_conflicts"]
    assert (roots["runtime_home"] / "tools/vibecrafted-current").readlink() == selector
    assert (roots["runtime_home"] / "active.json").read_bytes() == active
    _resolve(roots, capsys, status="ready")

    drifted = policy.read_text(encoding="utf-8").replace("opacity = 0.9", "opacity = 0.5")
    policy.write_text(drifted, encoding="utf-8")
    concurrent = _install_conflict(
        payload,
        capsys,
        resolve_preference="keep-current",
        preference_current_sha256=current_sha,
        preference_incoming_sha256=incoming_sha,
        preference_path=str(policy),
    )
    assert "concurrent" in str(concurrent).lower() or "changed during retry" in str(
        concurrent
    )
    policy.write_text(user, encoding="utf-8")
    for _ in range(2):
        _install(
            payload,
            capsys,
            resolve_preference="keep-current",
            preference_current_sha256=current_sha,
            preference_incoming_sha256=incoming_sha,
            preference_path=str(policy),
        )
        assert _EXPLICIT_FISH_SHELL in policy.read_text(encoding="utf-8")
        assert "padding = { x = 8, y = 24 }" in policy.read_text(encoding="utf-8")
        _resolve(roots, capsys, status="ready")


def test_use_incoming_shell_choice_is_bound_and_preserves_chrome(tmp_path, roots, capsys):
    previous = _previous_terminal_policy()
    incoming = _REPO_TERMINAL_POLICY.read_text(encoding="utf-8")
    _install(
        seed_runtime_pack(
            tmp_path / "pack-a", version="9.9.9+a", terminal_policy=previous
        ),
        capsys,
    )
    policy = roots["product_config"] / "terminal-policy.toml"
    user = _user_terminal_policy(shell=_EXPLICIT_FISH_SHELL)
    policy.write_text(user, encoding="utf-8")
    current_sha = installer._sha256_path(policy)
    incoming_source = tmp_path / "pack-b/config/vc-terminal/vibecrafted.toml"
    payload = seed_runtime_pack(
        tmp_path / "pack-b", version="9.9.10+b", terminal_policy=incoming
    )
    incoming_sha = installer._sha256_path(incoming_source)
    _install_conflict(payload, capsys)
    _resolve(roots, capsys, status="ready")
    _install(
        payload,
        capsys,
        resolve_preference="use-incoming",
        preference_current_sha256=current_sha,
        preference_incoming_sha256=incoming_sha,
        preference_path=str(policy),
    )
    text = policy.read_text(encoding="utf-8")
    assert _INCOMING_SHELL in text
    assert _EXPLICIT_FISH_SHELL not in text
    assert 'family = "User Mono"' in text
    assert 'background = "#111111"' in text
    assert 'mods = "Control"' in text
    _resolve(roots, capsys, status="ready")
    _install(
        payload,
        capsys,
        resolve_preference="use-incoming",
        preference_current_sha256=current_sha,
        preference_incoming_sha256=incoming_sha,
        preference_path=str(policy),
    )
    assert _INCOMING_SHELL in policy.read_text(encoding="utf-8")
    _resolve(roots, capsys, status="ready")


def test_failed_upgrade_without_prior_runtime_is_not_ready(roots, tmp_path, capsys):
    incoming = _REPO_TERMINAL_POLICY.read_text(encoding="utf-8")
    policy = roots["product_config"] / "terminal-policy.toml"
    policy.parent.mkdir(parents=True)
    policy.write_text(_user_terminal_policy(shell=_EXPLICIT_FISH_SHELL), encoding="utf-8")
    conflict = _install_conflict(
        seed_runtime_pack(
            tmp_path / "pack-a", version="9.9.9+a", terminal_policy=incoming
        ),
        capsys,
    )
    assert conflict.envelope["previous_runtime_available"] is False
    assert "not connected" in conflict.envelope["message"].lower() or (
        "no previously verified runtime" in conflict.envelope["message"].lower()
    )
    envelope = _resolve(roots, capsys, status="unusable")
    assert envelope["runtime"] is None
    assert envelope["status"] == "unusable"


def test_cli_preference_conflict_is_json_without_traceback(tmp_path, roots, capsys):
    previous = _previous_terminal_policy()
    incoming = _REPO_TERMINAL_POLICY.read_text(encoding="utf-8")
    _install(
        seed_runtime_pack(
            tmp_path / "pack-a", version="9.9.9+a", terminal_policy=previous
        ),
        capsys,
    )
    policy = roots["product_config"] / "terminal-policy.toml"
    policy.write_text(_user_terminal_policy(shell=_EXPLICIT_FISH_SHELL), encoding="utf-8")
    payload = seed_runtime_pack(
        tmp_path / "pack-b", version="9.9.10+b", terminal_policy=incoming
    )
    code = installer.main(
        ["runtime-install", "--payload-root", str(payload)]
    )
    captured = capsys.readouterr()
    assert code == 2
    envelope = json.loads(captured.out.splitlines()[-1])
    assert envelope["schema"] == installer.PREFERENCE_CONFLICT_SCHEMA
    assert "terminal.shell" in json.dumps(envelope)
    assert "/usr/bin/fish" not in captured.out
    assert "/usr/bin/fish" not in captured.err
    assert "Traceback" not in captured.err
    assert "^^^^" not in captured.err
    _resolve(roots, capsys, status="ready")


def _assert_toml_tree(text: str, intended: dict) -> None:
    """Parsed merge output must equal the intended resolved tree."""
    assert installer._toml_trees_equal(tomllib.loads(text), intended)


def test_toml_merge_respects_user_deletion_of_unchanged_default():
    """Current removed opacity; incoming still ships previous 0.8 and new history."""
    previous = "[window]\nopacity = 0.8\n\n[scrolling]\nhistory = 100\n"
    current = "[scrolling]\nhistory = 100\n"
    incoming = "[window]\nopacity = 0.8\n\n[scrolling]\nhistory = 200\n"
    merged = installer._merge_toml_runtime_preferences(previous, current, incoming)
    _assert_toml_tree(merged, {"scrolling": {"history": 200}})
    assert "opacity" not in tomllib.loads(merged).get("window", {})


def test_toml_merge_keep_current_preserves_deletion_against_changed_incoming():
    previous = "[window]\nopacity = 0.8\n"
    current = "# kept empty\n"
    incoming = "[window]\nopacity = 0.9\n"
    with pytest.raises(ValueError, match="settings conflict"):
        installer._merge_toml_runtime_preferences(previous, current, incoming)
    kept = installer._merge_toml_runtime_preferences(
        previous, current, incoming, choice="keep-current"
    )
    _assert_toml_tree(kept, {})
    assert "opacity" not in tomllib.loads(kept)
    incoming_choice = installer._merge_toml_runtime_preferences(
        previous, current, incoming, choice="use-incoming"
    )
    _assert_toml_tree(incoming_choice, {"window": {"opacity": 0.9}})


def test_toml_merge_array_table_stays_under_keyboard_not_window():
    previous = (
        '[[keyboard.bindings]]\nkey = "A"\naction = "Copy"\n\n[window]\nopacity = 0.8\n'
    )
    current = (
        '[[keyboard.bindings]]\nkey = "B"\naction = "Copy"\n\n[window]\nopacity = 0.8\n'
    )
    incoming = (
        '[[keyboard.bindings]]\nkey = "A"\naction = "Copy"\n\n[window]\nopacity = 0.9\n'
    )
    merged = installer._merge_toml_runtime_preferences(previous, current, incoming)
    parsed = tomllib.loads(merged)
    _assert_toml_tree(
        merged,
        {
            "keyboard": {"bindings": [{"key": "B", "action": "Copy"}]},
            "window": {"opacity": 0.9},
        },
    )
    assert "keyboard" not in parsed.get("window", {})


def test_toml_raw_assignment_joins_array_table_span_strings():
    text = (
        '[[keyboard.bindings]]\nkey = "B"\n\n[window]\nopacity = 0.8\n\n'
        '[[keyboard.bindings]]\nkey = "C"\n'
    )
    raw = installer._toml_raw_assignment(text, "keyboard.bindings")
    assert isinstance(raw, str)
    assert 'key = "B"' in raw
    assert 'key = "C"' in raw


def test_toml_merge_fail_closed_on_dotted_quoted_key():
    previous = '"foo.bar" = 1\n'
    current = '"foo.bar" = 2\n'
    incoming = '"foo.bar" = 1\n'
    with pytest.raises(ValueError, match="quoted or dotted"):
        installer._merge_toml_runtime_preferences(previous, current, incoming)


def test_preference_shell_correction_is_exact_not_first_flag():
    previous = {
        "program": "/bin/zsh",
        "args": [
            "-lc",
            'exec "launch-primary-shell.zsh" "$VIBECRAFTED_RUNTIME_ROOT/bin/vc-start" operator',
        ],
    }
    allowed = {
        "program": "/bin/zsh",
        "args": ["-lc", 'exec "launch-primary-shell.zsh"'],
    }
    extra = {
        "program": "/bin/zsh",
        "args": ["-lc", 'exec "launch-primary-shell.zsh"; echo extra; curl evil'],
    }
    incoming = {
        "program": "/bin/sh",
        "args": ["-c", 'exec "launch-primary-shell.zsh"'],
    }
    assert installer._preference_shell_is_previous_minus_operator(previous, allowed)
    assert not installer._preference_shell_is_previous_minus_operator(previous, extra)
    assert installer._preference_shell_accepts_incoming(previous, allowed, incoming)
    assert not installer._preference_shell_accepts_incoming(previous, extra, incoming)
    previous_toml = (
        "[terminal]\n"
        'shell = { program = "/bin/zsh", args = ["-lc", '
        '"exec \\"launch-primary-shell.zsh\\" \\"$VIBECRAFTED_RUNTIME_ROOT/bin/vc-start\\" operator"] }\n'
    )
    extra_toml = (
        "[terminal]\n"
        'shell = { program = "/bin/zsh", args = ["-lc", '
        '"exec \\"launch-primary-shell.zsh\\"; echo extra"] }\n'
    )
    incoming_toml = (
        "[terminal]\n"
        'shell = { program = "/bin/sh", args = ["-c", '
        '"exec \\"launch-primary-shell.zsh\\""] }\n'
    )
    with pytest.raises(ValueError, match="settings conflict"):
        installer._merge_toml_runtime_preferences(
            previous_toml, extra_toml, incoming_toml
        )
    kept = installer._merge_toml_runtime_preferences(
        previous_toml, extra_toml, incoming_toml, choice="keep-current"
    )
    _assert_toml_tree(kept, tomllib.loads(extra_toml))
    assert "[terminal.shell]" not in kept
    assert 'program = "/usr/bin/fish"' not in kept
    assert 'echo extra' in kept


def test_toml_flatten_keeps_shell_record_and_bindings_path():
    parsed = tomllib.loads(
        "[terminal]\n"
        'shell = { program = "/bin/zsh", args = ["-lc"] }\n'
        "\n"
        "[[keyboard.bindings]]\n"
        'key = "B"\n'
        'action = "Copy"\n'
        "\n"
        "[window]\n"
        "opacity = 0.8\n"
        "padding = { x = 8, y = 24 }\n"
    )
    flat = installer._toml_flatten(parsed)
    assert flat["terminal.shell"] == {"program": "/bin/zsh", "args": ["-lc"]}
    assert "terminal.shell.program" not in flat
    assert "terminal.shell.args" not in flat
    assert flat["keyboard.bindings"] == [{"key": "B", "action": "Copy"}]
    assert "keyboard" not in flat
    assert flat["window.opacity"] == 0.8
    assert flat["window.padding.x"] == 8
    assert flat["window.padding.y"] == 24
    assert "window.padding" not in flat
    empty_bindings = installer._toml_flatten(tomllib.loads("[[keyboard.bindings]]\n"))
    assert empty_bindings == {"keyboard.bindings": [{}]}
    explicit_empty = installer._toml_flatten(tomllib.loads("[keyboard]\nbindings = []\n"))
    assert explicit_empty == {"keyboard.bindings": []}
    assert "keyboard" not in explicit_empty


def test_toml_keep_current_nested_shell_replaces_inline_without_duplicate():
    previous = (
        "[terminal]\n"
        "[terminal.shell]\n"
        'program = "/bin/zsh"\n'
        'args = ["-lc", "exec \\"launch-primary-shell.zsh\\" operator"]\n'
    )
    current = (
        "[terminal]\n"
        "[terminal.shell]\n"
        'program = "/usr/bin/fish"\n'
        'args = ["-l"]\n'
    )
    incoming = (
        "[terminal]\n"
        'shell = { program = "/bin/sh", args = ["-c", "exec \\"launch-primary-shell.zsh\\""] }\n'
        "other = 1\n"
    )
    with pytest.raises(ValueError, match="settings conflict"):
        installer._merge_toml_runtime_preferences(previous, current, incoming)
    kept = installer._merge_toml_runtime_preferences(
        previous, current, incoming, choice="keep-current"
    )
    _assert_toml_tree(
        kept,
        {
            "terminal": {
                "shell": {"program": "/usr/bin/fish", "args": ["-l"]},
                "other": 1,
            }
        },
    )
    parsed = tomllib.loads(kept)
    assert parsed["terminal"]["shell"]["program"] == "/usr/bin/fish"
    assert "shell" in parsed["terminal"]
    assert kept.count("[terminal.shell]") + kept.count("shell =") == 1


def test_toml_merge_same_table_disjoint_scalars_do_not_conflict():
    """Root probe: sibling scalars keep independent identity."""
    previous = '[window]\nopacity = 0.8\ndecorations = "Full"\n'
    current = '[window]\nopacity = 0.9\ndecorations = "Full"\n'
    incoming = '[window]\nopacity = 0.8\ndecorations = "None"\n'
    assert installer._toml_flatten(tomllib.loads(previous)) == {
        "window.opacity": 0.8,
        "window.decorations": "Full",
    }
    merged = installer._merge_toml_runtime_preferences(previous, current, incoming)
    _assert_toml_tree(
        merged, {"window": {"opacity": 0.9, "decorations": "None"}}
    )
    assert "settings conflict" not in merged


def test_toml_merge_sibling_add_or_remove_does_not_rebind_identity():
    previous = '[window]\nopacity = 0.8\ndecorations = "Full"\n'
    current = (
        '[window]\nopacity = 0.8\ndecorations = "Full"\nstartup_mode = "Maximized"\n'
    )
    incoming = '[window]\nopacity = 0.9\ndecorations = "None"\n'
    merged = installer._merge_toml_runtime_preferences(previous, current, incoming)
    _assert_toml_tree(
        merged,
        {
            "window": {
                "opacity": 0.9,
                "decorations": "None",
                "startup_mode": "Maximized",
            }
        },
    )
    removed = installer._merge_toml_runtime_preferences(current, previous, incoming)
    _assert_toml_tree(
        removed, {"window": {"opacity": 0.9, "decorations": "None"}}
    )


def test_toml_merge_inline_and_nested_window_are_the_same_settings():
    previous = '[window]\nopacity = 0.8\ndecorations = "Full"\n'
    current = '[window]\nopacity = 0.9\ndecorations = "Full"\n'
    incoming = 'window = { opacity = 0.8, decorations = "None" }\n'
    merged = installer._merge_toml_runtime_preferences(previous, current, incoming)
    _assert_toml_tree(
        merged, {"window": {"opacity": 0.9, "decorations": "None"}}
    )
    nested_in = '[window]\nopacity = 0.8\ndecorations = "None"\n'
    inline_cur = 'window = { opacity = 0.9, decorations = "Full" }\n'
    crossed = installer._merge_toml_runtime_preferences(
        previous, inline_cur, nested_in
    )
    _assert_toml_tree(
        crossed, {"window": {"opacity": 0.9, "decorations": "None"}}
    )


def test_toml_merge_disjoint_padding_leaves_keep_inline_form():
    previous = "[window]\npadding = { x = 0, y = 0 }\nopacity = 0.8\n"
    current = "[window]\npadding = { x = 4, y = 0 }\nopacity = 0.8\n"
    incoming = "[window]\npadding = { x = 0, y = 24 }\nopacity = 0.9\n"
    merged = installer._merge_toml_runtime_preferences(previous, current, incoming)
    _assert_toml_tree(
        merged, {"window": {"padding": {"x": 4, "y": 24}, "opacity": 0.9}}
    )
    assert "padding = { x = 4, y = 24 }" in merged
    assert "[window.padding]" not in merged


def test_toml_merge_comment_on_sibling_survives_disjoint_edit():
    previous = '[window]\nopacity = 0.8\ndecorations = "Full"  # keep-chrome\n'
    current = '[window]\nopacity = 0.9\ndecorations = "Full"  # keep-chrome\n'
    incoming = '[window]\nopacity = 0.8\ndecorations = "None"  # keep-chrome\n'
    merged = installer._merge_toml_runtime_preferences(previous, current, incoming)
    _assert_toml_tree(
        merged, {"window": {"opacity": 0.9, "decorations": "None"}}
    )
    assert "# keep-chrome" in merged


_TOML_WINDOW_FORMS = ("nested", "dotted", "inline")
_TOML_WINDOW_FORM_TRIPLES = tuple(
    (previous_form, current_form, incoming_form)
    for previous_form in _TOML_WINDOW_FORMS
    for current_form in _TOML_WINDOW_FORMS
    for incoming_form in _TOML_WINDOW_FORMS
)
_ORACLE_MISSING = object()


def _window_pref_text(
    form: str, *, opacity: float, decorations: str | None
) -> str:
    if form == "nested":
        text = f"[window]\nopacity = {opacity}\n"
        if decorations is not None:
            text += f'decorations = "{decorations}"\n'
        return text
    if form == "dotted":
        text = f"window.opacity = {opacity}\n"
        if decorations is not None:
            text += f'window.decorations = "{decorations}"\n'
        return text
    if form == "inline":
        if decorations is None:
            return f"window = {{ opacity = {opacity} }}\n"
        return f'window = {{ opacity = {opacity}, decorations = "{decorations}" }}\n'
    raise AssertionError(form)


def _oracle_leaf_map(text: str) -> dict[str, object]:
    """Independent tomllib walk — not installer flatten or atomic exceptions."""

    def walk(node: object, prefix: str) -> dict[str, object]:
        if not isinstance(node, dict):
            return {prefix: node} if prefix else {}
        leaves: dict[str, object] = {}
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                leaves.update(walk(value, path))
            else:
                leaves[path] = value
        return leaves

    return walk(tomllib.loads(text), "")


def _oracle_three_way_tree(previous: str, current: str, incoming: str) -> dict:
    prev = _oracle_leaf_map(previous)
    curr = _oracle_leaf_map(current)
    inc = _oracle_leaf_map(incoming)
    resolved: dict[str, object] = {}
    for key in sorted(set(prev) | set(curr) | set(inc)):
        prev_value = prev.get(key, _ORACLE_MISSING)
        curr_value = curr.get(key, _ORACLE_MISSING)
        inc_value = inc.get(key, _ORACLE_MISSING)
        if curr_value == inc_value:
            if curr_value is not _ORACLE_MISSING:
                resolved[key] = curr_value
            continue
        if curr_value == prev_value:
            if inc_value is not _ORACLE_MISSING:
                resolved[key] = inc_value
            continue
        if inc_value == prev_value:
            if curr_value is not _ORACLE_MISSING:
                resolved[key] = curr_value
            continue
        raise AssertionError(
            f"oracle conflict on {key}: {prev_value!r} {curr_value!r} {inc_value!r}"
        )
    tree: dict = {}
    for dotted, value in resolved.items():
        node = tree
        parts = dotted.split(".")
        for part in parts[:-1]:
            child = node.get(part)
            if child is None:
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
    return tree


def test_toml_overlay_dotted_raw_keeps_nested_table_path():
    incoming = '[window]\nopacity = 0.8\ndecorations = "None"\n'
    overlayed = installer._overlay_toml_assignment(
        incoming, "window.opacity", "window.opacity = 0.9\n"
    )
    assert tomllib.loads(overlayed) == {
        "window": {"opacity": 0.9, "decorations": "None"}
    }
    after_header = overlayed.split("[window]", 1)[-1]
    assert "window.opacity" not in after_header


@pytest.mark.parametrize(
    "previous_form,current_form,incoming_form", _TOML_WINDOW_FORM_TRIPLES
)
def test_toml_merge_all_window_forms_keep_independent_leaves(
    previous_form, current_form, incoming_form
):
    previous = _window_pref_text(previous_form, opacity=0.8, decorations="Full")
    current = _window_pref_text(current_form, opacity=0.9, decorations="Full")
    incoming = _window_pref_text(incoming_form, opacity=0.8, decorations="None")
    expected = _oracle_three_way_tree(previous, current, incoming)
    assert expected == {"window": {"opacity": 0.9, "decorations": "None"}}
    merged = installer._merge_toml_runtime_preferences(previous, current, incoming)
    assert tomllib.loads(merged) == expected


@pytest.mark.parametrize(
    "previous_form,current_form,incoming_form", _TOML_WINDOW_FORM_TRIPLES
)
def test_toml_merge_all_window_forms_honor_incoming_deletion(
    previous_form, current_form, incoming_form
):
    previous = _window_pref_text(previous_form, opacity=0.8, decorations="Full")
    current = _window_pref_text(current_form, opacity=0.9, decorations="Full")
    incoming = _window_pref_text(incoming_form, opacity=0.8, decorations=None)
    expected = _oracle_three_way_tree(previous, current, incoming)
    assert expected == {"window": {"opacity": 0.9}}
    merged = installer._merge_toml_runtime_preferences(previous, current, incoming)
    parsed = tomllib.loads(merged)
    assert parsed == expected
    assert "decorations" not in parsed.get("window", {})


def test_published_previous_receipt_rejects_config_conflicts():
    healthy = {
        "schema": installer.RUNTIME_INSTALL_SCHEMA,
        "version": "9.9.9+a",
    }
    poisoned = {
        **healthy,
        "config_conflicts": [{"path": "terminal-policy.toml"}],
    }
    assert (
        installer._published_previous_receipt(
            {"preparing_previous_receipt": healthy}
        )
        is not None
    )
    assert (
        installer._published_previous_receipt(
            {"preparing_previous_receipt": poisoned}
        )
        is None
    )


def test_abandon_unpublished_requires_physical_previous_generation(tmp_path):
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    saved = {
        "schema": installer.RUNTIME_INSTALL_SCHEMA,
        "version": "9.9.9+ghost",
        "owned_symlinks": {},
    }
    receipt = {
        "schema": installer.RUNTIME_INSTALL_SCHEMA,
        "install_pending": True,
        "install_phase": "preparing",
        "preparing_previous_receipt": saved,
        "roots": {},
    }
    available = installer._abandon_unpublished_preference_conflicts(
        runtime_home=runtime_home,
        receipt=receipt,
        conflicts=[{"path": "terminal-policy.toml", "reason": "overlap"}],
    )
    assert available is False
    assert receipt["candidate_conflicts"]
    assert "config_conflicts" not in receipt

    version = "9.9.9+real"
    generation = runtime_home / "releases" / version
    generation.mkdir(parents=True)
    current = runtime_home / "tools/vibecrafted-current"
    current.parent.mkdir(parents=True)
    current.symlink_to(generation)
    (runtime_home / "active.json").write_text(
        json.dumps(
            {
                "schema": "vibecrafted.active-runtime.v1",
                "runtime_root": str(generation),
                "version": version,
            }
        ),
        encoding="utf-8",
    )
    physical = {
        "schema": installer.RUNTIME_INSTALL_SCHEMA,
        "version": version,
        "owned_symlinks": {str(current): str(generation)},
    }
    live = {
        "schema": installer.RUNTIME_INSTALL_SCHEMA,
        "install_pending": True,
        "install_phase": "preparing",
        "preparing_previous_receipt": physical,
        "roots": {},
    }
    assert (
        installer._abandon_unpublished_preference_conflicts(
            runtime_home=runtime_home,
            receipt=live,
            conflicts=[{"path": "terminal-policy.toml", "reason": "overlap"}],
        )
        is True
    )
