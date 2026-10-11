import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from vibecrafted_core import control_plane, spawn, workflow


@pytest.fixture(autouse=True)
def isolated_native_catalog(monkeypatch):
    monkeypatch.setattr(
        spawn, "native_catalog_session_candidates", lambda *_a, **_k: [], raising=False
    )


@pytest.fixture
def read_only_identity_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "control_plane_home", lambda: tmp_path)
    monkeypatch.setattr(control_plane, "control_plane_home", lambda: tmp_path)

    def forbidden_lookup(_):
        pytest.fail("native identity selection must not synchronize control plane")

    monkeypatch.setattr(workflow, "lookup_run", forbidden_lookup)
    return tmp_path


@pytest.mark.parametrize("recorded", [False, True])
def test_native_last_and_pinned_admission_never_synchronize_control_plane(
    read_only_identity_storage, monkeypatch, recorded
):
    root = read_only_identity_storage
    if recorded:
        record(root, "work-recorded", "native-session", root, agent="claude")
    monkeypatch.setattr(
        spawn,
        "native_catalog_session_candidates",
        lambda *_: [
            {
                "session_id": "native-session",
                "timestamp": 2_000_000_000.0,
                "identity_source": "native_provider_catalog",
            }
        ],
    )
    selected = workflow.resolve_session_selection("claude", "last", root)
    assert selected["source_run_id"] == ""
    assert (
        workflow.resolve_session_selection(
            "claude", "native-session", root, selection=selected
        )
        == selected
    )


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("run_shape", "control-plane run id"),
        ("uuid_run", "control-plane run id"),
        ("runtime", "runtime_session_id"),
        ("shared_runtime", "runtime_session_id"),
        ("provider", "recorded=claude requested=codex"),
        ("root", "different repository checkout"),
    ],
)
def test_read_only_native_selection_preserves_identity_guards(
    read_only_identity_storage, kind, message
):
    root = read_only_identity_storage
    native = "00000000-0000-4000-8000-000000000001"
    run_id = native if kind == "uuid_run" else "work-261011-000000-fixture"
    record(
        root,
        run_id,
        native,
        root / "sibling" if kind == "root" else root,
        agent="claude" if kind == "provider" else "codex",
    )
    token = run_id if kind in {"run_shape", "uuid_run"} else native
    if kind in {"runtime", "shared_runtime"}:
        path = root / "runtime_runs" / run_id / "meta.json"
        payload = json.loads(path.read_text())
        payload["runtime_session_id"] = native
        if kind == "runtime":
            payload["agent_session_id"] = "other-native-session"
        path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=message):
        workflow.resolve_session_selection("codex", token, root)


@pytest.mark.parametrize("corruption", ["unreadable", "mismatch", "transport"])
def test_read_only_identity_names_unavailable_canonical_facts(
    read_only_identity_storage, monkeypatch, corruption
):
    root = read_only_identity_storage
    token = "00000000-0000-4000-8000-000000000001"
    path = root / "runtime_runs" / token / "meta.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        "[]" if corruption == "unreadable" else json.dumps({"run_id": "different"})
    )
    if corruption == "transport":

        def unavailable(_):
            raise OSError("read failed")

        monkeypatch.setattr(workflow, "lookup_runtime_run_meta", unavailable)
    with pytest.raises(ValueError, match="identity facts unavailable"):
        workflow.resolve_session_selection("codex", token, root)


def test_read_only_identity_rejects_mismatched_record_for_native_token(
    read_only_identity_storage,
):
    root = read_only_identity_storage
    record(root, "work-recorded", "native-session", root)
    path = root / "runtime_runs/work-recorded/meta.json"
    payload = json.loads(path.read_text())
    payload["run_id"] = "work-other"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="identity facts unavailable"):
        workflow.resolve_session_selection("codex", "native-session", root)


