"""``--worktree`` on the core launcher reuses the canonical WorktreeManager."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import workflow


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


def test_launch_workflow_refuses_worktree_on_dirty_or_non_root_paths(
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

    (repo / "dirty.txt").write_text("x\n", encoding="utf-8")
    dirty_spec = workflow.normalize_launch_spec(
        {
            "skill": "workflow",
            "agent": "claude",
            "prompt": "x",
            "root": str(repo),
            "worktree": True,
        },
        tmp_path,
    )
    refused = workflow.launch_workflow(dirty_spec, tmp_path)
    assert refused["accepted"] is False
    assert "clean selected workspace" in refused["error"]
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1

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
