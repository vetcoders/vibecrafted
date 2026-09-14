"""Publish the canonical server settlement board to vc-frame.

The displayed f/x/n values come only from ``GET /api/control/state``.  The
append-only ledger contributes a monotonic compatibility carrier because the
current vc-frame v1 pipe validates that envelope before reading
``latest_by_run``.  It never supplies the displayed values.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .delivery.store import atomic_write_json
from .runtime_paths import vibecrafted_home
from .settlement_history import (
    MAX_U64,
    SETTLEMENT_HISTORY_WIRE_SCHEMA,
    SettlementCounts,
    SettlementHistoryError,
)
from .settlement_ledger import read_settlement_ledger

SETTLEMENT_COUNTS_PIPE = "vc_settlement_counts"
SETTLEMENT_REPLAY_INTERVAL_SECONDS = 5.0
SETTLEMENT_DELIVERY_RETRY_BACKOFF_SECONDS = 1.0
# A session listed under the same name but born this much later than before is
# a restarted server whose plugins never saw the board.
SETTLEMENT_SESSION_RESTART_TOLERANCE_SECONDS = 15.0
SETTLEMENT_BOARD_SCOPE = "retained_control_plane_snapshots"
MAX_STATE_RESPONSE_BYTES = 8 * 1024 * 1024
SETTLEMENT_BOARD_TRANSPORT_SCHEMA = "vibecrafted.settlement-board-transport.v1"
_NON_PLUGIN_SESSION_NAMES = frozenset(
    {"Failed runs", "Finalized runs", "Needs attention"}
)
LOGGER = logging.getLogger(__name__)

Runner = Callable[..., subprocess.CompletedProcess[str]]
Clock = Callable[[], float]
BoardReader = Callable[[str, float], Mapping[str, object]]
LedgerReader = Callable[[Path], Mapping[str, object]]


class SettlementBoardError(RuntimeError):
    """The server settlement board or compatibility carrier is invalid."""


@dataclass(frozen=True)
class ServerSettlementBoard:
    """The exact f/x/n aggregate exposed by the canonical server snapshot."""

    f: int
    x: int
    n: int

    @classmethod
    def from_state(cls, state: Mapping[str, object]) -> ServerSettlementBoard:
        raw = state.get("settlement_counts")
        if not isinstance(raw, Mapping):
            raise SettlementBoardError("server state omits settlement_counts")
        if raw.get("scope") != SETTLEMENT_BOARD_SCOPE:
            raise SettlementBoardError("server settlement scope is not canonical")
        values = {
            key: raw.get(key)
            for key in (
                "active",
                "f",
                "x",
                "n",
                "invalid",
                "unclassified",
                "total_settled",
            )
        }
        if any(
            type(value) is not int or not 0 <= value <= MAX_U64
            for value in values.values()
        ):
            raise SettlementBoardError("server settlement counts must be u64 integers")
        f, x, n = values["f"], values["x"], values["n"]
        total = values["total_settled"]
        invalid = values["invalid"]
        assert isinstance(f, int) and isinstance(x, int) and isinstance(n, int)
        assert isinstance(total, int) and isinstance(invalid, int)
        if total != f + x + n or invalid > x:
            raise SettlementBoardError("server settlement totals are inconsistent")
        return cls(f=f, x=x, n=n)

    def counts(self) -> SettlementCounts:
        """Return the validated vc-frame count shape."""

        return SettlementCounts(f=self.f, x=self.x, n=self.n)


@dataclass(frozen=True)
class DeliveryReport:
    """Outcome of one best-effort delivery pass to vc-frame sessions."""

    attempted_sessions: tuple[str, ...] = ()
    delivered_sessions: tuple[str, ...] = ()
    failed_sessions: tuple[str, ...] = ()
    deferred_sessions: tuple[str, ...] = ()
    unchanged_sessions: tuple[str, ...] = ()
    pending: bool = False
    reason: str = ""


def _default_runner(
    argv: Sequence[str], *, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), capture_output=True, text=True, timeout=timeout, check=False
    )


def _read_server_state(server_url: str, timeout: float) -> Mapping[str, object]:
    parsed_url = urllib.parse.urlsplit(server_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise SettlementBoardError("server URL must use http or https")
    request = urllib.request.Request(
        f"{server_url.rstrip('/')}/api/control/state",
        headers={"Accept": "application/json"},
    )
    # The explicit scheme check above prevents urllib's local file transport.
    with urllib.request.urlopen(  # nosemgrep: dynamic-urllib-use-detected
        request, timeout=timeout
    ) as response:
        body = response.read(MAX_STATE_RESPONSE_BYTES + 1)
    if len(body) > MAX_STATE_RESPONSE_BYTES:
        raise SettlementBoardError("server state response exceeds size limit")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettlementBoardError("server state response is not JSON") from exc
    if not isinstance(payload, Mapping):
        raise SettlementBoardError("server state response is not an object")
    return payload


def _read_ledger(path: Path) -> Mapping[str, object]:
    return read_settlement_ledger(path)


def _resolve_vc_frame_binary(env: Mapping[str, str]) -> str:
    explicit = str(env.get("VIBECRAFTED_VC_FRAME_BIN") or "").strip()
    if explicit:
        return explicit if Path(explicit).is_file() else ""
    return shutil.which("vc-frame", path=env.get("PATH")) or ""


# `vc-frame list-sessions --no-formatting` prints `[Created <age> ago]`, where
# the age is humantime-formatted whole seconds since the session socket was
# created.
_CREATED_AGE = re.compile(r"(?P<age>[^\]]*) ago\]")
_HUMANTIME_TERM = re.compile(r"(?P<value>\d+)(?P<unit>[a-z]+)")
_HUMANTIME_UNIT_SECONDS = {
    "year": 31_557_600,
    "years": 31_557_600,
    "month": 2_630_016,
    "months": 2_630_016,
    "day": 86_400,
    "days": 86_400,
    "h": 3_600,
    "m": 60,
    "s": 1,
}
_HUMANTIME_SUBSECOND_UNITS = frozenset({"ms", "us", "ns"})


def _created_age_seconds(rest: str) -> int | None:
    match = _CREATED_AGE.match(rest)
    if match is None:
        return None
    terms = match.group("age").split()
    if not terms:
        return None
    total = 0
    for term in terms:
        parsed = _HUMANTIME_TERM.fullmatch(term)
        if parsed is None:
            return None
        unit = parsed.group("unit")
        if unit in _HUMANTIME_SUBSECOND_UNITS:
            continue
        seconds = _HUMANTIME_UNIT_SECONDS.get(unit)
        if seconds is None:
            return None
        total += int(parsed.group("value")) * seconds
    return total


def _running_sessions(output: str) -> tuple[tuple[str, int | None], ...]:
    """Running session names with their created age, when the age is legible."""
    sessions: dict[str, int | None] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or "(EXITED - attach to resurrect)" in line:
            continue
        name, separator, rest = line.partition(" [Created ")
        if separator and name and name not in sessions:
            sessions[name] = _created_age_seconds(rest)
    return tuple(sessions.items())


class SettlementBoardPublisher:
    """Fetch and replay the canonical server board without blocking Guardian."""

    def __init__(
        self,
        *,
        server_url: str,
        control_plane_root: Path | None = None,
        runner: Runner = _default_runner,
        board_reader: BoardReader = _read_server_state,
        ledger_reader: LedgerReader = _read_ledger,
        env: Mapping[str, str] | None = None,
        timeout: float = 5.0,
        retry_backoff: float = SETTLEMENT_DELIVERY_RETRY_BACKOFF_SECONDS,
        clock: Clock = time.monotonic,
        transport_path: Path | None = None,
    ) -> None:
        if retry_backoff <= 0:
            raise ValueError("settlement delivery retry backoff must be positive")
        self.server_url = server_url.rstrip("/")
        self.root = control_plane_root or vibecrafted_home() / "control_plane"
        self.runner = runner
        self.board_reader = board_reader
        self.ledger_reader = ledger_reader
        self.env = dict(os.environ if env is None else env)
        self.timeout = timeout
        self.retry_backoff = retry_backoff
        self.clock = clock
        self.transport_path = (
            transport_path or self.root / "settlement_board_transport.json"
        )
        self._publisher_id = uuid.uuid4()
        self._board_revision = 0
        self._last_board_key: tuple[int, ...] | None = None
        self._last_payload = ""
        self._carrier = self._load_carrier()
        self._session_retry_after: dict[str, float] = {}
        self._delivered_payloads: dict[str, str] = {}
        self._session_born_at: dict[str, float] = {}
        self._refresh_lock = threading.Lock()
        self._refresh_requested = False
        self._refresh_thread: threading.Thread | None = None
        self._periodic_lock = threading.Lock()
        self._periodic_stop = threading.Event()
        self._periodic_thread: threading.Thread | None = None

    def _load_carrier(self) -> SettlementCounts:
        try:
            payload = json.loads(self.transport_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return SettlementCounts()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            LOGGER.error("ignoring invalid settlement board transport state: %s", exc)
            return SettlementCounts()
        if not isinstance(payload, Mapping) or set(payload) != {"schema", "carrier"}:
            LOGGER.error("ignoring invalid settlement board transport state shape")
            return SettlementCounts()
        if payload.get("schema") != SETTLEMENT_BOARD_TRANSPORT_SCHEMA:
            LOGGER.error("ignoring unknown settlement board transport schema")
            return SettlementCounts()
        try:
            return SettlementCounts.from_payload(payload.get("carrier"))
        except SettlementHistoryError as exc:
            LOGGER.error("ignoring invalid settlement board transport carrier: %s", exc)
            return SettlementCounts()

    def _store_carrier(self, carrier: SettlementCounts) -> None:
        atomic_write_json(
            self.transport_path,
            {
                "schema": SETTLEMENT_BOARD_TRANSPORT_SCHEMA,
                "carrier": carrier.to_payload(),
            },
        )

    def _compatibility_payload(self) -> str:
        state = self.board_reader(self.server_url, self.timeout)
        latest = ServerSettlementBoard.from_state(state).counts()
        ledger = self.ledger_reader(self.root / "settlement_ledger.jsonl")
        raw_counts = ledger.get("counts")
        if not isinstance(raw_counts, Mapping):
            raise SettlementBoardError("settlement ledger omits counts")
        try:
            ledger_historical = SettlementCounts.from_payload(
                raw_counts.get("historical_transitions")
            )
        except SettlementHistoryError as exc:
            raise SettlementBoardError("settlement ledger counts are invalid") from exc
        carrier = SettlementCounts(
            f=max(self._carrier.f, ledger_historical.f, latest.f),
            x=max(self._carrier.x, ledger_historical.x, latest.x),
            n=max(self._carrier.n, ledger_historical.n, latest.n),
        )
        if carrier != self._carrier:
            self._store_carrier(carrier)
            self._carrier = carrier
        board_key = (
            latest.f,
            latest.x,
            latest.n,
            carrier.f,
            carrier.x,
            carrier.n,
        )
        if board_key != self._last_board_key:
            self._board_revision += 1
            generation = str(uuid.uuid5(self._publisher_id, str(self._board_revision)))
            self._last_payload = json.dumps(
                {
                    "schema": SETTLEMENT_HISTORY_WIRE_SCHEMA,
                    "generation": generation,
                    "sequence": carrier.total,
                    "historical_transitions": carrier.to_payload(),
                    "latest_by_run": latest.to_payload(),
                    "gaps": 0,
                    "complete_from": 1 if carrier.total else None,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            self._last_board_key = board_key
        return self._last_payload

    def refresh_and_flush(self) -> DeliveryReport:
        """Fetch one canonical snapshot and deliver it to running plugins."""

        return self._deliver(self._compatibility_payload())

    def _deliver(self, payload: str) -> DeliveryReport:
        binary = _resolve_vc_frame_binary(self.env)
        if not binary:
            return DeliveryReport(pending=True, reason="vc-frame unavailable")
        try:
            listed = self.runner(
                [binary, "list-sessions", "--no-formatting"], timeout=self.timeout
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return DeliveryReport(
                pending=True,
                reason=f"session discovery failed: {type(exc).__name__}",
            )
        if listed.returncode != 0:
            return DeliveryReport(pending=True, reason="no running vc-frame sessions")
        discovered = tuple(
            (session, age)
            for session, age in _running_sessions(listed.stdout)
            if session not in _NON_PLUGIN_SESSION_NAMES
        )
        sessions = tuple(session for session, _age in discovered)
        if not sessions:
            self._session_retry_after.clear()
            self._delivered_payloads.clear()
            self._session_born_at.clear()
            return DeliveryReport(
                pending=True, reason="no eligible vc-frame plugin sessions"
            )

        now = self.clock()
        self._reconcile_sessions(discovered, now)
        delivered: list[str] = []
        failed: list[str] = []
        deferred: list[str] = []
        unchanged: list[str] = []
        for session in sessions:
            if self._session_retry_after.get(session, 0.0) > now:
                deferred.append(session)
                continue
            # Every pipe is a vc-frame CLI client, and each attach makes the
            # server re-broadcast Tab/Pane/Session updates to every plugin in
            # that session.  Replaying an identical board every few seconds
            # made the whole chrome flicker, so a session only receives a
            # payload it has not already accepted.
            if self._delivered_payloads.get(session) == payload:
                unchanged.append(session)
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
                result = None
            if result is not None and result.returncode == 0:
                delivered.append(session)
                self._session_retry_after.pop(session, None)
                self._delivered_payloads[session] = payload
            else:
                failed.append(session)
                self._session_retry_after[session] = now + self.retry_backoff
                self._delivered_payloads.pop(session, None)
        return DeliveryReport(
            attempted_sessions=sessions,
            delivered_sessions=tuple(delivered),
            failed_sessions=tuple(failed),
            deferred_sessions=tuple(deferred),
            unchanged_sessions=tuple(unchanged),
            pending=bool(failed or deferred),
            reason=(
                ""
                if not failed and not deferred
                else "one or more vc-frame deliveries failed"
                if failed
                else "vc-frame delivery retry deferred"
            ),
        )

    def _reconcile_sessions(
        self, discovered: Sequence[tuple[str, int | None]], now: float
    ) -> None:
        """Forget delivery state for sessions that left or were restarted."""

        running = {session for session, _age in discovered}
        self._session_retry_after = {
            session: retry_after
            for session, retry_after in self._session_retry_after.items()
            if session in running
        }
        self._delivered_payloads = {
            session: payload
            for session, payload in self._delivered_payloads.items()
            if session in running
        }
        born_at: dict[str, float] = {}
        for session, age in discovered:
            if age is None:
                continue
            estimate = now - age
            previous = self._session_born_at.get(session)
            if (
                previous is not None
                and estimate > previous + SETTLEMENT_SESSION_RESTART_TOLERANCE_SECONDS
            ):
                self._delivered_payloads.pop(session, None)
                self._session_retry_after.pop(session, None)
            born_at[session] = estimate
        self._session_born_at = born_at

    def request_refresh(self) -> bool:
        """Schedule one coalesced refresh without blocking Guardian's SSE loop."""

        with self._refresh_lock:
            self._refresh_requested = True
            if self._refresh_thread is not None and self._refresh_thread.is_alive():
                return False
            self._refresh_thread = threading.Thread(
                target=self._drain_refresh_requests,
                name="vibecrafted-settlement-board",
                daemon=True,
            )
            self._refresh_thread.start()
            return True

    def _drain_refresh_requests(self) -> None:
        while True:
            with self._refresh_lock:
                if not self._refresh_requested:
                    self._refresh_thread = None
                    return
                self._refresh_requested = False
            try:
                self.refresh_and_flush()
            except Exception:
                LOGGER.exception("server settlement-board refresh failed")

    def start_periodic_refresh(
        self, interval: float = SETTLEMENT_REPLAY_INTERVAL_SECONDS
    ) -> bool:
        if not 0 < interval <= SETTLEMENT_REPLAY_INTERVAL_SECONDS:
            raise ValueError(
                "settlement board replay interval must be within five seconds"
            )
        with self._periodic_lock:
            if self._periodic_thread is not None and self._periodic_thread.is_alive():
                return False
            stop = threading.Event()
            self._periodic_stop = stop
            self._periodic_thread = threading.Thread(
                target=self._periodic_refresh_loop,
                args=(stop, interval),
                name="vibecrafted-settlement-board-replay",
                daemon=True,
            )
            self._periodic_thread.start()
        self.request_refresh()
        return True

    def _periodic_refresh_loop(self, stop: threading.Event, interval: float) -> None:
        try:
            while not stop.wait(interval):
                self.request_refresh()
        finally:
            with self._periodic_lock:
                if self._periodic_stop is stop:
                    self._periodic_thread = None

    def stop_periodic_refresh(self, timeout: float = 5.0) -> bool:
        with self._periodic_lock:
            thread = self._periodic_thread
            self._periodic_stop.set()
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def wait_for_idle(self, timeout: float = 5.0) -> bool:
        with self._refresh_lock:
            thread = self._refresh_thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()


__all__ = [
    "SETTLEMENT_BOARD_SCOPE",
    "SETTLEMENT_BOARD_TRANSPORT_SCHEMA",
    "SETTLEMENT_COUNTS_PIPE",
    "SETTLEMENT_REPLAY_INTERVAL_SECONDS",
    "DeliveryReport",
    "ServerSettlementBoard",
    "SettlementBoardError",
    "SettlementBoardPublisher",
]
