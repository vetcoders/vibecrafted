"""A public declaration opens its workspace on a surface the Founder can see.

Founder repros (2026-09-09, installed 4.3.1 line), all from an agent shell
with no TTY that had inherited ``VC_FRAME_SESSION_NAME=vibecrafted`` from a
pane whose host was gone (the live sessions belonged to other projects):

    vibecrafted init codex --token-budget unmetered --prompt CONTINUITY
    vibecrafted operator codex --token-budget unmetered --prompt CONTINUITY
    vibecrafted fork codex --session current --prompt CONTINUITY

init/operator: extraction succeeded, the launcher adopted the stale marker as
its target, said the host was missing, ran ``attach --create-background
vibecrafted`` with the marker still inherited and hit Frame's own panic
(src/commands.rs:844), exit 2. fork: the parser accepted the call, then
"Session 'vibecrafted' not found" and "vc-frame refused the same-tab fork
pane; no replacement session or tab was created".

The Founder calls each of these a DECLARATION: it owes them the repository's
workspace and an actual interactive terminal or a usable attached client --
including when it is invoked by an agent tool with pipes for stdio. Printing
``vc-frame attach …`` is not completion. The contract proven here, with one
launch owner shared by init, operator, partner, resume and fork:

* the environment's claim to a surface is checked against the ENGINE: a frame
  marker or an explicit operator session counts only while the named session
  is live and someone is attached to it (``action list-clients``); dead,
  missing or unattended names are ambient context;
* with no surface, the public entry opens the product terminal on the
  declared repository and re-enters there with the exact declaration -- the
  native session id already resolved, the absolute repository, every prompt
  and execution option -- and the stale attachment context stripped;
* the escalating parent composes nothing, creates nothing and starts no
  provider; the child does each exactly once, in the order create -> tab ->
  handover, and a child that still has no terminal fails closed;
* inside a watched pane the in-workspace semantics stay (fork: same-tab pane);
* a rejected terminal launch is a failure, never a "launched".

Stubs: the catalogue owner, the Frame engine (refusing what the engine
refuses, answering ``list-clients`` the way 0.47.3 does), the terminal host
(records the launch), AICX and the provider command composers. The last cases
run against the REAL engine in an isolated sandbox.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

# The suite runs with --import-mode=importlib (pyproject), so a sibling test
# module is not importable by name; load the shared scene by file.
_DECLARED_SPEC = importlib.util.spec_from_file_location(
    "declared_workspace_scene",
    Path(__file__).with_name("test_resume_declared_workspace.py"),
)
assert _DECLARED_SPEC is not None and _DECLARED_SPEC.loader is not None
_declared = importlib.util.module_from_spec(_DECLARED_SPEC)
_DECLARED_SPEC.loader.exec_module(_declared)

FOREIGN_LIVE = _declared.FOREIGN_LIVE
IDENTITY_ENV = _declared.IDENTITY_ENV
MARKER_KEYS = _declared.MARKER_KEYS
NATIVE_SESSION = _declared.NATIVE_SESSION
PRIMARY_SHELL = _declared.PRIMARY_SHELL
SHELL_SH = _declared.SHELL_SH
STALE_MARKER = _declared.STALE_MARKER
VC_FRAME_STUB = _declared.VC_FRAME_STUB
Scene = _declared.Scene
_assert_no_foreign_mutation = _declared._assert_no_foreign_mutation
_assert_no_panic = _declared._assert_no_panic
_attaches = _declared._attaches
_creates = _declared._creates
_cwd_of = _declared._cwd_of
_expected_place = _declared._expected_place
_new_tabs = _declared._new_tabs
_root_aware_owner_cli = _declared._root_aware_owner_cli
_session_of = _declared._session_of
_shell_argv = _declared._shell_argv
_switches = _declared._switches
_tab_script = _declared._tab_script
_write = _declared._write

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK = REPO_ROOT / "scripts" / "vibecrafted"
CORE_PACKAGE = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core"

# The provider command composers reach into core (continuity extraction,
# interactive-command policy). They are not under test here; each stub records
# that -- and with which token budget -- it was asked, so a case can prove the
# composition happened exactly once and only where the launch happens.
COMPOSER_STUBS = (
    "_vetcoders_compose_init_prompt() { printf 'compose init\\n' >> \"$TEST_COMPOSE_CAPTURE\"; printf '/vc-init'; }\n"
    "_vetcoders_compose_operator_prompt() { printf 'compose operator\\n' >> \"$TEST_COMPOSE_CAPTURE\"; printf '/vc-operator'; }\n"
    "_vetcoders_compose_partner_prompt() { printf 'compose partner\\n' >> \"$TEST_COMPOSE_CAPTURE\"; printf '/vc-partner'; }\n"
    '_vetcoders_init_command_text() { printf \'init tool=%s budget=%s\\n\' "$1" "$5" >> "$TEST_COMMAND_CAPTURE"; printf \'%s --vc-face init --token-budget %s\' "$1" "$5"; }\n'
    '_vetcoders_operator_command_text() { printf \'operator tool=%s budget=%s\\n\' "$1" "$5" >> "$TEST_COMMAND_CAPTURE"; printf \'%s --vc-face operator --token-budget %s\' "$1" "$5"; }\n'
    '_vetcoders_partner_command_text() { printf \'partner tool=%s budget=%s\\n\' "$1" "$5" >> "$TEST_COMMAND_CAPTURE"; printf \'%s --vc-face partner --token-budget %s\' "$1" "$5"; }\n'
    "_vetcoders_aicx_resume_fallback() { printf 'called\\n' >> \"$TEST_AICX_CAPTURE\"; printf 'MODE=new_session\\n'; }\n"
)

FACES = {
    "init": "_vetcoders_skill_init",
    "operator": "_vetcoders_skill_operator",
    "partner": "_vetcoders_skill_partner",
}


def _run_face(
    scene: Scene,
    invocation: str,
    *,
    shell: str = "bash",
    extra_env: dict[str, str] | None = None,
    tty: bool = False,
    terminal_entry: bool = True,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a public shell face (init/operator/partner/resume) on the facade.

    ``terminal_entry`` marks the process as the child a terminal already
    opened; ``tty`` gives it a real controlling terminal. The default cwd is
    the scene's repository (a bare face declares the repository it runs in).
    """
    env = scene.env(extra_env)
    env["TEST_COMPOSE_CAPTURE"] = str(scene.tmp_path / "compose-called.txt")
    env["TEST_COMMAND_CAPTURE"] = str(scene.tmp_path / "command-composed.txt")
    if terminal_entry:
        env["VIBECRAFTED_TERMINAL_ENTRY"] = "1"
    script = "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{scene.generation}"',
            COMPOSER_STUBS,
            invocation,
            'printf "RC=[%s]\\n" "$?"',
            'printf "TARGET=[%s]\\n" "${VIBECRAFTED_OPERATOR_SESSION:-}"',
        ]
    )
    if tty:
        argv = [
            sys.executable,
            "-c",
            "import pty, sys; sys.exit(pty.spawn("
            + repr(_shell_argv(shell, script))
            + "))",
        ]
        stdin = None
    else:
        argv = _shell_argv(shell, script)
        stdin = subprocess.DEVNULL
    return subprocess.run(
        argv,
        check=False,
        cwd=cwd or scene.root,
        env=env,
        stdin=stdin,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _composed(scene: Scene) -> list[str]:
    path = scene.tmp_path / "command-composed.txt"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _composes(scene: Scene) -> list[str]:
    path = scene.tmp_path / "compose-called.txt"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _hosted(launch: dict) -> list[str]:
    argv = launch["argv"]
    return argv[argv.index("-e") + 1 :]


def _working_directory(launch: dict) -> Path:
    argv = launch["argv"]
    return Path(argv[argv.index("--working-directory") + 1]).resolve()


def _order(calls: list[dict]) -> tuple[int, int, int]:
    argvs = [c["argv"] for c in calls]
    created = next(i for i, a in enumerate(argvs) if "--create-background" in a)
    tab = next(i for i, a in enumerate(argvs) if "new-tab" in a and "--help" not in a)
    attach = next(i for i, a in enumerate(argvs) if a[:1] == ["attach"])
    return created, tab, attach


STALE = {
    "VC_FRAME": "1",
    "VC_FRAME_PANE_ID": "7",
    "VC_FRAME_SESSION_NAME": STALE_MARKER,
}


# --------------------------------------------------------------------------
# init / operator / partner: the Founder repro, from an agent shell
# --------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["init", "operator", "partner"])
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_face_from_stale_marker_without_tty_opens_the_terminal_with_exact_argv(
    tmp_path: Path, verb: str, shell: str
) -> None:
    """Baseline: the stale marker is adopted as the target, the host is
    missing, the create panics at src/commands.rs:844, exit 2 (init and
    operator alike -- one shared host path). Now the entry opens the product
    terminal on this repository and hands the child the same declaration,
    untouched: the unmetered budget the Founder chose and the prompt."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="vibecrafted")
    result = _run_face(
        scene,
        f"{FACES[verb]} codex --token-budget unmetered --prompt CONTINUITY",
        shell=shell,
        terminal_entry=False,
        extra_env=STALE,
    )
    calls = scene.calls()
    launch = scene.terminal_launch()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    _assert_no_panic(result, calls)
    assert "launch failed" not in result.stderr, result.stderr
    assert launch is not None, f"no terminal was opened: {result.stderr}"
    assert _working_directory(launch) == scene.root.resolve()
    assert _hosted(launch)[2:] == [
        verb,
        "codex",
        "--token-budget",
        "unmetered",
        "--prompt",
        "CONTINUITY",
    ], _hosted(launch)
    assert launch["boundary"] == "1", launch
    assert all(value is None for value in launch["markers"].values()), launch
    # The parent composed nothing and touched no session: the child does it,
    # once, where the provider actually starts.
    assert not _composes(scene) and not _composed(scene)
    assert not _creates(calls) and not _new_tabs(calls), calls
    assert not _attaches(calls) and not _switches(calls), calls
    _assert_no_foreign_mutation(calls, (STALE_MARKER, FOREIGN_LIVE))


def test_init_with_a_declared_repo_from_elsewhere_opens_the_terminal_on_it(
    tmp_path: Path,
) -> None:
    """`--repo` is the declared workspace: the window opens ON that repository
    and the child receives the absolute path, not the token as typed."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="repo with space")
    relative = os.path.relpath(scene.root, scene.cwd)
    result = _run_face(
        scene,
        f"_vetcoders_skill_init codex --repo {shlex.quote(relative)}",
        terminal_entry=False,
        extra_env=STALE,
        cwd=scene.cwd,
    )
    launch = scene.terminal_launch()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert launch is not None, result.stderr
    assert _working_directory(launch) == scene.root.resolve()
    assert _hosted(launch)[2:] == ["init", "codex", "--repo", str(scene.root)], _hosted(
        launch
    )


@pytest.mark.parametrize("verb", ["init", "operator"])
def test_face_child_creates_detached_hangs_the_tab_then_enters(
    tmp_path: Path, verb: str
) -> None:
    """The escalated child (real terminal, no inherited markers): the
    repository's own workspace session is created with the engine's detached
    form, the provider tab is hung on it with the composed command, and only
    then is the terminal handed over. Before this owner the init family
    prepared in the foreground and the tab appeared after the window closed."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="vibecrafted")
    result = _run_face(
        scene,
        f"{FACES[verb]} codex --token-budget unmetered --prompt CONTINUITY",
        tty=True,
        terminal_entry=True,
    )
    calls = scene.calls()
    place = _expected_place(scene)

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    _assert_no_panic(result, calls)
    creates = _creates(calls)
    assert len(creates) == 1 and creates[0]["argv"][-1] == place, calls
    assert creates[0]["VC_FRAME_SESSION_NAME"] is None, creates
    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == place, calls
    assert _cwd_of(tabs[0]) == scene.root, tabs
    assert f"codex --vc-face {verb} --token-budget unmetered" in _tab_script(tabs[0])
    attaches = _attaches(calls)
    assert len(attaches) == 1 and attaches[0]["argv"] == ["attach", place], calls
    assert attaches[0]["VC_FRAME_SESSION_NAME"] is None, attaches
    created_at, tab_at, attach_at = _order(calls)
    assert created_at < tab_at < attach_at, [c["argv"] for c in calls]
    # Exactly one composition, with the Founder's budget passed through.
    assert _composed(scene) == [f"{verb} tool=codex budget=unmetered"], _composed(scene)
    assert f"{verb} launched in workspace session: {place}" in result.stdout
    _assert_no_foreign_mutation(calls, (FOREIGN_LIVE,))


def test_init_child_reuses_a_live_workspace_and_still_enters_it(
    tmp_path: Path,
) -> None:
    """Repeat: the repository's session is already live -> no second create,
    the tab lands there, and the fresh terminal is attached to it."""
    scene = Scene(
        tmp_path, live=[FOREIGN_LIVE, "session-vibecrafted"], project="vibecrafted"
    )
    result = _run_face(
        scene, "_vetcoders_skill_init codex", tty=True, terminal_entry=True
    )
    calls = scene.calls()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert not _creates(calls), calls
    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == "session-vibecrafted", calls
    attaches = _attaches(calls)
    assert len(attaches) == 1 and attaches[0]["argv"] == [
        "attach",
        "session-vibecrafted",
    ]
    live = scene.live_file.read_text(encoding="utf-8").split()
    assert live.count("session-vibecrafted") == 1, live


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_operator_with_a_terminal_and_a_stale_marker_prepares_its_own_target(
    tmp_path: Path, shell: str
) -> None:
    """A real terminal but a stale marker (the Founder's "interactive
    context" for the operator repro): the marker is named as ambient context,
    never adopted, never resurrected; the repository's own session is created
    and entered."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="vibecrafted")
    result = _run_face(
        scene,
        "_vetcoders_skill_operator codex --token-budget unmetered --prompt CONTINUITY",
        shell=shell,
        tty=True,
        terminal_entry=False,
        extra_env=STALE,
    )
    calls = scene.calls()
    place = _expected_place(scene)

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    _assert_no_panic(result, calls)
    # A pty merges stderr into the captured stream.
    assert "ambient context" in result.stdout + result.stderr, result.stdout
    assert scene.terminal_launch(wait=1.0) is None, "a terminal was opened over a TTY"
    creates = _creates(calls)
    assert len(creates) == 1 and creates[0]["argv"][-1] == place, calls
    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == place, calls
    attaches = _attaches(calls)
    assert len(attaches) == 1 and attaches[0]["argv"] == ["attach", place], calls
    assert attaches[0]["VC_FRAME_SESSION_NAME"] is None, attaches
    _assert_no_foreign_mutation(calls, (STALE_MARKER, FOREIGN_LIVE))


@pytest.mark.parametrize("verb", ["init", "operator"])
def test_face_child_without_a_terminal_fails_closed(tmp_path: Path, verb: str) -> None:
    """The re-entry boundary is set (a terminal supposedly opened this) but
    there is still no PTY: never a second window, never a tab in a session
    nobody can see, never a "launched"."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="vibecrafted")
    result = _run_face(
        scene,
        f"{FACES[verb]} codex --prompt CONTINUITY",
        terminal_entry=True,
        extra_env=STALE,
    )
    calls = scene.calls()

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert "refusing to start" in result.stderr, result.stderr
    assert "launched in workspace session" not in result.stdout, result.stdout
    assert scene.terminal_launch(wait=1.0) is None
    assert not _creates(calls) and not _new_tabs(calls), calls
    _assert_no_foreign_mutation(calls, (STALE_MARKER, FOREIGN_LIVE))


# --------------------------------------------------------------------------
# The engine's word decides what a marker or an explicit session is worth
# --------------------------------------------------------------------------


def test_watched_live_marker_keeps_the_in_frame_path(tmp_path: Path) -> None:
    """An agent tool inside a pane of a session someone is attached to: the
    tab lands in that session, no window is opened."""
    scene = Scene(
        tmp_path, live=[FOREIGN_LIVE], project="vibecrafted", clients=[FOREIGN_LIVE]
    )
    result = _run_face(
        scene,
        "_vetcoders_skill_init codex --token-budget unmetered",
        terminal_entry=False,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "3",
            "VC_FRAME_SESSION_NAME": FOREIGN_LIVE,
        },
    )
    calls = scene.calls()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert scene.terminal_launch(wait=1.0) is None, (
        "a watched pane was rerouted to a window"
    )
    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == FOREIGN_LIVE, calls
    assert not _creates(calls) and not _attaches(calls), calls
    assert _composed(scene) == ["init tool=codex budget=unmetered"]


def test_live_but_unattended_marker_is_not_a_surface(tmp_path: Path) -> None:
    """The marker's session is live -- a background host with no client. A
    live NAME alone is not proof anyone would see the tab: open a terminal."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="vibecrafted", clients=[])
    result = _run_face(
        scene,
        "_vetcoders_skill_init codex",
        terminal_entry=False,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "3",
            "VC_FRAME_SESSION_NAME": FOREIGN_LIVE,
        },
    )
    launch = scene.terminal_launch()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert launch is not None, result.stderr
    assert _hosted(launch)[2:] == ["init", "codex"], _hosted(launch)
    assert not _new_tabs(scene.calls()) and not _creates(scene.calls())


def test_explicit_operator_session_that_is_watched_keeps_the_direct_path(
    tmp_path: Path,
) -> None:
    scene = Scene(
        tmp_path, live=[FOREIGN_LIVE], project="vibecrafted", clients=[FOREIGN_LIVE]
    )
    result = _run_face(
        scene,
        "_vetcoders_skill_init codex",
        terminal_entry=False,
        extra_env={"VIBECRAFTED_OPERATOR_SESSION": FOREIGN_LIVE},
    )
    calls = scene.calls()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert scene.terminal_launch(wait=1.0) is None
    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == FOREIGN_LIVE, calls


@pytest.mark.parametrize("live", [[], [FOREIGN_LIVE]], ids=["missing", "unattended"])
def test_explicit_operator_session_without_a_watcher_opens_a_terminal(
    tmp_path: Path, live: list[str]
) -> None:
    """VIBECRAFTED_OPERATOR_SESSION inherited from a dead pane (missing) or
    naming a background host (unattended) is untrusted env."""
    scene = Scene(tmp_path, live=live, project="vibecrafted", clients=[])
    result = _run_face(
        scene,
        "_vetcoders_skill_init codex",
        terminal_entry=False,
        extra_env={"VIBECRAFTED_OPERATOR_SESSION": FOREIGN_LIVE},
    )
    launch = scene.terminal_launch()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert launch is not None, result.stderr
    assert launch["markers"]["VIBECRAFTED_OPERATOR_SESSION"] is None, launch
    assert not _new_tabs(scene.calls()) and not _creates(scene.calls())


def test_rejected_terminal_host_is_a_failure_not_a_launch(tmp_path: Path) -> None:
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="vibecrafted")
    result = _run_face(
        scene,
        "_vetcoders_skill_init codex --prompt CONTINUITY",
        terminal_entry=False,
        extra_env={**STALE, "VC_TERMINAL_EXIT": "2"},
    )

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert "rejected this launch" in result.stderr, result.stderr
    assert not _composes(scene) and not _composed(scene)
    assert not _new_tabs(scene.calls()) and not _creates(scene.calls())


