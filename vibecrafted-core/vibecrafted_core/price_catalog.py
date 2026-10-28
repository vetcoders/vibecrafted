"""Official model rates for local USD estimates.

OpenRouter's public model list is the rate source. A copy pinned in this
package is the offline fallback. A cache under
``$VIBECRAFTED_HOME/telemetry/prices.json`` may replace the pin after a
successful fetch, and that fetch runs at most once a day. Nothing here
invents a rate: a model the table does not list stays unmapped.

Amounts are API equivalents. They are not subscription invoices.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
PIN_PATH = Path(__file__).resolve().parent / "data" / "openrouter_prices.json"
SOURCE_NAME = "openrouter"
UNMAPPED = "unmapped"
API_EQUIV = "api-equiv"
_SUFFIX = re.compile(
    r"(?:-\d{8}|-\d{4}-\d{2}-\d{2}|-build(?:-fast)?|-(?:low|medium|high|xhigh|max)(?:-fast)?|:cloud)\Z"
)

# Our ids, longest first so gpt-6.1-sol is not swallowed by a shorter sibling.
# The remote id is the OpenRouter model id, not a guessed family.
_ALIASES: tuple[tuple[str, str], ...] = (
    ("claude-opus-4.8", "anthropic/claude-opus-4.8"),
    ("claude-opus-4.7", "anthropic/claude-opus-4.7"),
    ("claude-opus-4.6", "anthropic/claude-opus-4.6"),
    ("claude-opus-4.5", "anthropic/claude-opus-4.5"),
    ("claude-opus-4-8", "anthropic/claude-opus-4.8"),
    ("claude-opus-4-7", "anthropic/claude-opus-4.7"),
    ("claude-opus-4-6", "anthropic/claude-opus-4.6"),
    ("claude-opus-4-5", "anthropic/claude-opus-4.5"),
    ("claude-sonnet-4.6", "anthropic/claude-sonnet-4.6"),
    ("claude-sonnet-4.5", "anthropic/claude-sonnet-4.5"),
    ("claude-sonnet-4-6", "anthropic/claude-sonnet-4.6"),
    ("claude-sonnet-4-5", "anthropic/claude-sonnet-4.5"),
    ("claude-haiku-4.5", "anthropic/claude-haiku-4.5"),
    ("claude-haiku-4-5", "anthropic/claude-haiku-4.5"),
    ("claude-haiku-5.5", "anthropic/claude-haiku-5.5"),
    ("claude-haiku-5-5", "anthropic/claude-haiku-5.5"),
    ("claude-sonnet-5.5", "anthropic/claude-sonnet-5.5"),
    ("claude-sonnet-5-5", "anthropic/claude-sonnet-5.5"),
    ("claude-fable-5.1", "anthropic/claude-fable-5.1"),
    ("claude-fable-5-1", "anthropic/claude-fable-5.1"),
    ("claude-opus-5.5", "anthropic/claude-opus-5.5"),
    ("claude-opus-5-5", "anthropic/claude-opus-5.5"),
    ("claude-sonnet-5", "anthropic/claude-sonnet-5"),
    ("claude-fable-5", "anthropic/claude-fable-5"),
    ("claude-opus-5", "anthropic/claude-opus-5"),
    ("gemini-3.8-flash", "google/gemini-3.8-flash"),
    ("gemini-3.7-flash", "google/gemini-3.7-flash"),
    ("gemini-3.6-flash", "google/gemini-3.6-flash"),
    ("gemini-3.5-flash", "google/gemini-3.5-flash"),
    ("gemini-2.5-flash", "google/gemini-2.5-flash"),
    ("gemini-2.5-pro", "google/gemini-2.5-pro"),
    ("gemini-4-argon", "google/gemini-4-argon"),
    ("gpt-6.1-terra", "openai/gpt-6.1-terra"),
    ("gpt-6.1-sol", "openai/gpt-6.1-sol"),
    ("gpt-6-1-sol", "openai/gpt-6.1-sol"),
    ("gpt-5.6-terra", "openai/gpt-5.6-terra"),
    ("gpt-5.6-luna", "openai/gpt-5.6-luna"),
    ("gpt-5.6-sol", "openai/gpt-5.6-sol"),
    ("gpt-6-astra", "openai/gpt-6-astra"),
    ("gpt-6-luna", "openai/gpt-6-luna"),
    ("gpt-6-sol", "openai/gpt-6-sol"),
    ("grok-4.7", "x-ai/grok-4.7"),
    ("grok-4.6", "x-ai/grok-4.6"),
    ("grok-4.5", "x-ai/grok-4.5"),
    ("kimi-k3", "moonshotai/kimi-k3"),
    ("k3", "moonshotai/kimi-k3"),
)


@dataclass(frozen=True)
class CatalogRate:
    """USD per million tokens, plus the ``price_table(source@date)`` label."""

    input_per_million: float
    cached_input_per_million: float
    output_per_million: float
    source: str
    cache_creation_per_million: float | None = None


def normalize_model(model: str) -> str:
    """Lower-case a model id the same way the historical price table does."""
    return (
        (model or "")
        .strip()
        .lower()
        .replace(" ", "-")
        .removeprefix("kimi-code/")
        .removeprefix("cursor-")
    )


def alias_match(normalized: str, alias: str) -> bool:
    """Exact id, or the alias plus a dated / effort / build suffix."""
    if normalized == alias:
        return True
    if not normalized.startswith(alias):
        return False
    return _SUFFIX.fullmatch(normalized[len(alias) :]) is not None


def cache_path() -> Path:
    """Local OpenRouter cache. Not a Vibecrafted backend."""
    base = os.environ.get("VIBECRAFTED_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".vibecrafted"
    return root / "telemetry" / "prices.json"


def price_source_label(source: str, fetched_at: str) -> str:
    """``price_table(<source>@<YYYY-MM-DD>)`` from a catalog document."""
    day = str(fetched_at or "")[:10]
    return f"price_table({source}@{day})" if day else f"price_table({source})"


def lookup_rate(
    model: str, document: dict[str, Any] | None = None
) -> CatalogRate | None:
    """Return the catalog rate for ``model``, or None when it is not listed."""
    normalized = normalize_model(model)
    if not normalized or normalized.endswith("-cli-default"):
        return None
    doc = document if document is not None else current_document()
    models = doc.get("models") if isinstance(doc.get("models"), dict) else {}
    remote = _remote_id(normalized, models)
    raw = models.get(remote) if remote else None
    if not isinstance(raw, dict):
        return None
    try:
        rate = CatalogRate(
            input_per_million=float(raw["input_per_million"]),
            cached_input_per_million=float(raw["cached_input_per_million"]),
            output_per_million=float(raw["output_per_million"]),
            cache_creation_per_million=(
                None
                if raw.get("cache_creation_per_million") is None
                else float(raw["cache_creation_per_million"])
            ),
            source=price_source_label(
                str(doc.get("source") or SOURCE_NAME),
                str(doc.get("fetched_at") or ""),
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return rate


def current_document() -> dict[str, Any]:
    """Env override, then a non-test cache, then the pinned snapshot."""
    override = os.environ.get("VIBECRAFTED_PRICE_CATALOG")
    if override:
        loaded = _read_json(Path(override).expanduser())
        if loaded:
            return loaded
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        cached = _read_json(cache_path())
        if cached:
            return cached
    return _read_json(PIN_PATH) or {}


def quote_usage(
    model: str | None,
    *,
    fresh_input: int,
    cache_read: int,
    cache_write: int,
    output: int,
    reasoning: int,
    cache_accounting: str,
) -> dict[str, Any]:
    """USD for one native bucket. Reasoning is reported and not added again."""
    clean = normalize_model(str(model or ""))
    if not clean or clean.endswith("-cli-default"):
        return _unknown("model unknown; no price table lookup")
    if cache_accounting == "subset_of_input" and fresh_input < 0:
        return _unknown("cache exceeds input; refused to price")
    rate = lookup_rate(clean)
    if rate is None:
        return {
            "cost_usd": "unknown",
            "cost_source": UNMAPPED,
            "cost_reason": f"no price table entry for model {clean!r}",
            "cache_accounting": cache_accounting,
        }
    if cache_write and rate.cache_creation_per_million is None:
        return _unknown(f"no cache creation rate for model {clean!r}")
    amount, components = component_amounts(
        rate,
        fresh=fresh_input,
        cached=cache_read,
        created=cache_write,
        output=output,
        reasoning=reasoning,
    )
    return {
        "cost_usd": amount,
        "cost_source": rate.source,
        "cost_basis": API_EQUIV,
        "cost_note": "API-equivalent estimate, not a subscription invoice",
        "components": components,
        "reasoning_billing": "subset_of_output",
        "cache_accounting": cache_accounting,
    }


def component_amounts(
    rate: CatalogRate,
    *,
    fresh: int,
    cached: int,
    created: int,
    output: int,
    reasoning: int,
) -> tuple[float, dict[str, float]]:
    """Split USD. ``reasoning_usd`` is a slice of output, not an extra charge."""
    write_rate = rate.cache_creation_per_million or 0.0
    parts = {
        "input_usd": fresh * rate.input_per_million / 1_000_000,
        "cached_input_usd": cached * rate.cached_input_per_million / 1_000_000,
        "cache_write_usd": created * write_rate / 1_000_000,
        "output_usd": output * rate.output_per_million / 1_000_000,
        "reasoning_usd": reasoning * rate.output_per_million / 1_000_000,
    }
    rounded = {key: round(value, 6) for key, value in parts.items()}
    total = round(
        parts["input_usd"]
        + parts["cached_input_usd"]
        + parts["cache_write_usd"]
        + parts["output_usd"],
        6,
    )
    return total, rounded


def sum_quotes(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum session quotes. One unmapped session keeps the total unmapped."""
    if not quotes:
        return _unknown("no sessions")
    if any(item.get("cost_source") == UNMAPPED for item in quotes):
        return {
            "cost_usd": "unknown",
            "cost_source": UNMAPPED,
            "cost_reason": "one or more sessions have no price table entry",
        }
    if any(not isinstance(item.get("cost_usd"), (int, float)) for item in quotes):
        reason = next(
            (
                str(item.get("cost_reason") or "cost unknown")
                for item in quotes
                if not isinstance(item.get("cost_usd"), (int, float))
            ),
            "cost unknown",
        )
        return _unknown(reason)
    components = {
        "input_usd": 0.0,
        "cached_input_usd": 0.0,
        "cache_write_usd": 0.0,
        "output_usd": 0.0,
        "reasoning_usd": 0.0,
    }
    for item in quotes:
        part = (
            item.get("components") if isinstance(item.get("components"), dict) else {}
        )
        for key, value in components.items():
            components[key] = round(value + float(part.get(key) or 0.0), 6)
    amount = round(sum(float(item["cost_usd"]) for item in quotes), 6)
    source = str(quotes[0].get("cost_source") or "")
    if any(str(item.get("cost_source") or "") != source for item in quotes):
        source = "+".join(
            sorted({str(item.get("cost_source") or "") for item in quotes})
        )
    return {
        "cost_usd": amount,
        "cost_source": source,
        "cost_basis": API_EQUIV,
        "cost_note": "API-equivalent estimate, not a subscription invoice",
        "components": components,
        "reasoning_billing": "subset_of_output",
    }


