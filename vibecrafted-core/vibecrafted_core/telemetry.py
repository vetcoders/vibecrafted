"""Provider-neutral per-run usage, cost and failure-attribution contracts.

Zero is a measurement, never a default. A value the provider did not report is
``Unknown`` with a reason; a cost is ``provider_reported``,
``estimated:<price-table-id>`` or ``unknown`` — never a fictional ``0``.
Currencies and units are kept apart; nothing here converts credits to dollars.

This module sits on every pane's stream path (``agent_stream`` imports it), so
it stays a leaf: stdlib only, no receipt/product-contract graph.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelPrice:
    """Per-million-token USD rates for one model family, plus a rate source label."""

    input_per_million: float
    cached_input_per_million: float
    output_per_million: float
    source: str
    cache_creation_per_million: float | None = None


# API-equivalent rates. Provider-reported CLI cost always wins; these rates are
# only a transparent fallback when a stream exposes tokens but no monetary cost.
_PRICES: tuple[tuple[tuple[str, ...], ModelPrice], ...] = (
    # Verified 2026-09-28: https://platform.openai.com/pricing
    (("gpt-6-astra",), ModelPrice(10.0, 1.0, 50.0, "openai-api-2026-09-28")),
    # Verified 2026-09-30: https://openai.com/index/introducing-gpt-6-1-sol/
    # ($2 in / $0.10 cached / $10 out, $2.50 cache write per 1M, standard tier;
    # >272K-input requests bill at $4/$15 — not modeled, flat standard rate).
    (
        ("gpt-6.1-sol", "gpt-6-1-sol"),
        ModelPrice(2.0, 0.1, 10.0, "openai-api-2026-09-30", 2.5),
    ),
    # Verified 2026-09-30 (openrouter.ai/openai/gpt-6-sol, eesel, layer3labs):
    # $2 in / $0.20 cached / $10 out, $2.50 cache write per 1M, standard tier;
    # >272K-input long-context rate ($4/$15) not modeled, flat standard rate.
    (("gpt-6-sol",), ModelPrice(2.0, 0.2, 10.0, "openai-api-2026-09-30", 2.5)),
    # Verified 2026-09-30 (pricepertoken.com, finitizer): $2 in / $0.50 cached
    # / $8 out per 1M. Covers dated stamps such as gpt-4.1-2025-04-14.
    (("gpt-4.1", "gpt-4-1"), ModelPrice(2.0, 0.5, 8.0, "openai-api-2026-09-30")),
    # https://platform.claude.com/docs/en/about-claude/pricing
    # Cache creation estimate uses the standard 5-minute write rate.
    (
        (
            "claude-fable-5-1",
            "claude-fable-5.1",
            "claude-mythos-5-1",
            "claude-mythos-5.1",
        ),
        ModelPrice(10, 0.25, 50, "anthropic-api-2026-09-28", 12.5),
    ),
    (
        ("claude-fable-5", "claude-mythos-5"),
        ModelPrice(10, 1, 50, "anthropic-api-2026-09-28", 12.5),
    ),
    (
        ("claude-opus-5-5", "claude-opus-5.5"),
        ModelPrice(4, 0.2, 20, "anthropic-api-2026-09-28", 5),
    ),
    (
        (
            "claude-opus-5",
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-opus-4-5",
            "claude-opus-4.8",
            "claude-opus-4.7",
            "claude-opus-4.6",
            "claude-opus-4.5",
        ),
        ModelPrice(5, 0.5, 25, "anthropic-api-2026-09-28", 6.25),
    ),
    (("claude-sonnet-5",), ModelPrice(2, 0.2, 10, "anthropic-api-2026-09-28", 2.5)),
    (
        (
            "claude-sonnet-4",
            "claude-sonnet-4-5",
            "claude-sonnet-4-6",
            "claude-sonnet-4.5",
            "claude-sonnet-4.6",
        ),
        ModelPrice(3, 0.3, 15, "anthropic-api-2026-09-28", 3.75),
    ),
    (
        ("claude-haiku-4-5", "claude-haiku-4.5"),
        ModelPrice(1, 0.1, 5, "anthropic-api-2026-09-28", 1.25),
    ),
    # https://docs.x.ai/developers/models/grok-4.6
    (("grok-4.6", "grok-4.7"), ModelPrice(2, 0.5, 6, "xai-api-2026-09-28")),
    # https://docs.x.ai/developers/pricing (standard context rates)
    (("grok-4.5",), ModelPrice(2, 0.3, 6, "xai-api-2026-09-28")),
    # https://forum.moonshot.ai/t/kimi-k3-is-here-our-most-capable-model/480
    (("kimi-k3", "k3"), ModelPrice(3, 0.3, 15, "moonshot-api-2026-09-28", 3)),
    (("grok-build", "grok-code-fast"), ModelPrice(1.0, 0.2, 2.0, "xai-api-2026-07")),
    (("gpt-5.6-sol",), ModelPrice(5.0, 0.5, 30.0, "openai-api-2026-07")),
    (("gpt-5.6-terra",), ModelPrice(2.5, 0.25, 15.0, "openai-api-2026-07")),
    (("gpt-5.6-luna",), ModelPrice(1.0, 0.1, 6.0, "openai-api-2026-07")),
    (("gpt-5.5",), ModelPrice(5.0, 0.5, 30.0, "openai-api-2026-07")),
    (("gpt-5.4",), ModelPrice(2.5, 0.25, 15.0, "openai-api-2026-07")),
    (("gpt-5",), ModelPrice(1.25, 0.125, 10.0, "openai-api-2026-07")),
)


def model_price(model: str) -> ModelPrice | None:
    """Exact model/version aliases only; unknown variants never inherit a rate."""
    normalized = (
        (model or "")
        .strip()
        .lower()
        .replace(" ", "-")
        .removeprefix("kimi-code/")
        .removeprefix("cursor-")
    )
    for aliases, price in _PRICES:
        if any(
            normalized == alias
            or re.fullmatch(
                re.escape(alias)
                + r"(?:-\d{8}|-\d{4}-\d{2}-\d{2}|-build(?:-fast)?|-(?:low|medium|high|xhigh|max)(?:-fast)?|:cloud)",
                normalized,
            )
            for alias in aliases
        ):
            return price
    return None


def estimate_cost_usd(
    model: str,
    *,
    tokens_input: int,
    tokens_cached_input: int,
    tokens_output: int,
) -> tuple[float | None, str | None]:
    """Estimate a USD cost from token counts and a known model's API rates.

    Returns (None, None) when the model is unrecognized or all token counts
    are zero. This is a fallback estimate only — provider-reported cost wins.
    """
    price = model_price(model)
    if price is None or not (tokens_input or tokens_cached_input or tokens_output):
        return None, None
    return _priced(price, tokens_input, tokens_cached_input, tokens_output), (
        f"estimated:{price.source}"
    )


def _priced(price: ModelPrice, tokens_input: int, cached: int, output: int) -> float:
    """USD for the given token counts at *price*, rounded to 6 places."""
    cost = (
        tokens_input * price.input_per_million
        + cached * price.cached_input_per_million
        + output * price.output_per_million
    ) / 1_000_000
    return round(cost, 6)


def tokens_total(
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


# --------------------------------------------------------------------------
# Unknown — the refused-to-guess value
# --------------------------------------------------------------------------

NO_USAGE_EVENTS_REASON = "provider emitted no usage events"
NO_CACHE_WRITE_REASON = "provider stream carried no cache-write field"
NO_COST_REASON = "provider emitted no usage events and reported no cost"
NO_MODEL_REASON = "model unknown; no price table lookup"
NO_SESSION_REASON = "provider emitted no session id"
NO_MODEL_REPORTED_REASON = "model not reported by provider stream"
LAZY_SOURCE = "transcript(lazy)"
USAGE_SCHEMA = "vibecrafted.usage.v1"


@dataclass(frozen=True)
class Unknown:
    """A refused-to-guess value: ``{"value": "unknown", "reason": ...}``.

    Same JSON projection as ``runtime_receipt.Unknown`` (parity is pinned by a
    test). Kept here rather than imported: that module drags the product
    contract graph (~0.4 s) onto every pane's stream path.
    """

    value: str = "unknown"
    reason: str = ""

    def as_dict(self) -> dict[str, str]:
        """JSON projection of this unknown marker."""
        return {"value": "unknown", "reason": self.reason}


def _project(value: object) -> object:
    """JSON value for meta: ``Unknown`` becomes its dict, everything else as-is."""
    return value.as_dict() if isinstance(value, Unknown) else value


def _flat(value: object) -> object:
    """Legacy scalar for flat meta/frontmatter keys: ``Unknown`` becomes "unknown"."""
    return "unknown" if isinstance(value, Unknown) else value


def is_unknown(value: object) -> bool:
    """True for an ``Unknown``, its JSON projection, or the flat "unknown" string."""
    if isinstance(value, Unknown):
        return True
    if isinstance(value, Mapping):
        return value.get("value") == "unknown"
    return isinstance(value, str) and value.strip().lower() == "unknown"


def unknown_reason(value: object) -> str:
    """Reason carried by an unknown value, or "" when none is recorded."""
    if isinstance(value, Unknown):
        return value.reason
    if isinstance(value, Mapping):
        return str(value.get("reason") or "")
    return ""


# --------------------------------------------------------------------------
# Usage
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageRecord:
    """Token usage of one run, with the channel it was read from.

    ``events`` counts provider usage events actually observed. With zero
    events every count is ``Unknown`` — a provider that stays silent did not
    use zero tokens. ``source`` names the channel inspected
    (``provider_stream``, ``transcript``, ``transcript(lazy)``).
    """

    tokens_input: int | Unknown
    tokens_cached_input: int | Unknown
    tokens_cache_write: int | Unknown
    tokens_output: int | Unknown
    tokens_total: int | Unknown
    source: str
    events: int
    unit: str = "tokens"
    counting_version: int = 1
    model_usage: dict[str, dict[str, int]] | None = None
    tokens_reasoning: int | None = None

    @property
    def known(self) -> bool:
        """True when at least one provider usage event was observed."""
        return self.events > 0

    def as_dict(self) -> dict[str, object]:
        """Structured ``usage`` block for meta.json."""
        return {
            "schema": USAGE_SCHEMA,
            "counting_version": self.counting_version,
            **(
                {
                    "model_usage": self.model_usage,
                    "input_semantics": "includes_cache",
                    "tokens_reasoning": self.tokens_reasoning,
                }
                if self.model_usage is not None
                else {}
            ),
            "unit": self.unit,
            "source": self.source,
            "events": self.events,
            "tokens_input": _project(self.tokens_input),
            "tokens_cached_input": _project(self.tokens_cached_input),
            "tokens_cache_write": _project(self.tokens_cache_write),
            "tokens_output": _project(self.tokens_output),
            "tokens_total": _project(self.tokens_total),
        }

    def flat(self) -> dict[str, object]:
        """Legacy flat ``tokens_*`` keys: integers, or "unknown" — never a fake 0."""
        fields: dict[str, object] = {
            "tokens_input": _flat(self.tokens_input),
            "tokens_cached_input": _flat(self.tokens_cached_input),
            "tokens_output": _flat(self.tokens_output),
            "tokens_total": _flat(self.tokens_total),
        }
        if isinstance(self.tokens_cache_write, int):
            fields["tokens_cache_write"] = self.tokens_cache_write
        return fields


def usage_record(
    events: int,
    *,
    tokens_input: int,
    tokens_cached_input: int,
    tokens_cache_write: int | None,
    tokens_output: int,
    source: str,
) -> UsageRecord:
    """Build a UsageRecord; no observed events means every count is Unknown."""
    if events <= 0:
        missing = Unknown(reason=NO_USAGE_EVENTS_REASON)
        return UsageRecord(
            tokens_input=missing,
            tokens_cached_input=missing,
            tokens_cache_write=missing,
            tokens_output=missing,
            tokens_total=missing,
            source=source,
            events=0,
        )
    return UsageRecord(
        tokens_input=int(tokens_input),
        tokens_cached_input=int(tokens_cached_input),
        tokens_cache_write=int(tokens_cache_write)
        if tokens_cache_write is not None
        else Unknown(reason=NO_CACHE_WRITE_REASON),
        tokens_output=int(tokens_output),
        tokens_total=tokens_total(tokens_input, tokens_cached_input, tokens_output),
        source=source,
        events=int(events),
    )


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CostRecord:
    """Cost of one run in exactly one of three forms.

    ``source`` is ``provider_reported``, ``estimated:<price-table-id>`` or
    ``unknown``. Money carries ``currency``; a non-monetary provider unit
    (credits) carries ``unit`` instead and is never folded into dollars.
    """

    amount: float | Unknown
    source: str
    currency: str = "USD"
    unit: str = ""

    def as_dict(self) -> dict[str, object]:
        """Structured ``cost`` block for meta.json."""
        payload: dict[str, object] = {"amount": _project(self.amount)}
        if self.unit:
            payload["unit"] = self.unit
        else:
            payload["currency"] = self.currency
        payload["source"] = self.source
        return payload

    def flat(self) -> dict[str, object]:
        """Legacy ``cost_usd``/``cost_source`` keys (non-USD amounts stay unknown)."""
        usd = (
            self.amount
            if not self.unit and self.currency == "USD" and not is_unknown(self.amount)
            else "unknown"
        )
        return {"cost_usd": usd, "cost_source": self.source}


def resolve_cost(
    model: str,
    usage: UsageRecord,
    *,
    reported_amount: float | None,
    reported_source: str | None,
) -> CostRecord:
    """Pick the one honest cost form for a run.

    A provider-reported amount wins. Otherwise a known model with a price
    table entry and observed usage is estimated from that table. Anything
    else is ``unknown`` with the reason — there is no default price.
    """
    source = str(reported_source or "").strip()
    if reported_amount is not None:
        amount = round(float(reported_amount), 6)
        if source.startswith("estimated:"):
            return CostRecord(amount=amount, source=source)
        return CostRecord(amount=amount, source="provider_reported")
    if not usage.known:
        return CostRecord(amount=Unknown(reason=NO_COST_REASON), source="unknown")
    if usage.model_usage is not None:
        amount = 0.0
        sources: set[str] = set()
        for name, buckets in usage.model_usage.items():
            price = model_price(name)
            if price is None:
                return CostRecord(
                    amount=Unknown(reason=f"no price table entry for model {name!r}"),
                    source="unknown",
                )
            write_rate = price.cache_creation_per_million
            if buckets["cache_creation"] and write_rate is None:
                return CostRecord(
                    amount=Unknown(reason=f"no cache creation rate for model {name!r}"),
                    source="unknown",
                )
            amount += (
                buckets["fresh_input"] * price.input_per_million
                + buckets["cache_read"] * price.cached_input_per_million
                + buckets["cache_creation"] * (write_rate or 0)
                + buckets["output"] * price.output_per_million
            ) / 1_000_000
            sources.add(price.source)
        return CostRecord(
            amount=round(amount, 6), source="estimated:" + "+".join(sorted(sources))
        )
    clean_model = str(model or "").strip()
    if not clean_model:
        return CostRecord(amount=Unknown(reason=NO_MODEL_REASON), source="unknown")
    price = model_price(clean_model)
    if price is None:
        return CostRecord(
            amount=Unknown(reason=f"no price table entry for model {clean_model!r}"),
            source="unknown",
        )
    counts = (usage.tokens_input, usage.tokens_cached_input, usage.tokens_output)
    tokens_in, cached, out = (int(value) for value in counts)  # type: ignore[arg-type]
    return CostRecord(
        amount=_priced(price, tokens_in, cached, out),
        source=f"estimated:{price.source}",
    )


def cost_from_dict(payload: Mapping[str, Any]) -> CostRecord:
    """Rebuild a CostRecord from a recorded meta ``cost`` block."""
    amount_value = payload.get("amount")
    amount: float | Unknown
    if isinstance(amount_value, (int, float)) and not isinstance(amount_value, bool):
        amount = float(amount_value)
    else:
        amount = Unknown(reason=unknown_reason(amount_value) or "cost not recorded")
    return CostRecord(
        amount=amount,
        source=str(payload.get("source") or "unknown"),
        currency=str(payload.get("currency") or "USD"),
        unit=str(payload.get("unit") or ""),
    )


# --------------------------------------------------------------------------
# Failure attribution
# --------------------------------------------------------------------------

#: ``quota_exhausted`` here is the *provider's* billing quota (origin
#: ``provider``) — not Vibecrafted's own token-budget stop, which settles as
#: ``status=quota_exhausted`` with exit 75 and is no provider failure.
FAILURE_KINDS = (
    "quota_exhausted",
    "auth_error",
    "rate_limited",
    "network",
    "tool_error",
    "unknown",
)
_KIND_WORDS = {
    "quota_exhausted": "quota exhausted",
    "auth_error": "auth error",
    "rate_limited": "rate limited",
    "network": "network error",
    "tool_error": "tool error",
}


@dataclass(frozen=True)
class FailureAttribution:
    """Why a run exited non-zero, with the transcript line that says so.

    ``provider_code``, ``message`` and ``evidence`` hold their JSON
    projection: a value, or ``{"value": "unknown", "reason": ...}``.
    """

    exit_code: int
    kind: str
    provider_code: int | dict[str, str]
    message: str | dict[str, str]
    evidence: dict[str, object]
    source: str
    reason: str = ""
    origin: str = "provider"

    def detail(self) -> str:
        """The parenthesised cause: ``provider's code 403 quota exhausted``."""
        if self.kind == "unknown" or self.kind not in _KIND_WORDS:
            return f"cause unknown: {self.reason or 'unclassified'}"
        words = _KIND_WORDS[self.kind]
        if isinstance(self.provider_code, int):
            return f"provider's code {self.provider_code} {words}"
        return f"provider {words}"

    def summary(self) -> str:
        """Founder form: ``exit_code=1 (provider's code 403 quota exhausted)``."""
        return f"exit_code={self.exit_code} ({self.detail()})"

    def as_dict(self) -> dict[str, object]:
        """Structured ``failure`` block for meta.json."""
        payload: dict[str, object] = {
            "exit_code": self.exit_code,
            "kind": self.kind,
            "origin": self.origin,
            "provider_code": self.provider_code,
            "message": self.message,
            "evidence": self.evidence,
            "source": self.source,
            "summary": self.summary(),
        }
        if self.reason:
            payload["reason"] = self.reason
        return payload

    def frontmatter(self) -> dict[str, str]:
        """Flat report-frontmatter keys (the redacted message stays in meta)."""
        return {
            "failure": self.summary(),
            "failure_kind": self.kind,
            "failure_provider_code": str(_flat(self.provider_code))
            if not isinstance(self.provider_code, dict)
            else "unknown",
            "failure_evidence": _evidence_text(self.evidence),
            "failure_source": self.source,
        }


