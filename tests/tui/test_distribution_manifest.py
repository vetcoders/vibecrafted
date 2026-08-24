from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts import distribution_manifest as manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_OWNER_REPO = "vetcoders/vibecrafted"
SOURCE_REVISION = "0123456789abcdef0123456789abcdef01234567"
OTHER_SOURCE_REVISION = "fedcba9876543210fedcba9876543210fedcba98"
SOURCE_PROVENANCE = {
    "schema": manifest.SOURCE_PROVENANCE_SCHEMA,
    "owner_repo": SOURCE_OWNER_REPO,
    "source_revision": SOURCE_REVISION,
    "payload": {
        "schema": manifest.DISTRIBUTION_TREE_SCHEMA,
        "algorithm": manifest.DISTRIBUTION_TREE_ALGORITHM,
        "tree_sha256": "0" * 64,
        "entry_count": 1,
    },
}

EXPECTED_REQUIRED = {
    "VERSION",
    "LICENSE",
    "README.md",
    "Makefile",
    "install.sh",
    "install.ps1",
    "install.toml",
    "scripts/distribution_manifest.py",
    "scripts/vetcoders_install.py",
    "scripts/runtime_paths.py",
    "scripts/vibecrafted",
    "scripts/verify-vibecrafted-product.sh",
    "vibecrafted-core/pyproject.toml",
    "vibecrafted-core/vibecrafted_core/VERSION",
    "vibecrafted-core/vibecrafted_core/deck/vibecrafted",
    "vibecrafted-core/vibecrafted_core/product_contract.py",
    "vibecrafted-core/vibecrafted_core/walkaround_runner.py",
    "vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json",
    "vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json",
    "vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub",
    "vibecrafted-core/vibecrafted_core/runtime",
    "vibecrafted-core/vibecrafted_core/skills",
    "vibecrafted-mcp/pyproject.toml",
    "plugins/iterm2/pyproject.toml",
    "vibecrafted-app/Cargo.toml",
    "vibecrafted-app/Cargo.lock",
    "vibecrafted-server/Cargo.toml",
    "vibecrafted-server/Cargo.lock",
}


def test_docker_entrypoint_seeds_packaged_canonical_skills() -> None:
    entrypoint = (REPO_ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "$VIBECRAFTED_SOURCE/vibecrafted-core/vibecrafted_core/skills/." in entrypoint
    )
    assert "$VIBECRAFTED_SOURCE/skills/." not in entrypoint
    assert "RUN chmod 0755" in dockerfile
    assert "RUN chmod +x" not in dockerfile


def _minimal_payload(root: Path) -> None:
    for relative in manifest.REQUIRED_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {relative}\n", encoding="utf-8")
    for relative in manifest.REQUIRED_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    for relative in manifest.REQUIRED_SURFACE_FILES.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"runtime sentinel for {relative}\n", encoding="utf-8")


def _source_provenance_for(
    root: Path,
    *,
    owner_repo: str = SOURCE_OWNER_REPO,
    source_revision: str = SOURCE_REVISION,
) -> dict[str, object]:
    entries = [
        path
        for path in manifest._walk_entries(root)
        if path.relative_to(root) != Path(manifest.SOURCE_PROVENANCE_FILE)
    ]
    tree = (
        manifest._distribution_tree_record(root)
        if entries
        else SOURCE_PROVENANCE["payload"]
    )
    return {
        "schema": manifest.SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": owner_repo,
        "source_revision": source_revision,
        "payload": tree,
    }


def _write_source_provenance(
    root: Path, payload: dict[str, object] | None = None
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / manifest.SOURCE_PROVENANCE_FILE
    record = _source_provenance_for(root) if payload is None else payload
    path.write_text(
        json.dumps(record, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o644)
    return path


def _clear_source_provenance_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBECRAFTED_SOURCE_OWNER_REPO", raising=False)
    monkeypatch.delenv("VIBECRAFTED_SOURCE_REVISION", raising=False)


def _git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *arguments],
        text=True,
    ).strip()


def _committed_git_source(root: Path) -> str:
    _minimal_payload(root)
    excluded_fixture = root / "tests" / "dev-only.txt"
    excluded_fixture.parent.mkdir(parents=True)
    excluded_fixture.write_text("committed dev-only fixture\n", encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Distribution Test")
    _git(root, "config", "user.email", "distribution-test@example.invalid")
    _git(root, "remote", "add", "origin", f"https://github.com/{SOURCE_OWNER_REPO}.git")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return _git(root, "rev-parse", "HEAD")


def _filter_clean_git_source(root: Path) -> tuple[str, Path]:
    _minimal_payload(root)
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Distribution Filter Test")
    _git(root, "config", "user.email", "distribution-filter@example.invalid")
    _git(
        root,
        "config",
        "filter.distribution-test.clean",
        "sed 's/^WORKTREE-.*$/COMMIT-BLOB/'",
    )
    _git(root, "config", "filter.distribution-test.smudge", "cat")
    _git(root, "config", "filter.distribution-test.required", "true")
    _git(root, "remote", "add", "origin", f"https://github.com/{SOURCE_OWNER_REPO}.git")
    (root / ".gitattributes").write_text(
        "scripts/vetcoders_install.py filter=distribution-test\n",
        encoding="utf-8",
    )
    target = root / "scripts" / "vetcoders_install.py"
    target.write_text("WORKTREE-ORIGINAL\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "filtered fixture")
    return _git(root, "rev-parse", "HEAD"), target


def test_load_source_provenance_accepts_only_the_exact_closed_record(
    tmp_path: Path,
) -> None:
    path = _write_source_provenance(tmp_path)

    assert manifest.load_source_provenance(tmp_path) == SOURCE_PROVENANCE
    assert path.read_text(encoding="utf-8") == (
        json.dumps(SOURCE_PROVENANCE, sort_keys=True, indent=2) + "\n"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            **SOURCE_PROVENANCE,
            "unexpected": "parallel provenance surface",
        },
        {
            "schema": manifest.SOURCE_PROVENANCE_SCHEMA,
            "owner_repo": SOURCE_OWNER_REPO,
        },
        {
            **SOURCE_PROVENANCE,
            "schema": "vibecrafted.source-provenance.v0",
        },
        {
            **SOURCE_PROVENANCE,
            "owner_repo": "not-an-owner-repo",
        },
        {
            **SOURCE_PROVENANCE,
            "source_revision": SOURCE_REVISION.upper(),
        },
    ],
)
def test_load_source_provenance_rejects_open_or_invalid_records(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    _write_source_provenance(tmp_path, payload)

    with pytest.raises(manifest.ManifestError, match="closed provenance schema"):
        manifest.load_source_provenance(tmp_path)


@pytest.mark.parametrize(
    ("owner_repo", "source_revision"),
    [
        (SOURCE_OWNER_REPO, None),
        (None, SOURCE_REVISION),
        ("", ""),
        (SOURCE_OWNER_REPO, SOURCE_REVISION.upper()),
    ],
)
def test_resolve_source_provenance_rejects_partial_or_noncanonical_explicit_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_repo: str | None,
    source_revision: str | None,
) -> None:
    _clear_source_provenance_environment(monkeypatch)

    with pytest.raises(manifest.ManifestError, match="explicit|source_revision"):
        manifest.resolve_source_provenance(
            tmp_path,
            owner_repo=owner_repo,
            source_revision=source_revision,
        )


