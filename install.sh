#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF_USAGE'
Usage: install.sh [--gui] [--yes] [--runtime <horse>] [--ref <branch>] [--archive-url <url> | --archive-file <path>] [--tools-dir <dir>] [make-target]

Verify a local 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. source snapshot, then run the transactional installer
from that private candidate. The installer publishes into $HOME/.local/share/vibecrafted/tools.

Use `--gui` when you want the browser-based guided installer.
Use `--yes` to skip the attended bootstrap confirmation prompt.
Use `--runtime <horse>` to install and activate a lab runtime: wezterm, vc-apprt, locterm, microsandbox, or none.
`--archive-url` and `--archive-file` require the closed
`source-provenance.json` carrier. Create local archives with
`scripts/distribution_manifest.py archive`: for a Git checkout, that writer
proves every included byte against the claimed commit before writing the carrier.
Non-interactive runs without `--gui` bypass the browser and call the compact installer directly.

Examples:
  curl -fsSL https://vibecrafted.io/install.sh | bash
  curl -fsSL https://vibecrafted.io/install.sh | bash -s -- --gui
  curl -fsSL https://vibecrafted.io/install.sh | bash -s -- --yes
  curl -fsSL https://vibecrafted.io/install.sh | bash -s -- --runtime wezterm
  curl -fsSL https://vibecrafted.io/install.sh | bash -s -- --ref develop
  bash install.sh doctor
  bash install.sh --runtime locterm
  bash install.sh --archive-file /tmp/vibecrafted.tar.gz vibecrafted
EOF_USAGE
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '%s\n' "$*"
}

# Output discipline: the default view is calm storytelling (≤10 lines total,
# ≤2 per install section). Detail lines are gated — VERBOSE=1 restores the
# full bazaar, nothing is lost.
vinfo() {
  if [[ "${VERBOSE:-0}" == "1" ]]; then
    printf '%s\n' "$*"
  fi
}

# -----------------------------------------------------------------------------
# Platform detection (Plan 03 — cross-platform install)
#
# detect_platform sets PLATFORM_OS to one of: macos, linux, wsl, unsupported.
# detect_linux_distro sets LINUX_DISTRO_ID (e.g. debian, ubuntu, arch, fedora)
# and LINUX_PKG_MGR (apt, dnf, pacman, "") based on /etc/os-release. Both are
# safe to call multiple times. On macOS, the linux helpers are no-ops.
#
# The detection layer is informational only — it does NOT change the staged
# install layout. macOS path (`$HOME/.vibecrafted`) and Linux/WSL path are
# the same; only the pre-flight hints (which package manager to suggest)
# differ. WSL is treated as Linux for runtime; the WSL banner only changes
# the user-facing message.
# -----------------------------------------------------------------------------

PLATFORM_OS=""
LINUX_DISTRO_ID=""
LINUX_PKG_MGR=""

detect_platform() {
  case "$(uname -s)" in
    Darwin*)
      PLATFORM_OS="macos"
      ;;
    Linux*)
      # WSL: kernel release contains 'microsoft' or '/proc/version' mentions it.
      if grep -qiE 'microsoft|wsl' /proc/sys/kernel/osrelease 2>/dev/null \
         || grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
        PLATFORM_OS="wsl"
      else
        PLATFORM_OS="linux"
      fi
      ;;
    MINGW*|MSYS*|CYGWIN*)
      PLATFORM_OS="unsupported"
      ;;
    *)
      PLATFORM_OS="unsupported"
      ;;
  esac
}

detect_linux_distro() {
  LINUX_DISTRO_ID=""
  LINUX_PKG_MGR=""
  [[ "$PLATFORM_OS" == "linux" || "$PLATFORM_OS" == "wsl" ]] || return 0
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    LINUX_DISTRO_ID="$(. /etc/os-release && printf '%s' "${ID:-}")"
  fi
  case "$LINUX_DISTRO_ID" in
    debian|ubuntu|linuxmint|pop|raspbian)
      LINUX_PKG_MGR="apt"
      ;;
    fedora|rhel|centos|rocky|almalinux)
      LINUX_PKG_MGR="dnf"
      ;;
    arch|manjaro|endeavouros)
      LINUX_PKG_MGR="pacman"
      ;;
    *)
      LINUX_PKG_MGR=""
      ;;
  esac
}

# preflight_pkg_hint emits a copy-pasteable install command for the named
# missing tool, scoped to the detected Linux package manager. macOS path
# uses brew. On unknown distros, emit a generic message. Idempotent and
# silent under non-Linux/macOS hosts.
preflight_pkg_hint() {
  local missing="$1"
  case "$PLATFORM_OS" in
    macos)
      printf '  hint: brew install %s\n' "$missing" >&2
      ;;
    linux|wsl)
      case "$LINUX_PKG_MGR" in
        apt)
          printf '  hint: sudo apt-get update && sudo apt-get install -y %s\n' "$missing" >&2
          ;;
        dnf)
          printf '  hint: sudo dnf install -y %s\n' "$missing" >&2
          ;;
        pacman)
          printf '  hint: sudo pacman -S --noconfirm %s\n' "$missing" >&2
          ;;
        *)
          printf '  hint: install %s via your distro package manager\n' "$missing" >&2
          ;;
      esac
      ;;
    *)
      printf '  hint: install %s for your platform\n' "$missing" >&2
      ;;
  esac
}

platform_banner() {
  case "$PLATFORM_OS" in
    macos)
      info "Platform: macOS ($(uname -m))"
      ;;
    linux)
      if [[ -n "$LINUX_DISTRO_ID" ]]; then
        info "Platform: Linux / $LINUX_DISTRO_ID ($(uname -m))"
      else
        info "Platform: Linux / generic ($(uname -m))"
      fi
      ;;
    wsl)
      if [[ -n "$LINUX_DISTRO_ID" ]]; then
        info "Platform: WSL / $LINUX_DISTRO_ID ($(uname -m))"
      else
        info "Platform: WSL / generic ($(uname -m))"
      fi
      ;;
    *)
      info "Platform: $(uname -s) (unsupported — best-effort only)"
      ;;
  esac
}

