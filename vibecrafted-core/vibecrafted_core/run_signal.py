"""Ephemeral Unix-socket wake channel for dispatcher-owned runs.

Durable truth remains in control-plane files.  This stream only wakes awaiters;
EOF means the dispatcher disappeared and clients must reconcile file truth.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import selectors
import socket
import stat
import sys
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self

from .runtime_paths import run_signal_socket_path

SCHEMA = "vibecrafted.run-signal.v1"
OWNER_SCHEMA = "vibecrafted.run-signal-owner.v1"
_MAX_EVENT_BYTES = 256 * 1024
_MAX_CLIENT_BUFFER_BYTES = 32 * 1024
_PENDING_BATCH_SIZE = 32
_DARWIN_SUN_PATH_LIMIT = 104


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner_path(path: Path) -> Path:
    return path.with_suffix(".owner.json")


def _lock_path(path: Path) -> Path:
    return path.with_suffix(".lock")


def _validate_socket_path(path: Path) -> None:
    length = len(os.fsencode(path))
    if length >= _DARWIN_SUN_PATH_LIMIT:
        raise RuntimeError(
            "run signal socket path exceeds the macOS sun_path limit "
            f"({length} >= {_DARWIN_SUN_PATH_LIMIT}); set VIBECRAFTED_HOME "
            "normally and keep /tmp available for the hashed vc-cp path"
        )


def _read_owner(path: Path) -> dict[str, Any] | None:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid run signal owner sidecar: {path}: {exc}") from exc
    if not isinstance(body, dict):
        raise TypeError(f"invalid run signal owner sidecar: {path}: expected object")
    return body


class RunSignalServer:
    """One dispatcher-owned listener with replay and fan-out to N clients."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.path = run_signal_socket_path(run_id)
        _validate_socket_path(self.path)
        self.dispatcher_pid = os.getpid()
        self.start_token = secrets.token_hex(16)
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
        self._owner_lock_fd: int | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._dropped_clients = 0

    def start(self) -> RunSignalServer:
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
        # A socket directory needs execute permission; owner-only rwx is the
        # least privilege that still lets this user connect and unlink.
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory_stat = self.path.parent.lstat()
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.getuid()
        ):
            raise RuntimeError(
                f"unsafe run signal socket directory (must be an owned directory): {self.path.parent}"
            )
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_fd = os.open(self.path.parent, directory_flags)
        try:
            os.fchmod(  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
                directory_fd, 0o700
            )
        finally:
            os.close(directory_fd)
        self._acquire_owner_lock()
        try:
            self._prepare_socket_path()
        except BaseException:
            self._release_owner_lock()
            raise
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            listener.listen()
            listener.setblocking(False)
            socket_stat = self.path.lstat()
            self._socket_identity = (socket_stat.st_dev, socket_stat.st_ino)
            self._write_owner_sidecar(socket_stat)
        except BaseException:
            listener.close()
            self._cleanup_failed_start()
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

    def _acquire_owner_lock(self) -> None:
        lock_path = _lock_path(self.path)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(lock_path, flags, 0o600)
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RuntimeError(
                f"run signal socket owned by live dispatcher (owner lock busy): {self.path}"
            ) from exc
        self._owner_lock_fd = fd

    def _valid_owner(self, owner: Mapping[str, Any]) -> bool:
        return bool(
            owner.get("schema") == OWNER_SCHEMA
            and owner.get("run_id") == self.run_id
            and isinstance(owner.get("dispatcher_pid"), int)
            and isinstance(owner.get("start_token"), str)
            and owner.get("start_token")
            and owner.get("path") == str(self.path)
            and isinstance(owner.get("socket_dev"), int)
            and isinstance(owner.get("socket_ino"), int)
        )

    def _prepare_socket_path(self) -> None:
        owner_path = _owner_path(self.path)
        owner = _read_owner(owner_path)
        try:
            socket_stat = self.path.lstat()
        except FileNotFoundError:
            if owner is not None:
                if not self._valid_owner(owner):
                    raise RuntimeError(
                        f"refusing invalid/foreign run signal owner sidecar: {owner_path}"
                    )
                owner_path.unlink(missing_ok=True)
            return
        if not stat.S_ISSOCK(socket_stat.st_mode):
            raise RuntimeError(
                f"refusing to unlink non-socket run signal path: {self.path}"
            )
        if owner is None or not self._valid_owner(owner):
            raise RuntimeError(
                f"refusing to unlink unowned/foreign run signal socket: {self.path}"
            )
        if (owner["socket_dev"], owner["socket_ino"]) != (
            socket_stat.st_dev,
            socket_stat.st_ino,
        ):
            raise RuntimeError(
                f"refusing run signal socket whose inode differs from owner sidecar: {self.path}"
            )
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.05)
            probe.connect(str(self.path))
        except (ConnectionRefusedError, FileNotFoundError):
            current = self.path.lstat()
            if (current.st_dev, current.st_ino) != (
                owner["socket_dev"],
                owner["socket_ino"],
            ):
                raise RuntimeError(
                    f"refusing raced run signal socket replacement: {self.path}"
                )
            self.path.unlink()
            owner_path.unlink(missing_ok=True)
        except (TimeoutError, OSError) as exc:
            raise RuntimeError(
                f"refusing to unlink live/foreign run signal socket: {self.path}: {exc}"
            ) from exc
        else:
            raise RuntimeError(
                f"run signal socket already owned by a live process: {self.path}"
            )
        finally:
            probe.close()

    def _write_owner_sidecar(self, socket_stat: os.stat_result) -> None:
        owner_path = _owner_path(self.path)
        temp_path = owner_path.with_name(
            f".{owner_path.name}.{self.dispatcher_pid}.{self.start_token}.tmp"
        )
        payload = {
            "schema": OWNER_SCHEMA,
            "run_id": self.run_id,
            "dispatcher_pid": self.dispatcher_pid,
            "start_token": self.start_token,
            "path": str(self.path),
            "socket_dev": socket_stat.st_dev,
            "socket_ino": socket_stat.st_ino,
            "created_at": _timestamp(),
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(temp_path, flags, 0o600)
        try:
            encoded = (json.dumps(payload, sort_keys=True) + "\n").encode()
            os.write(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temp_path, owner_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _cleanup_failed_start(self) -> None:
        try:
            if self._socket_identity is not None:
                current = self.path.lstat()
                if (current.st_dev, current.st_ino) == self._socket_identity:
                    self.path.unlink()
                    _owner_path(self.path).unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        self._release_owner_lock()

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
            "dispatcher_pid": self.dispatcher_pid,
            "start_token": self.start_token,
            "kind": kind,
            "state": state,
            "settlement": settlement,
            "report": report,
            "ts": _timestamp(),
        }
        with self._lock:
            event["dropped_clients"] = self._dropped_clients
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
        clients: dict[socket.socket, bytearray] = {}
        try:
            while not self._stopping:
                for key, mask in self._selector.select(timeout=1.0):
                    if key.data == "listener":
                        assert self._listener is not None
                        try:
                            client, _ = self._listener.accept()
                            client.setblocking(False)
                            client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
                            clients[client] = bytearray()
                            with self._lock:
                                replay = self._last_event
                            if replay:
                                clients[client].extend(replay)
                                self._selector.register(
                                    client, selectors.EVENT_WRITE, "client"
                                )
                        except OSError:
                            continue
                    elif key.data == "wakeup":
                        try:
                            assert self._wakeup_read is not None
                            self._wakeup_read.recv(4096)
                        except (BlockingIOError, OSError):
                            pass
                        with self._lock:
                            pending = self._pending[:_PENDING_BATCH_SIZE]
                            del self._pending[:_PENDING_BATCH_SIZE]
                            more_pending = bool(self._pending)
                        for event in pending:
                            for client in list(clients):
                                buffer = clients[client]
                                if len(buffer) + len(event) > _MAX_CLIENT_BUFFER_BYTES:
                                    self._drop_client(clients, client, "backpressure")
                                    continue
                                was_empty = not buffer
                                buffer.extend(event)
                                if was_empty:
                                    try:
                                        self._selector.register(
                                            client, selectors.EVENT_WRITE, "client"
                                        )
                                    except KeyError:
                                        self._selector.modify(
                                            client, selectors.EVENT_WRITE, "client"
                                        )
                        if more_pending:
                            self._wake()
                    elif key.data == "client" and mask & selectors.EVENT_WRITE:
                        client = key.fileobj
                        assert isinstance(client, socket.socket)
                        buffer = clients.get(client)
                        if buffer is None:
                            continue
                        try:
                            sent = client.send(buffer)
                        except (BlockingIOError, InterruptedError):
                            continue
                        except OSError:
                            self._drop_client(clients, client, "write-error")
                            continue
                        if sent <= 0:
                            self._drop_client(clients, client, "closed")
                            continue
                        del buffer[:sent]
                        if not buffer:
                            try:
                                self._selector.unregister(client)
                            except (KeyError, ValueError):
                                pass
        finally:
            for client in clients:
                client.close()

    def _drop_client(
        self,
        clients: dict[socket.socket, bytearray],
        client: socket.socket,
        reason: str,
    ) -> None:
        try:
            self._selector.unregister(client)
        except (KeyError, ValueError):
            pass
        clients.pop(client, None)
        client.close()
        if reason == "backpressure":
            with self._lock:
                self._dropped_clients += 1
            print(
                f"dispatcher: dropped run-signal client after backpressure: {self.run_id}",
                file=sys.stderr,
                flush=True,
            )

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
        try:
            self._unlink_owned_socket()
        finally:
            self._release_owner_lock()

    def _unlink_owned_socket(self) -> None:
        owner_path = _owner_path(self.path)
        try:
            owner = _read_owner(owner_path)
            current = self.path.lstat()
        except FileNotFoundError:
            return
        if (
            owner is None
            or owner.get("dispatcher_pid") != self.dispatcher_pid
            or owner.get("start_token") != self.start_token
            or (current.st_dev, current.st_ino)
            != (owner.get("socket_dev"), owner.get("socket_ino"))
        ):
            return
        self.path.unlink()
        owner_path.unlink(missing_ok=True)

    def _release_owner_lock(self) -> None:
        if self._owner_lock_fd is None:
            return
        try:
            fcntl.flock(self._owner_lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(self._owner_lock_fd)
            self._owner_lock_fd = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()


def wait_for_run_signal(
    run_id: str,
    *,
    timeout: float | None = None,
    read_size: int = 65536,
) -> dict[str, Any]:
    """Block for a terminal JSON line; return ``kind=missing|eof`` for reconciliation."""
    path = run_signal_socket_path(run_id)
    _validate_socket_path(path)
    if not 1 <= read_size <= 65536:
        raise ValueError("read_size must be between 1 and 65536")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if timeout is not None and timeout > 0:
        client.settimeout(timeout)
    try:
        try:
            client.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError, OSError):
            return {"kind": "missing", "run_id": run_id}
        buffer = bytearray()
        identity: tuple[int, str] | None = None
        while True:
            chunk = client.recv(read_size)
            if not chunk:
                result: dict[str, Any] = {"kind": "eof", "run_id": run_id}
                if identity is not None:
                    result["dispatcher_pid"], result["start_token"] = identity
                return result
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
                dispatcher_pid = event.get("dispatcher_pid")
                start_token = event.get("start_token")
                if not isinstance(dispatcher_pid, int) or not isinstance(
                    start_token, str
                ):
                    continue
                event_identity = (dispatcher_pid, start_token)
                if identity is None:
                    identity = event_identity
                elif identity != event_identity:
                    return {
                        "kind": "identity_changed",
                        "run_id": run_id,
                        "dispatcher_pid": dispatcher_pid,
                        "start_token": start_token,
                        "previous_dispatcher_pid": identity[0],
                        "previous_start_token": identity[1],
                    }
                if event.get("kind") == "terminal":
                    return dict(event)
                # Unknown kinds and heartbeats deliberately keep the read armed.
    except TimeoutError:
        return {"kind": "timeout", "run_id": run_id}
    finally:
        client.close()
