"""Session-chain contract: loud empty, exact project, no implicit native resume."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core.aicx_session_chain import (
    AICX_AUTO_LIVE_HOURS,
    DEFAULT_RESUME_AICX_HOURS,
    MAX_PACK_CHARS,
    MCP_RETRIEVAL_TOOLS,
    MCP_SESSION_CHAIN_TOOLS,
    MIN_INTENT_SUMMARY_CHARS,
    PROJECT_INTENT_LIMIT,
    SHORTENED_SUMMARY_NOTE,
    CliSessionChain,
    IntentItem,
    ProjectIntents,
    SessionChain,
    SessionChainError,
    SessionListResult,
    SessionRecord,
    _compose_bounded_pack,
    _merge_project_intents,
    _render_intents_section,
    assemble_resume_continuity_pack,
    classify_aicx_failure,
    matches_exact_project,
    mcp_session_chain_contract,
    pack_contains_recover_instruction,
    project_filter_for_root,
    resolve_project_identity,
)


def make_checkout(path: Path, owner_repo: str) -> Path:
    """A checkout whose identity is readable without invoking git."""
    path.mkdir(parents=True, exist_ok=True)
    git_dir = path / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text(
        f'[remote "origin"]\n\turl = https://github.com/{owner_repo}\n',
        encoding="utf-8",
    )
    return path


def make_linked_worktree(main: Path, name: str, where: Path) -> Path:
    """A linked worktree: git dir without config, reachable via commondir."""
    wt_git = main / ".git" / "worktrees" / name
    wt_git.mkdir(parents=True, exist_ok=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/cut\n", encoding="utf-8")
    (wt_git / "commondir").write_text("../..\n", encoding="utf-8")
    where.mkdir(parents=True, exist_ok=True)
    (where / ".git").write_text(f"gitdir: {wt_git}\n", encoding="utf-8")
    return where


class MemoryChain(SessionChain):
    def __init__(
        self,
        sessions: list[SessionRecord] | None = None,
        *,
        empty_kind: str = "none",
        scanned: int | None = None,
        continuity: str = "## NOW\npeer work\n## PEERS\n## DECISIONS\n## TASKS\n## SOURCES\n## INDEX HEALTH\nready",
        show: dict | None = None,
        extract: str = "user: hello\nassistant: world",
        show_error: SessionChainError | None = None,
        extract_error: SessionChainError | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self._sessions = list(sessions or [])
        self._empty_kind = empty_kind
        self._scanned = scanned if scanned is not None else len(self._sessions)
        self._continuity = continuity
        self._show = show or {}
        self._extract = extract
        self._show_error = show_error
        self._extract_error = extract_error
        self._warnings = list(warnings or [])
        self.list_calls: list[dict[str, object]] = []

    def list_sessions(self, **kwargs: object) -> SessionListResult:
        self.list_calls.append(kwargs)
        project = str(kwargs.get("project") or "")
        return SessionListResult(
            sessions=list(self._sessions),
            project_filter=project,
            empty_kind=self._empty_kind,  # type: ignore[arg-type]
            scanned=self._scanned,
            matched=len(self._sessions),
            warnings=list(self._warnings),
        )

    def show_session(self, session_id: str) -> dict:
        if self._show_error:
            raise self._show_error
        return {"session_id": session_id, **self._show}

    def extract_session(
        self, agent: str, session_id: str, *, conversation: bool = True
    ) -> str:
        if self._extract_error:
            raise self._extract_error
        return self._extract

    def continuity_pack(self, **kwargs: object) -> str:
        return self._continuity


def test_mcp_contract_names_the_missing_session_chain() -> None:
    contract = mcp_session_chain_contract()
    assert contract["mcp_tools"] == list(MCP_SESSION_CHAIN_TOOLS)
    assert "aicx_sessions" in contract["mcp_tools"]
    assert "aicx_extract" in contract["mcp_tools"]
    assert "aicx_continuity" in contract["mcp_tools"]
    assert contract["retrieval_tools_unchanged"] == list(MCP_RETRIEVAL_TOOLS)
    assert contract["native_resume"].startswith("only when operator")
    assert "recover previous session" in contract["pack_must_not"]


def test_exact_project_does_not_match_sibling_repo() -> None:
    root = Path("/tmp/codescribe")
    keep = SessionRecord(
        session_id="keep-1",
        agent="claude",
        project="vetcoders/codescribe",
        repo_path="/tmp/codescribe",
    )
    sibling = SessionRecord(
        session_id="leak-1",
        agent="claude",
        project="vetcoders/codescribe-rs",
        repo_path="/tmp/codescribe-rs",
    )
    foreign = SessionRecord(
        session_id="gpt-export",
        agent="chatgpt",
        project="exports/chatgpt",
        repo_path="/tmp/chatgpt-export",
    )
    assert matches_exact_project(keep, root=root, project_filter="/codescribe")
    assert not matches_exact_project(sibling, root=root, project_filter="/codescribe")
    assert not matches_exact_project(foreign, root=root, project_filter="/codescribe")


def test_list_without_project_is_missing_filter_not_silent_empty() -> None:
    chain = CliSessionChain("/usr/bin/false")
    result = chain.list_sessions(project=None)
    assert result.empty_kind == "missing_filter"
    assert result.matched == 0
    assert any("missing_filter" in warning for warning in result.warnings)


def test_list_empty_project_is_loud(tmp_path: Path) -> None:
    repo = tmp_path / "vibecrafted"
    repo.mkdir()
    calls: list[list[str]] = []

    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        calls.append(list(cmd))
        if cmd[1:3] == ["sessions", "list"]:
            foreign = [
                {
                    "session_id": "chatgpt-export-1",
                    "agent": "chatgpt",
                    "project": "exports/chatgpt",
                    "repo_path": str(tmp_path / "chatgpt"),
                    "updated_at": "2026-08-16T00:00:00Z",
                    "title": "ChatGPT export",
                }
            ]
            return 0, json.dumps(foreign), ""
        return 1, "", "unused"

    chain = CliSessionChain("aicx", runner=runner)
    result = chain.list_sessions(project="/vibecrafted", root=repo)
    assert result.empty_kind == "empty_project"
    assert result.matched == 0
    assert result.scanned == 1
    assert any(
        warning.startswith("empty_project:/vibecrafted:") for warning in result.warnings
    )
    assert result.sessions == []


def test_show_and_extract_are_loud_on_ambiguous_id() -> None:
    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        return 2, "", "ambiguous prefix matches 3 sessions"

    chain = CliSessionChain("aicx", runner=runner)
    with pytest.raises(SessionChainError) as show_err:
        chain.show_session("abc")
    assert show_err.value.kind == "ambiguous_id"
    with pytest.raises(SessionChainError) as extract_err:
        chain.extract_session("claude", "abc")
    assert extract_err.value.kind == "ambiguous_id"


def test_resume_pack_never_selects_native_even_with_same_agent(
    tmp_path: Path,
) -> None:
    repo = make_checkout(tmp_path / "repo", "vetcoders/repo")
    live = SessionRecord(
        session_id="live-bbbb-2222",
        agent="claude",
        project="vetcoders/repo",
        repo_path=str(repo),
        title="still resumable",
        updated_at="2026-08-16T12:00:00Z",
        live=True,
    )
    pack = assemble_resume_continuity_pack(
        agent="claude",
        root=repo,
        hours=48,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=MemoryChain([live]),
        now=dt.datetime(2026, 8, 17, 12, 0, tzinfo=dt.timezone.utc),
    )
    assert pack.mode == "new_session"
    assert pack.session_id == ""
    assert pack.session_count == 1
    assert "live-bbbb-2222" in pack.body
    assert "prefer native resume" not in pack.body.lower()
    assert not pack_contains_recover_instruction(pack.body)
    assert "This pack is not that signal." in pack.body
    meta = json.loads(pack.meta_file.read_text(encoding="utf-8"))
    assert meta["mode"] == "new_session"
    assert meta["session_id"] == ""


def test_resume_pack_empty_project_is_loud_not_foreign_catalog(
    tmp_path: Path,
) -> None:
    repo = make_checkout(tmp_path / "emptyproj", "vetcoders/emptyproj")
    pack = assemble_resume_continuity_pack(
        agent="claude",
        root=repo,
        hours=48,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=MemoryChain(
            [],
            empty_kind="empty_project",
            scanned=4,
            warnings=["empty_project:/emptyproj:scanned=4:matched=0"],
        ),
    )
    assert pack.empty_kind == "empty_project"
    assert "empty_project" in pack.body
    assert "chatgpt" not in pack.body.lower()
    assert "parser miss" in pack.body


def test_explicit_operator_session_is_background_only(tmp_path: Path) -> None:
    repo = make_checkout(tmp_path / "repo", "vetcoders/repo")
    pack = assemble_resume_continuity_pack(
        agent="claude",
        root=repo,
        hours=48,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=MemoryChain([]),
        operator_session_id="explicit-id-999",
    )
    assert pack.mode == "native_resume"
    assert pack.session_id == "explicit-id-999"
    assert "operator_session: `explicit-id-999`" in pack.body
    assert not pack_contains_recover_instruction(pack.body)


# ---------------------------------------------------------------------------
# Canonical project identity
# ---------------------------------------------------------------------------


def test_project_filter_is_canonical_owner_repo_not_a_basename_union(
    tmp_path: Path,
) -> None:
    """``/vibecrafted`` would union every same-named repo in every org."""
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")
    assert project_filter_for_root(repo) == "vetcoders/vibecrafted"
    identity = resolve_project_identity(repo)
    assert identity.known
    assert identity.source == "git_origin"
    assert "canonical git origin" in identity.describe()


def test_canonical_identity_resolves_through_a_linked_worktree(
    tmp_path: Path,
) -> None:
    """Every dispatched worker runs in a worktree whose git dir has no config."""
    main = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")
    worktree = make_linked_worktree(main, "cut-1", tmp_path / "cuts" / "cut-1")
    assert project_filter_for_root(worktree) == "vetcoders/vibecrafted"


def test_unknown_identity_is_reported_not_guessed(tmp_path: Path) -> None:
    """No git origin means no filter, and no AICX call made on a guess."""
    repo = tmp_path / "lbrx-services"
    repo.mkdir()
    calls: list[list[str]] = []

    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        calls.append(list(cmd))
        return 0, "[]", ""

    identity = resolve_project_identity(repo)
    assert not identity.known
    assert identity.source == "unknown"

    pack = assemble_resume_continuity_pack(
        agent="codex",
        root=repo,
        hours=DEFAULT_RESUME_AICX_HOURS,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=CliSessionChain("aicx", runner=runner),
    )
    assert pack.empty_kind == "unknown_identity"
    assert pack.project_filter == ""
    assert calls == [], "a repo without identity must not be queried on a guess"
    assert "unknown_identity" in pack.body
    assert "lbrx-services" not in pack.body.split("- root:")[1].split("\n")[1]
    assert any(item.startswith("identity:unknown:") for item in pack.degradations)


def test_owner_repo_filter_never_matches_a_foreign_org(tmp_path: Path) -> None:
    root = tmp_path / "vibecrafted"
    root.mkdir()
    ours = SessionRecord(session_id="a", project="vetcoders/vibecrafted")
    theirs = SessionRecord(session_id="b", project="someone-else/vibecrafted")
    assert matches_exact_project(
        ours, root=root, project_filter="vetcoders/vibecrafted"
    )
    assert not matches_exact_project(
        theirs, root=root, project_filter="vetcoders/vibecrafted"
    )


# ---------------------------------------------------------------------------
# Window defaults
# ---------------------------------------------------------------------------


def test_default_window_is_96_hours_and_the_caller_still_overrides(
    tmp_path: Path,
) -> None:
    """720h was explicitly rejected; 96h is the rooted default, not a ceiling."""
    assert DEFAULT_RESUME_AICX_HOURS == 96
    assert DEFAULT_RESUME_AICX_HOURS > AICX_AUTO_LIVE_HOURS, (
        "a 96h window outlives the auto-live boundary, so freshness is deliberate"
    )
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")
    windows: list[str] = []

    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        if "-H" in cmd:
            windows.append(cmd[cmd.index("-H") + 1])
        if cmd[1:3] == ["sessions", "list"]:
            return 0, "[]", ""
        return 1, "", "declined"

    for requested in (DEFAULT_RESUME_AICX_HOURS, 12):
        assemble_resume_continuity_pack(
            agent="codex",
            root=repo,
            hours=requested,
            context_file=tmp_path / f"pack-{requested}.md",
            meta_file=tmp_path / f"pack-{requested}.meta.json",
            chain=CliSessionChain("aicx", runner=runner),
        )
    assert "96" in windows
    assert "12" in windows
    assert "720" not in windows


def test_every_entrypoint_agrees_on_the_default_window() -> None:
    """Shell, CLI and contract must not each carry their own idea of fresh."""
    module = Path(__file__).resolve().parents[1] / "vibecrafted_core"
    shell = (module / "runtime/shell/lib/marbles.sh").read_text(encoding="utf-8")
    assert "VIBECRAFTED_RESUME_AICX_HOURS:-96" in shell
    assert "VIBECRAFTED_RESUME_AICX_HOURS:-720" not in shell
    assert "VIBECRAFTED_RESUME_AICX_HOURS:-48" not in shell
    spawn = (module / "spawn.py").read_text(encoding="utf-8")
    assert "hours=DEFAULT_RESUME_AICX_HOURS," in spawn
    assert mcp_session_chain_contract()["default_hours"] == DEFAULT_RESUME_AICX_HOURS


# ---------------------------------------------------------------------------
# Failure honesty
# ---------------------------------------------------------------------------


def test_a_timeout_is_never_reported_as_an_empty_project(tmp_path: Path) -> None:
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")

    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        return 124, "", "timeout"

    listing = CliSessionChain("aicx", runner=runner).list_sessions(
        project="vetcoders/vibecrafted", root=repo
    )
    assert listing.empty_kind == "retrieval_unavailable"
    assert any("no sessions listing completed" in w for w in listing.warnings)
    assert any(":timeout:" in w for w in listing.warnings)

    pack = assemble_resume_continuity_pack(
        agent="codex",
        root=repo,
        hours=DEFAULT_RESUME_AICX_HOURS,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=CliSessionChain("aicx", runner=runner),
    )
    assert pack.empty_kind == "retrieval_unavailable"
    assert "absence of sessions is unproven" in pack.body
    assert "has no sessions in this window" not in pack.body
    assert json.loads(pack.meta_file.read_text(encoding="utf-8"))["empty_kind"] == (
        "retrieval_unavailable"
    )


def test_mixed_workstream_refusal_is_not_a_rejected_project(tmp_path: Path) -> None:
    """AICX's guard text contains the word "candidate"; that is not bad_project."""
    refusal = (
        "Error: continuity: 2 mixed-workstream session(s) in the window and "
        "nothing homogeneous to distill; pass distill_mixed to override\n"
        "Caused by:\n    refused: claude session 4ff5e62c is a "
        "mixed-workstream candidate (4 cwd(s), 1 branch(es))"
    )
    assert classify_aicx_failure(1, "", refusal) == "mixed_workstream"
    assert classify_aicx_failure(124, "", "timeout") == "timeout"
    assert classify_aicx_failure(127, "", "not_found") == "aicx_missing"
    assert classify_aicx_failure(2, "", "unknown project foo") == "bad_project"

    repo = make_checkout(tmp_path / "lbrx-services", "LibraxisAI/lbrx-services")

    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        if cmd[1:3] == ["sessions", "list"]:
            return 0, "[]", ""
        if cmd[1:3] == ["continuity", "show"]:
            return 1, "", refusal
        return 1, "", "declined"

    pack = assemble_resume_continuity_pack(
        agent="codex",
        root=repo,
        hours=DEFAULT_RESUME_AICX_HOURS,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=CliSessionChain("aicx", runner=runner),
    )
    assert any(d.startswith("continuity:mixed_workstream:") for d in pack.degradations)
    assert not any(d.startswith("continuity:bad_project:") for d in pack.degradations)


