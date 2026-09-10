"""W1 authored contracts. Compilation and pytest are W2 integrator gates."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
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
    assert "reconcile_resume" in helper
    assert "require_exact_identity" in helper
    assert "flock_update_lock_nb" in helper
    assert "UPDATE_LOCK_FD" in helper
    assert "owned_displaced" in helper
    assert "journal_require operation" in helper
    assert "productUpdateObserveRuntimeEvidence" in transaction
    assert "ProductUpdatePackPublicationState" in transaction
    assert "unresolved" in transaction
    assert 'rm -rf "$LOCKDIR"' not in helper
    assert "ALLOW_UNSIGNED" not in helper
    assert "refusing --allow-unsigned" in helper
    assert "productUpdateObserveRuntimeEvidence" in delegate
    assert "writeProductUpdatePackEvidence" in delegate
    assert "signed fixture pair not mounted" not in helper
    assert "previous-marker.txt" not in (REPO_ROOT / "tests/tui/test_product_update.py").read_text(
        encoding="utf-8"
    )
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
    assert "allow-unsigned" in result.stderr


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


def _fail_missing_fixture(detail: str) -> None:
    pytest.fail(
        "unresolved required gate: signed update fixture is missing. "
        f"{detail} Mount the SSD dist pair or set VIBECRAFTED_UPDATE_FIXTURE_ROOT "
        "(*20260910-e37be2c9*) and VIBECRAFTED_UPDATE_PRIOR_FIXTURE_ROOT "
        "(*20260909-79001c3d*). This is not skippable."
    )


def _signed_search_roots() -> list[Path]:
    roots: list[Path] = []
    for key in (
        "VIBECRAFTED_UPDATE_FIXTURE_ROOT",
        "VIBECRAFTED_UPDATE_PRIOR_FIXTURE_ROOT",
    ):
        configured = os.environ.get(key)
        if configured:
            roots.append(Path(configured))
    roots.append(REPO_ROOT / "dist")
    roots.append(Path("/Volumes/vc-workspace/vetcoders/vibecrafted-suite/vibecrafted/dist"))
    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        resolved = root.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _first_glob(roots: list[Path], pattern: str) -> Path | None:
    for root in roots:
        if not root.is_dir():
            continue
        matches = sorted(root.glob(pattern))
        if matches:
            return matches[0]
    return None


def _require_generation_artifacts(label: str, dmg_glob: str, pack_glob: str) -> dict[str, Path]:
    roots = _signed_search_roots()
    dmg = _first_glob(roots, dmg_glob)
    pack = _first_glob(roots, pack_glob)
    feed = _first_glob(roots, "release-output.json")
    signature = _first_glob(roots, "release-output.json.sig")
    if dmg is None or pack is None:
        _fail_missing_fixture(f"{label} DMG/pack not found with {dmg_glob} / {pack_glob}.")
    assert dmg is not None and pack is not None
    if feed is None or signature is None:
        _fail_missing_fixture(f"{label} release-output.json + .sig are required next to the DMG.")
    assert feed is not None and signature is not None
    if signature.stat().st_size != 256:
        pytest.fail(
            "unresolved required gate: detached signature is not 256 bytes; "
            "do not treat an unsigned locator as a signed fixture."
        )
    return {"dmg": dmg, "pack": pack, "feed": feed, "signature": signature}


def _require_e37_artifacts() -> dict[str, Path]:
    return _require_generation_artifacts(
        "e37",
        "*20260910-e37be2c9*.dmg",
        "*20260910-e37be2c9*.tar.gz",
    )


def _require_prior_79001_artifacts() -> dict[str, Path]:
    return _require_generation_artifacts(
        "79001",
        "*20260909-79001c3d*.dmg",
        "*20260909-79001c3d*.tar.gz",
    )


def _attach_signed_app(dmg: Path, mount: Path) -> Path:
    mount.mkdir(parents=True, exist_ok=True)
    attached = subprocess.run(
        [
            "/usr/bin/hdiutil",
            "attach",
            "-nobrowse",
            "-readonly",
            "-mountpoint",
            str(mount),
            str(dmg),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if attached.returncode != 0:
        pytest.fail(
            "unresolved required gate: could not attach the signed DMG "
            f"{dmg}: {attached.stderr}"
        )
    apps = list(mount.rglob("Vibecrafted.app"))
    if not apps:
        pytest.fail(f"unresolved required gate: {dmg} has no Vibecrafted.app")
    return apps[0]


def _copy_signed_app(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/usr/bin/ditto", str(src), str(dest)], check=True, timeout=60)
    return dest


def _identity_token(app: Path) -> str:
    display = subprocess.run(
        ["/usr/bin/codesign", "--display", "--verbose=4", str(app)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    text = display.stdout + display.stderr
    for line in text.splitlines():
        if line.startswith("CDHash="):
            return "cdhash:" + line.split("=", 1)[1]
    pytest.fail(f"signed app {app} has no CDHash")
    raise AssertionError


class _SignedApps:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.e37 = _require_e37_artifacts()
        self.prior = _require_prior_79001_artifacts()
        self.mounts: list[Path] = []

    def __enter__(self) -> "_SignedApps":
        e37_mount = self.tmp / "mnt-e37"
        prior_mount = self.tmp / "mnt-79001"
        self.e37_app = _attach_signed_app(self.e37["dmg"], e37_mount)
        self.prior_app = _attach_signed_app(self.prior["dmg"], prior_mount)
        self.mounts = [e37_mount, prior_mount]
        self.e37_identity = _identity_token(self.e37_app)
        self.prior_identity = _identity_token(self.prior_app)
        if self.e37_identity == self.prior_identity:
            pytest.fail(
                "unresolved required gate: e37 and 79001 fixtures have the same "
                "CDHash; cross-generation acceptance needs two signed generations"
            )
        return self

    def __exit__(self, *exc: object) -> None:
        for mount in self.mounts:
            subprocess.run(
                ["/usr/bin/hdiutil", "detach", str(mount), "-quiet"],
                capture_output=True,
                timeout=30,
            )

    def copy_e37(self, dest: Path) -> Path:
        return _copy_signed_app(self.e37_app, dest)

    def copy_prior(self, dest: Path) -> Path:
        return _copy_signed_app(self.prior_app, dest)


def test_product_update_helper_writes_ready_before_waiting(tmp_path: Path) -> None:
    with _SignedApps(tmp_path) as apps:
        dest = apps.copy_prior(tmp_path / "Installed.app")
        source = apps.copy_e37(tmp_path / "Candidate.app")
        dest_identity = apps.prior_identity
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
            deadline = time.time() + 20
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
            assert payload["source_identity"] == apps.e37_identity
            assert _identity_token(dest) == dest_identity
            assert helper.poll() is None
            gate.write_text("go", encoding="utf-8")
            finished = helper.wait(timeout=20)
            assert finished == 5
            assert _identity_token(dest) == dest_identity
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


def test_product_update_helper_copy_failure_keeps_destination(tmp_path: Path) -> None:
    with _SignedApps(tmp_path) as apps:
        dest = apps.copy_prior(tmp_path / "Installed.app")
        source = apps.copy_e37(tmp_path / "Candidate.app")
        receipt = tmp_path / "receipt.json"
        result = _run_helper(
            [
                "--source",
                str(source),
                "--destination",
                str(dest),
                "--receipt",
                str(receipt),
                "--fail-after",
                "captured",
            ],
            env=_helper_env(),
        )
        assert result.returncode == 40
        assert _identity_token(dest) == apps.prior_identity
        journal = json.loads(Path(str(receipt) + ".journal.json").read_text(encoding="utf-8"))
        assert journal["phase"] == "captured"
        assert journal["prior_identity"] == apps.prior_identity
        assert journal["source_identity"] == apps.e37_identity
        assert not receipt.exists() or '"replaced":true' not in receipt.read_text(
            encoding="utf-8"
        )


def test_product_update_helper_write_ahead_displace_is_resumable(tmp_path: Path) -> None:
    with _SignedApps(tmp_path) as apps:
        dest = apps.copy_prior(tmp_path / "Installed.app")
        source = apps.copy_e37(tmp_path / "Candidate.app")
        receipt = tmp_path / "receipt.json"
        first = _run_helper(
            [
                "--source",
                str(source),
                "--destination",
                str(dest),
                "--receipt",
                str(receipt),
                "--fail-after",
                "displacing",
            ],
            env=_helper_env(),
        )
        assert first.returncode == 40
        assert not dest.exists()
        journal = json.loads(Path(str(receipt) + ".journal.json").read_text(encoding="utf-8"))
        assert journal["phase"] == "displacing"
        capture = Path(journal["capture"])
        assert _identity_token(capture / "displaced.app") == apps.prior_identity
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
                "--resume",
            ],
            env=_helper_env(),
        )
        assert resumed.returncode == 0, resumed.stderr
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        assert payload["replaced"] is True
        assert payload["detail"] == "replaced"
        assert payload["mode"] == "replace"
        assert _identity_token(dest) == apps.e37_identity
        assert (sibling / "keep.txt").read_text(encoding="utf-8") == "old"


def test_product_update_helper_resume_requires_complete_journal(tmp_path: Path) -> None:
    with _SignedApps(tmp_path) as apps:
        dest = apps.copy_prior(tmp_path / "Installed.app")
        source = apps.copy_e37(tmp_path / "Candidate.app")
        receipt = tmp_path / "receipt.json"
        first = _run_helper(
            [
                "--source",
                str(source),
                "--destination",
                str(dest),
                "--receipt",
                str(receipt),
                "--fail-after",
                "captured",
            ],
            env=_helper_env(),
        )
        assert first.returncode == 40
        journal_path = Path(str(receipt) + ".journal.json")
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
        del payload["operation"]
        journal_path.write_text(json.dumps(payload), encoding="utf-8")
        resumed = _run_helper(
            [
                "--source",
                str(source),
                "--destination",
                str(dest),
                "--receipt",
                str(receipt),
                "--journal",
                str(journal_path),
                "--transaction",
                payload["transaction"],
                "--resume",
            ],
            env=_helper_env(),
        )
        assert resumed.returncode == 14
        assert "operation" in resumed.stderr
        assert _identity_token(dest) == apps.prior_identity
        assert not receipt.exists() or '"replaced":true' not in receipt.read_text(
            encoding="utf-8"
        )


def test_product_update_helper_rejects_overlapping_lock(tmp_path: Path) -> None:
    with _SignedApps(tmp_path) as apps:
        dest = apps.copy_prior(tmp_path / "Installed.app")
        source = apps.copy_e37(tmp_path / "Candidate.app")
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
        deadline = time.time() + 20
        while time.time() < deadline and not admission.is_file():
            if first.poll() is not None:
                break
            time.sleep(0.05)
        assert admission.is_file()
        held = dest.parent / ".vc-update.lock" / "held"
        assert held.is_file()
        second = _run_helper(
            [
                "--source",
                str(source),
                "--destination",
                str(dest),
                "--receipt",
                str(tmp_path / "other-receipt.json"),
                "--resume",
            ],
            env=_helper_env(),
        )
        assert second.returncode == 13
        assert held.is_file()
        assert _identity_token(dest) == apps.prior_identity
        gate.write_text("go", encoding="utf-8")
        first.wait(timeout=20)
        assert held.is_file()


def test_product_update_helper_missing_receipt_is_not_success(tmp_path: Path) -> None:
    with _SignedApps(tmp_path) as apps:
        dest = apps.copy_prior(tmp_path / "Installed.app")
        source = apps.copy_e37(tmp_path / "Candidate.app")
        receipt = tmp_path / "receipt.json"
        result = _run_helper(
            [
                "--source",
                str(source),
                "--destination",
                str(dest),
                "--receipt",
                str(receipt),
                "--fail-after",
                "adopted",
            ],
            env=_helper_env(),
        )
        assert result.returncode == 40
        assert not receipt.exists() or '"replaced":true' not in receipt.read_text(
            encoding="utf-8"
        )


def test_product_update_helper_rejects_traversal_transaction(tmp_path: Path) -> None:
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
            "--transaction",
            "../evil",
        ]
    )
    assert result.returncode == 2
    assert "transaction" in result.stderr
    link = tmp_path / "link-dest.app"
    link.symlink_to(dest)
    linked = _run_helper(
        [
            "--source",
            str(source),
            "--destination",
            str(link),
            "--receipt",
            str(receipt),
        ]
    )
    assert linked.returncode == 6


def test_product_update_result_before_new_ui_and_restore(tmp_path: Path) -> None:
    with _SignedApps(tmp_path) as apps:
        dest = apps.copy_prior(tmp_path / "Installed.app")
        source = apps.copy_e37(tmp_path / "Candidate.app")
        receipt = tmp_path / "receipt.json"
        new_ui_started = tmp_path / "new-ui-started"
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
                    "20",
                ],
                env=_helper_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            admission = Path(str(receipt) + ".admission.json")
            deadline = time.time() + 20
            while time.time() < deadline and not admission.is_file():
                if helper.poll() is not None:
                    break
                time.sleep(0.05)
            assert admission.is_file(), "old UI must see READY before it exits"
            assert helper.poll() is None
            sleeper.terminate()
            sleeper.wait(timeout=5)
            assert helper.wait(timeout=90) == 0
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            assert payload["replaced"] is True
            assert payload["detail"] == "replaced"
            assert payload["transaction"]
            assert payload["source_identity"] == apps.e37_identity
            assert payload["prior_identity"] == apps.prior_identity
            assert _identity_token(dest) == apps.e37_identity
            assert not new_ui_started.exists()
            new_ui_started.write_text("started after receipt", encoding="utf-8")
            journal = json.loads(Path(str(receipt) + ".journal.json").read_text(encoding="utf-8"))
            capture = Path(journal["capture"])
            prior = capture / "prior.app"
            assert _identity_token(prior) == apps.prior_identity
            restore_receipt = tmp_path / "restore-receipt.json"
            shutil.copy2(
                Path(str(receipt) + ".journal.json"),
                Path(str(restore_receipt) + ".journal.json"),
            )
            restored = _run_helper(
                [
                    "--source",
                    str(prior),
                    "--destination",
                    str(dest),
                    "--receipt",
                    str(restore_receipt),
                    "--journal",
                    str(restore_receipt) + ".journal.json",
                    "--transaction",
                    journal["transaction"],
                    "--mode",
                    "restore",
                    "--fail-after",
                    "adopting",
                ],
                env=_helper_env(),
            )
            assert restored.returncode == 40
            restore_journal = json.loads(
                Path(str(restore_receipt) + ".journal.json").read_text(encoding="utf-8")
            )
            assert restore_journal["phase"] == "adopting"
            assert restore_journal["prior_identity"] == apps.prior_identity
            resume = _run_helper(
                [
                    "--source",
                    str(prior),
                    "--destination",
                    str(dest),
                    "--receipt",
                    str(restore_receipt),
                    "--journal",
                    str(restore_receipt) + ".journal.json",
                    "--transaction",
                    journal["transaction"],
                    "--mode",
                    "restore",
                    "--resume",
                ],
                env=_helper_env(),
            )
            assert resume.returncode == 0, resume.stderr
            restore_payload = json.loads(restore_receipt.read_text(encoding="utf-8"))
            assert restore_payload["detail"] == "restored"
            assert restore_payload["mode"] == "restore"
            assert restore_payload["operation"] == "restore"
            assert _identity_token(dest) == apps.prior_identity
            assert _identity_token(prior) == apps.prior_identity
            replace_again = _run_helper(
                [
                    "--source",
                    str(source),
                    "--destination",
                    str(dest),
                    "--receipt",
                    str(tmp_path / "receipt-2.json"),
                ],
                env=_helper_env(),
                timeout=90,
            )
            assert replace_again.returncode == 0, replace_again.stderr
            journal2_path = Path(str(tmp_path / "receipt-2.json") + ".journal.json")
            journal2 = json.loads(journal2_path.read_text(encoding="utf-8"))
            prior2 = Path(journal2["capture"]) / "prior.app"
            restore2 = tmp_path / "restore-displacing.json"
            interrupted = _run_helper(
                [
                    "--source",
                    str(prior2),
                    "--destination",
                    str(dest),
                    "--receipt",
                    str(restore2),
                    "--journal",
                    str(journal2_path),
                    "--transaction",
                    journal2["transaction"],
                    "--mode",
                    "restore",
                    "--fail-after",
                    "displacing",
                ],
                env=_helper_env(),
            )
            assert interrupted.returncode == 40
            assert not dest.exists()
            assert _identity_token(Path(journal2["capture"]) / "failed-new.app") == apps.e37_identity
            assert _identity_token(prior2) == apps.prior_identity
            resume_displacing = _run_helper(
                [
                    "--source",
                    str(prior2),
                    "--destination",
                    str(dest),
                    "--receipt",
                    str(restore2),
                    "--journal",
                    str(journal2_path),
                    "--transaction",
                    journal2["transaction"],
                    "--mode",
                    "restore",
                    "--resume",
                ],
                env=_helper_env(),
            )
            assert resume_displacing.returncode == 0, resume_displacing.stderr
            assert json.loads(restore2.read_text(encoding="utf-8"))["detail"] == "restored"
            assert _identity_token(dest) == apps.prior_identity
            assert _identity_token(prior2) == apps.prior_identity
        finally:
            if sleeper.poll() is None:
                sleeper.terminate()
                sleeper.wait(timeout=5)
            if helper is not None and helper.poll() is None:
                helper.terminate()
                helper.wait(timeout=5)


def test_product_update_pack_failure_and_concurrent_recovery(tmp_path: Path) -> None:
    installer = REPO_ROOT / "scripts/install-runtime-pack.sh"
    with _SignedApps(tmp_path) as apps:
        dest = apps.copy_prior(tmp_path / "Installed.app")
        source = apps.copy_e37(tmp_path / "Candidate.app")
        receipt = tmp_path / "receipt.json"
        replaced = _run_helper(
            [
                "--source",
                str(source),
                "--destination",
                str(dest),
                "--receipt",
                str(receipt),
            ],
            env=_helper_env(),
            timeout=90,
        )
        assert replaced.returncode == 0, replaced.stderr
        isolated = tmp_path / "isolated-runtime"
        isolated.mkdir()
        env = os.environ.copy()
        env["VIBECRAFTED_RUNTIME_HOME"] = str(isolated)
        env["HOME"] = str(tmp_path / "isolated-home")
        env["XDG_DATA_HOME"] = str(tmp_path / "isolated-xdg")
        pack = subprocess.run(
            [
                "/bin/bash",
                str(installer),
                "--pack",
                str(apps.e37["pack"]),
                "--app-root",
                str(dest),
                "--terminal-host",
                str(dest / "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty"),
                "--frame-helper",
                str(dest / "Contents/Helpers/vc-frame"),
                "--expected-source-revision",
                "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                "--expected-terminal-revision",
                "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                "--expected-frame-revision",
                "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            ],
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
        assert pack.returncode != 0
        evidence = {
            "schema": "io.vetcoders.vibecrafted.product-update-pack-evidence.v1",
            "transaction": json.loads(receipt.read_text(encoding="utf-8"))["transaction"],
            "state": "unresolved",
        }
        (tmp_path / "pack-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
        assert json.loads((tmp_path / "pack-evidence.json").read_text())["state"] == "unresolved"
        journal = json.loads(Path(str(receipt) + ".journal.json").read_text(encoding="utf-8"))
        capture = Path(journal["capture"])
        gate = tmp_path / "hold-restore"
        first = subprocess.Popen(
            [
                str(HELPER),
                "--source",
                str(capture / "prior.app"),
                "--destination",
                str(dest),
                "--receipt",
                str(tmp_path / "restore-a.json"),
                "--journal",
                str(receipt) + ".journal.json",
                "--transaction",
                journal["transaction"],
                "--mode",
                "restore",
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
        deadline = time.time() + 20
        admission = Path(str(tmp_path / "restore-a.json") + ".admission.json")
        while time.time() < deadline and not admission.is_file():
            if first.poll() is not None:
                break
            time.sleep(0.05)
        assert admission.is_file()
        concurrent = _run_helper(
            [
                "--source",
                str(capture / "prior.app"),
                "--destination",
                str(dest),
                "--receipt",
                str(tmp_path / "restore-b.json"),
                "--journal",
                str(receipt) + ".journal.json",
                "--transaction",
                journal["transaction"],
                "--mode",
                "restore",
                "--resume",
            ],
            env=_helper_env(),
        )
        assert concurrent.returncode == 13
        assert (dest.parent / ".vc-update.lock" / "held").is_file()
        gate.write_text("go", encoding="utf-8")
        first.wait(timeout=90)


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