def refresh_prices(
    *,
    now: datetime | None = None,
    cache_file: Path | None = None,
    fetcher: Callable[[], dict[str, Any]] | None = None,
) -> str:
    """Refresh the local cache at most daily. Failure keeps the previous file.

    Returns ``fresh``, ``fetched``, or ``offline``. Never raises for transport
    errors. Tests pass ``fetcher``; production uses OpenRouter.
    """
    if os.environ.get("VIBECRAFTED_PRICE_OFFLINE") == "1":
        return "offline"
    moment = now or datetime.now(UTC)
    path = cache_file or cache_path()
    existing = _read_json(path)
    if _is_fresh(existing, moment):
        return "fresh"
    fetch = fetcher or fetch_openrouter
    try:
        payload = fetch()
        document = (
            payload
            if isinstance(payload.get("models"), dict)
            else catalog_from_openrouter(payload, fetched_at=moment)
        )
    except (
        OSError,
        TimeoutError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        KeyError,
    ):
        return "offline"
    if not isinstance(document.get("models"), dict) or not document["models"]:
        return "offline"
    _write_json(path, document)
    return "fetched"


def fetch_openrouter() -> dict[str, Any]:
    """GET the public model list. No auth header, short timeout.

    The address is the literal ``OPENROUTER_URL``. Callers cannot retarget it.
    The urllib audit rule still matches a literal, so the suppression sits on
    that one call.
    """
    with (
        urllib.request.urlopen(  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            "https://openrouter.ai/api/v1/models",
            timeout=8,
        ) as response
    ):
        body = response.read()
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise TypeError("openrouter payload is not an object")
    return parsed


