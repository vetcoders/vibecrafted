"""Clean reinstall that resurrects the Founder's world from the fresh generation.

``vibecrafted reinstall --clean`` is the second, first-class way to replace the
runtime.  The in-place install keeps old processes alive across a publication,
which is how sessions end up running a dead generation's binaries with a cut
``PATH`` and ``NO_COLOR`` nobody asked for.  This verb does the opposite: it
writes down what belongs to the Founder, stops what belongs to the runtime,
installs, and rebuilds the world from the *new* generation.  "Upgrades preserve
the Founder" is kept by resurrection, not by keeping zombies alive.

Phases (each one leaves a durable receipt in the artifacts plane):

1. SNAPSHOT: live Frame sessions (``list-sessions``), their current layout
   (``action dump-layout``, read-only) and panes (``action list-panes``); per
   pane the agent process, its cwd and its native session id.  An id is either
   *proven* by one named recipe with its evidence, or it stays ``unknown``.
   Headless dispatcher runs ride along.
2. KILL CLEAN: the installer's own census (``_owned_runtime_process_census``)
   partitioned for a reinstall, plus agent processes proven by lineage to run
   under an owned process.  Every signal goes through the installer's
   birth+argv re-verification.  The persistent service is *stopped*, never
   uninstalled, so ``make install`` reconciles it back.  A LIVE headless
   dispatcher run is spared with its whole subtree (decyzja Macieja
   2026-10-10): the old generation stays on disk after an install, so the
   worker finishes untouched; only a dispatcher whose run meta is terminal
   or missing falls into the kill.
3. INSTALL: ``make -C <checkout> install [RUNTIME_PACK=<pack>]``.
4. RESURRECT: run by the newly installed launcher, so env, pins and colours come
   from the new generation.  Layouts are rewritten (chrome through the
   generation-agnostic ``pane-python``, agent panes through
   ``spawn interactive-command --skill resume``) and recreated detached.

Identity doctrine (``spawn.py``): a requested id is not an acknowledged id, a
parent id is never copied onto a child, and unknown stays unknown.  A pane
whose id is not proven comes back as a fresh session carrying a continuity
pack, never as a guessed attach.

Not in v1, on purpose: scrollback replay, live PTY children (an open editor
comes back as a suspended command the Founder re-runs), per-pane cwd beyond what
the layout records, Linux/Windows execution, and an install path without a
source checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .frame_layout import (
    KdlShapeError,
    PaneLaunch,
    _is_python,
    generation_root,
    minimal_layout,
    rewrite_layout,
)
from .runtime_paths import (
    vibecrafted_home,
    vibecrafted_launcher_bin,
    vibecrafted_runtime_home,
)

MANIFEST_SCHEMA = "vibecrafted.reinstall-clean.manifest.v1"
PLAN_SCHEMA = "vibecrafted.reinstall-clean.plan.v1"
RECEIPT_SCHEMA = "vibecrafted.reinstall-clean.receipt.v1"
PROVEN = "proven"
UNKNOWN = "unknown"

# Installed CLI name -> fleet agent key.
AGENT_BINARIES: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "grok": "grok",
    "junie": "junie",
    "agy": "agy",
    "kimi": "kimi",
    "cursor-agent": "cursor",
    "copilot": "copilot",
    "gemini": "gemini",
}
# Agents the interactive spawn surface can relaunch (spawn.POLICY_PROVIDERS).
RESURRECTABLE = frozenset(
    {"codex", "claude", "agy", "grok", "junie", "cursor", "kimi", "copilot"}
)
_SCRIPT_HOSTS = frozenset({"node", "bun", "deno"})
# Subcommands that run a provider binary without being a conversation.
_NON_SESSION_VERBS: dict[str, frozenset[str]] = {
    "claude": frozenset(
        {"mcp", "doctor", "update", "config", "install", "setup-token", "migrate"}
    ),
    "codex": frozenset(
        {"app-server", "mcp", "mcp-server", "login", "logout", "completion", "apply"}
    ),
}
_INFO_FLAGS = frozenset({"--version", "-V", "-v", "--help", "-h"})
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_RUN_ID = re.compile(r"\b[a-z]{4}-\d{6}-\d{6}-\d{5}\b")
# A transcript or rollout born within this window of its process is that
# process's own session.  Providers create the file on their first turn.
BIRTH_WINDOW_S = 15.0
# A rollout counts as *live* only while it is still being written: a finished
# worker in the same cwd must never lend its session to a picker resume.
LIVE_WINDOW_S = 600.0
# Pane-scoped identity variables a fresh Frame server must never inherit, plus
# the idle reaper that would kill a session nobody has attached to yet.
_FRAME_IDENTITY_ENV = (
    "VC_FRAME",
    "VC_FRAME_PANE_ID",
    "VC_FRAME_SESSION_NAME",
    "VC_FRAME_TAB_NAME",
    "VC_FRAME_SERVER_IDLE_EXIT_SECS",
    "ZELLIJ",
    "ZELLIJ_PANE_ID",
    "ZELLIJ_SESSION_NAME",
)
# Run identity of whoever invoked us; the rebuilt world must not inherit it.
_RUN_IDENTITY_PREFIXES = (
    "VIBECRAFTED_RUN_",
    "VIBECRAFTED_REPORT_",
    "VIBECRAFTED_TRANSCRIPT_",
    "VIBECRAFTED_META_",
    "VIBECRAFTED_AGENT",
    "VIBECRAFTED_SESSION_ID",
    "VIBECRAFTED_OPERATOR_SESSION",
    "VIBECRAFTED_WORKSPACE_",
    "CLAUDE_CODE_SESSION_ID",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
)
RESUMED_PROMPT = ""  # empty -> the spawn surface sends "/vc-resume"
FRESH_PROMPT = (
    "The Vibecrafted runtime was reinstalled from clean (`vibecrafted reinstall "
    "--clean`). The session that ran in this pane could not be proven, so this is "
    "a fresh session. If a continuity pack is attached, read it first, then "
    "re-read the repository state before acting."
)
HEADLESS_PROMPT = (
    "The Vibecrafted runtime was reinstalled from clean while this run was in "
    "flight; the worker process was stopped by `vibecrafted reinstall --clean`. "
    "Continue the same task from where you stopped. Re-verify the working tree "
    "and the last command you ran before taking the next step."
)


class ReinstallError(RuntimeError):
    """A refused step: nothing after it may run as if it had succeeded."""


def _iso(epoch: float | None = None) -> str:
    moment = datetime.now(UTC) if epoch is None else datetime.fromtimestamp(epoch, UTC)
    return moment.isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.timestamp()


# ---------------------------------------------------------------------------
# Process facts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessRecord:
    """One same-user process: installer-shaped birth identity, parent and argv."""

    pid: int
    ppid: int
    birth: tuple[str, int, int]
    argv: tuple[str, ...]

    @property
    def started_at(self) -> float | None:
        parts = self.birth[0].split(":")
        if len(parts) != 3 or parts[0] != "darwin":
            return None
        try:
            return int(parts[1]) + int(parts[2]) / 1_000_000
        except ValueError:
            return None

    def to_json(self) -> dict[str, Any]:
        started = self.started_at
        return {
            "pid": self.pid,
            "ppid": self.ppid,
            "birth": self.birth[0],
            "started_at": _iso(started) if started is not None else None,
            "argv": list(self.argv),
        }


ProcessTable = dict[int, ProcessRecord]


class ProcessProbe(Protocol):
    def table(self) -> ProcessTable: ...

    def cwd(self, pids: Sequence[int]) -> dict[int, str]: ...


class DarwinProcessProbe:
    """Process truth from the installer's own libproc/sysctl primitives.

    The kill phase re-verifies exactly this birth+argv shape before signaling,
    so the manifest names processes the same way the census proves them.
    """

    def __init__(self, installer: Any) -> None:
        self._installer = installer
        self.errors: list[int] = []

    def table(self) -> ProcessTable:
        installer = self._installer
        records: ProcessTable = {}
        for pid in installer._darwin_process_ids():
            try:
                birth = installer._darwin_process_birth(pid)
                if birth[1] != os.geteuid():
                    continue
                argv = installer._darwin_process_arguments(pid, pointer_size=birth[2])
                ppid = installer._darwin_process_parent_pid(pid)
            except ProcessLookupError:
                continue
            except OSError:
                self.errors.append(pid)
                continue
            records[pid] = ProcessRecord(pid, ppid, tuple(birth), tuple(argv))
        return records

    def cwd(self, pids: Sequence[int]) -> dict[int, str]:
        if not pids:
            return {}
        lsof = shutil.which("lsof", path="/usr/sbin:/usr/bin:/sbin:/bin") or "lsof"
        result = subprocess.run(
            [lsof, "-a", "-d", "cwd", "-p", ",".join(str(p) for p in pids), "-Fpn"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        )
        cwds: dict[int, str] = {}
        current: int | None = None
        for line in result.stdout.splitlines():
            if line.startswith("p") and line[1:].isdigit():
                current = int(line[1:])
            elif line.startswith("n") and current is not None:
                cwds[current] = line[1:]
        return cwds


def _children(table: ProcessTable) -> dict[int, list[int]]:
    index: dict[int, list[int]] = {}
    for record in table.values():
        index.setdefault(record.ppid, []).append(record.pid)
    for pids in index.values():
        pids.sort()
    return index


def descendants(table: ProcessTable, root: int) -> list[int]:
    """Every live descendant of ``root`` (excluding it), breadth first."""
    index = _children(table)
    found: list[int] = []
    queue = list(index.get(root, ()))
    while queue:
        pid = queue.pop(0)
        if pid in found:
            continue
        found.append(pid)
        queue.extend(index.get(pid, ()))
    return found


def ancestors(table: ProcessTable, pid: int) -> list[int]:
    """Parent chain of ``pid`` inside the table, nearest first."""
    chain: list[int] = []
    current = table.get(pid)
    while current is not None and current.ppid not in chain and current.ppid > 1:
        chain.append(current.ppid)
        current = table.get(current.ppid)
    return chain


# ---------------------------------------------------------------------------
# Agent classification and native identity
# ---------------------------------------------------------------------------


def classify_agent(argv: Sequence[str]) -> str | None:
    """Fleet agent key for a session-capable agent CLI process, else ``None``."""
    if not argv:
        return None
    index = 0
    name = Path(argv[0]).name
    hosted = name in _SCRIPT_HOSTS or _is_python(name)
    if hosted:
        if len(argv) < 2 or argv[1].startswith("-"):
            return None
        index = 1
        name = Path(argv[1]).name
    provider = AGENT_BINARIES.get(name)
    if provider is None:
        return None
    rest = list(argv[index + 1 :])
    if rest and rest[0] in _INFO_FLAGS:
        return None
    first_word = next((token for token in rest if not token.startswith("-")), "")
    if first_word in _NON_SESSION_VERBS.get(provider, frozenset()):
        return None
    return provider


def _flag_value(args: Sequence[str], *names: str) -> str:
    for index, token in enumerate(args):
        for name in names:
            if token == name and index + 1 < len(args):
                return args[index + 1]
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
    return ""


def argv_native_session(provider: str, args: Sequence[str]) -> tuple[str, str]:
    """The session id an agent argv explicitly names, with the flag that named it."""
    if provider == "claude":
        if "--fork-session" in args:
            return "", ""  # a fork mints its own id; the --resume value is the parent
        for flag in ("--session-id", "--resume", "-r"):
            value = _flag_value(args, flag)
            if value and _UUID.fullmatch(value):
                return value.lower(), flag
        return "", ""
    if provider == "codex" and "resume" in args:
        tail = [
            t for t in args[list(args).index("resume") + 1 :] if not t.startswith("-")
        ]
        if tail and _UUID.fullmatch(tail[0]):
            return tail[0].lower(), "resume"
    return "", ""


_ARGV_RECIPES = {
    "--session-id": "argv-session-id",
    "--resume": "argv-resume",
    "-r": "argv-resume",
    "resume": "argv-resume",
}


def codex_resume_mode(args: Sequence[str]) -> str:
    """``fresh`` (new conversation), ``explicit`` (resume <id>) or ``picker``."""
    if "resume" not in args:
        return "fresh"
    return "explicit" if argv_native_session("codex", args)[0] else "picker"


def argv_model(provider: str, args: Sequence[str]) -> str:
    if provider == "codex":
        return _flag_value(args, "-m", "--model")
    return _flag_value(args, "--model")


def infer_permissions(provider: str, args: Sequence[str]) -> tuple[str, str]:
    """Map observed agent flags onto the spawn permission vocabulary.

    Resurrection must never escalate: an unrecognised shape maps to ``auto``.
    """
    if provider == "claude":
        if "--dangerously-skip-permissions" in args:
            return "bypass", "--dangerously-skip-permissions"
        mode = _flag_value(args, "--permission-mode")
        mapping = {
            "bypassPermissions": "bypass",
            "acceptEdits": "accept-edits",
            "plan": "read-only",
            "default": "auto",
            "auto": "auto",
        }
        if mode in mapping:
            return mapping[mode], f"--permission-mode {mode}"
    if provider == "codex":
        for flag in ("--dangerously-bypass-approvals-and-sandbox", "--yolo"):
            if flag in args:
                return "bypass", flag
        if "--full-auto" in args:
            return "auto", "--full-auto"
        if _flag_value(args, "-s", "--sandbox") == "read-only":
            return "read-only", "--sandbox read-only"
    return "auto", "unrecognised flags (never escalate)"


def claude_project_slug(cwd: str) -> str:
    """Claude Code's project directory name for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


