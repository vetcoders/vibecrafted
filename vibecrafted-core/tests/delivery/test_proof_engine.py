from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from vibecrafted_core.delivery.model import DeliveryProofContract, ProofState
from vibecrafted_core.delivery.proof import run_proof


def _producer(
    declaration: dict[str, object], output: Path, payload: str
) -> dict[str, object]:
    script = Path(str(declaration["public_surface"]))
    script.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{payload}' > \"$1\"\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | 0o111)
    return {
        **declaration,
        "argv": [str(script), str(output)],
        "output": str(output),
        "cwd": str(output.parent),
    }


def _contract(
    tmp_path: Path,
    two_producers: tuple[dict[str, object], dict[str, object]],
    *,
    assertion: dict[str, Any] | None = None,
    controls: list[dict[str, str]] | None = None,
) -> DeliveryProofContract:
    subject_decl, oracle_decl = two_producers
    subject_output = tmp_path / "subject.out"
    oracle_output = tmp_path / "oracle.out"
    unrelated = tmp_path / "unrelated.out"
    unrelated.write_text("unrelated\n", encoding="utf-8")
    witness = tmp_path / "witness.txt"
    witness.write_text("proof witness\n", encoding="utf-8")
    subject = _producer(subject_decl, subject_output, "shared value")
    oracle = {
        **_producer(oracle_decl, oracle_output, "shared value"),
        "unrelated_output": str(unrelated),
    }
    payload = {
        "schema": DeliveryProofContract.SCHEMA,
        "id": "proof-engine-test",
        "execution_envelope_sha256": "sha256:envelope",
        "subject": subject,
        "witness": {
            "input": str(witness),
            "sha256": "sha256:witness",
            "expected_outcome": "equal producer outputs",
        },
        "oracle": oracle,
        "assertion": assertion
        or {
            "kind": "normalized-structural-equality",
            "actual": str(subject_output),
            "expected": str(oracle_output),
        },
        "negative_controls": controls
        if controls is not None
        else [
            {"id": "remove", "mutation": "remove_isolated_actual"},
            {"id": "corrupt", "mutation": "corrupt_isolated_actual"},
            {
                "id": "oracle-substitution",
                "mutation": "replace_actual_with_unrelated_oracle_output",
            },
        ],
        "delivery_scope": "checkout",
        "integration_target": None,
        "runtime_probes": [],
    }
    return DeliveryProofContract.from_payload(payload)


def test_happy_path_qualifies_two_real_distinct_producers(
    tmp_path: Path,
    two_producers: tuple[dict[str, object], dict[str, object]],
) -> None:
    contract = _contract(tmp_path, two_producers)

    result = run_proof(contract, run_id="run-happy")

    assert result.state is ProofState.PASSED
    assert result.subject_executed is True
    assert result.assertion_consumed_subject_output is True
    assert len(result.evidence) == 2
    assert result.assertion_results[0]["passed"] is True
    assert len(result.negative_control_results) == 3
    assert all(
        item["detected_falsehood"] is True for item in result.negative_control_results
    )
    assert all(item["isolated"] is True for item in result.negative_control_results)
    assert result.contract_sha256 == contract.content_digest()
    assert result.executor_sha256.startswith("sha256:")
    path_digests = next(
        item
        for item in result.assertion_results
        if item["kind"] == "relevant_path_digests"
    )
    assert path_digests["stable"] is True
    assert (tmp_path / "subject.out").read_text() == "shared value\n"


def test_t02_oracle_compared_with_itself_is_invalid_subject_not_consumed(
    tmp_path: Path,
    two_producers: tuple[dict[str, object], dict[str, object]],
) -> None:
    oracle_output = tmp_path / "oracle.out"
    contract = _contract(
        tmp_path,
        two_producers,
        assertion={
            "kind": "normalized-structural-equality",
            "actual": str(oracle_output),
            "expected": str(oracle_output),
        },
    )
    oracle_script = Path(str(contract.oracle["argv"][0]))
    oracle_script.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'oracle-only value' > \"$1\"\n",
        encoding="utf-8",
    )
    oracle_script.chmod(oracle_script.stat().st_mode | 0o111)

    result = run_proof(contract, run_id="run-t02")

    assert result.state is ProofState.INVALID
    assert result.assertion_consumed_subject_output is False
    assert "proof.invalid: subject output not consumed" in result.refusal_reasons


