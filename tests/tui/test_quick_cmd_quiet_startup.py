"""Quick cmd wrapper starts a quiet prompt; help stays on request."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "config"
    / "vc-frame"
    / "vc-quick-cmd.sh"
)
PROFILE = REPO_ROOT / "config" / "vc-terminal" / "interactive.zsh"


def test_quick_cmd_wrapper_exports_quiet_start_and_prints_no_banner(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "probe-shell"
    probe.write_text(
        '#!/bin/sh\nprintf "QUIET=%s\\n" "${VIBECRAFTED_QUIET_START-}"\nexit 0\n'
    )
    probe.chmod(0o755)
    result = subprocess.run(
        ["bash", str(WRAPPER)],
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "USER": "op",
            "SHELL": str(probe),
            "PATH": "/usr/bin:/bin",
        },
        cwd=tmp_path,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "QUIET=1" in result.stdout
    assert "in ~" not in result.stdout
    assert "op@" not in result.stdout
    assert "Explore commands" not in result.stdout
    assert "Vibecrafted --help" not in result.stdout


def test_product_profile_skips_help_banner_when_quiet_start_is_set(
    tmp_path: Path,
) -> None:
    product = tmp_path / ".config/vibecrafted/vc-terminal"
    product.mkdir(parents=True)
    text = PROFILE.read_text(encoding="utf-8")
    (product / "interactive.zsh").write_text(text, encoding="utf-8")
    result = subprocess.run(
        [
            "/bin/zsh",
            "-dfc",
            'source "$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"; print READY',
        ],
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "TERM": "xterm",
            "VIBECRAFTED_HOME": str(tmp_path / ".vibecrafted"),
            "VIBECRAFTED_QUIET_START": "1",
        },
        cwd=tmp_path,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    assert "Your terminal is ready" not in result.stdout
    assert "Explore commands" not in result.stdout
    assert "vc-start --repo" not in result.stdout


def test_product_profile_keeps_on_request_help_behind_quiet_gate() -> None:
    text = PROFILE.read_text(encoding="utf-8")
    assert "VIBECRAFTED_QUIET_START" in text
    assert "vibecrafted --help" in text
    assert "Your terminal is ready" in text