extract_tarball() {
  local archive="$1"
  local destination="$2"
  # The archive preflight admits only canonical 0755 directories, 0644/0755
  # files, and 0777 symlinks. Preserve those verified modes explicitly: a
  # hardened operator umask (for example 077) must not make our own extracted
  # tree fail the post-extraction identity check.
  local tar_args=(-xzf "$archive" -C "$destination" -p)

  # Release archives can carry macOS LIBARCHIVE/PAX xattrs. GNU tar prints a
  # wall of harmless "unknown keyword" warnings on Linux unless we quiet them.
  if tar --warning=no-unknown-keyword -tf "$archive" >/dev/null 2>&1; then
    tar --warning=no-unknown-keyword "${tar_args[@]}"
  else
    COPYFILE_DISABLE=1 tar "${tar_args[@]}"
  fi
}

# The archive's Python is candidate input, not a verifier.  Keep the integrity
# implementation in this bootstrap and run it before importing or executing a
# single archive-supplied byte.  The receipt returned by `archive` stays in shell
# memory while the candidate helper runs, so that helper cannot rewrite its
# expected digest before the final `tree` check.
bootstrap_integrity_preflight() {
  local operation="$1"
  shift
  "$bootstrap_python" -I -S - "$operation" "$@" <<'PY_BOOTSTRAP_INTEGRITY'
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import stat
import struct
import sys
import tarfile
from pathlib import Path, PurePosixPath


SOURCE_PROVENANCE_FILE = "source-provenance.json"
SOURCE_PROVENANCE_SCHEMA = "vibecrafted.source-provenance.v2"
TREE_SCHEMA = "vibecrafted.distribution-tree.v1"
TREE_ALGORITHM = "sha256"
TREE_DOMAIN = b"vibecrafted.distribution-tree.v1\0"
OWNER_REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
TREE_SHA_RE = re.compile(r"[0-9a-f]{64}")
ROOT_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")

REQUIRED_FILES = frozenset(
    {
        "VERSION",
        "LICENSE",
        "README.md",
        "Makefile",
        "install.sh",
        "install.ps1",
        "install.toml",
        "scripts/distribution_manifest.py",
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
    }
)
REQUIRED_DIRECTORIES = frozenset(
    {
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
    }
)
REQUIRED_SURFACE_FILES = frozenset(
    {
        "bin/vc-workflow",
        "config/README.md",
        "docs/INSTALL.md",
        "plugins/iterm2/README.md",
        "scripts/installer/pyproject.toml",
        "templates/hooks/install.sh",
        "tools/README.md",
        "vibecrafted-app/Cargo.toml",
        "vibecrafted-core/vibecrafted_core/runtime/README.md",
        "vibecrafted-core/vibecrafted_core/skills/LIVING_TREE_RULE.md",
        "vibecrafted-mcp/pyproject.toml",
        "vibecrafted-server/Cargo.toml",
        "vibecrafted-vm/Containerfile",
        "workflows/MARBLES.md",
    }
)
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
        "dist",
        "node_modules",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "target",
        "test",
        "tests",
        "uv.lock",
        "yarn.lock",
    }
)
FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".swp", "~")
REQUIRED_LOCKFILES = frozenset(
    {"vibecrafted-app/Cargo.lock", "vibecrafted-server/Cargo.lock"}
)


class PreflightError(ValueError):
    pass


def fail(message: str) -> None:
    raise PreflightError(message)


def utf8(value: str, *, label: str) -> bytes:
    try:
        return value.encode("utf-8", "strict")
    except UnicodeError as exc:
        fail(f"{label} is not canonical UTF-8: {exc}")


def is_secret_env(component: str) -> bool:
    return component == ".env" or (
        component.startswith(".env.") and not component.endswith(".example")
    )


