"""Explicit Runtime Pack rescue: missing historical backups, not a second installer."""

from __future__ import annotations

import json
import re
from argparse import Namespace
from pathlib import Path

import pytest
from _runtime_pack_fixture import seed_runtime_pack

from scripts import vetcoders_install as installer


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
    monkeypatch.setattr(
        installer, "_teardown_owned_runtime_for_uninstall", lambda *_a, **_k: ()
    )
    return installer._runtime_install_paths()


def _ns(payload: Path, **extra) -> Namespace:
    values = dict(
        payload_root=str(payload),
        app_root=None,
        terminal_host=None,
        frame_helper=None,
        resolve_preference=None,
        preference_current_sha256=None,
        preference_incoming_sha256=None,
        preference_path=None,
        allow_older_runtime=False,
        rescue=False,
        plan=False,
        apply=False,
        plan_digest=None,
        runtime_home=None,
    )
    values.update(extra)
    return Namespace(**values)


def _install(payload: Path, capsys, **extra) -> dict:
    assert installer.cmd_runtime_install(_ns(payload, **extra)) == 0
    return json.loads(capsys.readouterr().out.splitlines()[-1])


def _plan(payload: Path, capsys, **extra) -> dict:
    code = installer.cmd_runtime_install(
        _ns(payload, rescue=True, plan=True, **extra)
    )
    envelope = json.loads(capsys.readouterr().out.splitlines()[-1])
    return code, envelope


def _apply(payload: Path, capsys, plan_digest: str, **extra) -> tuple[int, dict]:
    code = installer.cmd_runtime_install(
        _ns(payload, rescue=True, apply=True, plan_digest=plan_digest, **extra)
    )
    envelope = json.loads(capsys.readouterr().out.splitlines()[-1])
    return code, envelope


def _receipt(paths: dict) -> Path:
    return installer._runtime_receipt_path(paths["runtime_home"])


def _load_receipt(paths: dict) -> dict:
    return json.loads(_receipt(paths).read_text(encoding="utf-8"))


def _write_receipt(paths: dict, receipt: dict) -> None:
    _receipt(paths).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def _plant_missing_historical(paths: dict, count: int = 3) -> list[str]:
    receipt = _load_receipt(paths)
    backup_root = paths["runtime_home"] / ".installer-backups" / "ghost"
    backup_root.mkdir(parents=True, exist_ok=True)
    planted: list[str] = []
    for index in range(count):
        destination = paths["launcher_home"] / f"historical-ghost-{index}"
        backup = backup_root / f"missing-{index}.bak"
        receipt.setdefault("backups", {})[str(destination)] = str(backup)
        planted.append(str(backup))
    _write_receipt(paths, receipt)
    return planted


@pytest.fixture
def installed(tmp_path: Path, roots, capsys):
    payload = seed_runtime_pack(tmp_path / "pack-a", version="9.9.9+a")
    result = _install(payload, capsys)
    return roots, payload, result


def test_normal_install_still_refuses_missing_historical_backups(
    tmp_path, installed, capsys
):
    paths, payload, _ = installed
    planted = _plant_missing_historical(paths)
    newer = seed_runtime_pack(tmp_path / "pack-b", version="9.9.9+b")
    with pytest.raises(FileNotFoundError):
        installer.cmd_runtime_install(_ns(newer))
    capsys.readouterr()
    for backup in planted:
        assert not Path(backup).exists()
    assert "rescue" not in _load_receipt(paths)


