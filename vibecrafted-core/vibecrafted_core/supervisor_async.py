"""Async supervisor: spawns agent processes, streams output, and settles run state."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .agent_stream import (
    AgentStreamParser,
    is_grok_ignorable_transport_error,
    resolve_default_model,
)
from .artifacts import ArtifactValidation, validate_artifacts
from .control_plane import (
    control_plane_home,
    ensure_session_id,
    lookup_run,
    normalize_run_root,
)
from .events import append_event
from .lifecycle import EventKind, RunState
from .model_overrides import _model_override_receipt
from .process_control import process_identity_receipt
from .report_contract import CLAIM_DIGEST_ENV
from .run_mutation import RunMetaMutationError, mutate_run_meta

STDIO_LIMIT_BYTES = 16 * 1024 * 1024
# Well under the reconciler's 120s staleness threshold, so an ordinary talking
# worker is never mistaken for a dead one, while a long tool call still emits
# at most a handful of pulse events.
_HEARTBEAT_INTERVAL_SECONDS = 20.0

# The heartbeat above tells "quiet" apart from "dead"; it never tells 20 seconds
# of quiet apart from three hours of it. Every pulse looks identical, so a worker
# blocked forever in `wait4` held the supervisor open forever — which made the
# RunState.STALLED transition in `run()` unreachable in production, because the
# only thing that could reach it was `--timeout` and no caller ever passed one.
#
# A wall-clock cap is the wrong instrument: it would also kill a worker that is
# legitimately long AND talking. Bound the SILENCE instead — the signal the
# watcher already measures — so productive runs are never touched and a mute one
# becomes a recorded stall with an exit code. Resolved at the watcher, not at the
# caller: a bound that has to be threaded is a bound nobody arms.
#
# 0 or negative disables it; VIBECRAFTED_SILENCE_TIMEOUT_SECONDS overrides.
_SILENCE_TIMEOUT_ENV = "VIBECRAFTED_SILENCE_TIMEOUT_SECONDS"
_DEFAULT_SILENCE_TIMEOUT_SECONDS = 1800.0


class _SilenceTimeout(asyncio.TimeoutError):
    """A live worker produced no output for longer than the silence bound.

    Subclasses ``asyncio.TimeoutError`` so the wall-clock ``wait_for`` handler in
    ``run()`` settles both stall kinds through one already-tested path, while the
    recorded reason still says which instrument fired.
    """


def _default_silence_timeout() -> float:
    """Seconds of unbroken stdout silence after which a live worker is stalled."""
    raw = os.environ.get(_SILENCE_TIMEOUT_ENV, "").strip()
    if not raw:
        return _DEFAULT_SILENCE_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        # An unparseable override must not silently disable the bound.
        return _DEFAULT_SILENCE_TIMEOUT_SECONDS


def _utc_now() -> datetime:
    """Current UTC-aware datetime."""
    return datetime.now(timezone.utc)


def transcript_human_path(transcript_path: Path | None) -> Path | None:
    """Sibling human-readable rendering of the raw transcript.

    The raw file is a machine contract (await, session-id extraction, salvage)
    and must stay byte-exact provider output. Streaming-json providers such as
    grok are unreadable there, so the AgentStreamParser rendering that
    ``_watch_process`` already produces is persisted next to it instead of
    being discarded when no terminal tee is attached.
    """
    if transcript_path is None:
        return None
    return transcript_path.with_name(
        transcript_path.stem + ".human" + transcript_path.suffix
    )


def _infer_agent(command: Sequence[str]) -> str:
    """Guess the agent name from argv[0] when VIBECRAFTED_AGENT is not set."""
    if not command:
        return "agent"
    name = Path(str(command[0])).name
    if name in {"claude", "codex", "agy", "junie", "grok"}:
        return name
    if name in {"python", "python3"}:
        return "python"
    return name or "agent"


def _json_text_fragment(event: dict[str, object]) -> str:
    """Extract the display text (if any) from one parsed JSON stream event line."""
    event_type = str(event.get("type") or "")
    if event_type == "thought":
        return ""
    if event_type == "item.completed":
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            return text if isinstance(text, str) else ""
    if event_type == "text":
        value = event.get("data")
        return value if isinstance(value, str) else ""
    for key in ("message", "text", "content", "data"):
        value = event.get(key)
        if isinstance(value, str):
            return value
    return ""


def _fallback_report_body(transcript_text: str) -> str:
    """Reconstruct a readable report body from raw JSON/plain transcript lines.

    Used when a worker exits 0 without writing its own report; skips
    grok transport-noise lines via ``is_grok_ignorable_transport_error``.
    """
    text_fragments: list[str] = []
    plain_lines: list[str] = []
    for line in transcript_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("{"):
            try:
                loaded = json.loads(stripped)
            except json.JSONDecodeError:
                if not is_grok_ignorable_transport_error(line):
                    plain_lines.append(line)
                continue
            if isinstance(loaded, dict):
                text_fragments.append(_json_text_fragment(loaded))
            continue
        if is_grok_ignorable_transport_error(line):
            continue
        plain_lines.append(line)
    body = "".join(text_fragments).strip()
    if body:
        return body + "\n"
    return "\n".join(plain_lines).strip() + ("\n" if plain_lines else "")


def _tokens_total(
    input_tokens: int, cached_input_tokens: int, output_tokens: int
) -> int:
    """Sum usage without double-counting provider-specific cache shapes.

    Claude/Codex: ``input`` already includes cache hits (cached ≤ input).
    Junie-style: ``input`` is non-cached only and ``cached`` is additive
    (cached can exceed input). Detect by comparing magnitudes.
    """
    inp = max(0, int(input_tokens or 0))
    cached = max(0, int(cached_input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    if cached and cached > inp:
        return inp + cached + out
    return inp + out


def _handle_tokens_total(handle: AsyncRunHandle) -> int:
    """Total token usage for a run handle, deduplicating cache-shape overlap."""
    return _tokens_total(
        handle.tokens_input, handle.tokens_cached_input, handle.tokens_output
    )


def _origin_fields_from_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Durable origin identity for post-run triage (dispatcher has no pane env)."""
    source = env if env is not None else os.environ

    def _get(*names: str) -> str:
        """Return the first non-empty stripped value found among *names* in the env source."""
        for name in names:
            value = str(source.get(name, "") or "").strip()
            if value:
                return value
        return ""

    fields: dict[str, str] = {}
    session = _get(
        "VIBECRAFTED_WORKER_SESSION",
        "VIBECRAFTED_OPERATOR_SESSION",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ_SESSION_NAME",
    )
    if session:
        fields["origin_session"] = session
        fields["operator_session"] = session
    # The run's tab is named by run id (spawn contract). A dispatcher's ambient
    # VC_FRAME_TAB_NAME instead names the *operator's* tab — stamping it would
    # hand triage a tab to capture and close that was never the run's. So the
    # tab comes only from the run-id envs, and the ambient tab claim serves one
    # purpose: proving this process sits in the run's own tab, which is the
    # only case where the ambient pane id is the run's pane. (2026-07-25:
    # dispatched runs stamped the operator's pane "1"; the scrollback dump
    # aimed at it found nothing and the tabs never reached their buckets.)
    tab = _get("VIBECRAFTED_RUN_ID", "SPAWN_RUN_ID")
    if session and tab:
        fields["origin_tab"] = tab
    pane = _get("VC_FRAME_PANE_ID", "ZELLIJ_PANE_ID")
    if session and pane and tab and _get("VC_FRAME_TAB_NAME") == tab:
        fields["origin_pane_id"] = pane
    dispatch_fields = {
        "resolved_worktree_path": "VIBECRAFTED_DISPATCH_WORKTREE",
        "branch": "VIBECRAFTED_DISPATCH_BRANCH",
        "baseline_sha": "VIBECRAFTED_DISPATCH_BASELINE_SHA",
        "target_path": "CARGO_TARGET_DIR",
        "artifact_path": "VIBECRAFTED_DISPATCH_ARTIFACT_PATH",
        "dependency_set": "VIBECRAFTED_DISPATCH_DEPENDENCIES",
        "scheduler_slot": "VIBECRAFTED_DISPATCH_SCHEDULER_SLOT",
        "integrator_exclusivity": "VIBECRAFTED_DISPATCH_INTEGRATOR",
        "cut_id": "VIBECRAFTED_DISPATCH_CUT_ID",
    }
    for metadata_field, variable in dispatch_fields.items():
        value = _get(variable)
        if value:
            fields[metadata_field] = value
    return fields


