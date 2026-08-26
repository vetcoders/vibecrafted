"""Foreground/launchd supervisor for the vibecrafted server+guardian pair: lease
coordination, identity-verified launchd service management, and CLI entrypoint."""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import plistlib
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self
from xml.parsers.expat import ExpatError

from . import __version__ as PACKAGE_VERSION
from .server_config import (
    ServerConfigError,
    config_path,
    has_server_config,
    load_server_config,
    origin_for,
)

SUPERVISOR_SCHEMA = "vibecrafted.server-supervisor.v1"
SUPERVISOR_LOCK_SCHEMA = "vibecrafted.server-supervisor-lock.v1"
LAUNCH_AGENT_LABEL = "io.vetcoders.vibecrafted.server"
EX_TEMPFAIL = 75
EX_CONFIG = 78
_TOOLS_INSTALL_LEASE_ENV = "VIBECRAFTED_INSTALL_LEASE_FD"
_TOOLS_INSTALL_LOCK_NAME = ".vibecrafted-install.lock"
_HOST_PATTERN = re.compile(r"[A-Za-z0-9._:-]+")
_MINIMAL_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_PASSTHROUGH_ENVIRONMENT = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "VIBECRAFTED_GUARDIAN_READY_TICKS",
    "VIBECRAFTED_LIFECYCLE_LOCK_TICKS",
    "VIBECRAFTED_PYTHON",
    "VIBECRAFTED_STOP_KILL_WAIT_TICKS",
    "VIBECRAFTED_STOP_TERM_WAIT_TICKS",
    "VIBECRAFTED_TRIAGE_RUN",
    "VIBECRAFTED_TEST_LIFECYCLE_LOG",
    "VIBECRAFTED_TEST_SERVER_STOP_DELAY",
)


class SupervisorError(RuntimeError):
    """Raised for any supervisor failure; carries the process exit code to use."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        """Store the message and the exit code the CLI should return."""

        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class SupervisorPaths:
    """Canonical filesystem locations the supervisor reads from and writes to."""

    home: Path
    runtime_home: Path
    operator_home: Path
    server_dir: Path
    lock_file: Path
    receipt_file: Path
    launch_agent_file: Path
    stdout_log: Path
    stderr_log: Path

    @classmethod
    def create(
        cls,
        *,
        home: Path,
        runtime_home: Path,
        operator_home: Path,
    ) -> SupervisorPaths:
        """Canonicalize the three home directories and derive the fixed
        server/lock/receipt/LaunchAgent/log paths beneath them."""

        canonical_home = _absolute_path(home)
        canonical_runtime_home = _absolute_path(runtime_home)
        canonical_operator_home = _absolute_path(operator_home)
        server_dir = canonical_home / "server"
        return cls(
            home=canonical_home,
            runtime_home=canonical_runtime_home,
            operator_home=canonical_operator_home,
            server_dir=server_dir,
            lock_file=server_dir / "supervisor.lock",
            receipt_file=server_dir / "supervisor.status.json",
            launch_agent_file=(
                canonical_operator_home
                / "Library"
                / "LaunchAgents"
                / f"{LAUNCH_AGENT_LABEL}.plist"
            ),
            stdout_log=server_dir / "supervisor.stdout.log",
            stderr_log=server_dir / "supervisor.stderr.log",
        )


@dataclass(frozen=True)
class SupervisorConfig:
    """Resolved supervisor run configuration: paths, launcher, endpoint, timing."""

    paths: SupervisorPaths
    launcher: Path
    host: str
    port: int
    public_url: str = ""
    config_file: Path | None = None
    interval: float = 1.0
    maximum_backoff: float = 30.0
    command_timeout: float = 60.0

    @property
    def endpoint(self) -> str:
        """`http://host:port` endpoint, bracketing a bare IPv6 host."""

        rendered_host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{rendered_host}:{self.port}"


@dataclass(frozen=True)
class SupervisorProbe:
    """Snapshot of the coordination-lock state: liveness, verified identity,
    and (when verified) the owning process and its build/hash fingerprint."""

    live: bool
    verified: bool
    pid: int | None
    service_managed: bool | None
    role: str | None = None
    executable: str | None = None
    executable_sha256: str | None = None
    runtime_sha256: str | None = None
    build_version: str | None = None
    launcher_sha256: str | None = None


@dataclass(frozen=True)
class ServiceStatus:
    """Composite view of launchd installation/load state plus the running
    supervisor's identity and the health of the managed server+guardian pair."""

    installed: bool
    loaded: bool
    supervisor_live: bool
    supervisor_verified: bool
    pair_healthy: bool
    supervisor_pid: int | None
    supervisor_service_managed: bool = False
    build_current: bool = False


@dataclass(frozen=True)
class SupervisorIdentity:
    """Content-hash fingerprint of the running supervisor build: its
    executable, this runtime module, package version, and paired launcher."""

    executable: Path
    executable_sha256: str
    runtime_sha256: str
    build_version: str
    launcher_sha256: str


def _absolute_path(path: Path) -> Path:
    """Expand `~` and resolve `path`; raise `SupervisorError` if the input was
    not already absolute (symlinks are resolved but relativity is rejected
    up front)."""

    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise SupervisorError(f"path must be absolute: {path}", EX_CONFIG)
    return expanded.resolve(strict=False)


def _utc_now() -> str:
    """Current UTC time as `YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ` for receipt fields."""

    return (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        + f".{int(time.time_ns() % 1_000_000_000):09d}Z"
    )


def _ensure_owned_directory(path: Path, mode: int = 0o700) -> None:
    """Create `path` if missing, then require it to be a non-symlinked
    directory owned by the current uid, chmod'd to `mode`."""

    path.mkdir(parents=True, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or path.is_symlink()
    ):
        raise SupervisorError(
            f"directory is not an owned regular directory: {path}",
            EX_CONFIG,
        )
    os.chmod(path, mode)


def _file_owner_is_trusted(
    file_uid: int,
    file_mode: int,
    *,
    allow_root_owned: bool,
) -> bool:
    """Trust a file owned by the current uid, or (when `allow_root_owned`) one
    owned by root with no group/other write bits set."""

    if file_uid == os.getuid():
        return True
    return (
        allow_root_owned
        and file_uid == 0
        and file_mode & (stat.S_IWGRP | stat.S_IWOTH) == 0
    )


def _validate_owned_regular_file(
    path: Path,
    *,
    executable: bool = False,
    allow_symlink: bool = True,
    allow_root_owned: bool = False,
) -> Path:
    """Resolve `path` and require it to be a trusted, single-hardlink regular
    file (optionally executable); raise `SupervisorError` otherwise."""

    if not allow_symlink and path.is_symlink():
        raise SupervisorError(f"path must not be a symlink: {path}", EX_CONFIG)
    canonical = path.resolve(strict=True)
    info = canonical.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or not _file_owner_is_trusted(
            info.st_uid,
            info.st_mode,
            allow_root_owned=allow_root_owned,
        )
        or info.st_nlink != 1
    ):
        raise SupervisorError(
            f"path is not an owned regular file: {canonical}",
            EX_CONFIG,
        )
    if executable and not os.access(canonical, os.X_OK):
        raise SupervisorError(f"path is not executable: {canonical}", EX_CONFIG)
    return canonical


def _sha256_file(path: Path, *, allow_root_owned: bool = False) -> str:
    """Hash `path` by open file descriptor, verifying it is a trusted, single-
    hardlink regular file whose descriptor identity matches the named path
    before reading; raises `SupervisorError` if that trust check fails."""

    digest = hashlib.sha256()
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        visible = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _file_owner_is_trusted(
                opened.st_uid,
                opened.st_mode,
                allow_root_owned=allow_root_owned,
            )
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise SupervisorError(
                f"cannot hash unstable or unowned executable: {path}",
                EX_CONFIG,
            )
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _supervisor_identity(
    executable: Path | None = None,
    *,
    launcher: Path,
    expected_sha256: str | None = None,
    expected_runtime_sha256: str | None = None,
    expected_version: str | None = None,
    expected_launcher_sha256: str | None = None,
) -> SupervisorIdentity:
    """Determine and hash the running supervisor's executable, this runtime
    module, and the launcher, verifying each against any `expected_*` value
    supplied (raising `SupervisorError` on mismatch). Defaults the executable
    to `sys.argv[0]` when executable and absolute, else `sys.executable`."""

    candidate = executable
    allow_root_owned = False
    if candidate is None:
        candidate = Path(sys.argv[0])
        if not candidate.is_absolute() or not os.access(candidate, os.X_OK):
            candidate = Path(sys.executable)
            allow_root_owned = True
    canonical = _validate_owned_regular_file(
        candidate,
        executable=True,
        allow_root_owned=allow_root_owned,
    )
    digest = _sha256_file(canonical, allow_root_owned=allow_root_owned)
    runtime_digest = _sha256_file(Path(__file__).resolve())
    launcher_digest = _launcher_sha256(launcher)
    if expected_sha256 and digest != expected_sha256:
        raise SupervisorError(
            "supervisor executable hash differs from the installed LaunchAgent",
            EX_CONFIG,
        )
    if expected_version and PACKAGE_VERSION != expected_version:
        raise SupervisorError(
            "supervisor package version "
            f"{PACKAGE_VERSION!r} differs from the installed LaunchAgent "
            f"{expected_version!r}",
            EX_CONFIG,
        )
    if expected_runtime_sha256 and runtime_digest != expected_runtime_sha256:
        raise SupervisorError(
            "supervisor runtime hash differs from the installed LaunchAgent",
            EX_CONFIG,
        )
    if expected_launcher_sha256 and launcher_digest != expected_launcher_sha256:
        raise SupervisorError(
            "Vibecrafted launcher hash differs from the installed LaunchAgent",
            EX_CONFIG,
        )
    return SupervisorIdentity(
        canonical,
        digest,
        runtime_digest,
        PACKAGE_VERSION,
        launcher_digest,
    )


def _launcher_sha256(
    launcher: Path,
    *,
    expected_sha256: str | None = None,
) -> str:
    """Hash the launcher, requiring the path to already be canonical and, if
    `expected_sha256` is given, matching it exactly."""

    canonical = _validate_owned_regular_file(launcher, executable=True)
    if canonical != launcher:
        raise SupervisorError("launcher path must already be canonical", EX_CONFIG)
    digest = _sha256_file(canonical)
    if expected_sha256 and digest != expected_sha256:
        raise SupervisorError(
            "Vibecrafted launcher hash differs from the installed LaunchAgent",
            EX_CONFIG,
        )
    return digest


