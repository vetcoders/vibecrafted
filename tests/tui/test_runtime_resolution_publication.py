"""Exercise the installer owner with sealed generations and disposable roots."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
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


def _install(payload: Path, capsys) -> dict:
    assert (
        installer.cmd_runtime_install(
            Namespace(
                payload_root=str(payload),
                app_root=None,
                terminal_host=None,
                frame_helper=None,
            )
        )
        == 0
    )
    return json.loads(capsys.readouterr().out.splitlines()[-1])


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


def test_both_changed_kdl_refuses_publication_and_preserves_evidence(
    installed, tmp_path, capsys
):
    paths, _, result = installed
    product = paths["product_config"]
    config = product / "vc-frame/config.kdl"
    config.write_bytes(config.read_bytes() + b"\ncopy_on_select true\n")
    before = _snapshot(product)
    active = (paths["runtime_home"] / "active.json").read_bytes()
    source = (
        installed[1] / "vibecrafted-core/vibecrafted_core/config/vc-frame/config.kdl"
    )
    payload_b = seed_runtime_pack(
        tmp_path / "pack-b",
        version="9.9.10+b",
        frame_config=source.read_text() + "\n// changed shipped KDL\n",
    )
    with pytest.raises(
        RuntimeError, match="both user KDL and shipped defaults changed"
    ):
        _install(payload_b, capsys)
    capsys.readouterr()
    assert _snapshot(product) == before
    assert (paths["runtime_home"] / "active.json").read_bytes() == active
    receipt = json.loads(
        (paths["runtime_home"] / installer.RUNTIME_INSTALL_RECEIPT).read_text()
    )
    conflict = receipt["config_conflicts"][0]
    assert Path(conflict["backup"]).read_bytes() == config.read_bytes()
    assert Path(conflict["previous_defaults"]).is_file()
    assert Path(conflict["incoming_defaults"]).is_file()
    _resolve(paths, capsys, status="unusable")


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
        f"#!{sys.executable}\nimport json, os, sys\nprint(json.dumps({{'argv':sys.argv[1:], 'env':{{k:v for k,v in os.environ.items() if k in ('VIBECRAFTED_RUNTIME_ROOT','VIBECRAFTED_ROOT','VIBECRAFTED_TERMINAL_HOST','VIBECRAFTED_VC_FRAME_BIN','VIBECRAFTED_PYTHON','XDG_CONFIG_HOME','VC_FRAME_CONFIG_DIR','VC_FRAME_CONFIG_FILE','PYTHONPATH')}}}}))\n"
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
    result = subprocess.run([str(wrapper), *arguments], capture_output=True, text=True)
    assert result.returncode == (0 if accepted else 2), result.stderr
    assert _snapshot(home) == before
    if accepted:
        capture = json.loads(result.stdout)
        assert capture["argv"] == ["--config-file", str(entry), *arguments]
        environment = capture["env"]
        for key in ("VIBECRAFTED_RUNTIME_ROOT", "VIBECRAFTED_ROOT"):
            assert environment[key] == str(generation)
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
