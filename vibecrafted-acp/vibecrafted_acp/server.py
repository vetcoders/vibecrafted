"""ACP v1 stdio JSON-RPC server: session lifecycle, prompt dispatch, hard-stop gating."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from vibecrafted_core.agent_stream import ANSI_PATTERN, AgentStreamParser
from vibecrafted_core.workflows.registry import (
    SHIP_STAGES,
    WORKFLOW_DEFINITIONS,
    WORKFLOW_MANIFESTS,
)

from . import __version__
from .bridge import RuntimeBridge
from .policy import allowed_once, classify_hard_stop, permission_request

PROTOCOL_VERSION = 1
SCHEMA_VERSION = "schema-v1.20.0"
_TRUTHY = {"1", "true", "yes", "on"}
_LOGGER = logging.getLogger(__name__)


class JsonWriter:
    """Thread-safe newline-delimited JSON writer for the ACP stdout stream."""

    def __init__(self, stream: TextIO) -> None:
        """Wrap ``stream`` for line-delimited JSON writes."""
        self.stream = stream
        self._lock = threading.Lock()

    def send(self, payload: dict[str, Any]) -> None:
        """Serialize ``payload`` as one compact JSON line and flush it to the stream."""
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.stream.write(line + "\n")
            self.stream.flush()


class ClientRequests:
    """Route agent→client JSON-RPC requests while stdin stays responsive."""

    def __init__(self, writer: JsonWriter) -> None:
        """Set up request bookkeeping over ``writer``."""
        self.writer = writer
        self._condition = threading.Condition()
        self._counter = 0
        self._responses: dict[str, dict[str, Any]] = {}
        self._closed = False

    def resolve(self, message: dict[str, Any]) -> None:
        """Deliver a client JSON-RPC response to whichever ``request()`` call is waiting."""
        request_id = str(message.get("id"))
        with self._condition:
            self._responses[request_id] = message
            self._condition.notify_all()

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
        cancel_event: threading.Event | None = None,
    ) -> Any:
        """Send a JSON-RPC request to the client and block for its result.

        Returns None on timeout, cancellation, closed connection, or an
        error response — callers treat all of those as "not granted".
        """
        with self._condition:
            self._counter += 1
            request_id = f"vibecrafted-permission-{self._counter}"
        self.writer.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        with self._condition:
            while (
                request_id not in self._responses
                and not self._closed
                and not (cancel_event is not None and cancel_event.is_set())
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=min(remaining, 0.1))
            message = self._responses.pop(request_id, None)
        if not isinstance(message, dict) or "error" in message:
            return None
        return message.get("result")

    def close(self) -> None:
        """Mark the client channel closed and wake any pending ``request()`` waiters."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def wake(self) -> None:
        """Wake pending ``request()`` waiters (e.g. after a cancel_event was set)."""
        with self._condition:
            self._condition.notify_all()


