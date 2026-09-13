"""Consumer-side AICX session chain for Vibecrafted resume.

AICX MCP 0.12.x is retrieval-only (search / read / rank / steer / intents /
index_status). The sessions → extract → continuity chain lives on the CLI.
This module is the vibecrafted-owned contract those MCP tools must satisfy,
plus the resume pack assembler that never auto-attaches a provider session.

Native provider resume happens only when the operator already passed
``--session``. The pack is continuity transport, not a marriage with the
last same-agent match.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .runtime_receipt import find_git_dir, owner_repo_from_git

SCHEMA = "vibecrafted.resume.aicx_fallback.v2"

# Hard cap for the ENTIRE injected pack, counted in Unicode characters.
# Headers, notices, source links and the operator instruction are all inside
# this budget; nothing is measured "except the frame".
MAX_PACK_CHARS = 18_000

# Founder's rooted proposal from their own example. 720h was explicitly
# rejected as too much history for a fresh resume. Callers still override.
DEFAULT_RESUME_AICX_HOURS = 96

# AICX scans live session sources automatically only for windows <= 48h.
# A 96h default therefore has to ask for freshness on purpose.
AICX_AUTO_LIVE_HOURS = 48

# Structured intent selection: enough mission to act on, few enough entries
# that the character budget buys whole items instead of a prefix.
PROJECT_INTENT_LIMIT = 8

# Measured on this catalog (2026-09-13, aicx 0.13.0): the durable-catalog query
# answers in ~3s while the same query with --live costs ~55s on a large repo
# and ~6s on a small one. So the durable answer is the pack's floor and the
# live scan is a bounded supplement -- never a reason to widen the first
# timeout until a fast query is indistinguishable from a hung one.
INTENTS_TIMEOUT_SECONDS = 30.0
INTENTS_LIVE_TIMEOUT_SECONDS = 45.0

# Expected AICX MCP names. AICX owns the server; this list is the consumer
# contract resume and TUI will call once those tools exist.
MCP_SESSION_CHAIN_TOOLS: tuple[str, ...] = (
    "aicx_sessions",
    "aicx_session_show",
    "aicx_extract",
    "aicx_continuity",
)

# Retrieval tools that must keep working; this cut does not rebuild indexes.
MCP_RETRIEVAL_TOOLS: tuple[str, ...] = (
    "aicx_search",
    "aicx_read",
    "aicx_rank",
    "aicx_steer",
    "aicx_intents",
    "aicx_index_status",
)

EmptyKind = Literal[
    "none",
    "empty_project",
    "missing_filter",
    "bad_project",
    # Retrieval never completed. Absence of sessions is unproven, so the pack
    # must not print "this project has no sessions in this window".
    "retrieval_unavailable",
    # No canonical owner/repo for this checkout. We refuse to guess a filter
    # from the directory basename.
    "unknown_identity",
]

RECOVER_FORBIDDEN: tuple[str, ...] = (
    "recover previous session",
    "recover session",
    "continue that session",
    "prefer native resume",
    "native resume of that session",
    "resume that session",
    "odzyskaj tamtą sesję",
)


class SessionChainError(Exception):
    """Loud session-chain failure. Never collapse these into scanned=0."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        scanned: int = 0,
        matched: int = 0,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.scanned = scanned
        self.matched = matched
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "kind": self.kind,
            "message": self.message,
            "scanned": self.scanned,
            "matched": self.matched,
            "details": self.details,
        }


@dataclass(frozen=True)
class SessionRecord:
    """One session row, same fields as ``aicx sessions list --format json``."""

    session_id: str
    agent: str = ""
    project: str = ""
    repo_path: str = ""
    title: str = ""
    updated_at: str = ""
    source_path: str = ""
    live: bool = False

    @classmethod
    def from_mapping(cls, item: Mapping[str, Any]) -> SessionRecord | None:
        if not isinstance(item, Mapping):
            return None
        session_id = str(item.get("session_id") or item.get("id") or "").strip()
        if not session_id:
            return None
        source = str(item.get("source_path") or "")
        live_flag = item.get("live")
        if isinstance(live_flag, bool):
            live = live_flag
        else:
            live = bool(source) and Path(source).exists()
        return cls(
            session_id=session_id,
            agent=str(item.get("agent") or ""),
            project=str(item.get("project") or ""),
            repo_path=str(item.get("repo_path") or item.get("cwd") or ""),
            title=str(item.get("title") or ""),
            updated_at=str(item.get("updated_at") or item.get("started_at") or ""),
            source_path=source,
            live=live,
        )


@dataclass
class SessionListResult:
    sessions: list[SessionRecord]
    project_filter: str
    empty_kind: EmptyKind
    scanned: int
    matched: int
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "project_filter": self.project_filter,
            "empty_kind": self.empty_kind,
            "scanned": self.scanned,
            "matched": self.matched,
            "warnings": list(self.warnings),
            "sessions": [asdict(row) for row in self.sessions],
        }


