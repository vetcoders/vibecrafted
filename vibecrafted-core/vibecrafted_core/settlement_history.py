"""Derived settlement counts and best-effort vc-frame publication.

``settlement_ledger.jsonl`` is the only permanent f/x/n authority.  This module
materializes a replaceable projection from the verified ledger snapshot and
delivers that projection to vc-frame.  Run snapshots, archives, event streams,
and this module's own JSON files are never settlement authorities.
"""

from __future__ import annotations

import contextlib
import errno
try:
    import fcntl
except ImportError:  # native Windows — flock-shaped portable_lock
    from . import portable_lock as fcntl
import json
import logging
import os
import shutil
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime_paths import vibecrafted_home
from .settlement_ledger import (
    SETTLEMENT_LEDGER_SCHEMA,
    SETTLEMENT_LEDGER_SNAPSHOT_SCHEMA,
    initialize_settlement_ledger,
    read_settlement_ledger,
)

SETTLEMENT_HISTORY_SCHEMA = "vibecrafted.settlement-history.v2"
SETTLEMENT_HISTORY_WIRE_SCHEMA = "vibecrafted.settlement-history.v1"
SETTLEMENT_HISTORY_GENERATION_SCHEMA = "vibecrafted.settlement-history-generation.v2"
_LEGACY_SETTLEMENT_HISTORY_GENERATION_SCHEMA = (
    "vibecrafted.settlement-history-generation.v1"
)
DELIVERY_OUTBOX_SCHEMA = "vibecrafted.settlement-history-delivery.v1"
SETTLEMENT_COUNTS_PIPE = "vc_settlement_counts"
SETTLEMENT_REPLAY_INTERVAL_SECONDS = 5.0
# Triage bucket sessions are output-only transfer destinations. They never host
# the session-manager plugin, and legacy vc-frame servers falsely report their
# CliPipe dispatch as successful before logging an asynchronous timeout.
_NON_PLUGIN_SESSION_NAMES = frozenset(
    {
        "Failed runs",
        "Finalized runs",
        "Needs attention",
    }
)
SETTLEMENT_HISTORY_AUTHORITY = "settlement_ledger"
SETTLEMENT_COUNT_SEMANTICS_EXACT = "exact"
SETTLEMENT_COUNT_SEMANTICS_LOWER_BOUND = "known_v2_lower_bound"
MAX_U64 = (1 << 64) - 1
_TUI_KEYS = ("f", "x", "n")
LOGGER = logging.getLogger(__name__)


class SettlementHistoryError(RuntimeError):
    """A persisted settlement-history invariant was violated."""


@dataclass(frozen=True)
class SettlementCounts:
    """Immutable f/x/n bucket counts, validated to stay within the u64 contract."""

    f: int = 0
    x: int = 0
    n: int = 0

    def __post_init__(self) -> None:
        """Refuse negative, non-int, or u64-overflowing bucket values."""
        values = (self.f, self.x, self.n)
        if any(type(value) is not int or not 0 <= value <= MAX_U64 for value in values):
            raise SettlementHistoryError("settlement counts exceed the u64 contract")
        if sum(values) > MAX_U64:
            raise SettlementHistoryError("settlement counts total exceeds u64")

    @property
    def total(self) -> int:
        """Return the sum of all three buckets."""
        return self.f + self.x + self.n

    def increment(self, tui: str) -> SettlementCounts:
        """Return a new ``SettlementCounts`` with one tui bucket incremented by one."""
        if tui not in _TUI_KEYS:
            raise SettlementHistoryError(f"invalid settlement tui {tui!r}")
        return SettlementCounts(
            f=self.f + (tui == "f"),
            x=self.x + (tui == "x"),
            n=self.n + (tui == "n"),
        )

    def to_payload(self) -> dict[str, int]:
        """Serialize the four-field f/x/n/total wire shape."""
        return {"f": self.f, "x": self.x, "n": self.n, "total": self.total}

    @classmethod
    def from_payload(cls, payload: object) -> SettlementCounts:
        """Parse and validate a payload's f/x/n/total shape, checking total agrees."""
        if not isinstance(payload, Mapping) or set(payload) != {
            "f",
            "x",
            "n",
            "total",
        }:
            raise SettlementHistoryError("settlement counts shape is invalid")
        f = payload.get("f")
        x = payload.get("x")
        n = payload.get("n")
        total = payload.get("total")
        if any(
            type(value) is not int or not 0 <= value <= MAX_U64
            for value in (f, x, n, total)
        ):
            raise SettlementHistoryError("settlement counts must be u64 integers")
        assert isinstance(f, int)
        assert isinstance(x, int)
        assert isinstance(n, int)
        assert isinstance(total, int)
        counts = cls(f=f, x=x, n=n)
        if total != counts.total:
            raise SettlementHistoryError("settlement counts total is invalid")
        return counts


