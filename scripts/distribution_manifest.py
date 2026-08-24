#!/usr/bin/env python3
"""Build and verify the complete Vibecrafted runtime payload.

The repository is a development surface. A distribution is an allowlisted
projection of it: required runtime paths must exist, development artifacts are
never copied, and runtime/skills have one physical package owner.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable
from pathlib import Path

SOURCE_PROVENANCE_FILE = "source-provenance.json"
SOURCE_PROVENANCE_SCHEMA = "vibecrafted.source-provenance.v2"
DISTRIBUTION_TREE_SCHEMA = "vibecrafted.distribution-tree.v1"
DISTRIBUTION_TREE_ALGORITHM = "sha256"
# Exact distribution-tree digest wire contract. Do not change this domain or the
# binary field layout without introducing a new schema version:
#   domain b"vibecrafted.distribution-tree.v1\0"
#   entry_count as unsigned 8-byte big-endian
#   entries sorted by raw UTF-8 relative-path bytes
#   kind byte d/f/l
#   unsigned 8-byte path length + path bytes
#   unsigned 4-byte canonical mode (directory 0755, regular file 0755 iff any
#   executable bit is set else 0644, symlink 0777)
#   unsigned 8-byte payload length + payload
# Directory payload is empty. File payload is unsigned 8-byte file size plus
# raw 32-byte SHA-256. Symlink payload is the raw UTF-8 link target.
DISTRIBUTION_TREE_DOMAIN = b"vibecrafted.distribution-tree.v1\0"
_OWNER_REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

REQUIRED_FILES = (
    "VERSION",
    "LICENSE",
    "README.md",
    "Makefile",
    "install.sh",
    "install.ps1",
    "install.toml",
    "scripts/distribution_manifest.py",
    "scripts/runtime_paths.py",
    "scripts/vetcoders_install.py",
    "scripts/vibecrafted",
    "scripts/verify-vibecrafted-product.sh",
    "vibecrafted-core/pyproject.toml",
    "vibecrafted-core/vibecrafted_core/VERSION",
    "vibecrafted-core/vibecrafted_core/deck/vibecrafted",
    "vibecrafted-core/vibecrafted_core/product_contract.py",
    "vibecrafted-core/vibecrafted_core/walkaround_runner.py",
    "vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json",
    "vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json",
    "vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub",
    "vibecrafted-mcp/pyproject.toml",
    "plugins/iterm2/pyproject.toml",
    "vibecrafted-app/Cargo.toml",
    "vibecrafted-app/Cargo.lock",
    "vibecrafted-server/Cargo.toml",
    "vibecrafted-server/Cargo.lock",
)

REQUIRED_DIRECTORIES = (
    "bin",
    "config",
    "docs",
    "plugins",
    "scripts/installer",
    "templates",
    "tools",
    "vibecrafted-app",
    "vibecrafted-core/vibecrafted_core/runtime",
    "vibecrafted-core/vibecrafted_core/skills",
    "vibecrafted-mcp",
    "vibecrafted-server",
    "vibecrafted-vm",
    "workflows",
)

# A directory name alone does not prove that its runtime survived packaging.
# Keep one stable, executable-or-documenting sentinel for every required
# surface so an empty subtree can never pass the complete-runtime gate.
REQUIRED_SURFACE_FILES = {
    "bin": "bin/vc-workflow",
    "config": "config/README.md",
    "docs": "docs/INSTALL.md",
    "plugins": "plugins/iterm2/README.md",
    "scripts/installer": "scripts/installer/pyproject.toml",
    "templates": "templates/hooks/install.sh",
    "tools": "tools/README.md",
    "vibecrafted-app": "vibecrafted-app/Cargo.toml",
    "vibecrafted-core/vibecrafted_core/runtime": (
        "vibecrafted-core/vibecrafted_core/runtime/README.md"
    ),
    "vibecrafted-core/vibecrafted_core/skills": (
        "vibecrafted-core/vibecrafted_core/skills/LIVING_TREE_RULE.md"
    ),
    "vibecrafted-mcp": "vibecrafted-mcp/pyproject.toml",
    "vibecrafted-server": "vibecrafted-server/Cargo.toml",
    "vibecrafted-vm": "vibecrafted-vm/Containerfile",
    "workflows": "workflows/MARBLES.md",
}

ALLOWED_TOP_LEVEL = frozenset(
    {
        "VERSION",
        "LICENSE",
        "README.md",
        "CHANGELOG.md",
        "SECURITY.md",
        "Makefile",
        "install.sh",
        "install.ps1",
        "install.toml",
        "runtime-manifest.json",
        SOURCE_PROVENANCE_FILE,
        "pyproject.toml",
        "plugin.json",
        "bin",
        "config",
        "docs",
        "plugins",
        "runtime",
        "scripts",
        "templates",
        "tools",
        "vibecrafted-app",
        "vibecrafted-core",
        "vibecrafted-mcp",
        "vibecrafted-server",
        "vibecrafted-vm",
        "workflows",
    }
)

FORBIDDEN_COMPONENTS = frozenset(
    {
        ".DS_Store",
        ".backup",
        ".build",
        ".cache",
        ".circleci",
        ".coverage",
        ".devcontainer",
        ".dockerignore",
        ".env",
        ".git",
        ".github",
        ".gitignore",
        ".gitlab",
        ".junie",
        ".legacy-state-agency",
        ".loctignore",
        ".loctree",
        ".mypy_cache",
        ".netrc",
        ".next",
        ".prettierignore",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "AGENTS.md",
        "Cargo.lock",
        "CONTRIBUTING.md",
        "DerivedData",
        "Pipfile.lock",
        "__pycache__",
        "__tests__",
        "build",
        "coverage.xml",
        "credentials.json",
        "dist",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "node_modules",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "reports",
        "secrets.json",
        "target",
        "test",
        "tests",
        "uv.lock",
        "yarn.lock",
    }
)

FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".swp", "~", ".pem", ".p12", ".pfx")
_SECRET_NAME_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
REQUIRED_LOCKFILES = frozenset(
    {
        "vibecrafted-app/Cargo.lock",
        "vibecrafted-server/Cargo.lock",
    }
)


class ManifestError(ValueError):
    """The requested payload cannot satisfy the distribution contract."""


def _parse_owner_repo_url(url: str) -> str | None:
    normalized = url.strip().rstrip("/").removesuffix(".git")
    if not normalized:
        return None
    path = (
        normalized.split(":", 1)[-1]
        if ":" in normalized and not normalized.startswith("http")
        else normalized
    )
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    if len(parts) < 2:
        return None
    owner_repo = f"{parts[-2]}/{parts[-1]}"
    return owner_repo if _OWNER_REPO_RE.fullmatch(owner_repo) else None


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    """Return the stable fields used to detect an in-flight path replacement."""
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _read_stable_regular_bytes(path: Path, *, label: str) -> tuple[bytes, int]:
    """Read one no-follow regular file and prove its path still names that inode."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ManifestError(f"cannot capture {label} {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ManifestError(f"{label} must be a regular file: {path}")
        if before.st_nlink != 1:
            raise ManifestError(f"{label} must not be hardlinked: {path}")
        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
        after = os.fstat(descriptor)
        try:
            path_after = path.lstat()
        except OSError as exc:
            raise ManifestError(f"{label} path changed during capture: {path}") from exc
        if (
            consumed != before.st_size
            or _stat_identity(before) != _stat_identity(after)
            or _stat_identity(after) != _stat_identity(path_after)
        ):
            raise ManifestError(f"{label} path changed during capture: {path}")
        return b"".join(chunks), stat.S_IMODE(after.st_mode)
    finally:
        os.close(descriptor)


def _canonical_provenance_bytes(provenance: dict[str, object]) -> bytes:
    return (
        json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")


def _closed_provenance_record(payload: object) -> dict[str, object]:
    """Validate and normalize one source-provenance v2 object."""
    required = {"schema", "owner_repo", "source_revision", "payload"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ManifestError(
            f"{SOURCE_PROVENANCE_FILE} does not satisfy the closed provenance schema"
        )
    owner_repo = payload.get("owner_repo")
    revision = payload.get("source_revision")
    tree = payload.get("payload")
    tree_required = {"schema", "algorithm", "tree_sha256", "entry_count"}
    if (
        payload.get("schema") != SOURCE_PROVENANCE_SCHEMA
        or not isinstance(owner_repo, str)
        or _OWNER_REPO_RE.fullmatch(owner_repo) is None
        or not isinstance(revision, str)
        or _GIT_SHA_RE.fullmatch(revision) is None
        or not isinstance(tree, dict)
        or set(tree) != tree_required
        or tree.get("schema") != DISTRIBUTION_TREE_SCHEMA
        or tree.get("algorithm") != DISTRIBUTION_TREE_ALGORITHM
        or not isinstance(tree.get("tree_sha256"), str)
        or _SHA256_RE.fullmatch(tree["tree_sha256"]) is None
        or not isinstance(tree.get("entry_count"), int)
        or isinstance(tree.get("entry_count"), bool)
        or tree["entry_count"] < 1
    ):
        raise ManifestError(
            f"{SOURCE_PROVENANCE_FILE} does not satisfy the closed provenance schema"
        )
    return {
        "schema": SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": owner_repo,
        "source_revision": revision,
        "payload": {
            "schema": DISTRIBUTION_TREE_SCHEMA,
            "algorithm": DISTRIBUTION_TREE_ALGORITHM,
            "tree_sha256": tree["tree_sha256"],
            "entry_count": tree["entry_count"],
        },
    }


def _load_source_provenance_with_bytes(
    root: str | Path,
) -> tuple[dict[str, object], bytes] | None:
    source_root = Path(root)
    path = source_root / SOURCE_PROVENANCE_FILE
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ManifestError(f"cannot inspect {SOURCE_PROVENANCE_FILE}: {exc}") from exc
    raw, mode = _read_stable_regular_bytes(path, label=SOURCE_PROVENANCE_FILE)
    if mode != 0o644:
        raise ManifestError(f"{SOURCE_PROVENANCE_FILE} must have canonical mode 0644")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"invalid {SOURCE_PROVENANCE_FILE}: {exc}") from exc
    provenance = _closed_provenance_record(parsed)
    if raw != _canonical_provenance_bytes(provenance):
        raise ManifestError(f"{SOURCE_PROVENANCE_FILE} is not canonical JSON")
    return provenance, raw


def load_source_provenance(root: str | Path) -> dict[str, object] | None:
    """Load the exact, canonical v2 carrier from an extracted release payload."""
    loaded = _load_source_provenance_with_bytes(root)
    return loaded[0] if loaded is not None else None


def _git_output(root: Path, *arguments: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _git_paths(root: Path, *arguments: str) -> set[Path]:
    """Return NUL-delimited Git paths, failing closed when Git cannot prove them."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        detail = (
            exc.stderr.decode("utf-8", "replace").strip()
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr
            else str(exc)
        )
        raise ManifestError(
            f"cannot verify archive source against Git: {detail}"
        ) from exc
    return {
        Path(os.fsdecode(raw_path))
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    }


def _git_bytes(root: Path, *arguments: str) -> bytes:
    """Return raw Git output or fail closed with bounded diagnostic detail."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        detail = (
            exc.stderr.decode("utf-8", "replace").strip()
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr
            else str(exc)
        )
        raise ManifestError(
            f"cannot verify source provenance against Git: {detail}"
        ) from exc
    return result.stdout


def _git_tree_entries(root: Path, revision: str) -> dict[Path, tuple[str, str, str]]:
    """Return included ``path -> (mode, type, object id)`` entries at revision."""
    entries: dict[Path, tuple[str, str, str]] = {}
    for raw_entry in _git_bytes(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        revision,
        "--",
    ).split(b"\0"):
        if not raw_entry:
            continue
        try:
            raw_header, raw_path = raw_entry.split(b"\t", 1)
            raw_mode, raw_type, raw_oid = raw_header.split(b" ", 2)
        except ValueError as exc:
            raise ManifestError("Git returned a malformed source tree entry") from exc
        relative = Path(os.fsdecode(raw_path))
        if relative == Path(SOURCE_PROVENANCE_FILE) or not path_is_included(relative):
            continue
        entries[relative] = (
            raw_mode.decode("ascii"),
            raw_type.decode("ascii"),
            raw_oid.decode("ascii"),
        )
    return entries


def _git_object_format(root: Path) -> str:
    algorithm = _git_output(root, "rev-parse", "--show-object-format")
    if algorithm not in {"sha1", "sha256"}:
        raise ManifestError(f"unsupported Git object format: {algorithm or 'unknown'}")
    return algorithm


def _git_blob_hasher(algorithm: str, size: int):
    digest = hashlib.new(algorithm)
    digest.update(f"blob {size}\0".encode("ascii"))
    return digest


def _git_blob_oid_from_bytes(raw: bytes, algorithm: str) -> str:
    digest = _git_blob_hasher(algorithm, len(raw))
    digest.update(raw)
    return digest.hexdigest()


def _git_blob_oid_from_symlink(path: Path, algorithm: str) -> str:
    """Hash one stable symlink target as a Git blob."""
    before = path.lstat()
    if not stat.S_ISLNK(before.st_mode):
        raise ManifestError(f"staged payload path is not a symlink: {path}")
    raw = os.fsencode(os.readlink(path))
    after = path.lstat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_mode,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_mode,
    ):
        raise ManifestError(f"staged payload symlink changed during capture: {path}")
    return _git_blob_oid_from_bytes(raw, algorithm)


def _git_blob_oid_from_file(path: Path, algorithm: str) -> tuple[str, os.stat_result]:
    """Hash one no-follow, stable regular-file snapshot as a Git blob."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ManifestError(
            f"cannot capture staged payload file {path}: {exc}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ManifestError(f"staged payload path is not a regular file: {path}")
        digest = _git_blob_hasher(algorithm, before.st_size)
        consumed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            consumed += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        try:
            path_after = path.lstat()
        except OSError as exc:
            raise ManifestError(
                f"staged payload file path changed during capture: {path}"
            ) from exc
        if consumed != before.st_size or (
            _stat_identity(before) != _stat_identity(after)
            or _stat_identity(after) != _stat_identity(path_after)
        ):
            raise ManifestError(f"staged payload file changed during capture: {path}")
        return digest.hexdigest(), after
    finally:
        os.close(descriptor)


def _expected_git_payload(
    root: Path, revision: str
) -> tuple[dict[Path, tuple[str, str, str]], set[Path]]:
    entries = _git_tree_entries(root, revision)
    directories: set[Path] = set()
    for relative in entries:
        directories.update(parent for parent in relative.parents if parent != Path("."))
    return entries, directories


def _materialize_git_payload(root: Path, destination: Path, revision: str) -> None:
    """Materialize the allowlisted payload from immutable Git objects.

    An exact checkout is an identity provider, not an archive input directory.
    Reading the committed tree prevents ignored or untracked local artifacts from
    silently joining a release while still allowing the worktree drift guard to
    reject modifications to tracked payload paths.
    """
    entries, directories = _expected_git_payload(root, revision)
    for relative in sorted(
        directories, key=lambda path: (len(path.parts), path.as_posix())
    ):
        path = destination / relative
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o755)
    for relative, (mode, object_type, object_id) in sorted(entries.items()):
        if object_type != "blob" or mode not in {"100644", "100755", "120000"}:
            raise ManifestError(
                f"unsupported Git payload entry: {relative.as_posix()}:{mode}:{object_type}"
            )
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = _git_bytes(root, "cat-file", "blob", object_id)
        if mode == "120000":
            try:
                target = raw.decode("utf-8")
            except UnicodeError as exc:
                raise ManifestError(
                    f"symlink target is not UTF-8: {relative.as_posix()}"
                ) from exc
            if error := _symlink_target_error(relative, target):
                raise ManifestError(error)
            path.symlink_to(target)
        else:
            path.write_bytes(raw)
            path.chmod(0o755 if mode == "100755" else 0o644)


def _distribution_tree_record_from_git(root: Path, revision: str) -> dict[str, object]:
    """Derive the payload identity from immutable Git tree/blob objects."""
    entries, directories = _expected_git_payload(root, revision)
    captured: list[tuple[bytes, bytes, int, bytes]] = []
    for relative in directories:
        path_bytes = _canonical_relative_path_bytes(relative)
        captured.append((path_bytes, b"d", 0o755, b""))
    for relative, (mode, object_type, object_id) in entries.items():
        path_bytes = _canonical_relative_path_bytes(relative)
        if object_type != "blob" or mode not in {"100644", "100755", "120000"}:
            raise ManifestError(
                f"unsupported Git payload entry: {relative.as_posix()}:{mode}:{object_type}"
            )
        raw = _git_bytes(root, "cat-file", "blob", object_id)
        if mode == "120000":
            try:
                target = raw.decode("utf-8")
            except UnicodeError as exc:
                raise ManifestError(
                    f"symlink target is not UTF-8: {relative.as_posix()}"
                ) from exc
            if error := _symlink_target_error(relative, target):
                raise ManifestError(error)
            captured.append((path_bytes, b"l", 0o777, raw))
        else:
            payload = len(raw).to_bytes(8, "big") + hashlib.sha256(raw).digest()
            captured.append(
                (path_bytes, b"f", 0o755 if mode == "100755" else 0o644, payload)
            )
    return _distribution_tree_record_from_entries(captured)


def _assert_staged_payload_matches_git_revision(
    git_root: Path,
    payload_root: Path,
    revision: str,
) -> None:
    """Prove the copied allowlisted payload has HEAD-exact bytes, types, and modes."""
    entries, directories = _expected_git_payload(git_root, revision)
    expected_paths = set(entries) | directories
    actual_paths = {
        path.relative_to(payload_root)
        for path in _walk_entries(payload_root)
        if path.relative_to(payload_root) != Path(SOURCE_PROVENANCE_FILE)
        and path_is_included(path.relative_to(payload_root))
    }
    errors = [
        *(
            f"missing:{path.as_posix()}"
            for path in sorted(expected_paths - actual_paths)
        ),
        *(f"extra:{path.as_posix()}" for path in sorted(actual_paths - expected_paths)),
    ]
    algorithm = _git_object_format(git_root)
    for relative in sorted(expected_paths & actual_paths):
        path = payload_root / relative
        if relative in directories:
            if path.is_symlink() or not path.is_dir():
                errors.append(f"type:{relative.as_posix()}")
            continue
        mode, object_type, expected_oid = entries[relative]
        if object_type != "blob" or mode not in {"100644", "100755", "120000"}:
            errors.append(
                f"unsupported-git-entry:{relative.as_posix()}:{mode}:{object_type}"
            )
            continue
        if mode == "120000":
            if not path.is_symlink():
                errors.append(f"type:{relative.as_posix()}")
                continue
            actual_oid = _git_blob_oid_from_symlink(path, algorithm)
        else:
            if path.is_symlink() or not path.is_file():
                errors.append(f"type:{relative.as_posix()}")
                continue
            actual_oid, metadata = _git_blob_oid_from_file(path, algorithm)
            if bool(metadata.st_mode & 0o111) != (mode == "100755"):
                errors.append(f"mode:{relative.as_posix()}")
        if actual_oid != expected_oid:
            errors.append(f"bytes:{relative.as_posix()}")
    if errors:
        raise ManifestError(
            "staged payload differs from claimed Git revision "
            f"{revision}: " + ", ".join(sorted(set(errors)))
        )


def _assert_git_payload_matches_revision(root: Path, revision: str) -> None:
    """Reject included source paths whose Git/index/worktree state is not ``revision``.

    Extracted release sources have no exact Git root and intentionally bypass this
    check: their closed provenance carrier remains the authority. For a checkout,
    however, an archive may claim its HEAD only when every path the distribution
    allowlist would copy is unchanged at HEAD. Development-only excluded paths do
    not participate in the archive and therefore do not block it.
    """
    git_root = _git_output(root, "rev-parse", "--show-toplevel")
    if not git_root or Path(git_root).resolve(strict=False) != root.resolve(
        strict=False
    ):
        return

    head_revision = _git_output(root, "rev-parse", "HEAD").lower()
    if head_revision != revision:
        raise ManifestError(
            "archive source revision does not match the exact Git root HEAD"
        )

    changed_paths: set[Path] = set()
    for arguments in (
        (
            "diff",
            "--cached",
            "--no-ext-diff",
            "--no-renames",
            "--name-only",
            "-z",
            revision,
            "--",
        ),
        (
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--name-only",
            "-z",
            "--",
        ),
    ):
        changed_paths.update(_git_paths(root, *arguments))

    included_drift = sorted(
        {
            path.as_posix()
            for path in changed_paths
            if path.parts and path_is_included(path)
        }
    )
    if included_drift:
        raise ManifestError(
            "archive source differs from claimed Git revision "
            f"{revision}; included path(s): " + ", ".join(included_drift)
        )


def _is_exact_git_root(root: Path) -> bool:
    git_root = _git_output(root, "rev-parse", "--show-toplevel")
    return bool(
        git_root and Path(git_root).resolve(strict=False) == root.resolve(strict=False)
    )


def resolve_source_provenance(
    root: Path,
    *,
    owner_repo: str | None,
    source_revision: str | None,
) -> dict[str, object]:
    """Resolve one closed identity; unbound owner/SHA pairs are never authority."""
    source_root = Path(root).resolve()
    if (owner_repo is None) != (source_revision is None):
        raise ManifestError("explicit source provenance must provide an atomic pair")
    if owner_repo is not None and (not owner_repo or not source_revision):
        raise ManifestError("explicit source provenance is not canonical")
    explicit = (
        (owner_repo, source_revision)
        if owner_repo is not None and source_revision is not None
        else ("", "")
    )
    environment = (
        os.environ.get("VIBECRAFTED_SOURCE_OWNER_REPO", ""),
        os.environ.get("VIBECRAFTED_SOURCE_REVISION", ""),
    )
    git_root = _git_output(source_root, "rev-parse", "--show-toplevel")
    if git_root:
        resolved_git_root = Path(git_root).resolve(strict=False)
        if resolved_git_root != source_root:
            raise ManifestError(
                "source root is nested inside an enclosing Git worktree; "
                f"expected the exact Git root {resolved_git_root}"
            )
        git_revision = _git_output(source_root, "rev-parse", "HEAD").lower()
        git_owner = (
            _parse_owner_repo_url(
                _git_output(source_root, "remote", "get-url", "origin")
            )
            or ""
        )
        git = (git_owner, git_revision) if git_owner else ("", "")
    else:
        git_revision = ""
        git = ("", "")
    inherited = load_source_provenance(source_root)
    carrier = (
        (inherited["owner_repo"], inherited["source_revision"])
        if inherited is not None
        else ("", "")
    )
    candidates = []
    for label, pair in (
        ("explicit", explicit),
        ("environment", environment),
        ("git", git),
        ("carrier", carrier),
    ):
        if bool(pair[0]) != bool(pair[1]):
            raise ManifestError(
                f"{label} source provenance must provide an atomic pair"
            )
        if pair[0]:
            candidates.append((label, pair))
    if not candidates:
        raise ManifestError("archive source provenance is unavailable")
    owner, revision = candidates[0][1]
    conflicts = [label for label, pair in candidates[1:] if pair != (owner, revision)]
    if conflicts:
        raise ManifestError(
            "source provenance providers disagree: "
            + ", ".join([candidates[0][0], *conflicts])
        )
    if _OWNER_REPO_RE.fullmatch(owner) is None:
        raise ManifestError("archive owner_repo must be an exact owner/repository slug")
    if _GIT_SHA_RE.fullmatch(revision) is None:
        raise ManifestError("archive source_revision must be a full lowercase Git SHA")
    if git_root:
        if git_revision != revision:
            raise ManifestError(
                "archive source revision does not match the exact Git root HEAD"
            )
        _assert_git_payload_matches_revision(source_root, revision)
        tree = _distribution_tree_record_from_git(source_root, revision)
        if inherited is not None and inherited["payload"] != tree:
            raise ManifestError(
                f"{SOURCE_PROVENANCE_FILE} payload does not match the Git revision tree"
            )
    else:
        if inherited is None:
            raise ManifestError(
                "non-Git source requires an existing source-provenance v2 carrier"
            )
        tree = _distribution_tree_record(source_root)
        if inherited["payload"] != tree:
            raise ManifestError(
                f"{SOURCE_PROVENANCE_FILE} payload digest does not match the source tree"
            )
    return {
        "schema": SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": owner,
        "source_revision": revision,
        "payload": tree,
    }


def assert_source_payload_matches_provenance(
    root: Path,
    *,
    owner_repo: str | None,
    source_revision: str | None,
    payload_root: Path | None = None,
) -> dict[str, object]:
    """Resolve one provenance pair and prove an exact Git-root payload matches it.

    Git checkouts must be clean for every included distribution path at the
    claimed HEAD. When ``payload_root`` is provided, its already-copied inventory,
    types, executable bits, symlink targets, and raw blob bytes must also match
    that commit. Extracted sources intentionally retain the carrier-only path:
    provider agreement and the closed carrier schema remain their proof.
    """
    source_root = Path(root).resolve()
    provenance = resolve_source_provenance(
        source_root,
        owner_repo=owner_repo,
        source_revision=source_revision,
    )
    if payload_root is not None:
        staged_root = Path(payload_root)
        if _is_exact_git_root(source_root):
            _assert_staged_payload_matches_git_revision(
                source_root,
                staged_root,
                str(provenance["source_revision"]),
            )
        staged_tree = _distribution_tree_record(staged_root)
        if staged_tree != provenance["payload"]:
            raise ManifestError(
                "staged payload digest does not match source provenance"
            )
    return provenance


def _relative_path(value: str | Path) -> Path:
    """Normalize ``value`` to a relative Path, rejecting absolute or ``..`` paths."""
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ManifestError(f"unsafe relative path: {value}")
    return relative


def _component_is_secret_env(name: str) -> bool:
    """Return True when a path component is a live-credential env file.

    `.env` and every `.env.*` variant carry live credentials; only the
    committed `*.example` templates are distributable.
    """
    if name == ".env":
        return True
    return name.startswith(".env.") and not name.endswith(".example")


def _component_looks_like_secret(name: str) -> bool:
    """Return True when a path component looks like a private key or credential file."""
    lowered = name.lower()
    if lowered.endswith((".pub", ".example")):
        return False
    return lowered.startswith(_SECRET_NAME_PREFIXES)


def path_is_forbidden(relative: str | Path) -> bool:
    """Return True when a relative path matches a forbidden component/suffix rule."""
    relative_path = _relative_path(relative)
    if not relative_path.parts:
        return True
    if relative_path.as_posix() in REQUIRED_LOCKFILES:
        return False
    return any(
        part in FORBIDDEN_COMPONENTS
        or _component_is_secret_env(part)
        or _component_looks_like_secret(part)
        for part in relative_path.parts
    ) or relative_path.name.endswith(FORBIDDEN_SUFFIXES)


def path_is_included(relative: str | Path) -> bool:
    """Return True when a relative path is under an allowed top-level and not forbidden."""
    relative_path = _relative_path(relative)
    return bool(
        relative_path.parts
        and relative_path.parts[0] in ALLOWED_TOP_LEVEL
        and not path_is_forbidden(relative_path)
    )


def _canonical_relative_path_bytes(relative: Path) -> bytes:
    """Return the portable raw UTF-8 spelling used by the tree digest."""
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ManifestError(f"noncanonical payload path: {relative}")
    rendered = relative.as_posix()
    if (
        rendered in {"", "."}
        or rendered.startswith("/")
        or "//" in rendered
        or "\\" in rendered
        or "\x00" in rendered
    ):
        raise ManifestError(f"noncanonical payload path: {rendered!r}")
    try:
        encoded = rendered.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise ManifestError(f"payload path is not UTF-8: {rendered!r}") from exc
    if encoded.decode("utf-8") != rendered:
        raise ManifestError(f"noncanonical payload path: {rendered!r}")
    return encoded


def _symlink_target_error(relative: Path, raw_target: str) -> str | None:
    """Validate a link target lexically, without trusting the live filesystem."""
    try:
        raw_target.encode("utf-8", "strict")
    except UnicodeError:
        return f"symlink target is not UTF-8: {relative}"
    target = Path(raw_target)
    if not raw_target or "\x00" in raw_target or target.is_absolute():
        return f"symlink escapes payload: {relative} -> {raw_target}"
    collapsed: list[str] = list(relative.parent.parts)
    for component in target.parts:
        if component in {"", "."}:
            continue
        if component == "..":
            if not collapsed:
                return f"symlink escapes payload: {relative} -> {raw_target}"
            collapsed.pop()
        else:
            collapsed.append(component)
    if not collapsed or not path_is_included(Path(*collapsed)):
        return f"symlink targets excluded path: {relative} -> {raw_target}"
    return None


def _distribution_tree_record_from_entries(
    entries: Iterable[tuple[bytes, bytes, int, bytes]],
) -> dict[str, object]:
    """Hash captured entries using the exact distribution-tree v1 wire format."""
    ordered = sorted(entries, key=lambda entry: entry[0])
    if not ordered:
        raise ManifestError("distribution payload must contain at least one entry")
    paths = [entry[0] for entry in ordered]
    if len(paths) != len(set(paths)):
        raise ManifestError("distribution payload contains duplicate paths")
    digest = hashlib.sha256()
    digest.update(DISTRIBUTION_TREE_DOMAIN)
    digest.update(len(ordered).to_bytes(8, "big"))
    for path_bytes, kind, mode, payload in ordered:
        if kind not in {b"d", b"f", b"l"}:
            raise ManifestError(f"unsupported distribution entry kind: {kind!r}")
        if (
            (kind == b"d" and (mode != 0o755 or payload))
            or (kind == b"f" and (mode not in {0o644, 0o755} or len(payload) != 40))
            or (kind == b"l" and mode != 0o777)
        ):
            raise ManifestError(
                f"noncanonical distribution entry: {path_bytes!r}:{kind!r}:{mode:o}"
            )
        digest.update(kind)
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(mode.to_bytes(4, "big"))
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return {
        "schema": DISTRIBUTION_TREE_SCHEMA,
        "algorithm": DISTRIBUTION_TREE_ALGORITHM,
        "tree_sha256": digest.hexdigest(),
        "entry_count": len(ordered),
    }


def _sha256_stable_regular_file(path: Path) -> tuple[int, bytes, int]:
    """Hash a no-follow file while proving fd and final path identity."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ManifestError(f"cannot capture payload file {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ManifestError(f"payload path is not a regular file: {path}")
        if before.st_nlink != 1:
            raise ManifestError(f"payload file must not be hardlinked: {path}")
        digest = hashlib.sha256()
        consumed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            consumed += len(chunk)
        after = os.fstat(descriptor)
        try:
            path_after = path.lstat()
        except OSError as exc:
            raise ManifestError(
                f"payload file path changed during capture: {path}"
            ) from exc
        if (
            consumed != before.st_size
            or _stat_identity(before) != _stat_identity(after)
            or _stat_identity(after) != _stat_identity(path_after)
        ):
            raise ManifestError(f"payload file path changed during capture: {path}")
        return consumed, digest.digest(), stat.S_IMODE(after.st_mode)
    finally:
        os.close(descriptor)


def _distribution_tree_record(root: Path) -> dict[str, object]:
    """Capture a closed payload tree, excluding only its recursive carrier."""
    payload_root = Path(root)
    captured: list[tuple[bytes, bytes, int, bytes]] = []
    for path in _walk_entries(payload_root):
        relative = path.relative_to(payload_root)
        if relative == Path(SOURCE_PROVENANCE_FILE):
            continue
        if path_is_forbidden(relative):
            raise ManifestError(f"forbidden path in distribution tree: {relative}")
        if not path_is_included(relative):
            raise ManifestError(f"unexpected path in distribution tree: {relative}")
        path_bytes = _canonical_relative_path_bytes(relative)
        try:
            before = path.lstat()
        except OSError as exc:
            raise ManifestError(
                f"cannot inspect payload path {relative}: {exc}"
            ) from exc
        if stat.S_ISLNK(before.st_mode):
            raw_target = os.readlink(path)
            try:
                target_bytes = raw_target.encode("utf-8", "strict")
            except UnicodeError as exc:
                raise ManifestError(f"symlink target is not UTF-8: {relative}") from exc
            after = path.lstat()
            if _stat_identity(before) != _stat_identity(after):
                raise ManifestError(
                    f"payload symlink changed during capture: {relative}"
                )
            if error := _symlink_target_error(relative, raw_target):
                raise ManifestError(error)
            captured.append((path_bytes, b"l", 0o777, target_bytes))
        elif stat.S_ISDIR(before.st_mode):
            after = path.lstat()
            if before.st_dev != after.st_dev or before.st_ino != after.st_ino:
                raise ManifestError(
                    f"payload directory changed during capture: {relative}"
                )
            captured.append((path_bytes, b"d", 0o755, b""))
        elif stat.S_ISREG(before.st_mode):
            size, raw_digest, mode = _sha256_stable_regular_file(path)
            file_payload = size.to_bytes(8, "big") + raw_digest
            captured.append(
                (path_bytes, b"f", 0o755 if mode & 0o111 else 0o644, file_payload)
            )
        else:
            raise ManifestError(f"special payload path is forbidden: {relative}")
    return _distribution_tree_record_from_entries(captured)


def _symlink_error(root: Path, path: Path) -> str | None:
    """Return a description of why a symlink is unsafe, or None if it is fine.

    Rejects absolute targets, targets resolving outside ``root``, and targets
    landing on an excluded path.
    """
    if not path.is_symlink():
        return None
    raw_target = os.readlink(path)
    target = Path(raw_target)
    relative = path.relative_to(root)
    if target.is_absolute():
        return f"symlink escapes payload: {relative} -> {raw_target}"
    resolved_root = root.resolve()
    resolved_target = (path.parent / target).resolve(strict=False)
    try:
        target_relative = resolved_target.relative_to(resolved_root)
    except ValueError:
        return f"symlink escapes payload: {relative} -> {raw_target}"
    if not path_is_included(target_relative):
        return f"symlink targets excluded path: {relative} -> {raw_target}"
    return None


def _required_candidate(root: Path, relative: str) -> Path:
    """Resolve a required physical path from its sole canonical owner."""
    return root / relative


def _required_errors(root: Path) -> list[str]:
    """Collect human-readable errors for every missing required file/dir/surface."""
    errors = []
    for relative in REQUIRED_FILES:
        candidate = _required_candidate(root, relative)
        if not candidate.is_file():
            errors.append(f"missing required path: {relative}")
    for relative in REQUIRED_DIRECTORIES:
        candidate = _required_candidate(root, relative)
        if not candidate.is_dir():
            errors.append(f"missing required path: {relative}")
    for surface, relative in REQUIRED_SURFACE_FILES.items():
        candidate = _required_candidate(root, relative)
        if not candidate.is_file():
            errors.append(f"missing required runtime content: {surface} -> {relative}")
    return errors


def _walk_entries(root: Path) -> Iterable[Path]:
    """Yield every directory then file under root, sorted, pruning forbidden dirs in-place."""
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            yield current_path / name
        for name in file_names:
            yield current_path / name
        directory_names[:] = [
            name
            for name in directory_names
            if not path_is_forbidden((current_path / name).relative_to(root))
        ]


def validate_payload(
    root: str | Path,
    *,
    require_source_provenance: bool = False,
    expected_owner_repo: str | None = None,
    expected_source_revision: str | None = None,
) -> None:
    """Raise ManifestError with every violation found in an already-staged payload."""
    payload_root = Path(root)
    if not payload_root.is_dir():
        raise ManifestError(f"payload root is not a directory: {payload_root}")

    errors = _required_errors(payload_root)
    if (expected_owner_repo is None) != (expected_source_revision is None):
        errors.append("expected source provenance must provide an atomic pair")
    elif expected_owner_repo is not None and (
        _OWNER_REPO_RE.fullmatch(expected_owner_repo) is None
        or _GIT_SHA_RE.fullmatch(expected_source_revision or "") is None
    ):
        errors.append("expected source provenance is not canonical")
    try:
        provenance = load_source_provenance(payload_root)
    except ManifestError as exc:
        errors.append(str(exc))
        provenance = None
    if require_source_provenance and provenance is None:
        errors.append(f"missing required path: {SOURCE_PROVENANCE_FILE}")
    if provenance is not None and expected_owner_repo is not None:
        expected = (expected_owner_repo, expected_source_revision or "")
        actual = (provenance["owner_repo"], provenance["source_revision"])
        if actual != expected:
            errors.append(
                "source provenance does not match the expected owner/revision"
            )
    if provenance is not None:
        try:
            tree = _distribution_tree_record(payload_root)
        except ManifestError as exc:
            errors.append(str(exc))
        else:
            if tree != provenance["payload"]:
                errors.append(
                    f"{SOURCE_PROVENANCE_FILE} payload digest does not match the payload tree"
                )
    for path in _walk_entries(payload_root):
        relative = path.relative_to(payload_root)
        if path_is_forbidden(relative):
            errors.append(f"forbidden path: {relative}")
            continue
        if relative.parts[0] not in ALLOWED_TOP_LEVEL:
            errors.append(f"unexpected top-level path: {relative}")
            continue
        if error := _symlink_error(payload_root, path):
            errors.append(error)

    if errors:
        raise ManifestError("\n".join(sorted(set(errors))))


def _validate_source(root: Path) -> None:
    """Raise ManifestError for missing required paths or unsafe symlinks in a source tree.

    Unlike ``validate_payload`` this only checks included paths and required
    surfaces at their physical canonical package paths — it does not flag
    paths as forbidden, since the source tree legitimately contains excluded content.
    """
    errors = _required_errors(root)
    try:
        load_source_provenance(root)
    except ManifestError as exc:
        errors.append(str(exc))
    for path in _walk_entries(root):
        relative = path.relative_to(root)
        if not path_is_included(relative):
            continue
        if error := _symlink_error(root, path):
            errors.append(error)
    if errors:
        raise ManifestError("\n".join(sorted(set(errors))))


def _remove_path(path: Path) -> None:
    """Delete a path regardless of whether it is a symlink, file, or directory tree."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _resolved_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path)).resolve(strict=False)


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _assert_paths_do_not_overlap(
    source_root: Path, target: Path, *, target_label: str
) -> None:
    """Reject equal/ancestor/descendant identities, including symlink aliases."""
    source_identity = source_root.resolve(strict=True)
    target_identity = _resolved_absolute(target)
    if _path_contains(source_identity, target_identity) or _path_contains(
        target_identity, source_identity
    ):
        raise ManifestError(
            f"source and {target_label} overlap: {source_identity} <-> {target_identity}"
        )


def _copy_regular_file(source: Path, destination: Path) -> None:
    """Copy bytes from one no-follow fd and prove the source path stayed attached."""
    source_flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    destination_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        source_fd = os.open(source, source_flags)
    except OSError as exc:
        raise ManifestError(f"cannot open source payload file {source}: {exc}") from exc
    destination_fd: int | None = None
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ManifestError(f"source payload path is not a regular file: {source}")
        if before.st_nlink != 1:
            raise ManifestError(f"source payload file must not be hardlinked: {source}")
        if destination.exists() or destination.is_symlink():
            _remove_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination_fd = os.open(destination, destination_flags, 0o600)
        consumed = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            consumed += len(chunk)
            pending = memoryview(chunk)
            while pending:
                written = os.write(destination_fd, pending)
                if written <= 0:
                    raise ManifestError(
                        f"short write while copying payload file: {source}"
                    )
                pending = pending[written:]
        canonical_mode = 0o755 if before.st_mode & 0o111 else 0o644
        os.fchmod(destination_fd, canonical_mode)
        after = os.fstat(source_fd)
        try:
            path_after = source.lstat()
        except OSError as exc:
            raise ManifestError(
                f"source payload file path changed during copy: {source}"
            ) from exc
        if (
            consumed != before.st_size
            or _stat_identity(before) != _stat_identity(after)
            or _stat_identity(after) != _stat_identity(path_after)
        ):
            raise ManifestError(
                f"source payload file path changed during copy: {source}"
            )
    except Exception:
        if destination_fd is not None:
            os.close(destination_fd)
            destination_fd = None
        if destination.exists() or destination.is_symlink():
            _remove_path(destination)
        raise
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)


def _copy_included(
    source_root: Path,
    source: Path,
    destination: Path,
    *,
    preserve_empty_directories: bool = False,
) -> None:
    """Recursively copy source to destination, skipping anything path_is_included() excludes.

    Symlinks are re-created (after a safety check) rather than followed;
    directories are copied with their own recursive fan-out.
    """
    relative = source.relative_to(source_root)
    if not path_is_included(relative):
        return
    try:
        before = source.lstat()
    except OSError as exc:
        raise ManifestError(
            f"cannot inspect source payload path {source}: {exc}"
        ) from exc
    if stat.S_ISLNK(before.st_mode):
        if error := _symlink_error(source_root, source):
            raise ManifestError(error)
        raw_target = os.readlink(source)
        after = source.lstat()
        if _stat_identity(before) != _stat_identity(after):
            raise ManifestError(f"source payload symlink changed during copy: {source}")
        if destination.exists() or destination.is_symlink():
            _remove_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(raw_target)
        return
    if stat.S_ISDIR(before.st_mode):
        destination.mkdir(parents=True, exist_ok=True)
        destination.chmod(0o755)
        try:
            children = sorted(
                source.iterdir(),
                key=lambda item: _canonical_relative_path_bytes(
                    item.relative_to(source_root)
                ),
            )
        except OSError as exc:
            raise ManifestError(
                f"cannot enumerate source payload path {source}: {exc}"
            ) from exc
        for child in children:
            _copy_included(
                source_root,
                child,
                destination / child.name,
                preserve_empty_directories=preserve_empty_directories,
            )
        after = source.lstat()
        if before.st_dev != after.st_dev or before.st_ino != after.st_ino:
            raise ManifestError(
                f"source payload directory changed during copy: {source}"
            )
        if not preserve_empty_directories:
            try:
                destination.rmdir()
            except OSError:
                pass
        return
    if stat.S_ISREG(before.st_mode):
        _copy_regular_file(source, destination)
        return
    raise ManifestError(f"special source payload path is forbidden: {relative}")


def _publish_staged_directory(candidate: Path, destination: Path) -> None:
    """Publish a complete sibling candidate, rolling back an existing target."""
    if not destination.exists() and not destination.is_symlink():
        os.replace(candidate, destination)
        return
    backup = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.previous-", dir=destination.parent
        )
    )
    backup.rmdir()
    os.replace(destination, backup)
    try:
        os.replace(candidate, destination)
    except Exception:
        os.replace(backup, destination)
        raise
    try:
        _remove_path(backup)
    except OSError:
        # Publication has already committed atomically. An inability to remove
        # the private rollback copy is cleanup debt, not a failed publication;
        # surfacing failure here would falsely claim the destination was
        # preserved even though it now names the accepted candidate.
        pass


def _stage_payload_into(
    source_root: Path,
    destination_root: Path,
    *,
    provenance: dict[str, object] | None,
    inherited_carrier_bytes: bytes | None,
    require_source_provenance: bool,
) -> None:
    destination_root.mkdir(parents=True, exist_ok=True)
    destination_root.chmod(0o755)
    if provenance is not None and _is_exact_git_root(source_root):
        _materialize_git_payload(
            source_root,
            destination_root,
            str(provenance["source_revision"]),
        )
    else:
        for item in sorted(
            source_root.iterdir(),
            key=lambda entry: _canonical_relative_path_bytes(Path(entry.name)),
        ):
            if provenance is not None and item.name == SOURCE_PROVENANCE_FILE:
                continue
            _copy_included(
                source_root,
                item,
                destination_root / item.name,
                preserve_empty_directories=inherited_carrier_bytes is not None,
            )
    if provenance is not None:
        assert_source_payload_matches_provenance(
            source_root,
            owner_repo=str(provenance["owner_repo"]),
            source_revision=str(provenance["source_revision"]),
            payload_root=destination_root,
        )
        carrier_bytes = (
            inherited_carrier_bytes
            if inherited_carrier_bytes is not None
            else _canonical_provenance_bytes(provenance)
        )
        if carrier_bytes != _canonical_provenance_bytes(provenance):
            raise ManifestError(
                "captured source provenance carrier changed before copy"
            )
        provenance_path = destination_root / SOURCE_PROVENANCE_FILE
        provenance_path.write_bytes(carrier_bytes)
        provenance_path.chmod(0o644)
    validate_payload(
        destination_root,
        require_source_provenance=require_source_provenance,
        expected_owner_repo=(str(provenance["owner_repo"]) if provenance else None),
        expected_source_revision=(
            str(provenance["source_revision"]) if provenance else None
        ),
    )


def stage_payload(
    source: str | Path,
    destination: str | Path,
    *,
    mirror: bool = False,
    owner_repo: str | None = None,
    source_revision: str | None = None,
    require_source_provenance: bool = False,
) -> dict[str, object] | None:
    """Copy the allowlisted subset of source into destination and validate the result.

    When ``mirror`` is set the destination is wiped first. The whole physical
    canonical package payload is then re-validated without repo-root aliases.
    """
    source_root = Path(source).resolve(strict=False)
    destination_root = Path(os.path.abspath(destination))
    if not source_root.is_dir():
        raise ManifestError(f"source root is not a directory: {source_root}")
    _assert_paths_do_not_overlap(
        source_root, destination_root, target_label="staging destination"
    )
    _validate_source(source_root)
    if (owner_repo is None) != (source_revision is None):
        raise ManifestError("explicit source provenance must provide an atomic pair")
    loaded_carrier = _load_source_provenance_with_bytes(source_root)
    provenance = (
        assert_source_payload_matches_provenance(
            source_root,
            owner_repo=owner_repo,
            source_revision=source_revision,
        )
        if owner_repo is not None
        or require_source_provenance
        or loaded_carrier is not None
        else None
    )
    inherited_carrier_bytes = None
    if provenance is not None and loaded_carrier is not None:
        if loaded_carrier[0] != provenance:
            raise ManifestError(
                "captured source provenance carrier changed before copy"
            )
        if not _is_exact_git_root(source_root):
            inherited_carrier_bytes = loaded_carrier[1]

    if mirror:
        destination_root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{destination_root.name}.stage-", dir=destination_root.parent
        ) as temporary:
            candidate = Path(temporary) / "payload"
            _stage_payload_into(
                source_root,
                candidate,
                provenance=provenance,
                inherited_carrier_bytes=inherited_carrier_bytes,
                require_source_provenance=require_source_provenance,
            )
            _assert_paths_do_not_overlap(
                source_root, destination_root, target_label="staging destination"
            )
            _publish_staged_directory(candidate, destination_root)
    else:
        _stage_payload_into(
            source_root,
            destination_root,
            provenance=provenance,
            inherited_carrier_bytes=inherited_carrier_bytes,
            require_source_provenance=require_source_provenance,
        )
    return provenance


def _normalized_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Zero out uid/gid/owner/mtime/pax-headers so archives build reproducibly."""
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    info.pax_headers = {}
    if info.isdir():
        info.mode = 0o755
    elif info.issym():
        info.mode = 0o777
    elif info.isreg():
        info.mode = 0o755 if info.mode & 0o111 else 0o644
    else:
        raise ManifestError(f"unsupported archive member type: {info.name}")
    return info


def _git_blob_oid_from_stream(stream, size: int, algorithm: str) -> str:
    digest = _git_blob_hasher(algorithm, size)
    consumed = 0
    while consumed < size:
        chunk = stream.read(min(1024 * 1024, size - consumed))
        if not chunk:
            break
        consumed += len(chunk)
        digest.update(chunk)
    if consumed != size or stream.read(1):
        raise ManifestError("archive member size changed during verification")
    return digest.hexdigest()


def _sha256_payload_from_stream(stream, size: int) -> bytes:
    """Return the distribution-tree file payload for one exact archive stream."""
    digest = hashlib.sha256()
    consumed = 0
    while consumed < size:
        chunk = stream.read(min(1024 * 1024, size - consumed))
        if not chunk:
            break
        consumed += len(chunk)
        digest.update(chunk)
    if consumed != size or stream.read(1):
        raise ManifestError("archive member size changed during verification")
    return size.to_bytes(8, "big") + digest.digest()


def _assert_archive_matches_git_revision(
    git_root: Path,
    archive_path: Path,
    root_name: str,
    provenance: dict[str, object],
) -> None:
    """Verify the emitted tar, not merely its staging tree, against the claimed commit."""
    revision = str(provenance["source_revision"])
    entries, directories = _expected_git_payload(git_root, revision)
    expected_paths = set(entries) | directories
    members: dict[Path, tarfile.TarInfo] = {}
    carrier_member: tarfile.TarInfo | None = None
    errors: list[str] = []
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.name == root_name:
                if not member.isdir():
                    errors.append("type:archive-root")
                continue
            prefix = f"{root_name}/"
            if not member.name.startswith(prefix):
                errors.append(f"outside-root:{member.name}")
                continue
            relative = Path(member.name.removeprefix(prefix))
            if relative.is_absolute() or ".." in relative.parts:
                errors.append(f"unsafe:{relative.as_posix()}")
                continue
            if relative == Path(SOURCE_PROVENANCE_FILE):
                if carrier_member is not None:
                    errors.append(f"duplicate:{SOURCE_PROVENANCE_FILE}")
                carrier_member = member
                continue
            if relative in members:
                errors.append(f"duplicate:{relative.as_posix()}")
            members[relative] = member

        actual_paths = set(members)
        errors.extend(
            f"missing:{path.as_posix()}"
            for path in sorted(expected_paths - actual_paths)
        )
        errors.extend(
            f"extra:{path.as_posix()}" for path in sorted(actual_paths - expected_paths)
        )
        algorithm = _git_object_format(git_root)
        for relative in sorted(expected_paths & actual_paths):
            member = members[relative]
            if relative in directories:
                if not member.isdir() or member.mode & 0o7777 != 0o755:
                    errors.append(f"type:{relative.as_posix()}")
                continue
            mode, object_type, expected_oid = entries[relative]
            if object_type != "blob" or mode not in {"100644", "100755", "120000"}:
                errors.append(
                    f"unsupported-git-entry:{relative.as_posix()}:{mode}:{object_type}"
                )
                continue
            if mode == "120000":
                if not member.issym() or member.mode & 0o7777 != 0o777:
                    errors.append(f"type:{relative.as_posix()}")
                    continue
                actual_oid = _git_blob_oid_from_bytes(
                    os.fsencode(member.linkname), algorithm
                )
            else:
                if not member.isreg():
                    errors.append(f"type:{relative.as_posix()}")
                    continue
                source = archive.extractfile(member)
                if source is None:
                    errors.append(f"unreadable:{relative.as_posix()}")
                    continue
                actual_oid = _git_blob_oid_from_stream(source, member.size, algorithm)
                expected_mode = 0o755 if mode == "100755" else 0o644
                if member.mode & 0o7777 != expected_mode:
                    errors.append(f"mode:{relative.as_posix()}")
            if actual_oid != expected_oid:
                errors.append(f"bytes:{relative.as_posix()}")

        if (
            carrier_member is None
            or not carrier_member.isreg()
            or carrier_member.mode & 0o7777 != 0o644
        ):
            errors.append(f"missing-or-invalid:{SOURCE_PROVENANCE_FILE}")
        else:
            carrier_source = archive.extractfile(carrier_member)
            try:
                carrier = (
                    json.load(carrier_source) if carrier_source is not None else None
                )
            except (UnicodeError, json.JSONDecodeError) as exc:
                errors.append(f"invalid:{SOURCE_PROVENANCE_FILE}:{exc}")
            else:
                if carrier != provenance:
                    errors.append(f"mismatch:{SOURCE_PROVENANCE_FILE}")

    if errors:
        raise ManifestError(
            "archive differs from claimed Git revision "
            f"{revision}: " + ", ".join(sorted(set(errors)))
        )


def _write_archive(payload_root: Path, output: Path, root_name: str) -> None:
    """Write payload_root as a deterministic gzip+PAX tarball rooted at root_name."""
    inventory = list(_walk_entries(payload_root))
    errors = []
    for path in inventory:
        relative = path.relative_to(payload_root)
        if path_is_forbidden(relative):
            errors.append(f"forbidden path: {relative}")
            continue
        if relative.parts[0] not in ALLOWED_TOP_LEVEL:
            errors.append(f"unexpected top-level path: {relative}")
    if errors:
        raise ManifestError("\n".join(sorted(set(errors))))
    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        output.open("wb") as raw_output,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_output, mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar,
    ):
        root_info = tarfile.TarInfo(root_name)
        root_info.type = tarfile.DIRTYPE
        root_info.mode = 0o755
        tar.addfile(_normalized_tar_info(root_info))
        for path in inventory:
            relative = path.relative_to(payload_root)
            archive_name = f"{root_name}/{relative.as_posix()}"
            info = _normalized_tar_info(tar.gettarinfo(str(path), arcname=archive_name))
            if info.isreg():
                raw, mode = _read_stable_regular_bytes(
                    path, label="staged archive payload file"
                )
                info.mode = 0o755 if mode & 0o111 else 0o644
                info.size = len(raw)
                with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as source:
                    source.write(raw)
                    source.seek(0)
                    tar.addfile(info, source)
            else:
                if info.isdir() or info.issym():
                    info.size = 0
                tar.addfile(info)


def _assert_archive_matches_source_provenance(
    archive_path: Path,
    root_name: str,
    provenance: dict[str, object],
) -> None:
    """Recompute the archived tree directly and compare it with its carrier."""
    prefix = f"{root_name}/"
    entries: list[tuple[bytes, bytes, int, bytes]] = []
    seen: set[bytes] = set()
    carrier_bytes: bytes | None = None
    root_count = 0
    errors: list[str] = []
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                if member.name == root_name:
                    root_count += 1
                    if (
                        not member.isdir()
                        or member.mode & 0o7777 != 0o755
                        or member.size != 0
                    ):
                        errors.append("invalid-root")
                    continue
                if not member.name.startswith(prefix):
                    errors.append(f"unexpected-root:{member.name}")
                    continue
                raw_relative = member.name.removeprefix(prefix)
                relative = Path(raw_relative)
                try:
                    path_bytes = _canonical_relative_path_bytes(relative)
                except ManifestError:
                    errors.append(f"unsafe-path:{member.name}")
                    continue
                if raw_relative != relative.as_posix():
                    errors.append(f"noncanonical-path:{member.name}")
                    continue
                if path_bytes in seen:
                    errors.append(f"duplicate:{relative.as_posix()}")
                    continue
                seen.add(path_bytes)
                if relative == Path(SOURCE_PROVENANCE_FILE):
                    if not member.isreg() or member.mode & 0o7777 != 0o644:
                        errors.append(f"invalid:{SOURCE_PROVENANCE_FILE}")
                        continue
                    source = archive.extractfile(member)
                    if source is None:
                        errors.append(f"unreadable:{SOURCE_PROVENANCE_FILE}")
                        continue
                    carrier_bytes = source.read(member.size + 1)
                    if len(carrier_bytes) != member.size:
                        errors.append(f"size:{SOURCE_PROVENANCE_FILE}")
                    continue
                if path_is_forbidden(relative):
                    errors.append(f"forbidden:{relative.as_posix()}")
                    continue
                if not path_is_included(relative):
                    errors.append(f"unexpected:{relative.as_posix()}")
                    continue
                mode = member.mode & 0o7777
                if member.isdir():
                    if mode != 0o755 or member.size != 0:
                        errors.append(f"mode:{relative.as_posix()}")
                    entries.append((path_bytes, b"d", 0o755, b""))
                elif member.issym():
                    if mode != 0o777 or member.size != 0:
                        errors.append(f"mode:{relative.as_posix()}")
                    if error := _symlink_target_error(relative, member.linkname):
                        errors.append(error)
                        continue
                    try:
                        target_bytes = member.linkname.encode("utf-8", "strict")
                    except UnicodeError:
                        errors.append(f"symlink-target:{relative.as_posix()}")
                        continue
                    entries.append((path_bytes, b"l", 0o777, target_bytes))
                elif member.isreg():
                    if mode not in {0o644, 0o755}:
                        errors.append(f"mode:{relative.as_posix()}")
                    source = archive.extractfile(member)
                    if source is None:
                        errors.append(f"unreadable:{relative.as_posix()}")
                        continue
                    entries.append(
                        (
                            path_bytes,
                            b"f",
                            mode,
                            _sha256_payload_from_stream(source, member.size),
                        )
                    )
                else:
                    errors.append(f"type:{relative.as_posix()}")
            if root_count != 1:
                errors.append(f"root-count:{root_count}")
    except (OSError, tarfile.TarError) as exc:
        raise ManifestError(f"cannot verify candidate archive: {exc}") from exc
    canonical_carrier = _canonical_provenance_bytes(provenance)
    if carrier_bytes is None:
        errors.append(f"missing:{SOURCE_PROVENANCE_FILE}")
    elif carrier_bytes != canonical_carrier:
        errors.append(f"mismatch:{SOURCE_PROVENANCE_FILE}")
    try:
        archived_tree = _distribution_tree_record_from_entries(entries)
    except ManifestError as exc:
        errors.append(str(exc))
        archived_tree = None
    if archived_tree != provenance["payload"]:
        errors.append("payload-digest")
    if errors:
        raise ManifestError(
            "archive does not satisfy source provenance: "
            + ", ".join(sorted(set(errors)))
        )


def _assert_archive_matches_staged_payload(
    payload_root: Path,
    archive_path: Path,
    root_name: str,
) -> None:
    """Prove a candidate archive is a type/mode/byte-exact copy of its staging tree."""
    expected = {
        path.relative_to(payload_root): path for path in _walk_entries(payload_root)
    }
    actual: dict[Path, tarfile.TarInfo] = {}
    errors: list[str] = []
    root_count = 0
    prefix = f"{root_name}/"
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                if member.name == root_name:
                    root_count += 1
                    if not member.isdir() or member.mode & 0o7777 != 0o755:
                        errors.append("invalid-root")
                    continue
                if not member.name.startswith(prefix):
                    errors.append(f"unexpected-root:{member.name}")
                    continue
                raw_relative = member.name.removeprefix(prefix)
                relative = Path(raw_relative)
                if (
                    not raw_relative
                    or relative.is_absolute()
                    or ".." in relative.parts
                    or relative == Path(".")
                ):
                    errors.append(f"unsafe-path:{member.name}")
                    continue
                if relative in actual:
                    errors.append(f"duplicate:{relative.as_posix()}")
                    continue
                actual[relative] = member

            if root_count != 1:
                errors.append(f"root-count:{root_count}")
            errors.extend(
                f"missing:{path.as_posix()}"
                for path in sorted(set(expected) - set(actual))
            )
            errors.extend(
                f"extra:{path.as_posix()}"
                for path in sorted(set(actual) - set(expected))
            )
            for relative in sorted(set(expected) & set(actual)):
                path = expected[relative]
                member = actual[relative]
                metadata = path.lstat()
                if path.is_symlink():
                    expected_mode = 0o777
                elif path.is_dir():
                    expected_mode = 0o755
                else:
                    expected_mode = 0o755 if metadata.st_mode & 0o111 else 0o644
                if member.mode & 0o7777 != expected_mode:
                    errors.append(f"mode:{relative.as_posix()}")
                if path.is_symlink():
                    if not member.issym() or member.linkname != os.readlink(path):
                        errors.append(f"symlink:{relative.as_posix()}")
                    continue
                if path.is_dir():
                    if not member.isdir():
                        errors.append(f"type:{relative.as_posix()}")
                    continue
                if not path.is_file() or not member.isreg():
                    errors.append(f"type:{relative.as_posix()}")
                    continue
                expected_oid, _ = _git_blob_oid_from_file(path, "sha256")
                source = archive.extractfile(member)
                if source is None:
                    errors.append(f"unreadable:{relative.as_posix()}")
                    continue
                actual_oid = _git_blob_oid_from_stream(source, member.size, "sha256")
                if actual_oid != expected_oid:
                    errors.append(f"bytes:{relative.as_posix()}")
    except (OSError, tarfile.TarError) as exc:
        raise ManifestError(f"cannot verify candidate archive: {exc}") from exc
    if errors:
        raise ManifestError(
            "candidate archive differs from staged payload: "
            + ", ".join(sorted(set(errors)))
        )


def create_archive(
    source: str | Path,
    output: str | Path,
    *,
    root_name: str,
    owner_repo: str | None = None,
    source_revision: str | None = None,
) -> Path:
    """Stage source into a temp dir under root_name and archive it to output.

    Returns the output path. Raises ManifestError if root_name is unsafe.
    """
    if not root_name or root_name in {".", ".."} or "/" in root_name:
        raise ManifestError(f"unsafe archive root name: {root_name!r}")
    source_root = Path(source).resolve(strict=False)
    if not source_root.is_dir():
        raise ManifestError(f"source root is not a directory: {source_root}")
    output_path = Path(os.path.abspath(output))
    _assert_paths_do_not_overlap(
        source_root, output_path, target_label="archive output"
    )
    if output_path.is_symlink():
        raise ManifestError(f"archive output must not be a symlink: {output_path}")
    if output_path.exists() and output_path.is_dir():
        raise ManifestError(f"archive output must not be a directory: {output_path}")
    if output_path.exists() and not output_path.is_file():
        raise ManifestError(f"archive output must be a regular file: {output_path}")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ManifestError(
            f"cannot prepare archive output directory {output_path.parent}: {exc}"
        ) from exc
    provenance = assert_source_payload_matches_provenance(
        source_root,
        owner_repo=owner_repo,
        source_revision=source_revision,
    )
    with tempfile.TemporaryDirectory(
        prefix=".vibecrafted-archive-",
        dir=output_path.parent,
    ) as temporary:
        temporary_root = Path(temporary)
        payload_root = temporary_root / root_name
        candidate_archive = temporary_root / "candidate.tar.gz"
        stage_payload(
            source_root,
            payload_root,
            mirror=True,
            owner_repo=provenance["owner_repo"],
            source_revision=provenance["source_revision"],
            require_source_provenance=True,
        )
        _write_archive(payload_root, candidate_archive, root_name)
        _assert_archive_matches_staged_payload(
            payload_root,
            candidate_archive,
            root_name,
        )
        _assert_archive_matches_source_provenance(
            candidate_archive,
            root_name,
            provenance,
        )
        if _is_exact_git_root(source_root):
            _assert_archive_matches_git_revision(
                source_root,
                candidate_archive,
                root_name,
                provenance,
            )
        os.replace(candidate_archive, output_path)
    return output_path


def publish_archive_candidate(
    source: str | Path,
    candidate: str | Path,
    output: str | Path,
) -> Path:
    """Atomically publish one verified archive without exposing source files.

    Archive construction must happen outside ``source`` so the source digest
    cannot include its own output. Publication back into that source is allowed
    only below its physical ``dist`` directory; every other in-source target is
    product input and must never be replaced by an overridable build variable.
    """
    source_root = Path(source).resolve(strict=True)
    if not source_root.is_dir():
        raise ManifestError(f"source root is not a directory: {source_root}")
    candidate_path = Path(os.path.abspath(candidate))
    output_path = Path(os.path.abspath(output))
    if output_path.name in {"", ".", ".."}:
        raise ManifestError(f"unsafe archive publish output: {output_path}")
    try:
        candidate_meta = candidate_path.lstat()
    except OSError as exc:
        raise ManifestError(
            f"cannot inspect archive publish candidate {candidate_path}: {exc}"
        ) from exc
    if (
        not stat.S_ISREG(candidate_meta.st_mode)
        or candidate_meta.st_nlink != 1
        or candidate_path.is_symlink()
    ):
        raise ManifestError(
            f"archive publish candidate must be one regular file: {candidate_path}"
        )
    try:
        parent_identity = output_path.parent.resolve(strict=True)
        parent_meta = parent_identity.lstat()
    except OSError as exc:
        raise ManifestError(
            f"cannot inspect archive publish directory {output_path.parent}: {exc}"
        ) from exc
    if not stat.S_ISDIR(parent_meta.st_mode) or parent_identity.is_symlink():
        raise ManifestError(
            f"archive publish parent must be a physical directory: {parent_identity}"
        )
    output_identity = parent_identity / output_path.name
    if output_identity == source_root or _path_contains(output_identity, source_root):
        raise ManifestError(
            f"archive publish output contains the source root: {output_identity}"
        )
    if _path_contains(source_root, output_identity):
        relative_output = output_identity.relative_to(source_root)
        if not relative_output.parts or relative_output.parts[0] != "dist":
            raise ManifestError(
                "archive publish output inside source must be below its physical "
                f"dist directory: {output_identity}"
            )
        distribution_root = source_root / "dist"
        try:
            distribution_meta = distribution_root.lstat()
            distribution_identity = distribution_root.resolve(strict=True)
        except OSError as exc:
            raise ManifestError(
                f"cannot inspect the source distribution directory: {exc}"
            ) from exc
        if (
            not stat.S_ISDIR(distribution_meta.st_mode)
            or distribution_root.is_symlink()
            or not _path_contains(distribution_identity, output_identity)
            or output_identity == distribution_identity
        ):
            raise ManifestError(
                "archive publish output inside source must be below its physical "
                f"dist directory: {output_identity}"
            )

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_fd = os.open(parent_identity, directory_flags)
    except OSError as exc:
        raise ManifestError(
            f"cannot bind archive publish directory {parent_identity}: {exc}"
        ) from exc
    try:
        opened_parent = os.fstat(parent_fd)
        if (opened_parent.st_dev, opened_parent.st_ino) != (
            parent_meta.st_dev,
            parent_meta.st_ino,
        ):
            raise ManifestError(
                f"archive publish directory changed before mutation: {parent_identity}"
            )
        try:
            existing = os.stat(
                output_path.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            existing = None
        except OSError as exc:
            raise ManifestError(
                f"cannot inspect archive publish output {output_identity}: {exc}"
            ) from exc
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise ManifestError(
                f"archive publish output must not be a symlink or directory: {output_identity}"
            )
        try:
            os.replace(
                candidate_path,
                output_path.name,
                dst_dir_fd=parent_fd,
            )
        except OSError as exc:
            raise ManifestError(
                f"cannot publish archive to {output_identity}: {exc}"
            ) from exc
        published = os.stat(
            output_path.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (published.st_dev, published.st_ino) != (
            candidate_meta.st_dev,
            candidate_meta.st_ino,
        ):
            raise ManifestError(
                f"archive publish output changed identity: {output_identity}"
            )
    finally:
        os.close(parent_fd)
    return output_path


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser exposing the check/stage/archive subcommands."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="Validate a staged payload")
    check.add_argument("--root", required=True, type=Path)
    check.add_argument("--require-source-provenance", action="store_true")
    check.add_argument("--expected-owner-repo")
    check.add_argument("--expected-source-revision")

    stage = subparsers.add_parser("stage", help="Create an allowlisted payload")
    stage.add_argument("--source", required=True, type=Path)
    stage.add_argument("--destination", required=True, type=Path)
    stage.add_argument("--mirror", action="store_true")
    stage.add_argument("--owner-repo")
    stage.add_argument("--source-revision")
    stage.add_argument("--require-source-provenance", action="store_true")

    archive = subparsers.add_parser("archive", help="Create a validated tarball")
    archive.add_argument("--source", required=True, type=Path)
    archive.add_argument("--output", required=True, type=Path)
    archive.add_argument("--publish-output", type=Path)
    archive.add_argument("--root-name", required=True)
    archive.add_argument("--owner-repo")
    archive.add_argument("--source-revision")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: dispatch to check/stage/archive, printing errors to stderr."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "check":
            validate_payload(
                args.root,
                require_source_provenance=(
                    args.require_source_provenance
                    or args.expected_owner_repo is not None
                    or args.expected_source_revision is not None
                ),
                expected_owner_repo=args.expected_owner_repo,
                expected_source_revision=args.expected_source_revision,
            )
            print(f"Payload valid: {args.root}")
        elif args.command == "stage":
            stage_payload(
                args.source,
                args.destination,
                mirror=args.mirror,
                owner_repo=args.owner_repo,
                source_revision=args.source_revision,
                require_source_provenance=args.require_source_provenance,
            )
            print(f"Payload staged: {args.destination}")
        elif args.command == "archive":
            archive = create_archive(
                args.source,
                args.output,
                root_name=args.root_name,
                owner_repo=args.owner_repo,
                source_revision=args.source_revision,
            )
            if args.publish_output is not None:
                archive = publish_archive_candidate(
                    args.source,
                    archive,
                    args.publish_output,
                )
            print(f"Archive built: {archive}")
        else:  # pragma: no cover - argparse owns command validation.
            raise ManifestError(f"unknown command: {args.command}")
    except (ManifestError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
