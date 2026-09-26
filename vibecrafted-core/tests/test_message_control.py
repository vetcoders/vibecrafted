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


def test_claude_inbox_and_transient_failures_remain_truthful(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "claude-run", agent="claude", session="claude-session")
    _run(home, "codex-run")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(
        message_control, "_resolve_agent_command", lambda _agent, argv, _env: argv
    )
    queued = message_control.send_message(session="claude-session", text="one")
    assert queued["delivery_state"] == "inbox_pending"
    assert queued["agent_ack_state"] == "unobserved"
    assert message_control.pending_messages(run_id="claude-run") == [queued]
    acknowledged = message_control.acknowledge_message(
        queued["message_id"], session="claude-session"
    )
    assert acknowledged["delivery_state"] == "agent_acknowledged"
    assert acknowledged["agent_ack_state"] == "claimed_by_recipient"
    assert message_control.pending_messages(run_id="claude-run") == []

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


def test_session_selector_fails_closed_on_collision_and_mismatch(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "one", agent="claude", session="shared")
    _run(home, "two", agent="claude", session="shared")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    with pytest.raises(message_control.MessageControlError, match="session_ambiguous"):
        message_control.send_message(session="shared", text="hello")
    with pytest.raises(message_control.MessageControlError, match="invalid_session_id"):
        message_control.send_message(session="pending", text="hello")
    with pytest.raises(
        message_control.MessageControlError, match="session_run_mismatch"
    ):
        message_control.send_message(run_id="one", session="foreign", text="hello")
    exact = message_control.send_message(run_id="one", text="hello")
    assert exact["delivery_state"] == "inbox_pending"
    with pytest.raises(
        message_control.MessageControlError, match="message_target_mismatch"
    ):
        message_control.acknowledge_message(exact["message_id"], run_id="two")


def test_runtime_session_selector_reaches_inbox_without_native_session(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "run-1", agent="claude", session="")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    item = message_control.send_message(session="runtime-1", text="new direction")
    assert item["run_id"] == "run-1"
    assert item["provider_session_id"] == ""
    assert message_control.pending_messages(session="runtime-1") == [item]
    meta = home / "control_plane/runtime_runs/run-1/meta.json"
    payload = json.loads(meta.read_text())
    payload["session_id"] = "report-session"
    meta.write_text(json.dumps(payload))
    assert (
        message_control.resolve_message_run(run_id="run-1", session="report-session")
        == "run-1"
    )


