"""Launch receipts must be observable before joining a stub worker."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from vibecrafted_core import cli, control_plane
from vibecrafted_core.dispatch import cli as dispatch_cli


@pytest.fixture
def launch(monkeypatch):
    seen = []
    monkeypatch.setattr(
        cli, "normalize_launch_spec", lambda payload, source: SimpleNamespace(**payload)
    )

    def stub(spec, source):
        seen.append(spec)
        return {
            "accepted": True,
            "run_id": "impl-stub",
            "agent": "codex",
            "skill": spec.skill,
            "report": "/stub/report.md",
            "status": "launching",
            "root": "/stub",
        }

    monkeypatch.setattr(cli, "launch_workflow", stub)
    monkeypatch.setattr(cli, "_watch_launch_startup", lambda _: None)
    return seen


@pytest.mark.parametrize(
    "verb", ["implement", "workflow", "review", "marbles", "research"]
)
@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize("json_mode", [False, True])
def test_receipt_precedes_await_and_exit_propagates(
    launch, monkeypatch, capsys, verb, exit_code, json_mode
):
    def join(run_id, **kwargs):
        out = capsys.readouterr().out
        assert "impl-stub" in out and "/stub/report.md" in out
        if json_mode:
            assert json.loads(out.splitlines()[0])["run_id"] == "impl-stub"
        return {
            "completed": True,
            "found": True,
            "outcome": "terminal",
            "worker_alive": False,
            "run": {
                "state": "completed" if exit_code == 0 else "failed",
                "exit_code": exit_code,
            },
        }

    monkeypatch.setattr(control_plane, "await_run", join)
    assert (
        cli.main(
            [
                verb,
                "codex",
                "--prompt",
                "x",
                "--await",
                *(["--json"] if json_mode else []),
            ]
        )
        == exit_code
    )
    if json_mode:
        assert json.loads(capsys.readouterr().out)["run_id"] == "impl-stub"


@pytest.mark.parametrize("skill", [None, "review"])
def test_dispatch_agent_normalizes_to_existing_launch(launch, skill):
    args = ["dispatch", "codex", "x", "--json"]
    if skill:
        args += ["--skill", skill]
    assert cli.main(args) == 0
    assert launch[0].skill == (skill or "implement")
    assert launch[0].prompt == "x"


def test_dispatch_plan_keeps_plan_route(monkeypatch):
    calls = []
    monkeypatch.setattr(
        dispatch_cli,
        "diagnose_file",
        lambda path: calls.append(path) or SimpleNamespace(ok=False),
    )
    monkeypatch.setattr(dispatch_cli, "_print_doctor", lambda *a, **k: None)
    assert cli.main(["dispatch", "plan.toml", "--doctor"]) == 1
    assert str(calls[0]) == "plan.toml"


def test_invented_dispatch_verb_remains_refused(capsys):
    assert cli.main(["dispatch", "launch", "plan.toml"]) == 2
    assert "unknown dispatch subcommand" in capsys.readouterr().err


def test_receipt_is_flushed_through_real_pipe_before_worker_unblocks(tmp_path):
    import os
    import selectors
    import subprocess
    import sys
    from pathlib import Path

    code = """
from types import SimpleNamespace
from vibecrafted_core import cli, control_plane
cli.normalize_launch_spec = lambda p, s: SimpleNamespace(**p)
cli.launch_workflow = lambda *a: dict(accepted=True, run_id="impl-pipe", report="/stub/report.md", agent="codex", skill="implement")
def join(*a, **k):
    input()
    return dict(completed=True, found=True, run=dict(state="failed", exit_code=7))
