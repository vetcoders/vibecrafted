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
<front door> [argv] -- and then, inside that terminal, the child completes the
resume in the right ORDER: exactly one AICX pack, exactly one provider tab, and
only afterwards the blocking foreground attach.

Four properties are load-bearing and each has a case below:
  * project identity -- explicit --root wins, and the runtime generation is
    never mistaken for the operator's project;
  * session ownership -- unrelated live sessions elsewhere never capture this
    project, however many or few there are;
  * one physical config owner -- only $HOME/.config/vibecrafted/vc-terminal/,
    no XDG override and no release-default fallback;
  * honest admission -- a rejected launch is reported as a failure, and a
    missing front door stops before any AICX/provider side effect.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
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

# The operator's terminal is not the inside of a dispatched worker. A worker
# run exports its own workspace/session identities, and pytest inherits them;
# leaving them in place hands these entries a workspace id that the fixture's
# isolated catalogue has never heard of. The public entries resolve identity
# through the canonical workspace owner, so the fixture must present the same
# blank slate a real terminal does.
WORKSPACE_IDENTITY_ENV = (
    "VIBECRAFTED_OPERATOR_SESSION",
    "VIBECRAFTED_WORKSPACE_ID",
    "VIBECRAFTED_SESSION_ID",
    "VIBECRAFTED_WORKSPACE_INSTANCE_ID",
    "VIBECRAFTED_WORKSPACE_ROOT",
    "VIBECRAFTED_BUILD_ID",
)

# A stand-in for the vc-frame engine. It records every invocation, keeps a live
# session list on disk, BLOCKS on the two calls that block for real (the
# interactive new-session client and the foreground attach), and -- crucially
# -- refuses the same things the real engine refuses.
#
# The refusals are the point. A fixture more permissive than Frame turns a
# broken create into a green test: the previous stub accepted an interactive
# `--new-session-with-layout` client with no TTY, which the real engine rejects
# at zellij-client/src/lib.rs:792 before the session is ever created.
VC_FRAME_STUB = """#!/usr/bin/env python3
import json, os, sys, time

argv = sys.argv[1:]
log = os.environ.get("VC_FRAME_LOG", "")
live_file = os.environ.get("VC_FRAME_LIVE", "")
if log:
    with open(log, "a") as handle:
        handle.write(json.dumps(argv) + "\\n")


def live_sessions():
    if live_file and os.path.exists(live_file):
        return [line.strip() for line in open(live_file) if line.strip()]
    return []


if "--help" in argv:
    print("--after-base   place the tab after the base card")
    print("--no-focus     do not focus the new tab")
    sys.exit(0)

if argv[:1] in (["ls"], ["list-sessions"]):
    for name in live_sessions():
        print("%s [Created 1s ago]" % name)
    sys.exit(0)

def remember(name):
    if live_file and name:
        with open(live_file, "a") as handle:
            handle.write("%s\\n" % name)


def refuse_without_tty():
    if sys.stdin.isatty():
        return
    # zellij-client/src/lib.rs:792, reached only by an INTERACTIVE client.
    sys.stderr.write(
        "vc-frame: stdin is not a terminal (TTY); cannot start an "
        "interactive session. Run vc-frame from a real terminal.\\n"
    )
    sys.exit(1)


session = None
rest = argv
if rest[:1] == ["--session"]:
    session = rest[1]
    rest = rest[2:]

layout = None
if rest[:1] == ["--new-session-with-layout"]:
    layout = rest[1]
    rest = rest[2:]

if rest[:1] == ["action"]:
    sys.exit(0)

# The native detached create: `[-n LAYOUT] attach --create-background NAME`.
# src/commands.rs:792 turns --create-background into should_create_detached and
# zellij-client/src/lib.rs:783 returns from start_server_detached BEFORE the
# TTY guard -- so this form, and only this form, works without a terminal. An
# existing session is REPORTED, never silently accepted (the ClientInfo::Attach
# arm of start_server_detached).
if rest[:2] == ["attach", "--create-background"]:
    name = rest[2] if len(rest) > 2 else session
    forced = os.environ.get("VC_FRAME_CREATE_ERROR", "")
    if forced:
        sys.stderr.write(forced + "\\n")
        sys.exit(1)
    if name in live_sessions():
        sys.stderr.write("Session already exists\\n")
        sys.exit(1)
    remember(name)
    sys.exit(0)

if rest[:1] == ["attach"]:
    target = rest[1] if len(rest) > 1 else session
    # zellij-utils/src/envs.rs normalize_vc_frame_env_aliases() copies
    # VC_FRAME_SESSION_NAME into ZELLIJ_SESSION_NAME whenever the latter is
    # unset, then src/commands.rs:844 panics when that value equals the
    # attach target. A caller must not get to inherit its OWN dispatch-
    # targeting marker as if it were proof of a pre-existing attachment --
    # the previous stub ignored this and let a broken caller pass.
    capture = os.environ.get("VC_FRAME_ATTACH_ENV_CAPTURE", "")
    if capture:
        with open(capture, "a") as handle:
            handle.write(json.dumps({
                "target": target,
                "VC_FRAME_SESSION_NAME": os.environ.get("VC_FRAME_SESSION_NAME"),
                "ZELLIJ_SESSION_NAME": os.environ.get("ZELLIJ_SESSION_NAME"),
            }) + "\\n")
    zellij_session = os.environ.get("ZELLIJ_SESSION_NAME") or os.environ.get(
        "VC_FRAME_SESSION_NAME"
    )
    if zellij_session == target:
        sys.stderr.write(
            "panicked at src/commands.rs:844: "
            'You are trying to attach to the current session ("%s"). '
            "This is not supported.\\n" % target
        )
        sys.exit(101)
    refuse_without_tty()
    # The foreground handover blocks; keep it short so the test can finish.
    time.sleep(0.3)
    sys.exit(0)

if layout is not None:
    # Interactive new-session client: owns the terminal until the operator
    # detaches, and cannot start at all without one.
    refuse_without_tty()
    remember(session)
    time.sleep(30)
    sys.exit(0)

sys.exit(0)
"""


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _install_canonical_launcher(home: Path) -> Path:
    """The one physical owner: $HOME/.config/vibecrafted/vc-terminal/."""
    return _write(
        home / ".config" / "vibecrafted" / "vc-terminal" / "launch-primary-shell.zsh",
        PRIMARY_SHELL.read_text(encoding="utf-8"),
    )


def _fake_generation(
    root: Path,
    capture: Path,
    *,
    front_doors: tuple[str, ...] = ("vc-start", "vibecrafted"),
    terminal_exit: int = 0,
) -> Path:
    """A generation tree strict enough for the real resolvers to accept."""
    generation = root / "generation"
    # Engines must be real, executable, non-symlink files.
    _write(generation / "libexec" / "vc-terminal", "#!/bin/bash\nexit 0\n")
    _write(generation / "libexec" / "vc-frame", VC_FRAME_STUB)
    _write(generation / "bin" / "vc-frame", VC_FRAME_STUB)
    # Product terminal entry records the launch instead of opening a window.
    _write(
        generation / "bin" / "vc-terminal",
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"open({str(capture)!r}, 'w').write(json.dumps("
        "{'argv': sys.argv[1:], 'cwd': os.getcwd(),"
        " 'boundary': os.environ.get('VIBECRAFTED_TERMINAL_ENTRY', '')}))\n"
        f"sys.exit({terminal_exit})\n",
    )
    for verb in front_doors:
        _write(generation / "bin" / verb, "#!/bin/bash\nexit 0\n")
    # Release defaults are installer INPUT. Their presence must never rescue a
    # missing canonical launcher, so the fixture deliberately ships them.
    _write(
        generation / "config" / "alacritty" / "launch-primary-shell.zsh",
        PRIMARY_SHELL.read_text(encoding="utf-8"),
    )
    return generation


def _shell_argv(shell: str, script: str) -> list[str]:
    """The no-rc invocation for a given login shell, bash or zsh."""
    if shell == "zsh":
        # -f: skip all rc/profile files, same isolation as bash's --noprofile
        # --norc. Zsh's own default array base (1-indexed, no KSH_ARRAYS) stays
        # untouched -- this is exactly the boundary the facade must survive.
        return ["zsh", "-f", "-c", script]
    return ["bash", "--noprofile", "--norc", "-c", script]


