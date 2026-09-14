"""Contract for the one caretaker truth.

Each test here guards a property whose loss silently restores the multi-source
fusion this envelope replaced: a verdict that disagrees with its own header, a
stale receipt read as a running server, a corrupt plane rendered as healthy, or
a second resume classifier drifting away from the one in ``init_resume``.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from vibecrafted_core import caretaker


def _plane(tmp_path: Path) -> Path:
    """A minimal but structurally real control plane."""
    plane = tmp_path / "control_plane"
    for child in ("runs", "runtime_runs", "lifecycle_runs"):
        (plane / child).mkdir(parents=True)
    (plane / "events.jsonl").write_text("", encoding="utf-8")
    return plane


def _receipt(tmp_path: Path, **overrides: object) -> Path:
    """Write a supervisor receipt under a fake crafted home; return that home."""
    home = tmp_path / "crafted"
    (home / "server").mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "vibecrafted.server-supervisor.v1",
        "state": "healthy",
        "supervisor_pid": 4242,
        "service_managed": True,
        "endpoint": {
            "host": "127.0.0.1",
            "port": 3024,
            "url": "http://127.0.0.1:3024",
            "public_url": "http://127.0.0.1:3024",
        },
        "managed_pair": {"guardian_pid": 11, "server_pid": 12},
        "last_error": None,
        "consecutive_failures": 0,
    }
    payload.update(overrides)
    (home / "server" / "supervisor.status.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return home


def _reachable(
    monkeypatch: pytest.MonkeyPatch, *, reachable: bool, reason: str = ""
) -> None:
    """Pin the liveness probe for tests about what the verdict does with it.

    Tests about the probe itself run against real loopback sockets instead
    (``health_endpoint``), because a pinned probe cannot prove a retry.
    """
    monkeypatch.setattr(
        caretaker,
        "probe_health",
        lambda origin, **_: {
            "origin": origin,
            "reachable": reachable,
            "reason": reason,
            "version": "4.3.0" if reachable else "",
        },
    )


#: Cadence at which the macOS App re-runs ``server caretaker --json``
#: (``AppDelegate.swift``). A probe that outlives it stacks polls.
APP_POLL_SECONDS = 5.0

#: Per-attempt bound where a test needs several silent attempts quickly. The
#: shipped constants are exercised unshrunk by the probe-level tests.
SHORT_PROBE_SECONDS = 0.3


class _HealthEndpoint:
    """A real loopback listener standing in for ``/api/health``.

    The first ``silent`` connections are accepted and then left without a single
    byte of answer — the shape of a server whose sockets stay open while its
    event loop is stalled. ``silent=None`` never answers anyone. Every later
    connection gets a genuine HTTP 200 health body.
    """

    def __init__(self, *, silent: int | None) -> None:
        self._silent = silent
        self._held: list[socket.socket] = []
        self._lock = threading.Lock()
        self._connections = 0
        self._stop = threading.Event()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self._listener.settimeout(0.05)
        self.port = int(self._listener.getsockname()[1])
        self.origin = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def connections(self) -> int:
        with self._lock:
            return self._connections

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._lock:
                self._connections += 1
                seen = self._connections
            if self._silent is None or seen <= self._silent:
                self._held.append(conn)
                continue
            self._answer(conn)

    @staticmethod
    def _answer(conn: socket.socket) -> None:
        body = json.dumps(
            {"status": "ok", "version": "9.9.9", "schema": "vibecrafted.health.v1"}
        ).encode()
        with conn:
            conn.settimeout(2.0)
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                request += chunk
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        for conn in self._held:
            conn.close()
        self._listener.close()


@pytest.fixture
def health_endpoint() -> Iterator[Callable[..., _HealthEndpoint]]:
    """Open real loopback health endpoints; close every one after the test."""
    opened: list[_HealthEndpoint] = []

    def _open(*, silent: int | None) -> _HealthEndpoint:
        endpoint = _HealthEndpoint(silent=silent)
        opened.append(endpoint)
        return endpoint

    yield _open
    for endpoint in opened:
        endpoint.close()


def _receipt_endpoint(port: int) -> dict[str, object]:
    url = f"http://127.0.0.1:{port}"
    return {"host": "127.0.0.1", "port": port, "url": url, "public_url": url}


def _declare_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, port: int
) -> None:
    """Declare the server endpoint through the operator config the caretaker reads."""
    config_home = tmp_path / "xdg-config"
    (config_home / "vibecrafted").mkdir(parents=True)
    (config_home / "vibecrafted" / "config.toml").write_text(
        f'[server]\nbind_host = "127.0.0.1"\nport = {port}\n', encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))


def test_verdict_header_and_health_never_disagree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A demoted verdict must demote its header too.

    The first draft of this module reported ``health: degraded`` under a header
    that still read ``HEALTHY`` — the exact two-truths defect the envelope
    exists to remove, reproduced inside the fix. A menu renders the header; a
    script branches on the health. They must never say different things.
    """
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)
    _reachable(monkeypatch, reachable=True)
    # One corrupt snapshot is enough to make upkeep non-silent.
    (plane / "runs" / "broken.json").write_text("{not json", encoding="utf-8")

    snapshot = caretaker.build_caretaker_snapshot(home=home, control_plane=plane)
    verdict = snapshot["verdict"]

    assert verdict["health"] != caretaker.HEALTHY
    assert "HEALTHY ·" in verdict["header"], verdict["header"]
    assert "upkeep item" in verdict["header"]
    # The server leg itself is fine; only the plane is not. Both stay legible.
    assert verdict["server_health"] == caretaker.HEALTHY