def validate_payload_path(relative: str) -> bytes:
    raw = utf8(relative, label="payload path")
    path = PurePosixPath(relative)
    if (
        not relative
        or relative.startswith("/")
        or relative.endswith("/")
        or path.as_posix() != relative
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        fail(f"unsafe or noncanonical payload path: {relative!r}")
    if path.parts[0] not in ALLOWED_TOP_LEVEL:
        fail(f"unexpected top-level path: {relative}")
    if relative not in REQUIRED_LOCKFILES and any(
        part in FORBIDDEN_COMPONENTS or is_secret_env(part) for part in path.parts
    ):
        fail(f"forbidden path: {relative}")
    if relative not in REQUIRED_LOCKFILES and path.name.endswith(FORBIDDEN_SUFFIXES):
        fail(f"forbidden path: {relative}")
    return raw


def validate_symlink_target(relative: str, target: str) -> bytes:
    raw = utf8(target, label=f"symlink target for {relative}")
    target_path = PurePosixPath(target)
    if (
        not target
        or target.startswith("/")
        or target_path.as_posix() != target
    ):
        fail(f"unsafe or noncanonical symlink: {relative} -> {target!r}")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        fail(f"symlink escapes payload: {relative} -> {target}")
    validate_payload_path(resolved)
    return raw


def validate_required_structure(kinds: dict[str, str]) -> None:
    missing_files = sorted(
        relative
        for relative in REQUIRED_FILES | REQUIRED_SURFACE_FILES
        if kinds.get(relative) != "file"
    )
    missing_directories = sorted(
        relative
        for relative in REQUIRED_DIRECTORIES
        if kinds.get(relative) != "directory"
    )
    errors = [
        *(f"missing required file: {relative}" for relative in missing_files),
        *(
            f"missing required directory: {relative}"
            for relative in missing_directories
        ),
    ]
    if errors:
        fail("; ".join(errors))


def validate_carrier(raw: bytes) -> dict[str, object]:
    if len(raw) > 64 * 1024:
        fail(f"{SOURCE_PROVENANCE_FILE} is unreasonably large")
    try:
        payload = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"invalid {SOURCE_PROVENANCE_FILE}: {exc}")
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "owner_repo",
        "source_revision",
        "payload",
    }:
        fail(f"{SOURCE_PROVENANCE_FILE} does not satisfy the closed v2 schema")
    nested = payload.get("payload")
    if not isinstance(nested, dict) or set(nested) != {
        "schema",
        "algorithm",
        "tree_sha256",
        "entry_count",
    }:
        fail(f"{SOURCE_PROVENANCE_FILE} does not satisfy the closed v2 schema")
    owner = payload.get("owner_repo")
    revision = payload.get("source_revision")
    tree_sha = nested.get("tree_sha256")
    entry_count = nested.get("entry_count")
    if (
        payload.get("schema") != SOURCE_PROVENANCE_SCHEMA
        or not isinstance(owner, str)
        or OWNER_REPO_RE.fullmatch(owner) is None
        or not isinstance(revision, str)
        or GIT_SHA_RE.fullmatch(revision) is None
        or nested.get("schema") != TREE_SCHEMA
        or nested.get("algorithm") != TREE_ALGORITHM
        or not isinstance(tree_sha, str)
        or TREE_SHA_RE.fullmatch(tree_sha) is None
        or isinstance(entry_count, bool)
        or not isinstance(entry_count, int)
        or entry_count < 1
    ):
        fail(f"{SOURCE_PROVENANCE_FILE} does not satisfy the closed v2 schema")
    canonical = (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    if raw != canonical:
        fail(f"{SOURCE_PROVENANCE_FILE} is not canonical JSON")
    return payload


def update_tree_digest(
    entries: list[tuple[bytes, bytes, int, bytes]],
) -> tuple[str, int]:
    entries.sort(key=lambda item: item[0])
    digest = hashlib.sha256()
    digest.update(TREE_DOMAIN)
    digest.update(struct.pack(">Q", len(entries)))
    for raw_path, kind, mode, payload in entries:
        digest.update(kind)
        digest.update(struct.pack(">Q", len(raw_path)))
        digest.update(raw_path)
        digest.update(struct.pack(">I", mode))
        digest.update(struct.pack(">Q", len(payload)))
        digest.update(payload)
    return digest.hexdigest(), len(entries)


def assert_expected(
    raw_carrier: bytes,
    carrier: dict[str, object],
    entries: list[tuple[bytes, bytes, int, bytes]],
    expected: tuple[str, str, str, str, int] | None,
) -> tuple[str, int]:
    actual_tree, actual_count = update_tree_digest(entries)
    nested = carrier["payload"]
    assert isinstance(nested, dict)
    if actual_count != nested["entry_count"] or actual_tree != nested["tree_sha256"]:
        fail(
            "distribution tree digest mismatch: "
            f"expected {nested['tree_sha256']}/{nested['entry_count']}, "
            f"got {actual_tree}/{actual_count}"
        )
    if expected is not None:
        expected_carrier_sha, owner, revision, tree_sha, entry_count = expected
        actual_carrier_sha = hashlib.sha256(raw_carrier).hexdigest()
        if actual_carrier_sha != expected_carrier_sha:
            fail(f"{SOURCE_PROVENANCE_FILE} changed after archive preflight")
        if (
            carrier["owner_repo"] != owner
            or carrier["source_revision"] != revision
            or actual_tree != tree_sha
            or actual_count != entry_count
        ):
            fail("source-provenance v2 receipt changed after archive preflight")
    return actual_tree, actual_count


def archive_entries(archive_path: Path) -> tuple[str, bytes, dict[str, object], list[tuple[bytes, bytes, int, bytes]]]:
    records: dict[str, tuple[bytes, bytes, int, bytes]] = {}
    member_types: dict[str, str] = {}
    roots: set[str] = set()
    root_members = 0
    carrier_raw: bytes | None = None
    carrier: dict[str, object] | None = None
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            for member in archive.getmembers():
                name = member.name
                utf8(name, label="archive member name")
                pure = PurePosixPath(name)
                if (
                    not name
                    or name.startswith("/")
                    or name.endswith("/")
                    or pure.as_posix() != name
                    or any(part in {"", ".", ".."} for part in pure.parts)
                ):
                    fail(f"unsafe or noncanonical archive member: {name!r}")
                root_name = pure.parts[0]
                if ROOT_NAME_RE.fullmatch(root_name) is None:
                    fail(f"unsafe or noncanonical archive root: {root_name!r}")
                roots.add(root_name)
                if len(pure.parts) == 1:
                    root_members += 1
                    if not member.isdir() or member.mode != 0o755:
                        fail("archive root must be one canonical 0755 directory")
                    continue
                relative = PurePosixPath(*pure.parts[1:]).as_posix()
                raw_path = validate_payload_path(relative)
                if relative in records or relative in member_types:
                    fail(f"duplicate archive member: {relative}")
                if member.islnk():
                    fail(f"hardlink archive member is forbidden: {relative}")
                if relative == SOURCE_PROVENANCE_FILE:
                    if not member.isreg() or member.mode != 0o644:
                        fail(f"{SOURCE_PROVENANCE_FILE} must be one canonical 0644 file")
                    source = archive.extractfile(member)
                    if source is None:
                        fail(f"cannot read {SOURCE_PROVENANCE_FILE}")
                    carrier_raw = source.read(64 * 1024 + 1)
                    if len(carrier_raw) != member.size:
                        fail(f"cannot read the complete {SOURCE_PROVENANCE_FILE}")
                    carrier = validate_carrier(carrier_raw)
                    member_types[relative] = "file"
                    continue
                if member.isdir():
                    if member.mode != 0o755 or member.size != 0:
                        fail(f"noncanonical directory member: {relative}")
                    records[relative] = (raw_path, b"d", 0o755, b"")
                    member_types[relative] = "directory"
                elif member.isreg():
                    if member.mode not in {0o644, 0o755}:
                        fail(f"noncanonical file mode: {relative}")
                    source = archive.extractfile(member)
                    if source is None:
                        fail(f"cannot read archive member: {relative}")
                    file_digest = hashlib.sha256()
                    consumed = 0
                    while consumed < member.size:
                        chunk = source.read(min(1024 * 1024, member.size - consumed))
                        if not chunk:
                            break
                        consumed += len(chunk)
                        file_digest.update(chunk)
                    if consumed != member.size or source.read(1):
                        fail(f"archive member size changed while reading: {relative}")
                    payload = struct.pack(">Q", member.size) + file_digest.digest()
                    records[relative] = (raw_path, b"f", member.mode, payload)
                    member_types[relative] = "file"
                elif member.issym():
                    if member.mode != 0o777 or member.size != 0:
                        fail(f"noncanonical symlink member: {relative}")
                    target = validate_symlink_target(relative, member.linkname)
                    records[relative] = (raw_path, b"l", 0o777, target)
                    member_types[relative] = "symlink"
                else:
                    fail(f"special archive member is forbidden: {relative}")
    except (OSError, tarfile.TarError) as exc:
        fail(f"cannot inspect archive: {exc}")

    if len(roots) != 1 or root_members != 1:
        fail("archive must contain exactly one explicit top-level root directory")
    if carrier_raw is None or carrier is None:
        fail(f"missing required {SOURCE_PROVENANCE_SCHEMA} carrier")
    validate_required_structure(member_types)
    for relative, kind in member_types.items():
        if relative == SOURCE_PROVENANCE_FILE:
            continue
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            parent_name = parent.as_posix()
            if member_types.get(parent_name) != "directory":
                fail(f"archive member has a missing or non-directory parent: {relative}")
            parent = parent.parent
        parts = PurePosixPath(relative).parts
        for index in range(1, len(parts)):
            ancestor = PurePosixPath(*parts[:index]).as_posix()
            if member_types.get(ancestor) == "symlink":
                fail(f"archive member descends through symlink: {relative}")
    return roots.pop(), carrier_raw, carrier, list(records.values())


def stable_regular_file(path: Path) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        fail(f"cannot open regular file without following links: {path}: {exc}")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            fail(f"hardlinked or non-regular file is forbidden: {path}")
        digest = hashlib.sha256()
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
        except OSError:
            fail(f"file path changed during integrity preflight: {path}")
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
        )
        if (
            consumed != before.st_size
            or identity(before) != identity(after)
            or identity(after) != identity(path_after)
        ):
            fail(f"file changed during integrity preflight: {path}")
        return digest.digest(), after
    finally:
        os.close(descriptor)


