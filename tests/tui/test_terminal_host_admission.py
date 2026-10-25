"""One native terminal host per user: admission, reuse, refusal and receipts.

Every host here is a private compiled fake under tmp_path. Nothing reads,
signals or connects to /Applications or to any running terminal: drivers run
inside a sandbox profile that denies /Applications, and every state directory
is a private VIBECRAFTED_TERMINAL_HOST_DIR.

The fakes are real linker-signed (ad-hoc) Mach-O files: the private control of
the signed pairing. Each fixture is a private generation carrying the helper
and its own ``libexec/vc-terminal`` bytes, exactly where the product carries
them. Developer ID production policy is exercised in-process only.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import fcntl
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
#ifndef VARIANT
#define VARIANT "A"
#endif
/* A page of its own: rebuilding with another VARIANT changes code identity
   without touching the code pages a running host executes. */
__attribute__((used, aligned(16384))) char vc_variant[16384] = "VCVARIANT-" VARIANT;
static int addr(struct sockaddr_un *a, const char *p) {
  memset(a, 0, sizeof *a); a->sun_family = AF_UNIX;
  if (strlen(p) >= sizeof a->sun_path) return -1;
  strcpy(a->sun_path, p); return 0;
}
/* Test-only race: once the client has written its request and is returning,
   it publishes other bytes in place over its own path (and the generation
   terminal when named), inode unchanged. */
static const char *republish_self;
static void republish(void) {
  const char *bytes = getenv("FAKE_MSG_REPUBLISH");
  const char *targets[2] = {republish_self, getenv("FAKE_MSG_REPUBLISH_EXPECTED")};
  for (int j = 0; j < 2; j++) {
    if (!targets[j]) continue;
    FILE *src = fopen(bytes, "rb"), *dst = fopen(targets[j], "r+b");
    if (!src || !dst) _exit(21);
    char buf[8192]; size_t n; off_t total = 0;
    while ((n = fread(buf, 1, sizeof buf, src)) > 0) {
      if (fwrite(buf, 1, n, dst) != n) _exit(22);
      total += n;
    }
    fclose(src);
    if (fflush(dst) || ftruncate(fileno(dst), total) || fclose(dst)) _exit(23);
  }
}
int main(int argc, char **argv) {
  const char *log = getenv("FAKE_HOST_LOG");
  if (argc >= 5 && !strcmp(argv[1], "msg") && !strcmp(argv[2], "--socket")) {
    if (getenv("FAKE_MSG_FAIL")) return 3;
    if (getenv("FAKE_MSG_REPUBLISH")) { republish_self = argv[0]; atexit(republish); }
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


def _build_fake_host(build: Path, variant: str) -> Path:
    source = build / "host.c"
    source.write_text(FAKE_HOST_C)
    binary = build / variant / "vc-terminal"
    binary.parent.mkdir()
    subprocess.run(
        [
            "/usr/bin/cc",
            "-O1",
            f'-DVARIANT="{variant}"',
            "-o",
            str(binary),
            str(source),
        ],
        check=True,
        timeout=120,
    )
    return binary


@pytest.fixture(scope="module")
def fake_host_binary(tmp_path_factory) -> Path:
    return _build_fake_host(tmp_path_factory.mktemp("fake-host-build"), "A")


@pytest.fixture(scope="module")
def variant_host_binary(tmp_path_factory) -> Path:
    return _build_fake_host(tmp_path_factory.mktemp("fake-host-variant"), "B")


def _codesign_cdhash(path: Path) -> str:
    """The installer authority's reading of one file (`codesign -d` CDHash=)."""
    details = subprocess.run(
        ["/usr/bin/codesign", "-d", "--verbose=4", str(path)],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    fields = dict(
        line.split("=", 1) for line in details.stderr.splitlines() if "=" in line
    )
    return fields["CDHash"]


def _overwrite_in_place(path: Path, data: bytes) -> None:
    inode = path.stat().st_ino
    with open(path, "r+b") as handle:
        handle.write(data)
        handle.truncate()
    assert path.stat().st_ino == inode


def _rewrite_record(fx: Fixture, **changes) -> None:
    path = fx.state / "host.json"
    record = json.loads(path.read_text())
    for key, value in changes.items():
        if value is None:
            record.pop(key)
        else:
            record[key] = value
    replacement = fx.state / ".host.json.test"
    replacement.write_text(json.dumps(record))
    replacement.chmod(0o600)
    os.replace(replacement, path)


def _helper_module():
    sys.path.insert(0, str(HELPER.parent))
    try:
        import terminal_host  # type: ignore[import-not-found]
    finally:
        sys.path.remove(str(HELPER.parent))
    return terminal_host


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
        # The selected generation: its helper and its own terminal bytes.
        self.generation = self.root / "generation"
        self.helper = (
            self.generation / "vibecrafted-core/vibecrafted_core/terminal_host.py"
        )
        self.helper.parent.mkdir(parents=True)
        shutil.copyfile(HELPER, self.helper)
        self.expected = self.generation / "libexec/vc-terminal"
        self.expected.parent.mkdir()
        shutil.copyfile(binary, self.expected)
        self.expected.chmod(0o755)
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
            "HELPER": str(self.helper),
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
        assert shim[1:4] == ["-I", "-B", str(fx.helper)] and shim[4] == "spawn"
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


def test_admitted_code_is_the_signed_pair_the_installer_reads(fx: Fixture) -> None:
    # Caller environment is not authority: a forged code id changes nothing.
    forged = {"VIBECRAFTED_TERMINAL_CODE_ID": "0" * 40}
    host = fx.launch(**forged)
    fx.wait_for(lambda: any(r[0].startswith("start") for r in fx.records()))
    record = json.loads((fx.state / "host.json").read_text())
    fx.wait_for(lambda: Path(record["socket"]).exists())
    assert record["pid"] == host.pid
    assert record["code_cdhash"] == _codesign_cdhash(fx.host)
    assert record["code_cdhash"] == _codesign_cdhash(fx.expected)
    assert record["code_team"] is None  # private ad-hoc control, not production
    receipt_dir = fx.root / "receipt"
    receipt_dir.mkdir(mode=0o700)
    receipt = receipt_dir / "launch.json"
    second = fx.launch(RECEIPT=str(receipt), **forged)
    assert second.wait(timeout=20) == 0, second.stderr.read()
    payload = json.loads(receipt.read_text())
    assert payload["kind"] == "reused"
    assert payload["code_cdhash"] == record["code_cdhash"]
    assert payload["code_proof"] == "kernel_live_cdhash"


def test_same_inode_republish_refuses_the_old_loaded_image(
    fx: Fixture, variant_host_binary: Path
) -> None:
    first = _start_one_host(fx)
    published = variant_host_binary.read_bytes()
    _overwrite_in_place(fx.host, published)
    _overwrite_in_place(fx.expected, published)
    second = fx.launch()
    assert second.wait(timeout=20) == 75
    assert "not admitted for the installed host code" in second.stderr.read()
    assert [r[0].split()[0] for r in fx.records()] == ["start"]
    assert first.poll() is None and not list(fx.state.glob("window-*.env"))


def _lock_waiters(fx: Fixture) -> set[int]:
    """PIDs other than this test holding the private admission lock open."""
    found = subprocess.run(
        ["/usr/sbin/lsof", "-t", str(fx.state / "admission.lock")],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return {int(pid) for pid in found.stdout.split()} - {os.getpid()}


def test_republish_while_admission_waits_on_lock_is_refused_before_delivery(
    fx: Fixture, variant_host_binary: Path
) -> None:
    """Code read before the lock is stale proof once the lock is finally held."""
    first = _start_one_host(fx)
    lock = os.open(fx.state / "admission.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        second = fx.launch()
        # The waiter has passed every pre-lock read and now blocks on the lock.
        fx.wait_for(lambda: bool(_lock_waiters(fx)))
        published = variant_host_binary.read_bytes()
        _overwrite_in_place(fx.host, published)
        _overwrite_in_place(fx.expected, published)
    finally:
        os.close(lock)
    assert second.wait(timeout=30) == 75
    stderr = second.stderr.read()
    assert "not admitted for the installed host code" in stderr
    assert "delivery" not in stderr  # no client of the new bytes was run
    assert [r[0].split()[0] for r in fx.records()] == ["start"]
    assert first.poll() is None and not list(fx.state.glob("window-*.env"))


@pytest.mark.parametrize("published", ["paired", "host-only", "unsigned"])
def test_republish_during_delivery_leaves_the_window_unconfirmed(
    fx: Fixture, variant_host_binary: Path, published: str
) -> None:
    """The receipt names admitted code only if the static pair still is that code."""
    first = _start_one_host(fx)
    loaded = _codesign_cdhash(fx.host)
    inode = fx.host.stat().st_ino
    if published == "unsigned":
        bytes_path = fx.root / "tampered"
        data = fx.host.read_bytes()
        assert data.count(b"VCVARIANT-A") == 1
        bytes_path.write_bytes(data.replace(b"VCVARIANT-A", b"VCVARIANT-Z"))
    else:
        bytes_path = variant_host_binary
    race = {"FAKE_MSG_REPUBLISH": str(bytes_path)}
    if published == "paired":
        race["FAKE_MSG_REPUBLISH_EXPECTED"] = str(fx.expected)
    receipt_dir = fx.root / "receipt"
    receipt_dir.mkdir(mode=0o700)
    receipt = receipt_dir / "reuse.json"
    second = fx.launch(RECEIPT=str(receipt), **race)
    assert second.wait(timeout=30) == 75
    stderr = second.stderr.read()
    assert "create-window written" in stderr and "window unconfirmed" in stderr
    assert not receipt.exists()
    # The actual client did publish: same inode, other bytes, A still loaded.
    assert fx.host.stat().st_ino == inode
    assert fx.host.read_bytes() == bytes_path.read_bytes()
    module = _helper_module()
    assert module.live_code_hash(first.pid) == loaded
    # Written once, never retried, no duplicate host, nothing unlinked.
    assert [r[0].split()[0] for r in fx.records()] == ["start", "window"]
    assert first.poll() is None
    assert len(list(fx.state.glob("window-*.env"))) == 1


def test_loaded_image_is_kernel_truth_not_the_record(
    fx: Fixture, variant_host_binary: Path
) -> None:
    first = _start_one_host(fx)
    loaded = _codesign_cdhash(fx.host)
    published = variant_host_binary.read_bytes()
    _overwrite_in_place(fx.host, published)
    _overwrite_in_place(fx.expected, published)
    # A record claiming the newly published code cannot vouch for the old image.
    _rewrite_record(fx, code_cdhash=_codesign_cdhash(variant_host_binary))
    second = fx.launch()
    assert second.wait(timeout=20) == 75
    assert f"runs code {loaded}" in second.stderr.read()
    assert [r[0].split()[0] for r in fx.records()] == ["start"]
    assert first.poll() is None


def test_same_inode_unresigned_byte_tamper_is_refused(fx: Fixture) -> None:
    first = _start_one_host(fx)
    data = fx.host.read_bytes()
    assert data.count(b"VCVARIANT-A") == 1
    _overwrite_in_place(fx.host, data.replace(b"VCVARIANT-A", b"VCVARIANT-Z"))
    second = fx.launch()
    assert second.wait(timeout=20) == 78
    assert "signature invalid" in second.stderr.read()
    assert [r[0].split()[0] for r in fx.records()] == ["start"]
    assert first.poll() is None


def test_live_host_admitted_without_code_identity_is_refused(fx: Fixture) -> None:
    first = _start_one_host(fx)
    _rewrite_record(fx, code_cdhash=None)
    second = fx.launch()
    assert second.wait(timeout=20) == 75
    assert "not admitted for the installed host code" in second.stderr.read()
    assert [r[0].split()[0] for r in fx.records()] == ["start"]
    assert first.poll() is None


@pytest.mark.parametrize(
    "damage", ["unmatched-pair", "unsigned", "malformed", "generation-missing"]
)
def test_unproven_host_code_never_starts_a_host(
    fx: Fixture, variant_host_binary: Path, damage: str
) -> None:
    if damage == "unmatched-pair":
        replacement = fx.host.with_name("vc-terminal.new")
        shutil.copyfile(variant_host_binary, replacement)
        replacement.chmod(0o755)
        os.replace(replacement, fx.host)
    elif damage == "unsigned":
        subprocess.run(
            ["/usr/bin/codesign", "--remove-signature", str(fx.host)],
            check=True,
            timeout=20,
        )
    elif damage == "malformed":
        fx.host.write_bytes(b"#!/bin/sh\nexit 0\n")
    else:
        fx.expected.unlink()
    process = fx.launch()
    assert process.wait(timeout=20) == 78
    assert process.stderr.read().startswith("vc-terminal: ")
    assert fx.records() == [] and not (fx.state / "host.json").exists()


def test_production_policy_requires_paired_developer_id(fx: Fixture) -> None:
    module = _helper_module()
    assert module.production_policy(module.FIXED_HOST)
    assert not module.production_policy(str(fx.host))
    identity = module.static_code_identity(str(fx.host))
    assert identity.adhoc and identity.team is None
    assert identity.cdhash == _codesign_cdhash(fx.host)
    with pytest.raises(module.Refusal) as adhoc:
        module.require_paired_developer_id(module.FIXED_HOST, identity)
    assert adhoc.value.code == 78 and "Developer ID" in str(adhoc.value)
    # A Team on the terminal still needs a signed main app with that Team.
    teamed = module.CodeIdentity(identity.cdhash, "TEAMFIXTURE", False)
    with pytest.raises(module.Refusal) as unpaired:
        module.require_paired_developer_id(str(fx.host), teamed)
    assert unpaired.value.code == 78


def test_fixed_host_policy_has_no_environment_switch(fx: Fixture, monkeypatch) -> None:
    """A perfectly paired ad-hoc identity on the fixed path is still refused.

    Static reads are bound to the private fixture so nothing touches
    /Applications; every caller knob a launch could carry is set hostile.
    """
    module = _helper_module()
    private = module.static_code_identity(str(fx.host))
    reads: list[str] = []

    def bound(path: str, *, basic: bool = False):
        reads.append(path)
        return private

    monkeypatch.setattr(module, "static_code_identity", bound)
    monkeypatch.setattr(module, "generation_terminal", lambda: str(fx.expected))
    for name in (
        "VIBECRAFTED_TERMINAL_CODE_ID",
        "VIBECRAFTED_TERMINAL_HOST",
        "VIBECRAFTED_TERMINAL_HOST_DIR",
        "VIBECRAFTED_TERMINAL_POLICY",
        "VIBECRAFTED_RUNTIME_ROOT",
    ):
        monkeypatch.setenv(name, private.cdhash if "CODE" in name else str(fx.root))
    assert module.admitted_code_identity(str(fx.host)) == private
    with pytest.raises(module.Refusal) as refusal:
        module.admitted_code_identity(module.FIXED_HOST)
    assert refusal.value.code == 78 and "Developer ID" in str(refusal.value)
    assert reads == [
        str(fx.expected),
        str(fx.host),
        str(fx.expected),
        module.FIXED_HOST,
    ]


def test_fixed_host_ancestor_uses_paired_static_and_live_code_identity(
    monkeypatch,
) -> None:
    module = _helper_module()
    code = module.CodeIdentity("ab" * 20, "TEAMFIXTURE", False)
    identities = {
        40: module.ProcessIdentity(40, os.getuid(), "4.000000", "/bin/zsh", 41),
        41: module.ProcessIdentity(41, os.getuid(), "5.000000", module.FIXED_HOST, 1),
    }
    reads: list[str] = []

    monkeypatch.setattr(module, "process_identity", identities.get)
    monkeypatch.setattr(module, "live_code_hash", lambda pid: code.cdhash)

    def paired(path: str):
        reads.append(path)
        return code

    monkeypatch.setattr(module, "admitted_code_identity", paired)
    assert module.owns_fixed_terminal_ancestor(40)
    assert reads == [module.FIXED_HOST, module.FIXED_HOST]


def test_process_identity_includes_kernel_parent() -> None:
    module = _helper_module()
    identity = module.process_identity(os.getpid())
    assert identity is not None
    assert identity.uid == os.getuid()
    assert identity.ppid == os.getppid()


def test_fixed_host_ancestor_refuses_wrong_uid_live_code_or_static_pair(
    monkeypatch,
) -> None:
    module = _helper_module()
    code = module.CodeIdentity("ab" * 20, "TEAMFIXTURE", False)
    valid = module.ProcessIdentity(41, os.getuid(), "5.000000", module.FIXED_HOST, 1)

    monkeypatch.setattr(module, "process_identity", lambda _pid: valid)
    monkeypatch.setattr(module, "admitted_code_identity", lambda _path: code)
    monkeypatch.setattr(module, "live_code_hash", lambda _pid: "cd" * 20)
    with pytest.raises(module.Refusal, match="does not run the admitted code"):
        module.owns_fixed_terminal_ancestor(41)

    wrong_uid = module.ProcessIdentity(
        41, os.getuid() + 1, "5.000000", module.FIXED_HOST, 1
    )
    monkeypatch.setattr(module, "process_identity", lambda _pid: wrong_uid)
    with pytest.raises(module.Refusal, match="belongs to another user"):
        module.owns_fixed_terminal_ancestor(41)

    monkeypatch.setattr(module, "process_identity", lambda _pid: valid)

    def refused_pair(_path: str):
        raise module.Refusal(module.EX_CONFIG, "selected generation pair differs")

    monkeypatch.setattr(module, "admitted_code_identity", refused_pair)
    with pytest.raises(module.Refusal, match="selected generation pair differs"):
        module.owns_fixed_terminal_ancestor(41)

    identities = iter(
        [
            module.CodeIdentity("ab" * 20, "TEAMFIXTURE", False),
            module.CodeIdentity("cd" * 20, "TEAMFIXTURE", False),
        ]
    )
    monkeypatch.setattr(
        module, "admitted_code_identity", lambda _path: next(identities)
    )
    monkeypatch.setattr(module, "live_code_hash", lambda _pid: "ab" * 20)
    with pytest.raises(module.Refusal, match="code changed during ancestry admission"):
        module.owns_fixed_terminal_ancestor(41)

    identities = iter(
        [
            valid,
            module.ProcessIdentity(41, os.getuid(), "6.000000", module.FIXED_HOST, 1),
        ]
    )
    monkeypatch.setattr(module, "process_identity", lambda _pid: next(identities))
    monkeypatch.setattr(module, "admitted_code_identity", lambda _path: code)
    with pytest.raises(module.Refusal, match="identity changed during admission"):
        module.owns_fixed_terminal_ancestor(41)


def test_foreign_ancestor_is_not_promoted_to_fixed_host_ownership(monkeypatch) -> None:
    module = _helper_module()
    identities = {
        40: module.ProcessIdentity(40, os.getuid(), "4.000000", "/bin/zsh", 41),
        41: module.ProcessIdentity(41, os.getuid(), "5.000000", "/foreign/host", 1),
    }
    monkeypatch.setattr(module, "process_identity", identities.get)
    monkeypatch.setattr(
        module,
        "admitted_code_identity",
        lambda _path: pytest.fail("foreign paths must not reach code admission"),
    )
    assert not module.owns_fixed_terminal_ancestor(40)


def _selected_generation_terminal_paths(module) -> tuple[str, str]:
    generation = Path(os.path.realpath(module.__file__)).parents[2]
    return (
        str(generation / "libexec/vc-terminal"),
        str(generation / "libexec/vc-terminal.app/Contents/MacOS/alacritty"),
    )


def test_generation_terminal_candidates_use_only_physical_helper_root(
    monkeypatch,
) -> None:
    module = _helper_module()
    for name in (
        "VIBECRAFTED_RUNTIME_ROOT",
        "VIBECRAFTED_TERMINAL_HOST",
        "VIBECRAFTED_ROOT",
    ):
        monkeypatch.setenv(name, f"/foreign/{name}")
    assert (
        module.generation_terminal_candidates()
        == _selected_generation_terminal_paths(module)
    )


@pytest.mark.parametrize("local_index", [0, 1])
def test_nearest_generation_local_precedes_outer_invalid_fixed(
    monkeypatch, local_index: int
) -> None:
    module = _helper_module()
    local = _selected_generation_terminal_paths(module)[local_index]
    identities = {
        40: module.ProcessIdentity(40, os.getuid(), "4.000000", "/bin/zsh", 41),
        41: module.ProcessIdentity(41, os.getuid(), "5.000000", local, 42),
        42: module.ProcessIdentity(42, os.getuid(), "6.000000", module.FIXED_HOST, 1),
    }
    monkeypatch.setattr(module, "process_identity", identities.get)
    monkeypatch.setattr(
        module,
        "admitted_code_identity",
        lambda _path: (_ for _ in ()).throw(
            module.Refusal(module.EX_CONFIG, "outer fixed host is invalid")
        ),
    )
    assert not module.owns_fixed_terminal_ancestor(40)


def test_nearest_invalid_fixed_refuses_before_outer_generation_local(
    monkeypatch,
) -> None:
    module = _helper_module()
    local = _selected_generation_terminal_paths(module)[0]
    identities = {
        40: module.ProcessIdentity(40, os.getuid(), "4.000000", "/bin/zsh", 41),
        41: module.ProcessIdentity(41, os.getuid(), "5.000000", module.FIXED_HOST, 42),
        42: module.ProcessIdentity(42, os.getuid(), "6.000000", local, 1),
    }
    reads: list[int] = []

    def process(pid: int):
        reads.append(pid)
        return identities.get(pid)

    monkeypatch.setattr(module, "process_identity", process)
    monkeypatch.setattr(
        module,
        "admitted_code_identity",
        lambda _path: (_ for _ in ()).throw(
            module.Refusal(module.EX_CONFIG, "near fixed host is invalid")
        ),
    )
    with pytest.raises(module.Refusal, match="near fixed host is invalid"):
        module.owns_fixed_terminal_ancestor(40)
    assert reads == [40, 41]


def test_nearest_generation_local_precedes_outer_depth_exhaustion(monkeypatch) -> None:
    module = _helper_module()
    local = _selected_generation_terminal_paths(module)[0]
    identities = {
        40: module.ProcessIdentity(40, os.getuid(), "4.000000", "/bin/zsh", 41),
        41: module.ProcessIdentity(41, os.getuid(), "5.000000", local, 42),
        **{
            pid: module.ProcessIdentity(
                pid, os.getuid(), f"{pid}.000000", f"/foreign/{pid}", pid + 1
            )
            for pid in range(42, 80)
        },
    }
    reads: list[int] = []

    def process(pid: int):
        reads.append(pid)
        return identities.get(pid)

    monkeypatch.setattr(module, "process_identity", process)
    assert not module.owns_fixed_terminal_ancestor(40)
    assert reads == [40, 41]


@pytest.mark.parametrize("failure", ["unreadable", "cycle", "depth"])
def test_inconclusive_fixed_host_ancestry_is_refused(monkeypatch, failure: str) -> None:
    module = _helper_module()
    if failure == "unreadable":
        identities = {}
    elif failure == "cycle":
        identities = {
            40: module.ProcessIdentity(40, os.getuid(), "4.000000", "/bin/zsh", 41),
            41: module.ProcessIdentity(41, os.getuid(), "5.000000", "/bin/sh", 40),
        }
    else:
        identities = {
            pid: module.ProcessIdentity(
                pid, os.getuid(), f"{pid}.000000", f"/foreign/{pid}", pid + 1
            )
            for pid in range(40, 73)
        }
    monkeypatch.setattr(module, "process_identity", identities.get)
    with pytest.raises(module.Refusal) as refusal:
        module.owns_fixed_terminal_ancestor(40)
    assert refusal.value.code == module.EX_TEMPFAIL
    assert failure in str(refusal.value)


def test_non_darwin_fixed_host_probe_allows_legacy_fallback(monkeypatch) -> None:
    module = _helper_module()
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(
        module,
        "process_identity",
        lambda _pid: pytest.fail("non-Darwin must not run the macOS identity probe"),
    )
    assert not module.owns_fixed_terminal_ancestor(40)


def test_fixed_host_ancestry_cli_refuses_extra_args_and_unknown_origin(
    monkeypatch, capsys
) -> None:
    module = _helper_module()
    monkeypatch.setattr(module, "owns_fixed_terminal_ancestor", lambda: False)
    assert module.main(["owns-fixed-host"]) == 1
    assert capsys.readouterr() == ("", "")
    assert module.main(["owns-fixed-host", "ambient-host"]) == module.EX_USAGE
    assert "usage: owns-fixed-host" in capsys.readouterr().err
    monkeypatch.setattr(module, "owns_fixed_terminal_ancestor", lambda: True)
    assert module.main(["owns-fixed-host"]) == 0


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


def test_stale_wrapper_record_older_than_startup_wait_is_not_starting(
    fx: Fixture,
) -> None:
    """2026-10-22: a host.json whose pid is a live *wrapper* process (the
    recorded pid was reused by an unrelated process, or the wrapper crashed
    before exec) and whose start is older than STARTUP_WAIT_SECONDS must
    classify as stale, not starting. A younger wrapper stays "starting" so
    two concurrent first launches cannot both exec a host (the race
    test_concurrent_first_launch pins that). The Founder symptom was
    'Terminal exited with status 75' on every launch: the stale record pinned
    state as starting until the admission deadline, then refused EX_TEMPFAIL.
    The live wrapper is never signalled; the new launch becomes the fresh host.
    """
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
                "code_cdhash": "0" * 40,
                "wrapper_executable": "/bin/sleep",
                # No listener is ever bound to this socket path.
                "socket": str(fx.state / "host-never.sock"),
            }
        )
    )
    (fx.state / "host.json").chmod(0o600)
    host = fx.launch()
    fx.wait_for(lambda: any(r[0].startswith("start") for r in fx.records()))
    assert inert.poll() is None  # the inert wrapper is untouched
    record = json.loads((fx.state / "host.json").read_text())
    assert record["pid"] == host.pid
    assert record["pid"] != inert.pid


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
    terminal_host = _helper_module()
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
