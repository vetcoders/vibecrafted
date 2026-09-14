from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from vibecrafted_core.settlement_board import (
    SETTLEMENT_BOARD_SCOPE,
    SETTLEMENT_COUNTS_PIPE,
    SETTLEMENT_REPLAY_INTERVAL_SECONDS,
    DeliveryReport,
    ServerSettlementBoard,
    SettlementBoardError,
    SettlementBoardPublisher,
    _probe_session_socket_namespace,
    _read_server_state,
)

needs_posix_sockets = pytest.mark.skipif(
    os.name != "posix", reason="vc-frame session sockets are AF_UNIX files"
)


def server_state(f: int = 158, x: int = 30, n: int = 80) -> dict[str, object]:
    return {
        "generated_at": "2026-08-10T19:59:44+00:00",
        "settlement_counts": {
            "scope": SETTLEMENT_BOARD_SCOPE,
            "active": 4,
            "f": f,
            "x": x,
            "n": n,
            "invalid": 0,
            "unclassified": 13,
            "total_settled": f + x + n,
        },
    }


def ledger_state(f: int = 118, x: int = 435, n: int = 2247) -> dict[str, object]:
    return {
        "counts": {
            "historical_transitions": {
                "f": f,
                "x": x,
                "n": n,
                "total": f + x + n,
            }
        }
    }


@pytest.fixture
def socket_root() -> Iterator[Path]:
    # AF_UNIX paths stop near 104 bytes; pytest's tmp_path is too deep on macOS.
    root = Path(tempfile.mkdtemp(prefix="vcsb-", dir="/tmp"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def bind_session_socket(
    root: Path, name: str, contract: str = "contract_version_2"
) -> Path:
    """Leave a session socket file exactly where a vc-frame server binds one."""

    directory = root / contract
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
    finally:
        listener.close()
    return path


def session_listing(names: list[str]) -> str:
    return "".join(f"{name} [Created 0s ago] \n" for name in names)


@dataclass
class DiscoveryProbe:
    discoveries: int
    board_reads: int
    reports: list[DeliveryReport]


def probe_discovery(
    tmp_path: Path, socket_env: dict[str, str], *, refreshes: int = 2
) -> DiscoveryProbe:
    """Run production refreshes and count the vc-frame discoveries they spawn."""

    binary = tmp_path / "vc-frame"
    binary.touch()
    discoveries = 0
    board_reads = 0

    def runner(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal discoveries
        del timeout
        if "list-sessions" in argv:
            discoveries += 1
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="No active vc-frame sessions found.\n"
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def read_board(_url: str, _timeout: float) -> dict[str, object]:
        nonlocal board_reads
        board_reads += 1
        return server_state()

    publisher = SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path / "control_plane",
        board_reader=read_board,
        ledger_reader=lambda _path: ledger_state(),
        runner=runner,
        env={"VIBECRAFTED_VC_FRAME_BIN": str(binary), **socket_env},
    )
    reports = [publisher.refresh_and_flush() for _ in range(refreshes)]
    return DiscoveryProbe(discoveries, board_reads, reports)


def test_server_board_requires_canonical_scope_and_consistent_totals() -> None:
    board = ServerSettlementBoard.from_state(server_state())
    assert (board.f, board.x, board.n) == (158, 30, 80)

    wrong_scope = server_state()
    assert isinstance(wrong_scope["settlement_counts"], dict)
    wrong_scope["settlement_counts"]["scope"] = "all_time"
    with pytest.raises(SettlementBoardError, match="scope"):
        ServerSettlementBoard.from_state(wrong_scope)

    wrong_total = server_state()
    assert isinstance(wrong_total["settlement_counts"], dict)
    wrong_total["settlement_counts"]["total_settled"] = 999
    with pytest.raises(SettlementBoardError, match="inconsistent"):
        ServerSettlementBoard.from_state(wrong_total)


def test_server_reader_rejects_non_http_transport() -> None:
    with pytest.raises(SettlementBoardError, match="http or https"):
        _read_server_state("file:///etc/passwd", 1.0)


def test_wire_displays_server_counts_not_local_historical_mountain(
    tmp_path: Path,
) -> None:
    publisher = SettlementBoardPublisher(
        server_url="http://100.82.232.70:3025",
        control_plane_root=tmp_path,
        board_reader=lambda _url, _timeout: server_state(),
        ledger_reader=lambda _path: ledger_state(),
        env={},
    )

    payload = json.loads(publisher._compatibility_payload())

    assert payload["latest_by_run"] == {"f": 158, "x": 30, "n": 80, "total": 268}
    assert payload["historical_transitions"] == {
        "f": 158,
        "x": 435,
        "n": 2247,
        "total": 2840,
    }
    assert payload["gaps"] == 0
    assert payload["complete_from"] == 1
    assert not (tmp_path / "settlement_history.json").exists()


def test_payload_identity_is_stable_until_canonical_board_changes(
    tmp_path: Path,
) -> None:
    states = [server_state(), server_state(), server_state(f=157)]

    def read_board(_url: str, _timeout: float) -> dict[str, object]:
        return states.pop(0)

    publisher = SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path,
        board_reader=read_board,
        ledger_reader=lambda _path: ledger_state(),
        env={},
    )

    first = json.loads(publisher._compatibility_payload())
    replay = json.loads(publisher._compatibility_payload())
    changed = json.loads(publisher._compatibility_payload())

    assert replay == first
    assert changed["generation"] != first["generation"]
    assert changed["sequence"] == first["sequence"]
    assert changed["latest_by_run"]["f"] == 157


def test_transport_carrier_stays_monotonic_across_guardian_restart(
    tmp_path: Path,
) -> None:
    first = SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path,
        board_reader=lambda _url, _timeout: server_state(f=158),
        ledger_reader=lambda _path: ledger_state(),
        env={},
    )
    first_payload = json.loads(first._compatibility_payload())

    restarted = SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path,
        board_reader=lambda _url, _timeout: server_state(f=157),
        ledger_reader=lambda _path: ledger_state(),
        env={},
    )
    restarted_payload = json.loads(restarted._compatibility_payload())

    assert (
        restarted_payload["historical_transitions"]
        == first_payload["historical_transitions"]
    )
    assert restarted_payload["latest_by_run"]["f"] == 157


