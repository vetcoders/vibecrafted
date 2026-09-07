"""Ephemeral dispatch scheduler receipts and integrator exclusivity locks."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self

from vibecrafted_core.control_plane import control_plane_home
from vibecrafted_core.delivery.store import atomic_write_json

from .model import SCHEDULER_STATES, Cut

try:  # POSIX is the production scheduler substrate; keep importability elsewhere.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX development hosts
    fcntl = None  # type: ignore[assignment]


class ReceiptContractError(RuntimeError):
    """Raised when receipt state or an exclusivity lock violates the contract."""


class DispatchReceiptStore:
    """Single-writer per-run receipt ledger on the control-plane plane."""

    def __init__(
        self,
        run_id: str,
        cuts: tuple[Cut, ...],
        *,
        concurrency: int = 1,
        root: Path | None = None,
        repo_root: str = "",
        create: bool = True,
    ) -> None:
        if not run_id:
            raise ReceiptContractError("dispatch run_id is required")
        self.run_id = run_id
        self.root = (root or control_plane_home() / "dispatches" / run_id).resolve()
        self.path = self.root / "receipts.json"
        self._lock = threading.RLock()
        self._cut_ids = tuple(cut.id for cut in cuts)
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() and not create:
            raise ReceiptContractError(
                f"dispatch receipt ledger not found for run {run_id}: {self.path}"
            )
        if not self.path.exists():
            atomic_write_json(
                self.path,
                {
                    "schema": "vibecrafted.dispatch-receipts.v1",
                    "run_id": run_id,
                    "scheduler_mode": "dag-concurrent",
                    "configured_concurrency": concurrency,
                    "repo_root": str(Path(repo_root).expanduser().resolve())
                    if repo_root
                    else "",
                    "created_at": _now(),
                    "updated_at": _now(),
                    "cuts": {
                        cut.id: {
                            "cut_id": cut.id,
                            "state": "queued",
                            "dependencies": list(cut.depends_on),
                            "scheduler_slot": 0,
                            "integrator_exclusivity": cut.integrator,
                            "worktree_path": "",
                            "target_path": "",
                            "artifact_path": "",
                            "report_path": "",
                            "branch": "",
                            "baseline_sha": "",
                            "delivered_commit_sha": "",
                            "integrated_sha": "",
                            "gates": [],
                            "acceptance": "pending",
                            "cleanup_status": "retained",
                            "pid": None,
                            "provider_run_id": "",
                            "updated_at": _now(),
                        }
                        for cut in cuts
                    },
                },
            )

    def read(self) -> dict[str, Any]:
        with self._locked_ledger():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReceiptContractError(
                    f"dispatch receipt unreadable: {exc}"
                ) from exc
            if not isinstance(payload, dict) or payload.get("run_id") != self.run_id:
                raise ReceiptContractError("dispatch receipt identity mismatch")
            return payload

    def cut(self, cut_id: str) -> dict[str, Any]:
        payload = self.read()
        cut = payload.get("cuts", {}).get(cut_id, {})
        return dict(cut) if isinstance(cut, dict) else {}

    def update(self, cut_id: str, state: str | None = None, **fields: Any) -> None:
        if cut_id not in self._cut_ids:
            raise ReceiptContractError(f"unknown receipt cut {cut_id!r}")
        if state is not None and state not in SCHEDULER_STATES:
            raise ReceiptContractError(f"unknown scheduler state {state!r}")
        with self._locked_ledger():
            payload = self._read_unlocked()
            entry = payload["cuts"][cut_id]
            if state is not None:
                entry["state"] = state
                entry[f"{state}_at"] = _now()
                entry[f"{state}_epoch_ns"] = time.time_ns()
            entry.update(fields)
            entry["updated_at"] = _now()
            payload["updated_at"] = _now()
            atomic_write_json(self.path, payload)

    def update_metadata(self, **fields: Any) -> None:
        """Atomically merge scheduler-owned ledger metadata.

        Scheduler state and cut receipts share one file.  A separate
        read/replace writer loses a concurrent supervisor cut transition, so
        scheduler lifecycle fields must use this same lock and merge path.
        """
        with self._locked_ledger():
            payload = self._read_unlocked()
            payload.update(fields)
            payload["updated_at"] = _now()
            atomic_write_json(self.path, payload)

    def stop_requested(self) -> bool:
        with self._locked_ledger():
            return bool(self._read_unlocked().get("scheduler_stop_requested"))

    def admit_launch(self, cut_id: str, *, scheduler_slot: int = 0) -> bool:
        """Order one cut's launch against the stop fence inside a single lock hold.

        Reading the fence and then spawning are two moments; an interrupt that
        lands between them launches work the operator already stopped.  The
        scheduler and the lifecycle interrupt write this same ledger under the
        same flock, so admission is the ordering point: a launch is either
        recorded here before the fence, or refused by it.

        Admission deliberately does NOT move the cut into ``launching``. That
        state means "a previous owner already spawned this"; the supervisor
        reads it to recover rather than relaunch, so claiming it before the
        spawn would make every cut look like an orphan of itself.
        """
        if cut_id not in self._cut_ids:
            raise ReceiptContractError(f"unknown receipt cut {cut_id!r}")
        with self._locked_ledger():
            payload = self._read_unlocked()
            if payload.get("scheduler_stop_requested"):
                return False
            entry = payload["cuts"][cut_id]
            entry["launch_admitted_at"] = _now()
            entry["launch_admitted_epoch_ns"] = time.time_ns()
            entry["launch_admitted_by_pid"] = os.getpid()
            entry["scheduler_slot"] = scheduler_slot
            entry["updated_at"] = _now()
            payload["updated_at"] = _now()
            atomic_write_json(self.path, payload)
            return True

    def clear_stop_fence(self, **fields: Any) -> dict[str, Any]:
        """Lift an interrupt for an explicit resume, touching no cut receipt.

        Only the scheduler-owned fence and its stale failure note are cleared.
        Every live and settled cut receipt stays exactly as the previous owner
        left it, so recovery re-runs the unfinished cuts and can never
        duplicate a sibling it did not run.
        """
        with self._locked_ledger():
            payload = self._read_unlocked()
            previous = {
                "scheduler_stop_requested": bool(
                    payload.get("scheduler_stop_requested")
                ),
                "scheduler_error": str(payload.get("scheduler_error") or ""),
            }
            payload["scheduler_stop_requested"] = False
            payload["scheduler_error"] = ""
            payload.update(fields)
            payload["updated_at"] = _now()
            atomic_write_json(self.path, payload)
            return previous

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReceiptContractError(f"dispatch receipt unreadable: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("run_id") != self.run_id:
            raise ReceiptContractError("dispatch receipt identity mismatch")
        return payload

    def _locked_ledger(self):
        """Serialize independent scheduler and observer processes on POSIX."""

        class _LedgerLock:
            def __init__(inner, store: DispatchReceiptStore) -> None:
                inner.store = store
                inner.handle: Any = None

            def __enter__(inner):
                inner.store._lock.acquire()
                lock_path = inner.store.root / "receipts.lock"
                inner.handle = lock_path.open("a+")
                if fcntl is not None:
                    fcntl.flock(inner.handle.fileno(), fcntl.LOCK_EX)
                return inner

            def __exit__(inner, *_args: object) -> None:
                try:
                    if inner.handle is not None and fcntl is not None:
                        fcntl.flock(inner.handle.fileno(), fcntl.LOCK_UN)
                    if inner.handle is not None:
                        inner.handle.close()
                finally:
                    inner.store._lock.release()

        return _LedgerLock(self)


class IntegratorLease:
    """O_EXCL lease proving only one main-checkout integrator owns a repo."""

    def __init__(self, org: str, repo: str, run_id: str, cut_id: str) -> None:
        self.run_id = run_id
        self.cut_id = cut_id
        self.path = control_plane_home() / "dispatch_integrators" / org / f"{repo}.lock"
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": self.run_id,
            "cut_id": self.cut_id,
            "pid": os.getpid(),
            "acquired_at": _now(),
        }
        while True:
            try:
                descriptor = os.open(
                    self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError:
                existing = _read_json(self.path)
                pid = existing.get("pid")
                if isinstance(pid, int) and _pid_alive(pid):
                    raise ReceiptContractError(
                        "integrator exclusivity refused: "
                        f"repo already owned by run {existing.get('run_id') or '?'} "
                        f"cut {existing.get('cut_id') or '?'} pid {pid}"
                    )
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    continue
                continue
            try:
                os.write(descriptor, (json.dumps(payload) + "\n").encode("utf-8"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._owned = True
            return

    def release(self) -> None:
        if not self._owned:
            return
        existing = _read_json(self.path)
        if (
            existing.get("run_id") == self.run_id
            and existing.get("cut_id") == self.cut_id
        ):
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self._owned = False

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
