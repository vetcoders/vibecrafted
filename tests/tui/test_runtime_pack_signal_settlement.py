from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from tests.tui.test_runtime_pack_cli import (
    FRAME_SHA,
    SOURCE_SHA,
    TERMINAL_SHA,
    VERSION,
    _fake_runtime_payload,
    _sealed_archive,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts/install-runtime-pack.sh"


def test_runtime_pack_timeout_signal_settles_owned_python_before_bash_exits(
    tmp_path: Path,
) -> None:
    """TERM must settle the bootstrap's lease-owning Python before return."""
    payload = tmp_path / "payload/VibecraftedRuntime"
    capture = tmp_path / "argv"
    child_pid = tmp_path / "child.pid"
    mutation = tmp_path / "mutation"
    _fake_runtime_payload(payload, capture)
    archive, public_key = _sealed_archive(tmp_path, payload)
    process = subprocess.Popen(
        [
            "bash",
            str(INSTALLER),
            "--pack",
            str(archive),
            "--expected-version",
            VERSION,
            "--expected-platform",
            "darwin-arm64",
            "--expected-architecture",
            "arm64",
            "--expected-source-revision",
            SOURCE_SHA,
            "--expected-terminal-revision",
            TERMINAL_SHA,
            "--expected-frame-revision",
            FRAME_SHA,
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "CAPTURE": str(capture),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
            "INSTALLER_CHILD_FIXTURE": "1",
            "INSTALLER_CHILD_PID": str(child_pid),
            "INSTALLER_CHILD_MUTATION": str(mutation),
            "INSTALLER_CHILD_SLEEP": "2",
        },
    )
    deadline = time.monotonic() + 10
    while not child_pid.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert child_pid.exists(), process.communicate(timeout=1)

    process.send_signal(signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode != 0, (stdout, stderr)
    pid = int(child_pid.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    time.sleep(2.1)
    assert not mutation.exists(), "lease-owning child mutated after bootstrap settled"