def _evidence_text(evidence: Mapping[str, object]) -> str:
    """``<path>:<line>`` for located evidence, else "unknown"."""
    if is_unknown(evidence):
        return "unknown"
    return f"{evidence.get('path')}:{evidence.get('line')}"


# --------------------------------------------------------------------------
# Provider session identity
# --------------------------------------------------------------------------

_PARENT_SESSION_ENV = re.compile(r"^VIBECRAFTED_\w*SESSION")
#: The launch contract's declaration of *this child's* provider session
#: (set for native resume, stripped for fresh children) — not a parent.
_CHILD_SESSION_ENV = frozenset({"VIBECRAFTED_AGENT_SESSION_ID"})


def parent_session_ids(
    env: Mapping[str, str] | None = None,
    *,
    extra: Mapping[str, object] | None = None,
) -> dict[str, str]:
    """Map every parent/runtime session id in scope to where it came from.

    Covers ``VIBECRAFTED_*SESSION*`` env values (except the child's own
    declared provider session) plus caller-supplied ids such as the run's
    runtime session or the fork source.
    """
    source = os.environ if env is None else env
    found: dict[str, str] = {}
    for key in sorted(source):
        if key in _CHILD_SESSION_ENV or not _PARENT_SESSION_ENV.match(key):
            continue
        value = str(source.get(key) or "").strip()
        if value:
            found.setdefault(value, key)
    for label, raw in (extra or {}).items():
        value = str(raw or "").strip()
        if value:
            found.setdefault(value, label)
    return found