@dataclass(frozen=True)
class ProviderStores:
    claude_projects: Path
    codex_sessions: Path

    @classmethod
    def default(cls) -> ProviderStores:
        from .compact_hooks import codex_sessions_dir

        claude_home = Path(
            os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
        ).expanduser()
        return cls(claude_home / "projects", codex_sessions_dir())


def _first_timestamp(path: Path, *, lines: int = 60) -> float | None:
    """Earliest record timestamp of a provider JSONL (its session birth)."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _, line in zip(range(lines), handle, strict=False):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                payload = event.get("payload")
                for candidate in (
                    payload.get("timestamp") if isinstance(payload, dict) else None,
                    event.get("timestamp"),
                ):
                    stamp = _parse_iso(candidate)
                    if stamp is not None:
                        return stamp
    except OSError:
        return None
    return None


def codex_rollout_meta(path: Path) -> dict[str, Any]:
    """``{id, cwd, born}`` of a codex rollout.

    ``compact_hooks.session_meta_from_jsonl`` owns id/cwd; birth-binding also
    needs the session timestamp, which that reader deliberately drops.
    """
    from .compact_hooks import session_meta_from_jsonl

    meta = session_meta_from_jsonl(path)
    if not meta.get("id"):
        return {}
    return {**meta, "born": _first_timestamp(path)}


@dataclass
class AgentProcess:
    provider: str
    record: ProcessRecord
    cwd: str
    linked_run_id: str = ""
    native_session_id: str = ""
    identity: str = UNKNOWN
    recipe: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    unknown_reason: str = ""

    def to_json(self) -> dict[str, Any]:
        args = self.record.argv[1:]
        permissions, permissions_source = infer_permissions(self.provider, args)
        return {
            "provider": self.provider,
            "process": self.record.to_json(),
            "cwd": self.cwd,
            "linked_run_id": self.linked_run_id,
            "model": argv_model(self.provider, args),
            "permissions": permissions,
            "permissions_source": permissions_source,
            "native_session_id": self.native_session_id or None,
            "identity": self.identity,
            "recipe": self.recipe or None,
            "evidence": self.evidence,
            "unknown_reason": self.unknown_reason or None,
        }


def linked_run_id(table: ProcessTable, pid: int) -> str:
    """Control-plane run that launched ``pid``: ``--run-id`` or a runtime_runs path."""
    for candidate in (pid, *ancestors(table, pid)):
        record = table.get(candidate)
        if record is None:
            continue
        explicit = _flag_value(record.argv, "--run-id")
        if explicit and _RUN_ID.fullmatch(explicit):
            return explicit
        for token in record.argv:
            match = re.search(r"/runtime_runs/([^/]+)/", token)
            if match and _RUN_ID.fullmatch(match.group(1)):
                return match.group(1)
    return ""


TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "stopped", "cancelled"})


def live_headless_dispatchers(
    dispatchers: Sequence[ProcessRecord],
    run_meta: Callable[[str], dict[str, Any] | None],
    table: ProcessTable,
) -> dict[int, str]:
    """Dispatcher pids whose control-plane run is still live, mapped to run id.

    Decyzja Macieja 2026-10-10: KILL CLEAN never takes a live headless worker
    down mid-run.  The previous generation stays on disk after an install, so
    the run finishes on the binaries it was born with.  Only a dispatcher
    whose run meta is terminal (or unreadable — an unprovable zombie) stays
    in the owned-census kill.
    """
    live: dict[int, str] = {}
    for record in dispatchers:
        run_id = _flag_value(record.argv, "--run-id") or linked_run_id(
            table, record.pid
        )
        if not run_id:
            continue
        meta = run_meta(run_id)
        if meta is None:
            continue
        status = str(meta.get("status") or "").strip().lower()
        if status not in TERMINAL_RUN_STATUSES:
            live[record.pid] = run_id
    return live


def default_run_meta(run_id: str) -> dict[str, Any] | None:
    from .control_plane import control_plane_home

    path = control_plane_home() / "runtime_runs" / run_id / "meta.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _verified_meta_session(meta: Mapping[str, Any]) -> str:
    """The canonical continue filter: requested/runtime ids never count."""
    from .workflow import _provider_session_for_continue

    return _provider_session_for_continue(dict(meta))


def resolve_native_identities(
    agents: Sequence[AgentProcess],
    *,
    stores: ProviderStores,
    run_meta: Callable[[str], dict[str, Any] | None] = default_run_meta,
    verified_meta_session: Callable[[Mapping[str, Any]], str] = _verified_meta_session,
    now: float | None = None,
) -> None:
    """Prove each agent's native session id by one named recipe, or leave it unknown.

    Pass 1 takes ids the process or the runtime ledger names explicitly. Pass 2
    attributes provider transcripts to the remaining processes, and only when
    exactly one transcript can belong to exactly one process. A transcript
    already claimed in pass 1 never proves a second process.
    """
    claimed: set[str] = set()
    for agent in agents:
        args = agent.record.argv[1:]
        explicit, flag = argv_native_session(agent.provider, args)
        meta_id = ""
        if agent.linked_run_id:
            meta = run_meta(agent.linked_run_id)
            if meta:
                meta_id = verified_meta_session(meta)
                agent.evidence["run_meta"] = {
                    "run_id": agent.linked_run_id,
                    "verified_session": meta_id or None,
                    "requested_session": meta.get("provider_session_id") or None,
                    "native_identity_status": meta.get("native_identity_status"),
                }
        if explicit and meta_id and explicit != meta_id:
            agent.unknown_reason = (
                f"argv names {explicit} but run meta verified {meta_id}"
            )
            agent.evidence["conflict"] = True
            continue
        if explicit:
            acknowledgement = _acknowledgement(agent.provider, explicit, stores)
            agent.evidence["argv_flag"] = flag
            if acknowledgement is None:
                agent.unknown_reason = (
                    f"argv requests {explicit} via {flag}, but the provider never "
                    "wrote that session (requested is not acknowledged)"
                )
                continue
            agent.evidence["transcript"] = str(acknowledgement)
            _prove(agent, explicit, f"{agent.provider}-{_ARGV_RECIPES[flag]}")
        elif meta_id:
            _prove(agent, meta_id, "run-meta")
        if agent.identity == PROVEN:
            claimed.add(agent.native_session_id)

    pending = [a for a in agents if a.identity != PROVEN and not a.unknown_reason]
    _attribute_claude(
        [a for a in pending if a.provider == "claude"], stores=stores, claimed=claimed
    )
    _attribute_codex(
        [a for a in pending if a.provider == "codex"],
        stores=stores,
        claimed=claimed,
        now=time.time() if now is None else now,
    )
    for agent in pending:
        if agent.identity != PROVEN and not agent.unknown_reason:
            agent.unknown_reason = f"no recipe proves a {agent.provider} session"


def _prove(agent: AgentProcess, session_id: str, recipe: str) -> None:
    agent.native_session_id = session_id
    agent.identity = PROVEN
    agent.recipe = recipe
    agent.unknown_reason = ""


def _acknowledgement(
    provider: str, session_id: str, stores: ProviderStores
) -> Path | None:
    """The provider-written file that proves ``session_id`` exists."""
    if provider == "claude":
        return next(
            iter(sorted(stores.claude_projects.glob(f"*/{session_id}.jsonl"))), None
        )
    if provider == "codex":
        return next(
            iter(sorted(stores.codex_sessions.rglob(f"rollout-*{session_id}.jsonl"))),
            None,
        )
    return None


def _fresh_files(paths: Sequence[Path], since: float) -> list[tuple[Path, float]]:
    fresh: list[tuple[Path, float]] = []
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= since:
            fresh.append((path, mtime))
    return sorted(fresh, key=lambda item: item[1], reverse=True)


def _attribute_claude(
    agents: Sequence[AgentProcess], *, stores: ProviderStores, claimed: set[str]
) -> None:
    by_cwd: dict[str, list[AgentProcess]] = {}
    for agent in agents:
        by_cwd.setdefault(agent.cwd, []).append(agent)
    for cwd, group in by_cwd.items():
        project = stores.claude_projects / claude_project_slug(cwd)
        files = sorted(project.glob("*.jsonl")) if project.is_dir() else []
        starts = [a.record.started_at for a in group if a.record.started_at]
        since = (min(starts) if starts else 0.0) - 2.0
        fresh = [(p, m) for p, m in _fresh_files(files, since) if p.stem not in claimed]
        listing = [{"session": p.stem, "mtime": _iso(m)} for p, m in fresh[:8]]
        if len(group) == 1:
            agent = group[0]
            agent.evidence["project_dir"] = str(project)
            agent.evidence["candidates"] = listing
            if not fresh:
                agent.unknown_reason = "no transcript written since the process started"
                continue
            chosen = fresh[0][0]
            agent.evidence["transcript"] = str(chosen)
            _prove(agent, chosen.stem, "claude-project-newest")
            claimed.add(chosen.stem)
            continue
        _bind_by_birth(
            group,
            fresh,
            claimed,
            recipe="claude-project-birth",
            born=lambda path: _first_timestamp(path),
            listing=listing,
        )


def _attribute_codex(
    agents: Sequence[AgentProcess],
    *,
    stores: ProviderStores,
    claimed: set[str],
    now: float,
) -> None:
    if not agents:
        return
    starts = [a.record.started_at for a in agents if a.record.started_at]
    since = (min(starts) if starts else 0.0) - 60.0
    rollouts = (
        list(stores.codex_sessions.rglob("rollout-*.jsonl"))
        if stores.codex_sessions.is_dir()
        else []
    )
    metas: list[tuple[Path, float, dict[str, Any]]] = []
    for path, mtime in _fresh_files(rollouts, since):
        meta = codex_rollout_meta(path)
        if meta and meta["id"] not in claimed:
            metas.append((path, mtime, meta))
    for agent in agents:
        mode = codex_resume_mode(agent.record.argv[1:])
        agent.evidence["codex_mode"] = mode
        started = agent.record.started_at or 0.0
        same_cwd = [
            m
            for m in metas
            if m[2].get("cwd") == agent.cwd and m[2]["id"] not in claimed
        ]
        agent.evidence["candidates"] = [
            {
                "session": m[2]["id"],
                "mtime": _iso(m[1]),
                "born": _iso(m[2]["born"]) if m[2].get("born") else None,
            }
            for m in same_cwd[:8]
        ]
        if mode == "fresh":
            born = [
                m
                for m in same_cwd
                if m[2].get("born") is not None
                and abs(m[2]["born"] - started) <= BIRTH_WINDOW_S
            ]
            if len(born) == 1:
                agent.evidence["rollout"] = str(born[0][0])
                _prove(agent, born[0][2]["id"], "codex-rollout-birth")
                claimed.add(born[0][2]["id"])
                continue
            if len(born) > 1:
                agent.unknown_reason = "several rollouts were born with this process"
                continue
    # Picker resumes (and fresh processes without a birth match) fall back to the
    # live-rollout recipe, which only proves when the cwd has one such process.
    remaining = [a for a in agents if a.identity != PROVEN and not a.unknown_reason]
    by_cwd: dict[str, list[AgentProcess]] = {}
    for agent in remaining:
        by_cwd.setdefault(agent.cwd, []).append(agent)
    for cwd, group in by_cwd.items():
        if len(group) != 1:
            for agent in group:
                agent.unknown_reason = (
                    f"{len(group)} unproven codex processes share {cwd}; "
                    "a live rollout cannot be attributed"
                )
            continue
        agent = group[0]
        started = agent.record.started_at or 0.0
        live = [
            m
            for m in metas
            if m[2].get("cwd") == cwd
            and m[1] >= max(started, now - LIVE_WINDOW_S)
            and m[2]["id"] not in claimed
        ]
        if len(live) != 1:
            agent.unknown_reason = (
                f"{len(live)} rollouts in {cwd} written in the last "
                f"{int(LIVE_WINDOW_S)}s after the process started"
            )
            continue
        agent.evidence["rollout"] = str(live[0][0])
        _prove(agent, live[0][2]["id"], "codex-rollout-live")
        claimed.add(live[0][2]["id"])


def _bind_by_birth(
    group: Sequence[AgentProcess],
    fresh: Sequence[tuple[Path, float]],
    claimed: set[str],
    *,
    recipe: str,
    born: Callable[[Path], float | None],
    listing: list[dict[str, Any]],
) -> None:
    births = {path: born(path) for path, _ in fresh}
    for agent in group:
        agent.evidence["candidates"] = listing
        started = agent.record.started_at
        matches = [
            path
            for path, stamp in births.items()
            if started is not None
            and stamp is not None
            and abs(stamp - started) <= BIRTH_WINDOW_S
            and path.stem not in claimed
        ]
        if len(matches) == 1:
            agent.evidence["transcript"] = str(matches[0])
            _prove(agent, matches[0].stem, recipe)
            claimed.add(matches[0].stem)
        else:
            agent.unknown_reason = (
                f"{len(group)} processes share this cwd and {len(matches)} "
                "transcripts were born with this one"
            )


# ---------------------------------------------------------------------------
# Frame surface
# ---------------------------------------------------------------------------


class FrameProbe(Protocol):
    def list_sessions(self) -> list[str]: ...

    def dump_layout(self, session: str) -> str: ...

    def list_panes(self, session: str) -> list[dict[str, Any]]: ...

    def save_session(self, session: str) -> None: ...


def frame_socket_dir() -> Path:
    """Frame's socket root, in the precedence the engine reads it."""
    for key in ("VC_FRAME_SOCKET_DIR", "ZELLIJ_SOCKET_DIR"):
        value = os.environ.get(key, "")
        if value:
            return Path(value).expanduser()
    return Path(f"/tmp/vc-frame-{os.getuid()}")


