"""Delivery scope qualification: where does the proven effect actually exist?

A green proof answers *did the declared assertion hold*. It cannot answer
*where*. This module is the qualification layer that turns a ``ProofResult``
plus observed scope evidence into a ``DeliveryRecord``.

Spec §7.7 (record fields) and §8 (the five scopes, each with its own evidence
requirements). The load-bearing rule from §8: a scope may only be promoted by a
new proof and a new seal. Editing the label must be structurally meaningless —
so every scope above ``checkout`` demands evidence that a checkout-local test
cannot fabricate, and a declared scope that fails its own requirements falls
back to what was actually checked rather than being reported as delivered.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .model import (
    DeliveryProofContract,
    DeliveryRecord,
    DeliveryState,
    ExecutionState,
    ProofResult,
    ProofState,
    delivery_transition_allowed,
)

SCOPE_NONE = "none"
"""Checked scope when not even the checkout base could be established."""

_GIT_TIMEOUT_SECONDS = 60.0


class DeliveryScope(str, Enum):
    """The five delivery scopes of spec §8."""

    CHECKOUT = "checkout"
    BRANCH = "branch"
    INTEGRATED = "integrated"
    INSTALLED = "installed"
    LIVE = "live"


@dataclass(frozen=True)
class RuntimeProbe:
    """One observation of a runtime target, recorded as separate evidence."""

    probe_id: str
    target: str
    performed: bool
    passed: bool
    observed_effect: str | None = None
    evidence_sha256: str | None = None
    destructive: bool = False

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-serializable representation of this probe."""
        return {
            "probe_id": self.probe_id,
            "target": self.target,
            "performed": self.performed,
            "passed": self.passed,
            "observed_effect": self.observed_effect,
            "evidence_sha256": self.evidence_sha256,
            "destructive": self.destructive,
        }


