"""Bounded, proof-gated cleanup for vc-frame run tabs.

Run artifacts are durable state. A terminal tab is only a transient viewer, but
GC may remove it only when vc-frame's v4 receipt, capture digest, finished meta,
runtime settlement, and the tab's exact incarnation all still agree.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeGuard

from .run_triage import (
    DurableTransferProof,
    TransferProofError,
    TransferTabIdentity,
    TriageGcResult,
    load_durable_transfer_proof,
    record_triage_gc_result,
)

BUCKET_SESSIONS = ("Finalized runs", "Failed runs", "Needs attention")
PROTECTED_TAB_NAMES = {"Start here", "Agents", "Shell", "voc"}
_HEX = frozenset("0123456789abcdefABCDEF")


@dataclass(frozen=True)
class LiveTab:
    """One typed tab returned by an unambiguous vc-frame inventory."""

    session: str
    tab_id: int
    name: str
    position: int
    active: bool
    focused_elsewhere: bool
    session_incarnation: str
    tab_instance_id: str

    def matches(self, identity: TransferTabIdentity) -> bool:
        """True when this live tab is the exact durable-proof identity."""
        return (
            self.session == identity.session
            and self.tab_id == identity.tab_id
            and self.name == identity.name
            and self.session_incarnation == identity.session_incarnation
            and self.tab_instance_id == identity.tab_instance_id
        )


@dataclass(frozen=True)
class TabRef:
    """A cleanup candidate bound to one proof and one tab incarnation."""

    run_id: str
    settlement_revision: int
    receipt_sha256: str
    session: str
    tab_id: int
    name: str
    position: int
    session_incarnation: str
    tab_instance_id: str
    reason: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(
    argv: Sequence[str], *, env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run one vc-frame CLI invocation with a 15s timeout, never raising."""
    try:
        return subprocess.run(
            list(argv),
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            list(argv),
            124,
            stdout=str(exc.stdout or ""),
            stderr=f"timed out: {exc}",
        )


def _run(
    runner: Runner,
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    """Thin indirection point so tests can inject a fake ``runner``."""
    return runner(list(argv), env=env)


def _is_hex(value: Any, length: int) -> TypeGuard[str]:
    """True when ``value`` is a lowercase/uppercase hex string of exact ``length``."""
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in _HEX for character in value)
    )


def _parse_live_tab(session: str, raw: Any) -> LiveTab | None:
    """Strictly validate and decode one ``list-tabs --json`` entry, or None."""
    if not isinstance(raw, Mapping):
        return None
    tab_id = raw.get("tab_id")
    name = raw.get("name")
    position = raw.get("position")
    active = raw.get("active")
    focused = raw.get("other_focused_clients")
    incarnation = raw.get("session_incarnation")
    instance = raw.get("tab_instance_id")
    if (
        type(tab_id) is not int
        or tab_id < 0
        or not isinstance(name, str)
        or not name
        or type(position) is not int
        or position < 0
        or type(active) is not bool
        or not isinstance(focused, list)
        or not isinstance(incarnation, str)
        or not incarnation
        or not _is_hex(instance, 32)
    ):
        return None
    return LiveTab(
        session=session,
        tab_id=tab_id,
        name=name,
        position=position,
        active=active,
        focused_elsewhere=bool(focused),
        session_incarnation=incarnation,
        tab_instance_id=instance,
    )


def list_tabs(
    binary: str,
    session: str,
    *,
    env: Mapping[str, str],
    runner: Runner = _default_runner,
) -> list[LiveTab] | None:
    """Return one fully typed inventory; ``None`` means fail-closed ambiguity."""
    session_env = dict(env)
    session_env["VC_FRAME_SESSION_NAME"] = session
    proc = _run(
        runner,
        [binary, "action", "list-tabs", "--json"],
        env=session_env,
    )
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list):
        return None

    parsed: list[LiveTab] = []
    for raw in payload:
        tab = _parse_live_tab(session, raw)
        if tab is None:
            return None
        parsed.append(tab)

    tab_ids = [tab.tab_id for tab in parsed]
    instances = [tab.tab_instance_id for tab in parsed]
    incarnations = {tab.session_incarnation for tab in parsed}
    if (
        len(tab_ids) != len(set(tab_ids))
        or len(instances) != len(set(instances))
        or len(incarnations) > 1
    ):
        return None
    return parsed


