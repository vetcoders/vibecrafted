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
* **exclusive create** -- adapter lock plus inventory, then Frame create;
  two concurrent starts yield exactly one workspace and one refusal.
* **enter** -- a caller with a terminal outside Frame attaches. Inside a live
  Frame host, start creates a `--guest-workspace` session and projects it with
  `vc-frame --session <host> project-workspace <guest> [--tab]`. Host identity
  is the attached owner, not the repo name. A caller without a TTY *outside*
  Frame creates first and then opens the VC Terminal; inherited `VC_FRAME_*`
  *inside* a host is the shared-canvas path, not a nested window.

Only the catalogue boundary and the Frame engine are stubbed. The stub refuses
what the real engine refuses, with the real engine's words, keeps an exclusive
on-disk session table so a race is a real race, and RESURRECTS an EXITED record
on `--create-background` exactly like the engine does -- so a start that skips
the inventory check fails here the way it would fail for the Founder. Unknown
verbs exit 2; `project-workspace` is an explicit command that emits one
compact `WorkspaceProjectionReceipt` and exits 0 only for Handled. The last
cases run against the REAL admitted or installed engine in an isolated sandbox.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
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
#     listed in VC_FRAME_CLIENTS;
#   * `project-workspace` requires `--session HOST`, refuses self/missing guest,
#     and prints one JSON WorkspaceProjectionReceipt (Handled only on a unique
#     host client). Unknown verbs exit 2 — never a silent accept.
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

no_guest_api = bool(os.environ.get("VC_FRAME_NO_PROJECT_WORKSPACE"))

if "--help" in argv:
    if no_guest_api and "project-workspace" in argv:
        sys.stderr.write("error: Found argument 'project-workspace' which wasn't expected\\n")
        sys.exit(2)
    if "project-workspace" in argv:
        print("Project an existing guest into a running host's VC Guest pane")
        print("Target the host with `--session <host>`.")
        print("USAGE:")
        print("    vc-frame project-workspace [OPTIONS] <SESSION_NAME>")
        print("        --tab <TAB>")
        sys.exit(0)
    print("Commands: action attach delete-session kill-session list-sessions")
    if not no_guest_api:
        print("         project-workspace")
        print("        --guest-workspace")
        print("        --session <SESSION>")
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
layout = None
guest_workspace = False
tab = None
rest = list(argv)
i = 0
while i < len(rest):
    token = rest[i]
    if token == "--session" and i + 1 < len(rest):
        session = rest[i + 1]
        i += 2
        continue
    if token == "--new-session-with-layout" and i + 1 < len(rest):
        layout = rest[i + 1]
        i += 2
        continue
    if token == "--layout" and i + 1 < len(rest):
        layout = rest[i + 1]
        i += 2
        continue
    if token == "--guest-workspace":
        guest_workspace = True
        i += 1
        continue
    if token == "--tab" and i + 1 < len(rest):
        tab = rest[i + 1]
        i += 2
        continue
    break
rest = rest[i:]

if rest[:2] == ["attach", "--create-background"]:
    name = rest[2] if len(rest) > 2 else session
    panic_if_current(name)
    forced = os.environ.get("VC_FRAME_CREATE_ERROR", "")
    if forced:
        sys.stderr.write(forced + "\\n")
        sys.exit(1)
    _FRAME_BUILTIN_LAYOUTS = (
        "default",
        "vibecrafted",
        "vibecrafted-host",
        "vibecrafted-guest",
        "vc-workflow",
        "vc-marbles",
        "vc-research",
        "operator",
    )
    if (
        layout is not None
        and not os.path.isfile(layout)
        and layout not in _FRAME_BUILTIN_LAYOUTS
    ):
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
    record({"created": name, "guest_workspace": guest_workspace})
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
        if "--json" in rest:
            position = max(int(os.environ.get("VC_FRAME_ACTIVE_TAB", "1")) - 1, 0)
            # Pretty-printed TabInfo array — the real engine's list-tabs --json.
            print(json.dumps([
                {
                    "name": "Tab",
                    "position": position,
                    "active": True,
                    "tab_id": position,
                    "panes_to_hide": 0,
                    "viewport_rows": 24,
                    "viewport_columns": 80,
                }
            ], indent=2))
        else:
            print("TAB_ID  POSITION  NAME")
        sys.exit(0)
    if verb == "list-panes":
        projected_dir = os.path.join(table, "projected")
        projected = sorted(os.listdir(projected_dir)) if os.path.isdir(projected_dir) else []
        marker = os.path.join(table, "panes-seen")
        drifted = bool(os.environ.get("VC_FRAME_PANES_DRIFT")) and os.path.exists(marker)
        open(marker, "w").write("1")
        panes = [
            {"title": "session-manager", "id": 1, "is_plugin": True, "tab_name": "Tab"},
            {"title": "VC Guest", "id": 2, "is_plugin": True, "tab_name": "Tab"},
            {
                "title": target,
                "id": 3,
                "terminal_command": "zsh",
                "tab_name": "Tab",
                "cursor_coordinates_in_pane": [1, 1],
            },
        ]
        if drifted:
            panes[2]["cursor_coordinates_in_pane"] = [8, 4]
            panes.append({"title": "unrelated-drift", "id": 4, "tab_name": "Tab"})
        for name in projected:
            panes.append({
                "title": name,
                "id": 9,
                "terminal_command": "--workspace-projection " + name,
                "tab_name": "Tab",
            })
        if os.environ.get("VC_FRAME_RECONCILE_GUEST") and os.environ.get("VC_FRAME_PROJECT_MALFORMED"):
            forced = os.environ.get("VC_FRAME_RECONCILE_GUEST")
            if forced and not any(p.get("title") == forced for p in panes):
                panes.append({
                    "title": forced,
                    "id": 9,
                    "terminal_command": "--workspace-projection " + forced,
                    "tab_name": "Tab",
                })
        if "--json" in rest:
            print(json.dumps(panes))
        else:
            print("ID TITLE")
            for pane in panes:
                print("%s %s" % (pane.get("id"), pane.get("title")))
        sys.exit(0)
    sys.stderr.write("error: Found argument '%s' which wasn't expected\\n" % verb)
    sys.exit(2)