@dataclass(frozen=True)
class InstalledEvidence:
    """Evidence that an *installed* artifact — not a repo path — was exercised."""

    resolved_path: str
    provenance_marker: str | None = None
    provenance_commit: str | None = None
    smoke: RuntimeProbe | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-serializable representation of this installed-evidence record."""
        return {
            "resolved_path": self.resolved_path,
            "provenance_marker": self.provenance_marker,
            "provenance_commit": self.provenance_commit,
            "smoke": self.smoke.to_payload() if self.smoke is not None else None,
        }


@dataclass(frozen=True)
class ScopeEvidence:
    """Observed facts a caller presents for qualification.

    Everything here is an *observation*, never a claim: ``artifact_ok`` keeps
    its legacy meaning (required artifacts exist) and by itself never promotes
    delivery (§14, T13).
    """

    repo: str
    repo_root: str
    branch: str
    baseline_head: str
    final_head: str
    commit_range: str
    execution_state: ExecutionState = ExecutionState.EXITED
    execution_exit_code: int | None = 0
    artifact_ok: bool = False
    tree_hash: str | None = None
    scoped_dirty_paths: tuple[str, ...] = ()
    integration_target: str | None = None
    fetch_remote: str | None = None
    installed: InstalledEvidence | None = None
    runtime_probes: tuple[RuntimeProbe, ...] = ()
    extra_target_identity: Mapping[str, Any] = field(default_factory=dict)


def qualify_scope(
    proof_result: ProofResult | None,
    contract: DeliveryProofContract,
    evidence: ScopeEvidence,
    *,
    declared_scope: str | None = None,
    record_id: str | None = None,
    recorded_at: str | None = None,
) -> DeliveryRecord:
    """Qualify a proof against a declared delivery scope.

    Returns a ``DeliveryRecord`` that is ``delivered`` only when the execution
    exited cleanly, the proof passed, and the declared scope's own evidence
    requirements were met. Every other outcome is ``unverified`` with explicit
    refusal reasons — there is no warning tier.
    """

    declared = declared_scope or contract.delivery_scope
    refusals: list[str] = []

    if declared_scope is not None and declared_scope != contract.delivery_scope:
        refusals.append(
            f"scope qualification FAIL: declared scope {declared_scope!r} does not "
            f"match contract delivery_scope {contract.delivery_scope!r}"
        )

    try:
        declared_enum: DeliveryScope | None = DeliveryScope(declared)
    except ValueError:
        declared_enum = None
        refusals.append(
            f"scope qualification FAIL: unknown delivery scope {declared!r}"
        )

    refusals.extend(_execution_refusals(evidence))
    refusals.extend(_proof_refusals(proof_result))

    base_ok = not refusals and _checkout_base_ok(evidence, refusals)
    scope_ok = False
    if base_ok and declared_enum is not None:
        scope_refusals = _scope_refusals(declared_enum, evidence)
        refusals.extend(scope_refusals)
        scope_ok = not scope_refusals

    checked = (
        declared
        if scope_ok
        else (DeliveryScope.CHECKOUT.value if base_ok else SCOPE_NONE)
    )
    if scope_ok and declared_enum is DeliveryScope.CHECKOUT:
        checked = DeliveryScope.CHECKOUT.value

    target_identity = _target_identity(declared, evidence)
    commit_provenance = _commit_provenance(evidence, proof_result)
    probe_results = tuple(probe.to_payload() for probe in _all_probes(evidence))

    state = DeliveryState.UNVERIFIED
    if not refusals and proof_result is not None:
        allowed = delivery_transition_allowed(
            current=DeliveryState.UNVERIFIED,
            target=DeliveryState.DELIVERED,
            execution_state=evidence.execution_state,
            execution_exit_code=evidence.execution_exit_code,
            proof_state=proof_result.state,
        )
        if allowed:
            state = DeliveryState.DELIVERED
        else:
            refusals.append("delivery transition refused by state-axis legality")

    resolved_id = record_id or _derive_record_id(
        proof_result=proof_result,
        declared=declared,
        checked=checked,
        target_identity=target_identity,
        commit_provenance=commit_provenance,
    )

    return DeliveryRecord(
        schema=DeliveryRecord.SCHEMA,
        record_id=resolved_id,
        proof_result_sha256=(
            proof_result.content_digest() if proof_result is not None else ""
        ),
        declared_scope=declared,
        checked_scope=checked,
        target_identity=target_identity,
        commit_provenance=commit_provenance,
        runtime_probe_results=probe_results,
        state=state,
        refusal_reasons=tuple(dict.fromkeys(refusals)),
        recorded_at=recorded_at or datetime.now(UTC).isoformat(timespec="milliseconds"),
    )


# ---------------------------------------------------------------------------
# axis refusals
# ---------------------------------------------------------------------------


def _execution_refusals(evidence: ScopeEvidence) -> list[str]:
    """T12: interrupted / timed_out / partial runs never advance delivery."""
    refusals: list[str] = []
    state = evidence.execution_state
    if state is not ExecutionState.EXITED:
        refusals.append(
            f"execution {state.value}: delivery cannot advance from a run that "
            "did not reach a clean exit"
        )
        return refusals
    if evidence.execution_exit_code != 0:
        refusals.append(
            f"execution exited with code {evidence.execution_exit_code}: "
            "delivery cannot advance"
        )
    return refusals


def _proof_refusals(proof_result: ProofResult | None) -> list[str]:
    """T13: artifacts without a proof are presence, never delivery."""
    if proof_result is None:
        return ["no_proof"]
    refusals: list[str] = []
    if proof_result.state is not ProofState.PASSED:
        refusals.append(
            f"proof.{proof_result.state.value}: delivery requires a passed proof"
        )
    if not proof_result.subject_executed:
        refusals.append("proof did not execute the subject")
    if not proof_result.assertion_consumed_subject_output:
        refusals.append("assertion did not consume subject output")
    if _relevant_paths_drifted(proof_result):
        refusals.append("relevant path drift recorded during proof")
    return refusals


def _relevant_paths_drifted(proof_result: ProofResult) -> bool:
    """Return whether the proof's relevant_path_digests assertion result was marked unstable."""
    for item in proof_result.assertion_results:
        if item.get("kind") == "relevant_path_digests" and item.get("stable") is False:
            return True
    return False


