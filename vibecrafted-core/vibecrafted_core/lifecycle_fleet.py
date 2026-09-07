"""WRITE-stage fleet: durable lifecycle identities around dispatcher-owned cuts.

The lifecycle owns the parent-to-cut relation; the supplied dispatcher owns
worktree creation, provider launch, liveness, recovery and receipts.  Keeping
that split explicit prevents a stage worker from becoming a second scheduler.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .control_plane import control_plane_home
from .dispatch.worktrees import repo_identity
from .process_control import process_identity_receipt, validate_process_identity
from .runtime_paths import vibecrafted_home
from .workflows.model import WorkflowStage

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle at runtime
    from .dispatch.model import Dispatch
    from .dispatch.supervisor import CellLauncher

WRITE_FLEET_STAGE_WORKFLOWS = frozenset(
    {"implement", "workflow", "marbles", "polarize", "hydrate"}
)

# Default runtime contract: a stage worker must not spawn agent lines.
STAGE_WORKER_MAY_LAUNCH_AGENT_LINES = False

_CUT_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

SupervisorLaunch = Callable[["CutDispatchContract"], dict[str, Any]]

# Fleet-level seam: one call owns every cut of a stage, so a single scheduler
# keeps the declared cuts concurrent.  A per-cut ``SupervisorLaunch`` cannot
# express that — invoking a scheduler once per contract would serialize the
# fleet into N independent one-cut runs.
FleetLaunch = Callable[["WriteStageFleet"], list[dict[str, Any]]]


@dataclass(frozen=True)
class CutDispatchContract:
    """One recorded child the supervisor would launch for a plan cut."""

    cut_id: str
    child_run_id: str
    parent_run_id: str
    stage_id: str
    stage_workflow: str
    worktree_path: str
    branch: str
    org: str
    repo: str
    agent: str
    meta_path: str
    live_dispatch: bool = False
    spawned: bool = False

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WriteStageFleet:
    """Dispatch contract for one WRITE stage over N listed cuts."""

    parent_run_id: str
    stage_id: str
    stage_workflow: str
    cuts: tuple[str, ...]
    exception_granted: bool
    live_dispatch: bool
    children: tuple[CutDispatchContract, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "parent_run_id": self.parent_run_id,
            "stage_id": self.stage_id,
            "stage_workflow": self.stage_workflow,
            "cuts": list(self.cuts),
            "exception_granted": self.exception_granted,
            "live_dispatch": self.live_dispatch,
            "children": [child.to_payload() for child in self.children],
        }


def is_write_fleet_stage(stage: WorkflowStage) -> bool:
    """True for ship WRITE stages that dispatch one worker per cut."""
    return stage.phase == "write" and stage.workflow in WRITE_FLEET_STAGE_WORKFLOWS


def mission_cuts(mission_text: str) -> tuple[str, ...]:
    """Cuts declared in the mission YAML frontmatter.

    Accepted shapes (mirrors ``stage_agents`` parsing, not a YAML load):

        ---
        cuts: W0-a, W0-b, W1-a
        ---

        ---
        cuts:
          - W0-a
          - W0-b
        ---
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for token in _mission_cut_tokens(mission_text):
        cut_id = _split_cut_token(token)
        if not cut_id or cut_id in seen:
            continue
        seen.add(cut_id)
        ordered.append(cut_id)
    return tuple(ordered)


def mission_stage_cuts(mission_text: str, stage_id: str) -> tuple[str, ...]:
    """Return cuts explicitly bound to one WRITE stage.

    A lifecycle mission may declare several WRITE stages.  A top-level
    ``cuts:`` list has no stage owner, so using it for every stage replays the
    same implementation work.  New missions must bind cuts with frontmatter
    such as ``stage_cuts: implement=W0-a,W0-b`` (or its nested-map form).
    """
    from .stage_cast import _mission_frontmatter_map

    bindings = _mission_frontmatter_map(mission_text, "stage_cuts")
    raw = bindings.get(str(stage_id).strip(), "")
    if raw:
        return mission_cuts(f"---\ncuts: {raw}\n---\n")
    # Compatibility is deliberately one-way: the historical unqualified
    # ``cuts`` syntax belongs to the implementation stage, never to every
    # later WRITE stage.  Additional fleets must be explicitly declared.
    return mission_cuts(mission_text) if str(stage_id).strip() == "implement" else ()


def mission_cut_agents(mission_text: str) -> dict[str, str]:
    """Per-cut provider pins declared alongside the cut ids.

    The ``cuts:`` scan has always tolerated a ``<cut>: <provider>`` shape and
    thrown the provider half away.  Reading it is what lets one declared stage
    fleet span two providers::

        ---
        cuts: W0-a: codex, W0-b: claude, W0-c: codex
        ---

    Cuts listed without a pin simply fall back to the stage agent.
    """
    pinned: dict[str, str] = {}
    for token in _mission_cut_tokens(mission_text):
        cut_id, agent = _split_cut_pin(token)
        if cut_id and agent and cut_id not in pinned:
            pinned[cut_id] = agent
    return pinned


