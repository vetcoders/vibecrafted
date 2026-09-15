#!/usr/bin/env python3
"""Agent Workspaces dashboard and interactive Agent launcher.

This is deliberately a terminal surface, not a second control plane.  vc-frame
owns the panes and tabs, Vibecrafted owns the launch command, and the User
chooses which project's live Frame session receives a new Agent tab.
"""

from __future__ import annotations

import argparse
import curses
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple


def _generation_python_candidates() -> list[str]:
    """Interpreters that can import vibecrafted_core without host PYTHONPATH.

    vc-frame ``bash -lc`` panes do not inherit the deck wrapper.  Match
    ``spawn_python_bin`` in runtime/scripts/lib/util.sh: env override, then the
    uv tool venv, then generation ``bin/python3``.  Never fall through to
    Homebrew ``python3`` — that is the 3.14 ModuleNotFoundError class.
    """
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share"))
    ordered: list[str] = []
    wanted = os.environ.get("VIBECRAFTED_PYTHON", "").strip()
    if wanted:
        ordered.append(wanted)
    for key in ("VIBECRAFTED_RUNTIME_ROOT", "VIBECRAFTED_ROOT"):
        root = os.environ.get(key, "").strip()
        if root:
            ordered.append(str(Path(root) / "bin" / "python3"))
    ordered.extend(
        (
            str(data / "uv" / "tools" / "vibecrafted" / "bin" / "python3"),
            str(data / "uv" / "tools" / "vibecrafted" / "bin" / "python"),
            str(data / "uv" / "tools" / "vibecrafted-core" / "bin" / "python3"),
            str(
                data
                / "vibecrafted"
                / "tools"
                / "vibecrafted-current"
                / "bin"
                / "python3"
            ),
        )
    )
    seen: set[str] = set()
    unique: list[str] = []
    for item in ordered:
        if not item or item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def ensure_generation_python() -> None:
    """Re-exec a generation/uv interpreter when host python3 lacks core.

    vc-frame panes often run ``#!/usr/bin/env python3`` (Homebrew 3.14 on this
    host).  Generation ``bin/python3`` is a wrapper that sets PYTHONPATH onto
    the receipted ``vibecrafted-core``.  Source-lane tools installs have no
    ``bin/python3``; the uv tool venv does.
    """
    try:
        import vibecrafted_core  # noqa: F401
    except ImportError:
        pass
    else:
        return
    here = os.path.realpath(sys.executable)
    for wanted in _generation_python_candidates():
        if not os.access(wanted, os.X_OK):
            continue
        if os.path.realpath(wanted) == here:
            continue
        os.execv(wanted, [wanted, *sys.argv])
    raise SystemExit(
        "vc-agent-workshop: no module named 'vibecrafted_core'; "
        "set VIBECRAFTED_PYTHON to the generation python3 "
        "(or run through vc-start so the Runtime Pack is on PATH)"
    )


# Self-consistency: when this script runs from inside a core source tree
# (worktree or installed generation), the core must come from the SAME tree.
# Ambient PYTHONPATH can otherwise resolve an older/newer installed generation
# while the launcher UI is this tree's — the "unsupported provider" crash class.
# Materialized frame-config copies fail the guard and keep the re-exec path.
_SCRIPT_CORE_TREE = Path(__file__).resolve().parents[3]
if (_SCRIPT_CORE_TREE / "vibecrafted_core" / "__init__.py").is_file():
    sys.path.insert(0, str(_SCRIPT_CORE_TREE))

ensure_generation_python()

from vibecrafted_core.aicx_session_chain import (
    CliSessionChain,
    SessionChain,
    SessionChainError,
    SessionRecord,
    project_filter_for_root,
)
from vibecrafted_core.spawn import (
    CONTINUITY_MODES,
    OPERATOR_POLICIES,
    PERMISSION_POLICIES,
    RUNTIME_POLICIES,
    continuity_policy_capabilities,
    resolve_operator_agent_policy,
    resolve_provider_policy,
    runtime_policy_capabilities,
)
from vibecrafted_core.workspace_catalog import resolve_operator_place_session

AGENTS = ("agy", "claude", "codex", "cursor", "grok", "junie", "kimi")
LAUNCH_MODES = ("init", "resume", "partner", "operator")
MODE_PROMPTS = {"partner": "/vc-partner", "operator": "/vc-operator"}
RUNTIME_HELP = {
    "local-native": ("This checkout, shared with you.", ""),
    # The worktree lane is admitted only on a verified live usage source
    # (runtime_policy_capabilities); the plain second line keeps that gate
    # visible instead of presenting the lane as unconditionally available.
    "local-worktrees": (
        "A separate working copy for this Agent.",
        "Only when this provider's live usage can be verified.",
    ),
    "local-vm": ("Not available yet.", ""),
    "cloud-soon": ("Not available yet.", ""),
}
_WORKSHOP_TITLES = frozenset({"agent workspaces", "new agent", "voc", "start here"})
_AGENT_BINARIES = {
    "agy": "agy",
    "gemini": "agy",
    "claude": "claude",
    "claude-code": "claude",
    "codex": "codex",
    "cursor": "cursor",
    "cursor-agent": "cursor",
    "grok": "grok",
    "junie": "junie",
    "kimi": "kimi",
}
PRESENCE_SCOPE = "this session"
PRESENCE_REFRESH_SECONDS = 15.0
PRESENCE_MAX_REFRESH_SECONDS = 60.0
PRESENCE_ON_DEMAND_FLOOR_SECONDS = 2.0


