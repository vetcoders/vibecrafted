"""Garbage collector for processes that outlive their run.

The control plane already *knows* when a run is dead — ``_reconcile_dead_launcher``
settles it to ``gc``/``stalled`` and records ``pid_gone``. Nothing ever acted on
that knowledge, so a run could reach a terminal state while its monitors, watchers
and helper children kept burning cores for days. This module is the missing actor:
given the control plane's terminal runs, it finds surviving processes that provably
belong to them and takes them down, with a receipt for every decision.

**Ownership must be proven, never inferred.** The reaper runs inside a live
workspace full of processes it must not touch — vc-frame servers, MCP servers, the
operator's own shells and panes. So a pid is a candidate only when it carries one of
two independent proofs tying it to a specific terminal run:

``env_run_id``
    The process environment carries ``SPAWN_RUN_ID=<run>``. Every process in a run's
    tree inherits it (``lib/launcher.sh`` exports it), so this is the strongest
    proof available. It is not universally *readable*, though: macOS strips the
    environment of SIP-protected/hardened binaries, so ``ps eww`` answers for a
    homebrew ``python``/``node`` (what agents actually are) and stays silent for
    ``/bin/sleep``. Absence of the variable therefore proves nothing.

``worker_pgid``
    The process group id matches the run's recorded ``worker_pgid``/``worker_pid``.
    Crucially this survives orphaning — when a parent dies the child is reparented
    to launchd/init but keeps its process group — which is exactly the state the
    survivors we are hunting are in. Where env-reading fails, this still holds.

Anything without one of those is reported as ``unproven`` and left running. A
survivor we failed to prove is a survivor we keep; killing the wrong process in a
live workspace costs far more than missing one.

**Three ownership buckets** (run-level inventory + process verdicts):

``provable``
    Terminal run carries a recorded ``worker_pgid``/``worker_pid`` (or a live
    process is proven via ``SPAWN_RUN_ID`` / matching pgid). Kill candidates only
    come from this bucket.

``legacy``
    Historical terminal run without a recorded pgid, explicitly marked by the
    one-shot migration (``reaper_ownership: legacy``). The reaper never treats
    these as kill sources — even if a lingering process still carries their
    ``SPAWN_RUN_ID``.

``undecidable``
    Terminal run with neither a recorded pgid nor a legacy mark, and no positive
    env proof on a live process. Fail-closed: never killed.

Migration lives at ``quarantine_legacy_runs`` (doctor: ``--quarantine-legacy-runs``):
marks terminal-no-pgid as legacy; best-effort recovers pgid for *live* runs only
when ``SPAWN_RUN_ID`` is positively visible in ``ps``.

**We never take ourselves down.** The terminal-seam caller runs *inside* the very
run whose survivors it is reaping, so its own pid and every ancestor are excluded
before anything is signalled. Siblings — the orphaned monitor — remain fair game;
that is the whole point.

Escalation is TERM, then a bounded grace, then KILL, receipted per step. The kill
switch is ``VIBECRAFTED_REAPER=0``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "OWNERSHIP_LEGACY",
    "OWNERSHIP_PROVABLE",
    "OWNERSHIP_UNDECIDABLE",
    "PROTECTED_COMMAND_PATTERNS",
    "REAPER_OWNERSHIP_KEY",
    "OwnershipBuckets",
    "ProcessEntry",
    "QuarantineResult",
    "ReapCandidate",
    "ReapPlan",
    "ReapReceipt",
    "build_env_index",
    "build_process_table",
    "classify_run_ownership",
    "main",
    "plan_json_payload",
    "plan_reap",
    "quarantine_legacy_runs",
    "reap_terminal_runs",
    "recorded_worker_pgid",
]

_TRUTHY_OFF = {"0", "false", "no", "off"}

REAPER_ENABLED_ENV = "VIBECRAFTED_REAPER"
REAPER_GRACE_ENV = "VIBECRAFTED_REAPER_GRACE_SECONDS"
DEFAULT_GRACE_SECONDS = 5.0

#: Field written by the one-shot legacy quarantine migration.
REAPER_OWNERSHIP_KEY = "reaper_ownership"
OWNERSHIP_LEGACY = "legacy"
OWNERSHIP_PROVABLE = "provable"
OWNERSHIP_UNDECIDABLE = "undecidable"

#: Never signalled, even when a proof matches. These are workspace infrastructure
#: that can share a process group with a run by accident of how a tab was opened:
#: the terminal multiplexer that *hosts* the run is not part of the run. Killing
#: one takes the operator's window (and every sibling run in it) with it.
PROTECTED_COMMAND_PATTERNS = (
    "vc-frame",
    "zellij",
    "tmux",
    "mcp-server",
    "loctree-mcp",
    "aicx-mcp",
    "sshd",
    "login",
)

_ENV_RUN_ID_PATTERN = re.compile(r"(?:^|\s)SPAWN_RUN_ID=(\S+)")


def reaper_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the reaper is enabled; disabled by ``VIBECRAFTED_REAPER`` in
    {"0", "false", "no", "off"} (case-insensitive)."""
    env = os.environ if env is None else env
    return str(env.get(REAPER_ENABLED_ENV, "") or "").strip().lower() not in _TRUTHY_OFF


