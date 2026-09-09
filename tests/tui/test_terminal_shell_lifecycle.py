"""A product command failure must not destroy the terminal's login shell."""

import subprocess
from pathlib import Path

import pytest

ENTRY = (
    Path(__file__).resolve().parents[2] / "config/alacritty/launch-primary-shell.zsh"
)


@pytest.mark.parametrize("failed_entry", [False, True])
def test_terminal_remains_a_shell_without_frame(
    tmp_path: Path, failed_entry: bool
) -> None:
    command = tmp_path / "vc-test-failure"
    command.write_text("#!/bin/sh\nprintf 'workspace rejected\\n' >&2\nexit 2\n")
    command.chmod(0o700)
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path),
        "ZDOTDIR": str(tmp_path),
        "TERM": "dumb",
        "VIBECRAFTED_HOME": str(tmp_path / "state"),
    }
    result = subprocess.run(
        ["/bin/bash", str(ENTRY), *([str(command)] if failed_entry else [])],
        input="printf 'SHELL_READY\\n'; exit 0\n",
        text=True,
        capture_output=True,
        env=environment,
        cwd=tmp_path,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "SHELL_READY" in result.stdout
    if failed_entry:
        assert "workspace rejected" in result.stderr
        assert "exit 2" in result.stderr
    assert not (tmp_path / "state").exists()