def test_serving_endpoint_with_stale_receipt_is_not_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receipt nobody refreshes is not evidence of a supervised server.

    The tray previously read ``supervisor.status.json`` with no age check, so a
    supervisor that died left a file still saying ``healthy`` next to a
    live-looking endpoint. Freshness is part of the truth or it is not truth.
    """
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)
    _reachable(monkeypatch, reachable=True)

    receipt = home / "server" / "supervisor.status.json"
    stale = os.stat(receipt).st_mtime - (caretaker.RECEIPT_STALE_SECONDS + 60)
    os.utime(receipt, (stale, stale))

    snapshot = caretaker.build_caretaker_snapshot(home=home, control_plane=plane)

    assert snapshot["server"]["receipt"]["stale"] is True
    assert snapshot["verdict"]["health"] == caretaker.DEGRADED
    assert "RECEIPT STALE" in snapshot["verdict"]["header"]
    codes = {finding["code"] for finding in snapshot["verdict"]["findings"]}
    assert "supervisor_receipt_stale" in codes


@pytest.mark.parametrize("receipt_fresh", [True, False], ids=["fresh", "stale"])
def test_unreachable_endpoint_is_never_rendered_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, receipt_fresh: bool
) -> None:
    """No receipt state may lift a silent port to HEALTHY.

    A fresh ``healthy`` receipt may soften a missed probe to DEGRADED — the
    supervisor proved the pair on its own cadence — and a stale one leaves the
    port's silence as UNREACHABLE. In neither case may the verdict or its header
    claim health the endpoint did not answer for.
    """
    plane = _plane(tmp_path)
    home = _receipt(tmp_path, state="healthy")
    if not receipt_fresh:
        receipt = home / "server" / "supervisor.status.json"
        stale = os.stat(receipt).st_mtime - (caretaker.RECEIPT_STALE_SECONDS + 60)
        os.utime(receipt, (stale, stale))
    _reachable(
        monkeypatch, reachable=False, reason="ConnectionRefusedError: [Errno 61]"
    )

    verdict = caretaker.build_caretaker_snapshot(home=home, control_plane=plane)[
        "verdict"
    ]

    assert verdict["health"] != caretaker.HEALTHY
    assert verdict["server_health"] != caretaker.HEALTHY
    assert "HEALTHY" not in verdict["header"]
    codes = {f["code"] for f in verdict["findings"]}
    if receipt_fresh:
        assert verdict["health"] == caretaker.DEGRADED
        assert "health_probe_missed" in codes
    else:
        assert verdict["health"] == caretaker.UNAVAILABLE
        assert "UNREACHABLE" in verdict["header"]
        assert "server_unreachable" in codes


def test_probe_answers_past_a_silent_first_attempt(
    health_endpoint: Callable[..., _HealthEndpoint],
) -> None:
    """One stalled GET must not decide liveness when the next one answers.

    On the Founder host the tray read ``UNREACHABLE · TimeoutError: timed out``
    while supervisor, server and guardian PIDs were alive, and ``/api/health``
    answered in under a millisecond minutes later: one probe turned one stalled
    read into a down verdict. This runs the shipped constants against a real
    socket that swallows the first request.
    """
    endpoint = health_endpoint(silent=1)

    started = time.monotonic()
    result = caretaker.probe_health(endpoint.origin)
    elapsed = time.monotonic() - started

    assert result["reachable"] is True, result
    assert result["version"] == "9.9.9"
    assert endpoint.connections == 2
    assert result["attempts"] == 2
    assert elapsed < caretaker.HEALTH_PROBE_BUDGET_SECONDS


def test_silent_endpoint_probe_is_bounded_inside_the_app_poll(
    health_endpoint: Callable[..., _HealthEndpoint],
) -> None:
    """Retries are bounded: a silent endpoint costs one budget, not a stacked poll."""
    endpoint = health_endpoint(silent=None)

    started = time.monotonic()
    result = caretaker.probe_health(endpoint.origin)
    elapsed = time.monotonic() - started

    assert result["reachable"] is False
    assert endpoint.connections > 1, "a single silent GET must not be the verdict"
    assert endpoint.connections == caretaker.HEALTH_PROBE_ATTEMPTS
    assert result["attempts"] == caretaker.HEALTH_PROBE_ATTEMPTS
    assert "timed out" in result["reason"]
    assert f"after {caretaker.HEALTH_PROBE_ATTEMPTS} attempts" in result["reason"]
    assert elapsed <= caretaker.HEALTH_PROBE_BUDGET_SECONDS + 0.5
    assert elapsed < APP_POLL_SECONDS


def test_silent_probe_under_fresh_healthy_receipt_is_degraded_not_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    health_endpoint: Callable[..., _HealthEndpoint],
) -> None:
    """A fresh ``healthy`` supervisor receipt outvotes a missed probe.

    The supervisor proves the managed pair itself and rewrites its receipt every
    pass. While that receipt is fresh (``RECEIPT_STALE_SECONDS``) and says
    ``healthy`` with no counted failures, a probe miss is this reader's problem,
    not the server's death: DEGRADED with a WARN finding, and Start — a verb for
    a server that is not running — stays disabled.
    """
    endpoint = health_endpoint(silent=None)
    plane = _plane(tmp_path)
    home = _receipt(tmp_path, endpoint=_receipt_endpoint(endpoint.port))
    _declare_endpoint(monkeypatch, tmp_path, endpoint.port)
    monkeypatch.setattr(caretaker, "HEALTH_PROBE_TIMEOUT_SECONDS", SHORT_PROBE_SECONDS)

    snapshot = caretaker.build_caretaker_snapshot(home=home, control_plane=plane)

    liveness = snapshot["server"]["liveness"]
    assert liveness["probed"] is True
    assert liveness["reachable"] is False

    verdict = snapshot["verdict"]
    assert verdict["health"] == caretaker.DEGRADED, verdict
    assert verdict["server_health"] == caretaker.DEGRADED
    assert verdict["server_state"] == "running"
    assert "UNREACHABLE" not in verdict["header"]
    by_code = {finding["code"]: finding for finding in verdict["findings"]}
    assert "server_unreachable" not in by_code
    missed = by_code["health_probe_missed"]
    assert missed["severity"] == caretaker.WARN
    assert "supervisor reports healthy" in missed["detail"]

    actions = snapshot["actions"]
    assert actions["start"]["enabled"] is False
    assert "supervisor" in actions["start"]["reason"]
    assert actions["restart"]["enabled"] is True
    assert endpoint.connections > 1, "the verdict must follow retried attempts"


@pytest.mark.parametrize("receipt_case", ["stale", "backoff"])
def test_silent_probe_without_fresh_healthy_receipt_stays_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    health_endpoint: Callable[..., _HealthEndpoint],
    receipt_case: str,
) -> None:
    """Hysteresis needs fresh healthy evidence; anything less keeps the port's word.

    A receipt nobody refreshed may be the last words of a dead supervisor, and a
    ``backoff`` receipt is the supervisor itself saying the pair is not up.
    Neither may soften a silent endpoint: UNREACHABLE with an ERROR finding, and
    Start stays offered.
    """
    endpoint = health_endpoint(silent=None)
    plane = _plane(tmp_path)
    if receipt_case == "stale":
        home = _receipt(tmp_path, endpoint=_receipt_endpoint(endpoint.port))
        receipt = home / "server" / "supervisor.status.json"
        stale = os.stat(receipt).st_mtime - (caretaker.RECEIPT_STALE_SECONDS + 60)
        os.utime(receipt, (stale, stale))
    else:
        home = _receipt(
            tmp_path,
            endpoint=_receipt_endpoint(endpoint.port),
            state="backoff",
            consecutive_failures=2,
            last_error="server start exited 1",
        )
    _declare_endpoint(monkeypatch, tmp_path, endpoint.port)
    monkeypatch.setattr(caretaker, "HEALTH_PROBE_TIMEOUT_SECONDS", SHORT_PROBE_SECONDS)

    snapshot = caretaker.build_caretaker_snapshot(home=home, control_plane=plane)

    assert snapshot["server"]["liveness"]["reachable"] is False
    verdict = snapshot["verdict"]
    assert verdict["health"] == caretaker.UNAVAILABLE, verdict
    assert verdict["server_state"] == "down"
    assert "UNREACHABLE" in verdict["header"]
    by_code = {finding["code"]: finding for finding in verdict["findings"]}
    assert by_code["server_unreachable"]["severity"] == caretaker.ERROR
    assert "health_probe_missed" not in by_code
    assert snapshot["actions"]["start"]["enabled"] is True


def test_unprobed_liveness_is_unknown_not_healthy(tmp_path: Path) -> None:
    """A snapshot built without probing must say ``unknown``, never guess up."""
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)

    snapshot = caretaker.build_caretaker_snapshot(
        home=home, control_plane=plane, probe=False
    )

    assert snapshot["server"]["liveness"]["probed"] is False
    assert snapshot["verdict"]["health"] == caretaker.UNKNOWN


def test_missing_receipt_degrades_without_raising(tmp_path: Path) -> None:
    """A supervisor that never ran is a reported condition, not an exception."""
    plane = _plane(tmp_path)
    home = tmp_path / "empty-home"
    home.mkdir()

    snapshot = caretaker.build_caretaker_snapshot(
        home=home, control_plane=plane, probe=False
    )

    server = snapshot["server"]
    assert server["available"] is False
    assert "not published" in server["receipt"]["reason"]
    assert snapshot["verdict"]["health"] != caretaker.HEALTHY


def test_missing_control_plane_is_an_error_finding(tmp_path: Path) -> None:
    """An absent plane must be loud; silence would read as a clean plane."""
    absent = tmp_path / "no-such-plane"

    section = caretaker.build_maintenance_section(control_plane=absent)

    assert section["available"] is False
    assert [finding["code"] for finding in section["findings"]] == [
        "control_plane_missing"
    ]
    assert section["findings"][0]["severity"] == caretaker.ERROR


def test_maintenance_names_orphans_and_corruption(tmp_path: Path) -> None:
    """Upkeep findings are computed from the plane, not asserted from config."""
    plane = _plane(tmp_path)
    (plane / "runtime_runs" / "with-meta").mkdir()
    (plane / "runtime_runs" / "with-meta" / "meta.json").write_text(
        '{"run_id": "with-meta"}', encoding="utf-8"
    )
    (plane / "runtime_runs" / "orphan-a").mkdir()
    (plane / "runtime_runs" / "orphan-b").mkdir()
    (plane / "runs" / "good.json").write_text('{"run_id": "good"}', encoding="utf-8")
    (plane / "runs" / "broken.json").write_text("{not json", encoding="utf-8")

    section = caretaker.build_maintenance_section(control_plane=plane)

    assert section["scanned"] == 3
    assert section["orphan_runtime_runs"] == 2
    assert section["corrupt_run_snapshots"] == 1
    by_code = {finding["code"]: finding for finding in section["findings"]}
    assert by_code["orphan_runtime_runs"]["count"] == 2
    assert by_code["corrupt_run_snapshots"]["severity"] == caretaker.ERROR
    # Three retained directories is not retention pressure.
    assert "runtime_run_retention" not in by_code


def test_maintenance_reconciles_missing_runtime_meta_from_matching_snapshot(
    tmp_path: Path,
) -> None:
    """A durable projected snapshot remains an identity receipt after meta loss."""
    plane = _plane(tmp_path)
    run_dir = plane / "runtime_runs" / "impl-projected"
    run_dir.mkdir()
    (run_dir / "transcript.log").write_text("retained transcript\n", encoding="utf-8")
    (plane / "runs" / "impl-projected.json").write_text(
        json.dumps({"run_id": "impl-projected", "state": "completed"}),
        encoding="utf-8",
    )

    section = caretaker.build_maintenance_section(control_plane=plane)

    assert section["identified_runtime_runs"] == 1
    assert section["runtime_runs_identified_by_snapshot"] == 1
    assert section["orphan_runtime_runs"] == 0
    assert "orphan_runtime_runs" not in {
        finding["code"] for finding in section["findings"]
    }


def test_maintenance_preserves_unattributed_transcript_without_inventing_identity(
    tmp_path: Path,
) -> None:
    """A transcript alone is evidence, not authority for provider or run state."""
    plane = _plane(tmp_path)
    run_dir = plane / "runtime_runs" / "unknown-evidence"
    run_dir.mkdir()
    (run_dir / "transcript.log").write_text("partial output\n", encoding="utf-8")

    section = caretaker.build_maintenance_section(control_plane=plane)

    assert section["orphan_runtime_runs"] == 1
    assert section["runtime_runs_with_transcript_only"] == 1
    finding = next(
        finding
        for finding in section["findings"]
        if finding["code"] == "orphan_runtime_runs"
    )
    assert finding["transcript_only"] == 1
    assert "inferred identity" in finding["detail"]


def test_event_stream_pressure_is_reported(tmp_path: Path) -> None:
    """Rotation debt shows up before it makes unrelated commands slow."""
    plane = _plane(tmp_path)
    (plane / "events.jsonl").write_bytes(
        b"x" * (caretaker.EVENT_STREAM_PRESSURE_BYTES + 1)
    )

    section = caretaker.build_maintenance_section(control_plane=plane)

    codes = {finding["code"] for finding in section["findings"]}
    assert "event_stream_pressure" in codes


def test_event_pressure_and_automatic_rotation_use_one_threshold() -> None:
    """A caretaker warning must name work the event writer will perform."""
    from vibecrafted_core import control_plane

    assert caretaker.EVENT_STREAM_PRESSURE_BYTES == control_plane.EVENTS_ROTATE_BYTES


def test_resume_classification_is_delegated_not_duplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caretaker counts must equal ``init_resume``'s own classification.

    A second classifier here would drift from the one init prints into agent
    prompts, and the two would eventually disagree about the same run. The
    contract is delegation, so this asserts the caretaker's buckets against
    ``classify_resume_row`` applied to the identical rows.
    """
    from vibecrafted_core import init_resume, settlements_query

    rows = [
        {
            "run_id": "guardian-1",
            "agent": "codex",
            "skill": "implement",
            "root": "/repo",
            "native_resume_candidate": True,
            "trust_receipt_present": True,
        },
        {
            "run_id": "operator-1",
            "agent": "claude",
            "skill": "workflow",
            "root": "/repo",
            "revalidatable": True,
            "checkout_exists": True,
        },
        {
            "run_id": "evidence-1",
            "agent": "grok",
            "skill": "review",
            "root": "/repo",
        },
    ]
    monkeypatch.setattr(
        settlements_query, "list_settlements", lambda **_: {"runs": rows}
    )

    section = caretaker.build_resumeability_section()

    expected: dict[str, int] = dict.fromkeys(init_resume.RESUME_CLASSES, 0)
    for row in rows:
        expected[init_resume.classify_resume_row(row)] += 1

    assert section["available"] is True
    assert section["counts"] == expected
    assert section["matched"] == len(rows)
    # The rendered command is the public grammar, produced by the same owner.
    assert (
        section["classes"]["operator_resume"][0]["command"]
        == "vibecrafted resume claude --run-id operator-1"
    )


