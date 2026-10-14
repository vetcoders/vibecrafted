"""One native terminal host per user: admission, reuse, refusal and receipts.

Every host here is a private compiled fake under tmp_path. Nothing reads,
signals or connects to /Applications or to any running terminal: drivers run
inside a sandbox profile that denies /Applications, and every state directory
is a private VIBECRAFTED_TERMINAL_HOST_DIR.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "vibecrafted-core/vibecrafted_core/terminal_host.py"
ENTRY = REPO_ROOT / "scripts/vc-terminal-product-entry.sh"

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="kernel host identity is only proven on macOS"
)

# A native fake: as a host it listens on its default $TMPDIR/Alacritty-<pid>.sock
# and logs each request as one NUL-separated record; `msg --socket PATH
# create-window ...` writes one.
FAKE_HOST_C = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/un.h>
static int addr(struct sockaddr_un *a, const char *p) {
  memset(a, 0, sizeof *a); a->sun_family = AF_UNIX;
  if (strlen(p) >= sizeof a->sun_path) return -1;
  strcpy(a->sun_path, p); return 0;
}
int main(int argc, char **argv) {
  const char *log = getenv("FAKE_HOST_LOG");
  if (argc >= 5 && !strcmp(argv[1], "msg") && !strcmp(argv[2], "--socket")) {
    if (getenv("FAKE_MSG_FAIL")) return 3;
    struct sockaddr_un a; int s = socket(AF_UNIX, SOCK_STREAM, 0);
    if (addr(&a, argv[3]) || connect(s, (void *)&a, sizeof a)) return 2;
    for (int i = 4; i < argc; i++) write(s, argv[i], strlen(argv[i]) + 1);
    shutdown(s, SHUT_WR); close(s); return 0;
  }
  if (argc >= 2 && strcmp(argv[1], "msg")) {
    char path[200];
    snprintf(path, sizeof path, "%s/Alacritty-%d.sock", getenv("TMPDIR"), getpid());
    FILE *f = fopen(log, "a");
    fprintf(f, "start %d", getpid());
    for (int i = 0; i < argc; i++) fprintf(f, "%c%s", 0, argv[i]);
    fputc(30, f); fclose(f);
    struct sockaddr_un a; int s = socket(AF_UNIX, SOCK_STREAM, 0);
    if (getenv("FAKE_HOST_BIND_DELAY")) usleep(300000);
    if (addr(&a, path) || bind(s, (void *)&a, sizeof a) || listen(s, 16)) return 4;
    alarm(60);
    for (;;) {
      int c = accept(s, 0, 0); if (c < 0) continue;
      char buf[65536]; ssize_t n, total = 0;
      while ((n = read(c, buf + total, sizeof buf - total)) > 0) total += n;
      close(c);
      if (total == 0) continue;
      f = fopen(log, "a"); fprintf(f, "window %d", getpid());
      fputc(0, f); fwrite(buf, 1, total, f); fputc(30, f); fclose(f);
    }
  }
  return 9;
}
"""

DRIVER = r"""
out="$(exec "$PYTHON" -I -B "$HELPER" admit --wrapper-pid "$$" --host "$HOST" \
  --config "$CONFIG" ${RECEIPT:+--receipt "$RECEIPT"} -- "$@")" || exit $?
case "$out" in
  reused) printf 'reused\n' >>"$DECISIONS"; exit 0 ;;
  fresh) printf 'fresh\n' >>"$DECISIONS"
    exec "$HOST" --config-file "$CONFIG" "$@" ;;
esac
exit 70
"""

SANDBOX = '(version 1)(allow default)(deny file-read* file-write* process-exec (subpath "/Applications"))'


@pytest.fixture(scope="module")
def fake_host_binary(tmp_path_factory) -> Path:
    build = tmp_path_factory.mktemp("fake-host-build")
    source = build / "host.c"
    source.write_text(FAKE_HOST_C)
    binary = build / "vc-terminal"
    subprocess.run(
        ["/usr/bin/cc", "-O1", "-o", str(binary), str(source)], check=True, timeout=120
    )
    return binary


