"""Owned `telemetry` PATH name: quota engines, not a second console."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "scripts" / "vibecrafted"
TELEMETRY_ROOT = (
    REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "runtime" / "telemetry"
)
AGY_ENGINE = TELEMETRY_ROOT / "agy-monitor" / "agy_monitor.py"
KIMI_ENGINE = TELEMETRY_ROOT / "kimi-monitor" / "kimi_monitor.py"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _launcher_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    return env


def _run_telemetry(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LAUNCHER), "telemetry", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_quota_engines_compile() -> None:
    subprocess.run(
        [sys.executable, "-m", "py_compile", str(AGY_ENGINE), str(KIMI_ENGINE)],
        check=True,
        cwd=REPO_ROOT,
    )


def test_telemetry_help_lists_quota_engines(tmp_path: Path) -> None:
    result = _run_telemetry("help", env=_launcher_env(tmp_path))
    assert result.returncode == 0, result.stderr
    out = ANSI.sub("", result.stdout)
    assert "telemetry agy line|once|sessions|daemon" in out
    assert "telemetry kimi line|once|daemon" in out
    assert "telemetry line" in out
    assert "telemetry once" in out
    assert "telemetry smoke" in out


def test_telemetry_agy_line_runs_vendored_engine(tmp_path: Path) -> None:
    result = _run_telemetry("agy", "line", env=_launcher_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "agy" in result.stdout.lower()


def test_telemetry_line_runs_present_engines(tmp_path: Path) -> None:
    result = _run_telemetry("line", env=_launcher_env(tmp_path))
    assert result.returncode == 0, result.stderr
    out = result.stdout.lower()
    assert "agy" in out
    assert "kimi" in out or "quota" in out


def test_telemetry_unknown_subcommand_fails(tmp_path: Path) -> None:
    result = _run_telemetry("dashboard", env=_launcher_env(tmp_path))
    assert result.returncode != 0
    err = ANSI.sub("", result.stderr)
    assert "Unknown telemetry subcommand" in err