def test_resume_section_survives_an_unreadable_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken ledger must read as unknown, never as an empty backlog."""

    def _explode(**_: object) -> dict[str, object]:
        raise RuntimeError("ledger is a directory")

    monkeypatch.setattr("vibecrafted_core.settlements_query.list_settlements", _explode)

    section = caretaker.build_resumeability_section()

    assert section["available"] is False
    assert "ledger is a directory" in section["reason"]
    assert section["matched"] == 0


def test_publish_and_read_round_trip_with_freshness(tmp_path: Path) -> None:
    """The published bytes and the read view carry one schema and one age."""
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)

    snapshot = caretaker.build_caretaker_snapshot(
        home=home, control_plane=plane, probe=False
    )
    written = caretaker.publish_caretaker_snapshot(snapshot, control_plane=plane)
    assert written == plane / caretaker.CARETAKER_SNAPSHOT_NAME

    view = caretaker.read_caretaker_snapshot(control_plane=plane)

    assert view["published"] is True
    assert view["stale"] is False
    assert view["snapshot"]["schema"] == caretaker.CARETAKER_SCHEMA
    assert view["snapshot"]["verdict"] == snapshot["verdict"]
    assert view["age_seconds"] is not None


def test_read_reports_absence_and_corruption_distinctly(tmp_path: Path) -> None:
    """ "Never published" and "published garbage" need different responses."""
    plane = _plane(tmp_path)

    absent = caretaker.read_caretaker_snapshot(control_plane=plane)
    assert absent["published"] is False
    assert absent["stale"] is True
    assert "not published" in absent["reason"]

    (plane / caretaker.CARETAKER_SNAPSHOT_NAME).write_text(
        "{not json", encoding="utf-8"
    )
    corrupt = caretaker.read_caretaker_snapshot(control_plane=plane)
    assert corrupt["published"] is False
    assert "corrupt JSON" in corrupt["reason"]


def test_envelope_sections_are_all_present_and_schema_stamped(
    tmp_path: Path,
) -> None:
    """Every reader can rely on the same sections plus verdict and actions."""
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)

    snapshot = caretaker.build_caretaker_snapshot(
        home=home, control_plane=plane, probe=False
    )

    assert snapshot["schema"] == caretaker.CARETAKER_SCHEMA
    assert set(snapshot) >= {
        "schema",
        "generated_at",
        "control_plane",
        "server",
        "observability",
        "resumeability",
        "maintenance",
        "actions",
        "verdict",
    }
    for name in ("server", "observability", "resumeability", "maintenance"):
        assert "available" in snapshot[name], name
        assert "reason" in snapshot[name], name
    assert json.loads(json.dumps(snapshot)) == snapshot, "envelope must be JSON-clean"


def test_stopped_receipt_reads_as_stopped_not_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An intentional stop is not a crash and must not wear red.

    A supervisor that completes a stop writes ``state: stopped`` and exits, so
    that receipt never refreshes again. Reading its staleness as failure would
    punish the operator for a deliberate act; the verdict names it STOPPED and
    the actions offer start, not restart.
    """
    plane = _plane(tmp_path)
    home = _receipt(tmp_path, state="stopped", supervisor_pid=None)
    _reachable(
        monkeypatch, reachable=False, reason="ConnectionRefusedError: [Errno 61]"
    )

    snapshot = caretaker.build_caretaker_snapshot(home=home, control_plane=plane)
    verdict = snapshot["verdict"]

    assert verdict["health"] == caretaker.UNAVAILABLE
    assert verdict["server_state"] == "stopped"
    assert "STOPPED" in verdict["header"]
    assert "intentionally stopped" in verdict["detail"]
    assert "server_unreachable" not in {f["code"] for f in verdict["findings"]}

    actions = snapshot["actions"]
    assert actions["start"]["enabled"] is True
    assert actions["stop"]["enabled"] is False
    assert "already stopped" in actions["stop"]["reason"]
    assert actions["restart"]["enabled"] is False
    assert "start it instead" in actions["restart"]["reason"]


