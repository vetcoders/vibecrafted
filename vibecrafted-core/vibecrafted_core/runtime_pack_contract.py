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
SOURCE_PROVENANCE_SCHEMA = "vibecrafted.source-provenance.v2"
GIT_SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")


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


def _validate_revision(value: str, *, field: str) -> str:
    if GIT_SHA.fullmatch(value) is None:
        raise RuntimePackContractError(f"{field} must be a full Git revision")
    return value


def _payload_files(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
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
    _source_provenance(payload_root, expected_revision=source_revision)
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
    _source_provenance(payload_root, expected_revision=revisions["vibecrafted"])
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
    args = parser.parse_args(argv)
    if args.command == "write":
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
        )
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
