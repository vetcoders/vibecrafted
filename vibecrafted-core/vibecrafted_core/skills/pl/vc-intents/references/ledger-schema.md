# Intent ledger — schema and continuity contract

Przeczytaj, gdy dotykasz ledgera. `scripts/intents_cli.py` jest właścicielem
schematu: każdy run, host i agent zapisują ten sam kształt. Ręczna edycja JSON
jest legalna, ale skrypt jest implementacją referencyjną.

## Location

```
~/.vibecrafted/artifacts/<owner>/<repo>/intents/
  sweep.json               coverage per source inside the window
  shards/sNN/              transcript copies for extraction lanes
  intents.json             verified catalog (verbatim quotes)
  lanes/<lane>.json        confrontation input per lane
  LEDGER.json              state — schema vc-intents.ledger.v2
  LEDGER.md                rendered, regenerated on every write
  overrides.jsonl          append-only reclassification layer
  replication-report.json  agreement between hosts
  queue.json · STABLE-QUEUE.md
  intents.html             review surface
```

Celowo poza repo: ledger śledzi intencje sprzed checkoutu, między branchami
i konfrontowane na więcej niż jednej maszynie.

## Record: intent (`LEDGER.json → intents[]`)

| Field                            | Meaning                                                                                                                                                                                                                           |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                             | Trwały uchwyt `<shard>-NNN`. Bez ponownego użycia i renumeracji — cytują go `after`, `superseded_by`, overrides.                                                                                                                  |
| `date`, `source`                 | Dzień i plik wypowiedzi (`<YYYY-MM-DD>_<rest>` rozwiązuje się do `<root>/<date>/<rest>` albo układu płaskiego).                                                                                                                   |
| `quote`                          | **Dosłowny** podciąg źródła; `verify-quotes` wymusza zawieranie. Parafraza odrzucona, nigdy naprawiana.                                                                                                                           |
| `topic`                          | Znormalizowane 3–8 słów w słownictwie Foundera. Normalizacja tutaj, nigdy w `quote`.                                                                                                                                              |
| `kind`                           | `task` · `constraint` · `preference` · `decision` · `complaint` · `question` — czynność Foundera; steruje regułą 1 niżej.                                                                                                         |
| `subject`                        | Klucz obszaru, podstawa torów.                                                                                                                                                                                                    |
| `strength`                       | 1–5: siła wypowiedzi. Próg kolejki `--min-strength`, domyślnie 4.                                                                                                                                                                 |
| `verdicts`                       | `{host: {disposition, evidence, gap, superseded_by, conflicts_with}}` — oryginał floty, nigdy nadpisywany.                                                                                                                        |
| `effective_by_host`              | Werdykt każdego hosta po override'ach reguł.                                                                                                                                                                                      |
| `stable`                         | True, gdy każdy host ma werdykt i po regułach są zgodne. Do kolejki wchodzą tylko stable lub rozstrzygnięte przez człowieka.                                                                                                      |
| `disposition`                    | Efektywna: override człowieka › override reguły › werdykt głównego hosta.                                                                                                                                                         |
| `decided_by`                     | `rule`, człowiek rozstrzygający albo null.                                                                                                                                                                                        |
| `runtime_verified`               | `yes` · `no` · `unknown`. Czy wykonano ścieżkę produktu, nie tylko znaleziono kod. Werdykty floty startują jako `unknown`; zmienia je człowiek albo sonda runtime. `status` pozostaje otwarte, gdy dowolne `landed` nie ma `yes`. |
| `after`, `risk`, `flow`, `notes` | Dane porządkowania i wolny tekst, zachowane przez `merge`.                                                                                                                                                                        |

## Record: source (`sweep.json → sources[]`)

`source` (`dir:<path>` lub `aicx`), `state` (`read` · `empty` · `unavailable`),
`files`/`rows`, `truncated` (aicx raportuje ucięcie korpusu tylko na stderr;
skrypt przechwytuje i oznacza), `reason` dla `unavailable`. Nieczytelne źródło
utrzymuje cel otwarty — znana niewiadoma, nie nieobecność.

## Dispositions

Werdykty floty: `landed` · `partial` · `absent` · `superseded` · `contradiction` · `unclear`.
Wyprowadzone regułą: `landed-contested` · `landed-by-doc`. Tylko człowiek: `drop`.

**Zamykające:** `landed` (z `runtime_verified = yes`) · `superseded` (z `superseded_by`) · `drop`

**Otwarte — liczone przez `status`:**

- `partial` — kształt jest, obietnica niespełniona; brak opisany w `gap`
- `absent` — brak śladu; dowód to pytanie, które zwróciło zero
- `contradiction` — dwie wypowiedzi Foundera sprzeczne, żadna późniejsza nie rozstrzyga (`conflicts_with`)
- `unclear` — potrzebny Founder; pytanie w `gap`
- `landed-contested` — kod istnieje, ale Founder mówi, że nie działa (`kind=complaint`).
  Runtime niezweryfikowany; skarga wygrywa z symbolem.
- `landed-by-doc` — jedyny dowód to dokument (`docs/*.md`, kontrakt `*.md`), nie kod.
  Dokument agenta jest twierdzeniem o wykonaniu w przebraniu dokumentu.

`drop` to „to nigdy nie była moja intencja” Foundera: głos modelu wklejony do
pliku dyktowania, inny projekt, fraza testowa. Zamyka wiersz bez udawania działania kodu.

## `landed` is a lower bound, not a verdict — mechanical reclassification

