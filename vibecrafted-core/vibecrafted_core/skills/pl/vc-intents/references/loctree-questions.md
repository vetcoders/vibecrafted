# Loctree: structural questions that settle an intent

Przeczytaj przed fazą 5.

## Contents

- [The errata that this file exists for](#the-errata)
- [Translation table: promise → structural question → call](#translation-table)
- [The instrument, command by command](#the-instrument)
- [Inventory prohibitions](#inventory-prohibitions)
- [When Loctree cannot answer](#when-loctree-cannot-answer)

## The errata

> Loctree nie jest grepem i nigdy nie miało nim zostać. Loctree to **struktura**.
> `loct --help-full` jest drogowskazem: przeczytaj bez ucięcia, zrozum i wypróbuj.
> Tryby literal i regex mają 130% pokrycia grepa — wszystko, co znajduje grep,
> plus wskazówki strukturalne — ale główna korzyść jest gdzie indziej.
> **Celne, jawne pytania strukturalne do Loctree oszczędzają dziesiątki minut grepowania.**

Dla polowania na intencje oznacza to: intencja jest _obietnicą zachowania_.
Search tekstowy mówi tylko, czy nazwa występuje. Struktura mówi, czy zachowanie
ma ścieżkę. To dwa różne pytania; tylko drugie może zamknąć intencję.

Dwa rodzaje błędu, którym to zapobiega:

- **Fałszywe `landed`.** Symbol i ciąg istnieją, więc intencja jest uznana za
  done, mimo że nic jej nie wywołuje. `loct dead`, `loct twins` i
  `loct query who-imports` wykrywają to jednym wywołaniem; grep nie potrafi.
- **Fałszywe `missing`.** Obietnicę dostarczono pod inną nazwą albo w innej
  warstwie, więc literalna sonda zwraca pustkę i kolejkuje już wykonaną pracę.
  `loct find --discover`, `loct crowd` i `loct follow pipelines` to wykrywają.

Narzędzia literalne (`loct find`, `loct occurrences`) zostają w zestawie jako
warstwa **potwierdzania** po odpowiedzi strukturalnej, nie odkrywania przed nią.

## Translation table

| The promise sounds like                        | The structural question                                           | The call                                                                                    |
| ---------------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| „X uruchamia się automatycznie / przy starcie” | Czy jest droga od żywego entrypointu do X?                        | `loct follow trace --handler X` · `loct follow pipelines` · `loct query who-imports <file>` |
| „Y jest konfigurowalne”                        | Czy deklaracja istnieje i kod ją odczytuje?                       | `loct env-truth --name Y` · `loct env-truth --fail-on orphan-code-reference`                |
| „Z zastąpiło W”                                | Czy W jest osierocone, czy dwóch właścicieli nadal współistnieje? | `loct impact <W>` · `loct twins` · `loct dead`                                              |
| „połączyliśmy dwie ścieżki”                    | Czy dwa moduły nadal odpowiadają na to samo pytanie?              | `loct twins` · `loct crowd <keyword>` · `vc-canary`                                         |
| „usunęliśmy hack / nic nie wyciszamy”          | Naprawa czy suppression?                                          | `loct suppressions --type nosemgrep\|allow\|type-ignore\|noqa\|ts-ignore`                   |
| „pokryte testami”                              | Czy istnieje ścieżka z testem?                                    | `loct coverage`                                                                             |
| „komenda/route istnieje”                       | Czy powierzchnia jest zarejestrowana?                             | `loct commands` · `loct routes` · `loct trace <handler>`                                    |
| „zdarzenia płyną z A do B”                     | Czy emit i listen tworzą parę?                                    | `loct follow events`                                                                        |
| „trafia do bundla”                             | Czy manifest/tree-shake to potwierdza?                            | `loct manifests` · `loct dist`                                                              |
| „moduł jest rdzeniem”                          | Czy naprawdę jest hubem?                                          | `loct hotspots` · `loct focus <dir>`                                                        |
| „bezpieczne do zmiany”                         | Jaki jest zasięg zmiany?                                          | `loct impact <file>` · `loct slice <file>`                                                  |
| „kształt zmienił się od uzgodnienia”           | Co zmieniło się strukturalnie?                                    | `loct diff --since <ref>` · `loct diff --since <ref> --problems-only`                       |
| „symbol S jest właścicielem”                   | Gdzie zdefiniowany, kto importuje, jak wygląda?                   | `loct query where-symbol S` · `loct query who-imports <file>` · `loct body S`               |
| „identyfikator zniknął”                        | Prawda literalna w indeksowanym zbiorze                           | `loct occurrences <ident> --count-only`                                                     |

## The instrument

Grupy według pytania, na które odpowiadają. `loct <command> --help` zawiera
wszystkie flagi; `loct --help-full` jest mapą.

**Orientacja**

- `loct context` — pigułka markdown i artefakty; `--full` daje pack.
- `loct repo-view` — pliki, LOC, języki, zdrowie, główne huby.
- `loct tree` — struktura katalogów z licznikami LOC.
- `loct focus <dir>` — moduł: pliki, krawędzie wewnętrzne, zależności zewnętrzne.
- `loct atlas` — sense / inventory / signals pack.

**Przed edycją lub usunięciem**

- `loct slice <file>` — plik oraz jego zależności i konsumenci.
- `loct impact <file>` — konsumenci bezpośredni i przechodni. Przed usunięciem,
  dużym refactorem i do rozstrzygania remisów w kolejce.

**Symbole i osiągalność**

- `loct query where-symbol <S>` — gdzie zdefiniowany albo eksportowany.
- `loct query who-imports <file>` — zależności odwrotne.
- `loct query component-of <file>` — właściciel modułu.
- `loct body <S>` — ograniczone ciało źródłowe z zasięgiem i informacją o ucięciu;
  używaj zamiast czytania całego pliku do kontekstu.
- `loct find <ident>` — literalnie, z granicami identyfikatora. `--regex` dla
  wzorców. `--discover` włącza szeroki silnik symboli/parametrów/fuzzy.
- `loct occurrences <ident>` — prawda literalna pod `find`; widzi lokale
  w dużych funkcjach, których search symboli nie widzi. `--count-only` dla
  obecności, `--group-by-file` dla podsumowania plików.

**Sygnały**

- `loct follow [all|dead|cycles|twins|hotspots|trace|commands|events|pipelines]`
  — wspólny follower; `--handler <name>` dla `trace`.
- `loct dead` — nieużywane eksporty.
- `loct twins` — dead parrots (0 importów) i zdublowane eksporty; detektor dwóch implementacji.
- `loct cycles` — cykle importów.
- `loct crowd <kw>` — grupowanie funkcjonalne wokół słowa; znajduje obietnice dostarczone w innym słownictwie.
- `loct tagmap <kw>` — pliki + crowd + dead w jednym widoku.

**Kontrakty i dryf**

- `loct env-truth` — dryf deklaracji env w dotenv, docker, k8s, helm, GHA,
  npm scripts. `--name <VAR>` pogłębia, `--fail-on <kind>` jest bramką.
  Sealed payloady pokazuje tylko po markerze formatu, nigdy nie dekoduje.
- `loct suppressions` — wyciszenia źródłowe: `allow`, `dead-code`,
  `nosemgrep`, `ts-ignore`, `noqa`, `type-ignore`, `shellcheck`, `unsafe`
  i inne. Intencja „zamknięta” wyciszeniem pozostaje otwarta.
- `loct coverage` — strukturalne luki testów.
- `loct diff --since <ref>` — nowe/usunięte symbole, zmiany grafu, nowe dead code
  i cykle. `--problems-only` dla regresji, `--changed-files` dla podsumowania.
- `loct anchors` — deterministyczny katalog kotwic; do niego `aicx overlay` przypisuje intencje.

**Porządkowanie kolejki**

- `loct hotspots` — mapa częstości importów; hub to ryzykowny cut.
- `loct health` / `loct findings` — agregują dead/twins/cycles jako bramkę.
- `loct prism --task <a> <b>` — ocenia rozmycie pojęć między ujęciami zadania;
  przydatne, gdy dwie intencje mogą oznaczać tę samą rzecz innymi słowami.

## Inventory prohibitions

Zakazy wynikają z wcześniejszych spalonych runów:

- **Nie** używaj `loct context --full` `structural.files` jako inventory;
  to ranking hubów, nie lista plików.
- **Nie** wczytuj wielomegabajtowego `snapshot.json` do kontekstu modelu.
  Pytaj: `loct '.files | length'`, `loct '.summary.health_score' --artifact agent`,
  `loct '.dead_parrots | length' --artifact findings`.
- **Nie** używaj `grep` / `rg` / filesystem `find` jako dowodu _nieobecności_.
  Literalny brak nie dowodzi niczego o plikach ignored/generated/unsupported
  lub nieindeksowanych; dlatego `loct occurrences` podaje pokrycie każdego zapytania.
- `--include-ignored` pokazuje pliki wykluczone przez `.loctignore` (testy,
  skrypty, docs) jako `ignored` dla `find` / `slice` / `impact`.
  Skan jest efemeryczny i nie zmienia cache. Użyj, gdy dowód intencji żyje tam.

## When Loctree cannot answer

Dopisz dokładną awarię do `~/.vibecrafted/loctree/loctree-fail.md`.
**Append, nigdy overwrite.** Powtórka sygnalizuje lukę, nie duplikat do usunięcia.
Analogiczna powierzchnia AICX: `~/.vibecrafted/aicx/aicx-fail.md`.

Zachowaj kształt istniejącego pliku — czyni wpis użytecznym, zamiast skargą:

```markdown
---

## <YYYY-MM-DD> — <one-line title>

- **Repo:** <path>
- **Command:** `<exact invocation>`
- **Observed:** <what came back, with counts>
- **Fallback:** <what you used instead>
- **Improvement:** <the capability that would have answered it>
```

Potem przejdź do search literalnego i powiedz o tym w raporcie, żeby dowód
niósł własny poziom pewności.