def filesystem_entries(root: Path) -> tuple[bytes, dict[str, object], list[tuple[bytes, bytes, int, bytes]]]:
    try:
        root_meta = root.lstat()
    except OSError as exc:
        fail(f"cannot inspect extracted tree: {exc}")
    if not stat.S_ISDIR(root_meta.st_mode) or stat.S_IMODE(root_meta.st_mode) != 0o755:
        fail("extracted tree root must be one canonical 0755 directory")

    paths: list[tuple[str, Path]] = []

    def visit(directory: Path, prefix: PurePosixPath) -> None:
        try:
            children = list(os.scandir(directory))
        except OSError as exc:
            fail(f"cannot scan extracted tree: {directory}: {exc}")
        children.sort(key=lambda entry: utf8(entry.name, label="filesystem path"))
        for child in children:
            relative = PurePosixPath(prefix, child.name).as_posix()
            validate_payload_path(relative)
            path = Path(child.path)
            paths.append((relative, path))
            if child.is_dir(follow_symlinks=False):
                visit(path, PurePosixPath(relative))

    visit(root, PurePosixPath("."))
    carrier_paths = [path for relative, path in paths if relative == SOURCE_PROVENANCE_FILE]
    if len(carrier_paths) != 1:
        fail(f"expected exactly one {SOURCE_PROVENANCE_FILE}")
    carrier_digest, carrier_meta = stable_regular_file(carrier_paths[0])
    if stat.S_IMODE(carrier_meta.st_mode) != 0o644:
        fail(f"{SOURCE_PROVENANCE_FILE} must be one canonical 0644 file")
    carrier_raw = carrier_paths[0].read_bytes()
    if hashlib.sha256(carrier_raw).digest() != carrier_digest:
        fail(f"{SOURCE_PROVENANCE_FILE} changed during integrity preflight")
    carrier = validate_carrier(carrier_raw)

    records: list[tuple[bytes, bytes, int, bytes]] = []
    kinds: dict[str, str] = {SOURCE_PROVENANCE_FILE: "file"}
    for relative, path in paths:
        if relative == SOURCE_PROVENANCE_FILE:
            continue
        raw_path = validate_payload_path(relative)
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            before = metadata
            target = os.readlink(path)
            after = path.lstat()
            if (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
            ):
                fail(f"symlink changed during integrity preflight: {relative}")
            payload = validate_symlink_target(relative, target)
            records.append((raw_path, b"l", 0o777, payload))
            kinds[relative] = "symlink"
        elif stat.S_ISDIR(metadata.st_mode):
            if mode != 0o755:
                fail(f"noncanonical directory mode: {relative}")
            records.append((raw_path, b"d", 0o755, b""))
            kinds[relative] = "directory"
        elif stat.S_ISREG(metadata.st_mode):
            if mode not in {0o644, 0o755}:
                fail(f"noncanonical file mode: {relative}")
            file_digest, stable_meta = stable_regular_file(path)
            payload = struct.pack(">Q", stable_meta.st_size) + file_digest
            records.append((raw_path, b"f", mode, payload))
            kinds[relative] = "file"
        else:
            fail(f"special extracted path is forbidden: {relative}")
    validate_required_structure(kinds)
    return carrier_raw, carrier, records


