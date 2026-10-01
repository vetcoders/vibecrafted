"""Runtime Pack publication owns retirement; live owners are never drained."""

from __future__ import annotations

import json
import subprocess
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
