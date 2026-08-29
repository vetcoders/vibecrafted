"""Identity-qualified process control for vc-procs.

Snapshot + terminate surface that reuses ``run_reaper`` ownership / protection
policy. Never kill on PID alone: require start token + command hash (+ optional
run_id match).
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .run_reaper import (
    ProcessEntry,
    _is_protected,
    _pid_alive,
    _signal_pid,
    build_env_index,
    build_process_table,
    grace_seconds,
    plan_reap,
)

SCHEMA_VERSION = "vibecrafted.procs.v1"


def _load_run_payloads_light() -> list[dict[str, Any]]:
    """Best-effort run list without full control-plane board rebuild."""
    try:
        from .control_plane import run_snapshot_dir

        root = run_snapshot_dir()
    except (ImportError, OSError, AttributeError, RuntimeError, ValueError, TypeError):
        return []
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    try:
        for path in root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("run_id"):
                out.append(data)
    except OSError:
        return []
    return out


__all__ = [
    "SCHEMA_VERSION",
    "ProcessIdentity",
    "ProcessSnapshotRow",
    "TerminateOutcome",
    "capture_process_identity",
    "command_sha256",
    "main",
    "process_identity_receipt",
    "process_start_token",
    "snapshot_processes",
    "terminate_process",
    "validate_process_identity",
]


def command_sha256(command: str) -> str:
    """Stable hash of a process's full command line, used as identity evidence."""
    return hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()


def process_start_token(pid: int, command: str) -> str:
    """Stable-enough identity token for PID reuse detection.

    Prefer OS start time when readable; fall back to command hash + pid so a
    recycled PID with a different command fails the expected-start check.
    """
    start = _read_proc_start(pid)
    if start is not None:
        return f"start:{start}"
    return f"cmd:{pid}:{command_sha256(command)[:16]}"


def _read_proc_start(pid: int) -> int | None:
    """Best-effort process start seconds (unix). None if unavailable."""
    # macOS: ps -o lstart= is locale-heavy; use etime is worse. Prefer /proc on
    # Linux; on macOS use `ps -p PID -o lstart=` only if we can parse, else None.
    try:
        if Path(f"/proc/{pid}/stat").is_file():
            # field 22 is starttime in clock ticks since boot — not wall clock,
            # but stable for reuse detection within a boot.
            raw = Path(f"/proc/{pid}/stat").read_text(
                encoding="utf-8", errors="replace"
            )
            # comm can contain spaces/parens; split after last ')'
            rparen = raw.rfind(")")
            if rparen == -1:
                return None
            fields = raw[rparen + 2 :].split()
            if len(fields) >= 20:
                return int(fields[19])
    except (OSError, ValueError, IndexError):
        pass
    try:
        import subprocess

        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode != 0:
            return None
        text = (proc.stdout or "").strip()
        if not text:
            return None
        # Hash the lstart string — enough for reuse detection.
        return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


@dataclass(frozen=True)
class ProcessIdentity:
    """A captured OS identity snapshot for one pid, used for re-check before kill."""

    pid: int
    ppid: int
    pgid: int
    start_token: str
    command_sha256: str
    command: str


def capture_process_identity(
    pid: int,
    *,
    table: Sequence[ProcessEntry] | None = None,
) -> ProcessIdentity | None:
    """Capture the current OS identity behind one PID, or ``None`` if gone.

    A caller may persist the returned start token and command hash, then require
    an exact re-capture before signalling. This is deliberately stronger than
    storing a PID/PGID, both of which the kernel may reuse.
    """

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    current_table = build_process_table() if table is None else table
    entry = next((row for row in current_table if row.pid == pid), None)
    if entry is None:
        return None
    return ProcessIdentity(
        pid=entry.pid,
        ppid=entry.ppid,
        pgid=entry.pgid,
        start_token=process_start_token(entry.pid, entry.command),
        command_sha256=command_sha256(entry.command),
        command=entry.command,
    )


def process_identity_receipt(
    pid: int,
    *,
    run_id: str,
    table: Sequence[ProcessEntry] | None = None,
) -> dict[str, Any] | None:
    """Return the non-secret process identity persisted in run events/meta."""

    identity = capture_process_identity(pid, table=table)
    if identity is None:
        return None
    return {
        "pid": identity.pid,
        "ppid": identity.ppid,
        "pgid": identity.pgid,
        "start_token": identity.start_token,
        "command_sha256": identity.command_sha256,
        "run_id": str(run_id or "").strip(),
    }


