---
name: vc-research
version: 1.3.0
description: >
  Standalone triple-agent research skill. Co-define the problem with the user,
  write a research plan, then spawn claude + codex + gemini simultaneously on the
  same questions. Three independent reports come back. Synthesize into one
  gap-free research document ready for implementation. Use whenever the team
  needs ground truth before coding: unknown APIs, architecture decisions, library
  assessment, protocol research, best-practice survey, competitive analysis,
  or any situation where one agent's perspective is not enough. Trigger phrases:
  "research this", "zbadaj to", "triple research", "research swarm", "3 agenty
  research", "gap-free research", "zbadaj przed implementacją", "co mówi
  dokumentacja", "state of the art", "SoTA research", "porównaj podejścia",
  "analyze options", "research plan", "plan researchu".
compatibility:
  tools:
    - Bash
    - Read
    - Write
    - Agent
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-research` (launcher `research`)**
>
> Ten sam _kształt_ trzech ścieżek floty, z **literałami tego** skilla — zobacz
> kanoniczną [Matrycę Delegacji](../DELEGATION_MATRIX.md):
>
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna)
> - [Native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy)
>
> | Ścieżka               | Literał tego skilla                                                                                                             |
> | --------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
> | 1. Worker użytkownika | `vibecrafted research <agent(s)>`                                                                                               |
> | 2. Interactive        | `/vc-research` — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                      |
>
> **Uwaga:** May default to multi-agent swarm; interactive still must not no-op into empty re-dispatch.

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-research — Triple-Agent Research Swarm

> Jedna perspektywa to opinia. Trzy perspektywy to dowód.

## Wejście operatora

### Reguła Living Tree / Worktree

Ten workflow działa w bieżącym checkoucie i na bieżącej gałęzi operatora. Nie twórz worktree gita, nie przełączaj się na niego ani nie przenoś do niego wykonania, chyba że operator wprost poprosi o worktree w tym prompcie. Ogólne słowa w stylu „isolate", „parallel" czy „clean branch" to za mało. Jedyny usankcjonowany drugi tryb to dispatch Fleet Worktrees (pisany plan, zacommitowane wcześniej verifiery, rozłączne domeny plików, jednowątkowy integrator — patrz Reguła Living Tree, Tryb B); poza tą formacją zostań we wspólnym drzewie. Czytaj pliki ponownie przed edycją, dostosowuj się do równoległych zmian i zgłoś awarię podłoża (substrate failure), jeśli bieżące drzewo jest zbyt zatrute, by bezpiecznie kontynuować.

Zobacz [Reguła Living Tree](../LIVING_TREE_RULE.md).

## Checkpoint orientacji

Zanim ten workflow wykona analizę specyficzną dla repo, planowanie, implementację, review, release lub delegację, MUSI uruchomić lub skonsumować procedurę `vc-init` dla przypisanego repo. Jeśli brakuje świeżych dowodów z `vc-init`, wykonaj najpierw przebieg init i traktuj pracę specyficzną dla workflow jako zablokowaną, dopóki nie ma aktualnej prawdy repo.

`Loctree:loctree` to domyślny skill do mapowania struktury repo dla tej procedury. Użyj Loctree przed grepem lub twierdzeniami opartymi na dokumentacji, aby wyprodukować lub odświeżyć Mapę Aplikacji Wyprowadzoną z Kodu (Code-Derived Application Map): repo-view, focus, slice, impact, find i follow w odpowiednim zakresie. Szukaj istniejących symboli i kontraktów, zanim utworzysz nowe; uruchom impact przed usunięciem lub dużym refactorem; uruchom slice przed edycją.

Chodzi o znalezienie zaczepów: węzłów nośnych, twins (duplikaty), martwego kodu, dryfu, entrypointów runtime'u oraz pułapek o dużym zasięgu zmiany. Jeśli task jest jawnie nie-repo lub bez kodu, zadeklaruj w raporcie wyjątek no-repo. W przeciwnym razie brak dowodów z `vc-init`/Loctree to awaria procesu.

Wejdź w sesję frameworka przez `vibecrafted start` (lub `vc-start`). Następnie uruchamiaj przez command deck — nigdy przez surowe ścieżki `skills/.../*.sh`:

```bash
vibecrafted research --prompt 'Compare auth libraries for Tauri desktop'
vc-research --prompt 'State of the art for MCP streaming transports'
vibecrafted research --file /path/to/research-plan.md
```

Research domyślnie uruchamia odłączone lane'y headless zarówno wewnątrz, jak i
poza vc-frame. Preferuj `--file` dla istniejącego planu, a `--prompt` dla
intencji inline. Jawnego runtime'u terminal używaj tylko dla kompatybilnościowej
lane'y, która wymaga TTY.

<details>
<summary>Foundation Dependencies</summary>

- [vc-loctree](../foundations/vc-loctree/SKILL.md) — świadomość strukturalna
- [vc-aicx](../foundations/vc-aicx/SKILL.md) — intencje i sterowalność

</details>

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## Cel

Zbadaj problem z trzech niezależnych perspektyw, zanim zaczniesz pisać kod. Orkiestrujący agent współdefiniuje problem z użytkownikiem, pisze plan, spawnuje claude + codex + gemini na te same pytania, a potem syntetyzuje findingi w jeden dokument bez luk. To faza Research z `vc-workflow`, wydzielona jako samodzielny skill i ulepszona o triangulację triple-agent.

## Kiedy używać

- Nieznane API, protokół lub biblioteka
- Decyzja architektoniczna z wieloma poprawnymi podejściami
- „Jaki jest aktualny best practice dla X?"
- Ocena biblioteki (A vs B vs C)
- Research integracji (jak X rozmawia z Y?)
- Każdy moment, w którym zgadywanie byłoby tańsze niż pomyłka

**NIE używaj do:**

- Pytań, na które odpowiada przeczytanie jednego pliku w repo
- Problemów, gdzie loctree slice + grep daje odpowiedź w 30 sekund
- Czystych tasków implementacyjnych (użyj `vc-workflow` przez `vc-agents`; `vc-delegate` tylko dla małej pracy niezależnej od modelu)

## Bezpieczeństwo researchu

Tryb research jest **read-only** dla źródłowego repozytorium.

- **Marker domknięcia = artefakty filesystemu**, nie git. Katalog runu pod `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/research/<run_id>/` z `report.md` + `meta.json` + `transcript.log` to deterministyczna kotwica. Operator weryfikuje przez `ls`, `cat meta.json | jq .status`. Żadne commity gita nie są potrzebne.
- **Brak mutacji źródła.** Nie edytuj źródła repo, konfiguracji, `.gitignore` ani plików generowanych, chyba że plan operatora wprost o to prosi.
- **Brak zapisów do gita.** Żadnego stage, commit, amend, tag, branch, merge, rebase, push, stash, clean, reset, checkout, switch. Working tree niezmienione na końcu. Puste commity / `--allow-empty` / chore stamps — zabronione.
- Jeśli research odkryje oczywisty fix, zapisz proponowany fix i referencje do plików w artefakcie raportu, zamiast go aplikować.
- Workery codex muszą zapisać pełny raport w markdownie pod podaną ścieżkę raportu przez filesystem, zanim zakończą działanie. Finalna wiadomość `codex exec --output-last-message` to tylko notka o ukończeniu, nie trwały raport.

## Sześciokrokowy flow researchu

### Krok 1 — Współdefiniuj problem

Porozmawiaj z użytkownikiem. Nie pisz jeszcze planu. Ustal:

- **Co musimy wiedzieć** — faktyczne pytanie, nie objaw
- **Dlaczego** — jaka decyzja zależy od tej odpowiedzi
- **Co już wiemy** — priors, wcześniejszy dorobek w repo
- **Granice** — co jest poza zakresem

Wynik: stwierdzenie problemu w 3-5 zdaniach uzgodnione z użytkownikiem.

### Krok 2 — Napisz plan researchu

Utwórz jeden plik planu. Każdy agent dostaje ten plan:

```markdown
---
run_id: <generated-unique-id>
agent: <claude|codex|gemini|cursor>
skill: vc-research
project: <repo-name>
status: in-progress
---

# Research Plan: <title>

## Problem

<co-defined problem statement>

## Questions

1. <specific, answerable question>
2. ...

## Mandatory tools

- loctree MCP (repo-view, slice, find, impact) — for codebase questions
- Brave Search or WebSearch — for external ground truth

## Encouraged tools (agent's choice)

- Context7 (resolve-library-id → query-docs) — for library docs
- WebFetch — for URLs found via search
- Codebase grep — for internal patterns (only after loctree mapping)

## Report format

Each question answered with: **Sources**, **Finding**, **Confidence** (high/medium/low), **Evidence**.
Conclude with **Synthesis**: recommended approach, alternatives, open questions, implementation notes.

## Constraints

- Append current year to search queries for freshness
- Prefer primary sources (official docs, RFCs, source code) over blog posts
- If two sources disagree, note the disagreement explicitly
- Do not hallucinate API signatures — verify them
```

`vc-research` zapisuje efektywny plan pod `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/research/<run_id>/plans/<ts>_<slug>_research-plan.md`. Plany można rozdzielić na osobne domeny, ale każdy agent dostaje WSZYSTKIE plany — to niezależni researcherzy, nie specjaliści.

### Krok 3 — Zespawnuj research swarm (rój researchowy)

```bash
PLAN="$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<ts>_<slug>_research-plan.md"
vc-research --file "$PLAN"
```

Skrypty spawnu należące do repo pozostają wewnętrznym silnikiem. Nie dokumentuj surowych ścieżek `bash skills/...spawn.sh` jako entrypointu operatora.

Launcher utrzymuje jeden wspólny `run_id` i uruchamia skonfigurowane lane'y
headless na tym samym planie. Domyślne lane'y to claude + codex + agy. vc-frame
może wyświetlić run; jawna kompatybilnościowa lane terminalowa używa jednej
współdzielonej karty `research.kdl`. Rozbieżność między raportami ujawnia
martwe punkty.

Zaraz po spawnie operator dostaje launch card ze współdzielonym `run_id`, katalogiem runu, katalogiem raportów, ścieżką podsumowania i dokładną komendą oczekiwania. **Launch card to domyślna powierzchnia.** `observe --last` to narzędzie do drilldownu, nie podstawowe źródło prawdy.

### Krok 4 — Zbierz raporty

Raporty lądują w:

```
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/research/<run_id>/reports/{claude,codex,gemini}.md
```

Launch card żyje pod `research/<run_id>/summary.md`. Metadane, transkrypty, surowe strumienie, prompty, launchery, layout Zellij zostają wewnątrz `research/<run_id>/logs/` i `research/<run_id>/tmp/`.

Poczekaj na wszystkie trzy przez dedykowany helper runtime'u:

```bash
vc-research-await --run-id <run_id>
vc-research-await --last     # newest swarm
```

Do inspekcji na poziomie transkryptu, gdy swarm jest w trakcie:

```bash
vibecrafted observe claude --last
vibecrafted observe codex --last
vibecrafted observe gemini --last
```

Nie traktuj ręcznych wywołań `observe --last` jako wystarczającej obserwowalności. Stan workflow domyślnie przechodzi przez metadane launchu, helper oczekiwania i trwałe ścieżki raportów.

### Krok 5 — Syntetyzuj

**Zanim zacytujesz choćby jedną linię któregokolwiek z raportów źródłowych, MUSISZ przeczytać każdy raport w całości przez warstwowe slicowanie.** Bez negocjacji.

Większość raportów ma 30-100KB. Narzędzia obcinają wyjście na ~25KB i zrzucają resztę do pliku z ostrzeżeniem „see path: ...". Pominięcie tego pliku, bo jest „długi", albo praca wyłącznie z tekstem ostrzeżenia to dokładnie ten tryb porażki, któremu ten skill ma zapobiegać. Synteza zbudowana z ostrzeżeń o obcięciu to halucynacja przebrana za eksperckość.

Per raport źródłowy:

1. Przeczytaj w całości przez slicowanie offset/limit w odcinkach ~1500-2000 linii (albo ~80 000 znaków).
2. Odnotuj pokrycie w sekcji syntezy „0. Coverage statement" — linie/bajty per raport źródłowy.
3. Jeśli raport jest zbyt duży na dostępny budżet, **PRZERWIJ (HALT)** i zgłoś granicę. NIE cytuj zakresów linii, których faktycznie nie przeczytałeś.

**Synteza = ekspercka opinia operatora zbudowana NA tych trzech raportach, NIE kopia.** Dwie sekcje: **A. Convergent (zdeduplikowane)** oraz **B. Signals (findingi pojedynczego agenta — potencjalnie kluczowe insighty)**. Reguły głosowania/większości jawnie odrzucone.

- **A. Convergent (Zbieżne)** — findingi, w których dwa lub trzy raporty się pokrywają, zredukowane do jednego stwierdzenia. Cytuj zgodne raporty z file:line. Jeśli jeden nie poruszył danego pytania, zaznacz to wprost (cisza ≠ niezgoda).
- **B. Signals (sygnały)** — findingi wyniesione tylko przez jednego agenta. NIE niższego priorytetu. Często to faktyczny kierunek, którego praca potrzebowała. Per sygnał: co (file:line) + dlaczego inni przeoczyli + verdict operatora (amplify / flag / acknowledge & reject) + uzasadnienie.

### Krok 6 — Wyprodukuj dokument syntezy

Zapisz syntezę do `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/research/<run_id>/synthesis.md` w katalogu runu. **Trzy raporty źródłowe pozostają osobnymi plikami w tym samym katalogu — NIE wklejaj ich inline.**

> Zobacz [references/synthesis-template.md](references/synthesis-template.md) po pełny szablon dokumentu, frontmatter, strukturę sekcji i imperatywy operatora.

Operatorskie bez negocjacji:

1. Synteza NIE zawiera treści verbatim z raportów — tylko cytowania file:line do nich.
2. Raporty pozostają osobnymi plikami w katalogu runu. Niezmienne eksperckie zeznanie.
3. Każda nietrywialna teza w syntezie MUSI mieć referencję file:line do co najmniej jednego raportu.
4. Zdanie odrębne cytuje się z file:line do obu/wszystkich stron + uzasadniony osąd.
5. Synteza jest krótka (zwykle 3–8KB). Jej wartość = jakość interpretacji + precyzja cytowania.

Przedstaw syntezę użytkownikowi. To wejście dla `vc-workflow` Faza 3 (Implement) lub samodzielnej implementacji.

## Integracja z pipelinem

vc-research można użyć:

- **Samodzielnie** — research bez pełnego pipeline'u ERi
- **Jako Faza 2 workflow** — `vc-workflow` deleguje tutaj zamiast researchu pojedynczego agenta
- **Przed vc-partner** — gdy tryb partner potrzebuje twardych faktów (ground truth) przed debugiem
- **Przed vc-runtime/vc-delegate** — research zasila plany implementacji

```
         ┌─── claude ──→ report ───┐
research │                         │
  plan ──├─── codex  ──→ report ───├──→ synthesis.md
         │                         │
         └─── gemini ──→ report ───┘
```

## Antywzorce

- Przekazywanie `claude|codex|gemini` do `vc-research` (niweczy sens — launcher jest swarmem)
- Dawanie każdemu agentowi innych pytań (muszą odpowiadać na TE SAME pytania dla triangulacji)
- Pomijanie syntezy i konkatenowanie raportów (wartość jest w delcie)
- Researchowanie rzeczy, które możesz zweryfikować przez przeczytanie jednego pliku (użyj loctree slice)
- Pisanie planu researchu bez użytkownika (Krok 1 jest wspólny)
- Ufanie blog postom ponad oficjalną dokumentacją
- Pozwalanie agentom researchować bez kontekstu loctree (zadają złe pytania)
- Przeskakiwanie do surowych wywołań `*_spawn.sh`, gdy `*-research` istnieje na realnej powierzchni helperów shellowych
- Patchworkowa synteza meta-artefaktu (konkatenacja verbatim 3 raportów)
- Synteza w trybie skompresowanym (sama parafraza operatora, bez referencji file:line)

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