if rest[:1] == ["project-workspace"]:
    if no_guest_api:
        sys.stderr.write("error: Found argument 'project-workspace' which wasn't expected\\n")
        sys.exit(2)
    guest = None
    i = 1
    while i < len(rest):
        if rest[i] == "--tab" and i + 1 < len(rest):
            tab = rest[i + 1]
            i += 2
            continue
        if not str(rest[i]).startswith("-") and guest is None:
            guest = rest[i]
            i += 1
            continue
        i += 1
    if not session:
        sys.stderr.write("project-workspace requires --session <host>\\n")
        sys.exit(2)
    if guest is None:
        sys.stderr.write("project-workspace requires a guest session\\n")
        sys.exit(2)
    if session == guest:
        sys.stderr.write(
            "Refused: `%s` cannot project into itself. Zero process/pane mutation.\\n" % guest
        )
        sys.exit(2)
    if guest not in names("live"):
        sys.stderr.write(
            "Refused: guest `%s` is missing. Zero process/pane mutation.\\n" % guest
        )
        sys.exit(2)
    if session not in names("live"):
        sys.stderr.write("No session with the name '%s' found!\\n" % session)
        sys.exit(1)
    tab_zero = None
    if tab is not None:
        tab_n = int(tab)
        if tab_n < 1:
            sys.stderr.write("--tab is one-based and must be at least 1\\n")
            sys.exit(2)
        tab_zero = tab_n - 1
    status = os.environ.get("VC_FRAME_PROJECT_STATUS", "")
    if not status:
        status = "Handled" if len(clients_of(session)) == 1 else "Refused"
    request_id = os.environ.get("VC_FRAME_PROJECT_REQUEST_ID") or ("req-%s" % time.time())
    receipt_guest = "someone-else" if os.environ.get("VC_FRAME_PROJECT_WRONG_GUEST") else guest
    receipt = {
        "request_id": request_id,
        "client_id": 1,
        "plugin_id": 2,
        "guest": receipt_guest,
        "tab": tab_zero,
        "pane_id": 9 if status == "Handled" else None,
        "status": status,
        "detail": "stub",
    }
    record({"projected": [session, guest], "tab": tab, "receipt": receipt})
    if os.environ.get("VC_FRAME_PROJECT_NO_RECEIPT"):
        sys.stderr.write(
            "Unavailable: no unique correlated projection receipt for request %s; the surface may have changed.\\n"
            % request_id
        )
        sys.exit(2)
    if os.environ.get("VC_FRAME_PROJECT_MALFORMED"):
        print("{this is not a WorkspaceProjectionReceipt")
        sys.exit(0)
    if status == "Handled" and receipt_guest == guest:
        os.makedirs(os.path.join(table, "projected"), exist_ok=True)
        open(os.path.join(table, "projected", guest), "w").write("1")
    print(json.dumps(receipt, separators=(",", ":")))
    forced = os.environ.get("VC_FRAME_PROJECT_EXIT")
    if forced:
        sys.exit(int(forced))
    sys.exit(0 if status == "Handled" and receipt_guest == guest else 2)

sys.stderr.write(
    "error: Found argument '%s' which wasn't expected\\n" % (rest[0] if rest else "")
)
sys.exit(2)
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


def _seed_committed_git(root: Path, *, home: Path | None = None) -> str:
    """Make ``root`` a genuine repository with a resolvable HEAD commit.

    ``git init`` alone leaves HEAD unborn; launch-spec ``--base`` (default
    HEAD) and any commit-based start path refuse that fixture. Create-only
    start can name a top-level without a commit, but tests that opt into
    ``git=True`` must still be real repositories.
    """
    root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if home is not None:
        env["HOME"] = str(home)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_AUTHOR_NAME"] = "start-fixture"
    env["GIT_AUTHOR_EMAIL"] = "start-fixture@example.invalid"
    env["GIT_COMMITTER_NAME"] = "start-fixture"
    env["GIT_COMMITTER_EMAIL"] = "start-fixture@example.invalid"
    subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
        capture_output=True,
        env=env,
    )
    marker = root / "README"
    if not marker.exists():
        marker.write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "-A"],
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=start-fixture",
            "-c",
            "user.email=start-fixture@example.invalid",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
        capture_output=True,
        env=env,
    )
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    sha = head.stdout.strip()
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), sha
    return sha


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
        self.head = _seed_committed_git(self.root, home=self.home) if git else ""
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


def _eval_start_fn(
    body: str,
    *,
    extra_env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    """Execute a real dashboard.sh function. Payload tests must call these,
    not a Python mirror of their parser — the baseline stdin/heredoc bug
    only fails when the shell function itself runs."""
    script = "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{REPO_ROOT}"',
            body,
            'printf "RC=[%s]\\n" "$?"',
        ]
    )
    env = os.environ.copy()
    for key in IDENTITY_ENV:
        env.pop(key, None)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        check=False,
        cwd=cwd or REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
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


def _projects(calls: list[dict]) -> list[dict]:
    return [
        c
        for c in calls
        if "project-workspace" in c["argv"] and "--help" not in c["argv"]
    ]


def _workspace_projection_receipts(text: str) -> list[dict]:
    """Collect WorkspaceProjectionReceipt objects from engine/launcher text.

    fd14 prints one compact serde_json document; the stub now matches that
    representation. Pretty and log-prefixed forms are accepted so a test can
    feed the real function, not a Python mirror of its parser.
    """
    decoder = json.JSONDecoder()
    rows: list[dict] = []
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx] not in "{[":
            idx += 1
        if idx >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            idx += 1
            continue
        idx = end
        candidates = obj if isinstance(obj, list) else [obj]
        for item in candidates:
            if (
                isinstance(item, dict)
                and "request_id" in item
                and "guest" in item
                and "status" in item
            ):
                rows.append(item)
    return rows


