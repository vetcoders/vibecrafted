"""Runtime caller for ``vc-frame triage-run``.

When a supervised run reaches a terminal state, the tab it lived in stops being
work-in-progress and starts being evidence. vc-frame owns the transfer primitive
(``vc-frame triage-run``, vc-frame ``71146085``); this module owns *calling* it —
the seam vc-frame deliberately left to the runtime, because vc-frame owns the
terminal and the runtime owns the run.

Two properties drive every decision here:

**Fail-open.** Triage is a convenience on top of a finished run. The report, the
meta, and the origin tab all already exist and are already correct by the time we
are called. So no failure in this module may propagate: a missing binary, a dead
session, a non-zero ``triage-run`` — each degrades to a recorded receipt, never an
exception and never a lost tab. vc-frame's engine guarantees no-close-before-confirm
on its side; this is the mirror of that caution on the caller side.

**Only ever our own tab.** The transfer closes the origin tab. That is safe only
when the tab belongs to this run alone. The runtime spawns run tabs named by run
id (``lib/vc_frame.sh``), but marbles runs share one tab across siblings — closing
that would take the siblings with it. :func:`plan_triage` refuses that case rather
than trusting the caller to know the difference.

**Single signals lie.** The drawer a run lands in is decided by
:func:`classify_run`. When a delivery-kernel receipt is present, the three
orthogonal axes (``execution_state`` / ``proof_state`` / ``delivery_state``)
own the verdict; otherwise a conjunction over exit code, run state, report
delivery and transcript volume decides — never the exit code alone. The AICX
record from 2026-05-14 holds runs reporting top-level ``completed``/exit 0
whose own reports said ``failed``, and ``timed_out``/``report_missing`` states
sitting next to complete artifacts. Every such contradiction is routed to
human review rather than to a confident drawer, and so is every signal the
classifier cannot read.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import wraps
from pathlib import Path
from typing import Any, Protocol, TypeGuard

# Module-path import on purpose; see the note in vc_frame_delivery.py.
import vibecrafted_core.run_mutation as run_mutation_module

from .run_mutation import (
    RunMetaMutationError,
    mutate_run_meta,
    read_run_meta,
)
from .runtime_transcript import validate_runtime_transcript

__all__ = [
    "BUCKET_FAILED",
    "BUCKET_FINALIZED",
    "BUCKET_LIVE",
    "BUCKET_NEEDS_ATTENTION",
    "MINIMAL_REPORT_BYTES",
    "MINIMAL_TRANSCRIPT_BYTES",
    "TRANSFER_PROOF_SCHEMA",
    "TRIAGE_GC_SCHEMA",
    "VERDICT_FAILED",
    "VERDICT_FINALIZED",
    "VERDICT_INFRA_FAILURE",
    "VERDICT_NEEDS_ATTENTION",
    "DurableTransferProof",
    "KernelAxes",
    "RunClassification",
    "RunSignals",
    "TransferProofError",
    "TransferTabIdentity",
    "TriageGcResult",
    "TriageOutcome",
    "TriagePlan",
    "TriageSweepItem",
    "TriageSweepReport",
    "bucket_for_exit_code",
    "classify_provider_error",
    "classify_run",
    "load_durable_transfer_proof",
    "load_vc_frame_transfer_proof",
    "main",
    "outcome_for_exit_code",
    "plan_triage",
    "read_kernel_axes",
    "read_run_signals",
    "reconcile_untriaged_runs",
    "record_triage_gc_result",
    "triage_finished_run",
    "triage_outcome_is_complete",
]

# Bucket names are vc-frame's wire contract (BucketKind::session_name), not ours.
# They are mirrored here only so the receipt can name the destination without a
# round-trip; vc-frame remains the owner of the rail UI and these strings.
BUCKET_FINALIZED = "Finalized runs"
BUCKET_FAILED = "Failed runs"
BUCKET_NEEDS_ATTENTION = "Needs attention"

#: The pre-terminal bucket. Unlike the three above it is not a triage
#: destination and never appears in ``_BUCKET_FOR_VERDICT`` — a run is never
#: *classified* as live. It hosts the read-only viewer tab that
#: ``workflow.open_live_viewer`` opens at launch for a detached headless
#: worker, and triage is what empties it: the viewer's ``origin_session`` is
#: this bucket, so the ordinary transfer moves it into Finalized/Failed/Needs
#: attention when the run settles. Same wire contract as the other three
#: (a vc-frame session name); vc-frame still owns the rail UI.
BUCKET_LIVE = "Live runs"

# The four verdicts. Also the receipt values written to meta.json under
# "triage" — the headline of a receipt is where the run went.
VERDICT_FINALIZED = "finalized"
VERDICT_FAILED = "failed"
VERDICT_NEEDS_ATTENTION = "needs_attention"
#: Provider overload / quota — not a worker error. Distinct from ``failed`` so
#: supervisors do not treat 429/529/usage-limit as "the agent worked badly".
VERDICT_INFRA_FAILURE = "infra_failure"

OUTCOME_FINALIZED = VERDICT_FINALIZED
OUTCOME_FAILED = VERDICT_FAILED
OUTCOME_NEEDS_ATTENTION = VERDICT_NEEDS_ATTENTION
OUTCOME_INFRA_FAILURE = VERDICT_INFRA_FAILURE
#: No transfer was attempted — nothing to triage, or nothing able to triage it.
OUTCOME_SKIPPED = "skipped"
#: The transfer itself broke. A different axis from the verdict: it says nothing
#: about the run, only about our call into vc-frame.
OUTCOME_ERROR = "error"

_TRUTHY_OFF = {"0", "false", "no", "off"}
_PERMANENT_SKIP_REASONS = {
    "disabled",
    "foreign_tab",
    "headless",
    "no_run_id",
    "no_session",
    "shared_tab",
}
# A newly persisted intent belongs to the caller that is about to spawn
# vc-frame.  A reconciler which observes that intent after the caller dies
# gives the child a short window to acquire vc-frame's own transfer.lock before
# it can consider a retry.  The durable outbox will revisit it.
_TRANSFER_CHILD_START_GRACE_NS = 5_000_000_000
_TRANSFER_LOCK_HANDOFF = "inherited_fd_v1"
_TRIAGE_SWEEP_CURSOR_RUN_ID = "__triage_reconciliation_cursor__"
_TRIAGE_SWEEP_CURSOR_FILE = ".triage-reconciliation-cursor.json"

# --------------------------------------------------------------------------
# Signal thresholds. Measured, not guessed (sample: every run transcript under
# ~/.vibecrafted/artifacts newer than 2026-06-15, read through their compat
# symlinks, on 2026-07-21).
# --------------------------------------------------------------------------

#: A transcript below this carries only the launcher's frontmatter banner
#: (~380 B) — no tool call, no output, no work. The smallest transcript in the
#: sample that came from a run which actually produced a report was 885 B, so
#: 512 sits in the empty gap between "died at startup" and "did something".
MINIMAL_TRANSCRIPT_BYTES = 512

#: A report file that exists but holds nothing is what control_plane calls
#: `report_invalid` — a contradiction, never a delivery.
MINIMAL_REPORT_BYTES = 1

# Run states, in control_plane's vocabulary (`FINAL_STATES`). Split by what each
# one *asserts*, because the verdict is a conjunction: a state that disagrees
# with the exit code is itself the contradiction.

#: States asserting the artifact contract held.
_STATES_DELIVERED = frozenset({"report_validated", "completed", "closed", "converged"})
#: States asserting the run stopped without delivering. Consistent with a death.
_STATES_DIED = frozenset({"failed", "stopped", "report_missing"})
#: States that *are* the contradiction, or that name human review outright.
#: These never reach a confident drawer regardless of the other signals.
_STATES_CONTRADICTORY = frozenset(
    {
        "report_invalid",
        "contract_failed",
        "recovery_required",
        "blocked",
        "stalled",
        "timed_out",
        # User-selected measured budget exhaustion is neither provider
        # overload nor proof that the worker failed. Keep it out of the
        # provider-error infra bucket and route it to operator attention.
        "quota_exhausted",
        "ghost",
        "gc",
    }
)

_BUCKET_FOR_VERDICT = {
    VERDICT_FINALIZED: BUCKET_FINALIZED,
    VERDICT_FAILED: BUCKET_FAILED,
    VERDICT_NEEDS_ATTENTION: BUCKET_NEEDS_ATTENTION,
    # vc-frame still has three rails. Infra is retryable substrate, not a
    # worker death, so it shares Needs attention rather than Failed.
    VERDICT_INFRA_FAILURE: BUCKET_NEEDS_ATTENTION,
}
# vc-frame's `triage-run --bucket` takes the kebab spelling (W2-B-4a).
_BUCKET_FLAG_FOR_VERDICT = {
    VERDICT_FINALIZED: "finalized",
    VERDICT_FAILED: "failed",
    VERDICT_NEEDS_ATTENTION: "needs-attention",
    VERDICT_INFRA_FAILURE: "needs-attention",
}

TRANSFER_PROOF_SCHEMA = "vibecrafted.vc-frame-transfer-proof.v1"
TRIAGE_GC_SCHEMA = "vibecrafted.vc-frame-tab-gc.v1"
_TRANSFER_RECEIPT_VERSION = 4
_CAPTURE_MANIFEST_VERSION = 1
_CAPTURE_SOURCES = {"terminal_scrollback", "runtime_transcript"}
_BUCKET_SESSION = {
    "Finalized": BUCKET_FINALIZED,
    "Failed": BUCKET_FAILED,
    "NeedsAttention": BUCKET_NEEDS_ATTENTION,
}
_SETTLEMENT_TUI = {
    VERDICT_FINALIZED: "f",
    VERDICT_FAILED: "x",
    "invalid": "x",
    VERDICT_NEEDS_ATTENTION: "n",
}
_SETTLEMENT_MATERIAL_FIELDS = frozenset(
    {
        "settlement_revision",
        "settlement_verdict",
        "settlement_tui",
        "settlement",
    }
)
_TERMINAL_AWAIT_OUTCOMES = {"completed", "timed_out"}
_TRIAGE_GC_REASONS = {
    "closed",
    "explicit_apply",
    "identity_or_focus_changed",
    "inventory_unavailable",
    "post_close_inventory_unavailable",
    "proof_changed_after_intent",
    "proof_changed_before_intent",
    "proof_unavailable",
    "target_still_present",
    "vc_frame_refused",
}
_HEX = frozenset("0123456789abcdefABCDEF")


class TransferProofError(ValueError):
    """The vc-frame v4 transfer evidence is absent, ambiguous, or inconsistent."""


@dataclass(frozen=True)
class TransferTabIdentity:
    """One tab incarnation, stable across numeric-ID reuse."""

    session: str
    name: str
    tab_id: int
    session_incarnation: str
    tab_instance_id: str

    def projection(self) -> dict[str, Any]:
        """JSON-serializable form of this tab identity."""
        return {
            "session": self.session,
            "name": self.name,
            "id": self.tab_id,
            "session_incarnation": self.session_incarnation,
            "tab_instance_id": self.tab_instance_id,
        }


@dataclass(frozen=True)
class DurableTransferProof:
    """Validated vc-frame v4 transfer plus its exact runtime settlement revision."""

    run_id: str
    receipt_path: Path
    receipt_sha256: str
    scrollback_path: Path
    finished_meta_path: Path
    capture_manifest_path: Path
    bucket: str
    bucket_session: str
    exit_code: int
    origin_session: str
    origin_tab: str
    capture_source: str
    capture_source_identity: str
    capture_bytes: int
    capture_sha256: str
    origin_identity: TransferTabIdentity | None
    viewer_identity: TransferTabIdentity
    viewer_token: str
    origin_tab_state: str
    updated_at: int
    settlement_revision: int = 0
    settlement_verdict: str = ""
    settlement_tui: str = ""

    def projection(self) -> dict[str, Any]:
        """JSON projection linked from runtime meta after a proven transfer."""
        projection = {
            "schema": TRANSFER_PROOF_SCHEMA,
            "receipt": str(self.receipt_path),
            "receipt_sha256": self.receipt_sha256,
            "version": _TRANSFER_RECEIPT_VERSION,
            "run": self.run_id,
            "bucket": self.bucket,
            "bucket_session": self.bucket_session,
            "exit_code": self.exit_code,
            "origin": {
                "session": self.origin_session,
                "tab": self.origin_tab,
                "identity": (
                    self.origin_identity.projection()
                    if self.origin_identity is not None
                    else None
                ),
                "state": self.origin_tab_state,
            },
            "capture": {
                "source": self.capture_source,
                "source_identity": self.capture_source_identity,
                "bytes": self.capture_bytes,
                "sha256": self.capture_sha256,
                "path": str(self.scrollback_path),
                "manifest": str(self.capture_manifest_path),
            },
            "finished_meta": str(self.finished_meta_path),
            "viewer": {
                "token": self.viewer_token,
                "identity": self.viewer_identity.projection(),
            },
            "updated_at": self.updated_at,
        }
        if self.settlement_revision > 0:
            projection["settlement"] = {
                "revision": self.settlement_revision,
                "verdict": self.settlement_verdict,
                "tui": self.settlement_tui,
            }
        return projection


@dataclass(frozen=True)
class TriageGcResult:
    """One explicit proof-bound viewer-GC attempt and its durable disposition."""

    run_id: str
    status: str
    reason: str
    target_role: str
    target: TransferTabIdentity
    settlement_revision: int
    receipt_sha256: str
    recorded_at: str
    detail: str = ""
    returncode: int | None = None
    persisted: bool = False

    @property
    def succeeded(self) -> bool:
        """A close counts only when the terminal mutation and receipt both landed."""
        return self.status == "closed" and self.persisted

    def projection(self) -> dict[str, Any]:
        """Canonical additive projection; never rewrites terminal triage truth."""
        return {
            "schema": TRIAGE_GC_SCHEMA,
            "run_id": self.run_id,
            "status": self.status,
            "reason": self.reason,
            "target_role": self.target_role,
            "target": self.target.projection(),
            "settlement_revision": self.settlement_revision,
            "receipt_sha256": self.receipt_sha256,
            "recorded_at": self.recorded_at,
            "detail": self.detail,
            "returncode": self.returncode,
        }


def _is_hex(value: Any, length: int) -> TypeGuard[str]:
    """Whether ``value`` is a lowercase/uppercase hex string of exact ``length``."""
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in _HEX for character in value)
    )


def _safe_run_id(value: Any) -> str:
    """Validate a run id is a bare path-segment string; raise otherwise.

    Rejects ``.``/``..``, any path separator, and anything that would resolve
    to a different name once wrapped in ``Path`` — a run id is used to build
    filesystem paths under the control plane, so it must never traverse.
    """
    if not isinstance(value, str):
        raise TransferProofError(f"invalid run id type: {type(value).__name__}")
    run_id = value.strip()
    if (
        not run_id
        or run_id in {".", ".."}
        or "/" in run_id
        or "\\" in run_id
        or Path(run_id).name != run_id
    ):
        raise TransferProofError(f"invalid run id: {run_id!r}")
    return run_id


def _canonical_root(control_plane: Path) -> Path:
    """Resolve ``control_plane`` to its real directory; raise if it is unusable."""
    try:
        root = control_plane.resolve(strict=True)
    except OSError as error:
        raise TransferProofError(
            f"control plane is unavailable: {control_plane}"
        ) from error
    if not root.is_dir():
        raise TransferProofError(f"control plane is not a directory: {root}")
    return root


def _read_bound_file(path: Path, root: Path, label: str) -> bytes:
    """Read one exact regular file without accepting a symlink/path escape."""
    descriptor: int | None = None
    try:
        if path.is_symlink():
            raise TransferProofError(f"{label} is a symlink: {path}")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        current = path.stat(follow_symlinks=False)
        if (
            resolved != path
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
        ):
            raise TransferProofError(f"{label} is not its canonical file: {path}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            return handle.read()
    except TransferProofError:
        raise
    except (OSError, ValueError) as error:
        raise TransferProofError(f"cannot read {label}: {path}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    """Parse ``data`` as a non-empty JSON object, or raise ``TransferProofError``."""
    try:
        payload = json.loads(data)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise TransferProofError(f"{label} is not valid JSON") from error
    if not isinstance(payload, dict) or not payload:
        raise TransferProofError(f"{label} is not a non-empty object")
    return payload


def _tab_identity(
    raw: Any,
    *,
    label: str,
    expected_session: str,
    expected_name: str,
) -> TransferTabIdentity:
    """Parse and validate one tab identity block against its expected session/name.

    Raises ``TransferProofError`` on any missing, mistyped, or mismatched field —
    the caller never receives a partially-trusted identity.
    """
    if not isinstance(raw, Mapping):
        raise TransferProofError(f"{label} identity is missing")
    tab_id = raw.get("id")
    session = raw.get("session")
    name = raw.get("name")
    incarnation = raw.get("session_incarnation")
    instance = raw.get("tab_instance_id")
    if (
        type(tab_id) is not int
        or tab_id < 0
        or session != expected_session
        or name != expected_name
        or not isinstance(incarnation, str)
        or not incarnation
        or not _is_hex(instance, 32)
    ):
        raise TransferProofError(f"{label} identity is not exact and typed")
    return TransferTabIdentity(
        session=session,
        name=name,
        tab_id=tab_id,
        session_incarnation=incarnation,
        tab_instance_id=instance,
    )


def _runtime_origin(payload: Mapping[str, Any]) -> tuple[str, str]:
    """Extract non-empty ``(origin_session, origin_tab)`` from runtime meta."""
    raw_session = payload.get("origin_session")
    raw_tab = payload.get("origin_tab")
    if not isinstance(raw_session, str) or not isinstance(raw_tab, str):
        raise TransferProofError("runtime meta origin fields are not strings")
    session = raw_session.strip()
    tab = raw_tab.strip()
    if not session or not tab:
        raise TransferProofError("runtime meta lacks exact origin_session/origin_tab")
    return session, tab


def _normalized_command(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Normalize the rerun command exactly as :func:`plan_triage` renders it."""

    raw = payload.get("command") or payload.get("launcher")
    if isinstance(raw, str):
        return (raw,) if raw.strip() else ()
    if isinstance(raw, Sequence):
        return tuple(str(part) for part in raw if str(part).strip())
    return ()


