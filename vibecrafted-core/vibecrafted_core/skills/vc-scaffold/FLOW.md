# `vc-scaffold` Flow

## Flow

```mermaid
flowchart TD
    A[Operator: vibecrafted scaffold claude --prompt 'Plan this'] --> G[Canonical Orientation Gate: vc-init + loctree — HARD-BLOCK]
    G --> I{Founder interview evidence?}
    I -->|journal / AICX / brief| O[1. Orient: map landscape + constraint space]
    I -->|none| Q[Ask founder before shaping]
    Q --> O
    O --> F[2. Falsify: try to break the founding assumption]
    F --> S[3. Shape: decisions · scope · product identity · output shape by scale]
    S --> D[4. Defend: agent-sized cuts, each with Vector + state + delivery-verifier]
    D --> H[5. Handoff: plan + briefs + validated .dispatch.toml]
    H --> E{What next?}
    E -->|single cut| W[vc-implement / vc-workflow cell consumes its brief]
    E -->|multi-cut A→Z| OP[/vc-ship consumes .dispatch.toml]
    E -->|shared steering| P[vc-partner]
    E -->|plan only| R[Write scaffold report]
```

## Cadence position

Scaffold is the **WRITE entry** of the VC-ship read/write cadence
(Scaffold→Implement→Review→Workflow→Follow-up→Marbles→Audit→Polarize→Dou→Hydrate→Release).
Each WRITE leaves an artifact; the next READ falsifies it. See `references/cadence.md`.

## Routes

| Entry                          | Args                   | Produces                                              | Exit            |
| ------------------------------ | ---------------------- | ----------------------------------------------------- | --------------- |
| `vibecrafted scaffold <agent>` | `--prompt` or `--file` | scaffold plan (with `state` column), transcript, meta | `0` on dispatch |
| `vc-scaffold <agent>`          | same                   | same                                                  | `0` on dispatch |

### Escalation edges

- Single cut ready for ship WRITE execution -> a bounded `vibecrafted implement <agent>` or `workflow` cell; posture-first prompt work -> `vibecrafted justdo <agent>`
- Conduct a multi-wave dispatch -> validate `vibecrafted.dispatch.v1` with `vibecrafted dispatch <path> --doctor`, then hand the artifact to `/vc-ship` A→Z
- Manual per-task workflow launch -> emergency fallback only; record the supervisor failure and return-control evidence
- Shared steering still needed -> `vibecrafted partner <agent>`
- Repo already exists and needs truth before planning -> `vibecrafted init <agent>`

### Session artifacts

- Artifact root: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`
- Lock: `$VIBECRAFTED_HOME/locks/<org>/<repo>/<run_id>.lock`
- Outputs: `reports/<timestamp>_<slug>_<agent>.md` with matching `.transcript.log` and `.meta.json`