def _accepted_operator_stop(run_id: str) -> dict[str, object] | None:
    """Read the durable operator-stop authority after the worker exits."""

    try:
        run = lookup_run(run_id)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    if (
        isinstance(run, dict)
        and str(run.get("state") or "") == "stopped"
        and run.get("operator_stop_accepted") is True
    ):
        return run
    return None


def _cache_write_line(prefix: str, value: int | None) -> str:
    """Render a `tokens_cache_write:` frontmatter/footer line, or "" when value is None."""
    return f"{prefix}tokens_cache_write: {value}\n" if value is not None else ""


def _render_fallback_report(handle: AsyncRunHandle, transcript_text: str) -> str:
    """Build a complete salvaged markdown report (frontmatter + body) for a silent worker."""
    body = _fallback_report_body(transcript_text)
    if not body:
        body = (
            "Worker exited successfully without writing a standalone report and "
            "without captured transcript text.\n"
        )
    now = (
        handle.completed_at.isoformat()
        if handle.completed_at
        else _utc_now().isoformat()
    )
    from .report_contract import render_minimal_frontmatter

    skill = handle.skill or str(
        os.environ.get("VIBECRAFTED_SKILL_NAME")
        or os.environ.get("VIBECRAFTED_SKILL_CODE")
        or "unknown"
    )
    header = render_minimal_frontmatter(
        run_id=handle.run_id,
        agent=handle.agent or "unknown",
        skill=skill,
        status="completed",
        extra={
            "claim_status": "completed",
            "claim_kind": skill,
            "session_id": handle.agent_session_id or "unknown",
            "tokens_input": handle.tokens_input,
            "tokens_cached_input": handle.tokens_cached_input,
            "tokens_output": handle.tokens_output,
            "tokens_total": _handle_tokens_total(handle),
            "cost_usd": handle.cost_usd if handle.cost_usd is not None else "unknown",
            "completed_at": now,
            "fallback_report": "true",
            **(
                {"tokens_cache_write": handle.tokens_cache_write}
                if handle.tokens_cache_write is not None
                else {}
            ),
        },
    )
    return (
        header
        + f"{body}\n"
        + "## Runtime fallback\n\n"
        + "The worker exited with code 0 but did not write "
        + "`VIBECRAFTED_REPORT_PATH`; Vibecrafted salvaged this report from the "
        + "captured transcript.\n"
    )


