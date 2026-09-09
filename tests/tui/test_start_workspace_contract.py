"""`vc-start` is ONE create-only workspace contract from every entrypoint.

P0 (Founder, 2026-09-09): from an agent tool inside an attached Frame pane,

    vc-start

ran the whole product preparation, then reached the engine's interactive
client and died sixteen seconds later on "stdin is not a terminal". From a
subdirectory it named the workspace after the subdirectory; two checkouts with
the same basename silently got a `-token` suffix from the catalogue; a name
already live in Frame was adopted, resurrected or duplicated depending on which
branch of the old launcher ran first.

The contract proven here, in the shipped shell sources (not a reimplementation):

* **root** -- explicit `--repo` (legacy `--root`) wins; otherwise the Git
  top-level of the caller's directory (a subdirectory names the SAME
  workspace); outside Git, the directory itself. Never the runtime generation.
* **name** -- the explicit bare argument or the root's basename, verbatim,
  validated once (exit 2), never truncated, normalized or suffixed.
* **inventory first** -- the engine's own `list-sessions` in the product socket
  namespace is read BEFORE any window, workspace record or provider. A live
  session (attached or detached) or an EXITED resurrection record under the
  name refuses with exit 3 and real commands; an unreadable inventory refuses
  with exit 4. Nothing is killed, deleted, switched, attached or renamed.
* **exclusive create** -- Frame's server-side `attach --create-background`;
  two concurrent starts yield exactly one workspace and one refusal.
* **enter** -- a caller with a terminal attaches (outside a frame) or moves its
  own client (`switch-session`, inside one). A caller without one creates the
  session first and then opens the VC Terminal on the root with the same
  name/repo argv; an inherited `VC_FRAME_*` marker is not proof of a TTY.

Only the catalogue boundary and the Frame engine are stubbed. The stub refuses
what the real engine refuses, with the real engine's words, keeps an exclusive
on-disk session table so a race is a real race, and RESURRECTS an EXITED record
on `--create-background` exactly like the engine does -- so a start that skips
the inventory check fails here the way it would fail for the Founder. The last
cases run against the REAL installed engine in an isolated sandbox.

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
DASHBOARD_SH = RUNTIME / "shell" / "lib" / "dashboard.sh"
DISPATCH_SH = RUNTIME / "shell" / "lib" / "dispatch.sh"
DECK = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "deck" / "vibecrafted"
PRIMARY_SHELL = REPO_ROOT / "config" / "alacritty" / "launch-primary-shell.zsh"

EXIT_USAGE = 2
EXIT_EXISTS = 3
EXIT_INVENTORY = 4

# A worker run exports these; pytest inherits them. The entry must derive
# everything from its own arguments and the engine, so the scene is blank.
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
    "VIBECRAFTED_START_CREATED_SESSION",
    "VIBECRAFTED_START_ROOT",
    "VIBECRAFTED_TERMINAL_ENTRY",
    "VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME",
    "VIBECRAFTED_PRODUCT_ENTRY",
    "VIBECRAFTED_PRODUCT_ENTRY_PROBE",
    "VIBECRAFTED_ROOT",
    "VIBECRAFTED_RUNTIME_ROOT",
    "VIBECRAFTED_RUNTIME_BIN",
    "VIBECRAFTED_RUNTIME_HOME",
    "VIBECRAFTED_PYTHON",
    "VIBECRAFTED_RUN_ID",
    "VIBECRAFTED_RUN_LOCK",
    "VIBECRAFTED_PREFER_REPO_VC_FRAME",
    "VIBECRAFTED_VC_FRAME_BIN",
    "VIBECRAFTED_LEGACY_VC_FRAME_SOCKET_DIR",
    "SPAWN_ROOT",
    "VC_FRAME",
    "VC_FRAME_PANE_ID",
    "VC_FRAME_SESSION_NAME",
    "VC_FRAME_CONFIG_DIR",
    "VC_FRAME_CONFIG_FILE",
    "ZELLIJ",
    "ZELLIJ_PANE_ID",
    "ZELLIJ_SESSION_NAME",
)

MARKER_KEYS = (
    "VC_FRAME",
    "VC_FRAME_PANE_ID",
    "VC_FRAME_SESSION_NAME",
    "ZELLIJ",
    "ZELLIJ_PANE_ID",
    "ZELLIJ_SESSION_NAME",
    "VIBECRAFTED_OPERATOR_SESSION",
)

# The Frame engine stand-in. Sessions are FILES: `<table>/live/<name>` and
# `<table>/dead/<name>`, so `attach --create-background` is an O_EXCL create
# (a real exclusive primitive, like the engine's server-side one) and two
# concurrent callers really race. Behaviours mirrored from vc-frame 0.47.3:
#   * `list-sessions [-n|--no-formatting]` prints `NAME [Created … ago]` and
#     `NAME [Created … ago] (EXITED - attach to resurrect)`; with nothing to
#     list it exits 1 with "No active vc-frame sessions found.";
#   * `attach --create-background NAME` on a live name → "Session already
#     exists", exit 1; on an EXITED record it RESURRECTS (dead → live) and
#     exits 0 -- the engine's ClientInfo::Resurrect arm, the reason the
#     launcher must rule a record out BEFORE calling this;
#   * `attach NAME` panics (101, src/commands.rs:844) when the inherited
#     session marker equals NAME, refuses a missing session, refuses a non-TTY
#     stdin, and otherwise blocks briefly;
#   * `--session S action switch-session NAME` needs both live;
#   * `--session S action list-clients` prints a header and one row when S is
#     listed in VC_FRAME_CLIENTS.
# Fault injection: VC_FRAME_INVENTORY_ERROR (list-sessions fails with that
# text), VC_FRAME_INVENTORY_GARBAGE (list-sessions prints an unparsable line),
# VC_FRAME_CREATE_ERROR (create refuses with that text).
# Every invocation is logged with the markers it inherited and whether stdin
# was a TTY, so a test can prove WHICH env a call ran under.
VC_FRAME_STUB = """#!/usr/bin/env python3
import errno, json, os, sys, time

argv = sys.argv[1:]
log = os.environ.get("VC_FRAME_LOG", "")
table = os.environ.get("VC_FRAME_TABLE", "")
clients_file = os.environ.get("VC_FRAME_CLIENTS", "")
marker = os.environ.get("ZELLIJ_SESSION_NAME") or os.environ.get("VC_FRAME_SESSION_NAME")


