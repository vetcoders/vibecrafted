"""vc-guard — in-flight enforcer sibling of vc-trust.

Trust judges after the fact. Guard enforces at the gate. Guard never invents
settlement letters: it only consumes trust journal verdicts (and inventories
existing gates). Fail-closed with mandatory remedium text.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

META_SKILLS = frozenset({"trust", "guard", "init"})
REMEDIATION_TASKS = frozenset({"repair", "admission"})
REMEDIATION_REASON_MIN = 8
REMEDIATION_REASON_MAX = 500

from . import control_plane, trust
from .settlement import TrustReceiptV1

# Gate inventory — naming what already exists (coverage, not reimplementation).
GATE_INVENTORY: tuple[dict[str, str], ...] = (
    {
        "id": "commit-msg",
        "path": "scripts/hooks/commit-msg",
        "enforces": "subject [<agent>/<runtime>] type, Authored-By, session_id, time, runtime, body; bans vendor Co-Authored-By",
        "phase": "commit",
        "mode": "hard",
    },
    {
        "id": "prepare-commit-msg",
        "path": "scripts/hooks/prepare-commit-msg",
        "enforces": "fills Authored-By/session_id/time/runtime trailers when subject is legal",
        "phase": "commit",
        "mode": "helper",
    },
    {
        "id": "pre-commit",
        "path": "scripts/hooks/pre-commit (or core.hooksPath)",
        "enforces": "ruff/prettier/semgrep family quality gates before commit",
        "phase": "commit",
        "mode": "hard",
    },
    {
        "id": "pre-push",
        "path": "scripts/hooks/pre-push (or core.hooksPath)",
        "enforces": "push-time quality/security gates",
        "phase": "push",
        "mode": "hard",
    },
    {
        "id": "loctree-first",
        "path": "agent doctrine + tools/hooks (loctree-first)",
        "enforces": "structural map before structural grep; rejects blind rummaging",
        "phase": "agent",
        "mode": "policy",
    },
    {
        "id": "classifier-hard-stops",
        "path": "vc-operator AUTONOMY.md + charter hard-stops",
        "enforces": "push/merge/deploy/secrets require operator button",
        "phase": "dispatch",
        "mode": "policy",
    },
    {
        "id": "commit-msg-diff-advisory",
        "path": "tools/hooks/lib/commit-msg-diff-gate.sh (seed)",
        "enforces": "advisory claim-vs-staged pack checks (e.g. 5-file pack)",
        "phase": "commit",
        "mode": "advisory",
    },
    {
        "id": "trust-block-dispatch",
        "path": "vibecrafted_core.guard.enforce_continuation",
        "enforces": "refuse workflow continuation when HEAD (or line) has trust verdict block; authorized remediation is launch-time only and never rewrites the journal",
        "phase": "dispatch",
        "mode": "hard",
    },
)

COVERAGE_GAPS: tuple[str, ...] = (
    "No fleet-wide guarantee that every sibling repo has seeded commit-msg hooks.",
    "commit-msg-diff-gate remains advisory until operator elevates it to hard.",
    "PATH/install drift (source green, installed deck stale) is not yet a guard gate.",
    "Trust block refuse covers HEAD-by-default; per-branch line policy is opt-in via --sha.",
    "Authorized remediation is a public-launcher override with reason+task; it does not disable VIBECRAFTED_GUARD or rewrite the trust journal.",
)


@dataclass(frozen=True)
class GuardDecision:
    """Allow/refuse verdict returned by `enforce_continuation`, with remedium text."""

    allowed: bool
    reason: str
    remedium: str
    blocking_sha: str = ""
    blocking_verdict: str = ""
    journal: str = ""
    remediation_authorized: bool = False
    remediation_reason: str = ""
    remediation_task: str = ""

    def to_payload(self) -> dict[str, Any]:
        """Serialize this decision to a plain dict for JSON output."""
        return asdict(self)


class GuardRefusal(ValueError):
    """Launch-time refusal that carries the full guard decision for receipts."""

    def __init__(self, decision: GuardDecision) -> None:
        super().__init__(decision.remedium or "vc-guard refused continuation")
        self.decision = decision


def _flag_enabled(value: Any) -> bool:
    """Parse a public boolean flag without treating the string 'false' as true."""
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_remediation_flags(
    *,
    remediating: Any = False,
    reason: Any = "",
    task: Any = "",
) -> tuple[bool, str, str]:
    """Validate the public remediation trio. Raises ValueError on incomplete/invalid input."""
    requested = _flag_enabled(remediating)
    cleaned_reason = str(reason or "").strip()
    cleaned_task = str(task or "").strip().lower()
    extras = bool(cleaned_reason or cleaned_task)
    if not requested and not extras:
        return False, "", ""
    if not requested:
        raise ValueError(
            "vc-guard: --remediation-reason/--remediation-task require "
            "--remediate-trust-block"
        )
    if not cleaned_reason:
        raise ValueError(
            "vc-guard: --remediate-trust-block requires --remediation-reason"
        )
    if len(cleaned_reason) < REMEDIATION_REASON_MIN:
        raise ValueError(
            "vc-guard: --remediation-reason must be at least "
            f"{REMEDIATION_REASON_MIN} characters"
        )
    if len(cleaned_reason) > REMEDIATION_REASON_MAX:
        raise ValueError(
            "vc-guard: --remediation-reason exceeds "
            f"{REMEDIATION_REASON_MAX} characters"
        )
    if any(ord(char) < 32 for char in cleaned_reason):
        raise ValueError("vc-guard: --remediation-reason must be printable text")
    if cleaned_task not in REMEDIATION_TASKS:
        allowed = "|".join(sorted(REMEDIATION_TASKS))
        raise ValueError(f"vc-guard: --remediation-task must be one of {allowed}")
    return True, cleaned_reason, cleaned_task


def launch_disclosure(decision: GuardDecision) -> dict[str, Any]:
    """Receipt projection that discloses a BLOCK override without implying PASS."""
    if not (
        decision.remediation_authorized
        or decision.blocking_verdict == "block"
        or decision.reason.startswith("remediation_")
        or decision.reason == "trust_block"
    ):
        return {}
    payload: dict[str, Any] = {
        "blocking_verdict": decision.blocking_verdict,
        "blocking_sha": decision.blocking_sha,
        "continuation": (
            "authorized_remediation" if decision.remediation_authorized else "refused"
        ),
        "reason": decision.reason,
        "trust_verdict_unchanged": True,
    }
    if decision.remediation_reason:
        payload["remediation_reason"] = decision.remediation_reason
    if decision.remediation_task:
        payload["remediation_task"] = decision.remediation_task
    return payload


@dataclass(frozen=True)
class GuardianResumeAuthority:
    """Fail-closed authority decision for one native Guardian resume."""

    allowed: bool
    reason: str
    retryable: bool
    terminal: bool
    receipt_id: str = ""
    journal: str = ""
    detail: str = ""

    def to_payload(self) -> dict[str, Any]:
        """Serialize this authority decision to a plain dict for JSON output."""
        return asdict(self)


def _guardian_resume_decision(
    *,
    allowed: bool,
    reason: str,
    journal: Path,
    receipt_id: str = "",
    retryable: bool = False,
    detail: str = "",
) -> GuardianResumeAuthority:
    """Build one `GuardianResumeAuthority`, deriving `terminal` from `retryable`."""
    return GuardianResumeAuthority(
        allowed=allowed,
        reason=reason,
        retryable=retryable,
        terminal=not retryable,
        receipt_id=receipt_id,
        journal=str(journal),
        detail=detail,
    )


def _canonical_root(value: Any) -> str:
    """Resolve `value` to an absolute path string; empty string on any failure."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return ""


