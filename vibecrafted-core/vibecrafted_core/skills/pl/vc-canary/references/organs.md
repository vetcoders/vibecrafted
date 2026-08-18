# Organy Loctree dla canary

## Trzy organy, jedno `loct auto`

| Organ     | Na dysku (cache) | Projekcja CLI                                       | Faza canary        |
| --------- | ---------------- | --------------------------------------------------- | ------------------ |
| Sense     | `agent.json`     | `loct repo-view --json`                             | planes / scopes    |
| Inventory | `snapshot.json`  | stream → `inventory.jsonl` przez `canary_cli atlas` | budżety unitów     |
| Signals   | `findings.json`  | `atlas/signals.json`                                | findings po flocie |

## Nigdy

- `loct context --full` → `structural.files` jako pełny inwentarz (ranking hubów)
- Zahardkodowane `~/Library/Caches/loctree/projects/<id>/...` w promptach
- Ładowanie całego `snapshot.json` do LLM-a

## Rozwiązanie ścieżki cache bez hardkodu

```bash
canary_cli snapshot-path --root "$PWD"
# uses agent.json project field match + prefers pack named latest
```

## Coverage receipt

`atlas/coverage.json` musi pokazać `snapshot_load_ratio >= 0.95` (pełne załadowanie
snapshotu) i udokumentować filtry inwentarza (tests/generated/lang). Przy load ratio
działa fail closed.
