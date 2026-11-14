"""Public installer presentation; only publication/dependency commands are doubles."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.tui.test_install_bootstrap import (
    _fixture_tree_record,
    _supplied_pack_env,
    _supplied_pack_fixture,
    _write_distribution_manifest_stub,
    _write_executable,
    _write_v2_archive,
)
from tests.tui.test_install_macos_cold import _cold_fixture, _run_cold

ROOT = Path(__file__).resolve().parents[2]
# Test-only baseline falsification; there is no production output bypass.
BOOTSTRAP = Path(
    os.environ.get("VC_TEST_INSTALL_OUTPUT_BOOTSTRAP", ROOT / "install.sh")
)


def _fixture(tmp_path: Path, *, status: int = 0, body: str = ""):
    fixture = _supplied_pack_fixture(tmp_path)
    source = tmp_path / "source"
    (source / "VERSION").write_text("9.9.9-test\n")
    _write_executable(
        source / "scripts/install-runtime-pack.sh",
        "#!/bin/bash\n"
        "printf 'hdiutil: deprecated boundary diagnostic\\n' >&2\n"
        'printf \'{"schema":"vibecrafted.runtime-install-result.v1","status":"installed"}\\n\'\n'
        + body
        + (
            "printf 'RuntimeError: publication refused by fixture\\n' >&2\n"
            if status
            else ""
        )
        + f"exit {status}\n",
    )
    _write_executable(
        source / "scripts/install-foundations.sh",
        "#!/bin/bash\nprintf '\\033[36mFOUNDATIONS_PRIVATE_DETAIL\\033[0m\\n'\n",
    )
    _write_distribution_manifest_stub(source)
    _write_v2_archive(source, fixture["archive"], "vibecrafted-main")
    env = _supplied_pack_env(tmp_path, fixture)
    env.pop("VERBOSE", None)
    env["TEST_PYTHON"] = sys.executable
    env["TEST_SOURCE_ROOT"] = str(ROOT)
    command = [
        "bash",
        str(BOOTSTRAP),
        "--yes",
        "--archive-file",
        str(fixture["archive"]),
        "--runtime-pack-file",
        str(fixture["pack"]),
        "install",
    ]
    return fixture, env, command


def _run(command, env, **kwargs):
    return subprocess.run(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=30,
        check=False,
        **kwargs,
    )


def test_default_public_output_is_factual_progress_with_private_complete_log(tmp_path):
    fixture, env, command = _fixture(tmp_path)
    result = _run(command, env, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[1/3] Download and verify" in result.stdout
    assert "[2/3] Install Vibecrafted and command-line tools" in result.stdout
    assert "[3/3] Complete" in result.stdout
    assert "Installed Vibecrafted 9.9.9-test" in result.stdout
    assert "Start: vibecrafted" in result.stdout
    assert result.stderr == ""
    assert "runtime-install-result" not in result.stdout
    assert "hdiutil:" not in result.stdout
    assert "FOUNDATIONS_PRIVATE_DETAIL" not in result.stdout
    assert "\x1b" not in result.stdout and "\r" not in result.stdout
    log = fixture["home"] / ".vibecrafted/install.log"
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    detail = log.read_text()
    assert "hdiutil: deprecated boundary diagnostic" in detail
    assert "runtime-install-result" in detail
    assert "FOUNDATIONS_PRIVATE_DETAIL" in detail
    assert "Verified vibecrafted 9.9.9-test candidate" in detail


def test_failure_keeps_exit_and_full_diagnostics_without_raw_console_dump(tmp_path):
    fixture, env, command = _fixture(
        tmp_path,
        status=23,
        body="printf 'Traceback: complete private diagnostic\\n  internal frame\\n' >&2\n",
    )
    result = _run(command, env, capture_output=True)
    assert result.returncode == 23
    combined = result.stdout + result.stderr
    assert combined.count("Installation stopped at:") == 1
    assert "exit 23" in result.stderr
    assert "publication refused by fixture" in result.stderr
    assert "Next:" in result.stderr and "install.log" in result.stderr
    assert "Installed Vibecrafted" not in combined
    assert "Traceback:" not in combined
    assert "runtime-install-result" not in combined
    log = fixture["home"] / ".vibecrafted/install.log"
    assert "Traceback: complete private diagnostic\n" in log.read_text()
    assert "internal frame" in log.read_text()


def test_redirected_combined_failure_has_one_block_and_no_terminal_sequences(tmp_path):
    _, env, command = _fixture(tmp_path, status=7)
    output = tmp_path / "redirected.txt"
    with output.open("w") as stream:
        result = _run(command, env, stdout=stream, stderr=subprocess.STDOUT)
    text = output.read_text()
    assert result.returncode == 7
    assert text.count("Installation stopped at:") == 1
    assert text.count("publication refused by fixture") == 1
    assert "\x1b" not in text and "\r" not in text
    assert "runtime-install-result" not in text


@pytest.mark.parametrize("status", [130, 143])
def test_interruption_never_claims_that_publication_was_rolled_back(tmp_path, status):
    marker = tmp_path / "published"
    _, env, command = _fixture(
        tmp_path,
        status=status,
        body=f"printf applied > {str(marker)!r}\n",
    )
    result = _run(command, env, capture_output=True)
    assert result.returncode == status
    assert marker.read_text() == "applied"
    text = result.stdout + result.stderr
    assert "completed steps may remain" in text
    assert "nothing was published" not in text
    assert "rolled back" not in text
    assert "Installed Vibecrafted" not in text


def test_private_log_redacts_credentials_without_discarding_diagnostics(tmp_path):
    fixture, env, command = _fixture(
        tmp_path,
        status=9,
        body="""printf 'diagnostic before credentials\\n'
