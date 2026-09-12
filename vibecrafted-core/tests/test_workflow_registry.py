from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path

from vibecrafted_core import workflow
from vibecrafted_core.workflows import registry


def test_registry_keeps_supported_workflows_explicit() -> None:
    assert registry.workflow_definition("workflow") is not None
    justdo = registry.workflow_definition("justdo")
    implement = registry.workflow_definition("implement")
    assert justdo is not None
    assert implement is not None
    assert justdo.id == "justdo"
    assert implement.id == "implement"
    assert justdo.id != implement.id
    assert tuple(implement.aliases) == ()
    ship = registry.workflow_manifest("vc-ship")
    assert ship is not None
    assert "justdo" not in {stage.id for stage in ship.stages}
    assert "implement" in {stage.id for stage in ship.stages}
    assert registry.SUPPORTED_WORKFLOWS == workflow.SUPPORTED_WORKFLOWS


def test_deck_command_canon_matches_registry() -> None:
    """The bash deck's front gate must accept every registry workflow.

    trust/guard entered the registry while the deck kept its old `_skills`
    array and dispatch case, so `vibecrafted trust` died at `cmd_unknown`
    before python ever saw the command. Pin both deck gate surfaces to
    SUPPORTED_WORKFLOWS so the canons cannot drift apart silently again.
    """
    from vibecrafted_core.package_resources import deck_path

    deck_text = deck_path().read_text(encoding="utf-8")

    skills_array = re.search(r"^_skills=\(([^)]*)\)", deck_text, re.MULTILINE)
    assert skills_array, "deck must declare the _skills canon array"
    assert set(skills_array.group(1).split()) == set(registry.SUPPORTED_WORKFLOWS)

    for workflow_id in sorted(registry.SUPPORTED_WORKFLOWS):
        in_alternation = re.search(
            rf'(?m)^\s*(?:[a-z]+\|)*{workflow_id}(?:\|[a-z]+)*\)\s*\n\s*run_skill "\$cmd"',
            deck_text,
        )
        dedicated = re.search(
            rf'(?m)^\s*{workflow_id}\)\s*run_skill "{workflow_id}"', deck_text
        )
        dedicated_frontend = re.search(
            rf'(?m)^\s*{workflow_id}\)\s*cmd_{workflow_id} "\$@"', deck_text
        )
        assert in_alternation or dedicated or dedicated_frontend, (
            f"deck main dispatch must route '{workflow_id}' to run_skill"
        )


def test_registry_classifies_workflow_runtime_kinds() -> None:
    assert registry.workflow_runtime_kind("workflow") == "direct_agent"
    assert registry.workflow_runtime_kind("prune") == "direct_agent"
    assert registry.workflow_runtime_kind("research") == "supervised_research"
    assert registry.workflow_runtime_kind("marbles") == "supervised_marbles"
    assert registry.workflow_runtime_kind("polarize") == "supervised_marbles"
    assert registry.workflow_definition("research").terminal_layout == "research"


def test_registry_models_input_policy() -> None:
    implement = registry.workflow_definition("implement")
    prune = registry.workflow_definition("prune")
    marbles = registry.workflow_definition("marbles")
    polarize = registry.workflow_definition("polarize")

    assert implement is not None
    assert implement.requires_input is True
    assert prune is not None
    assert prune.can_use_default_prompt is True
    assert marbles is not None
    assert marbles.requires_input is False
    assert marbles.supports_count is True
    assert marbles.supports_depth is True
    assert polarize is not None
    assert polarize.requires_input is False
    assert polarize.supports_count is True
    assert polarize.supports_depth is True


def test_registry_models_read_write_lifecycle() -> None:
    assert registry.workflow_definition("scaffold").cadence == "read"
    assert registry.workflow_definition("workflow").cadence == "write"
    assert registry.workflow_definition("marbles").cadence == "write"
    assert registry.workflow_definition("audit").cadence == "read"
    assert registry.workflow_definition("dou").cadence == "read"

    lifecycle = registry.workflow_lifecycle()
    names = [item.id for item in lifecycle]
    assert names.index("scaffold") < names.index("implement")
    assert names.index("review") < names.index("workflow")
    assert names.index("marbles") < names.index("audit")
    assert names.index("dou") < names.index("hydrate")
    assert registry.workflow_definition("workflow").tooling == (
        "vc-init",
        "vc-research",
        "vc-justdo",
    )


