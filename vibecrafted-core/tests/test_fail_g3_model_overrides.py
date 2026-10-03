"""Falsifiers: Grok model pins must reach argv, not be skipped as unsupported."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core.model_overrides import (
    MODEL_OVERRIDE_FLAGS,
    _model_override_receipt,
    _with_model_override,
)


def test_fail_g3_grok_model_pin_is_supported_and_injected() -> None:
    """A requested Grok model must not be silently dropped.

    grok 1.0.21 documents ``-m, --model <MODEL>``. The adapter previously
    omitted Grok from MODEL_OVERRIDE_FLAGS, so receipts claimed
    ``model_override_skipped`` while spawn already passed ``--model``.
    """
    receipt = _model_override_receipt("grok", "grok-4.6")
    assert receipt["model_requested"] == "grok-4.6"
    assert receipt["model_override_supported"] is True
    assert receipt["model_override_skipped"] is False
    assert "model_override_skip_reason" not in receipt
    assert MODEL_OVERRIDE_FLAGS["grok"] == "--model"

    command = ["grok", "--cwd", "/repo", "--permission-mode", "bypassPermissions"]
    pinned = _with_model_override("grok", command, "grok-4.6")
    assert pinned[:3] == ["grok", "--model", "grok-4.6"]
    assert pinned[3:] == command[1:]


def test_fail_g3_grok_short_model_flag_is_an_existing_pin() -> None:
    """``-m`` already on the command is the same pin as ``--model``."""
    already = ["grok", "-m", "grok-4.6", "--cwd", "/repo"]
    assert _with_model_override("grok", already, "grok-4.6") == already
    with pytest.raises(
        ValueError, match="model_override_conflicts_with_existing_model"
    ):
        _with_model_override("grok", already, "grok-3")


def test_fail_g3_grok_model_value_reaches_fake_cli_as_one_argv(
    tmp_path: Path,
) -> None:
    """The injected pin must be one argv value, not a shell-split payload."""
    capture = tmp_path / "argv.txt"
    fake_grok = tmp_path / "grok"
    fake_grok.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n', encoding="utf-8"
    )
    fake_grok.chmod(0o755)
    requested = "grok-4.6;touch should-not-run"

    completed = subprocess.run(
        _with_model_override("grok", ["grok", "--cwd", str(tmp_path)], requested),
        cwd=tmp_path,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CAPTURE": str(capture),
        },
        check=False,
    )

    assert completed.returncode == 0
    assert capture.read_text(encoding="utf-8").splitlines()[:3] == [
        "--model",
        requested,
        "--cwd",
    ]
    assert not (tmp_path / "should-not-run").exists()
