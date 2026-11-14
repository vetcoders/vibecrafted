"""Both command-deck carriers must reach the actual PR closure CLI."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "relative",
    ["scripts/vibecrafted", "vibecrafted-core/vibecrafted_core/deck/vibecrafted"],
)
def test_pr_closure_release_help_through_real_deck(relative, tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("VIBECRAFTED_")
        and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    env.update(
        VIBECRAFTED_HOME=str(tmp_path / "home"), VIBECRAFTED_PYTHON=sys.executable
    )
    result = subprocess.run(
        ["bash", str(ROOT / relative), "pr-closure", "release", "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--batch" in result.stdout
    assert "--repo" in result.stdout
    assert "--output" in result.stdout
