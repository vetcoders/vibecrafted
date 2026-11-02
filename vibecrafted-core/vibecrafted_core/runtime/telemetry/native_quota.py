"""Allowlisted native rate-limit observations, separate from token accounting.

The existing fleet engine owns persistence and scheduling. This adapter reads
bounded local Codex rollout tails and reuses that engine's per-file scan cache.
It never reads credentials, calls a provider, estimates capacity from tokens or
promotes collection time to the time a native limit was observed.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

PARSER_VERSION = 1
MAX_FILES = 8
MAX_LIMITS = 16
MAX_RECORD_BYTES = 16 * 1024
MAX_TAIL_BYTES = 512 * 1024
STALE_SECONDS = 300
SOURCE = "codex.native.token_count.rate_limits"


def _timestamp(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.timestamp() if stamp.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not 0 < len(value) <= 128:
        return None
    return value if all(char.isprintable() for char in value) else None


def _scope_key(scope: dict) -> tuple:
    return tuple(
        _text(scope.get(key))
        for key in ("pool", "account", "model", "limit_id", "plan_type")
    )


def _event(line: bytes, now: float) -> dict | None:
    if (
        len(line) > MAX_RECORD_BYTES
        or b'"rate_limits"' not in line
        or b'"token_count"' not in line
    ):
        return None
    try:
        event = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(event, dict):
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict) or (
        event.get("type") != "token_count" and payload.get("type") != "token_count"
    ):
        return None
    limits = payload.get("rate_limits")
    observed_at = event.get("timestamp")
    observed_ts = _timestamp(observed_at)
    if (
        not isinstance(limits, dict)
        or observed_ts is None
        or observed_ts > now + STALE_SECONDS
    ):
        return None
    scope = {"pool": "openai"}
    for key in ("limit_id", "plan_type"):
        value = _text(limits.get(key))
        if limits.get(key) is not None and value is None:
            return None
        if value is not None:
            scope[key] = value
    for native_key, key in (("account_id", "account"), ("model", "model")):
        value = _text(limits.get(native_key))
        if limits.get(native_key) is not None and value is None:
            return None
        if value is not None:
            scope[key] = value
    # No account/model is inferred from a conversation's model or token totals.
    windows = []
    for key in ("primary", "secondary"):
        native = limits.get(key)
        if not isinstance(native, dict):
            continue
        used = _number(native.get("used_percent"))
        duration = _number(native.get("window_minutes"))
        reset = _number(native.get("resets_at"))
        if (
            used is None
            or not 0 <= used <= 100
            or duration is None
            or duration <= 0
            or not duration.is_integer()
            or reset is None
            or reset <= 0
            or not reset.is_integer()
        ):
            continue
        windows.append(
            {
                "id": key,
                "used_ratio": used / 100,
                "remaining_ratio": (100 - used) / 100,
                "window_minutes": int(duration),
                "resets_at": int(reset),
                "observed_at": observed_at,
                "source": SOURCE,
                "scope": dict(scope),
            }
        )
    return {"observed_at": observed_at, "scope": scope, "windows": windows}


def _read_limits(path: Path, now: float) -> list[dict]:
    with path.open("rb") as stream:
        size = path.stat().st_size
        start = max(0, size - MAX_TAIL_BYTES)
        stream.seek(start)
        raw = stream.read(MAX_TAIL_BYTES)
    lines = raw.splitlines(keepends=True)
    if start and lines:
        start += len(lines[0])
        lines = lines[1:]  # The first line can be a cut private payload.
    latest = {}
    for line in lines:
        offset = start
        start += len(line)
        if not line.endswith(b"\n"):
            continue  # A partially written EOF is not a native observation yet.
        observation = _event(line, now)
        if observation is None:
            continue
        observation["source_event"] = {
            "offset": offset,
            "length": len(line),
            "sha256": hashlib.sha256(line).hexdigest(),
        }
        key = _scope_key(observation["scope"])
        previous = latest.get(key)
        if previous is None or _timestamp(observation["observed_at"]) >= _timestamp(
            previous["observed_at"]
        ):
            latest[key] = observation
    return list(latest.values())


def _retained_limits(path: Path, cached: dict, stat) -> list[dict]:
    old = cached.get("signature")
    limits = cached.get("limits")
    if not isinstance(old, dict) or not isinstance(limits, list):
        return []
    if (
        old.get("device") != stat.st_dev
        or old.get("inode") != stat.st_ino
        or not isinstance(old.get("size"), int)
        or old["size"] > stat.st_size
    ):
        return []
    verified = []
    try:
        with path.open("rb") as stream:
            for observation in limits[:MAX_LIMITS]:
                proof = (
                    observation.get("source_event")
                    if isinstance(observation, dict)
                    else None
                )
                if not isinstance(proof, dict):
                    continue
                offset, length = proof.get("offset"), proof.get("length")
                if (
                    isinstance(offset, bool)
                    or not isinstance(offset, int)
                    or offset < 0
                    or isinstance(length, bool)
                    or not isinstance(length, int)
                    or not 0 < length <= MAX_RECORD_BYTES
                ):
                    continue
                stream.seek(offset)
                if hashlib.sha256(stream.read(length)).hexdigest() == proof.get(
                    "sha256"
                ):
                    verified.append(observation)
    except OSError:
        pass
    return verified


def _project_window(raw: object, now: float) -> dict | None:
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("scope"), dict)
        or raw.get("source") != SOURCE
    ):
        return None
    ratio, duration, reset = (
        _number(raw.get(key)) for key in ("used_ratio", "window_minutes", "resets_at")
    )
    stamp = _timestamp(raw.get("observed_at"))
    scope = {
        key: value
        for key in ("pool", "account", "model", "limit_id", "plan_type")
        if (value := _text(raw["scope"].get(key))) is not None
    }
    if (
        raw.get("id") not in {"primary", "secondary"}
        or scope.get("pool") != "openai"
        or ratio is None
        or not 0 <= ratio <= 1
        or duration is None
        or duration <= 0
        or not duration.is_integer()
        or reset is None
        or reset <= 0
        or not reset.is_integer()
        or stamp is None
        or stamp > now + STALE_SECONDS
    ):
        return None
    return {
        "id": raw["id"],
        "used_ratio": ratio,
        "remaining_ratio": 1 - ratio,
        "window_minutes": int(duration),
        "resets_at": int(reset),
        "observed_at": raw["observed_at"],
        "source": SOURCE,
        "scope": scope,
        "age_s": max(0, int(now - stamp)),
        "stale": now - stamp > STALE_SECONDS or reset <= now,
    }


def collect_codex(
    paths: list[Path], file_cache: dict, *, now: float | None = None
) -> dict:
    """Normalize at most eight tails; unchanged signatures do not reopen files."""
    now = datetime.now(UTC).timestamp() if now is None else now
    candidates = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        candidates.append((stat.st_mtime_ns, path, stat))
    latest = {}
    for _mtime, path, stat in sorted(
        candidates, key=lambda item: item[0], reverse=True
    )[:MAX_FILES]:
        key = str(path)
        entry = file_cache.setdefault(key, {})
        if not isinstance(entry, dict):
            entry = file_cache[key] = {}
        signature = {
            "parser": PARSER_VERSION,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "device": stat.st_dev,
            "inode": stat.st_ino,
        }
        cached = entry.get("native_quota")
        if (
            isinstance(cached, dict)
            and cached.get("signature") == signature
            and isinstance(cached.get("limits"), list)
        ):
            limits = cached["limits"]
        else:
            try:
                limits = _read_limits(path, now)
            except OSError:
                limits = []
            # Appended non-quota output can move the last observation outside
            # the bounded tail. Retain the known observation at its original
            # timestamp, never after truncate/replacement or a new native event.
            if not limits and isinstance(cached, dict):
                limits = _retained_limits(path, cached, stat)
            entry["native_quota"] = {"signature": signature, "limits": limits}
        for observation in limits:
            if not isinstance(observation, dict) or not isinstance(
                observation.get("scope"), dict
            ):
                continue
            stamp = _timestamp(observation.get("observed_at"))
            if stamp is None or stamp > now + STALE_SECONDS:
                continue
            scope = observation["scope"]
            if scope.get("pool") != "openai":
                continue
            group = _scope_key(scope)
            previous = latest.get(group)
            if previous is None or stamp > _timestamp(previous["observed_at"]):
                latest[group] = observation
    windows = []
    ordered = sorted(
        latest.values(), key=lambda item: _timestamp(item["observed_at"]), reverse=True
    )
    # Explicit native account changes select the newest observed local account;
    # no old account's window is relabelled as the new account's capacity.
    current_account = ordered[0]["scope"].get("account") if ordered else None
    for observation in ordered[:MAX_LIMITS]:
        if observation["scope"].get("account") != current_account:
            continue
        raw_windows = observation.get("windows")
        if not isinstance(raw_windows, list):
            continue
        for raw in raw_windows[:2]:
            window = _project_window(raw, now)
            if window is not None and _scope_key(window["scope"]) == _scope_key(
                observation["scope"]
            ):
                windows.append(window)
    if not windows:
        return {
            "status": "unavailable",
            "reason": "No native rate-limit window found in bounded local Codex observations",
            "windows": [],
            "source": SOURCE,
            "provenance": "native_local_rate_limits",
            "scope": {"pool": "openai"},
        }
    scope = (
        dict(windows[0]["scope"])
        if all(window["scope"] == windows[0]["scope"] for window in windows)
        else {"pool": "openai"}
    )
    if current_account is not None:
        scope["account"] = current_account
    return {
        "status": "stale" if all(window["stale"] for window in windows) else "observed",
        "windows": windows,
        "observed_at": max(
            windows, key=lambda window: _timestamp(window["observed_at"])
        )["observed_at"],
        "source": SOURCE,
        "provenance": "native_local_rate_limits",
        "scope": scope,
    }
