"""Deterministic proof execution and verifier qualification.

The proof engine turns execution evidence into one of four terminal proof
states.  Product failure (a red subject or assertion) is kept separate from an
invalid verifier (tautology or a green negative control) and from stale input
evidence (relevant-path drift during the run).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .executor import ExecutionResult, run_evidence
from .model import DeliveryProofContract, ProofResult, ProofState

ENGINE_VERSION = "vibecrafted.proof-engine.v1"

_SUBJECT_NOT_CONSUMED = "proof.invalid: subject output not consumed"
_CONTROL_STAYED_GREEN = (
    "proof.invalid: verifier did not detect the controlled falsehood"
)


def run_proof(
    contract: DeliveryProofContract,
    *,
    run_id: str,
    relevant_paths: Sequence[str | os.PathLike[str]] = (),
    observed_paths: Sequence[str | os.PathLike[str]] = (),
    after_assertion: Callable[[], None] | None = None,
) -> ProofResult:
    """Execute and qualify one delivery proof contract.

    ``after_assertion`` is a deterministic test/embedding seam executed while
    the proof is still live.  It permits a caller to model concurrent Living
    Tree edits without races or sleeping background tasks.
    """

    contract_digest = contract.content_digest()
    engine_digest = _engine_digest()
    cwd = _contract_cwd(contract)
    inferred_relevant = _infer_relevant_paths(contract, cwd)
    relevant = _unique_paths((*inferred_relevant, *relevant_paths), cwd)
    observed = _unique_paths((*relevant, *observed_paths), cwd)
    before = _capture_paths(observed)

    evidences: list[Mapping[str, Any]] = []
    assertion_results: list[Mapping[str, Any]] = []
    control_results: list[Mapping[str, Any]] = []
    refusals: list[str] = []
    subject_executed = False
    subject_consumed = False
    invalid = False
    failed = False

    public_surface_error = _validate_public_surface(contract.subject)
    if public_surface_error is not None:
        refusals.append(f"proof.invalid: {public_surface_error}")
        invalid = True
        subject_result = None
    else:
        subject_result = _run_role(
            "subject", contract.subject, contract, run_id, cwd, contract.witness
        )
        evidences.append(subject_result.evidence.to_payload())
        subject_executed = subject_result.spawned

    oracle_result: ExecutionResult | None = None
    if contract.oracle is not None:
        oracle_surface_error = _validate_public_surface(contract.oracle)
        if oracle_surface_error is not None:
            refusals.append(f"proof.invalid: oracle {oracle_surface_error}")
            invalid = True
        else:
            oracle_result = _run_role(
                "oracle", contract.oracle, contract, run_id, cwd, contract.witness
            )
            evidences.append(oracle_result.evidence.to_payload())

    actual = _read_assertion_actual(contract.assertion, cwd, subject_result)
    expected = _read_assertion_expected(contract.assertion, cwd)

    if subject_result is not None:
        if _assertion_consumed_subject_output(subject_result, actual):
            subject_consumed = True
        elif (
            _execution_matches_contract(contract.subject, subject_result)
            and actual.missing
        ):
            refusals.append("proof.failed: subject output missing")
            failed = True
        else:
            refusals.append(_SUBJECT_NOT_CONSUMED)
            invalid = True

        vacuous_reason = _vacuous_reason(contract.assertion, actual.data)
        if vacuous_reason is not None:
            refusals.append(f"proof.failed: vacuous ({vacuous_reason})")
            failed = True
        elif not _execution_matches_contract(contract.subject, subject_result):
            refusals.append(
                "proof.failed: subject execution failed"
                + _failure_suffix(subject_result)
            )
            failed = True

    if (
        oracle_result is not None
        and contract.oracle is not None
        and not _execution_matches_contract(contract.oracle, oracle_result)
    ):
        refusals.append(
            "proof.failed: oracle execution failed" + _failure_suffix(oracle_result)
        )
        failed = True

    assertion_outcome = _evaluate_assertion(
        contract.assertion,
        actual=actual,
        expected=expected,
        subject_result=subject_result,
    )
    assertion_results.append(assertion_outcome)
    if assertion_outcome["valid"] is False:
        refusals.append(f"proof.invalid: {assertion_outcome['reason']}")
        invalid = True
    elif assertion_outcome["passed"] is False and not failed:
        refusals.append(
            f"proof.failed: assertion failed ({assertion_outcome['reason']})"
        )
        failed = True

    if not contract.negative_controls:
        refusals.append("proof.invalid: material assertion has no negative control")
        invalid = True
    elif subject_consumed and actual.data is not None:
        for control in contract.negative_controls:
            result = _run_negative_control(
                control,
                assertion=contract.assertion,
                actual=actual,
                expected=expected,
                subject_result=subject_result,
                oracle=contract.oracle,
                cwd=cwd,
            )
            control_results.append(result)
            if result["valid"] is False:
                refusals.append(f"proof.invalid: {result['reason']}")
                invalid = True
            elif result["detected_falsehood"] is False:
                refusals.append(_CONTROL_STAYED_GREEN)
                invalid = True

    if after_assertion is not None:
        after_assertion()

    after = _capture_paths(observed)
    relevant_drift = _changed_paths(before, after, frozenset(relevant))
    concurrent_drift = _changed_paths(
        before, after, frozenset(observed) - frozenset(relevant)
    )
    assertion_results.append(
        {
            "kind": "relevant_path_digests",
            "before": {str(path): before[path] for path in relevant},
            "after": {str(path): after[path] for path in relevant},
            "stable": not relevant_drift,
        }
    )
    if concurrent_drift:
        assertion_results.append(
            {
                "kind": "concurrent_drift",
                "paths": tuple(str(path) for path in concurrent_drift),
                "policy": "living-tree-scoped",
                "result_unaffected": True,
            }
        )

    if relevant_drift:
        state = ProofState.STALE
        refusals.append("proof.stale: relevant inputs changed during proof")
        assertion_results.append(
            {
                "kind": "invalidating_drift",
                "paths": tuple(str(path) for path in relevant_drift),
                "result_unaffected": False,
            }
        )
    elif invalid:
        state = ProofState.INVALID
    elif failed:
        state = ProofState.FAILED
    else:
        state = ProofState.PASSED

    return ProofResult(
        schema=ProofResult.SCHEMA,
        proof_id=contract.id,
        state=state,
        evidence=tuple(evidences),
        assertion_results=tuple(assertion_results),
        negative_control_results=tuple(control_results),
        subject_executed=subject_executed,
        assertion_consumed_subject_output=subject_consumed,
        refusal_reasons=tuple(dict.fromkeys(refusals)),
        contract_sha256=contract_digest,
        executor_sha256=engine_digest,
        evaluated_at=datetime.now(UTC).isoformat(timespec="milliseconds"),
    )


class _Artifact:
    """A loaded (or missing) evidence artifact: raw bytes, digest, source label."""

    def __init__(
        self,
        *,
        data: bytes | None,
        digest: str | None,
        source: str,
        missing: bool,
    ) -> None:
        self.data = data
        self.digest = digest
        self.source = source
        self.missing = missing


def _run_role(
    role: str,
    declaration: Mapping[str, Any],
    contract: DeliveryProofContract,
    run_id: str,
    default_cwd: Path,
    witness: Mapping[str, Any],
) -> ExecutionResult:
    """Run one declared role (subject or oracle) and return its execution evidence."""
    role_cwd = Path(str(declaration.get("cwd", default_cwd))).expanduser().resolve()
    argv = _declared_argv(declaration)
    inputs = [str(witness["input"])] if witness.get("input") else []
    outputs = [str(declaration["output"])] if declaration.get("output") else []
    roots = _allowed_roots(role_cwd, (*inputs, *outputs))
    return run_evidence(
        role,
        argv,
        cwd=role_cwd,
        parent_contract_id=contract.id,
        run_id=run_id,
        input_paths=inputs,
        output_paths=outputs,
        allowed_roots=roots,
        timeout_seconds=float(declaration.get("timeout_seconds", 600.0)),
    )


def _declared_argv(declaration: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the explicit ``argv`` if present, else shlex-split ``public_surface``."""
    argv = declaration.get("argv")
    if isinstance(argv, Sequence) and not isinstance(argv, (str, bytes)):
        return tuple(str(item) for item in argv)
    public_surface = declaration.get("public_surface")
    if isinstance(public_surface, str):
        return tuple(shlex.split(public_surface))
    return ()