def resolve_provider_session_id(
    candidate: str, *, source: str, parents: Mapping[str, str]
) -> tuple[str | Unknown, str]:
    """Return ``(provider_session_id, source)``; a parent id is refused, not kept."""
    value = str(candidate or "").strip()
    if not value:
        return Unknown(reason=NO_SESSION_REASON), "unknown"
    if value in parents:
        return (
            Unknown(reason=f"candidate equals parent session id ({parents[value]})"),
            "refused",
        )
    return value, source


# --------------------------------------------------------------------------
# One run's telemetry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RunTelemetry:
    """Usage, cost, failure and provider session of one closed run."""

    usage: UsageRecord
    cost: CostRecord
    failure: FailureAttribution | None
    provider_session_id: str | Unknown
    provider_session_source: str
    model: str = ""

    def meta_fields(self) -> dict[str, object]:
        """Keys merged into meta.json (structured blocks plus flat legacy mirrors)."""
        fields: dict[str, object] = {
            **self.usage.flat(),
            **self.cost.flat(),
            "usage": self.usage.as_dict(),
            "cost": self.cost.as_dict(),
            "provider_session_id": _project(self.provider_session_id),
            "provider_session_source": self.provider_session_source,
            "unpricedModels": sorted(
                name or "unknown"
                for name in (self.usage.model_usage or {self.model: {}})
                if model_price(name) is None
            ),
        }
        if self.failure is not None:
            fields["failure"] = self.failure.as_dict()
        return fields

    def frontmatter_fields(self) -> dict[str, str]:
        """Launcher-owned report frontmatter keys (flat strings)."""
        fields: dict[str, str] = {
            "provider_session_id": str(_flat(self.provider_session_id)),
            **{key: str(value) for key, value in self.usage.flat().items()},
            "usage_source": self.usage.source,
            "usage_unit": self.usage.unit,
            "cost_usd": str(self.cost.flat()["cost_usd"]),
            "cost_source": self.cost.source,
        }
        if not self.usage.known:
            fields["usage_reason"] = NO_USAGE_EVENTS_REASON
        if isinstance(self.cost.amount, Unknown):
            fields["cost_reason"] = self.cost.amount.reason
        if isinstance(self.provider_session_id, Unknown):
            fields["provider_session_reason"] = self.provider_session_id.reason
        if self.failure is not None:
            fields.update(self.failure.frontmatter())
        return fields


