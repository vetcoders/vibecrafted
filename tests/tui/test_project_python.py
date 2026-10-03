"""Exercise the project interpreter dispatcher without ambient host discovery."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def dispatcher(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "project with spaces"
    runner = root / "scripts" / "project-python"
    runner.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "project-python", runner)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Only the dispatcher's shell/dirname prerequisites; no host Python or uv.
    for name in ("bash", "dirname"):
        source = shutil.which(name)
        assert source
        (bin_dir / name).symlink_to(source)
    return runner, {"PATH": str(bin_dir)}


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _run(
    dispatcher: tuple[Path, dict[str, str]], *args: str, **environment: str
) -> subprocess.CompletedProcess[str]:
    runner, env = dispatcher
    return subprocess.run(
        [str(runner), *args],
        cwd=runner.parents[1],
        env={**env, **environment},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize("selection", ["absolute", "relative", "command"])
def test_project_python_explicit_precedes_venv_and_path(
    dispatcher: tuple[Path, dict[str, str]], selection: str
) -> None:
    runner, env = dispatcher
    root = runner.parents[1]
    # A real supported interpreter through a path containing spaces. The
    # wrappers fail loudly if default discovery is attempted, even for probing.
    selected = root / "selected environment" / "python"
    _executable(selected, f'exec {shlex.quote(sys.executable)} "$@"\n')
    trap = root / "fallback-used"
    for path in (root / ".venv/bin/python3", Path(env["PATH"]) / "python3.14"):
        _executable(path, f"printf used > {shlex.quote(str(trap))}\nexit 90\n")
    value = str(selected)
    if selection == "relative":
        value = str(selected.relative_to(root))
    elif selection == "command":
        (Path(env["PATH"]) / "chosen-python").symlink_to(selected)
        value = "chosen-python"
    args = ["space value", "", "żółć\r\nline", "$(false); *"]
    result = _run(
        dispatcher,
        "-c",
        "import json,sys; print(json.dumps(sys.argv[1:]))",
        *args,
        PYTHON=value,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == args
    assert not trap.exists()


@pytest.mark.parametrize(
    "selection", ["missing", "empty", "directory", "not-executable", "flags", "builtin"]
)
def test_project_python_invalid_selection_never_falls_back(
    dispatcher: tuple[Path, dict[str, str]], selection: str
) -> None:
    runner, env = dispatcher
    root = runner.parents[1]
    trap = root / "fallback-used"
    for path in (
        root / ".venv/bin/python3",
        Path(env["PATH"]) / "python3.14",
        Path(env["PATH"]) / "uv",
    ):
        _executable(path, f"printf used > {shlex.quote(str(trap))}\nexit 0\n")
    nonexec = root / "nonexec"
    nonexec.write_text("not executable", encoding="utf-8")
    value = {
        "missing": str(root / "absent"),
        "empty": "",
        "directory": str(root),
        "not-executable": str(nonexec),
        "flags": f"{sys.executable} -I",
        "builtin": "exit",
    }[selection]
    result = _run(dispatcher, "-c", "raise SystemExit(0)", PYTHON=value)
    assert result.returncode == 2
    assert "explicit PYTHON is not an executable" in result.stderr
    assert not trap.exists()


@pytest.mark.parametrize("failure", ["old-version", "missing-tomllib"])
def test_project_python_rejects_unsupported_explicit_interpreter(
    dispatcher: tuple[Path, dict[str, str]], failure: str
) -> None:
    runner, _ = dispatcher
    root = runner.parents[1]
    # Run the real compatibility probe with a simulated unsupported runtime.
    setup = (
        "sys.version_info = (3, 10)"
        if failure == "old-version"
        else "sys.modules['tomllib'] = None"
    )
    code = f"import sys; {setup}; exec(sys.argv[2])"
    selected = _executable(
        root / "unsupported",
        f'exec {shlex.quote(sys.executable)} -c {shlex.quote(code)} "$@"\n',
    )
    fallback = root / ".venv/bin/python3"
    _executable(fallback, f'exec {shlex.quote(sys.executable)} "$@"\n')
    result = _run(
        dispatcher, "-c", "print('unexpected fallback')", PYTHON=str(selected)
    )
    assert result.returncode == 2
    assert (
        "explicit PYTHON must support Python >=3.11 with stdlib tomllib"
        in result.stderr
    )
    assert not result.stdout


@pytest.mark.parametrize(
    "selection", ["absolute", "relative", "symlink", "hardlink", "command"]
)
def test_project_python_rejects_self_recursion(
    dispatcher: tuple[Path, dict[str, str]], selection: str
) -> None:
    runner, env = dispatcher
    value = str(runner)
    if selection == "relative":
        value = "scripts/../scripts/project-python"
    elif selection in {"symlink", "hardlink", "command"}:
        alias = Path(env["PATH"]) / "recursive-python"
        if selection == "hardlink":
            os.link(runner, alias)
        else:
            alias.symlink_to(runner)
        value = "recursive-python" if selection == "command" else str(alias)
    result = _run(dispatcher, "-c", "print('must not run')", PYTHON=value)
    assert result.returncode == 2
    assert "refusing recursion" in result.stderr
    assert not result.stdout


@pytest.mark.parametrize("exit_code", [0, 7, 28, 127])
def test_project_python_preserves_child_exit(
    dispatcher: tuple[Path, dict[str, str]], exit_code: int
) -> None:
    result = _run(
        dispatcher, "-c", f"raise SystemExit({exit_code})", PYTHON=sys.executable
    )
    assert result.returncode == exit_code


@pytest.mark.parametrize("choice", ["venv", "named", "generic", "uv", "none"])
def test_project_python_retains_default_discovery(
    dispatcher: tuple[Path, dict[str, str]], choice: str
) -> None:
    runner, env = dispatcher
    root = runner.parents[1]
    bin_dir = Path(env["PATH"])
    # The candidate reports its identity only after accepting the probe.
    for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3"):
        _executable(bin_dir / name, "exit 1\n")
    if choice in {"venv", "named", "generic"}:
        target = {
            "venv": root / ".venv/bin/python3",
            "named": bin_dir / "python3.12",
            "generic": bin_dir / "python3",
        }[choice]
        _executable(
            target, f'if [[ "$1" == -c ]]; then exit 0; fi\nprintf "%s\\n" {choice}\n'
        )
    if choice != "none":
        _executable(bin_dir / "uv", 'printf "%s\\n" "$@"\nexit 19\n')
    result = _run(dispatcher, "script with spaces.py", "argument")
    if choice == "none":
        assert result.returncode == 2
        assert "Python >=3.11" in result.stderr
    elif choice == "uv":
        assert result.returncode == 19
        assert result.stdout.splitlines() == [
            "run",
            "--project",
            str(root),
            "--no-sync",
            "python",
            "script with spaces.py",
            "argument",
        ]
    else:
        assert result.returncode == 0
        assert result.stdout.strip() == choice
