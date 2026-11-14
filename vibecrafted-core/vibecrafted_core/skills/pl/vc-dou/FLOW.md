# Przepływ `vc-dou`

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

## Trasy

| Wejście                   | Argumenty               | Produkuje                               | Wyjście            |
| ------------------------- | ----------------------- | --------------------------------------- | ------------------ |
| `vibecrafted dou <agent>` | `--prompt` lub `--file` | raport audytu DoU z transkryptem i meta | `0` przy dispatchu |
| `vc-dou <agent>`          | to samo                 | to samo                                 | `0` przy dispatchu |
| `vc-dou --floor`          | `--product`, `--json`   | raport powierzchni publicznej           | `1` przy rozjeździe |

### Krawędzie eskalacji

- Luki w pakowaniu, onboardingu lub SEO -> `vibecrafted hydrate <agent>`
- Niespójność interakcji lub wizualna -> `vibecrafted decorate <agent>`
- Findingi dotyczące deploymentu lub ryzyka launchu -> `vibecrafted release <agent>`

### Artefakty sesji

- Katalog artefaktów: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`
- Lock: `$VIBECRAFTED_HOME/locks/<org>/<repo>/<run_id>.lock`
- Wyjścia: `reports/<timestamp>_<slug>_<agent>.md` z odpowiadającymi `.transcript.log` i `.meta.json`
