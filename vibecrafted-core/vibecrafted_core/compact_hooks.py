"""PreCompact/PostCompact hook logic: extract session history via aicx before a
compaction and replay the unseen delta back to the agent afterward."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .clock import utc_now_z
from .runtime_paths import vibecrafted_home

AICX_EXTRACT_TIMEOUT_SECONDS = 90.0


def load_hook_input(stdin: str) -> dict[str, object]:
    """Parse the hook's JSON stdin payload; returns {} on empty/malformed input."""
    try:
        payload = json.loads(stdin or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def hook_agent() -> str:
    """Resolve the invoking agent name from env, defaulting to ``claude``."""
    return os.environ.get("VIBECRAFTED_COMPACT_AGENT") or os.environ.get(
        "VIBECRAFTED_AGENT", "claude"
    )


def aicx_extract_timeout_seconds() -> float:
    """Timeout budget for one ``aicx extract`` subprocess call, floored at 0.1s."""
    raw = os.environ.get(
        "VIBECRAFTED_AICX_EXTRACT_TIMEOUT_SECONDS",
        str(AICX_EXTRACT_TIMEOUT_SECONDS),
    )
    try:
        timeout = float(raw)
    except ValueError:
        timeout = AICX_EXTRACT_TIMEOUT_SECONDS
    return max(timeout, 0.1)


def codex_sessions_dir() -> Path:
    """Root directory of codex session JSONL files (env-overridable)."""
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    return Path(
        os.environ.get("CODEX_SESSIONS_DIR", str(codex_home / "sessions"))
    ).expanduser()


def normalize_path(value: str) -> str:
    """Expand and resolve a path to its canonical absolute form; best-effort on OSError."""
    if not value:
        return ""
    try:
        return str(Path(value).expanduser().resolve())
    except OSError:
        return str(Path(value).expanduser())


def hook_project_dir(payload: dict[str, object]) -> str:
    """Best-effort project directory for this hook invocation: payload cwd/
    project_dir/repository fields, else CODEX_PROJECT_DIR/CLAUDE_PROJECT_DIR
    env, else the process cwd."""
    raw = str(
        payload.get("cwd")
        or payload.get("project_dir")
        or payload.get("repository")
        or os.environ.get("CODEX_PROJECT_DIR")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.getcwd()
    )
    return normalize_path(raw)


def session_meta_from_jsonl(path: Path) -> dict[str, str]:
    """Scan a codex session JSONL for its ``session_meta`` event and return
    {"id", "cwd"}; {} if the file is unreadable or carries no such event."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if (
                    '"type":"session_meta"' not in line
                    and '"type": "session_meta"' not in line
                ):
                    continue
                event = json.loads(line)
                payload = event.get("payload") if isinstance(event, dict) else None
                if not isinstance(payload, dict):
                    continue
                return {
                    "id": str(payload.get("id") or "").strip(),
                    "cwd": normalize_path(str(payload.get("cwd") or "")),
                }
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def session_id_from_jsonl(path: Path) -> str:
    """Session id parsed from a codex session JSONL's ``session_meta`` event."""
    return session_meta_from_jsonl(path).get("id", "")


def latest_codex_session_path(project_dir: str = "") -> Path | None:
    """Most recently modified codex session file, scoped to ``project_dir`` when
    a scoped match exists; falls back to the newest file overall otherwise."""
    root = codex_sessions_dir()
    if not root.is_dir():
        return None
    candidates = [path for path in root.glob("**/*.jsonl") if path.is_file()]
    if project_dir:
        scoped: list[Path] = []
        for path in candidates:
            meta = session_meta_from_jsonl(path)
            if meta.get("cwd") == project_dir:
                scoped.append(path)
        if scoped:
            candidates = scoped
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def codex_session_path_for(session_id: str) -> Path | None:
    """Locate a codex session file by id: filename glob first, then a full
    session_meta scan fallback for files not named after their session id."""
    if not session_id:
        return None
    root = codex_sessions_dir()
    if not root.is_dir():
        return None
    for path in root.glob(f"**/*{session_id}*.jsonl"):
        if path.is_file():
            return path
    for path in root.glob("**/*.jsonl"):
        if path.is_file() and session_id_from_jsonl(path) == session_id:
            return path
    return None


def resolve_session_id(payload: dict[str, object], agent: str) -> str:
    """Resolve the active session id for the hook payload.

    Non-codex agents use the explicit ``session_id``/``conversation_id`` field
    verbatim. Codex resolves the true session id via its JSONL transcript
    (session_meta event), since the payload's session/conversation id may not
    match the on-disk session file's own identity.
    """
    explicit = str(
        payload.get("session_id") or payload.get("conversation_id") or ""
    ).strip()
    if agent != "codex":
        return explicit

    transcript = str(
        payload.get("transcript_path")
        or payload.get("transcript")
        or payload.get("session_path")
        or ""
    ).strip()
    if transcript:
        transcript_path = Path(transcript).expanduser()
        if transcript_path.is_file():
            parsed = session_id_from_jsonl(transcript_path)
            if parsed:
                return parsed

    if explicit:
        session_path = codex_session_path_for(explicit)
        if session_path:
            parsed = session_id_from_jsonl(session_path)
            return parsed or explicit
        return explicit

    latest = latest_codex_session_path(hook_project_dir(payload))
    if latest:
        return session_id_from_jsonl(latest)
    return ""


def compact_state_path() -> Path:
    """Path to the JSON file tracking per-session compact-hook delta cursors."""
    return Path(
        os.environ.get(
            "VIBECRAFTED_COMPACT_STATE",
            str(vibecrafted_home() / "runtime" / "compact-hook-state.json"),
        )
    ).expanduser()


def load_compact_state() -> dict[str, Any]:
    """Load the compact-hook state file; {} when missing or unparsable."""
    path = compact_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_compact_state(state: dict[str, Any]) -> None:
    """Persist the compact-hook state file; failures are swallowed (best-effort)."""
    path = compact_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return


def delta_lines(
    agent: str,
    session_id: str,
    extract: Path,
    raw_lines: list[str],
    project_dir: str,
) -> tuple[list[str], int, int]:
    """Slice the unseen tail of ``raw_lines`` since this session's last recorded
    cursor, then persist the new cursor. Returns (delta, previous_count,
    current_count); a stale/out-of-range previous cursor resets to 0."""
    state = load_compact_state()
    sessions = state.setdefault("sessions", {})
    key = f"{agent}:{session_id}"
    previous = sessions.get(key, {}) if isinstance(sessions, dict) else {}
    previous_count = (
        int(previous.get("raw_line_count", 0) or 0) if isinstance(previous, dict) else 0
    )
    if previous_count < 0 or previous_count > len(raw_lines):
        previous_count = 0
    delta = raw_lines[previous_count:]
    if isinstance(sessions, dict):
        sessions[key] = {
            "agent": agent,
            "session_id": session_id,
            "project_dir": project_dir,
            "extract": str(extract),
            "raw_line_count": len(raw_lines),
            "updated_at": utc_now_z(),
        }
        save_compact_state(state)
    return delta, previous_count, len(raw_lines)


def emit_postcompact_context(text: str, output_format: str = "json") -> str:
    """Wrap ``text`` as the PostCompact hookSpecificOutput JSON envelope, or
    return it verbatim when ``output_format == "plain"``."""
    if output_format == "plain":
        return text
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostCompact",
                "additionalContext": text,
            }
        },
        ensure_ascii=False,
    )


