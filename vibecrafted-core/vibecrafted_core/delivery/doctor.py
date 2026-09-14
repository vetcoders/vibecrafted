"""Validate ExecutionEnvelope and DeliveryProofContract payloads (delivery-doctor CLI)."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .model import (
    ContractValidationError,
    DeliveryProofContract,
    ExecutionEnvelope,
    UnsupportedSchemaError,
)


@dataclass(frozen=True)
class DoctorError:
    """One diagnostic finding: a dotted payload path plus a human-readable message."""

    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """Return the JSON-serializable representation of this error."""
        return {"path": self.path, "message": self.message}


@dataclass(frozen=True)
class DoctorReport:
    """Outcome of diagnosing one envelope/contract pair: pass/fail plus errors."""

    ok: bool
    errors: tuple[DoctorError, ...]
    envelope: ExecutionEnvelope | None = None
    contract: DeliveryProofContract | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serializable representation of this report (errors only)."""
        return {
            "ok": self.ok,
            "errors": [error.to_dict() for error in self.errors],
        }


def diagnose_payload(
    envelope_payload: Mapping[str, Any] | None,
    contract_payload: Mapping[str, Any] | None,
) -> DoctorReport:
    """Validate an execution envelope and a delivery proof contract independently.

    Collects field-level errors before attempting model construction, so a
    ``from_payload`` failure never masks the more specific field diagnostics
    already found (including the oracle/subject producer_id tautology check).
    """
    errors: list[DoctorError] = []
    envelope_obj: ExecutionEnvelope | None = None
    contract_obj: DeliveryProofContract | None = None

    # --- 1. Execution Envelope Validation ---
    if envelope_payload is None:
        errors.append(
            DoctorError(path="envelope", message="missing execution envelope payload")
        )
    elif not isinstance(envelope_payload, Mapping):
        errors.append(
            DoctorError(path="envelope", message="envelope payload must be a mapping")
        )
    else:
        required_env_fields = {
            "agent": "missing agent",
            "repo": "missing repo",
            "root": "missing root",
            "branch": "missing branch",
            "expected_head": "missing expected_head",
            "brief_sha256": "missing brief_sha256 (brief digest)",
        }
        for field, msg in required_env_fields.items():
            val = envelope_payload.get(field)
            if val is None or (isinstance(val, str) and not val.strip()):
                errors.append(DoctorError(path=f"envelope.{field}", message=msg))

        dirty_policy = envelope_payload.get("dirty_policy")
        if dirty_policy is None or (
            isinstance(dirty_policy, str) and not dirty_policy.strip()
        ):
            errors.append(
                DoctorError(
                    path="envelope.dirty_policy",
                    message="missing Living Tree policy (dirty_policy)",
                )
            )

        try:
            envelope_obj = ExecutionEnvelope.from_payload(envelope_payload)
        except (
            ContractValidationError,
            UnsupportedSchemaError,
            TypeError,
            ValueError,
        ) as exc:
            err_msg = str(exc)
            if not any(e.path.startswith("envelope") for e in errors):
                errors.append(DoctorError(path="envelope", message=err_msg))

    # --- 2. Delivery Proof Contract Validation ---
    if contract_payload is None:
        errors.append(
            DoctorError(
                path="contract", message="missing delivery proof contract payload"
            )
        )
    elif not isinstance(contract_payload, Mapping):
        errors.append(
            DoctorError(path="contract", message="contract payload must be a mapping")
        )
    else:
        subject_id: str | None = None
        subject = contract_payload.get("subject")
        if not isinstance(subject, Mapping):
            errors.append(
                DoctorError(path="subject", message="missing subject mapping")
            )
        else:
            pub_surface = subject.get("public_surface")
            if pub_surface is None or (
                isinstance(pub_surface, str) and not pub_surface.strip()
            ):
                errors.append(
                    DoctorError(
                        path="subject.public_surface",
                        message="missing subject public entrypoint",
                    )
                )

            raw_subject_id = subject.get("producer_id")
            if raw_subject_id is None or (
                isinstance(raw_subject_id, str) and not raw_subject_id.strip()
            ):
                errors.append(
                    DoctorError(
                        path="subject.producer_id", message="missing gate-tool producer"
                    )
                )
            elif isinstance(raw_subject_id, str):
                subject_id = raw_subject_id

        witness = contract_payload.get("witness")
        if not isinstance(witness, Mapping):
            errors.append(
                DoctorError(path="witness", message="missing witness mapping")
            )
        else:
            has_digest = any(
                witness.get(k) is not None
                and (
                    not isinstance(witness.get(k), str)
                    or bool(str(witness.get(k)).strip())
                )
                for k in ("sha256", "digest", "input_sha256", "witness_digest")
            )
            has_rule = any(
                witness.get(k) is not None
                and (
                    not isinstance(witness.get(k), str)
                    or bool(str(witness.get(k)).strip())
                )
                for k in (
                    "digest_rule",
                    "digest_computation_rule",
                    "rule",
                    "computation_rule",
                )
            )
            if not has_digest and not has_rule:
                errors.append(
                    DoctorError(
                        path="witness.digest",
                        message="missing witness digest AND missing digest-computation rule",
                    )
                )

            expected_outcome = witness.get("expected_outcome")
            if expected_outcome is None or (
                isinstance(expected_outcome, str) and not expected_outcome.strip()
            ):
                errors.append(
                    DoctorError(
                        path="witness.expected_outcome",
                        message="missing witness expected_outcome",
                    )
                )

        assertion = contract_payload.get("assertion")
        if assertion is None or (
            isinstance(assertion, Mapping) and len(assertion) == 0
        ):
            errors.append(
                DoctorError(
                    path="assertion", message="missing expected outcome/assertion"
                )
            )

        neg_controls = contract_payload.get("negative_controls")
        if not isinstance(neg_controls, (list, tuple)) or len(neg_controls) == 0:
            errors.append(
                DoctorError(path="negative_controls", message="zero negative controls")
            )

        delivery_scope = contract_payload.get("delivery_scope")
        if delivery_scope is None or (
            isinstance(delivery_scope, str) and not delivery_scope.strip()
        ):
            errors.append(
                DoctorError(path="delivery_scope", message="missing delivery scope")
            )

        oracle = contract_payload.get("oracle")
        if oracle is not None:
            if isinstance(oracle, Mapping):
                oracle_id = oracle.get("producer_id")
                if subject_id and oracle_id and subject_id == oracle_id:
                    errors.append(
                        DoctorError(
                            path="oracle.producer_id",
                            message="oracle_subject_tautology: subject and oracle producer_id cannot be identical",
                        )
                    )
            else:
                errors.append(
                    DoctorError(
                        path="oracle", message="oracle must be a mapping or null"
                    )
                )

        try:
            contract_obj = DeliveryProofContract.from_payload(contract_payload)
        except ContractValidationError as exc:
            err_str = str(exc)
            if "subject and oracle require a distinct producer_id" in err_str:
                if not any("oracle_subject_tautology" in e.message for e in errors):
                    errors.append(
                        DoctorError(
                            path="oracle.producer_id",
                            message="oracle_subject_tautology: subject and oracle require a distinct producer_id",
                        )
                    )
            elif not any(
                e.path
                in (
                    "contract",
                    "subject",
                    "witness",
                    "assertion",
                    "negative_controls",
                    "delivery_scope",
                    "oracle",
                )
                for e in errors
            ):
                errors.append(DoctorError(path="contract", message=err_str))
        except (UnsupportedSchemaError, TypeError, ValueError) as exc:
            if not any(e.path == "contract" for e in errors):
                errors.append(DoctorError(path="contract", message=str(exc)))

    return DoctorReport(
        ok=len(errors) == 0,
        errors=tuple(errors),
        envelope=envelope_obj if len(errors) == 0 else None,
        contract=contract_obj if len(errors) == 0 else None,
    )