printf '%s\\n' "$VC_TEST_API_TOKEN"
printf 'Authorization: Bearer unrelated-header-secret\\n' >&2
printf 'API_TOKEN=unrelated-variable-secret\\n' >&2
printf 'download https://user:url-password@release.invalid/file?access_token=query-secret&x=1\\n'
printf '%s\\n' 'command --password flag-secret'
printf '%s\\n' '{"api_key":"json-secret","detail":"useful context"}'
printf '%s\\n' '{"metadata":false}'
printf '%s\\n' '-----BEGIN PRIVATE KEY-----' 'private-key-material' '-----END PRIVATE KEY-----'
printf 'diagnostic after credentials\\n'
""",
    )
    env["VC_TEST_API_TOKEN"] = "environment-secret-sentinel"
    env["TOKENIZERS_PARALLELISM"] = "false"
    result = _run(command, env, capture_output=True)
    log = (fixture["home"] / ".vibecrafted/install.log").read_text()
    combined = log + result.stdout + result.stderr
    for secret in (
        "environment-secret-sentinel",
        "unrelated-header-secret",
        "unrelated-variable-secret",
        "url-password",
        "query-secret",
        "flag-secret",
        "json-secret",
        "private-key-material",
    ):
        assert secret not in combined
    assert "diagnostic before credentials" in log
    assert "diagnostic after credentials" in log
    assert "useful context" in log
    assert '{"metadata":false}' in log
    assert "[redacted]" in log


def test_signal_before_handoff_reports_interruption_without_publication_claim(tmp_path):
    fixture, env, command = _fixture(tmp_path)
    source = tmp_path / "source"
    marker = tmp_path / "stage-entered"
    _write_distribution_manifest_stub(source, stage_marker=marker)
    helper = source / "scripts/distribution_manifest.py"
    helper.write_text(
        helper.read_text().replace(
            "source = Path(", "import time\ntime.sleep(30)\nsource = Path("
        )
    )
    provenance = source / "source-provenance.json"
    record = json.loads(provenance.read_text())
    record["payload"] = _fixture_tree_record(source)
    provenance.write_text(
        json.dumps(record, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    )
    _write_v2_archive(source, fixture["archive"], "vibecrafted-main")
    # This group contains only this fabricated bootstrap and its fixture children.
    process = subprocess.Popen(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        while (
            not marker.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert marker.exists(), "authenticated candidate stage was not reached"
        os.killpg(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
    assert process.returncode == 130
    assert "completed steps may remain" in stderr
    assert "nothing was published" not in stdout + stderr
    assert "Installed Vibecrafted" not in stdout + stderr


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_log_collision_preserves_foreign_file_and_refuses_before_handoff(
    tmp_path, link
):
    fixture, env, command = _fixture(tmp_path)
    home = fixture["home"] / ".vibecrafted"
    home.mkdir()
    foreign = tmp_path / "foreign"
    foreign.write_text("preserve foreign bytes\n")
    log = home / "install.log"
    if link == "symlink":
        log.symlink_to(foreign)
    else:
        os.link(foreign, log)
    before = foreign.stat().st_mode
    result = _run(command, env, capture_output=True)
    assert result.returncode != 0
    assert foreign.read_text() == "preserve foreign bytes\n"
    assert foreign.stat().st_mode == before
    assert "Installed Vibecrafted" not in result.stdout


def test_retries_append_attempts_instead_of_erasing_previous_diagnostics(tmp_path):
    fixture, env, command = _fixture(tmp_path)
    first = _run(command, env, capture_output=True)
    log = fixture["home"] / ".vibecrafted/install.log"
    before = log.read_text()
    second = _run(command, env, capture_output=True)
    assert first.returncode == second.returncode == 0
    assert log.read_text().startswith(before)
    assert log.read_text().count("begin ·") == 2
    assert log.read_text().count("end · exit=0") == 2


def test_compact_child_cooperates_with_existing_bootstrap_log(tmp_path):
    fixture, env, command = _fixture(
        tmp_path,
        body=""""$TEST_PYTHON" - "$TEST_SOURCE_ROOT" <<'PY_COMPACT'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts import vetcoders_install as installer
