"""`vc-start` enters the repository workspace from every entrypoint.

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
* **inventory first** -- the engine's own `list-sessions` is read before any
  window. An unreadable inventory refuses with exit 4. A known WES binding
  for another repository keeps the name collision as an explicit choice.
* **exclusive create** -- adapter lock plus inventory, then Frame create;
  two concurrent starts yield exactly one workspace and both enter it.
  An EXITED record is restored in place without replacing its layout.
* **enter** -- foreign terminals hand off to the selected VC Terminal. An admitted
  product-origin fixture attaches directly; admitted Frame re-entry can
  create an ordinary full-chrome project; CLI switching requires a snapshot
  containing exactly one source client because pane markers do not address a
  client. An ambiguous source refuses safely and leaves the project available
  to attach.
  Targets accept any number of clients. Session 00 owns Operator chrome;
  project sessions retain their own tabs/status. Native keyboard/rail events
  carry client identity and can switch with multiple source clients. The shell
  snapshot is not an atomic guarantee against a concurrent client attachment.

The catalogue and Frame process are stubbed; older direct-route tests explicitly
select a product-admitted origin. Native ancestry and complete foreign handoff
are exercised separately with real private PTYs and sentinel processes. The stub keeps exclusive
on-disk session creation and rejects unknown verbs, including retired nested
projection. Native acceptance separately exercises the admitted engine in an
isolated sandbox; fixture success does not prove keyboard or client focus.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from _runtime_pack_fixture import (
    bind_private_terminal_wrapper,
    private_child_command,
    private_native_copy,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CORE_DIR = REPO_ROOT / "vibecrafted-core"
RUNTIME = SOURCE_CORE_DIR / "vibecrafted_core" / "runtime"
SHELL_SH = RUNTIME / "shell" / "vetcoders.sh"
DASHBOARD_SH = RUNTIME / "shell" / "lib" / "dashboard.sh"
DISPATCH_SH = RUNTIME / "shell" / "lib" / "dispatch.sh"
DECK = SOURCE_CORE_DIR / "vibecrafted_core" / "deck" / "vibecrafted"
PRIMARY_SHELL = REPO_ROOT / "config" / "alacritty" / "launch-primary-shell.zsh"

EXIT_USAGE = 2
EXIT_EXISTS = 3
HOST_SESSION = "vc-host"
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
    "VIBECRAFTED_START_LAUNCH_PINNED",
    "VIBECRAFTED_START_BASELINE_SHA",
    "VIBECRAFTED_START_PARENT_ROOT",
    "VIBECRAFTED_TERMINAL_ENTRY",
    "VIBECRAFTED_TERMINAL_ENTRY_OWNER",
    "_VETCODERS_OWNED_TERMINAL_ENTRY",
    "_VETCODERS_OWNED_TERMINAL_ENTRY_SHELL",
    "_VETCODERS_OWNED_TERMINAL_ENTRY_OWNER",
    "VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME",
    "VIBECRAFTED_PRODUCT_ENTRY",
    "VIBECRAFTED_PRODUCT_ENTRY_PROBE",
    "VIBECRAFTED_ROOT",
    "VIBECRAFTED_RUNTIME_ROOT",
    "VIBECRAFTED_RUNTIME_BIN",
    "VIBECRAFTED_RUNTIME_HOME",
    "VIBECRAFTED_PYTHON",
    "VIBECRAFTED_CORE_DIR",
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


def _strip_identity_env(env: dict[str, str]) -> dict[str, str]:
    """Drop ambient worker/runtime identity and pin the tested source core.

    A launcher run exports VIBECRAFTED_CORE_DIR at the installed generation.
    Fixtures that source this checkout's shell must not inherit that path.
    """
    for key in IDENTITY_ENV:
        env.pop(key, None)
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    env["VIBECRAFTED_CORE_DIR"] = str(SOURCE_CORE_DIR)
    return env


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
#   * `--session S --client-id N action switch-session NAME` needs both live
#     and validates the requested source frontend at action admission;
#   * `--session S action list-clients` prints a header and one row when S is
#     listed in VC_FRAME_CLIENTS;
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
client_state_file = os.environ.get("VC_FRAME_CLIENT_STATE", "")
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
        "python_pin": os.environ.get("VIBECRAFTED_PYTHON"),
        "core_dir": os.environ.get("VIBECRAFTED_CORE_DIR"),
        "workspace_identity": {key: os.environ.get(key) for key in (
            "VIBECRAFTED_WORKSPACE_ID", "VIBECRAFTED_SESSION_ID",
            "VIBECRAFTED_WORKSPACE_INSTANCE_ID", "VIBECRAFTED_BUILD_ID",
            "VIBECRAFTED_OPERATOR_SESSION", "VIBECRAFTED_WORKSPACE_ROOT",
        )},
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


def client_state_of(name):
    if client_state_file and os.path.exists(client_state_file):
        state = json.loads(open(client_state_file).read())
        if name in state:
            return state[name]
    ids = list(range(1, len(clients_of(name)) + 1))
    return {"client_ids": ids, "last_active_client_id": ids[-1] if ids else None}


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
client_id = None
layout = None
tab = None
rest = list(argv)
i = 0
while i < len(rest):
    token = rest[i]
    if token == "--session" and i + 1 < len(rest):
        session = rest[i + 1]
        i += 2
        continue
    if token == "--client-id" and i + 1 < len(rest):
        client_id = int(rest[i + 1])
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
    # Model the required Frame-owned host contract, not a product KDL copy.
    layout_text = "layout { frame_host true; workspace_surface true; }" if layout is None else ""
    if layout and os.path.isfile(layout):
        with open(layout, encoding="utf-8") as handle:
            layout_text = handle.read()
    os.write(fd, (layout_text or layout or "").encode())
    os.close(fd)
    record({
        "created": name,
        "layout": layout,
        "layout_bytes": len(layout_text) if layout_text else 0,
    })
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
    # Track attached clients independently from the session content.
    if clients_file:
        with open(clients_file, "a") as handle:
            handle.write(target + "\\n")
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
        if os.environ.get("VC_FRAME_SWITCH_ERROR"):
            sys.stderr.write(os.environ["VC_FRAME_SWITCH_ERROR"] + "\\n")
            sys.exit(2)
        state = client_state_of(target)
        selected = client_id if client_id is not None else state["last_active_client_id"]
        if selected not in state["client_ids"]:
            sys.stderr.write("vc-frame: requested client is not attached to source session\\n")
            sys.exit(2)
        record({"switched": [target, wanted], "switched_client_id": selected})
        sys.exit(0)
    if verb == "list-clients":
        if os.environ.get("VC_FRAME_CLIENTS_ERROR"):
            sys.stderr.write(os.environ["VC_FRAME_CLIENTS_ERROR"] + "\\n")
            sys.exit(1)
        if os.environ.get("VC_FRAME_CLIENTS_MALFORMED"):
            print(os.environ["VC_FRAME_CLIENTS_MALFORMED"])
            sys.exit(0)
        print("CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND")
        for attached_id in client_state_of(target)["client_ids"]:
            print("%s         terminal_1     zsh " % attached_id)
        # Deterministic concurrent attach/input between the inventory and action.
        replacement = os.environ.get("VC_FRAME_CLIENT_STATE_AFTER_LIST")
        if replacement:
            with open(client_state_file, "w") as handle:
                handle.write(replacement)
        sys.exit(0)
    if verb == "dump-layout":
        with open(os.path.join(table, "live", target), encoding="utf-8") as handle:
            print(handle.read(), end="")
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
    sys.stderr.write("error: Found argument '%s' which wasn't expected\\n" % verb)
    sys.exit(2)

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
if [[ "$1 $2" == "workspace session-owner" ]]; then
  shift 2
  name="" socket=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --runtime-session-id) name="$2"; shift 2 ;;
      --socket-dir) socket="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  owner=""
  if [[ -f "${log}.owners" ]]; then
    while IFS=$'\t' read -r known_name known_socket known_root; do
      [[ "$known_name" == "$name" && "$known_socket" == "$socket" ]] || continue
      [[ -z "$owner" || "$owner" == "$known_root" ]] || exit 2
      owner="$known_root"
    done < "${log}.owners"
  fi
  [[ -z "$owner" ]] || printf '%s\\n' "$owner"
  exit 0
fi
if [[ "$1 $2" == "workspace session-attach" ]]; then
  shift 2
  name="" socket=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --runtime-session-id) name="$2"; shift 2 ;;
      --socket-dir) socket="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  if [[ -n "$name" && -n "$socket" && -n "${VIBECRAFTED_WORKSPACE_ROOT:-}" ]]; then
    printf '%s\\t%s\\t%s\\n' "$name" "$socket" "$VIBECRAFTED_WORKSPACE_ROOT" >> "${log}.owners"
  fi
  exit 0
fi
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
        legacy: tuple[str, ...] = (),
        guests: tuple[str, ...] = (),
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
        for alias in ("dashboard", "marbles", "research", "workflow"):
            _write(self.config_dir / "layouts" / f"{alias}.kdl", "layout {\n}\n")
        self.terminal_log = tmp_path / "terminal-launches.jsonl"
        self.generation = self._generation(tmp_path)
        self.owner_log = tmp_path / "owner-calls.log"
        self.owner = _write(tmp_path / "owner-cli", OWNER_CLI)
        self.frame_log = tmp_path / "frame.log"
        self.table = tmp_path / "frame-table"
        (self.table / "live").mkdir(parents=True)
        (self.table / "dead").mkdir(parents=True)
        for name in live:
            if name in legacy:
                # Serialized pre-host/guest operator: four actual tabs plus
                # eight swap templates, all carrying the same host identity.
                tab = 'tab { pane { plugin location="frame-host" { frame_host "true"; } } }\n'
                body = (
                    "layout {\n"
                    + tab * 4
                    + "swap_tiled_layout {\n"
                    + tab * 8
                    + "}\n}\n"
                )
            elif name in guests:
                body = "layout { pane; }\n"
            else:
                # Explicit Operator role is layout metadata, not its display name.
                body = "layout { frame_host true; pane; }\n"
            (self.table / "live" / name).write_text(body, encoding="utf-8")
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
        _strip_identity_env(env)
        env["HOME"] = str(self.home)
        env["VIBECRAFTED_HOME"] = str(self.home / ".vibecrafted")
        env["XDG_CONFIG_HOME"] = str(self.home / ".config")
        env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        sock = self.tmp_path / "sock"
        sock.mkdir(exist_ok=True)
        env["VC_FRAME_SOCKET_DIR"] = str(sock)
        env["ZELLIJ_SOCKET_DIR"] = str(sock)
        env["VIBECRAFTED_PRODUCT_CORE_CLI"] = str(self.owner)
        env["OWNER_CLI_LOG"] = str(self.owner_log)
        env["VC_FRAME_LOG"] = str(self.frame_log)
        env["VC_FRAME_TABLE"] = str(self.table)
        env["VC_FRAME_CLIENTS"] = str(self.clients_file)
        env["VC_FRAME_CLIENT_STATE"] = str(self.tmp_path / "client-state.json")
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
        return [
            line
            for line in self.owner_log.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def live(self) -> list[str]:
        return sorted(p.name for p in (self.table / "live").iterdir())

    def workspaces(self) -> list[str]:
        """Live sessions minus the singleton Frame host."""
        return [name for name in self.live() if name != HOST_SESSION]

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


def test_source_start_pins_python_for_launchpad_and_agents(tmp_path: Path) -> None:
    scene = Scene(tmp_path, project="source-project", git=True)
    # Bind the private engine double to the test interpreter; its env-python
    # shebang must not select a different host framework under the exec fence.
    for relative in ("bin/vc-frame", "libexec/vc-frame"):
        frame = scene.generation / relative
        body = frame.read_text()
        frame.write_text(f"#!{sys.executable}\n" + body.split("\n", 1)[1])
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        owned_terminal=True,
        tty=True,
        extra_env={
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
        },
        fence_children=True,
        allowed_children=(
            scene.generation / "bin/vc-frame",
            SOURCE_CORE_DIR / "vibecrafted_core/config/vc-frame/pane-python",
        ),
    )
    assert _rc(result) == 0, result.stderr
    created = _creates(scene.calls())
    assert created
    for call in created:
        pin = call.get("python_pin")
        assert pin and Path(pin).is_absolute() and os.access(pin, os.X_OK)
        launcher = _write(
            tmp_path / "pane-launcher.py",
            f"#!{sys.executable}\n"
            "import json, sys, vibecrafted_core\n"
            "print(json.dumps({'argv': sys.argv[1:], 'core': vibecrafted_core.__file__}))\n",
        )
        command = [
            "bash",
            str(SOURCE_CORE_DIR / "vibecrafted_core/config/vc-frame/pane-python"),
            str(launcher),
            "launcher",
        ]
        command = private_child_command(
            command, allowed=(Path(pin), launcher), fixture_root=tmp_path
        )
        pane = subprocess.run(
            command,
            env=scene.env(
                {"VIBECRAFTED_PYTHON": pin, "VIBECRAFTED_CORE_DIR": call["core_dir"]}
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert pane.returncode == 0, pane.stderr
        payload = json.loads(pane.stdout.strip())
        assert payload["argv"] == ["launcher"]
        assert Path(payload["core"]).resolve().is_relative_to(SOURCE_CORE_DIR)


@pytest.mark.parametrize(
    "state",
    [
        "live",
        "dead",
        "missing",
        "history",
        "foreign-history",
        "ambiguous",
        "mismatch",
        "contradictory",
    ],
)
@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("origin", ["owned", "foreign"])
def test_wes_public_start_build_transition_preserves_exact_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, shell: str, origin: str
) -> None:
    """Real catalog + public parser, with only the native engine replaced.

    The entry receipt models the real terminal child, not an origin-policy mock.
    A private PTY drives the actual attach boundary without a GUI/provider.
    """
    from vibecrafted_core import workspace_catalog as wc

    scene = Scene(
        tmp_path,
        project="selected",
        git=True,
        live=["selected"] if state != "dead" and state != "missing" else (),
        dead=["selected"] if state == "dead" else (),
    )
    shutil.copytree(
        SOURCE_CORE_DIR / "vibecrafted_core/config/vc-frame",
        scene.generation / "vibecrafted-core/vibecrafted_core/config/vc-frame",
    )
    _seed_committed_git(scene.generation, home=scene.home)
    env = scene.env(
        {
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VIBECRAFTED_TERMINAL_ENTRY_OWNER": str(
                scene.generation / "bin/vibecrafted"
            ),
        }
    )
    if origin == "foreign":
        env.pop("VIBECRAFTED_TERMINAL_ENTRY")
        env.pop("VIBECRAFTED_TERMINAL_ENTRY_OWNER")
    native_receipts = tmp_path / "native-terminal.jsonl"
    native_done = tmp_path / "native-done.jsonl"
    _write(
        scene.generation / "bin/vc-terminal",
        (REPO_ROOT / "scripts/vc-terminal-product-entry.sh").read_text(),
    )
    _write(
        scene.home / ".config/vibecrafted/vc-terminal/vc-terminal.toml", "[terminal]\n"
    )
    _write(
        scene.home / ".config/vibecrafted/vc-terminal/launch-primary-shell.zsh",
        PRIMARY_SHELL.read_text().replace("exec /bin/zsh -d -l", "exit 0"),
    )
    _write(
        scene.generation / "libexec/vc-terminal",
        f"#!{sys.executable}\nimport json,os,pty,sys\n"
        "args=sys.argv[1:]; boundary=args.index('-e')\n"
        "os.chdir(args[args.index('--working-directory')+1])\n"
        f"with open({str(native_receipts)!r}, 'a') as f:\n"
        " f.write(json.dumps({'cwd':os.getcwd(),'argv':args,'identity':{key:os.environ.get(key) for key in "
        f"{(wc.ENV_WORKSPACE_ID, wc.ENV_VIBECRAFTED_SESSION_ID, wc.ENV_WORKSPACE_INSTANCE_ID, wc.ENV_BUILD_ID)!r}"
        + "}})+'\\n')\n"
        "rc=pty.spawn(['/bin/bash',*args[boundary+1:]])\n"
        f"with open({str(native_done)!r}, 'a') as f: f.write(json.dumps({{'status':rc}})+'\\n')\n"
        "sys.exit(os.waitstatus_to_exitcode(rc))\n",
    )
    selected_host = bind_private_terminal_wrapper(
        scene.generation / "bin/vc-terminal", scene.generation / "libexec/vc-terminal"
    )
    _write(
        scene.generation / "bin/vc-start",
        "#!/bin/bash\n"
        f"export VIBECRAFTED_CORE_DIR={shlex.quote(str(SOURCE_CORE_DIR))}\n"
        f"source {shlex.quote(str(SHELL_SH))}\n"
        f"_vetcoders_vc_frame_loaded_root={shlex.quote(str(scene.generation))}\n"
        'vc-start "$@"\n',
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    first = wc.resolve_run_workspace_identity(root=scene.root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=first.workspace_id,
        vibecrafted_session_id=first.vibecrafted_session_id,
        workspace_instance_id=first.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="selected",
        state="live",
        socket_dir=env["VC_FRAME_SOCKET_DIR"],
    )
    old_session = wc.read_workspace_session(first.vibecrafted_session_id).to_payload()
    old_instance = wc.instance_path(first.workspace_instance_id).read_bytes()
    (scene.root / "README").write_text("new revision\n")
    _seed_committed_git(scene.root, home=scene.home)
    expected = first
    if state == "history":
        expected = wc.resolve_run_workspace_identity(root=scene.root, env={})
        wc.record_runtime_session_attachment(
            workspace_id=expected.workspace_id,
            vibecrafted_session_id=expected.vibecrafted_session_id,
            workspace_instance_id=expected.workspace_instance_id,
            runtime="vc-frame",
            runtime_session_id="selected",
            state="live",
            socket_dir=env["VC_FRAME_SOCKET_DIR"],
        )
        old_instance = wc.instance_path(expected.workspace_instance_id).read_bytes()
        old_session = wc.read_workspace_session(
            expected.vibecrafted_session_id
        ).to_payload()
    if state == "foreign-history":
        foreign_root = tmp_path / "historical-foreign"
        foreign_root.mkdir()
        foreign = wc.resolve_run_workspace_identity(root=foreign_root, env={})
        wc.record_runtime_session_attachment(
            workspace_id=foreign.workspace_id,
            vibecrafted_session_id=foreign.vibecrafted_session_id,
            workspace_instance_id=foreign.workspace_instance_id,
            runtime="vc-frame",
            runtime_session_id="selected",
            state="missing",
            socket_dir=env["VC_FRAME_SOCKET_DIR"],
        )
    env.update(first.to_env())
    if state in ("ambiguous", "mismatch"):
        second = wc.materialize_instance(
            workspace_id=first.workspace_id,
            root=scene.root,
            vibecrafted_session_id=wc.new_uuid7(),
            build_id=first.build_id,
        )
        if state == "mismatch":
            env[wc.ENV_WORKSPACE_INSTANCE_ID] = second.workspace_instance_id
        else:
            wc.record_runtime_session_attachment(
                workspace_id=first.workspace_id,
                vibecrafted_session_id=second.vibecrafted_session_id,
                workspace_instance_id=second.workspace_instance_id,
                runtime="vc-frame",
                runtime_session_id="selected",
                state="live",
                socket_dir=env["VC_FRAME_SOCKET_DIR"],
            )
    elif state == "contradictory":
        path = wc.instance_path(first.workspace_instance_id)
        payload = json.loads(path.read_text())
        payload["vibecrafted_session_id"] = wc.new_uuid7()
        path.write_text(json.dumps(payload))
    preserved = {
        str(p): p.read_bytes()
        for folder in [wc.instances_dir(), wc.sessions_dir()]
        for p in folder.glob("*.json")
    }

    _write(
        scene.owner,
        f"#!{sys.executable}\nimport sys\n"
        f"sys.path.insert(0, {str(SOURCE_CORE_DIR)!r})\n"
        "if sys.argv[1:2] == ['workspace']:\n"
        "    from vibecrafted_core.workspace_catalog import workspace_cli_main\n"
        "    sys.exit(workspace_cli_main(sys.argv[2:]))\n"
        "sys.exit(0)\n",
    )
    script = _entry_script(scene, "vc-start --repo " + shlex.quote(str(scene.root)))
    run = subprocess.run(
        private_child_command(
            [
                sys.executable,
                "-c",
                "import pty,sys;sys.exit(pty.spawn("
                + repr(_shell_argv(shell, script))
                + "))",
            ],
            allowed=(
                scene.owner,
                selected_host,
                scene.generation
                / "vibecrafted-core/vibecrafted_core/config/vc-frame/pane-python",
                Path(shutil.which("git")),
                Path("/usr/libexec/PlistBuddy"),
            ),
            fixture_root=tmp_path,
        ),
        check=False,
        env=env,
        cwd=scene.root,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if state in ("ambiguous", "mismatch", "contradictory"):
        expected_rc = 2 if state == "mismatch" else 4
        assert f"RC=[{expected_rc}]" in run.stdout, run.stdout + run.stderr
        assert (
            "ownership" in run.stdout
            or "ambiguous live seats" in run.stdout
            or "could not verify the owner" in run.stdout
        )
        assert not native_receipts.exists()
        assert not _creates(scene.calls())
        assert not any(
            c.get("attached") or "switch-session" in c["argv"] for c in scene.calls()
        )
        assert {
            str(p): p.read_bytes()
            for folder in [wc.instances_dir(), wc.sessions_dir()]
            for p in folder.glob("*.json")
        } == preserved
        return
    assert "RC=[0]" in run.stdout, run.stdout + run.stderr
    if origin == "foreign":
        deadline = time.monotonic() + 35
        while not native_done.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert native_done.exists(), (
            scene.home / ".vibecrafted/logs/terminal-startup.log"
        ).read_text()
        assert json.loads(native_done.read_text().splitlines()[0])["status"] == 0
    entered = [c for c in scene.calls() if c.get("attached") == "selected"]
    assert len(entered) == 1
    identity = entered[0]["workspace_identity"]
    sid = identity[wc.ENV_VIBECRAFTED_SESSION_ID]
    iid = identity[wc.ENV_WORKSPACE_INSTANCE_ID]
    bound = wc.read_workspace_session(sid)
    assert bound.workspace_instance_id == iid
    if state in ("live", "history", "foreign-history"):
        assert sid == expected.vibecrafted_session_id
        assert iid == expected.workspace_instance_id
        assert identity[wc.ENV_BUILD_ID] == expected.build_id.rendered
        assert wc.instance_path(iid).read_bytes() == old_instance
        assert wc.read_workspace_session(sid).to_payload() == old_session
        assert not _creates(scene.calls())
        assert {
            str(p): p.read_bytes()
            for folder in [wc.instances_dir(), wc.sessions_dir()]
            for p in folder.glob("*.json")
        } == preserved
    else:
        assert sid != first.vibecrafted_session_id
        assert iid != first.workspace_instance_id
        old = wc.read_workspace_session(first.vibecrafted_session_id)
        assert old.workspace_instance_id == first.workspace_instance_id
        assert (
            old.attachments[0].attachment_id
            == old_session["attachments"][0]["attachment_id"]
        )
        assert old.attachments[0].state == state
        assert bound.attachments[0].state == "live"
        restored = [
            c
            for c in scene.calls()
            if c.get("created") == "selected" or c.get("resurrected") == "selected"
        ]
        assert len(restored) == 1
    snapshot = {
        str(p): p.read_bytes()
        for folder in [wc.instances_dir(), wc.sessions_dir()]
        for p in folder.glob("*.json")
    }
    # A second ordinary invocation still inherits the old shell identity.
    again = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pty,sys;sys.exit(pty.spawn("
            + repr(_shell_argv(shell, script))
            + "))",
        ],
        check=False,
        env=env,
        cwd=scene.root,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert "RC=[0]" in again.stdout, again.stdout + again.stderr
    if origin == "foreign":
        deadline = time.monotonic() + 35
        while (
            len(native_done.read_text().splitlines()) < 2
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        done = [json.loads(line) for line in native_done.read_text().splitlines()]
        assert done == [{"status": 0}, {"status": 0}]
        launches = [
            json.loads(line) for line in native_receipts.read_text().splitlines()
        ]
        assert len(launches) == 2
        assert all(
            item["cwd"] == str(scene.root)
            and item["identity"][wc.ENV_VIBECRAFTED_SESSION_ID] == sid
            and item["identity"][wc.ENV_WORKSPACE_INSTANCE_ID] == iid
            for item in launches
        )
    else:
        assert not native_receipts.exists()
    assert len([c for c in scene.calls() if c.get("attached") == "selected"]) == 2
    assert {
        str(p): p.read_bytes()
        for folder in [wc.instances_dir(), wc.sessions_dir()]
        for p in folder.glob("*.json")
    } == snapshot


def _shell_argv(shell: str, script: str) -> list[str]:
    if shell == "zsh":
        return ["zsh", "-f", "-c", script]
    return ["bash", "--noprofile", "--norc", "-c", script]


def _entry_script(
    scene: Scene,
    invocation: str,
    *,
    developer_root: bool = False,
    owned_terminal: bool = False,
) -> str:
    loaded_root = REPO_ROOT if developer_root else scene.generation
    return "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{loaded_root}"',
            # Direct-route tests select a product-admitted terminal explicitly.
            # Foreign-origin and physical-ancestry tests never use this stub.
            "_vetcoders_start_is_owned_terminal_child() { return 0; }"
            if owned_terminal
            else ":",
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
    _strip_identity_env(env)
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


def _create_lock_env(tmp_path: Path, *, sock: Path | None = None) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    _strip_identity_env(env)
    env.update(
        {
            "HOME": str(home),
            "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
            "VIBECRAFTED_START_CREATE_LOCK_TIMEOUT": "2",
        }
    )
    if sock is not None:
        env["VC_FRAME_SOCKET_DIR"] = str(sock)
    return env


def _create_lock_script(*body: str) -> str:
    return "\n".join((f'source "{SHELL_SH}"', *body))


def _run_create_lock(
    *body: str,
    env: dict[str, str],
    shell: str,
    timeout: int = 8,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _shell_argv(shell, _create_lock_script(*body)),
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _teardown_owned_create_lock_holder(holder: subprocess.Popen[str]) -> None:
    """Tear down only the start_new_session group this fixture created."""
    if holder.pid is None:
        return
    try:
        pgid = os.getpgid(holder.pid)
    except ProcessLookupError:
        try:
            holder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return
    pytest_pgid = os.getpgrp()
    owned = pgid == holder.pid and pgid not in (0, 1, pytest_pgid)
    if holder.poll() is None:
        try:
            if owned:
                os.killpg(pgid, signal.SIGKILL)
            else:
                holder.kill()
        except ProcessLookupError:
            pass
    try:
        holder.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            if owned:
                os.killpg(pgid, signal.SIGKILL)
            else:
                holder.kill()
        except ProcessLookupError:
            pass
        holder.wait(timeout=5)


def _spawn_owned_create_lock_holder(
    *,
    tmp_path: Path,
    env: dict[str, str],
    shell: str,
    session_name: str,
    ready: Path,
) -> subprocess.Popen[str]:
    err_path = tmp_path / f"create-lock-holder-{session_name}.err"
    with err_path.open("w", encoding="utf-8") as err_fh:
        holder = subprocess.Popen(
            _shell_argv(
                shell,
                _create_lock_script(
                    f"_vetcoders_start_acquire_create_lock {shlex.quote(session_name)} || exit 9",
                    f"printf held > {shlex.quote(str(ready))}",
                    "while true; do sleep 1; done",
                ),
            ),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err_fh,
            text=True,
            start_new_session=True,
        )
    assert holder.pid is not None
    try:
        pgid = os.getpgid(holder.pid)
    except ProcessLookupError:
        detail = err_path.read_text(encoding="utf-8") if err_path.exists() else ""
        raise AssertionError(detail or "create-lock holder died before pgid probe")
    assert pgid == holder.pid, "create-lock holder was not its own session/group leader"
    assert pgid not in (0, 1, os.getpgrp()), (
        "refusing to own a shared/system process group"
    )
    return holder


def _wait_owned_create_lock_ready(
    holder: subprocess.Popen[str],
    ready: Path,
    err_path: Path,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not ready.exists():
        if holder.poll() is not None:
            break
        time.sleep(0.05)
    if ready.exists():
        return
    if holder.poll() is None:
        raise AssertionError("create-lock holder stayed live without publishing ready")
    detail = err_path.read_text(encoding="utf-8") if err_path.exists() else ""
    raise AssertionError(detail or "create-lock holder died before ready")


def _read_owned_optional_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    if pid <= 1:
        return None
    return pid


def _reap_owned_pid(pid: int | None, *, timeout: float = 5.0) -> None:
    """SIGKILL one owned pid and wait until it is gone. No group sweep."""
    if pid is None or pid <= 1:
        return
    if pid in (os.getpid(), os.getppid()):
        return
    try:
        if os.getpgid(pid) == os.getpgrp():
            return
    except ProcessLookupError:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                return
        except ChildProcessError:
            pass
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _frame_env_daemon_python(child_pid_path: Path) -> str:
    """Synchronous CLI payload: parent forks a daemon and _exit(0)s.

    This is the production Frame shape: `_vetcoders_start_frame_env` waits
    for the immediate command, which daemonizes and leaves a living child.
    An outer `&` shell is not part of that contract and keeps a lock fd.
    """
    return (
        "import os, signal, time\n"
        "child = os.fork()\n"
        "if child:\n"
        "    os._exit(0)\n"
        "os.setsid()\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        f"open({str(child_pid_path)!r}, 'w').write(str(os.getpid()))\n"
        "fd = os.open(os.devnull, os.O_RDWR)\n"
        "for n in (0, 1, 2):\n"
        "    os.dup2(fd, n)\n"
        "if fd > 2:\n"
        "    os.close(fd)\n"
        "time.sleep(60)\n"
    )


def _run_owned_frame_env_lock_inherit_daemon(
    *,
    env: dict[str, str],
    shell: str,
    child_pid_path: Path,
    close_lock_fd: bool,
) -> subprocess.CompletedProcess[str]:
    """Acquire, run frame-env (fork+exit parent), release, return."""
    body: list[str] = []
    if not close_lock_fd:
        body.append("_vetcoders_start_close_create_lock_fd() { :; }")
    body.extend(
        [
            "_vetcoders_start_acquire_create_lock inherit || exit 9",
            (
                "_vetcoders_start_frame_env "
                + shlex.quote(sys.executable)
                + " -c "
                + shlex.quote(_frame_env_daemon_python(child_pid_path))
            ),
            "_vetcoders_start_release_create_lock",
        ]
    )
    return _run_create_lock(*body, env=env, shell=shell, timeout=8)


def _wait_owned_daemon_pid(child_pid_path: Path, *, timeout: float = 3.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        child = _read_owned_optional_pid(child_pid_path)
        if child is not None:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                time.sleep(0.05)
                continue
            return child
        time.sleep(0.05)
    raise AssertionError("inherit-lock daemon child did not publish a live pid")


def _create_lock_holder_pids(lock_file: Path) -> list[int]:
    lsof = next(
        (
            candidate
            for candidate in ("/usr/sbin/lsof", "/usr/bin/lsof", "/bin/lsof")
            if os.path.exists(candidate)
        ),
        None,
    )
    if lsof is None:
        pytest.skip("lsof is required to observe create-lock holders")
    result = subprocess.run(
        [lsof, "-n", "-P", "-t", str(lock_file)],
        check=False,
        capture_output=True,
        text=True,
    )
    pids: list[int] = []
    for raw in result.stdout.split():
        try:
            pid = int(raw)
        except ValueError:
            continue
        if pid > 1:
            pids.append(pid)
    return pids


def _teardown_owned_frame_env_lock_inherit(
    child_pid: int | None,
    child_pid_path: Path,
) -> None:
    """Reap only the daemon child published by this fixture."""
    resolved = child_pid
    if resolved is None:
        deadline = time.monotonic() + 1.0
        while resolved is None and time.monotonic() < deadline:
            resolved = _read_owned_optional_pid(child_pid_path)
            if resolved is None:
                time.sleep(0.05)
    if resolved is None:
        resolved = _read_owned_optional_pid(child_pid_path)
    _reap_owned_pid(resolved)


def _run(
    scene: Scene,
    invocation: str,
    *,
    shell: str = "bash",
    extra_env: dict[str, str] | None = None,
    tty: bool = False,
    cwd: Path | None = None,
    developer_root: bool = False,
    owned_terminal: bool = False,
    timeout: int = 120,
    fence_children: bool = False,
    allowed_children: tuple[Path, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run the REAL public entry (`vc-start` from dispatch.sh) in a fresh shell."""
    env = scene.env(extra_env)
    script = _entry_script(
        scene, invocation, developer_root=developer_root, owned_terminal=owned_terminal
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
    if fence_children:
        argv = private_child_command(
            argv, allowed=(scene.owner, *allowed_children), fixture_root=scene.tmp_path
        )
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


def _assert_host_first(scene: Scene, guest: str) -> None:
    """Session 00 uses embedded chrome; every project keeps its full layout."""
    created = [c for c in scene.calls() if c.get("created")]
    names = [c["created"] for c in created]
    assert HOST_SESSION in names and guest in names, names
    host = created[names.index(HOST_SESSION)]
    workspace = created[names.index(guest)]
    assert "--guest-workspace" not in host["argv"], host
    assert host["layout"] is None, host
    assert "--guest-workspace" not in workspace["argv"], workspace
    assert Path(workspace["layout"]).name == "operator.kdl", workspace
    assert names.index(HOST_SESSION) < names.index(guest), names


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


def _list_panes_payload(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
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
    assert scene.workspaces() == ["mlx-batch-runner"], (scene.live(), result.stderr)
    _assert_host_first(scene, "mlx-batch-runner")
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
    assert len(_creates(calls)) == 2, calls
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
    assert scene.workspaces() == ["plain-dir"]
    _assert_host_first(scene, "plain-dir")
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
    assert scene.workspaces() == ["Sentry-Selfhosted"]
    _assert_host_first(scene, "Sentry-Selfhosted")
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
    assert scene.workspaces() == ["real-project"], scene.live()
    _assert_host_first(scene, "real-project")


# --------------------------------------------------------------------------
# 2. explicit name, validation, reserved words, quoting
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_explicit_name_is_used_verbatim(tmp_path: Path, shell: str) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(scene, "vc-start review-b", shell=shell)
    assert _rc(result) == 0, result.stderr
    assert scene.workspaces() == ["review-b"]
    _assert_host_first(scene, "review-b")
    launch = scene.terminal_launches()[0]
    assert _hosted(launch)[2:] == ["review-b", "--repo", str(scene.root.resolve())]
    assert launch["created"] == "review-b"


def test_a_spaced_name_stays_one_argument_on_reentry(tmp_path: Path) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    first = _run(scene, "vc-start " + shlex.quote("two words"))
    assert _rc(first) == 0, first.stderr
    assert scene.workspaces() == ["two words"]
    _assert_host_first(scene, "two words")
    assert _hosted(scene.terminal_launches()[0])[2] == "two words"

    second = _run(scene, "vc-start " + shlex.quote("two words"))
    assert _rc(second) == 0, second.stdout + second.stderr
    assert _hosted(scene.terminal_launches(expect=2)[1])[2] == "two words"
    assert scene.workspaces() == ["two words"]


def test_a_name_with_a_single_quote_is_preserved_on_reentry(tmp_path: Path) -> None:
    name = "it's-mine"
    scene = Scene(tmp_path, project="p", live=(name,))
    result = _run(scene, "vc-start " + shlex.quote(name))
    assert _rc(result) == 0
    assert _hosted(scene.terminal_launches()[0])[2] == name


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
    assert scene.workspaces() == ["mlx-batch-runner"], scene.live()
    _assert_host_first(scene, "mlx-batch-runner")
    assert _hosted(scene.terminal_launches()[0])[2:] == [
        word,
        "--repo",
        str(scene.root.resolve()),
    ]


def test_plain_start_enters_a_live_workspace(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner", live=("mlx-batch-runner",))
    result = _run(scene, "vc-start")
    assert _rc(result) == 0
    assert _hosted(scene.terminal_launches()[0])[2:] == [
        "--repo",
        str(scene.root.resolve()),
    ]
    assert scene.workspaces() == ["mlx-batch-runner"]


@pytest.mark.parametrize(
    ("initial", "clients", "expected_action"),
    [
        ("live", True, "attach"),
        ("live", False, "attach"),
        ("dead", False, "resurrect"),
        ("missing", False, "create"),
    ],
)
def test_start_enters_workspace_for_each_inventory_state(
    tmp_path: Path, initial: str, clients: bool, expected_action: str
) -> None:
    name = "mlx-batch-runner"
    scene = Scene(
        tmp_path,
        project=name,
        live=(name,) if initial == "live" else (),
        dead=(name,) if initial == "dead" else (),
        clients=(name,) if clients else (),
    )
    result = _run(
        scene,
        "vc-start --repo " + shlex.quote(str(scene.root)),
        owned_terminal=True,
        tty=True,
        developer_root=True,
        extra_env={
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
        },
    )

    assert _rc(result) == 0, result.stdout + result.stderr
    calls = scene.calls()
    assert [call["attached"] for call in _attaches(calls)] == [name], calls
    assert scene.live().count(name) == 1 and scene.dead() == []
    if expected_action == "resurrect":
        assert [
            call.get("resurrected") for call in calls if call.get("resurrected")
        ] == [name]
    elif expected_action == "create":
        assert [call.get("created") for call in calls if call.get("created")].count(
            name
        ) == 1
    else:
        assert not [
            call
            for call in calls
            if call.get("created") == name or call.get("resurrected") == name
        ]


# --------------------------------------------------------------------------
# 3. inventory first: live attached / live detached / EXITED / unreadable
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_live_attached_start_opens_a_terminal_for_entry(
    tmp_path: Path, shell: str
) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("mlx-batch-runner",),
        clients=("mlx-batch-runner",),
    )
    result = _run(scene, "vc-start", shell=shell)

    assert _rc(result) == 0, result.stdout + result.stderr
    assert len(scene.terminal_launches()) == 1
    assert not _destroys(scene.calls())
    assert scene.live() == ["mlx-batch-runner"]


def test_live_detached_start_opens_a_terminal_for_entry(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner", live=("mlx-batch-runner",))
    result = _run(scene, "vc-start")

    assert _rc(result) == 0
    assert len(scene.terminal_launches()) == 1
    assert not _destroys(scene.calls())
    assert scene.live() == ["mlx-batch-runner"]


def test_exited_record_is_restored_before_opening_terminal(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner", dead=("mlx-batch-runner",))
    result = _run(scene, "vc-start")

    assert _rc(result) == 0, result.stdout + result.stderr
    assert [
        call.get("resurrected") for call in scene.calls() if call.get("resurrected")
    ] == ["mlx-batch-runner"]
    assert scene.dead() == [] and scene.live() == ["mlx-batch-runner"]
    assert len(scene.terminal_launches()) == 1


def test_unrestorable_exited_record_gives_one_recovery_step(tmp_path: Path) -> None:
    name = "mlx-batch-runner"
    scene = Scene(tmp_path, project=name, dead=(name,))
    result = _run(
        scene, "vc-start", extra_env={"VC_FRAME_CREATE_ERROR": "damaged record"}
    )

    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    assert f"vc-frame delete-session {name}, then run vc-start again" in result.stderr
    assert "Choose one:" not in result.stderr
    assert scene.dead() == [name] and scene.live() == []
    assert scene.terminal_launches(wait=0.5) == []


def test_inside_a_frame_live_workspace_switches_the_client(tmp_path: Path) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("mlx-batch-runner", "other-place"),
        clients=("mlx-batch-runner", "other-place"),
    )
    result = _run(
        scene,
        "vc-start",
        owned_terminal=True,
        developer_root=True,
        extra_env={
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "3",
            "VC_FRAME_SESSION_NAME": "other-place",
        },
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert [
        call["switched"] for call in _switches(scene.calls()) if call.get("switched")
    ] == [["other-place", "mlx-batch-runner"]]


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
    # The refusal itself names the reason. The inventory is read in the
    # caller's shell, so the reason survives to the sentence that refuses;
    # "unknown reason" was a reason lost in a command substitution.
    if "VC_FRAME_INVENTORY_ERROR" in fault:
        reason = "list-sessions exited 2: IPC error: cannot connect"
    else:
        reason = "unrecognized inventory line"
    assert f"could not read the live vc-frame session inventory ({reason}" in err, err
    assert "unknown reason" not in err, err
    assert "refusing to create mlx-batch-runner" in err
    assert "vc-frame list-sessions --no-formatting" in err
    _assert_nothing_mutated(scene.calls())
    _assert_no_workspace_record(scene)
    assert scene.live() == []
    assert scene.terminal_launches(wait=0.5) == []


def test_unreadable_default_operator_role_refuses_before_project_create(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, live=(HOST_SESSION,))
    (scene.table / "live" / HOST_SESSION).write_text("")
    result = _run(scene, "vc-start")
    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    assert HOST_SESSION in result.stderr and "role:" in result.stderr
    _assert_nothing_mutated(scene.calls())
    assert scene.terminal_launches(wait=0.1) == []


def test_unrelated_session_role_is_not_a_project_entry_prerequisite(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, live=(HOST_SESSION, "unrelated"))
    (scene.table / "live" / "unrelated").write_text("")
    result = _run(scene, "vc-start")
    assert _rc(result) == 0, result.stdout + result.stderr
    assert (scene.table / "live" / "unrelated").read_text() == ""
    assert not any(
        c["argv"] == ["--session", "unrelated", "action", "dump-layout"]
        for c in scene.calls()
    )


@pytest.mark.parametrize(
    ("layout", "role"),
    [
        ('layout { plugin location="frame-host" { frame_host "true"; } }', "host"),
        (
            'layout { plugin location="https://example.invalid/host" { frame_host true; } }',
            "host",
        ),
        ('layout { pane name="; frame_host true;"; }', "guest"),
        ('layout { pane { args "frame_host true"; } }', "guest"),
        ("layout { /* frame_host true; */ pane; // frame_host true\n}", "guest"),
        ("layout { frame_host false; pane; }", "guest"),
        ("layout { my_frame_host true; pane; }", "guest"),
        ("layout { frame_host trueish; pane; }", "guest"),
    ],
)
def test_session_role_uses_declared_identity_not_display_strings(
    tmp_path: Path, layout: str, role: str
) -> None:
    scene = Scene(tmp_path, live=("Dashboard",))
    (scene.table / "live" / "Dashboard").write_text(layout, encoding="utf-8")
    result = _eval_start_fn(
        f'_vetcoders_start_session_projection_role Dashboard "{scene.generation}/bin/vc-frame"',
        extra_env=scene.env(),
    )
    assert result.stdout.splitlines() == [role, "RC=[0]"], result
    _assert_nothing_mutated(scene.calls())


@pytest.mark.parametrize(
    "layout", ["engine error", "layout { pane;", "layout } { pane; }"]
)
def test_unparsable_live_role_warns_and_blocks_duplicate_host(
    tmp_path: Path, layout: str
) -> None:
    scene = Scene(tmp_path, live=(HOST_SESSION,))
    (scene.table / "live" / HOST_SESSION).write_text(layout, encoding="utf-8")
    result = _run(scene, "vc-start")
    assert _rc(result) == EXIT_INVENTORY, result
    assert "role: unknown" in result.stderr and HOST_SESSION in result.stderr
    _assert_nothing_mutated(scene.calls())


@pytest.mark.parametrize("host_layout", [None, "broken external host chrome"])
def test_host_creation_never_reads_or_passes_external_layout(
    tmp_path: Path, host_layout: str | None
) -> None:
    """The public door delegates host chrome to Frame, even with stale config."""
    scene = Scene(tmp_path, project="mlx-batch-runner")
    path = scene.config_dir / "layouts" / "host.kdl"
    if host_layout is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(host_layout)
    result = _run(scene, "vc-start")
    assert _rc(result) == 0, result.stdout + result.stderr
    _assert_host_first(scene, "mlx-batch-runner")
    host = next(c for c in scene.calls() if c.get("created") == HOST_SESSION)
    assert host["argv"] == ["attach", "--create-background", HOST_SESSION]


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
    assert "vc-frame refused to create the Frame host vc-host (exit 1)" in result.stderr
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
    assert scene.workspaces() == ["mlx"]
    _assert_host_first(scene, "mlx")
    # The terminal child records the existing WES ownership before another
    # repository with the same basename tries to enter.
    child = _run(
        scene,
        f"vc-start --repo {shlex.quote(str(repo_a))}",
        cwd=repo_a,
        tty=True,
        owned_terminal=True,
        developer_root=True,
        extra_env={
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
        },
    )
    assert _rc(child) == 0, child.stdout + child.stderr

    second = _run(scene, "vc-start", cwd=repo_b)
    assert _rc(second) == EXIT_EXISTS, second.stdout + second.stderr
    assert "workspace mlx already exists in vc-frame" in second.stderr
    assert (
        "start a different workspace here:   vc-start <new-name> --repo "
        + shlex.quote(str(repo_b.resolve()))
        in second.stderr
    ), second.stderr
    assert scene.workspaces() == ["mlx"], "a suffixed or renamed session appeared"
    assert [c["created"] for c in scene.calls() if c.get("created")] == [
        HOST_SESSION,
        "mlx",
    ]
    assert len(scene.terminal_launches()) == 1

    third = _run(scene, "vc-start mlx-b", cwd=repo_b)
    assert _rc(third) == 0, third.stderr
    assert scene.workspaces() == ["mlx", "mlx-b"]
    # One Operator session remains available alongside both projects.
    assert [c["created"] for c in scene.calls() if c.get("created")].count(
        HOST_SESSION
    ) == 1


def test_two_concurrent_starts_create_exactly_one_workspace(tmp_path: Path) -> None:
    """Both callers read `missing` then serialize on the create lock. The
    winner creates; the loser joins the same workspace. No duplicate session."""
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

    assert rcs == [0, 0], outs
    assert scene.workspaces() == ["mlx-batch-runner"]
    created = [c["created"] for c in scene.calls() if c.get("created")]
    assert sorted(created) == ["mlx-batch-runner", HOST_SESSION], scene.calls()
    assert any("created workspace mlx-batch-runner" in out for out, _ in outs)
    launches = scene.terminal_launches(expect=2, wait=3.0)
    assert len(launches) == 2, launches
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


def _assert_peer_switch(scene: Scene, current: str, project: str) -> None:
    calls = scene.calls()
    switches = _switches(calls)
    request = next(c for c in switches if not c.get("switched"))
    assert request["argv"] == [
        "--session",
        current,
        "--client-id",
        "1",
        "action",
        "switch-session",
        project,
    ], request
    assert request["VC_FRAME_SESSION_NAME"] == current, request
    assert request["VC_FRAME_PANE_ID"] == "2", request
    assert request["VC_FRAME_SOCKET_DIR"], request
    created = next(c for c in calls if c.get("created") == project)
    assert "--guest-workspace" not in created["argv"], created
    selected = Path(created["layout"])
    assert selected.name == "operator.kdl"
    assert (scene.table / "live" / project).read_text() == selected.read_text()
    assert not _attaches(calls), calls
    assert not _projects(calls), calls
    assert [c["argv"] for c in calls if "list-clients" in c["argv"]] == [
        ["--session", current, "action", "list-clients"]
    ], calls
    assert scene.terminal_launches(wait=0.1) == []


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("current", [HOST_SESSION, "project-a"])
def test_peer_start_refuses_ambiguous_source_and_preserves_session_contents(
    tmp_path: Path, tty: bool, current: str
) -> None:
    scene = Scene(
        tmp_path,
        live=(HOST_SESSION, "project-a"),
        clients=(HOST_SESSION, HOST_SESSION, "project-a", "project-a"),
        guests=("project-a",),
    )
    before = {p.name: p.read_bytes() for p in (scene.table / "live").iterdir()}
    result = _run(
        scene,
        "vc-start",
        owned_terminal=True,
        tty=tty,
        developer_root=True,
        extra_env=_inside_host_env(scene, current),
    )
    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    assert not _switches(scene.calls()), scene.calls()
    assert not _attaches(scene.calls()) and not _projects(scene.calls())
    assert "vc-frame attach mlx-batch-runner" in result.stdout + result.stderr
    assert "mlx-batch-runner" in scene.live()
    for name, content in before.items():
        assert (scene.table / "live" / name).read_bytes() == content
    assert len(_creates(scene.calls())) == 1


@pytest.mark.parametrize(
    "entry", ["vc-start operator", f"{shlex.quote(str(DECK))} start"]
)
def test_peer_public_aliases_share_native_switch(tmp_path: Path, entry: str) -> None:
    scene = Scene(tmp_path, live=(HOST_SESSION,), clients=(HOST_SESSION,))
    if entry.endswith(" start"):
        body = DECK.read_text().split("cmd_start() {", 1)[1].split("\n}\n", 1)[0]
        entry = (
            "_is_help_flag() { return 1; }; _script_owner_root() { return 0; }; "
            "_ensure_helpers_loaded() { return 0; }; cmd_start() {"
            + body
            + "\n}; cmd_start"
        )
    result = _run(
        scene,
        entry,
        owned_terminal=True,
        developer_root=True,
        extra_env=_inside_host_env(scene, HOST_SESSION),
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    _assert_peer_switch(scene, HOST_SESSION, "mlx-batch-runner")


@pytest.mark.parametrize("target_clients", [0, 1, 2, 3])
def test_peer_switch_allows_multiple_target_clients(
    tmp_path: Path, target_clients: int
) -> None:
    scene = Scene(
        tmp_path,
        live=("project-a", "project-b"),
        guests=("project-a", "project-b"),
        clients=("project-a",) + ("project-b",) * target_clients,
    )
    frame = scene.generation / "bin" / "vc-frame"
    result = _eval_start_fn(
        f'_vetcoders_start_enter_workspace_session "{frame}" project-b',
        extra_env=scene.env(_inside_host_env(scene, "project-a")),
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert [c["switched"] for c in scene.calls() if c.get("switched")] == [
        ["project-a", "project-b"]
    ]
    assert [c["argv"] for c in scene.calls() if "list-clients" in c["argv"]] == [
        ["--session", "project-a", "action", "list-clients"]
    ]
    assert not _creates(scene.calls()) and not _attaches(scene.calls())
    assert not _projects(scene.calls())


@pytest.mark.parametrize("replacement_ids", [[7, 11], [11]])
def test_peer_switch_keeps_observed_origin_during_concurrent_attach_or_disconnect(
    tmp_path: Path, replacement_ids: list[int]
) -> None:
    scene = Scene(
        tmp_path,
        live=("project-a", "project-b"),
        guests=("project-a", "project-b"),
        clients=("project-a",),
    )
    initial = {"project-a": {"client_ids": [7], "last_active_client_id": 7}}
    (scene.tmp_path / "client-state.json").write_text(json.dumps(initial))
    replacement = {
        "project-a": {
            "client_ids": replacement_ids,
            "last_active_client_id": 11,
        }
    }
    frame = scene.generation / "bin" / "vc-frame"
    result = _eval_start_fn(
        f'_vetcoders_start_enter_workspace_session "{frame}" project-b',
        extra_env=scene.env(
            {
                **_inside_host_env(scene, "project-a"),
                "VC_FRAME_CLIENT_STATE_AFTER_LIST": json.dumps(replacement),
            }
        ),
    )
    requests = [c for c in _switches(scene.calls()) if not c.get("switched")]
    assert len(requests) == 1, scene.calls()
    switched = [c for c in scene.calls() if c.get("switched")]
    if 7 in replacement_ids:
        assert _rc(result) == 0, result.stdout + result.stderr
        assert [c["switched_client_id"] for c in switched] == [7]
    else:
        assert _rc(result) == 2, result.stdout + result.stderr
        assert "requested client is not attached" in result.stderr
        assert not switched, switched
    assert requests[0]["argv"] == [
        "--session",
        "project-a",
        "--client-id",
        "7",
        "action",
        "switch-session",
        "project-b",
    ]
    assert not _attaches(scene.calls()) and not _projects(scene.calls())
    assert json.loads((scene.tmp_path / "client-state.json").read_text()) == replacement


@pytest.mark.parametrize(
    "fault",
    [
        {"VC_FRAME_CLIENTS_ERROR": "client inventory unavailable"},
        {"VC_FRAME_CLIENTS_MALFORMED": "not a client inventory"},
        {
            "VC_FRAME_CLIENTS_MALFORMED": "CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND\nnot-a-client terminal_1 zsh"
        },
        *[
            {
                "VC_FRAME_CLIENTS_MALFORMED": f"CLIENT_ID ZELLIJ_PANE_ID RUNNING_COMMAND\n{invalid_id} terminal_1 zsh"
            }
            for invalid_id in ("0", "-1", "65536", "999999999999999999999999")
        ],
    ],
)
def test_peer_switch_refuses_unreadable_source_client_inventory(
    tmp_path: Path, fault: dict[str, str]
) -> None:
    scene = Scene(tmp_path, live=(HOST_SESSION,), clients=(HOST_SESSION,))
    env = {**_inside_host_env(scene, HOST_SESSION), **fault}
    result = _run(
        scene, "vc-start", developer_root=True, owned_terminal=True, extra_env=env
    )
    assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
    assert not _switches(scene.calls()), scene.calls()
    assert not _attaches(scene.calls()) and not _projects(scene.calls())
    assert "mlx-batch-runner" in scene.live()
    assert "vc-frame attach mlx-batch-runner" in result.stderr
    assert scene.terminal_launches(wait=0.1) == []


@pytest.mark.parametrize("tty", [False, True])
def test_external_peer_entry_allows_busy_operator_and_project_sessions(
    tmp_path: Path, tty: bool
) -> None:
    scene = Scene(
        tmp_path,
        live=(HOST_SESSION, "project-a"),
        clients=(HOST_SESSION,) * 2 + ("project-a",) * 2,
        guests=("project-a",),
    )
    result = _run(
        scene,
        "vc-start",
        owned_terminal=tty,
        tty=tty,
        developer_root=tty,
        extra_env={
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
        }
        if tty
        else None,
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert len(_creates(scene.calls())) == 1
    assert not _projects(scene.calls())
    assert not _switches(scene.calls())
    assert not any("list-clients" in c["argv"] for c in scene.calls())
    if tty:
        assert [c["attached"] for c in _attaches(scene.calls())] == ["mlx-batch-runner"]
    else:
        assert scene.terminal_launches()[0]["created"] == "mlx-batch-runner"


def test_peer_start_preserves_customized_layout_and_full_chrome(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    layout = scene.config_dir / "layouts" / "operator.kdl"
    content = (
        'layout { session_layer { pane { plugin location="frame-host"; } } '
        'tab name="Custom" { pane; } }\n'
    )
    layout.write_text(content)
    result = _run(scene, "vc-start")
    assert _rc(result) == 0, result.stdout + result.stderr
    created = next(c for c in scene.calls() if c.get("created") == "mlx-batch-runner")
    assert "--guest-workspace" not in created["argv"]
    assert (scene.table / "live" / "mlx-batch-runner").read_text() == content


def test_peer_switch_failure_keeps_created_project_without_fallback(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, live=(HOST_SESSION,), clients=(HOST_SESSION,))
    env = _inside_host_env(scene, HOST_SESSION)
    env["VC_FRAME_SWITCH_ERROR"] = "injected native switch failure"
    result = _run(
        scene, "vc-start", developer_root=True, owned_terminal=True, extra_env=env
    )
    assert _rc(result) != 0, result.stdout + result.stderr
    assert env["VC_FRAME_SWITCH_ERROR"] in result.stderr
    assert "entry into mlx-batch-runner failed (exit 2)" in result.stderr
    assert "Retry with: vc-frame attach mlx-batch-runner" in result.stderr
    assert scene.live() == ["mlx-batch-runner", HOST_SESSION]
    assert not any(c.get("switched") for c in scene.calls())
    assert not _attaches(scene.calls()) and not _projects(scene.calls())
    assert not _destroys(scene.calls())
    assert scene.terminal_launches(wait=0.1) == []


def test_lobby_preserves_legacy_project_and_enters_recovered_operator(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, live=(HOST_SESSION,), guests=(HOST_SESSION,))
    lobby = scene.home / ".vibecrafted" / "projects"
    lobby.mkdir(parents=True)
    before = (scene.table / "live" / HOST_SESSION).read_bytes()
    result = _run(
        scene,
        "vc-start",
        cwd=lobby,
        owned_terminal=True,
        tty=True,
        developer_root=True,
        extra_env={
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
        },
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert (scene.table / "live" / HOST_SESSION).read_bytes() == before
    assert [c["attached"] for c in _attaches(scene.calls())] == ["vc-host-recovered"]
    assert scene.live() == [HOST_SESSION, "vc-host-recovered"]
    assert not _destroys(scene.calls())


@pytest.mark.parametrize("resurrect", [False, True])
def test_operator_creation_does_not_inherit_project_identity(
    tmp_path: Path, resurrect: bool
) -> None:
    scene = Scene(
        tmp_path,
        live=("project-a",),
        clients=("project-a",),
        guests=("project-a",),
        dead=(HOST_SESSION,) if resurrect else (),
    )
    if resurrect:
        (scene.table / "dead" / HOST_SESSION).write_text(
            "layout { frame_host true; pane; }\n"
        )
    result = _run(
        scene,
        "vc-start",
        owned_terminal=True,
        developer_root=True,
        extra_env=_inside_host_env(scene, "project-a"),
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    operator = next(
        c
        for c in scene.calls()
        if c.get("created") == HOST_SESSION or c.get("resurrected") == HOST_SESSION
    )
    project = next(c for c in scene.calls() if c.get("created") == "mlx-batch-runner")
    assert all(value is None for value in operator["workspace_identity"].values()), (
        operator
    )
    assert (
        project["workspace_identity"]["VIBECRAFTED_WORKSPACE_ID"]
        == "ws-mlx-batch-runner"
    )
    assert project["workspace_identity"]["VIBECRAFTED_WORKSPACE_ROOT"] == str(
        scene.root.resolve()
    )


# --------------------------------------------------------------------------
# 5. no TTY: inherited Frame env is not a terminal; the child enters what the
#    parent created; the boundary stops recursion
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("client_count", [0, 1, 2, 3])
def test_no_tty_peer_start_switches_only_for_unique_source_client(
    tmp_path: Path, shell: str, client_count: int
) -> None:
    scene = Scene(
        tmp_path,
        live=(HOST_SESSION, "other-place"),
        clients=("other-place",) * client_count,
        guests=("other-place",),
    )
    result = _run(
        scene,
        "vc-start",
        shell=shell,
        owned_terminal=True,
        developer_root=True,
        extra_env=_inside_host_env(scene),
    )
    if client_count == 1:
        assert _rc(result) == 0, result.stdout + result.stderr
        _assert_peer_switch(scene, "other-place", "mlx-batch-runner")
    else:
        assert _rc(result) == EXIT_INVENTORY, result.stdout + result.stderr
        assert not _switches(scene.calls()), scene.calls()
        assert not _attaches(scene.calls()) and not _projects(scene.calls())
        assert "vc-frame attach mlx-batch-runner" in result.stderr
        assert scene.terminal_launches(wait=0.1) == []
    assert scene.live() == ["mlx-batch-runner", "other-place", HOST_SESSION]


def test_the_terminal_child_enters_the_session_its_parent_created(
    tmp_path: Path,
) -> None:
    """An admitted terminal child enters its parent's exact LIVE WES seat
    without creating a session, opening a window or rewriting ownership."""
    scene = Scene(tmp_path, project="mlx-batch-runner", live=("mlx-batch-runner",))
    result = _run(
        scene,
        f"vc-start --repo {shlex.quote(str(scene.root))}",
        owned_terminal=True,
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
    # Returning to a validated LIVE WES seat reads its exact identity without
    # rewriting the attachment already owned by the parent.
    binds = [c for c in scene.owner_calls() if c.startswith("workspace session-attach")]
    assert not binds, binds
    owners = [c for c in scene.owner_calls() if c.startswith("workspace session-owner")]
    assert owners and all("--runtime-state live" in call for call in owners)
    resolves = [c for c in scene.owner_calls() if c.startswith("workspace resolve")]
    assert resolves, scene.owner_calls()
    for resolve in resolves:
        assert f"--root {scene.root}" in resolve, resolve
        assert "--runtime-session-id mlx-batch-runner" in resolve, resolve
        assert f"--socket-dir {scene.tmp_path / 'sock'}" in resolve, resolve
        assert "--runtime-state live" in resolve, resolve


def test_a_child_without_a_created_marker_enters_the_live_workspace(
    tmp_path: Path,
) -> None:
    """A terminal child joins the selected live workspace by its exact name."""
    scene = Scene(tmp_path, project="mlx-batch-runner", live=("mlx-batch-runner",))
    result = _run(
        scene,
        f"vc-start --repo {shlex.quote(str(scene.root))}",
        owned_terminal=True,
        tty=True,
        developer_root=True,
        extra_env={
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
        },
    )
    assert "RC=[0]" in result.stdout, result.stdout + result.stderr
    assert [c["attached"] for c in _attaches(scene.calls())] == ["mlx-batch-runner"]


def test_boundary_without_a_pty_fails_closed_without_a_second_terminal(
    tmp_path: Path,
) -> None:
    """Boundary set but still no PTY (a broken terminal host): no escalation
    loop, and the entry path reaches the engine's own TTY refusal on
    the attach rather than inventing a window. The session it created stays
    for `vc-dashboard attach`."""
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(
        scene,
        f"vc-start --repo {shlex.quote(str(scene.root))}",
        owned_terminal=True,
        developer_root=True,
        extra_env={
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
        },
    )
    assert "RC=[0]" not in result.stdout, result.stdout + result.stderr
    assert scene.terminal_launches(wait=0.5) == []
    assert scene.workspaces() == ["mlx-batch-runner"]
    _assert_host_first(scene, "mlx-batch-runner")


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
    assert scene.workspaces() == ["mlx-batch-runner"]
    _assert_host_first(scene, "mlx-batch-runner")


# --------------------------------------------------------------------------
# 6. TTY entry: direct project attachment outside Frame
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_admitted_owned_tty_creates_and_attaches_without_another_terminal(
    tmp_path: Path, shell: str
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(
        scene,
        "vc-start",
        shell=shell,
        owned_terminal=True,
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
    _assert_host_first(scene, "mlx-batch-runner")
    calls = scene.calls()
    assert len(_creates(calls)) == 2
    attaches = _attaches(calls)
    # The external client attaches directly to its ordinary project session.
    assert len(attaches) == 1 and attaches[0]["stdin_tty"] is True, calls
    assert attaches[0]["attached"] == "mlx-batch-runner", attaches[0]
    assert attaches[0]["VC_FRAME_SESSION_NAME"] is None, attaches[0]
    assert not _switches(calls)
    assert scene.terminal_launches(wait=0.5) == []
    assert calls.index(_creates(calls)[-1]) < calls.index(attaches[0]), (
        "attach before create"
    )
    assert not _projects(scene.calls())
    binds = [c for c in scene.owner_calls() if "session-attach" in c]
    assert any(
        "--state live" in b and "--runtime-session-id mlx-batch-runner" in b
        for b in binds
    ), binds
    # The catalogue's own label never becomes the Frame name.
    assert not any("catalog-" in n for n in scene.live()), scene.live()


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_create_lock_dies_with_holder_and_retry_succeeds(
    tmp_path: Path, shell: str
) -> None:
    sock = tmp_path / "sock"
    sock.mkdir()
    env = _create_lock_env(tmp_path, sock=sock)
    ready = tmp_path / "lock-held"
    err_path = tmp_path / "create-lock-holder-race.err"
    holder = _spawn_owned_create_lock_holder(
        tmp_path=tmp_path,
        env=env,
        shell=shell,
        session_name="race",
        ready=ready,
    )
    try:
        _wait_owned_create_lock_ready(holder, ready, err_path)
        busy = _run_create_lock(
            "_vetcoders_start_acquire_create_lock race",
            "lock_rc=$?",
            'printf "RC=[%s]\\n" "$lock_rc"',
            'exit "$lock_rc"',
            env=env,
            shell=shell,
        )
        assert busy.returncode == 4, busy.stdout + busy.stderr
        assert _rc(busy) == 4, busy.stdout + busy.stderr
        assert "could not obtain exclusive create lock" in busy.stderr
    finally:
        _teardown_owned_create_lock_holder(holder)
    retry = _run_create_lock(
        "_vetcoders_start_acquire_create_lock race",
        "lock_rc=$?",
        'if ((lock_rc != 0)); then printf "RC=[%s]\\n" "$lock_rc"; exit "$lock_rc"; fi',
        "_vetcoders_start_release_create_lock",
        "printf RETRY_OK\\n",
        env=env,
        shell=shell,
    )
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert "RETRY_OK" in retry.stdout
    lock_file = sock / ".vc-start-create.race.lock"
    assert lock_file.is_file()


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_create_locks_are_independent_per_socket_namespace(
    tmp_path: Path, shell: str
) -> None:
    sock_a = tmp_path / "sock-a"
    sock_b = tmp_path / "sock-b"
    sock_a.mkdir()
    sock_b.mkdir()
    env = _create_lock_env(tmp_path)
    env_a = dict(env)
    env_a["VC_FRAME_SOCKET_DIR"] = str(sock_a)
    env_b = dict(env)
    env_b["VC_FRAME_SOCKET_DIR"] = str(sock_b)
    ready = tmp_path / "ns-held"
    err_path = tmp_path / "create-lock-holder-shared-name.err"
    holder = _spawn_owned_create_lock_holder(
        tmp_path=tmp_path,
        env=env_a,
        shell=shell,
        session_name="shared-name",
        ready=ready,
    )
    try:
        _wait_owned_create_lock_ready(holder, ready, err_path)
        other = _run_create_lock(
            "_vetcoders_start_acquire_create_lock shared-name",
            "lock_rc=$?",
            'if ((lock_rc != 0)); then printf "RC=[%s]\\n" "$lock_rc"; exit "$lock_rc"; fi',
            "_vetcoders_start_release_create_lock",
            "printf NS_OK\\n",
            env=env_b,
            shell=shell,
        )
        assert other.returncode == 0, other.stdout + other.stderr
        assert "NS_OK" in other.stdout
    finally:
        _teardown_owned_create_lock_holder(holder)
    assert (sock_a / ".vc-start-create.shared-name.lock").is_file()
    assert (sock_b / ".vc-start-create.shared-name.lock").is_file()


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_create_lock_refuses_leftover_mkdir_directory_without_deleting(
    tmp_path: Path, shell: str
) -> None:
    sock = tmp_path / "sock"
    sock.mkdir()
    leftover = sock / ".vc-start-create.stale.lock"
    leftover.mkdir()
    marker = leftover / "foreign"
    marker.write_text("keep\n", encoding="utf-8")
    env = _create_lock_env(tmp_path, sock=sock)
    result = _run_create_lock(
        "_vetcoders_start_acquire_create_lock stale",
        "lock_rc=$?",
        'printf "RC=[%s]\\n" "$lock_rc"',
        'exit "$lock_rc"',
        env=env,
        shell=shell,
    )
    assert result.returncode == 4, result.stdout + result.stderr
    assert _rc(result) == 4, result.stdout + result.stderr
    assert leftover.is_dir()
    assert marker.read_text(encoding="utf-8") == "keep\n"
    assert "without removing" in result.stderr


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize(
    "close_lock_fd",
    [True, False],
    ids=["close", "no-close"],
)
def test_create_lock_is_not_held_by_live_frame_env_child(
    tmp_path: Path, shell: str, close_lock_fd: bool
) -> None:
    """Create holds flock across `_vetcoders_start_frame_env` -> Frame/PTY.

    The owner is a synchronous CLI: acquire, invoke frame-env, release.
    The payload forks a daemon and `_exit`s the parent so frame-env
    returns while the child stays alive. An extra background shell job
    is not the production topology and keeps a lock fd.

    Negative control: when `_vetcoders_start_close_create_lock_fd` is a
    no-op, the living child holds the lock and a second acquire fails.
    Teardown SIGKILLs only that owned daemon.
    """
    sock = tmp_path / "sock"
    sock.mkdir()
    env = _create_lock_env(tmp_path, sock=sock)
    child_pid_path = tmp_path / "inherit-child.pid"
    lock_file = sock / ".vc-start-create.inherit.lock"
    child_pid: int | None = None
    try:
        owner = _run_owned_frame_env_lock_inherit_daemon(
            env=env,
            shell=shell,
            child_pid_path=child_pid_path,
            close_lock_fd=close_lock_fd,
        )
        assert owner.returncode == 0, owner.stdout + owner.stderr
        child_pid = _wait_owned_daemon_pid(child_pid_path)
        os.kill(child_pid, 0)
        retry = _run_create_lock(
            "_vetcoders_start_acquire_create_lock inherit",
            "lock_rc=$?",
            'if ((lock_rc != 0)); then printf "RC=[%s]\\n" "$lock_rc"; exit "$lock_rc"; fi',
            "_vetcoders_start_release_create_lock",
            "printf INHERIT_OK\\n",
            env=env,
            shell=shell,
        )
        os.kill(child_pid, 0)
        if close_lock_fd:
            assert retry.returncode == 0, retry.stdout + retry.stderr
            assert "INHERIT_OK" in retry.stdout
            assert "could not obtain exclusive create lock" not in retry.stderr
        else:
            assert retry.returncode == 4, retry.stdout + retry.stderr
            assert "could not obtain exclusive create lock" in retry.stderr
            assert child_pid in _create_lock_holder_pids(lock_file), (
                child_pid,
                _create_lock_holder_pids(lock_file),
            )
        os.kill(child_pid, 0)
    finally:
        _teardown_owned_frame_env_lock_inherit(child_pid, child_pid_path)


# --------------------------------------------------------------------------
# 7. structural: both entrypoints share the owner; the help is true
# --------------------------------------------------------------------------


def test_both_entrypoints_parse_once_and_enter_the_same_owner() -> None:
    dispatch = DISPATCH_SH.read_text(encoding="utf-8")
    start_body = dispatch.split("vc-start()")[1].split("vc-dashboard()")[0]
    deck = DECK.read_text(encoding="utf-8")
    deck_body = deck.split("cmd_start()")[1].split("\n}\n")[0]
    # Both enter the one owner with the parsed argv. 88ea1097 guarded the deck
    # expansion (`${a[@]+"${a[@]}"}`): a bare `vibecrafted start` leaves the
    # array empty and the plain form dies with `unbound variable` under
    # `set -u` on macOS /bin/bash 3.2. The shell function may keep the plain
    # form; the deck, which runs under `set -u`, must carry the guard.
    plain_entry = '_vetcoders_start_entry "${_vetcoders_start_frame_argv[@]}"'
    guarded_entry = (
        "_vetcoders_start_entry "
        '${_vetcoders_start_frame_argv[@]+"${_vetcoders_start_frame_argv[@]}"}'
    )
    for body in (start_body, deck_body):
        assert '_vetcoders_start_prepare_arguments "$@"' in body
        entries = [
            line.strip()
            for line in body.splitlines()
            if line.strip().startswith("_vetcoders_start_entry ")
        ]
        assert len(entries) == 1, entries
        assert entries[0] in (plain_entry, guarded_entry), entries
        assert body.index('_vetcoders_start_prepare_arguments "$@"') < body.index(
            entries[0]
        )
        assert "_vetcoders_launch_dashboard" not in body
        assert "_vetcoders_start_open_terminal_if_needed" not in body
    assert guarded_entry in deck_body
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
        "an EXITED",
        "0 entered",
        "2 usage/name/root",
        "3 foreign name collision",
        "4 inventory/restore/create",
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


def _admitted_frame() -> Path | None:
    """Explicit paired engine: an old installed projection build is not admission."""
    candidate = os.environ.get("VC_FRAME_ADMITTED_BIN", "")
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
    3. the name then reads `live`; the engine's own serialization job writes
       THIS session's resurrection record to its cache dir on the product's
       interval, and only once that real file exists is the session killed
       (a bounded wait, never a created file) -- so the name reads `dead` (an
       EXITED record the engine would resurrect) rather than racing into
       `missing`, and the public entry refuses it with exit 3 and the
       resurrect/delete commands, creating nothing;
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
    # This config file is what the direct `frame(...)` calls below read. It
    # deliberately does NOT try to shorten serialization: the shipped helpers
    # run in developer mode (VIBECRAFTED_PREFER_REPO_VC_FRAME, the only mode
    # that honours VIBECRAFTED_VC_FRAME_BIN -- vc_frame.sh
    # _vetcoders_vc_frame_bin), and that mode pins the engine's config to the
    # repository's own `config/vc-frame/config.kdl`
    # (frontier.sh _vetcoders_pin_vc_frame_config_dir). A sandbox
    # `serialization_interval` would therefore be dead text: the session
    # server never reads this file. The wait below is paced by the product's
    # real setting instead -- see await_persisted_record.
    (sandbox / "cfg" / "config.kdl").write_text(
        "keybinds clear-defaults=true {}\n", encoding="utf-8"
    )
    shipped = (
        SOURCE_CORE_DIR
        / "vibecrafted_core"
        / "config"
        / "vc-frame"
        / "layouts"
        / "operator.kdl"
    )
    layout = sandbox / "cfg" / "layouts" / "operator.kdl"
    shutil.copy2(shipped, layout)
    # Public `vc-start --repo <dir>` names the workspace after the basename.
    session = repo.name

    env = os.environ.copy()
    _strip_identity_env(env)
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

    def engine_cache_dir() -> Path:
        """The engine's own answer, not a guess: `setup --check` prints the
        cache dir it resolved under this sandbox's HOME."""
        check = frame("setup", "--check")
        marker = "[CACHE DIR]: "
        for line in (check.stdout + check.stderr).splitlines():
            if line.startswith(marker):
                return Path(line[len(marker) :].strip().strip('"'))
        raise AssertionError(
            "the engine did not report a cache dir: " + check.stdout + check.stderr
        )

    def persisted_record(cache: Path, name: str) -> Path | None:
        """The serialized resurrection layout the engine writes for ONE name:
        <cache>/<contract>/session_info/<name>/session-layout.kdl. The
        contract segment belongs to the engine's build, so it is matched, not
        spelled out. A zero-length file is a write in progress, not a record."""
        for candidate in sorted(
            cache.glob(f"*/session_info/{name}/session-layout.kdl")
        ):
            try:
                if candidate.stat().st_size > 0:
                    return candidate
            except OSError:
                continue
        return None

    def await_persisted_record(name: str, timeout: float = 120.0) -> Path:
        """Bounded wait for the engine's real file -- never a created one.

        The repository config the helpers pin sets `session_serialization
        true` but no `serialization_interval`, so the engine keeps its
        DEFAULT_SERIALIZATION_INTERVAL of 60000ms (Frame background_jobs.rs)
        and the detached job writes this session's first record shortly
        after that minute (measured: 65s on the reference host). Killing
        before that write leaves nothing to resurrect and the name reads
        `missing`, not `dead` -- which is the race this wait removes. The
        budget clears the minute with room for a loaded host; it is a bound,
        not an expected duration."""
        cache = engine_cache_dir()
        deadline = time.monotonic() + timeout
        while True:
            found = persisted_record(cache, name)
            if found is not None:
                return found
            assert time.monotonic() < deadline, (
                f"the engine serialized no record for {name} under {cache} "
                f"within {timeout}s; killing now would leave nothing to "
                f"resurrect. Present: "
                f"{sorted(str(q) for q in cache.glob('*/session_info/*'))}"
            )
            time.sleep(0.25)

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

        # This lower-level creator deliberately has no WES seat. Public entry
        # refuses that unbound native session before opening any terminal.
        entry = shell(
            f'vc-start --repo {shlex.quote(str(repo))}\nprintf "RC=[%s]\\n" "$?"'
        )
        assert "RC=[2]" in entry.stdout, entry.stdout + entry.stderr
        assert "no consistent live WES identity" in entry.stderr
        assert "no installed vc-start front door" not in entry.stderr
        assert state() == "live"

        # Do not race the serializer: the EXITED record asserted below is a
        # real file this engine wrote for THIS session, proven present before
        # the kill. kill-session ends the server and keeps the record; only
        # delete-session removes it.
        record = await_persisted_record(session)

        kill = frame("kill-session", session)
        assert kill.returncode == 0, kill.stderr
        assert record.exists(), record
        deadline = time.monotonic() + 10
        while state() != "dead" and time.monotonic() < deadline:
            time.sleep(0.2)
        assert state() == "dead"
        exited = frame("list-sessions", "--no-formatting")
        assert "(EXITED" in exited.stdout, exited.stdout

        restored = shell(
            f'_vetcoders_start_resurrect_workspace_session "{_REAL_FRAME}" {session}\n'
            'printf "RC=[%s]\\n" "$?"'
        )
        assert "RC=[0]" in restored.stdout, restored.stdout + restored.stderr
        assert state() == "live", "the engine did not restore the existing record"

        deleted = frame("delete-session", session, "--force")
        assert deleted.returncode == 0, deleted.stderr
        assert state() == "missing"
    finally:
        frame("kill-session", session)
        frame("delete-session", session, "--force")
        leftover = frame("list-sessions", "--no-formatting").stdout
        shutil.rmtree(sandbox, ignore_errors=True)
    assert session not in leftover, leftover


@pytest.mark.parametrize(
    "route",
    [
        "prepare",
        "switch",
        "switch-failure",
        "no-client",
        "ambiguous",
        "invalid-name",
        "stale-engine",
        "symlink-facade",
        "live-build",
        "live-history",
    ],
)
def test_workshop_missing_destination_prepares_in_background_and_switches_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """Workshop prepares through the real owner, then switches the exact client.

    Frame is a file-backed engine double in an isolated home. The source
    developer lane runs the real shell owner without admitting an installed
    generation or starting the host server service.
    """
    from vibecrafted_core.workspace_catalog import (
        read_workspace_session,
        resolve_run_workspace_identity,
    )

    scene = Scene(
        tmp_path,
        project="selected",
        live=["workday-440"],
        clients=["workday-440"],
        git=route.startswith("live-"),
    )
    if route == "no-client":
        scene.clients_file.write_text("")
    elif route == "ambiguous":
        scene.clients_file.write_text("workday-440\nworkday-440\n")
    env = scene.env(
        {
            "VC_FRAME": "0",
            "VC_FRAME_SESSION_NAME": "workday-440",
            "VC_FRAME_PANE_ID": "terminal_1",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin" / "vc-frame"),
            "VIBECRAFTED_RUNTIME_ROOT": str(scene.generation),
            "PATH": str(scene.generation / "bin") + os.pathsep + os.environ["PATH"],
        }
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    identity = resolve_run_workspace_identity(root=scene.root)
    if route.startswith("live-"):
        from vibecrafted_core import workspace_catalog as wc

        wc.record_runtime_session_attachment(
            workspace_id=identity.workspace_id,
            vibecrafted_session_id=identity.vibecrafted_session_id,
            workspace_instance_id=identity.workspace_instance_id,
            runtime="vc-frame",
            runtime_session_id="selected",
            state="live",
            socket_dir=env["VC_FRAME_SOCKET_DIR"],
        )
        (scene.table / "live/selected").write_text("layout { pane; }\n")
        (scene.root / "README").write_text("changed project\n")
        _seed_committed_git(scene.root, home=scene.home)
        inherited = identity
        if route == "live-history":
            identity = resolve_run_workspace_identity(root=scene.root, env={})
            wc.record_runtime_session_attachment(
                workspace_id=identity.workspace_id,
                vibecrafted_session_id=identity.vibecrafted_session_id,
                workspace_instance_id=identity.workspace_instance_id,
                runtime="vc-frame",
                runtime_session_id="selected",
                state="live",
                socket_dir=env["VC_FRAME_SOCKET_DIR"],
            )
        snapshot = {
            str(p): p.read_bytes()
            for folder in [wc.instances_dir(), wc.sessions_dir()]
            for p in folder.glob("*.json")
        }
    else:
        inherited = identity
    for key, value in inherited.to_env().items():
        monkeypatch.setenv(key, value)
    (scene.generation / "VERSION").write_text("4.4.0+g00000000\n")
    (scene.generation / "bin" / "python3").symlink_to(sys.executable)
    _write(
        scene.owner,
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(SOURCE_CORE_DIR)!r})\n"
        "if sys.argv[1:2] == ['workspace']:\n"
        "    from vibecrafted_core.workspace_catalog import workspace_cli_main\n"
        "    sys.exit(workspace_cli_main(sys.argv[2:]))\n"
        "sys.exit(0)\n",
    )
    _write(
        scene.generation / "bin" / "vc-start",
        "#!/bin/bash\n"
        f"export VIBECRAFTED_CORE_DIR={shlex.quote(str(SOURCE_CORE_DIR))}\n"
        f"source {shlex.quote(str(SHELL_SH))}\n"
        f"_vetcoders_vc_frame_loaded_root={shlex.quote(str(REPO_ROOT))}\n"
        'vc-start "$@"\n',
    )
    # The selected generation physically owns its facade. This source-lane
    # fixture executes the actual adjacent source implementation, not a forged
    # terminal-origin receipt or a public-entry policy stub.
    _write(
        scene.generation
        / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh",
        f"export VIBECRAFTED_CORE_DIR={shlex.quote(str(SOURCE_CORE_DIR))}\n"
        f"source {shlex.quote(str(SHELL_SH))}\n"
        f"_vetcoders_vc_frame_loaded_root={shlex.quote(str(REPO_ROOT))}\n",
    )
    if route == "stale-engine":
        stale_called = tmp_path / "stale-engine-called"
        stale = _write(
            tmp_path / "older-frame",
            f"#!/bin/bash\ntouch {shlex.quote(str(stale_called))}\nexit 23\n",
        )
        monkeypatch.setenv("VIBECRAFTED_VC_FRAME_BIN", str(stale))
    elif route == "symlink-facade":
        facade = (
            scene.generation
            / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
        )
        outside = tmp_path / "outside-facade.sh"
        facade.rename(outside)
        facade.symlink_to(outside)
    frame_script = VC_FRAME_STUB.replace(
        'if verb == "switch-session":',
        'if verb == "new-tab":\n        sys.exit(0)\n    if verb == "switch-session":',
    )
    if route == "switch-failure":
        frame_script = frame_script.replace(
            'if verb == "switch-session":',
            'if verb == "switch-session":\n'
            '        print("Frame refused the client switch: incompatible session contract.")\n'
            '        print("Open a current-generation seat from the Sessions rail.")\n'
            "        sys.exit(4)",
        )
    _write(scene.generation / "bin" / "vc-frame", frame_script)
    script = SOURCE_CORE_DIR / "vibecrafted_core/config/vc-frame/vc-agent-workshop.py"
    spec = importlib.util.spec_from_file_location("workshop_resume_route", script)
    assert spec is not None and spec.loader is not None
    workshop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workshop)

    if route == "symlink-facade":
        with pytest.raises(ValueError, match="physical shell facade"):
            workshop.ensure_live_destination(scene.root, "selected", ["workday-440"])
        assert scene.live() == ["workday-440"]
        assert not scene.calls()
        assert not scene.terminal_launches(wait=0.1)
        return
    if route == "invalid-name":
        with pytest.raises(ValueError, match="path separators are not allowed"):
            workshop.ensure_live_destination(scene.root, "../foreign", ["workday-440"])
        assert scene.live() == ["workday-440"]
        assert not _creates(scene.calls())
        assert not scene.terminal_launches(wait=0.1)
        return
    if route == "prepare" or route.startswith("live-"):
        admitted = workshop.ensure_live_destination(
            scene.root, "selected", ["workday-440"]
        )
        assert admitted.session == "selected"
        assert admitted.environment["VIBECRAFTED_RUNTIME_ROOT"] == str(scene.generation)
        if route.startswith("live-"):
            assert (
                admitted.environment[wc.ENV_VIBECRAFTED_SESSION_ID]
                == identity.vibecrafted_session_id
            )
            assert (
                admitted.environment[wc.ENV_WORKSPACE_INSTANCE_ID]
                == identity.workspace_instance_id
            )
            assert admitted.environment[wc.ENV_BUILD_ID] == identity.build_id.rendered
            assert {
                str(p): p.read_bytes()
                for folder in [wc.instances_dir(), wc.sessions_dir()]
                for p in folder.glob("*.json")
            } == snapshot
            assert not _creates(scene.calls())
            assert not scene.terminal_launches(wait=0.1)
            assert not any(
                "switch-session" in c["argv"] or c.get("attached")
                for c in scene.calls()
            )
            return
    else:
        from types import SimpleNamespace

        launched = workshop.Workshop(SimpleNamespace(), mode="launcher")
        launched.path = str(scene.root)
        launched.agent = workshop.AGENTS.index("codex")
        launched.launch_mode = workshop.LAUNCH_MODES.index("partner")
        launched.runtime = workshop.RUNTIME_POLICIES.index("local-native")
        launched.permissions = workshop.PERMISSION_POLICIES.index("bypass")
        launched.continuity = workshop.CONTINUITY_MODES.index("fresh")
        monkeypatch.setattr(launched, "draw", lambda: None)
        monkeypatch.setattr(
            launched,
            "_continuity_caps",
            lambda _: {"fresh": {"available": True, "reason": ""}},
        )
        monkeypatch.setattr(
            workshop,
            "runtime_policy_capabilities",
            lambda _: {"local-native": {"available": True, "reason": ""}},
        )
        monkeypatch.setattr(
            workshop,
            "launch_argv",
            lambda *_a, **_kw: [str(scene.owner), "partner", "codex"],
        )
        launched._launch_one()
        if route == "switch-failure":
            assert "incompatible session contract" in launched.error
            assert "Open a current-generation seat" in launched.error
        elif route in ("no-client", "ambiguous"):
            assert "exactly one source client" in launched.error
        else:
            assert launched.error == ""
    receipt = read_workspace_session(identity.vibecrafted_session_id)
    assert any(
        a.runtime_session_id == "selected" and a.state == "live"
        for a in receipt.attachments
    )
    assert scene.live() == ["selected", HOST_SESSION, "workday-440"]
    if route == "stale-engine":
        assert not stale_called.exists()
    calls = scene.calls()
    # Preparation is detached. Neither native client attach nor a user terminal
    # is part of the internal operation; only the post-pane switch is permitted.
    assert not scene.terminal_launches(wait=0.1)
    assert not any(
        c["argv"][:1] == ["attach"] and "--create-background" not in c["argv"]
        for c in calls
    )
    pane_indices = [i for i, c in enumerate(calls) if "new-tab" in c["argv"]]
    switches = [
        (i, c)
        for i, c in enumerate(calls)
        if "switch-session" in c["argv"] and "switched" not in c
    ]
    if route == "prepare":
        assert not pane_indices and not switches
    else:
        assert len(pane_indices) == 1
        assert len(switches) == (0 if route in ("no-client", "ambiguous") else 1)
        for i, call in switches:
            assert i > pane_indices[0]
            assert call["argv"][:4] == ["--session", "workday-440", "--client-id", "1"]


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

    input_offset = 0
    while not os.path.exists(release_path):
        keys_path = attached_path + ".keys"
        if os.path.isfile(keys_path):
            with open(keys_path, "rb") as keys:
                keys.seek(input_offset)
                pending = keys.read()
            if pending:
                os.write(fd, pending)
                input_offset += len(pending)
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


# Persistent peer-session chrome is identified by resolved plugin URLs.
# Titles render asynchronously; runtime identity and geometry must survive
# leaving/rejoining the session with another client still attached.
_SESSION_LAYER_PLUGIN_URLS = (
    "vc-frame:link",
    "vc-frame:vc-tab-title",
    "frame-host",
)
_SHORT_TEMP_ROOT = "/tmp"


def _pane_plugin_url(pane: dict) -> str:
    return str(pane.get("plugin_url") or "").strip()


def _plugins_by_url(rows, urls) -> dict[str, dict]:
    wanted = set(urls)
    owned: dict[str, dict] = {}
    if not isinstance(rows, list):
        return owned
    for pane in rows:
        if not isinstance(pane, dict):
            continue
        if not bool(pane.get("is_plugin")):
            continue
        url = _pane_plugin_url(pane)
        if url in wanted and url not in owned:
            owned[url] = pane
    return owned


def _count_plugins_by_url(rows, urls) -> dict[str, int]:
    """Real occurrences per owner. `_plugins_by_url` keeps the first pane
    per URL, so a duplicated rail would otherwise stay invisible."""
    wanted = set(urls)
    counts = dict.fromkeys(wanted, 0)
    if not isinstance(rows, list):
        return counts
    for pane in rows:
        if not isinstance(pane, dict):
            continue
        if not bool(pane.get("is_plugin")):
            continue
        url = _pane_plugin_url(pane)
        if url in wanted:
            counts[url] += 1
    return counts


def _session_layer_plugins(rows) -> dict[str, dict]:
    return _plugins_by_url(rows, _SESSION_LAYER_PLUGIN_URLS)


def _session_layer_geometry(rows) -> dict[str, tuple]:
    return {
        url: (
            pane.get("id"),
            pane.get("plugin_runtime_id"),
            pane.get("pane_x"),
            pane.get("pane_y"),
            pane.get("pane_rows"),
            pane.get("pane_columns"),
        )
        for url, pane in _session_layer_plugins(rows).items()
    }


def _session_layer_ready(rows):
    """Persistent rails: exactly one link, one tab-title, one frame-host."""
    if not isinstance(rows, list) or not rows:
        return []
    counts = _count_plugins_by_url(rows, _SESSION_LAYER_PLUGIN_URLS)
    if all(counts.get(url) == 1 for url in _SESSION_LAYER_PLUGIN_URLS):
        return rows
    return []


def _assert_session_layer(rows, *, previous_geometry=None):
    """Stable session-layer identity across peer-session switching: exactly one
    owner per plugin_url, each keeping its typed pane id, plugin_runtime_id
    and geometry. Plugin-rendered titles are never identity."""
    assert _session_layer_ready(rows), rows
    counts = _count_plugins_by_url(rows, _SESSION_LAYER_PLUGIN_URLS)
    assert counts == dict.fromkeys(_SESSION_LAYER_PLUGIN_URLS, 1), (counts, rows)
    plugins = _session_layer_plugins(rows)
    for url, pane in plugins.items():
        assert bool(pane.get("is_plugin")) is True, pane
        assert _pane_plugin_url(pane) == url, pane
        # `plugin_runtime_id` is legitimately 0, so absence is `is None`.
        assert pane.get("id") is not None, pane
        assert pane.get("plugin_runtime_id") is not None, pane
    geometry = _session_layer_geometry(rows)
    assert set(geometry) == set(_SESSION_LAYER_PLUGIN_URLS), geometry
    if previous_geometry is not None:
        assert geometry == previous_geometry, (previous_geometry, geometry)
    return geometry


def _exclusive_sandbox(prefix: str = "vcs-") -> Path:
    """Short exclusive socket-capable root. Default $TMPDIR is too long on macOS."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=_SHORT_TEMP_ROOT))


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


def _wait_until(probe, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(0.2)
    return last


@pytest.mark.skipif(
    _ADMITTED_FRAME is None,
    reason="set VC_FRAME_ADMITTED_BIN to the paired peer-session engine",
)
def test_admitted_frame_peer_keyboard_roundtrip_with_two_clients() -> None:
    """Opt-in real PTY proof: two projects, two clients in A, Cmd navigation.

    No build/install is performed. This gate deliberately requires an explicit
    engine artifact. Fixture tests above cannot certify these runtime claims.
    """
    assert _ADMITTED_FRAME is not None
    binary = str(_ADMITTED_FRAME)
    sandbox = _exclusive_sandbox("vcs-peer-")
    owned = [HOST_SESSION, "project-a", "project-b"]
    ptys: list[tuple[subprocess.Popen[str], Path]] = []
    home, sock = sandbox / "home", sandbox / "sock"
    home.mkdir()
    sock.mkdir()
    config = SOURCE_CORE_DIR / "vibecrafted_core" / "config" / "vc-frame"
    env = _cli_frame_env(_strip_identity_env(os.environ.copy()))
    env.update(
        {
            "HOME": str(home),
            "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "VC_FRAME_SOCKET_DIR": str(sock),
            "ZELLIJ_SOCKET_DIR": str(sock),
            "VC_FRAME_CONFIG_DIR": str(config),
            "VC_FRAME_CONFIG_FILE": str(config / "config.kdl"),
        }
    )
    script = _write(home / "pty_attach.py", _PTY_ATTACH_PY)

    def frame(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [binary, *args],
            check=False,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=40,
        )

    def clients(session: str) -> list[str]:
        result = frame("--session", session, "action", "list-clients")
        assert result.returncode == 0, result.stderr
        return [
            line.split()[1]
            for line in result.stdout.splitlines()
            if line.strip() and not line.startswith("CLIENT_ID")
        ]

    def panes(session: str) -> list[dict]:
        result = frame(
            "--session", session, "action", "list-panes", "--json", "--command"
        )
        assert result.returncode == 0, result.stderr
        parsed = _list_panes_payload(result.stdout)
        assert isinstance(parsed, list), result.stdout
        return parsed

    def attach(token: str) -> Path:
        attached, release = home / token, home / (token + ".release")
        proc = subprocess.Popen(
            [
                sys.executable,
                str(script),
                binary,
                "project-a",
                str(attached),
                str(release),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        ptys.append((proc, release))
        assert _wait_until(lambda: attached.exists() and proc.poll() is None, 30), token
        return Path(str(attached) + ".keys")

    def press(path: Path, sequence: bytes) -> None:
        with path.open("ab") as handle:
            handle.write(sequence)

    try:
        created = frame("attach", "--create-background", HOST_SESSION)
        assert created.returncode == 0, created.stderr
        for name in owned[1:]:
            created = frame(
                "--new-session-with-layout",
                str(config / "layouts" / "operator.kdl"),
                "attach",
                "--create-background",
                name,
            )
            assert created.returncode == 0, created.stderr
        # Unique per-session tabs must remain confined to that session.
        for name in owned[1:]:
            created = frame(
                "--session", name, "action", "new-tab", "--name", name + "-only"
            )
            assert created.returncode == 0, created.stderr
        for name, other in (("project-a", "project-b"), ("project-b", "project-a")):
            tabs = frame("--session", name, "action", "list-tabs", "--json")
            assert tabs.returncode == 0, tabs.stderr
            names = {tab["name"] for tab in json.loads(tabs.stdout)}
            assert name + "-only" in names and other + "-only" not in names, names
        first = attach("client-one")
        attach("client-two")
        assert _wait_until(lambda: len(clients("project-a")) == 2)
        original = clients("project-a")
        assert original[0] == original[1], original
        # The exact bytes emitted by the shipped VC Terminal Cmd bindings.
        press(first, b"\x1b[1;9C")
        assert _wait_until(lambda: len(set(clients("project-a"))) == 2), clients(
            "project-a"
        )
        selected = next(p for p in clients("project-a") if p != original[0])
        baseline = {name: panes(name) for name in owned[1:]}
        for rows in baseline.values():
            assert _session_layer_ready(rows), rows
            assert not any(
                "--workspace-projection" in str(p.get("terminal_command", ""))
                for p in rows
            ), rows
        press(first, b"\x1b[1;9D")
        assert _wait_until(lambda: clients("project-a") == original)
        press(first, b"\x1b[1;9C")
        assert _wait_until(lambda: len(set(clients("project-a"))) == 2)
        before_switch = clients("project-a")
        press(first, b"\x1b[1;9B")
        assert _wait_until(
            lambda: len(clients("project-a")) == 1 and len(clients("project-b")) == 1
        )
        remaining = clients("project-a")
        press(first, b"\x1b[1;9A")
        assert _wait_until(
            lambda: len(clients("project-a")) == 2 and not clients("project-b")
        )
        assert sorted(clients("project-a")) == sorted(before_switch), (
            before_switch,
            clients("project-a"),
        )
        assert remaining[0] in before_switch
        assert selected in {
            "terminal_" + str(p["id"])
            for p in panes("project-a")
            if not p.get("is_plugin")
        }
        for name in owned[1:]:
            _assert_session_layer(
                panes(name), previous_geometry=_session_layer_geometry(baseline[name])
            )
        assert all(proc.poll() is None for proc, _ in ptys), (
            "a client died during switching"
        )
    finally:
        for _, release in ptys:
            release.touch()
        for proc, _ in ptys:
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.communicate(timeout=5)
        for name in owned:
            frame("kill-session", name)
            frame("delete-session", name, "--force")
        shutil.rmtree(sandbox, ignore_errors=True)


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("inside", [False, True])
def test_foreign_terminal_start_opens_product_host_not_foreign_frame(
    tmp_path: Path, shell: str, inside: bool
) -> None:
    scene = Scene(tmp_path, live=("vc-host",), clients=("vc-host",))
    env = {"TERM_PROGRAM": "Apple_Terminal"}
    if inside:
        env.update(VC_FRAME="1", VC_FRAME_PANE_ID="2", VC_FRAME_SESSION_NAME="vc-host")
    result = _run(
        scene,
        '_vetcoders_product_entry_prepare() { _vetcoders_product_workspace_prepare "$1"; }; vc-start chosen --repo '
        + shlex.quote(str(scene.root)),
        shell=shell,
        tty=True,
        extra_env=env,
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    launches = scene.terminal_launches(wait=0.5)
    assert len(launches) == 1, result.stdout + result.stderr
    assert launches[0]["argv"] == [
        "--working-directory",
        str(scene.root),
        "-e",
        str(scene.home / ".config/vibecrafted/vc-terminal/launch-primary-shell.zsh"),
        str(scene.generation / "bin/vc-start"),
        "chosen",
        "--repo",
        str(scene.root),
    ]
    assert all(value is None for value in launches[0]["markers"].values())
    assert not _attaches(scene.calls())
    assert not _switches(scene.calls())
    assert scene.live() == ["chosen", "vc-host"]


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_owned_terminal_receipt_start_keeps_direct_entry(
    tmp_path: Path, shell: str
) -> None:
    scene = Scene(tmp_path)
    result = _run(
        scene,
        '_vetcoders_product_entry_prepare() { _vetcoders_product_workspace_prepare "$1"; }; vc-start',
        shell=shell,
        tty=True,
        extra_env={
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VIBECRAFTED_TERMINAL_ENTRY_OWNER": str(
                scene.generation / "bin/vibecrafted"
            ),
        },
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert not scene.terminal_launches(wait=0)
    assert len(_attaches(scene.calls())) == 1


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_inherited_private_terminal_cache_does_not_admit_foreign_shell(
    tmp_path: Path, shell: str
) -> None:
    scene = Scene(tmp_path)
    result = _run(
        scene,
        "_vetcoders_start_is_owned_terminal_child; printf 'ORIGIN=[%s]\\n' \"$?\"",
        shell=shell,
        tty=True,
        extra_env={"_VETCODERS_OWNED_TERMINAL_ENTRY": "1"},
    )
    assert "ORIGIN=[1]" in result.stdout


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_all_export_terminal_receipt_cache_is_same_shell_only(
    tmp_path: Path, shell: str
) -> None:
    scene = Scene(tmp_path)
    invocation = (
        "set -a; _vetcoders_has_owned_vc_terminal_entry; "
        "_vetcoders_has_owned_vc_terminal_entry; printf 'SAME=[%s]\\n' \"$?\"; "
        "env | /usr/bin/grep '^_VETCODERS_OWNED_TERMINAL_ENTRY'; printf 'EXPORTED=[%s]\\n' \"$?\""
    )
    result = _run(
        scene,
        invocation,
        shell=shell,
        extra_env={
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VIBECRAFTED_TERMINAL_ENTRY_OWNER": str(
                scene.generation / "bin/vibecrafted"
            ),
        },
    )
    assert "SAME=[0]" in result.stdout
    assert "EXPORTED=[1]" in result.stdout


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_owned_terminal_receipt_cache_does_not_admit_subshell(
    tmp_path: Path, shell: str
) -> None:
    scene = Scene(tmp_path)
    result = _run(
        scene,
        "_vetcoders_has_owned_vc_terminal_entry; "
        "printf 'PARENT=[%s]\\n' \"$?\"; "
        "( _vetcoders_has_owned_vc_terminal_entry; "
        "printf 'CHILD=[%s]\\n' \"$?\" ); "
        "_vetcoders_has_owned_vc_terminal_entry; "
        "printf 'PARENT_AGAIN=[%s]\\n' \"$?\"",
        shell=shell,
        extra_env={
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VIBECRAFTED_TERMINAL_ENTRY_OWNER": str(
                scene.generation / "bin/vibecrafted"
            ),
        },
    )
    assert "PARENT=[0]" in result.stdout
    assert "CHILD=[1]" in result.stdout
    assert "PARENT_AGAIN=[0]" in result.stdout


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize(
    ("probe_exit", "expected_exit", "expected_calls"),
    [(1, 0, 2), (75, 75, 1), (78, 78, 1)],
)
def test_fixed_host_probe_only_falls_through_when_no_ancestor_exists(
    tmp_path: Path,
    shell: str,
    probe_exit: int,
    expected_exit: int,
    expected_calls: int,
) -> None:
    owner = tmp_path / "generation"
    helper = owner / "vibecrafted-core/vibecrafted_core/terminal_host.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("fixed-host admission fixture\n")
    log = tmp_path / "python-calls.log"
    python = _write(
        tmp_path / "python-wrapper",
        """#!/bin/sh
printf '%s\n' "$*" >> "$PROBE_LOG"
if [ "$1" = "-I" ] && [ "$2" = "-B" ] && [ "$4" = "owns-fixed-host" ]; then
  exit "$PROBE_EXIT"
fi
if [ "$1" = "-" ]; then
  cat >/dev/null
  exit 0
fi
exit 99
""",
    )
    script = "\n".join(
        [
            f'source "{SHELL_SH}"',
            f'_vetcoders_vc_frame_loaded_root="{REPO_ROOT}"',
            f'_vetcoders_vc_frame_owner_root() {{ printf "%s\\n" "{owner}"; }}',
            f'_vetcoders_internal_python() {{ printf "%s\\n" "{python}"; }}',
            "_vetcoders_start_has_owned_terminal_host",
            "rc=$?",
            'printf "RC=[%s]\\n" "$rc"',
            'exit "$rc"',
        ]
    )
    env = os.environ.copy()
    _strip_identity_env(env)
    env.update(
        {
            "PROBE_EXIT": str(probe_exit),
            "PROBE_LOG": str(log),
        }
    )
    result = subprocess.run(
        _shell_argv(shell, script),
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert f"RC=[{expected_exit}]" in result.stdout
    calls = log.read_text().splitlines()
    assert len(calls) == expected_calls
    assert calls[0] == f"-I -B {helper} owns-fixed-host"
    if probe_exit == 1:
        assert calls[1] == f"- {owner}"


def _run_with_owned_terminal_ancestor(
    scene: Scene, invocation: str, *, host: Path | None = None
) -> subprocess.CompletedProcess[str]:
    # A copied native shell stands in for the selected product executable.
    # There is a real PTY and real kernel process ancestry; no GUI/provider.
    host = host or scene.generation / "libexec/vc-terminal"
    host.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        # Apple's copied platform binary otherwise dies before main. This
        # private process fixture gets a local ad-hoc signature, no app build;
        # macOS 15's arm64e-only slice is replaced by one that really executes.
        private_native_copy(
            Path("/bin/bash"), host, ("--noprofile", "--norc", "-c", "echo ready")
        )
    else:
        shutil.copyfile("/bin/bash", host)
        host.chmod(0o755)
    script = _entry_script(scene, invocation)
    child = shlex.join(_shell_argv("bash", script))
    parent = [str(host), "--noprofile", "--norc", "-c", child + "; :"]
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import pty,sys;sys.exit(pty.spawn(" + repr(parent) + "))",
        ],
        check=False,
        cwd=scene.root,
        env=scene.env(),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_physical_owned_terminal_ancestor_keeps_direct_entry(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    result = _run_with_owned_terminal_ancestor(
        scene,
        '_vetcoders_product_entry_prepare() { _vetcoders_product_workspace_prepare "$1"; }; vc-start',
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert not scene.terminal_launches(wait=0)
    assert len(_attaches(scene.calls())) == 1


def test_frame_inherited_owned_host_and_socket_still_requires_handoff(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, live=("vc-host",), clients=("vc-host",))
    import socket

    owned_socket = socket.socket(socket.AF_UNIX)
    short_dir = tempfile.TemporaryDirectory(prefix="vco-")
    socket_path = Path(short_dir.name) / "s"
    owned_socket.bind(str(socket_path))
    try:
        result = _run_with_owned_terminal_ancestor(
            scene,
            "export VC_FRAME=1 VC_FRAME_PANE_ID=2 VC_FRAME_SESSION_NAME=vc-host; "
            + "export VIBECRAFTED_TERMINAL_HOST="
            + shlex.quote(str(scene.generation / "libexec/vc-terminal"))
            + " ALACRITTY_SOCKET="
            + shlex.quote(str(socket_path))
            + "; "
            + '_vetcoders_product_entry_prepare() { _vetcoders_product_workspace_prepare "$1"; }; vc-start',
        )
    finally:
        owned_socket.close()
        short_dir.cleanup()
    assert _rc(result) == 0, result.stdout + result.stderr
    assert len(scene.terminal_launches(wait=0.5)) == 1
    assert not _attaches(scene.calls()) and not _switches(scene.calls())


@pytest.mark.parametrize("contract", [False, True])
def test_foreign_start_reenters_exact_verified_active_generation(
    tmp_path: Path, contract: bool
) -> None:
    scene = Scene(tmp_path)
    active = tmp_path / "releases/active"
    _write(
        active / "bin/vc-start",
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        + f"open({str(tmp_path / 'active.json')!r},'w').write(json.dumps({{'argv':sys.argv[1:],'runtime':os.environ.get('VIBECRAFTED_RUNTIME_ROOT'),'frame':os.environ.get('VIBECRAFTED_VC_FRAME_BIN'),'core':os.environ.get('VIBECRAFTED_CORE_DIR')}}))\n",
    )
    flags = (
        " --base explicit-base --execution-runtime local-worktrees --worktree true"
        if contract
        else ""
    )
    result = _run(
        scene,
        '_vetcoders_product_entry_prepare() { _vetcoders_product_workspace_prepare "$1"; }; '
        + '_vetcoders_start_active_host_root() { printf "%s\\n" '
        + shlex.quote(str(active))
        + "; }; vc-start chosen"
        + flags
        + " --repo "
        + shlex.quote(str(scene.root)),
        tty=True,
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    receipt = json.loads((tmp_path / "active.json").read_text())
    expected = (
        [
            "--base",
            "explicit-base",
            "--execution-runtime",
            "local-worktrees",
            "--worktree",
            "true",
        ]
        if contract
        else []
    )
    assert receipt == {
        "argv": [*expected, "chosen", "--repo", str(scene.root)],
        "runtime": str(active),
        "frame": str(active / "bin/vc-frame"),
        "core": str(active / "vibecrafted-core"),
    }
    assert not scene.calls() and not scene.terminal_launches(wait=0)


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_owned_terminal_cache_does_not_follow_other_generation(
    tmp_path: Path, shell: str
) -> None:
    scene = Scene(tmp_path)
    other = tmp_path / "other"
    _write(other / "bin/vibecrafted", "#!/bin/bash\nexit 0\n")
    result = _run(
        scene,
        "_vetcoders_has_owned_vc_terminal_entry; "
        + "_vetcoders_vc_frame_loaded_root="
        + shlex.quote(str(other))
        + '; _vetcoders_has_owned_vc_terminal_entry; printf "OTHER=[%s]\\n" "$?"',
        shell=shell,
        extra_env={
            "VIBECRAFTED_TERMINAL_ENTRY": "1",
            "VIBECRAFTED_TERMINAL_ENTRY_OWNER": str(
                scene.generation / "bin/vibecrafted"
            ),
        },
    )
    assert "OTHER=[1]" in result.stdout


def test_wrong_generation_app_shaped_host_ancestor_requires_handoff(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path)
    foreign = (
        tmp_path
        / "old/Vibecrafted.app/Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty"
    )
    result = _run_with_owned_terminal_ancestor(
        scene,
        "export VIBECRAFTED_TERMINAL_HOST="
        + shlex.quote(str(foreign))
        + "; "
        + '_vetcoders_product_entry_prepare() { _vetcoders_product_workspace_prepare "$1"; }; vc-start',
        host=foreign,
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert len(scene.terminal_launches(wait=0.5)) == 1
    assert not _attaches(scene.calls())


@pytest.mark.parametrize("entry", ["shell", "deck"])
@pytest.mark.parametrize("resume", [False, True])
def test_foreign_start_handoff_crosses_real_terminal_and_primary_shell(
    tmp_path: Path, entry: str, resume: bool
) -> None:
    scene = Scene(tmp_path, live=("vc-host",), clients=("vc-host",))
    receipt_path = tmp_path / "primary-receipt.json"
    # A valid older ambient bundle must not displace the selected product host.
    # Bind only the copied wrapper; the child fence forbids installed GUI exec.
    import plistlib

    older = tmp_path / "old/Vibecrafted.app/Contents/Helpers/vc-terminal.app"
    older_host = older / "Contents/MacOS/alacritty"
    _write(older_host, "#!/bin/bash\nexit 23\n")
    _write(older / "Contents/Resources/alacritty.icns", "fixture-icon")
    (older / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleExecutable": "alacritty",
                "CFBundleIconFile": "alacritty.icns",
                "CFBundleName": "VC Terminal",
                "CFBundleDisplayName": "VC Terminal",
            }
        )
    )
    _write(
        scene.generation / "bin/vc-terminal",
        (REPO_ROOT / "scripts/vc-terminal-product-entry.sh").read_text(),
    )
    _write(
        scene.home / ".config/vibecrafted/vc-terminal/vc-terminal.toml", "[terminal]\n"
    )
    primary = scene.home / ".config/vibecrafted/vc-terminal/launch-primary-shell.zsh"
    # Keep the actual selector/entry route; end this owned sentinel instead of
    # starting the final interactive shell after the requested entry returns.
    _write(primary, PRIMARY_SHELL.read_text().replace("exec /bin/zsh -d -l", "exit 0"))
    _write(
        scene.generation / "libexec/vc-terminal",
        f"#!{sys.executable}\n"
        "import os,pty,sys\n"
        "args=sys.argv[1:]; boundary=args.index('-e')\n"
        "os.chdir(args[args.index('--working-directory')+1])\n"
        "sys.exit(pty.spawn(['/bin/bash',*args[boundary+1:]]))\n",
    )
    selected_host = bind_private_terminal_wrapper(
        scene.generation / "bin/vc-terminal",
        scene.generation / "libexec/vc-terminal",
    )
    _write(
        scene.generation / "bin/vc-start",
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        + f"with open({str(receipt_path)!r},'w') as f:\n"
        + " f.write(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd(),"
        "'runtime':os.environ.get('VIBECRAFTED_RUNTIME_ROOT'),"
        "'boundary':os.environ.get('VIBECRAFTED_TERMINAL_ENTRY'),"
        "'owner':os.environ.get('VIBECRAFTED_TERMINAL_ENTRY_OWNER'),"
        "'frame':os.environ.get('VC_FRAME_SESSION_NAME')}))\n",
    )
    args = ["resume", "--session", "fixture-session"] if resume else ["chosen"]
    if entry == "deck":
        body = DECK.read_text().split("cmd_start() {", 1)[1].split("\n}\n", 1)[0]
        invocation = (
            "_is_help_flag() { return 1; }; _script_owner_root() { return 0; }; "
            "_ensure_helpers_loaded() { return 0; }; cmd_start() {"
            + body
            + "\n}; cmd_start "
        )
    else:
        invocation = "vc-start "
    result = _run(
        scene,
        invocation + shlex.join([*args, "--repo", str(scene.root)]),
        tty=True,
        fence_children=True,
        allowed_children=(selected_host, Path("/usr/libexec/PlistBuddy")),
        extra_env={
            "VIBECRAFTED_TERMINAL_HOST": str(older_host),
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "2",
            "VC_FRAME_SESSION_NAME": "vc-host",
        },
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    receipt = json.loads(receipt_path.read_text())
    assert receipt == {
        "argv": [*args, "--repo", str(scene.root)],
        "cwd": str(scene.root),
        "runtime": str(scene.generation),
        "boundary": "1",
        "owner": str(scene.generation / "bin/vibecrafted"),
        "frame": None,
    }
    assert not _attaches(scene.calls()) and not _switches(scene.calls())


def test_ssh_start_fresh_workspace_attaches_in_caller_tty_without_terminal_gui(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        tty=True,
        extra_env={
            "SSH_CONNECTION": "10.0.0.1 50000 10.0.0.2 22",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
        },
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert scene.workspaces() == ["mlx-batch-runner"]
    attaches = _attaches(scene.calls())
    assert len(attaches) == 1, scene.calls()
    assert attaches[0]["attached"] == "mlx-batch-runner"
    assert scene.terminal_launches(wait=0) == []
    assert not _switches(scene.calls())


def test_ssh_start_existing_live_workspace_attaches_without_second_create(
    tmp_path: Path,
) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("mlx-batch-runner",),
        clients=("mlx-batch-runner",),
    )
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        tty=True,
        extra_env={
            "SSH_CLIENT": "10.0.0.1 50000 22",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
        },
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert not _creates(scene.calls())
    attaches = _attaches(scene.calls())
    assert len(attaches) == 1, scene.calls()
    assert attaches[0]["attached"] == "mlx-batch-runner"
    assert scene.terminal_launches(wait=0) == []
    assert not _switches(scene.calls())


def test_ssh_start_repeated_invocation_reuses_session_without_second_create(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    first = _run(
        scene,
        "vc-start",
        developer_root=True,
        tty=True,
        extra_env={
            "SSH_CONNECTION": "10.0.0.1 50000 10.0.0.2 22",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
        },
    )
    assert _rc(first) == 0, first.stdout + first.stderr
    initial_creates = len(_creates(scene.calls()))
    assert initial_creates > 0
    assert len(_attaches(scene.calls())) == 1
    assert scene.terminal_launches(wait=0) == []

    second = _run(
        scene,
        "vc-start",
        developer_root=True,
        tty=True,
        extra_env={
            "SSH_CONNECTION": "10.0.0.1 50000 10.0.0.2 22",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
        },
    )
    assert _rc(second) == 0, second.stdout + second.stderr
    assert len(_creates(scene.calls())) == initial_creates
    assert len(_attaches(scene.calls())) == 2
    assert scene.terminal_launches(wait=0) == []


def test_ssh_start_remote_in_frame_switches_client_without_terminal(
    tmp_path: Path,
) -> None:
    scene = Scene(
        tmp_path,
        project="mlx-batch-runner",
        live=("mlx-batch-runner", "other-place"),
        clients=("mlx-batch-runner", "other-place"),
    )
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        tty=True,
        extra_env={
            "SSH_CONNECTION": "10.0.0.1 50000 10.0.0.2 22",
            "VC_FRAME": "1",
            "VC_FRAME_SESSION_NAME": "other-place",
            "VC_FRAME_PANE_ID": "3",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
        },
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert [
        call["switched"] for call in _switches(scene.calls()) if call.get("switched")
    ] == [["other-place", "mlx-batch-runner"]]
    assert scene.terminal_launches(wait=0) == []
    assert not _attaches(scene.calls())


def test_ssh_start_without_tty_refuses_without_gui_fallback(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        tty=False,
        extra_env={
            "SSH_CONNECTION": "10.0.0.1 50000 10.0.0.2 22",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
        },
    )
    assert _rc(result) != 0, result.stdout + result.stderr
    assert "terminal (TTY)" in (result.stdout + result.stderr) or "TTY" in (
        result.stdout + result.stderr
    )
    assert scene.terminal_launches(wait=0) == []
    assert not _attaches(scene.calls())


def test_local_external_tty_still_escalates_to_company_terminal(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, project="mlx-batch-runner")
    result = _run(
        scene,
        "vc-start",
        tty=True,
        owned_terminal=False,
        # The local control must stay local even when pytest itself runs over
        # SSH: an inherited SSH_* marker would select the caller-TTY route.
        extra_env={"SSH_CONNECTION": "", "SSH_CLIENT": "", "SSH_TTY": ""},
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert len(scene.terminal_launches(wait=0.5)) == 1
    assert not _attaches(scene.calls())


def test_ssh_start_source_pane_retains_canonical_owned_python_pin(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, project="source-project", git=True)
    for relative in ("bin/vc-frame", "libexec/vc-frame"):
        frame = scene.generation / relative
        body = frame.read_text()
        frame.write_text(f"#!{sys.executable}\n" + body.split("\n", 1)[1])
    result = _run(
        scene,
        "vc-start",
        developer_root=True,
        tty=True,
        extra_env={
            "SSH_CONNECTION": "10.0.0.1 50000 10.0.0.2 22",
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
        },
        fence_children=True,
        allowed_children=(
            scene.generation / "bin/vc-frame",
            SOURCE_CORE_DIR / "vibecrafted_core/config/vc-frame/pane-python",
        ),
    )
    assert _rc(result) == 0, result.stderr
    created = _creates(scene.calls())
    assert created
    pin = created[0].get("python_pin")
    assert pin and Path(pin).is_absolute() and os.access(pin, os.X_OK)
    launcher = _write(
        tmp_path / "pane-launcher.py",
        f"#!{sys.executable}\n"
        "import json, sys, vibecrafted_core\n"
        "print(json.dumps({'argv': sys.argv[1:], 'core': vibecrafted_core.__file__}))\n",
    )
    command = [
        "bash",
        str(SOURCE_CORE_DIR / "vibecrafted_core/config/vc-frame/pane-python"),
        str(launcher),
        "launcher",
    ]
    command = private_child_command(
        command, allowed=(Path(pin), launcher), fixture_root=tmp_path
    )
    pane = subprocess.run(
        command,
        env=scene.env(
            {"VIBECRAFTED_PYTHON": pin, "VIBECRAFTED_CORE_DIR": created[0]["core_dir"]}
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert pane.returncode == 0, pane.stderr
    payload = json.loads(pane.stdout.strip())
    assert payload["argv"] == ["launcher"]
    assert Path(payload["core"]).resolve().is_relative_to(SOURCE_CORE_DIR)
