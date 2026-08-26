"""Closed identity contract for the immutable Vibecrafted Runtime Pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "io.vetcoders.vibecrafted.runtime-pack-provenance.v1"
PROVENANCE_NAME = "runtime-pack-provenance.json"
SOURCE_PROVENANCE_NAME = "source-provenance.json"
INVENTORY_NAME = "runtime-inventory.json"
FOUNDATIONS_NAME = "runtime-foundations.json"
SOURCE_PROVENANCE_SCHEMA = "vibecrafted.source-provenance.v2"
FOUNDATIONS_SCHEMA = "io.vetcoders.vibecrafted.runtime-foundations.v1"
GIT_SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
LINUX_ARM64_EXECUTABLES = frozenset(
    {
        "vibecrafted",
        "vc-server",
        "loct",
        "loctree",
        "loctree-mcp",
        "loctree-lsp",
        "aicx",
        "aicx-mcp",
        "prview",
        "screenscribe",
        "vc-frame",
        "vc-terminal",
        "voc",
    }
)
RUNTIME_INSTALLER_EXECUTABLES = frozenset(
    {
        "bin/vc-start",
        "scripts/vibecrafted",
    }
)
REQUIRED_FOUNDATION_EXECUTABLES = frozenset(
    {
        "aicx",
        "aicx-mcp",
        "loct",
        "loctree",
        "loctree-lsp",
        "loctree-mcp",
        "prview",
        "vc-server",
        "vc-start",
        "vibecrafted-server-web",
    }
)
FORBIDDEN_PAYLOAD_NAMES = frozenset({".DS_Store"})


class RuntimePackContractError(RuntimeError):
    """Raised when Runtime Pack identity or payload evidence is not closed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"


def _runtime_installer_payload(root: Path) -> None:
    for relative in RUNTIME_INSTALLER_EXECUTABLES:
        path = root / relative
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise RuntimePackContractError(
                f"Runtime Pack installer payload is missing {relative}"
            ) from exc
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o111 == 0:
            raise RuntimePackContractError(
                f"Runtime Pack installer payload is not executable: {relative}"
            )


def _runtime_foundations(root: Path, *, verify_hashes: bool = True) -> dict[str, Any]:
    """Load the closed foundation manifest and bind it to final shipped bytes."""
    path = root / FOUNDATIONS_NAME
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimePackContractError(
            "Runtime Pack foundation manifest is invalid"
        ) from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    required_fields = {
        "schema",
        "versions",
        "source_revisions",
        "source_archives",
        "licenses",
        "files",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required_fields
        or payload.get("schema") != FOUNDATIONS_SCHEMA
        or raw != _canonical_json(payload)
        or not all(
            isinstance(payload.get(field), dict)
            for field in required_fields - {"schema", "files"}
        )
        or not isinstance(files, dict)
        or not REQUIRED_FOUNDATION_EXECUTABLES.issubset(files)
        or any(
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or not isinstance(digest, str)
            or SHA256.fullmatch(digest) is None
            for name, digest in files.items()
        )
    ):
        raise RuntimePackContractError(
            "Runtime Pack foundation manifest violates the closed schema"
        )
    for name, digest in files.items():
        executable = root / "bin" / name
        try:
            mode = executable.lstat().st_mode
        except OSError as exc:
            raise RuntimePackContractError(
                f"Runtime Pack foundation is missing bin/{name}"
            ) from exc
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o111 == 0:
            raise RuntimePackContractError(
                f"Runtime Pack foundation is not executable: bin/{name}"
            )
        if verify_hashes and _sha256(executable) != digest:
            raise RuntimePackContractError(
                f"Runtime Pack foundation digest does not match final bytes: bin/{name}"
            )
    return payload


def refresh_runtime_foundations(root: str | Path) -> dict[str, Any]:
    """Rebind the staged manifest after the final signing/mutation boundary."""
    payload_root = Path(root).resolve(strict=True)
    payload = _runtime_foundations(payload_root, verify_hashes=False)
    files = payload["files"]
    payload["files"] = {
        name: _sha256(payload_root / "bin" / name) for name in sorted(files)
    }
    (payload_root / FOUNDATIONS_NAME).write_text(
        _canonical_json(payload), encoding="utf-8"
    )
    return _runtime_foundations(payload_root)


def _validate_revision(value: str, *, field: str) -> str:
    if GIT_SHA.fullmatch(value) is None:
        raise RuntimePackContractError(f"{field} must be a full Git revision")
    return value


