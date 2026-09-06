"""An installed public entry loads the helper deck of its OWN generation.

Observed red (Founder, 2026-09-06), from the framework source checkout::

    ~/.local/bin/vibecrafted resume codex --root <that same checkout>
    vc-resume: no TTY and no installed vibecrafted front door to open a
    terminal with.

`vc-start` opened a terminal from the very same directory. The asymmetry was
the whole bug: `cmd_start` handed `_ensure_helpers_loaded` the physical script
owner, while every other verb called it with no owner at all -- and the
ownerless path resolved `_repo_source_root` from the CURRENT DIRECTORY. Run
inside a checkout, the installed deck therefore sourced THAT CHECKOUT's helper
tree, `_vetcoders_vc_frame_loaded_root` became the checkout, and the front-door
lookup went asking the checkout for `bin/vibecrafted`, which no checkout has.

The contract proven here: which generation owns the helpers is decided by where
the running script PHYSICALLY lives, never by cwd and never by the caller's
environment. A direct source execution keeps its own deliberate route, and a
selected generation missing its own helper fails closed instead of borrowing
another tree's.

Unlike test_terminal_entry_escalation.py, these cases never source the shell
facade and never set `_vetcoders_vc_frame_loaded_root` by hand -- doing so would
pin the answer the deck is supposed to compute. The real launcher deck is
executed as host argv, which is the only surface where this defect is visible.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK = REPO_ROOT / "scripts" / "vibecrafted"
CORE = "vibecrafted-core/vibecrafted_core"
RUNTIME_TREE = REPO_ROOT / CORE / "runtime"
PRIMARY_SHELL = REPO_ROOT / "config" / "alacritty" / "launch-primary-shell.zsh"

# Which helper tree actually got sourced. Appended to the real facades, so the
# answer comes from the runtime's own load, not from a test-side guess.
OWNER_MARK = "GENERATION"
SOURCE_MARK = "SOURCE_CHECKOUT"


def _write(path: Path, body: str, *, executable: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    return path


def _mark_facade(runtime_root: Path, mark: str, log: Path) -> None:
    facade = runtime_root / "shell" / "vetcoders.sh"
    with facade.open("a", encoding="utf-8") as handle:
        handle.write(f'\nprintf "{mark}\\n" >> {str(log)!r}\n')


def _installed_generation(base: Path, log: Path, capture: Path) -> Path:
    """A physically installed generation: real deck, real helper tree, receipt."""
    generation = base / "generation"
    shutil.copy2(DECK, _ensure_dir(generation / "bin") / "vibecrafted")
    (generation / "bin" / "vibecrafted").chmod(0o755)
    shutil.copytree(RUNTIME_TREE, generation / CORE / "runtime")
    _mark_facade(generation / CORE / "runtime", OWNER_MARK, log)
    # The installer's receipt, and no .git: the boundary the shell layer already
    # draws between an installed payload and a development checkout.
    _write(generation / "VERSION", "4.3.0+fixture\n", executable=False)
    _write(
        generation / "runtime-manifest.json",
        json.dumps({"generation": "fixture"}) + "\n",
        executable=False,
    )
    # Engines must be real, executable, non-symlink files for the strict
    # resolvers in vc_frame.sh to accept them.
    for engine in ("vc-terminal", "vc-frame"):
        _write(generation / "libexec" / engine, "#!/bin/bash\nexit 0\n")
    _write(generation / "bin" / "vc-frame", "#!/bin/bash\nexit 0\n")
    _write(generation / "bin" / "vc-start", "#!/bin/bash\nexit 0\n")
    # The product terminal entry records the launch instead of opening a window.
    _write(
        generation / "bin" / "vc-terminal",
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"open({str(capture)!r}, 'w').write(json.dumps("
        "{'argv': sys.argv[1:], 'cwd': os.getcwd(),"
        " 'boundary': os.environ.get('VIBECRAFTED_TERMINAL_ENTRY', '')}))\n"
        "sys.exit(0)\n",
    )
    return generation


def _source_checkout(base: Path, log: Path) -> Path:
    """A framework source checkout, shaped like the real one.

    Faithful on the detail that made the defect reachable: the checkout has no
    top-level runtime/, so the helper the deck used to pick came from
    vibecrafted-core/vibecrafted_core/runtime -- a tree that loads cleanly and
    only fails later, at the front door.
    """
    checkout = base / "framework-source"
    shutil.copy2(DECK, _ensure_dir(checkout / "scripts") / "vibecrafted")
    (checkout / "scripts" / "vibecrafted").chmod(0o755)
    shutil.copytree(RUNTIME_TREE, checkout / CORE / "runtime")
    _mark_facade(checkout / CORE / "runtime", SOURCE_MARK, log)
    _write(checkout / "VERSION", "9.9.9-source\n", executable=False)
    _write(checkout / "skills" / "dou" / "SKILL.md", "placeholder\n", executable=False)
    _ensure_dir(checkout / ".git")
    return checkout


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def world(tmp_path: Path) -> dict[str, Path]:
    log = tmp_path / "sourced.log"
    capture = tmp_path / "terminal-launch.json"
    home = _ensure_dir(tmp_path / "home")
    _write(
        home / ".config" / "vibecrafted" / "vc-terminal" / "launch-primary-shell.zsh",
        PRIMARY_SHELL.read_text(encoding="utf-8"),
    )
    return {
        "base": tmp_path,
        "log": log,
        "capture": capture,
        "home": home,
        "project": _ensure_dir(tmp_path / "mlx-batch-runner"),
        "generation": _installed_generation(tmp_path, log, capture),
        "checkout": _source_checkout(tmp_path, log),
    }


def _run(
    world: dict[str, Path],
    entry: Path,
    argv: list[str],
    *,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
    expect_launch: bool = True,
    shell: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict | None]:
    env = os.environ.copy()
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIBECRAFTED_ROOT",
        "VIBECRAFTED_RUNTIME_ROOT",
        "VIBECRAFTED_PREFER_REPO_SPAWN",
        "VIBECRAFTED_TERMINAL_ENTRY",
        "SPAWN_ROOT",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ_PANE_ID",
        "ZELLIJ_SESSION_NAME",
    ):
        env.pop(key, None)
    env["HOME"] = str(world["home"])
    env["VIBECRAFTED_HOME"] = str(world["home"] / ".vibecrafted")
    env["XDG_CONFIG_HOME"] = str(world["home"] / ".config")
    env.update(extra_env or {})

    command = [str(entry), *argv]
    if shell is not None:
        # The caller's login shell must not change who owns the helpers.
        quoted = " ".join(f"'{part}'" for part in command)
        command = (
            ["zsh", "-f", "-c", quoted]
            if shell == "zsh"
            else ["bash", "--noprofile", "--norc", "-c", quoted]
        )

    result = subprocess.run(
        command,
        check=False,
        cwd=str(cwd or world["checkout"]),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    capture = world["capture"]
    deadline = time.monotonic() + (10.0 if expect_launch else 1.5)
    while not capture.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    launch = (
        json.loads(capture.read_text(encoding="utf-8")) if capture.exists() else None
    )
    return result, launch


def _sourced(world: dict[str, Path]) -> list[str]:
    log = world["log"]
    if not log.exists():
        return []
    return [
        line.strip()
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _hosted_argv(launch: dict) -> list[str]:
    argv = launch["argv"]
    return argv[argv.index("-e") + 1 :]


def _working_directory(launch: dict) -> Path:
    argv = launch["argv"]
    return Path(argv[argv.index("--working-directory") + 1]).resolve()


# --------------------------------------------------------------------------
# The reported red
# --------------------------------------------------------------------------


def test_installed_resume_inside_a_checkout_uses_its_own_generation(
    world: dict[str, Path],
) -> None:
    """The reported red: run from a checkout, resume kept the checkout's truth."""
    project = world["project"]
    result, launch = _run(
        world,
        world["generation"] / "bin" / "vibecrafted",
        ["resume", "codex", "--root", str(project)],
    )

    assert result.returncode == 0, result.stderr
    assert launch is not None, f"no terminal was opened: {result.stderr}"

    # Only the selected generation's helper tree was loaded.
    assert _sourced(world) == [OWNER_MARK], result.stderr

    # Host argv names the selected generation's front door, never the checkout's.
    hosted = _hosted_argv(launch)
    assert hosted[0].endswith("launch-primary-shell.zsh")
    assert hosted[1] == str(world["generation"] / "bin" / "vibecrafted")

    # Project identity is independent, and --root survives exactly.
    assert _working_directory(launch) == project.resolve()
    assert hosted[2:] == ["resume", "codex", "--root", str(project)]
    assert launch["boundary"] == "1"