def test_read_only_identity_does_not_fill_unknown_native_identity_from_projection(
    read_only_identity_storage, monkeypatch
):
    root = read_only_identity_storage
    run_id = "work-recorded"
    record(root, run_id, "", root)
    monkeypatch.setattr(
        workflow,
        "lookup_run_snapshot",
        lambda _: {
            "run_id": run_id,
            "agent": "codex",
            "agent_session_id": "stale-native",
            "session_id": "stale-native",
            "native_identity_status": "proven",
            "root": str(root),
        },
    )
    found = workflow.find_run_for_identity_token(run_id)
    assert found["agent_session_id"] == ""
    assert found["session_id"] == ""
    assert found["native_identity_status"] == ""
    assert found["agent"] == "codex"
    assert found["root"] == str(root)


def test_read_only_identity_retains_projected_runtime_namespace_guard(
    read_only_identity_storage, monkeypatch
):
    root = read_only_identity_storage
    run_id = "work-recorded"
    token = "00000000-0000-4000-8000-000000000001"
    record(root, run_id, token, root)
    monkeypatch.setattr(
        workflow,
        "lookup_run_snapshot",
        lambda target: (
            {"run_id": run_id, "runtime_session_id": token}
            if target == run_id
            else None
        ),
    )
    with pytest.raises(ValueError, match="runtime_session_id"):
        workflow.resolve_session_selection("codex", token, root)


@pytest.mark.parametrize("key", ["agent", "root"])
def test_read_only_identity_refuses_conflicting_projected_attribution(
    read_only_identity_storage, monkeypatch, key
):
    root = read_only_identity_storage
    run_id = "work-recorded"
    record(root, run_id, "native-session", root)
    projected = {"run_id": run_id, "agent": "codex", "root": str(root)}
    projected[key] = "claude" if key == "agent" else str(root / "sibling")
    monkeypatch.setattr(workflow, "lookup_run_snapshot", lambda _: projected)
    with pytest.raises(ValueError, match=f"conflicting recorded {key}"):
        workflow.find_run_for_identity_token(run_id)


@pytest.mark.parametrize("error", [OSError, RuntimeError])
def test_read_only_identity_names_unresolvable_recorded_root(
    read_only_identity_storage, monkeypatch, error
):
    root = read_only_identity_storage
    run_id = "work-recorded"
    record(root, run_id, "native-session", root)
    monkeypatch.setattr(
        workflow,
        "lookup_run_snapshot",
        lambda _: {"run_id": run_id, "agent": "codex", "root": str(root)},
    )

    def unavailable(_):
        raise error("resolve failed")

    monkeypatch.setattr(Path, "resolve", unavailable)
    with pytest.raises(ValueError, match="identity facts unavailable: recorded root"):
        workflow.find_run_for_identity_token(run_id)


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("payload", ["[]", '{"run_id": "different"}', "{"])
def test_read_only_identity_names_invalid_existing_snapshot(
    read_only_identity_storage, archived, payload
):
    root = read_only_identity_storage
    token = "00000000-0000-4000-8000-000000000001"
    directory = root / "runs" / "archive" if archived else root / "runs"
    directory.mkdir(parents=True)
    (directory / f"{token}.json").write_text(payload)
    with pytest.raises(
        ValueError, match="identity facts unavailable: invalid run snapshot"
    ):
        workflow.resolve_session_selection("codex", token, root)


def test_unrecorded_exact_native_identity_remains_unattributed(
    read_only_identity_storage,
):
    root = read_only_identity_storage
    selected = workflow.resolve_session_selection("codex", "native-session", root)
    assert selected["agent_session_id"] == "native-session"
    assert selected["source_run_id"] == ""
    assert selected["source_root"] == ""


