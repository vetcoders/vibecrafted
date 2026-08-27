from __future__ import annotations

import datetime as dt
import fcntl
import gzip
import json
import multiprocessing
import os
import time
import uuid
from pathlib import Path
from typing import Any, NoReturn

import pytest
from vibecrafted_core import control_plane, process_control


def _append_event_in_process(home: str, ready: Any) -> None:
    os.environ["VIBECRAFTED_HOME"] = home
    ready.set()
    control_plane._append_event(
        {
            "ts": "2026-07-26T06:30:00+00:00",
            "run_id": "append-during-rotation",
            "kind": "state",
            "message": "must land exactly once",
            "payload": {"state": "active"},
        }
    )


def _write_meta(home: Path, payload: dict[str, object]) -> Path:
    reports = home / "artifacts" / "Vetcoders" / "vibecrafted" / "2026_0519" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{payload['run_id']}.meta.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_lock(home: Path, payload: dict[str, object]) -> Path:
    locks = home / "locks" / ".vibecrafted"
    locks.mkdir(parents=True, exist_ok=True)
    path = locks / f"{payload['run_id']}.lock"
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in payload.items()) + "\n",
        encoding="utf-8",
    )
    return path


def _qualified_worker(pid: int, run_id: str) -> dict[str, object]:
    identity = process_control.process_identity_receipt(pid, run_id=run_id)
    assert identity is not None
    return {
        "worker_pid": pid,
        "worker_pgid": os.getpgid(pid),
        "worker_identity": identity,
        "liveness": "pid_alive",
    }


def test_resolve_run_prefers_runtime_runs_where_core_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "marb-260615-000000-1"
    run_dir = home / "control_plane" / "runtime_runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.log").write_text("hello\n", encoding="utf-8")
    report = home / "artifacts" / "x" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text("done\n", encoding="utf-8")
    (run_dir / "meta.json").write_text(
        json.dumps({"run_id": run_id, "report": str(report)}), encoding="utf-8"
    )

    resolved = control_plane.resolve_run(run_id)

    assert resolved.source == "runtime_runs"
    assert resolved.run_dir == run_dir
    assert resolved.transcript == run_dir / "transcript.log"
    assert resolved.meta == run_dir / "meta.json"
    assert resolved.report == report


def test_resolve_run_falls_back_to_legacy_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "rvew-260615-000000-2"
    meta = _write_meta(
        home, {"run_id": run_id, "report": "legacy-report.md", "agent": "codex"}
    )

    resolved = control_plane.resolve_run(run_id)

    assert resolved.source == "artifacts"
    assert resolved.meta == meta
    assert resolved.run_dir == meta.parent


def test_resolve_run_raises_loud_when_still_launching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "just-260615-000000-3"

    with pytest.raises(control_plane.RunNotResolved) as excinfo:
        control_plane.resolve_run(run_id)

    message = str(excinfo.value)
    assert run_id in message
    assert "await" in message
    assert excinfo.value.run_id == run_id


def test_full_sync_rotates_terminal_transcript_and_sweeps_old_temps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_TRANSCRIPT_MAX_BYTES", "64")
    run_dir = home / "control_plane" / "runtime_runs" / "done-run"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(
        json.dumps({"run_id": "done-run", "state": "completed", "exit_code": 0}),
        encoding="utf-8",
    )
    original = bytes(range(200))
    transcript = run_dir / "transcript.log"
    transcript.write_bytes(original)
    stale_runtime_temp = run_dir / "meta.json.tmp.123"
    stale_runtime_temp.write_text("stale", encoding="utf-8")
    runs_dir = home / "control_plane" / "runs"
    runs_dir.mkdir(parents=True)
    stale_snapshot_temp = runs_dir / "done-run.json.tmp.123"
    stale_snapshot_temp.write_text("stale", encoding="utf-8")
    fresh_temp = run_dir / "meta.json.tmp.456"
    fresh_temp.write_text("fresh", encoding="utf-8")
    old = time.time() - control_plane.RUNTIME_TMP_MAX_AGE_SECONDS - 1
    os.utime(stale_runtime_temp, (old, old))
    os.utime(stale_snapshot_temp, (old, old))

    result = control_plane.sync_state()

    assert transcript.read_bytes() == original[-64:]
    with gzip.open(run_dir / "transcript.log.archive.gz", "rb") as archive:
        assert archive.read() == original[:-64]
    assert not stale_runtime_temp.exists()
    assert not stale_snapshot_temp.exists()
    assert fresh_temp.exists()
    assert result["storage_maintenance"] == {
        "orphan_temps_removed": 2,
        "transcripts_rotated": 1,
        "archives_removed": 0,
    }


def test_full_sync_compacts_cold_terminal_transcripts_below_size_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_TRANSCRIPT_MAX_BYTES", "512")
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_TRANSCRIPT_COLD_TAIL_BYTES", "64")
    monkeypatch.setenv("VIBECRAFTED_RUN_SNAPSHOT_RETENTION_SECONDS", "60")
    run_dir = home / "control_plane" / "runtime_runs" / "cold-terminal"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(
        json.dumps({"run_id": "cold-terminal", "state": "completed", "exit_code": 0}),
        encoding="utf-8",
    )
    original = bytes(range(200))
    transcript = run_dir / "transcript.log"
    transcript.write_bytes(original)
    old = time.time() - 61
    os.utime(transcript, (old, old))

    result = control_plane.sync_state()

    assert transcript.read_bytes() == original[-64:]
    with gzip.open(run_dir / "transcript.log.archive.gz", "rb") as archive:
        assert archive.read() == original[:-64]
    assert result["storage_maintenance"]["transcripts_rotated"] == 1


def test_full_sync_never_compacts_old_active_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_TRANSCRIPT_MAX_BYTES", "512")
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_TRANSCRIPT_COLD_TAIL_BYTES", "64")
    monkeypatch.setenv("VIBECRAFTED_RUN_SNAPSHOT_RETENTION_SECONDS", "60")
    run_dir = home / "control_plane" / "runtime_runs" / "active-run"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(
        json.dumps({"run_id": "active-run", "state": "active"}), encoding="utf-8"
    )
    original = bytes(range(200))
    transcript = run_dir / "transcript.log"
    transcript.write_bytes(original)
    old = time.time() - 61
    os.utime(transcript, (old, old))

    result = control_plane.sync_state()

    assert transcript.read_bytes() == original
    assert not (run_dir / "transcript.log.archive.gz").exists()
    assert result["storage_maintenance"]["transcripts_rotated"] == 0


def test_atomic_write_reports_actionable_degraded_mode_before_enospc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "runs" / "run.json"
    monkeypatch.setattr(control_plane, "_storage_free_bytes", lambda _path: 0)

    with pytest.raises(control_plane.ControlPlaneStorageError) as excinfo:
        control_plane._write_json(target, {"run_id": "run"})

    message = str(excinfo.value)
    assert "control-plane degraded" in message
    assert "Free disk space" in message
    assert not target.exists()


def test_agent_meta_projects_structured_operator_relationship(tmp_path: Path) -> None:
    meta = tmp_path / "meta.json"
    meta.write_text(
        json.dumps(
            {
                "run_id": "init-child",
                "status": "active",
                "updated_at": "2026-08-25T12:00:00Z",
                "role": "agent",
                "prompt_role": "/vc-init",
                "provider_session_id": "child-session",
                "operator_policy": {
                    "selection": "auto",
                    "provider": "claude",
                    "supported": True,
                },
                "supervision": {
                    "relation_id": "relation-1",
                    "operator_run_id": "oper-1",
                    "child_run_id": "init-child",
                    "state": "active",
                },
                "continuity": {
                    "mode": "full-lineage",
                    "lineage_id": "parent-run-1",
                    "supported": True,
                    "materialized": True,
                    "context_sha256": "abc",
                    "loop_sha256": "def",
                },
            }
        ),
        encoding="utf-8",
    )
    projected = control_plane._normalize_agent_meta(meta)
    assert projected is not None
    assert projected.extra["role"] == "agent"
    assert projected.extra["operator_policy"]["provider"] == "claude"
    assert projected.extra["supervision"]["operator_run_id"] == "oper-1"
    assert projected.extra["continuity"] == {
        "mode": "full-lineage",
        "lineage_id": "parent-run-1",
        "supported": True,
        "materialized": True,
        "context_sha256": "abc",
        "loop_sha256": "def",
    }


def test_operator_stop_is_sticky_over_late_failure_and_artifact_aliases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "sticky-stop-projection"
    control_plane._append_event(
        {
            "ts": "2026-07-26T12:00:00+00:00",
            "run_id": run_id,
            "kind": "lifecycle:active",
            "message": "worker active",
            "payload": {
                "state": "active",
                "root": "/Volumes/vc-workspace/vetcoders/vibecrafted",
                "agent": "codex",
                "skill": "workflow",
                "mode": "workflow",
                "worker_pid": 987654321,
                "worker_pgid": 987654321,
                "liveness": "pid_alive",
            },
        }
    )
    control_plane.record_stop_transition(
        run_id,
        accepted=True,
        reason="manual operator stop",
        target="worker_pgid",
        target_pid=987654321,
        target_pgid=987654321,
        signal_sent=True,
        alive_after_grace=False,
        exit_code=143,
    )
    control_plane._append_event(
        {
            "ts": "2026-07-26T12:00:02+00:00",
            "run_id": run_id,
            "kind": "lifecycle:failed",
            "message": "process failed with exit code -15",
            "payload": {
                "state": "failed",
                "exit_code": -15,
                "liveness": "terminal",
                "recovery_required": True,
                "error": "supervisor observed SIGTERM",
            },
        }
    )
    control_plane._append_event(
        {
            "ts": "2026-07-26T12:00:03+00:00",
            "run_id": run_id,
            "kind": "lifecycle:artifact_seen",
            "message": "artifacts inspected",
            "payload": {
                "state": "artifact_seen",
                "event_kind": "artifact",
                "errors": ["report_missing"],
                "warnings": ["meta_missing"],
                "recovery_required": True,
            },
        }
    )

    run = control_plane.lookup_run(run_id)

    assert run is not None
    assert run["state"] == "stopped"
    assert run["operator_stop_accepted"] is True
    assert run["stop_reason"] == "manual operator stop"
    assert run["exit_code"] == 143
    assert run["artifact_ok"] is True
    assert run["artifact_errors"] == []
    assert run["artifact_gate"] == "stopped"
    assert run["recovery_required"] is False
    assert run["last_error"] == ""
    assert run["lifecycle"]["stop"] is False
    assert run["lifecycle"]["recovery_required"] is False