@dataclass(frozen=True)
class IntentItem:
    """One AICX intent entry with its provenance kept attached.

    ``agent``/``session_id``/``source_chunk`` and the three claim flags travel
    with the summary on purpose. The ``user_msg`` frame carries operator agent
    briefs and peer transport as well as direct human input, so nothing here
    may be re-labelled as a Founder decision downstream.
    """

    kind: str = ""
    summary: str = ""
    context: str = ""
    project: str = ""
    agent: str = ""
    date: str = ""
    session_id: str = ""
    source_chunk: str = ""
    claim_scope: str = ""
    freshness_contract: str = ""
    verification_state: str = ""

    @classmethod
    def from_mapping(cls, item: Mapping[str, Any]) -> IntentItem | None:
        if not isinstance(item, Mapping):
            return None
        summary = str(item.get("summary") or "").strip()
        if not summary:
            return None
        return cls(
            kind=str(item.get("kind") or ""),
            summary=summary,
            context=str(item.get("context") or ""),
            project=str(item.get("project") or ""),
            agent=str(item.get("agent") or ""),
            date=str(item.get("date") or item.get("timestamp") or ""),
            session_id=str(item.get("session_id") or ""),
            source_chunk=str(item.get("source_chunk") or ""),
            claim_scope=str(item.get("claim_scope") or ""),
            freshness_contract=str(item.get("freshness_contract") or ""),
            verification_state=str(item.get("verification_state") or ""),
        )


@dataclass
class ProjectIntents:
    """Structured project mission plus the stats that make omission visible."""

    items: list[IntentItem] = field(default_factory=list)
    available: int = 0
    limit: int = 0
    complete: bool = False
    matched_buckets: list[str] = field(default_factory=list)
    match_mode: str = ""
    identity_source: str = ""
    live: bool = False
    warnings: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def returned(self) -> int:
        return len(self.items)

    @property
    def omitted(self) -> int:
        return max(0, self.available - self.returned)

    @classmethod
    def from_json(cls, raw: str, *, live: bool) -> ProjectIntents:
        payload = json.loads(raw)
        if not isinstance(payload, Mapping):
            raise TypeError("aicx intents --emit json did not return an object")
        # ``results`` is a COUNT, ``items`` is the collection. Iterating both
        # would either double-count the mission or blow up on an int.
        rows = payload.get("items")
        raw_items = list(rows) if isinstance(rows, list) else []
        completeness = payload.get("completeness")
        completeness = completeness if isinstance(completeness, Mapping) else {}
        scope = completeness.get("scope")
        scope = scope if isinstance(scope, Mapping) else {}
        items = [
            parsed
            for parsed in (IntentItem.from_mapping(row) for row in raw_items)
            if parsed is not None
        ]
        warnings = completeness.get("warnings")
        return cls(
            items=items,
            available=int(completeness.get("available_before_limit") or len(items)),
            limit=int(completeness.get("requested_limit") or 0),
            complete=bool(completeness.get("complete")),
            matched_buckets=[
                str(bucket)
                for bucket in (completeness.get("matched_project_buckets") or [])
            ],
            match_mode=str(scope.get("match_mode") or ""),
            identity_source=str(completeness.get("identity_source") or ""),
            live=live,
            warnings=[str(item) for item in warnings]
            if isinstance(warnings, list)
            else [],
            raw=raw,
        )


@dataclass
class ResumePack:
    context_file: Path
    meta_file: Path
    full_context_file: Path
    session_id: str
    session_count: int
    mode: Literal["new_session", "native_resume"]
    project_filter: str
    empty_kind: EmptyKind
    degradations: list[str]
    body: str

    def stdout_lines(self) -> list[str]:
        return [
            f"SESSION_ID={self.session_id}",
            f"CONTEXT_FILE={self.context_file}",
            f"FULL_CONTEXT_FILE={self.full_context_file}",
            f"SESSION_COUNT={self.session_count}",
            f"MODE={self.mode}",
            f"EMPTY_KIND={self.empty_kind}",
        ]


@dataclass(frozen=True)
class ProjectIdentity:
    """Canonical repository identity behind an AICX ``-p`` filter.

    ``owner_repo`` is empty when nothing resolved. That is a real answer, not a
    prompt to fall back to the directory basename: ``/vibecrafted`` would union
    every same-named checkout across every org in the catalog.
    """

    root: Path
    owner_repo: str
    source: Literal["git_origin", "unknown"]

    @property
    def known(self) -> bool:
        return bool(self.owner_repo)

    @property
    def project_filter(self) -> str:
        return self.owner_repo

    def describe(self) -> str:
        if self.known:
            return f"`{self.owner_repo}` (canonical git origin owner/repo)"
        return (
            "unresolved (no git origin for this checkout; refusing a "
            "basename filter that would union foreign orgs)"
        )


def _git_common_dir(git_dir: Path) -> Path:
    """Resolve a worktree ``.git/worktrees/<name>`` back to the shared git dir.

    A linked worktree's git dir carries ``HEAD`` and ``index`` but no
    ``config``; ``commondir`` is the only pointer back to the checkout that
    owns the origin remote. Every dispatched worker runs inside such a
    worktree, so without this hop identity always reads as unknown.
    """
    try:
        raw = (
            (git_dir / "commondir")
            .read_text(encoding="utf-8", errors="replace")
            .strip()
        )
    except OSError:
        return git_dir
    if not raw:
        return git_dir
    target = Path(raw)
    if not target.is_absolute():
        target = git_dir / target
    try:
        resolved = target.resolve()
    except OSError:
        return git_dir
    return resolved if resolved.is_dir() else git_dir


def resolve_project_identity(root: str | Path) -> ProjectIdentity:
    """Canonical ``owner/repo`` for a checkout, linked worktrees included."""
    path = Path(root)
    try:
        path = path.resolve()
    except OSError:
        pass
    owner_repo = owner_repo_from_git(path) or ""
    if not owner_repo:
        git_dir = find_git_dir(path)
        if git_dir is not None:
            common = _git_common_dir(git_dir)
            if common != git_dir:
                owner_repo = owner_repo_from_git(common.parent) or ""
    return ProjectIdentity(
        root=path,
        owner_repo=owner_repo,
        source="git_origin" if owner_repo else "unknown",
    )