def test_a_pack_without_its_mission_says_so_instead_of_looking_healthy(
    tmp_path: Path,
) -> None:
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")

    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        if cmd[1:3] == ["sessions", "list"]:
            return 0, "[]", ""
        if cmd[1] == "intents":
            return 124, "", "timeout"
        if cmd[1:3] == ["continuity", "show"]:
            return 0, "## NOW\ncontinuity only", ""
        return 1, "", "declined"

    pack = assemble_resume_continuity_pack(
        agent="codex",
        root=repo,
        hours=DEFAULT_RESUME_AICX_HOURS,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=CliSessionChain("aicx", runner=runner),
    )
    assert any(d.startswith("intents:timeout:") for d in pack.degradations)
    assert "continuity is not a healthy substitute" in pack.body
    assert "project_mission: `unavailable`" in pack.body
    meta = json.loads(pack.meta_file.read_text(encoding="utf-8"))
    assert meta["project_mission"]["available"] is False


# ---------------------------------------------------------------------------
# Structured retrieval and provenance
# ---------------------------------------------------------------------------


def _intents_payload(count: int, *, available: int, summary: str) -> str:
    return json.dumps(
        {
            # `results` is a COUNT and `items` the collection; a consumer that
            # iterates both double-counts the mission.
            "results": count,
            "items": [
                {
                    "kind": "intent",
                    "summary": f"{summary} #{index}",
                    "context": "- ## 2. Mission | ## 3. Context",
                    "project": "vetcoders/vibecrafted",
                    "agent": "claude" if index % 2 else "codex",
                    "date": f"2026-09-{12 - index:02d}",
                    "timestamp": f"2026-09-{12 - index:02d}T00:00:00+00:00",
                    "session_id": f"session-{index:04d}",
                    "count": 1,
                    "source_chunk": (
                        f"/Users/tester/.claude/projects/-very-long-"
                        f"projection-path-segment-{index}/session-{index:04d}.jsonl"
                    ),
                    "claim_scope": "session_close",
                    "freshness_contract": "historical",
                    "verification_state": "not_verified_by_aicx",
                }
                for index in range(count)
            ],
            "completeness": {
                "complete": False,
                "requested_limit": PROJECT_INTENT_LIMIT,
                "available_before_limit": available,
                "matched_project_buckets": ["vetcoders/vibecrafted"],
                "identity_source": "project-bucket-v1",
                "warnings": [],
                "scope": {"match_mode": "exact"},
            },
        }
    )


