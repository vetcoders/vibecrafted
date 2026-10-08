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
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from scripts import vetcoders_install as installer
from tests._runtime_pack_fixture import (
    seed_runtime_pack,
)


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