def test_plan_inventories_missing_history_and_live_damage_without_writes(
    installed, capsys
):
    paths, payload, _ = installed
    original = _receipt(paths).read_bytes()
    planted = _plant_missing_historical(paths)
    original = _receipt(paths).read_bytes()
    launcher = paths["launcher_home"] / "vibecrafted"
    launcher.unlink()
    code, plan = _plan(payload, capsys)
    assert code == 0
    assert plan["schema"] == installer.RUNTIME_RESCUE_PLAN_SCHEMA
    assert plan["status"] == "rescueable"
    assert plan["historical_rollback"] == "unavailable"
    assert {entry["backup"] for entry in plan["missing_backups"]} >= set(planted)
    assert any(entry["path"] == str(launcher) for entry in plan["live_damage"])
    assert plan["receipt"]["sha256"] == installer._sha256_bytes(original)
    assert _receipt(paths).read_bytes() == original
    assert not any(
        (paths["runtime_home"] / ".installer-backups" / "rescue").rglob("*")
    )


def test_rescue_preserves_user_config_and_foreign_commands(
    tmp_path, installed, capsys
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    product = paths["product_config"]
    starship = product / "starship.toml"
    user_starship = "add_newline = false\n# user chrome\n"
    starship.write_text(user_starship)
    foreign = paths["launcher_home"] / "foreign-cmd"
    foreign.write_text("#!/bin/sh\necho mine\n")
    foreign.chmod(0o755)
    external = paths["launcher_home"] / "user-rg"
    external.write_text("#!/bin/sh\n# user foundation\n")
    external.chmod(0o755)
    code, plan = _plan(payload, capsys)
    assert code == 0
    apply_code, result = _apply(payload, capsys, plan["plan_digest"])
    assert apply_code == 0
    assert result["status"] == "rescued"
    assert result["healthy_restorepoint"] is True
    assert starship.read_text() == user_starship
    assert foreign.read_text() == "#!/bin/sh\necho mine\n"
    assert external.read_text() == "#!/bin/sh\n# user foundation\n"


def test_escaping_and_tampered_receipts_refuse(tmp_path, installed, capsys):
    paths, payload, _ = installed
    receipt = _load_receipt(paths)
    receipt.setdefault("backups", {})["/etc/passwd"] = "/tmp/not-a-vibecrafted-backup"
    _write_receipt(paths, receipt)
    original = _receipt(paths).read_bytes()
    code, plan = _plan(payload, capsys)
    assert code == 2
    assert plan["status"] == "refused"
    assert plan["unsafe"]
    apply_code, result = _apply(payload, capsys, plan.get("plan_digest") or "0" * 64)
    assert apply_code == 2
    assert result["status"] in {"refused", "unusable"}
    assert _receipt(paths).read_bytes() == original

    _write_receipt(paths, {"schema": "not-a-receipt", "owned_files": {}})
    tampered = _receipt(paths).read_bytes()
    code, plan = _plan(payload, capsys)
    assert code == 2
    assert plan["status"] == "unusable"
    assert _receipt(paths).read_bytes() == tampered


def test_input_drift_refuses_apply(installed, capsys):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)
    receipt = _load_receipt(paths)
    receipt["owned_files"][str(paths["launcher_home"] / "drift-probe")] = "a" * 64
    _write_receipt(paths, receipt)
    code, result = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2
    assert result["status"] == "refused"
    assert "input drift" in result["reason"]
    assert result["healthy_restorepoint"] is False


def test_interrupted_publish_then_retry_recovers(tmp_path, installed, capsys, monkeypatch):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)
    original = installer._publish_runtime_config_transaction
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected interrupt")
        return original(*args, **kwargs)

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", boom)
    code, first = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2
    assert first["healthy_restorepoint"] is False
    assert first["pre_rescue_snapshot"].get("label") == "damaged-pre-rescue"
    archive = Path(first["archived_receipt"]["path"])
    assert archive.is_file()
    assert installer._sha256_bytes(archive.read_bytes()) == first["archived_receipt"]["sha256"]
    _, plan2 = _plan(payload, capsys)
    code, second = _apply(payload, capsys, plan2["plan_digest"])
    assert code == 0
    assert second["status"] == "rescued"
    assert second["healthy_restorepoint"] is True
    assert archive.is_file()


