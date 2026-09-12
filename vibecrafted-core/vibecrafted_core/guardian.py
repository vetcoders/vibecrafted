"""Event-driven f/x/n guardian for terminal Vibecrafted runs.

The guardian has one trigger substrate: the vibecrafted-server
``GET /api/control/events`` SSE stream.  For each new typed settlement it
durably queues one exact run-triage projection before advancing the stream
cursor, then performs one exact run-projection read before deciding recovery.
One bounded local startup sweep repairs terminal receipts orphaned by a dead
dispatcher; periodic work revisits only that durable outbox.  The guardian
never tails ``events.jsonl`` or makes Zellij/vc-frame the run owner.

On the first attachment, historical frames are checkpointed without side
effects until the server's typed ``stream.caught-up`` receipt proves that the
finite baseline drained. Generation-aware ``v2:<epoch>:<generation>:<offset>``
cursors survive rotation; ``stream.gap`` revokes resume authority and requires
a fresh baseline. Numeric compatibility streams remain notification-only.
Subsequent ``settlement.changed`` revisions move through a durable
``pending -> completed`` outbox whose authority is bound to the v2 cursor.

Resume is deliberately an injected boundary.  The CLI wires the guarded
server-projection -> ``vc-guard`` -> tracked ``native_resume_run`` adapter;
library callers without an adapter remain fail-closed.  Only ``n`` may request
resume, and only when both the event and fresh projection carry an explicit
``trust`` settlement source.  ``x`` and ``f`` are always notification-only and
can never reach the resume callback.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import heapq
import hmac
import json
import logging
import math
import os
import queue
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .delivery.store import atomic_write_json
from .runtime_paths import vibecrafted_home
from .server_config import load_server_config
from .settlement import (
    SETTLEMENT_EVENT_KIND,
    SETTLEMENT_EVENT_SCHEMA,
    SETTLEMENT_EVENT_SCHEMA_V2,
    TUI_FAILED,
    TUI_FINALIZED,
    TUI_NEEDS_ATTENTION,
    SettlementEventV2,
    TrustReceiptV1,
)
from .settlement_board import SettlementBoardPublisher

LOGGER = logging.getLogger(__name__)

GUARDIAN_STATE_SCHEMA = "vibecrafted.guardian-state.v2"
GUARDIAN_STATE_SCHEMA_V1 = "vibecrafted.guardian-state.v1"
GUARDIAN_DEAD_LETTER_SCHEMA = "vibecrafted.guardian-dead-letters.v2"
GUARDIAN_READY_SCHEMA = "vibecrafted.guardian-ready.v1"
TERMINAL_TRIAGE_OUTBOX_SCHEMA = "vibecrafted.terminal-triage-outbox.v2"
TERMINAL_TRIAGE_OUTBOX_SCHEMA_V1 = "vibecrafted.terminal-triage-outbox.v1"
TERMINAL_TRIAGE_DEAD_LETTER_SCHEMA = "vibecrafted.terminal-triage-dead-letter.v1"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 30.0
DEFAULT_RECOVERY_TIMEOUT_SECONDS = 5.0
DEFAULT_BACKOFF_INITIAL_SECONDS = 1.0
DEFAULT_BACKOFF_MAX_SECONDS = 30.0
DEFAULT_REPLAY_HEARTBEATS = 4
DEFAULT_PENDING_PASS_LIMIT = 64
TERMINAL_TRIAGE_QUEUE_CAPACITY = 1024
TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT = 4096
TERMINAL_TRIAGE_OUTBOX_ATTEMPT_LIMIT = 64
TERMINAL_TRIAGE_QUARANTINE_CAPACITY = 256
TERMINAL_TRIAGE_RETRY_SECONDS = 15.0
TERMINAL_TRIAGE_RETRY_INITIAL_SECONDS = 30.0
TERMINAL_TRIAGE_RETRY_MAX_SECONDS = 30.0 * 60.0
TERMINAL_TRIAGE_MAX_ATTEMPTS = 8
MAX_PENDING_RECORDS = 1024
MAX_PENDING_ATTEMPTS = 8
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_DEAD_LETTER_BYTES = 1024 * 1024
MAX_DEAD_LETTER_ENTRIES = 128
MAX_DEAD_LETTER_DATA_BYTES = 16 * 1024
MAX_SSE_LINE_BYTES = 64 * 1024
MAX_SSE_FRAME_BYTES = 1024 * 1024
MAX_CURSOR_BYTES = 1024
MAX_RUN_ID_BYTES = 1024
MAX_STATE_TEXT_BYTES = 4096
PENDING_RETRY_INITIAL_SECONDS = 1.0
PENDING_RETRY_MAX_SECONDS = 300.0

_TUI_SEVERITY = {
    TUI_FINALIZED: "info",
    TUI_NEEDS_ATTENTION: "warning",
    TUI_FAILED: "critical",
}
_TUI_LABEL = {
    TUI_FINALIZED: "finalized",
    TUI_NEEDS_ATTENTION: "needs attention",
    TUI_FAILED: "failed",
}
_VERDICT_TUI = {
    "finalized": TUI_FINALIZED,
    "needs_attention": TUI_NEEDS_ATTENTION,
    "failed": TUI_FAILED,
    "invalid": TUI_FAILED,
}

SettlementKey = tuple[str, int]


class GuardianAlreadyRunning(RuntimeError):
    """Another guardian process owns the single-instance lock."""


class GuardianProtocolError(RuntimeError):
    """The configured endpoint did not return the guardian's SSE contract."""


class GuardianStateError(RuntimeError):
    """The durable guardian state failed a strict integrity or semantic check."""


class GuardianStateLimitError(GuardianStateError):
    """A bounded guardian persistence surface reached its hard limit."""


class GuardianLockSecurityError(GuardianStateError):
    """The single-instance lock path failed ownership or inode validation."""


CursorToken = int | str


def _truncate_utf8_bytes(value: str, maximum: int = MAX_STATE_TEXT_BYTES) -> str:
    """Return the longest valid UTF-8 prefix whose encoding fits `maximum`."""
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value
    return encoded[:maximum].decode("utf-8", errors="ignore")


def _bounded_text(
    value: object,
    *,
    field_name: str,
    allow_empty: bool = True,
    maximum: int = MAX_STATE_TEXT_BYTES,
) -> str:
    """Validate `value` is a string within byte length and control-char rules."""
    if not isinstance(value, str):
        raise GuardianStateError(f"{field_name} must be a string")
    if not allow_empty and not value:
        raise GuardianStateError(f"{field_name} must not be empty")
    if len(value.encode("utf-8")) > maximum:
        raise GuardianStateError(f"{field_name} exceeds {maximum} bytes")
    if any(ord(character) < 0x20 and character not in "\t" for character in value):
        raise GuardianStateError(f"{field_name} contains control characters")
    return value


def _validate_cursor(value: object) -> CursorToken:
    """Coerce `value` into a valid CursorToken (non-negative int or opaque str)."""
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str):
        return _bounded_text(
            value,
            field_name="cursor",
            allow_empty=False,
            maximum=MAX_CURSOR_BYTES,
        )
    raise GuardianStateError("cursor must be a non-negative integer or opaque string")


def _parse_event_cursor(raw_cursor: str) -> CursorToken | None:
    """Parse an SSE `id:` cursor as numeric or `v2:<epoch>:<gen>:<offset>`."""
    value = raw_cursor.strip()
    if not value or len(value.encode("utf-8")) > MAX_CURSOR_BYTES:
        return None
    if value.isdigit():
        parsed = int(value)
        return parsed if parsed <= (2**64) - 1 else None
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != "v2":
        return None
    epoch, generation_raw, offset_raw = parts[1:]
    if (
        not epoch
        or not epoch.isascii()
        or not all(character.isalnum() or character in "-_." for character in epoch)
        or not generation_raw.isdigit()
        or not offset_raw.isdigit()
    ):
        return None
    generation = int(generation_raw)
    offset = int(offset_raw)
    if generation > (2**64) - 1 or offset > (2**64) - 1:
        return None
    return value


def _is_v2_cursor(cursor: object) -> bool:
    """True when `cursor` is a well-formed opaque v2 cursor string."""
    return isinstance(cursor, str) and _parse_event_cursor(cursor) == cursor


def _v2_cursor_parts(cursor: CursorToken) -> tuple[str, int, int] | None:
    """Split a v2 cursor into (epoch, generation, offset); None if not v2."""
    if not _is_v2_cursor(cursor):
        return None
    assert isinstance(cursor, str)
    _version, epoch, generation, offset = cursor.split(":")
    return (epoch, int(generation), int(offset))


def _cursor_reaches(current: CursorToken, target: CursorToken) -> bool:
    """True when `current` is at or past `target`, comparing within the same epoch."""
    current_parts = _v2_cursor_parts(current)
    target_parts = _v2_cursor_parts(target)
    if current_parts is not None and target_parts is not None:
        current_epoch, current_generation, current_offset = current_parts
        target_epoch, target_generation, target_offset = target_parts
        return current_epoch == target_epoch and (
            current_generation > target_generation
            or (
                current_generation == target_generation
                and current_offset >= target_offset
            )
        )
    return type(current) is int and type(target) is int and current >= target


def _boundary_is_valid(
    from_cursor: CursorToken,
    to_cursor: CursorToken,
    *,
    reason: str,
) -> bool:
    """Validate a `stream.boundary` control: same cursor on connect, or same
    epoch with an advancing generation on `generation_change`."""
    if reason == "connection_start":
        return from_cursor == to_cursor
    if reason != "generation_change":
        return False
    from_parts = _v2_cursor_parts(from_cursor)
    to_parts = _v2_cursor_parts(to_cursor)
    if from_parts is None or to_parts is None:
        return False
    from_epoch, from_generation, _from_offset = from_parts
    to_epoch, to_generation, _to_offset = to_parts
    return from_epoch == to_epoch and to_generation > from_generation


def _canonical_json(payload: object) -> bytes:
    """Encode `payload` as sorted-key, compact-separator canonical JSON bytes."""
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GuardianStateError(f"state is not canonical JSON: {exc}") from exc
    return encoded


