"""Live phase presentation with loopback downloads and private fake payloads."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import http.server
import os
import pty
import re
import select
import signal
import subprocess
import threading
import time

import pytest

from tests.tui.test_install_output import BOOTSTRAP, _fixture, _run

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@contextlib.contextmanager
def _download_server(tmp_path, archive, *, unknown_length=False, failure=False):
    public = tmp_path / "download"
    public.mkdir()
    payload = archive.read_bytes()
    (public / archive.name).write_bytes(payload)
    key = tmp_path / "fixture-private.pem"
    for args in (
        [
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(key),
        ],
        [
            "pkey",
            "-in",
            str(key),
            "-pubout",
            "-out",
            str(public / "vibecrafted-signing.pub"),
        ],
        [
            "dgst",
            "-sha256",
            "-sign",
            str(key),
            "-out",
            str(public / (archive.name + ".sig")),
            str(archive),
        ],
    ):
        subprocess.run(["openssl", *args], check=True, capture_output=True)
    (public / "SHA256SUMS").write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {archive.name}\n"
    )
    started, finished = threading.Event(), threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            name = self.path.removeprefix("/")
            path = public / name
            if "/" in name or not path.is_file():
                self.send_error(404)
                return
            downloading = name == archive.name
            if downloading:
                started.set()
            try:
                if downloading and failure:
                    self.send_error(503, "controlled transfer refusal")
                    return
                body = path.read_bytes()
                self.send_response(200)
                if not (downloading and unknown_length):
                    self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                step = max(1, (len(body) + 29) // 30)
                for offset in range(0, len(body), step):
                    self.wfile.write(body[offset : offset + step])
                    self.wfile.flush()
                    if downloading:
                        time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if downloading:
                    finished.set()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/{archive.name}", started, finished
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _network_command(fixture, url):
    return [
        "/bin/bash",
        str(BOOTSTRAP),
        "--yes",
        "--archive-url",
        url,
        "--runtime-pack-file",
        str(fixture["pack"]),
        "install",
    ]


def _tty_script(command, env, *, source=None, transfer=None):
    """Use stock Bash and a real pipe for script stdin, with only output on PTY."""
    master, slave = pty.openpty()
    process = subprocess.Popen(
        ["/bin/bash", "-s", "--", *command[2:]],
        stdin=subprocess.PIPE,
        stdout=slave,
        stderr=slave,
        env=env,
        start_new_session=True,
    )
    os.close(slave)

    def feed():
        try:
            process.stdin.write(
                source if source is not None else BOOTSTRAP.read_bytes()
            )
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    output = bytearray()
    during_download = set()
    deadline = time.monotonic() + 30
    try:
        while True:
            assert time.monotonic() < deadline, (
                "private installer/presenter did not finish"
            )
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 8192)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    chunk = b""
                if chunk:
                    output.extend(chunk)
                    if transfer and transfer[0].is_set() and not transfer[1].is_set():
                        during_download.update(
                            re.findall(
                                rf"\r  ([{FRAMES}]) \[1/3\]",
                                chunk.decode(errors="ignore"),
                            )
                        )
                elif process.poll() is not None:
                    break
            elif process.poll() is not None:
                break
        code = process.wait(timeout=2)
    finally:
        if process.poll() is None:
            # This fresh session contains only this fabricated installer fixture.
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        os.close(master)
        writer.join(timeout=2)
    return code, output.decode(errors="replace"), during_download


@pytest.mark.parametrize("unknown_length", [False, True])
def test_piped_script_shows_live_tty_phase_then_checkmarks(tmp_path, unknown_length):
    fixture, env, _ = _fixture(tmp_path, body="sleep 1.1\n")
    with _download_server(
        tmp_path, fixture["archive"], unknown_length=unknown_length
    ) as (url, started, finished):
        code, text, frames = _tty_script(
            _network_command(fixture, url), env, transfer=(started, finished)
        )
    assert code == 0, text
    assert len(frames) >= 2, (
        "indicator must advance while the real curl transfer is active"
    )
    assert "  ✓ [1/3] Download and verify the release" in text
    assert "  ✓ [2/3] Install Vibecrafted and command-line tools" in text
    assert "  ✓ [3/3] Complete" in text
    assert "Installed Vibecrafted 9.9.9-test" in text
    assert "%" not in text
    assert "runtime-install-result" not in text


def test_redirected_download_stays_plain_and_concise(tmp_path):
    fixture, env, _ = _fixture(tmp_path)
    with _download_server(tmp_path, fixture["archive"], unknown_length=True) as (
        url,
        _,
        _,
    ):
        result = _run(_network_command(fixture, url), env, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert "\r" not in result.stdout and "\x1b" not in result.stdout
    assert not any(frame in result.stdout for frame in FRAMES)
    assert "  · [1/3] Download" in result.stdout
    assert "  ✓ [1/3] Download" in result.stdout
    assert "  ✓ [2/3] Install" in result.stdout
    assert "  ✓ [3/3] Complete" in result.stdout
    assert len(result.stdout.splitlines()) <= 10


def test_tty_failed_download_settles_failed_phase_and_keeps_diagnostics(tmp_path):
    fixture, env, _ = _fixture(tmp_path)
    with _download_server(tmp_path, fixture["archive"], failure=True) as (url, _, _):
        code, text, _ = _tty_script(_network_command(fixture, url), env)
    assert code == 1
    assert "  ✗ [1/3] Download and verify the release" in text
    assert "✓ [1/3]" not in text and "Installed Vibecrafted" not in text
    assert "Could not download the release archive" in text
    detail = (fixture["home"] / ".vibecrafted/install.log").read_text()
    assert "curl: (22)" in detail and "503" in detail


def test_immediate_tty_phase_transitions_never_wait_for_an_unopened_reader(tmp_path):
    _, env, command = _fixture(tmp_path)
    original = BOOTSTRAP.read_text()
    source = original.replace(
        '  installer_stage 1 "Download and verify the release"\n',
        """  for ((transition=0; transition<40; transition++)); do
    installer_stage 1 "Immediate first phase $transition"
    installer_stage 2 "Immediate second phase $transition"
  done
  installer_stage 1 "Download and verify the release"
