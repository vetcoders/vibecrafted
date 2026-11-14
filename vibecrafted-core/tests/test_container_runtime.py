from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
from vibecrafted_core import dev_container, package_resources


def _target() -> dev_container.ContainerTarget:
    return dev_container.ContainerTarget(
        cli="docker",
        project="fixture",
        container_id="fixture-id",
        container_name="fixture",
        image="image",
        recipe_digest="recipe",
        host_root="/tmp",
    )


def test_guest_build_metadata_matches_package_owner() -> None:
    core = Path(__file__).resolve().parents[1]
    metadata = core / "vibecrafted_core/runtime/container-python"
    assert (metadata / "pyproject.toml").read_bytes() == (
        core / "pyproject.toml"
    ).read_bytes()
    assert (metadata / "README.md").read_bytes() == (core / "README.md").read_bytes()


def test_provision_projects_local_selection_and_needs_no_host_uv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "source" / "vibecrafted_core"
    descriptor = package / "runtime/container-python"
    descriptor.mkdir(parents=True)
    (descriptor / "pyproject.toml").write_text("[project]\nname='vibecrafted'\n")
    (descriptor / "README.md").write_text("fixture")
    (package / "VERSION").write_text("1.0.0")
    (package / "cli.py").write_text("fixture")
    bundled = package / "skills/vc-init"
    bundled.mkdir(parents=True)
    (bundled / "SKILL.md").write_text("bundled")
    (package / "skills/DELEGATION_MATRIX.md").write_text("shared")
    chosen = tmp_path / "local/vc-init"
    chosen.mkdir(parents=True)
    (chosen / "SKILL.md").write_text("selected")
    (chosen / "reference.md").write_text("reference")
    (chosen / "link.md").symlink_to("reference.md")
    monkeypatch.setattr(package_resources, "package_root", lambda: package)
    monkeypatch.setattr(
        dev_container,
        "_package_identity",
        lambda _: ("1.0.0+g12345678.dirty", "1" * 40, True),
    )
    calls: list[list[str]] = []

    def run(argv, env, *, timeout, input_bytes):
        calls.append(argv)
        with tarfile.open(fileobj=io.BytesIO(input_bytes), mode="r:gz") as archive:
            assert (
                archive.extractfile("project/vibecrafted_core/cli.py").read()
                == b"fixture"
            )
            assert (
                archive.extractfile("project/vibecrafted_core/VERSION").read()
                == b"1.0.0+g12345678.dirty\n"
            )
            assert (
                archive.extractfile("skills/selection-0/skills/vc-init/SKILL.md").read()
                == b"selected"
            )
            assert (
                archive.extractfile(
                    "skills/selection-0/skills/DELEGATION_MATRIX.md"
                ).read()
                == b"shared"
            )
            assert (
                archive.getmember("skills/selection-0/skills/vc-init/link.md").linkname
                == "reference.md"
            )
        return subprocess.CompletedProcess(argv, 0, b"a" * 64 + b"\n", b"")

    monkeypatch.setattr(dev_container, "_run", run)
    host = str(chosen / "SKILL.md")
    runtime = dev_container.prepare_agent_runtime(
        _target(), "fixture-run", selected_skills={host: Path(host)}, env={"PATH": ""}
    )
    assert runtime.skill_paths[host].endswith("/skills/vc-init/SKILL.md")
    assert runtime.version.endswith(".dirty")
    assert runtime.source_dirty is True
    assert runtime.wheel_sha256 == "a" * 64
    assert calls[0][0:3] == ["docker", "exec", "-i"]


def test_local_skill_keeps_its_versioned_companions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "source" / "vibecrafted_core"
    descriptor = package / "runtime/container-python"
    descriptor.mkdir(parents=True)
    (descriptor / "pyproject.toml").write_text("[project]\nname='vibecrafted'\n")
    (descriptor / "README.md").write_text("fixture")
    (package / "VERSION").write_text("1.0.0")
    bundled = package / "skills/vc-init"
    bundled.mkdir(parents=True)
    (bundled / "SKILL.md").write_text("bundled")
    (package / "skills/DELEGATION_MATRIX.md").write_text("bundled sibling")
    tree = tmp_path / "local/vc-init/versions/abc/tree"
    skill = tree / "skills/vc-init"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("[matrix](../DELEGATION_MATRIX.md)")
    (tree / "skills/DELEGATION_MATRIX.md").write_text("local sibling")
    (tree / "runtime").mkdir()
    (tree / "runtime/helper.sh").write_text("local runtime")
    monkeypatch.setattr(package_resources, "package_root", lambda: package)
    monkeypatch.setattr(
        dev_container,
        "_package_identity",
        lambda _: ("1.0.0+g12345678", "1" * 40, False),
    )

    def run(argv, env, *, timeout, input_bytes):
        with tarfile.open(fileobj=io.BytesIO(input_bytes), mode="r:gz") as archive:
            guest = tmp_path / "guest"
            guest.mkdir()
            archive.extractall(guest, filter="data")
            guest_skill = guest / "skills/selection-0/skills/vc-init/SKILL.md"
            assert (
                guest_skill.parent / "../DELEGATION_MATRIX.md"
            ).read_text() == "local sibling"
            assert (
                archive.extractfile("skills/bundled/skills/DELEGATION_MATRIX.md").read()
                == b"bundled sibling"
            )
            assert (
                archive.extractfile(
                    "skills/selection-0/skills/DELEGATION_MATRIX.md"
                ).read()
                == b"local sibling"
            )
            assert (
                archive.extractfile("skills/selection-0/runtime/helper.sh").read()
                == b"local runtime"
            )
        return subprocess.CompletedProcess(argv, 0, b"b" * 64 + b"\n", b"")

    monkeypatch.setattr(dev_container, "_run", run)
    host = str(skill / "SKILL.md")
    result = dev_container.prepare_agent_runtime(
        _target(), "fixture-run", selected_skills={host: Path(host)}, env={"PATH": ""}
    )
    assert result.skill_paths[host].endswith(
        "/skills/selection-0/skills/vc-init/SKILL.md"
    )