def extract_payloads_from_markdown(
    text: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[DoctorError]]:
    """Extract ExecutionEnvelope and DeliveryProofContract payloads from ```yaml / ```json blocks."""
    pattern = re.compile(
        r"```(?:yaml|yml|json)\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE
    )
    matches = pattern.findall(text)

    envelope_payload: dict[str, Any] | None = None
    contract_payload: dict[str, Any] | None = None
    parse_errors: list[DoctorError] = []

    for block in matches:
        try:
            doc = yaml.safe_load(block)
        except Exception as exc:  # noqa: BLE001 - a malformed block is a parse error; the scan continues
            parse_errors.append(
                DoctorError(path="brief", message=f"failed to parse code block: {exc}")
            )
            continue

        if not isinstance(doc, dict):
            continue

        if "execution" in doc and isinstance(doc["execution"], dict):
            envelope_payload = doc["execution"]
        elif doc.get("schema") == "vibecrafted.execution-envelope.v1":
            envelope_payload = doc

        if "proof" in doc and isinstance(doc["proof"], dict):
            contract_payload = doc["proof"]
        elif doc.get("schema") == "vibecrafted.delivery-proof.v1":
            contract_payload = doc

    errors: list[DoctorError] = list(parse_errors)
    if envelope_payload is None:
        errors.append(
            DoctorError(
                path="brief",
                message="missing execution envelope code block in markdown brief",
            )
        )
    if contract_payload is None:
        errors.append(
            DoctorError(
                path="brief",
                message="missing delivery proof contract code block in markdown brief",
            )
        )

    return envelope_payload, contract_payload, errors