def test_interrupted_pending_journal_resumes_same_digest(
    installed, capsys, monkeypatch
):
    paths, payload, _ = installed
    planted = _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)
    original = installer._publish_runtime_config_transaction
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected interrupt")
        return original(*args, **kwargs)

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", boom)
    code, first = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2
    journal_path = installer._runtime_rescue_journal_path(paths["runtime_home"])
    assert journal_path.is_file()
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["plan_digest"] == plan["plan_digest"]
    # Crash-after-checkpoint: sanitized pending receipt remains; apply resumes.
    receipt = _load_receipt(paths)
    sanitized = installer._runtime_receipt_without_missing_historical(
        receipt, set(planted)
    )
    sanitized["install_pending"] = True
    sanitized["rescue_pending"] = {
        "schema": installer.RUNTIME_RESCUE_PENDING_SCHEMA,
        "plan_digest": plan["plan_digest"],
    }
    _write_receipt(paths, sanitized)
    code, second = _apply(payload, capsys, plan["plan_digest"])
    assert code == 0
    assert second["status"] == "rescued"
    assert second["healthy_restorepoint"] is True
    assert first["archived_receipt"]["path"]
    assert Path(first["archived_receipt"]["path"]).is_file()


def test_repeat_apply_converges_and_keeps_healthy_restorepoint_after_verify(
    installed, capsys
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)
    code, first = _apply(payload, capsys, plan["plan_digest"])
    assert code == 0
    snapshot = Path(first["pre_rescue_snapshot"]["path"])
    label = json.loads((snapshot / "label.json").read_text(encoding="utf-8"))
    assert label["label"] == "damaged-pre-rescue"
    assert label["healthy_restorepoint"] is False
    receipt = _load_receipt(paths)
    assert receipt["rescue"]["healthy_restorepoint"] is True
    assert receipt["rescue"]["archived_receipt_sha256"]
    assert receipt["rescue"]["missing_history"]
    _, healthy = _plan(payload, capsys)
    assert healthy["status"] == "healthy"
    code, repeat = _apply(payload, capsys, healthy["plan_digest"])
    assert code == 0
    assert repeat["status"] == "rescued"
    assert repeat["healthy_restorepoint"] is True
    assert (paths["launcher_home"] / "vibecrafted").is_file()


def test_unknown_ownership_collision_refuses(installed, capsys):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    receipt = _load_receipt(paths)
    foreign = paths["launcher_home"] / "telemetry"
    foreign.write_text("#!/bin/sh\n# not yours\n")
    receipt.get("owned_files", {}).pop(str(foreign), None)
    _write_receipt(paths, receipt)
    original = _receipt(paths).read_bytes()
    code, plan = _plan(payload, capsys)
    assert code == 2
    assert plan["status"] == "refused"
    assert any(entry["path"] == str(foreign) for entry in plan["collisions"])
    apply_code, result = _apply(payload, capsys, plan["plan_digest"])
    assert apply_code == 2
    assert result["status"] == "refused"
    assert _receipt(paths).read_bytes() == original
    assert foreign.read_text() == "#!/bin/sh\n# not yours\n"


def test_target_installer_without_rescue_is_honest_bootstrap(tmp_path, installed, capsys):
    paths, _, _ = installed
    _plant_missing_historical(paths)
    payload = seed_runtime_pack(tmp_path / "old-pack", version="9.9.9+old")
    old_installer = payload / "scripts/vetcoders_install.py"
    old_installer.write_text(
        "#!/usr/bin/env python3\n# historical installer without rescue\n",
        encoding="utf-8",
    )
    code, plan = _plan(payload, capsys)
    assert plan["target"]["installer_supports_rescue"] is False
    assert plan["bootstrap"]["required"] is True
    assert "--rescue --plan" in plan["bootstrap"]["command"]
    assert "test_runtime_pack_rescue.py" in plan["bootstrap"]["reason"]
    if code == 0:
        apply_code, result = _apply(payload, capsys, plan["plan_digest"])
        assert apply_code in {0, 2}
        if apply_code == 0:
            assert result["status"] == "rescued"
            published = (
                paths["runtime_home"]
                / "releases"
                / plan["target"]["version"]
                / "scripts/vetcoders_install.py"
            )
            assert "RUNTIME_RESCUE_PLAN_SCHEMA" not in published.read_text(
                encoding="utf-8"
            )