def test_resolve_source_provenance_rejects_partial_environment_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    monkeypatch.setenv("VIBECRAFTED_SOURCE_OWNER_REPO", SOURCE_OWNER_REPO)

    with pytest.raises(manifest.ManifestError, match="environment.*atomic pair"):
        manifest.resolve_source_provenance(
            tmp_path,
            owner_repo=None,
            source_revision=None,
        )


def test_resolve_source_provenance_rejects_disagreeing_complete_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    _write_source_provenance(tmp_path)

    with pytest.raises(manifest.ManifestError, match="providers disagree"):
        manifest.resolve_source_provenance(
            tmp_path,
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=OTHER_SOURCE_REVISION,
        )


def test_manifest_names_complete_runtime_and_forbidden_junk() -> None:
    declared = set(manifest.REQUIRED_FILES) | set(manifest.REQUIRED_DIRECTORIES)

    assert EXPECTED_REQUIRED <= declared
    assert set(manifest.REQUIRED_SURFACE_FILES) == set(manifest.REQUIRED_DIRECTORIES)
    assert {
        ".DS_Store",
        ".gitignore",
        ".prettierignore",
        ".dockerignore",
        "package-lock.json",
        "CONTRIBUTING.md",
        ".loctree",
        ".backup",
        ".cache",
        "tests",
        ".github",
        ".env",
        ".git",
        "reports",
        "id_rsa",
        "credentials.json",
    } <= manifest.FORBIDDEN_COMPONENTS
    assert {".pem", ".p12", ".pfx"} <= set(manifest.FORBIDDEN_SUFFIXES)


@pytest.mark.parametrize(
    ("surface", "sentinel"), manifest.REQUIRED_SURFACE_FILES.items()
)
def test_validate_payload_rejects_empty_required_runtime_surface(
    tmp_path: Path, surface: str, sentinel: str
) -> None:
    payload = tmp_path / "payload"
    _minimal_payload(payload)
    sentinel_path = payload / sentinel
    sentinel_path.unlink()

    with pytest.raises(manifest.ManifestError) as exc_info:
        manifest.validate_payload(payload)

    assert f"missing required runtime content: {surface} -> {sentinel}" in str(
        exc_info.value
    )


def test_forbidden_artifact_filter_is_safe_for_runtime_subtrees() -> None:
    assert not manifest.path_is_forbidden("SKILL.md")
    assert not manifest.path_is_forbidden("scripts/codex_spawn.sh")
    assert not manifest.path_is_forbidden("vibecrafted-app/Cargo.lock")
    assert not manifest.path_is_forbidden("vibecrafted-server/Cargo.lock")
    assert manifest.path_is_forbidden("Cargo.lock")
    assert manifest.path_is_forbidden("scratch/Cargo.lock")
    assert manifest.path_is_forbidden("tests/test_spawn.py")
    assert manifest.path_is_forbidden("scripts/__pycache__/helper.pyc")

    assert not manifest.path_is_included("SKILL.md")
    assert manifest.path_is_included(
        "vibecrafted-core/vibecrafted_core/skills/vc-init/SKILL.md"
    )


def test_secret_env_files_are_forbidden_everywhere() -> None:
    assert manifest.path_is_forbidden(".env")
    assert manifest.path_is_forbidden("vibecrafted-vm/.env")
    assert manifest.path_is_forbidden("vibecrafted-vm/.env.local")
    assert manifest.path_is_forbidden("config/.env.production")
    assert manifest.path_is_forbidden(".env/nested.txt")

    assert not manifest.path_is_forbidden("vibecrafted-vm/.env.example")
    assert not manifest.path_is_forbidden("templates/hooks/config/template.husky.env")

    assert not manifest.path_is_included("vibecrafted-vm/.env")
    assert manifest.path_is_included("vibecrafted-vm/.env.example")


def test_secret_looking_names_and_dev_inventory_are_forbidden() -> None:
    assert manifest.path_is_forbidden(".loctree/atlas.json")
    assert manifest.path_is_forbidden(".git/HEAD")
    assert manifest.path_is_forbidden("docs/reports/internal.md")
    assert manifest.path_is_forbidden("scripts/.cache/tmp")
    assert manifest.path_is_forbidden("scripts/.pytest_cache/v/cache")
    assert manifest.path_is_forbidden("scripts/id_rsa")
    assert manifest.path_is_forbidden("scripts/id_rsa.local")
    assert manifest.path_is_forbidden("config/service.pem")
    assert manifest.path_is_forbidden("config/credentials.json")
    assert not manifest.path_is_forbidden("scripts/id_rsa.pub")
    assert not manifest.path_is_forbidden("docs/INSTALL.md")


