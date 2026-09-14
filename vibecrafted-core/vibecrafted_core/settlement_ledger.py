"""Permanent, hash-chained history for authoritative settlement revisions.

The normal control-plane event stream is a notification surface: generations
rotate and old archives are intentionally retained only within bounded limits.
This ledger is different.  It records every authoritative
``SettlementEventV2`` exactly once and is never part of event/snapshot
retention.

The ledger does not derive verdicts or invent missing transitions.
``SettlementEventV2`` remains the transition authority.  On first creation the
ledger may also freeze one observed lower-bound settlement per existing run
snapshot so an upgrade does not erase already visible f/x/n truth.
"""

from __future__ import annotations

import contextlib
import errno
try:
    import fcntl
except ImportError:  # native Windows — flock-shaped portable_lock
    from . import portable_lock as fcntl
import hashlib
import json
import os
import stat
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settlement import SettlementEventV2

SETTLEMENT_LEDGER_SCHEMA = "vibecrafted.settlement-ledger.v1"
SETTLEMENT_LEDGER_METADATA_SCHEMA = "vibecrafted.settlement-ledger-metadata.v1"
SETTLEMENT_LEDGER_ENTRY_SCHEMA = "vibecrafted.settlement-ledger-entry.v1"
SETTLEMENT_LEDGER_SNAPSHOT_SCHEMA = "vibecrafted.settlement-ledger-snapshot.v1"
SETTLEMENT_LEDGER_MAX_LINE_BYTES = 512 * 1024
_ZERO_HASH = "0" * 64

_METADATA_FIELDS = {
    "schema",
    "record_type",
    "ledger_schema",
    "created_at",
    "history_origin",
    "backfill",
    "previous_hash",
    "record_hash",
}
_ENTRY_FIELDS = {
    "schema",
    "record_type",
    "ordinal",
    "previous_hash",
    "event_key",
    "run_id",
    "settlement_revision",
    "settlement_tui",
    "settlement_verdict",
    "settled_at",
    "event",
    "record_hash",
}


class SettlementLedgerError(RuntimeError):
    """Base error for an unsafe or invalid permanent settlement ledger."""


class SettlementLedgerCorrupt(SettlementLedgerError):
    """The durable chain cannot be verified exactly."""


class SettlementLedgerCollision(SettlementLedgerError):
    """A durable identity was reused for a different settlement fact."""


class SettlementLedgerOrderError(SettlementLedgerError):
    """A run attempted to append a non-monotonic settlement revision."""


@dataclass(frozen=True)
class SettlementLedgerAppendResult:
    """Outcome of one durable, idempotent ledger append."""

    event_key: str
    ordinal: int
    record_hash: str
    appended: bool


@dataclass(frozen=True)
class _LedgerState:
    """Fully-parsed, index-built view of the ledger file at one point in time."""

    metadata: dict[str, Any] | None
    records: tuple[dict[str, Any], ...]
    baseline_by_run: dict[str, dict[str, Any]]
    by_event_key: dict[str, dict[str, Any]]
    by_revision: dict[tuple[str, int], dict[str, Any]]
    latest_revision_by_run: dict[str, int]
    chain_head: str


def settlement_ledger_path() -> Path:
    """Return the permanent ledger path under the canonical control plane."""

    from .control_plane import control_plane_home

    return control_plane_home() / "settlement_ledger.jsonl"


def _settlement_ledger_lock_path(path: Path | None = None) -> Path:
    """Return the sibling lock-file path for the given (or default) ledger path."""
    ledger_path = settlement_ledger_path() if path is None else path
    return ledger_path.with_name(".settlement_ledger.lock")


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    """Encode ``payload`` as sorted, compact UTF-8 JSON for stable hashing."""
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _record_hash(payload: Mapping[str, Any]) -> str:
    """Return the SHA-256 hash of ``payload`` with any ``record_hash`` field excluded."""
    unsigned = dict(payload)
    unsigned.pop("record_hash", None)
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _secure_control_plane_home(path: Path | None = None) -> Path:
    """Return the control-plane home dir after verifying ownership and permissions.

    Raises if the directory is not owned by the current user or is
    group/world writable — the ledger's durability guarantees depend on it.
    """
    ledger_path = settlement_ledger_path() if path is None else path
    home = ledger_path.parent
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = home.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise NotADirectoryError(f"control-plane home is not a directory: {home}")
    if metadata.st_uid != os.getuid():
        raise PermissionError("control-plane home is not owned by current user")
    if metadata.st_mode & 0o022:
        raise PermissionError("control-plane home must not be group/world writable")
    return home


