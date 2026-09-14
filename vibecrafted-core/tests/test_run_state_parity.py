from __future__ import annotations

import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import control_plane

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "vibecrafted-core"
SCRIPT_DIR = CORE_ROOT / "vibecrafted_core" / "runtime" / "scripts"
SESSION_PLACEHOLDERS = {"", "pending", "none", "null", "unknown"}


def _child_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["VIBECRAFTED_HOME"] = str(home)
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VIBECRAFTED_CORE_PYTHONPATH"] = str(CORE_ROOT)
    env["PYTHONPATH"] = (
        str(CORE_ROOT)
        if not env.get("PYTHONPATH")
        else str(CORE_ROOT) + os.pathsep + env["PYTHONPATH"]
    )
    return env


def _snapshot_path(home: Path, run_id: str) -> Path:
    return home / "control_plane" / "runs" / f"{run_id}.json"


def _read_projection(home: Path, run_id: str) -> dict[str, Any] | None:
    control_plane.sync_state()
    path = _snapshot_path(home, run_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _wait_for_active_projection(
    home: Path,
    run_id: str,
    label: str,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _read_projection(home, run_id)
        if last:
            state = str(last.get("state", ""))
            session_id = str(last.get("session_id", ""))
            if state == "active" and session_id.lower() not in SESSION_PLACEHOLDERS:
                return last
        time.sleep(0.05)

    if last is None:
        raise AssertionError(
            f"{label} state drift: expected runs/{run_id}.json to reach "
            "state=active with non-empty session_id; no projection was written"
        )

    raise AssertionError(
        f"{label} state drift: expected runs/{run_id}.json to reach "
        "state=active with non-empty session_id; "
        f"last projection state={last.get('state')!r}, "
        f"liveness={last.get('liveness')!r}, "
        f"session_id={last.get('session_id')!r}, "
        f"operator_state={last.get('operator_state')!r}, "
        f"path={_snapshot_path(home, run_id)}"
    )


def _wait_process(proc: subprocess.Popen[str], *, timeout: float = 5.0) -> None:
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1.0)


def _write_async_worker(tmp_path: Path) -> Path:
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import pathlib\n"
        "import sys\n"
        "import time\n"
        "print('dispatcher-active', flush=True)\n"
        "while not pathlib.Path(sys.argv[1]).exists():\n"
        "    time.sleep(0.01)\n",
        encoding="utf-8",
    )
    return worker


def _append_lifecycle_event(home: Path, payload: dict[str, Any]) -> None:
    events = home / "control_plane" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    # A fresh timestamp is load-bearing: with a hardcoded past ts, terminal-run
    # cases become time bombs — once the event ages past the snapshot retention
    # horizon, sync_state archives the projection in the same pass the test
    # reads it (went red by itself on 2026-07-06 with ts=2026-06-29).
    event = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "run_id": payload["run_id"],
        "kind": f"lifecycle:{payload['state']}",
        "message": f"test {payload['state']}",
        "payload": payload,
    }
    with events.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def _launch_python_dispatcher_path(
    tmp_path: Path,
    home: Path,
    run_id: str,
) -> dict[str, Any]:
    """Path A — the async dispatcher launch entry (`dispatcher run`).

    Live lifecycle events advance the unified projection. Returns the captured
    ACTIVE projection so callers can assert liveness and cross-path shape.
    """

    worker = _write_async_worker(tmp_path)
    release_worker = tmp_path / f"{run_id}.release-worker"
    transcript = tmp_path / f"{run_id}.dispatcher.transcript.log"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "vibecrafted_core.dispatcher",
            "run",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--transcript",
            str(transcript),
            "--no-require-report",
            "--require-transcript-output",
            "--quiet",
            "--",
            sys.executable,
            str(worker),
            str(release_worker),
        ],
        cwd=tmp_path,
        env=_child_env(home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        projection = _wait_for_active_projection(home, run_id, "python-dispatcher path")
    finally:
        release_worker.touch()
        _wait_process(proc)

    stdout, stderr = proc.communicate()
    assert proc.returncode == 0, (
        "dispatcher control path should finish cleanly; "
        f"stdout={stdout!r}, stderr={stderr!r}"
    )
    return projection


def _launch_shell_meta_path(
    tmp_path: Path,
    home: Path,
    run_id: str,
) -> dict[str, Any]:
    """Path B — the legacy shell `spawn_write_meta` launch frontend.

    Drives the shell-owned writer directly (no terminal/LLM). Since W1-03 it
    delegates to the unified control-plane writer, so the worker being alive
    must project `state=active` with a real session identity instead of being
    stuck at launching/pid_pending. Returns the captured ACTIVE projection.
    """

    prompt = tmp_path / f"{run_id}.prompt.md"
    prompt.write_text("test prompt\n", encoding="utf-8")
    report = tmp_path / "reports" / f"{run_id}.md"
    transcript = tmp_path / f"{run_id}.shell.transcript.log"
    release_worker = tmp_path / f"{run_id}.release-worker"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    meta = (
        home
        / "artifacts"
        / "Vetcoders"
        / "vibecrafted"
        / "2026_0629"
        / "reports"
        / f"{run_id}.meta.json"
    )

    with transcript.open("w", encoding="utf-8") as transcript_fh:
        worker = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import pathlib, sys, time\n"
                    "print('shell worker active', flush=True)\n"
                    "release = pathlib.Path(sys.argv[1])\n"
                    "while not release.exists():\n"
                    "    time.sleep(0.01)\n"
                ),
                str(release_worker),
            ],
            stdout=transcript_fh,
            stderr=subprocess.STDOUT,
            text=True,
        )
        shell = "\n".join(
            [
                f"source {shlex.quote(str(SCRIPT_DIR / 'common.sh'))}",
                f"export SPAWN_RUN_ID={shlex.quote(run_id)}",
                "export SPAWN_PROMPT_ID=prompt-red",
                "export SPAWN_LOOP_NR=0",
                "export SPAWN_SKILL_CODE=impl",
                (
                    "spawn_write_meta "
                    f"{shlex.quote(str(meta))} "
                    "launching codex implement "
                    f"{shlex.quote(str(tmp_path))} "
                    f"{shlex.quote(str(prompt))} "
                    f"{shlex.quote(str(report))} "
                    f"{shlex.quote(str(transcript))} "
                    f"{shlex.quote(str(tmp_path / 'legacy-launcher.sh'))} "
                    "codex-cli-default"
                ),
            ]
        )
        try:
            subprocess.run(
                ["bash", "-lc", shell],
                cwd=REPO_ROOT,
                env=_child_env(home),
                check=True,
                capture_output=True,
                text=True,
            )
            projection = _wait_for_active_projection(home, run_id, "shell/legacy path")
        finally:
            release_worker.touch()
            _wait_process(worker)
    return projection