def _strict_json_loads(encoded: bytes) -> object:
    """Parse strict JSON: rejects duplicate keys and non-finite numbers."""

    def object_without_duplicates(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        """json.loads object_pairs_hook: reject any object with a duplicate key."""
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise GuardianStateError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=object_without_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                GuardianStateError(f"non-finite JSON number: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardianStateError(f"state is not valid UTF-8 JSON: {exc}") from exc


def _ensure_private_directory(path: Path) -> None:
    """Create `path` as a 0700 directory owned by this uid, or raise fail-closed."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise GuardianLockSecurityError(
            f"guardian directory cannot be opened securely: {path}"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise GuardianLockSecurityError(
                f"guardian directory is not a real directory: {path}"
            )
        if metadata.st_uid != os.getuid():
            raise GuardianLockSecurityError(
                f"guardian directory is not owned by this uid: {path}"
            )
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise GuardianLockSecurityError(
                f"guardian directory permissions are not private: {path}"
            )
    finally:
        os.close(descriptor)


def _validate_existing_private_file(path: Path) -> None:
    """Raise if `path` exists but is not a private (0600, single-link, owned) file."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise GuardianStateError(f"unsafe guardian file target: {path}")


def _fsync_directory(path: Path) -> None:
    """fsync a directory's own inode so a preceding rename/write is durable."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_private_write(path: Path, encoded: bytes) -> None:
    """Write `encoded` to `path` via a 0600 temp file + atomic rename + fsync."""
    _ensure_private_directory(path.parent)
    _validate_existing_private_file(path)
    temporary = path.parent / (f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while persisting guardian state")
            view = view[written:]
        metadata = os.fstat(descriptor)
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise GuardianStateError(
                f"guardian temporary file permissions are not 0600: {temporary}"
            )
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    else:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_private_file(path: Path, *, maximum: int) -> bytes:
    """Read `path` after verifying it is a private regular file within `maximum` bytes."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise GuardianStateError(f"unsafe guardian state file: {path}")
        if metadata.st_size > maximum:
            raise GuardianStateLimitError(
                f"guardian file exceeds {maximum} bytes: {path}"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if len(encoded) > maximum:
            raise GuardianStateLimitError(
                f"guardian file exceeds {maximum} bytes: {path}"
            )
        return encoded
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class SettlementRevision:
    """Validated terminal settlement carried by one SSE frame."""

    run_id: str
    revision: int
    verdict: str
    tui: str
    reason: str
    source: str
    settled_at: str
    receipt_id: str = ""

    @property
    def key(self) -> SettlementKey:
        """Dedupe/highwater key: (run_id, revision)."""
        return (self.run_id, self.revision)

    @property
    def idempotency_key(self) -> str:
        """Stable key passed through to resume adapters for exactly-once effect."""
        return f"settlement:{self.run_id}:{self.revision}"

    def to_state_payload(self) -> dict[str, object]:
        """Serialize this revision to its canonical durable-state dict shape."""
        return {
            "run_id": self.run_id,
            "settlement_revision": self.revision,
            "verdict": self.verdict,
            "tui": self.tui,
            "reason": self.reason,
            "source": self.source,
            "settled_at": self.settled_at,
            "receipt_id": self.receipt_id,
        }

    @classmethod
    def from_state_payload(
        cls, payload: Mapping[str, object]
    ) -> SettlementRevision | None:
        """Reconstruct a SettlementRevision from persisted state; None if invalid."""
        run_id = payload.get("run_id")
        revision = payload.get("settlement_revision")
        verdict = payload.get("verdict")
        tui = payload.get("tui")
        reason = payload.get("reason")
        source = payload.get("source")
        settled_at = payload.get("settled_at")
        receipt_id = payload.get("receipt_id", "")
        if (
            not isinstance(run_id, str)
            or not run_id
            or len(run_id.encode("utf-8")) > MAX_RUN_ID_BYTES
            or type(revision) is not int
            or revision <= 0
            or not isinstance(verdict, str)
            or not isinstance(tui, str)
            or _VERDICT_TUI.get(verdict) != tui
            or not isinstance(reason, str)
            or not isinstance(source, str)
            or not isinstance(settled_at, str)
            or not isinstance(receipt_id, str)
            or len(reason.encode("utf-8")) > MAX_STATE_TEXT_BYTES
            or len(source.encode("utf-8")) > MAX_STATE_TEXT_BYTES
            or len(settled_at.encode("utf-8")) > MAX_STATE_TEXT_BYTES
            or (
                receipt_id != ""
                and (
                    len(receipt_id) != 64
                    or any(
                        character not in "0123456789abcdef" for character in receipt_id
                    )
                )
            )
        ):
            return None
        return cls(
            run_id=run_id,
            revision=revision,
            verdict=verdict,
            tui=tui,
            reason=reason,
            source=source,
            settled_at=settled_at,
            receipt_id=receipt_id,
        )


@dataclass(frozen=True)
class GuardianNotification:
    """One operator-facing f/x/n notification."""

    event: SettlementRevision
    severity: str
    title: str
    message: str


@dataclass(frozen=True)
class ReconcileDecision:
    """Result of one idempotently keyed reconcile operation."""

    request_resume: bool = False
    reason: str = "resume adapter unavailable"


@dataclass(frozen=True)
class ActionResult:
    """Strict terminal/retryable result returned by an external action."""

    accepted: bool
    retryable: bool
    terminal: bool
    reason: str


@dataclass(frozen=True)
class CompletionRecord:
    """Compact maximum processed revision for one run."""

    revision: int
    outcome: str
    reason: str

    def to_state_payload(self, run_id: str) -> dict[str, object]:
        """Serialize this completion record with its owning `run_id` attached."""
        return {
            "run_id": run_id,
            "settlement_revision": self.revision,
            "outcome": self.outcome,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PendingRecord:
    """Durable outbox state for one settlement revision."""

    event: SettlementRevision
    stream_cursor: CursorToken = 0
    resume_authorized: bool = False
    notification_done: bool = False
    attempts: int = 0
    next_retry: float = 0.0
    last_reason: str = ""
    outcome: str = "pending"

    @property
    def key(self) -> SettlementKey:
        """Dedupe/highwater key of the underlying settlement event."""
        return self.event.key

    def to_state_payload(self) -> dict[str, object]:
        """Serialize this outbox record (event fields plus retry/outcome state)."""
        return {
            **self.event.to_state_payload(),
            "stream_cursor": self.stream_cursor,
            "resume_authorized": self.resume_authorized,
            "notification_done": self.notification_done,
            "attempts": self.attempts,
            "next_retry": self.next_retry,
            "last_reason": self.last_reason,
            "outcome": self.outcome,
        }

    @classmethod
    def from_state_payload(cls, payload: Mapping[str, object]) -> PendingRecord:
        """Reconstruct a PendingRecord from persisted state; raises on any invalid field."""
        expected = {
            "run_id",
            "settlement_revision",
            "verdict",
            "tui",
            "reason",
            "source",
            "settled_at",
            "receipt_id",
            "stream_cursor",
            "resume_authorized",
            "notification_done",
            "attempts",
            "next_retry",
            "last_reason",
            "outcome",
        }
        if set(payload) != expected:
            raise GuardianStateError("pending record fields are not canonical")
        event = SettlementRevision.from_state_payload(payload)
        if event is None:
            raise GuardianStateError("pending settlement is semantically invalid")
        notification_done = payload.get("notification_done")
        resume_authorized = payload.get("resume_authorized")
        attempts = payload.get("attempts")
        next_retry = payload.get("next_retry")
        last_reason = payload.get("last_reason")
        outcome = payload.get("outcome")
        if type(notification_done) is not bool:
            raise GuardianStateError("pending notification_done must be boolean")
        if type(resume_authorized) is not bool:
            raise GuardianStateError("pending resume_authorized must be boolean")
        stream_cursor = _validate_cursor(payload.get("stream_cursor"))
        if resume_authorized and not _is_v2_cursor(stream_cursor):
            raise GuardianStateError(
                "pending resume authority requires an opaque v2 cursor"
            )
        if type(attempts) is not int or attempts < 0 or attempts > 1_000_000:
            raise GuardianStateError("pending attempts is out of range")
        if not isinstance(next_retry, (int, float)) or isinstance(next_retry, bool):
            raise GuardianStateError("pending next_retry is invalid")
        next_retry_value = float(next_retry)
        if not math.isfinite(next_retry_value) or next_retry_value < 0:
            raise GuardianStateError("pending next_retry is invalid")
        parsed_outcome = _bounded_text(
            outcome,
            field_name="pending.outcome",
            allow_empty=False,
        )
        if parsed_outcome not in {"pending", "retryable"}:
            raise GuardianStateError("pending outcome is invalid")
        return cls(
            event=event,
            stream_cursor=stream_cursor,
            resume_authorized=resume_authorized,
            notification_done=notification_done,
            attempts=attempts,
            next_retry=next_retry_value,
            last_reason=_bounded_text(
                last_reason,
                field_name="pending.last_reason",
            ),
            outcome=parsed_outcome,
        )


Notifier = Callable[[GuardianNotification], None]
Reconciler = Callable[[SettlementRevision], ReconcileDecision]
ResumeCallback = Callable[[SettlementRevision, str], object]
BoardPublisher = Callable[[], object]
TriageScheduler = Callable[[str], bool]
UrlOpener = Callable[..., Any]
ReadyCallback = Callable[[], None]
GuardEnforcer = Callable[..., object]
NativeResumer = Callable[..., Mapping[str, object]]
CursorParser = Callable[[str], CursorToken | None]


def _ignore_board_publish() -> None:
    """Default no-op board publisher used when no rail projection is wired."""
    return


def _ignore_triage_schedule(_run_id: str) -> bool:
    """Default no-op triage scheduler that reports success without persisting."""
    return True


@dataclass(frozen=True)
class SSEFrame:
    """One complete SSE data frame with its durable cursor."""

    cursor: CursorToken
    data: str


@dataclass(frozen=True)
class SSEHeartbeat:
    """The vibecrafted-server ``: ping`` keepalive."""


@dataclass(frozen=True)
class SSEControlFrame:
    """Opaque named SSE control frame left for a versioned extension parser."""

    event: str
    raw_cursor: str
    data: str


@dataclass(frozen=True)
class SSEStreamBoundary:
    """Validated connection/generation boundary."""

    cursor: CursorToken
    from_cursor: CursorToken
    reason: str


@dataclass(frozen=True)
class SSEStreamGap:
    """Validated discontinuity requiring a fresh side-effect baseline."""

    cursor: CursorToken
    requested: str
    reason: str


@dataclass(frozen=True)
class SSEStreamCaughtUp:
    """Validated finite-baseline receipt independent of heartbeat traffic."""

    cursor: CursorToken
    high_watermark: CursorToken
    authoritative: bool


SSEItem = (
    SSEFrame
    | SSEHeartbeat
    | SSEControlFrame
    | SSEStreamBoundary
    | SSEStreamGap
    | SSEStreamCaughtUp
)
ControlParser = Callable[[SSEControlFrame], SSEItem | None]


def _control_document(
    frame: SSEControlFrame,
    *,
    schema: str,
    fields: set[str],
) -> dict[str, object]:
    """Parse and strictly shape-check one control frame's JSON body."""
    try:
        document = _strict_json_loads(frame.data.encode("utf-8"))
    except GuardianStateError as exc:
        raise GuardianProtocolError(
            f"{frame.event} control frame is not strict JSON"
        ) from exc
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or document.get("schema") != schema
        or document.get("kind") != frame.event
    ):
        raise GuardianProtocolError(f"{frame.event} control frame violates {schema}")
    return document


def parse_stream_control(frame: SSEControlFrame) -> SSEItem:
    """Parse only the committed v1 stream controls; preserve unknown frames."""

    if frame.event == "stream.boundary":
        document = _control_document(
            frame,
            schema="vibecrafted.stream-boundary.v1",
            fields={"schema", "kind", "from", "to", "reason"},
        )
        raw_from = document.get("from")
        raw_to = document.get("to")
        from_cursor = (
            _parse_event_cursor(raw_from) if isinstance(raw_from, str) else None
        )
        to_cursor = _parse_event_cursor(raw_to) if isinstance(raw_to, str) else None
        reason = document.get("reason")
        if (
            from_cursor is None
            or to_cursor is None
            or frame.raw_cursor != raw_to
            or not isinstance(reason, str)
            or not reason
            or len(reason.encode("utf-8")) > MAX_STATE_TEXT_BYTES
            or not _boundary_is_valid(
                from_cursor,
                to_cursor,
                reason=reason,
            )
        ):
            raise GuardianProtocolError("stream.boundary control fields are invalid")
        return SSEStreamBoundary(
            cursor=to_cursor,
            from_cursor=from_cursor,
            reason=reason,
        )
    if frame.event == "stream.gap":
        document = _control_document(
            frame,
            schema="vibecrafted.stream-gap.v1",
            fields={
                "schema",
                "kind",
                "requested",
                "resumed_at",
                "reason",
                "action",
            },
        )
        raw_resumed_at = document.get("resumed_at")
        resumed_at = (
            _parse_event_cursor(raw_resumed_at)
            if isinstance(raw_resumed_at, str)
            else None
        )
        requested = document.get("requested")
        reason = document.get("reason")
        if (
            resumed_at is None
            or frame.raw_cursor != raw_resumed_at
            or not isinstance(requested, str)
            or not requested
            or len(requested.encode("utf-8")) > MAX_CURSOR_BYTES
            or not isinstance(reason, str)
            or not reason
            or len(reason.encode("utf-8")) > MAX_STATE_TEXT_BYTES
            or document.get("action") != "resnapshot"
        ):
            raise GuardianProtocolError("stream.gap control fields are invalid")
        return SSEStreamGap(
            cursor=resumed_at,
            requested=requested,
            reason=reason,
        )
    if frame.event == "stream.caught-up":
        document = _control_document(
            frame,
            schema="vibecrafted.stream-caught-up.v1",
            fields={"schema", "kind", "cursor", "high_watermark"},
        )
        raw_cursor = document.get("cursor")
        raw_high_watermark = document.get("high_watermark")
        cursor = (
            _parse_event_cursor(raw_cursor) if isinstance(raw_cursor, str) else None
        )
        high_watermark = (
            _parse_event_cursor(raw_high_watermark)
            if isinstance(raw_high_watermark, str)
            else None
        )
        if (
            cursor is None
            or high_watermark is None
            or frame.raw_cursor != raw_cursor
            or not _cursor_reaches(cursor, high_watermark)
            or _is_v2_cursor(cursor) != _is_v2_cursor(high_watermark)
        ):
            raise GuardianProtocolError("stream.caught-up control fields are invalid")
        return SSEStreamCaughtUp(
            cursor=cursor,
            high_watermark=high_watermark,
            authoritative=_is_v2_cursor(cursor),
        )
    return frame


@dataclass
class ConnectionStats:
    """Small observability receipt for one SSE connection."""

    frames: int = 0
    heartbeats: int = 0
    claimed: int = 0
    completed_actions: int = 0
    action_failures: int = 0
    completed_baseline: bool = False

    @property
    def proved_stable(self) -> bool:
        """True once this connection proved useful, resetting reconnect backoff."""
        # One accept + one immediate ping + drop must not pin reconnect delay to
        # the minimum forever. Two quiet heartbeats prove a sustained stream;
        # a completed settlement action proves useful forward progress.
        return self.completed_actions > 0 or self.heartbeats >= 2


@dataclass
class GuardianState:
    """Checksummed v2 cursor, compact highwater, and durable action outbox."""

    path: Path
    cursor: CursorToken = 0
    baseline_complete: bool = False
    highwater: dict[str, CompletionRecord] = field(default_factory=dict)
    pending: dict[SettlementKey, PendingRecord] = field(default_factory=dict)
    degraded: bool = False
    recovered_from_backup: bool = False
    state_generation: int = 0

    def __post_init__(self) -> None:
        """Validate cursor/flags/highwater/pending invariants fail-closed on load."""
        self.cursor = _validate_cursor(self.cursor)
        if type(self.state_generation) is not int or self.state_generation < 0:
            raise GuardianStateError("guardian state generation must be non-negative")
        if type(self.baseline_complete) is not bool:
            raise GuardianStateError("baseline_complete must be boolean")
        if (
            type(self.degraded) is not bool
            or type(self.recovered_from_backup) is not bool
        ):
            raise GuardianStateError("guardian state flags must be boolean")
        if len(self.pending) > MAX_PENDING_RECORDS:
            raise GuardianStateLimitError("guardian pending outbox exceeds capacity")
        for run_id, completion in self.highwater.items():
            if not isinstance(completion, CompletionRecord):
                raise GuardianStateError("highwater value must be a completion record")
            _bounded_text(
                run_id,
                field_name="highwater.run_id",
                allow_empty=False,
                maximum=MAX_RUN_ID_BYTES,
            )
            if type(completion.revision) is not int or completion.revision <= 0:
                raise GuardianStateError("highwater revision must be positive")
            _bounded_text(
                completion.outcome,
                field_name="highwater.outcome",
                allow_empty=False,
            )
            _bounded_text(
                completion.reason,
                field_name="highwater.reason",
            )
        for key, record in self.pending.items():
            if not isinstance(record, PendingRecord):
                raise GuardianStateError("pending value must be a pending record")
            if PendingRecord.from_state_payload(record.to_state_payload()) != record:
                raise GuardianStateError("pending record is not canonical")
            if key != record.key:
                raise GuardianStateError("pending key does not match settlement")
            if record.resume_authorized and (
                not self.baseline_complete
                or not _is_v2_cursor(self.cursor)
                or record.event.source != "trust"
                or not record.event.receipt_id
            ):
                raise GuardianStateError(
                    "pending resume authority requires an authoritative state cursor"
                )
            prior_completion = self.highwater.get(record.event.run_id)
            if (
                prior_completion is not None
                and record.event.revision <= prior_completion.revision
            ):
                raise GuardianStateError(
                    "pending revision is already covered by highwater"
                )

    @property
    def backup_path(self) -> Path:
        """Path of this state file's `.bak` companion written before each primary write."""
        return self.path.with_name(f"{self.path.name}.bak")

    @property
    def processed(self) -> list[SettlementKey]:
        """Compatibility observation over the compact per-run highwater."""

        return [
            (run_id, completion.revision)
            for run_id, completion in sorted(self.highwater.items())
        ]

    @classmethod
    def _from_v2_document(
        cls,
        path: Path,
        encoded: bytes,
        *,
        recovered_from_backup: bool = False,
    ) -> GuardianState:
        """Parse and checksum-verify one v2 state document into a GuardianState."""
        document = _strict_json_loads(encoded)
        if not isinstance(document, dict) or frozenset(document) not in {
            frozenset(
                {
                    "schema",
                    "cursor",
                    "baseline_complete",
                    "highwater",
                    "pending",
                    "checksum",
                }
            ),
            frozenset(
                {
                    "schema",
                    "state_generation",
                    "cursor",
                    "baseline_complete",
                    "highwater",
                    "pending",
                    "checksum",
                }
            ),
        }:
            raise GuardianStateError("guardian v2 document fields are not canonical")
        state_generation = document.get("state_generation", 0)
        if type(state_generation) is not int or state_generation < 0:
            raise GuardianStateError("guardian state generation must be non-negative")
        checksum = document.get("checksum")
        if (
            not isinstance(checksum, str)
            or len(checksum) != 64
            or any(character not in "0123456789abcdef" for character in checksum)
        ):
            raise GuardianStateError("guardian state checksum is malformed")
        body = {key: value for key, value in document.items() if key != "checksum"}
        expected_checksum = hashlib.sha256(_canonical_json(body)).hexdigest()
        if not hmac.compare_digest(checksum, expected_checksum):
            raise GuardianStateError("guardian state checksum mismatch")
        if body.get("schema") != GUARDIAN_STATE_SCHEMA:
            raise GuardianStateError("guardian state schema is not v2")
        baseline_complete = body.get("baseline_complete")
        highwater_entries = body.get("highwater")
        pending_entries = body.get("pending")
        if type(baseline_complete) is not bool:
            raise GuardianStateError("baseline_complete must be boolean")
        if not isinstance(highwater_entries, list):
            raise GuardianStateError("highwater must be a list")
        if not isinstance(pending_entries, list):
            raise GuardianStateError("pending must be a list")
        if len(pending_entries) > MAX_PENDING_RECORDS:
            raise GuardianStateLimitError("guardian pending outbox exceeds capacity")

        highwater: dict[str, CompletionRecord] = {}
        for entry in highwater_entries:
            if not isinstance(entry, dict) or set(entry) != {
                "run_id",
                "settlement_revision",
                "outcome",
                "reason",
            }:
                raise GuardianStateError("highwater entry fields are not canonical")
            run_id = _bounded_text(
                entry.get("run_id"),
                field_name="highwater.run_id",
                allow_empty=False,
                maximum=MAX_RUN_ID_BYTES,
            )
            revision = entry.get("settlement_revision")
            if type(revision) is not int or revision <= 0:
                raise GuardianStateError("highwater revision must be positive")
            if run_id in highwater:
                raise GuardianStateError("duplicate highwater run")
            highwater[run_id] = CompletionRecord(
                revision=revision,
                outcome=_bounded_text(
                    entry.get("outcome"),
                    field_name="highwater.outcome",
                    allow_empty=False,
                ),
                reason=_bounded_text(
                    entry.get("reason"),
                    field_name="highwater.reason",
                ),
            )

        pending: dict[SettlementKey, PendingRecord] = {}
        for entry in pending_entries:
            if not isinstance(entry, dict):
                raise GuardianStateError("pending record must be an object")
            record = PendingRecord.from_state_payload(entry)
            if record.key in pending:
                raise GuardianStateError("duplicate pending settlement")
            pending[record.key] = record
        return cls(
            path=path,
            cursor=_validate_cursor(body.get("cursor")),
            baseline_complete=baseline_complete,
            highwater=highwater,
            pending=pending,
            recovered_from_backup=recovered_from_backup,
            state_generation=state_generation,
        )

    @classmethod
    def _migrate_v1(cls, path: Path, encoded: bytes) -> GuardianState:
        """Migrate a legacy v1 state document to v2 with a fresh baseline.

        Legacy pending actions are folded into highwater as suppressed/unbound
        rather than replayed, since v1 carried no resume-authority guarantees.
        """
        document = _strict_json_loads(encoded)
        if not isinstance(document, dict) or set(document) != {
            "schema",
            "cursor",
            "baseline_complete",
            "processed",
            "pending",
        }:
            raise GuardianStateError("guardian v1 document fields are not canonical")
        if document.get("schema") != GUARDIAN_STATE_SCHEMA_V1:
            raise GuardianStateError("guardian state schema is unsupported")
        cursor = document.get("cursor")
        baseline = document.get("baseline_complete")
        processed_entries = document.get("processed")
        pending_entries = document.get("pending")
        if type(cursor) is not int or cursor < 0 or type(baseline) is not bool:
            raise GuardianStateError("guardian v1 cursor/baseline is invalid")
        if not isinstance(processed_entries, list) or not isinstance(
            pending_entries, list
        ):
            raise GuardianStateError("guardian v1 ledgers must be lists")

        highwater: dict[str, CompletionRecord] = {}

        def merge(run_id: str, revision: int, outcome: str, reason: str) -> None:
            """Upsert `highwater[run_id]` during v1 migration, keeping the newer revision."""
            existing = highwater.get(run_id)
            if existing is None or revision >= existing.revision:
                highwater[run_id] = CompletionRecord(revision, outcome, reason)

        for entry in processed_entries:
            if not isinstance(entry, dict) or set(entry) != {
                "run_id",
                "settlement_revision",
            }:
                raise GuardianStateError("guardian v1 processed entry is invalid")
            run_id = _bounded_text(
                entry.get("run_id"),
                field_name="v1.processed.run_id",
                allow_empty=False,
                maximum=MAX_RUN_ID_BYTES,
            )
            revision = entry.get("settlement_revision")
            if type(revision) is not int or revision <= 0:
                raise GuardianStateError("guardian v1 processed revision is invalid")
            merge(run_id, revision, "legacy_processed", "v1_migration")

        expected_event_fields = {
            "run_id",
            "settlement_revision",
            "verdict",
            "tui",
            "reason",
            "source",
            "settled_at",
        }
        for entry in pending_entries:
            if not isinstance(entry, dict) or set(entry) != expected_event_fields:
                raise GuardianStateError("guardian v1 pending entry is invalid")
            event = SettlementRevision.from_state_payload(entry)
            if event is None:
                raise GuardianStateError("guardian v1 pending settlement is invalid")
            merge(
                event.run_id,
                event.revision,
                "legacy_authority_unbound",
                "legacy_authority_unbound",
            )

        migrated = cls(
            path=path,
            cursor=0,
            baseline_complete=False,
            highwater=highwater,
        )
        migrated.persist()
        LOGGER.warning(
            "guardian v1 state migrated with fresh baseline; legacy pending suppressed"
        )
        return migrated

    @classmethod
    def load(cls, path: Path) -> GuardianState:
        """Load a verified primary/backup or enter a side-effect-free baseline."""

        found_state = False
        candidates: list[tuple[GuardianState, bytes, bool]] = []
        for candidate, is_backup in (
            (path, False),
            (path.with_name(f"{path.name}.bak"), True),
        ):
            try:
                encoded = _read_private_file(candidate, maximum=MAX_STATE_BYTES)
            except FileNotFoundError:
                continue
            except (OSError, GuardianStateError, GuardianLockSecurityError) as exc:
                found_state = True
                LOGGER.error(
                    "guardian state candidate rejected (%s): %s", candidate, exc
                )
                continue
            found_state = True
            try:
                parsed = _strict_json_loads(encoded)
                schema = parsed.get("schema") if isinstance(parsed, dict) else None
                if schema == GUARDIAN_STATE_SCHEMA:
                    candidates.append(
                        (
                            cls._from_v2_document(
                                path,
                                encoded,
                                recovered_from_backup=is_backup,
                            ),
                            encoded,
                            is_backup,
                        )
                    )
                    continue
                if schema == GUARDIAN_STATE_SCHEMA_V1:
                    return cls._migrate_v1(path, encoded)
                raise GuardianStateError("guardian state schema is unsupported")
            except (OSError, GuardianStateError, GuardianLockSecurityError) as exc:
                LOGGER.error(
                    "guardian state candidate rejected (%s): %s", candidate, exc
                )
        if candidates:
            newest_generation = max(
                state.state_generation for state, _encoded, _backup in candidates
            )
            newest = [
                candidate
                for candidate in candidates
                if candidate[0].state_generation == newest_generation
            ]
            canonical_documents = {
                _canonical_json(_strict_json_loads(encoded))
                for _state, encoded, _backup in newest
            }
            if len(canonical_documents) != 1:
                LOGGER.critical(
                    "guardian state candidates diverge at generation %s; "
                    "entering a fresh side-effect-free baseline",
                    newest_generation,
                )
                return cls(path=path, degraded=True)
            selected = next(
                (candidate for candidate in newest if not candidate[2]),
                newest[0],
            )
            state, _encoded, is_backup = selected
            state.recovered_from_backup = is_backup
            if is_backup:
                LOGGER.warning(
                    "guardian recovered verified backup state generation %s",
                    state.state_generation,
                )
            return state
        return cls(path=path, degraded=found_state)

    @staticmethod
    def _body(
        *,
        state_generation: int,
        cursor: CursorToken,
        baseline_complete: bool,
        highwater: Mapping[str, CompletionRecord],
        pending: Mapping[SettlementKey, PendingRecord],
    ) -> dict[str, object]:
        """Assemble the canonical (unchecksummed) body dict for a state snapshot."""
        return {
            "schema": GUARDIAN_STATE_SCHEMA,
            "state_generation": state_generation,
            "cursor": cursor,
            "baseline_complete": baseline_complete,
            "highwater": [
                completion.to_state_payload(run_id)
                for run_id, completion in sorted(highwater.items())
            ],
            "pending": [
                record.to_state_payload() for _key, record in sorted(pending.items())
            ],
        }

    def _persist_snapshot(
        self,
        *,
        cursor: CursorToken,
        baseline_complete: bool,
        highwater: Mapping[str, CompletionRecord],
        pending: Mapping[SettlementKey, PendingRecord],
    ) -> None:
        """Checksum, write backup+primary atomically, then update in-memory state.

        The live object is advanced to the new generation before the primary
        write so a caught OSError on the primary cannot leave in-memory state
        pointing at a stale generation the backup already exceeded.
        """
        next_generation = self.state_generation + 1
        GuardianState(
            path=self.path,
            cursor=cursor,
            baseline_complete=baseline_complete,
            highwater=dict(highwater),
            pending=dict(pending),
            degraded=self.degraded,
            recovered_from_backup=self.recovered_from_backup,
            state_generation=next_generation,
        )
        body = self._body(
            state_generation=next_generation,
            cursor=_validate_cursor(cursor),
            baseline_complete=baseline_complete,
            highwater=highwater,
            pending=pending,
        )
        checksum = hashlib.sha256(_canonical_json(body)).hexdigest()
        encoded = _canonical_json({**body, "checksum": checksum}) + b"\n"
        if len(encoded) > MAX_STATE_BYTES:
            raise GuardianStateLimitError(
                f"guardian state exceeds {MAX_STATE_BYTES} bytes"
            )
        _atomic_private_write(self.backup_path, encoded)
        # The loader treats the highest verified generation as authoritative,
        # including a backup that landed before a primary-write crash. Move the
        # live object to that same snapshot immediately so a caught OSError
        # cannot overwrite durable pending work with a different same/next
        # generation assembled from stale in-memory fields.
        self.state_generation = next_generation
        self.cursor = _validate_cursor(cursor)
        self.baseline_complete = baseline_complete
        self.highwater = dict(highwater)
        self.pending = dict(pending)
        _atomic_private_write(self.path, encoded)

    def persist(self) -> None:
        """Atomically persist the current cursor and action ledger."""

        self._persist_snapshot(
            cursor=self.cursor,
            baseline_complete=self.baseline_complete,
            highwater=self.highwater,
            pending=self.pending,
        )

    def checkpoint(self, cursor: CursorToken) -> None:
        """Persist one frame cursor transactionally.

        Numeric cursors remain compatibility-only. Opaque v2 cursors preserve
        epoch, generation, and offset across rotation.
        """

        cursor = _validate_cursor(cursor)
        if cursor == self.cursor:
            return
        self._persist_snapshot(
            cursor=cursor,
            baseline_complete=self.baseline_complete,
            highwater=self.highwater,
            pending=self.pending,
        )
        self.cursor = cursor

    def _known(self, key: SettlementKey) -> bool:
        """True if `key` is already pending or already covered by highwater."""
        if key in self.pending:
            return True
        completion = self.highwater.get(key[0])
        return completion is not None and key[1] <= completion.revision

    @staticmethod
    def _merge_highwater(
        highwater: dict[str, CompletionRecord],
        *,
        key: SettlementKey,
        outcome: str,
        reason: str,
    ) -> None:
        """In-place upsert `highwater[run_id]` only if `key`'s revision is newer/equal."""
        existing = highwater.get(key[0])
        if existing is None or key[1] >= existing.revision:
            highwater[key[0]] = CompletionRecord(
                revision=key[1],
                outcome=_bounded_text(
                    outcome,
                    field_name="completion.outcome",
                    allow_empty=False,
                ),
                reason=_bounded_text(reason, field_name="completion.reason"),
            )

    def suppress(self, cursor: CursorToken, key: SettlementKey) -> bool:
        """Checkpoint one baseline key directly as intentionally completed."""

        if self._known(key):
            self.checkpoint(cursor)
            return False
        highwater = dict(self.highwater)
        self._merge_highwater(
            highwater,
            key=key,
            outcome="baseline_suppressed",
            reason="initial_baseline",
        )
        self._persist_snapshot(
            cursor=cursor,
            baseline_complete=self.baseline_complete,
            highwater=highwater,
            pending=self.pending,
        )
        self.cursor = cursor
        self.highwater = highwater
        return True

    def claim(self, cursor: CursorToken, event: SettlementRevision) -> bool:
        """Durably enqueue one action before invoking external adapters."""

        if self._known(event.key):
            return False
        if len(self.pending) >= MAX_PENDING_RECORDS:
            raise GuardianStateLimitError("guardian pending outbox is full")
        pending = {
            **self.pending,
            event.key: PendingRecord(
                event=event,
                stream_cursor=cursor,
                resume_authorized=(
                    self.baseline_complete
                    and _is_v2_cursor(cursor)
                    and not self.degraded
                    and event.source == "trust"
                    and bool(event.receipt_id)
                ),
            ),
        }
        self._persist_snapshot(
            cursor=cursor,
            baseline_complete=self.baseline_complete,
            highwater=self.highwater,
            pending=pending,
        )
        self.cursor = cursor
        self.pending = pending
        return True

    def mark_notified(self, key: SettlementKey) -> bool:
        """Persist that the operator notification for `key` was sent; idempotent."""
        record = self.pending.get(key)
        if record is None or record.notification_done:
            return False
        pending = dict(self.pending)
        pending[key] = PendingRecord(
            event=record.event,
            stream_cursor=record.stream_cursor,
            resume_authorized=record.resume_authorized,
            notification_done=True,
            attempts=record.attempts,
            next_retry=record.next_retry,
            last_reason=record.last_reason,
            outcome=record.outcome,
        )
        self._persist_snapshot(
            cursor=self.cursor,
            baseline_complete=self.baseline_complete,
            highwater=self.highwater,
            pending=pending,
        )
        self.pending = pending
        return True

    def complete(
        self,
        key: SettlementKey,
        *,
        outcome: str,
        reason: str,
    ) -> bool:
        """Move one attempted action from pending to the durable dedupe ledger."""

        if key not in self.pending:
            return False
        pending = dict(self.pending)
        pending.pop(key)
        highwater = dict(self.highwater)
        self._merge_highwater(
            highwater,
            key=key,
            outcome=outcome,
            reason=reason,
        )
        self._persist_snapshot(
            cursor=self.cursor,
            baseline_complete=self.baseline_complete,
            highwater=highwater,
            pending=pending,
        )
        self.pending = pending
        self.highwater = highwater
        return True

    def retry(
        self,
        key: SettlementKey,
        *,
        reason: str,
        now: float,
        bounded: bool,
    ) -> bool:
        """Schedule retry; return true when bounded exhaustion became terminal."""

        record = self.pending.get(key)
        if record is None:
            return True
        attempts = min(record.attempts + 1, 1_000_000)
        if bounded and attempts >= MAX_PENDING_ATTEMPTS:
            self.complete(
                key,
                outcome="retry_exhausted",
                reason=f"retry_exhausted:{reason}",
            )
            return True
        exponent = min(max(attempts - 1, 0), 20)
        delay = min(
            PENDING_RETRY_MAX_SECONDS,
            PENDING_RETRY_INITIAL_SECONDS * (2**exponent),
        )
        pending = dict(self.pending)
        pending[key] = PendingRecord(
            event=record.event,
            stream_cursor=record.stream_cursor,
            resume_authorized=record.resume_authorized,
            notification_done=record.notification_done,
            attempts=attempts,
            next_retry=max(float(now), 0.0) + delay,
            last_reason=_bounded_text(reason, field_name="pending.last_reason"),
            outcome="retryable",
        )
        self._persist_snapshot(
            cursor=self.cursor,
            baseline_complete=self.baseline_complete,
            highwater=self.highwater,
            pending=pending,
        )
        self.pending = pending
        return False

    def pending_records(
        self,
        *,
        now: float,
        limit: int,
    ) -> tuple[PendingRecord, ...]:
        """Return up to `limit` due pending records, oldest-retry-first."""
        if limit <= 0:
            return ()
        due = [record for record in self.pending.values() if record.next_retry <= now]
        due.sort(
            key=lambda record: (
                record.next_retry,
                record.event.run_id,
                record.event.revision,
            )
        )
        return tuple(due[:limit])

    def quarantine(self, cursor: CursorToken, data: str) -> bool:
        """Persist one invalid settlement frame as an idempotent dead letter."""

        encoded = data.encode("utf-8", errors="replace")
        digest = hashlib.sha256(encoded).hexdigest()
        target = self.path.parent / "dead_letters.json"
        entries: list[dict[str, object]] = []
        try:
            existing = _strict_json_loads(
                _read_private_file(target, maximum=MAX_DEAD_LETTER_BYTES)
            )
            if (
                isinstance(existing, dict)
                and existing.get("schema") == GUARDIAN_DEAD_LETTER_SCHEMA
                and isinstance(existing.get("entries"), list)
            ):
                entries = [
                    dict(entry)
                    for entry in existing["entries"]
                    if isinstance(entry, dict)
                ]
        except (FileNotFoundError, OSError, GuardianStateError):
            entries = []
        if any(entry.get("sha256") == digest for entry in entries):
            created = False
        else:
            created = True
            entries.append(
                {
                    "sha256": digest,
                    "sse_cursor": cursor,
                    "reason": "invalid settlement.changed contract",
                    "data": encoded[:MAX_DEAD_LETTER_DATA_BYTES].decode(
                        "utf-8", errors="replace"
                    ),
                    "truncated": len(encoded) > MAX_DEAD_LETTER_DATA_BYTES,
                }
            )
            while len(entries) > MAX_DEAD_LETTER_ENTRIES:
                entries.pop(0)
            payload: dict[str, object] = {
                "schema": GUARDIAN_DEAD_LETTER_SCHEMA,
                "entries": entries,
            }
            dead_letter_bytes = _canonical_json(payload) + b"\n"
            while entries and len(dead_letter_bytes) > MAX_DEAD_LETTER_BYTES:
                entries.pop(0)
                dead_letter_bytes = _canonical_json(payload) + b"\n"
            _atomic_private_write(target, dead_letter_bytes)
        if not self.baseline_complete:
            self.checkpoint(cursor)
        return created

    def reset_for_gap(self, cursor: CursorToken, *, reason: str) -> None:
        """Revoke pending resume authority and require a fresh caught-up receipt."""

        cursor = _validate_cursor(cursor)
        reason = _bounded_text(
            reason,
            field_name="stream_gap.reason",
            allow_empty=False,
        )
        pending = {
            key: PendingRecord(
                event=record.event,
                stream_cursor=cursor,
                resume_authorized=False,
                notification_done=record.notification_done,
                attempts=record.attempts,
                next_retry=0.0,
                last_reason=f"stream_gap:{reason}",
                outcome="retryable" if record.attempts else "pending",
            )
            for key, record in self.pending.items()
        }
        # Gap revocation is a live safety boundary, not merely a persistence
        # update. Poison the in-memory authority before the first write so a
        # full storage failure cannot let this process carry old resume rights
        # across the following caught-up control frame.
        self.cursor = cursor
        self.baseline_complete = False
        self.pending = pending
        self.degraded = True
        self._persist_snapshot(
            cursor=cursor,
            baseline_complete=False,
            highwater=self.highwater,
            pending=pending,
        )

    def complete_baseline(
        self,
        cursor: CursorToken,
        *,
        authoritative: bool,
    ) -> bool:
        """Open observation only from a typed caught-up control receipt."""

        cursor = _validate_cursor(cursor)
        if authoritative and not _is_v2_cursor(cursor):
            raise GuardianStateError(
                "authoritative baseline requires an opaque v2 cursor"
            )
        pending = self.pending
        if not authoritative:
            pending = {
                key: PendingRecord(
                    event=record.event,
                    stream_cursor=record.stream_cursor,
                    resume_authorized=False,
                    notification_done=record.notification_done,
                    attempts=record.attempts,
                    next_retry=record.next_retry,
                    last_reason=record.last_reason,
                    outcome=record.outcome,
                )
                for key, record in self.pending.items()
            }
        opened = not self.baseline_complete or self.degraded
        if (
            not opened
            and self.cursor == cursor
            and self.degraded is not authoritative
            and pending == self.pending
        ):
            return False
        self._persist_snapshot(
            cursor=cursor,
            baseline_complete=True,
            highwater=self.highwater,
            pending=pending,
        )
        self.cursor = cursor
        self.baseline_complete = True
        self.degraded = not authoritative
        self.pending = pending
        return opened


@dataclass
class BoundedBackoff:
    """Exponential reconnect delay with an explicit upper bound."""

    initial: float = DEFAULT_BACKOFF_INITIAL_SECONDS
    maximum: float = DEFAULT_BACKOFF_MAX_SECONDS
    _next: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate bounds and seed the next delay at `initial`."""
        if self.initial <= 0:
            raise ValueError("initial backoff must be > 0")
        if self.maximum < self.initial:
            raise ValueError("maximum backoff must be >= initial backoff")
        self._next = self.initial

    def next_delay(self) -> float:
        """Return the current delay and double it (capped at `maximum`)."""
        delay = self._next
        self._next = min(self.maximum, self._next * 2)
        return delay

    def reset(self) -> None:
        """Reset the delay back to `initial` after a proven-stable connection."""
        self._next = self.initial


def iter_sse(
    lines: Iterable[bytes | str],
    *,
    cursor_parser: CursorParser = _parse_event_cursor,
    control_parser: ControlParser | None = None,
) -> Iterator[SSEItem]:
    """Parse bounded SSE frames with explicit opaque-extension seams."""

    raw_cursor = ""
    event_name = ""
    data: list[str] = []
    heartbeat = False
    frame_bytes = 0

    for raw_line in lines:
        encoded_line = (
            raw_line
            if isinstance(raw_line, bytes)
            else raw_line.encode("utf-8", errors="replace")
        )
        if len(encoded_line) > MAX_SSE_LINE_BYTES:
            raise GuardianProtocolError(f"SSE line exceeds {MAX_SSE_LINE_BYTES} bytes")
        frame_bytes += len(encoded_line)
        if frame_bytes > MAX_SSE_FRAME_BYTES:
            raise GuardianProtocolError(
                f"SSE frame exceeds {MAX_SSE_FRAME_BYTES} bytes"
            )
        line = (
            encoded_line.decode("utf-8", errors="replace")
            if isinstance(raw_line, bytes)
            else raw_line
        ).rstrip("\r\n")
        if line:
            if line == ": ping":
                heartbeat = True
            elif line.startswith("id:"):
                raw_cursor = line[3:].strip()
                if len(raw_cursor.encode("utf-8")) > MAX_CURSOR_BYTES:
                    raise GuardianProtocolError(
                        f"SSE cursor exceeds {MAX_CURSOR_BYTES} bytes"
                    )
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                value = line[5:]
                data.append(value.removeprefix(" "))
            continue

        if event_name:
            control = SSEControlFrame(
                event=event_name,
                raw_cursor=raw_cursor,
                data="\n".join(data),
            )
            if control_parser is None:
                yield control
            else:
                parsed_control = control_parser(control)
                if parsed_control is not None:
                    yield parsed_control
        elif data and raw_cursor:
            cursor = cursor_parser(raw_cursor)
            if cursor is not None:
                yield SSEFrame(cursor=_validate_cursor(cursor), data="\n".join(data))
        elif heartbeat:
            yield SSEHeartbeat()
        raw_cursor = ""
        event_name = ""
        data = []
        heartbeat = False
        frame_bytes = 0


def _declares_settlement_event(data: str) -> bool:
    """True when `data` parses as JSON and claims `kind == settlement.changed`."""
    try:
        frame = json.loads(data)
    except json.JSONDecodeError:
        return False
    return isinstance(frame, dict) and frame.get("kind") == SETTLEMENT_EVENT_KIND


def parse_settlement_revision(data: str) -> SettlementRevision | None:
    """Validate the typed settlement event; reject contradictions fail-closed."""

    try:
        frame = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(frame, dict) or frame.get("kind") != SETTLEMENT_EVENT_KIND:
        return None
    payload = frame.get("payload")
    if not isinstance(payload, dict):
        return None
    top_run_id = frame.get("run_id")
    if payload.get("schema") == SETTLEMENT_EVENT_SCHEMA_V2:
        if set(payload) != {
            "schema",
            "event_key",
            "run_id",
            "previous",
            "current",
            "reason",
            "source",
            "settled_at",
            "claim_digest",
            "waived",
            "revision",
            "trust_receipt",
        }:
            return None
        try:
            event_v2 = SettlementEventV2.from_payload(payload)
        except (TypeError, ValueError):
            return None
        if top_run_id != event_v2.run_id:
            return None
        return SettlementRevision(
            run_id=event_v2.run_id,
            revision=event_v2.revision,
            verdict=event_v2.current.verdict,
            tui=event_v2.current.tui,
            reason=event_v2.reason,
            source=event_v2.source,
            settled_at=event_v2.settled_at,
            receipt_id=event_v2.trust_receipt.receipt_id,
        )
    if payload.get("schema") != SETTLEMENT_EVENT_SCHEMA:
        return None
    current = payload.get("current")
    if not isinstance(current, dict):
        return None

    run_id = payload.get("run_id")
    revision = payload.get("revision")
    verdict = current.get("verdict")
    tui = current.get("tui")
    if (
        not isinstance(top_run_id, str)
        or not isinstance(run_id, str)
        or not run_id
        or top_run_id != run_id
        or type(revision) is not int
        or revision <= 0
        or not isinstance(verdict, str)
        or not isinstance(tui, str)
        or _VERDICT_TUI.get(verdict) != tui
    ):
        return None
    return SettlementRevision(
        run_id=run_id,
        revision=revision,
        verdict=verdict,
        tui=tui,
        reason=str(payload.get("reason") or ""),
        source=str(payload.get("source") or ""),
        settled_at=str(payload.get("settled_at") or ""),
    )


def notification_for(event: SettlementRevision) -> GuardianNotification:
    """Map f/n/x onto the operator severity contract."""

    severity = _TUI_SEVERITY[event.tui]
    label = _TUI_LABEL[event.tui]
    reason = event.reason or "no settlement reason"
    return GuardianNotification(
        event=event,
        severity=severity,
        title=f"Vibecrafted {event.tui}: {label}",
        message=f"{event.run_id} · r{event.revision} · {reason}",
    )


NATIVE_APP_BUNDLE_ID = "io.vetcoders.vibecrafted"
NATIVE_APP_PID_NAME = "native_app.pid"


def native_app_pid_path(*, home: Path | None = None) -> Path:
    """Heartbeat written by Vibecrafted.app so guardian can skip osascript."""

    return (
        (home if home is not None else vibecrafted_home())
        / "control_plane"
        / NATIVE_APP_PID_NAME
    )


def _native_app_comm(pid: int) -> str | None:
    """Return ``ps`` comm for ``pid``, or None when the lookup cannot run."""

    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    comm = (result.stdout or "").strip()
    return comm or None


def native_app_is_alive(
    *,
    home: Path | None = None,
    kill: Callable[[int, int], None] = os.kill,
    comm_reader: Callable[[int], str | None] | None = None,
) -> bool:
    """True when Vibecrafted.app left a live heartbeat pid.

    Mux being up is not enough: tray/TUI can own the socket without the
    Swift bundle that posts ``UNUserNotificationCenter`` banners.
    """

    path = native_app_pid_path(home=home)
    try:
        raw = path.read_text(encoding="utf-8").strip().splitlines()
        pid = int(raw[0])
        recorded_bundle = raw[1].strip() if len(raw) > 1 else ""
    except (OSError, ValueError, IndexError):
        return False
    if pid <= 0:
        return False
    if recorded_bundle and recorded_bundle != NATIVE_APP_BUNDLE_ID:
        return False
    try:
        kill(pid, 0)
    except OSError:
        return False
    comm = (comm_reader or _native_app_comm)(pid)
    if comm is None:
        return True
    return Path(comm).name == "Vibecrafted"


def _append_run_settled_event(
    notification: GuardianNotification, *, home: Path | None = None
) -> None:
    """Append a spawn-update the jsonl bridge already maps to IpcEvent."""

    control = (home if home is not None else vibecrafted_home()) / "control_plane"
    control.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = control / "events.jsonl"
    event = {
        "ts": notification.event.settled_at
        or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": notification.event.run_id,
        "kind": "spawn-update",
        "payload": {
            "agent": "guardian",
            "skill": "settlement",
            "mode": notification.event.tui,
            "state": "settled",
            "status": "settled",
            "verdict": notification.event.verdict,
            "reason": notification.event.reason,
        },
    }
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def notify_operator(
    notification: GuardianNotification,
    *,
    desktop: bool = True,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    app_alive: Callable[[], bool] | None = None,
    home: Path | None = None,
) -> None:
    """Log every notification; native app owns the banner when it is alive."""

    level = {
        "info": logging.INFO,
        "warning": logging.WARNING,
        "critical": logging.CRITICAL,
    }[notification.severity]
    LOGGER.log(level, "%s — %s", notification.title, notification.message)

    if not desktop or (platform or sys.platform) != "darwin":
        return
    try:
        _append_run_settled_event(notification, home=home)
    except OSError as exc:
        LOGGER.warning("control-plane settlement event not written: %s", exc)
    alive = (
        app_alive if app_alive is not None else (lambda: native_app_is_alive(home=home))
    )
    if alive():
        LOGGER.info(
            "native app owns the notification; skipping osascript degradation (%s)",
            notification.event.run_id,
        )
        return
    LOGGER.info(
        "native app absent; osascript degradation for %s",
        notification.event.run_id,
    )
    osascript = which("osascript")
    if not osascript:
        return
    script = (
        "on run argv\n"
        "display notification item 2 of argv with title item 1 of argv\n"
        "end run"
    )
    try:
        result = runner(
            [osascript, "-e", script, "--", notification.title, notification.message],
            check=False,
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        LOGGER.warning("macOS notification unavailable: %s", exc)
        return
    if result.returncode != 0:
        LOGGER.warning("macOS notification failed with exit %s", result.returncode)


def fail_closed_reconcile(_event: SettlementRevision) -> ReconcileDecision:
    """Default reconcile boundary: observe once, never infer resume authority."""

    return ReconcileDecision()


@dataclass(frozen=True)
class RecoveryContext:
    """Server-verified arguments for one guarded native resume."""

    root: Path
    agent: str
    agent_session_id: str
    skill: str
    sha: str
    settlement_revision: int
    receipt_id: str


def _default_guard_enforcer(
    *,
    repo: Path | None = None,
    journal: Path | None = None,
    sha: str = "",
    skill: str = "",
) -> object:
    """Default GuardEnforcer: delegates to `guard.enforce_continuation`."""
    from .guard import enforce_continuation

    return enforce_continuation(
        repo=repo,
        journal=journal,
        sha=sha,
        skill=skill,
    )


def _default_native_resumer(
    run_id: str,
    source_dir: str | Path,
    *,
    expected_agent: str,
    expected_agent_session_id: str,
    expected_settlement_revision: int,
    expected_receipt_id: str,
    idempotency_key: str,
) -> Mapping[str, object]:
    """Default NativeResumer: delegates to `workflow.native_resume_run`."""
    from .workflow import native_resume_run

    return native_resume_run(
        run_id,
        source_dir=source_dir,
        expected_agent=expected_agent,
        expected_agent_session_id=expected_agent_session_id,
        expected_settlement_revision=expected_settlement_revision,
        expected_receipt_id=expected_receipt_id,
        idempotency_key=idempotency_key,
    )


def _explicit_identity(value: object) -> str:
    """Normalize `value` to a string, treating placeholder tokens as unset."""
    candidate = str(value or "").strip()
    if candidate.lower() in {"", "unknown", "pending", "none", "null"}:
        return ""
    return candidate


def _validate_server_url(value: str) -> str:
    """Validate `value` is a bare http(s) origin (no path/query/fragment)."""
    normalized = value.rstrip("/")
    parsed = urllib.parse.urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("server URL must be an http(s) origin without a path")
    return normalized


def _base_media_type(value: object) -> str:
    """Strip parameters (e.g. `; charset=`) from a Content-Type header value."""
    return str(value or "").split(";", 1)[0].strip().lower()


def _server_run_is_terminal(run: Mapping[str, object]) -> bool:
    """True when the server-projected run record is in a terminal state."""
    # Mirrors control_plane._run_is_terminal / control-core RunStatus::is_terminal.
    terminal_states = {
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
    }
    return (
        str(run.get("state") or "") in terminal_states
        or str(run.get("liveness") or "") == "terminal"
        or run.get("exit_code") is not None
    )


def _canonical_triage_run_id(run_id: object) -> str:
    """Validate `run_id` is a safe bare filename component; raise otherwise."""
    candidate = str(run_id or "").strip()
    if (
        not candidate
        or candidate in {".", ".."}
        or len(candidate.encode("utf-8")) > MAX_RUN_ID_BYTES
        or Path(candidate).name != candidate
        or "/" in candidate
        or "\\" in candidate
    ):
        raise GuardianStateError("terminal triage run id is not canonical")
    return candidate


@dataclass(frozen=True)
class TerminalTriageOutboxRecord:
    """One bounded retry receipt for terminal projection reconciliation."""

    run_id: str
    queued_at_ns: int
    attempts: int = 0
    next_retry_at_ns: int = 0
    last_reason: str = ""


def _terminal_triage_outbox_root() -> Path:
    """Return (creating if needed) the durable terminal-triage outbox directory."""
    root = vibecrafted_home() / "control_plane" / "guardian" / "triage-outbox"
    _ensure_private_directory(root)
    return root


def _terminal_triage_quarantine_root() -> Path:
    """Return (creating if needed) the terminal-triage quarantine directory."""
    root = vibecrafted_home() / "control_plane" / "guardian" / "triage-quarantine"
    _ensure_private_directory(root)
    return root


def _terminal_triage_quarantine_archive_root() -> Path:
    """Return the append-only archive for evidence retired from the hot quarantine."""
    root = (
        vibecrafted_home() / "control_plane" / "guardian" / "triage-quarantine-archive"
    )
    _ensure_private_directory(root)
    return root


def _terminal_triage_outbox_path(run_id: str) -> Path:
    """Return the digest-named outbox file path for `run_id`."""
    candidate = _canonical_triage_run_id(run_id)
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    return _terminal_triage_outbox_root() / f"{digest}.json"


def _terminal_triage_quarantine_has_capacity(root: Path) -> bool:
    """True while `root` holds fewer than TERMINAL_TRIAGE_QUARANTINE_CAPACITY entries."""
    if TERMINAL_TRIAGE_QUARANTINE_CAPACITY <= 0:
        return False
    for count, _entry in enumerate(root.iterdir(), start=1):
        if count >= TERMINAL_TRIAGE_QUARANTINE_CAPACITY:
            return False
    return True


def _ensure_terminal_triage_quarantine_capacity_locked(root: Path) -> bool:
    """Archive oldest evidence until the bounded hot quarantine has one free slot.

    The quarantine is an operator-facing working set, not the only copy of the
    evidence.  Previously, reaching its cap permanently pinned poison jobs in
    the retry outbox and emitted one CRITICAL line per job every sweep.  Moving
    the oldest entries to a separate append-only archive preserves every byte
    while restoring forward progress.
    """

    capacity = TERMINAL_TRIAGE_QUARANTINE_CAPACITY
    if capacity <= 0:
        return False

    entries: list[tuple[int, str, Path]] = []
    for index, entry in enumerate(root.iterdir(), start=1):
        if index > TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT:
            LOGGER.critical(
                "terminal triage quarantine archive scan exceeded hard limit %s",
                TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT,
            )
            return False
        try:
            modified_at_ns = entry.lstat().st_mtime_ns
        except OSError as exc:
            LOGGER.critical(
                "terminal triage quarantine evidence cannot be inspected at %s: %s",
                entry,
                exc,
            )
            return False
        entries.append((modified_at_ns, entry.name, entry))

    archive_count = len(entries) - capacity + 1
    if archive_count <= 0:
        return True

    archive_root = _terminal_triage_quarantine_archive_root()
    archived = 0
    for _modified_at_ns, name, source in sorted(entries)[:archive_count]:
        safe_name = "".join(
            character
            if character.isascii() and (character.isalnum() or character in "._-")
            else "_"
            for character in name
        )[:96]
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        destination = archive_root / (
            f"{time.time_ns()}-{digest}-{secrets.token_hex(4)}-{safe_name}.archived"
        )
        try:
            os.replace(source, destination)
        except FileNotFoundError:
            continue
        except OSError as exc:
            LOGGER.critical(
                "terminal triage quarantine evidence could not be archived %s: %s",
                source,
                exc,
            )
            return False
        archived += 1

    _fsync_directory(archive_root)
    _fsync_directory(root)
    LOGGER.warning(
        "terminal triage archived %s oldest quarantine entr%s to %s",
        archived,
        "y" if archived == 1 else "ies",
        archive_root,
    )
    return _terminal_triage_quarantine_has_capacity(root)


def _quarantine_terminal_triage_outbox_locked(path: Path) -> bool:
    """Move invalid evidence aside while the caller holds the triage lock."""

    outbox_root = _terminal_triage_outbox_root()
    if path.parent != outbox_root:
        raise GuardianStateError(
            f"terminal triage quarantine target is outside the outbox: {path}"
        )

    try:
        _read_terminal_triage_outbox(path)
    except FileNotFoundError:
        return True
    except (OSError, ValueError, GuardianStateError) as exc:
        reason = f"{type(exc).__name__}: {exc}"
    else:
        return False

    quarantine_root = _terminal_triage_quarantine_root()
    if not _ensure_terminal_triage_quarantine_capacity_locked(quarantine_root):
        LOGGER.critical(
            "terminal triage quarantine is full; invalid evidence remains at %s",
            path,
        )
        return False

    safe_name = "".join(
        character
        if character.isascii() and (character.isalnum() or character in "._-")
        else "_"
        for character in path.name
    )[:64]
    digest = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:16]
    destination = quarantine_root / (
        f"{time.time_ns()}-{digest}-{secrets.token_hex(4)}-{safe_name}.quarantined"
    )
    try:
        os.replace(path, destination)
    except FileNotFoundError:
        return True
    _fsync_directory(quarantine_root)
    _fsync_directory(outbox_root)

    LOGGER.error(
        "terminal triage quarantined invalid outbox evidence %s -> %s (%s)",
        path,
        destination,
        reason,
    )
    return True


def _quarantine_terminal_triage_outbox(path: Path) -> bool:
    """Lock and move one invalid outbox entry without following or deleting it."""

    with _TERMINAL_TRIAGE_LOCK:
        return _quarantine_terminal_triage_outbox_locked(path)


def _bounded_terminal_triage_outbox_occupancy_locked(
    root: Path,
) -> tuple[int, bool]:
    """Count valid jobs within one bounded scheduling scan under the lock."""

    limit = TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT
    if limit <= 0:
        raise GuardianStateLimitError(
            "terminal triage outbox scan limit must be positive"
        )
    page: list[Path] = []
    for entry in root.iterdir():
        page.append(entry)
        if len(page) > limit:
            break

    complete = len(page) <= limit
    valid_jobs = 0
    for entry in page[:limit]:
        try:
            _read_terminal_triage_outbox(entry)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, GuardianStateError):
            if not _quarantine_terminal_triage_outbox_locked(entry):
                complete = False
            continue
        valid_jobs += 1
    return valid_jobs, complete


def _persist_terminal_triage_outbox(run_id: str) -> Path:
    """Durably (re)queue one terminal-triage job, admitting new jobs only under capacity."""
    candidate = _canonical_triage_run_id(run_id)
    path = _terminal_triage_outbox_path(candidate)
    # Existing jobs may always refresh their generation. New jobs are admitted
    # only while the bounded recovery page can still cover every valid record.
    with _TERMINAL_TRIAGE_LOCK:
        previous = TerminalTriageOutboxRecord(candidate, 0)
        try:
            previous = _read_terminal_triage_outbox(path)
            exists = True
        except FileNotFoundError:
            exists = False
        except (OSError, ValueError, GuardianStateError) as exc:
            if not _quarantine_terminal_triage_outbox_locked(path):
                raise GuardianStateError(
                    "invalid terminal triage outbox could not be quarantined"
                ) from exc
            exists = False
        if not exists:
            valid_jobs, scan_complete = (
                _bounded_terminal_triage_outbox_occupancy_locked(path.parent)
            )
            if valid_jobs >= TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT:
                raise GuardianStateLimitError(
                    "terminal triage outbox is at its hard recovery capacity"
                )
            if not scan_complete:
                raise GuardianStateLimitError(
                    "terminal triage outbox capacity could not be established "
                    "within its bounded scheduling scan"
                )
        generation = max(time.time_ns(), previous.queued_at_ns + 1)
        _atomic_private_write(
            path,
            _terminal_triage_outbox_document(
                TerminalTriageOutboxRecord(
                    candidate,
                    generation,
                    attempts=previous.attempts,
                    next_retry_at_ns=previous.next_retry_at_ns,
                    last_reason=previous.last_reason,
                )
            ),
        )
    return path


def _terminal_triage_outbox_document(record: TerminalTriageOutboxRecord) -> bytes:
    """Serialize one terminal-triage outbox record to canonical JSON bytes."""
    return (
        json.dumps(
            {
                "schema": TERMINAL_TRIAGE_OUTBOX_SCHEMA,
                "run_id": record.run_id,
                "queued_at_ns": record.queued_at_ns,
                "attempts": record.attempts,
                "next_retry_at_ns": record.next_retry_at_ns,
                "last_reason": record.last_reason,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _read_terminal_triage_outbox(path: Path) -> TerminalTriageOutboxRecord:
    """Read and validate one outbox record; enforces its filename binds its run id."""
    payload = _strict_json_loads(_read_private_file(path, maximum=64 * 1024))
    if not isinstance(payload, dict):
        raise GuardianStateError(f"terminal triage outbox is invalid: {path}")
    schema = payload.get("schema")
    if schema == TERMINAL_TRIAGE_OUTBOX_SCHEMA_V1:
        expected_fields = {"schema", "run_id", "queued_at_ns"}
        attempts = 0
        next_retry_at_ns = 0
        last_reason = ""
    elif schema == TERMINAL_TRIAGE_OUTBOX_SCHEMA:
        expected_fields = {
            "schema",
            "run_id",
            "queued_at_ns",
            "attempts",
            "next_retry_at_ns",
            "last_reason",
        }
        attempts = payload.get("attempts")
        next_retry_at_ns = payload.get("next_retry_at_ns")
        last_reason = payload.get("last_reason")
        if (
            type(attempts) is not int
            or attempts < 0
            or type(next_retry_at_ns) is not int
            or next_retry_at_ns < 0
            or not isinstance(last_reason, str)
            or len(last_reason.encode("utf-8")) > MAX_STATE_TEXT_BYTES
        ):
            raise GuardianStateError(f"terminal triage outbox is invalid: {path}")
    else:
        raise GuardianStateError(f"terminal triage outbox is invalid: {path}")
    if (
        set(payload) != expected_fields
        or type(payload.get("queued_at_ns")) is not int
        or payload["queued_at_ns"] <= 0
    ):
        raise GuardianStateError(f"terminal triage outbox is invalid: {path}")
    run_id = _canonical_triage_run_id(payload.get("run_id"))
    if path != _terminal_triage_outbox_path(run_id):
        raise GuardianStateError(
            f"terminal triage outbox filename does not bind its run id: {path}"
        )
    return TerminalTriageOutboxRecord(
        run_id,
        payload["queued_at_ns"],
        attempts=attempts,
        next_retry_at_ns=next_retry_at_ns,
        last_reason=last_reason,
    )


def _clear_terminal_triage_outbox(
    run_id: str,
    *,
    expected_generation: int | None = None,
) -> bool:
    """Remove one outbox record; skip if its generation moved past `expected_generation`."""
    path = _terminal_triage_outbox_path(run_id)
    with _TERMINAL_TRIAGE_LOCK:
        try:
            if expected_generation is not None:
                current = _read_terminal_triage_outbox(path)
                if current.queued_at_ns != expected_generation:
                    return False
            _validate_existing_private_file(path)
            path.unlink()
        except FileNotFoundError:
            return False
        _fsync_directory(path.parent)
    return True


def _terminal_triage_failure_reason(run_id: str) -> str:
    """Read the last durable triage failure without invoking a terminal process."""

    meta = (
        vibecrafted_home()
        / "control_plane"
        / "runtime_runs"
        / _canonical_triage_run_id(run_id)
        / "meta.json"
    )
    try:
        payload = _strict_json_loads(_read_private_file(meta, maximum=MAX_STATE_BYTES))
    except (FileNotFoundError, OSError, ValueError, GuardianStateError):
        return "runtime_meta_unavailable"
    if not isinstance(payload, dict):
        return "runtime_meta_invalid"
    last_error = payload.get("triage_last_error")
    if isinstance(last_error, dict):
        reason = str(last_error.get("reason") or "").strip()
        if reason:
            return _truncate_utf8_bytes(reason)
    return _truncate_utf8_bytes(
        str(payload.get("triage_reason") or "triage_incomplete")
    )


def _terminal_triage_reason_is_permanent(reason: str) -> bool:
    """Classify stale/proven-invalid projection work that operator action must resolve."""

    normalized = reason.strip().lower()
    missing_session = "not found" in normalized and (
        "session '" in normalized or 'session "' in normalized
    )
    return (
        missing_session
        or normalized.startswith("transfer_proof_invalid:")
        or any(
            marker in normalized
            for marker in (
                "origin tab no longer exists",
                "origin tab is not proven closed",
                "receipt identity",
                "runtime_meta_unavailable",
                "runtime_meta_invalid",
            )
        )
    )


def _dead_letter_terminal_triage_outbox_locked(
    record: TerminalTriageOutboxRecord,
    reason: str,
) -> bool:
    """Retire one valid poison job into bounded operator-visible quarantine."""

    outbox_path = _terminal_triage_outbox_path(record.run_id)
    quarantine_root = _terminal_triage_quarantine_root()
    if not _ensure_terminal_triage_quarantine_capacity_locked(quarantine_root):
        LOGGER.critical(
            "terminal triage quarantine is full; poison job remains at %s",
            outbox_path,
        )
        return False
    digest = hashlib.sha256(record.run_id.encode("utf-8")).hexdigest()[:16]
    destination = quarantine_root / (
        f"{time.time_ns()}-{digest}-{secrets.token_hex(4)}.dead-letter.json"
    )
    document = (
        json.dumps(
            {
                "schema": TERMINAL_TRIAGE_DEAD_LETTER_SCHEMA,
                "run_id": record.run_id,
                "attempts": record.attempts + 1,
                "reason": _truncate_utf8_bytes(reason),
                "dead_lettered_at_ns": time.time_ns(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    _atomic_private_write(destination, document)
    try:
        current = _read_terminal_triage_outbox(outbox_path)
        if current.queued_at_ns != record.queued_at_ns:
            destination.unlink(missing_ok=True)
            _fsync_directory(quarantine_root)
            return False
        outbox_path.unlink()
    except FileNotFoundError:
        destination.unlink(missing_ok=True)
        _fsync_directory(quarantine_root)
        return False
    _fsync_directory(outbox_path.parent)
    LOGGER.error(
        "terminal triage dead-lettered %s after %s attempt(s): %s",
        record.run_id,
        record.attempts + 1,
        reason,
    )
    return True


def _refresh_terminal_triage_outbox(
    run_id: str,
    *,
    expected_generation: int,
    reason: str,
) -> bool:
    """Back off one retry or dead-letter it after a bounded attempt budget."""

    candidate = _canonical_triage_run_id(run_id)
    path = _terminal_triage_outbox_path(candidate)
    with _TERMINAL_TRIAGE_LOCK:
        try:
            current = _read_terminal_triage_outbox(path)
        except (FileNotFoundError, OSError, ValueError, GuardianStateError):
            return False
        if current.queued_at_ns != expected_generation:
            return False
        attempts = current.attempts + 1
        if (
            _terminal_triage_reason_is_permanent(reason)
            or attempts >= TERMINAL_TRIAGE_MAX_ATTEMPTS
        ):
            return _dead_letter_terminal_triage_outbox_locked(current, reason)
        generation = max(time.time_ns(), current.queued_at_ns + 1)
        delay_seconds = min(
            TERMINAL_TRIAGE_RETRY_MAX_SECONDS,
            TERMINAL_TRIAGE_RETRY_INITIAL_SECONDS * (2 ** (attempts - 1)),
        )
        _atomic_private_write(
            path,
            _terminal_triage_outbox_document(
                TerminalTriageOutboxRecord(
                    candidate,
                    generation,
                    attempts=attempts,
                    next_retry_at_ns=time.time_ns()
                    + int(delay_seconds * 1_000_000_000),
                    last_reason=_truncate_utf8_bytes(reason),
                )
            ),
        )
    return True


def _reconcile_terminal_triage_run(run_id: str) -> bool:
    """Repair one terminal run and report whether its outbox may be retired."""

    try:
        candidate = _canonical_triage_run_id(run_id)
    except GuardianStateError:
        return False
    meta = (
        vibecrafted_home() / "control_plane" / "runtime_runs" / candidate / "meta.json"
    )
    if not meta.is_file() or meta.is_symlink():
        return False

    from .run_triage import (
        OUTCOME_ERROR,
        triage_finished_run,
        triage_outcome_is_complete,
    )

    outcome = triage_finished_run(meta)
    if outcome.outcome == OUTCOME_ERROR:
        LOGGER.error(
            "terminal triage reconciliation failed for %s: %s",
            candidate,
            outcome.reason,
        )
    return triage_outcome_is_complete(outcome)


def _attempt_terminal_triage_record(record: TerminalTriageOutboxRecord) -> None:
    """Attempt one due job, with poison detection before any vc-frame invocation."""

    existing_reason = _terminal_triage_failure_reason(record.run_id)
    if _terminal_triage_reason_is_permanent(existing_reason):
        with _TERMINAL_TRIAGE_LOCK:
            try:
                current = _read_terminal_triage_outbox(
                    _terminal_triage_outbox_path(record.run_id)
                )
            except (FileNotFoundError, OSError, ValueError, GuardianStateError):
                return
            if current.queued_at_ns == record.queued_at_ns:
                _dead_letter_terminal_triage_outbox_locked(current, existing_reason)
        return
    if _reconcile_terminal_triage_run(record.run_id):
        _clear_terminal_triage_outbox(
            record.run_id,
            expected_generation=record.queued_at_ns,
        )
        return
    _refresh_terminal_triage_outbox(
        record.run_id,
        expected_generation=record.queued_at_ns,
        reason=_terminal_triage_failure_reason(record.run_id),
    )


def _recover_terminal_triage_outbox() -> None:
    """Retry a bounded oldest-first page of durable jobs."""

    root = _terminal_triage_outbox_root()
    newest_first: list[tuple[int, str, TerminalTriageOutboxRecord]] = []
    scanned = 0
    scan_saturated = False
    if TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT <= 0:
        LOGGER.critical("terminal triage outbox scan limit must be positive")
        return
    for entry_index, path in enumerate(root.iterdir(), start=1):
        if entry_index > TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT:
            scan_saturated = True
            break
        if path.name.startswith(".") or path.suffix != ".json":
            continue
        scanned += 1
        try:
            record = _read_terminal_triage_outbox(path)
        except Exception:
            if not _quarantine_terminal_triage_outbox(path):
                LOGGER.exception("terminal triage outbox remains corrupt: %s", path)
            continue
        if record.next_retry_at_ns > time.time_ns():
            continue
        item = (-record.queued_at_ns, record.run_id, record)
        if len(newest_first) < TERMINAL_TRIAGE_OUTBOX_ATTEMPT_LIMIT:
            heapq.heappush(newest_first, item)
        elif item > newest_first[0]:
            heapq.heapreplace(newest_first, item)
    if scan_saturated:
        LOGGER.critical(
            "terminal triage outbox scan reached hard capacity %s after %s "
            "candidates; draining the oldest bounded page",
            TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT,
            scanned,
        )

    candidates = sorted(
        (
            (-queued_at_ns, run_id, record)
            for queued_at_ns, run_id, record in newest_first
        )
    )
    for _queued_at_ns, run_id, record in candidates:
        try:
            _attempt_terminal_triage_record(record)
        except Exception:
            LOGGER.exception(
                "terminal triage outbox retry failed for %s; receipt remains durable",
                run_id,
            )
            _refresh_terminal_triage_outbox(
                run_id,
                expected_generation=record.queued_at_ns,
                reason="guardian_reconciliation_exception",
            )


_TERMINAL_TRIAGE_JOBS: queue.Queue[tuple[str, str | None]] = queue.Queue(
    maxsize=TERMINAL_TRIAGE_QUEUE_CAPACITY
)
_TERMINAL_TRIAGE_PENDING: set[str] = set()
_TERMINAL_TRIAGE_LOCK = threading.Lock()
_TERMINAL_TRIAGE_THREAD: threading.Thread | None = None
_TERMINAL_TRIAGE_STOP = threading.Event()


def _run_terminal_triage_jobs() -> None:
    """Background worker loop: drain the job queue, else periodically sweep the outbox."""
    while not _TERMINAL_TRIAGE_STOP.is_set():
        try:
            key, run_id = _TERMINAL_TRIAGE_JOBS.get(
                timeout=TERMINAL_TRIAGE_RETRY_SECONDS
            )
        except queue.Empty:
            if _TERMINAL_TRIAGE_STOP.is_set():
                break
            try:
                _recover_terminal_triage_outbox()
            except Exception:
                LOGGER.exception("periodic terminal triage outbox recovery crashed")
            continue
        try:
            if run_id is None:
                _recover_untriaged_runs_background()
            else:
                path = _terminal_triage_outbox_path(run_id)
                record = _read_terminal_triage_outbox(path)
                if record.next_retry_at_ns <= time.time_ns():
                    _attempt_terminal_triage_record(record)
        except Exception:
            LOGGER.exception("terminal triage background job crashed for %s", key)
        finally:
            with _TERMINAL_TRIAGE_LOCK:
                _TERMINAL_TRIAGE_PENDING.discard(key)
            _TERMINAL_TRIAGE_JOBS.task_done()


def _enqueue_terminal_triage_job(key: str, run_id: str | None) -> bool:
    """Enqueue one coalesced job without waiting in the SSE decision path."""

    global _TERMINAL_TRIAGE_THREAD

    with _TERMINAL_TRIAGE_LOCK:
        if key in _TERMINAL_TRIAGE_PENDING:
            return True
        try:
            _TERMINAL_TRIAGE_JOBS.put_nowait((key, run_id))
        except queue.Full:
            LOGGER.error(
                "terminal triage queue is full; durable job %s remains for recovery",
                key,
            )
            return False
        _TERMINAL_TRIAGE_PENDING.add(key)
        if _TERMINAL_TRIAGE_THREAD is None or not _TERMINAL_TRIAGE_THREAD.is_alive():
            _TERMINAL_TRIAGE_THREAD = threading.Thread(
                target=_run_terminal_triage_jobs,
                name="vibecrafted-terminal-triage",
                daemon=True,
            )
            _TERMINAL_TRIAGE_THREAD.start()
    return True


def _schedule_terminal_triage_run(run_id: str) -> bool:
    """Durably queue and enqueue one run's terminal-triage job (the TriageScheduler)."""
    try:
        candidate = _canonical_triage_run_id(run_id)
        _persist_terminal_triage_outbox(candidate)
    except Exception:
        LOGGER.exception(
            "terminal triage work could not be persisted for %r",
            run_id,
        )
        return False
    return _enqueue_terminal_triage_job(f"run:{candidate}", candidate)


def _schedule_triage_startup_sweep() -> bool:
    """Enqueue the one-time startup sweep that recovers dispatcher-orphaned runs."""
    return _enqueue_terminal_triage_job("startup-sweep", None)


class GuardianRecoveryAdapter:
    """HTTP truth check -> vc-guard -> idempotent native-resume adapter."""

    def __init__(
        self,
        *,
        server_url: str,
        opener: UrlOpener = urllib.request.urlopen,
        timeout: float = 5.0,
        guard_enforcer: GuardEnforcer = _default_guard_enforcer,
        native_resumer: NativeResumer = _default_native_resumer,
    ) -> None:
        """Bind server URL, HTTP opener, and injected guard/resume adapters."""
        self.server_url = _validate_server_url(server_url)
        if timeout <= 0:
            raise ValueError("recovery HTTP timeout must be > 0")
        self.opener = opener
        self.timeout = timeout
        self.guard_enforcer = guard_enforcer
        self.native_resumer = native_resumer
        self._contexts: dict[SettlementKey, RecoveryContext] = {}

    def _fetch_run(self, run_id: str) -> dict[str, object]:
        """Fetch and strictly validate the JSON run projection for `run_id`."""
        encoded = urllib.parse.quote(run_id, safe="")
        request = urllib.request.Request(
            f"{self.server_url}/api/control/runs/{encoded}",
            headers={"Accept": "application/json", "Cache-Control": "no-cache"},
        )
        response = self.opener(request, timeout=self.timeout)
        with contextlib.closing(response):
            status = getattr(response, "status", 200)
            if status != 200:
                raise GuardianProtocolError(
                    f"run projection returned HTTP {status} for {run_id}"
                )
            headers = getattr(response, "headers", {})
            content_type = str(headers.get("Content-Type", "") or "")
            if _base_media_type(content_type) != "application/json":
                raise GuardianProtocolError(
                    f"run projection returned unexpected content type {content_type!r}"
                )
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise GuardianProtocolError("run projection exceeds 1 MiB")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GuardianProtocolError(
                f"run projection is not valid JSON for {run_id}"
            ) from exc
        if not isinstance(payload, dict):
            raise GuardianProtocolError("run projection must be a JSON object")
        return payload

    @staticmethod
    def _attempt(run: Mapping[str, object]) -> int | None:
        """Return the run's recovery attempt count, defaulting to 0; None if malformed."""
        raw = run.get("attempt")
        if raw is None:
            return 0
        if type(raw) is not int or raw < 0:
            return None
        return raw

    def reconcile(self, event: SettlementRevision) -> ReconcileDecision:
        """Fetch the exact run projection and decide whether resume is safe."""

        self._contexts.pop(event.key, None)
        run = self._fetch_run(event.run_id)
        revision = run.get("settlement_revision")
        if (
            run.get("run_id") != event.run_id
            or run.get("settlement_tui") != event.tui
            or run.get("settlement_verdict") != event.verdict
            or type(revision) is not int
            or revision != event.revision
        ):
            return ReconcileDecision(reason="stale_or_mismatched_settlement")
        if event.tui != TUI_NEEDS_ATTENTION:
            return ReconcileDecision(reason=f"settlement_{event.tui}_not_resumable")
        if (
            event.verdict != "needs_attention"
            or event.source != "trust"
            or run.get("settlement_source") != "trust"
            or not event.receipt_id
        ):
            return ReconcileDecision(reason="vc_trust_authority_missing")
        receipt_payload = run.get("trust_receipt")
        if not isinstance(receipt_payload, Mapping):
            return ReconcileDecision(reason="trust_receipt_missing")
        try:
            projected_receipt = TrustReceiptV1.from_payload(receipt_payload)
        except (TypeError, ValueError):
            return ReconcileDecision(reason="trust_receipt_invalid")
        if (
            projected_receipt.receipt_id != event.receipt_id
            or projected_receipt.run_id != event.run_id
            or projected_receipt.settlement_revision != event.revision
            or projected_receipt.settlement_verdict != event.verdict
            or projected_receipt.settlement_tui != event.tui
        ):
            return ReconcileDecision(reason="trust_receipt_mismatch")
        if run.get("worker_alive") is not False:
            return ReconcileDecision(reason="worker_not_confirmed_dead")
        if run.get("recovery_required") is not True:
            return ReconcileDecision(reason="recovery_not_required")
        stop_reason = str(run.get("stop_reason") or "").strip().lower()
        if any(token in stop_reason for token in ("manual", "stop", "cancel")):
            return ReconcileDecision(reason="manual_stop_or_cancel")
        if not _server_run_is_terminal(run):
            return ReconcileDecision(reason="run_not_terminal")
        attempt = self._attempt(run)
        if attempt is None or attempt >= 1:
            return ReconcileDecision(reason="automatic_attempt_budget_exhausted")

        controls = run.get("controls")
        if not isinstance(controls, Mapping):
            return ReconcileDecision(reason="native_resume_candidate_missing")
        candidate = controls.get("native_resume_candidate")
        if not isinstance(candidate, Mapping):
            return ReconcileDecision(reason="native_resume_candidate_missing")
        agent = _explicit_identity(candidate.get("agent"))
        agent_session_id = _explicit_identity(candidate.get("agent_session_id"))
        if not agent or not agent_session_id:
            return ReconcileDecision(reason="native_resume_identity_missing")
        projected_agent = _explicit_identity(run.get("agent"))
        if projected_agent and projected_agent != agent:
            return ReconcileDecision(reason="native_resume_agent_mismatch")

        root = _explicit_identity(run.get("root"))
        if not root:
            return ReconcileDecision(reason="run_root_missing")
        skill = str(run.get("skill") or "").strip()
        sha = ""
        for projection_field in ("commit_sha", "sha", "commit"):
            sha = _explicit_identity(run.get(projection_field))
            if sha:
                break
        if (
            Path(projected_receipt.repo_root) != Path(root)
            or projected_receipt.commit_sha != sha
        ):
            return ReconcileDecision(reason="trust_receipt_projection_mismatch")
        decision = self.guard_enforcer(
            repo=Path(root),
            sha=sha,
            skill=skill,
        )
        allowed = (
            decision.get("allowed") is True
            if isinstance(decision, Mapping)
            else getattr(decision, "allowed", None) is True
        )
        if not allowed:
            return ReconcileDecision(reason="vc_guard_blocked")

        self._contexts[event.key] = RecoveryContext(
            root=Path(root),
            agent=agent,
            agent_session_id=agent_session_id,
            skill=skill,
            sha=sha,
            settlement_revision=event.revision,
            receipt_id=event.receipt_id,
        )
        return ReconcileDecision(
            request_resume=True,
            reason="server_truth_and_vc_guard_allow_resume",
        )

    def resume(
        self,
        event: SettlementRevision,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        """Invoke the tracked native-resume boundary with the stable key."""

        context = self._contexts.get(event.key)
        if context is None:
            raise RuntimeError(f"resume context missing for {event.idempotency_key}")
        try:
            return self.native_resumer(
                event.run_id,
                source_dir=context.root,
                expected_agent=context.agent,
                expected_agent_session_id=context.agent_session_id,
                expected_settlement_revision=context.settlement_revision,
                expected_receipt_id=context.receipt_id,
                idempotency_key=idempotency_key,
            )
        finally:
            # A context is a one-call capability, never a retry cache. Every
            # retry must re-fetch server truth and re-run vc-guard before the
            # native boundary can be reached again.
            self._contexts.pop(event.key, None)


def _parse_action_result(value: object) -> ActionResult | None:
    """Validate and coerce a raw resume-adapter reply into an ActionResult.

    Enforces the accepted/retryable/terminal exclusivity contract; returns
    None (fail-closed, treated as a retry) on any shape violation.
    """
    if not isinstance(value, Mapping):
        return None
    accepted = value.get("accepted")
    retryable = value.get("retryable")
    terminal = value.get("terminal")
    reason = value.get("reason")
    if (
        type(accepted) is not bool
        or type(retryable) is not bool
        or type(terminal) is not bool
        or not isinstance(reason, str)
        or not reason
        or len(reason.encode("utf-8")) > MAX_STATE_TEXT_BYTES
    ):
        return None
    if accepted:
        if retryable or not terminal:
            return None
    elif retryable == terminal:
        return None
    return ActionResult(
        accepted=accepted,
        retryable=retryable,
        terminal=terminal,
        reason=reason,
    )


class GuardianWorker:
    """Long-lived SSE consumer with durable dedupe and injected side effects."""

    def __init__(
        self,
        *,
        server_url: str,
        state: GuardianState,
        notifier: Notifier = notify_operator,
        reconciler: Reconciler = fail_closed_reconcile,
        resume: ResumeCallback | None = None,
        opener: UrlOpener = urllib.request.urlopen,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        replay_heartbeats: int = DEFAULT_REPLAY_HEARTBEATS,
        ready_callback: ReadyCallback | None = None,
        pending_pass_limit: int = DEFAULT_PENDING_PASS_LIMIT,
        board_publisher: BoardPublisher = _ignore_board_publish,
        triage_scheduler: TriageScheduler = _ignore_triage_schedule,
        clock: Callable[[], float] = time.time,
        cursor_parser: CursorParser = _parse_event_cursor,
        control_parser: ControlParser | None = parse_stream_control,
    ) -> None:
        """Bind server URL, durable state, and all injected side-effect callbacks."""
        self.server_url = _validate_server_url(server_url)
        self.state = state
        self.notifier = notifier
        self.reconciler = reconciler
        self.resume = resume
        self.opener = opener
        if connect_timeout <= 0:
            raise ValueError("connect timeout must be > 0")
        if replay_heartbeats <= 0:
            raise ValueError("replay heartbeat count must be > 0")
        if pending_pass_limit <= 0:
            raise ValueError("pending pass limit must be > 0")
        self.connect_timeout = connect_timeout
        self.replay_heartbeats = replay_heartbeats
        self.ready_callback = ready_callback
        self.pending_pass_limit = pending_pass_limit
        self.board_publisher = board_publisher
        self.triage_scheduler = triage_scheduler
        self.clock = clock
        self.cursor_parser = cursor_parser
        self.control_parser = control_parser
        self._ready_announced = False

    def _publish_board_safely(self) -> None:
        """Refresh the rail projection without owning or blocking settlement."""

        try:
            self.board_publisher()
        except Exception:
            LOGGER.exception("guardian server settlement-board publication failed")

    def _request(self) -> urllib.request.Request:
        """Build the SSE GET request, resuming from the persisted cursor when known."""
        # A fresh numeric zero is not sent: omitting it lets a v2 server choose
        # its generation-aware start cursor. Persisted opaque cursors are safe
        # across rotation; numeric cursors remain compatibility-only.
        cursor = self.state.cursor
        url = f"{self.server_url}/api/control/events"
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        if _is_v2_cursor(cursor) or self.state.baseline_complete:
            query = urllib.parse.urlencode({"since": str(cursor)})
            url = f"{url}?{query}"
            headers["Last-Event-ID"] = str(cursor)
        return urllib.request.Request(
            url,
            headers=headers,
        )

    @staticmethod
    def _validate_response(response: Any) -> None:
        """Raise GuardianProtocolError unless the response is a live SSE stream."""
        status = getattr(response, "status", 200)
        if status != 200:
            raise GuardianProtocolError(f"SSE endpoint returned HTTP {status}")
        headers = getattr(response, "headers", {})
        content_type = str(headers.get("Content-Type", "") or "")
        if _base_media_type(content_type) != "text/event-stream":
            raise GuardianProtocolError(
                f"SSE endpoint returned unexpected content type {content_type!r}"
            )

    def consume_connection(self) -> ConnectionStats:
        """Consume one SSE connection until the server disconnects."""

        stats = ConnectionStats()
        latest_cursor: CursorToken | None = None
        connection_caught_up = False
        try:
            response = self.opener(self._request(), timeout=self.connect_timeout)
            with contextlib.closing(response):
                self._validate_response(response)
                if not self._ready_announced and self.ready_callback is not None:
                    self.ready_callback()
                    self._ready_announced = True
                for item in iter_sse(
                    response,
                    cursor_parser=self.cursor_parser,
                    control_parser=self.control_parser,
                ):
                    if isinstance(item, SSEStreamBoundary):
                        latest_cursor = item.cursor
                        if self.state.baseline_complete:
                            # A boundary is orientation, not authority. Persisting
                            # its target before the following gap/caught-up control
                            # could skip the very gap that must revoke old pending.
                            continue
                        try:
                            self.state.checkpoint(item.cursor)
                        except (OSError, GuardianStateError):
                            LOGGER.exception(
                                "guardian could not persist stream boundary %s",
                                item.cursor,
                            )
                        continue

                    if isinstance(item, SSEStreamGap):
                        latest_cursor = item.cursor
                        self._publish_board_safely()
                        try:
                            self.state.reset_for_gap(
                                item.cursor,
                                reason=item.reason,
                            )
                            LOGGER.critical(
                                "guardian stream gap for %s; resume authority "
                                "revoked until fresh caught-up at %s",
                                item.requested,
                                item.cursor,
                            )
                        except (OSError, GuardianStateError):
                            LOGGER.exception(
                                "guardian could not persist stream gap %s",
                                item.cursor,
                            )
                        continue

                    if isinstance(item, SSEStreamCaughtUp):
                        latest_cursor = item.cursor
                        self._publish_board_safely()
                        try:
                            if self.state.complete_baseline(
                                item.cursor,
                                authoritative=item.authoritative,
                            ):
                                stats.completed_baseline = True
                                LOGGER.info(
                                    "guardian baseline caught up at %s (%s)",
                                    item.cursor,
                                    (
                                        "resume-authoritative"
                                        if item.authoritative
                                        else "legacy notification-only"
                                    ),
                                )
                            connection_caught_up = True
                            self._drain_due_pending(stats)
                        except (OSError, GuardianStateError):
                            LOGGER.exception(
                                "guardian could not persist caught-up receipt %s",
                                item.cursor,
                            )
                        continue

                    if isinstance(item, SSEHeartbeat):
                        stats.heartbeats += 1
                        if stats.heartbeats >= self.replay_heartbeats:
                            # Reattachment is only a liveness refresh. Baseline
                            # authority comes from stream.caught-up, never ping.
                            return stats
                        continue

                    if isinstance(item, SSEControlFrame):
                        LOGGER.debug(
                            "guardian ignored opaque SSE control frame %s",
                            item.event,
                        )
                        continue

                    stats.frames += 1
                    event = parse_settlement_revision(item.data)
                    if event is None:
                        try:
                            if _declares_settlement_event(item.data):
                                if self.state.quarantine(item.cursor, item.data):
                                    LOGGER.critical(
                                        "guardian quarantined invalid settlement frame "
                                        "at SSE cursor %s",
                                        item.cursor,
                                    )
                            elif not self.state.baseline_complete:
                                self.state.checkpoint(item.cursor)
                            latest_cursor = item.cursor
                        except (OSError, GuardianStateError):
                            LOGGER.exception(
                                "guardian could not persist invalid frame evidence at %s",
                                item.cursor,
                            )
                        continue

                    try:
                        triage_scheduled = self.triage_scheduler(event.run_id)
                    except Exception:
                        triage_scheduled = False
                        LOGGER.exception(
                            "guardian could not schedule terminal triage for %s",
                            event.run_id,
                        )
                    if not triage_scheduled:
                        # The durable stream cursor is still the previous frame.
                        # Reattachment will replay this settlement after local
                        # outbox capacity or persistence recovers.
                        stats.action_failures += 1
                        LOGGER.error(
                            "guardian paused settlement consumption before %s r%s "
                            "because terminal triage was not durably queued",
                            event.run_id,
                            event.revision,
                        )
                        return stats

                    # Projection publication is independent of settlement
                    # recovery and notification. A broken viewer must never
                    # block the durable Guardian cursor.
                    self._publish_board_safely()
                    baseline_was_complete = self.state.baseline_complete
                    try:
                        claimed = (
                            self.state.claim(item.cursor, event)
                            if baseline_was_complete
                            else self.state.suppress(item.cursor, event.key)
                        )
                    except (OSError, GuardianStateError):
                        LOGGER.exception(
                            "guardian could not persist settlement %s r%s",
                            event.run_id,
                            event.revision,
                        )
                        stats.action_failures += 1
                        # Do not consume later frames after a failed durable
                        # claim: their cursors would jump over this settlement.
                        return stats
                    latest_cursor = item.cursor
                    if not claimed:
                        continue
                    stats.claimed += 1
                    if not baseline_was_complete:
                        LOGGER.debug(
                            "guardian suppressed baseline settlement %s r%s",
                            event.run_id,
                            event.revision,
                        )
                        continue
                    record = self.state.pending.get(event.key)
                    if not connection_caught_up:
                        continue
                    if record is not None and self._attempt_record_safely(record):
                        stats.completed_actions += 1
                    else:
                        stats.action_failures += 1
            return stats
        finally:
            # Settlement claims and controls persist their own cursor. This
            # final checkpoint covers non-settlement traffic once per socket.
            if (
                self.state.baseline_complete
                and connection_caught_up
                and latest_cursor is not None
            ):
                try:
                    self.state.checkpoint(latest_cursor)
                except (OSError, GuardianStateError):
                    LOGGER.exception(
                        "guardian could not persist final SSE cursor %s",
                        latest_cursor,
                    )

    def _drain_due_pending(self, stats: ConnectionStats) -> None:
        """Advance due outbox records only behind this connection's SSE barrier."""

        for pending in self.state.pending_records(
            now=self.clock(),
            limit=self.pending_pass_limit,
        ):
            stats.claimed += 1
            if self._attempt_record_safely(pending):
                stats.completed_actions += 1
            else:
                stats.action_failures += 1

    def _retry_record(self, record: PendingRecord, reason: str) -> bool:
        """Persist bounded retry state; return true only on terminal exhaustion."""

        return self.state.retry(
            record.key,
            reason=reason,
            now=self.clock(),
            bounded=True,
        )

    def _attempt_record_safely(self, record: PendingRecord) -> bool:
        """Run `_attempt_record`, converting a persistence failure into a retry signal."""
        try:
            return self._attempt_record(record)
        except (OSError, GuardianStateError):
            LOGGER.exception(
                "guardian could not persist pending action for %s r%s",
                record.event.run_id,
                record.event.revision,
            )
            return False

    def _attempt_record(self, record: PendingRecord) -> bool:
        """Advance one durable outbox record by at most one external attempt."""

        event = record.event
        if not record.notification_done:
            try:
                self.notifier(notification_for(event))
            except Exception as exc:
                LOGGER.exception(
                    "guardian notifier failed for %s r%s",
                    event.run_id,
                    event.revision,
                )
                return self._retry_record(
                    record,
                    f"notifier_exception:{type(exc).__name__}",
                )
            self.state.mark_notified(event.key)
            current = self.state.pending.get(event.key)
            if current is None:
                return True
            record = current

        if not record.resume_authorized:
            return self.state.complete(
                event.key,
                outcome="terminal",
                reason="legacy_notification_only",
            )

        try:
            decision = self.reconciler(event)
        except Exception as exc:
            LOGGER.exception(
                "guardian reconcile failed for %s r%s",
                event.run_id,
                event.revision,
            )
            return self._retry_record(
                record,
                f"reconcile_exception:{type(exc).__name__}",
            )
        if not isinstance(decision, ReconcileDecision):
            LOGGER.error(
                "guardian reconcile returned invalid decision for %s r%s",
                event.run_id,
                event.revision,
            )
            return self._retry_record(record, "invalid_reconcile_decision")
        if not decision.request_resume:
            return self.state.complete(
                event.key,
                outcome="terminal",
                reason=decision.reason,
            )

        # Hard policy boundary: failed/invalid runs never resume. Finalized runs
        # have nothing to resume. Only needs-attention may reach the adapter.
        if event.tui != TUI_NEEDS_ATTENTION:
            LOGGER.warning(
                "guardian denied resume for %s settlement %s",
                event.tui,
                event.run_id,
            )
            return self.state.complete(
                event.key,
                outcome="terminal",
                reason=f"resume_denied_for_{event.tui}",
            )
        if self.resume is None:
            LOGGER.warning(
                "guardian resume requested for %s but adapter is unavailable",
                event.run_id,
            )
            return self._retry_record(record, "resume_adapter_unavailable")
        try:
            raw_result = self.resume(event, event.idempotency_key)
        except Exception as exc:
            LOGGER.exception("guardian resume adapter failed for %s", event.run_id)
            return self._retry_record(
                record,
                f"resume_exception:{type(exc).__name__}",
            )
        result = _parse_action_result(raw_result)
        if result is None:
            LOGGER.error(
                "guardian resume adapter returned invalid result for %s",
                event.run_id,
            )
            return self._retry_record(record, "invalid_resume_result")
        if result.accepted:
            return self.state.complete(
                event.key,
                outcome="accepted",
                reason=result.reason,
            )
        if result.retryable:
            LOGGER.warning(
                "guardian resume adapter deferred %s (%s): %s",
                event.run_id,
                event.idempotency_key,
                result.reason,
            )
            return self._retry_record(record, result.reason)
        return self.state.complete(
            event.key,
            outcome="terminal",
            reason=result.reason,
        )

    def run_forever(
        self,
        *,
        backoff: BoundedBackoff | None = None,
        sleep: Callable[[float], None] = time.sleep,
        stop_requested: Callable[[], bool] = lambda: False,
        max_connections: int | None = None,
    ) -> None:
        """Reconnect with bounded backoff; never enter a zero-delay spin."""

        retry = backoff or BoundedBackoff()
        attempts = 0
        while not stop_requested():
            stats: ConnectionStats | None = None
            try:
                stats = self.consume_connection()
            except (
                GuardianProtocolError,
                OSError,
                TimeoutError,
                urllib.error.URLError,
            ) as exc:
                LOGGER.warning("guardian SSE disconnected: %s", exc)
            attempts += 1
            if stats is not None and stats.proved_stable:
                retry.reset()
            if stop_requested() or (
                max_connections is not None and attempts >= max_connections
            ):
                return
            sleep(retry.next_delay())


def _validate_lock_descriptor(path: Path, descriptor: int) -> None:
    """Raise GuardianLockSecurityError unless `descriptor` is a private, unshared,
    still-linked regular file matching `path` (defends against symlink/TOCTOU races)."""
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise GuardianLockSecurityError(f"unsafe guardian lock descriptor: {path}")
    try:
        path_metadata = path.lstat()
    except OSError as exc:
        raise GuardianLockSecurityError(
            f"guardian lock path disappeared after open: {path}"
        ) from exc
    if (
        not stat.S_ISREG(path_metadata.st_mode)
        or path_metadata.st_uid != os.getuid()
        or path_metadata.st_nlink != 1
        or stat.S_IMODE(path_metadata.st_mode) != 0o600
        or path_metadata.st_dev != metadata.st_dev
        or path_metadata.st_ino != metadata.st_ino
    ):
        raise GuardianLockSecurityError(
            f"guardian lock path no longer names the opened inode: {path}"
        )
    if not fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
        raise GuardianLockSecurityError(f"guardian lock is not close-on-exec: {path}")


@contextlib.contextmanager
def single_instance_lock(path: Path) -> Iterator[None]:
    """Own one validated, non-following process lock for the guardian lifetime."""

    _ensure_private_directory(path.parent)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise GuardianLockSecurityError(
                f"guardian lock target is a symlink: {path}"
            ) from exc
        raise
    locked = False
    try:
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        fcntl.fcntl(descriptor, fcntl.F_SETFD, descriptor_flags | fcntl.FD_CLOEXEC)
        _validate_lock_descriptor(path, descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            raise GuardianAlreadyRunning(
                f"guardian lock is already held: {path}"
            ) from exc
        _validate_lock_descriptor(path, descriptor)
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def default_state_path() -> Path:
    """Default path for the guardian's durable cursor/outbox state file."""
    return vibecrafted_home() / "control_plane" / "guardian" / "state.json"


def default_lock_path() -> Path:
    """Default path for the guardian's single-instance lock file."""
    return vibecrafted_home() / "control_plane" / "guardian" / "guardian.lock"


def default_server_url() -> str:
    """Resolve the observer from an explicit process override or typed config."""
    return (
        os.environ.get("VC_SERVER_URL")
        or os.environ.get("VIBECRAFTED_SERVER_URL")
        or load_server_config().public_url
    )


def write_ready_receipt(
    path: Path,
    *,
    nonce: str,
    server_url: str,
    pid: int | None = None,
) -> Path:
    """Atomically prove that this process owns the lock and accepted SSE."""

    if not nonce:
        raise ValueError("guardian readiness nonce must not be empty")
    return atomic_write_json(
        path,
        {
            "schema": GUARDIAN_READY_SCHEMA,
            "nonce": nonce,
            "pid": os.getpid() if pid is None else pid,
            "server_url": _validate_server_url(server_url),
        },
    )


def remove_ready_receipt_if_owned(
    path: Path,
    *,
    nonce: str,
    pid: int | None = None,
) -> bool:
    """Remove only the receipt written by this exact guardian invocation."""

    expected_pid = os.getpid() if pid is None else pid
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or (
        payload.get("schema") != GUARDIAN_READY_SCHEMA
        or payload.get("nonce") != nonce
        or payload.get("pid") != expected_pid
    ):
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    """Build the guardian CLI argument parser (server URL, state/lock paths, timing)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server-url",
        default=default_server_url(),
        help="vibecrafted-server origin (default: %(default)s)",
    )
    parser.add_argument("--state", type=Path, default=default_state_path())
    parser.add_argument("--lock", type=Path, default=default_lock_path())
    parser.add_argument(
        "--ready-file",
        type=Path,
        help="atomic launcher readiness receipt (requires --ready-nonce)",
    )
    parser.add_argument(
        "--ready-nonce",
        help="launcher nonce copied into --ready-file after a valid SSE response",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--recovery-timeout",
        type=float,
        default=DEFAULT_RECOVERY_TIMEOUT_SECONDS,
        help="timeout for the exact run-projection truth check",
    )
    parser.add_argument(
        "--backoff-initial",
        type=float,
        default=DEFAULT_BACKOFF_INITIAL_SECONDS,
    )
    parser.add_argument(
        "--backoff-max",
        type=float,
        default=DEFAULT_BACKOFF_MAX_SECONDS,
    )
    parser.add_argument(
        "--replay-heartbeats",
        type=int,
        default=DEFAULT_REPLAY_HEARTBEATS,
        help="refresh the SSE attachment after this many idle heartbeats",
    )
    parser.add_argument(
        "--no-desktop",
        action="store_true",
        help="log f/x/n notifications without macOS Notification Center",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def _recover_pending_trust_before_attach() -> None:
    """Run one bounded trust outbox sweep before accepting live SSE work."""

    from .trust import recover_pending_trust_settlements

    report = recover_pending_trust_settlements()
    for recovered in report.recovered:
        LOGGER.info(
            "recovered pending trust settlement %s r%s (%s)",
            recovered.run_id,
            recovered.settlement_revision,
            recovered.receipt_id,
        )
    for error in report.errors:
        LOGGER.critical(
            "pending trust settlement recovery failed for %s (%s): %s",
            error.run_id or error.outbox_path,
            error.error_type,
            error.message,
        )
    if report.truncated:
        LOGGER.critical(
            "pending trust settlement recovery hit its bounded limit after "
            "%s outboxes; remaining outboxes stay durable for the next start",
            report.scanned,
        )
    if report.ok:
        LOGGER.info(
            "pending trust settlement recovery complete: scanned=%s recovered=%s",
            report.scanned,
            len(report.recovered),
        )


def _recover_untriaged_runs_background() -> None:
    """Recover durable jobs, then sweep dispatcher-orphaned terminal runs."""

    from .run_triage import reconcile_untriaged_runs

    _recover_terminal_triage_outbox()
    control_plane = vibecrafted_home() / "control_plane"
    try:
        report = reconcile_untriaged_runs(
            control_plane,
            attempt_limit=TERMINAL_TRIAGE_OUTBOX_ATTEMPT_LIMIT,
            scan_limit=TERMINAL_TRIAGE_OUTBOX_SCAN_LIMIT,
        )
    except Exception:
        LOGGER.exception(
            "terminal triage startup reconciliation could not scan %s",
            control_plane,
        )
        return
    for item in report.errors:
        LOGGER.error(
            "terminal triage recovery failed for %s: %s",
            item.meta_path,
            item.reason,
        )
    LOGGER.info(
        "terminal triage recovery complete: scanned=%s attempted=%s "
        "errors=%s truncated=%s",
        report.scanned,
        report.attempted,
        len(report.errors),
        report.truncated,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: load state, wire the recovery adapter, and run forever."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    state = GuardianState.load(args.state)
    if (args.ready_file is None) != (args.ready_nonce is None):
        parser.error("--ready-file and --ready-nonce must be provided together")

    def notifier(notification: GuardianNotification) -> None:
        """CLI Notifier: forward to `notify_operator` honoring `--no-desktop`."""
        notify_operator(notification, desktop=not args.no_desktop)

    ready_callback: ReadyCallback | None = None
    if args.ready_file is not None and args.ready_nonce is not None:

        def announce_ready() -> None:
            """ReadyCallback: write the launcher readiness receipt for this CLI run."""
            write_ready_receipt(
                args.ready_file,
                nonce=args.ready_nonce,
                server_url=args.server_url,
            )

        ready_callback = announce_ready

    try:
        recovery = GuardianRecoveryAdapter(
            server_url=args.server_url,
            timeout=args.recovery_timeout,
        )
        settlement_board = SettlementBoardPublisher(server_url=args.server_url)
        worker = GuardianWorker(
            server_url=args.server_url,
            state=state,
            notifier=notifier,
            reconciler=recovery.reconcile,
            resume=recovery.resume,
            connect_timeout=args.connect_timeout,
            replay_heartbeats=args.replay_heartbeats,
            ready_callback=ready_callback,
            board_publisher=settlement_board.request_refresh,
            triage_scheduler=_schedule_terminal_triage_run,
        )
        backoff = BoundedBackoff(args.backoff_initial, args.backoff_max)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        with single_instance_lock(args.lock):
            try:
                _recover_pending_trust_before_attach()
                if not _schedule_triage_startup_sweep():
                    LOGGER.error(
                        "terminal triage startup sweep could not be queued; "
                        "periodic durable recovery remains active"
                    )
                settlement_board.start_periodic_refresh()
                LOGGER.info(
                    "guardian attaching to %s/api/control/events; "
                    "guarded native recovery adapter active",
                    worker.server_url,
                )
                worker.run_forever(backoff=backoff)
            finally:
                settlement_board.stop_periodic_refresh()
                if args.ready_file is not None and args.ready_nonce is not None:
                    remove_ready_receipt_if_owned(
                        args.ready_file,
                        nonce=args.ready_nonce,
                    )
    except GuardianAlreadyRunning as exc:
        LOGGER.error("%s", exc)
        return 75
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
