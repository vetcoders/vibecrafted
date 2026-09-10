"""W1 authored contracts. Compilation and pytest are W2 integrator gates."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import pwd
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Self

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted"
SHELL = REPO_ROOT / "vibecrafted-app/shell-agent"
HELPER = REPO_ROOT / "scripts/vc-app-update.sh"

_FRAME_IDENTITY_ENV = (
    "VIBECRAFTED_OPERATOR_SESSION",
    "VIBECRAFTED_WORKER_SESSION",
    "VIBECRAFTED_WORKSPACE_ID",
    "VIBECRAFTED_SESSION_ID",
    "VIBECRAFTED_WORKSPACE_INSTANCE_ID",
    "VIBECRAFTED_WORKSPACE_ROOT",
    "VIBECRAFTED_DECLARED_WORKSPACE_ROOT",
    "VIBECRAFTED_PENDING_VC_FRAME_ATTACH",
    "VIBECRAFTED_PENDING_VC_FRAME_SWITCH",
    "VIBECRAFTED_PREPARED_VC_FRAME_SESSION",
    "VIBECRAFTED_START_CREATED_SESSION",
    "VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME",
    "VIBECRAFTED_PRODUCT_ENTRY",
    "VIBECRAFTED_PREFER_REPO_VC_FRAME",
    "VIBECRAFTED_VC_FRAME_BIN",
    "VIBECRAFTED_LEGACY_VC_FRAME_SOCKET_DIR",
    "VC_FRAME",
    "VC_FRAME_PANE_ID",
    "VC_FRAME_SESSION_NAME",
    "VC_FRAME_CONFIG_DIR",
    "VC_FRAME_CONFIG_FILE",
    "VC_FRAME_SOCKET_DIR",
    "ZELLIJ",
    "ZELLIJ_PANE_ID",
    "ZELLIJ_SESSION_NAME",
    "ZELLIJ_SOCKET_DIR",
)


def _authored_writes_named_socket(source: str, filename: str) -> bool:
    """AST: a write/open call whose arguments name `filename`. Not a text search."""
    tree = ast.parse(source)
    writers = {"write_text", "write_bytes", "touch", "open"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name not in writers:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and child.value == filename:
                return True
    return False


def _call_func_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _module_functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node
    return found


def _string_constants(node: ast.AST) -> set[object]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant)
    }


def _call_argv_strings(call: ast.Call) -> list[str]:
    """Flatten string constants from positional list/tuple argv (helper / Popen)."""
    strings: list[str] = []
    for arg in call.args:
        if isinstance(arg, (ast.List, ast.Tuple)):
            for elt in arg.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    strings.append(elt.value)
        elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            strings.append(arg.value)
    return strings


def _function_invokes_helper_mode(fn: ast.AST, mode: str) -> bool:
    """True when a call in `fn` passes adjacent `--mode`, `<mode>` argv strings."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        argv = _call_argv_strings(node)
        for index, item in enumerate(argv[:-1]):
            if item == "--mode" and argv[index + 1] == mode:
                return True
    return False