def test_python_dispatcher_projection_reaches_active_with_session_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Async dispatcher is the control case: live events advance the projection."""

    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    projection = _launch_python_dispatcher_path(tmp_path, home, "parity-async")
    assert projection["state"] == "active"
    assert projection["session_id"].lower() not in SESSION_PLACEHOLDERS


def test_shell_meta_projection_reaches_active_with_session_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Legacy shell path must match async liveness.

    This drives the shell-owned `spawn_write_meta` writer directly instead of
    launching a terminal or LLM. The RED bug is that the worker can be alive
    while `control_plane/runs/<id>.json` remains launching/pid_pending with no
    session identity because the shell path emits no live lifecycle events.
    """

    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    projection = _launch_shell_meta_path(tmp_path, home, "parity-shell")
    assert projection["state"] == "active"
    assert projection["session_id"].lower() not in SESSION_PLACEHOLDERS


@pytest.mark.parametrize("session_id", ["session-live", ""])
def test_active_projection_without_report_is_not_blocked_mid_delivery(
    tmp_path: Path,
    monkeypatch,
    session_id: str,
) -> None:
    run_id = "active-report-pending"
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    transcript = tmp_path / "active.log"
    transcript.write_text("still working\n", encoding="utf-8")
    report = tmp_path / "active-report.md"

    _append_lifecycle_event(
        home,
        {
            "run_id": run_id,
            "state": "active",
            "agent": "codex",
            "skill": "implement",
            "mode": "terminal",
            "root": str(tmp_path),
            "report": str(report),
            "transcript": str(transcript),
            "session_id": session_id,
            "identity_required": True,
            "liveness": "pid_alive",
        },
    )

    projection = _read_projection(home, run_id)
    assert projection is not None
    assert projection["state"] == "active"
    assert projection["operator_state"] == "running", (
        "active run drift: a healthy ACTIVE projection must not be blocked "
        "or report_missing before the run has reached a terminal state"
    )
    assert projection["artifact_gate"] == "pending"
    assert "report_missing" not in projection["artifact_errors"]


def test_report_validated_projection_clears_recovery_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_id = "validated-clears-recovery"
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    report = tmp_path / "validated.md"
    report.write_text("# Report\n\nok\n", encoding="utf-8")

    _append_lifecycle_event(
        home,
        {
            "run_id": run_id,
            "state": "report_validated",
            "agent": "codex",
            "skill": "implement",
            "mode": "terminal",
            "root": str(tmp_path),
            "report": str(report),
            "session_id": "session-done",
            "identity_required": True,
            "liveness": "terminal",
            "recovery_required": True,
        },
    )

    projection = _read_projection(home, run_id)
    assert projection is not None
    assert projection["state"] == "report_validated"
    assert projection["operator_state"] == "completed"
    assert projection["exit_code"] == 0, (
        "report_validated drift: validated terminal reports should project a "
        "successful exit_code instead of leaving exit_code=null"
    )
    assert projection.get("recovery_required") is not True
    assert projection["lifecycle"]["recovery_required"] is False, (
        "report_validated drift: validated terminal reports must clear stale "
        "recovery_required instead of projecting a recovery lane"
    )