def append_journal(
    event: str, agent: str, session_id: str, status: str, detail: str
) -> None:
    """Append one JSONL audit record of a compact-hook run; failures are silent."""
    journal = Path(
        os.environ.get(
            "VIBECRAFTED_COMPACT_JOURNAL",
            str(vibecrafted_home() / "runtime" / "compact-hooks.jsonl"),
        )
    ).expanduser()
    try:
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "ts": utc_now_z(),
                        "event": event,
                        "agent": agent,
                        "session_id": session_id,
                        "status": status,
                        "detail": detail,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    except OSError:
        return


def run_aicx_extract(agent: str, session_id: str, *, user_only: bool = False) -> str:
    """Run ``aicx extract`` for a session; returns "missing"/"timeout"/"ok"/"failed"."""
    if not shutil.which("aicx"):
        return "missing"
    command = [
        "aicx",
        "extract",
        "--agent",
        agent,
        "--session",
        session_id,
        "--conversation",
    ]
    if user_only:
        command.append("--user-only")
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=aicx_extract_timeout_seconds(),
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    return "ok" if result.returncode == 0 else "failed"


def precompact(stdin: str) -> int:
    """PreCompact hook body: extract the session's conversation + user-only
    transcripts via aicx before Claude/Codex compacts, and journal the outcome."""
    payload = load_hook_input(stdin)
    agent = hook_agent()
    session_id = resolve_session_id(payload, agent)
    if not session_id:
        return 0
    if not shutil.which("aicx"):
        append_journal("precompact", agent, session_id, "skipped", "aicx not found")
        return 0
    conversation_status = run_aicx_extract(agent, session_id)
    user_only_status = run_aicx_extract(agent, session_id, user_only=True)
    status = (
        "extracted"
        if conversation_status == "ok" and user_only_status == "ok"
        else "degraded"
    )
    append_journal(
        "precompact",
        agent,
        session_id,
        status,
        f"conversation={conversation_status}; user_only={user_only_status}",
    )
    return 0


