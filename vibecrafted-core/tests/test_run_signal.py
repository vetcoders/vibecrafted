from __future__ import annotations

import datetime as dt
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path

from vibecrafted_core import control_plane, run_signal, server_observation
from vibecrafted_core.run_signal import RunSignalServer, wait_for_run_signal
from vibecrafted_core.runtime_paths import (
    classify_vibecrafted_home_child,
    run_signal_socket_path,
)


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
    home: Path | None = None,
    heartbeat_lines: int = 0,
) -> tuple[subprocess.Popen[str], Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    home = home or tmp_path / "home"
    meta = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    report = tmp_path / f"{run_id}.md"
    transcript = tmp_path / f"{run_id}.log"
    meta.parent.mkdir(parents=True, exist_ok=True)
    worker = (
        "import os,time; from pathlib import Path; "
        f"time.sleep({delay!r}); "
        f"[(print('pulse-' + str(i) + '-' + ('x' * 1024), flush=True)) for i in range({heartbeat_lines})]; "
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


def _wait_for_owner_token(path: Path, *, not_token: str = "") -> dict[str, object]:
    owner_path = path.with_suffix(".owner.json")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.01)
            continue
        if owner.get("start_token") and owner.get("start_token") != not_token:
            return owner
        time.sleep(0.01)
    raise AssertionError(f"owner token did not change: {owner_path}")


def test_run_signal_fans_out_and_replays_terminal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    run_id = "signal-replay"
    with RunSignalServer(run_id) as server:
        results: list[dict[str, object]] = []
        clients = [
            threading.Thread(
                target=lambda: results.append(wait_for_run_signal(run_id)), daemon=True
            )
            for _ in range(3)
        ]
        for client in clients:
            client.start()
        server.heartbeat("active")
        server.terminal(state="completed", report="report.md", exit_code=0)
        for client in clients:
            client.join(timeout=1)
        replay = wait_for_run_signal(run_id)

    assert len(results) == 3
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


def test_sigkill_wakes_await_on_eof_and_stale_socket_is_absent(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "signal-dispatcher-killed"
    process, _meta, _report = _dispatcher(tmp_path, run_id, delay=0.5)
    socket_path = run_signal_socket_path(run_id)
    _wait_for(socket_path)
    accepted = threading.Event()
    real_wait_for_run_signal = wait_for_run_signal

    def synchronized_wait(run_id: str, *, timeout: float | None = None):
        return real_wait_for_run_signal(
            run_id,
            timeout=timeout,
            _on_event=lambda _event: accepted.set(),
        )

    monkeypatch.setattr(control_plane, "wait_for_run_signal", synchronized_wait)
    results: list[dict[str, object]] = []
    client = threading.Thread(
        target=lambda: results.append(
            control_plane.await_run(run_id, timeout_seconds=0)
        ),
        daemon=True,
    )
    client.start()
    assert accepted.wait(timeout=5), (
        "await client never received a valid dispatcher event"
    )
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=5)
    client.join(timeout=5)

    assert not client.is_alive()
    assert len(results) == 1
    result = results[0]
    assert result["completed"] is False
    assert result["signal_kind"] == "eof"
    assert result["reason"] in {"signal_invalidated", "signal_invalidated_live"}
    # SIGKILL cannot unlink; a refused orphan is treated as missing, never hung.
    started = time.monotonic()
    assert wait_for_run_signal(run_id)["kind"] == "missing"
    assert time.monotonic() - started < 0.1
    socket_path.unlink(missing_ok=True)
    time.sleep(0.55)  # the test-owned worker exits without being signalled


def test_connection_reset_is_an_eof_wake(monkeypatch) -> None:
    class ResetClient:
        def settimeout(self, _timeout: float) -> None:
            pass

        def connect(self, _path: str) -> None:
            pass

        def recv(self, _read_size: int) -> bytes:
            raise ConnectionResetError("dispatcher died before accept")

        def close(self) -> None:
            pass

    monkeypatch.setattr(run_signal.socket, "socket", lambda *_args: ResetClient())
    monkeypatch.setattr(
        run_signal,
        "run_signal_socket_path",
        lambda _run_id: Path("/tmp/vc-run-signal-reset.sock"),
    )

    assert run_signal.wait_for_run_signal("signal-reset", timeout=1) == {
        "kind": "eof",
        "run_id": "signal-reset",
    }


def test_connect_timeout_preserves_hard_cap_outcome(monkeypatch) -> None:
    class TimeoutClient:
        def settimeout(self, _timeout: float) -> None:
            pass

        def connect(self, _path: str) -> None:
            raise TimeoutError("connect deadline expired")

        def close(self) -> None:
            pass

    monkeypatch.setattr(run_signal.socket, "socket", lambda *_args: TimeoutClient())
    monkeypatch.setattr(
        run_signal,
        "run_signal_socket_path",
        lambda _run_id: Path("/tmp/vc-run-signal-timeout.sock"),
    )

    assert run_signal.wait_for_run_signal("signal-timeout", timeout=1) == {
        "kind": "timeout",
        "run_id": "signal-timeout",
    }


def test_identity_change_notifies_valid_event_observer(monkeypatch) -> None:
    first = {
        "schema": run_signal.SCHEMA,
        "run_id": "signal-replaced",
        "kind": "heartbeat",
        "dispatcher_pid": 100,
        "start_token": "first",
    }
    replacement = {
        **first,
        "dispatcher_pid": 200,
        "start_token": "replacement",
    }
    payload = (
        json.dumps(first).encode() + b"\n" + json.dumps(replacement).encode() + b"\n"
    )

    class ReplacedClient:
        def connect(self, _path: str) -> None:
            pass

        def recv(self, _read_size: int) -> bytes:
            nonlocal payload
            chunk, payload = payload, b""
            return chunk

        def close(self) -> None:
            pass

    observed: list[Mapping[str, object]] = []
    monkeypatch.setattr(run_signal.socket, "socket", lambda *_args: ReplacedClient())
    monkeypatch.setattr(
        run_signal,
        "run_signal_socket_path",
        lambda _run_id: Path("/tmp/vc-run-signal-replaced.sock"),
    )

    result = run_signal.wait_for_run_signal(
        "signal-replaced", _on_event=observed.append
    )

    assert result == {
        "kind": "identity_changed",
        "run_id": "signal-replaced",
        "dispatcher_pid": 200,
        "start_token": "replacement",
        "previous_dispatcher_pid": 100,
        "previous_start_token": "first",
    }
    assert observed == [first, replacement]


def test_dispatcher_sigterm_unlinks_socket(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    run_id = "signal-dispatcher-term"
    process, _meta, _report = _dispatcher(tmp_path, run_id, delay=0.3)
    socket_path = run_signal_socket_path(run_id)
    _wait_for_owner_token(socket_path)
    accepted = threading.Event()
    results: list[dict[str, object]] = []
    client = threading.Thread(
        target=lambda: results.append(
            wait_for_run_signal(
                run_id,
                _on_event=lambda _event: accepted.set(),
            )
        ),
        daemon=True,
    )
    client.start()
    assert accepted.wait(timeout=5), (
        "signal client never received a valid dispatcher event"
    )
    process.terminate()
    process.wait(timeout=5)
    client.join(timeout=5)
    assert not client.is_alive()
    assert len(results) == 1
    result = results[0]
    assert result["kind"] == "eof"
    assert result["run_id"] == run_id
    assert result["dispatcher_pid"] == process.pid
    assert isinstance(result["start_token"], str)
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


def test_real_dispatcher_drops_backpressured_client_without_stalling_terminal(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "signal-backpressure-drop"
    process, _meta, _report = _dispatcher(
        tmp_path, run_id, delay=0.25, heartbeat_lines=1000
    )
    socket_path = run_signal_socket_path(run_id)
    slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    try:
        _wait_for(socket_path)
        slow.connect(str(socket_path))
        result = control_plane.await_run(run_id, hard_cap_seconds=5)
        returned_at = dt.datetime.now(dt.timezone.utc)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        slow.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0, (stdout, stderr)
    assert "dropped run-signal client after backpressure" in stderr
    assert result["completed"] is True
    assert result["signal_kind"] == "terminal"
    signal_at = dt.datetime.fromisoformat(str(result["signal_ts"]))
    assert (returned_at - signal_at).total_seconds() < 0.1


def test_real_dispatcher_client_buffers_partial_jsonl_frame_byte_by_byte(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    run_id = "signal-partial-frame"
    process, _meta, _report = _dispatcher(tmp_path, run_id, delay=0.1)
    socket_path = run_signal_socket_path(run_id)
    try:
        _wait_for(socket_path)
        result = wait_for_run_signal(run_id, timeout=5, read_size=1)
        returned_at = dt.datetime.now(dt.timezone.utc)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0, (stdout, stderr)
    assert result["kind"] == "terminal"
    assert result["dispatcher_pid"] == process.pid
    assert result["start_token"]
    signal_at = dt.datetime.fromisoformat(str(result["ts"]))
    assert (returned_at - signal_at).total_seconds() < 0.1


def test_real_dispatcher_replacement_changes_identity_and_reclaims_dead_owner(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    run_id = "signal-identity-replacement"
    first, _meta, _report = _dispatcher(tmp_path, run_id, delay=0.8)
    socket_path = run_signal_socket_path(run_id)
    _wait_for(socket_path)
    first_owner = _wait_for_owner_token(socket_path)
    first_result: dict[str, object] = {}
    client = threading.Thread(
        target=lambda: first_result.update(wait_for_run_signal(run_id, timeout=5)),
        daemon=True,
    )
    client.start()
    time.sleep(0.05)
    os.kill(first.pid, signal.SIGKILL)
    first.wait(timeout=5)
    client.join(timeout=1)
    assert first_result["kind"] == "eof"
    assert first_result["start_token"] == first_owner["start_token"]

    replacement, _meta2, _report2 = _dispatcher(tmp_path, run_id, delay=0.1)
    replacement_owner = _wait_for_owner_token(
        socket_path, not_token=str(first_owner["start_token"])
    )
    try:
        verdict = control_plane.await_run(run_id, hard_cap_seconds=5)
        returned_at = dt.datetime.now(dt.timezone.utc)
        stdout, stderr = replacement.communicate(timeout=5)
    finally:
        if replacement.poll() is None:
            replacement.kill()
            replacement.wait(timeout=5)

    assert replacement.returncode == 0, (stdout, stderr)
    assert replacement_owner["dispatcher_pid"] == replacement.pid
    assert replacement_owner["start_token"] != first_owner["start_token"]
    assert verdict["completed"] is True
    assert verdict["signal_kind"] == "terminal"
    assert verdict["run"]["run_id"] == run_id
    signal_at = dt.datetime.fromisoformat(str(verdict["signal_ts"]))
    assert (returned_at - signal_at).total_seconds() < 0.1
    time.sleep(0.85)  # the first dispatcher-owned worker was intentionally orphaned


def test_real_dispatcher_refuses_foreign_live_socket_without_unlink(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    run_id = "signal-owner-check-unlink"
    socket_path = run_signal_socket_path(run_id)
    socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    foreign = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    foreign.bind(str(socket_path))
    foreign.listen()
    try:
        process, _meta, _report = _dispatcher(tmp_path, run_id, delay=0.1)
        _stdout, stderr = process.communicate(timeout=5)
        assert process.returncode != 0
        assert "refusing to unlink unowned/foreign run signal socket" in stderr
        assert socket_path.exists()
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(socket_path))
        finally:
            probe.close()
    finally:
        foreign.close()
        socket_path.unlink(missing_ok=True)


def test_real_dispatchers_long_home_use_distinct_mac_safe_sun_paths(
    monkeypatch, tmp_path: Path
) -> None:
    run_id = "signal-sun-path-long-home"
    home_a = tmp_path / ("a" * 140) / ("x" * 40)
    home_b = tmp_path / ("b" * 140) / ("x" * 40)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home_a))
    path_a = run_signal_socket_path(run_id)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home_b))
    path_b = run_signal_socket_path(run_id)
    assert len(os.fsencode(home_a / "control_plane" / f"{run_id}.sock")) > 104
    assert len(os.fsencode(path_a)) < 104
    assert len(os.fsencode(path_b)) < 104
    assert path_a != path_b

    first, _meta_a, _report_a = _dispatcher(
        tmp_path / "first", run_id, delay=0.1, home=home_a
    )
    try:
        monkeypatch.setenv("VIBECRAFTED_HOME", str(home_a))
        _wait_for(path_a)
        result_a = control_plane.await_run(run_id, hard_cap_seconds=5)
        returned_a = dt.datetime.now(dt.timezone.utc)
        first_output = first.communicate(timeout=5)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=5)

    second, _meta_b, _report_b = _dispatcher(
        tmp_path / "second", run_id, delay=0.1, home=home_b
    )
    try:
        monkeypatch.setenv("VIBECRAFTED_HOME", str(home_b))
        _wait_for(path_b)
        result_b = control_plane.await_run(run_id, hard_cap_seconds=5)
        returned_b = dt.datetime.now(dt.timezone.utc)
        second_output = second.communicate(timeout=5)
    finally:
        if second.poll() is None:
            second.kill()
            second.wait(timeout=5)

    assert first.returncode == 0, first_output
    assert second.returncode == 0, second_output
    assert result_a["completed"] is True
    assert result_b["completed"] is True
    signal_a = dt.datetime.fromisoformat(str(result_a["signal_ts"]))
    signal_b = dt.datetime.fromisoformat(str(result_b["signal_ts"]))
    assert (returned_a - signal_a).total_seconds() < 0.1
    assert (returned_b - signal_b).total_seconds() < 0.1


def test_united_runtime_paths_keep_uds_socket_and_home_child_classes(
    monkeypatch, tmp_path: Path
) -> None:
    """Merge union: UDS socket path and uninstall home classes share one module."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    assert classify_vibecrafted_home_child("control_plane") == "runtime-state"
    assert classify_vibecrafted_home_child("artifacts") == "founder-data"
    assert classify_vibecrafted_home_child("mystery-export") == "unknown"
    path = run_signal_socket_path("union-uds")
    assert len(os.fsencode(path)) < 104
    assert path.name.endswith(".sock")