def test_no_foreign_helper_is_sourced_from_the_surrounding_checkout(
    world: dict[str, Path],
) -> None:
    result, launch = _run(
        world,
        world["generation"] / "bin" / "vibecrafted",
        ["resume", "codex", "--root", str(world["project"])],
    )

    assert launch is not None, result.stderr
    assert SOURCE_MARK not in _sourced(world), (
        "the surrounding checkout's helper tree was sourced by an installed entry"
    )


def test_ambient_roots_cannot_select_another_generation(
    world: dict[str, Path],
) -> None:
    """Caller-controlled environment is not authority over the physical owner."""
    checkout = world["checkout"]
    result, launch = _run(
        world,
        world["generation"] / "bin" / "vibecrafted",
        ["resume", "codex", "--root", str(world["project"])],
        extra_env={
            "VIBECRAFTED_ROOT": str(checkout),
            "VIBECRAFTED_RUNTIME_ROOT": str(checkout),
            "VIBECRAFTED_PREFER_REPO_SPAWN": "1",
        },
    )

    assert launch is not None, result.stderr
    assert _sourced(world) == [OWNER_MARK]
    assert _hosted_argv(launch)[1] == str(world["generation"] / "bin" / "vibecrafted")


def test_normal_cwd_outside_any_checkout_still_uses_the_generation(
    world: dict[str, Path],
) -> None:
    project = world["project"]
    result, launch = _run(
        world,
        world["generation"] / "bin" / "vibecrafted",
        ["resume", "codex"],
        cwd=project,
    )

    assert launch is not None, result.stderr
    assert _sourced(world) == [OWNER_MARK]
    assert _working_directory(launch) == project.resolve()


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_caller_shell_does_not_change_the_owner(
    world: dict[str, Path], shell: str
) -> None:
    result, launch = _run(
        world,
        world["generation"] / "bin" / "vibecrafted",
        ["resume", "codex", "--root", str(world["project"])],
        shell=shell,
    )

    assert launch is not None, result.stderr
    assert _sourced(world) == [OWNER_MARK]
    assert _hosted_argv(launch)[1] == str(world["generation"] / "bin" / "vibecrafted")


