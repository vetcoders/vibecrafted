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
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelPrice:
    """Per-million-token USD rates for one model family, plus a rate source label."""

    input_per_million: float
    cached_input_per_million: float
    output_per_million: float
    source: str


# API-equivalent rates. Provider-reported CLI cost always wins; these rates are
# only a transparent fallback when a stream exposes tokens but no monetary cost.
_PRICES: tuple[tuple[tuple[str, ...], ModelPrice], ...] = (
    (("grok-build", "grok-code-fast"), ModelPrice(1.0, 0.2, 2.0, "xai-api-2026-07")),
    (("gpt-5.6-sol",), ModelPrice(5.0, 0.5, 30.0, "openai-api-2026-07")),
    (("gpt-5.6-terra",), ModelPrice(2.5, 0.25, 15.0, "openai-api-2026-07")),
    (("gpt-5.6-luna",), ModelPrice(1.0, 0.1, 6.0, "openai-api-2026-07")),
    (("gpt-5.5",), ModelPrice(5.0, 0.5, 30.0, "openai-api-2026-07")),
    (("gpt-5.4",), ModelPrice(2.5, 0.25, 15.0, "openai-api-2026-07")),
    (("gpt-5",), ModelPrice(1.25, 0.125, 10.0, "openai-api-2026-07")),
)


def model_price(model: str) -> ModelPrice | None:
    """Look up the ModelPrice whose alias substring-matches *model*, else None."""
    normalized = (model or "").strip().lower()
    for aliases, price in _PRICES:
        if any(alias in normalized for alias in aliases):
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

    @property
    def known(self) -> bool:
        """True when at least one provider usage event was observed."""
        return self.events > 0

    def as_dict(self) -> dict[str, object]:
        """Structured ``usage`` block for meta.json."""
        return {
            "schema": USAGE_SCHEMA,
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

    def meta_fields(self) -> dict[str, object]:
        """Keys merged into meta.json (structured blocks plus flat legacy mirrors)."""
        fields: dict[str, object] = {
            **self.usage.flat(),
            **self.cost.flat(),
            "usage": self.usage.as_dict(),
            "cost": self.cost.as_dict(),
            "provider_session_id": _project(self.provider_session_id),
            "provider_session_source": self.provider_session_source,
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
) -> RunTelemetry:
    """Assemble a RunTelemetry from what a close path observed."""
    provider_session_id, provider_session_source = resolve_provider_session_id(
        session_candidate, source=session_source, parents=parents
    )
    return RunTelemetry(
        usage=usage,
        cost=resolve_cost(
            model,
            usage,
            reported_amount=reported_cost,
            reported_source=reported_cost_source,
        ),
        failure=failure,
        provider_session_id=provider_session_id,
        provider_session_source=provider_session_source,
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
        provider_session = meta.get("provider_session_id")
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
    }
