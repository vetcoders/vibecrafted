"""Offline price-catalog contract. No test here opens the network."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from vibecrafted_core import price_catalog, telemetry


def test_terra_and_luna_come_from_the_pin_without_network(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("quote opened the network")

    monkeypatch.setattr(price_catalog.urllib.request, "urlopen", boom)
    terra = price_catalog.quote_usage(
        "gpt-6.1-terra",
        fresh_input=200,
        cache_read=800,
        cache_write=0,
        output=50,
        reasoning=20,
        cache_accounting="subset_of_input",
    )
    # 200*1 + 800*0.05 + 50*5 = 490 micro-dollars. Reasoning is not added.
    assert terra["cost_usd"] == pytest.approx(0.00049)
    assert terra["cost_source"] == "price_table(openrouter@2026-10-28)"
    assert terra["cost_basis"] == "api-equiv"
    assert terra["components"]["reasoning_usd"] == pytest.approx(0.0001)
    assert terra["cost_usd"] != pytest.approx(0.00129)

    luna = price_catalog.quote_usage(
        "gpt-6-luna",
        fresh_input=1000,
        cache_read=0,
        cache_write=0,
        output=0,
        reasoning=0,
        cache_accounting="subset_of_input",
    )
    assert luna["cost_usd"] == pytest.approx(0.0001)
    assert luna["cost_source"] == terra["cost_source"]


def test_named_miss_is_unmapped_and_negative_cache_is_unknown() -> None:
    missing = price_catalog.quote_usage(
        "not-a-real-model",
        fresh_input=10,
        cache_read=0,
        cache_write=0,
        output=1,
        reasoning=0,
        cache_accounting="subset_of_input",
    )
    assert missing["cost_source"] == "unmapped"
    assert missing["cost_usd"] == "unknown"
    assert missing["cost_usd"] != 0

    overflow = price_catalog.quote_usage(
        "gpt-6.1-terra",
        fresh_input=-1,
        cache_read=800,
        cache_write=0,
        output=50,
        reasoning=0,
        cache_accounting="subset_of_input",
    )
    assert overflow["cost_source"] == "unknown"
    assert "cache exceeds input" in overflow["cost_reason"]


def test_fresh_cache_skips_fetch_and_failure_keeps_the_pin(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("VIBECRAFTED_PRICE_OFFLINE", raising=False)
    cache = tmp_path / "prices.json"
    cache.write_text(
        '{"fetched_at":"2026-10-28T22:00:00Z","source":"openrouter","models":{}}\n',
        encoding="utf-8",
    )

    def boom():
        raise AssertionError("fresh cache fetched")

    assert (
        price_catalog.refresh_prices(
            now=datetime(2026, 10, 28, 23, 0, tzinfo=UTC),
            cache_file=cache,
            fetcher=boom,
        )
        == "fresh"
    )

    def down():
        raise TimeoutError("down")

    stale = tmp_path / "stale.json"
    assert price_catalog.refresh_prices(cache_file=stale, fetcher=down) == "offline"
    assert not stale.exists()
    monkeypatch.setenv("VIBECRAFTED_PRICE_OFFLINE", "1")
    assert (
        price_catalog.refresh_prices(cache_file=tmp_path / "offline.json", fetcher=down)
        == "offline"
    )


def test_codex_terra_receipt_uses_subset_math_and_names_the_table() -> None:
    usage = telemetry.usage_record(
        1,
        tokens_input=1000,
        tokens_cached_input=800,
        tokens_cache_write=0,
        tokens_output=50,
        source="provider_stream",
    )
    result = telemetry.build_run_telemetry(
        usage=usage,
        model="gpt-6.1-terra",
        reported_cost=None,
        reported_cost_source=None,
        session_candidate="sess-terra",
        session_source="provider_stream",
        parents={},
        failure=None,
        agent="codex",
    )
    front = result.frontmatter_fields()
    assert front["cost_usd"] != "unknown"
    assert float(front["cost_usd"]) == pytest.approx(0.00049)
    assert front["cost_source"] == "price_table(openrouter@2026-10-28)"
    assert front["cost_basis"] == "api-equiv"
    assert result.usage is usage

    blank = replace(
        usage, model_usage={"": {"fresh_input": 200, "cache_read": 800, "output": 50}}
    )
    retargeted = telemetry.build_run_telemetry(
        usage=blank,
        model="gpt-6.1-terra",
        reported_cost=None,
        reported_cost_source=None,
        session_candidate="sess-terra",
        session_source="provider_stream",
        parents={},
        failure=None,
        agent="codex",
    )
    assert retargeted.usage is blank
    assert "" in (blank.model_usage or {})
    assert retargeted.cost.source == "price_table(openrouter@2026-10-28)"
    assert retargeted.cost.amount == pytest.approx(0.00049)
