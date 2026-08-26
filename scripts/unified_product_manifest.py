#!/usr/bin/env python3
"""Produce the receipts consumed by the fail-closed unified product verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from vibecrafted_core import product_contract as contract
from vibecrafted_core import runtime_pack_contract


def _write(path: Path, payload: dict[str, Any], *, canonical: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    separators = (",", ":") if canonical else None
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=separators)
        + "\n",
        encoding="utf-8",
    )


def _entry(root: Path, relative: str, *, kind: str | None = None) -> dict[str, Any]:
    path = root / relative
    if kind is None:
        if relative in {
            "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty",
            "Contents/Helpers/vc-frame",
            "Contents/Resources/runtime/bin/vc-start",
        }:
            kind = "executable"
        elif path.suffix == ".dylib":
            kind = "dylib"
        elif (
            (relative.startswith("Contents/MacOS/") or "/Contents/MacOS/" in relative)
            and os.access(path, os.X_OK)
            or (
                "/python/bin/" in relative
                and os.access(path, os.X_OK)
                and "Mach-O"
                in subprocess.run(
                    ["/usr/bin/file", "-b", str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
        ):
            kind = "executable"
        elif path.suffix in {".json", ".plist", ".toml", ".kdl", ".yaml", ".yml"}:
            kind = "config"
        else:
            kind = "resource"
    dylibs: list[str] = []
    if kind in {"executable", "dylib"}:
        observed = contract._observed_macho(path, relative=relative, kind=kind)
        if observed is None:
            raise SystemExit(f"declared code is not Mach-O: {relative}")
        dylibs = list(observed.dependencies)
    return {
        "path": relative,
        "sha256": contract._sha256(path),
        "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
        "kind": kind,
        "size": path.stat().st_size,
        "dylibs": dylibs,
    }


def _module(
    app: Path,
    *,
    name: str,
    source: Path,
    product_relative: str,
    git_sha: str,
    version: str,
) -> dict[str, Any]:
    entrypoint = "terminal" if name == "vc-terminal" else "frame"
    module_relative = f"bin/{name}"
    source_entry = _entry(source.parent, source.name, kind="executable")
    source_entry["path"] = module_relative
    manifest = {
        "schema": contract.MODULE_SCHEMA,
        "module": name,
        "version": version,
        "git_sha": git_sha,
        "dirty": False,
        "architecture": "arm64",
        "minimum_macos": "14.0",
        "files": [source_entry],
        "entrypoints": {entrypoint: module_relative},
    }
    manifest_relative = (
        f"Contents/Resources/module-receipts/{name}/module-manifest.json"
    )
    _write(app / manifest_relative, manifest)
    manifest_hash = contract._sha256(app / manifest_relative)
    product_entry = _entry(app, product_relative, kind="executable")
    assembly = {
        "schema": contract.ASSEMBLY_SCHEMA,
        "module": name,
        "module_manifest_sha256": manifest_hash,
        "files": [
            {
                "module_path": module_relative,
                "product_path": product_relative,
                "unsigned_sha256": source_entry["sha256"],
                "product_sha256": product_entry["sha256"],
                "transformation": "codesign",
            }
        ],
    }
    assembly_relative = (
        f"Contents/Resources/module-receipts/{name}/assembly-receipt.json"
    )
    _write(app / assembly_relative, assembly)
    return {
        "module": name,
        "manifest_path": manifest_relative,
        "manifest_sha256": manifest_hash,
        "assembly_receipt_path": assembly_relative,
        "assembly_receipt_sha256": contract._sha256(app / assembly_relative),
        "git_sha": git_sha,
    }


def produce_app(args: argparse.Namespace) -> None:
    app = args.app.resolve()
    plist_path = app / "Contents/Info.plist"
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)
    plist["CFBundleIdentifier"] = contract.PRODUCT_BUNDLE_ID
    plist["CFBundleExecutable"] = contract.PRODUCT_EXECUTABLE
    plist["CFBundleIconFile"] = contract.PRODUCT_ICON_FILE
    plist["CFBundleShortVersionString"] = args.version
    plist["CFBundleVersion"] = args.build
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=True)

    modules = [
        _module(
            app,
            name="vc-terminal",
            source=args.terminal_source.resolve(),
            product_relative="Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty",
            git_sha=args.terminal_sha,
            version=args.version,
        ),
        _module(
            app,
            name="vc-frame",
            source=args.frame_source.resolve(),
            product_relative="Contents/Helpers/vc-frame",
            git_sha=args.frame_sha,
            version=args.version,
        ),
    ]
    manifest_relative = Path("Contents/Resources/product-manifest.json")
    outer_relative = "Contents/MacOS/Vibecrafted"
    excluded = {
        manifest_relative.as_posix(),
        outer_relative,
    }
    files: list[dict[str, Any]] = []
    for path in sorted(app.rglob("*")):
        relative = path.relative_to(app)
        relative_text = relative.as_posix()
        if (
            not path.is_file()
            or path.is_symlink()
            or relative_text in excluded
            or "Contents/_CodeSignature" in relative_text
            or relative.name == "CodeResources"
        ):
            continue
        files.append(_entry(app, relative_text))
    outer = app / outer_relative
    observed = contract._observed_macho(
        outer, relative=outer_relative, kind="executable"
    )
    if observed is None:
        raise SystemExit("outer executable is not Mach-O")
    product = {
        "schema": contract.PRODUCT_SCHEMA,
        "product": contract.PRODUCT_NAME,
        "bundle_id": contract.PRODUCT_BUNDLE_ID,
        "bundle_executable": contract.PRODUCT_EXECUTABLE,
        "version": args.version,
        "build": args.build,
        "git_sha": args.vibecrafted_sha,
        "dirty": False,
        "architecture": "arm64",
        "minimum_macos": "14.0",
        "modules": modules,
        "outer_bundle_code": {
            "identity": contract.OUTER_BUNDLE_CODE_IDENTITY,
            "path": outer_relative,
            "mode": f"{stat.S_IMODE(outer.stat().st_mode):04o}",
            "kind": "executable",
            "architecture": "arm64",
            "minimum_macos": "14.0",
            "dylibs": list(observed.dependencies),
            "code_identity": contract.MACHO_CODE_IDENTITY,
            "code_sha256": contract._macho_code_sha256(outer),
            "info_plist_sha256": contract._sha256(plist_path),
            "codesign_identifier": contract.PRODUCT_BUNDLE_ID,
        },
        "files": files,
        "entrypoints": {
            "app": outer_relative,
            "terminal": "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty",
            "frame": "Contents/Helpers/vc-frame",
        },
        "launch_contract": contract._canonical_launch_contract(),
    }
    _write(app / manifest_relative, product)


def produce_release(args: argparse.Namespace) -> None:
    root = args.output.parent.resolve()
    app = args.app.resolve()
    dmg = args.dmg.resolve()
    product_path = app / "Contents/Resources/product-manifest.json"
    product = json.loads(product_path.read_text(encoding="utf-8"))
    modules = {item["module"]: item for item in product["modules"]}
    executable = app / product["outer_bundle_code"]["path"]
    signer = contract._codesign_release_evidence(app)
    policy = contract._release_policy()
    runtime_pack = args.runtime_pack.resolve()
    embedded_runtime_pack = app / "Contents/Resources/runtime-pack" / runtime_pack.name
    if (
        runtime_pack.stat().st_size != embedded_runtime_pack.stat().st_size
        or contract._sha256(runtime_pack) != contract._sha256(embedded_runtime_pack)
    ):
        raise SystemExit(
            "standalone Runtime Pack bytes differ from the App-embedded carrier"
        )
    with tarfile.open(runtime_pack, "r:gz") as archive:
        member = archive.getmember(
            f"VibecraftedRuntime/{runtime_pack_contract.PROVENANCE_NAME}"
        )
        extracted = archive.extractfile(member)
        if extracted is None:
            raise SystemExit("Runtime Pack provenance cannot be read")
        provenance_raw = extracted.read()
    provenance = json.loads(provenance_raw.decode("utf-8"))
    expected_revisions = {
        "vibecrafted": product["git_sha"],
        "vc-terminal": modules["vc-terminal"]["git_sha"],
        "vc-frame": modules["vc-frame"]["git_sha"],
    }
    if provenance.get("source_revisions") != expected_revisions:
        raise SystemExit("Runtime Pack provenance disagrees with the product sources")
    payload = {
        "schema": contract.RELEASE_OUTPUT_SCHEMA,
        "signature_policy": {
            "algorithm": policy["algorithm"],
            "key_id": "vibecrafted-signing-v1",
            "spki_sha256": policy["public_key_spki_sha256"],
        },
        "product": {
            "version": product["version"],
            "build": product["build"],
            "architecture": product["architecture"],
            "minimum_macos": product["minimum_macos"],
            "manifest": {
                "path": "Contents/Resources/product-manifest.json",
                "sha256": contract._sha256(product_path),
            },
        },
        "outer_executable": {
            "path": product["outer_bundle_code"]["path"],
            "sha256": contract._sha256(executable),
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
            "sha256": contract._sha256(app / "Contents/_CodeSignature/CodeResources"),
        },
        "dmg": {
            "path": dmg.name,
            "sha256": contract._sha256(dmg),
            "size": dmg.stat().st_size,
        },
        "runtime_pack": {
            "path": runtime_pack.name,
            "embedded_path": (f"Contents/Resources/runtime-pack/{runtime_pack.name}"),
            "sha256": contract._sha256(runtime_pack),
            "size": runtime_pack.stat().st_size,
            "provenance": {
                "path": runtime_pack_contract.PROVENANCE_NAME,
                "sha256": hashlib.sha256(provenance_raw).hexdigest(),
                "version": provenance["version"],
                "platform": provenance["platform"],
                "architecture": provenance["architecture"],
                "source_revisions": provenance["source_revisions"],
            },
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
    if dmg.parent != root or not contract.is_canonical_release_dmg_name(
        dmg.name,
        version=product["version"],
        source_revision=product["git_sha"],
    ):
        raise SystemExit(
            "release DMG must be a canonical sibling bound to version, date, and source"
        )
    _write(args.output, payload, canonical=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    app = commands.add_parser("app")
    app.add_argument("--app", type=Path, required=True)
    app.add_argument("--terminal-source", type=Path, required=True)
    app.add_argument("--frame-source", type=Path, required=True)
    app.add_argument("--version", required=True)
    app.add_argument("--build", required=True)
    app.add_argument("--vibecrafted-sha", required=True)
    app.add_argument("--terminal-sha", required=True)
    app.add_argument("--frame-sha", required=True)
    release = commands.add_parser("release")
    release.add_argument("--app", type=Path, required=True)
    release.add_argument("--dmg", type=Path, required=True)
    release.add_argument("--runtime-pack", type=Path, required=True)
    release.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "app":
        produce_app(args)
    else:
        produce_release(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