def _latest_run_trust_record(
    records: Sequence[Mapping[str, Any]],
    run_id: str,
) -> Mapping[str, Any] | None:
    """Return the last (most recent) trust journal record matching `run_id`."""
    latest: Mapping[str, Any] | None = None
    for record in records:
        if str(record.get("run_id") or "") == run_id:
            latest = record
    return latest


def _journal_receipt(
    record: Mapping[str, Any],
) -> tuple[TrustReceiptV1 | None, str]:
    """Extract and cross-validate a v2 trust receipt embedded in a journal record.

    Returns (receipt, "") on success or (None, reason) fail-closed on any
    schema/binding mismatch between the journal record and its embedded receipt.
    """
    if record.get("schema") != trust.TRUST_JOURNAL_SCHEMA_V2:
        return None, "legacy_trust_record_not_resume_authority"
    raw = record.get("trust_receipt")
    if not isinstance(raw, Mapping):
        return None, "trust_receipt_missing"
    try:
        receipt = TrustReceiptV1.from_payload(raw)
    except (TypeError, ValueError) as exc:
        return None, str(exc)
    if not str(record.get("recorded_at") or "").strip():
        return None, "trust_journal_recorded_at_missing"
    claims = record.get("claims")
    if not isinstance(claims, list) or not all(
        isinstance(item, Mapping) for item in claims
    ):
        return None, "trust_receipt_claims_invalid"
    journal_bindings = {
        "repo_root": str(record.get("repo_root") or ""),
        "run_id": str(record.get("run_id") or ""),
        "commit_sha": str(record.get("sha") or ""),
        "trust_verdict": str(record.get("verdict") or ""),
        "settlement_tui": str(record.get("settlement_tui") or ""),
        "claim_digest": str(record.get("claim_digest") or ""),
    }
    receipt_bindings = {
        "repo_root": receipt.repo_root,
        "run_id": receipt.run_id,
        "commit_sha": receipt.commit_sha,
        "trust_verdict": receipt.trust_verdict,
        "settlement_tui": receipt.settlement_tui,
        "claim_digest": receipt.claim_digest,
    }
    for field_name, value in journal_bindings.items():
        if value != receipt_bindings[field_name]:
            return None, f"trust_journal_{field_name}_mismatch"
    if trust._claims_digest(claims) != receipt.claim_digest:
        return None, "trust_journal_claim_digest_mismatch"
    return receipt, ""


