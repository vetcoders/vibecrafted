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