def test_start_keeps_choosing_its_own_generation(world: dict[str, Path]) -> None:
    """R5b regression guard: start already passed the owner and must keep doing so."""
    result, _ = _run(
        world,
        world["generation"] / "bin" / "vibecrafted",
        ["start"],
        expect_launch=False,
    )

    # The fixture generation carries no python runtime, so start stops there --
    # but only AFTER loading its own helpers, which is the property under test.
    assert _sourced(world) == [OWNER_MARK], result.stderr


# --------------------------------------------------------------------------
# Fail closed, and the deliberate source route
# --------------------------------------------------------------------------


def test_missing_generation_helper_fails_closed(world: dict[str, Path]) -> None:
    """A broken install is reported, never rescued by a neighbouring tree."""
    facade = world["generation"] / CORE / "runtime" / "shell" / "vetcoders.sh"
    facade.unlink()

    result, launch = _run(
        world,
        world["generation"] / "bin" / "vibecrafted",
        ["resume", "codex", "--root", str(world["project"])],
        expect_launch=False,
    )

    assert result.returncode != 0
    assert launch is None, "a terminal was opened on a broken install"
    assert _sourced(world) == [], "another generation's helpers were borrowed"
    assert "selected runtime shell missing" in result.stderr
    assert str(facade) in result.stderr


def test_direct_source_execution_keeps_its_own_route(world: dict[str, Path]) -> None:
    """Explicit development execution stays deliberate; it never hijacks nor is hijacked."""
    checkout = world["checkout"]
    result, launch = _run(
        world,
        checkout / "scripts" / "vibecrafted",
        ["resume", "codex", "--root", str(world["project"])],
        expect_launch=False,
    )

    # The checkout answers with its OWN helpers -- that is the point of running
    # it directly -- and stops honestly, having no installed front door to open.
    assert _sourced(world) == [SOURCE_MARK]
    assert OWNER_MARK not in _sourced(world)
    assert launch is None
    assert "no installed vibecrafted front door" in result.stderr