def _function_calls_install_signed_pack_allow_older(fn: ast.AST) -> bool:
    """True when `fn` calls `_install_signed_pack` with allow_older other than False."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if _call_func_name(node) != "_install_signed_pack":
            continue
        for keyword in node.keywords:
            if keyword.arg != "allow_older":
                continue
            if isinstance(keyword.value, ast.Constant) and keyword.value.value is False:
                continue
            return True
    return False


def _installed_frame_engine() -> Path | None:
    for candidate in (
        os.environ.get("VIBECRAFTED_VC_FRAME_BIN", ""),
        os.environ.get("VIBECRAFTED_RUNTIME_ROOT", "")
        and os.path.join(os.environ["VIBECRAFTED_RUNTIME_ROOT"], "libexec", "vc-frame"),
    ):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return Path(candidate)
    founder = Path(pwd.getpwuid(os.getuid()).pw_dir)
    releases = founder / ".local" / "share" / "vibecrafted" / "releases"
    if releases.is_dir():
        found = sorted(
            releases.glob("*/libexec/vc-frame"), key=lambda p: p.stat().st_mtime
        )
        if found:
            return found[-1]
    return None


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
    assert 'forInfoDictionaryKey: "VCUpdateFeedURL"' in delegate
    assert "func resolveLiveUpdateChannel(" in delegate
    assert "func resolveProductUpdateFeedURL(" in policy
    assert 'scheme == "https"' in policy
    assert "VCUpdateFeedURL" not in policy
    assert "VCUpdateFeedURL" not in info
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
    assert "beginProductUpdateRecover" in delegate
    assert "productUpdateRecoverRequest" in delegate
    assert "admitProductUpdateHelper" in delegate
    assert "restore_previous_tuple" in helper
    assert "recover_whole_tuple" in helper
    assert "restore_prior_runtime" in helper
    assert "require_recovered_tuple" in helper
    assert "config_conflicts" in helper
    assert "config_transaction" in helper
    assert "--mode recover" in helper or "recover" in helper
    assert "--allow-older-runtime" in (
        REPO_ROOT / "scripts/install-runtime-pack.sh"
    ).read_text(encoding="utf-8")
    assert "installer_admits_allow_older" in helper
    assert "grep -Fq -- '--allow-older-runtime'" in helper
    assert "failed-new.app" in helper
    assert "rolledBack" in transaction
    assert "reconcile_resume" in helper
    assert "require_exact_identity" in helper
    assert "flock_update_lock_nb" in helper
    assert "UPDATE_LOCK_FD" in helper
    assert "without_update_lock_fd" in helper
    assert "without_update_lock_fd /bin/mv" in helper
    assert "if ! /bin/mv" not in helper
    # One rollback authority: the pack sealed inside the verified prior.app.
    # An owned copy beside the capture, and a journalled path read back as input,
    # were both second sources of truth and are gone.
    assert "historical-runtime-pack" not in helper
    assert "owned_historical_pack" not in helper
    assert "capture_owned_historical_pack" not in helper
    assert "journal_get prior_pack" not in helper
    assert "journal_get prior_generation" not in helper
    assert 'locate_prior_pack() {' in helper
    assert "resolve_prior_runtime" in helper
    assert "published_is_prior_generation" in helper
    assert '"${app}/Contents/Resources/runtime-pack"' in helper
    assert "owned_displaced" in helper
    assert "journal_require operation" in helper
    assert "productUpdateObserveRuntimeEvidence" in transaction
    assert "productUpdateObserveInstallerPublication" in transaction
    assert "productUpdateDerivePackPublication" in transaction
    assert "ProductUpdatePackPublicationState" in transaction
    assert "unresolved" in transaction
    assert "vibecrafted.active-runtime.v1" in transaction
    assert "vibecrafted.runtime-install.v1" in transaction
    assert 'rm -rf "$LOCKDIR"' not in helper
    assert "ALLOW_UNSIGNED" not in helper
    assert "refusing --allow-unsigned" in helper
    assert "productUpdateObserveRuntimeEvidence" in delegate
    assert "writeProductUpdatePackEvidence" in delegate
    assert "reconcileAdoptedPack" in delegate
    assert "persistAdmittedHandoff" in coordinator
    assert ".terminateCancel" in delegate
    assert 'try? writeProductUpdateHandoff' not in coordinator
    assert 'try? writeProductUpdateHandoff' not in delegate
    recover = delegate[delegate.index("beginProductUpdateRecover") :]
    assert "Could not save the recover handoff" in recover
    authored = (REPO_ROOT / "tests/tui/test_product_update.py").read_text(encoding="utf-8")
    assert "product_contract" in authored
    assert "VC_FRAME_SOCKET_DIR" in authored
    assert "_IsolatedFrameSession" in authored
    assert "_installed_frame_engine" in authored
    assert "os.ttyname" in authored
    assert "os.isatty" in authored
    # Commands reach the pane through the engine's addressed input, which the
    # server delivers to the process stdin because the server owns the PTY
    # master. A write to the tty slave lands in terminal OUTPUT, not input, so
    # that direction must not come back.
    assert "write-chars" in authored
    assert "--pane-id" in authored
    assert "dump-screen" in authored
    assert "list-panes" in authored
    assert "VCREPLY" in authored
    # AST, not a text search: a literal ban would match its own assertion.
    session_class = next(
        node
        for node in ast.walk(ast.parse(authored))
        if isinstance(node, ast.ClassDef) and node.name == "_IsolatedFrameSession"
    )
    assert not any(
        isinstance(child, ast.Call) and _call_func_name(child) == "open"
        for child in ast.walk(session_class)
    ), "the Frame proof must not open the tty slave or any command file"
    worker_source = _FRAME_PTY_WORKER
    assert "open(" not in worker_source, "the pane worker must not use a file channel"
    assert "socket" not in worker_source, "the pane worker must not fake a socket"
    assert "installed vc-frame engine is required" in authored
    assert "--create-background" in authored
    assert "delete-session" in authored
    assert "list-sessions" in authored
    tree = ast.parse(authored)
    session_start = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "_IsolatedFrameSession":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "start":
                    session_start = item
    assert session_start is not None, "_IsolatedFrameSession.start is missing"
    assert not any(
        isinstance(child, ast.Call) and _call_func_name(child) == "skip"
        for child in ast.walk(session_start)
    ), "whole-tuple Frame proof must not skip when the engine is missing"
    assert any(
        isinstance(child, ast.Call) and _call_func_name(child) == "fail"
        for child in ast.walk(session_start)
    ), "missing vc-frame engine must fail the recover proof"
    assigns_start_blank = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "start"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value == ""
                ):
                    assigns_start_blank = True
    assert not assigns_start_blank, "self.start = '' hides IsolatedFrameSession.start"
    assert not _authored_writes_named_socket(authored, "live-session" + ".sock")
    assert "VIBECRAFTED_LAUNCHER_BIN" in authored
    assert "allow-older-runtime" in authored
    assert '"--mode"' in authored and '"recover"' in authored
    assert "VIBECRAFTED_RUNTIME_PACK_HARNESS" in authored
    assert "fail-after" in authored
    functions = _module_functions(tree)
    recovery_names = [
        name
        for name in functions
        if name.startswith(
            (
                "test_product_update_cross_generation",
                "test_product_update_whole_tuple_recovery",
            )
        )
    ]
    assert recovery_names, "cross-generation / whole-tuple recovery tests are missing"
    for name in recovery_names:
        fn = functions[name]
        assert not _function_calls_install_signed_pack_allow_older(fn), (
            f"{name} must not call _install_signed_pack with allow_older True"
        )
        assert _function_invokes_helper_mode(fn, "recover"), (
            f"{name} must invoke production helper --mode recover"
        )
    other_constants: set[object] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == "test_product_update_source_contract":
            continue
        other_constants.update(_string_constants(node))
    assert "previous-marker.txt" not in other_constants
    assert ("deadbeef" * 5) not in other_constants
    assert "pwd.getpwuid" in authored
    assert "signed fixture pair not mounted" not in helper
    assert "case .waiting, .publishing, .restoring" in delegate
    launch = delegate[
        delegate.index("func applicationDidFinishLaunching(") : delegate.index(
            "func applicationShouldHandleReopen"
        )
    ]
    assert launch.index("adoptPendingProductUpdateIfNeeded") < launch.index("connectCommandDeck")
    recover = delegate[delegate.index("beginProductUpdateRecover") :]
    assert "productUpdateRecoverRequest" in recover
    assert "relaunch: false" not in recover
    assert "admitProductUpdateHelper" in recover
    assert "ProductUpdateHelperMode.recover" in recover
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
        check=False,
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
            check=False,
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
            check=False,
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
        check=False,
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
        check=False,
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
    for key in _FRAME_IDENTITY_ENV:
        env.pop(key, None)
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
        env=env if env is not None else _helper_env(),
        check=False,
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sibling_feed(artifact: Path) -> tuple[Path, Path] | None:
    feed = artifact.parent / "release-output.json"
    signature = artifact.parent / "release-output.json.sig"
    if feed.is_file() and signature.is_file():
        return feed, signature
    return None


def _require_pair(label: str, dmg_glob: str, pack_glob: str) -> tuple[Path, Path]:
    roots = _signed_search_roots()
    dmg = _first_glob(roots, dmg_glob)
    pack = _first_glob(roots, pack_glob)
    if dmg is None or pack is None:
        _fail_missing_fixture(f"{label} DMG/pack not found with {dmg_glob} / {pack_glob}.")
    return dmg, pack


_E37_ARTIFACTS: dict[str, Path] | None = None


def _require_e37_artifacts() -> dict[str, Path]:
    global _E37_ARTIFACTS
    if _E37_ARTIFACTS is not None:
        return _E37_ARTIFACTS
    dmg, pack = _require_pair(
        "e37",
        "*20260910-e37be2c9*.dmg",
        "*20260910-e37be2c9*.tar.gz",
    )
    sibling = _sibling_feed(dmg) or _sibling_feed(pack)
    if sibling is None:
        _fail_missing_fixture(
            "e37 release-output.json + .sig must sit next to the e37 DMG or pack; "
            "do not reuse another generation's feed."
        )
    feed, signature = sibling
    payload = json.loads(feed.read_text(encoding="utf-8"))
    if payload.get("schema") != "io.vetcoders.vibecrafted.release-output.v1":
        pytest.fail("e37 feed is not release-output.v1")
    source = str((payload.get("source_revisions") or {}).get("vibecrafted") or "")
    if "e37be2c9" not in source.lower():
        pytest.fail(
            f"sibling feed source {source} is not the e37 generation; "
            "refusing to treat a different manifest as the current pair"
        )
    dmg_name = Path(str((payload.get("dmg") or {}).get("path") or "")).name
    if dmg_name and dmg_name != dmg.name:
        pytest.fail(f"e37 feed dmg.path {dmg_name} does not name {dmg.name}")
    if payload.get("dmg", {}).get("sha256") != _sha256_file(dmg):
        pytest.fail("e37 feed dmg.sha256 does not match the DMG bytes")
    if payload.get("runtime_pack", {}).get("sha256") != _sha256_file(pack):
        pytest.fail("e37 feed runtime_pack.sha256 does not match the pack bytes")
    if signature.stat().st_size != 256:
        pytest.fail(
            "unresolved required gate: detached signature is not 256 bytes; "
            "do not treat an unsigned locator as a signed fixture."
        )
    verify_env = os.environ.copy()
    verify_env["PYTHONPATH"] = str(REPO_ROOT / "vibecrafted-core")
    verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "vibecrafted_core.product_contract",
            "release-output",
            str(feed),
            str(signature),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(REPO_ROOT),
        env=verify_env,
        check=False,
    )
    if verified.returncode != 0:
        pytest.fail(
            "unresolved required gate: product_contract refused the e37 pair: "
            f"{verified.stderr or verified.stdout}"
        )
    _E37_ARTIFACTS = {"dmg": dmg, "pack": pack, "feed": feed, "signature": signature}
    return _E37_ARTIFACTS


def _require_prior_79001_artifacts() -> dict[str, Path]:
    dmg, pack = _require_pair(
        "79001",
        "*20260909-79001c3d*.dmg",
        "*20260909-79001c3d*.tar.gz",
    )
    artifacts: dict[str, Path] = {"dmg": dmg, "pack": pack}
    sibling = _sibling_feed(dmg) or _sibling_feed(pack)
    if sibling is None:
        return artifacts
    feed, signature = sibling
    try:
        payload = json.loads(feed.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return artifacts
    source = str((payload.get("source_revisions") or {}).get("vibecrafted") or "")
    if "79001c3d" not in source.lower():
        # Current e37 feed sitting in the same dist/ must not be labeled prior.
        return artifacts
    artifacts["feed"] = feed
    artifacts["signature"] = signature
    return artifacts


def _verify_signed_generation(app: Path, *, source_token: str, label: str) -> None:
    verified = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(app)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if verified.returncode != 0:
        pytest.fail(
            f"unresolved required gate: {label} failed codesign --verify --strict: "
            f"{verified.stderr or verified.stdout}"
        )
    display = subprocess.run(
        ["/usr/bin/codesign", "--display", "--verbose=4", str(app)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    text = display.stdout + display.stderr
    if "Identifier=io.vetcoders.vibecrafted" not in text:
        pytest.fail(f"{label} is not io.vetcoders.vibecrafted")
    if "TeamIdentifier=MW223P3NPX" not in text:
        pytest.fail(f"{label} is not Team ID MW223P3NPX")
    stapled = subprocess.run(
        ["/usr/bin/stapler", "validate", str(app)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if stapled.returncode != 0:
        pytest.fail(
            f"unresolved required gate: {label} failed stapler validate: "
            f"{stapled.stderr or stapled.stdout}"
        )
    manifest_path = app / "Contents/Resources/product-manifest.json"
    if not manifest_path.is_file():
        pytest.fail(f"{label} has no Contents/Resources/product-manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    git_sha = str(manifest.get("git_sha") or "")
    if source_token.lower() not in git_sha.lower():
        pytest.fail(
            f"{label} product-manifest git_sha {git_sha} is not {source_token}; "
            "refusing to treat another generation as this fixture"
        )


def _founder_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _founder_identity_stamps() -> dict[str, int | None]:
    home = _founder_home()
    watched = (
        home / "Library/LaunchAgents/io.vetcoders.vibecrafted.server.plist",
        home / ".local/share/vibecrafted/active.json",
        home / ".local/share/vibecrafted/install-receipt.json",
    )
    return {str(path): (path.stat().st_mtime_ns if path.exists() else None) for path in watched}


def _assert_founder_identity_untouched(before: dict[str, int | None]) -> None:
    after = _founder_identity_stamps()
    assert after == before, f"Founder installer identity changed: {before} -> {after}"


def _isolated_product_env(
    tmp_path: Path, *, frame_socket_dir: Path | None = None
) -> dict[str, str]:
    home = tmp_path / "isolated-home"
    runtime = tmp_path / "isolated-runtime"
    crafted = tmp_path / "isolated-crafted"
    launcher = tmp_path / "isolated-launcher"
    xdg_config = tmp_path / "isolated-xdg-config"
    xdg_data = tmp_path / "isolated-xdg-data"
    xdg_cache = tmp_path / "isolated-xdg-cache"
    xdg_state = tmp_path / "isolated-xdg-state"
    sockets = frame_socket_dir if frame_socket_dir is not None else tmp_path / "isolated-frame-sockets"
    scratch = tmp_path / "isolated-tmp"
    for path in (
        home,
        runtime,
        crafted,
        launcher,
        xdg_config,
        xdg_data,
        xdg_cache,
        xdg_state,
        sockets,
        scratch,
    ):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    env = os.environ.copy()
    for key in (
        "VIBECRAFTED_WORKSPACE_ID",
        "VIBECRAFTED_RUNTIME_ROOT",
        "VIBECRAFTED_PYTHON",
        "VIBECRAFTED_REPO",
        "VIBECRAFTED_CONTROL_PLANE",
        *_FRAME_IDENTITY_ENV,
    ):
        env.pop(key, None)
    env.update(
        {
            "HOME": str(home),
            "VIBECRAFTED_HOME": str(crafted),
            "VIBECRAFTED_RUNTIME_HOME": str(runtime),
            "VIBECRAFTED_LAUNCHER_BIN": str(launcher),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(xdg_data),
            "XDG_CACHE_HOME": str(xdg_cache),
            "XDG_STATE_HOME": str(xdg_state),
            "VC_FRAME_SOCKET_DIR": str(sockets),
            "ZELLIJ_SOCKET_DIR": str(sockets),
            "TMPDIR": str(scratch),
            "VIBECRAFTED_UPDATE_HELPER_HARNESS": "1",
        }
    )
    return env


def _read_installer_publication(runtime_home: Path) -> dict[str, object]:
    pointer = runtime_home / "active.json"
    receipt = runtime_home / "install-receipt.json"
    assert pointer.is_file(), f"installer did not write {pointer}"
    assert receipt.is_file(), f"installer did not write {receipt}"
    pointer_doc = json.loads(pointer.read_text(encoding="utf-8"))
    receipt_doc = json.loads(receipt.read_text(encoding="utf-8"))
    assert pointer_doc.get("schema") == "vibecrafted.active-runtime.v1"
    assert receipt_doc.get("schema") == "vibecrafted.runtime-install.v1"
    version = str(pointer_doc.get("version") or "")
    receipt_version = str(receipt_doc.get("version") or "")
    assert version, "active.json has no version"
    assert receipt_version, "install-receipt.json has no version"
    assert version == receipt_version, f"{version} != {receipt_version}"
    assert receipt_doc.get("install_pending") is not True
    assert receipt_doc.get("install_phase") not in {"preparing", "ancillary"}
    assert "config_transaction" not in receipt_doc
    for key in ("config_pending", "uninstall_pending", "config_conflicts"):
        assert not receipt_doc.get(key), f"installer left pending {key}"
    return {
        "version": version,
        "receipt_version": receipt_version,
        "pointer": pointer_doc,
        "receipt": receipt_doc,
    }


def _assert_isolated_receipt_roots(receipt: dict[str, object], tmp_path: Path) -> None:
    roots = receipt.get("roots")
    assert isinstance(roots, dict), "install-receipt.json has no roots"
    prefix = str(tmp_path.resolve())
    for name, value in roots.items():
        resolved = str(Path(str(value)).resolve())
        assert resolved.startswith(prefix), f"receipt root {name} escaped isolation: {resolved}"


def _assert_receipt_names_live_app(receipt: dict[str, object], dest: Path) -> None:
    app_root = str(receipt.get("app_root") or "")
    assert app_root, "install-receipt.json has no app_root"
    assert Path(app_root).resolve() == dest.resolve()
    assert ".vc-update-capture-" not in app_root
    roots = receipt.get("roots")
    assert isinstance(roots, dict), "install-receipt.json has no roots"
    for name, value in roots.items():
        resolved = str(Path(str(value)).resolve())
        assert ".vc-update-capture-" not in resolved, f"receipt root {name} names the capture"
        assert not resolved.endswith("/prior.app"), f"receipt root {name} still names prior.app"


def _assert_shared_recovered_tuple(
    *,
    env: dict[str, str],
    dest: Path,
    tmp_path: Path,
    prior_pub: dict[str, object],
    settings: Path,
    session: _IsolatedFrameSession,
) -> None:
    """App + runtime + config + wrappers share the isolated recover, same PTY."""
    restored = _read_installer_publication(Path(env["VIBECRAFTED_RUNTIME_HOME"]))
    assert restored["version"] == prior_pub["version"]
    _assert_isolated_receipt_roots(restored["receipt"], tmp_path)  # type: ignore[arg-type]
    _assert_receipt_names_live_app(restored["receipt"], dest)  # type: ignore[arg-type]
    roots = restored["receipt"].get("roots")  # type: ignore[union-attr]
    assert isinstance(roots, dict)
    for key in ("runtime_home", "product_config", "crafted_home", "launcher_home"):
        assert key in roots, f"recovered receipt missing shared {key}"
        path = Path(str(roots[key]))
        assert path.exists(), f"recovered {key} is missing: {path}"
    launcher_home = Path(str(roots["launcher_home"]))
    wrappers = [
        entry.name
        for entry in launcher_home.iterdir()
        if entry.name == "vibecrafted" or entry.name.startswith("vc-")
    ]
    assert wrappers, f"recovered launcher_home has no product wrappers: {launcher_home}"
    assert settings.read_text(encoding="utf-8").count("tuple-recovery-260910") == 1
    session.assert_survived()


def _app_helpers(dest: Path) -> tuple[Path, Path]:
    terminal = dest / "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty"
    frame = dest / "Contents/Helpers/vc-frame"
    return terminal, frame


def _install_signed_pack(
    pack: Path,
    dest: Path,
    env: dict[str, str],
    *,
    allow_older: bool = False,
    fail_after: str | None = None,
) -> subprocess.CompletedProcess[str]:
    terminal, frame = _app_helpers(dest)
    if not allow_older:
        command = [
            "/bin/bash",
            str(REPO_ROOT / "scripts/install-runtime-pack.sh"),
            "--pack",
            str(pack),
            "--app-root",
            str(dest),
            "--terminal-host",
            str(terminal),
            "--frame-helper",
            str(frame),
        ]
        run_env = env
        if fail_after:
            command += ["--fail-after", fail_after]
            run_env = env.copy()
            run_env["VIBECRAFTED_RUNTIME_PACK_HARNESS"] = "1"
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            env=run_env,
            check=False,
        )
    extract = Path(env["TMPDIR"]) / f"pack-extract-{os.getpid()}-{pack.stem}"
    extract.mkdir(parents=True, exist_ok=True)
    unpacked = subprocess.run(
        ["/usr/bin/tar", "-xpzf", str(pack), "-C", str(extract)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if unpacked.returncode != 0:
        return unpacked
    roots = [path for path in extract.iterdir() if path.is_dir()]
    if len(roots) != 1:
        return subprocess.CompletedProcess(
            args=["extract"],
            returncode=2,
            stdout="",
            stderr=f"Runtime Pack archive did not have one root: {roots}",
        )
    payload = roots[0]
    pack_python = payload / "bin/python3"
    verify_env = env.copy()
    verify_env["PYTHONPATH"] = str(payload / "vibecrafted-core")
    verified = subprocess.run(
        [
            str(pack_python),
            "-m",
            "vibecrafted_core.runtime_pack_contract",
            "verify",
            "--root",
            str(payload),
            "--carrier-basename",
            pack.name,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=verify_env,
        check=False,
    )
    if verified.returncode != 0:
        return verified
    agree_env = verify_env
    for app_copy, pack_copy in (
        (terminal, payload / "libexec/vc-terminal"),
        (frame, payload / "libexec/vc-frame"),
    ):
        agreed = subprocess.run(
            [
                str(pack_python),
                "-m",
                "vibecrafted_core.runtime_pack_contract",
                "helpers-agree",
                "--app-copy",
                str(app_copy),
                "--pack-copy",
                str(pack_copy),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=agree_env,
            check=False,
        )
        if agreed.returncode != 0:
            return agreed
    return subprocess.run(
        [
            str(pack_python),
            str(REPO_ROOT / "scripts/vetcoders_install.py"),
            "runtime-install",
            "--payload-root",
            str(payload),
            "--app-root",
            str(dest),
            "--runtime-home",
            env["VIBECRAFTED_RUNTIME_HOME"],
            "--allow-older-runtime",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        check=False,
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
        check=False,
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
    if dest.exists():
        shutil.rmtree(dest)
    try:
        cloned = subprocess.run(
            ["/bin/cp", "-cR", str(src), str(dest)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if cloned.returncode != 0 or not dest.exists():
            if dest.exists():
                shutil.rmtree(dest)
            copied = subprocess.run(
                ["/usr/bin/ditto", str(src), str(dest)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if copied.returncode != 0 or not dest.exists():
                raise RuntimeError(
                    f"signed copy failed for {dest}: {copied.stderr or copied.stdout or cloned.stderr}"
                )
        return dest
    except BaseException:
        if dest.exists():
            shutil.rmtree(dest)
        raise


def _identity_token(app: Path) -> str:
    display = subprocess.run(
        ["/usr/bin/codesign", "--display", "--verbose=4", str(app)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
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
        self.copies: list[Path] = []

    def __enter__(self) -> Self:
        try:
            e37_mount = self.tmp / "mnt-e37"
            prior_mount = self.tmp / "mnt-79001"
            self.mounts.append(e37_mount)
            self.e37_app = _attach_signed_app(self.e37["dmg"], e37_mount)
            self.mounts.append(prior_mount)
            self.prior_app = _attach_signed_app(self.prior["dmg"], prior_mount)
            _verify_signed_generation(self.e37_app, source_token="e37be2c9", label="e37 app")
            _verify_signed_generation(self.prior_app, source_token="79001c3d", label="79001 app")
            if "feed" in self.prior:
                prior_feed = json.loads(self.prior["feed"].read_text(encoding="utf-8"))
                prior_source = str(
                    (prior_feed.get("source_revisions") or {}).get("vibecrafted") or ""
                )
                if "79001c3d" not in prior_source.lower():
                    pytest.fail("prior artifacts labeled a non-79001 feed as the prior manifest")
            self.e37_identity = _identity_token(self.e37_app)
            self.prior_identity = _identity_token(self.prior_app)
            if self.e37_identity == self.prior_identity:
                pytest.fail(
                    "unresolved required gate: e37 and 79001 fixtures have the same "
                    "CDHash; cross-generation acceptance needs two signed generations"
                )
            return self
        except BaseException:
            self.release_copies()
            self.detach_mounts()
            raise

    def __exit__(self, exc_type: object, exc: object, _tb: object) -> None:
        if exc is not None:
            evidence = self.tmp / "signed-fixture-failure.json"
            evidence.write_text(
                json.dumps(
                    {
                        "e37_identity": getattr(self, "e37_identity", ""),
                        "prior_identity": getattr(self, "prior_identity", ""),
                        "error": str(exc),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        self.release_copies()
        for leftover in (
            *self.tmp.glob(".vc-update-capture-*"),
            *self.tmp.glob(".vc-update-prepared-*.app"),
        ):
            shutil.rmtree(leftover, ignore_errors=True)
        self.detach_mounts()

    def detach_mounts(self) -> None:
        while self.mounts:
            mount = self.mounts.pop()
            subprocess.run(
                ["/usr/bin/hdiutil", "detach", str(mount), "-quiet"],
                capture_output=True,
                timeout=30,
                check=False,
            )

    def release_copies(self) -> None:
        while self.copies:
            path = self.copies.pop()
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)

    def release(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        self.copies = [item for item in self.copies if item != path]

    def copy_e37(self, dest: Path) -> Path:
        self.copies.append(dest)
        try:
            copied = _copy_signed_app(self.e37_app, dest)
            if len(self.copies) >= 2:
                self.detach_mounts()
            return copied
        except BaseException:
            self.release(dest)
            raise

    def copy_prior(self, dest: Path) -> Path:
        self.copies.append(dest)
        try:
            copied = _copy_signed_app(self.prior_app, dest)
            if len(self.copies) >= 2:
                self.detach_mounts()
            return copied
        except BaseException:
            self.release(dest)
            raise


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
        try:
            admission = Path(str(receipt) + ".admission.json")
            deadline = time.time() + 20
            while time.time() < deadline and not admission.is_file():
                if first.poll() is not None:
                    break
                time.sleep(0.05)
            assert admission.is_file()
            held = dest.parent / ".vc-update.lock" / "held"
            assert held.is_file()
            invalid = _run_helper(
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
            assert invalid.returncode == 14, invalid.stderr
            assert "complete immutable journal" in (invalid.stderr or "")
            fresh = _run_helper(
                [
                    "--source",
                    str(source),
                    "--destination",
                    str(dest),
                    "--receipt",
                    str(tmp_path / "other-receipt.json"),
                ],
                env=_helper_env(),
            )
            assert fresh.returncode == 13, fresh.stderr
            assert "another update transaction holds the destination lock" in (
                fresh.stderr or ""
            )
            journal_path = Path(str(receipt) + ".journal.json")
            assert journal_path.is_file()
            contended = _run_helper(
                [
                    "--source",
                    str(source),
                    "--destination",
                    str(dest),
                    "--receipt",
                    str(receipt),
                    "--journal",
                    str(journal_path),
                    "--resume",
                ],
                env=_helper_env(),
            )
            assert contended.returncode == 13, contended.stderr
            assert "another update transaction holds the destination lock" in (
                contended.stderr or ""
            )
            assert held.is_file()
            assert _identity_token(dest) == apps.prior_identity
            gate.write_text("go", encoding="utf-8")
            first.wait(timeout=20)
            assert held.is_file()
        finally:
            if first.poll() is None:
                gate.write_text("go", encoding="utf-8")
                first.terminate()
                first.wait(timeout=5)


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
            # Both restores above were interrupted, so both finished through
            # finish_restore_adopt. Only an uninterrupted restore reaches the
            # terminal at the end of restore_previous_tuple -- the very terminal
            # a whole-tuple recover also finishes on. Running it here pins that
            # shared epilogue to the mode that entered it: restore says restored
            # and never claims recovery it did not perform.
            restore3 = tmp_path / "restore-uninterrupted.json"
            shutil.copy2(journal2_path, Path(str(restore3) + ".journal.json"))
            uninterrupted = _run_helper(
                [
                    "--source",
                    str(prior2),
                    "--destination",
                    str(dest),
                    "--receipt",
                    str(restore3),
                    "--journal",
                    str(restore3) + ".journal.json",
                    "--transaction",
                    journal2["transaction"],
                    "--mode",
                    "restore",
                ],
                env=_helper_env(),
                timeout=90,
            )
            assert uninterrupted.returncode == 0, uninterrupted.stderr
            uninterrupted_payload = json.loads(restore3.read_text(encoding="utf-8"))
            assert uninterrupted_payload["detail"] == "restored"
            assert uninterrupted_payload["mode"] == "restore"
            assert uninterrupted_payload["operation"] == "restore"
            assert uninterrupted_payload["recovered"] is False
            assert uninterrupted_payload["replaced"] is True
            assert _identity_token(dest) == apps.prior_identity
            assert _identity_token(prior2) == apps.prior_identity
        finally:
            if sleeper.poll() is None:
                sleeper.terminate()
                sleeper.wait(timeout=5)
            if helper is not None and helper.poll() is None:
                helper.terminate()
                helper.wait(timeout=5)


def _plant_custom_settings(env: dict[str, str]) -> Path:
    config_home = Path(env["XDG_CONFIG_HOME"]) / "vibecrafted"
    config_home.mkdir(parents=True, exist_ok=True)
    settings = config_home / "founder-settings.toml"
    settings.write_text(
        'custom_marker = "tuple-recovery-260910"\nbind = "tailscale-preserved"\n',
        encoding="utf-8",
    )
    return settings


# The pane worker speaks only through the PTY the Frame server gave it: it reads
# commands from stdin and answers on stdout. No mailbox file, no socket — the
# server holds the master, so an addressed pane write is the only real input.
_FRAME_PTY_WORKER = """\
import os
import sys
import time

