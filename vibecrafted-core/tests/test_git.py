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
    # Pin the bare remote's HEAD to main: without -b the remote is born with
    # HEAD -> master, and newer git (2.55 on CI runners) leaves the clone on
    # master, so the diverging push never reaches origin/main (behind stays 0).
    subprocess.run(
        ["git", "init", "-q", "-b", "main", "--bare", str(remote)], check=True
    )
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
    # Pin the bare remote's HEAD to main: without -b the remote is born with
    # HEAD -> master, and newer git (2.55 on CI runners) leaves the clone on
    # master, so the diverging push never reaches origin/main (behind stays 0).
    subprocess.run(
        ["git", "init", "-q", "-b", "main", "--bare", str(remote)], check=True
    )
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


def _parallel_receipt(repo: Path, tmp_path: Path) -> Path:
    from vibecrafted_core.control_plane import control_plane_home

    _init_repo(repo)
    base = git._git_text(repo, "rev-parse", "HEAD")
    left = tmp_path / "left"
    right = tmp_path / "right"
    cuts = {}
    for name, worktree in (("left", left), ("right", right)):
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "worktree",
                "add",
                "-q",
                "-b",
                name,
                str(worktree),
            ],
            check=True,
        )
        _commit(worktree, "README.md", f"{name} delivery\n")
        cuts[name] = {
            "cut_id": name,
            "state": "settled",
            "acceptance": "verified",
            "baseline_sha": base,
            "delivered_commit_sha": git._git_text(worktree, "rev-parse", "HEAD"),
            "report_path": f"{name}.md",
            "provider_run_id": f"work-{name}",
        }
    receipt = control_plane_home() / "dispatches" / "parallel" / "receipts.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "schema": "vibecrafted.dispatch-receipts.v1",
                "run_id": "parallel",
                "repo_root": str(repo),
                "cuts": cuts,
            }
        )
    )
    return receipt


def test_compare_completed_parallel_work_from_public_cli(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    receipt = _parallel_receipt(repo, tmp_path)
    before = receipt.read_bytes()
    # Later edits/commits cannot change the delivered receipt's comparison.
    _commit(tmp_path / "left", "later.txt", "not part of delivery\n")
    (tmp_path / "right" / "README.md").write_text("unfinished later work\n")
    head = git._git_text(repo, "rev-parse", "HEAD")
    output = _vc_git(repo, "--compare", "parallel", "--cuts", "left", "right", "--json")
    payload = json.loads(output.stdout)
    assert payload["phase"] == "before-deploy"
    assert payload["deployment_authorized"] is False
    assert payload["overlapping_files"] == ["README.md"]
    assert "+left delivery" in payload["deliveries"][0]["patch"]
    assert "+right delivery" in payload["deliveries"][1]["patch"]
    assert "later.txt" not in output.stdout
    assert "unfinished later work" not in output.stdout
    assert "-left delivery" in payload["tip_diff"]
    assert "+right delivery" in payload["tip_diff"]
    rich = _vc_git(repo, "--compare", "parallel", "--cuts", "left", "right").stdout
    assert "Founder's decision" in rich
    assert receipt.read_bytes() == before
    assert git._git_text(repo, "rev-parse", "HEAD") == head


@pytest.mark.parametrize(
    "state",
    [
        "queued",
        "launching",
        "active",
        "reported",
        "verified",
        "integrating",
        "failed",
        "stopped",
    ],
)
def test_compare_refuses_work_before_both_deliveries_settle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    repo = tmp_path / "repo"
    receipt = _parallel_receipt(repo, tmp_path)
    ledger = json.loads(receipt.read_text())
    ledger["cuts"]["right"]["state"] = state
    receipt.write_text(json.dumps(ledger))
    original = git._git

    def no_early_diff(path: Path, *args: str, check: bool = False):
        assert args[0] != "diff", (
            "must not compare a finished worker while its peer is working"
        )
        return original(path, *args, check=check)

    monkeypatch.setattr(git, "_git", no_early_diff)
    assert (
        git.main([str(repo), "--compare", "parallel", "--cuts", "left", "right"]) == 2
    )


@pytest.mark.parametrize(
    "defect",
    [
        "identity",
        "schema",
        "repo",
        "acceptance",
        "baseline",
        "tip",
        "ancestry",
        "missing_cut",
        "malformed",
    ],
)
def test_compare_fails_closed_on_invalid_receipts(tmp_path: Path, defect: str) -> None:
    repo = tmp_path / "repo"
    receipt = _parallel_receipt(repo, tmp_path)
    ledger = json.loads(receipt.read_text())
    if defect == "identity":
        ledger["run_id"] = "another"
    elif defect == "schema":
        ledger["schema"] = "unknown"
    elif defect == "repo":
        other = tmp_path / "other"
        _init_repo(other)
        ledger["repo_root"] = str(other)
    elif defect == "acceptance":
        ledger["cuts"]["right"]["acceptance"] = "failed"
    elif defect == "baseline":
        ledger["cuts"]["right"]["baseline_sha"] = "HEAD"
    elif defect == "tip":
        ledger["cuts"]["right"]["delivered_commit_sha"] = "0" * 40
    elif defect == "ancestry":
        ledger["cuts"]["right"]["baseline_sha"] = ledger["cuts"]["left"][
            "delivered_commit_sha"
        ]
    elif defect == "missing_cut":
        del ledger["cuts"]["right"]
    receipt.write_text("{" if defect == "malformed" else json.dumps(ledger))
    assert (
        git.main([str(repo), "--compare", "parallel", "--cuts", "left", "right"]) == 2
    )


def test_compare_refuses_runtime_that_contradicts_settled_receipt(
    tmp_path: Path,
) -> None:
    from vibecrafted_core.control_plane import control_plane_home

    repo = tmp_path / "repo"
    _parallel_receipt(repo, tmp_path)
    runtime = control_plane_home() / "runtime_runs" / "work-right" / "meta.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(
        json.dumps({"run_id": "work-right", "status": "active", "exit_code": None})
    )
    assert (
        git.main([str(repo), "--compare", "parallel", "--cuts", "left", "right"]) == 2
    )
    runtime.write_text(
        json.dumps({"run_id": "wrong-run", "status": "completed", "exit_code": 0})
    )
    assert (
        git.main([str(repo), "--compare", "parallel", "--cuts", "left", "right"]) == 2
    )
    runtime.write_text(
        json.dumps({"run_id": "work-right", "status": "completed", "exit_code": 0})
    )
    assert (
        git.main([str(repo), "--compare", "parallel", "--cuts", "left", "right"]) == 0
    )