""",
        1,
    )
    assert source != original
    code, text, _ = _tty_script(command, env, source=source.encode())
    assert code == 0, text
    assert text.count("✓ [1/3] Immediate first phase") == 40
    assert text.count("✓ [2/3] Immediate second phase") == 40
    assert "✓ [3/3] Complete" in text


def test_presenter_stop_does_not_need_a_writable_stop_path(tmp_path):
    fixture, env, command = _fixture(
        tmp_path,
        body="""sleep 1.1
for path in "$VIBECRAFTED_HOME"/.install-output.*; do
  mkdir "$path/progress-1.stop" "$path/progress-2.stop" "$path/progress-3.stop"
done
""",
    )
    # Directories make the former stop-file redirects fail on every UID,
    # reproducing refused/ENOSPC-style allocation without filling any disk.
    code, text, _ = _tty_script(command, env)
    assert code == 0, text
    assert "  ✓ [2/3] Install" in text
    assert not list((fixture["home"] / ".vibecrafted").glob(".install-output.*"))


def test_late_diagnostic_failure_settles_tty_presenter_as_failed(tmp_path):
    _, env, command = _fixture(tmp_path)
    original = BOOTSTRAP.read_text()
    source = original.replace(
        "installer_collect() {\n",
        'installer_collect() {\n  [[ "$1" != "$installer_stderr_reader" ]] || return 1\n',
        1,
    )
    assert source != original
    code, text, _ = _tty_script(command, env, source=source.encode())
    assert code == 1
    assert "  ✗ [2/3] Install" in text
    assert "✓ [2/3]" not in text and "Installed Vibecrafted" not in text
    assert "Cannot preserve installer diagnostics" in text


def test_tty_eof_stop_and_detached_child_do_not_retain_presenter_writer(tmp_path):
    worker = tmp_path / "child.py"
    worker.write_text("""import os, stat, sys, time
from pathlib import Path
root=Path(sys.argv[1])
(root/'pid').write_text(str(os.getpid()))
fifo=[]
for entry in os.listdir('/dev/fd'):
 try:
  if int(entry)>2 and stat.S_ISFIFO(os.fstat(int(entry)).st_mode): fifo.append(entry)
 except OSError: pass
(root/'fifo').write_text(','.join(fifo))
while not (root/'release').exists(): time.sleep(0.01)
os.write(1,b'late stdout\\n');os.write(2,b'late stderr\\n')
(root/'wrote').touch()
time.sleep(30)
""")
    _, env, command = _fixture(
        tmp_path, body='"$TEST_PYTHON" "$TEST_WORKER" "$TEST_CHILD_ROOT" &\n'
    )
    env.update(TEST_WORKER=str(worker), TEST_CHILD_ROOT=str(tmp_path))
    original = BOOTSTRAP.read_text()
    source = original.replace(
        "  printf '\\n' >&\"$installer_progress_fd\" || true\n", "", 1
    )
    assert source != original
    pid = None
    try:
        code, text, _ = _tty_script(command, env, source=source.encode())
        assert code == 0, text
        deadline = time.monotonic() + 3
        while (
            not ((tmp_path / "pid").exists() and (tmp_path / "fifo").exists())
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        pid = int((tmp_path / "pid").read_text())
        assert (tmp_path / "fifo").read_text() == ""
        (tmp_path / "release").touch()
        deadline = time.monotonic() + 3
        while not (tmp_path / "wrote").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp_path / "wrote").exists()
        os.kill(pid, 0)
    finally:
        if pid is None and (tmp_path / "pid").exists():
            pid = int((tmp_path / "pid").read_text())
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
