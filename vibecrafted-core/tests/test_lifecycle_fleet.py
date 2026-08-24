from __future__ import annotations

import json
from pathlib import Path

from vibecrafted_core.lifecycle_fleet import (
    STAGE_WORKER_MAY_LAUNCH_AGENT_LINES,
    WRITE_FLEET_STAGE_WORKFLOWS,
    CutDispatchContract,
    cut_worktree_path,
    dispatch_recorded_children,
    is_write_fleet_stage,
    live_vc_dispatch_permitted,
    load_cut_records,
    mission_cuts,
    record_only_supervisor,
    record_write_stage_fleet,
    stage_worker_may_launch_agent_lines,
)
from vibecrafted_core.workflows.model import WorkflowStage


def _write_stage(workflow: str = "implement") -> WorkflowStage:
    return WorkflowStage(
        id=workflow,
        workflow=workflow,
        phase="write",
        order=2,
        name=f"VC {workflow.title()}",
    )


def _read_stage() -> WorkflowStage:
    return WorkflowStage(
        id="scaffold",
        workflow="scaffold",
        phase="read",
        order=1,
        name="VC Scaffold",
    )


def test_mission_cuts_parses_inline_and_nested_list() -> None:
    inline = "---\ncuts: W0-a, W0-b, W1-a\n---\nmission"
    assert mission_cuts(inline) == ("W0-a", "W0-b", "W1-a")

    nested = "---\ndoc_id: x\ncuts:\n  - W0-a\n  - W2-b\nother: y\n---\nmission body\n"
    assert mission_cuts(nested) == ("W0-a", "W2-b")
    assert mission_cuts("plain mission, no frontmatter") == ()
    assert mission_cuts("---\nstage_agents: implement=claude\n---\n") == ()


def test_mission_cuts_dedupes_and_strips() -> None:
    text = "---\ncuts: W0-a, W0-a, 'W1-c'\n---\n"
    assert mission_cuts(text) == ("W0-a", "W1-c")


def test_write_fleet_stage_set_is_the_ship_write_dispatchers() -> None:
    assert WRITE_FLEET_STAGE_WORKFLOWS == {
        "implement",
        "workflow",
        "marbles",
        "polarize",
        "hydrate",
    }
    assert is_write_fleet_stage(_write_stage("implement"))
    assert is_write_fleet_stage(_write_stage("hydrate"))
    assert not is_write_fleet_stage(_read_stage())
    decorate = WorkflowStage(
        id="decorate", workflow="decorate", phase="write", order=99, name="decorate"
    )
    assert not is_write_fleet_stage(decorate)


def test_agent_line_contract_exception_is_write_plus_cuts_and_never_live() -> None:
    assert STAGE_WORKER_MAY_LAUNCH_AGENT_LINES is False
    assert live_vc_dispatch_permitted() is False
    cuts = ("W0-a", "W0-b")
    assert stage_worker_may_launch_agent_lines(stage=_write_stage(), cuts=cuts)
    assert not stage_worker_may_launch_agent_lines(stage=_write_stage(), cuts=())
    assert not stage_worker_may_launch_agent_lines(stage=_read_stage(), cuts=cuts)
    assert live_vc_dispatch_permitted() is False