class Fixture:
    def __init__(self, root: Path, binary: Path) -> None:
        self.root = root.resolve()
        self.state = Path(f"/tmp/vcth-{os.getpid()}-{time.monotonic_ns() % 10**8}")
        self.state.mkdir(mode=0o700)
        self.temp = Path(f"{self.state}-T")
        self.temp.mkdir(mode=0o700)
        self.host = self.root / "Vibecrafted.app/Contents/MacOS/vc-terminal"
        self.host.parent.mkdir(parents=True)
        shutil.copyfile(binary, self.host)
        self.host.chmod(0o755)
        self.config = self.root / "vc-terminal.toml"
        self.config.write_text(
            '[terminal]\nshell = { program = "/bin/sh", args = ["-c", "exec primary"] }\n'
        )
        self.log = self.root / "host.log"
        self.decisions = self.root / "decisions"
        self.cwd = self.root / "work dir ✓"
        self.cwd.mkdir()
        self.processes: list[subprocess.Popen] = []

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.root),
            "PYTHON": sys.executable,
            "HELPER": str(HELPER),
            "HOST": str(self.host),
            "CONFIG": str(self.config),
            "DECISIONS": str(self.decisions),
            "FAKE_HOST_LOG": str(self.log),
            "VIBECRAFTED_TERMINAL_HOST_DIR": str(self.state),
            "TMPDIR": str(self.temp),
        }
        env.update(extra)
        return env

    def launch(self, *argv: str, **extra: str) -> subprocess.Popen:
        process = subprocess.Popen(
            [
                "/usr/bin/sandbox-exec",
                "-p",
                SANDBOX,
                "/bin/bash",
                "-c",
                DRIVER,
                "driver",
                *argv,
            ],
            env=self.env(**extra),
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.processes.append(process)
        return process

    def records(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        lines = self.log.read_bytes().split(b"\x1e")
        return [line.decode().split("\0") for line in lines if line]

    def wait_for(self, predicate, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError(f"condition not reached; log={self.records()}")

    def close(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        shutil.rmtree(self.state, ignore_errors=True)
        shutil.rmtree(self.temp, ignore_errors=True)


@pytest.fixture
def fx(tmp_path: Path, fake_host_binary: Path):
    fixture = Fixture(tmp_path, fake_host_binary)
    yield fixture
    fixture.close()


def _start_one_host(fx: Fixture) -> subprocess.Popen:
    host = fx.launch()
    fx.wait_for(lambda: any(r[0].startswith("start") for r in fx.records()))
    record = json.loads((fx.state / "host.json").read_text())
    fx.wait_for(lambda: Path(record["socket"]).exists())
    return host


def test_concurrent_first_launch_starts_exactly_one_host(fx: Fixture) -> None:
    title = "-e"
    command = ["/usr/bin/printf", "zażółć spaces\nnew line", "--title", "-e"]
    launches = [fx.launch("--title", title, "-e", *command) for _ in range(4)]
    fx.wait_for(lambda: len(fx.records()) == 4)
    starts = [r for r in fx.records() if r[0].startswith("start")]
    windows = [r for r in fx.records() if r[0].startswith("window")]
    assert len(starts) == 1 and len(windows) == 3
    host_pid = int(starts[0][0].split()[1])
    assert fx.decisions.read_text().split().count("fresh") == 1
    record = json.loads((fx.state / "host.json").read_text())
    assert record["pid"] == host_pid and record["executable"] == str(fx.host)
    # The fresh host received the caller argv exactly; its socket is its own default.
    assert record["socket"] == f"{fx.temp}/Alacritty-{host_pid}.sock"
    assert starts[0][1:] == [
        str(fx.host),
        "--config-file",
        str(fx.config),
        "--title",
        title,
        "-e",
        *command,
    ]
    for window in windows:
        assert int(window[0].split()[1]) == host_pid
        request = window[1:-1]
        assert (
            request[:2] == ["--socket", record["socket"]]
            or request[0] == "create-window"
        )
        create = request[request.index("create-window") + 1 :]
        assert create[:4] == ["--working-directory", str(fx.cwd), "--title", "-e"]
        shim = create[create.index("-e", 4) + 1 :]
        assert shim[1:4] == ["-I", "-B", str(HELPER)] and shim[4] == "spawn"
        assert shim[6] == "--" and shim[7:] == command
    fx.wait_for(lambda: sum(p.poll() is not None for p in launches) == 3)
    reused = [p for p in launches if p.poll() is not None]
    assert len(reused) == 3 and all(p.returncode == 0 for p in reused)


def test_reused_window_runs_default_shell_and_carries_receipt(fx: Fixture) -> None:
    _start_one_host(fx)
    receipt_dir = fx.root / "receipt"
    receipt_dir.mkdir(mode=0o700)
    receipt = receipt_dir / "launch.json"
    second = fx.launch(RECEIPT=str(receipt))
    assert second.wait(timeout=20) == 0, second.stderr.read()
    window = [r for r in fx.records() if r[0].startswith("window")][-1]
    assert window[-4:-1] == ["/bin/sh", "-c", "exec primary"]
    payload = json.loads(receipt.read_text())
    host = json.loads((fx.state / "host.json").read_text())
    assert payload["kind"] == "reused" and payload["host_pid"] == host["pid"]
    assert payload["host_start"] == host["start"]
    assert payload["delivery"] == "create_window_request_written"
    assert payload["agent_start_proven"] is False
    assert oct(receipt.stat().st_mode & 0o777) == "0o600"


def test_fresh_receipt_names_the_wrapper_pid_that_becomes_host(fx: Fixture) -> None:
    receipt_dir = fx.root / "receipt"
    receipt_dir.mkdir(mode=0o700)
    receipt = receipt_dir / "launch.json"
    host = fx.launch(RECEIPT=str(receipt))
    fx.wait_for(lambda: any(r[0].startswith("start") for r in fx.records()))
    payload = json.loads(receipt.read_text())
    assert payload["kind"] == "fresh" and payload["host_pid"] == host.pid
    assert payload["delivery"] == "host_exec_pending"


def test_lost_delivery_is_unconfirmed_and_never_starts_a_duplicate(fx: Fixture) -> None:
    _start_one_host(fx)
    second = fx.launch(FAKE_MSG_FAIL="1")
    assert second.wait(timeout=20) == 75
    assert "delivery unconfirmed" in second.stderr.read()
    assert [r[0].split()[0] for r in fx.records()] == ["start"]
    assert not list(fx.state.glob("window-*.env"))


def test_socket_peer_swap_is_refused(fx: Fixture) -> None:
    _start_one_host(fx)
    record = json.loads((fx.state / "host.json").read_text())
    os.unlink(record["socket"])
    impostor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    impostor.bind(record["socket"])
    impostor.listen(1)
    try:
        second = fx.launch()
        assert second.wait(timeout=20) == 75
        assert "is not recorded host" in second.stderr.read()
    finally:
        impostor.close()
    assert len([r for r in fx.records() if r[0].startswith("start")]) == 1


def test_replaced_host_executable_is_refused(fx: Fixture) -> None:
    _start_one_host(fx)
    replacement = fx.host.with_name("vc-terminal.new")
    shutil.copyfile(fx.host, replacement)
    replacement.chmod(0o755)
    os.replace(replacement, fx.host)
    second = fx.launch()
    assert second.wait(timeout=20) == 75
    assert "predates the installed host" in second.stderr.read()


def test_reused_pid_with_foreign_start_token_is_stale(fx: Fixture) -> None:
    inert = subprocess.Popen(["/bin/sleep", "30"])
    fx.processes.append(inert)
    (fx.state / "host.json").write_text(
        json.dumps(
            {
                "schema": "vibecrafted.terminal-host.v1",
                "pid": inert.pid,
                "start": "1.000000",
                "uid": os.getuid(),
                "executable": str(fx.host),
                "executable_file": [0, 0],
                "wrapper_executable": "/bin/bash",
                "socket": str(fx.state / "host-1.sock"),
            }
        )
    )
    (fx.state / "host.json").chmod(0o600)
    host = fx.launch()
    fx.wait_for(lambda: any(r[0].startswith("start") for r in fx.records()))
    assert inert.poll() is None
    assert json.loads((fx.state / "host.json").read_text())["pid"] == host.pid


@pytest.mark.parametrize("damage", ["mode", "symlink", "record-symlink", "record-mode"])
def test_unsafe_state_is_refused_without_a_host(fx: Fixture, damage: str) -> None:
    if damage == "mode":
        fx.state.chmod(0o755)
    elif damage == "symlink":
        real = Path(f"{fx.state}-real")
        real.mkdir(mode=0o700)
        fx.state.rmdir()
        fx.state.symlink_to(real)
    elif damage == "record-symlink":
        (fx.root / "elsewhere.json").write_text("{}")
        (fx.state / "host.json").symlink_to(fx.root / "elsewhere.json")
    else:
        (fx.state / "host.json").write_text("{}")
        (fx.state / "host.json").chmod(0o644)
    process = fx.launch()
    try:
        assert process.wait(timeout=20) == 78
    finally:
        if fx.state.is_symlink():
            shutil.rmtree(Path(f"{fx.state}-real"), ignore_errors=True)
            fx.state.unlink()
            fx.state.mkdir(mode=0o700)
        fx.state.chmod(0o700)
    assert fx.records() == []


def test_admission_refuses_a_requester_that_is_not_the_wrapper(fx: Fixture) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(HELPER),
            "admit",
            "--wrapper-pid",
            "1",
            "--host",
            str(fx.host),
            "--config",
            str(fx.config),
            "--",
        ],
        env=fx.env(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 64 and fx.records() == []


def test_spawn_consumes_private_env_file_and_execs_literal_argv(fx: Fixture) -> None:
    env_file = fx.state / "window-test.env"
    env_file.write_text(
        json.dumps({"environ": {"PATH": "/usr/bin:/bin", "VC_X": "a b\nc"}})
    )
    env_file.chmod(0o600)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(HELPER),
            "spawn",
            str(env_file),
            "--",
            "/bin/sh",
            "-c",
            'printf "%s|%s" "$VC_X" "$1"',
            "sh",
            "zażółć $(x)",
        ],
        env={"VC_X": "host"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "a b\nc|zażółć $(x)"
    assert not env_file.exists()
    env_file.write_text(json.dumps({"environ": {}}))
    env_file.chmod(0o644)
    refused = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(HELPER),
            "spawn",
            str(env_file),
            "--",
            "/usr/bin/true",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert refused.returncode == 78


def test_non_darwin_identity_is_refused(monkeypatch) -> None:
    sys.path.insert(0, str(HELPER.parent))
    try:
        import terminal_host  # type: ignore[import-not-found]
    finally:
        sys.path.remove(str(HELPER.parent))
    monkeypatch.setattr(terminal_host.sys, "platform", "linux")
    with pytest.raises(terminal_host.Refusal) as refusal:
        terminal_host.process_identity(os.getpid())
    assert refusal.value.code == 69


def test_product_entry_routes_bundle_host_through_admission() -> None:
    entry = ENTRY.read_text()
    assert (
        'admission_args=(admit --wrapper-pid "$$" --host "$host" --config "$config")'
        in entry
    )
    assert "reused) exit 0 ;;" in entry
    assert 'fresh) exec "$host" --config-file "$config"' in entry
    assert "host admission helper missing" in entry and "exit 78" in entry