def parse_expected(values: list[str]) -> tuple[str, str, str, str, int]:
    if len(values) != 5:
        fail("bootstrap receipt is incomplete")
    carrier_sha, owner, revision, tree_sha, raw_count = values
    if TREE_SHA_RE.fullmatch(carrier_sha) is None:
        fail("bootstrap carrier digest is invalid")
    if OWNER_REPO_RE.fullmatch(owner) is None or GIT_SHA_RE.fullmatch(revision) is None:
        fail("bootstrap source identity is invalid")
    if TREE_SHA_RE.fullmatch(tree_sha) is None or not raw_count.isascii() or not raw_count.isdigit():
        fail("bootstrap tree receipt is invalid")
    return carrier_sha, owner, revision, tree_sha, int(raw_count)


try:
    operation = sys.argv[1]
    if operation == "archive":
        if len(sys.argv) != 3:
            fail("archive preflight requires exactly one path")
        root_name, raw_carrier, carrier, records = archive_entries(Path(sys.argv[2]))
        actual_tree, actual_count = assert_expected(
            raw_carrier, carrier, records, expected=None
        )
        print(
            "\t".join(
                (
                    root_name,
                    hashlib.sha256(raw_carrier).hexdigest(),
                    str(carrier["owner_repo"]),
                    str(carrier["source_revision"]),
                    actual_tree,
                    str(actual_count),
                )
            )
        )
    elif operation == "tree":
        if len(sys.argv) != 8:
            fail("tree preflight requires one root and one complete receipt")
        raw_carrier, carrier, records = filesystem_entries(Path(sys.argv[2]))
        assert_expected(
            raw_carrier,
            carrier,
            records,
            expected=parse_expected(sys.argv[3:]),
        )
    else:
        fail(f"unknown bootstrap integrity operation: {operation}")
except (OSError, PreflightError) as exc:
    print(f"bootstrap integrity preflight: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY_BOOTSTRAP_INTEGRITY
}

snapshot_local_archive() {
  local source="$1"
  local destination="$2"
  "$bootstrap_python" -I -S - "$source" "$destination" <<'PY_BOOTSTRAP_SNAPSHOT'
import os
import stat
import sys

source, destination = sys.argv[1:]
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
try:
    source_fd = os.open(source, flags)
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("source is not a regular file")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            consumed = 0
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                consumed += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        after = os.fstat(source_fd)
        try:
            path_after = os.lstat(source)
        except OSError as exc:
            raise OSError("source path changed while making the private snapshot") from exc
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
        )
        if (
            consumed != before.st_size
            or identity(before) != identity(after)
            or identity(after) != identity(path_after)
        ):
            raise OSError("source changed while making the private snapshot")
    finally:
        os.close(source_fd)
except OSError as exc:
    print(f"bootstrap archive snapshot: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY_BOOTSTRAP_SNAPSHOT
}


has_attended_tty() {
  if { exec 9<>/dev/tty; } 2>/dev/null; then
    exec 9>&- 9<&- || true
    return 0
  fi
  return 1
}

# >>> scripts/lib/runtime-roots.sh (verbatim copy — install.sh is the curl|bash bootstrap; parity test pins it)
is_interactive_session() {
  [[ -t 0 && -t 1 ]]
}

default_vibecrafted_home() {
  if [[ -n "${VIBECRAFTED_HOME:-}" ]]; then
    printf '%s\n' "$VIBECRAFTED_HOME"
    return
  fi
  printf '%s\n' "$HOME/.vibecrafted"
}

default_vibecrafted_runtime_home() {
  if [[ -n "${VIBECRAFTED_RUNTIME_HOME:-}" ]]; then
    printf '%s\n' "$VIBECRAFTED_RUNTIME_HOME"
    return
  fi
  if [[ -n "${XDG_DATA_HOME:-}" ]]; then
    printf '%s\n' "$XDG_DATA_HOME/vibecrafted"
    return
  fi
  printf '%s\n' "$HOME/.local/share/vibecrafted"
}

default_vibecrafted_tools_home() {
  if [[ -n "${VIBECRAFTED_TOOLS_HOME:-}" ]]; then
    printf '%s\n' "$VIBECRAFTED_TOOLS_HOME"
    return
  fi
  printf '%s/tools\n' "$(default_vibecrafted_runtime_home)"
}

default_vibecrafted_launcher_bin() {
  if [[ -n "${VIBECRAFTED_LAUNCHER_BIN:-}" ]]; then
    printf '%s\n' "$VIBECRAFTED_LAUNCHER_BIN"
    return
  fi
  printf '%s\n' "$HOME/.local/bin"
}

canonical_vibecrafted_home() {
  printf '%s\n' "$HOME/.vibecrafted"
}

canonical_vibecrafted_runtime_home() {
  printf '%s\n' "$HOME/.local/share/vibecrafted"
}

canonical_vibecrafted_launcher_bin() {
  printf '%s\n' "$HOME/.local/bin"
}

pause_runtime_contract_failure() {
  printf '  → fix: vibecrafted doctor --fix-legacy-bootstrap --fix-launchers\n' >&2
  if [[ "${VIBECRAFTED_INSTALL_NONINTERACTIVE:-0}" == "1" ]] || ! is_interactive_session; then
    return
  fi
  printf 'Press Enter to continue, or Ctrl-C to abort: ' >&2
  read -r _ || true
}