def test_run_id_action_still_uses_fresh_lifecycle_lookup(
    read_only_identity_storage, monkeypatch
):
    root = read_only_identity_storage
    run_id = "work-261011-000000-fixture"
    record(root, run_id, "old-native", root)
    fresh = {
        "run_id": run_id,
        "agent": "codex",
        "agent_session_id": "fresh-native",
        "root": str(root),
    }
    calls = []

    def lookup(token):
        calls.append(token)
        return fresh

    monkeypatch.setattr(workflow, "lookup_run", lookup)
    monkeypatch.setattr(workflow, "_native_resume_meta", lambda *_: {})
    selected = workflow.resolve_fork_source("codex", run_id=run_id)
    assert selected["accepted"]
    assert selected["agent_session_id"] == "fresh-native"
    assert calls == [run_id]


def test_last_includes_native_history_without_laundering_unknown_run(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(workflow, "control_plane_home", lambda: tmp_path)
    monkeypatch.setattr(workflow, "lookup_run", lambda _: None)
    unknown = {
        "run_id": "rsme-unknown",
        "agent": "claude",
        "root": str(tmp_path),
        "provider_session_id": "native-catalog-session",
        "agent_session_id": "",
        "native_identity_status": "unknown",
    }
    path = tmp_path / "runtime_runs/rsme-unknown/meta.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(unknown))
    before = path.read_bytes()
    monkeypatch.setattr(
        spawn,
        "native_catalog_session_candidates",
        lambda agent, root: [
            {
                "session_id": "native-catalog-session",
                "run_id": "",
                "root": str(root),
                "timestamp": 100.0,
                "identity_source": "native_provider_catalog",
            }
        ],
    )
    selected = workflow.resolve_session_selection("claude", "last", tmp_path)
    assert selected["agent_session_id"] == "native-catalog-session"
    assert selected["source_run_id"] == ""
    assert selected["identity_source"] == "native_provider_catalog_latest_updated"
    assert path.read_bytes() == before


def test_last_merges_recorded_and_native_sessions_and_refuses_distinct_tie(
    tmp_path, monkeypatch
):
    from datetime import datetime

    monkeypatch.setattr(workflow, "control_plane_home", lambda: tmp_path)
    monkeypatch.setattr(workflow, "lookup_run", lambda _: None)
    record(tmp_path, "older", "tracked", tmp_path)
    stamp = datetime.fromisoformat("2026-09-10T09:00:00+00:00").timestamp()
    rows = [{"session_id": "native", "timestamp": stamp}]
    monkeypatch.setattr(spawn, "native_catalog_session_candidates", lambda *_: rows)
    selected = workflow.resolve_session_selection("codex", "last", tmp_path)
    assert selected["agent_session_id"] == "native"
    assert selected["source_run_id"] == ""
    rows.append({"session_id": "native", "timestamp": stamp})
    assert (
        workflow.resolve_session_selection("codex", "last", tmp_path)[
            "agent_session_id"
        ]
        == "native"
    )
    rows.append({"session_id": "other", "timestamp": stamp})
    with pytest.raises(ValueError, match="ambiguous"):
        workflow.resolve_session_selection("codex", "last", tmp_path)


def test_last_cannot_claim_old_receipt_is_latest_when_catalog_is_unavailable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(workflow, "control_plane_home", lambda: tmp_path)
    monkeypatch.setattr(workflow, "lookup_run", lambda _: None)
    record(tmp_path, "older", "tracked", tmp_path)

    def unavailable(*_):
        raise ValueError("native session catalog unavailable: retrieval_unavailable")

    monkeypatch.setattr(spawn, "native_catalog_session_candidates", unavailable)
    with pytest.raises(ValueError, match="native session catalog unavailable"):
        workflow.resolve_session_selection("codex", "last", tmp_path)


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
    monkeypatch.setattr(workflow, "find_run_for_identity_token", lambda _, **kw: None)
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
    monkeypatch.setattr(workflow, "find_run_for_identity_token", lambda _, **kw: None)
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


@pytest.mark.parametrize("recorded_source", [False, True])
@pytest.mark.parametrize("selector", ["source-native", "current", "last"])
def test_public_task_forks_are_independent_tracked_native_commands(
    tmp_path, recorded_source, selector
):
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
        CODEX_THREAD_ID="source-native",
    )
    repo_args = ["--repo", str(repo)]
    if recorded_source or selector == "last":
        record(
            tmp_path / "vc" / "control_plane",
            "work-source",
            "source-native",
            repo,
            stamp="2099-01-01T00:00:00Z",
        )
        if recorded_source and selector == "source-native":
            repo_args = []
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
                selector,
                *repo_args,
                "--file",
                str(plan),
            ],
            env=env,
            cwd=repo if selector in {"current", "last"} else root,
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
    for meta in metas:
        selection = meta["session_selection"]
        assert selection["session_selector"] == selector
        assert selection["selection_root"] == str(repo.resolve())
        assert selection["agent_session_id"] == "source-native"
        assert (
            selection["identity_source"]
            == {
                "current": "explicit_parent_context",
                "last": "repository_provider_latest_started",
                "source-native": "explicit_session",
            }[selector]
        )


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