def _validate_public_surface(declaration: Mapping[str, Any]) -> str | None:
    """Return an error string unless argv's first token matches the declared public_surface."""
    public_surface = declaration.get("public_surface")
    argv = _declared_argv(declaration)
    if not isinstance(public_surface, str) or not public_surface.strip():
        return "subject public_surface is not declared"
    public_argv = tuple(shlex.split(public_surface))
    if not argv or not public_argv or argv[0] != public_argv[0]:
        return "argv does not use the declared public_surface"
    return None


def _contract_cwd(contract: DeliveryProofContract) -> Path:
    """Return the resolved working directory declared by the contract's subject."""
    value = contract.subject.get("cwd") or os.getcwd()
    return Path(str(value)).expanduser().resolve()


def _allowed_roots(cwd: Path, paths: Sequence[str]) -> tuple[Path, ...]:
    """Return cwd plus the parent directory of every declared input/output path."""
    roots = {cwd}
    for raw in paths:
        path = Path(raw).expanduser()
        resolved = (cwd / path if not path.is_absolute() else path).resolve()
        roots.add(resolved.parent)
    return tuple(sorted(roots, key=str))


def _read_assertion_actual(
    assertion: Mapping[str, Any],
    cwd: Path,
    subject_result: ExecutionResult | None,
) -> _Artifact:
    """Load the assertion's ``actual`` artifact: subject stdout or a declared file path."""
    actual = assertion.get("actual")
    if actual == "subject.stdout":
        if subject_result is None:
            return _Artifact(
                data=None, digest=None, source="subject.stdout", missing=True
            )
        data = subject_result.evidence.stdout_excerpt.encode()
        return _Artifact(
            data=data,
            digest=subject_result.evidence.stdout_sha256,
            source="subject.stdout",
            missing=False,
        )
    return _read_path_artifact(actual, cwd, "actual")


