# Pricing snapshots

Deflated JSON embedded by `src/usage_ccusage.rs` and handed to
`ccusage_core::pricing::set_embedded_snapshots`.

| File                                    | Source                                                                              |
| --------------------------------------- | ----------------------------------------------------------------------------------- |
| `litellm-pricing.json.deflate`          | BerriAI/litellm `187f936cb3457cb8cdfbf541593588929d502bf4`, compacted               |
| `models-dev-pricing.json.deflate`       | ccusage `c4177eeb6f037bcfcdfe948c78699fb437399b41`, `ccusage-pricing-snapshots/src` |
| `models-dev-catalog-rules.json.deflate` | same ccusage revision                                                               |

Refresh with `refresh_pricing.py <model_prices_and_context_window.json> <ccusage-pricing-snapshots/src>`
(the first file fetched from the LiteLLM revision above) together with the ccusage
`rev` in `Cargo.toml` and the expected values in `tests/usage_ccusage.rs`.

## Where the report deliberately differs from `npx ccusage`

`tests/fixtures/ccusage/oracle/` holds unchanged output of the matching release. The
report differs from it in three places on cost, each pinned in `tests/usage_ccusage.rs`:

- Kimi `k3`: upstream resolves it fuzzily to `kimi-k3-fast`, 1.5 times Moonshot's price.
- Copilot shutdown aggregates: the context tier tests a whole session's sum, not a
  request. 73 requests averaging about 124K input stay under the 272K tier, so base
  rates apply.
- A session with any model no list prices: upstream counts that model as 0 and sums the
  rest, the report says unknown for the whole session. Totals carry the known sum and
  the count of unknown sessions.

Token counts match upstream. Session counts can differ in two ways:

- A session that recorded no tokens stays in the report. Upstream leaves it out.
- One session id found in two places is one row. Upstream can print a row per place.
