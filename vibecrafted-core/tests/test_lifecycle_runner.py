from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core import control_plane, ship, wrappers
from vibecrafted_core.lifecycle_delivery import claim_digest_for_text
from vibecrafted_core.lifecycle_fleet import (
    CutDispatchContract,
    live_vc_dispatch_permitted,
    load_cut_records,
)
from vibecrafted_core.lifecycle_runner import (
    LIFECYCLE_SCHEMA_ID,
    LifecycleRunner,
    LifecycleRunSpec,
    LifecycleSupervisor,
    _lifecycle_stage_run_id,
    record_stage_worker_completion,
)
from vibecrafted_core.workflows.model import WorkflowManifest, WorkflowStage

from .lifecycle_schema_assertions import (
    assert_lifecycle_state_matches_packaged_schema,
    assert_worker_report_frontmatter_matches_packaged_schema,
    packaged_lifecycle_schema,
)


def _init_read_stage_repo(root: Path) -> Path:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    tracked = root / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=VC Test",
            "-c",
            "user.email=vc@example.test",
            "commit",
            "-m",
            "initial",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return tracked


def _write_read_stage_report(path: Path, *, run_id: str, code_mutation: str) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f"run_id: {run_id}",
                "agent: codex",
                "skill: dou",
                "status: completed",
                "claim_status: completed",
                f"code_mutation: {code_mutation}",
                "---",
                "",
                "READ stage evidence.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_lifecycle_runner_honors_reserved_parent_run_id(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )

    def fake_launcher(spec, _source_dir):
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": "child-implement",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    runner = LifecycleRunner(
        launcher=fake_launcher,
        awaiter=lambda payload: {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        },
    )
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-implement",
                agent="codex",
                run_id="parent-session-123",
                prompt="reserved identity",
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    assert state["run_id"] == "parent-session-123"
    assert Path(state["state_path"]).parent.name == "parent-session-123"
    assert state["stages"][0]["launch"]["run_id"] == "child-implement"


def test_lifecycle_stage_identity_is_stable_per_attempt_not_content() -> None:
    first = _lifecycle_stage_run_id("parent-1", "implement", 0)

    assert first == _lifecycle_stage_run_id("parent-1", "implement", 0)
    assert first != _lifecycle_stage_run_id("parent-2", "implement", 0)
    assert first != _lifecycle_stage_run_id("parent-1", "implement", 1)


def test_lifecycle_runner_preserves_terminal_stage_failure(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )

    def fake_launcher(spec, _source_dir):
        return {
            "accepted": True,
            "run_id": "failed-dou",
            "report": str(tmp_path / f"{spec.skill}.md"),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    runner = LifecycleRunner(
        launcher=fake_launcher,
        awaiter=lambda payload: {
            "completed": False,
            "artifact_ok": False,
            "exit_code": 23,
            "execution_state": "failed",
            "report": payload["report"],
        },
    )
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-dou",
                agent="codex",
                prompt="prove terminal failure truth",
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    assert state["status"] == "failed"
    assert state["execution_state"] == "failed"
    assert state["proof_state"] == "undeclared"
    assert state["delivery_state"] == "unverified"
    assert state["stages"][0]["status"] == "failed"
    persisted = json.loads(Path(state["state_path"]).read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["execution_state"] == "failed"


def test_lifecycle_runner_triggers_audit_after_marbles(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    calls: list[str] = []
    loop_options: list[tuple[int | None, int | None]] = []

    def fake_launcher(spec, _source_dir):
        calls.append(spec.skill)
        if spec.skill == "marbles":
            loop_options.append((spec.count, spec.depth))
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    def fake_awaiter(payload):
        return {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        }

    runner = LifecycleRunner(launcher=fake_launcher, awaiter=fake_awaiter)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-marbles",
                agent="codex",
                prompt="close the gaps",
                root=str(tmp_path),
                await_stages=True,
                count=2,
                depth=4,
            )
        )
    )

    assert calls == ["marbles", "audit"]
    assert state["schema"] == LIFECYCLE_SCHEMA_ID
    assert loop_options == [(2, 4)]
    assert state["status"] == "completed"
    assert (
        state["supervisor"] == "vibecrafted_core.lifecycle_runner.LifecycleSupervisor"
    )
    assert state["human_controls"] == ["interrupt_workflow", "force_audit"]
    assert state["baton"]["from_stage"] == "audit"
    assert state["baton"]["next_stage"] == ""
    assert [stage["phase"] for stage in state["stages"]] == ["write", "read"]
    assert "changed_files_reported" in state["stages"][0]["transition_conditions"]
    assert "code" in state["stages"][0]["allowed_artifacts"]
    assert "no_code_mutation" in state["stages"][1]["transition_conditions"]
    assert Path(state["state_path"]).is_file()
    report = Path(state["report_path"]).read_text(encoding="utf-8")
    assert report.startswith("# Lifecycle run")
    assert "## Baton" in report
    assert "transition_conditions:" in report
    assert (
        len(Path(state["transcript_path"]).read_text(encoding="utf-8").splitlines())
        == 2
    )


def test_lifecycle_runner_propagates_polarize_loop_options(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    loop_options: list[tuple[str, int | None, int | None]] = []

    def fake_launcher(spec, _source_dir):
        loop_options.append((spec.skill, spec.count, spec.depth))
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    runner = LifecycleRunner(
        launcher=fake_launcher,
        awaiter=lambda payload: {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        },
    )
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-polarize",
                agent="codex",
                prompt="cut excess",
                root=str(tmp_path),
                await_stages=True,
                count=2,
                depth=4,
            )
        )
    )

    assert loop_options == [("polarize", 2, 4)]
    assert state["status"] == "completed"


