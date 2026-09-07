"""WRITE-stage fleet: durable lifecycle identities around dispatcher-owned cuts.

The lifecycle owns the parent-to-cut relation; the supplied dispatcher owns
worktree creation, provider launch, liveness, recovery and receipts.  Keeping
that split explicit prevents a stage worker from becoming a second scheduler.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .control_plane import control_plane_home
from .dispatch.worktrees import repo_identity
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
            description=(
                f"WRITE stage fleet for lifecycle run {fleet.parent_run_id}"
            ),
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
                raise RuntimeError(
                    f"declared dispatch plan not found: {candidate}"
                )
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
        else:
            _start_background_dispatch(run_id, run)

        by_cut = {cut.id: cut for cut in dispatch.cuts}
        return [
            _project_receipt(
                contract,
                dispatch_run_id=run_id,
                receipts_path=store.path,
                receipt=store.cut(contract.cut_id),
                cut=by_cut.get(contract.cut_id),
                resumed=resume,
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
        "receipt_state": str(receipt.get("state") or "queued"),
        "agent": getattr(cut, "agent", contract.agent),
        "model": getattr(cut, "model", ""),
        "resumed": resumed,
    }


_ACTIVE_FLEET_DISPATCHES: dict[str, threading.Thread] = {}
_FLEET_DISPATCH_ERRORS: dict[str, str] = {}
_FLEET_DISPATCH_LOCK = threading.Lock()


def _start_background_dispatch(run_id: str, run: Callable[[], None]) -> None:
    """Run one dispatch off the lifecycle thread, keeping the handle reachable."""

    def target() -> None:
        try:
            run()
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
            with _FLEET_DISPATCH_LOCK:
                _FLEET_DISPATCH_ERRORS[run_id] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=target, name=f"lifecycle-fleet-{run_id}")
    with _FLEET_DISPATCH_LOCK:
        _ACTIVE_FLEET_DISPATCHES[run_id] = thread
    thread.start()


def join_stage_dispatch(dispatch_run_id: str, timeout: float | None = None) -> bool:
    """Wait for a backgrounded stage dispatch; True when it is no longer running."""
    with _FLEET_DISPATCH_LOCK:
        thread = _ACTIVE_FLEET_DISPATCHES.get(dispatch_run_id)
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()


def stage_dispatch_error(dispatch_run_id: str) -> str:
    """The recorded failure of a backgrounded stage dispatch, if it failed."""
    with _FLEET_DISPATCH_LOCK:
        return _FLEET_DISPATCH_ERRORS.get(dispatch_run_id, "")


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