def validate_process_identity(
    receipt: Mapping[str, Any] | None,
    *,
    expected_pid: int,
    expected_pgid: int | None,
    expected_run_id: str,
    table: Sequence[ProcessEntry] | None = None,
    env_index: Mapping[int, str] | None = None,
) -> tuple[bool, str, ProcessIdentity | None]:
    """Re-capture and validate a persisted identity before any signal.

    ``SPAWN_RUN_ID`` evidence is best-effort on macOS. If the OS exposes it, it
    must match; when it does not, the mandatory receipt run id plus start token,
    command hash, PID, and PGID still have to match exactly.

    Receipt-invalid and receipt-mismatch reasons are emitted before OS capture
    and are never proof of stale ownership. ``process_identity_mismatch`` is
    reserved for a complete receipt that reached capture and differed from the
    live identity.
    """

    if not isinstance(receipt, Mapping):
        return False, "process_identity_unavailable", None
    receipt_pid = receipt.get("pid")
    receipt_pgid = receipt.get("pgid")
    receipt_run_id = receipt.get("run_id")
    expected_start = receipt.get("start_token")
    expected_hash = receipt.get("command_sha256")
    if (
        isinstance(receipt_pid, bool)
        or not isinstance(receipt_pid, int)
        or receipt_pid <= 0
        or isinstance(receipt_pgid, bool)
        or not isinstance(receipt_pgid, int)
        or receipt_pgid <= 0
        or not isinstance(receipt_run_id, str)
        or receipt_run_id != receipt_run_id.strip()
        or not receipt_run_id
        or not isinstance(expected_start, str)
        or expected_start != expected_start.strip()
        or not expected_start
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(char not in "0123456789abcdef" for char in expected_hash)
    ):
        return False, "process_identity_receipt_invalid", None

    if (
        isinstance(expected_pid, bool)
        or not isinstance(expected_pid, int)
        or expected_pid <= 0
        or not isinstance(expected_run_id, str)
        or expected_run_id != expected_run_id.strip()
        or not expected_run_id
        or (
            expected_pgid is not None
            and (
                isinstance(expected_pgid, bool)
                or not isinstance(expected_pgid, int)
                or expected_pgid <= 0
            )
        )
    ):
        return False, "process_identity_expectation_invalid", None
    if (
        receipt_pid != expected_pid
        or receipt_run_id != expected_run_id
        or (expected_pgid is not None and receipt_pgid != expected_pgid)
    ):
        return False, "process_identity_receipt_mismatch", None

    identity = capture_process_identity(expected_pid, table=table)
    if identity is None:
        return False, "process_identity_gone", None
    if (
        identity.pid != receipt_pid
        or identity.pgid != receipt_pgid
        or identity.start_token != expected_start
        or identity.command_sha256 != expected_hash
    ):
        return False, "process_identity_mismatch", identity

    discovered = (
        build_env_index().get(expected_pid)
        if env_index is None
        else env_index.get(expected_pid)
    )
    if discovered and str(discovered).strip() != expected_run_id:
        return False, "process_run_id_mismatch", identity
    return True, "process_identity_current", identity


@dataclass(frozen=True)
class ProcessSnapshotRow:
    """One row of the ``vc-procs`` snapshot: a process plus its ownership verdict."""

    pid: int
    ppid: int
    pgid: int
    start_token: str
    command_sha256: str
    command: str
    run_id: str
    ownership: str  # proven | unproven | protected | legacy
    evidence: str
    killable: bool
    kill_reason: str

    def as_dict(self) -> dict[str, Any]:
        """Serialize this row for JSON output."""
        return asdict(self)