def project_filter_for_root(root: str | Path) -> str:
    """Canonical ``owner/repo`` AICX filter, or ``""`` when it cannot resolve.

    The empty string is deliberate. Callers must surface ``unknown_identity``
    rather than substitute a cross-org basename union.
    """
    return resolve_project_identity(root).project_filter


def matches_exact_project(
    record: SessionRecord,
    *,
    root: Path,
    project_filter: str,
) -> bool:
    """Exact identity.

    With a canonical ``owner/repo`` filter, ``vetcoders/vibecrafted`` never
    matches ``someone-else/vibecrafted``, while sibling worktrees of the same
    repository still match through their catalog bucket. The legacy bare-name
    branch stays for callers that have no git origin to offer.
    """
    token = project_filter.strip().lstrip("/").lower()
    if record.repo_path:
        try:
            if Path(record.repo_path).resolve() == root:
                return True
        except OSError:
            pass
    project = record.project.lower().strip()
    if "/" in token:
        return project == token
    name = root.name.lower()
    if name and name != token:
        token = name
    if record.repo_path and Path(record.repo_path).name.lower() == token:
        return True
    return bool(project) and (project == token or project.endswith("/" + token))


def pack_contains_recover_instruction(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in RECOVER_FORBIDDEN)


def mcp_session_chain_contract() -> dict[str, Any]:
    """Stable consumer contract for the missing AICX MCP session tools."""
    return {
        "schema": "vibecrafted.aicx.session_chain.v1",
        "mcp_tools": list(MCP_SESSION_CHAIN_TOOLS),
        "retrieval_tools_unchanged": list(MCP_RETRIEVAL_TOOLS),
        "required_list_fields": [
            "agent",
            "project",
            "session_id",
            "title",
            "updated_at",
            "live",
        ],
        "project_filter": "canonical owner/repo from git origin; never a basename union",
        "empty_with_project": "empty_project",
        "empty_without_project": "missing_filter",
        "unknown_project": "bad_project",
        "retrieval_outage": "retrieval_unavailable",
        "unresolvable_identity": "unknown_identity",
        "failure_kinds": [
            "timeout",
            "aicx_missing",
            "mixed_workstream",
            "stale_index",
            "bad_project",
            "retrieval_failed",
        ],
        "default_hours": DEFAULT_RESUME_AICX_HOURS,
        "auto_live_hours": AICX_AUTO_LIVE_HOURS,
        "max_pack_chars": MAX_PACK_CHARS,
        "native_resume": "only when operator already supplied --session",
        "pack_must_not": list(RECOVER_FORBIDDEN),
        "continuity_sections": [
            "NOW",
            "PEERS",
            "DECISIONS",
            "TASKS",
            "SOURCES",
            "INDEX HEALTH",
        ],
    }


class SessionChain:
    """Transport-agnostic session chain. CLI today; MCP when AICX ships it."""

    def list_sessions(
        self,
        *,
        project: str | None,
        root: Path | None = None,
        agent: str | None = None,
        hours: int = DEFAULT_RESUME_AICX_HOURS,
        limit: int = 40,
    ) -> SessionListResult:
        raise NotImplementedError

    def show_session(self, session_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def extract_session(
        self,
        agent: str,
        session_id: str,
        *,
        conversation: bool = True,
    ) -> str:
        raise NotImplementedError

    def continuity_pack(
        self,
        *,
        project: str,
        hours: int = DEFAULT_RESUME_AICX_HOURS,
        for_inject: bool = True,
    ) -> str:
        raise NotImplementedError


RunFn = Callable[[Sequence[str], float, Path | None], tuple[int, str, str]]


def _default_run(
    cmd: Sequence[str],
    timeout: float,
    cwd: Path | None,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(cwd) if cwd is not None else None,
        )
    except FileNotFoundError:
        return 127, "", "not_found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _merge_project_intents(
    durable: ProjectIntents,
    live: ProjectIntents,
    *,
    limit: int,
) -> ProjectIntents:
    """Union both retrievals newest-first, once per entry.

    The live scan surfaces open sessions the durable catalog has not admitted
    yet, and the durable catalog holds closed history the live scan skips.
    Neither is a superset, so the pack carries both -- deduplicated, because
    the same session appearing twice would read as two separate missions.
    """
    merged: list[IntentItem] = []
    seen: set[tuple[str, str]] = set()
    for item in [*live.items, *durable.items]:
        key = (item.session_id, item.summary[:120])
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    merged.sort(key=lambda entry: entry.date or "", reverse=True)
    warnings = list(durable.warnings)
    warnings.extend(w for w in live.warnings if w not in warnings)
    return ProjectIntents(
        items=merged[:limit],
        available=max(durable.available, live.available, len(merged)),
        limit=limit,
        complete=durable.complete and live.complete,
        matched_buckets=sorted({*durable.matched_buckets, *live.matched_buckets}),
        match_mode=live.match_mode or durable.match_mode,
        identity_source=live.identity_source or durable.identity_source,
        live=True,
        warnings=warnings,
        raw=(
            "## durable catalog retrieval\n\n"
            + durable.raw
            + "\n\n## live supplement retrieval\n\n"
            + live.raw
        ),
    )


