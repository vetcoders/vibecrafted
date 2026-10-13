"""Real nested executors exercise the canonical identity and recipient inbox."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest
from test_cli_execution_controls import _git_repo
from test_workflow_runtime import _fake_agent, _fake_kimi, _runtime_env
from vibecrafted_core import control_plane, message_control, workflow_runtime


def test_exact_nested_child_refusal_regression(monkeypatch, tmp_path):
    home = _runtime_env(monkeypatch, tmp_path, "parent")
    _fake_kimi(tmp_path / "bin")
    result = asyncio.run(
        workflow_runtime._run_child(
            kind="marbles",
            label="marbles-L2",
            agent="kimi",
            root=str(tmp_path),
            prompt="unchanged task",
        )
    )
    assert message_control.resolve_message_run(run_id=result.run_id) == result.run_id
    assert (
        message_control.resolve_message_run(session=result.agent_session_id)
        == result.run_id
    )
    meta = home / "control_plane/runtime_runs" / result.run_id / "meta.json"
    record = json.loads(meta.read_text())
    assert record["agent_session_id"] == result.agent_session_id
    assert record["parent_run_id"] == "parent"
    assert record["root"] == str(tmp_path.resolve())
    assert record["status"] == "completed"
    assert json.loads(result.report.with_suffix(".meta.json").read_text()) == record
    assert control_plane.resolve_run(result.run_id).transcript == result.transcript
    artifacts = [meta, result.report, result.report.with_suffix(".prompt.md")]
    original = [path.read_bytes() for path in artifacts]
    with pytest.raises(ValueError, match="child_run_already_registered"):
        asyncio.run(
            workflow_runtime._run_child(
                kind="marbles",
                label="marbles-L2",
                agent="kimi",
                root=str(tmp_path),
                prompt="must not replace the executor or its artifacts",
            )
        )
    assert [path.read_bytes() for path in artifacts] == original


def _cli(*args):
    args = list(args)
    body_path = None
    if "--text" in args:
        index = args.index("--text")
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as body:
            body.write(args[index + 1])
            body_path = Path(body.name)
        args[index : index + 2] = ["--file", body.name]
        args.append("--json")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "vibecrafted_core.cli", *args],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    finally:
        if body_path is not None:
            body_path.unlink()
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


async def _until(predicate):
    async with asyncio.timeout(15):
        while not predicate():
            await asyncio.sleep(0.02)


def test_live_children_route_run_native_and_pre_native_inboxes(monkeypatch, tmp_path):
    home = _runtime_env(monkeypatch, tmp_path, "parent")
    parent = home / "control_plane/runtime_runs/parent/meta.json"
    parent.parent.mkdir(parents=True)
    parent.write_text(
        json.dumps(
            {"run_id": "parent", "agent": "kimi", "agent_session_id": "parent-native"}
        )
    )
    # An inherited provider id must never become the child's native identity.
    monkeypatch.setenv("VIBECRAFTED_AGENT_SESSION_ID", "parent-native")
    fixture = tmp_path / "recipient.py"
    fixture.write_text(
        textwrap.dedent("""\
        import json, os, subprocess, sys, time
        from pathlib import Path
        import yaml
        sys.stdin.read()
        run = os.environ['VIBECRAFTED_RUN_ID']
        exchange = Path(os.environ['EXCHANGE']) / run
        exchange.mkdir(parents=True)
        (exchange / 'ready').touch()
        while not (exchange / 'native').exists(): time.sleep(.02)
        print('[12:00:00] session: ' + run + '-native', flush=True)
        while not (exchange / 'receive').exists(): time.sleep(.02)
        def cli(*args):
            p = subprocess.run([sys.executable, '-m', 'vibecrafted_core.cli',
                                'message', '--run-id', run, *args],
                               text=True, capture_output=True, check=True)
            return json.loads(p.stdout)
        rows = cli('--receive')
        (exchange / 'received.json').write_text(json.dumps(rows))
        for row in rows: cli('--ack', row['message_id'])
        report = Path(os.environ['VIBECRAFTED_REPORT_PATH'])
        _, front, body = report.read_text().split('---', 2)
        header = yaml.safe_load(front)
        header.update(status='completed', finalized=True, claim='recipient checkpoint verified')
        report.write_text('---\\n' + yaml.safe_dump(header) + '---\\n' + body)
        print('recipient settled', flush=True)
        """)
    )
    queue = tmp_path / "bin/codex"
    queue.write_text(
        f"#!{sys.executable}\nimport json,os,sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[1] == 'queue'\n"
        "Path(os.environ['QUEUE_CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    queue.chmod(0o755)
    monkeypatch.setenv("EXCHANGE", str(tmp_path / "exchange"))
    monkeypatch.setenv("QUEUE_CAPTURE", str(tmp_path / "queue.json"))
    # Fixture-specific transport is explicitly allowed; no real provider runs.
    from vibecrafted_core import supervisor_async

    original_filter = supervisor_async.filter_headless_worker_env
    monkeypatch.setattr(
        supervisor_async,
        "filter_headless_worker_env",
        lambda env: {**original_filter(env), "EXCHANGE": env["EXCHANGE"]},
    )
    roots = [tmp_path / f"source-{i}" for i in range(3)]
    for root in roots:
        _git_repo(root)
    baselines = [workflow_runtime.tree_receipt(str(root)) for root in roots]

    async def exercise():
        tasks = [
            asyncio.create_task(
                workflow_runtime._run_child(
                    kind="marbles-reception" if i == 2 else "marbles",
                    label=f"child-{i}",
                    agent="kimi" if i else "codex",
                    root=str(root),
                    prompt="resource constraints bind READ too",
                    command=[sys.executable, str(fixture)],
                )
            )
            for i, root in enumerate(roots)
        ]
        try:
            ids = [f"parent-child-{i}" for i in range(3)]
            exchanges = [tmp_path / "exchange" / run for run in ids]
            await _until(lambda: all((p / "ready").exists() for p in exchanges))
            early = [
                _cli("message", "--run-id", run, "--text", f"early-{run}")
                for run in ids
            ]
            assert all(row["delivery_state"] == "inbox_pending" for row in early)
            assert all(not row["provider_session_id"] for row in early)
            for path in exchanges:
                (path / "native").touch()
            await _until(
                lambda: all(
                    control_plane.lookup_runtime_run_meta(run).get("agent_session_id")
                    == run + "-native"
                    for run in ids
                )
            )
            native = [
                _cli("message", "--session", run + "-native", "--text", f"native-{run}")
                for run in ids
            ]
            assert native[0]["delivery_state"] == "provider_accepted"
            assert all(row["delivery_state"] == "inbox_pending" for row in native[1:])
            assert json.loads((tmp_path / "queue.json").read_text()) == [
                "queue",
                "--thread",
                ids[0] + "-native",
                "--message=native-" + ids[0],
            ]
            parent_message = _cli(
                "message", "--run-id", "parent", "--text", "parent-only"
            )
            for path in exchanges:
                (path / "receive").touch()
            results = await asyncio.gather(*tasks)
            for run, path, result, first, second in zip(
                ids, exchanges, results, early, native, strict=True
            ):
                rows = json.loads((path / "received.json").read_text())
                assert {r["message_id"] for r in rows} == {
                    first["message_id"],
                    second["message_id"],
                }
                assert {r["run_id"] for r in rows} == {run}
                assert result.agent_session_id == run + "-native"
                assert message_control.receive_messages(run_id=run) == []
                assert result.exit_code == 0
            assert (
                message_control.receive_messages(run_id="parent")[0]["message_id"]
                == parent_message["message_id"]
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(exercise())
    assert [workflow_runtime.tree_receipt(str(root)) for root in roots] == baselines
    with pytest.raises(
        message_control.MessageControlError, match="session_not_resolved"
    ):
        message_control.resolve_message_run(session="missing-native")


@pytest.mark.parametrize("frontend", ["python", "deck"])
@pytest.mark.parametrize("selector", [["kimi"], ["--agent", "kimi"]])
def test_public_stop_frontends_select_exact_disposable_executor(
    monkeypatch, tmp_path, frontend, selector
):
    _runtime_env(monkeypatch, tmp_path, "parent-stop")
    root = tmp_path / "source"
    baseline = _git_repo(root)
    from vibecrafted_core import wrappers

    argv = (
        [sys.executable, "-m", "vibecrafted_core.cli"]
        if frontend == "python"
        else [str(wrappers.deck_path())]
    )
    monkeypatch.setenv("VIBECRAFTED_PYTHON", sys.executable)
    monkeypatch.setenv(
        "VIBECRAFTED_CORE_DIR", str(Path(workflow_runtime.__file__).parents[1])
    )
    fixture = tmp_path / "hold.py"
    fixture.write_text(
        "import time,sys\nsys.stdin.read()\nprint('[12:00:00] session: disposable-native', flush=True)\ntime.sleep(60)\n"
    )

    def invoke(*args):
        return subprocess.run(
            [*argv, *args], text=True, capture_output=True, timeout=20, check=False
        )

    async def exercise():
        task = asyncio.create_task(
            workflow_runtime._run_child(
                kind="marbles",
                label="hold",
                agent="kimi",
                root=str(root),
                prompt="bounded hold",
                command=[sys.executable, str(fixture)],
            )
        )
        run = "parent-stop-hold"
        try:
            await _until(
                lambda: (
                    (control_plane.lookup_runtime_run_meta(run) or {}).get(
                        "agent_session_id"
                    )
                    == "disposable-native"
                )
            )
            before = control_plane.lookup_runtime_run_meta(run)
            pid = before["worker_pid"]
            cases = [
                ([*selector, "--help"], 0),
                (["unknown", "--run-id", run], 2),
                (["codex", "--run-id", run], 1),
                ([*selector, "--run-id", run, "--session", "missing"], 2),
                (["kimi", "--agent", "codex", "--run-id", run], 2),
            ]
            for args, expected in cases:
                checked = invoke("stop", *args)
                assert checked.returncode == expected, checked.stdout + checked.stderr
                os.kill(pid, 0)
                assert control_plane.lookup_runtime_run_meta(run) == before
            (root / "drift.txt").write_text("concurrent source drift")
            stopped = invoke(
                "stop", *selector, "--run-id", run, "--grace-seconds", "0.2"
            )
            assert stopped.returncode == 0, stopped.stdout + stopped.stderr
            result = await asyncio.wait_for(task, 10)
            assert result.run_id == run
            record = control_plane.lookup_runtime_run_meta(run)
            assert record["agent_session_id"] == "disposable-native"
            assert record["baseline_sha"] == baseline
            assert record["terminal_sha"] == baseline
            assert record["status"] == "stopped"
            assert record["worker_identity"] == before["worker_identity"]
            assert (root / "drift.txt").read_text() == "concurrent source drift"
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


def test_real_shared_native_session_refuses_ambiguous_selector(monkeypatch, tmp_path):
    _runtime_env(monkeypatch, tmp_path, "ambiguous-parent")
    _fake_agent(tmp_path / "bin", "kimi")

    async def exercise():
        return [
            await workflow_runtime._run_child(
                kind="marbles",
                label=f"L{i}",
                agent="kimi",
                root=str(tmp_path),
                prompt="same native fixture",
                command=["kimi"],
            )
            for i in range(2)
        ]

    results = asyncio.run(exercise())
    assert results[0].agent_session_id == results[1].agent_session_id == "kimi-session"
    with pytest.raises(
        message_control.MessageControlError, match="session_ambiguous_use_run_id"
    ):
        message_control.resolve_message_run(session="kimi-session")
    for result in results:
        assert (
            message_control.resolve_message_run(
                run_id=result.run_id, session="kimi-session"
            )
            == result.run_id
        )


def test_failed_spawn_keeps_registered_terminal_identity(monkeypatch, tmp_path):
    _runtime_env(monkeypatch, tmp_path, "failed-parent")
    with pytest.raises(FileNotFoundError):
        asyncio.run(
            workflow_runtime._run_child(
                kind="marbles",
                label="missing",
                agent="kimi",
                root=str(tmp_path),
                prompt="bounded fixture",
                command=[str(tmp_path / "absent-executable")],
            )
        )
    record = control_plane.lookup_runtime_run_meta("failed-parent-missing")
    assert record["status"] == "failed" and record["liveness"] == "terminal"
    assert record["agent_session_id"] == ""
    assert record["terminal_reason"] == "child_launch_failed:FileNotFoundError"


def test_cancel_keeps_exact_terminal_child_record(monkeypatch, tmp_path):
    _runtime_env(monkeypatch, tmp_path, "cancel-parent")
    fixture = tmp_path / "hold.py"
    fixture.write_text(
        "import sys,time\nsys.stdin.read()\nprint('[12:00:00] session: cancelled-native', flush=True)\ntime.sleep(60)\n"
    )

    async def exercise():
        task = asyncio.create_task(
            workflow_runtime._run_child(
                kind="marbles",
                label="hold",
                agent="kimi",
                root=str(tmp_path),
                prompt="hold",
                command=[sys.executable, str(fixture)],
            )
        )
        run = "cancel-parent-hold"
        await _until(
            lambda: (
                (control_plane.lookup_runtime_run_meta(run) or {}).get(
                    "agent_session_id"
                )
                == "cancelled-native"
            )
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        record = control_plane.lookup_runtime_run_meta(run)
        assert record["status"] == "failed"
        assert record["terminal_reason"] == "supervisor_cancelled"
        assert record["agent_session_id"] == "cancelled-native"
        assert record["liveness"] == "terminal"
        assert message_control.resolve_message_run(session="cancelled-native") == run
        with pytest.raises(ProcessLookupError):
            os.kill(record["worker_pid"], 0)

    asyncio.run(exercise())
