"""Canonical registry of every workflow definition, ship stage, and manifest."""

from __future__ import annotations

import os
from pathlib import Path

from ..package_resources import runtime_path
from ..runtime_paths import vibecrafted_tools_home
from .model import WorkflowCadence, WorkflowDefinition, WorkflowManifest, WorkflowStage


def _direct(
    id: str,
    *,
    cadence: WorkflowCadence,
    lifecycle_order: int,
    tooling: tuple[str, ...] = (),
) -> WorkflowDefinition:
    """Build a plain ``direct_agent`` workflow definition (the common-case shape)."""
    return WorkflowDefinition(
        id=id,
        cadence=cadence,
        lifecycle_order=lifecycle_order,
        tooling=tooling,
    )


WORKFLOW_DEFINITIONS: dict[str, WorkflowDefinition] = {
    "audit": _direct(
        "audit",
        cadence="read",
        lifecycle_order=70,
        tooling=("vc-init", "vc-loctree", "vc-aicx", "vc-research"),
    ),
    "canary": _direct(
        "canary",
        cadence="write",
        lifecycle_order=17,
        tooling=("vc-init", "vc-loctree", "vc-canary", "vc-agents"),
    ),
    "decorate": _direct(
        "decorate",
        cadence="write",
        lifecycle_order=102,
        tooling=("vc-init", "vc-decorate"),
    ),
    "delegate": _direct(
        "delegate",
        cadence="write",
        lifecycle_order=25,
        tooling=("vc-init", "vc-delegate"),
    ),
    "dou": _direct(
        "dou",
        cadence="read",
        lifecycle_order=90,
        tooling=("vc-init", "vc-intents", "vc-loctree"),
    ),
    "followup": _direct(
        "followup",
        cadence="read",
        lifecycle_order=50,
        tooling=("vc-init", "vc-intents", "vc-loctree"),
    ),
    "hydrate": _direct(
        "hydrate",
        cadence="write",
        lifecycle_order=100,
        tooling=("vc-init", "vc-operator", "vc-decorate"),
    ),
    "implement": WorkflowDefinition(
        id="implement",
        cadence="write",
        lifecycle_order=20,
        tooling=("vc-init", "vc-operator", "vc-agents"),
    ),
    # Standalone posture launcher (ADR-0001). NOT an alias of implement; NOT a
    # VC-ship stage (absent from SHIP_STAGES). Task type is defined by the
    # prompt, not by this id. Order sits with ownership-family launchers, not
    # between ship WRITE stages.
    "justdo": WorkflowDefinition(
        id="justdo",
        cadence="write",
        lifecycle_order=16,
        tooling=("vc-init", "vc-justdo", "vc-ownership"),
    ),
    "intents": _direct(
        "intents",
        cadence="read",
        lifecycle_order=45,
        tooling=("vc-init", "vc-intents", "vc-loctree"),
    ),
    "marbles": WorkflowDefinition(
        id="marbles",
        cadence="write",
        lifecycle_order=60,
        input_policy="optional",
        runtime_kind="supervised_marbles",
        supports_count=True,
        supports_depth=True,
        tooling=("vc-init", "vc-marbles"),
    ),
    "ownership": _direct(
        "ownership",
        cadence="write",
        lifecycle_order=15,
        tooling=("vc-init", "vc-ownership"),
    ),
    "partner": _direct(
        "partner",
        cadence="meta",
        lifecycle_order=5,
        tooling=("vc-init", "vc-partner", "vc-scaffold"),
    ),
    "polarize": WorkflowDefinition(
        id="polarize",
        cadence="write",
        lifecycle_order=80,
        input_policy="optional",
        runtime_kind="supervised_marbles",
        supports_count=True,
        supports_depth=True,
        tooling=("vc-init", "vc-polarize"),
    ),
    "prune": WorkflowDefinition(
        id="prune",
        cadence="write",
        lifecycle_order=55,
        input_policy="optional_with_default",
        default_prompt_file="default_prompt.md",
        tooling=("vc-init", "vc-loctree"),
    ),
    "release": _direct(
        "release",
        cadence="write",
        lifecycle_order=110,
        tooling=("vc-init", "vc-release"),
    ),
    "research": WorkflowDefinition(
        id="research",
        cadence="read",
        lifecycle_order=12,
        default_agent="swarm",
        runtime_kind="supervised_research",
        terminal_layout="research",
        tooling=("vc-init", "vc-research"),
    ),
    "review": _direct(
        "review",
        cadence="read",
        lifecycle_order=30,
        tooling=("vc-init", "vc-loctree", "vc-review", "vc-screenscribe", "vc-prview"),
    ),
    "scaffold": _direct(
        "scaffold",
        cadence="read",
        lifecycle_order=10,
        tooling=("vc-init", "vc-loctree", "vc-research"),
    ),
    "trust": _direct(
        "trust",
        cadence="read",
        lifecycle_order=75,
        tooling=("vc-init", "vc-loctree", "vc-aicx", "vc-trust"),
    ),
    # Enforcement sibling of trust: inventories gates and refuses continuation
    # on trust block. READ cadence (can_modify_code=false); never judges claims.
    "guard": _direct(
        "guard",
        cadence="read",
        lifecycle_order=76,
        tooling=("vc-init", "vc-trust", "vc-guard"),
    ),
    "workflow": _direct(
        "workflow",
        cadence="write",
        lifecycle_order=40,
        tooling=("vc-init", "vc-research", "vc-justdo"),
    ),
}

