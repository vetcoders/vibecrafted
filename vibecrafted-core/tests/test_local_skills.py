"""Local versions are private, immutable candidates until the installer selects one."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from vibecrafted_core import local_skills, package_resources


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    runtime_home = tmp_path / "runtime-home"
    package = tmp_path / "package"
    skill = package / "skills" / "vc-demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("original\n", encoding="utf-8")
    (skill / "reference.md").write_text("reference\n", encoding="utf-8")
    sibling = package / "skills" / "vc-other"
    sibling.mkdir()
    (sibling / "SKILL.md").write_text("sibling\n", encoding="utf-8")
    (package / "skills" / "DELEGATION_MATRIX.md").write_text(
        "rules\n", encoding="utf-8"
    )
    (package / "runtime" / "scripts").mkdir(parents=True)
    (package / "runtime" / "scripts" / "common.sh").write_text(
        "common\n", encoding="utf-8"
    )
    shutil.copy2(
        Path(local_skills.__file__).parent / "portable_lock.py",
        package / "portable_lock.py",
    )
    monkeypatch.setattr(local_skills, "__file__", str(package / "local_skills.py"))
    monkeypatch.setattr(
        local_skills,
        "_paths",
        lambda: SimpleNamespace(
            vibecrafted_home=lambda: home, vibecrafted_runtime_home=lambda: runtime_home
        ),
    )
    return home, package


def _select(proof: local_skills.LocalSkillProjection) -> None:
    # The real installer owns selector publication. This fixture mimics only the
    # sealed postimage, without pretending to test its transaction or receipt.
    path = local_skills.local_skill_selector_path(proof.name)
    path.write_bytes(local_skills.selected_bytes(proof))


def test_prepare_is_not_activation_and_preserves_relative_resource_topology(store):
    home, package = store
    before = local_skills.read_skill("vc-demo")
    proof = local_skills.prepare_skill("vc-demo", "local\n", before["revision"])
    assert proof.directory.is_absolute()
    assert local_skills.read_skill("vc-demo")["text"] == "original\n"
    assert (proof.directory / "reference.md").read_text() == "reference\n"
    assert (proof.directory / "../DELEGATION_MATRIX.md").read_text() == "rules\n"
    assert (proof.directory / "../vc-other/SKILL.md").read_text() == "sibling\n"
    assert (
        proof.directory / "../../runtime/scripts/common.sh"
    ).read_text() == "common\n"
    _select(proof)
    assert local_skills.resolve_execution_skill("vc-demo").read_text() == "local\n"
    assert local_skills.read_skill("vc-demo")["origin"] == "local"
    assert (package / "skills/vc-demo/SKILL.md").read_text() == "original\n"
    assert local_skills.validated_local_skill("vc-demo") == proof
    assert (home / "local-skills").is_dir()


def test_successive_versions_are_immutable_and_stale_revision_conflicts(store):
    original = local_skills.read_skill("vc-demo")
    first = local_skills.prepare_skill("vc-demo", "first\n", original["revision"])
    _select(first)
    with pytest.raises(local_skills.LocalSkillError, match="changed") as exc:
        local_skills.prepare_skill("vc-demo", "lost update\n", original["revision"])
    assert exc.value.code == "conflict"
    second = local_skills.prepare_skill("vc-demo", "second\n", first.content_sha256)
    _select(second)
    assert first != second
    assert (first.directory / "SKILL.md").read_text() == "first\n"
    assert (second.directory / "SKILL.md").read_text() == "second\n"
    assert local_skills.validate_candidate(first) == first


def test_packaged_resource_path_remains_bundled_with_local_selection(
    store, monkeypatch
):
    _, package = store
    monkeypatch.setattr(package_resources, "_package_files", lambda: package)
    before = local_skills.read_skill("vc-demo")
    proof = local_skills.prepare_skill("vc-demo", "private edit\n", before["revision"])
    _select(proof)
    assert (
        package_resources.skills_path() / "vc-demo/SKILL.md"
    ).read_text() == "original\n"
    assert (
        local_skills.resolve_execution_skill("vc-demo").read_text() == "private edit\n"
    )


def test_snapshot_survives_replacement_of_bundled_resources(store):
    _, package = store
    original = local_skills.read_skill("vc-demo")
    proof = local_skills.prepare_skill("vc-demo", "personal\n", original["revision"])
    _select(proof)
    (package / "skills/vc-other/SKILL.md").write_text("new release\n")
    (package / "runtime/scripts/common.sh").write_text("new runtime\n")
    assert local_skills.read_skill("vc-demo")["text"] == "personal\n"
    assert (proof.directory / "../vc-other/SKILL.md").read_text() == "sibling\n"
    assert (
        proof.directory / "../../runtime/scripts/common.sh"
    ).read_text() == "common\n"


@pytest.mark.parametrize(
    "name", ["../vc-demo", "vc-../other", "vc-demo/file", "vc-demo\\file", "other"]
)
def test_rejects_traversal_and_unmanaged_names(store, name):
    with pytest.raises(local_skills.LocalSkillError) as exc:
        local_skills.read_skill(name)
    assert exc.value.code == "invalid"


def test_foreign_store_and_skill_directory_are_not_adopted(store):
    home, _ = store
    root = home / "local-skills"
    root.mkdir(parents=True)
    (root / "foreign").write_text("mine")
    with pytest.raises(local_skills.LocalSkillError):
        local_skills.prepare_skill(
            "vc-demo", "mine", local_skills._digest(b"original\n")
        )
    assert (root / "foreign").read_text() == "mine"


def test_source_symlink_escape_is_refused(store, tmp_path):
    _, package = store
    secret = tmp_path / "private"
    secret.write_text("private")
    (package / "skills/vc-demo/escape").symlink_to(secret)
    original = local_skills.read_skill("vc-demo")
    with pytest.raises(local_skills.LocalSkillError) as exc:
        local_skills.prepare_skill("vc-demo", "edited", original["revision"])
    assert exc.value.code == "unsafe"
    assert secret.read_text() == "private"


def test_tampered_candidate_and_selector_fail_closed(store):
    original = local_skills.read_skill("vc-demo")
    proof = local_skills.prepare_skill("vc-demo", "edited", original["revision"])
    _select(proof)
    (proof.directory / "reference.md").write_text("tampered")
    with pytest.raises(local_skills.LocalSkillError) as exc:
        local_skills.read_skill("vc-demo")
    assert exc.value.code == "unsafe"
    with pytest.raises(local_skills.LocalSkillError):
        local_skills.validate_candidate(proof)


def test_pending_or_malformed_runtime_receipt_refuses_reads(store, tmp_path):
    runtime = tmp_path / "runtime-home"
    (runtime / "tools").mkdir(parents=True)
    (runtime / "tools/.vibecrafted-install.lock").write_text("lock")
    receipt = runtime / "install-receipt.json"
    receipt.write_text(
        json.dumps(
            {"schema": "vibecrafted.runtime-install.v1", "config_transaction": {}}
        )
    )
    with pytest.raises(local_skills.LocalSkillError):
        local_skills.read_skill("vc-demo")
    receipt.write_text("not json")
    with pytest.raises(local_skills.LocalSkillError):
        local_skills.read_skill("vc-demo")


def test_foreign_skill_directory_is_not_adopted(store):
    home, _ = store
    local_skills._init_store()
    foreign = home / "local-skills/vc-demo"
    foreign.mkdir()
    (foreign / "mine").write_text("owned")
    with pytest.raises(local_skills.LocalSkillError) as exc:
        local_skills.prepare_skill(
            "vc-demo", "edit", local_skills._digest(b"original\n")
        )
    assert exc.value.code == "unsafe"
    assert (foreign / "mine").read_text() == "owned"


def test_selection_symlink_is_refused(store, tmp_path):
    original = local_skills.read_skill("vc-demo")
    proof = local_skills.prepare_skill("vc-demo", "edit", original["revision"])
    foreign = tmp_path / "foreign-selection"
    foreign.write_bytes(local_skills.selected_bytes(proof))
    local_skills.local_skill_selector_path("vc-demo").symlink_to(foreign)
    with pytest.raises(local_skills.LocalSkillError) as exc:
        local_skills.read_skill("vc-demo")
    assert exc.value.code == "unsafe"
    assert foreign.is_file()


def test_legacy_receipt_without_lock_reads_bundled_skill(store, tmp_path):
    runtime = tmp_path / "runtime-home"
    runtime.mkdir()
    (runtime / "install-receipt.json").write_text(
        json.dumps({"schema": "vibecrafted.runtime-install.v1"})
    )
    assert local_skills.read_skill("vc-demo")["text"] == "original\n"
    assert local_skills.validated_local_skill("vc-demo") is None
    assert local_skills.resolve_execution_skill("vc-demo").read_text() == "original\n"
    assert not (runtime / "tools/.vibecrafted-install.lock").exists()


@pytest.mark.parametrize(
    "receipt",
    [
        {"schema": "vibecrafted.runtime-install.v1", "install_pending": True},
        {"schema": "vibecrafted.runtime-install.v1", "config_transaction": {}},
        {"schema": "vibecrafted.runtime-install.v1", "config_pending": {"x": {}}},
        {"schema": "vibecrafted.runtime-install.v1", "uninstall_pending": True},
        {"schema": "vibecrafted.runtime-install.v1", "rescue_pending": True},
        {"schema": "wrong"},
    ],
)
def test_missing_lock_never_bypasses_pending_or_invalid_receipt(
    store, tmp_path, receipt
):
    runtime = tmp_path / "runtime-home"
    runtime.mkdir()
    (runtime / "install-receipt.json").write_text(json.dumps(receipt))
    with pytest.raises(local_skills.LocalSkillError) as exc:
        local_skills.read_skill("vc-demo")
    assert exc.value.code == "unsafe"


@pytest.mark.parametrize("shape", ["directory", "file", "link", "dangling"])
def test_missing_lock_refuses_any_existing_or_aliased_store(store, tmp_path, shape):
    home, _ = store
    runtime = tmp_path / "runtime-home"
    runtime.mkdir()
    (runtime / "install-receipt.json").write_text(
        json.dumps({"schema": "vibecrafted.runtime-install.v1"})
    )
    home.mkdir()
    root = home / "local-skills"
    if shape == "directory":
        root.mkdir()
        (root / "owner.json").write_text('{"schema":"vibecrafted.local-skills.v1"}')
    elif shape == "file":
        root.write_text("user data")
    else:
        target = tmp_path / "target"
        if shape == "link":
            target.mkdir()
        root.symlink_to(target, target_is_directory=True)
    with pytest.raises(local_skills.LocalSkillError) as exc:
        local_skills.read_skill("vc-demo")
    assert exc.value.code == "unsafe"
    assert root.is_symlink() if shape in ("link", "dangling") else root.exists()


@pytest.mark.parametrize("arrival", ["lock", "store"])
def test_missing_lock_rechecks_for_publication_during_bundled_read(
    store, tmp_path, monkeypatch, arrival
):
    home, package = store
    runtime = tmp_path / "runtime-home"
    runtime.mkdir()
    (runtime / "install-receipt.json").write_text(
        json.dumps({"schema": "vibecrafted.runtime-install.v1"})
    )
    original_read = local_skills._read_regular
    appeared = False

    def read_and_publish(path: Path) -> bytes:
        nonlocal appeared
        data = original_read(path)
        if not appeared and path == package / "skills/vc-demo/SKILL.md":
            appeared = True
            if arrival == "lock":
                lock = runtime / "tools/.vibecrafted-install.lock"
                lock.parent.mkdir()
                lock.write_text("lease")
            else:
                root = home / "local-skills"
                root.mkdir(parents=True)
                (root / "owner.json").write_text(
                    '{"schema":"vibecrafted.local-skills.v1"}'
                )
        return data

    monkeypatch.setattr(local_skills, "_read_regular", read_and_publish)
    with pytest.raises(local_skills.LocalSkillError) as exc:
        local_skills.read_skill("vc-demo")
    assert exc.value.code == "unsafe"
