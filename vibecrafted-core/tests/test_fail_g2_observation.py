"""Falsifiers for G2 observation, await-loop, and launch disk-guard repairs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import control_plane, process_control, server_observation


def _write_runtime_meta(home: Path, run_id: str, payload: dict[str, Any]) -> Path:
    run_dir = home / "control_plane" / "runtime_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = run_dir / "meta.json"
    body = {"run_id": run_id, **payload}
    meta.write_text(json.dumps(body), encoding="utf-8")
    (run_dir / "transcript.log").write_text("still working\n", encoding="utf-8")
    return meta


def test_observe_client_timeout_is_longer_than_measured_slow_observe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBECRAFTED_OBSERVE_TIMEOUT_S", raising=False)
    timeout = server_observation.observe_timeout_seconds()
    assert timeout >= 15.0
    recorded: dict[str, float | None] = {}

    def _boom(*_args: Any, **kwargs: Any) -> Any:
        recorded["timeout"] = kwargs.get("timeout")
        raise TimeoutError("slow observe")

    monkeypatch.setattr(server_observation, "_origin", lambda: "http://127.0.0.1:9")
    monkeypatch.setattr(server_observation.urllib.request, "urlopen", _boom)

    with pytest.raises(server_observation.ServerObservationError):
        server_observation.observe_run("missing-run")

    assert recorded["timeout"] is not None
    assert recorded["timeout"] >= 15.0


def test_observe_run_falls_back_to_runtime_runs_after_http_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "polr-260904-200526-39344"
    _write_runtime_meta(
        home,
        run_id,
        {
            "state": "running",
            "status": "running",
            "agent": "claude",
            "liveness": "pid_alive",
        },
    )

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutError("observe took 5.94s")

    monkeypatch.setattr(server_observation, "_origin", lambda: "http://127.0.0.1:9")
    monkeypatch.setattr(server_observation.urllib.request, "urlopen", _boom)

    payload = server_observation.observe_run(run_id)

    assert payload["found"] is True
    assert payload["run_id"] == run_id
    assert payload["source"] == "local_control_plane_fallback"
    assert payload.get("run") is not None
    assert str((payload.get("run") or {}).get("state") or "") == "running"


def test_await_does_not_complete_settled_parent_while_loop_lock_is_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "polr-lock-parent"
    _write_runtime_meta(
        home,
        run_id,
        {
            "state": "settled",
            "status": "settled",
            "agent": "guardian",
            "liveness": "lock_present",
            "latest_report": "",
            "latest_transcript": "",
        },
    )
    locks = home / "locks" / ".vibecrafted"
    locks.mkdir(parents=True, exist_ok=True)
    (locks / f"{run_id}.lock").write_text(
        "\n".join(
            [
                f"run_id={run_id}",
                "status=running",
                "current=3",
                "total=5",
                "agent=claude",
                f"root={tmp_path}",
                "started=2026-09-04T20:00:00+00:00",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = control_plane.await_run(
        run_id,
        timeout_seconds=0.15,
        interval_seconds=0.05,
        hard_cap_seconds=0.35,
    )

    assert payload["completed"] is False
    assert payload["worker_alive"] is True
    assert payload["run_id"] == run_id


def test_await_finds_loop_children_in_runtime_runs_without_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess
    import sys

    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(process_control, "build_env_index", dict)
    parent = "polr-runtime-children"
    _write_runtime_meta(
        home,
        parent,
        {"state": "settled", "status": "settled", "agent": "guardian"},
    )
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = process_control.process_identity_receipt(
            child.pid, run_id=f"{parent}-claude-L1"
        )
        assert identity is not None
        _write_runtime_meta(
            home,
            f"{parent}-claude-L1",
            {
                "state": "running",
                "status": "running",
                "agent": "claude",
                "worker_pid": child.pid,
                "worker_pgid": os.getpgid(child.pid),
                "worker_identity": identity,
                "liveness": "pid_alive",
            },
        )
        payload = control_plane.await_run(
            parent,
            timeout_seconds=0.15,
            interval_seconds=0.05,
            hard_cap_seconds=0.4,
        )
    finally:
        child.terminate()
        child.wait()

    assert payload["completed"] is False
    assert payload["worker_alive"] is True


def test_ensure_launch_storage_refuses_when_volume_is_below_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("VIBECRAFTED_LAUNCH_MIN_FREE_BYTES", raising=False)
    monkeypatch.setattr(control_plane, "_storage_free_bytes", lambda _path: 1024)
    with pytest.raises(control_plane.ControlPlaneStorageError) as excinfo:
        control_plane.ensure_launch_storage(tmp_path)
    message = str(excinfo.value)
    assert "control-plane degraded" in message
    assert "launch" in message


def test_ensure_launch_storage_can_be_disabled_for_tiny_fixtures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_LAUNCH_MIN_FREE_BYTES", "0")
    monkeypatch.setattr(control_plane, "_storage_free_bytes", lambda _path: 0)
    control_plane.ensure_launch_storage(tmp_path)