def test_lifecycle_runner_stamps_packaged_schema_contract(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )

    def fake_launcher(spec, _source_dir):
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    def fake_awaiter(payload):
        return {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        }

    runner = LifecycleRunner(launcher=fake_launcher, awaiter=fake_awaiter)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-dou",
                agent="codex",
                prompt="validate contract",
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )
    written_state = json.loads(Path(state["state_path"]).read_text(encoding="utf-8"))
    schema = packaged_lifecycle_schema()

    assert written_state["schema"] == LIFECYCLE_SCHEMA_ID
    assert LifecycleSupervisor().status(written_state)["schema"] == LIFECYCLE_SCHEMA_ID
    assert_lifecycle_state_matches_packaged_schema(written_state)
    frontmatter = schema["$defs"]["worker_report_frontmatter"]["properties"]
    assert set(frontmatter) == {"next_stage", "next_agent", "dou_index", "status"}
    assert_worker_report_frontmatter_matches_packaged_schema(
        {
            "next_stage": "audit",
            "next_agent": "codex",
            "dou_index": 0,
            "status": "completed",
        }
    )
    assert_worker_report_frontmatter_matches_packaged_schema({"dou_index": "0"})

    wrong_action_shape = json.loads(json.dumps(written_state))
    wrong_action_shape["operator_actions"] = {}
    with pytest.raises(AssertionError, match="operator_actions"):
        assert_lifecycle_state_matches_packaged_schema(wrong_action_shape)

    wrong_stage_phase = json.loads(json.dumps(written_state))
    wrong_stage_phase["stages"][0]["phase"] = "execute"
    with pytest.raises(AssertionError, match="phase"):
        assert_lifecycle_state_matches_packaged_schema(wrong_stage_phase)

    missing_baton_cargo = json.loads(json.dumps(written_state))
    del missing_baton_cargo["baton"]["previous_reports"]
    with pytest.raises(AssertionError, match="previous_reports"):
        assert_lifecycle_state_matches_packaged_schema(missing_baton_cargo)

    with pytest.raises(AssertionError, match="dou_index"):
        assert_worker_report_frontmatter_matches_packaged_schema({"dou_index": []})


def test_lifecycle_runner_honours_worker_requested_next_stage(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    calls: list[str] = []

    def fake_launcher(spec, _source_dir):
        calls.append(spec.skill)
        report = tmp_path / f"{spec.skill}-{len(calls)}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run-{len(calls)}",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    steering = iter(["marbles"])

    def fake_awaiter(payload):
        result = {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        }
        if payload["run_id"].startswith("audit"):
            result["next_stage"] = next(steering, "")
        return result

    runner = LifecycleRunner(launcher=fake_launcher, awaiter=fake_awaiter)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-marbles",
                agent="codex",
                prompt="steer back once",
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    assert calls == ["marbles", "audit", "marbles", "audit"]
    assert state["status"] == "completed"
    steered = state["stages"][1]["transition"]
    assert steered["requested_next_stage"] == "marbles"
    assert steered["next_stage"] == "marbles"
    assert state["stages"][3]["transition"]["next_stage"] == ""


def test_lifecycle_runner_hands_baton_to_worker_requested_next_agent(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    agents: list[str] = []

    def fake_launcher(spec, _source_dir):
        agents.append(spec.agent)
        report = tmp_path / f"{spec.skill}-{len(agents)}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run-{len(agents)}",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    def fake_awaiter(payload):
        result = {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        }
        if payload["run_id"] == "marbles-run-1":
            # Marbles hands the baton to junie for the rest of the cycle.
            result["next_agent"] = "junie"
        if payload["run_id"] == "audit-run-2":
            # Steer back once; the unknown agent must NOT steal the baton.
            result["next_stage"] = "marbles"
            result["next_agent"] = "chatgpt"
        return result

    runner = LifecycleRunner(launcher=fake_launcher, awaiter=fake_awaiter)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-marbles",
                agent="codex",
                prompt="relay the baton",
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    assert agents == ["codex", "junie", "junie", "junie"]
    assert state["status"] == "completed"
    assert [stage["agent"] for stage in state["stages"]] == agents
    handoff = state["stages"][0]["transition"]
    assert handoff["requested_next_agent"] == "junie"
    assert handoff["next_agent"] == "junie"
    ignored = state["stages"][1]["transition"]
    assert ignored["requested_next_agent"] == "chatgpt"
    assert ignored["next_agent"] == "junie"
    assert state["baton"]["next_agent"] == "junie"
    report = Path(state["report_path"]).read_text(encoding="utf-8")
    assert "- next_agent: junie" in report


def test_lifecycle_runner_stage_agent_pin_overrides_baton_holder(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    manifest = WorkflowManifest(
        id="vc-pinned",
        name="Pinned",
        description="Registry-pinned per-stage agent.",
        stages=(
            WorkflowStage(
                id="review",
                workflow="review",
                phase="read",
                order=1,
                next_stage="implement",
            ),
            WorkflowStage(
                id="implement",
                workflow="implement",
                phase="write",
                order=2,
                agent="gemini",
                model="worker-tier",
            ),
        ),
        entry_stage="review",
    )
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.workflow_manifest",
        lambda _id: manifest,
    )
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.workflow_manifest_payload",
        lambda _id: {"id": manifest.id},
    )
    agents: list[str] = []
    models: list[str] = []

    def fake_launcher(spec, _source_dir):
        agents.append(spec.agent)
        models.append(spec.model)
        report = tmp_path / f"{spec.skill}-{len(agents)}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run-{len(agents)}",
            "report": str(report),
        }

    def fake_awaiter(payload):
        return {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        }

    runner = LifecycleRunner(launcher=fake_launcher, awaiter=fake_awaiter)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-pinned",
                agent="codex",
                prompt="---\nstage_models:\n  review: frontier\n---\npin the writer",
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    assert agents == ["codex", "gemini"]
    assert models == ["frontier", "worker-tier"]
    assert state["status"] == "completed"
    # The pin runs its own stage only; the baton holder stays with the launch agent.
    assert state["baton"]["next_agent"] == "codex"


def test_lifecycle_runner_stage_cap_stops_runaway_steering(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setenv("VIBECRAFTED_LIFECYCLE_MAX_STAGES", "5")
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    calls: list[str] = []

    def fake_launcher(spec, _source_dir):
        calls.append(spec.skill)
        report = tmp_path / f"{spec.skill}-{len(calls)}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run-{len(calls)}",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    def fake_awaiter(payload):
        return {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
            "next_stage": "marbles",
        }

    runner = LifecycleRunner(launcher=fake_launcher, awaiter=fake_awaiter)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-marbles",
                agent="codex",
                prompt="steer forever",
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    assert state["status"] == "failed"
    assert "stage cap reached" in state["error"]
    assert len(state["stages"]) == 5
    assert len(calls) == 5


def test_lifecycle_runner_records_first_stage_without_await(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )

    def fake_launcher(spec, _source_dir):
        return {
            "accepted": True,
            "run_id": "dou-run",
            "skill": spec.skill,
            "report": str(tmp_path / "dou.md"),
        }

    runner = LifecycleRunner(launcher=fake_launcher)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-dou",
                agent="codex",
                prompt="audit readiness",
                root=str(tmp_path),
            )
        )
    )

    assert state["status"] == "launching"
    assert state["next_stage"] == ""
    assert state["baton"]["reason"] == "stage_launched_without_await"
    # The launched stage's report is the baton cargo an approve must carry on.
    assert state["baton"]["previous_reports"] == [str(tmp_path / "dou.md")]
    assert state["stages"][0]["workflow"] == "dou"
    assert state["stages"][0]["can_modify_code"] is False