def _payload_files(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.name in FORBIDDEN_PAYLOAD_NAMES:
            raise RuntimePackContractError(
                f"Runtime Pack payload contains mutable host metadata: {relative}"
            )
        if path.is_symlink():
            raise RuntimePackContractError(
                f"Runtime Pack payload contains a symlink: {relative}"
            )
        if path.is_dir():
            continue
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise RuntimePackContractError(
                f"Runtime Pack payload contains a non-regular file: {relative}"
            )
        if relative == PROVENANCE_NAME:
            continue
        records.append(
            {
                "path": relative,
                "sha256": _sha256(path),
                "size": path.stat().st_size,
                "mode": f"{stat.S_IMODE(mode):04o}",
            }
        )
    if not records:
        raise RuntimePackContractError("Runtime Pack payload is empty")
    return records


def _source_provenance(root: Path, *, expected_revision: str) -> dict[str, Any]:
    path = root / SOURCE_PROVENANCE_NAME
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimePackContractError(
            "Runtime Pack source provenance is invalid"
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "owner_repo", "source_revision", "payload"}
        or payload.get("schema") != SOURCE_PROVENANCE_SCHEMA
        or payload.get("owner_repo") != "vetcoders/vibecrafted"
        or payload.get("source_revision") != expected_revision
        or raw != _canonical_json(payload)
    ):
        raise RuntimePackContractError(
            "Runtime Pack source provenance disagrees with the expected source revision"
        )
    return payload


def _linux_arm64_inventory(root: Path) -> dict[str, Any]:
    path = root / INVENTORY_NAME
    try:
        raw = path.read_text(encoding="utf-8")
        inventory = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimePackContractError(
            "Linux arm64 Runtime Pack inventory is invalid"
        ) from exc
    executables = inventory.get("executables") if isinstance(inventory, dict) else None
    required_record = {
        "name",
        "path",
        "sha256",
        "version_argv",
        "version_output",
        "source_url",
        "source_revision",
        "source_archive_sha256",
        "target",
        "license",
    }
    if (
        not isinstance(inventory, dict)
        or set(inventory) != {"schema", "platform", "architecture", "executables"}
        or inventory.get("schema") != "io.vetcoders.vibecrafted.runtime-inventory.v1"
        or inventory.get("platform") != "linux-arm64"
        or inventory.get("architecture") != "arm64"
        or raw != _canonical_json(inventory)
        or not isinstance(executables, list)
        or {record.get("name") for record in executables if isinstance(record, dict)}
        != LINUX_ARM64_EXECUTABLES
    ):
        raise RuntimePackContractError(
            "Linux arm64 Runtime Pack inventory violates the closed schema"
        )
    for record in executables:
        if (
            not isinstance(record, dict)
            or set(record) != required_record
            or not all(
                isinstance(record[field], str) and record[field]
                for field in required_record - {"version_argv"}
            )
            or not isinstance(record["version_argv"], list)
            or not all(
                isinstance(item, str) and item for item in record["version_argv"]
            )
            or SHA256.fullmatch(record["sha256"]) is None
            or SHA256.fullmatch(record["source_archive_sha256"]) is None
            or record["target"] != "aarch64-unknown-linux-gnu"
            or record["path"] != f"bin/{record['name']}"
            or _sha256(root / record["path"]) != record["sha256"]
        ):
            raise RuntimePackContractError(
                "Linux arm64 Runtime Pack executable inventory is invalid"
            )
    return inventory


def write_provenance(
    root: str | Path,
    *,
    carrier_basename: str,
    version: str,
    platform: str,
    architecture: str,
    source_revision: str,
    terminal_revision: str,
    frame_revision: str,
) -> dict[str, Any]:
    payload_root = Path(root).resolve(strict=True)
    revisions = {
        "vibecrafted": _validate_revision(source_revision, field="source_revision"),
        "vc-terminal": _validate_revision(terminal_revision, field="terminal_revision"),
        "vc-frame": _validate_revision(frame_revision, field="frame_revision"),
    }
    if Path(carrier_basename).name != carrier_basename or not carrier_basename.endswith(
        ".tar.gz"
    ):
        raise RuntimePackContractError("carrier basename must be a .tar.gz basename")
    if not version or version != version.strip():
        raise RuntimePackContractError("Runtime Pack version is invalid")
    _runtime_installer_payload(payload_root)
    _runtime_foundations(payload_root)
    _source_provenance(payload_root, expected_revision=source_revision)
    if platform != f"{platform.rsplit('-', 1)[0]}-{architecture}":
        raise RuntimePackContractError(
            "Runtime Pack platform must be the canonical <os>-<architecture> target slug"
        )
    if platform == "linux-arm64" and architecture == "arm64":
        _linux_arm64_inventory(payload_root)
    provenance = {
        "schema": SCHEMA,
        "carrier_basename": carrier_basename,
        "version": version,
        "platform": platform,
        "architecture": architecture,
        "source_revisions": revisions,
        "payload": {
            "algorithm": "sha256",
            "files": _payload_files(payload_root),
        },
    }
    (payload_root / PROVENANCE_NAME).write_text(
        _canonical_json(provenance), encoding="utf-8"
    )
    return provenance


