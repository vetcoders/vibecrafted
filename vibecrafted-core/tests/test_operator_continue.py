from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from vibecrafted_core import cli, workflow
from vibecrafted_core.package_resources import deck_path
from vibecrafted_core.workflow import (
    classify_resume_identity,
    looks_like_control_plane_run_id,
    operator_continue_run,
)


def test_looks_like_control_plane_run_id_accepts_work_ids() -> None:
    assert looks_like_control_plane_run_id("work-260816-213657-08420")
    assert looks_like_control_plane_run_id("rsme-260816-215903-94636")
    assert not looks_like_control_plane_run_id("d863c229-8b7a-4ee4-a972-16babba5ae30")
    assert not looks_like_control_plane_run_id("01a00ad6-4495-7864-b995-dfa1a2ca8cfa")
    assert not looks_like_control_plane_run_id("")


def test_classify_resume_identity_uses_run_shape_without_lookup() -> None:
    assert classify_resume_identity("work-260816-213657-08420") == "run_id"
    assert classify_resume_identity("") == "empty"


def test_operator_continue_run_refuses_provider_session_as_run_id(
    monkeypatch,
) -> None:
    monkeypatch.setattr(workflow, "lookup_run", lambda _run_id: None)
    monkeypatch.setattr(
        workflow,
        "find_run_for_identity_token",
        lambda token: {
            "run_id": "work-260816-213657-08420",
            "agent_session_id": token,
            "runtime_session_id": "01a00ad6-4495-7864-b995-dfa1a2ca8cfa",
        },
    )

    result = operator_continue_run("d863c229-8b7a-4ee4-a972-16babba5ae30")

    assert result["accepted"] is False
    assert result["reason"] == "provider_session_not_run_id"
    assert "--session" in str(result.get("hint") or "")


def test_operator_continue_run_refuses_vibecrafted_session_as_run_id(
    monkeypatch,
) -> None:
    runtime = "01a00ad6-4495-7864-b995-dfa1a2ca8cfa"
    monkeypatch.setattr(workflow, "lookup_run", lambda _run_id: None)
    monkeypatch.setattr(
        workflow,
        "find_run_for_identity_token",
        lambda token: {
            "run_id": "work-260816-213657-08420",
            "agent_session_id": "d863c229-8b7a-4ee4-a972-16babba5ae30",
            "runtime_session_id": runtime,
            "vibecrafted_session_id": runtime,
        },
    )

    result = operator_continue_run(runtime)

    assert result["accepted"] is False
    assert result["reason"] == "vibecrafted_session_not_run_id"
    assert "work-260816-213657-08420" in str(result.get("hint") or "")


def test_operator_continue_run_uses_provider_session_after_stop(
    monkeypatch, tmp_path: Path
) -> None:
    parent = {
        "run_id": "work-260816-213657-08420",
        "agent": "claude",
        "skill": "workflow",
        "state": "stopped",
        "status": "stopped",
        "root": str(tmp_path),
        "agent_session_id": "d863c229-8b7a-4ee4-a972-16babba5ae30",
        "runtime_session_id": "01a00ad6-4495-7864-b995-dfa1a2ca8cfa",
        "operator_stop_accepted": True,
        "stop_reason": "operator stop request",
        "exit_code": 143,
    }
    monkeypatch.setattr(workflow, "lookup_run", lambda _run_id: dict(parent))
    monkeypatch.setattr(workflow, "_native_resume_meta", lambda *_a, **_k: dict(parent))
    monkeypatch.setattr(workflow, "_worker_process_alive", lambda _run: False)
    monkeypatch.setattr(
        workflow,
        "resolve_run",
        lambda _run_id: (_ for _ in ()).throw(workflow.RunNotResolved("x")),
    )
    seen: dict[str, Any] = {}

    def fake_manual(
        agent: str, agent_session_id: str, _source: Any, **kwargs: Any
    ) -> dict[str, Any]:
        seen.update({"agent": agent, "agent_session_id": agent_session_id, **kwargs})
        return {
            "accepted": True,
            "run_id": "rsme-child",
            "agent": agent,
            "agent_session_id": agent_session_id,
            "resume_mode": "manual_explicit",
            "root": str(kwargs.get("root") or ""),
        }

    monkeypatch.setattr(workflow, "manual_resume_session", fake_manual)

    result = operator_continue_run(
        "work-260816-213657-08420",
        source_dir=tmp_path,
        expected_agent="claude",
        prompt="continue the env-truth cut",
    )

    assert result["accepted"] is True
    assert result["resume_of"] == "work-260816-213657-08420"
    assert result["operator_continue"] is True
    assert seen["agent"] == "claude"
    assert seen["agent_session_id"] == "d863c229-8b7a-4ee4-a972-16babba5ae30"
    assert "continue the env-truth cut" in str(seen.get("prompt") or "")
    assert "stopped" in str(seen.get("prompt") or "").lower()