def test_vc_ship_manifest_is_single_source_for_lifecycle_order() -> None:
    manifest = registry.workflow_manifest("vc-ship")

    assert manifest is not None
    assert manifest.first_stage.id == "scaffold"
    assert "approve_transition" in manifest.human_controls
    assert "force_audit" in manifest.human_controls
    assert [stage.id for stage in manifest.stages] == [
        "scaffold",
        "implement",
        "review",
        "workflow",
        "followup",
        "marbles",
        "audit",
        "polarize",
        "dou",
        "hydrate",
        "release",
    ]
    assert [stage.phase for stage in manifest.stages] == [
        "read",
        "write",
        "read",
        "write",
        "read",
        "write",
        "read",
        "write",
        "read",
        "write",
        "write",
    ]
    assert manifest.stage("marbles").audit_after == "audit"
    assert (
        "audit_after_completed_stage" in manifest.stage("marbles").transition_conditions
    )
    assert manifest.stage("dou").can_modify_code is False
    assert manifest.stage("dou").allowed_artifacts == (
        "reports",
        "cache",
        "run_state",
        "transcripts",
    )
    assert manifest.stage("hydrate").can_modify_code is True
    assert "code" in manifest.stage("hydrate").allowed_artifacts


def test_manifest_payload_is_json_ready() -> None:
    payload = registry.workflow_manifest_payload("vc-marbles")

    assert payload["id"] == "vc-marbles"
    assert payload["entry_stage"] == "marbles"
    stages = payload["stages"]
    assert payload["human_controls"] == ["interrupt_workflow", "force_audit"]
    assert stages[0]["workflow"] == "marbles"
    assert stages[0]["audit_after"] == "audit"
    assert stages[0]["transition_conditions"] == [
        "launch_accepted",
        "stage_completed",
        "changed_files_reported",
        "next_stage_on_success",
        "audit_after_completed_stage",
    ]
    assert stages[0]["allowed_artifacts"] == [
        "code",
        "docs",
        "generated_files",
        "reports",
        "cache",
        "run_state",
        "transcripts",
    ]
    assert stages[1]["phase"] == "read"
    assert "no_code_mutation" in stages[1]["transition_conditions"]


def test_required_lifecycle_manifests_are_single_source() -> None:
    expected = {
        "vc-ship": ("scaffold", "read"),
        "vc-scaffold": ("scaffold", "read"),
        "vc-implement": ("implement", "write"),
        "vc-review": ("review", "read"),
        "vc-workflow": ("workflow", "write"),
        "vc-followup": ("followup", "read"),
        "vc-dou": ("dou", "read"),
        "vc-audit": ("audit", "read"),
        "vc-marbles": ("marbles", "write"),
        "vc-polarize": ("polarize", "write"),
        "vc-hydrate": ("hydrate", "write"),
        "vc-release": ("release", "write"),
    }

    for workflow_id, (entry_stage, phase) in expected.items():
        payload = registry.workflow_manifest_payload(workflow_id)
        stages = payload["stages"]
        assert payload["entry_stage"] == entry_stage
        assert stages[0]["id"] == entry_stage
        assert stages[0]["phase"] == phase


def test_prune_default_prompt_is_runtime_workflow_asset() -> None:
    assert importlib.util.find_spec("vibecrafted_core.package_resources") is not None
    package_resources = importlib.import_module("vibecrafted_core.package_resources")
    prompt = registry.workflow_default_prompt("prune")

    assert "Repository health / prune ACTION run." in prompt
    assert "No deletion on vibes. Prove every cut." in prompt
    assert registry._workflow_dirs("prune")[0] == (
        package_resources.runtime_path() / "workflows" / "prune"
    )
    assert (
        package_resources.runtime_path() / "workflows" / "prune" / "default_prompt.md"
    ).is_file()


def test_prune_default_prompt_can_be_overridden(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "prune"
    custom.mkdir()
    (custom / "default_prompt.md").write_text("custom prune prompt\n", encoding="utf-8")
    monkeypatch.setenv("VIBECRAFTED_WORKFLOWS_DIR", str(tmp_path))

    assert registry.workflow_default_prompt("prune") == "custom prune prompt"
