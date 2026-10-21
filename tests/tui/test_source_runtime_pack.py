"""The source lane binds donors before the canonical Runtime Pack builder runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _source_component_fixture import donor, repository_environment

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/build-source-runtime-pack.py"


@pytest.fixture
def inherited_git_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reproduce repository-location variables inherited from a foreign hook."""
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "foreign-worktree"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "foreign-index"))


@pytest.mark.parametrize("wrong_version", [False, True])
def test_source_check_binds_exact_commits_without_touching_donors(
    tmp_path: Path, wrong_version: bool, inherited_git_location: None
) -> None:
    frame = tmp_path / "vc-frame"
    terminal = tmp_path / "vc-terminal"
    frame_sha = donor(frame, "Cargo.toml", "4.3.1")
    terminal_sha = donor(terminal, "alacritty/Cargo.toml", "0.18.0-dev")
    (frame / "uncommitted.txt").write_text("Founder's work\n")
    manifest = tmp_path / "pins.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "vibecrafted.source-components.v1",
                "components": {
                    "vc-frame": {
                        "repository": "unused",
                        "version": "4.3.2" if wrong_version else "4.3.1",
                        "revision": frame_sha,
                    },
                    "vc-terminal": {
                        "repository": "unused",
                        "version": "0.18.0-dev",
                        "revision": terminal_sha,
                    },
                },
            }
        )
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--manifest", str(manifest)],
        env={
            **os.environ,
            "VIBECRAFTED_FRAME_REPO": str(frame),
            "VIBECRAFTED_TERMINAL_REPO": str(terminal),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) != wrong_version, result.stderr
    if wrong_version:
        assert "version at pinned commit is 4.3.1" in result.stderr
    assert (frame / "uncommitted.txt").read_text() == "Founder's work\n"
    assert (
        subprocess.check_output(
            ["git", "-C", str(frame), "rev-parse", "HEAD"],
            text=True,
            env=repository_environment(),
        ).strip()
        == frame_sha
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(terminal), "rev-parse", "HEAD"],
            text=True,
            env=repository_environment(),
        ).strip()
        == terminal_sha
    )


def test_source_check_fetches_missing_donor_objects_into_temporary_repositories(
    tmp_path: Path, inherited_git_location: None
) -> None:
    frame = tmp_path / "upstream-frame"
    terminal = tmp_path / "upstream-terminal"
    frame_sha = donor(frame, "Cargo.toml", "4.3.1")
    terminal_sha = donor(terminal, "alacritty/Cargo.toml", "0.18.0-dev")
    manifest = tmp_path / "pins.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "vibecrafted.source-components.v1",
                "components": {
                    "vc-frame": {
                        "repository": frame.as_uri(),
                        "version": "4.3.1",
                        "revision": frame_sha,
                    },
                    "vc-terminal": {
                        "repository": terminal.as_uri(),
                        "version": "0.18.0-dev",
                        "revision": terminal_sha,
                    },
                },
            }
        )
    )
    environment = {**os.environ}
    environment.pop("VIBECRAFTED_FRAME_REPO", None)
    environment.pop("VIBECRAFTED_TERMINAL_REPO", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--manifest", str(manifest)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "vibecrafted-source-donors-" in result.stdout
    assert (
        subprocess.check_output(
            ["git", "-C", str(frame), "rev-parse", "HEAD"],
            text=True,
            env=repository_environment(),
        ).strip()
        == frame_sha
    )


@pytest.mark.parametrize("builder", ["helper", "linux", "windows"])
@pytest.mark.parametrize("use_override", [False, True])
def test_builders_materialize_changed_pins_and_preserve_local_work(
    tmp_path: Path, builder: str, use_override: bool, inherited_git_location: None
) -> None:
    import hashlib
    import tarfile

    from _source_component_fixture import prepare, source_fixture, write_pins

    checkout, pins, repos = source_fixture(tmp_path)
    for generation in ("first", "next"):
        before = {}
        for name, repo in repos.items():
            (repo / "selected.txt").write_text(generation)
            subprocess.run(
                ["git", "-C", str(repo), "add", "selected.txt"],
                env=repository_environment(),
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "-qm",
                    generation,
                ],
                env=repository_environment(),
                check=True,
            )
            pins["components"][name]["revision"] = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                env=repository_environment(),
                text=True,
            ).strip()
            (repo / "selected.txt").write_text("dirty and staged Founder work")
            subprocess.run(
                ["git", "-C", str(repo), "add", "selected.txt"],
                env=repository_environment(),
                check=True,
            )
            (repo / "untracked.txt").write_text("untracked Founder work")
            before[name] = subprocess.check_output(
                ["git", "-C", str(repo), "status", "--porcelain=v1"],
                env=repository_environment(),
            )
        write_pins(checkout, pins)
        destination = tmp_path / f"selected {generation}"
        result = prepare(
            checkout, destination, builder, repos if use_override else None
        )
        assert result.returncode == 0, result.stderr
        selection = json.loads((destination / "selection.json").read_text())
        for name, repo in repos.items():
            pin = selection["components"][name]
            assert pin["revision"] == pins["components"][name]["revision"]
            assert (destination / name / "selected.txt").read_text() == generation
            assert not (destination / name / "untracked.txt").exists()
            archive = destination / f"{name}.tar"
            assert (
                pin["source_archive_sha256"]
                == hashlib.sha256(archive.read_bytes()).hexdigest()
            )
            with tarfile.open(archive) as exported:
                assert (
                    exported.extractfile("selected.txt").read().decode() == generation
                )
            assert (
                subprocess.check_output(
                    ["git", "-C", str(repo), "rev-parse", "HEAD"],
                    env=repository_environment(),
                    text=True,
                ).strip()
                == pin["revision"]
            )
            assert (
                subprocess.check_output(
                    ["git", "-C", str(repo), "status", "--porcelain=v1"],
                    env=repository_environment(),
                )
                == before[name]
            )
            assert (
                repo / "selected.txt"
            ).read_text() == "dirty and staged Founder work"
            assert (
                len(
                    subprocess.check_output(
                        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                        env=repository_environment(),
                        text=True,
                    ).split("worktree ")
                )
                == 2
            )


