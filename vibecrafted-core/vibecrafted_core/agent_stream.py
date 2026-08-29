"""Parse agent streaming-json output (Claude/Codex/Gemini/Junie/Grok) into pane text."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import tomllib

from .telemetry import estimate_cost_usd

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SESSION_PATTERN = re.compile(
    r"(?:^|\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]\s+)session:\s*([A-Za-z0-9][A-Za-z0-9._:-]*)",
    re.MULTILINE,
)
TOKEN_PATTERN = re.compile(
    r"tokens:\s*([0-9]+)\s+in(?:\s*\(([0-9]+)\s+cached\))?\s*/\s*([0-9]+)\s+out",
    re.IGNORECASE,
)
MODEL_PATTERN = re.compile(r"model:\s*([^\s]+)", re.IGNORECASE)
MODEL_ENV_VARS = (
    "VIBECRAFTED_PARENT_MODEL",
    "CLAUDE_MODEL",
    "CODEX_MODEL",
    "GEMINI_MODEL",
    "GROK_MODEL",
    "JUNIE_MODEL",
    "AGY_MODEL",
)
MODEL_PLACEHOLDERS = {"", "none", "null", "unknown", "pending"}
COST_PATTERNS = (
    re.compile(r"cost(?:_usd)?\s*[:=]\s*\$?([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
    re.compile(r"\$([0-9]+\.[0-9]+)\s*(?:usd)?", re.IGNORECASE),
)
GROK_IGNORABLE_TRANSPORT_ERROR = "worker quit with fatal: Transport channel closed"


def stamp() -> str:
    """Return the current local time as ``HH:MM:SS``."""
    return time.strftime("%H:%M:%S", time.localtime())


def tool_tag(name: str) -> str:
    """Render a cyan ``[HH:MM:SS name]`` tag used to mark tool-call lines in the pane."""
    return f"\x1b[36m[{stamp()} {name}]\x1b[0m "


def _stringish(value: Any) -> str:
    """Best-effort coercion of an event field to display text.

    Unwraps common envelope dicts (message/error/detail/text/content) and
    returns "" for a recognized-but-empty envelope, treating it as noise
    (e.g. grok's ``{"type":"text","text":""}`` heartbeats).
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        # message/error/detail are the classic envelopes; text/content cover
        # grok tool_call blocks ({"type":"content","content":{"type":"text",
        # "text":...}}) which otherwise degrade to json.dumps noise.
        keys = ("message", "error", "detail", "text", "content")
        inner = next((value[k] for k in keys if value.get(k)), None)
        if inner is not None and inner is not value:
            return _stringish(inner)
        if any(k in value for k in keys):
            # A recognized envelope whose payload is empty is noise, not data
            # (grok emits {"type":"text","text":""} heartbeats mid-stream).
            return ""
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return ", ".join(_stringish(item) for item in value)
    return json.dumps(value, ensure_ascii=False)


def _truncate_block(text: str, *, max_chars: int = 4000) -> str:
    """Dim-render ``text``, truncating long command/tool output for the pane."""
    lines = text.splitlines()
    if len(text) > max_chars:
        preview = text[:max_chars]
        return f"\x1b[2m{preview}\n  ... ({len(text)} chars)\x1b[0m\n"
    if len(lines) > 12:
        preview = "\n".join(lines[:5])
        return f"\x1b[2m{preview}\n  ... ({len(lines)} lines)\x1b[0m\n"
    return f"\x1b[2m{text}\x1b[0m\n"


def is_grok_ignorable_transport_error(text: str) -> bool:
    """Return True for grok transport-closed noise caused by an auth/DNS/TCP failure.

    These lines are expected retry chatter, not a real turn failure worth
    surfacing to the pane.
    """
    clean = ANSI_PATTERN.sub("", text or "")
    if GROK_IGNORABLE_TRANSPORT_ERROR not in clean:
        return False
    return any(
        marker in clean
        for marker in (
            "AuthorizationRequired",
            "AuthRequired",
            "tcp connect error",
            "dns error",
        )
    )


def _as_int(value: Any) -> int:
    """Coerce ``value`` to int, defaulting to 0 for None/unparseable input."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    """Coerce ``value`` to a float rounded to 6dp, or None for empty/unparseable input."""
    if value in (None, ""):
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _clean_model(value: object) -> str:
    """Stringify ``value`` and blank out known placeholder tokens (e.g. "none", "unknown")."""
    raw = str(value or "").strip()
    return "" if raw.lower() in MODEL_PLACEHOLDERS else raw


def _command_model(command: Sequence[str] | None) -> str:
    """Extract a ``--model``/``-m``/``--model=`` value from a launch command argv."""
    if not command:
        return ""
    items = [str(item) for item in command]
    for index, item in enumerate(items):
        if item in {"--model", "-m"} and index + 1 < len(items):
            model = _clean_model(items[index + 1])
            if model:
                return model
        if item.startswith("--model="):
            model = _clean_model(item.split("=", 1)[1])
            if model:
                return model
    return ""


def _codex_config_model(env: Mapping[str, str]) -> str:
    """Read the default model from ``$CODEX_HOME/config.toml``; "" if unset/unreadable."""
    codex_home = Path(env.get("CODEX_HOME") or Path.home() / ".codex")
    config = codex_home / "config.toml"
    try:
        with config.open("rb") as handle:
            loaded = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    if isinstance(loaded, dict):
        return _clean_model(loaded.get("model"))
    return ""


def resolve_default_model(
    agent: str,
    *,
    command: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve a default model id: launch command flag, then env vars, then codex config."""
    model = _command_model(command)
    if model:
        return model
    source_env = env or os.environ
    for key in MODEL_ENV_VARS:
        model = _clean_model(source_env.get(key))
        if model:
            return model
    if agent == "codex":
        return _codex_config_model(source_env)
    return ""


class AgentStreamParser:
    """Stateful per-agent parser turning streaming-json lines into human-readable text.

    Accumulates session id, model id, token counts, and cost as a side
    effect of ``feed_line`` so callers can read telemetry after the stream.
    """

    def __init__(self, agent: str, *, default_model: str = "") -> None:
        """Initialize parser state for ``agent`` (claude/codex/gemini/junie/grok/agy)."""
        self.agent = agent
        self.session_id = ""
        self._rendered_session_ids: set[str] = set()
        self.model_id = _clean_model(default_model)
        self.tokens_input = 0
        self.tokens_cached_input = 0
        self.tokens_cache_write: int | None = None
        self.tokens_output = 0
        self.cost_usd: float | None = None
        self.cost_source: str | None = None

    def feed_line(self, chunk: bytes) -> str:
        """Decode one line of agent output and render it to human-readable text.

        Non-JSON lines fall through to plain-text scanning for session/token/
        model/cost markers; JSON lines dispatch to the agent-specific formatter.
        """
        text = chunk.decode("utf-8", errors="replace")
        if self.agent == "grok" and is_grok_ignorable_transport_error(text):
            return ""
        stripped = text.lstrip()
        if not stripped.startswith("{"):
            self._scan_text(text)
            return text
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            self._scan_text(text)
            return text
        if not isinstance(event, dict):
            return ""
        return self._format_json_event(event)

    def resume_command(self, root: str | Path) -> str:
        """Build the shell command an operator would run to resume this agent's session."""
        session = self.session_id or "<session_id>"
        root_text = str(root)
        if self.agent == "claude":
            return f"cd {root_text} && claude --resume {session}"
        if self.agent == "codex":
            return f"cd {root_text} && codex resume {session}"
        if self.agent == "gemini":
            return f"cd {root_text} && gemini --resume {session}"
        if self.agent == "agy":
            return f"cd {root_text} && agy --conversation {session}"
        if self.agent == "junie":
            return f"cd {root_text} && junie --resume --session-id {session}"
        if self.agent == "grok":
            return f"cd {root_text} && grok --resume {session}"
        if self.agent == "cursor":
            return f"cd {root_text} && cursor-agent --resume {session}"
        return f"cd {root_text} && vc-resume --session {session}"

    def _scan_text(self, text: str) -> None:
        """Regex-scan a plain-text (non-JSON) line for session id, tokens, model, cost."""
        clean = ANSI_PATTERN.sub("", text or "")
        session_matches = SESSION_PATTERN.findall(clean)
        if session_matches:
            self.session_id = session_matches[-1]
        for raw_in, raw_cached, raw_out in TOKEN_PATTERN.findall(clean):
            self.tokens_input += int(raw_in)
            self.tokens_cached_input += int(raw_cached or 0)
            self.tokens_output += int(raw_out)
        model_matches = MODEL_PATTERN.findall(clean)
        if model_matches:
            self.model_id = model_matches[-1]
        for pattern in COST_PATTERNS:
            matches = pattern.findall(clean)
            if matches:
                self.cost_usd = _as_float(matches[-1])

    def _record_model(self, event: dict[str, Any]) -> None:
        """Capture the first model id found in ``event`` (recursing into nested dicts).

        No-op once ``self.model_id`` is already set — first sighting wins.
        """
        if self.model_id:
            return
        for key in ("model", "model_id", "modelId", "model_name", "modelName"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                self.model_id = value.strip()
                return
        message = event.get("message")
        if isinstance(message, dict):
            for key in ("model", "model_id", "modelId", "model_name", "modelName"):
                value = message.get(key)
                if isinstance(value, str) and value.strip():
                    self.model_id = value.strip()
                    return
        model_usage = event.get("modelUsage") or event.get("model_usage")
        if isinstance(model_usage, dict) and model_usage:
            self.model_id = str(next(iter(model_usage)))
            return
        if isinstance(model_usage, list):
            for item in model_usage:
                if isinstance(item, dict):
                    value = item.get("model") or item.get("model_id")
                    if isinstance(value, str) and value.strip():
                        self.model_id = value.strip()
                        return
        for value in event.values():
            if isinstance(value, dict):
                self._record_model(value)
                if self.model_id:
                    return

    def _record_usage(self, usage: dict[str, Any]) -> None:
        """Accumulate input/cached-input/cache-write/output token counts from a usage dict.

        Handles each provider's differing key names for the same fields.
        """
        self.tokens_input += _as_int(
            usage.get("input_tokens")
            or usage.get("inputTokens")
            or usage.get("prompt_tokens")
        )
        cached_input = usage.get("cached_input_tokens")
        if cached_input is None:
            cached_input = usage.get("cache_read_input_tokens")
        if cached_input is None:
            cached_input = usage.get("cacheReadInputTokens")
        if cached_input is None:
            cached_input = usage.get("cacheReadTokens")
        if cached_input is None:
            cached_input = usage.get("cacheInputTokens")
        if cached_input is None:
            cached_input = usage.get("cached_prompt_tokens")
        self.tokens_cached_input += _as_int(cached_input)
        cache_write = usage.get("cache_creation_input_tokens")
        if cache_write is None:
            cache_write = usage.get("cacheCreateTokens")
        if cache_write is None:
            cache_write = usage.get("cacheWriteTokens")
        if cache_write is not None:
            self.tokens_cache_write = (self.tokens_cache_write or 0) + _as_int(
                cache_write
            )
        self.tokens_output += _as_int(
            usage.get("output_tokens")
            or usage.get("outputTokens")
            or usage.get("completion_tokens")
        )

    def _record_cost(self, event: dict[str, Any]) -> None:
        """Capture a provider-reported cost field from ``event`` into ``self.cost_usd``."""
        cost = _as_float(
            event.get("total_cost_usd")
            or event.get("cost_usd")
            or event.get("cost")
            or event.get("total_cost")
        )
        if cost is not None:
            self.cost_usd = cost
            self.cost_source = "provider_reported"

    def _record_nested_telemetry(self, event: dict[str, Any]) -> None:
        """Walk ``event`` for modelUsage/usage/cost at any nesting depth (junie/grok shapes).

        Falls back to :func:`estimate_cost_usd` when no provider-reported
        cost was found, so telemetry never stays silently unset.
        """
        self._record_model(event)
        pending: list[dict[str, Any]] = [event]
        while pending:
            current = pending.pop()
            model_usage = current.get("modelUsage") or current.get("model_usage")
            if isinstance(model_usage, list):
                for usage in model_usage:
                    if isinstance(usage, dict):
                        self._record_usage(usage)
                        cost = _as_float(usage.get("cost") or usage.get("cost_usd"))
                        if cost is not None:
                            self.cost_usd = round((self.cost_usd or 0) + cost, 6)
                            self.cost_source = "provider_reported"
            usage = current.get("usage") or current.get("stats")
            if isinstance(usage, dict):
                self._record_usage(usage)
            self._record_cost(current)
            for value in current.values():
                if isinstance(value, dict):
                    pending.append(value)
        if self.cost_usd is None or (self.cost_source or "").startswith("estimated:"):
            self.cost_usd, self.cost_source = estimate_cost_usd(
                self.model_id,
                tokens_input=self.tokens_input,
                tokens_cached_input=self.tokens_cached_input,
                tokens_output=self.tokens_output,
            )

    def _session_banner(self, session_id: str, suffix: str = "") -> str:
        """Render the one-time yellow ``session: <id> model: <model>`` banner line.

        Returns "" for an already-rendered session id so the banner only
        appears once per session.
        """
        if not session_id or session_id == "?":
            return ""
        self.session_id = session_id
        if session_id in self._rendered_session_ids:
            return ""
        self._rendered_session_ids.add(session_id)
        model_suffix = f" model: {self.model_id}" if self.model_id else ""
        return (
            f"\x1b[33m[{stamp()}] session: {session_id}{model_suffix}\x1b[0m{suffix}\n"
        )

    def _format_json_event(self, event: dict[str, Any]) -> str:
        """Dispatch a decoded JSON event to the formatter for ``self.agent``."""
        if self.agent in {"claude", "agy", "cursor"}:
            if self.agent == "cursor":
                thinking = self._format_cursor_thinking(event)
                if thinking is not None:
                    return thinking
            return self._format_claude_event(event)
        if self.agent == "codex":
            return self._format_codex_event(event)
        if self.agent == "gemini":
            return self._format_gemini_event(event)
        if self.agent == "junie":
            return self._format_junie_event(event)
        if self.agent == "grok":
            return self._format_grok_event(event)
        return ""

    def _format_cursor_thinking(self, event: dict[str, Any]) -> str | None:
        """Render cursor-agent stream-json thinking deltas; None if not a thinking event."""
        if str(event.get("type") or "") != "thinking":
            return None
        text = str(event.get("text") or "")
        if str(event.get("subtype") or "") == "delta" and text:
            return f"\x1b[2m{text}\x1b[0m"
        return ""

    def _format_claude_event(self, event: dict[str, Any]) -> str:
        """Render one Claude/Agy streaming-json event (system/assistant/stream/result)."""
        self._record_model(event)
        event_type = str(event.get("type") or "")
        session_id = event.get("session_id")
        if isinstance(session_id, str) and session_id:
            self.session_id = session_id

        if (
            event_type == "system"
            and session_id
            and str(event.get("subtype") or "init") == "init"
        ):
            return self._session_banner(str(session_id))

        if event_type == "assistant":
            out: list[str] = []
            if isinstance(session_id, str) and session_id:
                out.append(self._session_banner(session_id))
            message = event.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text":
                        out.append("\n" + str(item.get("text") or "") + "\n")
                    elif item.get("type") == "thinking":
                        out.append(f"\n\x1b[2m{item.get('thinking') or ''}\x1b[0m\n")
                    elif item.get("type") == "tool_use":
                        out.append(tool_tag(str(item.get("name") or "?")))
            return "".join(out)

        if event_type == "stream_event":
            stream = event.get("event") or {}
            if not isinstance(stream, dict):
                return ""
            if stream.get("type") == "content_block_delta":
                delta = stream.get("delta") or {}
                if not isinstance(delta, dict):
                    return ""
                if delta.get("type") == "text_delta":
                    return str(delta.get("text") or "")
                if delta.get("type") == "thinking_delta":
                    return f"\x1b[2m{delta.get('thinking') or ''}\x1b[0m"
            if stream.get("type") == "content_block_start":
                block = stream.get("content_block") or {}
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    return "\n" + tool_tag(str(block.get("name") or "?"))
            return ""

        if event_type == "result":
            usage = event.get("usage")
            if isinstance(usage, dict):
                self._record_usage(usage)
            self._record_cost(event)
            return f"\n\x1b[32m[{stamp()}] {event.get('result') or 'done'}\x1b[0m\n"

        return ""

    def _format_codex_event(self, event: dict[str, Any]) -> str:
        """Render one Codex streaming-json event (thread/item/turn lifecycle types)."""
        self._record_model(event)
        event_type = str(event.get("type") or "")
        if event_type == "thread.started":
            session_id = str(event.get("thread_id") or "?")
            return self._session_banner(session_id)

        if event_type == "item.started":
            item = event.get("item") or {}
            if not isinstance(item, dict):
                return ""
            item_type = item.get("type")
            if item_type == "command_execution":
                return "\n" + tool_tag(f"$ {item.get('command', 'cmd')}") + "\n"
            if item_type == "mcp_tool_call":
                return tool_tag(
                    f"{item.get('server', '')}:{item.get('tool') or item.get('name') or '?'}"
                )
            if item_type == "web_search":
                return tool_tag("search")
            if item_type == "plan_update":
                return f"\x1b[35m[{stamp()} plan]\x1b[0m "
            return ""

        if event_type == "item.completed":
            item = event.get("item") or {}
            if not isinstance(item, dict):
                return ""
            item_type = item.get("type")
            if item_type == "agent_message":
                return "\n" + str(item.get("text") or "") + "\n"
            if item_type == "reasoning":
                return f"\x1b[2m{item.get('text', '')}\x1b[0m\n"
            if item_type == "command_execution":
                output = str(item.get("output") or "")
                return _truncate_block(output) if output else ""
            if item_type == "mcp_tool_call":
                result = item.get("result") or {}
                content = result.get("content") if isinstance(result, dict) else None
                first = content[0] if isinstance(content, list) and content else {}
                output = str(first.get("text") or "") if isinstance(first, dict) else ""
                return _truncate_block(output) if output else ""
            if item_type == "file_changes":
                return f"\x1b[32m[{stamp()} write: {item.get('path', '?')}]\x1b[0m\n"
            return ""

        if event_type in {"turn.completed", "turn_completed"}:
            usage = event.get("usage") or {}
            if isinstance(usage, dict):
                self._record_usage(usage)
                cached = usage.get("cached_input_tokens")
                cached_fragment = f" ({cached} cached)" if cached is not None else ""
                return (
                    f"\n\x1b[2m[{stamp()}] tokens: {usage.get('input_tokens', 0)} in"
                    f"{cached_fragment} / {usage.get('output_tokens', 0)} out\x1b[0m\n"
                )
            return ""

        if event_type in {"turn.failed", "turn_failed"}:
            return f"\n\x1b[31m[{stamp()} error] {_stringish(event.get('error') or event.get('message') or 'turn failed')}\x1b[0m\n"
        if event_type in {"turn.aborted", "turn_aborted"}:
            return f"\n\x1b[31m[{stamp()} abort] {_stringish(event.get('message') or event.get('reason') or event.get('error') or 'turn aborted')}\x1b[0m\n"
        return ""

    def _format_gemini_event(self, event: dict[str, Any]) -> str:
        """Render one Gemini streaming-json event, including synthesized token-usage lines."""
        self._record_model(event)
        event_type = str(event.get("type") or "")
        if event_type == "init":
            session_id = str(event.get("session_id") or "?")
            return self._session_banner(session_id)
        if event_type == "gemini":
            out: list[str] = []
            for thought in event.get("thoughts") or []:
                if isinstance(thought, dict):
                    out.append(
                        f"\x1b[2m[{stamp()} thinking] {thought.get('subject') or '?'}: {thought.get('description') or ''}\x1b[0m\n"
                    )
            content = str(event.get("content") or "")
            if content:
                out.append(content)
            for call in event.get("toolCalls") or []:
                if isinstance(call, dict):
                    out.append("\n" + tool_tag(str(call.get("name") or "?")))
            return "".join(out)
        if event_type == "message" and event.get("role") == "assistant":
            return str(event.get("content") or "")
        if event_type == "tool_use":
            return "\n" + tool_tag(
                str(event.get("tool_name") or event.get("name") or "?")
            )
        if event_type == "tool_result":
            output = str(event.get("output") or "")
            return _truncate_block(output) if output else ""
        if event_type == "error":
            return f"\x1b[31m[{stamp()} error] {event.get('message') or event.get('error') or 'unknown'}\x1b[0m\n"
        if event_type == "result":
            stats = event.get("stats") or {}
            status_line = (
                f"\n\x1b[32m[{stamp()}] {event.get('status') or 'done'}\x1b[0m\n"
            )
            if not isinstance(stats, dict):
                return status_line
            self._record_usage(stats)
            input_tokens = _as_int(stats.get("input_tokens"))
            output_tokens = _as_int(stats.get("output_tokens"))
            # Render the human-readable token line (same shape as codex/claude)
            # so the regex-based token extractor in spawn.py picks gemini usage
            # up from the transcript; without it research-swarm meta lands at 0.
            if input_tokens or output_tokens:
                cached = stats.get("cached_input_tokens") or stats.get(
                    "cache_read_input_tokens"
                )
                cached_fragment = f" ({_as_int(cached)} cached)" if cached else ""
                tokens_line = (
                    f"\x1b[2m[{stamp()}] tokens: {input_tokens} in"
                    f"{cached_fragment} / {output_tokens} out\x1b[0m\n"
                )
                return tokens_line + status_line
            return status_line
        return ""

    def _format_junie_event(self, event: dict[str, Any]) -> str:
        """Render one Junie streaming-json event's message/step text."""
        self._record_nested_telemetry(event)
        for key in ("session_id", "sessionId"):
            value = event.get(key)
            if isinstance(value, str) and value:
                self.session_id = value
                break
        usage = event.get("usage")
        if isinstance(usage, dict):
            self._record_usage(usage)
        message = (
            event.get("message")
            or event.get("text")
            or event.get("content")
            or event.get("details")
            or event.get("data")
        )
        text = _stringish(message)
        if not text or text == "None":
            return ""
        name = _stringish(event.get("name"))
        if name and event.get("type") == "step":
            return f"{name}: {text}\n"
        return text + "\n"

    def _format_grok_event(self, event: dict[str, Any]) -> str:
        """Render one Grok streaming-json event (thought/text/tool/diff/error/message)."""
        self._record_nested_telemetry(event)
        for key in ("session_id", "sessionId", "conversation_id"):
            value = event.get(key)
            if isinstance(value, str) and value:
                self.session_id = value
                break
        event_type = str(event.get("type") or "")
        if event_type == "end":
            value = event.get("sessionId") or event.get("session_id")
            if isinstance(value, str) and value:
                self.session_id = value
            return ""
        if event_type == "thought":
            text = _stringish(event.get("data"))
            return f"\x1b[2m{text}\x1b[0m" if text else ""
        if event_type == "text":
            return _stringish(event.get("data"))
        if event_type in {"tool", "tool_use", "tool_call"}:
            name = event.get("name") or event.get("tool") or event.get("toolName")
            return "\n" + tool_tag(_stringish(name) or "?")
        if event_type == "diff":
            path = _stringish(event.get("path"))
            return "\n" + tool_tag("diff") + (f" {path}\n" if path else "\n")
        if event_type == "error":
            message = event.get("message") or event.get("error") or event.get("data")
            text = _stringish(message)
            return f"\n\x1b[31m[{stamp()} error] {text or 'unknown'}\x1b[0m\n"

        message = (
            event.get("message")
            or event.get("text")
            or event.get("content")
            or event.get("data")
        )
        text = _stringish(message)
        if not text or text == "None":
            return ""
        return text + "\n"


def filter_stream(
    agent: str,
    *,
    stdin=None,
    stdout=None,
    raw_file: str | Path | None = None,
    default_model: str = "",
) -> int:
    """Read agent streaming-json from stdin, emit human text to stdout.

    Optional ``raw_file`` tees the unparsed stream for await/transcript parse
    while the pane only sees AgentStreamParser output.
    """
    import sys as _sys

    in_stream = stdin if stdin is not None else _sys.stdin.buffer
    out_stream = stdout if stdout is not None else _sys.stdout.buffer
    parser = AgentStreamParser(
        agent, default_model=default_model or resolve_default_model(agent)
    )
    raw_handle = None
    try:
        if raw_file:
            raw_path = Path(raw_file).expanduser()
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_handle = raw_path.open("ab")
        while True:
            chunk = in_stream.readline()
            if not chunk:
                break
            if raw_handle is not None:
                raw_handle.write(chunk)
                raw_handle.flush()
            display = parser.feed_line(chunk)
            if display:
                out_stream.write(display.encode("utf-8"))
                out_stream.flush()
    finally:
        if raw_handle is not None:
            raw_handle.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m vibecrafted_core.agent_stream --agent grok``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="vibecrafted_core.agent_stream",
        description=(
            "Filter agent streaming-json (stdin) into human-readable pane text "
            "(stdout). Used by non-interactive resume and worker pane launches."
        ),
    )
    parser.add_argument(
        "--agent",
        required=True,
        help="Agent family: grok, claude, codex, gemini, junie, agy",
    )
    parser.add_argument(
        "--raw-file",
        default="",
        help="Optional path to tee the raw streaming-json transcript",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Optional default model id for telemetry lines",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    agent = str(args.agent or "").strip().lower()
    if not agent:
        print("error: --agent is required", file=sys.stderr)
        return 2
    return filter_stream(
        agent,
        raw_file=args.raw_file or None,
        default_model=str(args.model or ""),
    )


if __name__ == "__main__":
    raise SystemExit(main())
