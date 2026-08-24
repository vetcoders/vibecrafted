---
name: canary
version: 2.0.0
description: >
  Radar konkurencji o prawdę w repo: wykrywa komponenty rywalizujące o tę
  samą klasę prawdy (identity, autorstwo, redukcja, finality, delivery,
  konfiguracja), zanim zatrują runtime. Cztery fazy: authority & freshness,
  kandydaci strukturalni, radar równoległych implementacji, polaryzacja
  ownership. Wyłącznie dowody i klasyfikacja — nigdy refaktor. Użyj, gdy
  użytkownik prosi o "canary", "truth radar", "parallel implementation
  radar", "truth collision", "semantic twins", "kto jest właścicielem
  prawdy", "skataloguj repo", "ownership catalog", albo uruchamia
  /vc-canary / vibecrafted canary.
---

<!-- fleet-imperative: v3 -->

> **Inwokacja `vc-canary` (launcher `canary`)**
>
> Ten sam trójścieżkowy _kształt_ co flota — patrz
> [DELEGATION_MATRIX.md](../DELEGATION_MATRIX.md):
>
> | Ścieżka                        | Literał                                     |
> | ------------------------------ | ------------------------------------------- |
> | 1. Worker odpalony przez usera | `vibecrafted canary <agent>`                |
> | 2. Interaktywnie               | `/vc-canary` — wykonaj **w tej sesji**      |
> | 3. Agent-operator              | `vibecrafted canary <agent>` przez dispatch |
>
> Root domyślnie **`$PWD`**. Nie wymyślaj `vibecrafted workflow` jako zamiennika.

<!-- /fleet-imperative -->

# vc-canary — radar konkurencji o prawdę

## Misja

Software generowany przez agentów psuje się w konkretny sposób: **lokalnie
poprawne moduły powstają szybciej, niż system ustanawia globalne ownership
semantyki.** Wynikiem nie jest zduplikowany kod — są nim komponenty
konkurujące o tę samą klasę prawdy: pięć pojęć identity, dwa reducery
dokumentu, pięć źródeł konfiguracji, dwudziestu aktywnych uczestników
między wejściem a delivery.

Canary odpowiada na jedno pytanie, per oś decyzji:

> **Ile miejsc w tym repo może odpowiedzieć na to samo pytanie runtime —
> i które z nich jest writerem, arbitrem, obserwatorem, projekcją?**

Canary **wykrywa** wielowładzę. Nie zakłada, że wielowładza jest błędem:
dwie implementacje mogą być legalne (runtime vs replay) — agent musi tę
granicę **udowodnić**, nigdy uznać za oczywistą. Canary produkuje dowody
i klasyfikację, **nigdy nie refaktoruje i nie proponuje decyzji tronowych
jako findingów** — implementacja przychodzi po QC, w skillach tnących,
na słowo operatora.

Baza empiryczna: dwa niezależne produkcyjne systemy zbudowane przez
agentów pokazały tę samą chorobę (jeden z nich: 5 pojęć identity,
5 źródeł settings, 2 reducery dokumentu, prism 11/12), znalezioną tym
samym protokołem.

## Kanoniczna bramka orientacji

Skonsumuj świeże dowody `vc-init` dla repo; jeśli ich brak, najpierw
`vc-init`. Użyj `Loctree:loctree` (repo-view, focus, slice, impact, find,
follow), by zmaterializować Code-Derived Application Map, która zasiewa
kandydatów na osie. Wyczuwanie planów przez goły grep, dokumentację albo
„pamiętam to repo" zamiast organów Loctree to porażka procesu.

**Zakazane jako inwentarz:** `loct context --full` `structural.files`
(wyłącznie ranking hubów). **Zakazane:** ładowanie surowego, wielomegabajtowego
`snapshot.json` do kontekstu modelu.

## Phase 0 — Authority & freshness

Żadnego radaru na nieświeżym drzewie. Zapisz, z pokwitowaniami:

