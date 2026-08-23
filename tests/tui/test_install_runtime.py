from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts import vetcoders_install

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_RUNTIME = REPO_ROOT / "scripts" / "install-runtime.sh"
FOUNDATIONS_SCRIPT = REPO_ROOT / "scripts" / "install-foundations.sh"


def test_install_runtime_none_is_noop(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["VIBECRAFTED_HOME"] = str(tmp_path / ".vibecrafted")
    env["VERBOSE"] = "1"  # the no-runtime notice is VERBOSE-gated; opt in to assert it

    result = subprocess.run(
        ["bash", str(INSTALL_RUNTIME), "--runtime", "none", "--platform", "linux"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Runtime horse: none" in result.stdout
    assert not (tmp_path / ".vibecrafted" / "runtime" / "runtime.json").exists()


def test_install_runtime_locterm_fast_fails_on_linux(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["VIBECRAFTED_HOME"] = str(tmp_path / ".vibecrafted")

    result = subprocess.run(
        ["bash", str(INSTALL_RUNTIME), "--runtime", "locterm", "--platform", "linux"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "locterm is macOS-only, try --runtime wezterm or --runtime microsandbox"
        in result.stderr
    )


def test_runtime_doctor_reports_active_wezterm(monkeypatch, tmp_path: Path) -> None:
    crafted_home = tmp_path / ".vibecrafted"
    runtime_dir = crafted_home / "runtime"
    runtime_dir.mkdir(parents=True)
    wezterm = tmp_path / "bin" / "wezterm"
    wezterm.parent.mkdir()
    wezterm.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    (runtime_dir / "runtime.json").write_text(
        json.dumps(
            {
                "runtime": "wezterm",
                "status": "ok",
                "path": str(wezterm),
                "message": "installed",
                "platform": "linux",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))

    finding = vetcoders_install.doctor_runtime_finding()

    assert finding.level == "ok"
    assert finding.component == "runtime:wezterm"
    assert finding.message == f"-> {wezterm}"


def test_foundations_default_prefix_uses_xdg_local_share(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env.pop("VIBECRAFTED_BIN", None)
    env.pop("VIBECRAFTED_RUNTIME_HOME", None)
    env.pop("XDG_DATA_HOME", None)

    result = subprocess.run(
        ["bash", str(FOUNDATIONS_SCRIPT), "--check", "agents"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    expected = tmp_path / ".local" / "share" / "vibecrafted" / "bin"
    assert f"Runtime bin:  {expected}" in result.stdout


def test_foundations_fail_fast_on_runtime_root_drift(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["VIBECRAFTED_HOME"] = str(tmp_path / ".vibecrafted")
    env["VIBECRAFTED_RUNTIME_HOME"] = str(tmp_path / ".legacy-runtime")

    result = subprocess.run(
        ["bash", str(FOUNDATIONS_SCRIPT), "--check", "agents"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    merged_output = f"{result.stdout}\n{result.stderr}"
    assert "✗ runtime root drift:" in merged_output


def test_install_sh_fail_fast_on_launcher_root_drift(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["VIBECRAFTED_LAUNCHER_BIN"] = str(tmp_path / ".legacy-bin")

    result = subprocess.run(
        [
            "bash",
            str(INSTALL_SH),
            "--yes",
            "--archive-file",
            str(tmp_path / "missing.tar.gz"),
            "doctor",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    merged_output = f"{result.stdout}\n{result.stderr}"
    assert "✗ launcher root drift:" in merged_output
