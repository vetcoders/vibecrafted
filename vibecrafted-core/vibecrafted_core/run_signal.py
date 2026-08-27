"""Ephemeral Unix-socket wake channel for dispatcher-owned runs.

Durable truth remains in control-plane files.  This stream only wakes awaiters;
EOF means the dispatcher disappeared and clients must reconcile file truth.
"""

from __future__ import annotations

import json
import os
import selectors
import socket
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Self

from .runtime_paths import run_signal_socket_path

SCHEMA = "vibecrafted.run-signal.v1"
_MAX_EVENT_BYTES = 256 * 1024


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunSignalServer:
    """One dispatcher-owned listener with replay and fan-out to N clients."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.path = run_signal_socket_path(run_id)
        self._selector = selectors.DefaultSelector()
        self._listener: socket.socket | None = None
        self._wakeup_read: socket.socket | None = None
        self._wakeup_write: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending: list[bytes] = []
        self._last_event: bytes | None = None
        self._terminal_sent = False
        self._stopping = False

    def start(self) -> RunSignalServer:
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
        # A socket directory needs execute permission; owner-only rwx is the
        # least privilege that still lets this user connect and unlink.
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
            self.path.parent, 0o700
        )
        self._remove_stale_path()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            listener.listen()
            listener.setblocking(False)
        except BaseException:
            listener.close()
            raise
        wake_read, wake_write = socket.socketpair()
        wake_read.setblocking(False)
        wake_write.setblocking(False)
        self._listener = listener
        self._wakeup_read = wake_read
        self._wakeup_write = wake_write
        self._selector.register(listener, selectors.EVENT_READ, "listener")
        self._selector.register(wake_read, selectors.EVENT_READ, "wakeup")
        self._thread = threading.Thread(
            target=self._serve, name=f"run-signal-{self.run_id}", daemon=True
        )
        self._thread.start()
        return self

    def _remove_stale_path(self) -> None:
        if not self.path.exists():
            return
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.05)
            probe.connect(str(self.path))
        except OSError:
            self.path.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"run signal socket already owned: {self.path}")
        finally:
            probe.close()

    def publish(
        self,
        kind: str,
        *,
        state: str,
        settlement: str = "",
        report: str = "",
        exit_code: int | None = None,
    ) -> None:
        if kind == "terminal":
            with self._lock:
                if self._terminal_sent:
                    return
                self._terminal_sent = True
        event: dict[str, Any] = {
            "schema": SCHEMA,
            "run_id": self.run_id,
            "kind": kind,
            "state": state,
            "settlement": settlement,
            "report": report,
            "ts": _timestamp(),
        }
        if exit_code is not None:
            event["exit"] = exit_code
        encoded = (
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode()
        with self._lock:
            self._last_event = encoded
            self._pending.append(encoded)
        self._wake()

    def heartbeat(self, state: str) -> None:
        self.publish("heartbeat", state=state)

    def terminal(
        self,
        *,
        state: str,
        settlement: str = "",
        report: str = "",
        exit_code: int | None = None,
    ) -> None:
        self.publish(
            "terminal",
            state=state,
            settlement=settlement,
            report=report,
            exit_code=exit_code,
        )

    def _wake(self) -> None:
        try:
            if self._wakeup_write is not None:
                self._wakeup_write.send(b"x")
        except (BlockingIOError, OSError):
            pass

    def _serve(self) -> None:
        clients: set[socket.socket] = set()
        try:
            while not self._stopping:
                for key, _mask in self._selector.select(timeout=1.0):
                    if key.data == "listener":
                        assert self._listener is not None
                        try:
                            client, _ = self._listener.accept()
                            client.setblocking(False)
                            clients.add(client)
                            with self._lock:
                                replay = self._last_event
                            if replay and not self._send(client, replay):
                                clients.discard(client)
                        except OSError:
                            continue
                    elif key.data == "wakeup":
                        try:
                            assert self._wakeup_read is not None
                            self._wakeup_read.recv(4096)
                        except (BlockingIOError, OSError):
                            pass
                        with self._lock:
                            pending, self._pending = self._pending, []
                        for event in pending:
                            clients = {
                                client
                                for client in clients
                                if self._send(client, event)
                            }
        finally:
            for client in clients:
                client.close()

    @staticmethod
    def _send(client: socket.socket, event: bytes) -> bool:
        try:
            client.setblocking(True)
            client.sendall(event)
            client.setblocking(False)
            return True
        except OSError:
            client.close()
            return False

    def close(self) -> None:
        self._stopping = True
        self._wake()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        for sock in (self._listener, self._wakeup_read, self._wakeup_write):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._selector.close()
        self.path.unlink(missing_ok=True)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()


def wait_for_run_signal(run_id: str, *, timeout: float | None = None) -> dict[str, Any]:
    """Block for a terminal JSON line; return ``kind=missing|eof`` for reconciliation."""
    path = run_signal_socket_path(run_id)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if timeout is not None and timeout > 0:
        client.settimeout(timeout)
    try:
        try:
            client.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError, OSError):
            return {"kind": "missing", "run_id": run_id}
        buffer = bytearray()
        while True:
            chunk = client.recv(65536)
            if not chunk:
                return {"kind": "eof", "run_id": run_id}
            buffer.extend(chunk)
            if len(buffer) > _MAX_EVENT_BYTES:
                return {"kind": "eof", "run_id": run_id}
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                try:
                    event = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(event, Mapping):
                    continue
                if event.get("schema") != SCHEMA or event.get("run_id") != run_id:
                    continue
                if event.get("kind") == "terminal":
                    return dict(event)
                # Unknown kinds and heartbeats deliberately keep the read armed.
    except TimeoutError:
        return {"kind": "timeout", "run_id": run_id}
    finally:
        client.close()