def emit(text):
    sys.stdout.write(text + "\\n")
    sys.stdout.flush()

if not os.isatty(0):
    sys.stderr.write("probe pane stdin is not a PTY\\n")
    raise SystemExit(2)
emit("VCIDENT " + str(os.getpid()) + " " + os.ttyname(0))
while True:
    line = sys.stdin.readline()
    if not line:
        time.sleep(0.05)
        continue
    parts = line.strip().split(" ", 1)
    if len(parts) != 2:
        continue
    nonce, command = parts
    if len(nonce) != 16 or any(char not in "0123456789abcdef" for char in nonce):
        continue
    if command == "ping":
        emit("VCREPLY " + nonce + " pong " + str(os.getpid()))
    elif command == "identify":
        emit("VCREPLY " + nonce + " " + str(os.getpid()))
"""


class _IsolatedFrameSession:
    """Real signed/admitted vc-frame engine, isolated from the Founder namespace.

    Create path is `--layout` plus `attach --create-background`: the detached
    create is the only form that needs no TTY, and `--layout` is the surface
    that actually carries a layout into it. The `--new-session-with-layout`
    alias is dropped on this route — the engine builds its default layout
    instead, so this test's worker pane never exists and every later assertion
    is really measuring somebody else's shell. Repairing that alias belongs to
    the engine; this proof uses the interface the engine supports today.
    A layout pane hosts the PTY worker used for pid/lstart identity and command
    roundtrip.

    Commands travel the engine's own addressed pane input
    (`action write-chars --pane-id` plus a CR through `action write`), which the
    server delivers to the pane process stdin because the server owns the PTY
    master. Replies are read back from that process's stdout through
    `action dump-screen --pane-id`. Writing to the tty slave would land in the
    terminal's output queue instead, which is not process input at all.

    Teardown is kill-session + delete-session --force + this exclusively
    created /tmp directory only.
    """

    def __init__(self) -> None:
        self.tag = f"vcu{secrets.token_hex(8)}"
        self.root = Path(tempfile.mkdtemp(prefix=f"{self.tag}-", dir="/tmp"))
        owned = self.root.stat()
        self._owned = (owned.st_dev, owned.st_ino)
        self.socket_dir = self.root / "s"
        self.home = self.root / "h"
        self.config_dir = self.root / "c"
        self.probe = self.root / "p"
        self.session = self.tag
        self.frame = _installed_frame_engine()
        self.pane_id = ""
        self.worker_pid = 0
        self.worker_start = ""
        self.worker_tty = ""
        self.server_pid = 0
        self.server_start = ""
        self._prepared = False

    def start(self) -> None:
        if self.frame is None:
            pytest.fail(
                "installed vc-frame engine is required for whole-tuple recover proof"
            )
        try:
            self._prepare()
            created = self._frame(
                "--layout",
                str(self.layout),
                "attach",
                "--create-background",
                self.session,
                timeout=60,
            )
            assert created.returncode == 0, created.stderr or created.stdout
            listing = self._frame("list-sessions", "--no-formatting")
            assert self.session in listing.stdout, listing.stdout or listing.stderr
            self._resolve_pane()
            self._wait_worker()
            self._record_server()
            assert self.roundtrip("identify") == str(self.worker_pid)
            assert self.roundtrip("ping") == f"pong {self.worker_pid}"
        except BaseException:
            self.close()
            raise

    def _prepare(self) -> None:
        for path in (self.socket_dir, self.home, self.config_dir / "layouts", self.probe):
            path.mkdir(parents=True, exist_ok=True)
        self.worker = self.probe / "pty-worker.py"
        self.worker.write_text(_FRAME_PTY_WORKER, encoding="utf-8")
        self.layout = self.config_dir / "layouts" / "operator.kdl"
        self.layout.write_text(
            "layout {\n"
            f'    pane command="{sys.executable}" name="probe" {{\n'
            f'        args "-u" "{self.worker}"\n'
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        (self.config_dir / "config.kdl").write_text(
            "keybinds clear-defaults=true {}\n", encoding="utf-8"
        )
        self._prepared = True

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in _FRAME_IDENTITY_ENV:
            env.pop(key, None)
        env.update(
            {
                "HOME": str(self.home),
                "VIBECRAFTED_HOME": str(self.home / ".vibecrafted"),
                "XDG_CONFIG_HOME": str(self.home / ".config"),
                "VC_FRAME_SOCKET_DIR": str(self.socket_dir),
                "ZELLIJ_SOCKET_DIR": str(self.socket_dir),
                "VC_FRAME_CONFIG_DIR": str(self.config_dir),
                "VC_FRAME_CONFIG_FILE": str(self.config_dir / "config.kdl"),
                "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
                "VIBECRAFTED_VC_FRAME_BIN": str(self.frame),
            }
        )
        return env

    def _frame(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        assert self.frame is not None
        return subprocess.run(
            [str(self.frame), *args],
            check=False,
            env=self._env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _action(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        # Address this session explicitly: a Founder frame must never be reachable.
        return self._frame("--session", self.session, "action", *args, timeout=timeout)

    def _resolve_pane(self) -> None:
        """Identify the pane by real ownership: the command it is running."""
        deadline = time.time() + 20
        last = ""
        foreign: list[str] = []
        while time.time() < deadline:
            listed = self._action("list-panes", "--json", "--all")
            last = listed.stderr or listed.stdout
            if listed.returncode == 0:
                try:
                    panes = json.loads(listed.stdout)
                except json.JSONDecodeError:
                    panes = []
                terminals = [pane for pane in panes if not self._is_engine_chrome(pane)]
                owned = [
                    pane
                    for pane in terminals
                    if str(self.worker) in str(pane.get("terminal_command") or "")
                    and not pane.get("exited")
                ]
                if owned:
                    self._assert_layout_is_this_test(terminals)
                    pane = owned[0]
                    self.pane_id = f"terminal_{pane['id']}"
                    self._assert_pane_fits_protocol(pane)
                    return
                if terminals:
                    foreign = [
                        str(pane.get("terminal_command") or "") for pane in terminals
                    ]
            time.sleep(0.1)
        raise AssertionError(
            "isolated Frame pane running the probe was not found. This session "
            f"carries {foreign or 'no terminal pane'} instead of this test's "
            f"layout, so the create route never delivered it: {last}"
        )

    @staticmethod
    def _is_engine_chrome(pane: dict[str, object]) -> bool:
        """Plugin/suppressed panes are engine chrome, never a layout's worker."""
        return bool(
            pane.get("is_plugin")
            or pane.get("is_suppressed")
            or pane.get("is_selectable") is False
        )

    def _assert_layout_is_this_test(self, terminals: list[dict[str, object]]) -> None:
        """The session must carry THIS test's layout, not an engine default.

        A create route that drops the layout still yields a live session — one
        built from the engine default, whose panes run the Founder's shell and
        are far too narrow for the reply protocol. Owning the worker is only
        half the claim; the other half is that nothing else is in the session.
        """
        foreign = [
            str(pane.get("terminal_command") or "")
            for pane in terminals
            if str(self.worker) not in str(pane.get("terminal_command") or "")
        ]
        assert not foreign, (
            "isolated Frame session carries panes outside this test's layout, so "
            f"the chosen layout was not applied: {foreign}"
        )

    @staticmethod
    def _assert_pane_fits_protocol(pane: dict[str, object]) -> None:
        """A wrapped reply is an unreadable reply.

        dump-screen returns rendered rows, so the widest protocol line
        (`VCREPLY <16 hex> pong <pid>`) has to fit one row. If an engine default
        ever shrinks the background pane, fail here with the reason instead of
        timing out on a line that was only ever split in half.
        """
        width = pane.get("pane_content_columns") or pane.get("pane_columns")
        if not isinstance(width, int):
            return
        needed = len("VCREPLY ") + 16 + len(" pong ") + 7
        assert width >= needed, (
            f"Frame pane is {width} columns; the reply protocol needs {needed} "
            "before dump-screen wraps it"
        )

    def _dump(self) -> str:
        dumped = self._action("dump-screen", "--pane-id", self.pane_id, "--full")
        if dumped.returncode != 0:
            return ""
        return dumped.stdout

    def _await_pane_line(self, prefix: str, timeout: float) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for line in self._dump().splitlines():
                stripped = line.strip()
                if stripped.startswith(prefix):
                    return stripped[len(prefix) :]
            time.sleep(0.1)
        return ""

    def _wait_worker(self) -> None:
        # The worker announces itself on its own stdout, read back from the pane.
        announced = self._await_pane_line("VCIDENT ", 20)
        parts = announced.split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].startswith("/dev/"):
            raise AssertionError("Frame PTY worker did not publish its process identity")
        self.worker_pid = int(parts[0])
        self.worker_tty = parts[1]
        self.worker_start = subprocess.check_output(
            ["/bin/ps", "-p", str(self.worker_pid), "-o", "lstart="], text=True
        ).strip()
        assert self.worker_start, "Frame PTY worker has no start time"
        assert os.path.exists(self.worker_tty), self.worker_tty

    def _record_server(self) -> None:
        # eww includes the environment: the socket dir is often env-only, not
        # argv. Require the isolated socket AND this engine running --server so a
        # Founder frame (or any passer-by naming the path) cannot match.
        listed = subprocess.check_output(
            ["/bin/ps", "eww", "-ax", "-o", "pid=,command="], text=True
        )
        socket = str(self.socket_dir)
        engine = str(self.frame)
        for line in listed.splitlines():
            if socket not in line or "--server" not in line or engine not in line:
                continue
            pid = int(line.strip().split(None, 1)[0])
            start = subprocess.check_output(
                ["/bin/ps", "-p", str(pid), "-o", "lstart="], text=True
            ).strip()
            if start:
                self.server_pid = pid
                self.server_start = start
                return
        raise AssertionError("isolated Frame server process was not found")

    def roundtrip(self, command: str) -> str:
        """Send nonce+command to the pane's real input; read the process answer."""
        assert self.pane_id, "Frame pane was not identified"
        nonce = secrets.token_hex(8)
        typed = self._action("write-chars", "--pane-id", self.pane_id, f"{nonce} {command}")
        assert typed.returncode == 0, typed.stderr or typed.stdout
        # 13 is the CR a human Enter produces; the line discipline turns it into
        # the newline readline() is waiting for.
        entered = self._action("write", "--pane-id", self.pane_id, "13")
        assert entered.returncode == 0, entered.stderr or entered.stdout
        # Only worker output carries VCREPLY; the tty echo of the typed line does not.
        answer = self._await_pane_line(f"VCREPLY {nonce} ", 12)
        if not answer:
            raise AssertionError(f"Frame pane did not answer {command!r}")
        return answer

    def assert_survived(self) -> None:
        listing = self._frame("list-sessions", "--no-formatting")
        assert self.session in listing.stdout, listing.stdout
        after = listing.stdout.split(self.session, 1)[1]
        assert "(EXITED" not in after.splitlines()[0]
        worker_now = subprocess.check_output(
            ["/bin/ps", "-p", str(self.worker_pid), "-o", "lstart="], text=True
        ).strip()
        assert worker_now == self.worker_start
        tty_now = subprocess.check_output(
            ["/bin/ps", "-p", str(self.worker_pid), "-o", "tty="], text=True
        ).strip()
        assert tty_now and tty_now != "??"
        assert self.worker_tty.endswith(tty_now) or Path(self.worker_tty).name == tty_now
        server_now = subprocess.check_output(
            ["/bin/ps", "-p", str(self.server_pid), "-o", "lstart="], text=True
        ).strip()
        assert server_now == self.server_start
        # Same addressed pane, still running the same owned command.
        pane_before = self.pane_id
        self._resolve_pane()
        assert self.pane_id == pane_before
        # A NEW answer: liveness, not a replay of the startup roundtrip.
        assert self.roundtrip("ping") == f"pong {self.worker_pid}"
        assert self.roundtrip("identify") == str(self.worker_pid)

    def close(self) -> None:
        if self.frame is not None and self._prepared:
            # kill-session already removes the session; delete-session may then
            # report it as unknown, which is not a failure of teardown.
            self._frame("kill-session", self.session)
            self._frame("delete-session", self.session, "--force")
        if self.root.exists():
            try:
                current = self.root.stat()
            except OSError:
                return
            if (current.st_dev, current.st_ino) == self._owned:
                shutil.rmtree(self.root, ignore_errors=True)


