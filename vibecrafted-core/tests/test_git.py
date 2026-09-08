from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import git


def _init_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "tester"], check=True
    )
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


def _commit(path: Path, name: str, text: str) -> None:
    (path / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", name], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", name], check=True)


def _vc_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    core = Path(git.__file__).parents[1]
    env = {**os.environ, "PYTHONPATH": str(core)}
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from vibecrafted_core.git import main; raise SystemExit(main())",
            *args,
            str(repo),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _repo_full(repo: Path, shell: str) -> subprocess.CompletedProcess[str]:
    dispatch = Path(git.__file__).parent / "runtime" / "shell" / "lib" / "dispatch.sh"
    return subprocess.run(
        [shell, "-c", 'source "$1"; repo-full', "repo-full", str(dispatch)],
        check=True,
        capture_output=True,
        text=True,
        cwd=repo,
    )


def test_repo_full_reports_git_availability_and_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)

    payload = git.repo_full(repo)

    assert payload["git_available"] is True
    assert payload["repo"] == "repo"
    assert payload["branch"] == "main"
    assert payload["recent_commits"][0]["title"] == "init"


def test_repo_full_preserves_porcelain_index_columns(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("unstaged\n", encoding="utf-8")

    payload = git.repo_full(repo)

    assert payload["status"] == {"staged": 0, "unstaged": 1, "untracked": 0}


def test_repo_full_rejects_non_repo_path(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not a git repository"):
        git.repo_full(tmp_path)


def test_vc_git_preserves_rich_repo_full_and_prints_every_worktree(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    sibling = tmp_path / "visible-worktree"
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-q",
            "-b",
            "visible",
            str(sibling),
        ],
        check=True,
    )

    assert git.main([str(repo)]) == 0

    output = capfd.readouterr().out
    assert "==================== REPO FULL ====================" in output
    assert "==================== WORKTREES ====================" in output
    assert str(repo) in output
    assert str(sibling) in output
    assert "visible" in output


def test_vc_git_reports_named_upstream_divergence_in_json_and_rich_output(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    repo = tmp_path / "repo"
    _init_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True
    )
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
    subprocess.run(
        ["git", "-C", str(other), "config", "user.email", "t@example.com"], check=True
    )
    subprocess.run(
        ["git", "-C", str(other), "config", "user.name", "tester"], check=True
    )
    _commit(other, "remote.txt", "remote\n")
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], check=True)
    _commit(repo, "local.txt", "local\n")

    payload = json.loads(_vc_git(repo, "--json").stdout)
    rich = _vc_git(repo).stdout

    assert payload["upstream_divergence"] == {
        "reference": "origin/main",
        "status": "known",
        "ahead": 1,
        "behind": 1,
    }
    assert "Ahead:             1 (vs origin/main; known)" in rich
    assert "Behind:            1 (vs origin/main; known)" in rich


def test_vc_git_worktree_integration_distinguishes_merged_patch_equivalent_and_unmerged(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    merged = tmp_path / "merged tree"
    unmerged = tmp_path / "unmerged tree"
    equivalent = tmp_path / "equivalent tree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "merged", str(merged)],
        check=True,
    )
    _commit(repo, "base.txt", "base\n")
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-q",
            "-b",
            "unmerged",
            str(unmerged),
        ],
        check=True,
    )
    _commit(unmerged, "unmerged.txt", "unmerged\n")
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-q",
            "-b",
            "equivalent",
            str(equivalent),
        ],
        check=True,
    )
    _commit(equivalent, "same.txt", "same\n")
    _commit(repo, "root.txt", "root\n")
    subprocess.run(["git", "-C", str(repo), "cherry-pick", "equivalent"], check=True)
    (merged / "README.md").write_text("dirty\n", encoding="utf-8")

    payload = json.loads(_vc_git(repo, "--json").stdout)
    by_path = {Path(item["path"]): item for item in payload["worktrees"]}
    rich = _vc_git(repo).stdout

    assert by_path[merged]["integration"]["status"] == "merged"
    assert by_path[merged]["status"] == {"staged": 0, "unstaged": 1, "untracked": 0}
    assert (
        by_path[equivalent]["integration"]["status"]
        == "integrated_by_patch_equivalence"
    )
    assert by_path[unmerged]["integration"]["status"] == "unmerged"
    assert by_path[unmerged]["integration"]["unmatched_commits"]
    assert "==================== WORKTREE INTEGRATION ====================" in rich
    assert "Comparison target: HEAD" in rich
    assert "integrated by patch equivalence" in rich
    assert "WARN unmerged" in rich


