import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from vibecrafted_core import workflow


def record(home, name, native, root, agent="codex", stamp="2026-09-09T09:00:00Z"):
    path = home / "runtime_runs" / name / "meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_id": name,
                "agent": agent,
                "agent_session_id": native,
                "root": str(root),
                "started_at": stamp,
            }
        )
    )


def test_last_scopes_provider_checkout_and_refuses_tie(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "control_plane_home", lambda: tmp_path)
    monkeypatch.setattr(workflow, "lookup_run", lambda _: None)
    record(tmp_path, "one", "source", tmp_path)
    record(
        tmp_path, "other", "foreign", tmp_path / "other", stamp="2027-01-01T00:00:00Z"
    )
    record(
        tmp_path,
        "claude",
        "foreign-provider",
        tmp_path,
        agent="claude",
        stamp="2027-01-01T00:00:00Z",
    )
    result = workflow.resolve_session_selection("codex", "last", tmp_path)
    assert result["agent_session_id"] == "source"
    assert result["identity_source"] == "repository_provider_latest_started"
    record(tmp_path, "two", "ambiguous", tmp_path)
    with pytest.raises(ValueError, match="ambiguous"):
        workflow.resolve_session_selection("codex", "last", tmp_path)


def test_current_requires_explicit_unambiguous_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(workflow, "lookup_run", lambda _: None)
    monkeypatch.setattr(workflow, "find_run_for_identity_token", lambda _: None)
    with pytest.raises(ValueError, match="explicit"):
        workflow.resolve_session_selection("codex", "current", tmp_path, environment={})
    assert (
        workflow.resolve_session_selection(
            "codex", "current", tmp_path, environment={"CODEX_THREAD_ID": "source"}
        )["agent_session_id"]
        == "source"
    )
    with pytest.raises(ValueError, match="explicit"):
        workflow.resolve_session_selection(
            "codex",
            "current",
            tmp_path,
            environment={"CODEX_THREAD_ID": "source", "CODEX_SESSION_ID": "other"},
        )
    with pytest.raises(ValueError, match="differs"):
        workflow.resolve_session_selection(
            "codex",
            "current",
            tmp_path / "other",
            environment={"CODEX_THREAD_ID": "source"},
        )


@pytest.mark.parametrize("provider", ["codex", "claude", "grok"])
def test_two_native_task_forks_preserve_source_and_private_input(
    tmp_path, monkeypatch, provider
):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    monkeypatch.setattr(workflow, "find_run_for_identity_token", lambda _: None)
    monkeypatch.setattr(workflow, "lookup_run", lambda _: None)
    from vibecrafted_core.workflow_runtime import native_resume_argv

    monkeypatch.setattr(
        workflow,
        "_verified_native_resume_command",
        lambda agent, session: (
            native_resume_argv(agent, session),
            "probe_confirmed",
            "test",
        ),
    )
    real_run = subprocess.run
    monkeypatch.setattr(
        workflow.subprocess,
        "run",
        lambda *a, **kw: (
            SimpleNamespace(returncode=0, stdout=b"<SESSION_ID> stdin")
            if a[0][1:3] == ["exec", "fork"]
            else real_run(*a, **kw)
        ),
    )
    captured = []

    def launch(spec, source, **kwargs):
        captured.append((spec, kwargs))
        return {"accepted": True, "run_id": spec.run_id}

    monkeypatch.setattr(workflow, "launch_workflow", launch)
    for task in ("first\r\n\r\n", 'second\n"quoted"\n'):
        result = workflow.manual_fork_session(
            provider, "source-native", tmp_path, prompt=task, root=tmp_path
        )
        assert result["accepted"], result
    assert captured[0][0].run_id != captured[1][0].run_id
    for spec, kwargs in captured:
        argv = kwargs["worker_command_override"]
        assert "source-native" in argv
        assert spec.prompt not in argv
        assert kwargs["env"]["VIBECRAFTED_AGENT_SESSION_ID"] == ""
        assert kwargs["launch_meta"]["fork_source_session_id"] == "source-native"
        assert (
            (argv[1] == "exec" and argv[-3:] == ["fork", "source-native", "-"])
            if provider == "codex"
            else "--fork-session" in argv
        )


def test_core_rejects_both_identities_before_lookup():
    from vibecrafted_core.cli import _agent_resume

    with pytest.raises(SystemExit) as exc:
        _agent_resume("codex", ["--session", "native", "--run-id", "work-123"])
    assert exc.value.code == 2