def _replace_prior_with_candidate(
    *,
    dest: Path,
    source: Path,
    receipt: Path,
    apps: _SignedApps,
) -> dict[str, object]:
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
        assert sleeper.poll() is None
        sleeper.terminate()
        sleeper.wait(timeout=5)
        assert helper.wait(timeout=90) == 0
    finally:
        if sleeper.poll() is None:
            sleeper.terminate()
            sleeper.wait(timeout=5)
        if helper is not None and helper.poll() is None:
            helper.terminate()
            helper.wait(timeout=5)
    assert _identity_token(dest) == apps.e37_identity
    return json.loads(Path(str(receipt) + ".journal.json").read_text(encoding="utf-8"))


def test_product_update_cross_generation_publish_then_restore_previous_tuple(
    tmp_path: Path,
) -> None:
    founder_before = _founder_identity_stamps()
    session = _IsolatedFrameSession()
    try:
        session.start()
        env = _isolated_product_env(tmp_path, frame_socket_dir=session.socket_dir)
        settings = _plant_custom_settings(env)
        runtime_home = Path(env["VIBECRAFTED_RUNTIME_HOME"])
        with _SignedApps(tmp_path) as apps:
            dest = apps.copy_prior(tmp_path / "Installed.app")
            source = apps.copy_e37(tmp_path / "Candidate.app")
            prior_pack = _install_signed_pack(apps.prior["pack"], dest, env)
            assert prior_pack.returncode == 0, prior_pack.stderr or prior_pack.stdout
            prior_pub = _read_installer_publication(runtime_home)
            assert "79001c3d" in str(prior_pub["version"]).lower()
            _assert_isolated_receipt_roots(prior_pub["receipt"], tmp_path)  # type: ignore[arg-type]
            _assert_receipt_names_live_app(prior_pub["receipt"], dest)  # type: ignore[arg-type]

            receipt = tmp_path / "receipt.json"
            journal = _replace_prior_with_candidate(
                dest=dest, source=source, receipt=receipt, apps=apps
            )
            apps.release(source)
            published = _install_signed_pack(
                apps.e37["pack"], dest, env, fail_after="published"
            )
            assert published.returncode == 42, published.stderr or published.stdout
            assert "harness injected failure after published" in (published.stderr or "")
            e37_pub = _read_installer_publication(runtime_home)
            assert "e37be2c9" in str(e37_pub["version"]).lower()
            assert e37_pub["version"] != prior_pub["version"]
            _assert_isolated_receipt_roots(e37_pub["receipt"], tmp_path)  # type: ignore[arg-type]
            _assert_receipt_names_live_app(e37_pub["receipt"], dest)  # type: ignore[arg-type]

            capture = Path(journal["capture"])
            prior_app = capture / "prior.app"
            assert _identity_token(prior_app) == apps.prior_identity
            recover_receipt = tmp_path / "recover-a.json"
            recovered = _run_helper(
                [
                    "--source",
                    str(prior_app),
                    "--destination",
                    str(dest),
                    "--receipt",
                    str(recover_receipt),
                    "--journal",
                    str(receipt) + ".journal.json",
                    "--transaction",
                    str(journal["transaction"]),
                    "--mode",
                    "recover",
                ],
                env={**_helper_env(), **env},
                timeout=300,
            )
            assert recovered.returncode == 0, recovered.stderr or recovered.stdout
            payload = json.loads(recover_receipt.read_text(encoding="utf-8"))
            assert payload["replaced"] is True
            assert payload["recovered"] is True
            assert payload["detail"] == "recovered"
            assert payload["mode"] == "recover"
            assert payload["operation"] == "recover"
            assert payload["installer_status"] == "0"
            assert payload["pack_generation"] == prior_pub["version"]
            assert _identity_token(dest) == apps.prior_identity
            assert "79001c3d" in str(prior_pub["version"]).lower()
            _assert_shared_recovered_tuple(
                env=env,
                dest=dest,
                tmp_path=tmp_path,
                prior_pub=prior_pub,
                settings=settings,
                session=session,
            )
            evidence_path = tmp_path / "pack-evidence.json"
            assert not evidence_path.exists(), "caller-written pack enum is not installer proof"
            _assert_founder_identity_untouched(founder_before)
    finally:
        session.close()


