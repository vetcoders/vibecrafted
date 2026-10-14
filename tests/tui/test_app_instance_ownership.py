"""Single user App-instance ownership: source contract and Swift behavior."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "vibecrafted-app/shell-agent"
APP = SHELL / "app/Vibecrafted"


def test_app_delegate_claims_instance_before_tray() -> None:
    delegate = (APP / "AppDelegate.swift").read_text(encoding="utf-8")
    tray = (APP / "CommandDeck/StatusItemController.swift").read_text(encoding="utf-8")
    helper = (APP / "AppInstanceOwnership.swift").read_text(encoding="utf-8")
    did_finish = delegate.split("func applicationDidFinishLaunching")[1].split(
        "func applicationShouldHandleReopen"
    )[0]
    assert "claimUserAppInstanceOrHandoff" in did_finish
    assert "AppInstanceOwnership.decide" in delegate
    assert did_finish.index("claimUserAppInstanceOrHandoff") < did_finish.index(
        "buildStatusItem()"
    )
    assert did_finish.index("--uninstall") < did_finish.index(
        "claimUserAppInstanceOrHandoff"
    )
    assert did_finish.index("--bootstrap-only") < did_finish.index(
        "claimUserAppInstanceOrHandoff"
    )
    assert "kill(" not in helper
    assert "SIGKILL" not in helper
    assert "terminate(" not in helper
    assert "removeItem" not in helper
    assert "instance owner after stale peer" in delegate
    assert "createDirectory(at:" not in helper
    assert "createDirectory(at:" not in did_finish
    assert "StatusItemController.install()" in tray or "func install()" in tray
    assert "process-wide" in f"{tray}\n{helper}".lower()


def test_app_instance_ownership_swift_behavior(tmp_path: Path) -> None:
    if os.uname().sysname != "Darwin":
        pytest.skip("macOS native harness")
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        pytest.skip("swiftc is unavailable")
    binary = tmp_path / "app-instance-ownership"
    target = "arm64" if os.uname().machine == "arm64" else "x86_64"
    subprocess.run(
        [
            swiftc,
            "-swift-version",
            "6",
            "-target",
            f"{target}-apple-macosx14.0",
            str(APP / "AppInstanceOwnership.swift"),
            str(SHELL / "tests/AppInstanceOwnershipTests.swift"),
            "-o",
            str(binary),
        ],
        check=True,
        timeout=180,
    )
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=30, check=True
    )
    assert "AppInstanceOwnershipTests passed" in result.stdout


def test_native_instance_boundaries_typecheck_without_app_build(tmp_path: Path) -> None:
    """Check the actual adapters against AppKit/Security without executing them."""
    if os.uname().sysname != "Darwin":
        pytest.skip("macOS native boundary")
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        pytest.skip("swiftc is unavailable")
    delegate = (APP / "AppDelegate.swift").read_text(encoding="utf-8")
    boundary = delegate.split("  private func claimUserAppInstanceOrHandoff()", 1)[1]
    boundary = boundary.split("  // MARK: - Main Menu", 1)[0]
    adapter = tmp_path / "InstanceBoundary.swift"
    adapter.write_text(
        "import AppKit\nimport Darwin\n"
        "func lifecycleLog(_ event: String) {}\n"
        "@MainActor final class InstanceBoundary {\n"
        "  private func claimUserAppInstanceOrHandoff()" + boundary + "\n}\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            swiftc,
            "-swift-version",
            "6",
            "-typecheck",
            str(APP / "AppInstanceOwnership.swift"),
            str(adapter),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    wrapper = (ROOT / "scripts/install-app.sh").read_text(encoding="utf-8")
    inline = wrapper.split("<<'MAIN_QUIT_SWIFT'\n", 1)[1].split(
        "\nMAIN_QUIT_SWIFT\n", 1
    )[0]
    main = tmp_path / "main.swift"
    main.write_text(
        (APP / "AppInstanceOwnership.swift").read_text(encoding="utf-8")
        + "\n"
        + inline,
        encoding="utf-8",
    )
    subprocess.run(
        [swiftc, "-swift-version", "6", "-typecheck", str(main)],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