@needs_posix_sockets
def test_refresh_pipes_canonical_board_only_to_plugin_sessions(
    tmp_path: Path, socket_root: Path
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    bind_session_socket(socket_root, "live-one")
    calls: list[list[str]] = []

    def runner(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        assert timeout == 2.0
        calls.append(argv)
        if "list-sessions" in argv:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    "Finalized runs [Created now]\n"
                    "live-one [Created now]\n"
                    "dead [Created now] (EXITED - attach to resurrect)\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    publisher = SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path / "control_plane",
        board_reader=lambda _url, _timeout: server_state(),
        ledger_reader=lambda _path: ledger_state(),
        runner=runner,
        env={
            "VIBECRAFTED_VC_FRAME_BIN": str(binary),
            "VC_FRAME_SOCKET_DIR": str(socket_root),
        },
        timeout=2.0,
    )

    report = publisher.refresh_and_flush()

    assert report.delivered_sessions == ("live-one",)
    assert len(calls) == 2
    pipe = calls[1]
    assert pipe[1:7] == [
        "--session",
        "live-one",
        "pipe",
        "--name",
        SETTLEMENT_COUNTS_PIPE,
        "--",
    ]
    assert json.loads(pipe[7])["latest_by_run"] == {
        "f": 158,
        "x": 30,
        "n": 80,
        "total": 268,
    }


@needs_posix_sockets
def test_delivery_failure_is_backed_off_without_blocking_new_snapshot(
    tmp_path: Path, socket_root: Path
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    bind_session_socket(socket_root, "live")
    now = [10.0]
    pipe_attempts = 0

    def runner(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal pipe_attempts
        del timeout
        if "list-sessions" in argv:
            return subprocess.CompletedProcess(
                argv, 0, stdout="live [Created now]\n", stderr=""
            )
        pipe_attempts += 1
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="busy")

    publisher = SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path / "control_plane",
        board_reader=lambda _url, _timeout: server_state(),
        ledger_reader=lambda _path: ledger_state(),
        runner=runner,
        env={
            "VIBECRAFTED_VC_FRAME_BIN": str(binary),
            "VC_FRAME_SOCKET_DIR": str(socket_root),
        },
        retry_backoff=300.0,
        clock=lambda: now[0],
    )

    assert publisher.refresh_and_flush().failed_sessions == ("live",)
    assert publisher.refresh_and_flush().deferred_sessions == ("live",)
    assert pipe_attempts == 1


@needs_posix_sockets
def test_default_delivery_backoff_retries_on_next_periodic_replay(
    tmp_path: Path, socket_root: Path
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    bind_session_socket(socket_root, "live")
    now = [10.0]
    pipe_attempts = 0

    def runner(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal pipe_attempts
        del timeout
        if "list-sessions" in argv:
            return subprocess.CompletedProcess(
                argv, 0, stdout="live [Created now]\n", stderr=""
            )
        pipe_attempts += 1
        return subprocess.CompletedProcess(
            argv,
            1 if pipe_attempts == 1 else 0,
            stdout="",
            stderr="busy" if pipe_attempts == 1 else "",
        )

    publisher = SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path / "control_plane",
        board_reader=lambda _url, _timeout: server_state(),
        ledger_reader=lambda _path: ledger_state(),
        runner=runner,
        env={
            "VIBECRAFTED_VC_FRAME_BIN": str(binary),
            "VC_FRAME_SOCKET_DIR": str(socket_root),
        },
        clock=lambda: now[0],
    )

    assert publisher.refresh_and_flush().failed_sessions == ("live",)
    now[0] += 5.0
    assert publisher.refresh_and_flush().delivered_sessions == ("live",)
    assert pipe_attempts == 2


def _namespace_absent(root: Path) -> Path:
    return root / "never-created"


def _only_client_logs(root: Path) -> Path:
    (root / "vc-frame-log" / "client-4242").mkdir(parents=True)
    return root


def _empty_contract_dir(root: Path) -> Path:
    (root / "contract_version_2").mkdir()
    return root


def _no_socket_in_contract_dir(root: Path) -> Path:
    contract = root / "contract_version_2"
    contract.mkdir()
    (contract / "session-info").write_text("", encoding="utf-8")
    # vc-frame lists entries without following links and ignores the root.
    outside = root / "outside.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(outside))
    finally:
        listener.close()
    (contract / "linked").symlink_to(outside)
    return root


@needs_posix_sockets
@pytest.mark.parametrize(
    "shape",
    [
        _namespace_absent,
        _only_client_logs,
        _empty_contract_dir,
        _no_socket_in_contract_dir,
    ],
    ids=["absent", "client-logs-only", "empty-contract", "no-socket-entry"],
)
def test_idle_replay_in_empty_socket_namespace_spawns_no_frame_discovery(
    tmp_path: Path, socket_root: Path, shape
) -> None:
    probe = probe_discovery(
        tmp_path, {"VC_FRAME_SOCKET_DIR": str(shape(socket_root))}, refreshes=3
    )

    assert probe.discoveries == 0
    assert probe.board_reads == 0
    assert all(
        report.pending and not report.attempted_sessions for report in probe.reports
    )


@needs_posix_sockets
def test_socket_arrival_resumes_discovery_and_replays_unchanged_board_to_new_plugin(
    tmp_path: Path, socket_root: Path
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    running: list[str] = []
    pipes: list[list[str]] = []
    discoveries = 0

    def runner(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal discoveries
        del timeout
        if "list-sessions" in argv:
            discoveries += 1
            return subprocess.CompletedProcess(
                argv, 0, stdout=session_listing(running), stderr=""
            )
        pipes.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    publisher = SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path / "control_plane",
        board_reader=lambda _url, _timeout: server_state(),
        ledger_reader=lambda _path: ledger_state(),
        runner=runner,
        env={
            "VIBECRAFTED_VC_FRAME_BIN": str(binary),
            "VC_FRAME_SOCKET_DIR": str(socket_root),
        },
    )

    assert publisher.refresh_and_flush().attempted_sessions == ()
    assert discoveries == 0

    bind_session_socket(socket_root, "live-one")
    running.append("live-one")
    assert publisher.refresh_and_flush().delivered_sessions == ("live-one",)
    assert discoveries == 1

    bind_session_socket(socket_root, "live-two")
    running.append("live-two")
    assert "live-two" in publisher.refresh_and_flush().delivered_sessions
    assert discoveries == 2
    board_for_first_plugin = pipes[0][7]
    assert [argv[7] for argv in pipes if argv[2] == "live-two"] == [
        board_for_first_plugin
    ]


@needs_posix_sockets
def test_periodic_replay_discovers_a_new_session_socket_on_its_next_tick(
    tmp_path: Path, socket_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    running: list[str] = []
    discoveries: list[list[str]] = []
    delivered = threading.Event()

    def runner(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        if "list-sessions" in argv:
            discoveries.append(argv)
            return subprocess.CompletedProcess(
                argv, 0, stdout=session_listing(list(running)), stderr=""
            )
        delivered.set()
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    publisher = SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path / "control_plane",
        board_reader=lambda _url, _timeout: server_state(),
        ledger_reader=lambda _path: ledger_state(),
        runner=runner,
        env={
            "VIBECRAFTED_VC_FRAME_BIN": str(binary),
            "VC_FRAME_SOCKET_DIR": str(socket_root),
        },
    )
    ticks: list[DeliveryReport] = []
    ticked = threading.Condition()
    refresh = publisher.refresh_and_flush

    def counted_refresh() -> DeliveryReport:
        report = refresh()
        with ticked:
            ticks.append(report)
            ticked.notify_all()
        return report

    monkeypatch.setattr(publisher, "refresh_and_flush", counted_refresh)

    assert 0 < SETTLEMENT_REPLAY_INTERVAL_SECONDS <= 5
    try:
        assert publisher.start_periodic_refresh(interval=0.02) is True
        with ticked:
            assert ticked.wait_for(lambda: len(ticks) >= 3, timeout=2)
        assert discoveries == []

        bind_session_socket(socket_root, "live")
        running.append("live")
        assert delivered.wait(timeout=2)
    finally:
        assert publisher.stop_periodic_refresh(timeout=1)
        assert publisher.wait_for_idle(timeout=1)

    assert discoveries
    assert any(report.delivered_sessions == ("live",) for report in ticks)


@needs_posix_sockets
def test_socket_dir_precedence_matches_vc_frame_aliases(
    tmp_path: Path, socket_root: Path
) -> None:
    primary = socket_root / "primary"
    (primary / "contract_version_2").mkdir(parents=True)
    legacy = socket_root / "legacy"
    bind_session_socket(legacy, "live")

    primary_wins = probe_discovery(
        tmp_path,
        {"VC_FRAME_SOCKET_DIR": str(primary), "ZELLIJ_SOCKET_DIR": str(legacy)},
    )
    legacy_alone = probe_discovery(tmp_path, {"ZELLIJ_SOCKET_DIR": str(legacy)})
    primary_with_socket = probe_discovery(
        tmp_path,
        {"VC_FRAME_SOCKET_DIR": str(legacy), "ZELLIJ_SOCKET_DIR": str(primary)},
    )

    assert primary_wins.discoveries == 0
    assert legacy_alone.discoveries == 2
    assert primary_with_socket.discoveries == 2


@needs_posix_sockets
@pytest.mark.parametrize(
    "contract", ["contract_version_2", "contract_version_3"], ids=["current", "next"]
)
def test_stale_socket_defers_liveness_to_vc_frame_and_is_left_in_place(
    tmp_path: Path, socket_root: Path, contract: str
) -> None:
    stale = bind_session_socket(socket_root, "crashed", contract=contract)

    probe = probe_discovery(tmp_path, {"VC_FRAME_SOCKET_DIR": str(socket_root)})

    assert probe.discoveries == 2
    assert all(
        report.pending and not report.delivered_sessions for report in probe.reports
    )
    assert stat.S_ISSOCK(stale.lstat().st_mode)


@needs_posix_sockets
@pytest.mark.parametrize(
    "shape",
    [
        "relative",
        "empty",
        "legacy-relative",
        "root-is-file",
        "root-unreadable",
        "contract-unreadable",
    ],
)
def test_unresolvable_or_unreadable_namespace_keeps_canonical_discovery(
    tmp_path: Path, socket_root: Path, shape: str
) -> None:
    locked: Path | None = None
    if shape == "relative":
        socket_env = {"VC_FRAME_SOCKET_DIR": "vc-frame-sockets"}
    elif shape == "empty":
        socket_env = {"VC_FRAME_SOCKET_DIR": ""}
    elif shape == "legacy-relative":
        socket_env = {"ZELLIJ_SOCKET_DIR": "vc-frame-sockets"}
    elif shape == "root-is-file":
        not_a_directory = socket_root / "sockets"
        not_a_directory.write_text("", encoding="utf-8")
        socket_env = {"VC_FRAME_SOCKET_DIR": str(not_a_directory)}
    else:
        if os.geteuid() == 0:
            pytest.skip("root reads through permission bits")
        namespace = socket_root / "sockets"
        bind_session_socket(namespace, "live")
        locked = namespace
        if shape == "contract-unreadable":
            locked = namespace / "contract_version_2"
        socket_env = {"VC_FRAME_SOCKET_DIR": str(namespace)}
        locked.chmod(0)
    try:
        probe = probe_discovery(tmp_path, socket_env)
    finally:
        if locked is not None:
            locked.chmod(0o700)

    assert probe.discoveries == 2


@needs_posix_sockets
def test_default_socket_root_is_mirrored_only_where_vc_frame_pins_it(
    socket_root: Path,
) -> None:
    darwin = _probe_session_socket_namespace({}, platform="darwin")
    linux_default = _probe_session_socket_namespace({}, platform="linux")
    linux_override = _probe_session_socket_namespace(
        {"VC_FRAME_SOCKET_DIR": str(socket_root)}, platform="linux"
    )

    assert darwin.root == Path(f"/tmp/vc-frame-{os.getuid()}")
    assert linux_default.root is None
    assert not linux_default.provably_empty
    assert linux_override.root == socket_root
    assert linux_override.provably_empty


FAKE_VC_FRAME = """#!@PYTHON@
import json
import os
import pathlib
import stat
import sys

# Like the donor CLI, every run opens its own client log directory first.
logs = pathlib.Path(os.environ["FAKE_VC_FRAME_LOG_ROOT"]) / "vc-frame-log"
(logs / f"client-{os.getpid()}").mkdir(parents=True)
argv = sys.argv[1:]
if argv[:1] == ["list-sessions"]:
    contract = pathlib.Path(os.environ["VC_FRAME_SOCKET_DIR"]) / "contract_version_2"
    names = sorted(
        entry.name
        for entry in (contract.iterdir() if contract.is_dir() else ())
        if stat.S_ISSOCK(entry.lstat().st_mode)
    )
    if not names:
        print("No active vc-frame sessions found.", file=sys.stderr)
        sys.exit(1)
    for name in names:
        print(f"{name} [Created 0s ago] ")
    sys.exit(0)
if "pipe" in argv:
    with open(os.environ["FAKE_VC_FRAME_PIPES"], "a", encoding="utf-8") as record:
        record.write(json.dumps(argv) + "\\n")
    sys.exit(0)
sys.exit(2)
"""


@contextmanager
def serve_control_state(state: dict[str, object]) -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []
    body = json.dumps(state).encode("utf-8")

    class StateHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            if self.path != "/api/control/state":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), StateHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@needs_posix_sockets
def test_production_publisher_spawns_no_frame_client_until_a_session_socket_exists(
    tmp_path: Path, socket_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tmp_path / "vc-frame"
    frame.write_text(FAKE_VC_FRAME.replace("@PYTHON@", sys.executable), "utf-8")
    frame.chmod(0o755)
    pipes = tmp_path / "pipes.jsonl"
    client_logs = socket_root / "vc-frame-log"
    (client_logs / "client-interactive").mkdir(parents=True)
    for proxy in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(proxy, raising=False)
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("VIBECRAFTED_VC_FRAME_BIN", str(frame))
    monkeypatch.setenv("VC_FRAME_SOCKET_DIR", str(socket_root))
    monkeypatch.setenv("FAKE_VC_FRAME_LOG_ROOT", str(socket_root))
    monkeypatch.setenv("FAKE_VC_FRAME_PIPES", str(pipes))

    with serve_control_state(server_state()) as (server_url, state_requests):
        # Constructed the way Guardian constructs it: real subprocess runner,
        # real HTTP board reader, real ledger under the isolated home.
        publisher = SettlementBoardPublisher(server_url=server_url)
        idle = [publisher.refresh_and_flush() for _ in range(3)]

        assert all(report.pending and not report.attempted_sessions for report in idle)
        assert [entry.name for entry in client_logs.iterdir()] == ["client-interactive"]
        assert state_requests == []

        bind_session_socket(socket_root, "live")
        arrived = publisher.refresh_and_flush()

    assert arrived.delivered_sessions == ("live",)
    assert state_requests == ["/api/control/state"]
    spawned = [
        entry.name
        for entry in client_logs.iterdir()
        if entry.name != "client-interactive"
    ]
    assert len(spawned) == 2
    assert (client_logs / "client-interactive").is_dir()
    (pipe,) = [json.loads(line) for line in pipes.read_text("utf-8").splitlines()]
    assert pipe[:6] == [
        "--session",
        "live",
        "pipe",
        "--name",
        SETTLEMENT_COUNTS_PIPE,
        "--",
    ]
    assert json.loads(pipe[6])["latest_by_run"] == {
        "f": 158,
        "x": 30,
        "n": 80,
        "total": 268,
    }
