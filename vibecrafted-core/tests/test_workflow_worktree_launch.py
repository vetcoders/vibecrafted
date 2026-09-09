"""``--worktree`` on the core launcher reuses the canonical WorktreeManager."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from vibecrafted_core import workflow
from vibecrafted_core.dispatch.worktrees import WorktreeContractError, WorktreeManager


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _repo(path: Path) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "agents@vetcoders.io")
    _git(path, "config", "user.name", "worktree-test")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    return _git(path, "rev-parse", "HEAD")


def _stub_worker(monkeypatch: pytest.MonkeyPatch, cwd_file: Path) -> None:
    monkeypatch.setattr(
        workflow,
        "_stdin_command",
        lambda _agent: [
            sys.executable,
            "-c",
            (
                "import os, sys; from pathlib import Path; "
                "sys.stdin.read(); "
                f"Path({str(cwd_file)!r}).write_text(os.getcwd()); "
                "Path(os.environ['VIBECRAFTED_REPORT_PATH']).write_text('ok\\n')"
            ),
        ],
    )
    monkeypatch.setattr(
        workflow, "_resolve_agent_command", lambda _agent, command, _env: list(command)
    )


def test_normalize_launch_spec_parses_worktree_words(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = workflow.normalize_launch_spec(
        {
            "skill": "workflow",
            "agent": "claude",
            "prompt": "go",
            "root": str(repo),
            "worktree": "yes",
        },
        tmp_path,
    )
    assert spec.worktree is True
    assert spec.parent_root == ""
    assert (
        workflow.normalize_launch_spec(
            {
                "skill": "workflow",
                "agent": "claude",
                "prompt": "go",
                "root": str(repo),
                "worktree": "off",
            },
            tmp_path,
        ).worktree
        is False
    )
    assert "worktree" in spec.to_payload()


def test_launch_workflow_prepares_a_linked_checkout_and_records_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_GUARD", "0")
    repo = tmp_path / "repo with space"
    baseline = _repo(repo)
    (tmp_path / "src").mkdir()
    cwd_file = tmp_path / "worker-cwd.txt"
    _stub_worker(monkeypatch, cwd_file)
    spec = workflow.normalize_launch_spec(
        {
            "skill": "workflow",
            "agent": "claude",
            "prompt": "isolated cut",
            "root": str(repo),
            "worktree": True,
        },
        tmp_path / "src",
    )

    payload = workflow.launch_workflow(spec, tmp_path / "src")

    assert payload["accepted"] is True, payload
    run_id = payload["run_id"]
    worktree = Path(payload["root"])
    assert payload["worktree"] is True
    assert payload["worktree_path"] == str(worktree)
    assert payload["parent_root"] == str(repo.resolve())
    assert payload["worktree_branch"] == f"cut/claude-{run_id}"
    assert payload["worktree_baseline_sha"] == baseline
    assert home in worktree.parents
    assert _git(worktree, "rev-parse", "HEAD") == baseline
    assert _git(worktree, "branch", "--show-current") == f"cut/claude-{run_id}"
    assert _git(repo, "branch", "--show-current") == _git(
        repo, "branch", "--show-current"
    )
    listing = _git(repo, "worktree", "list", "--porcelain")
    assert f"worktree {worktree}" in listing
    meta = json.loads(Path(payload["meta"]).read_text(encoding="utf-8"))
    assert meta["worktree"] is True
    assert meta["root"] == str(worktree)
    assert meta["parent_root"] == str(repo.resolve())
    assert meta["worktree_baseline_sha"] == baseline
    assert (
        workflow.machine_launch_receipt(payload)["worktree_branch"]
        == f"cut/claude-{run_id}"
    )
    # The dispatcher argv carries the checkout, so the worker really runs there.
    dispatch = payload["dispatch_command"]
    assert dispatch[dispatch.index("--root") + 1] == str(worktree)
    import time

    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline and not cwd_file.exists():
        time.sleep(0.2)
    assert cwd_file.exists(), Path(payload["transcript"]).read_text(
        encoding="utf-8", errors="replace"
    )[-2000:]
    assert Path(cwd_file.read_text(encoding="utf-8")).resolve() == worktree.resolve()


def test_launch_workflow_refuses_worktree_on_non_root_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_GUARD", "0")
    repo = tmp_path / "repo"
    _repo(repo)
    (repo / "sub").mkdir()
    _stub_worker(monkeypatch, tmp_path / "unused.txt")

    sub_spec = workflow.normalize_launch_spec(
        {
            "skill": "workflow",
            "agent": "claude",
            "prompt": "x",
            "root": str(repo / "sub"),
            "worktree": True,
        },
        tmp_path,
    )
    refused = workflow.launch_workflow(sub_spec, tmp_path)
    assert refused["accepted"] is False
    assert refused["reason"] == "worktree_rejected"
    assert "subdirectory" in refused["error"]
    assert not (home / "control_plane" / "runtime_runs").exists()

    plain = tmp_path / "plain"
    plain.mkdir()
    plain_spec = workflow.normalize_launch_spec(
        {
            "skill": "workflow",
            "agent": "claude",
            "prompt": "x",
            "root": str(plain),
            "worktree": True,
        },
        tmp_path,
    )
    refused = workflow.launch_workflow(plain_spec, tmp_path)
    assert refused["accepted"] is False
    assert "requires a Git repository" in refused["error"]


def test_normalize_launch_spec_worktree_needs_a_root() -> None:
    """Without any root (and no source-dir fallback) a worktree request is refused."""
    with pytest.raises(ValueError, match="--worktree requires a selected repository"):
        workflow.normalize_launch_spec(
            {
                "skill": "workflow",
                "agent": "claude",
                "prompt": "x",
                "root": "",
                "worktree": True,
            },
            "",
        )


@pytest.mark.parametrize("dirty", ["staged", "unstaged", "untracked", "combined"])
def test_pinned_checkout_preserves_dirty_parent(tmp_path, dirty):
    repo = tmp_path / "żółć repo"
    baseline = _repo(repo)
    if dirty in {"staged", "combined"}:
        (repo / "README.md").write_bytes(b"staged\n")
        _git(repo, "add", "README.md")
    if dirty in {"unstaged", "combined"}:
        (repo / "README.md").write_bytes(b"unstaged\n")
    if dirty in {"untracked", "combined"}:
        (repo / "private.txt").write_bytes(b"private parent data\n")
    before = {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}
    index = (repo / ".git/index").read_bytes()
    head = _git(repo, "rev-parse", "HEAD")
    spec = workflow.normalize_launch_spec(
        {
            "skill": "workflow",
            "agent": "codex",
            "prompt": "go",
            "root": str(repo),
            "worktree": True,
        },
        tmp_path,
    )
    prepared, receipt = workflow._prepare_launch_worktree(spec, "dirty-parent")
    worker = Path(prepared.root)
    assert _git(worker, "rev-parse", "HEAD") == baseline == head
    assert (worker / "README.md").read_bytes() == b"seed\n"
    assert not (worker / "private.txt").exists()
    assert _git(worker, "status", "--porcelain") == ""
    assert receipt["worktree_baseline_sha"] == baseline
    assert before == {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}
    assert (repo / ".git/index").read_bytes() == index
    assert _git(repo, "rev-parse", "HEAD") == head


def test_new_checkout_refuses_existing_branch_without_touching_it(tmp_path):
    repo = tmp_path / "repo"
    baseline = _repo(repo)
    _git(repo, "branch", "cut/collision")
    manager = WorktreeManager(repo)
    with pytest.raises(WorktreeContractError, match="existing branch"):
        manager.prepare("collision", baseline)
    assert _git(repo, "rev-parse", "cut/collision") == baseline
    assert not (manager.worktree_root / "collision").exists()


def test_checkout_uses_pinned_commit_after_parent_moves(tmp_path):
    repo = tmp_path / "repo"
    baseline = _repo(repo)
    (repo / "README.md").write_text("later\n")
    _git(repo, "commit", "-qam", "later unpushed commit")
    geometry = WorktreeManager(repo).prepare_agent_launch("codex", "pinned", baseline)
    assert _git(Path(geometry.worktree_path), "rev-parse", "HEAD") == baseline
    assert _git(repo, "rev-parse", "HEAD") != baseline


def test_new_checkout_rejects_noncommit_and_unpinned_refs(tmp_path):
    repo = tmp_path / "repo"
    baseline = _repo(repo)
    manager = WorktreeManager(repo)
    for invalid in [
        "HEAD",
        "missing",
        baseline[:8],
        _git(repo, "rev-parse", "HEAD^{tree}"),
    ]:
        with pytest.raises(WorktreeContractError, match="baseline"):
            manager.prepare("invalid", invalid)
        assert not (manager.worktree_root / "invalid").exists()


def test_reuse_checks_repository_and_baseline(tmp_path):
    repo = tmp_path / "repo"
    baseline = _repo(repo)
    manager = WorktreeManager(repo)
    geometry = manager.prepare("owned", baseline)
    foreign = tmp_path / "foreign"
    _repo(foreign)
    _git(foreign, "branch", "-m", geometry.branch)
    with pytest.raises(WorktreeContractError, match="owned|registered"):
        manager.validate(replace(geometry, worktree_path=str(foreign)))
    (repo / "later").write_text("later\n")
    _git(repo, "add", "later")
    _git(repo, "commit", "-qm", "later")
    with pytest.raises(WorktreeContractError, match="baseline"):
        manager.validate(
            replace(geometry, baseline_sha=_git(repo, "rev-parse", "HEAD"))
        )


def test_concurrent_duplicate_checkout_has_one_winner(tmp_path):
    repo = tmp_path / "repo"
    baseline = _repo(repo)
    manager = WorktreeManager(repo)

    def prepare(_):
        try:
            return manager.prepare("same-cut", baseline)
        except WorktreeContractError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(prepare, range(2)))
    assert sum(result is not None for result in results) == 1
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 2


def test_public_cli_launches_dirty_parent_in_committed_checkout(
    monkeypatch, tmp_path, capsys
):
    import time

    from vibecrafted_core.cli import main

    repo = tmp_path / "public żółć repo"
    baseline = _repo(repo)
    (repo / "private.txt").write_bytes(b"must stay in parent\n")
    cwd_file = tmp_path / "provider-cwd"
    _stub_worker(monkeypatch, cwd_file)
    monkeypatch.setenv("VIBECRAFTED_GUARD", "0")
    assert (
        main(
            [
                "workflow",
                "claude",
                "--repo",
                str(repo),
                "--worktree",
                "true",
                "--runtime",
                "headless",
                "--prompt",
                "literal żółć\n'quoted' ",
                "--json",
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["accepted"] is True
    worker = Path(receipt["worktree_path"])
    assert receipt["worktree_baseline_sha"] == baseline
    deadline = time.monotonic() + 30
    while not cwd_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert cwd_file.exists()
    assert Path(cwd_file.read_text()).resolve() == worker.resolve()
    assert not (worker / "private.txt").exists()
    assert (repo / "private.txt").read_bytes() == b"must stay in parent\n"
