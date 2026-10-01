"""Runtime Pack publication owns retirement; live owners are never drained."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _runtime_pack_fixture import seed_runtime_pack

from scripts import vetcoders_install as installer
from tests.tui.test_runtime_pack_rescue import (
    _apply,
    _ns,
    _plan,
    _seal_runtime_pack_for_admission,
)
from tests.tui.test_runtime_pack_rescue import roots as _runtime_roots


@pytest.fixture
def roots(tmp_path, monkeypatch):
    return _runtime_roots.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def quiet_census(monkeypatch):
    """Exercise owner pin policy against deterministic observation seams."""
    real_run = installer.subprocess.run
    monkeypatch.setattr(installer, "_darwin_process_ids", list)

    def run(command, *args, **kwargs):
        if isinstance(command, list) and Path(command[0]).name == "lsof":
            return subprocess.CompletedProcess(command, 0, "", "")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(installer.subprocess, "run", run)


def publish(tmp_path, roots, capsys, number):
    payload = seed_runtime_pack(tmp_path / f"pack-{number}", version=f"9.9.{number}+r4")
    _seal_runtime_pack_for_admission(payload)
    assert installer.cmd_runtime_install(_ns(payload)) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    return Path(result["root"]), result


def receipt(roots):
    return installer._load_runtime_install_receipt(
        installer._runtime_receipt_path(roots["runtime_home"])
    )


def retire(capsys, *options):
    code = installer.main(["runtime-repair", "--retire", "--json", *options])
    return code, json.loads(capsys.readouterr().out.splitlines()[-1])


def test_provider_configuration_pins_until_dependent_repair(
    tmp_path, roots, capsys, quiet_census
):
    old, _ = publish(tmp_path, roots, capsys, 0)
    config = Path.home() / ".codex/config.toml"
    config.parent.mkdir(exist_ok=True)
    config.write_text(f'skill = "{old}/skills/vc-test"\ncredential = "never-print"\n')
    original = config.read_bytes()
    publish(tmp_path, roots, capsys, 1)
    publish(tmp_path, roots, capsys, 2)
    assert old.exists() and config.read_bytes() == original
    code, plan = retire(capsys, "--plan")
    entry = next(x for x in plan["generations"] if x["path"] == str(old))
    assert code == 0 and entry["action"] == "pinned"
    assert any("provider-config:" in reason for reason in entry["reasons"])
    assert "never-print" not in json.dumps(plan)
    config.write_text(
        'skill = "canonical-provider-skill"\ncredential = "never-print"\n'
    )
    code, _ = retire(capsys)
    assert code == 0 and not old.exists()


@pytest.mark.parametrize("shape", ["malformed", "symlink", "directory", "duplicate"])
def test_ambiguous_provider_configuration_refuses_retirement(
    tmp_path, roots, capsys, shape, quiet_census
):
    old, _ = publish(tmp_path, roots, capsys, 0)
    publish(tmp_path, roots, capsys, 1)
    config = Path.home() / ".cursor/mcp.json"
    config.parent.mkdir()
    if shape == "symlink":
        foreign = tmp_path / "foreign.json"
        foreign.write_text("{}")
        config.symlink_to(foreign)
    elif shape == "directory":
        config.mkdir()
    else:
        config.write_text('{"x":1,"x":2}' if shape == "duplicate" else "secret invalid")
    code, result = retire(capsys)
    assert code == 2 and old.exists()
    assert "provider" in json.dumps(result) and "secret invalid" not in json.dumps(
        result
    )


@pytest.mark.parametrize("shape", ["empty", "hidden", "symlink", "nonempty"])
def test_exact_canary_empty_leaves_only(tmp_path, roots, capsys, shape, quiet_census):
    payload = seed_runtime_pack(tmp_path / "historical-canary", version="9.9.0+r4")
    base_relative = Path("vibecrafted-core/vibecrafted_core/skills/pl/vc-canary")
    owned = payload / base_relative / "SKILL.md"
    owned.parent.mkdir(parents=True, exist_ok=True)
    owned.write_text("fixture canary")
    _seal_runtime_pack_for_admission(payload)
    assert installer.cmd_runtime_install(_ns(payload)) == 0
    old = Path(json.loads(capsys.readouterr().out.splitlines()[-1])["root"])
    r = receipt(roots)
    # The historical carrier did not seal empty directories, unlike new installs.
    r["retirement_generations"].pop(str(old))
    installer._checkpoint_runtime_install_receipt(roots["runtime_home"], r)
    base = old / base_relative
    for leaf in ("plugins", "scripts"):
        (base / leaf).mkdir()
    if shape == "hidden":
        (base / "plugins/.DS_Store").write_bytes(b"hidden child")
    elif shape == "nonempty":
        (base / "scripts/private").write_bytes(b"keep")
    elif shape == "symlink":
        (base / "scripts").rmdir()
        (base / "scripts").symlink_to(tmp_path, target_is_directory=True)
    publish(tmp_path, roots, capsys, 1)
    publish(tmp_path, roots, capsys, 2)
    assert old.exists() == (shape != "empty")


@pytest.fixture
def legacy_copy(tmp_path, roots, capsys, monkeypatch, quiet_census):
    publish(tmp_path, roots, capsys, 0)
    preferences = roots["product_config"] / "private-preferences.toml"
    preferences.write_bytes(b"unique human preference bytes")
    history = roots["product_config"] / "vc-terminal/.zsh_history"
    history.write_bytes(b"unique shell history bytes")
    sessions = roots["product_config"] / "vc-terminal/.zsh_sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / "founder.session").write_bytes(b"unique session bytes")
    original_delete = installer._runtime_retirement_delete

    def retain_copies(path, proof):
        if path.name.startswith("publication-"):
            raise OSError("fixture retains original legacy copy")
        return original_delete(path, proof)

    monkeypatch.setattr(installer, "_runtime_retirement_delete", retain_copies)
    publish(tmp_path, roots, capsys, 1)
    target = Path(receipt(roots)["retirement_rollback"]["publication"])
    publish(tmp_path, roots, capsys, 2)
    r = receipt(roots)
    r["retirement_copies"].pop(str(target), None)
    r["retirement_pending"].pop(str(target), None)
    installer._checkpoint_runtime_install_receipt(roots["runtime_home"], r)
    monkeypatch.setattr(installer, "_runtime_retirement_delete", original_delete)
    return target


def legacy_evidence(tmp_path, capsys, target, *, state="missing"):
    code, result = retire(capsys, "--plan", "--legacy-inventory", str(target))
    assert code == 0, result
    evidence = result["evidence"]
    evidence["history"]["state"] = state
    path = tmp_path / "legacy-evidence.json"
    path.write_text(json.dumps(evidence))
    return path, evidence


def reviewed_plan(capsys, evidence):
    code, plan = retire(capsys, "--plan", "--legacy-evidence", str(evidence))
    assert code == 0, plan
    return plan["plan_sha256"]


def test_legacy_cli_review_preserves_unique_history_bytes_once(
    tmp_path, roots, capsys, legacy_copy
):
    original_receipt = installer._runtime_receipt_path(
        roots["runtime_home"]
    ).read_bytes()
    evidence_path, evidence = legacy_evidence(
        tmp_path, capsys, legacy_copy, state="failed"
    )
    digest = reviewed_plan(capsys, evidence_path)
    assert (
        installer._runtime_receipt_path(roots["runtime_home"]).read_bytes()
        == original_receipt
    )
    assert not list(
        (roots["runtime_home"] / ".installer-backups/drift").glob("legacy-*")
    )
    for wrong in (None, "0" * 64):
        options = ["--legacy-evidence", str(evidence_path)]
        if wrong:
            options += ["--admit-plan", wrong]
        code, result = retire(capsys, *options)
        assert code == 2 and legacy_copy.exists()
        assert "admit-plan" in result["reason"]
    before_settings = installer._runtime_config_digest(roots["product_config"])
    code, result = retire(
        capsys, "--legacy-evidence", str(evidence_path), "--admit-plan", digest
    )
    assert code == 0, result
    assert not legacy_copy.exists()
    assert (
        result["history"] == evidence["history"]
        and result["history"]["state"] == "failed"
    )
    assert result["authority"] == "reviewed-legacy-inventory"
    assert "unavailable" in result["original_aggregate_preimage"]
    assert "replaced" not in result
    archived = {
        Path(item["archive"]).read_bytes() for item in result["preserved"].values()
    }
    assert {
        b"unique human preference bytes",
        b"unique shell history bytes",
        b"unique session bytes",
    } <= archived
    assert len({item["archive"] for item in result["preserved"].values()}) == len(
        archived
    )
    assert installer._runtime_config_digest(roots["product_config"]) == before_settings
    assert installer._runtime_rescue_verify_destination(roots)[0]
    records = roots["runtime_home"] / ".installer-backups/retirement"
    plan_file = next(records.glob("legacy-*.plan.json"))
    compact = next(records.glob("legacy-*.receipt.json"))
    assert "entries" not in json.loads(compact.read_text())
    plan_before = plan_file.read_bytes()
    receipts_before = compact.read_bytes()
    archives_before = sorted(
        p.name
        for p in (roots["runtime_home"] / ".installer-backups/drift").glob("legacy-*")
    )
    code, again = retire(
        capsys, "--legacy-evidence", str(evidence_path), "--admit-plan", digest
    )
    assert code == 0 and again == result
    assert (
        plan_file.read_bytes() == plan_before
        and compact.read_bytes() == receipts_before
    )
    assert (
        sorted(
            p.name
            for p in (roots["runtime_home"] / ".installer-backups/drift").glob(
                "legacy-*"
            )
        )
        == archives_before
    )


@pytest.mark.parametrize(
    "drift", ["leaf", "parent", "roles", "unknown", "topology", "outside"]
)
def test_legacy_closed_inventory_roles_and_topology_refuse(
    tmp_path, roots, capsys, legacy_copy, drift
):
    evidence_path, evidence = legacy_evidence(tmp_path, capsys, legacy_copy)
    digest = reviewed_plan(capsys, evidence_path)
    if drift == "leaf":
        name = next(
            name
            for name, record in evidence["proof"]["entries"].items()
            if record[0] == "file"
        )
        (legacy_copy / name).write_bytes(b"late unknown bytes")
    elif drift == "parent":
        evidence["parent_identity"][1] += 1
    elif drift == "roles":
        name = next(iter(evidence["roles"]))
        evidence["roles"][name]["destination"] = str(tmp_path / "foreign")
    elif drift == "unknown":
        (legacy_copy / "unbound-leaf").write_bytes(b"no invented role")
    elif drift == "topology":
        pointer = legacy_copy / "current"
        pointer.unlink(missing_ok=True)
        pointer.symlink_to(tmp_path)
    else:
        evidence["path"] = str(tmp_path)
    evidence_path.write_text(json.dumps(evidence))
    code, result = retire(
        capsys, "--legacy-evidence", str(evidence_path), "--admit-plan", digest
    )
    assert code == 2 and legacy_copy.exists(), result
    assert not list(
        (roots["runtime_home"] / ".installer-backups/drift").glob("legacy-*")
    )


@pytest.mark.parametrize("pin", ["pending", "rollback", "live", "current"])
def test_legacy_pins_refuse_even_with_reviewed_digest(
    tmp_path, roots, capsys, legacy_copy, monkeypatch, pin
):
    evidence_path, _evidence = legacy_evidence(tmp_path, capsys, legacy_copy)
    digest = reviewed_plan(capsys, evidence_path)
    r = receipt(roots)
    if pin == "pending":
        r["rescue_pending"] = {"snapshot": str(legacy_copy)}
    elif pin == "rollback":
        r["retirement_rollback"]["publication"] = str(legacy_copy)
    elif pin == "live":
        original = installer._runtime_retirement_references
        monkeypatch.setattr(
            installer,
            "_runtime_retirement_references",
            lambda *a, **k: [*original(*a, **k), ("process:owner", str(legacy_copy))],
        )
    else:
        pointer = roots["runtime_home"] / "tools/vibecrafted-current"
        pointer.unlink()
        pointer.symlink_to(legacy_copy)
    installer._checkpoint_runtime_install_receipt(roots["runtime_home"], r)
    code, result = retire(
        capsys, "--legacy-evidence", str(evidence_path), "--admit-plan", digest
    )
    assert code == 2 and legacy_copy.exists(), result


@pytest.mark.parametrize("crash", ["partial", "after-rmdir"])
def test_legacy_partial_delete_resume_preserves_history_and_plan(
    tmp_path, roots, capsys, legacy_copy, monkeypatch, crash
):
    evidence_path, evidence = legacy_evidence(
        tmp_path, capsys, legacy_copy, state="restored"
    )
    digest = reviewed_plan(capsys, evidence_path)
    real = installer._runtime_retirement_delete

    def fail(path, proof):
        if crash == "partial":
            name = next(
                name for name, record in proof["entries"].items() if record[0] == "file"
            )
            (path / name).unlink()
        else:
            real(path, proof)
        raise OSError("fixture crash")

    monkeypatch.setattr(installer, "_runtime_retirement_delete", fail)
    code, result = retire(
        capsys, "--legacy-evidence", str(evidence_path), "--admit-plan", digest
    )
    assert code == 2 and "fixture crash" in result["reason"]
    monkeypatch.setattr(installer, "_runtime_retirement_delete", real)
    code, result = retire(
        capsys, "--legacy-evidence", str(evidence_path), "--admit-plan", digest
    )
    assert code == 0 and not legacy_copy.exists(), result
    assert result["history"] == evidence["history"]
    assert {b"unique human preference bytes", b"unique shell history bytes"} <= {
        Path(item["archive"]).read_bytes() for item in result["preserved"].values()
    }


def test_old_rescue_label_is_never_rewritten(tmp_path, roots, capsys, quiet_census):
    publish(tmp_path, roots, capsys, 0)
    snapshot = roots["runtime_home"] / ".installer-backups/rescue/original/pre-rescue"
    snapshot.mkdir(parents=True)
    label = snapshot / "label.json"
    label.write_bytes(b'{"original_aggregate": "unresolved", "paths": []}')
    before = label.read_bytes()
    code, result = retire(capsys, "--plan", "--legacy-inventory", str(snapshot))
    assert code == 2 and "per-capture" in result["reason"]
    assert snapshot.exists() and label.read_bytes() == before


def test_legacy_hundreds_of_leaves_use_bounded_receipt_writes(
    tmp_path, roots, capsys, legacy_copy, monkeypatch
):
    r = receipt(roots)
    capture = next(
        Path(backup)
        for backup in r["drift_backup_history"][str(roots["product_config"])]
        if Path(backup).parent == legacy_copy
    )
    for number in range(512):
        (capture / f"historical-{number}").write_bytes(
            f"distinct-{number % 10}".encode()
        )
    evidence_path, _ = legacy_evidence(tmp_path, capsys, legacy_copy)
    digest = reviewed_plan(capsys, evidence_path)
    checkpoints = []
    writes = []
    real_checkpoint = installer._checkpoint_runtime_install_receipt
    real_json = installer._atomic_json_file

    def checkpoint(runtime, receipt):
        checkpoints.append(1)
        return real_checkpoint(runtime, receipt)

    def write(path, document):
        writes.append((path, len(json.dumps(document).encode())))
        return real_json(path, document)

    monkeypatch.setattr(installer, "_checkpoint_runtime_install_receipt", checkpoint)
    monkeypatch.setattr(installer, "_atomic_json_file", write)
    started = time.monotonic()
    code, result = retire(
        capsys, "--legacy-evidence", str(evidence_path), "--admit-plan", digest
    )
    elapsed = time.monotonic() - started
    assert code == 0 and not legacy_copy.exists(), result
    assert len(result["preserved"]) >= 512
    assert len(checkpoints) <= 2 and len(writes) <= 5
    archives = list(
        (roots["runtime_home"] / ".installer-backups/drift").glob("legacy-file-*")
    )
    for number in range(10):
        assert (
            sum(path.read_bytes() == f"distinct-{number}".encode() for path in archives)
            == 1
        )
    with capsys.disabled():
        print(
            json.dumps(
                {
                    "legacy_scale_leaves": len(result["preserved"]),
                    "receipt_checkpoints": len(checkpoints),
                    "json_writes": len(writes),
                    "json_bytes_written": sum(size for _, size in writes),
                    "seconds": elapsed,
                }
            )
        )


def test_legacy_archive_interruption_retries_without_payload_loss(
    tmp_path, roots, capsys, legacy_copy, monkeypatch
):
    evidence_path, _ = legacy_evidence(tmp_path, capsys, legacy_copy)
    digest = reviewed_plan(capsys, evidence_path)
    real = installer._backup_runtime_drift
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("fixture archive interruption")
        return real(*args, **kwargs)

    original = installer._runtime_receipt_path(roots["runtime_home"]).read_bytes()
    monkeypatch.setattr(installer, "_backup_runtime_drift", fail)
    code, result = retire(
        capsys, "--legacy-evidence", str(evidence_path), "--admit-plan", digest
    )
    assert (
        code == 2
        and legacy_copy.exists()
        and "archive interruption" in result["reason"]
    )
    assert (
        installer._runtime_receipt_path(roots["runtime_home"]).read_bytes() == original
    )
    monkeypatch.setattr(installer, "_backup_runtime_drift", real)
    code, result = retire(
        capsys, "--legacy-evidence", str(evidence_path), "--admit-plan", digest
    )
    assert code == 0 and not legacy_copy.exists(), result


def test_legacy_review_is_stale_after_current_configuration_changes(
    tmp_path, roots, capsys, legacy_copy
):
    evidence_path, _ = legacy_evidence(tmp_path, capsys, legacy_copy)
    digest = reviewed_plan(capsys, evidence_path)
    (roots["product_config"] / "later-human-preference").write_bytes(
        b"later preference"
    )
    code, result = retire(
        capsys, "--legacy-evidence", str(evidence_path), "--admit-plan", digest
    )
    assert code == 2 and legacy_copy.exists() and "admit-plan" in result["reason"]


def test_legacy_real_subprocess_cli_excludes_only_own_inventory_operand(
    tmp_path, roots, capsys, legacy_copy
):
    source = str(Path(installer.__file__).resolve())

    def cli(*options):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                source,
                "runtime-repair",
                "--runtime-home",
                str(roots["runtime_home"]),
                "--retire",
                "--json",
                *options,
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            env={
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONPATH", "PYTHONHOME"}
            },
        )
        return result.returncode, json.loads(result.stdout.splitlines()[-1])

    code, result = cli("--plan", "--legacy-inventory", str(legacy_copy))
    assert code == 0, result
    evidence = tmp_path / "subprocess-evidence.json"
    evidence.write_text(json.dumps(result["evidence"]))
    code, result = cli("--plan", "--legacy-evidence", str(evidence))
    assert code == 0, result
    digest = result["plan_sha256"]
    # An independent real owner pins the same copy through cwd. No signals are
    # sent by retirement; this test owns and releases its own fixture process.
    owner = subprocess.Popen(
        ["/bin/sleep", "120"], cwd=legacy_copy, start_new_session=True
    )
    try:
        code, result = cli("--legacy-evidence", str(evidence), "--admit-plan", digest)
        assert code == 2 and "process:" in result["reason"], result
        assert legacy_copy.exists() and owner.poll() is None
    finally:
        owner.terminate()
        owner.wait(timeout=5)
    code, result = cli("--legacy-evidence", str(evidence), "--admit-plan", digest)
    assert code == 0 and not legacy_copy.exists(), result


def payload_bytes(roots):
    return sum(
        p.stat().st_size
        for p in (roots["runtime_home"] / "releases").rglob("*")
        if p.is_file() and not p.is_symlink()
    )


def test_six_canonical_upgrades_bound_generations_and_bytes(tmp_path, roots, capsys):
    measurements = []
    for number in range(6):
        generation, result = publish(tmp_path, roots, capsys, number)
        generations = list((roots["runtime_home"] / "releases").iterdir())
        measurements.append(payload_bytes(roots))
        assert len(generations) <= 2
        assert generation in generations
        assert result["retirement"]["status"] == "settled"
        assert installer._runtime_rescue_verify_destination(roots)[0]
    assert max(measurements[2:]) <= measurements[1] * 1.02
    assert result["retirement"]["deleted_bytes"] > 0
    with capsys.disabled():
        print(
            json.dumps(
                {
                    "r4_upgrade_bytes": measurements,
                    "last_deleted_bytes": result["retirement"]["deleted_bytes"],
                    "last_deletion_receipts": result["retirement"]["deleted"],
                    "fixture_source_sha256": installer._sha256_path(
                        Path(installer.__file__)
                    ),
                }
            )
        )
    assert (
        len(list((roots["runtime_home"] / ".installer-backups").glob("publication-*")))
        <= 1
    )
    assert (
        len(
            list(
                (roots["runtime_home"] / ".installer-backups/retirement").glob("*.json")
            )
        )
        >= 4
    )


def test_detached_owner_pins_old_generation_without_signal(tmp_path, roots, capsys):
    old, _ = publish(tmp_path, roots, capsys, 0)
    owner = subprocess.Popen(["/bin/sleep", "120"], cwd=old, start_new_session=True)
    try:
        for number in range(1, 4):
            publish(tmp_path, roots, capsys, number)
        assert old.is_dir()
        assert owner.poll() is None
        plan = installer._runtime_retirement_plan(roots, receipt(roots))
        entry = next(x for x in plan["generations"] if x["path"] == str(old))
        assert any("process:" in reason for reason in entry["reasons"])
    finally:
        owner.terminate()
        owner.wait(timeout=5)
    _, result = publish(tmp_path, roots, capsys, 4)
    assert not old.exists()
    assert result["retirement"]["deleted_bytes"] > 0


def test_foreign_child_symlink_and_identity_drift_refused(tmp_path, roots, capsys):
    old, _ = publish(tmp_path, roots, capsys, 0)
    (old / "foreign").write_bytes(b"founder data")
    publish(tmp_path, roots, capsys, 1)
    _, result = publish(tmp_path, roots, capsys, 2)
    assert old.is_dir()
    assert result["retirement"]["status"] == "residual"
    (old / "foreign").unlink()
    outside = tmp_path / "foreign-dir"
    outside.mkdir()
    (outside / "important").write_bytes(b"keep")
    (old / "foreign").symlink_to(outside, target_is_directory=True)
    _, result = publish(tmp_path, roots, capsys, 3)
    assert old.is_dir() and (outside / "important").read_bytes() == b"keep"
    (old / "foreign").unlink()
    (old / "VERSION").write_text("alien\n")
    _, result = publish(tmp_path, roots, capsys, 4)
    assert old.is_dir() and result["retirement"]["status"] == "residual"


def test_pending_publication_does_not_retire(tmp_path, roots, capsys):
    old, _ = publish(tmp_path, roots, capsys, 0)
    publish(tmp_path, roots, capsys, 1)
    r = receipt(roots)
    r["install_pending"] = True
    installer._checkpoint_runtime_install_receipt(roots["runtime_home"], r)
    before = installer._runtime_receipt_path(roots["runtime_home"]).read_bytes()
    result = installer._finish_runtime_retirement(roots, r)
    assert result["status"] == "pending"
    assert old.is_dir()
    assert installer._runtime_receipt_path(roots["runtime_home"]).read_bytes() == before


def test_new_pin_at_delete_recheck_is_retained(tmp_path, roots, capsys, monkeypatch):
    old, _ = publish(tmp_path, roots, capsys, 0)
    publish(tmp_path, roots, capsys, 1)
    real = installer._runtime_retirement_references
    calls = 0

    def race(paths, r):
        nonlocal calls
        calls += 1
        references = real(paths, r)
        if calls >= 2:
            references.append(("process:new-owner", str(old)))
        return references

    monkeypatch.setattr(installer, "_runtime_retirement_references", race)
    publish(tmp_path, roots, capsys, 2)
    assert old.is_dir()


def test_partial_delete_retry_preserves_selectors(tmp_path, roots, capsys, monkeypatch):
    old, _ = publish(tmp_path, roots, capsys, 0)
    publish(tmp_path, roots, capsys, 1)
    real = installer._runtime_retirement_delete

    def fail(path, proof):
        if path == old:
            (path / "bin/vc-workflow").unlink()
            raise OSError("fixture permission denied")
        return real(path, proof)

    monkeypatch.setattr(installer, "_runtime_retirement_delete", fail)
    current, result = publish(tmp_path, roots, capsys, 2)
    assert old.exists() and result["retirement"]["status"] == "residual"
    pointer = roots["runtime_home"] / "tools/vibecrafted-current"
    assert pointer.resolve() == current
    config_before = installer._runtime_config_digest(roots["product_config"])
    monkeypatch.setattr(installer, "_runtime_retirement_delete", real)
    with installer._tools_install_lease(pointer):
        result = installer._finish_runtime_retirement(roots, receipt(roots))
    assert not old.exists() and result["status"] == "settled"
    assert pointer.resolve() == current
    assert installer._runtime_config_digest(roots["product_config"]) == config_before
    assert installer._runtime_rescue_verify_destination(roots)[0]


def test_unreceipted_release_is_not_claimed(tmp_path, roots, capsys):
    publish(tmp_path, roots, capsys, 0)
    alien = roots["runtime_home"] / "releases/unowned"
    alien.mkdir()
    (alien / "data").write_bytes(b"not ours")
    _, result = publish(tmp_path, roots, capsys, 1)
    assert alien.is_dir()
    assert any("unreceipted" in x["reason"] for x in result["retirement"]["residuals"])


def test_failed_publication_keeps_rollback(tmp_path, roots, capsys, monkeypatch):
    old, _ = publish(tmp_path, roots, capsys, 0)
    prior, _ = publish(tmp_path, roots, capsys, 1)
    payload = seed_runtime_pack(tmp_path / "failed-pack", version="9.9.3+r4")
    _seal_runtime_pack_for_admission(payload)

    def fail(*a, **k):
        raise OSError("fixture publication failure")

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", fail)
    with pytest.raises(OSError, match="publication failure"):
        installer.cmd_runtime_install(_ns(payload))
    assert old.exists() and prior.exists()
    assert (roots["runtime_home"] / "tools/vibecrafted-current").resolve() == prior


def test_copy_namespace_is_not_generation_gc(tmp_path, roots, capsys):
    publish(tmp_path, roots, capsys, 0)
    other = roots["runtime_home"] / "tools/vibecrafted-generation-other"
    other.mkdir()
    (other / "data").write_bytes(b"other owner")
    publish(tmp_path, roots, capsys, 1)
    assert other.exists()


def test_rescue_reconciles_real_ownership_poison_and_resumes_finish(
    tmp_path, roots, capsys, monkeypatch
):
    current, _ = publish(tmp_path, roots, capsys, 0)
    r = receipt(roots)
    sessions = roots["product_config"] / "vc-terminal/.zsh_sessions"
    sessions.mkdir()
    history = sessions / "founder.session"
    history.write_bytes(b"preserve shell session/history")
    r["owned_files"][str(history)] = installer._sha256_path(history)
    r["owned_files"][str(sessions / "_expiration_lockfile")] = "a" * 64
    theme = roots["product_config"] / "terminal-theme.toml"
    r["owned_dirs"].append(str(theme))
    missing = [roots["runtime_home"] / "releases" / f"missing-{i}" for i in range(3)]
    r["owned_dirs"].extend(map(str, missing))
    installer._checkpoint_runtime_install_receipt(roots["runtime_home"], r)
    assert not installer._runtime_rescue_verify_destination(roots)[0]
    payload = tmp_path / "pack-0"
    _, plan = _plan(payload, capsys)
    original = installer._runtime_rescue_verify_destination
    monkeypatch.setattr(
        installer,
        "_runtime_rescue_verify_destination",
        lambda *a, **k: (False, "fixture deferred verification"),
    )
    code, result = _apply(payload, capsys, plan["plan_digest"])
    assert code == 2 and result["status"] == "residual"
    journal_path = installer._runtime_rescue_journal_path(roots["runtime_home"])
    journal = json.loads(journal_path.read_text())
    assert journal["phase"] == installer.RUNTIME_RESCUE_PHASE_VERIFICATION
    token = journal["token"]
    snapshot = Path(journal["pre_rescue_snapshot"]["path"])
    assert snapshot.exists()
    before_config = installer._runtime_config_digest(roots["product_config"])
    monkeypatch.setattr(installer, "_runtime_rescue_verify_destination", original)

    def never_republish(*a, **k):
        raise AssertionError("verification resume must not republish/capture")

    monkeypatch.setattr(installer, "_install_runtime_pack", never_republish)
    code, result = _apply(payload, capsys, plan["plan_digest"])
    assert code == 0 and result["status"] == "rescued"
    assert json.loads(journal_path.read_text())["token"] == token
    assert not snapshot.exists()
    assert (snapshot.parent / "original-receipt.json").exists()
    assert history.read_bytes() == b"preserve shell session/history"
    assert installer._runtime_config_digest(roots["product_config"]) == before_config
    r = receipt(roots)
    assert all(".zsh_sessions" not in raw for raw in r["owned_files"])
    assert str(theme) not in r["owned_dirs"]
    assert not set(map(str, missing)) & set(r["owned_dirs"])
    assert (roots["runtime_home"] / "tools/vibecrafted-current").resolve() == current
    assert original(roots)[0]


def test_generation_empty_foreign_directory_is_refused(tmp_path, roots, capsys):
    old, _ = publish(tmp_path, roots, capsys, 0)
    (old / "private-empty-directory").mkdir()
    publish(tmp_path, roots, capsys, 1)
    _, result = publish(tmp_path, roots, capsys, 2)
    assert old.exists()
    assert any(
        "foreign generation" in entry["reason"]
        for entry in result["retirement"]["residuals"]
    )


def test_lease_plan_and_finish_cli_are_idempotent(tmp_path, roots, capsys):
    publish(tmp_path, roots, capsys, 0)
    before = installer._runtime_receipt_path(roots["runtime_home"]).read_bytes()
    assert installer.main(["runtime-repair", "--retire", "--plan", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert plan["status"] == "ready"
    assert installer._runtime_receipt_path(roots["runtime_home"]).read_bytes() == before
    assert installer.main(["runtime-repair", "--retire", "--json"]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["status"] == "settled" and result["deleted_bytes"] == 0


@pytest.mark.parametrize("module", ["foundation", "iterm2_plugin"])
def test_proven_legacy_empty_retired_module_is_not_foreign_payload(
    tmp_path, roots, capsys, module
):
    old, _ = publish(tmp_path, roots, capsys, 0)
    r = receipt(roots)
    r["retirement_generations"].pop(str(old))
    installer._checkpoint_runtime_install_receipt(roots["runtime_home"], r)
    retired = old / "vibecrafted-core/vibecrafted_core" / module
    retired.mkdir()
    proof = installer._runtime_retirement_generation(old)
    assert proof["retired_empty_modules"] == [retired.relative_to(old).as_posix()]
    (retired / "foreign-data").write_bytes(b"must remain")
    with pytest.raises(RuntimeError, match="foreign generation"):
        installer._runtime_retirement_generation(old)
    (retired / "foreign-data").unlink()
    publish(tmp_path, roots, capsys, 1)
    publish(tmp_path, roots, capsys, 2)
    assert not old.exists()


def test_inventory_refuses_leaf_symlink_swap_without_reading_foreign_bytes(
    tmp_path, monkeypatch
):
    root = tmp_path / "owned"
    root.mkdir()
    child = root / "payload"
    child.write_bytes(b"owned")
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"must not be read or removed")
    original = installer._runtime_payload_open_entry_at

    def swap(parent, name):
        child.unlink()
        child.symlink_to(foreign)
        return original(parent, name)

    monkeypatch.setattr(installer, "_runtime_payload_open_entry_at", swap)
    with pytest.raises(OSError, match="symlink"):
        installer._runtime_retirement_tree(root)
    assert foreign.read_bytes() == b"must not be read or removed"


def test_deletion_detects_same_inode_content_drift_before_unlink(tmp_path, monkeypatch):
    root = tmp_path / "owned"
    root.mkdir()
    child = root / "payload"
    child.write_bytes(b"owned")
    proof = installer._runtime_retirement_tree(root)
    original = installer._runtime_retirement_tree

    def race(path, **kwargs):
        observed = original(path, **kwargs)
        child.write_bytes(b"changed by another owner")
        return observed

    monkeypatch.setattr(installer, "_runtime_retirement_tree", race)
    with pytest.raises(RuntimeError, match="content changed"):
        installer._runtime_retirement_delete(root, proof)
    assert child.read_bytes() == b"changed by another owner"


def test_retry_closes_crash_after_rmdir_before_receipt_reconciliation(
    tmp_path, roots, capsys, monkeypatch
):
    old, _ = publish(tmp_path, roots, capsys, 0)
    publish(tmp_path, roots, capsys, 1)
    original = installer._runtime_retirement_delete

    def crash(path, proof):
        original(path, proof)
        if path == old:
            raise RuntimeError("crash after physical retirement before receipt write")

    monkeypatch.setattr(installer, "_runtime_retirement_delete", crash)
    current, result = publish(tmp_path, roots, capsys, 2)
    assert result["retirement"]["status"] == "residual"
    assert not old.exists()
    assert str(old) in receipt(roots)["retirement_pending"]
    selector = (roots["runtime_home"] / "tools/vibecrafted-current").readlink()
    settings = installer._runtime_config_digest(roots["product_config"])
    monkeypatch.setattr(installer, "_runtime_retirement_delete", original)
    assert installer.cmd_runtime_repair(_ns(current, retire=True)) == 0
    capsys.readouterr()
    after = receipt(roots)
    assert str(old) not in after["owned_dirs"]
    assert not after["retirement_pending"]
    assert installer._runtime_rescue_verify_destination(roots)[0]
    assert (roots["runtime_home"] / "tools/vibecrafted-current").readlink() == selector
    assert installer._runtime_config_digest(roots["product_config"]) == settings


def test_retirement_receipt_directory_cannot_alias_foreign_storage(
    tmp_path, monkeypatch
):
    runtime = tmp_path / "runtime"
    backups = runtime / ".installer-backups"
    backups.mkdir(parents=True)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (backups / "retirement").symlink_to(foreign, target_is_directory=True)
    monkeypatch.setattr(
        installer,
        "_runtime_retirement_plan",
        lambda *_: {
            "schema": "vibecrafted.runtime-retirement.v1",
            "status": "ready",
            "residuals": [],
            "generations": [],
            "copies": [],
        },
    )
    monkeypatch.setattr(
        installer, "_runtime_rescue_verify_destination", lambda *_: (True, "")
    )
    result = installer._finish_runtime_retirement(
        {"runtime_home": runtime}, {"version": "test"}
    )
    assert result["status"] == "residual"
    assert "symlink" in result["residuals"][0]["reason"]
    assert list(foreign.iterdir()) == []


def test_linux_argv_and_dependency_paths_pin_even_with_lsof(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    old = runtime / "releases/old"
    current = runtime / "releases/current"
    old.mkdir(parents=True)
    current.mkdir()
    (runtime / "tools").mkdir()
    (runtime / "tools/vibecrafted-current").symlink_to(current)
    process = tmp_path / "proc/123"
    process.mkdir(parents=True)
    (process / "cmdline").write_bytes(
        b"system-python\0" + str(old / "entry.py").encode() + b"\0"
    )
    (process / "environ").write_bytes(
        b"PYTHONPATH=" + str(old).encode() + b"\0TOKEN=must-not-persist\0"
    )
    (process / "maps").write_text("")
    (process / "fd").mkdir()
    for leaf in ("exe", "cwd"):
        (process / leaf).symlink_to(tmp_path)
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setattr(installer.shutil, "which", lambda _: "/fake/lsof")
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "p123\n", ""),
    )

    def process_path(*args, **kwargs):
        return tmp_path / "proc" if args == ("/proc",) else Path(*args, **kwargs)

    process_path.home = lambda: tmp_path
    monkeypatch.setattr(installer, "Path", process_path)
    references = installer._runtime_retirement_references(
        {
            "runtime_home": runtime,
            "crafted_home": tmp_path / "state",
            "launcher_home": tmp_path / "bin",
        },
        {},
    )
    assert ("process:123:argv", str(old)) in references
    assert ("process:123:environment", str(old)) in references
    assert "must-not-persist" not in json.dumps(references)