@pytest.mark.parametrize("builder", ["helper", "linux", "windows"])
@pytest.mark.parametrize(
    "failure",
    [
        "missing-component",
        "missing-revision",
        "moving-ref",
        "unknown-commit",
        "wrong-version",
        "wrong-identity",
        "missing-override",
    ],
)
def test_builder_source_selection_refuses_without_stale_fallback(
    tmp_path: Path, builder: str, failure: str
) -> None:
    from _source_component_fixture import prepare, source_fixture, write_pins

    checkout, pins, repos = source_fixture(tmp_path)
    frame = pins["components"]["vc-frame"]
    if failure == "missing-component":
        pins["components"].pop("vc-terminal")
    elif failure == "missing-revision":
        frame.pop("revision")
    elif failure == "moving-ref":
        frame["revision"] = "main"
    elif failure == "unknown-commit":
        frame["revision"] = "0" * 40
    elif failure == "wrong-version":
        frame["version"] = "99.0.0"
    elif failure == "wrong-identity":
        repo = repos["vc-frame"]
        cargo = repo / "Cargo.toml"
        cargo.write_text(
            cargo.read_text().replace('name = "vc-frame"', 'name = "unrelated"')
        )
        subprocess.run(
            ["git", "-C", str(repo), "add", "."],
            env=repository_environment(),
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "wrong identity",
            ],
            env=repository_environment(),
            check=True,
        )
        frame["revision"] = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            env=repository_environment(),
            text=True,
        ).strip()
    else:
        repos["vc-frame"] = tmp_path / "absent"
    write_pins(checkout, pins)
    destination = tmp_path / "rejected"
    result = prepare(checkout, destination, builder, repos)
    assert result.returncode != 0, result.stdout
    assert not (destination / "selection.json").exists()
    assert not (destination / "vc-frame").exists()


@pytest.mark.parametrize("builder", ["helper", "linux", "windows"])
def test_source_materialization_refuses_to_overwrite_existing_directory(
    tmp_path: Path, builder: str
) -> None:
    from _source_component_fixture import prepare, source_fixture

    checkout, _, repos = source_fixture(tmp_path)
    destination = tmp_path / "existing"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("existing user data")
    result = prepare(checkout, destination, builder, repos)
    assert result.returncode != 0
    assert marker.read_text() == "existing user data"
    assert not (destination / "selection.json").exists()


@pytest.mark.parametrize("builder", ["helper", "linux", "windows"])
def test_selected_commit_is_independent_of_donor_head_and_replace_refs(
    tmp_path: Path, builder: str
) -> None:
    from _source_component_fixture import prepare, source_fixture

    checkout, pins, repos = source_fixture(tmp_path)
    frame = repos["vc-frame"]
    original = pins["components"]["vc-frame"]["revision"]
    cargo = frame / "Cargo.toml"
    committed_manifest = cargo.read_bytes()
    cargo.write_text(cargo.read_text().replace('name = "vc-frame"', 'name = "other"'))
    subprocess.run(
        ["git", "-C", str(frame), "add", "."], env=repository_environment(), check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(frame),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "later commit",
        ],
        env=repository_environment(),
        check=True,
    )
    later = subprocess.check_output(
        ["git", "-C", str(frame), "rev-parse", "HEAD"],
        env=repository_environment(),
        text=True,
    ).strip()
    subprocess.run(
        ["git", "-C", str(frame), "replace", original, later],
        env=repository_environment(),
        check=True,
    )
    destination = tmp_path / "historical"
    result = prepare(checkout, destination, builder, repos)
    assert result.returncode == 0, result.stderr
    assert (destination / "vc-frame/Cargo.toml").read_bytes() == committed_manifest
    receipt = json.loads((destination / "selection.json").read_text())
    assert receipt["components"]["vc-frame"]["revision"] == original
    assert (
        subprocess.check_output(
            ["git", "-C", str(frame), "rev-parse", "HEAD"],
            env=repository_environment(),
            text=True,
        ).strip()
        == later
    )