def _projection_receipt_mismatch(
    payload: Mapping[str, Any],
    receipt: TrustReceiptV1,
    *,
    label: str,
    recorded_at: str,
) -> str:
    """Return an empty string if `payload` field-for-field matches `receipt`.

    Otherwise returns a `{label}_{field}_mismatch` reason string for the first
    disagreement found (receipt copy, settlement fields, or nested settlement dict).
    """
    raw = payload.get("trust_receipt")
    if not isinstance(raw, Mapping):
        return f"{label}_trust_receipt_missing"
    if dict(raw) != receipt.to_payload():
        return f"{label}_trust_receipt_mismatch"
    expected_reason = (
        f"trust_{receipt.trust_verdict.replace('-', '_')}:{receipt.commit_sha}"
    )
    expected = {
        "run_id": receipt.run_id,
        "repo_root": receipt.repo_root,
        "commit_sha": receipt.commit_sha,
        "settlement_revision": receipt.settlement_revision,
        "settlement_verdict": receipt.settlement_verdict,
        "settlement_reason": expected_reason,
        "settlement_at": recorded_at,
        "settlement_tui": receipt.settlement_tui,
        "settlement_source": "trust",
        "settlement_claim_digest": receipt.claim_digest,
        "settlement_waived": False,
    }
    for field_name, value in expected.items():
        if payload.get(field_name) != value:
            return f"{label}_{field_name}_mismatch"
    for field_name in ("root", "repo_root"):
        if _canonical_root(payload.get(field_name)) != receipt.repo_root:
            return f"{label}_{field_name}_mismatch"
    if payload.get("await_rc") is not None:
        return f"{label}_await_rc_mismatch"
    if str(payload.get("await_outcome") or ""):
        return f"{label}_await_outcome_mismatch"
    if str(payload.get("await_settled_at") or ""):
        return f"{label}_await_settled_at_mismatch"
    nested = payload.get("settlement")
    if not isinstance(nested, Mapping):
        return f"{label}_settlement_missing"
    expected_nested = {
        "verdict": receipt.settlement_verdict,
        "reason": expected_reason,
        "settled_at": recorded_at,
        "source": "trust",
        "claim_digest": receipt.claim_digest,
        "waived": False,
        "tui": receipt.settlement_tui,
        "await_rc": None,
        "await_outcome": "",
    }
    if dict(nested) != expected_nested:
        return f"{label}_settlement_mismatch"
    return ""


