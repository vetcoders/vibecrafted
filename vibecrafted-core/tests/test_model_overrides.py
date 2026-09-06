"""Behavioral contract for safe Agy model-pin injection."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core.model_overrides import _with_model_override
from vibecrafted_core.spawn import _stdin_command


def _canonical_agy_wrapper(*prefix: str) -> list[str]:
    """Build a canonical Agy wrapper with a controlled existing option region."""

    argv = [
        "agy",
        *prefix,
        "--dangerously-skip-permissions",
        "--add-dir",
        ".",
        "--print-timeout",
        "30m",
        "--print",
        "$(cat)",
    ]
    return ["bash", "-c", f'{shlex.join(argv[:-2])} --print "$(cat)"']


def test_agy_single_pin_is_idempotent() -> None:
    pinned = _with_model_override("agy", _stdin_command("agy"), "gemini-test")

    assert shlex.split(pinned[2])[:3] == ["agy", "--model", "gemini-test"]
    assert _with_model_override("agy", pinned, "gemini-test") == pinned


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        (
            _canonical_agy_wrapper("--model", "first", "--model", "first"),
            "model_override_ambiguous_existing_model",
        ),
        (
            _canonical_agy_wrapper("--model"),
            "model_override_missing_existing_model",
        ),
        (
            ["bash", "-c", 'agy --print "$(cat)"'],
            "model_override_unsupported_agy_command_shape",
        ),
    ],
)
def test_agy_invalid_existing_pin_or_shell_shape_is_rejected_before_launch(
    command: list[str], reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        _with_model_override("agy", command, "requested")


def test_agy_model_metacharacters_reach_fake_cli_as_one_value(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "argv.txt"
    shell_payload_marker = tmp_path / "shell-payload-ran"
    command_substitution_marker = tmp_path / "command-substitution-ran"
    fake_agy = tmp_path / "agy"
    fake_agy.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n', encoding="utf-8"
    )
    fake_agy.chmod(0o755)
    requested = (
        f"gemini test; touch {shell_payload_marker}; "
        f"$(touch {command_substitution_marker})"
    )

    completed = subprocess.run(
        _with_model_override("agy", _stdin_command("agy"), requested),
        cwd=tmp_path,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CAPTURE": str(capture),
        },
        input="prompt body",
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert capture.read_text(encoding="utf-8").splitlines()[:3] == [
        "--model",
        requested,
        "--dangerously-skip-permissions",
    ]
    assert not shell_payload_marker.exists()
    assert not command_substitution_marker.exists()