def test_cut_worktree_path_uses_run_id_not_day(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    path = cut_worktree_path(
        org="vetcoders",
        repo="vibecrafted",
        run_id="life-ship-260819-1",
        cut_id="W0-a",
        home=home,
    )
    assert (
        path
        == home
        / "worktrees"
        / "vetcoders"
        / "vibecrafted"
        / "life-ship-260819-1"
        / "W0-a"
    )


def test_record_write_stage_fleet_writes_one_control_plane_record_per_cut(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    cuts = ("W0-a", "W0-b", "W1-c")
    parent = "life-impl-test"
    fleet = record_write_stage_fleet(
        stage=_write_stage("implement"),
        cuts=cuts,
        parent_run_id=parent,
        repo_root=tmp_path,
        agent="codex",
        org="vetcoders",
        repo="vibecrafted",
    )

    assert fleet.exception_granted is True
    assert fleet.live_dispatch is False
    assert len(fleet.children) == 3
    records = load_cut_records(parent)
    assert len(records) >= 3
    by_cut = {str(item["cut_id"]): item for item in records}
    assert set(by_cut) == set(cuts)
    for cut_id in cuts:
        expected = str(
            home / "worktrees" / "vetcoders" / "vibecrafted" / parent / cut_id
        )
        assert by_cut[cut_id]["worktree_path"] == expected
        assert by_cut[cut_id]["parent_run_id"] == parent
        assert by_cut[cut_id]["spawned"] is False
        assert by_cut[cut_id]["live_dispatch"] is False
        assert (
            home
            / "control_plane"
            / "runtime_runs"
            / by_cut[cut_id]["run_id"]
            / "meta.json"
        ).is_file()


def test_mocked_supervisor_would_launch_n_children(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    launched: list[str] = []

    def supervisor(contract: CutDispatchContract) -> dict:
        launched.append(contract.cut_id)
        return {
            "accepted": True,
            "spawned": False,
            "cut_id": contract.cut_id,
            "command": ["vc-dispatch", "--cut", contract.cut_id],
        }

    fleet = record_write_stage_fleet(
        stage=_write_stage("workflow"),
        cuts=("alpha", "beta", "gamma"),
        parent_run_id="life-work-test",
        repo_root=tmp_path,
        agent="claude",
        org="vetcoders",
        repo="vibecrafted",
    )
    results = dispatch_recorded_children(fleet, supervisor=supervisor)
    assert launched == ["alpha", "beta", "gamma"]
    assert len(results) == 3
    assert all(item["spawned"] is False for item in results)
    assert all(item["live_dispatch"] is False for item in results)
    assert "vc-dispatch" in results[0]["command"]


def test_read_stage_and_write_without_cuts_record_no_children(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    empty = record_write_stage_fleet(
        stage=_write_stage(),
        cuts=(),
        parent_run_id="life-empty",
        repo_root=tmp_path,
        agent="codex",
        org="vetcoders",
        repo="vibecrafted",
    )
    assert empty.children == ()
    assert empty.exception_granted is False
    assert load_cut_records("life-empty") == []

    refused = record_write_stage_fleet(
        stage=_read_stage(),
        cuts=("W0-a", "W0-b"),
        parent_run_id="life-read",
        repo_root=tmp_path,
        agent="codex",
        org="vetcoders",
        repo="vibecrafted",
    )
    assert refused.children == ()
    assert refused.exception_granted is False
    assert load_cut_records("life-read") == []


def test_record_only_supervisor_never_marks_spawned() -> None:
    contract = CutDispatchContract(
        cut_id="W0-a",
        child_run_id="parent-implement-W0-a",
        parent_run_id="parent",
        stage_id="implement",
        stage_workflow="implement",
        worktree_path="/tmp/worktree",
        branch="cut/W0-a",
        org="vetcoders",
        repo="vibecrafted",
        agent="codex",
        meta_path="/tmp/meta.json",
    )
    result = record_only_supervisor(contract)
    assert result["spawned"] is False
    assert result["live_dispatch"] is False
    assert result["cut_id"] == "W0-a"


def test_child_meta_is_json_control_plane_record(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    fleet = record_write_stage_fleet(
        stage=_write_stage("marbles"),
        cuts=("L2",),
        parent_run_id="life-marb",
        repo_root=tmp_path,
        agent="grok",
        org="vetcoders",
        repo="vibecrafted",
    )
    payload = json.loads(Path(fleet.children[0].meta_path).read_text(encoding="utf-8"))
    assert payload["cut_id"] == "L2"
    assert payload["stage_workflow"] == "marbles"
    assert payload["role"] == "write_stage_cut_child"
    assert payload["worktree_path"].endswith("/life-marb/L2")
