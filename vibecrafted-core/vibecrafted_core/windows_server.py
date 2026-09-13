"""Native Windows vc-server lifecycle bound to the active Runtime Pack generation.

Unix still owns the bash deck + launchd supervisor. This module is the one
Windows path: start the pack's ``vc-server.exe`` from ``active.json``, bind
loopback by default, probe ``/api/health``, and stop the same process. No
second control plane.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .runtime_paths import (
    GenerationResolutionError,
    resolve_active_generation,
    vibecrafted_home,
    vibecrafted_runtime_home,
)

DEFAULT_ADDR = "127.0.0.1:3024"
HEALTH_PATH = "/api/health"
TASK_NAME = "VibecraftedServer"


class WindowsServerError(RuntimeError):
    """Raised when the Windows server cannot be bound to the active generation."""


def server_state_dir() -> Path:
    """PID/log directory under the runtime home (installer-owned)."""
    return vibecrafted_runtime_home() / "server"


def pid_path() -> Path:
    return server_state_dir() / "vc-server.pid"


def log_path() -> Path:
    return server_state_dir() / "vc-server.log"


def _active_server_executable() -> Path:
    try:
        generation = resolve_active_generation()
    except GenerationResolutionError as exc:
        raise WindowsServerError(f"no active Runtime Pack generation: {exc}") from exc
    candidate = generation / "bin" / "vc-server.exe"
    if not candidate.is_file():
        raise WindowsServerError(
            f"active generation is missing vc-server.exe: {candidate}"
        )
    return candidate.resolve(strict=True)


def _read_pid() -> int | None:
    path = pid_path()
    if not path.is_file():
        return None
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def _pid_is_running(pid: int) -> bool:
    try:
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except (AttributeError, OSError, ValueError):
        return False


def _process_image(pid: int) -> str | None:
    """Return the running image path without depending on powershell on PATH."""
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value or None
            return None
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return None


def _write_pid(pid: int) -> None:
    directory = server_state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    pid_path().write_text(f"{pid}\n", encoding="utf-8")


def _clear_pid() -> None:
    path = pid_path()
    if path.is_file():
        path.unlink()


def health_url(addr: str = DEFAULT_ADDR) -> str:
    return f"http://{addr}{HEALTH_PATH}"


def probe_health(addr: str = DEFAULT_ADDR, *, timeout: float = 2.0) -> dict[str, Any]:
    request = urllib.request.Request(health_url(addr), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            payload: Any
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                payload = {"raw": body.decode("utf-8", errors="replace")}
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "body": payload,
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": int(exc.code), "error": str(exc)}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "status": 0, "error": str(exc)}


def status(*, addr: str = DEFAULT_ADDR) -> dict[str, Any]:
    executable = None
    generation = None
    try:
        executable = str(_active_server_executable())
        generation = str(resolve_active_generation())
    except (WindowsServerError, GenerationResolutionError) as exc:
        generation_error = str(exc)
    else:
        generation_error = ""
    pid = _read_pid()
    running = bool(pid and _pid_is_running(pid))
    image = _process_image(pid) if running and pid else None
    health = (
        probe_health(addr)
        if running
        else {"ok": False, "status": 0, "error": "not running"}
    )
    same_generation = False
    if running and executable and image:
        try:
            left = Path(image).resolve()
            right = Path(executable).resolve()
        except OSError:
            same_generation = False
        else:
            same_generation = os.path.normcase(str(left)) == os.path.normcase(str(right))
    return {
        "schema": "vibecrafted.windows-server-status.v1",
        "addr": addr,
        "pid": pid if running else None,
        "running": running,
        "executable": executable,
        "image": image,
        "generation": generation,
        "generation_error": generation_error,
        "same_generation": same_generation,
        "health": health,
        "pid_file": str(pid_path()),
        "home": str(vibecrafted_home()),
    }


def start(*, addr: str = DEFAULT_ADDR, wait_seconds: float = 8.0) -> dict[str, Any]:
    current = status(addr=addr)
    if current["running"]:
        if not current["same_generation"]:
            raise WindowsServerError(
                "vc-server is running from a path that is not the active generation"
            )
        return current
    executable = _active_server_executable()
    generation = resolve_active_generation()
    server_state_dir().mkdir(parents=True, exist_ok=True)
    log = log_path().open("ab")
    env = os.environ.copy()
    env["VIBECRAFTED_HOME"] = str(vibecrafted_home())
    env["VIBECRAFTED_RUNTIME_HOME"] = str(vibecrafted_runtime_home())
    env["VIBECRAFTED_RUNTIME_ROOT"] = str(generation)
    if not str(env.get("HOME") or "").strip():
        env["HOME"] = str(os.environ.get("USERPROFILE") or vibecrafted_home())
    site = generation / "server" / "site"
    if site.is_dir():
        env["VC_SERVER_SITE_ROOT"] = str(site)
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    )
    try:
        process = subprocess.Popen(
            [str(executable), "--addr", addr],
            cwd=str(generation),
            env=env,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    except OSError as exc:
        log.close()
        raise WindowsServerError(f"failed to start vc-server: {exc}") from exc
    _write_pid(process.pid)
    deadline = time.monotonic() + wait_seconds
    last = probe_health(addr)
    while time.monotonic() < deadline:
        if last.get("ok"):
            break
        time.sleep(0.25)
        last = probe_health(addr)
    result = status(addr=addr)
    if not result["health"].get("ok"):
        raise WindowsServerError(
            f"vc-server started (pid {process.pid}) but health failed: "
            f"{result['health']}"
        )
    if not result["same_generation"]:
        stop()
        raise WindowsServerError(
            "started vc-server is not the active generation binary"
        )
    return result


def stop(*, timeout: float = 8.0) -> dict[str, Any]:
    pid = _read_pid()
    if pid and _pid_is_running(pid):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and _pid_is_running(pid):
            time.sleep(0.1)
    _clear_pid()
    return status()


def uninstall_windows_server() -> list[str]:
    """Stop the server and remove the product scheduled task. Used by uninstall."""
    actions: list[str] = []
    before = status()
    if before["running"]:
        stop()
        actions.append("stop vc-server")
    subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    actions.append(f"remove task {TASK_NAME}")
    return actions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibecrafted server")
    parser.add_argument(
        "--addr",
        default=os.environ.get("VC_SERVER_ADDR", DEFAULT_ADDR),
        help=f"bind address (default {DEFAULT_ADDR})",
    )
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("start", help="start vc-server from the active generation")
    sub.add_parser("stop", help="stop the receipted vc-server process")
    sub.add_parser("status", help="print pid, generation, and health")
    sub.add_parser("health", help="probe /api/health")
    args = parser.parse_args(argv)
    action = args.action or "status"
    try:
        if action == "start":
            payload = start(addr=args.addr)
        elif action == "stop":
            payload = stop()
        elif action == "health":
            payload = probe_health(args.addr)
        else:
            payload = status(addr=args.addr)
    except WindowsServerError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0 if (action != "health" or payload.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
