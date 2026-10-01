"""The installer names the active Xcode channel and refuses a beta."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHANNEL = REPO_ROOT / "scripts" / "lib" / "xcode-channel.sh"


@pytest.fixture(autouse=True)
def _darwin_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the macOS branch through a test-owned platform probe."""
    fake_bin = tmp_path / "platform-bin"
    fake_bin.mkdir()
    uname = fake_bin / "uname"
    uname.write_text("#!/bin/sh\nprintf 'Darwin\\n'\n", encoding="utf-8")
    uname.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")


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


def _run_report(developer_dir: str | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("DEVELOPER_DIR", None)
    if developer_dir is not None:
        env["DEVELOPER_DIR"] = developer_dir
    return subprocess.run(
        ["bash", "-c", f'. "{CHANNEL}"; vibecrafted_xcode_report_channel'],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_report_channel_names_beta_and_never_refuses(tmp_path: Path) -> None:
    beta = tmp_path / "Xcode-beta.app" / "Contents" / "Developer"
    beta.mkdir(parents=True)
    result = _run_report(str(beta))
    assert result.returncode == 0, result.stderr
    assert f"Xcode: beta ({beta})" in result.stdout
    assert "refusing" not in result.stderr


def test_report_channel_tolerates_missing_developer_dir(tmp_path: Path) -> None:
    missing = tmp_path / "gone" / "Contents" / "Developer"
    result = _run_report(str(missing))
    assert result.returncode == 0, result.stderr
    assert "Xcode: none (not required for install)" in result.stdout
    assert "no usable Xcode developer dir" not in result.stderr


@pytest.mark.parametrize("function", ["require_stable", "report_channel"])
def test_non_darwin_skips_xcode_even_with_beta_selected(function: str) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'uname() { printf "Linux\\n"; }; '
                'xcode-select() { echo "unexpected Xcode probe" >&2; return 99; }; '
                "export DEVELOPER_DIR=/missing/Xcode-beta.app/Contents/Developer; "
                f'. "{CHANNEL}"; vibecrafted_xcode_{function}'
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
