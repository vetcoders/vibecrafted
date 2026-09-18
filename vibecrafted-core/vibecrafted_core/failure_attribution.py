"""Name why a run failed: ``exit_code=1 (provider's code 403 quota exhausted)``.

Only what the provider itself said counts as evidence:

* plain-text lines its CLI printed (the supervisor merges stderr into the
  transcript — kimi's ``error: failed to run prompt: provider.auth_error: 403``
  arrives that way), and
* explicit error envelopes of its JSON stream (``type: error``,
  ``turn.failed``, kimi ``turn.step.retrying``, an errored ``result``).

Tool results and assistant prose are never scanned: a worker that *read* a
quota error did not die of one. The last matching line in the transcript tail
wins — it is the one closest to the exit. Every pattern below has a specimen
pinned in the test suite; a provider without one is not guessed and settles as
``unknown`` with a reason.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .agent_stream import ANSI_PATTERN, is_grok_ignorable_transport_error
from .telemetry import FAILURE_KINDS, FailureAttribution, Unknown

__all__ = [
    "FAILURE_KINDS",
    "NO_PATTERN_REASON",
    "TRANSCRIPT_UNAVAILABLE_REASON",
    "attribute_failure",
    "classify_line",
    "redact_message",
]

NO_PATTERN_REASON = "no matching provider pattern"
TRANSCRIPT_UNAVAILABLE_REASON = "transcript unavailable"
NO_CODE_REASON = "provider message carries no status code"
#: Same window run_triage reads for provider-overload markers.
TAIL_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 240

# Cause words, strongest first: a 403 that says "usage limit" is a quota, not
# an auth problem (kimi reports its billing cap as ``provider.auth_error: 403``).
_QUOTA_RE = re.compile(
    r"usage[-\s_]?limit|\bquota\b|insufficient_quota|credit balance is too low",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"\bauth(?:entication)?_error\b|\bunauthori[sz]ed\b|\bnot logged in\b"
    r"|please run /login|invalid api key|access token could not be refreshed"
    r"|refresh token was already used|AuthenticationError",
    re.IGNORECASE,
)
_RATE_RE = re.compile(
    r"rate[-\s_]?limit|too many requests|RateLimitError|\boverloaded\b",
    re.IGNORECASE,
)
# Status codes need a frame (provider/api/http/status/error/code) so a
# traceback's ``line 429`` never becomes a rate limit.
_CODE_RES = (
    re.compile(r"provider\.[a-z_]+:\s*([1-5]\d\d)\b", re.IGNORECASE),
    re.compile(r'"(?:status_code|statusCode|status|code)"\s*:\s*([1-5]\d\d)\b'),
    re.compile(
        r"\b(?:api|https?|status(?:[\s_]code)?|error|code)[\s:=#/\]-]*([1-5]\d\d)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*([1-5]\d\d)\s+(?:rate|too many|overloaded|forbidden|unauthori|you)",
        re.IGNORECASE,
    ),
)
_CODE_KINDS = {
    401: "auth_error",
    402: "quota_exhausted",
    403: "auth_error",
    429: "rate_limited",
    529: "rate_limited",
}
_JSON_ERROR_TYPES = frozenset({"error", "turn.failed", "turn_failed"})

_SECRET_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(
        r"\b(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)\S+",
        re.IGNORECASE,
    ),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
)


def redact_message(text: str) -> str:
    """Strip secrets and addresses from a provider message and bound its length."""
    clean = ANSI_PATTERN.sub("", str(text or "")).strip()
    for pattern in _SECRET_RES:
        if pattern.groups == 2:
            clean = pattern.sub(r"\1\2[redacted]", clean)
        else:
            clean = pattern.sub("[redacted]", clean)
    clean = " ".join(clean.split())
    if len(clean) > MAX_MESSAGE_CHARS:
        clean = clean[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return clean


def _as_text(value: Any) -> str:
    """Flatten an error envelope value into searchable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_error(event: dict[str, Any]) -> tuple[str, int | None] | None:
    """Error text and structured status code of a provider error envelope.

    Returns None for anything that is not an error envelope — assistant
    messages, tool calls and tool results in particular.
    """
    if str(event.get("role") or "") in {"tool", "assistant", "user"}:
        return None
    event_type = str(event.get("type") or "")
    agy_kind = str(event.get("event") or "")
    text = ""
    if event_type in _JSON_ERROR_TYPES or agy_kind == "error":
        text = _as_text(event.get("error") or event.get("message"))
    elif event_type == "turn.step.retrying":
        text = " ".join(
            _as_text(event.get(key)) for key in ("error_name", "error_message")
        )
    elif event_type == "result" and (
        event.get("is_error") is True
        or str(event.get("subtype") or "").startswith("error")
    ):
        text = _as_text(event.get("result") or event.get("error"))
    elif agy_kind == "result" and isinstance(event.get("result"), dict):
        text = _as_text(event["result"].get("error"))
    if not text.strip():
        return None
    code = None
    error = event.get("error")
    for raw in (
        event.get("status_code"),
        event.get("code"),
        error.get("code") if isinstance(error, dict) else None,
        error.get("status") if isinstance(error, dict) else None,
    ):
        if isinstance(raw, int) and not isinstance(raw, bool) and 100 <= raw <= 599:
            code = raw
            break
    return text, code


