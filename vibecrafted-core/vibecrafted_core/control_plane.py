"""Vibecrafted control plane: projects on-disk run artifacts (agent meta, locks,
marbles state, event stream) into durable per-run snapshots, and provides the
sync/lookup/await/liveness surface every dispatch verb reads and writes through."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import errno
import fcntl
import gzip
import json
import os
import re
import shutil
import stat
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .clock import utc_now
from .delivery.model import (
    ContractError,
    DeliveryProofContract,
    DeliverySeal,
    DeliveryState,
    ExecutionState,
    ProofResult,
    ProofState,
)
from .report_contract import (
    CLAIM_BLOCKED,
    CLAIM_COMPLETED,
    CLAIM_FAILED,
    CLAIM_PARTIAL,
    validate_report_file,
)
from .run_mutation import run_mutation_locks
from .runtime_paths import vibecrafted_home
from .settlement import (
    SETTLEMENT_EVENT_KIND,
    board_fxn_counts,
    can_archive,
    emit_settlement_event,
    orphan_settlement_payloads,
    persist_await_verdict,
    persist_settlement_to_meta,
    prepare_settlement_event,
    settle_payload,
    settlement_from_payload,
)

ACTIVE_STATES = {
    "created",
    "process_spawned",
    "first_output_seen",
    "active",
    "artifact_seen",
    "report_started",
    "initialized",
    "launching",
    "promise",
    "confirmed",
    "running",
    "paused",
    "stalled",
}
FINAL_STATES = {
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
    "quota_exhausted",
    "gc",
    "ghost",
}
BLOCKED_STATES = {
    "blocked",
    "stalled",
    "report_missing",
    "report_invalid",
    "contract_failed",
    "recovery_required",
}
SETTLEMENT_PROJECTION_FIELDS = (
    "settlement_verdict",
    "settlement_reason",
    "settlement_at",
    "settlement_source",
    "settlement_tui",
    "settlement_waived",
    "settlement_claim_digest",
    "settlement_revision",
    "settlement",
    "await_rc",
    "await_outcome",
    "await_reason",
    "await_worker_alive",
    "await_settled_at",
)
SKILL_CODE_MAP = {
    "agnt": "agents",
    "deco": "decorate",
    "delg": "delegate",
    "vdou": "dou",
    "fwup": "followup",
    "hydr": "hydrate",
    "impl": "implement",
    "init": "init",
    "just": "justdo",
    "marb": "marbles",
    "prtn": "partner",
    "plan": "plan",
    "prun": "prune",
    "rels": "release",
    "rsch": "research",
    "rvew": "review",
    "scaf": "scaffold",
    "wflw": "workflow",
}
RUN_STALL_SECONDS = 20 * 60
LIVENESS_STALE_HEARTBEAT_SECONDS = 120
LIVENESS_STALE_HEARTBEAT_ENV = "VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS"
RUN_GC_GRACE_SECONDS = 6 * 60 * 60
RUN_GC_GRACE_ENV = "VIBECRAFTED_RUN_GC_GRACE_SECONDS"
RUN_SNAPSHOT_RETENTION_SECONDS = 7 * 24 * 60 * 60
RUN_SNAPSHOT_RETENTION_SECONDS_ENV = "VIBECRAFTED_RUN_SNAPSHOT_RETENTION_SECONDS"
RUN_SNAPSHOT_RETENTION_COUNT = 2000
RUN_SNAPSHOT_RETENTION_COUNT_ENV = "VIBECRAFTED_RUN_SNAPSHOT_RETENTION_COUNT"
EVENT_TAIL_LIMIT = 16
EVENTS_ROTATE_BYTES = 32 * 1024 * 1024
EVENTS_ROTATE_BYTES_ENV = "VIBECRAFTED_EVENTS_ROTATE_BYTES"
EVENTS_ARCHIVE_MAX_FILES = 64
EVENTS_ARCHIVE_MAX_FILES_ENV = "VIBECRAFTED_EVENTS_ARCHIVE_MAX_FILES"
EVENTS_ARCHIVE_MAX_BYTES = 512 * 1024 * 1024
EVENTS_ARCHIVE_MAX_BYTES_ENV = "VIBECRAFTED_EVENTS_ARCHIVE_MAX_BYTES"
RUNTIME_TRANSCRIPT_MAX_BYTES = 16 * 1024 * 1024
RUNTIME_TRANSCRIPT_MAX_BYTES_ENV = "VIBECRAFTED_RUNTIME_TRANSCRIPT_MAX_BYTES"
RUNTIME_TRANSCRIPT_COLD_TAIL_BYTES = 64 * 1024
RUNTIME_TRANSCRIPT_COLD_TAIL_BYTES_ENV = (
    "VIBECRAFTED_RUNTIME_TRANSCRIPT_COLD_TAIL_BYTES"
)
RUNTIME_TMP_MAX_AGE_SECONDS = 60 * 60
CONTROL_PLANE_WRITE_RESERVE_BYTES = 1024 * 1024
EVENT_SEGMENT_SCHEMA = "vibecrafted.event-stream-segment.v1"
EVENT_MAX_LINE_BYTES = 256 * 1024
RECENT_RUN_LIMIT = 12
MISSING_SESSION_IDS = {"", "pending", "none", "null", "unknown"}


@dataclass(frozen=True)
class RunStatus:
    """Normalized view of one run, merged from whichever on-disk source (agent
    meta, lock file, marbles state, event stream) currently describes it."""

    run_id: str
    state: str
    agent: str
    skill: str
    mode: str
    root: str
    operator_session: str
    latest_report: str
    latest_transcript: str
    last_error: str
    updated_at: str
    started_at: str
    health: str
    source: str
    lock_present: bool
    exit_code: int | None = None
    liveness: str = ""
    launcher_pid: int | None = None
    completed_at: str = ""
    session_id: str = ""
    current_loop: int | None = None
    total_loops: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    """One control-plane event as returned to :func:`subscribe_events` callers."""

    ts: str
    run_id: str
    kind: str
    message: str
    payload: dict[str, Any]
    cursor: str


@dataclass(frozen=True)
class _EventSegment:
    """One event-stream generation (active or archived) and its read cursor math."""

    path: Path
    epoch: str
    generation: int
    data_start: int
    size: int
    active: bool
    modified_ns: int

    def cursor(self, offset: int) -> str:
        """Build the opaque ``v2:<epoch>:<generation>:<offset>`` resume cursor."""
        return f"v2:{self.epoch}:{self.generation}:{max(offset, 0)}"


@dataclass(frozen=True)
class DeliveryAxes:
    """Read-only projection of execution, proof, and delivery state."""

    execution_state: ExecutionState
    proof_state: ProofState
    delivery_state: DeliveryState

    def to_payload(self) -> dict[str, str]:
        """Flatten the three axes to their string values for snapshot storage."""
        return {
            "execution_state": self.execution_state.value,
            "proof_state": self.proof_state.value,
            "delivery_state": self.delivery_state.value,
        }


def control_plane_home() -> Path:
    """Root directory of the control plane under VIBECRAFTED_HOME."""
    return vibecrafted_home() / "control_plane"


def run_snapshot_dir() -> Path:
    """Directory holding one JSON snapshot per run id."""
    return control_plane_home() / "runs"


def event_stream_path() -> Path:
    """Path to the active (current-generation) event stream file."""
    return control_plane_home() / "events.jsonl"


def _event_lock_path() -> Path:
    """Path to the lock file coordinating event-stream appends and rotation."""
    return control_plane_home() / ".events.lock"


def _sync_lock_path() -> Path:
    """Path to the lock file guarding the global (unscoped) board rebuild."""
    return control_plane_home() / ".sync.lock"


class ControlPlaneLockBusy(RuntimeError):
    """The shared control-plane lock could not be acquired inside the budget.

    Raised instead of blocking forever. A single global ``flock(LOCK_EX)`` with
    no timeout was the root cause of the "empty dispatcher, 2-minute wait, false
    stalled/pid_gone" migraine: under many concurrent runs every mutation queued
    on one mutex and a lock-starved-but-live worker could not even record that it
    was alive. Failing loud here turns an invisible infinite hang into a
    diagnosable error the operator can act on.
    """


_SYNC_LOCK_POLL_SECONDS = 0.05
_DEFAULT_SYNC_LOCK_TIMEOUT_SECONDS = 15.0


def _sync_lock_timeout_seconds() -> float:
    """Configured budget for acquiring the global sync lock (env-overridable)."""
    raw = os.environ.get("VIBECRAFTED_SYNC_LOCK_TIMEOUT_S")
    if raw:
        try:
            return max(float(raw), 0.0)
        except ValueError:
            pass
    return _DEFAULT_SYNC_LOCK_TIMEOUT_SECONDS


class ControlPlaneStorageError(RuntimeError):
    """A control-plane write cannot be made safely with available storage."""


def _storage_free_bytes(path: Path) -> int:
    return int(shutil.disk_usage(path).free)


def _ensure_storage_capacity(path: Path, required_bytes: int) -> None:
    required = max(int(required_bytes), 0) + CONTROL_PLANE_WRITE_RESERVE_BYTES
    try:
        free = _storage_free_bytes(path.parent)
    except OSError:
        return
    if free < required:
        raise ControlPlaneStorageError(
            "control-plane degraded: insufficient disk space for atomic write "
            f"at {path} (need {required} bytes including reserve, {free} free). "
            "Free disk space, then run `vibecrafted control-plane sync`."
        )


@contextlib.contextmanager
def _event_lock(*, exclusive: bool) -> Iterator[None]:
    """Coordinate event appends with generation rotation.

    Lock order is ``_sync_lock`` then ``_event_lock`` whenever both are held.
    Appenders take the exclusive lock only around tail repair, one bounded
    ``O_APPEND`` write, and fsync. Rotation takes the same exclusive lock, so no
    writer can open the old inode after it has been archived. Readers may take
    the shared lock without ever touching the global sync lock.

    The lock file is deliberately stable across rotations. ``O_NOFOLLOW`` plus
    owner/mode checks prevent a replaced symlink or another user's writable
    file from becoming the synchronization authority.
    """

    home = control_plane_home()
    home.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(_event_lock_path(), flags, 0o600)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(errno.EINVAL, "event lock is not a regular file")
        if metadata.st_uid != os.getuid():
            raise PermissionError("event lock is not owned by the current user")
        if metadata.st_mode & 0o022:
            raise PermissionError("event lock must not be group/world writable")
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(fd, operation)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _sync_lock(
    *, timeout: float | None = None, purpose: str = "sync"
) -> Iterator[None]:
    """Bounded, non-blocking exclusive lock over the shared control-plane files.

    Never blocks indefinitely: acquires with ``LOCK_NB`` in a short poll loop up
    to ``timeout`` seconds, then raises :class:`ControlPlaneLockBusy`.

    DOCTRINE (enforced by ``tests/test_control_plane_lock_doctrine.py`` — do not
    weaken): this GLOBAL lock guards ONLY the full (unscoped) board rebuild in
    ``sync_state``. It must NEVER wrap a per-run path or the append/emit path.
    Run snapshots use the independent ``run_mutation_locks`` key for their own
    run id; event appends use the dedicated event lock around one atomic
    ``O_APPEND`` write. Re-serializing either hot path on this shared mutex is
    the exact regression that caused the 2026-07-12 flock migraine (empty
    dispatchers, false stalled/pid_gone).
    """
    control_plane_home().mkdir(parents=True, exist_ok=True)
    lock_path = _sync_lock_path()
    budget = _sync_lock_timeout_seconds() if timeout is None else max(timeout, 0.0)
    deadline = time.monotonic() + budget
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                    raise
                if time.monotonic() >= deadline:
                    raise ControlPlaneLockBusy(
                        f"control-plane {purpose} lock busy for > {budget:.0f}s "
                        f"({lock_path}). Another process is holding it — usually a "
                        f"full board rebuild (install/doctor sync in progress; retry "
                        f"shortly) or a stuck run holding the lock."
                    ) from exc
                time.sleep(_SYNC_LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> dict[str, Any]:
    """Load a JSON object from disk; {} on any read/parse failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` as JSON via a tmp-file + atomic rename (crash-safe)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    _ensure_storage_capacity(path, len(data.encode("utf-8")))
    # Atomic write (tmp + os.replace) so a crash mid-write or a concurrent
    # reader never sees a half-written, unparseable snapshot — _read_json would
    # silently degrade a truncated file to {} and lose the run's metadata.
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise ControlPlaneStorageError(
                "control-plane degraded: disk became full while atomically writing "
                f"{path}. Free disk space, then run `vibecrafted control-plane sync`."
            ) from exc
        raise
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _write_json_durable(path: Path, payload: dict[str, Any]) -> None:
    """Atomically publish JSON and durably commit both data and directory entry."""

    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _ensure_storage_capacity(path, len(data))
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    descriptor = -1
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    try:
        descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        written = os.write(descriptor, data)
        if written != len(data):
            raise OSError(errno.EIO, f"short write to {tmp}")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(tmp, path)
        _fsync_directory_durable(path.parent)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise ControlPlaneStorageError(
                "control-plane degraded: disk became full while durably writing "
                f"{path}. Free disk space, then run `vibecrafted control-plane sync`."
            ) from exc
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            tmp.unlink()


def _fsync_directory_durable(path: Path) -> None:
    """Fsync a directory or raise; authority receipts cannot be best-effort."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_run_snapshot(
    path: Path,
    previous: dict[str, Any] | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """CAS one run projection, its runtime meta, and its settlement event.

    Projection work intentionally happens outside this critical section so
    different runs never herd behind one global mutex. The exact snapshot used
    to derive the candidate is compared again after the per-run lock is held.
    A stale writer loses without rebasing stale evidence into a newer verdict.
    """

    run_id = str(payload.get("run_id") or path.stem).strip()
    if not run_id:
        return {}
    root = control_plane_home()
    with run_mutation_locks(root, run_id=run_id):
        current_payload = _read_json(path)
        current = (
            current_payload
            if str(current_payload.get("run_id") or "") == run_id
            else None
        )
        expected = previous if previous else None
        if current != expected:
            return dict(current or {})

        # Per-run snapshots are projections, never f/x/n history authorities.
        # Scrub the short-lived 052c embedded ledger on the next successful CAS.
        payload.pop("settlement_history", None)
        event = prepare_settlement_event(run_id, current, payload)
        revision = _coerce_int(payload.get("settlement_revision"))
        nested = payload.get("settlement")
        if revision is not None and isinstance(nested, dict):
            nested["revision"] = revision

        settlement = settlement_from_payload(payload)
        runtime_meta = _runtime_run_dir(run_id) / "meta.json"
        if settlement is not None and runtime_meta.is_file():
            if revision is None or revision <= 0:
                return dict(current or {})
            if not persist_settlement_to_meta(
                runtime_meta,
                settlement,
                control_plane_root=root,
                run_id=run_id,
                revision=revision,
            ):
                return dict(current or {})

        _write_json(path, payload)
        _record_transition(current, payload)
        if event is not None:
            emit_settlement_event(event)
        return payload


def _read_lines(path: Path) -> list[str]:
    """Read a text file as a list of lines; [] on any OSError."""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _parse_kv_file(path: Path) -> dict[str, str]:
    """Parse a flat ``key=value`` per-line lock file into a dict."""
    payload: dict[str, str] = {}
    for line in _read_lines(path):
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip()
    return payload


def _safe_iso(raw: str | None) -> str:
    """Normalize a possibly-None ISO timestamp to a plain string (never None)."""
    return raw or ""


def _parse_iso(raw: str | None) -> dt.datetime | None:
    """Parse an ISO 8601 (``Z``-suffixed or offset) timestamp; None if invalid/empty."""
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _coerce_int(value: Any) -> int | None:
    """Best-effort int coercion; rejects bools and non-numeric strings (returns None)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value = value.strip()
        if value and value.lstrip("-").isdigit():
            return int(value)
    return None


def normalize_run_root(
    root: str | Path | None, fallback: str | Path | None = None
) -> str:
    """Resolve a run's declared root (or ``fallback``) to a canonical absolute path."""
    raw = str(root or fallback or "").strip()
    if not raw:
        return ""
    return str(Path(raw).expanduser().resolve())


def _is_pytest_temp_path(raw: Any) -> bool:
    """True when a path lives under a pytest ``tmp_path`` fixture directory."""
    value = str(raw or "").strip()
    if not value:
        return False
    return any(part.startswith("pytest-of-") for part in Path(value).parts)


def _event_has_test_provenance(event: dict[str, Any]) -> bool:
    """Reject pytest-owned event records from a production control plane.

    Pytest fixtures legitimately use an isolated temporary VIBECRAFTED_HOME, so
    quarantine is disabled inside that sandbox. The production home must never
    replay an event whose declared root/artifact paths belong to a pytest temp
    tree into the operator board.
    """

    if _is_pytest_temp_path(vibecrafted_home()):
        return False
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    return any(
        _is_pytest_temp_path(payload.get(key))
        for key in ("root", "source_dir", "report", "transcript", "meta")
    )


def ensure_session_id(session_id: Any = "") -> str:
    """Return ``session_id`` if it looks real, else mint a fresh uuid4 string."""
    raw = str(session_id or "").strip()
    if raw.lower() not in MISSING_SESSION_IDS:
        return raw
    return str(uuid.uuid4())


def _now() -> dt.datetime:
    """Control-plane "now" — read from :mod:`vibecrafted_core.clock`."""
    return utc_now()


def _configured_stale_heartbeat_seconds() -> int:
    """Idle-heartbeat threshold before a run is considered stalled/dead."""
    return _configured_nonnegative_int(
        LIVENESS_STALE_HEARTBEAT_ENV, LIVENESS_STALE_HEARTBEAT_SECONDS
    )


def _configured_run_gc_grace_seconds() -> int:
    """Grace period past staleness before a dead run is eligible for gc."""
    return _configured_nonnegative_int(RUN_GC_GRACE_ENV, RUN_GC_GRACE_SECONDS)


def _configured_snapshot_retention_seconds() -> int:
    """Max age of a terminal snapshot before it is archived."""
    return _configured_nonnegative_int(
        RUN_SNAPSHOT_RETENTION_SECONDS_ENV, RUN_SNAPSHOT_RETENTION_SECONDS
    )


def _configured_snapshot_retention_count() -> int:
    """Max count of retained terminal snapshots before the oldest are archived."""
    return _configured_nonnegative_int(
        RUN_SNAPSHOT_RETENTION_COUNT_ENV, RUN_SNAPSHOT_RETENTION_COUNT
    )


def _configured_events_rotate_bytes() -> int:
    """Byte size threshold at which the active event stream rotates."""
    return _configured_nonnegative_int(EVENTS_ROTATE_BYTES_ENV, EVENTS_ROTATE_BYTES)


def _configured_events_archive_max_files() -> int:
    """Max retained archived event-stream generations."""
    return _configured_nonnegative_int(
        EVENTS_ARCHIVE_MAX_FILES_ENV, EVENTS_ARCHIVE_MAX_FILES
    )


def _configured_events_archive_max_bytes() -> int:
    """Max total bytes retained across archived event-stream generations."""
    return _configured_nonnegative_int(
        EVENTS_ARCHIVE_MAX_BYTES_ENV, EVENTS_ARCHIVE_MAX_BYTES
    )


def _configured_runtime_transcript_max_bytes() -> int:
    return _configured_nonnegative_int(
        RUNTIME_TRANSCRIPT_MAX_BYTES_ENV, RUNTIME_TRANSCRIPT_MAX_BYTES
    )


def _configured_runtime_transcript_cold_tail_bytes() -> int:
    return _configured_nonnegative_int(
        RUNTIME_TRANSCRIPT_COLD_TAIL_BYTES_ENV,
        RUNTIME_TRANSCRIPT_COLD_TAIL_BYTES,
    )


def _runtime_runs_dir() -> Path:
    return control_plane_home() / "runtime_runs"


def _runtime_run_is_terminal(run_dir: Path) -> bool:
    payload = _read_json(run_dir / "meta.json")
    state = str(payload.get("state") or payload.get("status") or "")
    return state in FINAL_STATES or _coerce_int(payload.get("exit_code")) is not None


def _maintain_runtime_run_storage() -> dict[str, int]:
    """Bound terminal transcripts and remove stale interrupted-write debris.

    Live transcripts are never replaced under an open writer. Once a run is
    terminal, oversized transcripts keep the configured tail. Terminal logs
    older than the snapshot retention window keep only a small readable cold
    tail even when each file is individually below the size cap. Removed
    prefixes are gzip-archived; archives age out after another retention window.
    """

    root = _runtime_runs_dir()
    counts = {
        "orphan_temps_removed": 0,
        "transcripts_rotated": 0,
        "archives_removed": 0,
    }
    if not root.is_dir():
        return counts

    now = time.time()
    for temp in root.rglob("*.tmp.*"):
        try:
            if (
                temp.is_file()
                and now - temp.stat().st_mtime >= RUNTIME_TMP_MAX_AGE_SECONDS
            ):
                temp.unlink()
                counts["orphan_temps_removed"] += 1
        except OSError:
            continue
    for temp in run_snapshot_dir().glob("*.tmp.*"):
        try:
            if (
                temp.is_file()
                and now - temp.stat().st_mtime >= RUNTIME_TMP_MAX_AGE_SECONDS
            ):
                temp.unlink()
                counts["orphan_temps_removed"] += 1
        except OSError:
            continue

    max_bytes = _configured_runtime_transcript_max_bytes()
    cold_tail_bytes = _configured_runtime_transcript_cold_tail_bytes()
    retention = _configured_snapshot_retention_seconds()
    for run_dir in root.iterdir():
        if not run_dir.is_dir() or run_dir.name == "archive":
            continue
        archive = run_dir / "transcript.log.archive.gz"
        try:
            if archive.is_file() and now - archive.stat().st_mtime >= retention:
                archive.unlink()
                counts["archives_removed"] += 1
        except OSError:
            pass
        transcript = run_dir / "transcript.log"
        try:
            if not transcript.is_file() or not _runtime_run_is_terminal(run_dir):
                continue
            transcript_stat = transcript.stat()
            size = transcript_stat.st_size
            target_bytes = max_bytes
            if retention <= 0 or now - transcript_stat.st_mtime >= retention:
                target_bytes = min(target_bytes, cold_tail_bytes)
            if target_bytes <= 0 or size <= target_bytes:
                continue
            prefix_bytes = size - target_bytes
            with transcript.open("rb") as source:
                prefix = source.read(prefix_bytes)
                tail = source.read()
            _ensure_storage_capacity(archive, len(prefix) + len(tail))
            with gzip.open(archive, "ab") as compressed:
                compressed.write(prefix)
            replacement = transcript.with_name(
                f"{transcript.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
            )
            try:
                replacement.write_bytes(tail)
                os.replace(replacement, transcript)
            finally:
                with contextlib.suppress(OSError):
                    replacement.unlink()
            counts["transcripts_rotated"] += 1
        except (OSError, ControlPlaneStorageError):
            # Maintenance must not block projection; the next explicit write
            # still fails with an actionable degraded-mode error if disk is full.
            continue
    return counts


def _configured_nonnegative_int(env_name: str, default: int) -> int:
    """Read a non-negative int from an env var, falling back to ``default`` on
    a missing or unparsable value."""
    raw = os.environ.get(env_name)
    if not raw:
        return default
    try:
        return max(int(raw), 0)
    except ValueError:
        return default


def _state_health(state: str, updated_at: str) -> str:
    """Derive a coarse health label ("final"/"stalled"/"unknown"/"active") from
    lifecycle state and last-update recency."""
    updated_dt = _parse_iso(updated_at)
    if state in FINAL_STATES:
        return "final"
    if state == "stalled":
        return "stalled"
    if updated_dt is None:
        return "unknown"
    if (_now() - updated_dt).total_seconds() > RUN_STALL_SECONDS:
        return "stalled"
    return "active"


def _operator_state(run: dict[str, Any]) -> str:
    """Coarse operator-facing verdict ("stopped"/"blocked"/"failed"/"completed"/
    "running") derived from state, artifact gate, and exit code."""
    state = str(run.get("state") or "")
    artifact_ok = run.get("artifact_ok")
    artifact_errors = list(run.get("artifact_errors") or [])
    exit_code = _coerce_int(run.get("exit_code"))

    if state == "stopped":
        return "stopped"
    if state in BLOCKED_STATES:
        return "blocked"
    if state in {"failed", "process_dead", "ghost"}:
        return "failed"
    if exit_code not in (None, 0):
        return "failed"
    if state in {"report_validated", "completed", "closed"}:
        if artifact_ok is False or artifact_errors:
            return "blocked"
        return "completed"
    if artifact_ok is False or artifact_errors:
        return "blocked"
    return "running"


def _accepted_operator_stop_payload(run: dict[str, Any] | None) -> bool:
    """True when a run's own record is an operator-accepted stop, not just any 'stopped'."""
    return bool(
        isinstance(run, dict)
        and str(run.get("state") or "") == "stopped"
        and run.get("operator_stop_accepted") is True
    )


def _artifact_projection(
    run: dict[str, Any], previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Derive the artifact contract fields (ok/errors/gate, transcript growth,
    heartbeat) for one run, honoring an accepted operator stop as evidence-exempt
    and deferring the report gate while a run is still actively identity-bound."""
    report = str(run.get("latest_report") or "")
    transcript = str(run.get("latest_transcript") or "")
    state = str(run.get("state") or "")
    result = dict(run)
    errors = [str(item) for item in (run.get("artifact_errors") or []) if str(item)]
    artifact_ok = run.get("artifact_ok")
    terminal_state = state in {"report_validated", "completed", "closed"}
    operator_stopped = _accepted_operator_stop_payload(run)
    # Report absence is not a failure while execution has fresh proof of life.
    # Session identity may legitimately arrive after the first lifecycle/meta
    # write, but a stale/dead `pid_alive` string must not defer the gate forever.
    activity_age = _activity_age_seconds(run, _now())
    defer_report_gate = (
        state in ACTIVE_STATES
        and activity_age is not None
        and activity_age < _configured_stale_heartbeat_seconds()
    )

    if operator_stopped:
        # Completion evidence is intentionally not owed after an operator stop.
        # Later supervisor artifact errors remain observable in the event log,
        # but cannot turn accepted terminal intent into report_missing/failed.
        errors = []
        artifact_ok = True
    elif report and not defer_report_gate:
        report_path = Path(report)
        try:
            if not report_path.exists():
                if "report_missing" not in errors:
                    errors.append("report_missing")
            elif report_path.stat().st_size == 0 and "report_invalid" not in errors:
                errors.append("report_invalid")
        except OSError:
            if "report_invalid" not in errors:
                errors.append("report_invalid")
    elif terminal_state:
        if "report_missing" not in errors:
            errors.append("report_missing")

    transcript_bytes: int | None = None
    transcript_growth: int | None = None
    if transcript:
        transcript_path = Path(transcript)
        try:
            if transcript_path.exists():
                transcript_bytes = transcript_path.stat().st_size
                previous_bytes = _coerce_int((previous or {}).get("transcript_bytes"))
                if previous_bytes is not None:
                    transcript_growth = max(transcript_bytes - previous_bytes, 0)
                else:
                    transcript_growth = transcript_bytes
        except OSError:
            transcript_bytes = None
            transcript_growth = None

    if artifact_ok is None:
        artifact_ok = len(errors) == 0
    else:
        artifact_ok = bool(artifact_ok) and len(errors) == 0

    if operator_stopped:
        gate = "stopped"
    elif errors:
        gate = "failed"
    elif state in {"report_validated", "completed", "closed"}:
        gate = "validated"
    else:
        gate = "pending"

    result["artifact_ok"] = artifact_ok
    result["artifact_errors"] = errors
    result["artifact_gate"] = gate
    result["transcript_bytes"] = transcript_bytes
    result["transcript_growth"] = transcript_growth
    result["heartbeat_at"] = str(run.get("heartbeat_at") or run.get("updated_at") or "")
    if operator_stopped:
        result["health"] = "final"
        result["liveness"] = str(result.get("liveness") or "terminal")
        result["recovery_required"] = False
        result["last_error"] = ""
    elif terminal_state and artifact_ok and not errors:
        if _coerce_int(result.get("exit_code")) is None:
            result["exit_code"] = 0
        if not str(result.get("completed_at") or ""):
            result["completed_at"] = str(result.get("updated_at") or _now().isoformat())
        result["liveness"] = "terminal"
        result.pop("recovery_required", None)
    result["operator_state"] = _operator_state(result)
    return result


def _pid_is_alive(pid: int) -> bool:
    """Signal-0 liveness probe for a pid; ``EPERM`` counts as alive, ``ESRCH`` as dead."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.EPERM:
            return True
        if exc.errno == errno.ESRCH:
            return False
        return False
    return True


def _heartbeat_age_seconds(run: dict[str, Any], now: dt.datetime) -> float | None:
    """Seconds since the run's heartbeat (or updated_at fallback) stamp; None if unparsable."""
    heartbeat_at = _parse_iso(
        str(run.get("heartbeat_at") or run.get("updated_at") or "")
    )
    if heartbeat_at is None:
        return None
    if heartbeat_at.tzinfo is None:
        heartbeat_at = heartbeat_at.replace(tzinfo=dt.timezone.utc)
    return (now - heartbeat_at).total_seconds()


def _transcript_age_seconds(run: dict[str, Any], now: dt.datetime) -> float | None:
    """Seconds since the transcript last grew, or None when there is none."""
    transcript = str(run.get("latest_transcript") or "").strip()
    if not transcript:
        return None
    try:
        mtime = Path(transcript).stat().st_mtime
    except OSError:
        return None
    stamped = dt.datetime.fromtimestamp(mtime, dt.timezone.utc)
    return max((now - stamped).total_seconds(), 0.0)


def _activity_age_seconds(run: dict[str, Any], now: dt.datetime) -> float | None:
    """Age of the freshest proof of life, not merely of the heartbeat stamp.

    Two reasons the heartbeat alone lies. It is stamped once, when the first
    output arrives, and never refreshed for the rest of the run, so on its own
    every run older than the threshold reads as stale. And a worker sitting
    inside a ten-minute ``cargo build`` is silent in the token stream while
    being entirely alive — silence during a tool call is not death.

    The transcript keeps a file mtime that moves whenever the worker actually
    speaks, so take whichever signal is fresher.
    """
    ages = [
        age
        for age in (
            _heartbeat_age_seconds(run, now),
            _transcript_age_seconds(run, now),
        )
        if age is not None
    ]
    return min(ages) if ages else None


def _freshest_activity_stamp(*stamps: str, transcript: str = "") -> str:
    """Newest of the run's liveness clocks, as an ISO stamp.

    The health clock used ``heartbeat_at`` alone, and that stamp is written
    once — at first output — so a run doing real work for longer than
    ``RUN_STALL_SECONDS`` aged itself into ``stalled`` health while its worker
    was still talking. The transcript mtime moves whenever the worker actually
    speaks, so it is the honest floor under the frozen heartbeat.
    """
    candidates = list(stamps)
    if transcript:
        try:
            mtime = Path(transcript).stat().st_mtime
        except OSError:
            pass
        else:
            candidates.append(
                dt.datetime.fromtimestamp(mtime, dt.timezone.utc).isoformat()
            )
    best: dt.datetime | None = None
    best_raw = ""
    for raw in candidates:
        parsed = _parse_iso(str(raw or ""))
        if parsed is None:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        if best is None or parsed > best:
            best = parsed
            best_raw = str(raw)
    return best_raw


def _append_last_error(previous: str, explanation: str, *, limit: int = 4) -> str:
    """Append a watchdog note without letting it rewrite itself forever.

    The reconciler runs on every projection, so a parked run accumulated a
    dozen near-identical copies of the same sentence, differing only in the
    stale-seconds count. Collapse same-shape notes and keep the chain bounded.
    """
    explanation = str(explanation or "").strip()
    if not explanation:
        return str(previous or "").strip()
    shape = re.sub(r"\d+", "#", explanation)
    parts = [
        part.strip()
        for part in str(previous or "").split(";")
        if part.strip() and re.sub(r"\d+", "#", part.strip()) != shape
    ]
    parts.append(explanation)
    return "; ".join(parts[-limit:])


def _reconcile_dead_launcher(run: dict[str, Any]) -> dict[str, Any]:
    """Settle a run whose launcher/worker evidence disagrees with its recorded state.

    Prefers live worker process evidence over the ephemeral launcher pid; only
    transitions to stalled/failed/gc/report_missing/report_invalid once the
    worker is demonstrably gone and stale past the configured thresholds. See
    the inline P0 comment below for why a dead launcher pid alone is never proof.
    """
    result = dict(run)
    state = str(result.get("state") or "")
    launcher_pid = _coerce_int(result.get("launcher_pid"))
    liveness = str(result.get("liveness") or "")
    if state in FINAL_STATES:
        return result
    owner_pid = _coerce_int(result.get("owner_pid"))
    if owner_pid is not None:
        owner_alive = _pid_is_alive(owner_pid)
        provider_alive = any(
            _pid_is_alive(pid)
            for pid in (
                _coerce_int(result.get("worker_pid")),
                _coerce_int(result.get("worker_pgid")),
            )
            if pid is not None
        )
        if owner_alive and provider_alive:
            return result
        now = _now().isoformat()
        result["state"] = "failed"
        result["health"] = "final"
        result["liveness"] = "pid_gone"
        result["completed_at"] = str(result.get("completed_at") or now)
        result["updated_at"] = now
        result["exit_code"] = _coerce_int(result.get("exit_code")) or 1
        result["terminal_reason"] = (
            "owner_pid_gone" if not owner_alive else "provider_pid_gone"
        )
        result["recovery_required"] = True
        result["last_error"] = _append_last_error(
            str(result.get("last_error") or ""),
            "interactive lifecycle owner/provider identity is no longer live",
        )
        return result
    # P0: a dead/absent launcher pid is NOT proof the run died. The launcher is an
    # ephemeral spawn-shell that exits right after forking the detached dispatcher;
    # for headless/detached dispatch it is gone within seconds while the dispatcher
    # and worker keep running and DELIVER. If the worker (or its process group) is
    # still alive, the run is live regardless of the launcher pid, exit_code, or
    # stale terminal-looking metadata — reconciling it to success/stalled/recovery
    # here makes await capable of rc=0 on a live worker.
    if _worker_is_alive(result):
        return result
    if _has_success_evidence(result):
        return _reconcile_successful_terminal(result)

    exit_code = _coerce_int(result.get("exit_code"))
    # Field 2026-07-22 (Pensieve scaffold): `_run_is_terminal` is True whenever
    # exit_code is set, which previously short-circuited reconcile while leaving
    # state=`active`/`running`. Board stayed "active" + pid_gone; await could
    # disagree with projection. Normalize dead worker + nonzero exit → failed
    # *before* the terminal early-return.
    if exit_code is not None and exit_code != 0 and state in ACTIVE_STATES - {"paused"}:
        now = _now()
        result["state"] = "failed"
        result["health"] = "final"
        result["liveness"] = "pid_gone"
        result["completed_at"] = str(result.get("completed_at") or now.isoformat())
        result["updated_at"] = now.isoformat()
        result["recovery_required"] = True
        previous_error = str(result.get("last_error") or "").strip()
        explanation = (
            f"worker dead with exit_code={exit_code}; settled active→failed "
            f"(pid_gone immediate settle)"
        )
        result["last_error"] = (
            f"{previous_error}; {explanation}" if previous_error else explanation
        )
        return result

    if _run_is_terminal(result):
        return result
    if state not in ACTIVE_STATES - {"paused"}:
        return result
    if liveness == "pid_alive":
        if launcher_pid is None or _pid_is_alive(launcher_pid):
            return result
    else:
        if launcher_pid is not None and _pid_is_alive(launcher_pid):
            return result
        if launcher_pid is None and _has_signal_target(result):
            return result

    now = _now()
    age_seconds = _activity_age_seconds(result, now)
    threshold = _configured_stale_heartbeat_seconds()
    if age_seconds is None or age_seconds < threshold:
        return result

    artifact_errors = {
        str(item) for item in (result.get("artifact_errors") or []) if str(item)
    }
    artifact_failure_state = ""
    if "report_missing" in artifact_errors:
        artifact_failure_state = "report_missing"
    elif artifact_errors.intersection({"report_invalid", "report_empty"}):
        artifact_failure_state = "report_invalid"

    gc_grace = _configured_run_gc_grace_seconds()
    past_gc_grace = not artifact_failure_state and age_seconds >= gc_grace
    # Settlement precedes gc (contract §7): a collector must never erase a run
    # that has no settlement terminal. Past grace without settlement parks as
    # needs_attention (TUI n) instead of silent gc.
    if past_gc_grace:
        # Terminalize so settle_payload can classify; verdict is applied in
        # _project_run_payload. State stays visible as stalled-with-n until
        # settlement is written, then may become gc once settled.
        result["state"] = "gc"
        result["health"] = "final"
        result["liveness"] = "pid_gone"
        result["completed_at"] = now.isoformat()
        result["settlement_park"] = "gc_blocked_until_settled"
    elif artifact_failure_state:
        result["state"] = artifact_failure_state
        result["health"] = "final"
        result["liveness"] = "pid_gone"
        result["recovery_required"] = True
    else:
        result["state"] = "stalled"
        result["health"] = "stalled"
        result["liveness"] = "pid_gone"
        result["recovery_required"] = True
    result["updated_at"] = now.isoformat()
    pid_detail = (
        f"launcher_pid {launcher_pid} is not alive"
        if launcher_pid is not None
        else "launcher_pid is missing"
    )
    lock_detail = (
        "; lock file remains present for operator cleanup"
        if result.get("lock_present")
        else ""
    )
    artifact_detail = (
        f"; artifact contract failed ({artifact_failure_state})"
        if artifact_failure_state
        else ""
    )
    if past_gc_grace:
        explanation = (
            f"garbage-collected: dead launcher, no activity >{gc_grace}s; "
            f"{pid_detail}; no worker activity for {int(age_seconds)}s "
            f"(threshold {threshold}s); no live launcher proof{lock_detail}; "
            f"settlement parks as needs_attention before archive"
        )
    else:
        explanation = (
            f"{pid_detail}; no worker activity for {int(age_seconds)}s "
            f"(threshold {threshold}s); no live launcher proof{lock_detail}"
            f"{artifact_detail}; recovery_required"
        )
    result["last_error"] = _append_last_error(
        str(result.get("last_error") or ""), explanation
    )
    return result


def _report_attests_completion(run: dict[str, Any]) -> bool:
    """True when the delivered report carries the worker's own success attestation.

    ``finalized: true`` plus a non-empty ``claim`` is the deliberate self-attest
    the report contract defines — the very signal the operator reads off the
    frontmatter. It had no edge into the control plane: a run could finalize its
    report, land its commit and exit, while the record stayed ``stalled``
    forever because exit_code and completed_at were never recorded. The
    attestation IS the terminal proof; treat it as such.
    """
    report = str(run.get("latest_report") or "").strip()
    if not report:
        return False
    verdict = validate_report_file(report, require_frontmatter=False)
    if "report_missing" in verdict.errors:
        return False
    if not verdict.finalized or not verdict.claim:
        return False
    claim_status = verdict.claim_status
    if claim_status in CLAIM_COMPLETED:
        return True
    # A recognized negative/incomplete claim deliberately overrides the
    # top-level lifecycle status. An unrecognized evidence adjective (the live
    # report used ``verified``) must not erase an explicit ``status: completed``.
    if claim_status in CLAIM_FAILED | CLAIM_BLOCKED | CLAIM_PARTIAL:
        return False
    return str(verdict.fields.get("status") or "").strip().lower() in CLAIM_COMPLETED


def _has_success_evidence(run: dict[str, Any]) -> bool:
    """True when any independent signal (state, exit 0, completed_at, terminal
    liveness, or a self-attesting report) proves the run finished successfully."""
    if run.get("artifact_ok") is False or run.get("artifact_errors"):
        return False
    exit_code = _coerce_int(run.get("exit_code"))
    if exit_code not in (None, 0):
        return False
    state = str(run.get("state") or "")
    if state in {"report_validated", "completed", "closed", "converged"}:
        return True
    if exit_code == 0:
        return True
    if str(run.get("completed_at") or "").strip():
        return True
    if str(run.get("liveness") or "") == "terminal":
        return True
    return _report_attests_completion(run)


def _reconcile_successful_terminal(run: dict[str, Any]) -> dict[str, Any]:
    """Close a run that has success evidence, including after a stale watchdog stamp.

    The heartbeat watchdog can set ``last_error`` / ``recovery_required`` while a
    worker is still delivering. Once exit 0 / completed_at / terminal liveness is
    present, those recovery marks are stale noise — clear them so the failure
    board and action queue do not treat a green run as investigate-fodder.
    """
    result = dict(run)
    completed_at = str(result.get("completed_at") or result.get("updated_at") or "")
    if not completed_at:
        completed_at = _now().isoformat()
    result["state"] = "completed"
    result["health"] = "final"
    result["liveness"] = "terminal"
    result["completed_at"] = completed_at
    result["updated_at"] = completed_at
    result.pop("recovery_required", None)
    # Empty string matches _reconcile_repaired_report_terminal; consumers treat
    # blank last_error as absent.
    result["last_error"] = ""
    return result


def _reconcile_repaired_report_terminal(run: dict[str, Any]) -> dict[str, Any]:
    """Heal a report contract terminal after its artifact was repaired.

    A successful worker can only recover from report_missing/report_invalid.
    Execution failures and proof/delivery invalidation remain terminal failures.
    """
    result = dict(run)
    if str(result.get("state") or "") not in {"report_invalid", "report_missing"}:
        return result
    if _coerce_int(result.get("exit_code")) != 0:
        return result
    if str(result.get("proof_state") or "").lower() in {"failed", "invalid"}:
        return result
    if str(result.get("delivery_state") or "").lower() == "invalidated":
        return result

    report = str(result.get("latest_report") or result.get("report") or "").strip()
    if not report:
        return result
    from .report_contract import validate_report_file

    validation = validate_report_file(report, require_frontmatter=True)
    if not validation.ok:
        return result
    if (
        str(validation.fields.get("run_id") or "").strip()
        != str(result.get("run_id") or "").strip()
    ):
        return result
    errors = [str(item) for item in (result.get("artifact_errors") or []) if str(item)]
    if any(not item.startswith("report_") for item in errors):
        return result

    completed_at = str(result.get("completed_at") or result.get("updated_at") or "")
    result["state"] = "completed"
    result["health"] = "final"
    result["liveness"] = "terminal"
    result["completed_at"] = completed_at or _now().isoformat()
    result["artifact_ok"] = True
    result["artifact_errors"] = []
    result["artifact_gate"] = "validated"
    result["operator_state"] = "completed"
    result["last_error"] = ""
    result.pop("recovery_required", None)
    return result


def _failure_card(run: dict[str, Any]) -> dict[str, Any] | None:
    """Build an operator-facing failure summary when the run has artifact errors
    or sits in a blocked state; None when there is nothing to report."""
    errors = [str(item) for item in (run.get("artifact_errors") or []) if str(item)]
    state = str(run.get("state") or "")
    if not errors and state not in BLOCKED_STATES:
        return None

    return {
        "code": "runtime_contract_failure",
        "summary": "Artifact contract or lifecycle precondition failed.",
        "state": state,
        "run_id": run.get("run_id"),
        "artifact_errors": errors,
        "report": run.get("latest_report") or "",
        "transcript": run.get("latest_transcript") or "",
        "last_error": run.get("last_error") or "",
        "recovery": [
            "Inspect events tail for the run.",
            "Validate report/transcript paths and rerun or retry.",
            "Use vc_run_stop before retry when liveness remains active.",
        ],
    }


def _has_signal_target(run: dict[str, Any]) -> bool:
    """True when the run carries any pid/pgid value a stop/cancel could signal."""
    for key in ("worker_pgid", "worker_pid", "launcher_pid"):
        raw = run.get(key)
        if _coerce_int(raw):
            return True
    return False


def _worker_is_alive(run: dict[str, Any]) -> bool:
    """True when the run's worker process (or its group leader) is still alive.

    Distinct from ``_has_signal_target`` (which only checks that a pid VALUE is
    present): this checks actual OS liveness. The liveness reconciler uses it so
    a run is never marked recovery_required merely because the ephemeral launcher
    pid died while the worker keeps running and delivering.
    """
    provider_alive = False
    for key in ("worker_pid", "worker_pgid"):
        pid = _coerce_int(run.get(key))
        if pid is not None and _pid_is_alive(pid):
            provider_alive = True
            break
    owner_pid = _coerce_int(run.get("owner_pid"))
    if owner_pid is not None:
        return provider_alive and _pid_is_alive(owner_pid)
    return provider_alive


def _await_process_is_alive(run: dict[str, Any]) -> bool:
    """True while either the worker or its launcher is still finalizing.

    Launchers are intentionally ignored by the general liveness reconciler: a
    dead ephemeral launcher must not invalidate a worker that continues on its
    own.  Await has a narrower obligation.  It must not seal a delivered report
    while a live launcher can still write the terminal metadata and projection.
    """
    if _worker_is_alive(run):
        return True
    launcher_pid = _coerce_int(run.get("launcher_pid"))
    return launcher_pid is not None and _pid_is_alive(launcher_pid)


def _has_retry_spec(run: dict[str, Any]) -> bool:
    """True when the run carries enough state (marbles skill, or prompt/file) to resume."""
    skill = str(run.get("skill") or "")
    if skill == "marbles":
        return True
    return bool(
        str(run.get("prompt") or "").strip() or str(run.get("file") or "").strip()
    )


def _lifecycle_controls(run: dict[str, Any]) -> dict[str, bool]:
    """Compute the operator action-availability flags (await/inspect/stop/cancel/
    resume/recovery_required) for one run's current projection."""
    terminal = _run_is_terminal(run)
    liveness = str(run.get("liveness") or "")
    signalable = (
        not terminal
        and liveness not in {"pid_gone", "terminal"}
        and _has_signal_target(run)
    )
    recovery_required = (
        bool(run.get("recovery_required"))
        or liveness == "pid_gone"
        or str(run.get("state") or "") in BLOCKED_STATES
    )
    return {
        "await": not terminal,
        "inspect": True,
        "stop": signalable,
        "cancel": signalable,
        "resume": terminal and _has_retry_spec(run),
        "recovery_required": recovery_required,
    }


def _session_base_name(root: str) -> str:
    """Slugify a repo root's basename into a safe vc-frame session-name stem."""
    base = Path(root or "vibecrafted").name.lower()
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in base).strip("-")
    return cleaned or "vibecrafted"


def operator_session_name(root: str, run_id: str) -> str:
    """Deterministic vc-frame session name for a run: ``<root-slug>-<run_id>``."""
    base = _session_base_name(root)
    return f"{base}-{run_id}" if run_id else base


def _skill_from_code(skill_code: str) -> str:
    """Expand a 4-letter launcher skill code to its full skill name."""
    return SKILL_CODE_MAP.get(skill_code, skill_code or "unknown")


def _segment_header(epoch: str, generation: int) -> dict[str, Any]:
    """Build the JSON header record written at the start of every event segment."""
    return {
        "ts": _now().isoformat(),
        "run_id": "",
        "kind": "stream.segment",
        "message": f"event stream generation {generation}",
        "payload": {
            "schema": EVENT_SEGMENT_SCHEMA,
            "epoch": epoch,
            "generation": generation,
        },
    }


def _parse_segment_header(raw: bytes) -> tuple[str, int] | None:
    """Validate and parse one raw line as a segment header; None if malformed."""
    if not raw.endswith(b"\n") or len(raw) > EVENT_MAX_LINE_BYTES:
        return None
    try:
        header = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    payload = header.get("payload")
    if (
        header.get("kind") != "stream.segment"
        or not isinstance(payload, dict)
        or payload.get("schema") != EVENT_SEGMENT_SCHEMA
    ):
        return None
    epoch = str(payload.get("epoch") or "").strip()
    generation = payload.get("generation")
    if not epoch or ":" in epoch or type(generation) is not int or generation < 0:
        return None
    return epoch, generation


def _read_segment_header(path: Path) -> tuple[str, int] | None:
    """Read and parse the first line of an event-stream file as its segment header."""
    try:
        with path.open("rb") as handle:
            raw = handle.readline(EVENT_MAX_LINE_BYTES + 1)
    except OSError:
        return None
    return _parse_segment_header(raw)


def _read_event_segment(path: Path, *, active: bool) -> _EventSegment | None:
    """Capture one segment header and metadata from the same open inode."""

    try:
        with path.open("rb") as handle:
            raw = handle.readline(EVENT_MAX_LINE_BYTES + 1)
            metadata = os.fstat(handle.fileno())
    except OSError:
        return None
    header = _parse_segment_header(raw)
    if header is None:
        return None
    return _EventSegment(
        path=path,
        epoch=header[0],
        generation=header[1],
        data_start=len(raw),
        size=metadata.st_size,
        active=active,
        modified_ns=metadata.st_mtime_ns,
    )


def _parse_event_cursor(
    raw: str | int | None,
) -> tuple[str | None, int | None, int] | None:
    """Parse a cursor into (epoch, generation, offset); epoch/generation are
    None for legacy numeric cursors. None return means the cursor is invalid."""
    value = "0" if raw is None else str(raw).strip()
    if value.isdigit():
        return None, None, int(value)
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != "v2":
        return None
    epoch = parts[1]
    if not epoch or re.fullmatch(r"[A-Za-z0-9_.-]+", epoch) is None:
        return None
    try:
        generation = int(parts[2])
        offset = int(parts[3])
    except ValueError:
        return None
    if generation < 0 or offset < 0:
        return None
    return epoch, generation, offset


def _last_complete_event_offset(path: Path, minimum: int) -> int:
    """Byte offset of the end of the last complete (newline-terminated) record,
    never below ``minimum``."""
    try:
        with path.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size <= minimum:
                return min(size, minimum)
            handle.seek(size - 1)
            if handle.read(1) == b"\n":
                return size
            end = size
            while end > minimum:
                start = max(end - 8192, minimum)
                handle.seek(start)
                chunk = handle.read(end - start)
                position = chunk.rfind(b"\n")
                if position >= 0:
                    return start + position + 1
                end = start
    except OSError:
        return minimum
    return minimum


def _event_segments() -> list[_EventSegment]:
    """All readable event-stream segments (archived + active), oldest first."""
    segments: list[_EventSegment] = []
    archive_dir = _events_archive_dir()
    if archive_dir.is_dir():
        for path in archive_dir.glob("events-*.jsonl"):
            segment = _read_event_segment(path, active=False)
            if segment is not None:
                segments.append(segment)
    active = _read_event_segment(event_stream_path(), active=True)
    if active is not None:
        segments.append(active)
    segments.sort(
        key=lambda segment: (
            segment.active,
            segment.modified_ns,
            segment.epoch,
            segment.generation,
        )
    )
    return segments


def _active_event_resume_cursor() -> str:
    """Cursor pointing at the end of the currently active event segment."""
    stream = event_stream_path()
    segment = _read_event_segment(stream, active=True)
    if segment is not None:
        return segment.cursor(_last_complete_event_offset(stream, segment.data_start))
    return str(_last_complete_event_offset(stream, 0))


def _fsync_directory(path: Path) -> None:
    """Best-effort fsync of a directory's metadata (silently no-ops on failure)."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new_event_segment_locked(path: Path, epoch: str, generation: int) -> None:
    """Atomically publish an fsynced empty segment. Caller holds event EX."""

    line = (
        json.dumps(_segment_header(epoch, generation), ensure_ascii=False) + "\n"
    ).encode("utf-8")
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        written = os.write(fd, line)
        if written != len(line):
            raise OSError(errno.EIO, "short event segment header write")
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(tmp, path)
        _fsync_directory(path.parent)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _latest_archived_segment_header() -> tuple[str, int] | None:
    """(epoch, generation) of the most recently modified archived segment, if any."""
    candidates: list[tuple[int, str, int]] = []
    archive_dir = _events_archive_dir()
    if not archive_dir.is_dir():
        return None
    for path in archive_dir.glob("events-*.jsonl"):
        header = _read_segment_header(path)
        if header is None:
            continue
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        candidates.append((modified, header[0], header[1]))
    if not candidates:
        return None
    _, epoch, generation = max(candidates)
    return epoch, generation


def _legacy_archive_target() -> Path:
    """Timestamped archive path for a headerless legacy event stream being moved aside."""
    archive_dir = _events_archive_dir()
    stamp = _now().strftime("%Y%m%dT%H%M%S%fZ")
    return archive_dir / f"events-legacy-{stamp}-{uuid.uuid4().hex}.jsonl"


def _ensure_event_segment() -> None:
    """Ensure the active stream has a v1 header without racing appenders."""

    stream_path = event_stream_path()
    with _event_lock(exclusive=False):
        header = _read_segment_header(stream_path)
        if header is not None:
            return
        try:
            if not stream_path.exists() or stream_path.stat().st_size == 0:
                pass
            else:
                # A non-empty legacy stream must be moved, never rewritten:
                # prepending a header would invalidate every saved byte cursor.
                header = None
        except OSError:
            pass

    with _event_lock(exclusive=True):
        if _read_segment_header(stream_path) is not None:
            return
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = False
        try:
            legacy = stream_path.is_file() and stream_path.stat().st_size > 0
        except OSError:
            legacy = False
        if legacy:
            archive_dir = _events_archive_dir()
            archive_dir.mkdir(parents=True, exist_ok=True)
            stream_path.replace(_legacy_archive_target())
            _fsync_directory(archive_dir)
            _fsync_directory(stream_path.parent)
            epoch, generation = str(uuid.uuid4()), 0
        else:
            previous = _latest_archived_segment_header()
            if previous is None:
                epoch, generation = str(uuid.uuid4()), 0
            else:
                epoch, generation = previous[0], previous[1] + 1
        _write_new_event_segment_locked(stream_path, epoch, generation)
        _prune_event_archives_locked()


def _repair_incomplete_event_tail_locked(fd: int) -> int:
    """Drop only an unterminated final record while the event lock is exclusive."""

    end = os.lseek(fd, 0, os.SEEK_END)
    if end == 0 or os.pread(fd, 1, end - 1) == b"\n":
        return end

    scan_size = min(end, EVENT_MAX_LINE_BYTES + 1)
    tail = os.pread(fd, scan_size, end - scan_size)
    newline = tail.rfind(b"\n")
    if newline < 0:
        raise OSError(errno.EIO, "event segment has no complete record boundary")
    repaired_end = end - scan_size + newline + 1
    os.ftruncate(fd, repaired_end)
    os.fsync(fd)
    return repaired_end


def _append_event(event: dict[str, Any]) -> None:
    """Append one durable event line without taking the global sync lock.

    Appending to events.jsonl is the hottest control-plane path (every spawn /
    emit / stop of every run). It never acquires the global sync lock. The
    dedicated event lock makes the write/rollback boundary exclusive to
    appenders and rotation. ``fsync`` is the receipt: once this call returns,
    the complete newline-delimited effect is on the durable segment.
    """
    _ensure_event_segment()
    stream_path = event_stream_path()
    line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    if len(line) > EVENT_MAX_LINE_BYTES:
        raise ValueError(
            f"event line exceeds {EVENT_MAX_LINE_BYTES} byte stream contract"
        )
    with _event_lock(exclusive=True):
        flags = os.O_RDWR | os.O_APPEND
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(stream_path, flags)
        try:
            start = _repair_incomplete_event_tail_locked(fd)
            written = os.write(fd, line)
            if written != len(line):
                os.ftruncate(fd, start)
                os.fsync(fd)
                raise OSError(errno.EIO, "short atomic event append")
            os.fsync(fd)
        finally:
            os.close(fd)


def _iter_meta_files() -> Iterator[Path]:
    """All ``*.meta.json`` launcher artifact files under artifacts/."""
    artifacts_root = vibecrafted_home() / "artifacts"
    if not artifacts_root.is_dir():
        return iter(())
    return artifacts_root.rglob("*.meta.json")


def _iter_lock_files() -> Iterator[Path]:
    """All ``*.lock`` run lock files under locks/."""
    lock_root = vibecrafted_home() / "locks"
    if not lock_root.is_dir():
        return iter(())
    return lock_root.rglob("*.lock")


def _iter_marbles_state_files() -> Iterator[Path]:
    """All marbles ``state.json`` files under marbles/."""
    marbles_root = vibecrafted_home() / "marbles"
    if not marbles_root.is_dir():
        return iter(())
    return marbles_root.rglob("state.json")


def _normalize_agent_meta(path: Path) -> RunStatus | None:
    """Parse one launcher ``*.meta.json`` file into a :class:`RunStatus`; None if no run_id."""
    payload = _read_json(path)
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return None
    root = str(payload.get("root") or "")
    skill = _skill_from_code(str(payload.get("skill_code") or ""))
    state = str(payload.get("status") or "unknown")
    updated_at = _safe_iso(str(payload.get("updated_at") or ""))
    exit_code = _coerce_int(payload.get("exit_code"))
    liveness = str(payload.get("liveness") or "")
    health = (
        "final"
        if exit_code is not None or liveness == "terminal"
        else _state_health(state, updated_at)
    )
    extra: dict[str, Any] = {}
    for key in (
        "runtime",
        "source_dir",
        "prompt",
        "file",
        "retry_of",
        "agent_session_id",
        "runtime_session_id",
        "parent_runtime_session_id",
        "resume_of",
        "resume_root",
        "attempt",
        "native_resume",
        "resume_idempotency_key",
        "worker_command",
        "owner_pid",
        "owner_identity",
        "worker_pid",
        "worker_pgid",
        "worker_identity",
        "launcher_identity",
        "terminal_reason",
        "exit_signal",
        "heartbeat_at",
        "meta",
        "artifact_ok",
        "artifact_errors",
        "artifact_warnings",
        "artifact_gate",
        "operator_state",
        "operator_stop_accepted",
        "operator_stop_at",
        "stop_reason",
        "trust_receipt",
        # Cut A — durable Vibecrafted Workspace identity (projections/readers only).
        "workspace_id",
        "vibecrafted_session_id",
        "workspace_instance_id",
        "build_id",
        "workspace_display_label",
        "worker_host_session",
        "worker_host_display",
        # H2b2c — typed Operator Agent -> child Agent relationship truth.
        "role",
        "prompt_role",
        "provider_session_id",
        "operator_policy",
        "supervision",
        "stop_actor_run_id",
    ):
        if key in payload and payload.get(key) not in (None, ""):
            extra[key] = payload[key]
    return RunStatus(
        run_id=run_id,
        state=state,
        agent=str(payload.get("agent") or "unknown"),
        skill=skill,
        mode=str(payload.get("mode") or "unknown"),
        root=root,
        operator_session=operator_session_name(root, run_id),
        latest_report=str(payload.get("report") or ""),
        latest_transcript=str(payload.get("transcript") or ""),
        last_error=str(payload.get("message") or payload.get("reason") or ""),
        updated_at=updated_at,
        started_at=_safe_iso(
            str(payload.get("started_at") or payload.get("updated_at") or "")
        ),
        health=health,
        source="agent-meta",
        lock_present=False,
        exit_code=exit_code,
        liveness=liveness,
        launcher_pid=_coerce_int(payload.get("launcher_pid")),
        completed_at=_safe_iso(str(payload.get("completed_at") or "")),
        session_id=str(payload.get("session_id") or ""),
        extra=extra,
    )


def _normalize_lock(path: Path) -> RunStatus | None:
    """Parse one ``*.lock`` file into a :class:`RunStatus`; None if no run_id."""
    payload = _parse_kv_file(path)
    run_id = payload.get("run_id", "").strip()
    if not run_id:
        return None
    root = payload.get("root", "")
    state = payload.get("status", "running") or "running"
    started_at = payload.get("started", "")
    return RunStatus(
        run_id=run_id,
        state=state,
        agent=payload.get("agent", "unknown"),
        skill=_skill_from_code(payload.get("skill", "")),
        mode=payload.get("mode", payload.get("runtime", "unknown")),
        root=root,
        operator_session=operator_session_name(root, run_id),
        latest_report="",
        latest_transcript="",
        last_error="",
        updated_at=_safe_iso(started_at),
        started_at=_safe_iso(started_at),
        health=_state_health(state, started_at),
        source="lock",
        lock_present=True,
        liveness="lock_present",
        extra={},
    )


def _normalize_marbles_state(path: Path) -> RunStatus | None:
    """Parse one marbles ``state.json`` file into a :class:`RunStatus`; None if no run_id."""
    payload = _read_json(path)
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return None
    loops = payload.get("loops") or []
    latest_loop = loops[-1] if loops else {}
    updated_at = _safe_iso(
        str(payload.get("updated_at") or payload.get("started_at") or "")
    )
    state = str(payload.get("status") or "unknown")
    return RunStatus(
        run_id=run_id,
        state=state,
        agent=str(payload.get("agent") or "unknown"),
        skill="marbles",
        mode=str(payload.get("mode") or "steered"),
        root=str(payload.get("root") or ""),
        operator_session=operator_session_name(str(payload.get("root") or ""), run_id),
        latest_report=str(latest_loop.get("report") or ""),
        latest_transcript=str(latest_loop.get("transcript") or ""),
        last_error=str(payload.get("failure_hint") or latest_loop.get("reason") or ""),
        updated_at=updated_at,
        started_at=_safe_iso(str(payload.get("started_at") or "")),
        health=_state_health(state, updated_at),
        source="marbles-state",
        lock_present=False,
        current_loop=int(payload["current_loop"])
        if isinstance(payload.get("current_loop"), int)
        else None,
        total_loops=int(payload["total_loops"])
        if isinstance(payload.get("total_loops"), int)
        else None,
        extra={
            "runtime": str(payload.get("runtime") or "headless"),
        },
    )


def _merge_event_stream(
    merged: dict[str, RunStatus], *, only_run_id: str | None = None
) -> dict[str, RunStatus]:
    """Fold the durable event stream's per-run state into ``merged``, optionally
    scoped to one run id and its ``<run_id>-...`` child rounds."""
    stream = event_stream_path()
    if not stream.exists():
        return merged

    scope = str(only_run_id or "").strip()
    child_prefix = f"{scope}-" if scope else ""

    for line in _read_lines(stream):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _event_has_test_provenance(event):
            continue
        run_id = str(event.get("run_id") or "").strip()
        if not run_id:
            continue
        # Scoped projection: process only the target run and its child rounds
        # (marbles/polarize L2/L3 live in "<parent>-…-L<n>" records). Skipping
        # every other run's events is what keeps a per-run await/lookup off the
        # O(all-runs) board rebuild that caused the lock herd.
        if scope and run_id != scope and not run_id.startswith(child_prefix):
            continue

        payload = dict(event.get("payload") or {})
        kind = str(event.get("kind") or "")
        artifact_event = (
            str(payload.get("event_kind") or "") == "artifact"
            or "meta_exists" in payload
            or "report_exists" in payload
            or "transcript_exists" in payload
        )
        if artifact_event and "artifact_errors" not in payload and "errors" in payload:
            raw_errors = payload.get("errors")
            payload["artifact_errors"] = (
                list(raw_errors)
                if isinstance(raw_errors, (list, tuple))
                else ([raw_errors] if raw_errors else [])
            )
        if (
            artifact_event
            and "artifact_warnings" not in payload
            and "warnings" in payload
        ):
            raw_warnings = payload.get("warnings")
            payload["artifact_warnings"] = (
                list(raw_warnings)
                if isinstance(raw_warnings, (list, tuple))
                else ([raw_warnings] if raw_warnings else [])
            )
        if artifact_event and "artifact_ok" not in payload:
            payload["artifact_ok"] = not bool(payload.get("artifact_errors"))
        # Settlement events are a durable notification about a snapshot that
        # has already been written. Re-projecting them as lifecycle evidence
        # would resurrect an archived run as active/unknown on the next sync.
        if kind == SETTLEMENT_EVENT_KIND:
            continue
        message = str(event.get("message") or "")
        ts = _safe_iso(str(event.get("ts") or ""))
        existing = merged.get(run_id)

        state = str(payload.get("state") or "")
        if not state and kind.startswith("lifecycle:"):
            state = kind.split(":", 1)[1]
        if not state and kind == "launch":
            state = "created"
        if not state and kind == "audit:stop" and payload.get("accepted"):
            state = "stopped"
        if not state:
            state = existing.state if existing is not None else "unknown"

        identity_required = bool(payload.get("identity_required"))
        raw_root = payload.get("root") or (existing.root if existing else "")
        root = (
            normalize_run_root(str(raw_root or ""), Path.cwd())
            if identity_required
            else str(raw_root or "")
        )
        agent = str(payload.get("agent") or (existing.agent if existing else "unknown"))
        skill = str(payload.get("skill") or (existing.skill if existing else "unknown"))
        mode = str(payload.get("mode") or (existing.mode if existing else "unknown"))
        report = str(
            payload.get("report")
            or (existing.latest_report if existing is not None else "")
        )
        transcript = str(
            payload.get("transcript")
            or (existing.latest_transcript if existing is not None else "")
        )
        updated_at = ts or (existing.updated_at if existing else "")
        started_at = str(
            payload.get("started_at")
            or (existing.started_at if existing is not None else "")
            or updated_at
        )
        exit_code = _coerce_int(payload.get("exit_code"))
        if exit_code is None and existing is not None:
            exit_code = existing.exit_code
        liveness = str(
            payload.get("liveness") or (existing.liveness if existing else "")
        )
        launcher_pid = _coerce_int(payload.get("launcher_pid"))
        if launcher_pid is None and existing is not None:
            launcher_pid = existing.launcher_pid
        completed_at = str(
            payload.get("completed_at")
            or (existing.completed_at if existing is not None else "")
        )
        session_id = str(
            payload.get("session_id")
            or (existing.session_id if existing is not None else "")
        )
        if identity_required:
            session_id = ensure_session_id(session_id)

        extra = dict(existing.extra if existing is not None else {})
        for key in (
            "runtime",
            "source_dir",
            "prompt",
            "file",
            "retry_of",
            "agent_session_id",
            "runtime_session_id",
            "parent_runtime_session_id",
            "resume_of",
            "resume_root",
            "attempt",
            "native_resume",
            "resume_idempotency_key",
            "resume_mode",
            "automatic_attempt_budget",
            "automatic_attempt_number",
            "resume_settlement_revision",
            "resume_trust_receipt_id",
            "worker_command",
            "owner_pid",
            "owner_identity",
            "worker_pid",
            "worker_pgid",
            "worker_identity",
            "launcher_identity",
            "heartbeat_at",
            "meta",
            "artifact_ok",
            "artifact_errors",
            "artifact_warnings",
            "recovery_required",
            "operator_stop_accepted",
            "operator_stop_at",
            "stop_reason",
            "stop_signal",
            "stop_target",
            "stop_target_pid",
            "stop_target_pgid",
            "stop_signal_sent",
            "stop_already_dead",
            "stop_alive_after_grace",
            "stop_grace_seconds",
            "terminal_reason",
            "exit_signal",
            # Durable workspace identity must survive event-stream refreshes;
            # otherwise a later generic `state` event erases the identity
            # carried by lifecycle:created/active.
            "workspace_id",
            "vibecrafted_session_id",
            "workspace_instance_id",
            "build_id",
            "workspace_display_label",
            "worker_host_session",
            "worker_host_display",
        ):
            if key in payload and payload.get(key) not in (None, ""):
                extra[key] = payload[key]

        payload_last_error = str(
            payload.get("error") or payload.get("last_error") or ""
        )
        if kind == "state" and payload_last_error == message:
            payload_last_error = ""
        last_error = (
            payload_last_error
            or (existing.last_error if existing is not None else "")
            or (
                message
                if kind != "state"
                and (state in BLOCKED_STATES or state in {"failed", "ghost"})
                else ""
            )
        )
        declared_health = str(payload.get("health") or "")
        activity_at = _freshest_activity_stamp(
            str(payload.get("heartbeat_at") or ""),
            updated_at,
            transcript=transcript,
        ) or str(payload.get("heartbeat_at") or updated_at)
        if declared_health in {"stalled", "final", "unknown"}:
            health = declared_health
        else:
            health = _state_health(state, activity_at)

        incoming = RunStatus(
            run_id=run_id,
            state=state,
            agent=agent,
            skill=skill,
            mode=mode,
            root=root,
            operator_session=operator_session_name(root, run_id),
            latest_report=report,
            latest_transcript=transcript,
            last_error=last_error,
            updated_at=updated_at,
            started_at=started_at,
            health=health,
            source="event-stream",
            lock_present=existing.lock_present if existing is not None else False,
            exit_code=exit_code,
            liveness=liveness,
            launcher_pid=launcher_pid,
            completed_at=completed_at,
            session_id=session_id,
            current_loop=existing.current_loop if existing is not None else None,
            total_loops=existing.total_loops if existing is not None else None,
            extra=extra,
        )
        merged[run_id] = _merge_status(existing, incoming)

    return merged


def _merge_status(existing: RunStatus | None, incoming: RunStatus) -> RunStatus:
    """Merge two RunStatus records for the same run: newer-timestamp fields win,
    fields fall back to the other side when blank, and an accepted operator
    stop from either side is sticky and overrides the merged result."""
    if existing is None:
        return incoming
    existing_dt = _parse_iso(existing.updated_at)
    incoming_dt = _parse_iso(incoming.updated_at) or dt.datetime.min.replace(
        tzinfo=dt.timezone.utc
    )
    latest = (
        existing if existing_dt is not None and existing_dt >= incoming_dt else incoming
    )
    preferred = latest
    merged_status = RunStatus(
        run_id=preferred.run_id,
        state=preferred.state,
        agent=preferred.agent or existing.agent,
        skill=preferred.skill or existing.skill,
        mode=preferred.mode or existing.mode,
        root=preferred.root or existing.root,
        operator_session=preferred.operator_session or existing.operator_session,
        latest_report=preferred.latest_report or existing.latest_report,
        latest_transcript=preferred.latest_transcript or existing.latest_transcript,
        last_error=preferred.last_error or existing.last_error,
        updated_at=preferred.updated_at or existing.updated_at,
        started_at=preferred.started_at or existing.started_at,
        health=preferred.health,
        source=preferred.source,
        lock_present=existing.lock_present or incoming.lock_present,
        exit_code=preferred.exit_code
        if preferred.exit_code is not None
        else existing.exit_code,
        liveness=preferred.liveness or existing.liveness,
        launcher_pid=preferred.launcher_pid
        if preferred.launcher_pid is not None
        else existing.launcher_pid,
        completed_at=preferred.completed_at or existing.completed_at,
        session_id=preferred.session_id or existing.session_id,
        current_loop=preferred.current_loop
        if preferred.current_loop is not None
        else existing.current_loop,
        total_loops=preferred.total_loops
        if preferred.total_loops is not None
        else existing.total_loops,
        extra={**existing.extra, **incoming.extra},
    )
    stop_owner = next(
        (
            candidate
            for candidate in (incoming, existing)
            if candidate.state == "stopped"
            and candidate.extra.get("operator_stop_accepted") is True
        ),
        None,
    )
    if stop_owner is None:
        return merged_status

    stop_extra = {
        **merged_status.extra,
        **stop_owner.extra,
        "operator_stop_accepted": True,
        "recovery_required": False,
        "artifact_ok": True,
        "artifact_errors": [],
        "artifact_gate": "stopped",
        "operator_state": "stopped",
    }
    return replace(
        merged_status,
        state="stopped",
        last_error="",
        updated_at=stop_owner.updated_at or merged_status.updated_at,
        health="final",
        source=stop_owner.source,
        exit_code=stop_owner.exit_code,
        liveness=stop_owner.liveness or "terminal",
        completed_at=stop_owner.completed_at or merged_status.completed_at,
        extra=stop_extra,
    )


def _snapshot_path(run_id: str) -> Path:
    """Path to one run's live snapshot file."""
    return run_snapshot_dir() / f"{run_id}.json"


def _snapshot_archive_dir() -> Path:
    """Directory holding archived (settled + retired) run snapshots."""
    return run_snapshot_dir() / "archive"


def _archived_run_ids() -> set[str]:
    """Run ids already settled and archived — closed history, never rebuilt.

    Snapshot filenames are ``<run_id>.json``, so a directory listing is enough;
    no JSON parse. Used by the full board rebuild to stop archived runs from
    resurrecting out of their (still present) launcher meta files or old event
    lines — the resurrection loop is what let settlement debt grow without end.
    """
    archive_dir = _snapshot_archive_dir()
    if not archive_dir.is_dir():
        return set()
    return {path.stem for path in archive_dir.glob("*.json")}


def _events_archive_dir() -> Path:
    """Directory holding archived (rotated) event-stream generations."""
    return control_plane_home() / "events_archive"


def _prune_event_archives_locked() -> list[Path]:
    """Enforce retained generation count/byte caps while event EX is held."""

    archive_dir = _events_archive_dir()
    if not archive_dir.is_dir():
        return []
    entries: list[tuple[int, str, Path, int]] = []
    for path in archive_dir.glob("events-*.jsonl"):
        try:
            metadata = path.stat()
        except OSError:
            continue
        entries.append((metadata.st_mtime_ns, path.name, path, metadata.st_size))
    entries.sort()
    max_files = _configured_events_archive_max_files()
    max_bytes = _configured_events_archive_max_bytes()
    total_bytes = sum(entry[3] for entry in entries)
    removed: list[Path] = []
    while entries and (len(entries) > max_files or total_bytes > max_bytes):
        _, _, path, size = entries.pop(0)
        try:
            path.unlink()
        except OSError:
            continue
        total_bytes = max(total_bytes - size, 0)
        removed.append(path)
    if removed:
        with contextlib.suppress(OSError):
            _fsync_directory(archive_dir)
    return removed


def _read_tail_lines(path: Path, limit: int, *, window_bytes: int = 65536) -> list[str]:
    """Last ``limit`` complete lines of a large file without reading it whole."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(size - window_bytes, 0))
            chunk = handle.read()
    except OSError:
        return []
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if size > window_bytes and lines:
        # First line of the window is almost certainly a partial record.
        lines = lines[1:]
    return lines[-limit:]


def _rotate_event_stream() -> Path | None:
    """Rotate an oversized generation under the exclusive event lock.

    The event stream is append-only and unbounded; every full board rebuild
    re-parses it whole, so past ~1 GB a sync took a minute and starved every
    await on the lock. Rotation is safe exactly at the end of an unscoped sync:
    all information the stream carried has just been projected into per-run
    snapshots, which are the durable state. The archived generation remains the
    cursor bridge for SSE reconnects. The fresh generation contains only its
    header: copying tail records would replay effects.

    The normal caller already holds ``_sync_lock``. Lock order is therefore
    sync -> event, never the reverse.
    """
    with _event_lock(exclusive=True):
        return _rotate_event_stream_locked()


def _rotate_event_stream_locked() -> Path | None:
    """Implementation for tests/recovery; caller holds event EX."""

    threshold = _configured_events_rotate_bytes()
    if threshold <= 0:
        return None
    stream_path = event_stream_path()
    try:
        if not stream_path.is_file() or stream_path.stat().st_size <= threshold:
            return None
    except OSError:
        return None
    header = _read_segment_header(stream_path)
    if header is None:
        # Legacy input is archived intact. A fresh epoch makes the discontinuity
        # explicit to v2 clients instead of pretending byte offsets still align.
        epoch, generation = str(uuid.uuid4()), 0
    else:
        epoch, generation = header[0], header[1] + 1
    archive_dir = _events_archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)
    if header is None:
        target = _legacy_archive_target()
    else:
        target = archive_dir / f"events-{header[0]}-g{header[1]:020d}.jsonl"
        # Two files claiming the same epoch/generation make exactly-once replay
        # ambiguous. Keep the active segment in place and fail closed.
        if target.exists():
            return None
    try:
        stream_path.replace(target)
        _fsync_directory(archive_dir)
        _fsync_directory(stream_path.parent)
        _write_new_event_segment_locked(stream_path, epoch, generation)
        _prune_event_archives_locked()
    except OSError:
        # Best-effort rollback preserves the only copy when publishing the new
        # header fails (for example ENOSPC). The archive is never overwritten.
        if not stream_path.exists() and target.exists():
            with contextlib.suppress(OSError):
                target.replace(stream_path)
                _fsync_directory(stream_path.parent)
        return None
    return target


def _load_existing_snapshots() -> dict[str, dict[str, Any]]:
    """Load every retained run snapshot from disk, keyed by run_id."""
    snapshots: dict[str, dict[str, Any]] = {}
    for path in run_snapshot_dir().glob("*.json"):
        payload = _read_json(path)
        run_id = str(payload.get("run_id") or "").strip()
        if run_id:
            snapshots[run_id] = payload
    return snapshots


def _status_to_payload(status: RunStatus) -> dict[str, Any]:
    """Flatten a RunStatus (and its ``extra`` dict) into one JSON-ready payload."""
    payload = asdict(status)
    extra = payload.pop("extra", {})
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _run_is_terminal(run: dict[str, Any]) -> bool:
    """True when a run payload's state, liveness, or exit code marks it finished."""
    if str(run.get("state") or "") in FINAL_STATES:
        return True
    if str(run.get("liveness") or "") == "terminal":
        return True
    return _coerce_int(run.get("exit_code")) is not None


def _snapshot_age_seconds(payload: dict[str, Any], now: dt.datetime) -> float | None:
    """Seconds since the snapshot's completed_at/updated_at/started_at (first found)."""
    timestamp = _parse_iso(
        str(
            payload.get("completed_at")
            or payload.get("updated_at")
            or payload.get("started_at")
            or ""
        )
    )
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    return (now - timestamp).total_seconds()


def _archive_snapshot(path: Path) -> None:
    """Move one snapshot file into the archive directory (rename, not copy)."""
    archive_dir = _snapshot_archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)
    path.replace(archive_dir / path.name)


def _archive_expired_snapshots() -> None:
    """Settle-then-archive terminal snapshots past the retention age/count caps.

    Called at the end of a full board rebuild. Never archives a snapshot that
    is not yet settled (:func:`can_archive`) — it settles it first via
    :func:`settle_payload`, defaulting to ``needs_attention``.
    """
    now = _now()
    retention_seconds = _configured_snapshot_retention_seconds()
    retention_count = _configured_snapshot_retention_count()
    terminal_snapshots: list[tuple[dt.datetime, Path, dict[str, Any]]] = []
    expired_by_age: set[Path] = set()

    for path in run_snapshot_dir().glob("*.json"):
        payload = _read_json(path)
        if not payload or not _run_is_terminal(payload):
            continue
        age_seconds = _snapshot_age_seconds(payload, now)
        if age_seconds is not None and age_seconds >= retention_seconds:
            expired_by_age.add(path)
        updated_at = _parse_iso(
            str(payload.get("updated_at") or "")
        ) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=dt.timezone.utc)
        terminal_snapshots.append((updated_at, path, payload))

    terminal_snapshots.sort(key=lambda item: item[0], reverse=True)
    expired_by_count = {path for _, path, _ in terminal_snapshots[retention_count:]}
    for path in sorted(expired_by_age | expired_by_count):
        if not path.exists():
            continue
        payload = _read_json(path)
        # Settlement precedes gc: never erase without a written terminal.
        # Settle first (default → needs_attention), then archive only once
        # the settlement axis is present. Live/unreadable runs stay put.
        if not can_archive(payload):
            previous = dict(payload)
            settlement = settle_payload(payload, source="auto")
            if settlement is not None:
                payload.update(settlement.to_payload())
                payload["settlement"] = {
                    "verdict": settlement.verdict.value,
                    "reason": settlement.reason,
                    "settled_at": settlement.settled_at,
                    "source": settlement.source,
                    "claim_digest": settlement.claim_digest,
                    "waived": settlement.waived,
                    "tui": settlement.tui_key,
                }
                _write_run_snapshot(path, previous, payload)
            if not can_archive(payload):
                continue
        _archive_snapshot(path)


def drain_settled_snapshots(
    *, keep_hours: float = 24.0, batch_size: int = 50
) -> dict[str, Any]:
    """Settle-then-archive the parked terminal debt in ``runs/`` (canonical drain).

    Settlement precedes gc (contract §7): every terminal snapshot first gets its
    settlement terminal written (default → ``needs_attention``), then moves to
    ``runs/archive/`` once it is older than ``keep_hours``. Recent terminals stay
    retained so the board keeps showing the current day's f/x/n. Live runs are
    never touched and nothing is deleted — archive is a rename.

    Each batch (≤ ``batch_size`` snapshots) runs under its own bounded sync
    lock, released between batches, so concurrent awaits never starve. The
    operation is idempotent per run: a crashed drain resumes on the next call.
    """
    keep_seconds = max(float(keep_hours), 0.0) * 3600.0
    step = max(int(batch_size), 1)
    now = _now()
    counts = {
        "settled": 0,
        "archived": 0,
        "kept_recent": 0,
        "skipped_live": 0,
        "unreadable": 0,
    }
    paths = sorted(run_snapshot_dir().glob("*.json"))
    for start in range(0, len(paths), step):
        batch = paths[start : start + step]
        with _sync_lock(purpose="drain"):
            for path in batch:
                if not path.exists():
                    continue
                payload = _read_json(path)
                if not payload:
                    counts["unreadable"] += 1
                    continue
                if not _run_is_terminal(payload):
                    counts["skipped_live"] += 1
                    continue
                if not can_archive(payload):
                    previous = dict(payload)
                    settlement = settle_payload(payload, source="auto")
                    if settlement is None:
                        counts["skipped_live"] += 1
                        continue
                    payload.update(settlement.to_payload())
                    payload["settlement"] = {
                        "verdict": settlement.verdict.value,
                        "reason": settlement.reason,
                        "settled_at": settlement.settled_at,
                        "source": settlement.source,
                        "claim_digest": settlement.claim_digest,
                        "waived": settlement.waived,
                        "tui": settlement.tui_key,
                    }
                    _write_run_snapshot(path, previous, payload)
                    counts["settled"] += 1
                age = _snapshot_age_seconds(payload, now)
                if age is not None and age < keep_seconds:
                    counts["kept_recent"] += 1
                    continue
                if can_archive(payload):
                    _archive_snapshot(path)
                    counts["archived"] += 1
    counts["retained"] = len(list(run_snapshot_dir().glob("*.json")))
    return counts


def _select_run(snapshot: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    """Find one run by id in a sync_state snapshot, falling back to the live
    and then archived on-disk snapshot files if it's not in the in-memory lists."""
    target = str(run_id or "").strip()
    if not target:
        return None
    for key in ("active_runs", "recent_runs"):
        for run in snapshot.get(key) or []:
            if str(run.get("run_id") or "") == target:
                return dict(run)
    payload = _read_json(_snapshot_path(target))
    if str(payload.get("run_id") or "") == target:
        return payload
    archived = _read_json(_snapshot_archive_dir() / f"{target}.json")
    if str(archived.get("run_id") or "") == target:
        return archived
    return None


# Fields that drift between consecutive sync_state() passes without
# representing a meaningful lifecycle change (timestamps re-derived from the
# event stream, provenance of the winning source, transcript delta counters).
# They must be excluded from the idempotency comparison so re-syncing an
# unchanged run does not emit a spurious "refreshed" event.
_VOLATILE_TRANSITION_KEYS = frozenset(
    {
        "updated_at",
        "heartbeat_at",
        "generated_at",
        "source",
        "transcript_growth",
    }
)


def _stable_transition_view(run: dict[str, Any] | None) -> dict[str, Any]:
    """Run payload with volatile/re-derived fields stripped, for transition-change comparison."""
    if not run:
        return {}
    return {
        key: value for key, value in run.items() if key not in _VOLATILE_TRANSITION_KEYS
    }


def _record_transition(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> None:
    """Emit a "state" event when a projection meaningfully changed, suppressing
    no-op re-syncs that would otherwise flood the event stream."""
    previous_state = str(previous.get("state") or "") if previous else ""
    current_state = str(current.get("state") or "")
    if previous_state == current_state and _stable_transition_view(
        previous
    ) == _stable_transition_view(current):
        return
    message = (
        f"{current['run_id']} entered {current_state}"
        if previous_state != current_state
        else f"{current['run_id']} refreshed"
    )
    _append_event(
        {
            "ts": _now().isoformat(),
            "run_id": current["run_id"],
            "kind": "state",
            "message": message,
            "payload": {
                "previous_state": previous_state,
                "state": current_state,
                "agent": current.get("agent"),
                "skill": current.get("skill"),
                "mode": current.get("mode"),
                "health": current.get("health"),
                "root": current.get("root"),
                "session_id": current.get("session_id"),
                "liveness": current.get("liveness"),
                "launcher_pid": current.get("launcher_pid"),
                "heartbeat_at": current.get("heartbeat_at"),
                "recovery_required": current.get("recovery_required"),
                "last_error": current.get("last_error"),
            },
        }
    )


def record_stop_transition(
    run_id: str,
    *,
    run: dict[str, Any] | None = None,
    accepted: bool,
    reason: str,
    signal_name: str = "SIGTERM",
    target: str = "",
    target_pid: int | None = None,
    target_pgid: int | None = None,
    signal_sent: bool = False,
    already_dead: bool = False,
    alive_after_grace: bool | None = None,
    grace_seconds: float = 0.0,
    exit_code: int | None = None,
    error: str = "",
) -> dict[str, Any]:
    """Append the canonical control-plane event for an operator stop request."""
    now = _now().isoformat()
    payload: dict[str, Any] = {
        "accepted": accepted,
        "reason": reason,
        "signal": signal_name,
        "stop_signal": signal_name,
        "target": target,
        "stop_target": target,
        "target_pid": target_pid,
        "stop_target_pid": target_pid,
        "target_pgid": target_pgid,
        "stop_target_pgid": target_pgid,
        "signal_sent": signal_sent,
        "stop_signal_sent": signal_sent,
        "already_dead": already_dead,
        "stop_already_dead": already_dead,
        "alive_after_grace": alive_after_grace,
        "stop_alive_after_grace": alive_after_grace,
        "grace_seconds": grace_seconds,
        "stop_grace_seconds": grace_seconds,
        "error": error,
    }
    if run:
        for key in (
            "agent",
            "skill",
            "mode",
            "root",
            "session_id",
            "launcher_pid",
            "worker_pid",
            "worker_pgid",
            "launcher_identity",
            "worker_identity",
            "runtime",
            "source_dir",
            "prompt",
            "file",
            "meta",
            "report",
            "transcript",
        ):
            if key in run and run.get(key) not in (None, ""):
                payload[key] = run[key]
        if "report" not in payload and run.get("latest_report"):
            payload["report"] = run["latest_report"]
        if "transcript" not in payload and run.get("latest_transcript"):
            payload["transcript"] = run["latest_transcript"]
    if accepted:
        payload["state"] = "stopped"
        payload["operator_stop_accepted"] = True
        payload["operator_stop_at"] = now
        payload["stop_reason"] = reason
        payload["recovery_required"] = False
        payload["health"] = "final"
        payload["liveness"] = (
            "pid_alive_after_stop" if alive_after_grace else "terminal"
        )
        payload["completed_at"] = now
        if exit_code is not None:
            payload["exit_code"] = exit_code
    else:
        payload["stop_rejection_reason"] = reason

    event = {
        "ts": now,
        "run_id": str(run_id or ""),
        "kind": "audit:stop",
        "message": (
            "run stopped" if accepted else f"stop rejected: {reason.replace('_', ' ')}"
        ),
        "payload": payload,
    }
    # Dedicated event append boundary (see _append_event) — the stop path never
    # blocks behind the global board-rebuild lock.
    _append_event(event)
    return event


def _warnings_for_runs(runs: list[dict[str, Any]]) -> list[str]:
    """Short human-readable warning strings (stalled, lock-without-report,
    contract-failure) for a board of runs, capped to the first 6."""
    warnings: list[str] = []
    for run in runs:
        if run.get("health") == "stalled":
            warnings.append(f"{run['run_id']} looks stalled ({run.get('state')}).")
        if (
            run.get("lock_present")
            and not run.get("latest_report")
            and run.get("state") not in FINAL_STATES
        ):
            warnings.append(
                f"{run['run_id']} still has a live lock but no report artifact yet."
            )
        failure_card = run.get("failure_card")
        if isinstance(failure_card, dict) and failure_card.get("code"):
            warnings.append(
                f"{run['run_id']} contract failure: {failure_card.get('code')}"
            )
    return warnings[:6]


def read_event_tail(limit: int = EVENT_TAIL_LIMIT) -> list[dict[str, Any]]:
    """Most recent ``limit`` events (newest scanned first, across active + archived
    segments), skipping segment headers and pytest-provenance events."""
    stream = event_stream_path()
    if limit <= 0:
        return []
    paths: list[Path] = []
    if stream.is_file():
        paths.append(stream)
    archive_dir = _events_archive_dir()
    if archive_dir.is_dir():
        archived = list(archive_dir.glob("events-*.jsonl"))

        def _mtime_ns(path: Path) -> int:
            """Sort key: file mtime in nanoseconds, 0 if the file vanished mid-scan."""
            try:
                return path.stat().st_mtime_ns
            except OSError:
                return 0

        archived.sort(key=_mtime_ns, reverse=True)
        paths.extend(archived)
    events: list[dict[str, Any]] = []
    for path in paths:
        for line in reversed(_read_tail_lines(path, limit + 1)):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("kind") == "stream.segment":
                continue
            if _event_has_test_provenance(event):
                continue
            events.append(event)
            if len(events) >= limit:
                return events
    return events


def _stream_control_event(
    kind: str,
    *,
    cursor: str,
    requested: str = "",
    from_cursor: str = "",
    reason: str,
) -> Event:
    """Build a synthetic ``stream.gap``/``stream.boundary`` control :class:`Event`
    for subscribe_events callers when the raw event data can't be drained cleanly."""
    payload: dict[str, Any] = {"reason": reason}
    if kind == "stream.boundary":
        payload.update({"from": from_cursor, "to": cursor})
        message = "event stream generation boundary"
    else:
        payload.update(
            {
                "requested": requested,
                "resumed_at": cursor,
                "action": "resnapshot",
            }
        )
        message = "event stream cursor gap; resnapshot required"
    return Event(
        ts=_now().isoformat(),
        run_id="",
        kind=kind,
        message=message,
        payload=payload,
        cursor=cursor,
    )


def _drain_event_segment(
    segment: _EventSegment,
    offset: int,
    kinds_filter: set[str],
) -> tuple[list[Event], int, str | None]:
    """Read every complete record from ``offset`` to EOF of one segment.

    Returns (events, new_offset, failure_reason). A failure reason is set when
    the segment's header no longer matches what the caller expected (rotated
    out from under the reader) or the cursor falls outside the segment.
    """
    events: list[Event] = []
    try:
        with segment.path.open("rb") as handle:
            observed = _parse_segment_header(handle.readline(EVENT_MAX_LINE_BYTES + 1))
            if observed != (segment.epoch, segment.generation):
                return events, offset, "generation_changed_before_read"
            size = os.fstat(handle.fileno()).st_size
            if offset < segment.data_start or offset > size:
                return events, offset, "cursor_outside_segment"
            handle.seek(offset)
            cursor = offset
            while True:
                line_start = cursor
                raw = handle.readline(EVENT_MAX_LINE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > EVENT_MAX_LINE_BYTES:
                    complete = raw.endswith(b"\n")
                    if not complete:
                        while True:
                            suffix = handle.readline(EVENT_MAX_LINE_BYTES + 1)
                            if not suffix or suffix.endswith(b"\n"):
                                complete = suffix.endswith(b"\n")
                                break
                    cursor = handle.tell()
                    events.append(
                        _stream_control_event(
                            "stream.gap",
                            cursor=segment.cursor(cursor),
                            requested=segment.cursor(line_start),
                            reason="line_too_large",
                        )
                    )
                    if not complete and segment.active:
                        break
                    continue
                if not raw.endswith(b"\n"):
                    if segment.active:
                        break
                    cursor = handle.tell()
                    events.append(
                        _stream_control_event(
                            "stream.gap",
                            cursor=segment.cursor(cursor),
                            requested=segment.cursor(line_start),
                            reason="partial_archived_line",
                        )
                    )
                    break
                cursor = handle.tell()
                try:
                    payload = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    events.append(
                        _stream_control_event(
                            "stream.gap",
                            cursor=segment.cursor(cursor),
                            requested=segment.cursor(line_start),
                            reason="malformed_event",
                        )
                    )
                    continue
                kind = str(payload.get("kind") or "")
                if kind == "stream.segment":
                    continue
                if kinds_filter and kind not in kinds_filter:
                    continue
                events.append(
                    Event(
                        ts=str(payload.get("ts") or ""),
                        run_id=str(payload.get("run_id") or ""),
                        kind=kind,
                        message=str(payload.get("message") or ""),
                        payload=dict(payload.get("payload") or {}),
                        cursor=segment.cursor(cursor),
                    )
                )
            return events, cursor, None
    except OSError:
        return events, offset, "generation_expired_or_unknown"


def _drain_legacy_events(
    offset: int, kinds_filter: set[str]
) -> tuple[list[Event], int, str | None]:
    """Read every complete record from ``offset`` to EOF of a headerless legacy
    (pre-v2) event stream. Same (events, new_offset, failure_reason) contract
    as :func:`_drain_event_segment`."""
    stream = event_stream_path()
    events: list[Event] = []
    try:
        with stream.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if offset > size:
                return events, offset, "legacy_cursor_beyond_eof"
            handle.seek(offset)
            cursor = offset
            while True:
                line_start = cursor
                raw = handle.readline(EVENT_MAX_LINE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > EVENT_MAX_LINE_BYTES:
                    complete = raw.endswith(b"\n")
                    if not complete:
                        while True:
                            suffix = handle.readline(EVENT_MAX_LINE_BYTES + 1)
                            if not suffix or suffix.endswith(b"\n"):
                                complete = suffix.endswith(b"\n")
                                break
                    cursor = handle.tell()
                    events.append(
                        _stream_control_event(
                            "stream.gap",
                            cursor=str(cursor),
                            requested=str(line_start),
                            reason="line_too_large",
                        )
                    )
                    if not complete:
                        break
                    continue
                if not raw.endswith(b"\n"):
                    break
                cursor = handle.tell()
                try:
                    payload = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                kind = str(payload.get("kind") or "")
                if kinds_filter and kind not in kinds_filter:
                    continue
                events.append(
                    Event(
                        ts=str(payload.get("ts") or ""),
                        run_id=str(payload.get("run_id") or ""),
                        kind=kind,
                        message=str(payload.get("message") or ""),
                        payload=dict(payload.get("payload") or {}),
                        cursor=str(cursor),
                    )
                )
            return events, cursor, None
    except OSError:
        return events, offset, None


def _read_event_delta(
    requested: str | int | None, kinds_filter: set[str]
) -> tuple[list[Event], str]:
    """Resolve one cursor to its event delta, handling legacy-numeric cursors,
    generation boundaries, and gaps by emitting synthetic control events and
    resuming at the active stream's current position."""
    raw_requested = "0" if requested is None else str(requested).strip()
    parsed = _parse_event_cursor(requested)
    if parsed is None:
        resumed_at = _active_event_resume_cursor()
        return [
            _stream_control_event(
                "stream.gap",
                cursor=resumed_at,
                requested=raw_requested,
                reason="invalid_cursor",
            )
        ], resumed_at

    epoch, generation, offset = parsed
    segments = _event_segments()
    active = next((segment for segment in reversed(segments) if segment.active), None)
    if epoch is None:
        if active is not None:
            if offset != 0:
                resumed_at = _active_event_resume_cursor()
                return [
                    _stream_control_event(
                        "stream.gap",
                        cursor=resumed_at,
                        requested=raw_requested,
                        reason="legacy_cursor_generation_unknown",
                    )
                ], resumed_at
            start_cursor = active.cursor(active.data_start)
            legacy_items = [
                _stream_control_event(
                    "stream.boundary",
                    cursor=start_cursor,
                    from_cursor="0",
                    reason="legacy_zero_migrated",
                )
            ]
            drained, end, failure = _drain_event_segment(
                active, active.data_start, kinds_filter
            )
            if failure is not None:
                resumed_at = _active_event_resume_cursor()
                legacy_items.append(
                    _stream_control_event(
                        "stream.gap",
                        cursor=resumed_at,
                        requested=start_cursor,
                        reason=failure,
                    )
                )
                return legacy_items, resumed_at
            legacy_items.extend(drained)
            return legacy_items, active.cursor(end)
        drained, end, failure = _drain_legacy_events(offset, kinds_filter)
        if failure is None:
            return drained, str(end)
        resumed_at = _active_event_resume_cursor()
        return [
            _stream_control_event(
                "stream.gap",
                cursor=resumed_at,
                requested=raw_requested,
                reason=failure,
            )
        ], resumed_at

    matches = [
        segment
        for segment in segments
        if segment.epoch == epoch and segment.generation == generation
    ]
    if len(matches) != 1:
        resumed_at = _active_event_resume_cursor()
        reason = (
            "generation_expired_or_unknown" if not matches else "ambiguous_generation"
        )
        return [
            _stream_control_event(
                "stream.gap",
                cursor=resumed_at,
                requested=raw_requested,
                reason=reason,
            )
        ], resumed_at

    current = matches[0]
    current_offset = offset
    stream_items: list[Event] = []
    while True:
        drained, current_offset, failure = _drain_event_segment(
            current, current_offset, kinds_filter
        )
        stream_items.extend(drained)
        if failure is not None:
            resumed_at = _active_event_resume_cursor()
            stream_items.append(
                _stream_control_event(
                    "stream.gap",
                    cursor=resumed_at,
                    requested=current.cursor(current_offset),
                    reason=failure,
                )
            )
            return stream_items, resumed_at

        next_segments = [
            segment
            for segment in segments
            if segment.epoch == current.epoch
            and segment.generation == current.generation + 1
        ]
        if len(next_segments) > 1:
            resumed_at = _active_event_resume_cursor()
            stream_items.append(
                _stream_control_event(
                    "stream.gap",
                    cursor=resumed_at,
                    requested=current.cursor(current_offset),
                    reason="ambiguous_generation",
                )
            )
            return stream_items, resumed_at
        if not next_segments:
            if (
                not current.active
                and active is not None
                and (
                    active.epoch != current.epoch
                    or active.generation > current.generation + 1
                )
            ):
                resumed_at = _active_event_resume_cursor()
                stream_items.append(
                    _stream_control_event(
                        "stream.gap",
                        cursor=resumed_at,
                        requested=current.cursor(current_offset),
                        reason="generation_gap",
                    )
                )
                return stream_items, resumed_at
            return stream_items, current.cursor(current_offset)
        next_segment = next_segments[0]
        next_cursor = next_segment.cursor(next_segment.data_start)
        stream_items.append(
            _stream_control_event(
                "stream.boundary",
                cursor=next_cursor,
                from_cursor=current.cursor(current_offset),
                reason="generation_advanced",
            )
        )
        current = next_segment
        current_offset = current.data_start


def subscribe_events(
    since_cursor: str | int | None = None,
    kinds: set[str] | list[str] | tuple[str, ...] | None = None,
    callback: Callable[[Event], Any] | None = None,
) -> Iterator[Event]:
    """Yield events with an opaque generation-aware cursor.

    Numeric cursors remain an explicit migration input: ``0`` upgrades to the
    active v2 start, while a non-zero numeric cursor on a segmented stream emits
    ``stream.gap`` because it cannot identify a generation.
    """

    cursor = "0" if since_cursor is None else str(since_cursor)
    kinds_filter = set(kinds or [])
    while True:
        events, cursor = _read_event_delta(cursor, kinds_filter)
        for event in events:
            if (
                kinds_filter
                and event.kind.startswith("stream.")
                and event.kind not in kinds_filter
            ):
                continue
            if callback is not None:
                callback(event)
            yield event
        if callback is None:
            return
        time.sleep(1.0)


def _project_run_payload(
    run_id: str, status: RunStatus, previous: dict[str, Any] | None
) -> dict[str, Any]:
    """Project one run's status to a candidate snapshot payload."""
    incoming = _status_to_payload(status)
    if previous is not None and _accepted_operator_stop_payload(previous):
        # Snapshots are also reducer inputs after event rotation. Preserve the
        # accepted stop even if a stale runtime meta or a later supervisor
        # failure is the only newer source still on disk.
        incoming.update(
            {
                "state": "stopped",
                "operator_stop_accepted": True,
                "operator_stop_at": previous.get("operator_stop_at"),
                "stop_reason": previous.get("stop_reason"),
                "recovery_required": False,
                "health": "final",
                "liveness": previous.get("liveness") or "terminal",
                "completed_at": previous.get("completed_at"),
                "exit_code": previous.get("exit_code"),
                "last_error": "",
                "artifact_ok": True,
                "artifact_errors": [],
                "artifact_gate": "stopped",
                "operator_state": "stopped",
            }
        )
    # A settled gc park is sticky: the launcher meta that fed this status stays
    # "running" forever, so without this guard every full sync re-parked the
    # same dead run — restamping updated_at/completed_at (which made the drain
    # keep-window immortal) and appending the park explanation to last_error on
    # every pass. Fresh process evidence still reopens normal projection.
    if (
        previous is not None
        and str(previous.get("state") or "") == "gc"
        and str(previous.get("settlement_verdict") or "")
        and not _worker_is_alive(incoming)
        and _coerce_int(incoming.get("exit_code")) is None
    ):
        return dict(previous)
    payload = _artifact_projection(incoming, previous)
    payload = _reconcile_dead_launcher(payload)
    run_dir = _runtime_run_dir(run_id)
    runtime_meta = _read_json(run_dir / "meta.json") if run_dir.is_dir() else {}
    # Runtime meta is the durable half of the settlement transaction. If a
    # process died after meta replace but before snapshot replace, the next
    # projection must carry the higher revision forward rather than reviving
    # stale snapshot truth.
    for key in SETTLEMENT_PROJECTION_FIELDS:
        if key in runtime_meta:
            payload[key] = runtime_meta[key]
    axes = _delivery_axes_from_run_dir(
        run_dir if run_dir.is_dir() else None,
        legacy_state=str(payload.get("state") or ""),
        exit_code=_coerce_int(payload.get("exit_code")),
    )
    payload.update(axes.to_payload())
    previous_state = str(payload.get("state") or "")
    payload = _reconcile_repaired_report_terminal(payload)
    if previous_state != str(payload.get("state") or ""):
        axes = _delivery_axes_from_run_dir(
            run_dir if run_dir.is_dir() else None,
            legacy_state=str(payload.get("state") or ""),
            exit_code=_coerce_int(payload.get("exit_code")),
        )
        payload.update(axes.to_payload())
    kernel_claim_digest = _kernel_claim_digest_from_run_dir(
        run_dir if run_dir.is_dir() else None
    )
    launcher_claim_digest = ""
    if run_dir.is_dir():
        launcher_claim_digest = str(
            _read_json(run_dir / "meta.json").get("claim_digest") or ""
        ).strip()
    if kernel_claim_digest:
        payload["claim_digest"] = kernel_claim_digest
    elif launcher_claim_digest:
        # The launcher owns mission identity; report self-attestation may close
        # this digest, but it must never select a different mission itself.
        payload["claim_digest"] = launcher_claim_digest
    payload["failure_card"] = _failure_card(payload)
    state = str(payload.get("state") or "")
    # Launcher PIDs are ephemeral and can be reused by an unrelated process
    # months later. A current worker PID is durable process evidence; the
    # launch window is covered independently by a fresh heartbeat.
    has_live_process = _worker_is_alive(payload)
    payload["worker_alive"] = has_live_process
    if _run_is_terminal(payload):
        payload["health"] = "final"
    elif state == "stalled":
        payload["health"] = "stalled"
    elif has_live_process:
        payload["health"] = "active"
    else:
        # Synthetic state events legitimately refresh `updated_at`; they do not
        # prove worker activity. Heartbeat plus transcript growth is the
        # canonical temporal evidence — the heartbeat stamp alone is written
        # once, at first output, so it ages a working run into `stalled`.
        # A growing transcript is worker activity, never a synthetic refresh.
        payload["health"] = _state_health(
            state,
            _freshest_activity_stamp(
                str(payload.get("heartbeat_at") or ""),
                transcript=str(payload.get("latest_transcript") or ""),
            )
            or str(payload.get("heartbeat_at") or payload.get("updated_at") or ""),
        )
    if not payload.get("liveness"):
        payload["liveness"] = "terminal" if _run_is_terminal(payload) else "heartbeat"
    payload["lifecycle"] = _lifecycle_controls(payload)
    # Settlement axis (f/x/n). Written on every terminal projection so the
    # board never renders silence for unfinished claim→proof work. Delivery
    # kernel axes above stay orthogonal (unverified/sealed ≠ settled).
    if previous:
        for key in (
            "settlement_verdict",
            "settlement_reason",
            "settlement_at",
            "settlement_source",
            "settlement_tui",
            "settlement_waived",
            "settlement_claim_digest",
            "settlement_revision",
            "trust_receipt",
            "settlement",
            "await_rc",
            "await_outcome",
            "await_reason",
            "await_worker_alive",
            "await_settled_at",
            "settlement_waive",
            "operator_waive",
            "claim",
            "mission",
            "brief",
            "claim_digest",
        ):
            if key in previous and key not in payload:
                payload[key] = previous[key]
    nested_settlement = payload.get("settlement")
    projected_claim_digest = str(payload.get("settlement_claim_digest") or "")
    if isinstance(nested_settlement, dict) and not projected_claim_digest:
        projected_claim_digest = str(nested_settlement.get("claim_digest") or "")
    repair_sealed_claim_binding = bool(
        kernel_claim_digest
        and axes.delivery_state is DeliveryState.SEALED
        and projected_claim_digest != kernel_claim_digest
    )
    settlement = settle_payload(payload, force=repair_sealed_claim_binding)
    if settlement is not None:
        payload.update(settlement.to_payload())
        payload["settlement"] = {
            "verdict": settlement.verdict.value,
            "reason": settlement.reason,
            "settled_at": settlement.settled_at,
            "source": settlement.source,
            "claim_digest": settlement.claim_digest,
            "waived": settlement.waived,
            "tui": settlement.tui_key,
        }
    return payload


def sync_state(only_run_id: str | None = None) -> dict[str, Any]:
    """Project on-disk run artifacts into control-plane snapshots.

    ``only_run_id`` scopes the whole pass to one run and its child rounds. The
    scoped path is the hot path (``lookup_run`` / ``await_run`` poll it every few
    seconds): it takes NO global lock and commits each target behind only that
    run's mutation key. That removes the O(runs²) herd while serializing writers
    that can actually conflict. The full board rebuild
    (``only_run_id is None``) stays behind the bounded ``_sync_lock`` and is
    used by dashboards/status-all.
    """
    scope = str(only_run_id or "").strip()
    scoped = bool(scope)
    child_prefix = f"{scope}-" if scope else ""
    storage_maintenance = (
        {"orphan_temps_removed": 0, "transcripts_rotated": 0, "archives_removed": 0}
        if scoped
        else _maintain_runtime_run_storage()
    )

    def _in_scope(run_id: str) -> bool:
        """True when the pass is unscoped, or ``run_id`` is the target run or its child round."""
        return not scoped or run_id == scope or run_id.startswith(child_prefix)

    lock_ctx = contextlib.nullcontext() if scoped else _sync_lock(purpose="board-sync")
    with lock_ctx:
        previous_snapshots = _load_existing_snapshots()
        archived_ids = set() if scoped else _archived_run_ids()
        merged: dict[str, RunStatus] = {}

        # The migraine was the exclusive GLOBAL LOCK, not the file walk: every
        # per-run poll serialised on one flock while rebuilding the whole board.
        # Scoped mode keeps the same on-disk reads (so a run seeded only via its
        # meta/lock is still resolved) but folds ONLY the target + child rounds
        # and writes only their snapshots. Different runs stay parallel; only
        # writers for the same run meet at the final mutation CAS.
        for path in _iter_meta_files():
            status = _normalize_agent_meta(path)
            if status is None or not _in_scope(status.run_id):
                continue
            merged[status.run_id] = _merge_status(merged.get(status.run_id), status)

        for path in _iter_lock_files():
            status = _normalize_lock(path)
            if status is None or not _in_scope(status.run_id):
                continue
            merged[status.run_id] = _merge_status(merged.get(status.run_id), status)

        for path in _iter_marbles_state_files():
            status = _normalize_marbles_state(path)
            if status is None or not _in_scope(status.run_id):
                continue
            merged[status.run_id] = _merge_status(merged.get(status.run_id), status)

        merged = _merge_event_stream(merged, only_run_id=scope if scoped else None)

        payload_runs = []
        run_snapshot_dir().mkdir(parents=True, exist_ok=True)
        for run_id, status in merged.items():
            if not _in_scope(run_id):
                continue
            # Archived runs are closed history: settlement precedes gc, so an
            # archived snapshot already carries its terminal. Rebuilding it from
            # the launcher meta (which outlives the snapshot) resurrected every
            # collected run each sync and made the settlement debt immortal.
            # A demonstrably active status (fresh evidence) still projects.
            if (
                run_id in archived_ids
                and run_id not in previous_snapshots
                and status.health != "active"
            ):
                continue
            previous = previous_snapshots.get(run_id)
            payload = _project_run_payload(run_id, status, previous)
            committed = _write_run_snapshot(
                _snapshot_path(run_id),
                previous,
                payload,
            )
            if committed:
                payload_runs.append(committed)
        if not scoped:
            # Retained snapshots whose source evidence went quiet (e.g. their
            # event lines were rotated away) stay on the board until archived;
            # the snapshot itself is the durable state.
            seen_ids = {str(run.get("run_id") or "") for run in payload_runs}
            for run_id, prev in previous_snapshots.items():
                if run_id not in seen_ids:
                    payload_runs.append(prev)
            _archive_expired_snapshots()
            _rotate_event_stream()

    # A scoped pass only re-projects runs that had fresh events; quiet target/
    # child runs keep their last snapshot, which _select_run reads directly.
    if scoped:
        seen = {str(run.get("run_id") or "") for run in payload_runs}
        for run_id, prev in previous_snapshots.items():
            if _in_scope(run_id) and run_id not in seen:
                payload_runs.append(prev)

    payload_runs.sort(
        key=lambda item: (
            _parse_iso(item.get("updated_at"))
            or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        ),
        reverse=True,
    )
    active_runs = [
        run
        for run in payload_runs
        if run.get("health") == "active" and run.get("state") not in FINAL_STATES
    ]
    stalled_runs = [
        run
        for run in payload_runs
        if run.get("health") == "stalled" and run.get("state") not in FINAL_STATES
    ]
    # Contract rule 6: legacy Untitled*.md under artifacts/ lands as n.
    # Full board only — scoped single-run polls must not pay the rglob cost.
    orphan_runs: list[dict[str, Any]] = []
    if not scoped:
        try:
            orphan_runs = orphan_settlement_payloads(vibecrafted_home() / "artifacts")
        except OSError:
            orphan_runs = []
        if orphan_runs:
            # Count orphans on the settlement axis; surface a short list for
            # the board without drowning recent_runs.
            payload_runs = list(payload_runs) + orphan_runs

    recent_runs = payload_runs[:RECENT_RUN_LIMIT]
    # TUI f/x/n reads the settlement axis only — never exit counters or raw states.
    fxn = board_fxn_counts(payload_runs)
    return {
        "generated_at": _now().isoformat(),
        "storage_maintenance": storage_maintenance,
        "active_runs": active_runs,
        "stalled_runs": stalled_runs,
        "recent_runs": recent_runs,
        "warnings": _warnings_for_runs(payload_runs),
        "events": read_event_tail(),
        "orphan_artifacts": [
            str(run.get("orphan_path") or run.get("report") or "")
            for run in orphan_runs
            if str(run.get("orphan_path") or run.get("report") or "")
        ],
        "settlement_counts": {
            "f": fxn.get("f", 0),
            "x": fxn.get("x", 0),
            "n": fxn.get("n", 0),
            "total_settled": sum(fxn.values()),
            "orphans": len(orphan_runs),
        },
    }


def lookup_run(run_id: str) -> dict[str, Any] | None:
    """Return the current control-plane projection for one run id.

    Lockless single-run path: never triggers the global board rebuild, so a
    per-run lookup no longer serialises behind every other run on the shared
    control-plane lock.
    """
    target = str(run_id or "").strip()
    snapshot = sync_state(only_run_id=target or None)
    return _select_run(snapshot, run_id)


class RunNotResolved(Exception):
    """Raised when a run id maps to no on-disk artifacts yet.

    The actionable case is "still launching" — the dispatcher has accepted the
    run but the worker has not produced its run directory yet. Point the caller
    at ``await`` instead of failing silently.
    """

    def __init__(self, run_id: str) -> None:
        """Store the unresolved run id and format the actionable error message."""
        self.run_id = run_id
        super().__init__(
            f"run {run_id!r} not found in runtime_runs/ or artifacts/ — it may "
            f"still be launching. Wait with: vibecrafted await --run-id {run_id}"
        )


@dataclass(frozen=True)
class ResolvedRun:
    """On-disk location of a run, resolved read-follows-write.

    ``runtime_runs/`` is where the core runtime *writes*; ``artifacts/`` is the
    legacy location older readers expect. One resolver so observe / await / CLI /
    app / MCP read from the same place the runtime wrote (Niezmiennik 3).
    """

    run_id: str
    source: str  # "runtime_runs" | "artifacts"
    run_dir: Path
    meta: Path | None
    transcript: Path | None
    report: Path | None


def _runtime_run_dir(run_id: str) -> Path:
    """Path to the runtime-owned run directory (where the core runtime writes)."""
    return control_plane_home() / "runtime_runs" / run_id


def _report_from_meta(meta: Path) -> Path | None:
    """Resolve the report path recorded in a meta.json, if it still exists on disk."""
    try:
        payload = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = str(payload.get("report") or payload.get("latest_report") or "")
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_file() else None


def _resolve_run_in_artifacts(run_id: str) -> ResolvedRun | None:
    """Legacy fallback resolver: locate a run's meta/transcript/report under
    artifacts/ when it has no runtime_runs/ directory."""
    artifacts_root = vibecrafted_home() / "artifacts"
    if not artifacts_root.is_dir():
        return None
    for meta in sorted(artifacts_root.rglob(f"*{run_id}*.meta.json")):
        transcript = meta.with_suffix("").with_suffix(".transcript.log")
        return ResolvedRun(
            run_id=run_id,
            source="artifacts",
            run_dir=meta.parent,
            meta=meta,
            transcript=transcript if transcript.is_file() else None,
            report=_report_from_meta(meta),
        )
    return None


def resolve_run(run_id: str) -> ResolvedRun:
    """Resolve a run id to its on-disk artifacts, read-follows-write.

    Probes ``runtime_runs/<id>/`` (where the core runtime writes) first, then the
    legacy ``artifacts/`` location, then raises :class:`RunNotResolved` loudly so
    a still-launching run is sent to ``await`` instead of a silent "no metadata".
    This is the single resolver that closes the observe/await split-brain.
    """
    target = str(run_id or "").strip()
    if not target:
        raise ValueError("run_id is required")

    run_dir = _runtime_run_dir(target)
    if run_dir.is_dir():
        meta = run_dir / "meta.json"
        transcript = run_dir / "transcript.log"
        return ResolvedRun(
            run_id=target,
            source="runtime_runs",
            run_dir=run_dir,
            meta=meta if meta.is_file() else None,
            transcript=transcript if transcript.is_file() else None,
            report=_report_from_meta(meta) if meta.is_file() else None,
        )

    legacy = _resolve_run_in_artifacts(target)
    if legacy is not None:
        return legacy

    raise RunNotResolved(target)


def read_delivery_axes(run_id: str) -> DeliveryAxes:
    """Read the three independent state axes for any resolvable run.

    Legacy metadata supplies execution only. Proof and delivery are never
    inferred from ``completed``, report bytes, or ``artifact_ok``: absent proof
    remains ``undeclared`` and absent seal remains ``unverified``.
    """

    resolved = resolve_run(run_id)
    meta_payload = _read_json(resolved.meta) if resolved.meta is not None else {}
    legacy_state = str(
        meta_payload.get("state") or meta_payload.get("status") or "created"
    )
    return _delivery_axes_from_run_dir(
        resolved.run_dir,
        legacy_state=legacy_state,
        exit_code=_coerce_int(meta_payload.get("exit_code")),
    )


def _delivery_axes_from_run_dir(
    run_dir: Path | None,
    *,
    legacy_state: str,
    exit_code: int | None,
) -> DeliveryAxes:
    """Read the proof/delivery axes from a run's on-disk proof artifacts,
    deriving execution state from legacy state/exit_code as the third axis."""
    execution_state = _legacy_execution_state(legacy_state, exit_code)
    proof_state = ProofState.UNDECLARED
    delivery_state = DeliveryState.UNVERIFIED

    if run_dir is not None:
        proof_result_path = run_dir / "proof" / "result.json"
        proof_contract_path = run_dir / "delivery-proof-contract.json"
        if proof_result_path.is_file():
            try:
                proof_state = ProofResult.from_payload(
                    _read_json(proof_result_path)
                ).state
            except (ContractError, TypeError, ValueError):
                proof_state = ProofState.INVALID
        elif proof_contract_path.is_file():
            try:
                DeliveryProofContract.from_payload(_read_json(proof_contract_path))
            except (ContractError, TypeError, ValueError):
                proof_state = ProofState.INVALID
            else:
                proof_state = ProofState.DECLARED

        seal_path = run_dir / "delivery-seal.json"
        if seal_path.is_file():
            try:
                DeliverySeal.from_payload(_read_json(seal_path))
            except (ContractError, TypeError, ValueError):
                delivery_state = DeliveryState.INVALIDATED
            else:
                delivery_state = DeliveryState.SEALED

    return DeliveryAxes(
        execution_state=execution_state,
        proof_state=proof_state,
        delivery_state=delivery_state,
    )


def _kernel_claim_digest_from_run_dir(run_dir: Path | None) -> str:
    """Read the claim digest that the lifecycle proof actually verified.

    ``proof/mission-claim.json`` is materialized only after the validated
    report's digest matches the mission digest. It therefore outranks a
    projection fallback synthesized later from prompt/agent metadata.
    """
    if run_dir is None:
        return ""
    payload = _read_json(run_dir / "proof" / "mission-claim.json")
    return str(payload.get("claim_digest") or "").strip()


def _legacy_execution_state(state: str, exit_code: int | None) -> ExecutionState:
    """Map a legacy lifecycle state (+ exit code) onto the ExecutionState axis."""
    normalized = str(state or "").strip().lower()
    if normalized == "timed_out":
        return ExecutionState.TIMED_OUT
    if normalized in {"interrupted", "partial", "stopped", "gc"}:
        return ExecutionState.INTERRUPTED
    if normalized in {
        "blocked",
        "contract_failed",
        "failed",
        "ghost",
        "process_dead",
        "recovery_required",
        "report_invalid",
    }:
        return ExecutionState.FAILED
    if normalized == "report_missing":
        # Delivering nothing is not the same as delivering garbage.
        # report_invalid stays an execution failure (the recovery lane), but a
        # worker that exited 0 and simply produced no report is the contract's
        # exit_0_without_report specimen — needs_attention, never a fabricated
        # execution failure (x instead of n).
        if exit_code is not None:
            return ExecutionState.EXITED if exit_code == 0 else ExecutionState.FAILED
        return ExecutionState.FAILED
    if exit_code is not None:
        return ExecutionState.EXITED if exit_code == 0 else ExecutionState.FAILED
    if normalized in {"report_validated", "completed", "closed", "converged"}:
        return ExecutionState.EXITED
    if normalized in {"process_spawned", "initialized", "launching", "promise"}:
        return ExecutionState.LAUNCHED
    if normalized in ACTIVE_STATES - {"created"}:
        return ExecutionState.RUNNING
    return ExecutionState.CREATED


def _await_progress_fingerprint(run: dict[str, Any] | None) -> tuple[Any, ...]:
    """Cheap movement signal for liveness-aware waits.

    Any change between two polls is proof the worker is doing real work:
    transcript bytes grew (session-file growth), the lifecycle state advanced,
    a fresh heartbeat landed, or new tokens/commits were recorded. Used by
    :func:`await_run` to RESET its idle deadline instead of abandoning a run on
    a blind wall clock while the agent is demonstrably alive.
    """
    if not run:
        return ()
    return (
        str(run.get("state") or ""),
        str(run.get("liveness") or ""),
        _coerce_int(run.get("transcript_bytes")),
        str(run.get("heartbeat_at") or run.get("updated_at") or ""),
        _coerce_int(run.get("tokens_output")),
        str(run.get("commit") or run.get("commit_sha") or run.get("head_sha") or ""),
    )


def _await_child_runs(snapshot: dict[str, Any], target: str) -> list[dict[str, Any]]:
    """Child runs of a loop parent (marbles/polarize ``<parent>-<kind>-L<n>``).

    Loop parents write almost nothing themselves — the children carry the real
    transcripts and pids, yet they are separate run records linked only by the
    id prefix. An await that fingerprints the parent alone sees a frozen record
    (and possibly a gone pid between rounds) while a child is demonstrably
    working, then fires a false ``idle_stall`` mid-loop.
    """
    prefix = f"{target}-"
    children: list[dict[str, Any]] = []
    for key in ("active_runs", "recent_runs"):
        for run in snapshot.get(key) or []:
            if not isinstance(run, dict):
                continue
            if str(run.get("run_id") or "").startswith(prefix):
                children.append(run)
    return children


def _report_file_written(path: str) -> bool:
    """True when the announced report carries worker evidence, not just bytes.

    Size alone is a false seal. The launcher materializes an identity shell at
    spawn time, so the announced path is non-empty from the run's first second;
    await read that shell as ``report_delivered`` and returned rc=0 the moment
    the worker died, reporting a green handoff for a run that never wrote a
    word. The report contract already brands the untouched shell
    ``report_missing`` — honour that verdict here instead of re-deciding it.

    An honest blocked/partial/failed report is still a delivery: the worker
    spoke. Only the never-touched launcher template is not.
    """
    try:
        if Path(str(path)).stat().st_size <= 0:
            return False
    except OSError:
        return False
    return (
        "report_missing"
        not in validate_report_file(path, require_frontmatter=False).errors
    )


def _resolve_await_hard_cap(hard_cap_seconds: float | None) -> float | None:
    """Optional absolute ceiling for :func:`await_run`.

    Liveness governs by default (``None`` → no wall-clock kill of a live run).
    A hard cap, when set, is the only thing that stops a worker that stays alive
    but never finishes; the operator keeps it far above realistic single-marble
    work. Explicit argument wins; otherwise ``VIBECRAFTED_AWAIT_HARD_CAP_S``.
    """
    if hard_cap_seconds is not None:
        cap = float(hard_cap_seconds)
        return cap if cap > 0 else None
    raw = str(os.environ.get("VIBECRAFTED_AWAIT_HARD_CAP_S") or "").strip()
    if not raw:
        return None
    try:
        cap = float(raw)
    except ValueError:
        return None
    return cap if cap > 0 else None


def _finalize_await_result(
    run_id: str,
    last_run: dict[str, Any] | None,
    *,
    completed: bool,
    timed_out: bool,
    reason: str,
    worker_alive: bool,
    attempts: int,
) -> dict[str, Any]:
    """Build the await return value and persist the supervisor verdict.

    Contract §8: await verdicts (rc + 3-signal outcome + timestamp) are written
    into the run meta so supervisor knowledge survives the supervisor process.
    """
    exit_code = _coerce_int((last_run or {}).get("exit_code"))
    if timed_out and exit_code is None:
        exit_code = 124
    elif completed and exit_code is None:
        exit_code = 0

    outcome = "completed" if completed else ("timed_out" if timed_out else "unknown")
    meta_path: Path | None = None
    run_dir = _runtime_run_dir(run_id)
    if run_dir.is_dir() and (run_dir / "meta.json").is_file():
        meta_path = run_dir / "meta.json"
    elif last_run is not None:
        candidate = str(last_run.get("meta") or "").strip()
        if candidate:
            path = Path(candidate)
            if path.is_file():
                meta_path = path

    await_fields = persist_await_verdict(
        meta_path,
        control_plane_root=control_plane_home(),
        run_id=run_id,
        rc=exit_code,
        outcome=outcome,
        worker_alive=worker_alive,
        reason=reason,
    )

    # Also stamp the board snapshot so TUI/readers see the await verdict without
    # waiting for the next meta-driven projection.
    snapshot_path = _snapshot_path(run_id)
    if snapshot_path.is_file():
        try:
            snapshot = _read_json(snapshot_path)
            if snapshot:
                previous_snapshot = dict(snapshot)
                snapshot.update(await_fields)
                # Re-settle with await evidence so unsealed reports stay n.
                settlement = settle_payload(snapshot, force=True, source="await")
                if settlement is not None:
                    snapshot.update(settlement.to_payload())
                    snapshot["settlement"] = {
                        "verdict": settlement.verdict.value,
                        "reason": settlement.reason,
                        "settled_at": settlement.settled_at,
                        "source": settlement.source,
                        "claim_digest": settlement.claim_digest,
                        "waived": settlement.waived,
                        "tui": settlement.tui_key,
                        "await_rc": exit_code,
                        "await_outcome": outcome,
                    }
                committed = _write_run_snapshot(
                    snapshot_path,
                    previous_snapshot,
                    snapshot,
                )
                if committed:
                    last_run = committed
        except OSError:
            pass

    return {
        "run_id": run_id,
        "found": last_run is not None,
        "completed": completed,
        "timed_out": timed_out,
        "reason": reason,
        "worker_alive": worker_alive,
        "attempts": attempts,
        "run": last_run,
        "await_rc": exit_code,
        "await_outcome": outcome,
        **await_fields,
    }


def await_run(
    run_id: str,
    *,
    timeout_seconds: float = 300,
    interval_seconds: float = 5,
    hard_cap_seconds: float | None = None,
    report_path: str | None = None,
    on_poll: Callable[[dict[str, Any] | None], None] | None = None,
) -> dict[str, Any]:
    """Liveness-aware bounded wait for a run using control-plane state only.

    ``timeout_seconds`` is an IDLE deadline, not a blind wall clock: it RESETS
    whenever the run shows movement (transcript growth, state/heartbeat/token
    advance) or the worker process is demonstrably alive (``_worker_is_alive``).
    A run is only abandoned (``timed_out``) on a genuine stall — the idle window
    elapsing with zero movement AND no live worker — or, when configured, the
    optional ``hard_cap_seconds`` absolute ceiling. This stops the orchestrator
    from killing a marbles loop that is still doing real work (~13 min single
    marble) just because a fixed wall-clock budget expired.

    Two more truths keep this the ONE await nobody has to second-guess:

    - A non-empty report file (``report_path`` argument, else the run's own
      ``latest_report``) from a worker that is GONE is the handoff itself —
      return ``completed`` with ``reason: report_delivered`` immediately
      instead of idling out a full window on the corpse. While the worker is
      alive the report alone never completes the wait: it may be mid-write,
      or a stale leftover from a previous attempt on the same announced path.
    - Movement and liveness aggregate the run's CHILD runs (marbles/polarize
      loop rounds live in separate ``<parent>-…-L<n>`` records), so a working
      child keeps the parent's await open even while the parent record is
      frozen between rounds.

    ``on_poll`` (when given) is called once per poll with the latest run
    projection — for callers that print progress while blocking.
    """
    target = str(run_id or "").strip()
    if not target:
        raise ValueError("run_id is required")

    idle_window = max(float(timeout_seconds), 0.0)
    interval_seconds = max(float(interval_seconds), 0.1)
    hard_cap = _resolve_await_hard_cap(hard_cap_seconds)

    start = time.monotonic()
    idle_deadline = start + idle_window
    hard_deadline = start + hard_cap if hard_cap is not None else None
    previous_fingerprint: tuple[Any, ...] | None = None
    attempts = 0
    last_run: dict[str, Any] | None = None

    while True:
        attempts += 1
        # Scoped to the awaited run (+ its child rounds): a 5s await poll must
        # not rebuild the whole board under the shared lock every tick.
        snapshot = sync_state(only_run_id=target)
        last_run = _select_run(snapshot, target)
        if on_poll is not None:
            on_poll(last_run)
        children = _await_child_runs(snapshot, target)
        worker_alive = bool(
            (last_run is not None and _await_process_is_alive(last_run))
            or any(_await_process_is_alive(child) for child in children)
        )
        if last_run is not None and _run_is_terminal(last_run) and not worker_alive:
            return _finalize_await_result(
                target,
                last_run,
                completed=True,
                timed_out=False,
                reason="terminal",
                worker_alive=False,
                attempts=attempts,
            )

        delivered_report = str(
            report_path or (last_run.get("latest_report") if last_run else "") or ""
        ).strip()
        # Worker death is the handoff seal: a LIVE worker may still be
        # mid-write, and the announced path can hold a stale report from a
        # previous attempt (stage retries reuse artifact paths) — returning
        # `completed` on it reported exit 0 for a still-working run.
        if (
            delivered_report
            and _report_file_written(delivered_report)
            and not worker_alive
        ):
            return _finalize_await_result(
                target,
                last_run,
                completed=True,
                timed_out=False,
                reason="report_delivered",
                worker_alive=False,
                attempts=attempts,
            )

        now = time.monotonic()
        fingerprint = (
            _await_progress_fingerprint(last_run),
            tuple(
                sorted(
                    (str(child.get("run_id") or ""),)
                    + _await_progress_fingerprint(child)
                    for child in children
                )
            ),
        )
        moved = fingerprint != previous_fingerprint
        previous_fingerprint = fingerprint
        # Real activity or a live worker keeps the idle window open: never
        # abandon a run that is demonstrably making progress or alive.
        if moved or worker_alive:
            idle_deadline = now + idle_window

        if hard_deadline is not None and now >= hard_deadline:
            return _finalize_await_result(
                target,
                last_run,
                completed=False,
                timed_out=True,
                reason="hard_cap",
                worker_alive=worker_alive,
                attempts=attempts,
            )
        if now >= idle_deadline and not worker_alive:
            return _finalize_await_result(
                target,
                last_run,
                completed=False,
                timed_out=True,
                reason="idle_stall",
                worker_alive=worker_alive,
                attempts=attempts,
            )

        sleep_for = interval_seconds
        if hard_deadline is not None:
            sleep_for = min(sleep_for, max(hard_deadline - now, 0.0))
        if not worker_alive:
            sleep_for = min(sleep_for, max(idle_deadline - now, 0.0))
        time.sleep(max(sleep_for, 0.0))


def run_liveness(run_id: str) -> dict[str, Any]:
    """Bounded, reconciled liveness projection for a single run.

    Closes the report-on-death gap (docs/runtime/AGENT_OPS.md, Class 2): in
    no-await mode a worker that dies at startup emits no report and no
    terminal state, so passive readers wait on a corpse. This runs the same
    sync/reconcile pass the dispatch verbs use and answers the one question a
    supervisor needs before trusting silence: is the worker demonstrably
    alive, and what state did reconciliation settle on.
    """
    target = str(run_id or "").strip()
    if not target:
        return {"run_id": "", "found": False}
    try:
        # Scoped to the probed run: this is a supervisor-hot single-run probe
        # and must never queue on the global board lock behind a full sync.
        snapshot = sync_state(only_run_id=target)
    except OSError:
        return {"run_id": target, "found": False}
    run = _select_run(snapshot, target)
    if run is None:
        return {"run_id": target, "found": False}
    return {
        "run_id": target,
        "found": True,
        "state": str(run.get("state") or ""),
        "liveness": str(run.get("liveness") or ""),
        "worker_alive": _worker_is_alive(run),
        "recovery_required": bool(run.get("recovery_required")),
    }


def cli(argv: list[str] | None = None) -> int:
    """Standalone ``python -m vibecrafted_core.control_plane`` entrypoint:
    sync/status/drain the control-plane board and print JSON."""
    parser = argparse.ArgumentParser(
        description="Sync and inspect Vibecrafted control-plane state."
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="sync",
        choices=("sync", "status", "drain"),
        help=(
            "sync writes snapshots and prints the aggregate payload; status is "
            "an alias; drain settles and archives parked terminal snapshots."
        ),
    )
    parser.add_argument(
        "--keep-hours",
        type=float,
        default=24.0,
        help="drain: keep terminal snapshots newer than this many hours retained.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="drain: snapshots per lock acquisition (lock released between batches).",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "drain":
            payload: dict[str, Any] = drain_settled_snapshots(
                keep_hours=args.keep_hours, batch_size=args.batch_size
            )
        else:
            payload = sync_state()
    except ControlPlaneStorageError as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(cli())
