from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from vibecrafted_core import control_plane, server_observation
from vibecrafted_core.run_signal import RunSignalServer, wait_for_run_signal
from vibecrafted_core.runtime_paths import run_signal_socket_path


def _wait_for(path: Path, *, present: bool = True, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while path.exists() is not present and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists() is present


def _dispatcher(
    tmp_path: Path,
    run_id: str,
    *,
    delay: float = 0.15,
) -> tuple[subprocess.Popen[str], Path, Path]:
    home = tmp_path / "home"
    meta = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    report = tmp_path / f"{run_id}.md"
    transcript = tmp_path / f"{run_id}.log"
    meta.parent.mkdir(parents=True, exist_ok=True)
    worker = (
        "import os,time; from pathlib import Path; "
        f"time.sleep({delay!r}); "
        "Path(os.environ['VIBECRAFTED_REPORT_PATH']).write_text("
        "'---\\nrun_id: "
        + run_id
        + "\\nagent: python\\nskill: test\\nstatus: completed\\nclaim_status: completed\\nfinalized: true\\nclaim: uds-test\\n---\\nbody\\n', encoding='utf-8'); "
        "print('done')"
    )
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["VIBECRAFTED_HOME"] = str(home)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "vibecrafted_core.dispatcher",
            "run",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--meta",
            str(meta),
            "--report",
            str(report),
            "--transcript",
            str(transcript),
            "--quiet",
            "--",
            sys.executable,
            "-c",
            worker,
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process, meta, report


def test_run_signal_fans_out_and_replays_terminal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    run_id = "signal-replay"
    with RunSignalServer(run_id) as server:
        results: list[dict[str, object]] = []
        clients = [
            threading.Thread(
                target=lambda: results.append(wait_for_run_signal(run_id)), daemon=True
            )
            for _ in range(2)
        ]
        for client in clients:
            client.start()
        server.heartbeat("active")
        server.terminal(state="completed", report="report.md", exit_code=0)
        for client in clients:
            client.join(timeout=1)
        replay = wait_for_run_signal(run_id)

    assert len(results) == 2
    assert all(result["kind"] == "terminal" for result in results)
    assert replay["kind"] == "terminal"
    assert not run_signal_socket_path(run_id).exists()


def test_real_dispatcher_wakes_await_under_100ms_without_server(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "signal-real-terminal"
    process, meta, _report = _dispatcher(tmp_path, run_id)
    socket_path = run_signal_socket_path(run_id)
    try:
        _wait_for(socket_path)
        result = control_plane.await_run(run_id, hard_cap_seconds=5)
        returned_at = dt.datetime.now(dt.timezone.utc)
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    assert result["completed"] is True
    assert result["signal_kind"] == "terminal"
    signal_at = dt.datetime.fromisoformat(str(result["signal_ts"]))
    assert (returned_at - signal_at).total_seconds() < 0.1
    meta_body = json.loads(meta.read_text(encoding="utf-8"))
    assert meta_body["await_outcome"] == "completed"
    assert meta_body["exit_code"] == 0
    assert not socket_path.exists()


def test_sigkill_wakes_existing_client_on_eof_and_stale_socket_is_absent(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "signal-dispatcher-killed"
    process, _meta, _report = _dispatcher(tmp_path, run_id, delay=0.5)
    socket_path = run_signal_socket_path(run_id)
    _wait_for(socket_path)
    result: dict[str, object] = {}
    client = threading.Thread(
        target=lambda: result.update(control_plane.await_run(run_id)), daemon=True
    )
    client.start()
    time.sleep(0.05)
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=5)
    client.join(timeout=1)

    assert result["completed"] is True
    assert result["signal_kind"] == "eof"
    # SIGKILL cannot unlink; a refused orphan is treated as missing, never hung.
    started = time.monotonic()
    assert wait_for_run_signal(run_id)["kind"] == "missing"
    assert time.monotonic() - started < 0.1
    socket_path.unlink(missing_ok=True)
    time.sleep(0.55)  # the test-owned worker exits without being signalled


def test_dispatcher_sigterm_unlinks_socket(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    run_id = "signal-dispatcher-term"
    process, _meta, _report = _dispatcher(tmp_path, run_id, delay=0.3)
    socket_path = run_signal_socket_path(run_id)
    _wait_for(socket_path)
    process.terminate()
    process.wait(timeout=5)
    _wait_for(socket_path, present=False)
    time.sleep(0.35)  # the test-owned worker exits without being signalled


def test_await_ignores_missing_server_and_deck_verb(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "signal-no-server-deck"
    meta = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "completed",
                "exit_code": 0,
                "liveness": "terminal",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        server_observation,
        "_request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("await must not call vc-server or its deck subprocess")
        ),
    )

    result = server_observation.await_run(
        run_id,
        idle_timeout_seconds=0,
        hard_cap_seconds=None,
        interval_seconds=5,
    )

    assert result["completed"] is True
    assert result["signal_kind"] == "missing"