def test_operator_continue_run_uses_provider_session_after_stall(
    monkeypatch, tmp_path: Path
) -> None:
    parent = {
        "run_id": "owne-260822-093025-32039",
        "agent": "claude",
        "skill": "ownership",
        "state": "stalled",
        "status": "stalled",
        "root": str(tmp_path),
        "agent_session_id": "988ace3a-5cb9-4cae-a229-20e1d3222d45",
        "runtime_session_id": "01a02548-3097-7cd2-b7b2-61e218253026",
    }
    monkeypatch.setattr(workflow, "lookup_run", lambda _run_id: dict(parent))
    monkeypatch.setattr(workflow, "_native_resume_meta", lambda *_a, **_k: dict(parent))
    monkeypatch.setattr(workflow, "_worker_process_alive", lambda _run: False)
    seen: dict[str, Any] = {}

    def fake_manual(
        agent: str, agent_session_id: str, _source: Any, **kwargs: Any
    ) -> dict[str, Any]:
        seen.update({"agent": agent, "agent_session_id": agent_session_id, **kwargs})
        return {
            "accepted": True,
            "run_id": "rsme-child",
            "agent": agent,
            "agent_session_id": agent_session_id,
            "resume_mode": "manual_explicit",
            "root": str(kwargs.get("root") or ""),
        }

    monkeypatch.setattr(workflow, "manual_resume_session", fake_manual)

    result = operator_continue_run(
        "owne-260822-093025-32039",
        source_dir=tmp_path,
        expected_agent="claude",
        prompt="continue from the stalled ownership run",
    )

    assert result["accepted"] is True
    assert result["resume_of"] == "owne-260822-093025-32039"
    assert seen["agent_session_id"] == "988ace3a-5cb9-4cae-a229-20e1d3222d45"
    assert "stalled ownership run" in str(seen.get("prompt") or "")


def test_operator_continue_run_replays_prompt_without_provider_session(
    monkeypatch, tmp_path: Path
) -> None:
    run_dir = tmp_path / "runtime_runs" / "work-260816-000001-00001"
    run_dir.mkdir(parents=True)
    (run_dir / "prompt.md").write_text("Original job: fix env-truth lies.\n")
    parent = {
        "run_id": "work-260816-000001-00001",
        "agent": "claude",
        "skill": "workflow",
        "state": "stopped",
        "root": str(tmp_path),
        "agent_session_id": "01a00ad6-4495-7864-b995-dfa1a2ca8cfa",
        "runtime_session_id": "01a00ad6-4495-7864-b995-dfa1a2ca8cfa",
        "stop_reason": "operator stop request",
        "exit_code": 143,
    }
    monkeypatch.setattr(workflow, "lookup_run", lambda _run_id: dict(parent))
    monkeypatch.setattr(workflow, "_native_resume_meta", lambda *_a, **_k: dict(parent))
    monkeypatch.setattr(workflow, "_worker_process_alive", lambda _run: False)

    class _Resolved:
        def __init__(self) -> None:
            self.run_dir = run_dir

    monkeypatch.setattr(workflow, "resolve_run", lambda _run_id: _Resolved())
    launches: list[dict[str, Any]] = []

    def fake_launch(spec: Any, _source: Any, **kwargs: Any) -> dict[str, Any]:
        launches.append({"spec": spec, **kwargs})
        return {"accepted": True, "run_id": "work-child", "root": spec.root}

    monkeypatch.setattr(workflow, "launch_workflow", fake_launch)

    result = operator_continue_run(
        "work-260816-000001-00001",
        source_dir=tmp_path,
        expected_agent="claude",
    )

    assert result["accepted"] is True
    assert result["resume_mode"] == "resume_new_session"
    assert launches
    assert "Original job: fix env-truth lies." in launches[0]["spec"].prompt
    assert launches[0]["spec"].mode == "resume-new-session"
    assert launches[0]["launch_meta"]["resume_of"] == "work-260816-000001-00001"


def test_operator_continue_run_refuses_live_worker(monkeypatch, tmp_path: Path) -> None:
    parent = {
        "run_id": "work-260816-111111-00001",
        "agent": "claude",
        "state": "launching",
        "root": str(tmp_path),
    }
    monkeypatch.setattr(workflow, "lookup_run", lambda _run_id: dict(parent))
    monkeypatch.setattr(workflow, "_native_resume_meta", lambda *_a, **_k: dict(parent))
    monkeypatch.setattr(workflow, "_worker_process_alive", lambda _run: True)

    result = operator_continue_run(
        "work-260816-111111-00001",
        source_dir=tmp_path,
        expected_agent="claude",
    )

    assert result["accepted"] is False
    assert result["reason"] == "still_running"


def test_agent_resume_cli_routes_run_id(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "operator_continue_run",
        lambda run_id, **kwargs: {
            "accepted": True,
            "run_id": "work-child",
            "resume_of": run_id,
            "agent": kwargs.get("expected_agent"),
            "resume_mode": "manual_explicit",
            "root": str(tmp_path),
        },
    )
    monkeypatch.setattr(cli, "_watch_launch_startup", lambda *_a, **_k: None)

    rc = cli.main(
        [
            "claude",
            "resume",
            "--run-id",
            "work-260816-213657-08420",
            "--prompt",
            "continue",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "OPERATOR CONTINUE RECEIPT" in output
    assert "work-260816-213657-08420" in output
    assert "vibecrafted observe claude --run-id work-child" in output
    assert "vibecrafted await claude --run-id work-child" in output
    assert "vibecrafted claude observe" not in output
    assert "vibecrafted claude await" not in output


def test_agent_resume_cli_rejects_session_flag_used_as_run(capsys) -> None:
    rc = cli.main(["claude", "resume", "--session", "work-260816-213657-08420"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "control-plane run id" in err
    assert "--run-id" in err


def test_deck_accepts_action_first_resume_mode() -> None:
    deck = deck_path()
    result = subprocess.run(
        ["bash", str(deck), "resume", "claude", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Unknown mode" not in result.stderr
    assert "--run-id" in result.stdout or "resume --run-id" in result.stdout
