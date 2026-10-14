"""One native vc-terminal host per user: admission, reuse and launch receipts.

Only ``scripts/vc-terminal-product-entry.sh`` runs this file, as an isolated
stdlib-only script (``python3 -I -B <generation>/vibecrafted-core/...``)::

    terminal_host.py admit --wrapper-pid PID --host HOST --config FILE [--receipt PATH] -- ARGV...
    terminal_host.py spawn ENV_FILE -- COMMAND...   (per-window exec shim)

It prints exactly one decision line and exits 0:

``fresh``
    No admitted host is alive. The wrapper (PID ``--wrapper-pid``) must exec
    the host in place, so the recorded PID and kernel start token become the
    GUI host's own identity and its default ``$TMPDIR/Alacritty-<pid>.sock``.
``reused``
    The host's native ``msg create-window`` client delivered one request to
    the admitted host. Delivery is not window readiness and never agent start.

Any refusal prints a reason on stderr and exits non-zero without starting,
signalling, focusing or unlinking anything it does not positively own:
64 usage, 69 unsupported platform, 75 unconfirmed/busy/ambiguous, 78 unsafe
state. A refusal never falls back to a second host.

macOS proof only: kernel PID/UID/start token and executable path come from
libproc, socket peers from ``LOCAL_PEERPID``/``LOCAL_PEERCRED``. Linux has no
equivalent proof here and is refused (the wrapper keeps the flat host there).

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

RECORD_SCHEMA = "vibecrafted.terminal-host.v1"
RECEIPT_SCHEMA = "vibecrafted.terminal-launch-receipt.v1"
STATE_DIR_ENV = "VIBECRAFTED_TERMINAL_HOST_DIR"
LOCK_WAIT_SECONDS = 20.0
STARTUP_WAIT_SECONDS = 15.0
DELIVERY_TIMEOUT_SECONDS = 10.0
SOCKADDR_LIMIT = 103
EX_USAGE, EX_UNAVAILABLE, EX_TEMPFAIL, EX_CONFIG = 64, 69, 75, 78

# Window-level options accepted by `msg create-window` (WindowOptions).
_VALUE_OPTIONS = {
    "--working-directory",
    "--title",
    "-T",
    "-t",
    "--class",
    "-o",
    "--option",
}
_FLAG_OPTIONS = {"--hold"}
ENV_FILE_MAX_AGE_SECONDS = 120.0

# <sys/un.h>: SOL_LOCAL, LOCAL_PEERCRED, LOCAL_PEERPID. <sys/proc_info.h>.
_SOL_LOCAL, _LOCAL_PEERCRED, _LOCAL_PEERPID = 0, 0x001, 0x002
_PROC_PIDTBSDINFO, _BSDINFO_SIZE, _PATH_MAX = 3, 136, 4096


class Refusal(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    uid: int
    start: str
    executable: str


def _libsystem() -> ctypes.CDLL:
    if sys.platform != "darwin":
        raise Refusal(EX_UNAVAILABLE, "kernel host identity is only proven on macOS")
    return ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)


def process_identity(pid: int) -> ProcessIdentity | None:
    """Kernel facts for one PID, or None when it no longer exists."""
    if pid <= 0:
        return None
    lib = _libsystem()
    info = ctypes.create_string_buffer(_BSDINFO_SIZE)
    size = lib.proc_pidinfo(
        ctypes.c_int(pid),
        ctypes.c_int(_PROC_PIDTBSDINFO),
        ctypes.c_uint64(0),
        info,
        ctypes.c_int(_BSDINFO_SIZE),
    )
    if size != _BSDINFO_SIZE:
        return None
    raw = info.raw
    found_pid = int.from_bytes(raw[12:16], "little")
    uid = int.from_bytes(raw[20:24], "little")
    start_sec = int.from_bytes(raw[120:128], "little")
    start_usec = int.from_bytes(raw[128:136], "little")
    path = ctypes.create_string_buffer(_PATH_MAX)
    length = lib.proc_pidpath(ctypes.c_int(pid), path, ctypes.c_uint32(_PATH_MAX))
    if found_pid != pid or length <= 0:
        return None
    return ProcessIdentity(
        pid=pid,
        uid=uid,
        start=f"{start_sec}.{start_usec:06d}",
        executable=os.fsdecode(path.raw[:length]),
    )


def _private_directory(path: Path, *, create: bool) -> Path:
    if not path.is_absolute():
        raise Refusal(EX_CONFIG, f"state directory is not absolute: {path}")
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if not create:
            raise Refusal(EX_CONFIG, f"private directory missing: {path}") from None
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise Refusal(EX_CONFIG, f"private directory is not a real directory: {path}")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise Refusal(EX_CONFIG, f"private directory is shared or foreign: {path}")
    return path


def native_socket_path(environ: dict[str, str], pid: int) -> Path:
    """The host's own default IPC path on macOS: ``$TMPDIR/Alacritty-<pid>.sock``."""
    temp = environ.get("TMPDIR", "")
    if not temp.startswith("/"):
        raise Refusal(EX_CONFIG, "TMPDIR is required to locate the host IPC socket")
    directory = _private_directory(Path(os.path.normpath(temp)), create=False)
    return directory / f"Alacritty-{pid}.sock"