- dokładne repo, root, SHA HEAD; stan dirty-tree; worktree'y i zagnieżdżone
  repozytoria;
- pokrycie snapshotu Loctree: `canary_cli atlas --refresh` →
  `./.loctree/atlas/` (`repo-atlas.json`, `inventory.jsonl`,
  `coverage.json`); coverage `pass: true` wymagane, by iść dalej;
- commity osiągalne lokalnie, ale nieobecne w genealogii HEAD
  (`git cherry`, gałęzie tylko-lokalne/tylko-zdalne) — osierocona semantyka
  to wejście radaru, nie ciekawostka.

## Phase I — Strukturalni kandydaci semantyczni

Przebieg wysokoskalowy: `loct repo-view`, drzewo, `focus`, `twins`,
`crowd`, `hotspots`. Celem **nie** są findingi — jest nim lista
kandydackich **osi decyzji**: klas prawdy, które to repo rozstrzyga,
i plików, których nazwy/role sugerują więcej niż jednego rozstrzygającego.

Powtarzające się klasy osi (wyprowadź własne dla repo; nie kopiuj tej
listy ślepo): identity, korekta/autorstwo tekstu, wybór
silnika/orkiestracji, redukcja dokumentu/stanu, formatowanie,
finality/seal, delivery, precedencja konfiguracji, konkurenci
epistemiczni (weryfikatorzy/replayery/raporty osądzające podobną prawdę
bez pisania codziennego runtime).

## Phase II — Radar Równoległych Implementacji

Dla **każdej** osi schodź z pokwitowaniami:

```
find --discover → exact occurrences (literal coverage) → body
→ slice / consumers → follow (trace / pipelines / events) → impact (when needed)
```