@dataclass(frozen=True)
class SettlementHistorySnapshot:
    """A verified projection of the settlement ledger, generation-scoped and monotonic."""

    generation: str
    sequence: int
    historical_transitions: SettlementCounts
    latest_by_run: SettlementCounts
    gaps: int
    complete_from: int | None
    count_semantics: str
    history_complete: bool
    history_gaps: tuple[dict[str, object], ...]

    def to_payload(self) -> dict[str, object]:
        """Serialize the full rich document, including gap evidence and semantics."""
        return {
            "schema": SETTLEMENT_HISTORY_SCHEMA,
            "authority": SETTLEMENT_HISTORY_AUTHORITY,
            "generation": self.generation,
            "sequence": self.sequence,
            "historical_transitions": self.historical_transitions.to_payload(),
            "latest_by_run": self.latest_by_run.to_payload(),
            "gaps": self.gaps,
            "complete_from": self.complete_from,
            "count_semantics": self.count_semantics,
            "history_complete": self.history_complete,
            "history_gaps": [dict(gap) for gap in self.history_gaps],
        }

    def to_json(self) -> str:
        """Serialize ``to_payload`` as canonical (sorted, compact) JSON."""
        return json.dumps(
            self.to_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def to_wire_payload(self) -> dict[str, object]:
        """Return the stable seven-field contract consumed by vc-frame."""

        return {
            "schema": SETTLEMENT_HISTORY_WIRE_SCHEMA,
            "generation": self.generation,
            "sequence": self.sequence,
            "historical_transitions": self.historical_transitions.to_payload(),
            "latest_by_run": self.latest_by_run.to_payload(),
            "gaps": self.gaps,
            "complete_from": self.complete_from,
        }

    def to_wire_json(self) -> str:
        """Serialize ``to_wire_payload`` as canonical (sorted, compact) JSON."""
        return json.dumps(
            self.to_wire_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_payload(cls, payload: object) -> SettlementHistorySnapshot:
        """Parse and fully re-verify a rich document's shape and internal invariants.

        Raises ``SettlementHistoryError`` on any structural, semantic, or
        cross-field inconsistency (e.g. gap counts, completeness flags, bucket
        sums) — this is the sole trusted decode path for a persisted snapshot.
        """
        if not isinstance(payload, Mapping) or set(payload) != {
            "schema",
            "authority",
            "generation",
            "sequence",
            "historical_transitions",
            "latest_by_run",
            "gaps",
            "complete_from",
            "count_semantics",
            "history_complete",
            "history_gaps",
        }:
            raise SettlementHistoryError("settlement history document shape is invalid")
        if payload.get("schema") != SETTLEMENT_HISTORY_SCHEMA:
            raise SettlementHistoryError(
                "settlement history document schema is invalid"
            )
        if payload.get("authority") != SETTLEMENT_HISTORY_AUTHORITY:
            raise SettlementHistoryError(
                "settlement history authority is not the permanent ledger"
            )
        generation = payload.get("generation")
        if not isinstance(generation, str):
            raise SettlementHistoryError("settlement history generation is invalid")
        try:
            canonical_generation = str(uuid.UUID(generation))
        except ValueError as exc:
            raise SettlementHistoryError(
                "settlement history generation is invalid"
            ) from exc
        if canonical_generation != generation:
            raise SettlementHistoryError(
                "settlement history generation is not canonical"
            )
        sequence = payload.get("sequence")
        gaps = payload.get("gaps")
        complete_from = payload.get("complete_from")
        count_semantics = payload.get("count_semantics")
        history_complete = payload.get("history_complete")
        if type(sequence) is not int or not 0 <= sequence <= MAX_U64:
            raise SettlementHistoryError("settlement history sequence is invalid")
        if type(gaps) is not int or not 0 <= gaps <= MAX_U64:
            raise SettlementHistoryError("settlement history gaps are invalid")
        if type(history_complete) is not bool:
            raise SettlementHistoryError(
                "settlement history completeness flag is invalid"
            )
        if not isinstance(count_semantics, str):
            raise SettlementHistoryError(
                "settlement history count semantics are invalid"
            )
        history_gaps = _canonical_history_gaps(payload.get("history_gaps"))
        if gaps != _history_gap_units(history_gaps):
            raise SettlementHistoryError(
                "settlement history gap count does not match gap evidence"
            )
        if complete_from is not None and (
            type(complete_from) is not int or not 0 < complete_from <= MAX_U64
        ):
            raise SettlementHistoryError("settlement history complete_from is invalid")
        historical = SettlementCounts.from_payload(
            payload.get("historical_transitions")
        )
        latest = SettlementCounts.from_payload(payload.get("latest_by_run"))
        if historical.total != sequence:
            raise SettlementHistoryError("sequence must equal known transition count")
        if any(
            getattr(latest, bucket) > getattr(historical, bucket)
            for bucket in _TUI_KEYS
        ):
            raise SettlementHistoryError(
                "latest run bucket exceeds its known transitions"
            )
        if gaps and complete_from is not None:
            raise SettlementHistoryError("incomplete history cannot claim completeness")
        if sequence == 0 and complete_from is not None:
            raise SettlementHistoryError("empty history cannot claim completeness")
        if history_complete and gaps:
            raise SettlementHistoryError("complete history cannot contain gaps")
        if history_complete and count_semantics != SETTLEMENT_COUNT_SEMANTICS_EXACT:
            raise SettlementHistoryError("complete history must use exact semantics")
        if not history_complete and (
            count_semantics != SETTLEMENT_COUNT_SEMANTICS_LOWER_BOUND
            or complete_from is not None
            or not history_gaps
        ):
            raise SettlementHistoryError(
                "incomplete history must expose lower-bound gap evidence"
            )
        if sequence > 0 and history_complete and complete_from != 1:
            raise SettlementHistoryError("complete history must start at transition 1")
        return cls(
            generation=generation,
            sequence=sequence,
            historical_transitions=historical,
            latest_by_run=latest,
            gaps=gaps,
            complete_from=complete_from,
            count_semantics=count_semantics,
            history_complete=history_complete,
            history_gaps=history_gaps,
        )


@dataclass(frozen=True)
class DeliveryReport:
    """Outcome of one publisher flush attempt against vc-frame plugin sessions."""

    attempted_sessions: tuple[str, ...] = ()
    delivered_sessions: tuple[str, ...] = ()
    failed_sessions: tuple[str, ...] = ()
    deferred_sessions: tuple[str, ...] = ()
    pending: bool = False
    reason: str = ""


@dataclass(frozen=True)
class _GenerationState:
    """Tracks which projection lineage (generation) is currently authoritative."""

    generation: str
    continuity_gaps: int
    legacy: bool = False

    def to_payload(self) -> dict[str, object]:
        """Serialize the generation marker written to disk."""
        return {
            "schema": SETTLEMENT_HISTORY_GENERATION_SCHEMA,
            "authority": SETTLEMENT_HISTORY_AUTHORITY,
            "generation": self.generation,
            "continuity_gaps": self.continuity_gaps,
        }


def _canonical_history_gaps(
    payload: object,
) -> tuple[dict[str, object], ...]:
    """Validate and canonicalize a history-gap-evidence list, one kind per entry.

    Raises ``SettlementHistoryError`` if the shape or any known gap kind's
    required fields are missing or malformed, or the kind is unrecognized.
    """
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise SettlementHistoryError("settlement history gap evidence is invalid")
    canonical: list[dict[str, object]] = []
    for raw_gap in payload:
        if not isinstance(raw_gap, Mapping):
            raise SettlementHistoryError("settlement history gap evidence is invalid")
        gap = dict(raw_gap)
        kind = gap.get("kind")
        if kind in {"preledger_history_unknown", "ledger_not_started"}:
            allowed_statuses = (
                {"not_performed", "observed_snapshot_lower_bound"}
                if kind == "preledger_history_unknown"
                else {"not_performed"}
            )
            if (
                set(gap) != {"kind", "backfill_status", "facts_invented"}
                or gap.get("backfill_status") not in allowed_statuses
                or gap.get("facts_invented") is not False
            ):
                raise SettlementHistoryError(
                    "settlement pre-ledger gap evidence is invalid"
                )
        elif kind == "missing_settlement_revisions":
            if set(gap) != {
                "kind",
                "run_id",
                "from_revision",
                "to_revision",
                "count",
            }:
                raise SettlementHistoryError(
                    "settlement revision gap evidence is invalid"
                )
            run_id = gap.get("run_id")
            first = gap.get("from_revision")
            last = gap.get("to_revision")
            count = gap.get("count")
            if (
                not isinstance(run_id, str)
                or not run_id
                or type(first) is not int
                or type(last) is not int
                or type(count) is not int
                or not 0 < first <= last <= MAX_U64
                or count != last - first + 1
            ):
                raise SettlementHistoryError(
                    "settlement revision gap evidence is invalid"
                )
        else:
            raise SettlementHistoryError(
                f"unknown settlement history gap kind: {kind!r}"
            )
        canonical.append(gap)
    return tuple(canonical)


def _history_gap_units(history_gaps: Sequence[Mapping[str, object]]) -> int:
    """Sum the per-gap ``count`` fields (defaulting to 1) into a total gap unit count."""
    units = 0
    for gap in history_gaps:
        count = gap.get("count")
        units += count if type(count) is int else 1
        if units > MAX_U64:
            raise SettlementHistoryError("settlement history gaps exceed u64")
    return units


def _ledger_projection(
    ledger: object,
    *,
    generation: str,
) -> SettlementHistorySnapshot:
    """Verify a raw ledger snapshot and project it into a ``SettlementHistorySnapshot``.

    Cross-checks declared counts against the actual transition/latest-run
    records before trusting them; raises ``SettlementHistoryError`` on any
    shape or bucket-total mismatch.
    """
    if not isinstance(ledger, Mapping) or set(ledger) != {
        "schema",
        "ledger_schema",
        "metadata",
        "integrity",
        "historical_transitions",
        "latest_by_run",
        "counts",
        "history_gaps",
    }:
        raise SettlementHistoryError("settlement ledger snapshot shape is invalid")
    if (
        ledger.get("schema") != SETTLEMENT_LEDGER_SNAPSHOT_SCHEMA
        or ledger.get("ledger_schema") != SETTLEMENT_LEDGER_SCHEMA
    ):
        raise SettlementHistoryError("settlement ledger snapshot schema is invalid")

    integrity = ledger.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("valid") is not True:
        raise SettlementHistoryError("settlement ledger integrity is not valid")
    history_complete = integrity.get("history_complete")
    transition_records = integrity.get("transition_records")
    if type(history_complete) is not bool:
        raise SettlementHistoryError("settlement ledger completeness is invalid")
    if type(transition_records) is not int or not 0 <= transition_records <= MAX_U64:
        raise SettlementHistoryError("settlement ledger transition count is invalid")

    counts = ledger.get("counts")
    if not isinstance(counts, Mapping) or set(counts) != {
        "historical_transitions",
        "latest_by_run",
    }:
        raise SettlementHistoryError("settlement ledger counts shape is invalid")
    historical = SettlementCounts.from_payload(counts.get("historical_transitions"))
    latest = SettlementCounts.from_payload(counts.get("latest_by_run"))
    if transition_records != historical.total:
        raise SettlementHistoryError(
            "settlement ledger transition count does not match counts"
        )

    transitions = ledger.get("historical_transitions")
    latest_records = ledger.get("latest_by_run")
    if (
        not isinstance(transitions, Sequence)
        or isinstance(transitions, (str, bytes))
        or len(transitions) != historical.total
        or not isinstance(latest_records, Mapping)
        or len(latest_records) != latest.total
    ):
        raise SettlementHistoryError("settlement ledger records do not match counts")
    observed_historical = SettlementCounts()
    for record in transitions:
        if not isinstance(record, Mapping):
            raise SettlementHistoryError("settlement ledger transition is invalid")
        observed_historical = observed_historical.increment(
            str(record.get("settlement_tui") or "")
        )
    observed_latest = SettlementCounts()
    for run_id, record in latest_records.items():
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(record, Mapping)
            or record.get("run_id") != run_id
        ):
            raise SettlementHistoryError("settlement ledger latest record is invalid")
        observed_latest = observed_latest.increment(
            str(record.get("settlement_tui") or "")
        )
    if observed_historical != historical or observed_latest != latest:
        raise SettlementHistoryError("settlement ledger record buckets diverge")

    history_gaps = _canonical_history_gaps(ledger.get("history_gaps"))
    gaps = _history_gap_units(history_gaps)
    if history_complete and history_gaps:
        raise SettlementHistoryError("complete settlement ledger contains gaps")
    if not history_complete and not history_gaps:
        raise SettlementHistoryError(
            "incomplete settlement ledger omits uncertainty evidence"
        )
    return SettlementHistorySnapshot(
        generation=generation,
        sequence=historical.total,
        historical_transitions=historical,
        latest_by_run=latest,
        gaps=gaps,
        complete_from=1 if history_complete and historical.total else None,
        count_semantics=(
            SETTLEMENT_COUNT_SEMANTICS_EXACT
            if history_complete
            else SETTLEMENT_COUNT_SEMANTICS_LOWER_BOUND
        ),
        history_complete=history_complete,
        history_gaps=history_gaps,
    )


def _is_replaced_snapshot_projection(payload: Mapping[str, Any]) -> bool:
    """Recognize 052c's snapshot-derived DTO so it can be replaced, not trusted."""

    legacy_wire_fields = {
        "schema",
        "generation",
        "sequence",
        "historical_transitions",
        "latest_by_run",
        "gaps",
        "complete_from",
    }
    legacy_rich_fields = legacy_wire_fields | {
        "authority",
        "count_semantics",
        "history_complete",
        "history_gaps",
    }
    return payload.get("schema") == SETTLEMENT_HISTORY_WIRE_SCHEMA and frozenset(
        payload
    ) in {
        frozenset(legacy_wire_fields),
        frozenset(legacy_rich_fields),
    }


def default_settlement_history_path() -> Path:
    """Return the canonical on-disk path for the settlement history projection."""
    return vibecrafted_home() / "control_plane" / "settlement_history.json"


def default_delivery_outbox_path() -> Path:
    """Return the canonical on-disk path for the pending vc-frame delivery outbox."""
    return vibecrafted_home() / "control_plane" / "settlement_history_delivery.json"


def default_generation_path() -> Path:
    """Return the canonical on-disk path for the projection generation marker."""
    return vibecrafted_home() / "control_plane" / "settlement_history_generation.json"


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object file, returning {} if missing; raise on invalid content."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise SettlementHistoryError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SettlementHistoryError(f"JSON document is not an object: {path}")
    return payload


def _fsync_directory(path: Path) -> None:
    """Fsync a directory so a preceding rename/create is durable across a crash."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json_durable(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically write JSON via temp-file + fsync + rename + directory fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, 0o600)
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            raise OSError(errno.EIO, f"short write to {temporary}")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            temporary.unlink()


@contextlib.contextmanager
def _history_lock(root: Path):
    """Hold an exclusive, ownership-validated flock guarding history/generation writes."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".settlement-history.lock"
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SettlementHistoryError("settlement history lock is not regular")
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
            raise SettlementHistoryError("settlement history lock ownership is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _load_generation_locked(root: Path) -> _GenerationState | None:
    """Read and validate the generation marker; caller must hold ``_history_lock``."""
    path = root / "settlement_history_generation.json"
    payload = _read_json(path)
    if not payload:
        return None
    schema = payload.get("schema")
    current_shape = {
        "schema",
        "authority",
        "generation",
        "continuity_gaps",
    }
    legacy_shape = {"schema", "generation", "continuity_gaps"}
    if schema == SETTLEMENT_HISTORY_GENERATION_SCHEMA:
        if (
            set(payload) != current_shape
            or payload.get("authority") != SETTLEMENT_HISTORY_AUTHORITY
        ):
            raise SettlementHistoryError(
                "settlement history generation file is invalid"
            )
        legacy = False
    elif schema == _LEGACY_SETTLEMENT_HISTORY_GENERATION_SCHEMA:
        if set(payload) != legacy_shape:
            raise SettlementHistoryError(
                "settlement history generation file is invalid"
            )
        legacy = True
    else:
        raise SettlementHistoryError("settlement history generation file is invalid")
    generation = payload.get("generation")
    continuity_gaps = payload.get("continuity_gaps")
    if not isinstance(generation, str):
        raise SettlementHistoryError("settlement history generation is invalid")
    try:
        canonical = str(uuid.UUID(generation))
    except ValueError as exc:
        raise SettlementHistoryError(
            "settlement history generation is invalid"
        ) from exc
    if canonical != generation:
        raise SettlementHistoryError("settlement history generation is not canonical")
    if type(continuity_gaps) is not int or not 0 <= continuity_gaps <= MAX_U64:
        raise SettlementHistoryError(
            "settlement history generation continuity is invalid"
        )
    return _GenerationState(
        generation=generation,
        continuity_gaps=continuity_gaps,
        legacy=legacy,
    )


def _write_generation_locked(root: Path, state: _GenerationState) -> None:
    """Durably persist the generation marker; caller must hold ``_history_lock``."""
    _write_json_durable(
        root / "settlement_history_generation.json",
        state.to_payload(),
    )


def _load_or_create_generation_locked(
    root: Path,
) -> _GenerationState:
    """Return the existing generation marker, minting and persisting a fresh one if absent."""
    state = _load_generation_locked(root)
    if state is None:
        state = _GenerationState(
            generation=str(uuid.uuid4()),
            continuity_gaps=0,
        )
        _write_generation_locked(root, state)
    return state


def _rotate_generation_locked(
    root: Path,
    prior: _GenerationState,
) -> _GenerationState:
    """Mint a new generation, incrementing the continuity-gap counter it replaces."""
    if prior.continuity_gaps >= MAX_U64:
        raise SettlementHistoryError(
            "settlement history generation continuity exceeds u64"
        )
    state = _GenerationState(
        generation=str(uuid.uuid4()),
        continuity_gaps=prior.continuity_gaps + 1,
    )
    _write_generation_locked(root, state)
    return state


def reconcile_settlement_history(
    *,
    control_plane_root: Path | None = None,
    output_path: Path | None = None,
) -> SettlementHistorySnapshot:
    """Materialize the verified permanent ledger into the public DTO."""

    root = (
        control_plane_root
        if control_plane_root is not None
        else vibecrafted_home() / "control_plane"
    )
    target = (
        output_path if output_path is not None else root / "settlement_history.json"
    )
    ledger_path = root / "settlement_ledger.jsonl"
    initialize_settlement_ledger(ledger_path)
    ledger = read_settlement_ledger(ledger_path)
    with _history_lock(root):
        prior_payload = _read_json(target)
        replaced_projection = bool(
            prior_payload and _is_replaced_snapshot_projection(prior_payload)
        )
        prior = (
            None
            if not prior_payload or replaced_projection
            else SettlementHistorySnapshot.from_payload(prior_payload)
        )
        generation = _load_or_create_generation_locked(root)
        if generation.legacy or replaced_projection:
            generation = _rotate_generation_locked(root, generation)
        snapshot = _ledger_projection(
            ledger,
            generation=generation.generation,
        )
        if prior is not None and snapshot.generation == prior.generation:
            if snapshot.sequence < prior.sequence:
                raise SettlementHistoryError(
                    "settlement history projection would move sequence backwards"
                )
            if snapshot.sequence == prior.sequence and snapshot != prior:
                raise SettlementHistoryError(
                    "settlement history projection diverged at the same sequence"
                )
        if prior is None or snapshot != prior:
            _write_json_durable(target, snapshot.to_payload())
    return snapshot


Runner = Callable[..., subprocess.CompletedProcess[str]]
Clock = Callable[[], float]


def _default_runner(
    argv: Sequence[str], *, timeout: float
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` capturing output as text; the publisher's default ``Runner``."""
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _resolve_vc_frame_binary(env: Mapping[str, str]) -> str:
    """Return the vc-frame binary path from env override or PATH, else "" if unresolvable."""
    explicit = str(env.get("VIBECRAFTED_VC_FRAME_BIN") or "").strip()
    if explicit:
        return explicit if Path(explicit).is_file() else ""
    return shutil.which("vc-frame", path=env.get("PATH")) or ""


def _running_session_names(output: str) -> tuple[str, ...]:
    """Parse ``vc-frame list-sessions`` output into distinct live session names.

    Skips lines flagged ``(EXITED - attach to resurrect)`` — those sessions are
    not running and cannot receive a pipe delivery.
    """
    sessions: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or "(EXITED - attach to resurrect)" in line:
            continue
        name, separator, _rest = line.partition(" [Created ")
        if separator and name and name not in sessions:
            sessions.append(name)
    return tuple(sessions)


class SettlementHistoryPublisher:
    """Coalesced latest-only delivery to already-running vc-frame plugins."""

    def __init__(
        self,
        *,
        control_plane_root: Path | None = None,
        history_path: Path | None = None,
        outbox_path: Path | None = None,
        runner: Runner = _default_runner,
        env: Mapping[str, str] | None = None,
        timeout: float = 5.0,
        retry_backoff: float = 300.0,
        clock: Clock = time.monotonic,
    ) -> None:
        """Configure paths, runner, and retry/backoff policy for this publisher."""
        if retry_backoff <= 0:
            raise ValueError("settlement delivery retry backoff must be positive")
        self.root = (
            control_plane_root
            if control_plane_root is not None
            else vibecrafted_home() / "control_plane"
        )
        self.history_path = (
            history_path
            if history_path is not None
            else self.root / "settlement_history.json"
        )
        self.outbox_path = (
            outbox_path
            if outbox_path is not None
            else self.root / "settlement_history_delivery.json"
        )
        self.runner = runner
        self.env = dict(os.environ if env is None else env)
        self.timeout = timeout
        self.retry_backoff = retry_backoff
        self.clock = clock
        self._session_retry_after: dict[str, float] = {}
        self._refresh_lock = threading.Lock()
        self._refresh_requested = False
        self._refresh_thread: threading.Thread | None = None
        self._periodic_lock = threading.Lock()
        self._periodic_stop = threading.Event()
        self._periodic_thread: threading.Thread | None = None

    def stage(self, snapshot: SettlementHistorySnapshot) -> None:
        """Durably queue a snapshot for delivery, refusing a stale/regressed sequence.

        Raises ``SettlementHistoryError`` if the generation is stale or the
        outbox diverges from this snapshot at the same sequence.
        """
        snapshot = SettlementHistorySnapshot.from_payload(snapshot.to_payload())
        document = {
            "schema": DELIVERY_OUTBOX_SCHEMA,
            "sequence": snapshot.sequence,
            "payload": snapshot.to_payload(),
        }
        with _history_lock(self.root):
            generation = _load_generation_locked(self.root)
            if (
                generation is None
                or generation.legacy
                or snapshot.generation != generation.generation
            ):
                raise SettlementHistoryError(
                    "cannot stage a stale settlement history generation"
                )
            current = _read_json(self.outbox_path)
            if current:
                sequence = current.get("sequence")
                current_payload = current.get("payload")
                if (
                    current.get("schema") != DELIVERY_OUTBOX_SCHEMA
                    or type(sequence) is not int
                ):
                    raise SettlementHistoryError("delivery outbox sequence is invalid")
                if isinstance(
                    current_payload,
                    Mapping,
                ) and _is_replaced_snapshot_projection(current_payload):
                    current = {}
                else:
                    current_snapshot = SettlementHistorySnapshot.from_payload(
                        current_payload
                    )
                    if sequence != current_snapshot.sequence:
                        raise SettlementHistoryError(
                            "delivery outbox sequence is invalid"
                        )
                    if current_snapshot.generation == snapshot.generation:
                        if sequence > snapshot.sequence:
                            return
                        if sequence == snapshot.sequence and current != document:
                            raise SettlementHistoryError(
                                "delivery outbox diverged at the same sequence"
                            )
            if current != document:
                _write_json_durable(self.outbox_path, document)

    def flush(self) -> DeliveryReport:
        """Deliver the staged outbox snapshot to every running vc-frame plugin session.

        Clears the outbox only once every eligible session accepted delivery;
        failed/deferred sessions get a per-session retry backoff.
        """
        with _history_lock(self.root):
            document = _read_json(self.outbox_path)
            if not document:
                return DeliveryReport(reason="idle")
            if (
                document.get("schema") != DELIVERY_OUTBOX_SCHEMA
                or type(document.get("sequence")) is not int
            ):
                raise SettlementHistoryError("delivery outbox is invalid")
            snapshot = SettlementHistorySnapshot.from_payload(document.get("payload"))
            generation = _load_generation_locked(self.root)
            if (
                generation is None
                or generation.legacy
                or snapshot.generation != generation.generation
            ):
                return DeliveryReport(
                    pending=True,
                    reason="stale settlement history generation",
                )
        binary = _resolve_vc_frame_binary(self.env)
        if not binary:
            return DeliveryReport(pending=True, reason="vc-frame unavailable")
        try:
            listed = self.runner(
                [binary, "list-sessions", "--no-formatting"],
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return DeliveryReport(
                pending=True,
                reason=f"session discovery failed: {type(exc).__name__}",
            )
        if listed.returncode != 0:
            return DeliveryReport(pending=True, reason="no running vc-frame sessions")
        sessions = tuple(
            session
            for session in _running_session_names(listed.stdout)
            if session not in _NON_PLUGIN_SESSION_NAMES
        )
        if not sessions:
            self._session_retry_after.clear()
            return DeliveryReport(
                pending=True,
                reason="no eligible vc-frame plugin sessions",
            )

        delivered: list[str] = []
        failed: list[str] = []
        deferred: list[str] = []
        running_sessions = set(sessions)
        self._session_retry_after = {
            session: retry_after
            for session, retry_after in self._session_retry_after.items()
            if session in running_sessions
        }
        now = self.clock()
        payload = snapshot.to_wire_json()
        for session in sessions:
            if self._session_retry_after.get(session, 0.0) > now:
                deferred.append(session)
                continue
            try:
                result = self.runner(
                    [
                        binary,
                        "--session",
                        session,
                        "pipe",
                        "--name",
                        SETTLEMENT_COUNTS_PIPE,
                        "--",
                        payload,
                    ],
                    timeout=self.timeout,
                )
            except (OSError, subprocess.SubprocessError):
                failed.append(session)
                self._session_retry_after[session] = now + self.retry_backoff
                continue
            if result.returncode == 0:
                delivered.append(session)
                self._session_retry_after.pop(session, None)
            else:
                failed.append(session)
                self._session_retry_after[session] = now + self.retry_backoff

        if not failed and not deferred:
            with _history_lock(self.root):
                current = _read_json(self.outbox_path)
                if (
                    current.get("schema") == DELIVERY_OUTBOX_SCHEMA
                    and current.get("sequence") == snapshot.sequence
                    and current.get("payload") == snapshot.to_payload()
                ):
                    self.outbox_path.unlink(missing_ok=True)
                    _fsync_directory(self.outbox_path.parent)
        return DeliveryReport(
            attempted_sessions=sessions,
            delivered_sessions=tuple(delivered),
            failed_sessions=tuple(failed),
            deferred_sessions=tuple(deferred),
            pending=bool(failed or deferred),
            reason=(
                ""
                if not failed and not deferred
                else "one or more vc-frame deliveries failed"
                if failed
                else "vc-frame delivery retry deferred"
            ),
        )

    def refresh_and_flush(self) -> DeliveryReport:
        """Recompute the ledger projection, stage it, and flush it to vc-frame."""
        snapshot = reconcile_settlement_history(
            control_plane_root=self.root,
            output_path=self.history_path,
        )
        self.stage(snapshot)
        return self.flush()

    def request_refresh(self) -> bool:
        """Schedule one coalesced refresh without blocking Guardian's SSE loop."""

        with self._refresh_lock:
            self._refresh_requested = True
            if self._refresh_thread is not None and self._refresh_thread.is_alive():
                return False
            thread = threading.Thread(
                target=self._drain_refresh_requests,
                name="vibecrafted-settlement-history",
                daemon=True,
            )
            self._refresh_thread = thread
            thread.start()
            return True

    def start_periodic_refresh(
        self,
        interval: float = SETTLEMENT_REPLAY_INTERVAL_SECONDS,
    ) -> bool:
        """Replay latest truth often enough for newly loaded session managers."""

        if not 0 < interval <= SETTLEMENT_REPLAY_INTERVAL_SECONDS:
            raise ValueError(
                "settlement history replay interval must be within five seconds"
            )
        with self._periodic_lock:
            if self._periodic_thread is not None and self._periodic_thread.is_alive():
                return False
            stop = threading.Event()
            thread = threading.Thread(
                target=self._periodic_refresh_loop,
                args=(stop, interval),
                name="vibecrafted-settlement-history-replay",
                daemon=True,
            )
            self._periodic_stop = stop
            self._periodic_thread = thread
            thread.start()
        self.request_refresh()
        return True

    def _periodic_refresh_loop(
        self,
        stop: threading.Event,
        interval: float,
    ) -> None:
        """Background loop: request a refresh every ``interval`` until ``stop`` is set."""
        try:
            while not stop.wait(interval):
                self.request_refresh()
        finally:
            with self._periodic_lock:
                if self._periodic_stop is stop:
                    self._periodic_thread = None

    def stop_periodic_refresh(self, timeout: float = 5.0) -> bool:
        """Stop the replay timer; any in-flight publication stays non-blocking."""

        with self._periodic_lock:
            thread = self._periodic_thread
            self._periodic_stop.set()
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def _drain_refresh_requests(self) -> None:
        """Background worker: coalesce pending refresh requests into one pass at a time."""
        while True:
            with self._refresh_lock:
                if not self._refresh_requested:
                    self._refresh_thread = None
                    return
                self._refresh_requested = False
            try:
                self.refresh_and_flush()
            except Exception:
                LOGGER.exception("settlement-history background refresh failed")

    def wait_for_idle(self, timeout: float = 5.0) -> bool:
        """Test/doctor seam: wait for the current coalesced worker."""

        with self._refresh_lock:
            thread = self._refresh_thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()


__all__ = [
    "DELIVERY_OUTBOX_SCHEMA",
    "MAX_U64",
    "SETTLEMENT_COUNTS_PIPE",
    "SETTLEMENT_COUNT_SEMANTICS_EXACT",
    "SETTLEMENT_COUNT_SEMANTICS_LOWER_BOUND",
    "SETTLEMENT_HISTORY_AUTHORITY",
    "SETTLEMENT_HISTORY_GENERATION_SCHEMA",
    "SETTLEMENT_HISTORY_SCHEMA",
    "SETTLEMENT_HISTORY_WIRE_SCHEMA",
    "SETTLEMENT_REPLAY_INTERVAL_SECONDS",
    "DeliveryReport",
    "SettlementCounts",
    "SettlementHistoryError",
    "SettlementHistoryPublisher",
    "SettlementHistorySnapshot",
    "default_delivery_outbox_path",
    "default_generation_path",
    "default_settlement_history_path",
    "reconcile_settlement_history",
]
