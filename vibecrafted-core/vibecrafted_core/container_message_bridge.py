"""Run-bound guest requests serviced by the host's canonical message owner.

The guest directory is only a transport, never a message store. The persistent
owner is message_control on the host. A local dev container is not a security
boundary between processes running as the same guest user; the random channel
prevents accidental cross-run access, not inspection by a hostile guest root.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import container_message_transport as transport
from .message_control import (
    MessageControlError,
    acknowledge_message,
    inspect_message,
    receive_messages,
    resolve_message_run,
)

BRIDGE_ENV = "VIBECRAFTED_MESSAGE_BRIDGE"
_MAX_REQUEST = transport._MAX_REQUEST
_MAX_RESPONSE = transport._MAX_RESPONSE
_ID = re.compile(r"^[0-9a-f]{32}$")

# These programs execute inside the container. Their argv are literal arguments,
# never shell-interpolated. The scan only reads regular, non-symlink requests.
_INIT = """import json,os,sys,time
root,run_id,container_id=sys.argv[1:]
client=sys.stdin.buffer.read(65537)
if len(client)>65536: raise SystemExit(2)
os.makedirs(root,mode=0o700,exist_ok=False)
fd=os.open(os.path.join(root,"client.py"),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,"wb") as out: out.write(client)
payload={"schema":"vibecrafted.container-message-connection.v1",
 "run_id":run_id,"container_id":container_id,"channel":root,"updated_at":time.time()}