def test_public_resume_help_has_one_session_selector():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["bash", str(root / "scripts/vibecrafted"), "resume", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert "--session <id|current|last>" in result.stdout
    assert "--last" not in result.stdout
    assert "--fork-session" not in result.stdout


@pytest.mark.parametrize("confirmed", [False, True])
def test_bare_interactive_fork_never_publishes_requested_uuid(
    tmp_path, monkeypatch, confirmed
):
    import os

    from vibecrafted_core.spawn import launch_interactive_workspace

    repo = tmp_path / "repo"
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
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "argv"
    provider = fake_bin / "codex"
    provider.write_text("""#!/bin/sh
case "$*" in
  *--help*) echo 'exec resume fork'; exit 0;;
  *--version*) echo 'codex-cli fixture'; exit 0;;
esac
printf '%s\\n' "$@" > "$SMOKE_CAPTURE"
exit 0
""")
    provider.chmod(0o755)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "vc"))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(fake_bin))
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("SMOKE_CAPTURE", str(capture))
    child_id = "20000000-0000-4000-8000-000000000002"
    if confirmed:
        from vibecrafted_core.continuity import native_fork

        monkeypatch.setattr(
            native_fork,
            "confirm_codex_native_fork",
            lambda **kwargs: {
                "agent_session_id": child_id,
                "provider_session_id": child_id,
                "native_identity_status": "confirmed",
                "native_identity_evidence": {
                    "child_id": child_id,
                    "forked_from_id": "source-native",
                    "request_id": kwargs["run_id"] + ":fork",
                },
            },
        )
    result = launch_interactive_workspace(
        "codex",
        "/vc-fork",
        "local-native",
        "bypass",
        repo,
        "unmetered",
        continuity="bare-fork",
        parent_session_id="source-native",
        admission={"model_requested": "fixture-model", "skill": "fork"},
    )
    receipt = json.loads(
        next(
            (tmp_path / "vc/control_plane/runtime_runs").glob("*/meta.json")
        ).read_text()
    )
    assert receipt["agent_session_id"] == (child_id if confirmed else "")
    assert receipt["provider_session_id"] == (child_id if confirmed else "")
    assert workflow._provider_session_for_continue(receipt) == (
        child_id if confirmed else ""
    )
    assert receipt["provider_session_requested"] == ""
    assert receipt["fork_source_session_id"] == "source-native"
    assert receipt["native_identity_status"] == (
        "confirmed" if confirmed else "pending"
    )
    assert receipt["terminal_reason"] == (
        "provider_exit_zero" if confirmed else "native_fork_identity_unconfirmed"
    )
    assert result == (0 if confirmed else 1)
    projected = json.loads(
        (tmp_path / "vc/control_plane/runs" / (receipt["run_id"] + ".json")).read_text()
    )
    assert projected["native_identity_status"] == (
        "confirmed" if confirmed else "pending"
    )
    assert projected["fork_source_session_id"] == "source-native"
    assert workflow._provider_session_for_continue(projected) == (
        child_id if confirmed else ""
    )
    argv = capture.read_text().splitlines()
    assert ("resume" if confirmed else "app-server") in argv
    if confirmed:
        assert argv[argv.index("resume") + 1] == child_id
        assert argv[argv.index("-m") + 1] == "fixture-model"
        assert "source-native" not in argv
        assert "/vc-fork" not in argv
        assert (
            projected["native_identity_evidence"] == receipt["native_identity_evidence"]
        )