SUPPORTED_WORKFLOWS = frozenset(WORKFLOW_DEFINITIONS)
WORKFLOW_ALIASES = {
    alias: definition.id
    for definition in WORKFLOW_DEFINITIONS.values()
    for alias in definition.aliases
}


def _stage(
    workflow: str,
    order: int,
    *,
    id: str = "",
    name: str = "",
    agent: str = "",
    model: str = "",
    next_stage: str = "",
    fallback_stage: str = "",
    audit_after: str = "",
    transition: str = "success",
    transition_conditions: tuple[str, ...] = (),
    allowed_artifacts: tuple[str, ...] = (),
) -> WorkflowStage:
    """Build one ``WorkflowStage``, deriving phase/tooling from its workflow's
    definition and defaulting transition conditions/allowed artifacts when unset.
    """
    definition = WORKFLOW_DEFINITIONS[workflow]
    default_conditions = _transition_conditions(
        phase=definition.cadence,
        next_stage=next_stage,
        fallback_stage=fallback_stage,
        audit_after=audit_after,
    )
    return WorkflowStage(
        id=id or workflow,
        workflow=workflow,
        phase=definition.cadence,
        order=order,
        name=name or f"VC {workflow.title()}",
        tooling=definition.tooling,
        agent=agent,
        model=model,
        next_stage=next_stage,
        fallback_stage=fallback_stage,
        audit_after=audit_after,
        transition=transition,
        transition_conditions=transition_conditions or default_conditions,
        allowed_artifacts=allowed_artifacts or _allowed_artifacts(definition.cadence),
    )


def _allowed_artifacts(cadence: WorkflowCadence) -> tuple[str, ...]:
    """Default artifact kinds a stage may write: write-cadence adds code/docs/generated."""
    common = ("reports", "cache", "run_state", "transcripts")
    if cadence == "write":
        return ("code", "docs", "generated_files", *common)
    return common


def _transition_conditions(
    *,
    phase: WorkflowCadence,
    next_stage: str = "",
    fallback_stage: str = "",
    audit_after: str = "",
) -> tuple[str, ...]:
    """Derive default transition-condition labels for a stage from its shape."""
    conditions = ["launch_accepted", "stage_completed"]
    if phase == "read":
        conditions.append("no_code_mutation")
    if phase == "write":
        conditions.append("changed_files_reported")
    if next_stage:
        conditions.append("next_stage_on_success")
    if fallback_stage:
        conditions.append("fallback_on_failed_artifact")
    if audit_after:
        conditions.append("audit_after_completed_stage")
    return tuple(conditions)


SHIP_STAGES: tuple[WorkflowStage, ...] = (
    _stage("scaffold", 1, name="VC Scaffold", next_stage="implement"),
    _stage("implement", 2, name="VC Implement", next_stage="review"),
    _stage("review", 3, name="VC Review", next_stage="workflow"),
    _stage("workflow", 4, name="VC Workflow", next_stage="followup"),
    _stage("followup", 5, name="Follow-up", next_stage="marbles"),
    _stage(
        "marbles",
        6,
        name="VC Marbles",
        next_stage="audit",
        audit_after="audit",
    ),
    _stage(
        "audit", 7, name="VC Audit", next_stage="polarize", fallback_stage="marbles"
    ),
    _stage("polarize", 8, name="VC Polarize", next_stage="dou"),
    _stage("dou", 9, name="VC DoU", next_stage="hydrate", fallback_stage="polarize"),
    _stage("hydrate", 10, name="VC Hydrate", next_stage="release"),
    _stage("release", 11, name="VC Release"),
)


