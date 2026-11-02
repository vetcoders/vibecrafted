from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import git
from vibecrafted_core import git_worktrees as worktrees
from vibecrafted_core.control_plane import control_plane_home


def git_cmd(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def example(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_cmd(repo, "init", "-q", "-b", "main")
    git_cmd(repo, "config", "user.name", "Test")
    git_cmd(repo, "config", "user.email", "test@example.com")
    (repo / "file").write_text("base\n")
    (repo / ".gitignore").write_text("ignored/\n")
    git_cmd(repo, "add", ".")
    git_cmd(repo, "commit", "-qm", "base")
    wt = tmp_path / "worker tree"
    git_cmd(repo, "worktree", "add", "-qb", "worker", str(wt))
    receipt = control_plane_home() / "runtime_runs" / "run-1" / "meta.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "agent": "codex",
                "status": "completed",
                "worktree": True,
                "worktree_path": str(wt),
                "parent_root": str(repo),
                "worktree_baseline_sha": git_cmd(repo, "rev-parse", "HEAD"),
                "report": str(tmp_path / "report.md"),
            }
        )
    )
    return repo, wt, receipt


def row(payload: dict, path: Path) -> dict:
    return next(item for item in payload["worktrees"] if item["path"] == str(path))


def test_inventory_shows_receipt_and_exact_target(example, monkeypatch) -> None:
    repo, wt, _ = example
    monkeypatch.setattr(worktrees, "_holders", lambda path: "none-observed")
    inventory = worktrees.worktree_inventory(repo, target="main")
    item = row(inventory, wt)
    assert inventory["target_ref"] == "refs/heads/main"
    assert inventory["target_sha"] == git_cmd(repo, "rev-parse", "main")
    assert item["triage"] == "removable"
    assert item["owners"][0]["run_id"] == "run-1"
    assert item["owners"][0]["baseline"] == item["head"]
    assert item["owners"][0]["report"].endswith("report.md")
    assert row(inventory, repo)["triage"] == "unknown"


@pytest.mark.parametrize(
    "kind",
    [
        "untracked",
        "ignored",
        "hidden",
        "hidden-skip",
        "active",
        "no-owner",
        "holder",
        "locked",
        "bad-git",
        "wrong-parent",
        "unknown-state",
        "bad-record",
        "bad-cut",
        "bad-location",
        "active-parent",
        "invoking-cwd",
        "integrator",
        "archive",
        "default-branch",
    ],
)
def test_inventory_protects_uncertain_and_unwritten_work(
    example, monkeypatch, kind
) -> None:
    repo, wt, receipt = example
    monkeypatch.setattr(worktrees, "_holders", lambda path: "none-observed")
    if kind == "untracked":
        (wt / "private").write_text("untracked")
    elif kind == "ignored":
        (wt / "ignored").mkdir()
        (wt / "ignored" / "work").write_text("ignored work")
    elif kind in {"hidden", "hidden-skip"}:
        flag = "--assume-unchanged" if kind == "hidden" else "--skip-worktree"
        git_cmd(wt, "update-index", flag, "file")
        (wt / "file").write_text("hidden change")
    elif kind == "active":
        data = json.loads(receipt.read_text())
        data["pid"] = os.getpid()
        receipt.write_text(json.dumps(data))
    elif kind == "no-owner":
        receipt.unlink()
    elif kind == "holder":
        monkeypatch.setattr(worktrees, "_holders", lambda path: "live-open-file")
    elif kind == "locked":
        git_cmd(repo, "worktree", "lock", str(wt))
    elif kind == "bad-git":
        (wt / ".git").unlink()
    elif kind == "wrong-parent":
        data = json.loads(receipt.read_text())
        data["parent_root"] = str(wt.parent)
        receipt.write_text(json.dumps(data))
    elif kind == "unknown-state":
        data = json.loads(receipt.read_text())
        data.pop("status")
        receipt.write_text(json.dumps(data))
    elif kind == "bad-record":
        broken = receipt.parent.parent / "other" / "meta.json"
        broken.parent.mkdir()
        broken.write_text("{")
    elif kind == "bad-cut":
        broken = control_plane_home() / "dispatches" / "broken" / "receipts.json"
        broken.parent.mkdir(parents=True)
        broken.write_text(
            json.dumps(
                {
                    "schema": "vibecrafted.dispatch-receipts.v1",
                    "run_id": "broken",
                    "cuts": {"unknown": "not a cut"},
                }
            )
        )
    elif kind == "bad-location":
        broken = receipt.parent.parent / "other" / "meta.json"
        broken.parent.mkdir()
        broken.write_text(json.dumps({"worktree_path": ["not a path"]}))
    elif kind == "active-parent":
        other = receipt.parent.parent / "other" / "meta.json"
        other.parent.mkdir()
        other.write_text(
            json.dumps({"run_id": "other", "status": "running", "root": str(wt)})
        )
    elif kind == "invoking-cwd":
        monkeypatch.chdir(wt)
    elif kind == "integrator":
        dispatch = control_plane_home() / "dispatches" / "integrator" / "receipts.json"
        dispatch.parent.mkdir(parents=True)
        sha = git_cmd(repo, "rev-parse", "HEAD")
        dispatch.write_text(
            json.dumps(
                {
                    "schema": "vibecrafted.dispatch-receipts.v1",
                    "run_id": "integrator",
                    "repo_root": str(repo),
                    "cuts": {
                        "cut": {
                            "cut_id": "cut",
                            "worktree_path": str(wt),
                            "state": "settled",
                            "acceptance": "verified",
                            "baseline_sha": sha,
                            "delivered_commit_sha": sha,
                            "provider_run_id": "run-1",
                            "integrator_exclusivity": True,
                        }
                    },
                }
            )
        )
    elif kind == "archive":
        git_cmd(wt, "branch", "-m", "archive/worker")
    elif kind == "default-branch":
        git_cmd(repo, "remote", "add", "origin", str(repo))
        git_cmd(
            repo,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/worker",
        )
    plan = worktrees.worktree_inventory(repo, target="main")
    assert not [item for item in plan["worktrees"] if item["triage"] == "removable"]
    if kind == "ignored":
        assert (
            next(item for item in plan["worktrees"] if item["path"] == str(wt))[
                "triage"
            ]
            == "ignored_payload"
        )
    if kind in {
        "no-owner",
        "bad-git",
        "wrong-parent",
        "unknown-state",
        "bad-record",
        "bad-cut",
        "bad-location",
    }:
        assert (
            next(item for item in plan["worktrees"] if item["path"] == str(wt))[
                "triage"
            ]
            == "unknown"
        )
    assert wt.exists()