def classify_aicx_failure(code: int, out: str, err: str) -> str:
    """Name what actually happened to an AICX call.

    A timeout is a retrieval outage, never proof that a project has no
    history, and AICX's mixed-workstream refusal is a guard doing its job --
    not a rejected project filter. The refusal text contains the word
    "candidate", so it is matched before any generic ambiguity check.
    """
    blob = f"{err}\n{out}".lower()
    if code == 124 or "timeout" in blob:
        return "timeout"
    if code == 127 or "not_found" in blob:
        return "aicx_missing"
    if "mixed-workstream" in blob or "distill_mixed" in blob:
        return "mixed_workstream"
    if "stale" in blob:
        return "stale_index"
    if (
        "unknown project" in blob
        or "no such project" in blob
        or "unexpected argument" in blob
        or "ambiguous" in blob
    ):
        return "bad_project"
    return "retrieval_failed"


class CliSessionChain(SessionChain):
    """AICX CLI transport. Used until MCP grows the session-chain tools."""

    def __init__(
        self,
        aicx_bin: str,
        *,
        runner: RunFn | None = None,
    ) -> None:
        self.aicx_bin = aicx_bin
        self._run = runner or _default_run

    def list_sessions(
        self,
        *,
        project: str | None,
        root: Path | None = None,
        agent: str | None = None,
        hours: int = DEFAULT_RESUME_AICX_HOURS,
        limit: int = 40,
    ) -> SessionListResult:
        if not project:
            return SessionListResult(
                sessions=[],
                project_filter="",
                empty_kind="missing_filter",
                scanned=0,
                matched=0,
                warnings=[
                    (
                        "missing_filter: sessions list without a canonical "
                        "owner/repo filter is not a repo answer; pass exact "
                        "-p <owner>/<repo> or --root"
                    )
                ],
            )

        since = (
            (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours))
            .date()
            .isoformat()
        )
        warnings: list[str] = []
        raw_items: list[dict[str, Any]] = []
        listing_completed = False
        list_attempts: list[tuple[list[str], Path | None]] = []
        if root is not None:
            list_attempts.append((["--cwd"], root))
        list_attempts.append(([], None))

        for flags, cwd in list_attempts:
            cmd = [
                self.aicx_bin,
                "sessions",
                "list",
                "--format",
                "json",
                "--since",
                since,
                "--limit",
                str(limit),
                *flags,
            ]
            if agent:
                cmd.extend(["--agent", agent])
            code, out, err = self._run(cmd, 18, cwd)
            label = "".join(flags) or "_all"
            if code != 0 or not out.strip():
                kind = classify_aicx_failure(code, out, err)
                warnings.append(
                    f"sessions_list{label}:{kind}:{code}:{(err or out)[:160]}"
                )
                if kind == "bad_project":
                    raise SessionChainError(
                        "bad_project",
                        f"aicx sessions list rejected filter ({label}): {(err or out)[:240]}",
                        scanned=0,
                        matched=0,
                        details={"stderr": (err or "")[:240]},
                    )
                continue
            try:
                payload = json.loads(out)
            except json.JSONDecodeError:
                warnings.append(f"sessions_list_json_invalid{label}")
                continue
            if isinstance(payload, list):
                listing_completed = True
                raw_items = [row for row in payload if isinstance(row, dict)]
                if flags == ["--cwd"]:
                    break

        records = [
            rec
            for rec in (SessionRecord.from_mapping(item) for item in raw_items)
            if rec is not None
        ]
        scanned = len(records)
        root_path = root.resolve() if root is not None else None
        if root_path is not None:
            filtered = [
                rec
                for rec in records
                if matches_exact_project(rec, root=root_path, project_filter=project)
            ]
        else:
            token = project.lstrip("/").lower()
            filtered = []
            for rec in records:
                proj = rec.project.lower()
                if proj == token or proj.endswith("/" + token):
                    filtered.append(rec)

        empty_kind: EmptyKind = "none"
        if not filtered:
            if listing_completed:
                empty_kind = "empty_project"
                warnings.append(f"empty_project:{project}:scanned={scanned}:matched=0")
            else:
                # No listing attempt ever returned a catalog. Absence is
                # unproven; claiming empty_project here is the lie that made a
                # timed-out probe read as "this repo has no history".
                empty_kind = "retrieval_unavailable"
                warnings.append(
                    f"retrieval_unavailable:{project}:no sessions listing "
                    "completed; absence of sessions is unproven"
                )
        return SessionListResult(
            sessions=filtered,
            project_filter=project,
            empty_kind=empty_kind,
            scanned=scanned,
            matched=len(filtered),
            warnings=warnings,
        )

    def show_session(self, session_id: str) -> dict[str, Any]:
        code, out, err = self._run(
            [
                self.aicx_bin,
                "sessions",
                "show",
                session_id,
                "--format",
                "json",
            ],
            18,
            None,
        )
        if code != 0:
            kind = (
                "ambiguous_id" if "ambiguous" in (err or out).lower() else "not_found"
            )
            if "unsupported" in (err or out).lower():
                kind = "unsupported_source"
            raise SessionChainError(
                kind,
                f"aicx sessions show failed for {session_id!r}: {(err or out)[:240]}",
                details={"stderr": (err or "")[:240], "session_id": session_id},
            )
        try:
            payload = json.loads(out)
        except json.JSONDecodeError as exc:
            raise SessionChainError(
                "unsupported_source",
                f"aicx sessions show returned non-JSON for {session_id!r}",
            ) from exc
        if not isinstance(payload, dict):
            raise SessionChainError(
                "unsupported_source",
                f"aicx sessions show returned {type(payload).__name__}",
            )
        return payload

    def extract_session(
        self,
        agent: str,
        session_id: str,
        *,
        conversation: bool = True,
    ) -> str:
        cmd = [self.aicx_bin, "extract", agent, "--session", session_id]
        if conversation:
            cmd.append("--conversation")
        code, out, err = self._run(cmd, 30, None)
        if code != 0:
            kind = "not_found"
            blob = f"{err} {out}".lower()
            if "ambiguous" in blob:
                kind = "ambiguous_id"
            elif "unsupported" in blob:
                kind = "unsupported_source"
            raise SessionChainError(
                kind,
                f"aicx extract {agent} --session {session_id} failed: {(err or out)[:240]}",
                details={"stderr": (err or "")[:240]},
            )
        return out

    def continuity_pack(
        self,
        *,
        project: str,
        hours: int = DEFAULT_RESUME_AICX_HOURS,
        for_inject: bool = True,
    ) -> str:
        cmd = [
            self.aicx_bin,
            "continuity",
            "show",
            "-p",
            project,
            "-H",
            str(hours),
        ]
        if for_inject:
            cmd.append("--for-inject")
        code, out, err = self._run(cmd, 20, None)
        if code != 0:
            raise SessionChainError(
                classify_aicx_failure(code, out, err),
                f"aicx continuity show -p {project} failed: {(err or out)[:240]}",
                details={"stderr": (err or "")[:240]},
            )
        return out

    def _intents_once(
        self,
        *,
        project: str,
        hours: int,
        limit: int,
        live: bool,
        timeout: float,
    ) -> ProjectIntents:
        cmd = [
            self.aicx_bin,
            "intents",
            "-p",
            project,
            "-H",
            str(hours),
            "--sort",
            "newest",
            "--kind",
            "intent",
            "--frame-kind",
            "user_msg",
            "--collapse-session",
            "--limit",
            str(limit),
        ]
        if live:
            cmd.append("--live")
        cmd.extend(["--emit", "json"])
        code, out, err = self._run(cmd, timeout, None)
        if code != 0 or not out.strip():
            raise SessionChainError(
                classify_aicx_failure(code, out, err),
                f"aicx intents -p {project} -H {hours}"
                f"{' --live' if live else ''} failed: {(err or out)[:240]}",
                details={"stderr": (err or "")[:240]},
            )
        try:
            return ProjectIntents.from_json(out, live=live)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SessionChainError(
                "unsupported_source",
                f"aicx intents -p {project} returned unparseable JSON: {exc}",
                details={"stdout_head": out[:240]},
            ) from exc

    def project_intents(
        self,
        *,
        project: str,
        hours: int,
        limit: int = PROJECT_INTENT_LIMIT,
    ) -> ProjectIntents:
        """Structured, newest-first project mission for this repository.

        The selection is deliberate: ``--kind intent`` keeps the roadmap out of
        outcome chatter, ``--frame-kind user_msg`` keeps the human/brief lane,
        ``--collapse-session`` folds verbose evidence from one session, and
        ``--limit`` bounds entries. None of these bound characters -- that is
        the pack budget's job -- but together they buy whole entries instead of
        a truncated wall.

        Freshness is two-step above 48h, where AICX stops scanning live sources
        on its own. The durable catalog answers first and is the floor; the
        live scan is then merged in as a bounded supplement. A slow live scan
        therefore costs freshness, never the mission.
        """
        auto_live = hours <= AICX_AUTO_LIVE_HOURS
        durable = self._intents_once(
            project=project,
            hours=hours,
            limit=limit,
            live=False,
            timeout=INTENTS_TIMEOUT_SECONDS,
        )
        if auto_live:
            durable.live = True
            return durable
        try:
            live = self._intents_once(
                project=project,
                hours=hours,
                limit=limit,
                live=True,
                timeout=INTENTS_LIVE_TIMEOUT_SECONDS,
            )
        except SessionChainError as exc:
            durable.warnings.append(
                f"live supplement unavailable ({exc.kind}); entries come from "
                "the durable catalog only and may miss open sessions"
            )
            return durable
        return _merge_project_intents(durable, live, limit=limit)