def build_run_telemetry(
    *,
    usage: UsageRecord,
    model: str,
    reported_cost: float | None,
    reported_cost_source: str | None,
    session_candidate: str,
    session_source: str,
    parents: Mapping[str, str],
    failure: FailureAttribution | None,
    agent: str = "",
    started_at: object = None,
    completed_at: object = None,
) -> RunTelemetry:
    """Assemble a RunTelemetry from what a close path observed."""
    provider_session_id, provider_session_source = resolve_provider_session_id(
        session_candidate, source=session_source, parents=parents
    )
    if not usage.known and isinstance(provider_session_id, str):
        usage = (
            harness_usage_record(
                agent, provider_session_id, started_at, completed_at, model
            )
            or usage
        )
    return RunTelemetry(
        usage=usage,
        cost=resolve_cost(
            model,
            _stream_pricing_usage(usage, agent, model),
            reported_amount=None
            if str(reported_cost_source).startswith("estimated:")
            else reported_cost,
            reported_source=reported_cost_source,
        ),
        failure=failure,
        provider_session_id=provider_session_id,
        provider_session_source=provider_session_source,
        model=model,
    )


def _stream_pricing_usage(usage: UsageRecord, agent: str, model: str) -> UsageRecord:
    """Use provider semantics for pricing; never guess from cache magnitudes."""
    if (
        not usage.known
        or usage.model_usage is not None
        or agent not in {"codex", "grok", "cursor", "claude", "junie"}
    ):
        return usage
    inp, cached, out = (
        int(usage.tokens_input),
        int(usage.tokens_cached_input),
        int(usage.tokens_output),
    )
    created = (
        usage.tokens_cache_write if isinstance(usage.tokens_cache_write, int) else 0
    )
    # Legacy Grok stream modelUsage reports fresh input; native inference_done
    # prompt_tokens are inclusive and normalized separately in harness_usage.
    fresh = inp - cached - created if agent in {"codex", "cursor"} else inp
    if fresh < 0:
        return replace(usage, events=0)
    return replace(
        usage,
        model_usage={
            model: {
                "fresh_input": fresh,
                "cache_read": cached,
                "cache_creation": created,
                "output": out,
                "reasoning": 0,
            }
        },
    )


