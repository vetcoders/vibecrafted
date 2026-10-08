"""Admit the exact source deck through VOC using the shared isolated-home fixture."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "case",
    [
        "real_selected_generation_catalog_reaches_voc",
        "real_deck_receipt_confirms_voc_declaration",
    ],
)
def test_voc_admits_real_core(case: str) -> None:
    result = subprocess.run(
        [
            "cargo",
            "test",
            "--manifest-path",
            "vibecrafted-app/Cargo.toml",
            "-p",
            "voc",
            "--test",
            "launch_contract",
            case,
            "--",
            "--ignored",
            "--exact",
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "VIBECRAFTED_PYTHON": sys.executable,
            "VC_TEST_REAL_DECK": str(REPO_ROOT / "scripts" / "vibecrafted"),
        },
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed; 0 failed; 0 ignored" in result.stdout, result.stdout