def _list_panes_payload(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _pane_by_id(panes, pane_id):
    if not isinstance(panes, list) or pane_id in (None, ""):
        return None
    for pane in panes:
        if isinstance(pane, dict) and pane.get("id") == pane_id:
            return pane
    return None


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
    assert not _projects(calls), f"a projection was attempted: {calls}"
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
    assert scene.head, "git=True scenes must be committed repositories"
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
    """Create-only start: no Git means the caller's directory is the root.

    Launch-spec ``require_git`` / default ``--base HEAD`` is a different
    owner. ``vc-start`` without ``--base``/``--worktree`` must not refuse a
    plain directory, and must not invert this into a refusal-only success.
    """
    scene = Scene(tmp_path, project="plain-dir")
    assert not scene.head
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


def _inside_host_env(scene: Scene, host: str = "other-place") -> dict[str, str]:
    return {
        "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
        "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
        "VC_FRAME": "1",
        "VC_FRAME_PANE_ID": "2",
        "VC_FRAME_SESSION_NAME": host,
        "ZELLIJ_SESSION_NAME": host,
    }


def _assert_projected_into_host(
    scene: Scene, *, host: str, guest: str, result: subprocess.CompletedProcess[str]
) -> dict:
    combined = result.stdout + result.stderr
    assert "created workspace " + guest in combined, combined
    assert "projected workspace " + guest in combined, combined
    receipts = _workspace_projection_receipts(result.stdout)
    assert len(receipts) == 1, result.stdout
    receipt = receipts[0]
    assert receipt.get("status") == "Handled", receipt
    assert receipt.get("guest") == guest, receipt
    assert receipt.get("pane_id") not in (None, ""), receipt
    assert str(receipt.get("request_id") or "").strip(), receipt
    assert "confirmed by host list-panes" not in combined
    assert not _switches(scene.calls()), scene.calls()
    assert not _attaches(scene.calls()), scene.calls()
    assert scene.terminal_launches(wait=0.3) == []
    projects = _projects(scene.calls())
    assert projects, scene.calls()
    argv = projects[-1]["argv"]
    assert argv[argv.index("--session") + 1] == host, argv
    assert "project-workspace" in argv and guest in argv
    assert host != guest
    assert argv[argv.index("--session") + 1] != guest
    recorded = projects[-1].get("receipt") or {}
    assert recorded.get("pane_id") == receipt.get("pane_id"), (recorded, receipt)
    guest_creates = [
        c
        for c in scene.calls()
        if c.get("created") == guest and c.get("guest_workspace") is True
    ]
    assert guest_creates, scene.calls()
    assert "--guest-workspace" in guest_creates[0]["argv"]
    guest_argv = guest_creates[0]["argv"]
    assert "--new-session-with-layout" in guest_argv
    assert guest_argv[guest_argv.index("--new-session-with-layout") + 1] == "vibecrafted"
    return projects[-1]


# --------------------------------------------------------------------------
# 5. no TTY: inherited Frame env is not a terminal; the child enters what the
#    parent created; the boundary stops recursion
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_no_tty_inside_an_attached_frame_creates_guest_and_projects(
    tmp_path: Path, shell: str
) -> None:
    """Agent-tool P0: markers present, host live and watched, no TTY.
    Shared-canvas create+project — not a nested terminal and not switch-session."""
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    env = _inside_host_env(scene)
    env["VC_FRAME_PANE_ID"] = "7"
    env["VIBECRAFTED_OPERATOR_SESSION"] = "other-place"
    result = _run(
        scene,
        "vc-start",
        shell=shell,
        developer_root=True,
        extra_env=env,
    )

    assert _rc(result) == 0, result.stdout + result.stderr
    _assert_projected_into_host(
        scene, host="other-place", guest="mlx-batch-runner", result=result
    )
    assert scene.live() == ["mlx-batch-runner", "other-place"]


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
# 6. TTY entry: attach outside a frame; project a guest inside one
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
    assert not _projects(calls)
    assert "--guest-workspace" not in _creates(calls)[0]["argv"]
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


def test_tty_inside_an_attached_frame_projects_guest_no_nested_multiplexer(
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
        extra_env=_inside_host_env(scene),
    )
    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    _assert_projected_into_host(
        scene, host="other-place", guest="mlx-batch-runner", result=result
    )
    assert scene.live() == ["mlx-batch-runner", "other-place"]
    assert "TARGET=[mlx-batch-runner]" in result.stdout


def test_inside_host_refuses_older_frame_before_create(tmp_path: Path) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    env = _inside_host_env(scene)
    env["VC_FRAME_NO_PROJECT_WORKSPACE"] = "1"
    result = _run(scene, "vc-start", extra_env=env)
    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    assert "guest API" in result.stderr
    assert scene.live() == ["other-place"]
    _assert_nothing_mutated(scene.calls())


def test_inside_host_refuses_stale_host_env_before_create(tmp_path: Path) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("decoy",),
        clients=("decoy",),
    )
    result = _run(
        scene,
        "vc-start",
        extra_env=_inside_host_env(scene, host="ghost-host"),
    )
    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    assert "attached owner" in result.stderr
    assert scene.live() == ["decoy"]
    _assert_nothing_mutated(scene.calls())


def test_inside_host_refuses_ambiguous_clients_before_create(tmp_path: Path) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place", "other-place"),
    )
    result = _run(scene, "vc-start", extra_env=_inside_host_env(scene))
    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    assert "exactly one attached client" in result.stderr
    assert scene.live() == ["other-place"]
    _assert_nothing_mutated(scene.calls())


def test_inside_host_targets_attached_owner_not_repo_or_decoy(tmp_path: Path) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place", "decoy"),
        clients=("other-place",),
    )
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        extra_env=_inside_host_env(scene),
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    projected = _assert_projected_into_host(
        scene, host="other-place", guest="mlx-batch-runner", result=result
    )
    argv = projected["argv"]
    assert "decoy" not in argv
    assert argv[argv.index("--session") + 1] != "mlx-batch-runner"
    assert scene.live() == ["decoy", "mlx-batch-runner", "other-place"]


def test_inside_host_created_but_not_projected_keeps_host_canvas(
    tmp_path: Path,
) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    env = _inside_host_env(scene)
    env["VC_FRAME_PROJECT_STATUS"] = "Unavailable"
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        extra_env=env,
    )
    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    assert "created but not projected" in result.stderr
    assert "previous canvas was left unchanged" in result.stderr
    assert not _switches(scene.calls())
    assert scene.live() == ["mlx-batch-runner", "other-place"]
    assert any(c.get("created") == "mlx-batch-runner" for c in scene.calls())


def test_inside_host_does_not_treat_pipe_exit_as_adoption(tmp_path: Path) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    env = _inside_host_env(scene)
    env["VC_FRAME_PROJECT_NO_RECEIPT"] = "1"
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        extra_env=env,
    )
    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "created workspace mlx-batch-runner" in combined
    assert not [
        receipt
        for receipt in _workspace_projection_receipts(combined)
        if receipt.get("status") == "Handled"
    ], combined
    assert "projected workspace mlx-batch-runner" not in combined
    assert scene.live() == ["mlx-batch-runner", "other-place"]


def test_inside_host_malformed_ack_does_not_claim_unchanged_without_owner(
    tmp_path: Path,
) -> None:
    """Parse failure is not proof the canvas stayed put. Drift the owner
    list-panes so reconcile cannot attest identity, and refuse the
    unchanged guarantee."""
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    env = _inside_host_env(scene)
    env["VC_FRAME_PROJECT_MALFORMED"] = "1"
    env["VC_FRAME_PANES_DRIFT"] = "1"
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        extra_env=env,
    )
    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    assert "not confirmed" in result.stderr
    assert "not known to be unchanged" in result.stderr
    assert "previous canvas was left unchanged" not in result.stderr
    assert scene.live() == ["mlx-batch-runner", "other-place"]


def test_inside_host_guest_named_pane_is_not_projection_proof(
    tmp_path: Path,
) -> None:
    """list-panes has no public guest binding. A pane titled the guest, or a
    command that echoes the guest, must not certify a missing ACK."""
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    env = _inside_host_env(scene)
    env["VC_FRAME_PROJECT_MALFORMED"] = "1"
    env["VC_FRAME_RECONCILE_GUEST"] = "mlx-batch-runner"
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        extra_env=env,
    )
    combined = result.stdout + result.stderr
    assert _rc(result) == EXIT_INVENTORY, combined
    assert "projected workspace mlx-batch-runner" not in combined
    assert "confirmed by host list-panes" not in combined
    assert "not confirmed" in result.stderr
    assert scene.live() == ["mlx-batch-runner", "other-place"]


def test_inside_host_nonzero_engine_status_is_not_ordinary_success(
    tmp_path: Path,
) -> None:
    """A Handled-looking body with a nonzero Frame exit is not adoption."""
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    env = _inside_host_env(scene)
    env["VC_FRAME_PROJECT_STATUS"] = "Handled"
    env["VC_FRAME_PROJECT_EXIT"] = "2"
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        extra_env=env,
    )
    combined = result.stdout + result.stderr
    assert _rc(result) == EXIT_INVENTORY, combined
    assert "projected workspace mlx-batch-runner" not in combined
    assert "created workspace mlx-batch-runner" in combined
    assert scene.live() == ["mlx-batch-runner", "other-place"]


