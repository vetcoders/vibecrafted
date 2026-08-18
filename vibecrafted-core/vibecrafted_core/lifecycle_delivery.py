"""Lifecycle-stage delivery proof/seal bridge.

Closes the f-gap: when a lifecycle stage reaches a validated report with a
matching claim digest, write the delivery-kernel artifacts that
``control_plane._delivery_axes_from_run_dir`` and ``settle_payload`` require
for ``FINALIZED``.

Contract rule 2 (SETTLEMENT_CONTRACT.md): ``exit_code==0`` alone NEVER
produces a seal. Guards refuse bare success without a validated report
artifact and refuse claim mismatches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .delivery.model import (
    DeliveryProofContract,
    DeliveryState,
    ExecutionEnvelope,
    ExecutionEvidence,
    ExecutionState,
    ProofResult,
    ProofState,
)
from .delivery.proof import run_proof
from .delivery.scope import ScopeEvidence, qualify_scope
from .delivery.store import DeliveryStore, atomic_write_json
from .report_contract import CLAIM_COMPLETED, parse_report_path
from .settlement import claim_digest_from_payload

__all__ = [
    "StageSealResult",
    "claim_digest_for_text",
    "mission_claim_digest",
    "report_claim_matches",
    "resettle_retained_snapshots",
    "try_grant_lifecycle_stage_seal",
]

CLAIM_DIGEST_RE = re.compile(r"^[0-9a-f]{16}$")
ZERO_DIGEST = "sha256:" + "0" * 64
EventSink = Callable[[str, str, str, dict[str, Any]], Mapping[str, Any]]

# The proof executor scrubs the subject's environment down to _SAFE_ENV_KEYS —
# HOME, PATH, TERM and friends — because a proof that inherits ambient state is
# not a proof. PYTHONPATH is scrubbed with the rest, and rightly so. But the
# subject was declared as `-m vibecrafted_core.lifecycle_delivery`: a module
# invocation resolved through exactly the sys.path the scrub had just removed.
# On any host where the package reaches the interpreter through PYTHONPATH
# rather than site-packages — which is how the installed runtime ships it and
# how the test suite imports it — the subject died with ModuleNotFoundError,
# exit 1, and the kernel wrote `proof.failed: subject execution failed`. Every
# run it judged then settled `failed` regardless of what the worker had done.
#
# The answer is not to widen the scrub. It is to stop depending on ambient
# state at all: carry the package's own location as an explicit argument, so the
# import path is digest-covered contract evidence like every other input rather
# than something the environment is trusted to remember.
_SUBJECT_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from vibecrafted_core.lifecycle_delivery import _main; "
    "raise SystemExit(_main(sys.argv[2:]))"
)


def _package_search_path() -> str:
    """Directory that must be on ``sys.path`` for ``vibecrafted_core`` to import."""
    return str(Path(__file__).resolve().parent.parent)


@dataclass(frozen=True)
class StageSealResult:
    """Outcome of a lifecycle-stage seal attempt (grant or explicit refuse)."""

    granted: bool
    proof_state: str
    delivery_state: str
    reason: str
    claim_digest: str
    seal_id: str = ""
    proof_id: str = ""

    def axes_payload(self) -> dict[str, str]:
        """Just the proof/delivery axes, for merging into a stage or run record."""
        return {
            "proof_state": self.proof_state,
            "delivery_state": self.delivery_state,
        }

    def to_payload(self) -> dict[str, Any]:
        """Full JSON-serializable representation of this seal result."""
        return {
            "granted": self.granted,
            "proof_state": self.proof_state,
            "delivery_state": self.delivery_state,
            "reason": self.reason,
            "claim_digest": self.claim_digest,
            "seal_id": self.seal_id,
            "proof_id": self.proof_id,
        }


def claim_digest_for_text(text: str) -> str:
    """Stable 16-hex claim digest from free-form mission/brief text."""
    material = str(text or "").strip().encode("utf-8")
    if not material:
        return ""
    return hashlib.sha256(material).hexdigest()[:16]


def mission_claim_digest(
    *,
    mission_text: str = "",
    payload: Mapping[str, Any] | None = None,
) -> str:
    """Prefer an explicit payload digest; else hash mission/brief text."""
    if payload is not None:
        existing = claim_digest_from_payload(payload)
        if existing:
            return existing
    return claim_digest_for_text(mission_text)


def _file_sha256(path: Path) -> str:
    """``sha256:<hex>`` of a file's contents, or the all-zero digest if it's missing."""
    if not path.is_file():
        return ZERO_DIGEST
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _text_sha256(text: str) -> str:
    """``sha256:<hex>`` of a UTF-8 encoded text string."""
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def report_claim_matches(
    report_path: str | Path,
    *,
    mission_digest: str,
) -> tuple[bool, str]:
    """Return (ok, reason) for report↔mission claim match.

    Match rules (fail closed):
    - report must exist and parse with valid frontmatter
    - agent claim_status must be in the completed vocabulary
    - at least one canonical digest field must be present
    - every present digest must be a canonical 16-hex digest equal to the
      mission digest
    """
    path = Path(report_path)
    if not path.is_file():
        return False, "report_missing"
    try:
        frontmatter = parse_report_path(path)
    except Exception:  # noqa: BLE001 — fail closed on any parse path
        return False, "report_frontmatter_invalid"
    if not frontmatter.ok:
        return False, frontmatter.errors[0] if frontmatter.errors else "report_invalid"
    claim = str(frontmatter.claim_status or "").strip().lower()
    if claim not in CLAIM_COMPLETED:
        if not claim:
            return False, "report_claim_empty"
        return False, f"report_claim_not_completed:{claim}"
    mission = str(mission_digest or "").strip()
    if not mission:
        return False, "mission_claim_empty"
    if not CLAIM_DIGEST_RE.fullmatch(mission):
        return False, "mission_claim_digest_invalid"
    fields = frontmatter.fields or {}
    declared: list[tuple[str, str]] = []
    for key in ("claim_digest", "mission_digest", "brief_digest"):
        raw = str(fields.get(key) or "").strip()
        if not raw:
            continue
        if not CLAIM_DIGEST_RE.fullmatch(raw):
            return False, f"claim_digest_invalid:{key}"
        declared.append((key, raw))
        if raw != mission:
            return False, f"claim_digest_mismatch:{key}"
    if not declared:
        return False, "report_claim_digest_missing"
    return True, "claim_match"


def _report_verification_payload(report: Path, mission_digest: str) -> dict[str, Any]:
    """Verified-claim proof payload for a report; raises ValueError on any mismatch reason."""
    ok, reason = report_claim_matches(report, mission_digest=mission_digest)
    if not ok:
        raise ValueError(reason)
    return {
        "claim_digest": mission_digest,
        "report_sha256": _file_sha256(report),
        "validated": True,
    }


def _git_value(root: Path, *args: str) -> str:
    """Run a git subcommand in ``root`` and return trimmed stdout, or "" on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _repo_identity(root: Path) -> str:
    """``owner/repo`` derived from the origin remote URL, else the directory name."""
    remote = _git_value(root, "config", "--get", "remote.origin.url")
    if remote:
        normalized = remote.removesuffix(".git").replace(":", "/")
        pieces = [piece for piece in normalized.split("/") if piece]
        if len(pieces) >= 2:
            return "/".join(pieces[-2:])
    return root.name or "unversioned-checkout"