enforce_runtime_root_contract() {
  local expected_store expected_runtime expected_launcher
  local resolved_store resolved_runtime resolved_launcher
  local failed=0

  expected_store="$(canonical_vibecrafted_home)"
  expected_runtime="$(canonical_vibecrafted_runtime_home)"
  expected_launcher="$(canonical_vibecrafted_launcher_bin)"

  resolved_store="$(default_vibecrafted_home)"
  resolved_runtime="$(default_vibecrafted_runtime_home)"
  resolved_launcher="$(default_vibecrafted_launcher_bin)"

  if [[ "$resolved_store" != "$expected_store" ]]; then
    printf '✗ store root drift: %s ≠ %s\n' "$resolved_store" "$expected_store" >&2
    failed=1
  fi

  if [[ "$resolved_runtime" != "$expected_runtime" ]]; then
    printf '✗ runtime root drift: %s ≠ %s\n' "$resolved_runtime" "$expected_runtime" >&2
    failed=1
  fi

  if [[ "$resolved_launcher" != "$expected_launcher" ]]; then
    printf '✗ launcher root drift: %s ≠ %s\n' "$resolved_launcher" "$expected_launcher" >&2
    failed=1
  fi

  if [[ "$failed" == "1" ]]; then
    pause_runtime_contract_failure
    return 1
  fi

  return 0
}
# <<< scripts/lib/runtime-roots.sh

bootstrap_next_step() {
  if [[ "$target" == "vibecrafted" && "$use_gui" == "1" ]]; then
    printf '%s\n' "launch the guided installer UI"
    return
  fi

  if [[ "$target" == "vibecrafted" ]] && ! is_interactive_session; then
    printf '%s\n' "run the compact installer"
    return
  fi

  if [[ "$target" == "vibecrafted" ]]; then
    printf '%s\n' "run the terminal-native installer wizard"
    return
  fi

  printf "run make target '%s'\n" "$target"
}

prompt_attended_consent() {
  local source_description next_step response

  [[ "$auto_yes" == "1" ]] && return 0
  has_attended_tty || return 0

  if [[ -n "$archive_file" ]]; then
    source_description="unpack"
  else
    source_description="download"
  fi
  next_step="$(bootstrap_next_step)"

  {
    printf '\n'
    printf '⚒ 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. → %s\n' "$current_link"
    printf '  %s · verify · transact · %s\n' "$source_description" "$next_step"
  } > /dev/tty

  while true; do
    printf 'Proceed? [y/N] ' > /dev/tty
    if ! IFS= read -r response < /dev/tty; then
      printf '\nCancelled.\n' > /dev/tty
      exit 1
    fi
    case "$response" in
      [yY]|[yY][eE][sS])
        printf '\n' > /dev/tty
        return 0
        ;;
      ""|[nN]|[nN][oO])
        printf '\nCancelled.\n' > /dev/tty
        exit 0
        ;;
      *)
        printf 'Please answer yes or no.\n' > /dev/tty
        ;;
    esac
  done
}

vibecrafted_home="$(default_vibecrafted_home)"
export VIBECRAFTED_HOME="$vibecrafted_home"
vibecrafted_runtime_home="$(default_vibecrafted_runtime_home)"
export VIBECRAFTED_RUNTIME_HOME="$vibecrafted_runtime_home"
default_tools_dir="$(default_vibecrafted_tools_home)"
default_ref="${VIBECRAFTED_REF:-main}"

ref="$default_ref"
archive_url=""
archive_file=""
tools_dir="$default_tools_dir"
target="vibecrafted"
use_gui=0
auto_yes=0
runtime="none"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gui)
      use_gui=1
      ;;
    --yes|-y)
      auto_yes=1
      ;;
    --runtime)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --runtime"
      runtime="$1"
      ;;
    --ref)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --ref"
      ref="$1"
      ;;
    --archive-url)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --archive-url"
      archive_url="$1"
      ;;
    --archive-file)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --archive-file"
      archive_file="$1"
      ;;
    --tools-dir)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --tools-dir"
      tools_dir="$1"
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      target="$1"
      ;;
  esac
  shift
done

case "$target" in
  vibecrafted)
    target="vibecrafted"
    ;;
esac

if [[ -n "$archive_url" && -n "$archive_file" ]]; then
  die "Use either --archive-url or --archive-file, not both"
fi

if [[ "$use_gui" == "1" && "$target" != "vibecrafted" ]]; then
  die "--gui can only be used with the default vibecrafted install target"
fi

case "$runtime" in
  none|wezterm|vc-apprt|locterm|microsandbox)
    ;;
  vc_apprt|vc-)
    runtime="vc-apprt"
    ;;
  *)
    die "Unknown runtime horse: $runtime (expected wezterm, vc-apprt, locterm, microsandbox, none)"
    ;;
esac

enforce_runtime_root_contract || exit 1

if [[ -z "$archive_url" && -z "$archive_file" ]]; then
  # Resolve latest version from the channel manifest instead of hard-pinning.
  channel_url="https://vibecrafted.io/channel/${ref}.json"
  resolved_url=""
  if command -v curl >/dev/null 2>&1; then
    resolved_url="$(curl -fsSL "$channel_url" 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('archive_url',''))" 2>/dev/null)" || true
  fi
  if [[ -n "$resolved_url" ]]; then
    archive_url="$resolved_url"
    vinfo "Resolved from channel ($ref): $archive_url"
  else
    # Raw GitHub source archives do not carry the writer-bound v2 distribution
    # tree receipt.  Minting one after download would let candidate bytes attest
    # to themselves, so this legacy path is deliberately closed.  Release
    # authentication remains a named W4 blocker while signature fetch is soft.
    die "Channel manifest has no archive_url; refusing the untrusted raw GitHub fallback (W4 release authentication blocker)"
  fi