def grace_seconds(env: Mapping[str, str] | None = None) -> float:
    """TERM->KILL grace window in seconds, from env or :data:`DEFAULT_GRACE_SECONDS`."""
    env = os.environ if env is None else env
    raw = str(env.get(REAPER_GRACE_ENV, "") or "").strip()
    if not raw:
        return DEFAULT_GRACE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_GRACE_SECONDS
    return value if value >= 0 else DEFAULT_GRACE_SECONDS


def recorded_worker_pgid(run: Mapping[str, Any]) -> int | None:
    """Return a recorded process-group id that is safe to treat as ownership proof.

    pgid 0/1 would match half the machine; only a real group (>1) counts.
    """
    from .control_plane import _coerce_int

    for key in ("worker_pgid", "worker_pid"):
        pgid = _coerce_int(run.get(key))
        if pgid is not None and pgid > 1:
            return pgid
    return None


def classify_run_ownership(run: Mapping[str, Any]) -> str:
    """Classify a run into provable / legacy / undecidable.

    ``legacy`` is an explicit field written by the quarantine migration — it
    wins over a spurious recorded pgid so a marked historical run can never be
    treated as a kill source.
    """
    if str(run.get(REAPER_OWNERSHIP_KEY) or "").strip() == OWNERSHIP_LEGACY:
        return OWNERSHIP_LEGACY
    if recorded_worker_pgid(run) is not None:
        return OWNERSHIP_PROVABLE
    return OWNERSHIP_UNDECIDABLE


@dataclass(frozen=True)
class OwnershipBuckets:
    """Terminal-run inventory for the reaper report: three exclusive buckets."""

    provable: tuple[str, ...] = ()
    legacy: tuple[str, ...] = ()
    undecidable: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, list[str]]:
        """Serialize the three ownership buckets for JSON output."""
        return {
            OWNERSHIP_PROVABLE: list(self.provable),
            OWNERSHIP_LEGACY: list(self.legacy),
            OWNERSHIP_UNDECIDABLE: list(self.undecidable),
        }


