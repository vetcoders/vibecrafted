---
name: vc-review
version: 2.0.0
description: >
  READ-ONLY bounded code review. Generates structured artifacts with
  prview-rs, then runs findings-max analysis with falsification-first
  discipline. Every finding carries an explicit evidence grade
  (STRONG / MEDIUM / WEAK / NONE) and either passes or fails an
  adversarial pass. Stage-aware verdicts prevent mid-stage PRs from
  being judged as fully-staged. Per-implementation perception step
  in the pipeline; for per-plan post-marbles falsification use
  `vc-audit` instead; for trajectory direction checking use
  `vc-followup`. Trigger phrases: "review PR", "analyze branch",
  "run prview", "sprawdź PR", "zrób review", "daj findings",
  "zbadaj branch", "artifact pack", "PR quality check", "merge gate",
  "findings-max", "deep review".
default: vc-review
aliases:
  - vc-pr
compatibility:
  tools:
    - Skill
    - TaskCreate
    - TaskUpdate
    - Bash
    - Read
    - Write
requires:
  - vc-init
  - loctree
  - prview
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-review` (launcher `review`)**
>
> Ten sam _kształt_ trzech ścieżek floty, z **literałami tego** skilla — zobacz
> kanoniczną [Matrycę Delegacji](../DELEGATION_MATRIX.md):
>
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna)
> - [Native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy)
>
> | Ścieżka               | Literał tego skilla                                                                                                           |
> | --------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
> | 1. Worker użytkownika | `vibecrafted review <agent>`                                                                                                  |
> | 2. Interactive        | `/vc-review` — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                    |

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-review — READ-ONLY ograniczone code review

> Karta percepcji per-implementacja. Tam gdzie `vc-audit` mówi
> **„sfalsyfikuj twierdzenie ze speca"**, a `vc-followup` mówi **„czy
> trajektoria jest zdrowa?"**, ta mówi **„findings-max na ograniczonym
> diffie, każde twierdzenie domyślnie UNVERIFIED, reviewer nigdy nie
> dotyka kodu"**.

---

## Wejście operatora

### Reguła Living Tree / Worktree

Działa w bieżącym checkoucie i na bieżącej gałęzi operatora. Nie przenoś
się do worktree, chyba że operator wprost o to poprosi. Jedyny usankcjonowany drugi tryb to dispatch Fleet Worktrees (pisany plan, zacommitowane wcześniej verifiery, rozłączne domeny plików, jednowątkowy integrator — patrz Reguła Living Tree, Tryb B); poza tą formacją zostań we wspólnym drzewie. Czytaj pliki
ponownie przed osądzeniem stanu finalnego. Zobacz [Reguła Living Tree](../LIVING_TREE_RULE.md).

## Checkpoint orientacji

Przed review skonsumuj świeże evidence z `vc-init` dla repo. Jeśli go
brak, najpierw uruchom `vc-init`. Użyj `Loctree:loctree` (repo-view, focus,
slice, impact, find, follow), aby odświeżyć Mapę Aplikacji Wyprowadzoną
z Kodu (Code-Derived Application Map) przed twierdzeniami z grepa / docs /
pamięci. Pytania po stronie Loctree (grafy importerów, zasięg zmiany,
martwy kod, lokalizacje symboli) ominięte przez grep = awaria procesu.

Standardowy launcher:

```bash
vibecrafted start
vibecrafted review claude --prompt 'Review PR #4'
vc-review codex --prompt 'Deep review of release/v1.2.1 vs main'
vibecrafted review codex --prompt 'Review HEAD~10..HEAD'
vibecrafted review gemini --file /path/to/pr-artifacts-pack.md
```

`vc-review` potrzebuje **ograniczonego celu**: PR, diffa gałęzi, zakresu
commitów lub wygenerowanego artifact packa. Preferuj `--pr` lub inne
wejścia specyficzne dla review.

---

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## Cel

Użyj tego skilla, gdy ograniczony diff (PR, gałąź, zakres commitów,
artifact pack) wymaga findings z oceną dowodów (evidence-graded) przed
merge'em. Wyjście: findingi z poziomami P + stopień dowodu + checklista
Before-Merge TODO.

Ten skill **nigdy nie modyfikuje kodu**. Produkuje verdict + listę
findings — i nic więcej. Modyfikacja kodu należy w dół pipeline'u do
`vc-marbles` (over-write) i `vc-polarize` (decydujące cięcie).

---

## Kiedy używać

Użyj `vc-review`, gdy:

- PR / gałąź / zakres commitów wymaga bramki przed merge'em
- artefakty prview wymagają ekstrakcji findings-max
- wielocommitowy PR wymaga analizy progresji commitów
- celem jest **ograniczony diff**, nie „całe repo"

**Nie** używaj tego skilla, gdy:

- celem jest wielozadaniowy plan deklarujący ukończenie — to `vc-audit`
- pytanie brzmi „czy kierunek implementacji jest zdrowy?" — to
  `vc-followup`
- operator chce, żeby luki zostały naprawione w trakcie przebiegu — to
  `vc-marbles` (review jest READ-ONLY)

---

## Pozycja w pipelinie

`vc-review` to krok **percepcji per-implementacja**:

```
... → implement (WRITE) → followup (READ) → [REVIEW: READ-ONLY] → marbles (WRITE) → audit (READ) → ...
```

READ-ONLY: produkuje verdict + findingi + raport, nigdy nie modyfikuje kodu.

---

## Domyślna postawa: falsyfikacja

Domyślny verdict dla każdego twierdzenia ze speca, jakie czyni diff, to
**UNVERIFIED**. Opisy PR-ów, komunikaty commitów, markery „fixes #123" i
wcześniejsze raporty agentów to _twierdzenia_, nie dowody. `vc-review`
konwertuje te twierdzenia na dowody, inspekcjonując kod + testy.

### Twarde reguły braku zaufania

NIE WOLNO ci ufać punktom z opisu PR-a, komunikatom commitów nazywającym
fix, inline'owym komentarzom `// done` / `# implemented`, wcześniejszym
raportom `vc-followup` lub `vc-review`, statusowi we frontmatterze
podlinkowanych plików tasków, slice'om AICX / dziennika projektu / pamięci ani
adnotacjom „fixes #123" / „closes #456" — chyba że są **niezależnie
potwierdzone w bieżącym kodzie/testach**.

