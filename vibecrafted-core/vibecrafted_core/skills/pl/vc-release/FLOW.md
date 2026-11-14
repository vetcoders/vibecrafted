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

## Trasy

| Wejście                       | Argumenty               | Produkuje                           | Wyjście            |
| ----------------------------- | ----------------------- | ----------------------------------- | ------------------ |
| `vibecrafted release <agent>` | `--prompt` lub `--file` | raport release'u, transcript i meta | `0` przy dispatchu |
| `vc-release <agent>`          | to samo                 | to samo                             | `0` przy dispatchu |
| `vc-release --floor`          | `--require-release`     | bramka wydania powierzchni publicznej | `1` przy rozjeździe |

### Krawędzie eskalacji

- Pakowanie lub onboarding wciąż niedokończone -> `vibecrafted hydrate <agent>`
- Wizualna powierzchnia release'u wymaga dopieszczenia -> `vibecrafted decorate <agent>`
- Produkt faktycznie nie jest jeszcze gotowy do dowiezienia -> `vibecrafted dou <agent>`

### Artefakty sesji

- Root artefaktów: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`
- Lock: `$VIBECRAFTED_HOME/locks/<org>/<repo>/<run_id>.lock`
- Wyjścia: `reports/<timestamp>_<slug>_<agent>.md` z pasującymi `.transcript.log` i `.meta.json`
