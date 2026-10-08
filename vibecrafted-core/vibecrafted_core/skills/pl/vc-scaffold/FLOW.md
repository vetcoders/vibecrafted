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

Scaffold to **wejście WRITE** w cadence read/write VC-ship
(Scaffold→Implement→Review→Workflow→Follow-up→Marbles→Audit→Polarize→Dou→Hydrate→Release).
Każdy WRITE zostawia artefakt; następny READ go falsyfikuje. Zobacz `references/cadence.md`.

## Routes

| Wejście                        | Argumenty               | Produkuje                                            | Wyjście            |
| ------------------------------ | ----------------------- | ---------------------------------------------------- | ------------------ |
| `vibecrafted scaffold <agent>` | `--prompt` lub `--file` | plan scaffoldu (z kolumną `state`), transkrypt, meta | `0` przy dispatchu |
| `vc-scaffold <agent>`          | jak wyżej               | jak wyżej                                            | `0` przy dispatchu |

### Escalation edges

- Pojedyncze cięcie gotowe do ship WRITE -> ograniczona komórka `vibecrafted implement <agent>` lub `workflow`; praca promptowa z postawą na pierwszym miejscu -> `vibecrafted justdo <agent>`
- Wielofalowy dispatch -> zwaliduj `vibecrafted.dispatch.v1` przez `vibecrafted dispatch <path> --doctor`, potem przekaż artefakt do `/vc-ship` A→Z
- Ręczne workflow per task -> wyłącznie awaryjny fallback; zapisz awarię supervisora i dowód oddania kontroli
- Wciąż potrzebne wspólne sterowanie -> `vibecrafted partner <agent>`
- Repo już istnieje i potrzebuje prawdy przed planowaniem -> `vibecrafted init <agent>`

### Session artifacts

- Korzeń artefaktów: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/` — `<org>/<repo>`
  z tożsamości git remote, nigdy ze ścieżki checkoutu. Trwałe artefakty NIGDY nie
  lądują w `/tmp` ani w checkoucie produktu; drzewo niesie wyłącznie kod i
  dokumentację produktu.
- Lock: `$VIBECRAFTED_HOME/locks/<org>/<repo>/<run_id>.lock`
- Wyjścia: `reports/<timestamp>_<slug>_<agent>.md` z pasującymi `.transcript.log` i `.meta.json`
