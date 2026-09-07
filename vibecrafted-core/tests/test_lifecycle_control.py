from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from vibecrafted_core import ship
from vibecrafted_core.lifecycle_control import lifecycle_control_main
from vibecrafted_core.lifecycle_runner import (
    LifecycleRunner,
    LifecycleRunSpec,
    lifecycle_main,
)

from .lifecycle_schema_assertions import (
    assert_lifecycle_state_matches_packaged_schema,
)


def _fake_launcher(tmp_path: Path):
    def launcher(spec, _source_dir):
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    return launcher


def _fake_awaiter(payload):
    return {"completed": True, "artifact_ok": True, "report": payload["report"]}


def _make_lifecycle_run(
    tmp_path: Path,
    monkeypatch,
    *,
    workflow_id: str,
    await_stages: bool = False,
    prompt: str = "operator prompt",
) -> dict:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    runner = LifecycleRunner(launcher=_fake_launcher(tmp_path), awaiter=_fake_awaiter)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id=workflow_id,
                agent="codex",
                prompt=prompt,
                root=str(tmp_path),
                await_stages=await_stages,
            )
        )
    )
    assert_lifecycle_state_matches_packaged_schema(state)
    return state


def _reload_state(state: dict) -> dict:
    return json.loads(Path(state["state_path"]).read_text(encoding="utf-8"))


def _reload_contract_state(state: dict) -> dict:
    reloaded = _reload_state(state)
    assert_lifecycle_state_matches_packaged_schema(reloaded)
    return reloaded