@dataclass
class Session:
    """Mutable per-connection ACP session state: agent, run tracking, stream buffers."""

    session_id: str
    cwd: str
    agent: str
    skill: str
    runtime: str
    parser: AgentStreamParser
    prompt_started: bool = False
    launched: bool = False
    cancelled: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    transcript_offset: int = 0
    pending_stream: bytes = b""
    prompt_count: int = 0
    restored: bool = False
    child_run_ids: list[str] = field(default_factory=list)
    stage: str = ""
    active_run_id: str = ""
    dry_stages: int = 2
    plan_statuses: dict[str, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


class ACPServer:
    """Dispatches ACP JSON-RPC methods to session/RuntimeBridge operations."""

    def __init__(self, *, bridge: RuntimeBridge, output: TextIO) -> None:
        """Bind the server to ``bridge`` for runtime calls and ``output`` for responses."""
        self.bridge = bridge
        self.writer = JsonWriter(output)
        self.client = ClientRequests(self.writer)
        self.initialized = False
        self.sessions: dict[str, Session] = {}
        self._threads: list[threading.Thread] = []

    def _response(self, request_id: Any, result: dict[str, Any]) -> None:
        """Send a successful JSON-RPC result for ``request_id``."""
        self.writer.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: Any, code: int, message: str) -> None:
        """Send a JSON-RPC error response for ``request_id``."""
        self.writer.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    def _update(self, session_id: str, update: dict[str, Any]) -> None:
        """Send a ``session/update`` notification, tagging it with session vibecrafted meta."""
        payload = dict(update)
        session = self.sessions.get(session_id)
        if session is not None:
            payload.setdefault("_meta", self._session_meta(session))
        self.writer.send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": session_id, "update": payload},
            }
        )

    def handle_message(self, message: dict[str, Any]) -> None:
        """Route one incoming JSON-RPC line: client response, request, or malformed input."""
        if "method" not in message and "id" in message:
            self.client.resolve(message)
            return
        request_id = message.get("id")
        if message.get("jsonrpc") != "2.0" or not isinstance(
            message.get("method"), str
        ):
            self._error(request_id, -32600, "Invalid Request")
            return
        method = str(message["method"])
        params = message.get("params") or {}
        if not isinstance(params, dict):
            self._error(request_id, -32602, "params must be an object")
            return
        if method == "initialize":
            self._initialize(request_id)
        elif method == "session/new":
            self._new_session(request_id, params)
        elif method == "session/load":
            self._restore_session(request_id, params, replay=True)
        elif method == "session/resume":
            self._restore_session(request_id, params, replay=False)
        elif method == "session/prompt":
            self._start_prompt(request_id, params)
        elif method == "session/cancel":
            self._cancel(request_id, params)
        else:
            self._error(request_id, -32601, f"Method not found: {method}")

    def _initialize(self, request_id: Any) -> None:
        """Handle ``initialize``: mark ready and advertise protocol/agent capabilities."""
        self.initialized = True
        self._response(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "agentCapabilities": {
                    "loadSession": self.bridge.supports_resume,
                    "promptCapabilities": {
                        "image": False,
                        "audio": False,
                        "embeddedContext": False,
                    },
                    "sessionCapabilities": (
                        {"resume": {}} if self.bridge.supports_resume else {}
                    ),
                },
                "agentInfo": {
                    "name": "vibecrafted-acp",
                    "title": "Vibecrafted",
                    "version": __version__,
                },
                "authMethods": [],
                "_meta": {"vibecrafted": {"schema": SCHEMA_VERSION}},
            },
        )

    def _new_session(self, request_id: Any, params: dict[str, Any]) -> None:
        """Handle ``session/new``: validate params, create a Session, emit its command catalog."""
        if not self.initialized:
            self._error(request_id, -32002, "initialize must be called first")
            return
        cwd = str(params.get("cwd") or "").strip()
        if not cwd or not Path(cwd).is_absolute():
            self._error(request_id, -32602, "cwd must be an absolute path")
            return
        mcp_servers = params.get("mcpServers")
        if not isinstance(mcp_servers, list):
            self._error(request_id, -32602, "mcpServers must be an array")
            return
        if mcp_servers:
            self._error(
                request_id,
                -32602,
                "MCP passthrough is not advertised by this MVP",
            )
            return
        meta = params.get("_meta") or {}
        settings = meta.get("vibecrafted") if isinstance(meta, dict) else {}
        settings = settings if isinstance(settings, dict) else {}
        agent = str(settings.get("agent") or "codex").strip()
        skill = str(settings.get("skill") or "implement").strip()
        runtime = str(settings.get("runtime") or "headless").strip()
        try:
            dry_stages = int(settings.get("dryStages") or 2)
        except (TypeError, ValueError):
            self._error(
                request_id, -32602, "_meta.vibecrafted.dryStages must be an integer"
            )
            return
        if dry_stages < 1:
            self._error(
                request_id, -32602, "_meta.vibecrafted.dryStages must be positive"
            )
            return
        session_id = self.bridge.reserve_run_id(skill)
        session = Session(
            session_id=session_id,
            cwd=cwd,
            agent=agent,
            skill=skill,
            runtime=runtime,
            parser=AgentStreamParser(agent),
            dry_stages=dry_stages,
        )
        self.sessions[session_id] = session
        self._response(
            request_id,
            {"sessionId": session_id, "_meta": self._session_meta(session)},
        )
        self._emit_catalog(session)

    @staticmethod
    def _session_meta(session: Session) -> dict[str, Any]:
        """Build the ``_meta.vibecrafted`` envelope (parent/child run ids, stage) for a session."""
        return {
            "vibecrafted": {
                "parent_run_id": session.session_id,
                "child_run_ids": list(session.child_run_ids),
                "stage": session.stage,
            }
        }

    @staticmethod
    def _available_commands() -> list[dict[str, Any]]:
        """Build the slash-command catalog: ``/ship`` plus one per registered workflow."""
        ship = WORKFLOW_MANIFESTS["vc-ship"]
        commands: list[dict[str, Any]] = [
            {
                "name": "ship",
                "description": ship.description,
                "input": {"hint": "mission text passed unchanged"},
                "_meta": {"vibecrafted": {"argv": ["vibecrafted", "ship"]}},
            }
        ]
        definitions = sorted(
            WORKFLOW_DEFINITIONS.values(),
            key=lambda definition: (definition.lifecycle_order, definition.id),
        )
        for definition in definitions:
            manifest = WORKFLOW_MANIFESTS.get(f"vc-{definition.id}")
            description = (
                manifest.description
                if manifest is not None
                else f"Run the canonical vc-{definition.id} workflow."
            )
            commands.append(
                {
                    "name": definition.id,
                    "description": description,
                    "input": {"hint": "prompt text passed unchanged"},
                    "_meta": {
                        "vibecrafted": {
                            "argv": ["vibecrafted", definition.id],
                        }
                    },
                }
            )
        return commands

    def _emit_catalog(self, session: Session) -> None:
        """Send the available_commands_update for ``session``."""
        self._update(
            session.session_id,
            {
                "sessionUpdate": "available_commands_update",
                "availableCommands": self._available_commands(),
            },
        )

    def _emit_plan(self, session: Session) -> None:
        """Send a ``plan`` update listing every vc-ship stage and its current status."""
        entries: list[dict[str, str]] = []
        for stage in SHIP_STAGES:
            status = session.plan_statuses.get(stage.id, "pending")
            entries.append(
                {
                    "content": stage.name,
                    "priority": "high" if status == "in_progress" else "medium",
                    "status": status,
                }
            )
        self._update(
            session.session_id,
            {"sessionUpdate": "plan", "entries": entries},
        )

    def _restore_session(
        self,
        request_id: Any,
        params: dict[str, Any],
        *,
        replay: bool,
    ) -> None:
        """Handle ``session/load`` and ``session/resume``: restore a Session from artifacts.

        ``replay=True`` (load) also replays the saved transcript as a chunk;
        ``replay=False`` (resume) restores state without replaying output.
        """
        if not self.initialized:
            self._error(request_id, -32002, "initialize must be called first")
            return
        session_id = str(params.get("sessionId") or "").strip()
        cwd = str(params.get("cwd") or "").strip()
        if not session_id:
            self._error(request_id, -32602, "sessionId is required")
            return
        if not cwd or not Path(cwd).is_absolute():
            self._error(request_id, -32602, "cwd must be an absolute path")
            return
        mcp_servers = params.get("mcpServers", [])
        if replay and "mcpServers" not in params:
            self._error(request_id, -32602, "mcpServers must be an array")
            return
        if not isinstance(mcp_servers, list):
            self._error(request_id, -32602, "mcpServers must be an array")
            return
        if mcp_servers:
            self._error(request_id, -32602, "MCP passthrough is not supported")
            return
        try:
            restored = self.bridge.load_session(session_id)
        except (OSError, RuntimeError, ValueError) as exc:
            self._error(request_id, -32004, str(exc))
            return
        artifact_cwd = str(restored.get("cwd") or "").strip()
        if artifact_cwd and Path(artifact_cwd).resolve() != Path(cwd).resolve():
            self._error(
                request_id,
                -32004,
                "session resume unavailable: cwd differs from artifact root",
            )
            return
        agent = str(restored.get("agent") or "codex")
        session = Session(
            session_id=session_id,
            cwd=artifact_cwd or cwd,
            agent=agent,
            skill=str(restored.get("skill") or "implement"),
            runtime=str(restored.get("runtime") or "headless"),
            parser=AgentStreamParser(agent),
            restored=True,
            child_run_ids=[
                str(run_id) for run_id in restored.get("child_run_ids") or []
            ],
            stage=str(restored.get("stage") or ""),
        )
        for stage in restored.get("plan_stages") or []:
            if not isinstance(stage, dict):
                continue
            stage_id = str(stage.get("id") or "")
            if stage_id:
                session.plan_statuses[stage_id] = (
                    "completed"
                    if str(stage.get("status") or "") == "completed"
                    else "in_progress"
                )
        self.sessions[session_id] = session
        self._emit_catalog(session)
        if session.plan_statuses:
            self._emit_plan(session)
        if replay:
            transcript = str(restored.get("transcript") or "")
            if transcript:
                self._emit_stream(
                    session,
                    transcript.encode("utf-8", errors="replace"),
                    final=True,
                )
        self._response(
            request_id,
            {
                "_meta": {
                    **self._session_meta(session),
                    "vibecrafted": {
                        **self._session_meta(session)["vibecrafted"],
                        "restored_from": ["report", "transcript"],
                        "transcript_truncated": bool(
                            restored.get("transcript_truncated")
                        ),
                    },
                }
            },
        )

    @staticmethod
    def _prompt_text(params: dict[str, Any]) -> str:
        """Flatten an ACP prompt content-block list into plain text."""
        prompt = params.get("prompt")
        if not isinstance(prompt, list):
            return ""
        parts: list[str] = []
        for block in prompt:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif block.get("type") == "resource":
                resource = block.get("resource")
                if isinstance(resource, dict):
                    parts.append(str(resource.get("text") or ""))
            elif block.get("type") == "resource_link":
                parts.append(str(block.get("uri") or ""))
        return "\n".join(part for part in parts if part).strip()

    def _start_prompt(self, request_id: Any, params: dict[str, Any]) -> None:
        """Handle ``session/prompt``: validate, then run the prompt worker on a background thread."""
        session_id = str(params.get("sessionId") or "")
        session = self.sessions.get(session_id)
        if session is None:
            self._error(request_id, -32602, "unknown sessionId")
            return
        prompt = self._prompt_text(params)
        if not prompt:
            self._error(request_id, -32602, "prompt must contain text")
            return
        with session.lock:
            if session.prompt_started:
                self._error(request_id, -32001, "a prompt is already running")
                return
            session.prompt_started = True
        thread = threading.Thread(
            target=self._prompt_worker,
            args=(request_id, session, prompt),
            name=f"acp-{session_id}",
        )
        self._threads.append(thread)
        thread.start()

    @classmethod
    def _command_prompt(cls, session: Session, prompt: str) -> tuple[str, str, bool]:
        """Parse a ``/command remainder`` prompt into ``(skill, remainder, was_command)``.

        Returns ``("", remainder, True)`` for an unrecognized slash command so
        the caller can report it without guessing a skill.
        """
        if not prompt.startswith("/"):
            return session.skill, prompt, False
        command, separator, remainder = prompt[1:].partition(" ")
        names = {item["name"] for item in cls._available_commands()}
        if command not in names:
            return "", remainder if separator else "", True
        return command, remainder if separator else "", True

    def _prompt_worker(self, request_id: Any, session: Session, prompt: str) -> None:
        """Background-thread body of session/prompt: hard-stop gate, dispatch, respond."""
        permission_tool_id = ""
        try:
            skill, runtime_prompt, was_command = self._command_prompt(session, prompt)
            if was_command and not skill:
                self._update(
                    session.session_id,
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {
                            "type": "text",
                            "text": "Unknown slash command; use the advertised catalog.",
                        },
                    },
                )
                self._response(
                    request_id,
                    {
                        "stopReason": "refusal",
                        "_meta": self._session_meta(session),
                    },
                )
                return

            hard_stop = classify_hard_stop(prompt)
            if hard_stop is not None:
                permission_tool_id = f"hard-stop-{session.session_id}"
                self._update(
                    session.session_id,
                    {
                        "sessionUpdate": "tool_call",
                        "toolCallId": permission_tool_id,
                        "title": f"Operator button required: {hard_stop.category}",
                        "kind": "execute",
                        "status": "pending",
                        "rawInput": {"prompt": prompt},
                    },
                )
                timeout = float(
                    os.environ.get("VIBECRAFTED_ACP_PERMISSION_TIMEOUT", "30")
                )
                decision = self.client.request(
                    "session/request_permission",
                    permission_request(
                        session_id=session.session_id,
                        tool_call_id=permission_tool_id,
                        hard_stop=hard_stop,
                        raw_input=prompt,
                    ),
                    timeout_seconds=timeout,
                    cancel_event=session.cancel_event,
                )
                if not allowed_once(decision):
                    cancelled = session.cancel_event.is_set()
                    self._update(
                        session.session_id,
                        {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": permission_tool_id,
                            "status": "failed",
                            "content": [
                                {
                                    "type": "content",
                                    "content": {
                                        "type": "text",
                                        "text": (
                                            "Cancelled by client."
                                            if cancelled
                                            else (
                                                "Denied: explicit allow_once "
                                                "was not selected."
                                            )
                                        ),
                                    },
                                }
                            ],
                        },
                    )
                    self._response(
                        request_id,
                        {
                            "stopReason": ("cancelled" if cancelled else "refusal"),
                            "_meta": self._session_meta(session),
                        },
                    )
                    return
                self.bridge.record_hard_stop_override(
                    session.session_id,
                    category=hard_stop.category,
                    evidence=hard_stop.evidence,
                )
                self._update(
                    session.session_id,
                    {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": permission_tool_id,
                        "status": "in_progress",
                    },
                )

            with session.lock:
                if session.cancelled:
                    self._response(
                        request_id,
                        {
                            "stopReason": "cancelled",
                            "_meta": self._session_meta(session),
                        },
                    )
                    return

            if skill == "ship":
                stop_reason = self._run_lifecycle_prompt(session, runtime_prompt)
            else:
                stop_reason = self._run_direct_prompt(
                    session,
                    runtime_prompt,
                    skill=skill,
                )
            if permission_tool_id:
                self._update(
                    session.session_id,
                    {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": permission_tool_id,
                        "status": "completed"
                        if stop_reason == "end_turn"
                        else "failed",
                    },
                )
            self._response(
                request_id,
                {
                    "stopReason": stop_reason,
                    "_meta": self._session_meta(session),
                },
            )
        except Exception as exc:
            _LOGGER.exception("ACP prompt failed")
            self._update(
                session.session_id,
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": (
                            f"Vibecrafted runtime error: {type(exc).__name__}: {exc}"
                        ),
                    },
                },
            )
            self._response(
                request_id,
                {
                    "stopReason": ("cancelled" if session.cancelled else "refusal"),
                    "_meta": self._session_meta(session),
                },
            )
        finally:
            with session.lock:
                session.prompt_started = False
                session.prompt_count += 1

    def _run_direct_prompt(
        self,
        session: Session,
        prompt: str,
        *,
        skill: str,
    ) -> str:
        """Launch ``skill`` as a single run, pump its stream, and await completion.

        Returns the ACP stop reason: ``end_turn``, ``refusal``, or ``cancelled``.
        """
        continuation = session.restored or session.prompt_count > 0
        run_id = (
            self.bridge.reserve_run_id(skill) if continuation else session.session_id
        )
        if continuation:
            session.child_run_ids.append(run_id)
        session.skill = skill
        session.stage = skill
        session.active_run_id = run_id
        session.transcript_offset = 0
        session.pending_stream = b""
        launch = self.bridge.launch(
            run_id=run_id,
            root=session.cwd,
            prompt=prompt,
            agent=session.agent,
            skill=skill,
            runtime=session.runtime,
        )
        if not launch.get("accepted") or launch.get("run_id") != run_id:
            raise RuntimeError("core launch did not accept the reserved run_id")
        with session.lock:
            session.launched = True
            cancelled = session.cancelled
        if cancelled:
            self.bridge.stop(run_id)

        result = self.bridge.await_run(
            run_id,
            on_poll=lambda _run: self._pump_once(session, run_id=run_id),
            timeout_seconds=float(os.environ.get("VIBECRAFTED_ACP_TIMEOUT", "300")),
            interval_seconds=0.25,
        )
        self._pump_once(session, run_id=run_id, final=True)
        run = result.get("run") or {}
        stopped = session.cancelled or str(run.get("state") or "") == "stopped"
        if stopped:
            return "cancelled"
        return "end_turn" if result.get("completed") else "refusal"

    def _run_lifecycle_prompt(self, session: Session, prompt: str) -> str:
        """Run the full vc-ship lifecycle for ``prompt``, streaming per-stage plan updates."""
        session.skill = "ship"
        session.transcript_offset = 0
        session.pending_stream = b""
        session.plan_statuses = {}

        def stage_update(
            stage: str,
            status: str,
            child_run_ids: list[str],
            active_child: str,
        ) -> None:
            session.stage = stage
            session.child_run_ids = list(child_run_ids)
            if active_child:
                session.active_run_id = active_child
                session.launched = True
            session.plan_statuses[stage] = status
            self._emit_plan(session)

        state = self.bridge.run_lifecycle(
            parent_run_id=session.session_id,
            root=session.cwd,
            prompt=prompt,
            agent=session.agent,
            runtime=session.runtime,
            dry_stages=session.dry_stages,
            on_stage=stage_update,
            on_chunk=lambda _run_id, data: self._emit_stream(session, data),
            cancelled=session.cancel_event.is_set,
        )
        self._emit_stream(session, b"", final=True)
        if session.cancelled or str(state.get("status") or "") == "stopped":
            return "cancelled"
        return "end_turn" if state.get("status") == "completed" else "refusal"

    def _pump_once(
        self,
        session: Session,
        *,
        run_id: str = "",
        final: bool = False,
    ) -> None:
        """Fetch new transcript bytes for the active run and forward them to ``_emit_stream``."""
        observed = self.bridge.observe(
            run_id or session.active_run_id or session.session_id,
            offset=session.transcript_offset,
        )
        session.transcript_offset = int(
            observed.get("next_offset") or session.transcript_offset
        )
        self._emit_stream(
            session,
            bytes(observed.get("text") or b""),
            final=final,
        )

    def _emit_stream(
        self,
        session: Session,
        data: bytes,
        *,
        final: bool = False,
    ) -> None:
        """Line-buffer raw transcript bytes, parse each complete line, emit rendered chunks.

        Incomplete trailing lines are held in ``session.pending_stream`` until
        more data arrives or ``final=True`` flushes them anyway.
        """
        data = session.pending_stream + data
        lines = data.splitlines(keepends=True)
        session.pending_stream = b""
        if lines and not lines[-1].endswith((b"\n", b"\r")) and not final:
            session.pending_stream = lines.pop()
        if final and session.pending_stream:
            lines.append(session.pending_stream)
            session.pending_stream = b""
        for line in lines:
            rendered = session.parser.feed_line(line)
            if rendered:
                rendered = ANSI_PATTERN.sub("", rendered)
                self._update(
                    session.session_id,
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": rendered},
                    },
                )

    def _cancel(self, request_id: Any, params: dict[str, Any]) -> None:
        """Handle ``session/cancel``: mark the session cancelled and stop its active run."""
        session_id = str(params.get("sessionId") or "")
        session = self.sessions.get(session_id)
        if session is None:
            if request_id is not None:
                self._error(request_id, -32602, "unknown sessionId")
            return
        with session.lock:
            session.cancelled = True
            session.cancel_event.set()
            launched = session.launched
            active_run_id = session.active_run_id or session.session_id
        self.client.wake()
        if launched:
            self.bridge.stop(active_run_id)
        if request_id is not None:
            self._response(request_id, {})

    def wait(self) -> None:
        """Block until every spawned prompt-worker thread has finished."""
        for thread in list(self._threads):
            thread.join()

    def close(self) -> None:
        """Close the client request channel."""
        self.client.close()


def serve(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    bridge: RuntimeBridge,
) -> int:
    """Run the ACP server loop: read JSON-RPC lines from ``input_stream`` until EOF."""
    server = ACPServer(bridge=bridge, output=output_stream)
    for raw_line in input_stream:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            server._error(None, -32700, "Parse error")
            continue
        if not isinstance(message, dict):
            server._error(None, -32600, "Invalid Request")
            continue
        server.handle_message(message)
    server.close()
    server.wait()
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args and run the ACP stdio server over stdin/stdout."""
    parser = argparse.ArgumentParser(
        prog="vibecrafted acp",
        description=(
            f"ACP v1 stdio adapter ({SCHEMA_VERSION}) over Vibecrafted control_plane"
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="use the deterministic fake worker stream without launching an agent",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    dry_run = (
        args.dry_run or os.environ.get("VIBECRAFTED_ACP_DRY_RUN", "").lower() in _TRUTHY
    )
    return serve(sys.stdin, sys.stdout, bridge=RuntimeBridge(dry_run=dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