def _parse_ts(value: str) -> dt.datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        stamp = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(dt.timezone.utc)


def _in_window(record: SessionRecord, cutoff: dt.datetime) -> bool:
    stamp = _parse_ts(record.updated_at)
    return stamp is None or stamp >= cutoff


TRIM_MARKER = "\n\n_(trimmed to the injection budget; full source linked above)_"

# The frame stays whole even under pressure; only content sections shrink.
_CONTENT_SHARES: tuple[tuple[str, float], ...] = (
    ("intents", 0.55),
    ("continuity", 0.25),
    ("catalog", 0.20),
)

# Provenance lines are the point of an entry: a shortened summary that still
# names its session and source file remains actionable, a bare sentence does
# not. Below this, drop the whole item and count it as omitted instead.
MIN_INTENT_SUMMARY_CHARS = 240


def _fit_to_budget(text: str, budget: int, *, marker: str = TRIM_MARKER) -> str:
    """Trim on a line boundary and say that a trim happened."""
    if budget <= 0 or not text:
        return ""
    if len(text) <= budget:
        return text
    room = budget - len(marker)
    if room <= 0:
        return marker.strip()[:budget]
    head = text[:room]
    cut = head.rfind("\n")
    if cut > room // 2:
        head = head[:cut]
    return head.rstrip() + marker


