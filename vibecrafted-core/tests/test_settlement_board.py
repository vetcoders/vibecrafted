from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core.settlement_board import (
    SETTLEMENT_BOARD_SCOPE,
    SETTLEMENT_COUNTS_PIPE,
    SETTLEMENT_REPLAY_INTERVAL_SECONDS,
    ServerSettlementBoard,
    SettlementBoardError,
    SettlementBoardPublisher,
    _read_server_state,
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


def test_refresh_pipes_canonical_board_only_to_plugin_sessions(tmp_path: Path) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
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
        env={"VIBECRAFTED_VC_FRAME_BIN": str(binary)},
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


def test_delivery_failure_is_backed_off_without_blocking_new_snapshot(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
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
        env={"VIBECRAFTED_VC_FRAME_BIN": str(binary)},
        retry_backoff=300.0,
        clock=lambda: now[0],
    )

    assert publisher.refresh_and_flush().failed_sessions == ("live",)
    assert publisher.refresh_and_flush().deferred_sessions == ("live",)
    assert pipe_attempts == 1


def test_default_delivery_backoff_retries_on_next_periodic_replay(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
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
        env={"VIBECRAFTED_VC_FRAME_BIN": str(binary)},
        clock=lambda: now[0],
    )

    assert publisher.refresh_and_flush().failed_sessions == ("live",)
    now[0] += 5.0
    assert publisher.refresh_and_flush().delivered_sessions == ("live",)
    assert pipe_attempts == 2


def test_delivery_deduplicates_unchanged_payload_and_redelivers_on_change(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    pipe_calls: list[list[str]] = []
    current_f = [158]

    def runner(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        if "list-sessions" in argv:
            return subprocess.CompletedProcess(
                argv, 0, stdout="live [Created now]\n", stderr=""
            )
        pipe_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    publisher = SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path / "control_plane",
        board_reader=lambda _url, _timeout: server_state(f=current_f[0]),
        ledger_reader=lambda _path: ledger_state(),
        runner=runner,
        env={"VIBECRAFTED_VC_FRAME_BIN": str(binary)},
    )

    # First delivery: should pipe
    report1 = publisher.refresh_and_flush()
    assert report1.delivered_sessions == ("live",)
    assert len(pipe_calls) == 1

    # Second delivery with identical payload: should deduplicate (no pipe!)
    report2 = publisher.refresh_and_flush()
    assert report2.delivered_sessions == ()
    assert report2.unchanged_sessions == ("live",)
    assert len(pipe_calls) == 1

    # Third delivery with changed payload: should pipe the new payload
    current_f[0] = 160
    report3 = publisher.refresh_and_flush()
    assert report3.delivered_sessions == ("live",)
    assert len(pipe_calls) == 2
    assert json.loads(pipe_calls[1][7])["latest_by_run"]["f"] == 160


def test_socket_discovery_idle_and_arrival(tmp_path: Path) -> None:
    from vibecrafted_core.settlement_history import (
        _discover_running_sessions_from_sockets,
    )

    sockets_root = tmp_path / "sockets"
    sock_dir = sockets_root / "contract_version_2"
    sock_dir.mkdir(parents=True)
    env = {"VC_FRAME_SOCKET_DIR": str(sockets_root)}

    # Empty socket directory: zero sessions
    assert _discover_running_sessions_from_sockets(env) == ()

    # Non-session sockets (web_server_bus) and non-plugin sessions ignored
    (sock_dir / "web_server_bus").touch()
    (sock_dir / "Finalized runs").touch()
    assert _discover_running_sessions_from_sockets(env) == ()

    # Real session arriving
    (sock_dir / "worker-session").touch()
    assert _discover_running_sessions_from_sockets(env) == ("worker-session",)


def test_default_runner_uses_socket_discovery_zero_processes_when_idle(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "vc-frame"
    binary.touch()
    sockets_root = tmp_path / "sockets"
    sock_dir = sockets_root / "contract_version_2"
    sock_dir.mkdir(parents=True)

    publisher = SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path / "control_plane",
        board_reader=lambda _url, _timeout: server_state(),
        ledger_reader=lambda _path: ledger_state(),
        env={
            "VIBECRAFTED_VC_FRAME_BIN": str(binary),
            "VC_FRAME_SOCKET_DIR": str(sockets_root),
        },
    )

    # With empty socket dir, refresh_and_flush should discover 0 sessions without calling binary
    report = publisher.refresh_and_flush()
    assert report.delivered_sessions == ()
    assert report.reason == "no eligible vc-frame plugin sessions"
    assert report.pending is True


def _humantime(seconds: float) -> str:
    remaining = int(seconds)
    parts: list[str] = []
    for unit, size in (("h", 3_600), ("m", 60), ("s", 1)):
        value, remaining = divmod(remaining, size)
        if value:
            parts.append(f"{value}{unit}")
    return " ".join(parts) or "0s"


class _FakeFrame:
    """vc-frame CLI stand-in: a live session listing plus scripted pipe outcomes.

    Every pipe recorded here is one CLI client attach on the real engine.
    """

    def __init__(self, clock: list[float]) -> None:
        self.clock = clock
        self.sessions: dict[str, float] = {}  # name -> socket birth on the clock
        self.pipe_returncodes: list[int] = []
        self.pipes: list[tuple[str, str]] = []

    def __call__(
        self, argv: list[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        if "list-sessions" in argv:
            listing = "".join(
                f"{name} [Created {_humantime(self.clock[0] - born)} ago] \n"
                for name, born in self.sessions.items()
            )
            return subprocess.CompletedProcess(argv, 0, stdout=listing, stderr="")
        session = argv[argv.index("--session") + 1]
        self.pipes.append((session, argv[-1]))
        code = self.pipe_returncodes.pop(0) if self.pipe_returncodes else 0
        return subprocess.CompletedProcess(argv, code, stdout="", stderr="")


def _board_publisher(
    tmp_path: Path,
    frame: _FakeFrame,
    board: list[dict[str, object]],
    *,
    retry_backoff: float = 1.0,
) -> SettlementBoardPublisher:
    binary = tmp_path / "vc-frame"
    binary.touch()
    return SettlementBoardPublisher(
        server_url="http://server.example:3025",
        control_plane_root=tmp_path / "control_plane",
        board_reader=lambda _url, _timeout: board[0],
        ledger_reader=lambda _path: ledger_state(),
        runner=frame,
        env={"VIBECRAFTED_VC_FRAME_BIN": str(binary)},
        retry_backoff=retry_backoff,
        clock=lambda: frame.clock[0],
    )


def test_unchanged_board_is_piped_once_per_session_across_replays(
    tmp_path: Path,
) -> None:
    frame = _FakeFrame([100.0])
    frame.sessions = {"live-one": -3723.0, "live-two": 40.0}
    board = [server_state()]
    publisher = _board_publisher(tmp_path, frame, board)

    first = publisher.refresh_and_flush()
    for _ in range(11):  # one minute of periodic replays
        frame.clock[0] += SETTLEMENT_REPLAY_INTERVAL_SECONDS
        replay = publisher.refresh_and_flush()

    assert [session for session, _ in frame.pipes] == ["live-one", "live-two"]
    assert first.delivered_sessions == ("live-one", "live-two")
    assert replay.delivered_sessions == ()
    assert replay.unchanged_sessions == ("live-one", "live-two")
    assert replay.pending is False
    assert replay.reason == ""

    board[0] = server_state(f=157)
    frame.clock[0] += SETTLEMENT_REPLAY_INTERVAL_SECONDS
    changed = publisher.refresh_and_flush()

    assert changed.delivered_sessions == ("live-one", "live-two")
    assert len(frame.pipes) == 4
    assert json.loads(frame.pipes[-1][1])["latest_by_run"]["f"] == 157


def test_failed_delivery_resends_the_unchanged_board_after_backoff(
    tmp_path: Path,
) -> None:
    frame = _FakeFrame([100.0])
    frame.sessions = {"live": 0.0}
    frame.pipe_returncodes = [1]
    publisher = _board_publisher(tmp_path, frame, [server_state()], retry_backoff=30.0)

    assert publisher.refresh_and_flush().failed_sessions == ("live",)
    frame.clock[0] += 5.0
    assert publisher.refresh_and_flush().deferred_sessions == ("live",)
    frame.clock[0] += 30.0
    assert publisher.refresh_and_flush().delivered_sessions == ("live",)
    frame.clock[0] += 5.0
    settled = publisher.refresh_and_flush()

    assert len(frame.pipes) == 2
    assert frame.pipes[0][1] == frame.pipes[1][1]
    assert settled.unchanged_sessions == ("live",)
    assert settled.pending is False


def test_restarted_or_returning_session_receives_the_unchanged_board_again(
    tmp_path: Path,
) -> None:
    frame = _FakeFrame([1000.0])
    frame.sessions = {"alpha": 0.0, "beta": 0.0}
    publisher = _board_publisher(tmp_path, frame, [server_state()])

    publisher.refresh_and_flush()
    frame.clock[0] += 5.0
    publisher.refresh_and_flush()
    assert len(frame.pipes) == 2

    # alpha is killed and resurrected between two ticks: same name, new socket.
    frame.sessions["alpha"] = frame.clock[0] + 1.0
    frame.clock[0] += 5.0
    restarted = publisher.refresh_and_flush()
    assert restarted.delivered_sessions == ("alpha",)
    assert restarted.unchanged_sessions == ("beta",)

    # beta drops out of the listing for one tick, then comes back.
    del frame.sessions["beta"]
    frame.clock[0] += 5.0
    publisher.refresh_and_flush()
    frame.sessions["beta"] = 0.0
    frame.clock[0] += 5.0
    returned = publisher.refresh_and_flush()

    assert returned.delivered_sessions == ("beta",)
    assert returned.unchanged_sessions == ("alpha",)
    assert [session for session, _ in frame.pipes] == [
        "alpha",
        "beta",
        "alpha",
        "beta",
    ]


def test_session_listing_reads_humantime_created_age() -> None:
    from vibecrafted_core.settlement_board import _running_sessions

    listing = (
        "alpha [Created 2days 3h 4m 5s ago] \n"
        "beta [Created 1year 2months ago] (current)\n"
        "gamma [Created now]\n"
        "dead [Created 9s ago] (EXITED - attach to resurrect)\n"
    )

    assert _running_sessions(listing) == (
        ("alpha", 2 * 86_400 + 3 * 3_600 + 4 * 60 + 5),
        ("beta", 31_557_600 + 2 * 2_630_016),
        ("gamma", None),
    )
