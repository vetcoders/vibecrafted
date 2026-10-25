"""A live Frame keeps helper code and imports on its inherited generation."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

PANE = (
    Path(__file__).resolve().parents[2]
    / "vibecrafted-core/vibecrafted_core/config/vc-frame/pane-python"
)
CONFIG = Path("vibecrafted-core/vibecrafted_core/config/vc-frame")


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def cohort(tmp_path: Path):
    home = tmp_path / "home"
    shared = home / ".config/vibecrafted/vc-frame"
    generation = tmp_path / "old runtime"
    core = generation / "vibecrafted-core"
    _write(core / "vibecrafted_core/__init__.py", "")
    _write(core / "vibecrafted_core/workspace_catalog.py", "legacy = 'old'\n")
    py = _write(
        generation / "bin/python3",
        "#!/bin/bash\n"
        f"export PYTHONPATH={shlex.quote(str(core))}\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n',
    )
    old = _write(
        generation / CONFIG / "vc-agent-workshop.py",
        "from vibecrafted_core.workspace_catalog import legacy\n"
        "import json, sys\n"
        "print(json.dumps({'cohort': legacy, 'file': __file__, 'args': sys.argv[1:]}))\n",
    )
    shared_helper = _write(
        shared / old.name,
        "from vibecrafted_core.workspace_catalog import resolve_project_destination\n",
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "SHELL": "/bin/false",
        "VIBECRAFTED_HOME": str(tmp_path / "state"),
        "VIBECRAFTED_PYTHON": str(py),
        # A newer selected root must not override the live interpreter pin.
        "VIBECRAFTED_RUNTIME_ROOT": str(tmp_path / "new runtime"),
        "VIBECRAFTED_ROOT": str(tmp_path / "new runtime"),
    }
    env.pop("PYTHONHOME", None)
    env.pop("XDG_CONFIG_HOME", None)
    return generation, old, shared_helper, env


def _run(helper: Path, env: dict[str, str]):
    return subprocess.run(
        ["bash", str(PANE), str(helper), "launcher", "space argument"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_shared_new_helper_runs_old_pinned_cohort(cohort) -> None:
    _, old, shared, env = cohort
    result = _run(shared, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "cohort": "old",
        "file": str(old),
        "args": ["launcher", "space argument"],
    }


def test_same_generation_helper_keeps_its_exact_path(cohort) -> None:
    _, old, _, env = cohort
    result = _run(old, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["file"] == str(old)


def test_matching_shared_helper_still_runs_generation_copy(cohort) -> None:
    _, old, shared, env = cohort
    shared.write_text(old.read_text())
    result = _run(shared, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["file"] == str(old)


def test_launchpad_uses_the_same_generation_boundary(cohort) -> None:
    _, old, shared, env = cohort
    launchpad = _write(old.with_name("vc-start-here.py"), old.read_text())
    shared_launchpad = _write(shared.with_name(launchpad.name), shared.read_text())
    result = _run(shared_launchpad, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["file"] == str(launchpad)


def test_runtime_root_fallback_selects_matching_helper(cohort) -> None:
    generation, old, shared, env = cohort
    env.pop("VIBECRAFTED_PYTHON")
    env["VIBECRAFTED_RUNTIME_ROOT"] = str(generation)
    result = _run(shared, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["file"] == str(old)


def test_private_helper_with_product_name_is_not_remapped(cohort, tmp_path) -> None:
    _, _, _, env = cohort
    private = _write(
        tmp_path / "checkout/config/vc-frame/vc-agent-workshop.py",
        "import json\nprint(json.dumps({'file': __file__}))\n",
    )
    result = _run(private, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["file"] == str(private)


def test_unknown_helper_inside_shared_config_remains_founder_owned(cohort) -> None:
    _, _, shared, env = cohort
    custom = _write(
        shared.parent / "custom.py",
        "import json, sys\n"
        "print(json.dumps({'file': __file__, 'args': sys.argv[1:]}))\n",
    )
    result = _run(custom, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "file": str(custom),
        "args": ["launcher", "space argument"],
    }


def test_shared_directory_symlink_does_not_reclassify_checkout_path(cohort, tmp_path):
    _, _, shared, env = cohort
    private = _write(
        tmp_path / "checkout/vc-frame/vc-agent-workshop.py",
        "import json\nprint(json.dumps({'file': __file__}))\n",
    )
    shared.unlink()
    shared.parent.rmdir()
    shared.parent.symlink_to(private.parent, target_is_directory=True)
    result = _run(private, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["file"] == str(private)


@pytest.mark.parametrize("escape", ["missing", "file_symlink", "directory_symlink"])
def test_managed_helper_never_falls_back_to_shared_or_escaping_code(
    cohort, tmp_path, escape
) -> None:
    _, old, shared, env = cohort
    # Make the newer shared helper runnable, so failure cannot be attributed to
    # an incidental ImportError instead of a closed generation boundary.
    shared.write_text("print('WRONG SHARED COHORT')\n")
    old.unlink()
    if escape == "file_symlink":
        old.symlink_to(_write(tmp_path / "foreign.py", "print('ESCAPED')\n"))
    elif escape == "directory_symlink":
        directory = old.parent
        directory.rmdir()
        foreign = tmp_path / "foreign"
        _write(foreign / old.name, "print('ESCAPED')\n")
        directory.symlink_to(foreign, target_is_directory=True)
    result = _run(shared, env)
    assert result.returncode != 0
    assert "pinned generation" in result.stderr
    assert not result.stdout


def test_xdg_managed_helper_uses_pinned_generation(cohort, tmp_path) -> None:
    _, old, shared, env = cohort
    config = tmp_path / "xdg config"
    selected = _write(config / "vibecrafted/vc-frame" / shared.name, shared.read_text())
    env["XDG_CONFIG_HOME"] = str(config)
    result = _run(selected, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["file"] == str(old)


def test_unrelated_interpreter_cannot_select_newer_root(cohort, tmp_path) -> None:
    _, _, shared, env = cohort
    shared.write_text("print('WRONG SHARED COHORT')\n")
    private = _write(tmp_path / "private-python", "#!/bin/sh\nexit 0\n")
    env["VIBECRAFTED_PYTHON"] = str(private)
    result = _run(shared, env)
    assert result.returncode != 0
    assert "pinned generation" in result.stderr
    assert not result.stdout
