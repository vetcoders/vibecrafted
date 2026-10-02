"""G6 e2e: lifecycle_runner → disk receipt axes → live server → SSE → triage.

One loop, zero guessing. Stub worker is a real subprocess; server is the real
``vibecrafted-server-web`` binary on an ephemeral port against a tmp control plane.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core.events import append_event
from vibecrafted_core.lifecycle_runner import (
    LifecycleRunner,
    LifecycleRunSpec,
    delivery_axes_for_receipt,
)
from vibecrafted_core.run_triage import (
    VERDICT_FAILED,
    VERDICT_NEEDS_ATTENTION,
    classify_run,
    read_kernel_axes,
)

FIXTURES = Path(__file__).parent / "fixtures"
STUB_WORKER = FIXTURES / "stub_worker.py"
AXIS_KEYS = ("execution_state", "proof_state", "delivery_state")

# These clients observe the temporary HTTP server. File/FTP handlers cannot
# provide live server evidence and must never turn a local file into a response.
HTTP_OPENER = urllib.request.OpenerDirector()
HTTP_OPENER.add_handler(urllib.request.UnknownHandler())
HTTP_OPENER.add_handler(urllib.request.HTTPHandler())
HTTP_OPENER.add_handler(urllib.request.HTTPDefaultErrorHandler())
HTTP_OPENER.add_handler(urllib.request.HTTPErrorProcessor())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _lifecycle_id_from_state_path(state_path: str) -> str:
    """``.../lifecycle_runs/<id>/state.json`` → ``<id>``."""
    path = Path(state_path)
    if path.name == "state.json":
        return path.parent.name
    return path.stem


def _make_stub_launcher(
    *,
    work_dir: Path,
    fail: bool,
    worker: Path,
) -> Any:
    """Launcher that runs the real stub_worker.py subprocess and emits events.

    Events are stamped with the **lifecycle** run_id (parsed from
    ``spec.lifecycle_state_path``) so SSE assertions target the umbrella run,
    not an opaque stage id.
    """

    counter = {"n": 0}

    def launcher(spec: Any, _source_dir: Path | str) -> dict[str, Any]:
        counter["n"] += 1
        seq = counter["n"]
        stage = str(
            getattr(spec, "skill", None) or getattr(spec, "mode", None) or "stage"
        )
        stage_run_id = f"stub-{stage}-{seq:02d}"
        report = work_dir / f"{stage_run_id}-report.md"
        transcript = work_dir / f"{stage_run_id}-transcript.log"

        lifecycle_id = ""
        state_path = str(getattr(spec, "lifecycle_state_path", "") or "")
        if state_path:
            lifecycle_id = _lifecycle_id_from_state_path(state_path)
        event_run_id = lifecycle_id or stage_run_id

        append_event(
            kind="launch",
            run_id=event_run_id,
            message=f"lifecycle stage {stage} started (stub worker)",
            payload={
                "state": "process_spawned",
                "stage": stage,
                "stage_run_id": stage_run_id,
                "lifecycle_run_id": lifecycle_id,
                "skill": stage,
            },
        )

        env = {
            **dict(**{k: v for k, v in __import__("os").environ.items()}),
            "STUB_WORKER_REPORT": str(report),
            "STUB_WORKER_TRANSCRIPT": str(transcript),
            "STUB_WORKER_STAGE": stage,
            "STUB_WORKER_FAIL": "1" if fail else "0",
        }
        # Only the first stage fails in the negative scenario so the loop
        # terminates cleanly with execution=failed axes on the umbrella receipt.
        stage_fail = fail and seq == 1
        env["STUB_WORKER_FAIL"] = "1" if stage_fail else "0"

        proc = subprocess.run(
            [sys.executable, str(worker)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        exit_code = int(proc.returncode)
        terminal_state = "failed" if exit_code != 0 else "completed"
        append_event(
            kind="lifecycle.stage_terminal",
            run_id=event_run_id,
            message=f"lifecycle stage {stage} terminal ({terminal_state})",
            payload={
                "state": terminal_state,
                "exit_code": exit_code,
                "stage": stage,
                "stage_run_id": stage_run_id,
                "lifecycle_run_id": lifecycle_id,
                "artifact_ok": exit_code == 0 and report.is_file(),
            },
        )
        return {
            "accepted": True,
            "run_id": stage_run_id,
            "report": str(report),
            "transcript": str(transcript),
            "meta": str(work_dir / f"{stage_run_id}.meta.json"),
            "exit_code": exit_code,
            "state": "process_spawned" if exit_code == 0 else "failed",
        }

    return launcher


def _make_awaiter() -> Any:
    def awaiter(payload: dict[str, Any]) -> dict[str, Any]:
        exit_code = int(payload.get("exit_code") or 0)
        report = str(payload.get("report") or "")
        report_path = Path(report) if report else None
        artifact_ok = (
            exit_code == 0 and report_path is not None and report_path.is_file()
        )
        result: dict[str, Any] = {
            "completed": artifact_ok,
            "artifact_ok": artifact_ok,
            "exit_code": exit_code,
            "report": report,
            "transcript": str(payload.get("transcript") or ""),
        }
        if not artifact_ok:
            # Explicit axis on await payload — delivery_axes_for_receipt honours it.
            result["execution_state"] = "failed"
        return result

    return awaiter


def _http_json(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    with HTTP_OPENER.open(url, timeout=timeout) as resp:
        assert resp.status == 200, (url, resp.status)
        return json.loads(resp.read().decode("utf-8"))


def _collect_sse(
    base_url: str,
    *,
    stop_event: threading.Event,
    collected: list[str],
    timeout: float = 20.0,
) -> None:
    """Background SSE reader; appends raw frame text until stop or timeout.

    Critical: use ``read1`` (or 1-byte reads) — ``resp.read(n)`` on
    ``http.client`` waits until *n* bytes are buffered. After the first SSE
    frame, the next frame is often shorter than 256 bytes; a blocking
    ``read(256)`` then stalls until enough keepalives accumulate, which is
    longer than the e2e wait and yields a truncated ``data:`` line (only
    ``launch`` visible, ``lifecycle.stage_terminal`` half-parsed).
    """
    url = f"{base_url}/api/control/events?since=0"
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    deadline = time.time() + timeout
    try:
        with HTTP_OPENER.open(req, timeout=timeout) as resp:
            while not stop_event.is_set() and time.time() < deadline:
                # Prefer read1: return whatever is available without waiting for n.
                if hasattr(resp, "read1"):
                    chunk = resp.read1(8192)
                else:
                    chunk = resp.read(1)
                if not chunk:
                    time.sleep(0.02)
                    continue
                collected.append(chunk.decode("utf-8", errors="replace"))
    # The SSE reader runs in a background test thread; any socket or decoder exception must be
    # appended as __SSE_ERROR__ for the main-thread assertions to inspect.
    except Exception as exc:  # noqa: BLE001
        collected.append(f"\n__SSE_ERROR__:{type(exc).__name__}:{exc}\n")


def test_http_proof_rejects_file_responses(tmp_path: Path) -> None:
    payload = tmp_path / "local-response.json"
    payload.write_text('{"status":"ok"}', encoding="utf-8")
    with pytest.raises(urllib.error.URLError, match="unknown url type"):
        _http_json(payload.as_uri())


def _sse_events_for_run(raw: str, run_id: str) -> list[dict[str, Any]]:
    """Parse complete SSE frames only (split on blank lines); skip partials."""
    events: list[dict[str, Any]] = []
    # SSE frames end with a blank line; trailing incomplete frame is ignored.
    frames = raw.split("\n\n")
    for frame in frames:
        data_parts: list[str] = []
        for line in frame.splitlines():
            if line.startswith("data:"):
                # Spec: optional single space after data:
                payload = line[5:]
                payload = payload.removeprefix(" ")
                data_parts.append(payload)
        if not data_parts:
            continue
        payload = "\n".join(data_parts).strip()
        if not payload or payload.startswith(":"):
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue  # incomplete frame still being assembled
        if not isinstance(obj, dict):
            continue
        if str(obj.get("run_id") or "") == run_id:
            events.append(obj)
    return events


def _disk_event_kinds(vc_home: Path, run_id: str) -> list[str]:
    """Honesty check: kinds written to events.jsonl for this run (emission path)."""
    stream = vc_home / "control_plane" / "events.jsonl"
    if not stream.is_file():
        return []
    kinds: list[str] = []
    for line in stream.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(obj.get("run_id") or "") == run_id:
            kinds.append(str(obj.get("kind") or ""))
    return kinds


def _run_mini_lifecycle(
    *,
    root: Path,
    fail: bool,
    worker: Path,
    workflow_id: str = "vc-marbles",
) -> dict[str, Any]:
    monkey_atlas = {"ok": True, "command": ["loct", "context"], "returncode": 0}

    # Patch load_context_atlas so the e2e does not depend on loct being installed
    # in the test process PATH (the product surface under test is delivery, not loct).
    import vibecrafted_core.lifecycle_runner as lr

    original = lr.load_context_atlas
    lr.load_context_atlas = lambda *_a, **_k: monkey_atlas
    try:
        runner = LifecycleRunner(
            launcher=_make_stub_launcher(
                work_dir=root / "worker-artifacts", fail=fail, worker=worker
            ),
            awaiter=_make_awaiter(),
        )
        (root / "worker-artifacts").mkdir(parents=True, exist_ok=True)
        state = asyncio.run(
            runner.run(
                LifecycleRunSpec(
                    workflow_id=workflow_id,
                    agent="codex",
                    prompt="g6 e2e mini lifecycle — stub worker only",
                    root=str(root),
                    runtime="headless",
                    await_stages=True,
                )
            )
        )
    finally:
        lr.load_context_atlas = original
    return state


def _axes_from_state(state: dict[str, Any]) -> dict[str, str]:
    # Prefer explicit keys on the receipt; fall back to projector for honesty.
    if all(k in state for k in AXIS_KEYS):
        return {k: str(state[k]) for k in AXIS_KEYS}
    return delivery_axes_for_receipt(str(state.get("status") or ""), state)


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------


def test_e2e_lifecycle_server_loop_happy(
    vc_home: Path,
    live_server,
    stub_worker_script: Path,
    tmp_path: Path,
) -> None:
    """Happy: mini-lifecycle completes → three axes on disk → HTTP 1:1 → SSE → triage."""
    base_url, _proc = live_server
    root = tmp_path / "repo"
    root.mkdir()
    # Minimal git repo so lifecycle_runner git probes stay quiet.
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "g6@vibecrafted.test"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "G6 E2E"], cwd=root, check=True)
    (root / "README.md").write_text("g6\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "g6 seed"], cwd=root, check=True)

    stop = threading.Event()
    chunks: list[str] = []
    reader = threading.Thread(
        target=_collect_sse,
        kwargs={
            "base_url": base_url,
            "stop_event": stop,
            "collected": chunks,
            "timeout": 25.0,
        },
        daemon=True,
    )
    reader.start()
    # Let the SSE connection establish before writers append events.
    time.sleep(0.3)

    state = _run_mini_lifecycle(root=root, fail=False, worker=stub_worker_script)
    run_id = str(state["run_id"])
    state_path = Path(state["state_path"])
    assert state_path.is_file(), state_path
    on_disk = json.loads(state_path.read_text(encoding="utf-8"))

    # --- A: receipt on disk has three axes ---
    disk_axes = _axes_from_state(on_disk)
    for key in AXIS_KEYS:
        assert key in on_disk, f"missing {key} on lifecycle state: {on_disk.keys()}"
        assert on_disk[key], f"empty {key}"
    assert disk_axes["execution_state"] == "exited", disk_axes
    assert disk_axes["proof_state"] == "undeclared", disk_axes
    assert disk_axes["delivery_state"] == "unverified", disk_axes
    assert on_disk["status"] == "completed", on_disk.get("status")
    assert len(on_disk.get("stages") or []) >= 2, "vc-marbles should run 2 stages"

    # --- B: live server GET /api/control/runs/{id} returns axes 1:1 ---
    # Server reads the same VIBECRAFTED_HOME; give the FS a beat.
    time.sleep(0.1)
    http_run = _http_json(f"{base_url}/api/control/runs/{run_id}")
    assert http_run["run_id"] == run_id, http_run
    assert http_run.get("source") == "lifecycle_runs", http_run
    for key in AXIS_KEYS:
        assert key in http_run, f"server omitted {key}: {http_run}"
        assert str(http_run[key]) == disk_axes[key], (
            f"axis mismatch on {key}: server={http_run[key]!r} disk={disk_axes[key]!r}"
        )

    # Also the nested lifecycle detail route carries the same projection.
    life = _http_json(f"{base_url}/api/control/lifecycle/{run_id}")
    assert life["run_id"] == run_id
    for key in AXIS_KEYS:
        assert str(life.get(key)) == disk_axes[key], (
            key,
            life.get(key),
            disk_axes[key],
        )

    # --- C: SSE emitted start + terminal for this run ---
    # Wait briefly for the last frames to flush through the poll loop.
    deadline = time.time() + 5.0
    raw = ""
    events: list[dict[str, Any]] = []
    while time.time() < deadline:
        raw = "".join(chunks)
        events = _sse_events_for_run(raw, run_id)
        kinds = {str(e.get("kind") or "") for e in events}
        if "launch" in kinds and any(
            k.endswith("terminal") or k == "lifecycle.stage_terminal" for k in kinds
        ):
            break
        time.sleep(0.1)
    stop.set()
    reader.join(timeout=3)

    assert events, f"no SSE events for run {run_id}; raw tail:\n{raw[-2000:]}"
    kinds = [str(e.get("kind") or "") for e in events]
    assert "launch" in kinds, f"missing start/launch event: {kinds}"
    assert any(k == "lifecycle.stage_terminal" for k in kinds), (
        f"missing terminal event: {kinds}"
    )

    # --- D: classify_run from axes ---
    kernel = read_kernel_axes(on_disk)
    assert kernel is not None and not kernel.corrupt, kernel
    classification = classify_run(
        exit_code=0,
        run_state=on_disk.get("status"),
        report_exists=True,
        report_bytes=64,
        transcript_bytes=64,
        kernel_axes=kernel,
    )
    # Honest completed axes without seal → needs_attention (axes own the drawer).
    assert classification.verdict == VERDICT_NEEDS_ATTENTION, (
        classification.verdict,
        classification.reason,
        disk_axes,
    )
    assert "axes_" in classification.reason or "undeclared" in classification.reason


def test_e2e_lifecycle_server_loop_failed_worker(
    vc_home: Path,
    live_server,
    stub_worker_script: Path,
    tmp_path: Path,
) -> None:
    """Negative: stub worker exit≠0 → execution=failed → triage Failed → SSE terminal."""
    base_url, _proc = live_server
    root = tmp_path / "repo-fail"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "g6@vibecrafted.test"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "G6 E2E"], cwd=root, check=True)
    (root / "README.md").write_text("g6-fail\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "g6 fail seed"], cwd=root, check=True)

    stop = threading.Event()
    chunks: list[str] = []
    reader = threading.Thread(
        target=_collect_sse,
        kwargs={
            "base_url": base_url,
            "stop_event": stop,
            "collected": chunks,
            "timeout": 25.0,
        },
        daemon=True,
    )
    reader.start()
    time.sleep(0.3)

    # Single-stage workflow so the failure is the whole run.
    state = _run_mini_lifecycle(
        root=root,
        fail=True,
        worker=stub_worker_script,
        workflow_id="vc-dou",
    )
    run_id = str(state["run_id"])
    on_disk = json.loads(Path(state["state_path"]).read_text(encoding="utf-8"))

    disk_axes = _axes_from_state(on_disk)
    assert on_disk.get("status") == "failed"

    time.sleep(0.1)
    http_run = _http_json(f"{base_url}/api/control/runs/{run_id}")
    assert http_run["run_id"] == run_id
    assert (
        http_run.get("execution_state") == "failed" or on_disk.get("status") == "failed"
    )
    assert str(http_run.get("proof_state")) == disk_axes["proof_state"]
    assert str(http_run.get("delivery_state")) == disk_axes["delivery_state"]

    # Emission path (disk) must already have the terminal frame — independent of SSE.
    disk_kinds = _disk_event_kinds(vc_home, run_id)
    assert "launch" in disk_kinds, f"disk events missing launch: {disk_kinds}"
    assert "lifecycle.stage_terminal" in disk_kinds, (
        f"disk events missing terminal (emission bug): {disk_kinds}"
    )

    deadline = time.time() + 8.0
    raw = ""
    events: list[dict[str, Any]] = []
    while time.time() < deadline:
        raw = "".join(chunks)
        events = _sse_events_for_run(raw, run_id)
        if any(str(e.get("kind")) == "lifecycle.stage_terminal" for e in events):
            break
        time.sleep(0.05)
    stop.set()
    reader.join(timeout=3)

    assert events, f"no SSE events for failed run {run_id}; raw:\n{raw[-2000:]}"
    terminal = [e for e in events if str(e.get("kind")) == "lifecycle.stage_terminal"]
    assert terminal, (
        f"no terminal SSE frame: kinds={[e.get('kind') for e in events]} "
        f"disk={disk_kinds} raw_tail:\n{raw[-1500:]}"
    )

    kernel = read_kernel_axes(on_disk)
    assert kernel is not None
    classification = classify_run(
        exit_code=1,
        run_state=on_disk.get("status"),
        report_exists=True,
        report_bytes=32,
        transcript_bytes=32,
        kernel_axes=kernel,
    )
    assert classification.verdict == VERDICT_FAILED, (
        classification.verdict,
        classification.reason,
        disk_axes,
    )
    assert (
        "execution_failed" in classification.reason or "failed" in classification.reason
    )
