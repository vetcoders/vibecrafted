from __future__ import annotations

import importlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib
import vibecrafted_core
from vibecrafted_core import workflows

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "vibecrafted-core"


def test_every_public_name_resolves() -> None:
    """`__all__` is the package contract; a broken hub import or stale lazy
    entry must surface here, not at a downstream caller's import site."""
    missing = []
    for name in vibecrafted_core.__all__:
        try:
            getattr(vibecrafted_core, name)
        except AttributeError:
            missing.append(name)
    assert not missing, f"names in __all__ that do not resolve: {missing}"


def test_lazy_workflow_exports_load_on_demand() -> None:
    # These live behind module.__getattr__ to keep CLI modules out of a bare
    # `import vibecrafted_core`; accessing one must materialize a real symbol.
    assert callable(vibecrafted_core.launch_workflow)
    assert callable(vibecrafted_core.native_resume_run)
    assert callable(vibecrafted_core.vibecrafted_launcher)


def test_bare_package_import_does_not_preload_control_plane() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-c",
            (
                "import sys, vibecrafted_core; "
                "assert 'vibecrafted_core.control_plane' not in sys.modules"
            ),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={
            **__import__("os").environ,
            "PYTHONPATH": str(CORE_ROOT),
        },
    )

    assert result.returncode == 0, result.stderr


def test_lazy_access_caches_into_module_globals() -> None:
    # Second access must return the same object the lazy loader cached.
    first = vibecrafted_core.normalize_launch_spec
    second = vibecrafted_core.normalize_launch_spec
    assert first is second


def test_unknown_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        vibecrafted_core.this_symbol_does_not_exist  # noqa: B018


def test_version_matches_distribution_metadata() -> None:
    expected = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = CORE_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    packaged = (
        (CORE_ROOT / "vibecrafted_core" / "VERSION").read_text(encoding="utf-8").strip()
    )

    assert packaged == expected
    assert data["project"]["version"] == expected
    try:
        installed_version = importlib.metadata.version("vibecrafted")
    except importlib.metadata.PackageNotFoundError:
        installed_version = None
    if installed_version is not None:
        assert installed_version == expected
    # Runtime resolution deliberately prefers a stamped staged install over a
    # bare living-tree version. During a version bump that staged generation
    # can still be the previous release; it must remain explicit, never masquerade
    # as the new bare X.Y.Z.
    resolved = vibecrafted_core.__version__
    bare = expected.split("+", 1)[0]
    assert vibecrafted_core.version_is_stamped(resolved)
    if not resolved.startswith(bare):
        assert resolved == vibecrafted_core.read_staged_tools_version()


def test_version_falls_back_to_installed_metadata(monkeypatch) -> None:
    monkeypatch.setattr(vibecrafted_core, "read_version_file", lambda _root: "unknown")
    monkeypatch.setattr(
        vibecrafted_core, "read_staged_tools_version", lambda: "unknown"
    )
    monkeypatch.setattr(vibecrafted_core, "_version_from_git", lambda *_a, **_k: None)
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "8.7.6")

    assert vibecrafted_core._resolve_installed_version() == "8.7.6+UNSTAMPED"


def test_version_prefers_staged_stamp_over_bare_package(monkeypatch) -> None:
    """Living-tree editable must not report bare 3.7.0 when make install stamped."""
    monkeypatch.setattr(vibecrafted_core, "read_version_file", lambda _root: "3.7.0")
    monkeypatch.setattr(
        vibecrafted_core,
        "read_staged_tools_version",
        lambda: "3.7.0+ga2b2fbad",
    )
    assert vibecrafted_core._resolve_installed_version() == "3.7.0+ga2b2fbad"


def test_version_is_stamped_helper() -> None:
    assert vibecrafted_core.version_is_stamped("3.7.0+ga2b2fbad")
    assert vibecrafted_core.version_is_stamped("1.0.0+gdeadbeef")
    assert not vibecrafted_core.version_is_stamped("3.7.0")
    assert not vibecrafted_core.version_is_stamped("3.7.0+UNSTAMPED")
    assert not vibecrafted_core.version_is_stamped("unknown")


