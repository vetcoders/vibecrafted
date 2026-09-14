"""`vibecrafted resume <agent> --session <id> --root <repo>` is a declaration.

P0 (Founder, 2026-09-09, installed 4.3.1+g83c26f12): from a shell carrying a
stale frame marker (``VC_FRAME_SESSION_NAME=vibecrafted`` while the only live
session was another project's),

    vibecrafted resume codex --session 01a082bb-… --root ~/…/Sentry-Selfhosted

produced ``Session 'vibecrafted' not found; active session is '…'``, then
``hosting session missing; one-shot attach --create-background vibecrafted``,
then Frame's own panic at ``src/commands.rs:844`` ("trying to attach to the
current session"), then ``launch failed … target=vibecrafted/codex status=2``.
Founder: "to jest deklaracja", "powinienem załączyć się do nowego workspace".

Three identities were being conflated, and each case below keeps them apart:

* the **native agent session** (the provider id) is only ever read into argv;
* the **workspace identity** comes from the one canonical catalogue owner and
  names the place session that hosts the provider tab -- created when absent,
  reused when live, never duplicated;
* the **transport attachment** (``VC_FRAME_PANE_ID``/``VC_FRAME_SESSION_NAME``)
  is where THIS process happens to be. With an explicit repository it is
  ambient context: it never overrides the declared workspace, and a bounded
  create never inherits it (that inheritance is the native panic).

A caller with no visible surface at all (no TTY, and a marker the engine does
not confirm as a watched session) is not told an attach command: the public
entry opens the product terminal on the declared repository and re-enters
there, and the child -- which has a terminal -- attaches last.

Only the catalogue boundary and the Frame engine are stubbed. The stub refuses
what the real engine refuses, with the real engine's words, so a baseline that
targets the wrong session fails here the way it failed for the Founder. The
last case runs the bounded create against the REAL Frame binary when one is
installed, in an isolated socket/config/home sandbox.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "runtime"
SHELL_SH = RUNTIME / "shell" / "vetcoders.sh"
COMMON_SH = RUNTIME / "scripts" / "common.sh"
PRIMARY_SHELL = REPO_ROOT / "config" / "alacritty" / "launch-primary-shell.zsh"

NATIVE_SESSION = "01a082bb-1a48-7151-bc36-4e0372c0cecc"
STALE_MARKER = "vibecrafted"
FOREIGN_LIVE = "3more-studio"

# A worker run exports these; pytest inherits them. The public entry resolves
# identity through the canonical owner, so the fixture presents a blank slate.
IDENTITY_ENV = (
    "VIBECRAFTED_OPERATOR_SESSION",
    "VIBECRAFTED_WORKER_SESSION",
    "VIBECRAFTED_WORKSPACE_ID",
    "VIBECRAFTED_SESSION_ID",
    "VIBECRAFTED_WORKSPACE_INSTANCE_ID",
    "VIBECRAFTED_WORKSPACE_ROOT",
    "VIBECRAFTED_DECLARED_WORKSPACE_ROOT",
    "VIBECRAFTED_BUILD_ID",
    "VIBECRAFTED_PENDING_VC_FRAME_ATTACH",
    "VIBECRAFTED_PENDING_VC_FRAME_SWITCH",
    "VIBECRAFTED_PREPARED_VC_FRAME_SESSION",
    "VIBECRAFTED_TERMINAL_ENTRY",
    "VIBECRAFTED_TERMINAL_ENTRY_OWNER",
    # The suite-wide no-PTY create bypass (tests/conftest.py) would let a
    # caller with no terminal create and "enter" a session; these scenes model
    # real callers, with a pty where a terminal is meant.
    "VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME",
    "VIBECRAFTED_ROOT",
    "VIBECRAFTED_RUNTIME_ROOT",
    "VIBECRAFTED_RUNTIME_BIN",
    "VIBECRAFTED_RUNTIME_HOME",
    "VIBECRAFTED_PYTHON",
    "VIBECRAFTED_RUN_ID",
    "VIBECRAFTED_RUN_LOCK",
    "SPAWN_ROOT",
    "VC_FRAME",
    "VC_FRAME_PANE_ID",
    "VC_FRAME_SESSION_NAME",
    "ZELLIJ",
    "ZELLIJ_PANE_ID",
    "ZELLIJ_SESSION_NAME",
)

# Attachment-context keys a terminal child must never inherit from the process
# that opened it (the window is, by construction, outside that frame).
MARKER_KEYS = (
    "VC_FRAME",
    "VC_FRAME_PANE_ID",
    "VC_FRAME_SESSION_NAME",
    "ZELLIJ",
    "ZELLIJ_PANE_ID",
    "ZELLIJ_SESSION_NAME",
    "VIBECRAFTED_OPERATOR_SESSION",
)

# A stand-in for the Frame engine that refuses what the engine refuses:
#   * an action addressed to a session that is not live fails with the engine's
#     own "Session '…' not found" wording (src/commands.rs:426/476);
#   * `attach [--create-background] NAME` panics like src/commands.rs:844 when
#     the inherited session marker equals NAME -- the engine mirrors
#     VC_FRAME_SESSION_NAME into ZELLIJ_SESSION_NAME first, so either counts;
#   * a plain `attach` needs a TTY.
# Every invocation is logged with the markers it inherited, so a test can prove
# WHICH env a create or attach ran under, not just that it ran.
VC_FRAME_STUB = """#!/usr/bin/env python3
import json, os, sys, time

argv = sys.argv[1:]
log = os.environ.get("VC_FRAME_LOG", "")
live_file = os.environ.get("VC_FRAME_LIVE", "")
marker = os.environ.get("ZELLIJ_SESSION_NAME") or os.environ.get("VC_FRAME_SESSION_NAME")


