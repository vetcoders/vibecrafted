"""Public VC entries open a terminal instead of refusing when there is no TTY.

P0 (Founder, 2026-09-06): from an agent shell,

    cd ~/Libraxis/mlx-batch-runner && vc-resume codex
    cd ~/Libraxis/mlx-batch-runner && vc-start

both died. `vc-resume` assembled a 48h AICX pack, then called three unrelated
live vc-frame sessions "ambiguous", left VIBECRAFTED_OPERATOR_SESSION unset and
refused to downgrade. `vc-start` reached vc-frame directly and hit its strict
stdin-is-not-a-TTY guard.

The contract proven here: vc-frame keeps refusing pipes (it is internal), but a
PUBLIC entry owes the operator a visible terminal. The escalation reuses the one
owner Vibecrafted.app already uses -- vc-terminal -e <launch-primary-shell.zsh>
<front door> [argv] -- with the exact cwd and argv preserved, and it happens
before any AICX/provider side effect so nothing is launched twice.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SHELL_SH = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "shell"
    / "vetcoders.sh"
)
PRIMARY_SHELL = REPO_ROOT / "config" / "alacritty" / "launch-primary-shell.zsh"


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _fake_generation(root: Path, capture: Path) -> Path:
    """A generation tree strict enough for the real resolvers to accept."""
    generation = root / "generation"
    # Engine must be a real, executable, non-symlink file.
    _write(generation / "libexec" / "vc-terminal", "#!/bin/bash\nexit 0\n")
    _write(generation / "libexec" / "vc-frame", "#!/bin/bash\nexit 0\n")
    # Product terminal entry records the launch instead of opening a window.
    _write(
        generation / "bin" / "vc-terminal",
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"open({str(capture)!r}, 'w').write(json.dumps("
        "{'argv': sys.argv[1:], 'cwd': os.getcwd(),"
        " 'boundary': os.environ.get('VIBECRAFTED_TERMINAL_ENTRY', '')}))\n",
    )
    _write(generation / "bin" / "vc-start", "#!/bin/bash\nexit 0\n")
    _write(generation / "bin" / "vibecrafted", "#!/bin/bash\nexit 0\n")
    _write(
        generation / "config" / "alacritty" / "launch-primary-shell.zsh",
        PRIMARY_SHELL.read_text(encoding="utf-8"),
    )
    return generation


def _run_entry(
    tmp_path: Path,
    invocation: str,
    *,
    project: str = "mlx-batch-runner",
    extra_env: dict[str, str] | None = None,
    with_generation: bool = True,
    expect_launch: bool = True,
) -> tuple[subprocess.CompletedProcess[str], dict | None]:
    capture = tmp_path / "terminal-launch.json"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    project_dir = tmp_path / project
    project_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    for key in (
        "VIBECRAFTED_OPERATOR_SESSION",
        "VIBECRAFTED_TERMINAL_ENTRY",
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ",
        "ZELLIJ_PANE_ID",
        "ZELLIJ_SESSION_NAME",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["TEST_AICX_CAPTURE"] = str(tmp_path / "aicx-called.txt")
    env.update(extra_env or {})

    lines = [f'source "{SHELL_SH}"']
    if with_generation:
        generation = _fake_generation(tmp_path, capture)
        # The loaded-root variable is the runtime's own generation pin; setting
        # it points every product resolver at the fixture instead of the host.
        lines.append(f'_vetcoders_vc_frame_loaded_root="{generation}"')
    else:
        lines.append(f'_vetcoders_vc_frame_loaded_root="{tmp_path / "empty"}"')
    lines += [
        # Any AICX or provider side effect before the terminal is admitted is a
        # duplicate launch waiting to happen.
        (
            "_vetcoders_aicx_resume_fallback() { printf 'called\\n' "
            "> \"$TEST_AICX_CAPTURE\"; printf 'MODE=new_session\\n'; }"
        ),
        invocation,
    ]

    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", "\n".join(lines)],
        check=False,
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # The host is launched in the background on purpose: a non-interactive
    # caller must not block until the operator closes the window. Give that
    # child a bounded moment to land instead of racing it.
    deadline = time.monotonic() + (10.0 if expect_launch else 1.5)
    while not capture.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    launch = (
        json.loads(capture.read_text(encoding="utf-8")) if capture.exists() else None
    )
    return result, launch


def test_bare_resume_without_tty_opens_terminal_on_this_project(
    tmp_path: Path,
) -> None:
    """The reported P0: bare resume must open a terminal, not refuse."""
    result, launch = _run_entry(tmp_path, "vc-resume codex")

    assert result.returncode == 0, result.stderr
    assert launch is not None, f"no terminal was opened: {result.stderr}"

    argv = launch["argv"]
    # Exact cwd: the session belongs to the project the operator ran this in.
    assert "--working-directory" in argv
    working_directory = argv[argv.index("--working-directory") + 1]
    assert (
        Path(working_directory).resolve() == (tmp_path / "mlx-batch-runner").resolve()
    )

    # The existing owner contract, not a private launcher.
    assert "-e" in argv
    hosted = argv[argv.index("-e") + 1 :]
    assert hosted[0].endswith("launch-primary-shell.zsh")
    assert hosted[1].endswith("/bin/vibecrafted")
    assert hosted[2:] == ["resume", "codex"]

    # The child re-enters the same entry; the boundary must ride with it.
    assert launch["boundary"] == "1"

    # Nothing may be launched twice: no AICX pack in the escalating parent.
    assert not (tmp_path / "aicx-called.txt").exists()
    assert "refusing to downgrade" not in result.stderr


def test_bare_start_without_tty_opens_terminal_with_its_front_door(
    tmp_path: Path,
) -> None:
    result, launch = _run_entry(tmp_path, "vc-start")

    assert result.returncode == 0, result.stderr
    assert launch is not None, f"no terminal was opened: {result.stderr}"
    hosted = launch["argv"][launch["argv"].index("-e") + 1 :]
    assert hosted[0].endswith("launch-primary-shell.zsh")
    assert hosted[1].endswith("/bin/vc-start")


def test_start_preserves_exact_argv_including_quoting(tmp_path: Path) -> None:
    """argv is preserved verbatim -- a spaced argument stays one argument."""
    result, launch = _run_entry(
        tmp_path, "vc-start " + shlex.quote("two words") + " --flag=a b"
    )

    assert result.returncode == 0, result.stderr
    assert launch is not None
    hosted = launch["argv"][launch["argv"].index("-e") + 1 :]
    assert hosted[2:] == ["two words", "--flag=a", "b"]


def test_unrelated_live_sessions_do_not_block_the_project(tmp_path: Path) -> None:
    """Global sessions elsewhere are not a claim on this repository."""
    listing = tmp_path / "sessions.txt"
    listing.write_text(
        "Live runs [Created]\nNeeds attention [Created]\nhost-a [Created]\n",
        encoding="utf-8",
    )
    result, launch = _run_entry(
        tmp_path,
        "vc-resume codex",
        extra_env={"TEST_SESSION_LISTING": str(listing)},
    )

    assert result.returncode == 0, result.stderr
    assert launch is not None, f"ambiguity still blocked the project: {result.stderr}"
    assert "refusing to downgrade" not in result.stderr


@pytest.mark.parametrize("invocation", ["vc-resume codex", "vc-start"])
def test_reentry_boundary_stops_a_terminal_launch_loop(
    tmp_path: Path, invocation: str
) -> None:
    """A terminal-launched entry never opens another terminal."""
    result, launch = _run_entry(
        tmp_path,
        invocation,
        extra_env={"VIBECRAFTED_TERMINAL_ENTRY": "1"},
        expect_launch=False,
    )

    assert launch is None, "escalation looped despite the explicit boundary"
    assert "opened the Vibecrafted terminal" not in result.stderr


@pytest.mark.parametrize("invocation", ["vc-resume codex", "vc-start"])
def test_explicit_operator_session_keeps_the_direct_path(
    tmp_path: Path, invocation: str
) -> None:
    """An explicitly named target is honoured; do not hijack it into a window."""
    _result, launch = _run_entry(
        tmp_path,
        invocation,
        extra_env={"VIBECRAFTED_OPERATOR_SESSION": "mlx-batch-runner"},
        expect_launch=False,
    )

    assert launch is None, "explicit operator target was overridden by a terminal"


@pytest.mark.parametrize("invocation", ["vc-resume codex", "vc-start"])
def test_missing_terminal_host_fails_actionably(
    tmp_path: Path, invocation: str
) -> None:
    """When no terminal host exists the error names the gap, never a silent pass."""
    result, launch = _run_entry(
        tmp_path, invocation, with_generation=False, expect_launch=False
    )

    assert launch is None
    assert result.returncode != 0
    combined = result.stderr
    assert "no TTY" in combined
    assert "terminal" in combined.lower()


def test_real_tty_is_not_rerouted(tmp_path: Path) -> None:
    """A genuine terminal keeps the direct path -- detection is the TTY itself."""
    generation = _fake_generation(tmp_path, tmp_path / "unused.json")
    script = (
        f'source "{SHELL_SH}"\n'
        f'_vetcoders_vc_frame_loaded_root="{generation}"\n'
        "if _vetcoders_needs_vc_terminal_entry; then echo NEEDS; else echo DIRECT; fi\n"
    )
    # Give the probe a real controlling terminal.
    result = subprocess.run(
        [
            "python3",
            "-c",
            (
                "import pty, sys; sys.exit(pty.spawn("
                "['bash', '--noprofile', '--norc', '-c', sys.argv[1]]))"
            ),
            script,
        ],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "DIRECT" in result.stdout, result.stdout + result.stderr
    assert "NEEDS" not in result.stdout


def test_primary_shell_routes_every_product_verb(tmp_path: Path) -> None:
    """The terminal wrapper used to drop the argv of anything but vc-start."""
    capture = tmp_path / "verb.txt"
    front_door = _write(
        tmp_path / "bin" / "vc-resume",
        "#!/bin/bash\nprintf '%s\\n' \"$@\" > " + shlex.quote(str(capture)) + "\n",
    )
    result = subprocess.run(
        ["bash", str(PRIMARY_SHELL), str(front_door), "codex", "--runtime", "terminal"],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
    )
    assert capture.exists(), (
        "primary shell dropped the product verb argv: "
        f"rc={result.returncode} err={result.stderr}"
    )
    assert capture.read_text(encoding="utf-8").split() == [
        "codex",
        "--runtime",
        "terminal",
    ]
