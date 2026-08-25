from __future__ import annotations

import copy
import hashlib
import json
import os
import plistlib
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import product_contract as contract

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = REPO_ROOT / "scripts/verify-vibecrafted-product.sh"
SCHEMA_PATH = (
    REPO_ROOT
    / "vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prefixed_sha256(path: Path) -> str:
    return f"sha256:{_sha256(path)}"


def _write_executable(path: Path, text: str = "#!/bin/sh\nexit 0\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_canonical_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _clang() -> str:
    if sys.platform != "darwin":
        pytest.skip("unified app contract requires macOS Mach-O tooling")
    xcrun = shutil.which("xcrun")
    if xcrun is None:
        pytest.skip("xcrun is unavailable")
    return xcrun


def _compile_macho(
    path: Path,
    *,
    source: str = "int main(void) { return 0; }\n",
    architecture: str = "arm64",
    minimum_macos: str = "14.0",
    extra_args: tuple[str, ...] = (),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            _clang(),
            "--sdk",
            "macosx",
            "clang",
            "-arch",
            architecture,
            f"-mmacosx-version-min={minimum_macos}",
            *extra_args,
            "-x",
            "c",
            "-",
            "-o",
            str(path),
        ],
        input=source,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    path.chmod(0o755)


@pytest.fixture(scope="session")
def macho_executable(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("unified-contract-macho") / "fixture"
    _compile_macho(path)
    return path


def _macho_dependencies(path: Path) -> list[str]:
    result = subprocess.run(
        ["otool", "-L", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        line.strip().split(" (", 1)[0]
        for line in result.stdout.splitlines()[1:]
        if line.strip()
    ]


def _codesign_macho(path: Path) -> None:
    result = subprocess.run(
        ["codesign", "--force", "--sign", "-", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _codesign_app(app: Path) -> None:
    result = subprocess.run(
        ["codesign", "--force", "--sign", "-", str(app)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _write_app_manifest(app: Path, manifest: dict[str, Any], *, sign: bool) -> None:
    _write_json(app / "Contents/Resources/product-manifest.json", manifest)
    if sign:
        _codesign_app(app)


def _entry_from_path(path: Path, relative: str, *, kind: str) -> dict[str, Any]:
    return {
        "path": relative,
        "sha256": _sha256(path),
        "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
        "kind": kind,
        "size": path.stat().st_size,
        "dylibs": _macho_dependencies(path) if kind in {"executable", "dylib"} else [],
    }


def _entry(
    root: Path,
    relative: str,
    *,
    kind: str,
    dylibs: list[str] | None = None,
) -> dict[str, Any]:
    path = root / relative
    if dylibs is None and kind in {"executable", "dylib"}:
        dylibs = _macho_dependencies(path)
    return {
        "path": relative,
        "sha256": _sha256(path),
        "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
        "kind": kind,
        "size": path.stat().st_size,
        "dylibs": dylibs or [],
    }


def _module_fixture(
    root: Path, macho_executable: Path, module: str = "vc-terminal"
) -> dict[str, Any]:
    entrypoint_name = "terminal" if module == "vc-terminal" else "frame"
    executable = f"bin/{module}"
    (root / executable).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(macho_executable, root / executable)
    manifest = {
        "schema": contract.MODULE_SCHEMA,
        "module": module,
        "version": "1.0.0",
        "git_sha": "1" * 40,
        "dirty": False,
        "architecture": "arm64",
        "minimum_macos": "14.0",
        "files": [_entry(root, executable, kind="executable")],
        "entrypoints": {entrypoint_name: executable},
    }
    _write_json(root / "module-manifest.json", manifest)
    return manifest


def _app_fixture(app: Path, macho_executable: Path) -> dict[str, Any]:
    terminal_relative = "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty"
    for relative in (
        "Contents/MacOS/Vibecrafted",
        terminal_relative,
        "Contents/Helpers/vc-frame",
        "Contents/Resources/runtime/bin/vc-start",
    ):
        (app / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(macho_executable, app / relative)
    for relative in (
        terminal_relative,
        "Contents/Helpers/vc-frame",
        "Contents/Resources/runtime/bin/vc-start",
    ):
        _codesign_macho(app / relative)
    primary_shell = app / contract._LAUNCH_PRIMARY_SHELL
    primary_shell.parent.mkdir(parents=True, exist_ok=True)
    primary_shell.write_text('#!/bin/zsh\nexec vc-start "$@"\n', encoding="utf-8")
    primary_shell.chmod(0o755)
    terminal_app = app / "Contents/Helpers/vc-terminal.app"
    terminal_icon = terminal_app / "Contents/Resources/alacritty.icns"
    terminal_icon.parent.mkdir(parents=True, exist_ok=True)
    terminal_icon.write_bytes(b"terminal-icns-fixture")
    with (terminal_app / "Contents/Info.plist").open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleIdentifier": "io.vetcoders.vc-terminal",
                "CFBundleExecutable": "alacritty",
                "CFBundleIconFile": "alacritty.icns",
                "CFBundlePackageType": "APPL",
            },
            handle,
        )
    _codesign_app(terminal_app)
    terminal_config = app / "Contents/Resources/terminal/vibecrafted.toml"
    terminal_config.parent.mkdir(parents=True, exist_ok=True)
    terminal_config.write_text("[shell]\nprogram = 'vc-start'\n", encoding="utf-8")
    icon = app / "Contents/Resources" / contract.PRODUCT_ICON_FILE
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(b"icns-fixture")
    plist = app / "Contents/Info.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    with plist.open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleIdentifier": contract.PRODUCT_BUNDLE_ID,
                "CFBundleExecutable": contract.PRODUCT_EXECUTABLE,
                "CFBundleIconFile": contract.PRODUCT_ICON_FILE,
                "CFBundleShortVersionString": "1.0.0",
                "CFBundleVersion": "1",
            },
            handle,
        )
    terminal_product_entry = _entry(app, terminal_relative, kind="executable")
    frame_product_entry = _entry(app, "Contents/Helpers/vc-frame", kind="executable")

    def add_module_binding(
        *,
        module: str,
        git_sha: str,
        entrypoint_name: str,
        product_entry: dict[str, Any],
    ) -> dict[str, Any]:
        module_path = f"bin/{module}"
        module_entry = _entry_from_path(
            macho_executable,
            module_path,
            kind=str(product_entry["kind"]),
        )
        receipt = {
            "schema": contract.MODULE_SCHEMA,
            "module": module,
            "version": "1.0.0",
            "git_sha": git_sha,
            "dirty": False,
            "architecture": "arm64",
            "minimum_macos": "14.0",
            "files": [module_entry],
            "entrypoints": {entrypoint_name: module_path},
        }
        relative = f"Contents/Resources/module-receipts/{module}/module-manifest.json"
        _write_json(app / relative, receipt)
        receipt_hash = _sha256(app / relative)
        assembly = {
            "schema": contract.ASSEMBLY_SCHEMA,
            "module": module,
            "module_manifest_sha256": receipt_hash,
            "files": [
                {
                    "module_path": module_path,
                    "product_path": product_entry["path"],
                    "unsigned_sha256": module_entry["sha256"],
                    "product_sha256": product_entry["sha256"],
                    "transformation": "codesign",
                }
            ],
        }
        assembly_relative = (
            f"Contents/Resources/module-receipts/{module}/assembly-receipt.json"
        )
        _write_json(app / assembly_relative, assembly)
        return {
            "module": module,
            "manifest_path": relative,
            "manifest_sha256": receipt_hash,
            "assembly_receipt_path": assembly_relative,
            "assembly_receipt_sha256": _sha256(app / assembly_relative),
            "git_sha": git_sha,
        }

    terminal_binding = add_module_binding(
        module="vc-terminal",
        git_sha="4" * 40,
        entrypoint_name="terminal",
        product_entry=terminal_product_entry,
    )
    frame_binding = add_module_binding(
        module="vc-frame",
        git_sha="6" * 40,
        entrypoint_name="frame",
        product_entry=frame_product_entry,
    )
    manifest = {
        "schema": contract.PRODUCT_SCHEMA,
        "product": contract.PRODUCT_NAME,
        "bundle_id": contract.PRODUCT_BUNDLE_ID,
        "bundle_executable": contract.PRODUCT_EXECUTABLE,
        "version": "1.0.0",
        "build": "1",
        "git_sha": "2" * 40,
        "dirty": False,
        "architecture": "arm64",
        "minimum_macos": "14.0",
        "modules": [terminal_binding, frame_binding],
        "outer_bundle_code": {
            "identity": contract.OUTER_BUNDLE_CODE_IDENTITY,
            "path": "Contents/MacOS/Vibecrafted",
            "mode": "0755",
            "kind": "executable",
            "architecture": "arm64",
            "minimum_macos": "14.0",
            "dylibs": _macho_dependencies(app / "Contents/MacOS/Vibecrafted"),
            "code_identity": contract.MACHO_CODE_IDENTITY,
            "code_sha256": contract._macho_code_sha256(
                app / "Contents/MacOS/Vibecrafted"
            ),
            "info_plist_sha256": _sha256(plist),
            "codesign_identifier": contract.PRODUCT_BUNDLE_ID,
        },
        "files": [
            _entry(app, "Contents/Info.plist", kind="config"),
            _entry(
                app,
                f"Contents/Resources/{contract.PRODUCT_ICON_FILE}",
                kind="resource",
            ),
            terminal_product_entry,
            frame_product_entry,
            _entry(
                app,
                "Contents/Helpers/vc-terminal.app/Contents/Info.plist",
                kind="config",
            ),
            _entry(
                app,
                "Contents/Helpers/vc-terminal.app/Contents/Resources/alacritty.icns",
                kind="resource",
            ),
            _entry(app, terminal_binding["manifest_path"], kind="config"),
            _entry(app, frame_binding["manifest_path"], kind="config"),
            _entry(app, terminal_binding["assembly_receipt_path"], kind="config"),
            _entry(app, frame_binding["assembly_receipt_path"], kind="config"),
            _entry(
                app,
                "Contents/Resources/terminal/vibecrafted.toml",
                kind="config",
            ),
            _entry(
                app,
                "Contents/Resources/runtime/bin/vc-start",
                kind="executable",
            ),
            _entry(app, contract._LAUNCH_PRIMARY_SHELL, kind="resource"),
        ],
        "entrypoints": {
            "app": "Contents/MacOS/Vibecrafted",
            "terminal": terminal_relative,
            "frame": "Contents/Helpers/vc-frame",
        },
        "launch_contract": copy.deepcopy(contract._canonical_launch_contract()),
    }
    _write_app_manifest(app, manifest, sign=True)
    return manifest


def _identity(
    version: str,
    manifest_path: str,
    digest: str,
    revision: str,
    *,
    artifact: str,
) -> dict[str, str]:
    return {
        "version": version,
        "manifest_path": manifest_path,
        f"{artifact}_manifest_sha256": digest,
        "source_revision": revision,
    }


def _runtime_source_payload() -> dict[str, object]:
    return {
        "schema": contract.SOURCE_PAYLOAD_SCHEMA,
        "algorithm": "sha256",
        "tree_sha256": "9" * 64,
        "entry_count": 42,
    }


def _transaction_fixture(path: Path, macho_executable: Path) -> dict[str, Any]:
    product_manifest = path.parent / "manifests/product-manifest.json"
    runtime_manifest = path.parent / "manifests/runtime-manifest.json"
    transaction_app = path.parent / "transaction-product/Vibecrafted.app"
    product_payload = _app_fixture(transaction_app, macho_executable)
    product_payload["git_sha"] = "6" * 40
    _write_json(product_manifest, product_payload)
    _write_json(
        runtime_manifest,
        {
            "schema": contract.RUNTIME_GENERATION_SCHEMA,
            "version": "1.0.0",
            "source_fingerprint": "7" * 64,
            "owner_repo": "vetcoders/vibecrafted",
            "source_revision": "8" * 40,
            "source_payload": _runtime_source_payload(),
            "entrypoint": contract.RUNTIME_GENERATION_ENTRYPOINT,
            "hashes": {
                relative: f"{index:x}" * 64
                for index, relative in enumerate(
                    sorted(contract.RUNTIME_GENERATION_REQUIRED_HASHES), start=1
                )
            },
        },
    )
    product_relative = product_manifest.relative_to(path.parent).as_posix()
    runtime_relative = runtime_manifest.relative_to(path.parent).as_posix()
    new = {
        "state": "present",
        "app": _identity(
            "1.0.0",
            product_relative,
            _sha256(product_manifest),
            "6" * 40,
            artifact="product",
        ),
        "runtime": _identity(
            "1.0.0",
            runtime_relative,
            _sha256(runtime_manifest),
            "8" * 40,
            artifact="runtime",
        ),
    }
    payload = {
        "schema": contract.TRANSACTION_SCHEMA,
        "transaction_id": "tx-test",
        "previous": {"state": "absent"},
        "new": new,
        "active": copy.deepcopy(new),
        "outcome": "activated",
    }
    _write_json(path, payload)
    return payload


def _proof_artifact(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _captured_signature_pair(
    payload: Path, signature: Path
) -> tuple[contract._CapturedProofArtifact, contract._CapturedProofArtifact]:
    """Test double for the production verifier's single immutable tuple contract."""
    return (
        contract._capture_proof_artifact(payload, context="test signed payload"),
        contract._capture_proof_artifact(signature, context="test detached signature"),
    )


def _runner_walkaround_fixture(root: Path) -> dict[str, Any]:
    probes: dict[str, Any] = {}
    for spec in contract._probe_registry():
        observation: dict[str, Any] = {
            "probe_id": contract._walkaround_probe_id(spec.name),
            "executor": spec.executor,
            "owner_stage": spec.owner_stage,
            "operation_id": spec.operation_id,
            "assertions": [
                {"name": name, "passed": True, "evidence_sha256": "0" * 64}
                for name in spec.assertions
            ],
        }
        if spec.executor == "argv":
            observation.update(
                {
                    "command": list(spec.argv or ()),
                    "exit_code": 0,
                    "stdout_sha256": "0" * 64,
                    "stderr_sha256": "0" * 64,
                }
            )
        probes[spec.name] = observation
    payload = {
        "schema": contract.WALKAROUND_SCHEMA,
        "release_output": _proof_artifact(root, root / "release-output.json"),
        "release_signature": _proof_artifact(root, root / "release-output.json.sig"),
        "observations": {
            "issuer": contract.WALKAROUND_RUNNER_ID,
            "probes": probes,
        },
    }
    _write_json(root / "walkaround.json", payload)
    return payload


def _release_output_fixture(
    root: Path,
    app: Path,
    dmg: Path,
    *,
    signer: dict[str, Any] | None = None,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any]]:
    product = json.loads(
        (app / "Contents/Resources/product-manifest.json").read_text(encoding="utf-8")
    )
    modules = {item["module"]: item for item in product["modules"]}
    signer = signer or {
        "cdhash": "a" * 40,
        "team_id": "MW223P3NPX",
        "designated_requirement": contract._release_policy()["designated_requirement"],
        "hardened_runtime": True,
        "entitlements": {},
    }
    policy = contract._release_policy()
    executable = app / product["outer_bundle_code"]["path"]
    payload = {
        "schema": contract.RELEASE_OUTPUT_SCHEMA,
        "signature_policy": {
            "algorithm": "rsa-pkcs1v15-sha256",
            "key_id": "vibecrafted-signing-v1",
            "spki_sha256": contract.RELEASE_KEY_SPKI_SHA256,
        },
        "product": {
            "version": product["version"],
            "build": product["build"],
            "architecture": product["architecture"],
            "minimum_macos": product["minimum_macos"],
            "manifest": {
                "path": "Contents/Resources/product-manifest.json",
                "sha256": _sha256(app / "Contents/Resources/product-manifest.json"),
            },
        },
        "outer_executable": {
            "path": product["outer_bundle_code"]["path"],
            "sha256": _sha256(executable),
            "code_identity": contract.MACHO_CODE_IDENTITY,
            "code_sha256": contract._macho_code_sha256(executable),
            "cdhash": signer["cdhash"],
            "signer_policy": {
                "team_id": policy["team_id"],
                "designated_requirement": policy["designated_requirement"],
                "hardened_runtime": policy["hardened_runtime"],
                "entitlements": policy["entitlements"],
            },
        },
        "code_resources": {
            "path": "Contents/_CodeSignature/CodeResources",
            "sha256": _sha256(app / "Contents/_CodeSignature/CodeResources"),
        },
        "dmg": {
            "path": dmg.relative_to(root).as_posix(),
            "sha256": _sha256(dmg),
            "size": dmg.stat().st_size,
        },
        "modules": {
            name: {
                "manifest": {
                    "path": binding["manifest_path"],
                    "sha256": binding["manifest_sha256"],
                },
                "assembly_receipt": {
                    "path": binding["assembly_receipt_path"],
                    "sha256": binding["assembly_receipt_sha256"],
                },
                "git_sha": binding["git_sha"],
            }
            for name, binding in modules.items()
        },
        "source_revisions": {
            "vibecrafted": product["git_sha"],
            "vc-terminal": modules["vc-terminal"]["git_sha"],
            "vc-frame": modules["vc-frame"]["git_sha"],
        },
        "notarization": {
            "app": {"ticket": True, "gatekeeper": True},
            "dmg": {"codesign": True, "ticket": True, "gatekeeper": True},
        },
    }
    receipt = root / "release-output.json"
    signature = root / "release-output.json.sig"
    _write_canonical_json(receipt, payload)
    signature.write_bytes(b"fixture-signature")
    return receipt, signature, payload, signer


def _canonical_release_dmg_path(root: Path, app: Path) -> Path:
    product = json.loads(
        (app / "Contents/Resources/product-manifest.json").read_text(encoding="utf-8")
    )
    return root / contract.canonical_release_dmg_name(
        version=product["version"],
        release_date="20260814",
        source_revision=product["git_sha"],
    )


def _sign_release_receipt(receipt: Path, signature: Path) -> None:
    signing_key = Path.home() / ".keys/vibecrafted-signing.key"
    result = subprocess.run(
        [
            "/usr/bin/openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(signing_key),
            "-out",
            str(signature),
            str(receipt),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _developer_id_release_app(root: Path, macho_executable: Path) -> Path:
    """Build nested-first and apply the outer Developer-ID signature without --deep."""
    app = root / "Vibecrafted.app"
    _app_fixture(app, macho_executable)
    nested = [
        app / "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty",
        app / "Contents/Helpers/vc-frame",
        app / "Contents/Resources/runtime/bin/vc-start",
    ]
    nested_before = {path: _sha256(path) for path in nested}
    empty_entitlements = root / "empty-entitlements.plist"
    empty_entitlements.write_bytes(plistlib.dumps({}))
    signed = subprocess.run(
        [
            "/usr/bin/codesign",
            "--force",
            "--options",
            "runtime",
            "--entitlements",
            str(empty_entitlements),
            "--sign",
            "Developer ID Application: Maciej Gad (MW223P3NPX)",
            str(app),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert signed.returncode == 0, signed.stderr
    assert {path: _sha256(path) for path in nested} == nested_before
    return app


def _create_signed_release_dmg(
    root: Path, app: Path, *, name: str | None = None
) -> Path:
    """Create a real UDZO with one top-level app, then Developer-ID sign it."""
    staging = root / "dmg-staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / "Vibecrafted.app", copy_function=shutil.copy2)
    dmg = root / name if name is not None else _canonical_release_dmg_path(root, app)
    dmg.unlink(missing_ok=True)
    created = subprocess.run(
        [
            "/usr/bin/hdiutil",
            "create",
            "-quiet",
            "-volname",
            "Vibecrafted",
            "-srcfolder",
            str(staging),
            "-format",
            "UDZO",
            str(dmg),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    signed = subprocess.run(
        [
            "/usr/bin/codesign",
            "--force",
            "--sign",
            "Developer ID Application: Maciej Gad (MW223P3NPX)",
            str(dmg),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert signed.returncode == 0, signed.stderr
    return dmg


def _mutate_macho_code_byte(path: Path) -> None:
    payload = bytearray(path.read_bytes())
    sizeofcmds = struct.unpack_from("<I", payload, 20)[0]
    code_offset = 32 + sizeofcmds
    assert code_offset < len(payload)
    payload[code_offset] ^= 0x01
    path.write_bytes(payload)


def _assert_error(code: int, call: Callable[[], Any]) -> None:
    with pytest.raises(contract.ProductContractError) as exc_info:
        call()
    assert exc_info.value.code == code


def _patch_release_mount(monkeypatch: pytest.MonkeyPatch, app: Path) -> None:
    @contextmanager
    def mounted(_dmg: Path):
        yield app

    monkeypatch.setattr(contract, "_mounted_release_dmg", mounted)
    monkeypatch.setattr(contract, "_run_live_release_checks", lambda *_: {})


def test_native_app_bootstraps_and_launches_only_the_canonical_product_entry() -> None:
    delegate = (
        REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/AppDelegate.swift"
    ).read_text(encoding="utf-8")
    info = (
        REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/Info.plist"
    ).read_text(encoding="utf-8")
    cargo = (REPO_ROOT / "vibecrafted-app/tui-agent/Cargo.toml").read_text(
        encoding="utf-8"
    )
    launcher = (REPO_ROOT / "vibecrafted-app/tui-agent/src/bin/vc_start.rs").read_text(
        encoding="utf-8"
    )

    launch_handler = delegate[
        delegate.index("func applicationDidFinishLaunching") : delegate.index(
            "func applicationShouldTerminateAfterLastWindowClosed"
        )
    ]
    assert "launchWorkspaceTerminal()" in launch_handler
    assert "showMainWindowIfNeeded()" not in launch_handler
    assert "\t<key>LSUIElement</key>\n\t<true/>" in info
    assert 'withTitle: "VC Console"' in delegate
    assert 'withTitle: "VC Terminal"' in delegate
    assert 'withTitle: "VC Server"' in delegate
    assert 'withTitle: "Server Diagnostics…"' in delegate
    assert 'withTitle: "About"' in delegate
    assert 'withTitle: "Help"' in delegate
    assert 'withTitle: "Quit"' in delegate
    assert "#selector(requestQuit)" in delegate
    assert 'process.arguments = ["status", "--activity", "--json"]' in delegate
    assert "func applicationShouldTerminate(" in delegate
    assert 'withTitle: "Cancel"' in delegate
    assert 'withTitle: "Quit Anyway"' in delegate
    assert 'appendingPathComponent("server/supervisor.status.json")' in delegate
    assert 'title: "Server: RESTARTING…"' in delegate
    assert 'process.arguments = ["server", "service", "reconcile"]' in delegate
    assert "menu.delegate = self" in delegate
    assert "statusRefreshTimer = Timer.scheduledTimer(" in delegate
    assert "statusIcon(health:" in delegate
    assert "health.color.setFill()" in delegate
    assert "process.isRunning" in delegate
    assert (
        "NSRunningApplication(processIdentifier: process.processIdentifier)?.activate(options: [])"
        in delegate
    )
    termination_handler = delegate[
        delegate.index(
            "func applicationShouldTerminateAfterLastWindowClosed"
        ) : delegate.index("func applicationSupportsSecureRestorableState")
    ]
    assert "    false\n" in termination_handler
    assert "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty" in delegate
    assert 'appendingPathComponent("scripts/vetcoders_install.py")' in delegate
    assert '"runtime-install"' in delegate
    assert '"runtime-uninstall"' in delegate
    assert '"--payload-root", runtime.path' in delegate
    assert '"--terminal-host", terminalHost.path' in delegate
    assert '"--frame-helper", frameHelper.path' in delegate
    assert "JSONDecoder().decode(CanonicalRuntimeInstall.self" in delegate
    # The installer returns POSIX paths, not URL strings. Decoding them directly
    # as URL produces relative URLs whose `.path` passes file checks but which
    # Foundation.Process rejects as an executableURL on macOS.
    assert "container.decode(String.self, forKey: key)" in delegate
    assert 'guard path.hasPrefix("/")' in delegate
    assert "URL(fileURLWithPath: path)" in delegate
    # AppDelegate is the UI/process host, not a second installer implementation.
    assert "createDirectory(at:" not in delegate
    assert "copyItem(at:" not in delegate
    assert "writeLauncher(" not in delegate
    assert 'appendingPathComponent("active.json")' not in delegate
    # PATH composes: the signed generation wins, the caller's PATH survives behind
    # it. A hard-coded system-only PATH strips Homebrew/~/.local/bin/~/.cargo/bin
    # from every spawned agent CLI, so `#!/usr/bin/env` shebangs exit 127.
    assert 'environment["PATH"] = composedPath(' in delegate
    assert 'environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"' not in delegate
    assert '["server", "service", "reconcile"]' in delegate
    assert "shell-agent" not in delegate
    assert 'name = "vc-start"' in cargo
    assert '"--noprofile"' in launcher
    assert '"--norc"' in launcher
    assert 'source "$1"; shift; vc-start "$@"' in launcher
    assert 'Command::new("/bin/bash")' in launcher
    assert "fn host_agent_search_path(" in launcher
    assert '"/opt/homebrew/bin"' in launcher
    # vc-start composes: the PATH AppDelegate hands it (generation first, the
    # operator's Homebrew/npm/cargo/nvm tail behind) must survive the handoff,
    # sanitized rather than amputated — a closed allowlist here re-created the
    # exit-127 shebang failures the composed PATH fixed one process earlier.
    assert 'let inherited_path = env::var("PATH").ok();' in launcher
    assert "inherited_path.as_deref()" in launcher
    assert "!entry.starts_with('/')" in launcher


def test_tracked_product_source_contains_no_symlinks() -> None:
    index = subprocess.check_output(["git", "ls-files", "-s"], cwd=REPO_ROOT, text=True)
    tracked_symlinks = [
        line for line in index.splitlines() if line.startswith("120000 ")
    ]

    assert tracked_symlinks == []


def test_versioned_json_schema_matches_runtime_contract_ids() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "io.vetcoders.vibecrafted.contracts.v1"
    assert schema["$defs"]["moduleManifest"]["properties"]["schema"]["const"] == (
        contract.MODULE_SCHEMA
    )
    assert schema["$defs"]["assemblyReceipt"]["properties"]["schema"]["const"] == (
        contract.ASSEMBLY_SCHEMA
    )
    assert schema["$defs"]["productManifest"]["properties"]["schema"]["const"] == (
        contract.PRODUCT_SCHEMA
    )
    assert (
        schema["$defs"]["transactionReceipt"]["properties"]["schema"]["const"]
        == contract.TRANSACTION_SCHEMA
    )
    assert schema["$defs"]["walkaroundReceipt"] == {
        "$ref": "#/$defs/runnerWalkaroundReceipt"
    }
    assert "legacyWalkaroundReceipt" not in schema["$defs"]
    assert (
        schema["$defs"]["runnerWalkaroundReceipt"]["properties"]["schema"]["const"]
        == contract.WALKAROUND_SCHEMA
    )
    assert schema["$defs"]["releaseOutput"]["properties"]["schema"]["const"] == (
        contract.RELEASE_OUTPUT_SCHEMA
    )
    assert (
        schema["$defs"]["releaseOutput"]["properties"]["dmg"]["properties"]["path"][
            "pattern"
        ]
        == contract.RELEASE_DMG_PATTERN
    )


def test_versioned_json_schema_accepts_every_valid_contract_fixture(
    tmp_path: Path, macho_executable: Path
) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = contract.UnifiedProductValidator(schema)
    release_app = tmp_path / "ReleaseFixture.app"
    _app_fixture(release_app, macho_executable)
    release_dmg = _canonical_release_dmg_path(tmp_path, release_app)
    release_dmg.write_bytes(b"synthetic-dmg\n")
    _release_output_fixture(tmp_path, release_app, release_dmg)
    walkaround = _runner_walkaround_fixture(tmp_path)
    release_output = json.loads(
        (tmp_path / "release-output.json").read_text(encoding="utf-8")
    )
    fixtures = [
        _module_fixture(tmp_path / "terminal", macho_executable, "vc-terminal"),
        _module_fixture(tmp_path / "frame", macho_executable, "vc-frame"),
        _app_fixture(tmp_path / "Vibecrafted.app", macho_executable),
        _transaction_fixture(tmp_path / "transaction.json", macho_executable),
        release_output,
        walkaround,
    ]

    for fixture in fixtures:
        validator.validate(fixture)
        contract.validate_schema_document(fixture)


@pytest.mark.parametrize("module", ["vc-terminal", "vc-frame"])
def test_valid_module_binds_provenance_inventory_and_entrypoint(
    tmp_path: Path, macho_executable: Path, module: str
) -> None:
    root = tmp_path / module
    expected = _module_fixture(root, macho_executable, module)

    assert contract.verify_module(root) == expected


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing_entrypoint", contract.E_MISSING),
        ("absolute_path", contract.E_PATH),
        ("wrong_hash", contract.E_HASH),
        ("host_bound_path", contract.E_PATH),
        ("wrong_mode", contract.E_MODE),
        ("wrong_size", contract.E_SIZE),
        ("extra_file", contract.E_INVENTORY),
        ("external_dylib", contract.E_DEPENDENCY),
        ("wrong_arch", contract.E_PLATFORM),
        ("old_macos", contract.E_PLATFORM),
    ],
)
def test_module_negative_controls_fail_closed_with_stable_codes(
    tmp_path: Path,
    macho_executable: Path,
    mutation: str,
    expected_code: int,
) -> None:
    root = tmp_path / "module"
    manifest = _module_fixture(root, macho_executable)
    if mutation == "missing_entrypoint":
        manifest["entrypoints"] = {}
    elif mutation == "absolute_path":
        manifest["files"][0]["path"] = "/tmp/vc-terminal"
    elif mutation == "wrong_hash":
        manifest["files"][0]["sha256"] = "f" * 64
    elif mutation == "host_bound_path":
        _write_executable(
            root / "bin/vc-terminal",
            "#!/bin/sh\nexec /Volumes/vc-workspace/bin/vc-terminal\n",
        )
        manifest["files"][0] = _entry(
            root,
            "bin/vc-terminal",
            kind="executable",
        )
    elif mutation == "wrong_mode":
        manifest["files"][0]["mode"] = "0644"
    elif mutation == "wrong_size":
        manifest["files"][0]["size"] += 1
    elif mutation == "extra_file":
        (root / "undeclared.txt").write_text("extra\n", encoding="utf-8")
    elif mutation == "external_dylib":
        manifest["files"][0]["dylibs"] = ["/opt/homebrew/lib/libescape.dylib"]
    elif mutation == "wrong_arch":
        manifest["architecture"] = "x86_64"
    elif mutation == "old_macos":
        manifest["minimum_macos"] = "13.0"
    _write_json(root / "module-manifest.json", manifest)

    _assert_error(expected_code, lambda: contract.verify_module(root))


def test_host_path_scan_distinguishes_windows_examples_from_unix_paths(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    payload.write_bytes(b"splitroot('C:/Users/tester')")
    contract._reject_host_bound_paths(
        payload,
        relative="payload",
        kind="executable",
    )

    payload.write_bytes(b"panic at /Users/tester/src/main.rs")
    _assert_error(
        contract.E_PATH,
        lambda: contract._reject_host_bound_paths(
            payload,
            relative="payload",
            kind="executable",
        ),
    )


def test_host_path_scan_allows_only_known_libpython_documentation_paths(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "libpython3.12.dylib"
    relative = "Contents/Resources/runtime/python/lib/libpython3.12.dylib"
    payload.write_bytes(
        b"example /usr/local/lib/python2.5/site-packages "
        b"/usr/local/lib/python2.5/site-packages/bar "
        b"/usr/local/lib/python2.5/site-packages/foo"
    )
    contract._reject_host_bound_paths(payload, relative=relative, kind="dylib")

    payload.write_bytes(b"load /usr/local/lib/libescape.dylib")
    _assert_error(
        contract.E_PATH,
        lambda: contract._reject_host_bound_paths(
            payload,
            relative=relative,
            kind="dylib",
        ),
    )


@pytest.mark.parametrize(
    ("actual_architecture", "actual_minimum"),
    [("x86_64", "13.0"), ("arm64", "13.0")],
)
def test_module_measures_macho_platform_instead_of_trusting_manifest(
    tmp_path: Path,
    macho_executable: Path,
    actual_architecture: str,
    actual_minimum: str,
) -> None:
    root = tmp_path / "module"
    manifest = _module_fixture(root, macho_executable)
    _compile_macho(
        root / "bin/vc-terminal",
        architecture=actual_architecture,
        minimum_macos=actual_minimum,
    )
    manifest["files"][0] = _entry(root, "bin/vc-terminal", kind="executable")
    _write_json(root / "module-manifest.json", manifest)

    _assert_error(contract.E_PLATFORM, lambda: contract.verify_module(root))


def test_module_rejects_ambient_lc_rpath_even_when_basename_is_bundled(
    tmp_path: Path,
) -> None:
    root = tmp_path / "module"
    dylib = root / "lib/libfixture.dylib"
    _compile_macho(
        dylib,
        source="int fixture(void) { return 0; }\n",
        extra_args=(
            "-dynamiclib",
            "-Wl,-install_name,@rpath/libfixture.dylib",
        ),
    )
    executable = root / "bin/vc-terminal"
    _compile_macho(
        executable,
        source="extern int fixture(void); int main(void) { return fixture(); }\n",
        extra_args=(
            f"-L{dylib.parent}",
            "-lfixture",
            "-Wl,-rpath,/tmp",
        ),
    )
    manifest = {
        "schema": contract.MODULE_SCHEMA,
        "module": "vc-terminal",
        "version": "1.0.0",
        "git_sha": "1" * 40,
        "dirty": False,
        "architecture": "arm64",
        "minimum_macos": "14.0",
        "files": [
            _entry(root, "bin/vc-terminal", kind="executable"),
            _entry(root, "lib/libfixture.dylib", kind="dylib"),
        ],
        "entrypoints": {"terminal": "bin/vc-terminal"},
    }
    _write_json(root / "module-manifest.json", manifest)

    _assert_error(contract.E_DEPENDENCY, lambda: contract.verify_module(root))


def test_module_rejects_unused_absolute_lc_rpath(tmp_path: Path) -> None:
    root = tmp_path / "module"
    executable = root / "bin/vc-terminal"
    _compile_macho(executable, extra_args=("-Wl,-rpath,/tmp",))
    manifest = {
        "schema": contract.MODULE_SCHEMA,
        "module": "vc-terminal",
        "version": "1.0.0",
        "git_sha": "1" * 40,
        "dirty": False,
        "architecture": "arm64",
        "minimum_macos": "14.0",
        "files": [_entry(root, "bin/vc-terminal", kind="executable")],
        "entrypoints": {"terminal": "bin/vc-terminal"},
    }
    _write_json(root / "module-manifest.json", manifest)

    _assert_error(contract.E_DEPENDENCY, lambda: contract.verify_module(root))


def test_module_checks_every_declared_executable_as_a_closure_root(
    tmp_path: Path, macho_executable: Path
) -> None:
    root = tmp_path / "module"
    manifest = _module_fixture(root, macho_executable)
    ambient = tmp_path / "ambient/libescape.dylib"
    _compile_macho(
        ambient,
        source="int escape(void) { return 0; }\n",
        extra_args=(
            "-dynamiclib",
            "-Wl,-install_name,/private/tmp/libescape.dylib",
        ),
    )
    helper = root / "bin/ambient-helper"
    _compile_macho(
        helper,
        source="extern int escape(void); int main(void) { return escape(); }\n",
        extra_args=(f"-L{ambient.parent}", "-lescape"),
    )
    manifest["files"].append(_entry(root, "bin/ambient-helper", kind="executable"))
    _write_json(root / "module-manifest.json", manifest)

    _assert_error(contract.E_DEPENDENCY, lambda: contract.verify_module(root))


def test_module_resolves_loader_relative_rpath_to_exact_declared_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "module"
    dylib = root / "lib/libfixture.dylib"
    _compile_macho(
        dylib,
        source="int fixture(void) { return 0; }\n",
        extra_args=(
            "-dynamiclib",
            "-Wl,-install_name,@rpath/libfixture.dylib",
        ),
    )
    executable = root / "bin/vc-terminal"
    _compile_macho(
        executable,
        source="extern int fixture(void); int main(void) { return fixture(); }\n",
        extra_args=(
            f"-L{dylib.parent}",
            "-lfixture",
            "-Wl,-rpath,@loader_path/../lib",
        ),
    )
    manifest = {
        "schema": contract.MODULE_SCHEMA,
        "module": "vc-terminal",
        "version": "1.0.0",
        "git_sha": "1" * 40,
        "dirty": False,
        "architecture": "arm64",
        "minimum_macos": "14.0",
        "files": [
            _entry(root, "bin/vc-terminal", kind="executable"),
            _entry(root, "lib/libfixture.dylib", kind="dylib"),
        ],
        "entrypoints": {"terminal": "bin/vc-terminal"},
    }
    _write_json(root / "module-manifest.json", manifest)

    assert contract.verify_module(root) == manifest


def test_release_policy_can_require_clean_module_receipts(
    tmp_path: Path, macho_executable: Path
) -> None:
    root = tmp_path / "module"
    manifest = _module_fixture(root, macho_executable)
    manifest["dirty"] = True
    _write_json(root / "module-manifest.json", manifest)

    assert contract.verify_module(root) == manifest
    _assert_error(
        contract.E_PROOF,
        lambda: contract.verify_module(root, require_clean=True),
    )
    assert contract.main(["module", str(root), "--require-clean"]) == contract.E_PROOF


def test_valid_app_has_one_bundle_identity_and_three_bound_entrypoints(
    tmp_path: Path, macho_executable: Path
) -> None:
    app = tmp_path / "Vibecrafted.app"
    expected = _app_fixture(app, macho_executable)

    assert contract.verify_app(app) == expected
    outer = expected["outer_bundle_code"]
    assert outer["identity"] == contract.OUTER_BUNDLE_CODE_IDENTITY
    assert outer["code_identity"] == contract.MACHO_CODE_IDENTITY
    assert "sha256" not in outer and "size" not in outer
    assert outer["code_sha256"] == contract._macho_code_sha256(app / outer["path"])
    for entry in expected["files"]:
        if entry["kind"] in {"executable", "dylib"}:
            assert _sha256(app / entry["path"]) == entry["sha256"]
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_app_rejects_legacy_icon_resource(
    tmp_path: Path, macho_executable: Path
) -> None:
    app = tmp_path / "Vibecrafted.app"
    manifest = _app_fixture(app, macho_executable)
    legacy_icon = app / "Contents/Resources/icon1.icns"
    legacy_icon.write_bytes(b"legacy-icon")
    manifest["files"].append(
        _entry(app, "Contents/Resources/icon1.icns", kind="resource")
    )
    _write_app_manifest(app, manifest, sign=True)

    _assert_error(contract.E_BUNDLE, lambda: contract.verify_app(app))


def test_outer_macho_code_digest_rejects_substitution_after_resigning(
    tmp_path: Path, macho_executable: Path
) -> None:
    app = tmp_path / "Vibecrafted.app"
    manifest = _app_fixture(app, macho_executable)
    executable = app / manifest["outer_bundle_code"]["path"]
    replacement = tmp_path / "replacement"
    _compile_macho(replacement, source="int main(void) { return 73; }\n")
    assert (
        contract._macho_code_sha256(replacement)
        != manifest["outer_bundle_code"]["code_sha256"]
    )

    shutil.copy2(replacement, executable)
    _codesign_app(app)

    _assert_error(contract.E_HASH, lambda: contract.verify_app(app))


@pytest.mark.parametrize(
    "mutation",
    [
        "login_shell",
        "ambient_program",
        "applications_path",
        "direct_alias_session",
        "unknown_environment",
        "undeclared_config",
    ],
)
def test_app_launch_contract_rejects_noncanonical_product_entry(
    tmp_path: Path,
    macho_executable: Path,
    mutation: str,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    manifest = _app_fixture(app, macho_executable)
    launch = manifest["launch_contract"]
    if mutation == "login_shell":
        launch["shell"]["argv"] = ["--login"]
    elif mutation == "ambient_program":
        launch["program"] = "vc-terminal"
    elif mutation == "applications_path":
        launch["program"] = "/Applications/vc-terminal.app/Contents/MacOS/vc-terminal"
    elif mutation == "direct_alias_session":
        launch["shell"]["argv"] = ["attach", "--create", "Start here"]
    elif mutation == "unknown_environment":
        launch["environment"]["inject_bundle_paths"]["VC_FRAME_BIN"] = (
            "Contents/Helpers/vc-frame"
        )
    else:
        launch["config_path"] = "Contents/Resources/terminal/other.toml"
    _write_app_manifest(app, manifest, sign=True)

    _assert_error(
        contract.E_ENTRYPOINT
        if mutation != "unknown_environment"
        else contract.E_SCHEMA,
        lambda: contract.verify_app(app),
    )


def test_launch_environment_is_fresh_closed_and_resolves_writable_runtime_home(
    tmp_path: Path,
    macho_executable: Path,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    _app_fixture(app, macho_executable)
    runtime_home = tmp_path / "data/runtime"
    runtime_home.parent.mkdir()
    host = {
        "HOME": str(tmp_path),
        "USER": "operator",
        "LANG": "pl_PL.UTF-8",
        "PATH": "/attacker/bin",
        "VIBECRAFTED_RUNTIME_HOME": str(runtime_home),
        "VIBECRAFTED_ROOT": "/attacker/root",
        "VIBECRAFTED_TOOLS_HOME": "/attacker/tools",
        "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
        "VC_FRAME_BIN": "/attacker/vc-frame",
    }

    child = contract.build_launch_environment(app, host_environment=host)

    assert child == {
        "HOME": str(tmp_path),
        "USER": "operator",
        "LANG": "pl_PL.UTF-8",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "VIBECRAFTED_RUNTIME_HOME": str(runtime_home),
        "VIBECRAFTED_APP_ROOT": str(app.resolve()),
        "VIBECRAFTED_VC_FRAME_BIN": str(app.resolve() / "Contents/Helpers/vc-frame"),
    }


@pytest.mark.parametrize("runtime_home", ["relative/runtime", "APP_DESCENDANT"])
def test_launch_environment_rejects_relative_or_bundle_runtime_home(
    tmp_path: Path,
    macho_executable: Path,
    runtime_home: str,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    _app_fixture(app, macho_executable)
    value = (
        str(app / "Contents/Resources/runtime-data")
        if runtime_home == "APP_DESCENDANT"
        else runtime_home
    )
    _assert_error(
        contract.E_PATH,
        lambda: contract.build_launch_environment(
            app,
            host_environment={"HOME": str(tmp_path), "VIBECRAFTED_RUNTIME_HOME": value},
        ),
    )


def test_launch_environment_rejects_raw_app_symlink_and_uncreatable_exact_root(
    tmp_path: Path,
    macho_executable: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "real/Vibecrafted.app"
    _app_fixture(app, macho_executable)
    alias = tmp_path / "Alias.app"
    alias.symlink_to(app)

    _assert_error(
        contract.E_PATH,
        lambda: contract.build_launch_environment(
            alias, host_environment={"HOME": str(tmp_path)}
        ),
    )

    for malformed in (str(tmp_path / "bad\x00root"), str(tmp_path / ("x" * 4096))):
        _assert_error(
            contract.E_PATH,
            lambda malformed=malformed: contract.build_launch_environment(
                app,
                host_environment={
                    "HOME": str(tmp_path),
                    "VIBECRAFTED_RUNTIME_HOME": malformed,
                },
            ),
        )

    non_directory = tmp_path / "runtime-home"
    non_directory.write_text("not a directory\n", encoding="utf-8")
    _assert_error(
        contract.E_PATH,
        lambda: contract.build_launch_environment(
            app,
            host_environment={
                "HOME": str(tmp_path),
                "VIBECRAFTED_RUNTIME_HOME": str(non_directory),
            },
        ),
    )

    denied = tmp_path / "permission-denied-runtime"
    original_mkstemp = tempfile.mkstemp

    def deny_exact_root(*args: Any, **kwargs: Any) -> tuple[int, str]:
        if kwargs.get("dir") == denied:
            raise PermissionError("fixture exact root is not writable")
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(contract.tempfile, "mkstemp", deny_exact_root)
    _assert_error(
        contract.E_PATH,
        lambda: contract.build_launch_environment(
            app,
            host_environment={
                "HOME": str(tmp_path),
                "VIBECRAFTED_RUNTIME_HOME": str(denied),
            },
        ),
    )
    assert denied.is_dir()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("wrong_magic", contract.E_PLATFORM),
        ("too_many_commands", contract.E_SIZE),
        ("oversized_command_table", contract.E_SIZE),
        ("truncated_commands", contract.E_PROOF),
        ("signature_inside_commands", contract.E_PROOF),
        ("zero_segment_inside_linkedit", contract.E_PROOF),
    ],
)
def test_macho_code_digest_fails_closed_on_invalid_container_shapes(
    tmp_path: Path,
    macho_executable: Path,
    mutation: str,
    expected_code: int,
) -> None:
    candidate = tmp_path / mutation
    data = bytearray(macho_executable.read_bytes())
    if mutation == "wrong_magic":
        struct.pack_into("<I", data, 0, 0xCFFAEDFE)
    elif mutation == "too_many_commands":
        struct.pack_into("<I", data, 16, contract._MACHO_MAX_COMMANDS + 1)
    elif mutation == "oversized_command_table":
        struct.pack_into("<I", data, 20, contract._MACHO_MAX_COMMAND_BYTES + 1)
    elif mutation == "truncated_commands":
        data = data[:40]
    else:
        ncmds = struct.unpack_from("<I", data, 16)[0]
        offset = 32
        segments: list[tuple[int, str]] = []
        signature_command = -1
        for _ in range(ncmds):
            command, command_size = struct.unpack_from("<II", data, offset)
            if command == contract._LC_SEGMENT_64:
                name = data[offset + 8 : offset + 24].split(b"\0", 1)[0].decode()
                segments.append((offset, name))
            elif command == contract._LC_CODE_SIGNATURE:
                signature_command = offset
            offset += command_size
        linkedit_command = next(
            command for command, name in segments if name == "__LINKEDIT"
        )
        linkedit_fileoff = struct.unpack_from("<Q", data, linkedit_command + 40)[0]
        if mutation == "zero_segment_inside_linkedit":
            zero_command = next(
                command
                for command, name in segments
                if name != "__LINKEDIT"
                and struct.unpack_from("<Q", data, command + 48)[0] == 0
            )
            struct.pack_into("<Q", data, zero_command + 40, linkedit_fileoff)
        else:
            assert signature_command >= 0
            commands_end = 32 + struct.unpack_from("<I", data, 20)[0]
            assert commands_end > 32
            for command, name in segments:
                if name != "__LINKEDIT":
                    struct.pack_into("<Q", data, command + 48, 0)
            struct.pack_into("<Q", data, linkedit_command + 40, 32)
            struct.pack_into("<Q", data, linkedit_command + 48, len(data) - 32)
            struct.pack_into("<I", data, signature_command + 8, 32)
            struct.pack_into("<I", data, signature_command + 12, len(data) - 32)
    candidate.write_bytes(data)

    _assert_error(expected_code, lambda: contract._macho_code_sha256(candidate))


def test_app_rejects_byte_identical_macho_identity_transformation(
    tmp_path: Path, macho_executable: Path
) -> None:
    app = tmp_path / "Vibecrafted.app"
    manifest = _app_fixture(app, macho_executable)
    binding = manifest["modules"][0]
    receipt_path = app / binding["manifest_path"]
    assembly_path = app / binding["assembly_receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assembly = json.loads(assembly_path.read_text(encoding="utf-8"))
    mapping = assembly["files"][0]
    product_entry = next(
        entry for entry in manifest["files"] if entry["path"] == mapping["product_path"]
    )
    receipt["files"][0] = {
        **copy.deepcopy(product_entry),
        "path": mapping["module_path"],
    }
    _write_json(receipt_path, receipt)
    binding["manifest_sha256"] = _sha256(receipt_path)
    mapping["unsigned_sha256"] = product_entry["sha256"]
    mapping["product_sha256"] = product_entry["sha256"]
    mapping["transformation"] = "identity"
    assembly["module_manifest_sha256"] = binding["manifest_sha256"]
    _write_json(assembly_path, assembly)
    binding["assembly_receipt_sha256"] = _sha256(assembly_path)
    for relative in (binding["manifest_path"], binding["assembly_receipt_path"]):
        entry = next(item for item in manifest["files"] if item["path"] == relative)
        entry.update(_entry(app, relative, kind="config"))
    _write_app_manifest(app, manifest, sign=True)

    _assert_error(contract.E_PROOF, lambda: contract.verify_app(app))


def test_app_binds_unsigned_module_bytes_to_real_signed_product_bytes(
    tmp_path: Path, macho_executable: Path
) -> None:
    app = tmp_path / "Vibecrafted.app"
    manifest = _app_fixture(app, macho_executable)
    binding = manifest["modules"][0]
    receipt = json.loads((app / binding["manifest_path"]).read_text(encoding="utf-8"))
    assembly_path = app / binding["assembly_receipt_path"]
    assembly = json.loads(assembly_path.read_text(encoding="utf-8"))
    transformation = assembly["files"][0]
    product_entry = next(
        entry
        for entry in manifest["files"]
        if entry["path"] == transformation["product_path"]
    )

    assert transformation["unsigned_sha256"] == receipt["files"][0]["sha256"]
    assert transformation["product_sha256"] == product_entry["sha256"]
    assert transformation["unsigned_sha256"] != transformation["product_sha256"]
    subprocess.run(
        ["codesign", "--verify", "--strict", str(app / transformation["product_path"])],
        check=True,
        capture_output=True,
        text=True,
    )
    assert contract.verify_app(app) == manifest

    assembly["files"][0]["transformation"] = "identity"
    _write_json(assembly_path, assembly)
    binding["assembly_receipt_sha256"] = _sha256(assembly_path)
    assembly_entry = next(
        entry
        for entry in manifest["files"]
        if entry["path"] == binding["assembly_receipt_path"]
    )
    assembly_entry.update(_entry(app, binding["assembly_receipt_path"], kind="config"))
    _write_json(app / "Contents/Resources/product-manifest.json", manifest)
    _assert_error(contract.E_PROOF, lambda: contract.verify_app(app))


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("wrong_bundle_id", contract.E_BUNDLE),
        ("wrong_executable", contract.E_BUNDLE),
        ("missing_module", contract.E_SCHEMA),
        ("undeclared_file", contract.E_INVENTORY),
        ("nested_app", contract.E_BUNDLE),
    ],
)
def test_app_negative_controls_reject_competing_or_unbound_product_shape(
    tmp_path: Path,
    macho_executable: Path,
    mutation: str,
    expected_code: int,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    manifest = _app_fixture(app, macho_executable)
    if mutation == "wrong_bundle_id":
        manifest["bundle_id"] = "io.example.vibecrafted"
    elif mutation == "wrong_executable":
        manifest["bundle_executable"] = "vibecrafted-launch"
    elif mutation == "missing_module":
        manifest["modules"] = manifest["modules"][:1]
    elif mutation == "undeclared_file":
        (app / "Contents/Resources/ambient-path.txt").write_text(
            "/Applications/Vibecrafted.app\n", encoding="utf-8"
        )
    elif mutation == "nested_app":
        nested = app / "Contents/Resources/Second.app/Contents"
        nested.mkdir(parents=True)
        (nested / ".keep").write_text("nested app\n", encoding="utf-8")
        manifest["files"].append(
            _entry(
                app,
                "Contents/Resources/Second.app/Contents/.keep",
                kind="resource",
            )
        )
    _write_json(app / "Contents/Resources/product-manifest.json", manifest)

    _assert_error(expected_code, lambda: contract.verify_app(app))


def test_app_binds_embedded_module_receipt_bytes_and_copied_inventory(
    tmp_path: Path, macho_executable: Path
) -> None:
    app = tmp_path / "Vibecrafted.app"
    manifest = _app_fixture(app, macho_executable)
    binding = manifest["modules"][0]
    receipt_path = app / binding["manifest_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["git_sha"] = "e" * 40
    _write_json(receipt_path, receipt)
    binding["manifest_sha256"] = _sha256(receipt_path)
    receipt_entry = next(
        item for item in manifest["files"] if item["path"] == binding["manifest_path"]
    )
    receipt_entry.update(_entry(app, binding["manifest_path"], kind="config"))
    _write_json(app / "Contents/Resources/product-manifest.json", manifest)

    _assert_error(contract.E_PROOF, lambda: contract.verify_app(app))


def test_app_binds_manifest_version_to_dictionary_info_plist(
    tmp_path: Path, macho_executable: Path
) -> None:
    app = tmp_path / "Vibecrafted.app"
    manifest = _app_fixture(app, macho_executable)
    manifest["version"] = "9.9.9"
    _write_app_manifest(app, manifest, sign=True)
    _assert_error(contract.E_BUNDLE, lambda: contract.verify_app(app))

    plist_path = app / "Contents/Info.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump(["not", "a", "dictionary"], handle)
    plist_entry = next(
        item for item in manifest["files"] if item["path"] == "Contents/Info.plist"
    )
    plist_entry.update(_entry(app, "Contents/Info.plist", kind="config"))
    manifest["outer_bundle_code"]["info_plist_sha256"] = _sha256(plist_path)
    manifest["version"] = "1.0.0"
    _write_app_manifest(app, manifest, sign=False)
    _assert_error(contract.E_BUNDLE, lambda: contract.verify_app(app))


def test_app_allows_install_location_copy_in_declared_resource(
    tmp_path: Path, macho_executable: Path
) -> None:
    app = tmp_path / "Vibecrafted.app"
    manifest = _app_fixture(app, macho_executable)
    resource = app / "Contents/Resources/install-help.txt"
    resource.write_text("Drag to /Applications/Vibecrafted.app\n", encoding="utf-8")
    manifest["files"].append(
        _entry(app, "Contents/Resources/install-help.txt", kind="resource")
    )
    _write_app_manifest(app, manifest, sign=True)

    assert contract.verify_app(app) == manifest


def test_schema_and_runtime_reject_the_same_module_and_binding_shapes(
    tmp_path: Path, macho_executable: Path
) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = contract.UnifiedProductValidator(schema)

    module_root = tmp_path / "module"
    module = _module_fixture(module_root, macho_executable)
    module["entrypoints"] = {"frame": "bin/vc-terminal"}
    _write_json(module_root / "module-manifest.json", module)
    assert list(validator.iter_errors(module))
    _assert_error(contract.E_MISSING, lambda: contract.verify_module(module_root))

    app = tmp_path / "Vibecrafted.app"
    product = _app_fixture(app, macho_executable)
    product["modules"][1] = copy.deepcopy(product["modules"][0])
    _write_json(app / "Contents/Resources/product-manifest.json", product)
    assert list(validator.iter_errors(product))
    _assert_error(contract.E_SCHEMA, lambda: contract.verify_app(app))

    duplicate_module_root = tmp_path / "duplicate-module"
    duplicate_module = _module_fixture(duplicate_module_root, macho_executable)
    duplicate_file = copy.deepcopy(duplicate_module["files"][0])
    duplicate_file["sha256"] = "0" * 64
    duplicate_module["files"].append(duplicate_file)
    _write_json(duplicate_module_root / "module-manifest.json", duplicate_module)
    assert list(validator.iter_errors(duplicate_module))
    _assert_error(
        contract.E_INVENTORY,
        lambda: contract.verify_module(duplicate_module_root),
    )

    duplicate_app = tmp_path / "Duplicate.app"
    duplicate_product = _app_fixture(duplicate_app, macho_executable)
    binding = duplicate_product["modules"][0]
    assembly_path = duplicate_app / binding["assembly_receipt_path"]
    assembly = json.loads(assembly_path.read_text(encoding="utf-8"))
    duplicate_mapping = copy.deepcopy(assembly["files"][0])
    duplicate_mapping["unsigned_sha256"] = "0" * 64
    assembly["files"].append(duplicate_mapping)
    assert list(validator.iter_errors(assembly))
    _write_json(assembly_path, assembly)
    binding["assembly_receipt_sha256"] = _sha256(assembly_path)
    assembly_entry = next(
        entry
        for entry in duplicate_product["files"]
        if entry["path"] == binding["assembly_receipt_path"]
    )
    assembly_entry.update(
        _entry(duplicate_app, binding["assembly_receipt_path"], kind="config")
    )
    _write_json(
        duplicate_app / "Contents/Resources/product-manifest.json",
        duplicate_product,
    )
    _assert_error(contract.E_INVENTORY, lambda: contract.verify_app(duplicate_app))

    noncanonical = _module_fixture(tmp_path / "noncanonical-module", macho_executable)
    noncanonical["entrypoints"] = {"terminal": "bin//vc-terminal"}
    assert list(validator.iter_errors(noncanonical))
    _assert_error(
        contract.E_SCHEMA, lambda: contract.validate_schema_document(noncanonical)
    )


def test_transaction_receipt_binds_app_and_runtime_as_one_active_pair(
    tmp_path: Path,
    macho_executable: Path,
) -> None:
    receipt = tmp_path / "transaction.json"
    expected = _transaction_fixture(receipt, macho_executable)

    assert contract.verify_transaction(receipt) == expected

    split = copy.deepcopy(expected)
    split["active"] = {"state": "absent"}
    _write_json(receipt, split)
    _assert_error(
        contract.E_TRANSACTION,
        lambda: contract.verify_transaction(receipt),
    )

    rolled_back = copy.deepcopy(expected)
    rolled_back["outcome"] = "rolled_back"
    rolled_back["active"] = copy.deepcopy(rolled_back["previous"])
    _write_json(receipt, rolled_back)
    assert contract.verify_transaction(receipt) == rolled_back

    product_manifest = tmp_path / expected["new"]["app"]["manifest_path"]
    product_manifest.write_text("{}\n", encoding="utf-8")
    _write_json(receipt, expected)
    _assert_error(contract.E_HASH, lambda: contract.verify_transaction(receipt))


@pytest.mark.parametrize("artifact", ["app", "runtime"])
def test_transaction_rejects_minimal_manifest_lookalikes(
    tmp_path: Path, macho_executable: Path, artifact: str
) -> None:
    receipt = tmp_path / "transaction.json"
    payload = _transaction_fixture(receipt, macho_executable)
    identity = payload["new"][artifact]
    manifest = tmp_path / identity["manifest_path"]
    if artifact == "app":
        lookalike = {
            "schema": contract.PRODUCT_SCHEMA,
            "version": identity["version"],
            "git_sha": identity["source_revision"],
        }
        digest_field = "product_manifest_sha256"
    else:
        lookalike = {
            "schema": contract.RUNTIME_GENERATION_SCHEMA,
            "version": identity["version"],
            "source_revision": identity["source_revision"],
            "source_payload": _runtime_source_payload(),
        }
        digest_field = "runtime_manifest_sha256"
    _write_json(manifest, lookalike)
    identity[digest_field] = _sha256(manifest)
    payload["active"] = copy.deepcopy(payload["new"])
    _write_json(receipt, payload)

    _assert_error(contract.E_TRANSACTION, lambda: contract.verify_transaction(receipt))


def test_transaction_rejects_runtime_manifest_without_verifier_hash(
    tmp_path: Path, macho_executable: Path
) -> None:
    receipt = tmp_path / "transaction.json"
    payload = _transaction_fixture(receipt, macho_executable)
    identity = payload["new"]["runtime"]
    manifest_path = tmp_path / identity["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["hashes"].pop("vibecrafted-core/vibecrafted_core/product_contract.py")
    _write_json(manifest_path, manifest)
    identity["runtime_manifest_sha256"] = _sha256(manifest_path)
    payload["active"] = copy.deepcopy(payload["new"])
    _write_json(receipt, payload)

    _assert_error(contract.E_TRANSACTION, lambda: contract.verify_transaction(receipt))


def test_transaction_rejects_exact_legacy_four_hash_runtime_manifest(
    tmp_path: Path, macho_executable: Path
) -> None:
    receipt = tmp_path / "transaction.json"
    payload = _transaction_fixture(receipt, macho_executable)
    identity = payload["new"]["runtime"]
    manifest_path = tmp_path / identity["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["hashes"] = {
        "VERSION": manifest["hashes"]["VERSION"],
        "scripts/vibecrafted": manifest["hashes"]["scripts/vibecrafted"],
        "runtime/generated/vc-frame/config.kdl": manifest["hashes"][
            "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
        ],
        "vibecrafted-core/vibecrafted_core/deck/vibecrafted": manifest["hashes"][
            contract.RUNTIME_GENERATION_ENTRYPOINT
        ],
    }
    _write_json(manifest_path, manifest)
    identity["runtime_manifest_sha256"] = _sha256(manifest_path)
    payload["active"] = copy.deepcopy(payload["new"])
    _write_json(receipt, payload)

    _assert_error(contract.E_TRANSACTION, lambda: contract.verify_transaction(receipt))


@pytest.mark.parametrize("artifact", ["app", "runtime"])
def test_transaction_identity_validates_the_single_captured_manifest_snapshot(
    tmp_path: Path,
    macho_executable: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact: str,
) -> None:
    """A path swap after capture must not let unbound bytes pass validation."""
    receipt = tmp_path / "transaction.json"
    payload = _transaction_fixture(receipt, macho_executable)
    identity = payload["new"][artifact]
    manifest_path = tmp_path / identity["manifest_path"]
    valid_bytes = manifest_path.read_bytes()

    if artifact == "app":
        invalid_manifest = {
            "schema": contract.PRODUCT_SCHEMA,
            "version": identity["version"],
            "git_sha": identity["source_revision"],
        }
        digest_field = "product_manifest_sha256"
    else:
        invalid_manifest = {
            "schema": contract.RUNTIME_GENERATION_SCHEMA,
            "version": identity["version"],
            "source_fingerprint": "7" * 64,
            "owner_repo": "vetcoders/vibecrafted",
            "source_revision": identity["source_revision"],
            "source_payload": _runtime_source_payload(),
            "entrypoint": "vibecrafted-core/vibecrafted_core/deck/vibecrafted",
            "hashes": {
                relative: f"{index:x}" * 64
                for index, relative in enumerate(
                    (
                        "VERSION",
                        "runtime/generated/vc-frame/config.kdl",
                        "scripts/vibecrafted",
                        "vibecrafted-core/vibecrafted_core/deck/vibecrafted",
                    ),
                    start=1,
                )
            },
        }
        digest_field = "runtime_manifest_sha256"

    _write_json(manifest_path, invalid_manifest)
    identity[digest_field] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    replacement = manifest_path.with_name(f"{manifest_path.name}.valid")
    replacement.write_bytes(valid_bytes)
    real_capture = contract._capture_proof_artifact
    captures = 0

    def capture_then_swap(path: Path, *, context: str):
        nonlocal captures
        captured = real_capture(path, context=context)
        if path == manifest_path:
            captures += 1
            replacement.replace(manifest_path)
        return captured

    def forbid_second_open(path: Path) -> str:
        if path == manifest_path:
            pytest.fail("manifest identity reopened its mutable path after capture")
        return _sha256(path)

    monkeypatch.setattr(contract, "_capture_proof_artifact", capture_then_swap)
    monkeypatch.setattr(contract, "_sha256", forbid_second_open)

    _assert_error(
        contract.E_TRANSACTION,
        lambda: contract._validate_identity(
            identity,
            field=f"new.{artifact}",
            receipt_root=tmp_path,
            artifact="product" if artifact == "app" else "runtime",
        ),
    )
    assert captures == 1
    assert manifest_path.read_bytes() == valid_bytes


def test_transaction_product_referent_runs_semantic_launch_binding(
    tmp_path: Path, macho_executable: Path
) -> None:
    receipt = tmp_path / "transaction.json"
    payload = _transaction_fixture(receipt, macho_executable)
    identity = payload["new"]["app"]
    manifest_path = tmp_path / identity["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["launch_contract"]["config_path"] = (
        "Contents/Resources/terminal/not-declared.toml"
    )
    _write_json(manifest_path, manifest)
    identity["product_manifest_sha256"] = _sha256(manifest_path)
    payload["active"] = copy.deepcopy(payload["new"])
    _write_json(receipt, payload)

    _assert_error(contract.E_TRANSACTION, lambda: contract.verify_transaction(receipt))


@pytest.mark.parametrize("artifact", ["app", "runtime"])
def test_transaction_schema_and_runtime_reject_relocated_canonical_referents(
    tmp_path: Path, macho_executable: Path, artifact: str
) -> None:
    receipt = tmp_path / "transaction.json"
    payload = _transaction_fixture(receipt, macho_executable)
    payload["new"][artifact]["manifest_path"] = f"relocated/{artifact}.json"
    payload["active"] = copy.deepcopy(payload["new"])

    _assert_error(contract.E_SCHEMA, lambda: contract.validate_schema_document(payload))

    _write_json(receipt, payload)
    _assert_error(contract.E_TRANSACTION, lambda: contract.verify_transaction(receipt))


def test_walkaround_receipt_cannot_bypass_missing_semantic_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / "release-output.json"
    signature = tmp_path / "release-output.json.sig"
    release.write_text("{}\n", encoding="utf-8")
    signature.write_bytes(b"fixture")
    payload = _runner_walkaround_fixture(tmp_path)
    monkeypatch.setattr(
        contract,
        "_verify_release_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            contract.ProductContractError(
                contract.E_PROOF, "walk-around scenario provider is missing: start_here"
            )
        ),
    )
    _assert_error(
        contract.E_PROOF,
        lambda: contract.verify_walkaround(tmp_path / "walkaround.json"),
    )

    payload["observations"]["probes"]["start_here"]["command"] = ["fabricated"]
    _write_json(tmp_path / "walkaround.json", payload)
    _assert_error(
        contract.E_SCHEMA,
        lambda: contract.verify_walkaround(tmp_path / "walkaround.json"),
    )


def test_legacy_walkaround_mount_selector_is_rejected_by_schema(
    tmp_path: Path,
) -> None:
    legacy = {
        "schema": contract.WALKAROUND_SCHEMA,
        "mount_path": "/tmp/attacker-selected",
        "release_output": {},
        "release_signature": {},
        "proofs": {},
        "trusted_runner": {},
        "delivery_seal": {},
    }
    _write_json(tmp_path / "walkaround.json", legacy)

    _assert_error(
        contract.E_SCHEMA,
        lambda: contract.verify_walkaround(tmp_path / "walkaround.json"),
    )


def test_packaged_release_policy_pins_the_existing_public_key() -> None:
    policy = contract._release_policy()
    public_key = contract._trusted_runner_public_key()

    assert public_key.name == "vibecrafted-signing-v1.pub"
    assert public_key.is_file()
    assert contract._public_key_spki_sha256(public_key) == (
        "521ed59d3c446c540afe1557c2dbc39c9c190775f99896b2b65206c32814b25b"
    )
    assert policy["public_key_spki_sha256"] == contract.RELEASE_KEY_SPKI_SHA256
    assert policy["team_id"] == "MW223P3NPX"
    assert policy["hardened_runtime"] is True
    assert policy["entitlements"] == {}


def test_signed_release_output_binds_live_artifacts_and_signer_policy(
    tmp_path: Path,
    macho_executable: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    _app_fixture(app, macho_executable)
    dmg = _canonical_release_dmg_path(tmp_path, app)
    dmg.write_bytes(b"synthetic-dmg\n")
    receipt, signature, payload, signer = _release_output_fixture(tmp_path, app, dmg)
    monkeypatch.setattr(contract, "_verify_release_signature", _captured_signature_pair)
    monkeypatch.setattr(contract, "_codesign_release_evidence", lambda _: signer)
    _patch_release_mount(monkeypatch, app)

    assert contract.verify_release_output(receipt, signature) == payload


def test_walkaround_registry_is_typed_and_only_platform_tools_carry_argv() -> None:
    registry = contract._probe_registry()
    commands = contract._canonical_runner_commands()

    assert len(registry) == 15
    assert {spec.name for spec in registry} == contract._WALKAROUND_CHECKS
    assert {spec.executor for spec in registry} == {
        "argv",
        "builtin",
        "scenario",
        "pipeline_gate",
    }
    assert len(commands) == 6
    assert all(spec.argv is None for spec in registry if spec.executor != "argv")
    assert all(
        command[0] in {"codesign", "xcrun", "spctl"} for command in commands.values()
    )
    assert commands["dmg_gatekeeper"] == [
        "spctl",
        "--assess",
        "--type",
        "open",
        "--context",
        "context:primary-signature",
        "--verbose=4",
        "{DMG}",
    ]


@pytest.mark.parametrize(
    ("name", "template"),
    [
        ("app_codesign", ["codesign", "--verify", "--strict", "--verbose=2", "{APP}"]),
        ("dmg_codesign", ["codesign", "--verify", "--strict", "--verbose=2", "{DMG}"]),
        ("app_notarization", ["xcrun", "stapler", "validate", "{APP}"]),
        ("dmg_notarization", ["xcrun", "stapler", "validate", "{DMG}"]),
        (
            "app_gatekeeper",
            ["spctl", "--assess", "--type", "execute", "--verbose=4", "{APP}"],
        ),
        (
            "dmg_gatekeeper",
            [
                "spctl",
                "--assess",
                "--type",
                "open",
                "--context",
                "context:primary-signature",
                "--verbose=4",
                "{DMG}",
            ],
        ),
    ],
)
def test_each_live_platform_probe_uses_exact_argv_and_fails_on_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    template: list[str],
) -> None:
    app = tmp_path / "Vibecrafted.app"
    dmg = tmp_path / "Vibecrafted.dmg"
    calls: list[list[str]] = []
    expected = [
        item.replace("{APP}", str(app)).replace("{DMG}", str(dmg)) for item in template
    ]

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            1 if command == expected else 0,
            b"",
            f"{name} rejected fixture".encode() if command == expected else b"",
        )

    monkeypatch.setattr(contract, "_required_tool", lambda name, **_: name)
    monkeypatch.setattr(contract.subprocess, "run", fake_run)

    _assert_error(
        contract.E_PROOF,
        lambda: contract._run_live_release_checks(app, dmg),
    )
    assert expected in calls


def test_walkaround_runner_creates_and_reverifies_output_without_unsigned_mount_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release-output.json"
    signature = tmp_path / "release-output.json.sig"
    output = tmp_path / "walkaround.json"
    release.write_text("{}\n", encoding="utf-8")
    signature.write_bytes(b"fixture-signature")
    observations = _runner_walkaround_fixture(tmp_path)["observations"]["probes"]
    output.unlink()
    release_capture = contract._CapturedProofArtifact(
        release.absolute(),
        release.read_bytes(),
        _sha256(release),
        release.stat().st_size,
    )
    signature_capture = contract._CapturedProofArtifact(
        signature.absolute(),
        signature.read_bytes(),
        _sha256(signature),
        signature.stat().st_size,
    )
    monkeypatch.setattr(
        contract,
        "_verify_release_output",
        lambda *_args, **_kwargs: (
            {"schema": contract.RELEASE_OUTPUT_SCHEMA},
            observations,
            (release_capture, signature_capture),
        ),
    )

    payload = contract.produce_walkaround(release, signature, output)

    assert output.is_file()
    assert "mount_path" not in payload
    assert set(payload) == {
        "schema",
        "release_output",
        "release_signature",
        "observations",
    }
    contract.validate_schema_document(payload)
    monkeypatch.setattr(
        contract,
        "_verify_release_output",
        lambda *_args, **_kwargs: (
            {},
            observations,
            (release_capture, signature_capture),
        ),
    )
    assert contract.verify_walkaround(output) == payload


def test_walkaround_missing_scenario_provider_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(contract, "_run_live_release_checks", lambda *_: {})

    @contextmanager
    def missing_providers(*_args: object):
        yield {}

    monkeypatch.setattr(contract, "_walkaround_providers", missing_providers)

    _assert_error(
        contract.E_PROOF,
        lambda: contract._run_walkaround_probes(
            tmp_path / "Vibecrafted.app", tmp_path / "Vibecrafted.dmg"
        ),
    )


def test_walkaround_production_registry_covers_all_late_stage_probes() -> None:
    providers = contract._walkaround_provider_registry(object())  # type: ignore[arg-type]

    assert set(providers) == {
        spec.name
        for spec in contract._probe_registry()
        if spec.executor in {"scenario", "pipeline_gate"}
    }


def test_walkaround_sanitized_launch_requires_public_vc_git() -> None:
    sanitized = next(
        spec for spec in contract._probe_registry() if spec.name == "sanitized_launch"
    )

    assert sanitized.assertions == (
        "closed_environment",
        "launch_succeeds",
        "vc_git_reachable",
    )


def test_walkaround_session_socket_stays_below_darwin_sun_path_limit() -> None:
    representative = (
        contract._WALKAROUND_TEMP_PARENT / "vc-wa-xxxxxxxx" / "tmp" / "s.sock"
    )

    assert contract._WALKAROUND_TEMP_PARENT == Path("/tmp")
    assert len(os.fsencode(representative)) < 104


def test_walkaround_identity_accepts_only_canonical_parent_aliases(
    tmp_path: Path,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    runtime_root = tmp_path / "runtime/releases/4.1.0+g12345678"
    app.mkdir()
    runtime_root.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)

    active = {
        "app_root": str(alias / app.name),
        "runtime_root": str(alias / runtime_root.relative_to(tmp_path)),
        "schema": "vibecrafted.active-runtime.v1",
        "version": "4.1.0+g12345678",
    }

    assert contract._active_runtime_identity_matches(
        active,
        app=app,
        runtime_root=runtime_root,
        version="4.1.0+g12345678",
    )

    assert not contract._active_runtime_identity_matches(
        {**active, "app_root": str(tmp_path / "Different.app")},
        app=app,
        runtime_root=runtime_root,
        version="4.1.0+g12345678",
    )
    assert not contract._active_runtime_identity_matches(
        {**active, "unexpected": "field"},
        app=app,
        runtime_root=runtime_root,
        version="4.1.0+g12345678",
    )
    assert not contract._active_runtime_identity_matches(
        active,
        app=app,
        runtime_root=runtime_root,
        version="4.1.0+gdifferent",
    )


def test_walkaround_scenario_provider_normalizes_only_contract_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = contract.ProbeSpec(
        name="scenario",
        executor="scenario",
        owner_stage="W2",
        operation_id="scenario.test.v1",
        assertions=("launch_succeeds",),
    )
    monkeypatch.setattr(contract, "_run_live_release_checks", lambda *_: {})
    monkeypatch.setattr(contract, "_probe_registry", lambda: (spec,))

    def operational_failure(*_args: object) -> dict[str, str]:
        raise RuntimeError("scenario unavailable")

    @contextmanager
    def operational_providers(*_args: object):
        yield {"scenario": operational_failure}

    monkeypatch.setattr(contract, "_walkaround_providers", operational_providers)
    _assert_error(
        contract.E_PROOF,
        lambda: contract._run_walkaround_probes(
            tmp_path / "Vibecrafted.app", tmp_path / "Vibecrafted.dmg"
        ),
    )

    def programming_failure(*_args: object) -> dict[str, str]:
        raise AssertionError("provider invariant")

    @contextmanager
    def programming_providers(*_args: object):
        yield {"scenario": programming_failure}

    monkeypatch.setattr(contract, "_walkaround_providers", programming_providers)
    with pytest.raises(AssertionError, match="provider invariant"):
        contract._run_walkaround_probes(
            tmp_path / "Vibecrafted.app", tmp_path / "Vibecrafted.dmg"
        )


@pytest.mark.parametrize(
    ("mutation", "schema_code", "runtime_code"),
    [
        ("signature_algorithm", contract.E_SCHEMA, contract.E_SCHEMA),
        ("signature_key_id", contract.E_SCHEMA, contract.E_SCHEMA),
        ("signature_spki", contract.E_SCHEMA, contract.E_SCHEMA),
        ("absolute_dmg_path", contract.E_SCHEMA, contract.E_SCHEMA),
        ("noncanonical_dmg_path", contract.E_SCHEMA, contract.E_SCHEMA),
        ("zero_dmg_size", contract.E_SCHEMA, contract.E_SCHEMA),
        ("product_version", None, contract.E_PROOF),
        ("product_build", None, contract.E_PROOF),
        ("product_architecture", contract.E_SCHEMA, contract.E_SCHEMA),
        ("product_minimum_macos", contract.E_SCHEMA, contract.E_SCHEMA),
        ("product_manifest_path", contract.E_SCHEMA, contract.E_SCHEMA),
        ("product_manifest_hash", None, contract.E_PROOF),
        ("outer_path", contract.E_SCHEMA, contract.E_SCHEMA),
        ("outer_raw_hash", None, contract.E_HASH),
        ("outer_code_identity", contract.E_SCHEMA, contract.E_SCHEMA),
        ("outer_code_hash", None, contract.E_HASH),
        ("outer_cdhash", None, contract.E_PROOF),
        ("signer_team", contract.E_SCHEMA, contract.E_SCHEMA),
        ("signer_requirement", contract.E_SCHEMA, contract.E_SCHEMA),
        ("signer_runtime", contract.E_SCHEMA, contract.E_SCHEMA),
        ("signer_entitlements", contract.E_SCHEMA, contract.E_SCHEMA),
        ("code_resources_path", contract.E_SCHEMA, contract.E_SCHEMA),
        ("code_resources_hash", None, contract.E_HASH),
        ("module_manifest_path", None, contract.E_PROOF),
        ("module_manifest_hash", None, contract.E_PROOF),
        ("module_receipt_path", None, contract.E_PROOF),
        ("module_receipt_hash", None, contract.E_PROOF),
        ("module_git_sha", None, contract.E_PROOF),
        ("frame_manifest_path", None, contract.E_PROOF),
        ("frame_manifest_hash", None, contract.E_PROOF),
        ("frame_receipt_path", None, contract.E_PROOF),
        ("frame_receipt_hash", None, contract.E_PROOF),
        ("frame_git_sha", None, contract.E_PROOF),
        ("source_revision", None, contract.E_PROOF),
        ("source_terminal", None, contract.E_PROOF),
        ("source_frame", None, contract.E_PROOF),
        ("app_ticket", contract.E_SCHEMA, contract.E_SCHEMA),
        ("app_gatekeeper", contract.E_SCHEMA, contract.E_SCHEMA),
        ("dmg_codesign", contract.E_SCHEMA, contract.E_SCHEMA),
        ("dmg_ticket", contract.E_SCHEMA, contract.E_SCHEMA),
        ("dmg_gatekeeper", contract.E_SCHEMA, contract.E_SCHEMA),
        ("dmg_hash", None, contract.E_HASH),
        ("dmg_size", None, contract.E_SIZE),
    ],
)
def test_release_schema_and_runtime_boundary_mutation_matrix(
    tmp_path: Path,
    macho_executable: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    schema_code: int | None,
    runtime_code: int,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    _app_fixture(app, macho_executable)
    dmg = _canonical_release_dmg_path(tmp_path, app)
    dmg.write_bytes(b"synthetic-dmg\n")
    receipt, signature, payload, signer = _release_output_fixture(tmp_path, app, dmg)

    if mutation == "signature_algorithm":
        payload["signature_policy"]["algorithm"] = "attacker"
    elif mutation == "signature_key_id":
        payload["signature_policy"]["key_id"] = "attacker"
    elif mutation == "signature_spki":
        payload["signature_policy"]["spki_sha256"] = "0" * 64
    elif mutation == "absolute_dmg_path":
        payload["dmg"]["path"] = str(dmg)
    elif mutation == "noncanonical_dmg_path":
        payload["dmg"]["path"] = "artifacts/../Vibecrafted.dmg"
    elif mutation == "zero_dmg_size":
        payload["dmg"]["size"] = 0
    elif mutation == "product_version":
        payload["product"]["version"] = "9.9.9"
    elif mutation == "product_build":
        payload["product"]["build"] = "999"
    elif mutation == "product_architecture":
        payload["product"]["architecture"] = "x86_64"
    elif mutation == "product_minimum_macos":
        payload["product"]["minimum_macos"] = "13.0"
    elif mutation == "product_manifest_path":
        payload["product"]["manifest"]["path"] = "product-manifest.json"
    elif mutation == "product_manifest_hash":
        payload["product"]["manifest"]["sha256"] = "0" * 64
    elif mutation == "outer_path":
        payload["outer_executable"]["path"] = "Contents/MacOS/Other"
    elif mutation == "outer_raw_hash":
        payload["outer_executable"]["sha256"] = "0" * 64
    elif mutation == "outer_code_identity":
        payload["outer_executable"]["code_identity"] = "identity"
    elif mutation == "outer_code_hash":
        payload["outer_executable"]["code_sha256"] = "0" * 64
    elif mutation == "outer_cdhash":
        payload["outer_executable"]["cdhash"] = "0" * 40
    elif mutation == "signer_team":
        payload["outer_executable"]["signer_policy"]["team_id"] = "ATTACKER"
    elif mutation == "signer_requirement":
        payload["outer_executable"]["signer_policy"]["designated_requirement"] = (
            "attacker"
        )
    elif mutation == "signer_runtime":
        payload["outer_executable"]["signer_policy"]["hardened_runtime"] = False
    elif mutation == "signer_entitlements":
        payload["outer_executable"]["signer_policy"]["entitlements"] = {"debug": True}
    elif mutation == "code_resources_path":
        payload["code_resources"]["path"] = "Contents/CodeResources"
    elif mutation == "code_resources_hash":
        payload["code_resources"]["sha256"] = "0" * 64
    elif mutation == "module_manifest_hash":
        payload["modules"]["vc-terminal"]["manifest"]["sha256"] = "0" * 64
    elif mutation == "module_manifest_path":
        payload["modules"]["vc-terminal"]["manifest"]["path"] = "moved.json"
    elif mutation == "module_receipt_path":
        payload["modules"]["vc-terminal"]["assembly_receipt"]["path"] = "moved.json"
    elif mutation == "module_receipt_hash":
        payload["modules"]["vc-terminal"]["assembly_receipt"]["sha256"] = "0" * 64
    elif mutation == "module_git_sha":
        payload["modules"]["vc-terminal"]["git_sha"] = "0" * 40
    elif mutation == "frame_manifest_path":
        payload["modules"]["vc-frame"]["manifest"]["path"] = "moved.json"
    elif mutation == "frame_manifest_hash":
        payload["modules"]["vc-frame"]["manifest"]["sha256"] = "0" * 64
    elif mutation == "frame_receipt_path":
        payload["modules"]["vc-frame"]["assembly_receipt"]["path"] = "moved.json"
    elif mutation == "frame_receipt_hash":
        payload["modules"]["vc-frame"]["assembly_receipt"]["sha256"] = "0" * 64
    elif mutation == "frame_git_sha":
        payload["modules"]["vc-frame"]["git_sha"] = "0" * 40
    elif mutation == "source_revision":
        payload["source_revisions"]["vibecrafted"] = "0" * 40
    elif mutation == "source_terminal":
        payload["source_revisions"]["vc-terminal"] = "0" * 40
    elif mutation == "source_frame":
        payload["source_revisions"]["vc-frame"] = "0" * 40
    elif mutation == "app_ticket":
        payload["notarization"]["app"]["ticket"] = False
    elif mutation == "app_gatekeeper":
        payload["notarization"]["app"]["gatekeeper"] = False
    elif mutation == "dmg_codesign":
        payload["notarization"]["dmg"]["codesign"] = False
    elif mutation == "dmg_ticket":
        payload["notarization"]["dmg"]["ticket"] = False
    elif mutation == "dmg_gatekeeper":
        payload["notarization"]["dmg"]["gatekeeper"] = False
    elif mutation == "dmg_hash":
        payload["dmg"]["sha256"] = "0" * 64
    else:
        payload["dmg"]["size"] += 1

    if schema_code is None:
        contract.validate_schema_document(payload)
    else:
        _assert_error(schema_code, lambda: contract.validate_schema_document(payload))
    _write_canonical_json(receipt, payload)
    monkeypatch.setattr(contract, "_verify_release_signature", _captured_signature_pair)
    monkeypatch.setattr(contract, "_codesign_release_evidence", lambda _: signer)
    _patch_release_mount(monkeypatch, app)
    _assert_error(
        runtime_code, lambda: contract.verify_release_output(receipt, signature)
    )


def test_release_output_validates_public_schema_before_indexing_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = tmp_path / "release-output.json"
    signature = tmp_path / "release-output.json.sig"
    _write_canonical_json(receipt, {})
    signature.write_bytes(b"fixture")
    monkeypatch.setattr(contract, "_verify_release_signature", _captured_signature_pair)

    _assert_error(
        contract.E_SCHEMA,
        lambda: contract.verify_release_output(receipt, signature),
    )


def test_release_output_runtime_guard_rejects_noncanonical_dmg_selector(
    tmp_path: Path,
    macho_executable: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    _app_fixture(app, macho_executable)
    alternate_dmg = tmp_path / "Alternate.dmg"
    alternate_dmg.write_bytes(b"synthetic-alternate-dmg\n")
    receipt, signature, payload, _ = _release_output_fixture(
        tmp_path, app, alternate_dmg
    )
    _write_canonical_json(receipt, payload)
    monkeypatch.setattr(contract, "_verify_release_signature", _captured_signature_pair)
    # Isolate the deliberate runtime defense beneath the public schema gate.
    monkeypatch.setattr(contract, "validate_schema_document", lambda _: None)

    with pytest.raises(contract.ProductContractError) as exc_info:
        contract.verify_release_output(receipt, signature)
    assert exc_info.value.code == contract.E_PROOF
    assert "release DMG path must bind version, date and source revision" in str(
        exc_info.value
    )


@pytest.mark.parametrize("swapped_member", ["receipt", "signature"])
def test_release_output_consumes_only_the_captured_signed_tuple_after_replace(
    tmp_path: Path,
    macho_executable: Path,
    monkeypatch: pytest.MonkeyPatch,
    swapped_member: str,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    _app_fixture(app, macho_executable)
    dmg = _canonical_release_dmg_path(tmp_path, app)
    dmg.write_bytes(b"synthetic-dmg\n")
    receipt, signature, payload, signer = _release_output_fixture(tmp_path, app, dmg)
    captured = _captured_signature_pair(receipt, signature)
    replacement = copy.deepcopy(payload)
    replacement["source_revisions"]["vibecrafted"] = "b" * 40
    original_signature = signature.read_bytes()

    def verify_then_swap(path: Path, detached: Path):
        replacement_path = tmp_path / f"replacement-{swapped_member}"
        if swapped_member == "receipt":
            _write_canonical_json(replacement_path, replacement)
            os.replace(replacement_path, path)
        else:
            replacement_path.write_bytes(b"unsigned-replacement-signature")
            os.replace(replacement_path, detached)
        return captured

    monkeypatch.setattr(contract, "_verify_release_signature", verify_then_swap)
    monkeypatch.setattr(contract, "_codesign_release_evidence", lambda _: signer)
    _patch_release_mount(monkeypatch, app)

    verified, _, returned_captures = contract._verify_release_output(receipt, signature)
    assert verified == payload
    assert returned_captures == captured
    if swapped_member == "receipt":
        assert json.loads(receipt.read_text(encoding="utf-8")) == replacement
    else:
        assert signature.read_bytes() != original_signature
        assert returned_captures[1].raw == original_signature


def test_release_output_mounts_the_captured_dmg_when_source_path_is_swapped(
    tmp_path: Path,
    macho_executable: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    _app_fixture(app, macho_executable)
    dmg = _canonical_release_dmg_path(tmp_path, app)
    signed_bytes = b"signed-dmg-bytes\n"
    dmg.write_bytes(signed_bytes)
    receipt, signature, payload, signer = _release_output_fixture(tmp_path, app, dmg)

    @contextmanager
    def mounted(captured: Path):
        assert captured != dmg
        assert captured.read_bytes() == signed_bytes
        replacement = tmp_path / "replacement.dmg"
        replacement.write_bytes(b"attacker-approved-different-bytes\n")
        os.replace(replacement, dmg)
        assert captured.read_bytes() == signed_bytes
        yield app

    monkeypatch.setattr(contract, "_verify_release_signature", _captured_signature_pair)
    monkeypatch.setattr(contract, "_codesign_release_evidence", lambda _: signer)
    monkeypatch.setattr(contract, "_mounted_release_dmg", mounted)
    monkeypatch.setattr(contract, "_run_live_release_checks", lambda *_: {})

    assert contract.verify_release_output(receipt, signature) == payload
    assert dmg.read_bytes() != signed_bytes


@pytest.mark.parametrize("swapped_member", ["challenge", "signature"])
def test_trust_probe_consumes_only_the_captured_signed_tuple_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swapped_member: str,
) -> None:
    challenge = tmp_path / "challenge.json"
    signature = tmp_path / "challenge.sig"
    payload = {
        "schema": contract.TRUST_PROBE_SCHEMA,
        "domain": contract.TRUST_PROBE_DOMAIN,
        "nonce": "signed-nonce",
    }
    _write_canonical_json(challenge, payload)
    signature.write_bytes(b"fixture-signature")
    captured = _captured_signature_pair(challenge, signature)

    def verify_then_swap(path: Path, detached: Path):
        replacement = tmp_path / f"replacement-{swapped_member}"
        if swapped_member == "challenge":
            _write_canonical_json(replacement, {**payload, "nonce": "unsigned-nonce"})
            os.replace(replacement, path)
        else:
            replacement.write_bytes(b"unsigned-replacement-signature")
            os.replace(replacement, detached)
        return captured

    monkeypatch.setattr(contract, "_verify_release_signature", verify_then_swap)

    assert contract.verify_trust_probe(challenge, signature) == payload


def test_codesign_release_evidence_fails_closed_when_entitlements_are_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    metadata = (
        "CDHash=" + "a" * 40 + "\n"
        "TeamIdentifier=MW223P3NPX\n"
        "CodeDirectory v=20500 size=1 flags=0x10000(runtime)\n"
    )

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[Any]:
        if "--requirements" in command:
            return subprocess.CompletedProcess(
                command, 0, "", "designated => requirement\n"
            )
        if "--entitlements" in command:
            return subprocess.CompletedProcess(command, 1, b"", b"codesign failed\n")
        return subprocess.CompletedProcess(command, 0, metadata, "")

    monkeypatch.setattr(
        contract, "_required_tool", lambda *_args, **_kwargs: "codesign"
    )
    monkeypatch.setattr(contract.subprocess, "run", fake_run)

    _assert_error(contract.E_PROOF, lambda: contract._codesign_release_evidence(app))


def test_codesign_release_evidence_accepts_empty_success_as_no_entitlements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    metadata = (
        "CDHash=" + "a" * 40 + "\n"
        "TeamIdentifier=MW223P3NPX\n"
        "CodeDirectory v=20500 size=1 flags=0x10000(runtime)\n"
    )

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[Any]:
        if "--requirements" in command:
            return subprocess.CompletedProcess(
                command, 0, "", "designated => requirement\n"
            )
        if "--entitlements" in command:
            return subprocess.CompletedProcess(command, 0, b"", b"Executable=fixture\n")
        return subprocess.CompletedProcess(command, 0, metadata, "")

    monkeypatch.setattr(
        contract, "_required_tool", lambda *_args, **_kwargs: "codesign"
    )
    monkeypatch.setattr(contract.subprocess, "run", fake_run)

    evidence = contract._codesign_release_evidence(app)
    assert evidence["entitlements"] == {}


@pytest.mark.parametrize(
    "failure",
    [
        "display",
        "requirements",
        "semantic-requirement",
        "malformed-entitlements",
        "truncated-xml-entitlements",
    ],
)
def test_codesign_release_evidence_fails_closed_at_every_subprocess_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    metadata = (
        "CDHash=" + "a" * 40 + "\n"
        "TeamIdentifier=MW223P3NPX\n"
        "CodeDirectory v=20500 size=1 flags=0x10000(runtime)\n"
    )

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[Any]:
        if "--requirements" in command:
            return subprocess.CompletedProcess(
                command,
                1 if failure == "requirements" else 0,
                "",
                "designated => fixture requirement\n",
            )
        if "--test-requirement" in command:
            return subprocess.CompletedProcess(
                command, 1 if failure == "semantic-requirement" else 0, "", ""
            )
        if "--entitlements" in command:
            entitlement = {
                "malformed-entitlements": b"not-a-plist",
                "truncated-xml-entitlements": b"<plist><dict>",
            }.get(failure, plistlib.dumps({}))
            return subprocess.CompletedProcess(command, 0, entitlement, b"")
        return subprocess.CompletedProcess(
            command, 1 if failure == "display" else 0, metadata, ""
        )

    monkeypatch.setattr(
        contract, "_required_tool", lambda *_args, **_kwargs: "codesign"
    )
    monkeypatch.setattr(contract.subprocess, "run", fake_run)

    _assert_error(contract.E_PROOF, lambda: contract._codesign_release_evidence(app))


@pytest.mark.skipif(
    os.environ.get("VIBECRAFTED_REQUIRE_RELEASE_CREDENTIALS") != "1",
    reason="operator-gated Developer-ID signer-policy probe",
)
def test_real_developer_id_requirement_passes_and_entitlement_drift_fails(
    tmp_path: Path, macho_executable: Path
) -> None:
    identity = "Developer ID Application: Maciej Gad (MW223P3NPX)"
    app = tmp_path / "Vibecrafted.app"
    _app_fixture(app, macho_executable)
    empty_entitlements = tmp_path / "empty-entitlements.plist"
    empty_entitlements.write_bytes(plistlib.dumps({}))
    signed = subprocess.run(
        [
            "codesign",
            "--force",
            "--options",
            "runtime",
            "--entitlements",
            str(empty_entitlements),
            "--sign",
            identity,
            str(app),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert signed.returncode == 0, signed.stderr
    evidence = contract._codesign_release_evidence(app)
    policy = contract._release_policy()
    expected_signer = {
        "team_id": policy["team_id"],
        "designated_requirement": policy["designated_requirement"],
        "hardened_runtime": policy["hardened_runtime"],
        "entitlements": policy["entitlements"],
    }
    outer = {"cdhash": evidence["cdhash"], "signer_policy": expected_signer}
    contract._verify_release_signer_policy(app, outer)

    entitlements = tmp_path / "entitlements.plist"
    entitlements.write_bytes(
        plistlib.dumps({"com.apple.security.get-task-allow": True})
    )
    drifted = subprocess.run(
        [
            "codesign",
            "--force",
            "--deep",
            "--options",
            "runtime",
            "--entitlements",
            str(entitlements),
            "--sign",
            identity,
            str(app),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert drifted.returncode == 0, drifted.stderr
    _assert_error(
        contract.E_PROOF,
        lambda: contract._verify_release_signer_policy(app, outer),
    )


@pytest.mark.skipif(
    os.environ.get("VIBECRAFTED_REQUIRE_RELEASE_CREDENTIALS") != "1",
    reason="operator-gated real signed-artifact mutation matrix",
)
@pytest.mark.parametrize(
    ("mutation", "expected_code", "expected_detail"),
    [
        ("outer_raw_bytes", contract.E_HASH, "outer executable identity"),
        ("outer_code_bytes", contract.E_HASH, "Mach-O code identity"),
        ("code_resources_bytes", contract.E_PROOF, "code signature"),
        ("terminal_manifest_bytes", contract.E_SIZE, "size mismatch"),
        ("terminal_receipt_bytes", contract.E_SIZE, "size mismatch"),
        ("frame_manifest_bytes", contract.E_SIZE, "size mismatch"),
        ("frame_receipt_bytes", contract.E_SIZE, "size mismatch"),
        ("source_vibecrafted", contract.E_PROOF, "source revisions"),
        ("source_terminal", contract.E_PROOF, "source revisions"),
        ("source_frame", contract.E_PROOF, "source revisions"),
        ("notary_flag", contract.E_SCHEMA, "schema validation failed"),
        ("product_manifest_path", contract.E_SCHEMA, "schema validation failed"),
        ("detached_signature", contract.E_PROOF, "detached release signature"),
        ("dmg_bytes", contract.E_SIZE, "DMG size mismatch"),
        ("dmg_selector", contract.E_SCHEMA, "schema validation failed"),
        ("unnotarized", contract.E_PROOF, "Unnotarized Developer ID"),
    ],
)
def test_real_signed_release_mutations_enter_through_the_public_verifier(
    tmp_path: Path,
    macho_executable: Path,
    mutation: str,
    expected_code: int,
    expected_detail: str,
) -> None:
    app = _developer_id_release_app(tmp_path, macho_executable)
    product = json.loads(
        (app / "Contents/Resources/product-manifest.json").read_text(encoding="utf-8")
    )
    signer = contract._codesign_release_evidence(app)
    dmg = _create_signed_release_dmg(tmp_path, app)
    receipt, signature, payload, _ = _release_output_fixture(
        tmp_path, app, dmg, signer=signer
    )
    _sign_release_receipt(receipt, signature)

    module_bindings = {item["module"]: item for item in product["modules"]}
    physical_targets = {
        "outer_raw_bytes": app / product["outer_bundle_code"]["path"],
        "outer_code_bytes": app / product["outer_bundle_code"]["path"],
        "code_resources_bytes": app / "Contents/_CodeSignature/CodeResources",
        "terminal_manifest_bytes": app
        / module_bindings["vc-terminal"]["manifest_path"],
        "terminal_receipt_bytes": app
        / module_bindings["vc-terminal"]["assembly_receipt_path"],
        "frame_manifest_bytes": app / module_bindings["vc-frame"]["manifest_path"],
        "frame_receipt_bytes": app
        / module_bindings["vc-frame"]["assembly_receipt_path"],
    }
    target = physical_targets.get(mutation)
    if target is not None:
        if mutation == "outer_raw_bytes":
            raw = bytearray(target.read_bytes())
            raw[-1] ^= 0x01
            target.write_bytes(raw)
        elif mutation == "outer_code_bytes":
            _mutate_macho_code_byte(target)
        else:
            target.write_bytes(target.read_bytes() + b"\nreal-mutation")
        dmg = _create_signed_release_dmg(tmp_path, app)
        payload["dmg"] = {
            "path": dmg.relative_to(tmp_path).as_posix(),
            "sha256": _sha256(dmg),
            "size": dmg.stat().st_size,
        }
    elif mutation == "source_vibecrafted":
        payload["source_revisions"]["vibecrafted"] = "0" * 40
    elif mutation == "source_terminal":
        payload["source_revisions"]["vc-terminal"] = "0" * 40
    elif mutation == "source_frame":
        payload["source_revisions"]["vc-frame"] = "0" * 40
    elif mutation == "notary_flag":
        payload["notarization"]["dmg"]["gatekeeper"] = False
    elif mutation == "product_manifest_path":
        payload["product"]["manifest"]["path"] = "product-manifest.json"
    elif mutation == "dmg_bytes":
        dmg.write_bytes(dmg.read_bytes() + b"unsigned replacement")
    elif mutation == "dmg_selector":
        dmg = _create_signed_release_dmg(tmp_path, app, name="Alternate.dmg")
        payload["dmg"] = {
            "path": dmg.relative_to(tmp_path).as_posix(),
            "sha256": _sha256(dmg),
            "size": dmg.stat().st_size,
        }
    elif mutation == "detached_signature":
        signature.write_bytes(signature.read_bytes() + b"tamper")
    elif mutation != "unnotarized":
        raise AssertionError(f"unknown mutation: {mutation}")

    if mutation != "detached_signature":
        _write_canonical_json(receipt, payload)
        _sign_release_receipt(receipt, signature)

    with pytest.raises(contract.ProductContractError) as exc_info:
        contract.verify_release_output(receipt, signature)
    assert exc_info.value.code == expected_code
    assert expected_detail in str(exc_info.value)


def test_signed_payload_capture_rejects_symlink_and_hardlink_aliases(
    tmp_path: Path,
) -> None:
    original = tmp_path / "release-output.json"
    original.write_bytes(b"{}\n")
    symlink = tmp_path / "alias.json"
    symlink.symlink_to(original)
    _assert_error(
        contract.E_MISSING,
        lambda: contract._capture_proof_artifact(symlink, context="fixture"),
    )
    hardlink = tmp_path / "hardlink.json"
    os.link(original, hardlink)
    _assert_error(
        contract.E_PATH,
        lambda: contract._capture_proof_artifact(original, context="fixture"),
    )


def test_release_signature_rejects_trailing_bytes_before_openssl(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "release-output.json"
    signature = tmp_path / "release-output.json.sig"
    receipt.write_bytes(b"{}\n")
    signature.write_bytes(b"x" * (contract._RELEASE_SIGNATURE_BYTES + 1))

    _assert_error(
        contract.E_PROOF,
        lambda: contract._verify_release_signature(receipt, signature),
    )


@pytest.mark.parametrize("member", ["receipt", "signature"])
@pytest.mark.parametrize("alias_kind", ["alternate", "symlink", "hardlink"])
def test_release_signature_tuple_rejects_every_noncanonical_member_alias(
    tmp_path: Path,
    member: str,
    alias_kind: str,
) -> None:
    receipt = tmp_path / "release-output.json"
    signature = tmp_path / "release-output.json.sig"
    receipt.write_bytes(b"{}\n")
    signature.write_bytes(b"fixture-signature")
    selected = receipt if member == "receipt" else signature
    original = tmp_path / f"original-{selected.name}"
    selected.replace(original)
    if alias_kind == "alternate":
        selected = original
    elif alias_kind == "symlink":
        selected.symlink_to(original.name)
    else:
        os.link(original, selected)

    receipt_arg = selected if member == "receipt" else receipt
    signature_arg = selected if member == "signature" else signature
    expected = (
        contract.E_PROOF
        if alias_kind == "alternate"
        else (contract.E_MISSING if alias_kind == "symlink" else contract.E_PATH)
    )
    _assert_error(
        expected,
        lambda: contract._verify_release_output(receipt_arg, signature_arg),
    )


def test_release_output_rejects_zero_byte_dmg_even_when_hash_and_size_match(
    tmp_path: Path,
    macho_executable: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    _app_fixture(app, macho_executable)
    dmg = _canonical_release_dmg_path(tmp_path, app)
    dmg.write_bytes(b"")
    receipt, signature, _, signer = _release_output_fixture(tmp_path, app, dmg)
    monkeypatch.setattr(contract, "_verify_release_signature", _captured_signature_pair)
    monkeypatch.setattr(contract, "_codesign_release_evidence", lambda _: signer)
    _patch_release_mount(monkeypatch, app)

    _assert_error(
        contract.E_SCHEMA,
        lambda: contract.verify_release_output(receipt, signature),
    )


@pytest.mark.parametrize(
    "signer_drift",
    [
        {"team_id": "ATTACKERTEAM"},
        {"entitlements": {"com.apple.security.get-task-allow": True}},
    ],
)
def test_release_output_rejects_foreign_signer_or_entitlement_drift_even_with_same_code(
    tmp_path: Path,
    macho_executable: Path,
    monkeypatch: pytest.MonkeyPatch,
    signer_drift: dict[str, Any],
) -> None:
    app = tmp_path / "Vibecrafted.app"
    product = _app_fixture(app, macho_executable)
    dmg = _canonical_release_dmg_path(tmp_path, app)
    dmg.write_bytes(b"synthetic-dmg\n")
    signer = {
        "cdhash": "b" * 40,
        "team_id": "MW223P3NPX",
        "designated_requirement": contract._release_policy()["designated_requirement"],
        "hardened_runtime": True,
        "entitlements": {},
        **signer_drift,
    }
    receipt, signature, _, _ = _release_output_fixture(
        tmp_path, app, dmg, signer=signer
    )
    original_code_identity = product["outer_bundle_code"]["code_sha256"]
    assert (
        contract._macho_code_sha256(app / "Contents/MacOS/Vibecrafted")
        == original_code_identity
    )
    monkeypatch.setattr(contract, "_verify_release_signature", _captured_signature_pair)
    monkeypatch.setattr(contract, "_codesign_release_evidence", lambda _: signer)
    _patch_release_mount(monkeypatch, app)

    _assert_error(
        contract.E_PROOF,
        lambda: contract.verify_release_output(receipt, signature),
    )


@pytest.mark.parametrize(
    "foreign_entitlements",
    [None, {"com.apple.security.get-task-allow": True}],
)
def test_release_output_rejects_real_foreign_resign_without_mocking_codesign(
    tmp_path: Path,
    macho_executable: Path,
    monkeypatch: pytest.MonkeyPatch,
    foreign_entitlements: dict[str, bool] | None,
) -> None:
    app = tmp_path / "Vibecrafted.app"
    product = _app_fixture(app, macho_executable)
    executable = app / "Contents/MacOS/Vibecrafted"
    original_code_identity = contract._macho_code_sha256(executable)
    command = ["codesign", "--force", "--sign", "-", "--options", "runtime"]
    if foreign_entitlements is not None:
        entitlement_path = tmp_path / "foreign-entitlements.plist"
        with entitlement_path.open("wb") as handle:
            plistlib.dump(foreign_entitlements, handle)
        command.extend(["--entitlements", str(entitlement_path)])
    result = subprocess.run(
        [*command, str(app)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert contract._macho_code_sha256(executable) == original_code_identity
    assert product["outer_bundle_code"]["code_sha256"] == original_code_identity

    dmg = _canonical_release_dmg_path(tmp_path, app)
    dmg.write_bytes(b"synthetic-dmg\n")
    receipt, signature, _, _ = _release_output_fixture(tmp_path, app, dmg)
    monkeypatch.setattr(contract, "_verify_release_signature", _captured_signature_pair)
    _patch_release_mount(monkeypatch, app)

    _assert_error(
        contract.E_PROOF,
        lambda: contract.verify_release_output(receipt, signature),
    )


def test_cli_returns_distinct_nonzero_codes_for_hash_and_dylib_failures(
    tmp_path: Path,
    macho_executable: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "module"
    manifest = _module_fixture(root, macho_executable)
    assert contract.main(["schema", str(root / "module-manifest.json")]) == 0
    assert contract.main(["module", str(root)]) == 0

    manifest["files"][0]["sha256"] = "f" * 64
    _write_json(root / "module-manifest.json", manifest)
    assert contract.main(["module", str(root)]) == contract.E_HASH
    assert "VCPC024" in capsys.readouterr().err

    manifest = _module_fixture(root, macho_executable)
    manifest["files"][0]["dylibs"] = ["/usr/local/lib/libescape.dylib"]
    _write_json(root / "module-manifest.json", manifest)
    assert contract.main(["module", str(root)]) == contract.E_DEPENDENCY
    assert "VCPC027" in capsys.readouterr().err


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="real product-contract self-test requires the macOS signing toolchain",
)
def test_shell_front_door_self_test_exercises_real_verifier() -> None:
    env = {
        "HOME": os.environ["HOME"],
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        [str(VERIFY_SCRIPT), "--self-test"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "self-test: PASS valid=4 negative=2 error_codes=24,27" in result.stdout


def test_unified_release_has_one_top_level_owner() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    shell_makefile = (REPO_ROOT / "vibecrafted-app/shell-agent/Makefile").read_text(
        encoding="utf-8"
    )

    assert "release:\n\t@zsh -ic" in makefile
    assert 'make -C "$TERMINAL_REPO"' in builder
    assert "release-bins" in builder
    assert 'chmod 0755 "$terminal_source"' in builder
    assert 'local terminal_app="$APP/Contents/Helpers/vc-terminal.app"' in builder
    assert '"$terminal_app/Contents/MacOS/alacritty"' in builder
    assert '"$terminal_app/Contents/Resources/alacritty.icns"' in builder
    assert "sign_nested_app_bundles" in builder
    assert 'make -C "$FRAME_REPO" release-binary' in builder
    assert 'chmod 0755 "$frame_source"' in builder
    assert "build-server-release" in builder
    assert '"$runtime/bin/vc-server"' in builder
    assert '"$runtime/server/site/"' in builder
    assert "uv python install 3.12.3" in builder
    assert "install_name_tool -id '@loader_path/libpython3.12.dylib'" in builder
    # The remaps are built from PATH_REMAPS rather than written as one literal
    # string, because the snapshot pair is conditional and because rustc applies
    # the LAST match — so the list has to be ordered broadest-first, with $HOME
    # ahead of the checkout instead of trailing it.
    assert '"$HOME=/usr/src/operator-home"' in builder
    assert "--remap-path-prefix=$mapping" in builder
    assert "install_name_tool -delete_rpath /usr/lib/swift" in builder
    assert 'run_bundled_verifier app "$APP" --require-clean' in builder
    assert "run_bundled_verifier release-output" in builder
    assert '"$verifier" -m vibecrafted_core.product_contract "$@"' in builder
    assert '"$runtime/vibecrafted-core/vibecrafted_core/VERSION"' in builder
    assert '"$runtime/scripts/vetcoders_install.py"' in builder
    assert '"$runtime/scripts/distribution_manifest.py"' in builder
    assert '"$runtime/scripts/installer_brand.py"' in builder
    assert '"$REPO_ROOT/scripts/verify-vibecrafted-product.sh"' not in builder
    assert "--noprofile" not in builder  # vc-start, not the release shell, owns this
    assert "vc-frame.real" not in builder
    assert '"$runtime/runtime"' not in builder
    assert "$(MAKE) -C ../.. release" in shell_makefile
    assert not (REPO_ROOT / "vibecrafted-app/shell-agent/scripts/build-dmg.sh").exists()


def test_terminal_policy_uses_operator_toml_and_primary_shell_chain() -> None:
    terminal = (REPO_ROOT / "config/vc-terminal/vibecrafted.toml").read_text(
        encoding="utf-8"
    )
    dark = (REPO_ROOT / "config/vc-terminal/themes/dark.toml").read_text(
        encoding="utf-8"
    )
    light = (REPO_ROOT / "config/vc-terminal/themes/light.toml").read_text(
        encoding="utf-8"
    )
    primary_shell = (REPO_ROOT / "config/alacritty/launch-primary-shell.zsh").read_text(
        encoding="utf-8"
    )
    delegate = (
        REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/AppDelegate.swift"
    ).read_text(encoding="utf-8")
    installer = (REPO_ROOT / "scripts/vetcoders_install.py").read_text(encoding="utf-8")

    assert 'family = "Spot Mono"' in terminal
    assert "size = 18.5" in terminal
    assert "x = -1" in terminal
    assert "y = 2" in terminal
    assert 'style = { shape = "Underline", blinking = "On" }' in terminal
    assert 'cyan    = "#7dc4e4"' in dark
    assert 'cyan    = "#56949f"' in light
    assert "live_config_reload = true" in terminal
    assert "save_to_clipboard = true" in terminal
    assert 'command = "open"' in terminal
    assert 'mods = "Command"' in terminal
    assert 'key = "Period"' in terminal
    assert 'mods = "Command|Shift"' in terminal
    assert 'chars = "\\u001b[46;10u"' in terminal
    assert "launch-primary-shell.zsh" in terminal
    assert "$VIBECRAFTED_RUNTIME_ROOT/bin/vc-start" in terminal
    assert "${1##*/}" in primary_shell
    assert '"$0" "$@"' in primary_shell
    assert "process.executableURL = install.terminalHost" in delegate
    assert '"-e", install.primaryShell.path, install.start.path, "operator"' in delegate
    assert 'product_config / "terminal-entry.toml"' in installer
    assert 'product_config / "terminal-theme.toml"' in installer
    assert 'let socketRoot = "/tmp/vc-frame-\\(getuid())"' in delegate
    assert 'environment["VC_FRAME_SOCKET_DIR"] = socketRoot' in delegate
    assert 'environment["ZELLIJ_SOCKET_DIR"] = socketRoot' in delegate
    assert 'environment["VIBECRAFTED_LEGACY_VC_FRAME_SOCKET_DIR"]' in delegate


def test_installed_deck_resolves_server_binary_and_site_from_its_generation() -> None:
    deck = (REPO_ROOT / "scripts/vibecrafted").read_text(encoding="utf-8")
    canonical = (
        REPO_ROOT / "vibecrafted-core/vibecrafted_core/deck/vibecrafted"
    ).read_bytes()

    assert canonical == (REPO_ROOT / "scripts/vibecrafted").read_bytes()
    assert "_runtime_payload_root()" in deck
    assert 'server_bin="${runtime_root:+$runtime_root/bin/vc-server}"' in deck
    assert 'site_root="${runtime_root:+$runtime_root/server/site}"' in deck
    assert 'local server_bin="$HOME/.local/bin/vc-server"' not in deck
    assert (
        '"$script_dir/../vibecrafted-core/vibecrafted_core/runtime/scripts/lib/ulimits.sh"'
        in deck
    )


def test_primary_shell_exits_instead_of_reusing_pty_after_vc_start_failure(
    tmp_path: Path,
) -> None:
    failing_start = tmp_path / "vc-start"
    failing_start.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    failing_start.chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "config/alacritty/launch-primary-shell.zsh"),
            str(failing_start),
            "operator",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 7


def test_manifest_producer_emits_an_app_accepted_by_the_runtime_verifier(
    tmp_path: Path, macho_executable: Path
) -> None:
    app = tmp_path / "Vibecrafted.app"
    _app_fixture(app, macho_executable)
    script = REPO_ROOT / "scripts/unified_product_manifest.py"
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "vibecrafted-core")}
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "app",
            "--app",
            str(app),
            "--terminal-source",
            str(macho_executable),
            "--frame-source",
            str(macho_executable),
            "--version",
            "1.0.0",
            "--build",
            "1",
            "--vibecrafted-sha",
            "2" * 40,
            "--terminal-sha",
            "4" * 40,
            "--frame-sha",
            "6" * 40,
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    _codesign_app(app)
    product = contract.verify_app(app, require_clean=True)
    assert [item["module"] for item in product["modules"]] == [
        "vc-terminal",
        "vc-frame",
    ]