@dataclass
class QuarantineResult:
    """Outcome of the one-shot ``reaper_ownership: legacy`` migration."""

    marked_legacy: list[str] = field(default_factory=list)
    recovered_pgid: list[dict[str, Any]] = field(default_factory=list)
    skipped_live: list[str] = field(default_factory=list)
    skipped_has_pgid: list[str] = field(default_factory=list)
    already_legacy: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    changed: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Serialize the quarantine migration outcome for JSON output."""
        return {
            "marked_legacy": list(self.marked_legacy),
            "recovered_pgid": list(self.recovered_pgid),
            "skipped_live": list(self.skipped_live),
            "skipped_has_pgid": list(self.skipped_has_pgid),
            "already_legacy": list(self.already_legacy),
            "parse_errors": list(self.parse_errors),
            "changed": self.changed,
        }


@dataclass(frozen=True)
class ProcessEntry:
    """One row of the process table."""

    pid: int
    ppid: int
    pgid: int
    command: str


@dataclass(frozen=True)
class ReapCandidate:
    """A surviving process and what we could (or could not) prove about it."""

    pid: int
    command: str
    run_id: str = ""
    #: "env_run_id" | "worker_pgid" — empty when unproven.
    evidence: str = ""
    detail: str = ""

    @property
    def proven(self) -> bool:
        """True only when both an evidence kind and an owning run id are set."""
        return bool(self.evidence and self.run_id)

    def row(self) -> dict[str, Any]:
        """Serialize this candidate as one table/JSON row (command truncated)."""
        return {
            "pid": self.pid,
            "run_id": self.run_id,
            "evidence": self.evidence or "unproven",
            "detail": self.detail,
            "command": self.command[:200],
        }


@dataclass(frozen=True)
class ReapPlan:
    """The decision, taken without side effects so it can be tested directly."""

    should_run: bool
    skip_reason: str = ""
    doomed: tuple[ReapCandidate, ...] = ()
    unproven: tuple[ReapCandidate, ...] = ()
    protected: tuple[ReapCandidate, ...] = ()
    #: Processes tied to a ``reaper_ownership: legacy`` run — never killed.
    legacy: tuple[ReapCandidate, ...] = ()
    #: Terminal-run inventory: provable / legacy / undecidable.
    ownership: OwnershipBuckets = field(default_factory=OwnershipBuckets)

    def render(self) -> str:
        """The ``--dry-run`` table: every pid with the evidence behind its verdict."""
        if not self.should_run:
            return f"reaper: skipped ({self.skip_reason})"
        lines: list[str] = []
        for label, rows in (
            ("REAP", self.doomed),
            ("KEEP/legacy", self.legacy),
            ("KEEP/unproven", self.unproven),
            ("KEEP/protected", self.protected),
        ):
            for candidate in rows:
                lines.append(
                    f"{label:<15} pid={candidate.pid:<8} run={candidate.run_id or '-':<28} "
                    f"evidence={candidate.evidence or 'unproven':<12} {candidate.command[:80]}"
                )
        if not lines:
            buckets = self.ownership
            if buckets.legacy or buckets.undecidable or buckets.provable:
                return (
                    "reaper: no survivors of terminal runs "
                    f"(ownership provable={len(buckets.provable)} "
                    f"legacy={len(buckets.legacy)} "
                    f"undecidable={len(buckets.undecidable)})"
                )
            return "reaper: no survivors of terminal runs"
        header = (
            f"reaper: {len(self.doomed)} to reap, {len(self.legacy)} legacy, "
            f"{len(self.unproven)} unproven, {len(self.protected)} protected"
        )
        return "\n".join([header, *lines])


def plan_json_payload(plan: ReapPlan, dry_run: bool = False) -> dict[str, Any]:
    """Machine-readable reap plan: explicit ownership buckets + process rows."""
    return {
        "should_run": plan.should_run,
        "skip_reason": plan.skip_reason,
        "dry_run": dry_run,
        "ownership": plan.ownership.as_dict(),
        "reaped": [c.row() for c in plan.doomed],
        "legacy": [c.row() for c in plan.legacy],
        "unproven": [c.row() for c in plan.unproven],
        "protected": [c.row() for c in plan.protected],
    }


@dataclass
class ReapReceipt:
    """What actually happened, as written into the run's control-plane record."""

    run_id: str
    reaped: list[dict[str, Any]] = field(default_factory=list)
    unproven: list[dict[str, Any]] = field(default_factory=list)
    skipped_reason: str = ""

    def payload(self) -> dict[str, Any]:
        """Fields to merge into a run's control-plane snapshot after reaping."""
        data: dict[str, Any] = {"reaped": self.reaped}
        if self.unproven:
            data["reap_unproven"] = self.unproven
        if self.skipped_reason:
            data["reap_skipped"] = self.skipped_reason
        return data