# Keys that drive operator/liveness decisions. Both launch paths MUST project
# these (the W1-03 split-brain was exactly a path that never reached active /
# never grew a session identity). Asserting the contract floor — rather than
# raw set equality — keeps the gate robust against incidental, non-decision
# fields that one writer may legitimately carry.
PARITY_CONTRACT_KEYS = frozenset(
    {
        "run_id",
        "state",
        "session_id",
        "liveness",
        "operator_state",
        "artifact_gate",
        "lifecycle",
        "health",
        "workspace_id",
        "vibecrafted_session_id",
        "workspace_instance_id",
        "build_id",
        "workspace_display_label",
        "worker_host_session",
        "worker_host_display",
    }
)
LIVE_LIVENESS_PLACEHOLDERS = {"", "pid_gone", "terminal", "lock_present"}

# Keys that are legitimately local to ONE writer and carry no operator/liveness
# decision. The python dispatcher tracks the worker process directly; the legacy
# shell frontend echoes its meta source and runtime tag. The identity-qualified
# worker receipt is intentionally emitted only by the Python owner that launched
# and can later signal that process. Schema parity tolerates exactly these —
# anything else in the symmetric difference is real drift.
PARITY_PATH_LOCAL_KEYS = frozenset(
    {
        "worker_identity",
        "worker_pid",
        "worker_pgid",
        "meta",
        "runtime",
    }
)
# Operator-facing values that must be identical across both launch paths once
# both reach the same live state (not just present — equal).
PARITY_VALUE_KEYS = ("state", "operator_state", "artifact_gate")


def test_execution_path_state_parity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """W1-05 positive gate: both launch paths project equivalent live state.

    Path A = the python dispatcher launch entry (`dispatcher run`, live
    lifecycle events). Path B = the legacy shell `spawn_write_meta` frontend.
    After W1-03 unified the writer, both must project the same
    `control_plane/runs/<id>.json` schema AND both must reach live `active`
    state with a real session identity. This is the gate that prevents the
    split-brain (one path active, the other stuck launching/pid_pending) from
    silently returning.
    """

    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))

    projection_a = _launch_python_dispatcher_path(tmp_path, home, "parity-path-a")
    projection_b = _launch_shell_meta_path(tmp_path, home, "parity-path-b")

    # --- Liveness parity: both reach active with a real, alive session. -------
    for label, projection in (
        ("python-dispatcher", projection_a),
        ("shell-meta", projection_b),
    ):
        assert projection["state"] == "active", (
            f"{label} path did not reach live state: state={projection.get('state')!r}"
        )
        session_id = str(projection.get("session_id", ""))
        assert session_id.lower() not in SESSION_PLACEHOLDERS, (
            f"{label} path reached active without a real session identity: "
            f"session_id={session_id!r}"
        )
        liveness = str(projection.get("liveness", ""))
        assert liveness not in LIVE_LIVENESS_PLACEHOLDERS, (
            f"{label} path projects a non-live liveness while its worker is "
            f"alive: liveness={liveness!r}"
        )

    # --- Shape parity: equivalent projection schema across both writers. ------
    # The two paths may carry writer-local bookkeeping (worker pid/pgid vs meta/
    # runtime tag), but every operator/liveness DECISION key must exist in both
    # and the only permitted divergence is the documented path-local allowlist.
    keys_a = set(projection_a)
    keys_b = set(projection_b)
    for label, keys in (("python-dispatcher", keys_a), ("shell-meta", keys_b)):
        missing = PARITY_CONTRACT_KEYS - keys
        assert not missing, (
            f"{label} projection is missing operator/liveness contract keys: "
            f"{sorted(missing)}"
        )

    unexpected = (keys_a ^ keys_b) - PARITY_PATH_LOCAL_KEYS
    assert not unexpected, (
        "execution-path schema drift: runs/<id>.json keys diverge between the "
        "python-dispatcher and shell-meta launch paths outside the documented "
        f"path-local allowlist; unexpected={sorted(unexpected)} "
        f"(only in python-dispatcher={sorted(keys_a - keys_b)}, "
        f"only in shell-meta={sorted(keys_b - keys_a)})"
    )

    # --- Value parity: operator-facing state must agree, not just co-exist. ---
    for key in PARITY_VALUE_KEYS:
        assert projection_a.get(key) == projection_b.get(key), (
            f"execution-path value drift on {key!r}: "
            f"python-dispatcher={projection_a.get(key)!r} != "
            f"shell-meta={projection_b.get(key)!r}"
        )