def test_bare_resume_from_stale_marker_without_tty_opens_the_terminal(
    tmp_path: Path,
) -> None:
    """The same predicate serves resume: a stale marker no longer keeps the
    bare resume on a direct path that would assemble AICX for a launch into a
    host nobody sees."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="vibecrafted")
    result = _run_face(
        scene, "_vetcoders_resume_agent codex", terminal_entry=False, extra_env=STALE
    )
    launch = scene.terminal_launch()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert launch is not None, result.stderr
    assert _hosted(launch)[2:] == ["resume", "codex"], _hosted(launch)
    assert not scene.aicx_capture.exists(), "the escalating parent assembled AICX"


# --------------------------------------------------------------------------
# fork through the REAL public deck, in an installed-shaped generation
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generation(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An installed-shaped generation: the tracked deck and the core package
    copied physically (the owner root is derived from the sourced file's
    physical location), a runtime manifest so the deck selects it as its
    owner, and stub engines/front doors that record instead of opening."""
    gen = tmp_path_factory.mktemp("declaration-generation")
    shutil.copy2(DECK, _write(gen / "scripts" / "vibecrafted", "#!/bin/bash\n"))
    shutil.copytree(
        CORE_PACKAGE,
        gen / "vibecrafted-core" / "vibecrafted_core",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (gen / "runtime-manifest.json").write_text("{}\n", encoding="utf-8")
    (gen / "VERSION").write_text("0.0.0-test\n", encoding="utf-8")
    _write(gen / "libexec" / "vc-frame", VC_FRAME_STUB)
    _write(gen / "bin" / "vc-frame", VC_FRAME_STUB)
    _write(gen / "libexec" / "vc-terminal", "#!/bin/bash\nexit 0\n")
    _write(
        gen / "bin" / "vc-terminal",
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"MARKER_KEYS = {MARKER_KEYS!r}\n"
        "open(os.environ['VC_TERMINAL_CAPTURE'], 'w').write(json.dumps("
        "{'argv': sys.argv[1:], 'cwd': os.getcwd(),"
        " 'boundary': os.environ.get('VIBECRAFTED_TERMINAL_ENTRY', ''),"
        " 'markers': {k: os.environ.get(k) for k in MARKER_KEYS}}))\n"
        "sys.exit(int(os.environ.get('VC_TERMINAL_EXIT', '0')))\n",
    )
    for verb in ("vc-start", "vibecrafted"):
        _write(gen / "bin" / verb, "#!/bin/bash\nexit 0\n")
    return gen


class DeckScene:
    """One isolated home, catalogue owner, Frame stub state, provider and
    AICX stubs for a public `vibecrafted fork` run through the deck."""

    def __init__(
        self,
        tmp_path: Path,
        generation: Path,
        *,
        live: list[str],
        project: str,
        clients: list[str] | None = None,
    ) -> None:
        self.tmp_path = tmp_path
        self.generation = generation
        self.home = tmp_path / "home"
        _write(
            self.home
            / ".config"
            / "vibecrafted"
            / "vc-terminal"
            / "launch-primary-shell.zsh",
            PRIMARY_SHELL.read_text(encoding="utf-8"),
        )
        _write(
            self.home
            / ".config"
            / "vibecrafted"
            / "vc-frame"
            / "layouts"
            / "operator.kdl",
            "layout {\n}\n",
        )
        self.owner = _root_aware_owner_cli(tmp_path / "owner-cli")
        self.frame_log = tmp_path / "frame.log"
        self.live_file = tmp_path / "live-sessions.txt"
        self.live_file.write_text("".join(f"{n}\n" for n in live), encoding="utf-8")
        self.clients_file: Path | None = None
        if clients is not None:
            self.clients_file = tmp_path / "attached-clients.txt"
            self.clients_file.write_text(
                "".join(f"{n}\n" for n in clients), encoding="utf-8"
            )
        self.terminal_capture = tmp_path / "terminal-launch.json"
        self.aicx_capture = tmp_path / "aicx-called.txt"
        self.stubs = tmp_path / "stubs"
        # AICX resolves `current` from THIS process's own context; the stub
        # records every call so a case can prove where the resolution ran.
        _write(
            self.stubs / "aicx",
            "#!/bin/bash\n"
            'printf "%s\\n" "$*" >> "$TEST_AICX_CAPTURE"\n'
            'if [[ "$1 $2" == "sessions current" ]]; then\n'
            f'  printf \'{{"session_id": "{NATIVE_SESSION}", "agent": "codex"}}\\n\'\n'
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
        )
        for provider in ("codex", "claude", "grok"):
            _write(self.stubs / provider, "#!/bin/bash\nexit 0\n")
        self.cwd = tmp_path / "elsewhere"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.root = tmp_path / project
        self.root.mkdir(parents=True, exist_ok=True)

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
        }
        for key in (
            *IDENTITY_ENV,
            "VIBECRAFTED_CORE_DIR",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME",
            "VIBECRAFTED_VC_FRAME_BIN",
        ):
            env.pop(key, None)
        env["HOME"] = str(self.home)
        env["VIBECRAFTED_HOME"] = str(self.home / ".vibecrafted")
        env["XDG_CONFIG_HOME"] = str(self.home / ".config")
        env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        env["PATH"] = f"{self.stubs}{os.pathsep}{env.get('PATH', '')}"
        env["VIBECRAFTED_PRODUCT_CORE_CLI"] = str(self.owner)
        env["VC_FRAME_LOG"] = str(self.frame_log)
        env["VC_FRAME_LIVE"] = str(self.live_file)
        if self.clients_file is not None:
            env["VC_FRAME_CLIENTS"] = str(self.clients_file)
        env["VC_TERMINAL_CAPTURE"] = str(self.terminal_capture)
        env["TEST_AICX_CAPTURE"] = str(self.aicx_capture)
        env.update(extra or {})
        return env

    def fork(
        self,
        *args: str,
        cwd: Path | None = None,
        extra_env: dict[str, str] | None = None,
        tty: bool = False,
        terminal_entry: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = self.env(extra_env)
        if terminal_entry:
            env["VIBECRAFTED_TERMINAL_ENTRY"] = "1"
        deck_argv = [
            "bash",
            str(self.generation / "scripts" / "vibecrafted"),
            "fork",
            *args,
        ]
        if tty:
            argv = [
                sys.executable,
                "-c",
                "import pty, sys; sys.exit(pty.spawn(" + repr(deck_argv) + "))",
            ]
            stdin = None
        else:
            argv = deck_argv
            stdin = subprocess.DEVNULL
        return subprocess.run(
            argv,
            check=False,
            cwd=cwd or self.root,
            env=env,
            stdin=stdin,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def calls(self) -> list[dict]:
        if not self.frame_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.frame_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def aicx_calls(self) -> list[str]:
        if not self.aicx_capture.exists():
            return []
        return self.aicx_capture.read_text(encoding="utf-8").splitlines()

    def terminal_launch(self, wait: float = 10.0) -> dict | None:
        deadline = time.monotonic() + wait
        while not self.terminal_capture.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self.terminal_capture.exists():
            return None
        return json.loads(self.terminal_capture.read_text(encoding="utf-8"))


def _panes(calls: list[dict]) -> list[dict]:
    return [c for c in calls if "new-pane" in c["argv"]]


def _pane_script(call: dict) -> str:
    argv = call["argv"]
    return Path(argv[argv.index("--") + 1]).read_text(encoding="utf-8")


def test_fork_current_from_stale_marker_without_tty_opens_the_repo_workspace_terminal(
    tmp_path: Path, generation: Path
) -> None:
    """The Founder's fork repro. Baseline: "Session 'vibecrafted' not found",
    "vc-frame refused the same-tab fork pane; no replacement session or tab
    was created". Now: `current` is resolved HERE (AICX reads this process's
    context), and the terminal is opened on the repository with the exact
    native id, the absolute repository, the runtime/placement and the prompt.
    The parent starts nothing."""
    scene = DeckScene(tmp_path, generation, live=[FOREIGN_LIVE], project="vibecrafted")
    result = scene.fork(
        "codex", "--session", "current", "--prompt", "CONTINUITY", extra_env=STALE
    )
    calls = scene.calls()
    launch = scene.terminal_launch()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "refused the same-tab fork pane" not in result.stderr, result.stderr
    assert "needs an attached vc-frame pane" not in result.stderr, result.stderr
    assert launch is not None, f"no terminal was opened: {result.stderr}"
    assert _working_directory(launch) == scene.root.resolve()
    assert _hosted(launch)[2:] == [
        "fork",
        "codex",
        "--session",
        NATIVE_SESSION,
        "--repo",
        str(scene.root.resolve()),
        "--runtime",
        "visible",
        "--placement",
        "right",
        "--prompt",
        "CONTINUITY",
    ], _hosted(launch)
    assert launch["boundary"] == "1"
    assert all(value is None for value in launch["markers"].values()), launch
    # Resolved exactly once, in the parent, before the ambient context went.
    assert [c for c in scene.aicx_calls() if c.startswith("sessions current")] == [
        "sessions current --json"
    ], scene.aicx_calls()
    assert not _panes(calls) and not _new_tabs(calls) and not _creates(calls), calls
    _assert_no_foreign_mutation(calls, (STALE_MARKER, FOREIGN_LIVE))


def test_fork_child_enters_the_repo_workspace_with_the_fork_as_a_tab(
    tmp_path: Path, generation: Path
) -> None:
    """The escalated child: an exact id (no AICX), the declared repository's
    own session created detached, the native fork command as a tab in it,
    the prompt kept interactive, the terminal handed over last."""
    scene = DeckScene(
        tmp_path, generation, live=[FOREIGN_LIVE], project="repo with space"
    )
    result = scene.fork(
        "codex",
        "--session",
        NATIVE_SESSION,
        "--repo",
        str(scene.root),
        "--prompt",
        "CONTINUITY",
        cwd=scene.cwd,
        tty=True,
        terminal_entry=True,
    )
    calls = scene.calls()
    place = "session-repo-with-space"

    assert result.returncode == 0, result.stdout + result.stderr
    _assert_no_panic(result, calls)
    assert not scene.aicx_calls(), "an exact id consulted AICX"
    creates = _creates(calls)
    assert len(creates) == 1 and creates[0]["argv"][-1] == place, calls
    assert creates[0]["VC_FRAME_SESSION_NAME"] is None, creates
    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == place, calls
    assert _cwd_of(tabs[0]) == scene.root.resolve(), tabs
    script = _tab_script(tabs[0])
    assert "codex fork --cd" in script and str(scene.root.resolve()) in script, script
    assert NATIVE_SESSION in script and "CONTINUITY" in script, script
    assert "codex exec" not in script, script
    attaches = _attaches(calls)
    assert len(attaches) == 1 and attaches[0]["argv"] == ["attach", place], calls
    created_at, tab_at, attach_at = _order(calls)
    assert created_at < tab_at < attach_at, [c["argv"] for c in calls]
    assert not _panes(calls), calls
    assert f"fork launched in workspace session: {place}" in result.stdout, (
        result.stdout
    )
    assert f"session:   {NATIVE_SESSION}" in result.stdout, result.stdout
    _assert_no_foreign_mutation(calls, (FOREIGN_LIVE,))


def test_fork_from_stale_marker_with_a_terminal_declares_the_workspace(
    tmp_path: Path, generation: Path
) -> None:
    """A real terminal, a stale marker: no "needs an attached pane" refusal,
    no window over a TTY -- the repository's workspace is prepared and entered."""
    scene = DeckScene(tmp_path, generation, live=[FOREIGN_LIVE], project="vibecrafted")
    result = scene.fork(
        "codex",
        "--session",
        NATIVE_SESSION,
        "--prompt",
        "CONTINUITY",
        extra_env=STALE,
        tty=True,
    )
    calls = scene.calls()
    place = "session-vibecrafted"

    assert result.returncode == 0, result.stdout + result.stderr
    assert scene.terminal_launch(wait=1.0) is None, "a terminal was opened over a TTY"
    assert "ambient context" in result.stdout + result.stderr, result.stdout
    assert _creates(calls) and _creates(calls)[0]["argv"][-1] == place, calls
    assert len(_new_tabs(calls)) == 1 and _session_of(_new_tabs(calls)[0]) == place
    assert _attaches(calls) and _attaches(calls)[0]["argv"] == ["attach", place]
    _assert_no_foreign_mutation(calls, (STALE_MARKER, FOREIGN_LIVE))


def test_fork_inside_a_watched_pane_keeps_the_same_tab_pane(
    tmp_path: Path, generation: Path
) -> None:
    """Inside a usable pane the in-workspace semantics stay: a pane next to
    the current one in the attached session, no window, no new session."""
    scene = DeckScene(
        tmp_path,
        generation,
        live=[FOREIGN_LIVE],
        project="vibecrafted",
        clients=[FOREIGN_LIVE],
    )
    result = scene.fork(
        "codex",
        "--session",
        NATIVE_SESSION,
        "-p",
        "CONTINUITY",
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "3",
            "VC_FRAME_SESSION_NAME": FOREIGN_LIVE,
        },
    )
    calls = scene.calls()

    assert result.returncode == 0, result.stdout + result.stderr
    assert scene.terminal_launch(wait=1.0) is None
    panes = _panes(calls)
    assert len(panes) == 1 and _session_of(panes[0]) == FOREIGN_LIVE, calls
    assert "--near-current-pane" in panes[0]["argv"], panes
    assert "Fork launched in current vc-frame tab" in result.stdout
    assert not _creates(calls) and not _new_tabs(calls) and not _attaches(calls), calls


def test_fork_rejected_terminal_is_reported_and_starts_nothing(
    tmp_path: Path, generation: Path
) -> None:
    scene = DeckScene(tmp_path, generation, live=[FOREIGN_LIVE], project="vibecrafted")
    result = scene.fork(
        "codex",
        "--session",
        NATIVE_SESSION,
        "--prompt",
        "CONTINUITY",
        extra_env={**STALE, "VC_TERMINAL_EXIT": "2"},
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "rejected this launch" in result.stderr, result.stderr
    assert "launched" not in result.stdout, result.stdout
    assert not scene.calls() or not (_panes(scene.calls()) or _new_tabs(scene.calls()))


def test_fork_still_refuses_what_it_does_not_support(
    tmp_path: Path, generation: Path
) -> None:
    """No silent acceptance: a trailing bare `--` and `--worktree` stay
    refused, before any identity resolution or terminal."""
    scene = DeckScene(tmp_path, generation, live=[FOREIGN_LIVE], project="vibecrafted")
    dashdash = scene.fork("codex", "--session", "current", "--", extra_env=STALE)
    worktree = scene.fork(
        "codex", "--session", "current", "--worktree", extra_env=STALE
    )

    assert dashdash.returncode == 2 and "Unknown fork argument: --" in dashdash.stderr
    assert worktree.returncode == 2 and "fork has no --worktree" in worktree.stderr
    assert not scene.aicx_calls()
    assert scene.terminal_launch(wait=0.5) is None


# --------------------------------------------------------------------------
# The real engine: what "attached client" evidence looks like
# --------------------------------------------------------------------------


def _installed_frame() -> Path | None:
    for candidate in (
        os.environ.get("VIBECRAFTED_VC_FRAME_BIN", ""),
        os.environ.get("VIBECRAFTED_RUNTIME_ROOT", "")
        and os.path.join(os.environ["VIBECRAFTED_RUNTIME_ROOT"], "libexec", "vc-frame"),
    ):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return Path(candidate)
    releases = Path.home() / ".local" / "share" / "vibecrafted" / "releases"
    if releases.is_dir():
        found = sorted(
            releases.glob("*/libexec/vc-frame"), key=lambda p: p.stat().st_mtime
        )
        if found:
            return found[-1]
    return None


_REAL_FRAME = _installed_frame()


@pytest.mark.skipif(_REAL_FRAME is None, reason="no installed vc-frame engine")
def test_native_engine_client_evidence_decides_the_surface(tmp_path: Path) -> None:
    """Against the REAL engine in a sandbox it can never share with the
    Founder (own short socket dir under /tmp, own config, own home):

    1. a session created with --create-background has NO attached client:
       the shell helpers report `none` / `unattended`, and a process carrying
       that session's markers with no TTY owes the operator a terminal;
    2. once a pty client attaches, the same helpers report `clients` /
       `usable` and the same process keeps the direct path;
    3. the sandbox session is killed afterwards and nothing is left behind.
    """
    assert _REAL_FRAME is not None
    sandbox = Path("/tmp") / f"vcde{os.getpid() % 100000}"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    (sandbox / "sock").mkdir(parents=True)
    (sandbox / "cfg").mkdir()
    (sandbox / "home").mkdir()
    (sandbox / "cfg" / "config.kdl").write_text(
        "keybinds clear-defaults=true {}\n", encoding="utf-8"
    )
    (sandbox / "layout.kdl").write_text("layout {\n  pane\n}\n", encoding="utf-8")
    session = f"vcde{os.getpid() % 100000}"

    env = os.environ.copy()
    for key in IDENTITY_ENV:
        env.pop(key, None)
    env.update(
        {
            "HOME": str(sandbox / "home"),
            "VIBECRAFTED_HOME": str(sandbox / "home" / ".vibecrafted"),
            "XDG_CONFIG_HOME": str(sandbox / "home" / ".config"),
            "VC_FRAME_SOCKET_DIR": str(sandbox / "sock"),
            "VC_FRAME_CONFIG_DIR": str(sandbox / "cfg"),
            "VC_FRAME_CONFIG_FILE": str(sandbox / "cfg" / "config.kdl"),
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(_REAL_FRAME),
        }
    )

    def frame(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(_REAL_FRAME), *args],
            check=False,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def probe() -> str:
        script = "\n".join(
            [
                f'source "{SHELL_SH}"',
                f'printf "CLIENTS=[%s]\\n" "$(_vetcoders_vc_frame_session_client_state {session})"',
                f'printf "SURFACE=[%s]\\n" "$(_vetcoders_vc_frame_surface_state {session})"',
                "if _vetcoders_needs_vc_terminal_entry; then echo NEEDS; else echo DIRECT; fi",
            ]
        )
        probe_env = dict(env)
        probe_env.update(
            {"VC_FRAME": "1", "VC_FRAME_PANE_ID": "1", "VC_FRAME_SESSION_NAME": session}
        )
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", script],
            check=False,
            cwd=REPO_ROOT,
            env=probe_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    client: subprocess.Popen[bytes] | None = None
    try:
        created = frame(
            "--new-session-with-layout",
            str(sandbox / "layout.kdl"),
            "attach",
            "--create-background",
            session,
        )
        assert created.returncode == 0, created.stderr
        deadline = time.monotonic() + 15
        while session not in frame("ls").stdout and time.monotonic() < deadline:
            time.sleep(0.25)
        assert session in frame("ls").stdout

        background = probe()
        assert "CLIENTS=[none]" in background, background
        assert "SURFACE=[unattended]" in background, background
        assert "NEEDS" in background, background

        client = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import os, pty, sys, time\n"
                    "pid, fd = pty.fork()\n"
                    "if pid == 0:\n"
                    "    os.execvp(sys.argv[1], [sys.argv[1], 'attach', sys.argv[2]])\n"
                    "t0 = time.time()\n"
                    "while time.time() - t0 < 40:\n"
                    "    try:\n"
                    "        os.read(fd, 4096)\n"
                    "    except OSError:\n"
                    "        break\n"
                ),
                str(_REAL_FRAME),
                session,
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        watched = ""
        while time.monotonic() < deadline:
            watched = probe()
            if "CLIENTS=[clients]" in watched:
                break
            time.sleep(0.5)
        assert "CLIENTS=[clients]" in watched, watched
        assert "SURFACE=[usable]" in watched, watched
        assert "DIRECT" in watched, watched
    finally:
        if client is not None:
            client.kill()
            client.wait(timeout=10)
        frame("kill-session", session)
        time.sleep(0.5)
        assert session not in frame("ls").stdout
        shutil.rmtree(sandbox, ignore_errors=True)