def test_intents_json_counts_the_collection_once() -> None:
    parsed = ProjectIntents.from_json(
        _intents_payload(3, available=9, summary="mission"), live=False
    )
    assert parsed.returned == 3, "`results` is an int count, not a second collection"
    assert parsed.available == 9
    assert parsed.omitted == 6


def test_intent_entries_keep_provenance_and_are_not_called_founder_decisions(
    tmp_path: Path,
) -> None:
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")
    selection: list[list[str]] = []

    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        if cmd[1:3] == ["sessions", "list"]:
            return 0, "[]", ""
        if cmd[1] == "intents":
            selection.append(list(cmd))
            return 0, _intents_payload(2, available=7, summary="mission"), ""
        return 1, "", "declined"

    pack = assemble_resume_continuity_pack(
        agent="codex",
        root=repo,
        hours=DEFAULT_RESUME_AICX_HOURS,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=CliSessionChain("aicx", runner=runner),
    )

    durable = selection[0]
    for flag in ("--kind", "--frame-kind", "--collapse-session", "--limit", "--sort"):
        assert flag in durable
    assert durable[durable.index("-p") + 1] == "vetcoders/vibecrafted"
    assert durable[durable.index("--kind") + 1] == "intent"
    assert durable[durable.index("--frame-kind") + 1] == "user_msg"
    assert "--live" not in durable, "the durable catalog answers first"
    assert any("--live" in cmd for cmd in selection), (
        "a 96h window outlives auto-live, so freshness must be asked for"
    )

    # Provenance survives into the injected pack.
    assert "session-0000" in pack.body
    assert "-very-long-projection-path-segment-0/" in pack.body
    assert "not_verified_by_aicx" in pack.body
    assert "do not read as a Founder decision" not in pack.body
    assert "before treating it as a Founder decision" in pack.body
    # Omission is stated, not implied by a short section.
    assert "5 further entries exist in the window" in pack.body