def extract_candidates(agent: str, session_id: str) -> list[Path]:
    """Ordered candidate paths for a session's aicx extract, including the
    claude-namespace fallback for non-claude agents that share its store."""
    candidates = [
        Path.home() / ".aicx" / "extracts" / agent / f"{session_id}_conversation.md",
        Path.home() / ".aicx" / "extracts" / agent / f"{session_id}.md",
    ]
    if agent != "claude":
        candidates.extend(
            [
                Path.home()
                / ".aicx"
                / "extracts"
                / "claude"
                / f"{session_id}_conversation.md",
                Path.home() / ".aicx" / "extracts" / "claude" / f"{session_id}.md",
            ]
        )
    return candidates


def strip_skill_bodies(lines: list[str]) -> list[str]:
    """Replace ``Base directory for this skill: ...`` fenced skill bodies with a
    one-line placeholder, trimming bulk that PostCompact recall does not need."""
    output: list[str] = []
    in_skill = False
    for line in lines:
        if line.startswith("Base directory for this skill: "):
            skill_name = Path(line.strip().rsplit("/", 1)[-1]).name or "unknown"
            output.append(f"[SKILL BODY STRIPPED: {skill_name}]\n")
            in_skill = True
            continue
        if in_skill and line.strip() == "````":
            in_skill = False
            continue
        if not in_skill:
            output.append(line)
    return output


def dedupe_adjacent(lines: list[str]) -> list[str]:
    """Collapse consecutive duplicate lines, keeping the first of each run."""
    output: list[str] = []
    previous: str | None = None
    for line in lines:
        if line == previous:
            continue
        output.append(line)
        previous = line
    return output


def clean_context(text: str) -> str:
    """Strip control characters (except newline/tab) that would corrupt hook JSON."""
    return "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)


def fallback(
    reason: str,
    extract_path: Path | None = None,
    output_format: str = "json",
) -> str:
    """Build the degraded-recall PostCompact context: names the reason and points
    the agent at manual loctree/aicx recovery commands instead of pretending
    the compact summary is full temporal truth."""
    extract = str(extract_path) if extract_path else "<none>"
    return emit_postcompact_context(
        "\n".join(
            [
                f"POSTCOMPACT RECALL DEGRADED: {reason}",
                "",
                "Your turn-by-turn memory was just compacted and rich AICX recall could not be built.",
                "Before acting on earlier session state, recover context explicitly:",
                f"- inspect the raw extract if it exists: {extract}",
                "- run: loct context --full --markdown",
                "- run: aicx intents -p <project> --limit 20 --emit markdown",
                "- fallback: aicx search --no-semantic -p <project> 'recent intent agent claims verified outcomes unresolved human decisions'",
                "- run: vibecrafted loop status",
                "- for active workers: use vibecrafted await <agent> --run-id <id> and vibecrafted observe <agent> --run-id <id>; do not hand-roll sleep/ps/stat probes",
                "",
                "Do not treat the lossy compact summary as full temporal truth.",
            ]
        ),
        output_format,
    )


def emit_context(text: str, output_format: str) -> None:
    """Clean and print one PostCompact context payload to stdout."""
    print(emit_postcompact_context(clean_context(text), output_format))


