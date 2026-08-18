from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest
from vibecrafted_acp.bridge import RuntimeBridge
from vibecrafted_acp.policy import allowed_once, classify_hard_stop
from vibecrafted_acp.server import PROTOCOL_VERSION, SCHEMA_VERSION, ACPServer
from vibecrafted_core import cli


def _messages(output: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.getvalue().splitlines() if line]


def _request(
    server: ACPServer,
    *,
    request_id: int,
    method: str,
    params: dict[str, object],
) -> None:
    server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
    )


def _has_stop_reason(
    message: dict[str, object], request_id: int, stop_reason: str
) -> bool:
    result = message.get("result")
    return (
        message.get("id") == request_id
        and isinstance(result, dict)
        and result.get("stopReason") == stop_reason
    )


def test_mvp_fixture_handshake_prompt_update_and_cancel(tmp_path: Path) -> None:
    output = io.StringIO()
    bridge = RuntimeBridge(dry_run=True)
    server = ACPServer(bridge=bridge, output=output)

    _request(
        server,
        request_id=0,
        method="initialize",
        params={"protocolVersion": 1, "clientCapabilities": {}},
    )
    _request(
        server,
        request_id=1,
        method="session/new",
        params={"cwd": str(tmp_path), "mcpServers": []},
    )
    created = _messages(output)[1]["result"]
    assert isinstance(created, dict)
    session_id = str(created["sessionId"])

    _request(
        server,
        request_id=2,
        method="session/prompt",
        params={
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "Implement the fixture."}],
        },
    )
    server.wait()
    server.handle_message(
        {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": session_id},
        }
    )

    messages = _messages(output)
    initialized = messages[0]["result"]
    assert isinstance(initialized, dict)
    assert initialized["protocolVersion"] == PROTOCOL_VERSION
    assert initialized["_meta"] == {"vibecrafted": {"schema": SCHEMA_VERSION}}
    assert session_id.startswith("impl-")
    assert any(
        message.get("method") == "session/update"
        and message["params"]["sessionId"] == session_id  # type: ignore[index]
        and message["params"]["update"]["sessionUpdate"] == "agent_message_chunk"  # type: ignore[index]
        for message in messages
    )
    assert any(_has_stop_reason(message, 2, "end_turn") for message in messages)
    assert bridge._dry_runs[session_id]["cancelled"] is True


@pytest.mark.parametrize(
    "sample",
    [
        "git push",
        "git push origin HEAD",
        "git push -u origin feat/mcp-sessions-continuity",
    ],
)
def test_policy_allows_non_destructive_feature_branch_push(sample: str) -> None:
    assert classify_hard_stop({"command": sample}) is None


@pytest.mark.parametrize(
    "sample",
    [
        "git push origin main",
        "gh pr merge 42",
        "npm publish",
        "fly deploy",
    ],
)
def test_policy_hard_stops_fail_closed(sample: str) -> None:
    assert classify_hard_stop({"command": sample}) is not None
    assert allowed_once(None) is False
    assert allowed_once({"outcome": {"outcome": "cancelled"}}) is False
    assert (
        allowed_once(
            {
                "outcome": {
                    "outcome": "selected",
                    "optionId": "allow-always",
                }
            }
        )
        is False
    )
    assert (
        allowed_once({"outcome": {"outcome": "selected", "optionId": "allow-once"}})
        is True
    )


def test_prompt_hard_stop_requests_permission_and_denies_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_ACP_PERMISSION_TIMEOUT", "1")
    output = io.StringIO()
    bridge = RuntimeBridge(dry_run=True)
    server = ACPServer(bridge=bridge, output=output)
    _request(server, request_id=0, method="initialize", params={})
    _request(
        server,
        request_id=1,
        method="session/new",
        params={"cwd": str(tmp_path), "mcpServers": []},
    )
    created = _messages(output)[1]["result"]
    assert isinstance(created, dict)
    session_id = str(created["sessionId"])
    _request(
        server,
        request_id=2,
        method="session/prompt",
        params={
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "Please git push origin main"}],
        },
    )

    deadline = time.monotonic() + 1
    permission: dict[str, object] | None = None
    while time.monotonic() < deadline:
        permission = next(
            (
                message
                for message in _messages(output)
                if message.get("method") == "session/request_permission"
            ),
            None,
        )
        if permission is not None:
            break
        time.sleep(0.001)
    assert permission is not None
    server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": permission["id"],
            "result": {"outcome": {"outcome": "selected", "optionId": "reject-once"}},
        }
    )
    server.wait()

    messages = _messages(output)
    assert any(_has_stop_reason(message, 2, "refusal") for message in messages)
    assert session_id not in bridge._dry_runs


def test_cancel_interrupts_pending_permission(tmp_path: Path) -> None:
    output = io.StringIO()
    bridge = RuntimeBridge(dry_run=True)
    server = ACPServer(bridge=bridge, output=output)
    _request(server, request_id=0, method="initialize", params={})
    _request(
        server,
        request_id=1,
        method="session/new",
        params={"cwd": str(tmp_path), "mcpServers": []},
    )
    created = _messages(output)[1]["result"]
    assert isinstance(created, dict)
    session_id = str(created["sessionId"])
    _request(
        server,
        request_id=2,
        method="session/prompt",
        params={
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "Please npm publish"}],
        },
    )
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and not any(
        message.get("method") == "session/request_permission"
        for message in _messages(output)
    ):
        time.sleep(0.001)
    server.handle_message(
        {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": session_id},
        }
    )
    server.wait()

    assert any(
        _has_stop_reason(message, 2, "cancelled") for message in _messages(output)
    )
    assert session_id not in bridge._dry_runs


def test_allow_once_records_override_before_launch(tmp_path: Path) -> None:
    output = io.StringIO()
    bridge = RuntimeBridge(dry_run=True)
    server = ACPServer(bridge=bridge, output=output)
    _request(server, request_id=0, method="initialize", params={})
    _request(
        server,
        request_id=1,
        method="session/new",
        params={"cwd": str(tmp_path), "mcpServers": []},
    )
    created = _messages(output)[1]["result"]
    assert isinstance(created, dict)
    session_id = str(created["sessionId"])
    _request(
        server,
        request_id=2,
        method="session/prompt",
        params={
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "Please fly deploy"}],
        },
    )
    deadline = time.monotonic() + 1
    permission: dict[str, object] | None = None
    while time.monotonic() < deadline:
        permission = next(
            (
                message
                for message in _messages(output)
                if message.get("method") == "session/request_permission"
            ),
            None,
        )
        if permission is not None:
            break
        time.sleep(0.001)
    assert permission is not None
    server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": permission["id"],
            "result": {"outcome": {"outcome": "selected", "optionId": "allow-once"}},
        }
    )
    server.wait()

    assert bridge._dry_audit_events == [
        {
            "kind": "hard_stop_override",
            "run_id": session_id,
            "message": "ACP hard-stop allowed once",
            "payload": {
                "category": "deploy",
                "evidence": "fly deploy",
                "approval": "allow_once",
                "source": "acp",
            },
        }
    ]
    assert session_id in bridge._dry_runs


def test_vibecrafted_acp_cli_dry_run_help(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VIBECRAFTED_ACP_DRY_RUN", "1")
    with pytest.raises(SystemExit) as raised:
        cli.main(["acp", "--help"])
    assert raised.value.code == 0
    assert "ACP v1 stdio adapter" in capsys.readouterr().out