def _checkout_base_ok(evidence: ScopeEvidence, refusals: list[str]) -> bool:
    """Every scope stands on a real checkout: repo identity, branch, HEAD."""
    before = len(refusals)
    root = Path(evidence.repo_root).expanduser()
    if not root.is_dir():
        refusals.append(
            f"scope qualification FAIL: repo root {evidence.repo_root!r} is not a directory"
        )
    for name, value in (
        ("repo", evidence.repo),
        ("branch", evidence.branch),
        ("final_head", evidence.final_head),
    ):
        if not value or not str(value).strip():
            refusals.append(f"scope qualification FAIL: {name} is not declared")
    return len(refusals) == before


def _scope_refusals(scope: DeliveryScope, evidence: ScopeEvidence) -> list[str]:
    """Dispatch to the per-scope refusal checker for the declared DeliveryScope."""
    if scope is DeliveryScope.CHECKOUT:
        return []
    if scope is DeliveryScope.BRANCH:
        return _branch_refusals(evidence)
    if scope is DeliveryScope.INTEGRATED:
        return _integrated_refusals(evidence)
    if scope is DeliveryScope.INSTALLED:
        return _installed_refusals(evidence)
    return _live_refusals(evidence)


def _branch_refusals(evidence: ScopeEvidence) -> list[str]:
    """§8 `branch`: the commit must be reachable from the local branch ref."""
    root = Path(evidence.repo_root)
    if not _commit_reachable(root, evidence.final_head, evidence.branch):
        return [
            (
                f"scope qualification FAIL: commit {evidence.final_head} is not reachable "
                f"from local branch {evidence.branch!r}"
            )
        ]
    return []


def _integrated_refusals(evidence: ScopeEvidence) -> list[str]:
    """T17/§8 `integrated`: reachable from integration_target after a fresh fetch."""
    target = evidence.integration_target
    if not target:
        return [
            (
                "scope qualification FAIL: scope 'integrated' requires a declared "
                "integration_target"
            )
        ]
    root = Path(evidence.repo_root)
    if evidence.fetch_remote:
        fetched = _git(root, "fetch", "--quiet", evidence.fetch_remote)
        if fetched.returncode != 0:
            return [
                (
                    f"scope qualification FAIL: fetch from {evidence.fetch_remote!r} failed: "
                    f"{fetched.stderr.strip()}"
                )
            ]
    if not _commit_reachable(root, evidence.final_head, target):
        return [
            (
                f"scope qualification FAIL: commit {evidence.final_head} is not reachable "
                f"from integration target {target!r} after fetch"
            )
        ]
    return []


def _installed_refusals(evidence: ScopeEvidence) -> list[str]:
    """T15/§8 `installed`: an installed artifact, not a path inside the repo."""
    installed = evidence.installed
    if installed is None:
        return [
            (
                "scope qualification FAIL: scope 'installed' requires resolved installed "
                "path, provenance marker and a public-entrypoint smoke"
            )
        ]

    refusals: list[str] = []
    resolved = Path(installed.resolved_path).expanduser()
    root = Path(evidence.repo_root).expanduser()
    try:
        resolved_abs = resolved.resolve()
        root_abs = root.resolve()
    except OSError:  # defensive on exotic filesystems
        resolved_abs, root_abs = resolved, root

    if not installed.resolved_path.strip():
        refusals.append("scope qualification FAIL: installed path is not resolved")
    elif resolved_abs == root_abs or root_abs in resolved_abs.parents:
        refusals.append(
            f"scope qualification FAIL: resolved executable {resolved_abs} lies inside "
            f"the repo root {root_abs} — that is the checkout, not an installation"
        )

    if not installed.provenance_marker or not installed.provenance_commit:
        refusals.append(
            "scope qualification FAIL: installed scope requires a provenance marker "
            "binding the build to a commit"
        )
    elif installed.provenance_commit != evidence.final_head:
        refusals.append(
            f"scope qualification FAIL: provenance commit {installed.provenance_commit} "
            f"does not match delivered head {evidence.final_head}"
        )

    smoke = installed.smoke
    if smoke is None or not smoke.performed:
        refusals.append(
            "scope qualification FAIL: installed scope requires a real smoke through "
            "the public entrypoint"
        )
    elif smoke.destructive:
        refusals.append(
            f"scope qualification FAIL: smoke probe {smoke.probe_id!r} is destructive"
        )
    elif not smoke.passed:
        refusals.append(
            f"scope qualification FAIL: smoke probe {smoke.probe_id!r} did not pass"
        )
    return refusals


