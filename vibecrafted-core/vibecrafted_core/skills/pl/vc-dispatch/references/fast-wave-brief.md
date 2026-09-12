# Brief szybkiej fali — checklist a min z pola

Brief szybkiej fali (blitz) pisze dispatcher w sesji. Jest zwięzły, nie cienki:
każda pozycja poniżej zapracowała na swoje miejsce, paląc prawdziwą falę.
Uwzględnij każdą, która dotyczy — w duchu verbatim.

## Szkielet (wszystkie sekcje, w kolejności)

frontmatter (`plan_id`, `role: brief`, `agent`, `date`, `project`) · Misja ·
Kontekst/dowody · Pliki · Akceptacja · Bramki · Poza zakresem · Substrat ·
Loctree-first · Gałąź/commity/raport.

## Miny (polowe runy loctree-suite, 2026-08)

- **Dowody są verbatim, nie streszczone.** Wklej padającą komendę, jej output,
  kotwice linii (`cache.rs:453`) i maszynę, na której to się stało. Worker
  odtwarzający twoją diagnozę pali budżet na archeologię.
- **Baseline SHA przypięty w briefie** — i fetchowany świeżo w chwili dispatchu.
  HEAD przesuwa się między diagnozą a rozkazem (obserwowane: 788400c0 →
  826e88ae w jednej rozmowie).
- **`export CARGO_TARGET_DIR="$PWD/target"`** w każdym briefie rustowym —
  współdzielony target dir cicho podmienia binarki między równoległymi worktree.
- **Licz WSZYSTKIE linie `test result:`** — `cargo test … | tail -1` mierzy
  ostatnią binarkę, nie tę z twoim testem (fałszywe „ok. 0 passed”).
- **`PYTHONPATH=` przed każdą bramką semgrep** — Homebrew semgrep umiera pod
  overlayem python-site Vibecrafted.
- **Lista do-not-touch**: wymień pliki należące do wiszących siostrzanych
  gałęzi (niescalonych fal) i do siostrzanych cięć TEJ fali. Współdzielone
  huby dostają przydziały regionów („locki ~200–300 są w2-b; trzymaj się z dala”).
- **Pliki-huby tylko addytywnie**: na pliku o wysokim fan-in (`types.rs`,
  84 importerów) żądaj nowych pól z serde-default, zero rename'ów, zero zmian
  sygnatur.
- **Klauzula idempotencji**: „przy ponownym uruchomieniu na drzewie, gdzie to
  już wylądowało, zweryfikuj i zatrzymaj się” — refire to najtańszy prymityw
  konwergencji i nie może dublować roboty.
- **≥2 nietrywialne nowe testy** w akceptacji; bramka łapiąca 0 testów jest
  trywialnie zielona.
- **Blok trailerów wypisany wprost**: `[<agent>/workflow] typ(zakres): temat`,
  `Authored-By: <agent> <agents@vetcoders.io>`, `session_id:`, `date:`
  (ISO+strefa), `runtime:` — hook commit-msg odbija wszystko poniżej.
- **Raport do `$VIBECRAFTED_REPORT_PATH`**, push gałęzi po zieleni, ŻADNEGO
  merge do trunka — merge należy do integratora, PR do operatora.

## Blok substratu (worktrees na rozkaz operatora)

```
git -C <main-checkout> worktree add \
  ~/.vibecrafted/worktrees/<org>/<repo>/<YYYY_MMDD>/<slug> \
  -b <agent>/workflow/<slug> <baseline-sha>
cd ~/.vibecrafted/worktrees/<org>/<repo>/<YYYY_MMDD>/<slug>
```

Pracuj TYLKO tam; nigdy nie dotykaj głównego checkoutu; baseline SHA nazwany.

## Piny modeli

Piny nazwane przez operatora jadą dosłownie do `--model` i do tabeli trackera
(`cięcie | worker@model | run_id | gałąź | stan`). Pin operatora to nie
sugestia; cicha podmiana to fałszywa atrybucja decyzji.