def _normalized_meta_string(payload: Mapping[str, Any], *names: str) -> str:
    """First non-blank stripped string found in ``payload`` across ``names``."""
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalized_transfer_request(
    payload: Mapping[str, Any],
) -> tuple[tuple[str, ...], str | None, str | None, str | None]:
    """Return the proof-bound request fields derivable from durable runtime meta."""

    run_id = _safe_run_id(payload.get("run_id"))
    runtime_transcript = validate_runtime_transcript(
        payload.get("transcript"),
        run_id=run_id,
    )
    return (
        _normalized_command(payload),
        _normalized_meta_string(payload, "root") or None,
        _normalized_meta_string(
            payload,
            "origin_pane_id",
            "vc_frame_pane_id",
            "pane_id",
        )
        or None,
        str(runtime_transcript) if runtime_transcript is not None else None,
    )


def load_vc_frame_transfer_proof(
    control_plane: Path,
    runtime_payload: Mapping[str, Any],
) -> DurableTransferProof:
    """Validate vc-frame's exact v4 transfer files without trusting projections."""
    root = _canonical_root(control_plane)
    run_id = _safe_run_id(runtime_payload.get("run_id"))
    origin_session, origin_tab = _runtime_origin(runtime_payload)
    run_dir = root / "finished_runs" / run_id
    try:
        if (
            run_dir.is_symlink()
            or run_dir.resolve(strict=True) != run_dir
            or not run_dir.is_dir()
        ):
            raise TransferProofError(
                f"finished run directory is not canonical: {run_dir}"
            )
    except OSError as error:
        raise TransferProofError(
            f"finished run directory is missing: {run_dir}"
        ) from error

    receipt_path = run_dir / "transfer.json"
    scrollback_path = run_dir / "scrollback.txt"
    finished_meta_path = run_dir / "meta.json"
    capture_manifest_path = run_dir / "capture.manifest.json"
    receipt_bytes = _read_bound_file(receipt_path, root, "transfer receipt")
    receipt = _json_object(receipt_bytes, "transfer receipt")

    if receipt.get("version") != _TRANSFER_RECEIPT_VERSION:
        raise TransferProofError("transfer receipt is not schema version 4")
    if receipt.get("run") != run_id:
        raise TransferProofError("transfer receipt run does not match runtime meta")
    if (
        receipt.get("origin_session") != origin_session
        or receipt.get("origin_tab") != origin_tab
    ):
        raise TransferProofError("transfer receipt origin does not match runtime meta")
    exit_code = receipt.get("exit_code")
    runtime_exit_code = runtime_payload.get("exit_code")
    if (
        type(exit_code) is not int
        or type(runtime_exit_code) is not int
        or exit_code != runtime_exit_code
    ):
        raise TransferProofError(
            "transfer receipt exit code does not match runtime meta"
        )
    command = receipt.get("command")
    cwd = receipt.get("cwd")
    pane_id = receipt.get("pane_id")
    runtime_transcript = receipt.get("runtime_transcript")
    if (
        not isinstance(command, list)
        or any(not isinstance(part, str) for part in command)
        or (cwd is not None and not isinstance(cwd, str))
        or (pane_id is not None and not isinstance(pane_id, str))
        or (runtime_transcript is not None and not isinstance(runtime_transcript, str))
    ):
        raise TransferProofError("transfer receipt request fields are not typed")
    (
        expected_command,
        expected_cwd,
        expected_pane_id,
        expected_runtime_transcript,
    ) = _normalized_transfer_request(runtime_payload)
    if (
        command != list(expected_command)
        or cwd != expected_cwd
        or pane_id != expected_pane_id
        or runtime_transcript != expected_runtime_transcript
    ):
        raise TransferProofError(
            "transfer receipt request does not match normalized runtime meta"
        )
    settlement = _settlement_identity(runtime_payload)
    has_settlement = _has_settlement_material(runtime_payload)
    receipt_settlement_revision = receipt.get("settlement_revision")
    if has_settlement:
        if settlement is None:
            raise TransferProofError("runtime settlement is not exact and typed")
        if (
            type(receipt_settlement_revision) is not int
            or receipt_settlement_revision != settlement[0]
        ):
            raise TransferProofError(
                "transfer receipt settlement revision does not match runtime meta"
            )
    elif receipt_settlement_revision is not None and (
        type(receipt_settlement_revision) is not int or receipt_settlement_revision != 0
    ):
        raise TransferProofError(
            "transfer receipt carries an unexpected settlement revision"
        )
    if receipt.get("superseded_viewers") != []:
        raise TransferProofError("completed transfer still has superseded viewers")
    bucket = receipt.get("bucket")
    if not isinstance(bucket, str) or bucket not in _BUCKET_SESSION:
        raise TransferProofError(f"transfer receipt has unknown bucket: {bucket!r}")
    if settlement is not None:
        canonical_verdict = (
            VERDICT_FAILED if settlement[1] == "invalid" else settlement[1]
        )
        if _BUCKET_SESSION[bucket] != _BUCKET_FOR_VERDICT[canonical_verdict]:
            raise TransferProofError(
                "transfer receipt bucket disagrees with canonical settlement"
            )
    if receipt.get("capture_committed") is not True:
        raise TransferProofError("capture is not committed")
    if receipt.get("metadata_committed") is not True:
        raise TransferProofError("finished metadata is not committed")
    if receipt.get("viewer_confirmed") is not True:
        raise TransferProofError("viewer is not confirmed")
    if receipt.get("viewer_creation_pending") is not False:
        raise TransferProofError("viewer creation remains pending")
    if receipt.get("origin_tab_state") != "closed":
        raise TransferProofError("origin tab is not proven closed")
    if receipt.get("fault") is not None:
        raise TransferProofError("transfer receipt still carries a fault")
    updated_at = receipt.get("updated_at")
    if type(updated_at) is not int or updated_at <= 0:
        raise TransferProofError("transfer receipt has no durable timestamp")

    capture = receipt.get("capture")
    if not isinstance(capture, dict):
        raise TransferProofError("transfer receipt has no capture evidence")
    capture_source = capture.get("capture_source")
    source_identity = capture.get("source_identity")
    capture_bytes = capture.get("bytes")
    capture_sha256 = capture.get("sha256")
    if not isinstance(capture_source, str) or capture_source not in _CAPTURE_SOURCES:
        raise TransferProofError(f"unknown capture source: {capture_source!r}")
    if not isinstance(source_identity, str) or not source_identity:
        raise TransferProofError("capture source identity is empty")
    if type(capture_bytes) is not int or capture_bytes <= 0:
        raise TransferProofError("capture byte count is not positive")
    if not _is_hex(capture_sha256, 64):
        raise TransferProofError("capture sha256 is not a 64-character digest")

    scrollback = _read_bound_file(scrollback_path, root, "captured scrollback")
    if len(scrollback) != capture_bytes:
        raise TransferProofError("captured scrollback size does not match receipt")
    if hashlib.sha256(scrollback).hexdigest() != capture_sha256.lower():
        raise TransferProofError("captured scrollback hash does not match receipt")

    origin_identity: TransferTabIdentity | None = None
    raw_origin_identity = capture.get("origin_tab_identity")
    if raw_origin_identity is not None:
        origin_identity = _tab_identity(
            raw_origin_identity,
            label="origin",
            expected_session=origin_session,
            expected_name=origin_tab,
        )
    if capture_source == "terminal_scrollback":
        if origin_identity is None:
            raise TransferProofError("terminal capture lacks typed origin identity")
        source_parts = source_identity.split(";")
        expected_parts = [
            f"session={origin_session}",
            f"tab_id={origin_identity.tab_id}",
            f"tab_instance_id={origin_identity.tab_instance_id}",
        ]
        if (
            source_parts[:3] != expected_parts
            or len(source_parts) != 4
            or not source_parts[3].startswith("pane_id=terminal_")
            or not source_parts[3].removeprefix("pane_id=terminal_").isdigit()
        ):
            raise TransferProofError("terminal capture source identity is inconsistent")
    else:
        source_path = Path(source_identity)
        if (
            not source_path.is_absolute()
            or ".." in source_path.parts
            or source_path.resolve(strict=False) != source_path
        ):
            raise TransferProofError("runtime transcript source path is not canonical")
        if not isinstance(runtime_transcript, str) or not runtime_transcript:
            raise TransferProofError("runtime transcript request path is missing")
        requested_source = Path(runtime_transcript)
        if (
            not requested_source.is_absolute()
            or ".." in requested_source.parts
            or requested_source.resolve(strict=False) != requested_source
            or requested_source != source_path
        ):
            raise TransferProofError(
                "runtime transcript source does not match the requested path"
            )

    token = receipt.get("viewer_token")
    if not _is_hex(token, 32):
        raise TransferProofError("viewer ownership token is invalid")
    bucket_session = _BUCKET_SESSION[bucket]
    viewer_identity = _tab_identity(
        receipt.get("viewer_tab_identity"),
        label="viewer",
        expected_session=bucket_session,
        expected_name=f"{run_id} [vc:{token}]",
    )

    capture_manifest = _json_object(
        _read_bound_file(capture_manifest_path, root, "capture manifest"),
        "capture manifest",
    )
    expected_manifest = {
        "version": _CAPTURE_MANIFEST_VERSION,
        "run_id": run_id,
        "session": origin_session,
        "origin_tab": origin_tab,
        "pane_id": pane_id,
        "runtime_transcript": runtime_transcript,
        "staging_file": capture_manifest.get("staging_file"),
        "evidence": capture,
    }
    staging_file = capture_manifest.get("staging_file")
    if (
        not isinstance(staging_file, str)
        or not staging_file
        or Path(staging_file).name != staging_file
        or capture_manifest != expected_manifest
    ):
        raise TransferProofError("capture manifest does not equal transfer evidence")

    finished_meta = _json_object(
        _read_bound_file(finished_meta_path, root, "finished metadata"),
        "finished metadata",
    )
    expected_finished_meta = {
        "run": run_id,
        "exit_code": exit_code,
        "bucket": bucket,
        "origin_session": origin_session,
        "origin_tab": origin_tab,
        "command": command,
        "cwd": cwd,
        "captured_at": updated_at,
        "capture_source": capture_source,
        "capture_source_identity": source_identity,
        "capture_bytes": capture_bytes,
        "capture_sha256": capture_sha256,
    }
    if finished_meta != expected_finished_meta:
        raise TransferProofError("finished metadata does not equal transfer receipt")

    return DurableTransferProof(
        run_id=run_id,
        receipt_path=receipt_path,
        receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
        scrollback_path=scrollback_path,
        finished_meta_path=finished_meta_path,
        capture_manifest_path=capture_manifest_path,
        bucket=bucket,
        bucket_session=bucket_session,
        exit_code=exit_code,
        origin_session=origin_session,
        origin_tab=origin_tab,
        capture_source=capture_source,
        capture_source_identity=source_identity,
        capture_bytes=capture_bytes,
        capture_sha256=capture_sha256.lower(),
        origin_identity=origin_identity,
        viewer_identity=viewer_identity,
        viewer_token=token.lower(),
        origin_tab_state="closed",
        updated_at=updated_at,
        settlement_revision=settlement[0] if settlement is not None else 0,
        settlement_verdict=settlement[1] if settlement is not None else "",
        settlement_tui=settlement[2] if settlement is not None else "",
    )