def _validate_existing_destination(path: Path) -> None:
    """No-op if `path` doesn't exist yet; otherwise require it to be an owned,
    non-symlinked, single-hardlink regular file before it can be replaced."""

    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or path.is_symlink()
    ):
        raise SupervisorError(
            f"refusing to replace non-regular or unowned path: {path}",
            EX_CONFIG,
        )


def _fsync_directory(path: Path) -> None:
    """fsync a directory's inode so a preceding rename into it is durable."""

    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_private_write(path: Path, payload: bytes) -> bool:
    """Write `payload` to `path` at mode 0600 via tempfile+fsync+rename, made
    idempotent (returns False, no rename) when the existing content already
    matches. Returns True when a write actually occurred."""

    _ensure_owned_directory(path.parent)
    _validate_existing_destination(path)
    if path.is_file() and _read_owned_bytes(path) == payload:
        descriptor = os.open(
            path,
            os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            visible = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.getuid()
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
            ):
                raise SupervisorError(
                    f"path changed during idempotent write: {path}",
                    EX_CONFIG,
                )
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        return False

    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        _validate_existing_destination(path)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    """Serialize `payload` as sorted, indented JSON and write it atomically."""

    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    _atomic_private_write(path, encoded)


def _open_verified_lock(path: Path, *, create: bool) -> int:
    """Open (optionally creating) the lock file, verifying by fd that it is a
    stable, owned, single-hardlink regular file, and chmod it to 0600. Returns
    the open descriptor; propagates `FileNotFoundError` when `create` is False
    and the path is absent."""

    _ensure_owned_directory(path.parent)
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise SupervisorError(f"cannot open supervisor lock {path}: {exc}") from exc

    try:
        opened = os.fstat(descriptor)
        visible = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise SupervisorError(
                f"supervisor lock is not a stable owned regular file: {path}",
                EX_CONFIG,
            )
        os.fchmod(descriptor, 0o600)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_lock_payload(descriptor: int) -> dict[str, Any] | None:
    """Read and JSON-decode the lock file's content from `descriptor`; return
    None on any decode failure or a non-object payload."""

    os.lseek(descriptor, 0, os.SEEK_SET)
    encoded = os.read(descriptor, 64 * 1024)
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_lock_payload(
    descriptor: int,
    *,
    role: str,
    service_managed: bool,
    identity: SupervisorIdentity | None,
) -> None:
    """Truncate and rewrite the lock file's content with the current holder's
    pid/role/service-managed flag and (when supplied) build identity."""

    payload = {
        "schema": SUPERVISOR_LOCK_SCHEMA,
        "pid": os.getpid(),
        "role": role,
        "service_managed": service_managed,
        "acquired_at": _utc_now(),
    }
    if identity is not None:
        payload["supervisor_executable"] = str(identity.executable)
        payload["supervisor_executable_sha256"] = identity.executable_sha256
        payload["supervisor_runtime_sha256"] = identity.runtime_sha256
        payload["build_version"] = identity.build_version
        payload["launcher_sha256"] = identity.launcher_sha256
    encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.ftruncate(descriptor, 0)
    view = memoryview(encoded)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]
    os.fsync(descriptor)


def _process_alive(pid: int) -> bool:
    """Zero-signal liveness probe; treats pid <= 1 (init/invalid) as dead."""

    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _active_generation_root(runtime_home: Path) -> Path | None:
    """Runtime generation the app published, read from
    `runtime_home/active.json`; None when that receipt is missing, malformed, or
    names a root outside `runtime_home`."""

    encoded = _read_owned_bytes(runtime_home / "active.json")
    if encoded is None:
        return None
    try:
        payload = json.loads(encoded)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    runtime_root = payload.get("runtime_root")
    if not isinstance(runtime_root, str) or not runtime_root:
        return None
    try:
        generation = _absolute_path(Path(runtime_root))
    except SupervisorError:
        return None
    if not generation.is_relative_to(runtime_home):
        return None
    return generation


def _active_generation_bin(runtime_home: Path) -> Path | None:
    generation = _active_generation_root(runtime_home)
    return generation / "bin" if generation is not None else None


def _service_path(paths: SupervisorPaths) -> str:
    """PATH to embed in the LaunchAgent plist: the active generation's bin, then
    the PATH of whoever ran `server service install`.

    launchd hands a job only the PATH written into its plist. Freezing that at
    the system set hid /opt/homebrew/bin, ~/.local/bin and ~/.cargo/bin from the
    supervisor and everything it spawns, so agent CLIs with a
    `#!/usr/bin/env node` shebang exited 127.
    """

    inherited = _sane_path_entries(os.environ.get("PATH")) or _MINIMAL_PATH.split(
        os.pathsep
    )
    generation_bin = _active_generation_bin(paths.runtime_home)
    ordered: list[str] = []
    for entry in (
        [str(generation_bin)] if generation_bin is not None else []
    ) + inherited:
        if entry not in ordered:
            ordered.append(entry)
    return os.pathsep.join(ordered)


def _sane_path_entries(value: str | None) -> list[str]:
    """Only non-empty absolute entries survive into a long-lived job's PATH.

    An empty segment (leading/trailing ``:`` or ``::``) and relative entries
    (``.``, ``bin``) are implicit current-directory lookups; freezing them into
    a launchd plist or a supervised child turns every later ``cwd`` into a
    place unintended binaries can be picked up from."""

    entries: list[str] = []
    for entry in (value or "").split(os.pathsep):
        if not entry or not entry.startswith("/"):
            continue
        if entry not in entries:
            entries.append(entry)
    return entries


def _child_environment(paths: SupervisorPaths) -> dict[str, str]:
    """Build the sanitized environment for spawned launcher subprocesses: a
    passthrough allowlist plus fixed HOME/PATH/VIBECRAFTED_* overrides so the
    child can never inherit an operator's arbitrary shell environment."""

    environment = {
        key: os.environ[key] for key in _PASSTHROUGH_ENVIRONMENT if os.environ.get(key)
    }
    environment.update(
        {
            "HOME": str(paths.operator_home),
            "PATH": _child_path(paths),
            "VIBECRAFTED_HOME": str(paths.home),
            "VIBECRAFTED_RUNTIME_HOME": str(paths.runtime_home),
            "VIBECRAFTED_SERVER_SUPERVISOR_CHILD": "1",
        }
    )
    generation = _active_generation_root(paths.runtime_home)
    if generation is not None:
        environment["VIBECRAFTED_RUNTIME_ROOT"] = str(generation)
    return environment


def _child_path(paths: SupervisorPaths) -> str:
    """PATH for spawned launcher subprocesses.

    The canonical entries stay first, so a degenerate inherited PATH can never
    cost a child the product's own bins. What the supervisor inherited from
    launchd is appended rather than dropped: that is where a Homebrew-, cargo-
    or npm-installed agent CLI actually lives, and dropping it is what made
    those tools unreachable from supervised processes.
    """

    ordered: list[str] = []
    for entry in (
        f"{paths.operator_home}/.local/bin",
        "/usr/local/bin",
        "/opt/homebrew/bin",
        *_MINIMAL_PATH.split(os.pathsep),
        *_sane_path_entries(os.environ.get("PATH")),
    ):
        if entry and entry not in ordered:
            ordered.append(entry)
    return os.pathsep.join(ordered)


def _read_owned_bytes(path: Path) -> bytes | None:
    """Read up to 64KiB from an owned, non-symlinked, single-hardlink regular
    file, verifying descriptor identity matches the named path; None on any
    trust failure or OS error."""

    try:
        visible = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(visible.st_mode)
            or visible.st_uid != os.getuid()
            or visible.st_nlink != 1
        ):
            return None
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        return None
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino):
            return None
        return os.read(descriptor, 64 * 1024)
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _read_owned_text(path: Path) -> str | None:
    """UTF-8 decode of `_read_owned_bytes`; None on read failure or bad encoding."""

    encoded = _read_owned_bytes(path)
    if encoded is None:
        return None
    try:
        return encoded.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _service_stderr_cursor(path: Path) -> tuple[int, int, int] | None:
    """Snapshot (dev, ino, size) of the service stderr log so a later read can
    report only newly appended bytes; None if the file is untrusted or absent."""

    try:
        visible = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(visible.st_mode)
            or visible.st_uid != os.getuid()
            or visible.st_nlink != 1
        ):
            return None
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        return None
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino):
            return None
        return opened.st_dev, opened.st_ino, opened.st_size
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _bounded_service_stderr(
    path: Path,
    cursor: tuple[int, int, int] | None,
) -> str | None:
    """Read the last stderr line written since `cursor` (or the trailing 4KiB
    if no valid cursor), redact bearer tokens/secret-like key=value pairs/long
    base64-ish blobs and non-ASCII bytes, and clip to 512 chars for safe
    inclusion in an error message."""

    try:
        visible = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(visible.st_mode)
            or visible.st_uid != os.getuid()
            or visible.st_nlink != 1
        ):
            return None
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        return None
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino):
            return None
        start = max(0, opened.st_size - 4096)
        if (
            cursor is not None
            and cursor[:2] == (opened.st_dev, opened.st_ino)
            and 0 <= cursor[2] <= opened.st_size
        ):
            start = max(cursor[2], start)
        os.lseek(descriptor, start, os.SEEK_SET)
        encoded = os.read(descriptor, 4096)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    lines = [line.strip() for line in encoded.decode("utf-8", "replace").splitlines()]
    if not (lines := [line for line in lines if line]):
        return None
    detail = re.sub(r"(?i)\bBearer\s+\S+", "Bearer <redacted>", lines[-1])
    detail = re.sub(
        (
            r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|"
            r"API_KEY|AUTH)[A-Z0-9_]*)\s*(?:=>|[:=])\s*\S+"
        ),
        r"\1=<redacted>",
        detail,
    )
    detail = re.sub(r"\b[A-Za-z0-9_+/=-]{32,}\b", "<redacted-token>", detail)
    detail = re.sub(r"[^\x20-\x7e]", "?", detail)
    return detail[-512:]