def _read_assertion_expected(assertion: Mapping[str, Any], cwd: Path) -> _Artifact:
    """Load the assertion's ``expected`` file artifact, if declared."""
    expected = assertion.get("expected")
    if expected is None:
        return _Artifact(data=None, digest=None, source="undeclared", missing=True)
    return _read_path_artifact(expected, cwd, "expected")


def _read_path_artifact(value: Any, cwd: Path, label: str) -> _Artifact:
    """Read a file path (relative to cwd if not absolute) into an _Artifact."""
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        return _Artifact(data=None, digest=None, source=label, missing=True)
    raw = Path(value).expanduser()
    path = (cwd / raw if not raw.is_absolute() else raw).resolve()
    if not path.is_file():
        return _Artifact(data=None, digest=None, source=str(path), missing=True)
    data = path.read_bytes()
    return _Artifact(data=data, digest=_sha256(data), source=str(path), missing=False)


def _assertion_consumed_subject_output(
    result: ExecutionResult, actual: _Artifact
) -> bool:
    """Return whether the assertion's ``actual`` digest matches evidence the subject
    actually produced (its recorded stdout digest or a recorded output digest).
    """
    if actual.digest is None:
        return False
    if actual.source == "subject.stdout":
        return actual.digest == result.evidence.stdout_sha256
    recorded = result.evidence.output_digests.get(actual.source)
    return recorded is not None and recorded != "missing" and actual.digest == recorded


def _evaluate_assertion(
    assertion: Mapping[str, Any],
    *,
    actual: _Artifact,
    expected: _Artifact,
    subject_result: ExecutionResult | None,
) -> dict[str, Any]:
    """Evaluate one assertion kind (equality/exists/stdout-contract) against actual/expected.

    Returns a result dict with ``valid`` (was the check itself well-formed) and
    ``passed`` (did the assertion hold) kept as separate axes.
    """
    kind = str(assertion.get("kind", ""))
    base: dict[str, Any] = {
        "id": str(assertion.get("id", "assertion")),
        "kind": kind,
        "actual_digest": actual.digest,
        "expected_digest": expected.digest,
        "actual_source": actual.source,
    }
    if kind in {"normalized-structural-equality", "file-digest-equality"}:
        if actual.data is None or expected.data is None:
            return {
                **base,
                "passed": False,
                "valid": True,
                "reason": "artifact missing",
            }
        if actual.source == expected.source:
            return {
                **base,
                "passed": False,
                "valid": False,
                "reason": "assertion compares actual with itself",
            }
        if kind == "normalized-structural-equality":
            actual_value = _normalized_value(actual.data)
            expected_value = _normalized_value(expected.data)
            passed = actual_value == expected_value
        else:
            passed = actual.digest == expected.digest
        return {
            **base,
            "passed": passed,
            "valid": True,
            "reason": "equal" if passed else "artifacts differ",
        }
    if kind == "exists":
        passed = actual.data is not None
        return {
            **base,
            "passed": passed,
            "valid": True,
            "reason": "actual exists" if passed else "actual missing",
        }
    if kind == "stdout-contract":
        text = (actual.data or b"").decode("utf-8", errors="replace")
        required = tuple(str(item) for item in assertion.get("required_patterns", ()))
        forbidden = tuple(str(item) for item in assertion.get("forbidden_patterns", ()))
        expected_exit = int(assertion.get("expected_exit", 0))
        exit_code = subject_result.evidence.exit_code if subject_result else None
        missing = tuple(item for item in required if item not in text)
        present = tuple(item for item in forbidden if item in text)
        passed = exit_code == expected_exit and not missing and not present
        reason = "stdout contract matched" if passed else "stdout contract mismatch"
        return {
            **base,
            "passed": passed,
            "valid": True,
            "reason": reason,
            "exit_code": exit_code,
            "missing_patterns": missing,
            "forbidden_patterns_seen": present,
        }
    return {
        **base,
        "passed": False,
        "valid": False,
        "reason": f"unknown assertion kind {kind!r}",
    }


