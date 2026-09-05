from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import control_plane, workflow, wrappers


def test_deck_path_resolves_packaged_command_deck() -> None:
    deck = wrappers.deck_path()

    assert deck.name == "vibecrafted"
    assert deck.parent.name == "deck"
    assert deck.is_file()


def test_run_env_uses_current_interpreter_and_packaged_runtime() -> None:
    env = wrappers._env_for_run("impl-test", "impl")

    assert env["VIBECRAFTED_PYTHON"] == sys.executable
    assert env["VIBECRAFTED_ROOT"] == str(wrappers.runtime_root())
    assert Path(env["VIBECRAFTED_ROOT"]).name == "runtime"


def test_supervised_skill_main_routes_runtime_launch_through_dispatcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("VIBECRAFTED_RUN_ID", "impl-test")
    monkeypatch.setattr(wrappers, "invocation_root", lambda: tmp_path)
    monkeypatch.setattr(
        wrappers,
        "_await_run_forever",
        lambda run_id: {
            "completed": True,
            "run": {"state": "completed", "exit_code": 0},
        },
    )

    class FailingSupervisor:
        def __init__(self) -> None:  # pragma: no cover - would fail before branch body
            raise AssertionError("normal runtime launches must use dispatcher")

    def fake_call(
        command: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None
    ) -> int:
        calls.append({"command": command, "cwd": cwd, "env": env})
        return 0

    monkeypatch.setattr(wrappers, "Supervisor", FailingSupervisor)
    monkeypatch.setattr(subprocess, "call", fake_call)

    assert wrappers.supervised_skill_main("implement", ["junie", "--prompt", "x"]) == 0

    assert len(calls) == 1
    assert calls[0]["cwd"] == str(tmp_path)
    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert env["VIBECRAFTED_AGENT"] == "junie"
    assert env["VIBECRAFTED_RUN_ID"] == "impl-test"
    assert calls[0]["command"] == [
        sys.executable,
        "-m",
        "vibecrafted_core.dispatcher",
        "run",
        "--run-id",
        "impl-test",
        "--root",
        str(tmp_path),
        "--no-require-report",
        "--quiet",
        "--",
        sys.executable,
        "-m",
        "vibecrafted_core.cli",
        "implement",
        "junie",
        "--prompt",
        "x",
        "--runtime",
        "headless",
    ]


def test_supervised_wrapper_help_uses_core_renderer_without_subprocess(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        subprocess,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("help must not delegate to another CLI brain")
        ),
    )

    assert wrappers.supervised_skill_main("review", ["codex", "--help"]) == 0

    output = capsys.readouterr().out
    assert "Bounded PR, branch, commit-range, or artifact-pack review" in output
    assert "Flow:" in output


def test_lifecycle_and_research_wrapper_help_use_core_renderer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        subprocess,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("help must not delegate to the shell deck")
        ),
    )

    assert wrappers.workflow_main(["codex", "--help"]) == 0
    workflow_output = capsys.readouterr().out
    assert "Examine → Research → Implement" in workflow_output

    assert wrappers.marbles_main(["--help"]) == 0
    marbles_output = capsys.readouterr().out
    assert "one dedicated orchestrator tab" in marbles_output

    assert wrappers.research_main(["--help"]) == 0
    research_output = capsys.readouterr().out
    assert "Multi-agent research pass" in research_output


def test_print_completed_rejects_live_worker_completion_payload(capsys) -> None:
    rc = wrappers._print_completed(
        "impl-live",
        {
            "completed": True,
            "reason": "report_delivered",
            "worker_alive": True,
            "run": {
                "state": "running",
                "exit_code": None,
                "artifact_ok": True,
                "latest_report": "/tmp/report.md",
            },
        },
    )

    assert rc == 3
    assert "non-terminal completion disagreement" in capsys.readouterr().err


@pytest.mark.parametrize("exit_code, expected", [(7, 7), (0, 3), (None, 3)])
def test_print_completed_rejects_failed_delivered_artifact(
    exit_code: int | None, expected: int
) -> None:
    rc = wrappers._print_completed(
        "impl-failed",
        {
            "completed": True,
            "reason": "report_delivered",
            "worker_alive": False,
            "run": {
                "state": "failed",
                "exit_code": exit_code,
                "artifact_ok": False,
                "artifact_errors": ["report_missing"],
            },
        },
    )

    assert rc == expected


def test_print_completed_sends_missing_payload_error_to_stderr(capsys) -> None:
    assert wrappers._print_completed("impl-missing", {}) == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "completed without control-plane payload" in captured.err


def test_argv_has_job_input_detects_prompt_and_file_flags() -> None:
    assert wrappers.argv_has_job_input([]) is False
    assert wrappers.argv_has_job_input(["claude"]) is False
    assert wrappers.argv_has_job_input(["claude", "--runtime", "plain"]) is False
    assert wrappers.argv_has_job_input(["claude", "--prompt", "x"]) is True
    assert wrappers.argv_has_job_input(["claude", "-p", "x"]) is True
    assert wrappers.argv_has_job_input(["claude", "--file", "brief.md"]) is True
    assert wrappers.argv_has_job_input(["claude", "-f", "brief.md"]) is True
    assert wrappers.argv_has_job_input(["claude", "--prompt-stdin"]) is True
    assert wrappers.argv_has_job_input(["--prompt=x", "claude"]) is True
    assert wrappers.argv_has_job_input(["--file=brief.md"]) is True