@dataclass
class TerminateOutcome:
    """Result of one ``terminate_process`` call."""

    ok: bool
    outcome: str
    pid: int
    receipt: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Serialize this outcome for JSON output."""
        return {
            "ok": self.ok,
            "outcome": self.outcome,
            "pid": self.pid,
            "detail": self.detail,
            "receipt": self.receipt,
        }


def _classify_row(
    entry: ProcessEntry,
    *,
    run_id: str,
    evidence: str,
    protected: bool,
    legacy: bool,
) -> ProcessSnapshotRow:
    """Combine a process-table entry with reaper classification into one row."""
    start = process_start_token(entry.pid, entry.command)
    cmd_hash = command_sha256(entry.command)
    if protected:
        ownership, killable, reason = "protected", False, "protected_command"
    elif legacy:
        ownership, killable, reason = "legacy", False, "legacy_ownership"
    elif evidence and run_id:
        ownership, killable, reason = "proven", True, "owned"
    else:
        ownership, killable, reason = "unproven", False, "ownership_unproven"
    identity = ProcessIdentity(
        pid=entry.pid,
        ppid=entry.ppid,
        pgid=entry.pgid,
        start_token=start,
        command_sha256=cmd_hash,
        command=entry.command[:300],
    )
    return ProcessSnapshotRow(
        pid=identity.pid,
        ppid=identity.ppid,
        pgid=identity.pgid,
        start_token=identity.start_token,
        command_sha256=identity.command_sha256,
        command=identity.command,
        run_id=run_id,
        ownership=ownership,
        evidence=evidence or "unproven",
        killable=killable,
        kill_reason=reason,
    )


def snapshot_processes(
    *,
    table: Sequence[ProcessEntry] | None = None,
    runs: Sequence[Mapping[str, Any]] | None = None,
    env_index: Mapping[int, str] | None = None,
    self_pid: int | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a machine-readable process snapshot for vc-procs."""
    env = os.environ if env is None else env
    if table is None:
        table = build_process_table()
    if runs is None:
        # Prefer lightweight snapshot directory read over full board sync_state
        # (sync_state is dashboard-hot and can lock for seconds).
        runs = _load_run_payloads_light()
    if env_index is None:
        env_index = {}
    self_pid = os.getpid() if self_pid is None else self_pid

    plan = plan_reap(runs, table, env_index=env_index, self_pid=self_pid, env=env)
    by_pid: dict[int, ProcessSnapshotRow] = {}

    for candidate in plan.doomed:
        entry = next((e for e in table if e.pid == candidate.pid), None)
        if entry is None:
            continue
        by_pid[entry.pid] = _classify_row(
            entry,
            run_id=candidate.run_id,
            evidence=candidate.evidence,
            protected=False,
            legacy=False,
        )
    for candidate in plan.protected:
        entry = next((e for e in table if e.pid == candidate.pid), None)
        if entry is None:
            continue
        by_pid[entry.pid] = _classify_row(
            entry,
            run_id=candidate.run_id,
            evidence=candidate.evidence,
            protected=True,
            legacy=False,
        )
    for candidate in plan.legacy:
        entry = next((e for e in table if e.pid == candidate.pid), None)
        if entry is None:
            continue
        by_pid[entry.pid] = _classify_row(
            entry,
            run_id=candidate.run_id,
            evidence=candidate.evidence,
            protected=False,
            legacy=True,
        )
    for candidate in plan.unproven:
        entry = next((e for e in table if e.pid == candidate.pid), None)
        if entry is None:
            continue
        by_pid[entry.pid] = _classify_row(
            entry,
            run_id=candidate.run_id,
            evidence="",
            protected=_is_protected(entry.command),
            legacy=False,
        )

    # Also list interesting family processes even if not reaper-tagged (display).
    for entry in table:
        if entry.pid in by_pid:
            continue
        if not _looks_vc_family(entry.command):
            continue
        protected = _is_protected(entry.command)
        by_pid[entry.pid] = _classify_row(
            entry,
            run_id="",
            evidence="",
            protected=protected,
            legacy=False,
        )

    rows = sorted(by_pid.values(), key=lambda r: (-len(r.command), r.pid))
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(rows),
        "processes": [r.as_dict() for r in rows],
    }


def _looks_vc_family(command: str) -> bool:
    """Whether ``command`` looks like part of the vetcoders/vibecrafted process
    family, for display-only inclusion in a snapshot even without reaper proof.
    """
    low = command.lower()
    markers = (
        "aicx-mcp",
        "loctree-mcp",
        "rmcp-mux",
        "vibecrafted",
        "codex",
        "claude",
        "agy ",
        "junie",
        "grok",
        "cursor",
        "cursor-agent",
        "mlx",
        "lbrx-stt",
        "ollama",
        "rust-memex",
        ".vibecrafted",
    )
    return any(m in low for m in markers)