def _upstream_relation(root: Path) -> tuple[str, dict[str, int]]:
    """Upstream branch name and its ahead/behind commit counts vs HEAD; ("", {}) if none."""
    upstream = _git_value(root, "rev-parse", "--abbrev-ref", "@{upstream}")
    if not upstream:
        return "", {}
    raw = _git_value(root, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    parts = raw.split()
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return upstream, {}
    return upstream, {"ahead": int(parts[0]), "behind": int(parts[1])}


def _persist_proof(
    store: DeliveryStore,
    *,
    envelope: ExecutionEnvelope,
    contract: DeliveryProofContract,
    proof: ProofResult,
) -> None:
    """Write the envelope, contract, per-evidence-role executions, and proof result to disk."""
    store.write_execution_envelope(envelope)
    store.write_proof_contract(contract)
    sequence_by_role: dict[str, int] = {}
    for payload in proof.evidence:
        evidence = ExecutionEvidence.from_payload(payload)
        sequence_by_role[evidence.role] = sequence_by_role.get(evidence.role, 0) + 1
        store.write_execution(
            evidence,
            role=evidence.role,
            sequence=sequence_by_role[evidence.role],
        )
    store.write_assertions(
        proof.assertion_results,
        source_digests={"proof": proof.content_digest()},
    )
    store.write_negative_controls(
        proof.negative_control_results,
        source_digests={"proof": proof.content_digest()},
    )
    store.write_proof_result(proof)


def try_grant_lifecycle_stage_seal(
    run_dir: str | Path,
    *,
    run_id: str,
    lifecycle_id: str = "",
    stage_id: str = "",
    report_path: str | Path | None = None,
    transcript_path: str | Path | None = None,
    mission_text: str = "",
    mission_digest: str = "",
    artifact_ok: bool = False,
    exit_code: Any = None,
    repo_root: str | Path | None = None,
    agent: str = "",
    baseline_head: str = "",
    final_head: str = "",
    scoped_dirty_paths: tuple[str, ...] = (),
    baseline_status_lines: tuple[str, ...] = (),
    event_sink: EventSink | None = None,
) -> StageSealResult:
    """Declare proof → verify claim → grant seal for a lifecycle stage run dir.

    The validated report is executed as a real proof subject, then qualified
    at checkout scope. Only ``ship.seal_delivery_run`` may issue the seal.
    Refusal paths never invent FINALIZED.
    """
    target = Path(run_dir)
    rid = str(run_id or "").strip()
    digest = str(mission_digest or "").strip() or claim_digest_for_text(mission_text)

    # Guard: never from bare exit alone; never without validated report.
    if not rid:
        return StageSealResult(
            granted=False,
            proof_state=ProofState.UNDECLARED.value,
            delivery_state=DeliveryState.UNVERIFIED.value,
            reason="run_id_required",
            claim_digest=digest,
        )
    if not artifact_ok:
        return StageSealResult(
            granted=False,
            proof_state=ProofState.UNDECLARED.value,
            delivery_state=DeliveryState.UNVERIFIED.value,
            reason="artifact_not_ok",
            claim_digest=digest,
        )
    if exit_code not in (0, "0"):
        return StageSealResult(
            granted=False,
            proof_state=ProofState.UNDECLARED.value,
            delivery_state=DeliveryState.UNVERIFIED.value,
            reason="execution_not_clean",
            claim_digest=digest,
        )
    report = Path(report_path) if report_path else target / "report.md"
    if not report.is_file():
        return StageSealResult(
            granted=False,
            proof_state=ProofState.UNDECLARED.value,
            delivery_state=DeliveryState.UNVERIFIED.value,
            reason="exit_0_without_report"
            if exit_code in (0, "0", None)
            else "report_missing",
            claim_digest=digest,
        )
    matched, match_reason = report_claim_matches(report, mission_digest=digest)
    if not matched:
        return StageSealResult(
            granted=False,
            proof_state=ProofState.UNDECLARED.value,
            delivery_state=DeliveryState.UNVERIFIED.value,
            reason=match_reason,
            claim_digest=digest,
        )

    target.mkdir(parents=True, exist_ok=True)
    canonical_report = target / "report.md"
    if report.resolve() != canonical_report.resolve():
        shutil.copy2(report, canonical_report)
    transcript = Path(transcript_path) if transcript_path else target / "transcript.log"
    canonical_transcript = target / "transcript.log"
    if transcript.is_file() and transcript.resolve() != canonical_transcript.resolve():
        shutil.copy2(transcript, canonical_transcript)

    root = Path(repo_root or Path.cwd()).expanduser().resolve()
    observed_head = _git_value(root, "rev-parse", "HEAD")
    resolved_final = str(final_head or observed_head or "unversioned").strip()
    resolved_baseline = str(baseline_head or resolved_final).strip()
    branch = _git_value(root, "branch", "--show-current") or "detached"
    mission_path = target / "proof" / "mission-claim.json"
    atomic_write_json(
        mission_path,
        {
            "claim_digest": digest,
            "lifecycle_id": str(lifecycle_id or rid),
            "run_id": rid,
            "stage_id": str(stage_id or "stage"),
        },
    )
    expected_path = target / "proof" / "expected-report-verification.json"
    atomic_write_json(
        expected_path,
        _report_verification_payload(canonical_report, digest),
    )
    status_material = json.dumps(
        sorted(str(item) for item in baseline_status_lines),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    upstream_ref, upstream_relation = _upstream_relation(root)
    envelope = ExecutionEnvelope(
        schema=ExecutionEnvelope.SCHEMA,
        agent=str(agent or "lifecycle-worker"),
        repo=_repo_identity(root),
        root=str(root),
        branch=branch,
        expected_head=resolved_baseline,
        upstream_ref=upstream_ref,
        upstream_relation=upstream_relation,
        dirty_policy="living-tree-scoped",
        baseline_status_digest=_text_sha256(status_material),
        protected_paths=(),
        owned_paths=tuple(sorted(str(item) for item in scoped_dirty_paths)),
        brief_path=str(mission_path),
        brief_sha256=_file_sha256(mission_path),
    )
    contract = DeliveryProofContract(
        schema=DeliveryProofContract.SCHEMA,
        id=f"lifecycle-report-{rid}",
        execution_envelope_sha256=envelope.content_digest(),
        subject={
            "producer_id": "vibecrafted.lifecycle.report-verifier",
            "public_surface": sys.executable,
            "argv": [
                sys.executable,
                "-c",
                _SUBJECT_BOOTSTRAP,
                _package_search_path(),
                "verify-report",
                "--report",
                str(canonical_report),
                "--mission-digest",
                digest,
            ],
            "cwd": str(root),
            "expected_exit": 0,
            "timeout_seconds": 30,
        },
        witness={
            "input": str(canonical_report),
            "expected_outcome": "validated report binds the exact mission claim digest",
        },
        oracle=None,
        assertion={
            "id": "validated-report-claim-match",
            "kind": "normalized-structural-equality",
            "actual": "subject.stdout",
            "expected": str(expected_path),
        },
        negative_controls=(
            {"id": "corrupt-report-result", "mutation": "corrupt_isolated_actual"},
        ),
        delivery_scope="checkout",
        integration_target=None,
        runtime_probes=(),
    )

    store = DeliveryStore(target)
    try:
        proof = run_proof(
            contract,
            run_id=rid,
            relevant_paths=(canonical_report, mission_path, expected_path),
        )
        _persist_proof(store, envelope=envelope, contract=contract, proof=proof)
        record = qualify_scope(
            proof,
            contract,
            ScopeEvidence(
                repo=envelope.repo,
                repo_root=str(root),
                branch=branch,
                baseline_head=resolved_baseline,
                final_head=resolved_final,
                commit_range=f"{resolved_baseline}..{resolved_final}",
                execution_state=ExecutionState.EXITED,
                execution_exit_code=0,
                artifact_ok=True,
                scoped_dirty_paths=tuple(sorted(scoped_dirty_paths)),
            ),
        )
        store.write_delivery_record(record)
        atomic_write_json(
            target / "control-plane-snapshot.json",
            {
                "run_id": rid,
                "state": "report_validated",
                "proof_state": proof.state.value,
                "delivery_state": record.state.value,
                "claim_digest": digest,
            },
        )
        from .ship import seal_delivery_run

        ship_result = (
            seal_delivery_run(
                target,
                run_id=rid,
                lifecycle_id=str(lifecycle_id or rid),
                cut_id=str(stage_id or "stage"),
            )
            if event_sink is None
            else seal_delivery_run(
                target,
                run_id=rid,
                lifecycle_id=str(lifecycle_id or rid),
                cut_id=str(stage_id or "stage"),
                event_sink=event_sink,
            )
        )
    except Exception as exc:  # noqa: BLE001 — lifecycle must fail closed
        return StageSealResult(
            granted=False,
            proof_state=ProofState.UNDECLARED.value,
            delivery_state=DeliveryState.UNVERIFIED.value,
            reason=f"delivery_kernel_error:{type(exc).__name__}:{exc}",
            claim_digest=digest,
        )

    if proof.state is not ProofState.PASSED:
        return StageSealResult(
            granted=False,
            proof_state=proof.state.value,
            delivery_state=DeliveryState.UNVERIFIED.value,
            reason="; ".join(proof.refusal_reasons) or "proof_not_passed",
            claim_digest=digest,
            proof_id=proof.proof_id,
        )
    if ship_result.seal is None:
        return StageSealResult(
            granted=False,
            proof_state=proof.state.value,
            delivery_state=ship_result.delivery_state.value,
            reason="; ".join(ship_result.refusal_reasons) or "seal_refused",
            claim_digest=digest,
            proof_id=proof.proof_id,
        )

    seal = ship_result.seal

    # Stamp claim_digest on meta when present so settlement has claim surface.
    meta_path = target / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                meta["claim_digest"] = digest
                meta["proof_state"] = ProofState.PASSED.value
                meta["delivery_state"] = DeliveryState.SEALED.value
                meta["lifecycle_seal"] = {
                    "seal_id": seal.seal_id,
                    "proof_id": proof.proof_id,
                    "reason": match_reason,
                    "issuer": seal.issuer,
                }
                atomic_write_json(meta_path, meta)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    return StageSealResult(
        granted=True,
        proof_state=ProofState.PASSED.value,
        delivery_state=DeliveryState.SEALED.value,
        reason=match_reason,
        claim_digest=digest,
        seal_id=seal.seal_id,
        proof_id=proof.proof_id,
    )


def resettle_retained_snapshots(
    *,
    runs_dir: str | Path | None = None,
    force: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Re-run settlement over retained control-plane snapshots.

    Honest backfill: does not invent either automatic FINALIZED evidence source.
    Sealed finalization requires existing proof/seal evidence; self-attested
    finalization requires an existing validated, run-bound report carrying the
    worker's explicit ``finalized: true`` and non-empty ``claim``. A traced
    operator waive remains a separate explicit override. Bare exit 0 remains
    needs_attention. Idempotent when re-run after convergence.
    """
    from .control_plane import control_plane_home, run_snapshot_dir
    from .settlement import settle_payload

    root = Path(runs_dir) if runs_dir is not None else run_snapshot_dir()
    if not root.is_dir():
        return {
            "ok": True,
            "scanned": 0,
            "rewritten": 0,
            "unchanged": 0,
            "skipped": 0,
            "before": {"f": 0, "x": 0, "n": 0, "invalid": 0},
            "after": {"f": 0, "x": 0, "n": 0, "invalid": 0},
            "dry_run": dry_run,
            "runs_dir": str(root),
            "home": str(control_plane_home()),
        }

    before = {"f": 0, "x": 0, "n": 0, "invalid": 0, "none": 0}
    after = {"f": 0, "x": 0, "n": 0, "invalid": 0, "none": 0}
    rewritten = 0
    unchanged = 0
    skipped = 0
    scanned = 0

    def _count(bucket: dict[str, int], verdict: str | None) -> None:
        """Bump the f/x/n(/invalid/none) counter matching a settlement verdict string."""
        raw = str(verdict or "").strip().lower()
        if raw == "finalized":
            bucket["f"] += 1
        elif raw == "failed":
            bucket["x"] += 1
        elif raw == "invalid":
            bucket["x"] += 1
            bucket["invalid"] += 1
        elif raw == "needs_attention":
            bucket["n"] += 1
        else:
            bucket["none"] += 1

    for path in sorted(root.glob("*.json")):
        scanned += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            skipped += 1
            continue
        if not isinstance(payload, dict):
            skipped += 1
            continue
        prev_verdict = str(payload.get("settlement_verdict") or "")
        if not prev_verdict and isinstance(payload.get("settlement"), dict):
            prev_verdict = str(payload["settlement"].get("verdict") or "")
        _count(before, prev_verdict)

        settlement = settle_payload(payload, force=force, source="resettle")
        if settlement is None:
            after["none"] += 1
            unchanged += 1
            continue
        new_flat = settlement.to_payload()
        new_nested = {
            "verdict": settlement.verdict.value,
            "reason": settlement.reason,
            "settled_at": settlement.settled_at,
            "source": settlement.source,
            "claim_digest": settlement.claim_digest,
            "waived": settlement.waived,
            "tui": settlement.tui_key,
        }
        _count(after, settlement.verdict.value)
        # Idempotency: skip write when verdict/reason/tui already match.
        same = (
            str(payload.get("settlement_verdict") or "") == settlement.verdict.value
            and str(payload.get("settlement_tui") or "") == settlement.tui_key
            and str(payload.get("settlement_reason") or "") == settlement.reason
        )
        if same:
            unchanged += 1
            continue
        if dry_run:
            rewritten += 1
            continue
        payload.update(new_flat)
        payload["settlement"] = new_nested
        atomic_write_json(path, payload)
        rewritten += 1

    return {
        "ok": True,
        "scanned": scanned,
        "rewritten": rewritten,
        "unchanged": unchanged,
        "skipped": skipped,
        "before": {k: before[k] for k in ("f", "x", "n", "invalid")},
        "after": {k: after[k] for k in ("f", "x", "n", "invalid")},
        "dry_run": dry_run,
        "runs_dir": str(root),
        "force": force,
    }


def _main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: ``verify-report`` subcommand used as the seal's proof subject."""
    parser = argparse.ArgumentParser(
        prog="python -m vibecrafted_core.lifecycle_delivery"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify-report")
    verify.add_argument("--report", required=True)
    verify.add_argument("--mission-digest", required=True)
    args = parser.parse_args(argv)
    if args.command != "verify-report":
        return 2
    report = Path(args.report).expanduser().resolve()
    try:
        payload = _report_verification_payload(report, str(args.mission_digest))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by proof subprocess
    raise SystemExit(_main())