def test_empty_session_catalog_does_not_erase_retrieved_project_intentions(
    tmp_path: Path,
) -> None:
    """A valid empty catalog is not evidence that project history is absent."""
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")

    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        if cmd[1:3] == ["sessions", "list"]:
            return 0, "[]", ""
        if cmd[1] == "intents":
            return 0, _intents_payload(2, available=2, summary="mission"), ""
        if cmd[1:3] == ["continuity", "show"]:
            return 0, "## NOW\ncontinuity", ""
        return 1, "", "declined"

    pack = assemble_resume_continuity_pack(
        agent="codex",
        root=repo,
        hours=DEFAULT_RESUME_AICX_HOURS,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=CliSessionChain("aicx", runner=runner),
    )

    assert "session catalog returned no matching rows" in pack.body
    assert "has no sessions in this window" not in pack.body
    assert "## Project intentions (primary mission, repo-scoped)" in pack.body
    assert "session-0000" in pack.body
    meta = json.loads(pack.meta_file.read_text(encoding="utf-8"))
    assert meta["empty_kind"] == "empty_project"
    assert meta["session_count"] == 0
    assert meta["project_mission"]["available"] is True
    assert meta["project_mission"]["returned"] == 2


def test_live_supplement_timeout_costs_freshness_not_the_mission(
    tmp_path: Path,
) -> None:
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")

    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        if cmd[1:3] == ["sessions", "list"]:
            return 0, "[]", ""
        if cmd[1] == "intents":
            if "--live" in cmd:
                return 124, "", "timeout"
            return 0, _intents_payload(3, available=3, summary="durable"), ""
        return 1, "", "declined"

    chain = CliSessionChain("aicx", runner=runner)
    intents = chain.project_intents(
        project="vetcoders/vibecrafted", hours=DEFAULT_RESUME_AICX_HOURS
    )
    assert intents.returned == 3
    assert intents.live is False
    assert any("live supplement unavailable (timeout)" in w for w in intents.warnings)

    pack = assemble_resume_continuity_pack(
        agent="codex",
        root=repo,
        hours=DEFAULT_RESUME_AICX_HOURS,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=chain,
    )
    assert "project_mission: `retrieved`" in pack.body
    assert "live scan: no" in pack.body
    assert "live supplement unavailable" in pack.body