def test_actions_follow_the_fused_facts_when_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A serving server is not offered start; console opens by URL."""
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)
    _reachable(monkeypatch, reachable=True)

    snapshot = caretaker.build_caretaker_snapshot(home=home, control_plane=plane)
    verdict = snapshot["verdict"]
    actions = snapshot["actions"]

    assert verdict["server_state"] == "running"
    assert actions["start"]["enabled"] is False
    assert "already answering" in actions["start"]["reason"]
    assert actions["stop"]["enabled"] is True
    assert actions["restart"]["enabled"] is True
    assert actions["open_console"]["enabled"] is True
    assert actions["open_console"]["url"] == "http://127.0.0.1:3024"
    # The fixture home carries a server/ directory, so logs are a real
    # projection with paths, not a subprocess away.
    assert actions["open_logs"]["enabled"] is True
    assert actions["open_logs"]["paths"]["directory"].endswith("server")


def test_actions_when_server_is_down_offer_start_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Down-with-a-receipt: start and restart are the honest recovery verbs."""
    plane = _plane(tmp_path)
    home = _receipt(tmp_path, state="backoff", last_error="worker failed")
    _reachable(
        monkeypatch, reachable=False, reason="ConnectionRefusedError: [Errno 61]"
    )

    snapshot = caretaker.build_caretaker_snapshot(home=home, control_plane=plane)
    verdict = snapshot["verdict"]
    actions = snapshot["actions"]

    assert verdict["health"] == caretaker.UNAVAILABLE
    assert verdict["server_state"] == "down"
    assert actions["start"]["enabled"] is True
    assert actions["stop"]["enabled"] is False
    assert "not answering" in actions["stop"]["reason"]
    assert actions["restart"]["enabled"] is True
    assert actions["open_console"]["enabled"] is False
    assert "not answering" in actions["open_console"]["reason"]