**Falsyfikacja nieobecności jest obowiązkowa.** Szerokie/semantyczne
wyszukiwanie daje kandydatów; dopiero linia literal coverage („scanned X
of Y files") dowodzi, gdzie decyzja _nie_ żyje. „Przeszukałem semantycznie
i wygląda na jedno miejsce" to twierdzenie bez dowodu — dokładnie ten tryb
porażki, przed którym ta faza chroni.

Sklasyfikuj każdą konkurującą parę:

| Werdykt                | Znaczenie                                                          |
| ---------------------- | ------------------------------------------------------------------ |
| `SAME_SOURCE_OF_TRUTH` | oba sprowadzają się do jednej władzy; brak konkurencji             |
| `INTENTIONAL_VARIANT`  | legalna równoległość (np. runtime vs replay) — granica udowodniona |
| `DRIFTED_DUPLICATE`    | zaczęły równo, semantyka się rozjechała                            |
| `BYPASS_PATH`          | druga ścieżka omija władzę na części wejść                         |
| `FALSE_PARALLEL`       | wyglądało na równoległe strukturalnie; dowody to rozpuściły        |

Wagę runtime oznacz legendą:

- 🔥 bezpośrednia kolizja w codziennym runtime
- ⚠ ta sama odpowiedzialność w innym etapie lub trybie
- ◌ konkurent offline/testowy/alternatywny

## Phase III — Polaryzacja ownership

`loct prism` na ujęciach osi + ręczne rozstrzygnięcie. Dla każdej osi:
ile miejsc może odpowiedzieć na pytanie runtime; kto jest **writerem**,
kto **arbitrem**, kto **obserwatorem**, kto **projekcją**. Findingi rodzą
się tutaj — nigdzie wcześniej — i każdy niesie klasyfikację:

| Klasa          | Znaczenie                                                  |
| -------------- | ---------------------------------------------------------- |
| `CUT_BLOCKER`  | zatruwa codzienny runtime; blokuje planowane cuty          |
| `CUT_COHERENT` | naprawa naturalnie składa się w już zaplanowany cut        |
| `FOLLOW_UP`    | realne, niepilne; idzie do backlogu z dowodami             |
| `OBSERVATION`  | wielowładza udowodniona jako legalna lub uśpiona; obserwuj |

## Schemat dowodowy (per finding)

Oś · konkurenci (`file:line`, LOC) · walczące symbole · werdykt pary ·
znak legendy · klasyfikacja · dowód (wyjścia loct cytowane per organ) ·
pokwitowanie falsyfikacji nieobecności (linia literal coverage). Finding
bez któregokolwiek elementu jest kandydatem, nie findingiem.

## Kontrakt journala

Append-only `./.loctree/canary/JOURNAL.md` w docelowym repo. Każdy
przebieg dopisuje: SHA HEAD, pokwitowania Phase 0, zbadane osie, findingi
z dowodami, wyniki prism. Przebiegi nigdy nie nadpisują historii —
journal pokazuje, co radar widział _na tym SHA_, nawet gdy późniejszy
przebieg wie lepiej.

## Report → discuss → decide (stop QC)

Canary nie mutuje **żadnego kodu** i niczego nie sieje po cichu do
memex/aicx. Wyjściem jest wpis w journalu + raport dla operatora. Decyzje
tronowe, plany cięć i usunięcia dzieją się po dyskusji, we własnych
skillach, z dowodami canary w załączniku. Test akceptacyjny każdej zmiany
canary: przebieg na znanym studium przypadku i odtworzenie wcześniej
ręcznych findingów **bez podpowiadania, gdzie patrzeć**.

## Tryb floty (duże repozytoria)

Jeden agent na oś (albo na scope karmiący osie), zmienne N z Phase I —
nigdy stała liczba. Hybryda: N≤8 natywnie; N>8 zewnętrznie przez
[await-arming](../../vc-dispatch/references/await-arming.md). Szablon
briefu per-scope:
[references/canary-agent-brief.md](../../vc-canary/references/canary-agent-brief.md).
Pin agenta: user; inaczej ten launcher, który żyje w sesji, w kolejności
`claude` · `codex` · `grok`.

## Skrypty pomocnicze

```bash
CLI="uv run --python 3.12 …/vc-canary/scripts/canary_cli.py"

$CLI snapshot-path --root .
$CLI repo-view --root .
$CLI atlas --root . --refresh
$CLI coverage --root .
```

Wszystkie zapisy idą pod `./.loctree/` (atlas + canary). Status na stdout;
dane w plikach.

## Zależności

| Skill / narzędzie           | Po co                                |
| --------------------------- | ------------------------------------ |
| `loct` / loctree            | snapshot, organy, prism, occurrences |
| `vc-loctree`                | doktryna strukturalna                |
| `vc-dispatch` await-arming  | budzenie floty, nie pliki logów      |
| `vc-delegate` / `vc-agents` | hybryda natywni vs zewnętrzni        |

## Częste błędy

- Traktowanie każdej wielowładzy jako defektu (`INTENTIONAL_VARIANT`
  z udowodnioną granicą to zdrowa odpowiedź)
- Proponowanie refaktorów albo tronów wewnątrz przebiegu canary
- Twierdzenie o nieobecności z wyszukiwania semantycznego bez literal
  coverage
- Raport na nieświeżym snapshocie albo ignorowanie stanu dirty/worktree
- Nadpisywanie journala zamiast dopisywania
- Używanie `loct-context-full.json` jako inwentarza plików
- Stała liczba agentów zamiast osi z Phase I

## Weryfikacja przed handoffem

Patrz [VERIFICATION_RULE.md](../VERIFICATION_RULE.md). Zielone bramki ≠
prawdziwy radar. Wymagane: coverage `pass: true`, każdy finding z pełnym
schematem dowodowym i dopisany (nigdy przepisany) wpis w journalu.

---

_Dowód polowy v2: 5 pojęć identity, 5 źródeł settings, 20 aktywnych
uczestników między przechwyceniem a delivery — znalezione tym protokołem
w produkcyjnym systemie zbudowanym przez agentów, 2026-08._