def test_sync_state_preserves_runtime_observe_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    completed_at = "2026-05-19T00:01:00+00:00"
    _write_meta(
        home,
        {
            "run_id": "impl-010101-42",
            "status": "completed",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "report": "/tmp/report.md",
            "transcript": "/tmp/transcript.log",
            "updated_at": completed_at,
            "started_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "impl",
            "exit_code": 0,
            "liveness": "terminal",
            "launcher_pid": 12345,
            "completed_at": completed_at,
            "session_id": "session-abc",
        },
    )

    snapshot = control_plane.sync_state()

    run = snapshot["recent_runs"][0]
    assert run["run_id"] == "impl-010101-42"
    assert run["exit_code"] == 0
    assert run["liveness"] == "terminal"
    assert run["launcher_pid"] == 12345
    assert run["completed_at"] == completed_at
    assert run["session_id"] == "session-abc"


def test_sync_state_enforces_identity_on_fresh_creation_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    events = home / "control_plane" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-05-19T00:00:00+00:00",
                        "run_id": "wflw-known-identity",
                        "kind": "launch",
                        "message": "launch accepted",
                        "payload": {
                            "state": "created",
                            "agent": "codex",
                            "skill": "workflow",
                            "mode": "workflow",
                            "root": str(repo),
                            "session_id": "session-real",
                            "identity_required": True,
                        },
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-05-19T00:00:01+00:00",
                        "run_id": "wflw-fallback-identity",
                        "kind": "launch",
                        "message": "launch accepted",
                        "payload": {
                            "state": "created",
                            "agent": "claude",
                            "skill": "workflow",
                            "mode": "workflow",
                            "root": "repo",
                            "session_id": "pending",
                            "identity_required": True,
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = control_plane.sync_state()
    runs = {run["run_id"]: run for run in snapshot["recent_runs"]}

    known = runs["wflw-known-identity"]
    assert known["session_id"] == "session-real"
    assert known["root"] == str(repo.resolve())

    fallback = runs["wflw-fallback-identity"]
    assert fallback["root"] == str(repo.resolve())
    assert fallback["session_id"] != "pending"
    uuid.UUID(fallback["session_id"])


def test_sync_state_publishes_lifecycle_control_availability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "999999999")
    _write_meta(
        home,
        {
            "run_id": "wflw-010102-42",
            "status": "running",
            "agent": "codex",
            "mode": "workflow",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "wflw",
            "worker_pgid": 23456,
            "prompt": "continue",
        },
    )
    _write_meta(
        home,
        {
            "run_id": "impl-010103-42",
            "status": "completed",
            "agent": "claude",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:01:00+00:00",
            "skill_code": "impl",
            "exit_code": 0,
            "liveness": "terminal",
            "prompt": "continue",
        },
    )
    _write_meta(
        home,
        {
            "run_id": "rvew-010104-42",
            "status": "running",
            "agent": "gemini",
            "mode": "review",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:02:00+00:00",
            "skill_code": "rvew",
        },
    )

    snapshot = control_plane.sync_state()
    runs = {run["run_id"]: run for run in snapshot["recent_runs"]}

    assert runs["wflw-010102-42"]["lifecycle"] == {
        "await": True,
        "inspect": True,
        "stop": True,
        "cancel": True,
        "resume": False,
        "recovery_required": False,
    }
    assert runs["impl-010103-42"]["lifecycle"] == {
        "await": False,
        "inspect": True,
        "stop": False,
        "cancel": False,
        "resume": True,
        "recovery_required": False,
    }
    assert runs["rvew-010104-42"]["lifecycle"] == {
        "await": True,
        "inspect": True,
        "stop": False,
        "cancel": False,
        "resume": False,
        "recovery_required": True,
    }


def test_lookup_run_uses_synced_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    _write_meta(
        home,
        {
            "run_id": "rvew-020202-42",
            "status": "running",
            "agent": "claude",
            "mode": "review",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "rvew",
            "launcher_pid": os.getpid(),
            "liveness": "pid_alive",
        },
    )

    run = control_plane.lookup_run("rvew-020202-42")

    assert run is not None
    assert run["agent"] == "claude"
    assert run["launcher_pid"] == os.getpid()
    assert run["liveness"] == "pid_alive"


def test_scoped_lookup_is_lockless_while_global_lock_is_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A per-run lookup must not queue behind the shared control-plane lock.

    Regression for the flock migraine: one held global lock used to freeze every
    per-run await/lookup for the full heartbeat window, mislabeling live workers
    as stalled/pid_gone. The scoped path takes no lock, so it returns promptly
    even while another holder owns the lock.
    """
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    _write_meta(
        home,
        {
            "run_id": "work-030303-99",
            "status": "running",
            "agent": "codex",
            "mode": "workflow",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "work",
            "launcher_pid": os.getpid(),
            "liveness": "pid_alive",
        },
    )
    control_plane.control_plane_home().mkdir(parents=True, exist_ok=True)

    holder = control_plane._sync_lock_path().open("a+", encoding="utf-8")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        started = time.monotonic()
        run = control_plane.lookup_run("work-030303-99")
        elapsed = time.monotonic() - started

        # Lockless: returns without waiting out any lock budget.
        assert elapsed < 1.0
        assert run is not None
        assert run["agent"] == "codex"
        assert run["launcher_pid"] == os.getpid()

        # The hot emit path (append_event / _append_event) must NOT take the
        # global lock: a spawn/stop event lands even while the lock is held.
        from vibecrafted_core import events

        events.append_event("lifecycle:active", "work-030303-99", "still moving")
        control_plane.record_stop_transition(
            "work-030303-99", accepted=False, reason="probe"
        )
        stream = control_plane.event_stream_path().read_text(encoding="utf-8")
        assert "still moving" in stream
        assert "stop rejected" in stream

        # The board rebuild DOES take the lock — and now fails loud with a
        # bounded budget instead of hanging forever.
        monkeypatch.setenv("VIBECRAFTED_SYNC_LOCK_TIMEOUT_S", "0.2")
        with pytest.raises(control_plane.ControlPlaneLockBusy):
            control_plane.sync_state()
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_sync_state_reconciles_dead_launcher_to_stalled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "1")
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "999999999")
    _write_meta(
        home,
        {
            "run_id": "just-dead-pid",
            "status": "running",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:02:00+00:00",
            "heartbeat_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "just",
            "launcher_pid": 999999999,
            "liveness": "pid_alive",
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "stalled"
    assert run["health"] == "stalled"
    assert run["liveness"] == "pid_gone"
    assert run["recovery_required"] is True
    assert run["lifecycle"]["recovery_required"] is True
    assert run["lifecycle"]["stop"] is False
    assert "recovery_required" in run["last_error"]

    persisted = json.loads(
        (home / "control_plane" / "runs" / "just-dead-pid.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["state"] == "stalled"
    assert persisted["liveness"] == "pid_gone"

    refreshed = control_plane.lookup_run("just-dead-pid")
    assert refreshed is not None
    assert refreshed["state"] == "stalled"
    assert refreshed["liveness"] == "pid_gone"
    assert refreshed["recovery_required"] is True
    assert refreshed["lifecycle"]["recovery_required"] is True
    assert "recovery_required" in refreshed["last_error"]


def test_sync_state_settles_active_pid_gone_on_nonzero_exit_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Worker death with exit_code!=0 must not leave state=active and hang await.

    Field 2026-07-22: scaffold died on MCP AuthRequired; meta stayed active with
    pid_gone and await idled the heartbeat window.
    """
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    # Fresh heartbeat would previously block settle for the full threshold.
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "999999")
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "999999999")
    now = "2026-07-22T12:00:00+00:00"
    _write_meta(
        home,
        {
            "run_id": "scaf-dead-exit1",
            "status": "running",
            "state": "active",
            "agent": "codex",
            "mode": "scaffold",
            "root": str(tmp_path),
            "updated_at": now,
            "heartbeat_at": now,
            "skill_code": "scaf",
            "launcher_pid": 999999999,
            "exit_code": 1,
            "liveness": "pid_gone",
            "last_error": "AuthRequired: stripe",
        },
    )

    run = control_plane.sync_state()["recent_runs"][0]
    assert run["state"] == "failed"
    assert run["health"] == "final"
    assert run["liveness"] == "pid_gone"
    assert run["exit_code"] == 1
    assert run["recovery_required"] is True
    assert "pid_gone immediate settle" in str(run.get("last_error") or "")

    await_result = control_plane.await_run(
        "scaf-dead-exit1", timeout_seconds=2, interval_seconds=0.2
    )
    assert await_result["completed"] is True
    assert await_result["timed_out"] is False


def test_sync_state_keeps_run_live_when_worker_alive_despite_dead_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # P0 regression: a detached/headless dispatch records an EPHEMERAL launcher
    # pid (a spawn-shell that exits within seconds) while the dispatcher + worker
    # keep running and DELIVER. The liveness reconciler must NOT mark such a run
    # recovery_required just because the launcher pid is dead — a live worker_pid
    # is proof the run is alive. (Before this fix, every detached dispatch was
    # false-blocked as report_missing/recovery_required mid-delivery.)
    from vibecrafted_core import process_control
    from vibecrafted_core.process_control import process_identity_receipt

    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "1")
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "999999999")
    monkeypatch.setattr(process_control, "build_env_index", dict)
    worker_identity = process_identity_receipt(os.getpid(), run_id="just-live-worker")
    assert worker_identity is not None
    _write_meta(
        home,
        {
            "run_id": "just-live-worker",
            "status": "running",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:02:00+00:00",
            "heartbeat_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "just",
            "launcher_pid": 999999999,  # ephemeral spawn-shell, dead
            "worker_pid": os.getpid(),  # the dispatcher/worker — ALIVE
            "worker_pgid": os.getpgid(os.getpid()),
            "worker_identity": worker_identity,
            "liveness": "pid_alive",
        },
    )

    run = control_plane.sync_state()["recent_runs"][0]

    assert run["state"] != "stalled"
    assert run["liveness"] != "pid_gone"
    assert run.get("recovery_required") is not True
    assert "recovery_required" not in str(run.get("last_error") or "")


