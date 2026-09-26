"""HTTP `vc_*` list and schema parity against the stdio FastMCP server.

The stdio server is the meter. This test counts tools from that process and
from `POST /mcp` on vc-server; it does not hard-code the inventory.

Marked integration because it starts both processes. The delivery gate runs
this file directly, so the marker does not skip it.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
MCP_DIR = REPO / "vibecrafted-mcp"
CORE_DIR = REPO / "vibecrafted-core"
SERVER_DIR = REPO / "vibecrafted-server"
TOKEN = "parity-bearer"
PILOTS = {"vc_ping", "vc_run_status"}


def _pythonpath() -> str:
    parts = [str(MCP_DIR), str(CORE_DIR)]
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)


def _bridge_argv() -> list[str]:
    """Same interpreter the HTTP bridge will launch, so both lists match."""
    probe = "import vibecrafted_mcp, fastmcp; from vibecrafted_mcp.server import main"
    env = os.environ.copy()
    env["PYTHONPATH"] = _pythonpath()
    if (
        subprocess.run(
            [sys.executable, "-c", probe],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    ):
        return [
            sys.executable,
            "-c",
            "from vibecrafted_mcp.server import main; raise SystemExit(main())",
        ]
    uv = _which("uv")
    if uv is not None:
        return [uv, "run", "--project", str(MCP_DIR), "vibecrafted-mcp"]
    pytest.fail(
        "neither this interpreter nor `uv run` can start vibecrafted-mcp; "
        "HTTP parity has no stdio meter"
    )


def _which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _send(proc: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def _recv(proc: subprocess.Popen[str]) -> dict[str, Any]:
    assert proc.stdout is not None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line == "":
            err = ""
            if proc.stderr is not None:
                err = proc.stderr.read()
            raise AssertionError(f"stdio server closed stdout\n{err}")
        text = line.strip()
        if not text.startswith("{"):
            continue
        message = json.loads(text)
        if "id" in message:
            return message
    raise AssertionError("timed out waiting for a stdio response")


def _stdio_exchange(
    argv: list[str], env: dict[str, str], calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "parity", "version": "0"},
                },
            },
        )
        init = _recv(proc)
        if "error" in init:
            raise AssertionError(f"stdio initialize failed: {init}")
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        replies: list[dict[str, Any]] = []
        for call in calls:
            _send(proc, call)
            reply = _recv(proc)
            if "error" in reply:
                raise AssertionError(f"stdio call failed: {reply}")
            replies.append(reply)
        return replies
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def stdio_tools(argv: list[str], env: dict[str, str]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    cursor: str | None = None
    next_id = 2
    for _ in range(16):
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        replies = _stdio_exchange(
            argv,
            env,
            [
                {
                    "jsonrpc": "2.0",
                    "id": next_id,
                    "method": "tools/list",
                    "params": params,
                }
            ],
        )
        result = replies[0]["result"]
        tools.extend(result.get("tools") or [])
        cursor = result.get("nextCursor") or None
        if not cursor:
            return tools
        next_id += 1
    raise AssertionError("stdio tools/list cursor did not finish")


def stdio_call(
    argv: list[str], env: dict[str, str], name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    replies = _stdio_exchange(
        argv,
        env,
        [
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        ],
    )
    return replies[0]["result"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server_binary() -> Path:
    override = os.environ.get("VC_SERVER_BIN")
    if override:
        path = Path(override)
        if path.is_file():
            return path
    candidates = [
        SERVER_DIR / "target" / "debug" / "vibecrafted-server-web",
        REPO / "target" / "debug" / "vibecrafted-server-web",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    subprocess.run(
        [
            "cargo",
            "build",
            "-p",
            "vibecrafted-server-web",
            "--features",
            "ssr",
            "--bin",
            "vibecrafted-server-web",
        ],
        cwd=SERVER_DIR,
        check=True,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    pytest.fail(
        "vc-server binary missing after build: "
        + ", ".join(str(candidate) for candidate in candidates)
    )


class Server:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.port = _free_port()
        argv = _bridge_argv()
        env = os.environ.copy()
        env.update(
            {
                "VIBECRAFTED_HOME": str(home),
                "VC_SERVER_MCP_BEARER": TOKEN,
                "VC_MCP_BRIDGE_COMMAND": argv[0],
                "VC_MCP_BRIDGE_ARGS": json.dumps(argv[1:]),
                "PYTHONPATH": _pythonpath(),
                "VC_MCP_BRIDGE_CALL_TIMEOUT_MS": "120000",
            }
        )
        self.argv = argv
        self.env = env
        self.log_lines: list[str] = []
        self.proc = subprocess.Popen(
            [
                str(_server_binary()),
                "--addr",
                f"127.0.0.1:{self.port}",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()
        self._wait_until_listening()

    def _drain(self) -> None:
        if self.proc.stdout is None:
            return
        for line in self.proc.stdout:
            self.log_lines.append(line)

    def _wait_until_listening(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                self._reader.join(timeout=2)
                raise AssertionError(
                    "vc-server exited early\n" + "".join(self.log_lines)
                )
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        self.proc.kill()
        self._reader.join(timeout=2)
        raise AssertionError(
            f"vc-server did not listen on {self.port}\n" + "".join(self.log_lines)
        )

    def close(self) -> str:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self._reader.join(timeout=2)
        return "".join(self.log_lines)

    def rpc(self, method: str, params: dict[str, Any], rpc_id: int) -> dict[str, Any]:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
        )
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        try:
            connection.request(
                "POST",
                "/mcp",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {TOKEN}",
                },
            )
            response = connection.getresponse()
            payload = response.read()
        finally:
            connection.close()
        if response.status != 200:
            raise AssertionError(
                f"HTTP {response.status} from /mcp: {payload.decode(errors='replace')}"
            )
        return json.loads(payload.decode())


def _by_name(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    named: dict[str, dict[str, Any]] = {}
    for tool in tools:
        name = tool.get("name")
        assert isinstance(name, str) and name, tool
        assert name not in named, f"duplicate tool {name}"
        named[name] = tool
    return named


def _summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    summary: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value
        elif isinstance(value, list):
            summary[key] = f"list:{len(value)}"
        elif isinstance(value, dict):
            summary[key] = f"dict:{sorted(value)}"
        else:
            summary[key] = type(value).__name__
    return summary


def test_http_tool_list_matches_stdio_without_pilot_duplicates(tmp_path: Path) -> None:
    argv = _bridge_argv()
    env = os.environ.copy()
    env["PYTHONPATH"] = _pythonpath()
    env["VIBECRAFTED_HOME"] = str(tmp_path / "stdio-home")
    stdio = _by_name(stdio_tools(argv, env))
    assert stdio, "stdio server advertised no tools"
    assert all(name.startswith("vc_") for name in stdio), sorted(stdio)

    server = Server(tmp_path / "http-home")
    try:
        listed = server.rpc("tools/list", {}, 1)
    finally:
        log = server.close()
    assert "error" not in listed, f"{listed}\n{log}"
    http = _by_name(listed["result"]["tools"])

    expected = {name for name in stdio if name.startswith("vc_")}
    expected.add("vc_ping")
    expected.add("vc_run_status")
    assert set(http) == expected, (
        f"stdio={len(stdio)} http={len(http)} "
        f"missing={sorted(expected - set(http))} extra={sorted(set(http) - expected)}"
    )
    assert "vc_ping" not in stdio
    assert len(http) == len(stdio) + 1

    for name, tool in stdio.items():
        if name in PILOTS:
            continue
        assert http[name]["inputSchema"] == tool["inputSchema"], name
        if "description" in tool:
            assert http[name].get("description") == tool["description"], name

    stdio_status = json.dumps(stdio["vc_run_status"]["inputSchema"])
    http_status = json.dumps(http["vc_run_status"]["inputSchema"])
    assert "home" in stdio_status
    assert "home" not in http_status
    assert http["vc_run_status"]["description"] == "Read one control-plane run by id."
    print(
        f"PARITY_STDIO={len(stdio)} PARITY_HTTP={len(http)} "
        f"NAMES={','.join(sorted(http))}"
    )


def test_http_vc_doctor_matches_stdio_shape(tmp_path: Path) -> None:
    argv = _bridge_argv()
    env = os.environ.copy()
    env["PYTHONPATH"] = _pythonpath()
    env["VIBECRAFTED_HOME"] = str(tmp_path / "doctor-home")
    (tmp_path / "doctor-home").mkdir()
    stdio_result = stdio_call(argv, env, "vc_doctor", {})
    server = Server(tmp_path / "doctor-home")
    try:
        http_reply = server.rpc(
            "tools/call",
            {"name": "vc_doctor", "arguments": {}},
            2,
        )
    finally:
        log = server.close()
    assert "error" not in http_reply, f"{http_reply}\n{log}"
    http_result = http_reply["result"]
    assert stdio_result.get("isError") is False
    assert http_result.get("isError") is False
    stdio_structured = stdio_result.get("structuredContent")
    http_structured = http_result.get("structuredContent")
    assert isinstance(stdio_structured, dict), stdio_result
    assert isinstance(http_structured, dict), http_result
    assert set(stdio_structured) == set(http_structured)
    stdio_summary = _summary(stdio_structured)
    http_summary = _summary(http_structured)
    for key in ("ok", "warnings", "failures", "healthy", "unavailable"):
        if key in stdio_summary or key in http_summary:
            assert stdio_summary.get(key) == http_summary.get(key), (
                f"{key}: stdio={stdio_summary} http={http_summary}"
            )
    print(f"VC_DOCTOR_STDIO={json.dumps(stdio_summary, sort_keys=True)}")
    print(f"VC_DOCTOR_HTTP={json.dumps(http_summary, sort_keys=True)}")