def test_version_bump_updates_every_declared_projection(tmp_path: Path) -> None:
    version_file = tmp_path / "VERSION"
    plugin_manifest = tmp_path / "plugin.json"
    pyprojects = (
        tmp_path / "vibecrafted-core" / "pyproject.toml",
        tmp_path / "vibecrafted-mcp" / "pyproject.toml",
    )
    packaged_versions = (
        tmp_path / "vibecrafted-core" / "vibecrafted_core" / "VERSION",
        tmp_path / "vibecrafted-mcp" / "vibecrafted_mcp" / "VERSION",
    )
    cargo_manifests = (
        tmp_path / "vibecrafted-server" / "web" / "Cargo.toml",
        tmp_path / "vibecrafted-server" / "control-core" / "Cargo.toml",
    )
    cargo_locks = {
        tmp_path / "vibecrafted-server" / "Cargo.lock": (
            "control-core",
            "vibecrafted-server-web",
        ),
        tmp_path / "vibecrafted-app" / "Cargo.lock": ("control-core",),
    }
    release_texts = {
        tmp_path
        / "vibecrafted-app"
        / "shell-agent"
        / "app"
        / "project.yml": 'settings:\n  MARKETING_VERSION: "1.4.1"\n',
        tmp_path
        / "packaging"
        / "homebrew"
        / "Formula"
        / "vibecrafted.rb": '  version "1.4.1"\n',
        tmp_path
        / "packaging"
        / "homebrew"
        / "Casks"
        / "vibecrafted-app.rb": '  version "1.4.1,fixture"\n',
        tmp_path
        / "README.md": '<img alt="Version 1.4.1" src="badge/version-1.4.1-informational">\n',
        tmp_path / "docs" / "RELEASE_CHECKLIST.md": (
            "# Cut 1.4.1 now\n"
            "One GitHub Release `v1.4.1`\n"
            "`VERSION` is already `1.4.1`\n"
            'test "$(tr -d \'[:space:]\' < VERSION)" = "1.4.1"\n'
            'git tag -a v1.4.1 -m "release"\n'
            "git push origin v1.4.1\n"
        ),
    }
    version_file.write_text("1.4.1\n", encoding="utf-8")
    plugin_manifest.write_text(
        json.dumps({"name": "vibecrafted", "version": "1.4.1"}, indent=2) + "\n",
        encoding="utf-8",
    )
    for pyproject in pyprojects:
        pyproject.parent.mkdir(parents=True)
        pyproject.write_text(
            '[project]\nname = "fixture"\nversion = "1.4.1"\n', encoding="utf-8"
        )
    for packaged in packaged_versions:
        packaged.parent.mkdir(parents=True)
        packaged.write_text("1.4.1\n", encoding="utf-8")
    for cargo in cargo_manifests:
        cargo.parent.mkdir(parents=True)
        # A dependency `version =` below [package] must never be rewritten.
        cargo.write_text(
            '[package]\nname = "fixture"\nversion = "1.4.1"\n\n'
            '[dependencies.leptos]\nversion = "0.8"\n',
            encoding="utf-8",
        )
    for lock_path, package_names in cargo_locks.items():
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            "# generated fixture\n"
            + "".join(
                f'[[package]]\nname = "{name}"\nversion = "1.4.1"\n\n'
                for name in package_names
            ),
            encoding="utf-8",
        )
    for path, text in release_texts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "version_bump.py"),
            "minor",
            "--file",
            str(version_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert version_file.read_text(encoding="utf-8") == "1.5.0\n"
    assert json.loads(plugin_manifest.read_text(encoding="utf-8"))["version"] == "1.5.0"
    for pyproject in pyprojects:
        assert (
            tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
            == "1.5.0"
        )
    for packaged in packaged_versions:
        assert packaged.read_text(encoding="utf-8") == "1.5.0\n"
    for cargo in cargo_manifests:
        manifest = tomllib.loads(cargo.read_text(encoding="utf-8"))
        assert manifest["package"]["version"] == "1.5.0"
        assert manifest["dependencies"]["leptos"]["version"] == "0.8"
    for lock_path, package_names in cargo_locks.items():
        packages = {
            package["name"]: package["version"]
            for package in tomllib.loads(lock_path.read_text(encoding="utf-8"))[
                "package"
            ]
        }
        assert {packages[name] for name in package_names} == {"1.5.0"}
    for path in release_texts:
        updated = path.read_text(encoding="utf-8")
        assert "1.4.1" not in updated
        assert "1.5.0" in updated


def test_version_check_rejects_stale_readme_and_release_checklist_fixtures(
    tmp_path: Path,
) -> None:
    relatives = (
        "VERSION",
        "plugin.json",
        "vibecrafted-core/pyproject.toml",
        "vibecrafted-core/vibecrafted_core/VERSION",
        "vibecrafted-mcp/pyproject.toml",
        "vibecrafted-mcp/vibecrafted_mcp/VERSION",
        "vibecrafted-server/web/Cargo.toml",
        "vibecrafted-server/control-core/Cargo.toml",
        "vibecrafted-app/shell-agent/app/project.yml",
        "packaging/homebrew/Formula/vibecrafted.rb",
        "packaging/homebrew/Casks/vibecrafted-app.rb",
        "README.md",
        "docs/RELEASE_CHECKLIST.md",
    )
    for relative in relatives:
        source = REPO_ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "version_bump.py"),
        "--check",
        "--file",
        str(tmp_path / "VERSION"),
    ]
    current = subprocess.run(command, capture_output=True, text=True, check=False)
    assert current.returncode == 0, current.stderr

    readme = tmp_path / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("Version 4.3.0", "Version 9.9.9"),
        encoding="utf-8",
    )
    stale_readme = subprocess.run(command, capture_output=True, text=True, check=False)
    assert stale_readme.returncode == 2
    assert "README.md#projection-1=9.9.9" in stale_readme.stderr

    shutil.copy2(REPO_ROOT / "README.md", readme)
    checklist = tmp_path / "docs" / "RELEASE_CHECKLIST.md"
    checklist.write_text(
        checklist.read_text(encoding="utf-8").replace("# Cut 4.3.0", "# Cut 9.9.9", 1),
        encoding="utf-8",
    )
    stale_checklist = subprocess.run(
        command, capture_output=True, text=True, check=False
    )
    assert stale_checklist.returncode == 2
    assert "RELEASE_CHECKLIST.md#projection-1=9.9.9" in stale_checklist.stderr


