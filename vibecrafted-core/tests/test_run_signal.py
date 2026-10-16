from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path

import pytest
from vibecrafted_core import (
    control_plane,
    process_control,
    run_signal,
    server_observation,
)
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
    gate: Path | None = None,
) -> tuple[subprocess.Popen[str], Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    home = home or tmp_path / "home"
    meta = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    report = tmp_path / f"{run_id}.md"
    transcript = tmp_path / f"{run_id}.log"
    meta.parent.mkdir(parents=True, exist_ok=True)
    # ``gate`` replaces the fixed sleep with a release the test grants once it
    # has proof of its precondition; the cap only bounds a test that never does.
    hold = (
        f"time.sleep({delay!r})\n"
        if gate is None
        else (
            f"_g = Path({str(gate)!r}); _t = time.monotonic() + 120\n"
            "while not _g.exists() and time.monotonic() < _t: time.sleep(0.05)\n"
        )
    )
    worker = (
        "import os,time; from pathlib import Path\n"
        + hold
        + f"[(print('pulse-' + str(i) + '-' + ('x' * 1024), flush=True)) for i in range({heartbeat_lines})]; "
        + "Path(os.environ['VIBECRAFTED_REPORT_PATH']).write_text("
        + "'---\\nrun_id: "
        + run_id
        + "\\nagent: python\\nskill: test\\nstatus: completed\\nclaim_status: completed\\nfinalized: true\\nclaim: uds-test\\n---\\nbody\\n', encoding='utf-8'); "
        + "print('done')"
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
        returned_at = dt.datetime.now(dt.UTC)
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
        returned_at = dt.datetime.now(dt.UTC)
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
        returned_at = dt.datetime.now(dt.UTC)
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
    accepted = threading.Event()
    client = threading.Thread(
        target=lambda: first_result.update(
            wait_for_run_signal(
                run_id,
                timeout=5,
                _on_event=lambda _event: accepted.set(),
            )
        ),
        daemon=True,
    )
    client.start()
    assert accepted.wait(timeout=5), (
        "replacement client never received a valid dispatcher event"
    )
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
        returned_at = dt.datetime.now(dt.UTC)
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
        returned_a = dt.datetime.now(dt.UTC)
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
        returned_b = dt.datetime.now(dt.UTC)
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


def _dead_server_config(root: Path) -> Path:
    """Point XDG at a port nothing listens on so HTTP wake restore fails closed."""
    config_home = root / "xdg-config"
    dest = config_home / "vibecrafted"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "config.toml").write_text(
        '[server]\nbind_host = "127.0.0.1"\nport = 1\n'
        'public_url = "http://127.0.0.1:1"\n',
        encoding="utf-8",
    )
    return config_home


def _write_runtime_meta(home: Path, run_id: str, payload: dict[str, object]) -> Path:
    run_dir = home / "control_plane" / "runtime_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "meta.json"
    path.write_text(json.dumps({"run_id": run_id, **payload}), encoding="utf-8")
    return path


def _spawn_cli_await(
    home: Path,
    run_id: str,
    *,
    config_home: Path,
    timeout: str = "5",
    hard_cap: str = "10",
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["VIBECRAFTED_HOME"] = str(home)
    env["HOME"] = str(home.parent / "user-home")
    env["XDG_CONFIG_HOME"] = str(config_home)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "vibecrafted_core.cli",
            "await",
            "codex",
            "--run-id",
            run_id,
            "--timeout",
            timeout,
            "--hard-cap",
            hard_cap,
            "--json",
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _collect_cli(
    proc: subprocess.Popen[str], *, timeout: float = 12
) -> tuple[int, dict[str, object], str]:
    stdout, stderr = proc.communicate(timeout=timeout)
    payload: dict[str, object] = json.loads(stdout) if stdout.strip() else {}
    return int(proc.returncode if proc.returncode is not None else 1), payload, stderr


def _count_lsof_socket_rows(output: str, pid: int, socket_path: Path) -> int:
    """Count ``lsof -F pfn`` unix-socket fds of ``pid`` named exactly ``socket_path``.

    A listener and every connection it accepted carry the bound path as their
    name (macOS prints the bare path, Linux appends `` type=STREAM``); the
    connecting side prints ``->0x...`` / a bare ``type=STREAM`` and is not
    counted. Rows of any other pid, any other path, and anything malformed
    count as zero.
    """
    current: int | None = None
    rows = 0
    for line in output.splitlines():
        if not line:
            continue
        field, value = line[0], line[1:]
        if field == "p":
            current = int(value) if value.isdigit() else None
        elif (
            field == "n"
            and current == pid
            and value.removesuffix(" type=STREAM") == str(socket_path)
        ):
            rows += 1
    return rows


def _wait_for_connections(
    pid: int,
    socket_path: Path,
    *,
    expected: int,
    timeout: float = 90.0,
    lsof_timeout: float = 10.0,
    process: subprocess.Popen[str] | None = None,
) -> None:
    """Block until ``pid`` holds the listener plus ``expected`` accepted connections.

    Evidence is ``lsof -F pfn`` of that exact pid, bounded per call. Without
    lsof the precondition cannot be proven, so this fails instead of falling
    back to a timing guess (hosted macOS and ubuntu runners ship lsof).
    """
    lsof = shutil.which("lsof")
    assert lsof is not None, "lsof is required to prove the connection precondition"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert process is None or process.poll() is None, "dispatcher exited early"
        try:
            listing = subprocess.run(
                [lsof, "-nP", "-a", "-U", "-p", str(pid), "-F", "pfn"],
                capture_output=True,
                text=True,
                check=False,
                timeout=lsof_timeout,
            ).stdout
        except subprocess.TimeoutExpired:
            listing = ""
        if _count_lsof_socket_rows(listing, pid, socket_path) >= expected + 1:
            return
        time.sleep(0.1)
    raise AssertionError(f"pid {pid} never reached {expected} accepted connections")


_MACOS_LSOF = "p42\nf0\nn->0x1\nf3\nn{path}\nf4\nn->0x2\nf6\nn{path}\nf7\nn{path}\n"
_LINUX_LSOF = (
    "p42\nf3\nn{path} type=STREAM\nf4\nntype=STREAM\nf6\nn{path} type=STREAM\n"
    "f7\nn{path} type=STREAM\n"
)


def test_lsof_parser_counts_only_exact_pid_and_path() -> None:
    path = Path("/tmp/vc/run.sock")
    for template in (_MACOS_LSOF, _LINUX_LSOF):
        listing = template.format(path=path)
        assert _count_lsof_socket_rows(listing, 42, path) == 3
        # Wrong pid, wrong path (incl. a longer path sharing the prefix) never count.
        assert _count_lsof_socket_rows(listing, 43, path) == 0
        assert _count_lsof_socket_rows(listing, 42, Path("/tmp/vc/other.sock")) == 0
        assert _count_lsof_socket_rows(listing, 42, Path("/tmp/vc/run.soc")) == 0
    # Listener-only, truncated, empty and malformed output cannot reach expected+1.
    listener_only = "p42\nf3\nn/tmp/vc/run.sock\n"
    assert _count_lsof_socket_rows(listener_only, 42, path) == 1
    for garbage in (
        "",
        "\n",
        "n/tmp/vc/run.sock\n",
        "pxyz\nn/tmp/vc/run.sock\n",
        "\x00\xff",
    ):
        assert _count_lsof_socket_rows(garbage, 42, path) == 0
    # A second process's rows after the target pid do not leak into its count.
    mixed = "p42\nf3\nn/tmp/vc/run.sock\np99\nf3\nn/tmp/vc/run.sock\n"
    assert _count_lsof_socket_rows(mixed, 42, path) == 1


def test_wait_for_connections_needs_real_accepted_sockets() -> None:
    # AF_UNIX paths are length-capped; pytest's tmp_path is too long for them.
    with tempfile.TemporaryDirectory(prefix="vc-") as short:
        _exercise_wait_for_connections(Path(short) / "s.sock")


def _exercise_wait_for_connections(path: Path) -> None:
    listener = socket.socket(socket.AF_UNIX)
    clients: list[socket.socket] = []
    accepted: list[socket.socket] = []
    try:
        listener.bind(str(path))
        listener.listen()
        # Listener only: the gate must not release.
        with pytest.raises(AssertionError, match="never reached"):
            _wait_for_connections(os.getpid(), path, expected=2, timeout=1.0)
        for _ in range(2):
            client = socket.socket(socket.AF_UNIX)
            client.connect(str(path))
            clients.append(client)
            accepted.append(listener.accept()[0])
        _wait_for_connections(os.getpid(), path, expected=2, timeout=10.0)
        # A different pid with the right path is not evidence.
        with pytest.raises(AssertionError, match="never reached"):
            _wait_for_connections(os.getppid(), path, expected=2, timeout=1.0)
    finally:
        for sock in (*clients, *accepted, listener):
            sock.close()


def test_twenty_real_cli_await_clients_share_dispatcher_fanout(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    config_home = _dead_server_config(tmp_path)
    run_id = "signal-cli-fanout-20"
    socket_path = run_signal_socket_path(run_id)
    payloads: list[dict[str, object]] = []
    # Arm all twenty clients before the dispatcher exists: the launch-grace
    # re-arm holds them until the socket binds. The worker is then held on a
    # gate that is released only once the dispatcher demonstrably has all twenty
    # connections: twenty cold CLI boots can outlast any fixed worker delay on a
    # loaded host, and a client that connects after teardown honestly reports
    # signal_kind "missing" (durable terminal), which is not a fanout member.
    clients = [
        _spawn_cli_await(home, run_id, config_home=config_home, hard_cap="90")
        for _ in range(20)
    ]
    gate = tmp_path / "release-worker"
    process, _meta, _report = _dispatcher(
        tmp_path, run_id, delay=0.0, home=home, gate=gate
    )
    try:
        _wait_for(socket_path)
        _wait_for_connections(process.pid, socket_path, expected=20, process=process)
        gate.write_text("go\n", encoding="utf-8")
        for client in clients:
            rc, payload, err = _collect_cli(client, timeout=90)
            assert rc == 0, (payload, err)
            payloads.append(payload)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        for owned in (process, *clients):
            if owned.poll() is None:
                owned.kill()
                owned.communicate(timeout=10)

    assert process.returncode == 0, (stdout, stderr)
    assert len(payloads) == 20
    stamps = []
    for payload in payloads:
        assert payload["completed"] is True
        assert payload["outcome"] == "terminal"
        assert payload["signal_kind"] == "terminal"
        subscription = payload.get("subscription")
        ownership = (
            subscription.get("ownership") if isinstance(subscription, dict) else None
        )
        assert ownership != "server_await_subscription"
        stamps.append(payload.get("signal_ts"))
    assert stamps[0]
    assert all(stamp == stamps[0] for stamp in stamps)
    assert not socket_path.exists()

    late_rc, late, late_err = _collect_cli(
        _spawn_cli_await(home, run_id, config_home=config_home)
    )
    assert late_rc == 0, (late, late_err)
    assert late["completed"] is True
    assert late["outcome"] == "terminal"
    assert late["reason"] in {"terminal", "report_delivered"}


def test_real_cli_late_subscriber_replays_uds_terminal(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    config_home = _dead_server_config(tmp_path)
    run_id = "signal-cli-late-replay"
    report = tmp_path / f"{run_id}.md"
    report.write_text(
        "---\nrun_id: " + run_id + "\nagent: python\nskill: test\nstatus: completed\n"
        "claim_status: completed\nfinalized: true\nclaim: late-replay\n---\nbody\n",
        encoding="utf-8",
    )
    _write_runtime_meta(
        home,
        run_id,
        {
            "status": "report_validated",
            "state": "report_validated",
            "agent": "codex",
            "skill": "implement",
            "exit_code": 0,
            "liveness": "terminal",
            "report": str(report),
            "artifact_ok": True,
        },
    )
    with RunSignalServer(run_id) as server:
        server.terminal(state="report_validated", report=str(report), exit_code=0)
        rc, payload, err = _collect_cli(
            _spawn_cli_await(home, run_id, config_home=config_home)
        )
    assert rc == 0, (payload, err)
    assert payload["completed"] is True
    assert payload["outcome"] == "terminal"
    assert payload["signal_kind"] == "terminal"


def test_real_cli_await_names_missing_and_broken_signal(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    config_home = _dead_server_config(tmp_path)

    missing_id = "signal-cli-missing"
    _write_runtime_meta(
        home,
        missing_id,
        {
            "status": "running",
            "state": "running",
            "agent": "codex",
            "skill": "implement",
            "liveness": "heartbeat",
        },
    )
    rc, payload, _err = _collect_cli(
        _spawn_cli_await(
            home, missing_id, config_home=config_home, timeout="2", hard_cap="2"
        )
    )
    assert rc != 0
    assert payload["completed"] is False
    assert payload["reason"] == "signal_missing"
    assert payload["signal_kind"] == "missing"

    broken_id = "signal-cli-broken"
    _write_runtime_meta(
        home,
        broken_id,
        {
            "status": "running",
            "state": "running",
            "agent": "codex",
            "skill": "implement",
            "liveness": "heartbeat",
        },
    )
    broken_path = run_signal_socket_path(broken_id)
    broken_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    broken_path.write_text("not-a-socket\n", encoding="utf-8")
    rc, payload, _err = _collect_cli(
        _spawn_cli_await(
            home, broken_id, config_home=config_home, timeout="2", hard_cap="2"
        )
    )
    assert rc != 0
    assert payload["completed"] is False
    assert payload["reason"] == "signal_missing"
    assert payload["signal_kind"] == "missing"


def test_real_cli_await_live_worker_disagreement(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    config_home = _dead_server_config(tmp_path)
    run_id = "signal-cli-live-disagree"
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = process_control.process_identity_receipt(worker.pid, run_id=run_id)
        assert identity is not None
        _write_runtime_meta(
            home,
            run_id,
            {
                "status": "running",
                "state": "running",
                "agent": "codex",
                "skill": "implement",
                "exit_code": 0,
                "liveness": "terminal",
                "worker_pid": worker.pid,
                "worker_pgid": os.getpgid(worker.pid),
                "worker_identity": identity,
            },
        )
        rc, payload, _err = _collect_cli(
            _spawn_cli_await(
                home, run_id, config_home=config_home, timeout="2", hard_cap="2"
            )
        )
    finally:
        worker.terminate()
        worker.wait(timeout=5)
    assert rc != 0
    assert payload["completed"] is False
    assert payload["worker_alive"] is True
    assert payload["reason"] == "signal_missing_live"
