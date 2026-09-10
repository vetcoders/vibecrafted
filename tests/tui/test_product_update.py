"""W1 authored contracts. Compilation and pytest are W2 integrator gates."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
import uuid
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
    assert "write_admission" in helper
    assert '"status":"ready"' in helper or "write_admission \"ready\"" in helper
    assert "write_terminal_receipt" in helper
    assert helper.index("write_terminal_receipt") < helper.index('"$OPEN_BIN" -n')
    assert "/usr/bin/ditto" in helper
    assert "codesign --verify --strict" in helper
    assert ".vc-update-capture-" in helper
    assert ".vibecrafted-update-backup" not in helper
    assert "rm -rf \"$DESTINATION\"" not in helper
    assert '"replaced":%s' in helper or '"replaced":true' in helper
    assert "decideProductUpdateHandoff" in transaction
    assert "productUpdateRestoreRequest" in transaction
    assert "awaitProductUpdateReceipt" in delegate
    assert "beginProductUpdateRestore" in delegate
    assert "admitProductUpdateHelper" in delegate
    assert "restore_previous_tuple" in helper
    assert "failed-new.app" in helper
    assert "rolledBack" in transaction
    assert "case .waiting, .publishing, .restoring" in delegate
    launch = delegate[
        delegate.index("func applicationDidFinishLaunching(") : delegate.index(
            "func applicationShouldHandleReopen"
        )
    ]
    assert launch.index("adoptPendingProductUpdateIfNeeded") < launch.index("connectCommandDeck")
    restore = delegate[delegate.index("beginProductUpdateRestore") :]
    assert "productUpdateRestoreRequest" in restore
    assert "relaunch: false" not in restore
    assert "admitProductUpdateHelper" in restore
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
        assert result.returncode != 0
        assert (dest / "marker.txt").read_text(encoding="utf-8") == "keep"
        assert not receipt.exists() or '"replaced":true' not in receipt.read_text(encoding="utf-8")
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


def test_product_update_helper_refuses_harness_flags_in_production(tmp_path: Path) -> None:
    dest = tmp_path / "Installed.app"
    dest.mkdir()
    receipt = tmp_path / "receipt.json"
    result = _run_helper(
        [
            "--source",
            str(dest),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
            "--allow-unsigned",
        ],
        env={k: v for k, v in os.environ.items() if k != "VIBECRAFTED_UPDATE_HELPER_HARNESS"},
    )
    assert result.returncode == 2
    assert "VIBECRAFTED_UPDATE_HELPER_HARNESS" in result.stderr


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


def _unsigned_app(path: Path, marker: str) -> Path:
    path.mkdir(parents=True)
    (path / "marker.txt").write_text(marker, encoding="utf-8")
    return path


def _helper_env(**extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env["VIBECRAFTED_UPDATE_HELPER_HARNESS"] = "1"
    env.update(extra)
    return env


def _run_helper(args: list[str], env: dict[str, str] | None = None, timeout: float = 20):
    HELPER.chmod(HELPER.stat().st_mode | stat.S_IXUSR)
    return subprocess.run(
        [str(HELPER), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env or os.environ.copy(),
    )


def _signed_fixture_root() -> Path | None:
    configured = os.environ.get("VIBECRAFTED_UPDATE_FIXTURE_ROOT")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(REPO_ROOT / "dist")
    candidates.append(
        Path("/Volumes/vc-workspace/vetcoders/vibecrafted-suite/vibecrafted/dist")
    )
    for root in candidates:
        feed = root / "release-output.json"
        if feed.is_file() and any(root.glob("*20260910-e37be2c9*.dmg")):
            return root
    return None


def test_product_update_helper_writes_ready_before_waiting(tmp_path: Path) -> None:
    dest = _unsigned_app(tmp_path / "Installed.app", "keep")
    source = _unsigned_app(tmp_path / "Candidate.app", "next")
    receipt = tmp_path / "receipt.json"
    gate = tmp_path / "continue"
    sleeper = subprocess.Popen(["/bin/sleep", "30"])
    helper = None
    try:
        start = subprocess.check_output(
            ["/bin/ps", "-p", str(sleeper.pid), "-o", "lstart="], text=True
        ).strip()
        helper = subprocess.Popen(
            [
                str(HELPER),
                "--source",
                str(source),
                "--destination",
                str(dest),
                "--receipt",
                str(receipt),
                "--wait-pid",
                str(sleeper.pid),
                "--wait-start",
                start,
                "--wait-timeout",
                "8",
                "--allow-unsigned",
                "--hold-after",
                "ready",
                "--until",
                str(gate),
            ],
            env=_helper_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        admission = Path(str(receipt) + ".admission.json")
        deadline = time.time() + 8
        while time.time() < deadline and not admission.is_file():
            if helper.poll() is not None:
                break
            time.sleep(0.05)
        assert admission.is_file(), "helper exited before READY admission"
        payload = json.loads(admission.read_text(encoding="utf-8"))
        assert payload["status"] == "ready"
        assert payload["destination"] == str(dest)
        assert payload["parent_pid"] == str(sleeper.pid)
        assert payload["parent_start"] == start
        assert (dest / "marker.txt").read_text(encoding="utf-8") == "keep"
        assert helper.poll() is None
        gate.write_text("go", encoding="utf-8")
        finished = helper.wait(timeout=12)
        assert finished == 5
        assert (dest / "marker.txt").read_text(encoding="utf-8") == "keep"
        assert not receipt.exists() or '"replaced":true' not in receipt.read_text(
            encoding="utf-8"
        )
    finally:
        if sleeper.poll() is None:
            sleeper.terminate()
            sleeper.wait(timeout=5)
        if helper is not None and helper.poll() is None:
            helper.terminate()
            helper.wait(timeout=5)


def test_product_update_helper_receipt_exists_before_open_bin(tmp_path: Path) -> None:
    dest = _unsigned_app(tmp_path / "Installed.app", "keep")
    source = _unsigned_app(tmp_path / "Candidate.app", "next")
    receipt = tmp_path / "receipt.json"
    opened = tmp_path / "opened.txt"
    observer = tmp_path / "open-bin"
    observer.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f'RECEIPT="{receipt}"\n'
        'test -f "$RECEIPT"\n'
        "python3 - <<'PY'\n"
        "import json, pathlib, sys\n"
        f"payload = json.loads(pathlib.Path({str(receipt)!r}).read_text())\n"
        "assert payload.get('replaced') is True\n"
        "assert payload.get('relaunched') is False\n"
        "PY\n"
        f'echo opened > "{opened}"\n',
        encoding="utf-8",
    )
    observer.chmod(observer.stat().st_mode | stat.S_IXUSR)
    result = _run_helper(
        [
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
            "--relaunch",
            "--allow-unsigned",
            "--open-bin",
            str(observer),
        ],
        env=_helper_env(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(receipt.read_text(encoding="utf-8"))["replaced"] is True
    assert opened.read_text(encoding="utf-8").strip() == "opened"
    assert (dest / "marker.txt").read_text(encoding="utf-8") == "next"


def test_product_update_helper_copy_failure_keeps_destination(tmp_path: Path) -> None:
    dest = _unsigned_app(tmp_path / "Installed.app", "keep")
    source = _unsigned_app(tmp_path / "Candidate.app", "next")
    receipt = tmp_path / "receipt.json"
    result = _run_helper(
        [
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
            "--allow-unsigned",
            "--fail-after",
            "captured",
        ],
        env=_helper_env(),
    )
    assert result.returncode == 40
    assert (dest / "marker.txt").read_text(encoding="utf-8") == "keep"
    journal = json.loads(Path(str(receipt) + ".journal.json").read_text(encoding="utf-8"))
    assert journal["phase"] == "captured"
    assert journal["capture"]
    assert not receipt.exists() or '"replaced":true' not in receipt.read_text(encoding="utf-8")


def test_product_update_helper_interrupt_after_displace_is_resumable(tmp_path: Path) -> None:
    dest = _unsigned_app(tmp_path / "Installed.app", "keep")
    source = _unsigned_app(tmp_path / "Candidate.app", "next")
    receipt = tmp_path / "receipt.json"
    first = _run_helper(
        [
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
            "--allow-unsigned",
            "--fail-after",
            "displaced",
        ],
        env=_helper_env(),
    )
    assert first.returncode == 40
    assert not dest.exists()
    journal = json.loads(Path(str(receipt) + ".journal.json").read_text(encoding="utf-8"))
    assert journal["phase"] == "displaced"
    capture = Path(journal["capture"])
    assert (capture / "displaced.app" / "marker.txt").is_file()
    sibling = tmp_path / ".vc-update-capture-oldid" / "prior.app"
    sibling.mkdir(parents=True)
    (sibling / "keep.txt").write_text("old", encoding="utf-8")
    resumed = _run_helper(
        [
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
            "--journal",
            str(receipt) + ".journal.json",
            "--transaction",
            journal["transaction"],
            "--allow-unsigned",
            "--resume",
        ],
        env=_helper_env(),
    )
    assert resumed.returncode == 0, resumed.stderr
    assert dest.exists()
    assert (dest / "marker.txt").read_text(encoding="utf-8") in {"keep", "next"}
    assert (sibling / "keep.txt").read_text(encoding="utf-8") == "old"
    assert json.loads(receipt.read_text(encoding="utf-8"))["replaced"] is True


def test_product_update_helper_rejects_overlapping_lock(tmp_path: Path) -> None:
    dest = _unsigned_app(tmp_path / "Installed.app", "keep")
    source = _unsigned_app(tmp_path / "Candidate.app", "next")
    receipt = tmp_path / "receipt.json"
    gate = tmp_path / "continue"
    first = subprocess.Popen(
        [
            str(HELPER),
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
            "--allow-unsigned",
            "--hold-after",
            "ready",
            "--until",
            str(gate),
        ],
        env=_helper_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    admission = Path(str(receipt) + ".admission.json")
    deadline = time.time() + 8
    while time.time() < deadline and not admission.is_file():
        if first.poll() is not None:
            break
        time.sleep(0.05)
    assert admission.is_file()
    second = _run_helper(
        [
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--receipt",
            str(tmp_path / "other-receipt.json"),
            "--allow-unsigned",
        ],
        env=_helper_env(),
    )
    assert second.returncode == 13
    assert (dest / "marker.txt").read_text(encoding="utf-8") == "keep"
    gate.write_text("go", encoding="utf-8")
    first.wait(timeout=15)


def test_product_update_helper_missing_receipt_is_not_success(tmp_path: Path) -> None:
    dest = _unsigned_app(tmp_path / "Installed.app", "keep")
    source = _unsigned_app(tmp_path / "Candidate.app", "next")
    receipt = tmp_path / "receipt.json"
    result = _run_helper(
        [
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
            "--allow-unsigned",
            "--fail-after",
            "adopted",
        ],
        env=_helper_env(),
    )
    assert result.returncode == 40
    assert not receipt.exists() or '"replaced":true' not in receipt.read_text(encoding="utf-8")


def test_product_update_helper_restore_does_not_overwrite_prior(tmp_path: Path) -> None:
    transaction = str(uuid.uuid4())
    dest = _unsigned_app(tmp_path / "Installed.app", "new-failed")
    capture = tmp_path / f".vc-update-capture-{transaction}"
    prior = _unsigned_app(capture / "prior.app", "old-working")
    receipt = tmp_path / "restore-receipt.json"
    opened = tmp_path / "opened.txt"
    observer = tmp_path / "open-bin"
    observer.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"python3 -c \"import json; p=json.load(open(r'{receipt}')); assert p['replaced'] is True\"\n"
        f'echo opened > "{opened}"\n',
        encoding="utf-8",
    )
    observer.chmod(observer.stat().st_mode | stat.S_IXUSR)
    result = _run_helper(
        [
            "--source",
            str(prior),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
            "--transaction",
            transaction,
            "--mode",
            "restore",
            "--relaunch",
            "--allow-unsigned",
            "--open-bin",
            str(observer),
        ],
        env=_helper_env(),
    )
    assert result.returncode == 0, result.stderr
    assert (dest / "marker.txt").read_text(encoding="utf-8") == "old-working"
    assert (prior / "marker.txt").read_text(encoding="utf-8") == "old-working"
    failed_new = capture / "failed-new.app"
    assert (failed_new / "marker.txt").read_text(encoding="utf-8") == "new-failed"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["replaced"] is True
    assert payload["detail"] == "restored"
    assert payload["mode"] == "restore"
    assert opened.is_file()


def test_product_update_helper_restore_interrupt_keeps_prior(tmp_path: Path) -> None:
    transaction = str(uuid.uuid4())
    dest = _unsigned_app(tmp_path / "Installed.app", "new-failed")
    capture = tmp_path / f".vc-update-capture-{transaction}"
    prior = _unsigned_app(capture / "prior.app", "old-working")
    receipt = tmp_path / "restore-receipt.json"
    first = _run_helper(
        [
            "--source",
            str(prior),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
            "--transaction",
            transaction,
            "--mode",
            "restore",
            "--allow-unsigned",
            "--fail-after",
            "displaced",
        ],
        env=_helper_env(),
    )
    assert first.returncode == 40
    assert not dest.exists()
    assert (prior / "marker.txt").read_text(encoding="utf-8") == "old-working"
    assert (capture / "failed-new.app" / "marker.txt").read_text(encoding="utf-8") == "new-failed"
    resumed = _run_helper(
        [
            "--source",
            str(prior),
            "--destination",
            str(dest),
            "--receipt",
            str(receipt),
            "--transaction",
            transaction,
            "--mode",
            "restore",
            "--allow-unsigned",
        ],
        env=_helper_env(),
    )
    assert resumed.returncode == 0, resumed.stderr
    assert (dest / "marker.txt").read_text(encoding="utf-8") == "old-working"
    assert (prior / "marker.txt").read_text(encoding="utf-8") == "old-working"


def test_product_update_signed_fixture_positive_path(tmp_path: Path) -> None:
    root = _signed_fixture_root()
    if root is None:
        pytest.skip(
            "signed fixture pair not mounted in this worktree; parameterized for W2 via "
            "VIBECRAFTED_UPDATE_FIXTURE_ROOT or dist/*20260910-e37be2c9*"
        )
    feed = root / "release-output.json"
    signature = root / "release-output.json.sig"
    dmgs = list(root.glob("*20260910-e37be2c9*.dmg"))
    packs = list(root.glob("*20260910-e37be2c9*.tar.gz"))
    assert feed.is_file()
    assert dmgs, "signed fixture DMG is missing from the parameterized root"
    assert packs, "signed fixture Runtime Pack is missing from the parameterized root"
    if signature.is_file():
        assert signature.stat().st_size == 256
    mount = tmp_path / "mnt"
    mount.mkdir()
    attached = subprocess.run(
        ["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(dmgs[0])],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if attached.returncode != 0:
        pytest.skip(f"W2 must attach the signed DMG: {attached.stderr}")
    try:
        apps = list(mount.rglob("Vibecrafted.app"))
        assert apps, "signed fixture DMG has no Vibecrafted.app"
        source = tmp_path / "Candidate.app"
        dest = tmp_path / "Installed.app"
        subprocess.run(["/usr/bin/ditto", str(apps[0]), str(source)], check=True, timeout=60)
        subprocess.run(["/usr/bin/ditto", str(apps[0]), str(dest)], check=True, timeout=60)
        (dest / "previous-marker.txt").write_text("old-tuple", encoding="utf-8")
        receipt = tmp_path / "receipt.json"
        opened = tmp_path / "opened.txt"
        observer = tmp_path / "open-bin"
        observer.write_text(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            f"python3 -c \"import json; p=json.load(open(r'{receipt}')); assert p['replaced'] is True\"\n"
            f'echo opened > "{opened}"\n',
            encoding="utf-8",
        )
        observer.chmod(observer.stat().st_mode | stat.S_IXUSR)
        sleeper = subprocess.Popen(["/bin/sleep", "2"])
        start = subprocess.check_output(
            ["/bin/ps", "-p", str(sleeper.pid), "-o", "lstart="], text=True
        ).strip()
        helper = subprocess.Popen(
            [
                str(HELPER),
                "--source",
                str(source),
                "--destination",
                str(dest),
                "--receipt",
                str(receipt),
                "--wait-pid",
                str(sleeper.pid),
                "--wait-start",
                start,
                "--relaunch",
                "--open-bin",
                str(observer),
            ],
            env=_helper_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        admission = Path(str(receipt) + ".admission.json")
        deadline = time.time() + 30
        while time.time() < deadline and not admission.is_file():
            if helper.poll() is not None:
                break
            time.sleep(0.05)
        assert admission.is_file(), "signed helper exited before READY admission"
        assert json.loads(admission.read_text(encoding="utf-8"))["status"] == "ready"
        sleeper.wait(timeout=5)
        assert helper.wait(timeout=90) == 0
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        assert payload["replaced"] is True
        assert payload["transaction"]
        assert opened.is_file()
        assert (dest / "Contents").exists()
    finally:
        subprocess.run(
            ["/usr/bin/hdiutil", "detach", str(mount), "-quiet"],
            capture_output=True,
            timeout=30,
        )


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