with installer.compact_logging(Path.home() / ".vibecrafted/install.log") as output:
    print("INNER_COMPACT_DETAIL")
    output.write("inner checkpoint\\n")
PY_COMPACT
""",
    )
    result = _run(command, env, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    text = (fixture["home"] / ".vibecrafted/install.log").read_text()
    assert "begin ·" in text and "Verified vibecrafted" in text
    assert "INNER_COMPACT_DETAIL" in text and "inner checkpoint" in text
    assert "INNER_COMPACT_DETAIL" not in result.stdout


@pytest.mark.parametrize("status", [0, 23])
def test_detached_descendant_can_write_after_foreground_completion(tmp_path, status):
    worker = tmp_path / "background.py"
    worker.write_text("""import os
import sys
import time
from pathlib import Path
root = Path(sys.argv[1])
(root / "pid").write_text(str(os.getpid()))
while not (root / "release").exists():
    time.sleep(0.01)
assert os.fstat(1).st_nlink == 0
assert os.fstat(1).st_mode & 0o777 == 0o600
print("background stdout after installer completion", flush=True)
os.write(2, b"background stderr after installer completion\\n")
(root / "wrote").write_text("both inherited streams remain writable")
time.sleep(30)
""")
    _, env, command = _fixture(
        tmp_path,
        status=status,
        body='"$TEST_PYTHON" "$TEST_BACKGROUND_SCRIPT" "$TEST_BACKGROUND_ROOT" &\n',
    )
    env["TEST_BACKGROUND_SCRIPT"] = str(worker)
    env["TEST_BACKGROUND_ROOT"] = str(tmp_path)
    pid = None
    try:
        result = _run(command, env, capture_output=True)
        assert result.returncode == status, result.stdout + result.stderr
        deadline = time.monotonic() + 5
        while not (tmp_path / "pid").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        pid = int((tmp_path / "pid").read_text())
        (tmp_path / "release").touch()
        while not (tmp_path / "wrote").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (
            tmp_path / "wrote"
        ).read_text() == "both inherited streams remain writable"
        os.kill(pid, 0)
    finally:
        if pid is None and (tmp_path / "pid").exists():
            pid = int((tmp_path / "pid").read_text())
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_occupied_descriptor_numbers_do_not_refuse_a_valid_install(tmp_path):
    _, env, command = _fixture(tmp_path)
    result = _run(
        [
            "bash",
            "-c",
            'exec 20>/dev/null 21>/dev/null 22>/dev/null 195>/dev/null 196>/dev/null 197>/dev/null; exec "$@"',
            "fixture",
            *command,
        ],
        env,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Installed Vibecrafted" in result.stdout


def test_cold_mac_failure_keeps_its_authenticated_interpreter_until_diagnostics(
    tmp_path,
):
    install, _, env = _cold_fixture(tmp_path, damage="ambiguous")
    result = _run_cold(install, env)
    assert result.returncode != 0
    assert "Could not establish exact App Main ownership" in result.stderr
    assert "No such file or directory" not in result.stderr
    assert (
        "ambiguous destination Main instances"
        in (Path(env["VIBECRAFTED_HOME"]) / "install.log").read_text()
    )
    assert not list(Path(env["TMPDIR"]).glob("vibecrafted-bootstrap.*"))
