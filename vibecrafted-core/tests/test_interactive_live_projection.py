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


# --- Owned, bounded recovery of a deferred publication -----------------------
#
# The transient fault is injected below the writer boundary, in the real
# filesystem: ``control_plane/runs`` is made non-writable, so the scoped
# ``sync_state`` raises the exact storage error ``_project_interactive_snapshot``
# reports. Nothing in production code knows it is under test. Releasing the
# directory while the provider idles must be enough — no observe/status/await
# runs between the release and the read.


def _snapshot_dir(home: Path) -> Path:
    path = home / "control_plane" / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _events_for(home: Path, run_id: str) -> list[dict[str, object]]:
    stream = home / "control_plane" / "events.jsonl"
    if not stream.is_file():
        return []
    events: list[dict[str, object]] = []
    for line in stream.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("run_id") == run_id:
            events.append(event)
    return events


def _meta(home: Path, run_id: str) -> dict[str, object]:
    path = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json(path: Path) -> dict[str, object] | None:
    """Read a JSON object that may still be mid-write. None is not-yet-ready."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _projection_receipt(home: Path, run_id: str) -> dict[str, object] | None:
    meta = _load_json(home / "control_plane" / "runtime_runs" / run_id / "meta.json")
    if meta is None or meta.get("run_id") != run_id:
        return None
    projection = meta.get("projection")
    return projection if isinstance(projection, dict) else None


def _active_snapshot_and_published_receipt(
    home: Path, run_id: str
) -> tuple[dict[str, object], dict[str, object]] | None:
    """True only when the active snapshot and published stamp name the same run.

    ``_InteractiveProjection._attempt`` writes the canonical snapshot first,
    then ``_stamp`` mirrors ``status=published`` into a separate meta file.
    Waiting on the snapshot path alone can observe that gap. This predicate
    never calls ``sync_state`` and never invents a receipt.
    """
    snapshot = _load_json(home / "control_plane" / "runs" / f"{run_id}.json")
    if (
        snapshot is None
        or snapshot.get("run_id") != run_id
        or snapshot.get("state") != "active"
    ):
        return None
    meta = _load_json(home / "control_plane" / "runtime_runs" / run_id / "meta.json")
    if meta is None or meta.get("run_id") != run_id:
        return None
    projection = meta.get("projection")
    if not isinstance(projection, dict) or projection.get("status") != "published":
        return None
    return snapshot, meta


def _wait_until(predicate, *, timeout: float, what: str, evidence=None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    detail = ""
    if evidence is not None:
        try:
            detail = f"; observed {evidence()}"
        except (OSError, TypeError, ValueError) as exc:
            detail = f"; evidence unavailable: {type(exc).__name__}: {exc}"
    raise AssertionError(f"timed out waiting for {what}{detail}")


def _drain_pty(master_fd: int) -> str:
    import select

    chunks: list[bytes] = []
    while True:
        ready, _, _ = select.select([master_fd], [], [], 0.2)
        if not ready:
            break
        try:
            chunk = os.read(master_fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _wait_owner(
    owner: subprocess.Popen[bytes], master_fd: int, *, timeout: float
) -> tuple[int, str]:
    """Wait for the owner while draining its pty (a full pty blocks stderr)."""
    deadline = time.monotonic() + timeout
    output: list[str] = []
    while True:
        output.append(_drain_pty(master_fd))
        code = owner.poll()
        if code is not None:
            output.append(_drain_pty(master_fd))
            return code, "".join(output)
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for the owner to exit")


def _spawn_blocking_owner(
    tmp_path: Path, home: Path
) -> tuple[subprocess.Popen[bytes], int, Path]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    capture = tmp_path / "provider.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
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
    return owner, master_fd, capture


def test_active_projection_recovers_after_transient_storage_failure(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    snapshots = _snapshot_dir(home)
    snapshots.chmod(0o500)  # transient: the ACTIVE publication cannot land
    owner, master_fd, capture = _spawn_blocking_owner(tmp_path, home)
    try:
        _wait_for(capture)
        run_id = json.loads(capture.read_text(encoding="utf-8"))["run_id"]
        _wait_until(
            lambda: any(
                e["kind"] == "lifecycle:active" for e in _events_for(home, run_id)
            ),
            timeout=5.0,
            what="lifecycle:active event",
        )

        def _deferred_without_snapshot() -> bool:
            if (snapshots / f"{run_id}.json").exists():
                return False
            receipt = _projection_receipt(home, run_id)
            return bool(
                receipt
                and receipt.get("status") == "pending"
                and receipt.get("first_failed_at")
                and int(receipt.get("attempts") or 0) >= 1
            )

        # The fault held: durable meta + deferred receipt, no snapshot yet.
        _wait_until(
            _deferred_without_snapshot,
            timeout=5.0,
            what="deferred publication receipt while the snapshot is still missing",
            evidence=lambda: (
                f"snapshot_exists={(snapshots / f'{run_id}.json').exists()} "
                f"projection={_projection_receipt(home, run_id)!r}"
            ),
        )
        assert not (snapshots / f"{run_id}.json").exists()
        assert _meta(home, run_id)["status"] == "active"

        # Release the contention while the provider idles. Nothing else runs.
        # Snapshot-then-meta is a legitimate gap: wait for both, not the path.
        snapshots.chmod(0o755)
        converged: list[tuple[dict[str, object], dict[str, object]]] = []

        def _both_ready() -> bool:
            found = _active_snapshot_and_published_receipt(home, run_id)
            if found is None:
                return False
            converged.clear()
            converged.append(found)
            return True

        _wait_until(
            _both_ready,
            timeout=6.0,
            what=f"active snapshot and published receipt for {run_id}",
            evidence=lambda: (
                f"snapshot={_load_json(snapshots / f'{run_id}.json')!r} "
                f"projection={_projection_receipt(home, run_id)!r}"
            ),
        )
        live, published_meta = converged[0]
        assert live["state"] == "active"
        assert live["health"] == "active"
        assert live["liveness"] == "active"
        assert "worker_pgid" not in live
        assert _stop_signal_target(live) == ("worker_pid", live["worker_pid"])
        assert sorted(path.stem for path in snapshots.glob("*.json")) == [run_id]
        projection = published_meta["projection"]
        assert isinstance(projection, dict)
        assert projection["status"] == "published"
        assert projection["attempts"] >= 2
        assert projection["first_failed_at"]
        assert "PermissionError" in _drain_pty(master_fd)

        owner.send_signal(signal.SIGTERM)
        assert _wait_owner(owner, master_fd, timeout=10)[0] == 128 + signal.SIGTERM
        terminal = _snapshot(home, run_id)
        assert terminal["state"] == "cancelled"
        assert terminal["liveness"] == "terminal"
    finally:
        snapshots.chmod(0o755)
        if owner.poll() is None:
            owner.kill()
            owner.wait()
        os.close(master_fd)


def test_snapshot_before_meta_gap_is_not_publication_success(tmp_path: Path) -> None:
    """The snapshot-before-stamp gap is not publication success.

    ``_attempt`` can leave an active snapshot on disk while meta still says
    pending. The recovery wait must stay red until the published receipt
    arrives for the same run, including when it never does.
    """
    home = tmp_path / "home"
    run_id = "init-260910-000000-00007"
    snapshots = _snapshot_dir(home)
    snapshot_path = snapshots / f"{run_id}.json"
    meta_path = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    meta_path.parent.mkdir(parents=True)
    snapshot_body = {
        "run_id": run_id,
        "state": "active",
        "health": "active",
        "liveness": "active",
    }
    pending_projection = {
        "status": "pending",
        "attempts": 2,
        "first_failed_at": "2026-09-10T00:00:00+00:00",
        "last_error": "PermissionError: [Errno 13] Permission denied",
    }
    pending_meta = {
        "run_id": run_id,
        "status": "active",
        "projection": dict(pending_projection),
    }
    snapshot_path.write_text("{", encoding="utf-8")
    assert _load_json(snapshot_path) is None
    snapshot_path.write_text(json.dumps(snapshot_body), encoding="utf-8")
    assert _active_snapshot_and_published_receipt(home, run_id) is None
    meta_path.write_text(json.dumps(pending_meta), encoding="utf-8")
    assert _active_snapshot_and_published_receipt(home, run_id) is None
    with pytest.raises(AssertionError, match="published receipt") as timed_out:
        _wait_until(
            lambda: _active_snapshot_and_published_receipt(home, run_id) is not None,
            timeout=0.08,
            what=f"active snapshot and published receipt for {run_id}",
            evidence=lambda: f"projection={_projection_receipt(home, run_id)!r}",
        )
    assert "pending" in str(timed_out.value)
    published = {
        **pending_meta,
        "projection": {
            **pending_projection,
            "status": "published",
            "published_at": "2026-09-10T00:00:01+00:00",
        },
    }
    meta_path.write_text(json.dumps(published), encoding="utf-8")
    found = _active_snapshot_and_published_receipt(home, run_id)
    assert found is not None
    live, meta = found
    assert live["run_id"] == run_id
    assert meta["projection"]["status"] == "published"


def test_terminal_projection_recovers_with_retained_terminal_truth(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    snapshots = _snapshot_dir(home)
    owner, master_fd, capture = _spawn_blocking_owner(tmp_path, home)
    try:
        _wait_for(capture)
        run_id = json.loads(capture.read_text(encoding="utf-8"))["run_id"]
        _wait_for(snapshots / f"{run_id}.json")
        assert _snapshot(home, run_id)["state"] == "active"

        snapshots.chmod(0o500)  # transient: the TERMINAL publication cannot land
        owner.send_signal(signal.SIGTERM)
        _wait_until(
            lambda: _meta(home, run_id).get("liveness") == "terminal",
            timeout=5.0,
            what="terminal meta receipt",
        )
        # Retained terminal truth is durable before any projection succeeds.
        blocked_meta = _meta(home, run_id)
        assert blocked_meta["status"] == "cancelled"
        assert blocked_meta["exit_code"] == 128 + signal.SIGTERM
        assert _snapshot(home, run_id)["state"] == "active"  # stale, not wrong
        assert owner.poll() is None  # the owner is inside its bounded flush

        time.sleep(0.6)
        snapshots.chmod(0o755)
        code, output = _wait_owner(owner, master_fd, timeout=10)
        assert code == 128 + signal.SIGTERM
        assert "terminal snapshot projection recovered" in output
        terminal = _snapshot(home, run_id)
        assert terminal["state"] == "cancelled"
        assert terminal["health"] == "final"
        assert terminal["liveness"] == "terminal"
        assert terminal["exit_code"] == 128 + signal.SIGTERM
        assert terminal["worker_alive"] is False
        assert not any(
            e["kind"] == "projection:abandoned" for e in _events_for(home, run_id)
        )
    finally:
        snapshots.chmod(0o755)
        if owner.poll() is None:
            owner.kill()
            owner.wait()
        os.close(master_fd)


def test_terminal_projection_abandons_observably_when_failure_persists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    snapshots = _snapshot_dir(home)
    owner, master_fd, capture = _spawn_blocking_owner(tmp_path, home)
    try:
        _wait_for(capture)
        run_id = json.loads(capture.read_text(encoding="utf-8"))["run_id"]
        _wait_for(snapshots / f"{run_id}.json")

        snapshots.chmod(0o500)  # persistent: never released before exit
        started = time.monotonic()
        owner.send_signal(signal.SIGTERM)
        returncode, output = _wait_owner(owner, master_fd, timeout=20)
        elapsed = time.monotonic() - started
        # Bounded owner exit: the flush budget, not an unbounded wait.
        assert returncode == 128 + signal.SIGTERM
        assert elapsed < 5.0 + 4.0
        assert _snapshot(home, run_id)["state"] == "active"  # stale, visible
        abandoned = [
            e for e in _events_for(home, run_id) if e["kind"] == "projection:abandoned"
        ]
        assert len(abandoned) == 1
        payload = abandoned[0]["payload"]
        assert payload["phase"] == "terminal"
        assert payload["attempts"] >= 2
        assert payload["state"] == "cancelled"
        assert "PermissionError" in payload["last_error"]
        assert "canonical snapshot projection abandoned" in output
        assert _meta(home, run_id)["liveness"] == "terminal"

        # The durable truth re-projects once the cause is fixed; the abandoned
        # event never becomes lifecycle authority.
        snapshots.chmod(0o755)
        # The real owner used this per-test home.  The test process itself is
        # isolated by conftest into another home, so make the public reader
        # inspect the owner's actual canonical snapshots rather than an empty
        # fixture control plane.
        monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
        board = control_plane.sync_state()
        settled = {run["run_id"]: run for run in board["recent_runs"]}[run_id]
        assert settled["state"] == "cancelled"
        assert settled["liveness"] == "terminal"
        assert settled["exit_code"] == 128 + signal.SIGTERM
    finally:
        snapshots.chmod(0o755)
        if owner.poll() is None:
            owner.kill()
            owner.wait()
        os.close(master_fd)


def test_supervised_launch_recovers_both_projections_after_transient_failure(
    tmp_path: Path,
) -> None:
    import threading

    home = tmp_path / "home"
    snapshots = _snapshot_dir(home)
    repo = tmp_path / "repo"
    repo.mkdir()
    captures = tmp_path / "captures"
    captures.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_supervision_provider(fake_bin / "claude")
    env = _launcher_env(home, fake_bin, SUPERVISION_CAPTURES=str(captures))
    snapshots.chmod(0o500)
    release = threading.Timer(1.0, lambda: snapshots.chmod(0o755))
    release.start()
    try:
        completed = subprocess.run(
            _interactive_argv(repo, "--operator", "auto"),
            cwd=CORE_DIR,
            env=env,
            check=False,
            timeout=30,
        )
    finally:
        release.cancel()
        release.join(timeout=2)
        snapshots.chmod(0o755)
    assert completed.returncode == 0
    captured = {
        role: json.loads((captures / f"{role}.json").read_text(encoding="utf-8"))
        for role in ("operator", "agent")
    }
    agent = _snapshot(home, captured["agent"]["run_id"])
    operator = _snapshot(home, captured["operator"]["run_id"])
    assert agent["state"] == "completed"
    assert agent["liveness"] == "terminal"
    assert operator["state"] == "completed"
    assert operator["liveness"] == "terminal"
    for snapshot in (agent, operator):
        assert "worker_pgid" not in snapshot
        assert not any(
            e["kind"] == "projection:abandoned"
            for e in _events_for(home, snapshot["run_id"])
        )


def test_active_projection_recovers_same_owner_after_former_attempt_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibecrafted_core import spawn

    home = Path(os.environ["VIBECRAFTED_HOME"])
    run_id = "init-260909-000000-00003"
    meta_path = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    meta_path.parent.mkdir(parents=True)
    scopes: list[str | None] = []

    def failing(only_run_id: str | None = None) -> dict[str, object]:
        scopes.append(only_run_id)
        raise ControlPlaneStorageErrorProxy("control-plane degraded: disk full")

    ControlPlaneStorageErrorProxy = control_plane.ControlPlaneStorageError
    monkeypatch.setattr(spawn, "sync_state", failing)
    now = [1000.0]
    receipt: dict[str, object] = {
        "run_id": run_id,
        "status": "active",
        "liveness": "active",
    }
    projection = spawn._InteractiveProjection(
        run_id=run_id, meta_path=meta_path, receipt=receipt, clock=lambda: now[0]
    )
    assert spawn._projection_backoff_seconds(1025) == 30.0
    assert spawn._projection_backoff_seconds(10**100) == 30.0
    assert projection.publish() == "pending"
    assert projection.attempts == 1
    assert projection.pump() == "pending"  # not due yet: no busy retry
    assert projection.attempts == 1
    delays: list[float] = []
    while projection.attempts <= 20:
        delays.append(projection.next_attempt_at - now[0])
        now[0] = projection.next_attempt_at
        projection.pump()
    assert projection.status == "pending"
    assert projection.attempts == 21
    assert delays[:6] == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0]
    assert max(delays) == 30.0
    assert set(scopes) == {run_id}  # exact scope on every attempt
    stamped = json.loads(meta_path.read_text(encoding="utf-8"))["projection"]
    assert stamped["status"] == "pending"
    assert stamped["attempts"] == 21
    assert "disk full" in stamped["last_error"]
    assert not [
        e for e in _events_for(home, run_id) if e["kind"] == "projection:abandoned"
    ]

    # A same-owner retry remains schedulable beyond the former float-overflow
    # boundary, so recovery does not need a new projection object or observer.
    projection.attempts = projection.failures = 1024
    now[0] = projection.next_attempt_at
    assert projection.pump() == "pending"
    assert projection.attempts == projection.failures == 1025
    assert projection.next_attempt_at - now[0] == 30.0

    # Storage heals while the same owner remains live: no fresh projection
    # object, observe call, or manual sync is needed for recovery.
    monkeypatch.setattr(spawn, "sync_state", lambda only_run_id=None: {})
    now[0] = projection.next_attempt_at
    assert projection.pump() == "published"
    now[0] += 3600
    assert projection.pump() == "published"
    assert projection.attempts == 1026
    assert json.loads(meta_path.read_text(encoding="utf-8"))["projection"][
        "published_at"
    ]


def test_terminal_projection_flush_is_bounded_and_interrupt_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibecrafted_core import spawn

    home = Path(os.environ["VIBECRAFTED_HOME"])
    run_id = "init-260909-000000-00004"
    # An ACTIVE success must not remain the terminal meta's projection truth
    # when the later terminal flush exhausts.
    receipt = {
        "run_id": run_id,
        "status": "cancelled",
        "liveness": "terminal",
        "projection": {"phase": "active", "status": "published"},
    }
    meta_path = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    meta_path.parent.mkdir(parents=True)
    meta_path.write_text(json.dumps(receipt), encoding="utf-8")
    now = [50.0]
    slept: list[float] = []

    def sleep(delay: float) -> None:
        slept.append(delay)
        now[0] += delay

    monkeypatch.setattr(
        spawn,
        "sync_state",
        lambda only_run_id=None: (_ for _ in ()).throw(OSError(13, "denied")),
    )
    result = spawn._flush_terminal_projection(
        run_id, receipt, meta_path=meta_path, clock=lambda: now[0], sleep=sleep
    )
    assert result == "abandoned"
    assert sum(slept) <= spawn._PROJECTION_TERMINAL_BUDGET_SECONDS
    assert len(slept) + 1 <= spawn._PROJECTION_TERMINAL_ATTEMPT_LIMIT
    abandoned = [
        e for e in _events_for(home, run_id) if e["kind"] == "projection:abandoned"
    ]
    assert [e["payload"]["phase"] for e in abandoned] == ["terminal"]
    assert abandoned[0]["payload"]["state"] == "cancelled"
    exhausted_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert exhausted_meta["projection"]["phase"] == "terminal"
    assert exhausted_meta["projection"]["status"] == "abandoned"

    # Transient: two failures then success returns published, no abandonment.
    outcomes = iter([OSError(13, "denied"), OSError(13, "denied"), None])

    def flaky(only_run_id: str | None = None) -> dict[str, object]:
        outcome = next(outcomes)
        if outcome is not None:
            raise outcome
        return {}

    monkeypatch.setattr(spawn, "sync_state", flaky)
    slept.clear()
    # Conversely, a failed ACTIVE publication must be replaced by a successful
    # terminal flush, not preserved as stale active-phase metadata.
    terminal_success = {
        "run_id": "init-260909-000000-00005",
        "status": "cancelled",
        "liveness": "terminal",
        "projection": {"phase": "active", "status": "pending"},
    }
    assert (
        spawn._flush_terminal_projection(
            "init-260909-000000-00005",
            terminal_success,
            clock=lambda: now[0],
            sleep=sleep,
        )
        == "published"
    )
    assert slept == [0.5, 1.0]
    assert terminal_success["projection"]["phase"] == "terminal"
    assert terminal_success["projection"]["status"] == "published"

    # Ctrl-C inside the flush abandons it instead of unwinding the owner exit.
    def interrupting(delay: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        spawn,
        "sync_state",
        lambda only_run_id=None: (_ for _ in ()).throw(OSError(13, "denied")),
    )
    assert (
        spawn._flush_terminal_projection(
            "init-260909-000000-00006",
            receipt,
            clock=lambda: now[0],
            sleep=interrupting,
        )
        == "abandoned"
    )
    interrupted = [
        e
        for e in _events_for(home, "init-260909-000000-00006")
        if e["kind"] == "projection:abandoned"
    ]
    assert "KeyboardInterrupt" in interrupted[0]["payload"]["last_error"]
