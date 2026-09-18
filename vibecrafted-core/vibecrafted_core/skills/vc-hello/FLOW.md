# `vc-hello` Flow

## Flow

```mermaid
flowchart TD
    A[Operator: vc-hello — onboard new CLI / drift audit] --> B[fleet_scan.py scan — posture + consensus JSON]
    B --> C{Mode?}
    C -->|onboarding| D[Fetch target CLI official config docs]
    D --> E[Map consensus to documented target keys; skip CLI-specific and secret-bearing items with reasons]
    E --> F[Smoke-test carried-over hooks against target payload format]
    F --> G[Apply: candidate copy -> native validator -> timestamped backup -> overwrite]
    G --> H[Parity report: per-axis match / divergent / no-key + unverified list]
    C -->|drift audit| I[fleet_scan.py diff --target X — read-only divergence report]
    I --> H
    H --> J{Gaps need code?}
    J -->|yes| K[Hand off bounded brief to vc-implement]
    J -->|posture dispute| L[Escalate to Founder]
    J -->|no| M[Done — reload instruction to operator]
```

## Routes

| Entry                   | Args                                       | Produces                                  | Exit                  |
| ----------------------- | ------------------------------------------ | ----------------------------------------- | --------------------- |
| `vc-hello` (in-session) | target CLI name or "drift <cli>"           | posture.json + parity report / drift.json | `0` on report written |
| `fleet_scan.py scan`    | `--output FILE`                            | fleet posture + consensus JSON            | `0` / `1` on error    |
| `fleet_scan.py diff`    | `--target CLI --output FILE [--posture F]` | per-axis drift report                     | `0` / `1` on error    |

### Escalation edges

- Missing hook script / MCP server that must be built → `vc-implement`.
- Fleet posture conflict without majority (2:2) → Founder decision, recorded in the report.
- New human onboarding beyond configs → `references/onboarding-stack.md` + vibecraftsmanship skill.