def record(extra=None):
    if not log:
        return
    entry = {
        "argv": argv,
        "cwd": os.getcwd(),
        "stdin_tty": sys.stdin.isatty(),
        "VC_FRAME_SESSION_NAME": os.environ.get("VC_FRAME_SESSION_NAME"),
        "ZELLIJ_SESSION_NAME": os.environ.get("ZELLIJ_SESSION_NAME"),
        "VC_FRAME_PANE_ID": os.environ.get("VC_FRAME_PANE_ID"),
        "VC_FRAME_SOCKET_DIR": os.environ.get("VC_FRAME_SOCKET_DIR"),
    }
    entry.update(extra or {})
    with open(log, "a") as handle:
        handle.write(json.dumps(entry) + "\\n")


def names(kind):
    path = os.path.join(table, kind)
    if not table or not os.path.isdir(path):
        return []
    return sorted(os.listdir(path))


def clients_of(name):
    if not clients_file or not os.path.exists(clients_file):
        return []
    return [n for n in open(clients_file).read().splitlines() if n.strip() == name]


def not_found(name):
    live = names("live")
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
    print("Commands: action attach delete-session kill-session list-sessions")
    sys.exit(0)

if argv[:1] in (["ls"], ["list-sessions"]):
    forced = os.environ.get("VC_FRAME_INVENTORY_ERROR", "")
    if forced:
        sys.stderr.write(forced + "\\n")
        sys.exit(2)
    if os.environ.get("VC_FRAME_INVENTORY_GARBAGE", ""):
        print("thread 'main' panicked at zellij-utils/src/sessions.rs")
        sys.exit(0)
    live, dead = names("live"), names("dead")
    if not live and not dead:
        sys.stderr.write("No active vc-frame sessions found.\\n")
        sys.exit(1)
    for name in live:
        tag = " (current)" if marker == name else ""
        print("%s [Created 1s ago]%s" % (name, tag))
    for name in dead:
        print("%s [Created 2s ago] (EXITED - attach to resurrect)" % name)
    sys.exit(0)

session = None
rest = argv
if rest[:1] == ["--session"]:
    session = rest[1]
    rest = rest[2:]
layout = None
if rest[:1] == ["--new-session-with-layout"]:
    layout = rest[1]
    rest = rest[2:]

if rest[:2] == ["attach", "--create-background"]:
    name = rest[2] if len(rest) > 2 else session
    panic_if_current(name)
    forced = os.environ.get("VC_FRAME_CREATE_ERROR", "")
    if forced:
        sys.stderr.write(forced + "\\n")
        sys.exit(1)
    if layout is not None and not os.path.isfile(layout):
        sys.stderr.write("Error occurred in server: could not read the layout file\\n")
        sys.exit(1)
    os.makedirs(os.path.join(table, "live"), exist_ok=True)
    os.makedirs(os.path.join(table, "dead"), exist_ok=True)
    dead_path = os.path.join(table, "dead", name)
    live_path = os.path.join(table, "live", name)
    if os.path.exists(dead_path):
        os.replace(dead_path, live_path)
        record({"resurrected": name})
        sys.exit(0)
    try:
        fd = os.open(live_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            sys.stderr.write("Session already exists\\n")
            sys.exit(1)
        raise
    os.write(fd, layout.encode() if layout else b"")
    os.close(fd)
    record({"created": name})
    sys.exit(0)

if rest[:1] == ["attach"]:
    target = rest[1] if len(rest) > 1 else session
    panic_if_current(target)
    if target in names("dead"):
        record({"resurrected": target})
        os.replace(os.path.join(table, "dead", target), os.path.join(table, "live", target))
    elif target not in names("live"):
        not_found(target)
    if not sys.stdin.isatty():
        sys.stderr.write("vc-frame: stdin is not a terminal (TTY); cannot start an interactive session.\\n")
        sys.exit(1)
    record({"attached": target})
    time.sleep(0.2)
    sys.exit(0)

if rest[:1] in (["kill-session"], ["delete-session"]):
    record({"destroyed": rest[1] if len(rest) > 1 else session})
    sys.exit(0)

if rest[:1] == ["action"]:
    target = session or marker
    if not target or target not in names("live"):
        not_found(target or "")
    verb = rest[1] if len(rest) > 1 else ""
    if verb == "switch-session":
        wanted = rest[2] if len(rest) > 2 else ""
        if wanted not in names("live"):
            not_found(wanted)
        record({"switched": [target, wanted]})
        sys.exit(0)
    if verb == "list-clients":
        print("CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND")
        for _ in clients_of(target):
            print("1         terminal_1     zsh ")
        sys.exit(0)
    if verb == "list-tabs":
        print("TAB_ID  POSITION  NAME")
    sys.exit(0)

sys.exit(0)
"""

# The canonical catalogue owner. `workspace resolve --root R --env` answers
# with the catalogue's OWN session label (`catalog-<basename>-token`, the shape
# that used to leak into the Frame name); every call is logged so a test can
# prove the inventory refusal happened BEFORE any workspace record was asked
# for, and that the WES binding names the session start really created.
OWNER_CLI = """#!/usr/bin/env bash
log="${OWNER_CLI_LOG:-}"
[[ -z "$log" ]] || printf '%s\\n' "$*" >> "$log"
if [[ "$1 $2" == "workspace resolve" ]]; then
  shift 2
  root=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root) root="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  tag="$(basename "$root")"
  tag="$(printf '%s' "$tag" | tr -c 'A-Za-z0-9\\n' '-')"
  echo "VIBECRAFTED_WORKSPACE_ID=ws-$tag"
  echo "VIBECRAFTED_SESSION_ID=sess-$tag"
  echo "VIBECRAFTED_WORKSPACE_INSTANCE_ID=inst-$tag"
  echo "VIBECRAFTED_OPERATOR_SESSION=catalog-$tag-a1b2c3"
  echo "VIBECRAFTED_WORKSPACE_ROOT=$root"
  exit 0