def launch_argv(
    agent: str,
    mode: str,
    runtime: str = "local-native",
    permissions: str = "bypass",
    operator: str = "none",
    continuity: str = "fresh",
    continuity_parent: str = "",
    workspace: str | os.PathLike[str] = "",
) -> list[str]:
    """Return the one canonical interactive command for a launcher choice."""
    if agent not in AGENTS:
        raise ValueError(f"unsupported agent: {agent}")
    if mode not in LAUNCH_MODES:
        raise ValueError(f"unsupported interactive mode: {mode}")
    if continuity not in CONTINUITY_MODES:
        raise ValueError(f"unsupported continuity policy: {continuity}")
    root = str(Path(workspace).expanduser().resolve()) if workspace else ""
    if mode != "resume":
        decision = resolve_provider_policy(agent, runtime, permissions, "interactive")
        if not decision.supported:
            raise ValueError(decision.reason)
        if operator not in OPERATOR_POLICIES:
            raise ValueError(f"unsupported Operator Agent policy: {operator}")
        operator_decision = resolve_operator_agent_policy(operator, runtime=runtime)
        if not operator_decision.supported:
            raise ValueError(operator_decision.reason)
        # `init` defaults to opening another vc-frame tab.  The workshop's law
        # is stricter: the destination pane this launcher opens becomes the
        # Agent TTY.  `--runtime plain` is that pane, not a nested composer.
        command = [
            "vibecrafted",
            "init",
            agent,
            "--runtime",
            "plain",
            "--policy-runtime",
            runtime,
            "--permissions",
            permissions,
            "--operator",
            operator_decision.selection,
            "--continuity",
            continuity,
        ]
        if root:
            command.extend(["--root", root])
        if continuity == "bare-fork":
            if not continuity_parent:
                raise ValueError(
                    "bare-fork requires an explicit parent provider-session id"
                )
            command.extend(["--parent-session", continuity_parent])
        elif continuity == "full-lineage" and continuity_parent:
            command.extend(["--continuity-parent", continuity_parent])
        if mode in MODE_PROMPTS:
            command.extend(["--prompt", MODE_PROMPTS[mode]])
        return command
    if runtime != "local-native":
        raise ValueError(
            "worktree resume supervision belongs to H2b2 and is not configured yet"
        )
    command = ["vibecrafted", "resume", agent]
    if root:
        command.extend(["--root", root])
    return command


def current_frame_session(*, env: Mapping[str, str] | None = None) -> str:
    """Name of the Frame session hosting this pane, if the runtime exported one."""
    environ = env if env is not None else os.environ
    for key in (
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ_SESSION_NAME",
        "VIBECRAFTED_FRAME_SESSION",
    ):
        value = str(environ.get(key) or "").strip()
        if value:
            return value
    return ""