def _live_refusals(evidence: ScopeEvidence) -> list[str]:
    """T16/§8 `live`: a green local test can never satisfy this scope."""
    probes = evidence.runtime_probes
    if not probes:
        return [
            (
                "scope qualification FAIL: scope 'live' requires a runtime probe against "
                "the declared target"
            )
        ]
    refusals: list[str] = []
    for probe in probes:
        if probe.destructive:
            refusals.append(
                f"scope qualification FAIL: runtime probe {probe.probe_id!r} is "
                "destructive and is forbidden (§13)"
            )
            continue
        if not probe.performed:
            refusals.append(
                f"scope qualification FAIL: runtime probe {probe.probe_id!r} was not performed"
            )
        elif not probe.passed:
            refusals.append(
                f"scope qualification FAIL: runtime probe {probe.probe_id!r} did not "
                "observe the expected effect"
            )
        if not probe.target.strip():
            refusals.append(
                f"scope qualification FAIL: runtime probe {probe.probe_id!r} does not "
                "identify a target"
            )
    return refusals


# ---------------------------------------------------------------------------
# record payload assembly
# ---------------------------------------------------------------------------


def _all_probes(evidence: ScopeEvidence) -> tuple[RuntimeProbe, ...]:
    """Return declared runtime_probes plus the installed-evidence smoke probe, if any."""
    probes = list(evidence.runtime_probes)
    if evidence.installed is not None and evidence.installed.smoke is not None:
        probes.append(evidence.installed.smoke)
    return tuple(probes)


def _target_identity(declared: str, evidence: ScopeEvidence) -> dict[str, Any]:
    """Assemble the DeliveryRecord.target_identity payload from scope evidence."""
    identity: dict[str, Any] = {
        "repo": evidence.repo,
        "root": evidence.repo_root,
        "branch": evidence.branch,
        "declared_scope": declared,
        "artifact_ok": evidence.artifact_ok,
        "integration_target": evidence.integration_target,
        "installed": (
            evidence.installed.to_payload() if evidence.installed is not None else None
        ),
        "runtime_targets": tuple(probe.target for probe in evidence.runtime_probes),
    }
    identity.update(dict(evidence.extra_target_identity))
    return identity


def _commit_provenance(
    evidence: ScopeEvidence, proof_result: ProofResult | None
) -> dict[str, Any]:
    """Assemble the DeliveryRecord.commit_provenance payload from scope and proof evidence."""
    return {
        "baseline_head": evidence.baseline_head,
        "final_head": evidence.final_head,
        "commit_range": evidence.commit_range,
        "tree_hash": evidence.tree_hash,
        "scoped_dirty_paths": tuple(evidence.scoped_dirty_paths),
        "execution_state": evidence.execution_state.value,
        "execution_exit_code": evidence.execution_exit_code,
        "proof_state": proof_result.state.value if proof_result is not None else None,
    }


def _derive_record_id(
    *,
    proof_result: ProofResult | None,
    declared: str,
    checked: str,
    target_identity: Mapping[str, Any],
    commit_provenance: Mapping[str, Any],
) -> str:
    """Derive a deterministic sha256 record id from proof digest, scope, and identity."""
    payload = {
        "proof": proof_result.content_digest() if proof_result is not None else None,
        "declared_scope": declared,
        "checked_scope": checked,
        "target_identity": _jsonable(target_identity),
        "commit_provenance": _jsonable(commit_provenance),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _jsonable(value: Any) -> Any:
    """Recursively convert mappings/sequences/enums into JSON-plain values."""
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


# ---------------------------------------------------------------------------
# git observation
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one git subcommand against ``root`` with a bounded timeout, capturing output."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )


def _commit_reachable(root: Path, commit: str, ref: str) -> bool:
    """Return whether ``commit`` resolves to a real commit that is an ancestor of ``ref``."""
    if not commit.strip() or not ref.strip():
        return False
    resolved = _git(root, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    if resolved.returncode != 0:
        return False
    return _git(root, "merge-base", "--is-ancestor", commit, ref).returncode == 0