### Taksonomia dowodów

Każdy finding niesie jawny stopień dowodu:

| Stopień | Kryteria                                                                                            |
| ------- | --------------------------------------------------------------------------------------------------- |
| STRONG  | Kod w diffie + test asertujący dokładne zachowanie + negatywne sprawdzenie (stara ścieżka usunięta) |
| MEDIUM  | Kod w diffie + słaby/ogólny test, lub wymuszony przez system typów                                  |
| WEAK    | Tylko kod w diffie, bez testu, bez negatywnego sprawdzenia                                          |
| NONE    | Brak bezpośredniego dowodu — finding otagowany `[VERIFY]`, severity ograniczone do P3               |

Rekomendacja „ready to merge" jest ważna tylko wtedy, gdy każdy kandydat
na finding P0/P1 dostał ocenę STRONG lub MEDIUM. WEAK na kandydacie P0
oznacza, że samo review jest UNVERIFIED na tej osi.

### Werdykty świadome etapu

Większość PR-ów jest mid-stage. PR lądujący Etap 1 z 3 NIE może być
oznaczony jako P1-blocking dlatego, że Etap 2 jest w kolejce. Otaguj każdy
finding jawnie:

- `[STAGE-OK-DEFERRED]` — luka jest jawnie poza zakresem tego PR-a
- `[STAGE-PARTIAL]` — wylądowany etap ma realną lukę wewnątrz swojego scope
- `[STAGE-DRIFT]` — PR miesza odroczony i wylądowany scope, nie mówiąc o tym

Tagi etapu jadą obok poziomu P: `[P2][STAGE-OK-DEFERRED]` to nota
higieniczna, nie blokada merge'a.

### Skala poziomów P

| Poziom P | Definicja                                                      | Przykłady                                                  |
| -------- | -------------------------------------------------------------- | ---------------------------------------------------------- |
| **P0**   | Blocker merge / security / utrata danych / czerwona bramka     | Padający tsc, wyciekłe credentiale, brak artefaktów        |
| **P1**   | Wysokie ryzyko regresji w core flow, łamanie kontraktu         | Łamiące API, duże nieprzetestowane zmiany, krytyczne cykle |
| **P2**   | Średnie: przypadki brzegowe, a11y, telemetria, częściowe testy | Brak kluczy i18n, hardcodowane URL-e, brak obsługi błędów  |
| **P3**   | Niskie ryzyko / higiena / drobny dryf                          | Puste tytuły docs, duplikacja setupu testów, nazewnictwo   |

---

## Model operacyjny

Dwie fazy. Każda opisana szczegółowo w plikach towarzyszących.

### Faza 1 — Generuj artefakty ([PRVIEW.md](PRVIEW.md))

Najczęstsze dispatche:

```bash
prview --pr <NUMBER>                          # local branch vs develop/main
prview -R --remote-only <branch> <base>       # remote branch (no checkout)
prview --pr <NUMBER> --with-tests --with-lint # GitHub PR by number
prview --deep                                 # all gates
```

Domyślnie dla vc-review: **nie używaj `--quick`**. Używaj `--quick` tylko
do jawnego szybkiego triage albo gdy ciężkie bramki są niemożliwe.

Dodaj `--gh-repo owner/repo`, jeśli origin jest niejednoznaczny. Pełna
referencja flag, tabela trybów, wykrywanie profilu, system polityk i
specjalne przypadki tooli w [`PRVIEW.md`](PRVIEW.md).

