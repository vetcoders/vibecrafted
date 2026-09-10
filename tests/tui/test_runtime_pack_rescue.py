"""Explicit Runtime Pack rescue: missing historical backups, not a second installer."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from _runtime_pack_fixture import seed_runtime_pack

from scripts import vetcoders_install as installer


def _seal_runtime_pack_for_admission(payload: Path) -> Path:
    """Close a seed pack with the canonical 79001 provenance owner.

    This is test construction of required release content, not a production
    bypass. Admission still goes through ``verify_provenance``.
    """
    contract = installer._runtime_pack_contract_module()
    (payload / "bin").mkdir(parents=True, exist_ok=True)
    (payload / "libexec").mkdir(parents=True, exist_ok=True)
    (payload / "scripts").mkdir(parents=True, exist_ok=True)
    frame_wrapper = payload / "bin/vc-frame"
    frame_wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    frame_wrapper.chmod(0o755)
    terminal_src = Path(installer.__file__).resolve().parent / "vc-terminal-product-entry.sh"
    (payload / "bin/vc-terminal").write_text(
        terminal_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (payload / "bin/vc-terminal").chmod(0o755)
    donor = Path("/usr/bin/true")
    native = payload / "libexec/vc-terminal"
    if donor.is_file():
        shutil.copyfile(donor, native)
    else:
        native.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 32)
    native.chmod(0o755)
    files: dict[str, str] = {}
    for name in sorted(contract.REQUIRED_FOUNDATION_EXECUTABLES):
        executable = payload / "bin" / name
        if not executable.is_file():
            executable.write_text(f"#!/bin/sh\n# {name}\n", encoding="utf-8")
            executable.chmod(0o755)
        files[name] = hashlib.sha256(executable.read_bytes()).hexdigest()
    foundations = {
        "schema": "io.vetcoders.vibecrafted.runtime-foundations.v1",
        "versions": {},
        "source_revisions": {},
        "source_archives": {},
        "licenses": {},
        "files": files,
    }
    (payload / "runtime-foundations.json").write_text(
        json.dumps(foundations, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    source_path = payload / "source-provenance.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    revision = str(source["source_revision"])
    source_path.write_text(
        json.dumps(source, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    contract.write_provenance(
        payload,
        carrier_basename="Vibecrafted_RuntimePack_rescue-fixture.tar.gz",
        version=(payload / "VERSION").read_text(encoding="utf-8").strip(),
        platform="darwin-arm64",
        architecture="arm64",
        source_revision=revision,
        terminal_revision="2" * 40,
        frame_revision="3" * 40,
    )
    return payload


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
    _seal_runtime_pack_for_admission(payload)
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
    receipt = _load_receipt(paths)
    assert not receipt.get("rescue_pending")
    assert not receipt.get("install_pending")
    assert receipt["rescue"]["verified"] is True
    assert receipt["rescue"]["healthy_restorepoint"] is True
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
    finalized = _load_receipt(paths)
    assert not finalized.get("rescue_pending")
    assert finalized["rescue"]["verified"] is True


class _FrozenUtcDateTime(datetime):
    """Pin ``datetime.now`` so interrupt+retry share a UTC second."""

    @classmethod
    def now(cls, tz=None):
        pinned = datetime(2026, 9, 10, 0, 35, 51, tzinfo=timezone.utc)
        return pinned if tz is not None else pinned.replace(tzinfo=None)


def test_allocate_evidence_token_does_not_reuse_existing_directory(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(installer, "datetime", _FrozenUtcDateTime)
    first = installer._runtime_rescue_allocate_evidence_token(tmp_path, "ab" * 32)
    second = installer._runtime_rescue_allocate_evidence_token(tmp_path, "ab" * 32)
    assert first == "20260910T003551Z-abababababab"
    assert second == "20260910T003551Z-abababababab-1"
    assert installer._runtime_rescue_evidence_root(tmp_path, first).is_dir()
    assert installer._runtime_rescue_evidence_root(tmp_path, second).is_dir()


def test_interrupted_publish_then_retry_recovers_when_evidence_token_collides(
    tmp_path, installed, capsys, monkeypatch
):
    """Warm-suite same-second retry must not reuse the interrupted snapshot."""
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)
    monkeypatch.setattr(installer, "datetime", _FrozenUtcDateTime)
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
    first_archive = Path(first["archived_receipt"]["path"])
    first_snapshot = Path(first["pre_rescue_snapshot"]["path"])
    assert first_archive.is_file()
    assert first_snapshot.is_dir()
    _, plan2 = _plan(payload, capsys)
    code, second = _apply(payload, capsys, plan2["plan_digest"])
    assert code == 0
    assert second["status"] == "rescued"
    assert second["healthy_restorepoint"] is True
    second_archive = Path(second["archived_receipt"]["path"])
    assert second_archive.is_file()
    assert second_archive != first_archive
    assert first_archive.is_file()
    assert first_snapshot.is_dir()
    rescue_root = paths["runtime_home"] / ".installer-backups" / "rescue"
    tokens = {
        path.name
        for path in rescue_root.iterdir()
        if path.is_dir() and path.name.startswith("20260910T003551Z-")
    }
    assert len(tokens) >= 2
    finalized = _load_receipt(paths)
    assert not finalized.get("rescue_pending")
    assert finalized["rescue"]["verified"] is True


def test_apply_refuses_mismatched_rescue_pending_identity(
    installed, capsys, monkeypatch
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)

    def boom(*args, **kwargs):
        raise RuntimeError("injected interrupt")

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", boom)
    code, first = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2
    journal = json.loads(
        installer._runtime_rescue_journal_path(paths["runtime_home"]).read_text(
            encoding="utf-8"
        )
    )
    receipt = _load_receipt(paths)
    receipt["rescue_pending"] = {
        "schema": installer.RUNTIME_RESCUE_PENDING_SCHEMA,
        "plan_digest": "a" * 64,
        "input_digest": journal.get("input_digest"),
        "binding": {
            **dict(journal.get("binding") or {}),
            "payload_sha256": "b" * 64,
        },
    }
    _write_receipt(paths, receipt)
    captured: dict[str, bool] = {}

    def spy(*args, **kwargs):
        captured["called"] = True
        return 0

    monkeypatch.setattr(installer, "_install_runtime_pack", spy)
    code, result = _apply(payload, capsys, "a" * 64)
    assert code == 2
    assert result["status"] == "refused"
    assert "journal" in result["reason"]
    assert "plan-digest" in result["reason"]
    assert "called" not in captured
    assert first["archived_receipt"]["path"]
    assert Path(first["archived_receipt"]["path"]).is_file()


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
    finalized = _load_receipt(paths)
    assert not finalized.get("rescue_pending")
    assert finalized["rescue"]["verified"] is True


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
    assert receipt["rescue"]["verified"] is True
    assert not receipt.get("rescue_pending")
    assert not receipt.get("install_pending")
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
    assert first["target"]["provenance_verified"] is True
    assert first["target"]["usable"] is True
    assert first["target"]["inventory_count"] >= 1
    assert payload_digest != version_identity
    assert inventory_digest != version_identity
    launcher = payload / "bin/vibecrafted"
    launcher.write_text(launcher.read_text() + "# payload-bind probe\n")
    _, second = _plan(payload, capsys)
    assert second["target"]["version_identity_sha256"] == version_identity
    assert second["target"]["payload_sha256"] != payload_digest
    assert second["target"]["inventory_sha256"] != inventory_digest
    assert second["target"]["inventory_verified"] is False
    assert second["target"]["provenance_verified"] is False
    assert second["target"]["usable"] is False
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
    """New generation publication snapshot, not a same-version payload rewrite."""
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    newer = seed_runtime_pack(tmp_path / "pack-snapshot", version="9.9.10+b")
    new_skill = newer / "vibecrafted-core/vibecrafted_core/skills/vc-rescue-probe/SKILL.md"
    new_skill.parent.mkdir(parents=True, exist_ok=True)
    new_skill.write_text("# vc-rescue-probe\n")
    _seal_runtime_pack_for_admission(newer)
    installed_identity = installer._runtime_rescue_target_identity(payload)
    requested = installer._runtime_rescue_target_identity(newer)
    assert installed_identity["version"] != requested["version"]
    assert installed_identity["payload_sha256"] != requested["payload_sha256"]
    projected = Path.home() / ".agents/skills/vc-rescue-probe"
    assert not projected.exists()
    _, plan = _plan(newer, capsys)
    assert plan["status"] == "rescueable"
    assert plan["target"]["version"] == "9.9.10+b"
    assert plan["generation"]["current"] == "9.9.9+a"
    original = installer._publish_runtime_config_transaction

    def boom(*args, **kwargs):
        raise RuntimeError("injected interrupt")

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", boom)
    code, first = _apply(newer, capsys, plan["plan_digest"])
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


def test_same_version_added_skill_refuses_without_publication(installed, capsys):
    """Same version plus new payload file is not a generation to snapshot."""
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    new_skill = payload / "vibecrafted-core/vibecrafted_core/skills/vc-rescue-probe/SKILL.md"
    new_skill.parent.mkdir(parents=True, exist_ok=True)
    new_skill.write_text("# vc-rescue-probe\n")
    _seal_runtime_pack_for_admission(payload)
    projected = Path.home() / ".agents/skills/vc-rescue-probe"
    assert not projected.exists()
    generation = (paths["runtime_home"] / "tools/vibecrafted-current").resolve()
    receipt_before = _receipt(paths).read_bytes()
    _, plan = _plan(payload, capsys)
    code, result = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2
    assert result["status"] == "refused"
    assert result["status"] != "rescued"
    assert not projected.exists()
    assert not (result.get("pre_rescue_snapshot") or {}).get("path")
    assert not (result.get("archived_receipt") or {}).get("path")
    assert generation.name == "9.9.9+a"
    assert generation.is_dir()
    assert _load_receipt(paths)["version"] == "9.9.9+a"
    assert _receipt(paths).read_bytes() == receipt_before


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
    finalized = _load_receipt(paths)
    assert not finalized.get("rescue_pending")
    assert finalized["rescue"]["verified"] is True
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


def test_plan_does_not_execute_user_startup_marker(installed, capsys):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    marker = Path.home() / "rescue-plan-executed-zshrc"
    zshrc = Path.home() / ".zshrc"
    zshrc.write_text(
        f"print -n executed > {marker}\n"
        "touch ${HOME}/rescue-plan-executed-zshrc\n",
        encoding="utf-8",
    )
    code, plan = _plan(payload, capsys)
    assert code == 0
    assert not marker.exists()
    assert plan["shell"]["interactive_shell"]["executed"] is False
    assert "returncode" not in plan["shell"]["interactive_shell"]
    digest_material = json.dumps(plan["shell"], sort_keys=True)
    assert "returncode" not in digest_material


def test_minimal_pack_is_not_verified_release_admission(tmp_path):
    payload = tmp_path / "minimal"
    (payload / "scripts").mkdir(parents=True)
    (payload / "VERSION").write_text("4.3.1+g79001c3d\n", encoding="utf-8")
    shutil.copy2(Path(installer.__file__), payload / "scripts/vetcoders_install.py")
    identity = installer._runtime_rescue_target_identity(payload)
    assert identity["usable"] is False
    assert identity["inventory_verified"] is False
    assert identity["provenance_verified"] is False
    assert identity["inventory_count"] >= 1
    assert identity["payload_sha256"]
    assert "not verified release admission" in identity["reason"]


def test_pack_admission_uses_canonical_provenance_contract(tmp_path):
    payload = seed_runtime_pack(tmp_path / "pack")
    _seal_runtime_pack_for_admission(payload)
    identity = installer._runtime_rescue_target_identity(payload)
    assert identity["usable"] is True
    assert identity["inventory_verified"] is True
    assert identity["provenance_verified"] is True

    (payload / ".DS_Store").write_bytes(b"mutable Finder metadata")
    (payload / "bin/.DS_Store").write_bytes(b"mutable Finder metadata")
    identity = installer._runtime_rescue_target_identity(payload)
    assert identity["usable"] is True
    assert identity["inventory_verified"] is True

    extra = payload / "not-in-release.txt"
    extra.write_text("omitted-from-provenance\n", encoding="utf-8")
    identity = installer._runtime_rescue_target_identity(payload)
    assert identity["usable"] is False
    assert identity["inventory_verified"] is False
    extra.unlink()

    identity = installer._runtime_rescue_target_identity(payload)
    assert identity["usable"] is True

    missing = payload / "bin/vc-start"
    missing.unlink()
    identity = installer._runtime_rescue_target_identity(payload)
    assert identity["usable"] is False
    assert identity["inventory_verified"] is False

    payload2 = seed_runtime_pack(tmp_path / "pack-malformed")
    _seal_runtime_pack_for_admission(payload2)
    (payload2 / "runtime-pack-provenance.json").write_text("{not-json", encoding="utf-8")
    identity = installer._runtime_rescue_target_identity(payload2)
    assert identity["usable"] is False
    assert identity["inventory_verified"] is False
    assert identity["provenance_verified"] is False

    payload3 = seed_runtime_pack(tmp_path / "pack-bytes")
    _seal_runtime_pack_for_admission(payload3)
    mutated = payload3 / "bin/vc-start"
    mutated.write_bytes(mutated.read_bytes() + b"#changed-bytes")
    identity = installer._runtime_rescue_target_identity(payload3)
    assert identity["usable"] is False
    assert identity["inventory_verified"] is False

    payload4 = seed_runtime_pack(tmp_path / "pack-mode")
    _seal_runtime_pack_for_admission(payload4)
    mode_target = payload4 / "bin/vc-start"
    mode_target.chmod(0o644)
    identity = installer._runtime_rescue_target_identity(payload4)
    assert identity["usable"] is False
    assert identity["inventory_verified"] is False


def test_same_type_foreign_command_replacement_refuses(installed, capsys):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    owned = paths["launcher_home"] / "vibecrafted"
    owned.write_text("#!/bin/sh\n# foreign occupant\n", encoding="utf-8")
    owned.chmod(0o755)
    original = owned.read_bytes()
    code, plan = _plan(payload, capsys)
    entry = next(item for item in plan["ownership"] if item["path"] == str(owned))
    assert entry["current_type"] == "file"
    assert entry["occupant_proof"] == "none"
    assert entry["disposition"] == "preserve"
    assert entry["class"] == "unknown_ownership"
    assert plan["status"] == "refused"
    apply_code, result = _apply(payload, capsys, plan["plan_digest"])
    assert apply_code == 2
    assert result["status"] == "refused"
    assert owned.read_bytes() == original


def test_same_type_foreign_symlink_target_refuses(tmp_path, installed, capsys):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    foreign = tmp_path / "foreign-live-target"
    foreign.write_text("not yours\n", encoding="utf-8")
    selector = paths["runtime_home"] / "tools/vibecrafted-current"
    selector.unlink()
    selector.symlink_to(foreign)
    code, plan = _plan(payload, capsys)
    entry = next(item for item in plan["ownership"] if item["path"] == str(selector))
    assert entry["current_type"] == "symlink"
    assert entry["occupant_proof"] == "none"
    assert entry["disposition"] == "preserve"
    assert entry["class"] == "unknown_ownership"
    assert plan["status"] == "refused"
    apply_code, result = _apply(payload, capsys, plan["plan_digest"])
    assert apply_code == 2
    assert result["status"] == "refused"
    assert selector.is_symlink()
    assert selector.readlink() == foreign


def _leave_interrupted_pending(paths: dict, plan_digest: str) -> None:
    receipt = _load_receipt(paths)
    receipt["install_pending"] = True
    receipt["rescue_pending"] = {
        "schema": installer.RUNTIME_RESCUE_PENDING_SCHEMA,
        "plan_digest": plan_digest,
    }
    _write_receipt(paths, receipt)


def test_interrupted_resume_changed_pack_refuses(
    tmp_path, installed, capsys, monkeypatch
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)

    def boom(*args, **kwargs):
        raise RuntimeError("injected interrupt")

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", boom)
    code, first = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2
    assert first["healthy_restorepoint"] is False
    _leave_interrupted_pending(paths, plan["plan_digest"])
    other = seed_runtime_pack(tmp_path / "other-pack", version="9.9.9+other")
    _seal_runtime_pack_for_admission(other)
    captured: dict[str, str] = {}

    def spy(args, **kwargs):
        captured["payload_root"] = str(Path(args.payload_root).resolve())
        return 0

    monkeypatch.setattr(installer, "_install_runtime_pack", spy)
    code, result = _apply(other, capsys, plan["plan_digest"])
    assert code == 2
    assert result["status"] == "refused"
    assert "input drift" in result["reason"]
    assert "pack" in result["reason"]
    assert "payload_root" not in captured


def test_interrupted_resume_changed_path_refuses(
    tmp_path, installed, capsys, monkeypatch
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)

    def boom(*args, **kwargs):
        raise RuntimeError("injected interrupt")

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", boom)
    assert _apply(payload, capsys, plan["plan_digest"])[0] == 2
    _leave_interrupted_pending(paths, plan["plan_digest"])
    relocated = tmp_path / "relocated-pack"
    shutil.copytree(payload, relocated)
    captured: dict[str, str] = {}

    def spy(args, **kwargs):
        captured["payload_root"] = str(Path(args.payload_root).resolve())
        return 0

    monkeypatch.setattr(installer, "_install_runtime_pack", spy)
    code, result = _apply(relocated, capsys, plan["plan_digest"])
    assert code == 2
    assert result["status"] == "refused"
    assert "input drift" in result["reason"]
    assert "path" in result["reason"]
    assert "payload_root" not in captured


def test_interrupted_resume_changed_mode_refuses(
    installed, capsys, monkeypatch
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)

    def boom(*args, **kwargs):
        raise RuntimeError("injected interrupt")

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", boom)
    assert _apply(payload, capsys, plan["plan_digest"])[0] == 2
    _leave_interrupted_pending(paths, plan["plan_digest"])
    captured: dict[str, bool] = {}

    def spy(args, **kwargs):
        captured["called"] = True
        return 0

    monkeypatch.setattr(installer, "_install_runtime_pack", spy)
    code, result = _apply(
        payload, capsys, plan["plan_digest"], allow_older_runtime=True
    )
    assert code == 2
    assert result["status"] == "refused"
    assert "input drift" in result["reason"]
    assert "mode" in result["reason"]
    assert "called" not in captured


def test_interrupted_resume_valid_same_binding_resumes(
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
    journal = json.loads(
        installer._runtime_rescue_journal_path(paths["runtime_home"]).read_text(
            encoding="utf-8"
        )
    )
    assert journal["binding"]["payload_root"] == str(payload.resolve())
    assert journal["binding"]["payload_sha256"]
    assert journal["binding"]["inventory_sha256"]
    _leave_interrupted_pending(paths, plan["plan_digest"])
    sanitized = installer._runtime_receipt_without_missing_historical(
        _load_receipt(paths), set(planted)
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
    finalized = _load_receipt(paths)
    assert not finalized.get("rescue_pending")
    assert finalized["rescue"]["verified"] is True


def test_rollback_both_missing_reports_residual(tmp_path, roots):
    journal = {
        "pre_rescue_snapshot": {
            "path": str(tmp_path / "missing-snap"),
            "evidence_sha256": "b" * 64,
        },
        "archived_receipt": {
            "path": str(tmp_path / "missing-receipt.json"),
            "sha256": "a" * 64,
        },
        "evidence_sha256": "b" * 64,
    }
    receipt_path = installer._runtime_receipt_path(roots["runtime_home"])
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("{}\n", encoding="utf-8")
    roots["launcher_home"].mkdir(parents=True, exist_ok=True)
    marker = roots["launcher_home"] / "keep-me"
    marker.write_text("stay\n", encoding="utf-8")
    residuals = installer._runtime_rescue_rollback_captured_state(
        roots, journal, receipt_path, None
    )
    assert residuals
    assert any("snapshot" in item["reason"] for item in residuals)
    assert any(
        "receipt" in item["reason"] or "archived" in item["reason"]
        for item in residuals
    )
    assert marker.read_text(encoding="utf-8") == "stay\n"
    assert receipt_path.read_text(encoding="utf-8") == "{}\n"


def test_rollback_snapshot_missing_reports_residual(tmp_path, roots):
    archive = tmp_path / "original-receipt.json"
    original = b'{"schema":"probe"}\n'
    archive.write_bytes(original)
    journal = {
        "pre_rescue_snapshot": {"path": str(tmp_path / "missing-snap")},
        "archived_receipt": {
            "path": str(archive),
            "sha256": installer._sha256_bytes(original),
        },
        "evidence_sha256": "b" * 64,
    }
    receipt_path = installer._runtime_receipt_path(roots["runtime_home"])
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("live\n", encoding="utf-8")
    marker = tmp_path / "destination.txt"
    marker.write_text("live dest\n", encoding="utf-8")
    residuals = installer._runtime_rescue_rollback_captured_state(
        roots, journal, receipt_path, None
    )
    assert any("snapshot" in item["reason"] for item in residuals)
    assert marker.read_text(encoding="utf-8") == "live dest\n"


def test_rollback_receipt_missing_reports_residual(tmp_path, roots):
    snapshot = tmp_path / "pre-rescue"
    snapshot.mkdir()
    label = {
        "schema": installer.RUNTIME_RESCUE_EVIDENCE_SCHEMA,
        "label": "damaged-pre-rescue",
        "healthy_restorepoint": False,
        "paths": [],
    }
    label["evidence_sha256"] = installer._runtime_rescue_snapshot_evidence_digest(
        label, snapshot
    )
    (snapshot / "label.json").write_text(json.dumps(label), encoding="utf-8")
    journal = {
        "pre_rescue_snapshot": {
            "path": str(snapshot),
            "evidence_sha256": label["evidence_sha256"],
        },
        "archived_receipt": {
            "path": str(tmp_path / "missing-receipt.json"),
            "sha256": "a" * 64,
        },
        "evidence_sha256": label["evidence_sha256"],
    }
    receipt_path = installer._runtime_receipt_path(roots["runtime_home"])
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("live\n", encoding="utf-8")
    residuals = installer._runtime_rescue_rollback_captured_state(
        roots, journal, receipt_path, None
    )
    assert any("receipt" in item["reason"] or "archived" in item["reason"] for item in residuals)
    assert receipt_path.read_text(encoding="utf-8") == "live\n"


def test_rollback_corrupted_label_reports_residual(tmp_path, roots):
    snapshot = tmp_path / "pre-rescue"
    snapshot.mkdir()
    (snapshot / "label.json").write_text("{not-json", encoding="utf-8")
    archive = tmp_path / "original-receipt.json"
    archive.write_bytes(b"{}\n")
    journal = {
        "pre_rescue_snapshot": {
            "path": str(snapshot),
            "evidence_sha256": "b" * 64,
        },
        "archived_receipt": {
            "path": str(archive),
            "sha256": installer._sha256_bytes(b"{}\n"),
        },
        "evidence_sha256": "b" * 64,
    }
    receipt_path = installer._runtime_receipt_path(roots["runtime_home"])
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("live\n", encoding="utf-8")
    marker = roots["launcher_home"] / "keep-me"
    roots["launcher_home"].mkdir(parents=True, exist_ok=True)
    marker.write_text("stay\n", encoding="utf-8")
    residuals = installer._runtime_rescue_rollback_captured_state(
        roots, journal, receipt_path, None
    )
    assert any("malformed" in item["reason"] or "mismatched" in item["reason"] for item in residuals)
    assert marker.read_text(encoding="utf-8") == "stay\n"


def test_rollback_corrupt_copied_bytes_reports_residual(installed, tmp_path):
    paths, payload, _ = installed
    receipt = _load_receipt(paths)
    original = _receipt(paths).read_bytes()
    evidence_root = tmp_path / "rescue-ev"
    evidence_root.mkdir()
    archive = evidence_root / "original-receipt.json"
    archive.write_bytes(original)
    label = installer._runtime_rescue_snapshot_pre_rescue(
        paths, receipt, evidence_root, payload
    )
    snapshot = evidence_root / "pre-rescue"
    captured = next(entry for entry in label["paths"] if entry["kind"] == "file")
    tampered = snapshot / captured["rel"]
    tampered.write_bytes(tampered.read_bytes() + b"tamper")
    journal = {
        "pre_rescue_snapshot": {
            "path": str(snapshot),
            "evidence_sha256": label["evidence_sha256"],
        },
        "archived_receipt": {
            "path": str(archive),
            "sha256": installer._sha256_bytes(original),
        },
        "evidence_sha256": label["evidence_sha256"],
    }
    live = paths["launcher_home"] / "vibecrafted"
    before = live.read_bytes()
    residuals = installer._runtime_rescue_rollback_captured_state(
        paths, journal, _receipt(paths), None
    )
    assert residuals
    assert any("mismatched" in item["reason"] or "evidence" in item["reason"] for item in residuals)
    assert live.read_bytes() == before
    assert archive.read_bytes() == original


def test_rollback_valid_restores_and_keeps_evidence(installed, tmp_path):
    paths, payload, _ = installed
    receipt = _load_receipt(paths)
    original = _receipt(paths).read_bytes()
    evidence_root = tmp_path / "rescue-ev"
    evidence_root.mkdir()
    archive = evidence_root / "original-receipt.json"
    archive.write_bytes(original)
    label = installer._runtime_rescue_snapshot_pre_rescue(
        paths, receipt, evidence_root, payload
    )
    snapshot = evidence_root / "pre-rescue"
    launcher = paths["launcher_home"] / "vibecrafted"
    before = launcher.read_bytes()
    launcher.write_text("mutated\n", encoding="utf-8")
    journal = {
        "pre_rescue_snapshot": {
            "path": str(snapshot),
            "label": "damaged-pre-rescue",
            "evidence_sha256": label["evidence_sha256"],
        },
        "archived_receipt": {
            "path": str(archive),
            "sha256": installer._sha256_bytes(original),
        },
        "evidence_sha256": label["evidence_sha256"],
    }
    residuals = installer._runtime_rescue_rollback_captured_state(
        paths, journal, _receipt(paths), None
    )
    assert residuals == []
    assert _receipt(paths).read_bytes() == original
    assert archive.is_file()
    assert (snapshot / "label.json").is_file()
    assert launcher.read_bytes() == before


def test_snapshot_refuses_uncapturable_physical_path(
    installed, tmp_path, monkeypatch
):
    paths, payload, _ = installed
    receipt = _load_receipt(paths)
    original = installer._assert_runtime_physical_path
    probe = paths["launcher_home"] / "vibecrafted"

    def boom(path, *, leaf_symlink=False):
        if path == probe:
            raise RuntimeError("aliased for probe")
        return original(path, leaf_symlink=leaf_symlink)

    monkeypatch.setattr(installer, "_assert_runtime_physical_path", boom)
    with pytest.raises(RuntimeError, match="cannot capture"):
        installer._runtime_rescue_snapshot_pre_rescue(
            paths, receipt, tmp_path / "ev", payload
        )


def test_snapshot_covers_rc_backup_paths(installed, capsys, monkeypatch):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    zshrc = Path.home() / ".zshrc"
    zshrc.write_text("# user rc\n", encoding="utf-8")
    bak = installer._runtime_rescue_rc_backup_path(zshrc)
    _, plan = _plan(payload, capsys)

    def boom(*args, **kwargs):
        raise RuntimeError("injected interrupt")

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", boom)
    code, first = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2
    snapshot = Path(first["pre_rescue_snapshot"]["path"])
    label = json.loads((snapshot / "label.json").read_text(encoding="utf-8"))
    assert any(entry["path"] == str(zshrc) for entry in label["paths"])
    assert any(entry["path"] == str(bak) for entry in label["paths"])


def test_snapshot_parent_child_overlap_restore(installed, tmp_path):
    paths, payload, _ = installed
    receipt = _load_receipt(paths)
    parent = paths["product_config"] / "vc-frame"
    child = parent / "config.kdl"
    if not child.is_file():
        child = next(path for path in parent.rglob("*") if path.is_file())
    original_child = child.read_bytes()
    label = installer._runtime_rescue_snapshot_pre_rescue(
        paths, receipt, tmp_path / "ev", payload
    )
    snapshot = tmp_path / "ev" / "pre-rescue"
    (parent / "injected.txt").write_text("new\n", encoding="utf-8")
    child.write_text("changed\n", encoding="utf-8")
    residuals = installer._runtime_rescue_restore_pre_rescue(paths, label, snapshot)
    assert residuals == []
    assert not (parent / "injected.txt").exists()
    assert child.read_bytes() == original_child


def test_preference_conflict_keeps_pending_recoverable(
    installed, capsys, monkeypatch
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)

    def boom(*args, **kwargs):
        raise installer.PreferenceConflict(
            "preference conflict",
            {"message": "preference conflict", "files": []},
        )

    monkeypatch.setattr(installer, "_prepare_runtime_preferences", boom)
    with pytest.raises(installer.PreferenceConflict):
        installer.cmd_runtime_install(
            _ns(payload, rescue=True, apply=True, plan_digest=plan["plan_digest"])
        )
    journal_path = installer._runtime_rescue_journal_path(paths["runtime_home"])
    assert journal_path.is_file()
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["binding"]["payload_root"] == str(payload.resolve())
    receipt = _load_receipt(paths)
    assert receipt.get("rescue_pending")
    assert receipt.get("install_pending") is True
    assert receipt["rescue_pending"].get("binding", {}).get("payload_sha256")


def _owned_pending_from_journal(paths: dict) -> tuple[dict, dict]:
    journal = json.loads(
        installer._runtime_rescue_journal_path(paths["runtime_home"]).read_text(
            encoding="utf-8"
        )
    )
    return installer._runtime_rescue_pending_record_from_journal(journal), journal


def test_verify_destination_refuses_unrelated_pending_identity(
    installed, capsys
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)
    code, result = _apply(payload, capsys, plan["plan_digest"])
    assert code == 0
    expected, _ = _owned_pending_from_journal(paths)
    receipt = _load_receipt(paths)
    assert not receipt.get("rescue_pending")

    receipt["rescue_pending"] = {
        "schema": installer.RUNTIME_RESCUE_PENDING_SCHEMA,
        "plan_digest": "a" * 64,
        "input_digest": expected.get("input_digest"),
        "binding": dict(expected.get("binding") or {}),
    }
    _write_receipt(paths, receipt)
    verified, reason = installer._runtime_rescue_verify_destination(
        paths, expected_pending=expected
    )
    assert verified is False
    assert reason == "destination publication is still pending"

    receipt = _load_receipt(paths)
    receipt["rescue_pending"] = dict(expected)
    receipt["install_pending"] = True
    _write_receipt(paths, receipt)
    verified, reason = installer._runtime_rescue_verify_destination(
        paths, expected_pending=expected
    )
    assert verified is False
    assert reason == "destination publication is still pending"

    receipt = _load_receipt(paths)
    receipt.pop("install_pending", None)
    receipt["rescue_pending"] = dict(expected)
    receipt["config_pending"] = {"staged": True}
    _write_receipt(paths, receipt)
    verified, reason = installer._runtime_rescue_verify_destination(
        paths, expected_pending=expected
    )
    assert verified is False
    assert reason == "destination publication is still pending"

    receipt = _load_receipt(paths)
    receipt.pop("config_pending", None)
    receipt["uninstall_pending"] = True
    receipt["rescue_pending"] = dict(expected)
    _write_receipt(paths, receipt)
    verified, reason = installer._runtime_rescue_verify_destination(
        paths, expected_pending=expected
    )
    assert verified is False
    assert reason == "destination publication is still pending"

    receipt = _load_receipt(paths)
    receipt.pop("uninstall_pending", None)
    mismatched = dict(expected)
    mismatched["binding"] = dict(expected.get("binding") or {})
    mismatched["binding"]["payload_sha256"] = "b" * 64
    receipt["rescue_pending"] = mismatched
    _write_receipt(paths, receipt)
    verified, reason = installer._runtime_rescue_verify_destination(
        paths, expected_pending=expected
    )
    assert verified is False
    assert reason == "destination publication is still pending"

    receipt = _load_receipt(paths)
    receipt["rescue_pending"] = dict(expected)
    _write_receipt(paths, receipt)
    verified, reason = installer._runtime_rescue_verify_destination(paths)
    assert verified is False
    assert reason == "destination publication is still pending"

    receipt = _load_receipt(paths)
    receipt["rescue_pending"] = dict(expected)
    _write_receipt(paths, receipt)
    verified, reason = installer._runtime_rescue_verify_destination(
        paths, expected_pending=expected
    )
    assert verified is True
    assert reason == ""


def test_verification_failed_retry_keeps_pending_then_finalizes(
    installed, capsys, monkeypatch
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)
    original = installer._runtime_rescue_interactive_shell_check
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "ok": "false",
                "reason": "injected destination shell residual",
                "returncode": "1",
                "stderr": "",
            }
        return original(*args, **kwargs)

    monkeypatch.setattr(installer, "_runtime_rescue_interactive_shell_check", boom)
    code, first = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2
    assert first["status"] == "residual"
    assert first["healthy_restorepoint"] is False
    assert "injected destination shell residual" in first["reason"]
    receipt = _load_receipt(paths)
    assert receipt.get("rescue_pending")
    assert receipt["rescue_pending"]["plan_digest"] == plan["plan_digest"]
    assert receipt["rescue_pending"]["binding"]["payload_sha256"]
    assert receipt["rescue_pending"]["healthy_restorepoint"] is False
    assert not receipt.get("install_pending")
    journal = json.loads(
        installer._runtime_rescue_journal_path(paths["runtime_home"]).read_text(
            encoding="utf-8"
        )
    )
    assert journal["plan_digest"] == plan["plan_digest"]
    code, second = _apply(payload, capsys, plan["plan_digest"])
    assert code == 0
    assert second["status"] == "rescued"
    assert second["healthy_restorepoint"] is True
    receipt = _load_receipt(paths)
    assert not receipt.get("rescue_pending")
    assert not receipt.get("install_pending")
    assert receipt["rescue"]["verified"] is True
    assert receipt["rescue"]["healthy_restorepoint"] is True
    assert receipt["rescue"]["schema"] == "vibecrafted.runtime-rescue.v1"


def test_owned_pending_identity_requires_validated_binding():
    expected = {
        "schema": installer.RUNTIME_RESCUE_PENDING_SCHEMA,
        "plan_digest": "c" * 64,
        "input_digest": "d" * 64,
        "binding": {
            "payload_root": "/tmp/pack",
            "payload_sha256": "e" * 64,
            "inventory_sha256": "f" * 64,
            "version_identity_sha256": "1" * 64,
            "mode": "apply",
            "allow_older_runtime": False,
        },
    }
    stub = {
        "schema": installer.RUNTIME_RESCUE_PENDING_SCHEMA,
        "plan_digest": "c" * 64,
    }
    assert installer._runtime_rescue_pending_matches_owned_identity(stub, expected) is False
    assert (
        installer._runtime_rescue_pending_matches_owned_identity(expected, expected)
        is True
    )
    receipt = {"rescue_pending": expected, "install_pending": True}
    assert (
        installer._runtime_rescue_pending_publication_reason(
            receipt, expected_pending=expected
        )
        == "destination publication is still pending"
    )
    receipt = {"rescue_pending": expected}
    assert (
        installer._runtime_rescue_pending_publication_reason(
            receipt, expected_pending=expected
        )
        == ""
    )


def test_prepublication_failure_preserves_post_rescue_user_file(
    installed, capsys, monkeypatch
):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    code, plan = _plan(payload, capsys)
    assert code == 0
    code, result = _apply(payload, capsys, plan["plan_digest"])
    assert code == 0 and result["healthy_restorepoint"]
    snapshot = Path(result["pre_rescue_snapshot"]["path"])
    archive = Path(result["archived_receipt"]["path"])
    journal_path = installer._runtime_rescue_journal_path(paths["runtime_home"])
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal.get("phase") == installer.RUNTIME_RESCUE_PHASE_COMPLETED
    user_file = paths["product_config"] / "after-rescue-user-note.txt"
    user_bytes = b"Founder settings created after completed rescue\n"
    user_file.write_text(user_bytes.decode("utf-8"))
    receipt_before = _receipt(paths).read_bytes()

    def fail_before_publication(**kwargs):
        raise RuntimeError("W2 injected planning failure before new capture/publication")

    monkeypatch.setattr(installer, "_build_runtime_rescue_plan", fail_before_publication)
    code, second = _apply(payload, capsys, "a" * 64)
    assert code == 2
    assert user_file.is_file(), (
        "PRE-PUBLICATION FAILURE RESTORED OLD SNAPSHOT AND DELETED NEW USER FILE"
    )
    assert user_file.read_bytes() == user_bytes
    assert _receipt(paths).read_bytes() == receipt_before
    assert snapshot.is_dir()
    assert archive.is_file()
    historical = json.loads(journal_path.read_text(encoding="utf-8"))
    assert historical.get("phase") == installer.RUNTIME_RESCUE_PHASE_COMPLETED
    assert historical.get("token") == journal.get("token")


def test_healthy_old_generation_does_not_satisfy_new_target(
    installed, tmp_path, capsys
):
    paths, payload, _ = installed
    newer = seed_runtime_pack(tmp_path / "pack-b", version="9.9.10+b")
    _seal_runtime_pack_for_admission(newer)
    original = installer._runtime_rescue_target_identity(payload)
    target = installer._runtime_rescue_target_identity(newer)
    assert original["payload_sha256"] != target["payload_sha256"]
    code, plan = _plan(newer, capsys)
    assert code == 0
    assert plan["status"] == "rescueable"
    assert plan["target"]["version"] == "9.9.10+b"
    assert plan["generation"]["current"] == "9.9.9+a"
    code, result = _apply(newer, capsys, plan["plan_digest"])
    assert code == 0
    assert result["status"] == "rescued"
    assert _load_receipt(paths)["version"] == "9.9.10+b", (
        "RESCUED SUCCESS CLAIM RETAINED OLD GENERATION INSTEAD OF REQUESTED PACK"
    )
    _, healthy = _plan(newer, capsys)
    assert healthy["status"] == "healthy"
    code, repeat = _apply(newer, capsys, healthy["plan_digest"])
    assert code == 0
    assert repeat["status"] == "rescued"
    assert _load_receipt(paths)["version"] == "9.9.10+b"
    assert (paths["runtime_home"] / "tools/vibecrafted-current").resolve().name == (
        "9.9.10+b"
    )


def test_same_version_different_payload_is_not_false_success(
    installed, tmp_path, capsys
):
    """Adopted W2 falsifier: same version/provenance is not requested content."""
    paths, payload, _ = installed
    other = seed_runtime_pack(tmp_path / "other-pack", version="9.9.9+a")
    marker = other / "requested-content-marker.txt"
    marker.write_text("unique requested bytes\n")
    _seal_runtime_pack_for_admission(other)
    original = installer._runtime_rescue_target_identity(payload)
    target = installer._runtime_rescue_target_identity(other)
    assert target["usable"]
    assert original["version_identity_sha256"] == target["version_identity_sha256"]
    assert original["payload_sha256"] != target["payload_sha256"]
    assert original["inventory_sha256"] != target["inventory_sha256"]
    code, plan = _plan(other, capsys)
    assert code == 0
    assert plan["status"] == "rescueable"
    generation = (paths["runtime_home"] / "tools/vibecrafted-current").resolve()
    receipt_before = _receipt(paths).read_bytes()
    code, result = _apply(other, capsys, plan["plan_digest"])
    if code == 0:
        assert (generation / marker.name).is_file(), (
            "rescued success retained a different payload with same version/provenance"
        )
        assert (generation / marker.name).read_bytes() == marker.read_bytes()
    else:
        assert result["status"] != "rescued"
        assert result["status"] == "refused"
        assert not (generation / marker.name).is_file()
        assert generation.name == "9.9.9+a"
        assert _load_receipt(paths)["version"] == "9.9.9+a"
        assert _receipt(paths).read_bytes() == receipt_before


def test_unchanged_requested_pack_repeat_is_healthy_noop(installed, capsys):
    paths, payload, _ = installed
    code, plan = _plan(payload, capsys)
    assert code == 0
    assert plan["status"] == "healthy"
    generation = (paths["runtime_home"] / "tools/vibecrafted-current").resolve()
    receipt_before = _receipt(paths).read_bytes()
    code, result = _apply(payload, capsys, plan["plan_digest"])
    assert code == 0
    assert result["status"] == "rescued"
    assert _receipt(paths).read_bytes() == receipt_before
    assert generation.name == "9.9.9+a"
    assert generation.is_dir()


def test_product_owned_shell_check_does_not_execute_user_startup(
    installed, tmp_path
):
    marker = tmp_path / "outside-smoke-home-touch"
    zshrc = Path.home() / ".zshrc"
    zshrc.write_text(f"touch {marker}\n", encoding="utf-8")
    record = installer._runtime_rescue_interactive_shell_check()
    assert record["ok"] == "true"
    assert record["user_startup_executed"] == "false"
    assert record["process_isolation"] == "false"
    assert record["surface"] == "product-owned-startup"
    assert not marker.exists()
    paths, _payload, _ = installed
    verified, reason = installer._runtime_rescue_verify_destination(paths)
    assert verified is True, reason
    assert not marker.exists()


def test_completed_journal_is_not_an_owned_resume(installed, capsys):
    paths, payload, _ = installed
    _plant_missing_historical(paths)
    _, plan = _plan(payload, capsys)
    code, result = _apply(payload, capsys, plan["plan_digest"])
    assert code == 0
    journal_path = installer._runtime_rescue_journal_path(paths["runtime_home"])
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal.get("phase") == installer.RUNTIME_RESCUE_PHASE_COMPLETED
    assert installer._runtime_rescue_resume_phase_error(journal)
    receipt = _load_receipt(paths)
    receipt["rescue_pending"] = installer._runtime_rescue_pending_record_from_journal(
        journal
    )
    _write_receipt(paths, receipt)
    code, refused = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2
    assert refused["status"] == "refused"
    assert "historical" in refused["reason"] or "completed" in refused["reason"]