def test_product_update_whole_tuple_recovery_fails_on_damaged_signed_historical_app(
    tmp_path: Path,
) -> None:
    founder_before = _founder_identity_stamps()
    env = _isolated_product_env(tmp_path)
    runtime_home = Path(env["VIBECRAFTED_RUNTIME_HOME"])
    with _SignedApps(tmp_path) as apps:
        dest = apps.copy_prior(tmp_path / "Installed.app")
        source = apps.copy_e37(tmp_path / "Candidate.app")
        prior_pack = _install_signed_pack(apps.prior["pack"], dest, env)
        assert prior_pack.returncode == 0, prior_pack.stderr or prior_pack.stdout
        prior_pub = _read_installer_publication(runtime_home)
        receipt = tmp_path / "receipt.json"
        journal = _replace_prior_with_candidate(
            dest=dest, source=source, receipt=receipt, apps=apps
        )
        apps.release(source)
        published = _install_signed_pack(
            apps.e37["pack"], dest, env, fail_after="published"
        )
        assert published.returncode == 42, published.stderr or published.stdout
        e37_pub = _read_installer_publication(runtime_home)
        capture = Path(journal["capture"])
        prior_app = capture / "prior.app"
        pack_dir = prior_app / "Contents/Resources/runtime-pack"
        assert pack_dir.is_dir(), (
            "the signed prior.app is the only rollback authority and must embed "
            f"its Runtime Pack: {pack_dir}"
        )
        assert not (capture / "historical-runtime-pack").exists(), (
            "replace must not mint a second rollback authority beside the capture"
        )
        shutil.rmtree(pack_dir)
        recover_receipt = tmp_path / "recover-damaged-signed.json"
        failed = _run_helper(
            [
                "--source",
                str(prior_app),
                "--destination",
                str(dest),
                "--receipt",
                str(recover_receipt),
                "--journal",
                str(receipt) + ".journal.json",
                "--transaction",
                str(journal["transaction"]),
                "--mode",
                "recover",
            ],
            env={**_helper_env(), **env},
            timeout=120,
        )
        err = failed.stderr or ""
        assert failed.returncode == 15, err or failed.stdout
        assert "codesign --verify --strict failed" in err
        assert "missing historical rollback data" not in err, (
            "a broken seal must refuse at codesign, never reach the pack lookup"
        )
        assert not recover_receipt.is_file() or "recovered" not in recover_receipt.read_text(
            encoding="utf-8"
        )
        assert _identity_token(dest) == apps.e37_identity
        still = _read_installer_publication(runtime_home)
        assert still["version"] == e37_pub["version"]
        assert still["version"] != prior_pub["version"]
        _assert_founder_identity_untouched(founder_before)