def test_codex_before_thread_identity_uses_inbox(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    _run(home, "run-1", agent="codex", session="")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    item = message_control.send_message(run_id="run-1", text="early steering")
    assert item["provider"] == "codex"
    assert item["delivery_state"] == "inbox_pending"
    assert message_control.pending_messages(run_id="run-1") == [item]


def test_legacy_session_id_does_not_impersonate_runtime_session(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "run-1", agent="claude", session="native-1")
    meta = home / "control_plane/runtime_runs/run-1/meta.json"
    payload = json.loads(meta.read_text())
    payload.pop("runtime_session_id")
    payload["session_id"] = "native-1"
    meta.write_text(json.dumps(payload))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    item = message_control.send_message(session="native-1", text="hello")
    assert item["delivery_state"] == "inbox_pending"


def test_cli_session_send_receive_ack_round_trip(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from vibecrafted_core import cli

    home = tmp_path / "home"
    _run(home, "run-1", agent="claude", session="claude-native")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    body = tmp_path / "body.txt"
    body.write_text("steer this run", encoding="utf-8")
    assert (
        cli.main(
            ["message", "--session", "claude-native", "--file", str(body), "--json"]
        )
        == 0
    )
    item = json.loads(capsys.readouterr().out)
    assert item["delivery_state"] == "inbox_pending"
    assert cli.main(["message", "--run-id", "run-1", "--receive"]) == 0
    inbox = json.loads(capsys.readouterr().out)
    assert [row["message_id"] for row in inbox] == [item["message_id"]]
    assert cli.main(["message", "--run-id", "run-1", "--ack", item["message_id"]]) == 0
    ack = json.loads(capsys.readouterr().out)
    assert ack["agent_ack_state"] == "claimed_by_recipient"
    assert cli.main(["message", "--run-id", "run-1", "--receive"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_mark_context_injected_leaves_receive(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from vibecrafted_core import cli

    home = tmp_path / "home"
    _run(home, "run-1", agent="claude", session="claude-native")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    body = tmp_path / "body.txt"
    body.write_text("during the turn", encoding="utf-8")
    assert (
        cli.main(["message", "--run-id", "run-1", "--file", str(body), "--json"]) == 0
    )
    item = json.loads(capsys.readouterr().out)
    assert (
        cli.main(
            [
                "message",
                "--mark-context-injected",
                item["message_id"],
                "--nonce",
                "nonce-1",
                "--json",
            ]
        )
        == 0
    )
    marked = json.loads(capsys.readouterr().out)
    assert marked["delivery_state"] == "context_injected"
    assert marked["context_injected_nonce"] == "nonce-1"
    assert cli.main(["message", "--run-id", "run-1", "--receive"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert (
        cli.main(
            [
                "message",
                "--mark-context-injected",
                item["message_id"],
                "--nonce",
                "other-nonce",
            ]
        )
        == 2
    )


def test_same_key_body_different_run_fails_before_runner_including_retry(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "run-1", session="thread-a")
    _run(home, "run-2", session="thread-b")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(
        message_control, "_resolve_agent_command", lambda _agent, argv, _env: argv
    )
    seen: list[list[str]] = []

    def runner(argv, **_kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    first = message_control.send_message(
        run_id="run-1", text="one", idempotency_key="shared", runner=runner
    )
    assert first["run_id"] == "run-1"
    assert first["delivery_state"] == "provider_accepted"
    assert len(seen) == 1

    with pytest.raises(
        message_control.MessageControlError, match="idempotency_key_run_mismatch"
    ):
        message_control.send_message(
            run_id="run-2", text="one", idempotency_key="shared", runner=runner
        )
    with pytest.raises(
        message_control.MessageControlError, match="idempotency_key_run_mismatch"
    ):
        message_control.send_message(
            run_id="run-2",
            text="one",
            idempotency_key="shared",
            retry=True,
            runner=runner,
        )

    assert len(seen) == 1
    assert seen[0][3] == "thread-a"
    replay = message_control.send_message(
        run_id="run-1", text="one", idempotency_key="shared", runner=runner
    )
    assert replay["idempotent_replay"] is True
    assert replay["message_id"] == first["message_id"]
    assert len(seen) == 1


def test_retry_does_not_resubmit_provider_accepted(monkeypatch, tmp_path: Path) -> None:
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
        run_id="run-1", text="one", idempotency_key="accepted", runner=runner
    )
    retried = message_control.send_message(
        run_id="run-1",
        text="one",
        idempotency_key="accepted",
        retry=True,
        runner=runner,
    )
    assert calls == 1
    assert first["delivery_state"] == "provider_accepted"
    assert retried["delivery_state"] == "provider_accepted"
    assert retried["idempotent_replay"] is True
    assert retried["message_id"] == first["message_id"]


def test_retry_resubmits_unresolved_timeout_on_same_run_only(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "run-1", session="thread-a")
    _run(home, "run-2", session="thread-b")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(
        message_control, "_resolve_agent_command", lambda _agent, argv, _env: argv
    )
    calls = 0

    def runner(argv, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=30)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    timed_out = message_control.send_message(
        run_id="run-1", text="one", idempotency_key="timeout", runner=runner
    )
    assert timed_out["delivery_state"] == "retryable_failure"
    assert timed_out["failure"]["reason"] == "provider_queue_timeout"

    recovered = message_control.send_message(
        run_id="run-1",
        text="one",
        idempotency_key="timeout",
        retry=True,
        runner=runner,
    )
    assert calls == 2
    assert recovered["delivery_state"] == "provider_accepted"
    assert recovered["message_id"] == timed_out["message_id"]

    with pytest.raises(
        message_control.MessageControlError, match="idempotency_key_run_mismatch"
    ):
        message_control.send_message(
            run_id="run-2",
            text="one",
            idempotency_key="timeout",
            retry=True,
            runner=runner,
        )
    assert calls == 2


def test_timeout_reason_is_typed_and_omits_private_text(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "run-1")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(
        message_control, "_resolve_agent_command", lambda _agent, argv, _env: argv
    )
    secret = "SECRET_MARKER_DO_NOT_PERSIST"

    def runner(argv, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=30, output=secret)

    result = message_control.send_message(run_id="run-1", text=secret, runner=runner)
    diagnostics = json.dumps(
        {
            "failure": result.get("failure"),
            "attempts": result.get("attempts"),
            "delivery_state": result.get("delivery_state"),
        },
        default=str,
    )
    assert result["delivery_state"] == "retryable_failure"
    assert result["failure"]["reason"] == "provider_queue_timeout"
    assert "TimeoutExpired" not in diagnostics
    assert secret not in diagnostics
    assert secret not in json.dumps(result["attempts"], default=str)


_HISTORICAL_DELIVERY_STATES = {
    "recorded",
    "inbox_pending",
    "provider_accepted",
    "agent_acknowledged",
    "retryable_failure",
    "permanent_failure",
}


def _refuse_provider_command(*_args, **_kwargs):
    raise AssertionError("inbox send must not call a provider command")


def test_public_message_api_keeps_historical_states_and_contract_docs() -> None:
    public = (
        message_control.send_message,
        message_control.receive_messages,
        message_control.acknowledge_message,
        message_control.inspect_message,
        message_control.resolve_message_run,
    )
    for function in public:
        assert callable(function)
        assert (function.__doc__ or "").strip()
    states = message_control.DELIVERY_STATES
    assert _HISTORICAL_DELIVERY_STATES <= set(states)
    assert states["context_injected"] == (
        "attached to a tool response; not proof the recipient read it"
    )
    assert "not an agent acknowledgement" in states["provider_accepted"]
    assert "not proof the requested action ran" in states["agent_acknowledged"]
    assert "inbox_pending" in (message_control.receive_messages.__doc__ or "")
    assert "claimed_by_recipient" in (message_control.acknowledge_message.__doc__ or "")


def test_gemini_send_creates_inbox_pending_without_native_session(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "fixture-gemini", agent="gemini", session="")
    meta_path = home / "control_plane/runtime_runs/fixture-gemini/meta.json"
    payload = json.loads(meta_path.read_text())
    payload.pop("agent_session_id", None)
    payload.pop("session_id", None)
    meta_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))

    queued = message_control.send_message(
        run_id="fixture-gemini",
        text="eighth passenger",
        runner=_refuse_provider_command,
    )

    assert queued["provider"] == "gemini"
    assert queued["delivery_state"] == "inbox_pending"
    assert queued["provider_session_id"] == ""
    assert queued["agent_ack_state"] == "unobserved"
    assert message_control.receive_messages(run_id="fixture-gemini") == [queued]
    assert message_control.pending_messages(run_id="fixture-gemini") == [queued]
    receipt = home / "control_plane" / "messages" / f"{queued['message_id']}.json"
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["schema"] == message_control.MESSAGE_SCHEMA
    assert saved["delivery_state"] == "inbox_pending"
    assert saved["text"] == "eighth passenger"


def test_gemini_with_native_session_stays_in_inbox(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    _run(home, "fixture-gemini", agent="gemini", session="gemini-native")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    queued = message_control.send_message(
        run_id="fixture-gemini",
        text="still inbox",
        runner=_refuse_provider_command,
    )
    assert queued["provider"] == "gemini"
    assert queued["provider_session_id"] == "gemini-native"
    assert queued["delivery_state"] == "inbox_pending"


def test_gemini_placeholder_session_is_not_a_queue_attempt(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "fixture-gemini", agent="gemini", session="pending")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    queued = message_control.send_message(
        run_id="fixture-gemini",
        text="placeholder",
        runner=_refuse_provider_command,
    )
    assert queued["delivery_state"] == "inbox_pending"
    assert queued["provider_session_id"] == ""


def test_unknown_provider_stays_unsupported(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    _run(home, "run-1", agent="mystery", session="native-1")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    with pytest.raises(
        message_control.MessageControlError,
        match="provider_steering_unsupported:mystery",
    ):
        message_control.send_message(run_id="run-1", text="no")


def test_context_injected_records_nonce_and_ack_still_claims(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "fixture-gemini", agent="gemini", session="")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    queued = message_control.send_message(
        run_id="fixture-gemini",
        text="inject me",
        idempotency_key="inject",
        runner=_refuse_provider_command,
    )
    injected = message_control.mark_context_injected(queued["message_id"], "nonce-1")
    assert injected["delivery_state"] == "context_injected"
    assert injected["context_injected_nonce"] == "nonce-1"
    assert injected["context_injected_at"]
    assert injected["agent_ack_state"] == "unobserved"
    repeated = message_control.mark_context_injected(queued["message_id"], "nonce-1")
    assert repeated["context_injected_at"] == injected["context_injected_at"]
    assert repeated["updated_at"] == injected["updated_at"]
    assert message_control.receive_messages(run_id="fixture-gemini") == []
    assert message_control.pending_messages(run_id="fixture-gemini") == []
    inspected = message_control.inspect_message(queued["message_id"])
    assert inspected is not None
    assert inspected["delivery_state"] == "context_injected"
    with pytest.raises(
        message_control.MessageControlError,
        match="context_injection_nonce_mismatch",
    ):
        message_control.mark_context_injected(queued["message_id"], "nonce-2")
    replay = message_control.send_message(
        run_id="fixture-gemini",
        text="inject me",
        idempotency_key="inject",
        runner=_refuse_provider_command,
    )
    assert replay["idempotent_replay"] is True
    assert replay["delivery_state"] == "context_injected"
    assert replay["message_id"] == queued["message_id"]
    acknowledged = message_control.acknowledge_message(
        queued["message_id"], run_id="fixture-gemini"
    )
    assert acknowledged["delivery_state"] == "agent_acknowledged"
    assert acknowledged["agent_ack_state"] == "claimed_by_recipient"
    assert acknowledged["context_injected_nonce"] == "nonce-1"
    again = message_control.acknowledge_message(
        queued["message_id"], run_id="fixture-gemini"
    )
    assert again["delivery_state"] == "agent_acknowledged"
    assert again["acknowledged_at"] == acknowledged["acknowledged_at"]
    with pytest.raises(
        message_control.MessageControlError, match="message_not_awaiting_injection"
    ):
        message_control.mark_context_injected(queued["message_id"], "nonce-1")


def test_context_injection_rejects_bad_nonce_and_non_inbox(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _run(home, "fixture-gemini", agent="gemini", session="")
    _run(home, "run-1", session="thread-1")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setattr(
        message_control, "_resolve_agent_command", lambda _agent, argv, _env: argv
    )
    queued = message_control.send_message(
        run_id="fixture-gemini", text="one", runner=_refuse_provider_command
    )
    with pytest.raises(
        message_control.MessageControlError, match="context_injection_nonce_required"
    ):
        message_control.mark_context_injected(queued["message_id"], "  ")
    with pytest.raises(
        message_control.MessageControlError, match="invalid_context_injection_nonce"
    ):
        message_control.mark_context_injected(queued["message_id"], "../nonce")
    with pytest.raises(message_control.MessageControlError, match="message_not_found"):
        message_control.mark_context_injected("msg-missing", "nonce-1")

    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    accepted = message_control.send_message(
        run_id="run-1", text="queued", runner=runner
    )
    assert accepted["delivery_state"] == "provider_accepted"
    with pytest.raises(
        message_control.MessageControlError, match="message_not_awaiting_injection"
    ):
        message_control.mark_context_injected(accepted["message_id"], "nonce-1")


def test_cli_gemini_file_send_writes_inbox_receipt(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from vibecrafted_core import cli

    home = tmp_path / "home"
    _run(home, "fixture-gemini", agent="gemini", session="")
    meta_path = home / "control_plane/runtime_runs/fixture-gemini/meta.json"
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload.pop("agent_session_id", None)
    meta_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    note = tmp_path / "note.txt"
    note.write_text("gemini inbox note\n", encoding="utf-8")
    assert (
        cli.main(
            ["message", "--run-id", "fixture-gemini", "--file", str(note), "--json"]
        )
        == 0
    )
    item = json.loads(capsys.readouterr().out)
    assert item["provider"] == "gemini"
    assert item["delivery_state"] == "inbox_pending"
    receipt = home / "control_plane" / "messages" / f"{item['message_id']}.json"
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["text"] == "gemini inbox note\n"
    assert saved["delivery_state"] == "inbox_pending"