def _validate_private_regular_file(fd: int, *, label: str) -> None:
    """Raise unless ``fd`` is a regular, current-user-owned, non-group/world-writable file."""
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(errno.EINVAL, f"{label} is not a regular file")
    if metadata.st_uid != os.getuid():
        raise PermissionError(f"{label} is not owned by current user")
    if metadata.st_mode & 0o022:
        raise PermissionError(f"{label} must not be group/world writable")


@contextmanager
def _settlement_ledger_lock(
    *,
    exclusive: bool,
    path: Path | None = None,
) -> Iterator[None]:
    """Hold the stable ledger boundary without taking the global sync lock."""

    home = _secure_control_plane_home(path)
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(_settlement_ledger_lock_path(path), flags, 0o600)
    try:
        _validate_private_regular_file(fd, label="settlement ledger lock")
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(fd, operation)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    # The lock file may have been created by this call. Persisting the directory
    # entry is cheap and keeps the synchronization authority crash-safe.
    _fsync_directory(home)


def _fsync_directory(path: Path) -> None:
    """Fsync a directory so a preceding rename/create is durable across a crash."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_fd(fd: int) -> bytes:
    """Read the full contents of an open file descriptor from offset 0."""
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _repair_partial_tail_locked(fd: int) -> int:
    """Roll back only an unterminated tail; complete corrupt lines fail later."""

    end = os.lseek(fd, 0, os.SEEK_END)
    if end == 0 or os.pread(fd, 1, end - 1) == b"\n":
        return end

    scan_size = min(end, SETTLEMENT_LEDGER_MAX_LINE_BYTES + 1)
    tail = os.pread(fd, scan_size, end - scan_size)
    newline = tail.rfind(b"\n")
    if newline < 0:
        if end > SETTLEMENT_LEDGER_MAX_LINE_BYTES:
            raise SettlementLedgerCorrupt(
                "unterminated settlement ledger tail exceeds line limit"
            )
        repaired_end = 0
    else:
        repaired_end = end - scan_size + newline + 1
    os.ftruncate(fd, repaired_end)
    os.fsync(fd)
    return repaired_end


def _decode_line(raw: bytes, *, line_number: int) -> dict[str, Any]:
    """Decode one ledger line as a JSON object, raising ``SettlementLedgerCorrupt`` on failure."""
    if len(raw) + 1 > SETTLEMENT_LEDGER_MAX_LINE_BYTES:
        raise SettlementLedgerCorrupt(
            f"settlement ledger line {line_number} exceeds line limit"
        )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettlementLedgerCorrupt(
            f"settlement ledger line {line_number} is invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise SettlementLedgerCorrupt(
            f"settlement ledger line {line_number} is not an object"
        )
    return payload


def _validate_metadata(payload: dict[str, Any]) -> None:
    """Validate the ledger's first-line metadata record's shape, backfill, and hash.

    Raises ``SettlementLedgerCorrupt`` on any structural or hash mismatch.
    """
    if set(payload) != _METADATA_FIELDS:
        raise SettlementLedgerCorrupt("settlement ledger metadata fields invalid")
    if (
        payload.get("schema") != SETTLEMENT_LEDGER_METADATA_SCHEMA
        or payload.get("record_type") != "ledger_metadata"
        or payload.get("ledger_schema") != SETTLEMENT_LEDGER_SCHEMA
        or payload.get("previous_hash") != _ZERO_HASH
    ):
        raise SettlementLedgerCorrupt("settlement ledger metadata contract invalid")
    created_at = payload.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        raise SettlementLedgerCorrupt("settlement ledger creation time invalid")
    history_origin = payload.get("history_origin")
    backfill = payload.get("backfill")
    if history_origin == "ledger_creation_without_backfill":
        valid_backfill = backfill == {
            "status": "not_performed",
            "history_before_ledger": "unknown",
            "facts_invented": False,
        }
    elif history_origin == "observed_snapshot_lower_bound":
        valid_backfill = _validated_observations(backfill) is not None
    else:
        valid_backfill = False
    if not valid_backfill:
        raise SettlementLedgerCorrupt("settlement ledger backfill metadata invalid")
    if payload.get("record_hash") != _record_hash(payload):
        raise SettlementLedgerCorrupt("settlement ledger metadata hash mismatch")


def _validated_event(payload: Any) -> SettlementEventV2:
    """Parse ``payload`` as a ``SettlementEventV2`` and confirm it round-trips exactly.

    Raises ``SettlementLedgerCorrupt`` if the payload is not a valid, canonical
    v2 event — guards against a hand-edited or truncated ledger line.
    """
    from .settlement import SettlementEventV2

    if not isinstance(payload, Mapping):
        raise SettlementLedgerCorrupt("settlement ledger event payload missing")
    try:
        event = SettlementEventV2.from_payload(payload)
    except (TypeError, ValueError) as exc:
        raise SettlementLedgerCorrupt(
            "settlement ledger event payload invalid"
        ) from exc
    if event.to_payload() != dict(payload):
        raise SettlementLedgerCorrupt(
            "settlement ledger event payload is non-canonical"
        )
    return event


def _parse_ledger_bytes(data: bytes) -> _LedgerState:
    """Parse and fully re-verify the raw ledger file bytes into a ``_LedgerState``.

    Re-checks the hash chain, monotonic revisions per run, and duplicate
    identities on every call; raises ``SettlementLedgerCorrupt`` on the first
    violation found.
    """
    if not data:
        return _LedgerState(None, (), {}, {}, {}, {}, _ZERO_HASH)
    if not data.endswith(b"\n"):
        raise SettlementLedgerCorrupt("settlement ledger has an unterminated tail")

    raw_lines = data[:-1].split(b"\n")
    if not raw_lines:
        return _LedgerState(None, (), {}, {}, {}, {}, _ZERO_HASH)
    metadata = _decode_line(raw_lines[0], line_number=1)
    _validate_metadata(metadata)
    chain_head = str(metadata["record_hash"])
    records: list[dict[str, Any]] = []
    baseline_by_run = _metadata_observations(metadata)
    by_event_key: dict[str, dict[str, Any]] = {}
    by_revision: dict[tuple[str, int], dict[str, Any]] = {}
    latest_revision_by_run = {
        run_id: int(observation["settlement_revision"])
        for run_id, observation in baseline_by_run.items()
    }

    for ordinal, raw in enumerate(raw_lines[1:], start=1):
        line_number = ordinal + 1
        record = _decode_line(raw, line_number=line_number)
        if set(record) != _ENTRY_FIELDS:
            raise SettlementLedgerCorrupt(
                f"settlement ledger entry {ordinal} fields invalid"
            )
        if (
            record.get("schema") != SETTLEMENT_LEDGER_ENTRY_SCHEMA
            or record.get("record_type") != "settlement_transition"
            or record.get("ordinal") != ordinal
            or record.get("previous_hash") != chain_head
            or record.get("record_hash") != _record_hash(record)
        ):
            raise SettlementLedgerCorrupt(
                f"settlement ledger entry {ordinal} chain invalid"
            )
        event = _validated_event(record.get("event"))
        identity = (event.run_id, event.revision)
        if (
            record.get("event_key") != event.event_key
            or record.get("run_id") != event.run_id
            or record.get("settlement_revision") != event.revision
            or record.get("settlement_tui") != event.current.tui
            or record.get("settlement_verdict") != event.current.verdict
            or record.get("settled_at") != event.settled_at
        ):
            raise SettlementLedgerCorrupt(
                f"settlement ledger entry {ordinal} identity invalid"
            )
        if event.current.tui not in {"f", "x", "n"}:
            raise SettlementLedgerCorrupt(
                f"settlement ledger entry {ordinal} has invalid tui key"
            )
        if event.event_key in by_event_key or identity in by_revision:
            raise SettlementLedgerCorrupt(
                f"settlement ledger entry {ordinal} duplicates durable identity"
            )
        previous_revision = latest_revision_by_run.get(event.run_id, 0)
        baseline = baseline_by_run.get(event.run_id)
        upgrades_matching_baseline = bool(
            baseline
            and event.revision == baseline["settlement_revision"]
            and event.current.tui == baseline["settlement_tui"]
            and identity not in by_revision
        )
        if (
            previous_revision
            and event.revision <= previous_revision
            and not (upgrades_matching_baseline)
        ):
            raise SettlementLedgerCorrupt(
                f"settlement ledger entry {ordinal} is non-monotonic"
            )
        records.append(record)
        by_event_key[event.event_key] = record
        by_revision[identity] = record
        latest_revision_by_run[event.run_id] = event.revision
        chain_head = str(record["record_hash"])

    return _LedgerState(
        metadata=metadata,
        records=tuple(records),
        baseline_by_run=baseline_by_run,
        by_event_key=by_event_key,
        by_revision=by_revision,
        latest_revision_by_run=latest_revision_by_run,
        chain_head=chain_head,
    )


def _snapshot_candidates(root: Path) -> tuple[Path, ...]:
    """List active and archived run-snapshot JSON files under ``root/runs``."""
    runs = root / "runs"
    return tuple(sorted(runs.glob("*.json"))) + tuple(
        sorted((runs / "archive").glob("*.json"))
    )


def _snapshot_observations(root: Path) -> tuple[dict[str, Any], ...]:
    """Scan run snapshots for the highest-revision settlement per run_id.

    Used once, at ledger creation, to freeze a lower-bound pre-ledger baseline.
    Raises ``SettlementLedgerCollision`` if two snapshots disagree at the same
    revision for the same run.
    """
    by_run: dict[str, dict[str, Any]] = {}
    for path in _snapshot_candidates(root):
        try:
            encoded = path.read_bytes()
            payload = json.loads(encoded)
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError) as exc:
            raise SettlementLedgerCorrupt(
                f"cannot read settlement bootstrap snapshot {path}: {exc}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise SettlementLedgerCorrupt(
                f"settlement bootstrap snapshot is not an object: {path}"
            )
        settlement = payload.get("settlement")
        if not isinstance(settlement, Mapping):
            continue
        tui = str(settlement.get("tui") or "")
        if tui not in {"f", "x", "n"}:
            continue
        run_id = str(payload.get("run_id") or path.stem).strip()
        if not run_id:
            continue
        revision = settlement.get("revision", 1)
        if type(revision) is not int or revision <= 0:
            raise SettlementLedgerCorrupt(
                f"settlement bootstrap revision is invalid: {path}"
            )
        observation = {
            "run_id": run_id,
            "settlement_revision": revision,
            "settlement_tui": tui,
        }
        prior = by_run.get(run_id)
        if prior is None or revision > prior["settlement_revision"]:
            by_run[run_id] = observation
        elif revision == prior["settlement_revision"] and observation != prior:
            raise SettlementLedgerCollision(
                f"divergent settlement bootstrap snapshot: {run_id}:r{revision}"
            )
    return tuple(by_run[run_id] for run_id in sorted(by_run))


def _observation_manifest(observations: Sequence[Mapping[str, Any]]) -> str:
    """Return the SHA-256 manifest hash binding a canonical observation list."""
    return hashlib.sha256(
        _canonical_json({"observations": list(observations)})
    ).hexdigest()


def _validated_observations(backfill: object) -> tuple[dict[str, Any], ...] | None:
    """Validate a backfill block's observed pre-ledger baseline, or return None if invalid.

    Enforces strictly ascending ``run_id`` ordering and a matching manifest
    hash — this is the sole trusted decode path for baseline observations.
    """
    if not isinstance(backfill, Mapping) or set(backfill) != {
        "status",
        "history_before_ledger",
        "facts_invented",
        "source",
        "observation_count",
        "manifest_sha256",
        "observations",
    }:
        return None
    observations = backfill.get("observations")
    if (
        backfill.get("status") != "observed_snapshot_lower_bound"
        or backfill.get("history_before_ledger") != "unknown"
        or backfill.get("facts_invented") is not False
        or backfill.get("source") != "control_plane_run_snapshots"
        or not isinstance(observations, list)
        or backfill.get("observation_count") != len(observations)
    ):
        return None
    canonical: list[dict[str, Any]] = []
    previous_run_id = ""
    for item in observations:
        if not isinstance(item, Mapping) or set(item) != {
            "run_id",
            "settlement_revision",
            "settlement_tui",
        }:
            return None
        run_id = item.get("run_id")
        revision = item.get("settlement_revision")
        tui = item.get("settlement_tui")
        if (
            not isinstance(run_id, str)
            or not run_id
            or run_id <= previous_run_id
            or type(revision) is not int
            or revision <= 0
            or tui not in {"f", "x", "n"}
        ):
            return None
        canonical.append(dict(item))
        previous_run_id = run_id
    if backfill.get("manifest_sha256") != _observation_manifest(canonical):
        return None
    return tuple(canonical)


def _metadata_observations(metadata: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the metadata record's baseline observations keyed by run_id."""
    observations = _validated_observations(metadata.get("backfill"))
    if observations is None:
        return {}
    return {str(item["run_id"]): item for item in observations}