def diagnose_file(path: str | Path) -> DoctorReport:
    """Diagnose a contract file: markdown brief (code-fenced) or a raw JSON/YAML file.

    Markdown is detected by ``.md`` suffix or a leading ``#``; otherwise the file
    is parsed as YAML/JSON and must contain ``execution``/``proof`` keys or a
    recognized top-level ``schema``.
    """
    source = Path(path).expanduser()
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        return DoctorReport(
            ok=False,
            errors=(DoctorError(path=str(source), message=f"unreadable file: {exc}"),),
        )

    if source.suffix == ".md" or text.lstrip().startswith("#"):
        envelope, contract, md_errors = extract_payloads_from_markdown(text)
        if md_errors:
            return DoctorReport(ok=False, errors=tuple(md_errors))
        return diagnose_payload(envelope, contract)

    try:
        doc = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - an unparseable payload is a doctor report, not a traceback
        return DoctorReport(
            ok=False,
            errors=(
                DoctorError(
                    path=str(source), message=f"unparseable json/yaml file: {exc}"
                ),
            ),
        )

    if isinstance(doc, dict):
        if "execution" in doc or "proof" in doc:
            return diagnose_payload(doc.get("execution"), doc.get("proof"))
        if doc.get("schema") == "vibecrafted.execution-envelope.v1":
            return diagnose_payload(doc, None)
        if doc.get("schema") == "vibecrafted.delivery-proof.v1":
            return diagnose_payload(None, doc)

    return DoctorReport(
        ok=False,
        errors=(DoctorError(path=str(source), message="invalid input file structure"),),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: diagnose one contract file and print text or JSON output."""
    parser = argparse.ArgumentParser(
        prog="delivery-doctor",
        description="Validate vibecrafted ExecutionEnvelope and DeliveryProofContract artifacts.",
    )
    parser.add_argument("contract_file", help="Path to contract.json or brief.md file")
    parser.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON report"
    )
    args = parser.parse_args(argv)

    report = diagnose_file(args.contract_file)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    elif report.ok:
        print("delivery-doctor: ok")
    else:
        for error in report.errors:
            print(f"{error.path}: {error.message}")
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
