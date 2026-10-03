"""Public vc-* launchers run on an interpreter the product owns, never host python3.

A launcher that starts with ``#!/usr/bin/env python3`` takes whatever ``python3``
comes first on the caller's PATH. The installer's ``~/.local/bin`` shim even
strips the generation ``bin`` from PATH, so in any shell without a personal
profile that is macOS ``/usr/bin/python3`` 3.9.6, which cannot import
``vibecrafted_core`` (``tomllib``). These tests put a fake host ``python3`` first
on PATH and require it never to run.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BIN = REPO / "bin"
CORE_IMPORT = "from vibecrafted_core"
HOST_MARKER = "HOST_PYTHON_SELECTED"


def _python_launchers() -> list[Path]:
    return sorted(
        path
        for path in BIN.iterdir()
        if path.is_file()
        and CORE_IMPORT in path.read_text(encoding="utf-8", errors="ignore")
    )


LAUNCHERS = _python_launchers()
# Launchers whose single import the generation stub below can stand in for.
# vc-sandbox imports several sandbox classes; its prelude is pinned byte-for-byte
# with the others, so one behavioural proof covers it.
STUBBED_LAUNCHERS = [
    path
    for path in LAUNCHERS
    if "from vibecrafted_core.wrappers import" in path.read_text(encoding="utf-8")
    or "from vibecrafted_core.paste import" in path.read_text(encoding="utf-8")
]


def _prelude(text: str) -> str:
    head, sep, _ = text.partition('\n":"""\n')
    assert sep, "launcher has no closed sh prelude"
    return head + sep


def test_every_python_launcher_carries_the_same_owned_interpreter_prelude() -> None:
    assert len(LAUNCHERS) == 24
    reference = _prelude(LAUNCHERS[0].read_text(encoding="utf-8"))
    assert reference.startswith('#!/bin/sh\n"""":\n')
    for launcher in LAUNCHERS:
        text = launcher.read_text(encoding="utf-8")
        assert _prelude(text) == reference, launcher.name
        assert "#!/usr/bin/env python3" not in text, launcher.name


def test_no_bin_entry_selects_python_from_path() -> None:
    offenders = [
        path.name
        for path in sorted(BIN.iterdir())
        if path.is_file()
        and path.read_bytes()[:64].split(b"\n", 1)[0].startswith(b"#!")
        and b"python" in path.read_bytes()[:64].split(b"\n", 1)[0]
    ]
    assert offenders == []


def _write_executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _generation(
    tmp_path: Path, launcher_text: str, name: str, *, sibling: bool
) -> Path:
    """A generation-shaped tree whose wrappers module only reports who ran it."""
    generation = tmp_path / "generation"
    core = generation / "vibecrafted-core" / "vibecrafted_core"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    reporter = (
        "import sys\n"
        "def __getattr__(name):\n"
        "    def main(*_args, **_kwargs):\n"
        "        print(f'OWNED name={name} exe={sys.executable} args={sys.argv[1:]}')\n"
        "        return 0\n"
        "    return main\n"
    )
    for module in ("wrappers.py", "paste.py"):
        (core / module).write_text(reporter, encoding="utf-8")
    target = _write_executable(generation / "bin" / name, launcher_text)
    if sibling:
        _owned_python(generation / "bin" / "python3")
    return target


def _owned_python(path: Path) -> Path:
    return _write_executable(
        path, f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n'
    )


def _host_path(tmp_path: Path) -> str:
    fake = tmp_path / "host-bin"
    _write_executable(
        fake / "python3", f"#!/bin/sh\nprintf '{HOST_MARKER}\\n'\nexit 99\n"
    )
    return f"{fake}:/usr/bin:/bin"


def _run(
    argv: list[str], tmp_path: Path, **extra: str
) -> subprocess.CompletedProcess[str]:
    env = {"HOME": str(tmp_path), "PATH": _host_path(tmp_path), **extra}
    return subprocess.run(
        argv, capture_output=True, text=True, env=env, timeout=30, check=False
    )


