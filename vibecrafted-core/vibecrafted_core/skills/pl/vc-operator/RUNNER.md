---
name: vc-operator-runner
version: 2.0.0
role: deterministic entrypoint
absorbs:
  - REC-1 (7-step deterministic runner)
  - REC-2 (categorical no native subagents as fleet dispatch)
  - REC-3 (/loop primary cadence)
  - REC-4 (journal.md append-only convention)
  - REC-11 (vc-scaffold auto-chain on fuzzy plans)
---

# vc-operator — RUNNER

> **Przeczytaj ten plik najpierw. Wykonaj jeden flow.** Dokumenty towarzyszące (`./EMIL.md`,
> `./DISPATCH.md`, `./AWAIT.md`, `./AUTONOMY.md`, `./FRAME.md`, `./GUIDE.md`,
> `./FLOW.md`, `./DASHBOARD.md`) to materiał referencyjny. Nie bramkują
> runnera. Wyjaśniają poszczególne kroki, gdy potrzebujesz głębi.

**Deklaracja przesunięcia framingu (obowiązkowa, jedna linia):**

```text
Operator mode active — <plan-name>
```

Żadnego 12-liniowego szablonu. Jedna linia. Potem wykonaj siedem kroków.

---

## Siedem kroków

### 1. Przeczytaj wejścia

Skonsumuj, po kolei, każde wejście, które dał ci operator:

- sam prompt operatora (wiadomość, która wywołała `/vc-operator`)
- każdy plik planu / raportu / pomysłu, który operator zacytował verbatim (czytaj
  cały plik, nie streszczenia — użyj Read z zakresami offset/limit, jeśli
  jest ucięty; zobacz `vc-implement` Layered Reading Discipline)
- aktywny katalog artefaktów dla tego runu:
  `~/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/<plan-slug>/`
- każdy wcześniejszy `journal.md` w tym katalogu artefaktów — ciągłość ponad
  ponowne wyprowadzanie

Wywołania narzędzi:

- `Read` na każdej cytowanej ścieżce, pełne pokrycie
- `mcp__aicx-mcp__aicx_search`, jeśli operator odwołał się do wcześniejszej
  sesji agenta po nazwie lub temacie
- `Bash` do `ls` katalogu artefaktów, aby wyliczyć istniejące raporty

Output tego kroku: jeden akapit z powrotem do operatora, nazywający
plan, katalog artefaktów oraz wykrytą liczbę fal.

### 2. Przekształć przez `vc-scaffold`, jeśli plan nie jest dispatchowalny

Kategoryczny trigger — wywołaj `vc-scaffold` (bez wnioskowania, bez
ad-hoc dociągania), gdy **którekolwiek** z poniższych zachodzi:

- plan ma więcej niż 5 promptów i brak grupowania w fale
- plan nie ma grafu zależności (`depends_on` / `parallel_with` /
  `blocks` brakuje na którymkolwiek prompcie)
- plan nie ma trackowalnych cięć (kryteria akceptacji nieobecne lub rozmyte)

Wywołanie narzędzia:

```text
Skill: vc-scaffold
Args: --input <plan-path> --output <artifact-dir>/master-dispatch.md
```

Przebieg scaffoldu produkuje znormalizowany `master-dispatch.md` z
wave atlas + szkieletami treści per prompt. To staje się twoim roboczym
planem od kroku 3 wzwyż. Surowy plan wejściowy jest archiwizowany pod
`<artifact-dir>/inputs/`.

Jeśli żaden z trzech triggerów nie odpala, przejdź do kroku 3 — plan jest
już dispatchowalny.

### 3. Zweryfikuj, że każde cięcie mapuje się na strukturę repo przez Loctree

Dla każdego promptu w (teraz już dispatchowalnym) planie przejdź jego listę `Files
to create / edit` (per `./DISPATCH.md` Sekcja 4) i zweryfikuj
każdy plik przez Loctree:

Wywołania narzędzi:

- `mcp__loctree-mcp__context` raz na korzeniu projektu (materializacja
  atlasu — pierwszy ruch każdej sesji operatora)
- `mcp__loctree-mcp__slice` na każdym pliku w scope cięcia (plik
  istnieje, co od niego zależy, co importuje)
- `mcp__loctree-mcp__impact` na plikach oznaczonych do delete / rename
  / ciężkiego refactoru (zasięg zmiany)
- `mcp__loctree-mcp__find` tryb `where-symbol` na każdym współdzielonym
  typie lub kontrakcie, który plan nazywa (żadnych fantomowych importów)

Jeśli cięcie odwołuje się do pliku, który nie istnieje i nie jest w
grupie `Create:` → zgłoś z powrotem do operatora przed dispatchem. Plan
kłamie o strukturze repo.

Output: adnotacja `cut-verified: true|false` per prompt, dopisana
do frontmattera `master-dispatch.md`.

### 4. Wybierz agenta przez lookup w `WHY_MATRIX_TABLE.md`