fd=os.open(os.path.join(root,"connection.json"),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,"w") as out: json.dump(payload,out)
"""
_SCAN = """import json,os,re,stat,sys,time
root=sys.argv[1]; rows=[]
connection=os.path.join(root,"connection.json")
if os.path.isfile(connection):
 fd=os.open(connection,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
 with os.fdopen(fd) as inp: payload=json.load(inp)
 payload["updated_at"]=time.time()
 tmp=connection+".tmp"
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
 with os.fdopen(fd,"w") as out: json.dump(payload,out)
 os.replace(tmp,connection)
for entry in sorted(os.scandir(root), key=lambda e:e.name):
 if len(rows)>=64: break
 if not re.fullmatch(r"[0-9a-f]{32}\\.request",entry.name): continue
 try:
  fd=os.open(entry.path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
  try:
   if not stat.S_ISREG(os.fstat(fd).st_mode): continue
   data=os.read(fd,8193)
  finally: os.close(fd)
  os.unlink(entry.path)
  rows.append([entry.name[:-8],data.decode("utf-8","replace")])
 except OSError: continue
print(json.dumps(rows))
"""
_REPLY = """import os,sys
root,name=sys.argv[1:]; data=sys.stdin.buffer.read(8388609)
if len(data)>8388608: raise SystemExit(2)
tmp=os.path.join(root,name+".reply.tmp")
fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
with os.fdopen(fd,"wb") as out: out.write(data)
os.replace(tmp,os.path.join(root,name+".reply"))
"""
_CLEAN = """import os,re,sys
root=sys.argv[1]
for entry in os.scandir(root):
 if entry.name in {"connection.json","connection.json.tmp","client.py"} or re.fullmatch(r"[0-9a-f]{32}\\.(?:tmp|request|reply|reply\\.tmp)",entry.name):
  try: os.unlink(entry.path)
  except OSError: pass
try: os.rmdir(root)
except OSError: pass
"""


def _handle_request(bound_run: str, request: Any) -> dict[str, Any]:
    """Dispatch only receive/ACK for the explicit host-owned run."""
    try:
        if not isinstance(request, dict) or set(request) != {
            "action",
            "run_id",
            "session",
            "message_id",
        }:
            raise MessageControlError("bridge_invalid_request")
        if request["run_id"] != bound_run:
            raise MessageControlError("bridge_run_mismatch")
        if not all(isinstance(request[key], str) for key in request):
            raise MessageControlError("bridge_invalid_request")
        if request["action"] == "ping" and not request["message_id"]:
            resolve_message_run(run_id=bound_run, session=request["session"])
            result = {"run_id": bound_run}
        elif request["action"] == "receive" and not request["message_id"]:
            result = receive_messages(run_id=bound_run, session=request["session"])
        elif request["action"] == "ack" and request["message_id"]:
            result = acknowledge_message(
                request["message_id"], run_id=bound_run, session=request["session"]
            )
        elif request["action"] == "inspect" and request["message_id"]:
            resolve_message_run(run_id=bound_run, session=request["session"])
            result = inspect_message(request["message_id"])
            if result is None:
                raise MessageControlError("message_not_found")
            if result.get("run_id") != bound_run:
                raise MessageControlError("message_target_mismatch")
        else:
            raise MessageControlError("bridge_action_unsupported")
        response: dict[str, Any] = {"ok": True, "result": result}
        if len(json.dumps(response, ensure_ascii=False).encode()) > _MAX_RESPONSE:
            raise MessageControlError("bridge_response_too_large")
        return response
    except MessageControlError as exc:
        return {"ok": False, "error": str(exc)}


def guest_bridge_selected(run_id: str) -> bool:
    """A staged guest run requires its host owner, even without launch-time env."""
    if (
        os.environ.get(BRIDGE_ENV)
        or os.environ.get("VIBECRAFTED_MESSAGE_BRIDGE_REQUIRED") == "1"
    ):
        return True
    if not run_id:
        return False
    from .dev_container import _run_dir

    try:
        root = Path(_run_dir(run_id))
    except RuntimeError:
        return False
    return (root / "provider.identity").is_file() or (root / "prompt.md").is_file()


def guest_channel(run_id: str) -> str:
    """Discover a live transport in this run's existing staging directory."""
    explicit = os.environ.get(BRIDGE_ENV, "")
    if explicit:
        if not os.path.isabs(explicit):
            raise MessageControlError("bridge_unavailable")
        return explicit
    if not run_id:
        raise MessageControlError("bridge_run_id_required")
    from .dev_container import _run_dir

    try:
        return transport.discover_channel(Path(_run_dir(run_id)), run_id)
    except (RuntimeError, transport.BridgeTransportError) as exc:
        raise MessageControlError(str(exc)) from exc


class ContainerMessageRelay:
    """Own a scoped guest channel for the lifetime of one host-run owner."""

    def __init__(
        self,
        *,
        cli: str,
        container_id: str,
        run_id: str,
        env: Mapping[str, str],
        staging_root: str,
    ) -> None:
        self.cli, self.container_id, self.run_id = cli, container_id, run_id
        self.env = dict(env)
        self.guest_path = f"{staging_root}/message-{secrets.token_hex(24)}"
        self.failure = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    def _exec(
        self, script: str, *args: str, data: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        argv = [self.cli, "exec"]
        if data is not None:
            argv.append("-i")
        argv.extend([self.container_id, "python3", "-c", script, *args])
        return subprocess.run(
            argv, env=self.env, input=data, capture_output=True, timeout=15, check=False
        )

    def start(self) -> None:
        if self._thread is not None or self._stop.is_set():
            raise MessageControlError("bridge_already_started_or_closed")
        resolve_message_run(run_id=self.run_id)
        try:
            client = Path(transport.__file__).read_bytes()
            result = self._exec(
                _INIT, self.guest_path, self.run_id, self.container_id, data=client
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MessageControlError(
                f"bridge_start_failed:{type(exc).__name__}"
            ) from exc
        if result.returncode:
            raise MessageControlError("bridge_start_failed")
        self._thread = threading.Thread(
            target=self._serve, name="vc-container-message", daemon=True
        )
        self._thread.start()
        try:
            # Prove a guest -> host -> guest request before calling it connected.
            result = self._exec(
                _PROBE, self.guest_path, self.run_id, hashlib.sha256(client).hexdigest()
            )
            if result.returncode:
                raise MessageControlError("bridge_roundtrip_failed")
        except (OSError, subprocess.SubprocessError, MessageControlError):
            self.close()
            raise

    def _serve(self) -> None:
        try:
            while not self._stop.is_set():
                result = self._exec(_SCAN, self.guest_path)
                if result.returncode:
                    raise RuntimeError("container request scan failed")
                rows = json.loads(result.stdout)
                if not isinstance(rows, list):
                    raise TypeError("invalid scan response")
                for row in rows:
                    if (
                        not isinstance(row, list)
                        or len(row) != 2
                        or not isinstance(row[0], str)
                        or not _ID.fullmatch(row[0])
                        or not isinstance(row[1], str)
                    ):
                        raise ValueError("invalid request envelope")
                    try:
                        if len(row[1].encode()) > _MAX_REQUEST:
                            raise ValueError("request too large")
                        payload = json.loads(row[1])
                        reply = _handle_request(self.run_id, payload)
                    except (ValueError, UnicodeError):
                        reply = {"ok": False, "error": "bridge_invalid_request"}
                    encoded = json.dumps(reply, ensure_ascii=False).encode()
                    sent = self._exec(_REPLY, self.guest_path, row[0], data=encoded)
                    if sent.returncode:
                        raise RuntimeError("container response write failed")
                self._stop.wait(0.15)
        except (
            OSError,
            subprocess.SubprocessError,
            TypeError,
            ValueError,
            RuntimeError,
        ) as exc:
            self.failure = f"{type(exc).__name__}: {exc}"
            self._stop.set()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=20)
            if self._thread.is_alive():
                self.failure = "bridge_stop_timeout"
                return
            try:
                result = self._exec(_CLEAN, self.guest_path)
                if result.returncode and not self.failure:
                    self.failure = "bridge_cleanup_failed"
            except (OSError, subprocess.SubprocessError):
                if not self.failure:
                    self.failure = "bridge_cleanup_failed"


def guest_request(
    *, action: str, run_id: str, session: str, message_id: str, timeout: float = 20
) -> Any:
    """Request host operation, fail closed on timeout or invalid response."""
    try:
        return transport.request(
            channel=guest_channel(run_id),
            action=action,
            run_id=run_id,
            session=session,
            message_id=message_id,
            timeout=timeout,
        )
    except transport.BridgeTransportError as exc:
        raise MessageControlError(str(exc)) from exc


_PROBE = """import hashlib,importlib.util,json,sys
from pathlib import Path
sys.dont_write_bytecode=True
root,run_id,digest=sys.argv[1:]; client=Path(root)/"client.py"
if hashlib.sha256(client.read_bytes()).hexdigest()!=digest: raise SystemExit(2)
spec=importlib.util.spec_from_file_location("vc_container_client",client)
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
channel=module.discover_channel(Path(root).parent,run_id)
if channel!=root: raise SystemExit(2)
result=module.request(channel=channel,action="ping",run_id=run_id,session="",message_id="",timeout=8)
if result!={"run_id":run_id}: raise SystemExit(2)
"""