def state_directory(environ: dict[str, str], host: str) -> Path:
    """Private per-user namespace, keyed by the exact host path it admits."""
    configured = environ.get(STATE_DIR_ENV)
    if configured:
        return _private_directory(Path(configured), create=True)
    base = _private_directory(Path(f"/tmp/vc-terminal-{os.getuid()}"), create=True)
    key = hashlib.sha256(os.fsencode(host)).hexdigest()[:16]
    return _private_directory(base / key, create=True)


def _owned_regular(fd: int, label: str) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
    ):
        raise Refusal(EX_CONFIG, f"{label} is not a private regular file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise Refusal(EX_CONFIG, f"{label} is readable by others")
    return info


def acquire_lock(directory: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(directory / "admission.lock", flags, 0o600)
    except OSError as error:
        raise Refusal(EX_CONFIG, f"admission lock refused: {error}") from None
    try:
        _owned_regular(fd, "admission lock")
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Refusal(
                        EX_TEMPFAIL, "another terminal admission is still running"
                    ) from None
                time.sleep(0.05)
    except BaseException:
        os.close(fd)
        raise


def read_record(directory: Path) -> dict | None:
    try:
        fd = os.open(
            directory / "host.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Refusal(EX_CONFIG, f"host record refused: {error}") from None
    try:
        _owned_regular(fd, "host record")
        data = os.read(fd, 65536)
    finally:
        os.close(fd)
    try:
        record = json.loads(data)
    except ValueError:
        raise Refusal(EX_CONFIG, "host record is not valid JSON") from None
    if not isinstance(record, dict) or record.get("schema") != RECORD_SCHEMA:
        raise Refusal(EX_CONFIG, "host record has an unknown schema")
    return record


def _write_private_json(path: Path, payload: dict, *, exclusive: bool) -> None:
    data = (json.dumps(payload, sort_keys=True) + "\n").encode()
    target = path if exclusive else path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(target, flags, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    if not exclusive:
        os.replace(target, path)


def _file_identity(path: str) -> list[int]:
    info = os.stat(path)
    return [info.st_dev, info.st_ino]


def peer_identity(path: str) -> tuple[int, int]:
    """(pid, uid) of the listener behind one owned Unix socket path."""
    info = os.lstat(path)
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise Refusal(EX_CONFIG, f"host socket is not an owned socket: {path}")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2.0)
        client.connect(path)
        pid = client.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID)
        cred = client.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, 76)
    uid = int.from_bytes(cred[4:8], "little")
    return pid, uid


def host_state(record: dict, host: str) -> str:
    """``live``, ``starting`` or ``stale`` for one record; refuses replaced hosts."""
    pid = record.get("pid")
    if not isinstance(pid, int):
        return "stale"
    identity = process_identity(pid)
    if identity is None or identity.start != record.get("start"):
        return "stale"
    if identity.uid != os.getuid() or record.get("uid") != os.getuid():
        raise Refusal(EX_TEMPFAIL, f"recorded host {pid} belongs to another user")
    if record.get("executable") != host:
        raise Refusal(EX_TEMPFAIL, f"recorded host {pid} runs another host path")
    if identity.executable != host:
        if identity.executable == record.get("wrapper_executable"):
            return "starting"
        return "stale"
    try:
        current_file = _file_identity(host)
    except OSError:
        current_file = None
    if current_file != record.get("executable_file"):
        raise Refusal(
            EX_TEMPFAIL,
            f"terminal host {pid} predates the installed host; quit Vibecrafted Terminal to load it",
        )
    sock = record.get("socket")
    if not isinstance(sock, str):
        return "stale"
    try:
        peer_pid, peer_uid = peer_identity(sock)
    except (FileNotFoundError, ConnectionRefusedError):
        return "starting"
    except OSError as error:
        raise Refusal(EX_TEMPFAIL, f"host socket unreachable: {error}") from None
    if peer_pid != pid or peer_uid != os.getuid():
        raise Refusal(
            EX_TEMPFAIL, f"host socket peer {peer_pid} is not recorded host {pid}"
        )
    return "live"


def configured_shell(config: str) -> list[str]:
    """``terminal.shell`` (or legacy ``shell``) from the product config chain."""
    import tomllib

    def load(path: str) -> dict:
        with open(path, "rb") as handle:
            return tomllib.load(handle)

    try:
        main_config = load(config)
    except (OSError, ValueError):
        raise Refusal(EX_CONFIG, f"product config unreadable: {config}") from None
    layers = []
    for imported in (
        main_config.get("general", {}).get("import") or main_config.get("import") or []
    ):
        if isinstance(imported, str):
            try:
                layers.append(load(os.path.expanduser(imported)))
            except (OSError, ValueError):
                continue
    layers.append(main_config)
    shell = None
    for layer in layers:
        candidate = (layer.get("terminal") or {}).get("shell") or layer.get("shell")
        if candidate is not None:
            shell = candidate
    if isinstance(shell, str):
        return [shell]
    if isinstance(shell, dict) and isinstance(shell.get("program"), str):
        args = shell.get("args") or []
        if all(isinstance(arg, str) for arg in args):
            return [shell["program"], *args]
    raise Refusal(
        EX_CONFIG, "product config names no terminal shell for an additional window"
    )


def window_request(
    argv: list[str], *, config: str, cwd: str, shim: list[str]
) -> list[str]:
    """Exact `create-window` argv: caller window options, then the shim + command.

    The running host spawns IPC windows with its own process environment, so
    the caller's environment travels through the private env file the shim
    consumes. Command bytes stay literal argv; nothing is shell-evaluated.
    """
    head: list[str] = []
    has_cwd = False
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument in ("--", "--command") or argument.startswith(("-e", "--command=")):
            break
        if argument in _FLAG_OPTIONS:
            head.append(argument)
        elif argument in _VALUE_OPTIONS:
            if index + 1 >= len(argv):
                raise Refusal(EX_USAGE, f"{argument} requires a value")
            head.extend(argv[index : index + 2])
            has_cwd |= argument == "--working-directory"
            index += 1
        elif "=" in argument and argument.split("=", 1)[0] in {
            "--working-directory",
            "--title",
            "--class",
            "--option",
        }:
            head.append(argument)
            has_cwd |= argument.startswith("--working-directory=")
        elif argument[:2] in ("-T", "-t", "-o") and len(argument) > 2:
            head.append(argument)
        else:
            raise Refusal(
                EX_USAGE,
                f"{argument} cannot open an additional window of the running host",
            )
        index += 1
    if index < len(argv):
        boundary = argv[index]
        if boundary in ("--", "--command") or boundary == "-e":
            command = argv[index + 1 :]
        elif boundary.startswith("--command="):
            command = [boundary.split("=", 1)[1], *argv[index + 1 :]]
        else:
            command = [boundary[2:], *argv[index + 1 :]]
        if not command or not command[0]:
            raise Refusal(EX_USAGE, "-e requires a command")
    else:
        command = configured_shell(config)
    if not has_cwd:
        head = ["--working-directory", cwd, *head]
    return [*head, "-e", *shim, *command]


def write_env_file(directory: Path, environ: dict[str, str]) -> Path:
    for stale in directory.glob("window-*.env"):
        try:
            info = os.lstat(stale)
            if (
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.getuid()
                and time.time() - info.st_mtime > ENV_FILE_MAX_AGE_SECONDS
            ):
                os.unlink(stale)
        except OSError:
            continue
    path = directory / f"window-{os.getpid()}-{time.monotonic_ns()}.env"
    _write_private_json(path, {"environ": environ}, exclusive=True)
    return path


def spawn(env_file: str, command: list[str]) -> int:
    """Per-window exec shim: consume one private env file, then exec in place."""
    path = Path(env_file)
    try:
        _private_directory(path.parent, create=False)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            _owned_regular(fd, "window environment")
            data = b""
            while chunk := os.read(fd, 1 << 16):
                data += chunk
        finally:
            os.close(fd)
            os.unlink(path)
        environ = json.loads(data)["environ"]
        if not isinstance(environ, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in environ.items()
        ):
            raise ValueError("environment is not a string map")
    except (OSError, ValueError, KeyError, Refusal) as error:
        print(f"vc-terminal: window environment refused: {error}", file=sys.stderr)
        return EX_CONFIG
    if not command:
        print("vc-terminal: window command missing", file=sys.stderr)
        return EX_USAGE
    try:
        os.execvpe(command[0], command, environ)
    except OSError as error:
        print(f"vc-terminal: cannot execute {command[0]}: {error}", file=sys.stderr)
        return 127


def write_receipt(path: str, payload: dict) -> None:
    receipt = Path(path)
    _private_directory(receipt.parent, create=False)
    try:
        _write_private_json(
            receipt, {"schema": RECEIPT_SCHEMA, **payload}, exclusive=True
        )
    except OSError as error:
        raise Refusal(EX_CONFIG, f"launch receipt refused: {error}") from None


def _deliver(host: str, sock: str, window_argv: list[str]) -> None:
    command = [host, "msg", "--socket", sock, "create-window", *window_argv]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=DELIVERY_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise Refusal(
            EX_TEMPFAIL, "create-window delivery unconfirmed: native client timed out"
        ) from None
    except OSError as error:
        raise Refusal(
            EX_TEMPFAIL, f"create-window delivery unconfirmed: {error}"
        ) from None
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()[-400:]
        raise Refusal(
            EX_TEMPFAIL,
            f"create-window delivery unconfirmed (exit {result.returncode}): {detail}",
        )


def _clear_own_stale_socket(path: Path) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise Refusal(EX_CONFIG, f"host socket path is occupied: {path}")
    try:
        peer_identity(str(path))
    except ConnectionRefusedError:
        os.unlink(path)
        return
    raise Refusal(EX_TEMPFAIL, f"host socket path has a live listener: {path}")


def admit(
    wrapper_pid: int,
    host: str,
    argv: list[str],
    receipt: str | None,
    environ: dict[str, str],
    *,
    config: str,
    python: str,
) -> str:
    if wrapper_pid != os.getppid():
        raise Refusal(
            EX_USAGE,
            "admission must be requested by the wrapper that will exec the host",
        )
    if not os.path.isabs(host) or os.path.realpath(host) != host:
        raise Refusal(EX_CONFIG, f"host path is not canonical: {host}")
    directory = state_directory(environ, host)
    lock = acquire_lock(directory)
    try:
        record = read_record(directory)
        deadline = time.monotonic() + STARTUP_WAIT_SECONDS
        state = host_state(record, host) if record else "stale"
        while state == "starting":
            if time.monotonic() >= deadline:
                raise Refusal(
                    EX_TEMPFAIL,
                    f"terminal host {record['pid']} is still starting; window unconfirmed",
                )
            time.sleep(0.05)
            state = host_state(record, host)
        if state == "live":
            assert record is not None
            env_file = write_env_file(directory, environ)
            shim = [
                python,
                "-I",
                "-B",
                os.path.abspath(__file__),
                "spawn",
                str(env_file),
                "--",
            ]
            try:
                window_argv = window_request(
                    argv, config=config, cwd=os.getcwd(), shim=shim
                )
                _deliver(host, record["socket"], window_argv)
            except BaseException:
                env_file.unlink(missing_ok=True)
                raise
            # The write went to the recorded socket; the receipt may only name
            # the host when the same kernel identity still owns that socket.
            # A change here is unconfirmed and is never retried.
            if host_state(record, host) != "live":
                raise Refusal(
                    EX_TEMPFAIL,
                    f"create-window written but host {record['pid']} identity changed; window unconfirmed",
                )
            if receipt:
                write_receipt(
                    receipt,
                    {
                        "kind": "reused",
                        "host_pid": record["pid"],
                        "host_start": record["start"],
                        "executable": host,
                        "delivery": "create_window_request_written",
                        "agent_start_proven": False,
                    },
                )
            return "reused"
        wrapper = process_identity(wrapper_pid)
        if wrapper is None or wrapper.uid != os.getuid():
            raise Refusal(EX_TEMPFAIL, "wrapper identity unavailable")
        # The native host binds its default PID-named socket in TMPDIR; the
        # wrapper execs in place, so that PID is this wrapper's PID.
        sock = native_socket_path(environ, wrapper_pid)
        if len(os.fsencode(str(sock))) > SOCKADDR_LIMIT:
            raise Refusal(EX_CONFIG, f"host socket path exceeds sockaddr_un: {sock}")
        _clear_own_stale_socket(sock)
        fresh = {
            "schema": RECORD_SCHEMA,
            "pid": wrapper_pid,
            "start": wrapper.start,
            "uid": os.getuid(),
            "executable": host,
            "executable_file": _file_identity(host),
            "wrapper_executable": wrapper.executable,
            "socket": str(sock),
        }
        if receipt:
            write_receipt(
                receipt,
                {
                    "kind": "fresh",
                    "host_pid": wrapper_pid,
                    "host_start": wrapper.start,
                    "executable": host,
                    "delivery": "host_exec_pending",
                    "agent_start_proven": False,
                },
            )
        _write_private_json(directory / "host.json", fresh, exclusive=False)
        return "fresh"
    finally:
        os.close(lock)


def main(argv: list[str]) -> int:
    if argv[:1] == ["spawn"]:
        if len(argv) < 4 or argv[2] != "--":
            print("vc-terminal: usage: spawn ENV_FILE -- COMMAND...", file=sys.stderr)
            return EX_USAGE
        return spawn(argv[1], argv[3:])
    try:
        if len(argv) < 4 or argv[0] != "admit":
            raise Refusal(
                EX_USAGE,
                "usage: admit --wrapper-pid PID --host HOST --config FILE [--receipt PATH] -- ARGV...",
            )
        options: dict[str, str] = {}
        index = 1
        while index < len(argv) and argv[index] != "--":
            name = argv[index]
            if name not in (
                "--wrapper-pid",
                "--host",
                "--config",
                "--receipt",
            ) or index + 1 >= len(argv):
                raise Refusal(EX_USAGE, f"unknown admission option: {name}")
            options[name] = argv[index + 1]
            index += 2
        if (
            index >= len(argv)
            or not {"--wrapper-pid", "--host", "--config"} <= options.keys()
        ):
            raise Refusal(
                EX_USAGE, "admission requires --wrapper-pid, --host, --config and --"
            )
        try:
            wrapper_pid = int(options["--wrapper-pid"])
        except ValueError:
            raise Refusal(EX_USAGE, "--wrapper-pid must be an integer") from None
        decision = admit(
            wrapper_pid,
            options["--host"],
            argv[index + 1 :],
            options.get("--receipt") or None,
            dict(os.environ),
            config=options["--config"],
            python=sys.executable,
        )
    except Refusal as refusal:
        print(f"vc-terminal: {refusal}", file=sys.stderr)
        return refusal.code
    except OSError as error:
        print(f"vc-terminal: terminal host admission failed: {error}", file=sys.stderr)
        return EX_TEMPFAIL if error.errno != errno.EACCES else EX_CONFIG
    print(decision)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