def _status_code(text: str) -> int | None:
    """First framed HTTP-style status code in *text*."""
    for pattern in _CODE_RES:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def classify_line(line: str) -> tuple[str, int | None, str] | None:
    """Classify one transcript line as a provider failure.

    Returns ``(kind, status_code, message_text)`` or None when the line is not
    a provider error (including tool output and assistant text).
    """
    clean = ANSI_PATTERN.sub("", line or "").strip()
    if not clean or is_grok_ignorable_transport_error(clean):
        return None
    code: int | None = None
    if clean.startswith("{"):
        try:
            event = json.loads(clean)
        except json.JSONDecodeError:
            event = None
        if isinstance(event, dict):
            envelope = _json_error(event)
            if envelope is None:
                return None
            text, code = envelope
            clean_text = text
        else:
            clean_text = clean
    else:
        clean_text = clean
    if code is None:
        code = _status_code(clean_text)
    if _QUOTA_RE.search(clean_text):
        kind = "quota_exhausted"
    elif _AUTH_RE.search(clean_text):
        kind = "auth_error"
    elif _RATE_RE.search(clean_text):
        kind = "rate_limited"
    elif code in _CODE_KINDS:
        kind = _CODE_KINDS[code]
    else:
        return None
    return kind, code, clean_text


def _tail_lines(path: Path) -> list[tuple[int, str]]:
    """``(1-based line number, text)`` for the transcript's last TAIL_BYTES."""
    data = path.read_bytes()
    start = 0
    if len(data) > TAIL_BYTES:
        newline = data.find(b"\n", len(data) - TAIL_BYTES)
        start = len(data) if newline < 0 else newline + 1
    base = data.count(b"\n", 0, start)
    return [
        (base + index + 1, raw.decode("utf-8", errors="replace"))
        for index, raw in enumerate(data[start:].split(b"\n"))
    ]


def _unknown(exit_code: int, reason: str, source: str) -> FailureAttribution:
    """A failure whose cause the transcript does not name."""
    missing = Unknown(reason=reason).as_dict()
    return FailureAttribution(
        exit_code=exit_code,
        kind="unknown",
        provider_code=missing,
        message=missing,
        evidence=missing,
        source=source,
        reason=reason,
    )


def attribute_failure(
    transcript: str | Path | None,
    exit_code: int | None,
    *,
    agent: str = "",
    source: str = "transcript",
) -> FailureAttribution | None:
    """Attribute a non-zero exit to the provider line that explains it.

    Returns None for a successful (or not yet exited) run. A failure whose
    transcript is missing or names no known provider cause is ``unknown`` with
    the reason, never a guess. *agent* is accepted for callers' symmetry; the
    patterns are provider-neutral because every specimen is.
    """
    del agent
    if exit_code is None or exit_code == 0:
        return None
    path = Path(transcript) if transcript else None
    if path is None or not path.is_file():
        return _unknown(exit_code, TRANSCRIPT_UNAVAILABLE_REASON, source)
    try:
        lines = _tail_lines(path)
    except OSError:
        return _unknown(exit_code, TRANSCRIPT_UNAVAILABLE_REASON, source)
    match: tuple[int, str, int | None, str] | None = None
    for number, text in lines:
        classified = classify_line(text)
        if classified is not None:
            kind, code, message = classified
            match = (number, kind, code, message)
    if match is None:
        return _unknown(exit_code, NO_PATTERN_REASON, source)
    number, kind, code, message = match
    return FailureAttribution(
        exit_code=exit_code,
        kind=kind,
        provider_code=code
        if code is not None
        else Unknown(reason=NO_CODE_REASON).as_dict(),
        message=redact_message(message),
        evidence={"path": str(path), "line": number},
        source=source,
    )
