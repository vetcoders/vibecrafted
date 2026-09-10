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
    process = (APP / "ProductUpdateProcess.swift").read_text(encoding="utf-8")
    replacement = (APP / "ProductUpdateReplacement.swift").read_text(encoding="utf-8")
    transaction = (APP / "ProductUpdateTransaction.swift").read_text(encoding="utf-8")
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
    assert "productUpdateExpectedVerifierOwner" in policy
    assert "productUpdateExpectedTeamID" in policy
    assert "codesignTeamID" in policy
    assert "VIBECRAFTED_UPDATE_FIXTURE" in policy
    assert "func installUpdate(" in coordinator
    assert "hasAdmittedHelperHandoff" in coordinator
    assert "noteUIShutdownPreservingHandoff" in coordinator
    assert "ProductUpdateReplacementAdmission" in coordinator
    assert "Install Update" in view
    assert "Quit App" not in view
    assert "case readyToReplace" not in policy
    assert "io.vetcoders.vibecrafted.release-output.v1" in policy
    assert "vibecrafted-signing-v1" in policy
    assert "VCUpdateFeedURL" in policy and "VCUpdateFeedURL" in delegate
    assert "Contents/Helpers/vc-app-update" in delegate
    assert "vc-app-update:" not in project
    assert "BUILT_PRODUCTS_DIR/vc-app-update" not in project
    assert "scripts/vc-app-update.sh" in project
    assert "scripts/vc-app-update.sh" in release
    assert "dist/vc-app-update" not in release
    assert not (SHELL / "app/Helpers/vc-app-update/main.swift").exists()
    assert "does not stop Frame" in helper
    assert "--wait-pid" in helper and "--wait-start" in helper
    assert "wait_for_identity" in helper
    assert "/usr/bin/ditto" in helper
    assert "codesign --verify --strict" in helper
    assert ".vc-update-capture-" in helper
    assert ".vibecrafted-update-backup" not in helper
    assert "rm -rf \"$DESTINATION\"" not in helper
    assert "replaced\":true" in helper
    assert "func runProductUpdateBoundProcess(" in process
    assert "func productUpdateStagedRelativePath(" in process
    assert "VIBECRAFTED_PYTHON" in process
    assert "VIBECRAFTED_PYTHON" not in trust
    assert "makeVerifiedProductUpdateProof" not in trust
    assert "makeVerifiedProductUpdateProof" not in delegate
    assert "--verify" in trust and "--strict" in trust
    assert "receiptMissing" in replacement
    assert "productUpdateRepositoryHelperScript" in replacement
    assert "return productUpdateRepositoryHelperScript()" not in replacement
    assert "helperAdmitted" in transaction
    assert "case checkForUpdates" in tray
    assert 'title: "Check for Updates…"' in tray
    assert 'toolTip = "Sprawdź aktualizacje"' in tray
    assert 'withTitle: "Check for Updates…"' in delegate
    assert "checkForUpdatesFromMenu" in delegate
    assert "installProductUpdate(" in delegate
    assert "runRuntimePackInstaller(" in delegate
    assert "cancelRuntimePackInstaller(" in delegate
    assert "noteUIShutdownPreservingHandoff" in delegate
    assert "hasAdmittedHelperHandoff" in delegate
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
    assert "hasAdmittedHelperHandoff" in will_terminate
    assert "noteUIShutdownPreservingHandoff" in will_terminate
    assert "productUpdate?.interrupt()" in will_terminate
    assert "cancelRuntimePackInstaller()" in will_terminate
    assert "Nothing here stops the terminal" in will_terminate
    assert "closeUIAfterHelperArmed" in coordinator
    assert "An admitted helper is not owned" in coordinator
    assert "nothing here stops frame" in coordinator.lower() or "Frame, terminals, agents" in coordinator
    assert "launchctl" not in helper
    assert "stop runtime" not in helper.lower()
    assert "trap '' HUP" in helper


def test_product_update_helper_refuses_unsigned_source(tmp_path: Path) -> None:
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
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert (dest / "Contents.txt").read_text(encoding="utf-8") == "previous"
    assert not receipt.exists()


def test_product_update_helper_times_out_live_parent(tmp_path: Path) -> None:
    dest = tmp_path / "Installed.app"
    dest.mkdir()
    (dest / "marker.txt").write_text("keep", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    HELPER.chmod(HELPER.stat().st_mode | stat.S_IXUSR)
    sleeper = subprocess.Popen(["/bin/sleep", "30"])
    try:
        start = subprocess.check_output(
            ["/bin/ps", "-p", str(sleeper.pid), "-o", "lstart="],
            text=True,
        ).strip()
        result = subprocess.run(
            [
                str(HELPER),
                "--source",
                str(dest),
                "--destination",
                str(dest),
                "--receipt",
                str(receipt),
                "--wait-pid",
                str(sleeper.pid),
                "--wait-start",
                start,
                "--wait-timeout",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 5
        assert (dest / "marker.txt").read_text(encoding="utf-8") == "keep"
        assert not receipt.exists()
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)


def test_product_update_helper_keeps_previous_capture(tmp_path: Path) -> None:
    dest = tmp_path / "Installed.app"
    dest.mkdir()
    (dest / "marker.txt").write_text("keep", encoding="utf-8")
    old = tmp_path / ".vc-update-capture-oldid" / "prior.app"
    old.mkdir(parents=True)
    (old / "keep.txt").write_text("old", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    HELPER.chmod(HELPER.stat().st_mode | stat.S_IXUSR)
    sleeper = subprocess.Popen(["/bin/sleep", "30"])
    try:
        start = subprocess.check_output(
            ["/bin/ps", "-p", str(sleeper.pid), "-o", "lstart="],
            text=True,
        ).strip()
        subprocess.run(
            [
                str(HELPER),
                "--source",
                str(dest),
                "--destination",
                str(dest),
                "--receipt",
                str(receipt),
                "--wait-pid",
                str(sleeper.pid),
                "--wait-start",
                start,
                "--wait-timeout",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert (old / "keep.txt").read_text(encoding="utf-8") == "old"
        assert (dest / "marker.txt").read_text(encoding="utf-8") == "keep"
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)


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
    assert not receipt.exists()


def test_product_update_helper_does_not_synthesize_receipt(tmp_path: Path) -> None:
    stub = tmp_path / "fake-helper.sh"
    stub.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    dest = tmp_path / "Installed.app"
    dest.mkdir()
    (dest / "marker.txt").write_text("keep", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    result = subprocess.run(
        [str(stub)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert not receipt.exists()
    assert (dest / "marker.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(
    os.environ.get("VIBECRAFTED_UPDATE_FIXTURE") != "1",
    reason="signed real-process fixture: W2 supplies the notarized tuple",
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
            str(APP / "ProductUpdateProcess.swift"),
            str(APP / "ProductUpdateCoordinator.swift"),
            str(SHELL / "tests/ProductUpdatePolicyTests.swift"),
            "-o",
            str(binary),
        ],
        check=True,
        timeout=180,
    )
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=90, check=True
    )
    assert "ProductUpdatePolicyTests passed" in result.stdout
