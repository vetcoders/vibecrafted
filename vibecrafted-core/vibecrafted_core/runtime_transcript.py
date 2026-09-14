"""Durable ownership manifests for vc-frame runtime transcript recovery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

MAX_MANIFEST_BYTES = 64 * 1024


def runtime_transcript_manifest_path(transcript: Path) -> Path:
    """Match vc-frame's `<transcript>.manifest.json` path contract exactly."""

    return Path(f"{transcript}.manifest.json")


def _canonical_regular_path(path: Path) -> Path | None:
    """Resolve `path` to its canonical form and require a real regular file.

    Ancestor directories may sit behind symlinks (a `$HOME` under `/tmp` or
    `/var/home` is a legitimate runtime root); the file entry itself must not
    be a symlink, so the O_NOFOLLOW read path keeps its TOCTOU guarantees.
    Return None on any mismatch or OS error."""

    absolute = Path(os.path.abspath(path.expanduser()))
    try:
        canonical_parent = absolute.parent.resolve(strict=True)
        canonical = canonical_parent / absolute.name
        metadata = canonical.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    return canonical


def _canonical_directory(path: Path) -> Path | None:
    """Resolve `path` to its canonical form, requiring a real directory;
    symlinked ancestors are followed. Return None on any mismatch or OS error."""

    absolute = Path(os.path.abspath(path.expanduser()))
    try:
        canonical = absolute.resolve(strict=True)
        metadata = canonical.lstat()
    except OSError:
        return None
    if not stat.S_ISDIR(metadata.st_mode):
        return None
    return canonical


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    """Return whether two stat results identify the same underlying file
    (device + inode), independent of the path used to reach them."""

    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _read_regular_bytes(path: Path, *, limit: int) -> bytes | None:
    """Read up to `limit` bytes from a non-symlinked regular file, re-checking
    identity, size, and mtime before and after the read to reject a TOCTOU swap
    or truncation; return None on any anomaly or when content exceeds `limit`."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        try:
            opened_before = os.fstat(descriptor)
            visible_before = path.lstat()
            if (
                not stat.S_ISREG(opened_before.st_mode)
                or not stat.S_ISREG(visible_before.st_mode)
                or not _same_file_identity(opened_before, visible_before)
            ):
                return None
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(limit + 1)
            opened_after = os.fstat(descriptor)
            visible_after = path.lstat()
            if (
                len(raw) > limit
                or not _same_file_identity(opened_before, opened_after)
                or not _same_file_identity(opened_after, visible_after)
                or opened_before.st_size != opened_after.st_size
                or opened_before.st_mtime_ns != opened_after.st_mtime_ns
            ):
                return None
            return raw
        except OSError:
            return None
    finally:
        os.close(descriptor)


def _read_regular_file_digest(path: Path) -> tuple[int, str] | None:
    """Compute (size, sha256-hex) for a non-symlinked regular file, re-checking
    identity, size, and mtime before and after hashing; return None on any
    anomaly, so a mutated-mid-read file never yields a false digest."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        try:
            opened_before = os.fstat(descriptor)
            visible_before = path.lstat()
            if (
                not stat.S_ISREG(opened_before.st_mode)
                or not stat.S_ISREG(visible_before.st_mode)
                or not _same_file_identity(opened_before, visible_before)
            ):
                return None
            digest = hashlib.sha256()
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            opened_after = os.fstat(descriptor)
            visible_after = path.lstat()
            if (
                not _same_file_identity(opened_before, opened_after)
                or not _same_file_identity(opened_after, visible_after)
                or opened_before.st_size != opened_after.st_size
                or opened_before.st_mtime_ns != opened_after.st_mtime_ns
            ):
                return None
            return opened_after.st_size, digest.hexdigest()
        except OSError:
            return None
    finally:
        os.close(descriptor)