@pytest.mark.parametrize("provider", ["claude", "grok"])
def test_bare_interactive_fork_sends_no_synthetic_task_marker(
    tmp_path, monkeypatch, provider
):
    """A bare fork carries no task input. Only the codex branch dropped the
    shell's synthetic /vc-fork marker; claude and grok children opened on a
    private task file that said nothing but "/vc-fork" and spent their first
    turn asking what to do."""
    import os

    from vibecrafted_core.spawn import launch_interactive_workspace

    repo = tmp_path / "repo"
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
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "argv"
    fake = fake_bin / provider
    fake.write_text("""#!/bin/sh
case "$*" in
  *--help*) echo '--resume --fork-session --print --prompt-file --session-id'; exit 0;;
  *--version*) echo '9.9.9 (fixture)'; exit 0;;
esac
printf '%s\\n' "$@" > "$SMOKE_CAPTURE"
exit 0
""")
    fake.chmod(0o755)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "vc"))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(fake_bin))
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("SMOKE_CAPTURE", str(capture))

    result = launch_interactive_workspace(
        provider,
        "/vc-fork",
        "local-native",
        "bypass",
        repo,
        "unmetered",
        continuity="bare-fork",
        parent_session_id="source-native",
        admission={"skill": "fork"},
    )

    # The fixture exits without ever acknowledging a child identity, so the run
    # settles as an unconfirmed native fork; the argv below is what is pinned.
    receipt = json.loads(
        next(
            (tmp_path / "vc/control_plane/runtime_runs").glob("*/meta.json")
        ).read_text()
    )
    assert result == 1
    assert receipt["fork_source_session_id"] == "source-native"
    assert receipt["native_identity_status"] == "pending"
    argv = capture.read_text().splitlines()
    assert argv[argv.index("--resume") + 1] == "source-native"
    assert "--fork-session" in argv
    assert "/vc-fork" not in argv
    assert not any("private task file" in item for item in argv), argv