def test_runtime_snapshot_rejects_escaping_selected_symlink(tmp_path: Path) -> None:
    source = tmp_path / "vc-init"
    source.mkdir()
    (source / "SKILL.md").write_text("selected")
    (source / "escape").symlink_to("../outside")
    (tmp_path / "outside").write_text("private")
    with pytest.raises(dev_container.ContainerError, match="escapes"):
        dev_container._copy_runtime_tree(source, tmp_path / "copy")


def test_installed_package_provenance_binds_full_revision(tmp_path: Path) -> None:
    generation = tmp_path / "generation"
    package = generation / "vibecrafted-core/vibecrafted_core"
    package.mkdir(parents=True)
    (package / "VERSION").write_text("4.5.1+g12345678\n")
    (generation / "source-provenance.json").write_text(
        json.dumps({"source_revision": "12345678" + "a" * 32})
    )
    assert dev_container._package_identity(package) == (
        "4.5.1+g12345678",
        "12345678" + "a" * 32,
        None,
    )
    (package / "VERSION").write_text("4.5.1+gabcdef00\n")
    with pytest.raises(dev_container.ContainerError, match="does not match"):
        dev_container._package_identity(package)


def _git_fixture(root: Path) -> str:
    root.mkdir()
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    subprocess.run(["git", "init", "-q", str(root)], env=env, check=True)
    (root / "tracked.txt").write_text("baseline")
    subprocess.run(["git", "-C", str(root), "add", "."], env=env, check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        env=env,
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_package_identity_ignores_foreign_git_routing_and_reports_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    foreign = tmp_path / "foreign"
    _git_fixture(source)
    _git_fixture(foreign)
    package = source / "vibecrafted-core" / "vibecrafted_core"
    package.mkdir(parents=True)
    (package / "VERSION").write_text("4.5.1\n")
    clean_env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    subprocess.run(
        ["git", "-C", str(source), "add", "vibecrafted-core"], env=clean_env, check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "package",
        ],
        env=clean_env,
        check=True,
    )
    expected = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        env=clean_env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    assert dev_container._package_identity(package) == (
        f"4.5.1+g{expected[:8]}",
        expected,
        False,
    )
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    monkeypatch.setenv("GIT_INDEX_FILE", str(foreign / ".git" / "index"))
    assert dev_container._package_identity(package) == (
        f"4.5.1+g{expected[:8]}",
        expected,
        False,
    )
    (package / "untracked.py").write_text("local change")
    assert dev_container._package_identity(package) == (
        f"4.5.1+g{expected[:8]}.dirty",
        expected,
        True,
    )


def test_installed_package_inside_foreign_checkout_uses_its_provenance(
    tmp_path: Path,
) -> None:
    foreign = tmp_path / "foreign"
    _git_fixture(foreign)
    generation = foreign / "release"
    package = generation / "vibecrafted-core" / "vibecrafted_core"
    package.mkdir(parents=True)
    (package / "VERSION").write_text("4.5.1+g12345678\n")
    revision = "12345678" + "a" * 32
    (generation / "source-provenance.json").write_text(
        json.dumps({"source_revision": revision})
    )
    assert dev_container._package_identity(package) == (
        "4.5.1+g12345678",
        revision,
        None,
    )


def test_package_snapshot_digest_ignores_bytecode_but_detects_tamper(
    tmp_path: Path,
) -> None:
    (tmp_path / "SKILL.md").write_text("original")
    digest = dev_container._tree_digest(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__/example.pyc").write_bytes(b"cache")
    assert dev_container._tree_digest(tmp_path) == digest
    (tmp_path / "SKILL.md").write_text("modified")
    assert dev_container._tree_digest(tmp_path) != digest
