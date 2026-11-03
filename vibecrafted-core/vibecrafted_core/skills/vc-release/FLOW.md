# `vc-release` Flow

## Flow

```mermaid
flowchart TD
    A[Operator: vibecrafted release codex --prompt 'Prepare the release'] --> B[Read deployment, security, and launch context]
    B --> C[Verify release mechanics and outward ship path]
    C --> D[Produce release checklist and fixes]
    D --> E{Need upstream work first?}
    E -->|packaging gap| F[Escalate to vc-hydrate]
    E -->|surface polish| G[Escalate to vc-decorate]
    E -->|broader ship-readiness audit| H[Escalate to vc-dou]
    E -->|ready| I[Write release report]
    F --> I
    G --> I
    H --> I
```

## Routes

| Entry                         | Args                   | Produces                             | Exit            |
| ----------------------------- | ---------------------- | ------------------------------------ | --------------- |
| `vibecrafted release <agent>` | `--prompt` or `--file` | release report, transcript, and meta | `0` on dispatch |
| `vc-release <agent>`          | same                   | same                                 | `0` on dispatch |
| `vc-release --floor`          | `--require-release`    | public-surface ship gate             | `1` on drift    |

### Escalation edges

- Packaging or onboarding is still unfinished -> `vibecrafted hydrate <agent>`
- Visual release surface needs polish -> `vibecrafted decorate <agent>`
- The product is not actually shippable yet -> `vibecrafted dou <agent>`

### Session artifacts

- Artifact root: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`
- Lock: `$VIBECRAFTED_HOME/locks/<org>/<repo>/<run_id>.lock`
- Outputs: `reports/<timestamp>_<slug>_<agent>.md` with matching `.transcript.log` and `.meta.json`
