"""Settlement layer — typed terminal for every finished run.

Contract (canonical draft v1, polarized 2026-07-21):

**Artifact without settlement = automatically ``needs_attention`` (TUI ``n``),
never silence.**

The delivery-proof kernel (``DeliveryState`` / ``ProofState``) answers "was
the claim sealed?" Settlement answers a different question: "where does this
run sit on the operator board (f/x/n)?" Those axes are deliberately separate.
``exit_code == 0`` alone can never produce ``FINALIZED``.

**Reason dialect (one language, no dual rewrite):**

- When a delivery-kernel receipt is present (``kernel_axes`` on the run),
  ``settlement_reason`` is the triage classification reason
  (``axes_e=…_p=…_d=…``, ``delivery_sealed``, ``execution_failed``, …).
  Settlement does **not** rewrite axes into legacy human tokens.
- When no kernel receipt is present (legacy five-signal path), settlement may
  use legacy tokens (``report_without_seal``, ``claim_unchecked``,
  ``exit_0_without_report``, …).

Terminals (1:1 to TUI f/x/n; INVALID folds into ``x`` with reason):

- ``finalized`` (f) — claim evidence + report path + proof/seal, explicit
  worker self-attestation, or explicit operator waive
- ``failed`` (x) — proof failed, death without delivery, or INVALID folded
- ``needs_attention`` (n) — default for unsealed reports, stalls, contradictions,
  orphans, and every unreadable signal
- ``invalid`` (x) — schema/contract invalidation, folded into the failed TUI cell

Settlement is written into the board projection (and, when a meta path is known,
into the run meta) so the supervisor's knowledge survives the supervisor.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .clock import utc_now_iso
from .report_contract import validate_report_file
from .run_mutation import RunMetaMutationError, mutate_run_meta
from .run_triage import (
    VERDICT_FAILED,
    VERDICT_FINALIZED,
    VERDICT_NEEDS_ATTENTION,
    read_run_signals,
)

__all__ = [
    "SETTLED_TERMINALS",
    "SETTLEMENT_EVENT_KIND",
    "SETTLEMENT_EVENT_SCHEMA",
    "SETTLEMENT_EVENT_SCHEMA_V2",
    "TRUST_RECEIPT_SCHEMA",
    "TUI_FAILED",
    "TUI_FINALIZED",
    "TUI_NEEDS_ATTENTION",
    "BareMarkdownError",
    "Settlement",
    "SettlementEventV1",
    "SettlementEventV2",
    "SettlementVerdict",
    "TrustReceiptV1",
    "board_fxn_counts",
    "build_trust_settlement_event",
    "can_archive",
    "claim_digest_from_payload",
    "emit_settlement_event",
    "is_untitled_markdown",
    "orphan_markdown_paths",
    "orphan_settlement_payloads",
    "persist_await_verdict",
    "persist_settlement_to_meta",
    "prepare_settlement_event",
    "require_bound_markdown",
    "settle_payload",
    "settlement_from_payload",
    "tui_key_for",
]

TUI_FINALIZED = "f"
TUI_FAILED = "x"
TUI_NEEDS_ATTENTION = "n"
SETTLEMENT_EVENT_KIND = "settlement.changed"
SETTLEMENT_EVENT_SCHEMA = "vibecrafted.settlement-event.v1"
SETTLEMENT_EVENT_SCHEMA_V2 = "vibecrafted.settlement-event.v2"
TRUST_RECEIPT_SCHEMA = "vibecrafted.trust-receipt.v1"

# Operator waive must be explicit and traced — never inferred from exit 0.
_WAIVE_KEYS = ("settlement_waive", "operator_waive", "waive_settlement")


class SettlementVerdict(str, Enum):
    """The four settled terminals a finished run can land on."""

    FINALIZED = "finalized"
    FAILED = "failed"
    NEEDS_ATTENTION = "needs_attention"
    INVALID = "invalid"


SETTLED_TERMINALS = frozenset(v.value for v in SettlementVerdict)


def tui_key_for(verdict: SettlementVerdict | str) -> str:
    """Map a settlement terminal onto the TUI f/x/n cell."""
    value = (
        (
            verdict.value
            if isinstance(verdict, SettlementVerdict)
            else str(verdict or "")
        )
        .strip()
        .lower()
    )
    if value == SettlementVerdict.FINALIZED.value:
        return TUI_FINALIZED
    if value in {
        SettlementVerdict.FAILED.value,
        SettlementVerdict.INVALID.value,
    }:
        return TUI_FAILED
    return TUI_NEEDS_ATTENTION


@dataclass(frozen=True)
class Settlement:
    """One settled terminal for a finished run."""

    verdict: SettlementVerdict
    reason: str
    settled_at: str
    source: str = "auto"
    claim_digest: str = ""
    waived: bool = False
    await_rc: int | None = None
    await_outcome: str = ""

    @property
    def tui_key(self) -> str:
        """Return this settlement's TUI f/x/n cell."""
        return tui_key_for(self.verdict)

    def to_payload(self) -> dict[str, Any]:
        """Serialize to the flat field set merged onto a run projection/meta."""
        payload: dict[str, Any] = {
            "settlement_verdict": self.verdict.value,
            "settlement_reason": self.reason,
            "settlement_at": self.settled_at,
            "settlement_source": self.source,
            "settlement_tui": self.tui_key,
            "settlement_waived": self.waived,
            "settlement_claim_digest": self.claim_digest,
        }
        if self.await_rc is not None:
            payload["await_rc"] = self.await_rc
        if self.await_outcome:
            payload["await_outcome"] = self.await_outcome
            payload["await_settled_at"] = self.settled_at
        return payload


