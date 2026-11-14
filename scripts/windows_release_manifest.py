#!/usr/bin/env python3
"""Bind native Windows carriers to one tested product source and workflow run."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import tarfile
from pathlib import Path

SCHEMA = "io.vetcoders.vibecrafted.windows-release-output.v1"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_PACK = re.compile(
    r"Vibecrafted_RuntimePack_(\d+\.\d+\.\d+)-(\d{8})-([0-9a-f]{8})-win32-x64\.tar\.gz\Z"
)
_MSI_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


def _regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or non-regular release artifact: {path.name}")


def _record(path: Path) -> dict:
    _regular(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": path.name, "sha256": digest, "size": path.stat().st_size}


def _checksummed(path: Path) -> dict:
    record = _record(path)
    sidecar = Path(f"{path}.sha256")
    _regular(sidecar)
    if sidecar.stat().st_size > 4096:
        raise ValueError(f"oversized checksum: {sidecar.name}")
    line = sidecar.read_text(encoding="ascii").strip().split()
    if line != [record["sha256"], path.name]:
        raise ValueError(f"checksum mismatch: {path.name}")
    return record


def _one(directory: Path, pattern: str) -> Path:
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {pattern} artifact")
    _regular(matches[0])
    return matches[0]


def _native_carriers(msi: Path, exe: Path) -> None:
    with msi.open("rb") as stream:
        if stream.read(8) != _MSI_MAGIC:
            raise ValueError("MSI is not a native compound-file installer")
    with exe.open("rb") as stream:
        dos = stream.read(64)
        if len(dos) != 64 or dos[:2] != b"MZ":
            raise ValueError("EXE is not a native PE installer")
        offset = struct.unpack_from("<I", dos, 0x3C)[0]
        if not 64 <= offset <= exe.stat().st_size - 6:
            raise ValueError("invalid EXE PE offset")
        stream.seek(offset)
        pe = stream.read(6)
        # WiX 3 Burn may use an x86 bootstrapper around the x64 MSI/payload.
        if pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] not in (
            0x014C,
            0x8664,
        ):
            raise ValueError("EXE has an unsupported PE machine")


def build_manifest(
    directory: Path,
    public_key: Path,
    *,
    source_sha: str,
    terminal_sha: str,
    frame_sha: str,
    controller_sha: str,
    repository: str,
    run_id: int,
    run_attempt: int,
    request_id: str,
    verification: dict[str, str],
) -> dict:
    for value in (source_sha, terminal_sha, frame_sha, controller_sha):
        if not _SHA.fullmatch(value):
            raise ValueError("release revisions must be full lowercase Git SHAs")
    if repository != "vetcoders/vibecrafted" or run_id < 1 or run_attempt < 1:
        raise ValueError("invalid workflow identity")
    if not re.fullmatch(r"[0-9a-f]{24}", request_id):
        raise ValueError("release request_id must be 24 lowercase hex characters")
    if verification != {
        "cold_install": "success",
        "msi_install": "success",
        "exe_install": "success",
    }:
        raise ValueError("all three native install/doctor/uninstall jobs must succeed")
    if any(directory.glob("*.rehearsal.*")) or (directory / "ci-signing.pub").exists():
        raise ValueError("rehearsal signing material cannot enter a release")
    pack = _one(directory, "Vibecrafted_RuntimePack_*-win32-x64.tar.gz")
    match = _PACK.fullmatch(pack.name)
    if not match or match[3] != source_sha[:8]:
        raise ValueError("Windows Runtime Pack name disagrees with product source")
    version = match[1]
    msi = _one(directory, f"Vibecrafted_{version}-*-{source_sha[:8]}-windows-x64.msi")
    exe = _one(directory, f"Vibecrafted_{version}-*-{source_sha[:8]}-windows-x64.exe")
    records = {
        name: _checksummed(path)
        for name, path in (("runtime_pack", pack), ("msi", msi), ("exe", exe))
    }
    _native_carriers(msi, exe)
    signature = Path(f"{pack}.sig")
    _regular(signature)
    _regular(public_key)
    result = subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            str(public_key),
            "-signature",
            str(signature),
            str(pack),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("Windows Runtime Pack is not signed by the product key")
    with tarfile.open(pack, "r:gz") as archive:
        manifests = [
            member
            for member in archive
            if member.name == "VibecraftedRuntime/runtime-pack-provenance.json"
        ]
        if (
            len(manifests) != 1
            or not manifests[0].isfile()
            or not 0 < manifests[0].size <= 2_000_000
        ):
            raise ValueError("missing or ambiguous Runtime Pack provenance")
        provenance = json.load(archive.extractfile(manifests[0]))
    expected = {
        "vibecrafted": source_sha,
        "vc-terminal": terminal_sha,
        "vc-frame": frame_sha,
    }
    if (
        not isinstance(provenance, dict)
        or provenance.get("schema")
        != "io.vetcoders.vibecrafted.runtime-pack-provenance.v1"
        or provenance.get("source_revisions") != expected
        or provenance.get("platform") != "win32-x64"
        or provenance.get("architecture") != "x64"
        or provenance.get("carrier_basename") != pack.name
        or provenance.get("version") not in (version, f"{version}+g{source_sha[:8]}")
    ):
        raise ValueError(
            "Windows Runtime Pack provenance disagrees with the release request"
        )
    return {
        "schema": SCHEMA,
        "product": {"version": version, "platform": "win32-x64", "architecture": "x64"},
        "source_revisions": expected,
        **records,
        "workflow": {
            "repository": repository,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "controller_sha": controller_sha,
            "source_sha": source_sha,
            "request_id": request_id,
        },
        "verification": verification,
        "signing": {"runtime_pack": "product-key", "authenticode": "unsigned"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--public-key", required=True, type=Path)
    for name in (
        "source-sha",
        "terminal-sha",
        "frame-sha",
        "controller-sha",
        "repository",
        "request-id",
    ):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    for name in ("cold-install", "msi-install", "exe-install"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    try:
        receipt = build_manifest(
            args.directory,
            args.public_key,
            source_sha=args.source_sha,
            terminal_sha=args.terminal_sha,
            frame_sha=args.frame_sha,
            controller_sha=args.controller_sha,
            repository=args.repository,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            request_id=args.request_id,
            verification={
                "cold_install": args.cold_install,
                "msi_install": args.msi_install,
                "exe_install": args.exe_install,
            },
        )
        output = args.directory / "windows-release.json"
        if output.exists() or output.is_symlink():
            raise ValueError("release receipt already exists")
        output.write_text(
            json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError, tarfile.TarError) as exc:
        parser.exit(1, f"Windows release refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