def destination_session_for_workspace(
    workspace: str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Canonical human place-session for the selected project checkout.

    This is the workspace catalog place-session, not a worker host and not
    the current Frame seat. An empty result is an error, never a cue to
    omit ``--session``.
    """
    name = str(resolve_operator_place_session(root=workspace, env=env) or "").strip()
    if not name:
        raise ValueError("could not resolve a Frame session for that project")
    return name


def require_live_destination(session: str, live_names: list[str]) -> None:
    """Refuse to launch when the destination session is not live.

    Missing or wrong context must not fall back to the current session.
    """
    dest = str(session or "").strip()
    if not dest:
        raise ValueError("could not resolve a Frame session for that project")
    if dest not in live_names:
        raise ValueError(
            f"No live Frame session for that project (expected {dest!r}). "
            "Open the project first."
        )


def launch_pane_argv(
    title: str,
    workspace: str | os.PathLike[str],
    command: list[str],
    *,
    session: str,
) -> list[str]:
    """Open a new tab in the selected project's live Frame session.

    cwd does not route the workspace. ``--session`` is required so a launch
    from project A into project B cannot land beside the source workshop.
    """
    dest = str(session or "").strip()
    if not dest:
        raise ValueError("destination Frame session is missing")
    return [
        "vc-frame",
        "--session",
        dest,
        "action",
        "new-tab",
        "--name",
        title,
        "--cwd",
        str(workspace),
        "--",
        *command,
    ]


def attach_session_argv(session: str) -> list[str]:
    """Switch the attached client to the destination session's rail."""
    dest = str(session or "").strip()
    if not dest:
        raise ValueError("destination Frame session is missing")
    return ["vc-frame", "attach", dest]


def mode_capabilities(
    agent: str, runtime: str, permissions: str
) -> dict[str, dict[str, Any]]:
    """Describe every launch mode without hiding unsupported combinations."""
    decision = resolve_provider_policy(agent, runtime, permissions, "interactive")
    resume_available = runtime == "local-native"
    return {
        "init": {"available": decision.supported, "reason": decision.reason},
        "resume": {
            "available": resume_available,
            "reason": (
                ""
                if resume_available
                else "resume is supported only in local-native runtime"
            ),
        },
        "partner": {"available": decision.supported, "reason": decision.reason},
        "operator": {"available": decision.supported, "reason": decision.reason},
    }


def parent_session_choices(
    agent: str,
    root: str | os.PathLike[str],
    *,
    chain: SessionChain | None = None,
) -> tuple[list[SessionRecord], str]:
    """Read parent choices from the canonical AICX session catalog."""
    root_path = Path(root).expanduser().resolve()
    # Parent choices are an exact-project answer. Without a canonical
    # owner/repo there is nothing to ask the catalog; a basename guess would
    # offer sessions from any same-named repository.
    project = project_filter_for_root(root_path)
    if not project:
        return [], (
            f"no canonical owner/repo for {root_path.name}; "
            "parent sessions need a git origin"
        )
    if chain is None:
        aicx = shutil.which("aicx")
        if not aicx:
            return [], "aicx executable not found; parent sessions unavailable"
        chain = CliSessionChain(aicx)
    try:
        result = chain.list_sessions(
            project=project,
            root=root_path,
            agent=agent,
            hours=24 * 30,
            limit=40,
        )
    except SessionChainError as exc:
        return [], f"session catalog {exc.kind}: {exc.message}"
    except OSError as exc:
        return [], f"session catalog unavailable: {exc}"
    sessions = sorted(result.sessions, key=lambda item: item.updated_at, reverse=True)
    if sessions:
        return sessions, ""
    reason = next(
        (warning for warning in result.warnings if warning),
        f"no {agent} sessions for {root_path.name}",
    )
    return [], reason


def normalized_workspace(raw: str, *, base: Path | None = None) -> Path:
    """Resolve and validate the full workspace path entered by the User."""
    root = (base or Path.cwd()).expanduser()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if not candidate.is_dir():
        raise ValueError(f"workspace does not exist: {candidate}")
    return candidate


def public_reason(reason: str) -> str:
    """Map internal policy text to a short User-facing sentence."""
    text = reason.strip()
    if not text:
        return ""
    low = text.casefold()
    # Parent-session and project reasons are not provider policy; keep them
    # out of the provider buckets below (an origin-less checkout is not an
    # uninstalled provider).
    if "owner/repo" in low or "git origin" in low:
        return "Parent sessions need a git origin"
    if "session catalog" in low or "aicx" in low:
        return "Parent sessions are unavailable right now"
    if "workspace does not exist" in low:
        return "That project folder does not exist"
    # Host VM unavailability is not a provider limit.  The token "canonical"
    # in "no canonical VM entrypoint" used to fall through to the provider
    # bucket and lie; keep this branch ahead of that gate.
    if "no canonical vm" in low or "docker/colima" in low:
        return "VM runtime is not available on this host"
    if any(
        token in low
        for token in (
            "child-usage",
            "child-attributable",
            "usage side channel",
            "monotonic usage",
        )
    ):
        if "coming" in low or "h2b" in low:
            return "Not available yet"
        return "Needs live usage metering; only claude today"
    if any(token in low for token in ("canonical", "admission")):
        if "coming" in low or "h2b" in low:
            return "Not available yet"
        return "Not available for this provider"
    if "executable not found" in low:
        return "This provider is not installed"
    if "coming soon" in low or "coming in" in low or "h2b" in low:
        return "Not available yet"
    if "resume is supported only" in low:
        return "Resume works only in this checkout"
    if "expert-only" in low:
        return "Needs a parent session"
    if "no inherited memory" in low:
        return "Starts without earlier memory"
    if "git/dispatch manage_worktrees" in low:
        return "Separate working copies are not available here"
    if "no live frame session" in low:
        return (
            text
            if len(text) <= 96
            else ("No live Frame session for that project. Open it first.")
        )
    if "could not resolve a frame session" in low:
        return "Could not resolve a Frame session for that project"
    if "destination frame session is missing" in low:
        return "Could not resolve a Frame session for that project"
    if len(text) > 72:
        return "Not available"
    return text


def _pane_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("panes", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


class AgentFace(NamedTuple):
    """One discoverable interactive Agent in the declared Frame session."""

    label: str
    tab: str
    liveness: str


class AgentPresence(NamedTuple):
    """Pane-state projection. Unknown is never counted live. Status is honest."""

    active: tuple[str, ...]
    unknown: tuple[str, ...]
    status: str = "ok"
    scope: str = PRESENCE_SCOPE
    faces: tuple[AgentFace, ...] = ()


def _pane_liveness(pane: dict[str, Any]) -> str:
    # vc-frame's public list-panes schema explicitly carries `exited` even
    # when it does not serialize a separate lifecycle/state field.  Terminal
    # exit evidence is authoritative if fields conflict: `exited: false` then
    # describes neither an open pane nor a live provider child.  A false value
    # without terminal evidence is only pane truth, not a provider-liveness
    # claim.
    if pane.get("exit_status") is not None or pane.get("exited") is True:
        return "inactive"
    if pane.get("exited") is False:
        return "active"
    state = (
        str(pane.get("state") or pane.get("lifecycle") or pane.get("status") or "")
        .strip()
        .casefold()
    )
    if state in {"running", "active", "alive", "connected"}:
        return "active"
    if state in {"exited", "closed", "dead", "terminated", "failed"}:
        return "inactive"
    return "unknown"


def _agent_from_command(command: str) -> str:
    tokens = command.split()
    joined = " ".join(tokens).casefold()
    if "vc-agent-workshop" in joined:
        return ""
    for agent in AGENTS:
        if (
            f"vibecrafted init {agent}" in joined
            or f"vibecrafted resume {agent}" in joined
        ):
            return agent
    if not tokens:
        return ""
    binary = Path(tokens[0]).name.casefold()
    return _AGENT_BINARIES.get(binary, "")


def _agent_face(pane: dict[str, Any]) -> AgentFace | None:
    if pane.get("is_plugin"):
        return None
    title = str(
        pane.get("pane_title") or pane.get("title") or pane.get("name") or ""
    ).strip()
    if title.casefold() in _WORKSHOP_TITLES:
        return None
    command = str(pane.get("command") or pane.get("pane_command") or "")
    tab = str(pane.get("tab_name") or pane.get("tab") or "").strip()
    label = ""
    if " · " in title:
        identity = title.split(" · ", 1)[0].casefold()
        if identity in AGENTS:
            label = title
    if not label:
        agent = _agent_from_command(command)
        if not agent:
            return None
        label = title or (f"{agent} · {tab}" if tab else agent)
    liveness = _pane_liveness(pane)
    if liveness == "inactive":
        return None
    if liveness == "unknown" and _agent_from_command(command):
        # An Agent command in an open pane is session-scope presence. It is
        # not a provider-health claim; missing `exited` must not hide it.
        liveness = "active"
    return AgentFace(label=label, tab=tab, liveness=liveness)


def agent_presence_from_payload(payload: Any) -> AgentPresence:
    """Project live interactive Agents in the current Frame session.

    Sidebar plugin labels are not liveness.  Titles without a provider
    contract or Agent command are not counted.  Every tab in this session
    is in scope — not only the Agents tab.
    """
    faces: list[AgentFace] = []
    for pane in _pane_rows(payload):
        face = _agent_face(pane)
        # Two open panes with one title are two Agents: count panes, never
        # collapse them by label.
        if face is not None:
            faces.append(face)
    active = tuple(face.label for face in faces if face.liveness == "active")
    unknown = tuple(face.label for face in faces if face.liveness == "unknown")
    return AgentPresence(
        active=active,
        unknown=unknown,
        status="ok",
        scope=PRESENCE_SCOPE,
        faces=tuple(faces),
    )


def agent_faces_from_payload(payload: Any) -> list[str]:
    """Active-agent labels for the dashboard list."""
    return list(agent_presence_from_payload(payload).active)


def presence_headline(
    status: str,
    count: int,
    *,
    stale: bool = False,
    scope: str = PRESENCE_SCOPE,
) -> str:
    if status == "unavailable" and count == 0 and not stale:
        return f"Agents in {scope} — unavailable"
    marker = ", stale" if stale else ""
    return f"Agents in {scope} ({count}{marker})"


def current_agent_presence() -> AgentPresence:
    try:
        result = subprocess.run(
            [
                "vc-frame",
                "action",
                "list-panes",
                "--json",
                "--state",
                "--tab",
                "--command",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if result.returncode != 0:
            return AgentPresence((), (), status="unavailable")
        return agent_presence_from_payload(json.loads(result.stdout))
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return AgentPresence((), (), status="unavailable")


def session_names_from_listing(text: str) -> list[str]:
    """Parse `vc-frame list-sessions --no-formatting` into live session names."""
    names: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        if "EXITED" in raw.upper():
            continue
        name = raw.split(" [", 1)[0].strip()
        name = name.split(" (", 1)[0].strip()
        if name and name not in names:
            names.append(name)
    return names


def list_live_frame_sessions() -> tuple[list[str], str]:
    """Live Frame session names. Destination routing never guesses this list."""
    try:
        result = subprocess.run(
            ["vc-frame", "list-sessions", "--no-formatting"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.5,
        )
    except FileNotFoundError:
        return [], "vc-frame is not available in this Runtime Pack"
    except subprocess.TimeoutExpired:
        return [], "Frame sessions are unavailable"
    if result.returncode != 0:
        return [], (
            result.stderr or result.stdout or "Frame sessions are unavailable"
        ).strip()
    return session_names_from_listing(result.stdout), ""


def list_other_sessions() -> tuple[list[str], str]:
    """On-demand Frame session names. Never polled from the redraw loop."""
    names, error = list_live_frame_sessions()
    if error:
        if error == "Frame sessions are unavailable":
            return [], "other sessions — unavailable"
        return [], error
    current = current_frame_session()
    if current:
        names = [name for name in names if name != current]
    return names, ""


class PresenceSchedule:
    """Decide when the dashboard may spawn a pane-state probe.

    Each probe is a short-lived `vc-frame action` CLI client.  Each attach
    makes the server broadcast Tab/Pane/Session updates to every plugin, so
    a 2 s poll flickered Plugin Manager, rail and bars.  The 500 ms redraw
    spawns nothing; probes run on first draw, on User input (2 s floor),
    and otherwise every 15 s doubling to 60 s while the answer is unchanged.
    """

    def __init__(
        self,
        *,
        base: float = PRESENCE_REFRESH_SECONDS,
        ceiling: float = PRESENCE_MAX_REFRESH_SECONDS,
        floor: float = PRESENCE_ON_DEMAND_FLOOR_SECONDS,
    ) -> None:
        self.base = base
        self.ceiling = ceiling
        self.floor = floor
        self.interval = base
        self.last_at: float | None = None
        self.requested = False

    def request(self) -> None:
        """Ask for a probe on the next draw that clears the on-demand floor."""
        self.requested = True

    def due(self, now: float) -> bool:
        if self.last_at is None:
            return True
        elapsed = now - self.last_at
        return elapsed >= (self.floor if self.requested else self.interval)

    def record(self, now: float, *, changed: bool) -> None:
        self.last_at = now
        self.requested = False
        self.interval = self.base if changed else min(self.ceiling, self.interval * 2)


# Curses pair 0 is COLOR_BLACK. Signed palettes put purple-navy `#26233a` in
# `colors.normal.black`, which is not dark paper (`#0b0b12`) and becomes a dark
# rectangle on light paper (`#fafafa`). Pair 1 at default/default follows the
# vc-frame / alacritty theme in both modes.
_PAPER_PAIR = 1
_PAPER = 0


def bind_terminal_paper(window: curses.window) -> int:
    """Paint with the host terminal paper; never ANSI black."""
    global _PAPER
    curses.use_default_colors()
    curses.init_pair(_PAPER_PAIR, -1, -1)
    _PAPER = curses.color_pair(_PAPER_PAIR)
    window.bkgd(" ", _PAPER)
    window.bkgdset(" ", _PAPER)
    return _PAPER


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def _safe_addstr(
    window: curses.window, row: int, col: int, text: str, attr: int = 0
) -> None:
    height, width = window.getmaxyx()
    if row < 0 or row >= height or col < 0 or col >= width:
        return
    try:
        window.addstr(row, col, _clip(text, width - col), attr | _PAPER)
    except curses.error:
        pass


def _dim_unavailable_choices(
    window: curses.window,
    row: int,
    col: int,
    choices: tuple[str, ...],
    available: tuple[bool, ...],
    selected: int,
    end_col: int,
) -> None:
    """Paint unavailable tokens dim and the selected token in reverse video."""
    tokens = _choice_tokens(choices, selected=selected, available=available)
    # The base line clips the *whole* token sequence.  Slice that same rendered
    # sequence before applying token attributes so a trailing disabled token
    # cannot overwrite its ellipsis or drift relative to the selected token.
    visible = _clip(" ".join(tokens), end_col - col)
    offset = 0
    for index, (token, enabled) in enumerate(zip(tokens, available, strict=True)):
        fragment = visible[offset : offset + len(token)]
        if not fragment:
            break
        if not enabled:
            _safe_addstr(window, row, col + offset, fragment, curses.A_DIM)
        elif index == selected:
            _safe_addstr(
                window, row, col + offset, fragment, curses.A_REVERSE | curses.A_BOLD
            )
        offset += len(token) + 1


def _choice_tokens(
    choices: tuple[str, ...],
    *,
    selected: int,
    available: tuple[bool, ...] | None = None,
) -> tuple[str, ...]:
    """Bare labels. Selection is reverse video; unavailable is dim."""
    _ = (selected, available)
    return tuple(choices)


def _provider_available(agent: str) -> bool:
    capabilities = runtime_policy_capabilities(agent)
    return any(bool(capabilities[name]["available"]) for name in RUNTIME_POLICIES)


class Workshop:
    def __init__(self, window: curses.window, *, mode: str) -> None:
        self.window = window
        self.mode = mode
        self.standalone_launcher = mode == "launcher"
        self.home_choice = 0
        self.row = 0
        self.agent = 2  # codex is the least surprising neutral default here
        self.launch_mode = 0
        self.runtime = 1  # separate working copy when the provider supports it
        self.permissions = 0
        self.continuity = 0
        self.continuity_parent = ""
        self.parent_sessions: list[SessionRecord] = []
        self.parent_index = -1
        self.parent_error = ""
        self.path = str(Path.cwd())
        self.error = ""
        self.mouse_targets: list[tuple[int, int, int, int, str]] = []
        self.presence_schedule = PresenceSchedule()
        self.faces: list[str] = []
        self.unknown_faces: list[str] = []
        self.face_records: tuple[AgentFace, ...] = ()
        self.presence_status = "ok"
        self.presence_stale = False
        self.advanced = False
        self.other_sessions: list[str] = []
        self.other_error = ""
        self.show_other = False

    def configure(self) -> None:
        try:
            bind_terminal_paper(self.window)
        except curses.error:
            pass
        curses.curs_set(0)
        curses.noecho()
        curses.cbreak()
        self.window.keypad(True)
        self.window.timeout(500)
        self._normalize_runtime_choice()
        self._normalize_permission_choice()
        self._normalize_mode_choice()
        self._normalize_continuity_choice()
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except curses.error:
            pass

    def run(self) -> None:
        self.configure()
        while True:
            self.draw()
            key = self.window.getch()
            if key == -1:
                continue
            if key == curses.KEY_MOUSE:
                self.handle_mouse()
                continue
            self.presence_schedule.request()
            if key == curses.KEY_RESIZE:
                continue
            if self.mode == "home":
                self.handle_home_key(key)
            else:
                self.handle_launcher_key(key)

    def draw(self) -> None:
        self.window.erase()
        self.mouse_targets.clear()
        if self.mode == "home":
            self.draw_home()
        else:
            self.draw_launcher()
        self.window.refresh()

    def _refresh_presence(self) -> None:
        now = time.monotonic()
        if not self.presence_schedule.due(now):
            return
        presence = current_agent_presence()
        if presence.status == "unavailable":
            if self.faces or self.unknown_faces:
                self.presence_stale = True
            else:
                self.presence_status = "unavailable"
            self.presence_schedule.record(now, changed=False)
            return
        faces = list(presence.active)
        unknown = list(presence.unknown)
        changed = self.presence_schedule.last_at is None or (
            faces,
            unknown,
            presence.status,
        ) != (self.faces, self.unknown_faces, self.presence_status)
        self.faces = faces
        self.unknown_faces = unknown
        # Home rows index `self.faces` (active only); keep the tab records
        # aligned with them so a click opens the tab of the row it hit.
        self.face_records = tuple(
            face for face in presence.faces if face.liveness == "active"
        )
        self.presence_status = "ok"
        self.presence_stale = False
        self.presence_schedule.record(now, changed=changed)

    def draw_home(self) -> None:
        height, width = self.window.getmaxyx()
        compact = height < 14 or width < 48
        left = 1 if compact else max(2, (width - min(width - 4, 78)) // 2)
        top = 0 if compact else max(1, min(4, (height - 16) // 2))
        self._refresh_presence()
        _safe_addstr(self.window, top, left, "Agents", curses.A_BOLD)
        workspace_name = Path(self.path).name or self.path
        if not compact:
            _safe_addstr(
                self.window,
                top + 1,
                left,
                f"{workspace_name} · {PRESENCE_SCOPE}",
                curses.A_DIM,
            )
        headline = presence_headline(
            self.presence_status,
            len(self.faces),
            stale=self.presence_stale,
            scope=PRESENCE_SCOPE,
        )
        headline_row = top + (1 if compact else 3)
        _safe_addstr(self.window, headline_row, left, headline, curses.A_BOLD)
        list_row = headline_row + 1
        if self.presence_status == "unavailable" and not self.faces:
            _safe_addstr(
                self.window,
                list_row,
                left,
                "Pane list is unavailable — not zero.",
                curses.A_DIM,
            )
            list_row += 1
        elif self.faces:
            budget = max(1, height - list_row - (4 if compact else 6))
            for offset, face in enumerate(self.faces[:budget]):
                tab = ""
                if offset < len(self.face_records):
                    tab = self.face_records[offset].tab
                suffix = f"  [{tab}]" if tab and tab.casefold() != "agents" else ""
                line = f"  {face}{suffix}"
                _safe_addstr(self.window, list_row + offset, left, line)
                self.mouse_targets.append(
                    (
                        list_row + offset,
                        left,
                        left + min(len(line), max(1, width - left)),
                        offset,
                        "face",
                    )
                )
            list_row += min(len(self.faces), budget)
        else:
            _safe_addstr(
                self.window,
                list_row,
                left,
                "No live Agents in this session yet.",
                curses.A_DIM,
            )
            list_row += 1
        if self.unknown_faces and not compact:
            _safe_addstr(
                self.window,
                list_row,
                left,
                f"Unverified ({len(self.unknown_faces)}) — titles only, not counted live",
                curses.A_DIM,
            )
            list_row += 1
        buttons = ("New agent", "voc")
        col = left
        button_row = min(height - 3, list_row + (0 if compact else 1))
        for index, label in enumerate(buttons):
            text = f"[ {label} ]"
            attr = curses.A_REVERSE if index == self.home_choice else curses.A_BOLD
            _safe_addstr(self.window, button_row, col, text, attr)
            self.mouse_targets.append((button_row, col, col + len(text), index, "home"))
            col += len(text) + 2
        if self.show_other:
            other_row = min(height - 2, button_row + 1)
            if self.other_error:
                _safe_addstr(
                    self.window, other_row, left, self.other_error, curses.A_DIM
                )
            elif self.other_sessions:
                _safe_addstr(
                    self.window,
                    other_row,
                    left,
                    "Other sessions (Enter opens): "
                    + ", ".join(self.other_sessions[:4]),
                    curses.A_DIM,
                )
            else:
                _safe_addstr(
                    self.window,
                    other_row,
                    left,
                    "No other Frame sessions listed.",
                    curses.A_DIM,
                )
        hint = (
            "n New  v voc  o other sessions"
            if compact
            else "n New agent · v voc · o other sessions · click a row to open its tab"
        )
        _safe_addstr(self.window, height - 2, left, hint, curses.A_DIM)
        if self.error:
            _safe_addstr(self.window, height - 1, left, self.error, curses.A_BOLD)

    def _draw_providers(self, row: int, col: int, width: int) -> None:
        x = col
        for index, name in enumerate(AGENTS):
            token = f" {name} "
            if x + len(token) >= col + width:
                row += 1
                x = col
            available = _provider_available(name)
            if index == self.agent and available:
                attr = curses.A_REVERSE | curses.A_BOLD
            elif available:
                attr = curses.A_BOLD
            else:
                attr = curses.A_DIM
            _safe_addstr(self.window, row, x, token, attr)
            self.mouse_targets.append((row, x, x + len(token), index, "provider"))
            x += len(token) + 1

    def draw_launcher(self) -> None:
        height, width = self.window.getmaxyx()
        compact = height < 16 or width < 52
        left = 1 if compact else max(1, (width - min(width - 2, 84)) // 2)
        top = 0 if compact else max(1, (height - (19 if self.advanced else 11)) // 2)
        inner = max(12, width - left - 2)
        _safe_addstr(self.window, top, left, "New agent", curses.A_BOLD)
        _safe_addstr(
            self.window,
            top + 1,
            left,
            "Choose a provider, then Launch.",
            curses.A_DIM,
        )
        self._draw_providers(top + 3, left, inner)
        path_row = top + 5
        path_attr = curses.A_REVERSE if self.row == 1 else 0
        _safe_addstr(
            self.window,
            path_row,
            left,
            _clip(f"Project  {self.path}", inner),
            path_attr,
        )
        toggle = "▾ Advanced options" if self.advanced else "▸ Advanced options"
        toggle_row = path_row + 2
        _safe_addstr(self.window, toggle_row, left, _clip(toggle, inner), curses.A_BOLD)
        self.mouse_targets.append(
            (toggle_row, left, left + min(len(toggle), inner), 0, "advanced")
        )
        cursor = toggle_row + 1
        if self.advanced and not compact:
            provider = AGENTS[self.agent]
            capabilities = runtime_policy_capabilities(provider)
            runtime_available = tuple(
                bool(capabilities[name]["available"]) for name in RUNTIME_POLICIES
            )
            permission_available = tuple(
                resolve_provider_policy(
                    provider, RUNTIME_POLICIES[self.runtime], name, "interactive"
                ).supported
                for name in PERMISSION_POLICIES
            )
            mode_caps = mode_capabilities(
                provider,
                RUNTIME_POLICIES[self.runtime],
                PERMISSION_POLICIES[self.permissions],
            )
            mode_available = tuple(
                bool(mode_caps[name]["available"]) for name in LAUNCH_MODES
            )
            continuity_caps = continuity_policy_capabilities(
                provider,
                root=self.path,
                explicit_parent=self.continuity_parent,
            )
            continuity_available = tuple(
                bool(continuity_caps[name]["available"]) for name in CONTINUITY_MODES
            )
            advanced_rows = (
                (
                    "Mode      ",
                    LAUNCH_MODES,
                    self.launch_mode,
                    mode_available,
                ),
                (
                    "Runtime   ",
                    RUNTIME_POLICIES,
                    self.runtime,
                    runtime_available,
                ),
                (
                    "Permits   ",
                    PERMISSION_POLICIES,
                    self.permissions,
                    permission_available,
                ),
                (
                    "Memory    ",
                    CONTINUITY_MODES,
                    self.continuity,
                    continuity_available,
                ),
            )
            for offset, (label, choices, selected, available) in enumerate(
                advanced_rows
            ):
                line = label + " ".join(
                    _choice_tokens(choices, selected=selected, available=available)
                )
                focused = self.row == offset + 2
                _safe_addstr(self.window, cursor + offset, left, _clip(line, inner), 0)
                if focused:
                    _safe_addstr(
                        self.window, cursor + offset, left, label, curses.A_REVERSE
                    )
                _dim_unavailable_choices(
                    self.window,
                    cursor + offset,
                    left + len(label),
                    choices,
                    available,
                    selected,
                    left + inner,
                )
            _safe_addstr(
                self.window,
                cursor + 4,
                left,
                _clip(f"Parent    {self.continuity_parent or '(none)'}", inner),
                curses.A_REVERSE if self.row == 6 else 0,
            )
            runtime_help = RUNTIME_HELP[RUNTIME_POLICIES[self.runtime]]
            help_text = public_reason(runtime_help[0]) or runtime_help[0]
            _safe_addstr(
                self.window, cursor + 5, left, _clip(help_text, inner), curses.A_DIM
            )
            if runtime_help[1]:
                _safe_addstr(
                    self.window,
                    cursor + 6,
                    left,
                    _clip(runtime_help[1], inner),
                    curses.A_DIM,
                )
            # Disabled choices keep a visible reason in plain words.  The
            # admission gates themselves live in spawn; only wording is public.
            unavailable = [
                f"{name}: {public_reason(str(caps[name]['reason'])) or 'Not available'}"
                for names, caps in (
                    (RUNTIME_POLICIES, capabilities),
                    (LAUNCH_MODES, mode_caps),
                    (CONTINUITY_MODES, continuity_caps),
                )
                for name in names
                if not caps[name]["available"]
            ]
            if unavailable:
                _safe_addstr(
                    self.window,
                    cursor + 7,
                    left,
                    _clip("Unavailable — " + " · ".join(unavailable), inner),
                    curses.A_DIM,
                )
            cursor += 9
        launch_label = "[ Launch ]"
        launch_attr = curses.A_REVERSE | curses.A_BOLD
        _safe_addstr(self.window, cursor, left, launch_label, launch_attr)
        self.mouse_targets.append((cursor, left, left + len(launch_label), 0, "launch"))
        hint = (
            "Enter launch  Esc back"
            if compact
            else "←/→ provider · type to edit project · a advanced · Enter launch · Esc back"
        )
        _safe_addstr(self.window, height - 2, left, hint, curses.A_DIM)
        if self.error:
            _safe_addstr(
                self.window, height - 1, left, public_reason(self.error) or self.error
            )

    def handle_home_key(self, key: int) -> None:
        if key in (curses.KEY_LEFT, ord("h")):
            self.home_choice = (self.home_choice - 1) % 2
        elif key in (curses.KEY_RIGHT, ord("l"), ord("\t")):
            self.home_choice = (self.home_choice + 1) % 2
        elif key in (ord("n"), ord("N")):
            self.open_launcher()
        elif key in (ord("v"), ord("V")):
            self.open_voc()
        elif key in (ord("o"), ord("O")):
            self._toggle_other_sessions()
        elif key in (10, 13, curses.KEY_ENTER):
            if self.show_other and self.other_sessions:
                self._attach_session(self.other_sessions[0])
            else:
                (self.open_launcher, self.open_voc)[self.home_choice]()

    def handle_launcher_key(self, key: int) -> None:
        self.error = ""
        if key == 27:
            if self.standalone_launcher:
                raise SystemExit(0)
            self.mode = "home"
            return
        editing_parent = self.advanced and self.row == 6
        editing_path = self.row == 1
        if key in (ord("a"), ord("A")) and not editing_path and not editing_parent:
            self.advanced = not self.advanced
            if not self.advanced:
                self.row = min(self.row, 1)
            return
        rows = 7 if self.advanced else 2
        if key == curses.KEY_UP:
            self.row = (self.row - 1) % rows
            return
        if key in (curses.KEY_DOWN, ord("\t")):
            self.row = (self.row + 1) % rows
            return
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord(" ")):
            delta = -1 if key == curses.KEY_LEFT else 1
            if self.row == 0:
                self._cycle_agent(delta)
            elif self.advanced and self.row == 2:
                self._cycle_mode(delta)
            elif self.advanced and self.row == 3:
                self._cycle_runtime(delta)
            elif self.advanced and self.row == 4:
                self._cycle_permissions(delta)
            elif self.advanced and self.row == 5:
                self._cycle_continuity(delta)
            elif self.advanced and self.row == 6:
                self._cycle_parent(delta)
            return
        if key in (10, 13, curses.KEY_ENTER):
            self.launch()
            return
        editing_parent = self.advanced and self.row == 6
        editing_path = self.row == 1
        if editing_path or editing_parent:
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if editing_parent:
                    self.continuity_parent = self.continuity_parent[:-1]
                    self.parent_index = -1
                else:
                    self.path = self.path[:-1]
                    self.parent_sessions = []
                    self.parent_index = -1
            elif 32 <= key <= 126:
                if editing_parent:
                    self.continuity_parent += chr(key)
                    self.parent_index = -1
                else:
                    self.path += chr(key)
                    self.parent_sessions = []
                    self.parent_index = -1

    def _cycle_agent(self, delta: int) -> None:
        index = self.agent
        for _ in AGENTS:
            index = (index + delta) % len(AGENTS)
            if _provider_available(AGENTS[index]):
                self._select_agent(index)
                return
        self.error = "No provider is available"

    def _select_agent(self, index: int) -> None:
        """Switch provider by key or click; parent sessions belong to the old one."""
        self.agent = index
        self._normalize_runtime_choice()
        self._normalize_permission_choice()
        self._normalize_mode_choice()
        self._normalize_continuity_choice()
        self.parent_sessions = []
        self.parent_index = -1

    def _cycle_mode(self, delta: int) -> None:
        capabilities = mode_capabilities(
            AGENTS[self.agent],
            RUNTIME_POLICIES[self.runtime],
            PERMISSION_POLICIES[self.permissions],
        )
        for _ in LAUNCH_MODES:
            self.launch_mode = (self.launch_mode + delta) % len(LAUNCH_MODES)
            if capabilities[LAUNCH_MODES[self.launch_mode]]["available"]:
                return
        self.error = "No start mode is available for this provider"

    def _refresh_parent_sessions(self) -> None:
        self.parent_sessions, self.parent_error = parent_session_choices(
            AGENTS[self.agent], self.path
        )
        self.parent_index = -1

    def _cycle_parent(self, delta: int) -> None:
        if not self.parent_sessions:
            self._refresh_parent_sessions()
        if not self.parent_sessions:
            self.error = self.parent_error
            return
        self.parent_index = (
            self.parent_index + delta
            if self.parent_index >= 0
            else (0 if delta > 0 else len(self.parent_sessions) - 1)
        ) % len(self.parent_sessions)
        self.continuity_parent = self.parent_sessions[self.parent_index].session_id
        self._normalize_continuity_choice()

    def _cycle_runtime(self, delta: int) -> None:
        capabilities = runtime_policy_capabilities(AGENTS[self.agent])
        for _ in RUNTIME_POLICIES:
            self.runtime = (self.runtime + delta) % len(RUNTIME_POLICIES)
            name = RUNTIME_POLICIES[self.runtime]
            if capabilities[name]["available"]:
                self._normalize_permission_choice()
                self._normalize_mode_choice()
                return
        self.error = "No runtime is available for this provider"

    def _cycle_permissions(self, delta: int) -> None:
        provider = AGENTS[self.agent]
        runtime = RUNTIME_POLICIES[self.runtime]
        for _ in PERMISSION_POLICIES:
            self.permissions = (self.permissions + delta) % len(PERMISSION_POLICIES)
            if resolve_provider_policy(
                provider, runtime, PERMISSION_POLICIES[self.permissions], "interactive"
            ).supported:
                self._normalize_mode_choice()
                return
        self.error = "No permission policy is available for this provider"

    def _normalize_runtime_choice(self) -> None:
        capabilities = runtime_policy_capabilities(AGENTS[self.agent])
        current = RUNTIME_POLICIES[self.runtime]
        if capabilities[current]["available"]:
            return
        for index, runtime in enumerate(RUNTIME_POLICIES):
            if capabilities[runtime]["available"]:
                self.runtime = index
                return

    def _normalize_permission_choice(self) -> None:
        provider = AGENTS[self.agent]
        runtime = RUNTIME_POLICIES[self.runtime]
        current = PERMISSION_POLICIES[self.permissions]
        if resolve_provider_policy(provider, runtime, current, "interactive").supported:
            return
        for index, permissions in enumerate(PERMISSION_POLICIES):
            if resolve_provider_policy(
                provider, runtime, permissions, "interactive"
            ).supported:
                self.permissions = index
                return

    def _normalize_mode_choice(self) -> None:
        capabilities = mode_capabilities(
            AGENTS[self.agent],
            RUNTIME_POLICIES[self.runtime],
            PERMISSION_POLICIES[self.permissions],
        )
        if capabilities[LAUNCH_MODES[self.launch_mode]]["available"]:
            return
        for index, mode in enumerate(LAUNCH_MODES):
            if capabilities[mode]["available"]:
                self.launch_mode = index
                return

    def _cycle_continuity(self, delta: int) -> None:
        capabilities = continuity_policy_capabilities(
            AGENTS[self.agent], root=self.path, explicit_parent=self.continuity_parent
        )
        for _ in CONTINUITY_MODES:
            self.continuity = (self.continuity + delta) % len(CONTINUITY_MODES)
            if capabilities[CONTINUITY_MODES[self.continuity]]["available"]:
                return
        self.error = "No memory option is available"

    def _normalize_continuity_choice(self) -> None:
        capabilities = continuity_policy_capabilities(
            AGENTS[self.agent], root=self.path, explicit_parent=self.continuity_parent
        )
        if capabilities["full-lineage"]["available"]:
            self.continuity = CONTINUITY_MODES.index("full-lineage")
        else:
            self.continuity = CONTINUITY_MODES.index("fresh")

    def handle_mouse(self) -> None:
        try:
            _, x, y, _, state = curses.getmouse()
        except curses.error:
            return
        if not state:
            return
        # Pointer motion reports arrive continuously while hovering; only a
        # button event is User intent worth a pane-state probe or a click.
        if not state & ~curses.REPORT_MOUSE_POSITION:
            return
        self.presence_schedule.request()
        for row, start, end, index, kind in self.mouse_targets:
            if y != row or not (start <= x < end):
                continue
            if kind == "home":
                self.home_choice = index
                (self.open_launcher, self.open_voc)[index]()
                return
            if kind == "face":
                self._focus_face(index)
                return
            if kind == "provider":
                if _provider_available(AGENTS[index]):
                    self._select_agent(index)
                else:
                    self.error = "That provider is not available"
                return
            if kind == "launch":
                self.launch()
                return
            if kind == "advanced":
                self.advanced = not self.advanced
                if not self.advanced:
                    self.row = min(self.row, 1)
                return

    def open_launcher(self) -> None:
        """Inline compose view in this pane. Never spawn a nested floating form."""
        self.mode = "launcher"
        self.row = 0
        self.advanced = False
        self.error = ""

    def open_voc(self) -> None:
        try:
            result = subprocess.run(
                ["vc-frame", "action", "go-to-tab-name", "voc"],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            self.error = "vc-frame is not available in this Runtime Pack"
            return
        if result.returncode != 0:
            self.error = (
                result.stderr or result.stdout or "voc tab is unavailable"
            ).strip()

    def _focus_face(self, index: int) -> None:
        if index < 0 or index >= len(self.face_records):
            return
        tab = self.face_records[index].tab
        if not tab:
            self.error = "That Agent has no tab name to open"
            return
        try:
            result = subprocess.run(
                ["vc-frame", "action", "go-to-tab-name", tab],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            self.error = "vc-frame is not available in this Runtime Pack"
            return
        if result.returncode != 0:
            self.error = (
                result.stderr or result.stdout or f"cannot open tab {tab}"
            ).strip()

    def _toggle_other_sessions(self) -> None:
        self.show_other = not self.show_other
        if not self.show_other:
            return
        self.other_sessions, self.other_error = list_other_sessions()

    def _attach_session(self, name: str) -> None:
        try:
            result = subprocess.run(
                ["vc-frame", "attach", name],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            self.error = "vc-frame is not available in this Runtime Pack"
            return
        if result.returncode != 0:
            self.error = (
                result.stderr or result.stdout or f"cannot open session {name}"
            ).strip()

    def launch(self) -> None:
        try:
            workspace = normalized_workspace(self.path)
            runtime_name = RUNTIME_POLICIES[self.runtime]
            capability = runtime_policy_capabilities(AGENTS[self.agent])[runtime_name]
            if not capability["available"]:
                raise ValueError(str(capability["reason"]))
            continuity_name = CONTINUITY_MODES[self.continuity]
            continuity_capability = continuity_policy_capabilities(
                AGENTS[self.agent],
                root=workspace,
                explicit_parent=self.continuity_parent,
            )[continuity_name]
            if not continuity_capability["available"]:
                raise ValueError(str(continuity_capability["reason"]))
            argv = launch_argv(
                AGENTS[self.agent],
                LAUNCH_MODES[self.launch_mode],
                runtime_name,
                PERMISSION_POLICIES[self.permissions],
                continuity=continuity_name,
                continuity_parent=self.continuity_parent,
                workspace=workspace,
            )
        except ValueError as exc:
            self.error = public_reason(str(exc)) or str(exc)
            return
        executable = shutil.which(argv[0])
        if executable is None:
            self.error = "vibecrafted launcher is missing from PATH"
            return
        title = (
            f"{AGENTS[self.agent]} · "
            f"{LAUNCH_MODES[self.launch_mode]} · {workspace.name}"
        )
        try:
            destination = destination_session_for_workspace(workspace)
        except ValueError as exc:
            self.error = public_reason(str(exc)) or str(exc)
            return
        live, listing_error = list_live_frame_sessions()
        if listing_error:
            self.error = listing_error
            return
        try:
            require_live_destination(destination, live)
            pane = launch_pane_argv(title, workspace, argv, session=destination)
        except ValueError as exc:
            self.error = public_reason(str(exc)) or str(exc)
            return
        try:
            result = subprocess.run(pane, check=False, capture_output=True, text=True)
        except FileNotFoundError:
            self.error = "vc-frame is not available in this Runtime Pack"
            return
        if result.returncode != 0:
            self.error = (
                result.stderr or result.stdout or "cannot open a tab for that Agent"
            ).strip()
            return
        current = current_frame_session()
        if current != destination:
            try:
                attached = subprocess.run(
                    attach_session_argv(destination),
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                self.error = "vc-frame is not available in this Runtime Pack"
                return
            if attached.returncode != 0:
                self.error = (
                    attached.stderr
                    or attached.stdout
                    or f"Agent opened in {destination}, but that session could not be shown"
                ).strip()
                return
        self.mode = "home"
        self.presence_schedule.last_at = None
        self.presence_schedule.request()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vibecrafted Agent Workspaces")
    parser.add_argument("mode", choices=("home", "launcher"), nargs="?", default="home")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        curses.wrapper(lambda window: Workshop(window, mode=args.mode).run())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