def verify_provenance(
    root: str | Path,
    *,
    carrier_basename: str,
    expected_source_revision: str | None = None,
    expected_terminal_revision: str | None = None,
    expected_frame_revision: str | None = None,
    expected_version: str | None = None,
    expected_platform: str | None = None,
    expected_architecture: str | None = None,
) -> dict[str, Any]:
    payload_root = Path(root).resolve(strict=True)
    path = payload_root / PROVENANCE_NAME
    try:
        raw = path.read_text(encoding="utf-8")
        provenance = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimePackContractError("Runtime Pack provenance is invalid") from exc
    required = {
        "schema",
        "carrier_basename",
        "version",
        "platform",
        "architecture",
        "source_revisions",
        "payload",
    }
    revisions = (
        provenance.get("source_revisions") if isinstance(provenance, dict) else None
    )
    payload = provenance.get("payload") if isinstance(provenance, dict) else None
    files = payload.get("files") if isinstance(payload, dict) else None
    if (
        not isinstance(provenance, dict)
        or set(provenance) != required
        or provenance.get("schema") != SCHEMA
        or raw != _canonical_json(provenance)
        or provenance.get("carrier_basename") != carrier_basename
        or not isinstance(provenance.get("version"), str)
        or not provenance["version"]
        or not isinstance(provenance.get("platform"), str)
        or not provenance["platform"]
        or not isinstance(provenance.get("architecture"), str)
        or not provenance["architecture"]
        or not isinstance(revisions, dict)
        or set(revisions) != {"vibecrafted", "vc-terminal", "vc-frame"}
        or any(
            not isinstance(value, str) or GIT_SHA.fullmatch(value) is None
            for value in revisions.values()
        )
        or not isinstance(payload, dict)
        or set(payload) != {"algorithm", "files"}
        or payload.get("algorithm") != "sha256"
        or not isinstance(files, list)
        or not files
    ):
        raise RuntimePackContractError(
            "Runtime Pack provenance violates the closed schema"
        )
    expected_revisions = {
        "vibecrafted": expected_source_revision,
        "vc-terminal": expected_terminal_revision,
        "vc-frame": expected_frame_revision,
    }
    for name, expected in expected_revisions.items():
        if expected is not None and revisions[name] != _validate_revision(
            expected, field=f"expected_{name}_revision"
        ):
            raise RuntimePackContractError(
                f"Runtime Pack {name} revision disagrees with the expected release tuple"
            )
    expected_identity = {
        "version": expected_version,
        "platform": expected_platform,
        "architecture": expected_architecture,
    }
    for field, expected in expected_identity.items():
        if expected is not None and provenance[field] != expected:
            raise RuntimePackContractError(
                f"Runtime Pack {field} disagrees with the selected release asset"
            )
    _runtime_installer_payload(payload_root)
    _runtime_foundations(payload_root)
    _source_provenance(payload_root, expected_revision=revisions["vibecrafted"])
    if provenance["platform"] != (
        f"{provenance['platform'].rsplit('-', 1)[0]}-{provenance['architecture']}"
    ):
        raise RuntimePackContractError(
            "Runtime Pack platform must be the canonical <os>-<architecture> target slug"
        )
    if (
        provenance["platform"] == "linux-arm64"
        and provenance["architecture"] == "arm64"
    ):
        _linux_arm64_inventory(payload_root)
    observed = _payload_files(payload_root)
    if files != observed:
        raise RuntimePackContractError(
            "Runtime Pack payload digests do not match provenance"
        )
    version = (payload_root / "VERSION").read_text(encoding="utf-8").strip()
    if version != provenance["version"]:
        raise RuntimePackContractError("Runtime Pack VERSION disagrees with provenance")
    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("write")
    verify = commands.add_parser("verify")
    refresh_foundations = commands.add_parser("refresh-foundations")
    refresh_foundations.add_argument("--root", type=Path, required=True)
    for command in (write, verify):
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--carrier-basename", required=True)
    write.add_argument("--version", required=True)
    write.add_argument("--platform", required=True)
    write.add_argument("--architecture", required=True)
    write.add_argument("--source-revision", required=True)
    write.add_argument("--terminal-revision", required=True)
    write.add_argument("--frame-revision", required=True)
    verify.add_argument("--expected-source-revision")
    verify.add_argument("--expected-terminal-revision")
    verify.add_argument("--expected-frame-revision")
    verify.add_argument("--expected-version")
    verify.add_argument("--expected-platform")
    verify.add_argument("--expected-architecture")
    args = parser.parse_args(argv)
    if args.command == "refresh-foundations":
        payload = refresh_runtime_foundations(args.root)
    elif args.command == "write":
        payload = write_provenance(
            args.root,
            carrier_basename=args.carrier_basename,
            version=args.version,
            platform=args.platform,
            architecture=args.architecture,
            source_revision=args.source_revision,
            terminal_revision=args.terminal_revision,
            frame_revision=args.frame_revision,
        )
    else:
        payload = verify_provenance(
            args.root,
            carrier_basename=args.carrier_basename,
            expected_source_revision=args.expected_source_revision,
            expected_terminal_revision=args.expected_terminal_revision,
            expected_frame_revision=args.expected_frame_revision,
            expected_version=args.expected_version,
            expected_platform=args.expected_platform,
            expected_architecture=args.expected_architecture,
        )
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
