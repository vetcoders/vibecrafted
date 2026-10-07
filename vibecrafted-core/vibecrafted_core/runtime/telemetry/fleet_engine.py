"""Local fleet token engines.

Read-only scanners for host session stores. The only write is the engine
projection under ``$VIBECRAFTED_HOME/telemetry/<agent>/`` (``quota.json`` plus
the mtime sidecar ``scan-cache.json``). Nothing is sent off the machine.

``cache_semantics`` is declared per agent so consumers do not guess:

- ``subset_of_input`` — cached/reasoning tokens already sit inside input or
  output. ``processed_total`` is input+output (or the provider total that
  equals that sum). Cache buckets stay in ``native`` and are not added again.
- ``separate_buckets`` — each native bucket is its own spend.
  ``processed_total`` is the sum of those buckets.

Measured on this host (2026-10-03), not assumed:

- codex ``total_token_usage`` is cumulative per session; the last event wins.
  ``total_tokens == input_tokens + output_tokens``; ``cached_input_tokens``
  is inside input.
- claude ``message.usage`` buckets are separate and must be summed per event.
- grok ``usage.json`` ``totalTokens == inputTokens + outputTokens``;
  ``cachedReadTokens`` is inside input.
- junie ``modelUsage`` fields are separate per call (cache create/input can
  exceed the fresh ``inputTokens``).
- copilot spend lives on the last ``session.shutdown`` ``data.modelMetrics``
  usage. ``cacheReadTokens <= inputTokens`` on sampled sessions. Nested
  ``agentMetrics`` repeats the same numbers and is ignored. Compaction
  events are a slice, not added on top of shutdown.
- cursor has no local token ledger. It does not grow an archive.

Daily rows are appended to ``telemetry/archive/<agent>.jsonl`` (see
``usage_archive``). Cache inside input is subtracted for codex, grok, and
copilot so ``total_tokens`` matches ``processed_total``. A warm ``once`` or
daemon tick reuses the mtime scan cache and does not reread an unchanged file.
Rows from a file that later disappears stay in the sidecar, so a shared day
does not shrink to the files that remain. A file that only moved (same
session id, new path) is not counted twice.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path

# Tests load this file by path. The deck wrappers put this directory on
# sys.path first; do the same so `usage_archive` resolves either way.
_ENGINE_DIR = Path(__file__).resolve().parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))
import usage_archive

TAIL_BYTES = 400 * 1024
# Codex usage is cumulative and usually sits near EOF. A fixed 400 KB window
# misses it when a later oversized event (tool output) pushes the last
# token_count further back. Walk backward in chunks and stop at the cap.
CODEX_TAIL_CAP = 32 * 1024 * 1024
# 3: scan-cache entries carry per-file day buckets for the archive.
PARSER_VERSION = 3
_CODEX_DAY_IN_PATH = re.compile(r"sessions/(\d{4})/(\d{2})/(\d{2})/")

CODEX_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
CLAUDE_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
GROK_KEYS = (
    "inputTokens",
    "outputTokens",
    "cachedReadTokens",
    "cacheCreationTokens",
    "reasoningTokens",
    "totalTokens",
    "modelCalls",
    "costUsdTicks",
)
JUNIE_KEYS = (
    "inputTokens",
    "cacheInputTokens",
    "cacheCreateTokens",
    "cacheReadTokens",
    "outputTokens",
)
COPILOT_KEYS = (
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
)

FLEET_AGENTS = ("codex", "claude", "grok", "junie", "copilot", "cursor")
CURSOR_REASON = "usage lives in Cursor cloud"


def fmt_tokens(value: int) -> str:
    number = int(value)
    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.1f}B"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k"
    return str(number)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(text)
        except ValueError:
            return 0
    return 0


def _home() -> Path:
    return Path(os.environ.get("HOME") or str(Path.home()))


def quota_dir(agent: str, override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    base = os.environ.get("VIBECRAFTED_HOME")
    root = Path(base).expanduser() if base else _home() / ".vibecrafted"
    return root / "telemetry" / agent


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def default_store(agent: str) -> Path:
    home = _home()
    roots = {
        "codex": home / ".codex",
        "claude": home / ".claude",
        "grok": home / ".grok",
        "junie": home / ".junie",
        "copilot": home / ".copilot",
        "cursor": home / ".cursor",
    }
    return roots[agent]


def cache_semantics(agent: str) -> str | None:
    if agent in {"codex", "grok", "copilot"}:
        return "subset_of_input"
    if agent in {"claude", "junie"}:
        return "separate_buckets"
    return None


def _day_from_iso(value: object, fallback_mtime: float) -> str:
    if (
        isinstance(value, str)
        and len(value) >= 10
        and value[4] == "-"
        and value[7] == "-"
    ):
        return value[:10]
    return datetime.fromtimestamp(fallback_mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def _day_from_epoch(value: object, fallback_mtime: float) -> str:
    number = _as_int(value)
    if number > 10_000_000_000:
        number = int(number / 1000)
    if number > 1_000_000_000:
        return datetime.fromtimestamp(number, tz=timezone.utc).strftime("%Y-%m-%d")
    return _day_from_iso(None, fallback_mtime)


def _subset_bucket(
    input_tokens: int, output_tokens: int, cache_read: int, cache_create: int
) -> dict[str, int]:
    """Cache sits inside input. Archive input is the fresh remainder."""

    return {
        "input_tokens": max(0, input_tokens - cache_read - cache_create),
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_create,
        "cache_read_tokens": cache_read,
    }


def _separate_bucket(
    input_tokens: int, output_tokens: int, cache_read: int, cache_create: int
) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_create,
        "cache_read_tokens": cache_read,
    }


def _day_row(day: str, model: str, bucket: dict[str, int]) -> dict | None:
    if usage_archive.bucket_total(bucket) <= 0:
        return None
    return {"day": day, "model": model, **bucket}


def _walk_files(
    roots: Iterable[Path], name_ok: Callable[[str], bool]
) -> Iterator[Path]:
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(dirpath)
            dirnames[:] = [
                name for name in dirnames if not (current / name).is_symlink()
            ]
            for name in filenames:
                if name_ok(name):
                    yield current / name


def discover_files(agent: str, store: Path) -> list[Path]:
    if agent == "codex":
        return list(
            _walk_files(
                (store / "sessions", store / "archived_sessions"),
                lambda name: name.endswith(".jsonl"),
            )
        )
    if agent == "claude":
        return list(
            _walk_files((store / "projects",), lambda name: name.endswith(".jsonl"))
        )
    if agent == "grok":
        return list(
            _walk_files((store / "sessions",), lambda name: name == "usage.json")
        )
    if agent == "junie":
        return list(
            _walk_files(
                (store / "sessions",),
                lambda name: name == "events.jsonl",
            )
        )
    if agent == "copilot":
        return list(
            _walk_files(
                (store / "session-state",),
                lambda name: name == "events.jsonl",
            )
        )
    return []


def _blank_native(keys: tuple[str, ...]) -> dict[str, int]:
    return {key: 0 for key in keys}


def _add_native(total: dict[str, int], extra: dict[str, int]) -> None:
    for key, value in extra.items():
        total[key] = total.get(key, 0) + _as_int(value)


def _codex_meta_from_line(
    line: bytes,
) -> tuple[dict[str, int] | None, str | None, str | None]:
    if b"token_count" not in line and b"turn_context" not in line:
        return None, None, None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None, None, None
    if not isinstance(obj, dict):
        return None, None, None
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else None
    payload_type = payload.get("type") if isinstance(payload, dict) else None
    model = None
    if payload_type == "turn_context" or obj.get("type") == "turn_context":
        candidate = payload.get("model") if isinstance(payload, dict) else None
        if isinstance(candidate, str) and candidate.strip():
            model = candidate.strip()
    if obj.get("type") != "token_count" and payload_type != "token_count":
        return None, None, model
    info = payload.get("info") if isinstance(payload, dict) else None
    usage = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(usage, dict):
        return None, None, model
    native = _blank_native(CODEX_KEYS)
    for key in CODEX_KEYS:
        native[key] = _as_int(usage.get(key))
    if native["total_tokens"] <= 0:
        native["total_tokens"] = native["input_tokens"] + native["output_tokens"]
    timestamp = obj.get("timestamp")
    iso = timestamp if isinstance(timestamp, str) else None
    return native, iso, model


def _codex_native_from_line(line: bytes) -> dict[str, int] | None:
    native, _iso, _model = _codex_meta_from_line(line)
    return native


def _walk_codex_tail(
    path: Path, cap: int = CODEX_TAIL_CAP, chunk: int = TAIL_BYTES
) -> tuple[dict[str, int] | None, str | None, str | None]:
    """Newest cumulative usage within ``cap`` bytes of EOF. Read-only.

    The walk stops at the first (newest) ``token_count``. A model seen closer
    to EOF is kept; the walk does not continue toward the start of a 47 GB
    rollout just to find an older ``turn_context``.
    """

    try:
        size = path.stat().st_size
    except OSError:
        return None, None, None
    if size <= 0:
        return None, None, None
    remaining = min(size, cap)
    offset = size
    carry = b""
    found_model: str | None = None
    with path.open("rb") as handle:
        while remaining > 0:
            step = min(chunk, remaining)
            offset -= step
            handle.seek(offset)
            block = handle.read(step) + carry
            remaining -= step
            if offset > 0:
                newline = block.find(b"\n")
                if newline == -1:
                    carry = block
                    continue
                carry = block[:newline]
                block = block[newline + 1 :]
            else:
                carry = b""
            for line in reversed(block.splitlines()):
                native, iso, model = _codex_meta_from_line(line)
                if model and found_model is None:
                    found_model = model
                if native is not None:
                    return native, iso, found_model
    if carry:
        native, iso, model = _codex_meta_from_line(carry)
        if model and found_model is None:
            found_model = model
        if native is not None:
            return native, iso, found_model
    return None, None, found_model


def last_codex_native(
    path: Path, cap: int = CODEX_TAIL_CAP, chunk: int = TAIL_BYTES
) -> dict[str, int] | None:
    """Newest cumulative usage within ``cap`` bytes of EOF. Read-only."""

    native, _iso, _model = _walk_codex_tail(path, cap=cap, chunk=chunk)
    return native


def _codex_day(path: Path, iso: str | None, mtime: float) -> str:
    if iso:
        return _day_from_iso(iso, mtime)
    match = _CODEX_DAY_IN_PATH.search(path.as_posix())
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return _day_from_iso(None, mtime)


def _codex_day_rows(
    path: Path, native: dict[str, int], iso: str | None, model: str | None, mtime: float
) -> list[dict]:
    bucket = _subset_bucket(
        native.get("input_tokens", 0),
        native.get("output_tokens", 0),
        native.get("cached_input_tokens", 0),
        native.get("cache_write_input_tokens", 0),
    )
    row = _day_row(_codex_day(path, iso, mtime), model or "codex", bucket)
    return [row] if row else []


def parse_codex_tail(text: str) -> dict[str, int] | None:
    """Last cumulative ``token_count`` in a tail window. Earlier events lose."""

    last: dict[str, int] | None = None
    for line in text.splitlines():
        native = _codex_native_from_line(line.encode("utf-8"))
        if native is not None:
            last = native
    return last


def codex_processed(native: dict[str, int]) -> int:
    total = native.get("total_tokens") or 0
    if total > 0:
        return total
    return native.get("input_tokens", 0) + native.get("output_tokens", 0)


def parse_grok_document(document: dict) -> tuple[dict[str, int], str | None]:
    session = (
        document.get("session")
        if isinstance(document.get("session"), dict)
        else document
    )
    if not isinstance(session, dict):
        return _blank_native(GROK_KEYS), None
    native = _blank_native(GROK_KEYS)
    for key in GROK_KEYS:
        native[key] = _as_int(session.get(key))
    if native["totalTokens"] <= 0:
        native["totalTokens"] = native["inputTokens"] + native["outputTokens"]
    model = session.get("primaryModelId")
    return native, model if isinstance(model, str) else None


def grok_processed(native: dict[str, int]) -> int:
    total = native.get("totalTokens") or 0
    if total > 0:
        return total
    return native.get("inputTokens", 0) + native.get("outputTokens", 0)


def grok_day_rows(document: dict, mtime: float) -> list[dict]:
    """One row per ``modelUsage`` entry. Session totals are the fallback.

    Cached tokens sit inside input (``subset_of_input``). ``processed_total``
    stays on ``totalTokens`` and is not rebuilt from the split buckets.
    """

    iso = (
        document.get("updatedAt")
        if isinstance(document.get("updatedAt"), str)
        else None
    )
    day = _day_from_iso(iso, mtime)
    session = (
        document.get("session")
        if isinstance(document.get("session"), dict)
        else document
    )
    if not isinstance(session, dict):
        return []
    rows: list[dict] = []
    usage = session.get("modelUsage")
    if isinstance(usage, dict):
        for model, body in usage.items():
            if not isinstance(body, dict):
                continue
            bucket = _subset_bucket(
                _as_int(body.get("inputTokens")),
                _as_int(body.get("outputTokens")),
                _as_int(body.get("cachedReadTokens")),
                _as_int(body.get("cacheCreationTokens")),
            )
            row = _day_row(day, str(model), bucket)
            if row is not None:
                rows.append(row)
    if rows:
        return rows
    native, model = parse_grok_document(document)
    bucket = _subset_bucket(
        native.get("inputTokens", 0),
        native.get("outputTokens", 0),
        native.get("cachedReadTokens", 0),
        native.get("cacheCreationTokens", 0),
    )
    row = _day_row(day, model or "grok", bucket)
    return [row] if row else []


def _claude_from_line(
    line: str,
    native: dict[str, int],
    days: list | None = None,
    mtime: float = 0.0,
) -> bool:
    if "usage" not in line:
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    message = obj.get("message") if isinstance(obj, dict) else None
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return False
    for key in CLAUDE_KEYS:
        native[key] += _as_int(usage.get(key))
    if days is not None:
        model = message.get("model") if isinstance(message, dict) else None
        model_name = (
            model.strip() if isinstance(model, str) and model.strip() else "claude"
        )
        bucket = _separate_bucket(
            _as_int(usage.get("input_tokens")),
            _as_int(usage.get("output_tokens")),
            _as_int(usage.get("cache_read_input_tokens")),
            _as_int(usage.get("cache_creation_input_tokens")),
        )
        row = _day_row(_day_from_iso(obj.get("timestamp"), mtime), model_name, bucket)
        if row is not None:
            days.append(row)
    return True


def claude_processed(native: dict[str, int]) -> int:
    return sum(native.get(key, 0) for key in CLAUDE_KEYS)


def _iter_junie_usages(obj: object) -> Iterator[dict]:
    if isinstance(obj, dict):
        if "inputTokens" in obj and "outputTokens" in obj:
            yield obj
            return
        for value in obj.values():
            yield from _iter_junie_usages(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_junie_usages(value)


def _junie_from_line(
    line: str,
    native: dict[str, int],
    days: list | None = None,
    mtime: float = 0.0,
) -> bool:
    if "inputTokens" not in line:
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    hit = False
    day = _day_from_epoch(
        obj.get("timestampMs") if isinstance(obj, dict) else None, mtime
    )
    for usage in _iter_junie_usages(obj):
        hit = True
        for key in JUNIE_KEYS:
            native[key] += _as_int(usage.get(key))
        if days is None:
            continue
        model = usage.get("model")
        model_name = (
            model.strip() if isinstance(model, str) and model.strip() else "junie"
        )
        bucket = _separate_bucket(
            _as_int(usage.get("inputTokens")),
            _as_int(usage.get("outputTokens")),
            _as_int(usage.get("cacheInputTokens"))
            + _as_int(usage.get("cacheReadTokens")),
            _as_int(usage.get("cacheCreateTokens")),
        )
        row = _day_row(day, model_name, bucket)
        if row is not None:
            days.append(row)
    return hit


def junie_processed(native: dict[str, int]) -> int:
    return sum(native.get(key, 0) for key in JUNIE_KEYS)


def _sum_model_metrics(metrics: dict) -> dict[str, int]:
    native = _blank_native(COPILOT_KEYS)
    for body in metrics.values():
        usage = body.get("usage") if isinstance(body, dict) else None
        if not isinstance(usage, dict):
            continue
        for key in COPILOT_KEYS:
            native[key] += _as_int(usage.get(key))
    return native


def _copilot_from_line(line: str, state: dict) -> bool:
    if "inputTokens" not in line:
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(obj, dict):
        return False
    data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
    kind = obj.get("type")
    if kind == "session.shutdown":
        metrics = data.get("modelMetrics")
        if isinstance(metrics, dict):
            state["shutdown"] = _sum_model_metrics(metrics)
            state["saw_shutdown"] = True
            timestamp = obj.get("timestamp")
            if isinstance(timestamp, str):
                state["iso"] = timestamp
            models: dict[str, dict[str, int]] = {}
            for model, body in metrics.items():
                usage = body.get("usage") if isinstance(body, dict) else None
                if not isinstance(usage, dict):
                    continue
                models[str(model)] = _subset_bucket(
                    _as_int(usage.get("inputTokens")),
                    _as_int(usage.get("outputTokens")),
                    _as_int(usage.get("cacheReadTokens")),
                    _as_int(usage.get("cacheWriteTokens")),
                )
            # Cumulative snapshot: this shutdown replaces earlier models.
            state["models"] = models
            return True
        return False
    if kind == "session.compaction_complete" and not state.get("saw_shutdown"):
        blob = data.get("compactionTokensUsed")
        if isinstance(blob, dict):
            fallback = state["fallback"]
            for key in COPILOT_KEYS:
                fallback[key] += _as_int(blob.get(key))
            timestamp = obj.get("timestamp")
            if isinstance(timestamp, str) and "iso" not in state:
                state["iso"] = timestamp
            return True
    return False


def copilot_processed(native: dict[str, int]) -> int:
    return native.get("inputTokens", 0) + native.get("outputTokens", 0)


def _empty_copilot_state() -> dict:
    return {
        "saw_shutdown": False,
        "shutdown": None,
        "fallback": _blank_native(COPILOT_KEYS),
        "models": {},
    }


def _copilot_day_rows(state: dict, mtime: float) -> list[dict]:
    day = _day_from_iso(state.get("iso"), mtime)
    models = state.get("models")
    rows: list[dict] = []
    if isinstance(models, dict) and models:
        for model, bucket in models.items():
            if not isinstance(bucket, dict):
                continue
            row = _day_row(day, str(model), bucket)
            if row is not None:
                rows.append(row)
        return rows
    native = _copilot_native_from_state(state)
    bucket = _subset_bucket(
        native.get("inputTokens", 0),
        native.get("outputTokens", 0),
        native.get("cacheReadTokens", 0),
        native.get("cacheWriteTokens", 0),
    )
    row = _day_row(day, "copilot", bucket)
    return [row] if row else []


def _copilot_native_from_state(state: dict) -> dict[str, int]:
    shutdown = state.get("shutdown")
    if isinstance(shutdown, dict):
        return {key: _as_int(shutdown.get(key)) for key in COPILOT_KEYS}
    fallback = state.get("fallback")
    if isinstance(fallback, dict):
        return {key: _as_int(fallback.get(key)) for key in COPILOT_KEYS}
    return _blank_native(COPILOT_KEYS)


def _session_id(agent: str, path: Path) -> str:
    if agent == "junie":
        return path.parent.name
    if agent == "copilot":
        return path.parent.name
    if agent == "grok":
        # sessions/<id>/<leaf>/usage.json
        return path.parent.parent.name
    return path.stem


def _processed_for(agent: str, native: dict[str, int]) -> int:
    if agent == "codex":
        return codex_processed(native)
    if agent == "claude":
        return claude_processed(native)
    if agent == "grok":
        return grok_processed(native)
    if agent == "junie":
        return junie_processed(native)
    if agent == "copilot":
        return copilot_processed(native)
    return 0


def _record(
    agent: str, path: Path, native: dict[str, int], model: str | None, mtime: float
) -> dict:
    payload = {
        "id": _session_id(agent, path),
        "path": str(path),
        "mtime": mtime,
        "native": native,
        "processed_total": _processed_for(agent, native),
    }
    if model:
        payload["model"] = model
    return payload


def _stat_signature(path: Path) -> tuple[int, int, float]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_mtime


def scan_file(
    agent: str, path: Path, cached: dict | None
) -> tuple[dict | None, dict, bool]:
    """Return ``(record, cache_entry, scanned)``.

    Unchanged size+mtime reuses the sidecar. Codex re-reads a tail window when
    the file grew. Claude, junie, and copilot resume from the cached byte
    offset instead of reparsing the prefix.
    """

    try:
        size, mtime_ns, mtime = _stat_signature(path)
    except OSError:
        return None, cached or {}, False
    if (
        isinstance(cached, dict)
        and cached.get("parser") == PARSER_VERSION
        and cached.get("size") == size
        and cached.get("mtime_ns") == mtime_ns
        and isinstance(cached.get("record"), dict)
        and isinstance(cached.get("days"), list)
    ):
        return cached["record"], cached, False

    if agent == "codex":
        native, iso, model = _walk_codex_tail(path)
        if native is None:
            native = _blank_native(CODEX_KEYS)
            days: list[dict] = []
        else:
            days = _codex_day_rows(path, native, iso, model, mtime)
        record = _record(agent, path, native, model, mtime)
        entry = {
            "parser": PARSER_VERSION,
            "size": size,
            "mtime_ns": mtime_ns,
            "record": record,
            "days": days,
        }
        return record, entry, True

    if agent == "grok":
        document = _load_json(path)
        native, model = parse_grok_document(document)
        record = _record(agent, path, native, model, mtime)
        entry = {
            "parser": PARSER_VERSION,
            "size": size,
            "mtime_ns": mtime_ns,
            "record": record,
            "days": grok_day_rows(document, mtime),
        }
        return record, entry, True

    if agent == "claude":
        native = _blank_native(CLAUDE_KEYS)
        days = []
        offset = 0
        if (
            isinstance(cached, dict)
            and cached.get("parser") == PARSER_VERSION
            and isinstance(cached.get("native"), dict)
            and isinstance(cached.get("days"), list)
            and _as_int(cached.get("offset")) > 0
            and _as_int(cached.get("size")) < size
            and _as_int(cached.get("offset")) == _as_int(cached.get("size"))
        ):
            native = {key: _as_int(cached["native"].get(key)) for key in CLAUDE_KEYS}
            days = [dict(item) for item in cached["days"] if isinstance(item, dict)]
            offset = _as_int(cached.get("offset"))
        try:
            with path.open("rb") as handle:
                if offset:
                    handle.seek(offset)
                for raw in handle:
                    _claude_from_line(
                        raw.decode("utf-8", errors="replace"), native, days, mtime
                    )
                end = handle.tell()
        except OSError:
            return None, cached or {}, False
        record = _record(agent, path, native, None, mtime)
        entry = {
            "parser": PARSER_VERSION,
            "size": size,
            "mtime_ns": mtime_ns,
            "offset": end,
            "native": native,
            "days": days,
            "record": record,
        }
        return record, entry, True

    if agent == "junie":
        native = _blank_native(JUNIE_KEYS)
        days = []
        offset = 0
        if (
            isinstance(cached, dict)
            and cached.get("parser") == PARSER_VERSION
            and isinstance(cached.get("native"), dict)
            and isinstance(cached.get("days"), list)
            and _as_int(cached.get("offset")) > 0
            and _as_int(cached.get("size")) < size
            and _as_int(cached.get("offset")) == _as_int(cached.get("size"))
        ):
            native = {key: _as_int(cached["native"].get(key)) for key in JUNIE_KEYS}
            days = [dict(item) for item in cached["days"] if isinstance(item, dict)]
            offset = _as_int(cached.get("offset"))
        try:
            with path.open("rb") as handle:
                if offset:
                    handle.seek(offset)
                for raw in handle:
                    _junie_from_line(
                        raw.decode("utf-8", errors="replace"), native, days, mtime
                    )
                end = handle.tell()
        except OSError:
            return None, cached or {}, False
        record = _record(agent, path, native, None, mtime)
        entry = {
            "parser": PARSER_VERSION,
            "size": size,
            "mtime_ns": mtime_ns,
            "offset": end,
            "native": native,
            "days": days,
            "record": record,
        }
        return record, entry, True

    if agent == "copilot":
        state = _empty_copilot_state()
        offset = 0
        if (
            isinstance(cached, dict)
            and cached.get("parser") == PARSER_VERSION
            and isinstance(cached.get("state"), dict)
            and isinstance(cached.get("days"), list)
            and _as_int(cached.get("offset")) > 0
            and _as_int(cached.get("size")) < size
            and _as_int(cached.get("offset")) == _as_int(cached.get("size"))
        ):
            state = cached["state"]
            offset = _as_int(cached.get("offset"))
        try:
            with path.open("rb") as handle:
                if offset:
                    handle.seek(offset)
                for raw in handle:
                    _copilot_from_line(raw.decode("utf-8", errors="replace"), state)
                end = handle.tell()
        except OSError:
            return None, cached or {}, False
        native = _copilot_native_from_state(state)
        record = _record(agent, path, native, None, mtime)
        entry = {
            "parser": PARSER_VERSION,
            "size": size,
            "mtime_ns": mtime_ns,
            "offset": end,
            "state": state,
            "days": _copilot_day_rows(state, mtime),
            "record": record,
        }
        return record, entry, True

    return None, cached or {}, False


def _sum_records(agent: str, records: list[dict]) -> tuple[dict[str, int], int]:
    keys = {
        "codex": CODEX_KEYS,
        "claude": CLAUDE_KEYS,
        "grok": GROK_KEYS,
        "junie": JUNIE_KEYS,
        "copilot": COPILOT_KEYS,
    }[agent]
    native = _blank_native(keys)
    processed = 0
    for record in records:
        record_native = record.get("native")
        if isinstance(record_native, dict):
            _add_native(native, {key: _as_int(record_native.get(key)) for key in keys})
        processed += _as_int(record.get("processed_total"))
    return native, processed


def build_snapshot(
    agent: str,
    *,
    status: str,
    reason: str | None,
    last: dict | None,
    fleet_processed: int | None,
    session_count: int | None,
    fleet_native: dict | None = None,
) -> dict:
    semantics = cache_semantics(agent)
    if status == "unavailable":
        metrics = None
    elif last is None:
        metrics = {
            "native": {},
            "processed_total": 0,
            "fleet_processed_total": fleet_processed,
            "session_count": session_count,
        }
    else:
        metrics = {
            "native": last.get("native") or {},
            "processed_total": _as_int(last.get("processed_total")),
            "fleet_processed_total": fleet_processed,
            "session_count": session_count,
        }
    quota = {"status": status}
    if reason:
        quota["reason"] = reason
    if fleet_native is not None and status == "ok":
        quota["fleet_native"] = fleet_native
    snapshot = {
        "agent": agent,
        "status": status,
        "reason": reason,
        "cache_semantics": semantics,
        "generated_at": _now_iso(),
        "last_session": last,
        "metrics": metrics,
        "quota": quota,
    }
    return snapshot


def render_statusline(snapshot: dict) -> str:
    agent = str(snapshot.get("agent") or "agent")
    status = snapshot.get("status")
    if status == "unavailable":
        reason = snapshot.get("reason") or CURSOR_REASON
        return f"{agent}  ·  unavailable  ·  {reason}"
    if status != "ok" or not isinstance(snapshot.get("last_session"), dict):
        return f"{agent}  ·  no local sessions"
    last = snapshot["last_session"]
    session_id = str(last.get("id") or "")
    short = session_id[:8] if session_id else "session"
    processed = _as_int((snapshot.get("metrics") or {}).get("processed_total"))
    parts = [agent, short, f"{fmt_tokens(processed)} toks"]
    fleet = (snapshot.get("metrics") or {}).get("fleet_processed_total")
    if isinstance(fleet, int):
        parts.append(f"fleet {fmt_tokens(fleet)}")
    return "  ·  ".join(parts)


def render_sessions(agent: str, records: list[dict], snapshot: dict) -> str:
    ordered = sorted(
        records, key=lambda item: float(item.get("mtime") or 0), reverse=True
    )
    lines = [
        f"{'MODIFIED':<20} {'PROCESSED':>14}  SESSION",
        "-" * 64,
    ]
    for record in ordered:
        stamp = datetime.fromtimestamp(float(record.get("mtime") or 0), tz=timezone.utc)
        when = stamp.astimezone().strftime("%Y-%m-%d %H:%M")
        processed = fmt_tokens(_as_int(record.get("processed_total")))
        lines.append(f"{when:<20} {processed:>14}  {record.get('id')}")
    metrics = snapshot.get("metrics") or {}
    lines.append(f"sessions {len(records)}")
    lines.append(f"processed_total {_as_int(metrics.get('fleet_processed_total'))}")
    lines.append(f"cache_semantics {snapshot.get('cache_semantics')}")
    lines.append(f"live_total {_as_int(snapshot.get('live_total'))}")
    lines.append(f"archived_total {_as_int(snapshot.get('archived_total'))}")
    lines.append(f"agent {agent}")
    return "\n".join(lines)


def _apply_aggregate(
    snapshot: dict, aggregate: dict | None, last_mtime: float | None
) -> dict:
    if not isinstance(aggregate, dict) or not aggregate.get("complete"):
        return snapshot
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict):
        return snapshot
    metrics["fleet_processed_total"] = _as_int(aggregate.get("processed_total"))
    metrics["session_count"] = _as_int(aggregate.get("session_count"))
    newest = aggregate.get("newest_mtime")
    if (
        last_mtime is not None
        and isinstance(newest, (int, float))
        and last_mtime > float(newest)
    ):
        snapshot["quota"]["fleet_stale"] = True
    fleet_native = aggregate.get("native")
    if isinstance(fleet_native, dict):
        snapshot["quota"]["fleet_native"] = fleet_native
    return snapshot


def collect(
    agent: str,
    store: Path,
    *,
    mode: str,
    cache: dict,
) -> tuple[list[dict], dict | None, dict]:
    files = discover_files(agent, store)
    file_cache = cache.get("files")
    if not isinstance(file_cache, dict):
        file_cache = {}
        cache["files"] = file_cache

    signed: list[tuple[float, Path]] = []
    for path in files:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        signed.append((mtime, path))
    # In-memory only. _write_cache drops this before the sidecar is written.
    cache["__discovered"] = [str(path) for _mtime, path in signed]
    if not signed:
        cache.pop("aggregate", None)
        return [], None, cache

    if mode == "once":
        signed.sort(key=lambda item: item[0], reverse=True)
        mtime, path = signed[0]
        key = str(path)
        record, entry, _scanned = scan_file(
            agent,
            path,
            file_cache.get(key) if isinstance(file_cache.get(key), dict) else None,
        )
        if record is not None:
            file_cache[key] = entry
            record["mtime"] = mtime
        records = [record] if record is not None else []
        return records, records[0] if records else None, cache

    records: list[dict] = []
    for mtime, path in signed:
        key = str(path)
        record, entry, _scanned = scan_file(
            agent,
            path,
            file_cache.get(key) if isinstance(file_cache.get(key), dict) else None,
        )
        if record is None:
            continue
        record["mtime"] = mtime
        file_cache[key] = entry
        records.append(record)
    # Missing paths stay in the sidecar. Dropping them would let the next
    # last-wins row replace a shared day with only the files still on disk.
    last = (
        max(records, key=lambda item: float(item.get("mtime") or 0))
        if records
        else None
    )
    native, processed = _sum_records(agent, records)
    newest = max((float(item.get("mtime") or 0) for item in records), default=0)
    cache["aggregate"] = {
        "complete": True,
        "processed_total": processed,
        "session_count": len(records),
        "native": native,
        "generated_at": _now_iso(),
        "newest_mtime": newest,
    }
    return records, last, cache


def snapshot_for(
    agent: str,
    store: Path,
    *,
    mode: str,
    cache: dict,
) -> tuple[dict, list[dict], dict]:
    if agent == "cursor":
        snapshot = build_snapshot(
            agent,
            status="unavailable",
            reason=CURSOR_REASON,
            last=None,
            fleet_processed=None,
            session_count=None,
        )
        snapshot["metrics"] = None
        return snapshot, [], cache

    records, last, cache = collect(
        agent, store, mode="sessions" if mode == "sessions" else "once", cache=cache
    )
    if mode == "sessions":
        aggregate = (
            cache.get("aggregate") if isinstance(cache.get("aggregate"), dict) else None
        )
        fleet_processed = _as_int(aggregate.get("processed_total")) if aggregate else 0
        session_count = _as_int(aggregate.get("session_count")) if aggregate else 0
        fleet_native = aggregate.get("native") if aggregate else None
        status = "ok" if records else "empty"
        reason = None if records else "no local sessions"
        snapshot = build_snapshot(
            agent,
            status=status,
            reason=reason,
            last=last,
            fleet_processed=fleet_processed if records else 0,
            session_count=session_count,
            fleet_native=fleet_native if isinstance(fleet_native, dict) else None,
        )
        return snapshot, records, cache

    status = "ok" if last else "empty"
    reason = None if last else "no local sessions"
    snapshot = build_snapshot(
        agent,
        status=status,
        reason=reason,
        last=last,
        fleet_processed=None,
        session_count=None,
    )
    last_mtime = float(last["mtime"]) if last else None
    aggregate = (
        cache.get("aggregate") if isinstance(cache.get("aggregate"), dict) else None
    )
    snapshot = _apply_aggregate(snapshot, aggregate, last_mtime)
    return snapshot, records, cache


def _cache_path(agent: str, quota_root: Path) -> Path:
    return quota_root / "scan-cache.json"


def _quota_path(quota_root: Path) -> Path:
    return quota_root / "quota.json"


def _write_cache(agent: str, quota_root: Path, cache: dict) -> None:
    """Persist the scan sidecar without the discovered-path list."""

    discovered = cache.pop("__discovered", None)
    atomic_write_json(_cache_path(agent, quota_root), cache)
    if discovered is not None:
        cache["__discovered"] = discovered


def _file_cache(cache: dict) -> dict:
    files = cache.get("files")
    if not isinstance(files, dict):
        files = {}
        cache["files"] = files
    return files


def _discovered_paths(cache: dict) -> list[Path]:
    raw = cache.get("__discovered")
    if not isinstance(raw, list):
        return []
    return [Path(item) for item in raw if isinstance(item, str)]


def _entry_current(entry: object, path: Path) -> bool:
    """True when the sidecar already holds this file's day buckets."""

    if not isinstance(entry, dict):
        return False
    if entry.get("parser") != PARSER_VERSION:
        return False
    if not isinstance(entry.get("days"), list) or not isinstance(
        entry.get("record"), dict
    ):
        return False
    try:
        size, mtime_ns, _mtime = _stat_signature(path)
    except OSError:
        return False
    return entry.get("size") == size and entry.get("mtime_ns") == mtime_ns


