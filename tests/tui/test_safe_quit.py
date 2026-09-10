from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
QUIT_SAFETY = REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/QuitSafety.swift"


def _run_swift_policy(tmp_path: Path, payload: bytes, status: int) -> str:
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        pytest.skip("swiftc is required for the macOS Safe Quit contract")
    main = tmp_path / "main.swift"
    main.write_text(
        r"""
import Foundation

let payload = Data(FileHandle.standardInput.readDataToEndOfFile())
switch decodeRuntimeActivityTruth(data: payload, terminationStatus: Int32(CommandLine.arguments[1])!) {
case .available(let summary):
  print("available:\(summary.lanes):\(summary.worktrees)")
case .unavailable(let reason):
  print("unavailable:\(reason)")
}
""",
        encoding="utf-8",
    )
    binary = tmp_path / "quit-safety"
    subprocess.run(
        [swiftc, str(QUIT_SAFETY), str(main), "-o", str(binary)],
        check=True,
        cwd=REPO_ROOT,
    )
    return (
        subprocess.run(
            [str(binary), str(status)],
            input=payload,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


def test_safe_quit_policy_accepts_zero_active_lanes(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "schema_version": "vibecrafted.lifecycle-activity.v1",
            "summary": {"lanes": 0, "worktrees": 0},
        }
    ).encode()
    assert _run_swift_policy(tmp_path, payload, 0) == "available:0:0"


@pytest.mark.parametrize(
    ("payload", "status", "reason"),
    [
        (b"{}", 7, "exited with status 7"),
        (b"not-json", 0, "malformed JSON"),
    ],
)
def test_safe_quit_policy_fails_safe_when_truth_is_unavailable(
    tmp_path: Path, payload: bytes, status: int, reason: str
) -> None:
    result = _run_swift_policy(tmp_path, payload, status)
    assert result.startswith("unavailable:")
    assert reason in result


def test_app_quit_is_ui_only_and_stop_has_native_confirmation() -> None:
    delegate = (
        REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/AppDelegate.swift"
    ).read_text(encoding="utf-8")
    assert 'process.arguments = ["status", "--activity", "--json"]' in delegate
    assert "func applicationShouldTerminate(" in delegate
    termination = delegate[
        delegate.index("func applicationShouldTerminate(") : delegate.index(
            "func applicationSupportsSecureRestorableState"
        )
    ]
    assert ".terminateNow" in termination
    assert "activeRunSummary" not in termination
    assert "performServerAction" not in termination
    confirmation = delegate[
        delegate.index("private func confirmRuntimeStop()") : delegate.index(
            "private func showNativeMessage"
        )
    ]
    assert "activeRunSummary()" in confirmation
    assert 'alert.addButton(withTitle: "Cancel")' in confirmation
    assert 'alert.addButton(withTitle: "Stop Runtime Service")' in confirmation
    request_quit = delegate[
        delegate.index("@objc private func requestQuit()") : delegate.index(
            "@objc private func checkForUpdatesFromMenu()"
        )
    ]
    assert "NSApp.terminate(nil)" in request_quit
    assert "activeRunSummary" not in request_quit
    assert "productUpdate?.interrupt()" in delegate
    assert "requestUIOnlyQuit: { [weak self] in self?.requestQuit() }" in delegate
    assert "case .checkForUpdates: checkForUpdatesFromMenu()" in delegate
    assert "Stop Runtime" not in request_quit
    install_update = delegate[
        delegate.index("private func installProductUpdate(") : delegate.index(
            "private func showProductUpdatePanel()"
        )
    ]
    assert "Receipts are not deleted here" in delegate
    assert "Refusing to publish a Runtime Pack that does not match" in install_update
    assert "Bundle.main.bundleURL" in install_update
    assert 'arguments: ["--uninstall"]' not in install_update
    assert "performServerAction" not in install_update
