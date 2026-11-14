# `vc-dou` Flow

## Flow

```mermaid
flowchart TD
    A[Operator: vibecrafted dou claude --prompt 'Audit launch readiness'] --> B[Audit repo, runtime, onboarding, and discoverability]
    B --> C[Build Definition of Undone matrix]
    C --> D{What kind of gap dominates?}
    D -->|product packaging| E[Escalate to vc-hydrate]
    D -->|surface polish| F[Escalate to vc-decorate]
    D -->|release mechanics| G[Escalate to vc-release]
    D -->|direct findings only| H[Write audit report]
    E --> H
    F --> H
    G --> H
```

## Routes

| Entry                     | Args                   | Produces                                  | Exit            |
| ------------------------- | ---------------------- | ----------------------------------------- | --------------- |
| `vibecrafted dou <agent>` | `--prompt` or `--file` | DoU audit report with transcript and meta | `0` on dispatch |
| `vc-dou <agent>`          | same                   | same                                      | `0` on dispatch |
| `vc-dou --floor`          | `--product`, `--json`  | public-surface report                     | `1` on drift    |

### Escalation edges

- Packaging, onboarding, or SEO gaps -> `vibecrafted hydrate <agent>`
- Interaction or visual incoherence -> `vibecrafted decorate <agent>`
- Deployment or launch-risk findings -> `vibecrafted release <agent>`

### Session artifacts

- Artifact root: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`
- Lock: `$VIBECRAFTED_HOME/locks/<org>/<repo>/<run_id>.lock`
- Outputs: `reports/<timestamp>_<slug>_<agent>.md` with matching `.transcript.log` and `.meta.json`