def _default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Default ``runner`` callable: a bounded, non-raising subprocess invocation."""
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def build_process_table(
    runner: Callable[..., Any] | None = None,
) -> tuple[ProcessEntry, ...]:
    """Snapshot the process table. Empty on any failure — never raises."""
    runner = _default_runner if runner is None else runner
    try:
        # ww: never truncate the command column. Without it ps honors COLUMNS
        # (pytest and CI runners export one), so the same process hashes to two
        # different command_sha256 values depending on the observer — identity
        # validation then refuses a legitimate stop with
        # process_identity_mismatch.
        proc = runner(["ps", "-A", "-ww", "-o", "pid=,ppid=,pgid=,command="])
    except Exception:  # noqa: BLE001 - no process table means nothing to reap, never a crash
        return ()
    if getattr(proc, "returncode", 1) != 0:
        return ()
    entries: list[ProcessEntry] = []
    for line in str(getattr(proc, "stdout", "") or "").splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) < 4:
            continue
        try:
            pid, ppid, pgid = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        entries.append(ProcessEntry(pid=pid, ppid=ppid, pgid=pgid, command=parts[3]))
    return tuple(entries)


def build_env_index(runner: Callable[..., Any] | None = None) -> dict[int, str]:
    """Map pid -> ``SPAWN_RUN_ID`` for every process that exposes one.

    One ``ps axeww`` for the whole machine rather than one probe per pid: the sweep
    runs on every spawn, so hundreds of forks would make it too expensive to be
    opportunistic. ``ww`` disables column truncation, without which a long
    environment is clipped and the variable silently disappears.

    Best-effort by design: macOS refuses the environment of SIP-protected/hardened
    binaries and lists only argv for them. A missing entry means "could not prove",
    never "not owned" — which is why ``worker_pgid`` exists as the second proof.

    The pattern is anchored on a whitespace boundary so a run id merely *mentioned*
    in some other process's command line (``--env SPAWN_RUN_ID:...``) cannot forge
    ownership; only a real ``SPAWN_RUN_ID=<id>`` token counts.
    """
    runner = _default_runner if runner is None else runner
    try:
        proc = runner(["ps", "axeww"])
    except Exception:  # noqa: BLE001 - no ps output means no environment evidence, never a crash
        return {}
    if getattr(proc, "returncode", 1) != 0:
        return {}
    index: dict[int, str] = {}
    for line in str(getattr(proc, "stdout", "") or "").splitlines():
        head = line.split(maxsplit=1)
        if not head or not head[0].isdigit():
            continue
        match = _ENV_RUN_ID_PATTERN.search(line)
        if match:
            index[int(head[0])] = match.group(1)
    return index


def _ancestors(pid: int, by_pid: Mapping[int, ProcessEntry]) -> set[int]:
    """Every pid up the parent chain from ``pid``, inclusive."""
    seen: set[int] = set()
    current = pid
    while current > 1 and current not in seen:
        seen.add(current)
        entry = by_pid.get(current)
        if entry is None:
            break
        current = entry.ppid
    return seen


def _is_protected(command: str) -> bool:
    """Whether ``command`` matches a :data:`PROTECTED_COMMAND_PATTERNS` entry."""
    lowered = command.lower()
    return any(pattern in lowered for pattern in PROTECTED_COMMAND_PATTERNS)


def _terminal_run_index(
    runs: Iterable[Mapping[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[int, str],
    dict[int, str],
    set[str],
    OwnershipBuckets,
]:
    """Map terminal runs by id, pgid ownership, legacy set, and ownership inventory.

    Legacy runs are inventoried and tracked in ``legacy_pgid_owner`` for KEEP
    reporting only — never in ``pgid_owner`` (kill source). A process that still
    carries their ``SPAWN_RUN_ID`` or matches a legacy-recorded pgid is reported
    in the legacy process bucket instead of doomed.
    """
    from .control_plane import _run_is_terminal

    by_run: dict[str, dict[str, Any]] = {}
    pgid_owner: dict[int, str] = {}
    legacy_pgid_owner: dict[int, str] = {}
    legacy_runs: set[str] = set()
    provable: list[str] = []
    legacy_ids: list[str] = []
    undecidable: list[str] = []

    for run in runs:
        run_id = str(run.get("run_id") or "").strip()
        if not run_id or not _run_is_terminal(dict(run)):
            continue
        payload = dict(run)
        by_run[run_id] = payload
        bucket = classify_run_ownership(payload)
        if bucket == OWNERSHIP_LEGACY:
            legacy_runs.add(run_id)
            legacy_ids.append(run_id)
            pgid = recorded_worker_pgid(payload)
            if pgid is not None:
                legacy_pgid_owner.setdefault(pgid, run_id)
            continue
        if bucket == OWNERSHIP_PROVABLE:
            provable.append(run_id)
            pgid = recorded_worker_pgid(payload)
            if pgid is not None:
                pgid_owner.setdefault(pgid, run_id)
            continue
        undecidable.append(run_id)

    ownership = OwnershipBuckets(
        provable=tuple(provable),
        legacy=tuple(legacy_ids),
        undecidable=tuple(undecidable),
    )
    return by_run, pgid_owner, legacy_pgid_owner, legacy_runs, ownership


def plan_reap(
    runs: Iterable[Mapping[str, Any]],
    table: Sequence[ProcessEntry],
    env_index: Mapping[int, str] | None = None,
    self_pid: int | None = None,
    env: Mapping[str, str] | None = None,
) -> ReapPlan:
    """Decide which survivors may be killed. Pure: signals nothing, touches nothing.

    The process table and env index are injected so unit tests can drive a fake
    machine with no real processes anywhere near the decision.
    """
    env = os.environ if env is None else env
    if not reaper_enabled(env):
        return ReapPlan(should_run=False, skip_reason="disabled")

    by_run, pgid_owner, legacy_pgid_owner, legacy_runs, ownership = _terminal_run_index(
        runs
    )
    if not by_run:
        return ReapPlan(should_run=True, ownership=ownership)

    env_index = {} if env_index is None else env_index
    self_pid = os.getpid() if self_pid is None else self_pid
    by_pid = {entry.pid: entry for entry in table}
    # Our own line of descent. The terminal-seam caller is itself a process of the
    # run being reaped; killing an ancestor would kill the reap mid-flight.
    protected_pids = _ancestors(self_pid, by_pid)

    doomed: list[ReapCandidate] = []
    unproven: list[ReapCandidate] = []
    protected: list[ReapCandidate] = []
    legacy_candidates: list[ReapCandidate] = []

    for entry in table:
        if entry.pid <= 1 or entry.pid in protected_pids:
            continue

        run_id = ""
        evidence = ""
        detail = ""
        is_legacy = False

        env_run_id = env_index.get(entry.pid, "")
        if env_run_id and env_run_id in by_run:
            if env_run_id in legacy_runs:
                is_legacy = True
                run_id, evidence = env_run_id, OWNERSHIP_LEGACY
                detail = f"reaper_ownership=legacy SPAWN_RUN_ID={env_run_id}"
            else:
                run_id, evidence = env_run_id, "env_run_id"
                detail = f"SPAWN_RUN_ID={env_run_id}"
        else:
            legacy_owner = legacy_pgid_owner.get(entry.pgid)
            if legacy_owner:
                is_legacy = True
                run_id, evidence = legacy_owner, OWNERSHIP_LEGACY
                detail = f"reaper_ownership=legacy pgid={entry.pgid}"
            else:
                owner = pgid_owner.get(entry.pgid)
                if owner:
                    run_id, evidence = owner, "worker_pgid"
                    detail = f"pgid={entry.pgid} matches run worker_pgid"

        if not evidence:
            # Only surface processes that look run-adjacent; the whole machine is
            # not an interesting "unproven" list.
            if env_run_id:
                unproven.append(
                    ReapCandidate(
                        pid=entry.pid,
                        command=entry.command,
                        run_id=env_run_id,
                        detail="SPAWN_RUN_ID names a run that is not terminal",
                    )
                )
            continue

        candidate = ReapCandidate(
            pid=entry.pid,
            command=entry.command,
            run_id=run_id,
            evidence=evidence,
            detail=detail,
        )
        if is_legacy or run_id in legacy_runs:
            legacy_candidates.append(candidate)
            continue
        if _is_protected(entry.command):
            protected.append(candidate)
        else:
            doomed.append(candidate)

    return ReapPlan(
        should_run=True,
        doomed=tuple(doomed),
        unproven=tuple(unproven),
        protected=tuple(protected),
        legacy=tuple(legacy_candidates),
        ownership=ownership,
    )


def _signal_pid(pid: int, sig: int) -> str:
    """Send ``sig``; report what the OS said. Never raises."""
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return "already_gone"
    except PermissionError:
        return "permission_denied"
    except OSError as exc:
        return f"error:{exc.errno}"
    return "signalled"


def _pid_alive(pid: int) -> bool:
    """Default ``alive_check`` callable, delegating to the control plane's probe."""
    from .control_plane import _pid_is_alive

    return _pid_is_alive(pid)