def _terminal_frontmatter(handle: AsyncRunHandle) -> str:
    """Render the launching-state frontmatter block written to stdout when tee_output is on."""
    model_requested = (
        f"model_requested: {handle.model_requested}\n" if handle.model_requested else ""
    )
    return (
        "---\n"
        "runner: vibecrafted\n"
        f"run_id: {handle.run_id}\n"
        f"agent: {handle.agent}\n"
        f"{model_requested}"
        f"root: {handle.root}\n"
        f"report: {handle.report_path or ''}\n"
        f"transcript: {handle.transcript_path or ''}\n"
        "status: launching\n"
        "---\n"
    )


def _terminal_footer(handle: AsyncRunHandle) -> str:
    """Render the terminal-state summary footer written to stdout when tee_output is on."""
    model_requested = (
        f"model_requested: {handle.model_requested}\n" if handle.model_requested else ""
    )
    override_skipped = (
        f"model_override_skipped: {str(handle.model_override_skipped).lower()}\n"
        if handle.model_requested
        else ""
    )
    cost_source = f"cost_source: {handle.cost_source}\n" if handle.cost_source else ""
    return (
        "\n---\n"
        "runner: vibecrafted\n"
        f"run_id: {handle.run_id}\n"
        f"status: {handle.state.value}\n"
        f"exit_code: {handle.exit_code if handle.exit_code is not None else 'unknown'}\n"
        f"session_id: {handle.agent_session_id or 'unknown'}\n"
        f"model: {handle.agent_model or 'unknown'}\n"
        f"{model_requested}"
        f"{override_skipped}"
        f"tokens_input: {handle.tokens_input}\n"
        f"tokens_cached_input: {handle.tokens_cached_input}\n"
        f"{_cache_write_line('', handle.tokens_cache_write)}"
        f"tokens_output: {handle.tokens_output}\n"
        f"tokens_total: {_handle_tokens_total(handle)}\n"
        f"cost_usd: {handle.cost_usd if handle.cost_usd is not None else 'unknown'}\n"
        f"{cost_source}"
        f"resume: {handle.resume_command}\n"
        f"report: {handle.report_path or ''}\n"
        f"transcript: {handle.transcript_path or ''}\n"
        "---\n"
    )


def _write_terminal(text: str) -> None:
    """Write *text* to stdout and flush immediately (used for tee_output)."""
    sys.stdout.write(text)
    sys.stdout.flush()


@dataclass
class AsyncRunHandle:
    """Mutable state of one asynchronously supervised agent run, live or terminal."""

    run_id: str
    command: tuple[str, ...]
    root: Path
    process: asyncio.subprocess.Process
    started_at: datetime
    meta_path: Path | None = None
    report_path: Path | None = None
    transcript_path: Path | None = None
    pgid: int | None = None
    states: list[RunState] = field(default_factory=lambda: [RunState.CREATED])
    exit_code: int | None = None
    completed_at: datetime | None = None
    artifact_validation: ArtifactValidation | None = None
    first_output_seen: bool = False
    session_id: str = ""
    agent: str = ""
    skill: str = ""
    agent_session_id: str = ""
    agent_model: str = ""
    claim_digest: str = ""
    model_requested: str = ""
    model_override_supported: bool = False
    model_override_skipped: bool = False
    model_override_skip_reason: str = ""
    tokens_input: int = 0
    tokens_cached_input: int = 0
    tokens_cache_write: int | None = None
    tokens_output: int = 0
    cost_usd: float | None = None
    cost_source: str | None = None
    resume_command: str = ""
    heartbeat_monotonic: float = 0.0
    worker_identity: dict[str, object] | None = None
    workspace_fields: dict[str, object] = field(default_factory=dict)
    operator_stopped: bool = False
    operator_stop_reason: str = ""

    @property
    def state(self) -> RunState:
        """Current (most recent) lifecycle state."""
        return self.states[-1]


