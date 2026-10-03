from __future__ import annotations

import json
import subprocess
from dataclasses import FrozenInstanceError

import pytest
from vibecrafted_core.delivery import (
    ALLOWED_TRANSITIONS,
    ContractValidationError,
    DeliveryProofContract,
    DeliveryRecord,
    DeliverySeal,
    DeliveryState,
    ExecutionEnvelope,
    ExecutionEvidence,
    ExecutionState,
    ProofResult,
    ProofState,
    UnsupportedSchemaError,
    delivery_transition_allowed,
)


def envelope_payload() -> dict[str, object]:
    return {
        "schema": "vibecrafted.execution-envelope.v1",
        "agent": "codex",
        "repo": "vetcoders/vibecrafted",
        "root": "/repo",
        "branch": "feature/proof",
        "expected_head": "a" * 40,
        "upstream_ref": "origin/feature/proof",
        "upstream_relation": {"ahead": 1, "behind": 0},
        "dirty_policy": "living-tree-scoped",
        "baseline_status_digest": "sha256:baseline",
        "protected_paths": ["protected.py"],
        "owned_paths": ["delivery/model.py"],
        "brief_path": "/brief.md",
        "brief_sha256": "sha256:brief",
    }


def proof_payload() -> dict[str, object]:
    return {
        "schema": "vibecrafted.delivery-proof.v1",
        "id": "proof-1",
        "execution_envelope_sha256": "sha256:envelope",
        "subject": {
            "producer_id": "subject.test",
            "public_surface": "subject --run",
            "argv": ["subject", "--run"],
            "cwd": "/repo",
            "expected_exit": 0,
            "output": "/tmp/subject.json",
        },
        "witness": {
            "input": "/tmp/input.json",
            "sha256": "sha256:witness",
            "expected_outcome": "normalized-v1",
        },
        "oracle": {
            "producer_id": "oracle.test",
            "argv": ["oracle", "--run"],
            "version_probe": ["oracle", "--version"],
            "output": "/tmp/oracle.json",
        },
        "assertion": {
            "kind": "normalized-structural-equality",
            "actual": "/tmp/subject.json",
            "expected": "/tmp/oracle.json",
            "normalizer_id": "normalized-v1",
        },
        "negative_controls": [
            {
                "id": "missing-subject-output",
                "mutation": "remove_isolated_actual",
                "expected": "proof_failed",
            }
        ],
        "delivery_scope": "checkout",
        "integration_target": None,
        "runtime_probes": [],
    }


def contract_payloads() -> tuple[tuple[type[object], dict[str, object]], ...]:
    return (
        (ExecutionEnvelope, envelope_payload()),
        (DeliveryProofContract, proof_payload()),
        (
            ExecutionEvidence,
            {
                "schema": "vibecrafted.execution-evidence.v1",
                "evidence_id": "evidence-1",
                "parent_contract_id": "proof-1",
                "run_id": "run-1",
                "role": "subject",
                "argv": ["subject", "--run"],
                "cwd": "/repo",
                "environment": {"LANG": "C"},
                "resolved_executable": "/usr/bin/subject",
                "executable_version": "1.0",
                "executable_sha256": "sha256:executable",
                "started_at": "2026-07-20T10:00:00Z",
                "ended_at": "2026-07-20T10:00:01Z",
                "elapsed_ms": 1000,
                "timeout_seconds": 30.0,
                "exit_code": 0,
                "stdout_sha256": "sha256:stdout",
                "stderr_sha256": "sha256:stderr",
                "stdout_excerpt": "ok",
                "stderr_excerpt": "",
                "input_digests": {"/tmp/input": "sha256:input"},
                "output_digests": {"/tmp/output": "sha256:output"},
                "repo_before": {"head": "a" * 40},
                "repo_after": {"head": "a" * 40},
                "run_identity_sha256": "sha256:identity",
                "liveness_evidence_sha256": ["sha256:liveness"],
            },
        ),
        (
            ProofResult,
            {
                "schema": "vibecrafted.proof-result.v1",
                "proof_id": "proof-1",
                "state": "passed",
                "evidence": [{"evidence_id": "evidence-1"}],
                "assertion_results": [{"id": "assertion", "passed": True}],
                "negative_control_results": [{"id": "missing", "passed": True}],
                "subject_executed": True,
                "assertion_consumed_subject_output": True,
                "refusal_reasons": [],
                "contract_sha256": "sha256:contract",
                "executor_sha256": "sha256:executor",
                "evaluated_at": "2026-07-20T10:00:02Z",
            },
        ),
        (
            DeliveryRecord,
            {
                "schema": "vibecrafted.delivery-record.v1",
                "record_id": "record-1",
                "proof_result_sha256": "sha256:proof",
                "declared_scope": "checkout",
                "checked_scope": "checkout",
                "target_identity": {"repo": "vetcoders/vibecrafted"},
                "commit_provenance": {"head": "a" * 40},
                "runtime_probe_results": [],
                "state": "delivered",
                "refusal_reasons": [],
                "recorded_at": "2026-07-20T10:00:03Z",
            },
        ),
        (
            DeliverySeal,
            {
                "schema": "vibecrafted.delivery-seal.v1",
                "seal_id": "seal-1",
                "issued_at": "2026-07-20T10:00:04Z",
                "issuer": "vc-ship",
                "run_id": "run-1",
                "lifecycle_id": "lifecycle-1",
                "cut_id": "cut-1",
                "proof_id": "proof-1",
                "run_identity_sha256": "sha256:identity",
                "liveness_evidence_sha256": ["sha256:liveness"],
                "execution_envelope_sha256": "sha256:envelope",
                "delivery_proof_contract_sha256": "sha256:contract",
                "proof_result_sha256": "sha256:proof",
                "executor_source_sha256": "sha256:executor",
                "executor_version": "1.0",
                "subject_evidence_sha256": "sha256:subject",
                "witness_sha256": "sha256:witness",
                "oracle_evidence_sha256": "sha256:oracle",
                "assertion_evidence_sha256": "sha256:assertion",
                "negative_control_evidence_sha256": ["sha256:negative"],
                "repo": "vetcoders/vibecrafted",
                "branch": "feature/proof",
                "baseline_head": "a" * 40,
                "final_head": "b" * 40,
                "scoped_dirty_status_sha256": "sha256:dirty",
                "commit_range": "a..b",
                "declared_scope": "checkout",
                "checked_scope": "checkout",
                "runtime_probe_sha256": [],
                "report_sha256": "sha256:report",
                "transcript_sha256": "sha256:transcript",
                "control_plane_snapshot_sha256": "sha256:control-plane",
                "unverified_surfaces": [],
            },
        ),
    )


