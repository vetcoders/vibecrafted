"""Append-only daily usage archive.

One JSON object per line under
``$VIBECRAFTED_HOME/telemetry/archive/<agent>.jsonl``. Reading is last-wins
per ``(day, model)``: a later line replaces the logical row and never
rewrites or deletes earlier lines. Days that a later run does not emit stay
as they were, which is how a rotated source file keeps its totals.

The file is not compacted in this cut.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
)


def empty_bucket() -> dict[str, int]:
    return {field: 0 for field in TOKEN_FIELDS}


def bucket_total(bucket: dict) -> int:
    return sum(_as_int(bucket.get(field)) for field in TOKEN_FIELDS)


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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def vibecrafted_home() -> Path:
    base = os.environ.get("VIBECRAFTED_HOME")
    if base:
        return Path(base).expanduser()
    return Path(os.environ.get("HOME") or str(Path.home())) / ".vibecrafted"


def archive_file(agent: str, quota_root: Path | None = None) -> Path:
    """Archive path next to the fleet quota directory.

    Default quota root is ``$VIBECRAFTED_HOME/telemetry/<agent>``, so the
    archive is ``$VIBECRAFTED_HOME/telemetry/archive/<agent>.jsonl``. A test
    that passes its own quota directory lands the archive beside that
    directory instead of the host ledger.
    """

    if "/" in agent or "\\" in agent or not agent or agent.startswith("."):
        raise ValueError(f"refusing archive name: {agent}")
    if quota_root is not None:
        return quota_root.parent / "archive" / f"{agent}.jsonl"
    return vibecrafted_home() / "telemetry" / "archive" / f"{agent}.jsonl"


def merge_rows(rows: Iterable[dict]) -> list[dict]:
    """Sum partial observations into one bucket per ``(day, model)``."""

    merged: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = row.get("day")
        if not isinstance(day, str) or len(day) != 10 or day[4] != "-" or day[7] != "-":
            continue
        model = row.get("model")
        model_name = (
            model.strip() if isinstance(model, str) and model.strip() else "unknown"
        )
        key = (day, model_name)
        bucket = merged.get(key)
        if bucket is None:
            bucket = empty_bucket()
            merged[key] = bucket
        for field in TOKEN_FIELDS:
            bucket[field] += _as_int(row.get(field))
    emitted: list[dict] = []
    for (day, model_name), bucket in sorted(merged.items()):
        if bucket_total(bucket) <= 0:
            continue
        emitted.append({"day": day, "model": model_name, **bucket})
    return emitted


def read_last_wins(path: Path) -> dict[tuple[str, str], dict]:
    found: dict[tuple[str, str], dict] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return found
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        day = row.get("day")
        model = row.get("model")
        if not isinstance(day, str) or not isinstance(model, str):
            continue
        found[(day, model)] = row
    return found


def archived_total(path: Path) -> int:
    return sum(
        _as_int(row.get("total_tokens")) for row in read_last_wins(path).values()
    )


def append_days(
    path: Path, rows: Iterable[dict], *, observed_at: str | None = None
) -> int:
    """Append rows whose token buckets differ from the current last-wins row.

    Identical buckets are not appended: last-wins would not change, and a
    daemon tick must not grow the file. A changed bucket is a new line.
    The previous line stays on disk.
    """

    pending = merge_rows(rows)
    if not pending:
        return 0
    current = read_last_wins(path)
    stamp = observed_at or _now_iso()
    lines: list[str] = []
    for row in pending:
        payload = {
            "day": row["day"],
            "model": row["model"],
            "input_tokens": _as_int(row.get("input_tokens")),
            "output_tokens": _as_int(row.get("output_tokens")),
            "cache_creation_tokens": _as_int(row.get("cache_creation_tokens")),
            "cache_read_tokens": _as_int(row.get("cache_read_tokens")),
        }
        payload["total_tokens"] = bucket_total(payload)
        key = (payload["day"], payload["model"])
        previous = current.get(key)
        if isinstance(previous, dict) and all(
            _as_int(previous.get(field)) == payload[field]
            for field in (*TOKEN_FIELDS, "total_tokens")
        ):
            continue
        payload["observed_at"] = stamp
        lines.append(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    if not lines:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return len(lines)