def _assert_owned(result: subprocess.CompletedProcess[str]) -> None:
    assert HOST_MARKER not in result.stdout + result.stderr
    assert result.returncode == 0, result.stderr
    assert f"exe={sys.executable}" in result.stdout
    assert "args=['probe']" in result.stdout


@pytest.mark.parametrize("launcher", STUBBED_LAUNCHERS, ids=lambda path: path.name)
def test_launcher_runs_on_the_generation_python_not_host(
    tmp_path: Path, launcher: Path
) -> None:
    target = _generation(
        tmp_path, launcher.read_text(encoding="utf-8"), launcher.name, sibling=True
    )
    _assert_owned(_run([str(target), "probe"], tmp_path))


def test_launcher_reached_through_a_symlink_still_finds_its_generation(
    tmp_path: Path,
) -> None:
    launcher = BIN / "vc-audit"
    target = _generation(
        tmp_path, launcher.read_text(encoding="utf-8"), launcher.name, sibling=True
    )
    link = tmp_path / "elsewhere" / "vc-audit"
    link.parent.mkdir()
    link.symlink_to(target)
    _assert_owned(_run([str(link), "probe"], tmp_path))


def test_installer_shaped_shim_reaches_the_owned_python(tmp_path: Path) -> None:
    launcher = BIN / "vc-audit"
    target = _generation(
        tmp_path, launcher.read_text(encoding="utf-8"), launcher.name, sibling=True
    )
    # Same shape as the ~/.local/bin shim: export the owner, strip generation
    # bin directories from PATH, exec the generation launcher.
    shim = _write_executable(
        tmp_path / "local-bin" / "vc-audit",
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"export VIBECRAFTED_PYTHON={shlex.quote(str(target.parent / 'python3'))}\n"
        'PATH="${PATH//' + str(target.parent) + ':/}"\n'
        f'exec {shlex.quote(str(target))} "$@"\n',
    )
    _assert_owned(_run([str(shim), "probe"], tmp_path))


def test_launcher_without_sibling_uses_vibecrafted_python(tmp_path: Path) -> None:
    launcher = BIN / "vc-audit"
    target = _generation(
        tmp_path, launcher.read_text(encoding="utf-8"), launcher.name, sibling=False
    )
    owner = _owned_python(tmp_path / "owner" / "python3")
    _assert_owned(_run([str(target), "probe"], tmp_path, VIBECRAFTED_PYTHON=str(owner)))


def test_launcher_in_a_source_checkout_uses_the_workspace_venv(tmp_path: Path) -> None:
    launcher = BIN / "vc-audit"
    target = _generation(
        tmp_path, launcher.read_text(encoding="utf-8"), launcher.name, sibling=False
    )
    # A checkout has no bin/python3; its owned interpreter is the uv workspace
    # venv at the repository root (scripts/project-python, core.sh).
    _owned_python(target.parent.parent / ".venv" / "bin" / "python3")
    _assert_owned(_run([str(target), "probe"], tmp_path))


def test_launcher_without_an_owned_interpreter_refuses_instead_of_host(
    tmp_path: Path,
) -> None:
    launcher = BIN / "vc-audit"
    target = _generation(
        tmp_path, launcher.read_text(encoding="utf-8"), launcher.name, sibling=False
    )
    result = _run([str(target), "probe"], tmp_path)
    assert result.returncode == 127
    assert "refusing host python3" in result.stderr
    assert HOST_MARKER not in result.stdout + result.stderr
    assert "OWNED" not in result.stdout


def test_harness_detects_a_launcher_that_takes_python_from_path(tmp_path: Path) -> None:
    """Control run: the old shebang must be caught, or the tests above prove nothing."""
    launcher = BIN / "vc-audit"
    old = (
        "#!/usr/bin/env python3\n"
        + launcher.read_text(encoding="utf-8").split('\n":"""\n', 1)[1]
    )
    target = _generation(tmp_path, old, launcher.name, sibling=True)
    result = _run([str(target), "probe"], tmp_path)
    assert HOST_MARKER in result.stdout
    assert result.returncode == 99


def test_shell_gate_checks_only_the_launcher_prelude() -> None:
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck is unavailable")
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/check_shell.py"),
            "--require-shellcheck",
            *(str(path) for path in LAUNCHERS),
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