fi

detect_platform
detect_linux_distro
platform_banner

if [[ "$PLATFORM_OS" == "unsupported" ]]; then
  info ""
  info "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. ships macOS, Linux and WSL2 paths."
  info "There is no native Windows build; on Windows the installer runs"
  info "inside WSL2. Install WSL2 once, then bootstrap inside it:"
  info "    wsl --install"
  info "    wsl bash -c 'curl -fsSL https://vibecrafted.io/install.sh | bash'"
  info "Full per-platform matrix: docs/INSTALL.md"
  die "Unsupported platform: $(uname -s). Re-run inside WSL2."
fi

case "$runtime:$PLATFORM_OS" in
  none:*|wezterm:macos|wezterm:linux|wezterm:wsl|vc-apprt:macos|vc-apprt:linux|locterm:macos|microsandbox:macos|microsandbox:linux)
    ;;
  locterm:*)
    die "locterm is macOS-only, try --runtime wezterm or --runtime microsandbox"
    ;;
  vc-apprt:*)
    die "vc-apprt supports macOS and Linux only, try --runtime wezterm"
    ;;
  microsandbox:*)
    die "microsandbox requires macOS HVF or Linux KVM, try --runtime wezterm"
    ;;
  *)
    die "Unsupported platform '$PLATFORM_OS' for runtime '$runtime'"
    ;;
esac