def test_live_and_durable_entries_merge_without_duplicating_a_session(
    tmp_path: Path,
) -> None:
    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        if "--live" in cmd:
            return 0, _intents_payload(2, available=2, summary="mission"), ""
        return 0, _intents_payload(3, available=3, summary="mission"), ""

    merged = CliSessionChain("aicx", runner=runner).project_intents(
        project="vetcoders/vibecrafted", hours=DEFAULT_RESUME_AICX_HOURS
    )
    assert merged.live is True
    assert merged.returned == 3
    assert len({item.session_id for item in merged.items}) == 3


# ---------------------------------------------------------------------------
# The whole-pack character budget
# ---------------------------------------------------------------------------


def _oversized_chain(repo: Path) -> CliSessionChain:
    """Every section far larger than the budget, with non-ASCII content."""
    huge_summary = "Misja · przebudowa świeżego wznowienia — " + ("ą" * 3_000)
    sessions = json.dumps(
        [
            {
                "session_id": f"{index:08d}-0000-0000-0000-000000000000",
                "agent": "claude",
                "project": "vetcoders/vibecrafted",
                "repo_path": str(repo),
                "updated_at": "2026-09-13T00:00:00Z",
                "title": "ż" * 200,
            }
            for index in range(30)
        ]
    )

    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        if cmd[1:3] == ["sessions", "list"]:
            return 0, sessions, ""
        if cmd[1] == "intents":
            return 0, _intents_payload(8, available=4_000, summary=huge_summary), ""
        if cmd[1:3] == ["continuity", "show"]:
            return 0, "## NOW\n" + ("ciągłość wielolinijkowa\n" * 3_000), ""
        return 1, "", "declined"

    return CliSessionChain("aicx", runner=runner)