def test_doctor_names_the_single_rescue_path(installed):
    paths, _, _ = installed
    _plant_missing_historical(paths)
    findings = installer._doctor_runtime_receipt_findings()
    fail = [finding for finding in findings if finding.level == "fail"]
    assert fail
    assert "--rescue --plan" in fail[0].message
    assert "historical rollback" in fail[0].message


def test_wrapper_help_and_source_support_marker():
    wrapper = Path(installer.__file__).resolve().parent / "install-runtime-pack.sh"
    text = wrapper.read_text(encoding="utf-8")
    assert "--rescue --plan|--apply" in text
    assert "Do not rewrite the signed payload" in text
    assert installer._runtime_installer_supports_rescue(Path(installer.__file__))
    assert installer.RUNTIME_RESCUE_PLAN_SCHEMA in Path(installer.__file__).read_text(
        encoding="utf-8"
    )


def test_target_identity_binds_payload_bytes_not_version_hash(tmp_path, installed, capsys):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, first = _plan(payload, capsys)
    version_identity = first["target"]["version_identity_sha256"]
    payload_digest = first["target"]["payload_sha256"]
    inventory_digest = first["target"]["inventory_sha256"]
    assert first["target"]["inventory_verified"] is True
    assert first["target"]["inventory_count"] >= 1
    assert payload_digest != version_identity
    assert inventory_digest != version_identity
    launcher = payload / "bin/vibecrafted"
    launcher.write_text(launcher.read_text() + "# payload-bind probe\n")
    _, second = _plan(payload, capsys)
    assert second["target"]["version_identity_sha256"] == version_identity
    assert second["target"]["payload_sha256"] != payload_digest
    assert second["target"]["inventory_sha256"] != inventory_digest
    assert second["plan_digest"] != first["plan_digest"]


def test_verify_destination_refuses_wrapper_skill_config_and_shell_drift(
    tmp_path, installed, capsys
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)
    code, result = _apply(payload, capsys, plan["plan_digest"])
    assert code == 0
    assert result["healthy_restorepoint"] is True
    wrapper = paths["launcher_home"] / "vc-status"
    wrapper.unlink()
    wrapper.symlink_to(paths["runtime_home"] / "tools/vibecrafted-current/.venv/bin/vc-status")
    skill = Path.home() / ".agents/skills/vc-audit"
    if skill.exists() or skill.is_symlink():
        skill.unlink()
    config = paths["product_config"] / "vc-terminal" / "vc-terminal.toml"
    config.unlink()
    zshrc = Path.home() / ".zshrc"
    zshrc.write_text(
        f"{installer._shell_source_line()}\n"
        "source ~/.local/share/vibecrafted/tools/vibecrafted-current/runtime/shell/vetcoders.sh\n",
        encoding="utf-8",
    )
    verified, reason = installer._runtime_rescue_verify_destination(paths)
    assert verified is False
    assert "vc-status" in reason
    assert "vc-audit" in reason or "skill" in reason
    assert "vc-terminal.toml" in reason or "product config" in reason
    assert _receipt(paths).is_file()