def _metadata_record(
    observations: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build the ledger's first-line metadata record, hashing in its own record_hash."""
    canonical_observations = [dict(item) for item in observations]
    if canonical_observations:
        history_origin = "observed_snapshot_lower_bound"
        backfill: dict[str, Any] = {
            "status": "observed_snapshot_lower_bound",
            "history_before_ledger": "unknown",
            "facts_invented": False,
            "source": "control_plane_run_snapshots",
            "observation_count": len(canonical_observations),
            "manifest_sha256": _observation_manifest(canonical_observations),
            "observations": canonical_observations,
        }
    else:
        history_origin = "ledger_creation_without_backfill"
        backfill = {
            "status": "not_performed",
            "history_before_ledger": "unknown",
            "facts_invented": False,
        }
    payload: dict[str, Any] = {
        "schema": SETTLEMENT_LEDGER_METADATA_SCHEMA,
        "record_type": "ledger_metadata",
        "ledger_schema": SETTLEMENT_LEDGER_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "history_origin": history_origin,
        "backfill": backfill,
        "previous_hash": _ZERO_HASH,
    }
    payload["record_hash"] = _record_hash(payload)
    return payload


def _entry_record(
    event: SettlementEventV2,
    *,
    ordinal: int,
    previous_hash: str,
) -> dict[str, Any]:
    """Build one hash-chained ledger entry record for a settlement transition."""
    payload: dict[str, Any] = {
        "schema": SETTLEMENT_LEDGER_ENTRY_SCHEMA,
        "record_type": "settlement_transition",
        "ordinal": ordinal,
        "previous_hash": previous_hash,
        "event_key": event.event_key,
        "run_id": event.run_id,
        "settlement_revision": event.revision,
        "settlement_tui": event.current.tui,
        "settlement_verdict": event.current.verdict,
        "settled_at": event.settled_at,
        "event": event.to_payload(),
    }
    payload["record_hash"] = _record_hash(payload)
    return payload


def _encoded_record(payload: Mapping[str, Any]) -> bytes:
    """Encode a record as a canonical-JSON line, enforcing the per-line byte limit."""
    line = _canonical_json(payload) + b"\n"
    if len(line) > SETTLEMENT_LEDGER_MAX_LINE_BYTES:
        raise ValueError(
            "settlement ledger line exceeds "
            f"{SETTLEMENT_LEDGER_MAX_LINE_BYTES} byte contract"
        )
    return line


def _append_line_locked(fd: int, line: bytes) -> None:
    """Append and fsync one line; caller must hold the exclusive ledger lock.

    Truncates back to the pre-write offset on any failure (short write or
    exception) so a partial append never survives.
    """
    start = os.lseek(fd, 0, os.SEEK_END)
    try:
        written = os.write(fd, line)
        if written != len(line):
            raise OSError(errno.EIO, "short settlement ledger append")
        os.fsync(fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.ftruncate(fd, start)
            os.fsync(fd)
        raise


def _ensure_metadata_locked(fd: int, path: Path) -> _LedgerState:
    """Return the parsed ledger state, writing the metadata line first if absent.

    Caller must hold the exclusive ledger lock.
    """
    state = _parse_ledger_bytes(_read_fd(fd))
    if state.metadata is not None:
        return state
    metadata = _metadata_record(_snapshot_observations(path.parent))
    _append_line_locked(fd, _encoded_record(metadata))
    return _parse_ledger_bytes(_read_fd(fd))


def initialize_settlement_ledger(path: Path | None = None) -> dict[str, Any]:
    """Create the immutable lower-bound baseline exactly once.

    Existing active/archive snapshots are observations, not reconstructed
    transitions.  The hash-chained metadata freezes one highest-revision
    ``run_id -> tui`` fact and an aggregate manifest before normal V2 appends.
    """

    resolved_path = settlement_ledger_path() if path is None else path
    with _settlement_ledger_lock(exclusive=True, path=resolved_path):
        created = not resolved_path.exists()
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(resolved_path, flags, 0o600)
        try:
            _validate_private_regular_file(fd, label="settlement ledger")
            _repair_partial_tail_locked(fd)
            state = _ensure_metadata_locked(fd, resolved_path)
            os.fsync(fd)
            if created:
                _fsync_directory(resolved_path.parent)
            return dict(state.metadata or {})
        finally:
            os.close(fd)


def _append_settlement_fact(
    event: SettlementEventV2,
) -> SettlementLedgerAppendResult:
    """Durably append one V2 fact or return its exact idempotent prior record.

    ``event_key`` and ``(run_id, revision)`` are both unique. Replaying the
    exact canonical event is a no-op; reusing either identity for different
    content fails closed.
    """

    validated = _validated_event(event.to_payload())
    path = settlement_ledger_path()
    with _settlement_ledger_lock(exclusive=True, path=path):
        created = not path.exists()
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            _validate_private_regular_file(fd, label="settlement ledger")
            _repair_partial_tail_locked(fd)
            state = _ensure_metadata_locked(fd, path)

            prior_by_key = state.by_event_key.get(validated.event_key)
            if prior_by_key is not None:
                if prior_by_key.get("event") != validated.to_payload():
                    raise SettlementLedgerCollision(
                        f"settlement event key collision: {validated.event_key}"
                    )
                os.fsync(fd)
                return SettlementLedgerAppendResult(
                    event_key=validated.event_key,
                    ordinal=int(prior_by_key["ordinal"]),
                    record_hash=str(prior_by_key["record_hash"]),
                    appended=False,
                )

            identity = (validated.run_id, validated.revision)
            prior_by_revision = state.by_revision.get(identity)
            if prior_by_revision is not None:
                raise SettlementLedgerCollision(
                    "settlement revision collision: "
                    f"{validated.run_id}:{validated.revision}"
                )
            latest_revision = state.latest_revision_by_run.get(validated.run_id, 0)
            baseline = state.baseline_by_run.get(validated.run_id)
            upgrades_matching_baseline = bool(
                baseline
                and validated.revision == baseline["settlement_revision"]
                and validated.current.tui == baseline["settlement_tui"]
            )
            if (
                latest_revision
                and validated.revision <= latest_revision
                and not (upgrades_matching_baseline)
            ):
                raise SettlementLedgerOrderError(
                    "non-monotonic settlement revision: "
                    f"{validated.run_id}:{validated.revision} <= {latest_revision}"
                )

            record = _entry_record(
                validated,
                ordinal=len(state.records) + 1,
                previous_hash=state.chain_head,
            )
            _append_line_locked(fd, _encoded_record(record))
            if created:
                _fsync_directory(path.parent)
            return SettlementLedgerAppendResult(
                event_key=validated.event_key,
                ordinal=int(record["ordinal"]),
                record_hash=str(record["record_hash"]),
                appended=True,
            )
        finally:
            os.close(fd)


def _history_gaps(state: _LedgerState) -> list[dict[str, Any]]:
    """Derive gap evidence: pre-ledger unknown history plus any missing revision runs."""
    backfill_status = str(
        ((state.metadata or {}).get("backfill") or {}).get("status") or "not_performed"
    )
    gaps: list[dict[str, Any]] = [
        {
            "kind": "preledger_history_unknown",
            "backfill_status": backfill_status,
            "facts_invented": False,
        }
    ]
    revisions: dict[str, list[int]] = {}
    for record in state.records:
        revisions.setdefault(str(record["run_id"]), []).append(
            int(record["settlement_revision"])
        )
    for run_id in sorted(revisions):
        baseline = state.baseline_by_run.get(run_id)
        expected = (
            int(baseline["settlement_revision"]) + 1 if baseline is not None else 1
        )
        for revision in revisions[run_id]:
            if baseline is not None and revision == baseline["settlement_revision"]:
                continue
            if revision > expected:
                gaps.append(
                    {
                        "kind": "missing_settlement_revisions",
                        "run_id": run_id,
                        "from_revision": expected,
                        "to_revision": revision - 1,
                        "count": revision - expected,
                    }
                )
            expected = revision + 1
    return gaps


def _snapshot_payload(state: _LedgerState) -> dict[str, Any]:
    """Build the public read-side snapshot payload (historical + latest-by-run + gaps)."""
    historical_counts = {"f": 0, "x": 0, "n": 0}
    historical_transitions: list[dict[str, Any]] = []
    latest_by_run: dict[str, dict[str, Any]] = {}
    upgraded_baselines = {
        (str(record["run_id"]), int(record["settlement_revision"]))
        for record in state.records
    }
    for run_id, observation in state.baseline_by_run.items():
        baseline_record = {
            "record_type": "observed_preledger_settlement",
            **observation,
        }
        latest_by_run[run_id] = baseline_record
        identity = (run_id, int(observation["settlement_revision"]))
        if identity not in upgraded_baselines:
            historical_transitions.append(baseline_record)
            historical_counts[str(observation["settlement_tui"])] += 1
    for record in state.records:
        historical_transitions.append(dict(record))
        historical_counts[str(record["settlement_tui"])] += 1
        latest_by_run[str(record["run_id"])] = dict(record)
    latest_by_run = {run_id: latest_by_run[run_id] for run_id in sorted(latest_by_run)}
    latest_counts = {"f": 0, "x": 0, "n": 0}
    for record in latest_by_run.values():
        latest_counts[str(record["settlement_tui"])] += 1
    historical_counts["total"] = len(historical_transitions)
    latest_counts["total"] = len(latest_by_run)
    backfill_status = (
        str((state.metadata or {}).get("backfill", {}).get("status") or "")
        or "ledger_not_started"
    )
    gaps = (
        _history_gaps(state)
        if state.metadata is not None
        else [
            {
                "kind": "ledger_not_started",
                "backfill_status": "not_performed",
                "facts_invented": False,
            }
        ]
    )
    return {
        "schema": SETTLEMENT_LEDGER_SNAPSHOT_SCHEMA,
        "ledger_schema": SETTLEMENT_LEDGER_SCHEMA,
        "metadata": dict(state.metadata) if state.metadata is not None else None,
        "integrity": {
            "valid": True,
            "chain_head": state.chain_head,
            "transition_records": len(historical_transitions),
            "historical_coverage": (
                "observed_preledger_lower_bound_plus_v2"
                if state.baseline_by_run
                else "from_first_observed_v2_event_only"
            ),
            "history_complete": False,
            "backfill_status": backfill_status,
        },
        "historical_transitions": historical_transitions,
        "latest_by_run": latest_by_run,
        "counts": {
            "historical_transitions": historical_counts,
            "latest_by_run": latest_counts,
        },
        "history_gaps": gaps,
    }


def read_settlement_ledger(path: Path | None = None) -> dict[str, Any]:
    """Read and verify the entire immutable history, returning stable aggregates."""

    resolved_path = settlement_ledger_path() if path is None else path
    with _settlement_ledger_lock(exclusive=False, path=resolved_path):
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(resolved_path, flags)
        except FileNotFoundError:
            return _snapshot_payload(_LedgerState(None, (), {}, {}, {}, {}, _ZERO_HASH))
        try:
            _validate_private_regular_file(fd, label="settlement ledger")
            state = _parse_ledger_bytes(_read_fd(fd))
        finally:
            os.close(fd)
    return _snapshot_payload(state)


__all__ = [
    "SETTLEMENT_LEDGER_ENTRY_SCHEMA",
    "SETTLEMENT_LEDGER_METADATA_SCHEMA",
    "SETTLEMENT_LEDGER_SCHEMA",
    "SETTLEMENT_LEDGER_SNAPSHOT_SCHEMA",
    "SettlementLedgerAppendResult",
    "SettlementLedgerCollision",
    "SettlementLedgerCorrupt",
    "SettlementLedgerError",
    "SettlementLedgerOrderError",
    "initialize_settlement_ledger",
    "read_settlement_ledger",
    "settlement_ledger_path",
]