def frame_client_env(
    base: Mapping[str, str], socket_dir: Path, *, caller: str = "vibecrafted-reinstall"
) -> dict[str, str]:
    env = {k: v for k, v in base.items() if k not in _FRAME_IDENTITY_ENV}
    env["VC_FRAME_SOCKET_DIR"] = str(socket_dir)
    env["ZELLIJ_SOCKET_DIR"] = str(socket_dir)
    env["VC_FRAME_CALLER"] = caller
    return env


def scrubbed_env(base: Mapping[str, str]) -> dict[str, str]:
    """Caller env minus Frame pane identity, run identity and inherited colour kill."""
    env: dict[str, str] = {}
    for key, value in base.items():
        if key in _FRAME_IDENTITY_ENV or key == "NO_COLOR":
            continue
        if any(key.startswith(prefix) for prefix in _RUN_IDENTITY_PREFIXES):
            continue
        env[key] = value
    return env


def without_python_path(env: Mapping[str, str]) -> dict[str, str]:
    """Env for anything that must not import the *calling* generation's core.

    The deck hands this process ``PYTHONPATH=<core dir>``.  A Frame server would
    export it into every pane (host tools then import a runtime package), and
    the new launcher would load the previous generation's code.
    """
    return {k: v for k, v in env.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}


