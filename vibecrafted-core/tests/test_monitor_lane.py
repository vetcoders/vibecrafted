"""Monitor lane: cursor replay, dedup, Claude stdin, capability table."""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path

import pytest
from vibecrafted_core import message_control, monitor_lane


class _Stdin:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.buf = io.BytesIO()
        self.writes = 0
        self.fail_after = fail_after

    def write(self, data: bytes) -> int:
        self.writes += 1
        if self.fail_after is not None and self.writes > self.fail_after:
            raise OSError("stdin closed")
        return self.buf.write(data)

    def flush(self) -> None:
        return None


def _run(home: Path, run_id: str, *, agent: str = "claude", session: str = "") -> None:
    meta = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "agent": agent,
                "agent_session_id": session,
                "runtime_session_id": "runtime-1",
            }
        ),
        encoding="utf-8",
    )


def _home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    return home


def test_replay_does_not_lose_or_duplicate_across_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _home(monkeypatch, tmp_path)
    _run(home, "run-1")
    first = message_control.send_message(run_id="run-1", text="alpha")
    second = message_control.send_message(run_id="run-1", text="beta")
    texts = {first["message_id"]: "alpha", second["message_id"]: "beta"}
    stub = _Stdin(fail_after=1)
    with monitor_lane.RunFollower("run-1", stdin=stub, pid=os.getpid()) as follower:
        handed = follower.poll()
    injected = [row for row in handed if row.injected]
    missed = [row for row in handed if not row.injected]
    assert len(injected) == 1 and len(missed) == 1
    assert injected[0].level == "live"
    assert missed[0].reason == "stdin_unavailable"
    assert injected[0].message_id != missed[0].message_id
    assert (
        message_control.inspect_message(injected[0].message_id)["delivery_state"]
        == "agent_acknowledged"
    )

    stub_again = _Stdin()
    with monitor_lane.RunFollower(
        "run-1", stdin=stub_again, pid=os.getpid()
    ) as follower:
        replayed = follower.poll()
    assert [row.message_id for row in replayed] == [missed[0].message_id]
    assert injected[0].message_id not in {row.message_id for row in replayed}
    body = json.loads(stub_again.buf.getvalue().decode("utf-8"))
    assert body["type"] == "user"
    assert texts[missed[0].message_id] in body["message"]["content"][0]["text"]
    assert texts[injected[0].message_id] not in body["message"]["content"][0]["text"]

    with monitor_lane.RunFollower("run-1", stdin=_Stdin(), pid=os.getpid()) as follower:
        assert follower.poll() == []


def test_terminal_receipts_are_not_delivered_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _home(monkeypatch, tmp_path)
    _run(home, "run-1")
    injected = message_control.send_message(run_id="run-1", text="already glued")
    message_control.mark_context_injected(injected["message_id"], "nonce-1")
    acked = message_control.send_message(run_id="run-1", text="already claimed")
    message_control.acknowledge_message(acked["message_id"], run_id="run-1")
    stub = _Stdin()
    with monitor_lane.RunFollower("run-1", stdin=stub, pid=os.getpid()) as follower:
        assert follower.poll() == []
    assert stub.writes == 0

    pending = message_control.send_message(run_id="run-1", text="still pending")
    real_inspect = message_control.inspect_message

    def claimed(message_id: str):
        row = real_inspect(message_id)
        if row and row["message_id"] == pending["message_id"]:
            return {**row, "delivery_state": "context_injected"}
        return row

    monkeypatch.setattr(message_control, "inspect_message", claimed)
    stub = _Stdin()
    with monitor_lane.RunFollower("run-1", stdin=stub, pid=os.getpid()) as follower:
        assert follower.poll() == []
    assert stub.writes == 0
    assert real_inspect(pending["message_id"])["delivery_state"] == "inbox_pending"


def test_claude_stdin_stub_and_absent_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _home(monkeypatch, tmp_path)
    _run(home, "run-1")
    item = message_control.send_message(run_id="run-1", text="steer now")
    nonce = monitor_lane.run_delivery_nonce("run-1")
    stub = _Stdin()
    with monitor_lane.RunFollower("run-1", stdin=stub, pid=os.getpid()) as follower:
        delivered = follower.poll()
    assert delivered[0].level == "live"
    assert delivered[0].reason == "stdin_stream_json"
    payload = json.loads(stub.buf.getvalue().decode("utf-8"))
    assert payload["type"] == "user"
    assert payload["message"]["role"] == "user"
    assert payload["parent_tool_use_id"] is None
    text = payload["message"]["content"][0]["text"]
    assert text.startswith(f"[vibecrafted-bus nonce={nonce} run=run-1 message=")
    assert "steer now" in text
    assert item["message_id"] in text

    _run(home, "run-2")
    message_control.send_message(run_id="run-2", text="no process")
    dead = _Stdin()
    with monitor_lane.RunFollower("run-2", stdin=dead, pid=-1) as follower:
        dropped = follower.poll()
    assert dropped[0].level == "injected-on-call"
    assert dropped[0].reason == "process_absent"
    assert dropped[0].injected is False
    assert dead.writes == 0
    assert message_control.receive_messages(run_id="run-2")[0]["text"] == "no process"

    level, injected, reason = monitor_lane.push_claude_stdin(None, None, b"{}\n")
    assert (level, injected, reason) == ("injected-on-call", False, "process_absent")


