"""Ordered, cross-process locks for mutations of one run lineage.

The control-plane projection is intentionally cheap to read, but mutations that
change settlement or dispatch recovery work must agree on one ordering:

    parent run -> resume lineage -> idempotency key

Keeping this helper below ``workflow`` and ``trust`` avoids an import cycle
while giving both surfaces the same filesystem-backed exclusion boundary.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

_LOCAL_LOCKS_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.RLock] = {}
_LOCAL_HELD_LOCKS = threading.local()


class RunMetaMutationError(ValueError):
    """The requested mutation is not bound to one regular run meta file."""


def _lock_path(root: Path, kind: str, value: str) -> Path:
    """Deterministic lock-file path for one ``(kind, value)`` mutation key."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return root / "run_mutation_locks" / kind / f"{digest}.lock"


def _local_lock(path: Path) -> threading.RLock:
    """Process-local reentrant lock guarding one lock-file path, memoized by path."""
    key = str(path)
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(key, threading.RLock())


def _held_lock_counts() -> dict[str, int]:
    """Thread-local reentrancy counters keyed by lock-file path."""
    counts = getattr(_LOCAL_HELD_LOCKS, "counts", None)
    if counts is None:
        counts = {}
        _LOCAL_HELD_LOCKS.counts = counts
    return counts


@contextmanager
def run_mutation_locks(
    control_plane_root: Path,
    *,
    run_id: str,
    resume_root: str = "",
    idempotency_key: str = "",
) -> Iterator[None]:
    """Acquire the shared mutation locks in the only supported order."""

    target = str(run_id or "").strip()
    if not target:
        raise ValueError("run_id is required for a mutation lock")
    ordered = [("run", target)]
    lineage = str(resume_root or "").strip()
    if lineage:
        ordered.append(("lineage", lineage))
    key = str(idempotency_key or "").strip()
    if key:
        ordered.append(("idempotency", key))

    # Reentrancy is keyed by lock-file path, so one root must always map to
    # one key: an outer caller holding the lock via ``~/.vibecrafted/...`` and
    # an inner caller via its resolved form (``/private/...``, ``/var/home``)
    # would otherwise flock the same file twice and deadlock themselves.
    lock_root = Path(os.path.abspath(Path(control_plane_root).expanduser()))
    lock_root = lock_root.resolve(strict=False)
    paths = [_lock_path(lock_root, kind, value) for kind, value in ordered]
    local_locks = [_local_lock(path) for path in paths]
    counts = _held_lock_counts()
    handles: list[tuple[BinaryIO | None, threading.RLock, str]] = []
    try:
        for local, path in zip(local_locks, paths, strict=True):
            local.acquire()
            key = str(path)
            if counts.get(key, 0):
                counts[key] += 1
                handles.append((None, local, key))
                continue
            opened_handle: BinaryIO | None = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                opened_handle = path.open("a+b")
                fcntl.flock(opened_handle.fileno(), fcntl.LOCK_EX)
            except BaseException:
                if opened_handle is not None:
                    opened_handle.close()
                local.release()
                raise
            counts[key] = 1
            handles.append((opened_handle, local, key))
        yield
    finally:
        for handle, local, key in reversed(handles):
            count = counts.get(key, 0)
            if count > 1:
                counts[key] = count - 1
                local.release()
                continue
            counts.pop(key, None)
            if handle is None:
                local.release()
                continue
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
                local.release()


def _canonical_meta_path(meta_path: Path, *, allow_missing: bool) -> Path:
    """Return the canonical path of a run meta file.

    Ancestor directories are resolved (a ``$HOME`` that lives behind a symlink —
    ``/tmp`` → ``/private/tmp`` on macOS, ``/home`` → ``/var/home`` on ostree
    Linux — is a legitimate runtime root, not an attack). The meta entry
    itself must be a regular file: a symlinked or special ``meta.json`` is
    refused so the TOCTOU checks in :func:`_read_regular_json` stay meaningful.
    """

    absolute = Path(os.path.abspath(meta_path.expanduser()))
    try:
        canonical_parent = absolute.parent.resolve(strict=True)
    except OSError as exc:
        raise RunMetaMutationError(
            f"run meta parent is unavailable: {absolute}"
        ) from exc
    canonical = canonical_parent / absolute.name
    try:
        mode = canonical.lstat().st_mode
    except FileNotFoundError:
        if allow_missing:
            return canonical
        raise
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise RunMetaMutationError(f"run meta is not a regular file: {canonical}")
    return canonical