def postcompact(stdin: str, output_format: str | None = None) -> int:
    """PostCompact hook body: locate the session's aicx extract, slice the delta
    since the last compact, chunk it to bounded files, and emit a recall
    context pointing the agent at the newest chunk plus recovery commands."""
    output_format = output_format or os.environ.get(
        "VIBECRAFTED_COMPACT_OUTPUT", "json"
    )
    if output_format == "empty-json":
        print("{}")
        return 0

    payload = load_hook_input(stdin)
    agent = hook_agent()
    session_id = resolve_session_id(payload, agent)
    if not session_id:
        if output_format != "plain":
            print("{}")
        return 0
    candidates = extract_candidates(agent, session_id)
    extract = next((candidate for candidate in candidates if candidate.is_file()), None)
    if extract is None:
        print(
            fallback(
                (
                    f"aicx extract not found for agent={agent}, session={session_id}; "
                    "precompact should create it before postcompact recall"
                ),
                candidates[0],
                output_format,
            )
        )
        return 0

    raw_lines = extract.read_text(encoding="utf-8", errors="replace").splitlines(
        keepends=True
    )
    if not raw_lines:
        print(fallback("extract is empty", extract, output_format))
        return 0

    project_dir = hook_project_dir(payload)
    scoped_lines, previous_count, current_count = delta_lines(
        agent, session_id, extract, raw_lines, project_dir
    )
    lines = scoped_lines
    if os.environ.get("AICX_RECALL_STRIP_SKILLS", "1") == "1":
        lines = strip_skill_bodies(lines)
    if os.environ.get("AICX_RECALL_DEDUP", "1") == "1":
        lines = dedupe_adjacent(lines)

    chunk_size = max(int(os.environ.get("AICX_RECALL_CHUNK_LINES", "400") or "400"), 1)
    recall_root = Path(
        os.environ.get(
            "VIBECRAFTED_RECALL_DIR",
            str(Path(os.environ.get("TMPDIR", "/tmp")) / "vibecrafted-aicx-recall"),
        )
    ).expanduser()
    chunk_dir = recall_root / agent / session_id
    try:
        chunk_dir.mkdir(parents=True, exist_ok=True)
        for stale in chunk_dir.glob("chunk-*"):
            if stale.is_file():
                stale.unlink()
        chunks = [lines[i : i + chunk_size] for i in range(0, len(lines), chunk_size)]
        if not chunks:
            context = "\n".join(
                [
                    f"Session {session_id} was just compacted. AICX has no new delta since the previous compact recall.",
                    "",
                    "Context discipline after compaction:",
                    "- Keep continuity through AICX, not lossy compact memory.",
                    "- Dispatcher continuity: use vibecrafted await <agent> --run-id <id> and vibecrafted observe <agent> --run-id <id>; never replace them with manual sleep/ps/stat/git probes.",
                    f"- Full raw extract remains available: {extract}",
                    f"- Cursor: raw lines {previous_count}..{current_count} for project {project_dir or '<unknown>'}.",
                ]
            )
            emit_context(context, output_format)
            return 0
        for idx, chunk in enumerate(chunks):
            (chunk_dir / f"chunk-{idx:03d}").write_text(
                "".join(chunk),
                encoding="utf-8",
            )
    except OSError as exc:
        print(fallback(f"could not build recall chunks: {exc}", extract, output_format))
        return 0

    reduction_pct = 0
    if raw_lines:
        reduction_pct = 100 - (len(lines) * 100 // len(raw_lines))
    last_chunk = chunk_dir / f"chunk-{len(chunks) - 1:03d}"
    context = "\n".join(
        [
            f"Session {session_id} was just compacted. AICX delta recall is available as bounded chunks.",
            "",
            "Context discipline after compaction:",
            "- LOOP is the foundation: run or inspect vibecrafted loop status before claiming continuity.",
            "- Loctree + AICX are the constant context: refresh with loct context --full --markdown and aicx intents/search when earlier turns matter.",
            "- Dispatcher continuity: use vibecrafted await <agent> --run-id <id> and vibecrafted observe <agent> --run-id <id>; never replace them with manual sleep/ps/stat/git probes.",
            f"- Delta cursor: raw lines {previous_count}..{current_count} for project {project_dir or '<unknown>'}.",
            f"- Delta recall chunks: {chunk_dir}/chunk-000 through {last_chunk} ({len(chunks)} chunks, skill-stripped + deduped, {reduction_pct}% smaller).",
            f"- Full raw extract: {extract}",
            "",
            "Only the new recovered delta is loaded below. Remember: when the task seems to be touching earlier decisions, you obligatory must recall previous intents, claims, or unresolved operator choices via `aicx intents`.",
            "",
            f"== VERBATIM AICX DELTA ({last_chunk.name}) ==",
            last_chunk.read_text(encoding="utf-8", errors="replace"),
            "== END AICX DELTA ==",
        ]
    )
    emit_context(context, output_format)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """``vibecrafted compact-hook`` entrypoint: dispatches precompact/postcompact/
    postcompact-noop/recall events against JSON stdin."""
    parser = argparse.ArgumentParser(prog="vibecrafted compact-hook")
    parser.add_argument(
        "event",
        choices=("precompact", "postcompact", "postcompact-noop", "recall"),
    )
    args = parser.parse_args(argv)
    if args.event == "postcompact-noop":
        print("{}")
        return 0
    stdin = sys.stdin.read()
    if args.event == "precompact":
        return precompact(stdin)
    if args.event == "recall":
        return postcompact(stdin, output_format="plain")
    return postcompact(stdin)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
