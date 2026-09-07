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


def _write_report(
    path: Path,
    run_id: str,
    *,
    finalized: bool = True,
    status: str = "completed",
    claim: str = "receipt inspection proof",
) -> None:
    path.write_text(
        "---\n"
        f"run_id: {run_id}\n"
        "agent: codex\n"
        "skill: implement\n"
        f"status: {status}\n"
        f"finalized: {'true' if finalized else 'false'}\n"
        f"claim: {claim}\n"
        "---\n",
        encoding="utf-8",
    )


def _receipt(
    repo: Path, baseline: str, terminal: str, report: Path, provider_run_id: str
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
        "provider_run_id": provider_run_id,
        "gates": [{"command": "true", "ok": True, "exit_code": 0}],
    }


def test_inspector_distinguishes_isolated_delivery_from_integrated_acceptance(
    tmp_path: Path,
) -> None:
    repo, baseline, terminal = _repo(tmp_path)
    report = tmp_path / "report.md"
    _write_report(report, "provider-isolated")
    home = tmp_path / "home"
    receipt = _receipt(repo, baseline, terminal, report, "provider-isolated")
    worker = tmp_path / "worker"
    _git(repo, "worktree", "add", "-q", "-b", "worker-only", str(worker), baseline)
    (worker / "isolated").write_text("isolated", encoding="utf-8")
    _git(worker, "add", ".")
    _git(worker, "commit", "-qm", "isolated")
    receipt["worktree_path"] = str(worker)
    receipt["branch"] = "worker-only"
    receipt["delivered_commit_sha"] = _git(worker, "rev-parse", "HEAD")
    receipt["integration_target"] = "worker-only"
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

    receipt["branch"] = _git(repo, "branch", "--show-current")
    receipt["worktree_path"] = str(repo)
    receipt["delivered_commit_sha"] = terminal
    receipt["integration_target"] = "worker-only"
    _write_report(report, "provider-integrated")
    receipt["provider_run_id"] = "provider-integrated"
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
    _write_report(report, f"provider-{state}")
    receipt = _receipt(repo, baseline, terminal, report, f"provider-{state}")
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
    _write_report(report, "provider-forged")
    home = tmp_path / "home"
    receipt = _receipt(repo, baseline, "f" * 40, report, "provider-forged")
    receipt["attempts"] = [
        {"attempt": "initial", "provider_run_id": "provider-duplicate"},
        {"attempt": "repair1", "provider_run_id": "provider-duplicate"},
    ]
    path = _write_ledger(home, "forged", repo, receipt)
    before = path.read_bytes()
    result = _inspect(home, "forged")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert any("duplicate provider attempts" in issue for issue in payload["issues"])
    assert any(
        "terminal SHA is not an effective-root commit" in issue
        for issue in payload["issues"]
    )
    assert path.read_bytes() == before
    assert not (path.parent / "receipts.lock").exists()


def test_inspector_refuses_settled_pending_unfinalized_acceptance(
    tmp_path: Path,
) -> None:
    repo, baseline, terminal = _repo(tmp_path)
    report = tmp_path / "report.md"
    _write_report(report, "provider-pending", finalized=False)
    receipt = _receipt(repo, baseline, terminal, report, "provider-pending")
    receipt["acceptance"] = "pending"
    _write_ledger(tmp_path / "home", "pending", repo, receipt)

    result = _inspect(tmp_path / "home", "pending", "--require-integrated")
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["status"] == "incomplete"
    assert payload["cuts"][0]["integration"]["disposition"] == "incomplete"
    assert any("acceptance is not verified" in issue for issue in payload["issues"])
    assert any("report is not finalized" in issue for issue in payload["issues"])


@pytest.mark.parametrize(
    ("finalized", "status", "claim", "expected"),
    [
        (False, "completed", "claim", "report is not finalized"),
        (True, "failed", "claim", "report status is not successful"),
        (True, "completed", "", "report claim missing"),
    ],
)
def test_inspector_refuses_missing_or_unattested_report_evidence(
    tmp_path: Path, finalized: bool, status: str, claim: str, expected: str
) -> None:
    repo, baseline, terminal = _repo(tmp_path)
    report = tmp_path / "report.md"
    _write_report(
        report, "provider-report", finalized=finalized, status=status, claim=claim
    )
    receipt = _receipt(repo, baseline, terminal, report, "provider-report")
    _write_ledger(tmp_path / "home", "report", repo, receipt)
    result = _inspect(tmp_path / "home", "report")
    assert result.returncode == 1
    assert any(expected in issue for issue in json.loads(result.stdout)["issues"])

    receipt["report_path"] = str(tmp_path / "missing.md")
    _write_ledger(tmp_path / "home", "report-missing", repo, receipt)
    missing = _inspect(tmp_path / "home", "report-missing")
    assert missing.returncode == 1
    assert any(
        "report_missing" in issue for issue in json.loads(missing.stdout)["issues"]
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("baseline_sha", "f" * 40, "baseline SHA is not an effective-root commit"),
        ("worktree_path", "missing-root", "effective root missing or not a directory"),
        ("branch", "missing-branch", "receipt branch does not match"),
    ],
)
def test_inspector_refuses_worker_identity_mismatches(
    tmp_path: Path, field: str, value: str, expected: str
) -> None:
    repo, baseline, terminal = _repo(tmp_path)
    report = tmp_path / "report.md"
    _write_report(report, "provider-identity")
    receipt = _receipt(repo, baseline, terminal, report, "provider-identity")
    receipt[field] = value
    _write_ledger(tmp_path / "home", "identity", repo, receipt)
    result = _inspect(tmp_path / "home", "identity")
    assert result.returncode == 1
    assert any(expected in issue for issue in json.loads(result.stdout)["issues"])


def test_missing_run_does_not_create_a_ledger(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = _inspect(home, "missing")
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "missing"
    assert not (home / "control_plane" / "dispatches" / "missing").exists()