def _outbox_has_receipt(run_id: str, receipt_id: str) -> bool:
    """True when the run's trust outbox already carries the given receipt id."""
    outbox = control_plane._read_json(trust._trust_outbox_path(run_id))
    raw = outbox.get("trust_receipt")
    if not isinstance(raw, Mapping):
        return False
    try:
        receipt = TrustReceiptV1.from_payload(raw)
    except (TypeError, ValueError):
        return False
    return (
        outbox.get("schema") == trust.TRUST_OUTBOX_SCHEMA
        and str(outbox.get("run_id") or "") == run_id
        and receipt.receipt_id == receipt_id
    )


def _outbox_projection_lag(
    run_id: str,
    receipt: TrustReceiptV1,
    payload: Mapping[str, Any],
) -> bool:
    """True only for the pre-receipt projection an exact pending outbox can heal."""

    outbox = control_plane._read_json(trust._trust_outbox_path(run_id))
    raw = outbox.get("trust_receipt")
    fields = outbox.get("projection_fields")
    if not isinstance(raw, Mapping) or not isinstance(fields, Mapping):
        return False
    try:
        outbox_receipt = TrustReceiptV1.from_payload(raw)
    except (TypeError, ValueError):
        return False
    normalized_fields = trust._projection_fields_for_receipt(fields, outbox_receipt)
    return (
        outbox.get("schema") == trust.TRUST_OUTBOX_SCHEMA
        and str(outbox.get("run_id") or "") == run_id
        and outbox_receipt == receipt
        and trust._can_complete_projection(
            payload,
            receipt=receipt,
            previous_revision=receipt.settlement_revision - 1,
            fields=normalized_fields,
        )
    )