def _run_entry(
    tmp_path: Path,
    invocation: str,
    *,
    project: str = "mlx-batch-runner",
    extra_env: dict[str, str] | None = None,
    with_generation: bool = True,
    with_canonical_launcher: bool = True,
    front_doors: tuple[str, ...] = ("vc-start", "vibecrafted"),
    terminal_exit: int = 0,
    expect_launch: bool = True,
    shell: str = "bash",
) -> tuple[subprocess.CompletedProcess[str], dict | None]:
    capture = tmp_path / "terminal-launch.json"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    project_dir = tmp_path / project
    project_dir.mkdir(parents=True, exist_ok=True)
    if with_canonical_launcher:
        _install_canonical_launcher(home)

    env = os.environ.copy()
    for key in (
        *WORKSPACE_IDENTITY_ENV,
        "VIBECRAFTED_TERMINAL_ENTRY",
        "VIBECRAFTED_ROOT",
        "VIBECRAFTED_RUNTIME_ROOT",
        "SPAWN_ROOT",
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
        generation = _fake_generation(
            tmp_path, capture, front_doors=front_doors, terminal_exit=terminal_exit
        )
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
            ">> \"$TEST_AICX_CAPTURE\"; printf 'MODE=new_session\\n'; }"
        ),
        invocation,
    ]

    result = subprocess.run(
        _shell_argv(shell, "\n".join(lines)),
        check=False,
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
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


def _hosted_argv(launch: dict) -> list[str]:
    argv = launch["argv"]
    return argv[argv.index("-e") + 1 :]


def _working_directory(launch: dict) -> Path:
    argv = launch["argv"]
    return Path(argv[argv.index("--working-directory") + 1]).resolve()


def _child_effective_root(tmp_path: Path, launch: dict) -> Path:
    """The project the CHILD lands on, resolved from the argv it was handed.

    The child re-parses the forwarded vector from the terminal's working
    directory. That SECOND parse is where a raw relative --root resolves one
    level too deep, so asserting on the parent's cwd alone cannot see it.
    """
    hosted = _hosted_argv(launch)
    assert hosted[2] == "resume", hosted
    contract_args = hosted[4:]
    script = "\n".join(
        [
            f'source "{SHELL_SH}"',
            "_vetcoders_parse_contract "
            + " ".join(shlex.quote(arg) for arg in contract_args),
            'printf "CHILD_ROOT=[%s]\\n" "$(_vetcoders_effective_project_root)"',
        ]
    )
    env = os.environ.copy()
    for key in ("VIBECRAFTED_ROOT", "VIBECRAFTED_RUNTIME_ROOT", "SPAWN_ROOT"):
        env.pop(key, None)
    env["HOME"] = str(tmp_path / "home")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        check=False,
        cwd=_working_directory(launch),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    marker = "CHILD_ROOT=["
    assert marker in result.stdout, result.stdout + result.stderr
    return Path(result.stdout.split(marker, 1)[1].split("]", 1)[0]).resolve()


# --------------------------------------------------------------------------
# The reported P0
# --------------------------------------------------------------------------


def test_bare_resume_without_tty_opens_terminal_on_this_project(
    tmp_path: Path,
) -> None:
    """The reported P0: bare resume must open a terminal, not refuse."""
    result, launch = _run_entry(tmp_path, "vc-resume codex")

    assert result.returncode == 0, result.stderr
    assert launch is not None, f"no terminal was opened: {result.stderr}"

    # Exact cwd: the session belongs to the project the operator ran this in.
    assert _working_directory(launch) == (tmp_path / "mlx-batch-runner").resolve()

    # The existing owner contract, not a private launcher.
    hosted = _hosted_argv(launch)
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
    hosted = _hosted_argv(launch)
    assert hosted[0].endswith("launch-primary-shell.zsh")
    assert hosted[1].endswith("/bin/vc-start")


def test_start_preserves_exact_argv_including_quoting(tmp_path: Path) -> None:
    """argv is preserved verbatim -- a spaced argument stays one argument."""
    result, launch = _run_entry(
        tmp_path, "vc-start " + shlex.quote("two words") + " --flag=a b"
    )

    assert result.returncode == 0, result.stderr
    assert launch is not None
    assert _hosted_argv(launch)[2:] == ["two words", "--flag=a", "b"]


# --------------------------------------------------------------------------
# Project identity: explicit --root, and the generation is not a project
# --------------------------------------------------------------------------


def test_explicit_absolute_root_binds_the_terminal_not_the_cwd(
    tmp_path: Path,
) -> None:
    """`--root B` from A opens B. Forwarding B while opening A is the bug."""
    other = tmp_path / "project-b"
    other.mkdir()
    result, launch = _run_entry(
        tmp_path, f"vc-resume codex --root {shlex.quote(str(other))}"
    )

    assert result.returncode == 0, result.stderr
    assert launch is not None, result.stderr
    assert _working_directory(launch) == other.resolve()
    assert _hosted_argv(launch)[2:4] == ["resume", "codex"]


def test_relative_explicit_root_is_resolved_against_the_caller(
    tmp_path: Path,
) -> None:
    """`--root ../project-b` must not be re-read after we chdir into it."""
    other = tmp_path / "project-b"
    other.mkdir()
    result, launch = _run_entry(tmp_path, "vc-resume codex --root ../project-b")

    assert result.returncode == 0, result.stderr
    assert launch is not None, result.stderr
    assert _working_directory(launch) == other.resolve()
    # A sibling token resolves back onto itself from the new cwd, so this case
    # is forgiving by accident. Check the child too, or it proves nothing.
    assert _child_effective_root(tmp_path, launch) == other.resolve()


def test_nested_relative_root_survives_the_child_reparse(tmp_path: Path) -> None:
    """`--root child` from /project must not become /project/child/child.

    The parent chdirs the terminal INTO the normalized root, so the child reads
    the same token from one level deeper. Forwarding it raw makes the child
    resolve a directory that usually does not exist -- and, when it happens to
    exist, silently binds continuity and the provider to the wrong repository.
    """
    project = tmp_path / "mlx-batch-runner"
    project.mkdir(parents=True, exist_ok=True)
    nested = project / "child"
    nested.mkdir()

    result, launch = _run_entry(tmp_path, "vc-resume codex --root child")

    assert result.returncode == 0, result.stderr
    assert launch is not None, result.stderr
    assert _working_directory(launch) == nested.resolve()

    hosted = _hosted_argv(launch)
    assert hosted[4] == "--root", hosted
    assert Path(hosted[5]).is_absolute(), f"a relative root crossed the cwd: {hosted}"
    assert Path(hosted[5]).resolve() == nested.resolve()
    assert _child_effective_root(tmp_path, launch) == nested.resolve()


def test_root_rewrite_preserves_every_other_argument(tmp_path: Path) -> None:
    """Only the root VALUE is rewritten; flags, order and provider args stay."""
    project = tmp_path / "mlx-batch-runner"
    project.mkdir(parents=True, exist_ok=True)
    nested = project / "child"
    nested.mkdir()

    result, launch = _run_entry(
        tmp_path,
        "vc-resume claude --fork-session --root child --runtime terminal",
    )

    assert result.returncode == 0, result.stderr
    assert launch is not None, result.stderr

    hosted = _hosted_argv(launch)[2:]
    assert hosted[:3] == ["resume", "claude", "--fork-session"], hosted
    assert hosted[3] == "--root", hosted
    assert Path(hosted[4]).resolve() == nested.resolve()
    assert hosted[5:] == ["--runtime", "terminal"], hosted


def test_explicit_root_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    result, launch = _run_entry(
        tmp_path,
        "vc-resume codex --root /nonexistent/project",
        expect_launch=False,
    )

    assert launch is None
    assert result.returncode != 0
    assert "--root is not an existing directory" in result.stderr
    assert not (tmp_path / "aicx-called.txt").exists()


def test_generation_root_is_not_mistaken_for_the_project(tmp_path: Path) -> None:
    """Front doors pin VIBECRAFTED_ROOT to the generation; that is not a project.

    vc_start.rs, vc-terminal-product-entry.sh and vc-frame-product-entry.sh all
    export VIBECRAFTED_ROOT == VIBECRAFTED_RUNTIME_ROOT. Reading that as the
    project opened the terminal on the release directory.
    """
    generation = tmp_path / "generation"
    result, launch = _run_entry(
        tmp_path,
        "vc-resume codex",
        extra_env={
            "VIBECRAFTED_ROOT": str(generation),
            "VIBECRAFTED_RUNTIME_ROOT": str(generation),
        },
    )

    assert result.returncode == 0, result.stderr
    assert launch is not None, result.stderr
    assert _working_directory(launch) == (tmp_path / "mlx-batch-runner").resolve()


# --------------------------------------------------------------------------
# Session ownership: a global session is not a claim on this project
# --------------------------------------------------------------------------


def _resolve_target(
    tmp_path: Path, live: list[str], *, project: str = "mlx-batch-runner"
) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    _install_canonical_launcher(home)
    project_dir = tmp_path / project
    project_dir.mkdir(parents=True, exist_ok=True)
    generation = _fake_generation(tmp_path, tmp_path / "unused.json")
    live_file = tmp_path / "live-sessions.txt"
    live_file.write_text("".join(f"{name}\n" for name in live), encoding="utf-8")

    env = os.environ.copy()
    for key in (
        *WORKSPACE_IDENTITY_ENV,
        "VIBECRAFTED_ROOT",
        "VIBECRAFTED_RUNTIME_ROOT",
        "SPAWN_ROOT",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ_SESSION_NAME",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VC_FRAME_LIVE"] = str(live_file)
    env["VC_FRAME_LOG"] = str(tmp_path / "frame.log")

    script = (
        f'source "{SHELL_SH}"\n'
        f'_vetcoders_vc_frame_loaded_root="{generation}"\n'
        "printf 'TARGET=[%s]\\n' \"$(_vetcoders_resolve_interactive_operator_target)\"\n"
    )
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        check=False,
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_no_live_sessions_leaves_the_target_unresolved(tmp_path: Path) -> None:
    result = _resolve_target(tmp_path, [])
    assert "TARGET=[]" in result.stdout, result.stdout + result.stderr


def test_single_unrelated_live_session_is_not_adopted(tmp_path: Path) -> None:
    """One live session elsewhere is a coincidence, not ownership."""
    result = _resolve_target(tmp_path, ["host-a"])
    assert "TARGET=[]" in result.stdout, result.stdout + result.stderr
    assert "host-a" in result.stderr


def test_many_unrelated_live_sessions_do_not_capture_the_project(
    tmp_path: Path,
) -> None:
    """The exact P0 listing: none of these belong to mlx-batch-runner."""
    result = _resolve_target(tmp_path, ["Live runs", "Needs attention", "host-a"])
    assert "TARGET=[]" in result.stdout, result.stdout + result.stderr
    assert "unrelated live vc-frame session" in result.stderr


def test_attached_marker_on_another_project_is_not_ownership(
    tmp_path: Path,
) -> None:
    """`(attached)` means SOME client is attached -- not this caller."""
    result = _resolve_target(tmp_path, ["host-a (attached)"])
    assert "TARGET=[]" in result.stdout, result.stdout + result.stderr


def test_project_bound_live_session_is_reused(tmp_path: Path) -> None:
    """Proven ownership: the session named after THIS repository."""
    result = _resolve_target(
        tmp_path, ["Live runs", "mlx-batch-runner", "host-a"]
    )
    assert "TARGET=[mlx-batch-runner]" in result.stdout, result.stdout + result.stderr


def test_unrelated_live_sessions_do_not_block_the_project(tmp_path: Path) -> None:
    """End to end: global sessions elsewhere never refuse the escalation."""
    live_file = tmp_path / "live-sessions.txt"
    live_file.write_text("Live runs\nNeeds attention\nhost-a\n", encoding="utf-8")
    result, launch = _run_entry(
        tmp_path,
        "vc-resume codex",
        extra_env={"VC_FRAME_LIVE": str(live_file)},
    )

    assert result.returncode == 0, result.stderr
    assert launch is not None, f"ambiguity still blocked the project: {result.stderr}"
    assert "refusing to downgrade" not in result.stderr


# --------------------------------------------------------------------------
# Workspace binding: ONE canonical owner for start and resume
#
# S1 R8 (Founder, 2026-09-07): the installed public entries both opened a
# persistent terminal and then disagreed about the project. `vc-start` resolved
# the workspace through the selected generation's CLI and targeted
# `vibecrafted-<token>`; bare resume recomputed the name through whatever
# `python3` the login PATH offered, could not import vibecrafted_core there,
# swallowed that into the repository basename, and then adopted any live
# session carrying that name. The catalogue had four binding receipts to the
# hashed session and none to the plain one.
#
# Only the native catalogue boundary is stubbed below. Which owner the shell
# asks, whether a failure is swallowed, and which live name counts as
# ownership -- the three things the defect actually lived in -- all run for
# real, in both shells.
# --------------------------------------------------------------------------

BOUND_SESSION = "vibecrafted-921310b3"
BOUND_WORKSPACE_ID = "01a06f41-ebc6-706b-990e-b7ba921310b3"


def _canonical_owner_cli(
    path: Path,
    *,
    session: str = BOUND_SESSION,
    resolve_exit: int = 0,
    calls: Path | None = None,
) -> Path:
    """The selected generation's CLI: the physical owner of the catalogue."""
    body = ["#!/usr/bin/env bash"]
    if calls is not None:
        body.append(f'printf "%s\\n" "$*" >> "{calls}"')
    body.append('if [[ "$1 $2" == "workspace resolve" ]]; then')
    if resolve_exit == 0:
        body += [
            f"  echo VIBECRAFTED_WORKSPACE_ID={BOUND_WORKSPACE_ID}",
            "  echo VIBECRAFTED_SESSION_ID=sess-canonical",
            "  echo VIBECRAFTED_WORKSPACE_INSTANCE_ID=inst-canonical",
            f"  echo VIBECRAFTED_OPERATOR_SESSION={session}",
            "  exit 0",
        ]
    else:
        body += [
            '  echo "workspace catalogue is unreadable" >&2',
            f"  exit {resolve_exit}",
        ]
    body += ["fi", "exit 0"]
    return _write(path, "\n".join(body) + "\n")


def _bound_project(
    tmp_path: Path,
    *,
    live: list[str],
    owner_cli: Path,
    project: str = "mlx-batch-runner",
    extra_env: dict[str, str] | None = None,
) -> tuple[dict[str, str], Path, Path]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    _install_canonical_launcher(home)
    project_dir = tmp_path / project
    project_dir.mkdir(parents=True, exist_ok=True)
    generation = _fake_generation(tmp_path, tmp_path / "unused.json")
    live_file = tmp_path / "live-sessions.txt"
    live_file.write_text("".join(f"{name}\n" for name in live), encoding="utf-8")

    # The login child's PATH as the operator really has it under `zsh -lic`:
    # a foreign python3 FIRST (it starts, but cannot import vibecrafted_core)
    # and the generation's bin only later. Resolving a workspace through that
    # interpreter is exactly what produced a silent basename.
    foreign_bin = tmp_path / "foreign-bin"
    _write(foreign_bin / "python3", '#!/usr/bin/env bash\nexec /usr/bin/python3 "$@"\n')

    env = os.environ.copy()
    for key in (
        *WORKSPACE_IDENTITY_ENV,
        "VIBECRAFTED_ROOT",
        "VIBECRAFTED_RUNTIME_ROOT",
        "SPAWN_ROOT",
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ",
        "ZELLIJ_PANE_ID",
        "ZELLIJ_SESSION_NAME",
        # tests/conftest.py sets this suite-wide. Here the no-PTY path IS the
        # contract under test, so the bypass is opted into per case instead.
        "VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VC_FRAME_LIVE"] = str(live_file)
    env["VC_FRAME_LOG"] = str(tmp_path / "frame.log")
    env["VIBECRAFTED_PRODUCT_CORE_CLI"] = str(owner_cli)
    env["PATH"] = f"{foreign_bin}:{generation / 'bin'}:{env.get('PATH', '')}"
    env.update(extra_env or {})
    return env, project_dir, generation


def _prepare_target(
    shell: str,
    env: dict[str, str],
    project_dir: Path,
    generation: Path,
    *,
    tail: str = "",
) -> subprocess.CompletedProcess[str]:
    script = (
        f'source "{SHELL_SH}"\n'
        f'_vetcoders_vc_frame_loaded_root="{generation}"\n'
        "_vetcoders_prepare_operator_runtime terminal\n"
        'printf "RC=[%s]\\n" "$?"\n'
        'printf "TARGET=[%s]\\n" "${VIBECRAFTED_OPERATOR_SESSION:-}"\n'
        'printf "WORKSPACE=[%s]\\n" "${VIBECRAFTED_WORKSPACE_ID:-}"\n'
        'printf "INSTANCE=[%s]\\n" "${VIBECRAFTED_WORKSPACE_INSTANCE_ID:-}"\n'
        'printf "SESSION_ID=[%s]\\n" "${VIBECRAFTED_SESSION_ID:-}"\n' + tail
    )
    return subprocess.run(
        _shell_argv(shell, script),
        check=False,
        cwd=project_dir,
        env=env,
        # A non-interactive caller, exactly like the agent shell that reported
        # this: inheriting the runner's tty would take the create branch and
        # never exercise the no-live-target contract.
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_default_resume_targets_the_registered_workspace_session(
    tmp_path: Path, shell: str
) -> None:
    """Both the registered session and a same-basename stranger are live.

    The catalogue binds THIS root to the hashed session. Picking the basename
    is the defect: it is a coincidence of naming, and the operator's work lands
    in a window the catalogue never bound to this project.
    """
    owner = _canonical_owner_cli(tmp_path / "owner-cli")
    env, project_dir, generation = _bound_project(
        tmp_path, live=[BOUND_SESSION, "mlx-batch-runner"], owner_cli=owner
    )

    result = _prepare_target(shell, env, project_dir, generation)

    assert f"TARGET=[{BOUND_SESSION}]" in result.stdout, result.stdout + result.stderr
    assert "TARGET=[mlx-batch-runner]" not in result.stdout
    # The binding ids travel with the target: a subshell resolver could pick a
    # name, but WES attachment needs these, and without them the resumed
    # session carries no receipt at all.
    assert f"WORKSPACE=[{BOUND_WORKSPACE_ID}]" in result.stdout, result.stdout
    assert "INSTANCE=[inst-canonical]" in result.stdout, result.stdout
    assert "SESSION_ID=[sess-canonical]" in result.stdout, result.stdout


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_same_basename_live_session_is_not_a_workspace_binding(
    tmp_path: Path, shell: str
) -> None:
    """The bound session is NOT live; a same-basename stranger is.

    A live name alone is not ownership. With no live canonical target and no
    PTY to create one, the entry leaves the operator session unset instead of
    adopting the stranger.
    """
    owner = _canonical_owner_cli(tmp_path / "owner-cli")
    env, project_dir, generation = _bound_project(
        tmp_path, live=["mlx-batch-runner"], owner_cli=owner
    )

    result = _prepare_target(shell, env, project_dir, generation)

    assert "TARGET=[]" in result.stdout, result.stdout + result.stderr
    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    # The project identity is still resolved and propagated -- it describes the
    # workspace, not a live session.
    assert f"WORKSPACE=[{BOUND_WORKSPACE_ID}]" in result.stdout, result.stdout


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_explicit_operator_session_outranks_the_catalogue(
    tmp_path: Path, shell: str
) -> None:
    """A deliberate override stays the higher-priority choice."""
    calls = tmp_path / "owner-calls"
    owner = _canonical_owner_cli(tmp_path / "owner-cli", calls=calls)
    env, project_dir, generation = _bound_project(
        tmp_path,
        live=[BOUND_SESSION, "chosen-by-hand"],
        owner_cli=owner,
        extra_env={"VIBECRAFTED_OPERATOR_SESSION": "chosen-by-hand"},
    )

    result = _prepare_target(shell, env, project_dir, generation)

    assert "TARGET=[chosen-by-hand]" in result.stdout, result.stdout + result.stderr
    assert not calls.exists(), "an explicit override still consulted the catalogue"


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_verified_attached_caller_outranks_the_catalogue(
    tmp_path: Path, shell: str
) -> None:
    """A genuine nested caller already owns its session; do not re-target it."""
    owner = _canonical_owner_cli(tmp_path / "owner-cli")
    env, project_dir, generation = _bound_project(
        tmp_path,
        live=[BOUND_SESSION, "already-inside"],
        owner_cli=owner,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_SESSION_NAME": "already-inside",
            "VC_FRAME_PANE_ID": "7",
        },
    )

    result = _prepare_target(shell, env, project_dir, generation)

    assert "TARGET=[already-inside]" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_unresolvable_workspace_owner_refuses_before_provider_side_effects(
    tmp_path: Path, shell: str
) -> None:
    """No canonical ownership -> refuse, and refuse BEFORE AICX/provider work.

    The failure that matters is not "no session": it is targeting someone
    else's. Refusing here keeps a broken catalogue from silently redirecting
    the operator into an unrelated live window.
    """
    owner = _canonical_owner_cli(tmp_path / "owner-cli", resolve_exit=64)
    env, project_dir, generation = _bound_project(
        tmp_path, live=[BOUND_SESSION, "mlx-batch-runner"], owner_cli=owner
    )
    env["TEST_AICX_CAPTURE"] = str(tmp_path / "aicx-called.txt")

    script = (
        f'source "{SHELL_SH}"\n'
        f'_vetcoders_vc_frame_loaded_root="{generation}"\n'
        "_vetcoders_aicx_resume_fallback() { printf 'called\\n' "
        '>> "$TEST_AICX_CAPTURE"; printf "MODE=new_session\\n"; }\n'
        "_vetcoders_prepare_operator_runtime terminal\n"
        'printf "RC=[%s]\\n" "$?"\n'
        'printf "TARGET=[%s]\\n" "${VIBECRAFTED_OPERATOR_SESSION:-}"\n'
    )
    result = subprocess.run(
        _shell_argv(shell, script),
        check=False,
        cwd=project_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert "TARGET=[mlx-batch-runner]" not in result.stdout, result.stdout
    assert f"TARGET=[{BOUND_SESSION}]" not in result.stdout, result.stdout
    assert not (tmp_path / "aicx-called.txt").exists(), "AICX ran before admission"


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_absent_canonical_target_is_created_under_its_bound_name(
    tmp_path: Path, shell: str
) -> None:
    """Nothing live: prepare THIS project's target, under the bound name."""
    owner = _canonical_owner_cli(tmp_path / "owner-cli")
    env, project_dir, generation = _bound_project(
        tmp_path,
        live=[],
        owner_cli=owner,
        extra_env={"VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME": "1"},
    )
    _write(
        Path(env["HOME"])
        / ".config"
        / "vibecrafted"
        / "vc-frame"
        / "layouts"
        / "operator.kdl",
        "layout {\n}\n",
    )

    result = _prepare_target(shell, env, project_dir, generation)

    frame_log = tmp_path / "frame.log"
    assert frame_log.exists(), result.stdout + result.stderr
    calls = [
        json.loads(line)
        for line in frame_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    created = [c for c in calls if "--new-session-with-layout" in c]
    assert created, f"no session was created: {calls}"
    for call in created:
        assert BOUND_SESSION in call, f"created under a non-bound name: {call}"
        assert "mlx-batch-runner" not in call, call


# --------------------------------------------------------------------------
# S1 R8b: the REAL public resume boundary, not dashboard.sh's `vc-start resume`
#
# Independent admission (2026-09-07) found R8 incomplete in two ways:
#  1. `_vetcoders_ensure_canonical_workspace_identity` preferred an inherited
#     VIBECRAFTED_WORKSPACE_ROOT over an explicit normalized --root, and
#     treated three nonempty fields (missing VIBECRAFTED_SESSION_ID, no root
#     check) as proof of an already-resolved identity -- so a fully-cached,
#     stale binding for a DIFFERENT project could stand in for the one an
#     operator had just explicitly requested with --root.
#  2. The actual public `vibecrafted resume <tool> --root A` entry is
#     marbles.sh's `_vetcoders_resume_agent`. It assembles the AICX
#     continuity pack (no --session, no explicit prompt/file) well before it
#     ever calls `_vetcoders_prepare_operator_runtime`; R8's gate lived only
#     inside that later helper, so it never stopped AICX from running on an
#     unresolvable owner. R8's own test called only that helper directly --
#     a body that never touches AICX -- so the gap was invisible to it.
#  3. Confirmed by an independent trace (r8-missing-python-path-trace.json):
#     an installed generation missing its own bin/python3 let
#     `_vetcoders_product_core_cli` fall through to a bare `python3` lookup,
#     silently substituting whatever interpreter PATH happened to offer.
#
# Tests below exercise the real functions at the real boundary.
# --------------------------------------------------------------------------


def _root_aware_owner_cli(path: Path) -> Path:
    """Encodes the requested --root into its answer.

    Unlike `_canonical_owner_cli` (fixed session name regardless of root),
    this stub lets a test tell whether the owner was actually asked for THIS
    root, or a stale cached answer for a different one was reused untouched.
    """
    body = (
        "#!/usr/bin/env bash\n"
        'if [[ "$1 $2" == "workspace resolve" ]]; then\n'
        "  shift 2\n"
        '  root=""\n'
        "  while [[ $# -gt 0 ]]; do\n"
        '    case "$1" in\n'
        '      --root) root="$2"; shift 2 ;;\n'
        "      *) shift ;;\n"
        "    esac\n"
        "  done\n"
        '  tag="$(basename "$root")"\n'
        "  tag=\"$(printf '%s' \"$tag\" | tr -c 'A-Za-z0-9' '-')\"\n"
        '  echo "VIBECRAFTED_WORKSPACE_ID=ws-$tag"\n'
        '  echo "VIBECRAFTED_SESSION_ID=sess-$tag"\n'
        '  echo "VIBECRAFTED_WORKSPACE_INSTANCE_ID=inst-$tag"\n'
        '  echo "VIBECRAFTED_OPERATOR_SESSION=session-$tag"\n'
        '  echo "VIBECRAFTED_WORKSPACE_ROOT=$root"\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    return _write(path, body)


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_explicit_root_wins_over_stale_ambient_workspace_binding(
    tmp_path: Path, shell: str
) -> None:
    """Finding #1: an inherited, FULLY cached binding for project B (all four
    identity fields, including SESSION_ID) must never stand in for an
    explicit normalized --root A. Pre-fix, three nonempty fields (no
    SESSION_ID, no root comparison) were treated as sufficient proof and the
    stale B binding was returned untouched, even though A was requested.
    """
    owner = _root_aware_owner_cli(tmp_path / "owner-cli")
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    root_a.mkdir()
    root_b.mkdir()

    env = os.environ.copy()
    for key in WORKSPACE_IDENTITY_ENV:
        env.pop(key, None)
    env["VIBECRAFTED_PRODUCT_CORE_CLI"] = str(owner)
    # A fully-cached, fully-valid binding -- for a DIFFERENT project (B).
    env["VIBECRAFTED_WORKSPACE_ID"] = "ws-project-b"
    env["VIBECRAFTED_SESSION_ID"] = "sess-project-b"
    env["VIBECRAFTED_WORKSPACE_INSTANCE_ID"] = "inst-project-b"
    env["VIBECRAFTED_OPERATOR_SESSION"] = "session-project-b"
    env["VIBECRAFTED_WORKSPACE_ROOT"] = str(root_b)

    script = (
        f'source "{SHELL_SH}"\n'
        f'_vetcoders_ensure_canonical_workspace_identity "{root_a}"\n'
        'printf "RC=[%s]\\n" "$?"\n'
        'printf "ROOT=[%s]\\n" "${VIBECRAFTED_WORKSPACE_ROOT:-}"\n'
        'printf "TARGET=[%s]\\n" "${VIBECRAFTED_OPERATOR_SESSION:-}"\n'
        'printf "SESSION_ID=[%s]\\n" "${VIBECRAFTED_SESSION_ID:-}"\n'
    )
    result = subprocess.run(
        _shell_argv(shell, script),
        check=False,
        cwd=root_a,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert f"ROOT=[{root_a}]" in result.stdout, result.stdout + result.stderr
    assert "TARGET=[session-project-a]" in result.stdout, result.stdout + result.stderr
    assert "SESSION_ID=[sess-project-a]" in result.stdout, result.stdout + result.stderr
    assert "TARGET=[session-project-b]" not in result.stdout, result.stdout


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_public_resume_owner_failure_records_zero_aicx_or_provider_calls(
    tmp_path: Path, shell: str
) -> None:
    """Finding #2: the actual public boundary is marbles.sh's
    `_vetcoders_resume_agent` (`vibecrafted resume <tool> --root A`), not
    dashboard.sh's `vc-start resume`. On an unresolvable owner it must refuse
    before AICX assembly and before any provider composition -- not merely
    before the later `_vetcoders_prepare_operator_runtime` helper.
    """
    owner = _canonical_owner_cli(tmp_path / "owner-cli", resolve_exit=64)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    env = os.environ.copy()
    for key in WORKSPACE_IDENTITY_ENV:
        env.pop(key, None)
    env["VIBECRAFTED_PRODUCT_CORE_CLI"] = str(owner)
    # Simulates running past the no-TTY terminal escalation, exactly like the
    # re-parsed child it would hand off to -- the real boundary under test.
    env["VIBECRAFTED_TERMINAL_ENTRY"] = "1"
    env["TEST_AICX_CAPTURE"] = str(tmp_path / "aicx-called.txt")
    env["TEST_PROVIDER_CAPTURE"] = str(tmp_path / "provider-called.txt")
    env["TEST_PREPARE_CAPTURE"] = str(tmp_path / "prepare-called.txt")

    script = (
        f'source "{SHELL_SH}"\n'
        "_vetcoders_aicx_resume_fallback() { printf 'called\\n' >> \"$TEST_AICX_CAPTURE\"; "
        "printf 'MODE=new_session\\n'; }\n"
        "_vetcoders_fresh_session_command() { printf 'called\\n' >> \"$TEST_PROVIDER_CAPTURE\"; "
        "printf 'true\\n'; }\n"
        "_vetcoders_prepare_operator_runtime() { printf 'called\\n' >> \"$TEST_PREPARE_CAPTURE\"; "
        "return 0; }\n"
        f'_vetcoders_resume_agent codex --root "{project_dir}"\n'
        'printf "RC=[%s]\\n" "$?"\n'
    )
    result = subprocess.run(
        _shell_argv(shell, script),
        check=False,
        cwd=project_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert not (tmp_path / "aicx-called.txt").exists(), (
        "AICX ran before admission: " + result.stdout + result.stderr
    )
    assert not (tmp_path / "provider-called.txt").exists(), (
        "provider composed before admission: " + result.stdout + result.stderr
    )
    assert not (tmp_path / "prepare-called.txt").exists(), (
        "operator runtime prepared before admission: " + result.stdout + result.stderr
    )


def test_public_resume_success_path_one_aicx_one_provider_canonical_ids(
    tmp_path: Path,
) -> None:
    """Exact public success path, once: one AICX assembly, one provider
    composition, canonical workspace/session ids for --root A and the exact
    requested cwd -- propagated into the caller's shell, not a subshell.

    The project's bound session is already live (the realistic re-entry
    shape: an operator resuming into a session the catalogue already knows),
    so the real live-detection/attach path runs end to end; only the final
    blocking foreground attach is stubbed.
    """
    owner = _canonical_owner_cli(tmp_path / "owner-cli")
    env, project_dir, generation = _bound_project(
        tmp_path, live=[BOUND_SESSION], owner_cli=owner
    )
    env["VIBECRAFTED_TERMINAL_ENTRY"] = "1"
    env["TEST_AICX_CAPTURE"] = str(tmp_path / "aicx-called.txt")

    script = (
        f'source "{SHELL_SH}"\n'
        f'_vetcoders_vc_frame_loaded_root="{generation}"\n'
        "_vetcoders_aicx_resume_fallback() { printf 'called\\n' >> \"$TEST_AICX_CAPTURE\"; "
        "printf 'MODE=new_session\\n'; }\n"
        # Only the final blocking foreground attach is stubbed; the real
        # canonical-identity gate, the real live-session detection and the
        # real provider composition/spawn (recorded in frame.log via the
        # vc-frame stub) all run for real.
        "_vetcoders_attach_prepared_vc_frame_session() { return 0; }\n"
        f'_vetcoders_resume_agent codex --root "{project_dir}"\n'
        'printf "RC=[%s]\\n" "$?"\n'
        'printf "WORKSPACE=[%s]\\n" "${VIBECRAFTED_WORKSPACE_ID:-}"\n'
        'printf "SESSION_ID=[%s]\\n" "${VIBECRAFTED_SESSION_ID:-}"\n'
        'printf "TARGET=[%s]\\n" "${VIBECRAFTED_OPERATOR_SESSION:-}"\n'
    )
    result = subprocess.run(
        _shell_argv("bash", script),
        check=False,
        cwd=project_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert f"WORKSPACE=[{BOUND_WORKSPACE_ID}]" in result.stdout, (
        result.stdout + result.stderr
    )
    assert "SESSION_ID=[sess-canonical]" in result.stdout, result.stdout + result.stderr
    # Canonical target/IDs resolved for the EXACT requested --root/cwd (this
    # project dir); the owner stub was invoked with --root project_dir, and
    # the process itself ran with cwd=project_dir throughout.
    assert f"TARGET=[{BOUND_SESSION}]" in result.stdout, result.stdout + result.stderr
    aicx_calls = (tmp_path / "aicx-called.txt").read_text(encoding="utf-8").splitlines()
    assert len(aicx_calls) == 1, aicx_calls
    frame_log = tmp_path / "frame.log"
    assert frame_log.exists(), result.stdout + result.stderr
    calls = [
        json.loads(line)
        for line in frame_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    provider_tabs = [c for c in calls if "new-tab" in c and BOUND_SESSION in c]
    assert len(provider_tabs) == 1, calls


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_installed_generation_missing_interpreter_refuses_before_aicx_or_provider(
    tmp_path: Path, shell: str
) -> None:
    """Finding #3 (confirmed by r8-missing-python-path-trace.json): an
    installed generation missing its own bin/python3 must refuse, even with a
    perfectly real, executable host python3 available on PATH. Silently
    substituting it is exactly how a broken/incomplete install produced a
    coherent-looking but foreign resolution.
    """
    # Installed generations live at <runtime-home>/releases/<generation> --
    # the exact physical shape _vetcoders_product_core_cli now checks
    # (parent directory literally named "releases") to tell a genuine
    # installed payload apart from a bare source/developer checkout.
    generation = tmp_path / "runtime-home" / "releases" / "gen-a"
    (generation / "vibecrafted-core" / "vibecrafted_core").mkdir(
        parents=True, exist_ok=True
    )
    (generation / "vibecrafted-core" / "vibecrafted_core" / "cli.py").write_text(
        "", encoding="utf-8"
    )
    (generation / "bin").mkdir(parents=True, exist_ok=True)
    # Deliberately no generation/bin/python3: the exact "missing owned
    # interpreter" shape from the independent admission trace.
    host_bin = tmp_path / "host-bin"
    _write(
        host_bin / "python3",
        '#!/usr/bin/env bash\nexec /usr/bin/python3 "$@"\n',
    )
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    env = os.environ.copy()
    for key in WORKSPACE_IDENTITY_ENV:
        env.pop(key, None)
    for key in (
        "VIBECRAFTED_PRODUCT_CORE_CLI",
        "VIBECRAFTED_PYTHON",
        "VIBECRAFTED_PREFER_REPO_VC_FRAME",
    ):
        env.pop(key, None)
    # The real owner selection, not a stub -- exercising the actual
    # interpreter fallback chain in _vetcoders_product_core_cli.
    env["VIBECRAFTED_CORE_DIR"] = str(generation / "vibecrafted-core")
    env["PATH"] = f"{host_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_TERMINAL_ENTRY"] = "1"
    env["TEST_AICX_CAPTURE"] = str(tmp_path / "aicx-called.txt")
    env["TEST_PROVIDER_CAPTURE"] = str(tmp_path / "provider-called.txt")

    script = (
        f'source "{SHELL_SH}"\n'
        "_vetcoders_aicx_resume_fallback() { printf 'called\\n' >> \"$TEST_AICX_CAPTURE\"; "
        "printf 'MODE=new_session\\n'; }\n"
        "_vetcoders_fresh_session_command() { printf 'called\\n' >> \"$TEST_PROVIDER_CAPTURE\"; "
        "printf 'true\\n'; }\n"
        f'_vetcoders_resume_agent codex --root "{project_dir}"\n'
        'printf "RC=[%s]\\n" "$?"\n'
        'printf "TARGET=[%s]\\n" "${VIBECRAFTED_OPERATOR_SESSION:-}"\n'
    )
    result = subprocess.run(
        _shell_argv(shell, script),
        check=False,
        cwd=project_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert "TARGET=[]" in result.stdout, result.stdout + result.stderr
    assert not (tmp_path / "aicx-called.txt").exists(), (
        "AICX ran with a foreign, unowned interpreter: " + result.stdout + result.stderr
    )
    assert not (tmp_path / "provider-called.txt").exists(), (
        "provider composed with a foreign, unowned interpreter: "
        + result.stdout
        + result.stderr
    )


# --------------------------------------------------------------------------
# One physical config owner
# --------------------------------------------------------------------------


@pytest.mark.parametrize("invocation", ["vc-resume codex", "vc-start"])
def test_missing_canonical_launcher_is_not_rescued_by_release_defaults(
    tmp_path: Path, invocation: str
) -> None:
    """The generation's config/alacritty copy is installer input, not a fallback."""
    result, launch = _run_entry(
        tmp_path,
        invocation,
        with_canonical_launcher=False,
        expect_launch=False,
    )

    assert launch is None, "a release default was substituted for a broken install"
    assert result.returncode != 0
    assert "canonical product shell launcher missing" in result.stderr
    assert ".config/vibecrafted/vc-terminal/launch-primary-shell.zsh" in result.stderr


def test_xdg_config_home_cannot_supply_the_launcher(tmp_path: Path) -> None:
    """A foreign XDG launcher must not be passed verbatim after -e."""
    foreign = tmp_path / "foreign-xdg"
    _write(
        foreign / "vibecrafted" / "vc-terminal" / "launch-primary-shell.zsh",
        "#!/bin/bash\nexit 0\n",
    )
    result, launch = _run_entry(
        tmp_path,
        "vc-resume codex",
        with_canonical_launcher=False,
        extra_env={"XDG_CONFIG_HOME": str(foreign)},
        expect_launch=False,
    )

    assert launch is None, "an XDG launcher was accepted over the physical owner"
    assert result.returncode != 0
    assert "no XDG override" in result.stderr


def test_symlinked_canonical_launcher_is_refused(tmp_path: Path) -> None:
    """Same symlink boundary scripts/vc-terminal-product-entry.sh enforces."""
    home = tmp_path / "home"
    elsewhere = _write(tmp_path / "elsewhere.zsh", "#!/bin/bash\nexit 0\n")
    target = home / ".config" / "vibecrafted" / "vc-terminal"
    target.mkdir(parents=True, exist_ok=True)
    (target / "launch-primary-shell.zsh").symlink_to(elsewhere)

    result, launch = _run_entry(
        tmp_path,
        "vc-resume codex",
        with_canonical_launcher=False,
        expect_launch=False,
    )

    assert launch is None, "a symlinked launcher was accepted"
    assert result.returncode != 0
    assert "canonical product shell launcher missing" in result.stderr


# --------------------------------------------------------------------------
# Honest admission
# --------------------------------------------------------------------------


@pytest.mark.parametrize("invocation", ["vc-resume codex", "vc-start"])
def test_rejected_terminal_launch_is_reported_as_a_failure(
    tmp_path: Path, invocation: str
) -> None:
    """exit 2 from the wrapper (missing product config) is not "opened"."""
    result, _launch = _run_entry(
        tmp_path, invocation, terminal_exit=2, expect_launch=False
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "rejected this launch (exit 2)" in result.stderr
    assert "opened the Vibecrafted terminal" not in result.stderr


def test_missing_front_door_stops_before_any_aicx_work(tmp_path: Path) -> None:
    """No front door means no PTY is obtainable -- do not assemble a 48h pack."""
    result, launch = _run_entry(
        tmp_path,
        "vc-resume codex",
        front_doors=("vc-start",),
        expect_launch=False,
    )

    assert launch is None
    assert result.returncode != 0
    assert "no installed vibecrafted front door" in result.stderr
    assert not (tmp_path / "aicx-called.txt").exists()


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


# --------------------------------------------------------------------------
# Boundaries that must stay untouched
# --------------------------------------------------------------------------


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
        timeout=60,
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
        timeout=60,
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


# --------------------------------------------------------------------------
# The child, for real: order of operations inside the opened terminal
# --------------------------------------------------------------------------


def _run_child_resume(
    tmp_path: Path, *, live: list[str] | None = None
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    """Run the re-entered child with a REAL pty and a blocking frame client."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    _install_canonical_launcher(home)
    # The operator layout lives under the canonical config owner.
    _write(
        home / ".config" / "vibecrafted" / "vc-frame" / "layouts" / "operator.kdl",
        "layout {\n}\n",
    )
    project_dir = tmp_path / "mlx-batch-runner"
    project_dir.mkdir(parents=True, exist_ok=True)
    generation = _fake_generation(tmp_path, tmp_path / "unused.json")

    frame_log = tmp_path / "frame.log"
    live_file = tmp_path / "live-sessions.txt"
    live_file.write_text(
        "".join(f"{name}\n" for name in (live or [])), encoding="utf-8"
    )

    env = os.environ.copy()
    for key in (
        *WORKSPACE_IDENTITY_ENV,
        "VIBECRAFTED_ROOT",
        "VIBECRAFTED_RUNTIME_ROOT",
        "SPAWN_ROOT",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ_SESSION_NAME",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VC_FRAME_LOG"] = str(frame_log)
    env["VC_FRAME_LIVE"] = str(live_file)
    env["TEST_AICX_CAPTURE"] = str(tmp_path / "aicx-called.txt")
    # This process IS the child the terminal opened.
    env["VIBECRAFTED_TERMINAL_ENTRY"] = "1"

    script = "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{generation}"',
            (
                "_vetcoders_aicx_resume_fallback() { printf 'called\\n' "
                ">> \"$TEST_AICX_CAPTURE\"; printf 'MODE=new_session\\n'; }"
            ),
            "vc-resume codex",
        ]
    )
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
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    calls = [
        json.loads(line)
        for line in frame_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return result, calls


def _first_index(calls: list[list[str]], needle: str) -> int:
    for index, argv in enumerate(calls):
        if needle in argv:
            return index
    return -1


def _foreground_attach_index(calls: list[list[str]]) -> int:
    """Index of the blocking handover, which is `attach <session>` and nothing else.

    Substring matching cannot be used for this: the detached CREATE is
    `-n LAYOUT attach --create-background NAME`, so the token `attach` legally
    appears in the call that must come FIRST.
    """
    for index, argv in enumerate(calls):
        if argv[:1] == ["attach"]:
            return index
    return -1


def test_provider_tab_is_created_before_the_foreground_attach(
    tmp_path: Path,
) -> None:
    """The order the P0 depended on, proven on the actual child path.

    A foreground vc-frame client blocks until the operator detaches. When
    preparation attached first, the provider tab was only created after the
    window had been CLOSED -- an empty terminal, and the work nowhere.
    """
    result, calls = _run_child_resume(tmp_path)

    created = _first_index(calls, "--new-session-with-layout")
    new_tab = _first_index(calls, "new-tab")
    attach = _foreground_attach_index(calls)

    assert created >= 0, f"the project session was never created: {calls}"
    assert new_tab >= 0, f"no provider tab was created: {calls}\n{result.stdout}"
    assert attach >= 0, f"the terminal was never handed over: {calls}"
    assert created < new_tab < attach, (
        "wrong order -- the foreground attach must be the LAST act: "
        f"created={created} new_tab={new_tab} attach={attach} calls={calls}"
    )


def test_session_creation_uses_the_native_detached_form(tmp_path: Path) -> None:
    """Preparation must use the create Frame supports without a terminal.

    A backgrounded interactive client is handed /dev/null on stdin by any
    non-interactive shell -- proven independently of this test -- so the real
    engine refuses it at its TTY guard and the session never appears. Only
    `[-n LAYOUT] attach --create-background NAME` reaches start_server_detached,
    which returns BEFORE that guard.
    """
    _result, calls = _run_child_resume(tmp_path)

    creates = [argv for argv in calls if "--new-session-with-layout" in argv]
    assert creates, f"the project session was never created: {calls}"
    assert len(creates) == 1, f"the session was created more than once: {creates}"

    argv = creates[0]
    assert "attach" in argv and "--create-background" in argv, (
        "preparation used the interactive client form, which Frame refuses "
        f"without a TTY: {argv}"
    )
    # The layout is a top-level option and must precede the subcommand:
    # src/main.rs:359 moves it into the layout field while KEEPING Attach.
    assert argv.index("--new-session-with-layout") < argv.index("attach"), argv
    created_name = argv[argv.index("--create-background") + 1]
    assert created_name and not created_name.startswith("-"), argv

    # The terminal is handed to the session that was actually prepared.
    attaches = [a for a in calls if a[:1] == ["attach"]]
    assert attaches, f"the terminal was never handed over: {calls}"
    assert attaches[-1][1:2] == [created_name], (attaches, created_name)


def _detached_create(
    tmp_path: Path,
    session_name: str,
    *,
    live: list[str] | None = None,
    create_error: str = "",
) -> subprocess.CompletedProcess[str]:
    """Call the preparation helper directly, with nothing else in the way."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    layout = _write(
        home / ".config" / "vibecrafted" / "vc-frame" / "layouts" / "operator.kdl",
        "layout {\n}\n",
    )
    generation = _fake_generation(tmp_path, tmp_path / "unused.json")
    live_file = tmp_path / "live-sessions.txt"
    live_file.write_text(
        "".join(f"{name}\n" for name in (live or [])), encoding="utf-8"
    )

    env = os.environ.copy()
    for key in (
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
    env["VC_FRAME_LIVE"] = str(live_file)
    env["VC_FRAME_LOG"] = str(tmp_path / "frame.log")
    if create_error:
        env["VC_FRAME_CREATE_ERROR"] = create_error

    script = "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{generation}"',
            (
                "if _vetcoders_create_vc_frame_session_detached "
                f'"{generation}/bin/vc-frame" {shlex.quote(session_name)} '
                f'"{layout}"; then echo PREPARED; else echo REFUSED; fi'
            ),
        ]
    )
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_detached_create_reconciles_a_session_that_already_exists(
    tmp_path: Path,
) -> None:
    """A lost race is exit 1 + "Session already exists", not an idempotent 0.

    Frame refuses the detached create outright when the name is taken. That is
    still a usable outcome -- the session the caller wanted is up -- but only
    readiness may say so, never the exit status.
    """
    result = _detached_create(tmp_path, "already-there", live=["already-there"])

    assert "PREPARED" in result.stdout, result.stdout + result.stderr
    assert "refused to create" not in result.stderr


def test_detached_create_refuses_instead_of_waiting_out_a_real_error(
    tmp_path: Path,
) -> None:
    """Any other non-zero exit is a refusal and must stop the launch at once.

    Waiting the full readiness budget out on an error that already told us the
    session will never appear is how a dead launch gets reported as a slow one.
    """
    started = time.monotonic()
    result = _detached_create(
        tmp_path,
        "never-comes-up",
        create_error="vc-frame: could not read the layout file",
    )
    elapsed = time.monotonic() - started

    assert "REFUSED" in result.stdout, result.stdout + result.stderr
    assert "refused to create the session never-comes-up" in result.stderr
    assert "could not read the layout file" in result.stderr, (
        "the engine's own reason was swallowed: " + result.stderr
    )
    assert elapsed < 8, f"a hard refusal was waited out for {elapsed:.1f}s"


# --------------------------------------------------------------------------
# R7 -- native attach owns a clean client environment
# --------------------------------------------------------------------------
#
# _vetcoders_prepare_operator_runtime exports VC_FRAME_SESSION_NAME (and, on
# some paths, ZELLIJ_SESSION_NAME) into ITS OWN shell purely for downstream
# dispatch targeting -- so the AICX pack and provider tab land on the right
# project. _vetcoders_attach_prepared_vc_frame_session then hands the
# terminal to a BRAND NEW native client, which inherits that same shell's
# exported env unless something clears it. The real engine's own startup
# aliases VC_FRAME_SESSION_NAME into ZELLIJ_SESSION_NAME (zellij-utils
# envs::normalize_vc_frame_env_aliases) and src/commands.rs:844 panics
# ("You are trying to attach to the current session") whenever that value
# equals the attach target -- so an inherited targeting marker makes a fresh
# client panic as if it were an illegal nested reattach.


def _prepare_and_attach(
    tmp_path: Path,
    project_name: str,
    *,
    ambient_attached: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Drive prepare_operator_runtime(defer-attach) + the deferred handover.

    A REAL pty is required: _vetcoders_mark_pending_vc_frame_attach only marks
    a pending handover when both stdin and stdout are a controlling terminal,
    the same gate the real non-interactive-shell-hands-a-window-to-the-
    operator contract relies on.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    _write(
        home / ".config" / "vibecrafted" / "vc-frame" / "layouts" / "operator.kdl",
        "layout {\n}\n",
    )
    project_dir = tmp_path / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    generation = _fake_generation(tmp_path, tmp_path / "unused.json")

    frame_log = tmp_path / "frame.log"
    attach_capture = tmp_path / "attach-env.jsonl"
    live_file = tmp_path / "live-sessions.txt"
    live_file.write_text("", encoding="utf-8")

    env = os.environ.copy()
    for key in (
        *WORKSPACE_IDENTITY_ENV,
        "VIBECRAFTED_PENDING_VC_FRAME_ATTACH",
        "VIBECRAFTED_ROOT",
        "VIBECRAFTED_RUNTIME_ROOT",
        "SPAWN_ROOT",
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
    env["VC_FRAME_LOG"] = str(frame_log)
    env["VC_FRAME_LIVE"] = str(live_file)
    env["VC_FRAME_ATTACH_ENV_CAPTURE"] = str(attach_capture)
    env["VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME"] = "1"
    if ambient_attached:
        # A GENUINE nested caller: this process already lives inside the
        # target session -- _vetcoders_in_vc_frame's own trusted signal.
        env["VC_FRAME"] = "1"
        env["VC_FRAME_PANE_ID"] = "0"
        env["VC_FRAME_SESSION_NAME"] = project_name

    script = "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{generation}"',
            "_vetcoders_prepare_operator_runtime terminal defer-attach; prep_rc=$?",
            'echo "PREP_RC=$prep_rc"',
            'echo "SESSION=$VIBECRAFTED_OPERATOR_SESSION"',
            'echo "PENDING=$VIBECRAFTED_PENDING_VC_FRAME_ATTACH"',
            "_vetcoders_attach_prepared_vc_frame_session; attach_rc=$?",
            'echo "ATTACH_RC=$attach_rc"',
            'echo "PARENT_VC_FRAME_SESSION_NAME=$VC_FRAME_SESSION_NAME"',
        ]
    )
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
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, attach_capture


def test_final_attach_clears_forged_targeting_markers(tmp_path: Path) -> None:
    """The deferred handover must not hand the native client its own dispatch-
    targeting env as if it were a real nested attachment (src/commands.rs:844)
    -- while the parent shell keeps that marker for its own downstream use.
    """
    result, attach_capture = _prepare_and_attach(tmp_path, "freshproject")

    assert "PREP_RC=0" in result.stdout, result.stdout + result.stderr
    assert "PENDING=freshproject" in result.stdout, result.stdout + result.stderr
    assert "ATTACH_RC=0" in result.stdout, (
        "the deferred handover was rejected by the native guard: "
        + result.stdout
        + result.stderr
    )
    assert "commands.rs:844" not in result.stderr, result.stderr

    assert attach_capture.exists(), "the native attach binary was never invoked"
    seen = [
        json.loads(line)
        for line in attach_capture.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert seen, "no attach invocation was captured"
    last = seen[-1]
    assert last["target"] == "freshproject", last
    assert last["VC_FRAME_SESSION_NAME"] is None, (
        "the fresh native client inherited a forged targeting marker: " + str(last)
    )
    assert last["ZELLIJ_SESSION_NAME"] is None, last

    # Only the child invocation was sanitized -- the parent shell's own
    # targeting state must survive for its other callers.
    assert "PARENT_VC_FRAME_SESSION_NAME=freshproject" in result.stdout, result.stdout


def test_genuine_attached_caller_gets_no_pending_external_attach(
    tmp_path: Path,
) -> None:
    """A caller already living inside the target session must not spawn a
    second client, and its ambient targeting env must stay untouched.
    """
    result, attach_capture = _prepare_and_attach(
        tmp_path, "already-inside", ambient_attached=True
    )

    assert "PREP_RC=0" in result.stdout, result.stdout + result.stderr
    assert "PENDING=already-inside" not in result.stdout, result.stdout
    assert "ATTACH_RC=0" in result.stdout, result.stdout + result.stderr
    assert not attach_capture.exists(), (
        "a genuinely attached caller must not invoke a second native client"
    )
    assert "PARENT_VC_FRAME_SESSION_NAME=already-inside" in result.stdout, result.stdout


def test_child_creates_exactly_one_tab_and_one_aicx_pack(tmp_path: Path) -> None:
    """Exactly once: no duplicated AICX composition, no second provider launch."""
    _result, calls = _run_child_resume(tmp_path)

    tabs = [argv for argv in calls if "new-tab" in argv and "--help" not in argv]
    attaches = [argv for argv in calls if argv[:1] == ["attach"]]
    assert len(tabs) == 1, f"expected exactly one provider tab, got {tabs}"
    assert len(attaches) == 1, f"expected exactly one handover, got {attaches}"

    aicx = tmp_path / "aicx-called.txt"
    assert aicx.exists(), "the child must assemble the continuity pack"
    assert aicx.read_text(encoding="utf-8").count("called") == 1


def test_child_does_not_hang_its_tab_on_an_unrelated_session(
    tmp_path: Path,
) -> None:
    """Three unrelated live sessions must not receive this project's provider."""
    _result, calls = _run_child_resume(
        tmp_path, live=["Live runs", "Needs attention", "host-a"]
    )

    unrelated = {"Live runs", "Needs attention", "host-a"}
    for argv in calls:
        if "new-tab" in argv and argv[:1] == ["--session"]:
            assert argv[1] not in unrelated, (
                f"the provider tab landed in an unrelated session: {argv}"
            )
    assert _first_index(calls, "--new-session-with-layout") >= 0, (
        f"this project's own session was never prepared: {calls}"
    )


# --------------------------------------------------------------------------
# Zsh compatibility: the facade's compatibility promise, proven, not assumed
# --------------------------------------------------------------------------
#
# The two module-scope helpers below are direct probes, not the full terminal
# harness: `_run_entry` is out of reach here because these cases assert on
# `_vetcoders_contract_argv` itself, before any terminal or session exists.


def _probe_rewrite_argv(shell: str, normalized: str, *args: str) -> list[str]:
    """Direct probe: call the scanner alone, print back `_vetcoders_contract_argv`."""
    probe = "\n".join(
        [
            f'source "{SHELL_SH}"',
            "_vetcoders_rewrite_contract_root_argv "
            + shlex.quote(normalized)
            + " "
            + " ".join(shlex.quote(a) for a in args),
            'for _a in "${_vetcoders_contract_argv[@]}"; do printf "ARG=[%s]\\n" "$_a"; done',
        ]
    )
    result = subprocess.run(
        _shell_argv(shell, probe),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (shell, result.stdout, result.stderr)
    return [
        line[len("ARG=[") : -1]
        for line in result.stdout.splitlines()
        if line.startswith("ARG=[")
    ]


def test_rewrite_contract_root_argv_root_value_as_last_token(tmp_path: Path) -> None:
    """Direct scanner probe: nested relative --root rewritten when its value is
    the LAST token in the vector -- exactly the S1 R3 confirmed shape
    (`_vetcoders_rewrite_contract_root_argv /abs --root child`, snapshot
    sha256 b271db305fe127025b0486d2b1ee7ed415c5756d3c7d550c300618b23649a057)
    and the real `vc-resume codex --root child` forward.

    Zsh arrays are 1-indexed (no KSH_ARRAYS). The old scanner's own bounds
    check (`index + 1 < $#`, sized for a 0-based array whose last valid slot
    is `$# - 1`) silently under-counts by one once the loop's index space
    re-aligns to zsh's 1-based positions, so it wrongly treats the true last
    element as out of bounds and never rewrites it. The fix must not
    subscript the argv array with a computed numeric index at all -- neither
    special-cased per shell nor via a global array-base option.
    """
    normalized = str(tmp_path / "project" / "child")
    for shell in ("bash", "zsh"):
        args = _probe_rewrite_argv(
            shell, normalized, "--fork-session", "--root", "child"
        )
        assert args == ["--fork-session", "--root", normalized], (shell, args)


def test_rewrite_contract_root_argv_preserves_prompt_and_dashdash_payload(
    tmp_path: Path,
) -> None:
    """Direct scanner probe: only the root VALUE is rewritten -- everything
    around it, including a --prompt / `--` payload after it, stays byte for
    byte, in both shells.
    """
    normalized = str(tmp_path / "project" / "child")
    expected = [
        "--fork-session",
        "--root",
        normalized,
        "--prompt",
        "two",
        "words",
        "with",
        "--",
        "inside",
    ]
    for shell in ("bash", "zsh"):
        args = _probe_rewrite_argv(
            shell,
            normalized,
            "--fork-session",
            "--root",
            "child",
            "--prompt",
            "two",
            "words",
            "with",
            "--",
            "inside",
        )
        assert args == expected, (shell, args)


def test_nested_relative_root_survives_the_child_reparse_under_zsh(
    tmp_path: Path,
) -> None:
    """The bash P0 fix (test_nested_relative_root_survives_the_child_reparse)
    held only under bash: the same `--root child` case must open the correct
    nested project, and the hosted argv must carry the absolute rewrite, when
    the operator's login shell is zsh.
    """
    project = tmp_path / "mlx-batch-runner"
    project.mkdir(parents=True, exist_ok=True)
    nested = project / "child"
    nested.mkdir()

    result, launch = _run_entry(tmp_path, "vc-resume codex --root child", shell="zsh")

    assert result.returncode == 0, result.stderr
    assert launch is not None, result.stderr
    assert _working_directory(launch) == nested.resolve()

    hosted = _hosted_argv(launch)
    assert hosted[4] == "--root", hosted
    assert Path(hosted[5]).is_absolute(), f"a relative root crossed the cwd: {hosted}"
    assert Path(hosted[5]).resolve() == nested.resolve()


# --------------------------------------------------------------------------
# Process lifetime: the host must outlive its caller's process group
# --------------------------------------------------------------------------


def test_terminal_host_survives_synthetic_caller_group_death(tmp_path: Path) -> None:
    """S1 R5: the opened terminal host must not die when ITS CALLER's own
    process group is torn down after the caller has already returned.

    Evidence (public-start-lifetime.json/.log, S1 R5 brief): the real
    installed vc-start child exited 0 while its vc-terminal PID (37536)
    remained alive at second 12 -- but that terminal's PGID (37470) was the
    OUTER TOOL's group, not its own. Once that outer group was torn down,
    the terminal, its shell and its Frame client were all gone, though no
    signal was ever sent to any of them directly -- their group simply
    died. `disown` (the prior mechanism) only drops a job from the shell's
    OWN job table; under a non-interactive shell (no job control, the
    normal case for a scripted/agent caller) a backgrounded job keeps the
    SAME pgid as its caller and goes down with it.

    Reproduced directly here with an isolated, self-created,
    identity-verified synthetic "caller" process group -- never a Founder
    process, never anything outside this test's own process tree.
    """
    import signal

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    _install_canonical_launcher(home)
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    survived = tmp_path / "survived.marker"
    capture = tmp_path / "terminal-launch.json"
    generation = _fake_generation(tmp_path, capture)
    # Outlive the 1.5s bounded admission window before proving survival --
    # a host that exits inside that window would pass even under the bug.
    _write(
        generation / "bin" / "vc-terminal",
        "#!/bin/bash\n"
        "sleep 2\n"
        f"printf '%s\\n' \"$$\" > {shlex.quote(str(survived))}\n"
        "exit 0\n",
    )

    caller_pid_file = tmp_path / "caller.pid"
    script = "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{generation}"',
            f'printf "%s\\n" "$$" > {shlex.quote(str(caller_pid_file))}',
            "_vetcoders_open_entry_in_vc_terminal "
            + shlex.quote(str(generation / "bin" / "vc-start"))
            + " "
            + shlex.quote(str(project_dir)),
        ]
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["XDG_CONFIG_HOME"] = str(home / ".config")

    # The isolated synthetic caller: its own session AND process group,
    # created fresh for this test, never shared with pytest's own group.
    # DEVNULL, not PIPE: a pipe's write end stays open for as long as ANY
    # descendant holds it, which under the pre-fix code is the backgrounded
    # host too -- that would make process-exit detection below depend on
    # the very stdio coupling this fix removes, instead of testing the
    # caller's own lifetime independently of it.
    caller = subprocess.Popen(
        ["bash", "--noprofile", "--norc", "-c", script],
        cwd=project_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Identity check while it is certainly still alive: a freshly
    # start_new_session=True child is always its own session/group leader.
    caller_pgid = os.getpgid(caller.pid)
    assert caller_pgid == caller.pid, (
        "the synthetic caller was not its own session/group leader -- "
        "the test setup, not the fix, would be under test here"
    )
    assert caller_pgid not in (0, 1, os.getpgrp()), (
        "refusing to signal a shared/system process group"
    )

    try:
        caller.wait(timeout=30)
        assert caller.returncode == 0, caller.returncode
        recorded_pid = int(caller_pid_file.read_text().strip())
        assert recorded_pid == caller.pid, (
            "pid mismatch -- refusing to signal an unverified group"
        )
        assert not survived.exists(), (
            "the fake host finished inside the caller's own lifetime -- "
            "this proves nothing about surviving the caller's group death"
        )

        # The caller (standing in for the reported incident's "outer tool
        # call") is done. Tear down ITS ENTIRE process group now -- this
        # signal targets ONLY the pgid created and identity-verified above,
        # never a pid discovered by scanning, never a Founder process.
        try:
            os.killpg(caller_pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # nothing left in that group -- the host already detached out of it

        deadline = time.monotonic() + 5.0
        while not survived.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

        assert survived.exists(), (
            "the terminal host did not survive its caller's process group "
            "being torn down -- it is still coupled to the caller's "
            "session/pgid"
        )
    finally:
        if caller.poll() is None:
            try:
                os.killpg(caller_pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            caller.wait(timeout=5)


def test_bare_resume_without_tty_opens_terminal_under_zsh(tmp_path: Path) -> None:
    """The reported P0, under zsh: `local status=""` collides with zsh's
    readonly `$status` special parameter inside
    `_vetcoders_open_entry_in_vc_terminal` (S1 R3 probe: `read-only variable:
    status`, function still exit 0) -- the receipt-polling loop that decides
    whether the host admitted the launch must not silently read the wrong
    variable.
    """
    result, launch = _run_entry(tmp_path, "vc-resume codex", shell="zsh")

    assert result.returncode == 0, result.stderr
    assert launch is not None, f"no terminal was opened: {result.stderr}"
    assert "read-only variable" not in result.stderr, result.stderr
    hosted = _hosted_argv(launch)
    assert hosted[0].endswith("launch-primary-shell.zsh")

    # Nothing may be launched twice: no AICX pack in the escalating parent.
    assert not (tmp_path / "aicx-called.txt").exists()


# --------------------------------------------------------------------------
# Truthful admission: the foreground spawner's OWN failures, not the host's
# --------------------------------------------------------------------------
#
# S1 R5b (independent review of R5): the receipt-polling loop only ever saw
# the ABSENCE of a receipt file and reported that as "accepted... starting",
# whether that absence came from a host still opening (correct) or from the
# foreground python3 driver never having run at all (wrong). Both cases below
# reproduce a driver that never gets as far as spawning the detached writer,
# so no receipt can ever appear -- proven on both shells, matching how the
# original evidence (reports/S1-terminal-lifetime-R5-spawner-failure.json,
# reports/S1-terminal-lifetime-R5-popen-failure.json) was reproduced.


def _write_fake_python3(bin_dir: Path, body: str) -> Path:
    """A `python3` shim placed ahead of the real interpreter on PATH, used to
    force the foreground driver itself to fail before it ever spawns the
    detached writer. Its shebang points at THIS interpreter's real absolute
    path, never back through PATH, so it cannot recursively invoke itself.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    return _write(bin_dir / "python3", body)


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_interpreter_start_failure_is_not_reported_as_accepted(
    tmp_path: Path, shell: str
) -> None:
    """reports/S1-terminal-lifetime-R5-spawner-failure.json: a python3 that
    fails to even start the driver (here, SPAWNER_EXIT_42 -- it exits before
    reading its heredoc'd stdin at all) must never be reported as "accepted"
    or "starting". No writer was ever spawned, so the real terminal host
    (`vc-terminal`) must never be invoked either.
    """
    fake_bin = tmp_path / "fakebin"
    _write_fake_python3(fake_bin, f"#!{sys.executable}\nimport sys\nsys.exit(42)\n")

    result, launch = _run_entry(
        tmp_path,
        "vc-resume codex",
        extra_env={"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
        expect_launch=False,
        shell=shell,
    )

    assert launch is None, "the host was invoked despite the driver never running"
    assert result.returncode != 0, result.stdout + result.stderr
    assert "failed to start an independent terminal session" in result.stderr
    assert "accepted this launch" not in result.stderr
    assert "opened the Vibecrafted terminal" not in result.stderr


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_driver_popen_failure_is_not_reported_as_accepted(
    tmp_path: Path, shell: str
) -> None:
    """reports/S1-terminal-lifetime-R5-popen-failure.json: the REAL driver
    runs, but its own `subprocess.Popen` call -- the one spawning the
    detached writer, not the writer's later exec of the host -- raises
    OSError (e.g. a fork/resource limit). That must be caught and reported
    as a clean, single diagnostic: never an uncaught traceback, and never
    silently folded into "accepted... starting".
    """
    fake_bin = tmp_path / "fakebin"
    _write_fake_python3(
        fake_bin,
        f"#!{sys.executable}\n"
        "import sys, subprocess\n"
        "class _FailingPopen:\n"
        "    def __init__(self, *a, **kw):\n"
        "        raise OSError(11, 'R5B_REVIEW_SPAWN_REFUSED')\n"
        "subprocess.Popen = _FailingPopen\n"
        "exec(sys.stdin.read())\n",
    )

    result, launch = _run_entry(
        tmp_path,
        "vc-resume codex",
        extra_env={"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
        expect_launch=False,
        shell=shell,
    )

    assert launch is None, "the host was invoked despite the writer never spawning"
    assert result.returncode != 0, result.stdout + result.stderr
    assert "failed to start an independent terminal session" in result.stderr
    assert "R5B_REVIEW_SPAWN_REFUSED" in result.stderr, (
        "the underlying Popen failure was swallowed instead of surfaced: "
        + result.stderr
    )
    assert "Traceback" not in result.stderr, (
        "an uncaught exception leaked instead of a clean diagnostic: " + result.stderr
    )
    assert "accepted this launch" not in result.stderr
    assert "opened the Vibecrafted terminal" not in result.stderr