Dla każdego promptu rozstrzygnij `recommended_agent` przez lookup, nie przez
wnioskowanie z mermaidu w prozie.

Wywołanie narzędzia:

- `Read` na `./WHY_MATRIX_TABLE.md` (zależność forward — ląduje w
  W2-A tej fali reformy; dopóki nie dowiezie, użyj fallbacku na
  mermaid w `./GUIDE.md` i opatrz adnotacją `agent-lookup: provisional`)
- Klucz lookup: `(task_kind, sensitivity) → agent`
- Zastosuj rotację AGENT FAIRNESS jako tiebreaker — równi
  kandydaci → rotuj Claude → Gemini → Codex w obrębie fali
- Zastosuj AGENT MODEL PARITY jako twardą podłogę — każdy worker działa
  na tierze agenta operatora (rodzic Opus → worker Opus, bez
  wyjątków, bez „tanich równoległych skanów")

Output: każdy prompt w `master-dispatch.md` ma niepusty
`recommended_agent` z jednoliniowym uzasadnieniem lookupu.

### 5. Zbuduj treść dispatchu Iter-3 przez `DISPATCH_TEMPLATE.md`

Dla każdego promptu zmaterializuj dwunastosekcyjną treść Iter-3 przez
podstawienie placeholderów w szablonie.

Wywołania narzędzi:

- `Read` na `./DISPATCH_TEMPLATE.md` (zależność forward — ląduje
  w W2-B tej fali reformy; dopóki nie dowiezie, napisz ręcznie per
  `./DISPATCH.md` „The twelve sections" verbatim i opatrz adnotacją
  `template: hand-authored`)
- `Write` każdej wyrenderowanej treści do
  `<artifact-dir>/briefs/<wave>-<position>_<slug>.md`
- Zweryfikuj, że Sekcja 8 (etykieta Living Tree) to verbatim blok
  z `./DISPATCH.md` — nigdy parafrazowany
- Zweryfikuj, że closing rail Sekcji 12 niesie trzy wymagane elementy
  (jednoliniowiec anti-debt + kaomoji + suchar)

Closing rail jest **obowiązkowy dla briefów kierowanych do workerów**. Artefakty
po stronie operatora (tracker, dziennik, zamknięcie, handoff w punkcie stopu)
nie niosą raila — zobacz `./EMIL.md` Reguła 5.

### 6. Odpal każdy prompt przez `vibecrafted <mode> <agent> --file <brief>`

Każdy spawn idzie przez launcher frameworka. Bez wyjątków.

Kształt wywołania narzędzia:

```bash
vibecrafted <mode> <agent> --file <artifact-dir>/briefs/<wave>-<position>_<slug>.md
```

Gdzie `<mode>` to zdispatchowany skill (`implement`, `marbles`,
`research`, `polarize`, `audit`, `dou`, `hydrate`, `decorate`,
`scaffold`, `init`), a `<agent>` to rozstrzygnięty agent z
kroku 4.

**Antywzorzec (kategoryczny, REC-2):** nigdy nie używaj natywnych subagentów
(narzędzie `Task`, `vc-delegate`) jako zamienników dla zdispatchowanych slice'ów
workerów w trybie operatora. Każdy spawn workera floty musi iść przez
launcher frameworka. Natywne subagenty pozostają dozwolone dla równoległego zwiadu
lub małego bounded researchu wewnątrz sesji operatora.

**Uzasadnienie (nie racjonalizuj się wokół niego):**

- telemetria — każde odpalenie launchera zapisuje `meta.json` + transkrypt
  - ścieżkę raportu, natywne subagenty nie
- obserwowalność — receipt, stan control-plane, transkrypt i raport pozostają
  dostępne, gdy odłączone workery działają headless; vc-frame może wyświetlać
  te powierzchnie, ale nie hostuje workera. Natywne subagenty nie rejestrują
  tego samego kontraktu runu floty
- odzyskiwanie — zacięty dispatch z launchera ma znaną doktrynę
  odzyskiwania w `./AWAIT.md`; stall natywnego subagenta jest niewidoczny

Przed odpaleniem przeskanuj treść każdego promptu pod kątem niebezpiecznych komend i triggerów
hard-stop. Jeśli prompt prosi o niedozwolone działanie hard-stop, odmów
dispatchu i zapisz rozwidlenie w `journal.md`.

Kształt fali per `./GUIDE.md` (A foundation / B sequential / C
parallel / D close-out). Odpalaj po jednej fali naraz. W obrębie fali
odpalaj wszystkie równoległe prompty w jednej paczce; sekwencyjne prompty
czekają na wylądowanie poprzedniego commita.

### 7. Wejdź w kanoniczny runtime pętli i dopisz do `journal.md`

Użyj `vibecrafted loop` jako kanonicznej powierzchni interaktywnej kontynuacji,
gdy agent operatora musi utrzymać stan między odpowiedziami:

```bash
vibecrafted loop start --file <artifact-dir>/master-dispatch.md --max-iterations <n> --completion-promise "<done-condition>"
vibecrafted loop next
vibecrafted loop complete --promise "<done-condition>"
```

Domyślny stan pętli jest zakotwiczony w korzeniu repo w
`<repo-root>/.vibecrafted/operator-loop.local.md`, co jest lokalnym stanem runtime'u
i nie wolno go commitować. Runtime pętli to mechanizm kontynuacji i
łańcucha await; nie zastępuje doktryny decyzji operatora w tym runnerze.

Po odpaleniu fali wejdź w `vibecrafted loop` / `/loop` jako **główną** kadencję
po dispatchu (REC-3). Heartbeat `ScheduleWakeup` to tylko siatka
bezpieczeństwa **fallback** — zobacz `./AWAIT.md` po tabelę opóźnień.

Wywołania narzędzi per wybudzenie:

- na każdym wybudzeniu `/loop` **oraz** na każdym `<task-notification>` i
  na każdym odpaleniu heartbeatu dopisz jeden wpis do:
  `<artifact-dir>/journal.md` (REC-4 — pojedyncza rosnąca oś czasu tylko do
  dopisywania, nie trzy oddzielne artefakty)
- przeczytaj raport workera przez `Read` (cały plik, nie surowy
  transkrypt output zadania)
- zweryfikuj, że commit wylądował na `result_branch` przez
  `Bash: git log -1 <result-branch>`
- zweryfikuj, że bramki są zielone, czytając sekcję gate-output raportu
- przerzuć `[ ]` → `[x]` w trackerze fali per `./EMIL.md` Reguła 1

Kształt wpisu do dziennika (per wybudzenie):

```markdown
## <ISO-timestamp> — <event-kind>

- run_id: <run-id>
- wave: <wave>-<position>
- agent: <agent>
- status: <fired | notify-received | heartbeat-fire | stop-point>
- next move: <one-line>
```

Kadencja pętli: `/loop 25m` jest domyślna dla kroków Wave B i
równoległych Wave C (per tabela heartbeatu `./AWAIT.md`); operator
nadpisuje wedle potrzeby. `/loop` wychodzi czysto, gdy tracker fali
osiąga wszystkie `[x]` lub gdy krok 7 dochodzi do punktu stopu.

---

## Warunek stopu

```text
Operator stops at the operator's button for actions not already permitted
by the written plan or current session — force-push, trunk push, merge,
public release, deploy, paid action.
```

Zobacz `./AUTONOMY.md` po harmonogram hard-stop (powierzchnia git +
powierzchnie zewnętrzne + zaufanie/bezpieczeństwo/billing + powierzchnia
skill/konwencja) oraz szablon handoffu w punkcie stopu. Soft stopy
(zmiana kształtu dispatchu, pominięcie scope, dodanie scope, rebase,
cherry-pick) mogą przebiec bez nowego przycisku, gdy nie zmieniają
finalnego celu; każda taka mutacja musi być zapisana w `journal.md`.
Mutacje zmieniające scope nadal wymagają przycisku.

Gdy tracker fali jest cały `[x]`, a następny ruch jest po stronie operatora
bez zezwolenia spisanego plan/sesja, napisz handoff w punkcie stopu per
`./AUTONOMY.md` „The stop-point handoff" i wyjdź. Nie force-pushuj, nie
pushuj trunka, nie merguj, nie deployuj, chyba że spisany plan lub
bieżąca sesja jawnie na to zezwala. Niedestruktywny push gałęzi feature
jest już dozwolony — zrób go po swoich commitach.

---

## Akceptacja — runner jest skończony, gdy

- [ ] krok 1 wejścia przeczytane i potwierdzone
- [ ] krok 2 albo wywołał `vc-scaffold`, albo potwierdził, że plan jest
      dispatchowalny
- [ ] krok 3 każde cięcie ma adnotację `cut-verified: true`
- [ ] krok 4 każdy prompt ma `recommended_agent` z lookupu
- [ ] krok 5 każdy brief wyrenderowany do `<artifact-dir>/briefs/`
- [ ] krok 6 każde odpalenie workera poszło przez launcher `vibecrafted`
      (natywne subagenty tylko do zwiadu/researchu, nie do dispatchu floty)
- [ ] krok 7 `journal.md` jest aktualny; tracker fali jest aktualny
- [ ] handoff w punkcie stopu napisany i operator powiadomiony

---

## Klamra końcowa

```text
=======================
Siedem kroków. Żadnych niespodzianek. Przeczytaj jeden plik, wykonaj jeden flow.
Towarzysze stoją w pogotowiu jako referencja, nigdy jako bramka. Operator
jest właścicielem przycisku — runner tylko prowadzi do niego agentów.
(งಠ_ಠ)ง
=======================

Suchar: Dlaczego RUNNER.md nigdy nie zaczyna się od „To zależy"?
Bo następny agent już to robi. (._.)
```

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