def test_capability_table_is_explicit_per_provider() -> None:
    rows = {row.provider: row for row in monitor_lane.capability_rows()}
    assert set(rows) == {
        "claude",
        "codex",
        "agy",
        "grok",
        "junie",
        "kimi",
        "cursor",
        "gemini",
    }
    for row in rows.values():
        assert row.declared_level in monitor_lane.LEVELS
        assert row.when_unavailable in monitor_lane.LEVELS
        assert row.source
    assert rows["claude"].declared_level == "live"
    assert rows["claude"].monitor == "stdin-stream-json"
    assert (
        monitor_lane.effective_level("claude", process_alive=True, stdin_open=True)
        == "live"
    )
    assert (
        monitor_lane.effective_level("claude", process_alive=False, stdin_open=True)
        == "injected-on-call"
    )
    assert rows["codex"].declared_level == "live"
    assert rows["codex"].monitor == "native-queue"
    assert monitor_lane.effective_level("codex", native_session=True) == "live"
    assert monitor_lane.effective_level("codex", native_session=False) == (
        "injected-on-call"
    )
    for name in ("agy", "grok", "junie", "kimi", "cursor", "gemini"):
        assert rows[name].declared_level == "injected-on-call"
        assert rows[name].monitor is None
        assert monitor_lane.effective_level(name) == "injected-on-call"
    assert monitor_lane.effective_level("not-a-cli") == "checkpoint-poll"


def test_codex_queue_is_not_injected_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _home(monkeypatch, tmp_path)
    _run(home, "run-1", agent="codex", session="")
    message_control.send_message(run_id="run-1", text="early")
    stub = _Stdin()
    with monitor_lane.RunFollower("run-1", stdin=stub, pid=os.getpid()) as follower:
        delivered = follower.poll()
    assert delivered[0].level == "injected-on-call"
    assert delivered[0].injected is False
    assert delivered[0].reason == "no_native_thread"
    assert stub.writes == 0
    assert message_control.receive_messages(run_id="run-1")[0]["text"] == "early"


def test_second_follower_is_refused_until_the_lease_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _home(monkeypatch, tmp_path)
    _run(home, "run-1")
    holder = monitor_lane.RunFollower("run-1", stdin=_Stdin(), pid=os.getpid())
    try:
        with pytest.raises(monitor_lane.MonitorLaneBusy):
            monitor_lane.RunFollower("run-1", stdin=_Stdin(), pid=os.getpid())
    finally:
        holder.close()
    with monitor_lane.RunFollower("run-1", stdin=_Stdin(), pid=os.getpid()):
        pass


def test_unreadable_cursor_is_not_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _home(monkeypatch, tmp_path)
    _run(home, "run-1")
    cursor = (
        home
        / "control_plane"
        / "runtime_runs"
        / "run-1"
        / "monitor-lane"
        / "lease.json"
    )
    cursor.parent.mkdir(parents=True)
    cursor.write_text("{", encoding="utf-8")
    with pytest.raises(monitor_lane.MonitorLaneError, match="unreadable"):
        monitor_lane.RunFollower("run-1", stdin=_Stdin(), pid=os.getpid())
    assert cursor.read_text(encoding="utf-8") == "{"


def test_ack_failure_does_not_reinject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _home(monkeypatch, tmp_path)
    _run(home, "run-1")
    item = message_control.send_message(run_id="run-1", text="once")
    real_ack = message_control.acknowledge_message
    calls = {"n": 0}

    def flaky(message_id: str, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise message_control.MessageControlError("ack_failed")
        return real_ack(message_id, **kwargs)

    monkeypatch.setattr(message_control, "acknowledge_message", flaky)
    first = _Stdin()
    with monitor_lane.RunFollower("run-1", stdin=first, pid=os.getpid()) as follower:
        handed = follower.poll()
    assert handed[0].injected is True
    assert calls["n"] == 1
    assert (
        message_control.inspect_message(item["message_id"])["delivery_state"]
        == "inbox_pending"
    )

    second = _Stdin()
    with monitor_lane.RunFollower("run-1", stdin=second, pid=os.getpid()) as follower:
        assert follower.poll() == []
    assert second.writes == 0
    assert calls["n"] == 2
    assert (
        message_control.inspect_message(item["message_id"])["delivery_state"]
        == "agent_acknowledged"
    )


def test_run_nonce_is_stable_and_store_shaped() -> None:
    assert monitor_lane.run_delivery_nonce("run-1") == monitor_lane.run_delivery_nonce(
        "run-1"
    )
    assert monitor_lane.run_delivery_nonce("run-1") != monitor_lane.run_delivery_nonce(
        "run-2"
    )
    nonce = monitor_lane.run_delivery_nonce("run-1")
    assert re.fullmatch(r"vcbus-[0-9a-f]{24}", nonce)
    with pytest.raises(monitor_lane.MonitorLaneError):
        monitor_lane.run_delivery_nonce(" pending")