def _render_intent_item(item: IntentItem, ordinal: int, *, budget: int) -> str:
    """One entry with its provenance attached, shortened summary-first."""
    heading = "### {n}. {kind} · {agent} · {date}".format(
        n=ordinal,
        kind=item.kind or "entry",
        agent=item.agent or "unknown-agent",
        date=item.date or "undated",
    )
    tail_lines = [
        "- origin: agent `{agent}`, frame `user_msg`, project `{project}`".format(
            agent=item.agent or "?",
            project=item.project or "?",
        )
    ]
    if item.context:
        # AICX context values sometimes arrive already bulleted; a nested dash
        # would read as a sub-item of the provenance list.
        context = " ".join(item.context.split()).lstrip("-").strip()
        if context:
            tail_lines.append(f"- context: {context}")
    tail_lines.append(
        "- claim: {scope} · {freshness} · {verification}".format(
            scope=item.claim_scope or "unknown-scope",
            freshness=item.freshness_contract or "unknown-freshness",
            verification=item.verification_state or "unknown-verification",
        )
    )
    if item.source_chunk:
        tail_lines.append(
            f"- source: `{item.source_chunk}`"
            + (f" (session `{item.session_id}`)" if item.session_id else "")
        )
    elif item.session_id:
        tail_lines.append(f"- source: session `{item.session_id}`")
    tail = "\n".join(tail_lines)

    fixed = len(heading) + len(tail) + 4  # two blank-line joins
    summary_room = budget - fixed
    if summary_room < MIN_INTENT_SUMMARY_CHARS:
        return ""
    summary = item.summary
    if len(summary) > summary_room:
        summary = summary[: summary_room - 3].rstrip() + "..."
    return f"{heading}\n{summary}\n\n{tail}"


def _render_intents_section(intents: ProjectIntents, *, budget: int) -> str:
    """Whole entries only. What does not fit is counted, never half-printed."""
    preamble = [
        "## Project intentions (primary mission, repo-scoped)",
        "",
        (
            "Fresh sessions start here. The `user_msg` lane carries direct "
            "human input, operator agent briefs and peer transport alike, so "
            "read each entry's origin and source before treating it as a "
            "Founder decision. AICX has not verified any of these claims."
        ),
        "",
    ]
    head = "\n".join(preamble)
    remaining = budget - len(head)
    if remaining <= 0:
        return ""

    blocks: list[str] = []
    shown = 0
    for ordinal, item in enumerate(intents.items, start=1):
        # Keep room for the stats footer we are obliged to print.
        block = _render_intent_item(item, ordinal, budget=remaining - 200)
        if not block:
            break
        cost = len(block) + 2
        if cost > remaining - 200:
            break
        blocks.append(block)
        remaining -= cost
        shown += 1

    dropped_for_budget = len(intents.items) - shown
    stats = (
        "_retrieval: {shown} shown of {returned} returned, {available} available "
        "in window (limit {limit}); identity `{identity}`, match `{mode}`, "
        "buckets {buckets}; live scan: {live}; complete: {complete}._"
    ).format(
        shown=shown,
        returned=intents.returned,
        available=intents.available,
        limit=intents.limit or "unset",
        identity=intents.identity_source or "unknown",
        mode=intents.match_mode or "unknown",
        buckets=", ".join(f"`{b}`" for b in intents.matched_buckets) or "none",
        live="yes" if intents.live else "no",
        complete="yes" if intents.complete else "no",
    )
    omission_notes: list[str] = []
    if dropped_for_budget > 0:
        omission_notes.append(
            f"{dropped_for_budget} retrieved entr"
            f"{'y' if dropped_for_budget == 1 else 'ies'} omitted for the "
            "injection budget"
        )
    if intents.omitted > 0:
        omission_notes.append(
            f"{intents.omitted} further entr"
            f"{'y' if intents.omitted == 1 else 'ies'} exist in the window "
            "beyond the retrieval limit"
        )
    if omission_notes:
        stats += (
            "\n_omitted: " + "; ".join(omission_notes) + ". Full source linked above._"
        )
    for warning in intents.warnings:
        stats += f"\n_warning: {warning}_"

    if not blocks:
        return (
            head
            + "_(no entry fit the injection budget; read the full source.)_\n\n"
            + stats
        )
    return head + "\n\n".join(blocks) + "\n\n" + stats


def _compose_bounded_pack(
    *,
    header: str,
    instruction: str,
    content: Mapping[str, Callable[[int], str]],
) -> str:
    """Assemble the pack so the WHOLE result honours ``MAX_PACK_CHARS``.

    Header, notices, source links and the operator instruction are counted
    first because they are non-negotiable: a pack that drops its own "do not
    attach" clause to make room for history is worse than a short pack. What
    is left is shared between the content sections in priority order, each
    with a floor so a large mission cannot starve the evidence entirely.

    Sections are rendered by callables that receive their own allowance, so a
    section can shrink itself coherently -- dropping whole entries and saying
    how many -- instead of being cut off mid-line by a blunt trim that would
    take its own omission footer with it.
    """
    separator = "\n\n"
    frame = len(header) + len(instruction) + 2 * len(separator)
    remaining = MAX_PACK_CHARS - frame

    rendered: dict[str, str] = {}
    if remaining > 0:
        floors = {name: int(remaining * share) for name, share in _CONTENT_SHARES}
        for name, _share in _CONTENT_SHARES:
            reserved = sum(
                size
                for other, size in floors.items()
                if other != name and other not in rendered
            )
            allowance = max(0, remaining - reserved - len(separator))
            fitted = _fit_to_budget(content[name](allowance), allowance)
            rendered[name] = fitted
            if fitted:
                remaining -= len(fitted) + len(separator)

    parts = [header]
    for name, _share in _CONTENT_SHARES:
        if rendered.get(name):
            parts.append(rendered[name])
    parts.append(instruction)
    body = separator.join(parts)
    if len(body) > MAX_PACK_CHARS:
        # Last-resort guard: the cap is a contract, not a target.
        body = _fit_to_budget(body, MAX_PACK_CHARS)
    return body


