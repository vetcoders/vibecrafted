# vc-scaffold — `plans/HOWTO.md`: konwencja MASTER + plans + TRACKER

> Gdy zescaffoldowany pomysł jest za duży na jeden prompt, staje się planem.
> Gdy plan jest za duży na jednego agenta, staje się ukształtowanym w fale łańcuchem
> dispatchu. Ten HOWTO koduje layout artefaktów, który łączy
> `vc-scaffold` (brainstorm → plan) z `vc-operator` (plan → dispatch
> → close-out).

Czytaj razem z [vc-scaffold SKILL](../SKILL.md), [vc-operator EMIL](../../vc-operator/EMIL.md),
[vc-operator GUIDE](../../vc-operator/GUIDE.md),
[vc-operator DISPATCH](../../vc-operator/DISPATCH.md).

---

## 1) Root planu oparty na manifeście

Każdy solidny plan jest jednym kanonicznym katalogiem z jawnym manifestem:

| Artefakt     | Nazwa pliku                     | Rola                                                                                |
| ------------ | ------------------------------- | ----------------------------------------------------------------------------------- |
| **MANIFEST** | `manifest.json`                 | Uporządkowane ID, jawne role, ścieżki, edytowalność, wymaganie i zależności         |
| **DRIVER**   | `DRIVER.md`                     | Wykonywalne przekazanie dla zimnego operatora                                       |
| **MASTER**   | `00_ATLAS.md`                   | Atlas: dlaczego-budujemy + struktura fal + protokół dispatchu + reguły odzyskiwania |
| **plans/**   | `01-<slug>.md` … `0N-<slug>.md` | Jedno ciało promptu Iter-3 na każdy zdispatchowany prompt                           |
| **TRACKER**  | `tracker.md` (jeden żywy plik)  | Append-only, stan checkboxów fala po fali                                           |

Wszystkie trzy żyją pod domyślną ścieżką artefaktów:

```text
~/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<plan_id>/
├── manifest.json
├── DRIVER.md
├── 00_ATLAS.md
├── briefs/01-<wave>-<slug>.md
├── briefs/02-<wave>-<slug>.md
├── ...
├── briefs/0N-<wave>-<slug>.md
└── tracker.md
```

`manifest.json` jest obowiązkowy i jest jedynym inwentarzem. Role nigdy nie wynikają z nazw plików.
Nie twórz lustra `operator/`, duplikatu ani symlinka.

Numeracja to kolejność dispatchu (z góry na dół = kolejność odpalania). Slug w nazwie
pliku niesie literę fali, dzięki czemu `ls` daje się skanować ludzkim okiem.

---

## 2) Kształt atlasu MASTER

```markdown
# Plan: <Title> — <one-line tagline>

> Captured <YYYY-MM-DD>. <Akapit głosem operatora: dlaczego bieżący kształt
> zawodzi, jaki jest kształt docelowy, dlaczego teraz.>

Reference baseline branch: `<branch>@<sha>`.
Dispatch target: `vc-operator` on `<host>`.
Mandate: `<skill>` for every prompt — `<one-line scope-bound>`.

## 1) Why the current shape fails (1:1)

[Wypowiedziana/wpisana diagnoza operatora, verbatim gdzie się da.]

## 2) Target shape

[Diagram ASCII architektury / layoutu / flow stanu końcowego.]

## 3) Reusable pieces from the existing tree

| Surface | Reuse from | Notes |
| ------- | ---------- | ----- |
| ...     | ...        | ...   |

## 4) Out of scope for this plan

- [ ] [item jawnie POZA scope'em 1]
- [ ] [item jawnie POZA scope'em 2]

## 5) N prompts for `<dispatcher>`

[Krótki zarys — jedna sekcja na prompt, plus link do jego
pliku ciała `0N-<slug>.md`.]

### Prompt 1 — `<slug>` (`<wave>`)

**Mission**: [jeden akapit].
**Files**: see `01-<slug>.md`.
**Agent**: `<recommended-agent>`.
**Acceptance bar**: [jednoliniowe podsumowanie].

### Prompt 2 — `<slug>` (`<wave>`)

...

## 6) Dispatch order + dependencies

[Graf Mermaid lub drzewo ASCII pokazujące strukturę fal oraz
strzałki sekwencyjne/równoległe.]

## 7) Operator handoff

[Jeden akapit opisujący, jak operator przekazuje plan
operator-agentowi: który plik podać przez `--file`, którego triggera użyć,
kto pushuje wynikowe gałęzie.]
```

Głos operatora + pieczęć `(1:1)` na Sekcji 1 = „to jest diagnoza operatora,
nie redakcja agenta". W Sekcji 2+ zrzuć pieczęć tam,
gdzie zsyntetyzowałeś.

---

## 3) Kształt ciała per prompt (`0N-<slug>.md`)

Dwanaście sekcji wg [`vc-operator/DISPATCH.md`](../../vc-operator/DISPATCH.md).
Podsumowanie:

````markdown
---
prompt_id: <slug>-<YYYYMMDD>
wave: <A|B|C|D>
position: <1..N within wave>
mandate: /<skill>
recommended_agent: <claude|codex|gemini|cursor>
parent_branch: <branch>@<sha>
result_branch: feat/<slug>
depends_on: [<prompt_ids>]
parallel_with: [<prompt_ids>]
blocks: [<prompt_ids>]
report_path: ~/.vibecrafted/artifacts/<...>/reports/<slug>_<ts>_<agent>.md
authored_by: <agent> <agents@vetcoders.io>
---

# Prompt <N> — <slug>

[Akapit misji — co ląduje, gdy ten prompt się powiedzie.]

## 1) Context

[Bullety wskazujące pliki / SHA / kontrakty do przeczytania najpierw.]

## 2) Files to create / edit

[Pogrupowana lista, z markerami APPEND-ONLY na plikach współdzielonych.]

## 3) Acceptance

- [ ] [obserwowalny wynik 1]
- [ ] [obserwowalny wynik 2]
- [ ] All existing tests stay green.

## 4) Gates

```bash
<exact commands>
```
````

## 5) Out of scope

- [DO NOT touch] [item 1]
- [DO NOT touch] [item 2]

## 6) Living Tree etiquette (verbatim)

[Standardowy blok z vc-operator/DISPATCH.md Sekcja 8.]

## 7) Loctree first

[Standardowy blok z vc-operator/DISPATCH.md Sekcja 9.]

## 8) Recovery hint

[Standardowy blok — substrate / scope / zacięcie implementacji.]

## 9) Branch + commit convention

[Nazwa gałęzi, szablon tytułu commita, Authored-By, do-not-push.]

## 10) Report path + Call to Action + closing rail

[Kanoniczna ścieżka + sekcje raportu + blok railu Emila: jednoliniówka
antydługowa (งಠ_ಠ)ง + Call to Action (sekwencyjny tryb rozkazujący) + Suchar (._.).]

````

Closing rail mówi workerowi *„teraz ty jesteś agentem, a
operator czyta raport, nie diff"* — zobacz
[`../../vc-operator/DISPATCH.md`](../../vc-operator/DISPATCH.md) Sekcja
„Klamra końcowa — domyślny blok Emila", gdzie jest wymagany kształt i
bank sucharów.

---

## 4) Kształt TRACKER-a

Jeden żywy plik, append-only na poziomie fali. Każda fala dostaje jedną
sekcję; wiersze przechodzą `- [ ]` → `- [x]`, gdy commity lądują.

```markdown
# Tracker — <plan title>

Plan: `00-master-dispatch.md`
Started: <YYYY-MM-DD HH:MM Z>
Operator-agent session: `<session-uuid>`

## Wave A (foundation)
- [x] A-1 <slug> (<agent>) — `<sha>` on `<branch>` · report: `<path>`

## Wave B (sequential, <coordination note>)
- [x] B-1 <slug> (<agent>) — `<sha>` on `<branch>` · report: `<path>`
- [x] B-2 <slug> (<agent>) — `<sha>` on `<branch>` · report: `<path>`
- [ ] B-3 <slug> (<agent>) — 🔄 firing, await `<task-id>`, ETA `<minutes>`
- [ ] B-4 <slug> (<agent>) — pending

## Wave C (parallel, file-scope disjoint)
- [ ] C-1 <slug> (<agent>) — pending
- [ ] C-2 <slug> (<agent>) — pending
- [ ] C-3 <slug> (<agent>) — pending

## Wave D (final, sequential)
- [ ] D-1 <slug> (<agent>) — blocked by Wave B+C merge
- [ ] D-2 <slug> (<agent>) — blocked by D-1

## Operator action queue ("wystarczy wcisnąć guzik")

- [ ] Push `<branch>` to origin
- [ ] Open PR `<branch>` → `develop`
- [ ] Merge wave B into trunk before firing wave C (if applicable)

## Recovery dispatches (if any)

- [x] `<prompt-id>-recovery-<ts>` recovers `<original-id>` — `<sha>`
- [ ] ...
````

**Dyscyplina aktualizacji**: operator-agent edytuje ten plik po każdym close-oucie fali,
nie po każdym commicie. Jedna edycja na falę utrzymuje historię diffów
czystą, a plik skanowalny.

---

## 5) Konwencje nazewnicze

### Slug w nazwie pliku

- kebab-case, małe litery
- zaczyna się od litery fali dla skanowalności przez ls: `01-a-shell.md`,
  `02-b-editor-core.md`, …
- kończy się slugiem, który pasuje do docelowej nazwy gałęzi minus prefiks
  `feat/`: gałąź `feat/textforge-editor-core` → plik
  `02-b-textforge-editor-core.md`

### prompt_id

Ten sam slug + datownik: `textforge-editor-core-20260516`. Używany jako
cross-walk do retrievalu sesji. Stabilny mimo dispatchów odzyskiwania —
odzyskiwanie używa `<original-id>-recovery-<ts>`, żeby retrieval wciąż mógł zrobić join.

### Nazwa gałęzi

`feat/<slug>` dla pracy nad ficzerem, `chore/<slug>` dla porządków,
`fix/<slug>` dla hotfixów.

---

## 6) Punkty przekazania między skillami

`vc-scaffold` pisze MASTER + ciała per prompt + początkowy TRACKER.
`vc-operator` konsumuje je i dispatchuje. Przekazaniem jest
domyślna ścieżka artefaktów — operator-agent ładuje z
`~/.vibecrafted/artifacts/<...>/dispatch/`, bez potrzeby dalszej
koordynacji.

Gdy plan dochodzi do close-outu:

- Ostatni prompt w planie pisze wpis backlogu close-out pod
  `docs/backlog/` repo konsumenta (wg
  [`vc-init/backlog/HOWTO.md`](../../vc-init/backlog/HOWTO.md)).
- „Operator action queue" TRACKER-a wylicza pozostałe guziki.
- Operator-agent pisze przekazanie w punkcie stopu (wg
  [`vc-operator/AUTONOMY.md`](../../vc-operator/AUTONOMY.md) Sekcja
  „The stop-point handoff").

---

## 7) Antywzorce

- **Jeden plik na wszystko**: zlanie MASTER + ciał w jeden
  plik Markdown. Ciała muszą dać się ładować pojedynczo przez
  `vc-justdo <agent> --file 02-b-editor-core.md`.
- **Nieaktualny TRACKER**: zapomnienie o przerzuceniu `[ ]` → `[x]` po zazielenieniu fali.
  Operator nie może audytować postępu; przyszli agenci re-derywują stan.
- **Numeracja wg czasu wpadnięcia na pomysł zamiast kolejności dispatchu**: 01- 02- 03-
  muszą odzwierciedlać kolejność, w jakiej operator-agent odpala, nie kolejność,
  w jakiej autor planu o nich pomyślał.
- **Brakujący `parent_branch` we frontmatterze**: worker nie wie,
  z czego się odgałęzić; zgaduje; psuje łańcuch.
- **Plan w Google Docu**: nie jest w `~/.vibecrafted/artifacts/`, więc
  retrieval go nie znajdzie, więc przyszli agenci go nie znajdą. Plany żyją
  na dysku, w domyślnych ścieżkach.

---

## 8) Przykład: plan TextForge jako studium przypadku

Plan: `~/.vibecrafted/artifacts/<org>/<repo>/2026_0516/dispatch/`

- `00-master-dispatch.md` — atlas Fala A→D
- `01-textforge-editor-core.md` — ciało Promptu 2 (Fala B-1)
- `02-textforge-tool-rail.md` — ciało Promptu 3 (Fala B-2)
- ...
- `09-textforge-e2e-docs.md` — ciało Promptu 10 (Fala D-2)
- `tracker.md` — append-only stan checkboxów fal

Zdispatchowano 2026-05-16. Fala B 3/4 zielone w momencie pisania tego HOWTO —
B-1 `304791be` claude, B-2 `ba60ef66` gemini, B-3 `ab32a848` codex,
B-4 odpala. Żywy dowód, że konwencja się skaluje.

---

## Wezwanie do działania

Zanim ogłosisz plan ukończonym, zweryfikuj, że istnieją trzy pliki:
`00-master-dispatch.md`, co najmniej jeden `0N-<slug>.md` oraz `tracker.md`.
Potem przekaż ścieżkę operator-agentowi — odpali Falę A i
zaplanuje heartbeat bez dalszego promptowania.

---

## Klamra końcowa

```text
=======================
Plan to nie lista życzeń. To kontrakt między brainstormerem
a dyrygentem — trzy pliki, jedna domyślna ścieżka, jeden tracker, który
przerzuca się z [ ] na [x]. Uhonoruj layout, a dispatch staje się
nudny w najlepszym możliwym sensie. (งಠ_ಠ)ง
=======================

Suchar: Dlaczego plany bez trackera zawsze są spóźnione? Bo nikt nie
pamięta, który checkbox przerzucić, gdy SHA w końcu wyląduje. (._.)
```

---

_Vibecrafted. with AI Agents (c)2024–2026_
