"""One repository selector for every command: --repo, legacy --root, conflicts."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core.repo_selection import (
    RepoSelectionError,
    add_repo_arguments,
    git_toplevel,
    parse_worktree_flag,
    select_repository,
    selected_root,
)


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)
    return path


def test_repo_flag_selects_from_any_cwd_including_outside_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path / "repo with space")
    elsewhere = tmp_path / "no git here"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    selection = select_repository(str(repo), "")

    assert selection.path == str(repo.resolve())
    assert selection.source == "repo"
    assert selection.flag == "--repo"
    assert selection.is_git
    assert selection.git_toplevel == str(repo.resolve())


def test_legacy_root_keeps_identical_semantics(tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()

    selection = select_repository("", str(repo))

    assert selection.path == str(repo.resolve())
    assert selection.source == "root"
    assert selection.flag == "--root"
    assert not selection.is_git


def test_conflicting_repo_and_root_fail_loudly(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()

    with pytest.raises(RepoSelectionError, match="conflicting --repo .* and --root"):
        select_repository(str(left), str(right), label="vibecrafted workflow")


def test_same_path_spelled_twice_is_not_a_conflict(tmp_path: Path) -> None:
    repo = tmp_path / "same"
    repo.mkdir()

    selection = select_repository(str(repo), str(tmp_path / "same" / "."))

    assert selection.path == str(repo.resolve())
    assert selection.source == "repo"


def test_missing_and_non_directory_paths_name_the_flag(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(RepoSelectionError, match="--repo is not an existing directory"):
        select_repository(str(tmp_path / "nope"), "")
    with pytest.raises(RepoSelectionError, match="--root is not an existing directory"):
        select_repository("", str(tmp_path / "nope"))
    with pytest.raises(RepoSelectionError, match="--repo is not a directory"):
        select_repository(str(file_path), "")


def test_require_git_refuses_plain_directories_only_when_asked(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert select_repository(str(plain), "").is_git is False
    with pytest.raises(RepoSelectionError, match="not inside a Git repository"):
        select_repository(str(plain), "", require_git=True)


def test_fallback_callable_and_path_and_absence(tmp_path: Path) -> None:
    here = tmp_path / "here"
    here.mkdir()

    assert select_repository("", "", fallback=here).source == "fallback"
    assert select_repository("", "", fallback=lambda: here).path == str(here.resolve())
    with pytest.raises(RepoSelectionError, match="no repository selected"):
        select_repository("", "", fallback="")


def test_git_toplevel_survives_missing_git_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()

    assert git_toplevel(plain, env={"PATH": str(empty_bin)}) == ""
    selection = select_repository(str(plain), "", env={"PATH": str(empty_bin)})
    assert selection.path == str(plain.resolve())
    assert not selection.is_git


def test_add_repo_arguments_keeps_both_destinations(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    parser = argparse.ArgumentParser()
    add_repo_arguments(parser)

    args = parser.parse_args(["--repo", str(repo)])
    assert args.repo == str(repo)
    assert args.root == ""
    assert selected_root(args) == str(repo.resolve())

    args = parser.parse_args(["--root", str(repo)])
    assert args.root == str(repo)
    assert selected_root(args) == str(repo.resolve())

    args = parser.parse_args(["--repo", str(repo), "--root", str(tmp_path)])
    with pytest.raises(RepoSelectionError, match="conflicting"):
        selected_root(args)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("1", True),
        ("on", True),
        ("false", False),
        ("no", False),
        ("0", False),
        ("off", False),
        (True, True),
        (False, False),
    ],
)
def test_parse_worktree_flag_words(value: object, expected: bool) -> None:
    assert parse_worktree_flag(value) is expected  # type: ignore[arg-type]


def test_parse_worktree_flag_rejects_garbage() -> None:
    with pytest.raises(RepoSelectionError, match="--worktree expects true or false"):
        parse_worktree_flag("maybe", label="vibecrafted workflow")


def test_selection_expands_user_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / "proj").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    assert os.path.expanduser("~") == str(home)

    assert select_repository("~/proj", "").path == str((home / "proj").resolve())


def _repo_selection_cli(
    *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    core = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(core) + (
        f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else ""
    )
    return subprocess.run(
        [sys.executable, "-m", "vibecrafted_core.repo_selection", *args],
        check=False,
        cwd=cwd or core,
        env=env,
        capture_output=True,
        text=True,
    )


def test_cli_worktree_false_stays_directory_only(tmp_path: Path) -> None:
    """Parse-time ``--worktree false`` must not enter the launch resolver."""
    plain = tmp_path / "plain"
    plain.mkdir()

    result = _repo_selection_cli("--worktree", "false", "--repo", str(plain))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(plain.resolve())
    assert "Git repository" not in result.stderr
    assert not any(path != plain for path in tmp_path.iterdir() if path.is_dir())


def test_cli_worktree_true_still_requires_git_for_launch(tmp_path: Path) -> None:
    """Launch-time ``--worktree true`` still refuses a plain directory."""
    plain = tmp_path / "plain"
    plain.mkdir()

    result = _repo_selection_cli("--worktree", "true", "--repo", str(plain))

    assert result.returncode == 2
    assert "not inside a Git repository" in result.stderr
    assert not any(path != plain for path in tmp_path.iterdir() if path.is_dir())