def execute_reap(
    plan: ReapPlan,
    grace: float | None = None,
    sleeper: Callable[[float], None] | None = None,
    alive_check: Callable[[int], bool] | None = None,
    signaller: Callable[[int, int], str] | None = None,
) -> dict[str, ReapReceipt]:
    """Escalate TERM -> grace -> KILL over the plan's doomed pids, receipting each step."""
    grace = grace_seconds() if grace is None else grace
    sleeper = time.sleep if sleeper is None else sleeper
    alive_check = _pid_alive if alive_check is None else alive_check
    signaller = _signal_pid if signaller is None else signaller

    receipts: dict[str, ReapReceipt] = {}

    def _receipt(run_id: str) -> ReapReceipt:
        return receipts.setdefault(run_id, ReapReceipt(run_id=run_id))

    if not plan.should_run:
        return receipts

    # Unproven survivors are reported in the plan (and so in `--dry-run`/`--json`),
    # but deliberately NOT written to a snapshot: by definition they name a run that
    # is still live, and a live run's record is not the place for our indecision.

    termed: list[tuple[ReapCandidate, dict[str, Any]]] = []
    for candidate in plan.doomed:
        row = candidate.row()
        row["term"] = signaller(candidate.pid, signal.SIGTERM)
        termed.append((candidate, row))

    # One shared grace window rather than per-pid: the processes were signalled
    # together, so they get to exit together.
    if termed and grace > 0:
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not any(alive_check(candidate.pid) for candidate, _ in termed):
                break
            sleeper(min(0.1, max(deadline - time.monotonic(), 0.0)))

    for candidate, row in termed:
        if alive_check(candidate.pid):
            result = signaller(candidate.pid, signal.SIGKILL)
            row["kill"] = result
            # Judge the delivery, not a liveness re-probe. SIGKILL cannot be caught,
            # so once it lands the process is dead — but it stays visible to
            # `kill(pid, 0)` as a zombie until its parent reaps it, and an orphan's
            # parent is by definition gone. Re-checking liveness here would report a
            # successful kill as "survived". Only a signal we failed to DELIVER
            # leaves something actually running.
            if result == "signalled":
                row["outcome"] = "killed"
            elif result == "already_gone":
                row["outcome"] = "exited"
            else:
                row["outcome"] = "survived"
        else:
            row["outcome"] = "exited"
        _receipt(candidate.run_id).reaped.append(row)

    return receipts