def test_whole_pack_including_frame_never_exceeds_the_character_cap(
    tmp_path: Path,
) -> None:
    """Header, notices, links and the instruction are inside the 18k budget."""
    assert MAX_PACK_CHARS == 18_000
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")
    stamp = dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.timezone.utc)
    pack = assemble_resume_continuity_pack(
        agent="claude",
        root=repo,
        hours=DEFAULT_RESUME_AICX_HOURS,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=_oversized_chain(repo),
        now=stamp,
    )

    body = pack.context_file.read_text(encoding="utf-8")
    assert body == pack.body
    assert len(body) <= MAX_PACK_CHARS
    # Characters, not bytes: the content is deliberately non-ASCII.
    assert len(body.encode("utf-8")) > len(body)
    meta = json.loads(pack.meta_file.read_text(encoding="utf-8"))
    assert meta["pack_chars"] == len(body)
    assert meta["max_pack_chars"] == MAX_PACK_CHARS


def test_impossible_mandatory_frame_fails_before_any_oversized_pack_is_written() -> (
    None
):
    with pytest.raises(ValueError, match=r"mandatory frame exceeds MAX_PACK_CHARS"):
        _compose_bounded_pack(
            header="H" * MAX_PACK_CHARS,
            instruction="INSTRUCTION",
            content={
                name: lambda _budget: ""
                for name in ("intents", "continuity", "catalog")
            },
        )


def test_an_oversized_pack_still_carries_content_not_only_a_pointer(
    tmp_path: Path,
) -> None:
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")
    stamp = dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.timezone.utc)
    pack = assemble_resume_continuity_pack(
        agent="claude",
        root=repo,
        hours=DEFAULT_RESUME_AICX_HOURS,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=_oversized_chain(repo),
        now=stamp,
    )
    body = pack.body

    # The frame survives whole: a pack that drops its own refusal clause to
    # make room for history is worse than a short pack.
    assert "# Resume continuity pack" in body
    assert "## Operator instruction" in body
    assert "Native provider attach requires an explicit operator `--session`." in body
    assert not pack_contains_recover_instruction(body)

    # Real mission content, with the source pointer that makes it checkable.
    assert "## Project intentions (primary mission, repo-scoped)" in body
    assert "Misja · przebudowa świeżego wznowienia" in body
    assert "-very-long-projection-path-segment-0/" in body
    assert "session-0000" in body
    assert str(pack.full_context_file) in body

    # Every retrieved entry is now budgeted rather than dropped, so each one
    # arrives with its own source and says outright that it was shortened.
    for index in range(8):
        assert f"session-{index:04d}.jsonl" in body
    assert body.count(SHORTENED_SUMMARY_NOTE) == 8
    assert "8 shown of 8 returned" in body

    # What the retrieval limit left behind is still counted, never swallowed.
    assert "further entries exist in the window" in body

    # And the private full retrieval keeps everything the pack could not.
    full = pack.full_context_file.read_text(encoding="utf-8")
    assert len(full) > MAX_PACK_CHARS
    assert "session-0007" in full


def test_the_budget_leaves_room_for_evidence_under_a_huge_mission(
    tmp_path: Path,
) -> None:
    """A large mission must not starve the catalog and continuity sections."""
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")
    stamp = dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.timezone.utc)
    pack = assemble_resume_continuity_pack(
        agent="claude",
        root=repo,
        hours=DEFAULT_RESUME_AICX_HOURS,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=_oversized_chain(repo),
        now=stamp,
    )
    assert "## Session catalog (evidence, not a picker)" in pack.body
    assert "## Continuity (supplementary context)" in pack.body
    assert "00000000-0000-0000-0000-000000000000" in pack.body


# ---------------------------------------------------------------------------
# One assembly for every provider
# ---------------------------------------------------------------------------


def test_every_provider_gets_the_same_assembly(tmp_path: Path) -> None:
    """Continuity is repo truth; only the agent name may differ."""
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")
    stamp = dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.timezone.utc)
    bodies: dict[str, str] = {}
    for provider in ("claude", "codex", "gemini", "junie", "grok"):
        pack = assemble_resume_continuity_pack(
            agent=provider,
            root=repo,
            hours=DEFAULT_RESUME_AICX_HOURS,
            context_file=tmp_path / f"{provider}.md",
            meta_file=tmp_path / f"{provider}.meta.json",
            chain=_oversized_chain(repo),
            now=stamp,
        )
        assert f"- agent: `{provider}`" in pack.body
        assert len(pack.body) <= MAX_PACK_CHARS
        assert pack.mode == "new_session"
        bodies[provider] = pack.body.replace(
            f"- agent: `{provider}`", "- agent: `<provider>`"
        ).replace(str(tmp_path / f"{provider}.md"), "<pack>")
    assert len(set(bodies.values())) == 1, (
        "providers must not diverge on how continuity is assembled"
    )


# ---------------------------------------------------------------------------
# The public macOS Bash 3.2 boundary
# ---------------------------------------------------------------------------

