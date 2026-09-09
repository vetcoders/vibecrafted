"""Interactive Agent Workspace → canonical snapshot projection.

The public server projection (``/api/control/state`` → control-core
``read_state_view``) trusts ``control_plane/runs/<id>.json`` and never folds
``runtime_runs/`` or the event stream itself. Before this cut an interactive
``init``/``operator``/``partner`` provider published only ``meta.json`` plus a
``lifecycle:active`` event, so three living providers rendered
``active_runs=[]`` on a real installed candidate until some observer happened
to call ``sync_state``. These tests pin the writer contract: the launcher
publishes the scoped projection itself, an idle provider stays provably live,
exit is terminal, and unrelated runs are never promoted by that publication.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pty
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from vibecrafted_core import control_plane, process_control
from vibecrafted_core.workflow import _stop_signal_target

CORE_DIR = Path(__file__).resolve().parents[1]


def _fake_blocking_provider(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "if '--help' in sys.argv:\n"
        "  print('  --session-id <uuid>')\n"
        "  raise SystemExit(0)\n"
        "if '--version' in sys.argv:\n"
        "  print('2.1.232 (Claude Code)')\n"
        "  raise SystemExit(0)\n"
        "capture = pathlib.Path(os.environ['SMOKE_CAPTURE'])\n"
        "capture.write_text(json.dumps({\n"
        "  'pid': os.getpid(), 'pgid': os.getpgid(0),\n"
        "  'run_id': os.environ['VIBECRAFTED_RUN_ID'],\n"
        "}) + '\\n', encoding='utf-8')\n"
        "if os.environ.get('SMOKE_BLOCK') == '1':\n"
        "  while True: time.sleep(0.05)\n"
        "raise SystemExit(int(os.environ.get('SMOKE_EXIT', '0')))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _fake_supervision_provider(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "if '--help' in sys.argv:\n"
        "  print('  --session-id <uuid>')\n"
        "  raise SystemExit(0)\n"
        "if '--version' in sys.argv:\n"
        "  print('2.1.232 (Claude Code)')\n"
        "  raise SystemExit(0)\n"
        "role = os.environ['VIBECRAFTED_AGENT_ROLE']\n"
        "capture = pathlib.Path(os.environ['SUPERVISION_CAPTURES']) / f'{role}.json'\n"
        "capture.write_text(json.dumps({\n"
        "  'pid': os.getpid(), 'role': role,\n"
        "  'run_id': os.environ['VIBECRAFTED_RUN_ID'],\n"
        "}) + '\\n', encoding='utf-8')\n"
        "if role == 'agent': raise SystemExit(0)\n"
        "while True: time.sleep(0.05)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _wait_for(path: Path, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def _interactive_argv(repo: Path, *extra: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "vibecrafted_core.spawn",
        "interactive-launch",
        "claude",
        "--runtime",
        "local-native",
        "--permissions",
        "read-only",
        "--token-budget",
        "unmetered",
        "--root",
        str(repo),
        "--prompt",
        "/vc-init",
        *extra,
    ]


def _launcher_env(home: Path, fake_bin: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        VIBECRAFTED_HOME=str(home),
        VIBECRAFTED_RUNTIME_BIN=str(fake_bin),
        PATH=str(fake_bin) + os.pathsep + env["PATH"],
    )
    env.update(overrides)
    return env


def _snapshot(home: Path, run_id: str) -> dict[str, object]:
    return json.loads(
        (home / "control_plane" / "runs" / f"{run_id}.json").read_text(encoding="utf-8")
    )


def _dead_pid() -> int:
    probe = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
    probe.wait()
    return probe.pid


def _seed_foreign_stale_run(home: Path, root: Path) -> str:
    """A dead, long-idle headless run that only ever reached the event stream."""
    run_id = "impl-260101-000000-00001"
    control_plane._append_event(
        {
            "ts": "2026-01-01T00:00:00+00:00",
            "run_id": run_id,
            "kind": "lifecycle:active",
            "message": "foreign stale worker",
            "payload": {
                "state": "active",
                "agent": "codex",
                "skill": "implement",
                "mode": "implement",
                "root": str(root),
                "liveness": "pid_alive",
                "worker_pid": _dead_pid(),
                "launcher_pid": _dead_pid(),
                "identity_required": True,
            },
        }
    )
    return run_id


def test_interactive_launch_publishes_live_projection_without_observer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    foreign_root = tmp_path / "foreign"
    foreign_root.mkdir()
    foreign_run_id = _seed_foreign_stale_run(home, foreign_root)
    capture = tmp_path / "provider.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_blocking_provider(fake_bin / "claude")
    env = _launcher_env(home, fake_bin, SMOKE_CAPTURE=str(capture), SMOKE_BLOCK="1")
    master_fd, slave_fd = pty.openpty()
    owner = subprocess.Popen(
        _interactive_argv(repo),
        cwd=CORE_DIR,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
    )
    os.close(slave_fd)
    snapshots = home / "control_plane" / "runs"
    try:
        _wait_for(capture)
        observed = json.loads(capture.read_text(encoding="utf-8"))
        run_id = observed["run_id"]

        # The launcher itself publishes the canonical snapshot: no observer,
        # await, or status call runs between launch and this read.
        _wait_for(snapshots / f"{run_id}.json")
        live = _snapshot(home, run_id)
        assert live["state"] == "active"
        assert live["health"] == "active"
        assert live["liveness"] == "active"
        assert live["mode"] == "interactive"
        assert live["owner_pid"] == owner.pid
        assert live["worker_pid"] == observed["pid"]
        assert live["worker_identity"]["pid"] == observed["pid"]
        assert live["worker_identity"]["pgid"] == observed["pgid"]
        assert live["worker_identity"]["run_id"] == run_id
        assert live["owner_identity"]["pid"] == owner.pid
        # The pane process group is never a stop-signal target: the provider
        # shares it with the User's shell, and worker_pgid is killpg's first pick.
        assert "worker_pgid" not in live
        assert _stop_signal_target(live) == ("worker_pid", observed["pid"])
        # Scoped publication: the dead foreign run stays unprojected.
        assert not (snapshots / f"{foreign_run_id}.json").exists()
        assert sorted(path.stem for path in snapshots.glob("*.json")) == [run_id]

        # An idle provider stays visibly live far past the heartbeat stall
        # window because its identity receipt proves the same process is alive.
        idle_now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            seconds=control_plane.RUN_STALL_SECONDS + 600
        )
        monkeypatch.setattr(control_plane, "_now", lambda: idle_now)
        board = control_plane.sync_state()
        monkeypatch.setattr(control_plane, "_now", control_plane.utc_now)
        idle = {run["run_id"]: run for run in board["recent_runs"]}[run_id]
        assert idle["health"] == "active"
        assert idle["process_truth"] == "live"
        assert idle["worker_alive"] is True
        assert [run["run_id"] for run in board["active_runs"]] == [run_id]
        assert foreign_run_id not in {run["run_id"] for run in board["active_runs"]}

        owner.send_signal(signal.SIGTERM)
        assert owner.wait(timeout=5) == 128 + signal.SIGTERM
        # Exit is terminal in the launcher's own projection, not a lagging
        # active snapshot that only a later observer would settle.
        terminal = _snapshot(home, run_id)
        assert terminal["state"] == "cancelled"
        assert terminal["health"] == "final"
        assert terminal["liveness"] == "terminal"
        assert terminal["exit_code"] == 128 + signal.SIGTERM
        assert terminal["completed_at"]
        assert terminal["worker_alive"] is False
        assert terminal["lifecycle"]["await"] is False
        assert terminal["lifecycle"]["stop"] is False
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait()
        os.close(master_fd)


def test_supervised_launch_publishes_projection_for_both_runs(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    captures = tmp_path / "captures"
    captures.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_supervision_provider(fake_bin / "claude")
    env = _launcher_env(home, fake_bin, SUPERVISION_CAPTURES=str(captures))
    completed = subprocess.run(
        _interactive_argv(repo, "--operator", "auto"),
        cwd=CORE_DIR,
        env=env,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0
    captured = {
        role: json.loads((captures / f"{role}.json").read_text(encoding="utf-8"))
        for role in ("operator", "agent")
    }
    agent = _snapshot(home, captured["agent"]["run_id"])
    operator = _snapshot(home, captured["operator"]["run_id"])
    assert agent["state"] == "completed"
    assert agent["health"] == "final"
    assert agent["liveness"] == "terminal"
    assert agent["exit_code"] == 0
    assert operator["state"] == "completed"
    assert operator["health"] == "final"
    assert operator["liveness"] == "terminal"
    for snapshot in (agent, operator):
        assert "worker_pgid" not in snapshot
        assert snapshot["worker_identity"]["run_id"] == snapshot["run_id"]


def test_worker_process_truth_proves_interactive_identity_without_pgid_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    run_id = "init-260909-000000-00001"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        worker_identity = process_control.process_identity_receipt(
            child.pid, run_id=run_id
        )
        owner_identity = process_control.process_identity_receipt(
            os.getpid(), run_id=run_id
        )
        assert worker_identity is not None and owner_identity is not None
        run = {
            "run_id": run_id,
            "state": "active",
            "owner_pid": os.getpid(),
            "owner_identity": owner_identity,
            "worker_pid": child.pid,
            "worker_identity": worker_identity,
        }
        assert control_plane._worker_process_truth(run) == (
            True,
            "process_identity_current",
        )
        assert control_plane._worker_is_alive(run) is True
        # Observation never manufactures a group target for the stop path.
        assert _stop_signal_target(run) == ("worker_pid", child.pid)
        assert "worker_pgid" not in run
    finally:
        child.kill()
        child.wait()
    alive, reason = control_plane._worker_process_truth(run)
    assert alive is False
    assert reason == "process_identity_gone"


def test_reconcile_honours_owner_terminalized_cancelled_receipt() -> None:
    # `cancelled` is not a FINAL_STATES member; the owner's atomic terminal
    # receipt must still be closed history, never a failed/pid_gone alert.
    receipt = {
        "run_id": "init-260909-000000-00002",
        "state": "cancelled",
        "liveness": "terminal",
        "exit_code": 128 + signal.SIGTERM,
        "completed_at": "2026-09-09T04:30:00+00:00",
        "terminal_reason": "owner_signal:SIGTERM",
        "owner_pid": _dead_pid(),
        "worker_pid": _dead_pid(),
    }
    reconciled = control_plane._reconcile_dead_launcher(dict(receipt))
    assert reconciled == receipt