def _has_day_entry(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("parser") == PARSER_VERSION
        and isinstance(entry.get("days"), list)
        and isinstance(entry.get("record"), dict)
    )


def _day_rows_of(entry: object) -> list[dict]:
    days = entry.get("days") if isinstance(entry, dict) else None
    if not isinstance(days, list):
        return []
    return [item for item in days if isinstance(item, dict)]


def _rows_for_paths(files: dict, paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        rows.extend(_day_rows_of(files.get(str(path))))
    return rows


def _release_moved_ghosts(files: dict, paths: list[Path]) -> None:
    """Drop a retained path when that session id is still live under a new path."""

    live = {str(path) for path in paths}
    live_ids: set[str] = set()
    for path in paths:
        entry = files.get(str(path))
        record = entry.get("record") if isinstance(entry, dict) else None
        session_id = record.get("id") if isinstance(record, dict) else None
        if isinstance(session_id, str) and session_id:
            live_ids.add(session_id)
    for key in list(files):
        if key in live or not isinstance(files.get(key), dict):
            continue
        record = files[key].get("record")
        session_id = record.get("id") if isinstance(record, dict) else None
        if isinstance(session_id, str) and session_id in live_ids:
            files.pop(key, None)


def _retained_rows(files: dict, paths: list[Path]) -> list[dict]:
    live = {str(path) for path in paths}
    rows: list[dict] = []
    for key, entry in files.items():
        if key in live:
            continue
        rows.extend(_day_rows_of(entry))
    return rows


def _processed_for_paths(files: dict, paths: list[Path]) -> int:
    total = 0
    for path in paths:
        entry = files.get(str(path))
        record = entry.get("record") if isinstance(entry, dict) else None
        if isinstance(record, dict):
            total += _as_int(record.get("processed_total"))
    return total


def _coverage_current(files: dict, paths: list[Path]) -> bool:
    return all(_entry_current(files.get(str(path)), path) for path in paths)


def _refresh_stale(agent: str, files: dict, paths: list[Path]) -> None:
    """Re-read only files whose size or mtime moved. Cache hits do not open."""

    for path in paths:
        key = str(path)
        entry = files.get(key)
        if _entry_current(entry, path):
            continue
        record, new_entry, _scanned = scan_file(
            agent, path, entry if isinstance(entry, dict) else None
        )
        if record is not None and isinstance(new_entry, dict):
            files[key] = new_entry


def annotate_archive(
    agent: str,
    snapshot: dict,
    records: list[dict],
    cache: dict,
    quota_root: Path,
    *,
    mode: str,
) -> dict:
    """Attach ``live_total`` / ``archived_total`` and upsert complete days.

    A day is published only when every live file has a current day bucket.
    Last-wins would otherwise replace a full day with the one file ``once``
    just scanned. Rows from paths that disappeared stay in the sidecar and
    are summed again, so a shared day cannot shrink to the surviving files.
    ``once`` and ``line`` never open an unchanged file. A daemon tick refreshes
    signature mismatches only after a full ``archive``/``sessions`` pass has
    marked ``day_coverage``; it does not backfill an empty or pre-v3 sidecar.
    """

    if agent == "cursor" or cache_semantics(agent) is None:
        snapshot["live_total"] = None
        snapshot["archived_total"] = None
        snapshot["live_scope"] = "unavailable"
        return snapshot

    archive_path = usage_archive.archive_file(agent, quota_root)
    paths = _discovered_paths(cache)
    files = _file_cache(cache)
    if mode == "daemon" and paths and cache.get("day_coverage") == "store":
        _refresh_stale(agent, files, paths)

    covered = _coverage_current(files, paths) if paths else True
    publish = False
    if (
        mode in {"archive", "sessions"}
        and covered
        or mode == "daemon"
        and cache.get("day_coverage") == "store"
        and covered
        or mode in {"once", "line"}
        and paths
        and covered
        and cache.get("day_coverage") == "store"
    ):
        publish = True

    appended = 0
    if publish:
        _release_moved_ghosts(files, paths)
        appended = usage_archive.append_days(
            archive_path,
            [*_rows_for_paths(files, paths), *_retained_rows(files, paths)],
        )
        if mode in {"archive", "sessions"}:
            cache["day_coverage"] = "store"

    if not paths:
        live: int | None = 0
        scope = "store"
    elif covered:
        live = _processed_for_paths(files, paths)
        scope = "store"
    else:
        live = sum(_as_int(record.get("processed_total")) for record in records)
        scope = "scanned"

    archived = (
        usage_archive.archived_total(archive_path) if archive_path.is_file() else 0
    )
    snapshot["live_total"] = live
    snapshot["archived_total"] = archived
    snapshot["live_scope"] = scope
    snapshot["archive_appended"] = appended
    snapshot["archive_path"] = str(archive_path)
    metrics = snapshot.get("metrics")
    if scope == "store" and isinstance(metrics, dict):
        metrics["fleet_processed_total"] = live
        metrics["session_count"] = sum(
            1
            for path in paths
            if isinstance(files.get(str(path)), dict)
            and isinstance(files[str(path)].get("record"), dict)
        )
    return snapshot


def render_archive_summary(snapshot: dict) -> str:
    if (
        snapshot.get("status") == "unavailable"
        or snapshot.get("live_scope") == "unavailable"
    ):
        payload = {
            "agent": snapshot.get("agent"),
            "status": "unavailable",
            "reason": snapshot.get("reason"),
            "live_total": None,
            "archived_total": None,
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
    payload = {
        "agent": snapshot.get("agent"),
        "status": snapshot.get("status"),
        "live_total": snapshot.get("live_total"),
        "archived_total": snapshot.get("archived_total"),
        "appended": snapshot.get("archive_appended"),
        "archive": snapshot.get("archive_path"),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def run(
    agent: str, mode: str, store: Path, quota_root: Path, interval: int = 30
) -> int:
    if agent not in FLEET_AGENTS:
        print(f"unknown fleet agent: {agent}", file=sys.stderr)
        return 2
    cache = _load_json(_cache_path(agent, quota_root))
    if mode == "archive" and agent == "cursor":
        snapshot = build_snapshot(
            agent,
            status="unavailable",
            reason=CURSOR_REASON,
            last=None,
            fleet_processed=None,
            session_count=None,
        )
        snapshot["metrics"] = None
        snapshot = annotate_archive(
            agent, snapshot, [], cache, quota_root, mode="archive"
        )
        print(render_archive_summary(snapshot))
        return 0

    if mode in {"sessions", "archive"}:
        snapshot, records, cache = snapshot_for(
            agent, store, mode="sessions", cache=cache
        )
        snapshot = annotate_archive(
            agent, snapshot, records, cache, quota_root, mode=mode
        )
        _write_cache(agent, quota_root, cache)
        atomic_write_json(_quota_path(quota_root), snapshot)
        if mode == "archive":
            print(render_archive_summary(snapshot))
            return 0
        if agent == "cursor":
            print(render_statusline(snapshot))
            return 0
        print(render_sessions(agent, records, snapshot))
        return 0

    if mode == "daemon":
        return _daemon(agent, store, quota_root, interval)

    snapshot, records, cache = snapshot_for(agent, store, mode="once", cache=cache)
    snapshot = annotate_archive(agent, snapshot, records, cache, quota_root, mode=mode)
    _write_cache(agent, quota_root, cache)
    atomic_write_json(_quota_path(quota_root), snapshot)
    if mode == "line":
        print(render_statusline(snapshot))
        return 0
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    return 0


def _daemon(agent: str, store: Path, quota_root: Path, interval: int) -> int:
    stop = False

    def _sig(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    print(
        f"[{agent}-monitor] daemon refreshing local quota every {interval}s -> {_quota_path(quota_root)}",
        flush=True,
    )
    while not stop:
        cache = _load_json(_cache_path(agent, quota_root))
        snapshot, records, cache = snapshot_for(agent, store, mode="once", cache=cache)
        snapshot = annotate_archive(
            agent, snapshot, records, cache, quota_root, mode="daemon"
        )
        _write_cache(agent, quota_root, cache)
        atomic_write_json(_quota_path(quota_root), snapshot)
        print(
            f"[{agent}-monitor] {snapshot['generated_at']} {render_statusline(snapshot)}",
            flush=True,
        )
        for _ in range(max(interval, 1) * 4):
            if stop:
                break
            time.sleep(0.25)
    return 0


def main(agent: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{agent} local token engine")
    parser.add_argument(
        "mode",
        nargs="?",
        default="line",
        choices=["line", "once", "sessions", "daemon", "archive"],
    )
    parser.add_argument("--store", help="Agent store root (default: the host store)")
    parser.add_argument(
        "--quota-dir",
        help="Projection directory (default: $VIBECRAFTED_HOME/telemetry/<agent>)",
    )
    parser.add_argument("--interval", type=int, default=30)
    args, _unknown = parser.parse_known_args(argv)
    store = Path(args.store).expanduser() if args.store else default_store(agent)
    root = quota_dir(agent, args.quota_dir)
    return run(agent, args.mode, store, root, interval=args.interval)


# Imported by tests that want the line renderer without spawning a daemon.
__all__ = [
    "CURSOR_REASON",
    "FLEET_AGENTS",
    "TAIL_BYTES",
    "build_snapshot",
    "cache_semantics",
    "copilot_processed",
    "fmt_tokens",
    "main",
    "parse_codex_tail",
    "parse_grok_document",
    "render_statusline",
    "run",
    "scan_file",
]