fi
exit 0
"""


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


class Scene:
    """One isolated home, generation, catalogue owner, Frame table and stub."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        project: str = "mlx-batch-runner",
        live: tuple[str, ...] = (),
        dead: tuple[str, ...] = (),
        clients: tuple[str, ...] = (),
        git: bool = False,
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
        self.config_dir = self.home / ".config" / "vibecrafted" / "vc-frame"
        _write(self.config_dir / "layouts" / "operator.kdl", "layout {\n}\n")
        self.terminal_log = tmp_path / "terminal-launches.jsonl"
        self.generation = self._generation(tmp_path)
        self.owner_log = tmp_path / "owner-calls.log"
        self.owner = _write(tmp_path / "owner-cli", OWNER_CLI)
        self.frame_log = tmp_path / "frame.log"
        self.table = tmp_path / "frame-table"
        (self.table / "live").mkdir(parents=True)
        (self.table / "dead").mkdir(parents=True)
        for name in live:
            (self.table / "live" / name).write_text("", encoding="utf-8")
        for name in dead:
            (self.table / "dead" / name).write_text("", encoding="utf-8")
        self.clients_file = tmp_path / "attached-clients.txt"
        self.clients_file.write_text(
            "".join(f"{n}\n" for n in clients), encoding="utf-8"
        )
        self.root = tmp_path / project
        self.root.mkdir(parents=True, exist_ok=True)
        if git:
            subprocess.run(
                ["git", "init", "-q", str(self.root)],
                check=True,
                capture_output=True,
                env={**os.environ, "HOME": str(self.home)},
            )
        self.cwd = self.root

    def _generation(self, root: Path) -> Path:
        generation = root / "generation"
        _write(generation / "libexec" / "vc-terminal", "#!/bin/bash\nexit 0\n")
        _write(generation / "libexec" / "vc-frame", VC_FRAME_STUB)
        _write(generation / "bin" / "vc-frame", VC_FRAME_STUB)
        _write(
            generation / "bin" / "vc-terminal",
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"MARKER_KEYS = {MARKER_KEYS!r}\n"
            f"with open({str(self.terminal_log)!r}, 'a') as fh:\n"
            "    fh.write(json.dumps("
            "{'argv': sys.argv[1:], 'cwd': os.getcwd(),"
            " 'boundary': os.environ.get('VIBECRAFTED_TERMINAL_ENTRY', ''),"
            " 'created': os.environ.get('VIBECRAFTED_START_CREATED_SESSION', ''),"
            " 'markers': {k: os.environ.get(k) for k in MARKER_KEYS}}) + '\\n')\n"
            "sys.exit(int(os.environ.get('VC_TERMINAL_EXIT', '0')))\n",
        )
        for verb in ("vc-start", "vibecrafted"):
            _write(generation / "bin" / verb, "#!/bin/bash\nexit 0\n")
        return generation

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        for key in IDENTITY_ENV:
            env.pop(key, None)
        env["HOME"] = str(self.home)
        env["VIBECRAFTED_HOME"] = str(self.home / ".vibecrafted")
        env["XDG_CONFIG_HOME"] = str(self.home / ".config")
        env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        env["VC_FRAME_SOCKET_DIR"] = str(self.tmp_path / "sock")
        env["VIBECRAFTED_PRODUCT_CORE_CLI"] = str(self.owner)
        env["OWNER_CLI_LOG"] = str(self.owner_log)
        env["VC_FRAME_LOG"] = str(self.frame_log)
        env["VC_FRAME_TABLE"] = str(self.table)
        env["VC_FRAME_CLIENTS"] = str(self.clients_file)
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

    def owner_calls(self) -> list[str]:
        if not self.owner_log.exists():
            return []
        return [l for l in self.owner_log.read_text(encoding="utf-8").splitlines() if l]

    def live(self) -> list[str]:
        return sorted(p.name for p in (self.table / "live").iterdir())

    def dead(self) -> list[str]:
        return sorted(p.name for p in (self.table / "dead").iterdir())

    def terminal_launches(self, wait: float = 6.0, expect: int = 1) -> list[dict]:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            launches = self._read_launches()
            if len(launches) >= expect:
                return launches
            time.sleep(0.05)
        return self._read_launches()

    def _read_launches(self) -> list[dict]:
        if not self.terminal_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.terminal_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def _shell_argv(shell: str, script: str) -> list[str]:
    if shell == "zsh":
        return ["zsh", "-f", "-c", script]
    return ["bash", "--noprofile", "--norc", "-c", script]


def _entry_script(
    scene: Scene, invocation: str, *, developer_root: bool = False
) -> str:
    loaded_root = REPO_ROOT if developer_root else scene.generation
    return "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{loaded_root}"',
            invocation,
            'printf "RC=[%s]\\n" "$?"',
            'printf "TARGET=[%s]\\n" "${VIBECRAFTED_OPERATOR_SESSION:-}"',
        ]
    )


