#!/usr/bin/env python3
"""Run a fake-transport dispatch smoke or inspect an existing dispatch receipt."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[1] / "vibecrafted-core"
sys.path.insert(0, str(CORE_ROOT))


def _git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return process.stdout.strip()


def _seed_repo(root: Path) -> str:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "agents@vetcoders.io")
    _git(root, "config", "user.name", "Vibecrafted smoke")
    _git(
        root,
        "remote",
        "add",
        "origin",
        "git@github.com:vetcoders/vibecrafted-smoke.git",
    )
    (root / ".gitignore").write_text("target/\n", encoding="utf-8")
    (root / "README.md").write_text("parallel smoke\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "smoke baseline")
    return _git(root, "rev-parse", "HEAD")


def _plan(repo: Path):
    from vibecrafted_core.dispatch.schema import parse_dispatch

    return parse_dispatch(
        f'''schema = "vibecrafted.dispatch.v1"
[meta]
name = "fake-transport-two-worker-parallel-smoke"
repo = "{repo}"
[policy]
concurrency = 2
allow_concurrency = true
require_commit = true
await = {{ poll_s = 0.02, timeout_min = 2.0 }}

[[cuts]]
id = "smoke-left"
agent = "local-smoke"
workflow = "implement"
prompt = "write the left proof"
  [[cuts.verify]]
  run = "test -f smoke-left.txt"
  expect = {{ exit_code = 0 }}

[[cuts]]
id = "smoke-right"
agent = "local-smoke"
workflow = "implement"
prompt = "write the right proof"
  [[cuts.verify]]
  run = "test -f smoke-right.txt"
  expect = {{ exit_code = 0 }}

[[cuts]]
id = "smoke-join"
agent = "local-smoke"
workflow = "implement"
integrator = true
depends_on = ["smoke-left", "smoke-right"]
prompt = "integrate both verified branches"
  [[cuts.verify]]
  run = "test -f smoke-left.txt && test -f smoke-right.txt && test -f smoke-join.txt"
  expect = {{ exit_code = 0 }}
'''
    )


def _git_check(repo: Path, *args: str) -> tuple[bool, str]:
    """Run a read-only Git query without making inspection depend on success."""
    try:
        process = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return False, str(exc)
    return process.returncode == 0, (process.stdout or process.stderr).strip()


def _receipt_path(run_id: str) -> Path:
    """Return a run ledger path without constructing a store (and therefore no mkdir)."""
    from vibecrafted_core.control_plane import control_plane_home

    candidate = Path(run_id)
    if not run_id or candidate.name != run_id or run_id in {".", ".."}:
        raise ValueError("dispatch run id must be one path component")
    return control_plane_home() / "dispatches" / run_id / "receipts.json"


def _provider_attempts(
    receipt: dict[str, object],
) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """Project provider-attempt identity without equating labels to attempts."""
    raw = receipt.get("attempts", receipt.get("launch_attempts", ()))
    attempts: list[dict[str, str]] = []
    raw_attempts = raw if isinstance(raw, list) else [raw] if raw else []
    for value in raw_attempts:
        if isinstance(value, dict):
            attempts.append(
                {
                    "attempt": str(value.get("attempt") or ""),
                    "provider_run_id": str(value.get("provider_run_id") or ""),
                }
            )
        elif value:
            attempts.append({"attempt": str(value), "provider_run_id": ""})
    if not attempts and (receipt.get("attempt") or receipt.get("provider_run_id")):
        attempts.append(
            {
                "attempt": str(receipt.get("attempt") or ""),
                "provider_run_id": str(receipt.get("provider_run_id") or ""),
            }
        )
    provider_ids = [
        item["provider_run_id"] for item in attempts if item["provider_run_id"]
    ]
    duplicates = sorted({item for item in provider_ids if provider_ids.count(item) > 1})
    missing_provider_ids = [
        item["attempt"] or "unnamed" for item in attempts if not item["provider_run_id"]
    ]
    return attempts, duplicates, missing_provider_ids


def _report_evidence(
    report_path: str, provider_run_id: str
) -> tuple[dict[str, object], list[str]]:
    """Apply the canonical report-attestation contract to one provider run."""
    from vibecrafted_core.report_contract import (
        CLAIM_BLOCKED,
        CLAIM_FAILED,
        CLAIM_PARTIAL,
        validate_report_file,
    )

    frontmatter = validate_report_file(report_path, require_frontmatter=True)
    evidence = {
        "path": report_path,
        "exists": bool(report_path) and Path(report_path).is_file(),
        "valid": frontmatter.ok,
        "finalized": frontmatter.finalized,
        "claim": frontmatter.claim,
        "run_id": frontmatter.run_id,
        "status": frontmatter.claim_status,
    }
    issues = list(frontmatter.errors)
    if not frontmatter.finalized:
        issues.append("report is not finalized")
    if not frontmatter.claim:
        issues.append("report claim missing")
    # ``finalized`` plus ``claim`` is the canonical provider-neutral positive
    # attestation.  Do not duplicate a local success-word allowlist here:
    # callers may use a valid neutral status such as ``attested``.  Known
    # negative, blocked, and in-progress canonical claims are still refusal
    # states even if their author incorrectly flips ``finalized``.
    if frontmatter.claim_status in CLAIM_FAILED | CLAIM_BLOCKED | CLAIM_PARTIAL:
        issues.append(
            f"report status is not successful: {frontmatter.claim_status or 'missing'}"
        )
    if not provider_run_id:
        issues.append("provider run identity missing")
    elif frontmatter.run_id != provider_run_id:
        issues.append("report run identity does not match provider run")
    return evidence, list(dict.fromkeys(issues))


def _worker_evidence(
    *, receipt: dict[str, object], terminal_sha: str
) -> tuple[dict[str, object], list[str]]:
    """Verify the worker root, branch and baseline independently of receipt text."""
    root = Path(str(receipt.get("worktree_path") or "")).expanduser()
    branch = str(receipt.get("branch") or "")
    baseline = str(receipt.get("baseline_sha") or "")
    evidence: dict[str, object] = {
        "root": str(root),
        "branch": branch,
        "baseline_sha": baseline,
        "terminal_sha": terminal_sha,
        "git_verified": False,
    }
    issues: list[str] = []
    if not root.is_dir():
        return evidence, ["effective root missing or not a directory"]
    ok, top_level = _git_check(root, "rev-parse", "--show-toplevel")
    if not ok:
        return evidence, [f"effective root is not a Git checkout: {top_level}"]
    if Path(top_level).resolve() != root.resolve():
        issues.append("effective root does not match Git checkout root")
    ok, observed_branch = _git_check(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch:
        issues.append("branch missing")
    elif not ok or observed_branch != branch:
        issues.append("receipt branch does not match effective root branch")
    for name, sha in (("baseline SHA", baseline), ("terminal SHA", terminal_sha)):
        valid, _detail = _git_check(
            root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"
        )
        if not sha or not valid:
            issues.append(f"{name} is not an effective-root commit")
    if not issues:
        ancestor, _detail = _git_check(
            root, "merge-base", "--is-ancestor", baseline, terminal_sha
        )
        if not ancestor:
            issues.append("baseline SHA is not an ancestor of terminal SHA")
        on_branch, _detail = _git_check(
            root, "merge-base", "--is-ancestor", terminal_sha, branch
        )
        if not on_branch:
            issues.append("terminal SHA is not reachable from receipt branch")
    evidence["git_verified"] = not issues
    return evidence, issues


def _integration_evidence(
    *, receipt: dict[str, object], repo_root: Path, terminal_sha: str
) -> dict[str, object]:
    """Verify delivery against the destination checkout, not a receipt label."""
    declared = str(receipt.get("integrated_sha") or "")
    declared_target = str(receipt.get("integration_target") or "")
    target = "HEAD"
    evidence: dict[str, object] = {
        "declared_sha": declared,
        "declared_target": declared_target,
        "target": target,
        "terminal_sha": terminal_sha,
        "git_verified": False,
    }
    if not terminal_sha:
        evidence["reason"] = "terminal SHA missing"
        return evidence
    if not str(repo_root) or str(repo_root) == "." or not repo_root.is_dir():
        evidence["reason"] = f"destination repository missing: {repo_root}"
        return evidence
    valid_commit, detail = _git_check(
        repo_root, "rev-parse", "--verify", "--quiet", f"{terminal_sha}^{{commit}}"
    )
    if not valid_commit:
        evidence["reason"] = f"terminal SHA is not a destination commit: {detail}"
        return evidence
    # Share delivery scope's exact ancestry rule; a receipt's integrated_sha is
    # informative only, while this Git relation is the independently observed fact.
    from vibecrafted_core.delivery.scope import _commit_reachable

    reachable = _commit_reachable(repo_root, terminal_sha, target)
    _ok, detail = _git_check(
        repo_root, "merge-base", "--is-ancestor", terminal_sha, target
    )
    evidence["git_verified"] = reachable
    if not reachable:
        evidence["reason"] = (
            f"terminal SHA is not reachable from destination HEAD: {detail}"
        )
    return evidence


def inspect_run(
    run_id: str, *, require_integrated: bool = False
) -> tuple[int, dict[str, object]]:
    """Read one dispatch ledger and produce a conservative machine-readable verdict.

    Inspection deliberately never creates a store: a missing run must remain missing.
    """
    try:
        ledger_path = _receipt_path(run_id)
    except ValueError as exc:
        return 2, {
            "schema": "vibecrafted.dispatch-inspection.v1",
            "run_id": run_id,
            "status": "invalid",
            "issues": [str(exc)],
        }
    if not ledger_path.is_file():
        return 2, {
            "schema": "vibecrafted.dispatch-inspection.v1",
            "run_id": run_id,
            "status": "missing",
            "receipt_path": str(ledger_path),
            "issues": ["receipt ledger not found"],
        }

    # DispatchReceiptStore remains the canonical parser/identity validator. The
    # public read() takes an exclusive lock and creates receipts.lock, which is
    # a write; the private JSON reader is safe here because ledger updates are
    # atomic replacements and inspection must leave no footprint.
    from vibecrafted_core.dispatch.receipts import (
        DispatchReceiptStore,
        ReceiptContractError,
    )

    try:
        payload = DispatchReceiptStore(run_id, (), create=False)._read_unlocked()
    except ReceiptContractError as exc:
        return 2, {
            "schema": "vibecrafted.dispatch-inspection.v1",
            "run_id": run_id,
            "status": "invalid",
            "receipt_path": str(ledger_path),
            "issues": [str(exc)],
        }

    repo_root_text = str(payload.get("repo_root") or "")
    repo_root = Path(repo_root_text).expanduser()
    issues: list[str] = []
    cuts: list[dict[str, object]] = []
    raw_cuts = payload.get("cuts")
    if not isinstance(raw_cuts, dict) or not raw_cuts:
        issues.append("receipt has no cuts")
        raw_cuts = {}
    for cut_id, raw_receipt in raw_cuts.items():
        if not isinstance(raw_receipt, dict):
            issues.append(f"{cut_id}: receipt is not an object")
            continue
        receipt = raw_receipt
        state = str(receipt.get("state") or "")
        terminal_sha = str(receipt.get("delivered_commit_sha") or "")
        report_path = str(receipt.get("report_path") or "")
        attempts, duplicate_attempts, missing_provider_ids = _provider_attempts(receipt)
        provider_run_id = str(receipt.get("provider_run_id") or "")
        report, report_issues = _report_evidence(report_path, provider_run_id)
        worker, worker_issues = _worker_evidence(
            receipt=receipt, terminal_sha=terminal_sha
        )
        verification = receipt.get("gates")
        gates = verification if isinstance(verification, list) else []
        gates_ok = bool(gates) and all(
            isinstance(gate, dict) and gate.get("ok") is True for gate in gates
        )
        integration = _integration_evidence(
            receipt=receipt, repo_root=repo_root, terminal_sha=terminal_sha
        )
        delivered = (
            state == "settled"
            and receipt.get("acceptance") == "verified"
            and not report_issues
            and not worker_issues
            and not duplicate_attempts
            and not missing_provider_ids
        )
        if state in {"launching", "active", "reported", "verified", "integrating"}:
            # Receipt state is a declaration, not an independently observed
            # liveness fact.  This read-only inspector has no process identity
            # probe, so it must not promote stale receipt text into a live-work
            # observation.
            disposition = "declared-live-unknown"
        elif state == "failed" or receipt.get("acceptance") == "failed":
            disposition = "failed"
        elif delivered and integration["git_verified"]:
            disposition = "integrated"
        elif delivered:
            disposition = "delivered-isolated"
        else:
            disposition = "incomplete"
        cut_issues: list[str] = []
        if state not in {"settled", "failed"}:
            cut_issues.append(f"state is {state or 'missing'}")
        if state == "failed" or receipt.get("acceptance") == "failed":
            cut_issues.append("cut failed")
        if receipt.get("acceptance") != "verified":
            cut_issues.append("acceptance is not verified")
        if not terminal_sha:
            cut_issues.append("terminal SHA missing")
        cut_issues.extend(worker_issues)
        cut_issues.extend(f"report: {issue}" for issue in report_issues)
        if not gates_ok:
            cut_issues.append("verification evidence missing or not fully green")
        if not attempts:
            cut_issues.append("provider attempt evidence missing")
        if missing_provider_ids:
            cut_issues.append(
                "provider run identity missing for attempts: "
                + ", ".join(missing_provider_ids)
            )
        if duplicate_attempts:
            cut_issues.append(
                "ambiguous duplicate provider attempts: "
                f"{', '.join(duplicate_attempts)}"
            )
        if require_integrated and not integration["git_verified"]:
            cut_issues.append(
                str(integration.get("reason") or "integration not proven")
            )
        issues.extend(f"{cut_id}: {issue}" for issue in cut_issues)
        cuts.append(
            {
                "cut_id": str(cut_id),
                "state": state,
                "effective_root": str(receipt.get("worktree_path") or ""),
                "branch": str(receipt.get("branch") or ""),
                "baseline_sha": str(receipt.get("baseline_sha") or ""),
                "terminal_sha": terminal_sha,
                "report": report,
                "verification": {"gates": gates, "complete": gates_ok},
                "provider_attempts": attempts,
                "provider_run_id": provider_run_id,
                "worker": worker,
                "integration": {"disposition": disposition, **integration},
            }
        )
    status = "accepted" if not issues else "incomplete"
    return (0 if status == "accepted" else 1), {
        "schema": "vibecrafted.dispatch-inspection.v1",
        "run_id": run_id,
        "status": status,
        "require_integrated": require_integrated,
        "receipt_path": str(ledger_path),
        "repo_root": repo_root_text,
        "cuts": cuts,
        "issues": issues,
    }


def smoke() -> int:
    from vibecrafted_core.dispatch.receipts import DispatchReceiptStore
    from vibecrafted_core.dispatch.supervisor import (
        CellRun,
        cleanup_settled_run,
        run_dispatch,
    )
    from vibecrafted_core.dispatch.worktrees import canonical_artifact_root

    run_id = f"dispatch-smoke-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="vibecrafted-dispatch-smoke-") as temporary:
        repo = Path(temporary) / "vibecrafted-smoke"
        baseline = _seed_repo(repo)
        dispatch = _plan(repo)
        dispatch = replace(
            dispatch,
            meta=replace(
                dispatch.meta,
                baseline={
                    "head": baseline,
                    "branch": _git(repo, "branch", "--show-current"),
                },
            ),
        )
        artifact_root = canonical_artifact_root(repo)
        reports = artifact_root / "reports" / "parallel-smoke" / run_id
        reports.mkdir(parents=True, exist_ok=True)

        def launcher(cut, _prompt: str, kind: str) -> CellRun:
            root = Path(cut.runtime_root)
            report = reports / f"{cut.id}.md"
            if cut.integrator:
                body = (
                    "git merge --quiet --no-ff --no-edit cut/smoke-left\n"
                    "git merge --quiet --no-ff --no-edit cut/smoke-right\n"
                    "printf joined > smoke-join.txt\n"
                    "git add smoke-join.txt\n"
                    'git commit --quiet -m "smoke-join local integration"\n'
                )
            else:
                marker = root / f"{cut.id}.txt"
                body = (
                    "sleep 0.5\n"
                    f"printf {shlex.quote(cut.id)} > {shlex.quote(str(marker))}\n"
                    f"git add {shlex.quote(marker.name)}\n"
                    f"git commit --quiet -m {shlex.quote(cut.id + ' local parallel smoke')}\n"
                )
            script = (
                "set -eu\n"
                + body
                + f"printf '%s\\n' {shlex.quote('completed ' + cut.id)} > {shlex.quote(str(report))}\n"
            )
            process = subprocess.Popen(["bash", "-c", script], cwd=root)
            return CellRun(
                cut_id=cut.id,
                kind=kind,
                accepted=True,
                run_id=f"local-{run_id}-{cut.id}",
                pid=process.pid,
                report_path=str(report),
                proc=process,
            )

        result = run_dispatch(
            dispatch,
            launcher=launcher,
            artifacts_dir=artifact_root / "plans" / "dispatch" / run_id,
            run_id=run_id,
            manage_worktrees=True,
        )
        store = DispatchReceiptStore(run_id, dispatch.cuts, concurrency=2)
        before_cleanup = store.read()
        left = before_cleanup["cuts"]["smoke-left"]
        right = before_cleanup["cuts"]["smoke-right"]
        join = before_cleanup["cuts"]["smoke-join"]
        sibling_overlap = max(
            left["launching_epoch_ns"], right["launching_epoch_ns"]
        ) < min(left["reported_epoch_ns"], right["reported_epoch_ns"])
        join_after_siblings = join["integrating_epoch_ns"] >= max(
            left["settled_epoch_ns"], right["settled_epoch_ns"]
        )
        cleanup = cleanup_settled_run(dispatch, run_id)
        payload = {
            "schema": "vibecrafted.fake-transport-dispatch-smoke-receipt.v1",
            "run_id": run_id,
            "result": result.to_dict(),
            "sibling_overlap": sibling_overlap,
            "join_after_siblings": join_after_siblings,
            "worker_roots_distinct": left["worktree_path"] != right["worktree_path"],
            "worker_targets_distinct": left["target_path"] != right["target_path"],
            "cleanup": cleanup,
            "receipts_before_cleanup": before_cleanup,
            "receipts_after_cleanup": store.read(),
        }
        receipt = reports / "parallel-smoke-receipt.json"
        receipt.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(receipt)
        return (
            0
            if all(
                (
                    sibling_overlap,
                    join_after_siblings,
                    payload["worker_roots_distinct"],
                    payload["worker_targets_distinct"],
                    all(state == "[x]" for state in result.states.values()),
                )
            )
            else 1
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect-run", metavar="DISPATCH_RUN_ID")
    parser.add_argument("--require-integrated", action="store_true")
    args = parser.parse_args()
    if args.require_integrated and not args.inspect_run:
        parser.error("--require-integrated requires --inspect-run")
    if args.inspect_run:
        exit_code, evidence = inspect_run(
            args.inspect_run, require_integrated=args.require_integrated
        )
        print(json.dumps(evidence, sort_keys=True))
        raise SystemExit(exit_code)
    raise SystemExit(smoke())