def test_unreceived_commit_is_not_integration(example, monkeypatch) -> None:
    repo, wt, _ = example
    monkeypatch.setattr(worktrees, "_holders", lambda path: "none-observed")
    (wt / "new").write_text("unique")
    git_cmd(wt, "add", "new")
    git_cmd(wt, "commit", "-qm", "unique")
    item = row(worktrees.worktree_inventory(repo, target="main"), wt)
    assert item["triage"] == "review_reception"
    assert item["integration"] == "not_ancestor"


@pytest.mark.parametrize("field", ["report", "transcript", "artifact_path"])
@pytest.mark.parametrize("owner", ["same", "other"])
def test_tracked_durable_paths_from_any_run_block_removal_classification(
    example, monkeypatch, field, owner
) -> None:
    repo, wt, receipt = example
    monkeypatch.setattr(worktrees, "_holders", lambda path: "none-observed")
    durable = wt / "delivery.md"
    durable.write_text("delivery")
    git_cmd(wt, "add", "delivery.md")
    git_cmd(wt, "commit", "-qm", "delivery")
    git_cmd(repo, "merge", "--ff-only", "worker")
    if owner == "same":
        data = json.loads(receipt.read_text())
        data[field] = str(durable)
        receipt.write_text(json.dumps(data))
    else:
        other = receipt.parent.parent / "other" / "meta.json"
        other.parent.mkdir()
        other.write_text(json.dumps({"run_id": "other", field: str(durable)}))
    plan = worktrees.worktree_inventory(repo, target="main")
    item = next(item for item in plan["worktrees"] if item["path"] == str(wt))
    assert item["integration"] == "exact_ancestor"
    assert str(durable) in item["durable_paths"]
    assert not [item for item in plan["worktrees"] if item["triage"] == "removable"]
    assert durable.read_text() == "delivery"


def test_patch_equivalent_commit_is_not_removal_proof(example, monkeypatch) -> None:
    repo, wt, _ = example
    monkeypatch.setattr(worktrees, "_holders", lambda path: "none-observed")
    (wt / "patch").write_text("same change")
    git_cmd(wt, "add", "patch")
    git_cmd(wt, "commit", "-qm", "worker change")
    (repo / "other").write_text("different history")
    git_cmd(repo, "add", "other")
    git_cmd(repo, "commit", "-qm", "main change")
    git_cmd(repo, "cherry-pick", "worker")
    item = row(worktrees.worktree_inventory(repo, target="main"), wt)
    assert item["integration"] == "not_ancestor"
    assert item["triage"] == "review_reception"


@pytest.mark.parametrize(
    "override", ["GIT_DIR", "GIT_INDEX_FILE", "GIT_GRAFT_FILE", "GIT_CONFIG_COUNT"]
)
def test_repository_git_overrides_are_refused(example, monkeypatch, override) -> None:
    repo, wt, _ = example
    monkeypatch.setenv(override, "1")
    with pytest.raises(
        RuntimeError, match="repository-local Git environment overrides"
    ):
        worktrees.worktree_inventory(repo, target="main")
    assert wt.exists()