def test_product_update_whole_tuple_recovery_interrupted_then_resumed(
    tmp_path: Path,
) -> None:
    founder_before = _founder_identity_stamps()
    session = _IsolatedFrameSession()
    try:
        session.start()
        env = _isolated_product_env(tmp_path, frame_socket_dir=session.socket_dir)
        settings = _plant_custom_settings(env)
        runtime_home = Path(env["VIBECRAFTED_RUNTIME_HOME"])
        helper_env = {**_helper_env(), **env}
        with _SignedApps(tmp_path) as apps:
            dest = apps.copy_prior(tmp_path / "Installed.app")
            source = apps.copy_e37(tmp_path / "Candidate.app")
            prior_pack = _install_signed_pack(apps.prior["pack"], dest, env)
            assert prior_pack.returncode == 0, prior_pack.stderr or prior_pack.stdout
            prior_pub = _read_installer_publication(runtime_home)
            receipt = tmp_path / "receipt.json"
            journal = _replace_prior_with_candidate(
                dest=dest, source=source, receipt=receipt, apps=apps
            )
            apps.release(source)
            published = _install_signed_pack(
                apps.e37["pack"], dest, env, fail_after="published"
            )
            assert published.returncode == 42, published.stderr or published.stdout
            capture = Path(journal["capture"])
            prior_app = capture / "prior.app"
            gate = tmp_path / "hold-recover"
            recover_receipt = tmp_path / "recover-a.json"
            first = None
            try:
                first = subprocess.Popen(
                    [
                        str(HELPER),
                        "--source",
                        str(prior_app),
                        "--destination",
                        str(dest),
                        "--receipt",
                        str(recover_receipt),
                        "--journal",
                        str(receipt) + ".journal.json",
                        "--transaction",
                        str(journal["transaction"]),
                        "--mode",
                        "recover",
                        "--hold-after",
                        "runtime_restored",
                        "--until",
                        str(gate),
                    ],
                    env=helper_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                recover_admission = Path(str(recover_receipt) + ".admission.json")
                deadline = time.time() + 180
                while time.time() < deadline and not recover_admission.is_file():
                    if first.poll() is not None:
                        break
                    time.sleep(0.05)
                assert recover_admission.is_file(), first.stderr
                mid_deadline = time.time() + 180
                mid = None
                while time.time() < mid_deadline:
                    if first.poll() is not None:
                        break
                    try:
                        mid = _read_installer_publication(runtime_home)
                    except AssertionError:
                        time.sleep(0.2)
                        continue
                    if mid["version"] == prior_pub["version"]:
                        break
                    time.sleep(0.2)
                assert mid is not None and mid["version"] == prior_pub["version"]
                assert _identity_token(dest) == apps.e37_identity
                held = dest.parent / ".vc-update.lock" / "held"
                assert held.is_file()
                held_inode = held.stat()
                held_key = (held_inode.st_dev, held_inode.st_ino)
                concurrent = _run_helper(
                    [
                        "--source",
                        str(prior_app),
                        "--destination",
                        str(dest),
                        "--receipt",
                        str(tmp_path / "recover-b.json"),
                        "--journal",
                        str(receipt) + ".journal.json",
                        "--transaction",
                        str(journal["transaction"]),
                        "--mode",
                        "recover",
                        "--resume",
                    ],
                    env=helper_env,
                )
                assert concurrent.returncode == 13
                assert held.is_file()
                after_concurrent = held.stat()
                assert (after_concurrent.st_dev, after_concurrent.st_ino) == held_key
                first.send_signal(signal.SIGTERM)
                first.wait(timeout=20)
                after_term = held.stat()
                assert (after_term.st_dev, after_term.st_ino) == held_key
                resumed = _run_helper(
                    [
                        "--source",
                        str(prior_app),
                        "--destination",
                        str(dest),
                        "--receipt",
                        str(tmp_path / "recover-resume.json"),
                        "--journal",
                        str(receipt) + ".journal.json",
                        "--transaction",
                        str(journal["transaction"]),
                        "--mode",
                        "recover",
                        "--resume",
                    ],
                    env=helper_env,
                    timeout=300,
                )
                assert resumed.returncode == 0, resumed.stderr or resumed.stdout
                after_resume = held.stat()
                assert (after_resume.st_dev, after_resume.st_ino) == held_key
            finally:
                if first is not None and first.poll() is None:
                    gate.write_text("go", encoding="utf-8")
                    first.terminate()
                    first.wait(timeout=5)

            assert _identity_token(dest) == apps.prior_identity
            resume_payload = json.loads(
                (tmp_path / "recover-resume.json").read_text(encoding="utf-8")
            )
            assert resume_payload["recovered"] is True
            # The resumed recover returns through restore_previous_tuple, the
            # epilogue restore shares. The receipt must answer for the mode that
            # was resumed, not for the function that carried it home.
            assert resume_payload["detail"] == "recovered"
            assert resume_payload["mode"] == "recover"
            assert resume_payload["operation"] == "recover"
            assert resume_payload["pack_generation"] == prior_pub["version"]
            _assert_shared_recovered_tuple(
                env=env,
                dest=dest,
                tmp_path=tmp_path,
                prior_pub=prior_pub,
                settings=settings,
                session=session,
            )
            _assert_founder_identity_untouched(founder_before)
    finally:
        session.close()


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
            str(APP / "ServerMenuPolicy.swift"),
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
