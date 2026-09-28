"""Local evidence readers for telemetry's settle fallback (never estimates).

No daemon, network, writes, or independent ledger. Identity and a closed time
window are mandatory. Buckets are disjoint; reasoning is a reported subset.
Malformed evidence is discarded, including present-but-invalid optional counts.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_COUNT = 2**53 - 1
MAX_LINE = 8 * 1024 * 1024
BUCKETS = ("fresh_input", "cache_read", "cache_creation", "output", "reasoning")


def epoch(value: object) -> float | None:
    """ISO timestamp or harness epoch milliseconds; no mtime attribution."""
    try:
        if type(value) in (int, float):
            return float(value) / 1000 if math.isfinite(value) else None
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (ValueError, OverflowError, TypeError):
        return None


def count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise ValueError("invalid token count")
    return value


def counts(usage: dict, names: tuple[str, ...]) -> tuple[int, ...]:
    # Validate reported totals too, even when they are not part of the sum.
    for key in ("total_tokens", "totalTokens"):
        if key in usage:
            count(usage[key])
    return tuple(count(usage.get(name, 0)) for name in names)


def _rows(path: Path, markers: tuple[bytes, ...] = ()) -> Iterator[dict[str, Any]]:
    # Bound each read, ignore concurrent trailing fragments and oversized lines.
    try:
        if path.is_symlink() or not path.is_file():
            return
        with path.open("rb") as handle:
            while line := handle.readline(MAX_LINE + 1):
                if len(line) > MAX_LINE:
                    while line and not line.endswith(b"\n"):
                        line = handle.readline(MAX_LINE + 1)
                    continue
                if not line.endswith(b"\n"):
                    break
                if markers and not any(marker in line for marker in markers):
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _key(row: dict) -> str:
    for name in ("id", "eventId", "event_id", "requestId", "request_id"):
        if isinstance(row.get(name), str) and row[name]:
            return row[name]
    # Kimi/Junie/Grok lack event ids: full timestamp-bearing record identity.
    return hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class HarnessUsage:
    models: dict[str, dict[str, int]]
    events: int

    def totals(self) -> dict[str, int]:
        return {
            key: count(sum(row[key] for row in self.models.values())) for key in BUCKETS
        }


class _Collector:
    def __init__(self) -> None:
        self.models: dict[str, dict[str, int]] = {}
        self.events = 0
        self.seen: dict[tuple[str, str], tuple[int, ...]] = {}

    def result(self) -> HarnessUsage | None:
        if not self.events:
            return None
        result = HarnessUsage(self.models, self.events)
        try:
            count(sum(result.totals()[key] for key in BUCKETS[:4]))
        except ValueError:
            return None
        return result

    def add(
        self, model: str, key: str, values: tuple[int, ...], *, before: bool = False
    ) -> None:
        previous = self.seen.get((model, key), (0,) * len(BUCKETS))
        maxima = tuple(max(a, b) for a, b in zip(previous, values, strict=True))
        if before:
            self.seen[model, key] = maxima
            return
        delta = tuple(a - b for a, b in zip(maxima, previous, strict=True))
        if (model, key) in self.seen and not any(delta):
            return
        current = self.models.get(model, dict.fromkeys(BUCKETS, 0))
        updated = {
            k: count(current[k] + v) for k, v in zip(BUCKETS, delta, strict=True)
        }
        count(sum(updated[k] for k in BUCKETS[:4]))
        self.models[model] = updated
        self.seen[model, key] = maxima
        self.events += 1


def _codex(
    paths: list[Path], session: str, start: float, end: float, model: str
) -> HarnessUsage | None:
    for path in paths:
        rows = _rows(path, (b"session_meta", b"turn_context", b"token_count"))
        first = next(rows, {})
        meta = first.get("payload", {})
        if (
            first.get("type") != "session_meta"
            or not isinstance(meta, dict)
            or meta.get("id") != session
        ):
            continue
        created = epoch(first.get("timestamp") or meta.get("timestamp"))
        baseline_known = created is not None and created >= start
        previous = (0, 0, 0, 0)
        collector = _Collector()
        for row in rows:
            stamp = epoch(row.get("timestamp"))
            if stamp is None or stamp > end:
                continue
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            if row.get("type") == "turn_context":
                model = str(payload.get("model") or model)
                continue
            if row.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            usage = info.get("total_token_usage") if isinstance(info, dict) else None
            if (
                not isinstance(usage, dict)
                or not {"input_tokens", "output_tokens"} <= usage.keys()
            ):
                continue
            try:
                inp, cached, out, reasoning, total = counts(
                    usage,
                    (
                        "input_tokens",
                        "cached_input_tokens",
                        "output_tokens",
                        "reasoning_output_tokens",
                        "total_tokens",
                    ),
                )
                if (
                    cached > inp
                    or reasoning > out
                    or ("total_tokens" in usage and total != inp + out)
                ):
                    continue
                current = tuple(
                    max(a, b)
                    for a, b in zip(
                        previous, (inp, cached, out, reasoning), strict=True
                    )
                )
                if stamp < start:
                    previous, baseline_known = current, True
                    continue
                if not baseline_known:
                    return None  # resumed session without an attributable baseline
                delta = tuple(a - b for a, b in zip(current, previous, strict=True))
                if delta[1] > delta[0] or delta[3] > delta[2]:
                    continue
                if current == previous and collector.events:
                    continue
                collector.add(
                    model,
                    _key(row),
                    (delta[0] - delta[1], delta[1], 0, delta[2], delta[3]),
                )
                previous = current
            except ValueError:
                continue
        # First authoritative file wins; forks repeating parent metadata cannot add.
        return collector.result()
    return None


def _event_usage(
    agent: str, row: dict, model: str
) -> list[tuple[str, str, tuple[int, ...]]]:
    """Extract only empirically identified billing records, never context sizes."""
    if agent in ("claude", "kimi") and isinstance(row.get("message"), dict):
        message = row["message"]
        usage = message.get("usage")
        if (
            not isinstance(usage, dict)
            or not message.get("id")
            or "input_tokens" not in usage
            or "output_tokens" not in usage
        ):
            return []
        values = counts(
            usage,
            (
                "input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "output_tokens",
                "reasoning_tokens",
            ),
        )
        return [(str(message.get("model") or model), str(message["id"]), values)]
    if (
        agent == "kimi"
        and row.get("type") == "usage.record"
        and row.get("usageScope") == "turn"
    ):
        usage = row.get("usage")
        if not isinstance(usage, dict) or not {"inputOther", "output"} <= usage.keys():
            return []
        values = counts(
            usage,
            (
                "inputOther",
                "inputCacheRead",
                "inputCacheCreation",
                "output",
                "reasoning",
            ),
        )
        return [(str(row.get("model") or model), _key(row), values)]
    if agent == "grok" and row.get("msg") == "shell.turn.inference_done":
        usage = row.get("ctx")
        if (
            not isinstance(usage, dict)
            or not {"prompt_tokens", "completion_tokens"} <= usage.keys()
        ):
            return []
        inp, cached, out, reasoning = counts(
            usage,
            (
                "prompt_tokens",
                "cached_prompt_tokens",
                "completion_tokens",
                "reasoning_tokens",
            ),
        )
        if cached > inp:
            raise ValueError("cache exceeds prompt")
        return [
            (
                str(usage.get("model") or model),
                _key(row),
                (inp - cached, cached, 0, out, reasoning),
            )
        ]
    if agent == "junie":
        event = row.get("event", {}).get("agentEvent", {})
        if event.get("kind") != "LlmResponseMetadataEvent":
            return []
        result = []
        for index, usage in enumerate(event.get("modelUsage", [])):
            if (
                not isinstance(usage, dict)
                or not {"inputTokens", "outputTokens"} <= usage.keys()
            ):
                raise ValueError("incomplete model usage")
            values = counts(
                usage,
                (
                    "inputTokens",
                    "cacheInputTokens",
                    "cacheCreateTokens",
                    "outputTokens",
                    "reasoningTokens",
                ),
            )
            result.append(
                (str(usage.get("model") or model), f"{_key(row)}:{index}", values)
            )
        return result
    if agent == "cursor" and row.get("type") == "result":
        usage = row.get("usage")
        if (
            not isinstance(usage, dict)
            or not {"inputTokens", "outputTokens"} <= usage.keys()
        ):
            return []
        inp, cached, created, out, reasoning = counts(
            usage,
            (
                "inputTokens",
                "cacheReadTokens",
                "cacheWriteTokens",
                "outputTokens",
                "reasoningTokens",
            ),
        )
        if cached + created > inp:
            raise ValueError("cache exceeds input")
        return [
            (
                str(row.get("model") or model),
                _key(row),
                (inp - cached - created, cached, created, out, reasoning),
            )
        ]
    if agent == "copilot" and row.get("type") == "session.shutdown":
        result = []
        for name, metric in row.get("data", {}).get("modelMetrics", {}).items():
            usage = metric.get("usage", {})
            if not {"inputTokens", "outputTokens"} <= usage.keys():
                raise ValueError("incomplete Copilot usage")
            inp, cached, created, out, reasoning = counts(
                usage,
                (
                    "inputTokens",
                    "cacheReadTokens",
                    "cacheWriteTokens",
                    "outputTokens",
                    "reasoningTokens",
                ),
            )
            if cached + created > inp or reasoning > out:
                raise ValueError("invalid Copilot subsets")
            result.append(
                (
                    name,
                    "session.shutdown",
                    (inp - cached - created, cached, created, out, reasoning),
                )
            )
        return result
    return []


def _gemini(home: Path, session: str, start: float, end: float) -> HarnessUsage | None:
    """Measured Gemini CLI chat records only; Antigravity protobuf is not guessed."""
    collector = _Collector()
    for path in sorted((home / ".gemini/tmp").glob("*/chats/session-*.json")):
        try:
            if path.stat().st_size > 32 * 1024 * 1024:
                continue
            data = json.loads(path.read_bytes())
            if not isinstance(data, dict) or data.get("sessionId") != session:
                continue
            for row in data.get("messages", []):
                stamp = epoch(row.get("timestamp"))
                usage = row.get("tokens")
                if (
                    stamp is None
                    or stamp > end
                    or not isinstance(usage, dict)
                    or not row.get("id")
                    or not {"input", "output"} <= usage.keys()
                ):
                    continue
                try:
                    inp, out, cached, thoughts, tool, total = counts(
                        usage,
                        ("input", "output", "cached", "thoughts", "tool", "total"),
                    )
                    if (
                        cached > inp
                        or tool
                        or ("total" in usage and total != inp + out + thoughts)
                    ):
                        continue
                    collector.add(
                        str(row.get("model") or ""),
                        str(row["id"]),
                        (inp - cached, cached, 0, count(out + thoughts), thoughts),
                        before=stamp < start,
                    )
                except ValueError:
                    continue
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return collector.result()


def resolve_harness_usage(
    *,
    agent: str,
    session_id: str,
    started_at: object,
    completed_at: object,
    model: str = "",
    home: Path | None = None,
) -> HarnessUsage | None:
    """Resolve a single closed run; absence/unattributable evidence stays unknown."""
    if agent == "kimi":
        session_id = session_id.removeprefix("session_")
    start, end = epoch(started_at), epoch(completed_at)
    if (
        start is None
        or end is None
        or start > end
        or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id)
    ):
        return None
    home = home or Path.home()
    if agent in ("agy", "gemini"):
        return _gemini(home, session_id, start, end)
    if agent == "codex":
        root = (
            Path(os.environ.get("CODEX_HOME", home / ".codex"))
            if home == Path.home()
            else home / ".codex"
        )
        return _codex(
            sorted((root / "sessions").glob(f"*/*/*/*{session_id}.jsonl")),
            session_id,
            start,
            end,
            model,
        )
    patterns = {
        "claude": [f".claude/projects/*/{session_id}.jsonl"],
        "kimi": [
            f".kimi-code/sessions/*/session_{session_id}/wire.jsonl",
            f".kimi-code/sessions/*/session_{session_id}/agents/*/wire.jsonl",
            f".claude/projects/*/{session_id}.jsonl",
        ],
        "grok": [".grok/logs/unified*.jsonl"],
        "junie": [f".junie/sessions/{session_id}/events.jsonl"],
        "cursor": [
            f".cursor/projects/*/agent-transcripts/{session_id}/{session_id}.jsonl",
            f".cursor/projects/*/agent-transcripts/{session_id}.jsonl",
        ],
        "copilot": [f".copilot/session-state/{session_id}/events.jsonl"],
    }
    collector = _Collector()
    for pattern in patterns.get(agent, []):
        for path in sorted(home.glob(pattern)):
            for row in _rows(path):
                stamp = epoch(
                    row.get(
                        "timestamp",
                        row.get("ts", row.get("time", row.get("timestampMs"))),
                    )
                )
                if stamp is None or stamp > end:
                    continue
                identity = row.get("sessionId", row.get("session_id", row.get("sid")))
                if agent == "kimi" and isinstance(identity, str):
                    identity = identity.removeprefix("session_")
                if identity is not None and identity != session_id:
                    continue
                if agent == "grok" and identity != session_id:
                    continue
                if agent == "copilot":
                    data = row.get("data")
                    if not isinstance(data, dict):
                        continue
                    session_start = epoch(data.get("sessionStartTime"))
                    if session_start is None or session_start < start:
                        continue  # whole-session summary cannot prove a resumed run
                try:
                    entries = _event_usage(agent, row, model)
                    for _, _, values in entries:
                        count(sum(values[:4]))
                    # A multi-model line is one piece of evidence: overflow in
                    # its last bucket must not leave earlier buckets counted.
                    prior_models = {
                        name: collector.models.get(name) for name, _, _ in entries
                    }
                    prior_seen = {
                        (name, key): collector.seen.get((name, key))
                        for name, key, _ in entries
                    }
                    prior_events = collector.events
                    try:
                        for name, key, values in entries:
                            collector.add(name, key, values, before=stamp < start)
                    except ValueError:
                        for name, previous in prior_models.items():
                            if previous is None:
                                collector.models.pop(name, None)
                            else:
                                collector.models[name] = previous
                        for key, previous in prior_seen.items():
                            if previous is None:
                                collector.seen.pop(key, None)
                            else:
                                collector.seen[key] = previous
                        collector.events = prior_events
                        raise
                except (ValueError, TypeError, AttributeError):
                    continue
    if not collector.events:
        return None
    result = HarnessUsage(collector.models, collector.events)
    try:
        count(sum(result.totals()[k] for k in BUCKETS[:4]))
    except ValueError:
        return None
    return result