class AsyncSupervisor:
    """Async orchestrator: spawns one agent process per run, streams and settles it."""

    def __init__(self) -> None:
        """Initialize an empty run-id -> AsyncRunHandle registry."""
        self._runs: dict[str, AsyncRunHandle] = {}

    def get(self, run_id: str) -> AsyncRunHandle | None:
        """Look up a tracked run handle by id, or None if unknown to this instance."""
        return self._runs.get(run_id)

    async def spawn(
        self,
        *,
        run_id: str,
        command: Sequence[str],
        root: str | Path = ".",
        env: Mapping[str, str] | None = None,
        meta_path: str | Path | None = None,
        report_path: str | Path | None = None,
        transcript_path: str | Path | None = None,
        prompt_file_path: str | Path | None = None,
    ) -> AsyncRunHandle:
        """Start one agent subprocess and register it as a live AsyncRunHandle.

        Materializes the report template, merges the run's identity env vars,
        seeds durable origin metadata into meta.json, and emits the
        CREATED/PROCESS_SPAWNED lifecycle transitions before returning.
        """
        if not command:
            raise ValueError("command must not be empty")
        cwd = Path(normalize_run_root(root))
        transcript = Path(transcript_path) if transcript_path is not None else None
        if transcript is not None:
            transcript.parent.mkdir(parents=True, exist_ok=True)
        prompt_file = Path(prompt_file_path).expanduser() if prompt_file_path else None

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        session_id = ensure_session_id(merged_env.get("VIBECRAFTED_SESSION_ID"))
        merged_env["VIBECRAFTED_SESSION_ID"] = session_id
        workspace_fields: dict[str, object] = {}
        try:
            from .workspace_catalog import resolve_run_workspace_identity

            workspace_identity = resolve_run_workspace_identity(
                root=cwd,
                env=merged_env,
                create_if_missing=True,
            )
            workspace_fields = workspace_identity.to_meta_fields()
            merged_env.update(workspace_identity.to_env())
            session_id = workspace_identity.vibecrafted_session_id
            merged_env["VIBECRAFTED_SESSION_ID"] = session_id
        except Exception:  # noqa: BLE001, S110 — supervisor launch remains fail-open.
            pass
        merged_env["VIBECRAFTED_RUN_ID"] = run_id
        merged_env["SPAWN_RUN_ID"] = run_id
        if meta_path is not None:
            merged_env["VIBECRAFTED_META_PATH"] = str(meta_path)
        if report_path is not None:
            merged_env["VIBECRAFTED_REPORT_PATH"] = str(report_path)
        if transcript_path is not None:
            merged_env["VIBECRAFTED_TRANSCRIPT_PATH"] = str(transcript_path)
        if prompt_file is not None:
            merged_env["VIBECRAFTED_PROMPT_PATH"] = str(prompt_file)
        agent = str(merged_env.get("VIBECRAFTED_AGENT") or _infer_agent(command))
        initial_agent_session_id = str(
            merged_env.get("VIBECRAFTED_AGENT_SESSION_ID") or ""
        ).strip()
        skill = str(
            merged_env.get("VIBECRAFTED_SKILL_NAME")
            or merged_env.get("VIBECRAFTED_SKILL_CODE")
            or merged_env.get("VIBECRAFTED_SKILL")
            or "unknown"
        )
        claim_digest = str(merged_env.get(CLAIM_DIGEST_ENV) or "").strip()
        agent_model = resolve_default_model(agent, command=command, env=merged_env)
        model_receipt = _model_override_receipt(
            agent, str(merged_env.get("VIBECRAFTED_MODEL_REQUESTED") or "")
        )
        started_at = _utc_now()
        if report_path is not None:
            from .report_contract import materialize_launcher_report_template

            materialize_launcher_report_template(
                report_path,
                run_id=run_id,
                agent=agent,
                skill=skill,
                claim_digest=claim_digest,
            )

        await self._emit(
            run_id,
            RunState.CREATED,
            "async supervisor run created",
            payload={
                "root": str(cwd),
                "command": list(command),
                "meta": str(meta_path or ""),
                "report": str(report_path or ""),
                "transcript": str(transcript_path or ""),
                "prompt_file": str(prompt_file or ""),
                "started_at": started_at.isoformat(),
                "session_id": session_id,
                "identity_required": True,
                "agent": agent,
                **workspace_fields,
                **(
                    {
                        "agent_session_id": initial_agent_session_id,
                        "runtime_session_id": session_id,
                    }
                    if initial_agent_session_id
                    else {}
                ),
                "skill": skill,
                "agent_model": agent_model,
                **({"claim_digest": claim_digest} if claim_digest else {}),
                **model_receipt,
            },
        )

        stdin_handle = None
        try:
            if prompt_file is not None:
                stdin_handle = prompt_file.open("rb")
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                env=merged_env,
                stdin=stdin_handle,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                limit=STDIO_LIMIT_BYTES,
            )
        finally:
            if stdin_handle is not None:
                stdin_handle.close()
        handle = AsyncRunHandle(
            run_id=run_id,
            command=tuple(command),
            root=cwd,
            process=process,
            started_at=started_at,
            meta_path=Path(meta_path) if meta_path is not None else None,
            report_path=Path(report_path) if report_path is not None else None,
            transcript_path=transcript,
            session_id=session_id,
            agent=agent,
            skill=skill,
            agent_session_id=initial_agent_session_id,
            agent_model=agent_model,
            claim_digest=claim_digest,
            model_requested=str(model_receipt.get("model_requested") or ""),
            model_override_supported=bool(
                model_receipt.get("model_override_supported")
            ),
            model_override_skipped=bool(model_receipt.get("model_override_skipped")),
            model_override_skip_reason=str(
                model_receipt.get("model_override_skip_reason") or ""
            ),
            workspace_fields=dict(workspace_fields),
        )
        try:
            handle.pgid = os.getpgid(process.pid)
        except ProcessLookupError:
            handle.pgid = None
        handle.worker_identity = process_identity_receipt(
            process.pid,
            run_id=run_id,
        )
        self._runs[run_id] = handle
        # Seed durable origin identity as soon as the worker exists so triage
        # still works when the finisher has no ambient VC_FRAME pane env.
        if handle.meta_path is not None:
            try:
                origin = _origin_fields_from_env(merged_env)
                handle.meta_path.parent.mkdir(parents=True, exist_ok=True)

                def _seed(latest: dict[str, object]) -> dict[str, object]:
                    if (
                        origin.get("origin_session")
                        and not str(latest.get("origin_session") or "").strip()
                    ):
                        latest["origin_session"] = origin["origin_session"]
                        latest["operator_session"] = origin.get(
                            "operator_session", origin["origin_session"]
                        )
                    if (
                        origin.get("origin_session")
                        and origin.get("origin_tab")
                        and not str(latest.get("origin_tab") or "").strip()
                    ):
                        latest["origin_tab"] = origin["origin_tab"]
                    if (
                        origin.get("origin_pane_id")
                        and not str(latest.get("origin_pane_id") or "").strip()
                    ):
                        latest["origin_pane_id"] = origin["origin_pane_id"]
                    for field in (
                        "resolved_worktree_path",
                        "branch",
                        "baseline_sha",
                        "target_path",
                        "artifact_path",
                        "dependency_set",
                        "scheduler_slot",
                        "integrator_exclusivity",
                        "cut_id",
                    ):
                        if origin.get(field):
                            latest.setdefault(field, origin[field])
                    latest.setdefault("run_id", run_id)
                    latest.setdefault("root", str(cwd))
                    latest.setdefault("agent", agent)
                    latest.setdefault("skill", skill)
                    for field, value in workspace_fields.items():
                        latest.setdefault(field, value)
                    latest["worker_pid"] = handle.process.pid
                    latest["worker_pgid"] = handle.pgid
                    if handle.worker_identity is not None:
                        latest["worker_identity"] = handle.worker_identity
                    if initial_agent_session_id:
                        latest.setdefault("agent_session_id", initial_agent_session_id)
                        latest.setdefault("runtime_session_id", session_id)
                    if claim_digest:
                        latest["claim_digest"] = claim_digest
                    return latest

                mutate_run_meta(
                    control_plane_home(),
                    meta_path=handle.meta_path,
                    mutation_root=handle.meta_path.parent,
                    run_id=run_id,
                    mutator=_seed,
                    create=True,
                )
            except (OSError, RunMetaMutationError, TypeError):
                pass
        await self._transition(
            handle,
            RunState.PROCESS_SPAWNED,
            "process spawned",
            payload={
                "worker_pid": handle.process.pid,
                "worker_pgid": handle.pgid,
                **(
                    {"worker_identity": handle.worker_identity}
                    if handle.worker_identity is not None
                    else {}
                ),
                "meta": str(handle.meta_path or ""),
                "report": str(handle.report_path or ""),
                "transcript": str(handle.transcript_path or ""),
            },
        )
        return handle

    async def run(
        self,
        *,
        run_id: str,
        command: Sequence[str],
        root: str | Path = ".",
        env: Mapping[str, str] | None = None,
        meta_path: str | Path | None = None,
        report_path: str | Path | None = None,
        transcript_path: str | Path | None = None,
        prompt_file_path: str | Path | None = None,
        timeout: float | None = None,
        silence_timeout: float | None = None,
        require_report: bool = True,
        require_transcript_output: bool = False,
        tee_output: bool = False,
        salvage_report_from_stream: bool = False,
    ) -> AsyncRunHandle:
        """Spawn, watch to completion, validate artifacts, and settle a run end to end.

        Honors an accepted operator-stop by skipping completion-report
        manufacturing; otherwise writes a report fallback, meta summary, and
        the terminal lifecycle transition (COMPLETED/FAILED/STALLED).
        """
        handle = await self.spawn(
            run_id=run_id,
            command=command,
            root=root,
            env=env,
            meta_path=meta_path,
            report_path=report_path,
            transcript_path=transcript_path,
            prompt_file_path=prompt_file_path,
        )
        if tee_output:
            _write_terminal(_terminal_frontmatter(handle))
        try:
            if timeout is None:
                await self._watch_process(
                    handle, tee_output=tee_output, silence_timeout=silence_timeout
                )
            else:
                await asyncio.wait_for(
                    self._watch_process(
                        handle, tee_output=tee_output, silence_timeout=silence_timeout
                    ),
                    timeout=timeout,
                )
        except asyncio.TimeoutError as stall:
            await self._terminate(handle)
            handle.exit_code = handle.process.returncode
            handle.completed_at = _utc_now()
            # Both stall kinds settle through this one path; the recorded reason
            # still names which instrument fired, so a triage never has to guess
            # whether the worker ran too long or went mute.
            silent = isinstance(stall, _SilenceTimeout)
            reason = str(stall) if silent else "process timed out"
            await self._transition(
                handle,
                RunState.STALLED,
                f"worker went silent: {reason}" if silent else reason,
                payload={
                    "exit_code": handle.exit_code,
                    "completed_at": handle.completed_at.isoformat(),
                    "liveness": "terminal",
                    "stall_kind": "silence" if silent else "wall_clock",
                },
            )
            return handle

        handle.exit_code = handle.process.returncode
        handle.completed_at = _utc_now()
        operator_stop = await asyncio.to_thread(
            _accepted_operator_stop,
            handle.run_id,
        )
        if operator_stop is not None:
            handle.operator_stopped = True
            handle.operator_stop_reason = str(
                operator_stop.get("stop_reason") or "operator stop request"
            )
            self._write_meta_summary(handle)
            # A deliberately stopped run owes no completion report. Keep an
            # observational artifact receipt for dispatcher callers, but do
            # not manufacture lifecycle:failed/report_missing after the
            # durable stop authority already won.
            handle.artifact_validation = validate_artifacts(
                meta_path=handle.meta_path,
                report_path=handle.report_path,
                transcript_path=handle.transcript_path,
                require_report=False,
                require_transcript_output=False,
            )
            if tee_output:
                _write_terminal(_terminal_footer(handle))
            return handle
        self._write_report_fallback(
            handle,
            salvage_report_from_stream=salvage_report_from_stream,
        )
        self._write_meta_summary(handle)
        if handle.exit_code == 0:
            await self._transition(
                handle,
                RunState.COMPLETED,
                "process completed",
                payload={
                    "exit_code": handle.exit_code,
                    "completed_at": handle.completed_at.isoformat(),
                    "liveness": "terminal",
                },
            )
        else:
            await self._transition(
                handle,
                RunState.FAILED,
                f"process failed with exit code {handle.exit_code}",
                payload={
                    "exit_code": handle.exit_code,
                    "completed_at": handle.completed_at.isoformat(),
                    "liveness": "terminal",
                },
            )

        handle.artifact_validation = validate_artifacts(
            meta_path=handle.meta_path,
            report_path=handle.report_path,
            transcript_path=handle.transcript_path,
            require_report=require_report,
            require_transcript_output=require_transcript_output,
        )
        artifact_payload = {
            "event_kind": EventKind.ARTIFACT.value,
            "meta": str(handle.meta_path or ""),
            "report": str(handle.report_path or ""),
            "transcript": str(handle.transcript_path or ""),
            "agent": handle.agent,
            "agent_session_id": handle.agent_session_id,
            "agent_model": handle.agent_model,
            "tokens_input": handle.tokens_input,
            "tokens_cached_input": handle.tokens_cached_input,
            "tokens_output": handle.tokens_output,
            "tokens_total": _handle_tokens_total(handle),
            "cost_usd": handle.cost_usd,
            "resume_command": handle.resume_command,
            **handle.artifact_validation.as_payload(),
        }
        if handle.tokens_cache_write is not None:
            artifact_payload["tokens_cache_write"] = handle.tokens_cache_write
        await self._emit(
            handle.run_id,
            RunState.ARTIFACT_SEEN,
            "artifacts inspected",
            payload=artifact_payload,
        )
        if handle.artifact_validation.ok:
            await self._transition(
                handle, RunState.REPORT_VALIDATED, "artifact contract validated"
            )
        else:
            failed_state = (
                RunState.REPORT_MISSING
                if "report_missing" in handle.artifact_validation.errors
                else RunState.REPORT_INVALID
            )
            await self._transition(
                handle,
                failed_state,
                "artifact contract failed",
                payload={
                    **handle.artifact_validation.as_payload(),
                    "liveness": "terminal",
                },
            )
        if tee_output:
            _write_terminal(_terminal_footer(handle))
        return handle

    async def _watch_process(
        self,
        handle: AsyncRunHandle,
        *,
        tee_output: bool = False,
        silence_timeout: float | None = None,
    ) -> None:
        """Stream stdout lines to the transcript, parse usage, and emit heartbeats.

        Emits a synthetic heartbeat every ``_HEARTBEAT_INTERVAL_SECONDS`` of
        stdout silence so a long tool call is never mistaken for a dead worker,
        and raises ``_SilenceTimeout`` once that silence outlives
        ``silence_timeout`` so an unbroken quiet worker settles as STALLED
        instead of hanging the supervisor forever.
        """
        assert handle.process.stdout is not None
        silence_bound = (
            _default_silence_timeout() if silence_timeout is None else silence_timeout
        )
        last_output_monotonic = time.monotonic()
        parser = AgentStreamParser(handle.agent, default_model=handle.agent_model)
        human_path = transcript_human_path(handle.transcript_path)
        read_task = asyncio.create_task(handle.process.stdout.readline())
        try:
            while True:
                completed, _pending = await asyncio.wait(
                    {read_task}, timeout=_HEARTBEAT_INTERVAL_SECONDS
                )
                if not completed:
                    # stdout silence is normal while an agent waits inside a
                    # long build/test/install tool call. Drive liveness from
                    # the process clock so the exact quiet phase still pulses.
                    silent_for = time.monotonic() - last_output_monotonic
                    if silence_bound > 0 and silent_for >= silence_bound:
                        raise _SilenceTimeout(
                            f"no worker output for {silent_for:.0f}s"
                            f" (bound {silence_bound:.0f}s)"
                        )
                    if handle.process.returncode is None:
                        handle.heartbeat_monotonic = time.monotonic()
                        await self._emit(
                            handle.run_id,
                            handle.state,
                            "worker heartbeat",
                            payload={
                                "liveness": "pid_alive",
                                "heartbeat_at": _utc_now().isoformat(),
                            },
                        )
                    continue

                chunk = read_task.result()
                if not chunk:
                    break
                last_output_monotonic = time.monotonic()
                read_task = asyncio.create_task(handle.process.stdout.readline())
                if handle.transcript_path is not None:
                    with handle.transcript_path.open("ab") as transcript:
                        transcript.write(chunk)
                display_text = parser.feed_line(chunk)
                if human_path is not None and display_text:
                    with human_path.open("ab") as human:
                        human.write(display_text.encode("utf-8"))
                previous_agent_session_id = handle.agent_session_id
                self._sync_stream_summary(handle, parser)
                if (
                    handle.report_path is not None
                    and handle.agent_session_id
                    and handle.agent_session_id != previous_agent_session_id
                ):
                    from .report_contract import stamp_launcher_report_identity

                    stamp_launcher_report_identity(
                        handle.report_path,
                        run_id=handle.run_id,
                        session_id=handle.agent_session_id,
                        agent=handle.agent,
                        skill=handle.skill,
                        status="pending",
                        model=handle.agent_model,
                        claim_digest=handle.claim_digest,
                    )
                if tee_output and display_text:
                    sys.stdout.buffer.write(display_text.encode("utf-8"))
                    sys.stdout.buffer.flush()
                if not handle.first_output_seen:
                    handle.first_output_seen = True
                    handle.heartbeat_monotonic = time.monotonic()
                    await self._transition(
                        handle,
                        RunState.FIRST_OUTPUT_SEEN,
                        "first output observed",
                        payload={
                            "heartbeat_at": _utc_now().isoformat(),
                        },
                    )
                    await self._transition(
                        handle,
                        RunState.ACTIVE,
                        "process active",
                        payload={
                            "liveness": "pid_alive",
                            "heartbeat_at": _utc_now().isoformat(),
                        },
                    )
                    continue
                # Output movement is also a heartbeat, but use `_emit` rather
                # than `_transition`: a pulse must not mutate state history.
                now_monotonic = time.monotonic()
                if (
                    now_monotonic - handle.heartbeat_monotonic
                    >= _HEARTBEAT_INTERVAL_SECONDS
                ):
                    handle.heartbeat_monotonic = now_monotonic
                    await self._emit(
                        handle.run_id,
                        RunState.ACTIVE,
                        "worker heartbeat",
                        payload={
                            "liveness": "pid_alive",
                            "heartbeat_at": _utc_now().isoformat(),
                        },
                    )
        finally:
            if not read_task.done():
                read_task.cancel()
                try:
                    await read_task
                except asyncio.CancelledError:
                    pass
        await handle.process.wait()
        self._sync_stream_summary(handle, parser)

    def _write_report_fallback(
        self,
        handle: AsyncRunHandle,
        *,
        salvage_report_from_stream: bool = False,
    ) -> None:
        """Ensure the report file exists and carries stamped launcher identity.

        Salvages a report from the transcript when missing (grok, or when
        opted in), and re-renders any launcher-template placeholder report
        left behind by a worker that never overwrote it.
        """
        report = handle.report_path
        if report is None:
            return
        if not report.exists():
            if handle.exit_code != 0 or (
                handle.agent != "grok" and not salvage_report_from_stream
            ):
                return
            transcript_text = ""
            if handle.transcript_path is not None and handle.transcript_path.exists():
                try:
                    transcript_text = handle.transcript_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    transcript_text = ""
            rendered = _render_fallback_report(handle, transcript_text)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(rendered, encoding="utf-8")
            return

        # Reports are worker-authored evidence, but the runtime owns their
        # transport contract. Normalize an existing substantive report before
        # strict validation so a good handoff cannot become report_invalid only
        # because the worker omitted the dashboard frontmatter. Preserve the
        # body and any explicit claim (blocked/partial/failed) verbatim.
        try:
            if report.stat().st_size == 0:
                return
            text = report.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        from .report_contract import (
            parse_report_text,
            stamp_launcher_report_identity,
            worker_authored_report,
        )

        fields, body, has_frontmatter = parse_report_text(text)
        # `launcher_template` is a PRESERVED machine key: the worker contract
        # tells workers to keep seeded frontmatter, so its presence is not
        # evidence that the worker stayed silent. Only substance decides —
        # otherwise a fully authored report gets replaced by a transcript
        # salvage (observed: a complete review report destroyed on resume).
        if (
            handle.exit_code == 0
            and (handle.agent == "grok" or salvage_report_from_stream)
            and has_frontmatter
            and fields.get("launcher_template", "").strip().lower()
            in {"1", "true", "yes", "on"}
            and not worker_authored_report(fields, body)
        ):
            transcript_text = ""
            if handle.transcript_path is not None and handle.transcript_path.exists():
                try:
                    transcript_text = handle.transcript_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    transcript_text = ""
            report.write_text(
                _render_fallback_report(handle, transcript_text),
                encoding="utf-8",
            )

        status = "completed" if handle.exit_code == 0 else "failed"
        stamp_launcher_report_identity(
            report,
            run_id=handle.run_id,
            session_id=handle.agent_session_id,
            agent=handle.agent or "unknown",
            skill=handle.skill or "unknown",
            status=status,
            model=handle.agent_model,
            claim_digest=handle.claim_digest,
        )

    def _sync_stream_summary(
        self, handle: AsyncRunHandle, parser: AgentStreamParser
    ) -> None:
        """Copy the parser's latest session/model/token/cost readings onto the handle."""
        if parser.session_id:
            handle.agent_session_id = parser.session_id
        handle.agent_model = parser.model_id
        handle.tokens_input = parser.tokens_input
        handle.tokens_cached_input = parser.tokens_cached_input
        handle.tokens_cache_write = parser.tokens_cache_write
        handle.tokens_output = parser.tokens_output
        handle.cost_usd = parser.cost_usd
        handle.cost_source = parser.cost_source
        handle.resume_command = parser.resume_command(handle.root)

    def _write_meta_summary(self, handle: AsyncRunHandle) -> None:
        """Merge a full run-state summary (status, tokens, cost, origin) into meta.json."""
        if handle.meta_path is None:
            return
        summary = {
            "run_id": handle.run_id,
            "agent": handle.agent,
            "session_id": handle.agent_session_id,
            "agent_session_id": handle.agent_session_id,
            "agent_model": handle.agent_model,
            "runtime_session_id": handle.session_id,
            "root": str(handle.root),
            "report": str(handle.report_path or ""),
            "transcript": str(handle.transcript_path or ""),
            "tokens_input": handle.tokens_input,
            "tokens_cached_input": handle.tokens_cached_input,
            "tokens_output": handle.tokens_output,
            "tokens_total": _handle_tokens_total(handle),
            "cost_usd": handle.cost_usd if handle.cost_usd is not None else "unknown",
            "cost_source": handle.cost_source or "unknown",
            "resume_command": handle.resume_command,
            "exit_code": handle.exit_code,
            "completed_at": handle.completed_at.isoformat()
            if handle.completed_at
            else "",
            "status": (
                "stopped"
                if handle.operator_stopped
                else (
                    "completed"
                    if handle.exit_code == 0
                    else ("failed" if handle.exit_code is not None else "running")
                )
            ),
            "worker_pid": handle.process.pid,
            "worker_pgid": handle.pgid,
        }
        if handle.worker_identity is not None:
            summary["worker_identity"] = handle.worker_identity
        if handle.operator_stopped:
            summary["operator_stop_accepted"] = True
            summary["stop_reason"] = handle.operator_stop_reason
            summary["recovery_required"] = False
        # Stamp origin for triage-run: dispatcher finishes outside the worker
        # pane, so ambient VC_FRAME_* is often empty at triage time. Prefer
        # values already in meta (launch path) over live env.
        origin = _origin_fields_from_env()
        if handle.model_requested:
            summary["model_requested"] = handle.model_requested
            summary["model_override_supported"] = handle.model_override_supported
            summary["model_override_skipped"] = handle.model_override_skipped
            if handle.model_override_skip_reason:
                summary["model_override_skip_reason"] = (
                    handle.model_override_skip_reason
                )
        if handle.tokens_cache_write is not None:
            summary["tokens_cache_write"] = handle.tokens_cache_write
        handle.meta_path.parent.mkdir(parents=True, exist_ok=True)

        def _merge_summary(payload: dict[str, object]) -> dict[str, object]:
            """Merge the computed summary onto the mutator's current meta payload."""
            if not str(payload.get("origin_session") or "").strip() and origin.get(
                "origin_session"
            ):
                summary["origin_session"] = origin["origin_session"]
                summary["operator_session"] = origin.get(
                    "operator_session", origin["origin_session"]
                )
            if (
                origin.get("origin_session")
                and origin.get("origin_tab")
                and not str(payload.get("origin_tab") or "").strip()
            ):
                summary["origin_tab"] = origin["origin_tab"]
            if (
                origin.get("origin_pane_id")
                and not str(payload.get("origin_pane_id") or "").strip()
            ):
                summary["origin_pane_id"] = origin["origin_pane_id"]
            if handle.tokens_cache_write is None:
                payload.pop("tokens_cache_write", None)
            payload.update(summary)
            return payload

        mutate_run_meta(
            control_plane_home(),
            meta_path=handle.meta_path,
            mutation_root=handle.meta_path.parent,
            run_id=handle.run_id,
            mutator=_merge_summary,
            create=True,
        )

    async def _terminate(self, handle: AsyncRunHandle) -> None:
        """SIGTERM the run's process group (or process), escalating to SIGKILL after 3s."""
        if handle.process.returncode is not None:
            return
        try:
            if handle.pgid is not None:
                os.killpg(handle.pgid, signal.SIGTERM)
            else:
                handle.process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(handle.process.wait(), timeout=3)
        except asyncio.TimeoutError:
            try:
                if handle.pgid is not None:
                    os.killpg(handle.pgid, signal.SIGKILL)
                else:
                    handle.process.kill()
            except ProcessLookupError:
                pass
            await handle.process.wait()

    async def _transition(
        self,
        handle: AsyncRunHandle,
        state: RunState,
        message: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> None:
        """Append *state* to the handle's history and emit the corresponding event."""
        handle.states.append(state)
        event_payload: dict[str, object] = {
            "root": str(handle.root),
            "session_id": handle.session_id,
            "identity_required": True,
        }
        if payload:
            event_payload.update(payload)
        await self._emit(handle.run_id, state, message, payload=event_payload)

    async def _emit(
        self,
        run_id: str,
        state: RunState,
        message: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> None:
        """Persist one lifecycle event to the durable event stream (off-thread)."""
        event_payload: dict[str, object] = {
            "event_kind": EventKind.LIFECYCLE.value,
            "state": state.value,
        }
        handle = self._runs.get(run_id)
        if handle is not None:
            event_payload.update(handle.workspace_fields)
        if payload:
            event_payload.update(payload)
        await asyncio.to_thread(
            append_event,
            kind=f"{EventKind.LIFECYCLE.value}:{state.value}",
            run_id=run_id,
            message=message,
            payload=event_payload,
        )