def authorize_guardian_resume(
    *,
    run_id: str,
    repo: Path | str | None = None,
    journal: Path | None = None,
    meta: Mapping[str, Any] | None = None,
    projection: Mapping[str, Any] | None = None,
    expected_receipt_id: str = "",
) -> GuardianResumeAuthority:
    """Authorize only the exact latest v2 receipt that owns live ``n`` state.

    This boundary intentionally never resolves ``HEAD``. The commit identity is
    the full SHA already bound by the receipt; every persisted copy must match
    field-for-field before native resume can launch.
    """

    target = str(run_id or "").strip()
    resolved_journal = (journal or trust.default_journal_path()).expanduser()
    if not target or Path(target).name != target or target in {".", ".."}:
        return _guardian_resume_decision(
            allowed=False,
            reason="invalid_run_id",
            journal=resolved_journal,
        )
    expected = str(expected_receipt_id or "").strip()
    if expected and (
        len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected)
    ):
        return _guardian_resume_decision(
            allowed=False,
            reason="invalid_expected_receipt_id",
            journal=resolved_journal,
        )
    if not resolved_journal.is_file():
        return _guardian_resume_decision(
            allowed=False,
            reason="trust_journal_missing",
            journal=resolved_journal,
        )
    try:
        records = trust._read_journal(resolved_journal)
    except trust.TrustJournalRetryable as exc:
        return _guardian_resume_decision(
            allowed=False,
            reason="trust_journal_busy",
            journal=resolved_journal,
            receipt_id=expected,
            retryable=True,
            detail=f"{type(exc).__name__}: {exc}",
        )
    except (OSError, TypeError, ValueError) as exc:
        return _guardian_resume_decision(
            allowed=False,
            reason="trust_journal_unreadable",
            journal=resolved_journal,
            detail=f"{type(exc).__name__}: {exc}",
        )
    record = _latest_run_trust_record(records, target)
    if record is None:
        retryable = bool(expected and _outbox_has_receipt(target, expected))
        return _guardian_resume_decision(
            allowed=False,
            reason=(
                "trust_receipt_not_yet_visible"
                if retryable
                else "trust_receipt_missing"
            ),
            journal=resolved_journal,
            receipt_id=expected,
            retryable=retryable,
        )
    receipt, error = _journal_receipt(record)
    if receipt is None:
        return _guardian_resume_decision(
            allowed=False,
            reason=error,
            journal=resolved_journal,
        )
    if expected and receipt.receipt_id != expected:
        return _guardian_resume_decision(
            allowed=False,
            reason="expected_trust_receipt_mismatch",
            journal=resolved_journal,
            receipt_id=receipt.receipt_id,
        )
    if (
        receipt.trust_verdict != "pass-with-gaps"
        or receipt.settlement_verdict != "needs_attention"
        or receipt.settlement_tui != "n"
    ):
        return _guardian_resume_decision(
            allowed=False,
            reason="trust_receipt_not_resumable",
            journal=resolved_journal,
            receipt_id=receipt.receipt_id,
        )

    live_meta = dict(meta or {})
    live_projection = dict(projection or {})
    if not live_meta or not live_projection:
        try:
            resolved = control_plane.resolve_run(target)
        except (OSError, ValueError, control_plane.RunNotResolved) as exc:
            return _guardian_resume_decision(
                allowed=False,
                reason="run_projection_unreadable",
                journal=resolved_journal,
                receipt_id=receipt.receipt_id,
                detail=f"{type(exc).__name__}: {exc}",
            )
        if not live_meta and resolved.meta is not None:
            live_meta = control_plane._read_json(resolved.meta)
        if not live_projection:
            live_projection = control_plane._read_json(
                control_plane.run_snapshot_dir() / f"{target}.json"
            )
    if not live_meta or not live_projection:
        retryable = _outbox_has_receipt(target, receipt.receipt_id)
        return _guardian_resume_decision(
            allowed=False,
            reason=(
                "trust_receipt_projection_pending"
                if retryable
                else "run_projection_unreadable"
            ),
            journal=resolved_journal,
            receipt_id=receipt.receipt_id,
            retryable=retryable,
        )

    if _canonical_root(receipt.repo_root) != receipt.repo_root:
        return _guardian_resume_decision(
            allowed=False,
            reason="trust_receipt_repo_root_mismatch",
            journal=resolved_journal,
            receipt_id=receipt.receipt_id,
        )
    if repo is not None and _canonical_root(repo) != receipt.repo_root:
        return _guardian_resume_decision(
            allowed=False,
            reason="trust_receipt_repo_root_mismatch",
            journal=resolved_journal,
            receipt_id=receipt.receipt_id,
        )
    for label, payload in (("meta", live_meta), ("projection", live_projection)):
        mismatch = _projection_receipt_mismatch(
            payload,
            receipt,
            label=label,
            recorded_at=str(record.get("recorded_at") or ""),
        )
        if mismatch:
            retryable = _outbox_projection_lag(target, receipt, payload)
            return _guardian_resume_decision(
                allowed=False,
                reason=("trust_receipt_projection_pending" if retryable else mismatch),
                journal=resolved_journal,
                receipt_id=receipt.receipt_id,
                retryable=retryable,
            )
    return _guardian_resume_decision(
        allowed=True,
        reason="trust_receipt_authorized",
        journal=resolved_journal,
        receipt_id=receipt.receipt_id,
    )


def inventory() -> dict[str, Any]:
    """Return the static named-gates inventory and known coverage gaps as a dict."""
    return {
        "schema": "vibecrafted.guard-inventory.v1",
        "role": "enforcer",
        "sibling": "vc-trust (judge; never blocks dispatch)",
        "gates": list(GATE_INVENTORY),
        "coverage_gaps": list(COVERAGE_GAPS),
        "doctrine": {
            "fail_closed": True,
            "remedium_required": True,
            "non_interactive_safe": True,
            "settlement_authority": "vc-trust note only; guard never invents f/x/n letters",
            "agent_fairness": (
                "commit-msg enforces Authored-By shape; trust falsifies fairness "
                "truth; guard may refuse when trust has blocked the line"
            ),
        },
    }