def _force_interactive_stdio(
    monkeypatch: pytest.MonkeyPatch, interactive: bool
) -> None:
    monkeypatch.setattr(wrappers.sys.stdin, "isatty", lambda: interactive)
    monkeypatch.setattr(wrappers.sys.stdout, "isatty", lambda: interactive)


def test_partner_main_without_prompt_calls_deck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_call(cmd):
        calls.append(list(cmd))
        return 0

    _force_interactive_stdio(monkeypatch, True)
    monkeypatch.setattr(subprocess, "call", fake_call)

    assert wrappers.partner_main(["claude"]) == 0
    assert calls
    assert calls[0][1:] == ["partner", "claude"]


def test_partner_main_with_prompt_stays_on_deck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_call(cmd):
        calls.append(list(cmd))
        return 0

    def boom(_skill, _argv):
        raise AssertionError("partner --prompt must not call supervised_skill_main")

    _force_interactive_stdio(monkeypatch, True)
    monkeypatch.setattr(subprocess, "call", fake_call)
    monkeypatch.setattr(wrappers, "supervised_skill_main", boom)

    assert wrappers.partner_main(["claude", "--prompt", "x"]) == 0
    assert calls
    assert calls[0][1:] == ["partner", "claude", "--prompt", "x"]


def test_partner_main_without_tty_refuses_even_with_prompt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(_cmd):
        raise AssertionError("headless vc-partner must not spawn the deck")

    _force_interactive_stdio(monkeypatch, False)
    monkeypatch.setattr(subprocess, "call", boom)

    assert wrappers.partner_main(["claude", "--prompt", "x"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == wrappers.PARTNER_INTERACTIVE_ONLY


def test_resume_main_routes_through_tracked_native_resume_api(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []

    def fake_resume(run_id: str, **kwargs: object) -> dict[str, object]:
        calls.append({"run_id": run_id, **kwargs})
        return {
            "accepted": True,
            "run_id": run_id,
            "resume_run_id": "rsme-child",
            "attempt": 2,
        }

    monkeypatch.setattr(workflow, "native_resume_run", fake_resume)

    assert (
        wrappers.resume_main(
            [
                "--run-id",
                "impl-080608-14038",
                "--agent",
                "codex",
                "--prompt",
                "Continue the fix",
                "--idempotency-key",
                "settlement:impl-080608-14038:7",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "run_id": "impl-080608-14038",
            "source_dir": ".",
            "prompt": "Continue the fix",
            "expected_agent": "codex",
            "idempotency_key": "settlement:impl-080608-14038:7",
        }
    ]
    assert "run_id=rsme-child" in capsys.readouterr().out


def test_resume_main_returns_nonzero_and_reason_on_refusal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        workflow,
        "native_resume_run",
        lambda *_args, **_kwargs: {
            "accepted": False,
            "reason": "trust_x",
            "detail": "trust rejected this run",
        },
    )

    rc = wrappers.resume_main(["--run-id", "impl-x", "--agent", "codex"])

    assert rc == 1
    assert (
        "vibecrafted-resume: refused trust_x: trust rejected this run"
        in capsys.readouterr().err
    )


def test_stop_main_prints_success_for_stopped_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        workflow,
        "stop_run",
        lambda run_id, *, reason, grace_seconds: {
            "accepted": True,
            "run_id": run_id,
            "target": "launcher_pid",
            "target_pid": 1234,
            "target_pgid": 1234,
            "already_dead": False,
            "run": {"state": "stopped"},
        },
    )

    code = wrappers.stop_main(
        ["--agent", "codex", "--run-id", "wflw-010101-0001", "--grace-seconds", "0"]
    )

    assert code == 0
    assert (
        "run_id=wflw-010101-0001 state=stopped "
        "target=launcher_pid:1234 pgid=1234 TERM sent"
    ) in capsys.readouterr().out


def test_stop_main_terminal_noop_is_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        workflow,
        "stop_run",
        lambda run_id, *, reason, grace_seconds: {
            "accepted": False,
            "run_id": run_id,
            "reason": "run_terminal",
            "run": {"state": "completed"},
        },
    )

    code = wrappers.stop_main(["--run-id", "wflw-terminal"])

    assert code == 0
    assert "already terminal state=completed; no-op" in capsys.readouterr().out


def test_stop_main_accepts_last_for_agent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        control_plane,
        "sync_state",
        lambda: {
            "active_runs": [{"run_id": "work-260816-213657-08420", "agent": "claude"}],
            "recent_runs": [],
        },
    )
    monkeypatch.setattr(control_plane, "lookup_run", lambda run_id: {"run_id": run_id})
    monkeypatch.setattr(
        workflow,
        "stop_run",
        lambda run_id, *, reason, grace_seconds: {
            "accepted": True,
            "run_id": run_id,
            "target": "launcher_pid",
            "target_pid": 9,
            "target_pgid": 9,
            "already_dead": True,
            "run": {"state": "stopped"},
        },
    )

    code = wrappers.stop_main(["--agent", "claude", "--last", "--grace-seconds", "0"])

    assert code == 0
    assert "run_id=work-260816-213657-08420" in capsys.readouterr().out