def mission_dispatch_plan(mission_text: str) -> str:
    """Path of an existing dispatch plan the mission hands to the stage fleet.

        ---
        dispatch_plan: plans/acceptance/three-cuts.toml
        ---

    Referencing a plan is the route that carries declared verifiers, so a cut
    can actually earn its verified state; a mission that only lists cut ids
    gets worktrees, providers and receipts, but nothing to verify against.
    """
    lines = str(mission_text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith("dispatch_plan:"):
            return stripped.split(":", 1)[1].strip().strip("'\"")
    return ""


def _mission_cut_tokens(mission_text: str) -> tuple[str, ...]:
    """Raw (unsplit) ``cuts:`` entries from the mission YAML frontmatter."""
    lines = str(mission_text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return ()
    tokens: list[str] = []
    in_block = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if not in_block:
            if not stripped.startswith("cuts:"):
                continue
            inline = stripped.split(":", 1)[1].strip().strip("[]")
            if inline:
                tokens.extend(inline.split(","))
                break
            in_block = True
            continue
        if not line.startswith((" ", "\t")):
            break
        if stripped.startswith("- "):
            tokens.append(stripped[2:])
            continue
        if stripped.startswith("-"):
            tokens.append(stripped[1:])
            continue
        break
    return tuple(tokens)


def _split_cut_pin(raw: str) -> tuple[str, str]:
    """Split one ``cuts:`` entry into its cut id and optional provider pin."""
    token = raw.strip().strip("'\"")
    if ":" in token and not token.startswith("cut/"):
        cut_id, agent = token.split(":", 1)
        return cut_id.strip().strip("'\""), agent.strip().strip("'\"")
    return token, ""


def _split_cut_token(raw: str) -> str:
    return _split_cut_pin(raw)[0]


def safe_cut_id(cut_id: str) -> str:
    """Path-safe cut id: letters, numbers, '.', '_' or '-'."""
    cleaned = _CUT_SAFE.sub("-", str(cut_id).strip()).strip(".-")
    return cleaned or "cut"


def cut_worktree_path(
    *,
    org: str,
    repo: str,
    run_id: str,
    cut_id: str,
    home: Path | None = None,
) -> Path:
    """``$VIBECRAFTED_HOME/worktrees/<org>/<repo>/<run_id>/<cut_id>``."""
    root = Path(home) if home is not None else vibecrafted_home()
    return (
        root
        / "worktrees"
        / safe_cut_id(org)
        / safe_cut_id(repo)
        / safe_cut_id(run_id)
        / safe_cut_id(cut_id)
    )


def stage_worker_may_launch_agent_lines(
    *,
    stage: WorkflowStage,
    cuts: Sequence[str],
) -> bool:
    """Exception to the default forbid: WRITE stages with listed cuts.

    Grants recording a per-cut dispatch contract. Does not grant live
    ``vc-dispatch``.
    """
    if STAGE_WORKER_MAY_LAUNCH_AGENT_LINES:
        return True
    return is_write_fleet_stage(stage) and bool(tuple(cuts))


def live_vc_dispatch_permitted() -> bool:
    """A stage worker may never invoke live dispatcher lines directly."""
    return False


def child_run_id(parent_run_id: str, stage_id: str, cut_id: str) -> str:
    """Stable control-plane id for one WRITE-stage cut child."""
    return f"{safe_cut_id(parent_run_id)}-{safe_cut_id(stage_id)}-{safe_cut_id(cut_id)}"


def record_only_supervisor(contract: CutDispatchContract) -> dict[str, Any]:
    """Explicit test/degraded supervisor: accept the recorded contract, do not spawn."""
    return {
        "accepted": True,
        "spawned": False,
        "live_dispatch": False,
        "cut_id": contract.cut_id,
        "run_id": contract.child_run_id,
        "worktree_path": contract.worktree_path,
        "meta_path": contract.meta_path,
    }


def stage_dispatch_run_id(parent_run_id: str, stage_id: str) -> str:
    """Deterministic dispatcher run id for one lifecycle stage fleet.

    Deriving it from the parent binds the dispatcher receipt ledger to the
    lifecycle run, and makes the ledger findable again after the lifecycle
    process is gone — which is what turns a crash into a resume instead of a
    second execution.
    """
    return f"{safe_cut_id(parent_run_id)}-{safe_cut_id(stage_id)}-fleet"


def stage_dispatch_home(dispatch_run_id: str) -> Path:
    """Control-plane directory owning one stage fleet's dispatch receipts."""
    return control_plane_home() / "dispatches" / dispatch_run_id


def build_stage_dispatch(
    fleet: WriteStageFleet,
    *,
    repo_root: str | Path,
    cut_agents: dict[str, str] | None = None,
    mission_text: str = "",
    stage_model: str = "",
    plan_path: str | Path = "",
    await_config: dict[str, Any] | None = None,
) -> Dispatch:
    """The typed ``Dispatch`` plan for one WRITE stage fleet.

    When the mission references an existing dispatch plan the plan is loaded
    through the dispatcher's own parser and returned untouched — the lifecycle
    never re-parses cut syntax.  Otherwise the recorded contracts are lifted
    into the same typed model, one ``Cut`` per lifecycle cut, so a single
    scheduler owns them all.
    """
    from .dispatch.model import Common, Cut, Dispatch, Meta, Phase, Policy
    from .dispatch.schema import load_dispatch

    if plan_path:
        return load_dispatch(plan_path)

    pins = dict(cut_agents or {})
    built: list[Cut] = []
    for contract in fleet.children:
        agent = pins.get(contract.cut_id) or contract.agent
        built.append(
            Cut(
                id=contract.cut_id,
                phase=fleet.stage_workflow,
                agent=agent,
                workflow=fleet.stage_workflow,
                resolved_workflow=fleet.stage_workflow,
                mode="write",
                # The stage model only travels with the stage's own provider;
                # a second provider gets its runtime default rather than a
                # model name it does not own.
                model=stage_model if agent == contract.agent else "",
                brief=(
                    f"Cut `{contract.cut_id}` of lifecycle run "
                    f"`{contract.parent_run_id}`, stage `{contract.stage_id}` "
                    f"({contract.stage_workflow}).\n"
                    f"Lifecycle child run id: `{contract.child_run_id}`."
                ),
            )
        )
    cuts = tuple(built)
    total = len(cuts)
    return Dispatch(
        schema="vibecrafted.dispatch.v1",
        meta=Meta(
            name=f"{fleet.stage_workflow}-fleet-{fleet.stage_id}",
            repo=str(Path(repo_root).expanduser().resolve()),
            description=(f"WRITE stage fleet for lifecycle run {fleet.parent_run_id}"),
        ),
        # Every declared cut is disjoint, so the whole fleet is allowed to be
        # in flight at once; the scheduler pool is what makes that real.
        policy=Policy(
            concurrency=max(1, total),
            allow_concurrency=total > 1,
            await_config=dict(await_config or {}),
        ),
        common=Common(text=str(mission_text or "")),
        phases=(Phase(title=fleet.stage_workflow),),
        cuts=cuts,
    )


def dispatcher_fleet_launch(
    *,
    repo_root: str | Path,
    mission_text: str = "",
    stage_model: str = "",
    plan_path: str | Path = "",
    cell_launcher: CellLauncher | None = None,
    await_config: dict[str, Any] | None = None,
    wait: bool = False,
) -> FleetLaunch:
    """The production seam: run the whole stage fleet on the existing dispatcher.

    One ``run_dispatch`` owns the declared cuts, so worktree creation, provider
    launch, concurrency, liveness, recovery and receipts all stay with their
    existing owner.  The receipt ledger is created *before* the scheduler
    starts, so the lifecycle can never hold a cut it cannot name again.

    ``wait=False`` (the lifecycle default) keeps the stage record observable
    while the fleet is in flight; the launched workers are detached runs, so
    losing this process loses the observer, not the work.
    """

    def launch(fleet: WriteStageFleet) -> list[dict[str, Any]]:
        from .dispatch.receipts import DispatchReceiptStore
        from .dispatch.supervisor import run_dispatch

        declared = str(plan_path or mission_dispatch_plan(mission_text))
        resolved_plan = ""
        if declared:
            candidate = Path(declared).expanduser()
            if not candidate.is_absolute():
                candidate = Path(repo_root).expanduser() / candidate
            if not candidate.is_file():
                raise RuntimeError(f"declared dispatch plan not found: {candidate}")
            resolved_plan = str(candidate)
        dispatch = build_stage_dispatch(
            fleet,
            repo_root=repo_root,
            cut_agents=mission_cut_agents(mission_text),
            mission_text=mission_text,
            stage_model=stage_model,
            plan_path=resolved_plan,
            await_config=await_config,
        )
        planned = {cut.id for cut in dispatch.cuts}
        missing = [c.cut_id for c in fleet.children if c.cut_id not in planned]
        if missing:
            raise RuntimeError(
                "declared dispatch plan does not cover lifecycle cut(s) "
                f"{', '.join(missing)}; plan {resolved_plan or '<derived>'} "
                f"declares {', '.join(sorted(planned)) or '<none>'}"
            )
        run_id = stage_dispatch_run_id(fleet.parent_run_id, fleet.stage_id)
        home = stage_dispatch_home(run_id)
        # An existing ledger means a previous attempt already owned these cuts:
        # resume it instead of scheduling the same work twice.
        resume = (home / "receipts.json").exists()
        store = DispatchReceiptStore(
            run_id,
            dispatch.cuts,
            concurrency=dispatch.policy.concurrency,
            repo_root=str(Path(repo_root).expanduser().resolve()),
            create=True,
        )
        artifacts_dir = home / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        def run() -> None:
            run_dispatch(
                dispatch,
                launcher=cell_launcher,
                artifacts_dir=artifacts_dir,
                run_id=run_id,
                # Worktree geometry belongs to the dispatcher even when the
                # provider transport is bounded for a test.
                manage_worktrees=True,
                resume=resume,
            )

        if wait:
            run()
        elif cell_launcher is not None:
            # A bounded transport was injected into this interpreter; there is
            # nothing to hand to another process, and the caller owns the
            # scheduler by construction.
            _start_background_dispatch(run_id, run)
            store.update_metadata(
                scheduler_owner_mode="in-process",
                scheduler_detached=False,
                scheduler_owner_pid=os.getpid(),
            )
        elif resolved_plan:
            _start_detached_dispatch(
                run_id, store, plan_path=resolved_plan, repo_root=repo_root
            )
        else:
            # A derived plan lives only in this interpreter's memory: no file
            # names these cuts, so no fresh process could re-own them.  Run it
            # here and record that, rather than detaching an owner nobody can
            # recover.
            _start_background_dispatch(run_id, run)
            store.update_metadata(
                scheduler_owner_mode="in-process",
                scheduler_detached=False,
                scheduler_owner_pid=os.getpid(),
                scheduler_undetachable_reason=(
                    "no durable dispatch plan declared; declare `dispatch_plan:` "
                    "in the mission to give this fleet a detachable owner"
                ),
            )

        by_cut = {cut.id: cut for cut in dispatch.cuts}
        return [
            _project_receipt(
                contract,
                dispatch_run_id=run_id,
                receipts_path=store.path,
                receipt=store.cut(contract.cut_id),
                cut=by_cut.get(contract.cut_id),
                resumed=resume,
                plan_path=resolved_plan,
            )
            for contract in fleet.children
        ]

    return launch


def _project_receipt(
    contract: CutDispatchContract,
    *,
    dispatch_run_id: str,
    receipts_path: Path,
    receipt: dict[str, Any],
    cut: Any,
    resumed: bool,
    plan_path: str = "",
) -> dict[str, Any]:
    """Project one authoritative dispatch receipt into a lifecycle launch result."""
    return {
        "accepted": True,
        "spawned": True,
        "live_dispatch": True,
        "cut_id": contract.cut_id,
        "run_id": contract.child_run_id,
        "dispatcher_run_id": dispatch_run_id,
        "provider_run_id": str(receipt.get("provider_run_id") or ""),
        # Geometry is read back from the dispatcher, never asserted here.
        "worktree_path": str(receipt.get("worktree_path") or ""),
        "branch": str(receipt.get("branch") or ""),
        "receipts_path": str(receipts_path),
        # The typed plan on disk; without it the dispatcher's own resume verb
        # has nothing to name.
        "plan_path": str(plan_path or ""),
        "receipt_state": str(receipt.get("state") or "queued"),
        "agent": getattr(cut, "agent", contract.agent),
        "model": getattr(cut, "model", ""),
        "resumed": resumed,
    }


_ACTIVE_FLEET_DISPATCHES: dict[str, threading.Thread] = {}
# Detached owners this interpreter spawned.  A reopened observer has an empty
# map and falls back to the ledger's identity receipt, which is the point.
_ACTIVE_FLEET_OWNERS: dict[str, subprocess.Popen[bytes]] = {}
_FLEET_DISPATCH_ERRORS: dict[str, str] = {}
_FLEET_DISPATCH_LOCK = threading.Lock()


def _start_background_dispatch(run_id: str, run: Callable[[], None]) -> None:
    """Run one dispatch off the lifecycle thread, keeping the handle reachable."""

    def target() -> None:
        try:
            run()
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
            message = f"{type(exc).__name__}: {exc}"
            with _FLEET_DISPATCH_LOCK:
                _FLEET_DISPATCH_ERRORS[run_id] = message
            # In-process memory dies with the observer.  A scheduler that
            # failed before its children started would otherwise leave every
            # cut reading `queued` forever, with the reason gone.
            record_stage_dispatch_failure(run_id, message)

    thread = threading.Thread(target=target, name=f"lifecycle-fleet-{run_id}")
    with _FLEET_DISPATCH_LOCK:
        _ACTIVE_FLEET_DISPATCHES[run_id] = thread
    thread.start()


def scheduler_owner_command(plan_path: str | Path, dispatch_run_id: str) -> list[str]:
    """The argv of the existing dispatcher entrypoint that owns one stage fleet.

    ``vibecrafted dispatch <plan> --resume <run_id>`` is the dispatcher's own
    public verb, and this is that verb's module.  Using it — rather than a
    forked closure — means the owner started here is the same owner the
    recorded recovery command re-creates.
    """
    return [
        sys.executable,
        "-m",
        "vibecrafted_core.dispatch.cli",
        str(plan_path),
        "--resume",
        str(dispatch_run_id),
    ]


def _start_detached_dispatch(
    run_id: str,
    store: Any,
    *,
    plan_path: str | Path,
    repo_root: str | Path,
) -> dict[str, Any]:
    """Hand this fleet to a process that outlives the caller, and prove whose it is.

    The owner is a fresh interpreter in its own session, so closing the
    terminal or the App removes the observer, not the scheduler and not the
    settlement work it still owes.  Its full OS identity — pid, pgid, start
    token, command hash — is captured before anything can act on it: a bare
    PID is a name the kernel reuses, and signalling a stranger because a
    number came back around is exactly what this record prevents.
    """
    plan = str(plan_path or "").strip()
    if not plan or not Path(plan).expanduser().is_file():
        raise RuntimeError(
            "detached scheduler owner needs a durable typed plan; "
            f"{plan or '<none>'} is not a file"
        )
    command = scheduler_owner_command(plan, run_id)
    log_path = store.root / "scheduler.log"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    env = dict(os.environ)
    # The owner has to run the same code that wrote this ledger.  Without
    # this, an interpreter that resolves an installed release would take over
    # receipts a different version created — a skew nobody can see from the
    # outside.  Scoped to this child; never exported to the host shell.
    package_root = str(Path(__file__).resolve().parents[1])
    inherited = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        package_root if not inherited else package_root + os.pathsep + inherited
    )
    with log_path.open("ab", buffering=0) as log:
        proc = subprocess.Popen(
            command,
            cwd=str(Path(repo_root).expanduser().resolve()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            # The whole point: a new session has no controlling terminal, so
            # the SIGHUP that closes the operator's window never reaches it.
            start_new_session=True,
        )
    identity = process_identity_receipt(proc.pid, run_id=run_id)
    receipt: dict[str, Any] = {
        "scheduler_owner_mode": "detached-subprocess",
        "scheduler_detached": True,
        "scheduler_owner_pid": proc.pid,
        "scheduler_owner_identity": identity or {},
        "scheduler_owner_command": list(command),
        "scheduler_owner_plan": plan,
        "scheduler_owner_log": str(log_path),
        "scheduler_owner_started_at": started_at,
        "scheduler_stop_requested": False,
    }
    if identity is None:
        # The owner was gone before we could describe it.  Say so, rather than
        # persisting a pid that a later stop would be entitled to signal.
        receipt["scheduler_owner_identity_error"] = "process_identity_unavailable"
    store.update_metadata(**receipt)
    with _FLEET_DISPATCH_LOCK:
        _ACTIVE_FLEET_OWNERS[run_id] = proc
    threading.Thread(
        target=_reap_detached_owner,
        args=(run_id, proc, log_path),
        name=f"lifecycle-fleet-owner-{run_id}",
        daemon=True,
    ).start()
    return receipt


def _reap_detached_owner(run_id: str, proc: Any, log_path: Path) -> None:
    """Reap the owner and record an exit that left cuts unstarted.

    An unreaped child stays a zombie, and ``kill(pid, 0)`` then answers
    "alive" for a process that already finished — an observer could not tell
    settlement from stale OS state.  A non-zero exit is only a *scheduler*
    failure when cuts never left the queue; a run that finished with failed
    cuts already carries its own verdicts.
    """
    try:
        code = proc.wait()
    except OSError:
        return
    try:
        store = _receipt_store_for(run_id)
        store.update_metadata(
            scheduler_owner_exit_code=code,
            scheduler_owner_finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
        if code == 0:
            return
        payload = store.read()
        ledger = payload.get("cuts") if isinstance(payload.get("cuts"), dict) else {}
        unstarted = sorted(
            cut_id
            for cut_id, entry in ledger.items()
            if isinstance(entry, dict) and entry.get("state") == "queued"
        )
        if unstarted:
            record_stage_dispatch_failure(
                run_id,
                f"scheduler owner exited {code} leaving {', '.join(unstarted)} "
                f"unstarted; see {log_path}",
            )
    except Exception:  # noqa: BLE001 - a note about a failure is never a second one
        return


def _receipt_store_for(dispatch_run_id: str) -> Any:
    """Open one dispatch run's existing ledger; never create a new one."""
    from .dispatch.receipts import DispatchReceiptStore

    return DispatchReceiptStore(dispatch_run_id, (), create=False)


def scheduler_owner_identity(dispatch_run_id: str) -> tuple[dict[str, Any], int]:
    """The persisted owner identity receipt and pid for one dispatch run."""
    try:
        payload = _receipt_store_for(dispatch_run_id).read()
    except Exception:  # noqa: BLE001 - a missing ledger owns nothing
        return {}, 0
    receipt = payload.get("scheduler_owner_identity")
    return (
        dict(receipt) if isinstance(receipt, dict) else {},
        int(payload.get("scheduler_owner_pid") or 0),
    )


def scheduler_owner_alive(dispatch_run_id: str) -> tuple[bool, str]:
    """Whether the recorded owner is still the process that was recorded.

    Liveness here is an identity question, not a PID question.  "Something
    holds that number" is precisely how an unrelated program gets adopted as
    our scheduler — and then signalled as one.
    """
    receipt, pid = scheduler_owner_identity(dispatch_run_id)
    if not receipt or pid <= 0:
        return False, "process_identity_unavailable"
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False, "process_identity_gone"
    ok, reason, _identity = validate_process_identity(
        receipt,
        expected_pid=pid,
        expected_pgid=None,
        expected_run_id=dispatch_run_id,
    )
    return ok, reason


def join_stage_dispatch(dispatch_run_id: str, timeout: float | None = None) -> bool:
    """Wait for a stage dispatch owner; True when it is no longer running."""
    with _FLEET_DISPATCH_LOCK:
        thread = _ACTIVE_FLEET_DISPATCHES.get(dispatch_run_id)
        owner = _ACTIVE_FLEET_OWNERS.get(dispatch_run_id)
    if thread is not None:
        thread.join(timeout)
        return not thread.is_alive()
    if owner is not None:
        try:
            owner.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return True
    return _join_detached_owner(dispatch_run_id, timeout)


def _join_detached_owner(dispatch_run_id: str, timeout: float | None) -> bool:
    """Await an owner this interpreter never spawned, by its recorded identity.

    A detached owner belongs to another process tree, so it can never appear
    in this interpreter's handle map.  Its durable identity receipt is the
    same reconnectable truth a reopened observer reads — and the reason a
    recycled PID does not read here as "still running".
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        alive, _reason = scheduler_owner_alive(dispatch_run_id)
        if not alive:
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def stage_dispatch_error(dispatch_run_id: str) -> str:
    """The recorded failure of a stage dispatch, from memory or the ledger.

    The in-process map answers the launching process; the receipt ledger
    answers everyone who comes after it.  A reopened observer must be able to
    see why a fleet never moved.
    """
    with _FLEET_DISPATCH_LOCK:
        live = _FLEET_DISPATCH_ERRORS.get(dispatch_run_id, "")
    if live:
        return live
    path = stage_dispatch_home(dispatch_run_id) / "receipts.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return (
        str(payload.get("scheduler_error") or "") if isinstance(payload, dict) else ""
    )


def record_stage_dispatch_failure(dispatch_run_id: str, message: str) -> None:
    """Persist a scheduler failure into the existing dispatch receipt ledger.

    Reuses the dispatcher's own ledger file rather than opening a second
    error store: the ledger is created before the scheduler starts, so the
    failure lands next to the cut states it explains.
    """
    try:
        from .dispatch.receipts import DispatchReceiptStore

        store = DispatchReceiptStore(dispatch_run_id, (), create=False)
        store.update_metadata(
            scheduler_error=str(message),
            scheduler_error_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
    except Exception:  # noqa: BLE001 - original scheduler failure is primary
        # No ledger means the dispatch never reached the point of owning cuts;
        # the caller still holds the exception.
        return


def request_stage_dispatch_stop(
    dispatch_run_id: str, *, signal_owner: bool = False
) -> dict[str, Any]:
    """Fence future launches, and signal an owner only when it proves itself.

    The fence is the stop: it is written under the same ledger lock the
    scheduler uses to admit a launch, so once this returns ``accepted`` no
    queued cut can be admitted.  Signalling is separate and off by default —
    terminating the owner would abandon the settlement work the operator's
    already-running cuts still need, and killing on a bare PID is how an
    unrelated process gets a SIGTERM meant for a scheduler that died hours
    ago.  When a caller does ask for a signal, the recorded identity must
    re-capture exactly; anything else is reported, never signalled.
    """
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        store = _receipt_store_for(dispatch_run_id)
        store.update_metadata(
            scheduler_stop_requested=True,
            scheduler_stop_requested_at=stamp,
        )
        payload = store.read()
    except Exception as exc:  # noqa: BLE001 - interrupt must report refusal honestly
        return {
            "accepted": False,
            "fenced": False,
            "reason": str(exc),
            "owner_pid": 0,
            "owner_mode": "",
            "owner_identity": "process_identity_unavailable",
            "owner_signalled": False,
        }
    owner_pid = int(payload.get("scheduler_owner_pid") or 0)
    receipt = payload.get("scheduler_owner_identity")
    result: dict[str, Any] = {
        "accepted": True,
        "fenced": True,
        "reason": "future launches fenced",
        "owner_pid": owner_pid,
        "owner_mode": str(payload.get("scheduler_owner_mode") or ""),
        "owner_identity": "not_probed",
        "owner_signalled": False,
    }
    if not signal_owner:
        return result

    ok, reason, identity = validate_process_identity(
        receipt if isinstance(receipt, dict) else None,
        expected_pid=owner_pid,
        expected_pgid=None,
        expected_run_id=dispatch_run_id,
    )
    result["owner_identity"] = reason
    if not ok or identity is None:
        # Stale, missing or mismatched identity: the recorded owner is gone.
        # Whatever holds that PID now is a stranger, and the fence already
        # did the part of the stop that is actually ours to do.
        result["reason"] = f"fenced; owner not signalled ({reason})"
        _record_stop_probe(store, result)
        return result
    if identity.pgid != identity.pid:
        # A detached owner leads its own session.  A group that is not its own
        # is somebody else's group, and SIGTERM there is a stray blast.
        result["owner_identity"] = "process_group_not_owned"
        result["reason"] = "fenced; owner is not its own session leader"
        _record_stop_probe(store, result)
        return result
    try:
        os.killpg(identity.pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        result["owner_signal_error"] = f"{type(exc).__name__}: {exc}"
        result["reason"] = f"fenced; owner signal refused ({type(exc).__name__})"
        _record_stop_probe(store, result)
        return result
    result["owner_signalled"] = True
    result["owner_pgid"] = identity.pgid
    result["reason"] = "future launches fenced; owner group signalled"
    _record_stop_probe(store, result)
    return result


def _record_stop_probe(store: Any, result: dict[str, Any]) -> None:
    """Persist what the stop actually did, so a fresh observer reads the truth."""
    try:
        store.update_metadata(
            scheduler_stop_owner_identity=str(result.get("owner_identity") or ""),
            scheduler_stop_signalled=bool(result.get("owner_signalled")),
            scheduler_stop_signal_error=str(result.get("owner_signal_error") or ""),
        )
    except Exception:  # noqa: BLE001 - the fence itself is already durable
        return


def stage_fleet_receipts(parent_run_id: str, stage_id: str) -> dict[str, Any]:
    """Authoritative dispatch receipts for one lifecycle stage fleet.

    This is the read side of the bridge: lifecycle child records carry the
    binding (parent, cut, dispatcher run), and the live truth about worktree,
    provider run and scheduler state is read back from the dispatcher.
    """
    run_id = stage_dispatch_run_id(parent_run_id, stage_id)
    path = stage_dispatch_home(run_id) / "receipts.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


# The obligation vocabulary IS the dispatcher's ``SCHEDULER_STATES``, read
# back from its ledger.  The lifecycle classifies those states; it never keeps
# a second copy of them.
FLEET_STATES_COMPLETE = frozenset({"settled"})
FLEET_STATES_ACTIVE = frozenset(
    {"queued", "launching", "active", "reported", "integrating", "verified"}
)
FLEET_STATES_FAILED = frozenset({"failed", "stopped"})

# Worst obligation wins: a fleet is only "complete" when every declared cut is.
_VERDICT_RANK = ("complete", "active", "unknown", "missing", "failed")

# Fields copied verbatim out of the ledger so a reopened observer recovers the
# same cut, provider, worktree and attempt it had before the owner exited.
_RECEIPT_IDENTITY_FIELDS = (
    "provider_run_id",
    "pid",
    "worktree_path",
    "branch",
    "baseline_sha",
    "target_path",
    "report_path",
    "meta_path",
    "delivered_commit_sha",
    "integrated_sha",
    "scheduler_slot",
    "attempt",
    "acceptance",
    "updated_at",
)


def _classify_obligation(
    state: str, *, acceptance: str = "", scheduler_failed: bool
) -> str:
    """Map one dispatcher scheduler state onto a lifecycle obligation."""
    if state in FLEET_STATES_FAILED:
        return "failed"
    if state in FLEET_STATES_COMPLETE:
        # Dispatcher settlement only says scheduling has stopped.  Lifecycle
        # admission additionally requires the dispatcher receipt's actual
        # verifier result; otherwise a settled-but-unknown cut becomes false
        # completion.
        return "complete" if acceptance == "verified" else "unknown"
    if state in FLEET_STATES_ACTIVE:
        # A cut still sitting in the scheduler's queue is not evidence that a
        # provider was ever spawned.  When the scheduler itself died, "queued"
        # is a failure that never started, not work in flight.
        if scheduler_failed and state == "queued":
            return "failed"
        return "active"
    return "unknown"


def fleet_obligations(
    parent_run_id: str,
    stage_id: str,
    *,
    declared_cuts: Sequence[str] = (),
    plan_path: str = "",
) -> dict[str, Any]:
    """Project the authoritative dispatch receipts into lifecycle obligations.

    This is the whole read side of the control boundary: ``ship status``,
    ``await`` and ``approve`` all decide from this one projection, so they
    cannot disagree about whether a fleet still owes work.
    """
    dispatch_run_id = stage_dispatch_run_id(parent_run_id, stage_id)
    payload = stage_fleet_receipts(parent_run_id, stage_id)
    scheduler_error = stage_dispatch_error(dispatch_run_id)
    ledger = payload.get("cuts") if isinstance(payload.get("cuts"), dict) else {}
    declared = tuple(str(cut) for cut in declared_cuts if str(cut)) or tuple(ledger)

    cuts: list[dict[str, Any]] = []
    for cut_id in declared:
        entry = ledger.get(cut_id)
        if not isinstance(entry, dict):
            # Declared by the lifecycle, absent from the dispatcher's ledger:
            # nobody can say what happened to it.
            cuts.append({"cut_id": cut_id, "state": "", "obligation": "missing"})
            continue
        state = str(entry.get("state") or "")
        acceptance = str(entry.get("acceptance") or "")
        record: dict[str, Any] = {
            "cut_id": cut_id,
            "state": state,
            "obligation": _classify_obligation(
                state, acceptance=acceptance, scheduler_failed=bool(scheduler_error)
            ),
        }
        for field in _RECEIPT_IDENTITY_FIELDS:
            if field in entry:
                record[field] = entry[field]
        cuts.append(record)

    counts: dict[str, int] = {}
    for record in cuts:
        counts[record["obligation"]] = counts.get(record["obligation"], 0) + 1
    if not cuts:
        verdict = "none"
    else:
        verdict = max(
            (record["obligation"] for record in cuts),
            key=lambda obligation: (
                _VERDICT_RANK.index(obligation)
                if obligation in _VERDICT_RANK
                else len(_VERDICT_RANK)
            ),
        )
    blocking = [
        f"{record['cut_id']}={record['obligation']}({record['state'] or 'no-receipt'})"
        for record in cuts
        if record["obligation"] != "complete"
    ]
    return {
        "present": bool(cuts),
        "dispatch_run_id": dispatch_run_id,
        "receipts_path": str(stage_dispatch_home(dispatch_run_id) / "receipts.json"),
        "plan_path": str(plan_path or ""),
        "verdict": verdict,
        "complete": verdict == "complete",
        "counts": counts,
        "cuts": cuts,
        "blocking": blocking,
        "scheduler_error": scheduler_error,
        "scheduler_owner_pid": int(payload.get("scheduler_owner_pid") or 0),
        "scheduler_owner_mode": str(payload.get("scheduler_owner_mode") or ""),
        "scheduler_detached": bool(payload.get("scheduler_detached")),
        "scheduler_stop_requested": bool(payload.get("scheduler_stop_requested")),
        "recovery_command": fleet_recovery_command(dispatch_run_id, plan_path),
    }


def fleet_recovery_command(dispatch_run_id: str, plan_path: str = "") -> str:
    """The existing public command that re-owns this fleet, or why there is none.

    ``vibecrafted dispatch <plan> --resume <run_id>`` is the dispatcher's own
    recovery verb: settled cuts are restored from the ledger, live cuts are
    re-owned, and only failed cuts run again.  It needs the typed plan on
    disk, which is exactly what a mission ``dispatch_plan:`` declares.
    """
    plan = str(plan_path or "").strip()
    if not plan:
        return ""
    return f"vibecrafted dispatch {shlex.quote(plan)} --resume {shlex.quote(dispatch_run_id)}"


def stage_fleet_progress(
    state: dict[str, Any], *, stage_id: str = ""
) -> dict[str, Any]:
    """Fleet obligations for a lifecycle run's current (or named) WRITE stage."""
    stages = [dict(item) for item in (state.get("stages") or [])]
    candidates: list[dict[str, Any]] = []
    for stage in reversed(stages):
        if stage_id and str(stage.get("id") or "") != stage_id:
            continue
        if dict(stage.get("fleet") or {}).get("children") is not None:
            candidates.append(stage)
        if stage_id:
            break
    if not candidates:
        return {
            "present": False,
            "verdict": "none",
            "complete": True,
            "cuts": [],
            "blocking": [],
            "counts": {},
            "scheduler_error": "",
            "dispatch_run_id": "",
            "receipts_path": "",
            "plan_path": "",
            "recovery_command": "",
        }
    # A later empty fleet must not mask an earlier open fleet.  Return the
    # newest blocking projection; only if all are complete may the newest be
    # used as the status surface.
    projections = []
    for target in candidates:
        fleet = dict(target.get("fleet") or {})
        projections.append(
            fleet_obligations(
                str(fleet.get("parent_run_id") or state.get("run_id") or ""),
                str(fleet.get("stage_id") or target.get("id") or ""),
                declared_cuts=[str(cut) for cut in (fleet.get("cuts") or [])],
                plan_path=str(fleet.get("plan_path") or ""),
            )
        )
    return next(
        (
            item
            for item in projections
            if item.get("present") and not item.get("complete")
        ),
        projections[0],
    )


def record_write_stage_fleet(
    *,
    stage: WorkflowStage,
    cuts: Sequence[str],
    parent_run_id: str,
    repo_root: str | Path,
    agent: str,
    org: str = "",
    repo: str = "",
) -> WriteStageFleet:
    """Persist ≥N control-plane records (one per cut) when the exception applies.

    Does not create git worktrees and does not spawn ``vc-dispatch``.
    """
    listed = tuple(cut_id for cut_id in (str(item).strip() for item in cuts) if cut_id)
    granted = stage_worker_may_launch_agent_lines(stage=stage, cuts=listed)
    if not granted:
        return WriteStageFleet(
            parent_run_id=parent_run_id,
            stage_id=stage.id,
            stage_workflow=stage.workflow,
            cuts=(),
            exception_granted=False,
            live_dispatch=False,
            children=(),
        )

    resolved_org, resolved_repo = (
        (org, repo) if org and repo else repo_identity(repo_root)
    )
    children: list[CutDispatchContract] = []
    for cut_id in listed:
        safe = safe_cut_id(cut_id)
        child_id = child_run_id(parent_run_id, stage.id, safe)
        worktree = cut_worktree_path(
            org=resolved_org,
            repo=resolved_repo,
            run_id=parent_run_id,
            cut_id=safe,
        )
        meta_path = _write_child_record(
            child_run_id=child_id,
            parent_run_id=parent_run_id,
            cut_id=safe,
            stage=stage,
            agent=agent,
            worktree_path=str(worktree),
            branch=f"cut/{safe}",
            org=resolved_org,
            repo=resolved_repo,
        )
        children.append(
            CutDispatchContract(
                cut_id=safe,
                child_run_id=child_id,
                parent_run_id=parent_run_id,
                stage_id=stage.id,
                stage_workflow=stage.workflow,
                worktree_path=str(worktree),
                branch=f"cut/{safe}",
                org=resolved_org,
                repo=resolved_repo,
                agent=agent,
                meta_path=str(meta_path),
            )
        )
    return WriteStageFleet(
        parent_run_id=parent_run_id,
        stage_id=stage.id,
        stage_workflow=stage.workflow,
        cuts=tuple(child.cut_id for child in children),
        exception_granted=True,
        live_dispatch=False,
        children=tuple(children),
    )


def dispatch_recorded_children(
    fleet: WriteStageFleet,
    *,
    supervisor: SupervisorLaunch | None = None,
    fleet_launch: FleetLaunch | None = None,
    resume: bool = False,
) -> list[dict[str, Any]]:
    """Invoke the single supplied dispatcher after durable identity registration.

    Exactly one seam must be named.  ``fleet_launch`` is the production shape:
    one call owns every cut, so the declared fleet stays concurrent under one
    scheduler.  ``supervisor`` is the per-cut degraded/test seam and cannot
    schedule a fleet — it is deliberately not a fallback, because silently
    degrading to a record-only supervisor is what previously let a "dispatched"
    stage mean nothing at all.

    A dispatcher result is projected verbatim enough to retain the real provider
    run identity.  Relaunching an already-live child is refused; ``resume=True``
    is the explicit recovery verb that lets the dispatcher re-own its own cuts.
    """
    if not fleet.children:
        return []
    if supervisor is not None and fleet_launch is not None:
        raise RuntimeError(
            "ambiguous lifecycle fleet seam: name either supervisor or fleet_launch"
        )
    if supervisor is None and fleet_launch is None:
        raise RuntimeError(
            "no lifecycle fleet dispatcher: stage "
            f"{fleet.stage_id!r} of run {fleet.parent_run_id!r} declared "
            f"{len(fleet.children)} cut(s) but no fleet_launch was supplied; "
            "the dispatch boundary owns the launch "
            "(vibecrafted_core.lifecycle_fleet.dispatcher_fleet_launch). "
            "Pass supervisor=record_only_supervisor to record without dispatching."
        )
    for contract in fleet.children:
        prior = _load_child_record(Path(contract.meta_path))
        if bool(prior.get("spawned")) and not resume:
            raise RuntimeError(
                f"refusing duplicate lifecycle cut launch: {contract.child_run_id}"
            )
    if fleet_launch is not None:
        raw = list(fleet_launch(fleet))
        by_cut = {str(item.get("cut_id") or ""): item for item in raw}
        results = [
            dict(by_cut.get(contract.cut_id) or {}) for contract in fleet.children
        ]
    else:
        assert supervisor is not None
        results = [dict(supervisor(contract)) for contract in fleet.children]

    launched: list[dict[str, Any]] = []
    for contract, result in zip(fleet.children, results, strict=True):
        result.setdefault("cut_id", contract.cut_id)
        result.setdefault("run_id", contract.child_run_id)
        result.setdefault("spawned", False)
        result.setdefault("live_dispatch", bool(result["spawned"]))
        # The dispatcher owns worktree geometry; fall back to the recorded
        # placeholder only while it has not resolved one yet.
        if not str(result.get("worktree_path") or ""):
            result["worktree_path"] = contract.worktree_path
        if result["spawned"] and not result["live_dispatch"]:
            raise RuntimeError(
                f"dispatcher contradicted live launch for lifecycle cut: {contract.child_run_id}"
            )
        _record_dispatch_result(contract, result)
        launched.append(result)
    return launched


def load_cut_records(parent_run_id: str) -> list[dict[str, Any]]:
    """Control-plane child metas recorded for ``parent_run_id``."""
    root = control_plane_home() / "runtime_runs"
    if not root.is_dir():
        return []
    parent = str(parent_run_id or "").strip()
    records: list[dict[str, Any]] = []
    for meta in sorted(root.glob("*/meta.json")):
        try:
            payload = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("parent_run_id") or "") != parent:
            continue
        if not str(payload.get("cut_id") or "").strip():
            continue
        records.append(payload)
    return records


def _write_child_record(
    *,
    child_run_id: str,
    parent_run_id: str,
    cut_id: str,
    stage: WorkflowStage,
    agent: str,
    worktree_path: str,
    branch: str,
    org: str,
    repo: str,
) -> Path:
    run_dir = control_plane_home() / "runtime_runs" / child_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_path = run_dir / "meta.json"
    prior = _load_child_record(meta_path)
    if prior:
        expected = {
            "run_id": child_run_id,
            "parent_run_id": parent_run_id,
            "cut_id": cut_id,
            "stage_id": stage.id,
        }
        if any(str(prior.get(key) or "") != value for key, value in expected.items()):
            raise RuntimeError(f"conflicting lifecycle cut record: {child_run_id}")
        return meta_path
    payload = {
        "schema": "vibecrafted.lifecycle_fleet.v1",
        "run_id": child_run_id,
        "parent_run_id": parent_run_id,
        "cut_id": cut_id,
        "stage_id": stage.id,
        "stage_workflow": stage.workflow,
        "agent": agent,
        "worktree_path": worktree_path,
        "branch": branch,
        "org": org,
        "repo": repo,
        "status": "recorded",
        "role": "write_stage_cut_child",
        "live_dispatch": False,
        "spawned": False,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    meta_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return meta_path


def _load_child_record(meta_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _record_dispatch_result(
    contract: CutDispatchContract, result: dict[str, Any]
) -> None:
    """Atomically retain dispatcher identity before the child can disappear."""
    meta_path = Path(contract.meta_path)
    payload = _load_child_record(meta_path)
    if not payload:
        raise RuntimeError(f"missing lifecycle cut record: {contract.child_run_id}")
    payload.update(
        {
            "status": "launching" if result.get("spawned") else "recorded",
            "spawned": bool(result.get("spawned")),
            "live_dispatch": bool(result.get("live_dispatch")),
            "dispatcher_run_id": str(result.get("dispatcher_run_id") or ""),
            "provider_run_id": str(
                result.get("provider_run_id") or result.get("run_id") or ""
            ),
            # Reference to the authoritative dispatch receipts. Worktree
            # geometry and child scheduler state are read back from there;
            # this record binds the parent identity to them, it does not
            # compete with them.
            "receipts_path": str(result.get("receipts_path") or ""),
            "dispatch": result,
            "dispatched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    temporary = meta_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(meta_path)
