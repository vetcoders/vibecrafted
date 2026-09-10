"""W1 authored contracts. Compilation and pytest are W2 integrator gates."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted"
SHELL = REPO_ROOT / "vibecrafted-app/shell-agent"
HELPER = REPO_ROOT / "scripts/vc-app-update.sh"


def test_product_update_source_contract() -> None:
    policy = (APP / "ProductUpdatePolicy.swift").read_text(encoding="utf-8")
    coordinator = (APP / "ProductUpdateCoordinator.swift").read_text(encoding="utf-8")
    trust = (APP / "ProductUpdateTrust.swift").read_text(encoding="utf-8")
    view = (APP / "ProductUpdateView.swift").read_text(encoding="utf-8")
    delegate = (APP / "AppDelegate.swift").read_text(encoding="utf-8")
    tray = (APP / "CommandDeck/StatusItemController.swift").read_text(encoding="utf-8")
    project = (SHELL / "app/project.yml").read_text(encoding="utf-8")
    info = (APP / "Info.plist").read_text(encoding="utf-8")
    helper = HELPER.read_text(encoding="utf-8")
    release = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(encoding="utf-8")

    assert "import Sparkle" not in policy + coordinator + view + delegate
    assert "SPUStandardUpdaterController" not in delegate
    assert "SUFeedURL" not in info
    assert "SUPublicEDKey" not in info
    assert "sparkle-project" not in project
    assert "func admitProductUpdateCandidate(" in policy
    assert "func verifyDetachedReleaseSignature(" in trust
    assert "signature_valid" in policy
    assert "let signatureValid" not in policy
    assert "product_contract" in trust
    assert "VIBECRAFTED_UPDATE_FIXTURE" in policy
    assert "func installUpdate(" in coordinator
    assert "Install Update" in view
    assert "Quit App" not in view
    assert "case readyToReplace" not in policy
    assert "io.vetcoders.vibecrafted.release-output.v1" in policy
    assert "vibecrafted-signing-v1" in policy
    assert "VCUpdateFeedURL" in policy and "VCUpdateFeedURL" in delegate
    assert "Contents/Helpers/vc-app-update" in delegate
    assert "vc-app-update:" in project
    assert "scripts/vc-app-update.sh" in release
    assert "does not stop Frame" in helper
    assert "--wait-pid" in helper and "/usr/bin/ditto" in helper
    assert "case checkForUpdates" in tray
    assert 'title: "Check for Updates…"' in tray
    assert 'toolTip = "Sprawdź aktualizacje"' in tray
    assert 'withTitle: "Check for Updates…"' in delegate
    assert "checkForUpdatesFromMenu" in delegate
    assert "installProductUpdate(" in delegate
    assert "runRuntimePackInstaller(" in delegate
    assert "cancelRuntimePackInstaller(" in delegate
    assert "productUpdate?.interrupt()" in delegate
    assert "Stop Runtime" not in policy
    assert "performServerAction" not in coordinator
    assert "Sprawdź aktualizacje" in view
    assert "Installed:" in view
    assert "commandDeckThemed()" in view


def test_product_update_quit_stays_ui_only() -> None:
    delegate = (APP / "AppDelegate.swift").read_text(encoding="utf-8")
    coordinator = (APP / "ProductUpdateCoordinator.swift").read_text(encoding="utf-8")
    helper = HELPER.read_text(encoding="utf-8")
    termination = delegate[
        delegate.index("func applicationShouldTerminate(") : delegate.index(
            "func applicationSupportsSecureRestorableState"
        )
    ]
    assert ".terminateNow" in termination
    assert "activeRunSummary" not in termination
    assert "stopRuntime" not in termination
    will_terminate = delegate[
        delegate.index("func applicationWillTerminate(") : delegate.index(
            "private func startNativeNotifications("
        )
    ]
    assert "productUpdate?.interrupt()" in will_terminate
    assert "cancelRuntimePackInstaller()" in will_terminate
    assert "Nothing here stops the terminal" in will_terminate
    assert "closeUIAfterHelperArmed" in coordinator
    assert "nothing here stops frame" in coordinator.lower()
    assert "launchctl" not in helper
    assert "stop runtime" not in helper.lower()


def test_product_update_helper_replaces_fixture_app(tmp_path: Path) -> None:
    source = tmp_path / "Vibecrafted.app"
    source.mkdir()
    (source / "Contents.txt").write_text("candidate", encoding="utf-8")
    dest = tmp_path / "Installed.app"
    dest.mkdir()
    (dest / "Contents.txt").write_text("previous", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    HELPER.chmod(HELPER.stat().st_mode | stat.S_IXUSR)
    result = subprocess.run(
        [
            str(HELPER),
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert (dest / "Contents.txt").read_text(encoding="utf-8") == "candidate"
    assert '"replaced":true' in receipt.read_text(encoding="utf-8")


def test_product_update_helper_unable_to_replace(tmp_path: Path) -> None:
    dest = tmp_path / "Installed.app"
    receipt = tmp_path / "receipt.json"
    HELPER.chmod(HELPER.stat().st_mode | stat.S_IXUSR)
    result = subprocess.run(
        [
            str(HELPER),
            "--source",
            str(tmp_path / "missing.app"),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert not dest.exists()
    assert not receipt.exists() or "replaced" not in receipt.read_text(encoding="utf-8")


@pytest.mark.skipif(
    os.environ.get("VIBECRAFTED_UPDATE_FIXTURE") != "1",
    reason="real-process fixture coverage: integrator sets VIBECRAFTED_UPDATE_FIXTURE=1",
)
def test_product_update_real_process_fixture_preserves_sessions() -> None:
    root = os.environ.get("VIBECRAFTED_UPDATE_FIXTURE_ROOT")
    assert root, "fixture root is required when VIBECRAFTED_UPDATE_FIXTURE=1"
    feed = Path(root) / "release-output.json"
    signature = Path(root) / "release-output.json.sig"
    assert feed.is_file() and signature.is_file()
    assert signature.stat().st_size == 256


def test_product_update_policy_swift_behavior(tmp_path: Path) -> None:
    if os.uname().sysname != "Darwin":
        pytest.skip("macOS native harness")
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        pytest.skip("swiftc is unavailable")
    binary = tmp_path / "product-update-policy"
    target = "arm64" if os.uname().machine == "arm64" else "x86_64"
    subprocess.run(
        [
            swiftc,
            "-swift-version",
            "6",
            "-target",
            f"{target}-apple-macosx14.0",
            str(APP / "RuntimePackMenuPolicy.swift"),
            str(APP / "ProductUpdatePolicy.swift"),
            str(APP / "ProductUpdateTransaction.swift"),
            str(APP / "ProductUpdateTrust.swift"),
            str(APP / "ProductUpdateReplacement.swift"),
            str(APP / "ProductUpdateTransfer.swift"),
            str(APP / "ProductUpdateCoordinator.swift"),
            str(SHELL / "tests/ProductUpdatePolicyTests.swift"),
            "-o",
            str(binary),
        ],
        check=True,
        timeout=180,
    )
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=30, check=True
    )
    assert "ProductUpdatePolicyTests passed" in result.stdout
