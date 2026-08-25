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


def _run_policy(tmp_path: Path, scenario: str) -> list[str]:
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        pytest.skip("swiftc is required for the native server menu contract")
    main = tmp_path / "main.swift"
    main.write_text(
        r"""
import Foundation

let scenario = CommandLine.arguments[1]
let service: Data?
let receipt: Data?
let action: ServerLifecycleAction?

switch scenario {
case "stopped":
  service = #"{"installed":true,"loaded":false,"supervisor_live":false,"supervisor_verified":false,"supervisor_service_managed":false,"build_current":true,"pair_healthy":false,"supervisor_pid":null}"#.data(using: .utf8)
  receipt = #"{"state":"healthy","endpoint":{"host":"127.0.0.1","port":4107,"url":"http://127.0.0.1:4107"}}"#.data(using: .utf8)
  action = nil
case "healthy":
  service = #"{"installed":true,"loaded":true,"supervisor_live":true,"supervisor_verified":true,"supervisor_service_managed":true,"build_current":true,"pair_healthy":true,"supervisor_pid":123}"#.data(using: .utf8)
  receipt = #"{"state":"healthy","endpoint":{"host":"127.0.0.1","port":4107,"url":"http://127.0.0.1:4107"},"managed_pair":{"guardian_pid":124,"server_pid":125}}"#.data(using: .utf8)
  action = nil
case "transition":
  service = #"{"installed":true,"loaded":true,"supervisor_live":true,"supervisor_verified":true,"supervisor_service_managed":true,"build_current":true,"pair_healthy":true,"supervisor_pid":123}"#.data(using: .utf8)
  receipt = nil
  action = .restart
default:
  service = #"{"installed":true,"loaded":true,"supervisor_live":false,"supervisor_verified":false,"supervisor_service_managed":false,"build_current":false,"pair_healthy":false,"supervisor_pid":null}"#.data(using: .utf8)
  receipt = #"{"state":"backoff","last_error":"worker failed\ntrace"}"#.data(using: .utf8)
  action = nil
}

let state = deriveServerMenuState(
  supervisorData: receipt,
  serviceData: service,
  actionInFlight: action,
  runtimeReady: true)
print(state.header)
print(state.detail)
print(state.health.rawValue)
print("\(state.canStart),\(state.canStop),\(state.canRestart)")
print(serverActionArguments(for: .start).joined(separator: " "))
print(serverActionArguments(for: .stop).joined(separator: " "))
print(serverActionArguments(for: .restart).joined(separator: " "))
let logs = decodeServerLogs(
  data: #"{"directory":"/tmp/vc-home/server","stdout":"/tmp/vc-home/server/supervisor.stdout.log","stderr":"/tmp/vc-home/server/supervisor.stderr.log"}"#.data(using: .utf8)!)!
print(logs.directory.path)
""",
        encoding="utf-8",
    )
    binary = tmp_path / "server-menu-policy"
    subprocess.run(
        [swiftc, str(POLICY), str(main), "-o", str(binary)],
        check=True,
        cwd=REPO_ROOT,
    )
    return subprocess.run(
        [str(binary), scenario],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()


def test_server_menu_policy_routes_canonical_actions_and_logs(tmp_path: Path) -> None:
    lines = _run_policy(tmp_path, "stopped")
    assert lines[:4] == [
        "VC Server: STOPPED · 127.0.0.1:4107",
        "Service is intentionally stopped",
        "neutral",
        "true,false,false",
    ]
    assert lines[4:7] == [
        "server service start",
        "server service stop",
        "server service restart",
    ]
    assert lines[7] == "/tmp/vc-home/server"


def test_server_menu_policy_enables_only_valid_healthy_actions(tmp_path: Path) -> None:
    lines = _run_policy(tmp_path, "healthy")
    assert lines[:4] == [
        "VC Server: HEALTHY · 127.0.0.1:4107",
        "Supervisor PID 123",
        "healthy",
        "false,true,true",
    ]


def test_server_menu_policy_disables_duplicate_transition_actions(
    tmp_path: Path,
) -> None:
    lines = _run_policy(tmp_path, "transition")
    assert lines[:4] == [
        "VC Server: RESTARTING…",
        "Waiting for the installed service owner",
        "transitioning",
        "false,false,false",
    ]


def test_server_menu_policy_surfaces_actionable_failure(tmp_path: Path) -> None:
    lines = _run_policy(tmp_path, "failed")
    assert lines[:4] == [
        "VC Server: NEEDS ATTENTION",
        "worker failed",
        "failed",
        "false,true,true",
    ]