def test_status_and_runs_surface_lifecycle_state(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-marbles")

    assert (
        lifecycle_control_main(
            ["status", state["run_id"], "--json"], workflow_id="vc-marbles"
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == state["run_id"]
    assert payload["workflow"] == "vc-marbles"
    assert payload["next_stage"] == "audit"
    assert payload["operator_actions"] == 0

    assert lifecycle_control_main(["runs", "--json"], workflow_id="vc-marbles") == 0
    listed = json.loads(capsys.readouterr().out)
    assert [entry["run_id"] for entry in listed] == [state["run_id"]]


def test_approve_launches_continuation_from_baton(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-ship")
    launched: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec) -> dict:
        launched.append(spec)
        return {"run_id": "life-cont-1", "status": "launching"}

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_control.run_lifecycle", fake_run_lifecycle
    )

    assert (
        lifecycle_control_main(
            ["approve", state["run_id"], "--json"], workflow_id="vc-ship"
        )
        == 0
    )
    assert len(launched) == 1
    spec = launched[0]
    assert spec.workflow_id == "vc-ship"
    assert spec.start_stage == "implement"
    assert spec.agent == "codex"
    assert spec.prompt == "operator prompt"
    assert spec.parent_run_id == state["run_id"]
    # The baton cargo: the scaffold report rides into the implement continuation.
    assert spec.previous_reports == (str(tmp_path / "scaffold.md"),)

    reloaded = _reload_contract_state(state)
    actions = reloaded["operator_actions"]
    assert [action["action"] for action in actions] == ["approve_transition"]
    assert actions[0]["details"]["continuation_run_id"] == "life-cont-1"
    report = Path(state["report_path"]).read_text(encoding="utf-8")
    assert "## Operator actions" in report
    assert "approve_transition" in report
    transcript = Path(state["transcript_path"]).read_text(encoding="utf-8")
    assert any(
        json.loads(line).get("kind") == "operator_action"
        for line in transcript.splitlines()
    )


def test_approve_gates_on_missing_baton_report_until_forced(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-ship")
    launched: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec) -> dict:
        launched.append(spec)
        return {"run_id": "life-cont-forced", "status": "launching"}

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_control.run_lifecycle", fake_run_lifecycle
    )
    # The worker has not finished writing: the baton path exists in state
    # but the file is gone (same truth as not-yet-written or truncated).
    scaffold_report = tmp_path / "scaffold.md"
    scaffold_report.unlink()

    assert (
        lifecycle_control_main(["approve", state["run_id"]], workflow_id="vc-ship") == 1
    )
    err = capsys.readouterr().err
    assert "baton cargo not ready" in err
    assert str(scaffold_report) in err
    assert "--force" in err
    assert launched == []

    # Empty file is equally not-ready.
    scaffold_report.write_text("", encoding="utf-8")
    assert (
        lifecycle_control_main(["approve", state["run_id"]], workflow_id="vc-ship") == 1
    )
    assert "baton cargo not ready" in capsys.readouterr().err

    # --force is the conscious override and must leave a trace.
    assert (
        lifecycle_control_main(
            ["approve", state["run_id"], "--force", "--json"], workflow_id="vc-ship"
        )
        == 0
    )
    assert len(launched) == 1
    reloaded = _reload_contract_state(state)
    details = reloaded["operator_actions"][0]["details"]
    assert details["forced_missing_reports"] == [str(scaffold_report)]


def test_approve_rejected_when_nothing_pending(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _make_lifecycle_run(
        tmp_path, monkeypatch, workflow_id="vc-implement", await_stages=True
    )
    assert state["baton"]["next_stage"] == ""

    assert (
        lifecycle_control_main(["approve", state["run_id"]], workflow_id="vc-implement")
        == 1
    )
    assert "nothing to approve" in capsys.readouterr().err


def test_interrupt_stops_stage_and_marks_state(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-marbles")
    stopped: list[str] = []

    def fake_stop_run(run_id: str, *, reason: str = "") -> dict:
        stopped.append(run_id)
        return {"accepted": True, "run_id": run_id, "reason": reason}

    monkeypatch.setattr("vibecrafted_core.workflow.stop_run", fake_stop_run)

    assert (
        lifecycle_control_main(
            ["interrupt", state["run_id"], "--json"], workflow_id="vc-marbles"
        )
        == 0
    )
    assert stopped == ["marbles-run"]
    reloaded = _reload_contract_state(state)
    assert reloaded["status"] == "interrupted"
    assert [action["action"] for action in reloaded["operator_actions"]] == [
        "interrupt_workflow"
    ]
    assert reloaded["operator_actions"][0]["details"]["stop_accepted"] is True


def test_control_verbs_validate_human_controls(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-hydrate")

    assert (
        lifecycle_control_main(
            ["force-audit", state["run_id"]], workflow_id="vc-hydrate"
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "force_audit" in err
    assert "human" in err

    assert (
        lifecycle_control_main(
            ["fallback", state["run_id"], "--stage", "hydrate"],
            workflow_id="vc-hydrate",
        )
        == 1
    )
    assert "choose_fallback_stage" in capsys.readouterr().err


def test_force_audit_steers_baton_when_manifest_has_audit_stage(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-ship")
    assert state["baton"]["next_stage"] == "implement"

    assert (
        lifecycle_control_main(
            ["force-audit", state["run_id"], "--json"], workflow_id="vc-ship"
        )
        == 0
    )
    reloaded = _reload_contract_state(state)
    assert reloaded["baton"]["next_stage"] == "audit"
    assert reloaded["baton"]["reason"] == "operator_forced_audit"
    details = reloaded["operator_actions"][0]["details"]
    assert details["mode"] == "steered_baton"
    assert details["displaced_next_stage"] == "implement"


def test_force_audit_dispatches_vc_audit_for_single_stage_manifest(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-implement")
    launched: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec) -> dict:
        launched.append(spec)
        return {"run_id": "life-audi-1", "status": "launching"}

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_control.run_lifecycle", fake_run_lifecycle
    )

    assert (
        lifecycle_control_main(
            ["force-audit", state["run_id"], "--json"], workflow_id="vc-implement"
        )
        == 0
    )
    assert len(launched) == 1
    assert launched[0].workflow_id == "vc-audit"
    assert launched[0].parent_run_id == state["run_id"]
    reloaded = _reload_contract_state(state)
    details = reloaded["operator_actions"][0]["details"]
    assert details["mode"] == "dispatched_vc_audit"
    assert details["continuation_run_id"] == "life-audi-1"


def test_fallback_validates_stage_and_steers_backwards(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-ship")

    assert (
        lifecycle_control_main(
            ["fallback", state["run_id"], "--stage", "bogus"], workflow_id="vc-ship"
        )
        == 1
    )
    assert "unknown stage 'bogus'" in capsys.readouterr().err

    assert (
        lifecycle_control_main(
            ["fallback", state["run_id"], "--stage", "polarize", "--json"],
            workflow_id="vc-ship",
        )
        == 0
    )
    reloaded = _reload_contract_state(state)
    assert reloaded["baton"]["next_stage"] == "polarize"
    assert reloaded["baton"]["reason"] == "operator_chose_fallback"


def test_accept_dou_records_finding(monkeypatch, tmp_path: Path, capsys) -> None:
    state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-dou")

    assert (
        lifecycle_control_main(
            [
                "accept-dou",
                state["run_id"],
                "--finding",
                "install path unverified — accepted for this release",
            ],
            workflow_id="vc-dou",
        )
        == 0
    )
    reloaded = _reload_contract_state(state)
    assert reloaded["accepted_dou_findings"][0]["finding"].startswith(
        "install path unverified"
    )
    assert [action["action"] for action in reloaded["operator_actions"]] == [
        "accept_dou"
    ]
    report = Path(state["report_path"]).read_text(encoding="utf-8")
    assert "accept_dou" in report
    assert "- accepted_dou_findings: 1" in report

    # The accepted-gap counter pairs with the reported dou_index in status.
    capsys.readouterr()  # drop the accept-dou print before parsing status JSON
    assert (
        lifecycle_control_main(
            ["status", state["run_id"], "--json"], workflow_id="vc-dou"
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted_dou"] == 1
    assert payload["dou_index"] is None


def test_ship_and_wrapper_clis_route_control_verbs(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    dou_state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-dou")
    ship_state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-ship")

    assert ship.main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == ship_state["run_id"]
    assert payload["workflow"] == "vc-ship"

    assert lifecycle_main("vc-dou", ["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == dou_state["run_id"]
    assert payload["workflow"] == "vc-dou"

    assert lifecycle_main("vc-dou", ["runs", "--all", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {entry["workflow"] for entry in listed} == {"vc-dou", "vc-ship"}


def test_approve_honors_operator_stage_casting(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-ship")
    state_file = Path(state["state_path"])
    payload = json.loads(state_file.read_text(encoding="utf-8"))
    payload["spec"]["stage_agents"] = {"implement": "junie"}
    payload["spec"]["stage_models"] = {"implement": "gpt-5.5"}
    state_file.write_text(json.dumps(payload), encoding="utf-8")

    launched: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec) -> dict:
        launched.append(spec)
        return {"run_id": "life-cast-1", "status": "launching"}

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_control.run_lifecycle", fake_run_lifecycle
    )

    assert (
        lifecycle_control_main(
            ["approve", state["run_id"], "--json"], workflow_id="vc-ship"
        )
        == 0
    )
    # The operator's A-to-Z casting wins over the baton's next_agent for a
    # stage it names, and the map rides into the continuation spec.
    assert launched[0].agent == "junie"
    assert launched[0].stage_agents == {"implement": "junie"}
    assert launched[0].stage_models == {"implement": "gpt-5.5"}
    capsys.readouterr()


def test_await_stage_reports_delivery_and_death(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _make_lifecycle_run(tmp_path, monkeypatch, workflow_id="vc-ship")

    awaited: list[str] = []
    forwarded_report_paths: list[str] = []

    def fake_await_run(run_id: str, **kwargs) -> dict:
        awaited.append(run_id)
        forwarded_report_paths.append(str(kwargs.get("report_path") or ""))
        return {
            "completed": True,
            "timed_out": False,
            "reason": "terminal",
            "worker_alive": False,
        }

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_control.control_plane_await_run", fake_await_run
    )

    # The seeded run's scaffold report exists -> a dead worker with a written
    # report is the normal no-await handoff, not a death signal.
    assert (
        lifecycle_control_main(
            ["await", state["run_id"], "--idle", "1", "--json"],
            workflow_id="vc-ship",
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert awaited  # went through the runtime contract, not a sleep loop
    # The stage report is the handoff: forwarding it lets await_run return
    # `report_delivered` on the first poll instead of idling on the corpse.
    assert forwarded_report_paths[0] == payload["report"]
    assert payload["stage"] == "scaffold"
    assert payload["report_written"] is True
    assert payload["worker_dead_without_report"] is False
    assert payload["next_stage"] == "implement"

    # Erase the report: same terminal await now means death-without-delivery.
    Path(payload["report"]).unlink()
    assert (
        lifecycle_control_main(
            ["await", state["run_id"], "--idle", "1", "--json"],
            workflow_id="vc-ship",
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_written"] is False
    assert payload["worker_dead_without_report"] is True


# --------------------------------------------------------------------------
# Fleet obligations as an operator gate.
#
# The dispatcher owns the cuts a WRITE stage launched; the stage worker's own
# report says nothing about them.  These tests drive the REAL control verbs
# over a REAL dispatch receipt ledger — only the provider transport is faked,
# at the lowest boundary (CellLauncher).
# --------------------------------------------------------------------------

from vibecrafted_core.dispatch.receipts import DispatchReceiptStore  # noqa: E402
from vibecrafted_core.lifecycle_control import (  # noqa: E402
    approve_transition,
    await_stage,
    interrupt_workflow,
)
from vibecrafted_core.lifecycle_fleet import (  # noqa: E402
    dispatch_recorded_children,
    dispatcher_fleet_launch,
    mission_cuts,
    record_write_stage_fleet,
    stage_dispatch_run_id,
)

from .test_lifecycle_fleet import (  # noqa: E402
    _plan,
    _report_writer,
    _seed_repo,
    _write_stage,
)

_FLEET_PARENT = "life-fleet-gate"
_FLEET_STAGE = "implement"


def _settled_fleet(tmp_path: Path, monkeypatch) -> tuple[Path, Path, list[str]]:
    """Run one real three-cut fleet to settlement; return repo, plan, launches."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    repo = tmp_path / "repo"
    _seed_repo(repo)
    plan = _plan(repo, ("W0-a", "W0-b", "W0-c"), ("codex", "claude", "codex"))
    mission = f"---\ndispatch_plan: {plan.name}\ncuts: W0-a, W0-b, W0-c\n---\n"
    launches: list[str] = []
    fleet = record_write_stage_fleet(
        stage=_write_stage(_FLEET_STAGE),
        cuts=mission_cuts(mission),
        parent_run_id=_FLEET_PARENT,
        repo_root=repo,
        agent="codex",
    )
    dispatch_recorded_children(
        fleet,
        fleet_launch=dispatcher_fleet_launch(
            repo_root=repo,
            mission_text=mission,
            cell_launcher=_report_writer(launches),
            wait=True,
        ),
    )
    return repo, plan, launches


def _fleet_state(tmp_path: Path, plan: Path) -> tuple[Path, dict]:
    """A lifecycle state whose WRITE stage owns the settled fleet above.

    The baton cargo is deliberately present and non-empty: the ONLY thing that
    may hold this run back is the fleet itself.
    """
    report = tmp_path / "stage-report.md"
    report.write_text("stage worker delivered\n", encoding="utf-8")
    state_path = tmp_path / "state.json"
    state = {
        "schema": "vibecrafted.lifecycle.v1",
        "run_id": _FLEET_PARENT,
        "workflow": "vc-ship",
        "status": "launching",
        "root": str(tmp_path),
        "state_path": str(state_path),
        "report_path": str(tmp_path / "run-report.md"),
        "human_controls": ["approve_transition", "interrupt_workflow"],
        "spec": {"prompt": "mission", "runtime": "headless"},
        "baton": {
            "next_stage": "review",
            "next_agent": "codex",
            "previous_reports": [str(report)],
        },
        "stages": [
            {
                "id": _FLEET_STAGE,
                "launch": {"run_id": "stage-worker-run", "report": str(report)},
                "fleet": {
                    "parent_run_id": _FLEET_PARENT,
                    "stage_id": _FLEET_STAGE,
                    "cuts": ["W0-a", "W0-b", "W0-c"],
                    "children": [],
                    "plan_path": str(plan),
                },
            }
        ],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state_path, state


def _receipt_store(repo: Path, plan: Path) -> DispatchReceiptStore:
    """The dispatcher's own ledger API — never a hand-edited state file."""
    from vibecrafted_core.dispatch.doctor import diagnose_file

    report = diagnose_file(plan)
    assert report.dispatch is not None
    return DispatchReceiptStore(
        stage_dispatch_run_id(_FLEET_PARENT, _FLEET_STAGE),
        report.dispatch.cuts,
        repo_root=str(repo),
        create=False,
    )


def test_approve_advances_when_the_whole_fleet_is_genuinely_settled(
    tmp_path: Path, monkeypatch
) -> None:
    repo, plan, launches = _settled_fleet(tmp_path, monkeypatch)
    assert sorted(launches) == ["W0-a", "W0-b", "W0-c"]
    state_path, state = _fleet_state(tmp_path, plan)

    launched: list[str] = []

    def _continuation(spec):
        launched.append(spec.start_stage)
        return {"run_id": "continuation-run"}

    child = approve_transition(state_path, state, run_lifecycle_fn=_continuation)

    assert child["run_id"] == "continuation-run"
    assert launched == ["review"]
    action = state["operator_actions"][-1]
    assert action["details"]["fleet_verdict"] == "complete"


def test_approve_refuses_while_the_fleet_is_active_or_failed(
    tmp_path: Path, monkeypatch
) -> None:
    """The recorded counterexample, inverted into a gate.

    Two cuts still active, one failed, and a perfectly good stage worker
    report: approve must not advance the lifecycle.
    """
    repo, plan, _ = _settled_fleet(tmp_path, monkeypatch)
    store = _receipt_store(repo, plan)
    store.update("W0-a", "active")
    store.update("W0-b", "active")
    store.update("W0-c", "failed", acceptance="failed")

    state_path, state = _fleet_state(tmp_path, plan)
    launched: list[str] = []

    with pytest.raises(ValueError) as excinfo:
        approve_transition(
            state_path,
            state,
            run_lifecycle_fn=lambda spec: launched.append(spec.start_stage) or {},
        )

    message = str(excinfo.value)
    assert "fleet obligations not settled" in message
    assert "W0-a=active" in message and "W0-c=failed" in message
    # The refusal must name the existing recovery verb, not a new one.
    assert f"--resume {stage_dispatch_run_id(_FLEET_PARENT, _FLEET_STAGE)}" in message
    assert launched == [], "no continuation may start over an open fleet"


def test_approve_refuses_when_a_declared_cut_has_no_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    """An obligation nobody can account for is not an obligation that is met."""
    _repo, plan, _ = _settled_fleet(tmp_path, monkeypatch)
    state_path, state = _fleet_state(tmp_path, plan)
    state["stages"][0]["fleet"]["cuts"].append("W0-ghost")

    with pytest.raises(ValueError, match="W0-ghost=missing"):
        approve_transition(state_path, state, run_lifecycle_fn=lambda spec: {})


def test_forced_approve_records_exactly_which_obligations_it_stepped_over(
    tmp_path: Path, monkeypatch
) -> None:
    repo, plan, _ = _settled_fleet(tmp_path, monkeypatch)
    _receipt_store(repo, plan).update("W0-b", "failed", acceptance="failed")
    state_path, state = _fleet_state(tmp_path, plan)

    approve_transition(
        state_path,
        state,
        run_lifecycle_fn=lambda spec: {"run_id": "forced"},
        force=True,
    )

    details = state["operator_actions"][-1]["details"]
    assert details["forced_open_fleet"] == ["W0-b=failed(failed)"]


def test_await_does_not_call_a_stage_complete_while_its_fleet_still_owes_work(
    tmp_path: Path, monkeypatch
) -> None:
    repo, plan, _ = _settled_fleet(tmp_path, monkeypatch)
    _receipt_store(repo, plan).update("W0-a", "active")
    _state_path, state = _fleet_state(tmp_path, plan)

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_control.control_plane_await_run",
        lambda *_a, **_k: {"completed": True, "worker_alive": False, "reason": "ok"},
    )
    payload = await_stage(state, idle_seconds=0.05, interval_seconds=0.01)

    assert payload["stage_worker_completed"] is True
    assert payload["completed"] is False
    assert payload["reason"] == "fleet_active"
    assert payload["fleet"]["blocking"] == ["W0-a=active(active)"]


def test_await_hard_cap_covers_stage_and_fleet_wait_together(
    tmp_path: Path, monkeypatch
) -> None:
    repo, plan, _ = _settled_fleet(tmp_path, monkeypatch)
    _receipt_store(repo, plan).update("W0-a", "active")
    _state_path, state = _fleet_state(tmp_path, plan)

    def stage_wait(*_args, **_kwargs):
        time.sleep(0.03)
        return {"completed": True, "worker_alive": False, "reason": "ok"}

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_control.control_plane_await_run", stage_wait
    )
    started = time.monotonic()
    payload = await_stage(
        state, idle_seconds=1, interval_seconds=0.01, hard_cap_seconds=0.02
    )
    assert time.monotonic() - started < 0.05
    assert payload["completed"] is False
    assert payload["timed_out"] is True


def test_await_reports_complete_once_the_ledger_says_the_fleet_settled(
    tmp_path: Path, monkeypatch
) -> None:
    _repo, plan, _ = _settled_fleet(tmp_path, monkeypatch)
    _state_path, state = _fleet_state(tmp_path, plan)

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_control.control_plane_await_run",
        lambda *_a, **_k: {"completed": True, "worker_alive": False, "reason": "ok"},
    )
    payload = await_stage(state, idle_seconds=0.05, interval_seconds=0.01)

    assert payload["completed"] is True
    assert payload["fleet"]["verdict"] == "complete"


@pytest.mark.parametrize("acceptance", ["", "unknown", "failed"])
def test_settled_receipt_requires_verified_acceptance(
    tmp_path: Path, monkeypatch, acceptance: str
) -> None:
    repo, plan, _ = _settled_fleet(tmp_path, monkeypatch)
    _receipt_store(repo, plan).update("W0-a", "settled", acceptance=acceptance)
    state_path, state = _fleet_state(tmp_path, plan)
    with pytest.raises(ValueError, match="W0-a=unknown\\(settled\\)"):
        approve_transition(state_path, state, run_lifecycle_fn=lambda spec: {})

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_control.control_plane_await_run",
        lambda *_a, **_k: {"completed": True, "worker_alive": False, "reason": "ok"},
    )
    payload = await_stage(state, idle_seconds=0.05, interval_seconds=0.01)
    assert payload["completed"] is False
    assert payload["fleet"]["blocking"] == ["W0-a=unknown(settled)"]


def test_interrupt_stops_live_cuts_by_their_recorded_provider_identity(
    tmp_path: Path, monkeypatch
) -> None:
    repo, plan, _ = _settled_fleet(tmp_path, monkeypatch)
    store = _receipt_store(repo, plan)
    store.update("W0-a", "active", provider_run_id="provider-W0-a")
    store.update("W0-c", "failed", acceptance="failed")
    state_path, state = _fleet_state(tmp_path, plan)

    stopped: list[str] = []

    def _stop(run_id, reason=""):
        stopped.append(run_id)
        return {"accepted": True, "reason": reason}

    result = interrupt_workflow(state_path, state, stop_run_fn=_stop)

    # The live cut is stopped by the identity the dispatcher recorded; the
    # settled and failed siblings are left alone.
    assert stopped == ["stage-worker-run", "provider-W0-a"]
    assert [item["cut_id"] for item in result["fleet_stops"]] == ["W0-a"]
    assert result["scheduler_stop"]["accepted"] is True
    assert _receipt_store(repo, plan).read()["scheduler_stop_requested"] is True
    assert state["status"] == "interrupted"