def test_compare_rejects_traversal_missing_receipts_and_self_comparison(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _parallel_receipt(repo, tmp_path)
    for dispatch, cuts in (
        ("../parallel", ["left", "right"]),
        ("missing", ["left", "right"]),
        ("parallel", ["left", "left"]),
    ):
        assert git.main([str(repo), "--compare", dispatch, "--cuts", *cuts]) == 2


def test_compare_refuses_receipt_drift_and_failed_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    receipt = _parallel_receipt(repo, tmp_path)
    original = git._git

    def drift(path: Path, *args: str, check: bool = False):
        result = original(path, *args, check=check)
        if args[0] == "diff":
            ledger = json.loads(receipt.read_text())
            ledger["cuts"]["right"]["state"] = "active"
            receipt.write_text(json.dumps(ledger))
        return result

    monkeypatch.setattr(git, "_git", drift)
    with pytest.raises(RuntimeError, match="changed during comparison"):
        git.compare_parallel_work(repo, "parallel", ["left", "right"])
    ledger = json.loads(receipt.read_text())
    ledger["cuts"]["right"]["state"] = "settled"
    receipt.write_text(json.dumps(ledger))

    def fail_diff(path: Path, *args: str, check: bool = False):
        if args[0] == "diff":
            return subprocess.CompletedProcess(["git", *args], 1, "", "failed")
        return original(path, *args, check=check)

    monkeypatch.setattr(git, "_git", fail_diff)
    with pytest.raises(RuntimeError, match="Git probe failed: diff"):
        git.compare_parallel_work(repo, "parallel", ["left", "right"])


def test_compare_works_through_installed_console_entrypoint(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _parallel_receipt(repo, tmp_path)
    result = subprocess.run(
        [
            str(Path(sys.executable).with_name("vc-git")),
            str(repo),
            "--compare",
            "parallel",
            "--cuts",
            "left",
            "right",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    assert json.loads(result.stdout)["overlapping_files"] == ["README.md"]


@pytest.mark.parametrize(
    "args", [["--compare", "parallel"], ["--cuts", "left", "right"]]
)
def test_compare_requires_both_options(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        git.main(args)
    assert exc.value.code == 2
