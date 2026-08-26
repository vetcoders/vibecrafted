"""Deterministic dispatch control plane: render, launch, await, verify, repair, record."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vibecrafted_core.delivery.model import ExecutionEnvelope
from vibecrafted_core.workflow import (
    LAUNCH_IDEMPOTENCY_KEY_ENV,
    WorkflowLaunchSpec,
    launch_workflow,
    reserve_run_id,
)

from .model import (
    STATE_FAILED,
    STATE_PENDING,
    STATE_UNKNOWN,
    STATE_VERIFIED,
    STATE_WORKER_DONE,
    Baton,
    Cut,
    Dispatch,
    Verdict,
)
from .receipts import DispatchReceiptStore, IntegratorLease
from .schema import render_cell_prompt, render_cut_verifies
from .verify import run_verifies
from .worktrees import WorktreeContractError, WorktreeGeometry, WorktreeManager

DEFAULT_POLL_S = 90.0
DEFAULT_TIMEOUT_MIN = 90.0
SUBSTRATE_FAILURE_MARKER = "SUBSTRATE_FAILURE"
RESULT_SCHEMA = "vibecrafted.dispatch-result.v1"

# Recovery report search tolerates clock skew between the supervisor wall
# clock and the worker's filesystem writes.
_MTIME_TOLERANCE_S = 1.0


class CellContractError(RuntimeError):
    """The external runtime ended without a trustworthy delivery envelope."""


@dataclass
class CellRun:
    """Process handle for one launched work cell.

    `proc` is set by in-process launchers (tests, future embedded runners)
    and awaited via Popen.poll(); production `launch_workflow` cells only
    expose `pid`, awaited via waitpid/kill(0) probing.
    """

    cut_id: str
    kind: str
    accepted: bool
    run_id: str = ""
    pid: int | None = None
    report_path: str = ""
    meta_path: str = ""
    exit_code: int | None = None
    error: str = ""
    proc: subprocess.Popen[Any] | None = None


CellLauncher = Callable[[Cut, str, str], CellRun]


@dataclass(frozen=True)
class AwaitOutcome:
    """Result of awaiting one launched cell: whether it finished, timed out, and its report."""

    finished: bool
    timed_out: bool
    elapsed_s: float
    report_path: str = ""
    report_text: str = ""
    recovered_by_mtime: bool = False


@dataclass(frozen=True)
class DispatchResult:
    """Final outcome of one full dispatch run: per-cut states, baton, and artifact paths."""

    line_broken: bool
    baton: Baton
    states: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    # Ordered per-cut entries (dispatch order, skipped cuts included) so
    # machine consumers can read result["cuts"][i]["state"] positionally.
    cuts: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Render this result as the ``vibecrafted.dispatch-result.v1`` JSON-safe mapping."""
        return {
            "schema": RESULT_SCHEMA,
            "line_broken": self.line_broken,
            "states": dict(self.states),
            "cuts": [dict(entry) for entry in self.cuts],
            "baton": self.baton.to_dict(),
            "artifacts": dict(self.artifacts),
        }


def workflow_cell_launcher(
    dispatch: Dispatch,
    *,
    source_dir: str | Path | None = None,
    dispatch_run_id: str = "",
) -> CellLauncher:
    """Production launcher: every cell goes through the existing
    `launch_workflow` runtime — the dispatch layer never spawns its own
    agent convention."""

    def launch(cut: Cut, prompt: str, kind: str) -> CellRun:
        """Launch one cell via ``launch_workflow`` and adapt its result into a ``CellRun``."""
        root = cut.runtime_root or dispatch.meta.repo
        base_dir = Path(root)
        spec = WorkflowLaunchSpec(
            agent=cut.agent,
            mode=cut.resolved_workflow,
            skill=cut.resolved_workflow,
            prompt=prompt,
            file="",
            runtime="headless",
            root=root,
            model=cut.model,
        )
        runtime_env = {
            "VIBECRAFTED_DISPATCH_CUT_ID": cut.id,
            "VIBECRAFTED_DISPATCH_WORKTREE": root,
            "VIBECRAFTED_DISPATCH_BRANCH": cut.runtime_branch,
            "VIBECRAFTED_DISPATCH_BASELINE_SHA": cut.baseline_sha,
            "VIBECRAFTED_DISPATCH_ARTIFACT_PATH": cut.artifact_path,
            "VIBECRAFTED_DISPATCH_DEPENDENCIES": ",".join(cut.depends_on),
            "VIBECRAFTED_DISPATCH_SCHEDULER_SLOT": str(cut.scheduler_slot),
            "VIBECRAFTED_DISPATCH_INTEGRATOR": str(cut.integrator).lower(),
        }
        if dispatch_run_id:
            runtime_env[LAUNCH_IDEMPOTENCY_KEY_ENV] = (
                f"dispatch:{dispatch_run_id}:cut:{cut.id}:attempt:{kind}"
            )
        if cut.target_path:
            runtime_env["CARGO_TARGET_DIR"] = cut.target_path
        result = launch_workflow(spec, base_dir, env=runtime_env)
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=bool(result.get("accepted")),
            run_id=str(result.get("run_id") or ""),
            pid=result.get("pid"),
            report_path=str(result.get("report") or ""),
            meta_path=str(result.get("meta") or ""),
            error=str(result.get("error") or result.get("message") or ""),
        )

    return launch