def test_lifecycle_runner_seeds_previous_reports_from_spec(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    prompts: list[str] = []

    def fake_launcher(spec, _source_dir):
        prompts.append(spec.prompt)
        return {
            "accepted": True,
            "run_id": "implement-run",
            "report": str(tmp_path / "implement.md"),
        }

    inherited = str(tmp_path / "scaffold.md")
    runner = LifecycleRunner(launcher=fake_launcher)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-implement",
                agent="codex",
                prompt="build it",
                root=str(tmp_path),
                previous_reports=(inherited, "  ", ""),
            )
        )
    )

    assert inherited in prompts[0]
    # The dou_index contract is a DoU-stage concern; implement must not see it.
    assert "dou_index: <int>" not in prompts[0]
    assert state["spec"]["previous_reports"] == [inherited]
    assert state["baton"]["previous_reports"] == [
        inherited,
        str(tmp_path / "implement.md"),
    ]


def test_lifecycle_runner_injects_context_atlas_into_stage_prompt(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {
            "ok": True,
            "command": ["loct", "context"],
            "stdout": "Context Atlas says: runtime owner is lifecycle_runner.py",
        },
    )
    prompts: list[str] = []

    def fake_launcher(spec, _source_dir):
        prompts.append(spec.prompt)
        return {
            "accepted": True,
            "run_id": "dou-run",
            "report": str(tmp_path / "dou.md"),
        }

    runner = LifecycleRunner(launcher=fake_launcher)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-dou",
                agent="codex",
                prompt="audit readiness",
                root=str(tmp_path),
            )
        )
    )

    assert state["context_atlas"]["ok"] is True
    assert "Context Atlas says: runtime owner" in prompts[0]
    assert (
        "Transition conditions: launch_accepted, stage_completed, no_code_mutation"
        in prompts[0]
    )
    assert "Allowed artifacts: reports, cache, run_state, transcripts" in prompts[0]
    assert "code_mutation: false" in prompts[0]
    assert "code_mutation: true" in prompts[0]
    digest = claim_digest_for_text("audit readiness")
    assert f"mission claim digest: {digest}" in prompts[0]
    assert f"claim_digest: {digest}" in prompts[0]
    assert "finalized: false" in prompts[0]
    assert "finalized: true" in prompts[0]
    assert "claim: <what succeeded>" in prompts[0]
    assert "Human controls: accept_dou, force_audit, interrupt_workflow" in prompts[0]
    assert "next_stage: <stage-id>" in prompts[0]
    assert "next_agent: <agent-id>" in prompts[0]
    # DoU stages carry the index contract; other stages must not (see below).
    assert "dou_index: <int>" in prompts[0]
    assert "ZERO DoU index" in prompts[0]