def test_vc_git_does_not_call_unique_merge_patch_equivalent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    merged = tmp_path / "unique-merge"
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-q",
            "-b",
            "unique-merge",
            str(merged),
        ],
        check=True,
    )
    _commit(repo, "target.txt", "target\n")
    target = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    parent = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD^"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input="unique\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "-C", str(repo), "mktree"],
        input=f"100644 blob {blob}\tx\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    merge = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "commit-tree",
            tree,
            "-p",
            target,
            "-p",
            parent,
            "-m",
            "unique merge",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(merged), "reset", "--hard", merge], check=True)

    payload = json.loads(_vc_git(repo, "--json").stdout)
    item = next(
        entry for entry in payload["worktrees"] if Path(entry["path"]) == merged
    )
    rich = _vc_git(repo).stdout

    assert item["integration"]["status"] == "unmerged"
    assert item["integration"]["unique_merge_commits"] == [merge]
    assert "integrated by patch equivalence" not in rich
    assert "unique merge commits" in rich
    assert (
        "WARN unmerged (1 unique merge commits; patch equivalence unavailable)"
        in git.repo_full_summary(repo)
    )


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/zsh"])
def test_repo_full_runs_under_bash_and_zsh(tmp_path: Path, shell: str) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)

    rich = _repo_full(repo, shell).stdout

    assert "==================== REPO FULL ====================" in rich
    assert "Staged changes:    0" in rich


def test_vc_git_reports_failed_worktree_status_as_unknown(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    broken = tmp_path / "broken-status"
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-q",
            "-b",
            "broken-status",
            str(broken),
        ],
        check=True,
    )
    (broken / ".git").unlink()

    payload = json.loads(_vc_git(repo, "--json").stdout)
    item = next(
        entry for entry in payload["worktrees"] if Path(entry["path"]) == broken
    )
    rich = _vc_git(repo).stdout

    assert item["status"] is None
    assert "Dirt: staged unknown, unstaged unknown, untracked unknown" in rich


def test_vc_git_missing_upstream_and_detached_worktree_are_truthful(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    detached = tmp_path / "detached tree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(detached)],
        check=True,
    )

    payload = json.loads(_vc_git(repo, "--json").stdout)
    rich = _vc_git(repo).stdout

    assert payload["upstream_divergence"]["status"] == "not_configured"
    assert payload["ahead"] is None and payload["behind"] is None
    assert any(item.get("detached") is not None for item in payload["worktrees"])
    assert "Ahead:             not configured" in rich


def test_vc_git_marks_missing_worktree_root_as_unknown_not_clean(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    missing = tmp_path / "missing tree"
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-q",
            "-b",
            "missing",
            str(missing),
        ],
        check=True,
    )
    shutil.rmtree(missing)

    payload = json.loads(_vc_git(repo, "--json").stdout)
    item = next(
        entry for entry in payload["worktrees"] if Path(entry["path"]) == missing
    )

    assert item["availability"] == "missing"
    assert item["status"] is None
    assert item["integration"]["status"] == "merged"


def test_repo_full_marks_failed_upstream_probe_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    repo = tmp_path / "repo"
    _init_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True
    )
    original_git = git._git

    def fail_divergence(
        path: Path, *args: str, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        if args[:1] == ("rev-list",):
            return subprocess.CompletedProcess(["git", *args], 1, "", "probe failed")
        return original_git(path, *args, check=check)

    monkeypatch.setattr(git, "_git", fail_divergence)
    payload = git.repo_full(repo)

    assert payload["upstream_divergence"]["status"] == "unknown"
    assert payload["ahead"] is None and payload["behind"] is None
