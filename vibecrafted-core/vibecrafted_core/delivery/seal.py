"""Content-addressed delivery seal: issuance and reconstruction from disk.

Spec §7.8. The seal is the only artifact allowed to say *sealed*, and it binds
~30 components so that a change to any one of them invalidates reconstruction.
It is not cryptographically signed in v1 (a non-goal) — its integrity comes
from canonical serialization plus a content digest over the full binding set.

Two deliberate boundaries:

* **Issuer is a field, enforced elsewhere.** ``issue_seal`` refuses to issue
  without an explicit issuer identity, but it does not know that ``vc-ship`` is
  the only legitimate one — that authority check lands in W6.
* **The run-dir layout is injectable.** W4 owns persistence naming; this module
  only needs *some* mapping from bound component to on-disk artifact, so the
  default layout is a parameter rather than a decision.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .model import (
    ContractError,
    DeliveryRecord,
    DeliverySeal,
    DeliveryState,
)

# Single source for the schema string used at issuance; mirrors DeliverySeal.SCHEMA.
SEAL_VERSION = DeliverySeal.SCHEMA

ReconstructionStatus = Literal["verified", "stale", "missing"]


class SealError(ContractError):
    """Base error for seal issuance and reconstruction."""


class SealAuthorityError(SealError):
    """Raised when a seal is issued without an explicit issuer identity."""


class SealRefusedError(SealError):
    """Raised when the delivery record does not support a seal."""


@dataclass(frozen=True)
class SealLayout:
    """Relative paths of the on-disk artifacts a seal binds.

    W4 owns the real persistence layout; this indirection lets it rename files
    without touching seal semantics.
    """

    seal: str = "delivery-seal.json"
    envelope: str = "execution-envelope.json"
    contract: str = "delivery-proof-contract.json"
    proof_result: str = "proof/result.json"
    report: str = "report.md"
    transcript: str = "transcript.log"
    control_plane: str = "control-plane-snapshot.json"

    def reconstructable(self) -> Mapping[str, str]:
        """Map seal component name → relative artifact path."""
        return {
            "execution_envelope_sha256": self.envelope,
            "delivery_proof_contract_sha256": self.contract,
            "proof_result_sha256": self.proof_result,
            "report_sha256": self.report,
            "transcript_sha256": self.transcript,
            "control_plane_snapshot_sha256": self.control_plane,
        }


DEFAULT_SEAL_LAYOUT = SealLayout()


@dataclass(frozen=True)
class SealComponents:
    """The §7.8 binding set that does not come from the ``DeliveryRecord``.

    Kept as one frozen struct so the set can never be silently narrowed: adding
    a component to the spec means adding a field here, and every caller breaks
    loudly rather than issuing a weaker seal.
    """

    run_id: str
    lifecycle_id: str
    cut_id: str
    proof_id: str
    run_identity_sha256: str
    liveness_evidence_sha256: tuple[str, ...]
    execution_envelope_sha256: str
    delivery_proof_contract_sha256: str
    proof_result_sha256: str
    executor_source_sha256: str
    executor_version: str
    subject_evidence_sha256: str
    witness_sha256: str
    oracle_evidence_sha256: str | None
    assertion_evidence_sha256: str
    negative_control_evidence_sha256: tuple[str, ...]
    repo: str
    branch: str
    baseline_head: str
    final_head: str
    scoped_dirty_status_sha256: str
    commit_range: str
    runtime_probe_sha256: tuple[str, ...] = ()
    report_sha256: str = ""
    transcript_sha256: str = ""
    control_plane_snapshot_sha256: str = ""
    unverified_surfaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class SealReconstruction:
    """Outcome of re-deriving a seal from on-disk artifacts."""

    status: ReconstructionStatus
    seal: DeliverySeal | None
    recomputed: Mapping[str, str]
    mismatches: tuple[Mapping[str, Any], ...]

    @property
    def verified(self) -> bool:
        """True only when every bound component matched its recomputed digest."""
        return self.status == "verified"


def issue_seal(
    record: DeliveryRecord,
    *,
    issuer: str,
    components: SealComponents,
    issued_at: str | None = None,
    seal_id: str | None = None,
) -> DeliverySeal:
    """Issue a content-addressed seal for a delivered record.

    Refuses without an issuer identity, and refuses any record that is not
    ``delivered`` at exactly the scope it declared — a seal certifies the
    declared claim or it is not issued at all.
    """

    if not issuer or not issuer.strip():
        raise SealAuthorityError("seal issuance requires an explicit issuer identity")

    if record.state is not DeliveryState.DELIVERED:
        raise SealRefusedError(
            f"cannot seal delivery record in state {record.state.value!r}; "
            f"refusals: {', '.join(record.refusal_reasons) or 'none recorded'}"
        )

    if record.declared_scope != record.checked_scope:
        raise SealRefusedError(
            f"cannot seal: declared scope {record.declared_scope!r} was not the scope "
            f"actually checked ({record.checked_scope!r})"
        )

    if record.refusal_reasons:
        raise SealRefusedError(
            f"cannot seal a record carrying refusals: {', '.join(record.refusal_reasons)}"
        )

    resolved_seal_id = seal_id or _derive_seal_id(
        record=record, issuer=issuer.strip(), components=components
    )

    return DeliverySeal(
        schema=SEAL_VERSION,
        seal_id=resolved_seal_id,
        issued_at=issued_at or datetime.now(UTC).isoformat(timespec="milliseconds"),
        issuer=issuer.strip(),
        run_id=components.run_id,
        lifecycle_id=components.lifecycle_id,
        cut_id=components.cut_id,
        proof_id=components.proof_id,
        run_identity_sha256=components.run_identity_sha256,
        liveness_evidence_sha256=tuple(components.liveness_evidence_sha256),
        execution_envelope_sha256=components.execution_envelope_sha256,
        delivery_proof_contract_sha256=components.delivery_proof_contract_sha256,
        proof_result_sha256=components.proof_result_sha256,
        executor_source_sha256=components.executor_source_sha256,
        executor_version=components.executor_version,
        subject_evidence_sha256=components.subject_evidence_sha256,
        witness_sha256=components.witness_sha256,
        oracle_evidence_sha256=components.oracle_evidence_sha256,
        assertion_evidence_sha256=components.assertion_evidence_sha256,
        negative_control_evidence_sha256=tuple(
            components.negative_control_evidence_sha256
        ),
        repo=components.repo,
        branch=components.branch,
        baseline_head=components.baseline_head,
        final_head=components.final_head,
        scoped_dirty_status_sha256=components.scoped_dirty_status_sha256,
        commit_range=components.commit_range,
        declared_scope=record.declared_scope,
        checked_scope=record.checked_scope,
        runtime_probe_sha256=tuple(components.runtime_probe_sha256),
        report_sha256=components.report_sha256,
        transcript_sha256=components.transcript_sha256,
        control_plane_snapshot_sha256=components.control_plane_snapshot_sha256,
        unverified_surfaces=tuple(components.unverified_surfaces),
    )


def write_seal(
    run_dir: str | Path, seal: DeliverySeal, *, layout: SealLayout = DEFAULT_SEAL_LAYOUT
) -> Path:
    """Write the seal payload into ``run_dir`` and return its path.

    Naming comes from ``layout`` — W4 may override it without changing seal
    semantics.
    """

    directory = Path(run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / layout.seal
    path.write_text(
        json.dumps(seal.to_payload(), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path


def reconstruct_seal(
    run_dir: str | Path, *, layout: SealLayout = DEFAULT_SEAL_LAYOUT
) -> SealReconstruction:
    """Re-derive a seal's bound digests from on-disk artifacts.

    T14: if the verifier, the envelope, the proof result, or any other bound
    artifact drifted after the seal was issued, reconstruction goes ``stale``.
    Components not materialized in the run dir (evidence bundles, run identity)
    are carried forward as recorded — v1 reconstructs what is on disk and says
    so, rather than pretending to verify what it cannot see.
    """

    directory = Path(run_dir)
    seal_path = directory / layout.seal
    if not seal_path.is_file():
        return SealReconstruction(
            status="missing", seal=None, recomputed={}, mismatches=()
        )

    try:
        payload = json.loads(seal_path.read_text(encoding="utf-8"))
        seal = DeliverySeal.from_payload(payload)
    except (json.JSONDecodeError, ContractError) as exc:
        return SealReconstruction(
            status="stale",
            seal=None,
            recomputed={},
            mismatches=(
                {
                    "component": layout.seal,
                    "expected": "readable delivery seal",
                    "observed": f"unreadable: {exc}",
                },
            ),
        )

    recomputed: dict[str, str] = {}
    mismatches: list[Mapping[str, Any]] = []
    recorded = seal.to_payload()

    for component, relative in layout.reconstructable().items():
        expected = str(recorded.get(component, ""))
        artifact = directory / relative
        if not artifact.is_file():
            mismatches.append(
                {
                    "component": component,
                    "path": relative,
                    "expected": expected,
                    "observed": "missing",
                }
            )
            continue
        observed = digest_file(artifact)
        recomputed[component] = observed
        if observed != expected:
            mismatches.append(
                {
                    "component": component,
                    "path": relative,
                    "expected": expected,
                    "observed": observed,
                }
            )

    status: ReconstructionStatus = "verified" if not mismatches else "stale"
    return SealReconstruction(
        status=status,
        seal=seal,
        recomputed=recomputed,
        mismatches=tuple(mismatches),
    )


def digest_file(path: str | Path) -> str:
    """Return the canonical ``sha256:`` digest of a file's bytes."""
    return digest_bytes(Path(path).read_bytes())


def digest_bytes(data: bytes) -> str:
    """Return the canonical ``sha256:`` digest of an in-memory byte string."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _derive_seal_id(
    *, record: DeliveryRecord, issuer: str, components: SealComponents
) -> str:
    """Derive a deterministic seal id from the immutable binding set.

    ``issued_at`` is deliberately absent: rerunning the same immutable input
    yields the same identity with a different event time (T19/T20).
    """

    payload = {
        "issuer": issuer,
        "record": record.identity_payload(),
        "components": _components_payload(components),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _components_payload(components: SealComponents) -> dict[str, Any]:
    """Flatten ``SealComponents`` into a JSON-safe dict (tuples become lists)."""
    payload: dict[str, Any] = {}
    for key, value in vars(components).items():
        payload[key] = list(value) if isinstance(value, tuple) else value
    return payload


__all__ = [
    "DEFAULT_SEAL_LAYOUT",
    "SEAL_VERSION",
    "SealAuthorityError",
    "SealComponents",
    "SealError",
    "SealLayout",
    "SealReconstruction",
    "SealRefusedError",
    "digest_bytes",
    "digest_file",
    "issue_seal",
    "reconstruct_seal",
    "write_seal",
]