def _repo_root(path: Path | None = None) -> Path:
    """Delegate repo-root resolution to `trust._repo_root`."""
    return trust._repo_root(path)


def _head_sha(repo: Path) -> str:
    """Return the full HEAD sha for `repo`, raising ValueError on git failure."""
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or "cannot resolve HEAD")
    return proc.stdout.strip()


def latest_trust_verdict(
    *,
    repo: Path,
    journal: Path,
    sha: str = "",
) -> dict[str, Any] | None:
    """Latest append-only trust journal record for repo+sha (default HEAD)."""
    resolved_repo = _repo_root(repo)
    target = sha or _head_sha(resolved_repo)
    full = trust._resolve_commit(resolved_repo, target)
    roots = {str(resolved_repo), str(repo), str(Path(repo).resolve())}
    latest: dict[str, Any] | None = None
    for record in trust._read_journal(journal):
        if str(record.get("repo_root") or "") not in roots:
            # Also accept if both resolve to the same path (macOS /var).
            try:
                if Path(str(record.get("repo_root"))).resolve() != resolved_repo:
                    continue
            except OSError:
                continue
        if str(record.get("sha") or "") != full:
            continue
        latest = dict(record)
    return latest


def enforce_continuation(
    *,
    repo: Path | None = None,
    journal: Path | None = None,
    sha: str = "",
    skill: str = "",
    remediating: Any = False,
    remediation_reason: str = "",
    remediation_task: str = "",
) -> GuardDecision:
    """Refuse continuation when trust has recorded block for the target commit.

    Does not invent settlement. Does not re-judge claims. Fail-closed on
    unreadable journals only when an explicit block cannot be ruled out? —
    No: missing journal means no trust block yet → allow (trust is opt-in
    judge; guard only acts on recorded block).

    Authorized remediation is a launch-time continuation override. It never
    rewrites the journal, never reports PASS, and still records the BLOCK
    sha/verdict on the decision and launch receipt.
    """
    try:
        requested, cleaned_reason, cleaned_task = parse_remediation_flags(
            remediating=remediating,
            reason=remediation_reason,
            task=remediation_task,
        )
    except ValueError as exc:
        return GuardDecision(
            allowed=False,
            reason="remediation_flags_invalid",
            remedium=str(exc),
            journal=str(journal or trust.default_journal_path()),
        )

    resolved_journal = (journal or trust.default_journal_path()).expanduser()
    # Guard must never refuse its own inventory / trust workflows circularly
    # in a way that prevents read-only audit. Those skills stay exempt.
    # They do not accept a remediation override — that would look like PASS.
    if skill in META_SKILLS:
        if requested:
            return GuardDecision(
                allowed=False,
                reason="remediation_not_applicable",
                remedium=(
                    "vc-guard: --remediate-trust-block is a launch override for "
                    "repair/admission; trust/guard/init remain read-only audit "
                    "and stay blocked from rewriting the verdict"
                ),
                journal=str(resolved_journal),
                remediation_reason=cleaned_reason,
                remediation_task=cleaned_task,
            )
        return GuardDecision(
            allowed=True,
            reason="meta_skill_exempt",
            remedium="",
            journal=str(resolved_journal),
        )

    resolved_repo = _repo_root(repo)
    if not resolved_journal.is_file():
        if requested:
            return GuardDecision(
                allowed=False,
                reason="remediation_not_applicable",
                remedium=(
                    "vc-guard: --remediate-trust-block requires a recorded trust "
                    "block; no journal is present"
                ),
                journal=str(resolved_journal),
                remediation_reason=cleaned_reason,
                remediation_task=cleaned_task,
            )
        return GuardDecision(
            allowed=True,
            reason="no_trust_journal",
            remedium="",
            journal=str(resolved_journal),
        )

    record = latest_trust_verdict(repo=resolved_repo, journal=resolved_journal, sha=sha)
    if record is None:
        if requested:
            return GuardDecision(
                allowed=False,
                reason="remediation_not_applicable",
                remedium=(
                    "vc-guard: --remediate-trust-block requires a recorded trust "
                    "block for this HEAD; none exists"
                ),
                journal=str(resolved_journal),
                remediation_reason=cleaned_reason,
                remediation_task=cleaned_task,
            )
        return GuardDecision(
            allowed=True,
            reason="no_trust_verdict_for_target",
            remedium="",
            journal=str(resolved_journal),
        )

    verdict = str(record.get("verdict") or "")
    blocking_sha = str(record.get("sha") or "")
    if verdict != "block":
        if requested:
            return GuardDecision(
                allowed=False,
                reason="remediation_not_applicable",
                remedium=(
                    "vc-guard: --remediate-trust-block is refused when the latest "
                    f"trust verdict is {verdict or 'empty'}, not block"
                ),
                blocking_sha=blocking_sha,
                blocking_verdict=verdict,
                journal=str(resolved_journal),
                remediation_reason=cleaned_reason,
                remediation_task=cleaned_task,
            )
        return GuardDecision(
            allowed=True,
            reason=f"trust_verdict_{verdict or 'empty'}",
            remedium="",
            blocking_sha=blocking_sha,
            blocking_verdict=verdict,
            journal=str(resolved_journal),
        )

    claims = record.get("claims") or []
    claim_lines = []
    if isinstance(claims, list):
        for item in claims[:6]:
            if isinstance(item, Mapping):
                claim_lines.append(f"  - {item.get('claim')}: {item.get('evidence')}")
    if requested:
        return GuardDecision(
            allowed=True,
            reason="authorized_trust_block_remediation",
            remedium="",
            blocking_sha=blocking_sha,
            blocking_verdict="block",
            journal=str(resolved_journal),
            remediation_authorized=True,
            remediation_reason=cleaned_reason,
            remediation_task=cleaned_task,
        )
    remedium = (
        f"vc-guard: refuse continuation — trust recorded block on {blocking_sha[:12]}.\n"
        f"Remedium:\n"
        f"  1. Read journal entry: {resolved_journal}\n"
        f"  2. Fix the falsified claims (agent fairness, completeness, runtime).\n"
        f"  3. Commit the fix with legal Authored-By matching the executor.\n"
        f"  4. Re-run: python -m vibecrafted_core.trust inspect <new-sha>\n"
        f"  5. Explicitly note a new verdict (pass/pass-with-gaps) — never imply pass.\n"
        f"  Authorized continuation (does not rewrite this BLOCK):\n"
        f"     vibecrafted <skill> <agent> --remediate-trust-block "
        f"--remediation-task repair|admission --remediation-reason TEXT\n"
        f"Blocked claims:\n"
        + ("\n".join(claim_lines) if claim_lines else "  (see journal)")
    )
    return GuardDecision(
        allowed=False,
        reason="trust_block",
        remedium=remedium,
        blocking_sha=blocking_sha,
        blocking_verdict="block",
        journal=str(resolved_journal),
    )


def _parser() -> argparse.ArgumentParser:
    """Build the `python -m vibecrafted_core.guard` CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m vibecrafted_core.guard",
        description="vc-guard inventory and trust-block enforcement helper.",
    )
    parser.add_argument("--journal", type=Path, default=None)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory", help="List named gates and coverage gaps")
    check = commands.add_parser(
        "check",
        help="Allow/refuse continuation based on trust journal block for HEAD/sha",
    )
    check.add_argument("--sha", default="")
    check.add_argument("--skill", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: dispatch `inventory`/`check`, print JSON, exit 0/1/2."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "inventory":
            print(json.dumps(inventory(), indent=2, ensure_ascii=False))
            return 0
        decision = enforce_continuation(
            repo=args.repo,
            journal=args.journal,
            sha=args.sha,
            skill=args.skill,
        )
        print(json.dumps(decision.to_payload(), indent=2, ensure_ascii=False))
        if decision.allowed:
            return 0
        print(decision.remedium, file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"vc-guard: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
