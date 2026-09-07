from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest
from vibecrafted_core.dispatch.receipts import DispatchReceiptStore
from vibecrafted_core.dispatch.supervisor import CellRun
from vibecrafted_core.lifecycle_fleet import (
    _FLEET_DISPATCH_ERRORS,
    STAGE_WORKER_MAY_LAUNCH_AGENT_LINES,
    WRITE_FLEET_STAGE_WORKFLOWS,
    CutDispatchContract,
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
    record_only_supervisor,
    record_write_stage_fleet,
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


def test_agent_line_contract_exception_is_write_plus_cuts_and_stage_worker_safe() -> None:
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


def test_supervisor_launch_is_retained_and_duplicate_replay_is_refused(tmp_path: Path, monkeypatch) -> None:
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
            stage_dispatch_home(
                stage_dispatch_run_id("life-public", "implement")
            )
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
    mission = (
        "---\n"
        f"dispatch_plan: {plan.name}\n"
        "cuts: W0-a, W0-b, W0-c\n"
        "---\n"
    )
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
    assert stage_fleet_receipts("life-resume", "implement")["cuts"]["solo"][
        "state"
    ] == "settled"

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
    """Queued is not proof of a spawn, and the reason must outlive the owner."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _plan(repo, ("W0-a", "W0-b", "W0-c"), ("codex", "claude", "codex"))
    mission = f"---\ndispatch_plan: {plan.name}\ncuts: W0-a, W0-b, W0-c\n---\n"

    def _explode(*_args, **_kwargs):
        raise RuntimeError("provider bootstrap unavailable")

    monkeypatch.setattr(
        "vibecrafted_core.dispatch.supervisor.run_dispatch", _explode
    )
    fleet = record_write_stage_fleet(
        stage=_write_stage("implement"),
        cuts=mission_cuts(mission),
        parent_run_id="life-scheduler-dead",
        repo_root=repo,
        agent="codex",
    )
    dispatch_recorded_children(
        fleet,
        fleet_launch=dispatcher_fleet_launch(
            repo_root=repo, mission_text=mission, cell_launcher=None
        ),
    )
    run_id = stage_dispatch_run_id("life-scheduler-dead", "implement")
    assert join_stage_dispatch(run_id, timeout=10)

    # Simulate the launching process being gone: only the ledger remains.
    _FLEET_DISPATCH_ERRORS.clear()
    assert "provider bootstrap unavailable" in stage_dispatch_error(run_id)

    progress = fleet_obligations(
        "life-scheduler-dead",
        "implement",
        declared_cuts=("W0-a", "W0-b", "W0-c"),
        plan_path=str(plan),
    )
    assert progress["verdict"] == "failed"
    assert "provider bootstrap unavailable" in progress["scheduler_error"]
    assert {item["state"] for item in progress["cuts"]} == {"queued"}
    assert {item["obligation"] for item in progress["cuts"]} == {"failed"}


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
