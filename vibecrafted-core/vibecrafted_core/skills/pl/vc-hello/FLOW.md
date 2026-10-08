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

| Entry                   | Args                                       | Produces                                  | Exit                     |
| ----------------------- | ------------------------------------------ | ----------------------------------------- | ------------------------ |
| `vc-hello` (in-session) | nazwa CLI celu lub "drift <cli>"           | posture.json + raport parity / drift.json | `0` po zapisaniu raportu |
| `fleet_scan.py scan`    | `--output FILE`                            | postawa floty + konsensus JSON            | `0` / `1` przy błędzie   |
| `fleet_scan.py diff`    | `--target CLI --output FILE [--posture F]` | raport dryfu każdej osi                   | `0` / `1` przy błędzie   |

### Escalation edges

- Brak skryptu hooka / serwera MCP, który trzeba zbudować → `vc-implement`.
- Konflikt postawy floty bez większości (2:2) → decyzja Foundera zapisana w raporcie.
- Onboarding człowieka szerszy niż konfiguracja → `references/onboarding-stack.md` + skill vibecraftsmanship.