def _run_negative_control(
    control: Mapping[str, Any],
    *,
    assertion: Mapping[str, Any],
    actual: _Artifact,
    expected: _Artifact,
    subject_result: ExecutionResult | None,
    oracle: Mapping[str, Any] | None,
    cwd: Path,
) -> dict[str, Any]:
    """Apply one declared mutation to an isolated copy of ``actual`` and re-assert.

    The control passes (``detected_falsehood: True``) only if the mutated copy
    now fails the assertion — proving the verifier is not tautologically green.
    """
    control_id = str(control.get("id", "negative-control"))
    mutation = str(control.get("mutation", ""))
    with tempfile.TemporaryDirectory(prefix="vibecrafted-proof-control-") as temp:
        isolated = Path(temp)
        actual_path = isolated / "actual"
        expected_path = isolated / "expected"
        actual_path.write_bytes(actual.data or b"")
        if expected.data is not None:
            expected_path.write_bytes(expected.data)

        valid = True
        reason = "controlled falsehood detected"
        if mutation == "remove_isolated_actual":
            actual_path.unlink()
        elif mutation == "corrupt_isolated_actual":
            with actual_path.open("ab") as stream:
                stream.write(b"\nVIBECRAFTED_CONTROLLED_FALSEHOOD\n")
        elif mutation == "replace_actual_with_unrelated_oracle_output":
            replacement = _replacement_bytes(control, oracle, cwd)
            if replacement in {actual.data, expected.data}:
                replacement = f"unrelated-oracle-control:{control_id}".encode()
            actual_path.write_bytes(replacement)
        else:
            valid = False
            reason = f"unknown negative-control mutation {mutation!r}"

        isolated_actual = _read_path_artifact(actual_path, isolated, "isolated-actual")
        isolated_expected = _read_path_artifact(
            expected_path, isolated, "isolated-expected"
        )
        outcome = _evaluate_assertion(
            assertion,
            actual=isolated_actual,
            expected=isolated_expected,
            subject_result=subject_result,
        )
        detected = valid and outcome["valid"] is True and outcome["passed"] is False
        if valid and not detected:
            reason = "verifier stayed green"
        return {
            "id": control_id,
            "mutation": mutation,
            "isolated": True,
            "valid": valid and outcome["valid"] is True,
            "detected_falsehood": detected,
            "assertion_result": outcome,
            "reason": reason,
        }


def _replacement_bytes(
    control: Mapping[str, Any], oracle: Mapping[str, Any] | None, cwd: Path
) -> bytes:
    """Resolve replacement bytes for the unrelated-oracle-output negative control.

    Falls back to a fixed sentinel string when no replacement/oracle path is declared
    or the declared path does not resolve to a real file.
    """
    raw = control.get("replacement")
    if raw is None and oracle is not None:
        raw = oracle.get("unrelated_output")
    if isinstance(raw, str):
        path = Path(raw).expanduser()
        resolved = (cwd / path if not path.is_absolute() else path).resolve()
        if resolved.is_file():
            return resolved.read_bytes()
    return b"VIBECRAFTED_UNRELATED_ORACLE_OUTPUT\n"


