"""The installer names the active Xcode channel and refuses a beta."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHANNEL = REPO_ROOT / "scripts" / "lib" / "xcode-channel.sh"


def _run(
    developer_dir: str, *, allow_beta: bool = False
) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "VIBECRAFTED_ALLOW_BETA_XCODE"
    }
    env["DEVELOPER_DIR"] = developer_dir
    if allow_beta:
        env["VIBECRAFTED_ALLOW_BETA_XCODE"] = "1"
    return subprocess.run(
        [
            "bash",
            "-c",
            f'. "{CHANNEL}"; vibecrafted_xcode_require_stable',
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_beta_xcode_is_named_and_refused(tmp_path: Path) -> None:
    beta = tmp_path / "Xcode-beta.app" / "Contents" / "Developer"
    beta.mkdir(parents=True)
    result = _run(str(beta))
    assert result.returncode != 0
    assert f"Xcode: beta ({beta})" in result.stdout
    assert "refusing beta Xcode" in result.stderr


def test_stable_xcode_is_named_and_accepted(tmp_path: Path) -> None:
    stable = tmp_path / "Xcode.app" / "Contents" / "Developer"
    stable.mkdir(parents=True)
    result = _run(str(stable))
    assert result.returncode == 0, result.stderr
    assert f"Xcode: stable ({stable})" in result.stdout
    assert "refusing beta Xcode" not in result.stderr


def test_missing_xcode_dir_keeps_the_builder_phrase(tmp_path: Path) -> None:
    missing = tmp_path / "gone" / "Contents" / "Developer"
    result = _run(str(missing))
    assert result.returncode != 0
    assert "no usable Xcode developer dir" in result.stderr


def test_allow_beta_names_the_channel_and_continues(tmp_path: Path) -> None:
    beta = tmp_path / "Xcode-beta.app" / "Contents" / "Developer"
    beta.mkdir(parents=True)
    result = _run(str(beta), allow_beta=True)
    assert result.returncode == 0, result.stderr
    assert f"Xcode: beta ({beta})" in result.stdout
