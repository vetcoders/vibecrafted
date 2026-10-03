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
- cursor has no local token ledger.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path

TAIL_BYTES = 400 * 1024
# Codex usage is cumulative and usually sits near EOF. A fixed 400 KB window
# misses it when a later oversized event (tool output) pushes the last
# token_count further back. Walk backward in chunks and stop at the cap.
CODEX_TAIL_CAP = 32 * 1024 * 1024
PARSER_VERSION = 2

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


def _codex_native_from_line(line: bytes) -> dict[str, int] | None:
    if b"token_count" not in line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    payload = obj.get("payload")
    payload_type = payload.get("type") if isinstance(payload, dict) else None
    if obj.get("type") != "token_count" and payload_type != "token_count":
        return None
    info = payload.get("info") if isinstance(payload, dict) else None
    usage = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(usage, dict):
        return None
    native = _blank_native(CODEX_KEYS)
    for key in CODEX_KEYS:
        native[key] = _as_int(usage.get(key))
    if native["total_tokens"] <= 0:
        native["total_tokens"] = native["input_tokens"] + native["output_tokens"]
    return native


def last_codex_native(
    path: Path, cap: int = CODEX_TAIL_CAP, chunk: int = TAIL_BYTES
) -> dict[str, int] | None:
    """Newest cumulative usage within ``cap`` bytes of EOF. Read-only."""

    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    remaining = min(size, cap)
    offset = size
    carry = b""
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
                native = _codex_native_from_line(line)
                if native is not None:
                    return native
    if carry:
        return _codex_native_from_line(carry)
    return None


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


def _claude_from_line(line: str, native: dict[str, int]) -> bool:
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


def _junie_from_line(line: str, native: dict[str, int]) -> bool:
    if "inputTokens" not in line:
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    hit = False
    for usage in _iter_junie_usages(obj):
        hit = True
        for key in JUNIE_KEYS:
            native[key] += _as_int(usage.get(key))
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
            return True
        return False
    if kind == "session.compaction_complete" and not state.get("saw_shutdown"):
        blob = data.get("compactionTokensUsed")
        if isinstance(blob, dict):
            fallback = state["fallback"]
            for key in COPILOT_KEYS:
                fallback[key] += _as_int(blob.get(key))
            return True
    return False


def copilot_processed(native: dict[str, int]) -> int:
    return native.get("inputTokens", 0) + native.get("outputTokens", 0)


def _empty_copilot_state() -> dict:
    return {
        "saw_shutdown": False,
        "shutdown": None,
        "fallback": _blank_native(COPILOT_KEYS),
    }


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
    ):
        return cached["record"], cached, False

    if agent == "codex":
        native = last_codex_native(path)
        if native is None:
            native = _blank_native(CODEX_KEYS)
        record = _record(agent, path, native, None, mtime)
        entry = {
            "parser": PARSER_VERSION,
            "size": size,
            "mtime_ns": mtime_ns,
            "record": record,
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
        }
        return record, entry, True

    if agent == "claude":
        native = _blank_native(CLAUDE_KEYS)
        offset = 0
        if (
            isinstance(cached, dict)
            and cached.get("parser") == PARSER_VERSION
            and isinstance(cached.get("native"), dict)
            and _as_int(cached.get("offset")) > 0
            and _as_int(cached.get("size")) < size
            and _as_int(cached.get("offset")) == _as_int(cached.get("size"))
        ):
            native = {key: _as_int(cached["native"].get(key)) for key in CLAUDE_KEYS}
            offset = _as_int(cached.get("offset"))
        try:
            with path.open("rb") as handle:
                if offset:
                    handle.seek(offset)
                for raw in handle:
                    _claude_from_line(raw.decode("utf-8", errors="replace"), native)
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
            "record": record,
        }
        return record, entry, True

    if agent == "junie":
        native = _blank_native(JUNIE_KEYS)
        offset = 0
        if (
            isinstance(cached, dict)
            and cached.get("parser") == PARSER_VERSION
            and isinstance(cached.get("native"), dict)
            and _as_int(cached.get("offset")) > 0
            and _as_int(cached.get("size")) < size
            and _as_int(cached.get("offset")) == _as_int(cached.get("size"))
        ):
            native = {key: _as_int(cached["native"].get(key)) for key in JUNIE_KEYS}
            offset = _as_int(cached.get("offset"))
        try:
            with path.open("rb") as handle:
                if offset:
                    handle.seek(offset)
                for raw in handle:
                    _junie_from_line(raw.decode("utf-8", errors="replace"), native)
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
    seen: set[str] = set()
    for mtime, path in signed:
        key = str(path)
        seen.add(key)
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
    for key in list(file_cache):
        if key not in seen:
            file_cache.pop(key, None)
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


def run(
    agent: str, mode: str, store: Path, quota_root: Path, interval: int = 30
) -> int:
    if agent not in FLEET_AGENTS:
        print(f"unknown fleet agent: {agent}", file=sys.stderr)
        return 2
    cache = _load_json(_cache_path(agent, quota_root))
    if mode == "sessions":
        snapshot, records, cache = snapshot_for(
            agent, store, mode="sessions", cache=cache
        )
        atomic_write_json(_cache_path(agent, quota_root), cache)
        atomic_write_json(_quota_path(quota_root), snapshot)
        if agent == "cursor":
            print(render_statusline(snapshot))
            return 0
        print(render_sessions(agent, records, snapshot))
        return 0

    if mode == "daemon":
        return _daemon(agent, store, quota_root, interval)

    snapshot, _records, cache = snapshot_for(agent, store, mode="once", cache=cache)
    atomic_write_json(_cache_path(agent, quota_root), cache)
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
        snapshot, _records, cache = snapshot_for(agent, store, mode="once", cache=cache)
        atomic_write_json(_cache_path(agent, quota_root), cache)
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
        choices=["line", "once", "sessions", "daemon"],
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