# Pre-flight tool check — ALL missing tools are reported at once with ONE
# copy-pasteable install hint for the detected platform. A stranger on a
# fresh machine must never pay one full rerun per missing tool (observed:
# make → rerun → python3 → rerun → git discovered mid-install at phase 4).
preflight_require_all() {
  local missing=()
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
  done
  [[ ${#missing[@]} -eq 0 ]] && return 0
  printf 'Error: missing required tools: %s\n' "${missing[*]}" >&2
  preflight_pkg_hint "${missing[*]}"
  exit 1
}

# git is consumed later by install-tools-held; without it the install used
# to die mid-flight AFTER three green phases — it belongs in pre-flight.
preflight_tools=(tar make python3 git)
if [[ -z "$archive_file" ]]; then
  preflight_tools+=(curl)
fi
preflight_require_all "${preflight_tools[@]}"
bootstrap_python="$(command -v python3)"
[[ -n "$bootstrap_python" && -f "$bootstrap_python" ]] \
  || die "Could not resolve the bootstrap-owned Python interpreter"
export VIBECRAFTED_TOOLS_HOME="$tools_dir"
if [[ -n "$archive_file" ]]; then
  [[ -f "$archive_file" ]] || die "Archive file not found: $archive_file"
fi

current_link="$tools_dir/vibecrafted-current"

prompt_attended_consent

mkdir -p "$tools_dir"

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-bootstrap.XXXXXX")"
trap 'rm -rf -- "$tmpdir"' EXIT

# Candidate commands must return through this shell so the EXIT trap can remove
# the private verified payload. Publication is owned by install-bundle-tools
# under the installer lease; the bootstrap itself never writes `current`.
run_candidate_command() {
  local status=0
  "$@" || status=$?
  exit "$status"
}

extract_root="$tmpdir/extract"
mkdir -p "$extract_root"

verify_signature() {
  local file="$1" base_url="$2"
  local sig_file="${file}.sig"
  local pub_file="$tmpdir/vibecrafted-signing.pub"
  local sums_file="$tmpdir/SHA256SUMS"

  if ! curl -fsSL "${base_url}/vibecrafted-signing.pub" -o "$pub_file" 2>/dev/null; then
    info "  [warn] Could not fetch signing key — skipping signature verification"
    return 0
  fi
  if ! curl -fsSL "${base_url}/SHA256SUMS" -o "$sums_file" 2>/dev/null; then
    info "  [warn] Could not fetch SHA256SUMS — skipping checksum verification"
    return 0
  fi

  local expected actual
  expected="$(grep "$(basename "$file")" "$sums_file" | awk '{print $1}')"
  actual="$(shasum -a 256 "$file" 2>/dev/null || sha256sum "$file" 2>/dev/null)"
  actual="${actual%% *}"
  if [[ -n "$expected" && "$actual" != "$expected" ]]; then
    die "SHA256 mismatch for $(basename "$file"): expected $expected, got $actual"
  fi
  [[ -n "$expected" ]] && vinfo "  SHA256 ✓"

  if curl -fsSL "${base_url}/$(basename "$sig_file")" -o "$sig_file" 2>/dev/null; then
    if openssl dgst -sha256 -verify "$pub_file" -signature "$sig_file" "$file" >/dev/null 2>&1; then
      vinfo "  Signature ✓  (Developer ID, team MW223P3NPX)"
    else
      die "Signature verification FAILED for $(basename "$file")"
    fi
  else
    info "  [warn] No .sig file found — skipping signature verification"
  fi
}

local_archive="$tmpdir/vibecrafted-input.tar.gz"
if [[ -n "$archive_file" ]]; then
  info "Unpacking the local snapshot…"
  vinfo "  archive: $archive_file"
  snapshot_local_archive "$archive_file" "$local_archive" \
    || die "Could not make a stable private copy of the local archive"
else
  info "Fetching vibecrafted ($ref)…"
  vinfo "  source: $archive_url"
  curl -fsSL "$archive_url" -o "$local_archive"

  base_url="${archive_url%/*}"
  verify_signature "$local_archive" "$base_url"
fi

bootstrap_receipt="$(bootstrap_integrity_preflight archive "$local_archive")" \
  || die "Archive failed the bootstrap-owned source-provenance v2 preflight"
IFS=$'\t' read -r archive_root_name expected_carrier_sha expected_owner \
  expected_revision expected_tree_sha expected_entry_count <<< "$bootstrap_receipt"
[[ -n "$archive_root_name" && -n "$expected_carrier_sha" \
   && -n "$expected_owner" && -n "$expected_revision" \
   && -n "$expected_tree_sha" && -n "$expected_entry_count" ]] \
  || die "Archive returned an incomplete bootstrap integrity receipt"

extract_tarball "$local_archive" "$extract_root"
source_dir="$extract_root/$archive_root_name"
[[ -d "$source_dir" && ! -L "$source_dir" ]] \
  || die "Could not find the sole extracted source directory"
bootstrap_integrity_preflight tree "$source_dir" \
  "$expected_carrier_sha" "$expected_owner" "$expected_revision" \
  "$expected_tree_sha" "$expected_entry_count" \
  || die "Extracted source failed the bootstrap-owned integrity recheck"

candidate_root="$tmpdir/candidate"
manifest_helper="$source_dir/scripts/distribution_manifest.py"
[[ -f "$manifest_helper" ]] || die "Distribution manifest missing: $manifest_helper"

stage_args=(
  stage
  --source "$source_dir"
  --destination "$candidate_root"
  --mirror
  --require-source-provenance
)
"$bootstrap_python" -I -S "$manifest_helper" "${stage_args[@]}" >/dev/null
bootstrap_integrity_preflight tree "$candidate_root" \
  "$expected_carrier_sha" "$expected_owner" "$expected_revision" \
  "$expected_tree_sha" "$expected_entry_count" \
  || die "Staged candidate failed the bootstrap-owned integrity recheck"

# Read canonical VERSION file from the verified candidate for the post-install banner.
# The repo ships VERSION at the root; fall back to 'unknown' if absent (e.g. custom tarballs).
_installed_version=""
if [[ -f "$candidate_root/VERSION" ]]; then
  _installed_version="$(tr -d '[:space:]' < "$candidate_root/VERSION" 2>/dev/null || true)"
fi
[[ -n "$_installed_version" ]] || _installed_version="unknown"

# Section truth line: the payload is verified, but publication has not happened
# yet. The installer owns that transition under its cross-process lease.
info "✓ Verified vibecrafted $_installed_version candidate; transactional install is next"

post_install_banner() {
  # The default view already told the candidate truth in one line; the full
  # banner is detail and lives behind VERBOSE=1.
  [[ "${VERBOSE:-0}" == "1" ]] || return 0
  printf '\n'
  info "---------------------------------------------------------------"
  info " Candidate: vibecrafted $_installed_version"
  info " Channel:   tarball"
  info ""
  info " Update:  vibecrafted update"
  info " Health:  vibecrafted doctor"
  info "---------------------------------------------------------------"
}

if [[ "$target" == "vibecrafted" && "$use_gui" == "1" ]]; then
  gui_installer="$candidate_root/scripts/installer_gui.py"
  [[ -f "$gui_installer" ]] || die "Guided installer not found: $gui_installer"
  post_install_banner
  info "▸ Launching the guided installer (browser UI)…"
  vinfo "  python3 $gui_installer --source $candidate_root"
  export VIBECRAFTED_RUNTIME="$runtime"
  run_candidate_command python3 "$gui_installer" --source "$candidate_root"
fi

if [[ "$target" == "vibecrafted" ]] && ! is_interactive_session; then
  # Non-TTY public installs use the same automation lane as local source
  # installs. Hand the private verified candidate to the manifest-owned
  # installer; it publishes under the installer lease.
  for _p in "$HOME/.local/bin" "${tools_dir}/node/bin"; do
    case ":${PATH}:" in
      *":${_p}:"*) ;;
      *) [[ -d "$_p" ]] && export PATH="${_p}:${PATH}" ;;
    esac
  done

  export VIBECRAFTED_RUNTIME="$runtime"
  run_candidate_command make --no-print-directory -C "$candidate_root" install-auto RUNTIME="$runtime"
fi

# Interactive terminal session: default target is the built-in
# vetcoders-installer sequential runner, executed out of the candidate's
# own scripts/installer/ sub-package via `uv run --project`. The browser
# GUI is opt-in via `--gui` (handled above). Other make targets still fall
# through to the Makefile.
if [[ "$target" == "vibecrafted" ]]; then
  manifest="$candidate_root/install.toml"
  installer_dir="$candidate_root/scripts/installer"
  [[ -f "$manifest" ]] || die "Install manifest not found: $manifest"
  [[ -d "$installer_dir" ]] || die "Built-in installer package not found: $installer_dir"

  # Make sure user-local binaries (cargo, .local) are visible to the installer's
  # subprocesses — otherwise tools installed outside PATH won't be detected.
  for _p in "$HOME/.local/bin" "${tools_dir}/node/bin"; do
    case ":${PATH}:" in
      *":${_p}:"*) ;;
      *) [[ -d "$_p" ]] && export PATH="${_p}:${PATH}" ;;
    esac
  done

  if ! command -v uv >/dev/null 2>&1; then
    info "Bootstrapping uv (one-time setup)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh \
      || die "Failed to bootstrap uv"
    # shellcheck disable=SC1090
    # shellcheck disable=SC1091
    [[ -f "$HOME/.local/bin/env" ]] && source "$HOME/.local/bin/env"
    export PATH="$HOME/.local/bin:$PATH"
  fi

  post_install_banner
  info "▸ Opening the 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. installer…"
  vinfo "  uv run --project $installer_dir vetcoders-installer $manifest"
  export VIBECRAFTED_RUNTIME="$runtime"
  run_candidate_command uv run --project "$installer_dir" --quiet vetcoders-installer "$manifest"
fi

post_install_banner
info "▸ Running make ${target}…"
vinfo "  make --no-print-directory -C $candidate_root $target"

run_candidate_command make --no-print-directory -C "$candidate_root" "$target"
