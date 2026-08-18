# `vc-canary` Flow

```mermaid
flowchart TD
  A[$PWD] --> B[loct auto + canary_cli atlas]
  B --> C{coverage pass?}
  C -->|no| D[STOP + loctree-fail hak]
  C -->|yes| E[SENSE: planes_hint + hubs → scopes.json]
  E --> F[Fleet: 1 agent per scope]
  F --> G[merge-catalog + diff-audit]
  G --> H{suspicious deletions?}
  H -->|yes| I[examine why + AskUser — no revert]
  H -->|no| J[gates + one commit]
  I --> J
  J --> K[findings cross-check]
  K --> L[report → discuss → decide]
```

## Kontrakt faz

| Faza     | Pytanie                                 | Wyjście                          |
| -------- | --------------------------------------- | -------------------------------- |
| Atlas    | Czy inwentarz jest kompletny z receipt? | `.loctree/atlas/*`               |
| Sense    | Które planes?                           | `scopes.json` + briefy           |
| Fleet    | Czy każdy scope dowiózł katalog?        | `catalogs/<id>.json`             |
| Settle   | Czy można bezpiecznie commitować?       | jeden commit albo hold operatora |
| Findings | Co jest potwierdzonym sygnałem?         | `findings.json` + raport         |