@pytest.mark.parametrize("selector", ["source-native", "current", "last"])
def test_bare_resume_admission_retains_original_selection(
    tmp_path, monkeypatch, selector
):
    from vibecrafted_core.spawn import interactive_workspace_command

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
    monkeypatch.chdir(tmp_path)
    for key in (
        "CODEX_SESSION_ID",
        "VIBECRAFTED_AGENT_SESSION_ID",
        "VIBECRAFTED_RUN_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "source-native")
    record(workflow.control_plane_home(), "work-source", "source-native", tmp_path)
    command = interactive_workspace_command(
        "codex",
        "",
        "local-native",
        "bypass",
        tmp_path,
        "unmetered",
        skill="resume",
        native_session=selector,
    )
    admission = json.loads(
        Path(command[command.index("--admission-file") + 1]).read_text()
    )
    assert admission["agent_session_id"] == "source-native"
    assert admission["session_selection"]["session_selector"] == selector
    assert admission["session_selection"]["selection_root"] == str(tmp_path)


def test_selection_handoff_does_not_reselect_last(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "control_plane_home", lambda: tmp_path)
    monkeypatch.setattr(workflow, "lookup_run", lambda _: None)
    record(tmp_path, "one", "source", tmp_path)
    selection = workflow.resolve_session_selection("codex", "last", tmp_path)
    record(tmp_path, "two", "newer", tmp_path, stamp="2027-01-01T00:00:00Z")
    retained = workflow.resolve_session_selection(
        "codex", "source", tmp_path, selection=selection
    )
    assert retained == selection
    with pytest.raises(ValueError, match="does not match"):
        workflow.resolve_session_selection(
            "codex", "newer", tmp_path, selection=selection
        )


def test_exact_resume_infers_recorded_checkout_only_when_defaulted(
    read_only_identity_storage,
):
    home = read_only_identity_storage
    caller = home / "caller"
    checkout = home / "worker"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    record(home, "source", "native-session", checkout, agent="claude")
    record(home, "continued", "native-session", checkout, agent="claude")
    selected = workflow.resolve_session_selection(
        "claude", "native-session", caller, infer_recorded_root=True
    )
    assert selected["selection_root"] == str(checkout)
    assert selected["agent_session_id"] == "native-session"
    with pytest.raises(ValueError, match="different repository checkout"):
        workflow.resolve_session_selection("claude", "native-session", caller)
    assert (
        workflow.resolve_session_selection(
            "claude", "native-session", checkout, selection=selected
        )
        == selected
    )
    for name in ("source", "continued"):
        record(home, name, "native-session", caller, agent="claude")
    with pytest.raises(ValueError, match="different repository checkout"):
        workflow.resolve_session_selection(
            "claude", "native-session", checkout, selection=selected
        )


@pytest.mark.parametrize("failure", ["unknown", "missing", "nongit", "conflicting"])
def test_default_exact_resume_refuses_unproven_checkout(
    read_only_identity_storage, failure
):
    home = read_only_identity_storage
    checkout = home / "worker"
    if failure != "missing":
        checkout.mkdir()
    if failure != "unknown":
        record(home, "source", "native-session", checkout, agent="claude")
    if failure == "conflicting":
        record(home, "other", "native-session", home / "sibling", agent="claude")
    message = {
        "unknown": "no recorded repository checkout",
        "missing": "not an existing directory",
        "nongit": "not inside a Git repository",
        "conflicting": "conflicting recorded session checkouts",
    }[failure]
    with pytest.raises(ValueError, match=message):
        workflow.resolve_session_selection(
            "claude", "native-session", home, infer_recorded_root=True
        )


@pytest.mark.parametrize("verb", ["resume-session", "workflow"])
def test_cli_exact_resume_defaults_to_native_checkout(
    read_only_identity_storage, monkeypatch, verb
):
    from vibecrafted_core import cli

    home = read_only_identity_storage
    caller, checkout = home / "caller", home / "worker"
    for path in (caller, checkout):
        path.mkdir()
        subprocess.run(["git", "init", "-q", str(path)], check=True)
    monkeypatch.chdir(caller)
    record(home, "source", "native-session", checkout, agent="claude")
    captured = []

    def launch(*args, **kwargs):
        captured.append(kwargs)
        return {"accepted": True}

    monkeypatch.setattr(cli, "manual_resume_session", launch)
    identity = "--agent-session-id" if verb == "resume-session" else "--session"
    argv = [verb, "claude", identity, "native-session", "--prompt", "fixture", "--json"]
    assert cli.main(argv) == 0
    assert len(captured) == 1
    assert Path(captured[0]["root"]) == checkout
    assert captured[0]["launch_meta"]["session_selection"]["selection_root"] == str(
        checkout
    )
    assert cli.main([*argv, "--repo", str(caller)]) == 2
    assert len(captured) == 1


@pytest.mark.parametrize("selector", ["current", "last"])
def test_default_checkout_inference_keeps_current_last_scoped(
    read_only_identity_storage, selector
):
    home = read_only_identity_storage
    record(home, "source", "native-session", home / "worker", agent="claude")
    message = (
        "different repository checkout"
        if selector == "current"
        else "no recorded native session"
    )
    with pytest.raises(ValueError, match=message):
        workflow.resolve_session_selection(
            "claude",
            selector,
            home,
            environment={"CLAUDE_CODE_SESSION_ID": "native-session"},
            infer_recorded_root=True,
        )


def test_default_checkout_checks_conflicting_record_older_than_lookup_window(
    read_only_identity_storage,
):
    import os

    home = read_only_identity_storage
    checkout = home / "worker"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    record(home, "old", "native-session", home / "other", agent="claude")
    os.utime(home / "runtime_runs/old/meta.json", (1, 1))
    for index in range(301):
        record(home, f"unrelated-{index}", f"other-{index}", home)
    record(home, "source", "native-session", checkout, agent="claude")
    with pytest.raises(ValueError, match="conflicting recorded session checkouts"):
        workflow.resolve_session_selection(
            "claude", "native-session", home, infer_recorded_root=True
        )


def test_default_checkout_refuses_conflicting_providers_for_same_native_id(
    read_only_identity_storage,
):
    home = read_only_identity_storage
    record(home, "older", "native-session", home, agent="codex")
    record(home, "newer", "native-session", home, agent="claude")
    with pytest.raises(ValueError, match="conflicting recorded session providers"):
        workflow.resolve_session_selection(
            "claude", "native-session", home, infer_recorded_root=True
        )


def test_default_checkout_does_not_restore_blank_canonical_root_from_older_record(
    read_only_identity_storage,
):
    home = read_only_identity_storage
    record(home, "older", "native-session", home, agent="claude")
    record(home, "newer", "native-session", home, agent="claude")
    meta = home / "runtime_runs/newer/meta.json"
    payload = json.loads(meta.read_text())
    payload["root"] = ""
    meta.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="no recorded repository checkout"):
        workflow.resolve_session_selection(
            "claude", "native-session", home, infer_recorded_root=True
        )