@pytest.mark.parametrize(("contract_type", "payload"), contract_payloads())
def test_contracts_are_frozen_and_round_trip(
    contract_type: type[object], payload: dict[str, object]
) -> None:
    contract = contract_type.from_payload(payload)
    assert contract.to_payload() == payload
    assert json.loads(contract.canonical_json())["schema"] == payload["schema"]
    assert contract.content_digest().startswith("sha256:")
    with pytest.raises(FrozenInstanceError):
        contract.schema = "changed"


def test_transition_tables_keep_axes_separate_and_delivery_fail_closed() -> None:
    assert (
        ExecutionState.LAUNCHED
        in ALLOWED_TRANSITIONS["execution"][ExecutionState.CREATED]
    )
    assert ProofState.PASSED in ALLOWED_TRANSITIONS["proof"][ProofState.RUNNING]
    assert (
        DeliveryState.SEALED in ALLOWED_TRANSITIONS["delivery"][DeliveryState.DELIVERED]
    )

    assert delivery_transition_allowed(
        current=DeliveryState.UNVERIFIED,
        target=DeliveryState.DELIVERED,
        execution_state=ExecutionState.EXITED,
        execution_exit_code=0,
        proof_state=ProofState.PASSED,
    )
    assert delivery_transition_allowed(
        current=DeliveryState.DELIVERED,
        target=DeliveryState.SEALED,
        execution_state=ExecutionState.EXITED,
        execution_exit_code=0,
        proof_state=ProofState.PASSED,
    )
    for terminal_failure in (
        ExecutionState.INTERRUPTED,
        ExecutionState.TIMED_OUT,
        ExecutionState.FAILED,
    ):
        assert not delivery_transition_allowed(
            current=DeliveryState.UNVERIFIED,
            target=DeliveryState.DELIVERED,
            execution_state=terminal_failure,
            execution_exit_code=None,
            proof_state=ProofState.PASSED,
        )


@pytest.mark.parametrize(
    ("payload_index", "timestamp_updates"),
    (
        (
            2,
            {
                "started_at": "2030-01-01T00:00:00Z",
                "ended_at": "2030-01-01T00:00:02Z",
            },
        ),
        (3, {"evaluated_at": "2030-01-01T00:00:03Z"}),
        (4, {"recorded_at": "2030-01-01T00:00:04Z"}),
        (5, {"issued_at": "2030-01-01T00:00:05Z"}),
    ),
)
def test_digest_is_key_order_and_event_time_invariant(
    payload_index: int, timestamp_updates: dict[str, str]
) -> None:
    contract_type, first_payload = contract_payloads()[payload_index]
    second_payload = dict(reversed(tuple(first_payload.items())))
    second_payload.update(timestamp_updates)
    first = contract_type.from_payload(first_payload)
    second = contract_type.from_payload(second_payload)
    assert first.to_payload() != second.to_payload()
    assert first.canonical_json() == second.canonical_json()
    assert first.content_digest() == second.content_digest()


def test_unknown_schema_fails_closed_with_typed_error() -> None:
    payload = proof_payload()
    payload["schema"] = "vibecrafted.delivery-proof.v999"
    with pytest.raises(UnsupportedSchemaError):
        DeliveryProofContract.from_payload(payload)


def test_subject_and_oracle_must_have_distinct_producers() -> None:
    payload = proof_payload()
    payload["oracle"] = {"producer_id": "subject.test"}
    with pytest.raises(ContractValidationError, match="distinct producer_id"):
        DeliveryProofContract.from_payload(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (("witness", {}), ("assertion", {}), ("negative_controls", [])),
)
def test_oracle_free_contract_requires_self_sufficient_proof(
    field: str, value: object
) -> None:
    payload = proof_payload()
    payload["oracle"] = None
    payload[field] = value
    with pytest.raises(ContractValidationError, match="oracle-free"):
        DeliveryProofContract.from_payload(payload)


def test_oracle_free_contract_is_legal_when_falsifiable() -> None:
    payload = proof_payload()
    payload["oracle"] = None
    contract = DeliveryProofContract.from_payload(payload)
    assert contract.oracle is None


def test_future_fixtures_are_real_and_distinct(temp_git_repo, two_producers) -> None:
    head = subprocess.run(
        ["git", "-C", str(temp_git_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subject, oracle = two_producers
    assert len(head) == 40
    assert subject["producer_id"] != oracle["producer_id"]
    for producer in (subject, oracle):
        completed = subprocess.run(
            producer["argv"],
            cwd=producer["cwd"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert completed.stdout.strip() in {"subject", "oracle"}