WORKFLOW_MANIFESTS: dict[str, WorkflowManifest] = {
    "vc-ship": WorkflowManifest(
        id="vc-ship",
        name="VC Ship",
        description="Full Vibecrafted lifecycle from scaffold through release.",
        stages=SHIP_STAGES,
        entry_stage="scaffold",
        human_controls=(
            "approve_transition",
            "interrupt_workflow",
            "force_audit",
            "accept_dou",
            "choose_fallback_stage",
        ),
    ),
    "vc-scaffold": WorkflowManifest(
        id="vc-scaffold",
        name="VC Scaffold",
        description="Read-only founder brainstorm and plan-writing entry stage.",
        stages=(_stage("scaffold", 1, name="VC Scaffold"),),
        entry_stage="scaffold",
        human_controls=("approve_transition", "interrupt_workflow"),
    ),
    "vc-implement": WorkflowManifest(
        id="vc-implement",
        name="VC Implement",
        description="Write delivery stage for autonomous implementation.",
        stages=(_stage("implement", 1, name="VC Implement"),),
        entry_stage="implement",
        human_controls=("approve_transition", "interrupt_workflow", "force_audit"),
    ),
    "vc-review": WorkflowManifest(
        id="vc-review",
        name="VC Review",
        description="Read-only bounded review of implemented changes.",
        stages=(_stage("review", 1, name="VC Review"),),
        entry_stage="review",
        human_controls=("approve_transition", "interrupt_workflow"),
    ),
    "vc-workflow": WorkflowManifest(
        id="vc-workflow",
        name="VC Workflow",
        description="Write examine-research-implement pipeline stage.",
        stages=(_stage("workflow", 1, name="VC Workflow"),),
        entry_stage="workflow",
        human_controls=("approve_transition", "interrupt_workflow"),
    ),
    "vc-followup": WorkflowManifest(
        id="vc-followup",
        name="Follow-up",
        description="Read-only post-implementation trajectory audit.",
        stages=(_stage("followup", 1, name="Follow-up"),),
        entry_stage="followup",
        human_controls=("approve_transition", "interrupt_workflow", "force_audit"),
    ),
    "vc-dou": WorkflowManifest(
        id="vc-dou",
        name="VC DoU",
        description="Definition of Undone read-only launch-readiness audit.",
        stages=(_stage("dou", 1, name="VC DoU"),),
        entry_stage="dou",
        human_controls=("accept_dou", "force_audit", "interrupt_workflow"),
    ),
    "vc-audit": WorkflowManifest(
        id="vc-audit",
        name="VC Audit",
        description="Read-only falsification of completed implementation claims.",
        stages=(_stage("audit", 1, name="VC Audit"),),
        entry_stage="audit",
        human_controls=("approve_transition", "choose_fallback_stage"),
    ),
    "vc-marbles": WorkflowManifest(
        id="vc-marbles",
        name="VC Marbles",
        description="Entropy-up write convergence with automatic audit handoff.",
        stages=(
            _stage(
                "marbles",
                1,
                name="VC Marbles",
                next_stage="audit",
                audit_after="audit",
            ),
            _stage("audit", 2, name="VC Audit"),
        ),
        entry_stage="marbles",
        human_controls=("approve_transition", "interrupt_workflow", "force_audit"),
    ),
    "vc-polarize": WorkflowManifest(
        id="vc-polarize",
        name="VC Polarize",
        description="Entropy-down write simplification after Marbles/Audit.",
        stages=(
            _stage(
                "polarize",
                1,
                name="VC Polarize",
                next_stage="audit",
                audit_after="audit",
            ),
            _stage("audit", 2, name="VC Audit"),
        ),
        entry_stage="polarize",
        human_controls=("approve_transition", "interrupt_workflow"),
    ),
    "vc-hydrate": WorkflowManifest(
        id="vc-hydrate",
        name="VC Hydrate",
        description="Write preflight for product surface and release readiness.",
        stages=(_stage("hydrate", 1, name="VC Hydrate"),),
        entry_stage="hydrate",
        human_controls=("approve_transition", "interrupt_workflow"),
    ),
    "vc-release": WorkflowManifest(
        id="vc-release",
        name="VC Release",
        description="Write outward ship stage: deploy, publish, verify.",
        stages=(_stage("release", 1, name="VC Release"),),
        entry_stage="release",
        human_controls=("approve_transition", "interrupt_workflow"),
    ),
}
WORKFLOW_MANIFEST_ALIASES = {
    key.removeprefix("vc-"): key for key in WORKFLOW_MANIFESTS if key.startswith("vc-")
}