def durable_transfer_proofs(
    control_plane: Path,
) -> dict[str, DurableTransferProof]:
    """Load only exact v4 transfer + terminal settlement proofs.

    Any duplicate durable tab identity invalidates every colliding proof. There
    is no safe winner when two runtime records claim the same incarnation.
    """
    try:
        root = control_plane.resolve(strict=True)
    except OSError:
        return {}
    runtime_runs = root / "runtime_runs"
    if not runtime_runs.is_dir() or runtime_runs.is_symlink():
        return {}

    proofs: dict[str, DurableTransferProof] = {}
    try:
        run_dirs = sorted(runtime_runs.iterdir(), key=lambda path: path.name)
    except OSError:
        return {}
    for run_dir in run_dirs:
        if run_dir.is_symlink() or not run_dir.is_dir():
            continue
        runtime_meta = run_dir / "meta.json"
        try:
            proof = load_durable_transfer_proof(root, runtime_meta)
        except TransferProofError:
            continue
        proofs[proof.run_id] = proof

    owners: dict[tuple[str, str], set[str]] = {}
    for proof in proofs.values():
        identities = [proof.viewer_identity]
        if proof.origin_identity is not None:
            identities.append(proof.origin_identity)
        for identity in identities:
            owners.setdefault(
                (identity.session, identity.tab_instance_id),
                set(),
            ).add(proof.run_id)
    collisions = {
        run_id for run_ids in owners.values() if len(run_ids) > 1 for run_id in run_ids
    }
    return {
        run_id: proof for run_id, proof in proofs.items() if run_id not in collisions
    }


def durable_run_ids(control_plane: Path) -> set[str]:
    """Compatibility projection: run IDs backed by full durable proof."""
    return set(durable_transfer_proofs(control_plane))


def terminal_origins(
    control_plane: Path,
    durable: set[str] | None = None,
) -> set[tuple[str, str]]:
    """Compatibility projection of exact proof-backed source names."""
    proofs = durable_transfer_proofs(control_plane)
    allowed = set(proofs) if durable is None else durable
    return {
        (proof.origin_session, proof.origin_tab)
        for run_id, proof in proofs.items()
        if run_id in allowed and proof.origin_identity is not None
    }


def _candidate(
    proof: DurableTransferProof,
    tab: LiveTab,
    *,
    reason: str,
) -> TabRef:
    """Project one durable proof + live tab pair into a cleanup candidate."""
    return TabRef(
        run_id=proof.run_id,
        settlement_revision=proof.settlement_revision,
        receipt_sha256=proof.receipt_sha256,
        session=tab.session,
        tab_id=tab.tab_id,
        name=tab.name,
        position=tab.position,
        session_incarnation=tab.session_incarnation,
        tab_instance_id=tab.tab_instance_id,
        reason=reason,
    )


def plan_tab_cleanup(
    tabs_by_session: Mapping[str, Sequence[LiveTab]],
    *,
    proofs: Mapping[str, DurableTransferProof],
    bucket_tab_limit: int | None,
) -> list[TabRef]:
    """Choose exact proof-backed origins and explicitly bounded viewers."""
    candidates: dict[tuple[str, str], TabRef] = {}

    for proof in proofs.values():
        origin = proof.origin_identity
        if origin is None:
            continue
        matches = [
            tab
            for tab in tabs_by_session.get(origin.session, ())
            if tab.matches(origin)
        ]
        if len(matches) != 1:
            continue
        tab = matches[0]
        if not tab.active and not tab.focused_elsewhere:
            candidates[(tab.session, tab.tab_instance_id)] = _candidate(
                proof,
                tab,
                reason="redundant-origin",
            )

    if bucket_tab_limit is None:
        return sorted(
            candidates.values(),
            key=lambda item: (item.session, item.position, item.run_id),
        )

    limit = max(0, bucket_tab_limit)
    for session in BUCKET_SESSIONS:
        eligible: list[tuple[DurableTransferProof, LiveTab]] = []
        for proof in proofs.values():
            viewer = proof.viewer_identity
            if viewer.session != session or viewer.name in PROTECTED_TAB_NAMES:
                continue
            matches = [
                tab for tab in tabs_by_session.get(session, ()) if tab.matches(viewer)
            ]
            if len(matches) == 1:
                eligible.append((proof, matches[0]))
        eligible.sort(
            key=lambda item: (
                item[0].updated_at,
                item[1].position,
                item[0].run_id,
            ),
            reverse=True,
        )
        keep_instances = {tab.tab_instance_id for _proof, tab in eligible[:limit]}
        for proof, tab in eligible:
            if (
                tab.tab_instance_id not in keep_instances
                and not tab.active
                and not tab.focused_elsewhere
            ):
                candidates[(tab.session, tab.tab_instance_id)] = _candidate(
                    proof,
                    tab,
                    reason="durable-bucket-view",
                )

    return sorted(
        candidates.values(),
        key=lambda item: (item.session, item.position, item.run_id),
    )