control_plane.await_run = join
raise SystemExit(cli.main(["implement", "codex", "--prompt", "x", "--await", "--json"]))
"""
    env = dict(os.environ, PYTHONPATH=str(Path(cli.__file__).resolve().parent.parent))
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "receipt was buffered behind await"
        receipt = json.loads(proc.stdout.readline())
        assert receipt["run_id"] == "impl-pipe"
        assert receipt["report"] == "/stub/report.md"
        assert proc.poll() is None
        out, err = proc.communicate(b"finish\n", timeout=10)
        assert proc.returncode == 7, err
        assert json.loads(out)["run"]["exit_code"] == 7
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


@pytest.mark.parametrize(
    "payload",
    [
        {"completed": False, "found": False},
        {
            "completed": True,
            "worker_alive": True,
            "run": {"state": "completed", "exit_code": 0},
        },
        {
            "completed": True,
            "run": {"state": "completed", "exit_code": 0, "artifact_ok": False},
        },
    ],
)
def test_await_does_not_claim_success_without_terminal_valid_result(
    launch, monkeypatch, payload
):
    monkeypatch.setattr(control_plane, "await_run", lambda *a, **k: payload)
    assert cli.main(["implement", "codex", "--prompt", "x", "--await", "--json"]) != 0


def test_refused_launch_never_awaits(launch, monkeypatch):
    monkeypatch.setattr(cli, "launch_workflow", lambda *a: {"accepted": False})
    monkeypatch.setattr(
        control_plane, "await_run", lambda *a, **k: pytest.fail("refused run awaited")
    )
    assert cli.main(["implement", "codex", "--prompt", "x", "--await"]) == 1


@pytest.mark.parametrize(
    "last",
    [
        {
            "completed": False,
            "found": True,
            "worker_alive": False,
            "reason": "signal_missing",
        },
        {"completed": False, "found": True, "worker_alive": True, "reason": "hard_cap"},
        {
            "completed": True,
            "found": True,
            "worker_alive": False,
            "run": {"state": "completed"},
        },
    ],
)
def test_shared_await_rearms_live_run_but_returns_terminal_or_missing(
    monkeypatch, last
):
    from vibecrafted_core import wrappers

    answers = iter([{"completed": False, "found": True, "worker_alive": True}, last])
    monkeypatch.setattr(control_plane, "await_run", lambda *a, **k: next(answers))
    assert wrappers._await_run_forever("impl-stub", heartbeat=False) == last


def test_session_launch_await_uses_continued_run_receipt(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli,
        "resolve_session_selection",
        lambda *a: {"agent_session_id": "native-session"},
    )
    monkeypatch.setattr(
        cli,
        "manual_resume_session",
        lambda *a, **k: {
            "accepted": True,
            "run_id": "rsme-stub",
            "report": "/stub/resume.md",
            "agent": "codex",
            "skill": "workflow",
        },
    )

    def join(run_id, **kwargs):
        assert run_id == "rsme-stub"
        assert "rsme-stub" in capsys.readouterr().out
        return {"completed": True, "run": {"state": "completed", "exit_code": 0}}

    monkeypatch.setattr(control_plane, "await_run", join)
    assert (
        cli.main(
            [
                "workflow",
                "codex",
                "--session",
                "native-session",
                "--repo",
                str(tmp_path),
                "--await",
                "--json",
            ]
        )
        == 0
    )


def test_agent_named_plan_file_remains_a_plan(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "codex").write_text("plan")
    calls = []
    monkeypatch.setattr(
        dispatch_cli,
        "diagnose_file",
        lambda path: calls.append(path) or SimpleNamespace(ok=False),
    )
    monkeypatch.setattr(dispatch_cli, "_print_doctor", lambda *a, **k: None)
    assert dispatch_cli.main(["codex", "--doctor"]) == 1
    assert str(calls[0]) == "codex"


@pytest.mark.parametrize(
    "deck",
    ["scripts/vibecrafted", "vibecrafted-core/vibecrafted_core/deck/vibecrafted"],
)
def test_deck_dispatch_alias_reaches_core_launch(tmp_path, deck):
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    shim = tmp_path / "python-shim"
    shim.write_text(
        f"#!{sys.executable}\n"
        + """
import os, runpy, sys
if sys.argv[1:3] != ["-m", "vibecrafted_core.dispatch.cli"]:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
from types import SimpleNamespace
from vibecrafted_core import cli, control_plane
cli.normalize_launch_spec = lambda p, s: SimpleNamespace(**p)
def launch(spec, source):
    assert spec.skill == "review" and spec.prompt == "opaque ; $(literal)"
    return dict(accepted=True, run_id="impl-deck", report="/stub/report.md", skill=spec.skill, agent=spec.agent)
cli.launch_workflow = launch
control_plane.await_run = lambda *a, **k: dict(completed=True, run=dict(state="failed", exit_code=7))
sys.argv = [sys.argv[2], *sys.argv[3:]]
runpy.run_module("vibecrafted_core.dispatch.cli", run_name="__main__")
"""
    )
    shim.chmod(0o755)
    env = dict(os.environ, VIBECRAFTED_PYTHON=str(shim))
    result = subprocess.run(
        [
            "bash",
            str(repo / deck),
            "dispatch",
            "codex",
            "opaque ; $(literal)",
            "--skill",
            "review",
            "--await",
            "--json",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 7, result.stderr
    receipt, completion = map(json.loads, result.stdout.splitlines())
    assert receipt["run_id"] == "impl-deck" and receipt["skill"] == "review"
    assert completion["run"]["exit_code"] == 7