def test_validate_payload_rejects_ignored_env_secret(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    _minimal_payload(payload)
    (payload / "vibecrafted-vm" / ".env").write_text(
        "TAILSCALE_AUTHKEY=tskey-auth-FAKEFAKEFAKE\n", encoding="utf-8"
    )

    with pytest.raises(manifest.ManifestError) as exc_info:
        manifest.validate_payload(payload)

    assert "forbidden path: vibecrafted-vm/.env" in str(exc_info.value)


def test_public_bundle_fails_closed_on_ignored_env_planted_in_staging_tree(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    staged = tmp_path / "staged"
    _minimal_payload(source)
    (source / ".gitignore").write_text(".env\n", encoding="utf-8")
    (source / "vibecrafted-vm" / ".env").write_text(
        "TAILSCALE_AUTHKEY=tskey-auth-test\n",
        encoding="utf-8",
    )

    manifest.stage_payload(source, staged, mirror=True)
    assert not (staged / "vibecrafted-vm" / ".env").exists()

    (staged / "vibecrafted-vm" / ".env").write_text(
        "TAILSCALE_AUTHKEY=tskey-auth-test\n",
        encoding="utf-8",
    )

    with pytest.raises(manifest.ManifestError) as check_info:
        manifest.validate_payload(staged)
    check_message = str(check_info.value)
    assert "forbidden path: vibecrafted-vm/.env" in check_message
    assert "tskey-auth-test" not in check_message

    archive = tmp_path / "public.tar.gz"
    with pytest.raises(manifest.ManifestError) as archive_info:
        manifest._write_archive(staged, archive, "vibecrafted-public")
    archive_message = str(archive_info.value)
    assert "forbidden path: vibecrafted-vm/.env" in archive_message
    assert "tskey-auth-test" not in archive_message
    assert not archive.exists()


@pytest.mark.parametrize(
    ("relative", "forbidden_marker"),
    [
        (".loctree/atlas.json", ".loctree"),
        (".git/HEAD", ".git"),
        ("docs/reports/internal.md", "docs/reports"),
        ("scripts/.pytest_cache/v/cache", "scripts/.pytest_cache"),
        ("scripts/id_rsa", "scripts/id_rsa"),
        ("config/service.pem", "config/service.pem"),
    ],
)
def test_public_archive_fails_closed_on_forbidden_staging_inventory(
    tmp_path: Path, relative: str, forbidden_marker: str
) -> None:
    payload = tmp_path / "payload"
    _minimal_payload(payload)
    planted = payload / relative
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text("planted-forbidden-inventory\n", encoding="utf-8")

    with pytest.raises(manifest.ManifestError) as check_info:
        manifest.validate_payload(payload)
    assert f"forbidden path: {forbidden_marker}" in str(check_info.value)

    archive = tmp_path / "public.tar.gz"
    with pytest.raises(manifest.ManifestError) as archive_info:
        manifest._write_archive(payload, archive, "vibecrafted-public")
    assert f"forbidden path: {forbidden_marker}" in str(archive_info.value)
    assert not archive.exists()


def test_stage_payload_never_copies_env_secret(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "payload"
    _minimal_payload(source)
    (source / "vibecrafted-vm" / ".env").write_text(
        "TAILSCALE_AUTHKEY=tskey-auth-FAKEFAKEFAKE\n", encoding="utf-8"
    )
    (source / "vibecrafted-vm" / ".env.example").write_text(
        "TAILSCALE_AUTHKEY=\n", encoding="utf-8"
    )

    manifest.stage_payload(source, destination, mirror=True)

    manifest.validate_payload(destination)
    assert not (destination / "vibecrafted-vm" / ".env").exists()
    assert (destination / "vibecrafted-vm" / ".env.example").is_file()


def test_stage_payload_filters_junk_and_mirrors_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "payload"
    _minimal_payload(source)
    (source / "scripts" / "keep.py").write_text("keep\n", encoding="utf-8")
    (source / "scripts" / ".DS_Store").write_text("junk\n", encoding="utf-8")
    (source / "scripts" / "tests").mkdir()
    (source / "scripts" / "tests" / "test_dev.py").write_text(
        "junk\n", encoding="utf-8"
    )
    (source / ".gitignore").write_text("junk\n", encoding="utf-8")
    destination.mkdir()
    (destination / "orphan.txt").write_text("stale\n", encoding="utf-8")

    manifest.stage_payload(source, destination, mirror=True)

    manifest.validate_payload(destination)
    assert (destination / "scripts" / "keep.py").is_file()
    assert not (destination / "scripts" / ".DS_Store").exists()
    assert not (destination / "scripts" / "tests").exists()
    assert not (destination / ".gitignore").exists()
    assert not (destination / "orphan.txt").exists()


def test_stage_payload_keeps_runtime_only_in_canonical_package_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "payload"
    _minimal_payload(source)

    manifest.stage_payload(source, destination, mirror=True)

    assert not (destination / "runtime").exists()
    runtime = destination / "vibecrafted-core/vibecrafted_core/runtime"
    assert (runtime / "README.md").is_file()
    manifest.validate_payload(destination)


def test_stage_payload_keeps_skills_only_in_canonical_package_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "payload"
    _minimal_payload(source)

    manifest.stage_payload(source, destination, mirror=True)

    assert not (destination / "skills").exists()
    skills = destination / "vibecrafted-core/vibecrafted_core/skills"
    assert (skills / "LIVING_TREE_RULE.md").is_file()
    manifest.validate_payload(destination)


def test_validate_payload_reports_missing_and_forbidden_paths(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    _minimal_payload(payload)
    (payload / "VERSION").unlink()
    (payload / "scripts" / "nested").mkdir()
    (payload / "scripts" / "nested" / ".DS_Store").write_text(
        "junk\n", encoding="utf-8"
    )

    with pytest.raises(manifest.ManifestError) as exc_info:
        manifest.validate_payload(payload)

    message = str(exc_info.value)
    assert "missing required path: VERSION" in message
    assert "forbidden path: scripts/nested/.DS_Store" in message


def test_walk_entries_prunes_forbidden_directories_before_descending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    descended: list[str] = []

    def fake_walk(root: Path, *, followlinks: bool):
        assert root == payload
        assert followlinks is False
        directory_names = ["scripts", ".git", "node_modules"]
        yield str(payload), directory_names, []
        descended.extend(directory_names)

    monkeypatch.setattr(manifest.os, "walk", fake_walk)

    assert list(manifest._walk_entries(payload)) == [
        payload / ".git",
        payload / "node_modules",
        payload / "scripts",
    ]
    assert descended == ["scripts"]


def test_stage_payload_rejects_symlink_that_escapes_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _minimal_payload(source)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (source / "scripts" / "escape").symlink_to(outside)

    with pytest.raises(manifest.ManifestError, match="symlink escapes payload"):
        manifest.stage_payload(source, tmp_path / "payload", mirror=True)


@pytest.mark.parametrize("relation", ["equal", "ancestor", "descendant"])
def test_stage_mirror_rejects_path_overlap_and_preserves_sentinel(
    tmp_path: Path, relation: str
) -> None:
    source = tmp_path / "owner" / "source"
    _minimal_payload(source)
    if relation == "equal":
        destination = source
    elif relation == "ancestor":
        destination = source.parent
    else:
        destination = source / "scripts" / "nested-output"
        destination.mkdir(parents=True)
    sentinel = destination / "OVERLAP-SENTINEL"
    sentinel.write_text("operator-owned\n", encoding="utf-8")

    with pytest.raises(manifest.ManifestError, match="overlap"):
        manifest.stage_payload(source, destination, mirror=True)

    assert sentinel.read_text(encoding="utf-8") == "operator-owned\n"


def test_stage_mirror_rejects_symlink_alias_overlap_and_preserves_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    alias = tmp_path / "source-alias"
    _minimal_payload(source)
    sentinel = source / "ALIAS-SENTINEL"
    sentinel.write_text("operator-owned\n", encoding="utf-8")
    alias.symlink_to(source, target_is_directory=True)

    with pytest.raises(manifest.ManifestError, match="overlap"):
        manifest.stage_payload(source, alias, mirror=True)

    assert alias.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "operator-owned\n"


def test_stage_copy_rejects_source_path_replacement_after_fd_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _minimal_payload(source)
    destination.mkdir()
    sentinel = destination / "KEEP"
    sentinel.write_text("old destination\n", encoding="utf-8")
    target = source / "scripts" / "vetcoders_install.py"
    replacement = source / "scripts" / "replacement.py"
    replacement.write_text("replacement bytes\n", encoding="utf-8")
    real_open = manifest.os.open
    replaced = False

    def replace_after_open(
        path: object, flags: int, *args: object, **kwargs: object
    ) -> int:
        nonlocal replaced
        descriptor = real_open(path, flags, *args, **kwargs)
        if Path(path) == target and not replaced:
            replaced = True
            replacement.replace(target)
        return descriptor

    monkeypatch.setattr(manifest.os, "open", replace_after_open)
    with pytest.raises(manifest.ManifestError, match="hardlinked|path changed"):
        manifest.stage_payload(source, destination, mirror=True)

    assert replaced
    assert sentinel.read_text(encoding="utf-8") == "old destination\n"


def test_stage_publish_cleanup_failure_is_nonfatal_after_atomic_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _minimal_payload(source)
    destination.mkdir()
    (destination / "OLD").write_text("old\n", encoding="utf-8")
    real_remove = manifest._remove_path

    def fail_private_backup_cleanup(path: Path) -> None:
        if ".previous-" in path.name:
            raise OSError("synthetic cleanup failure")
        real_remove(path)

    monkeypatch.setattr(manifest, "_remove_path", fail_private_backup_cleanup)
    manifest.stage_payload(source, destination, mirror=True)

    assert (destination / "VERSION").is_file()
    assert not (destination / "OLD").exists()


def test_manifest_cli_check_is_loud_and_nonzero_for_junk(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    _minimal_payload(payload)
    (payload / "package-lock.json").write_text("{}\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "distribution_manifest.py"),
            "check",
            "--root",
            str(payload),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "forbidden path: package-lock.json" in result.stderr


def test_stage_payload_preserves_one_canonical_provenance_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    destination = tmp_path / "payload"
    _minimal_payload(source)
    source_carrier = _write_source_provenance(source).read_bytes()

    returned = manifest.stage_payload(
        source,
        destination,
        mirror=True,
        owner_repo=SOURCE_OWNER_REPO,
        source_revision=SOURCE_REVISION,
        require_source_provenance=True,
    )

    provenance_path = destination / manifest.SOURCE_PROVENANCE_FILE
    expected = _source_provenance_for(destination)
    assert returned == expected
    assert manifest.load_source_provenance(destination) == expected
    assert provenance_path.read_bytes() == source_carrier
    assert provenance_path.read_text(encoding="utf-8") == (
        json.dumps(expected, sort_keys=True, indent=2) + "\n"
    )
    assert provenance_path.stat().st_mode & 0o777 == 0o644


def test_required_stage_infers_git_provenance_without_an_explicit_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    destination = tmp_path / "payload"
    revision = _committed_git_source(source)

    returned = manifest.stage_payload(
        source,
        destination,
        mirror=True,
        require_source_provenance=True,
    )

    assert returned == {
        "schema": manifest.SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": SOURCE_OWNER_REPO,
        "source_revision": revision,
        "payload": manifest._distribution_tree_record(destination),
    }
    assert manifest.load_source_provenance(destination) == returned


def test_required_stage_checks_all_provenance_providers_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    destination = tmp_path / "payload"
    _committed_git_source(source)
    monkeypatch.setenv("VIBECRAFTED_SOURCE_OWNER_REPO", SOURCE_OWNER_REPO)
    monkeypatch.setenv("VIBECRAFTED_SOURCE_REVISION", OTHER_SOURCE_REVISION)

    with pytest.raises(manifest.ManifestError, match="providers disagree"):
        manifest.stage_payload(
            source,
            destination,
            mirror=True,
            require_source_provenance=True,
        )

    assert not destination.exists()


def test_required_stage_materializes_git_bytes_without_an_explicit_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "filter-source"
    destination = tmp_path / "payload"
    _filter_clean_git_source(source)

    manifest.stage_payload(
        source,
        destination,
        mirror=True,
        require_source_provenance=True,
    )

    assert (destination / "scripts" / "vetcoders_install.py").read_text(
        encoding="utf-8"
    ) == "COMMIT-BLOB\n"
    assert (destination / manifest.SOURCE_PROVENANCE_FILE).is_file()


def test_manifest_cli_check_requires_and_matches_source_provenance(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    _minimal_payload(payload)
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "distribution_manifest.py"),
        "check",
        "--root",
        str(payload),
        "--require-source-provenance",
        "--expected-owner-repo",
        SOURCE_OWNER_REPO,
        "--expected-source-revision",
        SOURCE_REVISION,
    ]

    missing = subprocess.run(command, capture_output=True, text=True, check=False)
    assert missing.returncode == 2
    assert f"missing required path: {manifest.SOURCE_PROVENANCE_FILE}" in missing.stderr

    _write_source_provenance(payload)
    matching = subprocess.run(command, capture_output=True, text=True, check=False)
    assert matching.returncode == 0, matching.stderr

    mismatch = subprocess.run(
        [*command[:-1], OTHER_SOURCE_REVISION],
        capture_output=True,
        text=True,
        check=False,
    )
    assert mismatch.returncode == 2
    assert "does not match the expected owner/revision" in mismatch.stderr


def test_manifest_cli_parser_builds_and_accepts_each_provenance_flag_once() -> None:
    parser = manifest._build_parser()

    stage = parser.parse_args(
        [
            "stage",
            "--source",
            "/tmp/source",
            "--destination",
            "/tmp/destination",
            "--owner-repo",
            SOURCE_OWNER_REPO,
            "--source-revision",
            SOURCE_REVISION,
            "--require-source-provenance",
        ]
    )
    archive = parser.parse_args(
        [
            "archive",
            "--source",
            "/tmp/source",
            "--output",
            "/tmp/archive.tar.gz",
            "--root-name",
            "vibecrafted-test",
            "--owner-repo",
            SOURCE_OWNER_REPO,
            "--source-revision",
            SOURCE_REVISION,
        ]
    )

    assert stage.source_revision == SOURCE_REVISION
    assert stage.require_source_provenance is True
    assert archive.source_revision == SOURCE_REVISION


def test_archive_rejects_source_without_exact_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    _minimal_payload(source)

    with pytest.raises(manifest.ManifestError, match="provenance is unavailable"):
        manifest.create_archive(
            source,
            tmp_path / "unattributed.tar.gz",
            root_name="vibecrafted-unattributed",
        )


def test_non_git_explicit_identity_cannot_mint_v2_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    _minimal_payload(source)

    with pytest.raises(manifest.ManifestError, match="existing source-provenance v2"):
        manifest.stage_payload(
            source,
            tmp_path / "destination",
            mirror=True,
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=SOURCE_REVISION,
            require_source_provenance=True,
        )


def test_v1_source_carrier_is_rejected_on_provenance_required_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    _minimal_payload(source)
    _write_source_provenance(
        source,
        {
            "schema": "vibecrafted.source-provenance.v1",
            "owner_repo": SOURCE_OWNER_REPO,
            "source_revision": SOURCE_REVISION,
        },
    )

    with pytest.raises(manifest.ManifestError, match="closed provenance schema"):
        manifest.stage_payload(
            source,
            tmp_path / "destination",
            mirror=True,
            require_source_provenance=True,
        )


@pytest.mark.parametrize(
    "mutation",
    ["bytes", "mode", "type", "symlink", "add", "delete", "empty-directory"],
)
def test_carrier_digest_rejects_every_payload_tree_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    _minimal_payload(source)
    _write_source_provenance(source)
    target = source / "scripts" / "vetcoders_install.py"
    if mutation == "bytes":
        target.write_text("substituted bytes\n", encoding="utf-8")
    elif mutation == "mode":
        target.chmod(0o755)
    elif mutation == "type":
        target.unlink()
        target.symlink_to("../VERSION")
    elif mutation == "symlink":
        runtime = source / "vibecrafted-core/vibecrafted_core/runtime"
        shutil.rmtree(runtime)
        runtime.symlink_to("skills")
    elif mutation == "add":
        (source / "scripts" / "added.py").write_text("added\n", encoding="utf-8")
    elif mutation == "delete":
        target.unlink()
    elif mutation == "empty-directory":
        (source / "scripts" / "new-empty-directory").mkdir()
    else:  # pragma: no cover - parametrization owns the closed set.
        raise AssertionError(mutation)

    with pytest.raises(manifest.ManifestError, match="payload digest"):
        manifest.assert_source_payload_matches_provenance(
            source,
            owner_repo=None,
            source_revision=None,
        )


def test_carrier_only_stage_preserves_digest_bound_empty_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _minimal_payload(source)
    empty = source / "scripts" / "bound-empty-directory"
    empty.mkdir()
    _write_source_provenance(source)

    returned = manifest.stage_payload(
        source,
        destination,
        mirror=True,
        require_source_provenance=True,
    )

    assert (destination / empty.relative_to(source)).is_dir()
    assert returned == manifest.load_source_provenance(destination)


@pytest.mark.parametrize(
    "relative",
    [
        "vibecrafted-core/vibecrafted_core/product_contract.py",
        "vibecrafted-core/vibecrafted_core/walkaround_runner.py",
    ],
)
def test_unchanged_carrier_rejects_product_and_runner_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    _minimal_payload(source)
    _write_source_provenance(source)
    (source / relative).write_text("semantic substitute\n", encoding="utf-8")

    with pytest.raises(manifest.ManifestError, match="payload digest"):
        manifest.create_archive(
            source,
            tmp_path / "substituted.tar.gz",
            root_name="vibecrafted-substituted",
        )


def test_carrier_consistent_but_structurally_incomplete_source_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts" / "one.py").write_text("one\n", encoding="utf-8")
    _write_source_provenance(source)

    with pytest.raises(manifest.ManifestError, match="missing required"):
        manifest.create_archive(
            source,
            tmp_path / "incomplete.tar.gz",
            root_name="vibecrafted-incomplete",
        )


def test_archive_rejects_nested_enclosing_git_source_instead_of_carrier_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    enclosing = tmp_path / "enclosing"
    source = enclosing / "nested-source"
    output = tmp_path / "nested.tar.gz"
    _minimal_payload(source)
    _write_source_provenance(source)
    _git(enclosing, "init", "--quiet")
    _git(enclosing, "config", "user.name", "Nested Source Test")
    _git(enclosing, "config", "user.email", "nested-source@example.invalid")
    _git(enclosing, "add", ".")
    _git(enclosing, "commit", "--quiet", "-m", "fixture")

    with pytest.raises(manifest.ManifestError, match="nested inside.*Git worktree"):
        manifest.create_archive(
            source,
            output,
            root_name="vibecrafted-nested",
        )

    assert not output.exists()


def test_archive_rejects_existing_output_directory_without_deleting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    output = tmp_path / "existing-output"
    sentinel = output / "KEEP"
    _minimal_payload(source)
    output.mkdir()
    sentinel.write_text("operator-owned\n", encoding="utf-8")

    with pytest.raises(manifest.ManifestError, match="output.*directory"):
        manifest.create_archive(
            source,
            output,
            root_name="vibecrafted-output-directory",
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=SOURCE_REVISION,
        )

    assert output.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "operator-owned\n"


def test_archive_rejects_output_symlink_without_touching_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    target = tmp_path / "operator-owned.tar.gz"
    output = tmp_path / "archive.tar.gz"
    _minimal_payload(source)
    target.write_bytes(b"operator-owned\n")
    output.symlink_to(target)

    with pytest.raises(manifest.ManifestError, match="output.*symlink"):
        manifest.create_archive(
            source,
            output,
            root_name="vibecrafted-output-symlink",
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=SOURCE_REVISION,
        )

    assert output.is_symlink()
    assert target.read_bytes() == b"operator-owned\n"


@pytest.mark.parametrize("relation", ["equal", "ancestor", "descendant"])
def test_archive_rejects_output_overlap_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relation: str
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "owner" / "source"
    _minimal_payload(source)
    sentinel = source / "SOURCE-SENTINEL"
    sentinel.write_text("operator-owned\n", encoding="utf-8")
    if relation == "equal":
        output = source
    elif relation == "ancestor":
        output = source.parent
    else:
        output = source / "archive.tar.gz"

    with pytest.raises(manifest.ManifestError, match="overlap"):
        manifest.create_archive(source, output, root_name="vibecrafted-overlap")

    assert sentinel.read_text(encoding="utf-8") == "operator-owned\n"
    if relation == "descendant":
        assert not output.exists()


def test_archive_rejects_output_symlink_alias_into_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    alias = tmp_path / "archive-alias.tar.gz"
    target = source / "archive.tar.gz"
    _minimal_payload(source)
    sentinel = source / "SOURCE-SENTINEL"
    sentinel.write_text("operator-owned\n", encoding="utf-8")
    alias.symlink_to(target)

    with pytest.raises(manifest.ManifestError, match="overlap"):
        manifest.create_archive(source, alias, root_name="vibecrafted-alias")

    assert alias.is_symlink()
    assert not target.exists()
    assert sentinel.read_text(encoding="utf-8") == "operator-owned\n"


@pytest.mark.parametrize("through_alias", [False, True])
def test_archive_publish_rejects_non_dist_source_target_without_mutation(
    tmp_path: Path,
    through_alias: bool,
) -> None:
    source = tmp_path / "source"
    _minimal_payload(source)
    candidate = tmp_path / "candidate.tar.gz"
    candidate.write_bytes(b"verified candidate\n")
    tracked = source / "README.md"
    before = tracked.read_bytes()
    if through_alias:
        alias = tmp_path / "source-alias"
        alias.symlink_to(source, target_is_directory=True)
        output = alias / "README.md"
    else:
        output = tracked

    with pytest.raises(
        manifest.ManifestError,
        match="inside source must be below its physical dist directory",
    ):
        manifest.publish_archive_candidate(source, candidate, output)

    assert tracked.read_bytes() == before
    assert candidate.read_bytes() == b"verified candidate\n"


def test_archive_publish_atomically_moves_verified_candidate_into_dist(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _minimal_payload(source)
    distribution = source / "dist"
    distribution.mkdir()
    candidate = tmp_path / "candidate.tar.gz"
    candidate.write_bytes(b"verified candidate\n")
    output = distribution / "vibecrafted.tar.gz"
    output.write_bytes(b"old archive\n")

    published = manifest.publish_archive_candidate(source, candidate, output)

    assert published == output
    assert output.read_bytes() == b"verified candidate\n"
    assert not candidate.exists()


def test_archive_publish_rejects_symlink_output_without_touching_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _minimal_payload(source)
    distribution = source / "dist"
    distribution.mkdir()
    candidate = tmp_path / "candidate.tar.gz"
    candidate.write_bytes(b"verified candidate\n")
    target = tmp_path / "operator-owned.tar.gz"
    target.write_bytes(b"operator owned\n")
    output = distribution / "vibecrafted.tar.gz"
    output.symlink_to(target)

    with pytest.raises(manifest.ManifestError, match="must not be a symlink"):
        manifest.publish_archive_candidate(source, candidate, output)

    assert output.is_symlink()
    assert target.read_bytes() == b"operator owned\n"
    assert candidate.read_bytes() == b"verified candidate\n"


def test_archive_write_failure_preserves_existing_regular_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    output = tmp_path / "archive.tar.gz"
    _minimal_payload(source)
    _write_source_provenance(source)
    output.write_bytes(b"operator-owned\n")

    def fail_after_write(_payload: Path, candidate: Path, _root_name: str) -> None:
        candidate.write_bytes(b"partial candidate\n")
        raise manifest.ManifestError("synthetic archive write failure")

    monkeypatch.setattr(manifest, "_write_archive", fail_after_write)

    with pytest.raises(manifest.ManifestError, match="synthetic archive write failure"):
        manifest.create_archive(
            source,
            output,
            root_name="vibecrafted-write-failure",
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=SOURCE_REVISION,
        )

    assert output.read_bytes() == b"operator-owned\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "archive.tar.gz",
        "source",
    ]


def test_archive_verification_failure_preserves_existing_regular_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    output = tmp_path / "archive.tar.gz"
    _minimal_payload(source)
    _write_source_provenance(source)
    output.write_bytes(b"operator-owned\n")

    def reject_candidate(*_args: object, **_kwargs: object) -> None:
        raise manifest.ManifestError("synthetic archive verification failure")

    monkeypatch.setattr(
        manifest,
        "_assert_archive_matches_staged_payload",
        reject_candidate,
    )

    with pytest.raises(
        manifest.ManifestError, match="synthetic archive verification failure"
    ):
        manifest.create_archive(
            source,
            output,
            root_name="vibecrafted-verification-failure",
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=SOURCE_REVISION,
        )

    assert output.read_bytes() == b"operator-owned\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "archive.tar.gz",
        "source",
    ]


def test_archive_atomically_replaces_existing_regular_output_only_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    output = tmp_path / "archive.tar.gz"
    _minimal_payload(source)
    _write_source_provenance(source)
    output.write_bytes(b"operator-owned\n")
    original_replace = manifest.os.replace
    replacements: list[tuple[Path, Path]] = []

    def record_replace(
        candidate: str | bytes | Path, target: str | bytes | Path
    ) -> None:
        candidate_path = Path(candidate)
        target_path = Path(target)
        if candidate_path.is_file():
            assert target_path.read_bytes() == b"operator-owned\n"
            replacements.append((candidate_path, target_path))
        original_replace(candidate_path, target_path)

    monkeypatch.setattr(manifest.os, "replace", record_replace)

    manifest.create_archive(
        source,
        output,
        root_name="vibecrafted-atomic",
        owner_repo=SOURCE_OWNER_REPO,
        source_revision=SOURCE_REVISION,
    )

    assert len(replacements) == 1
    candidate, target = replacements[0]
    assert candidate.parent.parent == output.parent
    assert target == output
    assert not candidate.exists()
    with tarfile.open(output, "r:gz") as archive:
        assert f"vibecrafted-atomic/{manifest.SOURCE_PROVENANCE_FILE}" in (
            archive.getnames()
        )


def test_archive_has_one_safe_root_and_validated_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    archive_path = tmp_path / "vibecrafted-9.8.7.tar.gz"
    extracted = tmp_path / "extracted"
    _minimal_payload(source)
    (source / "scripts" / "keep.py").write_text("keep\n", encoding="utf-8")
    _write_source_provenance(source)

    manifest.create_archive(
        source,
        archive_path,
        root_name="vibecrafted-9.8.7",
        owner_repo=SOURCE_OWNER_REPO,
        source_revision=SOURCE_REVISION,
    )

    with tarfile.open(archive_path, "r:gz") as archive:
        names = archive.getnames()
        assert names
        assert all(
            name == "vibecrafted-9.8.7" or name.startswith("vibecrafted-9.8.7/")
            for name in names
        )
        assert all(
            not name.startswith("/") and ".." not in Path(name).parts for name in names
        )
        assert not any(name.endswith(".DS_Store") for name in names)
        for member in archive.getmembers():
            archive.extract(member, path=extracted, set_attrs=False, filter="data")

    payload = extracted / "vibecrafted-9.8.7"
    manifest.validate_payload(
        payload,
        require_source_provenance=True,
        expected_owner_repo=SOURCE_OWNER_REPO,
        expected_source_revision=SOURCE_REVISION,
    )
    assert manifest.load_source_provenance(payload) == _source_provenance_for(payload)
    assert (payload / "scripts" / "keep.py").is_file()


def test_archive_writer_emits_only_canonical_member_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    output = tmp_path / "canonical-modes.tar.gz"
    _minimal_payload(source)
    (source / "scripts").chmod(0o700)
    (source / "README.md").chmod(0o600)
    (source / "scripts" / "vibecrafted").chmod(0o700)
    _write_source_provenance(source)

    manifest.create_archive(
        source,
        output,
        root_name="vibecrafted-modes",
    )

    with tarfile.open(output, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
    assert members["vibecrafted-modes"].mode & 0o7777 == 0o755
    assert members["vibecrafted-modes/scripts"].mode & 0o7777 == 0o755
    assert members["vibecrafted-modes/README.md"].mode & 0o7777 == 0o644
    assert members["vibecrafted-modes/scripts/vibecrafted"].mode & 0o7777 == 0o755
    assert all(
        member.size == 0
        for member in members.values()
        if member.isdir() or member.issym()
    )


def test_archive_accepts_clean_committed_included_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    archive_path = tmp_path / "vibecrafted-clean.tar.gz"
    revision = _committed_git_source(source)

    manifest.create_archive(
        source,
        archive_path,
        root_name="vibecrafted-clean",
        owner_repo=SOURCE_OWNER_REPO,
        source_revision=revision,
    )

    assert archive_path.is_file()
    with tarfile.open(archive_path, "r:gz") as archive:
        carrier = archive.extractfile(
            f"vibecrafted-clean/{manifest.SOURCE_PROVENANCE_FILE}"
        )
        assert carrier is not None
        assert json.load(carrier)["source_revision"] == revision


@pytest.mark.parametrize(
    ("mutation", "relative"),
    [
        ("tracked", "scripts/vetcoders_install.py"),
        ("index", "scripts/runtime_paths.py"),
        ("deleted", "scripts/distribution_manifest.py"),
    ],
)
def test_archive_rejects_every_included_git_drift_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    relative: str,
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    revision = _committed_git_source(source)
    path = source / relative

    if mutation in {"tracked", "index"}:
        path.write_text("changed included payload\n", encoding="utf-8")
        if mutation == "index":
            _git(source, "add", relative)
    elif mutation == "deleted":
        path.unlink()
    else:  # pragma: no cover - parametrization owns the closed mutation set.
        raise AssertionError(mutation)

    with pytest.raises(manifest.ManifestError, match="included path") as exc_info:
        manifest.create_archive(
            source,
            tmp_path / f"{mutation}.tar.gz",
            root_name="vibecrafted-dirty",
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=revision,
        )

    assert relative in str(exc_info.value)


@pytest.mark.parametrize("ignored", [False, True])
def test_archive_projects_git_tree_without_local_untracked_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ignored: bool,
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    archive_path = tmp_path / "vibecrafted-projected.tar.gz"
    revision = _committed_git_source(source)
    relative = Path("scripts/local-archive-input.py")
    path = source / relative
    path.write_text("host-local payload\n", encoding="utf-8")
    if ignored:
        (source / ".git" / "info" / "exclude").write_text(
            f"/{relative.as_posix()}\n", encoding="utf-8"
        )

    manifest.create_archive(
        source,
        archive_path,
        root_name="vibecrafted-projected",
        owner_repo=SOURCE_OWNER_REPO,
        source_revision=revision,
    )

    with tarfile.open(archive_path, "r:gz") as archive:
        assert f"vibecrafted-projected/{relative.as_posix()}" not in archive.getnames()


def test_archive_allows_tracked_and_untracked_excluded_dev_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    archive_path = tmp_path / "vibecrafted-dev-dirty.tar.gz"
    revision = _committed_git_source(source)
    (source / "tests" / "dev-only.txt").write_text(
        "dirty but excluded\n", encoding="utf-8"
    )
    (source / "tests" / "untracked-dev-only.txt").write_text(
        "also excluded\n", encoding="utf-8"
    )
    untracked_empty_runtime_dir = (
        source / "vibecrafted-core" / "vibecrafted_core" / "foundation"
    )
    untracked_empty_runtime_dir.mkdir(parents=True)

    manifest.create_archive(
        source,
        archive_path,
        root_name="vibecrafted-dev-dirty",
        owner_repo=SOURCE_OWNER_REPO,
        source_revision=revision,
    )

    assert archive_path.is_file()
    with tarfile.open(archive_path, "r:gz") as archive:
        assert not any(
            member.name.endswith("vibecrafted-core/vibecrafted_core/foundation")
            for member in archive.getmembers()
        )


def test_archive_preserves_carrier_only_extracted_source_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "extracted"
    archive_path = tmp_path / "repacked.tar.gz"
    _minimal_payload(source)
    _write_source_provenance(source)

    manifest.create_archive(
        source,
        archive_path,
        root_name="vibecrafted-repacked",
    )

    assert archive_path.is_file()


def test_public_provenance_assertion_rejects_dirty_git_but_accepts_carrier_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    git_source = tmp_path / "git-source"
    revision = _committed_git_source(git_source)
    (git_source / "scripts" / "vetcoders_install.py").write_text(
        "dirty included runtime source\n", encoding="utf-8"
    )

    with pytest.raises(manifest.ManifestError, match="included path"):
        manifest.assert_source_payload_matches_provenance(
            git_source,
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=revision,
        )

    extracted = tmp_path / "extracted"
    _minimal_payload(extracted)
    _write_source_provenance(extracted)
    assert manifest.assert_source_payload_matches_provenance(
        extracted,
        owner_repo=None,
        source_revision=None,
    ) == _source_provenance_for(extracted)


def test_stage_cannot_launder_dirty_git_into_a_source_carrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "git-source"
    destination = tmp_path / "staged"
    revision = _committed_git_source(source)
    (source / "scripts" / "vetcoders_install.py").write_text(
        "dirty included runtime source\n", encoding="utf-8"
    )

    with pytest.raises(manifest.ManifestError, match="included path"):
        manifest.stage_payload(
            source,
            destination,
            mirror=True,
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=revision,
            require_source_provenance=True,
        )

    assert not destination.exists()


def test_filter_clean_worktree_is_projected_from_commit_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "filter-source"
    destination = tmp_path / "staged"
    archive_path = tmp_path / "filtered.tar.gz"
    revision, target = _filter_clean_git_source(source)

    assert _git(source, "status", "--porcelain=v1") == ""
    assert _git(source, "show", f"{revision}:scripts/vetcoders_install.py") == (
        "COMMIT-BLOB"
    )
    assert target.read_text(encoding="utf-8") == "WORKTREE-ORIGINAL\n"

    manifest.stage_payload(
        source,
        destination,
        mirror=True,
        owner_repo=SOURCE_OWNER_REPO,
        source_revision=revision,
        require_source_provenance=True,
    )
    assert (destination / "scripts" / "vetcoders_install.py").read_text(
        encoding="utf-8"
    ) == "COMMIT-BLOB\n"

    manifest.create_archive(
        source,
        archive_path,
        root_name="vibecrafted-filtered",
        owner_repo=SOURCE_OWNER_REPO,
        source_revision=revision,
    )
    with tarfile.open(archive_path, "r:gz") as archive:
        member = archive.extractfile(
            "vibecrafted-filtered/scripts/vetcoders_install.py"
        )
        assert member is not None
        assert member.read() == b"COMMIT-BLOB\n"


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    [
        ("bytes", "bytes:scripts/vetcoders_install.py"),
        ("mode", "mode:scripts/vetcoders_install.py"),
        ("type", "type:scripts/vetcoders_install.py"),
    ],
)
def test_copy_time_destination_swap_is_rejected_before_carrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected_issue: str,
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    destination = tmp_path / "staged"
    revision = _committed_git_source(source)
    relative = Path("scripts/vetcoders_install.py")
    original_materialize = manifest._materialize_git_payload
    swapped = False

    def swap_after_materialize(
        source_root: Path, destination_root: Path, source_revision: str
    ) -> None:
        nonlocal swapped
        original_materialize(source_root, destination_root, source_revision)
        destination_path = destination_root / relative
        if mutation == "bytes":
            destination_path.write_text("COPY-TIME-RACE\n", encoding="utf-8")
        elif mutation == "mode":
            destination_path.chmod(destination_path.stat().st_mode | 0o111)
        elif mutation == "type":
            destination_path.unlink()
            destination_path.symlink_to("../VERSION")
        else:  # pragma: no cover - parametrization owns the mutation set.
            raise AssertionError(mutation)
        swapped = True

    monkeypatch.setattr(manifest, "_materialize_git_payload", swap_after_materialize)

    with pytest.raises(manifest.ManifestError, match=expected_issue):
        manifest.stage_payload(
            source,
            destination,
            mirror=True,
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=revision,
            require_source_provenance=True,
        )

    assert swapped
    assert _git(source, "status", "--porcelain=v1") == ""
    assert not (destination / manifest.SOURCE_PROVENANCE_FILE).exists()


def test_post_stage_archive_swap_cannot_emit_misattributed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    archive_path = tmp_path / "raced.tar.gz"
    revision = _committed_git_source(source)
    original_write = manifest._write_archive

    def write_swapped_payload(payload_root: Path, output: Path, root_name: str) -> None:
        (payload_root / "scripts" / "vetcoders_install.py").write_text(
            "POST-STAGE-RACE\n", encoding="utf-8"
        )
        original_write(payload_root, output, root_name)

    monkeypatch.setattr(manifest, "_write_archive", write_swapped_payload)

    with pytest.raises(manifest.ManifestError, match="payload-digest"):
        manifest.create_archive(
            source,
            archive_path,
            root_name="vibecrafted-raced",
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=revision,
        )

    assert not archive_path.exists()


def test_archive_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    source = tmp_path / "source"
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    _minimal_payload(source)
    _write_source_provenance(source)

    for output in (first, second):
        manifest.create_archive(
            source,
            output,
            root_name="vibecrafted-test",
            owner_repo=SOURCE_OWNER_REPO,
            source_revision=SOURCE_REVISION,
        )

    assert first.read_bytes() == second.read_bytes()


def test_archive_bytes_change_with_exact_source_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_source_provenance_environment(monkeypatch)
    first_source = tmp_path / "first-source"
    second_source = tmp_path / "second-source"
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    _minimal_payload(first_source)
    _minimal_payload(second_source)
    _write_source_provenance(first_source)
    _write_source_provenance(
        second_source,
        _source_provenance_for(
            second_source,
            source_revision=OTHER_SOURCE_REVISION,
        ),
    )

    manifest.create_archive(
        first_source,
        first,
        root_name="vibecrafted-test",
        owner_repo=SOURCE_OWNER_REPO,
        source_revision=SOURCE_REVISION,
    )
    manifest.create_archive(
        second_source,
        second,
        root_name="vibecrafted-test",
        owner_repo=SOURCE_OWNER_REPO,
        source_revision=OTHER_SOURCE_REVISION,
    )

    assert first.read_bytes() != second.read_bytes()


def test_portable_bootstrap_lanes_use_canonical_provenance_archives() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "portable.yml").read_text(
        encoding="utf-8"
    )
    portable = (REPO_ROOT / "tests" / "portable" / "run.sh").read_text(encoding="utf-8")

    assert "python3 scripts/distribution_manifest.py archive" in workflow
    assert 'source_revision="$(git rev-parse HEAD)"' in workflow
    assert '--owner-repo "$GITHUB_REPOSITORY"' in workflow
    assert '--source-revision "$source_revision"' in workflow
    assert '--source-revision "$GITHUB_SHA"' not in workflow
    assert 'tar -czf "$archive"' not in workflow

    assert 'python3 "$repo_root/scripts/distribution_manifest.py" archive' in portable
    assert '--owner-repo "$bootstrap_source_owner"' in portable
    assert '--source-revision "$bootstrap_source_revision"' in portable
    assert 'tar -czf "$bootstrap_archive"' not in portable