def test_t05_t06_and_oracle_substitution_controls_all_make_assertion_red(
    tmp_path: Path,
    two_producers: tuple[dict[str, object], dict[str, object]],
) -> None:
    contract = _contract(tmp_path, two_producers)

    result = run_proof(contract, run_id="run-controls")

    by_mutation = {item["mutation"]: item for item in result.negative_control_results}
    assert set(by_mutation) == {
        "remove_isolated_actual",
        "corrupt_isolated_actual",
        "replace_actual_with_unrelated_oracle_output",
    }
    assert all(item["detected_falsehood"] is True for item in by_mutation.values())


def test_t07_green_negative_control_is_terminal_invalid_not_warning(
    tmp_path: Path,
    two_producers: tuple[dict[str, object], dict[str, object]],
) -> None:
    contract = _contract(
        tmp_path,
        two_producers,
        assertion={
            "kind": "exists",
            "actual": str(tmp_path / "subject.out"),
        },
        controls=[{"id": "corrupt", "mutation": "corrupt_isolated_actual"}],
    )

    result = run_proof(contract, run_id="run-green-control")

    assert result.state is ProofState.INVALID
    assert "proof.invalid: verifier did not detect the controlled falsehood" in (
        result.refusal_reasons
    )
    assert not hasattr(result, "warnings")


def test_t10_relevant_path_drift_makes_proof_stale(
    tmp_path: Path,
    two_producers: tuple[dict[str, object], dict[str, object]],
) -> None:
    contract = _contract(tmp_path, two_producers)
    witness = Path(str(contract.witness["input"]))

    result = run_proof(
        contract,
        run_id="run-stale",
        after_assertion=lambda: witness.write_text("changed during proof\n"),
    )

    assert result.state is ProofState.STALE
    assert "proof.stale: relevant inputs changed during proof" in result.refusal_reasons
    drift = next(
        item
        for item in result.assertion_results
        if item["kind"] == "invalidating_drift"
    )
    assert str(witness.resolve()) in drift["paths"]


def test_t11_unrelated_drift_is_recorded_without_changing_pass(
    tmp_path: Path,
    two_producers: tuple[dict[str, object], dict[str, object]],
) -> None:
    contract = _contract(tmp_path, two_producers)
    unrelated = tmp_path / "concurrent.md"
    unrelated.write_text("before\n", encoding="utf-8")

    result = run_proof(
        contract,
        run_id="run-concurrent",
        observed_paths=[unrelated],
        after_assertion=lambda: unrelated.write_text("after\n", encoding="utf-8"),
    )

    assert result.state is ProofState.PASSED
    drift = next(
        item for item in result.assertion_results if item["kind"] == "concurrent_drift"
    )
    assert drift["result_unaffected"] is True
    assert str(unrelated.resolve()) in drift["paths"]


def test_t21_zero_test_collection_is_failed_as_declared_vacuous(
    tmp_path: Path,
    two_producers: tuple[dict[str, object], dict[str, object]],
) -> None:
    test_file = tmp_path / "test_sample.py"
    test_file.write_text("def test_sample():\n    assert True\n", encoding="utf-8")
    witness = tmp_path / "witness.txt"
    witness.write_text("pytest collection witness\n", encoding="utf-8")
    subject = {
        "producer_id": "pytest.real",
        "public_surface": sys.executable,
        "argv": [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            "-q",
            "-k",
            "definitely_no_such_test",
        ],
        "cwd": str(tmp_path),
        "expected_exit": 0,
    }
    payload = {
        "schema": DeliveryProofContract.SCHEMA,
        "id": "proof-vacuous",
        "execution_envelope_sha256": "sha256:envelope",
        "subject": subject,
        "witness": {
            "input": str(witness),
            "expected_outcome": "at least one test collected",
        },
        "oracle": None,
        "assertion": {
            "kind": "stdout-contract",
            "actual": "subject.stdout",
            "expected_exit": 0,
            "vacuous_patterns": ["deselected", "no tests ran", "collected 0 items"],
        },
        "negative_controls": [{"id": "corrupt", "mutation": "corrupt_isolated_actual"}],
        "delivery_scope": "checkout",
        "integration_target": None,
        "runtime_probes": [],
    }
    contract = DeliveryProofContract.from_payload(payload)

    result = run_proof(contract, run_id="run-vacuous")

    assert result.state is ProofState.FAILED
    assert result.subject_executed is True
    assert result.assertion_consumed_subject_output is True
    assert any(
        reason.startswith("proof.failed: vacuous") for reason in result.refusal_reasons
    )