def _run(
    scene: Scene,
    invocation: str,
    *,
    shell: str = "bash",
    extra_env: dict[str, str] | None = None,
    tty: bool = False,
    cwd: Path | None = None,
    developer_root: bool = False,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run the REAL public entry (`vc-start` from dispatch.sh) in a fresh shell."""
    env = scene.env(extra_env)
    script = _entry_script(scene, invocation, developer_root=developer_root)
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
        cwd=cwd or scene.cwd,
        env=env,
        stdin=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _rc(result: subprocess.CompletedProcess[str]) -> int:
    marker = "RC=["
    assert marker in result.stdout, result.stdout + result.stderr
    return int(result.stdout.split(marker, 1)[1].split("]", 1)[0])


def _creates(calls: list[dict]) -> list[dict]:
    return [
        c
        for c in calls
        if "--create-background" in c["argv"]
        and not c.get("created")
        and not c.get("resurrected")
    ]


def _attaches(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["argv"][:1] == ["attach"] and c.get("attached")]


def _switches(calls: list[dict]) -> list[dict]:
    return [c for c in calls if "switch-session" in c["argv"]]


def _destroys(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c.get("destroyed")]


def _hosted(launch: dict) -> list[str]:
    argv = launch["argv"]
    return argv[argv.index("-e") + 1 :]


def _working_directory(launch: dict) -> Path:
    argv = launch["argv"]
    return Path(argv[argv.index("--working-directory") + 1]).resolve()


def _assert_nothing_mutated(calls: list[dict]) -> None:
    assert not _creates(calls), f"a session was created: {calls}"
    assert not _attaches(calls), f"an attach was attempted: {calls}"
    assert not _switches(calls), f"a switch was attempted: {calls}"
    assert not _destroys(calls), f"a session was killed or deleted: {calls}"
    assert not [c for c in calls if c.get("resurrected")], calls


def _assert_no_workspace_record(scene: Scene) -> None:
    """The inventory refusal comes BEFORE any catalogue write."""
    assert not [c for c in scene.owner_calls() if c.startswith("workspace")], (
        scene.owner_calls()
    )


def _commands_in(stderr: str, prefix: str) -> list[list[str]]:
    """Every shell command line the refusal offered, tokenized as a shell would."""
    found = []
    for line in stderr.splitlines():
        if prefix in line:
            found.append(shlex.split(line.split(prefix, 1)[1].strip().split("  --")[0]))
    return found


# --------------------------------------------------------------------------
# 1. root and default name: Git top-level from a subdirectory, explicit repo
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_default_name_is_the_git_toplevel_basename_from_a_subdirectory(
    tmp_path: Path, shell: str
) -> None:
    """Baseline named the workspace after the subdirectory (`pwd -P`). The
    contract: the Git top-level basename, from the root and from `src/deep`
    alike, and the terminal child is handed that root as `--repo`."""
    scene = Scene(tmp_path, project="mlx-batch-runner", git=True)
    deep = scene.root / "src" / "deep"
    deep.mkdir(parents=True)

    result = _run(scene, "vc-start", shell=shell, cwd=deep)

    assert _rc(result) == 0, result.stdout + result.stderr
    assert scene.live() == ["mlx-batch-runner"], (scene.live(), result.stderr)
    assert "created workspace mlx-batch-runner" in result.stdout, result.stdout
    launches = scene.terminal_launches()
    assert len(launches) == 1, (launches, result.stderr)
    launch = launches[0]
    assert _working_directory(launch) == scene.root.resolve()
    hosted = _hosted(launch)
    assert hosted[1].endswith("/bin/vc-start"), hosted
    assert hosted[2:] == ["--repo", str(scene.root.resolve())], hosted
    assert launch["created"] == "mlx-batch-runner", launch
    assert launch["boundary"] == "1", launch
    # The escalating parent created (no PTY needed) and did nothing else.
    calls = scene.calls()
    assert len(_creates(calls)) == 1, calls
    assert not _attaches(calls) and not _switches(calls) and not _destroys(calls), calls


def test_outside_git_the_directory_itself_is_the_root(tmp_path: Path) -> None:
    scene = Scene(tmp_path, project="plain-dir")
    result = _run(scene, "vc-start")
    assert _rc(result) == 0, result.stderr
    assert scene.live() == ["plain-dir"]
    launch = scene.terminal_launches()[0]
    assert _working_directory(launch) == scene.root.resolve()


@pytest.mark.parametrize("flag", ["--repo", "--root"])
def test_explicit_repo_from_the_filesystem_root_names_that_repository(
    tmp_path: Path, flag: str
) -> None:
    """`cd / && vc-start --repo <path>`: the root is the argument, not `/`."""
    scene = Scene(tmp_path, project="Sentry-Selfhosted")
    result = _run(
        scene, f"vc-start {flag} {shlex.quote(str(scene.root))}", cwd=Path("/")
    )
    assert _rc(result) == 0, result.stderr
    assert scene.live() == ["Sentry-Selfhosted"]
    launch = scene.terminal_launches()[0]
    assert _working_directory(launch) == scene.root.resolve()
    assert _hosted(launch)[2:] == ["--repo", str(scene.root.resolve())]


def test_the_runtime_generation_is_never_the_workspace(tmp_path: Path) -> None:
    """Run from inside the generation tree (the bundled front door's own cwd
    shape): the workspace is still the caller's project, never `generation`."""
    scene = Scene(tmp_path, project="real-project")
    result = _run(
        scene, f"vc-start --repo {shlex.quote(str(scene.root))}", cwd=scene.generation
    )
    assert _rc(result) == 0, result.stderr
    assert scene.live() == ["real-project"], scene.live()


# --------------------------------------------------------------------------
# 2. explicit name, validation, reserved words, quoting
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_explicit_name_is_used_verbatim(tmp_path: Path, shell: str) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(scene, "vc-start review-b", shell=shell)
    assert _rc(result) == 0, result.stderr
    assert scene.live() == ["review-b"]
    launch = scene.terminal_launches()[0]
    assert _hosted(launch)[2:] == ["review-b", "--repo", str(scene.root.resolve())]
    assert launch["created"] == "review-b"


def test_a_spaced_name_stays_one_argument_and_is_quoted_back(tmp_path: Path) -> None:
    """Quoting: `vc-start 'two words'` creates `two words`; a later collision
    prints commands a shell parses back to that exact name."""
    scene = Scene(tmp_path, project="mlx-batch-runner")
    first = _run(scene, "vc-start " + shlex.quote("two words"))
    assert _rc(first) == 0, first.stderr
    assert scene.live() == ["two words"]
    assert _hosted(scene.terminal_launches()[0])[2] == "two words"

    second = _run(scene, "vc-start " + shlex.quote("two words"))
    assert _rc(second) == EXIT_EXISTS, second.stdout + second.stderr
    offered = _commands_in(second.stderr, "vc-dashboard attach")
    assert offered == [["two words"]], second.stderr


def test_a_name_with_a_single_quote_is_quoted_back_correctly(tmp_path: Path) -> None:
    name = "it's-mine"
    scene = Scene(tmp_path, project="p", live=(name,))
    result = _run(scene, "vc-start " + shlex.quote(name))
    assert _rc(result) == EXIT_EXISTS
    assert _commands_in(result.stderr, "vc-dashboard attach") == [[name]], result.stderr


@pytest.mark.parametrize(
    "bad, reason",
    [
        ("''", "it is empty"),
        ("'   '", "it is empty"),
        (".", "'.' and '..' are not names"),
        ("..", "'.' and '..' are not names"),
        ("a/b", "path separators"),
        ("'a\\\\b'", "path separators"),
        ("' padded'", "leading or trailing whitespace"),
        ("$'tab\\there'", "control characters"),
        ("abcdefghijklmnopqrstuvwxy", "25 characters long; the limit is 24"),
    ],
)
def test_invalid_names_are_refused_once_before_any_side_effect(
    tmp_path: Path, bad: str, reason: str
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(scene, f"vc-start {bad}")
    assert _rc(result) == EXIT_USAGE, result.stdout + result.stderr
    assert "cannot be used" in result.stderr and reason in result.stderr, result.stderr
    assert scene.calls() == [], "the engine was consulted for an invalid name"
    assert scene.terminal_launches(wait=0.5, expect=1) == []
    _assert_no_workspace_record(scene)


def test_a_leading_dash_name_is_an_unknown_option_not_a_name(tmp_path: Path) -> None:
    scene = Scene(tmp_path, project="p")
    result = _run(scene, "vc-start -bad")
    assert _rc(result) == EXIT_USAGE
    assert "unknown option -bad" in result.stderr, result.stderr
    assert scene.calls() == []


def test_a_repository_whose_basename_is_not_a_legal_name_asks_for_an_explicit_one(
    tmp_path: Path,
) -> None:
    """No silent truncation: a 30-character checkout name is refused with the
    explicit-name form, quoting the root."""
    long_name = "a-very-long-repository-name-30"
    assert len(long_name) == 30
    scene = Scene(tmp_path, project=long_name)
    result = _run(scene, "vc-start")
    assert _rc(result) == EXIT_USAGE, result.stderr
    assert "the repository name" in result.stderr and "limit is 24" in result.stderr
    assert (
        "Pass a workspace name explicitly: vc-start <workspace> --repo" in result.stderr
    )
    assert shlex.quote(str(scene.root.resolve())) in result.stderr, result.stderr
    assert scene.calls() == []
    assert scene.live() == []


@pytest.mark.parametrize(
    "argv",
    [
        "-s foo",
        "--session foo",
        "-n layout.kdl",
        "--new-session-with-layout layout.kdl",
        "-l x",
        "--layout x",
        "--layout-string 'layout {}'",
        "-- attach foo",
        "--wat",
        "one two",
    ],
)
def test_frame_spellings_and_extra_words_are_refused(tmp_path: Path, argv: str) -> None:
    scene = Scene(tmp_path, project="p")
    result = _run(scene, f"vc-start {argv}")
    assert _rc(result) == EXIT_USAGE, result.stdout + result.stderr
    assert "vc-start" in result.stderr
    assert scene.calls() == [], scene.calls()
    assert scene.live() == []


@pytest.mark.parametrize("word", ["operator", "vibecrafted"])
def test_reserved_layout_aliases_mean_the_default_start(
    tmp_path: Path, word: str
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(scene, f"vc-start {word}")
    assert _rc(result) == 0, result.stderr
    assert scene.live() == ["mlx-batch-runner"], scene.live()
    assert _hosted(scene.terminal_launches()[0])[2:] == [
        word,
        "--repo",
        str(scene.root.resolve()),
    ]


def test_plain_start_never_silently_attaches_resume_is_deliberate(
    tmp_path: Path,
) -> None:
    """The live same-name session is refused by plain start (exit 3) and its
    message names the deliberate paths; `resume` keeps its own owner and is
    never entered by accident."""
    scene = Scene(tmp_path, project="mlx-batch-runner", live=("mlx-batch-runner",))
    result = _run(scene, "vc-start")
    assert _rc(result) == EXIT_EXISTS
    assert "vc-dashboard attach mlx-batch-runner" in result.stderr, result.stderr
    _assert_nothing_mutated(scene.calls())
    assert scene.terminal_launches(wait=0.5) == []


# --------------------------------------------------------------------------
# 3. inventory first: live attached / live detached / EXITED / unreadable
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_live_attached_collision_refuses_with_attach_and_rename_only(
    tmp_path: Path, shell: str
) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("mlx-batch-runner",),
        clients=("mlx-batch-runner",),
    )
    result = _run(scene, "vc-start", shell=shell)

    assert _rc(result) == EXIT_EXISTS, result.stdout + result.stderr
    err = result.stderr
    assert "already exists in vc-frame (live, a client is attached)" in err, err
    assert "Nothing was created, attached, switched or deleted" in err
    assert _commands_in(err, "vc-dashboard attach") == [["mlx-batch-runner"]]
    assert "vc-start <new-name> --repo " + shlex.quote(str(scene.root.resolve())) in err
    # A watched session is never offered for killing; nobody is inside a
    # frame here, so no switch either.
    assert "kill-session" not in err and "delete-session" not in err, err
    assert "vc-dashboard switch" not in err
    _assert_nothing_mutated(scene.calls())
    _assert_no_workspace_record(scene)
    assert scene.terminal_launches(wait=0.5) == []
    assert scene.live() == ["mlx-batch-runner"]


def test_live_detached_collision_offers_kill_only_because_nobody_is_attached(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner", live=("mlx-batch-runner",))
    result = _run(scene, "vc-start")

    assert _rc(result) == EXIT_EXISTS
    err = result.stderr
    assert "(live, detached: nobody is attached)" in err, err
    assert _commands_in(err, "vc-frame kill-session") == [["mlx-batch-runner"]], err
    assert "delete-session" not in err
    _assert_nothing_mutated(scene.calls())
    _assert_no_workspace_record(scene)
    assert scene.terminal_launches(wait=0.5) == []


def test_exited_record_is_not_live_and_is_never_resurrected_by_start(
    tmp_path: Path,
) -> None:
    """The engine's `attach --create-background` RESURRECTS a dead record. The
    inventory check must rule the record out first: refuse, offer resurrect
    (`vc-frame attach`) or delete, and call no create at all."""
    scene = Scene(tmp_path, project="mlx-batch-runner", dead=("mlx-batch-runner",))
    result = _run(scene, "vc-start")

    assert _rc(result) == EXIT_EXISTS, result.stdout + result.stderr
    err = result.stderr
    assert "EXITED session (a resurrection record, not running)" in err, err
    assert _commands_in(err, "vc-frame attach") == [["mlx-batch-runner"]], err
    assert _commands_in(err, "vc-frame delete-session") == [["mlx-batch-runner"]], err
    assert "kill-session" not in err and "vc-dashboard attach" not in err, err
    _assert_nothing_mutated(scene.calls())
    assert scene.dead() == ["mlx-batch-runner"] and scene.live() == []
    assert scene.terminal_launches(wait=0.5) == []


def test_inside_a_frame_the_collision_also_offers_switch(tmp_path: Path) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("mlx-batch-runner", "other-place"),
        clients=("mlx-batch-runner", "other-place"),
    )
    result = _run(
        scene,
        "vc-start",
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "3",
            "VC_FRAME_SESSION_NAME": "other-place",
        },
    )
    assert _rc(result) == EXIT_EXISTS
    assert _commands_in(result.stderr, "vc-dashboard switch") == [["mlx-batch-runner"]]
    _assert_nothing_mutated(scene.calls())


@pytest.mark.parametrize(
    "fault",
    [
        {
            "VC_FRAME_INVENTORY_ERROR": "IPC error: cannot connect to the vc-frame server"
        },
        {"VC_FRAME_INVENTORY_GARBAGE": "1"},
    ],
)
def test_unreadable_inventory_refuses_instead_of_creating(
    tmp_path: Path, fault: dict[str, str]
) -> None:
    """Doubt is not permission to duplicate: exit 4, no create, no terminal,
    no workspace record; the engine's own words are surfaced."""
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(scene, "vc-start", extra_env=fault)

    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    err = result.stderr
    assert "could not read the live vc-frame session inventory" in err, err
    assert "refusing to create mlx-batch-runner" in err
    if "VC_FRAME_INVENTORY_ERROR" in fault:
        assert "cannot connect" in err, err
    else:
        assert "unrecognized inventory line" in err, err
    assert "vc-frame list-sessions --no-formatting" in err
    _assert_nothing_mutated(scene.calls())
    _assert_no_workspace_record(scene)
    assert scene.live() == []
    assert scene.terminal_launches(wait=0.5) == []


def test_missing_engine_is_an_inventory_failure_not_a_terminal(tmp_path: Path) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    (scene.generation / "libexec" / "vc-frame").unlink()
    result = _run(scene, "vc-start")
    assert _rc(result) == EXIT_INVENTORY, result.stderr
    assert "vc-frame engine is unavailable" in result.stderr
    assert scene.terminal_launches(wait=0.5) == []


def test_engine_create_failure_is_reported_as_the_engines_words_not_as_a_conflict(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(
        scene,
        "vc-start",
        extra_env={
            "VC_FRAME_CREATE_ERROR": "Error occurred in server: could not read the layout file"
        },
    )
    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    assert (
        "vc-frame refused to create workspace mlx-batch-runner (exit 1)"
        in result.stderr
    )
    assert "could not read the layout file" in result.stderr
    assert "already exists" not in result.stderr
    assert scene.terminal_launches(wait=0.5) == []


# --------------------------------------------------------------------------
# 4. same basename, different repositories; concurrency
# --------------------------------------------------------------------------


def test_same_basename_from_a_different_repository_is_a_conflict_with_a_rename_option(
    tmp_path: Path,
) -> None:
    """Baseline let the catalogue append `-token` silently. Now: `mlx` from
    repo A owns the name; `mlx` from repo B is refused with the rename form
    quoting repo B, and nothing is created under any other name."""
    repo_a = tmp_path / "a" / "mlx"
    repo_b = tmp_path / "b" / "mlx"
    repo_a.mkdir(parents=True)
    repo_b.mkdir(parents=True)
    scene = Scene(tmp_path, project="unused")

    first = _run(scene, "vc-start", cwd=repo_a)
    assert _rc(first) == 0, first.stderr
    assert scene.live() == ["mlx"]

    second = _run(scene, "vc-start", cwd=repo_b)
    assert _rc(second) == EXIT_EXISTS, second.stdout + second.stderr
    assert "workspace mlx already exists in vc-frame" in second.stderr
    assert (
        "start a different workspace here:   vc-start <new-name> --repo "
        + shlex.quote(str(repo_b.resolve()))
        in second.stderr
    ), second.stderr
    assert scene.live() == ["mlx"], "a suffixed or renamed session appeared"
    assert len(_creates(scene.calls())) == 1
    assert len(scene.terminal_launches()) == 1

    third = _run(scene, "vc-start mlx-b", cwd=repo_b)
    assert _rc(third) == 0, third.stderr
    assert scene.live() == ["mlx", "mlx-b"]


def test_two_concurrent_starts_create_exactly_one_workspace(tmp_path: Path) -> None:
    """Both callers read `missing`, both call the exclusive create: the engine
    admits one. The loser reports exit 3 as a conflict (not an engine
    failure), opens no terminal, and nothing is left behind twice."""
    scene = Scene(tmp_path, project="mlx-batch-runner")
    env = scene.env()
    script = _entry_script(scene, "vc-start")
    procs = [
        subprocess.Popen(
            _shell_argv("bash", script),
            cwd=scene.cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outs = [p.communicate(timeout=120) for p in procs]
    rcs = sorted(int(o[0].split("RC=[", 1)[1].split("]", 1)[0]) for o in outs)

    assert rcs == [0, EXIT_EXISTS], outs
    assert scene.live() == ["mlx-batch-runner"]
    created = [c for c in scene.calls() if c.get("created")]
    assert len(created) == 1, scene.calls()
    assert len(_creates(scene.calls())) == 2, "both callers must have raced the create"
    loser = next(o for o in outs if f"RC=[{EXIT_EXISTS}]" in o[0])
    assert "already exists in vc-frame" in loser[1], loser[1]
    assert "refused to create" not in loser[1], loser[1]
    winner = next(o for o in outs if "RC=[0]" in o[0])
    assert "created workspace mlx-batch-runner" in winner[0]
    launches = scene.terminal_launches(expect=2, wait=3.0)
    assert len(launches) == 1, launches
    assert not _destroys(scene.calls())


# --------------------------------------------------------------------------
# 5. no TTY: inherited Frame env is not a terminal; the child enters what the
#    parent created; the boundary stops recursion
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_no_tty_with_inherited_frame_env_creates_first_then_opens_the_terminal(
    tmp_path: Path, shell: str
) -> None:
    """The reported P0 shape: an agent tool inside an attached pane (markers
    present, the marked session live and watched) but no controlling TTY.
    Baseline: direct in-frame path → interactive client → "stdin is not a
    terminal". Now: exclusive create with the markers cleared, then the VC
    Terminal on the root with the created-marker; no attach, no switch, no
    panic, and the child inherits no marker."""
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    result = _run(
        scene,
        "vc-start",
        shell=shell,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "7",
            "VC_FRAME_SESSION_NAME": "other-place",
            "ZELLIJ_SESSION_NAME": "other-place",
            "VIBECRAFTED_OPERATOR_SESSION": "other-place",
        },
    )

    assert _rc(result) == 4, result.stdout + result.stderr
    assert "guest API" in result.stderr
    assert scene.live() == ["other-place"]
    assert not _creates(scene.calls())
    assert not _attaches(scene.calls()) and not _switches(scene.calls())
    assert scene.terminal_launches(wait=0.5) == []


def test_the_terminal_child_enters_the_session_its_parent_created(
    tmp_path: Path,
) -> None:
    """The child shape: boundary set, created-marker naming the live session,
    a real PTY. The inventory says `live`, and only because the marker names
    this very start does the child enter instead of refusing."""
    scene = Scene(tmp_path, project="mlx-batch-runner", live=("mlx-batch-runner",))
    result = _run(
        scene,
        f"vc-start --repo {shlex.quote(str(scene.root))}",
        tty=True,
        developer_root=True,
        extra_env={
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VIBECRAFTED_START_CREATED_SESSION": "mlx-batch-runner",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
        },
    )
    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert "TARGET=[mlx-batch-runner]" in result.stdout, result.stdout
    calls = scene.calls()
    attaches = _attaches(calls)
    assert len(attaches) == 1 and attaches[0].get("attached") == "mlx-batch-runner", (
        calls
    )
    assert attaches[0]["stdin_tty"] is True
    assert not _creates(calls), "the child created a second time"
    assert scene.terminal_launches(wait=0.5) == [], "the child opened another terminal"
    # The WES binding names the session this start entered, in the product
    # socket namespace.
    binds = [c for c in scene.owner_calls() if c.startswith("workspace session-attach")]
    assert binds and all("--runtime-session-id mlx-batch-runner" in b for b in binds), (
        binds
    )


def test_a_child_without_a_created_marker_does_not_adopt_a_live_name(
    tmp_path: Path,
) -> None:
    """Same child shape but no marker (a broken host replayed argv, or someone
    exported the boundary): a live same-name session is a conflict, exit 3."""
    scene = Scene(tmp_path, project="mlx-batch-runner", live=("mlx-batch-runner",))
    result = _run(
        scene,
        f"vc-start --repo {shlex.quote(str(scene.root))}",
        tty=True,
        developer_root=True,
        extra_env={
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
        },
    )
    assert f"RC=[{EXIT_EXISTS}]" in result.stdout, result.stdout + result.stderr
    _assert_nothing_mutated(scene.calls())


def test_boundary_without_a_pty_fails_closed_without_a_second_terminal(
    tmp_path: Path,
) -> None:
    """Boundary set but still no PTY (a broken terminal host): no escalation
    loop, and the create-only path reaches the engine's own TTY refusal on
    the attach rather than inventing a window. The session it created stays
    for `vc-dashboard attach`."""
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(
        scene,
        f"vc-start --repo {shlex.quote(str(scene.root))}",
        developer_root=True,
        extra_env={
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
        },
    )
    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert scene.terminal_launches(wait=0.5) == []
    assert scene.live() == ["mlx-batch-runner"]


def test_rejected_terminal_host_is_reported_and_the_workspace_is_named(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(scene, "vc-start", extra_env={"VC_TERMINAL_EXIT": "2"})
    assert _rc(result) != 0, result.stdout
    assert "rejected this launch (exit 2)" in result.stderr, result.stderr
    assert (
        "workspace mlx-batch-runner exists (detached) but no terminal could be opened"
        in result.stderr
    ), result.stderr
    assert "vc-dashboard attach mlx-batch-runner" in result.stderr
    assert scene.live() == ["mlx-batch-runner"]


# --------------------------------------------------------------------------
# 6. TTY entry: attach outside a frame, switch the shared canvas inside one
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_tty_outside_a_frame_creates_and_attaches_without_a_terminal(
    tmp_path: Path, shell: str
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(
        scene,
        "vc-start",
        shell=shell,
        tty=True,
        developer_root=True,
        extra_env={
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
        },
    )
    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert "created workspace mlx-batch-runner" in result.stdout
    assert "TARGET=[mlx-batch-runner]" in result.stdout
    calls = scene.calls()
    assert len(_creates(calls)) == 1
    attaches = _attaches(calls)
    assert len(attaches) == 1 and attaches[0]["stdin_tty"] is True, calls
    assert attaches[0]["VC_FRAME_SESSION_NAME"] is None, attaches[0]
    assert not _switches(calls)
    assert scene.terminal_launches(wait=0.5) == []
    assert calls.index(_creates(calls)[0]) < calls.index(attaches[0]), (
        "attach before create"
    )
    binds = [c for c in scene.owner_calls() if "session-attach" in c]
    assert any(
        "--state live" in b and "--runtime-session-id mlx-batch-runner" in b
        for b in binds
    ), binds
    # The catalogue's own label never becomes the Frame name.
    assert not any("catalog-" in n for n in scene.live()), scene.live()


def test_tty_inside_an_attached_frame_switches_this_client_no_nested_multiplexer(
    tmp_path: Path,
) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    result = _run(
        scene,
        "vc-start",
        tty=True,
        developer_root=True,
        extra_env={
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "2",
            "VC_FRAME_SESSION_NAME": "other-place",
        },
    )
    assert "RC=[4]" in result.stdout, result.stdout + result.stderr
    assert "guest API" in result.stdout + result.stderr
    assert not _creates(scene.calls())
    assert not _attaches(scene.calls()) and not _switches(scene.calls())
    assert scene.terminal_launches(wait=0.5) == []
    assert scene.live() == ["other-place"]


# --------------------------------------------------------------------------
# 7. structural: both entrypoints share the owner; the help is true
# --------------------------------------------------------------------------


def test_both_entrypoints_parse_once_and_enter_the_same_owner() -> None:
    dispatch = DISPATCH_SH.read_text(encoding="utf-8")
    start_body = dispatch.split("vc-start()")[1].split("vc-dashboard()")[0]
    deck = DECK.read_text(encoding="utf-8")
    deck_body = deck.split("cmd_start()")[1].split("\n}\n")[0]
    for body in (start_body, deck_body):
        assert '_vetcoders_start_prepare_arguments "$@"' in body
        assert '_vetcoders_start_entry "${_vetcoders_start_frame_argv[@]}"' in body
        assert "_vetcoders_launch_dashboard" not in body
        assert "_vetcoders_start_open_terminal_if_needed" not in body
    assert (REPO_ROOT / "scripts" / "vibecrafted").read_text(encoding="utf-8") == deck


def test_deck_help_documents_the_contract_and_exit_codes() -> None:
    result = subprocess.run(
        ["bash", str(DECK), "start", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "NO_COLOR": "1"},
    )
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    for needle in (
        "vc-start [<workspace>] [--repo <project-path>]",
        "vibecrafted start --root <project-path>",
        "1-24 characters",
        "create-only",
        "0 entered",
        "2 usage/name/root",
        "3 workspace exists",
        "4 inventory/engine",
        "vc-start resume",
    ):
        assert needle in out, (needle, out)


def test_every_command_the_refusal_offers_exists_in_the_shipped_shell() -> None:
    """`vc-dashboard attach|switch` are dashboard.sh case arms; a fictitious
    verb in the message would be found here before a Founder finds it."""
    text = DASHBOARD_SH.read_text(encoding="utf-8")
    launcher = text.split("_vetcoders_launch_dashboard()")[1]
    assert "\n    attach)\n" in launcher
    assert "\n    switch)\n" in launcher
    refusal = text.split("_vetcoders_start_refuse_existing_workspace()")[1].split(
        "\n}\n"
    )[0]
    offered = {
        line.split(":", 1)[1].strip().split(" ")[0:2][0]
        + " "
        + line.split(":", 1)[1].strip().split(" ")[1]
        for line in refusal.splitlines()
        if "printf '    " in line
    }
    assert offered == {
        "vc-frame attach",
        "vc-dashboard switch",
        "vc-dashboard attach",
        "vc-start <new-name>",
        "vc-frame delete-session",
        "vc-frame kill-session",
    }, offered


# --------------------------------------------------------------------------
# 8. native evidence: the REAL engine, isolated
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
def test_real_engine_help_accepts_every_command_the_refusal_names() -> None:
    """Not fictitious: attach --create-background, list-sessions
    --no-formatting, action switch-session / list-clients, kill-session,
    delete-session --force."""
    assert _REAL_FRAME is not None

    def helptext(*args: str) -> str:
        result = subprocess.run(
            [str(_REAL_FRAME), *args, "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        assert result.returncode == 0, (args, result.stderr)
        return result.stdout + result.stderr

    top = helptext()
    for verb in ("attach", "kill-session", "delete-session", "list-sessions", "action"):
        assert f"\n    {verb} " in top or f"\n  {verb} " in top, (verb, top)
    assert "--create-background" in helptext("attach")
    assert "--no-formatting" in helptext("list-sessions")
    actions = helptext("action")
    assert "switch-session" in actions and "list-clients" in actions, actions
    assert "--force" in helptext("delete-session")


@pytest.mark.skipif(_REAL_FRAME is None, reason="no installed vc-frame engine")
def test_real_engine_inventory_and_exclusive_create_through_the_shipped_helpers(
    tmp_path: Path,
) -> None:
    """Against the REAL engine in a sandbox it can never share with the
    Founder (own short socket dir under /tmp, own config, own home):

    1. an empty namespace reads `missing` (the engine's exit-1 "No active
       vc-frame sessions found." is an answer, not an error);
    2. two concurrent shipped creates → exactly one 0 and one 3, one session;
    3. the name then reads `live`; after kill-session it reads `dead` (an
       EXITED record the engine would resurrect) and the public entry refuses
       it with exit 3 and the resurrect/delete commands, creating nothing;
    4. after delete-session --force the name reads `missing` again; the
       sandbox is empty afterwards.
    """
    assert _REAL_FRAME is not None
    tag = f"vcs{os.getpid() % 100000}"
    sandbox = Path("/tmp") / tag
    if sandbox.exists():
        shutil.rmtree(sandbox)
    (sandbox / "sock").mkdir(parents=True)
    (sandbox / "cfg" / "layouts").mkdir(parents=True)
    (sandbox / "home").mkdir()
    repo = sandbox / tag
    repo.mkdir()
    (sandbox / "cfg" / "config.kdl").write_text(
        "keybinds clear-defaults=true {}\n", encoding="utf-8"
    )
    layout = sandbox / "cfg" / "layouts" / "operator.kdl"
    layout.write_text("layout {\n}\n", encoding="utf-8")
    session = tag

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

    def shell(body: str, timeout: int = 90) -> subprocess.CompletedProcess[str]:
        script = "\n".join(
            [
                f'source "{SHELL_SH}"',
                f'_vetcoders_vc_frame_loaded_root="{REPO_ROOT}"',
                body,
            ]
        )
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", script],
            check=False,
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def state() -> str:
        result = shell(
            f'printf "STATE=[%s]\\n" "$(_vetcoders_start_session_inventory_state {session})"'
        )
        assert "STATE=[" in result.stdout, result.stdout + result.stderr
        return result.stdout.split("STATE=[", 1)[1].split("]", 1)[0]

    try:
        assert state() == "missing"

        create = "\n".join(
            [
                f'_vetcoders_start_create_workspace_session "{_REAL_FRAME}" {session} "{layout}"',
                'printf "CREATE=[%s]\\n" "$?"',
            ]
        )
        script = "\n".join(
            [
                f'source "{SHELL_SH}"',
                f'_vetcoders_vc_frame_loaded_root="{REPO_ROOT}"',
                create,
            ]
        )
        procs = [
            subprocess.Popen(
                ["bash", "--noprofile", "--norc", "-c", script],
                cwd=repo,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        outs = [p.communicate(timeout=120) for p in procs]
        codes = sorted(int(o[0].split("CREATE=[", 1)[1].split("]", 1)[0]) for o in outs)
        assert codes == [0, EXIT_EXISTS], outs
        listing = frame("list-sessions", "--no-formatting")
        assert listing.stdout.count(session) == 1, listing.stdout
        assert state() == "live"

        # The public entry, no TTY, against the live name: refuse, no window.
        entry = shell(
            f'vc-start --repo {shlex.quote(str(repo))}\nprintf "RC=[%s]\\n" "$?"'
        )
        assert f"RC=[{EXIT_EXISTS}]" in entry.stdout, entry.stdout + entry.stderr
        assert "already exists in vc-frame (live" in entry.stderr, entry.stderr

        kill = frame("kill-session", session)
        assert kill.returncode == 0, kill.stderr
        deadline = time.monotonic() + 10
        while state() != "dead" and time.monotonic() < deadline:
            time.sleep(0.2)
        assert state() == "dead"
        exited = frame("list-sessions", "--no-formatting")
        assert "(EXITED" in exited.stdout, exited.stdout

        entry = shell(
            f'vc-start --repo {shlex.quote(str(repo))}\nprintf "RC=[%s]\\n" "$?"'
        )
        assert f"RC=[{EXIT_EXISTS}]" in entry.stdout, entry.stdout + entry.stderr
        assert "EXITED session (a resurrection record" in entry.stderr, entry.stderr
        assert f"vc-frame attach {session}" in entry.stderr
        assert f"vc-frame delete-session {session}" in entry.stderr
        assert state() == "dead", "the entry resurrected or recreated the record"

        deleted = frame("delete-session", session, "--force")
        assert deleted.returncode == 0, deleted.stderr
        assert state() == "missing"
    finally:
        frame("kill-session", session)
        frame("delete-session", session, "--force")
        leftover = frame("list-sessions", "--no-formatting").stdout
        shutil.rmtree(sandbox, ignore_errors=True)
    assert session not in leftover, leftover