class _TransferLockBusy(TransferProofError):
    """vc-frame or another runtime caller still owns this run transfer."""


def _vc_frame_transfer_lock_path(
    control_plane: Path,
    runtime_payload: Mapping[str, Any],
    *,
    create_parents: bool,
) -> tuple[Path, Path]:
    """Resolve ``(root, transfer.lock path)`` for this run, verifying canonical dirs.

    With ``create_parents=True``, missing ``finished_runs``/``finished_runs/<run>``
    directories are created (mode 0o700); otherwise a missing directory is
    tolerated and only checked when present.
    """
    root = _canonical_root(control_plane)
    run_id = _safe_run_id(runtime_payload.get("run_id"))
    finished_root = root / "finished_runs"
    run_dir = finished_root / run_id
    for directory, label in (
        (finished_root, "finished run root"),
        (run_dir, "finished run directory"),
    ):
        try:
            metadata = directory.lstat()
        except FileNotFoundError:
            if not create_parents:
                continue
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
            metadata = directory.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or directory.resolve(strict=True) != directory
        ):
            raise TransferProofError(f"{label} is not canonical: {directory}")
    return root, run_dir / "transfer.lock"


def _validate_open_transfer_lock(path: Path, root: Path, descriptor: int) -> None:
    """Raise unless the already-open ``descriptor`` is exactly ``path``'s regular file.

    Guards the classic TOCTOU: the path may have been swapped for a symlink or a
    different inode between resolution and the ``open`` call this validates.
    """
    opened = os.fstat(descriptor)
    resolved = path.resolve(strict=True)
    resolved.relative_to(root)
    current = path.stat(follow_symlinks=False)
    if (
        resolved != path
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_dev != current.st_dev
        or opened.st_ino != current.st_ino
    ):
        raise TransferProofError(f"transfer lock is not its canonical file: {path}")