def assemble_resume_continuity_pack(
    *,
    agent: str,
    root: Path,
    hours: int,
    context_file: Path,
    meta_file: Path,
    chain: SessionChain,
    operator_session_id: str = "",
    now: dt.datetime | None = None,
) -> ResumePack:
    """Build the resume pack. Never selects native resume on its own."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=hours)
    root = root.resolve()
    identity = resolve_project_identity(root)
    project_filter = identity.project_filter
    degradations: list[str] = []

    if identity.known:
        listing = chain.list_sessions(
            project=project_filter,
            root=root,
            hours=hours,
            limit=40,
        )
    else:
        # Project identity ambiguity resolves before packing, never during it.
        degradations.append(
            "identity:unknown:no git origin resolved for this checkout; "
            "refusing a basename filter that would union foreign orgs"
        )
        listing = SessionListResult(
            sessions=[],
            project_filter="",
            empty_kind="unknown_identity",
            scanned=0,
            matched=0,
        )
    degradations.extend(listing.warnings)
    catalog = [row for row in listing.sessions if _in_window(row, cutoff)]

    continuity_md = ""
    intents: ProjectIntents | None = None
    if identity.known:
        try:
            continuity_md = chain.continuity_pack(
                project=project_filter,
                hours=hours,
                for_inject=True,
            ).strip()
        except SessionChainError as exc:
            degradations.append(f"continuity:{exc.kind}:{exc.message[:160]}")
        except Exception as exc:  # noqa: BLE001 — pack degrades, never crashes resume
            degradations.append(f"continuity:error:{exc!s:.160}")

        if isinstance(chain, CliSessionChain):
            try:
                intents = chain.project_intents(project=project_filter, hours=hours)
            except SessionChainError as exc:
                degradations.append(f"intents:{exc.kind}:{exc.message[:160]}")
            except Exception as exc:  # noqa: BLE001 — same contract as continuity
                degradations.append(f"intents:error:{exc!s:.160}")

    mission_available = bool(intents and intents.items)
    operator_id = operator_session_id.strip()
    mode: Literal["new_session", "native_resume"] = (
        "native_resume" if operator_id else "new_session"
    )

    lines: list[str] = [
        "# Resume continuity pack",
        "",
        "AICX multi-agent context for this repository and time window.",
        (
            "This pack is continuity transport. It does not select a provider "
            "session and it is not an instruction to attach or recover one."
        ),
        "",
        f"- agent: `{agent}`",
        f"- root: `{root}`",
        f"- aicx_project_filter: {identity.describe()}",
        f"- window: last {hours}h across all agents",
        f"- assembled_at: `{now.isoformat()}`",
        (
            f"- session_list_empty: `{listing.empty_kind}` "
            f"(scanned={listing.scanned}, matched={listing.matched})"
        ),
        f"- project_mission: `{'retrieved' if mission_available else 'unavailable'}`",
        "- native_resume: only if the operator already passed `--session`",
        f"- mode: `{mode}`",
    ]
    if operator_id:
        lines.append(
            f"- operator_session: `{operator_id}` (explicit `--session`; "
            "pack is background only)"
        )
    else:
        lines.append("- operator_session: none")
    if degradations:
        lines.append("- degradations:")
        for item in degradations:
            lines.append(f"  - `{item}`")

    full_context_file = Path(str(context_file) + ".full.md")
    full_context_file.parent.mkdir(parents=True, exist_ok=True)
    full_context_file.write_text(
        "# Full project intention source\n\n"
        f"- project: `{project_filter or 'unresolved'}`\n"
        f"- window: last {hours}h\n"
        "- invocation: `aicx intents -p <owner/repo> -H <hours> --sort newest "
        "--kind intent --frame-kind user_msg --collapse-session --limit "
        f"{PROJECT_INTENT_LIMIT}"
        + (" --live" if hours > AICX_AUTO_LIVE_HOURS else "")
        + " --emit json`\n\n"
        + (
            intents.raw
            if intents is not None
            else "_(project intention retrieval unavailable; see degradations)_"
        )
        + "\n\n# Full continuity source\n\n"
        + (continuity_md or "_(continuity unavailable; see degradations)_")
        + "\n",
        encoding="utf-8",
    )
    lines.append(f"- full_retrieval: `{full_context_file}`")

    mission_unavailable_note = "\n".join(
        [
            "## Project intentions (primary mission, repo-scoped)",
            "",
            (
                "_(project intention retrieval did not deliver; continuity "
                "is not a healthy substitute for the mission. See "
                f"degradations and `{full_context_file}`.)_"
            ),
        ]
    )

    def render_intents(budget: int) -> str:
        if intents is None or not intents.items:
            return mission_unavailable_note
        return _render_intents_section(intents, budget=budget)

    catalog_lines = ["## Session catalog (evidence, not a picker)", ""]
    if listing.empty_kind == "empty_project":
        catalog_lines.append(
            f"_(empty_project: `{project_filter}` has no sessions in this "
            f"window; scanned={listing.scanned}, matched=0. This is not a "
            "parser miss and not a license to use another project's sessions.)_"
        )
    elif listing.empty_kind == "retrieval_unavailable":
        catalog_lines.append(
            "_(retrieval_unavailable: no session listing completed, so the "
            "absence of sessions is unproven. This is a retrieval outage, "
            "not an empty project. See degradations.)_"
        )
    elif listing.empty_kind == "unknown_identity":
        catalog_lines.append(
            "_(unknown_identity: this checkout has no resolvable git origin, "
            "so no canonical owner/repo filter exists. Refusing to union "
            "same-basename repositories from other orgs.)_"
        )
    elif listing.empty_kind == "missing_filter":
        catalog_lines.append(
            "_(missing_filter: no project identity was supplied; refusing "
            "to treat an unfiltered catalog as this repo.)_"
        )
    elif listing.empty_kind == "bad_project":
        catalog_lines.append(
            "_(bad_project: the project filter was rejected. See degradations.)_"
        )
    elif not catalog:
        catalog_lines.append("_(no sessions discovered in window)_")
    else:
        catalog_lines.append(
            "Rows below are evidence for a new head. They are not launch "
            "targets. Native attach happens only when the operator already "
            "passed `--session`."
        )
        catalog_lines.append("")
        catalog_lines.append(
            "| agent | session_id | project | live | updated_at | title |"
        )
        catalog_lines.append("| --- | --- | --- | --- | --- | --- |")
        for item in catalog[:30]:
            title = str(item.title or "").replace("|", "/").replace("\n", " ")
            if len(title) > 72:
                title = title[:69] + "..."
            catalog_lines.append(
                "| {agent} | `{sid}` | {proj} | {live} | {updated} | {title} |".format(
                    agent=item.agent or "?",
                    sid=item.session_id,
                    proj=item.project or "?",
                    live="yes" if item.live else "no",
                    updated=item.updated_at or "?",
                    title=title or "—",
                )
            )
    catalog_section = "\n".join(catalog_lines)

    continuity_section = "\n".join(
        [
            "## Continuity (supplementary context)",
            "",
            continuity_md or "_(continuity unavailable; see degradations)_",
        ]
    )

    instruction_section = (
        "## Operator instruction\n"
        "\n"
        "Start a **new** provider session in this repository. Use the catalog "
        "and continuity sections as background. Historical paths and "
        "foreign-agent sessions are evidence only — not launch destinations.\n"
        "Native provider attach requires an explicit operator `--session`. "
        "This pack is not that signal.\n"
        "Re-read files before editing (Living Tree). Prefer runtime truth "
        "over remembered state.\n"
    )

    body = _compose_bounded_pack(
        header="\n".join(lines),
        instruction=instruction_section,
        content={
            "intents": render_intents,
            "continuity": lambda _budget: continuity_section,
            "catalog": lambda _budget: catalog_section,
        },
    )
    if pack_contains_recover_instruction(body):
        raise RuntimeError("resume pack assembler emitted a recover instruction")

    context_file.parent.mkdir(parents=True, exist_ok=True)
    context_file.write_text(body, encoding="utf-8")
    meta = {
        "schema": SCHEMA,
        "agent": agent,
        "root": str(root),
        "hours": hours,
        "session_id": operator_id,
        "session_count": len(catalog),
        "mode": mode,
        "empty_kind": listing.empty_kind,
        "scanned": listing.scanned,
        "matched": listing.matched,
        "project_filter": project_filter,
        "identity_source": identity.source,
        "context_file": str(context_file),
        "full_context_file": str(full_context_file),
        "pack_chars": len(body),
        "max_pack_chars": MAX_PACK_CHARS,
        "project_mission": {
            "available": mission_available,
            "returned": intents.returned if intents else 0,
            "in_window": intents.available if intents else 0,
            "limit": intents.limit if intents else 0,
            "live_scan": intents.live if intents else False,
            "complete": intents.complete if intents else False,
            "matched_buckets": list(intents.matched_buckets) if intents else [],
        },
        "degradations": degradations,
    }
    meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return ResumePack(
        context_file=context_file,
        meta_file=meta_file,
        full_context_file=full_context_file,
        session_id=operator_id,
        session_count=len(catalog),
        mode=mode,
        project_filter=project_filter,
        empty_kind=listing.empty_kind,
        degradations=degradations,
        body=body,
    )


def _resume_pack_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibecrafted_core.aicx_session_chain")
    parser.add_argument("command", choices=["resume-pack", "contract"])
    parser.add_argument("--agent", default="")
    parser.add_argument("--root", default="")
    parser.add_argument("--hours", type=int, default=DEFAULT_RESUME_AICX_HOURS)
    parser.add_argument("--aicx", default="")
    parser.add_argument("--context-file", default="")
    parser.add_argument("--meta-file", default="")
    parser.add_argument("--operator-session", default="")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "contract":
        sys.stdout.write(json.dumps(mcp_session_chain_contract(), indent=2) + "\n")
        return 0

    if not args.agent or not args.root or not args.aicx:
        print("resume-pack requires --agent --root --aicx", file=sys.stderr)
        return 2
    root = Path(args.root)
    context_file = (
        Path(args.context_file)
        if args.context_file
        else Path(f"resume-aicx-{args.agent}.md")
    )
    meta_file = (
        Path(args.meta_file)
        if args.meta_file
        else Path(str(context_file) + ".meta.json")
    )
    pack = assemble_resume_continuity_pack(
        agent=args.agent,
        root=root,
        hours=args.hours,
        context_file=context_file,
        meta_file=meta_file,
        chain=CliSessionChain(args.aicx),
        operator_session_id=args.operator_session,
    )
    for line in pack.stdout_lines():
        print(line)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return _resume_pack_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
