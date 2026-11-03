# `vc-hydrate` Flow

## Flow

```mermaid
flowchart TD
    A[Operator: vibecrafted hydrate codex --prompt 'Package the product'] --> B[Read DoU findings and current external surface]
    B --> C[Add packaging, onboarding, SEO, and listing truth]
    C --> D[Verify install path and market-facing docs]
    D --> E{Need release mechanics or polish?}
    E -->|release| F[Escalate to vc-release]
    E -->|visual polish| G[Escalate to vc-decorate]
    E -->|done| H[Write hydration report]
    F --> H
    G --> H
```

## Routes

| Entry                         | Args                   | Produces                                                  | Exit            |
| ----------------------------- | ---------------------- | --------------------------------------------------------- | --------------- |
| `vibecrafted hydrate <agent>` | `--prompt` or `--file` | hydrated docs/package/report set plus transcript and meta | `0` on dispatch |
| `vc-hydrate <agent>`          | same                   | same                                                      | `0` on dispatch |
| `vc-hydrate --floor`          | `--product`, `--json`  | checkout version, README install, brand                   | `1` on drift    |

### Escalation edges

- Final outward ship work -> `vibecrafted release <agent>`
- Visual coherence pass on the outward surface -> `vibecrafted decorate <agent>`
- Bigger product-surface gap audit needed -> `vibecrafted dou <agent>`

### Session artifacts

- Artifact root: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`
- Lock: `$VIBECRAFTED_HOME/locks/<org>/<repo>/<run_id>.lock`
- Outputs: `reports/<timestamp>_<slug>_<agent>.md` with matching `.transcript.log` and `.meta.json`