def collect_cleanup(
    binary: str,
    control_plane: Path,
    *,
    bucket_tab_limit: int | None,
    env: Mapping[str, str],
    runner: Runner = _default_runner,
) -> list[TabRef]:
    """Enumerate live tabs for proof-relevant sessions and plan their cleanup."""
    proofs = durable_transfer_proofs(control_plane)
    if not proofs:
        return []
    sessions = set(BUCKET_SESSIONS)

    # Query an origin session only when this process is already inside it.
    # Asking a dead historical session for tabs can resurrect it.
    ambient_sessions = {
        str(env.get(name, "") or "").strip()
        for name in ("VC_FRAME_SESSION_NAME", "ZELLIJ_SESSION_NAME")
    }
    ambient_sessions.discard("")
    sessions.update(
        proof.origin_session
        for proof in proofs.values()
        if proof.origin_session in ambient_sessions
    )

    tabs: dict[str, Sequence[LiveTab]] = {}
    for session in sorted(sessions):
        inventory = list_tabs(binary, session, env=env, runner=runner)
        if inventory is not None:
            tabs[session] = inventory
    return plan_tab_cleanup(
        tabs,
        proofs=proofs,
        bucket_tab_limit=bucket_tab_limit,
    )


def _bound_identity(
    proof: DurableTransferProof,
    tab: TabRef,
) -> TransferTabIdentity | None:
    """Return the proof-side identity for ``tab`` only if it still matches exactly."""
    if tab.reason == "redundant-origin":
        identity = proof.origin_identity
    elif tab.reason == "durable-bucket-view":
        identity = proof.viewer_identity
    else:
        return None
    if identity is None:
        return None
    if (
        proof.settlement_revision != tab.settlement_revision
        or proof.receipt_sha256 != tab.receipt_sha256
        or identity.session != tab.session
        or identity.name != tab.name
        or identity.tab_id != tab.tab_id
        or identity.session_incarnation != tab.session_incarnation
        or identity.tab_instance_id != tab.tab_instance_id
    ):
        return None
    return identity


def _reload_bound_proof(
    control_plane: Path,
    tab: TabRef,
) -> DurableTransferProof | None:
    """Re-read the proof for ``tab`` from disk and confirm it still binds this tab."""
    try:
        root = control_plane.resolve(strict=True)
        proof = load_durable_transfer_proof(
            root,
            root / "runtime_runs" / tab.run_id / "meta.json",
        )
    except (OSError, TransferProofError):
        return None
    return proof if _bound_identity(proof, tab) is not None else None


def _gc_result(
    tab: TabRef,
    *,
    status: str,
    reason: str,
    detail: str = "",
    returncode: int | None = None,
) -> TriageGcResult:
    """Build one typed GC outcome record for a tab, truncating overlong detail."""
    role = {
        "redundant-origin": "origin",
        "durable-bucket-view": "viewer",
    }.get(tab.reason, "viewer")
    return TriageGcResult(
        run_id=tab.run_id,
        status=status,
        reason=reason,
        target_role=role,
        target=TransferTabIdentity(
            session=tab.session,
            name=tab.name,
            tab_id=tab.tab_id,
            session_incarnation=tab.session_incarnation,
            tab_instance_id=tab.tab_instance_id,
        ),
        settlement_revision=tab.settlement_revision,
        receipt_sha256=tab.receipt_sha256,
        recorded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        detail=detail[:500],
        returncode=returncode,
    )


def _persist_gc_result(
    control_plane: Path,
    result: TriageGcResult,
) -> TriageGcResult:
    """Persist ``result`` to the control plane and stamp its ``persisted`` flag."""
    return replace(
        result,
        persisted=record_triage_gc_result(control_plane, result),
    )