def workflow_definition(workflow_id: str) -> WorkflowDefinition | None:
    """Resolve a workflow id or alias to its `WorkflowDefinition`, or `None` if unknown."""
    resolved = WORKFLOW_ALIASES.get(workflow_id, workflow_id)
    return WORKFLOW_DEFINITIONS.get(resolved)


def workflow_runtime_kind(workflow_id: str) -> str:
    """Look up a workflow's runtime kind, defaulting to ``direct_agent`` if unknown."""
    definition = workflow_definition(workflow_id)
    return definition.runtime_kind if definition is not None else "direct_agent"


def workflow_lifecycle() -> tuple[WorkflowDefinition, ...]:
    """All workflow definitions ordered by `lifecycle_order` (scaffold-first)."""
    return tuple(
        sorted(WORKFLOW_DEFINITIONS.values(), key=lambda item: item.lifecycle_order)
    )


def workflow_manifest(manifest_id: str) -> WorkflowManifest | None:
    """Resolve a manifest id or bare ``vc-`` alias to its `WorkflowManifest`."""
    key = WORKFLOW_MANIFEST_ALIASES.get(manifest_id, manifest_id)
    return WORKFLOW_MANIFESTS.get(key)


def workflow_manifest_payload(manifest_id: str) -> dict[str, object]:
    """Serialize a manifest and its stages into a JSON-ready dict.

    Raises ``ValueError`` for an unknown manifest id.
    """
    manifest = workflow_manifest(manifest_id)
    if manifest is None:
        raise ValueError(f"Unsupported lifecycle workflow: {manifest_id}")
    return {
        "id": manifest.id,
        "name": manifest.name,
        "description": manifest.description,
        "entry_stage": manifest.entry_stage,
        "human_controls": list(manifest.human_controls),
        "stages": [
            {
                "id": stage.id,
                "workflow": stage.workflow,
                "phase": stage.phase,
                "order": stage.order,
                "name": stage.name,
                "tooling": list(stage.tooling),
                "agent": stage.agent,
                "model": stage.model,
                "can_modify_code": stage.can_modify_code,
                "next_stage": stage.next_stage,
                "fallback_stage": stage.fallback_stage,
                "audit_after": stage.audit_after,
                "transition": stage.transition,
                "transition_conditions": list(stage.transition_conditions),
                "allowed_artifacts": list(stage.allowed_artifacts),
            }
            for stage in manifest.stages
        ],
    }


def _workflow_dirs(workflow_id: str) -> list[Path]:
    """Candidate directories that may hold a workflow's on-disk assets, in
    precedence order: env override, runtime path, tools-home fallback, package dir.
    """
    candidates: list[Path] = []
    override = str(os.environ.get("VIBECRAFTED_WORKFLOWS_DIR") or "").strip()
    if override:
        candidates.append(Path(override).expanduser() / workflow_id)
    candidates.extend(
        [
            runtime_path() / "workflows" / workflow_id,
            vibecrafted_tools_home()
            / "vibecrafted-local"
            / "runtime"
            / "workflows"
            / workflow_id,
            Path(__file__).resolve().parent / workflow_id,
        ]
    )
    return candidates


def workflow_default_prompt(workflow_id: str) -> str:
    """Read a workflow's `default_prompt_file` from the first dir where it exists.

    Returns "" when the workflow has no default prompt or none of the
    candidate directories hold the file.
    """
    definition = workflow_definition(workflow_id)
    if definition is None or not definition.default_prompt_file:
        return ""
    for directory in _workflow_dirs(definition.id):
        path = directory / definition.default_prompt_file
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
    return ""
