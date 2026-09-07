from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from vibecrafted_core.dispatch.receipts import DispatchReceiptStore
from vibecrafted_core.dispatch.supervisor import CellRun
from vibecrafted_core.lifecycle_fleet import (
    _FLEET_DISPATCH_ERRORS,
    STAGE_WORKER_MAY_LAUNCH_AGENT_LINES,
    WRITE_FLEET_STAGE_WORKFLOWS,
    CutDispatchContract,
    _start_detached_dispatch,
    build_stage_dispatch,
    cut_worktree_path,
    dispatch_recorded_children,
    dispatcher_fleet_launch,
    fleet_obligations,
    is_write_fleet_stage,
    join_stage_dispatch,
    live_vc_dispatch_permitted,
    load_cut_records,
    mission_cut_agents,
    mission_cuts,
    mission_dispatch_plan,
    mission_stage_cuts,
    record_only_supervisor,
    record_write_stage_fleet,
    request_stage_dispatch_stop,
    scheduler_owner_alive,
    scheduler_owner_identity,
    stage_dispatch_error,
    stage_dispatch_home,
    stage_dispatch_run_id,
    stage_fleet_receipts,
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


def test_mission_stage_cuts_binds_each_write_stage_without_replay() -> None:
    mission = "---\nstage_cuts: implement=W0-a,W0-b, marbles=W1-a\n---\n"
    assert mission_stage_cuts(mission, "implement") == ("W0-a", "W0-b")
    assert mission_stage_cuts(mission, "marbles") == ("W1-a",)
    assert mission_stage_cuts(mission, "hydrate") == ()


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


def test_agent_line_contract_exception_is_write_plus_cuts_and_stage_worker_safe() -> (
    None
):
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


def test_supervisor_launch_is_retained_and_duplicate_replay_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    launched: list[str] = []

    def supervisor(contract: CutDispatchContract) -> dict:
        launched.append(contract.cut_id)
        return {
            "accepted": True,
            "spawned": True,
            "live_dispatch": True,
            "cut_id": contract.cut_id,
            "dispatcher_run_id": "dispatch-parent",
            "provider_run_id": f"provider-{contract.cut_id}",
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
    assert all(item["spawned"] is True for item in results)
    assert all(item["live_dispatch"] is True for item in results)
    assert "vc-dispatch" in results[0]["command"]
    records = {item["cut_id"]: item for item in load_cut_records("life-work-test")}
    assert records["alpha"]["provider_run_id"] == "provider-alpha"
    assert records["alpha"]["dispatcher_run_id"] == "dispatch-parent"
    try:
        dispatch_recorded_children(fleet, supervisor=supervisor)
    except RuntimeError as exc:
        assert "refusing duplicate" in str(exc)
    else:
        raise AssertionError("live lifecycle child replayed")


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


def _seed_repo(path: Path) -> str:
    """A real git repo: the dispatcher refuses to build geometry without one."""
    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ("init", "-q"),
        ("config", "user.email", "agents@vetcoders.io"),
        ("config", "user.name", "lifecycle-fleet-test"),
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"], cwd=path, check=True, capture_output=True
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_mission_cut_agents_reads_the_provider_pin_beside_each_cut() -> None:
    inline = "---\ncuts: W0-a: codex, W0-b: claude, W0-c\n---\n"
    assert mission_cuts(inline) == ("W0-a", "W0-b", "W0-c")
    assert mission_cut_agents(inline) == {"W0-a": "codex", "W0-b": "claude"}

    nested = "---\ncuts:\n  - W0-a: codex\n  - W0-b: claude\n---\n"
    assert mission_cuts(nested) == ("W0-a", "W0-b")
    assert mission_cut_agents(nested) == {"W0-a": "codex", "W0-b": "claude"}
    assert mission_cut_agents("---\ncuts: W0-a, W0-b\n---\n") == {}


def test_no_dispatcher_seam_fails_closed_naming_the_owning_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    fleet = record_write_stage_fleet(
        stage=_write_stage("implement"),
        cuts=("W0-a",),
        parent_run_id="life-fail-closed",
        repo_root=tmp_path,
        agent="codex",
        org="vetcoders",
        repo="vibecrafted",
    )
    # The old default silently degraded to record-only, so a "dispatched"
    # stage could mean nothing had launched at all.
    with pytest.raises(RuntimeError) as excinfo:
        dispatch_recorded_children(fleet)
    assert "dispatcher_fleet_launch" in str(excinfo.value)

    # The degraded seam stays reachable, but only when it is asked for.
    results = dispatch_recorded_children(fleet, supervisor=record_only_supervisor)
    assert results[0]["spawned"] is False


def test_build_stage_dispatch_is_one_plan_over_all_cuts_with_two_providers(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    mission = "---\ncuts: W0-a: codex, W0-b: claude, W0-c: codex\n---\nmission body\n"
    fleet = record_write_stage_fleet(
        stage=_write_stage("implement"),
        cuts=mission_cuts(mission),
        parent_run_id="life-plan",
        repo_root=tmp_path,
        agent="codex",
        org="vetcoders",
        repo="vibecrafted",
    )
    dispatch = build_stage_dispatch(
        fleet,
        repo_root=tmp_path,
        cut_agents=mission_cut_agents(mission),
        mission_text=mission,
    )
    assert [cut.id for cut in dispatch.cuts] == ["W0-a", "W0-b", "W0-c"]
    assert {cut.agent for cut in dispatch.cuts} == {"codex", "claude"}
    # One plan, wide enough for every declared cut to be in flight at once.
    assert dispatch.policy.concurrency == 3
    assert dispatch.policy.allow_concurrency is True
    assert dispatch.common.text == mission


def test_public_default_dispatches_one_concurrent_fleet_on_the_real_dispatcher(
    tmp_path: Path, monkeypatch
) -> None:
    """The ordinary construction path — no injected fleet seam at all.

    Only the provider spawn is bounded (the lowest transport boundary); the
    production supervisor, worktree manager and receipt ledger all execute.
    """
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)

    # A barrier of three only clears if the scheduler really holds three cuts
    # open at once; a serialized fleet deadlocks here instead of passing.
    barrier = threading.Barrier(3, timeout=30)
    observed: dict[str, tuple[str, str]] = {}
    lock = threading.Lock()

    def cell_launcher(cut, _prompt: str, kind: str):
        with lock:
            observed[cut.id] = (cut.agent, cut.runtime_root)
        barrier.wait()
        report = Path(cut.artifact_path) / f"{cut.id}.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(f"{cut.id} done\n", encoding="utf-8")
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id=f"provider-{cut.id}",
            report_path=str(report),
            exit_code=0,
        )

    mission = (
        "---\n"
        "cuts: W0-a: codex, W0-b: claude, W0-c: codex\n"
        "---\n"
        "# Mission: three disjoint cuts, two providers\n"
    )
    fleet = record_write_stage_fleet(
        stage=_write_stage("implement"),
        cuts=mission_cuts(mission),
        parent_run_id="life-public",
        repo_root=repo,
        agent="codex",
    )
    launch = dispatcher_fleet_launch(
        repo_root=repo,
        mission_text=mission,
        cell_launcher=cell_launcher,
        await_config={"poll_s": 0.01, "timeout_min": 1.0},
        wait=True,
    )
    results = dispatch_recorded_children(fleet, fleet_launch=launch)

    assert len(results) == 3
    assert all(item["live_dispatch"] is True for item in results)
    assert {item["agent"] for item in results} == {"codex", "claude"}
    assert {agent for agent, _ in observed.values()} == {"codex", "claude"}

    # Real, disjoint worktrees created by the dispatcher's own manager.
    worktrees = {cut_id: Path(root) for cut_id, (_, root) in observed.items()}
    assert len(set(worktrees.values())) == 3
    for cut_id, path in worktrees.items():
        assert path.is_dir()
        assert (path / ".git").exists()
        assert path.name == cut_id

    # Durable receipts under the dispatcher's authoritative ledger.
    receipts = stage_fleet_receipts("life-public", "implement")
    assert receipts["run_id"] == stage_dispatch_run_id("life-public", "implement")
    for cut_id in ("W0-a", "W0-b", "W0-c"):
        entry = receipts["cuts"][cut_id]
        assert entry["provider_run_id"] == f"provider-{cut_id}"
        assert entry["worktree_path"] == str(worktrees[cut_id])
        # A mission that only lists cut ids declares nothing to verify, so no
        # cut may claim a verified state — unverified is reported, not hidden.
        assert entry["acceptance"] == "failed"
        assert entry["gates"] == []

    # The lifecycle record binds parent identity to that ledger; it does not
    # keep a second copy of the geometry.
    records = {item["cut_id"]: item for item in load_cut_records("life-public")}
    for cut_id in ("W0-a", "W0-b", "W0-c"):
        assert records[cut_id]["parent_run_id"] == "life-public"
        assert records[cut_id]["receipts_path"] == str(
            stage_dispatch_home(stage_dispatch_run_id("life-public", "implement"))
            / "receipts.json"
        )
        assert records[cut_id]["dispatcher_run_id"] == stage_dispatch_run_id(
            "life-public", "implement"
        )


def _plan(repo: Path, cuts: tuple[str, ...], agents: tuple[str, ...]) -> Path:
    """A real dispatch plan TOML: the route that carries declared verifiers."""
    body = "".join(
        f'''[[cuts]]
id = "{cut_id}"
agent = "{agent}"
workflow = "implement"
prompt = "run {cut_id}"
  [[cuts.verify]]
  run = "test -s {{reports_dir}}/{cut_id}.md"
  expect = {{ exit_code = 0 }}
'''
        for cut_id, agent in zip(cuts, agents, strict=True)
    )
    path = repo / "acceptance-plan.toml"
    path.write_text(
        f'''schema = "vibecrafted.dispatch.v1"
[meta]
name = "acceptance-fleet"
repo = "{repo}"
[policy]
concurrency = {len(cuts)}
allow_concurrency = {str(len(cuts) > 1).lower()}
await = {{ poll_s = 0.01, timeout_min = 1.0 }}
{body}''',
        encoding="utf-8",
    )
    return path


def _report_writer(launches: list[str]):
    def cell_launcher(cut, _prompt: str, kind: str):
        launches.append(cut.id)
        report = Path(cut.artifact_path) / f"{cut.id}.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(f"{cut.id} done\n", encoding="utf-8")
        return CellRun(
            cut_id=cut.id,
            kind=kind,
            accepted=True,
            run_id=f"provider-{cut.id}",
            report_path=str(report),
            exit_code=0,
        )

    return cell_launcher


def test_mission_referenced_plan_carries_verifiers_and_settles_the_fleet(
    tmp_path: Path, monkeypatch
) -> None:
    """The declared-plan route: existing loader, existing verifiers, no new parser."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _plan(repo, ("W0-a", "W0-b", "W0-c"), ("codex", "claude", "codex"))
    mission = f"---\ndispatch_plan: {plan.name}\ncuts: W0-a, W0-b, W0-c\n---\n"
    assert mission_dispatch_plan(mission) == plan.name

    launches: list[str] = []
    fleet = record_write_stage_fleet(
        stage=_write_stage("implement"),
        cuts=mission_cuts(mission),
        parent_run_id="life-plan-run",
        repo_root=repo,
        agent="codex",
    )
    results = dispatch_recorded_children(
        fleet,
        fleet_launch=dispatcher_fleet_launch(
            repo_root=repo,
            mission_text=mission,
            cell_launcher=_report_writer(launches),
            wait=True,
        ),
    )
    assert sorted(launches) == ["W0-a", "W0-b", "W0-c"]
    assert {item["agent"] for item in results} == {"codex", "claude"}

    receipts = stage_fleet_receipts("life-plan-run", "implement")
    for cut_id in ("W0-a", "W0-b", "W0-c"):
        entry = receipts["cuts"][cut_id]
        assert entry["state"] == "settled"
        assert entry["acceptance"] == "verified"
        assert entry["gates"], "a settled cut must carry its verifier evidence"


def test_a_plan_that_misses_a_declared_cut_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _plan(repo, ("W0-a",), ("codex",))
    mission = f"---\ndispatch_plan: {plan.name}\ncuts: W0-a, W0-b\n---\n"
    fleet = record_write_stage_fleet(
        stage=_write_stage("implement"),
        cuts=mission_cuts(mission),
        parent_run_id="life-plan-gap",
        repo_root=repo,
        agent="codex",
    )
    with pytest.raises(RuntimeError, match="does not cover lifecycle cut"):
        dispatch_recorded_children(
            fleet,
            fleet_launch=dispatcher_fleet_launch(
                repo_root=repo, mission_text=mission, wait=True
            ),
        )


def test_second_launch_resumes_the_same_dispatch_without_relaunching(
    tmp_path: Path, monkeypatch
) -> None:
    """Crash retry is settled by the dispatcher's receipts, not by a bool."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _plan(repo, ("solo",), ("codex",))
    mission = f"---\ndispatch_plan: {plan.name}\ncuts: solo\n---\n"
    launches: list[str] = []

    def make_fleet():
        return record_write_stage_fleet(
            stage=_write_stage("implement"),
            cuts=mission_cuts(mission),
            parent_run_id="life-resume",
            repo_root=repo,
            agent="codex",
        )

    launch = dispatcher_fleet_launch(
        repo_root=repo,
        mission_text=mission,
        cell_launcher=_report_writer(launches),
        wait=True,
    )
    dispatch_recorded_children(make_fleet(), fleet_launch=launch)
    assert launches == ["solo"]
    assert (
        stage_fleet_receipts("life-resume", "implement")["cuts"]["solo"]["state"]
        == "settled"
    )

    # Replaying a live child without the recovery verb is still refused.
    with pytest.raises(RuntimeError, match="refusing duplicate"):
        dispatch_recorded_children(make_fleet(), fleet_launch=launch)

    # With the recovery verb the dispatcher re-owns its settled cut from the
    # receipt ledger instead of executing the same work a second time.
    again = dispatch_recorded_children(make_fleet(), fleet_launch=launch, resume=True)
    assert launches == ["solo"]
    assert again[0]["resumed"] is True


# --------------------------------------------------------------------------
# The read side of the control boundary: obligations, durable scheduler
# failure, and exact-cut recovery.
# --------------------------------------------------------------------------


def _run_fleet(
    repo: Path,
    plan: Path,
    parent_run_id: str,
    launches: list[str],
    *,
    resume: bool = False,
) -> list[dict]:
    mission = f"---\ndispatch_plan: {plan.name}\ncuts: W0-a, W0-b, W0-c\n---\n"
    fleet = record_write_stage_fleet(
        stage=_write_stage("implement"),
        cuts=mission_cuts(mission),
        parent_run_id=parent_run_id,
        repo_root=repo,
        agent="codex",
    )
    return dispatch_recorded_children(
        fleet,
        fleet_launch=dispatcher_fleet_launch(
            repo_root=repo,
            mission_text=mission,
            cell_launcher=_report_writer(launches),
            wait=True,
        ),
        resume=resume,
    )


def test_fleet_obligations_read_identities_back_from_the_dispatcher_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    """A reopened observer recovers cut, provider, worktree and attempt."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _plan(repo, ("W0-a", "W0-b", "W0-c"), ("codex", "claude", "codex"))
    launches: list[str] = []
    projected = _run_fleet(repo, plan, "life-reopen", launches)

    progress = fleet_obligations(
        "life-reopen",
        "implement",
        declared_cuts=("W0-a", "W0-b", "W0-c"),
        plan_path=str(plan),
    )

    assert progress["verdict"] == "complete"
    assert progress["complete"] is True
    assert progress["blocking"] == []
    by_cut = {item["cut_id"]: item for item in progress["cuts"]}
    for cut_id in ("W0-a", "W0-b", "W0-c"):
        entry = by_cut[cut_id]
        assert entry["state"] == "settled"
        assert entry["acceptance"] == "verified"
        # Identity survives the launching process, verbatim from the ledger.
        assert entry["provider_run_id"] == f"provider-{cut_id}"
        assert entry["worktree_path"] and Path(entry["worktree_path"]).name.endswith(
            cut_id
        )
        assert entry["attempt"] == "initial"
    # The dispatcher's geometry — not the lifecycle placeholder — is authority.
    assert {item["worktree_path"] for item in projected} == {
        item["worktree_path"] for item in progress["cuts"]
    }
    assert progress["recovery_command"] == (
        f"vibecrafted dispatch {plan} --resume "
        f"{stage_dispatch_run_id('life-reopen', 'implement')}"
    )


def test_a_scheduler_that_dies_before_its_children_is_durably_visible(
    tmp_path: Path, monkeypatch
) -> None:
    """Queued is not proof of a spawn, and the reason must outlive the owner.

    The owner is a separate process now, so its failure cannot be reported by
    an exception in this interpreter. It has to survive in the ledger, which
    is the only thing a reopened observer will ever read.
    """
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _plan(repo, ("W0-a", "W0-b", "W0-c"), ("codex", "claude", "codex"))
    from vibecrafted_core.dispatch.doctor import diagnose_file

    dispatch = diagnose_file(plan).dispatch
    assert dispatch is not None
    run_id = stage_dispatch_run_id("life-scheduler-dead", "implement")
    store = DispatchReceiptStore(run_id, dispatch.cuts, repo_root=str(repo))

    # A plan the owner cannot parse: it dies before any provider is reached,
    # which is exactly the case where "queued" would otherwise look like work.
    broken = tmp_path / "unparseable.dispatch.toml"
    broken.write_text("this is not a dispatch plan\n", encoding="utf-8")
    _start_detached_dispatch(run_id, store, plan_path=broken, repo_root=repo)
    assert join_stage_dispatch(run_id, timeout=60)

    # Simulate the launching process being gone: only the ledger remains.
    _FLEET_DISPATCH_ERRORS.clear()
    error = stage_dispatch_error(run_id)
    assert "scheduler owner exited" in error
    assert "W0-a" in error and "W0-b" in error and "W0-c" in error

    progress = fleet_obligations(
        "life-scheduler-dead",
        "implement",
        declared_cuts=("W0-a", "W0-b", "W0-c"),
        plan_path=str(plan),
    )
    # A cut that never left the queue under a dead owner is a failure that
    # never started, not work in flight.
    assert progress["verdict"] == "failed"
    assert progress["counts"] == {"failed": 3}
    assert progress["scheduler_detached"] is True


def test_recovery_reruns_only_the_failed_cut_and_leaves_its_siblings_alone(
    tmp_path: Path, monkeypatch
) -> None:
    """Step 5 of the acceptance: resume one cut, never execute the others twice."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _plan(repo, ("W0-a", "W0-b", "W0-c"), ("codex", "claude", "codex"))
    launches: list[str] = []
    _run_fleet(repo, plan, "life-exact-retry", launches)
    assert sorted(launches) == ["W0-a", "W0-b", "W0-c"]

    from vibecrafted_core.dispatch.doctor import diagnose_file

    report = diagnose_file(plan)
    assert report.dispatch is not None
    store = DispatchReceiptStore(
        stage_dispatch_run_id("life-exact-retry", "implement"),
        report.dispatch.cuts,
        repo_root=str(repo),
        create=False,
    )
    store.update("W0-b", "failed", acceptance="failed")

    _run_fleet(repo, plan, "life-exact-retry", launches, resume=True)

    # The first three are concurrent, so their order is not meaningful; what
    # is meaningful is that recovery added exactly one launch, and it was the
    # failed cut.
    assert sorted(launches[:3]) == ["W0-a", "W0-b", "W0-c"]
    assert launches[3:] == ["W0-b"], (
        "only the failed cut may run again; settled siblings are restored "
        "from the receipt ledger"
    )
    progress = fleet_obligations(
        "life-exact-retry",
        "implement",
        declared_cuts=("W0-a", "W0-b", "W0-c"),
        plan_path=str(plan),
    )
    assert progress["verdict"] == "complete"


def test_scheduler_metadata_merge_never_discards_a_concurrent_cut_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    """Scheduler errors and supervisor transitions share the locked ledger."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _plan(repo, ("W0-a",), ("codex",))
    from vibecrafted_core.dispatch.doctor import diagnose_file

    dispatch = diagnose_file(plan).dispatch
    assert dispatch is not None
    run_id = stage_dispatch_run_id("life-ledger-lock", "implement")
    store = DispatchReceiptStore(run_id, dispatch.cuts, repo_root=str(repo))
    barrier = threading.Barrier(2)

    def supervisor_write() -> None:
        barrier.wait()
        store.update("W0-a", "active", provider_run_id="provider-W0-a")

    def scheduler_write() -> None:
        barrier.wait()
        store.update_metadata(scheduler_error="owner lost transport")

    first = threading.Thread(target=supervisor_write)
    second = threading.Thread(target=scheduler_write)
    first.start()
    second.start()
    first.join()
    second.join()
    payload = store.read()
    assert payload["scheduler_error"] == "owner lost transport"
    assert payload["cuts"]["W0-a"]["state"] == "active"
    assert payload["cuts"]["W0-a"]["provider_run_id"] == "provider-W0-a"


def test_parent_handoff_never_overwrites_a_stop_accepted_during_popen(
    tmp_path: Path, monkeypatch
) -> None:
    """The post-Popen identity receipt is stale metadata, never stop authority."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _plan(repo, ("W0-a",), ("codex",))
    from vibecrafted_core.dispatch.doctor import diagnose_file
    import vibecrafted_core.lifecycle_fleet as fleet_module

    dispatch = diagnose_file(plan).dispatch
    assert dispatch is not None
    store = DispatchReceiptStore("handoff-stop-run", dispatch.cuts, repo_root=str(repo))

    class PopenThatStops:
        pid = 424242

        def __init__(self, *_args, **_kwargs) -> None:
            store.request_stop(scheduler_stop_requested_at="during-popen")

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(fleet_module.subprocess, "Popen", PopenThatStops)
    monkeypatch.setattr(
        fleet_module,
        "process_identity_receipt",
        lambda pid, **_kwargs: {
            "pid": pid,
            "pgid": pid,
            "start_token": "test",
            "command_sha256": "a" * 64,
        },
    )

    _start_detached_dispatch("handoff-stop-run", store, plan_path=plan, repo_root=repo)

    payload = store.read()
    assert payload["scheduler_stop_requested"] is True
    assert (
        payload["scheduler_stop_sequence"] > payload["scheduler_owner_resume_sequence"]
    )


def test_fleet_recovery_command_quotes_space_containing_plan_path() -> None:
    from vibecrafted_core.lifecycle_fleet import fleet_recovery_command

    command = fleet_recovery_command(
        "life implement fleet", "/tmp/plan with spaces.toml"
    )
    assert (
        command
        == "vibecrafted dispatch '/tmp/plan with spaces.toml' --resume 'life implement fleet'"
    )


# --------------------------------------------------------------- owner boundary

# The only fake in the detached-owner proof is the provider binary itself. The
# real launcher, worktree geometry, receipt store, verifiers and control code
# all run for real, in a fresh interpreter, out of this test's reach.
_BOUNDED_PROVIDER = r"""#!/bin/sh
set -e
# An optional dwell so a test can catch this cut genuinely in flight.
if [ -n "$_VC_FAKE_PROVIDER_SLEEP" ]; then
  sleep "$_VC_FAKE_PROVIDER_SLEEP"
fi
if [ -n "$VIBECRAFTED_DISPATCH_ARTIFACT_PATH" ]; then
  mkdir -p "$VIBECRAFTED_DISPATCH_ARTIFACT_PATH"
  printf 'delivery for %s\n' "$VIBECRAFTED_DISPATCH_CUT_ID" \
    > "$VIBECRAFTED_DISPATCH_ARTIFACT_PATH/$VIBECRAFTED_DISPATCH_CUT_ID.md"
fi
if [ -n "$VIBECRAFTED_REPORT_PATH" ]; then
  mkdir -p "$(dirname "$VIBECRAFTED_REPORT_PATH")"
  printf -- '---\nstatus: complete\nfinalized: true\nclaim: bounded provider delivery\n---\ndone\n' \
    > "$VIBECRAFTED_REPORT_PATH"
fi
echo "bounded provider done"
"""

# A caller that does exactly what a terminal or the App does: start the fleet,
# then sit there until it is closed.
_DETACHED_CALLER = """
import json, sys, time
from pathlib import Path

from vibecrafted_core.dispatch.doctor import diagnose_file
from vibecrafted_core.dispatch.receipts import DispatchReceiptStore
from vibecrafted_core.lifecycle_fleet import _start_detached_dispatch

plan, repo, run_id = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
dispatch = diagnose_file(plan).dispatch
store = DispatchReceiptStore(run_id, dispatch.cuts, repo_root=str(repo), create=True)
receipt = _start_detached_dispatch(run_id, store, plan_path=plan, repo_root=repo)
print(json.dumps(receipt), flush=True)
time.sleep(600)
"""


def _bounded_runtime_env(tmp_path: Path) -> dict[str, str]:
    """An isolated runtime home whose provider discovery finds only our fake."""
    import sys as _sys

    import vibecrafted_core

    home = tmp_path / "home"
    fake_bin = tmp_path / "provider-bin"
    tmp_dir = tmp_path / "tmp"
    for path in (home, fake_bin, tmp_dir):
        path.mkdir(parents=True, exist_ok=True)
    for agent in ("codex", "claude"):
        binary = fake_bin / agent
        binary.write_text(_BOUNDED_PROVIDER, encoding="utf-8")
        binary.chmod(0o755)
    package_root = str(Path(vibecrafted_core.__file__).resolve().parents[1])
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
        "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        # The existing provider-discovery seam: this directory is searched
        # first, so the fake wins without the launcher being touched.
        "VIBECRAFTED_RUNTIME_BIN": str(fake_bin),
        "VIBECRAFTED_GUARD": "0",
        "PYTHONPATH": package_root,
        "TMPDIR": str(tmp_dir),
        "LANG": "C",
        "_VC_TEST_INTERPRETER": _sys.executable,
    }


