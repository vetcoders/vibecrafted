"""Black-box contract tests for the dispatch receipt inspection mode."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "smoke-dispatch-worktrees.py"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "base").write_text("base", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    baseline = _git(repo, "rev-parse", "HEAD")
    (repo / "delivered").write_text("delivered", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "delivered")
    return repo, baseline, _git(repo, "rev-parse", "HEAD")


def _write_ledger(
    home: Path, run_id: str, repo: Path, receipt: dict[str, object]
) -> Path:
    path = home / "control_plane" / "dispatches" / run_id / "receipts.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema": "vibecrafted.dispatch-receipts.v1",
                "run_id": run_id,
                "repo_root": str(repo),
                "cuts": {"cut": receipt},
            }
        ),
        encoding="utf-8",
    )
    return path


def _inspect(home: Path, run_id: str, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "VIBECRAFTED_HOME": str(home), "PYTHONPATH": ""}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--inspect-run", run_id, *args],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


def _receipt(
    repo: Path, baseline: str, terminal: str, report: Path
) -> dict[str, object]:
    return {
        "state": "settled",
        "acceptance": "verified",
        "worktree_path": str(repo),
        "branch": "main",
        "baseline_sha": baseline,
        "delivered_commit_sha": terminal,
        "report_path": str(report),
        "attempt": "initial",
        "gates": [{"command": "true", "ok": True, "exit_code": 0}],
    }


def test_inspector_distinguishes_isolated_delivery_from_integrated_acceptance(
    tmp_path: Path,
) -> None:
    repo, baseline, terminal = _repo(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("verified", encoding="utf-8")
    home = tmp_path / "home"
    receipt = _receipt(repo, baseline, terminal, report)
    receipt["integration_target"] = "refs/heads/missing-target"
    _write_ledger(home, "isolated", repo, receipt)

    observed = _inspect(home, "isolated")
    assert observed.returncode == 0
    assert (
        json.loads(observed.stdout)["cuts"][0]["integration"]["disposition"]
        == "delivered-isolated"
    )
    required = _inspect(home, "isolated", "--require-integrated")
    assert required.returncode == 1
    assert "not reachable" in json.loads(required.stdout)["issues"][0]

    receipt.pop("integration_target")
    _write_ledger(home, "integrated", repo, receipt)
    accepted = _inspect(home, "integrated", "--require-integrated")
    assert accepted.returncode == 0
    assert (
        json.loads(accepted.stdout)["cuts"][0]["integration"]["disposition"]
        == "integrated"
    )


@pytest.mark.parametrize("state", ["active", "failed"])
def test_inspector_refuses_live_or_failed_cuts(tmp_path: Path, state: str) -> None:
    repo, baseline, terminal = _repo(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("verified", encoding="utf-8")
    receipt = _receipt(repo, baseline, terminal, report)
    receipt["state"] = state
    if state == "failed":
        receipt["acceptance"] = "failed"
    _write_ledger(tmp_path / "home", state, repo, receipt)
    result = _inspect(tmp_path / "home", state)
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["cuts"][0]["integration"]["disposition"] == (
        "observed-live-work" if state == "active" else "failed"
    )


def test_inspector_refuses_forged_or_ambiguous_receipts_without_writing(
    tmp_path: Path,
) -> None:
    repo, baseline, _terminal = _repo(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("verified", encoding="utf-8")
    home = tmp_path / "home"
    receipt = _receipt(repo, baseline, "f" * 40, report)
    receipt["attempts"] = ["initial", "initial"]
    path = _write_ledger(home, "forged", repo, receipt)
    before = path.read_bytes()
    result = _inspect(home, "forged", "--require-integrated")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert any("duplicate attempts" in issue for issue in payload["issues"])
    assert any("not a destination commit" in issue for issue in payload["issues"])
    assert path.read_bytes() == before
    assert not (path.parent / "receipts.lock").exists()


def test_missing_run_does_not_create_a_ledger(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = _inspect(home, "missing")
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "missing"
    assert not (home / "control_plane" / "dispatches" / "missing").exists()