def test_classify_records_symlink_target_and_directory_listing_for_binding(
    installed, capsys
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    owned = paths["launcher_home"] / "vibecrafted"
    owned.unlink()
    owned.symlink_to("/tmp/rescue-missing-venv/vibecrafted")
    occupied = paths["launcher_home"] / "vc-help"
    occupied.unlink()
    occupied.mkdir()
    (occupied / "keep.txt").write_text("foreign dir\n")
    _, plan = _plan(payload, capsys)
    drifted = next(
        entry
        for entry in plan["ownership"]
        if entry["path"] == str(owned)
    )
    assert drifted["current_type"] == "symlink"
    assert drifted["current_target"] == "/tmp/rescue-missing-venv/vibecrafted"
    assert drifted["class"] == "live_damage"
    assert drifted["disposition"] == "republish"
    foreign_dir = next(
        entry
        for entry in plan["ownership"]
        if entry["path"] == str(occupied)
    )
    assert foreign_dir["current_type"] == "directory"
    assert "keep.txt" in foreign_dir["current_listing"]
    assert foreign_dir["class"] == "unknown_ownership"
    assert plan["status"] == "refused"
    owned.unlink()
    owned.symlink_to("/tmp/rescue-missing-venv/other")
    code, result = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2
    assert result["status"] == "refused"
    assert "input drift" in result["reason"]
    assert (occupied / "keep.txt").read_text() == "foreign dir\n"


def test_snapshot_covers_new_publication_and_validates_evidence(
    tmp_path, installed, capsys, monkeypatch
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    new_skill = payload / "vibecrafted-core/vibecrafted_core/skills/vc-rescue-probe/SKILL.md"
    new_skill.parent.mkdir(parents=True, exist_ok=True)
    new_skill.write_text("# vc-rescue-probe\n")
    projected = Path.home() / ".agents/skills/vc-rescue-probe"
    assert not projected.exists()
    _, plan = _plan(payload, capsys)
    original = installer._publish_runtime_config_transaction

    def boom(*args, **kwargs):
        raise RuntimeError("injected interrupt")

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", boom)
    code, first = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2
    snapshot = Path(first["pre_rescue_snapshot"]["path"])
    label = json.loads((snapshot / "label.json").read_text(encoding="utf-8"))
    assert label["healthy_restorepoint"] is False
    assert re.fullmatch(r"[0-9a-f]{64}", label["evidence_sha256"])
    assert any(
        entry["path"] == str(projected) and entry["kind"] == "absent"
        for entry in label["paths"]
    )
    captured = next(entry for entry in label["paths"] if entry["kind"] == "file")
    tampered = snapshot / captured["rel"]
    tampered.write_bytes(tampered.read_bytes() + b"tamper")
    residuals = installer._runtime_rescue_restore_pre_rescue(paths, label, snapshot)
    assert residuals
    assert "evidence" in residuals[0]["reason"]
    archive = Path(first["archived_receipt"]["path"])
    assert installer._sha256_bytes(archive.read_bytes()) == first["archived_receipt"]["sha256"]


def test_fix_rc_is_explicit_planned_stanza_preserving_user_content(
    installed, capsys
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    zshrc = Path.home() / ".zshrc"
    user = "# keep my aliases\nalias keep-me=true\n"
    zshrc.write_text(
        user + installer._shell_source_line() + "\n",
        encoding="utf-8",
    )
    code, plan = _plan(payload, capsys)
    assert code == 0
    stanza = next(
        item
        for item in plan["shell"]["stanzas"]
        if item["path"] == str(zshrc)
    )
    assert stanza["owner"] == "doctor --fix-rc"
    assert stanza["action"] == "doctor_fix_rc"
    apply_code, result = _apply(payload, capsys, plan["plan_digest"])
    assert apply_code == 0
    assert result["healthy_restorepoint"] is True
    repaired = zshrc.read_text(encoding="utf-8")
    assert "alias keep-me=true" in repaired
    assert installer._shell_source_line() not in repaired
    assert installer._launcher_path_line() in repaired
    unclosed = Path.home() / ".zprofile"
    unclosed.write_text(
        "# user login\n# >>> vibecrafted >>>\n"
        + installer._shell_source_line()
        + "\nexport KEEP_ME=1\n",
        encoding="utf-8",
    )
    refuse_code, refused = _plan(payload, capsys)
    assert refuse_code == 2
    assert refused["status"] == "refused"
    assert unclosed.read_text(encoding="utf-8").endswith("export KEEP_ME=1\n")
