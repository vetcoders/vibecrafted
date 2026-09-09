from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core import message_control


def _run(
    home: Path, run_id: str, *, agent: str = "codex", session: str = "thread-1"
) -> None:
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


def test_codex_message_is_recorded_then_queued_without_resume_or_spawn(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "run-1", session="codex-thread-42")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(tmp_path / "bin"))
    monkeypatch.setattr(
        message_control, "_resolve_agent_command", lambda _agent, argv, _env: argv
    )
    seen: list[str] = []

    def runner(argv, **_kwargs):
        seen.extend(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="queued", stderr="")

    result = message_control.send_message(
        run_id="run-1", text="private steering", runner=runner
    )

    assert result["delivery_state"] == "provider_accepted"
    assert result["agent_ack_state"] == "unobserved"
    assert seen[-6:] == [
        "codex",
        "queue",
        "--thread",
        "codex-thread-42",
        "--message",
        "private steering",
    ]
    assert "resume" not in seen and "exec" not in seen
    saved = message_control.inspect_message(result["message_id"])
    assert saved is not None and saved["text"] == "private steering"


def test_duplicate_idempotency_does_not_submit_again_and_crash_state_is_inspectable(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "run-1")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(
        message_control, "_resolve_agent_command", lambda _agent, argv, _env: argv
    )
    calls = 0

    def runner(argv, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    first = message_control.send_message(
        run_id="run-1", text="one", idempotency_key="same", runner=runner
    )
    duplicate = message_control.send_message(
        run_id="run-1", text="one", idempotency_key="same", runner=runner
    )
    assert calls == 1
    assert duplicate["message_id"] == first["message_id"]
    assert duplicate["idempotent_replay"] is True


def test_unsupported_and_transient_failures_remain_truthful(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "claude-run", agent="claude")
    _run(home, "codex-run")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(
        message_control, "_resolve_agent_command", lambda _agent, argv, _env: argv
    )
    unsupported = message_control.send_message(run_id="claude-run", text="one")
    assert unsupported["delivery_state"] == "permanent_failure"
    assert "unsupported" in unsupported["failure"]["reason"]

    def down(*_args, **_kwargs):
        raise OSError("temporary provider socket")

    retryable = message_control.send_message(
        run_id="codex-run", text="one", runner=down
    )
    assert retryable["delivery_state"] == "retryable_failure"
    assert retryable["agent_ack_state"] == "unobserved"


def test_missing_or_runtime_provider_identity_is_refused(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "bad", session="runtime-1")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    with pytest.raises(
        message_control.MessageControlError, match="provider_session_is_runtime_session"
    ):
        message_control.send_message(run_id="bad", text="one")


def test_message_inspection_refuses_path_escape(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    with pytest.raises(message_control.MessageControlError, match="invalid_message_id"):
        message_control.inspect_message("../../outside")