class DispatchSupervisor:
    """Deterministic control plane for one dispatch: render -> launch ->
    await -> substrate check -> verify -> repair -> state -> baton.

    The supervisor is the ONLY writer of tracker states. A cut flips from
    `[~]` to `[x]` exclusively after this class executed the declared
    verifiers and every matcher passed (measure-core invariant).
    """

    def __init__(
        self,
        dispatch: Dispatch,
        *,
        launcher: CellLauncher | None = None,
        artifacts_dir: str | Path | None = None,
        source_dir: str | Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        run_id: str = "",
        manage_worktrees: bool | None = None,
        resume: bool = False,
    ) -> None:
        """Resolve artifact paths (tracker/journal/handoff/result) and seed pending states."""
        self.dispatch = dispatch
        self.policy = dispatch.policy
        self.repo = dispatch.meta.repo
        self.run_id = run_id or reserve_run_id("dispatch")
        self.manage_worktrees = (
            launcher is None if manage_worktrees is None else manage_worktrees
        )
        self.worktrees = WorktreeManager(self.repo) if self.manage_worktrees else None
        self.launcher = launcher or workflow_cell_launcher(
            dispatch,
            source_dir=source_dir,
            dispatch_run_id=self.run_id,
        )
        self._sleep = sleep
        self._io_lock = threading.RLock()
        self._geometries: dict[str, WorktreeGeometry] = {}

        base = artifacts_dir
        if base is None and dispatch.meta.tracker:
            base = Path(dispatch.meta.tracker).expanduser().parent
        if base is None and dispatch.meta.reports_dir:
            base = Path(dispatch.meta.reports_dir).expanduser()
        if base is None:
            base = Path.cwd()
        self.artifacts_dir = Path(base)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        receipt_root = (
            None
            if self.manage_worktrees or run_id
            else self.artifacts_dir / ".test-runtime" / self.run_id
        )
        self._receipt_store = DispatchReceiptStore(
            self.run_id,
            dispatch.cuts,
            concurrency=dispatch.policy.concurrency,
            root=receipt_root,
            repo_root=self.repo,
            create=not resume,
        )

        tracker = dispatch.meta.tracker
        self.tracker_path = (
            Path(tracker).expanduser() if tracker else self.artifacts_dir / "tracker.md"
        )
        self.tracker_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.artifacts_dir / "journal.md"
        self.handoff_path = self.artifacts_dir / "handoff.md"
        self.result_path = self.artifacts_dir / "dispatch-result.json"
        self.prompts_dir = self.artifacts_dir / "prompts"

        self._states: dict[str, tuple[str, str]] = {
            cut.id: (STATE_PENDING, "pending") for cut in dispatch.cuts
        }

    # ------------------------------------------------------------------ run

    def run(self) -> DispatchResult:
        """Execute the cut DAG, launching every ready cut up to the configured limit.

        Always writes final artifacts (tracker/journal/handoff/result) via the
        ``finally`` clause, even when an unhandled exception aborts the loop.
        """
        verdicts: dict[str, Verdict] = {}
        baton = self.dispatch.empty_baton()
        line_broken = False
        result: DispatchResult | None = None
        poll_s, timeout_s = self._await_config()
        try:
            self._journal(
                f"dispatch start: {self.dispatch.meta.name!r} repo={self.repo}"
                f" cuts={len(self.dispatch.cuts)} repair_rounds={self.policy.repair_rounds}"
                f" on_critical_fail={self.policy.on_critical_fail}"
                f" on_timeout={self.policy.on_timeout}"
                f" concurrency={self.policy.concurrency} run_id={self.run_id}"
                f" await=poll {poll_s:g}s/timeout {timeout_s:g}s"
            )
            self._write_tracker()
            self._restore_settled_verdicts(verdicts)
            pending = {
                cut.id: cut for cut in self.dispatch.cuts if cut.id not in verdicts
            }
            active: dict[Future[Verdict], tuple[Cut, int]] = {}
            free_slots = set(range(1, self.policy.concurrency + 1))
            with ThreadPoolExecutor(
                max_workers=self.policy.concurrency,
                thread_name_prefix=f"dispatch-{self.run_id}",
            ) as pool:
                while pending or active:
                    made_progress = False
                    if line_broken and not active:
                        for stopped in pending.values():
                            self._set_state(
                                stopped.id,
                                STATE_PENDING,
                                "skipped: line broken upstream",
                            )
                            self._receipt_store.update(
                                stopped.id, "stopped", acceptance="fail-fast"
                            )
                        pending.clear()
                        break
                    completed_ok = {
                        cut_id for cut_id, verdict in verdicts.items() if verdict.ok
                    }
                    completed_bad = set(verdicts) - completed_ok
                    for cut_id, cut in list(pending.items()):
                        failed_dependencies = set(cut.depends_on) & completed_bad
                        if failed_dependencies:
                            verdict = Verdict(
                                cut_id=cut.id,
                                phase=cut.phase,
                                state=STATE_FAILED,
                                failures=(
                                    f"{cut.id}: stopped because dependencies failed: "
                                    + ", ".join(sorted(failed_dependencies)),
                                ),
                            )
                            verdicts[cut.id] = verdict
                            pending.pop(cut.id)
                            self._receipt_store.update(
                                cut.id,
                                "stopped",
                                acceptance="dependency-failed",
                                unresolved_surfaces=sorted(failed_dependencies),
                            )
                            self._set_state(
                                cut.id, STATE_FAILED, self._verdict_note(verdict)
                            )
                            made_progress = True
                            continue
                        if not set(cut.depends_on).issubset(completed_ok):
                            continue
                        if line_broken or not free_slots:
                            continue
                        if cut.integrator and active:
                            continue
                        if active and any(
                            active_cut.integrator for active_cut, _ in active.values()
                        ):
                            continue
                        slot = min(free_slots)
                        free_slots.remove(slot)
                        pending.pop(cut.id)
                        scheduled = replace(cut, scheduler_slot=slot)
                        snapshot = self._baton_from_verdicts(verdicts)
                        future = pool.submit(
                            self._run_scheduled_cut, scheduled, snapshot, verdicts
                        )
                        active[future] = (scheduled, slot)
                        made_progress = True
                    if active:
                        done, _ = wait(tuple(active), return_when=FIRST_COMPLETED)
                        for future in done:
                            cut, slot = active.pop(future)
                            free_slots.add(slot)
                            try:
                                verdict = future.result()
                            except Exception as exc:  # noqa: BLE001
                                # A worker verdict may fail independently; an
                                # exception here means the launch/evidence
                                # substrate itself breached its contract.
                                line_broken = True
                                verdict = Verdict(
                                    cut_id=cut.id,
                                    phase=cut.phase,
                                    state=STATE_FAILED,
                                    failures=(
                                        f"{cut.id}: supervisor exception: {type(exc).__name__}: {exc}",
                                    ),
                                )
                                self._receipt_store.update(
                                    cut.id,
                                    "failed",
                                    acceptance="supervisor-exception",
                                    unresolved_surfaces=list(verdict.failures),
                                )
                            verdicts[cut.id] = verdict
                            self._set_state(
                                cut.id, verdict.state, self._verdict_note(verdict)
                            )
                            if (
                                cut.critical
                                and not verdict.ok
                                and self.policy.on_critical_fail == "break"
                            ):
                                line_broken = True
                                self._journal(
                                    f"[{cut.id}] critical cut not verified ({verdict.state}):"
                                    " breaking the dispatch line"
                                )
                        made_progress = True
                    if not made_progress and pending:
                        unresolved = ", ".join(sorted(pending))
                        raise CellContractError(
                            f"scheduler deadlock: no ready cuts among {unresolved}"
                        )
            if line_broken:
                for cut in pending.values():
                    self._set_state(
                        cut.id, STATE_PENDING, "skipped: line broken upstream"
                    )
                    self._receipt_store.update(
                        cut.id, "stopped", acceptance="fail-fast"
                    )
            baton = self._baton_from_verdicts(verdicts)
            result = self._build_result(baton, line_broken)
            return result
        except Exception as exc:  # noqa: BLE001
            label = (
                "dispatch substrate failure"
                if isinstance(exc, CellContractError)
                else "supervisor exception"
            )
            message = f"{label}: {type(exc).__name__}: {exc}"
            self._journal(message)
            line_broken = True
            baton = self._baton_from_verdicts(verdicts)
            result = self._build_result(baton, line_broken)
            return result
        finally:
            final_result = result or self._build_result(baton, line_broken)
            self._write_final_artifacts(final_result)

    def _run_scheduled_cut(
        self, cut: Cut, baton: Baton, verdicts: dict[str, Verdict]
    ) -> Verdict:
        """Prepare one isolated root, hold integration exclusivity, and settle it."""
        runtime_cut = self._prepare_runtime_cut(cut, verdicts)
        receipt = self._receipt_store.cut(cut.id)
        if receipt.get("state") in {"launching", "active", "reported"}:
            resumed = self._resume_active_cut(runtime_cut, receipt)
            if resumed is not None:
                self._receipt_store.update(
                    runtime_cut.id,
                    "settled" if resumed.ok else "failed",
                    delivered_commit_sha=resumed.commit,
                    integrated_sha=resumed.commit if runtime_cut.integrator else "",
                    report_path=resumed.report,
                    acceptance="verified" if resumed.ok else "failed",
                    gates=[evidence.to_dict() for evidence in resumed.verifiers],
                    unresolved_surfaces=list(resumed.failures),
                )
                return resumed
        lease = None
        if runtime_cut.integrator and self.worktrees is not None:
            geometry = self._geometries[runtime_cut.id]
            lease = IntegratorLease(
                geometry.org, geometry.repo, self.run_id, runtime_cut.id
            )
            lease.acquire()
        try:
            self._receipt_store.update(
                runtime_cut.id,
                "integrating" if runtime_cut.integrator else "launching",
                scheduler_slot=runtime_cut.scheduler_slot,
            )
            self._set_state(
                runtime_cut.id,
                STATE_PENDING,
                (
                    "exclusive integrator launching in main checkout"
                    if runtime_cut.integrator
                    else f"scheduler launching worker in slot {runtime_cut.scheduler_slot}"
                ),
            )
            verdict = self._run_cut(runtime_cut, baton)
            state = "settled" if verdict.ok else "failed"
            self._receipt_store.update(
                runtime_cut.id,
                state,
                delivered_commit_sha=verdict.commit,
                integrated_sha=verdict.commit if runtime_cut.integrator else "",
                report_path=verdict.report,
                acceptance="verified" if verdict.ok else "failed",
                gates=[evidence.to_dict() for evidence in verdict.verifiers],
                unresolved_surfaces=list(verdict.failures),
            )
            return verdict
        finally:
            if lease is not None:
                lease.release()

    def _prepare_runtime_cut(self, cut: Cut, verdicts: dict[str, Verdict]) -> Cut:
        if self.worktrees is None:
            return cut
        baseline = self._baseline_for(cut, verdicts)
        previous = self._receipt_store.cut(cut.id)
        previous_root = Path(str(previous.get("worktree_path") or "")).expanduser()
        recovering_active = (
            previous.get("state") in {"launching", "active", "reported"}
            and bool(previous.get("worktree_path"))
            and previous_root.is_dir()
            and not cut.integrator
        )
        if recovering_active:
            geometry = WorktreeGeometry(
                org=self.worktrees.org,
                repo=self.worktrees.repo,
                day=self.worktrees.day,
                cut_id=cut.id,
                worktree_path=str(previous_root),
                branch=str(previous.get("branch") or f"cut/{cut.id}"),
                baseline_sha=str(previous.get("baseline_sha") or baseline),
                target_path=str(
                    previous.get("target_path") or previous_root / "target"
                ),
                artifact_path=str(self.worktrees.artifact_root),
                integrator_exclusive=False,
            )
            self.worktrees.recover_active(geometry)
        else:
            geometry = self.worktrees.prepare(
                cut.id,
                baseline,
                integrator=cut.integrator,
                allow_reuse=bool(previous.get("worktree_path")),
            )
        self._geometries[cut.id] = geometry
        self._receipt_store.update(
            cut.id,
            scheduler_slot=cut.scheduler_slot,
            worktree_path=geometry.worktree_path,
            target_path=geometry.target_path,
            artifact_path=geometry.artifact_path,
            branch=geometry.branch,
            baseline_sha=geometry.baseline_sha,
            integrator_exclusivity=geometry.integrator_exclusive,
        )
        return replace(
            cut,
            runtime_root=geometry.worktree_path,
            runtime_branch=geometry.branch,
            baseline_sha=geometry.baseline_sha,
            target_path=geometry.target_path,
            artifact_path=geometry.artifact_path,
        )

    def _baseline_for(self, cut: Cut, verdicts: dict[str, Verdict]) -> str:
        if not cut.depends_on:
            return str(self.dispatch.meta.baseline.get("head") or self._git_head())
        dependency_commits = [
            verdicts[dependency].commit
            for dependency in cut.depends_on
            if verdicts[dependency].commit
        ]
        if not dependency_commits:
            raise WorktreeContractError(
                f"[{cut.id}] dependencies supplied no delivered commit SHA"
            )
        if cut.integrator:
            return self._git_head()
        by_id = {planned.id: planned for planned in self.dispatch.cuts}
        non_integrated = [
            dependency
            for dependency in cut.depends_on
            if not by_id[dependency].integrator
        ]
        if non_integrated:
            raise WorktreeContractError(
                f"[{cut.id}] downstream workers require an exact integrated SHA; dependencies are not integrators: {', '.join(non_integrated)}"
            )
        declared_integrated = [
            str(self._receipt_store.cut(dependency).get("integrated_sha") or "")
            for dependency in cut.depends_on
        ]
        if any(not commit for commit in declared_integrated):
            raise WorktreeContractError(
                f"[{cut.id}] dependency receipt is missing its integrated_sha declaration"
            )
        if set(declared_integrated) != set(dependency_commits):
            raise WorktreeContractError(
                f"[{cut.id}] dependency verdict does not match its declared integrated_sha"
            )
        dependency_commits = declared_integrated
        candidate = dependency_commits[0]
        for commit in dependency_commits[1:]:
            if self._git_ok(["merge-base", "--is-ancestor", candidate, commit]):
                candidate = commit
            elif not self._git_ok(["merge-base", "--is-ancestor", commit, candidate]):
                raise WorktreeContractError(
                    f"[{cut.id}] dependency tips are not an integrated ancestry chain; add a named integrator cut"
                )
        return candidate

    def _baton_from_verdicts(self, verdicts: dict[str, Verdict]) -> Baton:
        baton = self.dispatch.empty_baton()
        for cut in self.dispatch.cuts:
            if cut.id in verdicts:
                baton = baton.append(verdicts[cut.id])
        return baton

    def _restore_settled_verdicts(self, verdicts: dict[str, Verdict]) -> None:
        for cut in self.dispatch.cuts:
            receipt = self._receipt_store.cut(cut.id)
            if receipt.get("state") != "settled":
                continue
            commit = str(receipt.get("delivered_commit_sha") or "")
            if commit:
                resolved = self._git(["rev-parse", "--verify", f"{commit}^{{commit}}"])
                reference = (
                    "HEAD"
                    if bool(receipt.get("integrator_exclusivity"))
                    else str(receipt.get("branch") or f"cut/{cut.id}")
                )
                if not resolved or not self._git_ok(
                    ["merge-base", "--is-ancestor", resolved, reference]
                ):
                    continue
            verdict = Verdict(
                cut_id=cut.id,
                phase=cut.phase,
                state=STATE_VERIFIED,
                commit=commit,
                report=str(receipt.get("report_path") or ""),
            )
            verdicts[cut.id] = verdict
            self._set_state(
                cut.id, STATE_VERIFIED, "restored from receipt and Git ancestry"
            )

    def _resume_active_cut(self, cut: Cut, receipt: dict[str, Any]) -> Verdict | None:
        pid = receipt.get("pid")
        if isinstance(pid, int) and self._pid_alive(pid):
            cell = CellRun(
                cut_id=cut.id,
                kind="resume",
                accepted=True,
                run_id=str(receipt.get("provider_run_id") or ""),
                pid=pid,
                report_path=str(receipt.get("report_path") or ""),
                meta_path=str(receipt.get("meta_path") or ""),
            )
            outcome = self._await(cell)
            failure = self._cell_contract_failure(cell, outcome)
            if failure:
                raise CellContractError(
                    f"[{cut.id}] resumed runtime contract failed: {failure}"
                )
            self._receipt_store.update(cut.id, "reported")
            verdict = self._verify(cut)
            commit = self._cut_delivery_head(cut) or self._git_head(cut)
            return replace(verdict, commit=commit, report=outcome.report_path)
        if receipt.get("report_path") and Path(str(receipt["report_path"])).is_file():
            verdict = self._verify(cut)
            commit = self._cut_delivery_head(cut) or self._git_head(cut)
            return replace(verdict, commit=commit, report=str(receipt["report_path"]))
        if receipt.get("state") in {"launching", "active", "reported"}:
            raise CellContractError(
                f"[{cut.id}] previous launch is no longer live and has no report; refusing duplicate launch"
            )
        return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            return False
        return True

    # -------------------------------------------------------------- per cut

    def _run_cut(self, cut: Cut, baton: Baton) -> Verdict:
        """Run one cut end-to-end: envelope gate, launch, timeout/repair handling, verify.

        Also enforces the ``require_commit`` WRITE-cut contract (clean start, new
        commit or valid idempotent proof, commit message identifies the cut).
        """
        blocked = self._envelope_block_failures(cut)
        if blocked:
            self._journal(
                f"[{cut.id}] blocked before spawn: envelope qualification failed:"
                f" {'; '.join(blocked)}"
            )
            return Verdict(
                cut_id=cut.id,
                phase=cut.phase,
                state=STATE_FAILED,
                failures=tuple(
                    f"{cut.id}: blocked before spawn: {reason}" for reason in blocked
                ),
            )
        prompt = render_cell_prompt(self.dispatch, cut, baton=baton)
        self._materialize_prompt(cut, "initial", prompt)
        git_before = self._git_state(cut)
        fleet_before = self._cut_delivery_head(cut)
        if cut.mode != "read" and self.policy.require_commit and git_before[1]:
            raise CellContractError(
                f"[{cut.id}] WRITE cut started from a dirty worktree: {git_before[1]}"
            )
        repair_attempts = 0

        outcome, launch_failure = self._execute_cell(cut, prompt, "initial")
        if launch_failure is not None:
            raise CellContractError(
                "; ".join(launch_failure.failures) or f"[{cut.id}] launch failed"
            )
        assert outcome is not None

        if outcome.timed_out:
            timeout_note = (
                f"timeout after {outcome.elapsed_s:.0f}s;"
                f" policy on_timeout={self.policy.on_timeout}"
            )
            self._set_state(cut.id, STATE_UNKNOWN, timeout_note)
            if self.policy.on_timeout == "fail":
                return Verdict(
                    cut_id=cut.id,
                    phase=cut.phase,
                    state=STATE_FAILED,
                    failures=(f"{cut.id}: {timeout_note}",),
                )
            if self.policy.on_timeout == "continue":
                return Verdict(
                    cut_id=cut.id,
                    phase=cut.phase,
                    state=STATE_UNKNOWN,
                    failures=(f"{cut.id}: {timeout_note}",),
                )
            # on_timeout == "repair"
            if self.policy.repair_rounds < 1:
                return Verdict(
                    cut_id=cut.id,
                    phase=cut.phase,
                    state=STATE_UNKNOWN,
                    failures=(f"{cut.id}: {timeout_note}; no repair rounds available",),
                )
            repair_attempts += 1
            repair_prompt = self._repair_prompt(
                prompt, (f"previous attempt timed out after {outcome.elapsed_s:.0f}s",)
            )
            self._materialize_prompt(cut, f"repair{repair_attempts}", repair_prompt)
            outcome, launch_failure = self._execute_cell(
                cut, repair_prompt, f"repair{repair_attempts}"
            )
            if launch_failure is not None:
                raise CellContractError(
                    "; ".join(launch_failure.failures)
                    or f"[{cut.id}] repair launch failed"
                )
            assert outcome is not None
            if outcome.timed_out:
                raise CellContractError(
                    f"[{cut.id}] repair round {repair_attempts} also timed out"
                )

        self._set_state(
            cut.id,
            STATE_WORKER_DONE,
            "worker finished; supervisor verification pending",
        )

        substrate = self._substrate_failure(cut, outcome)
        if substrate is not None:
            raise CellContractError("; ".join(substrate.failures))

        if cut.mode == "read":
            violation = self._read_violation(cut, git_before)
            if violation:
                self._journal(f"[{cut.id}] {violation}")
                return Verdict(
                    cut_id=cut.id,
                    phase=cut.phase,
                    state=STATE_FAILED,
                    report=outcome.report_path,
                    failures=(violation,),
                    repair_attempts=repair_attempts,
                )

        verdict = self._verify(cut)
        while (
            verdict.state == STATE_FAILED
            and repair_attempts < self.policy.repair_rounds
        ):
            repair_attempts += 1
            self._journal(
                f"[{cut.id}] verify failed; launching repair round"
                f" {repair_attempts}/{self.policy.repair_rounds}:"
                f" {'; '.join(verdict.failures) or 'no failure detail'}"
            )
            repair_prompt = self._repair_prompt(prompt, verdict.failures)
            self._materialize_prompt(cut, f"repair{repair_attempts}", repair_prompt)
            outcome2, launch_failure = self._execute_cell(
                cut, repair_prompt, f"repair{repair_attempts}"
            )
            if launch_failure is not None:
                raise CellContractError(
                    "; ".join(launch_failure.failures)
                    or f"[{cut.id}] repair launch failed"
                )
            assert outcome2 is not None
            if outcome2.timed_out:
                raise CellContractError(
                    f"[{cut.id}] repair round {repair_attempts} timed out"
                )
            substrate = self._substrate_failure(cut, outcome2)
            if substrate is not None:
                raise CellContractError("; ".join(substrate.failures))
            if cut.mode == "read":
                violation = self._read_violation(cut, git_before)
                if violation:
                    self._journal(f"[{cut.id}] {violation}")
                    return Verdict(
                        cut_id=cut.id,
                        phase=cut.phase,
                        state=STATE_FAILED,
                        report=outcome2.report_path,
                        failures=(violation,),
                        repair_attempts=repair_attempts,
                    )
            outcome = outcome2
            verdict = self._verify(cut)

        fleet_after = self._cut_delivery_head(cut)
        delivery_commit = fleet_after or self._git_head(cut)
        if cut.mode != "read" and self.policy.require_commit:
            if fleet_after or fleet_before:
                # Fleet Worktrees formation: the cut's evidence surface is its
                # own worktree/branch — the main checkout's HEAD never moves
                # and must not be judged.
                wt_status = self._cut_worktree_status(cut)
                if verdict.ok and wt_status:
                    raise CellContractError(
                        f"[{cut.id}] WRITE cut left uncommitted changes in its"
                        f" worktree after verification: {wt_status}"
                    )
                if verdict.ok and (not fleet_after or fleet_after == fleet_before):
                    existing = self._existing_delivery_commit(cut, outcome.report_text)
                    if not existing:
                        raise CellContractError(
                            f"[{cut.id}] WRITE cut produced no new commit on its"
                            " cut branch and supplied no valid idempotent"
                            " existing-commit proof: tip remained"
                            f" {fleet_after[:8] or '<unknown>'}"
                        )
                    delivery_commit = existing
                    self._journal(
                        f"[{cut.id}] accepted idempotent existing commit {existing[:8]}"
                    )
                elif verdict.ok and not self._commit_matches_cut(cut, fleet_after):
                    raise CellContractError(
                        f"[{cut.id}] cut-branch tip {fleet_after[:8]} does not"
                        " identify the delivered cut"
                    )
            else:
                head_before = git_before[0]
                head_after, status_after = self._git_state(cut)
                if verdict.ok and status_after:
                    raise CellContractError(
                        f"[{cut.id}] WRITE cut left uncommitted changes after"
                        f" verification: {status_after}"
                    )
                if verdict.ok and (
                    not head_before or not head_after or head_after == head_before
                ):
                    existing = self._existing_delivery_commit(cut, outcome.report_text)
                    if not existing:
                        raise CellContractError(
                            f"[{cut.id}] WRITE cut produced no new commit and"
                            " supplied no valid idempotent existing-commit proof:"
                            f" HEAD remained {head_after[:8] or '<unknown>'}"
                        )
                    delivery_commit = existing
                    self._journal(
                        f"[{cut.id}] accepted idempotent existing commit {existing[:8]}"
                    )
                elif verdict.ok and not self._commit_matches_cut(cut, head_after):
                    raise CellContractError(
                        f"[{cut.id}] new HEAD {head_after[:8]} does not identify"
                        " the delivered cut"
                    )

        return replace(
            verdict,
            commit=delivery_commit,
            report=outcome.report_path,
            repair_attempts=repair_attempts,
        )

    # ------------------------------------------------------- cell execution

    def _execute_cell(
        self, cut: Cut, prompt: str, kind: str
    ) -> tuple[AwaitOutcome | None, Verdict | None]:
        """Launch one cell, await it, and check its contract.

        Returns ``(outcome, None)`` on a normal (possibly timed-out) completion, or
        ``(None, verdict)`` for a launch-time refusal/crash. Raises
        ``CellContractError`` when the finished cell fails the exit/meta/report contract.
        """
        try:
            cell = self.launcher(cut, prompt, kind)
        except Exception as exc:  # noqa: BLE001
            message = f"{kind} launch crashed: {type(exc).__name__}: {exc}"
            self._journal(f"[{cut.id}] {message}")
            return None, Verdict(
                cut_id=cut.id,
                phase=cut.phase,
                state=STATE_FAILED,
                failures=(f"{cut.id}: {message}",),
            )
        if not cell.accepted:
            message = f"{kind} launch refused: {cell.error or 'unknown error'}"
            self._journal(f"[{cut.id}] {message}")
            return None, Verdict(
                cut_id=cut.id,
                phase=cut.phase,
                state=STATE_FAILED,
                failures=(f"{cut.id}: {message}",),
            )
        self._journal(
            f"[{cut.id}] {kind} cell launched:"
            f" run_id={cell.run_id or '?'} pid={cell.pid or '?'}"
            f" report={cell.report_path or '?'}"
        )
        self._receipt_store.update(
            cut.id,
            "active",
            provider_run_id=cell.run_id,
            pid=cell.pid,
            report_path=cell.report_path,
            meta_path=cell.meta_path,
        )
        self._set_state(
            cut.id,
            STATE_PENDING,
            f"worker active in scheduler slot {cut.scheduler_slot}",
        )
        outcome = self._await(cell)
        if outcome.timed_out:
            self._terminate(cell)
            self._journal(
                f"[{cut.id}] {kind} cell timed out after {outcome.elapsed_s:.0f}s;"
                " process terminated"
            )
        else:
            recovered = " (recovered by mtime)" if outcome.recovered_by_mtime else ""
            self._journal(
                f"[{cut.id}] {kind} cell finished in {outcome.elapsed_s:.1f}s;"
                f" report={outcome.report_path or 'MISSING'}{recovered}"
            )
            failure = self._cell_contract_failure(cell, outcome)
            if failure:
                self._journal(f"[{cut.id}] {failure}")
                raise CellContractError(f"[{cut.id}] {failure}")
            self._receipt_store.update(
                cut.id,
                "reported",
                report_path=outcome.report_path,
                pid=None,
            )
        return outcome, None

    def _cell_contract_failure(self, cell: CellRun, outcome: AwaitOutcome) -> str:
        """Check exit code, meta.json status, and report presence/non-emptiness.

        Returns "" when the contract is satisfied, else a description of the breach.
        """
        if cell.exit_code not in (None, 0):
            return f"runtime process failed: exit_code={cell.exit_code}"

        meta: dict[str, Any] = {}
        if cell.meta_path:
            try:
                payload = json.loads(
                    Path(cell.meta_path).expanduser().read_text(encoding="utf-8")
                )
                if isinstance(payload, dict):
                    meta = payload
            except (OSError, json.JSONDecodeError) as exc:
                return (
                    "runtime contract failed: meta missing or unreadable"
                    f" ({type(exc).__name__}: {exc})"
                )

        if cell.meta_path:
            status = str(meta.get("status") or "").strip().lower()
            exit_code = meta.get("exit_code")
            if status != "completed" or exit_code not in (0, "0"):
                detail = str(meta.get("error") or meta.get("last_error") or "").strip()
                suffix = f"; {detail}" if detail else ""
                return (
                    "runtime contract failed:"
                    f" status={status or '<unknown>'}"
                    f" exit_code={exit_code!r}{suffix}"
                )
        elif cell.proc is None and cell.exit_code is None:
            return "runtime contract failed: exit status unavailable"

        if not outcome.report_path:
            return (
                "runtime contract failed: report missing"
                f" for run_id={cell.run_id or '<unknown>'}"
            )
        if not outcome.report_text.strip():
            return (
                "runtime contract failed: report empty"
                f" for run_id={cell.run_id or '<unknown>'}"
            )
        return ""

    def _existing_delivery_commit(self, cut: Cut, report_text: str) -> str:
        """Accept a worker-claimed pre-existing commit as this cut's delivery, if provable.

        Requires the commit to resolve, be an ancestor of HEAD, and identify the cut.
        """
        if not self.policy.allow_idempotent_existing:
            return ""
        match = re.search(
            r"(?mi)^(?:commit|commit_sha|sha|head_sha):"
            r"\s*(?P<quote>[`'\"]?)(?P<sha>[0-9a-f]{7,40})(?P=quote)\s*$",
            report_text,
        )
        if match is None:
            return ""
        root = self._cut_root(cut)
        resolved = self._git(
            ["rev-parse", "--verify", f"{match.group('sha')}^{{commit}}"],
            repo=root,
        )
        if not resolved or not self._git_ok(
            ["merge-base", "--is-ancestor", resolved, "HEAD"], repo=root
        ):
            return ""
        if not self._commit_matches_cut(cut, resolved):
            return ""
        return resolved

    def _commit_matches_cut(self, cut: Cut, commit: str) -> bool:
        """Check the commit message names this cut's id or its slot bracket tag."""
        message = self._git(
            ["show", "-s", "--format=%B", commit], repo=self._cut_root(cut)
        )
        slot = cut.id.split("_", 1)[0]
        return cut.id in message or f"[{slot}]" in message

    def _await(self, cell: CellRun) -> AwaitOutcome:
        """Await the cell through its process handle, never through meta.json
        state — the launcher-truth prerequisite says terminal metadata may
        lie, the process cannot."""
        poll_s, timeout_s = self._await_config()
        started = time.monotonic()
        wall_started = time.time()
        while not self._cell_finished(cell):
            elapsed = time.monotonic() - started
            if elapsed >= timeout_s:
                return AwaitOutcome(finished=False, timed_out=True, elapsed_s=elapsed)
            self._sleep(poll_s)
        elapsed = time.monotonic() - started
        report_path, report_text, recovered = self._resolve_report(cell, wall_started)
        return AwaitOutcome(
            finished=True,
            timed_out=False,
            elapsed_s=elapsed,
            report_path=report_path,
            report_text=report_text,
            recovered_by_mtime=recovered,
        )

    def _cell_finished(self, cell: CellRun) -> bool:
        """Poll one cell's liveness: Popen.poll() in-process, else waitpid/kill(0) probing."""
        if cell.proc is not None:
            exit_code = cell.proc.poll()
            if exit_code is None:
                return False
            cell.exit_code = exit_code
            return True
        if not cell.pid:
            return True
        try:
            done, status = os.waitpid(cell.pid, os.WNOHANG)
        except ChildProcessError:
            pass  # not our child: fall through to the liveness probe
        except OSError:
            pass
        else:
            if done == cell.pid:
                cell.exit_code = os.waitstatus_to_exitcode(status)
                return True
            return False
        try:
            os.kill(cell.pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False

    def _terminate(self, cell: CellRun) -> None:
        """Kill a timed-out cell's process (or process group for launch_workflow pids)."""
        try:
            if cell.proc is not None:
                cell.proc.terminate()
            elif cell.pid:
                # launch_workflow spawns with start_new_session, so the pid
                # doubles as the process-group id.
                os.killpg(cell.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _resolve_report(
        self, cell: CellRun, wall_started: float
    ) -> tuple[str, str, bool]:
        """Read the announced report, or recover the freshest report written since launch.

        Returns ``(path, text, recovered_by_mtime)``; all-empty when nothing is found.
        """
        announced = Path(cell.report_path).expanduser() if cell.report_path else None
        if announced is not None and announced.is_file():
            try:
                return str(announced), announced.read_text(encoding="utf-8"), False
            except OSError:
                pass
        if self.manage_worktrees or self.policy.concurrency > 1:
            # Concurrent workers have preallocated report identities. Guessing by
            # mtime can attribute a sibling's report and is therefore forbidden.
            return "", "", False
        # Legacy serial recovery ONLY: the announced path is broken, search reports_dir
        # for the freshest report written since launch.
        reports_dir = (
            Path(self.dispatch.meta.reports_dir).expanduser()
            if self.dispatch.meta.reports_dir
            else None
        )
        if reports_dir is not None and reports_dir.is_dir():
            candidates = [
                path
                for path in reports_dir.glob("*.md")
                if path.is_file()
                and path.stat().st_mtime >= wall_started - _MTIME_TOLERANCE_S
            ]
            if candidates:
                newest = max(candidates, key=lambda path: path.stat().st_mtime)
                self._journal(
                    f"[{cell.cut_id}] announced report path broken"
                    f" ({cell.report_path or 'none'}); recovered by mtime:"
                    f" {newest}"
                )
                try:
                    return str(newest), newest.read_text(encoding="utf-8"), True
                except OSError:
                    pass
        return "", "", False

    # ------------------------------------------------------------- checks

    def _substrate_failure(self, cut: Cut, outcome: AwaitOutcome) -> Verdict | None:
        """Detect a worker-reported SUBSTRATE_FAILURE marker and build its failed verdict."""
        if SUBSTRATE_FAILURE_MARKER not in outcome.report_text:
            return None
        line = next(
            (
                stripped
                for stripped in outcome.report_text.splitlines()
                if SUBSTRATE_FAILURE_MARKER in stripped
            ),
            SUBSTRATE_FAILURE_MARKER,
        ).strip()
        self._journal(f"[{cut.id}] substrate failure reported by worker: {line}")
        return Verdict(
            cut_id=cut.id,
            phase=cut.phase,
            state=STATE_FAILED,
            report=outcome.report_path,
            failures=(f"{cut.id}: {line}",),
        )

    def _read_violation(self, cut: Cut, before: tuple[str, str]) -> str:
        """Detect a READ cut mutating the repo against its declared mutation policy."""
        # "allow-report-only" permits artifact writes outside the repo;
        # any in-repo git drift still violates it, same as "forbid".
        if cut.mutation == "allow":
            return ""
        after = self._git_state(cut)
        if before == after:
            return ""
        policy = cut.mutation or "forbid"
        head_before = before[0][:8] or "<none>"
        head_after = after[0][:8] or "<none>"
        drift = after[1] if after[1] != before[1] else ""
        return (
            f"READ cut mutated the repository (mutation policy {policy!r}):"
            f" HEAD {head_before} -> {head_after};"
            f" status drift: {drift.strip() or '<head moved>'}"
        )

    def _verify(self, cut: Cut) -> Verdict:
        """Run the cut's rendered verifiers and journal each verifier's outcome."""
        if self.policy.verify_executor != "supervisor":
            self._journal(
                f"[{cut.id}] verify_executor={self.policy.verify_executor!r}"
                " is not supported yet; falling back to supervisor execution"
            )
        root = cut.runtime_root or self.repo
        verifier_env = (
            {"CARGO_TARGET_DIR": cut.target_path} if cut.target_path else None
        )
        verdict = run_verifies(
            render_cut_verifies(self.dispatch, cut), repo=root, env=verifier_env
        )
        for evidence in verdict.verifiers:
            self._journal(
                f"[{cut.id}] verifier {evidence.matcher_result}:"
                f" {evidence.command!r} exit={evidence.exit_code}"
                f" ({evidence.elapsed_ms}ms)"
            )
        for failure in verdict.failures:
            self._journal(f"[{cut.id}] verifier failure: {failure}")
        if verdict.ok:
            self._receipt_store.update(
                cut.id,
                "verified",
                gates=[evidence.to_dict() for evidence in verdict.verifiers],
            )
        return verdict

    def _repair_prompt(self, prompt: str, failures: tuple[str, ...]) -> str:
        """Append a REPAIR ROUND directive citing prior failure evidence to the base prompt."""
        details = "\n".join(f"- {failure}" for failure in failures)
        return (
            f"{prompt}\n\nREPAIR ROUND — the supervisor refuted the previous"
            f" attempt with this evidence:\n{details or '- no failure detail'}\n"
            "Fix the refuted surface, re-run the gates, and commit the repair."
        )

    # -------------------------------------------------- envelope gate

    def _envelope_block_failures(self, cut: Cut) -> tuple[str, ...]:
        """Qualify the ExecutionEnvelope against the live checkout (spec §7.1).

        Called before ANY spawn. Any mismatch between the declared envelope
        and the observed agent/repo/root/branch/HEAD/brief digest/dirty state
        blocks the cut before the worker process exists. Absent envelope
        keeps the legacy path unchanged.
        """
        envelope = self.dispatch.envelope
        if envelope is None:
            return ()
        failures: list[str] = []
        if cut.agent != envelope.agent:
            failures.append(
                f"agent mismatch: declared {envelope.agent!r},"
                f" observed cut agent {cut.agent!r}"
            )
        observed_repo = _repo_identity_from_url(
            self._git(["remote", "get-url", "origin"])
        )
        if observed_repo != envelope.repo:
            failures.append(
                f"repo identity mismatch: declared {envelope.repo!r},"
                f" observed {observed_repo or '<none>'!r}"
            )
        observed_root = self._git(["rev-parse", "--show-toplevel"])
        declared_root = (
            str(Path(envelope.root).expanduser().resolve()) if envelope.root else ""
        )
        resolved_root = str(Path(observed_root).resolve()) if observed_root else ""
        if not resolved_root or resolved_root != declared_root:
            failures.append(
                f"root mismatch: declared {envelope.root!r},"
                f" observed {observed_root or '<none>'!r}"
            )
        observed_branch = self._git(["branch", "--show-current"])
        if observed_branch != envelope.branch:
            failures.append(
                f"branch mismatch: declared {envelope.branch!r},"
                f" observed {observed_branch or '<none>'!r}"
            )
        observed_head = self._git_head()
        if observed_head != envelope.expected_head:
            failures.append(
                f"HEAD mismatch: declared {envelope.expected_head!r},"
                f" observed {observed_head or '<none>'!r}"
            )
        declared_digest = _normalize_digest(envelope.brief_sha256)
        try:
            brief_bytes = Path(envelope.brief_path).expanduser().read_bytes()
        except OSError as exc:
            failures.append(
                f"brief unreadable at {envelope.brief_path!r}:"
                f" {type(exc).__name__}: {exc}"
            )
        else:
            observed_digest = f"sha256:{hashlib.sha256(brief_bytes).hexdigest()}"
            if observed_digest != declared_digest:
                failures.append(
                    f"brief digest mismatch: declared {declared_digest},"
                    f" observed {observed_digest}"
                )
        failures.extend(self._dirty_policy_failures(envelope))
        return tuple(failures)

    def _dirty_policy_failures(self, envelope: ExecutionEnvelope) -> list[str]:
        """Enforce living-tree-scoped dirt policy: only dirt inside owned_paths blocks launch."""
        if envelope.dirty_policy != "living-tree-scoped":
            # Fail closed: an unknown policy never degrades to "allow".
            return [
                (
                    f"unsupported dirty_policy {envelope.dirty_policy!r}:"
                    " refusing to launch"
                )
            ]
        # Living tree: dirt outside the cut's owned paths is expected and
        # allowed; dirt inside owned paths would poison attribution.
        dirty = self._dirty_paths()
        owned_dirty = sorted(
            path for path in dirty if _path_in_scope(path, envelope.owned_paths)
        )
        if owned_dirty:
            return [
                f"dirty files inside owned_paths at launch: {', '.join(owned_dirty)}"
            ]
        return []

    def _dirty_paths(self) -> set[str]:
        """Parse `git status --porcelain` into the set of dirty (and rename-target) paths."""
        paths: set[str] = set()
        # `_git` strips stdout, which eats the leading status column of the
        # first porcelain line — split on whitespace instead of slicing.
        for line in self._git(["status", "--porcelain"]).splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            for part in parts[1].split(" -> "):
                cleaned = part.strip().strip('"')
                if cleaned:
                    paths.add(cleaned)
        return paths

    # ------------------------------------------------------------- git

    def _git_state(self, cut: Cut | None = None) -> tuple[str, str]:
        """Return ``(HEAD sha, porcelain status)`` as one drift-detection snapshot."""
        return self._git_head(cut), self._git(
            ["status", "--porcelain"], repo=self._cut_root(cut)
        )

    def _git_head(self, cut: Cut | None = None) -> str:
        """Return the repo's current HEAD sha, or "" if it cannot be resolved."""
        return self._git(["rev-parse", "HEAD"], repo=self._cut_root(cut))

    def _cut_root(self, cut: Cut | None) -> str:
        return cut.runtime_root if cut is not None and cut.runtime_root else self.repo

    def _cut_worktree_dir(self, cut: Cut) -> Path:
        """Return this cut's centrally resolved provider-neutral checkout."""
        if cut.runtime_root:
            return Path(cut.runtime_root)
        # Recovery-only compatibility lookup. New dispatches never create here.
        return Path(self.repo) / ".claude" / "worktrees" / cut.id

    def _cut_delivery_head(self, cut: Cut) -> str:
        """Resolve the cut's OWN delivery tip under the Fleet Worktrees formation.

        Mode B puts each cut on ``cut/<id>`` inside ``.claude/worktrees/<id>``;
        the worker's commit never moves the main checkout's HEAD, so the main
        HEAD is the wrong evidence surface there (field bug 2026-08-10: the
        tracker recorded the baseline sha as a verified cut's evidence).
        Prefers the cut worktree's HEAD, falls back to the ``cut/<id>`` branch
        tip, and returns "" when neither exists — Living Tree mode, where the
        caller keeps judging the main HEAD.
        """
        worktree = self._cut_worktree_dir(cut)
        if worktree.is_dir():
            head = self._git(["-C", str(worktree), "rev-parse", "HEAD"])
            if head:
                return head
        return self._git(
            ["rev-parse", "--verify", "--quiet", f"cut/{cut.id}^{{commit}}"]
        )

    def _cut_worktree_status(self, cut: Cut) -> str:
        """Porcelain status inside the cut's worktree; "" when it does not exist."""
        worktree = self._cut_worktree_dir(cut)
        if not worktree.is_dir():
            return ""
        return self._git(["-C", str(worktree), "status", "--porcelain"])

    def _git(self, args: list[str], *, repo: str | Path | None = None) -> str:
        """Run a git command in the dispatch repo; return trimmed stdout, or "" on failure."""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=repo or self.repo,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def _git_ok(self, args: list[str], *, repo: str | Path | None = None) -> bool:
        """Run a git command in the dispatch repo; return True only on exit code 0."""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=repo or self.repo,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0

    # --------------------------------------------------------- artifacts

    def _await_config(self) -> tuple[float, float]:
        """Resolve ``(poll_s, timeout_s)`` from policy.await_config, clamped to sane minimums."""
        config = self.policy.await_config or {}
        poll_s = float(config.get("poll_s", DEFAULT_POLL_S))
        timeout_s = float(config.get("timeout_min", DEFAULT_TIMEOUT_MIN)) * 60.0
        return max(poll_s, 0.01), max(timeout_s, 0.01)

    def _materialize_prompt(self, cut: Cut, kind: str, prompt: str) -> None:
        """Write a rendered prompt to ``prompts/<cut_id>_<kind>.md`` for provenance."""
        with self._io_lock:
            self.prompts_dir.mkdir(parents=True, exist_ok=True)
            path = self.prompts_dir / f"{cut.id}_{kind}.md"
            path.write_text(prompt, encoding="utf-8")

    def _set_state(self, cut_id: str, state: str, note: str) -> None:
        """Update one cut's in-memory state, journal the transition, and rewrite the tracker."""
        with self._io_lock:
            self._states[cut_id] = (state, note)
            self._journal(f"[{cut_id}] state {state}: {note}")
            self._write_tracker()

    def _verdict_note(self, verdict: Verdict) -> str:
        """Compose a one-line tracker note summarizing a verdict's commit/verifiers/failures."""
        parts: list[str] = []
        if verdict.commit:
            parts.append(f"commit {verdict.commit[:8]}")
        if verdict.verifiers:
            green = sum(1 for evidence in verdict.verifiers if evidence.ok)
            parts.append(f"verifiers {green}/{len(verdict.verifiers)} green")
        if verdict.repair_attempts:
            parts.append(f"repair rounds {verdict.repair_attempts}")
        if verdict.failures:
            parts.append(verdict.failures[0])
        return "; ".join(parts) or "no evidence recorded"

    def _journal(self, message: str) -> None:
        """Append one timestamped line to journal.md."""
        with self._io_lock:
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self.journal_path.open("a", encoding="utf-8") as handle:
                handle.write(f"- {timestamp} {message}\n")

    def _write_tracker(self) -> None:
        """Rewrite tracker.md in full from the current in-memory per-cut states."""
        meta = self.dispatch.meta
        lines = [
            f"# dispatch tracker — {meta.name or 'unnamed'}",
            "",
            f"- repo: {meta.repo}",
            f"- baseline_branch: {meta.baseline.get('branch', '')}",
            f"- baseline_head: {meta.baseline.get('head', '')}",
            f"- validated_copy: {self.artifacts_dir / 'validated-dispatch.toml'}",
            f"- updated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            (
                "- writer: dispatch supervisor (single writer; verified state"
                " flips only after green supervisor verify)"
            ),
            "",
            "| Cut | Phase | Agent | State | Scheduler | Supervisor evidence |",
            "|---|---|---|---:|---|---|",
        ]
        for cut in self.dispatch.cuts:
            state, note = self._states[cut.id]
            scheduler_state = str(
                self._receipt_store.cut(cut.id).get("state") or "queued"
            )
            lines.append(
                f"| {cut.id} | {cut.phase} | {cut.agent} | {state} | {scheduler_state} | {note} |"
            )
        with self._io_lock:
            self.tracker_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _mark_downstream_skipped(self, cut_id: str) -> None:
        """Mark every still-pending cut after ``cut_id`` as skipped (line broken upstream)."""
        past_failed_cut = False
        for cut in self.dispatch.cuts:
            if cut.id == cut_id:
                past_failed_cut = True
                continue
            if past_failed_cut and self._states[cut.id][0] == STATE_PENDING:
                self._set_state(cut.id, STATE_PENDING, "skipped: line broken upstream")

    def _build_result(self, baton: Baton, line_broken: bool) -> DispatchResult:
        """Assemble the final ``DispatchResult`` from the baton and current per-cut states."""
        verdicts = {state.cut_id: state for state in baton.states}
        return DispatchResult(
            line_broken=line_broken,
            baton=baton,
            states={cut_id: state for cut_id, (state, _) in self._states.items()},
            cuts=tuple(
                {
                    "id": cut.id,
                    "phase": cut.phase,
                    "agent": cut.agent,
                    "state": self._states[cut.id][0],
                    "scheduler_state": self._receipt_store.cut(cut.id).get(
                        "state", "queued"
                    ),
                    "note": self._states[cut.id][1],
                    "commit": verdicts[cut.id].commit if cut.id in verdicts else "",
                    "report": verdicts[cut.id].report if cut.id in verdicts else "",
                }
                for cut in self.dispatch.cuts
            ),
            artifacts={
                "tracker": str(self.tracker_path),
                "journal": str(self.journal_path),
                "handoff": str(self.handoff_path),
                "result": str(self.result_path),
                "receipts": str(self._receipt_store.path),
                "run_id": self.run_id,
            },
        )

    def _write_final_artifacts(self, result: DispatchResult) -> None:
        """Write dispatch-result.json and handoff.md, then journal the run's end summary."""
        self.result_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._write_handoff(result)
        self._journal(
            f"dispatch end: dou_index {result.baton.verified}/{result.baton.total}"
            f" line_broken={result.line_broken}"
        )

    def _write_handoff(self, result: DispatchResult) -> None:
        """Render handoff.md: per-cut state table, final-verdict evidence, next action."""
        baton = result.baton
        meta = self.dispatch.meta
        lines = [
            f"# dispatch handoff — {meta.name or 'unnamed'}",
            "",
            f"- repo: {meta.repo}",
            (
                f"- dou_index: {baton.verified}/{baton.total}"
                f" ({baton.ratio:.0%} supervisor-verified)"
            ),
            f"- line_broken: {result.line_broken}",
            f"- tracker: {self.tracker_path}",
            f"- journal: {self.journal_path}",
            "",
            "## Cut states",
            "",
            "| Cut | Phase | State | Commit | Report |",
            "|---|---|---:|---|---|",
        ]
        verdicts = {state.cut_id: state for state in baton.states}
        for cut in self.dispatch.cuts:
            state, _ = self._states[cut.id]
            recorded = verdicts.get(cut.id)
            commit = recorded.commit[:8] if recorded and recorded.commit else ""
            report = recorded.report if recorded else ""
            lines.append(f"| {cut.id} | {cut.phase} | {state} | {commit} | {report} |")
        lines += ["", "## Evidence", ""]
        last = baton.last
        seen_verdicts = []
        # Baton states carry summaries; full verifier evidence is journaled
        # per transition. Surface the final verdict's evidence here too.
        if last is not None:
            seen_verdicts.append(last)
        for verdict in seen_verdicts:
            for evidence in verdict.verifiers:
                status = "PASS" if evidence.ok else evidence.matcher_result.upper()
                lines.append(
                    f"- [{verdict.cut_id}] {status}: `{evidence.command}`"
                    f" exit={evidence.exit_code}"
                )
            for failure in verdict.failures:
                lines.append(f"- [{verdict.cut_id}] FAILURE: {failure}")
        lines += ["", "## Next suggested action", ""]
        lines.append(self._next_action(result))
        self.handoff_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _next_action(self, result: DispatchResult) -> str:
        """Compose the handoff's operator-facing recommended next step."""
        if result.line_broken:
            broken = [
                cut_id
                for cut_id, (state, _) in self._states.items()
                if state == STATE_FAILED
            ]
            return (
                f"Critical cut failed ({', '.join(broken) or 'unknown'});"
                " operator decision required before re-dispatch."
            )
        unverified = [
            cut_id
            for cut_id, (state, _) in self._states.items()
            if state != STATE_VERIFIED
        ]
        if not unverified:
            return (
                "All cuts supervisor-verified. STOP at the operator button:"
                " push/release stays a human action."
            )
        return f"Re-dispatch or inspect unverified cuts: {', '.join(unverified)}."


def _repo_identity_from_url(url: str) -> str:
    """Reduce a git remote URL to its `owner/repo` identity.

    Handles https/ssh scheme URLs and scp-like `git@host:owner/repo.git`.
    Empty input (no remote) reduces to "" and fails the qualification
    against any declared identity.
    """
    tail = url.strip()
    if not tail:
        return ""
    if "://" in tail:
        tail = tail.split("://", 1)[1]
        tail = tail.split("/", 1)[1] if "/" in tail else ""
    elif ":" in tail.split("/", 1)[0]:
        tail = tail.split(":", 1)[1]
    tail = tail.removesuffix(".git")
    return tail.strip("/")


def _normalize_digest(digest: str) -> str:
    """Normalize a digest string to lowercase, always prefixed with ``sha256:``."""
    cleaned = digest.strip().lower()
    cleaned = cleaned.removeprefix("sha256:")
    return f"sha256:{cleaned}"


def _path_in_scope(path: str, scopes: tuple[str, ...]) -> bool:
    """True when ``path`` equals or is nested under any of the given scope prefixes."""
    for scope in scopes:
        anchor = scope.rstrip("/")
        if not anchor:
            continue
        if path == anchor or path.startswith(anchor + "/"):
            return True
    return False


def run_dispatch(
    dispatch: Dispatch,
    *,
    launcher: CellLauncher | None = None,
    artifacts_dir: str | Path | None = None,
    source_dir: str | Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    run_id: str = "",
    manage_worktrees: bool | None = None,
    resume: bool = False,
) -> DispatchResult:
    """Construct a ``DispatchSupervisor`` for ``dispatch`` and run it to completion."""
    supervisor = DispatchSupervisor(
        dispatch,
        launcher=launcher,
        artifacts_dir=artifacts_dir,
        source_dir=source_dir,
        sleep=sleep,
        run_id=run_id,
        manage_worktrees=manage_worktrees,
        resume=resume,
    )
    return supervisor.run()


def cleanup_settled_run(dispatch: Dispatch, run_id: str) -> dict[str, str]:
    """Explicitly remove settled worker checkouts while retaining branches/evidence."""
    store = DispatchReceiptStore(run_id, dispatch.cuts, create=False)
    manager = WorktreeManager(dispatch.meta.repo)
    outcomes: dict[str, str] = {}
    for cut in dispatch.cuts:
        receipt = store.cut(cut.id)
        if receipt.get("state") != "settled":
            outcomes[cut.id] = "retained-active"
            continue
        if bool(receipt.get("integrator_exclusivity")):
            outcomes[cut.id] = "not-applicable"
            store.update(cut.id, cleanup_status="not-applicable")
            continue
        required = {
            "worktree_path",
            "branch",
            "baseline_sha",
            "target_path",
            "artifact_path",
        }
        if any(not receipt.get(field) for field in required):
            outcomes[cut.id] = "receipt-incomplete"
            continue
        geometry = WorktreeGeometry(
            org=manager.org,
            repo=manager.repo,
            day=manager.day,
            cut_id=cut.id,
            worktree_path=str(receipt["worktree_path"]),
            branch=str(receipt["branch"]),
            baseline_sha=str(receipt["baseline_sha"]),
            target_path=str(receipt["target_path"]),
            artifact_path=str(receipt["artifact_path"]),
            integrator_exclusive=False,
        )
        outcome = manager.cleanup(geometry, settled=True)
        outcomes[cut.id] = outcome
        store.update(cut.id, cleanup_status=outcome)
    return outcomes