### Faza 2 — Analizuj artefakty ([FINDINGS.md](FINDINGS.md))

Filozofia findings-max. Kolejność czytania, obowiązkowe skany wzorców,
przebieg adwersaryjny, minimalne wymagania pokrycia i format wyjścia —
wszystko w [`FINDINGS.md`](FINDINGS.md).

---

## Kontrakt wyjścia

Trzy obowiązkowe sekcje, w tej kolejności:

1. **Findingi (P0/P1/P2/P3 ze stopniem dowodu)** — zobacz
   [`FINDINGS.md`](FINDINGS.md) po pełny szablon
2. **Before-Merge TODO** — markdownowe checkboxy z odniesieniami do ID
   findings (`P1-01`, `P1-02`, ...) wraz z komendami weryfikacyjnymi
3. **Self-Attack Pass (przebieg autoataku) + Model Check** — zaatakuj
   każdy verdict STRONG, zdegraduj, jeśli istnieje falsyfikator; wyemituj
   `model_confidence: high | medium | low`. Jeśli confidence ≠ high, nie
   można rekomendować „merge as-is" — tylko „merge after operator
   verifies X"

Sekcje opcjonalne (dodaj, gdy wnoszą wartość): Executive Summary,
Architecture Context, Scope / What Changed, Commit Progression, Test
Coverage Matrix, Security & Privacy Check, QA Plan, Evidence Index.

---

## Złożenie ze skillami sąsiednimi

`vc-review` komponuje się z — nie zastępuje — tych:

- **`vc-init`** — wymagana bramka przed review.
- **`vc-audit`** — siostrzana rola READ-ONLY w scope per-plan (nie per-diff).
- **`vc-followup`** — siostrzana rola READ-ONLY w scope trajektorii.
- **`vc-marbles`** — kolejny krok WRITE w dół pipeline'u, który naprawia to, co review znajdzie.
- **`vc-polarize`** — kolejny krok WRITE w dół pipeline'u, który tnie do jednej prawdy.

---

## Antywzorce

Użycie narzędzi:

- Używanie `--quick` jako domyślnego dla review PR-a (gubi sygnał test/lint/security)
- Uruchamianie `--deep` na każdym PR-ze, gdy `--with-tests --with-lint` wystarcza
- Czytanie całego `full.patch` dla dużych PR-ów (użyj `per-file-diffs/`)
- Ignorowanie `report.json` / `MERGE_GATE.json` (najpierw parsuj ustrukturyzowane)
- Nieużywanie `--update` po amendzie/force-pushu (zduplikowane zestawy artefaktów)

Analiza:

- Zatrzymanie się na 5 findings, gdy widać 25 (findings-max = wyczerpująco)
- Findingi bez stopnia dowodu (STRONG / MEDIUM / WEAK / NONE obowiązkowe)
- Findingi bez negatywnego sprawdzenia („stara ścieżka usunięta" musi być zweryfikowane)
- Mieszanie osobnych problemów w jeden finding (jeden punkt = jeden problem)
- Pomijanie skanów wzorców (`.unwrap()` / `any` / checklista PII obowiązkowe)
- Pomijanie przebiegu adwersaryjnego (Faza 2.5 jest obowiązkowa)
- Pomijanie autoataku na verdictach STRONG
- Modyfikowanie kodu podczas review (READ-ONLY — fixy należą do marbles)
- Ufanie opisowi PR-a / commitom / `// done` bez weryfikacji w kodzie
- Rekomendowanie „merge", gdy `model_confidence` ≠ `high`
- Traktowanie PR-ów mid-stage jako fully-staged (użyj `[STAGE-OK-DEFERRED]`)

---

## Wezwanie do działania

Przeczytaj [`PRVIEW.md`](PRVIEW.md) przed pierwszym dispatchem — niesie
referencję flag prview i układ artifact packa. Przeczytaj
[`FINDINGS.md`](FINDINGS.md) przed pierwszym przebiegiem findings — niesie
kolejność czytania, obowiązkowe skany wzorców, przebieg adwersaryjny oraz
pełny szablon wyjścia. Potem ustaw każde twierdzenie domyślnie na
UNVERIFIED i zapracuj na stopień dowodu każdego findingu.

---

## Klamra końcowa

```text
=======================
Pamiętaj: tryb review to pozwolenie na odmowę merge'a, nie pozwolenie na
naprawę diffa. Czytasz prview, oceniasz dowody, tagujesz etap, atakujesz
własny verdict, zatrzymujesz się. Operator jest właścicielem przycisku
merge.
(•_•)つ━☆
=======================

Suchar: Dlaczego reviewer ze słabym (WEAK) dowodem śpi kiepsko?
Bo paragon i tak trzyma diff.  (._.)
```

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
