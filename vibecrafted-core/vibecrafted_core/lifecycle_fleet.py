"""WRITE-stage fleet: one recorded child run per listed plan cut.

Stage workers remain forbidden from launching live agent lines. The
test-gated exception is WRITE stages whose mission lists cuts: they
become dispatchers that persist a control-plane contract per cut.
Live ``vc-dispatch`` is still never spawned from this module.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .control_plane import control_plane_home
from .dispatch.worktrees import repo_identity
from .runtime_paths import vibecrafted_home
from .workflows.model import WorkflowStage

WRITE_FLEET_STAGE_WORKFLOWS = frozenset(
    {"implement", "workflow", "marbles", "polarize", "hydrate"}
)

# Default runtime contract: a stage worker must not spawn agent lines.
STAGE_WORKER_MAY_LAUNCH_AGENT_LINES = False

_CUT_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

SupervisorLaunch = Callable[["CutDispatchContract"], dict[str, Any]]


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
    lines = str(mission_text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return ()
    cuts: list[str] = []
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
                cuts.extend(_split_cut_token(token) for token in inline.split(","))
                break
            in_block = True
            continue
        if not line.startswith((" ", "\t")):
            break
        if stripped.startswith("- "):
            cuts.append(_split_cut_token(stripped[2:]))
            continue
        if stripped.startswith("-"):
            cuts.append(_split_cut_token(stripped[1:]))
            continue
        break
    seen: set[str] = set()
    ordered: list[str] = []
    for cut_id in cuts:
        if not cut_id or cut_id in seen:
            continue
        seen.add(cut_id)
        ordered.append(cut_id)
    return tuple(ordered)


def _split_cut_token(raw: str) -> str:
    token = raw.strip().strip("'\"")
    if ":" in token and not token.startswith("cut/"):
        token = token.split(":", 1)[0].strip().strip("'\"")
    return token


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
    """Live agent-line spawn stays forbidden even when the fleet exception applies."""
    return False


def child_run_id(parent_run_id: str, stage_id: str, cut_id: str) -> str:
    """Stable control-plane id for one WRITE-stage cut child."""
    return f"{safe_cut_id(parent_run_id)}-{safe_cut_id(stage_id)}-{safe_cut_id(cut_id)}"


def record_only_supervisor(contract: CutDispatchContract) -> dict[str, Any]:
    """Default supervisor: accept the recorded contract, do not spawn."""
    return {
        "accepted": True,
        "spawned": False,
        "live_dispatch": False,
        "cut_id": contract.cut_id,
        "run_id": contract.child_run_id,
        "worktree_path": contract.worktree_path,
        "meta_path": contract.meta_path,
    }


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
) -> list[dict[str, Any]]:
    """Call a supervisor once per recorded cut. Default records-only; never live."""
    if not fleet.children:
        return []
    launch = supervisor or record_only_supervisor
    launched: list[dict[str, Any]] = []
    for contract in fleet.children:
        result = dict(launch(contract))
        result.setdefault("cut_id", contract.cut_id)
        result.setdefault("run_id", contract.child_run_id)
        result.setdefault("worktree_path", contract.worktree_path)
        result["spawned"] = False
        result["live_dispatch"] = False
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