def test_version_bump_rejects_drift_without_partial_writes(tmp_path: Path) -> None:
    version_file = tmp_path / "VERSION"
    pyprojects = (
        tmp_path / "vibecrafted-core" / "pyproject.toml",
        tmp_path / "vibecrafted-mcp" / "pyproject.toml",
    )
    packaged_versions = (
        tmp_path / "vibecrafted-core" / "vibecrafted_core" / "VERSION",
        tmp_path / "vibecrafted-mcp" / "vibecrafted_mcp" / "VERSION",
    )
    cargo_manifests = (
        tmp_path / "vibecrafted-server" / "web" / "Cargo.toml",
        tmp_path / "vibecrafted-server" / "control-core" / "Cargo.toml",
    )
    version_file.write_text("1.4.1\n", encoding="utf-8")
    for index, pyproject in enumerate(pyprojects):
        pyproject.parent.mkdir(parents=True)
        pyproject.write_text(
            f'[project]\nname = "fixture"\nversion = "1.4.{index}"\n',
            encoding="utf-8",
        )
    for packaged in packaged_versions:
        packaged.parent.mkdir(parents=True)
        packaged.write_text("1.4.1\n", encoding="utf-8")
    for cargo in cargo_manifests:
        cargo.parent.mkdir(parents=True)
        cargo.write_text(
            '[package]\nname = "fixture"\nversion = "1.4.1"\n', encoding="utf-8"
        )

    before = {
        path: path.read_text(encoding="utf-8")
        for path in (version_file, *pyprojects, *packaged_versions, *cargo_manifests)
    }
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "version_bump.py"),
            "patch",
            "--file",
            str(version_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "version drift" in result.stderr.lower()
    assert {path: path.read_text(encoding="utf-8") for path in before} == before


def test_workflows_package_reexports_resolve() -> None:
    missing = [name for name in workflows.__all__ if not hasattr(workflows, name)]
    assert not missing, f"workflows.__all__ names that do not resolve: {missing}"


def test_supported_workflows_is_non_empty() -> None:
    assert workflows.SUPPORTED_WORKFLOWS, "SUPPORTED_WORKFLOWS must not be empty"


def test_package_import_is_idempotent() -> None:
    reloaded = importlib.import_module("vibecrafted_core")
    assert reloaded is vibecrafted_core
