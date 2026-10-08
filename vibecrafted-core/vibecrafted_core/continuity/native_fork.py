"""Correlated Codex app-server fork acknowledgement, consumed by spawn.

No session inventory or identity store: request IDs belong to one launch and
native identity comes only from its fork response plus independent thread/read.
The local adapter uses the selected CLI's stdio server; an explicit CODEX_REMOTE
Unix endpoint stays on that endpoint. Other remote transports refuse.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Self
from uuid import UUID

from websockets.exceptions import WebSocketException
from websockets.sync.client import ClientConnection, unix_connect

ACK_TIMEOUT_SECONDS = 20.0
MAX_ACK_BYTES = 16 * 1024 * 1024


class NativeForkError(ValueError):
    """Native fork is unconfirmed; never substitute a requested identity."""


class CodexAppServer:
    """One bounded, launch-owned RPC connection; never launches a daemon."""

    def __init__(self, executable: str, env: dict[str, str], root: str):
        self.remote = env.get("CODEX_REMOTE", "")
        self.process: subprocess.Popen[bytes] | None = None
        self.buffer = b""
        self.selector = selectors.DefaultSelector()
        self.resources = ExitStack()
        self.websocket: ClientConnection | None = None
        if self.remote:
            if not self.remote.startswith("unix:///"):
                self.selector.close()
                raise NativeForkError(
                    "Codex fork acknowledgement supports only explicit absolute unix:// remote endpoints"
                )
            try:
                self.websocket = self.resources.enter_context(
                    unix_connect(
                        self.remote[len("unix://") :],
                        open_timeout=ACK_TIMEOUT_SECONDS,
                        close_timeout=1,
                        max_size=MAX_ACK_BYTES,
                        max_queue=4,
                        ping_interval=None,
                        compression=None,
                    )
                )
            except (WebSocketException, OSError) as exc:
                self.selector.close()
                raise NativeForkError(
                    "selected Codex remote WebSocket is unavailable"
                ) from exc
        else:
            try:
                self.process = subprocess.Popen(
                    [executable, "app-server", "--stdio"],
                    cwd=root,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                self.selector.close()
                raise
            assert self.process.stdout is not None
            self.reader = self.process.stdout.fileno()
            self.selector.register(self.reader, selectors.EVENT_READ)

    def __enter__(self) -> Self:
        return self

    def send(self, value: dict[str, Any]) -> None:
        data = json.dumps(value).encode() + b"\n"
        if self.websocket is not None:
            try:
                self.websocket.send(data.rstrip(b"\n").decode("utf-8"))
            except WebSocketException as exc:
                raise NativeForkError(
                    "Codex connection closed before acknowledgement"
                ) from exc
            return
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def request(
        self, request_id: str, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + ACK_TIMEOUT_SECONDS
        consumed = 0
        while time.monotonic() < deadline:
            if self.websocket is not None and not self.buffer:
                try:
                    message = self.websocket.recv(
                        timeout=max(0, deadline - time.monotonic())
                    )
                except (WebSocketException, TimeoutError) as exc:
                    raise NativeForkError(
                        f"Codex {method} acknowledgement absent or timed out"
                    ) from exc
                if not isinstance(message, str):
                    raise NativeForkError("Codex returned a non-text acknowledgement")
                self.buffer = message.encode("utf-8") + b"\n"
            if b"\n" not in self.buffer:
                if not self.selector.select(max(0, deadline - time.monotonic())):
                    break
                data = os.read(self.reader, 65536)
                if not data:
                    raise NativeForkError(
                        "Codex closed before native fork acknowledgement"
                    )
                consumed += len(data)
                if consumed > MAX_ACK_BYTES:
                    raise NativeForkError(
                        "Codex acknowledgement exceeded the bounded response limit"
                    )
                self.buffer += data
                continue
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                raise NativeForkError("Codex acknowledgement is malformed") from None
            if not isinstance(value, dict) or value.get("id") != request_id:
                continue
            if "error" in value or not isinstance(value.get("result"), dict):
                raise NativeForkError(
                    f"Codex rejected {method}; native identity remains unconfirmed"
                )
            return value["result"]
        raise NativeForkError(f"Codex {method} acknowledgement timed out")

    def __exit__(self, *_args: object) -> None:
        self.selector.close()
        self.resources.close()
        if self.process is not None:
            assert self.process.stdin is not None
            self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait()
            finally:
                assert self.process.stdout is not None
                self.process.stdout.close()


def _confirmed_thread(
    value: dict[str, Any], parent: str, root: str, child: str = ""
) -> str:
    thread = value.get("thread")
    if not isinstance(thread, dict):
        raise NativeForkError("Codex acknowledgement has no native thread")
    identity = thread.get("id")
    try:
        valid = isinstance(identity, str) and UUID(identity).int != 0
    except ValueError:
        valid = False
    if not valid or identity == parent or (child and identity != child):
        raise NativeForkError(
            "Codex returned a missing, reused or mismatched child identity"
        )
    if thread.get("forkedFromId") != parent:
        raise NativeForkError("Codex child has no matching direct fork parent")
    cwd = thread.get("cwd")
    if not isinstance(cwd, str) or Path(cwd).resolve() != Path(root).resolve():
        raise NativeForkError("Codex child repository differs from the admitted root")
    return identity


def confirm_codex_native_fork(
    *,
    executable: str,
    env: dict[str, str],
    root: str,
    parent: str,
    run_id: str,
    model: str,
    permissions: str,
) -> dict[str, Any]:
    """Fork natively, then read the exact child back over the same transport."""
    from ..spawn import resolve_provider_policy

    policy = resolve_provider_policy(
        "codex", "local-native", permissions, "interactive"
    )
    if not policy.supported:
        raise NativeForkError(policy.reason)
    flags = list(policy.flags)
    if "--dangerously-bypass-approvals-and-sandbox" in flags:
        sandbox, approval = "danger-full-access", "never"
    else:
        sandbox = flags[flags.index("--sandbox") + 1]
        approval = flags[flags.index("--ask-for-approval") + 1]
    with CodexAppServer(executable, env, root) as rpc:
        rpc.request(
            f"{run_id}:initialize",
            "initialize",
            {
                "clientInfo": {"name": "vibecrafted_fork", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        rpc.send({"method": "initialized"})
        params: dict[str, Any] = {
            "threadId": parent,
            "cwd": root,
            "sandbox": sandbox,
            "approvalPolicy": approval,
        }
        if model:
            params["model"] = model
        fork_id, read_id = f"{run_id}:fork", f"{run_id}:read-child"
        response = rpc.request(fork_id, "thread/fork", params)
        child = _confirmed_thread(response, parent, root)
        _confirmed_thread(
            rpc.request(
                read_id, "thread/read", {"threadId": child, "includeTurns": False}
            ),
            parent,
            root,
            child,
        )
        return {
            "provider_session_id": child,
            "agent_session_id": child,
            "native_identity_status": "confirmed",
            "native_identity_evidence": {
                "method": "thread/fork",
                "request_id": fork_id,
                "read_request_id": read_id,
                "forked_from_id": parent,
                "child_id": child,
                "cwd": root,
                "executable": executable,
                "transport": rpc.remote or "stdio://",
            },
        }
