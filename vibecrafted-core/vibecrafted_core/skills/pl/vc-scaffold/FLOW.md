# `vc-scaffold` Flow

## Flow

```mermaid
flowchart TD
    A[Operator: vibecrafted scaffold claude --prompt 'Plan this'] --> G[Canonical Orientation Gate: vc-init + loctree — HARD-BLOCK]
    G --> O[1. Orient: map landscape + constraint space]
    O --> F[2. Falsify: try to break the founding assumption]
    F --> S[3. Shape: decisions · scope · product identity · output shape by scale]
    S --> D[4. Defend: agent-sized cuts, each with Vector + state + delivery-verifier]
    D --> H[5. Handoff: plan with a state column]
    H --> E{What next?}
    E -->|WRITE phase| W[vc-implement / vc-workflow consume the plan]
    E -->|conduct dispatch| OP[vc-operator reads state column → trigger/stop]
    E -->|shared steering| P[vc-partner]
    E -->|plan only| R[Write scaffold report]
```

## Pozycja w cadence

Scaffold to **wejście WRITE** w cadence read/write VC-ship
(Scaffold→Implement→Review→Workflow→Followup→Marbles→Audit→Polarize→Dou→Hydrate→Release).
Każdy WRITE zostawia artefakt; następny READ go falsyfikuje. Zobacz `references/cadence.md`.

## Trasy

| Wejście                        | Argumenty               | Produkuje                                            | Wyjście            |
| ------------------------------ | ----------------------- | ---------------------------------------------------- | ------------------ |
| `vibecrafted scaffold <agent>` | `--prompt` lub `--file` | plan scaffoldu (z kolumną `state`), transkrypt, meta | `0` przy dispatchu |
| `vc-scaffold <agent>`          | jak wyżej               | jak wyżej                                            | `0` przy dispatchu |

### Krawędzie eskalacji

- Plan gotowy do wykonania ship WRITE -> `vibecrafted implement <agent>` lub `workflow`; praca promptowa z postawą na pierwszym miejscu -> `vibecrafted justdo <agent>`
- Poprowadzenie wielofalowego dispatchu -> postawa `$vc-operator` plus `vibecrafted dispatch` lub lane'y workflow
- Wciąż potrzebne wspólne sterowanie -> `vibecrafted partner <agent>`
- Repo już istnieje i potrzebuje prawdy przed planowaniem -> `vibecrafted init <agent>`

### Artefakty sesji

- Korzeń artefaktów: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`
- Lock: `$VIBECRAFTED_HOME/locks/<org>/<repo>/<run_id>.lock`
- Wyjścia: `reports/<timestamp>_<slug>_<agent>.md` z pasującymi `.transcript.log` i `.meta.json`