def terminate_process(
    *,
    pid: int,
    expected_start: str,
    expected_command_sha256: str,
    expected_run_id: str = "",
    table: Sequence[ProcessEntry] | None = None,
    runs: Sequence[Mapping[str, Any]] | None = None,
    env_index: Mapping[int, str] | None = None,
    self_pid: int | None = None,
    env: Mapping[str, str] | None = None,
    grace: float | None = None,
    signaller: Callable[[int, int], str] | None = None,
    alive_check: Callable[[int], bool] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> TerminateOutcome:
    """TERM→grace→KILL only when identity and ownership re-check pass."""
    env = os.environ if env is None else env
    if table is None:
        table = build_process_table()
    if runs is None:
        runs = _load_run_payloads_light()
    if env_index is None:
        env_index = {}
    self_pid = os.getpid() if self_pid is None else self_pid
    signaller = _signal_pid if signaller is None else signaller
    alive_check = _pid_alive if alive_check is None else alive_check
    sleeper = time.sleep if sleeper is None else sleeper
    grace = grace_seconds(env) if grace is None else grace

    entry = next((e for e in table if e.pid == pid), None)
    if entry is None:
        return TerminateOutcome(
            ok=False, outcome="not_found", pid=pid, detail="pid_not_in_table"
        )

    live_start = process_start_token(entry.pid, entry.command)
    live_hash = command_sha256(entry.command)
    if live_start != expected_start or live_hash != expected_command_sha256:
        return TerminateOutcome(
            ok=False,
            outcome="stale_selection",
            pid=pid,
            detail="start_token_or_command_hash_mismatch",
            receipt={
                "expected_start": expected_start,
                "live_start": live_start,
                "expected_command_sha256": expected_command_sha256,
                "live_command_sha256": live_hash,
            },
        )

    snap = snapshot_processes(
        table=table, runs=runs, env_index=env_index, self_pid=self_pid, env=env
    )
    row = next((r for r in snap["processes"] if r["pid"] == pid), None)
    if row is None:
        return TerminateOutcome(
            ok=False, outcome="unproven", pid=pid, detail="not_in_snapshot"
        )
    if not row.get("killable"):
        return TerminateOutcome(
            ok=False,
            outcome=str(row.get("ownership") or "unproven"),
            pid=pid,
            detail=str(row.get("kill_reason") or "not_killable"),
            receipt=row,
        )
    if expected_run_id and row.get("run_id") and row["run_id"] != expected_run_id:
        return TerminateOutcome(
            ok=False,
            outcome="stale_selection",
            pid=pid,
            detail="run_id_mismatch",
            receipt={"expected_run_id": expected_run_id, "live_run_id": row["run_id"]},
        )

    receipt: dict[str, Any] = {
        "pid": pid,
        "run_id": row.get("run_id") or expected_run_id,
        "steps": [],
    }
    term = signaller(pid, signal.SIGTERM)
    receipt["steps"].append({"signal": "TERM", "result": term})
    if term == "already_gone":
        receipt["outcome"] = "already_gone"
        return TerminateOutcome(
            ok=True, outcome="already_gone", pid=pid, receipt=receipt
        )

    if grace > 0:
        sleeper(grace)
    if not alive_check(pid):
        receipt["outcome"] = "terminated"
        return TerminateOutcome(ok=True, outcome="terminated", pid=pid, receipt=receipt)

    kill = signaller(pid, signal.SIGKILL)
    receipt["steps"].append({"signal": "KILL", "result": kill})
    if kill == "already_gone" or not alive_check(pid):
        receipt["outcome"] = "killed"
        return TerminateOutcome(ok=True, outcome="killed", pid=pid, receipt=receipt)

    receipt["outcome"] = "survived"
    return TerminateOutcome(
        ok=False,
        outcome="survived",
        pid=pid,
        detail="still_alive_after_kill",
        receipt=receipt,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``vibecrafted procs`` CLI entry point: ``snapshot`` or ``terminate``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="vibecrafted procs")
    sub = parser.add_subparsers(dest="action", required=True)
    snap = sub.add_parser("snapshot", help="JSON process snapshot for vc-procs")
    snap.add_argument("--json", action="store_true", default=True)
    term = sub.add_parser("terminate", help="identity-qualified TERM→KILL")
    term.add_argument("--pid", type=int, required=True)
    term.add_argument("--expected-start", required=True)
    term.add_argument("--expected-command-sha256", required=True)
    term.add_argument("--expected-run-id", default="")
    term.add_argument("--json", action="store_true", default=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.action == "snapshot":
        payload = snapshot_processes()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.action == "terminate":
        outcome = terminate_process(
            pid=args.pid,
            expected_start=args.expected_start,
            expected_command_sha256=args.expected_command_sha256,
            expected_run_id=args.expected_run_id,
        )
        print(json.dumps(outcome.as_dict(), ensure_ascii=False, indent=2))
        return 0 if outcome.ok else 1

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
