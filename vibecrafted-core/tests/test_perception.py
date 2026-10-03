from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from vibecrafted_core import perception

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeProc:
    """Stands in for a ``loct watch`` Popen handle.

    ``poll_codes`` are returned in sequence by successive ``poll()`` calls; the
    last value sticks. ``None`` means "still running".
    """

    def __init__(self, poll_codes: Sequence[int | None], pid: int = 4242) -> None:
        self._codes = list(poll_codes) or [None]
        self._i = 0
        self.pid = pid

    def poll(self) -> int | None:
        if self._i < len(self._codes):
            value = self._codes[self._i]
            self._i += 1
            return value
        return self._codes[-1]


def _recording_spawner(proc: FakeProc):
    calls: list[list[str]] = []

    def spawn(cmd: Sequence[str]) -> FakeProc:
        calls.append(list(cmd))
        return proc

    spawn.calls = calls
    return spawn


def _fast_clock():
    """Monotonic-ish clock that advances 1.0 per call so probe loops terminate."""
    state = {"t": 0.0}

    def clock() -> float:
        state["t"] += 1.0
        return state["t"]

    return clock


def _no_sleep(_seconds: float) -> None:  # trivial
    return None


def _always_loct() -> str | None:
    return "loct"


def _ensure(root, **kwargs):
    base = {
        "resolver": _always_loct,
        "sleeper": _no_sleep,
        "clock": _fast_clock(),
        "lock_probe": lambda _root: False,
    }
    base.update(kwargs)
    return perception.ensure_watch(root, **base)


# ---------------------------------------------------------------------------
# Watcher canon — scans are per-root; MCP transport is owned separately
# ---------------------------------------------------------------------------


def test_default_watcher_mode_never_spawns_an_http_mcp() -> None:
    assert perception.DEFAULT_WATCH_MODE == "scan"


def test_mcp_config_entry_default_is_http_url(tmp_path: Path) -> None:
    entry = perception.loctree_mcp_config_entry(tmp_path)
    assert entry["type"] == "http"
    assert entry["url"].startswith("http://127.0.0.1:")
    assert entry["url"].endswith("/mcp")
    assert "command" not in entry


def test_mcp_servers_config_wires_loctree_over_http(tmp_path: Path) -> None:
    cfg = perception.mcp_servers_config(tmp_path)
    loctree = cfg["mcpServers"]["loctree"]
    assert loctree["type"] == "http"
    assert loctree["url"].endswith("/mcp")


def test_default_template_entry_uses_default_port() -> None:
    entry = perception.default_loctree_mcp_config_entry()
    assert entry == {"type": "http", "url": "http://127.0.0.1:5174/mcp"}


def test_stdio_transport_is_explicit_legacy_fallback(tmp_path: Path) -> None:
    entry = perception.loctree_mcp_config_entry(tmp_path, transport="stdio")
    assert entry["command"] == "loctree-mcp"
    assert "url" not in entry


def test_build_watch_command_default_is_scan_only(tmp_path: Path) -> None:
    cmd = perception.build_watch_command("loct", tmp_path)
    assert cmd[:2] == ["loct", "watch"]
    assert "--bg" in cmd
    assert "--http" not in cmd
    assert cmd[-1] == perception.canonical_root(tmp_path)


def test_build_watch_command_stdio_falls_back_to_bg(tmp_path: Path) -> None:
    cmd = perception.build_watch_command("loct", tmp_path, transport="stdio")
    assert "--bg" in cmd
    assert "--http" not in cmd


# ---------------------------------------------------------------------------
# Per-root port derivation
# ---------------------------------------------------------------------------


def test_port_for_root_is_deterministic_and_in_window(tmp_path: Path) -> None:
    first = perception.port_for_root(tmp_path)
    second = perception.port_for_root(tmp_path)
    assert first == second
    assert perception.DEFAULT_MCP_PORT <= first < perception.DEFAULT_MCP_PORT + 800


def test_port_for_root_differs_across_roots(tmp_path: Path) -> None:
    a = tmp_path / "repo_a"
    b = tmp_path / "repo_b"
    a.mkdir()
    b.mkdir()
    # Distinct roots should (overwhelmingly) get distinct ports so simultaneous
    # per-root watchers do not collide on 5174.
    assert perception.port_for_root(a) != perception.port_for_root(b)


def test_canonical_root_collapses_trailing_slash(tmp_path: Path) -> None:
    plain = perception.canonical_root(tmp_path)
    trailing = perception.canonical_root(str(tmp_path) + "/")
    assert plain == trailing


