#!/usr/bin/env python3
"""Rebuild the deflated pricing snapshots control-core embeds for ccusage.

ccusage-core inflates raw-deflate JSON handed to `set_embedded_snapshots`. Its
own snapshot crate needs a network fetch or a build-time environment variable,
so these bytes are committed and the build stays offline and reproducible.
Compaction mirrors `compact_pricing_json` of ccusage-pricing-snapshots/build.rs.

Usage: refresh_pricing.py <model_prices_and_context_window.json> <path to
ccusage-pricing-snapshots/src>
"""

from __future__ import annotations

import json
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIELDS = (
    ("input_cost_per_token", "i"),
    ("output_cost_per_token", "o"),
    ("cache_creation_input_token_cost", "cc"),
    ("cache_read_input_token_cost", "cr"),
    ("input_cost_per_token_above_200k_tokens", "ia"),
    ("output_cost_per_token_above_200k_tokens", "oa"),
    ("cache_creation_input_token_cost_above_200k_tokens", "cca"),
    ("cache_read_input_token_cost_above_200k_tokens", "cra"),
    ("max_input_tokens", "ctx"),
)
PREFIXES = (
    "claude-",
    "anthropic.",
    "anthropic/",
    "us.anthropic.",
    "eu.anthropic.",
    "global.anthropic.",
    "jp.anthropic.",
    "au.anthropic.",
    "gpt-",
    "o3-",
    "o4-mini-",
    "openai/",
    "azure/",
    "zai/",
    "openrouter/openai/",
)
EXACT = {"o3", "o4-mini"}


def embedded(model: str) -> bool:
    return model in EXACT or model.startswith(PREFIXES)


def compact(raw: dict) -> dict:
    out = {}
    for model, pricing in raw.items():
        if not embedded(model) or not isinstance(pricing, dict):
            continue
        fields = {
            target: pricing[source]
            for source, target in FIELDS
            if pricing.get(source) is not None
        }
        tiers = pricing.get("provider_specific_entry")
        for tier in ("fast", "flex"):
            if isinstance(tiers, dict) and tiers.get(tier) is not None:
                fields[tier] = tiers[tier]
        if "i" in fields and "o" in fields:
            out[model] = fields
    return out


def deflate(value: object) -> bytes:
    text = json.dumps(value, separators=(",", ":"), sort_keys=True)
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    return packer.compress(text.encode()) + packer.flush()


def main(litellm: Path, snapshots: Path) -> None:
    outputs = {
        "litellm-pricing.json.deflate": compact(json.loads(litellm.read_text())),
        "models-dev-pricing.json.deflate": json.loads(
            (snapshots / "models-dev-pricing.json").read_text()
        ),
        "models-dev-catalog-rules.json.deflate": json.loads(
            (snapshots / "models-dev-catalog-rules.json").read_text()
        ),
    }
    for name, value in outputs.items():
        (HERE / name).write_bytes(deflate(value))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