def _canonical_directory(path: Path) -> Path:
    """Resolve ``path`` to its canonical directory.

    Symlinked ancestors are followed and the resolved path is returned, so
    every later ``relative_to`` boundary check compares canonical forms. The
    resolved target must exist and be a directory.
    """
    absolute = Path(os.path.abspath(path.expanduser()))
    try:
        canonical = absolute.resolve(strict=True)
        mode = canonical.lstat().st_mode
    except OSError as exc:
        raise RunMetaMutationError(
            f"run mutation root is unavailable: {absolute}"
        ) from exc
    if not stat.S_ISDIR(mode):
        raise RunMetaMutationError(f"run mutation root is not a directory: {absolute}")
    return canonical


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    """Whether two stat results refer to the same inode (device + inode number)."""
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _read_regular_json(meta_path: Path) -> dict[str, Any]:
    """Read a JSON object from ``meta_path``, refusing symlinks and TOCTOU swaps.

    Opens with ``O_NOFOLLOW``, then verifies the open fd and the path's visible
    entry agree on identity/size/mtime both before and after the read, raising
    :class:`RunMetaMutationError` on any mismatch (concurrent replace, non-regular
    file, or invalid JSON).
    """
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(meta_path, flags)
    try:
        opened_before = os.fstat(descriptor)
        visible_before = meta_path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(opened_before.st_mode)
            or not stat.S_ISREG(visible_before.st_mode)
            or not _same_file_identity(opened_before, visible_before)
        ):
            raise RunMetaMutationError(f"run meta is not a regular file: {meta_path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read()
        opened_after = os.fstat(descriptor)
        visible_after = meta_path.stat(follow_symlinks=False)
        if (
            not _same_file_identity(opened_before, opened_after)
            or not _same_file_identity(opened_after, visible_after)
            or opened_before.st_size != opened_after.st_size
            or opened_before.st_mtime_ns != opened_after.st_mtime_ns
        ):
            raise RunMetaMutationError(f"run meta changed while reading: {meta_path}")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RunMetaMutationError(f"run meta is not valid JSON: {meta_path}") from exc
    if not isinstance(payload, dict):
        raise RunMetaMutationError(f"run meta is not an object: {meta_path}")
    return payload


def read_run_meta(
    meta_path: Path,
    *,
    expected_run_id: str = "",
) -> dict[str, Any]:
    """Read a canonical regular meta file without following symlinks."""

    canonical = _canonical_meta_path(Path(meta_path), allow_missing=False)
    payload = _read_regular_json(canonical)
    expected = str(expected_run_id or "").strip()
    if expected and payload.get("run_id") != expected:
        raise RunMetaMutationError(f"run meta identity mismatch: expected {expected!r}")
    return payload


def _write_json_durable(meta_path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via temp-file + fsync + atomic rename, then fsync the directory."""
    serialized = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode()
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{meta_path.name}.",
            suffix=".tmp",
            dir=meta_path.parent,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, meta_path)
        temporary_path = None
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(meta_path.parent, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def mutate_run_meta(
    control_plane_root: Path,
    *,
    meta_path: Path,
    mutation_root: Path | None = None,
    run_id: str,
    mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
    create: bool = False,
) -> bool:
    """Serialize one read/merge/replace transaction for a run's latest meta.

    The mutator always receives the payload read *after* the shared per-run
    lock is held. Returning ``None`` refuses the write without changing the
    file. Existing metadata and the replacement must both carry the exact
    caller-supplied run id. The lock namespace always lives under
    ``control_plane_root``. Callers that own run metadata outside that tree
    must opt into its narrow canonical directory through ``mutation_root``;
    omitting it preserves the control-plane-only write boundary.
    """

    expected = str(run_id or "").strip()
    if not expected:
        raise RunMetaMutationError("run_id is required for a run meta mutation")
    canonical_lock_root = _canonical_directory(Path(control_plane_root))
    canonical_mutation_root = (
        canonical_lock_root
        if mutation_root is None
        else _canonical_directory(Path(mutation_root))
    )
    canonical = _canonical_meta_path(Path(meta_path), allow_missing=create)
    try:
        canonical.relative_to(canonical_mutation_root)
    except ValueError as exc:
        raise RunMetaMutationError(
            f"run meta is outside the mutation root: {canonical}"
        ) from exc
    with run_mutation_locks(canonical_lock_root, run_id=expected):
        try:
            current = _read_regular_json(canonical)
        except FileNotFoundError:
            if not create:
                raise
            current = {}
        if current and current.get("run_id") != expected:
            raise RunMetaMutationError(
                f"run meta identity mismatch: expected {expected!r}"
            )
        updated = mutator(dict(current))
        if updated is None:
            return False
        if not isinstance(updated, dict):
            raise RunMetaMutationError("run meta mutator must return an object or None")
        if updated.get("run_id") != expected:
            raise RunMetaMutationError(
                f"run meta identity mismatch after mutation: expected {expected!r}"
            )
        _write_json_durable(canonical, updated)
        return True