def harness_usage_record(
    agent: str, session: str, start: object, end: object, model: str
) -> UsageRecord | None:
    """Adapt local evidence into the one existing usage contract."""
    from .harness_usage import resolve_harness_usage

    evidence = resolve_harness_usage(
        agent=agent, session_id=session, started_at=start, completed_at=end, model=model
    )
    if evidence is None:
        return None
    totals = evidence.totals()
    inp = totals["fresh_input"] + totals["cache_read"] + totals["cache_creation"]
    return UsageRecord(
        tokens_input=inp,
        tokens_cached_input=totals["cache_read"],
        tokens_cache_write=totals["cache_creation"],
        tokens_output=totals["output"],
        tokens_total=inp + totals["output"],
        tokens_reasoning=totals["reasoning"],
        source="harness_log",
        events=evidence.events,
        counting_version=2,
        model_usage=evidence.models,
    )


def _meta_parent_ids(meta: Mapping[str, Any]) -> dict[str, str]:
    """Parent/runtime session ids recorded in a run's meta."""
    return parent_session_ids(
        extra={
            key: meta.get(key)
            for key in (
                "runtime_session_id",
                "vibecrafted_session_id",
                "parent_provider_session_id",
                "fork_source_session_id",
            )
        }
    )


def coerce_exit_code(value: object) -> int | None:
    """Integer exit code from meta (int or digit string), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.lstrip("-").isdigit() else None


def run_telemetry_from_meta(
    meta: Mapping[str, Any], *, transcript: Path | None
) -> dict[str, Any]:
    """Telemetry of an already-closed run, read-only.

    Prefers what the run's own close recorded (``meta.usage``/``cost``/
    ``failure``). A run closed before those fields existed is re-derived from
    its raw transcript and labelled ``transcript(lazy)``; nothing is written
    back to someone else's run.
    """
    from .agent_stream import AgentStreamParser
    from .failure_attribution import attribute_failure

    agent = str(meta.get("agent") or "")
    exit_code = coerce_exit_code(meta.get("exit_code"))
    transcript_path = (
        transcript if transcript is not None and transcript.is_file() else None
    )
    recorded = isinstance(meta.get("usage"), Mapping)

    parser = AgentStreamParser(agent)
    if not recorded and transcript_path is not None:
        with transcript_path.open("rb") as handle:
            for line in handle:
                parser.feed_line(line)

    model_value = str(
        meta.get("agent_model") or meta.get("model") or parser.model_id or ""
    ).strip()
    model: str | dict[str, str] = (
        model_value or Unknown(reason=NO_MODEL_REPORTED_REASON).as_dict()
    )

    if recorded:
        usage_block: dict[str, Any] = dict(meta["usage"])
        cost_block = (
            cost_from_dict(meta["cost"]).as_dict()
            if isinstance(meta.get("cost"), Mapping)
            else CostRecord(
                amount=Unknown(reason="cost not recorded"), source="unknown"
            ).as_dict()
        )
        provider_session = meta.get("provider_session_id") or meta.get(
            "agent_session_id"
        )
        if provider_session in (None, ""):
            provider_session = Unknown(reason=NO_SESSION_REASON).as_dict()
        session_source = str(meta.get("provider_session_source") or "meta")
        telemetry_source = "meta"
    else:
        usage = usage_record(
            parser.usage_events,
            tokens_input=parser.tokens_input,
            tokens_cached_input=parser.tokens_cached_input,
            tokens_cache_write=parser.tokens_cache_write,
            tokens_output=parser.tokens_output,
            source=LAZY_SOURCE,
        )
        usage_block = usage.as_dict()
        cost_block = resolve_cost(
            model_value,
            usage,
            reported_amount=parser.cost_usd,
            reported_source=parser.cost_source,
        ).as_dict()
        candidate, candidate_source = parser.session_id, LAZY_SOURCE
        if not candidate:
            candidate = str(meta.get("agent_session_id") or "")
            candidate_source = "meta"
        resolved, session_source = resolve_provider_session_id(
            candidate, source=candidate_source, parents=_meta_parent_ids(meta)
        )
        provider_session = _project(resolved)
        telemetry_source = LAZY_SOURCE

    started_at = meta.get("started_at") or meta.get("created_at")
    if not started_at and isinstance(provider_session, str) and meta.get("run_id"):
        # Historical supervisor meta omitted start time; reuse its canonical
        # snapshot, but only when both durable records identify the same session.
        from .control_plane import lookup_run_snapshot

        snapshot = lookup_run_snapshot(str(meta["run_id"])) or {}
        if snapshot.get("agent_session_id") == provider_session:
            started_at = snapshot.get("started_at")

    if (
        not usage_block.get("events")
        and isinstance(provider_session, str)
        and not is_unknown(provider_session)
    ):
        resolved, _ = resolve_provider_session_id(
            provider_session, source=session_source, parents=_meta_parent_ids(meta)
        )
        recovered = (
            harness_usage_record(
                agent,
                resolved,
                started_at,
                meta.get("completed_at"),
                model_value,
            )
            if isinstance(resolved, str)
            else None
        )
        if recovered is not None:
            usage_block = recovered.as_dict()
            telemetry_source = "harness_log"
            if cost_block.get("source") != "provider_reported":
                cost_block = resolve_cost(
                    model_value, recovered, reported_amount=None, reported_source=None
                ).as_dict()

    if is_unknown(model) and len(usage_block.get("model_usage") or {}) == 1:
        model = next(iter(usage_block["model_usage"]))
    if cost_block.get("source") == "unknown" and usage_block.get("events"):
        keys = ("tokens_input", "tokens_cached_input", "tokens_output", "tokens_total")
        if all(
            type(usage_block.get(key)) is int and usage_block[key] >= 0 for key in keys
        ):
            measured = UsageRecord(
                tokens_input=usage_block["tokens_input"],
                tokens_cached_input=usage_block["tokens_cached_input"],
                tokens_cache_write=usage_block.get(
                    "tokens_cache_write", Unknown(reason=NO_CACHE_WRITE_REASON)
                ),
                tokens_output=usage_block["tokens_output"],
                tokens_total=usage_block["tokens_total"],
                source=str(usage_block.get("source") or "meta"),
                events=usage_block["events"],
                model_usage=usage_block.get("model_usage"),
            )
            cost_block = resolve_cost(
                str(model) if isinstance(model, str) else "",
                _stream_pricing_usage(measured, agent, model_value),
                reported_amount=None,
                reported_source=None,
            ).as_dict()

    failure_block = (
        meta.get("failure") if isinstance(meta.get("failure"), Mapping) else None
    )
    if (
        failure_block is None
        and exit_code not in (None, 0)
        and not meta.get("operator_stop_accepted")
    ):
        failure = attribute_failure(
            transcript_path, exit_code, agent=agent, source=LAZY_SOURCE
        )
        failure_block = failure.as_dict() if failure is not None else None

    return {
        "run_id": str(meta.get("run_id") or ""),
        "agent": agent,
        "model": model,
        "status": str(meta.get("status") or ""),
        "exit_code": exit_code,
        "usage": usage_block,
        "cost": cost_block,
        "failure": dict(failure_block) if failure_block is not None else None,
        "provider_session_id": provider_session,
        "provider_session_source": session_source,
        "telemetry_source": telemetry_source,
        "unpricedModels": sorted(
            name or "unknown"
            for name in (usage_block.get("model_usage") or {model_value: {}})
            if model_price(name) is None
        ),
    }
