from __future__ import annotations

import json
import shutil
from argparse import Namespace
from pathlib import Path

import pytest
from _runtime_pack_fixture import fixed_terminal_fixture, seed_runtime_pack

from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def installed_local_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    home, state, runtime = tmp_path / "home", tmp_path / "state", tmp_path / "runtime"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(state))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setattr(installer, "_runtime_loaded_service_home", lambda: None)
    monkeypatch.setattr(installer, "_runtime_service_snapshot", lambda _home: None)
    monkeypatch.setattr(installer, "_darwin_process_ids", tuple)
    fixed_terminal_fixture(monkeypatch, tmp_path)

    def add_owner(payload: Path) -> None:
        package = payload / "vibecrafted-core/vibecrafted_core"
        for filename in ("local_skills.py", "portable_lock.py"):
            shutil.copy2(
                REPO_ROOT / "vibecrafted-core/vibecrafted_core" / filename,
                package / filename,
            )

    payload = seed_runtime_pack(tmp_path / "payload", before_source_seal=add_owner)
    app = tmp_path / "Vibecrafted.app"
    terminal, frame = (
        app / "Contents/Helpers/vc-terminal",
        app / "Contents/Helpers/vc-frame",
    )
    terminal.parent.mkdir(parents=True)
    for path in (terminal, frame):
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    args = Namespace(
        payload_root=str(payload),
        app_root=str(app),
        terminal_host=str(terminal),
        frame_helper=str(frame),
    )
    assert installer.cmd_runtime_install(args) == 0
    capsys.readouterr()
    current = runtime / "tools/vibecrafted-current"
    monkeypatch.setattr(
        installer, "__file__", str(current.resolve() / "scripts/vetcoders_install.py")
    )
    owner = installer._load_local_skill_owner()
    before = owner.read_skill("vc-audit")
    (home / ".cursor/skills").mkdir(parents=True)
    saved = installer.publish_local_skill("vc-audit", "# local A\n", before["revision"])
    capsys.readouterr()
    return {
        "home": home,
        "runtime": runtime,
        "state": state,
        "args": args,
        "owner": owner,
        "saved": saved,
        "receipt": runtime / installer.RUNTIME_INSTALL_RECEIPT,
        "selector": state / "local-skills/vc-audit/selected.json",
        "views": [
            installer.runtime_skills_dir(rt) / "vc-audit"
            for rt in (*installer.STANDARD_VIEW_RUNTIMES, "cursor")
        ],
    }


@pytest.mark.parametrize("tamper", ["missing", "other_valid_version"])
def test_upgrade_refuses_unreceipted_selection_and_preserves_views(
    installed_local_skill, tamper
):
    env = installed_local_skill
    owner = env["owner"]
    receipt_before = env["receipt"].read_bytes()
    targets = [view.readlink() for view in env["views"]]
    if tamper == "missing":
        env["selector"].unlink()
        selector_before = None
    else:
        alternate = owner.prepare_skill(
            "vc-audit", "# local B\n", env["saved"]["revision"]
        )
        env["selector"].write_bytes(owner.selected_bytes(alternate))
        selector_before = env["selector"].read_bytes()
    with pytest.raises(RuntimeError, match="local skill selection"):
        installer.cmd_runtime_install(env["args"])
    assert env["receipt"].read_bytes() == receipt_before
    assert [view.readlink() for view in env["views"]] == targets
    assert all(
        (view / "SKILL.md").read_text() == "# local A\n" for view in env["views"]
    )
    assert (
        env["selector"].read_bytes() == selector_before
        if selector_before
        else not env["selector"].exists()
    )


def test_upgrade_migrates_legacy_selection_only_with_a_live_owned_view(
    installed_local_skill, capsys
):
    env = installed_local_skill
    old = json.loads(env["receipt"].read_text())
    old.pop("local_skill_selections")
    installer._checkpoint_runtime_install_receipt(env["runtime"], old)
    assert installer.cmd_runtime_install(env["args"]) == 0
    capsys.readouterr()
    receipt = json.loads(env["receipt"].read_text())
    assert (
        receipt["local_skill_selections"]["vc-audit"]["content_sha256"]
        == env["saved"]["revision"]
    )
    assert all(
        (view / "SKILL.md").read_text() == "# local A\n" for view in env["views"]
    )