def _normalized_value(data: bytes) -> Any:
    """Parse bytes as JSON for structural comparison; return raw bytes if not JSON."""
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data


def _vacuous_reason(assertion: Mapping[str, Any], data: bytes | None) -> str | None:
    """Return a reason string if ``data`` matches a declared vacuous_patterns entry."""
    text = (data or b"").decode("utf-8", errors="replace")
    for pattern in assertion.get("vacuous_patterns", ()):
        if str(pattern) in text:
            return f"matched declared pattern {pattern!r}"
    return None


def _infer_relevant_paths(
    contract: DeliveryProofContract, cwd: Path
) -> tuple[Path, ...]:
    """Infer the paths whose drift during proof execution would invalidate the result.

    Includes this module itself plus resolved subject/oracle executables, file
    arguments, witness input, and verifier_config — anything the proof depends on.
    """
    candidates: list[str | os.PathLike[str]] = [Path(__file__).resolve()]
    for declaration in (contract.subject, contract.oracle):
        if declaration is None:
            continue
        argv = _declared_argv(declaration)
        if argv:
            resolved_executable = (
                str(Path(argv[0]).expanduser().resolve())
                if os.sep in argv[0]
                else shutil.which(argv[0])
            )
            if resolved_executable is not None:
                candidates.append(resolved_executable)
            for argument in argv[1:]:
                candidate = Path(argument).expanduser()
                resolved = (
                    candidate
                    if candidate.is_absolute()
                    else Path(str(declaration.get("cwd", cwd))) / candidate
                ).resolve()
                if resolved.is_file():
                    candidates.append(resolved)
        public_surface = declaration.get("public_surface")
        if isinstance(public_surface, str) and os.sep in public_surface:
            candidates.append(shlex.split(public_surface)[0])
    witness = contract.witness.get("input")
    if isinstance(witness, str):
        candidates.append(witness)
    verifier_config = contract.assertion.get("verifier_config")
    if isinstance(verifier_config, str):
        candidates.append(verifier_config)
    elif isinstance(verifier_config, Sequence):
        candidates.extend(str(item) for item in verifier_config)
    return _unique_paths(candidates, cwd)


def _unique_paths(
    paths: Sequence[str | os.PathLike[str]], cwd: Path
) -> tuple[Path, ...]:
    """Resolve each path against ``cwd``, dedupe, and return in sorted order."""
    normalized: dict[str, Path] = {}
    for item in paths:
        path = Path(item).expanduser()
        resolved = (cwd / path if not path.is_absolute() else path).resolve()
        normalized[str(resolved)] = resolved
    return tuple(normalized[key] for key in sorted(normalized))


def _capture_paths(paths: Sequence[Path]) -> dict[Path, str]:
    """Return {path: sha256-or-"missing"} snapshots for drift detection before/after a proof."""
    return {
        path: _sha256(path.read_bytes()) if path.is_file() else "missing"
        for path in paths
    }


def _changed_paths(
    before: Mapping[Path, str], after: Mapping[Path, str], selected: frozenset[Path]
) -> tuple[Path, ...]:
    """Return the sorted subset of ``selected`` whose digest differs between snapshots."""
    return tuple(
        path
        for path in sorted(selected, key=str)
        if before.get(path) != after.get(path)
    )


def _failure_suffix(result: ExecutionResult) -> str:
    """Return a parenthesized failure_reason suffix for a message, or an empty string."""
    if result.failure_reason:
        return f" ({result.failure_reason})"
    return ""


def _execution_matches_contract(
    declaration: Mapping[str, Any], result: ExecutionResult
) -> bool:
    """Return whether a run spawned, did not time out or hit output limits, and matched
    the declaration's expected_exit code."""
    expected_exit = int(declaration.get("expected_exit", 0))
    return (
        result.spawned
        and not result.timed_out
        and not result.output_limit_exceeded
        and result.evidence.exit_code == expected_exit
    )


def _engine_digest() -> str:
    """Return a digest binding ENGINE_VERSION to the source of this module and executor.py."""
    digest = hashlib.sha256()
    digest.update(ENGINE_VERSION.encode())
    digest.update(Path(__file__).read_bytes())
    digest.update((Path(__file__).with_name("executor.py")).read_bytes())
    return f"sha256:{digest.hexdigest()}"


def _sha256(data: bytes) -> str:
    """Return the ``sha256:<hex>`` digest of an in-memory byte string."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"