def record(extra=None):
    if not log:
        return
    entry = {
        "argv": argv,
        "cwd": os.getcwd(),
        "VC_FRAME_SESSION_NAME": os.environ.get("VC_FRAME_SESSION_NAME"),
        "ZELLIJ_SESSION_NAME": os.environ.get("ZELLIJ_SESSION_NAME"),
        "VC_FRAME_PANE_ID": os.environ.get("VC_FRAME_PANE_ID"),
    }
    entry.update(extra or {})
    with open(log, "a") as handle:
        handle.write(json.dumps(entry) + "\\n")


def live_sessions():
    if live_file and os.path.exists(live_file):
        return [line.strip() for line in open(live_file) if line.strip()]
    return []


def remember(name):
    if live_file and name:
        with open(live_file, "a") as handle:
            handle.write("%s\\n" % name)


def forget(name):
    if live_file and os.path.exists(live_file):
        rest = [n for n in live_sessions() if n != name]
        with open(live_file, "w") as handle:
            handle.write("".join("%s\\n" % n for n in rest))


def not_found(name):
    live = live_sessions()
    if len(live) == 1:
        sys.stderr.write("Session '%s' not found; active session is '%s'\\n" % (name, live[0]))
    elif live:
        sys.stderr.write("Session '%s' not found. The following sessions are active:\\n" % name)
        for n in live:
            sys.stderr.write(n + "\\n")
    else:
        sys.stderr.write("There is no active session!\\n")
    sys.exit(1)


def panic_if_current(target):
    if marker and marker == target:
        record({"panic": True})
        sys.stderr.write(
            "thread 'main' panicked at src/commands.rs:844:21:\\n"
            'You are trying to attach to the current session ("%s"). '
            "This is not supported.\\n" % target
        )
        sys.exit(101)


record()

if "--help" in argv:
    print("--after-base   place the tab after the base card")
    print("--no-focus     do not focus the new tab")
    sys.exit(0)

if argv[:1] in (["ls"], ["list-sessions"]):
    for name in live_sessions():
        print("%s [Created 1s ago]" % name)
    sys.exit(0)

session = None
rest = argv
if rest[:1] == ["--session"]:
    session = rest[1]
    rest = rest[2:]
if rest[:1] == ["--new-session-with-layout"]:
    rest = rest[2:]

if rest[:2] == ["attach", "--create-background"]:
    name = rest[2] if len(rest) > 2 else session
    panic_if_current(name)
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
    panic_if_current(target)
    if target not in live_sessions():
        not_found(target)
    if not sys.stdin.isatty():
        sys.stderr.write("vc-frame: stdin is not a terminal (TTY); cannot start an interactive session.\\n")
        sys.exit(1)
    time.sleep(0.2)
    sys.exit(0)

if rest[:1] == ["kill-session"] or rest[:1] == ["delete-session"]:
    forget(rest[1] if len(rest) > 1 else session)
    sys.exit(0)

if rest[:1] == ["action"]:
    target = session or marker
    if not target or target not in live_sessions():
        not_found(target or "")
    verb = rest[1] if len(rest) > 1 else ""
    if verb == "new-tab":
        print("1")
        sys.exit(0)
    if verb == "switch-session":
        wanted = rest[2] if len(rest) > 2 else ""
        if wanted not in live_sessions():
            not_found(wanted)
        sys.exit(0)
    if verb == "list-tabs":
        print("TAB_ID  POSITION  NAME")
        sys.exit(0)
    if verb == "list-clients":
        # vc-frame 0.47.3: a header, then one row per attached client. With
        # VC_FRAME_CLIENTS unset every live session counts as watched; when it
        # names a file, only the sessions listed there have a client.
        print("CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND")
        clients_file = os.environ.get("VC_FRAME_CLIENTS", "")
        if not clients_file:
            print("1         terminal_1     zsh ")
        elif os.path.exists(clients_file) and target in [
            line.strip() for line in open(clients_file) if line.strip()
        ]:
            print("1         terminal_1     zsh ")
        sys.exit(0)
    sys.exit(0)

sys.exit(0)
"""


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _initialize_fixture_repo(root: Path) -> None:
    """Give every declared workspace the minimum valid Git identity."""
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"], cwd=root, check=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _root_aware_owner_cli(path: Path) -> Path:
    """The canonical catalogue owner, answering for the root it was asked about.

    `workspace resolve --root R --env` binds R to `session-<basename>`; the
    binding therefore tells which root was actually bound. Every other verb
    (session-attach receipts) is accepted.
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
        "  tag=\"$(printf '%s' \"$tag\" | tr -c 'A-Za-z0-9\\n' '-')\"\n"
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


def _generation(root: Path, terminal_capture: Path) -> Path:
    """A generation tree strict enough for the real resolvers to accept."""
    generation = root / "generation"
    _write(generation / "libexec" / "vc-terminal", "#!/bin/bash\nexit 0\n")
    _write(generation / "libexec" / "vc-frame", VC_FRAME_STUB)
    _write(generation / "bin" / "vc-frame", VC_FRAME_STUB)
    _write(
        generation / "bin" / "vc-terminal",
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"MARKER_KEYS = {MARKER_KEYS!r}\n"
        f"open({str(terminal_capture)!r}, 'w').write(json.dumps("
        "{'argv': sys.argv[1:], 'cwd': os.getcwd(),"
        " 'boundary': os.environ.get('VIBECRAFTED_TERMINAL_ENTRY', ''),"
        " 'boundary_owner': os.environ.get('VIBECRAFTED_TERMINAL_ENTRY_OWNER', ''),"
        " 'markers': {k: os.environ.get(k) for k in MARKER_KEYS}}))\n"
        f"sys.exit(int(os.environ.get('VC_TERMINAL_EXIT', '0')))\n",
    )
    for verb in ("vc-start", "vibecrafted"):
        _write(generation / "bin" / verb, "#!/bin/bash\nexit 0\n")
    return generation


