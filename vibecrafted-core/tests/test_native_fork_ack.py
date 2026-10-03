"""Provider fixtures falsify RPC correlation; real-native acceptance is separate."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from vibecrafted_core.continuity import native_fork

PARENT = "10000000-0000-4000-8000-000000000001"
CHILD = "20000000-0000-4000-8000-000000000002"


def provider(tmp_path: Path) -> Path:
    path = tmp_path / "codex"
    path.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
for line in sys.stdin:
    message = json.loads(line)
    if 'id' not in message: continue
    mode = os.environ.get('ACK_MODE', '')
    request_id = message['id']
    method = message['method']
    if method == 'thread/fork':
        assert message['params'] == {'threadId': os.environ['PARENT'], 'cwd': os.getcwd(), 'model': 'exact-model', 'sandbox': 'workspace-write', 'approvalPolicy': 'on-request'}
    if method == 'initialize': result = {}
    else:
        if mode == 'absent': continue
        if mode == 'wrong-request': request_id = 'someone-elses-fork'
        child = os.environ['CHILD']
        if mode == 'parent-reused': child = os.environ['PARENT']
        if mode == 'read-mismatch' and method == 'thread/read': child = '30000000-0000-4000-8000-000000000003'
        result = {'thread': {'id': child, 'forkedFromId': os.environ['PARENT'] if mode != 'wrong-parent' else 'foreign', 'cwd': os.getcwd() if mode != 'wrong-root' else '/'}}
    print(json.dumps({'id': request_id, 'result': result}), flush=True)
"""
    )
    path.chmod(0o700)
    return path


@pytest.mark.parametrize(
    "mode",
    [
        "",
        "absent",
        "wrong-request",
        "parent-reused",
        "wrong-parent",
        "wrong-root",
        "read-mismatch",
    ],
)
def test_native_ack_requires_exact_response_and_independent_lineage(
    tmp_path, monkeypatch, mode
):
    monkeypatch.setattr(native_fork, "ACK_TIMEOUT_SECONDS", 2.0)
    env = {**os.environ, "PARENT": PARENT, "CHILD": CHILD, "ACK_MODE": mode}
    env.pop("CODEX_REMOTE", None)
    args = {
        "executable": str(provider(tmp_path)),
        "env": env,
        "root": str(tmp_path),
        "parent": PARENT,
        "run_id": "fork-owned",
        "model": "exact-model",
        "permissions": "auto",
    }
    if mode:
        with pytest.raises(native_fork.NativeForkError):
            native_fork.confirm_codex_native_fork(**args)
    else:
        result = native_fork.confirm_codex_native_fork(**args)
        assert result["provider_session_id"] == CHILD
        assert result["native_identity_status"] == "confirmed"
        evidence = result["native_identity_evidence"]
        assert evidence["forked_from_id"] == PARENT
        assert evidence["request_id"] == "fork-owned:fork"
        assert evidence["read_request_id"] == "fork-owned:read-child"
        assert evidence["executable"] == args["executable"]


def test_unsupported_remote_refuses_before_provider_spawn(tmp_path):
    with pytest.raises(native_fork.NativeForkError, match="unix"):
        native_fork.confirm_codex_native_fork(
            executable="must-not-start",
            env={"CODEX_REMOTE": "wss://provider.invalid"},
            root=str(tmp_path),
            parent=PARENT,
            run_id="fork-owned",
            model="",
            permissions="bypass",
        )


def test_unix_remote_ack_uses_selected_endpoint(tmp_path):
    import json
    import tempfile
    import threading

    from websockets.sync.server import unix_serve

    def respond(connection):
        for raw in connection:
            message = json.loads(raw)
            if "id" not in message:
                continue
            if message["method"] == "thread/fork":
                assert message["params"] == {
                    "threadId": PARENT,
                    "cwd": str(tmp_path),
                    "model": "exact-model",
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                }
            result = (
                {}
                if message["method"] == "initialize"
                else {
                    "thread": {
                        "id": CHILD,
                        "forkedFromId": PARENT,
                        "cwd": str(tmp_path),
                    }
                }
            )
            connection.send(json.dumps({"id": message["id"], "result": result}))

    # Darwin sun_path is short: retain temp ownership while avoiding the long
    # pytest case directory as the physical socket name.
    with tempfile.TemporaryDirectory(
        prefix="fork-ws-", dir=os.environ.get("TMPDIR")
    ) as directory:
        endpoint = str(Path(directory) / "rpc.sock")
        with unix_serve(respond, endpoint) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                result = native_fork.confirm_codex_native_fork(
                    executable="selected-provider-must-not-be-replaced",
                    env={"CODEX_REMOTE": "unix://" + endpoint},
                    root=str(tmp_path),
                    parent=PARENT,
                    run_id="fork-remote",
                    model="exact-model",
                    permissions="read-only",
                )
                assert result["provider_session_id"] == CHILD
                assert (
                    result["native_identity_evidence"]["transport"]
                    == "unix://" + endpoint
                )
            finally:
                server.shutdown()
                thread.join(timeout=3)
            assert not thread.is_alive()