def _atomic_write_manifest(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write `payload` as pretty JSON to `path` via a private
    tempfile in the same directory, fsync it, rename over `path`, then fsync
    the parent directory so the replace survives a crash."""

    serialized = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode()
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def write_runtime_transcript_manifest(
    transcript: Path,
    *,
    run_id: str,
) -> Path | None:
    """Durably bind one finalized, non-empty transcript to its owning run."""

    expected_run_id = str(run_id or "").strip()
    if not expected_run_id:
        return None
    canonical = _canonical_regular_path(Path(transcript))
    if canonical is None:
        return None
    canonical_root = _canonical_directory(canonical.parent)
    evidence = _read_regular_file_digest(canonical)
    if canonical_root is None or evidence is None or evidence[0] <= 0:
        return None

    manifest = runtime_transcript_manifest_path(canonical)
    _atomic_write_manifest(
        manifest,
        {
            "version": 1,
            "run_id": expected_run_id,
            "transcript": str(canonical),
            "root": str(canonical_root),
            "bytes": evidence[0],
            "sha256": evidence[1],
        },
    )
    return manifest


def validate_runtime_transcript(
    transcript: object,
    *,
    run_id: str,
) -> Path | None:
    """Return only a canonical vc-frame input with complete manifest evidence."""

    expected_run_id = str(run_id or "").strip()
    declared = str(transcript or "").strip()
    if not expected_run_id or not declared:
        return None
    canonical = _canonical_regular_path(Path(declared))
    if canonical is None:
        return None
    manifest_path = _canonical_regular_path(runtime_transcript_manifest_path(canonical))
    if manifest_path is None:
        return None
    raw = _read_regular_bytes(manifest_path, limit=MAX_MANIFEST_BYTES)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if type(payload.get("version")) is not int or payload["version"] != 1:
        return None
    if payload.get("run_id") != expected_run_id:
        return None
    declared_transcript = payload.get("transcript")
    declared_root = payload.get("root")
    declared_bytes = payload.get("bytes")
    declared_sha256 = payload.get("sha256")
    if (
        not isinstance(declared_transcript, str)
        or not isinstance(declared_root, str)
        or type(declared_bytes) is not int
        or declared_bytes <= 0
        or not isinstance(declared_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", declared_sha256) is None
    ):
        return None
    manifest_transcript = _canonical_regular_path(Path(declared_transcript))
    canonical_root = _canonical_directory(Path(declared_root))
    if manifest_transcript != canonical or canonical_root is None:
        return None
    try:
        canonical.relative_to(canonical_root)
        manifest_path.relative_to(canonical_root)
    except ValueError:
        return None
    evidence = _read_regular_file_digest(canonical)
    if evidence != (declared_bytes, declared_sha256):
        return None
    return canonical


class PrivatePromptFilter:
    """Streaming exact-byte redaction, including matches across read boundaries."""

    def __init__(self, secret: bytes) -> None:
        self.secret = secret
        self.prefix = [0] * len(secret)
        matched = 0
        for index in range(1, len(secret)):
            while matched and secret[index] != secret[matched]:
                matched = self.prefix[matched - 1]
            if secret[index] == secret[matched]:
                matched += 1
            self.prefix[index] = matched
        self.matched = 0
        self.pending = bytearray()

    def feed(self, chunk: bytes) -> bytes:
        if not self.secret:
            return chunk
        output = bytearray()
        for value in chunk:
            self.pending.append(value)
            while self.matched and value != self.secret[self.matched]:
                self.matched = self.prefix[self.matched - 1]
            if value == self.secret[self.matched]:
                self.matched += 1
            if self.matched == len(self.secret):
                output.extend(self.pending[: -self.matched])
                output.extend(b"[private launch input redacted]")
                self.pending.clear()
                self.matched = 0
            else:
                count = len(self.pending) - self.matched
                output.extend(self.pending[:count])
                del self.pending[:count]
        return bytes(output)

    def finish(self) -> bytes:
        tail = bytes(self.pending)
        self.pending.clear()
        self.matched = 0
        return tail


class InteractiveTranscriptCapture:
    """Capture output through a tty while leaving the caller's input tty intact.

    The capture is an output adapter, not a process supervisor. Losing the
    display sink never signals the child; the existing spawn owner keeps its
    identity, lifecycle, and publication loop.
    """

    def __init__(self, path: Path, secret: str, *, display_fd: int = 1) -> None:
        import fcntl
        import pty
        import termios
        import threading

        self.master, self.slave = pty.openpty()
        self.path = path
        self.display_fd = display_fd
        self.error = ""
        self.filter = PrivatePromptFilter(secret.encode("utf-8"))
        self.normalized_filter = PrivatePromptFilter(
            secret.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
        )
        try:
            size = fcntl.ioctl(display_fd, termios.TIOCGWINSZ, b"\0" * 8)
            fcntl.ioctl(self.slave, termios.TIOCSWINSZ, size)
        except OSError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.log = os.fdopen(fd, "wb", buffering=0)
        self.thread = threading.Thread(target=self._drain, daemon=True)

    def start(self) -> None:
        os.close(self.slave)
        self.slave = -1
        self.thread.start()

    def _emit(self, raw: bytes) -> None:
        if not raw:
            return
        self.log.write(raw)
        if self.display_fd >= 0:
            try:
                view = memoryview(raw)
                while view:
                    count = os.write(self.display_fd, view)
                    view = view[count:]
            except OSError:
                self.display_fd = -1

    def _drain(self) -> None:
        import errno
        import fcntl
        import select
        import termios

        try:
            while True:
                # The provider sees this output PTY, so keep its geometry in
                # step with the original terminal even during quiet periods.
                try:
                    size = fcntl.ioctl(self.display_fd, termios.TIOCGWINSZ, b"\0" * 8)
                    fcntl.ioctl(self.master, termios.TIOCSWINSZ, size)
                except (OSError, ValueError):
                    pass
                ready, _, _ = select.select([self.master], [], [], 0.1)
                if not ready:
                    continue
                try:
                    chunk = os.read(self.master, 65536)
                except OSError as exc:
                    if exc.errno in (errno.EIO, errno.EBADF):
                        break
                    raise
                if not chunk:
                    break
                self._emit(self.normalized_filter.feed(self.filter.feed(chunk)))
            self._emit(self.normalized_filter.feed(self.filter.finish()))
            self._emit(self.normalized_filter.finish())
        except OSError as exc:
            self.error = f"transcript capture failed: {type(exc).__name__}"
        finally:
            self.log.close()

    def close(self) -> None:
        if self.slave >= 0:
            os.close(self.slave)
            self.slave = -1
        if self.thread.ident is not None:
            self.thread.join(timeout=1)
        else:
            self.log.close()
        os.close(self.master)