def _vc_frame_transfer_lock_is_held(
    control_plane: Path,
    runtime_payload: Mapping[str, Any],
) -> bool:
    """Probe vc-frame's exact per-run lock without creating or following it."""

    root, path = _vc_frame_transfer_lock_path(
        control_plane,
        runtime_payload,
        create_parents=False,
    )
    descriptor: int | None = None
    locked = False
    try:
        if path.is_symlink():
            raise TransferProofError(f"transfer lock is a symlink: {path}")
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        _validate_open_transfer_lock(path, root, descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                return True
            raise TransferProofError(f"cannot probe transfer lock: {path}") from error

        # Revalidate the path after taking the advisory lock.  If another inode
        # replaced it during the probe, treating it as unsafe prevents a retry
        # from coordinating against a different lock file than vc-frame.
        opened = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
        if opened.st_dev != current.st_dev or opened.st_ino != current.st_ino:
            raise TransferProofError(f"transfer lock changed during probe: {path}")
        return False
    except FileNotFoundError:
        return False
    except TransferProofError:
        raise
    except (OSError, ValueError) as error:
        raise TransferProofError(f"cannot inspect transfer lock: {path}") from error
    finally:
        if descriptor is not None:
            try:
                if locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def _hold_vc_frame_transfer_lock(
    control_plane: Path,
    runtime_payload: Mapping[str, Any],
) -> Iterator[int]:
    """Own vc-frame's flock before spawn so the child can inherit it."""

    root, path = _vc_frame_transfer_lock_path(
        control_plane,
        runtime_payload,
        create_parents=True,
    )
    descriptor: int | None = None
    try:
        if path.is_symlink():
            raise TransferProofError(f"transfer lock is a symlink: {path}")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        _validate_open_transfer_lock(path, root, descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise _TransferLockBusy(
                    f"another triage process owns transfer lock: {path}"
                ) from error
            raise TransferProofError(f"cannot acquire transfer lock: {path}") from error
        _validate_open_transfer_lock(path, root, descriptor)
        yield descriptor
    except (_TransferLockBusy, TransferProofError):
        raise
    except (OSError, ValueError) as error:
        raise TransferProofError(f"cannot own transfer lock: {path}") from error
    finally:
        if descriptor is not None:
            # Never issue LOCK_UN here. pass_fds gives vc-frame a descriptor
            # for the same open file description; explicitly unlocking the
            # parent's fd would therefore unlock the living child too. Closing
            # only this descriptor releases the lock when no child inherited
            # it, and preserves it until the last inheriting child closes.
            os.close(descriptor)


def load_durable_transfer_proof(
    control_plane: Path,
    runtime_meta: Path,
) -> DurableTransferProof:
    """Validate vc-frame files, exact runtime projection, and terminal settlement."""
    root = _canonical_root(control_plane)
    runtime_bytes = _read_bound_file(runtime_meta, root, "runtime meta")
    payload = _json_object(runtime_bytes, "runtime meta")
    run_id = _safe_run_id(payload.get("run_id"))
    expected_runtime_meta = root / "runtime_runs" / run_id / "meta.json"
    if runtime_meta != expected_runtime_meta:
        raise TransferProofError("runtime meta path does not match its run id")

    proof = load_vc_frame_transfer_proof(root, payload)
    triage = payload.get("triage")
    triage_verdict = payload.get("triage_verdict")
    expected_bucket = (
        _BUCKET_FOR_VERDICT.get(triage) if isinstance(triage, str) else None
    )
    if (
        not isinstance(triage, str)
        or expected_bucket is None
        or triage_verdict != triage
        or payload.get("triage_pending") is not False
        or payload.get("triage_bucket") != expected_bucket
        or proof.bucket_session != expected_bucket
    ):
        raise TransferProofError("runtime triage is not one exact terminal verdict")

    revision = payload.get("settlement_revision")
    settlement_verdict = payload.get("settlement_verdict")
    settlement_tui = payload.get("settlement_tui")
    await_outcome = payload.get("await_outcome")
    if type(revision) is not int or revision <= 0:
        raise TransferProofError("runtime settlement revision is missing")
    if (
        not isinstance(settlement_verdict, str)
        or settlement_verdict not in _SETTLEMENT_TUI
        or settlement_tui != _SETTLEMENT_TUI[settlement_verdict]
        or await_outcome not in _TERMINAL_AWAIT_OUTCOMES
    ):
        raise TransferProofError("runtime settlement is not terminal and typed")
    normalized_settlement = (
        VERDICT_FAILED if settlement_verdict == "invalid" else settlement_verdict
    )
    if normalized_settlement != triage_verdict:
        raise TransferProofError("settlement and triage verdicts disagree")

    settlement = (revision, settlement_verdict, settlement_tui)
    if _triage_settlement_identity(payload) != settlement:
        raise TransferProofError("runtime triage settlement is absent or stale")
    if (
        proof.settlement_revision,
        proof.settlement_verdict,
        proof.settlement_tui,
    ) != settlement:
        raise TransferProofError("transfer proof settlement is absent or stale")
    projection = proof.projection()
    if (
        payload.get("triage_transfer_receipt") != str(proof.receipt_path)
        or payload.get("triage_transfer") != projection
    ):
        raise TransferProofError("runtime transfer projection is absent or stale")
    return proof


@dataclass(frozen=True)
class RunClassification:
    """Where a finished run belongs, and the evidence that put it there."""

    verdict: str
    reason: str
    #: Copied from meta ``cost_usd`` when that field already exists. Never
    #: invented here. Parents should still aggregate this for
    #: ``infra_failure`` children — a dead provider run is not a free run.
    cost_usd: float | None = None

    @property
    def bucket(self) -> str:
        """The vc-frame session name for this verdict."""
        return _BUCKET_FOR_VERDICT[self.verdict]

    @property
    def bucket_flag(self) -> str:
        """The value for ``triage-run --bucket``."""
        return _BUCKET_FLAG_FOR_VERDICT[self.verdict]


def _attention(reason: str) -> RunClassification:
    """Shorthand for a ``needs_attention`` verdict carrying its evidence string."""
    return RunClassification(VERDICT_NEEDS_ATTENTION, reason)


# Axis field names written by lifecycle/ship receipts and nested under
# ``delivery_axes``. Presence of any of these (or a nested receipt body) is
# the switch that hands the drawer to the kernel path.
_KERNEL_AXIS_KEYS = ("execution_state", "proof_state", "delivery_state")


@dataclass(frozen=True)
class KernelAxes:
    """Delivery-kernel axes when a kernel receipt is present for the run.

    Constructed only when a receipt exists. Individual fields may be ``None``
    (unreadable). ``corrupt=True`` means the receipt body itself could not be
    parsed — that is not the same as "no receipt", and fails closed.
    """

    execution_state: str | None = None
    proof_state: str | None = None
    delivery_state: str | None = None
    corrupt: bool = False


def _normalize_axis_value(raw: Any) -> str | None:
    """Coerce one axis field. ``None`` / blank → unreadable (fail closed)."""
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip().lower()
        return text or None
    # Enums and other value-bearing objects: take their value/str form.
    enum_value = getattr(raw, "value", None)
    if isinstance(enum_value, str):
        text = enum_value.strip().lower()
        return text or None
    text = str(raw).strip().lower()
    return text or None


def _kernel_axes_from_mapping(source: Mapping[str, Any]) -> KernelAxes:
    """Read the three kernel axes off a mapping, patching a legacy status field.

    A launched/running ``execution_state`` paired with top-level ``status ==
    "failed"`` is corrected to ``execution_state == "failed"`` — older receipts
    predate the execution axis being written accurately at failure time.
    """
    execution = _normalize_axis_value(source.get("execution_state"))
    if (
        execution in ("launched", "running")
        and str(source.get("status") or "") == "failed"
    ):
        execution = "failed"
    return KernelAxes(
        execution_state=execution,
        proof_state=_normalize_axis_value(source.get("proof_state")),
        delivery_state=_normalize_axis_value(source.get("delivery_state")),
        corrupt=False,
    )


def _load_axes_blob(raw: Any) -> KernelAxes | None:
    """Parse a nested ``delivery_axes`` value into axes or a corrupt marker.

    Returns ``None`` only when ``raw`` is a missing/empty marker (caller should
    fall through to top-level keys). A present-but-broken body is corrupt.
    """
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return _kernel_axes_from_mapping(raw)
    if not isinstance(raw, str):
        return KernelAxes(corrupt=True)
    text = raw.strip()
    if not text:
        return None
    # Inline JSON object.
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return KernelAxes(corrupt=True)
        if not isinstance(payload, Mapping):
            return KernelAxes(corrupt=True)
        return _kernel_axes_from_mapping(payload)
    # Path to a receipt file on disk.
    path = Path(text)
    try:
        if not path.is_file():
            return KernelAxes(corrupt=True)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return KernelAxes(corrupt=True)
    if not isinstance(payload, Mapping):
        return KernelAxes(corrupt=True)
    return _kernel_axes_from_mapping(payload)


def read_kernel_axes(meta: Mapping[str, Any]) -> KernelAxes | None:
    """Extract kernel axes from a run receipt, or ``None`` if no receipt exists.

    Presence rules (any one is enough):

    * nested ``delivery_axes`` mapping / JSON / path
    * any of ``execution_state`` / ``proof_state`` / ``delivery_state`` on meta

    A present-but-unreadable body returns :class:`KernelAxes` with
    ``corrupt=True`` — never raises, never pretends the receipt was absent.
    """
    if "delivery_axes" in meta:
        loaded = _load_axes_blob(meta.get("delivery_axes"))
        if loaded is not None:
            return loaded
        # Explicit null/empty delivery_axes still counts as a receipt attempt
        # only when other axis keys are also absent; fall through.
    if any(key in meta for key in _KERNEL_AXIS_KEYS):
        return _kernel_axes_from_mapping(meta)
    return None


# Provider-overload markers. Specimen: "API 529 Overloaded" (postmortem
# 2026-08-19 §C3). Codes require an HTTP/API/status/error frame so a traceback
# line number 429 does not become infra_failure.
_PROVIDER_HTTP_RE = re.compile(
    r"(?:api|http(?:s)?|status(?:\s+code)?|error|code)[\s:=#/-]*(?:429|529)\b"
    r"|\b(?:429|529)\s+(?:overloaded|too\s+many|error|unavailable)",
    re.IGNORECASE,
)
_PROVIDER_OVERLOADED_RE = re.compile(r"\boverloaded\b", re.IGNORECASE)
_PROVIDER_USAGE_LIMIT_RE = re.compile(r"\busage[-\s_]?limit\b", re.IGNORECASE)
_PROVIDER_RATE_LIMIT_RE = re.compile(r"\brate[-\s_]?limit\b", re.IGNORECASE)
_META_PROVIDER_TEXT_KEYS = (
    "error",
    "last_error",
    "provider_error",
    "message",
    "status_message",
    "stderr",
    "failure_reason",
    "incomplete_reason",
)
_TRANSCRIPT_PROVIDER_TAIL_BYTES = 64 * 1024


def classify_provider_error(transcript_text: str) -> str | None:
    """Return a ``provider_error:*`` reason if the text is provider overload.

    Matches HTTP 429/529, ``overloaded``, ``usage limit`` / ``usage-limit``,
    and ``rate limit``. Returns ``None`` for ordinary worker traces.
    """
    text = str(transcript_text or "")
    if not text.strip():
        return None
    if _PROVIDER_HTTP_RE.search(text):
        lowered = text.lower()
        if "529" in lowered:
            return "provider_error:529"
        return "provider_error:429"
    if _PROVIDER_OVERLOADED_RE.search(text):
        return "provider_error:overloaded"
    if _PROVIDER_USAGE_LIMIT_RE.search(text):
        return "provider_error:usage_limit"
    if _PROVIDER_RATE_LIMIT_RE.search(text):
        return "provider_error:rate_limit"
    return None


def _classify_from_kernel_axes(axes: KernelAxes) -> RunClassification:
    """Drawer from the three delivery-kernel axes. Fail closed on uncertainty.

    * ``delivery=sealed`` → finalized (seal is authority; legacy signals ignored)
    * ``execution=failed`` or ``proof∈{failed,invalid}`` → failed
    * every other combination, partial axes, or corrupt receipt → needs_attention
    """
    if axes.corrupt:
        return _attention("kernel_axes_unreadable")

    delivery = axes.delivery_state
    execution = axes.execution_state
    proof = axes.proof_state

    if delivery == "sealed":
        return RunClassification(VERDICT_FINALIZED, "delivery_sealed")

    if execution == "failed":
        return RunClassification(VERDICT_FAILED, "execution_failed")
    if proof == "failed":
        return RunClassification(VERDICT_FAILED, "proof_failed")
    if proof == "invalid":
        return RunClassification(VERDICT_FAILED, "proof_invalid")

    # Partial, in-progress, or honest-but-unsealed terminals.
    parts = [
        f"e={execution or 'none'}",
        f"p={proof or 'none'}",
        f"d={delivery or 'none'}",
    ]
    return _attention("axes_" + "_".join(parts))


def _classify_from_legacy_signals(
    exit_code: Any,
    run_state: Any,
    report_exists: bool | None,
    report_bytes: int | None,
    transcript_bytes: int | None,
    *,
    report_claim_status: str = "",
    report_frontmatter_ok: bool | None = None,
) -> RunClassification:
    """Legacy five-signal conjunction. Caller overlays provider errors."""
    state = str(run_state or "").strip().lower()
    if not state:
        return _attention("state_unreadable")
    if state in _STATES_CONTRADICTORY:
        return _attention(f"state_{state}")
    if state not in _STATES_DELIVERED and state not in _STATES_DIED:
        return _attention(f"state_unrecognized:{state}")

    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        return _attention("exit_code_unreadable")

    if report_exists is None:
        return _attention("report_unreadable")

    delivered = bool(report_exists)
    if delivered:
        if report_bytes is None:
            return _attention("report_size_unreadable")
        if report_bytes < MINIMAL_REPORT_BYTES:
            return _attention("report_empty")
        # Mandatory frontmatter when checked (False). None = not evaluated (unit tests).
        if report_frontmatter_ok is False:
            return _attention("report_frontmatter_invalid")

    claim = str(report_claim_status or "").strip().lower()
    from .report_contract import (
        CLAIM_BLOCKED,
        CLAIM_COMPLETED,
        CLAIM_FAILED,
        CLAIM_PARTIAL,
    )

    if code == 0:
        if not delivered:
            # The 2026-05-14 specimen: top-level success, nothing delivered.
            return _attention("exit_0_without_report")
        if state not in _STATES_DELIVERED:
            return _attention(f"exit_0_but_state_{state}")
        if claim in CLAIM_FAILED:
            return _attention("exit_0_but_claim_failed")
        if claim in CLAIM_BLOCKED or claim in CLAIM_PARTIAL:
            return _attention(f"exit_0_claim_{claim or 'partial'}")
        # claim completed / empty: empty allowed only if frontmatter_ok is True
        # (required keys present including status). Missing claim after ok FM
        # should not happen; treat unrecognized as attention.
        if claim and claim not in CLAIM_COMPLETED:
            return _attention(f"exit_0_claim_unrecognized:{claim}")
        return RunClassification(VERDICT_FINALIZED, "exit_0_report_delivered")

    # Non-zero exit from here down.
    if delivered:
        # Claim can admit failure while still leaving a report for the board.
        if claim in CLAIM_FAILED:
            return RunClassification(
                VERDICT_FAILED, f"exit_{code}_claim_failed_with_report"
            )
        # The mirror specimen: the run says it died, the artifacts say otherwise.
        return _attention(f"exit_{code}_with_report")
    if state not in _STATES_DIED:
        return _attention(f"exit_{code}_but_state_{state}")
    if transcript_bytes is None:
        return _attention("transcript_unreadable")
    if transcript_bytes >= MINIMAL_TRANSCRIPT_BYTES:
        # It died, but not before working. Whatever it managed is worth a look.
        return _attention(f"exit_{code}_no_report_after_{transcript_bytes}b")
    return RunClassification(
        VERDICT_FAILED,
        f"exit_{code}_no_report_transcript_{transcript_bytes}b",
    )


def classify_run(
    exit_code: Any,
    run_state: Any,
    report_exists: bool | None,
    report_bytes: int | None,
    transcript_bytes: int | None,
    *,
    kernel_axes: KernelAxes | None = None,
    report_claim_status: str = "",
    report_frontmatter_ok: bool | None = None,
    transcript_text: str = "",
    meta_text: str = "",
    cost_usd: float | None = None,
) -> RunClassification:
    """Decide a finished run's drawer from its signals.

    Pure. Four outcomes; three of them are confident.

    When ``kernel_axes`` is provided (a delivery-kernel receipt was present),
    the three orthogonal axes decide:

    * **finalized** — ``delivery_state=sealed``
    * **failed** — ``execution_state=failed`` or ``proof_state∈{failed,invalid}``
    * **needs_attention** — every other axis combination, and any unreadable
      receipt body

    Provider overload in ``transcript_text`` / ``meta_text`` (HTTP 429/529,
    ``overloaded``, ``usage limit``, ``usage-limit``, ``rate limit``) overlays
    any non-finalized drawer as **infra_failure**. That class is not a worker
    error and must not fold into ``failed``. A sealed delivery still wins:
    the run delivered.

    When no kernel receipt is present (``kernel_axes is None``), the legacy
    five-signal conjunction applies (then the same provider overlay):

    * **finalized** — exit 0, a state asserting delivery, a non-empty report
      with valid frontmatter claim, and claim not contradicting death. Agent
      claim alone never finalizes.
    * **failed** — exit non-zero, a state asserting death, no report, and a
      transcript too small to contain work. A run that died before doing any.
    * **needs_attention** — everything else. Every contradiction between signals
      (exit 0 with no report, non-zero exit *with* a report, a state that
      disagrees with the exit code, ``report_invalid``/``contract_failed``/
      ``ghost``/``timed_out``), missing/invalid report frontmatter, claim
      vs evidence conflicts, and every signal we could not read.

    ``cost_usd`` is copied from an existing meta field when the caller has one.
    This function does not invent a billing backend. Parents should still
    aggregate ``cost_usd`` for ``infra_failure`` children.

    The last clause is the point: an unreadable signal fails closed, to a human,
    never to a confident drawer. ``report_exists=None`` and
    ``transcript_bytes=None`` mean "could not stat", not "absent".
    """
    if kernel_axes is not None:
        classified = _classify_from_kernel_axes(kernel_axes)
    else:
        classified = _classify_from_legacy_signals(
            exit_code,
            run_state,
            report_exists,
            report_bytes,
            transcript_bytes,
            report_claim_status=report_claim_status,
            report_frontmatter_ok=report_frontmatter_ok,
        )

    provider_reason = classify_provider_error(
        "\n".join(part for part in (transcript_text, meta_text) if part)
    )
    if provider_reason and classified.verdict != VERDICT_FINALIZED:
        return RunClassification(
            VERDICT_INFRA_FAILURE,
            provider_reason,
            cost_usd=cost_usd,
        )
    if cost_usd is None:
        return classified
    return replace(classified, cost_usd=cost_usd)


@dataclass(frozen=True)
class RunSignals:
    """The classifier's inputs, read off a run's meta payload and its artifacts.

    ``None`` always means "could not read", never "absent" — the distinction the
    classifier needs to fail closed. ``kernel_axes`` is ``None`` when no delivery
    kernel receipt is present (legacy path); a :class:`KernelAxes` instance when
    one is, including the corrupt case.
    """

    exit_code: Any
    run_state: str
    report_exists: bool | None
    report_bytes: int | None
    transcript_bytes: int | None
    kernel_axes: KernelAxes | None = None
    # Agent claim from report frontmatter (claim_status/status). Empty = absent.
    report_claim_status: str = ""
    report_frontmatter_ok: bool | None = None
    transcript_text: str = ""
    meta_text: str = ""
    cost_usd: float | None = None

    def classify(self) -> RunClassification:
        """Classify this signal bundle via :func:`classify_run`."""
        return classify_run(
            self.exit_code,
            self.run_state,
            self.report_exists,
            self.report_bytes,
            self.transcript_bytes,
            kernel_axes=self.kernel_axes,
            report_claim_status=self.report_claim_status,
            report_frontmatter_ok=self.report_frontmatter_ok,
            transcript_text=self.transcript_text,
            meta_text=self.meta_text,
            cost_usd=self.cost_usd,
        )


def _optional_cost_usd(raw: Any) -> float | None:
    """Parse meta ``cost_usd`` when already present. Never invent a value."""
    if raw is None or raw is False:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text.lower() == "unknown":
            return None
        raw = text
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _meta_provider_text(meta: Mapping[str, Any]) -> str:
    """Concatenate string error fields that may name a provider overload."""
    parts: list[str] = []
    for key in _META_PROVIDER_TEXT_KEYS:
        value = meta.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                parts.append(text)
            continue
        if isinstance(value, Mapping):
            for inner in ("message", "error", "reason", "type", "incomplete_reason"):
                nested = value.get(inner)
                if isinstance(nested, str) and nested.strip():
                    parts.append(nested.strip())
    return "\n".join(parts)


def _transcript_tail_text(raw: Any) -> str:
    """Last ``_TRANSCRIPT_PROVIDER_TAIL_BYTES`` of a declared transcript.

    Fail-open: missing/unreadable path → empty string. The classifier then
    falls through to the other signals instead of inventing infra_failure.
    """
    path = str(raw or "").strip()
    if not path:
        return ""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    if len(data) > _TRANSCRIPT_PROVIDER_TAIL_BYTES:
        data = data[-_TRANSCRIPT_PROVIDER_TAIL_BYTES:]
    return data.decode("utf-8", errors="replace")


def _stat_artifact(raw: Any) -> tuple[bool | None, int | None]:
    """``(exists, bytes)`` for an artifact path.

    Three distinguishable answers, because the classifier needs them apart:
    an undeclared path is unknown (``None`` bytes — we do not know where to
    look), a declared path that is not there is a known zero, and an ``OSError``
    is unreadable in both fields.
    """
    path = str(raw or "").strip()
    if not path:
        return False, None
    try:
        target = Path(path)
        if not target.exists():
            return False, 0
        return True, target.stat().st_size
    except OSError:
        return None, None


def read_run_signals(meta: Mapping[str, Any]) -> RunSignals:
    """Gather the classifier's inputs from a run's meta payload.

    The only impure step in the chain — it stats the report and the transcript.
    ``Path.stat`` follows symlinks, which matters: ``spawn.finalize_artifacts``
    leaves a compat symlink at the announced path, so the announced transcript is
    routinely a link to the real one.
    """
    report_exists, report_bytes = _stat_artifact(meta.get("report"))
    _, transcript_bytes = _stat_artifact(meta.get("transcript"))
    # `state` is the control-plane spelling, `status` the launcher meta's. Either
    # is the run's own account of how it ended.
    run_state = str(meta.get("state") or meta.get("status") or "").strip()

    claim_status = str(meta.get("report_claim_status") or "").strip()
    frontmatter_ok: bool | None = None
    report_path = str(meta.get("report") or "").strip()
    if report_exists and report_path:
        from .report_contract import validate_report_file

        fm = validate_report_file(report_path, require_frontmatter=True)
        frontmatter_ok = fm.ok
        if not claim_status:
            claim_status = fm.claim_status
    elif report_exists is False:
        frontmatter_ok = None

    return RunSignals(
        exit_code=meta.get("exit_code"),
        run_state=run_state,
        report_exists=report_exists,
        report_bytes=report_bytes,
        transcript_bytes=transcript_bytes,
        kernel_axes=read_kernel_axes(meta),
        report_claim_status=claim_status,
        report_frontmatter_ok=frontmatter_ok,
        transcript_text=_transcript_tail_text(meta.get("transcript")),
        meta_text=_meta_provider_text(meta),
        cost_usd=_optional_cost_usd(meta.get("cost_usd")),
    )


def bucket_for_exit_code(exit_code: Any) -> str:
    """Map a run's exit code to its vc-frame bucket — the degraded path only.

    :func:`classify_run` is the verdict. This survives for one case: a vc-frame
    predating ``triage-run --bucket``, which buckets by exit code on its own. We
    mirror its arithmetic so the receipt can name the destination it will pick.

    Mirrors vc-frame's ``BucketKind::for_exit_code``: exit 0 is the only success.
    Timeouts and kills arrive as their signal-derived codes (137, 143, ...) and
    are non-zero, so they land in "Needs attention" without a special case.

    An unparseable or missing exit code is treated as failure — a run whose
    outcome we cannot read is precisely a run that needs attention.
    """
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        return BUCKET_NEEDS_ATTENTION
    return BUCKET_FINALIZED if code == 0 else BUCKET_NEEDS_ATTENTION


def outcome_for_exit_code(exit_code: Any) -> str:
    """Receipt outcome corresponding to :func:`bucket_for_exit_code`."""
    return (
        OUTCOME_FINALIZED
        if bucket_for_exit_code(exit_code) == BUCKET_FINALIZED
        else OUTCOME_NEEDS_ATTENTION
    )


@dataclass(frozen=True)
class TriagePlan:
    """The validated transfer decision, rendered without further filesystem reads."""

    should_run: bool
    skip_reason: str = ""
    run_id: str = ""
    exit_code: int = 0
    bucket: str = ""
    verdict: str = ""
    verdict_reason: str = ""
    settlement_revision: int = 0
    settlement_verdict: str = ""
    settlement_tui: str = ""
    origin_session: str = ""
    origin_tab: str = ""
    pane_id: str = ""
    cwd: str = ""
    runtime_transcript: str = ""
    command: tuple[str, ...] = ()

    def argv(self, binary: str, with_bucket: bool = True) -> list[str]:
        """Render the ``vc-frame triage-run`` invocation for this plan.

        ``with_bucket=False`` omits ``--bucket`` for a binary predating W2-B-4a,
        leaving vc-frame to bucket by exit code on its own — the degraded path.
        """
        argv = [
            binary,
            "triage-run",
            "--run",
            self.run_id,
        ]
        if self.exit_code < 0:
            # Clap treats a standalone negative value as another option unless
            # the consumer opts into hyphen values. The equals form is
            # unambiguous across old and current vc-frame binaries.
            argv.append(f"--exit-code={self.exit_code}")
        else:
            argv += ["--exit-code", str(self.exit_code)]
        if self.settlement_revision > 0:
            argv += ["--settlement-revision", str(self.settlement_revision)]
        if with_bucket and self.verdict:
            argv += ["--bucket", _BUCKET_FLAG_FOR_VERDICT[self.verdict]]
        if self.origin_session:
            argv += ["--origin-session", self.origin_session]
        if self.origin_tab:
            argv += ["--origin-tab", self.origin_tab]
        if self.pane_id:
            argv += ["--pane-id", self.pane_id]
        if self.cwd:
            argv += ["--cwd", self.cwd]
        if self.runtime_transcript:
            argv += ["--runtime-transcript", self.runtime_transcript]
        if self.command:
            # `command` is clap `last(true)`: everything after `--` is the
            # original command line, preserved for the rerun pane.
            argv += ["--", *self.command]
        return argv


@dataclass
class TriageOutcome:
    """What actually happened, as written into the run's receipt."""

    outcome: str
    reason: str = ""
    bucket: str = ""
    #: True only for the intent written before the transfer is attempted. A
    #: receipt left pending means the process did not survive its own transfer.
    pending: bool = False
    #: The classifier's verdict and its evidence, preserved independently of the
    #: outcome so the operator can audit *why* a run went where — including when
    #: the transfer later broke, or when the destination was degraded.
    verdict: str = ""
    verdict_reason: str = ""
    #: Non-empty when the destination was not the classifier's to choose:
    #: ``exit_code_only`` for a vc-frame predating ``--bucket``.
    verdict_degraded: str = ""

    def receipt(self) -> dict[str, Any]:
        """Field set to merge into meta.json to durably record this outcome."""
        payload: dict[str, Any] = {
            "triage": self.outcome,
            "triage_pending": self.pending,
        }
        # Always written, so a confirming receipt clears a stale intent.
        payload["triage_reason"] = self.reason
        payload["triage_bucket"] = self.bucket
        payload["triage_verdict"] = self.verdict
        payload["triage_verdict_reason"] = self.verdict_reason
        payload["triage_verdict_degraded"] = self.verdict_degraded
        return payload


@dataclass(frozen=True)
class TriageSweepItem:
    """One terminal run examined by the independent triage reconciler."""

    run_id: str
    meta_path: str
    outcome: str
    reason: str = ""
    bucket: str = ""


@dataclass(frozen=True)
class TriageSweepReport:
    """Bounded, inspectable result of one recovery sweep."""

    scanned: int
    attempted: int
    items: tuple[TriageSweepItem, ...]
    errors: tuple[TriageSweepItem, ...]
    truncated: bool = False

    @property
    def ok(self) -> bool:
        """Whether the sweep ran clean: no errors and the full page was scanned."""
        return not self.errors and not self.truncated


def _settlement_identity(
    payload: Mapping[str, Any],
) -> tuple[int, str, str] | None:
    """Extract ``(revision, verdict, tui)`` from the runtime's own settlement fields.

    Returns ``None`` unless the top-level fields are exactly typed and, when a
    nested ``settlement`` block is also present, agree with it field-for-field.
    """
    revision = payload.get("settlement_revision")
    verdict = payload.get("settlement_verdict")
    tui = payload.get("settlement_tui")
    if (
        type(revision) is not int
        or revision <= 0
        or not isinstance(verdict, str)
        or verdict not in _SETTLEMENT_TUI
        or tui != _SETTLEMENT_TUI[verdict]
    ):
        return None

    if "settlement" in payload:
        nested = payload.get("settlement")
        if (
            not isinstance(nested, Mapping)
            or type(nested.get("revision")) is not int
            or nested.get("revision") != revision
            or nested.get("verdict") != verdict
            or nested.get("tui") != tui
        ):
            return None
    return revision, verdict, tui


def _has_settlement_material(payload: Mapping[str, Any]) -> bool:
    """Whether runtime meta claims any canonical settlement representation."""

    return any(field in payload for field in _SETTLEMENT_MATERIAL_FIELDS)


def _triage_settlement_identity(
    payload: Mapping[str, Any],
) -> tuple[int, str, str] | None:
    """Extract the settlement ``(revision, verdict, tui)`` triage last recorded.

    Sibling of :func:`_settlement_identity` but reads the ``triage_settlement_*``
    mirror fields written by this module, not the runtime's own settlement.
    """
    revision = payload.get("triage_settlement_revision")
    verdict = payload.get("triage_settlement_verdict")
    tui = payload.get("triage_settlement_tui")
    if (
        type(revision) is int
        and revision > 0
        and isinstance(verdict, str)
        and verdict in _SETTLEMENT_TUI
        and tui == _SETTLEMENT_TUI[verdict]
    ):
        return revision, verdict, tui
    return None


def _receipt_outcome(
    payload: Mapping[str, Any],
    *,
    require_current_settlement: bool = True,
) -> TriageOutcome | None:
    """Return a completed transfer receipt, never a pending intent."""

    outcome = str(payload.get("triage") or "").strip()
    if payload.get("triage_pending") is not False or outcome not in _BUCKET_FOR_VERDICT:
        return None
    bucket = payload.get("triage_bucket")
    verdict = payload.get("triage_verdict")
    degraded = payload.get("triage_verdict_degraded")
    if (
        bucket != _BUCKET_FOR_VERDICT[outcome]
        or not isinstance(verdict, str)
        or verdict not in _BUCKET_FOR_VERDICT
        or degraded not in {"", "exit_code_only"}
    ):
        return None
    if degraded == "":
        if verdict != outcome:
            return None
    elif outcome != outcome_for_exit_code(
        payload.get("exit_code")
    ) or bucket != bucket_for_exit_code(payload.get("exit_code")):
        return None
    if require_current_settlement:
        current_settlement = _settlement_identity(payload)
        has_settlement = _has_settlement_material(payload)
        if has_settlement and (
            current_settlement is None
            or _triage_settlement_identity(payload) != current_settlement
        ):
            return None
        if current_settlement is not None:
            settlement_verdict = (
                VERDICT_FAILED
                if current_settlement[1] == "invalid"
                else current_settlement[1]
            )
            if (
                degraded != ""
                or verdict != settlement_verdict
                or outcome != settlement_verdict
                or bucket != _BUCKET_FOR_VERDICT[settlement_verdict]
            ):
                return None
    return TriageOutcome(
        outcome=outcome,
        reason=str(payload.get("triage_reason") or ""),
        bucket=bucket,
        verdict=verdict,
        verdict_reason=str(payload.get("triage_verdict_reason") or ""),
        verdict_degraded=degraded,
    )


def _proof_projection_is_exact(
    payload: Mapping[str, Any],
    *,
    control_plane: Path,
) -> bool:
    """Whether terminal triage is linked to its exact current durable proof."""

    completed = _receipt_outcome(payload)
    if completed is None:
        return False
    try:
        proof = load_vc_frame_transfer_proof(control_plane, payload)
    except TransferProofError:
        return False
    return (
        proof.bucket_session == completed.bucket
        and payload.get("triage_transfer_receipt") == str(proof.receipt_path)
        and payload.get("triage_transfer") == proof.projection()
    )


def triage_outcome_is_complete(outcome: TriageOutcome) -> bool:
    """Whether durable reconciliation may retire its work item."""

    return outcome.outcome in _BUCKET_FOR_VERDICT or (
        outcome.outcome == OUTCOME_SKIPPED and outcome.reason in _PERMANENT_SKIP_REASONS
    )


def _proof_recovery_outcome(payload: Mapping[str, Any]) -> TriageOutcome | None:
    """Recover the exact pre-transfer destination from pending or legacy error."""

    pending = payload.get("triage_pending") is True
    recoverable_error = (
        payload.get("triage") == OUTCOME_ERROR
        and payload.get("triage_pending") is False
    )
    if not pending and not recoverable_error:
        return None
    current_settlement = _settlement_identity(payload)
    has_settlement = _has_settlement_material(payload)
    if has_settlement and (
        current_settlement is None
        or _triage_settlement_identity(payload) != current_settlement
    ):
        return None
    outcome = payload.get("triage")
    bucket = payload.get("triage_bucket")
    verdict = payload.get("triage_verdict")
    reason = payload.get("triage_reason")
    verdict_reason = payload.get("triage_verdict_reason")
    degraded = payload.get("triage_verdict_degraded")
    if recoverable_error:
        if degraded == "":
            outcome = verdict
        elif degraded == "exit_code_only":
            outcome = outcome_for_exit_code(payload.get("exit_code"))
        reason = verdict_reason
    if (
        not isinstance(outcome, str)
        or outcome not in _BUCKET_FOR_VERDICT
        or bucket != _BUCKET_FOR_VERDICT[outcome]
        or not isinstance(verdict, str)
        or verdict not in _BUCKET_FOR_VERDICT
        or not isinstance(reason, str)
        or not isinstance(verdict_reason, str)
        or degraded not in {"", "exit_code_only"}
    ):
        return None
    if degraded == "":
        if outcome != verdict or bucket != _BUCKET_FOR_VERDICT[verdict]:
            return None
    elif outcome != outcome_for_exit_code(
        payload.get("exit_code")
    ) or bucket != bucket_for_exit_code(payload.get("exit_code")):
        return None
    if current_settlement is not None:
        canonical_verdict = (
            VERDICT_FAILED
            if current_settlement[1] == "invalid"
            else current_settlement[1]
        )
        if (
            degraded != ""
            or verdict != canonical_verdict
            or outcome != canonical_verdict
            or bucket != _BUCKET_FOR_VERDICT[canonical_verdict]
        ):
            return None
    return TriageOutcome(
        outcome=outcome,
        reason=reason,
        bucket=bucket,
        verdict=verdict,
        verdict_reason=verdict_reason,
        verdict_degraded=degraded,
    )


def plan_triage(
    meta: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
) -> TriagePlan:
    """Decide whether this finished run may be transferred, and with what arguments.

    Reads declared artifact evidence but never mutates runtime or terminal state.
    """
    env = os.environ if env is None else env

    def _env(*names: str) -> str:
        """First non-blank stripped value found in ``env`` across ``names``."""
        for name in names:
            value = str(env.get(name, "") or "").strip()
            if value:
                return value
        return ""

    if str(env.get("VIBECRAFTED_TRIAGE_RUN", "") or "").strip().lower() in _TRUTHY_OFF:
        return TriagePlan(should_run=False, skip_reason="disabled")

    runtime = (
        str(meta.get("runtime") or env.get("VIBECRAFTED_RUNTIME", "") or "")
        .strip()
        .lower()
    )
    if runtime == "headless":
        return TriagePlan(should_run=False, skip_reason="headless")

    run_id = str(meta.get("run_id", "") or "").strip() or _env(
        "SPAWN_RUN_ID", "VIBECRAFTED_RUN_ID"
    )
    if not run_id:
        return TriagePlan(should_run=False, skip_reason="no_run_id")

    def _meta_str(*names: str) -> str:
        """First non-blank stripped value found in ``meta`` across ``names``."""
        for name in names:
            value = str(meta.get(name, "") or "").strip()
            if value:
                return value
        return ""

    # Origin identity: prefer durable meta fields (dispatcher path stamps them
    # at finish; shell launchers may already have them). Fall back to live pane
    # env for the classic in-tab finish path.
    origin_session = _meta_str(
        "origin_session",
        "vc_frame_session",
        "operator_session",
        "worker_session",
    ) or _env(
        "VIBECRAFTED_WORKER_SESSION",
        "VIBECRAFTED_OPERATOR_SESSION",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ_SESSION_NAME",
    )
    tab_name = (
        _meta_str("origin_tab", "vc_frame_tab", "tab_name")
        or _env("VC_FRAME_TAB_NAME")
        or run_id
    )
    # The ambient pane is only the run's own pane when this process sits in the
    # run's tab (the classic in-tab finish, where vc_frame.sh names the tab by
    # run id). A dispatcher inherits the *operator's* pane env instead, and
    # aiming dump-screen at that pane captures the wrong terminal — or nothing,
    # once the id no longer resolves (2026-07-25: every dispatched run stamped
    # pane "1", scrollback dump missing, tab never bucketed).
    pane_id = _meta_str("origin_pane_id", "vc_frame_pane_id", "pane_id")
    if not pane_id and _env("VC_FRAME_TAB_NAME") == tab_name:
        pane_id = _env("VC_FRAME_PANE_ID", "ZELLIJ_PANE_ID")

    # Headless / CI / detached (setsid) runs have no pane env and no stamped
    # host session. Not an error — there is simply no terminal to triage.
    # Meta-stamped origin_session is enough for the Python dispatcher path:
    # triage-run targets that session by name without needing ambient pane env.
    in_frame = bool(
        pane_id
        or origin_session
        or _env("VC_FRAME_PANE_ID", "ZELLIJ_PANE_ID")
        or "VC_FRAME" in env
        or "ZELLIJ" in env
    )
    if not in_frame or not origin_session:
        return TriagePlan(should_run=False, skip_reason="no_session")

    # The run tab is named by run id (lib/vc_frame.sh). A marbles run instead
    # shares one tab with its siblings, so closing it would destroy their
    # scrollback along with ours. Refuse rather than guess.
    marbles_tab = _meta_str("marbles_tab_name") or _env("VIBECRAFTED_MARBLES_TAB_NAME")
    if marbles_tab and tab_name == marbles_tab and marbles_tab != run_id:
        return TriagePlan(should_run=False, skip_reason="shared_tab")

    # The same caution for any other env-sourced tab: when the meta names no
    # tab and the ambient VC_FRAME_TAB_NAME is not the run's own (dispatcher
    # env leaking the operator's tab), transferring would capture and close a
    # tab that was never ours. Refuse rather than guess.
    if not _meta_str("origin_tab", "vc_frame_tab", "tab_name"):
        env_tab = _env("VC_FRAME_TAB_NAME")
        if env_tab and env_tab != run_id:
            return TriagePlan(should_run=False, skip_reason="foreign_tab")

    exit_code_raw: Any = meta.get("exit_code")
    try:
        exit_code = int(exit_code_raw)
    except (TypeError, ValueError):
        exit_code = 1

    # A typed settlement is the canonical terminal answer.  Heuristics remain
    # only for legacy runs that predate settlement; a partial/corrupt settlement
    # must never silently fall back to a contradictory destination.
    settlement = _settlement_identity(meta)
    has_settlement = _has_settlement_material(meta)
    if has_settlement and settlement is None:
        return TriagePlan(should_run=False, skip_reason="invalid_settlement")
    if settlement is not None:
        settlement_revision, raw_settlement_verdict, settlement_tui = settlement
        canonical_verdict = (
            VERDICT_FAILED
            if raw_settlement_verdict == "invalid"
            else raw_settlement_verdict
        )
        classification = RunClassification(
            canonical_verdict,
            f"canonical_settlement_revision_{settlement_revision}",
        )
    else:
        settlement_revision = 0
        canonical_verdict = ""
        settlement_tui = ""
        classification = read_run_signals(meta).classify()
    runtime_transcript = validate_runtime_transcript(
        meta.get("transcript"),
        run_id=run_id,
    )

    return TriagePlan(
        should_run=True,
        run_id=run_id,
        exit_code=exit_code,
        bucket=classification.bucket,
        verdict=classification.verdict,
        verdict_reason=classification.reason,
        settlement_revision=settlement_revision,
        settlement_verdict=canonical_verdict,
        settlement_tui=settlement_tui,
        origin_session=origin_session,
        origin_tab=tab_name,
        pane_id=pane_id,
        cwd=str(meta.get("root", "") or "") or _env("SPAWN_ROOT"),
        runtime_transcript=str(runtime_transcript) if runtime_transcript else "",
        command=_normalized_command(meta),
    )


def _resolve_binary(env: Mapping[str, str]) -> str:
    """Find the vc-frame binary: explicit env override, else PATH lookup.

    Returns ``""`` when neither resolves, so callers treat it as "not available"
    rather than raising.
    """
    from shutil import which

    explicit = str(env.get("VIBECRAFTED_VC_FRAME_BIN", "") or "").strip()
    if explicit:
        return explicit if Path(explicit).exists() else ""
    return which("vc-frame", path=env.get("PATH")) or ""


@dataclass(frozen=True)
class _Probe:
    """What the installed vc-frame can actually be asked to do."""

    supported: bool
    bucket: bool = False
    settlement_revision: bool = False
    inherited_lock: bool = False


def _probe_triage_run(binary: str, runner: Callable[..., Any]) -> _Probe:
    """Probe for the subcommand, and for ``--bucket`` within it.

    The installed binary may predate vc-frame ``71146085``; an older build parses
    ``triage-run`` as a stray argument and exits non-zero. Probing keeps a stale
    install a recorded skip rather than a spurious failure. A binary that has
    ``triage-run`` but predates W2-B-4a has no ``--bucket``, so its help text is
    read too: we degrade to exit-code bucketing rather than passing a flag that
    would make the whole call fail.
    """
    try:
        proc = runner([binary, "triage-run", "--help"])
    except Exception:  # noqa: BLE001
        return _Probe(supported=False)
    if getattr(proc, "returncode", 1) != 0:
        return _Probe(supported=False)
    help_text = (
        f"{getattr(proc, 'stdout', '') or ''}{getattr(proc, 'stderr', '') or ''}"
    )
    return _Probe(
        supported=True,
        bucket="--bucket" in help_text,
        settlement_revision="--settlement-revision" in help_text,
        inherited_lock="--transfer-lock-fd" in help_text,
    )


def _default_runner(
    argv: Sequence[str],
    *,
    inherited_lock_fd: int | None = None,
    control_plane: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Default ``vc-frame triage-run`` invoker: a 120s subprocess with captured output.

    When ``inherited_lock_fd`` is given, passes it through to the child via
    ``pass_fds`` and injects ``--transfer-lock-fd``/``VIBECRAFTED_CONTROL_PLANE``
    so vc-frame inherits the already-held transfer lock instead of re-acquiring it.
    """
    child_env: dict[str, str] | None = None
    pass_fds: tuple[int, ...] = ()
    if inherited_lock_fd is not None:
        child_env = dict(os.environ)
        if control_plane is not None:
            child_env["VIBECRAFTED_CONTROL_PLANE"] = str(control_plane)
        pass_fds = (inherited_lock_fd,)
        argv_with_lock = list(argv)
        try:
            command_boundary = argv_with_lock.index("--")
        except ValueError:
            command_boundary = len(argv_with_lock)
        argv_with_lock[command_boundary:command_boundary] = [
            "--transfer-lock-fd",
            str(inherited_lock_fd),
        ]
        argv = argv_with_lock
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=child_env,
        pass_fds=pass_fds,
    )


def _control_plane_root_for(
    meta: Path,
    env: Mapping[str, str],
) -> Path | None:
    """Resolve the authoritative vc-frame control plane when one is knowable.

    Explicit configuration and a canonical ``runtime_runs/<run>/meta.json``
    location are authority even before the receipt exists, so a missing proof
    fails closed.  The conventional HOME location is only adopted when present;
    this keeps detached/unit-test callers without a control plane on the legacy
    fail-open path.
    """
    explicit = str(env.get("VIBECRAFTED_CONTROL_PLANE", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve(strict=False)

    vibecrafted_home = str(env.get("VIBECRAFTED_HOME", "") or "").strip()
    if vibecrafted_home:
        return (
            Path(vibecrafted_home).expanduser().resolve(strict=False) / "control_plane"
        )

    absolute_meta = meta.expanduser().resolve(strict=False)
    if (
        absolute_meta.name == "meta.json"
        and len(absolute_meta.parents) >= 3
        and absolute_meta.parents[1].name == "runtime_runs"
    ):
        return absolute_meta.parents[2]

    home = str(env.get("HOME", "") or "").strip()
    if home:
        conventional = (
            Path(home).expanduser().resolve(strict=False)
            / ".vibecrafted"
            / "control_plane"
        )
        if conventional.is_dir():
            return conventional
    return None


def _meta_mutation_root_for(
    meta: Path,
    *,
    control_plane: Path | None,
    env: Mapping[str, str],
) -> Path:
    """Return the canonical owner root for the exact meta file being mutated.

    Runtime-run metadata is owned by ``control_plane/`` and must share its lock
    namespace with the supervisor and settlement writers. Legacy launcher
    metadata is owned by ``VIBECRAFTED_HOME`` instead; using ``control_plane/``
    for a sibling ``artifacts/`` file rejects every receipt as out-of-root.
    Detached callers without either layout use the regular file's parent.
    """

    canonical_meta = Path(os.path.abspath(meta.expanduser())).resolve(strict=True)
    candidates: list[Path] = []
    if control_plane is not None:
        candidates.append(control_plane)
    home = str(env.get("VIBECRAFTED_HOME", "") or "").strip()
    if home:
        candidates.append(Path(home).expanduser())

    for candidate in candidates:
        try:
            root = candidate.resolve(strict=True)
            canonical_meta.relative_to(root)
        except (OSError, ValueError):
            continue
        if root.is_dir():
            return root
    return canonical_meta.parent


def _canonical_runtime_meta(
    control_plane: Path,
    run_id: str,
) -> Path:
    """The one canonical ``runtime_runs/<run_id>/meta.json`` path for this run."""
    return control_plane / "runtime_runs" / run_id / "meta.json"


def _runtime_meta_identity_is_exact(
    meta: Path,
    control_plane: Path,
    run_id: str,
) -> bool:
    """Reject aliases inside runtime_runs while allowing legacy artifact meta."""

    try:
        root = control_plane.resolve(strict=True)
        runtime_runs = root / "runtime_runs"
        canonical_meta = meta.resolve(strict=True)
        relative = canonical_meta.relative_to(runtime_runs)
    except ValueError:
        return True
    except OSError:
        return False
    return relative.parts == (run_id, "meta.json")


class _TriageCallable(Protocol):
    """Call signature shared by :func:`triage_finished_run` and its wrapper."""

    def __call__(
        self,
        meta_path: str | os.PathLike[str],
        env: Mapping[str, str] | None = None,
        runner: Callable[..., Any] | None = None,
    ) -> TriageOutcome:
        """Triage the run named by ``meta_path``, returning its recorded outcome."""
        ...


def _serialized_triage_call(
    function: _TriageCallable,
) -> _TriageCallable:
    """Hold the run lock across intent, external transfer, and final receipt.

    A dispatcher hook and the always-on guardian may observe the same terminal
    transition.  Serializing only each JSON replacement still lets both invoke
    ``vc-frame triage-run``.  The outer run lock makes the whole side effect one
    transaction; the existing mutation helper is re-entrant for nested receipt
    writes in the same process.
    """

    @wraps(function)
    def wrapped(
        meta_path: str | os.PathLike[str],
        env: Mapping[str, str] | None = None,
        runner: Callable[..., Any] | None = None,
    ) -> TriageOutcome:
        """Acquire the run lock, then delegate to the wrapped triage function."""
        effective_env = os.environ if env is None else env
        meta = Path(meta_path)
        try:
            payload = read_run_meta(meta)
        except Exception:  # noqa: BLE001 - preserve the function's no_meta receipt
            return function(meta_path, env, runner)
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            return function(meta_path, env, runner)
        try:
            control_plane = _control_plane_root_for(meta, effective_env)
            mutation_root = _meta_mutation_root_for(
                meta,
                control_plane=control_plane,
                env=effective_env,
            )
            with run_mutation_module.run_mutation_locks(
                mutation_root,
                run_id=run_id,
            ):
                read_run_meta(meta, expected_run_id=run_id)
                return function(meta_path, env, runner)
        except Exception as exc:  # noqa: BLE001 - triage stays fail-open
            return TriageOutcome(
                OUTCOME_ERROR,
                reason=f"triage_lock_unavailable: {type(exc).__name__}: {exc}",
            )

    return wrapped


@_serialized_triage_call
def triage_finished_run(
    meta_path: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
    runner: Callable[..., Any] | None = None,
) -> TriageOutcome:
    """Transfer a finished run's tab into its bucket, and record what happened.

    Never raises. Invocation failures preserve the origin. A transfer that
    succeeds but cannot prove or link its durable v4 receipt is recorded as an
    error so no later GC treats the move as authoritative.
    """
    env = os.environ if env is None else env
    runner = _default_runner if runner is None else runner

    meta = Path(meta_path)
    try:
        payload = read_run_meta(meta)
    except Exception as exc:  # noqa: BLE001
        # No meta means no receipt to write to either; report and stop.
        return TriageOutcome(OUTCOME_SKIPPED, reason=f"no_meta: {exc}")

    run_id = str(payload.get("run_id") or "").strip()
    completed_receipt = _receipt_outcome(payload)
    historical_receipt = _receipt_outcome(
        payload,
        require_current_settlement=False,
    )
    control_plane = _control_plane_root_for(meta, env)
    try:
        mutation_root = _meta_mutation_root_for(
            meta,
            control_plane=control_plane,
            env=env,
        )
    except OSError as exc:
        return TriageOutcome(
            OUTCOME_ERROR,
            reason=f"meta_owner_unavailable: {exc}",
        )
    if control_plane is not None and not _runtime_meta_identity_is_exact(
        meta,
        control_plane,
        run_id,
    ):
        return TriageOutcome(
            OUTCOME_ERROR,
            reason="runtime_meta_identity_mismatch",
        )

    # The dispatcher may have died after vc-frame committed the v4 transfer but
    # before the runtime replaced its pending intent with the final projection.
    # Durable transfer files are the authority in that kill window. Adopt them
    # under the same run lock instead of spawning a second viewer or touching a
    # now-closed origin tab. A valid proof also repairs an older completed
    # receipt whose projection was never linked.
    if control_plane is not None:
        proof_checks = 2 if payload.get("triage_pending") is True else 1
        for proof_check in range(proof_checks):
            try:
                recovered_proof = load_vc_frame_transfer_proof(control_plane, payload)
            except TransferProofError:
                recovered_proof = None
            if recovered_proof is not None:
                recovered = _proof_recovery_outcome(payload) or completed_receipt
                current_settlement = _settlement_identity(payload)
                if recovered is None and current_settlement is not None:
                    canonical_verdict = (
                        VERDICT_FAILED
                        if current_settlement[1] == "invalid"
                        else current_settlement[1]
                    )
                    recovered = TriageOutcome(
                        canonical_verdict,
                        reason=(
                            f"canonical_settlement_revision_{current_settlement[0]}"
                        ),
                        bucket=_BUCKET_FOR_VERDICT[canonical_verdict],
                        verdict=canonical_verdict,
                        verdict_reason=(
                            f"canonical_settlement_revision_{current_settlement[0]}"
                        ),
                    )
                elif recovered is None and historical_receipt is not None:
                    recovered = historical_receipt
                if (
                    recovered is None
                    or recovered_proof.bucket_session != recovered.bucket
                ):
                    failure = TriageOutcome(
                        OUTCOME_ERROR,
                        reason="transfer_receipt_intent_invalid",
                    )
                    _record_receipt(
                        meta,
                        failure,
                        control_plane_root=mutation_root,
                        run_id=run_id,
                    )
                    return failure
                if _record_receipt(
                    meta,
                    recovered,
                    control_plane_root=mutation_root,
                    run_id=run_id,
                    proof=recovered_proof,
                ):
                    return recovered
                failure = TriageOutcome(
                    OUTCOME_ERROR,
                    reason="transfer_projection_persist_failed",
                    bucket=recovered.bucket,
                    verdict=recovered.verdict,
                    verdict_reason=recovered.verdict_reason,
                    verdict_degraded=recovered.verdict_degraded,
                )
                _record_receipt(
                    meta,
                    failure,
                    control_plane_root=mutation_root,
                    run_id=run_id,
                )
                return failure

            if proof_check == 0 and proof_checks == 2:
                try:
                    transfer_running = _vc_frame_transfer_lock_is_held(
                        control_plane, payload
                    )
                except TransferProofError as error:
                    failure = TriageOutcome(
                        OUTCOME_ERROR,
                        reason=f"transfer_lock_unavailable: {error}",
                    )
                    _record_retryable_triage_error(
                        meta,
                        failure,
                        control_plane_root=mutation_root,
                        run_id=run_id,
                    )
                    return failure
                if transfer_running:
                    failure = TriageOutcome(
                        OUTCOME_ERROR,
                        reason="transfer_in_progress",
                    )
                    _record_retryable_triage_error(
                        meta,
                        failure,
                        control_plane_root=mutation_root,
                        run_id=run_id,
                    )
                    return failure
                started_at_ns = payload.get("triage_attempt_started_at_ns")
                now_ns = time.time_ns()
                if type(started_at_ns) is int and (
                    started_at_ns > now_ns
                    or now_ns - started_at_ns < _TRANSFER_CHILD_START_GRACE_NS
                ):
                    failure = TriageOutcome(
                        OUTCOME_ERROR,
                        reason="transfer_child_start_grace",
                    )
                    _record_retryable_triage_error(
                        meta,
                        failure,
                        control_plane_root=mutation_root,
                        run_id=run_id,
                    )
                    return failure
                if payload.get("triage_lock_handoff") != _TRANSFER_LOCK_HANDOFF:
                    failure = TriageOutcome(
                        OUTCOME_ERROR,
                        reason="legacy_pending_without_lock_handoff",
                    )
                    _record_retryable_triage_error(
                        meta,
                        failure,
                        control_plane_root=mutation_root,
                        run_id=run_id,
                    )
                    return failure

    if completed_receipt is not None and control_plane is None:
        return completed_receipt

    plan = plan_triage(payload, env)
    if not plan.should_run:
        outcome = TriageOutcome(OUTCOME_SKIPPED, reason=plan.skip_reason)
        _record_receipt(
            meta,
            outcome,
            control_plane_root=mutation_root,
            run_id=run_id,
        )
        return outcome

    # Resolve the binary before writing anything: a stale or absent vc-frame
    # means no transfer will be attempted at all, and a pending intent for a
    # transfer that never starts would be a receipt describing fiction.
    binary = _resolve_binary(env)
    if not binary:
        outcome = TriageOutcome(OUTCOME_SKIPPED, reason="no_binary")
        _record_receipt(
            meta,
            outcome,
            control_plane_root=mutation_root,
            run_id=run_id,
        )
        return outcome
    # Commit the desired intent before even probing vc-frame. The probe is a
    # real external call too; if this write fails, no vc-frame process may run.
    intent = TriageOutcome(
        plan.verdict,
        reason=plan.verdict_reason,
        bucket=plan.bucket,
        pending=True,
        verdict=plan.verdict,
        verdict_reason=plan.verdict_reason,
    )
    barrier_error = _persist_intent_barrier(
        meta,
        intent,
        control_plane_root=mutation_root,
        run_id=run_id,
    )
    if barrier_error is not None:
        return barrier_error

    probe = _probe_triage_run(binary, runner)
    if not probe.supported:
        outcome = TriageOutcome(OUTCOME_SKIPPED, reason="unsupported_binary")
        _record_receipt(
            meta,
            outcome,
            control_plane_root=mutation_root,
            run_id=run_id,
        )
        return outcome
    if plan.settlement_revision > 0 and (
        not probe.bucket or not probe.settlement_revision
    ):
        outcome = TriageOutcome(
            OUTCOME_ERROR,
            reason="unsupported_settlement_contract",
            bucket=plan.bucket,
            verdict=plan.verdict,
            verdict_reason=plan.verdict_reason,
        )
        _record_retryable_triage_error(
            meta,
            outcome,
            control_plane_root=mutation_root,
            run_id=run_id,
        )
        return outcome
    if control_plane is not None and not probe.inherited_lock:
        outcome = TriageOutcome(
            OUTCOME_ERROR,
            reason="unsupported_transfer_lock_handoff",
        )
        _record_retryable_triage_error(
            meta,
            outcome,
            control_plane_root=mutation_root,
            run_id=run_id,
        )
        return outcome

    # Where the run will actually land. With `--bucket` that is the classifier's
    # verdict; without it vc-frame decides by exit code alone, so the receipt
    # must say the exit-code answer — and say that it was degraded to it.
    if probe.bucket:
        destination, bucket = plan.verdict, plan.bucket
        degraded = ""
    else:
        destination = outcome_for_exit_code(plan.exit_code)
        bucket = bucket_for_exit_code(plan.exit_code)
        degraded = "exit_code_only"

    # A successful transfer closes the origin tab — the tab this process is
    # running in. Our own success is therefore likely to kill us before we can
    # write the receipt. So record the intent first, marked pending, and correct
    # it only if we live long enough to learn better. A run that vanishes mid-
    # transfer then still says where it was headed instead of saying nothing.
    actual_intent = TriageOutcome(
        destination,
        reason=plan.verdict_reason,
        bucket=bucket,
        pending=True,
        verdict=plan.verdict,
        verdict_reason=plan.verdict_reason,
        verdict_degraded=degraded,
    )
    if actual_intent != intent:
        barrier_error = _persist_intent_barrier(
            meta,
            actual_intent,
            control_plane_root=mutation_root,
            run_id=run_id,
        )
        if barrier_error is not None:
            return barrier_error

    if control_plane is None:
        outcome = _run_triage(
            plan,
            binary,
            probe,
            runner,
            destination,
            bucket,
            degraded,
        )
    else:
        try:
            with _hold_vc_frame_transfer_lock(control_plane, payload) as lock_fd:
                outcome = _run_triage(
                    plan,
                    binary,
                    probe,
                    runner,
                    destination,
                    bucket,
                    degraded,
                    inherited_lock_fd=lock_fd,
                    control_plane=control_plane,
                )
        except _TransferLockBusy:
            outcome = TriageOutcome(
                OUTCOME_ERROR,
                reason="transfer_in_progress",
                bucket=bucket,
                verdict=plan.verdict,
                verdict_reason=plan.verdict_reason,
                verdict_degraded=degraded,
            )
        except TransferProofError as error:
            outcome = TriageOutcome(
                OUTCOME_ERROR,
                reason=f"transfer_lock_unavailable: {error}",
                bucket=bucket,
                verdict=plan.verdict,
                verdict_reason=plan.verdict_reason,
                verdict_degraded=degraded,
            )
    proof: DurableTransferProof | None = None
    if control_plane is not None:
        try:
            proof = load_vc_frame_transfer_proof(control_plane, payload)
            if proof.bucket_session != bucket:
                raise TransferProofError(
                    "transfer receipt bucket does not match the triage verdict"
                )
        except TransferProofError as error:
            proof = None
            if outcome.outcome != OUTCOME_ERROR:
                outcome = TriageOutcome(
                    OUTCOME_ERROR,
                    reason=f"transfer_proof_invalid: {error}",
                    bucket=bucket,
                    verdict=plan.verdict,
                    verdict_reason=plan.verdict_reason,
                    verdict_degraded=degraded,
                )
        else:
            # A sibling invocation may have returned lock contention while the
            # original vc-frame child committed the exact proof. The proof wins.
            outcome = replace(actual_intent, pending=False)

    if control_plane is not None and outcome.outcome == OUTCOME_ERROR and proof is None:
        _record_retryable_triage_error(
            meta,
            outcome,
            control_plane_root=mutation_root,
            run_id=run_id,
        )
        return outcome

    written = _record_receipt(
        meta,
        outcome,
        control_plane_root=mutation_root,
        run_id=run_id,
        proof=proof,
    )
    if proof is not None and control_plane is not None:
        canonical_root = control_plane.resolve(strict=True)
        canonical_meta = _canonical_runtime_meta(canonical_root, proof.run_id)
        if canonical_meta != meta.resolve(strict=True):
            written = (
                _record_receipt(
                    canonical_meta,
                    outcome,
                    control_plane_root=canonical_root,
                    run_id=proof.run_id,
                    proof=proof,
                )
                and written
            )
    if proof is not None and not written:
        outcome = TriageOutcome(
            OUTCOME_ERROR,
            reason="transfer_projection_persist_failed",
            bucket=bucket,
            verdict=plan.verdict,
            verdict_reason=plan.verdict_reason,
            verdict_degraded=degraded,
        )
        _record_receipt(
            meta,
            outcome,
            control_plane_root=mutation_root,
            run_id=run_id,
        )
    return outcome


def _run_triage(
    plan: TriagePlan,
    binary: str,
    probe: _Probe,
    runner: Callable[..., Any],
    destination: str,
    bucket: str,
    degraded: str,
    *,
    inherited_lock_fd: int | None = None,
    control_plane: Path | None = None,
) -> TriageOutcome:
    """Invoke ``vc-frame triage-run`` for one already-decided destination.

    Assumes the caller has already persisted the pending intent; this only runs
    the external process and turns its result into a :class:`TriageOutcome`.
    """

    def _error(reason: str) -> TriageOutcome:
        """Build an ``error`` outcome carrying the classifier's verdict as context."""
        return TriageOutcome(
            OUTCOME_ERROR,
            reason=reason,
            bucket=bucket,
            verdict=plan.verdict,
            verdict_reason=plan.verdict_reason,
            verdict_degraded=degraded,
        )

    try:
        argv = plan.argv(binary, with_bucket=probe.bucket)
        if runner is _default_runner:
            proc = runner(
                argv,
                inherited_lock_fd=inherited_lock_fd,
                control_plane=control_plane,
            )
        else:
            proc = runner(argv)
    except Exception as exc:  # noqa: BLE001
        return _error(f"invoke_error: {type(exc).__name__}: {exc}")

    returncode = getattr(proc, "returncode", 1)
    if returncode != 0:
        stderr = str(getattr(proc, "stderr", "") or "").strip()
        return _error(
            f"exit {returncode}: {stderr[:500]}" if stderr else f"exit {returncode}"
        )

    return TriageOutcome(
        destination,
        reason=plan.verdict_reason,
        bucket=bucket,
        verdict=plan.verdict,
        verdict_reason=plan.verdict_reason,
        verdict_degraded=degraded,
    )


def _record_retryable_triage_error(
    meta: Path,
    outcome: TriageOutcome,
    *,
    control_plane_root: Path,
    run_id: str,
) -> bool:
    """Keep the pre-transfer intent pending while recording the last failure."""

    def _merge(current: dict[str, Any]) -> dict[str, Any] | None:
        """Attach the last error to a still-pending intent; abort if it moved on."""
        if (
            current.get("run_id") != run_id
            or current.get("triage_pending") is not True
            or current.get("triage") not in _BUCKET_FOR_VERDICT
        ):
            return None
        current["triage_last_error"] = {
            "reason": outcome.reason,
            "recorded_at_ns": time.time_ns(),
        }
        return current

    try:
        return mutate_run_meta(
            control_plane_root,
            meta_path=meta,
            run_id=run_id,
            mutator=_merge,
        )
    except (OSError, RunMetaMutationError, TypeError, ValueError):
        return False


def _record_receipt(
    meta: Path,
    outcome: TriageOutcome,
    *,
    control_plane_root: Path,
    run_id: str,
    proof: DurableTransferProof | None = None,
) -> bool:
    """Merge the receipt through the shared per-run mutation transaction."""
    receipt_updates = outcome.receipt()

    transfer_keys = {"triage_transfer_receipt", "triage_transfer"}

    def _merge(current: dict[str, Any]) -> dict[str, Any] | None:
        """Fold the outcome, settlement, and proof-linkage fields into ``current``.

        Returns ``None`` (abort the mutation) if the current settlement is
        unreadable or disagrees with the proof/outcome being recorded.
        """
        updates = dict(receipt_updates)
        settlement = _settlement_identity(current)
        has_settlement = _has_settlement_material(current)
        if has_settlement and settlement is None:
            return None
        if settlement is not None:
            revision, settlement_verdict, settlement_tui = settlement
            updates.update(
                {
                    "triage_settlement_revision": revision,
                    "triage_settlement_verdict": settlement_verdict,
                    "triage_settlement_tui": settlement_tui,
                }
            )
        else:
            for key in (
                "triage_settlement_revision",
                "triage_settlement_verdict",
                "triage_settlement_tui",
            ):
                current.pop(key, None)
        if proof is not None and (
            current.get("run_id") != proof.run_id
            or type(current.get("exit_code")) is not int
            or current.get("exit_code") != proof.exit_code
            or current.get("origin_session") != proof.origin_session
            or current.get("origin_tab") != proof.origin_tab
        ):
            return None
        if proof is not None:
            proof_settlement = (
                proof.settlement_revision,
                proof.settlement_verdict,
                proof.settlement_tui,
            )
            if (settlement is not None and proof_settlement != settlement) or (
                settlement is None and proof_settlement != (0, "", "")
            ):
                return None
            if settlement is not None:
                canonical_verdict = (
                    VERDICT_FAILED if settlement[1] == "invalid" else settlement[1]
                )
                expected_bucket = _BUCKET_FOR_VERDICT[canonical_verdict]
                if (
                    proof.bucket_session != expected_bucket
                    or outcome.outcome != canonical_verdict
                    or outcome.bucket != expected_bucket
                    or outcome.verdict != canonical_verdict
                    or outcome.verdict_degraded != ""
                ):
                    return None
            updates["triage_transfer_receipt"] = str(proof.receipt_path)
            updates["triage_transfer"] = proof.projection()
        current.update(updates)
        if outcome.pending:
            current["triage_lock_handoff"] = _TRANSFER_LOCK_HANDOFF
            started_at_ns = current.get("triage_attempt_started_at_ns")
            if type(started_at_ns) is not int or started_at_ns <= 0:
                current["triage_attempt_started_at_ns"] = time.time_ns()
        else:
            current.pop("triage_attempt_started_at_ns", None)
            current.pop("triage_last_error", None)
        if proof is None and (
            outcome.pending
            or outcome.outcome == OUTCOME_ERROR
            or outcome.outcome in _BUCKET_FOR_VERDICT
        ):
            for key in transfer_keys:
                current.pop(key, None)
        return current

    try:
        return mutate_run_meta(
            control_plane_root,
            meta_path=meta,
            run_id=run_id,
            mutator=_merge,
        )
    except (OSError, RunMetaMutationError, TypeError, ValueError):
        return False


def _persist_intent_barrier(
    meta: Path,
    intent: TriageOutcome,
    *,
    control_plane_root: Path,
    run_id: str,
) -> TriageOutcome | None:
    """Commit pending intent or durably record why no external call was allowed."""

    if _record_receipt(
        meta,
        intent,
        control_plane_root=control_plane_root,
        run_id=run_id,
    ):
        return None
    failure = TriageOutcome(
        OUTCOME_ERROR,
        reason="intent_persist_failed",
        bucket=intent.bucket,
        verdict=intent.verdict,
        verdict_reason=intent.verdict_reason,
        verdict_degraded=intent.verdict_degraded,
    )
    _record_receipt(
        meta,
        failure,
        control_plane_root=control_plane_root,
        run_id=run_id,
    )
    return failure


def record_triage_gc_result(
    control_plane: Path,
    result: TriageGcResult,
) -> bool:
    """Persist one explicit GC attempt without changing terminal triage fields."""
    if (
        result.status not in {"pending", "closed", "error"}
        or result.target_role not in {"origin", "viewer"}
        or result.reason not in _TRIAGE_GC_REASONS
        or result.settlement_revision <= 0
        or not _is_hex(result.receipt_sha256, 64)
        or not result.recorded_at
        or type(result.returncode) not in {int, type(None)}
        or not result.target.session
        or not result.target.name
        or result.target.tab_id < 0
        or not result.target.session_incarnation
        or not _is_hex(result.target.tab_instance_id, 32)
    ):
        return False
    try:
        run_id = _safe_run_id(result.run_id)
        root = _canonical_root(control_plane)
    except TransferProofError:
        return False
    runtime_meta = _canonical_runtime_meta(root, run_id)
    projection = result.projection()

    def _merge(current: dict[str, Any]) -> dict[str, Any] | None:
        """Record the GC attempt only if it still targets the bound transfer identity."""
        transfer = current.get("triage_transfer")
        if (
            current.get("run_id") != run_id
            or current.get("triage_pending") is not False
            or current.get("triage") not in _BUCKET_FOR_VERDICT
            or current.get("settlement_revision") != result.settlement_revision
            or not isinstance(transfer, Mapping)
            or transfer.get("receipt_sha256") != result.receipt_sha256
        ):
            return None
        if result.target_role == "viewer":
            viewer = transfer.get("viewer")
            bound_identity = (
                viewer.get("identity") if isinstance(viewer, Mapping) else None
            )
        else:
            origin = transfer.get("origin")
            bound_identity = (
                origin.get("identity") if isinstance(origin, Mapping) else None
            )
        if bound_identity != result.target.projection():
            return None

        current["triage_gc"] = projection
        if result.status == "error":
            current["triage_gc_error"] = {
                "schema": TRIAGE_GC_SCHEMA,
                "code": result.reason,
                "detail": result.detail,
                "returncode": result.returncode,
                "recorded_at": result.recorded_at,
            }
        else:
            current.pop("triage_gc_error", None)
        return current

    try:
        return mutate_run_meta(
            root,
            meta_path=runtime_meta,
            run_id=run_id,
            mutator=_merge,
        )
    except (OSError, RunMetaMutationError, TypeError, ValueError):
        return False


def _needs_triage_reconciliation(
    payload: Mapping[str, Any],
    *,
    control_plane: Path,
) -> bool:
    """Whether one terminal runtime meta still lacks a durable triage answer."""

    if type(payload.get("exit_code")) is not int:
        return False
    if _receipt_outcome(payload) is not None:
        return not _proof_projection_is_exact(
            payload,
            control_plane=control_plane,
        )
    return not (
        payload.get("triage") == OUTCOME_SKIPPED
        and payload.get("triage_pending") is False
        and str(payload.get("triage_reason") or "") in _PERMANENT_SKIP_REASONS
    )


def _rotating_sweep_candidates(
    root: Path,
    runtime_runs: Path,
    *,
    scan_limit: int,
    attempt_limit: int,
) -> tuple[tuple[Path, ...], bool]:
    """Reserve a durable fair page instead of restarting at iterdir's prefix."""

    try:
        candidates = sorted(runtime_runs.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise TransferProofError(
            f"runtime run directory is unavailable: {runtime_runs}"
        ) from error
    if not candidates:
        return (), False

    # A page cannot exceed the attempt budget.  That guarantees every eligible
    # entry in the reserved page can be attempted before the cursor advances;
    # an always-failing first run therefore cannot starve later stable names.
    page_size = min(scan_limit, attempt_limit)
    selected: list[Path] = []
    cursor_path = root / _TRIAGE_SWEEP_CURSOR_FILE

    def _rotate(current: dict[str, Any]) -> dict[str, Any]:
        """Advance the durable cursor past the reserved page and record it."""
        cursor = current.get("cursor")
        if not isinstance(cursor, str):
            cursor = ""
        start = next(
            (
                index
                for index, candidate in enumerate(candidates)
                if candidate.name > cursor
            ),
            0,
        )
        ordered = candidates[start:] + candidates[:start]
        selected.extend(ordered[:page_size])
        return {
            "run_id": _TRIAGE_SWEEP_CURSOR_RUN_ID,
            "cursor": selected[-1].name,
            "updated_at_ns": time.time_ns(),
        }

    try:
        rotated = mutate_run_meta(
            root,
            meta_path=cursor_path,
            run_id=_TRIAGE_SWEEP_CURSOR_RUN_ID,
            mutator=_rotate,
            create=True,
        )
    except (OSError, RunMetaMutationError, TypeError, ValueError) as error:
        raise TransferProofError("triage sweep cursor is unavailable") from error
    if not rotated or not selected:
        raise TransferProofError("triage sweep cursor did not reserve a page")
    return tuple(selected), len(candidates) > len(selected)


def reconcile_untriaged_runs(
    control_plane: Path,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., Any] | None = None,
    *,
    scan_limit: int = 1024,
    attempt_limit: int = 128,
) -> TriageSweepReport:
    """Recover bounded terminal runs independently of their dispatchers.

    Only canonical direct children of ``runtime_runs`` participate. Every
    individual transfer remains fail-open and serialized by its run lock; a
    corrupt record is reported without blocking the rest of the sweep.
    """

    if scan_limit <= 0 or attempt_limit <= 0:
        raise ValueError("triage reconciliation limits must be positive")
    root = _canonical_root(control_plane)
    runtime_runs = root / "runtime_runs"
    try:
        if (
            runtime_runs.is_symlink()
            or runtime_runs.resolve(strict=True) != runtime_runs
            or not runtime_runs.is_dir()
        ):
            raise TransferProofError(
                f"runtime run directory is not canonical: {runtime_runs}"
            )
    except OSError as error:
        raise TransferProofError(
            f"runtime run directory is unavailable: {runtime_runs}"
        ) from error
    candidates, truncated = _rotating_sweep_candidates(
        root,
        runtime_runs,
        scan_limit=scan_limit,
        attempt_limit=attempt_limit,
    )

    effective_env = dict(os.environ if env is None else env)
    effective_env["VIBECRAFTED_CONTROL_PLANE"] = str(root)
    items: list[TriageSweepItem] = []
    errors: list[TriageSweepItem] = []
    scanned = 0
    attempted = 0

    for run_dir in candidates:
        scanned += 1
        if run_dir.is_symlink() or not run_dir.is_dir():
            continue
        try:
            canonical_run_dir = run_dir.resolve(strict=True)
        except OSError:
            continue
        if canonical_run_dir != run_dir or canonical_run_dir.parent != runtime_runs:
            continue
        meta = run_dir / "meta.json"
        if meta.is_symlink() or not meta.is_file():
            continue
        try:
            _safe_run_id(run_dir.name)
            payload = read_run_meta(meta, expected_run_id=run_dir.name)
        except Exception as exc:  # noqa: BLE001 - one corrupt run cannot stop sweep
            item = TriageSweepItem(
                run_id=run_dir.name,
                meta_path=str(meta),
                outcome=OUTCOME_ERROR,
                reason=f"meta_unreadable: {type(exc).__name__}: {exc}",
            )
            items.append(item)
            errors.append(item)
            continue
        if not _needs_triage_reconciliation(payload, control_plane=root):
            continue
        if attempted >= attempt_limit:
            truncated = True
            break
        attempted += 1
        outcome = triage_finished_run(meta, effective_env, runner)
        item = TriageSweepItem(
            run_id=str(payload.get("run_id") or run_dir.name),
            meta_path=str(meta),
            outcome=outcome.outcome,
            reason=outcome.reason,
            bucket=outcome.bucket,
        )
        items.append(item)
        if outcome.outcome == OUTCOME_ERROR:
            errors.append(item)

    return TriageSweepReport(
        scanned=scanned,
        attempted=attempted,
        items=tuple(items),
        errors=tuple(errors),
        truncated=truncated,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: triage one run's meta.json, or sweep a control plane.

    Always returns 0 — triage failures are recorded in the receipt, never
    surfaced as a nonzero process exit.
    """
    parser = argparse.ArgumentParser(
        prog="vibecrafted_core.run_triage",
        description="Transfer a finished run's tab into its vc-frame status bucket.",
    )
    parser.add_argument("meta", nargs="?", help="Path to the run's launcher meta.json")
    parser.add_argument(
        "--sweep-control-plane",
        type=Path,
        help="Reconcile terminal runtime_runs under this control-plane root",
    )
    parser.add_argument("--scan-limit", type=int, default=1024)
    parser.add_argument("--attempt-limit", type=int, default=128)
    args = parser.parse_args(argv)

    if bool(args.meta) == bool(args.sweep_control_plane):
        parser.error("provide exactly one meta path or --sweep-control-plane")
    if args.sweep_control_plane is not None:
        try:
            report = reconcile_untriaged_runs(
                args.sweep_control_plane,
                scan_limit=args.scan_limit,
                attempt_limit=args.attempt_limit,
            )
        except (OSError, TransferProofError, ValueError) as exc:
            print(f"triage sweep: error ({type(exc).__name__}: {exc})")
            return 0
        print(
            "triage sweep: "
            f"scanned={report.scanned} attempted={report.attempted} "
            f"errors={len(report.errors)} truncated={str(report.truncated).lower()}"
        )
        return 0

    assert args.meta is not None
    outcome = triage_finished_run(args.meta)
    line = f"triage: {outcome.outcome}"
    if outcome.bucket and outcome.outcome in _BUCKET_FOR_VERDICT:
        line += f" → {outcome.bucket}"
    if outcome.reason:
        line += f" ({outcome.reason})"
    if outcome.verdict_degraded:
        line += f" [degraded: {outcome.verdict_degraded}]"
    print(line)
    # Always 0: triage never fails a run.
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
