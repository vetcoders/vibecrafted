"""W6 closing smoke: ship authority, honest axes, and founding incidents."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import control_plane
from vibecrafted_core.delivery.executor import run_pipeline
from vibecrafted_core.delivery.legacy import import_verify_run
from vibecrafted_core.delivery.model import (
    DeliveryProofContract,
    DeliveryState,
    ExecutionEnvelope,
    ExecutionEvidence,
    ProofState,
)
from vibecrafted_core.delivery.proof import run_proof
from vibecrafted_core.delivery.scope import ScopeEvidence, qualify_scope
from vibecrafted_core.delivery.seal import (
    SealComponents,
    SealRefusedError,
    issue_seal,
    reconstruct_seal,
)
from vibecrafted_core.delivery.store import DeliveryStore
from vibecrafted_core.lifecycle_runner import (
    delivery_axes_for_receipt,
    write_lifecycle_report,
)
from vibecrafted_core.ship import SHIP_SEAL_LAYOUT, seal_delivery_run

FIXTURES = Path(__file__).parent / "fixtures"
ZERO = "sha256:" + "0" * 64


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _copy_producers(repo: Path) -> tuple[Path, Path, Path, Path]:
    source = FIXTURES / "false_oracle"
    copied = []
    for name in ("subject.py", "oracle.py", "compare.py", "oracle-golden.txt"):
        target = repo / name
        shutil.copy2(source / name, target)
        copied.append(target)
    subprocess.run(
        ["git", "-C", str(repo), "add", *(path.name for path in copied)], check=True
    )
    staged = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--quiet"], check=False
    )
    if staged.returncode != 0:
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "add proof producers"],
            check=True,
        )
    return copied[0], copied[1], copied[2], copied[3]


def _contract(
    repo: Path, *, false_oracle: bool
) -> tuple[DeliveryProofContract, Path, Path, Path]:
    subject_script, oracle_script, compare_script, golden = _copy_producers(repo)
    subject_output = repo / "subject.out"
    oracle_output = repo / "oracle.out"
    subject = {
        "producer_id": "fixture.subject",
        "public_surface": sys.executable,
        "argv": [sys.executable, str(subject_script), str(subject_output)],
        "cwd": str(repo),
        "expected_exit": 0,
        "output": str(subject_output),
    }
    oracle = {
        "producer_id": "fixture.oracle",
        "public_surface": sys.executable,
        "argv": [sys.executable, str(oracle_script), str(oracle_output)],
        "cwd": str(repo),
        "expected_exit": 0,
        "output": str(oracle_output),
    }
    actual = oracle_output if false_oracle else subject_output
    expected = golden if false_oracle else oracle_output
    contract = DeliveryProofContract(
        schema=DeliveryProofContract.SCHEMA,
        id="dpk-w6-false" if false_oracle else "dpk-w6-correct",
        execution_envelope_sha256=ZERO,
        subject=subject,
        witness={
            "input": str(repo / "witness.txt"),
            "expected_outcome": "two distinct producers agree",
        },
        oracle=oracle,
        assertion={
            "id": "producer-equality",
            "kind": "normalized-structural-equality",
            "actual": str(actual),
            "expected": str(expected),
            "verifier_config": str(compare_script),
        },
        negative_controls=({"id": "corrupt", "mutation": "corrupt_isolated_actual"},),
        delivery_scope="checkout",
        integration_target=None,
        runtime_probes=(),
    )
    return contract, subject_output, oracle_output, golden


def _envelope(repo: Path, contract_id: str) -> ExecutionEnvelope:
    head = _head(repo)
    return ExecutionEnvelope(
        schema=ExecutionEnvelope.SCHEMA,
        agent="codex",
        repo="vetcoders/vibecrafted",
        root=str(repo),
        branch="master",
        expected_head=head,
        upstream_ref="origin/master",
        upstream_relation={"ahead": 0, "behind": 0},
        dirty_policy="living-tree-scoped",
        baseline_status_digest=ZERO,
        protected_paths=(),
        owned_paths=("subject.py", "oracle.py"),
        brief_path=f"/{contract_id}.md",
        brief_sha256=ZERO,
    )


def _persist_run(
    run_dir: Path,
    repo: Path,
    envelope: ExecutionEnvelope,
    contract: DeliveryProofContract,
    proof: Any,
) -> DeliveryStore:
    store = DeliveryStore(run_dir)
    store.write_execution_envelope(envelope)
    store.write_proof_contract(contract)
    sequence_by_role: dict[str, int] = {}
    for payload in proof.evidence:
        evidence = ExecutionEvidence.from_payload(payload)
        sequence_by_role[evidence.role] = sequence_by_role.get(evidence.role, 0) + 1
        store.write_execution(
            evidence, role=evidence.role, sequence=sequence_by_role[evidence.role]
        )
    store.write_assertions(
        proof.assertion_results,
        source_digests={"proof": proof.content_digest()},
    )
    store.write_negative_controls(
        proof.negative_control_results or ({"id": "not-run", "valid": False},),
        source_digests={"proof": proof.content_digest()},
    )
    store.write_proof_result(proof)
    head = _head(repo)
    record = qualify_scope(
        proof,
        contract,
        ScopeEvidence(
            repo=envelope.repo,
            repo_root=str(repo),
            branch=envelope.branch,
            baseline_head=head,
            final_head=head,
            commit_range=f"{head}..{head}",
            artifact_ok=True,
        ),
    )
    store.write_delivery_record(record)
    (run_dir / "report.md").write_text("proof report\n", encoding="utf-8")
    (run_dir / "transcript.log").write_text("proof transcript\n", encoding="utf-8")
    (run_dir / "control-plane-snapshot.json").write_text(
        json.dumps({"run_id": "run-w6", "status": "completed"}) + "\n",
        encoding="utf-8",
    )
    return store


def _event_collector(events: list[dict[str, Any]]):
    def collect(
        kind: str, run_id: str, message: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        event = {
            "kind": kind,
            "run_id": run_id,
            "message": message,
            "payload": payload,
        }
        events.append(event)
        return event

    return collect


def test_e2e_ship_refuses_false_verifier_and_seals_correct_proof(
    temp_git_repo: Path, tmp_path: Path
) -> None:
    correct_contract, *_ = _contract(temp_git_repo, false_oracle=False)
    correct_proof = run_proof(correct_contract, run_id="run-correct")
    assert correct_proof.state is ProofState.PASSED, correct_proof.refusal_reasons
    correct_dir = tmp_path / "correct-run"
    correct_store = _persist_run(
        correct_dir,
        temp_git_repo,
        _envelope(temp_git_repo, correct_contract.id),
        correct_contract,
        correct_proof,
    )
    assert correct_store.read_delivery_record().state is DeliveryState.DELIVERED
    direct_axes = control_plane._delivery_axes_from_run_dir(
        correct_dir, legacy_state="completed", exit_code=0
    )
    assert direct_axes.proof_state is ProofState.PASSED
    assert direct_axes.delivery_state is DeliveryState.UNVERIFIED
    assert not (correct_dir / "delivery-seal.json").exists()

    events: list[dict[str, Any]] = []
    granted = seal_delivery_run(
        correct_dir,
        run_id="run-correct",
        lifecycle_id="life-w6",
        cut_id="dpk-w6-e2e",
        event_sink=_event_collector(events),
    )
    assert granted.delivery_state is DeliveryState.SEALED
    assert granted.seal is not None
    first_seal_id = granted.seal.seal_id
    repeated = seal_delivery_run(
        correct_dir,
        run_id="run-correct",
        lifecycle_id="life-w6",
        cut_id="dpk-w6-e2e",
        event_sink=_event_collector(events),
    )
    assert repeated.seal is not None
    assert repeated.seal.seal_id == first_seal_id
    reconstructed = reconstruct_seal(correct_dir, layout=SHIP_SEAL_LAYOUT)
    assert reconstructed.verified is True, reconstructed.mismatches
    assert events[-1]["kind"] == "delivery.sealed"
    print(f"SEAL GRANTED {first_seal_id} reconstruction=verified")

    false_repo = tmp_path / "false-repo"
    shutil.copytree(temp_git_repo, false_repo)
    false_contract, subject_output, oracle_output, golden = _contract(
        false_repo, false_oracle=True
    )
    false_proof = run_proof(false_contract, run_id="run-false")
    assert false_proof.state is ProofState.INVALID
    assert "proof.invalid: subject output not consumed" in false_proof.refusal_reasons
    subject_output.write_text("corrupted subject\n", encoding="utf-8")
    negative_control = subprocess.run(
        [
            sys.executable,
            str(false_repo / "compare.py"),
            str(oracle_output),
            str(golden),
        ],
        check=False,
    )
    assert negative_control.returncode == 0
    false_dir = tmp_path / "false-run"
    false_store = _persist_run(
        false_dir,
        false_repo,
        _envelope(false_repo, false_contract.id),
        false_contract,
        false_proof,
    )
    assert false_store.read_delivery_record().state is DeliveryState.UNVERIFIED
    refused = seal_delivery_run(
        false_dir,
        run_id="run-false",
        lifecycle_id="life-w6",
        cut_id="dpk-w6-e2e",
        event_sink=_event_collector(events),
    )
    assert refused.delivery_state is DeliveryState.UNVERIFIED
    assert refused.seal is None
    assert not (false_dir / "delivery-seal.json").exists()
    refusal = json.loads((false_dir / "delivery-seal-refusal.json").read_text())
    assert "subject output not consumed" in refusal["reason"]
    assert events[-1]["kind"] == "delivery.seal_refused"
    print(f"SEAL REFUSED {refusal['reason']}")


def test_t04_masked_exit_fixture_preserves_both_segment_codes(tmp_path: Path) -> None:
    fixture = FIXTURES / "masked_exit"
    result = run_pipeline(
        [
            [sys.executable, str(fixture / "producer.py")],
            [sys.executable, str(fixture / "filter.py")],
        ],
        cwd=tmp_path,
        parent_contract_id="dpk-w6-t04",
        run_id="run-masked-exit",
        timeout_seconds=5,
    )
    assert result.segment_exit_codes == (101, 0)
    assert tuple(item.exit_code for item in result.evidences) == (101, 0)
    assert result.succeeded is False
    assert "segment 0 exited 101" in (result.failure_reason or "")


def test_legacy_verify_run_is_unqualified_and_cannot_issue_seal(tmp_path: Path) -> None:
    imported = import_verify_run("pytest -q")
    assert imported["qualification"] == "unqualified"
    assert imported["proof_state"] == "undeclared"
    assert imported["delivery_state"] == "unverified"
    assert imported["seal_eligible"] is False

    contract = DeliveryProofContract(
        schema=DeliveryProofContract.SCHEMA,
        id="legacy",
        execution_envelope_sha256=ZERO,
        subject={"producer_id": "legacy", "public_surface": "/bin/true"},
        witness={"input": "legacy", "expected_outcome": "legacy command exits"},
        oracle=None,
        assertion=imported,
        negative_controls=({"id": "required", "mutation": "remove_isolated_actual"},),
        delivery_scope="checkout",
        integration_target=None,
        runtime_probes=(),
    )
    record = qualify_scope(
        None,
        contract,
        ScopeEvidence(
            repo="repo",
            repo_root=str(tmp_path),
            branch="main",
            baseline_head="a" * 40,
            final_head="a" * 40,
            commit_range="a..a",
            artifact_ok=True,
        ),
    )
    assert record.state is DeliveryState.UNVERIFIED
    with pytest.raises(SealRefusedError):
        issue_seal(
            record,
            issuer="vc-ship",
            components=SealComponents(
                run_id="legacy",
                lifecycle_id="legacy",
                cut_id="legacy",
                proof_id="legacy",
                run_identity_sha256=ZERO,
                liveness_evidence_sha256=(),
                execution_envelope_sha256=ZERO,
                delivery_proof_contract_sha256=ZERO,
                proof_result_sha256=ZERO,
                executor_source_sha256=ZERO,
                executor_version="legacy",
                subject_evidence_sha256=ZERO,
                witness_sha256=ZERO,
                oracle_evidence_sha256=None,
                assertion_evidence_sha256=ZERO,
                negative_control_evidence_sha256=(ZERO,),
                repo="repo",
                branch="main",
                baseline_head="a" * 40,
                final_head="a" * 40,
                scoped_dirty_status_sha256=ZERO,
                commit_range="a..a",
            ),
        )


def test_completed_receipts_are_explicitly_unverified(tmp_path: Path) -> None:
    axes = delivery_axes_for_receipt("completed")
    assert axes == {
        "execution_state": "exited",
        "proof_state": "undeclared",
        "delivery_state": "unverified",
    }
    report = tmp_path / "receipt.md"
    write_lifecycle_report(
        report,
        {
            "run_id": "life-legacy",
            "workflow": "vc-ship",
            "status": "completed",
            "agent": "codex",
            "root": str(tmp_path),
            "context_atlas": {"ok": True},
            "stages": [],
        },
    )
    text = report.read_text(encoding="utf-8")
    assert "- status: completed" in text
    assert "- execution_state: exited" in text
    assert "- proof_state: undeclared" in text
    assert "- delivery_state: unverified" in text


def test_quota_exhaustion_is_a_user_policy_interruption_not_provider_failure() -> None:
    assert delivery_axes_for_receipt("quota_exhausted", {"exit_code": 75}) == {
        "execution_state": "interrupted",
        "proof_state": "undeclared",
        "delivery_state": "unverified",
    }