def _record_receipts(receipts: Mapping[str, ReapReceipt]) -> None:
    """Append reap receipts to each run's control-plane snapshot.

    Re-reads before writing: the control-plane sync may have touched the snapshot
    since the plan was built. Losing a receipt is acceptable; clobbering a run's
    terminal state to save one is not.
    """
    from .control_plane import _read_json, _snapshot_path, _write_json

    for run_id, receipt in receipts.items():
        if not run_id or run_id == "unknown":
            continue
        path = _snapshot_path(run_id)
        current = _read_json(path)
        if not current:
            continue
        current.update(receipt.payload())
        try:
            _write_json(path, current)
        except OSError:
            pass


def _terminal_run_snapshots() -> list[dict[str, Any]]:
    """All known runs, straight from the snapshot dir.

    Deliberately not ``sync_state()``: that returns only active plus the dozen most
    recent runs, and a two-day-old corpse — precisely our target — is in neither.
    """
    from .control_plane import _load_existing_snapshots

    try:
        return list(_load_existing_snapshots().values())
    except OSError:
        return []


def quarantine_legacy_runs(
    runs: Iterable[Mapping[str, Any]] | None = None,
    table: Sequence[ProcessEntry] | None = None,
    env_index: Mapping[int, str] | None = None,
    *,
    dry_run: bool = False,
    writer: Callable[[str, dict[str, Any]], None] | None = None,
) -> QuarantineResult:
    """One-shot migration: mark terminal runs without pgid as ``reaper_ownership: legacy``.

    Rules (fail-closed, no fiction):

    * Terminal + no valid recorded pgid → write ``reaper_ownership: legacy``.
    * Terminal + valid pgid → untouched (already provable).
    * Already ``reaper_ownership: legacy`` → no write (idempotent).
    * Live (non-terminal) → never marked legacy; best-effort recover
      ``worker_pgid`` from the process table **only** when ``SPAWN_RUN_ID`` is
      positively visible for that run (env proof). A bare pgid match is not enough.

    Historical JSON variants that break parsing are listed in ``parse_errors``
    and skipped rather than crashing the migration.
    """
    from .control_plane import (
        _run_is_terminal,
        _snapshot_path,
        _write_json,
    )

    result = QuarantineResult()

    if runs is None:
        try:
            run_list: list[dict[str, Any]] = _terminal_run_snapshots()
        except Exception as exc:  # pragma: no cover - defensive  # noqa: BLE001 - a corrupt snapshot store is reported, not raised
            result.parse_errors.append(f"load_snapshots:{exc}")
            return result
    else:
        run_list = []
        for raw in runs:
            try:
                run_list.append(dict(raw))
            except Exception as exc:  # noqa: BLE001 - one malformed row is recorded; the rest still coerce
                result.parse_errors.append(f"coerce:{exc}")

    proc_table = () if table is None else table
    # When the caller did not inject a table, only build one if we need live recovery.
    need_recovery = any(
        (not _run_is_terminal(r)) and recorded_worker_pgid(r) is None for r in run_list
    )
    if table is None and need_recovery:
        proc_table = build_process_table()
    index = {} if env_index is None else dict(env_index)
    if env_index is None and need_recovery:
        index = build_env_index()

    by_pid = {entry.pid: entry for entry in proc_table}

    def _default_writer(run_id: str, payload: dict[str, Any]) -> None:
        _write_json(_snapshot_path(run_id), payload)

    persist = writer if writer is not None else _default_writer

    for run in run_list:
        try:
            run_id = str(run.get("run_id") or "").strip()
            if not run_id:
                result.parse_errors.append("missing_run_id")
                continue
            payload = dict(run)

            if not _run_is_terminal(payload):
                # Live run: never legacy. Best-effort pgid recovery with env proof only.
                if recorded_worker_pgid(payload) is not None:
                    result.skipped_live.append(run_id)
                    continue
                recovered: int | None = None
                for pid, env_run_id in index.items():
                    if env_run_id != run_id:
                        continue
                    entry = by_pid.get(pid)
                    if entry is None:
                        continue
                    if entry.pgid > 1:
                        recovered = entry.pgid
                        break
                if recovered is None:
                    result.skipped_live.append(run_id)
                    continue
                payload["worker_pgid"] = recovered
                if not dry_run:
                    persist(run_id, payload)
                result.recovered_pgid.append(
                    {"run_id": run_id, "worker_pgid": recovered}
                )
                result.changed += 1
                continue

            # Terminal path.
            if str(payload.get(REAPER_OWNERSHIP_KEY) or "").strip() == OWNERSHIP_LEGACY:
                result.already_legacy.append(run_id)
                continue
            if recorded_worker_pgid(payload) is not None:
                result.skipped_has_pgid.append(run_id)
                continue

            payload[REAPER_OWNERSHIP_KEY] = OWNERSHIP_LEGACY
            if not dry_run:
                persist(run_id, payload)
            result.marked_legacy.append(run_id)
            result.changed += 1
        except Exception as exc:  # noqa: BLE001
            # Quarantine variants, never crash the doctor path.
            rid = str(run.get("run_id") or "?")
            result.parse_errors.append(f"{rid}:{type(exc).__name__}:{exc}")

    return result


