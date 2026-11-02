# Pricing snapshots

Deflated JSON embedded by `src/usage_ccusage.rs` and handed to
`ccusage_core::pricing::set_embedded_snapshots`.

| File                                    | Source                                                                              |
| --------------------------------------- | ----------------------------------------------------------------------------------- |
| `litellm-pricing.json.deflate`          | BerriAI/litellm `187f936cb3457cb8cdfbf541593588929d502bf4`, compacted               |
| `models-dev-pricing.json.deflate`       | ccusage `c4177eeb6f037bcfcdfe948c78699fb437399b41`, `ccusage-pricing-snapshots/src` |
| `models-dev-catalog-rules.json.deflate` | same ccusage revision                                                               |

Refresh with `refresh_pricing.py <litellm-rev> <ccusage-pricing-snapshots/src>`
together with the ccusage `rev` in `Cargo.toml` and the expected values in
`tests/usage_ccusage.rs`.