def _managed_pair_snapshot(paths: SupervisorPaths) -> dict[str, int | None]:
    """Read the `server`/`guardian` pid+identity files, accepting a role's pid
    only when its identity file corroborates the same schema/role/pid and the
    process is alive; unmatched roles stay None."""

    snapshot: dict[str, int | None] = {
        "server_pid": None,
        "guardian_pid": None,
    }
    for role in ("server", "guardian"):
        raw_pid = _read_owned_text(paths.server_dir / f"{role}.pid")
        raw_identity = _read_owned_text(paths.server_dir / f"{role}.identity.json")
        if raw_pid is None or raw_identity is None:
            continue
        try:
            pid = int(raw_pid.strip())
            identity = json.loads(raw_identity)
        except (ValueError, json.JSONDecodeError):
            continue
        if (
            isinstance(identity, dict)
            and identity.get("schema") == "vibecrafted.managed-process.v1"
            and identity.get("role") == role
            and identity.get("pid") == pid
            and _process_alive(pid)
        ):
            snapshot[f"{role}_pid"] = pid
    return snapshot


def _managed_pair_healthy(snapshot: dict[str, int | None]) -> bool:
    """True when both server and guardian pids are present, real ints, and
    distinct from each other."""

    server_pid = snapshot.get("server_pid")
    guardian_pid = snapshot.get("guardian_pid")
    return (
        isinstance(server_pid, int)
        and not isinstance(server_pid, bool)
        and isinstance(guardian_pid, int)
        and not isinstance(guardian_pid, bool)
        and server_pid != guardian_pid
    )


def probe_supervisor(paths: SupervisorPaths) -> SupervisorProbe:
    """Non-destructively probe the coordination lock: try a non-blocking
    exclusive flock; if it succeeds no one holds the lease (unlock and report
    not live), if it's held (EAGAIN/EACCES) read and schema-validate the
    holder's payload to decide whether it is a verified supervisor."""

    try:
        descriptor = _open_verified_lock(paths.lock_file, create=False)
    except FileNotFoundError:
        return SupervisorProbe(False, False, None, None)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise SupervisorError(
                    f"cannot probe supervisor lock {paths.lock_file}: {exc}"
                ) from exc
            payload = _read_lock_payload(descriptor)
            if payload is None:
                return SupervisorProbe(True, False, None, None)
            pid = payload.get("pid")
            service_managed = payload.get("service_managed")
            role = payload.get("role")
            executable = payload.get("supervisor_executable")
            executable_sha256 = payload.get("supervisor_executable_sha256")
            runtime_sha256 = payload.get("supervisor_runtime_sha256")
            build_version = payload.get("build_version")
            launcher_sha256 = payload.get("launcher_sha256")
            verified = (
                payload.get("schema") == SUPERVISOR_LOCK_SCHEMA
                and isinstance(pid, int)
                and not isinstance(pid, bool)
                and _process_alive(pid)
                and isinstance(service_managed, bool)
                and role in {"supervisor", "manual-stop"}
                and (
                    role == "manual-stop"
                    or (
                        isinstance(executable, str)
                        and bool(executable)
                        and isinstance(executable_sha256, str)
                        and len(executable_sha256) == 64
                        and isinstance(runtime_sha256, str)
                        and len(runtime_sha256) == 64
                        and isinstance(build_version, str)
                        and bool(build_version)
                        and (
                            launcher_sha256 is None
                            or (
                                isinstance(launcher_sha256, str)
                                and len(launcher_sha256) == 64
                            )
                        )
                    )
                )
            )
            return SupervisorProbe(
                True,
                verified,
                pid if verified else None,
                service_managed if isinstance(service_managed, bool) else None,
                role if isinstance(role, str) else None,
                executable if isinstance(executable, str) else None,
                executable_sha256 if isinstance(executable_sha256, str) else None,
                runtime_sha256 if isinstance(runtime_sha256, str) else None,
                build_version if isinstance(build_version, str) else None,
                launcher_sha256 if isinstance(launcher_sha256, str) else None,
            )
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return SupervisorProbe(False, False, None, None)
    finally:
        os.close(descriptor)


class _SupervisorLease:
    """Context manager holding the exclusive coordination flock while this
    process supervises the server+guardian pair; writes/clears the lock
    payload declaring who holds it."""

    def __init__(
        self,
        paths: SupervisorPaths,
        *,
        service_managed: bool,
        role: str = "supervisor",
        identity: SupervisorIdentity | None = None,
    ) -> None:
        """Store lease parameters; the descriptor is acquired in `__enter__`."""

        self.paths = paths
        self.service_managed = service_managed
        self.role = role
        self.identity = identity
        self.descriptor = -1

    def __enter__(self) -> Self:
        """Acquire the non-blocking exclusive flock (raising `SupervisorError`
        if another holder already has it) and write the lease payload."""

        descriptor = _open_verified_lock(self.paths.lock_file, create=True)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise SupervisorError(
                    "server supervision coordination lease is already active",
                    EX_TEMPFAIL,
                ) from exc
            raise
        self.descriptor = descriptor
        try:
            _write_lock_payload(
                descriptor,
                role=self.role,
                service_managed=self.service_managed,
                identity=self.identity,
            )
        except BaseException:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            self.descriptor = -1
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        """Release the flock and close the descriptor, if held."""

        if self.descriptor < 0:
            return
        fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        os.close(self.descriptor)
        self.descriptor = -1


def _tools_install_lock_path(paths: SupervisorPaths) -> Path:
    """Location of the cross-process installer coordination lock, honoring
    `VIBECRAFTED_TOOLS_HOME` or defaulting under `runtime_home/tools`."""

    configured = os.environ.get("VIBECRAFTED_TOOLS_HOME")
    tools_home = (
        _absolute_path(Path(configured)) if configured else paths.runtime_home / "tools"
    )
    return tools_home / _TOOLS_INSTALL_LOCK_NAME


def _validate_tools_install_descriptor(descriptor: int, lock_path: Path) -> None:
    """Verify an install-lock descriptor still identifies the named
    `lock_path` and is a regular file owned by the effective uid; raise
    `SupervisorError` otherwise."""

    try:
        opened = os.fstat(descriptor)
        named = os.stat(lock_path, follow_symlinks=False)
    except OSError as exc:
        raise SupervisorError(
            f"installer coordination lease is unavailable at {lock_path}",
            EX_TEMPFAIL,
        ) from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise SupervisorError(
            f"installer coordination lease does not own {lock_path}",
            EX_TEMPFAIL,
        )