def test_inside_host_operator_layout_alias_projects(tmp_path: Path) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    result = _run(
        scene,
        "vc-start operator",
        developer_root=True,
        extra_env=_inside_host_env(scene),
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    _assert_projected_into_host(
        scene, host="other-place", guest="mlx-batch-runner", result=result
    )


def test_inside_host_deck_start_projects(tmp_path: Path) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    result = _run(
        scene,
        f"{shlex.quote(str(DECK))} start --repo {shlex.quote(str(scene.root))}",
        developer_root=True,
        extra_env=_inside_host_env(scene),
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    _assert_projected_into_host(
        scene, host="other-place", guest="mlx-batch-runner", result=result
    )


def test_two_concurrent_inside_host_starts_create_exactly_one_guest(
    tmp_path: Path,
) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("other-place",),
        clients=("other-place",),
    )
    env = scene.env(_inside_host_env(scene))
    script = _entry_script(scene, "vc-start", developer_root=True)
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
    assert scene.live() == ["mlx-batch-runner", "other-place"]
    assert not _switches(scene.calls())
    created = [c for c in scene.calls() if c.get("created") == "mlx-batch-runner"]
    assert len(created) == 1, scene.calls()
    winner = next(o for o in outs if "RC=[0]" in o[0])
    assert "projected workspace mlx-batch-runner" in winner[0] + winner[1]


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


def test_start_entry_owns_inside_host_projection_not_switch_session() -> None:
    text = DASHBOARD_SH.read_text(encoding="utf-8")
    entry = text.split("_vetcoders_start_entry()")[1]
    assert "_vetcoders_start_inside_host_guest" in entry
    assert "action switch-session" not in entry
    assert "_vetcoders_start_projection_receipt_ok" in text
    assert "_vetcoders_start_classify_projection" in text
    assert "_vetcoders_start_reconcile_host_projection" in text
    assert "project-workspace" in text
    enter = text.split("_vetcoders_start_enter_workspace_session()")[1].split(
        "\n}\n"
    )[0]
    assert "action switch-session" not in enter


# --------------------------------------------------------------------------
# 7b. function-level: execute the real shell parsers (compact ACK, no argv leak)
# --------------------------------------------------------------------------

_HANDLED_RECEIPT = {
    "request_id": "pipe-1",
    "client_id": 1,
    "plugin_id": 2,
    "guest": "mlx-batch-runner",
    "tab": 0,
    "pane_id": 9,
    "status": "Handled",
    "detail": "projected",
}


def test_start_resolve_host_tab_reads_real_list_tabs_json(tmp_path: Path) -> None:
    """Pretty-printed TabInfo array on the engine's stdout. JSON is stdin
    to python -c; putting the listing on python argv is the leak this
    cut closes."""
    fake = tmp_path / "vc-frame"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps([\n"
        "    {\n"
        '        "name": "Tab",\n'
        '        "position": 1,\n'
        '        "active": True,\n'
        '        "tab_id": 1,\n'
        '        "panes_to_hide": 0,\n'
        '        "viewport_rows": 24,\n'
        '        "viewport_columns": 80,\n'
        "    }\n"
        "], indent=2))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    result = _eval_start_fn(
        "\n".join(
            [
                f'tab="$(_vetcoders_start_resolve_host_tab host {shlex.quote(str(fake))})"',
                "fn_rc=$?",
                'printf "TAB=[%s]\\n" "$tab"',
                'printf "FN_RC=[%s]\\n" "$fn_rc"',
            ]
        )
    )
    assert "FN_RC=[0]" in result.stdout, result.stdout + result.stderr
    assert "TAB=[2]" in result.stdout, result.stdout + result.stderr


def test_start_projection_receipt_ok_accepts_real_handled_receipt() -> None:
    compact = json.dumps(_HANDLED_RECEIPT, separators=(",", ":"))
    pretty = json.dumps(_HANDLED_RECEIPT, indent=2)
    for receipt in (compact, pretty):
        result = _eval_start_fn(
            "\n".join(
                [
                    f"_vetcoders_start_projection_receipt_ok {shlex.quote(receipt)} mlx-batch-runner 1",
                    'printf "FN_RC=[%s]\\n" "$?"',
                ]
            )
        )
        assert "FN_RC=[0]" in result.stdout, receipt + result.stdout + result.stderr


def test_start_projection_receipt_ok_rejects_refused_and_malformed() -> None:
    refused = dict(_HANDLED_RECEIPT)
    refused["status"] = "Refused"
    refused["pane_id"] = None
    for payload, guest, tab in (
        (json.dumps(refused, separators=(",", ":")), "mlx-batch-runner", "1"),
        ("{this is not a WorkspaceProjectionReceipt", "mlx-batch-runner", "1"),
        (json.dumps(_HANDLED_RECEIPT, separators=(",", ":")), "someone-else", "1"),
    ):
        result = _eval_start_fn(
            "\n".join(
                [
                    f"_vetcoders_start_projection_receipt_ok {shlex.quote(payload)} {guest} {tab}",
                    'printf "FN_RC=[%s]\\n" "$?"',
                ]
            )
        )
        assert "FN_RC=[0]" not in result.stdout, (payload, result.stdout + result.stderr)
        assert "FN_RC=[1]" in result.stdout, result.stdout + result.stderr


def test_start_classify_projection_accepts_real_receipt_representations() -> None:
    """Execute the shipped classifier. Compact is the fd14 emission; pretty
    and log-prefixed must not be double-counted into indeterminate."""
    compact = json.dumps(_HANDLED_RECEIPT, separators=(",", ":"))
    pretty = json.dumps(_HANDLED_RECEIPT, indent=2)
    prefixed = "vc-frame[host]: " + compact
    cases = (
        (compact, "handled"),
        (pretty, "handled"),
        (prefixed, "handled"),
        (
            json.dumps({**_HANDLED_RECEIPT, "status": "Refused", "pane_id": None}, separators=(",", ":")),
            "refused",
        ),
        (
            "Refused: guest `gone` is missing. Zero process/pane mutation.\n",
            "refused",
        ),
        (
            "Unavailable: no unique correlated projection receipt for request x; the surface may have changed.\n",
            "indeterminate",
        ),
        (
            json.dumps({**_HANDLED_RECEIPT, "status": "Unavailable", "pane_id": None}, separators=(",", ":")),
            "indeterminate",
        ),
        ("{this is not a WorkspaceProjectionReceipt", "indeterminate"),
        (compact + "\n" + compact, "indeterminate"),
        (
            compact
            + "\n"
            + json.dumps({**_HANDLED_RECEIPT, "request_id": "pipe-2"}, separators=(",", ":")),
            "indeterminate",
        ),
        (
            json.dumps({**_HANDLED_RECEIPT, "guest": "someone-else"}, separators=(",", ":")),
            "indeterminate",
        ),
        (
            json.dumps({**_HANDLED_RECEIPT, "tab": 4}, separators=(",", ":")),
            "indeterminate",
        ),
        (
            json.dumps({**_HANDLED_RECEIPT, "request_id": ""}, separators=(",", ":")),
            "indeterminate",
        ),
    )
    for text, expected in cases:
        result = _eval_start_fn(
            "\n".join(
                [
                    f'printf "CLASS=[%s]\\n" "$(_vetcoders_start_classify_projection {shlex.quote(text)} mlx-batch-runner 1)"',
                ]
            )
        )
        assert f"CLASS=[{expected}]" in result.stdout, (
            expected,
            text,
            result.stdout + result.stderr,
        )


def test_start_reconcile_host_projection_ignores_guest_substrings() -> None:
    """Unrelated title/command mentions are not Frame guest binding."""
    guest = "mlx-batch-runner"
    host_pane = {
        "id": 3,
        "title": guest,
        "terminal_command": "echo " + guest,
        "name": guest,
        "cursor_coordinates_in_pane": [1, 1],
    }
    same = json.dumps([host_pane])
    drifted = json.dumps(
        [
            {
                **host_pane,
                "cursor_coordinates_in_pane": [8, 4],
                "title": "echo " + guest,
            },
            {
                "id": 4,
                "title": guest,
                "terminal_command": "echo " + guest,
            },
        ]
    )
    cases = (
        (same, same, "unchanged"),
        (same, drifted, "unknown"),
        ("", drifted, "unknown"),
    )
    for before, after, expected in cases:
        result = _eval_start_fn(
            "\n".join(
                [
                    f'printf "REC=[%s]\\n" "$(_vetcoders_start_reconcile_host_projection {shlex.quote(before)} {shlex.quote(after)} {guest})"',
                ]
            )
        )
        assert f"REC=[{expected}]" in result.stdout, (
            expected,
            result.stdout + result.stderr,
        )
        assert "REC=[projected]" not in result.stdout, result.stdout


def test_start_parsers_keep_private_json_off_python_argv(tmp_path: Path) -> None:
    secret = "private-pane-cmd-" + uuid.uuid4().hex
    log = tmp_path / "python-argv.json"
    real = shutil.which("python3")
    assert real
    spy = tmp_path / "spy-python"
    spy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"log = {str(log)!r}\n"
        "try:\n"
        "    prev = json.loads(open(log, encoding='utf-8').read())\n"
        "except Exception:\n"
        "    prev = []\n"
        "prev.append(list(sys.argv[1:]))\n"
        "open(log, 'w', encoding='utf-8').write(json.dumps(prev))\n"
        f"os.execv({real!r}, [{real!r}] + sys.argv[1:])\n",
        encoding="utf-8",
    )
    spy.chmod(0o755)
    receipt = dict(_HANDLED_RECEIPT)
    receipt["detail"] = secret
    panes = json.dumps(
        [{"id": 3, "title": "host", "terminal_command": secret}]
    )
    fake = tmp_path / "vc-frame"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps([{"
        '"name":"Tab","position":0,"active":True,"tab_id":0,'
        '"panes_to_hide":0,"viewport_rows":24,"viewport_columns":80'
        "}], indent=2))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    result = _eval_start_fn(
        "\n".join(
            [
                f'printf "CLASS=[%s]\\n" "$(_vetcoders_start_classify_projection {shlex.quote(json.dumps(receipt, separators=(",", ":")))} mlx-batch-runner 1)"',
                f'printf "REC=[%s]\\n" "$(_vetcoders_start_reconcile_host_projection {shlex.quote(panes)} {shlex.quote(panes)} mlx-batch-runner)"',
                f'tab="$(_vetcoders_start_resolve_host_tab host {shlex.quote(str(fake))})"',
                'printf "TAB=[%s]\\n" "$tab"',
            ]
        ),
        extra_env={"VIBECRAFTED_PYTHON": str(spy)},
    )
    assert "CLASS=[handled]" in result.stdout, result.stdout + result.stderr
    assert "REC=[unchanged]" in result.stdout, result.stdout + result.stderr
    assert "TAB=[1]" in result.stdout, result.stdout + result.stderr
    logged = json.loads(log.read_text(encoding="utf-8"))
    blob = json.dumps(logged)
    assert secret not in blob, logged


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

_ADMITTED_FRAME_DEFAULT = Path(
    "/Volumes/vc-workspace/vetcoders/vibecrafted-suite/vc-frame/target/release/vc-frame"
)


def _admitted_frame() -> Path | None:
    """Donor fd14 public binary. Not the installed generation (may be older)."""
    for candidate in (
        os.environ.get("VC_FRAME_ADMITTED_BIN", ""),
        str(_ADMITTED_FRAME_DEFAULT),
    ):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return Path(candidate)
    return None


_ADMITTED_FRAME = _admitted_frame()


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
    sandbox = _exclusive_sandbox("vcs-x-")
    (sandbox / "sock").mkdir(parents=True)
    (sandbox / "cfg" / "layouts").mkdir(parents=True)
    (sandbox / "home").mkdir()
    (sandbox / "tmp").mkdir()
    repo = sandbox / "repo"
    repo.mkdir()
    (sandbox / "cfg" / "config.kdl").write_text(
        "keybinds clear-defaults=true {}\n", encoding="utf-8"
    )
    shipped = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "config"
        / "vc-frame"
        / "layouts"
        / "operator.kdl"
    )
    layout = sandbox / "cfg" / "layouts" / "operator.kdl"
    shutil.copy2(shipped, layout)
    session = _short_token("x")

    env = os.environ.copy()
    for key in IDENTITY_ENV:
        env.pop(key, None)
    for key in (
        "ZELLIJ_SOCKET_DIR",
        "ZELLIJ_CONFIG_DIR",
        "ZELLIJ_CONFIG_FILE",
        "XDG_RUNTIME_DIR",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "TMPDIR",
        "VC_FRAME_SOCKET_DIR",
    ):
        env.pop(key, None)
    env.update(
        {
            "HOME": str(sandbox / "home"),
            "VIBECRAFTED_HOME": str(sandbox / "home" / ".vibecrafted"),
            "XDG_CONFIG_HOME": str(sandbox / "home" / ".config"),
            "XDG_DATA_HOME": str(sandbox / "home" / ".local" / "share"),
            "XDG_STATE_HOME": str(sandbox / "home" / ".local" / "state"),
            "XDG_RUNTIME_DIR": str(sandbox / "tmp"),
            "TMPDIR": str(sandbox / "tmp"),
            "VC_FRAME_SOCKET_DIR": str(sandbox / "sock"),
            "ZELLIJ_SOCKET_DIR": str(sandbox / "sock"),
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


_PTY_ATTACH_PY = r"""
import fcntl, os, pty, select, signal, struct, sys, termios, time

binary, session, attached_path, release_path = sys.argv[1:5]
pid, fd = pty.fork()
if pid == 0:
    fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 160, 0, 0))
    os.environ["TERM"] = "xterm-256color"
    os.execvpe(binary, [binary, "attach", session], os.environ)

screen_path = attached_path + ".screen"
deadline = time.time() + 25
saw = False
with open(screen_path, "wb") as screen:
    while time.time() < deadline and not saw:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if chunk:
                screen.write(chunk)
                screen.flush()
                time.sleep(0.5)
                ended, status = os.waitpid(pid, os.WNOHANG)
                if ended:
                    sys.stderr.write("pty client exited during startup: " + str(status) + "\n")
                    sys.exit(2)
                saw = True
                with open(attached_path, "w", encoding="utf-8") as handle:
                    handle.write(str(pid))
        time.sleep(0.05)

    if not saw:
        sys.stderr.write("pty attach produced no output\n")
        sys.exit(2)

    while not os.path.exists(release_path):
        ready, _, _ = select.select([fd], [], [], 0.25)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            screen.write(chunk)
            screen.flush()
        time.sleep(0.05)

try:
    os.close(fd)
except OSError:
    pass
try:
    os.kill(pid, signal.SIGHUP)
except OSError:
    pass
try:
    os.waitpid(pid, 0)
except ChildProcessError:
    pass
"""


# Host chrome is owned by plugin_url, not by guessed titles. Real vibecrafted-host
# panes (W2 short-TMPDIR listing): vc-frame:link, vc-frame:vc-tab-title,
# frame-host ("Sessions"), session-manager ("VC Guest"). Title is never the URL.
_HOST_CHROME_PLUGIN_URLS = (
    "vc-frame:link",
    "vc-frame:vc-tab-title",
    "frame-host",
    "session-manager",
)
_SHORT_TEMP_ROOT = "/tmp"


def _pane_plugin_url(pane: dict) -> str:
    return str(pane.get("plugin_url") or "").strip()


def _host_chrome_plugins(rows) -> dict[str, dict]:
    owned: dict[str, dict] = {}
    if not isinstance(rows, list):
        return owned
    for pane in rows:
        if not isinstance(pane, dict):
            continue
        url = _pane_plugin_url(pane)
        if url in _HOST_CHROME_PLUGIN_URLS and url not in owned:
            owned[url] = pane
    return owned


def _chrome_geometry(rows) -> dict[str, tuple]:
    return {
        url: (
            pane.get("id"),
            pane.get("plugin_runtime_id"),
            pane.get("pane_x"),
            pane.get("pane_y"),
            pane.get("pane_rows"),
            pane.get("pane_columns"),
        )
        for url, pane in _host_chrome_plugins(rows).items()
    }


def _host_chrome_ready(rows):
    """Single host chrome: one session-manager and one frame-host plugin."""
    if not isinstance(rows, list) or not rows:
        return []
    urls = [
        _pane_plugin_url(pane)
        for pane in rows
        if isinstance(pane, dict) and _pane_plugin_url(pane) in _HOST_CHROME_PLUGIN_URLS
    ]
    if urls.count("session-manager") == 1 and urls.count("frame-host") == 1:
        return rows
    return []


def _assert_host_chrome(rows, *, previous_geometry=None):
    """Exact plugin_url ownership + stable chrome IDs/geometry; never title==URL."""
    assert _host_chrome_ready(rows), rows
    plugins = _host_chrome_plugins(rows)
    guest = plugins["session-manager"]
    host = plugins["frame-host"]
    assert _pane_plugin_url(guest) == "session-manager", guest
    assert _pane_plugin_url(host) == "frame-host", host
    assert guest.get("title") != "session-manager", guest
    assert host.get("title") != "frame-host", host
    assert guest.get("title") == "VC Guest", guest
    geometry = _chrome_geometry(rows)
    assert set(geometry) >= {"session-manager", "frame-host"}, geometry
    if previous_geometry is not None:
        assert geometry == previous_geometry, (previous_geometry, geometry)
    return geometry


def _exclusive_sandbox(prefix: str = "vcs-") -> Path:
    """Short exclusive socket-capable root. Default $TMPDIR is too long on macOS."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=_SHORT_TEMP_ROOT))


def _short_token(prefix: str) -> str:
    token = prefix + uuid.uuid4().hex
    return token[:12]


def _cli_frame_env(base: dict[str, str]) -> dict[str, str]:
    env = base.copy()
    for key in (
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ",
        "ZELLIJ_PANE_ID",
        "ZELLIJ_SESSION_NAME",
        "VC_FRAME_CONFIG_FILE",
        "ZELLIJ_CONFIG_FILE",
        "ZELLIJ_CONFIG_DIR",
    ):
        env.pop(key, None)
    env["VC_FRAME_SERVER_FOREGROUND"] = "1"
    return env


def _pid_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_start_identity(pid: int) -> str | None:
    """Kernel start time plus command for one PID. A recycled PID differs."""
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "pid=,lstart=,command="],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = " ".join(result.stdout.split())
    return value or None


def _pid_file_start_identity(path: Path) -> str | None:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return _process_start_identity(pid)


def _wait_until(probe, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(0.2)
    return last


def _count_list_clients(text: str) -> int:
    header_seen = False
    rows = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not header_seen:
            if line.startswith("CLIENT_ID"):
                header_seen = True
            continue
        rows += 1
    return rows


@pytest.mark.skipif(_ADMITTED_FRAME is None, reason="admitted vc-frame binary missing")
def test_admitted_frame_project_workspace_is_a_real_verb_not_a_stub() -> None:
    """Help / unknown / missing-guest / self-project against the fd14 binary.

    Isolated exclusive tempdir — never a PID-modulo path that is recursively
    deleted if it already exists. No Founder session mutation.
    """
    assert _ADMITTED_FRAME is not None
    bin_path = _ADMITTED_FRAME

    def helptext(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(bin_path), *args, "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )

    project_help = helptext("project-workspace")
    assert project_help.returncode == 0, project_help.stderr
    combined = project_help.stdout + project_help.stderr
    assert "project-workspace" in combined
    assert "--session" in combined
    assert "--tab" in combined
    top = helptext()
    assert top.returncode == 0, top.stderr
    assert "--guest-workspace" in top.stdout + top.stderr

    bogus = subprocess.run(
        [str(bin_path), "definitely-not-a-frame-verb"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
    )
    assert bogus.returncode != 0, "unknown verb must not be accepted"

    sandbox = _exclusive_sandbox("vcs-v-")
    sock = sandbox / "sock"
    home = sandbox / "home"
    sock.mkdir()
    home.mkdir()
    host = _short_token("h")
    guest = _short_token("g")
    env = os.environ.copy()
    for key in IDENTITY_ENV:
        env.pop(key, None)
    env.update(
        {
            "HOME": str(home),
            "VC_FRAME_SOCKET_DIR": str(sock),
            "ZELLIJ_SOCKET_DIR": str(sock),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
            "VC_FRAME_SERVER_FOREGROUND": "1",
        }
    )
    cli_env = _cli_frame_env(env)

    def frame(*args: str, timeout: int = 40) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(bin_path), *args],
            check=False,
            env=cli_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    owned = []
    try:
        missing_session = frame("project-workspace", guest)
        assert missing_session.returncode != 0
        assert "requires --session" in (
            missing_session.stdout + missing_session.stderr
        )

        missing_guest = frame("--session", host, "project-workspace", guest)
        assert missing_guest.returncode != 0
        receipts = [
            json.loads(line)
            for line in missing_guest.stdout.splitlines()
            if line.startswith("{")
        ]
        assert not [
            r
            for r in receipts
            if r.get("status") == "Handled" and r.get("pane_id") not in (None, "")
        ], missing_guest.stdout

        owned.append(guest)
        created = frame(
            "--guest-workspace",
            "--layout",
            "vibecrafted-guest",
            "attach",
            "--create-background",
            guest,
        )
        assert created.returncode == 0, created.stderr
        self_project = frame("--session", guest, "project-workspace", guest)
        assert self_project.returncode != 0
        assert "cannot project into itself" in (
            self_project.stdout + self_project.stderr
        )
    finally:
        for name in owned:
            frame("kill-session", name)
            frame("delete-session", name, "--force")
        shutil.rmtree(sandbox, ignore_errors=True)


@pytest.mark.skipif(_ADMITTED_FRAME is None, reason="admitted vc-frame binary missing")
def test_admitted_frame_inside_host_vc_start_projects_guest_on_stable_canvas() -> None:
    """W2 acceptance: public vc-start inside a PTY-backed admitted host.

        Creates an isolated exclusive short sandbox, a vibecrafted-host session,
        one attached PTY client, and a prior pane whose process records PID plus
        start identity and then reads commands (`exec sh -s`). Invokes public
        `vc-start --repo` with attached-host markers. Guest/alias/other dirs are
        committed repositories. Success requires a compact Handled receipt whose
        pane_id exists on the host, semantic plugin_url chrome (not title==URL),
        the same prior pane id / PID / start identity, and a harmless command
        completed in that same prior pane via public `action write-chars
        --pane-id` — not sleep-alive or guest dump-screen alone. Offered
        `operator` layout alias must also project on the same canvas; `--layout`
        stays usage-refused. Then client-ambiguity refuses before another
        create. Cleanup identities are registered before ops that can throw.
    """
    assert _ADMITTED_FRAME is not None
    bin_path = _ADMITTED_FRAME
    sandbox = _exclusive_sandbox("vcs-a-")
    ptys: list[tuple[subprocess.Popen[str], Path]] = []
    owned: list[str] = []
    cli_env: dict[str, str] | None = None
    try:
        home = sandbox / "home"
        sock = sandbox / "sock"
        home.mkdir()
        sock.mkdir()
        (home / "config" / "vc-frame").mkdir(parents=True)
        (home / "data").mkdir()
        host = _short_token("h")
        guest = _short_token("g")
        alias = _short_token("a")
        other = _short_token("o")
        guest_repo = sandbox / guest
        alias_repo = sandbox / alias
        other_repo = sandbox / other
        _seed_committed_git(guest_repo, home=home)
        _seed_committed_git(alias_repo, home=home)
        _seed_committed_git(other_repo, home=home)
        prior_pid = home / "prior.pid"
        prior_lstart = home / "prior.lstart"
        prior_done = home / "prior.done"
        prior_token = "prior-ok-" + uuid.uuid4().hex[:12]
        owner = _write(sandbox / "owner-cli", OWNER_CLI)
        env = os.environ.copy()
        for key in IDENTITY_ENV:
            env.pop(key, None)
        env.update(
            {
                "HOME": str(home),
                "VIBECRAFTED_HOME": str(home / "vibecrafted"),
                "XDG_CONFIG_HOME": str(home / "config"),
                "XDG_DATA_HOME": str(home / "data"),
                "XDG_CACHE_HOME": str(home / "cache"),
                "VC_FRAME_SOCKET_DIR": str(sock),
                "ZELLIJ_SOCKET_DIR": str(sock),
                "VC_FRAME_CONFIG_DIR": str(home / "config" / "vc-frame"),
                "VC_FRAME_SERVER_FOREGROUND": "1",
                "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
                "VIBECRAFTED_VC_FRAME_BIN": str(bin_path),
                "VIBECRAFTED_PRODUCT_CORE_CLI": str(owner),
                "OWNER_CLI_LOG": str(sandbox / "owner.log"),
            }
        )
        cli_env = _cli_frame_env(env)
        script_path = home / "pty_attach.py"
        script_path.write_text(_PTY_ATTACH_PY, encoding="utf-8")
        owned.append(host)

        def frame(*args: str, timeout: int = 40) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(bin_path), *args],
                check=False,
                env=cli_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        def listing() -> str:
            return frame("list-sessions", "--no-formatting").stdout

        def panes() -> str:
            return frame(
                "--session", host, "action", "list-panes", "--json", "--command"
            ).stdout

        def pane_rows() -> list:
            payload = _list_panes_payload(panes())
            return payload if isinstance(payload, list) else []

        def attach(token: str) -> subprocess.Popen[str]:
            attached = home / f"pty-attached-{token}"
            release = home / f"pty-release-{token}"
            attached.unlink(missing_ok=True)
            release.unlink(missing_ok=True)
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(script_path),
                    str(bin_path),
                    host,
                    str(attached),
                    str(release),
                ],
                env=cli_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            ptys.append((proc, release))
            assert _wait_until(lambda: attached.is_file() and proc.poll() is None, 30), attached
            return proc

        def public_start(repo: Path, invocation: str) -> subprocess.CompletedProcess[str]:
            start_env = env.copy()
            start_env.update(
                {
                    "VC_FRAME": "1",
                    "VC_FRAME_PANE_ID": "1",
                    "VC_FRAME_SESSION_NAME": host,
                }
            )
            start_env.pop("VC_FRAME_CONFIG_FILE", None)
            script = "\n".join(
                [
                    f'source "{SHELL_SH}"',
                    f'_vetcoders_vc_frame_loaded_root="{REPO_ROOT}"',
                    invocation,
                    'printf "RC=[%s]\\n" "$?"',
                ]
            )
            return subprocess.run(
                ["bash", "--noprofile", "--norc", "-c", script],
                check=False,
                cwd=repo,
                env=start_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=180,
            )

        created = frame(
            "--layout",
            "vibecrafted-host",
            "attach",
            "--create-background",
            host,
        )
        assert created.returncode == 0, created.stderr
        assert _wait_until(lambda: host in listing() and "(EXITED" not in listing(), 20), listing()

        attach("host")

        def unique_client_listing(expected: int):
            text = frame("--session", host, "action", "list-clients").stdout
            return text if _count_list_clients(text) == expected else ""

        clients = _wait_until(lambda: unique_client_listing(1), 20)
        assert clients and _count_list_clients(clients) == 1, (
            frame("--session", host, "action", "list-clients").stdout
        )

        def host_chrome_rows():
            return _host_chrome_ready(pane_rows())

        chrome = _wait_until(host_chrome_rows, 25)
        assert chrome, panes()
        chrome_geometry = _assert_host_chrome(chrome)

        pane = frame(
            "--session",
            host,
            "action",
            "new-pane",
            "--",
            "sh",
            "-c",
            (
                f"echo $$ > {prior_pid}; "
                f"ps -p $$ -o lstart= > {prior_lstart}; "
                "exec sh -s"
            ),
        )
        assert pane.returncode == 0, pane.stderr
        assert _wait_until(
            lambda: _pid_alive(prior_pid) and prior_lstart.is_file(),
            15,
        ), (prior_pid, prior_lstart)
        prior_pid_value = prior_pid.read_text(encoding="utf-8").strip()
        prior_lstart_value = " ".join(
            prior_lstart.read_text(encoding="utf-8").split()
        )
        prior_identity = _pid_file_start_identity(prior_pid)
        assert prior_pid_value.isdigit() and prior_identity, prior_pid_value
        assert prior_lstart_value and prior_lstart_value in prior_identity, (
            prior_lstart_value,
            prior_identity,
        )

        def find_prior_pane():
            marker = str(prior_pid)
            for row in pane_rows():
                if not isinstance(row, dict):
                    continue
                command = str(row.get("terminal_command") or "")
                if marker in command or "sh -s" in command:
                    return row
            return None

        prior_pane = _wait_until(find_prior_pane, 15)
        assert isinstance(prior_pane, dict) and prior_pane.get("id") not in (
            None,
            "",
        ), panes()
        prior_pane_id = prior_pane["id"]
        prior_command = str(prior_pane.get("terminal_command") or "")

        refused_layout = public_start(
            guest_repo,
            f"vc-start --layout vibecrafted-guest --repo {shlex.quote(str(guest_repo))}",
        )
        assert "RC=[2]" in refused_layout.stdout, (
            refused_layout.stdout + refused_layout.stderr
        )
        assert guest not in listing(), listing()

        owned.append(guest)
        started = public_start(
            guest_repo,
            f"vc-start --repo {shlex.quote(str(guest_repo))}",
        )
        combined = started.stdout + started.stderr
        assert "RC=[0]" in started.stdout, combined
        assert f"created workspace {guest}" in combined, combined
        assert f"projected workspace {guest}" in combined, combined
        assert "switch-session" not in combined
        assert host in listing() and "(EXITED" not in listing()
        assert guest in listing()
        receipts = _workspace_projection_receipts(started.stdout)
        assert len(receipts) == 1, started.stdout
        receipt = receipts[0]
        assert receipt.get("status") == "Handled", receipt
        assert receipt.get("guest") == guest, receipt
        assert receipt.get("pane_id") not in (None, ""), receipt
        assert str(receipt.get("request_id") or "").strip(), receipt
        after_rows = pane_rows()
        _assert_host_chrome(after_rows, previous_geometry=chrome_geometry)
        guest_pane = _pane_by_id(after_rows, receipt["pane_id"])
        assert guest_pane is not None, (receipt, after_rows)
        surviving = _pane_by_id(after_rows, prior_pane_id)
        assert surviving is not None, (prior_pane_id, after_rows)
        assert str(surviving.get("terminal_command") or "") == prior_command or (
            "sh -s" in str(surviving.get("terminal_command") or "")
        ), (prior_command, surviving)
        assert prior_pid.read_text(encoding="utf-8").strip() == prior_pid_value
        assert _pid_alive(prior_pid), prior_pid_value
        after_identity = _pid_file_start_identity(prior_pid)
        assert after_identity == prior_identity, (prior_identity, after_identity)

        written = frame(
            "--session",
            host,
            "action",
            "write-chars",
            "--pane-id",
            f"terminal_{prior_pane_id}",
            f"echo {prior_token} > {prior_done}",
        )
        submitted = frame(
            "--session",
            host,
            "action",
            "send-keys",
            "--pane-id",
            f"terminal_{prior_pane_id}",
            "Enter",
        )
        assert written.returncode == 0, written.stdout + written.stderr
        assert submitted.returncode == 0, submitted.stdout + submitted.stderr
        assert _wait_until(
            lambda: (
                prior_done.is_file()
                and prior_token in prior_done.read_text(encoding="utf-8")
            ),
            15,
        ), (prior_done, written.stdout + written.stderr + submitted.stdout + submitted.stderr)
        assert prior_pid.read_text(encoding="utf-8").strip() == prior_pid_value
        assert _pid_file_start_identity(prior_pid) == prior_identity
        prior_dump = frame(
            "--session",
            host,
            "action",
            "dump-screen",
            "--pane-id",
            f"terminal_{prior_pane_id}",
        )
        assert prior_dump.returncode == 0, prior_dump.stdout + prior_dump.stderr
        assert "not found" not in (prior_dump.stdout + prior_dump.stderr).lower()
        dumped = frame(
            "--session",
            host,
            "action",
            "dump-screen",
            "--pane-id",
            f"terminal_{receipt['pane_id']}",
        )
        dump = dumped.stdout + dumped.stderr
        assert dumped.returncode == 0, dump
        assert "not found" not in dump.lower(), dump

        owned.append(alias)
        alias_started = public_start(
            alias_repo,
            f"vc-start operator --repo {shlex.quote(str(alias_repo))}",
        )
        alias_combined = alias_started.stdout + alias_started.stderr
        assert "RC=[0]" in alias_started.stdout, alias_combined
        assert f"created workspace {alias}" in alias_combined, alias_combined
        assert f"projected workspace {alias}" in alias_combined, alias_combined
        assert "switch-session" not in alias_combined
        assert host in listing() and "(EXITED" not in listing()
        assert alias in listing()
        alias_receipts = _workspace_projection_receipts(alias_started.stdout)
        assert len(alias_receipts) == 1, alias_started.stdout
        assert alias_receipts[0].get("status") == "Handled", alias_receipts[0]
        assert alias_receipts[0].get("guest") == alias, alias_receipts[0]
        assert alias_receipts[0].get("pane_id") not in (None, ""), alias_receipts[0]
        alias_rows = pane_rows()
        _assert_host_chrome(alias_rows, previous_geometry=chrome_geometry)
        assert _pane_by_id(alias_rows, receipt["pane_id"]) is not None
        assert _pane_by_id(alias_rows, alias_receipts[0]["pane_id"]) is not None
        assert _pane_by_id(alias_rows, prior_pane_id) is not None
        assert prior_pid.read_text(encoding="utf-8").strip() == prior_pid_value
        assert _pid_file_start_identity(prior_pid) == prior_identity
        assert prior_token in prior_done.read_text(encoding="utf-8")

        attach("host-second")
        two = _wait_until(lambda: unique_client_listing(2), 20)
        assert two and _count_list_clients(two) == 2, (
            frame("--session", host, "action", "list-clients").stdout
        )
        owned.append(other)
        ambiguous = public_start(
            other_repo,
            f"vc-start --repo {shlex.quote(str(other_repo))}",
        )
        assert "RC=[4]" in ambiguous.stdout, ambiguous.stdout + ambiguous.stderr
        assert "exactly one attached client" in ambiguous.stderr
        assert other not in listing(), listing()
        assert prior_pid.read_text(encoding="utf-8").strip() == prior_pid_value
        assert _pid_alive(prior_pid)
        assert _pid_file_start_identity(prior_pid) == prior_identity
        assert prior_token in prior_done.read_text(encoding="utf-8")
        _assert_host_chrome(pane_rows(), previous_geometry=chrome_geometry)
        assert _pane_by_id(pane_rows(), prior_pane_id) is not None
    finally:
        for proc, release in ptys:
            try:
                release.write_text("1", encoding="utf-8")
            except OSError:
                pass
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        leftover = ""
        if cli_env is not None:
            def _cleanup_frame(*args: str, timeout: int = 40) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [str(bin_path), *args],
                    check=False,
                    env=cli_env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )

            for name in owned:
                _cleanup_frame("kill-session", name)
                _cleanup_frame("delete-session", name, "--force")
            leftover = _cleanup_frame("list-sessions", "--no-formatting").stdout
        shutil.rmtree(sandbox, ignore_errors=True)
        for name in owned:
            assert name not in leftover, leftover