def _await_receipt_state(
    receipts_path: Path, cut_id: str, states: set[str], timeout: float
) -> dict:
    """Poll one cut receipt until it reaches one of ``states``, or give up."""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            payload = json.loads(receipts_path.read_text(encoding="utf-8"))
            last = dict(payload.get("cuts", {}).get(cut_id) or {})
        except (OSError, json.JSONDecodeError):
            last = {}
        if last.get("state") in states:
            return last
        time.sleep(0.2)
    return last


def test_the_detached_owner_outlives_its_caller_and_settles_the_fleet(
    tmp_path: Path, monkeypatch
) -> None:
    """Closing the initiating process must cost the observer, not the work.

    Everything below the provider binary is real: a fresh interpreter runs the
    dispatcher's own public entrypoint, builds real worktrees, writes the real
    receipt ledger and runs the declared verifier. The caller is killed
    outright — only this test's own process — and the scheduler still settles.
    """
    env = _bounded_runtime_env(tmp_path)
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _plan(repo, ("W0-a",), ("codex",))
    run_id = stage_dispatch_run_id("life-detached-owner", "implement")
    caller_script = tmp_path / "caller.py"
    caller_script.write_text(_DETACHED_CALLER, encoding="utf-8")

    caller = subprocess.Popen(
        [env["_VC_TEST_INTERPRETER"], str(caller_script), str(plan), str(repo), run_id],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert caller.stdout is not None
    line = caller.stdout.readline()
    if not line:
        caller.kill()
        raise AssertionError(
            f"caller never launched an owner: {caller.stderr.read()[-2000:]}"
        )
    receipt = json.loads(line)

    owner_pid = int(receipt["scheduler_owner_pid"])
    identity = receipt["scheduler_owner_identity"]
    assert receipt["scheduler_owner_mode"] == "detached-subprocess"
    assert receipt["scheduler_detached"] is True
    assert identity["pid"] == owner_pid
    # A detached owner leads its own session; that is what SIGHUP cannot reach.
    assert identity["pgid"] == owner_pid
    assert identity["start_token"]
    assert len(identity["command_sha256"]) == 64

    # Close the "terminal": kill ONLY this test's own caller process.
    caller.kill()
    caller.wait(timeout=30)
    assert caller.poll() is not None

    receipts_path = (
        Path(env["VIBECRAFTED_HOME"])
        / "control_plane"
        / "dispatches"
        / run_id
        / "receipts.json"
    )
    settled = _await_receipt_state(
        receipts_path, "W0-a", {"settled", "failed", "stopped"}, timeout=240
    )
    assert settled.get("state") == "settled", f"owner did not settle: {settled}"
    assert settled.get("acceptance") == "verified"

    # Delivery proof is the artifact the declared verifier measured.
    delivered = Path(str(settled.get("artifact_path") or "")) / "W0-a.md"
    assert delivered.is_file()
    assert delivered.stat().st_size > 0

    # A fresh observer — this process never spawned the owner, so its handle
    # map is empty — recovers the same identity from the durable ledger.
    monkeypatch.setenv("VIBECRAFTED_HOME", env["VIBECRAFTED_HOME"])
    observed, observed_pid = scheduler_owner_identity(run_id)
    assert observed_pid == owner_pid
    assert observed["start_token"] == identity["start_token"]
    assert observed["command_sha256"] == identity["command_sha256"]
    assert join_stage_dispatch(run_id, timeout=60) is True
    # The owner is finished, so identity liveness says gone — not "alive
    # because something holds that number".
    alive, reason = scheduler_owner_alive(run_id)
    assert alive is False
    assert reason in {"process_identity_gone", "process_identity_mismatch"}


def _owner_ledger_for_sentinel(
    tmp_path: Path, run_id: str, sentinel_pid: int, *, stale: bool
) -> DispatchReceiptStore:
    """A ledger whose recorded owner is (or only looks like) the live sentinel."""
    from vibecrafted_core.dispatch.model import Cut
    from vibecrafted_core.process_control import process_identity_receipt

    cuts = (
        Cut(
            id="W0-a",
            phase="implement",
            agent="codex",
            workflow="implement",
            resolved_workflow="implement",
        ),
    )
    store = DispatchReceiptStore(run_id, cuts, repo_root=str(tmp_path))
    identity = process_identity_receipt(sentinel_pid, run_id=run_id)
    assert identity is not None
    if stale:
        # The recorded owner died and the kernel handed its number to this
        # unrelated process. Start token and command hash are what expose it.
        identity = {
            **identity,
            "start_token": "start:000000",
            "command_sha256": "0" * 64,
        }
    store.update_metadata(
        scheduler_owner_pid=sentinel_pid,
        scheduler_owner_identity=identity,
        scheduler_owner_mode="detached-subprocess",
        scheduler_detached=True,
    )
    return store


class _SignalRecorder:
    """Stand-in for ``os`` that records signals instead of delivering them."""

    def __init__(self, signals: list[tuple[str, int, int]]) -> None:
        self._signals = signals

    def __getattr__(self, name: str):
        return getattr(os, name)

    def kill(self, pid: int, sig: int) -> None:
        self._signals.append(("kill", pid, sig))

    def killpg(self, pid: int, sig: int) -> None:
        self._signals.append(("killpg", pid, sig))


def test_a_stale_owner_identity_signals_nothing_at_all(
    tmp_path: Path, monkeypatch
) -> None:
    """A recycled PID must not collect a SIGTERM meant for a dead scheduler."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    sentinel = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        run_id = "stale-owner-run"
        store = _owner_ledger_for_sentinel(tmp_path, run_id, sentinel.pid, stale=True)

        signals: list[tuple[str, int, int]] = []
        # Replace the module reference the stop path uses, not ``os`` itself:
        # patching the shared module would also disarm this test's own cleanup.
        monkeypatch.setattr(
            "vibecrafted_core.lifecycle_fleet.os", _SignalRecorder(signals)
        )

        result = request_stage_dispatch_stop(run_id, signal_owner=True)

        # The fence is real work, and is honestly reported as accepted.
        assert result["accepted"] is True
        assert result["fenced"] is True
        assert store.read()["scheduler_stop_requested"] is True
        # The signal is not.
        assert result["owner_signalled"] is False
        assert result["owner_identity"] == "process_identity_mismatch"
        assert signals == []
        # And the unrelated process is untouched, not merely unsignalled.
        assert sentinel.poll() is None
        assert store.read()["scheduler_stop_signalled"] is False
    finally:
        sentinel.kill()
        sentinel.wait(timeout=10)


def test_a_proven_owner_identity_is_the_only_thing_that_gets_signalled(
    tmp_path: Path, monkeypatch
) -> None:
    """The counterpart: a matching identity really does stop its own group."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    sentinel = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        run_id = "live-owner-run"
        store = _owner_ledger_for_sentinel(tmp_path, run_id, sentinel.pid, stale=False)

        result = request_stage_dispatch_stop(run_id, signal_owner=True)

        assert result["owner_identity"] == "process_identity_current"
        assert result["owner_signalled"] is True
        assert result["owner_pgid"] == sentinel.pid
        assert sentinel.wait(timeout=15) is not None
        assert store.read()["scheduler_stop_signalled"] is True
    finally:
        if sentinel.poll() is None:
            sentinel.kill()
            sentinel.wait(timeout=10)


def test_a_recycled_pid_does_not_read_as_a_running_owner(
    tmp_path: Path, monkeypatch
) -> None:
    """join answers from identity, not from "something holds that pid"."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    sentinel = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        run_id = "recycled-pid-run"
        _owner_ledger_for_sentinel(tmp_path, run_id, sentinel.pid, stale=True)
        started = time.monotonic()
        # No handle in this interpreter: the ledger identity is the only truth.
        assert join_stage_dispatch(run_id, timeout=5) is True
        assert time.monotonic() - started < 5
        assert sentinel.poll() is None
    finally:
        sentinel.kill()
        sentinel.wait(timeout=10)


def test_a_fleet_without_a_durable_plan_says_it_cannot_be_detached(
    tmp_path: Path, monkeypatch
) -> None:
    """A plan that exists only in memory cannot own detached work; say so."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)
    # No `dispatch_plan:` — the cuts are derived, so no file names them.
    mission = "---\ncuts: W0-a\n---\n"
    monkeypatch.setattr(
        "vibecrafted_core.dispatch.supervisor.run_dispatch",
        lambda *_args, **_kwargs: None,
    )
    fleet = record_write_stage_fleet(
        stage=_write_stage("implement"),
        cuts=mission_cuts(mission),
        parent_run_id="life-underivable",
        repo_root=repo,
        agent="codex",
    )
    dispatch_recorded_children(
        fleet,
        fleet_launch=dispatcher_fleet_launch(
            repo_root=repo, mission_text=mission, cell_launcher=None
        ),
    )
    run_id = stage_dispatch_run_id("life-underivable", "implement")
    assert join_stage_dispatch(run_id, timeout=10)
    payload = stage_fleet_receipts("life-underivable", "implement")
    assert payload["scheduler_detached"] is False
    assert payload["scheduler_owner_mode"] == "in-process"
    assert "dispatch_plan:" in payload["scheduler_undetachable_reason"]


def _serial_plan(repo: Path, cuts: tuple[str, ...], agents: tuple[str, ...]) -> Path:
    """A real plan the scheduler must run one cut at a time.

    Serial execution is what makes the interrupt race observable at all: while
    the first cut is in flight, the second is genuinely queued.
    """
    body = "".join(
        f'''[[cuts]]
id = "{cut_id}"
agent = "{agent}"
workflow = "implement"
prompt = "run {cut_id}"
  [[cuts.verify]]
  run = "test -s {{reports_dir}}/{cut_id}.md"
  expect = {{ exit_code = 0 }}
'''
        for cut_id, agent in zip(cuts, agents, strict=True)
    )
    path = repo / "serial-plan.toml"
    path.write_text(
        f'''schema = "vibecrafted.dispatch.v1"
[meta]
name = "serial-fleet"
repo = "{repo}"
[policy]
concurrency = 1
await = {{ poll_s = 0.05, timeout_min = 2.0 }}
{body}''',
        encoding="utf-8",
    )
    return path


def _await_ledger(receipts_path: Path, predicate, timeout: float) -> dict:
    """Poll the whole ledger until ``predicate`` holds, or give up."""
    deadline = time.monotonic() + timeout
    payload: dict = {}
    while time.monotonic() < deadline:
        try:
            payload = json.loads(receipts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if payload and predicate(payload):
            return payload
        time.sleep(0.1)
    return payload


def test_an_interrupt_from_another_process_stops_the_queue_not_the_settlement(
    tmp_path: Path, monkeypatch
) -> None:
    """The operator's interrupt and the scheduler are different processes.

    This is the boundary the ledger lock exists for: the fence is written by
    one process while another is scheduling. The queued cut must never reach a
    provider, and the cut already in flight must still be settled — a stop
    that abandoned live work would be a different kind of loss.
    """
    env = _bounded_runtime_env(tmp_path)
    # The first cut stays in flight long enough for the interrupt to land
    # while its sibling is genuinely queued.
    env["_VC_FAKE_PROVIDER_SLEEP"] = "3"
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _serial_plan(repo, ("W0-a", "W0-b"), ("codex", "codex"))
    run_id = stage_dispatch_run_id("life-interrupt-race", "implement")
    caller_script = tmp_path / "caller.py"
    caller_script.write_text(_DETACHED_CALLER, encoding="utf-8")

    caller = subprocess.Popen(
        [env["_VC_TEST_INTERPRETER"], str(caller_script), str(plan), str(repo), run_id],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert caller.stdout is not None
        line = caller.stdout.readline()
        if not line:
            raise AssertionError(
                f"caller never launched an owner: {caller.stderr.read()[-2000:]}"
            )
        receipts_path = (
            Path(env["VIBECRAFTED_HOME"])
            / "control_plane"
            / "dispatches"
            / run_id
            / "receipts.json"
        )
        in_flight = _await_ledger(
            receipts_path,
            lambda payload: (
                payload["cuts"]["W0-a"].get("state") in {"launching", "active"}
            ),
            timeout=120,
        )
        assert in_flight["cuts"]["W0-a"]["state"] in {"launching", "active"}
        assert in_flight["cuts"]["W0-b"]["state"] == "queued"

        # The interrupt is issued from THIS process, against a scheduler that
        # is somewhere else entirely.
        monkeypatch.setenv("VIBECRAFTED_HOME", env["VIBECRAFTED_HOME"])
        stop = request_stage_dispatch_stop(run_id)
        assert stop["accepted"] is True
        assert stop["fenced"] is True
        # The fence is not a kill: the owner keeps its settlement duty.
        assert stop["owner_signalled"] is False

        assert join_stage_dispatch(run_id, timeout=180) is True
        payload = json.loads(receipts_path.read_text(encoding="utf-8"))
    finally:
        caller.kill()
        caller.wait(timeout=30)

    queued = payload["cuts"]["W0-b"]
    assert queued["state"] == "stopped"
    assert queued["acceptance"] == "interrupted"
    # Never admitted means never spawned; admission is the only door.
    assert "launch_admitted_at" not in queued

    live = payload["cuts"]["W0-a"]
    assert live["launch_admitted_at"]
    assert live["state"] == "settled", f"interrupt abandoned live work: {live}"
    assert live["acceptance"] == "verified"