def close_tab(
    binary: str,
    control_plane: Path,
    tab: TabRef,
    *,
    env: Mapping[str, str],
    runner: Runner = _default_runner,
) -> TriageGcResult:
    """Apply one explicit GC close with proof, atomic quiescence, and a receipt."""

    def _error(
        reason: str,
        *,
        detail: str = "",
        returncode: int | None = None,
    ) -> TriageGcResult:
        return _persist_gc_result(
            control_plane,
            _gc_result(
                tab,
                status="error",
                reason=reason,
                detail=detail,
                returncode=returncode,
            ),
        )

    proof = _reload_bound_proof(control_plane, tab)
    if proof is None:
        return _error("proof_unavailable")

    current = list_tabs(binary, tab.session, env=env, runner=runner)
    if current is None:
        return _error("inventory_unavailable")
    matches = [
        candidate
        for candidate in current
        if candidate.tab_instance_id == tab.tab_instance_id
    ]
    identity = _bound_identity(proof, tab)
    if (
        identity is None
        or len(matches) != 1
        or not matches[0].matches(identity)
        or matches[0].active
        or matches[0].focused_elsewhere
    ):
        return _error("identity_or_focus_changed")

    # Re-read after the live query: a changed receipt/revision cancels apply.
    if _reload_bound_proof(control_plane, tab) is None:
        return _error("proof_changed_before_intent")

    pending = _persist_gc_result(
        control_plane,
        _gc_result(
            tab,
            status="pending",
            reason="explicit_apply",
        ),
    )
    if not pending.persisted:
        return _gc_result(
            tab,
            status="error",
            reason="intent_persist_failed",
        )
    if _reload_bound_proof(control_plane, tab) is None:
        return _error("proof_changed_after_intent")

    session_env = dict(env)
    session_env["VC_FRAME_SESSION_NAME"] = tab.session
    proc = _run(
        runner,
        [
            binary,
            "action",
            "close-tab",
            "--tab-id",
            str(tab.tab_id),
            "--expected-name",
            tab.name,
            "--expected-session-incarnation",
            tab.session_incarnation,
            "--expected-tab-instance-id",
            tab.tab_instance_id,
            "--gc-if-quiescent",
        ],
        env=session_env,
    )
    if proc.returncode != 0:
        detail = str(proc.stderr or proc.stdout or "").strip()
        return _error(
            "vc_frame_refused",
            detail=detail,
            returncode=proc.returncode,
        )
    remaining = list_tabs(binary, tab.session, env=env, runner=runner)
    if remaining is None:
        return _error("post_close_inventory_unavailable")
    if any(item.tab_instance_id == tab.tab_instance_id for item in remaining):
        return _error("target_still_present")

    closed = _persist_gc_result(
        control_plane,
        _gc_result(
            tab,
            status="closed",
            reason="closed",
            returncode=proc.returncode,
        ),
    )
    if closed.persisted:
        return closed
    return _gc_result(
        tab,
        status="error",
        reason="close_result_persist_failed",
        detail="vc-frame closed the exact target but canonical meta was not updated",
        returncode=proc.returncode,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: plan (and, with ``--apply``, execute) bounded tab GC."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vc-frame-bin", required=True)
    parser.add_argument("--control-plane", type=Path, required=True)
    parser.add_argument("--bucket-tab-limit", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.bucket_tab_limit is not None and args.bucket_tab_limit < 0:
        parser.error("--bucket-tab-limit must be non-negative")

    env = dict(os.environ)
    candidates = collect_cleanup(
        args.vc_frame_bin,
        args.control_plane,
        bucket_tab_limit=args.bucket_tab_limit,
        env=env,
    )
    results: list[tuple[TabRef, TriageGcResult]] = []
    if args.apply:
        results = [
            (
                tab,
                close_tab(
                    args.vc_frame_bin,
                    args.control_plane,
                    tab,
                    env=env,
                ),
            )
            for tab in candidates
        ]
    closed = [tab for tab, result in results if result.succeeded]

    failed = args.apply and any(not result.succeeded for _tab, result in results)
    if not args.quiet or failed:
        mode = "applied" if args.apply else "dry-run"
        print(
            f"vc_frame-tab-gc: {mode}; "
            f"candidates={len(candidates)} closed={len(closed)}"
        )
        result_by_tab = {tab: result for tab, result in results}
        for tab in candidates:
            result = result_by_tab.get(tab)
            state = "closed" if result is not None and result.succeeded else "candidate"
            suffix = ""
            if result is not None and not result.succeeded:
                suffix = f"; error={result.reason}; persisted={str(result.persisted).lower()}"
            print(
                f"  {state}: {tab.session}/{tab.name} "
                f"id={tab.tab_id} instance={tab.tab_instance_id} "
                f"run={tab.run_id} ({tab.reason}){suffix}"
            )
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