@pytest.mark.parametrize("overlay", ["replace", "graft"])
def test_synthetic_git_history_cannot_fake_integration(
    example, monkeypatch, overlay
) -> None:
    repo, wt, _ = example
    monkeypatch.setattr(worktrees, "_holders", lambda path: "none-observed")
    (wt / "unique").write_text("worker work")
    git_cmd(wt, "add", "unique")
    git_cmd(wt, "commit", "-qm", "worker")
    worker = git_cmd(wt, "rev-parse", "HEAD")
    (repo / "different").write_text("target work")
    git_cmd(repo, "add", "different")
    git_cmd(repo, "commit", "-qm", "target")
    target = git_cmd(repo, "rev-parse", "HEAD")
    if overlay == "replace":
        tree = git_cmd(repo, "rev-parse", "HEAD^{tree}")
        replacement = git_cmd(
            repo, "commit-tree", tree, "-p", worker, "-m", "fake ancestry"
        )
        git_cmd(repo, "replace", target, replacement)
    else:
        common = Path(
            git_cmd(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        )
        (common / "info" / "grafts").write_text(f"{target} {worker}\n")
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", worker, target],
            cwd=repo,
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )
    item = row(worktrees.worktree_inventory(repo, target="main"), wt)
    assert item["integration"] == "not_ancestor"
    assert item["triage"] == "review_reception"


def test_dispatch_restart_is_active_even_after_settlement(example, monkeypatch) -> None:
    repo, wt, _ = example
    monkeypatch.setattr(worktrees, "_holders", lambda path: "none-observed")
    sha = git_cmd(repo, "rev-parse", "HEAD")
    receipt = control_plane_home() / "dispatches" / "dispatch-1" / "receipts.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "schema": "vibecrafted.dispatch-receipts.v1",
                "run_id": "dispatch-1",
                "repo_root": str(repo),
                "cuts": {
                    "cut-1": {
                        "cut_id": "cut-1",
                        "worktree_path": str(wt),
                        "state": "settled",
                        "acceptance": "verified",
                        "baseline_sha": sha,
                        "delivered_commit_sha": sha,
                        "provider_run_id": "run-1",
                        "integrator_exclusivity": False,
                        "settled_epoch_ns": 20,
                        "launch_admitted_epoch_ns": 30,
                    }
                },
            }
        )
    )
    item = row(worktrees.worktree_inventory(repo, target="main"), wt)
    assert item["triage"] == "working"
    assert "dispatch launch was admitted after settlement" in item["reasons"]


@pytest.mark.parametrize("verb", ["list", "triage"])
def test_cli_uses_shared_inventory(example, monkeypatch, capsys, verb) -> None:
    repo, _, _ = example
    monkeypatch.setattr(worktrees, "_holders", lambda path: "none-observed")
    assert git.main([str(repo), "--worktrees", verb, "--target", "main", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == worktrees.worktree_inventory(
        repo, target="main"
    )


def test_installed_console_entrypoint_accepts_readonly_alias(example) -> None:
    repo, wt, _ = example
    result = subprocess.run(
        [
            str(Path(sys.executable).with_name("vc-git")),
            "worktrees",
            "list",
            str(repo),
            "--target",
            "main",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["schema"] == "vibecrafted.worktree-inventory.v1"
    assert row(payload, wt)["owners"][0]["run_id"] == "run-1"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--worktrees", "clean", "--apply"],
        ["--worktrees", "prune", "--dry-run"],
        ["worktrees", "merge", "worker"],
        ["--worktrees", "list", "--apply"],
        ["--worktrees", "triage", "--remove-integrated"],
    ],
)
def test_mutating_commands_are_unavailable(example, capsys, arguments) -> None:
    _, wt, receipt = example
    with pytest.raises(SystemExit) as exc:
        git.main(arguments)
    assert exc.value.code == 2
    assert "error:" in capsys.readouterr().err
    assert wt.exists() and receipt.exists()


def test_inventory_does_not_write_git_or_owner_state(example, monkeypatch) -> None:
    repo, wt, receipt = example
    monkeypatch.setattr(worktrees, "_holders", lambda path: "none-observed")
    index = Path(
        git_cmd(wt, "rev-parse", "--path-format=absolute", "--git-path", "index")
    )
    before = (
        index.read_bytes(),
        index.stat().st_mtime_ns,
        receipt.read_bytes(),
        git_cmd(repo, "show-ref"),
        git_cmd(repo, "count-objects", "-v"),
    )
    worktrees.worktree_inventory(repo, target="main")
    assert before == (
        index.read_bytes(),
        index.stat().st_mtime_ns,
        receipt.read_bytes(),
        git_cmd(repo, "show-ref"),
        git_cmd(repo, "count-objects", "-v"),
    )


def test_real_holder_probe_observes_a_process_in_worktree(example) -> None:
    _, wt, _ = example
    child = subprocess.Popen(["sleep", "20"], cwd=wt)
    try:
        assert worktrees._holders(wt) == "live-open-file"
    finally:
        child.terminate()
        child.wait()


def test_compare_cut_names_are_not_worktree_commands(monkeypatch, capsys) -> None:
    seen = []

    def compare(project, dispatch, cuts):
        seen.append((project, dispatch, cuts))
        return {"received": True}

    monkeypatch.setattr(git, "compare_parallel_work", compare)
    assert (
        git.main(["--compare", "dispatch-1", "--cuts", "clean", "merge", "--json"]) == 0
    )
    assert seen == [(".", "dispatch-1", ["clean", "merge"])]
    assert json.loads(capsys.readouterr().out) == {"received": True}
