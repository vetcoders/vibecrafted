# Przepływ `vc-intents`

## Flow

```mermaid
flowchart TD
    A[/vc-intents — 'co obiecaliśmy i nie dowieźliśmy?' + okno/] --> B[vc-init: prawda repo]
    B --> C[0 · tożsamość: aicx intents -p /repo; najnowsza intencja vs najnowszy commit]
    C --> D[1 · sweep: katalogi transkrypcji + aicx w oknie → pokrycie, shardy]
    D --> E[2 · plan --stage extract → flota vc-dispatch czyta shardy]
    E --> F[verify-quotes: dosłowne albo odrzucone → intents.json]
    F --> G[3–4 · głosy i chronologia: kind, superseded_by, contradiction]
    G --> H[5 · tory po obszarze → plan --stage confront na hoście A i hoście B]
    H --> I[merge: LEDGER.json + replication-report.json]
    I --> J[6 · reclassify: complaint∧landed, dowód tylko z dokumentu → overrides.jsonl]
    J --> K[7 · render --html → Founder przegląda → decide --import-file]
    K --> L[8 · queue: stabilne ∧ otwarte ∧ mocne, uporządkowane]
    L --> M[plan --stage implement → cięcia codex przez vc-dispatch]
    M --> N{status}
    N -->|OPEN| K
    N -->|CLOSED| O[Cel zamknięty: pokrycie rozliczone, nic otwartego, landed zweryfikowane w runtime]
```

## Trasy

| Wejście                                   | Argumenty                                        | Produkuje                                               | Wyjście               |
| ----------------------------------------- | ------------------------------------------------ | ------------------------------------------------------- | --------------------- |
| `/vc-intents`                             | okno (`--since/--until` albo temat), repo z cwd  | workdir z ledgerem, kolejką, planami, stroną przeglądu  | interaktywnie         |
| `vibecrafted intents <agent>`             | jeden tor: JSON sharda albo toru, ścieżka briefu | `<shard>.json` albo `<tor>.verdicts.json`, raport       | `0` po dispatchu      |
| `intents_cli.py <workdir> plan --stage …` | `extract` · `confront` · `implement`             | plan `vibecrafted.dispatch.v1` pod `<artifacts>/plans/` | `0`                   |
| `intents_cli.py <workdir> status`         | —                                                | liczniki, powody otwarcia                               | `0` CLOSED · `1` OPEN |

### Krawędzie eskalacji

- Dwa moduły konkurują o prawdę, której intencja potrzebuje → `vibecrafted canary <agent>` (potwierdź w kodzie; canary nigdy nie refaktoruje)
- Kolejka zapisana → `vibecrafted dispatch <intents-implement.*.dispatch.toml>` (domyślnie codex)
- Worker twierdzi, że cięcie wylądowało → `vibecrafted trust <agent>`, zanim ledger powie `landed`
- Sprzeczność dwu wypowiedzi Foundera → `aicx clarify`, potem Founder
- Wejściem jest spisany plan, nie korpus → `vibecrafted audit <agent>`

### Artefakty sesji

- Workdir: `~/.vibecrafted/artifacts/<owner>/<repo>/intents/` — `sweep.json`, `shards/`, `intents.json`,
  `lanes/`, `LEDGER.json`, `LEDGER.md`, `overrides.jsonl`, `replication-report.json`, `queue.json`,
  `STABLE-QUEUE.md`, `intents.html`
- Plany: `~/.vibecrafted/artifacts/<owner>/<repo>/<RRRR_MMDD>/plans/intents-{extract,confront,implement}.<host>.dispatch.toml`
- Werdykty floty: `<artifacts>/reports/verdicts-<host>/<tor>.verdicts.json`
- Haki narzędzi: `~/.vibecrafted/loctree/loctree-fail.md`, `~/.vibecrafted/aicx/aicx-fail.md` — append-only; hak to twierdzenie o narzędziu, potwierdź zanim dopiszesz

### Powrót po cięciu lub kompakcji

`status` → przeczytaj `LEDGER.md` → `queue` → `plan --stage implement`. Nie
powtarzaj sweepu ani ekstrakcji; oba są na dysku i drogie.
