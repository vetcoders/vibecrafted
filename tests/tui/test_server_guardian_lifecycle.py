from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "scripts" / "vibecrafted"
PACKAGED_LAUNCHER = (
    REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "deck" / "vibecrafted"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until_dead(pid: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return
        time.sleep(0.05)
    raise AssertionError(f"process {pid} did not exit within {timeout} seconds")


def _wait_for_text(path: Path, expected: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and expected in path.read_text(encoding="utf-8"):
            return
        time.sleep(0.05)
    raise AssertionError(
        f"{expected!r} did not appear in {path} within {timeout} seconds"
    )


def _wait_for_path(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.05)
    raise AssertionError(f"{path} did not appear within {timeout} seconds")


def _wait_for_proven_managed_pid(
    state_dir: Path,
    role: str,
    *,
    previous: int | None = None,
    timeout: float = 12.0,
) -> int:
    deadline = time.monotonic() + timeout
    pid_file = state_dir / f"{role}.pid"
    identity_file = state_dir / f"{role}.identity.json"
    last_error = "evidence not written"
    while time.monotonic() < deadline:
        try:
            raw_pid = pid_file.read_text(encoding="utf-8").strip()
            identity = json.loads(identity_file.read_text(encoding="utf-8"))
            pid = int(raw_pid)
            if previous is not None and pid == previous:
                last_error = f"{role} still has previous PID {pid}"
            elif (
                identity.get("schema") != "vibecrafted.managed-process.v1"
                or identity.get("role") != role
                or identity.get("pid") != pid
                or not isinstance(identity.get("process"), dict)
            ):
                last_error = f"{role} PID and identity do not agree"
            elif not _process_alive(pid):
                last_error = f"{role} PID {pid} is not live"
            else:
                return pid
        except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.05)
    raise AssertionError(
        f"no proven live {role} PID appeared within {timeout} seconds: {last_error}"
    )


def _wait_for_proven_supervisor_pid(
    wrapper_receipt: Path,
    state_dir: Path,
    *,
    previous: int | None = None,
    timeout: float = 12.0,
) -> int:
    deadline = time.monotonic() + timeout
    lock_file = state_dir / "supervisor.lock"
    status_file = state_dir / "supervisor.status.json"
    last_error = "evidence not written"
    while time.monotonic() < deadline:
        try:
            wrapper = json.loads(wrapper_receipt.read_text(encoding="utf-8"))
            lock = json.loads(lock_file.read_text(encoding="utf-8"))
            status = json.loads(status_file.read_text(encoding="utf-8"))
            pid = int(wrapper["pid"])
            if previous is not None and pid == previous:
                last_error = f"supervisor still has previous PID {pid}"
            elif wrapper.get("schema") != "fake.launchd-supervisor.v1":
                last_error = "fake service receipt has wrong schema"
            elif lock.get("schema") != "vibecrafted.server-supervisor-lock.v1":
                last_error = "supervisor lock has wrong schema"
            elif lock.get("pid") != pid or status.get("supervisor_pid") != pid:
                last_error = "wrapper, lock, and status PID do not agree"
            elif not _process_alive(pid):
                last_error = f"supervisor PID {pid} is not live"
            else:
                return pid
        except (
            FileNotFoundError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            last_error = str(exc)
        time.sleep(0.05)
    raise AssertionError(
        f"no proven live supervisor PID appeared within {timeout} seconds: {last_error}"
    )


def _wait_for_coordination_role(
    state_dir: Path,
    role: str,
    *,
    timeout: float = 5.0,
) -> int:
    deadline = time.monotonic() + timeout
    lock_file = state_dir / "supervisor.lock"
    last_error = "lock payload not written"
    while time.monotonic() < deadline:
        try:
            payload = json.loads(lock_file.read_text(encoding="utf-8"))
            pid = int(payload["pid"])
            if payload.get("schema") != "vibecrafted.server-supervisor-lock.v1":
                last_error = "wrong lock schema"
            elif payload.get("role") != role:
                last_error = f"lock role is {payload.get('role')!r}"
            elif not _process_alive(pid):
                last_error = f"lease PID {pid} is not live"
            else:
                return pid
        except (
            FileNotFoundError,
            KeyError,
            ValueError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            last_error = str(exc)
        time.sleep(0.02)
    raise AssertionError(
        f"no live {role!r} coordination lease appeared within {timeout}s: {last_error}"
    )


def _wait_for_supervisor_success(
    state_dir: Path,
    *,
    previous: str | None = None,
    server_pid: int | None = None,
    guardian_pid: int | None = None,
    timeout: float = 12.0,
) -> str:
    deadline = time.monotonic() + timeout
    status_file = state_dir / "supervisor.status.json"
    last_error = "status receipt not written"
    while time.monotonic() < deadline:
        try:
            payload = json.loads(status_file.read_text(encoding="utf-8"))
            success = payload.get("last_success_at")
            if payload.get("state") != "healthy":
                last_error = f"supervisor state is {payload.get('state')!r}"
            elif not isinstance(success, str) or not success:
                last_error = "last_success_at is absent"
            elif previous is not None and success == previous:
                last_error = f"last_success_at has not advanced from {previous}"
            elif (
                server_pid is not None
                and payload.get("managed_pair", {}).get("server_pid") != server_pid
            ):
                last_error = f"success does not attest server PID {server_pid}"
            elif (
                guardian_pid is not None
                and payload.get("managed_pair", {}).get("guardian_pid") != guardian_pid
            ):
                last_error = f"success does not attest guardian PID {guardian_pid}"
            else:
                return success
        except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.05)
    raise AssertionError(
        f"supervisor did not record a new healthy pass within {timeout}s: {last_error}"
    )


def _run_launcher(
    env: dict[str, str], *args: str, check: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(LAUNCHER), *args],
        check=check,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _run_server_helper(
    env: dict[str, str],
    *args: str,
) -> subprocess.CompletedProcess[str]:
    helper_source = _server_helper_source()
    return subprocess.run(
        [sys.executable, "-", *args],
        input=helper_source,
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )


def _server_helper_source(*, definitions_only: bool = False) -> str:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    marker = "_server_python() {\n  python3 - \"$@\" <<'PY'\n"
    helper_source = launcher.split(marker, 1)[1].split("\nPY\n}", 1)[0]
    if definitions_only:
        helper_source = helper_source.split(
            "\noperation = sys.argv[1]",
            1,
        )[0]
    return helper_source


def _run_server_helper_definitions(
    source_suffix: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-", *args],
        input=_server_helper_source(definitions_only=True) + "\n" + source_suffix,
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=5,
    )


def _darwin_procargs_payload(
    argv: list[str],
    *,
    environment: list[str] | None = None,
    pointer_size: int = 8,
    corrupt_padding: bool = False,
) -> bytes:
    encoded = bytearray(b"/bin/probe\0")
    padding_size = (-len(encoded)) % pointer_size
    padding = bytearray(b"\0" * padding_size)
    if corrupt_padding:
        assert padding
        padding[0] = 1
    encoded.extend(padding)
    for argument in argv:
        encoded.extend(os.fsencode(argument))
        encoded.append(0)
    for item in environment or []:
        encoded.extend(os.fsencode(item))
        encoded.append(0)
    return struct.pack("=i", len(argv)) + bytes(encoded)


def _parse_synthetic_darwin_arguments(
    raw: bytes,
    *,
    include_environment: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run_server_helper_definitions(
        """
import base64
raw_payload = base64.b64decode(sys.argv[1])
darwin_procargs_raw = lambda _pid: raw_payload
executable, argv, nonce = darwin_arguments(
    4242,
    include_environment=sys.argv[2] == "1",
    pointer_size=8,
)
print(json.dumps({"executable": executable, "argv": argv, "nonce": nonce}))
""",
        base64.b64encode(raw).decode("ascii"),
        "1" if include_environment else "0",
    )


def test_server_healthcheck_uses_constant_time_endpoint() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    health_block = launcher.split('elif operation in {"health", "health-wait"}:', 1)[
        1
    ].split('elif operation == "port-free":', 1)[0]

    assert "/api/health" in health_block
    assert "/api/control/state" not in health_block
    assert "http.client.HTTPConnection" in health_block
    assert "urllib.request" not in health_block


def test_darwin_procargs_parser_preserves_unicode_and_omitted_environment() -> None:
    nonce = "a" * 64
    with_environment = _parse_synthetic_darwin_arguments(
        _darwin_procargs_payload(
            ["python-żółty", "--mode", "pełny"],
            environment=[f"VIBECRAFTED_PROCESS_NONCE={nonce}"],
        )
    )
    assert with_environment.returncode == 0, with_environment.stderr
    assert json.loads(with_environment.stdout) == {
        "executable": "/bin/probe",
        "argv": ["python-żółty", "--mode", "pełny"],
        "nonce": nonce,
    }

    omitted_environment = _parse_synthetic_darwin_arguments(
        _darwin_procargs_payload(["/bin/probe", "--serve"])
    )
    assert omitted_environment.returncode == 0, omitted_environment.stderr
    assert json.loads(omitted_environment.stdout)["nonce"] is None


def test_darwin_procargs_parser_stops_at_environment_terminator() -> None:
    hidden_nonce = "a" * 64
    payload = _darwin_procargs_payload(
        ["/bin/probe"],
        environment=[
            "VISIBLE=value",
            "",
            f"VIBECRAFTED_PROCESS_NONCE={hidden_nonce}",
        ],
    )

    result = _parse_synthetic_darwin_arguments(payload)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["nonce"] is None


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (
            _darwin_procargs_payload(
                ["/bin/probe"],
                corrupt_padding=True,
            ),
            "invalid Darwin process argument alignment",
        ),
        (
            _darwin_procargs_payload(["/bin/probe"])[:-1],
            "cannot parse Darwin argv",
        ),
        (
            _darwin_procargs_payload(["", "8"]),
            "Darwin argv is empty",
        ),
        (
            _darwin_procargs_payload(
                ["/bin/probe"],
                environment=[
                    f"VIBECRAFTED_PROCESS_NONCE={'a' * 64}",
                    f"VIBECRAFTED_PROCESS_NONCE={'b' * 64}",
                ],
            ),
            "ambiguous Darwin process nonce",
        ),
    ],
    ids=("bad-padding", "truncated", "empty-argv", "duplicate-nonce"),
)
def test_darwin_procargs_parser_rejects_ambiguous_payloads(
    payload: bytes,
    expected_error: str,
) -> None:
    result = _parse_synthetic_darwin_arguments(payload)
    assert result.returncode == 4
    assert expected_error in result.stderr


@pytest.mark.parametrize(
    ("mode", "expected_error"),
    [
        ("short", "cannot inspect Darwin process birth identity"),
        ("zombie", "invalid Darwin process birth identity"),
        ("in-exit", "invalid Darwin process birth identity"),
    ],
)
def test_darwin_bsd_info_rejects_short_or_dying_process_records(
    mode: str,
    expected_error: str,
) -> None:
    result = _run_server_helper_definitions(
        """
mode = sys.argv[1]
class FakeLibproc:
    def proc_pidinfo(self, pid, _flavor, _arg, buffer, _size):
        info = ctypes.cast(
            buffer,
            ctypes.POINTER(DarwinProcBSDInfo),
        ).contents
        info.pbi_pid = pid
        info.pbi_ppid = 1
        info.pbi_status = 5 if mode == "zombie" else 2
        info.pbi_flags = _DARWIN_PROC_FLAG_INEXIT if mode == "in-exit" else 0
        info.pbi_start_tvsec = 1
        info.pbi_start_tvusec = 0
        return _DARWIN_PROC_BSDINFO_SIZE - 1 if mode == "short" else _DARWIN_PROC_BSDINFO_SIZE
darwin_libraries = lambda: (FakeLibproc(), None)
print(darwin_bsd_info(4242))
""",
        mode,
    )
    assert result.returncode == 4
    assert expected_error in result.stderr


def test_darwin_process_record_rejects_argv_mutation_during_capture() -> None:
    result = _run_server_helper_definitions(
        """
darwin_bsd_info = lambda _pid: ("darwin:1:0", 1, 8)
darwin_pidpath = lambda _pid: "/bin/probe"
argument_records = iter(
    [
        ("/bin/probe", ["/bin/probe", "first"], "a" * 64),
        ("/bin/probe", ["/bin/probe", "second"], "a" * 64),
    ]
)
darwin_arguments = lambda _pid, **_kwargs: next(argument_records)
print(darwin_process_record(4242, include_nonce=True))
"""
    )
    assert result.returncode == 4
    assert "changed while identity was captured" in result.stderr


def test_darwin_process_record_rejects_path_mutation_during_capture() -> None:
    result = _run_server_helper_definitions(
        """
darwin_bsd_info = lambda _pid: ("darwin:1:0", 1, 8)
path_records = iter(["/bin/probe", "/bin/replaced"])
darwin_pidpath = lambda _pid: next(path_records)
darwin_arguments = lambda _pid, **_kwargs: (
    "/bin/probe",
    ["/bin/probe"],
    "a" * 64,
)
print(darwin_process_record(4242, include_nonce=True))
"""
    )
    assert result.returncode == 4
    assert "changed while identity was captured" in result.stderr


@pytest.fixture
def isolated_server_runtime(
    tmp_path: Path,
) -> tuple[dict[str, str], Path, Path]:
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    site_root = runtime_home / "server" / "site"
    lifecycle_log = tmp_path / "lifecycle.log"
    state_dir = home / ".vibecrafted" / "server"
    (site_root / "fonts").mkdir(parents=True)
    (site_root / "fonts" / "fixture.woff2").write_bytes(b"fixture")
    (home / ".vibecrafted" / "control_plane").mkdir(parents=True)

    _write_executable(
        bin_dir / "vc-server",
        f"#!{sys.executable}\n"
        """import socket
import json
import os
import signal
import time

host, raw_port = os.environ["VC_SERVER_ADDR"].rsplit(":", 1)
log_path = os.environ.get("VIBECRAFTED_TEST_LIFECYCLE_LOG") or os.environ[
    "LIFECYCLE_LOG"
]

def record(message):
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(message + "\\n")

def stop(_signum, _frame):
    if os.environ.get("SERVER_IGNORE_TERM") == "1":
        record("server-term-ignored")
        return
    record("server-stop")
    raise SystemExit(0)

def response(status, content_type, payload, extra_headers=()):
    reason = "OK" if status == 200 else "Not Found"
    headers = [
        f"HTTP/1.1 {status} {reason}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(payload)}",
        "Connection: close",
        *extra_headers,
        "",
        "",
    ]
    return "\\r\\n".join(headers).encode("ascii") + payload

def serve_connection(connection):
    connection.settimeout(0.25)
    request = b""
    try:
        while b"\\r\\n\\r\\n" not in request and len(request) < 65536:
            chunk = connection.recv(4096)
            if not chunk:
                return
            request += chunk
    except (OSError, TimeoutError):
        return
    parts = request.split(b" ", 2)
    path = parts[1].decode("ascii", "replace") if len(parts) >= 2 else ""
    if path in {"/api/health", "/api/control/state"}:
        payload = json.dumps({"status": "ok"}).encode()
        reply = response(200, "application/json", payload)
    elif path.startswith("/api/control/events"):
        if os.environ.get("SSE_STATUS") == "404":
            reply = response(404, "text/plain", b"")
        else:
            reply = response(
                200,
                "text/event-stream",
                b": ping\\n\\n",
                ("Cache-Control: no-cache",),
            )
    else:
        reply = response(404, "text/plain", b"")
    try:
        connection.sendall(reply)
    except OSError:
        pass

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
record("server-start")
start_delay = float(os.environ.get("SERVER_START_DELAY", "0"))
if start_delay > 0:
    record(f"server-delay {start_delay:g}")
    time.sleep(start_delay)
with socket.socket() as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, int(raw_port)))
    server.listen(128)
    while True:
        connection, _ = server.accept()
        with connection:
            serve_connection(connection)
""",
    )
    _write_executable(
        bin_dir / "vc-guardian",
        f"#!{sys.executable}\n"
        """import argparse
import json
import os
import signal
import sys
import time
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--server-url", required=True)
parser.add_argument("--ready-file", type=Path, required=True)
parser.add_argument("--ready-nonce", required=True)
args = parser.parse_args()
log_path = Path(
    os.environ.get("VIBECRAFTED_TEST_LIFECYCLE_LOG")
    or os.environ["LIFECYCLE_LOG"]
)

def record(message):
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message + "\\n")

def ready_payload():
    return {
        "schema": "vibecrafted.guardian-ready.v1",
        "nonce": args.ready_nonce,
        "pid": os.getpid(),
        "server_url": args.server_url,
    }

def remove_ready_if_owned():
    try:
        payload = json.loads(args.ready_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    if payload == ready_payload():
        args.ready_file.unlink(missing_ok=True)

def stop(_signum, _frame):
    record("guardian-stop")
    remove_ready_if_owned()
    raise SystemExit(0)

record("guardian-start " + " ".join(sys.argv[1:]))
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
request = urllib.request.Request(
    args.server_url + "/api/control/events?since=0",
    headers={"Accept": "text/event-stream"},
)
with urllib.request.urlopen(request, timeout=2.0) as response:
    if response.status != 200:
        raise SystemExit(2)
    content_type = response.headers.get("Content-Type", "").lower()
    if not content_type.startswith("text/event-stream"):
        raise SystemExit(3)
temporary = args.ready_file.with_name("." + args.ready_file.name + ".tmp")
temporary.write_text(json.dumps(ready_payload()) + "\\n", encoding="utf-8")
os.replace(temporary, args.ready_file)
while True:
    time.sleep(0.1)
""",
    )
    (bin_dir / "python3").symlink_to(Path(sys.executable).resolve())

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
            "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
            "VIBECRAFTED_RUNTIME_HOME": str(runtime_home),
            "LIFECYCLE_LOG": str(lifecycle_log),
            "VIBECRAFTED_TEST_LIFECYCLE_LOG": str(lifecycle_log),
        }
    )

    yield env, state_dir, lifecycle_log

    for pid_name in ("guardian.pid", "server.pid"):
        pid_file = state_dir / pid_name
        if not pid_file.is_file():
            continue
        raw_pid = pid_file.read_text(encoding="utf-8").strip()
        if not raw_pid.isdigit():
            continue
        pid = int(raw_pid)
        identity_file = state_dir / pid_name.replace(".pid", ".identity.json")
        try:
            identity = json.loads(identity_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        if identity.get("pid") == pid and _process_alive(pid):
            os.kill(pid, signal.SIGKILL)


def test_server_lifecycle_starts_heals_and_stops_guardian(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, lifecycle_log = isolated_server_runtime
    port = _free_port()

    started = _run_launcher(
        env, "server", "start", "--host", "127.0.0.1", "--port", str(port)
    )
    assert started.returncode == 0, started.stderr
    assert "Server is up and healthy" in started.stdout
    assert "Guardian is running" in started.stdout

    server_pid = int((state_dir / "server.pid").read_text(encoding="utf-8"))
    guardian_pid = int((state_dir / "guardian.pid").read_text(encoding="utf-8"))
    assert _process_alive(server_pid)
    assert _process_alive(guardian_pid)
    _wait_for_text(
        lifecycle_log,
        f"guardian-start --server-url http://127.0.0.1:{port}",
    )

    status = _run_launcher(env, "server", "status")
    assert status.returncode == 0, status.stderr
    assert f"Server: RUNNING (PID {server_pid}" in status.stdout
    assert f"Guardian: RUNNING (PID {guardian_pid}" in status.stdout

    compact_audit = state_dir / "compact-probe-audit.jsonl"
    compact_env = env | {"VIBECRAFTED_TEST_PROCESS_AUDIT": str(compact_audit)}
    compact_probe = _run_launcher(
        compact_env,
        "server",
        "supervisor-pair-health",
    )
    assert compact_probe.returncode == 0, compact_probe.stderr
    assert f"Server: RUNNING (PID {server_pid}" in compact_probe.stdout
    assert f"Guardian: RUNNING (PID {guardian_pid}" in compact_probe.stdout
    compact_probe_pids = {
        json.loads(line)["probe_owner_pid"]
        for line in compact_audit.read_text(encoding="utf-8").splitlines()
    }
    assert len(compact_probe_pids) == 1

    doctor = _run_launcher(env, "server", "doctor")
    assert doctor.returncode == 0, doctor.stderr
    assert "Guardian entrypoint present and executable" in doctor.stdout
    assert "Guardian PID" in doctor.stdout
    assert "Server and guardian doctor check passed" in doctor.stdout

    os.kill(guardian_pid, signal.SIGTERM)
    _wait_until_dead(guardian_pid)
    stale = _run_launcher(env, "server", "status")
    assert stale.returncode != 0
    assert "Guardian: STALE-PID" in stale.stdout
    stale_compact_probe = _run_launcher(
        env,
        "server",
        "supervisor-pair-health",
    )
    assert stale_compact_probe.returncode != 0

    healed = _run_launcher(env, "server", "start")
    assert healed.returncode == 0, healed.stderr
    healed_guardian_pid = int((state_dir / "guardian.pid").read_text(encoding="utf-8"))
    assert healed_guardian_pid != guardian_pid
    assert "Server is already running" in healed.stdout
    assert "Healing sidecar" in healed.stdout
    assert "Guardian is running" in healed.stdout

    stopped = _run_launcher(env, "server", "stop")
    assert stopped.returncode == 0, stopped.stderr
    assert stopped.stdout.index("Stopping guardian") < stopped.stdout.index(
        "Stopping server"
    )
    assert not (state_dir / "guardian.pid").exists()
    assert not (state_dir / "server.pid").exists()
    _wait_until_dead(healed_guardian_pid)
    _wait_until_dead(server_pid)
    lifecycle_lines = lifecycle_log.read_text(encoding="utf-8").splitlines()
    assert lifecycle_lines.index("guardian-stop", 2) < lifecycle_lines.index(
        "server-stop"
    )


def test_symlinked_guardian_entrypoint_gets_durable_identity(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, _ = isolated_server_runtime
    bin_dir = Path(env["HOME"]) / ".local" / "bin"
    guardian = bin_dir / "vc-guardian"
    guardian_target = bin_dir / "vc-guardian-real"
    guardian.rename(guardian_target)
    guardian.symlink_to(guardian_target)
    port = _free_port()

    started = _run_launcher(
        env, "server", "start", "--host", "127.0.0.1", "--port", str(port)
    )
    assert started.returncode == 0, started.stderr
    identity = json.loads(
        (state_dir / "guardian.identity.json").read_text(encoding="utf-8")
    )
    assert identity["declared_executable"] == str(guardian)

    doctor = _run_launcher(env, "server", "doctor")
    assert doctor.returncode == 0, doctor.stderr
    assert "durable identity and a valid SSE readiness receipt" in doctor.stdout

    stopped = _run_launcher(env, "server", "stop")
    assert stopped.returncode == 0, stopped.stderr


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS native process identity source",
)
def test_macos_identity_reverification_does_not_reread_launch_nonce(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, _ = isolated_server_runtime
    bin_dir = Path(env["HOME"]) / ".local" / "bin"
    unicode_python = bin_dir / "python-żółty"
    unicode_python.symlink_to(Path(sys.executable).resolve())
    for entrypoint_name in ("vc-server", "vc-guardian"):
        entrypoint = bin_dir / entrypoint_name
        _, separator, body = entrypoint.read_text(encoding="utf-8").partition("\n")
        assert separator
        entrypoint.write_text(
            f"#!{unicode_python}\n{body}",
            encoding="utf-8",
        )
    ps_marker = state_dir / "unexpected-ps-invocation"
    _write_executable(
        bin_dir / "ps",
        f"""#!/bin/sh
printf 'called\\n' >> {str(ps_marker)!r}
exit 97
""",
    )
    audit_path = state_dir / "process-audit.jsonl"
    env["VIBECRAFTED_TEST_PROCESS_AUDIT"] = str(audit_path)
    port = _free_port()

    started = _run_launcher(
        env, "server", "start", "--host", "127.0.0.1", "--port", str(port)
    )

    assert started.returncode == 0, started.stderr
    assert not ps_marker.exists()
    for role in ("server", "guardian"):
        identity = json.loads(
            (state_dir / f"{role}.identity.json").read_text(encoding="utf-8")
        )
        assert identity["process"]["start_token"].startswith("darwin:")
        assert identity["process"]["command"]["kind"] == "argv"
        assert identity["process"]["process_nonce"] == identity["nonce"]
        assert Path(identity["process"]["executable"]).name not in {"env", "nohup"}
        assert not (state_dir / f"{role}.launch-witness.json").exists()
    ps_calls = [
        json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    nonce_calls = [call for call in ps_calls if call["kind"] == "nonce"]
    assert all(call["operation"] == "capture-identity" for call in nonce_calls)
    nonce_pids = {int(call["pid"]) for call in nonce_calls}
    role_pids = {
        int((state_dir / f"{role}.pid").read_text(encoding="utf-8"))
        for role in ("server", "guardian")
    }
    assert nonce_pids == role_pids
    operation_counts = Counter(call["operation"] for call in ps_calls)
    assert set(operation_counts) == {
        "lock-owner-write",
        "launch-witness-write",
        "capture-identity",
        "verify-identity",
    }
    probe_groups: dict[tuple[str, int, int], list[str]] = {}
    for call in ps_calls:
        owner_pid = int(call["probe_owner_pid"])
        target_pid = int(call["pid"])
        assert owner_pid > 1
        probe_groups.setdefault(
            (call["operation"], owner_pid, target_pid),
            [],
        ).append(call["kind"])

    lock_groups = [
        kinds
        for (operation, _, _), kinds in probe_groups.items()
        if operation == "lock-owner-write"
    ]
    witness_groups = [
        kinds
        for (operation, _, _), kinds in probe_groups.items()
        if operation == "launch-witness-write"
    ]
    assert lock_groups == [["record"]]
    assert len(witness_groups) == 2
    assert all(kinds == ["launch"] for kinds in witness_groups)

    for role_pid in role_pids:
        capture_groups = [
            kinds
            for (operation, _, target_pid), kinds in probe_groups.items()
            if operation == "capture-identity" and target_pid == role_pid
        ]
        verify_groups = [
            kinds
            for (operation, _, target_pid), kinds in probe_groups.items()
            if operation == "verify-identity" and target_pid == role_pid
        ]
        assert 1 <= len(capture_groups) <= 21
        assert 1 <= len(verify_groups) <= 2
        for kinds in capture_groups:
            kind_counts = Counter(kinds)
            assert set(kind_counts) <= {"record", "nonce"}
            assert 1 <= kind_counts["record"] <= 3
            assert kind_counts["nonce"] <= 1
            assert len(kinds) <= 4
        assert all(kinds == ["record"] for kinds in verify_groups)
    assert len(ps_calls) <= 175, ps_calls


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS native KERN_PROCARGS2 parser",
)
def test_macos_native_procargs_rejects_empty_argv_without_environment_slide(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env["VIBECRAFTED_PROCESS_NONCE"] = "a" * 64
    owner_path = tmp_path / "owner.json"
    child = subprocess.Popen(
        ["", "8"],
        executable="/bin/sleep",
        env=env,
    )
    try:
        result = _run_server_helper(
            env,
            "lock-owner-write",
            str(owner_path),
            str(child.pid),
            "b" * 64,
        )
        assert result.returncode == 4
        assert "Darwin argv is empty" in result.stderr
        assert not owner_path.exists()
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_server_delayed_exec_mismatch_cleans_launch_owned_child(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, lifecycle_log = isolated_server_runtime
    server_bin = Path(env["HOME"]) / ".local" / "bin" / "vc-server"
    real_server = server_bin.with_name("vc-server-real")
    child_pid_path = state_dir / "delayed-exec-child.pid"
    identity_path = state_dir / "server.identity.json"
    server_bin.rename(real_server)
    _write_executable(
        server_bin,
        f"""#!{sys.executable}
import os
import time
from pathlib import Path

identity_path = Path({str(identity_path)!r})
pid_path = Path({str(child_pid_path)!r})
pid_path.write_text(str(os.getpid()) + "\\n", encoding="utf-8")
deadline = time.monotonic() + 8
while not identity_path.is_file():
    if time.monotonic() >= deadline:
        raise SystemExit(70)
    time.sleep(0.01)
os.execve({str(real_server)!r}, [{str(real_server)!r}], os.environ.copy())
""",
    )
    port = _free_port()

    result = _run_launcher(
        env, "server", "start", "--host", "127.0.0.1", "--port", str(port)
    )

    assert result.returncode != 0
    assert "managed identity could not be verified" in result.stderr
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    _wait_until_dead(child_pid)
    assert "server-stop" in lifecycle_log.read_text(encoding="utf-8")
    assert not (state_dir / "server.pid").exists()
    assert not (state_dir / "server.identity.json").exists()
    assert not (state_dir / "server.launch-witness.json").exists()
    status = json.loads((state_dir / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["pid"] is None


def test_launch_witness_nonce_mismatch_refuses_foreign_process_signal(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, lifecycle_log = isolated_server_runtime
    server_bin = Path(env["HOME"]) / ".local" / "bin" / "vc-server"
    real_server = server_bin.with_name("vc-server-real")
    child_pid_path = state_dir / "foreign-child.pid"
    identity_path = state_dir / "server.identity.json"
    witness_path = state_dir / "server.launch-witness.json"
    server_bin.rename(real_server)
    _write_executable(
        server_bin,
        f"""#!{sys.executable}
import json
import os
import time
from pathlib import Path

identity_path = Path({str(identity_path)!r})
witness_path = Path({str(witness_path)!r})
pid_path = Path({str(child_pid_path)!r})
pid_path.write_text(str(os.getpid()) + "\\n", encoding="utf-8")
deadline = time.monotonic() + 8
while not identity_path.is_file():
    if time.monotonic() >= deadline:
        raise SystemExit(70)
    time.sleep(0.01)
payload = json.loads(witness_path.read_text(encoding="utf-8"))
payload["nonce"] = "0" * 64
temporary = witness_path.with_name("." + witness_path.name + ".tampered")
temporary.write_text(json.dumps(payload) + "\\n", encoding="utf-8")
os.replace(temporary, witness_path)
os.execve({str(real_server)!r}, [{str(real_server)!r}], os.environ.copy())
""",
    )
    port = _free_port()
    child_pid: int | None = None

    try:
        result = _run_launcher(
            env, "server", "start", "--host", "127.0.0.1", "--port", str(port)
        )

        assert result.returncode != 0
        assert "launch ownership cannot be reverified" in result.stderr
        assert "refusing to signal" in result.stderr
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        assert _process_alive(child_pid)
        assert "server-start" in lifecycle_log.read_text(encoding="utf-8")
        assert "server-stop" not in lifecycle_log.read_text(encoding="utf-8")
        assert (state_dir / "server.pid").is_file()
        assert (state_dir / "server.identity.json").is_file()
        assert witness_path.is_file()
        status = json.loads((state_dir / "status.json").read_text(encoding="utf-8"))
        assert status["status"] == "failed"
        assert status["pid"] == child_pid
    finally:
        if child_pid is not None and _process_alive(child_pid):
            os.kill(child_pid, signal.SIGTERM)
            _wait_until_dead(child_pid)


def test_server_start_rolls_back_when_guardian_entrypoint_is_missing(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, lifecycle_log = isolated_server_runtime
    (Path(env["HOME"]) / ".local" / "bin" / "vc-guardian").unlink()
    assert shutil.which("vc-guardian", path=env["PATH"]) is None
    port = _free_port()

    result = _run_launcher(env, "server", "start", "--port", str(port))

    assert result.returncode != 0
    assert "vc-guardian entrypoint not found" in result.stderr
    assert "rolling back the newly started server" in result.stderr
    assert not (state_dir / "server.pid").exists()
    status = json.loads((state_dir / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["pid"] is None
    assert "server-stop" in lifecycle_log.read_text(encoding="utf-8")


def test_server_start_retargets_live_guardian_after_server_endpoint_changes(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, lifecycle_log = isolated_server_runtime
    first_port = _free_port()
    first = _run_launcher(env, "server", "start", "--port", str(first_port))
    assert first.returncode == 0, first.stderr
    first_server_pid = int((state_dir / "server.pid").read_text(encoding="utf-8"))
    first_guardian_pid = int((state_dir / "guardian.pid").read_text(encoding="utf-8"))
    _wait_for_text(
        lifecycle_log,
        f"guardian-start --server-url http://127.0.0.1:{first_port}",
    )

    os.kill(first_server_pid, signal.SIGTERM)
    _wait_until_dead(first_server_pid)
    second_port = _free_port()
    second = _run_launcher(env, "server", "start", "--port", str(second_port))

    assert second.returncode == 0, second.stderr
    assert "Guardian endpoint changed" in second.stdout
    second_guardian_pid = int((state_dir / "guardian.pid").read_text(encoding="utf-8"))
    assert second_guardian_pid != first_guardian_pid
    assert (state_dir / "guardian.url").read_text(
        encoding="utf-8"
    ).strip() == f"http://127.0.0.1:{second_port}"
    _wait_until_dead(first_guardian_pid)
    _wait_for_text(
        lifecycle_log,
        f"guardian-start --server-url http://127.0.0.1:{second_port}",
    )

    stopped = _run_launcher(env, "server", "stop")
    assert stopped.returncode == 0, stopped.stderr


def test_server_status_and_doctor_report_stale_guardian_pid(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, _ = isolated_server_runtime
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "guardian.pid").write_text("99999999\n", encoding="utf-8")

    status = _run_launcher(env, "server", "status")
    doctor = _run_launcher(env, "server", "doctor")

    assert status.returncode != 0
    assert "Server: STOPPED" in status.stdout
    assert "Guardian: STALE-PID (stale: 99999999)" in status.stdout
    assert doctor.returncode != 0
    assert "Stale guardian PID file (stale: 99999999)" in doctor.stdout


def test_server_stop_refuses_to_signal_pid_mismatch(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, _ = isolated_server_runtime
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "guardian.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    result = _run_launcher(env, "server", "stop")

    assert result.returncode != 0
    assert "unverified live process" in result.stderr
    assert _process_alive(os.getpid())


def test_server_stop_does_not_signal_decoy_with_guardian_marker(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, _ = isolated_server_runtime
    state_dir.mkdir(parents=True, exist_ok=True)
    decoy = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            "vc-guardian-decoy",
        ],
        env=env,
    )
    try:
        (state_dir / "guardian.pid").write_text(
            f"{decoy.pid}\n",
            encoding="utf-8",
        )

        result = _run_launcher(env, "server", "stop")

        assert result.returncode != 0
        assert "unverified live process" in result.stderr
        assert _process_alive(decoy.pid)
        assert (state_dir / "guardian.pid").is_file()
    finally:
        if _process_alive(decoy.pid):
            decoy.kill()
        decoy.wait(timeout=5)


def test_concurrent_server_starts_create_exactly_one_managed_pair(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, lifecycle_log = isolated_server_runtime
    port = _free_port()
    argv = [
        str(LAUNCHER),
        "server",
        "start",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    first = subprocess.Popen(
        argv,
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        argv,
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    first_stdout, first_stderr = first.communicate(timeout=20)
    second_stdout, second_stderr = second.communicate(timeout=20)

    assert first.returncode == 0, first_stderr
    assert second.returncode == 0, second_stderr
    assert "Server is up and healthy" in first_stdout + second_stdout
    assert "Server is already running" in first_stdout + second_stdout
    lines = lifecycle_log.read_text(encoding="utf-8").splitlines()
    assert lines.count("server-start") == 1
    assert sum(line.startswith("guardian-start ") for line in lines) == 1
    assert (state_dir / "server.identity.json").is_file()
    assert (state_dir / "guardian.identity.json").is_file()
    assert (state_dir / "guardian.ready-path").is_file()

    stopped = _run_launcher(env, "server", "stop")
    assert stopped.returncode == 0, stopped.stderr


def test_manual_stop_serializes_against_concurrent_supervisor_acquire(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, _ = isolated_server_runtime
    port = _free_port()
    started = _run_launcher(
        env,
        "server",
        "start",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    )
    assert started.returncode == 0, started.stderr
    server_pid = _wait_for_proven_managed_pid(state_dir, "server")
    guardian_pid = _wait_for_proven_managed_pid(state_dir, "guardian")

    delayed_stop_env = env.copy()
    delayed_stop_env["VIBECRAFTED_TEST_SERVER_STOP_DELAY"] = "0.6"
    stopper = subprocess.Popen(
        [str(LAUNCHER), "server", "stop"],
        cwd=REPO_ROOT,
        env=delayed_stop_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_for_coordination_role(state_dir, "manual-stop")

    supervisor_env = env.copy()
    supervisor_env["PYTHONPATH"] = str(REPO_ROOT / "vibecrafted-core")
    contender = subprocess.run(
        [
            sys.executable,
            "-m",
            "vibecrafted_core.server_supervisor",
            "run",
            "--launcher",
            str(LAUNCHER.resolve()),
            "--home",
            env["VIBECRAFTED_HOME"],
            "--runtime-home",
            env["VIBECRAFTED_RUNTIME_HOME"],
            "--operator-home",
            env["HOME"],
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--interval",
            "0.1",
            "--maximum-backoff",
            "0.2",
            "--command-timeout",
            "5",
        ],
        cwd=REPO_ROOT,
        env=supervisor_env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    stopped_stdout, stopped_stderr = stopper.communicate(timeout=20)

    assert contender.returncode == 75
    assert "coordination lease is already active" in contender.stderr
    assert stopper.returncode == 0, stopped_stderr
    assert "Guardian stopped" in stopped_stdout
    assert "Server stopped" in stopped_stdout
    _wait_until_dead(server_pid)
    _wait_until_dead(guardian_pid)
    assert not (state_dir / "server.pid").exists()
    assert not (state_dir / "guardian.pid").exists()


def test_stale_lifecycle_lock_is_quarantined_once_under_parallel_recovery(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, lifecycle_log = isolated_server_runtime
    port = _free_port()
    delayed_env = env.copy()
    delayed_env["SERVER_START_DELAY"] = "30"
    argv = [
        str(LAUNCHER),
        "server",
        "start",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    crashed_launcher = subprocess.Popen(
        argv,
        cwd=REPO_ROOT,
        env=delayed_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    original_server_pid: int | None = None
    try:
        owner_path = state_dir / "lifecycle.lock" / "owner.json"
        identity_path = state_dir / "server.identity.json"
        _wait_for_path(owner_path)
        _wait_for_path(identity_path)
        original_owner = owner_path.read_bytes()
        owner_payload = json.loads(original_owner)
        owner_pid = int(owner_payload["pid"])
        original_server_pid = int(
            (state_dir / "server.pid").read_text(encoding="utf-8")
        )
        assert owner_payload["schema"] == "vibecrafted.server-lifecycle-lock.v1"
        assert len(owner_payload["nonce"]) == 64
        assert owner_payload["process"]
        assert _process_alive(owner_pid)
        assert _process_alive(original_server_pid)

        os.kill(owner_pid, signal.SIGKILL)
        crashed_launcher.wait(timeout=5)
        _wait_until_dead(owner_pid)
        assert _process_alive(original_server_pid)

        contenders = [
            subprocess.Popen(
                argv,
                cwd=REPO_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results = [
            (*contender.communicate(timeout=20), contender.returncode)
            for contender in contenders
        ]

        for stdout, stderr, returncode in results:
            assert returncode == 0, stderr
        combined = "".join(stdout for stdout, _, _ in results)
        assert combined.count("Recovered stale server lifecycle lock") == 1
        assert combined.count("Server is up and healthy") == 1
        assert combined.count("Server is already running") == 1

        quarantines = list(state_dir.glob("lifecycle.lock.stale.*"))
        assert len(quarantines) == 1
        assert (quarantines[0] / "owner.json").read_bytes() == original_owner
        assert (state_dir / "lifecycle.recovery.lock").is_file()
        assert not (state_dir / "lifecycle.lock").exists()

        final_server_pid = int((state_dir / "server.pid").read_text(encoding="utf-8"))
        final_guardian_pid = int(
            (state_dir / "guardian.pid").read_text(encoding="utf-8")
        )
        assert final_server_pid != original_server_pid
        assert _process_alive(final_server_pid)
        assert _process_alive(final_guardian_pid)
        _wait_until_dead(original_server_pid)
        lifecycle_lines = lifecycle_log.read_text(encoding="utf-8").splitlines()
        assert sum(line.startswith("guardian-start ") for line in lifecycle_lines) == 1

        stopped = _run_launcher(env, "server", "stop")
        assert stopped.returncode == 0, stopped.stderr
        _wait_until_dead(final_guardian_pid)
        _wait_until_dead(final_server_pid)
    finally:
        if crashed_launcher.poll() is None:
            crashed_launcher.kill()
            crashed_launcher.wait(timeout=5)
        if original_server_pid is not None and _process_alive(original_server_pid):
            os.kill(original_server_pid, signal.SIGKILL)
            _wait_until_dead(original_server_pid)


def test_lifecycle_lock_recovery_refuses_invalid_and_unverified_owners(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, _ = isolated_server_runtime
    lock_dir = state_dir / "lifecycle.lock"
    owner_path = lock_dir / "owner.json"
    lock_dir.mkdir(parents=True)
    invalid_owner = b'{"schema":"wrong"}\n'
    owner_path.write_bytes(invalid_owner)

    invalid = _run_launcher(
        env,
        "server",
        "start",
        "--port",
        str(_free_port()),
    )

    assert invalid.returncode == 75
    assert "invalid owner evidence" in invalid.stderr
    assert owner_path.read_bytes() == invalid_owner
    assert not (state_dir / "lifecycle.recovery.lock").exists()
    assert not (state_dir / "server.pid").exists()

    shutil.rmtree(lock_dir)
    lock_dir.mkdir()
    unverified_owner = {
        "schema": "vibecrafted.server-lifecycle-lock.v1",
        "pid": os.getpid(),
        "nonce": "a" * 64,
        "acquired_at": "2026-07-26T00:00:00+00:00",
        "process": {
            "start_token": "proc:1",
            "command": {"kind": "argv", "value": ["decoy"]},
            "executable": sys.executable,
            "executable_sha256": None,
            "process_nonce": None,
        },
    }
    owner_path.write_text(
        json.dumps(unverified_owner, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    unverified = _run_launcher(
        env,
        "server",
        "start",
        "--port",
        str(_free_port()),
    )

    assert unverified.returncode == 75
    assert "unverified" in unverified.stderr
    assert json.loads(owner_path.read_text(encoding="utf-8")) == unverified_owner
    assert _process_alive(os.getpid())
    assert not (state_dir / "lifecycle.recovery.lock").exists()
    assert not (state_dir / "server.pid").exists()


def test_short_lived_guardian_fails_readiness_and_rolls_back_server(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, lifecycle_log = isolated_server_runtime
    guardian = Path(env["HOME"]) / ".local" / "bin" / "vc-guardian"
    _write_executable(
        guardian,
        """#!/usr/bin/env python3
raise SystemExit(0)
""",
    )

    result = _run_launcher(
        env,
        "server",
        "start",
        "--port",
        str(_free_port()),
    )

    assert result.returncode != 0
    assert "Guardian failed SSE readiness" in result.stderr
    assert "Guardian startup failed" in result.stderr
    assert not (state_dir / "server.pid").exists()
    status = json.loads((state_dir / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["pid"] is None
    assert "server-stop" in lifecycle_log.read_text(encoding="utf-8")


def test_guardian_404_fails_readiness_and_rolls_back_server(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, lifecycle_log = isolated_server_runtime
    env["SSE_STATUS"] = "404"

    result = _run_launcher(
        env,
        "server",
        "start",
        "--port",
        str(_free_port()),
    )

    assert result.returncode != 0
    assert "Guardian failed SSE readiness" in result.stderr
    assert "Guardian startup failed" in result.stderr
    assert not (state_dir / "server.pid").exists()
    assert not (state_dir / "guardian.pid").exists()
    assert "server-stop" in lifecycle_log.read_text(encoding="utf-8")


def test_term_ignoring_server_remains_tracked_until_confirmed_sigkill(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, state_dir, lifecycle_log = isolated_server_runtime
    env["SERVER_IGNORE_TERM"] = "1"
    env["VIBECRAFTED_STOP_TERM_WAIT_TICKS"] = "10"
    started = _run_launcher(
        env,
        "server",
        "start",
        "--port",
        str(_free_port()),
    )
    assert started.returncode == 0, started.stderr
    server_pid = int((state_dir / "server.pid").read_text(encoding="utf-8"))

    stopper = subprocess.Popen(
        [str(LAUNCHER), "server", "stop"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_for_text(lifecycle_log, "server-term-ignored")
    assert (state_dir / "server.pid").is_file()
    assert (state_dir / "server.identity.json").is_file()
    stdout, stderr = stopper.communicate(timeout=15)

    assert stopper.returncode == 0, stderr
    assert "sending SIGKILL" in stdout
    assert not (state_dir / "server.pid").exists()
    assert not (state_dir / "server.identity.json").exists()
    _wait_until_dead(server_pid)


def test_server_runtime_uses_custom_vibecrafted_home(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
) -> None:
    env, default_state_dir, _ = isolated_server_runtime
    custom_home = Path(env["HOME"]).parent / "custom-vibecrafted-home"
    env["VIBECRAFTED_HOME"] = str(custom_home)
    custom_state_dir = custom_home / "server"

    started = _run_launcher(
        env,
        "server",
        "start",
        "--port",
        str(_free_port()),
    )

    assert started.returncode == 0, started.stderr
    assert (custom_state_dir / "server.pid").is_file()
    assert (custom_state_dir / "guardian.pid").is_file()
    assert not default_state_dir.exists()
    status = _run_launcher(env, "server", "status")
    doctor = _run_launcher(env, "server", "doctor")
    assert status.returncode == 0, status.stderr
    assert doctor.returncode == 0, doctor.stderr
    stopped = _run_launcher(env, "server", "stop")
    assert stopped.returncode == 0, stopped.stderr


def test_supervisor_and_fake_service_manager_repair_every_proven_process(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
    tmp_path: Path,
) -> None:
    env, state_dir, _ = isolated_server_runtime
    port = _free_port()
    wrapper_receipt = tmp_path / "fake-launchd.json"
    wrapper_script = tmp_path / "fake-launchd.py"
    wrapper_script.write_text(
        """from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

receipt = Path(sys.argv[1])
command = sys.argv[2:]
stopping = False
generation = 0

def request_stop(_signum, _frame):
    global stopping
    stopping = True

def write_receipt(pid, state):
    payload = {
        "schema": "fake.launchd-supervisor.v1",
        "pid": pid,
        "state": state,
        "generation": generation,
        "command": command,
    }
    temporary = receipt.with_name("." + receipt.name + ".tmp")
    temporary.write_text(json.dumps(payload) + "\\n", encoding="utf-8")
    os.replace(temporary, receipt)

signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)

while not stopping:
    generation += 1
    child = subprocess.Popen(command)
    write_receipt(child.pid, "running")
    while child.poll() is None and not stopping:
        time.sleep(0.05)
    if stopping and child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=20)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)
    if not stopping:
        time.sleep(0.1)

write_receipt(None, "stopped")
""",
        encoding="utf-8",
    )
    supervisor_env = env.copy()
    supervisor_env["PYTHONPATH"] = str(REPO_ROOT / "vibecrafted-core")
    supervisor_command = [
        sys.executable,
        "-m",
        "vibecrafted_core.server_supervisor",
        "run",
        "--launcher",
        str(LAUNCHER.resolve()),
        "--home",
        env["VIBECRAFTED_HOME"],
        "--runtime-home",
        env["VIBECRAFTED_RUNTIME_HOME"],
        "--operator-home",
        env["HOME"],
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--interval",
        "0.5",
        "--maximum-backoff",
        "1.0",
        "--command-timeout",
        "20",
    ]
    wrapper = subprocess.Popen(
        [
            sys.executable,
            str(wrapper_script),
            str(wrapper_receipt),
            *supervisor_command,
        ],
        cwd=REPO_ROOT,
        env=supervisor_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        first_supervisor = _wait_for_proven_supervisor_pid(
            wrapper_receipt,
            state_dir,
        )
        first_server = _wait_for_proven_managed_pid(state_dir, "server")
        first_guardian = _wait_for_proven_managed_pid(state_dir, "guardian")
        first_success = _wait_for_supervisor_success(
            state_dir,
            server_pid=first_server,
            guardian_pid=first_guardian,
        )

        status = _run_launcher(env, "server", "status")
        assert status.returncode == 0, status.stderr
        assert "Supervision: FOREGROUND" in status.stdout
        assert f"Server: RUNNING (PID {first_server}" in status.stdout
        assert f"Guardian: RUNNING (PID {first_guardian}" in status.stdout
        packaged_pair_probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; from pathlib import Path; "
                    "from vibecrafted_core.server_supervisor import "
                    "SupervisorPaths, _child_environment, _pair_healthy; "
                    "paths=SupervisorPaths.create(home=Path(sys.argv[2]), "
                    "runtime_home=Path(sys.argv[3]), "
                    "operator_home=Path(sys.argv[4])); "
                    "raise SystemExit(0 if _pair_healthy("
                    "Path(sys.argv[1]), _child_environment(paths)) else 1)"
                ),
                str(PACKAGED_LAUNCHER.resolve()),
                env["VIBECRAFTED_HOME"],
                env["VIBECRAFTED_RUNTIME_HOME"],
                env["HOME"],
            ],
            cwd=REPO_ROOT,
            env=supervisor_env,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert packaged_pair_probe.returncode == 0, packaged_pair_probe.stderr

        refused = _run_launcher(env, "server", "stop")
        assert refused.returncode == 75
        assert "coordination lease is already active" in refused.stderr
        assert _process_alive(first_server)
        assert _process_alive(first_guardian)

        os.kill(first_server, signal.SIGKILL)
        _wait_until_dead(first_server)
        second_server = _wait_for_proven_managed_pid(
            state_dir,
            "server",
            previous=first_server,
        )
        second_guardian = _wait_for_proven_managed_pid(
            state_dir,
            "guardian",
            previous=first_guardian,
        )
        second_success = _wait_for_supervisor_success(
            state_dir,
            previous=first_success,
            server_pid=second_server,
            guardian_pid=second_guardian,
        )
        assert second_server != first_server
        assert second_guardian != first_guardian

        os.kill(second_guardian, signal.SIGKILL)
        _wait_until_dead(second_guardian)
        third_guardian = _wait_for_proven_managed_pid(
            state_dir,
            "guardian",
            previous=second_guardian,
        )
        _wait_for_supervisor_success(
            state_dir,
            previous=second_success,
            server_pid=second_server,
            guardian_pid=third_guardian,
        )
        assert _wait_for_proven_managed_pid(state_dir, "server") == second_server

        os.kill(first_supervisor, signal.SIGKILL)
        _wait_until_dead(first_supervisor)
        second_supervisor = _wait_for_proven_supervisor_pid(
            wrapper_receipt,
            state_dir,
            previous=first_supervisor,
        )
        assert second_supervisor != first_supervisor
        assert _wait_for_proven_managed_pid(state_dir, "server") == second_server
        assert _wait_for_proven_managed_pid(state_dir, "guardian") == third_guardian

        healed = _run_launcher(env, "server", "status")
        assert healed.returncode == 0, healed.stderr
        assert "Server: RUNNING" in healed.stdout
        assert "Guardian: RUNNING" in healed.stdout

        wrapper.terminate()
        wrapper.wait(timeout=30)
        _wait_until_dead(second_server, timeout=10)
        _wait_until_dead(third_guardian, timeout=10)
        assert not (state_dir / "server.pid").exists()
        assert not (state_dir / "guardian.pid").exists()
        assert not _process_alive(second_supervisor)
        stopped_receipt = json.loads(
            (state_dir / "supervisor.status.json").read_text(encoding="utf-8")
        )
        assert stopped_receipt["state"] == "stopped"
        stopped = _run_launcher(env, "server", "status")
        assert stopped.returncode == 0, stopped.stderr
        assert "Supervision: UNSUPERVISED" in stopped.stdout
        assert "Server: STOPPED" in stopped.stdout
        assert "Guardian: STOPPED" in stopped.stdout
    finally:
        if wrapper.poll() is None:
            wrapper.terminate()
            try:
                wrapper.wait(timeout=30)
            except subprocess.TimeoutExpired:
                wrapper.kill()
                wrapper.wait(timeout=5)
        cleanup_env = env.copy()
        cleanup_env["VIBECRAFTED_SERVER_SUPERVISOR_CHILD"] = "1"
        _run_launcher(cleanup_env, "server", "stop")


def test_malicious_cli_host_and_status_json_are_never_executed(
    isolated_server_runtime: tuple[dict[str, str], Path, Path],
    tmp_path: Path,
) -> None:
    env, state_dir, _ = isolated_server_runtime
    cli_pwn = tmp_path / "cli-pwn"
    status_pwn = tmp_path / "status-pwn"
    malicious_cli_host = (
        "127.0.0.1');__import__('pathlib').Path("
        f"{str(cli_pwn)!r}).write_text('owned');#"
    )

    cli_result = _run_launcher(
        env,
        "server",
        "start",
        "--host",
        malicious_cli_host,
        "--port",
        str(_free_port()),
    )

    assert cli_result.returncode != 0
    assert "Invalid server endpoint" in cli_result.stderr
    assert not cli_pwn.exists()

    state_dir.mkdir(parents=True, exist_ok=True)
    malicious_status_host = (
        "127.0.0.1');__import__('pathlib').Path("
        f"{str(status_pwn)!r}).write_text('owned');#"
    )
    (state_dir / "status.json").write_text(
        json.dumps({"host": malicious_status_host, "port": 3024}),
        encoding="utf-8",
    )
    status = _run_launcher(env, "server", "status")
    opened = _run_launcher(env, "server", "open")
    doctor = _run_launcher(env, "server", "doctor")

    assert status.returncode != 0
    assert "INVALID-ENDPOINT" in status.stdout
    assert opened.returncode != 0
    assert "invalid endpoint" in opened.stderr
    assert doctor.returncode != 0
    assert "status.json contains an invalid endpoint" in doctor.stdout
    assert not status_pwn.exists()


def test_packaged_launcher_matches_repo_launcher() -> None:
    assert PACKAGED_LAUNCHER.read_bytes() == LAUNCHER.read_bytes()