Wynik terenowy 2026-09-19: dwie niezależne floty na dwóch hostach, te same
wejścia i commit — **43–45% werdyktów `landed` upadło** po dwóch regułach,
które wymagają tylko rekordu, bez osądu:

1. `kind == complaint` ∧ `disposition == landed` → `landed-contested`.
   Skarga mówi „nie działa”, `landed` mówi „działa”. Flota wybrała kod, bo
   można go sprawdzić, a runtime nie. Słowa Foundera wygrywają do sondy runtime.
2. `evidence` nie zawiera pliku kodu (`.rs .swift .py .ts .js .toml .sh …`) →
   `landed-by-doc`. `docs/HOTKEYS_CONTRACT.md:37` dowodzi zapisania kontraktu,
   nie jego dotrzymania.

Stosuj obie **przed** liczeniem zamkniętych spraw. Zachowaj oryginalny werdykt
floty i zapisz reklasyfikację osobno:

```
overrides.jsonl   # one JSON object per line, append-only
{"id":"s4-017","host":"div0","from":"landed","to":"landed-contested","rule":"complaint∧landed","by":"rule","reason":"…","at":"2026-09-19"}
{"id":"s2-052","host":"*","from":"landed","to":"absent","by":"founder","reason":"double control nie działa na drugiej maszynie","at":"2026-09-19"}
```

`by` to `rule` lub człowiek rozstrzygający; `host` to `*`, gdy override dotyczy
każdego werdyktu. Render pokazuje override i oryginał obok — warstwa, nigdy
nadpisanie. Tak Founder edytuje katalog: wierszem tutaj, nie ręczną zmianą
pliku werdyktu. Oba warianty `landed-*` **liczą się jako otwarte**.

## Record: source

| Field     | Meaning                                                                           |
| --------- | --------------------------------------------------------------------------------- |
| `session` | Session id lub ścieżka źródła ze sweep.                                           |
| `state`   | `unread` · `read` · `unavailable`.                                                |
| `reason`  | Wymagany sensownie dla `unavailable`: luka indeksu, brak rollout, brak uprawnień. |

Każda sesja znaleziona przez sweep ma wiersz. To czyni rozliczone pokrycie źródeł
liczbą audytowalną, nie wrażeniem. Dlatego `unavailable` utrzymuje cel otwarty:
nieczytelne źródło jest znaną niewiadomą, nie nieobecnością.

## Ordering

`queue` stosuje algorytm Kahna na krawędziach `after` dla otwartych actionable
dispositions (`partial`, `absent`, `landed-contested`, `landed-by-doc`).
Remisy: `risk`, `flow`, strength malejąco, date rosnąco, `id`. Zależność
wygrywa z ryzykiem: sprawa utraty danych, która jeszcze nie kompiluje, jest druga.
Wchodzą tylko intencje `stable` lub rozstrzygnięte przez człowieka.

Cykl jest raportowany na stderr po id, a jego elementy nie wchodzą do kolejki.
To realna informacja: zwykle intencje były nierozdzielne i trzeba połączyć je w jedną.

## Commands

```bash
CLI=~/.claude/skills/vc-intents/scripts/intents_cli.py
W=~/.vibecrafted/artifacts/<owner>/<repo>/intents

uv run "$CLI" "$W" sweep --since … --until … --source dir:<transcripts> --source aicx --repo <repo>
uv run "$CLI" "$W" plan --stage extract --repo <repo> --agent '*=junie:gemini-3.8-flash'
uv run "$CLI" "$W" verify-quotes --extractions "$W/extractions" --transcripts <dir> --strict
uv run "$CLI" "$W" lanes [--lane NAME=subject,subject …]
uv run "$CLI" "$W" plan --stage confront --repo <repo> --host <host> --agent 'LANE=agent:model' …
uv run "$CLI" "$W" merge --verdicts <host>=<dir> [--verdicts <host2>=<dir>] --primary <host> --project <owner>/<repo>
uv run "$CLI" "$W" reclassify
uv run "$CLI" "$W" render --html --transcripts <dir> --blob-base <github blob base>
uv run "$CLI" "$W" decide --import-file decisions.jsonl --by founder   # or --id … --to … --reason …
uv run "$CLI" "$W" queue --min-strength 4
uv run "$CLI" "$W" plan --stage implement --repo <repo> --limit N --gate '<repo gate>'
uv run "$CLI" "$W" status      # exit 0 = CLOSED, 1 = OPEN — usable as a gate
```

Każda komenda zapisuje pliki i wypisuje ścieżki oraz liczniki, nic dużego na stdout.
`merge` zachowuje `after`, `risk`, `flow`, `notes`, `runtime_verified` z poprzedniego
ledgera, więc powtórny merge po ponownym dispatchu toru nie gubi wkładu człowieka.

## Re-entry after a cut or a compaction

1. `intents_cli.py "$W" status` — stan celu i powód otwarcia.
2. `intents_cli.py "$W" queue` — czoło `STABLE-QUEUE.md` to następny cut.
3. Przeczytaj `LEDGER.md`: cytaty, werdykty i override'y za tą pozycją.
4. `aicx continuity show -p <owner>/<repo> --hours <N> --for-inject`, jeśli inni
   agenci mogli dotknąć repo od poprzedniej tury.

**Nie powtarzaj sweep ani ekstrakcji przy re-entry.** Są kosztowne i już zapisane.
Powtórz sweep dopiero po nowych materiałach Foundera lub gdy okno przesunęło się
na tyle, że są nowe sesje.
