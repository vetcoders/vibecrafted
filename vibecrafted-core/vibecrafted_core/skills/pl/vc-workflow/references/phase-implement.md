# Faza 3: IMPLEMENT — delegacja agentów z kontekstem

## Cel

Przełóż Examination + Research na precyzyjne plany agentów.
Spawnuj agentów, którzy dziedziczą pełny kontekst pipeline'u.
Kluczowy wyróżnik: agenci dostają strukturalną inteligencję, nie tylko opisy tasków.

## Reguła instrukcji loctree

**Potwierdzone benchmarkiem**: agenci poinstruowani, by używać loctree MCP, osiągają 98% kompletności tasku
wobec 85% bez instrukcji. To NIE jest opcjonalne.

Każdy plan agenta MUSI zawierać tę preambułę:

```
## Structural Intelligence (loctree MCP)

Use loctree MCP tools as your primary exploration layer:
- `repo-view(project)` first for codebase overview
- `slice(file)` before modifying any file — understand dependencies + consumers
- `find(name)` before creating new types/functions — avoid duplicates
- `impact(file)` before deleting or major refactoring — know the blast radius
- `focus(directory)` to understand a module before changing it

Never edit code without mapping it first.
Grep/rg is for local detail only — after structural mapping.
```

## Konstrukcja planu agenta

### Z artefaktów pipeline'u

Każdy plan agenta powinien zawierać odpowiednie sekcje z:

1. **CONTEXT.md** (Examination):

   - Pliki krytyczne istotne dla scope'u agenta
   - Mapa ryzyka dla plików, których agent dotknie
   - Istniejące symbole do ponownego użycia

2. **RESEARCH.md** (Research):
   - Sekcja wskazówek implementacyjnych
   - Przykłady kodu z autorytatywnych źródeł
   - Zależności do dodania
   - Pułapki do uniknięcia

### Szablon planu (wzbogacony o ERi)

```markdown
# Task: <short title>

## Structural Intelligence (loctree MCP)

[loctree preamble — always include]

## Pipeline Context

### From Examination (CONTEXT.md):

- Critical files: <relevant subset>
- Risk: <relevant risk items>
- Existing patterns: <symbols to reuse>

### From Research (RESEARCH.md):

- Chosen approach: <architectural decision>
- Key API: <usage pattern from research>
- Pitfalls: <what to avoid>

## Goal

- <1-3 bullets>

## Scope

- In scope: <files/areas>
- Out of scope: <explicit boundaries>

## Acceptance

- [ ] <objective, testable outcome>
- [ ] <objective, testable outcome>
- [ ] Refinement: review changed files with `slice(file)` to verify no broken consumers

## Test Gate

- <repo-specific commands: make check, cargo clippy, etc.>

## Living Tree Note

- Work on a living tree with 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚜𝚖𝚊𝚗𝚜𝚑𝚒𝚙 methodology — concurrent changes expected.
- Adapt proactively, but never skip quality, security, or test gates.
- If blocked, report exact blocker and run closest safe equivalent.
```

## Strategia delegacji

### Dzielenie tasków

Podziel implementację na niezależne, bezpieczne do równoległego wykonania jednostki:

| Wzorzec           | Podział wg             | Przykład                                              |
| ----------------- | ---------------------- | ----------------------------------------------------- |
| Warstwy funkcji   | core → app → testy     | Typy backendu, integracja UI, testy E2E               |
| Niezależne moduły | granica modułu         | zmiany w auth, zmiany w API osobno                    |
| Read/Write        | research → implement   | Jeden agent bada, drugi implementuje                  |
| Poziomy ryzyka    | bezpieczne → ryzykowne | Najpierw bezpieczne refaktory, potem ryzykowne zmiany |

### Heurystyki liczby agentów

- **1 agent**: Prosta poprawka, pojedynczy moduł, zmiana <200 LOC
- **2 agenci**: Funkcja z backendem + frontendem albo implementacja + testy
- **3+ agentów**: Duży refaktor, funkcja wielomodułowa, złożona migracja