def catalog_from_openrouter(
    payload: dict[str, Any], *, fetched_at: datetime
) -> dict[str, Any]:
    """Keep standard (non-batch) models. Per-token prices become per million."""
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise TypeError("openrouter payload has no data list")
    models: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or "")
        if not model_id or model_id.startswith("~") or ":batch" in model_id:
            continue
        pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
        prompt = _per_million(pricing.get("prompt"))
        completion = _per_million(pricing.get("completion"))
        if prompt is None or completion is None:
            continue
        models[model_id] = {
            "canonical": str(row.get("canonical_slug") or model_id),
            "input_per_million": prompt,
            "cached_input_per_million": _per_million(pricing.get("input_cache_read")),
            "output_per_million": completion,
            "cache_creation_per_million": _per_million(
                pricing.get("input_cache_write")
            ),
        }
    return {
        "source": SOURCE_NAME,
        "source_url": OPENROUTER_URL,
        "fetched_at": fetched_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": (
            "Fetched from OpenRouter. Amounts are API equivalents, "
            "not subscription invoices. Batch variants are omitted."
        ),
        "models": models,
    }


def _remote_id(normalized: str, models: dict[str, Any]) -> str | None:
    for alias, remote in _ALIASES:
        if alias_match(normalized, alias) and remote in models:
            return remote
    exact = [
        key
        for key in models
        if key.split("/")[-1] == normalized and ":batch" not in key
    ]
    if len(exact) == 1:
        return exact[0]
    return None


def _per_million(raw: object) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return round(float(raw) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


def _is_fresh(document: dict[str, Any], now: datetime) -> bool:
    text = str(document.get("fetched_at") or "")
    if not text:
        return False
    try:
        fetched = datetime.fromisoformat(text)
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    return now.astimezone(UTC) - fetched.astimezone(UTC) < timedelta(days=1)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _unknown(reason: str) -> dict[str, Any]:
    return {"cost_usd": "unknown", "cost_source": "unknown", "cost_reason": reason}
