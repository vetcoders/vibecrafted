# Przepływ `vc-hydrate`

## Flow

```mermaid
flowchart TD
    A[Operator: vibecrafted hydrate codex --prompt 'Package the product'] --> B[Read DoU findings and current external surface]
    B --> C[Add packaging, onboarding, SEO, and listing truth]
    C --> D[Verify install path and market-facing docs]
    D --> E{Need release mechanics or polish?}
    E -->|release| F[Escalate to vc-release]
    E -->|visual polish| G[Escalate to vc-decorate]
    E -->|done| H[Write hydration report]
    F --> H
    G --> H
```

## Trasy

| Wejście                       | Argumenty               | Produkuje                                                     | Wyjście            |
| ----------------------------- | ----------------------- | ------------------------------------------------------------- | ------------------ |
| `vibecrafted hydrate <agent>` | `--prompt` lub `--file` | zestaw docs/pakietu/raportu po hydrate plus transkrypt i meta | `0` przy dispatchu |
| `vc-hydrate <agent>`          | to samo                 | to samo                                                       | `0` przy dispatchu |
| `vc-hydrate --floor`          | `--product`, `--json`   | wersja checkoutu, instalacja README, marka                    | `1` przy rozjeździe |

### Krawędzie eskalacji

- Finalna praca nad dowiezieniem na zewnątrz -> `vibecrafted release <agent>`
- Przebieg spójności wizualnej na powierzchni zewnętrznej -> `vibecrafted decorate <agent>`
- Potrzebny szerszy audyt luk w powierzchni produktu -> `vibecrafted dou <agent>`

### Artefakty sesji

- Katalog artefaktów: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`
- Lock: `$VIBECRAFTED_HOME/locks/<org>/<repo>/<run_id>.lock`
- Wyjścia: `reports/<timestamp>_<slug>_<agent>.md` z odpowiadającymi `.transcript.log` i `.meta.json`
