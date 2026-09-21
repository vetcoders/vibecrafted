# Wzór Wpisu do Dziennika Śledczego (Forensics Journal)

Dziennik śledczy jest prowadzony w badanym repozytorium pod ścieżką:
`./.loctree/forensics/JOURNAL.md`

Zasada: **Append-only**. Nigdy nie nadpisuj i nie usuwaj wcześniejszych wpisów. Każdy bieg dokleja swój blok per `HEAD SHA`.

---

## Szablon Wpisu Biegowego (Run Block)

```markdown
## [Run YYYY-MM-DD HH:MM] — HEAD `pełny_sha_40_znaków`

- **Branch / Stan:** `main` (lub nazwa brancha), dirty files: `brak` (lub lista z `git status --short`)
- **Cel dochodzenia:** Badanie ścieżki X pod kątem wyścigów i wielowładzy
- **Instrumenty:** Loctree (`repo-view`, `focus`, `slice`, `impact`, `occurrences`), AICX

### 1. Rozpoznane Osi Decyzyjne i Zagrożenia

| Oznaczenie | Komponent A | Komponent B (lub wada) | Klasyfikacja | Dyspozycja   |
| ---------- | ----------- | ---------------------- | ------------ | ------------ |
| 🔥         | `mod_a.rs`  | `mod_b.rs`             | CUT_BLOCKER  | RADICAL_CUT  |
| ⚠          | `state.rs`  | brak locka / race      | CUT_COHERENT | SURGICAL_FIX |

Legenda:

- 🔥 bezpośrednia kolizja w codziennym runtime
- ⚠ ta sama odpowiedzialność w innym trybie / wyścig na krawędzi
- ◌ konkurencja testowa / offline

### 2. Wyniki Dochodzenia i Zastosowane Cięcia

#### Finding 1: [Tytuł problemu]

- **Komponenty / Symbole w walce:** `file_a:L10` vs `file_b:L50`
- **Dowód Loctree:** `slice(file_a)` wykazało 3 wywołania, `impact(file_b)` = 0 zależności runtime
- **Scenariusz błędu:** Krok 1 -> Krok 2 -> wyścig / błąd
- **Obalenie pozorności:** Wykazano, że to nie jest legalny wariant
- **Zastosowane cięcie:** Wycięto konkurenta `git rm file_b` (zero shimów!)
- **Test regresji:** `tests/test_race.rs` (FAIL przed poprawką -> PASS po poprawce)
- **Commit:** `sha_commit_po_poprawce`

### 3. Weryfikacja DoU

- Bramka jakościowa: `make check` (ruff/prettier/semgrep) / `cargo clippy -- -D warnings`: PASS
- Testy regresyjne: PASS
- Ścieżka runtime: zweryfikowano rzeczywisty przepływ produktu
- Cel instalacyjny desktopowy: `make installable-safe`: zweryfikowany proces, dźwięk Ping odtworzony
- Status handoffu: `ZDIAGNOZOWANE` -> `POPRAWIONE` -> `ZWERYFIKOWANE` -> `ZCOMMITOWANE`
```