@dataclass(frozen=True)
class SettlementEventStateV1:
    """The compact f/x/n identity carried on either side of a revision."""

    verdict: str
    tui: str

    def to_payload(self) -> dict[str, str]:
        """Serialize the verdict/tui pair for embedding in an event payload."""
        return {"verdict": self.verdict, "tui": self.tui}


@dataclass(frozen=True)
class SettlementEventV1:
    """One durable settlement revision written to the control-plane stream."""

    run_id: str
    previous: SettlementEventStateV1 | None
    current: SettlementEventStateV1
    reason: str
    source: str
    settled_at: str
    claim_digest: str
    waived: bool
    revision: int

    def to_payload(self) -> dict[str, Any]:
        """Serialize this v1 settlement revision for the event stream."""
        return {
            "schema": SETTLEMENT_EVENT_SCHEMA,
            "run_id": self.run_id,
            "previous": self.previous.to_payload() if self.previous else None,
            "current": self.current.to_payload(),
            "reason": self.reason,
            "source": self.source,
            "settled_at": self.settled_at,
            "claim_digest": self.claim_digest,
            "waived": self.waived,
            "revision": self.revision,
        }


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    """Encode ``payload`` as sorted, compact UTF-8 JSON for stable hashing."""
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class TrustReceiptV1:
    """Exact authority receipt issued by one explicit ``vc-trust note``.

    ``receipt_id`` is the SHA-256 of every other field in the receipt. The
    claims digest is independently the full SHA-256 of the canonical claim
    array, so neither digest is a shortened display token.
    """

    receipt_id: str
    repo_root: str
    run_id: str
    commit_sha: str
    trust_verdict: str
    settlement_verdict: str
    settlement_tui: str
    settlement_revision: int
    claim_digest: str
    schema: str = TRUST_RECEIPT_SCHEMA

    @classmethod
    def issue(
        cls,
        *,
        repo_root: str,
        run_id: str,
        commit_sha: str,
        trust_verdict: str,
        settlement_verdict: str,
        settlement_tui: str,
        settlement_revision: int,
        claim_digest: str,
    ) -> TrustReceiptV1:
        """Build the receipt and derive its ``receipt_id`` from the claim fields.

        The claim fields are hashed exactly as given — no normalization beyond
        canonical JSON encoding, so callers must pass already-validated values.
        """
        claims = {
            "schema": TRUST_RECEIPT_SCHEMA,
            "repo_root": repo_root,
            "run_id": run_id,
            "commit_sha": commit_sha,
            "trust_verdict": trust_verdict,
            "settlement_verdict": settlement_verdict,
            "settlement_tui": settlement_tui,
            "settlement_revision": settlement_revision,
            "claim_digest": claim_digest,
        }
        receipt_id = hashlib.sha256(_canonical_json(claims)).hexdigest()
        return cls(
            schema=TRUST_RECEIPT_SCHEMA,
            receipt_id=receipt_id,
            repo_root=repo_root,
            run_id=run_id,
            commit_sha=commit_sha,
            trust_verdict=trust_verdict,
            settlement_verdict=settlement_verdict,
            settlement_tui=settlement_tui,
            settlement_revision=settlement_revision,
            claim_digest=claim_digest,
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TrustReceiptV1:
        """Parse and fully re-verify a receipt, recomputing its hash to detect tampering.

        Raises ``ValueError``/``TypeError`` on any field, shape, or hash mismatch —
        this is the sole trusted decode path for a persisted receipt.
        """
        expected_fields = {
            "schema",
            "receipt_id",
            "repo_root",
            "run_id",
            "commit_sha",
            "trust_verdict",
            "settlement_verdict",
            "settlement_tui",
            "settlement_revision",
            "claim_digest",
        }
        if set(payload) != expected_fields:
            raise ValueError("trust_receipt_fields_invalid")
        if payload.get("schema") != TRUST_RECEIPT_SCHEMA:
            raise ValueError("trust_receipt_schema_invalid")
        strings: dict[str, str] = {}
        for field_name in expected_fields - {"settlement_revision"}:
            value = payload.get(field_name)
            if not isinstance(value, str):
                raise TypeError(f"trust_receipt_{field_name}_invalid")
            strings[field_name] = value
        revision = payload.get("settlement_revision")
        if type(revision) is not int or revision <= 0:
            raise ValueError("trust_receipt_settlement_revision_invalid")
        if not strings["repo_root"] or not Path(strings["repo_root"]).is_absolute():
            raise ValueError("trust_receipt_repo_root_invalid")
        if not strings["run_id"]:
            raise ValueError("trust_receipt_run_id_invalid")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", strings["commit_sha"]):
            raise ValueError("trust_receipt_commit_sha_invalid")
        if strings["trust_verdict"] not in {"pass", "pass-with-gaps", "block"}:
            raise ValueError("trust_receipt_trust_verdict_invalid")
        try:
            settlement = SettlementVerdict(strings["settlement_verdict"])
        except ValueError as exc:
            raise ValueError("trust_receipt_settlement_verdict_invalid") from exc
        if tui_key_for(settlement) != strings["settlement_tui"]:
            raise ValueError("trust_receipt_settlement_tui_invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", strings["claim_digest"]):
            raise ValueError("trust_receipt_claim_digest_invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", strings["receipt_id"]):
            raise ValueError("trust_receipt_receipt_id_invalid")
        issued = cls.issue(
            repo_root=strings["repo_root"],
            run_id=strings["run_id"],
            commit_sha=strings["commit_sha"],
            trust_verdict=strings["trust_verdict"],
            settlement_verdict=strings["settlement_verdict"],
            settlement_tui=strings["settlement_tui"],
            settlement_revision=revision,
            claim_digest=strings["claim_digest"],
        )
        if issued.receipt_id != strings["receipt_id"]:
            raise ValueError("trust_receipt_receipt_id_mismatch")
        return issued

    def to_payload(self) -> dict[str, Any]:
        """Serialize the receipt's exact ten-field wire shape."""
        return {
            "schema": self.schema,
            "receipt_id": self.receipt_id,
            "repo_root": self.repo_root,
            "run_id": self.run_id,
            "commit_sha": self.commit_sha,
            "trust_verdict": self.trust_verdict,
            "settlement_verdict": self.settlement_verdict,
            "settlement_tui": self.settlement_tui,
            "settlement_revision": self.settlement_revision,
            "claim_digest": self.claim_digest,
        }


@dataclass(frozen=True)
class SettlementEventV2:
    """Settlement event whose resume authority is one exact trust receipt."""

    run_id: str
    previous: SettlementEventStateV1 | None
    current: SettlementEventStateV1
    reason: str
    source: str
    settled_at: str
    claim_digest: str
    waived: bool
    revision: int
    trust_receipt: TrustReceiptV1

    @property
    def event_key(self) -> str:
        """Return the unique idempotency key binding this event to one receipt."""
        return f"{self.run_id}:{self.revision}:{self.trust_receipt.receipt_id}"

    def to_payload(self) -> dict[str, Any]:
        """Serialize this v2 settlement revision, including its trust receipt."""
        return {
            "schema": SETTLEMENT_EVENT_SCHEMA_V2,
            "event_key": self.event_key,
            "run_id": self.run_id,
            "previous": self.previous.to_payload() if self.previous else None,
            "current": self.current.to_payload(),
            "reason": self.reason,
            "source": self.source,
            "settled_at": self.settled_at,
            "claim_digest": self.claim_digest,
            "waived": self.waived,
            "revision": self.revision,
            "trust_receipt": self.trust_receipt.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SettlementEventV2:
        """Parse and cross-verify a v2 event against its embedded trust receipt.

        Raises ``ValueError``/``TypeError`` on any shape, revision, or
        receipt-consistency mismatch.
        """
        if payload.get("schema") != SETTLEMENT_EVENT_SCHEMA_V2:
            raise ValueError("settlement_event_schema_invalid")
        receipt_payload = payload.get("trust_receipt")
        if not isinstance(receipt_payload, Mapping):
            raise TypeError("settlement_event_trust_receipt_missing")
        receipt = TrustReceiptV1.from_payload(receipt_payload)
        current_payload = payload.get("current")
        if not isinstance(current_payload, Mapping):
            raise TypeError("settlement_event_current_invalid")
        current = SettlementEventStateV1(
            verdict=str(current_payload.get("verdict") or ""),
            tui=str(current_payload.get("tui") or ""),
        )
        previous_payload = payload.get("previous")
        previous = (
            SettlementEventStateV1(
                verdict=str(previous_payload.get("verdict") or ""),
                tui=str(previous_payload.get("tui") or ""),
            )
            if isinstance(previous_payload, Mapping)
            else None
        )
        revision = payload.get("revision")
        waived = payload.get("waived")
        if type(revision) is not int or revision <= 0:
            raise ValueError("settlement_event_revision_invalid")
        if type(waived) is not bool:
            raise ValueError("settlement_event_waived_invalid")
        event = cls(
            run_id=str(payload.get("run_id") or ""),
            previous=previous,
            current=current,
            reason=str(payload.get("reason") or ""),
            source=str(payload.get("source") or ""),
            settled_at=str(payload.get("settled_at") or ""),
            claim_digest=str(payload.get("claim_digest") or ""),
            waived=waived,
            revision=revision,
            trust_receipt=receipt,
        )
        if (
            event.run_id != receipt.run_id
            or event.revision != receipt.settlement_revision
            or event.current.verdict != receipt.settlement_verdict
            or event.current.tui != receipt.settlement_tui
            or event.claim_digest != receipt.claim_digest
            or event.source != "trust"
            or payload.get("event_key") != event.event_key
        ):
            raise ValueError("settlement_event_trust_receipt_mismatch")
        return event


def build_trust_settlement_event(
    *,
    previous_payload: Mapping[str, Any] | None,
    settlement: Settlement,
    receipt: TrustReceiptV1,
) -> SettlementEventV2:
    """Build the v2 event from the same receipt persisted on both projections."""

    previous = settlement_from_payload(previous_payload or {})
    return SettlementEventV2(
        run_id=receipt.run_id,
        previous=_settlement_event_state(previous) if previous else None,
        current=_settlement_event_state(settlement),
        reason=settlement.reason,
        source=settlement.source,
        settled_at=settlement.settled_at,
        claim_digest=settlement.claim_digest,
        waived=settlement.waived,
        revision=receipt.settlement_revision,
        trust_receipt=receipt,
    )


def _as_bool(value: Any) -> bool:
    """Coerce a payload value to bool, treating common truthy strings loosely."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "waived"}


def _expected_claim_digest(payload: Mapping[str, Any]) -> str:
    """Return the machine-owned mission binding, when the launcher supplied one."""
    for key in ("claim_digest", "mission_digest", "brief_digest"):
        digest = str(payload.get(key) or "").strip()
        if digest:
            return digest
    return ""


def claim_digest_from_payload(payload: Mapping[str, Any]) -> str:
    """Stable digest of the run's claim surface (brief / mission / prompt).

    Empty when no claim text is present — FINALIZED then requires an explicit
    waive, never a silent promotion.
    """
    for key in (
        "claim_digest",
        "settlement_claim_digest",
        "mission_digest",
        "brief_digest",
    ):
        raw = str(payload.get(key) or "").strip()
        if raw:
            return raw

    chunks: list[str] = []
    for key in ("claim", "mission", "brief", "prompt", "file", "skill", "agent"):
        value = payload.get(key)
        if value not in (None, ""):
            chunks.append(f"{key}={value}")
    if not chunks:
        return ""
    material = "\n".join(chunks).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def _has_operator_waive(payload: Mapping[str, Any]) -> bool:
    """True when the payload (top-level or nested ``settlement``) carries an explicit waive."""
    for key in _WAIVE_KEYS:
        if _as_bool(payload.get(key)):
            return True
    settlement = payload.get("settlement")
    return isinstance(settlement, Mapping) and _as_bool(settlement.get("waived"))


def _proof_passed(payload: Mapping[str, Any]) -> bool:
    """True when the delivery-kernel proof passed or the delivery was sealed."""
    proof = str(payload.get("proof_state") or "").strip().lower()
    if proof == "passed":
        return True
    delivery = str(payload.get("delivery_state") or "").strip().lower()
    return delivery == "sealed"


def _proof_failed(payload: Mapping[str, Any]) -> bool:
    """True when the delivery-kernel proof failed/invalid or was invalidated."""
    proof = str(payload.get("proof_state") or "").strip().lower()
    if proof in {"failed", "invalid"}:
        return True
    delivery = str(payload.get("delivery_state") or "").strip().lower()
    return delivery == "invalidated"


def _report_self_attestation(
    payload: Mapping[str, Any],
) -> tuple[bool, str]:
    """Return (accepted, claim_digest) for an explicit report attestation.

    Identity is fail-closed: when the run projection has a run id, the report
    must carry the same launcher-stamped id. The claim text itself is allowed
    to be pragmatic; this tier is intentionally weaker than a kernel seal.
    """
    report_path = str(
        payload.get("report") or payload.get("latest_report") or ""
    ).strip()
    if not report_path:
        return False, ""
    frontmatter = validate_report_file(report_path, require_frontmatter=True)
    if not frontmatter.ok or not frontmatter.finalized or not frontmatter.claim:
        return False, ""
    run_id = str(payload.get("run_id") or "").strip()
    if run_id and frontmatter.run_id != run_id:
        return False, ""
    report_digest = (frontmatter.fields.get("claim_digest") or "").strip()
    expected_digest = _expected_claim_digest(payload)
    # A lifecycle launcher stamps the expected mission digest into run metadata
    # before the worker starts. Pragmatic attestation may be weaker than a seal,
    # but it must still close that exact mission rather than a report-selected one.
    if expected_digest and report_digest != expected_digest:
        return False, ""
    digest = (
        report_digest
        or expected_digest
        or claim_digest_from_payload(payload)
        or hashlib.sha256(frontmatter.claim.encode("utf-8")).hexdigest()[:16]
    )
    return True, digest


def _is_terminal(payload: Mapping[str, Any]) -> bool:
    """True when the run projection has reached a finished (non-live) state."""
    state = str(payload.get("state") or payload.get("status") or "").strip().lower()
    if state in {
        "report_validated",
        "completed",
        "closed",
        "converged",
        "stopped",
        "blocked",
        "failed",
        "report_missing",
        "report_invalid",
        "contract_failed",
        "recovery_required",
        "timed_out",
        "gc",
        "ghost",
        "stalled",
        "killed_by_operator",
        "process_dead",
    }:
        return True
    if str(payload.get("liveness") or "") == "terminal":
        return True
    exit_code = payload.get("exit_code")
    if exit_code is None or exit_code == "":
        return False
    try:
        int(exit_code)
    except (TypeError, ValueError):
        return False
    return True


def settlement_from_payload(payload: Mapping[str, Any]) -> Settlement | None:
    """Read a previously written settlement, or None if absent/unreadable."""
    raw = str(payload.get("settlement_verdict") or "").strip().lower()
    if not raw:
        nested = payload.get("settlement")
        if isinstance(nested, Mapping):
            raw = str(nested.get("verdict") or "").strip().lower()
            if raw:
                try:
                    verdict = SettlementVerdict(raw)
                except ValueError:
                    return None
                return Settlement(
                    verdict=verdict,
                    reason=str(nested.get("reason") or ""),
                    settled_at=str(nested.get("settled_at") or ""),
                    source=str(nested.get("source") or "persisted"),
                    claim_digest=str(nested.get("claim_digest") or ""),
                    waived=_as_bool(nested.get("waived")),
                    await_rc=_coerce_int(nested.get("await_rc")),
                    await_outcome=str(nested.get("await_outcome") or ""),
                )
        return None
    try:
        verdict = SettlementVerdict(raw)
    except ValueError:
        return None
    return Settlement(
        verdict=verdict,
        reason=str(payload.get("settlement_reason") or ""),
        settled_at=str(payload.get("settlement_at") or ""),
        source=str(payload.get("settlement_source") or "persisted"),
        claim_digest=str(payload.get("settlement_claim_digest") or ""),
        waived=_as_bool(payload.get("settlement_waived")),
        await_rc=_coerce_int(payload.get("await_rc")),
        await_outcome=str(payload.get("await_outcome") or ""),
    )


def _settlement_fingerprint(settlement: Settlement) -> tuple[Any, ...]:
    """Return the tuple of fields whose equality defines "the same resolution"."""
    return (
        settlement.verdict.value,
        settlement.tui_key,
        settlement.reason,
        settlement.source,
        settlement.settled_at,
        settlement.claim_digest,
        settlement.waived,
    )


def _settlement_event_state(settlement: Settlement) -> SettlementEventStateV1:
    """Project a ``Settlement`` down to its compact verdict/tui event state."""
    return SettlementEventStateV1(
        verdict=settlement.verdict.value,
        tui=settlement.tui_key,
    )


def prepare_settlement_event(
    run_id: str,
    previous_payload: Mapping[str, Any] | None,
    current_payload: dict[str, Any],
) -> SettlementEventV1 | None:
    """Stamp one monotonic revision and describe a real settlement change.

    The caller must persist ``current_payload`` before emitting the returned
    event. Existing legacy settlements are adopted as revision 1 without a
    synthetic event; a subsequent real change advances them to revision 2.
    """

    current = settlement_from_payload(current_payload)
    previous = settlement_from_payload(previous_payload or {})
    previous_revision = max(
        _coerce_int((previous_payload or {}).get("settlement_revision")) or 0,
        0,
    )
    current_revision = max(
        _coerce_int(current_payload.get("settlement_revision")) or 0,
        0,
    )
    if current is None:
        if max(previous_revision, current_revision):
            current_payload["settlement_revision"] = max(
                previous_revision,
                current_revision,
            )
        return None

    if previous is not None and _settlement_fingerprint(previous) == (
        _settlement_fingerprint(current)
    ):
        current_payload["settlement_revision"] = (
            max(previous_revision, current_revision) or 1
        )
        return None

    base_revision = previous_revision or (1 if previous is not None else 0)
    revision = max(base_revision + 1, current_revision)
    current_payload["settlement_revision"] = revision
    return SettlementEventV1(
        run_id=str(run_id or current_payload.get("run_id") or ""),
        previous=_settlement_event_state(previous) if previous else None,
        current=_settlement_event_state(current),
        reason=current.reason,
        source=current.source,
        settled_at=current.settled_at,
        claim_digest=current.claim_digest,
        waived=current.waived,
        revision=revision,
    )


def emit_settlement_event(
    event: SettlementEventV1 | SettlementEventV2,
) -> dict[str, Any]:
    """Append a prepared settlement revision after its snapshot is durable."""

    from .events import append_event
    from .settlement_ledger import _append_settlement_fact

    # V2 is the authority-bearing settlement surface. Preserve its fact in the
    # permanent hash chain before publishing the bounded notification stream.
    # A ledger failure is fail-closed: no transient event may claim a fact that
    # the immutable history did not durably accept.
    if isinstance(event, SettlementEventV2):
        _append_settlement_fact(event)

    previous = event.previous.verdict if event.previous else "unsettled"
    return append_event(
        SETTLEMENT_EVENT_KIND,
        event.run_id,
        (
            f"settlement revision {event.revision}: "
            f"{previous} -> {event.current.verdict}"
        ),
        event.to_payload(),
    )


def _coerce_int(value: Any) -> int | None:
    """Parse ``value`` as an int, returning None for empty/unparseable input."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def settle_payload(
    payload: Mapping[str, Any],
    *,
    now: str | None = None,
    force: bool = False,
    source: str = "auto",
) -> Settlement | None:
    """Derive the settlement terminal for a run projection.

    Returns ``None`` for still-live runs (no terminal yet). Every finished run
    returns exactly one of the four terminals — never silence.
    """
    existing = settlement_from_payload(payload)
    if existing is not None and not force:
        # Operator waives and orphan findings are sticky. Awaited/attested n
        # may acquire stronger evidence later; self-attested f may upgrade to
        # sealed provenance (or be refuted by kernel proof).
        if (
            existing.source in {"operator_waive", "orphan_scan", "sealed"}
            or existing.waived
        ):
            return existing
        if (
            existing.source == "await"
            and existing.verdict is not SettlementVerdict.NEEDS_ATTENTION
        ):
            return existing
        if existing.source == "self_attested":
            expected_digest = _expected_claim_digest(payload)
            binding_changed = bool(
                expected_digest and existing.claim_digest != expected_digest
            )
            if not binding_changed and not (
                _proof_passed(payload) or _proof_failed(payload)
            ):
                return existing
        # Auto settlements may be recomputed as more evidence lands (seal late).
        elif existing.source not in {"auto", "persisted", "await", ""}:
            return existing

    if not _is_terminal(payload):
        return None

    settled_at = now or utc_now_iso()
    claim_digest = claim_digest_from_payload(payload)
    waived = _has_operator_waive(payload)

    def _stable(candidate: Settlement) -> Settlement:
        """Return ``existing`` unchanged when ``candidate`` resolves identically."""
        # Recomputing with unchanged evidence must be a no-op: re-stamping
        # ``settled_at`` on every board sync made each pass rewrite every
        # terminal snapshot and emit a spurious "refreshed" event — the event
        # stream grew without bound and the idempotency comparison never held.
        same_resolution = (
            existing is not None
            and existing.verdict is candidate.verdict
            and existing.reason == candidate.reason
            and existing.claim_digest == candidate.claim_digest
            and existing.waived == candidate.waived
        )
        if (
            existing is not None
            and same_resolution
            and (
                existing.source == candidate.source
                # A normal board sync is not stronger provenance than the await
                # finalizer. Keep the durable await source instead of toggling
                # await -> auto -> await and inventing two revisions per replay.
                or (existing.source == "await" and candidate.source == "auto")
            )
        ):
            return existing
        return candidate

    # Hard invalidation from the delivery kernel — folds into x.
    if _proof_failed(payload):
        return _stable(
            Settlement(
                verdict=SettlementVerdict.INVALID
                if str(payload.get("proof_state") or "").lower() == "invalid"
                or str(payload.get("delivery_state") or "").lower() == "invalidated"
                else SettlementVerdict.FAILED,
                reason="proof_or_delivery_failed",
                settled_at=settled_at,
                source=source,
                claim_digest=claim_digest,
                waived=False,
            )
        )

    # Board projections use `latest_report` / `latest_transcript`; launcher
    # meta uses `report` / `transcript`. Normalize before classifying.
    signal_payload = dict(payload)
    if not str(signal_payload.get("report") or "").strip():
        alt = str(signal_payload.get("latest_report") or "").strip()
        if alt:
            signal_payload["report"] = alt
    if not str(signal_payload.get("transcript") or "").strip():
        alt = str(signal_payload.get("latest_transcript") or "").strip()
        if alt:
            signal_payload["transcript"] = alt
    signals = read_run_signals(signal_payload)
    classification = signals.classify()
    self_attested, attested_claim_digest = _report_self_attestation(signal_payload)

    if waived:
        return _stable(
            Settlement(
                verdict=SettlementVerdict.FINALIZED,
                reason="operator_waive",
                settled_at=settled_at,
                source="operator_waive",
                claim_digest=claim_digest,
                waived=True,
            )
        )

    # Sealed provenance is stronger than the pragmatic attestation tier.
    if classification.verdict == VERDICT_FINALIZED and _proof_passed(payload):
        if not claim_digest:
            return _stable(
                Settlement(
                    verdict=SettlementVerdict.NEEDS_ATTENTION,
                    reason="finalized_candidate_without_claim",
                    settled_at=settled_at,
                    source=source,
                    claim_digest="",
                )
            )
        return _stable(
            Settlement(
                verdict=SettlementVerdict.FINALIZED,
                reason="claim_report_and_seal",
                settled_at=settled_at,
                source="sealed",
                claim_digest=claim_digest,
            )
        )

    # Pragmatic tier from the operator addendum: a validated, run-bound report
    # must contain both deliberate fields. Process exit alone never enters it.
    if self_attested:
        return _stable(
            Settlement(
                verdict=SettlementVerdict.FINALIZED,
                reason="report_self_attested",
                settled_at=settled_at,
                source="self_attested",
                claim_digest=attested_claim_digest,
            )
        )

    if classification.verdict == VERDICT_FAILED:
        return _stable(
            Settlement(
                verdict=SettlementVerdict.FAILED,
                reason=classification.reason,
                settled_at=settled_at,
                source=source,
                claim_digest=claim_digest,
            )
        )

    has_report = bool(signals.report_exists)
    # Polarized contract (doctrine, 2026-07-21):
    # When a delivery-kernel receipt is present, settlement_reason IS the
    # triage classification reason (axes_e=… / delivery_sealed / …). Never
    # rewrite axes into legacy tokens. Legacy human tokens
    # (report_without_seal, claim_unchecked) apply only when kernel_axes
    # is absent — one language per path, no dual dialect.
    if classification.verdict == VERDICT_NEEDS_ATTENTION:
        reason = classification.reason
        if signals.kernel_axes is None and has_report and not _proof_passed(payload):
            reason = "claim_unchecked" if not claim_digest else "report_without_seal"
        return _stable(
            Settlement(
                verdict=SettlementVerdict.NEEDS_ATTENTION,
                reason=reason,
                settled_at=settled_at,
                source=source,
                claim_digest=claim_digest,
            )
        )

    # classify_run says finalized (exit 0 + delivered state + report, or
    # delivery_state=sealed). Contract still requires claim + proof/seal —
    # otherwise park as n. exit_code==0 alone never reaches here as FINALIZED
    # without a report (or a seal) from the classifier.
    if classification.verdict == VERDICT_FINALIZED:
        if not claim_digest:
            return _stable(
                Settlement(
                    verdict=SettlementVerdict.NEEDS_ATTENTION,
                    reason="finalized_candidate_without_claim",
                    settled_at=settled_at,
                    source=source,
                    claim_digest="",
                )
            )
        # The sealed branch returned above. What remains is an unsealed legacy
        # finalized candidate, which stays n without explicit attestation.
        demote_reason = (
            classification.reason
            if signals.kernel_axes is not None
            else "report_without_seal"
        )
        return _stable(
            Settlement(
                verdict=SettlementVerdict.NEEDS_ATTENTION,
                reason=demote_reason,
                settled_at=settled_at,
                source=source,
                claim_digest=claim_digest,
            )
        )

    # Unreachable under current classify_run, but fail closed.
    return _stable(
        Settlement(
            verdict=SettlementVerdict.NEEDS_ATTENTION,
            reason=f"unmapped_classification:{classification.verdict}",
            settled_at=settled_at,
            source=source,
            claim_digest=claim_digest,
        )
    )


def can_archive(payload: Mapping[str, Any]) -> bool:
    """Settlement precedes gc: refuse to archive unsettled runs.

    A run with no settlement_verdict is never collectable — it must be parked
    as needs_attention first. Any of the four terminals unlocks archive.
    """
    settlement = settlement_from_payload(payload)
    if settlement is None:
        return False
    return settlement.verdict.value in SETTLED_TERMINALS


def board_fxn_counts(runs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count TUI f/x/n cells from the settlement axis only.

    Never recomputed from exit codes or raw lifecycle states. Unsettled
    terminal runs count as ``n`` (never silence). Live runs are ignored.
    """
    counts = {TUI_FINALIZED: 0, TUI_FAILED: 0, TUI_NEEDS_ATTENTION: 0}
    for run in runs:
        settlement = settlement_from_payload(run)
        if settlement is None:
            if _is_terminal(run):
                counts[TUI_NEEDS_ATTENTION] += 1
            continue
        counts[settlement.tui_key] += 1
    return counts


_UNTITLED_RE = re.compile(r"(?i)^untitled.*\.md$")


class BareMarkdownError(ValueError):
    """Raised when an artifact path would violate the bound-markdown contract."""


def is_untitled_markdown(path: Path | str) -> bool:
    """True when the basename matches the bare ``Untitled*.md`` pattern."""
    name = path.name if isinstance(path, Path) else Path(str(path)).name
    return bool(_UNTITLED_RE.match(name))


def require_bound_markdown(
    path: Path | str,
    *,
    run_id: str = "",
    claim_digest: str = "",
) -> Path:
    """Refuse bare markdown. Artifact creation requires run_id (+ claim digest).

    Contract rule 6: ``Untitled*.md`` cannot be created through the runtime
    write path. Call this before every report write that the runtime owns.
    """
    target = path if isinstance(path, Path) else Path(str(path))
    if is_untitled_markdown(target):
        raise BareMarkdownError(
            f"bare markdown refused: {target.name!r} (Untitled*.md lands only "
            f"as needs_attention via orphan scan, never as a runtime write)"
        )
    rid = str(run_id or "").strip()
    if not rid:
        raise BareMarkdownError(f"artifact write requires run_id: {target}")
    # claim_digest is required for FINALIZED later; at write time we only
    # refuse the empty-binding case when the caller explicitly asks for it
    # by passing claim_digest="" *and* setting a sentinel — keep write path
    # permissive on digest so workers can still land reports that settle as n.
    _ = claim_digest  # reserved for future hard-require at seal time
    return target


def orphan_markdown_paths(artifacts_root: Path) -> list[Path]:
    """List bare ``Untitled*.md`` artifacts (no run_id binding).

    Contract rule 6: bare markdown is either uncreatable or lands directly
    in ``n``. Legacy orphans are listed here for settlement as ``n``.
    """
    if not artifacts_root.is_dir():
        return []
    found: list[Path] = []
    for path in artifacts_root.rglob("*.md"):
        if _UNTITLED_RE.match(path.name):
            found.append(path)
    return sorted(found)


def orphan_settlement_payloads(
    artifacts_root: Path,
    *,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Project each legacy ``Untitled*.md`` as a synthetic settled-``n`` run.

    Used by the board so orphan artifacts are never silent — they always
    contribute to the TUI ``n`` count until cleaned up or waived.
    """
    stamp = now or utc_now_iso()
    payloads: list[dict[str, Any]] = []
    for path in orphan_markdown_paths(artifacts_root):
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
        run_id = f"orphan-md-{digest}"
        settlement = Settlement(
            verdict=SettlementVerdict.NEEDS_ATTENTION,
            reason="orphan_untitled_markdown",
            settled_at=stamp,
            source="orphan_scan",
            claim_digest="",
        )
        payload: dict[str, Any] = {
            "run_id": run_id,
            "state": "completed",
            "health": "final",
            "liveness": "terminal",
            "agent": "orphan",
            "skill": "orphan_scan",
            "report": str(path),
            "latest_report": str(path),
            "orphan_path": str(path),
            "updated_at": stamp,
        }
        payload.update(settlement.to_payload())
        payload["settlement"] = {
            "verdict": settlement.verdict.value,
            "reason": settlement.reason,
            "settled_at": settlement.settled_at,
            "source": settlement.source,
            "claim_digest": settlement.claim_digest,
            "waived": False,
            "tui": settlement.tui_key,
        }
        payloads.append(payload)
    return payloads


def persist_settlement_to_meta(
    meta_path: Path,
    settlement: Settlement,
    *,
    control_plane_root: Path,
    run_id: str,
    revision: int | None = None,
) -> bool:
    """Merge one monotonic settlement revision into canonical runtime meta.

    An explicit ``revision`` binds the meta write to the snapshot transaction.
    A lower revision, or a conflicting settlement at the same revision, is
    refused after the per-run lock is held. Legacy callers without a revision
    are assigned the next monotonic revision under that same lock.
    """

    requested_revision = _coerce_int(revision)
    if revision is not None and (requested_revision is None or requested_revision <= 0):
        return False

    def _merge(payload: dict[str, Any]) -> dict[str, Any] | None:
        """Mutator for ``mutate_run_meta``: merge the settlement, or None to refuse the write."""
        current = settlement_from_payload(payload)
        current_revision = max(
            _coerce_int(payload.get("settlement_revision")) or 0,
            0,
        )
        if requested_revision is None:
            if current is not None and _settlement_fingerprint(current) == (
                _settlement_fingerprint(settlement)
            ):
                target_revision = current_revision or 1
            else:
                target_revision = current_revision + 1
        else:
            target_revision = requested_revision
            if current_revision > target_revision:
                return None
            if (
                current_revision == target_revision
                and current is not None
                and _settlement_fingerprint(current)
                != _settlement_fingerprint(settlement)
            ):
                return None

        nested = {
            "verdict": settlement.verdict.value,
            "reason": settlement.reason,
            "settled_at": settlement.settled_at,
            "source": settlement.source,
            "claim_digest": settlement.claim_digest,
            "waived": settlement.waived,
            "tui": settlement.tui_key,
            "await_rc": settlement.await_rc,
            "await_outcome": settlement.await_outcome,
            "revision": target_revision,
        }
        payload.update(settlement.to_payload())
        payload["settlement_revision"] = target_revision
        payload["settlement"] = nested
        return payload

    try:
        return mutate_run_meta(
            control_plane_root,
            meta_path=meta_path,
            run_id=run_id,
            mutator=_merge,
        )
    except (OSError, RunMetaMutationError, TypeError, ValueError):
        return False


def persist_await_verdict(
    meta_path: Path | None,
    *,
    control_plane_root: Path,
    run_id: str,
    rc: int | None,
    outcome: str,
    worker_alive: bool,
    reason: str,
    settled_at: str | None = None,
) -> dict[str, Any]:
    """Persist the supervisor await verdict so it survives the supervisor.

    Returns the fields to merge into a board projection even when meta is
    missing (projection-only write).
    """
    stamp = settled_at or utc_now_iso()
    fields: dict[str, Any] = {
        "await_rc": rc,
        "await_outcome": outcome,
        "await_reason": reason,
        "await_worker_alive": bool(worker_alive),
        "await_settled_at": stamp,
    }
    if meta_path is not None:

        def _merge(payload: dict[str, Any]) -> dict[str, Any]:
            """Mutator for ``mutate_run_meta``: merge the await-verdict fields in place."""
            payload.update(fields)
            return payload

        try:
            mutate_run_meta(
                control_plane_root,
                meta_path=meta_path,
                run_id=run_id,
                mutator=_merge,
            )
        except (OSError, RunMetaMutationError, TypeError, ValueError):
            pass
    return fields
