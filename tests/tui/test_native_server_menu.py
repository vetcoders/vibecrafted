"""Contract for the tray's server menu: one caretaker truth, rendered verbatim.

The menu used to fuse three sources in Swift — a raw supervisor receipt read
with no freshness check, a ``server service status --json`` subprocess, and a
third subprocess for log paths — with the fusion rule living in the view
layer. That fusion is gone. The tray now runs one verb
(``server caretaker --json``), and these tests pin what the menu does with
the envelope: render the already-derived verdict, take button state from the
envelope's actions, and stay honest — never crash — when the server is down,
the envelope is garbage, or the caretaker never answered at all.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = (
    REPO_ROOT
    / "vibecrafted-app"
    / "shell-agent"
    / "app"
    / "Vibecrafted"
    / "ServerMenuPolicy.swift"
)

MAIN_SWIFT = r'''
import Foundation

let healthyEnvelope = #"""
{
  "schema": "vibecrafted.caretaker.v1",
  "generated_at": "2026-08-29T08:00:00+00:00",
  "control_plane": "/tmp/vc-home/control_plane",
  "server": {
    "available": true,
    "state": "healthy",
    "supervisor_pid": 123,
    "endpoint": {"host": "127.0.0.1", "port": 4107, "url": "http://127.0.0.1:4107"},
    "receipt": {"path": "/tmp/vc-home/server/supervisor.status.json", "present": true, "stale": false},
    "liveness": {"probed": true, "reachable": true, "reason": "", "version": "4.3.0"},
    "managed_pair": {"guardian_pid": 124, "server_pid": 125},
    "logs": {"available": true, "directory": "/tmp/vc-home/server", "stdout": "/tmp/vc-home/server/supervisor.stdout.log", "stderr": "/tmp/vc-home/server/supervisor.stderr.log", "reason": ""}
  },
  "verdict": {
    "health": "healthy",
    "server_health": "healthy",
    "server_state": "running",
    "header": "VC Server: HEALTHY · 127.0.0.1:4107",
    "detail": "Supervisor PID 123",
    "findings": []
  },
  "actions": {
    "start": {"enabled": false, "reason": "the server is already answering"},
    "stop": {"enabled": true, "reason": ""},
    "restart": {"enabled": true, "reason": ""},
    "open_console": {"enabled": true, "reason": "", "url": "http://127.0.0.1:4107"},
    "open_logs": {"enabled": true, "reason": "", "paths": {"directory": "/tmp/vc-home/server", "stdout": "/tmp/vc-home/server/supervisor.stdout.log", "stderr": "/tmp/vc-home/server/supervisor.stderr.log"}}
  }
}
"""#.data(using: .utf8)

let downEnvelope = #"""
{
  "schema": "vibecrafted.caretaker.v1",
  "generated_at": "2026-08-29T08:00:04+00:00",
  "control_plane": "/tmp/vc-home/control_plane",
  "server": {
    "available": true,
    "state": "backoff",
    "supervisor_pid": 123,
    "last_error": "worker failed\ntrace",
    "endpoint": {"host": "127.0.0.1", "port": 4107, "url": "http://127.0.0.1:4107"},
    "receipt": {"path": "/tmp/vc-home/server/supervisor.status.json", "present": true, "stale": true},
    "liveness": {"probed": true, "reachable": false, "reason": "ConnectionRefusedError: [Errno 61]", "version": ""},
    "managed_pair": {"guardian_pid": 124, "server_pid": 125},
    "logs": {"available": true, "directory": "/tmp/vc-home/server", "stdout": "/tmp/vc-home/server/supervisor.stdout.log", "stderr": "/tmp/vc-home/server/supervisor.stderr.log", "reason": ""}
  },
  "verdict": {
    "health": "unavailable",
    "server_health": "unavailable",
    "server_state": "down",
    "header": "VC Server: UNREACHABLE · 127.0.0.1:4107",
    "detail": "worker failed",
    "findings": [{"code": "server_unreachable", "severity": "error", "detail": "worker failed"}]
  },
  "actions": {
    "start": {"enabled": true, "reason": ""},
    "stop": {"enabled": false, "reason": "the endpoint is not answering"},
    "restart": {"enabled": true, "reason": ""},
    "open_console": {"enabled": false, "reason": "the server is not answering http://127.0.0.1:4107", "url": "http://127.0.0.1:4107"},
    "open_logs": {"enabled": true, "reason": "", "paths": {"directory": "/tmp/vc-home/server", "stdout": "/tmp/vc-home/server/supervisor.stdout.log", "stderr": "/tmp/vc-home/server/supervisor.stderr.log"}}
  }
}
"""#.data(using: .utf8)

let stoppedEnvelope = #"""
{
  "schema": "vibecrafted.caretaker.v1",
  "server": {
    "available": true,
    "state": "stopped",
    "endpoint": {"host": "127.0.0.1", "port": 4107, "url": "http://127.0.0.1:4107"},
    "receipt": {"path": "/tmp/vc-home/server/supervisor.status.json", "present": true, "stale": true},
    "liveness": {"probed": true, "reachable": false, "reason": "ConnectionRefusedError: [Errno 61]", "version": ""},
    "logs": {"available": true, "directory": "/tmp/vc-home/server", "stdout": "/tmp/vc-home/server/supervisor.stdout.log", "stderr": "/tmp/vc-home/server/supervisor.stderr.log", "reason": ""}
  },
  "verdict": {
    "health": "unavailable",
    "server_health": "unavailable",
    "server_state": "stopped",
    "header": "VC Server: STOPPED · 127.0.0.1:4107",
    "detail": "Service is intentionally stopped",
    "findings": []
  },
  "actions": {
    "start": {"enabled": true, "reason": ""},
    "stop": {"enabled": false, "reason": "the service is already stopped"},
    "restart": {"enabled": false, "reason": "the service is stopped; start it instead"},
    "open_console": {"enabled": false, "reason": "the server is not answering http://127.0.0.1:4107", "url": "http://127.0.0.1:4107"},
    "open_logs": {"enabled": true, "reason": "", "paths": {"directory": "/tmp/vc-home/server", "stdout": "/tmp/vc-home/server/supervisor.stdout.log", "stderr": "/tmp/vc-home/server/supervisor.stderr.log"}}
  }
}
"""#.data(using: .utf8)

let degradedEnvelope = #"""
{
  "schema": "vibecrafted.caretaker.v1",
  "server": {
    "available": true,
    "state": "healthy",
    "supervisor_pid": 123,
    "endpoint": {"host": "127.0.0.1", "port": 4107, "url": "http://127.0.0.1:4107"},
    "receipt": {"path": "/tmp/vc-home/server/supervisor.status.json", "present": true, "stale": false},
    "liveness": {"probed": true, "reachable": true, "reason": "", "version": "4.3.0"},
    "logs": {"available": true, "directory": "/tmp/vc-home/server", "stdout": "/tmp/vc-home/server/supervisor.stdout.log", "stderr": "/tmp/vc-home/server/supervisor.stderr.log", "reason": ""}
  },
  "verdict": {
    "health": "degraded",
    "server_health": "healthy",
    "server_state": "running",
    "header": "VC Server: HEALTHY · 127.0.0.1:4107 · 1 upkeep item",
    "detail": "events.jsonl is 16 MiB; rotation is overdue",
    "findings": [{"code": "event_stream_pressure", "severity": "warn", "detail": "events.jsonl is 16 MiB; rotation is overdue"}]
  },
  "actions": {
    "start": {"enabled": false, "reason": "the server is already answering"},
    "stop": {"enabled": true, "reason": ""},
    "restart": {"enabled": true, "reason": ""},
    "open_console": {"enabled": true, "reason": "", "url": "http://127.0.0.1:4107"},
    "open_logs": {"enabled": true, "reason": "", "paths": {"directory": "/tmp/vc-home/server", "stdout": "/tmp/vc-home/server/supervisor.stdout.log", "stderr": "/tmp/vc-home/server/supervisor.stderr.log"}}
  }
}
"""#.data(using: .utf8)

let malformedEnvelope = #"""
{
  "schema": "vibecrafted.caretaker.v1",
  "actions": {
    "open_console": {
      "enabled": true,
      "reason": "",
      "url": "file:///tmp/not-a-server"
    }
  }
}
"""#.data(using: .utf8)

let scenario = CommandLine.arguments[1]
let caretaker: Data?
let action: ServerLifecycleAction?
let ready: Bool

switch scenario {
case "healthy":
  caretaker = healthyEnvelope
  action = nil
  ready = true
case "down":
  caretaker = downEnvelope
  action = nil
  ready = true
case "stopped":
  caretaker = stoppedEnvelope
  action = nil
  ready = true
case "degraded":
  caretaker = degradedEnvelope
  action = nil
  ready = true
case "malformed":
  caretaker = malformedEnvelope
  action = nil
  ready = true
case "garbage":
  caretaker = "{not json".data(using: .utf8)
  action = nil
  ready = true
case "absent":
  caretaker = nil
  action = nil
  ready = true
case "transition":
  caretaker = healthyEnvelope
  action = .restart
  ready = true
default:
  caretaker = nil
  action = nil
  ready = false
}

let state = deriveServerMenuState(
  caretakerData: caretaker,
  actionInFlight: action,
  runtimeReady: ready)
print(state.header)
print(state.detail)
print(state.health.rawValue)
print("\(state.canStart),\(state.canStop),\(state.canRestart)")
print(serverActionArguments(for: .start).joined(separator: " "))
print(serverActionArguments(for: .stop).joined(separator: " "))
print(serverActionArguments(for: .restart).joined(separator: " "))
print(serverCaretakerArguments().joined(separator: " "))
let logs = decodeServerLogs(
  data: #"{"directory":"/tmp/vc-home/server","stdout":"/tmp/vc-home/server/supervisor.stdout.log","stderr":"/tmp/vc-home/server/supervisor.stderr.log"}"#.data(using: .utf8)!)!
print(logs.directory.path)
print(caretakerDiagnosticsLines(data: caretaker).joined(separator: " | "))
let navigation = resolveServerNavigation(caretakerData: caretaker)
print(navigation.server?.absoluteString ?? "nil")
print(navigation.workspaces?.absoluteString ?? "nil")
print(navigation.unavailableReason ?? "")
'''


@pytest.fixture(scope="module")
def policy_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        pytest.skip("swiftc is required for the native server menu contract")
    build = tmp_path_factory.mktemp("server-menu-policy")
    main = build / "main.swift"
    main.write_text(MAIN_SWIFT, encoding="utf-8")
    binary = build / "server-menu-policy"
    subprocess.run(
        [swiftc, str(POLICY), str(main), "-o", str(binary)],
        check=True,
        cwd=REPO_ROOT,
    )
    return binary


def _run_policy(binary: Path, scenario: str) -> list[str]:
    return subprocess.run(
        [str(binary), scenario],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()


def test_server_menu_renders_the_caretaker_verdict_verbatim(
    policy_binary: Path,
) -> None:
    lines = _run_policy(policy_binary, "healthy")
    assert lines[:4] == [
        "VC Server: HEALTHY · 127.0.0.1:4107",
        "Supervisor PID 123",
        "healthy",
        "false,true,true",
    ]
    assert lines[4:8] == [
        "server service start",
        "server service stop",
        "server service restart",
        "server caretaker --json",
    ]
    assert lines[8] == "/tmp/vc-home/server"
    diagnostics = lines[9]
    assert "VC Server: HEALTHY · 127.0.0.1:4107" in diagnostics
    assert "Supervisor PID: 123" in diagnostics
    assert "Server PID: 125" in diagnostics
    assert "Guardian PID: 124" in diagnostics
    assert "Endpoint: 127.0.0.1:4107" in diagnostics
    assert "Status receipt: /tmp/vc-home/server/supervisor.status.json" in diagnostics
    assert lines[10:12] == [
        "http://127.0.0.1:4107",
        "http://127.0.0.1:4107/workspaces",
    ]
    assert lines[12] == ""


def test_server_menu_exposes_the_server_when_it_is_down(policy_binary: Path) -> None:
    """The acceptance case: server down must not crash the tray or go silent.

    The envelope still answers (the caretaker builds it even when the port is
    silent), so the menu renders the derived UNREACHABLE verdict and offers
    the honest recovery verbs — start and restart — instead of a blank or a
    crash.
    """
    lines = _run_policy(policy_binary, "down")
    assert lines[:4] == [
        "VC Server: UNREACHABLE · 127.0.0.1:4107",
        "worker failed",
        "failed",
        "true,false,true",
    ]
    assert "[error] server_unreachable: worker failed" in lines[9]
    assert lines[10:12] == ["nil", "nil"]
    assert "not answering" in lines[12]


def test_server_navigation_rejects_malformed_non_http_configuration(
    policy_binary: Path,
) -> None:
    lines = _run_policy(policy_binary, "malformed")
    assert lines[10:12] == ["nil", "nil"]
    assert lines[12] == "The caretaker returned a malformed server URL."


def test_server_menu_marks_an_intentional_stop_neutral(policy_binary: Path) -> None:
    """Stopped on purpose is gray with start offered — not a red crash row."""
    lines = _run_policy(policy_binary, "stopped")
    assert lines[:4] == [
        "VC Server: STOPPED · 127.0.0.1:4107",
        "Service is intentionally stopped",
        "neutral",
        "true,false,false",
    ]


def test_server_menu_surfaces_degraded_upkeep_as_attention(policy_binary: Path) -> None:
    lines = _run_policy(policy_binary, "degraded")
    assert lines[:4] == [
        "VC Server: HEALTHY · 127.0.0.1:4107 · 1 upkeep item",
        "events.jsonl is 16 MiB; rotation is overdue",
        "transitioning",
        "false,true,true",
    ]


def test_server_menu_is_honest_when_the_caretaker_is_absent(
    policy_binary: Path,
) -> None:
    lines = _run_policy(policy_binary, "absent")
    assert lines[:4] == [
        "VC Server: CARETAKER UNAVAILABLE",
        "The canonical caretaker did not answer — the runtime may be missing or broken",
        "failed",
        "false,false,false",
    ]
    assert "has not published a reading" in lines[9]


def test_server_menu_is_honest_when_the_envelope_is_garbage(
    policy_binary: Path,
) -> None:
    """A corrupt envelope is a reported condition, never a crashed status item."""
    lines = _run_policy(policy_binary, "garbage")
    assert lines[:4] == [
        "VC Server: CARETAKER UNAVAILABLE",
        "The canonical caretaker did not answer — the runtime may be missing or broken",
        "failed",
        "false,false,false",
    ]


def test_server_menu_disables_duplicate_transition_actions(policy_binary: Path) -> None:
    lines = _run_policy(policy_binary, "transition")
    assert lines[:4] == [
        "VC Server: RESTARTING…",
        "Waiting for the installed service owner",
        "transitioning",
        "false,false,false",
    ]


def test_server_menu_waits_for_runtime_onboarding(policy_binary: Path) -> None:
    lines = _run_policy(policy_binary, "runtime")
    assert lines[:4] == [
        "VC Server: WAITING FOR RUNTIME",
        "Runtime onboarding has not completed",
        "checking",
        "false,false,false",
    ]
