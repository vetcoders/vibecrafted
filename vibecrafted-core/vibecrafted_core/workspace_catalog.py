"""Canonical Vibecrafted Workspace identity and catalog (Cut A).

The control plane under ``VIBECRAFTED_HOME`` is the sole durable writer of
workspace identity. Server/API and vc-frame integrations are readers only.

Identity model
--------------
- ``workspace_id`` — explicit UUID for one durable logical workspace.
  Never derived from root path; the same root may host multiple workspaces.
- ``vibecrafted_session_id`` — durable logical session under a workspace.
- ``workspace_instance_id`` — concrete materialization bound to a ``build_id``.
- ``run_id`` — concrete execution belonging to workspace + vibecrafted session.

Ids are *minted* as UUIDv7 (:func:`new_uuid7`) but *accepted* version-agnostically
(:func:`require_uuid`): the live catalog legitimately mixes v4 and v7. Readers
must never validate, filter, or sort on the UUID version — use ``created_at``
for chronology.

``agent_session_id``, ``runtime_session_id``, and vc-frame/Zellij session
names remain subordinate technical identifiers and are not renamed here.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .control_plane import control_plane_home
from .runtime_paths import read_version_file

CATALOG_SCHEMA = "vibecrafted.workspace-catalog.v1"
WORKSPACE_RECORD_SCHEMA = "vibecrafted.workspace.v1"
INSTANCE_SCHEMA = "vibecrafted.workspace-instance.v1"
BUILD_ID_SCHEMA = "vibecrafted.build-id.v1"
SESSION_RECORD_SCHEMA = "vibecrafted.workspace-session.v1"
SNAPSHOT_MANIFEST_SCHEMA = "vibecrafted.workspace-snapshot-manifest.v1"
MIGRATION_REPORT_SCHEMA = "vibecrafted.workspace-migration-report.v1"

WORKSPACE_STATUS_ACTIVE = "active"
WORKSPACE_STATUS_BURIED = "buried"
INSTANCE_STATUS_LIVE = "live"
INSTANCE_STATUS_STALE = "stale"
INSTANCE_STATUS_DETACHED = "detached"
RUNTIME_SESSION_STATES = frozenset({"live", "dead", "missing"})

ENV_WORKSPACE_ID = "VIBECRAFTED_WORKSPACE_ID"
ENV_VIBECRAFTED_SESSION_ID = "VIBECRAFTED_SESSION_ID"
ENV_WORKSPACE_INSTANCE_ID = "VIBECRAFTED_WORKSPACE_INSTANCE_ID"
ENV_BUILD_ID = "VIBECRAFTED_BUILD_ID"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_UUID7_LOCK = threading.Lock()
_UUID7_LAST_MS = -1
_UUID7_COUNTER = 0


class WorkspaceCatalogError(RuntimeError):
    """Unsafe or invalid workspace-catalog operation."""


class WorkspaceNotFound(WorkspaceCatalogError):
    """Referenced workspace_id is not in the catalog."""


class WorkspaceInstanceBuildMismatch(WorkspaceCatalogError):
    """A live instance cannot claim ownership of a different build_id."""


# ---------------------------------------------------------------------------
# time + ids
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_uuid7_fallback() -> str:
    """Mint a monotonic RFC 9562 UUIDv7 on Python versions without uuid.uuid7."""

    global _UUID7_COUNTER, _UUID7_LAST_MS
    timestamp_ms = time.time_ns() // 1_000_000
    with _UUID7_LOCK:
        if timestamp_ms > _UUID7_LAST_MS:
            counter = int.from_bytes(os.urandom(10), "big") & ((1 << 74) - 1)
        else:
            timestamp_ms = _UUID7_LAST_MS
            counter = (_UUID7_COUNTER + 1) & ((1 << 74) - 1)
            if counter == 0:
                timestamp_ms += 1
                counter = int.from_bytes(os.urandom(10), "big") & ((1 << 74) - 1)
        _UUID7_LAST_MS = timestamp_ms
        _UUID7_COUNTER = counter

    rand_a = counter >> 62
    rand_b = counter & ((1 << 62) - 1)
    value = (
        ((timestamp_ms & ((1 << 48) - 1)) << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return str(uuid.UUID(int=value))


def new_uuid7() -> str:
    """Mint a canonical UUIDv7 string on every supported Python runtime."""

    factory = getattr(uuid, "uuid7", None)
    if callable(factory):
        return str(factory())
    return _new_uuid7_fallback()


def is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    raw = value.strip()
    if not _UUID_RE.match(raw):
        return False
    try:
        return str(uuid.UUID(raw)) == raw.lower() or str(uuid.UUID(raw)) == raw
    except ValueError:
        return False


def require_uuid(value: object, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not is_uuid(raw):
        raise WorkspaceCatalogError(f"{field_name} must be a canonical UUID")
    return str(uuid.UUID(raw))


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def workspaces_dir() -> Path:
    return control_plane_home() / "workspaces"


def catalog_path() -> Path:
    return workspaces_dir() / "catalog.json"


def catalog_lock_path() -> Path:
    return workspaces_dir() / ".catalog.lock"


def instances_dir() -> Path:
    return workspaces_dir() / "instances"


def instance_path(workspace_instance_id: str) -> Path:
    return instances_dir() / f"{workspace_instance_id}.json"


def sessions_dir() -> Path:
    return workspaces_dir() / "sessions"


def workspace_session_path(vibecrafted_session_id: str) -> Path:
    session_id = require_uuid(
        vibecrafted_session_id, field_name="vibecrafted_session_id"
    )
    return sessions_dir() / f"{session_id}.json"


def migration_report_path() -> Path:
    return workspaces_dir() / "migration_report.json"


def snapshot_manifests_dir() -> Path:
    return workspaces_dir() / "snapshot_manifests"


# ---------------------------------------------------------------------------
# durable atomic IO (catalog-local; mirrors control-plane discipline)
# ---------------------------------------------------------------------------


def _secure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    meta = path.lstat()
    if not stat.S_ISDIR(meta.st_mode):
        raise NotADirectoryError(f"not a directory: {path}")
    if meta.st_uid != os.getuid():
        raise PermissionError(f"directory not owned by current user: {path}")
    if meta.st_mode & 0o022:
        raise PermissionError(f"directory must not be group/world writable: {path}")
    return path


def _fsync_dir(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    parent = _secure_dir(path.parent)
    data = (
        json.dumps(dict(payload), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = -1
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        written = os.write(fd, data)
        if written != len(data):
            raise OSError(errno.EIO, f"short write to {tmp}")
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
        _fsync_dir(parent)
    except BaseException:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


@contextlib.contextmanager
def _catalog_lock(*, exclusive: bool) -> Iterator[None]:
    home = _secure_dir(workspaces_dir())
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(catalog_lock_path(), flags, 0o600)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode):
            raise OSError(errno.EINVAL, "workspace catalog lock is not a regular file")
        if meta.st_uid != os.getuid():
            raise PermissionError("workspace catalog lock is not owned by current user")
        if meta.st_mode & 0o022:
            raise PermissionError(
                "workspace catalog lock must not be group/world writable"
            )
        op = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(fd, op)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    _fsync_dir(home)


# ---------------------------------------------------------------------------
# build_id
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildId:
    """Precise build identity: commit + dirty digest + package version."""

    git_commit: str
    dirty: bool
    dirty_digest: str
    package_version: str
    root: str

    @property
    def rendered(self) -> str:
        dirty_part = f"+dirty:{self.dirty_digest[:12]}" if self.dirty else ""
        commit = self.git_commit[:12] if self.git_commit else "nogit"
        version = self.package_version or "unknown"
        return f"git:{commit}{dirty_part}@v{version}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": BUILD_ID_SCHEMA,
            "git_commit": self.git_commit,
            "dirty": self.dirty,
            "dirty_digest": self.dirty_digest,
            "package_version": self.package_version,
            "root": self.root,
            "rendered": self.rendered,
        }

    @classmethod
    def from_payload(cls, payload: object) -> BuildId:
        if not isinstance(payload, Mapping):
            raise WorkspaceCatalogError("build_id payload is invalid")
        git_commit = str(payload.get("git_commit") or "")
        dirty = bool(payload.get("dirty"))
        dirty_digest = str(payload.get("dirty_digest") or "")
        package_version = str(payload.get("package_version") or "unknown")
        root = str(payload.get("root") or "")
        return cls(
            git_commit=git_commit,
            dirty=dirty,
            dirty_digest=dirty_digest,
            package_version=package_version,
            root=root,
        )

    def matches(self, other: BuildId) -> bool:
        return (
            self.git_commit == other.git_commit
            and self.dirty == other.dirty
            and self.dirty_digest == other.dirty_digest
            and self.package_version == other.package_version
        )


def _dirty_worktree_digest(root: Path, *, porcelain: bytes, git_commit: str) -> str:
    """Hash tracked diffs plus untracked path/type/content for one dirty checkout."""

    digest = hashlib.sha256()
    digest.update(b"vibecrafted-dirty-build-v1\0status\0")
    digest.update(len(porcelain).to_bytes(8, "big"))
    digest.update(porcelain)

    diff_commands: tuple[list[str], ...]
    if git_commit:
        diff_commands = (
            [
                "git",
                "-C",
                str(root),
                "diff",
                "--binary",
                "--no-ext-diff",
                "HEAD",
                "--",
            ],
        )
    else:
        diff_commands = (
            ["git", "-C", str(root), "diff", "--binary", "--no-ext-diff", "--cached"],
            ["git", "-C", str(root), "diff", "--binary", "--no-ext-diff"],
        )
    for command in diff_commands:
        proc = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=20,
        )
        if proc.returncode != 0:
            raise WorkspaceCatalogError(
                "cannot compute exact dirty build_id: git diff failed"
            )
        digest.update(b"diff\0")
        digest.update(len(proc.stdout).to_bytes(8, "big"))
        digest.update(proc.stdout)

    untracked_proc = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=False,
        capture_output=True,
        timeout=20,
    )
    if untracked_proc.returncode != 0:
        raise WorkspaceCatalogError(
            "cannot compute exact dirty build_id: untracked inventory failed"
        )
    for raw_path in sorted(item for item in untracked_proc.stdout.split(b"\0") if item):
        path = root / os.fsdecode(raw_path)
        digest.update(b"untracked\0")
        digest.update(len(raw_path).to_bytes(8, "big"))
        digest.update(raw_path)
        try:
            meta = path.lstat()
            digest.update(meta.st_mode.to_bytes(4, "big"))
            if stat.S_ISLNK(meta.st_mode):
                content = os.fsencode(os.readlink(path))
                digest.update(len(content).to_bytes(8, "big"))
                digest.update(content)
            elif stat.S_ISREG(meta.st_mode):
                digest.update(meta.st_size.to_bytes(8, "big"))
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
            else:
                digest.update(b"non-regular")
        except OSError as exc:
            raise WorkspaceCatalogError(
                f"cannot compute exact dirty build_id for {raw_path!r}: {exc}"
            ) from exc
    return digest.hexdigest()


def compute_build_id(root: str | Path | None = None) -> BuildId:
    """Compute build_id for a checkout root (commit + dirty digest + version)."""

    resolved = Path(root or os.getcwd()).expanduser().resolve()
    git_commit = ""
    dirty = False
    dirty_digest = ""
    try:
        commit_proc = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if commit_proc.returncode == 0:
            git_commit = commit_proc.stdout.strip()
        status_proc = subprocess.run(
            [
                "git",
                "-C",
                str(resolved),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            check=False,
            capture_output=True,
            timeout=10,
        )
        if status_proc.returncode == 0:
            porcelain = status_proc.stdout
            dirty = bool(porcelain.strip())
            if dirty:
                dirty_digest = _dirty_worktree_digest(
                    resolved,
                    porcelain=porcelain,
                    git_commit=git_commit,
                )
        elif git_commit:
            raise WorkspaceCatalogError(
                "cannot compute exact build_id: git status failed"
            )
    except (OSError, subprocess.SubprocessError) as exc:
        if git_commit or dirty:
            raise WorkspaceCatalogError(
                f"cannot compute exact dirty build_id: {exc}"
            ) from exc
    package_version = read_version_file(resolved)
    if package_version == "unknown":
        # Prefer the installed package version when the root is not a checkout.
        try:
            from . import __version__ as installed_version

            package_version = installed_version
        except Exception:  # noqa: BLE001 — version is advisory only
            package_version = "unknown"
    return BuildId(
        git_commit=git_commit,
        dirty=dirty,
        dirty_digest=dirty_digest,
        package_version=str(package_version or "unknown"),
        root=str(resolved),
    )


# ---------------------------------------------------------------------------
# catalog records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceRecord:
    workspace_id: str
    display_label: str
    canonical_root: str
    status: str
    created_at: str
    updated_at: str
    buried_at: str | None = None
    recovered_at: str | None = None
    notes: str = ""
    migration: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": WORKSPACE_RECORD_SCHEMA,
            "workspace_id": self.workspace_id,
            "display_label": self.display_label,
            "canonical_root": self.canonical_root,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "buried_at": self.buried_at,
            "recovered_at": self.recovered_at,
            "notes": self.notes,
            "migration": dict(self.migration),
        }

    @classmethod
    def from_payload(cls, payload: object) -> WorkspaceRecord:
        if not isinstance(payload, Mapping):
            raise WorkspaceCatalogError("workspace record is invalid")
        workspace_id = require_uuid(
            payload.get("workspace_id"), field_name="workspace_id"
        )
        status = str(payload.get("status") or "")
        if status not in {WORKSPACE_STATUS_ACTIVE, WORKSPACE_STATUS_BURIED}:
            raise WorkspaceCatalogError(f"invalid workspace status: {status!r}")
        display_label = str(payload.get("display_label") or "").strip() or "workspace"
        canonical_root = str(payload.get("canonical_root") or "").strip()
        created_at = str(payload.get("created_at") or "")
        updated_at = str(payload.get("updated_at") or created_at)
        buried_at = payload.get("buried_at")
        recovered_at = payload.get("recovered_at")
        notes = str(payload.get("notes") or "")
        raw_migration = payload.get("migration")
        migration = (
            {str(key): value for key, value in raw_migration.items()}
            if isinstance(raw_migration, dict)
            else {}
        )
        return cls(
            workspace_id=workspace_id,
            display_label=display_label,
            canonical_root=canonical_root,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            buried_at=str(buried_at) if buried_at else None,
            recovered_at=str(recovered_at) if recovered_at else None,
            notes=notes,
            migration=dict(migration),
        )


@dataclass(frozen=True)
class WorkspaceCatalog:
    workspaces: dict[str, WorkspaceRecord]
    selected_workspace_id: str | None
    updated_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": CATALOG_SCHEMA,
            "updated_at": self.updated_at,
            "selected_workspace_id": self.selected_workspace_id,
            "workspaces": {
                wid: record.to_payload()
                for wid, record in sorted(self.workspaces.items())
            },
        }

    @classmethod
    def empty(cls) -> WorkspaceCatalog:
        return cls(workspaces={}, selected_workspace_id=None, updated_at=_now_iso())

    @classmethod
    def from_payload(cls, payload: object) -> WorkspaceCatalog:
        if not isinstance(payload, Mapping):
            raise WorkspaceCatalogError("workspace catalog payload is invalid")
        if payload.get("schema") != CATALOG_SCHEMA:
            raise WorkspaceCatalogError(
                f"unsupported workspace catalog schema: {payload.get('schema')!r}"
            )
        raw_workspaces = payload.get("workspaces")
        if not isinstance(raw_workspaces, Mapping):
            raise WorkspaceCatalogError("workspace catalog workspaces map is invalid")
        workspaces: dict[str, WorkspaceRecord] = {}
        for key, value in raw_workspaces.items():
            record = WorkspaceRecord.from_payload(value)
            if record.workspace_id != str(key):
                raise WorkspaceCatalogError("workspace map key must equal workspace_id")
            workspaces[record.workspace_id] = record
        selected = payload.get("selected_workspace_id")
        selected_id = None
        if selected not in (None, ""):
            selected_id = require_uuid(selected, field_name="selected_workspace_id")
            if selected_id not in workspaces:
                raise WorkspaceCatalogError("selected_workspace_id is not in catalog")
        return cls(
            workspaces=workspaces,
            selected_workspace_id=selected_id,
            updated_at=str(payload.get("updated_at") or _now_iso()),
        )


@dataclass(frozen=True)
class WorkspaceInstance:
    workspace_instance_id: str
    workspace_id: str
    build_id: BuildId
    vibecrafted_session_id: str | None
    status: str
    created_at: str
    updated_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": INSTANCE_SCHEMA,
            "workspace_instance_id": self.workspace_instance_id,
            "workspace_id": self.workspace_id,
            "build_id": self.build_id.to_payload(),
            "vibecrafted_session_id": self.vibecrafted_session_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_payload(cls, payload: object) -> WorkspaceInstance:
        if not isinstance(payload, Mapping):
            raise WorkspaceCatalogError("workspace instance payload is invalid")
        if payload.get("schema") != INSTANCE_SCHEMA:
            raise WorkspaceCatalogError("workspace instance schema is invalid")
        workspace_instance_id = require_uuid(
            payload.get("workspace_instance_id"), field_name="workspace_instance_id"
        )
        workspace_id = require_uuid(
            payload.get("workspace_id"), field_name="workspace_id"
        )
        status = str(payload.get("status") or "")
        if status not in {
            INSTANCE_STATUS_LIVE,
            INSTANCE_STATUS_STALE,
            INSTANCE_STATUS_DETACHED,
        }:
            raise WorkspaceCatalogError(f"invalid instance status: {status!r}")
        session_raw = payload.get("vibecrafted_session_id")
        session_id = (
            require_uuid(session_raw, field_name="vibecrafted_session_id")
            if session_raw not in (None, "")
            else None
        )
        return cls(
            workspace_instance_id=workspace_instance_id,
            workspace_id=workspace_id,
            build_id=BuildId.from_payload(payload.get("build_id")),
            vibecrafted_session_id=session_id,
            status=status,
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
        )


@dataclass(frozen=True)
class RuntimeSessionAttachment:
    """One physical runtime incarnation attached to a logical WES session."""

    attachment_id: str
    runtime: str
    runtime_session_id: str
    state: str
    socket_dir: str
    attached_at: str
    updated_at: str
    replaces_runtime_session_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "runtime": self.runtime,
            "runtime_session_id": self.runtime_session_id,
            "state": self.state,
            "socket_dir": self.socket_dir,
            "attached_at": self.attached_at,
            "updated_at": self.updated_at,
            "replaces_runtime_session_id": self.replaces_runtime_session_id,
        }

    @classmethod
    def from_payload(cls, payload: object) -> RuntimeSessionAttachment:
        if not isinstance(payload, Mapping):
            raise WorkspaceCatalogError("runtime session attachment is invalid")
        state = str(payload.get("state") or "")
        if state not in RUNTIME_SESSION_STATES:
            raise WorkspaceCatalogError(f"invalid runtime session state: {state!r}")
        runtime = str(payload.get("runtime") or "").strip()
        runtime_session_id = str(payload.get("runtime_session_id") or "").strip()
        if not runtime or not runtime_session_id:
            raise WorkspaceCatalogError(
                "runtime and runtime_session_id are required for an attachment"
            )
        replacement = str(payload.get("replaces_runtime_session_id") or "").strip()
        return cls(
            attachment_id=require_uuid(
                payload.get("attachment_id"), field_name="attachment_id"
            ),
            runtime=runtime,
            runtime_session_id=runtime_session_id,
            state=state,
            socket_dir=str(payload.get("socket_dir") or ""),
            attached_at=str(payload.get("attached_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            replaces_runtime_session_id=replacement or None,
        )


@dataclass(frozen=True)
class WorkspaceSessionRecord:
    """WES-owned logical session with preserved physical runtime attachments."""

    session_id: str
    workspace_id: str
    workspace_instance_id: str
    created_at: str
    updated_at: str
    attachments: tuple[RuntimeSessionAttachment, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": SESSION_RECORD_SCHEMA,
            "session_id": self.session_id,
            "vibecrafted_session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "workspace_instance_id": self.workspace_instance_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "attachments": [item.to_payload() for item in self.attachments],
        }

    @classmethod
    def from_payload(cls, payload: object) -> WorkspaceSessionRecord:
        if not isinstance(payload, Mapping):
            raise WorkspaceCatalogError("workspace session record is invalid")
        if payload.get("schema") != SESSION_RECORD_SCHEMA:
            raise WorkspaceCatalogError("workspace session record schema is invalid")
        raw_attachments = payload.get("attachments") or []
        if not isinstance(raw_attachments, Sequence) or isinstance(
            raw_attachments, (str, bytes)
        ):
            raise WorkspaceCatalogError("workspace session attachments are invalid")
        session_id = require_uuid(
            payload.get("vibecrafted_session_id") or payload.get("session_id"),
            field_name="vibecrafted_session_id",
        )
        return cls(
            session_id=session_id,
            workspace_id=require_uuid(
                payload.get("workspace_id"), field_name="workspace_id"
            ),
            workspace_instance_id=require_uuid(
                payload.get("workspace_instance_id"),
                field_name="workspace_instance_id",
            ),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            attachments=tuple(
                RuntimeSessionAttachment.from_payload(item) for item in raw_attachments
            ),
        )


# ---------------------------------------------------------------------------
# worker host routing
# ---------------------------------------------------------------------------


def short_workspace_token(workspace_id: str) -> str:
    """Stable 8-char token from workspace_id for human-readable host names.

    UUIDv7 places the timestamp in the high bits, so the *leading* hex digits
    collide when two ids are minted in the same millisecond. Use the trailing
    random bits instead.
    """

    return require_uuid(workspace_id, field_name="workspace_id").replace("-", "")[-8:]


WORKER_HOST_SUFFIX = "-w"
LEGACY_WORKER_HOST_SUFFIX = " workers"
LEGACY_OPERATOR_SESSION_PREFIX = "workspace-"
_MAX_WORKER_HOST_LABEL = 24
_MAX_PLACE_SESSION_LEN = 24
_LEGACY_OPERATOR_SESSION_RE = re.compile(r"^workspace-[0-9a-f]{8}$")


def _sanitize_worker_host_label(
    display_label: str, *, max_len: int | None = None
) -> str:
    label = (display_label or "workspace").strip() or "workspace"
    label = re.sub(r"\s+", "-", label)
    label = re.sub(r"[^A-Za-z0-9._-]+", "", label) or "workspace"
    if max_len is not None and len(label) > max_len:
        label = label[:max_len].rstrip("-._") or "workspace"
    return label


def worker_host_session_name(
    *,
    workspace_id: str,
    display_label: str = "",
) -> str:
    """Workspace-bound worker host session name.

    Socket-safe token: ``{label}-{short}-w``. No spaces. The older
    ``{label}-{short} workers`` form overflowed macOS ``sockaddr_un`` (104
    bytes) on the default TMPDIR socket root and clap reported the overflow
    as ``session name must be less than 0 characters``.
    """

    label = _sanitize_worker_host_label(display_label, max_len=_MAX_WORKER_HOST_LABEL)
    return f"{label}-{short_workspace_token(workspace_id)}{WORKER_HOST_SUFFIX}"


def legacy_worker_host_session_name(
    *,
    workspace_id: str,
    display_label: str = "",
) -> str:
    """Pre-2026-08-17 host name (space + ``workers``). Kept for WES attach."""

    label = _sanitize_worker_host_label(display_label)
    return f"{label}-{short_workspace_token(workspace_id)}{LEGACY_WORKER_HOST_SUFFIX}"


def worker_host_display_label(
    *,
    workspace_id: str,
    display_label: str = "",
) -> str:
    """Human-facing label without the Zellij/session suffix."""

    label = (display_label or "workspace").strip() or "workspace"
    return f"{label} [{short_workspace_token(workspace_id)}]"


def legacy_operator_session_name(workspace_id: str) -> str:
    """Pre-2026-08-19 catalog fallback rendered as a rail label."""

    return f"{LEGACY_OPERATOR_SESSION_PREFIX}{short_workspace_token(workspace_id)}"


def is_legacy_operator_session_name(name: str) -> bool:
    """True when ``name`` is the old ``workspace-{8hex}`` catalog fallback."""

    return bool(_LEGACY_OPERATOR_SESSION_RE.fullmatch((name or "").strip()))


def _place_label_for_workspace(
    *,
    display_label: str = "",
    canonical_root: str = "",
    max_len: int = _MAX_PLACE_SESSION_LEN,
) -> str:
    raw = (
        (display_label or "").strip() or Path(canonical_root or "").name or "workspace"
    )
    return _sanitize_worker_host_label(raw, max_len=max_len)


def operator_session_name(
    workspace_id: str,
    *,
    display_label: str = "",
    catalog: WorkspaceCatalog | None = None,
) -> str:
    """Interactive place-session for the human rail.

    Uses the workspace display label (or checkout basename). A short
    workspace token is appended only when another *active* workspace
    would collide on the same sanitized label. Never includes a run_id.
    """

    wid = require_uuid(workspace_id, field_name="workspace_id")
    loaded = catalog
    record: WorkspaceRecord | None = None
    if loaded is None:
        try:
            loaded = read_catalog()
        except WorkspaceCatalogError:
            loaded = None
    if loaded is not None:
        record = loaded.workspaces.get(wid)

    label = _place_label_for_workspace(
        display_label=display_label or (record.display_label if record else ""),
        canonical_root=record.canonical_root if record else "",
    )
    collide = False
    if loaded is not None:
        for other in loaded.workspaces.values():
            if other.workspace_id == wid:
                continue
            if other.status != WORKSPACE_STATUS_ACTIVE:
                continue
            other_label = _place_label_for_workspace(
                display_label=other.display_label,
                canonical_root=other.canonical_root,
            )
            if other_label == label:
                collide = True
                break
    if not collide:
        return label
    token = short_workspace_token(wid)
    max_label = max(1, _MAX_PLACE_SESSION_LEN - 1 - len(token))
    short_label = _sanitize_worker_host_label(label, max_len=max_label)
    return f"{short_label}-{token}"


def resolve_operator_place_session(
    *,
    root: str | Path,
    env: Mapping[str, str] | None = None,
) -> str:
    """Human place-session for this checkout. Does not create workspaces."""

    environ = dict(env) if env is not None else dict(os.environ)
    try:
        identity = resolve_run_workspace_identity(
            root=root, env=environ, create_if_missing=False
        )
        return operator_session_name(
            identity.workspace_id, display_label=identity.display_label
        )
    except (WorkspaceCatalogError, WorkspaceNotFound):
        return _sanitize_worker_host_label(
            Path(root or ".").name or "vibecrafted",
            max_len=_MAX_PLACE_SESSION_LEN,
        )


# ---------------------------------------------------------------------------
# catalog lifecycle
# ---------------------------------------------------------------------------


def _load_catalog_unlocked() -> WorkspaceCatalog:
    path = catalog_path()
    if not path.exists():
        return WorkspaceCatalog.empty()
    payload = _read_json(path)
    if payload is None:
        raise WorkspaceCatalogError(
            f"workspace catalog is corrupt or unreadable: {path}"
        )
    return WorkspaceCatalog.from_payload(payload)


def _save_catalog_unlocked(catalog: WorkspaceCatalog) -> None:
    _atomic_write_json(catalog_path(), catalog.to_payload())


def read_catalog() -> WorkspaceCatalog:
    """Shared-lock read of the workspace catalog."""

    with _catalog_lock(exclusive=False):
        return _load_catalog_unlocked()


def create_workspace(
    *,
    root: str | Path,
    display_label: str = "",
    workspace_id: str | None = None,
    notes: str = "",
    select: bool = True,
) -> WorkspaceRecord:
    """Create a durable workspace. workspace_id is never derived from root."""

    resolved = Path(root).expanduser().resolve()
    label = (display_label or resolved.name or "workspace").strip()
    wid = (
        require_uuid(workspace_id, field_name="workspace_id")
        if workspace_id
        else new_uuid7()
    )
    now = _now_iso()
    record = WorkspaceRecord(
        workspace_id=wid,
        display_label=label,
        canonical_root=str(resolved),
        status=WORKSPACE_STATUS_ACTIVE,
        created_at=now,
        updated_at=now,
        notes=notes,
        migration={"source": "explicit"},
    )
    with _catalog_lock(exclusive=True):
        catalog = _load_catalog_unlocked()
        if wid in catalog.workspaces:
            raise WorkspaceCatalogError(f"workspace_id already exists: {wid}")
        workspaces = dict(catalog.workspaces)
        workspaces[wid] = record
        selected = wid if select else catalog.selected_workspace_id
        next_catalog = WorkspaceCatalog(
            workspaces=workspaces,
            selected_workspace_id=selected,
            updated_at=now,
        )
        _save_catalog_unlocked(next_catalog)
    return record


def list_workspaces(*, include_buried: bool = False) -> list[WorkspaceRecord]:
    catalog = read_catalog()
    records = list(catalog.workspaces.values())
    if not include_buried:
        records = [r for r in records if r.status == WORKSPACE_STATUS_ACTIVE]
    return sorted(records, key=lambda r: r.created_at)


def show_workspace(workspace_id: str) -> WorkspaceRecord:
    wid = require_uuid(workspace_id, field_name="workspace_id")
    catalog = read_catalog()
    record = catalog.workspaces.get(wid)
    if record is None:
        raise WorkspaceNotFound(f"workspace not found: {wid}")
    return record


def select_workspace(workspace_id: str) -> WorkspaceRecord:
    wid = require_uuid(workspace_id, field_name="workspace_id")
    now = _now_iso()
    with _catalog_lock(exclusive=True):
        catalog = _load_catalog_unlocked()
        record = catalog.workspaces.get(wid)
        if record is None:
            raise WorkspaceNotFound(f"workspace not found: {wid}")
        if record.status != WORKSPACE_STATUS_ACTIVE:
            raise WorkspaceCatalogError(
                "cannot select a buried workspace; recover it first"
            )
        next_catalog = WorkspaceCatalog(
            workspaces=catalog.workspaces,
            selected_workspace_id=wid,
            updated_at=now,
        )
        _save_catalog_unlocked(next_catalog)
    return record


def bury_workspace(workspace_id: str) -> WorkspaceRecord:
    """Hide a workspace without deleting history."""

    wid = require_uuid(workspace_id, field_name="workspace_id")
    now = _now_iso()
    with _catalog_lock(exclusive=True):
        catalog = _load_catalog_unlocked()
        record = catalog.workspaces.get(wid)
        if record is None:
            raise WorkspaceNotFound(f"workspace not found: {wid}")
        buried = WorkspaceRecord(
            workspace_id=record.workspace_id,
            display_label=record.display_label,
            canonical_root=record.canonical_root,
            status=WORKSPACE_STATUS_BURIED,
            created_at=record.created_at,
            updated_at=now,
            buried_at=now,
            recovered_at=record.recovered_at,
            notes=record.notes,
            migration=dict(record.migration),
        )
        workspaces = dict(catalog.workspaces)
        workspaces[wid] = buried
        selected = catalog.selected_workspace_id
        if selected == wid:
            selected = None
        next_catalog = WorkspaceCatalog(
            workspaces=workspaces,
            selected_workspace_id=selected,
            updated_at=now,
        )
        _save_catalog_unlocked(next_catalog)
        # Detach live instances for this workspace (do not delete history files).
        for instance in _list_instances_unlocked(workspace_id=wid):
            if instance.status == INSTANCE_STATUS_LIVE:
                _write_instance_unlocked(
                    WorkspaceInstance(
                        workspace_instance_id=instance.workspace_instance_id,
                        workspace_id=instance.workspace_id,
                        build_id=instance.build_id,
                        vibecrafted_session_id=instance.vibecrafted_session_id,
                        status=INSTANCE_STATUS_DETACHED,
                        created_at=instance.created_at,
                        updated_at=now,
                    )
                )
    return buried


def recover_workspace(workspace_id: str, *, select: bool = False) -> WorkspaceRecord:
    """Reactivate a buried logical workspace without attaching an incompatible runtime."""

    wid = require_uuid(workspace_id, field_name="workspace_id")
    now = _now_iso()
    with _catalog_lock(exclusive=True):
        catalog = _load_catalog_unlocked()
        record = catalog.workspaces.get(wid)
        if record is None:
            raise WorkspaceNotFound(f"workspace not found: {wid}")
        recovered = WorkspaceRecord(
            workspace_id=record.workspace_id,
            display_label=record.display_label,
            canonical_root=record.canonical_root,
            status=WORKSPACE_STATUS_ACTIVE,
            created_at=record.created_at,
            updated_at=now,
            buried_at=record.buried_at,
            recovered_at=now,
            notes=record.notes,
            migration=dict(record.migration),
        )
        workspaces = dict(catalog.workspaces)
        workspaces[wid] = recovered
        selected = wid if select else catalog.selected_workspace_id
        next_catalog = WorkspaceCatalog(
            workspaces=workspaces,
            selected_workspace_id=selected,
            updated_at=now,
        )
        _save_catalog_unlocked(next_catalog)
    return recovered


def selected_workspace() -> WorkspaceRecord | None:
    catalog = read_catalog()
    if not catalog.selected_workspace_id:
        return None
    return catalog.workspaces.get(catalog.selected_workspace_id)


# ---------------------------------------------------------------------------
# instances (build-bound materializations)
# ---------------------------------------------------------------------------


def _write_instance_unlocked(instance: WorkspaceInstance) -> None:
    _secure_dir(instances_dir())
    _atomic_write_json(
        instance_path(instance.workspace_instance_id), instance.to_payload()
    )


def _list_instances_unlocked(
    *, workspace_id: str | None = None
) -> list[WorkspaceInstance]:
    root = instances_dir()
    if not root.is_dir():
        return []
    out: list[WorkspaceInstance] = []
    for path in sorted(root.glob("*.json")):
        payload = _read_json(path)
        if payload is None:
            continue
        try:
            instance = WorkspaceInstance.from_payload(payload)
        except WorkspaceCatalogError:
            continue
        if workspace_id and instance.workspace_id != workspace_id:
            continue
        out.append(instance)
    return out


def list_instances(*, workspace_id: str | None = None) -> list[WorkspaceInstance]:
    with _catalog_lock(exclusive=False):
        return _list_instances_unlocked(workspace_id=workspace_id)


def materialize_instance(
    *,
    workspace_id: str,
    root: str | Path | None = None,
    vibecrafted_session_id: str | None = None,
    build_id: BuildId | None = None,
) -> WorkspaceInstance:
    """Create or refresh a live instance for workspace_id at the current build.

    An existing live instance for a *different* build is detached (never
    overwritten). A matching build reuses the instance id.
    """

    wid = require_uuid(workspace_id, field_name="workspace_id")
    resolved_root = Path(root or os.getcwd()).expanduser().resolve()
    bid = build_id or compute_build_id(resolved_root)
    session_id = (
        require_uuid(vibecrafted_session_id, field_name="vibecrafted_session_id")
        if vibecrafted_session_id
        else new_uuid7()
    )
    now = _now_iso()
    with _catalog_lock(exclusive=True):
        catalog = _load_catalog_unlocked()
        record = catalog.workspaces.get(wid)
        if record is None:
            raise WorkspaceNotFound(f"workspace not found: {wid}")
        if record.status != WORKSPACE_STATUS_ACTIVE:
            raise WorkspaceCatalogError("cannot materialize a buried workspace")

        existing = _list_instances_unlocked(workspace_id=wid)
        for instance in existing:
            if instance.status != INSTANCE_STATUS_LIVE:
                continue
            if instance.build_id.matches(bid):
                # Same build — refresh session stamp if provided.
                refreshed = WorkspaceInstance(
                    workspace_instance_id=instance.workspace_instance_id,
                    workspace_id=wid,
                    build_id=bid,
                    vibecrafted_session_id=session_id,
                    status=INSTANCE_STATUS_LIVE,
                    created_at=instance.created_at,
                    updated_at=now,
                )
                _write_instance_unlocked(refreshed)
                return refreshed
            # Different build cannot claim live ownership.
            detached = WorkspaceInstance(
                workspace_instance_id=instance.workspace_instance_id,
                workspace_id=instance.workspace_id,
                build_id=instance.build_id,
                vibecrafted_session_id=instance.vibecrafted_session_id,
                status=INSTANCE_STATUS_STALE,
                created_at=instance.created_at,
                updated_at=now,
            )
            _write_instance_unlocked(detached)

        new_instance = WorkspaceInstance(
            workspace_instance_id=new_uuid7(),
            workspace_id=wid,
            build_id=bid,
            vibecrafted_session_id=session_id,
            status=INSTANCE_STATUS_LIVE,
            created_at=now,
            updated_at=now,
        )
        _write_instance_unlocked(new_instance)
        return new_instance


def claim_live_instance(
    *,
    workspace_instance_id: str,
    expected_build_id: BuildId,
) -> WorkspaceInstance:
    """Fail closed if a caller tries to attach a live instance to the wrong build."""

    iid = require_uuid(workspace_instance_id, field_name="workspace_instance_id")
    with _catalog_lock(exclusive=True):
        payload = _read_json(instance_path(iid))
        if payload is None:
            raise WorkspaceCatalogError(f"workspace instance not found: {iid}")
        instance = WorkspaceInstance.from_payload(payload)
        if not instance.build_id.matches(expected_build_id):
            raise WorkspaceInstanceBuildMismatch(
                f"instance {iid} is bound to build "
                f"{instance.build_id.rendered!r}; cannot claim live ownership for "
                f"{expected_build_id.rendered!r}"
            )
        if instance.status != INSTANCE_STATUS_LIVE:
            raise WorkspaceCatalogError(
                f"instance {iid} is not live (status={instance.status})"
            )
        return instance


# ---------------------------------------------------------------------------
# logical session records + physical runtime attachments (WES)
# ---------------------------------------------------------------------------


def read_workspace_session(vibecrafted_session_id: str) -> WorkspaceSessionRecord:
    """Read one logical WES session and all preserved runtime incarnations."""

    path = workspace_session_path(vibecrafted_session_id)
    with _catalog_lock(exclusive=False):
        payload = _read_json(path)
        if payload is None:
            raise WorkspaceCatalogError(
                f"workspace session record not found: {vibecrafted_session_id}"
            )
        return WorkspaceSessionRecord.from_payload(payload)


def record_runtime_session_attachment(
    *,
    workspace_id: str,
    vibecrafted_session_id: str,
    workspace_instance_id: str,
    runtime: str,
    runtime_session_id: str,
    state: str,
    socket_dir: str = "",
    replaces_runtime_session_id: str | None = None,
) -> WorkspaceSessionRecord:
    """Attach or refresh a physical runtime incarnation under one WES session.

    This is the sole durable writer for vc-frame recovery evidence. A dead
    attachment is retained when a live replacement is added; no physical
    session cache is deleted or rewritten here.
    """

    wid = require_uuid(workspace_id, field_name="workspace_id")
    sid = require_uuid(vibecrafted_session_id, field_name="vibecrafted_session_id")
    iid = require_uuid(workspace_instance_id, field_name="workspace_instance_id")
    runtime_name = str(runtime or "").strip()
    physical_id = str(runtime_session_id or "").strip()
    attachment_state = str(state or "").strip()
    replacement = str(replaces_runtime_session_id or "").strip() or None
    if not runtime_name or not physical_id:
        raise WorkspaceCatalogError("runtime and runtime_session_id are required")
    if attachment_state not in RUNTIME_SESSION_STATES:
        raise WorkspaceCatalogError(
            f"invalid runtime session state: {attachment_state!r}"
        )

    now = _now_iso()
    with _catalog_lock(exclusive=True):
        catalog = _load_catalog_unlocked()
        if wid not in catalog.workspaces:
            raise WorkspaceNotFound(f"workspace not found: {wid}")
        instance_payload = _read_json(instance_path(iid))
        if instance_payload is None:
            raise WorkspaceCatalogError(f"workspace instance not found: {iid}")
        instance = WorkspaceInstance.from_payload(instance_payload)
        if instance.workspace_id != wid:
            raise WorkspaceCatalogError(
                f"workspace instance {iid} does not belong to workspace {wid}"
            )
        if instance.vibecrafted_session_id != sid:
            raise WorkspaceCatalogError(
                f"workspace instance {iid} does not belong to session {sid}"
            )

        path = workspace_session_path(sid)
        current_payload = _read_json(path)
        if current_payload is None:
            current = WorkspaceSessionRecord(
                session_id=sid,
                workspace_id=wid,
                workspace_instance_id=iid,
                created_at=now,
                updated_at=now,
                attachments=(),
            )
        else:
            current = WorkspaceSessionRecord.from_payload(current_payload)
            if current.workspace_id != wid or current.workspace_instance_id != iid:
                raise WorkspaceCatalogError(
                    "workspace session ownership does not match the requested instance"
                )

        attachments = list(current.attachments)
        match_index = next(
            (
                index
                for index, item in enumerate(attachments)
                if item.runtime == runtime_name
                and item.runtime_session_id == physical_id
                and item.socket_dir == str(socket_dir or "")
            ),
            None,
        )
        if match_index is None:
            attachments.append(
                RuntimeSessionAttachment(
                    attachment_id=new_uuid7(),
                    runtime=runtime_name,
                    runtime_session_id=physical_id,
                    state=attachment_state,
                    socket_dir=str(socket_dir or ""),
                    attached_at=now,
                    updated_at=now,
                    replaces_runtime_session_id=replacement,
                )
            )
        else:
            existing = attachments[match_index]
            attachments[match_index] = RuntimeSessionAttachment(
                attachment_id=existing.attachment_id,
                runtime=existing.runtime,
                runtime_session_id=existing.runtime_session_id,
                state=attachment_state,
                socket_dir=existing.socket_dir,
                attached_at=existing.attached_at,
                updated_at=now,
                replaces_runtime_session_id=(
                    replacement or existing.replaces_runtime_session_id
                ),
            )

        updated = WorkspaceSessionRecord(
            session_id=sid,
            workspace_id=wid,
            workspace_instance_id=iid,
            created_at=current.created_at,
            updated_at=now,
            attachments=tuple(attachments),
        )
        _atomic_write_json(path, updated.to_payload())
        return updated


# ---------------------------------------------------------------------------
# identity resolution for new runs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunWorkspaceIdentity:
    workspace_id: str
    vibecrafted_session_id: str
    workspace_instance_id: str
    build_id: BuildId
    display_label: str
    worker_host_session: str
    worker_host_display: str

    def to_meta_fields(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "vibecrafted_session_id": self.vibecrafted_session_id,
            "workspace_instance_id": self.workspace_instance_id,
            "build_id": self.build_id.to_payload(),
            "workspace_display_label": self.display_label,
            "worker_host_session": self.worker_host_session,
            "worker_host_display": self.worker_host_display,
        }

    def to_env(self) -> dict[str, str]:
        return {
            ENV_WORKSPACE_ID: self.workspace_id,
            ENV_VIBECRAFTED_SESSION_ID: self.vibecrafted_session_id,
            ENV_WORKSPACE_INSTANCE_ID: self.workspace_instance_id,
            ENV_BUILD_ID: self.build_id.rendered,
        }


def resolve_run_workspace_identity(
    *,
    root: str | Path,
    env: Mapping[str, str] | None = None,
    create_if_missing: bool = True,
) -> RunWorkspaceIdentity:
    """Resolve the workspace identity for a new run.

    Preference order for workspace_id:
    1. ``VIBECRAFTED_WORKSPACE_ID`` env
    2. catalog selected workspace
    3. create a new explicit workspace for this root (when allowed)
    """

    environ = dict(env) if env is not None else dict(os.environ)
    resolved_root = Path(root).expanduser().resolve()
    bid = compute_build_id(resolved_root)

    env_wid = str(environ.get(ENV_WORKSPACE_ID) or "").strip()
    catalog = read_catalog()
    record: WorkspaceRecord | None = None
    root_key = _canonical_root_key(str(resolved_root))
    if env_wid:
        wid = require_uuid(env_wid, field_name=ENV_WORKSPACE_ID)
        record = catalog.workspaces.get(wid)
        if record is None:
            raise WorkspaceNotFound(
                f"{ENV_WORKSPACE_ID}={wid} is not present in the catalog"
            )
        if record.status != WORKSPACE_STATUS_ACTIVE:
            raise WorkspaceCatalogError(
                f"workspace {wid} is buried; recover it before launching runs"
            )
    else:
        # Prefer the unique active workspace whose canonical_root matches this
        # root. Selected workspace only wins when it is rooted here — never
        # leak a selected workspace from a different checkout into this host.
        matches = [
            r
            for r in catalog.workspaces.values()
            if r.status == WORKSPACE_STATUS_ACTIVE
            and _canonical_root_key(r.canonical_root) == root_key
        ]
        if len(matches) == 1:
            record = matches[0]
        elif len(matches) > 1:
            selected = catalog.selected_workspace_id
            selected_match = next(
                (r for r in matches if r.workspace_id == selected), None
            )
            if selected_match is not None:
                record = selected_match
            else:
                raise WorkspaceCatalogError(
                    "multiple active workspaces share this root; set "
                    f"{ENV_WORKSPACE_ID} or select one explicitly"
                )
        elif catalog.selected_workspace_id:
            selected_rec = catalog.workspaces.get(catalog.selected_workspace_id)
            if (
                selected_rec is not None
                and selected_rec.status == WORKSPACE_STATUS_ACTIVE
                and _canonical_root_key(selected_rec.canonical_root) == root_key
            ):
                record = selected_rec

    if record is None:
        if not create_if_missing:
            raise WorkspaceCatalogError(
                "no active workspace selected; create or select one first"
            )
        record = create_workspace(
            root=resolved_root,
            display_label=resolved_root.name or "workspace",
            select=True,
        )

    env_session = str(environ.get(ENV_VIBECRAFTED_SESSION_ID) or "").strip()
    session_id = (
        require_uuid(env_session, field_name=ENV_VIBECRAFTED_SESSION_ID)
        if env_session
        else new_uuid7()
    )
    instance = materialize_instance(
        workspace_id=record.workspace_id,
        root=resolved_root,
        vibecrafted_session_id=session_id,
        build_id=bid,
    )
    host = worker_host_session_name(
        workspace_id=record.workspace_id,
        display_label=record.display_label,
    )
    display = worker_host_display_label(
        workspace_id=record.workspace_id,
        display_label=record.display_label,
    )
    return RunWorkspaceIdentity(
        workspace_id=record.workspace_id,
        vibecrafted_session_id=instance.vibecrafted_session_id or session_id,
        workspace_instance_id=instance.workspace_instance_id,
        build_id=bid,
        display_label=record.display_label,
        worker_host_session=host,
        worker_host_display=display,
    )


def resolve_worker_host_session(
    *,
    root: str | Path,
    env: Mapping[str, str] | None = None,
) -> str:
    """Worker-host name for G7 routing — workspace-bound when catalog is usable.

    Falls back to basename-only *only* when an explicit
    ``VIBECRAFTED_WORKER_SESSION`` override is set (legacy operator force) or
    when the catalog cannot be opened; otherwise always workspace-bound.
    """

    environ = dict(env) if env is not None else dict(os.environ)
    override = str(environ.get("VIBECRAFTED_WORKER_SESSION") or "").strip()
    if override:
        return override
    try:
        identity = resolve_run_workspace_identity(
            root=root, env=environ, create_if_missing=True
        )
        return identity.worker_host_session
    except WorkspaceCatalogError:
        # Fail open to basename host only for catastrophic catalog failure;
        # callers in tests that set VIBECRAFTED_HOME still get full behavior.
        base = _sanitize_worker_host_label(
            Path(root or ".").name or "vibecrafted",
            max_len=_MAX_WORKER_HOST_LABEL,
        )
        return f"{base}{WORKER_HOST_SUFFIX}"


# ---------------------------------------------------------------------------
# snapshot manifest contract (Cut B consumption; no resurrection here)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceSnapshotManifest:
    """Versioned workspace snapshot contract for future vc-frame resurrection."""

    snapshot_id: str
    workspace_id: str
    schema_version: str
    build_id: BuildId
    created_at: str
    sessions: tuple[dict[str, Any], ...]
    runs: tuple[dict[str, Any], ...]
    layout_snapshots: tuple[dict[str, Any], ...]
    artifacts: tuple[dict[str, Any], ...]
    checksums: dict[str, str]
    migration_lineage: tuple[dict[str, Any], ...]
    previous_snapshot_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": SNAPSHOT_MANIFEST_SCHEMA,
            "snapshot_id": self.snapshot_id,
            "workspace_id": self.workspace_id,
            "schema_version": self.schema_version,
            "build_id": self.build_id.to_payload(),
            "created_at": self.created_at,
            "previous_snapshot_id": self.previous_snapshot_id,
            "sessions": list(self.sessions),
            "runs": list(self.runs),
            "layout_snapshots": list(self.layout_snapshots),
            "artifacts": list(self.artifacts),
            "checksums": dict(self.checksums),
            "migration_lineage": list(self.migration_lineage),
        }

    @classmethod
    def from_payload(cls, payload: object) -> WorkspaceSnapshotManifest:
        if not isinstance(payload, Mapping):
            raise WorkspaceCatalogError("snapshot manifest payload is invalid")
        if payload.get("schema") != SNAPSHOT_MANIFEST_SCHEMA:
            raise WorkspaceCatalogError("snapshot manifest schema is invalid")
        snapshot_id = require_uuid(payload.get("snapshot_id"), field_name="snapshot_id")
        workspace_id = require_uuid(
            payload.get("workspace_id"), field_name="workspace_id"
        )
        previous = payload.get("previous_snapshot_id")
        previous_id = (
            require_uuid(previous, field_name="previous_snapshot_id")
            if previous not in (None, "")
            else None
        )

        def _tuple_of_maps(key: str) -> tuple[dict[str, Any], ...]:
            raw = payload.get(key)
            if raw is None:
                return ()
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                raise WorkspaceCatalogError(f"snapshot manifest {key} is invalid")
            out: list[dict[str, Any]] = []
            for item in raw:
                if not isinstance(item, Mapping):
                    raise WorkspaceCatalogError(
                        f"snapshot manifest {key} entry invalid"
                    )
                out.append(dict(item))
            return tuple(out)

        checksums_raw = payload.get("checksums")
        if not isinstance(checksums_raw, Mapping):
            raise WorkspaceCatalogError("snapshot manifest checksums are invalid")
        checksums = {str(k): str(v) for k, v in checksums_raw.items()}
        return cls(
            snapshot_id=snapshot_id,
            workspace_id=workspace_id,
            schema_version=str(payload.get("schema_version") or "1"),
            build_id=BuildId.from_payload(payload.get("build_id")),
            created_at=str(payload.get("created_at") or ""),
            sessions=_tuple_of_maps("sessions"),
            runs=_tuple_of_maps("runs"),
            layout_snapshots=_tuple_of_maps("layout_snapshots"),
            artifacts=_tuple_of_maps("artifacts"),
            checksums=checksums,
            migration_lineage=_tuple_of_maps("migration_lineage"),
            previous_snapshot_id=previous_id,
        )


def write_snapshot_manifest(manifest: WorkspaceSnapshotManifest) -> Path:
    """Persist a snapshot manifest (contract surface; no runtime attach)."""

    _secure_dir(snapshot_manifests_dir())
    path = snapshot_manifests_dir() / f"{manifest.snapshot_id}.json"
    with _catalog_lock(exclusive=True):
        _atomic_write_json(path, manifest.to_payload())
    return path


def read_snapshot_manifest(snapshot_id: str) -> WorkspaceSnapshotManifest:
    sid = require_uuid(snapshot_id, field_name="snapshot_id")
    payload = _read_json(snapshot_manifests_dir() / f"{sid}.json")
    if payload is None:
        raise WorkspaceCatalogError(f"snapshot manifest not found: {sid}")
    return WorkspaceSnapshotManifest.from_payload(payload)


def build_empty_snapshot_manifest(
    *,
    workspace_id: str,
    build_id: BuildId | None = None,
    root: str | Path | None = None,
) -> WorkspaceSnapshotManifest:
    """Construct a minimal valid snapshot shell for Cut B consumers."""

    wid = require_uuid(workspace_id, field_name="workspace_id")
    bid = build_id or compute_build_id(root or os.getcwd())
    return WorkspaceSnapshotManifest(
        snapshot_id=new_uuid7(),
        workspace_id=wid,
        schema_version="1",
        build_id=bid,
        created_at=_now_iso(),
        sessions=(),
        runs=(),
        layout_snapshots=(),
        artifacts=(),
        checksums={},
        migration_lineage=(),
        previous_snapshot_id=None,
    )


# ---------------------------------------------------------------------------
# migration / backfill (idempotent, fail-closed)
# ---------------------------------------------------------------------------


def _canonical_root_key(root: str) -> str | None:
    raw = str(root or "").strip()
    if not raw:
        return None
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return None


def _collect_legacy_root_evidence() -> dict[str, list[dict[str, Any]]]:
    """Scan control-plane run surfaces for unambiguous root evidence."""

    from .control_plane import run_snapshot_dir

    evidence: dict[str, list[dict[str, Any]]] = {}
    roots_to_scan = [
        run_snapshot_dir(),
        control_plane_home() / "runtime_runs",
    ]
    for base in roots_to_scan:
        if not base.is_dir():
            continue
        if base.name == "runtime_runs":
            candidates = list(base.glob("*/meta.json"))
        else:
            candidates = list(base.glob("*.json"))
        for path in candidates:
            payload = _read_json(path)
            if payload is None:
                continue
            run_id = str(
                payload.get("run_id") or path.parent.name
                if base.name == "runtime_runs"
                else path.stem
            )
            root_key = _canonical_root_key(str(payload.get("root") or ""))
            entry = {
                "run_id": run_id,
                "path": str(path),
                "root": payload.get("root"),
                "workspace_id": payload.get("workspace_id"),
                "source": "runtime_meta"
                if base.name == "runtime_runs"
                else "run_snapshot",
            }
            if root_key is None:
                evidence.setdefault("__unassigned__", []).append(entry)
                continue
            # If the record already carries a workspace_id, treat it as assigned.
            if is_uuid(payload.get("workspace_id")):
                evidence.setdefault(
                    f"assigned:{payload.get('workspace_id')}", []
                ).append(entry)
                continue
            evidence.setdefault(root_key, []).append(entry)
    return evidence


def migrate_legacy_workspaces(*, dry_run: bool = False) -> dict[str, Any]:
    """Idempotent fail-closed backfill.

    Rules:
    - Group only when canonical-root evidence is unambiguous.
    - Do not invent membership for records lacking sufficient evidence.
    - Preserve unclassified records and report them.
    - Never rewrite or delete original historical evidence.
    """

    evidence = _collect_legacy_root_evidence()
    assigned: list[dict[str, Any]] = []
    created: list[dict[str, Any]] = []
    unassigned: list[dict[str, Any]] = list(evidence.get("__unassigned__", []))
    reused: list[dict[str, Any]] = []

    with _catalog_lock(exclusive=True):
        catalog = _load_catalog_unlocked()
        # Map existing roots → workspace_ids (only when a root maps to exactly one workspace).
        root_to_ids: dict[str, list[str]] = {}
        for record in catalog.workspaces.values():
            key = _canonical_root_key(record.canonical_root)
            if key:
                root_to_ids.setdefault(key, []).append(record.workspace_id)

        workspaces = dict(catalog.workspaces)
        now = _now_iso()

        for key, items in evidence.items():
            if key in {"__unassigned__"} or key.startswith("assigned:"):
                if key.startswith("assigned:"):
                    reused.extend(items)
                continue
            existing_ids = root_to_ids.get(key, [])
            if len(existing_ids) > 1:
                # Ambiguous: multiple workspaces already share this root.
                for item in items:
                    unassigned.append({**item, "reason": "ambiguous_root_membership"})
                continue
            if len(existing_ids) == 1:
                wid = existing_ids[0]
                for item in items:
                    assigned.append(
                        {**item, "workspace_id": wid, "action": "linked_existing"}
                    )
                continue
            # No workspace yet for this unambiguous root → create one (idempotent).
            if dry_run:
                created.append(
                    {
                        "canonical_root": key,
                        "display_label": Path(key).name or "workspace",
                        "run_count": len(items),
                        "action": "would_create",
                    }
                )
                for item in items:
                    assigned.append({**item, "action": "would_link_new"})
                continue
            label = Path(key).name or "workspace"
            wid = new_uuid7()
            record = WorkspaceRecord(
                workspace_id=wid,
                display_label=label,
                canonical_root=key,
                status=WORKSPACE_STATUS_ACTIVE,
                created_at=now,
                updated_at=now,
                notes="created by idempotent legacy migration",
                migration={
                    "source": "legacy_backfill",
                    "evidence_runs": len(items),
                    "fail_closed": True,
                },
            )
            workspaces[wid] = record
            root_to_ids[key] = [wid]
            created.append(record.to_payload())
            for item in items:
                assigned.append({**item, "workspace_id": wid, "action": "linked_new"})

        if not dry_run:
            next_catalog = WorkspaceCatalog(
                workspaces=workspaces,
                selected_workspace_id=catalog.selected_workspace_id,
                updated_at=now,
            )
            _save_catalog_unlocked(next_catalog)

    report = {
        "schema": MIGRATION_REPORT_SCHEMA,
        "created_at": _now_iso(),
        "dry_run": dry_run,
        "created_workspaces": created,
        "assigned_records": assigned,
        "reused_already_assigned": reused,
        "unassigned_records": unassigned,
        "unassigned_count": len(unassigned),
        "assigned_count": len(assigned),
        "created_count": len(created),
        "notes": (
            "Original historical run meta and snapshots are never rewritten. "
            "Unassigned records lack unambiguous canonical-root evidence."
        ),
    }
    if not dry_run:
        with _catalog_lock(exclusive=True):
            _atomic_write_json(migration_report_path(), report)
    return report


# ---------------------------------------------------------------------------
# settlement scoping helpers (projection; ledger remains global authority)
# ---------------------------------------------------------------------------


def run_workspace_id_from_meta(meta: Mapping[str, Any]) -> str | None:
    raw = meta.get("workspace_id")
    if is_uuid(raw):
        return str(uuid.UUID(str(raw)))
    return None


def settlement_counts_for_workspace(
    workspace_id: str,
    *,
    ledger_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project f/x/n latest-by-run counts scoped to one workspace_id.

    Uses run meta/snapshots for membership. Runs without workspace_id evidence
    are excluded (fail-closed: never guessed).
    """

    wid = require_uuid(workspace_id, field_name="workspace_id")
    from .settlement_ledger import read_settlement_ledger

    ledger = (
        dict(ledger_snapshot)
        if ledger_snapshot is not None
        else read_settlement_ledger()
    )
    records = ledger.get("records") or []
    if not isinstance(records, Sequence):
        records = []

    # Latest settlement per run_id from the ledger.
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if record.get("record_type") != "settlement_transition":
            continue
        run_id = str(record.get("run_id") or "")
        if not run_id:
            continue
        rev = record.get("settlement_revision")
        prev = latest.get(run_id)
        if prev is None or (
            type(rev) is int
            and type(prev.get("settlement_revision")) is int
            and rev >= prev["settlement_revision"]
        ):
            latest[run_id] = dict(record)

    # Membership via run snapshots / runtime meta.
    from .control_plane import run_snapshot_dir

    membership: dict[str, str | None] = {}
    snap_dir = run_snapshot_dir()
    runtime_dir = control_plane_home() / "runtime_runs"
    for run_id in latest:
        workspace_for_run: str | None = None
        for candidate in (
            snap_dir / f"{run_id}.json",
            runtime_dir / run_id / "meta.json",
        ):
            payload = _read_json(candidate)
            if payload is None:
                continue
            workspace_for_run = run_workspace_id_from_meta(payload)
            if workspace_for_run:
                break
        membership[run_id] = workspace_for_run

    counts = {"f": 0, "x": 0, "n": 0}
    matched_runs: list[str] = []
    excluded_unassigned = 0
    for run_id, record in latest.items():
        member = membership.get(run_id)
        if member is None:
            excluded_unassigned += 1
            continue
        if member != wid:
            continue
        tui = str(record.get("settlement_tui") or "")
        if tui in counts:
            counts[tui] += 1
            matched_runs.append(run_id)

    total = counts["f"] + counts["x"] + counts["n"]
    return {
        "schema": "vibecrafted.workspace-settlement-counts.v1",
        "workspace_id": wid,
        "count_semantics": "known_v2_lower_bound_workspace_scoped",
        "latest_by_run": {**counts, "total": total},
        "matched_run_count": len(matched_runs),
        "excluded_unassigned_run_count": excluded_unassigned,
        "sample_run_ids": matched_runs[:20],
        "authority": "settlement_ledger+run_workspace_membership",
        "notes": (
            "Global ledger remains the permanent authority. This projection "
            "filters latest-by-run settlements to runs with explicit workspace_id "
            "membership evidence. Unassigned legacy runs are never guessed."
        ),
    }


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def workspace_cli_main(argv: Sequence[str] | None = None) -> int:
    """CLI surface for workspace identity, WES attachments, and migration."""

    import argparse

    parser = argparse.ArgumentParser(prog="vibecrafted workspace")
    sub = parser.add_subparsers(dest="action", required=True)

    create_p = sub.add_parser("create", help="create a durable workspace")
    create_p.add_argument("--root", default=os.getcwd())
    create_p.add_argument("--label", default="")
    create_p.add_argument("--workspace-id", default="")
    create_p.add_argument("--notes", default="")
    create_p.add_argument("--no-select", action="store_true")
    create_p.add_argument("--json", action="store_true")

    list_p = sub.add_parser("list", help="list workspaces")
    list_p.add_argument("--include-buried", action="store_true")
    list_p.add_argument("--json", action="store_true")

    show_p = sub.add_parser("show", help="show one workspace")
    show_p.add_argument("workspace_id")
    show_p.add_argument("--json", action="store_true")

    select_p = sub.add_parser("select", help="select active workspace")
    select_p.add_argument("workspace_id")
    select_p.add_argument("--json", action="store_true")

    bury_p = sub.add_parser("bury", help="bury workspace (hide, keep history)")
    bury_p.add_argument("workspace_id")
    bury_p.add_argument("--json", action="store_true")

    recover_p = sub.add_parser("recover", help="recover buried workspace")
    recover_p.add_argument("workspace_id")
    recover_p.add_argument("--select", action="store_true")
    recover_p.add_argument("--json", action="store_true")

    migrate_p = sub.add_parser("migrate", help="idempotent legacy backfill")
    migrate_p.add_argument("--dry-run", action="store_true")
    migrate_p.add_argument("--json", action="store_true")

    materialize_p = sub.add_parser(
        "materialize", help="bind a live workspace_instance to current build_id"
    )
    materialize_p.add_argument("workspace_id")
    materialize_p.add_argument("--root", default=os.getcwd())
    materialize_p.add_argument("--json", action="store_true")

    resolve_p = sub.add_parser(
        "resolve", help="resolve or create the workspace used by vc-start"
    )
    resolve_p.add_argument("--root", default="")
    resolve_p.add_argument("--env", action="store_true")
    resolve_p.add_argument("--json", action="store_true")

    attach_p = sub.add_parser(
        "session-attach", help="attach a physical runtime session to WES"
    )
    attach_p.add_argument("--workspace-id", required=True)
    attach_p.add_argument("--session-id", required=True)
    attach_p.add_argument("--instance-id", required=True)
    attach_p.add_argument("--runtime", required=True)
    attach_p.add_argument("--runtime-session-id", required=True)
    attach_p.add_argument(
        "--state", choices=sorted(RUNTIME_SESSION_STATES), required=True
    )
    attach_p.add_argument("--socket-dir", default="")
    attach_p.add_argument("--replaces-runtime-session-id", default="")
    attach_p.add_argument("--json", action="store_true")

    counts_p = sub.add_parser(
        "settlement-counts", help="F/X/N projection scoped to workspace_id"
    )
    counts_p.add_argument("workspace_id")
    counts_p.add_argument("--json", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)

    def _emit(payload: Mapping[str, Any], *, as_json: bool) -> int:
        if as_json:
            print(
                json.dumps(dict(payload), indent=2, ensure_ascii=False, sort_keys=True)
            )
        else:
            for key, value in payload.items():
                print(f"{key}: {value}")
        return 0

    try:
        if args.action == "create":
            record = create_workspace(
                root=args.root,
                display_label=args.label,
                workspace_id=args.workspace_id or None,
                notes=args.notes,
                select=not args.no_select,
            )
            return _emit(record.to_payload(), as_json=args.json)
        if args.action == "list":
            records = list_workspaces(include_buried=args.include_buried)
            if args.json:
                print(
                    json.dumps(
                        [r.to_payload() for r in records],
                        indent=2,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            else:
                selected = read_catalog().selected_workspace_id
                for record in records:
                    mark = "*" if record.workspace_id == selected else " "
                    print(
                        f"{mark} {record.workspace_id}  {record.status:6}  "
                        f"{record.display_label}  {record.canonical_root}"
                    )
            return 0
        if args.action == "show":
            return _emit(
                show_workspace(args.workspace_id).to_payload(), as_json=args.json
            )
        if args.action == "select":
            return _emit(
                select_workspace(args.workspace_id).to_payload(), as_json=args.json
            )
        if args.action == "bury":
            return _emit(
                bury_workspace(args.workspace_id).to_payload(), as_json=args.json
            )
        if args.action == "recover":
            return _emit(
                recover_workspace(args.workspace_id, select=args.select).to_payload(),
                as_json=args.json,
            )
        if args.action == "migrate":
            return _emit(
                migrate_legacy_workspaces(dry_run=args.dry_run), as_json=args.json
            )
        if args.action == "materialize":
            instance = materialize_instance(
                workspace_id=args.workspace_id, root=args.root
            )
            return _emit(instance.to_payload(), as_json=args.json)
        if args.action == "resolve":
            environ = dict(os.environ)
            selected = selected_workspace()
            if not environ.get(ENV_WORKSPACE_ID) and selected is not None:
                environ[ENV_WORKSPACE_ID] = selected.workspace_id
            root = args.root or (
                selected.canonical_root if selected is not None else os.getcwd()
            )
            identity = resolve_run_workspace_identity(root=root, env=environ)
            payload = {
                **identity.to_env(),
                "VIBECRAFTED_OPERATOR_SESSION": operator_session_name(
                    identity.workspace_id,
                    display_label=identity.display_label,
                ),
                "VIBECRAFTED_WORKSPACE_ROOT": str(Path(root).expanduser().resolve()),
            }
            if args.env:
                for key, value in payload.items():
                    print(f"{key}={value}")
                return 0
            return _emit(payload, as_json=args.json)
        if args.action == "session-attach":
            session = record_runtime_session_attachment(
                workspace_id=args.workspace_id,
                vibecrafted_session_id=args.session_id,
                workspace_instance_id=args.instance_id,
                runtime=args.runtime,
                runtime_session_id=args.runtime_session_id,
                state=args.state,
                socket_dir=args.socket_dir,
                replaces_runtime_session_id=(args.replaces_runtime_session_id or None),
            )
            return _emit(session.to_payload(), as_json=args.json)
        if args.action == "settlement-counts":
            return _emit(
                settlement_counts_for_workspace(args.workspace_id),
                as_json=True,
            )
    except WorkspaceCatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown action {args.action!r}")
    return 2


__all__ = [
    "BUILD_ID_SCHEMA",
    "CATALOG_SCHEMA",
    "ENV_BUILD_ID",
    "ENV_VIBECRAFTED_SESSION_ID",
    "ENV_WORKSPACE_ID",
    "ENV_WORKSPACE_INSTANCE_ID",
    "INSTANCE_SCHEMA",
    "RUNTIME_SESSION_STATES",
    "SESSION_RECORD_SCHEMA",
    "SNAPSHOT_MANIFEST_SCHEMA",
    "WORKER_HOST_SUFFIX",
    "BuildId",
    "RunWorkspaceIdentity",
    "RuntimeSessionAttachment",
    "WorkspaceCatalog",
    "WorkspaceCatalogError",
    "WorkspaceInstance",
    "WorkspaceInstanceBuildMismatch",
    "WorkspaceNotFound",
    "WorkspaceRecord",
    "WorkspaceSessionRecord",
    "WorkspaceSnapshotManifest",
    "build_empty_snapshot_manifest",
    "bury_workspace",
    "catalog_path",
    "claim_live_instance",
    "compute_build_id",
    "create_workspace",
    "is_legacy_operator_session_name",
    "legacy_operator_session_name",
    "legacy_worker_host_session_name",
    "list_instances",
    "list_workspaces",
    "materialize_instance",
    "migrate_legacy_workspaces",
    "new_uuid7",
    "operator_session_name",
    "read_catalog",
    "read_snapshot_manifest",
    "read_workspace_session",
    "record_runtime_session_attachment",
    "recover_workspace",
    "resolve_operator_place_session",
    "resolve_run_workspace_identity",
    "resolve_worker_host_session",
    "select_workspace",
    "selected_workspace",
    "settlement_counts_for_workspace",
    "show_workspace",
    "worker_host_display_label",
    "worker_host_session_name",
    "workspace_cli_main",
    "workspace_session_path",
    "write_snapshot_manifest",
]