def test_public_task_forks_are_independent_tracked_native_commands(tmp_path):
    import os
    import time

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    fake = tmp_path / "bin"
    fake.mkdir()
    provider = fake / "codex"
    provider.write_text("""#!/usr/bin/env python3
import sys, json, uuid
if "--help" in sys.argv:
    print("exec resume fork <SESSION_ID> stdin"); sys.exit()
if "--version" in sys.argv:
    print("codex-cli fixture"); sys.exit()
assert "fork" in sys.argv and "source-native" in sys.argv, sys.argv
text = sys.stdin.buffer.read().decode()
print(json.dumps({"type":"thread.started", "thread_id":str(uuid.uuid4())}), flush=True)
print(json.dumps({"type":"item.completed", "item":{"type":"agent_message", "text":"Task done"}}), flush=True)
print(json.dumps({"type":"turn.completed", "usage":{"input_tokens":1,"output_tokens":1}}), flush=True)
""")
    provider.chmod(0o755)
    root = Path(__file__).resolve().parents[2]
    # Use the test interpreter, which has this source package available.
    import sys

    runner = sys.executable
    env = {
        k: os.environ[k] for k in ("PATH", "HOME", "USER", "LANG") if k in os.environ
    }
    env.update(
        PATH=f"{fake}:{env['PATH']}",
        VIBECRAFTED_HOME=str(tmp_path / "vc"),
        VIBECRAFTED_ROOT=str(root),
        VIBECRAFTED_PYTHON=runner,
    )
    receipts = []
    for i in range(2):
        plan = tmp_path / f"task-{i}.md"
        body = f"task {i}\r\nŻółć\r\n\r\n".encode()
        plan.write_bytes(body)
        proc = subprocess.run(
            [
                "bash",
                str(root / "scripts/vibecrafted"),
                "fork",
                "codex",
                "--session",
                "source-native",
                "--repo",
                str(repo),
                "--file",
                str(plan),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        receipt = json.loads(proc.stdout)
        receipts.append(receipt)
        assert receipt["accepted"]
        assert body == Path(receipt["source_snapshot"]).read_bytes()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        metas = [json.loads(Path(r["meta"]).read_text()) for r in receipts]
        if all(m.get("exit_code") is not None for m in metas):
            break
        time.sleep(0.1)
    assert len({r["run_id"] for r in receipts}) == 2
    assert len({m.get("agent_session_id") for m in metas}) == 2
    assert all(
        m.get("agent_session_id") != "source-native" and m.get("exit_code") == 0
        for m in metas
    ), metas
    assert all(m["fork_source_session_id"] == "source-native" for m in metas)


def test_junie_advertised_model_flag_is_preserved():
    from vibecrafted_core.model_overrides import _with_model_override

    assert _with_model_override(
        "junie", ["junie", "--resume", "--session-id", "native"], "exact-model"
    ) == ["junie", "--model", "exact-model", "--resume", "--session-id", "native"]


def test_capture_tracks_terminal_resize_while_idle(tmp_path):
    import fcntl
    import os
    import pty
    import struct
    import termios
    import time

    from vibecrafted_core.runtime_transcript import InteractiveTranscriptCapture

    outer, display = pty.openpty()
    capture = InteractiveTranscriptCapture(tmp_path / "output", "", display_fd=display)
    provider_output = os.dup(capture.slave)
    capture.start()
    try:
        wanted = struct.pack("HHHH", 51, 137, 0, 0)
        fcntl.ioctl(display, termios.TIOCSWINSZ, wanted)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if fcntl.ioctl(provider_output, termios.TIOCGWINSZ, b"\0" * 8) == wanted:
                break
            time.sleep(0.02)
        assert fcntl.ioctl(provider_output, termios.TIOCGWINSZ, b"\0" * 8) == wanted
    finally:
        os.close(provider_output)
        capture.close()
        os.close(display)
        os.close(outer)


def test_bare_native_fork_allows_explicit_current_parent(monkeypatch):
    from vibecrafted_core.continuity import capabilities
    from vibecrafted_core.spawn import resolve_continuity_policy

    monkeypatch.setattr(
        capabilities,
        "probe",
        lambda *a, **kw: SimpleNamespace(
            state=capabilities.PROBE_CONFIRMED, detail="confirmed"
        ),
    )
    policy = resolve_continuity_policy(
        "bare-fork",
        provider="codex",
        parent_session_id="source-current",
        env={"CODEX_SESSION_ID": "source-current"},
    )
    assert policy.parent_provider_session_id == "source-current"


@pytest.mark.parametrize("child", ["", "source-native"])
def test_native_fork_refuses_missing_or_reused_child_identity(
    tmp_path, monkeypatch, child
):
    import asyncio
    import sys

    from vibecrafted_core.supervisor_async import AsyncSupervisor

    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    meta = tmp_path / "meta.json"
    meta.write_text(
        json.dumps(
            {
                "run_id": "fork-invalid-child",
                "native_fork": True,
                "fork_source_session_id": "source-native",
            }
        )
    )
    event = (
        json.dumps({"type": "thread.started", "thread_id": child})
        if child
        else "no native identity"
    )
    handle = asyncio.run(
        AsyncSupervisor().run(
            run_id="fork-invalid-child",
            command=[sys.executable, "-c", f"print({event!r})"],
            root=tmp_path,
            env={"VIBECRAFTED_AGENT": "codex", "VIBECRAFTED_AGENT_SESSION_ID": ""},
            meta_path=meta,
            report_path=tmp_path / "report.md",
            transcript_path=tmp_path / "transcript.log",
        )
    )
    assert handle.process.returncode == 0
    assert handle.exit_code == 1