class Scene:
    """One isolated home, generation, catalogue owner and Frame stub."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        live: list[str],
        project: str,
        clients: list[str] | None = None,
    ) -> None:
        self.tmp_path = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir(parents=True, exist_ok=True)
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
        self.terminal_capture = tmp_path / "terminal-launch.json"
        self.generation = _generation(tmp_path, self.terminal_capture)
        self.owner = _root_aware_owner_cli(tmp_path / "owner-cli")
        self.frame_log = tmp_path / "frame.log"
        self.live_file = tmp_path / "live-sessions.txt"
        self.live_file.write_text("".join(f"{n}\n" for n in live), encoding="utf-8")
        # None: every live session is watched (older-engine shape); a list:
        # exactly these live sessions have an attached client.
        self.clients_file: Path | None = None
        if clients is not None:
            self.clients_file = tmp_path / "attached-clients.txt"
            self.clients_file.write_text(
                "".join(f"{n}\n" for n in clients), encoding="utf-8"
            )
        self.aicx_capture = tmp_path / "aicx-called.txt"
        # The caller's cwd is deliberately NOT the declared root.
        self.cwd = tmp_path / "elsewhere"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.root = tmp_path / project
        self.root.mkdir(parents=True, exist_ok=True)
        _initialize_fixture_repo(self.root)

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        for key in IDENTITY_ENV:
            env.pop(key, None)
        env["HOME"] = str(self.home)
        env["VIBECRAFTED_HOME"] = str(self.home / ".vibecrafted")
        env["XDG_CONFIG_HOME"] = str(self.home / ".config")
        env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        env["VIBECRAFTED_PRODUCT_CORE_CLI"] = str(self.owner)
        env["VC_FRAME_LOG"] = str(self.frame_log)
        env["VC_FRAME_LIVE"] = str(self.live_file)
        if self.clients_file is not None:
            env["VC_FRAME_CLIENTS"] = str(self.clients_file)
        env["TEST_AICX_CAPTURE"] = str(self.aicx_capture)
        env.update(extra or {})
        return env

    def calls(self) -> list[dict]:
        if not self.frame_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.frame_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def terminal_launch(self, wait: float = 10.0) -> dict | None:
        deadline = time.monotonic() + wait
        while not self.terminal_capture.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self.terminal_capture.exists():
            return None
        return json.loads(self.terminal_capture.read_text(encoding="utf-8"))


def _shell_argv(shell: str, script: str) -> list[str]:
    if shell == "zsh":
        return ["zsh", "-f", "-c", script]
    return ["bash", "--noprofile", "--norc", "-c", script]


def _entry_script(scene: Scene, invocation: str) -> str:
    return "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{scene.generation}"',
            # An explicit --session never assembles a continuity pack; record
            # any call so the assertion can say so.
            (
                "_vetcoders_aicx_resume_fallback() { printf 'called\\n' "
                ">> \"$TEST_AICX_CAPTURE\"; printf 'MODE=new_session\\n'; }"
            ),
            invocation,
            'printf "RC=[%s]\\n" "$?"',
            'printf "TARGET=[%s]\\n" "${VIBECRAFTED_OPERATOR_SESSION:-}"',
            'printf "WORKSPACE_ROOT=[%s]\\n" "${VIBECRAFTED_WORKSPACE_ROOT:-}"',
            'printf "DECLARED=[%s]\\n" "${VIBECRAFTED_DECLARED_WORKSPACE_ROOT:-}"',
        ]
    )


def _run_resume(
    scene: Scene,
    invocation: str,
    *,
    shell: str = "bash",
    extra_env: dict[str, str] | None = None,
    tty: bool = False,
    terminal_entry: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the REAL public resume boundary (`_vetcoders_resume_agent`).

    ``terminal_entry`` marks the process as the child a terminal already
    opened (the shape after the no-TTY escalation); the one escalation case
    turns it off. ``tty`` gives the entry a real controlling terminal.
    """
    env = scene.env(extra_env)
    if terminal_entry:
        env["VIBECRAFTED_TERMINAL_ENTRY"] = "1"
        env["VIBECRAFTED_TERMINAL_ENTRY_OWNER"] = str(
            scene.generation / "bin" / "vibecrafted"
        )
    script = _entry_script(scene, invocation)
    if tty:
        argv = [
            sys.executable,
            "-c",
            (
                "import pty, sys; sys.exit(pty.spawn("
                + repr(_shell_argv(shell, script))
                + "))"
            ),
        ]
        stdin = None
    else:
        argv = _shell_argv(shell, script)
        stdin = subprocess.DEVNULL
    return subprocess.run(
        argv,
        check=False,
        cwd=scene.cwd,
        env=env,
        stdin=stdin,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _declared(scene: Scene, flag: str = "--root") -> str:
    return (
        f"_vetcoders_resume_agent codex --session {NATIVE_SESSION} "
        f"{flag} {shlex.quote(str(scene.root))}"
    )


def _expected_place(scene: Scene) -> str:
    tag = "".join(ch if ch.isalnum() else "-" for ch in scene.root.name)
    return f"session-{tag}"


def _creates(calls: list[dict]) -> list[dict]:
    return [c for c in calls if "--create-background" in c["argv"]]


def _new_tabs(calls: list[dict]) -> list[dict]:
    return [c for c in calls if "new-tab" in c["argv"] and "--help" not in c["argv"]]


def _attaches(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["argv"][:1] == ["attach"]]


def _switches(calls: list[dict]) -> list[dict]:
    return [c for c in calls if "switch-session" in c["argv"]]


def _session_of(call: dict) -> str | None:
    argv = call["argv"]
    if argv[:1] == ["--session"]:
        return argv[1]
    return None


def _tab_script(call: dict) -> str:
    argv = call["argv"]
    return Path(argv[argv.index("--") + 1]).read_text(encoding="utf-8")


def _tab_admission(call: dict) -> dict[str, object]:
    """Read the private interactive receipt referenced by a provider tab.

    The tab script deliberately carries only the canonical private handoff,
    rather than an expanded provider command.  The receipt is therefore the
    authoritative declaration boundary for assertions about agent identity and
    root selection.
    """
    command = shlex.split(_tab_script(call), comments=True)[-1]
    tokens = shlex.split(command)
    assert "interactive-launch" in tokens
    admission_file = Path(tokens[tokens.index("--admission-file") + 1])
    return json.loads(admission_file.read_text(encoding="utf-8"))


def _assert_resume_admission(admission: dict[str, object], scene: Scene) -> None:
    assert admission["agent"] == "codex"
    assert admission["skill"] == "resume"
    assert admission["agent_session_id"] == NATIVE_SESSION
    assert admission["root"] == str(scene.root)
    selection = admission["session_selection"]
    assert isinstance(selection, dict)
    assert selection["agent_session_id"] == NATIVE_SESSION
    assert selection["selection_root"] == str(scene.root)
    assert selection["session_selector"] == NATIVE_SESSION


def _cwd_of(call: dict) -> Path:
    argv = call["argv"]
    return Path(argv[argv.index("--cwd") + 1])


def _assert_no_panic(
    result: subprocess.CompletedProcess[str], calls: list[dict]
) -> None:
    assert "commands.rs:844" not in result.stderr, result.stderr
    assert not [c for c in calls if c.get("panic")], calls


def _assert_no_foreign_mutation(calls: list[dict], foreign: tuple[str, ...]) -> None:
    """No tab, kill, delete or create ever addressed an ambient session."""
    for call in calls:
        argv = call["argv"]
        if argv[:1] in (["kill-session"], ["delete-session"]):
            raise AssertionError(f"a session was killed or deleted: {argv}")
        target = _session_of(call)
        if target in foreign and ("new-tab" in argv or "attach" in argv):
            raise AssertionError(f"the ambient session {target} was mutated: {argv}")
        if "--create-background" in argv and argv[-1] in foreign:
            raise AssertionError(f"an ambient session name was (re)created: {argv}")


# --------------------------------------------------------------------------
# The reported P0: stale attached marker + explicit different repo
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_explicit_root_from_stale_attached_marker_opens_the_declared_workspace(
    tmp_path: Path, shell: str
) -> None:
    """The exact shape the Founder hit, without a TTY (an agent shell).

    Baseline: the in-frame branch adopts the stale marker `vibecrafted` as the
    target, the tab action fails with "Session 'vibecrafted' not found; active
    session is '3more-studio'", the bounded create inherits the marker and
    panics at src/commands.rs:844, and the launch fails with status 2. The
    first cut then prepared the workspace and printed `vc-frame attach …`,
    which the parent rejected: a declaration ENTERS the workspace.

    Now: the stale marker is not a surface (the engine has no such session),
    so the public entry opens the product terminal ON the declared repository
    and hands the child the same declaration -- native id, absolute root --
    with the stale attachment context stripped. The parent itself creates,
    launches and assembles nothing.
    """
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="Sentry-Selfhosted")
    result = _run_resume(
        scene,
        _declared(scene),
        shell=shell,
        terminal_entry=False,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "7",
            "VC_FRAME_SESSION_NAME": STALE_MARKER,
        },
    )
    calls = scene.calls()
    launch = scene.terminal_launch()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    _assert_no_panic(result, calls)
    assert "launch failed" not in result.stderr, result.stderr
    assert launch is not None, f"no terminal was opened: {result.stderr}"
    argv = launch["argv"]
    assert (
        Path(argv[argv.index("--working-directory") + 1]).resolve()
        == scene.root.resolve()
    )
    hosted = argv[argv.index("-e") + 1 :]
    assert hosted[2:] == [
        "resume",
        "codex",
        "--session",
        NATIVE_SESSION,
        "--root",
        str(scene.root),
    ], hosted
    # The child is outside the frame the parent inherited its markers from.
    assert launch["boundary"] == "1", launch
    assert all(value is None for value in launch["markers"].values()), launch
    # The escalating parent does nothing else: no create, no tab, no client,
    # no AICX, nothing addressed to the stale or the foreign session.
    assert not _creates(calls) and not _new_tabs(calls), calls
    assert not _attaches(calls) and not _switches(calls), calls
    _assert_no_foreign_mutation(calls, (STALE_MARKER, FOREIGN_LIVE))
    assert not scene.aicx_capture.exists(), "an explicit --session assembled AICX"
    assert "vc-frame attach" not in result.stderr, result.stderr


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_inherited_unqualified_terminal_entry_opens_requested_root_before_aicx(
    tmp_path: Path, shell: str
) -> None:
    """A bare inherited boundary cannot give an unrelated Frame client away."""
    scene = Scene(
        tmp_path,
        live=[FOREIGN_LIVE],
        clients=[FOREIGN_LIVE],
        project="loctree-suite",
    )
    result = _run_resume(
        scene,
        f"_vetcoders_resume_agent codex --root {shlex.quote(str(scene.root))}",
        shell=shell,
        terminal_entry=False,
        extra_env={
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "7",
            "VC_FRAME_SESSION_NAME": FOREIGN_LIVE,
            "ZELLIJ_SESSION_NAME": FOREIGN_LIVE,
        },
    )
    launch = scene.terminal_launch()
    calls = scene.calls()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert launch is not None, f"no terminal was opened: {result.stderr}"
    argv = launch["argv"]
    assert (
        Path(argv[argv.index("--working-directory") + 1]).resolve()
        == scene.root.resolve()
    )
    hosted = argv[argv.index("-e") + 1 :]
    assert hosted[2:] == ["resume", "codex", "--root", str(scene.root)], hosted
    assert launch["boundary"] == "1", launch
    assert launch["boundary_owner"] == str(scene.generation / "bin" / "vibecrafted")
    assert all(value is None for value in launch["markers"].values()), launch
    assert not scene.aicx_capture.exists(), "AICX ran before terminal admission"
    assert not _creates(calls) and not _new_tabs(calls), calls
    assert not _attaches(calls) and not _switches(calls), calls
    _assert_no_foreign_mutation(calls, (FOREIGN_LIVE,))


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_owned_terminal_boundary_is_consumed_before_a_descendant_resume(
    tmp_path: Path, shell: str
) -> None:
    """The immediate terminal child does not loop, but its descendant admits.

    A provider can launch another agent from a different checkout. Its process
    must not inherit the terminal child's recursion exemption merely because
    both descendants retain the same product generation on PATH.
    """
    scene = Scene(tmp_path, live=[], project="loctree-suite")
    descendant = "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{scene.generation}"',
            f"_vetcoders_resume_agent codex --session {NATIVE_SESSION} --root {shlex.quote(str(scene.root))}",
            'printf "DESCENDANT_RC=[%s]\\n" "$?"',
        ]
    )
    script = "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{scene.generation}"',
            "if _vetcoders_needs_vc_terminal_entry; then echo IMMEDIATE_NEEDS; else echo IMMEDIATE_DIRECT; fi",
            'if [[ -z "${VIBECRAFTED_TERMINAL_ENTRY:-}" && -z "${VIBECRAFTED_TERMINAL_ENTRY_OWNER:-}" ]]; then echo EXPORTED_BOUNDARY_CONSUMED; fi',
            f"env {shell} {'--noprofile --norc' if shell == 'bash' else '-f'} -c {shlex.quote(descendant)}",
        ]
    )
    result = subprocess.run(
        _shell_argv(shell, script),
        check=False,
        cwd=scene.cwd,
        env=scene.env(
            {
                "_vetcoders_vc_frame_loaded_root": str(scene.generation),
                "VIBECRAFTED_TERMINAL_ENTRY": "1",
                "VIBECRAFTED_TERMINAL_ENTRY_OWNER": str(
                    scene.generation / "bin" / "vibecrafted"
                ),
            }
        ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    launch = scene.terminal_launch()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "IMMEDIATE_DIRECT" in result.stdout, result.stdout + result.stderr
    assert "IMMEDIATE_NEEDS" not in result.stdout, result.stdout + result.stderr
    assert "EXPORTED_BOUNDARY_CONSUMED" in result.stdout, result.stdout + result.stderr
    assert "DESCENDANT_RC=[0]" in result.stdout, result.stdout + result.stderr
    assert launch is not None, (
        f"descendant did not receive one terminal: {result.stderr}"
    )
    assert launch["boundary"] == "1", launch
    assert launch["boundary_owner"] == str(scene.generation / "bin" / "vibecrafted")


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_same_root_watched_ambient_marker_does_not_bypass_no_tty_admission(
    tmp_path: Path, shell: str
) -> None:
    """Another client's matching workspace is not this pipe's surface proof."""
    scene = Scene(
        tmp_path,
        live=["session-loctree-suite"],
        clients=["session-loctree-suite"],
        project="loctree-suite",
    )
    result = _run_resume(
        scene,
        _declared(scene),
        shell=shell,
        terminal_entry=False,
        extra_env={
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "7",
            "VC_FRAME_SESSION_NAME": "session-loctree-suite",
            "ZELLIJ_SESSION_NAME": "session-loctree-suite",
        },
    )
    launch = scene.terminal_launch()
    calls = scene.calls()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert launch is not None, f"no terminal was opened: {result.stderr}"
    assert (
        Path(launch["argv"][launch["argv"].index("--working-directory") + 1]).resolve()
        == scene.root.resolve()
    )
    assert launch["boundary"] == "1", launch
    assert not _creates(calls) and not _new_tabs(calls), calls
    assert not _attaches(calls) and not _switches(calls), calls


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_child_shape_without_a_terminal_fails_closed_before_creating(
    tmp_path: Path, shell: str
) -> None:
    """The escalated-child shape (re-entry boundary set) but still no PTY: a
    broken terminal host, or a bypassed entry. The declaration must not leave
    a workspace session behind that nobody can see, nor claim a launch."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="Sentry-Selfhosted")
    result = _run_resume(
        scene,
        _declared(scene),
        shell=shell,
        terminal_entry=True,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "7",
            "VC_FRAME_SESSION_NAME": STALE_MARKER,
        },
    )
    calls = scene.calls()

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert "cannot be entered from this process" in result.stderr, result.stderr
    assert "Resume launched" not in result.stdout, result.stdout
    assert scene.terminal_launch(wait=1.0) is None
    assert not _creates(calls) and not _new_tabs(calls), calls
    assert not _attaches(calls) and not _switches(calls), calls
    _assert_no_foreign_mutation(calls, (STALE_MARKER, FOREIGN_LIVE))
    assert not scene.aicx_capture.exists()


def test_stale_marker_with_a_terminal_enters_the_declared_workspace(
    tmp_path: Path,
) -> None:
    """Same stale marker, but the Founder has a real terminal: after the tab
    exists the terminal is handed to the declared workspace by a fresh native
    client that inherits none of the stale attachment context."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="Sentry-Selfhosted")
    result = _run_resume(
        scene,
        _declared(scene),
        tty=True,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "7",
            "VC_FRAME_SESSION_NAME": STALE_MARKER,
        },
    )
    calls = scene.calls()
    place = _expected_place(scene)

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    _assert_no_panic(result, calls)
    attaches = _attaches(calls)
    assert len(attaches) == 1, calls
    assert attaches[0]["argv"] == ["attach", place], attaches
    assert attaches[0]["VC_FRAME_SESSION_NAME"] is None, attaches
    assert attaches[0]["ZELLIJ_SESSION_NAME"] is None, attaches
    # Order: create -> provider tab -> handover, the tab never after the attach.
    order = [c["argv"] for c in calls]
    created_at = next(i for i, a in enumerate(order) if "--create-background" in a)
    tab_at = next(
        i for i, a in enumerate(order) if "new-tab" in a and "--help" not in a
    )
    attach_at = next(i for i, a in enumerate(order) if a[:1] == ["attach"])
    assert created_at < tab_at < attach_at, order
    _assert_no_foreign_mutation(calls, (STALE_MARKER, FOREIGN_LIVE))


# --------------------------------------------------------------------------
# Attached to a LIVE frame elsewhere: the client is moved, never the target
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_live_attached_client_elsewhere_is_switched_onto_the_declared_workspace(
    tmp_path: Path, shell: str
) -> None:
    """Baseline: the provider tab lands in the attached foreign session."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="project-b")
    result = _run_resume(
        scene,
        _declared(scene),
        shell=shell,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "3",
            "VC_FRAME_SESSION_NAME": FOREIGN_LIVE,
        },
    )
    calls = scene.calls()
    place = _expected_place(scene)

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    _assert_no_panic(result, calls)
    assert f"TARGET=[{place}]" in result.stdout, result.stdout + result.stderr

    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == place, calls
    assert _cwd_of(tabs[0]) == scene.root

    # The shared-canvas move: the ambient client is addressed explicitly (our
    # own targeting export already points at the new place) and moved AFTER
    # the tab exists. Nothing else about the foreign session changes.
    switches = _switches(calls)
    assert len(switches) == 1, calls
    assert switches[0]["argv"] == [
        "--session",
        FOREIGN_LIVE,
        "action",
        "switch-session",
        place,
    ], switches
    order = [c["argv"] for c in calls]
    tab_at = next(
        i for i, a in enumerate(order) if "new-tab" in a and "--help" not in a
    )
    switch_at = next(i for i, a in enumerate(order) if "switch-session" in a)
    assert tab_at < switch_at, order
    assert not _attaches(calls), "a nested foreground client was attempted"
    _assert_no_foreign_mutation(calls, (FOREIGN_LIVE,))


def test_repeat_reuses_the_live_declared_workspace_without_a_duplicate(
    tmp_path: Path,
) -> None:
    """Second run: the declared workspace is already live -- reuse it exactly."""
    scene = Scene(
        tmp_path, live=[FOREIGN_LIVE, "session-project-b"], project="project-b"
    )
    result = _run_resume(
        scene,
        _declared(scene),
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "3",
            "VC_FRAME_SESSION_NAME": FOREIGN_LIVE,
        },
    )
    calls = scene.calls()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert not _creates(calls), f"a live workspace session was created again: {calls}"
    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == "session-project-b", calls
    assert len(_switches(calls)) == 1, calls
    live = scene.live_file.read_text(encoding="utf-8").split()
    assert live.count("session-project-b") == 1, live


def test_attached_client_already_in_the_declared_workspace_is_reused(
    tmp_path: Path,
) -> None:
    """Coherent repeat from inside: no create, no switch, no second client."""
    scene = Scene(
        tmp_path, live=["session-project-b", FOREIGN_LIVE], project="project-b"
    )
    result = _run_resume(
        scene,
        _declared(scene),
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "5",
            "VC_FRAME_SESSION_NAME": "session-project-b",
        },
    )
    calls = scene.calls()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert "TARGET=[session-project-b]" in result.stdout, result.stdout
    assert not _creates(calls), calls
    assert not _switches(calls), calls
    assert not _attaches(calls), calls
    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == "session-project-b", calls
    assert "ambient context" not in result.stderr, result.stderr


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_ambient_operator_session_env_does_not_override_the_declared_root(
    tmp_path: Path, shell: str
) -> None:
    """An inherited VIBECRAFTED_OPERATOR_SESSION is context, not the target.

    Baseline: the canonical gate is skipped whenever that variable is set and
    the preparation honours it verbatim, so the declared repo's agent is
    dispatched into `chosen-elsewhere`.
    """
    scene = Scene(tmp_path, live=["chosen-elsewhere"], project="project-b")
    result = _run_resume(
        scene,
        _declared(scene),
        shell=shell,
        tty=True,
        extra_env={"VIBECRAFTED_OPERATOR_SESSION": "chosen-elsewhere"},
    )
    calls = scene.calls()
    place = _expected_place(scene)

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert f"TARGET=[{place}]" in result.stdout, result.stdout + result.stderr
    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == place, calls
    # Under a pty the shell's stderr is merged into the captured stream.
    assert (
        "VIBECRAFTED_OPERATOR_SESSION=chosen-elsewhere is ambient context"
        in result.stdout + result.stderr
    )
    _assert_no_foreign_mutation(calls, ("chosen-elsewhere",))


# --------------------------------------------------------------------------
# Ordinary external terminal, and the supported no-TTY entry
# --------------------------------------------------------------------------


def test_plain_terminal_creates_and_attaches_the_declared_workspace(
    tmp_path: Path,
) -> None:
    """No frame at all, a real terminal: create, tab, then a clean handover."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="project-b")
    result = _run_resume(scene, _declared(scene), tty=True)
    calls = scene.calls()
    place = _expected_place(scene)

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    _assert_no_panic(result, calls)
    creates = _creates(calls)
    assert len(creates) == 1 and creates[0]["argv"][-1] == place, calls
    tabs = _new_tabs(calls)
    assert len(tabs) == 1 and _session_of(tabs[0]) == place, calls
    assert _cwd_of(tabs[0]) == scene.root
    attaches = _attaches(calls)
    assert len(attaches) == 1 and attaches[0]["argv"] == ["attach", place], calls
    assert attaches[0]["VC_FRAME_SESSION_NAME"] is None, attaches
    _assert_no_foreign_mutation(calls, (FOREIGN_LIVE,))


@pytest.mark.parametrize("flag", ["--root", "--repo"])
def test_no_tty_outside_a_frame_escalates_with_session_and_absolute_root(
    tmp_path: Path, flag: str
) -> None:
    """The supported no-TTY entry opens the product terminal ON the declared
    repo and hands the child the same declaration: native id and absolute root."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="project-b")
    result = _run_resume(scene, _declared(scene, flag), terminal_entry=False)
    launch = scene.terminal_launch()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert launch is not None, f"no terminal was opened: {result.stderr}"
    argv = launch["argv"]
    assert (
        Path(argv[argv.index("--working-directory") + 1]).resolve()
        == scene.root.resolve()
    )
    hosted = argv[argv.index("-e") + 1 :]
    assert hosted[2:] == [
        "resume",
        "codex",
        "--session",
        NATIVE_SESSION,
        flag,
        str(scene.root),
    ], hosted
    # The escalating parent does nothing else: no tab, no create, no AICX.
    assert not _new_tabs(scene.calls()) and not _creates(scene.calls())
    assert not scene.aicx_capture.exists()


# --------------------------------------------------------------------------
# --repo ≡ --root, paths with spaces, honest failure
# --------------------------------------------------------------------------


def test_repo_and_root_are_one_declaration(tmp_path: Path) -> None:
    root_scene = Scene(tmp_path / "a", live=[], project="project-b")
    repo_scene = Scene(tmp_path / "b", live=[], project="project-b")
    by_root = _run_resume(root_scene, _declared(root_scene, "--root"), tty=True)
    by_repo = _run_resume(repo_scene, _declared(repo_scene, "--repo"), tty=True)

    for result in (by_root, by_repo):
        assert "RC=[0]" in result.stdout, result.stdout + result.stderr
        assert "TARGET=[session-project-b]" in result.stdout, result.stdout
    root_tab = _new_tabs(root_scene.calls())[0]
    repo_tab = _new_tabs(repo_scene.calls())[0]
    assert _session_of(root_tab) == _session_of(repo_tab) == "session-project-b"
    assert _cwd_of(root_tab).name == _cwd_of(repo_tab).name == "project-b"
    root_admission = _tab_admission(root_tab)
    repo_admission = _tab_admission(repo_tab)
    for admission, scene in (
        (root_admission, root_scene),
        (repo_admission, repo_scene),
    ):
        _assert_resume_admission(admission, scene)
    assert root_admission["source_digest"] == repo_admission["source_digest"]

    other = tmp_path / "a" / "other"
    other.mkdir()
    conflict = _run_resume(
        root_scene,
        f"_vetcoders_resume_agent codex --session {NATIVE_SESSION} "
        f"--repo {shlex.quote(str(root_scene.root))} --root {shlex.quote(str(other))}",
    )
    assert "RC=[0]" not in conflict.stdout, conflict.stdout + conflict.stderr
    assert "conflicting --repo" in conflict.stderr, conflict.stderr


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_declared_root_with_spaces_binds_the_exact_cwd_and_session(
    tmp_path: Path, shell: str
) -> None:
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="repo with space")
    result = _run_resume(
        scene,
        _declared(scene),
        shell=shell,
        tty=True,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "7",
            "VC_FRAME_SESSION_NAME": STALE_MARKER,
        },
    )
    calls = scene.calls()

    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert "TARGET=[session-repo-with-space]" in result.stdout, (
        result.stdout + result.stderr
    )
    creates = _creates(calls)
    assert len(creates) == 1 and creates[0]["argv"][-1] == "session-repo-with-space", (
        calls
    )
    tabs = _new_tabs(calls)
    assert len(tabs) == 1, calls
    assert _cwd_of(tabs[0]) == scene.root, tabs
    admission = _tab_admission(tabs[0])
    _assert_resume_admission(admission, scene)


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_failed_workspace_creation_is_reported_and_launches_nothing_elsewhere(
    tmp_path: Path, shell: str
) -> None:
    """A refused create is a failed resume -- never a tab in the ambient
    session, never a headless downgrade, never a fake "launched"."""
    scene = Scene(tmp_path, live=[FOREIGN_LIVE], project="project-b")
    result = _run_resume(
        scene,
        _declared(scene),
        shell=shell,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "3",
            "VC_FRAME_SESSION_NAME": FOREIGN_LIVE,
            "VC_FRAME_CREATE_ERROR": "vc-frame: could not read the layout file",
        },
    )
    calls = scene.calls()

    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert "could not create the workspace session session-project-b" in result.stderr
    assert "could not read the layout file" in result.stderr, result.stderr
    assert "Resume launched" not in result.stdout, result.stdout
    assert not _new_tabs(calls), calls
    assert not _switches(calls) and not _attaches(calls), calls
    _assert_no_foreign_mutation(calls, (FOREIGN_LIVE,))


# --------------------------------------------------------------------------
# The bounded host create clears ONLY the current-client context (both twins)
# --------------------------------------------------------------------------


def _host_create_env(tmp_path: Path, host: str, generation: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in IDENTITY_ENV:
        env.pop(key, None)
    env["HOME"] = str(tmp_path / "home")
    env["VIBECRAFTED_HOME"] = str(tmp_path / "home" / ".vibecrafted")
    env["VC_FRAME_LOG"] = str(tmp_path / "frame.log")
    env["VC_FRAME_LIVE"] = str(tmp_path / "live.txt")
    # The dispatcher's own targeting export names exactly the missing host.
    env["VC_FRAME"] = "1"
    env["VC_FRAME_PANE_ID"] = "9"
    env["VC_FRAME_SESSION_NAME"] = host
    env["PATH"] = f"{generation / 'bin'}:{env.get('PATH', '')}"
    return env


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_shell_missing_host_create_clears_only_the_client_context(
    tmp_path: Path, shell: str
) -> None:
    """G3 resurrect (shell/lib twin): baseline inherits the marker and the
    engine panics (exit 101) -> status 2; fixed: one clean create, retry ok."""
    host = "hostless-place"
    (tmp_path / "home").mkdir()
    generation = _generation(tmp_path, tmp_path / "unused.json")
    (tmp_path / "live.txt").write_text("", encoding="utf-8")
    env = _host_create_env(tmp_path, host, generation)
    script = "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{generation}"',
            (
                f'_vetcoders_vc_frame_session_action "{generation}/bin/vc-frame" {host} '
                f'action new-tab --name t --cwd "{tmp_path}" -- /bin/true'
            ),
            'printf "STATUS=[%s]\\n" "$?"',
            'printf "PARENT_MARKER=[%s]\\n" "${VC_FRAME_SESSION_NAME:-}"',
        ]
    )
    result = subprocess.run(
        _shell_argv(shell, script),
        check=False,
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    calls = [
        json.loads(line)
        for line in (tmp_path / "frame.log").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert "STATUS=[0]" in result.stdout, result.stdout + result.stderr
    assert "commands.rs:844" not in result.stderr, result.stderr
    creates = _creates(calls)
    assert len(creates) == 1, calls
    assert creates[0]["argv"] == ["attach", "--create-background", host], creates
    assert creates[0]["VC_FRAME_SESSION_NAME"] is None, creates
    assert creates[0]["ZELLIJ_SESSION_NAME"] is None, creates
    assert creates[0]["VC_FRAME_PANE_ID"] is None, creates
    # Only that one invocation was sanitized; the parent keeps its own state.
    assert f"PARENT_MARKER=[{host}]" in result.stdout, result.stdout
    assert len(_new_tabs(calls)) == 2, calls


def test_spawn_missing_host_create_clears_only_the_client_context(
    tmp_path: Path,
) -> None:
    """Same contract for the dispatch twin (scripts/lib spawn_vc_frame_*)."""
    host = "hostless-worker"
    (tmp_path / "home").mkdir()
    generation = _generation(tmp_path, tmp_path / "unused.json")
    (tmp_path / "live.txt").write_text("", encoding="utf-8")
    env = _host_create_env(tmp_path, host, generation)
    script = "\n".join(
        [
            f'source "{COMMON_SH}"',
            (
                f"spawn_vc_frame_session_action vc-frame {host} "
                f'action new-tab --name t --cwd "{tmp_path}" -- /bin/true'
            ),
            'printf "STATUS=[%s]\\n" "$?"',
        ]
    )
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    calls = [
        json.loads(line)
        for line in (tmp_path / "frame.log").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert "STATUS=[0]" in result.stdout, result.stdout + result.stderr
    assert "commands.rs:844" not in result.stderr, result.stderr
    creates = _creates(calls)
    assert len(creates) == 1, calls
    assert creates[0]["VC_FRAME_SESSION_NAME"] is None, creates
    assert creates[0]["ZELLIJ_SESSION_NAME"] is None, creates
    assert len(_new_tabs(calls)) == 2, calls


# --------------------------------------------------------------------------
# Native evidence: the real Frame engine, isolated
# --------------------------------------------------------------------------


def _installed_frame() -> Path | None:
    """The installed engine, resolved at collection time (before fixtures
    replace HOME). Never a PATH lookup: a stale shim must not stand in."""
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
def test_native_bounded_create_survives_an_inherited_marker(tmp_path: Path) -> None:
    """Against the REAL engine, in a sandbox it can never share with the
    Founder (own short socket dir under /tmp, own config, own home):

    1. the hazard is real: `attach --create-background NAME` with an inherited
       NAME marker exits 101 at src/commands.rs:844 and creates nothing;
    2. the bounded create through the shell twin, same inherited marker,
       creates a live session;
    3. a tab opened `--cwd "<repo with space>"` really runs there;
    4. the sandbox session is killed afterwards and nothing is left behind.
    """
    assert _REAL_FRAME is not None
    sandbox = Path("/tmp") / f"vcd{os.getpid() % 100000}"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    (sandbox / "sock").mkdir(parents=True)
    (sandbox / "cfg").mkdir()
    (sandbox / "home").mkdir()
    repo = sandbox / "repo with space"
    repo.mkdir()
    (sandbox / "cfg" / "config.kdl").write_text(
        "keybinds clear-defaults=true {}\n", encoding="utf-8"
    )
    session = f"vcd{os.getpid() % 100000}"

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
            # Developer mode lets the shell twin address the explicit engine.
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(_REAL_FRAME),
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "1",
            "VC_FRAME_SESSION_NAME": session,
        }
    )

    def frame(
        *args: str, clean: bool = False, timeout: int = 30
    ) -> subprocess.CompletedProcess[str]:
        run_env = dict(env)
        if clean:
            for key in ("VC_FRAME", "VC_FRAME_PANE_ID", "VC_FRAME_SESSION_NAME"):
                run_env.pop(key, None)
        return subprocess.run(
            [str(_REAL_FRAME), *args],
            check=False,
            env=run_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        hazard = frame("attach", "--create-background", session)
        assert hazard.returncode == 101, (hazard.returncode, hazard.stderr)
        assert "You are trying to attach to the current session" in hazard.stderr, (
            hazard.stderr
        )
        assert session not in frame("ls", clean=True).stdout

        script = "\n".join(
            [
                f'source "{SHELL_SH}"',
                f'_vetcoders_vc_frame_create_host_session "{_REAL_FRAME}" {session}',
                'printf "CREATE=[%s]\\n" "$?"',
                f'printf "STATE=[%s]\\n" "$(_vetcoders_vc_frame_session_state {session})"',
            ]
        )
        created = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", script],
            check=False,
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert "CREATE=[0]" in created.stdout, created.stdout + created.stderr
        assert "STATE=[live]" in created.stdout, created.stdout + created.stderr
        assert (
            "You are trying to attach to the current session" not in created.stderr
        ), created.stderr

        probe = repo / "cwd.txt"
        tab = frame(
            "--session",
            session,
            "action",
            "new-tab",
            "--name",
            "probe",
            "--cwd",
            str(repo),
            "--",
            "sh",
            "-c",
            'pwd > "$0"; sleep 20',
            str(probe),
            clean=True,
        )
        assert tab.returncode == 0, tab.stderr
        deadline = time.monotonic() + 15
        while not probe.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert probe.exists(), "the probe tab never ran"
        assert (
            Path(probe.read_text(encoding="utf-8").strip()).resolve() == repo.resolve()
        )
    finally:
        frame("kill-session", session, clean=True)
        frame("delete-session", session, "--force", clean=True)
        leftover = frame("ls", clean=True).stdout
        shutil.rmtree(sandbox, ignore_errors=True)
    assert session not in leftover, leftover