@pytest.mark.parametrize("namespace", ["runtime_session_id", "vibecrafted_session_id"])
def test_default_checkout_refuses_older_runtime_alias_for_same_native_id(
    read_only_identity_storage, namespace
):
    home = read_only_identity_storage
    subprocess.run(["git", "init", "-q", str(home)], check=True)
    record(home, "older", "different-native", home, agent="claude")
    meta = home / "runtime_runs/older/meta.json"
    payload = json.loads(meta.read_text())
    payload[namespace] = "native-session"
    meta.write_text(json.dumps(payload))
    record(home, "newer", "native-session", home, agent="claude")
    with pytest.raises(ValueError, match="runtime_session_id"):
        workflow.resolve_session_selection(
            "claude", "native-session", home, infer_recorded_root=True
        )


def test_bare_fork_environment_keeps_transport_but_drops_parent_identity():
    from vibecrafted_core.spawn import ContinuityPolicy, _fresh_child_environment

    source = {
        name: "source-native"
        for name in (
            "CODEX_THREAD_ID",
            "CODEX_SESSION_ID",
            "CLAUDE_CODE_SESSION_ID",
            "GROK_SESSION_ID",
            "VIBECRAFTED_AGENT_SESSION_ID",
        )
    }
    source["CODEX_REMOTE"] = "unix:///isolated/app-server.sock"
    child = _fresh_child_environment(
        source,
        ContinuityPolicy(
            mode="bare-fork",
            lineage_id="source-native",
            parent_provider_session_id="source-native",
        ),
    )
    # The visible-child contract now adds terminal color defaults. Continue
    # asserting the complete environment so no parent identity can slip through.
    assert child == {
        "CODEX_REMOTE": source["CODEX_REMOTE"],
        "TERM": "xterm-256color",
        "FORCE_COLOR": "2",
        "CLICOLOR": "1",
        "CLICOLOR_FORCE": "1",
    }
    assert source["CODEX_THREAD_ID"] == "source-native"