def reap_terminal_runs(
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
    runs: Iterable[Mapping[str, Any]] | None = None,
    table: Sequence[ProcessEntry] | None = None,
    env_index: Mapping[int, str] | None = None,
) -> ReapPlan:
    """Find and terminate survivors of terminal runs. Never raises.

    Returns the plan so callers can render it; receipts land on the runs' snapshots.
    """
    env = os.environ if env is None else env
    try:
        if not reaper_enabled(env):
            return ReapPlan(should_run=False, skip_reason="disabled")
        run_list = _terminal_run_snapshots() if runs is None else list(runs)
        if not run_list:
            return ReapPlan(should_run=True)
        proc_table = build_process_table() if table is None else table
        if not proc_table:
            return ReapPlan(should_run=False, skip_reason="no_process_table")
        index = build_env_index() if env_index is None else env_index
        plan = plan_reap(run_list, proc_table, env_index=index, env=env)
        if dry_run or not plan.should_run:
            return plan
        receipts = execute_reap(plan, grace=grace_seconds(env))
        _record_receipts(receipts)
        return plan
    except Exception:  # noqa: BLE001
        # A garbage collector may never take down the thing it is cleaning up after.
        return ReapPlan(should_run=False, skip_reason="error")


def sweep_quietly(env: Mapping[str, str] | None = None) -> None:
    """Opportunistic pre-flight sweep. Silent, best-effort, never raises."""
    reap_terminal_runs(env=env)


def main(argv: Sequence[str] | None = None) -> int:
    """``vibecrafted-reap`` CLI entry point; always exits 0 (maintenance, not a gate)."""
    parser = argparse.ArgumentParser(
        prog="vibecrafted-reap",
        description="Terminate processes that outlived their run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the would-kill table with ownership evidence, signal nothing",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    plan = reap_terminal_runs(dry_run=args.dry_run)
    if args.json:
        print(
            json.dumps(
                plan_json_payload(plan, dry_run=args.dry_run),
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(plan.render())
    # Always 0: the reaper is maintenance, it never fails its caller.
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