def test_lifecycle_runner_validated_report_reaches_finalized_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=VC Test",
            "-c",
            "user.email=vc@example.test",
            "commit",
            "-m",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    mission = "prove one lifecycle stage automatically"
    digest = claim_digest_for_text(mission)
    stage_run_id = "implement-proof-run"
    runtime_dir = home / "control_plane" / "runtime_runs" / stage_run_id
    artifact_dir = home / "artifacts" / "tests" / stage_run_id

    def fake_launcher(spec, _source_dir):
        runtime_dir.mkdir(parents=True)
        artifact_dir.mkdir(parents=True)
        report = artifact_dir / "report.md"
        report.write_text(
            "\n".join(
                [
                    "---",
                    f"run_id: {stage_run_id}",
                    "agent: codex",
                    "skill: implement",
                    "status: completed",
                    "claim_status: completed",
                    f"claim_digest: {digest}",
                    "---",
                    "",
                    "Validated stage output.",
                ]
            ),
            encoding="utf-8",
        )
        transcript = artifact_dir / "transcript.log"
        transcript.write_text("stage completed\n", encoding="utf-8")
        meta = artifact_dir / "stage.meta.json"
        meta.write_text(
            json.dumps(
                {
                    "run_id": stage_run_id,
                    "agent": "codex",
                    "skill_code": "impl",
                    "status": "report_validated",
                    "state": "report_validated",
                    "root": str(tmp_path),
                    "prompt": mission,
                    "report": str(report),
                    "transcript": str(transcript),
                    "exit_code": 0,
                    "liveness": "terminal",
                    "updated_at": "2026-07-23T12:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        (runtime_dir / "meta.json").write_text(meta.read_text(), encoding="utf-8")
        return {
            "accepted": True,
            "run_id": stage_run_id,
            "report": str(report),
            "transcript": str(transcript),
            "meta": str(meta),
        }

    def fake_awaiter(payload):
        return {
            "completed": True,
            "artifact_ok": True,
            "run_id": stage_run_id,
            "report": payload["report"],
            "transcript": payload["transcript"],
            "meta": payload["meta"],
            "run": {"exit_code": 0},
        }

    state = asyncio.run(
        LifecycleRunner(launcher=fake_launcher, awaiter=fake_awaiter).run(
            LifecycleRunSpec(
                workflow_id="vc-implement",
                agent="codex",
                prompt=mission,
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    assert state["proof_state"] == "passed"
    assert state["delivery_state"] == "sealed"
    assert state["stages"][0]["lifecycle_seal"]["granted"] is True
    control_plane.sync_state(stage_run_id)
    snapshot = json.loads(
        (control_plane.run_snapshot_dir() / f"{stage_run_id}.json").read_text()
    )
    assert snapshot["proof_state"] == "passed"
    assert snapshot["delivery_state"] == "sealed"
    assert snapshot["settlement_verdict"] == "finalized"
    assert snapshot["settlement_tui"] == "f"


def test_read_stage_detects_mutation_to_preexisting_dirty_file(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = _init_read_stage_repo(repo)
    tracked.write_text("base\ndirty before read\n", encoding="utf-8")
    report = tmp_path / "dou.md"

    def fake_launcher(_spec, _source_dir):
        with tracked.open("a", encoding="utf-8") as handle:
            handle.write("mutated by read stage\n")
        _write_read_stage_report(report, run_id="dou-run", code_mutation="true")
        return {
            "accepted": True,
            "run_id": "dou-run",
            "report": str(report),
        }

    def fake_awaiter(payload):
        return {"completed": True, "artifact_ok": True, "report": payload["report"]}

    runner = LifecycleRunner(launcher=fake_launcher, awaiter=fake_awaiter)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-dou",
                agent="codex",
                prompt="audit readiness",
                root=str(repo),
                await_stages=True,
            )
        )
    )

    assert state["status"] == "failed"
    assert state["stages"][0]["read_phase_violation"] is True
    assert state["stages"][0]["changed_files"] == ["tracked.txt"]
    assert state["stages"][0]["read_phase_evidence"] == {
        "repo_delta": "observed",
        "observed_paths": ["tracked.txt"],
        "worker_claim": "mutated",
        "declared_value": "true",
        "attribution": "worker_self_attested",
        "hard_violation": True,
    }


@pytest.mark.parametrize(
    (
        "code_mutation",
        "concurrent_delta",
        "expected_status",
        "worker_claim",
        "attribution",
        "hard_violation",
    ),
    (
        (
            "false",
            True,
            "completed",
            "no_mutation",
            "unattributed_living_tree",
            False,
        ),
        (
            "",
            True,
            "completed",
            "undeclared",
            "unattributed_living_tree",
            False,
        ),
        ("true", False, "failed", "mutated", "worker_self_attested", True),
        ("perhaps", False, "failed", "invalid", "none_observed", True),
    ),
)
def test_read_stage_attribution_uses_worker_claim_not_global_delta(
    monkeypatch,
    tmp_path: Path,
    code_mutation: str,
    concurrent_delta: bool,
    expected_status: str,
    worker_claim: str,
    attribution: str,
    hard_violation: bool,
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner._maybe_seal_awaited_stage",
        lambda **_kwargs: None,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = _init_read_stage_repo(repo)
    report = tmp_path / "dou.md"

    def fake_launcher(_spec, _source_dir):
        _write_read_stage_report(
            report, run_id="dou-attribution", code_mutation=code_mutation
        )
        return {
            "accepted": True,
            "run_id": "dou-attribution",
            "report": str(report),
        }

    def fake_awaiter(payload):
        if concurrent_delta:
            tracked.write_text("base\nconcurrent actor\n", encoding="utf-8")
        return {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        }

    state = asyncio.run(
        LifecycleRunner(launcher=fake_launcher, awaiter=fake_awaiter).run(
            LifecycleRunSpec(
                workflow_id="vc-dou",
                agent="codex",
                prompt="audit living tree attribution",
                root=str(repo),
                await_stages=True,
            )
        )
    )

    stage = state["stages"][0]
    assert state["status"] == expected_status
    assert stage["changed_files"] == (["tracked.txt"] if concurrent_delta else [])
    assert stage["attributed_changed_files"] == []
    assert stage["read_phase_violation"] is hard_violation
    assert stage["read_phase_evidence"] == {
        "repo_delta": "observed" if concurrent_delta else "none",
        "observed_paths": ["tracked.txt"] if concurrent_delta else [],
        "worker_claim": worker_claim,
        "declared_value": code_mutation,
        "attribution": attribution,
        "hard_violation": hard_violation,
    }


@pytest.mark.parametrize(
    ("code_mutation", "concurrent_delta", "expected_status", "attribution"),
    (
        ("false", True, "launching", "unattributed_living_tree"),
        ("true", False, "failed", "worker_self_attested"),
    ),
)
def test_default_no_await_read_stage_uses_worker_claim_for_attribution(
    monkeypatch,
    tmp_path: Path,
    code_mutation: str,
    concurrent_delta: bool,
    expected_status: str,
    attribution: str,
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner._maybe_seal_awaited_stage",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "vibecrafted_core.control_plane.sync_state",
        lambda _run_id: {"recent_runs": []},
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = _init_read_stage_repo(repo)
    report = tmp_path / "dou-no-await.md"
    stage_run_id = "dou-no-await"

    def fake_launcher(_spec, _source_dir):
        _write_read_stage_report(
            report, run_id=stage_run_id, code_mutation=code_mutation
        )
        return {
            "accepted": True,
            "run_id": stage_run_id,
            "report": str(report),
        }

    state = asyncio.run(
        LifecycleRunner(launcher=fake_launcher).run(
            LifecycleRunSpec(
                workflow_id="vc-dou",
                agent="codex",
                prompt="default no-await living tree attribution",
                root=str(repo),
            )
        )
    )
    if concurrent_delta:
        tracked.write_text("base\nconcurrent actor\n", encoding="utf-8")

    assert record_stage_worker_completion(
        state["state_path"],
        stage_run_id,
        {
            "run_id": stage_run_id,
            "state": "report_validated",
            "exit_code": 0,
            "artifact_ok": True,
            "report": str(report),
        },
    )
    reloaded = json.loads(Path(state["state_path"]).read_text(encoding="utf-8"))
    stage = reloaded["stages"][0]
    assert reloaded["status"] == expected_status
    assert stage["changed_files"] == (["tracked.txt"] if concurrent_delta else [])
    assert stage["attributed_changed_files"] == []
    assert stage["read_phase_evidence"]["attribution"] == attribution
    assert stage["read_phase_evidence"]["hard_violation"] is (code_mutation == "true")


def test_lifecycle_runner_records_commits_created_during_stage(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=VC Test",
            "-c",
            "user.email=vc@example.test",
            "commit",
            "-m",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    def fake_launcher(_spec, _source_dir):
        tracked.write_text("base\nwrite stage commit\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=VC Test",
                "-c",
                "user.email=vc@example.test",
                "commit",
                "-m",
                "stage commit",
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        return {
            "accepted": True,
            "run_id": "hydrate-run",
            "report": str(tmp_path / "hydrate.md"),
        }

    def fake_awaiter(payload):
        return {
            "completed": True,
            "artifact_ok": True,
            "exit_code": 0,
            "report": payload["report"],
        }

    runner = LifecycleRunner(launcher=fake_launcher, awaiter=fake_awaiter)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-hydrate",
                agent="codex",
                prompt="preflight",
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    stage = state["stages"][0]
    assert state["status"] == "completed"
    assert len(stage["new_commits"]) == 1
    assert stage["commit_before"] != stage["commit_after"]
    assert stage["changed_files"] == ["tracked.txt"]
    assert "exit_code: 0" in Path(state["report_path"]).read_text(encoding="utf-8")


def test_lifecycle_runner_records_worker_reported_dou_index(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )

    def fake_launcher(spec, _source_dir):
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run",
            "report": str(report),
        }

    def fake_awaiter(payload):
        return {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
            "dou_index": 4,
        }

    runner = LifecycleRunner(launcher=fake_launcher, awaiter=fake_awaiter)
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-dou",
                agent="codex",
                prompt="measure the launch gap",
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    assert state["status"] == "completed"
    assert state["stages"][0]["dou_index"] == 4
    assert state["dou_index"] == {
        "value": 4,
        "stage": "dou",
        "report": str(tmp_path / "dou.md"),
    }
    assert state["baton"]["dou_index"] == 4
    report = Path(state["report_path"]).read_text(encoding="utf-8")
    assert "- dou_index: 4 (stage: dou)" in report
    status = LifecycleSupervisor().status(state)
    assert status["dou_index"] == 4
    assert status["accepted_dou"] == 0


def test_lifecycle_status_reads_dou_index_live_in_no_await_mode(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    report_path = tmp_path / "dou.md"

    def fake_launcher(spec, _source_dir):
        return {
            "accepted": True,
            "run_id": "dou-run",
            "skill": spec.skill,
            "report": str(report_path),
        }

    supervisor = LifecycleSupervisor(runner=LifecycleRunner(launcher=fake_launcher))
    state = asyncio.run(
        supervisor.start(
            LifecycleRunSpec(
                workflow_id="vc-dou",
                agent="codex",
                prompt="measure the launch gap",
                root=str(tmp_path),
            )
        )
    )

    # The runner exited before the worker finished; no dou_index in state yet.
    assert supervisor.status(state)["dou_index"] is None
    # The worker writes its report afterwards — status must read the live truth.
    report_path.write_text(
        "---\nstatus: completed\ndou_index: 0\n---\nZERO DoU index\n",
        encoding="utf-8",
    )
    status = supervisor.status(supervisor.read_state(state["state_path"]))
    assert status["dou_index"] == 0


def test_lifecycle_supervisor_reports_status(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )

    def fake_launcher(spec, _source_dir):
        return {
            "accepted": True,
            "run_id": "dou-run",
            "skill": spec.skill,
            "report": str(tmp_path / "dou.md"),
        }

    supervisor = LifecycleSupervisor(runner=LifecycleRunner(launcher=fake_launcher))
    state = asyncio.run(
        supervisor.start(
            LifecycleRunSpec(
                workflow_id="vc-dou",
                agent="codex",
                prompt="audit readiness",
                root=str(tmp_path),
            )
        )
    )

    loaded = supervisor.read_state(state["state_path"])
    status = supervisor.status(loaded)

    assert loaded["run_id"] == state["run_id"]
    assert status["schema"] == LIFECYCLE_SCHEMA_ID
    assert status["workflow"] == "vc-dou"
    assert status["status"] == "launching"
    assert status["current_stage"] == "dou"
    assert status["next_stage"] == ""


def test_vc_ship_routes_to_lifecycle_runner(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    captured: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec):
        captured.append(spec)
        return {
            "run_id": "life-ship-test",
            "workflow": spec.workflow_id,
            "status": "launching",
            "state_path": str(tmp_path / "state.json"),
            "report_path": str(tmp_path / "report.md"),
        }

    monkeypatch.setattr(ship, "run_lifecycle", fake_run_lifecycle)
    rc = ship.main(["codex", "--prompt", "ship it"])

    assert rc == 0
    assert captured[0].workflow_id == "vc-ship"
    assert captured[0].start_stage == "scaffold"
    assert "VC-SHIP LIFECYCLE RECEIPT" in capsys.readouterr().out


def test_vc_ship_can_start_with_default_lifecycle_prompt(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec):
        captured.append(spec)
        return {
            "run_id": "life-ship-test",
            "workflow": spec.workflow_id,
            "status": "launching",
            "state_path": str(tmp_path / "state.json"),
            "report_path": str(tmp_path / "report.md"),
        }

    monkeypatch.setattr(ship, "run_lifecycle", fake_run_lifecycle)

    assert ship.main(["codex"]) == 0
    assert captured[0].workflow_id == "vc-ship"
    assert captured[0].prompt
    assert "full Vibecrafted lifecycle" in captured[0].prompt


def test_vc_ship_file_mission_is_not_shadowed_by_default_prompt(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec):
        captured.append(spec)
        return {
            "run_id": "life-ship-test",
            "workflow": spec.workflow_id,
            "status": "launching",
            "state_path": str(tmp_path / "state.json"),
            "report_path": str(tmp_path / "report.md"),
        }

    monkeypatch.setattr(ship, "run_lifecycle", fake_run_lifecycle)
    mission = tmp_path / "mission.md"
    mission.write_text("# Mission: adapt the server\n", encoding="utf-8")

    assert ship.main(["codex", "--file", str(mission)]) == 0
    # An empty prompt lets the runner read the mission file; the old
    # `args.prompt or DEFAULT_SHIP_PROMPT` silently discarded --file.
    assert captured[0].prompt == ""
    assert captured[0].file == str(mission)


def test_vc_dou_wrapper_routes_to_lifecycle_runner(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    captured: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec):
        captured.append(spec)
        return {
            "run_id": "life-dou-test",
            "workflow": spec.workflow_id,
            "status": "launching",
            "state_path": str(tmp_path / "state.json"),
            "report_path": str(tmp_path / "report.md"),
        }

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.run_lifecycle", fake_run_lifecycle
    )
    rc = wrappers.dou_main(["codex", "--prompt", "audit readiness"])

    assert rc == 0
    assert captured[0].workflow_id == "vc-dou"
    assert captured[0].agent == "codex"
    assert captured[0].prompt == "audit readiness"
    assert "VC-DOU LIFECYCLE RECEIPT" in capsys.readouterr().out


def test_vc_marbles_wrapper_uses_lifecycle_runner_with_loop_options(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec):
        captured.append(spec)
        return {
            "run_id": "life-marbles-test",
            "workflow": spec.workflow_id,
            "status": "launching",
            "state_path": str(tmp_path / "state.json"),
            "report_path": str(tmp_path / "report.md"),
        }

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.run_lifecycle", fake_run_lifecycle
    )
    rc = wrappers.marbles_main(["codex", "--count", "5", "--depth", "7"])

    assert rc == 0
    assert captured[0].workflow_id == "vc-marbles"
    assert captured[0].count == 5
    assert captured[0].depth == 7


def test_vc_polarize_wrapper_uses_lifecycle_runner_with_loop_options(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec):
        captured.append(spec)
        return {
            "run_id": "life-polarize-test",
            "workflow": spec.workflow_id,
            "status": "launching",
            "state_path": str(tmp_path / "state.json"),
            "report_path": str(tmp_path / "report.md"),
        }

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.run_lifecycle", fake_run_lifecycle
    )
    rc = wrappers.polarize_main(["codex", "--count", "5", "--depth", "7"])

    assert rc == 0
    assert captured[0].workflow_id == "vc-polarize"
    assert captured[0].count == 5
    assert captured[0].depth == 7


@pytest.mark.parametrize(
    ("wrapper_name", "workflow_id"),
    [
        # scaffold/implement/review/followup: supervised_skill_main (one path
        # with `vibecrafted <skill>`). Lifecycle stages remain under `ship`.
        ("workflow_main", "vc-workflow"),
        ("release_main", "vc-release"),
    ],
)
def test_ship_stage_wrappers_route_to_lifecycle_runner(
    monkeypatch, tmp_path: Path, wrapper_name: str, workflow_id: str
) -> None:
    captured: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec):
        captured.append(spec)
        return {
            "run_id": f"life-{workflow_id}-test",
            "workflow": spec.workflow_id,
            "status": "launching",
            "state_path": str(tmp_path / "state.json"),
            "report_path": str(tmp_path / "report.md"),
        }

    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.run_lifecycle", fake_run_lifecycle
    )
    wrapper = getattr(wrappers, wrapper_name)
    rc = wrapper(["codex", "--prompt", "run the stage"])

    assert rc == 0
    assert captured[0].workflow_id == workflow_id
    assert captured[0].agent == "codex"
    assert captured[0].prompt == "run the stage"


def test_scaffold_main_uses_supervised_skill_not_lifecycle(monkeypatch) -> None:
    """vc-scaffold binary must share the cli skill path, not a second lifecycle CLI."""
    called: list[tuple[str, list[str] | None]] = []

    def fake_supervised(skill: str, argv=None):
        called.append((skill, list(argv) if argv is not None else None))
        return 0

    monkeypatch.setattr(wrappers, "supervised_skill_main", fake_supervised)
    monkeypatch.setattr(
        wrappers,
        "_lifecycle_main",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("lifecycle must not run")),
    )
    rc = wrappers.scaffold_main(["codex", "--prompt", "plan auth"])
    assert rc == 0
    assert called == [("scaffold", ["codex", "--prompt", "plan auth"])]


def test_lifecycle_console_scripts_are_packaged() -> None:
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )

    for name in (
        "vc-audit",
        "vc-dou",
        "vc-followup",
        "vc-hydrate",
        "vc-implement",
        "vc-polarize",
        "vc-marbles",
        "vc-release",
        "vc-review",
        "vc-scaffold",
        "vc-ship",
        "vc-workflow",
    ):
        assert f"{name} = " in pyproject


def test_status_surfaces_stage_worker_death_without_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from vibecrafted_core import lifecycle_runner

    calls: list[str] = []

    def fake_liveness(run_id: str) -> dict[str, object]:
        calls.append(run_id)
        return {
            "run_id": run_id,
            "found": True,
            "state": "running",
            "liveness": "pid_alive",
            "worker_alive": False,
            "recovery_required": False,
        }

    monkeypatch.setattr(lifecycle_runner, "run_liveness", fake_liveness)

    report_path = tmp_path / "never-written.md"
    state = {
        "status": "launching",
        "stages": [
            {
                "id": "implement",
                "launch": {"run_id": "impl-dead-1", "report": str(report_path)},
            }
        ],
        "baton": {},
    }

    stage_worker = LifecycleSupervisor().status(state)["stage_worker"]

    assert calls == ["impl-dead-1"]
    assert stage_worker["worker_alive"] is False
    assert stage_worker["report_written"] is False
    # The actionable report-on-death signal: this stage will never deliver on
    # its own — recover with interrupt/fallback/approve.
    assert stage_worker["worker_dead_without_report"] is True

    # A dead worker that DID deliver its report is not a death signal — the
    # normal no-await handoff looks exactly like this.
    report_path.write_text("---\nstatus: completed\n---\ndone\n", encoding="utf-8")
    stage_worker = LifecycleSupervisor().status(state)["stage_worker"]
    assert stage_worker["report_written"] is True
    assert stage_worker["worker_dead_without_report"] is False


def test_status_skips_stage_worker_liveness_when_run_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibecrafted_core import lifecycle_runner

    def explode(_run_id: str) -> dict[str, object]:
        raise AssertionError("terminal runs must not pay for a liveness sync")

    monkeypatch.setattr(lifecycle_runner, "run_liveness", explode)

    state = {
        "status": "completed",
        "stages": [{"id": "release", "launch": {"run_id": "rel-1"}}],
        "baton": {},
    }

    assert LifecycleSupervisor().status(state)["stage_worker"] == {}


def test_record_stage_worker_exit_writes_push_side_death(tmp_path: Path) -> None:
    from vibecrafted_core.lifecycle_runner import record_stage_worker_exit

    state_path = tmp_path / "state.json"
    state = {
        "status": "launching",
        "stages": [
            {"id": "scaffold", "launch": {"run_id": "scaf-1"}},
            {"id": "implement", "launch": {"run_id": "impl-1"}},
        ],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    ok = record_stage_worker_exit(
        state_path,
        "impl-1",
        {"state": "process_dead", "exit_code": 3, "artifact_ok": False},
    )

    assert ok is True
    reloaded = json.loads(state_path.read_text(encoding="utf-8"))
    worker_exit = reloaded["stages"][1]["worker_exit"]
    assert worker_exit["state"] == "process_dead"
    assert worker_exit["exit_code"] == 3
    assert worker_exit["recorded_at"]
    # The passive-reader alarm: current stage of a still-waiting run.
    assert reloaded["stage_worker_exit"]["stage"] == "implement"
    assert reloaded["stage_worker_exit"]["run_id"] == "impl-1"


def test_record_stage_worker_exit_keeps_history_quiet(tmp_path: Path) -> None:
    from vibecrafted_core.lifecycle_runner import record_stage_worker_exit

    state_path = tmp_path / "state.json"
    state = {
        "status": "launching",
        "stages": [
            {"id": "scaffold", "launch": {"run_id": "scaf-1"}},
            {"id": "implement", "launch": {"run_id": "impl-1"}},
        ],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    # A superseded stage (fallback/approve relaunched past it) is history:
    # annotate the stage, do NOT raise the top-level alarm.
    assert record_stage_worker_exit(state_path, "scaf-1", {"state": "failed"})
    reloaded = json.loads(state_path.read_text(encoding="utf-8"))
    assert reloaded["stages"][0]["worker_exit"]["state"] == "failed"
    assert "stage_worker_exit" not in reloaded

    # Unknown run and corrupt state must fail closed, never raise.
    assert record_stage_worker_exit(state_path, "no-such-run", {}) is False
    assert record_stage_worker_exit(state_path, "", {}) is False
    assert record_stage_worker_exit(tmp_path / "missing.json", "impl-1", {}) is False
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert record_stage_worker_exit(broken, "impl-1", {}) is False


def test_start_stage_carries_lifecycle_state_path_to_launch_spec(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    seen_state_paths: list[str] = []

    def fake_launcher(spec, _source_dir):
        seen_state_paths.append(spec.lifecycle_state_path)
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    runner = LifecycleRunner(
        launcher=fake_launcher,
        awaiter=lambda payload: {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        },
    )
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-marbles",
                agent="codex",
                prompt="close the gaps",
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    # Every stage launch tells the dispatcher which lifecycle state.json it
    # belongs to — the push-side report-on-death write-back address.
    assert seen_state_paths
    assert all(path == state["state_path"] for path in seen_state_paths)


def test_vc_ship_default_runtime_prefers_visible_tabs(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[LifecycleRunSpec] = []

    def fake_run_lifecycle(spec: LifecycleRunSpec):
        captured.append(spec)
        return {
            "run_id": "life-ship-test",
            "workflow": spec.workflow_id,
            "status": "launching",
            "state_path": str(tmp_path / "state.json"),
            "report_path": str(tmp_path / "report.md"),
        }

    monkeypatch.setattr(ship, "run_lifecycle", fake_run_lifecycle)
    # Operator invariant: workers fly visibly in vc-frame tabs. Ship must route
    # through the fleet-wide resolution instead of hardcoding headless.
    monkeypatch.setattr(
        "vibecrafted_core.cli._default_runtime",
        lambda explicit, root="": explicit or "terminal",
    )

    assert ship.main(["codex", "--prompt", "ship it"]) == 0
    assert captured[0].runtime == "terminal"

    # An explicit --runtime always wins over the resolved default.
    assert ship.main(["codex", "--prompt", "quiet", "--runtime", "headless"]) == 0
    assert captured[1].runtime == "headless"


def test_mission_stage_agents_parses_inline_and_nested() -> None:
    from vibecrafted_core.lifecycle_runner import _mission_stage_agents

    inline = "---\nstage_agents: scaffold=claude, review=codex\n---\nmission"
    assert _mission_stage_agents(inline) == {
        "scaffold": ["claude"],
        "review": ["codex"],
    }

    nested = (
        "---\n"
        "doc_id: x\n"
        "stage_agents:\n"
        "  marbles: codex\n"
        "  audit: claude\n"
        "other: y\n"
        "---\n"
        "mission body\n"
    )
    assert _mission_stage_agents(nested) == {
        "marbles": ["codex"],
        "audit": ["claude"],
    }

    assert _mission_stage_agents("plain mission, no frontmatter") == {}


def test_mission_stage_models_parses_and_manifest_filters() -> None:
    from vibecrafted_core.lifecycle_runner import (
        _mission_stage_models,
        _validated_stage_models,
    )
    from vibecrafted_core.workflows.registry import workflow_manifest

    inline = "---\nstage_models: scaffold=opus, implement=gpt-5.5\n---\nmission"
    assert _mission_stage_models(inline) == {
        "scaffold": "opus",
        "implement": "gpt-5.5",
    }

    nested = (
        "---\n"
        "doc_id: x\n"
        "stage_models:\n"
        "  marbles: opus\n"
        "  audit: sonnet\n"
        "  nosuch: cheap\n"
        "---\n"
        "mission body\n"
    )
    assert _mission_stage_models(nested) == {
        "marbles": "opus",
        "audit": "sonnet",
        "nosuch": "cheap",
    }

    manifest = workflow_manifest("vc-marbles")
    assert manifest is not None
    assert _validated_stage_models(_mission_stage_models(nested), manifest) == {
        "marbles": "opus",
        "audit": "sonnet",
    }
    assert _mission_stage_models("plain mission, no frontmatter") == {}
    assert _validated_stage_models({}, manifest) == {}


def test_lifecycle_run_casts_stage_agents_from_mission_frontmatter(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    casting: list[tuple[str, str]] = []

    def fake_launcher(spec, _source_dir):
        casting.append((spec.skill, spec.agent))
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    runner = LifecycleRunner(
        launcher=fake_launcher,
        awaiter=lambda payload: {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        },
    )
    mission = (
        "---\n"
        "stage_agents:\n"
        "  marbles: codex\n"
        "  audit: claude\n"
        "---\n"
        "# Mission: cast the relay A-to-Z\n"
    )
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-marbles",
                agent="gemini",
                prompt=mission,
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    # The operator's A-to-Z casting drives every stage launch, overriding the
    # run-level agent; the map is persisted for continuations and readers.
    assert casting == [("marbles", "codex"), ("audit", "claude")]
    assert state["spec"]["stage_agents"] == {"marbles": "codex", "audit": "claude"}


def test_lifecycle_run_casts_stage_models_from_mission_frontmatter(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    casting: list[tuple[str, str, str]] = []

    def fake_launcher(spec, _source_dir):
        casting.append((spec.skill, spec.agent, spec.model))
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
            "model_requested": spec.model,
        }

    runner = LifecycleRunner(
        launcher=fake_launcher,
        awaiter=lambda payload: {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        },
    )
    mission = (
        "---\n"
        "stage_agents:\n"
        "  marbles: codex\n"
        "  audit: claude\n"
        "stage_models:\n"
        "  marbles: opus\n"
        "  audit: sonnet\n"
        "  nosuch: ignored\n"
        "---\n"
        "# Mission: cast models A-to-Z\n"
    )
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-marbles",
                agent="gemini",
                prompt=mission,
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    assert casting == [("marbles", "codex", "opus"), ("audit", "claude", "sonnet")]
    assert state["spec"]["stage_models"] == {"marbles": "opus", "audit": "sonnet"}
    assert [stage["model_requested"] for stage in state["stages"]] == [
        "opus",
        "sonnet",
    ]
    report = Path(state["report_path"]).read_text(encoding="utf-8")
    assert "model_requested: opus" in report
    assert "model_requested: sonnet" in report


def test_lifecycle_run_rejects_invalid_stage_casting(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    runner = LifecycleRunner(
        launcher=lambda spec, _src: {"accepted": True},
        awaiter=lambda payload: {"completed": True},
    )

    with pytest.raises(ValueError, match="unknown stage 'nosuch'"):
        asyncio.run(
            runner.run(
                LifecycleRunSpec(
                    workflow_id="vc-marbles",
                    agent="codex",
                    prompt="---\nstage_agents: nosuch=claude\n---\nmission",
                    root=str(tmp_path),
                )
            )
        )

    with pytest.raises(ValueError, match="unsupported agent 'hal9000'"):
        asyncio.run(
            runner.run(
                LifecycleRunSpec(
                    workflow_id="vc-marbles",
                    agent="codex",
                    prompt="---\nstage_agents: marbles=hal9000\n---\nmission",
                    root=str(tmp_path),
                )
            )
        )


def test_write_stage_with_n_cuts_records_n_cut_id_children(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    launched: list[str] = []

    def supervisor(contract: CutDispatchContract) -> dict:
        launched.append(contract.cut_id)
        return {"accepted": True, "cut_id": contract.cut_id}

    def fake_launcher(spec, _source_dir):
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run",
            "report": str(report),
            "transcript": str(tmp_path / f"{spec.skill}.log"),
            "meta": str(tmp_path / f"{spec.skill}.json"),
        }

    mission = (
        "---\n"
        "cuts:\n"
        "  - W0-a\n"
        "  - W0-b\n"
        "  - W1-c\n"
        "---\n"
        "# Mission: fleet the WRITE stage\n"
    )
    runner = LifecycleRunner(
        launcher=fake_launcher,
        awaiter=lambda payload: {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        },
        fleet_supervisor=supervisor,
    )
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-implement",
                agent="codex",
                run_id="life-impl-fleet",
                prompt=mission,
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )

    fleet = state["stages"][0]["fleet"]
    assert fleet["exception_granted"] is True
    assert fleet["live_dispatch"] is False
    assert fleet["cuts"] == ["W0-a", "W0-b", "W1-c"]
    assert launched == ["W0-a", "W0-b", "W1-c"]
    assert live_vc_dispatch_permitted() is False
    records = load_cut_records("life-impl-fleet")
    assert len(records) >= 3
    by_cut = {item["cut_id"]: item for item in records}
    for cut_id in ("W0-a", "W0-b", "W1-c"):
        worktree = Path(by_cut[cut_id]["worktree_path"])
        assert "worktrees" in worktree.parts
        assert worktree.parts[-2:] == ("life-impl-fleet", cut_id)
        assert worktree.is_relative_to(home / "worktrees")
        assert len(worktree.relative_to(home / "worktrees").parts) == 4


def test_read_stage_with_cuts_does_not_record_fleet(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    prompts: list[str] = []

    def fake_launcher(spec, _source_dir):
        prompts.append(spec.prompt)
        report = tmp_path / f"{spec.skill}.md"
        report.write_text(f"{spec.skill} ok\n", encoding="utf-8")
        return {
            "accepted": True,
            "run_id": f"{spec.skill}-run",
            "report": str(report),
        }

    runner = LifecycleRunner(
        launcher=fake_launcher,
        awaiter=lambda payload: {
            "completed": True,
            "artifact_ok": True,
            "report": payload["report"],
        },
    )
    state = asyncio.run(
        runner.run(
            LifecycleRunSpec(
                workflow_id="vc-dou",
                agent="codex",
                run_id="life-dou-nofleet",
                prompt="---\ncuts: W0-a, W0-b\n---\nREAD mission\n",
                root=str(tmp_path),
                await_stages=True,
            )
        )
    )
    assert "fleet" not in state["stages"][0]
    assert load_cut_records("life-dou-nofleet") == []
    assert "WRITE fleet exception" not in prompts[0]
    assert "must not launch external agent lines" in prompts[0]


def test_write_stage_prompt_names_fleet_exception_when_cuts_listed(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(
        "vibecrafted_core.lifecycle_runner.load_context_atlas",
        lambda *_args, **_kwargs: {"ok": True, "command": ["loct", "context"]},
    )
    prompts: list[str] = []

    def fake_launcher(spec, _source_dir):
        prompts.append(spec.prompt)
        return {
            "accepted": True,
            "run_id": "impl-run",
            "report": str(tmp_path / "implement.md"),
        }

    asyncio.run(
        LifecycleRunner(launcher=fake_launcher).run(
            LifecycleRunSpec(
                workflow_id="vc-implement",
                agent="codex",
                run_id="life-impl-prompt",
                prompt="---\ncuts: A, B\n---\nship the cuts\n",
                root=str(tmp_path),
            )
        )
    )
    assert "WRITE fleet exception" in prompts[0]
    assert "Listed cuts: A, B" in prompts[0]
    assert "no live vc-dispatch" in prompts[0]
    assert "life-impl-prompt" in prompts[0]