def test_upgrade_refuses_legacy_selection_without_live_proof(installed_local_skill):
    env = installed_local_skill
    old = json.loads(env["receipt"].read_text())
    old.pop("local_skill_selections")
    installer._checkpoint_runtime_install_receipt(env["runtime"], old)
    for view in env["views"]:
        view.unlink()
        view.write_text("foreign\n")
    before = env["receipt"].read_bytes()
    with pytest.raises(RuntimeError, match="local skill selection"):
        installer.cmd_runtime_install(env["args"])
    assert env["receipt"].read_bytes() == before
    assert all(view.read_text() == "foreign\n" for view in env["views"])


def test_legacy_migration_refuses_disagreeing_live_receipted_versions(
    installed_local_skill,
):
    env = installed_local_skill
    codex = installer.runtime_skills_dir("codex") / "vc-audit"
    old_target = codex.readlink()
    codex.unlink()
    codex.write_text("foreign\n")
    saved = installer.publish_local_skill(
        "vc-audit", "# local B\n", env["saved"]["revision"]
    )
    assert saved["activation"] == "partial"
    codex.unlink()
    codex.symlink_to(old_target)
    old = json.loads(env["receipt"].read_text())
    old.pop("local_skill_selections")
    installer._checkpoint_runtime_install_receipt(env["runtime"], old)
    before = env["receipt"].read_bytes()
    targets = [view.readlink() for view in env["views"]]
    with pytest.raises(RuntimeError, match="local skill selection"):
        installer.cmd_runtime_install(env["args"])
    assert env["receipt"].read_bytes() == before
    assert [view.readlink() for view in env["views"]] == targets


def test_all_conflicting_views_still_receipt_the_new_selection(
    installed_local_skill, capsys
):
    env = installed_local_skill
    for view in env["views"]:
        view.unlink()
        view.write_text("foreign\n")
    saved = installer.publish_local_skill(
        "vc-audit", "# local B\n", env["saved"]["revision"]
    )
    assert saved["activation"] == "partial"
    receipt = json.loads(env["receipt"].read_text())
    assert (
        receipt["local_skill_selections"]["vc-audit"]["content_sha256"]
        == saved["revision"]
    )
    assert installer.cmd_runtime_install(env["args"]) == 1
    assert json.loads(capsys.readouterr().out)["skill_projection_status"] == "partial"
    assert all(view.read_text() == "foreign\n" for view in env["views"])
    assert env["owner"].read_skill("vc-audit")["text"] == "# local B\n"


def test_failed_save_restores_selector_and_its_selection_receipt(
    installed_local_skill, monkeypatch: pytest.MonkeyPatch
):
    env = installed_local_skill
    before = json.loads(env["receipt"].read_text())
    selector = env["selector"].read_bytes()
    pointers = [view.readlink() for view in env["views"]]
    replace = installer._replace_runtime_transaction_entry
    fired = False

    def interrupt(entry, source):
        nonlocal fired
        if Path(entry["path"]) == env["selector"] and not fired:
            fired = True
            raise RuntimeError("injected selector failure")
        return replace(entry, source)

    monkeypatch.setattr(installer, "_replace_runtime_transaction_entry", interrupt)
    with pytest.raises(RuntimeError, match="injected selector failure"):
        installer.publish_local_skill(
            "vc-audit", "# local B\n", env["saved"]["revision"]
        )
    after = json.loads(env["receipt"].read_text())
    assert after["local_skill_selections"] == before["local_skill_selections"]
    assert "config_transaction" not in after
    assert env["selector"].read_bytes() == selector
    assert [view.readlink() for view in env["views"]] == pointers


def test_legacy_migration_refuses_when_only_witness_changes_before_publish(
    installed_local_skill, monkeypatch: pytest.MonkeyPatch
):
    env = installed_local_skill
    old = json.loads(env["receipt"].read_text())
    old.pop("local_skill_selections")
    installer._checkpoint_runtime_install_receipt(env["runtime"], old)
    for view in env["views"][:-1]:
        view.unlink()
        view.write_text("foreign\n")
    witness = env["views"][-1]
    publish = installer._publish_runtime_config_transaction

    def race(*args, **kwargs):
        witness.unlink()
        witness.write_text("user retargeted during install\n")
        return publish(*args, **kwargs)

    monkeypatch.setattr(installer, "_publish_runtime_config_transaction", race)
    with pytest.raises(RuntimeError, match="selection witness changed"):
        installer.cmd_runtime_install(env["args"])
    assert witness.read_text() == "user retargeted during install\n"
    assert all(view.read_text() == "foreign\n" for view in env["views"][:-1])
    receipt = json.loads(env["receipt"].read_text())
    assert not receipt.get("local_skill_selections")