class CliFrameProbe:
    """Read-mostly client of live Frame servers through the vc-frame CLI."""

    def __init__(
        self,
        binary: str,
        socket_dir: Path,
        *,
        session_binaries: Mapping[str, str] | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.binary = binary
        self.socket_dir = socket_dir
        self.session_binaries = dict(session_binaries or {})
        self.timeout = timeout
        self.env = frame_client_env(os.environ, socket_dir)

    def _run(self, binary: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [binary, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=self.env,
            stdin=subprocess.DEVNULL,
        )

    def list_sessions(self) -> list[str]:
        from .settlement_history import _running_session_names

        result = self._run(self.binary, "list-sessions", "--no-formatting")
        return list(_running_session_names(result.stdout))

    def _session_binary(self, session: str) -> str:
        return self.session_binaries.get(session) or self.binary

    def dump_layout(self, session: str) -> str:
        result = self._run(
            self._session_binary(session), "--session", session, "action", "dump-layout"
        )
        if result.returncode != 0 or "layout" not in result.stdout:
            raise ReinstallError(
                f"dump-layout failed for {session}: {result.stderr.strip()[:300]}"
            )
        return result.stdout

    def list_panes(self, session: str) -> list[dict[str, Any]]:
        result = self._run(
            self._session_binary(session),
            "--session",
            session,
            "action",
            "list-panes",
            "--json",
            "--all",
        )
        if result.returncode != 0:
            raise ReinstallError(
                f"list-panes failed for {session}: {result.stderr.strip()[:300]}"
            )
        payload = json.loads(result.stdout or "[]")
        return (
            [p for p in payload if isinstance(p, dict)]
            if isinstance(payload, list)
            else []
        )

    def save_session(self, session: str) -> None:
        self._run(
            self._session_binary(session),
            "--session",
            session,
            "action",
            "save-session",
        )


def frame_server_for(
    table: ProcessTable, socket_dir: Path, session: str
) -> ProcessRecord | None:
    """The ``vc-frame --server <socket_dir>/contract_version_N/<session>`` process."""
    wanted = socket_dir.resolve(strict=False)
    for record in table.values():
        argv = record.argv
        if "--server" not in argv or Path(argv[0]).name not in {
            "vc-frame",
            "vc-frame.real",
        }:
            continue
        index = argv.index("--server")
        if index + 1 >= len(argv):
            continue
        socket_path = Path(argv[index + 1])
        if (
            socket_path.name == session
            and socket_path.parent.name.startswith("contract_version_")
            and socket_path.parent.parent.resolve(strict=False) == wanted
        ):
            return record
    return None


# ---------------------------------------------------------------------------
# Ownership (installer census, reused)
# ---------------------------------------------------------------------------


class RuntimeOwnership(Protocol):
    def census(self) -> Sequence[Any]: ...

    def caller_ancestors(self) -> frozenset[int]: ...

    def roots(self) -> tuple[Path, ...]: ...

    def is_owned_argv(self, argv: Sequence[str]) -> bool: ...

    def terminate(self, records: Sequence[ProcessRecord], *, label: str) -> None: ...

    def stop_service(self) -> dict[str, Any]: ...

    def service_state(self) -> dict[str, Any]: ...


class InstallerOwnership:
    """The installer's census, identity re-verification and service runner."""

    def __init__(self, installer: Any) -> None:
        self._installer = installer

    def census(self) -> Sequence[Any]:
        return self._installer._owned_runtime_process_census()

    def caller_ancestors(self) -> frozenset[int]:
        return self._installer._darwin_caller_ancestor_pids()

    def roots(self) -> tuple[Path, ...]:
        return self._installer._owned_runtime_process_roots()

    def is_owned_argv(self, argv: Sequence[str]) -> bool:
        return bool(
            self._installer._runtime_process_argv_is_owned(
                tuple(argv), roots=self.roots()
            )
        )

    def terminate(self, records: Sequence[ProcessRecord], *, label: str) -> None:
        installer = self._installer
        verified = [
            installer._RetiredVcFrameProcess(r.pid, tuple(r.birth), tuple(r.argv))
            for r in records
        ]
        installer._terminate_verified_runtime_processes(verified, label=label)

    def service_state(self) -> dict[str, Any]:
        installer = self._installer
        shared_home = vibecrafted_home()
        plist = (
            Path.home()
            / "Library"
            / "LaunchAgents"
            / "io.vetcoders.vibecrafted.server.plist"
        )
        return {
            "evidence": bool(installer._runtime_service_has_evidence(shared_home)),
            "launch_agent": str(plist) if plist.exists() else None,
        }

    def stop_service(self) -> dict[str, Any]:
        """``server service stop`` under the install lease; plist stays for reconcile."""
        installer = self._installer
        shared_home = vibecrafted_home()
        if not installer._runtime_service_has_evidence(shared_home):
            return {"action": "none", "reason": "no service evidence"}
        current_link = installer._current_tools_link(shared_home)
        with installer._tools_install_lease(
            current_link, operation="runtime-reinstall-clean"
        ) as descriptor:
            os.set_inheritable(descriptor, True)
            with installer._inherited_tools_install_lease(descriptor):
                installer._assert_runtime_loaded_service_owner(shared_home)
                snapshot = installer._runtime_service_snapshot(shared_home)
                if snapshot is None:
                    raise ReinstallError(
                        "service evidence exists but no verified launcher"
                    )
                launcher, status, pair = snapshot
                was_live = bool(
                    status.installed or status.loaded or status.supervisor_live
                )
                if not was_live and pair == "stopped":
                    return {"action": "none", "was_live": False, "pair": pair}
                result = installer._run_runtime_service_command(
                    launcher, shared_home, "service", "stop"
                )
                final = installer._runtime_service_snapshot(shared_home)
                if result.returncode != 0 or final is None:
                    raise ReinstallError(
                        "service stop failed: "
                        + (
                            result.stderr.strip()
                            or result.stdout.strip()
                            or f"exit={result.returncode}"
                        )
                    )
                _, final_status, final_pair = final
                if not final_status.quiescent or final_pair != "stopped":
                    raise ReinstallError("service is not quiescent after stop")
                return {"action": "stopped", "was_live": was_live, "pair": pair}


def partition_census(
    census: Sequence[Any], *, roots: Sequence[Path], config_dir: Path
) -> dict[str, list[Any]]:
    """Split the uninstall census into what a reinstall kills and what it spares.

    A *guest* is a foreign program that merely runs on our interpreter, e.g.
    Codescribe's ``cs-bus`` started as ``<runtime python> ~/.local/bin/cs-bus``.
    Uninstall must stop it (the interpreter is about to vanish); a reinstall
    leaves the Founder's other products alone.
    """
    resolved_roots = [Path(r).resolve(strict=False) for r in roots]
    config = config_dir.expanduser().resolve(strict=False)

    def managed(raw: str) -> bool:
        path = Path(raw).expanduser().resolve(strict=False)
        return any(path == r or r in path.parents for r in [*resolved_roots, config])

    kill: list[Any] = []
    guests: list[Any] = []
    for record in census:
        argv = tuple(record.argv)
        script = argv[1] if len(argv) > 1 else ""
        if (
            argv
            and _is_python(Path(argv[0]).name)
            and script.startswith("/")
            and not managed(script)
        ):
            guests.append(record)
        else:
            kill.append(record)
    return {"kill": kill, "guests": guests}


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def _pane_leader(
    pane: Mapping[str, Any],
    table: ProcessTable,
    subtree: Sequence[int],
    server: int,
    cwds: Mapping[int, str],
) -> ProcessRecord | None:
    command = str(pane.get("pane_command") or "").strip()
    if not command:
        return None
    matches = [table[p] for p in subtree if " ".join(table[p].argv) == command]
    if not matches:
        return None

    def depth(record: ProcessRecord) -> int:
        chain = ancestors(table, record.pid)
        return chain.index(server) if server in chain else len(chain)

    shallow = min(depth(r) for r in matches)
    top = [r for r in matches if depth(r) == shallow]
    if len(top) > 1:
        cwd = str(pane.get("pane_cwd") or "")
        top = [r for r in top if cwds.get(r.pid) == cwd] if cwd else []
    return top[0] if len(top) == 1 else None


def _topmost_agents(table: ProcessTable, pids: Sequence[int]) -> list[tuple[str, int]]:
    agents = [(classify_agent(table[p].argv), p) for p in pids if p in table]
    agent_pids = {p for provider, p in agents if provider}
    return [
        (provider, pid)
        for provider, pid in agents
        if provider and not (set(ancestors(table, pid)) & agent_pids)
    ]


def _real(path: str) -> str:
    return os.path.realpath(path) if path else ""


def snapshot_world(
    *,
    procs: ProcessProbe,
    frame: FrameProbe,
    ownership: RuntimeOwnership | None,
    stores: ProviderStores,
    socket_dir: Path,
    config_dir: Path,
    run_meta: Callable[[str], dict[str, Any] | None] = default_run_meta,
    verified_meta_session: Callable[[Mapping[str, Any]], str] = _verified_meta_session,
    active_root: str | None = None,
    mode: str = "dry-run",
    save_sessions: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Phase 1: record the world the reinstall must give back."""
    table = procs.table()
    sessions_out: list[dict[str, Any]] = []
    agents: list[AgentProcess] = []
    placements: list[tuple[dict[str, Any] | None, dict[str, Any], AgentProcess]] = []
    framed: set[int] = set()
    session_names = frame.list_sessions()
    agent_pids_all: list[int] = []
    for name in session_names:
        server = frame_server_for(table, socket_dir, name)
        if save_sessions:
            frame.save_session(name)
        layout = frame.dump_layout(name)
        panes = [p for p in frame.list_panes(name) if not p.get("is_plugin")]
        subtree = descendants(table, server.pid) if server else []
        framed.update(subtree)
        agent_pids_all.extend(p for p in subtree if classify_agent(table[p].argv))
        session = {
            "name": name,
            "server": server.to_json() if server else None,
            "client_bin": server.argv[0] if server else None,
            "generation_root": generation_root(server.argv[0]) if server else None,
            "layout": layout,
            "cwd": _layout_cwd(layout),
            "panes": [],
            "unplaced_agents": [],
        }
        sessions_out.append(session)
        session["_subtree"] = subtree
        session["_server_pid"] = server.pid if server else 0
        session["_raw_panes"] = panes

    dispatchers = [
        r
        for r in table.values()
        if "vibecrafted_core.dispatcher" in r.argv
        and "run" in r.argv
        and r.pid not in framed
    ]
    for dispatcher in dispatchers:
        agent_pids_all.extend(
            p
            for p in descendants(table, dispatcher.pid)
            if classify_agent(table[p].argv)
        )
    spared_headless = live_headless_dispatchers(dispatchers, run_meta, table)
    cwds = procs.cwd(sorted(set(agent_pids_all)))

    for session in sessions_out:
        subtree = session.pop("_subtree")
        server_pid = session.pop("_server_pid")
        panes = session.pop("_raw_panes")
        claimed_leaders: set[int] = set()
        placed: set[int] = set()
        leader_cwds = cwds
        for pane in panes:
            leader = _pane_leader(pane, table, subtree, server_pid, leader_cwds)
            entry: dict[str, Any] = {
                "id": pane.get("id"),
                "tab_id": pane.get("tab_id"),
                "tab_position": pane.get("tab_position"),
                "tab_name": pane.get("tab_name"),
                "title": pane.get("title"),
                "terminal_command": pane.get("terminal_command"),
                "pane_command": pane.get("pane_command"),
                "cwd": pane.get("pane_cwd"),
                "leader": None,
                "agent": None,
            }
            session["panes"].append(entry)
            if leader is None or leader.pid in claimed_leaders:
                continue
            claimed_leaders.add(leader.pid)
            entry["leader"] = leader.to_json()
            scope = [leader.pid, *descendants(table, leader.pid)]
            top = _topmost_agents(table, scope)
            for index, (provider, pid) in enumerate(top):
                cwd = _real(cwds.get(pid) or entry["cwd"] or "")
                agent = AgentProcess(provider, table[pid], cwd)
                agent.linked_run_id = linked_run_id(table, pid)
                agents.append(agent)
                placed.add(pid)
                placements.append((session, entry if index == 0 else {}, agent))
        for provider, pid in _topmost_agents(table, subtree):
            if pid in placed:
                continue
            cwd = _real(cwds.get(pid) or session["cwd"] or "")
            agent = AgentProcess(provider, table[pid], cwd)
            agent.linked_run_id = linked_run_id(table, pid)
            agents.append(agent)
            placements.append((session, {}, agent))

    headless: list[dict[str, Any]] = []
    for dispatcher in sorted(dispatchers, key=lambda r: r.pid):
        run_id = _flag_value(dispatcher.argv, "--run-id")
        for provider, pid in _topmost_agents(table, descendants(table, dispatcher.pid)):
            agent = AgentProcess(provider, table[pid], _real(cwds.get(pid, "")))
            agent.linked_run_id = run_id or linked_run_id(table, pid)
            agents.append(agent)
            entry = {
                "run_id": agent.linked_run_id,
                "dispatcher": dispatcher.to_json(),
                "spared": dispatcher.pid in spared_headless,
            }
            headless.append(entry)
            placements.append((None, entry, agent))

    resolve_native_identities(
        agents,
        stores=stores,
        run_meta=run_meta,
        verified_meta_session=verified_meta_session,
        now=now,
    )
    for session, entry, agent in placements:
        payload = agent.to_json()
        if session is None or entry:
            entry["agent"] = payload
        else:
            session["unplaced_agents"].append(payload)

    servers = [s["server"]["pid"] for s in sessions_out if s.get("server")]
    kill_plan = _kill_snapshot(
        table,
        ownership,
        config_dir,
        agent_pids=agent_pids_all,
        dispatchers=dispatchers,
        frame_servers=servers,
        spared_headless=spared_headless,
    )
    running_roots = sorted(
        {root for s in sessions_out if (root := s.get("generation_root"))}
        | {
            root
            for r in kill_plan.get("kill", [])
            if (root := generation_root(r["argv"][0]))
        }
    )
    proven = sum(1 for a in agents if a.identity == PROVEN)
    return {
        "schema": MANIFEST_SCHEMA,
        "mode": mode,
        "created_at": _iso(),
        "host": socket.gethostname(),
        "runtime": {
            "active_root": active_root,
            "running_roots": running_roots,
            "drift": bool(active_root and any(r != active_root for r in running_roots)),
        },
        "frame": {"socket_dir": str(socket_dir), "sessions": sessions_out},
        "headless_runs": headless,
        "ownership": kill_plan,
        "summary": {
            "sessions": len(sessions_out),
            "panes": sum(len(s["panes"]) for s in sessions_out),
            "agents": len(agents),
            "agents_proven": proven,
            "agents_unknown": len(agents) - proven,
            "headless_runs": len(headless),
        },
    }


def _layout_cwd(layout: str) -> str:
    match = re.search(r'^\s*cwd\s+"((?:[^"\\]|\\.)*)"', layout, re.MULTILINE)
    return match.group(1) if match else ""


def _kill_snapshot(
    table: ProcessTable,
    ownership: RuntimeOwnership | None,
    config_dir: Path,
    *,
    agent_pids: Sequence[int],
    dispatchers: Sequence[ProcessRecord],
    frame_servers: Sequence[int] = (),
    spared_headless: Mapping[int, str] | None = None,
) -> dict[str, Any]:
    """Phase 2 as data.  The census never lists the caller's own ancestry, so a
    Frame server hosting the invoking pane is named here as *blocking*: only a
    detached executor can stop it.  ``spared_headless`` maps live headless
    dispatcher pids to their run ids; their whole subtrees stay running
    (decyzja Macieja 2026-10-10).
    """
    if ownership is None:
        return {"available": False, "reason": "process census is macOS-only in v1"}
    spared_headless = dict(spared_headless or {})
    spared_pids: set[int] = set()
    for pid in spared_headless:
        spared_pids.add(pid)
        spared_pids.update(descendants(table, pid))
    census = list(ownership.census())
    split = partition_census(census, roots=ownership.roots(), config_dir=config_dir)
    owned_pids = (
        {r.pid for r in census} | set(frame_servers) | {d.pid for d in dispatchers}
    )
    caller = sorted(ownership.caller_ancestors())
    lineage = []
    for pid in sorted(set(agent_pids)):
        record = table.get(pid)
        if (
            record
            and pid not in caller
            and pid not in spared_pids
            and (set(ancestors(table, pid)) & owned_pids)
            and pid not in owned_pids
        ):
            lineage.append(record)
    blocking = [
        pid
        for pid in caller
        if pid in table
        and (pid in frame_servers or ownership.is_owned_argv(table[pid].argv))
    ]
    return {
        "available": True,
        "kill": [_census_json(r) for r in split["kill"] if r.pid not in spared_pids],
        "headless_spared": [
            {**_census_json(r), "run_id": spared_headless.get(r.pid, "")}
            for r in split["kill"]
            if r.pid in spared_pids
        ],
        "headless_spared_pids": sorted(spared_pids),
        "guests": [_census_json(r) for r in split["guests"]],
        "lineage_agents": [
            {**r.to_json(), "birth_identity": list(r.birth)} for r in lineage
        ],
        "caller_ancestors": caller,
        "blocking_ancestors": blocking,
        "dispatchers": [r.pid for r in dispatchers],
        "service": ownership.service_state(),
    }


def _census_json(record: Any) -> dict[str, Any]:
    birth = tuple(record.birth)
    return {
        "pid": record.pid,
        "birth": birth[0],
        "birth_identity": list(birth),
        "argv": list(record.argv),
    }


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def resolve_source(explicit: str | None, cwd: Path) -> Path | None:
    """A vibecrafted checkout whose Makefile owns ``make install``."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    probe = cwd.resolve()
    candidates.extend([probe, *probe.parents])
    package_checkout = Path(__file__).resolve().parents[2]
    candidates.append(package_checkout)
    for candidate in candidates:
        if (candidate / "Makefile").is_file() and (
            candidate / "scripts" / "vetcoders_install.py"
        ).is_file():
            return candidate
        if explicit and candidate == Path(explicit).expanduser():
            return None
    return None


def install_command(source: Path, pack: Path | None) -> list[str]:
    command = ["make", "-C", str(source), "install"]
    if pack is not None:
        command.append(f"RUNTIME_PACK={pack.resolve()}")
    return command


def resurrect_command(
    agent: Mapping[str, Any], *, python: str, root: str, pack_file: str = ""
) -> list[str]:
    """``spawn interactive-command`` argv: native resume when proven, else fresh."""
    command = [
        python,
        "-m",
        "vibecrafted_core.spawn",
        "interactive-command",
        agent["provider"],
        "--runtime",
        "local-native",
        "--permissions",
        agent.get("permissions") or "auto",
        "--token-budget",
        "unmetered",
        "--operator",
        "none",
        "--continuity",
        "fresh",
        "--skill",
        "resume",
        "--root",
        root,
    ]
    if agent.get("identity") == PROVEN and agent.get("native_session_id"):
        command.extend(["--session", agent["native_session_id"]])
    elif pack_file:
        command.extend(["--file", pack_file])
    if agent.get("model"):
        command.extend(["--model", agent["model"]])
    return command


def _resume_root(
    agent: Mapping[str, Any], run_meta: Callable[[str], dict[str, Any] | None]
) -> str:
    run_id = agent.get("linked_run_id") or ""
    meta = run_meta(run_id) if run_id else None
    recorded = str((meta or {}).get("root") or "")
    return recorded or agent.get("cwd") or ""


def build_plan(
    manifest: Mapping[str, Any],
    *,
    source: Path | None,
    pack: Path | None,
    launcher: Path,
    run_meta: Callable[[str], dict[str, Any] | None] = default_run_meta,
) -> dict[str, Any]:
    """Phases 2-4 as data: what would be killed, installed and brought back."""
    ownership = manifest.get("ownership") or {}
    preflight: list[str] = []
    if not ownership.get("available"):
        preflight.append(ownership.get("reason") or "process census unavailable")
    if source is None:
        preflight.append(
            "no vibecrafted checkout found for `make install`; pass --source <checkout>"
        )
    if pack is not None and not pack.is_file():
        preflight.append(f"runtime pack not found: {pack}")
    resurrect_sessions = []
    for session in manifest["frame"]["sessions"]:
        agents = [
            (pane["id"], pane["tab_name"], pane["pane_command"], pane["agent"])
            for pane in session["panes"]
            if pane.get("agent")
        ] + [(None, a["provider"], "", a) for a in session["unplaced_agents"]]
        resurrect_sessions.append(
            {
                "session": session["name"],
                "layout": "rewrite" if session.get("layout") else "minimal",
                "agents": [
                    {
                        "pane": pane_id,
                        "tab": tab,
                        "provider": agent["provider"],
                        "identity": agent["identity"],
                        "native_session_id": agent.get("native_session_id"),
                        "mode": _resurrect_mode(agent),
                        "root": _resume_root(agent, run_meta),
                        "command": resurrect_command(
                            agent,
                            python="<new-generation-python>",
                            root=_resume_root(agent, run_meta),
                            pack_file="<continuity-pack>"
                            if agent["identity"] != PROVEN
                            else "",
                        )
                        if agent["provider"] in RESURRECTABLE
                        else None,
                    }
                    for pane_id, tab, _command, agent in agents
                ],
            }
        )
    headless = [
        {
            "run_id": run["run_id"],
            "provider": run["agent"]["provider"],
            "action": (
                "left-running (live dispatcher spared)"
                if run.get("spared")
                else "resume --run-id"
                if run["agent"]["identity"] == PROVEN and run["run_id"]
                else "report-only (no proven native session)"
            ),
            "command": [
                str(launcher),
                "resume",
                run["agent"]["provider"],
                "--run-id",
                run["run_id"],
                "--prompt",
                HEADLESS_PROMPT,
            ]
            if not run.get("spared")
            and run["agent"]["identity"] == PROVEN
            and run["run_id"]
            else None,
        }
        for run in manifest.get("headless_runs", [])
    ]
    apps = sorted(
        {
            bundle
            for record in ownership.get("kill", [])
            if (bundle := _app_bundle(record["argv"][0]))
        }
    )
    return {
        "schema": PLAN_SCHEMA,
        "preflight": preflight,
        "ready": not preflight,
        "kill": {
            "service": ownership.get("service"),
            "lineage_agents": [r["pid"] for r in ownership.get("lineage_agents", [])],
            "owned": [r["pid"] for r in ownership.get("kill", [])],
            "spared_guests": [
                {"pid": r["pid"], "argv": r["argv"][:3]}
                for r in ownership.get("guests", [])
            ],
            "headless_spared": [
                {"pid": r["pid"], "run_id": r.get("run_id", ""), "argv": r["argv"][:3]}
                for r in ownership.get("headless_spared", [])
            ],
            "caller_ancestors": ownership.get("caller_ancestors", []),
            "blocking_ancestors": ownership.get("blocking_ancestors", []),
        },
        "install": {
            "source": str(source) if source else None,
            "pack": str(pack) if pack else None,
            "command": install_command(source, pack) if source else None,
        },
        "resurrect": {
            "launcher": str(launcher),
            "sessions": resurrect_sessions,
            "headless": headless,
            "relaunch_apps": apps,
        },
    }


def _resurrect_mode(agent: Mapping[str, Any]) -> str:
    if agent["provider"] not in RESURRECTABLE:
        return "unsupported-provider"
    return "native-resume" if agent["identity"] == PROVEN else "fresh-with-continuity"


def _app_bundle(executable: str) -> str | None:
    parts = Path(executable).parts
    if "Contents" in parts and "MacOS" in parts:
        index = parts.index("Contents")
        bundle = str(Path(*parts[:index]))
        if bundle.endswith(".app") and "/releases/" not in bundle:
            return bundle
    return None


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


def reinstall_runs_root() -> Path:
    from .dispatch.worktrees import canonical_artifact_root

    root = canonical_artifact_root(Path.home(), explicit="vetcoders/vibecrafted")
    return root / "reports" / "reinstall-clean"


def new_run_dir(mode: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = reinstall_runs_root() / f"{stamp}-{mode}-{os.getpid()}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)
    return path


def write_receipt(run_dir: Path, phase: str, status: str, **details: Any) -> Path:
    return write_json(
        run_dir / "receipts" / f"{phase}.json",
        {
            "schema": RECEIPT_SCHEMA,
            "phase": phase,
            "status": status,
            "at": _iso(),
            **details,
        },
    )


def store_manifest(run_dir: Path, manifest: Mapping[str, Any]) -> Path:
    layouts = run_dir / "layouts"
    stored = json.loads(json.dumps(manifest))
    for session in stored["frame"]["sessions"]:
        layout = session.pop("layout", "") or ""
        path = layouts / f"{session['name']}.kdl"
        layouts.mkdir(parents=True, exist_ok=True)
        path.write_text(layout, encoding="utf-8")
        session["layout_file"] = str(path)
    return write_json(run_dir / "manifest.json", stored)


def load_manifest(run_dir: Path) -> dict[str, Any]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    for session in manifest["frame"]["sessions"]:
        layout_file = session.get("layout_file")
        session["layout"] = (
            Path(layout_file).read_text(encoding="utf-8")
            if layout_file and Path(layout_file).is_file()
            else ""
        )
    return manifest


# ---------------------------------------------------------------------------
# Execution: kill, install, resurrect
# ---------------------------------------------------------------------------


def kill_clean(
    manifest: Mapping[str, Any],
    ownership: RuntimeOwnership,
    *,
    table: ProcessTable,
    config_dir: Path,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Phase 2: service stop, lineage agents, then the partitioned owned census.

    ``detail`` is filled as each step lands, so a caller that catches a refusal
    still knows what was already stopped (a stopped service must come back).
    """
    detail = {} if detail is None else detail
    detail["service"] = ownership.stop_service()
    protected = set(ownership.caller_ancestors())
    spared = set(manifest["ownership"].get("headless_spared_pids", []))

    def _spared(pid: int) -> bool:
        # A live headless dispatcher and anything under it — including
        # children forked after the snapshot — stays running (decyzja
        # Macieja 2026-10-10).
        return pid in spared or bool(set(ancestors(table, pid)) & spared)

    lineage = [
        table[e["pid"]]
        for e in manifest["ownership"].get("lineage_agents", [])
        if e["pid"] in table
        and e["pid"] not in protected
        and not _spared(e["pid"])
        and table[e["pid"]].birth[0] == e["birth"]
    ]
    if lineage:
        ownership.terminate(lineage, label="lineage agent process")
    detail["lineage_agents_stopped"] = [r.pid for r in lineage]
    census = list(ownership.census())
    split = partition_census(census, roots=ownership.roots(), config_dir=config_dir)
    owned = [
        ProcessRecord(r.pid, 0, tuple(r.birth), tuple(r.argv))
        for r in split["kill"]
        if not _spared(r.pid)
    ]
    if owned:
        ownership.terminate(owned, label="owned runtime process")
    detail["owned_stopped"] = [r.pid for r in owned]
    detail["guests_spared"] = [r.pid for r in split["guests"]]
    detail["headless_spared"] = sorted(r.pid for r in split["kill"] if _spared(r.pid))
    leftover = [
        r
        for r in partition_census(
            list(ownership.census()), roots=ownership.roots(), config_dir=config_dir
        )["kill"]
        if not _spared(r.pid)
    ]
    detail["leftover"] = [_census_json(r) for r in leftover]
    if leftover:
        raise ReinstallError(
            f"{len(leftover)} owned runtime process(es) remain after teardown"
        )
    return detail


Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_install(
    command: Sequence[str],
    *,
    log_path: Path,
    env: Mapping[str, str],
    runner: Runner = subprocess.run,
) -> int:
    """Phase 3: the standard install, its output kept as an artifact."""
    install_env = {
        k: v for k, v in env.items() if k not in {"PYTHONPATH", "PYTHONHOME"}
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = runner(
            list(command),
            check=False,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=install_env,
            stdin=subprocess.DEVNULL,
            text=True,
        )
    return int(result.returncode)


def frame_binary() -> str:
    env_binary = os.environ.get("VIBECRAFTED_VC_FRAME_BIN", "")
    if env_binary and Path(env_binary).is_file():
        return env_binary
    active = _active_runtime_root()
    if active and (Path(active) / "libexec" / "vc-frame").is_file():
        return str(Path(active) / "libexec" / "vc-frame")
    return shutil.which("vc-frame") or "vc-frame"


def _active_runtime_root() -> str | None:
    from .runtime_paths import (
        GenerationResolutionError,
        _read_active_runtime_root,
        active_runtime_pointer,
    )

    runtime_home = vibecrafted_runtime_home()
    try:
        root = _read_active_runtime_root(
            active_runtime_pointer(runtime_home), runtime_home
        )
    except GenerationResolutionError:
        return None
    return str(root) if root else None


def config_dir() -> Path:
    raw = os.environ.get("VC_FRAME_CONFIG_DIR", "")
    if raw:
        return Path(raw).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "vibecrafted" / "vc-frame"


@dataclass
class ResurrectContext:
    run_dir: Path
    python: str
    frame_bin: str
    socket_dir: Path
    config_dir: Path
    new_root: str | None
    launcher: Path
    env: dict[str, str]
    runner: Runner = subprocess.run
    run_meta: Callable[[str], dict[str, Any] | None] = default_run_meta
    live_sessions: Callable[[], list[str]] | None = None
    wait_seconds: float = 30.0


def _continuity_pack(
    ctx: ResurrectContext, agent: Mapping[str, Any], root: str, key: str
) -> tuple[str, str]:
    aicx = shutil.which("aicx", path=ctx.env.get("PATH"))
    if not aicx:
        return "", "aicx not on PATH; fresh session without a continuity pack"
    target = ctx.run_dir / "continuity" / f"{key}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    result = ctx.runner(
        [
            ctx.python,
            "-m",
            "vibecrafted_core.aicx_session_chain",
            "resume-pack",
            "--agent",
            agent["provider"],
            "--root",
            root,
            "--aicx",
            aicx,
            "--hours",
            "96",
            "--context-file",
            str(target),
            "--meta-file",
            str(target.with_suffix(".meta.json")),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=ctx.env,
        timeout=180,
    )
    if result.returncode != 0 or not target.is_file():
        return "", f"continuity pack failed: {result.stderr.strip()[:200]}"
    return str(target), ""


def _interactive_launch(
    ctx: ResurrectContext, agent: Mapping[str, Any], key: str
) -> tuple[list[str] | None, dict[str, Any]]:
    """Admit one agent through the spawn surface; returns its pane argv."""
    outcome: dict[str, Any] = {
        "provider": agent["provider"],
        "identity": agent["identity"],
    }
    if agent["provider"] not in RESURRECTABLE:
        outcome.update(
            result="skipped", reason="provider has no interactive spawn policy"
        )
        return None, outcome
    root = _resume_root(agent, ctx.run_meta)
    pack, note = ("", "")
    if agent["identity"] != PROVEN:
        pack, note = _continuity_pack(ctx, agent, root, key)
    command = resurrect_command(agent, python=ctx.python, root=root, pack_file=pack)
    prompt = RESUMED_PROMPT if agent["identity"] == PROVEN else FRESH_PROMPT
    result = ctx.runner(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=ctx.env,
        input=prompt,
        timeout=120,
    )
    outcome.update(root=root, admission_command=command, continuity_pack=pack or None)
    if note:
        outcome["degraded"] = note
    if result.returncode != 0:
        outcome.update(result="refused", reason=result.stderr.strip()[-500:])
        return None, outcome
    launch = (
        shlex.split(result.stdout.strip().splitlines()[-1])
        if result.stdout.strip()
        else []
    )
    if not launch:
        outcome.update(result="refused", reason="spawn printed no launch command")
        return None, outcome
    outcome.update(
        result="native-resume"
        if agent["identity"] == PROVEN
        else "fresh-with-continuity",
        native_session_id=agent.get("native_session_id"),
        launch=launch,
    )
    return launch, outcome


def create_session(
    ctx: ResurrectContext, name: str, layout_file: Path
) -> dict[str, Any]:
    """Create one detached Frame session from a layout.

    ``--layout`` (not ``--new-session-with-layout``) survives the ``attach``
    subcommand; ``attach --create-background`` is the only terminal-free create.
    """
    env = frame_client_env(without_python_path(ctx.env), ctx.socket_dir)
    result = ctx.runner(
        [
            ctx.frame_bin,
            "--layout",
            str(layout_file),
            "attach",
            "--create-background",
            name,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    outcome = {
        "session": name,
        "rc": result.returncode,
        "stderr": result.stderr.strip()[-400:],
    }
    if "already exists" in (result.stdout + result.stderr):
        outcome["result"] = "already-exists"
        return outcome
    deadline = time.monotonic() + ctx.wait_seconds
    live = ctx.live_sessions or (
        lambda: CliFrameProbe(ctx.frame_bin, ctx.socket_dir).list_sessions()
    )
    while time.monotonic() < deadline:
        if name in live():
            outcome["result"] = "live"
            return outcome
        time.sleep(0.5)
    outcome["result"] = "not-live"
    return outcome


def resurrect(manifest: Mapping[str, Any], ctx: ResurrectContext) -> dict[str, Any]:
    """Phase 4: rebuild sessions, agents and apps from the fresh generation."""
    report: dict[str, Any] = {
        "sessions": [],
        "headless": [],
        "apps": [],
        "service": None,
    }
    already = set((ctx.live_sessions or (list))())
    old_roots = list((manifest.get("runtime") or {}).get("running_roots") or [])
    sessions = sorted(
        manifest["frame"]["sessions"], key=lambda s: (s["name"] != "vc-host", s["name"])
    )
    for session in sessions:
        name = session["name"]
        entry: dict[str, Any] = {"session": name, "panes": []}
        report["sessions"].append(entry)
        if name in already:
            entry["result"] = "already-live"
            continue
        launches: list[PaneLaunch] = []
        candidates = [(p, p.get("agent")) for p in session["panes"] if p.get("agent")]
        candidates += [
            ({"id": None, "tab_name": a["provider"], "pane_command": ""}, a)
            for a in session.get("unplaced_agents", [])
        ]
        for index, (pane, agent) in enumerate(candidates):
            key = f"{name}-{pane.get('id') if pane.get('id') is not None else f'u{index}'}"
            argv, outcome = _interactive_launch(ctx, agent, key)
            outcome.update(old_pane=pane.get("id"), tab=pane.get("tab_name"))
            entry["panes"].append(outcome)
            if argv:
                launches.append(
                    PaneLaunch(
                        key=key,
                        pane_command=str(pane.get("pane_command") or ""),
                        tab_name=str(pane.get("tab_name") or agent["provider"]),
                        argv=tuple(argv),
                        cwd=outcome.get("root") or agent.get("cwd") or "",
                        title=str(pane.get("title") or ""),
                    )
                )
        layout_text = session.get("layout") or ""
        try:
            rewritten, matches = rewrite_layout(
                layout_text,
                launches=launches,
                config_dir=str(ctx.config_dir),
                old_roots=old_roots,
                new_root=ctx.new_root,
            )
            entry["layout"] = "rewritten"
        except KdlShapeError as exc:
            rewritten = minimal_layout(session.get("cwd") or str(Path.home()), launches)
            matches = [{"key": launch.key, "appended_tab": True} for launch in launches]
            entry["layout"] = f"minimal ({exc})"
        layout_file = ctx.run_dir / "resurrect-layouts" / f"{name}.kdl"
        layout_file.parent.mkdir(parents=True, exist_ok=True)
        layout_file.write_text(rewritten, encoding="utf-8")
        entry["layout_file"] = str(layout_file)
        entry["layout_matches"] = matches
        entry.update(create_session(ctx, name, layout_file))
    for run in manifest.get("headless_runs", []):
        agent = run["agent"]
        if agent["identity"] != PROVEN or not run.get("run_id"):
            report["headless"].append(
                {
                    "run_id": run.get("run_id"),
                    "result": "report-only",
                    "reason": agent.get("unknown_reason"),
                }
            )
            continue
        command = [
            str(ctx.launcher),
            "resume",
            agent["provider"],
            "--run-id",
            run["run_id"],
            "--prompt",
            HEADLESS_PROMPT,
        ]
        try:
            result = ctx.runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                env=ctx.env,
                stdin=subprocess.DEVNULL,
                timeout=180,
            )
            report["headless"].append(
                {
                    "run_id": run["run_id"],
                    "rc": result.returncode,
                    "result": "resumed" if result.returncode == 0 else "refused",
                    "stderr": result.stderr.strip()[-400:],
                }
            )
        except subprocess.TimeoutExpired:
            report["headless"].append(
                {"run_id": run["run_id"], "result": "launch-watch-timeout"}
            )
    return report


def relaunch_after(
    plan: Mapping[str, Any], ctx: ResurrectContext, service: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Bring back the service and the App the kill phase stopped."""
    out: dict[str, Any] = {"service": None, "apps": []}
    if service and service.get("action") == "stopped" and service.get("was_live"):
        result = ctx.runner(
            [str(ctx.launcher), "server", "service", "start"],
            check=False,
            capture_output=True,
            text=True,
            env=ctx.env,
            timeout=120,
        )
        out["service"] = {
            "rc": result.returncode,
            "stderr": result.stderr.strip()[-300:],
        }
    for bundle in plan["resurrect"].get("relaunch_apps", []):
        result = ctx.runner(
            ["/usr/bin/open", bundle],
            check=False,
            capture_output=True,
            text=True,
            env=ctx.env,
            timeout=30,
        )
        out["apps"].append({"bundle": bundle, "rc": result.returncode})
    return out


# ---------------------------------------------------------------------------
# Orchestration and CLI
# ---------------------------------------------------------------------------


def _installer() -> Any:
    from .doctor import _installer_module

    return _installer_module()


def _live_dependencies() -> tuple[ProcessProbe, RuntimeOwnership | None, Any]:
    if sys.platform != "darwin":
        raise ReinstallError("reinstall --clean v1 executes on macOS only")
    installer = _installer()
    return DarwinProcessProbe(installer), InstallerOwnership(installer), installer


def take_live_snapshot(*, mode: str, save_sessions: bool) -> dict[str, Any]:
    procs, ownership, _ = _live_dependencies()
    socket_dir = frame_socket_dir()
    table = procs.table()
    session_binaries = {}
    probe_binary = frame_binary()
    frame = CliFrameProbe(probe_binary, socket_dir)
    for name in frame.list_sessions():
        server = frame_server_for(table, socket_dir, name)
        if server:
            session_binaries[name] = server.argv[0]
    frame.session_binaries = session_binaries

    class _CachedProbe:
        def table(self) -> ProcessTable:
            return table

        def cwd(self, pids: Sequence[int]) -> dict[int, str]:
            return procs.cwd(pids)

    return snapshot_world(
        procs=_CachedProbe(),
        frame=frame,
        ownership=ownership,
        stores=ProviderStores.default(),
        socket_dir=socket_dir,
        config_dir=config_dir(),
        active_root=_active_runtime_root(),
        mode=mode,
        save_sessions=save_sessions,
    )


def render_summary(
    manifest: Mapping[str, Any], plan: Mapping[str, Any], run_dir: Path
) -> str:
    summary = manifest["summary"]
    runtime = manifest["runtime"]
    lines = [
        "⚒  vibecrafted reinstall --clean"
        + (" (dry run)" if manifest["mode"] == "dry-run" else ""),
        f"  active generation : {runtime.get('active_root')}",
        f"  running generation: {', '.join(runtime.get('running_roots') or []) or '-'}"
        + ("   ← drift" if runtime.get("drift") else ""),
        (
            f"  sessions {summary['sessions']} · panes {summary['panes']} · agents {summary['agents']}"
            f" ({summary['agents_proven']} proven, {summary['agents_unknown']} unknown)"
            f" · headless runs {summary['headless_runs']}"
        ),
    ]
    for session in manifest["frame"]["sessions"]:
        lines.append(f"  ▸ {session['name']}")
        for pane in session["panes"]:
            agent = pane.get("agent")
            if not agent:
                continue
            ident = agent.get("native_session_id") or "UNKNOWN"
            how = agent.get("recipe") or agent.get("unknown_reason")
            lines.append(
                f"      {pane['tab_name']:<14} {agent['provider']:<7} {ident}  [{how}]"
            )
        for agent in session["unplaced_agents"]:
            ident = agent.get("native_session_id") or "UNKNOWN"
            lines.append(
                f"      (unplaced)     {agent['provider']:<7} {ident}  [{agent.get('recipe') or agent.get('unknown_reason')}]"
            )
    for run in manifest["headless_runs"]:
        agent = run["agent"]
        lines.append(
            f"  ▸ headless {run['run_id']}: {agent['provider']} "
            f"{agent.get('native_session_id') or 'UNKNOWN'} [{agent.get('recipe') or agent.get('unknown_reason')}]"
        )
    kill = plan["kill"]
    lines.append(
        f"  kill: {len(kill['owned'])} owned + {len(kill['lineage_agents'])} lineage agents;"
        f" spared guests {len(kill['spared_guests'])}"
    )
    for guest in kill["spared_guests"]:
        lines.append(f"      spared: {guest['pid']} {' '.join(guest['argv'])[:110]}")
    if kill.get("blocking_ancestors"):
        lines.append(
            f"  caller tree {kill['blocking_ancestors']} runs under the old runtime;"
            " only the detached executor stops it"
        )
    lines.append(
        f"  install: {' '.join(plan['install']['command'] or ['<no source>'])}"
    )
    if plan["preflight"]:
        lines.append("  preflight REFUSED:")
        lines.extend(f"    - {item}" for item in plan["preflight"])
    lines.append(f"  receipts: {run_dir}")
    return "\n".join(lines) + "\n"


def _confirm(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print(
            "reinstall --clean stops every runtime process; pass --yes to confirm "
            "non-interactively.",
            file=sys.stderr,
        )
        return False
    reply = input(
        "This stops all Vibecrafted processes and agents, reinstalls, and "
        "resurrects them. Type 'reinstall' to continue: "
    )
    return reply.strip() == "reinstall"


def _detach_executor(run_dir: Path, source: Path, pack: Path | None) -> int:
    """Start the executor in its own session; it outlives this pane and its Frame."""
    log = run_dir / "logs" / "executor.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "vibecrafted_core.reinstall_clean",
        "execute",
        "--run-dir",
        str(run_dir),
        "--parent-pid",
        str(os.getpid()),
        "--source",
        str(source),
    ]
    if pack is not None:
        command += ["--pack", str(pack)]
    with log.open("ab") as handle:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            cwd=str(Path.home()),
            env=scrubbed_env(os.environ),
        )
    print(f"executor detached: pid {process.pid}")
    print(f"  follow: tail -f {log}")
    print(f"  receipts: {run_dir}/receipts/")
    return 0


def execute(
    run_dir: Path, *, source: str | None, pack: str | None, parent_pid: int
) -> int:
    """Executor: authoritative snapshot, kill, install, then the new launcher."""
    deadline = time.monotonic() + 15
    while parent_pid > 0 and os.getppid() == parent_pid and time.monotonic() < deadline:
        time.sleep(0.2)
    print(f"[{_iso()}] executor {os.getpid()} ppid {os.getppid()}", flush=True)
    procs, ownership, _ = _live_dependencies()
    manifest = take_live_snapshot(mode="execute", save_sessions=True)
    store_manifest(run_dir, manifest)
    source_path = resolve_source(source, Path.home()) if source else None
    pack_path = Path(pack).expanduser() if pack else None
    launcher = vibecrafted_launcher_bin() / "vibecrafted"
    plan = build_plan(manifest, source=source_path, pack=pack_path, launcher=launcher)
    write_json(run_dir / "plan.json", plan)
    write_receipt(run_dir, "phase-1-snapshot", "ok", summary=manifest["summary"])
    if not plan["ready"]:
        write_receipt(run_dir, "phase-2-kill", "refused", preflight=plan["preflight"])
        print("preflight refused; nothing was stopped", flush=True)
        return 2
    kill: dict[str, Any] = {}
    try:
        kill_clean(
            manifest,
            ownership,
            table=procs.table(),
            config_dir=config_dir(),
            detail=kill,
        )
    except (ReinstallError, OSError) as exc:
        write_receipt(run_dir, "phase-2-kill", "failed", error=str(exc), **kill)
        write_receipt(run_dir, "phase-3-install", "skipped", reason="kill clean failed")
        print(f"kill clean failed: {exc}; install skipped, resurrecting", flush=True)
    else:
        write_receipt(run_dir, "phase-2-kill", "ok", **kill)
        install_log = run_dir / "logs" / "install.log"
        install_rc = run_install(
            plan["install"]["command"],
            log_path=install_log,
            env=scrubbed_env(os.environ),
        )
        write_receipt(
            run_dir,
            "phase-3-install",
            "ok" if install_rc == 0 else "failed",
            rc=install_rc,
            command=plan["install"]["command"],
            log=str(install_log),
        )
    write_json(run_dir / "kill-service.json", {"service": kill.get("service")})
    handover = without_python_path(scrubbed_env(os.environ))
    if launcher.is_file():
        print(f"[{_iso()}] handing over to {launcher}", flush=True)
        os.execve(
            str(launcher),
            [str(launcher), "reinstall", "--resurrect", str(run_dir)],
            handover,
        )
    print("launcher missing; resurrecting with the current interpreter", flush=True)
    return resurrect_main(run_dir)


def resurrect_main(run_dir: Path) -> int:
    manifest = load_manifest(run_dir)
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    service = {}
    service_file = run_dir / "kill-service.json"
    if service_file.is_file():
        service = (
            json.loads(service_file.read_text(encoding="utf-8")).get("service") or {}
        )
    socket_dir = frame_socket_dir()
    frame_bin = frame_binary()
    ctx = ResurrectContext(
        run_dir=run_dir,
        python=os.environ.get("VIBECRAFTED_PYTHON") or sys.executable,
        frame_bin=frame_bin,
        socket_dir=socket_dir,
        config_dir=config_dir(),
        new_root=_active_runtime_root(),
        launcher=vibecrafted_launcher_bin() / "vibecrafted",
        env=scrubbed_env(os.environ),
        live_sessions=lambda: CliFrameProbe(frame_bin, socket_dir).list_sessions(),
    )
    restored = resurrect(manifest, ctx)
    restored.update(relaunch_after(plan, ctx, service))
    failures = [
        s["session"]
        for s in restored["sessions"]
        if s.get("result") not in {"live", "already-live"}
    ]
    write_receipt(
        run_dir,
        "phase-4-resurrect",
        "ok" if not failures else "partial",
        generation=ctx.new_root,
        failed_sessions=failures,
        **restored,
    )
    print(
        json.dumps(
            {
                "resurrected": [s["session"] for s in restored["sessions"]],
                "failed": failures,
                "receipts": str(run_dir / "receipts"),
            },
            indent=2,
        )
    )
    return 0 if not failures else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibecrafted reinstall",
        description=(
            "Clean reinstall: snapshot Frame sessions and agents, stop every runtime "
            "process with identity proof, install, and resurrect sessions and agents "
            "(native resume where the session id is proven) from the new generation."
        ),
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="kill-clean reinstall with resurrection (v1: the only mode)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="snapshot + plan only; stops, installs and starts nothing",
    )
    parser.add_argument(
        "--pack",
        default="",
        help="explicit Runtime Pack tarball for make install RUNTIME_PACK=",
    )
    parser.add_argument(
        "--source",
        default="",
        help="vibecrafted checkout that owns `make install` (default: discovered)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm the destructive run without a prompt",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="run the executor in this process (only from outside Frame)",
    )
    parser.add_argument(
        "--json", action="store_true", help="print manifest and plan as JSON"
    )
    parser.add_argument(
        "--resurrect",
        default="",
        metavar="RUN_DIR",
        help="phase 4 only, from a recorded run directory",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "execute":
        inner = argparse.ArgumentParser(prog="reinstall_clean execute")
        inner.add_argument("--run-dir", required=True)
        inner.add_argument("--parent-pid", type=int, default=0)
        inner.add_argument("--source", default="")
        inner.add_argument("--pack", default="")
        ns = inner.parse_args(raw[1:])
        return execute(
            Path(ns.run_dir),
            source=ns.source or None,
            pack=ns.pack or None,
            parent_pid=ns.parent_pid,
        )
    args = build_parser().parse_args(raw)
    if args.resurrect:
        return resurrect_main(Path(args.resurrect).expanduser())
    if not args.clean:
        print(
            "reinstall supports --clean only; for an in-place update use `vibecrafted update`.",
            file=sys.stderr,
        )
        return 2
    try:
        manifest = take_live_snapshot(
            mode="dry-run" if args.dry_run else "preflight", save_sessions=False
        )
    except ReinstallError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    source = resolve_source(args.source or None, Path.cwd())
    pack = Path(args.pack).expanduser() if args.pack else None
    plan = build_plan(
        manifest,
        source=source,
        pack=pack,
        launcher=vibecrafted_launcher_bin() / "vibecrafted",
    )
    run_dir = new_run_dir("dry-run" if args.dry_run else "run")
    store_manifest(run_dir, manifest)
    write_json(run_dir / "plan.json", plan)
    write_receipt(
        run_dir,
        "phase-1-snapshot",
        "ok",
        summary=manifest["summary"],
        mode=manifest["mode"],
    )
    if args.json:
        print(
            json.dumps(
                {
                    "manifest": json.loads((run_dir / "manifest.json").read_text()),
                    "plan": plan,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(render_summary(manifest, plan, run_dir), end="")
    if args.dry_run:
        return 0 if plan["ready"] else 2
    if not plan["ready"]:
        print("✗ preflight refused; nothing was stopped.", file=sys.stderr)
        return 2
    if not _confirm(args):
        return 1
    assert source is not None  # plan["ready"] proves a checkout
    if args.foreground:
        blocking = manifest["ownership"].get("blocking_ancestors") or []
        if blocking:
            print(
                "✗ --foreground would leave the process tree that runs this command "
                f"alive (pids {blocking}); run without --foreground to detach.",
                file=sys.stderr,
            )
            return 2
        return execute(
            run_dir, source=str(source), pack=args.pack or None, parent_pid=-1
        )
    return _detach_executor(run_dir, source, pack)


if __name__ == "__main__":
    raise SystemExit(main())