@pytest.mark.parametrize(
    ("identity_mode", "expected_reason", "expected_terminal"),
    [
        ("missing", "process_identity_unavailable", False),
        ("start_token_mismatch", "process_identity_mismatch", False),
        ("dead_pid", "process_identity_gone", True),
    ],
)
def test_sync_state_settles_stale_pid_alive_only_after_qualified_identity_check(
    identity_mode: str,
    expected_reason: str,
    expected_terminal: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from vibecrafted_core.process_control import process_identity_receipt

    run_id = f"impl-qualified-{identity_mode}"
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    worker_pid = os.getpid() if identity_mode != "dead_pid" else 999999999
    worker_pgid = os.getpgid(os.getpid()) if identity_mode != "dead_pid" else 999999999
    identity = process_identity_receipt(os.getpid(), run_id=run_id)
    assert identity is not None
    identity["pid"] = worker_pid
    identity["pgid"] = worker_pgid
    if identity_mode == "missing":
        identity = None
    elif identity_mode == "start_token_mismatch":
        identity["start_token"] = "definitely-not-the-current-start-token"

    _write_meta(
        home,
        {
            "run_id": run_id,
            "status": "running",
            "state": "active",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": "2026-08-26T00:00:00+00:00",
            "worker_pid": worker_pid,
            "worker_pgid": worker_pgid,
            "worker_identity": identity,
            "liveness": "pid_alive",
        },
    )

    run = control_plane.sync_state()["recent_runs"][0]

    if expected_terminal:
        assert run["process_truth_reason"] == expected_reason
        assert run["state"] == "failed"
        assert run["health"] == "final"
        assert run["liveness"] == "pid_gone"
        assert run["process_truth"] == "terminal"
        assert run["terminal_reason"] == "qualified_process_identity_lost"
        assert run["settlement_verdict"] == "failed"
        assert run["settlement_tui"] == "x"
    else:
        assert run["state"] == "running"
        assert run["liveness"] == "pid_alive"
        assert control_plane._worker_process_truth(run) == (False, expected_reason)
        assert run.get("terminal_reason") is None
        assert run.get("settlement_verdict") is None


def test_worker_truth_valid_receipt_does_not_scan_fleet_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibecrafted_core import process_control

    run_id = "impl-scoped-observation"
    receipt = process_control.process_identity_receipt(os.getpid(), run_id=run_id)
    assert receipt is not None

    def reject_env_scan(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("scoped observation must not run ps axeww")

    monkeypatch.setattr(process_control, "build_env_index", reject_env_scan)

    assert control_plane._worker_process_truth(
        {
            "run_id": run_id,
            "worker_pid": os.getpid(),
            "worker_pgid": os.getpgid(os.getpid()),
            "worker_identity": receipt,
        }
    ) == (True, "process_identity_current")


def test_worker_truth_accepts_owned_native_child_in_canonical_pgid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibecrafted_core import process_control
    from vibecrafted_core.run_reaper import ProcessEntry

    run_id = "impl-wrapper-child"
    monkeypatch.setattr(
        process_control,
        "validate_process_identity",
        lambda *_args, **_kwargs: (False, "process_gone", None),
    )
    monkeypatch.setattr(
        process_control,
        "build_process_table",
        lambda: (ProcessEntry(pid=778, ppid=777, pgid=700, command="codex exec"),),
    )
    monkeypatch.setattr(process_control, "build_env_index", lambda: {778: run_id})

    alive, reason = control_plane._worker_process_truth(
        {
            "run_id": run_id,
            "worker_pid": 777,
            "worker_pgid": 700,
            "worker_identity": {
                "pid": 777,
                "pgid": 700,
                "run_id": run_id,
                "start_token": "old-wrapper",
                "command_sha256": "0" * 64,
            },
        }
    )

    assert alive is True
    assert reason == "canonical_pgid_descendant"


def test_terminal_meta_overrides_stale_pid_alive_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    _write_meta(
        home,
        {
            "run_id": "repa-260826-ambiguity-p0",
            "status": "completed",
            "state": "completed",
            "agent": "codex",
            "mode": "repair",
            "root": str(tmp_path),
            "updated_at": "2026-08-26T00:00:00+00:00",
            "completed_at": "2026-08-26T00:00:00+00:00",
            "worker_pid": os.getpid(),
            "worker_pgid": os.getpgid(os.getpid()),
            "liveness": "pid_alive",
            "exit_code": 0,
        },
    )

    run = control_plane.sync_state()["recent_runs"][0]

    assert run["state"] == "completed"
    assert run["worker_alive"] is False
    assert run["process_truth"] == "terminal"
    assert run["health"] == "final"


@pytest.mark.parametrize(
    ("owner_pid", "worker_pid", "terminal_reason"),
    [
        (999999999, os.getpid(), "owner_pid_gone"),
        (os.getpid(), 999999999, "provider_pid_gone"),
    ],
)
def test_sync_state_never_projects_interactive_run_live_when_either_role_is_dead(
    owner_pid: int,
    worker_pid: int,
    terminal_reason: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "999999999")
    _write_meta(
        home,
        {
            "run_id": f"interactive-{terminal_reason}",
            "status": "active",
            "agent": "codex",
            "mode": "interactive",
            "root": str(tmp_path),
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "skill_code": "init",
            "owner_pid": owner_pid,
            "worker_pid": worker_pid,
            "liveness": "active",
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "failed"
    assert run["health"] == "final"
    assert run["liveness"] == "pid_gone"
    assert run["terminal_reason"] == terminal_reason
    assert run["run_id"] not in {item["run_id"] for item in snapshot["active_runs"]}


def test_sync_state_gc_terminalizes_old_stalled_dead_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "60")
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "3600")
    now = dt.datetime(2026, 5, 19, 6, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(control_plane, "_now", lambda: now)
    _write_meta(
        home,
        {
            "run_id": "just-old-stalled",
            "status": "stalled",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "heartbeat_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "just",
            "launcher_pid": 999999999,
            "liveness": "pid_alive",
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "gc"
    assert run["health"] == "final"
    assert run["liveness"] == "pid_gone"
    assert run["completed_at"] == now.isoformat()
    assert "garbage-collected: dead launcher, no activity >3600s" in run["last_error"]
    assert all(item["run_id"] != "just-old-stalled" for item in snapshot["active_runs"])

    persisted = json.loads(
        (home / "control_plane" / "runs" / "just-old-stalled.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["state"] == "gc"


def test_sync_state_separates_stalled_dead_launcher_before_gc_grace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "60")
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "3600")
    now = dt.datetime(2026, 5, 19, 0, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(control_plane, "_now", lambda: now)
    _write_meta(
        home,
        {
            "run_id": "just-fresh-stalled",
            "status": "stalled",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "heartbeat_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "just",
            "launcher_pid": 999999999,
            "liveness": "pid_alive",
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "stalled"
    assert run["health"] == "stalled"
    assert run["liveness"] == "pid_gone"
    assert run["run_id"] not in {item["run_id"] for item in snapshot["active_runs"]}
    assert run["run_id"] in {item["run_id"] for item in snapshot["stalled_runs"]}
    assert "garbage-collected" not in run["last_error"]


def test_sync_state_active_truth_quarantines_pytest_events_and_separates_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tempfile

    now = dt.datetime(2026, 7, 23, 10, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(control_plane, "_now", lambda: now)
    with tempfile.TemporaryDirectory(prefix="vibecrafted-production-home-") as raw_home:
        home = Path(raw_home) / ".vibecrafted"
        monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
        events = home / "control_plane" / "events.jsonl"
        events.parent.mkdir(parents=True)
        records = [
            {
                "ts": now.isoformat(),
                "run_id": "live-worker",
                "kind": "lifecycle:active",
                "message": "worker heartbeat",
                "payload": {
                    "state": "active",
                    "root": "/Volumes/vc-workspace/vetcoders/vibecrafted",
                    "launcher_pid": os.getpid(),
                    "liveness": "pid_alive",
                    "heartbeat_at": now.isoformat(),
                },
            },
            {
                "ts": now.isoformat(),
                "run_id": "definitely-missing",
                "kind": "state",
                "message": "stale event only",
                "payload": {
                    "state": "running",
                    "health": "active",
                    "liveness": "pid_alive",
                    "heartbeat_at": (now - dt.timedelta(hours=2)).isoformat(),
                    "root": "/Volumes/vc-workspace/vetcoders/vibecrafted",
                },
            },
            {
                "ts": now.isoformat(),
                "run_id": "pytest-fixture-run",
                "kind": "lifecycle:active",
                "message": "fixture leak",
                "payload": {
                    "state": "active",
                    "root": "/private/tmp/pytest-of-operator/pytest-1/test_board0",
                    "launcher_pid": os.getpid(),
                },
            },
        ]
        events.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

        snapshot = control_plane.sync_state()

        assert [run["run_id"] for run in snapshot["active_runs"]] == ["live-worker"]
        assert [run["run_id"] for run in snapshot["stalled_runs"]] == [
            "definitely-missing"
        ]
        projected_ids = {
            run["run_id"]
            for bucket in ("active_runs", "stalled_runs", "recent_runs")
            for run in snapshot[bucket]
        }
        assert "pytest-fixture-run" not in projected_ids
        assert all(
            event["run_id"] != "pytest-fixture-run" for event in snapshot["events"]
        )
        assert snapshot["settlement_counts"] == {
            "f": 0,
            "x": 0,
            "n": 0,
            "total_settled": 0,
            "orphans": 0,
        }

        replayed = control_plane.sync_state()
        assert [run["run_id"] for run in replayed["active_runs"]] == ["live-worker"]
        assert [run["run_id"] for run in replayed["stalled_runs"]] == [
            "definitely-missing"
        ]


def test_sync_state_never_resurrects_guardian_settlement_as_stalled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    now = dt.datetime(2026, 8, 27, 12, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(control_plane, "_now", lambda: now)
    events = home / "control_plane" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(
        json.dumps(
            {
                "ts": (now - dt.timedelta(days=8)).isoformat(),
                "run_id": "guardian-settled",
                "kind": "spawn-update",
                "payload": {
                    "agent": "guardian",
                    "skill": "settlement",
                    "mode": "n",
                    "state": "settled",
                    "health": "stalled",
                    "liveness": "heartbeat",
                    "heartbeat_at": (now - dt.timedelta(days=8)).isoformat(),
                    "verdict": "needs_attention",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = control_plane.sync_state()
    run = next(
        run for run in snapshot["recent_runs"] if run["run_id"] == "guardian-settled"
    )

    assert run["state"] == "settled"
    assert run["health"] == "final"
    assert run["run_id"] not in {item["run_id"] for item in snapshot["active_runs"]}
    assert run["run_id"] not in {item["run_id"] for item in snapshot["stalled_runs"]}


def test_projection_notification_cannot_regress_newer_lifecycle_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "parity-projection-race"
    now = dt.datetime.now(dt.timezone.utc)
    control_plane._append_event(
        {
            "ts": now.isoformat(),
            "run_id": run_id,
            "kind": "lifecycle:active",
            "message": "process active",
            "payload": {
                "state": "active",
                "root": str(tmp_path),
                "session_id": "session-projection-race",
                "liveness": "pid_alive",
                "heartbeat_at": now.isoformat(),
            },
        }
    )
    control_plane._append_event(
        {
            "ts": (now + dt.timedelta(milliseconds=1)).isoformat(),
            "run_id": run_id,
            "kind": "state",
            "message": f"{run_id} entered process_spawned",
            "payload": {
                "projection_event": True,
                "previous_state": "created",
                "state": "process_spawned",
                "root": str(tmp_path),
                "session_id": "session-projection-race",
                "liveness": "heartbeat",
            },
        }
    )

    run = control_plane.sync_state(only_run_id=run_id)["recent_runs"][0]

    assert run["state"] == "active"
    assert run["liveness"] == "pid_alive"


def test_sync_state_reconciles_dead_launcher_success_evidence_to_completed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "1")
    completed_at = "2026-05-19T00:02:00+00:00"
    _write_meta(
        home,
        {
            "run_id": "just-success-pid-gone",
            "status": "running",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:02:00+00:00",
            "heartbeat_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "just",
            "exit_code": 0,
            "launcher_pid": 999999999,
            "liveness": "pid_alive",
            "completed_at": completed_at,
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "completed"
    assert run["health"] == "final"
    assert run["liveness"] == "terminal"
    assert run["completed_at"] == completed_at
    assert all(
        item["run_id"] != "just-success-pid-gone" for item in snapshot["active_runs"]
    )


def test_sync_state_clears_stale_last_error_when_success_evidence_arrives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """False-fail gap: watchdog stamps last_error, then exit 0 lands — must not stay Failed.

    Live pattern (work-260724-050009-56000): exit 0 + report + completed_at while
    the snapshot still carried recovery_required + launcher-dead last_error.
    """
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "1")
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "999999999")
    run_id = "work-false-fail-success"
    completed_at = "2026-05-19T00:05:00+00:00"

    # Phase 1: dead launcher + stale heartbeat → stalled + last_error.
    _write_meta(
        home,
        {
            "run_id": run_id,
            "status": "running",
            "agent": "grok",
            "mode": "workflow",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:02:00+00:00",
            "heartbeat_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "work",
            "launcher_pid": 999999999,
            "liveness": "pid_alive",
        },
    )
    stalled = control_plane.sync_state()["recent_runs"][0]
    assert stalled["state"] == "stalled"
    assert stalled["recovery_required"] is True
    assert "recovery_required" in str(stalled.get("last_error") or "")

    # Phase 2: worker delivered — exit 0 + completed_at on disk.
    _write_meta(
        home,
        {
            "run_id": run_id,
            "status": "completed",
            "agent": "grok",
            "mode": "workflow",
            "root": str(tmp_path),
            "updated_at": completed_at,
            "heartbeat_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "work",
            "launcher_pid": 999999999,
            "exit_code": 0,
            "liveness": "terminal",
            "completed_at": completed_at,
            # Residual recovery marks on meta must not re-poison the board.
            "last_error": "launcher_pid 999999999 is not alive; recovery_required",
            "recovery_required": True,
        },
    )

    snapshot = control_plane.sync_state()
    run = next(item for item in snapshot["recent_runs"] if item["run_id"] == run_id)

    assert run["state"] == "completed"
    assert run["health"] == "final"
    assert run["liveness"] == "terminal"
    assert run["exit_code"] == 0
    assert run["completed_at"] == completed_at
    assert "recovery_required" not in run or run.get("recovery_required") in (
        False,
        None,
    )
    assert not str(run.get("last_error") or "").strip()
    assert run["lifecycle"]["recovery_required"] is False

    # Terminal success may already be drained to runs/archive/ after settle.
    live = home / "control_plane" / "runs" / f"{run_id}.json"
    archived = home / "control_plane" / "runs" / "archive" / f"{run_id}.json"
    path = live if live.exists() else archived
    assert path.exists(), f"expected snapshot at {live} or {archived}"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["state"] == "completed"
    assert not str(persisted.get("last_error") or "").strip()
    assert not persisted.get("recovery_required")


def test_sync_state_reconciles_dead_launcher_with_missing_report_to_terminal_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "1")
    now = dt.datetime.now(dt.timezone.utc)
    _write_meta(
        home,
        {
            "run_id": "just-missing-report",
            "status": "running",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "report": str(tmp_path / "missing-report.md"),
            "transcript": str(tmp_path / "transcript.log"),
            "updated_at": now.isoformat(),
            "heartbeat_at": (now - dt.timedelta(seconds=2)).isoformat(),
            "skill_code": "just",
            "launcher_pid": 999999999,
            "liveness": "pid_alive",
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "report_missing"
    assert run["health"] == "final"
    assert run["operator_state"] == "blocked"
    assert run["artifact_gate"] == "failed"
    assert run["liveness"] == "pid_gone"
    assert run["recovery_required"] is True
    assert run["lifecycle"]["await"] is False
    assert run["lifecycle"]["recovery_required"] is True
    assert "report_missing" in run["artifact_errors"]
    assert "artifact contract failed (report_missing)" in run["last_error"]

    refreshed = control_plane.lookup_run("just-missing-report")
    assert refreshed is not None
    assert refreshed["state"] == "report_missing"
    assert refreshed["health"] == "final"


@pytest.mark.parametrize("terminal_state", ["report_invalid", "report_missing"])
def test_sync_state_heals_repaired_report_contract_to_completed_attention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, terminal_state: str
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    report = tmp_path / "repaired.md"
    report.write_text(
        "---\n"
        "run_id: repaired-report\n"
        "agent: codex\n"
        "skill: implement\n"
        "status: completed\n"
        "---\n"
        "# Verified handoff\n",
        encoding="utf-8",
    )
    events = home / "control_plane" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        json.dumps(
            {
                "ts": "2026-07-22T14:28:54+00:00",
                "run_id": "repaired-report",
                "kind": f"lifecycle:{terminal_state}",
                "message": "artifact contract failed",
                "payload": {
                    "state": terminal_state,
                    "agent": "codex",
                    "skill": "implement",
                    "mode": "implement",
                    "root": str(tmp_path),
                    "report": str(report),
                    "exit_code": 0,
                    "artifact_ok": False,
                    "artifact_errors": [
                        "report_missing"
                        if terminal_state == "report_missing"
                        else "report_frontmatter_missing"
                    ],
                    "liveness": "terminal",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "completed"
    assert run["artifact_ok"] is True
    assert run["artifact_errors"] == []
    assert run["execution_state"] == "exited"
    assert run["settlement_verdict"] == "needs_attention"
    assert run["settlement_tui"] == "n"
    assert run["lifecycle"]["recovery_required"] is False
    assert run["failure_card"] is None
    assert snapshot["settlement_counts"] == {
        "f": 0,
        "x": 0,
        "n": 1,
        "total_settled": 1,
        "orphans": 0,
    }


def test_sync_state_refuses_report_repair_from_another_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    report = tmp_path / "cross-wired.md"
    report.write_text(
        "---\n"
        "run_id: other-run\n"
        "agent: codex\n"
        "skill: implement\n"
        "status: completed\n"
        "---\n"
        "# Wrong report\n",
        encoding="utf-8",
    )
    events = home / "control_plane" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        json.dumps(
            {
                "ts": "2026-07-22T14:28:54+00:00",
                "run_id": "victim-run",
                "kind": "lifecycle:report_invalid",
                "message": "artifact contract failed",
                "payload": {
                    "state": "report_invalid",
                    "agent": "codex",
                    "skill": "implement",
                    "mode": "implement",
                    "root": str(tmp_path),
                    "report": str(report),
                    "exit_code": 0,
                    "artifact_ok": False,
                    "artifact_errors": ["report_frontmatter_missing"],
                    "liveness": "terminal",
                    "recovery_required": True,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["run_id"] == "victim-run"
    assert run["state"] == "report_invalid"
    assert run["artifact_ok"] is False
    assert run["settlement_tui"] == "x"
    assert run["lifecycle"]["recovery_required"] is True


def test_sync_state_keeps_unrepaired_report_invalid_in_failed_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    report = tmp_path / "still-invalid.md"
    report.write_text("# Missing frontmatter\n", encoding="utf-8")
    events = home / "control_plane" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        json.dumps(
            {
                "ts": "2026-07-22T14:28:54+00:00",
                "run_id": "still-invalid",
                "kind": "lifecycle:report_invalid",
                "message": "artifact contract failed",
                "payload": {
                    "state": "report_invalid",
                    "agent": "codex",
                    "skill": "implement",
                    "mode": "implement",
                    "root": str(tmp_path),
                    "report": str(report),
                    "exit_code": 0,
                    "artifact_ok": False,
                    "artifact_errors": ["report_frontmatter_missing"],
                    "liveness": "terminal",
                    "recovery_required": True,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "report_invalid"
    assert run["settlement_verdict"] == "failed"
    assert run["settlement_tui"] == "x"
    assert run["lifecycle"]["recovery_required"] is True
    assert snapshot["settlement_counts"]["x"] == 1


@pytest.mark.parametrize(
    ("exit_code", "invalid_proof"),
    [(0, True), (7, False)],
)
def test_sync_state_never_heals_failed_execution_or_invalid_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exit_code: int,
    invalid_proof: bool,
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "proof-invalid" if invalid_proof else "execution-failed"
    report = tmp_path / "valid-report.md"
    report.write_text(
        "---\n"
        f"run_id: {run_id}\n"
        "agent: codex\n"
        "skill: implement\n"
        "status: completed\n"
        "---\n"
        "# Report\n",
        encoding="utf-8",
    )
    if invalid_proof:
        proof = home / "control_plane" / "runtime_runs" / run_id / "proof"
        proof.mkdir(parents=True, exist_ok=True)
        (proof / "result.json").write_text("{}\n", encoding="utf-8")
    events = home / "control_plane" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        json.dumps(
            {
                "ts": "2026-07-22T14:28:54+00:00",
                "run_id": run_id,
                "kind": "lifecycle:report_invalid",
                "message": "artifact contract failed",
                "payload": {
                    "state": "report_invalid",
                    "agent": "codex",
                    "skill": "implement",
                    "mode": "implement",
                    "root": str(tmp_path),
                    "report": str(report),
                    "exit_code": exit_code,
                    "artifact_errors": ["report_frontmatter_missing"],
                    "liveness": "terminal",
                    "recovery_required": True,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "report_invalid"
    assert run["proof_state"] == ("invalid" if invalid_proof else "undeclared")
    assert run["settlement_verdict"] == ("invalid" if invalid_proof else "failed")
    assert run["settlement_tui"] == "x"
    assert run["lifecycle"]["recovery_required"] is True


def test_sync_state_reaps_stale_lock_present_run_without_launcher_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "999999999")
    lock = _write_lock(
        home,
        {
            "run_id": "just-233141-77774",
            "status": "running",
            "agent": "codex",
            "mode": "justdo",
            "root": str(tmp_path),
            "skill": "just",
            "started": "2026-06-11T06:31:41Z",
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert lock.exists()
    assert run["state"] == "stalled"
    assert run["health"] == "stalled"
    assert run["liveness"] == "pid_gone"
    assert run["launcher_pid"] is None
    assert run["heartbeat_at"] == "2026-06-11T06:31:41Z"
    assert run["lock_present"] is True
    assert run["recovery_required"] is True
    assert run["lifecycle"]["recovery_required"] is True
    assert "launcher_pid is missing" in run["last_error"]
    assert "no live launcher proof" in run["last_error"]
    assert "lock file remains present" in run["last_error"]


def test_sync_state_leaves_fresh_lock_present_run_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "3600")
    started_at = control_plane._now().isoformat()
    lock = _write_lock(
        home,
        {
            "run_id": "just-fresh-lock",
            "status": "running",
            "agent": "codex",
            "mode": "justdo",
            "root": str(tmp_path),
            "skill": "just",
            "started": started_at,
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert lock.exists()
    assert run["state"] == "running"
    assert run["health"] == "active"
    assert run["liveness"] == "lock_present"
    assert run["launcher_pid"] is None
    assert run["heartbeat_at"] == started_at
    assert run["lock_present"] is True
    assert "recovery_required" not in run
    assert run["lifecycle"]["recovery_required"] is False


def test_sync_state_leaves_live_launcher_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "1")
    _write_meta(
        home,
        {
            "run_id": "just-live-pid",
            "status": "running",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:02:00+00:00",
            "heartbeat_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "just",
            "launcher_pid": os.getpid(),
            "liveness": "pid_alive",
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "running"
    assert run["liveness"] == "pid_alive"
    assert run["lifecycle"]["recovery_required"] is False


def test_sync_state_leaves_terminal_dead_launcher_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "1")
    completed_at = "2026-05-19T00:02:00+00:00"
    _write_meta(
        home,
        {
            "run_id": "just-terminal-pid",
            "status": "completed",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": completed_at,
            "heartbeat_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "just",
            "exit_code": 0,
            "launcher_pid": 999999999,
            "liveness": "terminal",
            "completed_at": completed_at,
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "completed"
    assert run["health"] == "final"
    assert run["liveness"] == "terminal"
    assert "recovery_required" not in run


def test_sync_state_archives_old_terminal_snapshots_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUN_SNAPSHOT_RETENTION_SECONDS", "3600")
    monkeypatch.setenv("VIBECRAFTED_RUN_SNAPSHOT_RETENTION_COUNT", "100")
    now = dt.datetime(2026, 5, 19, 6, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(control_plane, "_now", lambda: now)
    runs_dir = home / "control_plane" / "runs"
    runs_dir.mkdir(parents=True)
    terminal_path = runs_dir / "old-terminal.json"
    active_path = runs_dir / "old-active.json"
    terminal_path.write_text(
        json.dumps(
            {
                "run_id": "old-terminal",
                "state": "completed",
                "health": "final",
                "updated_at": "2026-05-19T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    active_path.write_text(
        json.dumps(
            {
                "run_id": "old-active",
                "state": "running",
                "health": "active",
                "updated_at": "2026-05-19T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    control_plane.sync_state()
    control_plane.sync_state()

    archived = runs_dir / "archive" / "old-terminal.json"
    assert archived.exists()
    assert not terminal_path.exists()
    assert active_path.exists()
    assert json.loads(archived.read_text(encoding="utf-8"))["run_id"] == "old-terminal"


def test_await_run_completes_from_metadata_without_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    _write_meta(
        home,
        {
            "run_id": "wflw-030303-42",
            "status": "running",
            "agent": "codex",
            "mode": "workflow",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "wflw",
            "exit_code": "0",
            "liveness": "terminal",
        },
    )

    payload = control_plane.await_run(
        "wflw-030303-42", timeout_seconds=0, interval_seconds=0.1
    )

    assert payload["completed"] is True
    assert payload["timed_out"] is False
    assert payload["run"]["exit_code"] == 0


def test_await_run_completes_when_dead_worker_missing_report_is_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "1")
    now = dt.datetime.now(dt.timezone.utc)
    _write_meta(
        home,
        {
            "run_id": "wflw-missing-report",
            "status": "running",
            "agent": "codex",
            "mode": "workflow",
            "root": str(tmp_path),
            "report": str(tmp_path / "missing-report.md"),
            "transcript": str(tmp_path / "transcript.log"),
            "updated_at": now.isoformat(),
            "heartbeat_at": (now - dt.timedelta(seconds=2)).isoformat(),
            "skill_code": "wflw",
            "launcher_pid": 999999999,
            "liveness": "pid_alive",
        },
    )

    payload = control_plane.await_run(
        "wflw-missing-report", timeout_seconds=0, interval_seconds=0.1
    )

    assert payload["found"] is True
    assert payload["completed"] is True
    assert payload["timed_out"] is False
    assert payload["run"]["state"] == "report_missing"
    assert "report_missing" in payload["run"]["artifact_errors"]


def test_await_run_times_out_when_metadata_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))

    payload = control_plane.await_run(
        "missing-040404-42", timeout_seconds=0, interval_seconds=0.1
    )

    assert payload["found"] is False
    assert payload["completed"] is False
    assert payload["timed_out"] is True


def test_await_run_does_not_abandon_live_worker_past_idle_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-terminal run with a DEMONSTRABLY ALIVE worker must survive the base
    idle deadline. On host-a the marbles agent did ~13 min of real work (exit 0,
    full report) but the orchestrator abandoned the loop on a fixed wall clock.
    Here the worker stays alive while the idle window (0.2s) lapses many times
    over; the run is only stopped by the explicit hard cap, never the base idle
    timeout. ``reason == "hard_cap"`` is the proof the liveness gate held."""
    import subprocess
    import sys

    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(process_control, "build_env_index", dict)

    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _write_meta(
            home,
            {
                "run_id": "marb-live-worker",
                "status": "running",
                "agent": "codex",
                "mode": "marbles",
                "skill_code": "marb",
                "root": str(tmp_path),
                "updated_at": "2026-05-19T00:00:00+00:00",
                **_qualified_worker(worker.pid, "marb-live-worker"),
            },
        )

        assert (
            control_plane._worker_is_alive(
                {
                    "run_id": "marb-live-worker",
                    **_qualified_worker(worker.pid, "marb-live-worker"),
                }
            )
            is True
        )

        payload = control_plane.await_run(
            "marb-live-worker",
            timeout_seconds=0.2,
            interval_seconds=0.05,
            hard_cap_seconds=0.6,
        )
    finally:
        worker.terminate()
        worker.wait()

    # With no dispatcher socket, await performs one file-triad verdict and
    # reports the live worker without creating a replacement poll loop.
    assert payload["timed_out"] is False
    assert payload["reason"] == "signal_missing_live"
    assert payload["worker_alive"] is True
    assert payload["completed"] is False
    assert payload["attempts"] == 1


def test_await_run_dead_pid_alive_record_fails_without_waiting_for_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stale pid_alive claim is revalidated and receipted failed immediately."""
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "100000")
    _write_meta(
        home,
        {
            "run_id": "marb-dead-worker",
            "status": "running",
            "agent": "codex",
            "mode": "marbles",
            "skill_code": "marb",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "worker_pid": 999999999,
            "worker_pgid": 999999999,
            "liveness": "pid_alive",
        },
    )

    payload = control_plane.await_run(
        "marb-dead-worker",
        timeout_seconds=0.1,
        interval_seconds=0.05,
        hard_cap_seconds=5,
    )

    assert payload["completed"] is True
    assert payload["timed_out"] is False
    assert payload["reason"] == "terminal"
    assert payload["worker_alive"] is False


def test_await_run_rearms_when_stalled_projection_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`stalled` is an observation, not a terminal verdict.

    Await must stay armed when a false stall clears and only return after the
    later terminal transition. This is the operator notification contract: a
    transient watchdog flag cannot silently disarm supervision.
    """
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    states = [
        {
            "run_id": "wflw-rearm-7",
            "state": "stalled",
            "health": "stalled",
            "liveness": "pid_gone",
            "updated_at": "2026-07-25T08:33:00+00:00",
            "recovery_required": True,
        },
        {
            "run_id": "wflw-rearm-7",
            "state": "active",
            "health": "active",
            "liveness": "pid_gone",
            "updated_at": "2026-07-25T08:34:00+00:00",
        },
        {
            "run_id": "wflw-rearm-7",
            "state": "completed",
            "health": "final",
            "liveness": "terminal",
            "updated_at": "2026-07-25T08:35:00+00:00",
            "completed_at": "2026-07-25T08:35:00+00:00",
            "exit_code": 0,
        },
    ]
    snapshots = iter(
        {"active_runs": [], "recent_runs": [run], "stalled_runs": []} for run in states
    )
    monkeypatch.setattr(
        control_plane, "sync_state", lambda *, only_run_id=None: next(snapshots)
    )
    observed: list[str] = []

    payload = control_plane.await_run(
        "wflw-rearm-7",
        timeout_seconds=1,
        interval_seconds=0.01,
        hard_cap_seconds=5,
        on_poll=lambda run: observed.append(str((run or {}).get("state") or "")),
    )

    assert observed == ["stalled"]
    assert payload["completed"] is False
    assert payload["timed_out"] is True
    assert payload["reason"] == "signal_missing"
    assert payload["attempts"] == 1


def test_await_run_returns_report_delivered_when_worker_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dead worker + non-empty report is the sealed no-await handoff. Await
    must return ``report_delivered`` on the first poll instead of idling out a
    full window on the corpse and answering with a misleading ``idle_stall``
    (which taught agents to distrust await and hedge with manual monitors)."""
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "100000")
    report = tmp_path / "stage-report.md"
    report.write_text("### Summary\ndelivered\n", encoding="utf-8")
    _write_meta(
        home,
        {
            "run_id": "revi-delivered-42",
            "status": "running",
            "agent": "junie",
            "mode": "review",
            "skill_code": "revi",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "report": str(report),
            "worker_pid": 999999999,
            "worker_pgid": 999999999,
            "liveness": "pid_alive",
        },
    )

    payload = control_plane.await_run(
        "revi-delivered-42",
        timeout_seconds=5,
        interval_seconds=0.05,
        hard_cap_seconds=5,
        report_path=str(report),
    )

    assert payload["completed"] is True
    assert payload["timed_out"] is False
    assert payload["reason"] in {"terminal", "report_delivered"}
    assert payload["attempts"] == 1


def test_await_run_waits_for_live_launcher_to_finalize_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dead worker's report is not terminal while its launcher can still
    publish final metadata.  Await must keep the canonical loop open until the
    finalizer exits instead of returning an ``active`` pre-handoff snapshot.
    """
    import subprocess
    import sys
    import threading

    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    report = tmp_path / "stage-report.md"
    report.write_text("### Summary\ndelivered\n", encoding="utf-8")

    launcher = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
    reaper = threading.Thread(target=launcher.wait, daemon=True)
    reaper.start()
    try:
        _write_meta(
            home,
            {
                "run_id": "revi-launcher-finalizing-42",
                "status": "running",
                "agent": "codex",
                "mode": "review",
                "skill_code": "revi",
                "root": str(tmp_path),
                "updated_at": "2026-05-19T00:00:00+00:00",
                "launcher_pid": launcher.pid,
                "worker_pid": 999999999,
                "worker_pgid": 999999999,
                "liveness": "pid_alive",
            },
        )

        payload = control_plane.await_run(
            "revi-launcher-finalizing-42",
            timeout_seconds=1,
            interval_seconds=0.05,
            hard_cap_seconds=2,
            report_path=str(report),
        )
    finally:
        reaper.join(timeout=2)

    assert payload["completed"] is True
    assert payload["timed_out"] is False
    assert payload["reason"] in {"terminal", "report_delivered"}
    assert payload["worker_alive"] is False
    assert payload["attempts"] == 1


def test_await_run_report_alone_never_completes_a_live_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The inverse guard (fleet bug: `codex await` exited 0 on a still-live
    run): while the worker is ALIVE, a non-empty report never completes the
    wait — it may be mid-write, or a stale leftover from a previous attempt on
    the same announced path. Liveness holds the window until the hard cap."""
    import subprocess
    import sys

    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(process_control, "build_env_index", dict)
    report = tmp_path / "stage-report.md"
    report.write_text("### Summary\nstale or mid-write\n", encoding="utf-8")

    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _write_meta(
            home,
            {
                "run_id": "revi-live-writer-42",
                "status": "running",
                "agent": "junie",
                "mode": "review",
                "skill_code": "revi",
                "root": str(tmp_path),
                "updated_at": "2026-05-19T00:00:00+00:00",
                **_qualified_worker(worker.pid, "revi-live-writer-42"),
            },
        )

        payload = control_plane.await_run(
            "revi-live-writer-42",
            timeout_seconds=0.2,
            interval_seconds=0.05,
            hard_cap_seconds=0.6,
            report_path=str(report),
        )
    finally:
        worker.terminate()
        worker.wait()

    assert payload["completed"] is False
    assert payload["timed_out"] is False
    assert payload["reason"] == "signal_missing_live"
    assert payload["worker_alive"] is True


def test_await_run_terminal_looking_meta_never_completes_a_live_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """rc=0-on-live guard: stale terminal-looking metadata cannot beat OS liveness."""
    import subprocess
    import sys

    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(process_control, "build_env_index", dict)

    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _write_meta(
            home,
            {
                "run_id": "impl-terminal-looking-live",
                "status": "running",
                "agent": "codex",
                "mode": "implement",
                "skill_code": "impl",
                "root": str(tmp_path),
                "updated_at": "2026-05-19T00:00:00+00:00",
                **_qualified_worker(worker.pid, "impl-terminal-looking-live"),
                "exit_code": 0,
                "liveness": "terminal",
            },
        )

        payload = control_plane.await_run(
            "impl-terminal-looking-live",
            timeout_seconds=0.2,
            interval_seconds=0.05,
            hard_cap_seconds=0.6,
        )
    finally:
        worker.terminate()
        worker.wait()

    assert payload["completed"] is False
    assert payload["timed_out"] is False
    assert payload["reason"] == "signal_missing_live"
    assert payload["worker_alive"] is True


def test_await_run_live_child_keeps_loop_parent_open_past_idle_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Marbles/polarize loop parents freeze between rounds — no transcript of
    their own, sometimes no live pid — while the children do the real work in
    separate ``<parent>-…-L<n>`` records linked only by id prefix. A live
    child must keep the parent's await open: the run stops only at the hard
    cap, never on a false ``idle_stall`` mid-loop (the premature return that
    made supervising agents double-guard await with manual monitors)."""
    import subprocess
    import sys

    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "100000")
    monkeypatch.setattr(process_control, "build_env_index", dict)
    _write_meta(
        home,
        {
            "run_id": "marb-loop-parent",
            "status": "running",
            "agent": "codex",
            "mode": "marbles",
            "skill_code": "marb",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "liveness": "heartbeat",
        },
    )

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _write_meta(
            home,
            {
                "run_id": "marb-loop-parent-marbles-L2",
                "status": "running",
                "agent": "codex",
                "mode": "marbles",
                "skill_code": "marb",
                "root": str(tmp_path),
                "updated_at": "2026-05-19T00:00:00+00:00",
                **_qualified_worker(child.pid, "marb-loop-parent-marbles-L2"),
            },
        )

        payload = control_plane.await_run(
            "marb-loop-parent",
            timeout_seconds=0.2,
            interval_seconds=0.05,
            hard_cap_seconds=0.8,
        )
    finally:
        child.terminate()
        child.wait()

    # A missing dispatcher is reconciled once. Child-loop liveness belongs to
    # that dispatcher socket, not to a new client-side polling aggregate.
    assert payload["timed_out"] is False
    assert payload["reason"] == "signal_missing_live"
    assert payload["worker_alive"] is True
    assert payload["completed"] is False


def test_sync_state_projects_event_stream_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    now = dt.datetime.now(dt.timezone.utc)
    events = home / "control_plane" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": now.isoformat(),
                        "run_id": "wflw-111111-1111",
                        "kind": "launch",
                        "message": "launch accepted",
                        "payload": {
                            "state": "created",
                            "agent": "claude",
                            "skill": "workflow",
                            "mode": "workflow",
                            "runtime": "headless",
                            "root": str(tmp_path),
                            "prompt": "go",
                            "report": str(tmp_path / "report.md"),
                            "transcript": str(tmp_path / "run.log"),
                            "agent_session_id": "claude-native-parent",
                            "runtime_session_id": "runtime-child",
                            "parent_runtime_session_id": "runtime-parent",
                            "resume_of": "wflw-parent",
                            "resume_root": "wflw-root",
                            "attempt": 2,
                            "native_resume": True,
                            "resume_idempotency_key": ("settlement:wflw-parent:7"),
                        },
                    }
                ),
                json.dumps(
                    {
                        "ts": (now + dt.timedelta(microseconds=1)).isoformat(),
                        "run_id": "wflw-111111-1111",
                        "kind": "lifecycle:active",
                        "message": "process active",
                        "payload": {"state": "active", "liveness": "pid_alive"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = control_plane.sync_state()

    run = snapshot["recent_runs"][0]
    assert run["run_id"] == "wflw-111111-1111"
    assert run["state"] == "active"
    assert run["operator_state"] == "running"
    assert run["artifact_gate"] == "pending"
    assert "report_missing" not in run["artifact_errors"]
    assert run["agent_session_id"] == "claude-native-parent"
    assert run["runtime_session_id"] == "runtime-child"
    assert run["parent_runtime_session_id"] == "runtime-parent"
    assert run["resume_of"] == "wflw-parent"
    assert run["resume_root"] == "wflw-root"
    assert run["attempt"] == 2
    assert run["native_resume"] is True
    assert run["resume_idempotency_key"] == "settlement:wflw-parent:7"


def test_sync_state_surfaces_failure_card_on_contract_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    events = home / "control_plane" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        json.dumps(
            {
                "ts": "2026-05-19T00:01:00+00:00",
                "run_id": "wflw-222222-2222",
                "kind": "lifecycle:report_missing",
                "message": "artifact contract failed",
                "payload": {
                    "state": "report_missing",
                    "agent": "codex",
                    "skill": "workflow",
                    "mode": "workflow",
                    "root": str(tmp_path),
                    "artifact_errors": ["report_missing"],
                    "liveness": "terminal",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot = control_plane.sync_state()

    run = snapshot["recent_runs"][0]
    assert run["run_id"] == "wflw-222222-2222"
    assert run["operator_state"] == "blocked"
    card = run["failure_card"]
    assert card is not None
    assert card["code"] == "runtime_contract_failure"
    assert any("contract failure" in warning for warning in snapshot["warnings"])


def test_block_run_lever_pins_terminal_blocked_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from vibecrafted_core import workflow

    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    events = home / "control_plane" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        json.dumps(
            {
                "ts": "2026-05-19T00:02:00+00:00",
                "run_id": "wflw-333333-3333",
                "kind": "lifecycle:active",
                "message": "process active",
                "payload": {
                    "state": "active",
                    "agent": "claude",
                    "skill": "workflow",
                    "mode": "workflow",
                    "root": str(tmp_path),
                    "liveness": "pid_alive",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = workflow.block_run(
        "wflw-333333-3333", reason="waiting on operator", note="needs api key"
    )
    assert result["accepted"] is True

    snapshot = control_plane.sync_state()
    run = next(
        item
        for bucket in ("active_runs", "recent_runs")
        for item in (snapshot.get(bucket) or [])
        if item["run_id"] == "wflw-333333-3333"
    )
    assert run["state"] == "blocked"
    assert run["operator_state"] == "blocked"
    assert run["health"] == "final"
    assert run["failure_card"] is not None


def test_run_liveness_projects_reconciled_worker_truth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess
    import sys

    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _write_meta(
        home,
        {
            "run_id": "impl-dead-worker",
            "status": "running",
            "agent": "codex",
            "mode": "workflow",
            "skill_code": "impl",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "worker_pid": dead.pid,
            "worker_pgid": dead.pid,
            "liveness": "pid_alive",
        },
    )

    payload = control_plane.run_liveness("impl-dead-worker")

    # The report-on-death gap: a startup corpse must be tellable from slow
    # work by OS liveness, not inferred from an eternally-"running" status.
    assert payload["found"] is True
    assert payload["worker_alive"] is False
    assert isinstance(payload["state"], str)
    assert isinstance(payload["recovery_required"], bool)

    assert control_plane.run_liveness("no-such-run") == {
        "run_id": "no-such-run",
        "found": False,
    }
    assert control_plane.run_liveness("")["found"] is False


def _write_snapshot(home: Path, payload: dict[str, object]) -> Path:
    runs = home / "control_plane" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{payload['run_id']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_drain_settles_then_archives_old_terminals_and_keeps_recent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    now = dt.datetime.now(dt.timezone.utc)
    old_stamp = (now - dt.timedelta(days=3)).isoformat()
    fresh_stamp = now.isoformat()
    # Parked gc run without a settlement terminal — must settle before archive.
    _write_snapshot(
        home,
        {
            "run_id": "impl-old-parked",
            "state": "gc",
            "health": "final",
            "liveness": "pid_gone",
            "updated_at": old_stamp,
            "completed_at": old_stamp,
        },
    )
    # Recent terminal — settles but stays retained inside the keep window.
    _write_snapshot(
        home,
        {
            "run_id": "impl-fresh-terminal",
            "state": "failed",
            "health": "final",
            "liveness": "terminal",
            "updated_at": fresh_stamp,
            "completed_at": fresh_stamp,
        },
    )
    # Live run — untouched.
    _write_snapshot(
        home,
        {
            "run_id": "impl-live",
            "state": "active",
            "health": "active",
            "updated_at": fresh_stamp,
        },
    )

    counts = control_plane.drain_settled_snapshots(keep_hours=24.0, batch_size=2)

    assert counts["settled"] == 2
    assert counts["archived"] == 1
    assert counts["kept_recent"] == 1
    assert counts["skipped_live"] == 1
    assert counts["retained"] == 2
    archived = control_plane._snapshot_archive_dir() / "impl-old-parked.json"
    assert archived.is_file()
    archived_payload = json.loads(archived.read_text(encoding="utf-8"))
    # Settlement precedes gc: the archived run carries a written terminal.
    assert archived_payload["settlement_verdict"]
    retained = json.loads(
        (home / "control_plane" / "runs" / "impl-fresh-terminal.json").read_text(
            encoding="utf-8"
        )
    )
    assert retained["settlement_verdict"]
    # Idempotent: a second drain changes nothing.
    again = control_plane.drain_settled_snapshots(keep_hours=24.0, batch_size=2)
    assert again["settled"] == 0
    assert again["archived"] == 0


def test_sync_state_does_not_resurrect_archived_runs_from_meta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    old_stamp = "2026-05-19T00:00:00+00:00"
    # Launcher meta outlives the snapshot — the resurrection source.
    _write_meta(
        home,
        {
            "run_id": "impl-archived-1",
            "status": "completed",
            "agent": "codex",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": old_stamp,
            "skill_code": "impl",
            "exit_code": 0,
            "liveness": "terminal",
        },
    )
    snapshot_path = _write_snapshot(
        home,
        {
            "run_id": "impl-archived-1",
            "state": "completed",
            "health": "final",
            "liveness": "terminal",
            "updated_at": old_stamp,
            "completed_at": old_stamp,
            "settlement_verdict": "needs_attention",
            "settlement_reason": "report_without_seal",
            "settlement_at": old_stamp,
            "settlement_source": "auto",
            "settlement_tui": "n",
        },
    )
    archive_dir = control_plane._snapshot_archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path.replace(archive_dir / snapshot_path.name)

    board = control_plane.sync_state()

    assert not (home / "control_plane" / "runs" / "impl-archived-1.json").exists()
    assert all(run.get("run_id") != "impl-archived-1" for run in board["recent_runs"])
    assert board["settlement_counts"]["n"] == 0
    # The archived projection still resolves for a direct scoped lookup.
    looked_up = control_plane.lookup_run("impl-archived-1")
    assert looked_up is not None
    assert looked_up["settlement_verdict"] == "needs_attention"


def test_sync_state_keeps_retained_snapshot_only_runs_on_board(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After event rotation a retained snapshot is the only trace of a run."""
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    _write_snapshot(
        home,
        {
            "run_id": "impl-snapshot-only",
            "state": "failed",
            "health": "final",
            "liveness": "terminal",
            "updated_at": stamp,
            "completed_at": stamp,
            "settlement_verdict": "failed",
            "settlement_reason": "execution_failed",
            "settlement_at": stamp,
            "settlement_source": "auto",
            "settlement_tui": "x",
        },
    )

    board = control_plane.sync_state()

    run_ids = {run.get("run_id") for run in board["recent_runs"]}
    assert "impl-snapshot-only" in run_ids
    assert board["settlement_counts"]["x"] == 1


def test_sync_state_rotates_oversized_stream_without_tail_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_EVENTS_ROTATE_BYTES", "512")
    events = control_plane.event_stream_path()
    for index in range(40):
        control_plane._append_event(
            {
                "ts": f"2026-05-19T00:00:{index:02d}+00:00",
                "run_id": "impl-rotate-1",
                "kind": "state",
                "message": f"tick {index}",
                "payload": {"state": "active", "agent": "codex"},
            }
        )

    board = control_plane.sync_state()

    archive = control_plane._events_archive_dir()
    rotated = list(archive.glob("events-*.jsonl"))
    assert len(rotated) == 1
    archived_text = rotated[0].read_text(encoding="utf-8")
    assert "tick 39" in archived_text
    assert 'tick 0"' in archived_text
    # The new segment is a header only. Tail copying would duplicate effects
    # during an archived-generation reconnect.
    fresh = [
        json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["kind"] for event in fresh] == ["stream.segment"]
    assert fresh[0]["payload"]["schema"] == control_plane.EVENT_SEGMENT_SCHEMA
    assert fresh[0]["payload"]["generation"] == 1
    assert any(
        event["message"] == "tick 39"
        for event in control_plane.read_event_tail(control_plane.EVENT_TAIL_LIMIT)
    )
    # The run projected before rotation stays resolvable from its snapshot.
    assert control_plane.lookup_run("impl-rotate-1") is not None
    assert any(run.get("run_id") == "impl-rotate-1" for run in board["recent_runs"])


def test_append_during_rotation_lands_once_in_new_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_EVENTS_ROTATE_BYTES", "1")
    control_plane._append_event(
        {
            "ts": "2026-07-26T06:29:00+00:00",
            "run_id": "before-rotation",
            "kind": "state",
            "message": "old generation",
            "payload": {},
        }
    )

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(
        target=_append_event_in_process,
        args=(str(home), ready),
    )
    with control_plane._event_lock(exclusive=True):
        process.start()
        assert ready.wait(timeout=5)
        time.sleep(0.1)
        assert process.is_alive(), "append must wait behind rotation EX"
        rotated_path = control_plane._rotate_event_stream_locked()
        assert rotated_path is not None
    process.join(timeout=10)
    assert process.exitcode == 0

    records: list[dict[str, object]] = []
    paths = list(control_plane._events_archive_dir().glob("events-*.jsonl"))
    paths.append(control_plane.event_stream_path())
    for path in paths:
        records.extend(
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        )
    raced = [
        record for record in records if record.get("run_id") == "append-during-rotation"
    ]
    assert len(raced) == 1
    assert raced[0]["message"] == "must land exactly once"
    active = [
        json.loads(line)
        for line in control_plane.event_stream_path()
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert active[0]["kind"] == "stream.segment"
    assert active[0]["payload"]["generation"] == 1
    assert active[1]["run_id"] == "append-during-rotation"


def test_short_event_append_rolls_back_before_later_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    control_plane._append_event(
        {
            "ts": "2026-07-26T07:00:00+00:00",
            "run_id": "before-short-write",
            "kind": "unit",
            "message": "complete before",
            "payload": {},
        }
    )
    real_write = control_plane.os.write

    def short_write(fd: int, data: bytes) -> int:
        prefix = data[: max(1, len(data) // 2)]
        return real_write(fd, prefix)

    with monkeypatch.context() as scoped:
        scoped.setattr(control_plane.os, "write", short_write)
        with pytest.raises(OSError, match="short atomic event append"):
            control_plane._append_event(
                {
                    "ts": "2026-07-26T07:00:01+00:00",
                    "run_id": "short-write",
                    "kind": "unit",
                    "message": "must roll back",
                    "payload": {},
                }
            )

    control_plane._append_event(
        {
            "ts": "2026-07-26T07:00:02+00:00",
            "run_id": "after-short-write",
            "kind": "unit",
            "message": "complete after",
            "payload": {},
        }
    )
    records = [
        json.loads(line)
        for line in control_plane.event_stream_path()
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [record.get("run_id") for record in records[1:]] == [
        "before-short-write",
        "after-short-write",
    ]


def test_next_event_repairs_incomplete_tail_left_by_dead_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    control_plane._append_event(
        {
            "ts": "2026-07-26T07:01:00+00:00",
            "run_id": "before-dead-writer",
            "kind": "unit",
            "message": "complete before",
            "payload": {},
        }
    )
    stream = control_plane.event_stream_path()
    with control_plane._event_lock(exclusive=True):
        fd = control_plane.os.open(stream, control_plane.os.O_WRONLY | os.O_APPEND)
        try:
            control_plane.os.write(fd, b'{"run_id":"dead-writer"')
            control_plane.os.fsync(fd)
        finally:
            control_plane.os.close(fd)

    control_plane._append_event(
        {
            "ts": "2026-07-26T07:01:01+00:00",
            "run_id": "after-dead-writer",
            "kind": "unit",
            "message": "complete after",
            "payload": {},
        }
    )
    records = [
        json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()
    ]
    assert [record.get("run_id") for record in records[1:]] == [
        "before-dead-writer",
        "after-dead-writer",
    ]


def test_legacy_subscriber_hides_segment_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setattr(control_plane.time, "sleep", lambda _seconds: None)
    control_plane._append_event(
        {
            "ts": "2026-07-26T06:30:00+00:00",
            "run_id": "legacy-subscriber",
            "kind": "unit",
            "message": "visible event",
            "payload": {},
        }
    )

    events = list(control_plane.subscribe_events())

    assert [(event.kind, event.run_id) for event in events] == [
        ("stream.boundary", ""),
        ("unit", "legacy-subscriber"),
    ]
    assert events[0].payload["from"] == "0"
    assert events[0].payload["to"].startswith("v2:")
    assert events[1].cursor.startswith("v2:")


def test_python_subscriber_rotation_larger_and_smaller_emits_gap_or_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setenv("VIBECRAFTED_EVENTS_ROTATE_BYTES", "1")
    monkeypatch.setenv("VIBECRAFTED_EVENTS_ARCHIVE_MAX_FILES", "8")
    monkeypatch.setenv("VIBECRAFTED_EVENTS_ARCHIVE_MAX_BYTES", str(8 * 1024 * 1024))

    def _append(message: str) -> None:
        control_plane._append_event(
            {
                "ts": "2026-07-26T06:30:00+00:00",
                "run_id": "subscriber-rotation",
                "kind": "unit",
                "message": message,
                "payload": {},
            }
        )

    _append("old")
    initial = list(control_plane.subscribe_events())
    saved = next(event.cursor for event in initial if event.message == "old")
    with control_plane._event_lock(exclusive=True):
        assert control_plane._rotate_event_stream_locked() is not None
    _append("larger-" + ("x" * 2000))

    larger = list(control_plane.subscribe_events(saved))
    assert any(event.kind == "stream.boundary" for event in larger)
    larger_event = next(event for event in larger if event.kind == "unit")
    assert larger_event.message.startswith("larger-")

    with control_plane._event_lock(exclusive=True):
        assert control_plane._rotate_event_stream_locked() is not None
    _append("small")
    smaller = list(control_plane.subscribe_events(larger_event.cursor))

    assert any(event.kind == "stream.boundary" for event in smaller)
    assert [event.message for event in smaller if event.kind == "unit"] == ["small"]
    assert all(event.kind != "stream.gap" for event in larger + smaller)


def test_retention_prunes_and_expired_cursor_emits_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setenv("VIBECRAFTED_EVENTS_ROTATE_BYTES", "1")
    monkeypatch.setenv("VIBECRAFTED_EVENTS_ARCHIVE_MAX_FILES", "1")
    monkeypatch.setenv("VIBECRAFTED_EVENTS_ARCHIVE_MAX_BYTES", str(8 * 1024 * 1024))

    control_plane._append_event(
        {
            "ts": "2026-07-26T06:30:00+00:00",
            "run_id": "retention",
            "kind": "unit",
            "message": "generation-zero",
            "payload": {},
        }
    )
    initial = list(control_plane.subscribe_events())
    expired = next(event.cursor for event in initial if event.kind == "unit")
    with control_plane._event_lock(exclusive=True):
        assert control_plane._rotate_event_stream_locked() is not None
    control_plane._append_event(
        {
            "ts": "2026-07-26T06:31:00+00:00",
            "run_id": "retention",
            "kind": "unit",
            "message": "generation-one",
            "payload": {},
        }
    )
    with control_plane._event_lock(exclusive=True):
        assert control_plane._rotate_event_stream_locked() is not None

    archives = list(control_plane._events_archive_dir().glob("events-*.jsonl"))
    assert len(archives) == 1
    recovered = list(control_plane.subscribe_events(expired))
    gaps = [event for event in recovered if event.kind == "stream.gap"]
    assert len(gaps) == 1
    assert gaps[0].payload["reason"] == "generation_expired_or_unknown"
    assert gaps[0].payload["action"] == "resnapshot"
    assert gaps[0].cursor.startswith("v2:")


def test_lock_busy_message_names_install_doctor_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_SYNC_LOCK_TIMEOUT_S", "0.1")
    control_plane.control_plane_home().mkdir(parents=True, exist_ok=True)
    holder = control_plane._sync_lock_path().open("a+", encoding="utf-8")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        with pytest.raises(control_plane.ControlPlaneLockBusy) as excinfo:
            control_plane.sync_state()
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
    message = str(excinfo.value)
    assert "install/doctor sync in progress; retry" in message
    assert "stuck run" in message


def test_await_status_with_run_id_is_lockless_during_board_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`vibecrafted await <agent> --run-id` must survive a concurrent full sync."""
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_SYNC_LOCK_TIMEOUT_S", "0.2")
    _write_meta(
        home,
        {
            "run_id": "impl-await-lockless",
            "status": "running",
            "agent": "grok",
            "mode": "implement",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "skill_code": "impl",
            "launcher_pid": os.getpid(),
            "liveness": "pid_alive",
        },
    )
    control_plane.control_plane_home().mkdir(parents=True, exist_ok=True)

    from vibecrafted_core import cli as core_cli

    holder = control_plane._sync_lock_path().open("a+", encoding="utf-8")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        run = core_cli._run_for_agent("grok", "impl-await-lockless")
        assert run is not None
        assert run["run_id"] == "impl-await-lockless"
        # run_liveness is the other supervisor-hot probe — also lockless now.
        liveness = control_plane.run_liveness("impl-await-lockless")
        assert liveness["found"] is True
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


# --- stall-detector truth: silence is not death, finalization is terminal ----
# Field evidence 2026-07-25 (work-260725-111347-98000, work-260725-103320-02000,
# work-260725-052543-65000, impl-260725-032908-13000): every one of these runs
# carried heartbeat_at == started_at, exit_code None and completed_at "", while
# its report was already on disk. Three separate edges were missing.


def test_await_run_refuses_untouched_launcher_template_as_delivery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pre-seeded identity shell is transport scaffolding, never a handoff.

    The launcher materializes the report path at spawn time, so `size > 0` was
    true from the run's first second. Await read that as ``report_delivered``
    and answered rc=0 the moment the worker died — a green verdict on a run
    that never wrote a word. Delivery now requires worker evidence.
    """
    from vibecrafted_core.report_contract import materialize_launcher_report_template

    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "100000")
    report = tmp_path / "shell-report.md"
    assert materialize_launcher_report_template(
        report, run_id="wflw-template-7", agent="grok", skill="workflow"
    )
    assert report.stat().st_size > 0
    _write_meta(
        home,
        {
            "run_id": "wflw-template-7",
            "status": "running",
            "agent": "grok",
            "mode": "workflow",
            "skill_code": "wflw",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:00:00+00:00",
            "worker_pid": 999999999,
            "worker_pgid": 999999999,
            "liveness": "pid_alive",
        },
    )

    payload = control_plane.await_run(
        "wflw-template-7",
        timeout_seconds=0.2,
        interval_seconds=0.05,
        hard_cap_seconds=5,
        report_path=str(report),
    )

    assert payload["reason"] != "report_delivered"
    assert payload["completed"] is True
    assert payload["await_rc"] != 0

    # A late report cannot revive a run after qualified ownership loss was
    # durably settled failed; retry must create a new run identity.
    report.write_text(
        "---\n"
        "run_id: wflw-template-7\n"
        "agent: grok\n"
        "skill: workflow\n"
        "status: blocked\n"
        "finalized: false\n"
        "---\n\n"
        "Blocked on a missing credential.\n",
        encoding="utf-8",
    )
    delivered = control_plane.await_run(
        "wflw-template-7",
        timeout_seconds=5,
        interval_seconds=0.05,
        hard_cap_seconds=5,
        report_path=str(report),
    )
    assert delivered["completed"] is True
    assert delivered["reason"] == "terminal"
    assert delivered["run"]["state"] == "failed"
    assert delivered["run"]["settlement_verdict"] == "failed"


def test_sync_state_closes_stalled_run_when_report_attests_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Finalization is a terminal transition, even from `stalled`.

    Live case work-260725-111347-98000: the report was finalized, the commit
    landed, the worker exited — and the control plane held `state=stalled`
    forever because exit_code and completed_at were never recorded. The
    worker's own `finalized: true` + `claim` attestation is the terminal proof.
    """
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "1")
    monkeypatch.setenv("VIBECRAFTED_RUN_GC_GRACE_SECONDS", "999999999")
    report = tmp_path / "finalized-report.md"
    report.write_text(
        "---\n"
        "run_id: wflw-finalized-9\n"
        "agent: grok\n"
        "skill: workflow\n"
        "status: completed\n"
        # Mirrors work-260725-111347-98000: the top-level lifecycle status is
        # complete, but the agent used an unrecognized evidence adjective in
        # claim_status. That must not erase the explicit completion status.
        "claim_status: verified\n"
        "finalized: true\n"
        "claim: literal boost shipped and gates are green\n"
        "---\n\n"
        "## Report\n",
        encoding="utf-8",
    )
    _write_meta(
        home,
        {
            "run_id": "wflw-finalized-9",
            "status": "running",
            "agent": "grok",
            "mode": "workflow",
            "skill_code": "wflw",
            "root": str(tmp_path),
            "updated_at": "2026-05-19T00:02:00+00:00",
            "heartbeat_at": "2026-05-19T00:00:00+00:00",
            "report": str(report),
            "launcher_pid": 999999999,
            "liveness": "pid_alive",
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] == "completed"
    assert run["health"] == "final"
    assert run["liveness"] == "terminal"
    assert run["completed_at"]
    assert not run.get("recovery_required")
    assert all(item["run_id"] != "wflw-finalized-9" for item in snapshot["active_runs"])


def test_sync_state_keeps_run_alive_while_transcript_is_still_growing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Silence in the token stream during a long tool call is not death.

    Live case work-260725-103320-02000: the agent was mid `cargo build` (10+
    min of no tokens) and the detector called it stalled. `heartbeat_at` is
    stamped once, at first output, so it cannot carry a run's liveness alone —
    a growing transcript is the proof that the worker is still speaking.
    """
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LIVENESS_STALE_HEARTBEAT_SECONDS", "120")
    transcript = tmp_path / "transcript.log"
    transcript.write_text(
        "release build finished. Let me check if install ran", "utf-8"
    )
    # Mirrors the live record exactly: projection kept updated_at fresh while
    # heartbeat_at stayed frozen at the run's first output 43 minutes earlier.
    now = dt.datetime.now(dt.timezone.utc)
    _write_meta(
        home,
        {
            "run_id": "wflw-building-3",
            "status": "running",
            "agent": "grok",
            "mode": "workflow",
            "skill_code": "wflw",
            "root": str(tmp_path),
            "updated_at": now.isoformat(),
            # Ancient heartbeat: stamped once at first output, never refreshed.
            "heartbeat_at": (now - dt.timedelta(minutes=43)).isoformat(),
            "transcript": str(transcript),
            "launcher_pid": 999999999,
            "liveness": "pid_alive",
        },
    )

    snapshot = control_plane.sync_state()
    run = snapshot["recent_runs"][0]

    assert run["state"] != "stalled"
    assert run["health"] != "stalled"
    assert run["liveness"] != "pid_gone"
    assert not run.get("recovery_required")


def test_append_last_error_collapses_repeated_watchdog_notes() -> None:
    """The reconciler must not rewrite its own sentence on every projection.

    Live records carried a dozen copies of the same note, differing only in the
    stale-seconds count, which pushed the real first cause off the operator's
    screen.
    """
    chain = ""
    for age in (126, 9153, 9158, 9536, 9616):
        chain = control_plane._append_last_error(
            chain,
            f"launcher_pid 63148 is not alive; no worker activity for {age}s "
            f"(threshold 120s); no live launcher proof; recovery_required",
        )

    assert chain.count("launcher_pid 63148 is not alive") == 1
    assert "9616s" in chain
    assert "9153s" not in chain

    # Distinct causes are still preserved, and the chain stays bounded.
    chain = control_plane._append_last_error(chain, "artifact contract failed")
    assert "artifact contract failed" in chain
    assert "no worker activity for 9616s" in chain