# ---------------------------------------------------------------------------
# Single-instance: lock holds, second start does not multiply
# ---------------------------------------------------------------------------


def test_lock_held_skips_spawn(tmp_path: Path) -> None:
    def boom(_cmd: Sequence[str]):  # must not be called
        pytest.fail("ensure_watch must not spawn when a watcher already holds the lock")

    outcome = _ensure(tmp_path, lock_probe=lambda _root: True, spawner=boom)
    assert outcome.status == "already_running"
    assert outcome.transport == "scan"
    assert outcome.endpoint is None


def test_contention_exit_75_is_already_running_not_crash(tmp_path: Path) -> None:
    spawn = _recording_spawner(FakeProc([perception.EXIT_LOCK_CONTENDED]))
    outcome = _ensure(tmp_path, spawner=spawn)
    assert outcome.status == "already_running"
    assert outcome.returncode == perception.EXIT_LOCK_CONTENDED
    assert len(spawn.calls) == 1


def test_two_starts_do_not_multiply(tmp_path: Path) -> None:
    # First start spawns and takes the lock; the second sees the lock held and
    # skips — net one watcher, never multiplied across agent shells.
    state = {"running": False}
    calls: list[list[str]] = []

    def lock_probe(_root) -> bool:
        return state["running"]

    def spawn(cmd: Sequence[str]) -> FakeProc:
        calls.append(list(cmd))
        state["running"] = True
        return FakeProc([None])

    first = perception.ensure_watch(
        tmp_path,
        resolver=_always_loct,
        spawner=spawn,
        lock_probe=lock_probe,
        sleeper=_no_sleep,
        clock=_fast_clock(),
    )
    second = perception.ensure_watch(
        tmp_path,
        resolver=_always_loct,
        spawner=spawn,
        lock_probe=lock_probe,
        sleeper=_no_sleep,
        clock=_fast_clock(),
    )

    assert first.status == "started"
    assert second.status == "already_running"
    assert len(calls) == 1


def test_fresh_start_reports_started(tmp_path: Path) -> None:
    spawn = _recording_spawner(FakeProc([None]))
    outcome = _ensure(tmp_path, spawner=spawn)
    assert outcome.status == "started"
    assert outcome.pid == 4242
    assert outcome.endpoint is None


def test_bg_fork_returncode_zero_is_started(tmp_path: Path) -> None:
    spawn = _recording_spawner(FakeProc([0]))
    outcome = _ensure(tmp_path, spawner=spawn, transport="stdio")
    assert outcome.status == "started"
    assert outcome.endpoint is None  # stdio transport has no HTTP endpoint


def test_nonzero_failure_is_reported_not_raised(tmp_path: Path) -> None:
    spawn = _recording_spawner(FakeProc([1]))
    outcome = _ensure(tmp_path, spawner=spawn)
    assert outcome.status == "failed"
    assert outcome.returncode == 1


def test_missing_loct_is_unavailable_and_does_not_spawn(tmp_path: Path) -> None:
    def boom(_cmd: Sequence[str]):  # must not be called
        pytest.fail("must not spawn when loct is unavailable")

    outcome = _ensure(tmp_path, resolver=lambda: None, spawner=boom)
    assert outcome.status == "unavailable"
    assert outcome.pid is None


# ---------------------------------------------------------------------------
# Real flock-based single-instance detection
# ---------------------------------------------------------------------------


def test_watcher_running_missing_lock_is_false(tmp_path: Path) -> None:
    assert perception.watcher_running(tmp_path) is False


def test_watcher_running_detects_held_flock(tmp_path: Path) -> None:
    fcntl = pytest.importorskip("fcntl")
    lock = perception.scan_lock_path(tmp_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("{}", encoding="utf-8")

    assert perception.watcher_running(tmp_path) is False

    fd = os.open(str(lock), os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert perception.watcher_running(tmp_path) is True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert perception.watcher_running(tmp_path) is False


# ---------------------------------------------------------------------------
# CLI — best-effort, always exits 0
# ---------------------------------------------------------------------------


def test_cli_ensure_watch_unavailable_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    monkeypatch.setattr(perception, "_resolve_foundation", lambda name: None)
    rc = perception.main(["ensure-watch", "--root", str(tmp_path)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "unavailable"
    assert payload["transport"] == "scan"


def test_cli_status_exits_zero_without_per_root_mcp_endpoint(
    capsys, tmp_path: Path
) -> None:
    rc = perception.main(["status", "--root", str(tmp_path)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False
    assert payload["endpoint"] is None
    assert payload["port"] is None