class _ToolsInstallMutationLease:
    """Serialize service mutations with runtime publication and uv replacement."""

    def __init__(self, paths: SupervisorPaths) -> None:
        """Store paths; the descriptor is acquired or inherited in `__enter__`."""

        self.paths = paths
        self.descriptor = -1
        self.inherited = False

    def __enter__(self) -> Self:
        """Reuse an inherited lease descriptor from
        `VIBECRAFTED_INSTALL_LEASE_FD` if present and still held, else open and
        flock the install lock file, creating its directory as needed."""

        lock_path = _tools_install_lock_path(self.paths)
        inherited_raw = os.environ.get(_TOOLS_INSTALL_LEASE_ENV)
        if inherited_raw:
            try:
                descriptor = int(inherited_raw)
            except ValueError as exc:
                raise SupervisorError(
                    "invalid inherited installer coordination descriptor",
                    EX_TEMPFAIL,
                ) from exc
            _validate_tools_install_descriptor(descriptor, lock_path)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise SupervisorError(
                    "inherited installer coordination lease is not held",
                    EX_TEMPFAIL,
                ) from exc
            self.descriptor = descriptor
            self.inherited = True
            return self

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        directory = lock_path.parent.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.geteuid()
            or lock_path.parent.is_symlink()
        ):
            raise SupervisorError(
                f"tools directory is not an owned regular directory: {lock_path.parent}",
                EX_CONFIG,
            )
        descriptor = os.open(
            lock_path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            _validate_tools_install_descriptor(descriptor, lock_path)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise SupervisorError(
                    "runtime install is active; refusing concurrent service mutation",
                    EX_TEMPFAIL,
                ) from exc
            raise
        except BaseException:
            os.close(descriptor)
            raise
        self.descriptor = descriptor
        return self

    def __exit__(self, *_exc: object) -> None:
        """Release the flock and close the descriptor, unless it was inherited
        (in which case the original owner remains responsible for closing it)."""

        if self.descriptor < 0 or self.inherited:
            return
        fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        os.close(self.descriptor)
        self.descriptor = -1


def _receipt(
    config: SupervisorConfig,
    *,
    identity: SupervisorIdentity,
    managed_pair: dict[str, int | None],
    state: str,
    started_at: str,
    service_managed: bool,
    last_success_at: str | None,
    last_failure_at: str | None,
    consecutive_failures: int,
    total_failures: int,
    last_error: str | None,
    last_exit_code: int | None,
) -> dict[str, Any]:
    """Assemble the JSON receipt payload describing current supervisor state,
    identity, endpoint, and success/failure history for the status file."""

    return {
        "schema": SUPERVISOR_SCHEMA,
        "state": state,
        "supervisor_pid": os.getpid(),
        "service_managed": service_managed,
        "launcher": str(config.launcher),
        "launcher_sha256": identity.launcher_sha256,
        "supervisor_executable": {
            "path": str(identity.executable),
            "sha256": identity.executable_sha256,
            "runtime_sha256": identity.runtime_sha256,
            "version": identity.build_version,
        },
        "endpoint": {
            "host": config.host,
            "port": config.port,
            "url": config.endpoint,
            "public_url": config.public_url or config.endpoint,
            "config_path": str(config.config_file) if config.config_file else None,
        },
        "managed_pair": managed_pair,
        "started_at": started_at,
        "updated_at": _utc_now(),
        "last_success_at": last_success_at,
        "last_failure_at": last_failure_at,
        "consecutive_failures": consecutive_failures,
        "total_failures": total_failures,
        "last_error": last_error,
        "last_exit_code": last_exit_code,
    }


def _run_child(
    argv: Sequence[str],
    *,
    env: dict[str, str],
    timeout: float,
    stop_event: threading.Event,
) -> tuple[int, str]:
    """Run `argv` to completion (or until `timeout`/`stop_event`), capturing
    output into temp files rather than pipes; returns (exit_code, tail-of-
    stderr-or-stdout detail, clipped to 4000 chars). Exit code 143 signals a
    stop-event abort, 124 a timeout."""

    # Pipes make ``communicate`` wait for EOF from every descendant that inherited
    # them, even after the direct launcher has exited. The shell deck deliberately
    # daemonizes the server and guardian, so capture into private temporary files
    # and wait only for the direct child.
    with (
        tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout_file,
        tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr_file,
    ):
        process = subprocess.Popen(
            list(argv),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
        )
        deadline = time.monotonic() + timeout
        timed_out = False
        while process.poll() is None:
            if stop_event.wait(0.1):
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read()
        stderr = stderr_file.read()
        detail = (stderr or stdout).strip()
        if stop_event.is_set():
            return 143, (
                f"supervisor stopping: {detail[-3800:]}"
                if detail
                else "supervisor stopping"
            )
        if timed_out:
            return 124, (
                f"command timed out: {detail[-3800:]}"
                if detail
                else "command timed out"
            )
        return int(process.returncode or 0), detail[-4000:]


def run_supervisor(
    config: SupervisorConfig,
    *,
    stop_event: threading.Event | None = None,
    service_managed: bool = False,
    identity: SupervisorIdentity | None = None,
) -> int:
    """Foreground supervisor loop: acquire the coordination lease, then
    repeatedly launch `<launcher> server start`, verify canonical pair health,
    and back off exponentially on failure, until `stop_event` fires — at which
    point it runs `<launcher> server stop` and writes a final receipt. Always
    returns 0; failures are recorded in the receipt, not the return value."""

    event = stop_event or threading.Event()
    if (
        config.interval <= 0
        or config.maximum_backoff < config.interval
        or config.command_timeout <= 0
    ):
        raise SupervisorError("supervisor timing values are invalid", EX_CONFIG)
    _ensure_owned_directory(config.paths.server_dir)
    runtime_identity = identity or _supervisor_identity(
        launcher=config.launcher,
    )
    _launcher_sha256(
        config.launcher,
        expected_sha256=runtime_identity.launcher_sha256,
    )

    started_at = _utc_now()
    last_success_at: str | None = None
    last_failure_at: str | None = None
    consecutive_failures = 0
    total_failures = 0
    last_error: str | None = None
    last_exit_code: int | None = None
    child_environment = _child_environment(config.paths)

    with _SupervisorLease(
        config.paths,
        service_managed=service_managed,
        identity=runtime_identity,
    ):
        managed_pair = _managed_pair_snapshot(config.paths)
        _atomic_json(
            config.paths.receipt_file,
            _receipt(
                config,
                identity=runtime_identity,
                managed_pair=managed_pair,
                state="starting",
                started_at=started_at,
                service_managed=service_managed,
                last_success_at=None,
                last_failure_at=None,
                consecutive_failures=0,
                total_failures=0,
                last_error=None,
                last_exit_code=None,
            ),
        )
        try:
            while not event.is_set():
                try:
                    _launcher_sha256(
                        config.launcher,
                        expected_sha256=runtime_identity.launcher_sha256,
                    )
                except (OSError, SupervisorError) as exc:
                    consecutive_failures += 1
                    total_failures += 1
                    last_failure_at = _utc_now()
                    last_error = str(exc)
                    last_exit_code = EX_CONFIG
                    delay = min(
                        config.maximum_backoff,
                        config.interval * (2 ** min(consecutive_failures - 1, 6)),
                    )
                    managed_pair = _managed_pair_snapshot(config.paths)
                    _atomic_json(
                        config.paths.receipt_file,
                        _receipt(
                            config,
                            identity=runtime_identity,
                            managed_pair=managed_pair,
                            state="backoff",
                            started_at=started_at,
                            service_managed=service_managed,
                            last_success_at=last_success_at,
                            last_failure_at=last_failure_at,
                            consecutive_failures=consecutive_failures,
                            total_failures=total_failures,
                            last_error=last_error,
                            last_exit_code=last_exit_code,
                        ),
                    )
                    event.wait(delay)
                    continue
                return_code, detail = _run_child(
                    [
                        str(config.launcher),
                        "server",
                        "start",
                        "--host",
                        config.host,
                        "--port",
                        str(config.port),
                    ],
                    env=child_environment,
                    timeout=config.command_timeout,
                    stop_event=event,
                )
                last_exit_code = return_code
                if event.is_set():
                    break
                managed_pair = _managed_pair_snapshot(config.paths)
                managed_pair_live = _managed_pair_healthy(managed_pair)
                canonical_pair_healthy = (
                    return_code == 0
                    and managed_pair_live
                    and _pair_healthy(
                        config.launcher,
                        child_environment,
                        timeout=config.command_timeout,
                    )
                )
                if canonical_pair_healthy:
                    consecutive_failures = 0
                    last_success_at = _utc_now()
                    last_error = None
                    state = "healthy"
                    delay = config.interval
                else:
                    consecutive_failures += 1
                    total_failures += 1
                    last_failure_at = _utc_now()
                    if return_code == 0:
                        if managed_pair_live:
                            last_error = (
                                "server start returned success without canonical "
                                "managed-pair status proof"
                            )
                        else:
                            last_error = (
                                "server start returned success without a verified "
                                "live server and guardian PID pair"
                            )
                    else:
                        last_error = detail or f"server start exited {return_code}"
                    state = "backoff"
                    delay = min(
                        config.maximum_backoff,
                        config.interval * (2 ** min(consecutive_failures - 1, 6)),
                    )
                _atomic_json(
                    config.paths.receipt_file,
                    _receipt(
                        config,
                        identity=runtime_identity,
                        managed_pair=managed_pair,
                        state=state,
                        started_at=started_at,
                        service_managed=service_managed,
                        last_success_at=last_success_at,
                        last_failure_at=last_failure_at,
                        consecutive_failures=consecutive_failures,
                        total_failures=total_failures,
                        last_error=last_error,
                        last_exit_code=last_exit_code,
                    ),
                )
                event.wait(delay)
        finally:
            managed_pair = _managed_pair_snapshot(config.paths)
            try:
                _atomic_json(
                    config.paths.receipt_file,
                    _receipt(
                        config,
                        identity=runtime_identity,
                        managed_pair=managed_pair,
                        state="stopping",
                        started_at=started_at,
                        service_managed=service_managed,
                        last_success_at=last_success_at,
                        last_failure_at=last_failure_at,
                        consecutive_failures=consecutive_failures,
                        total_failures=total_failures,
                        last_error=last_error,
                        last_exit_code=last_exit_code,
                    ),
                )
            except (OSError, SupervisorError) as exc:
                print(
                    f"warning: cannot write stopping supervisor receipt: {exc}",
                    file=sys.stderr,
                )
            stop_environment = child_environment.copy()
            stop_environment["VIBECRAFTED_SERVER_SUPERVISOR_CHILD"] = "1"
            cleanup_event = threading.Event()
            _launcher_sha256(
                config.launcher,
                expected_sha256=runtime_identity.launcher_sha256,
            )
            stop_code, stop_detail = _run_child(
                [str(config.launcher), "server", "stop"],
                env=stop_environment,
                timeout=config.command_timeout,
                stop_event=cleanup_event,
            )
            if stop_code != 0:
                total_failures += 1
                consecutive_failures += 1
                last_failure_at = _utc_now()
                last_error = stop_detail or f"server stop exited {stop_code}"
                last_exit_code = stop_code
            managed_pair = _managed_pair_snapshot(config.paths)
            _atomic_json(
                config.paths.receipt_file,
                _receipt(
                    config,
                    identity=runtime_identity,
                    managed_pair=managed_pair,
                    state="stopped" if stop_code == 0 else "stop-failed",
                    started_at=started_at,
                    service_managed=service_managed,
                    last_success_at=last_success_at,
                    last_failure_at=last_failure_at,
                    consecutive_failures=consecutive_failures,
                    total_failures=total_failures,
                    last_error=last_error,
                    last_exit_code=last_exit_code,
                ),
            )
    return 0


def render_launch_agent_plist(
    config: SupervisorConfig,
    *,
    supervisor_binary: Path,
) -> bytes:
    """Render the launchd LaunchAgent plist for the supervisor: verifies and
    hashes the supervisor binary, this runtime module, and the launcher, and
    embeds those hashes plus RunAtLoad/KeepAlive so launchd both launches and
    respawns the exact build; returns the plist bytes."""

    supervisor = _validate_owned_regular_file(supervisor_binary, executable=True)
    launcher = _validate_owned_regular_file(config.launcher, executable=True)
    supervisor_sha256 = _sha256_file(supervisor)
    runtime_sha256 = _sha256_file(Path(__file__).resolve())
    launcher_sha256 = _sha256_file(launcher)
    for directory in (
        config.paths.server_dir,
        config.paths.runtime_home,
        config.paths.launch_agent_file.parent,
    ):
        _ensure_owned_directory(directory)
    service_environment = {
        "HOME": str(config.paths.operator_home),
        "PATH": _service_path(config.paths),
        "VIBECRAFTED_HOME": str(config.paths.home),
        "VIBECRAFTED_RUNTIME_HOME": str(config.paths.runtime_home),
        "VC_SERVER_PUBLIC_URL": config.public_url or config.endpoint,
        "VIBECRAFTED_SERVER_CONFIG": str(config.config_file or ""),
        "VIBECRAFTED_SERVER_SERVICE": "launchd",
        "VIBECRAFTED_SERVER_SUPERVISOR_PATH": str(supervisor),
        "VIBECRAFTED_SERVER_SUPERVISOR_SHA256": supervisor_sha256,
        "VIBECRAFTED_SERVER_SUPERVISOR_RUNTIME_SHA256": runtime_sha256,
        "VIBECRAFTED_SERVER_SUPERVISOR_VERSION": PACKAGE_VERSION,
        "VIBECRAFTED_SERVER_LAUNCHER_SHA256": launcher_sha256,
        "VIBECRAFTED_TRIAGE_RUN": os.environ.get("VIBECRAFTED_TRIAGE_RUN", "1"),
    }
    generation = _active_generation_root(config.paths.runtime_home)
    if generation is not None:
        service_environment["VIBECRAFTED_RUNTIME_ROOT"] = str(generation)
    payload: dict[str, Any] = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            str(supervisor),
            "run",
            "--supervisor-bin",
            str(supervisor),
            "--expected-supervisor-sha256",
            supervisor_sha256,
            "--expected-runtime-sha256",
            runtime_sha256,
            "--expected-build-version",
            PACKAGE_VERSION,
            "--expected-launcher-sha256",
            launcher_sha256,
            "--launcher",
            str(launcher),
            "--home",
            str(config.paths.home),
            "--runtime-home",
            str(config.paths.runtime_home),
            "--host",
            config.host,
            "--port",
            str(config.port),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(config.paths.stdout_log),
        "StandardErrorPath": str(config.paths.stderr_log),
        "EnvironmentVariables": service_environment,
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def install_service(
    config: SupervisorConfig,
    *,
    supervisor_binary: Path,
) -> bool:
    """Render and atomically write the LaunchAgent plist; returns True only if
    the file's content actually changed."""

    rendered = render_launch_agent_plist(
        config,
        supervisor_binary=supervisor_binary,
    )
    return _atomic_private_write(config.paths.launch_agent_file, rendered)


def _installed_service_identity(paths: SupervisorPaths) -> SupervisorIdentity | None:
    """Parse the identity fields embedded in the installed LaunchAgent plist's
    `EnvironmentVariables`; None if the plist is missing, malformed, or any
    expected identity field is absent/mistyped."""

    encoded = _read_owned_bytes(paths.launch_agent_file)
    if encoded is None:
        return None
    try:
        payload = plistlib.loads(encoded)
    except (plistlib.InvalidFileException, ExpatError):
        return None
    if not isinstance(payload, dict):
        return None
    environment = payload.get("EnvironmentVariables")
    if not isinstance(environment, dict):
        return None
    executable = environment.get("VIBECRAFTED_SERVER_SUPERVISOR_PATH")
    digest = environment.get("VIBECRAFTED_SERVER_SUPERVISOR_SHA256")
    runtime_digest = environment.get("VIBECRAFTED_SERVER_SUPERVISOR_RUNTIME_SHA256")
    version = environment.get("VIBECRAFTED_SERVER_SUPERVISOR_VERSION")
    launcher_digest = environment.get("VIBECRAFTED_SERVER_LAUNCHER_SHA256")
    if (
        not isinstance(executable, str)
        or not executable
        or not isinstance(digest, str)
        or len(digest) != 64
        or not isinstance(runtime_digest, str)
        or len(runtime_digest) != 64
        or not isinstance(version, str)
        or not version
        or not isinstance(launcher_digest, str)
        or len(launcher_digest) != 64
    ):
        return None
    return SupervisorIdentity(
        Path(executable),
        digest,
        runtime_digest,
        version,
        launcher_digest,
    )


def _installed_service_launcher(paths: SupervisorPaths) -> Path | None:
    """Extract the `--launcher` argument value from the installed LaunchAgent
    plist's `ProgramArguments`; None if the plist is missing/malformed or the
    argument is absent."""

    encoded = _read_owned_bytes(paths.launch_agent_file)
    if encoded is None:
        return None
    try:
        payload = plistlib.loads(encoded)
    except (plistlib.InvalidFileException, ExpatError):
        return None
    if not isinstance(payload, dict):
        return None
    arguments = payload.get("ProgramArguments")
    if not isinstance(arguments, list) or not all(
        isinstance(argument, str) for argument in arguments
    ):
        return None
    try:
        launcher_index = arguments.index("--launcher") + 1
        launcher = arguments[launcher_index]
    except (ValueError, IndexError):
        return None
    if not launcher:
        return None
    return Path(launcher)


def _probe_is_supervisor(probe: SupervisorProbe) -> bool:
    """True when the probe found a live, schema-verified `supervisor`-role
    lease holder (as opposed to a `manual-stop` cleanup lease)."""

    return probe.live and probe.verified and probe.role == "supervisor"


def _probe_matches_identity(
    probe: SupervisorProbe,
    identity: SupervisorIdentity | None,
    *,
    service_managed: bool,
) -> bool:
    """True when the probe reports a live supervisor whose service-managed
    flag and full build fingerprint exactly match `identity`."""

    return (
        identity is not None
        and _probe_is_supervisor(probe)
        and probe.service_managed is service_managed
        and probe.executable == str(identity.executable)
        and probe.executable_sha256 == identity.executable_sha256
        and probe.runtime_sha256 == identity.runtime_sha256
        and probe.build_version == identity.build_version
        and probe.launcher_sha256 == identity.launcher_sha256
    )


def _launcher_matches_identity(
    launcher: Path,
    identity: SupervisorIdentity | None,
) -> bool:
    """True when `launcher`'s current hash still matches `identity`'s recorded
    launcher hash; False on any mismatch, missing identity, or OS error."""

    if identity is None:
        return False
    try:
        _launcher_sha256(launcher, expected_sha256=identity.launcher_sha256)
    except (OSError, SupervisorError):
        return False
    return True


def _launchctl(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run `/bin/launchctl` with `args` under a minimal sanitized environment
    and a 15s timeout; raises `SupervisorError` if launchctl is unavailable
    (non-macOS)."""

    launchctl = Path("/bin/launchctl")
    if not launchctl.is_file():
        raise SupervisorError(
            "server service is macOS launchd-only; /bin/launchctl is unavailable",
            EX_CONFIG,
        )
    return subprocess.run(
        [str(launchctl), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env={
            key: value
            for key, value in {
                "HOME": str(Path.home().resolve()),
                # /bin/launchctl is an absolute invocation and needs nothing
                # else; this PATH stays minimal on purpose.
                "PATH": _MINIMAL_PATH,
                "LANG": os.environ.get("LANG"),
                "LC_ALL": os.environ.get("LC_ALL"),
                "LC_CTYPE": os.environ.get("LC_CTYPE"),
                "TMPDIR": os.environ.get("TMPDIR"),
            }.items()
            if value
        },
    )


def _launch_domain() -> str:
    """launchd GUI domain string for the current uid."""

    return f"gui/{os.getuid()}"


def _launch_target() -> str:
    """Fully-qualified launchd service target for the supervisor's LaunchAgent."""

    return f"{_launch_domain()}/{LAUNCH_AGENT_LABEL}"


def _launchctl_loaded() -> bool:
    """True when `launchctl print` for the target job succeeds (job loaded)."""

    return _launchctl(["print", _launch_target()]).returncode == 0


def _launchctl_print_value(
    payload: str,
    key: str,
    *,
    separator: str,
    section: str | None = None,
) -> str | None:
    """Scrape one `key <separator> value` line out of `launchctl print` text
    output, optionally scoped inside a `section = { ... }` block; None if not
    found or the value is empty."""

    prefix = f"{key} {separator} "
    in_section = section is None
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not in_section:
            if line == f"{section} = {{":
                in_section = True
            continue
        if section is not None and line == "}":
            return None
        if line.startswith(prefix):
            value = line.removeprefix(prefix)
            return value or None
    return None


def _launchctl_start_diagnostics() -> tuple[str, bool]:
    """Best-effort `launchctl print` snapshot of state/pid/runs/last-exit-code
    for error messages; returns (human-readable detail, whether any process
    activity was actually observed)."""

    try:
        result = _launchctl(["print", _launch_target()])
    except (OSError, subprocess.SubprocessError, SupervisorError):
        return "launchctl(print=unavailable)", False
    if result.returncode != 0:
        return f"launchctl(print-exit={result.returncode})", False

    state = _launchctl_print_value(result.stdout, "state", separator="=")
    if state is None or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", state) is None:
        state = "-"

    numeric: dict[str, str] = {}
    for rendered, key in (
        ("pid", "pid"),
        ("runs", "runs"),
        ("last-exit-code", "last exit code"),
    ):
        value = _launchctl_print_value(result.stdout, key, separator="=")
        numeric[rendered] = (
            value
            if value is not None and re.fullmatch(r"-?[0-9]{1,12}", value)
            else "-"
        )

    pid = int(numeric["pid"]) if numeric["pid"] != "-" else None
    runs = int(numeric["runs"]) if numeric["runs"] != "-" else None
    last_exit = (
        int(numeric["last-exit-code"]) if numeric["last-exit-code"] != "-" else None
    )
    process_observed = (
        (pid is not None and pid > 1)
        or (runs is not None and runs > 0)
        or last_exit is not None
        or state in {"running", "spawned", "exited", "throttled"}
    )
    detail = (
        f"launchctl(state={state},pid={numeric['pid']},runs={numeric['runs']},"
        f"last-exit-code={numeric['last-exit-code']})"
    )
    return detail, process_observed


def _launchctl_job_owns_paths(paths: SupervisorPaths) -> bool:
    """Cross-check the loaded launchd job's plist path, program path, and
    environment (supervisor, home, runtime_home, operator_home) against `paths`,
    to detect a stale job definition pointed at a different install."""

    result = _launchctl(["print", _launch_target()])
    if result.returncode != 0:
        return False
    actual_values = {
        "plist": _launchctl_print_value(result.stdout, "path", separator="="),
        "program": _launchctl_print_value(result.stdout, "program", separator="="),
        "supervisor": _launchctl_print_value(
            result.stdout,
            "VIBECRAFTED_SERVER_SUPERVISOR_PATH",
            separator="=>",
            section="environment",
        ),
        "home": _launchctl_print_value(
            result.stdout,
            "VIBECRAFTED_HOME",
            separator="=>",
            section="environment",
        ),
        "runtime_home": _launchctl_print_value(
            result.stdout,
            "VIBECRAFTED_RUNTIME_HOME",
            separator="=>",
            section="environment",
        ),
        "operator_home": _launchctl_print_value(
            result.stdout,
            "HOME",
            separator="=>",
            section="environment",
        ),
    }
    if any(value is None for value in actual_values.values()):
        return False
    try:
        actual_paths = {
            key: _absolute_path(Path(value))
            for key, value in actual_values.items()
            if value is not None
        }
    except (OSError, SupervisorError):
        return False
    return (
        actual_paths["plist"] == paths.launch_agent_file
        and actual_paths["program"] == actual_paths["supervisor"]
        and actual_paths["home"] == paths.home
        and actual_paths["runtime_home"] == paths.runtime_home
        and actual_paths["operator_home"] == paths.operator_home
    )


def _require_macos_service() -> None:
    """Raise `SupervisorError` when not running on macOS; the service
    management surface is launchd-only."""

    if sys.platform != "darwin":
        raise SupervisorError(
            "server service is macOS launchd-only; this platform is unsupported "
            "and no service state was changed",
            EX_CONFIG,
        )


def _wait_for_supervisor(
    paths: SupervisorPaths,
    *,
    live: bool,
    timeout: float = 10.0,
) -> SupervisorProbe:
    """Poll `probe_supervisor` until its liveness matches `live` or `timeout`
    elapses; returns whatever the last probe observed either way."""

    deadline = time.monotonic() + timeout
    probe = probe_supervisor(paths)
    while probe.live != live and time.monotonic() < deadline:
        time.sleep(0.1)
        probe = probe_supervisor(paths)
    return probe


def _wait_for_managed_supervisor(
    config: SupervisorConfig,
    *,
    identity: SupervisorIdentity,
    previous_pid: int | None = None,
    timeout: float = 10.0,
) -> SupervisorProbe:
    """Poll until the coordination lease is held by a service-managed
    supervisor matching `identity` and (if `previous_pid` given) running under
    a new pid, or until `timeout` elapses."""

    deadline = time.monotonic() + timeout
    probe = probe_supervisor(config.paths)
    while time.monotonic() < deadline:
        if _probe_matches_identity(probe, identity, service_managed=True) and (
            previous_pid is None or probe.pid != previous_pid
        ):
            return probe
        time.sleep(0.1)
        probe = probe_supervisor(config.paths)
    return probe


def _pair_healthy(
    launcher: Path,
    environment: dict[str, str],
    *,
    timeout: float = 60.0,
) -> bool:
    """Run `<launcher> server status` and require both "Server: RUNNING" and
    "Guardian: RUNNING" in stdout with exit code 0; False on any subprocess
    error."""

    try:
        result = subprocess.run(
            [str(launcher), "server", "status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return (
        result.returncode == 0
        and "Server: RUNNING" in result.stdout
        and "Guardian: RUNNING" in result.stdout
    )


def service_status(config: SupervisorConfig) -> ServiceStatus:
    """Compose a full `ServiceStatus` from the LaunchAgent's presence,
    launchctl's load state, the lease probe, and a live pair-health check."""

    installed = False
    if (
        config.paths.launch_agent_file.exists()
        or config.paths.launch_agent_file.is_symlink()
    ):
        _validate_owned_regular_file(
            config.paths.launch_agent_file,
            allow_symlink=False,
        )
        installed = True
    loaded = _launchctl_loaded() if sys.platform == "darwin" else False
    probe = probe_supervisor(config.paths)
    identity = _installed_service_identity(config.paths) if installed else None
    environment = _child_environment(config.paths)
    pair_snapshot = _managed_pair_snapshot(config.paths)
    launcher_current = _launcher_matches_identity(config.launcher, identity)
    pair_healthy = (
        launcher_current
        and _managed_pair_healthy(pair_snapshot)
        and _pair_healthy(
            config.launcher,
            environment,
            timeout=config.command_timeout,
        )
    )
    return ServiceStatus(
        installed=installed,
        loaded=loaded,
        supervisor_live=probe.live,
        supervisor_verified=_probe_is_supervisor(probe),
        pair_healthy=pair_healthy,
        supervisor_pid=probe.pid,
        supervisor_service_managed=(
            _probe_is_supervisor(probe) and probe.service_managed is True
        ),
        build_current=_probe_matches_identity(
            probe,
            identity,
            service_managed=True,
        )
        and launcher_current,
    )


def start_service(config: SupervisorConfig) -> None:
    """Load (or kickstart) the LaunchAgent and block until the installed
    supervisor identity acquires the coordination lease, raising
    `SupervisorError` with diagnostics on any inconsistency or timeout along
    the way (unowned lease, foreground supervisor already active, etc.)."""

    _require_macos_service()
    if not config.paths.launch_agent_file.is_file():
        raise SupervisorError(
            "server service is not installed; run "
            "'vibecrafted server service install' first",
            EX_CONFIG,
        )
    _validate_owned_regular_file(
        config.paths.launch_agent_file,
        allow_symlink=False,
    )
    identity = _installed_service_identity(config.paths)
    if identity is None:
        raise SupervisorError(
            "installed LaunchAgent has no verified supervisor identity; reinstall it",
            EX_CONFIG,
        )
    _launcher_sha256(
        config.launcher,
        expected_sha256=identity.launcher_sha256,
    )
    stderr_cursor = _service_stderr_cursor(config.paths.stderr_log)
    loaded = _launchctl_loaded()
    probe = probe_supervisor(config.paths)
    if loaded and probe.live and not _probe_is_supervisor(probe):
        raise SupervisorError(
            "launchd is loaded but a non-supervisor coordination lease is held",
            EX_TEMPFAIL,
        )
    if loaded and _probe_is_supervisor(probe) and probe.service_managed is not True:
        raise SupervisorError(
            "launchd is loaded but a foreground supervisor owns the lock; "
            "refusing to report service startup success",
            EX_TEMPFAIL,
        )
    if probe.live and not loaded:
        raise SupervisorError(
            "a server supervision coordination lease is already active; stop it "
            "before starting the launchd service",
            EX_TEMPFAIL,
        )
    if not loaded:
        result = _launchctl(["enable", _launch_target()])
        if result.returncode != 0:
            raise SupervisorError(
                f"launchctl enable failed: {result.stderr.strip()}",
                result.returncode or 1,
            )
        result = _launchctl(
            [
                "bootstrap",
                _launch_domain(),
                str(config.paths.launch_agent_file),
            ]
        )
        if result.returncode != 0 and not _launchctl_loaded():
            raise SupervisorError(
                f"launchctl bootstrap failed: {result.stderr.strip()}",
                result.returncode or 1,
            )
    probe = probe_supervisor(config.paths)
    if not loaded:
        # `bootstrap` registers the job, but launchd may defer its first
        # RunAtLoad execution.  A plain kickstart closes that startup race
        # without `-k`, which would terminate a supervisor that already won it.
        result = _launchctl(["kickstart", _launch_target()])
        if result.returncode != 0:
            raise SupervisorError(
                f"launchctl kickstart failed: {result.stderr.strip()}",
                result.returncode or 1,
            )
    elif not _probe_matches_identity(
        probe,
        identity,
        service_managed=True,
    ):
        result = _launchctl(["kickstart", "-k", _launch_target()])
        if result.returncode != 0:
            raise SupervisorError(
                f"launchctl kickstart failed: {result.stderr.strip()}",
                result.returncode or 1,
            )
    probe = _wait_for_managed_supervisor(config, identity=identity)
    if not _probe_matches_identity(probe, identity, service_managed=True):
        launchctl_detail, process_observed = _launchctl_start_diagnostics()
        stderr_detail = _bounded_service_stderr(
            config.paths.stderr_log,
            stderr_cursor,
        )
        if probe.live:
            failure = (
                "launchd supervisor acquired the coordination lock but the "
                "installed identity was rejected"
            )
        elif process_observed or stderr_detail is not None:
            failure = (
                "launchd supervisor process started but exited or stalled before "
                "acquiring the coordination lock"
            )
        else:
            failure = (
                "launchd job did not start and no supervisor acquired the "
                "coordination lock"
            )
        diagnostics = launchctl_detail
        if stderr_detail is not None:
            diagnostics += f"; supervisor-stderr={stderr_detail}"
        raise SupervisorError(
            f"{failure}; {diagnostics}",
            EX_TEMPFAIL,
        )


def stop_service(config: SupervisorConfig) -> None:
    """Unload the LaunchAgent job (`launchctl bootout`) and wait for the
    coordination lease to clear, raising `SupervisorError` if a foreground
    (non-launchd) supervisor holds it, if the job doesn't own the target
    paths, or if the lease is still held after unload."""

    _require_macos_service()
    identity = _installed_service_identity(config.paths)
    if identity is None:
        raise SupervisorError(
            "installed LaunchAgent has no verified supervisor identity; reinstall it",
            EX_CONFIG,
        )
    _launcher_sha256(
        config.launcher,
        expected_sha256=identity.launcher_sha256,
    )
    loaded = _launchctl_loaded()
    probe = probe_supervisor(config.paths)
    if not loaded and probe.live:
        raise SupervisorError(
            "the active supervisor is not owned by launchd; stop the foreground "
            "supervisor with SIGTERM or Ctrl-C",
            EX_TEMPFAIL,
        )
    if loaded:
        if not _launchctl_job_owns_paths(config.paths):
            raise SupervisorError(
                "launchd label is loaded for foreign runtime paths; refusing "
                "bootout with zero service mutation",
                EX_TEMPFAIL,
            )
        result = _launchctl(["bootout", _launch_target()])
        if result.returncode != 0 and _launchctl_loaded():
            raise SupervisorError(
                f"launchctl bootout failed: {result.stderr.strip()}",
                result.returncode or 1,
            )
    probe = _wait_for_supervisor(config.paths, live=False)
    if probe.live:
        raise SupervisorError(
            "launchd was unloaded but the supervisor lock is still held; "
            "refusing to signal an unverified PID",
            EX_TEMPFAIL,
        )
    with _SupervisorLease(
        config.paths,
        service_managed=False,
        role="manual-stop",
    ):
        if _launchctl_loaded():
            raise SupervisorError(
                "launchd became active while acquiring the service-stop cleanup "
                "lease; refusing uncoordinated cleanup",
                EX_TEMPFAIL,
            )
        environment = _child_environment(config.paths)
        environment["VIBECRAFTED_SERVER_SUPERVISOR_CHILD"] = "1"
        result = subprocess.run(
            [str(config.launcher), "server", "stop"],
            check=False,
            capture_output=True,
            text=True,
            timeout=config.command_timeout,
            env=environment,
        )
        if result.returncode != 0:
            raise SupervisorError(
                f"service unloaded but managed pair cleanup failed: "
                f"{result.stderr.strip() or result.stdout.strip()}",
                result.returncode,
            )
        if _launchctl_loaded():
            raise SupervisorError(
                "launchd became active during service-stop cleanup; refusing to "
                "report the server pair as stopped",
                EX_TEMPFAIL,
            )


def restart_service(
    config: SupervisorConfig,
    *,
    previous_pid: int | None = None,
) -> SupervisorProbe:
    """Stop then start the launchd service, requiring the post-restart
    supervisor to match the installed identity and (when a previous pid was
    live) to have actually respawned under a new pid."""

    _require_macos_service()
    if _launchctl_loaded():
        active = probe_supervisor(config.paths)
        if active.live and (
            not _probe_is_supervisor(active) or active.service_managed is not True
        ):
            raise SupervisorError(
                "refusing to reload launchd while an unowned coordination lease "
                "is active",
                EX_TEMPFAIL,
            )
        if previous_pid is None:
            previous_pid = active.pid
        stop_service(config)
    start_service(config)
    identity = _installed_service_identity(config.paths)
    if identity is None:
        raise SupervisorError(
            "reloaded LaunchAgent has no verified supervisor identity",
            EX_CONFIG,
        )
    probe = _wait_for_managed_supervisor(
        config,
        identity=identity,
        previous_pid=previous_pid,
    )
    if not _probe_matches_identity(probe, identity, service_managed=True):
        raise SupervisorError(
            "LaunchAgent reload did not activate the installed supervisor build",
            EX_TEMPFAIL,
        )
    if previous_pid is not None and probe.pid == previous_pid:
        raise SupervisorError(
            "LaunchAgent reload retained the previous supervisor PID",
            EX_TEMPFAIL,
        )
    return probe


def install_and_reconcile_service(
    config: SupervisorConfig,
    *,
    supervisor_binary: Path,
) -> tuple[bool, bool]:
    """Install/refresh the LaunchAgent plist, then restart it if it changed or
    the running supervisor no longer matches the installed identity, or start
    it fresh if launchd wasn't loaded. Returns (plist_changed, restarted)."""

    _require_macos_service()
    loaded = _launchctl_loaded()
    previous = probe_supervisor(config.paths)
    changed = install_service(config, supervisor_binary=supervisor_binary)
    installed_identity = _installed_service_identity(config.paths)
    current = _probe_matches_identity(
        previous,
        installed_identity,
        service_managed=True,
    )
    restarted = False
    if loaded and (changed or not current):
        restart_service(config, previous_pid=previous.pid)
        restarted = True
    elif not loaded:
        # `service install` is the canonical install/reconcile entrypoint used by
        # make install and doctor remediation.  A fresh definition is not a
        # running service, so always finish the contract by bootstrapping and
        # verifying the installed identity.
        start_service(config)
    return changed, restarted


def uninstall_service(config: SupervisorConfig) -> bool:
    """Stop the service if loaded (raising if a foreground supervisor is
    active instead), then delete the LaunchAgent plist. Returns whether a
    plist was actually removed."""

    _require_macos_service()
    if _launchctl_loaded():
        stop_service(config)
    elif probe_supervisor(config.paths).live:
        raise SupervisorError(
            "a foreground supervisor is active; refusing to uninstall its "
            "service definition",
            EX_TEMPFAIL,
        )
    path = config.paths.launch_agent_file
    if not path.exists() and not path.is_symlink():
        return False
    _validate_owned_regular_file(path, allow_symlink=False)
    path.unlink()
    _fsync_directory(path.parent)
    return True


def _launchd_owns_pair(paths: SupervisorPaths) -> bool:
    """True on macOS when the launchd job is loaded and its plist/environment
    corroborate ownership of `paths`; validates the plist file's trust first."""

    if paths.launch_agent_file.exists() or paths.launch_agent_file.is_symlink():
        _validate_owned_regular_file(
            paths.launch_agent_file,
            allow_symlink=False,
        )
    return sys.platform == "darwin" and _launchctl_job_owns_paths(paths)


def manual_stop_guard(paths: SupervisorPaths) -> None:
    """Raise `SupervisorError` if a launchd service or foreground supervisor
    currently owns the pair, since a manual stop would just be respawned; a
    no-op when invoked from inside a supervisor child (env flag set)."""

    if os.environ.get("VIBECRAFTED_SERVER_SUPERVISOR_CHILD") == "1":
        return
    loaded = _launchd_owns_pair(paths)
    probe = probe_supervisor(paths)
    if loaded or probe.live:
        owner = "launchd service" if loaded else "foreground supervisor"
        raise SupervisorError(
            f"server pair is owned by an active {owner}; refusing a manual stop "
            "that would immediately respawn it. Use "
            "'vibecrafted server service stop' (or stop the foreground "
            "supervisor) instead.",
            EX_TEMPFAIL,
        )


def manual_stop(config: SupervisorConfig) -> None:
    """Stop the server+guardian pair directly while holding the manual-stop
    coordination lease, refusing when launchd owns the pair and repairing
    (bootout) a launchd job that races back in mid-stop; raises
    `SupervisorError` if invoked from inside a supervisor child."""

    if os.environ.get("VIBECRAFTED_SERVER_SUPERVISOR_CHILD") == "1":
        raise SupervisorError(
            "manual-stop coordination command cannot run as a supervisor child",
            EX_CONFIG,
        )
    if _launchd_owns_pair(config.paths):
        raise SupervisorError(
            "server pair is owned by an active launchd service; use "
            "'vibecrafted server service stop' instead",
            EX_TEMPFAIL,
        )
    with _SupervisorLease(
        config.paths,
        service_managed=False,
        role="manual-stop",
    ):
        # Re-check after acquiring the common lease. A concurrent launchd start
        # can no longer race a manual stop without being observed here.
        if _launchd_owns_pair(config.paths):
            raise SupervisorError(
                "launchd became active while acquiring the manual-stop lease; "
                "use 'vibecrafted server service stop' instead",
                EX_TEMPFAIL,
            )
        environment = _child_environment(config.paths)
        environment["VIBECRAFTED_SERVER_SUPERVISOR_CHILD"] = "1"
        result = subprocess.run(
            [str(config.launcher), "server", "stop"],
            check=False,
            capture_output=True,
            text=True,
            timeout=config.command_timeout,
            env=environment,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode != 0:
            raise SupervisorError(
                "manual server pair stop failed while holding the coordination "
                f"lease (exit {result.returncode})",
                result.returncode,
            )
        if _launchd_owns_pair(config.paths):
            # The common lease prevents the reactivated launchd worker from
            # acquiring supervision ownership while we repair the race.
            repair = _launchctl(["bootout", _launch_target()])
            if repair.returncode != 0 and _launchctl_loaded():
                raise SupervisorError(
                    "launchd reactivated during manual-stop cleanup and could not "
                    f"be unloaded again: {repair.stderr.strip()}",
                    repair.returncode or EX_TEMPFAIL,
                )
            if _launchctl_loaded():
                raise SupervisorError(
                    "launchd reactivated during manual-stop cleanup and remains "
                    "loaded after repair",
                    EX_TEMPFAIL,
                )
            raise SupervisorError(
                "launchd reactivated during manual-stop cleanup; it was unloaded "
                "again and the managed pair remains stopped. Retry the intended "
                "service action explicitly.",
                EX_TEMPFAIL,
            )


def _validated_endpoint(host: str, port: int) -> tuple[str, int]:
    """Validate a host/port pair (hostname charset, length, leading dash, port
    range); raises `SupervisorError` on any violation."""

    if (
        not host
        or host != host.strip()
        or len(host) > 253
        or host.startswith("-")
        or _HOST_PATTERN.fullmatch(host) is None
    ):
        raise SupervisorError(f"invalid server host: {host!r}", 2)
    if not 1 <= port <= 65535:
        raise SupervisorError(f"server port out of range: {port}", 2)
    return host, port


def default_config(
    *,
    launcher: Path,
    home: Path | None = None,
    runtime_home: Path | None = None,
    operator_home: Path | None = None,
    host: str | None = None,
    port: int | None = None,
) -> SupervisorConfig:
    """Build a `SupervisorConfig` from environment defaults and operator
    config-file settings, letting explicit `host`/`port` arguments override
    the on-disk `[server]` values (and, when overriding, recomputing
    `public_url` from the override instead of reusing the stored one)."""

    resolved_operator_home = _absolute_path(
        operator_home or Path(os.environ.get("HOME", str(Path.home())))
    )
    resolved_home = _absolute_path(
        home
        or Path(
            os.environ.get(
                "VIBECRAFTED_HOME",
                str(resolved_operator_home / ".vibecrafted"),
            )
        )
    )
    resolved_runtime_home = _absolute_path(
        runtime_home
        or Path(
            os.environ.get(
                "VIBECRAFTED_RUNTIME_HOME",
                str(resolved_operator_home / ".local" / "share" / "vibecrafted"),
            )
        )
    )
    settings_path = config_path(operator_home=resolved_operator_home)
    settings = load_server_config(settings_path)
    validated_host, validated_port = _validated_endpoint(
        host if host is not None else settings.bind_host,
        port if port is not None else settings.port,
    )
    public_url = (
        settings.public_url
        if host is None and port is None
        else origin_for(validated_host, validated_port)
    )
    return SupervisorConfig(
        paths=SupervisorPaths.create(
            home=resolved_home,
            runtime_home=resolved_runtime_home,
            operator_home=resolved_operator_home,
        ),
        launcher=_validate_owned_regular_file(launcher, executable=True),
        host=validated_host,
        port=validated_port,
        public_url=public_url,
        config_file=settings_path,
    )


def _paths_from_args(args: argparse.Namespace) -> SupervisorPaths:
    """Build `SupervisorPaths` from the parsed `--home`/`--runtime-home`/
    `--operator-home` CLI arguments."""

    return SupervisorPaths.create(
        home=Path(args.home),
        runtime_home=Path(args.runtime_home),
        operator_home=Path(args.operator_home),
    )


def _config_from_args(args: argparse.Namespace) -> SupervisorConfig:
    """Build a `SupervisorConfig` for CLI subcommands, layering CLI
    `--host`/`--port`/timing overrides on top of the on-disk server config."""

    paths = _paths_from_args(args)
    settings_path = config_path(operator_home=paths.operator_home)
    settings = load_server_config(settings_path)
    host, port = _validated_endpoint(
        args.host if args.host is not None else settings.bind_host,
        args.port if args.port is not None else settings.port,
    )
    launcher = _validate_owned_regular_file(Path(args.launcher), executable=True)
    return SupervisorConfig(
        paths=paths,
        launcher=launcher,
        host=host,
        port=port,
        public_url=(
            settings.public_url
            if args.host is None and args.port is None
            else origin_for(host, port)
        ),
        config_file=settings_path,
        interval=args.interval,
        maximum_backoff=args.maximum_backoff,
        command_timeout=args.command_timeout,
    )


def _add_common_paths(parser: argparse.ArgumentParser) -> None:
    """Register `--home`/`--runtime-home`/`--operator-home` with environment-
    derived defaults on `parser`."""

    operator_home = _absolute_path(Path(os.environ.get("HOME", str(Path.home()))))
    home = _absolute_path(
        Path(os.environ.get("VIBECRAFTED_HOME", operator_home / ".vibecrafted"))
    )
    runtime_home = _absolute_path(
        Path(
            os.environ.get(
                "VIBECRAFTED_RUNTIME_HOME",
                operator_home / ".local" / "share" / "vibecrafted",
            )
        )
    )
    parser.add_argument("--home", default=str(home))
    parser.add_argument("--runtime-home", default=str(runtime_home))
    parser.add_argument("--operator-home", default=str(operator_home))


def _add_config_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the common path arguments plus `--launcher`, `--host`,
    `--port`, and supervisor timing knobs on `parser`."""

    _add_common_paths(parser)
    parser.add_argument("--launcher", required=True)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--maximum-backoff", type=float, default=30.0)
    parser.add_argument("--command-timeout", type=float, default=60.0)


def _build_parser() -> argparse.ArgumentParser:
    """Construct the `vc-server-supervisor` argparse CLI: `run`, `service`,
    `runtime-status`, `config`, `manual-stop-guard`, `manual-stop`, `probe`."""

    parser = argparse.ArgumentParser(prog="vc-server-supervisor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run the foreground supervisor")
    _add_config_arguments(run)
    run.add_argument("--supervisor-bin", default="")
    run.add_argument("--expected-supervisor-sha256", default="")
    run.add_argument("--expected-runtime-sha256", default="")
    run.add_argument("--expected-build-version", default="")
    run.add_argument("--expected-launcher-sha256", default="")

    service = subparsers.add_parser(
        "service",
        help="manage the macOS launchd user service",
    )
    service.add_argument(
        "action",
        choices=(
            "install",
            "reconcile",
            "restart",
            "start",
            "stop",
            "status",
            "logs",
            "uninstall",
        ),
    )
    _add_config_arguments(service)
    service.add_argument("--supervisor-bin", default="")
    service.add_argument("--json", action="store_true")

    runtime_status = subparsers.add_parser(
        "runtime-status",
        help="report supervised versus unsupervised runtime truth",
    )
    _add_common_paths(runtime_status)

    config = subparsers.add_parser(
        "config",
        help="report the effective operator-owned server configuration",
    )
    _add_common_paths(config)
    config.add_argument("--json", action="store_true")

    guard = subparsers.add_parser(
        "manual-stop-guard",
        help="refuse a manual pair stop while a supervisor owns it",
    )
    _add_common_paths(guard)

    manual = subparsers.add_parser(
        "manual-stop",
        help="stop the pair while holding the supervision coordination lease",
    )
    _add_config_arguments(manual)

    probe = subparsers.add_parser("probe", help="probe the kernel supervisor lock")
    _add_common_paths(probe)
    probe.add_argument("--json", action="store_true")
    return parser


def _install_requires_supervisor_binary(args: argparse.Namespace) -> Path:
    """Validate and return the `--supervisor-bin` path required for install/
    reconcile; raises `SupervisorError` telling the operator to run
    `make install` when it was not supplied."""

    if not args.supervisor_bin:
        raise SupervisorError(
            "vc-server-supervisor entrypoint is missing; run 'make install' first",
            EX_CONFIG,
        )
    return _validate_owned_regular_file(Path(args.supervisor_bin), executable=True)


def _print_service_status(status: ServiceStatus, *, as_json: bool) -> None:
    """Print `status` as JSON or a fixed-format human-readable summary line."""

    payload = {
        "installed": status.installed,
        "loaded": status.loaded,
        "supervisor_live": status.supervisor_live,
        "supervisor_verified": status.supervisor_verified,
        "supervisor_service_managed": status.supervisor_service_managed,
        "build_current": status.build_current,
        "pair_healthy": status.pair_healthy,
        "supervisor_pid": status.supervisor_pid,
    }
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    print(
        "Service: "
        f"installed={'yes' if status.installed else 'no'} "
        f"loaded={'yes' if status.loaded else 'no'} "
        f"supervisor-live={'yes' if status.supervisor_live else 'no'} "
        f"supervisor-verified={'yes' if status.supervisor_verified else 'no'} "
        f"service-managed={'yes' if status.supervisor_service_managed else 'no'} "
        f"build-current={'yes' if status.build_current else 'no'} "
        f"pair-healthy={'yes' if status.pair_healthy else 'no'}"
    )


def _print_server_config(args: argparse.Namespace) -> None:
    """Print the effective operator server config as JSON or a summary line,
    noting whether it came from file or built-in defaults."""

    operator_home = _absolute_path(Path(args.operator_home))
    path = config_path(operator_home=operator_home)
    configured = has_server_config(path)
    config = load_server_config(path)
    payload = {
        "bind_host": config.bind_host,
        "port": config.port,
        "public_url": config.public_url,
        "config_path": str(path),
        "source": "file" if configured else "default",
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True))
        return
    print(
        f"bind={config.bind_addr} public_url={config.public_url} "
        f"source={payload['source']} config={path}"
    )


def _runtime_status(paths: SupervisorPaths) -> int:
    """Print a one-line supervision verdict (LAUNCHD/FOREGROUND/BROKEN/
    UNSUPERVISED) and return the matching exit code (0 for a coherent state,
    1 for BROKEN)."""

    installed = False
    if paths.launch_agent_file.exists() or paths.launch_agent_file.is_symlink():
        _validate_owned_regular_file(
            paths.launch_agent_file,
            allow_symlink=False,
        )
        installed = True
    loaded = sys.platform == "darwin" and (
        (installed and _launchctl_loaded())
        or (not installed and _launchctl_job_owns_paths(paths))
    )
    probe = probe_supervisor(paths)
    identity = _installed_service_identity(paths) if installed else None
    launcher = _installed_service_launcher(paths) if installed else None
    launcher_current = launcher is not None and _launcher_matches_identity(
        launcher, identity
    )
    if (
        loaded
        and _probe_matches_identity(
            probe,
            identity,
            service_managed=True,
        )
        and launcher_current
    ):
        print(
            f"Supervision: LAUNCHD (installed=yes, loaded=yes, "
            f"supervisor PID {probe.pid})"
        )
        return 0
    if _probe_is_supervisor(probe) and probe.service_managed is False:
        print(
            f"Supervision: FOREGROUND (installed={'yes' if installed else 'no'}, "
            f"supervisor PID {probe.pid})"
        )
        return 0
    if loaded or probe.live:
        print(
            "Supervision: BROKEN "
            f"(installed={'yes' if installed else 'no'}, "
            f"loaded={'yes' if loaded else 'no'}, "
            f"lock-held={'yes' if probe.live else 'no'})"
        )
        return 1
    print(
        "Supervision: UNSUPERVISED "
        f"(service installed={'yes' if installed else 'no'}, loaded=no)"
    )
    return 0


def _service_command(args: argparse.Namespace) -> int:
    """Dispatch the `service` subcommand's action (status/logs/install/
    reconcile/restart/start/stop/uninstall), serializing mutating actions behind
    the tools-install lease; prints a confirmation line and returns an exit code
    (status returns 1 unless every health field is green)."""

    _require_macos_service()
    config = _config_from_args(args)
    if args.action == "status":
        status = service_status(config)
        _print_service_status(status, as_json=args.json)
        return (
            0
            if (
                status.installed
                and status.loaded
                and status.supervisor_live
                and status.supervisor_verified
                and status.supervisor_service_managed
                and status.build_current
                and status.pair_healthy
            )
            else 1
        )
    if args.action == "logs":
        payload = {
            "directory": str(config.paths.server_dir),
            "stdout": str(config.paths.stdout_log),
            "stderr": str(config.paths.stderr_log),
        }
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"Directory: {payload['directory']}")
            print(f"Stdout: {payload['stdout']}")
            print(f"Stderr: {payload['stderr']}")
        return 0
    with _ToolsInstallMutationLease(config.paths):
        if args.action in {"install", "reconcile"}:
            changed, restarted = install_and_reconcile_service(
                config,
                supervisor_binary=_install_requires_supervisor_binary(args),
            )
            print(
                f"LaunchAgent {'installed' if changed else 'already current'} at "
                f"{config.paths.launch_agent_file}"
                f"{'; reloaded current supervisor build' if restarted else ''}"
                "; verified service is active"
            )
            return 0
        if args.action == "restart":
            probe = restart_service(config)
            print(f"LaunchAgent reloaded; current supervisor PID {probe.pid}.")
            return 0
        if args.action == "start":
            start_service(config)
            print("LaunchAgent loaded; verified supervisor is live.")
            return 0
        if args.action == "stop":
            stop_service(config)
            print("LaunchAgent unloaded; server and guardian are stopped.")
            return 0
        changed = uninstall_service(config)
        print("LaunchAgent removed." if changed else "LaunchAgent is not installed.")
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: parse `argv` and dispatch to the matching subcommand
    handler, converting `SupervisorError`/`ServerConfigError` into a printed
    message plus their exit code."""

    args = _build_parser().parse_args(argv)
    try:
        if args.command == "run":
            config = _config_from_args(args)
            supervisor_binary = (
                Path(args.supervisor_bin) if args.supervisor_bin else None
            )
            identity = _supervisor_identity(
                supervisor_binary,
                launcher=config.launcher,
                expected_sha256=args.expected_supervisor_sha256 or None,
                expected_runtime_sha256=args.expected_runtime_sha256 or None,
                expected_version=args.expected_build_version or None,
                expected_launcher_sha256=args.expected_launcher_sha256 or None,
            )
            stop_event = threading.Event()

            def request_stop(_signum: int, _frame: object) -> None:
                """SIGTERM/SIGINT handler: signal the supervisor loop to stop."""

                stop_event.set()

            previous_term = signal.signal(signal.SIGTERM, request_stop)
            previous_int = signal.signal(signal.SIGINT, request_stop)
            try:
                return run_supervisor(
                    config,
                    stop_event=stop_event,
                    service_managed=(
                        os.environ.get("VIBECRAFTED_SERVER_SERVICE") == "launchd"
                    ),
                    identity=identity,
                )
            finally:
                signal.signal(signal.SIGTERM, previous_term)
                signal.signal(signal.SIGINT, previous_int)
        if args.command == "service":
            return _service_command(args)
        if args.command == "config":
            _print_server_config(args)
            return 0
        paths = _paths_from_args(args)
        if args.command == "runtime-status":
            return _runtime_status(paths)
        if args.command == "manual-stop-guard":
            manual_stop_guard(paths)
            return 0
        if args.command == "manual-stop":
            config = _config_from_args(args)
            with _ToolsInstallMutationLease(config.paths):
                manual_stop(config)
            return 0
        probe = probe_supervisor(paths)
        payload = {
            "live": probe.live,
            "verified": probe.verified,
            "pid": probe.pid,
            "service_managed": probe.service_managed,
            "role": probe.role,
            "supervisor_executable": probe.executable,
            "supervisor_executable_sha256": probe.executable_sha256,
            "supervisor_runtime_sha256": probe.runtime_sha256,
            "build_version": probe.build_version,
            "launcher_sha256": probe.launcher_sha256,
        }
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(
                f"live={'yes' if probe.live else 'no'} "
                f"verified={'yes' if probe.verified else 'no'} "
                f"pid={probe.pid or '-'}"
            )
        return 0 if probe.live and probe.verified else 1
    except SupervisorError as exc:
        print(f"vc-server-supervisor: {exc}", file=sys.stderr)
        return exc.exit_code
    except ServerConfigError as exc:
        print(f"vc-server-supervisor: {exc}", file=sys.stderr)
        return EX_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