_BASH32_HARNESS = """\
set -u
source {shell}
_vetcoders_parse_contract() {{
  _vetcoders_contract_runtime="terminal"
  _vetcoders_contract_execution_runtime=""
  _vetcoders_contract_last=""
  _vetcoders_contract_session=""
  _vetcoders_contract_run_id=""
  _vetcoders_contract_prompt_explicit=""
  _vetcoders_contract_file_explicit=""
  _vetcoders_contract_help=""
  _vetcoders_contract_worktree=""
  _vetcoders_contract_prompt=""
  _vetcoders_contract_file=""
  _vetcoders_contract_base=""
  _vetcoders_contract_tail=""
  _vetcoders_contract_fork_session=""
  _vetcoders_contract_model=""
  _vetcoders_contract_policy_runtime=""
  _vetcoders_contract_permissions=""
  _vetcoders_contract_token_budget=""
  _vetcoders_contract_count=""
  _vetcoders_contract_depth=""
  return 0
}}
_vetcoders_normalize_declared_contract_root() {{ return 0; }}
_vetcoders_core_python_spec() {{ return 0; }}
_vetcoders_declaration_escalate_if_needed() {{
  printf 'argc=%s\\n' "$#"
  for arg in "$@"; do printf 'arg=[%s]\\n' "$arg"; done
  return 0
}}
{invocation}
"""


def _run_bash32(invocation: str) -> subprocess.CompletedProcess[str]:
    shell = (
        Path(__file__).resolve().parents[1]
        / "vibecrafted_core/runtime/shell/lib/marbles.sh"
    )
    script = _BASH32_HARNESS.format(shell=repr(str(shell)), invocation=invocation)
    return subprocess.run(
        ["/bin/bash", "-c", script], text=True, capture_output=True, check=False
    )


def test_bash32_nounset_bare_resume_keeps_a_truly_empty_public_vector() -> None:
    """``vc-resume codex`` with no further words must not invent a blank arg."""
    result = _run_bash32("_vetcoders_resume_agent codex")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "argc=2"
    assert "arg=[]" not in result.stdout
    assert result.stdout.splitlines()[1:3] == ["arg=[resume]", "arg=[codex]"]


