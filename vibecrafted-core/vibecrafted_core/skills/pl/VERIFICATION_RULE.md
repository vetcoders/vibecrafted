---
title: Verification Rule
kind: core_rule
version: 2.0.0
description: "Walk around the truck verification rule: real artifact execution over synthetic green CI."
scope: framework
status: active
---

# Reguła weryfikacji — obejdź ciężarówkę

Vetcoders dowożą dowodem, nie bramkami. Zanim jakikolwiek worker powie „done",
„shippable" albo „ready" — nawet o pracy, którą osobiście nadzorował — obchodzi
ciężarówkę i sprawdza każdy pas, zanim powie „teraz możesz jechać".

## Twarda reguła

- Uruchom PRAWDZIWY artefakt, który uruchomi user. Odpal aplikację (nie tylko
  `--version` ze świeżego builda); zamontuj DMG i zrób `otool -L` / odpal
  aplikację ze środka; przejdź ścieżkę runtime'u, nie tylko bramki.
- Signed / notarized / `spctl: accepted` / zielony `cargo check` / przechodzące
  testy ≠ działa. To są pasy widoczne z kabiny, nie obejście dookoła.
- Nigdy nie ufaj weryfikacji z upstreamu — cudzemu codesign, notaryzacji ani
  zielonemu CI innego agenta czy pipeline'u — jako dowodowi runtime'u.
  Zweryfikuj sam od nowa.
- Sprawdź własny przyrząd. Komenda weryfikacyjna, która nie może się wywalić, to
  luźny pas na samym sprawdzającym pasy (`grep | sed && echo` zawsze kończy się
  kodem 0 — kłamie).
- Zatrzymanie się na guziku operatora po pełnym obejściu jest poprawne, nie
  słabe. „Teraz możesz jechać" zasługuje się obejściem, nie załadunkiem.

## Dlaczego

Release vc_terminal przeszedł `codesign` + notaryzację + `spctl: accepted`,
został ostaplowany i zainstalowany do `/Applications` — i wywalił się
`SIGABRT`-em przy starcie (libgit2 Team-ID, hardened runtime). Każdy pas
widoczny z kabiny był napięty. Luźny wyłapało dopiero odpalenie binarki +
`otool -L` + zamontowanie DMG.

## Checkpointy dowodowe

Weryfikacja nie jest warstwą ceremonii. Jest infrastrukturą atrybucji.

Minimalne checkpointy cyklu życia to:

1. **Przyjęcie przed pracą** — przed działaniem przeczytaj ponownie stan repo,
   branch, `HEAD`, dirty pliki i wcześniejsze raporty.
2. **Baseline przed zmianą** — zapisz bieżące checki albo znane failures, zanim
   stwierdzisz, że regresja pojawiła się później.
3. **Implementacja** — trzymaj scope i granice odpowiedzialności jawne.
4. **Baseline przed handoffem** — zanim przejmie inny agent, zapisz branch,
   `HEAD`, `git status --short`, zmienione pliki, bramki, znane failures oraz
   dokładną następną instrukcję / ścieżkę raportu.
5. **Przyjęcie handoffu** — agent przejmujący porównuje baseline z żywym drzewem
   przed edycją.

Nie traktuj tego jako opcjonalnego procesu. Pomijając checkpoint dowodowy, nie
oszczędzasz czasu; niszczysz atrybucję.

## Loct jest przyrządem — literal vs semantic

Wybierz soczewkę wg tego, gdzie mieszka odpowiedź:

- **Semantyczne mapowanie kodu** (PIERWSZY ruch przy każdym pytaniu
  strukturalnym — nie grep): `loct context`, `loct slice` / `impact`,
  `loct find --mode who-imports|where-symbol`, `loct follow dead|cycles|twins`,
  `loct health`, `loct suppressions`, `loct env-truth`; MCP `slice` / `impact` /
  `follow`. Dla tego, gdzie mieszka symbol, kto importuje X, jaki jest zasięg
  zmiany przy edycji albo usunięciu Z, osiągalności, martwego kodu, cykli, twins,
  silencerów, kontraktów env — grafy AST / importerów / dispatchu.
- **Literalne wystąpienia + analiza ciała**: `loct find --literal <text>`,
  `loct occurrences <id>`, `loct body <symbol>`; MCP `find mode=literal`. Dla
  dokładnych trafień tekstu/identyfikatora z `occurrence_kind`
  (string_literal / reference / definition), stringów błędów, pinów wersji,
  ścieżek w configach, treści komentarzy/markdownu oraz czytania prawdziwego
  ciała funkcji. Prawda na granicy identyfikatora; pokrycie podawane per query;
  „not found" znaczy not found.

Odruch: „czy odpowiedź mieszka w grafach AST/importerów/dispatchu, czy w
literalnym tekście?" Pierwsze → loct semantic. Drugie → `loct --literal`. grep
tylko jako lokalna lupa po loct, z zalogowaniem nieudanej komendy loct do
`~/.vibecrafted/loctree/loctree-fail.md`.

## Dotyczy

- Closing rail w treści każdego skilla WRITE (Implement, Workflow, Marbles,
  Polarize, Hydrate) — weryfikuj przed handoffem.
- Sekcja 6 (Gates) i Sekcja 9 (Loctree first) każdego promptu dispatchu
  workera — zob. `vc-operator/DISPATCH_TEMPLATE.md`.
- Każdy skill READ stawiający claim o runtimie (Review, Audit, Followup, DoU).

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 The LibraxisAI Team_
