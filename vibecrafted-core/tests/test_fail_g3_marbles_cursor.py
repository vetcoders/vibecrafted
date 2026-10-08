"""Marbles Cursor flags must use Cursor probe 7597d881 — never hardcoded."""

from __future__ import annotations

import os
import subprocess
import types
from pathlib import Path

import pytest
from vibecrafted_core.cursor_admission import (
    CURSOR_PROBE_COMMIT,
    cursor_permission_flags,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SHELL_LIB = (
    REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "runtime" / "shell" / "lib"
)
MARBLES_SH = SHELL_LIB / "marbles.sh"


def test_fail_g3_cursor_admission_depends_on_cursor_7597d881() -> None:
    assert CURSOR_PROBE_COMMIT.startswith("7597d881")


def test_fail_g3_cursor_admission_refuses_probe_failed_error_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = types.SimpleNamespace(
        state="probe_failed",
        detail="error: --force --trust unavailable",
        version="old",
        executable="/tmp/cursor-agent",
        supports=lambda flag: True,
    )

    def probe(**_kwargs):
        return surface

    def require(flags, probed, permissions=""):
        if probed.state == "probe_failed":
            raise ValueError(probed.detail)
        return tuple(flags)

    import vibecrafted_core.continuity.capabilities as caps

    monkeypatch.setattr(caps, "probe_cursor_cli_surface", probe, raising=False)
    monkeypatch.setattr(caps, "require_cursor_flags", require, raising=False)

    with pytest.raises(ValueError, match="--force --trust unavailable"):
        cursor_permission_flags(timeout=1.0)


def test_fail_g3_cursor_admission_emits_flags_only_after_successful_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = types.SimpleNamespace(
        state="confirmed",
        detail="",
        version="new",
        executable="/tmp/cursor-agent",
        supports=lambda flag: flag in {"--force", "--trust"},
    )

    def probe(**_kwargs):
        return surface

    def require(flags, probed, permissions=""):
        assert permissions == "bypass"
        missing = [flag for flag in flags if not probed.supports(flag)]
        if missing:
            raise ValueError(f"missing {missing}")
        return tuple(flags)

    import vibecrafted_core.continuity.capabilities as caps

    monkeypatch.setattr(caps, "probe_cursor_cli_surface", probe, raising=False)
    monkeypatch.setattr(caps, "require_cursor_flags", require, raising=False)

    assert cursor_permission_flags(timeout=1.0) == ("--force", "--trust")


def test_fail_g3_marbles_fresh_cursor_does_not_hardcode_flags_when_probe_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without Cursor's probe, marbles must refuse rather than print --force --trust."""
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    script = f'''
set -euo pipefail
_vetcoders_shell_quote() {{ printf '%q' "$1"; }}
source "{MARBLES_SH}"
_vetcoders_fresh_session_command cursor "continue" headless
'''
    result = subprocess.run(
        ["bash", "-lc", script],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "cursor-agent -p --output-format stream-json --force --trust" not in combined
    assert (
        "7597d881" in combined
        or "capability probe" in combined
        or "ImportError" in combined
        or "refused" in combined
    )