def test_bash32_nounset_resume_preserves_a_quoted_non_empty_vector() -> None:
    """Quoted words keep their spelling and their word boundaries."""
    result = _run_bash32(
        '_vetcoders_resume_agent codex --prompt "dwa slowa" --model opus'
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "argc=6"
    assert lines[1:] == [
        "arg=[resume]",
        "arg=[codex]",
        "arg=[--prompt]",
        "arg=[dwa slowa]",
        "arg=[--model]",
        "arg=[opus]",
    ]


# ---------------------------------------------------------------------------
# Budget arithmetic: one huge entry must not eat the ones behind it
# ---------------------------------------------------------------------------


def _lopsided_chain(repo: Path) -> CliSessionChain:
    """First entry far past the section budget, second one tiny.

    Long metadata, long source paths and a retrieval warning are all present,
    because they are exactly what the section footer and each entry's
    provenance have to keep room for.
    """
    payload = json.dumps(
        {
            "results": 2,
            "items": [
                {
                    "kind": "intent",
                    "summary": "PIERWSZA MISJA · " + ("ą" * 30_000),
                    "context": "- ## 2. Mission | ## 3. Context " + ("kontekst " * 40),
                    "project": "vetcoders/vibecrafted",
                    "agent": "codex",
                    "date": "2026-09-13",
                    "session_id": "session-lopsided-first",
                    "source_chunk": (
                        "/Users/tester/.claude/projects/"
                        + ("-long-projection-path-segment" * 6)
                        + "/first.jsonl"
                    ),
                    "claim_scope": "session_close",
                    "freshness_contract": "historical",
                    "verification_state": "not_verified_by_aicx",
                },
                {
                    "kind": "intent",
                    "summary": "DRUGA MISJA — krotka i konkretna",
                    "context": "- ## 2. Mission",
                    "project": "vetcoders/vibecrafted",
                    "agent": "claude",
                    "date": "2026-09-12",
                    "session_id": "session-lopsided-second",
                    "source_chunk": (
                        "/Users/tester/.claude/projects/"
                        + ("-long-projection-path-segment" * 6)
                        + "/second.jsonl"
                    ),
                    "claim_scope": "session_close",
                    "freshness_contract": "historical",
                    "verification_state": "not_verified_by_aicx",
                },
            ],
            "completeness": {
                "complete": False,
                "requested_limit": PROJECT_INTENT_LIMIT,
                "available_before_limit": 9,
                "matched_project_buckets": ["vetcoders/vibecrafted"],
                "identity_source": "project-bucket-v1",
                "warnings": ["durable catalog rebuilt " + ("d" * 300)],
                "scope": {"match_mode": "exact"},
            },
        }
    )

    def runner(
        cmd: list[str], timeout: float, cwd: Path | None
    ) -> tuple[int, str, str]:
        if cmd[1:3] == ["sessions", "list"]:
            return 0, "[]", ""
        if cmd[1] == "intents":
            return 0, payload, ""
        if cmd[1:3] == ["continuity", "show"]:
            return 0, "## NOW\n" + ("ciągłość\n" * 2_000), ""
        return 1, "", "declined"

    return CliSessionChain("aicx", runner=runner)


def test_an_oversized_first_entry_does_not_starve_the_next_one(
    tmp_path: Path,
) -> None:
    """The defect: a 30k summary took the whole section and emitted nothing."""
    repo = make_checkout(tmp_path / "vibecrafted", "vetcoders/vibecrafted")
    pack = assemble_resume_continuity_pack(
        agent="claude",
        root=repo,
        hours=DEFAULT_RESUME_AICX_HOURS,
        context_file=tmp_path / "pack.md",
        meta_file=tmp_path / "pack.meta.json",
        chain=_lopsided_chain(repo),
    )
    body = pack.body

    # The whole pack, frame included, stays inside the hard cap.
    assert len(body) <= MAX_PACK_CHARS

    # Both missions are actually there -- neither was replaced by a pointer.
    assert "PIERWSZA MISJA" in body
    assert "DRUGA MISJA — krotka i konkretna" in body

    # And each shown summary kept the source that makes it checkable.
    assert "/first.jsonl" in body
    assert "/second.jsonl" in body
    assert "session-lopsided-first" in body
    assert "session-lopsided-second" in body

    # The oversized one says it was shortened; the short one is untouched.
    assert body.count(SHORTENED_SUMMARY_NOTE) == 1

    # Stats are the real counts, and the footer survived the section budget.
    assert "_retrieval: 2 shown of 2 returned, 9 available in window (limit 8)" in body
    assert "7 further entries exist in the window" in body
    assert "durable catalog rebuilt" in body

    # The frame and the other evidence sections are still whole.
    assert "## Operator instruction" in body
    assert "Native provider attach requires an explicit operator `--session`." in body
    assert "## Continuity (supplementary context)" in body


def test_a_section_too_small_for_an_entry_says_so_instead_of_going_silent() -> None:
    """Below the entry floor the section still prints its own stats."""
    intents = ProjectIntents(
        items=[
            IntentItem(
                summary="M" * 4_000,
                source_chunk=f"/proof/{index}.jsonl",
                session_id=f"s{index}",
            )
            for index in range(4)
        ],
        available=12,
        limit=8,
        complete=False,
    )
    # Room for the preamble and the footer, not for an entry.
    starved = _render_intents_section(intents, budget=900)
    assert len(starved) <= 900
    assert "### " not in starved
    assert "_retrieval: 0 shown of 4 returned, 12 available" in starved
    assert "4 retrieved entries omitted for the injection budget" in starved
    assert "8 further entries exist in the window" in starved

    # Give it room and the same four entries all arrive: equal-sized entries
    # get an equal share, so they share a fate rather than the first one
    # spending the budget of the three behind it.
    roomy = _render_intents_section(intents, budget=3_000)
    assert len(roomy) <= 3_000
    assert roomy.count("\n### ") == 4
    assert "_retrieval: 4 shown of 4 returned, 12 available" in roomy
    assert "omitted for the injection budget" not in roomy
    assert "8 further entries exist in the window" in roomy
    for index in range(4):
        assert f"/proof/{index}.jsonl" in roomy


def test_an_entry_is_never_printed_without_its_source() -> None:
    """Provenance is the point of an entry; a bare sentence is not shippable."""
    for budget in range(400, 4_000, 37):
        intents = ProjectIntents(
            items=[
                IntentItem(summary="A" * 9_000, source_chunk="/proof/a.jsonl"),
                IntentItem(summary="B" * 20, source_chunk="/proof/b.jsonl"),
            ],
            available=2,
            limit=8,
            complete=True,
        )
        section = _render_intents_section(intents, budget=budget)
        assert len(section) <= budget, budget
        for marker, source in (("AAAA", "/proof/a.jsonl"), ("BBBB", "/proof/b.jsonl")):
            if marker in section:
                assert source in section, (budget, marker)
        if "AAAA" in section:
            # A shown summary is either whole or long enough to act on. The
            # floor is the room offered; the ellipsis is spent out of it.
            assert section.count("A") >= MIN_INTENT_SUMMARY_CHARS - 3


def test_a_merge_that_drops_entries_is_not_reported_as_complete() -> None:
    """Two complete halves do not make a complete union past the limit."""

    def half(prefix: str) -> ProjectIntents:
        return ProjectIntents(
            items=[
                IntentItem(summary=f"{prefix}-{i}", session_id=f"{prefix}-s{i}")
                for i in range(6)
            ],
            available=6,
            limit=8,
            complete=True,
        )

    merged = _merge_project_intents(half("a"), half("b"), limit=8)
    assert merged.returned == 8
    assert merged.available == 12
    assert merged.omitted == 4
    assert merged.complete is False, (
        "a union truncated back to the limit is not complete"
    )

    # A union that fits keeps the completeness both halves reported.
    small = _merge_project_intents(half("a"), half("b"), limit=12)
    assert small.returned == 12
    assert small.omitted == 0
    assert small.complete is True
