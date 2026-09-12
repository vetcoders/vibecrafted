# Przepływ `vc-canary`

```mermaid
flowchart TD
  A[$PWD] --> B[Phase 0: HEAD + dirty + worktrees + atlas --refresh]
  B --> C{coverage pass?}
  C -->|no| D[STOP + loctree-fail hak]
  C -->|yes| E[Phase I: repo-view / focus / twins / crowd / hotspots]
  E --> F[candidate decision axes]
  F --> G[Phase II: per axis — find --discover → occurrences → body → slice → follow]
  G --> H[pair verdicts: SAME / VARIANT / DRIFTED / BYPASS / FALSE]
  H --> I[Phase III: prism + writer/arbiter/observer/projection]
  I --> J[findings: CUT_BLOCKER / CUT_COHERENT / FOLLOW_UP / OBSERVATION]
  J --> V[werdykt przebiegu: AXES_CLOSED_CANDIDATE / AXES_OPEN / INSTRUMENT_INCOMPLETE / LAUNCHER_CONTRACT_CONFLICT]
  V --> K[append .loctree/canary/JOURNAL.md]
  K --> L[raport + BUILD/LINT/TEST/RUNTIME=NOT_ASSESSED → discuss → decide — zero mutacji kodu]
```

## Kontrakt faz

| Faza | Pytanie                                                     | Wyjście                                  |
| ---- | ----------------------------------------------------------- | ---------------------------------------- |
| 0    | Czy drzewo jest świeże i w pełni pokryte, z pokwitowaniami? | zapis HEAD/dirty/worktree + atlas        |
| I    | Które klasy prawdy mogą mieć >1 autora?                     | kandydackie osie decyzji                 |
| II   | Gdzie każda decyzja naprawdę żyje — udowodnione?            | werdykty par + falsyfikacja nieobecności |
| III  | Kto jest writerem / arbitrem / obserwatorem / projekcją?    | sklasyfikowane findingi + wynik prism    |
| QC   | Co decyduje operator?                                       | dopisany wpis journala + raport          |
