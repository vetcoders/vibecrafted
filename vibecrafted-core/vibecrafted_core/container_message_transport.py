"""Standard-library client for the run-scoped host inbox transport.

The guest CLI and the launch handshake use these exact functions. This module
contains no host state access, run registry or provider lifecycle ownership.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
import uuid
from pathlib import Path
from typing import Any

_MAX_REQUEST = 8192
_MAX_RESPONSE = 8 * 1024 * 1024
_CONNECTION_TTL = 60
_CONNECTION_SCHEMA = "vibecrafted.container-message-connection.v1"


class BridgeTransportError(ValueError):
    """A missing, stale, ambiguous or unresponsive run connection."""


def discover_channel(root: Path, run_id: str) -> str:
    """Select exactly one live run-local transport, never a message store."""
    try:
        channels = list(root.glob("message-*"))
        if len(channels) > 64:
            raise BridgeTransportError("bridge_discovery_limit")
        current = []
        for channel in channels:
            if channel.is_symlink() or not channel.is_dir():
                raise BridgeTransportError("bridge_invalid_connection")
            try:
                fd = os.open(
                    channel / "connection.json",
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
            except FileNotFoundError:
                # Old relays had no discovery descriptor. Do not mutate them.
                continue
            with os.fdopen(fd, "rb") as inp:
                if not stat.S_ISREG(os.fstat(inp.fileno()).st_mode):
                    raise BridgeTransportError("bridge_invalid_connection")
                raw = inp.read(_MAX_REQUEST + 1)
            if len(raw) > _MAX_REQUEST:
                raise BridgeTransportError("bridge_invalid_connection")
            data = json.loads(raw)
            if (
                not isinstance(data, dict)
                or data.get("schema") != _CONNECTION_SCHEMA
                or data.get("run_id") != run_id
                or data.get("channel") != str(channel)
                or not re.fullmatch(r"message-[0-9a-f]{48}", channel.name)
                or not isinstance(data.get("updated_at"), (float, int))
                or isinstance(data["updated_at"], bool)
            ):
                raise BridgeTransportError("bridge_invalid_connection")
            age = time.time() - data["updated_at"]
            if 0 <= age < _CONNECTION_TTL:
                current.append(str(channel))
        if len(current) > 1:
            raise BridgeTransportError("bridge_connection_ambiguous")
        if not current:
            raise BridgeTransportError(
                "bridge_unavailable: reconnect this run from its host with --connect-container"
            )
        return current[0]
    except (OSError, ValueError) as exc:
        if isinstance(exc, BridgeTransportError):
            raise
        raise BridgeTransportError("bridge_invalid_connection") from exc


def request(
    *,
    channel: str,
    action: str,
    run_id: str,
    session: str,
    message_id: str,
    timeout: float = 20,
) -> Any:
    """Request host operation, fail closed on timeout or invalid response."""
    payload = json.dumps(
        {
            "action": action,
            "run_id": run_id,
            "session": session,
            "message_id": message_id,
        }
    ).encode()
    if len(payload) > _MAX_REQUEST:
        raise BridgeTransportError("bridge_request_too_large")
    request_id = uuid.uuid4().hex
    root = Path(channel)
    temporary, request, response = (
        root / f"{request_id}.{suffix}" for suffix in ("tmp", "request", "reply")
    )
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
        os.replace(temporary, request)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                fd = os.open(response, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            except FileNotFoundError:
                time.sleep(0.05)
                continue
            with os.fdopen(fd, "rb") as inp:
                raw = inp.read(_MAX_RESPONSE + 1)
            response.unlink(missing_ok=True)
            if len(raw) > _MAX_RESPONSE:
                raise BridgeTransportError("bridge_response_too_large")
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
                raise BridgeTransportError("bridge_invalid_response")
            if not data["ok"]:
                raise BridgeTransportError(str(data.get("error") or "bridge_failed"))
            return data.get("result")
        raise BridgeTransportError("bridge_timeout")
    except (OSError, ValueError) as exc:
        if isinstance(exc, BridgeTransportError):
            raise
        raise BridgeTransportError(f"bridge_unavailable:{type(exc).__name__}") from exc
    finally:
        for path in (temporary, request, response):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