### Kanoniczna struktura artefaktów

```
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/
├── plans/
│   ├── <ts>_<slug>_CONTEXT.md
│   ├── <ts>_<slug>_RESEARCH.md
│   ├── <ts>_<agent-task>.md
│   └── ...
├── reports/
│   ├── <ts>_<agent-task>_<launcher>_<agent>.md
│   └── ...
└── tmp/
```

Lokalne dla repo `.vibecrafted/plans` i `.vibecrafted/reports` to tylko wygodne symlinki.

## Komendy spawnowania

Użyj przenośnych skryptów z `runtime/scripts/`. Obsługują one generowanie
artefaktów, wybór trybu uruchomienia (domyślnie headless; terminal tylko na
wyraźne żądanie) oraz automatyczną konfigurację środowiska wykonania.

### Codex (domyślny do implementacji)

```bash
ARTIFACT_DAY="$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>"
PLAN="$ARTIFACT_DAY/plans/<ts>_<agent-task>.md"

bash runtime/scripts/codex_spawn.sh "$PLAN" --mode implement
```

### Claude (do złożonych zadań rozumowania)

```bash
bash agents//claude_spawn.sh "$PLAN" --mode review
```

### Gemini

```bash
bash agents//agy_spawn.sh "$PLAN" --mode implement  # gemini deprecated; agy is the Google-family runtime
```

> Skrypty domyślnie uruchamiają odłączone procesy headless na każdej platformie.
> Przekaż `--runtime terminal` tylko dla ścieżki providera, która dowodowo
> wymaga TTY.

Jeśli zainstalowana jest opcjonalna warstwa pomocnicza zsh, te same akcje stają się:

```bash
codex-implement "$PLAN"
claude-review "$PLAN"
gemini-implement "$PLAN"
```

## Protokół review

Po ukończeniu pracy przez agentów:

### 1. Zbierz raporty

Przeczytaj wszystkie raporty z
`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/`.

### 2. Bramka jakości

Uruchom komendy jakości repo:

- Rust: `cargo clippy -- -D warnings && cargo test`
- Ogólne: `make check` lub odpowiednik

### 3. Weryfikacja strukturalna

Dla każdego zmienionego pliku:

- `slice(file)` — zweryfikuj brak zepsutych konsumentów
- `impact(file)` — potwierdź akceptowalny zasięg zmiany
- Skonfrontuj z mapą ryzyka z CONTEXT.md

### 4. Zgodność z researchem

Zweryfikuj, że implementacja odpowiada decyzjom z RESEARCH.md:

- Czy użyto poprawnych wzorców API?
- Czy dodano zależności zgodnie ze specyfikacją?
- Czy uniknięto pułapek?

### 5. Przedstaw użytkownikowi

Strukturalne podsumowanie:

- Zmienione pliki (liczba + delta LOC)
- Testy przechodzące / padające
- Pozycje ryzyka z CONTEXT.md: zaadresowane / pozostałe
- Decyzje z researchu: dotrzymane / odstępstwa

## Iteracja

Jeśli review wykryje problemy:

1. Zaktualizuj domyślny `plans/<ts>_<slug>_CONTEXT.md` o nowe findingi
2. Napisz ukierunkowane plany napraw
3. Spawnuj agentów naprawczych z tym samym kontekstem pipeline'u
4. Uruchom ponownie bramkę jakości

Nie kumuluj więcej niż 2 rund iteracji bez konsultacji z użytkownikiem.

## Antywzorce

- Spawnowanie agentów bez kontekstu pipeline'u (zmarnują czas na ponowne odkrywanie)
- Pomijanie instrukcji loctree (udowodniony spadek jakości o 37%)
- Brak podziału wg poziomu ryzyka (jedna ryzykowna zmiana psuje bezpieczną pracę)
- Pomijanie weryfikacji strukturalnej po ukończeniu pracy przez agentów
- Więcej niż 5 równoległych agentów (koszt koordynacji przewyższa korzyść)
