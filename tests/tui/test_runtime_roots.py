"""Hermetic subprocess contract for tests that intentionally use login shells."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path


def test_login_shell_reasserts_only_synthetic_runtime_roots(
    tmp_path: Path,
    hermetic_login_shell: Callable[[dict[str, str], str], str],
) -> None:
    synthetic_home = tmp_path / "synthetic-home"
    operator_home = tmp_path / "forbidden-operator-home"
    effective_environment = tmp_path / "effective-environment.txt"
    synthetic_home.mkdir()
    operator_home.mkdir()

    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(synthetic_home),
            "PATH": "/usr/bin:/bin",
            "VIBECRAFTED_HOME": str(synthetic_home / ".vibecrafted"),
            "VIBECRAFTED_RUNTIME_HOME": str(
                synthetic_home / ".local/share/vibecrafted"
            ),
            "VIBECRAFTED_RUNTIME_BIN": str(
                synthetic_home / ".local/share/vibecrafted/bin"
            ),
        }
    )
    environment["FORBIDDEN_OPERATOR_HOME"] = str(operator_home)
    script = hermetic_login_shell(
        environment,
        (
            'printf "HOME=%s\\nRUNTIME_HOME=%s\\nRUNTIME_BIN=%s\\n" '
            '"$HOME" "$VIBECRAFTED_RUNTIME_HOME" "$VIBECRAFTED_RUNTIME_BIN" '
            ">"
            f' "{effective_environment}"'
        ),
    )

    subprocess.run(
        ["bash", "-lc", script],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    effective = effective_environment.read_text(encoding="utf-8")
    assert f"HOME={synthetic_home}" in effective
    assert f"RUNTIME_HOME={synthetic_home}/.local/share/vibecrafted" in effective
    assert f"RUNTIME_BIN={synthetic_home}/.local/share/vibecrafted/bin" in effective
    assert str(operator_home) not in effective
