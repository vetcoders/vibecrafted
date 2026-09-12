"""Real Git/worktree falsifier for G3 descendant-head baseline selection."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from vibecrafted_core.dispatch.model import Cut
from vibecrafted_core.dispatch.receipts import DispatchReceiptStore
from vibecrafted_core.dispatch.supervisor import select_live_descendant_head
from vibecrafted_core.dispatch.worktrees import WorktreeManager


def _cut(cut_id: str) -> Cut:
    return Cut(
        id=cut_id,
        phase="p0",
        agent="codex",
        workflow="implement",
        resolved_workflow="implement",
    )


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def test_fail_g3_real_worktree_records_descendant_head_not_silent_drift(
    tmp_path: Path, monkeypatch
) -> None:
    """Frozen receipt SHA drifts to HEAD only when HEAD is a descendant.

    Source requirement is local-069/071. The selected SHA is recorded on the
    worker receipt with the planned SHA and reason — not a helper-only assert.
    """
    home = tmp_path / "vc-home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "agents@vetcoders.io")
    _git(repo, "config", "user.name", "g3-baseline")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed")
    frozen = _git(repo, "rev-parse", "HEAD")

    (repo / "fix.txt").write_text("between-cut fix\n", encoding="utf-8")
    _git(repo, "add", "fix.txt")
    _git(repo, "commit", "-q", "-m", "living-tree advance")
    live_head = _git(repo, "rev-parse", "HEAD")
    assert live_head != frozen
    assert _is_ancestor(repo, frozen, live_head)

    selection = select_live_descendant_head(
        frozen,
        live_head,
        is_ancestor=lambda ancestor, descendant: _is_ancestor(
            repo, ancestor, descendant
        ),
    )
    assert selection.planned == frozen
    assert selection.selected == live_head
    assert selection.reason == "live_descendant_head"
    assert "local-069" in selection.source_requirement

    manager = WorktreeManager(repo, day="2026_0908")
    geometry = manager.prepare("w4-t16", selection.selected)
    worktree = Path(geometry.worktree_path)
    assert worktree.is_dir()
    assert _git(worktree, "rev-parse", "HEAD") == live_head
    assert (worktree / "fix.txt").read_text(encoding="utf-8") == "between-cut fix\n"

    receipts = tmp_path / "receipts"
    store = DispatchReceiptStore(
        "disp-g3-baseline",
        (_cut("w4-t16"),),
        root=receipts,
        repo_root=str(repo),
    )
    store.update(
        "w4-t16",
        baseline_sha=geometry.baseline_sha,
        planned_baseline_sha=selection.planned,
        baseline_selection_reason=selection.reason,
        baseline_source_requirement=selection.source_requirement,
        worktree_path=geometry.worktree_path,
    )
    receipt = store.cut("w4-t16")
    assert receipt["baseline_sha"] == live_head
    assert receipt["planned_baseline_sha"] == frozen
    assert receipt["baseline_selection_reason"] == "live_descendant_head"
    assert "local-069" in str(receipt["baseline_source_requirement"])

    unrelated_repo = tmp_path / "unrelated"
    unrelated_repo.mkdir()
    _git(unrelated_repo, "init", "-q")
    _git(unrelated_repo, "config", "user.email", "agents@vetcoders.io")
    _git(unrelated_repo, "config", "user.name", "g3-baseline")
    (unrelated_repo / "other.txt").write_text("other\n", encoding="utf-8")
    _git(unrelated_repo, "add", "other.txt")
    _git(unrelated_repo, "commit", "-q", "-m", "unrelated")
    unrelated = _git(unrelated_repo, "rev-parse", "HEAD")
    kept = select_live_descendant_head(
        frozen,
        unrelated,
        is_ancestor=lambda ancestor, descendant: _is_ancestor(
            repo, ancestor, descendant
        ),
    )
    assert kept.selected == frozen
    assert kept.reason == "frozen_baseline_kept_head_not_descendant"
    frozen_geometry = manager.prepare("frozen-cut", kept.selected)
    assert _git(Path(frozen_geometry.worktree_path), "rev-parse", "HEAD") == frozen
    assert not (Path(frozen_geometry.worktree_path) / "fix.txt").exists()


def test_fail_g3_receipt_json_roundtrip_exposes_selected_baseline(
    tmp_path: Path,
) -> None:
    """Worker receipt on disk carries planned vs selected, not only geometry SHA."""
    receipts = tmp_path / "receipts"
    store = DispatchReceiptStore(
        "disp-g3-receipt-json",
        (_cut("cut-a"),),
        root=receipts,
    )
    store.update(
        "cut-a",
        baseline_sha="bb" * 20,
        planned_baseline_sha="aa" * 20,
        baseline_selection_reason="live_descendant_head",
        baseline_source_requirement="local-069/071",
    )
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    entry = payload["cuts"]["cut-a"]
    assert entry["planned_baseline_sha"] == "aa" * 20
    assert entry["baseline_sha"] == "bb" * 20
    assert entry["baseline_selection_reason"] == "live_descendant_head"
