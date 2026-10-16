"""Regression tests for Runtime Pack fixture generation and foundation handling.

Verifies:
1. seed_runtime_pack packages an offline, synthetic test-only foundations installer
   and scripts/lib/runtime-roots.sh before source provenance seal.
2. The synthetic installer supports controlled success, failure, and observation
   without network access.
3. Missing foundation installer fails runtime install unless explicitly skipped.
4. Fixture provenance binds source before native donor injection.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

import pytest

from scripts import vetcoders_install as installer
from tests._runtime_pack_fixture import (
    fixed_terminal_fixture,
    private_child_command,
    seed_runtime_pack,
)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin Seatbelt exec policy")
@pytest.mark.parametrize("tool", ["perl", "shasum"])
def test_private_child_admits_selected_tool_variant(tmp_path, monkeypatch, tool):
    tools = tmp_path / "selected-tools"
    tools.mkdir()
    executable = tools / tool
    shutil.copyfile(f"/usr/bin/{tool}", executable)
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}:/usr/bin:/bin")
    data = tmp_path / "data"
    data.write_text("fixture-data")
    argv = (
        [str(executable), "-e", "print qq(tool-ok\\n)"]
        if tool == "perl"
        else [str(executable), "-a", "256", str(data)]
    )
    result = subprocess.run(
        private_child_command(argv),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert ("tool-ok" if tool == "perl" else str(data)) in result.stdout


# `kill -0` is itself a signal and denied under the fence, so liveness is read
# from a marker the descendant writes only if it was NOT stopped in time.
_SIGNAL_PROBE = (
    '( sleep 1; : > "$1" ) >/dev/null 2>&1 & child=$!; '
    'kill -TERM "$child" 2>/dev/null; rc=$?; sleep 1.6; '
    'if [ -e "$1" ]; then alive=1; else alive=0; fi; '
    'printf "%s %s\\n" "$rc" "$alive"'
)


def _probe_own_descendant(
    tmp_path: Path, same_sandbox_signals: bool
) -> tuple[int, int]:
    marker = tmp_path / f"descendant-ran-{int(same_sandbox_signals)}"
    result = subprocess.run(
        private_child_command(
            ["/bin/bash", "-c", _SIGNAL_PROBE, "probe", str(marker)],
            same_sandbox_signals=same_sandbox_signals,
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    kill_rc, alive = result.stdout.split()
    return int(kill_rc), int(alive)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin Seatbelt signal policy")
def test_private_child_signals_are_hard_denied_by_default(tmp_path: Path) -> None:
    """Existing callers keep the global hard deny: not even a child of its own."""
    kill_rc, alive = _probe_own_descendant(tmp_path, False)
    assert kill_rc != 0
    assert alive == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin Seatbelt signal policy")
def test_private_child_opt_in_signals_only_its_own_descendant(tmp_path: Path) -> None:
    """Opted in, the kernel lets the fenced child stop its own inert descendant,
    while an unrelated private process outside the sandbox receives nothing."""
    kill_rc, alive = _probe_own_descendant(tmp_path, True)
    assert (kill_rc, alive) == (0, 0)

    receipt = tmp_path / "outside-received-signal"
    ready = tmp_path / "outside-ready"
    outside = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, signal, sys, time\n"
                "receipt, ready = map(pathlib.Path, sys.argv[1:3])\n"
                "signal.signal(signal.SIGTERM, lambda *_: receipt.write_text('seen'))\n"
                "ready.write_text('up')\n"
                "time.sleep(20)\n"
            ),
            str(receipt),
            str(ready),
        ]
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        attempt = subprocess.run(
            private_child_command(
                [
                    "/bin/bash",
                    "-c",
                    f'kill -TERM {outside.pid} 2>/dev/null; echo "$?"',
                ],
                same_sandbox_signals=True,
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        time.sleep(0.3)
        assert attempt.stdout.strip() != "0"
        assert not receipt.exists()
        assert outside.poll() is None
    finally:
        outside.kill()
        outside.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin Seatbelt policy")
def test_private_child_signal_opt_in_keeps_network_and_applications_fences() -> None:
    profile = private_child_command(["/bin/true"], same_sandbox_signals=True)[2]
    assert "(deny network*)" in profile
    assert "(deny mach-lookup)" in profile
    assert '(deny file-read* file-write* (subpath "/Applications"))' in profile
    assert "(allow signal (target same-sandbox))" in profile
    assert "(allow signal" not in private_child_command(["/bin/true"])[2]


def test_seed_runtime_pack_includes_offline_synthetic_foundation_installer_and_roots(
    tmp_path: Path,
) -> None:
    """The fixture must bundle offline foundations installer and runtime-roots before seal."""
    payload = seed_runtime_pack(tmp_path / "pack")

    foundations_script = payload / "scripts/install-foundations.sh"
    assert foundations_script.is_file()
    assert os.access(foundations_script, os.X_OK)

    roots_script = payload / "scripts/lib/runtime-roots.sh"
    assert roots_script.is_file()

    # Verify source-provenance.json includes both files in its sealed payload
    provenance_file = payload / "source-provenance.json"
    assert provenance_file.is_file()
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    assert provenance["schema"] == installer._SOURCE_PROVENANCE_SCHEMA

    # Run the synthetic installer directly: must succeed offline and output synthetic note
    proc = subprocess.run(
        ["bash", str(foundations_script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "synthetic foundations installed (test-only)" in proc.stdout


def test_synthetic_foundations_installer_controlled_failure_and_observation(
    tmp_path: Path,
) -> None:
    """The synthetic installer must support controlled test failure and observation hooks."""
    payload = seed_runtime_pack(tmp_path / "pack")
    foundations_script = payload / "scripts/install-foundations.sh"

    # Observation hook
    env = os.environ.copy()
    env["VIBECRAFTED_TEST_FOUNDATIONS_OBSERVE"] = "probe-marker-42"
    proc_obs = subprocess.run(
        ["bash", str(foundations_script)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc_obs.returncode == 0
    assert "observation: probe-marker-42" in proc_obs.stdout

    # Failure hook
    env_fail = os.environ.copy()
    env_fail["VIBECRAFTED_TEST_FOUNDATIONS_FAIL"] = "simulated error"
    env_fail["VIBECRAFTED_TEST_FOUNDATIONS_EXIT_CODE"] = "7"
    proc_fail = subprocess.run(
        ["bash", str(foundations_script)],
        capture_output=True,
        text=True,
        env=env_fail,
        check=False,
    )
    assert proc_fail.returncode == 7
    assert "simulated error" in proc_fail.stderr


def test_missing_foundations_installer_fails_unless_explicitly_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing foundations installer fails install; skip_foundations allows bypass."""
    fixed_terminal_fixture(monkeypatch, tmp_path / "paired-host")
    home = tmp_path / "home"
    home.mkdir()
    runtime_home = home / ".local" / "share" / "vibecrafted"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / ".local" / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / ".vibecrafted"))
    monkeypatch.setenv("VC_FRAME_SOCKET_DIR", str(tmp_path / "frame-sockets"))
    monkeypatch.setattr(
        installer, "_teardown_owned_runtime_for_uninstall", lambda *_args, **_kwargs: ()
    )

    # 1. Missing installer fails runtime install
    payload_missing = seed_runtime_pack(
        tmp_path / "pack-missing", include_foundations_installer=False
    )
    assert not (payload_missing / "scripts/install-foundations.sh").exists()

    rc_missing = installer.cmd_runtime_install(
        Namespace(
            payload_root=str(payload_missing),
            app_root=None,
            terminal_host=None,
            frame_helper=None,
            skip_foundations=False,
        )
    )
    assert rc_missing == 1

    # 2. Explicit skip allows missing installer to pass
    payload_skipped = seed_runtime_pack(
        tmp_path / "pack-skipped",
        version="9.9.9+skipped",
        include_foundations_installer=False,
    )
    rc_skipped = installer.cmd_runtime_install(
        Namespace(
            payload_root=str(payload_skipped),
            app_root=None,
            terminal_host=None,
            frame_helper=None,
            skip_foundations=True,
        )
    )
    assert rc_skipped == 0