def test_actions_are_honest_when_nothing_was_probed(tmp_path: Path) -> None:
    """An unprobed snapshot disables lifecycle verbs with the reason why."""
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)

    snapshot = caretaker.build_caretaker_snapshot(
        home=home, control_plane=plane, probe=False
    )
    actions = snapshot["actions"]

    assert snapshot["verdict"]["server_state"] == "unknown"
    for verb in ("start", "stop", "restart"):
        assert actions[verb]["enabled"] is False, verb
        assert "not probed" in actions[verb]["reason"], verb
    # Opening the console is still offered: there is no evidence of death.
    assert actions["open_console"]["enabled"] is True


def test_logs_projection_is_named_and_honest(tmp_path: Path) -> None:
    """Logs are a deterministic projection of the crafted home, not a guess."""
    plane = _plane(tmp_path)
    home = tmp_path / "bare-home"
    home.mkdir()

    snapshot = caretaker.build_caretaker_snapshot(
        home=home, control_plane=plane, probe=False
    )
    logs = snapshot["server"]["logs"]

    assert logs["available"] is False
    assert logs["directory"].endswith("server")
    assert logs["stdout"].endswith("supervisor.stdout.log")
    assert "no supervisor log directory" in logs["reason"]
    assert snapshot["actions"]["open_logs"]["enabled"] is False

    (home / "server").mkdir()
    snapshot = caretaker.build_caretaker_snapshot(
        home=home, control_plane=plane, probe=False
    )
    assert snapshot["server"]["logs"]["available"] is True
    assert snapshot["actions"]["open_logs"]["enabled"] is True
