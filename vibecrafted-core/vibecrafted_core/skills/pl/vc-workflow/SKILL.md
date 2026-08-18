---
name: vc-workflow
version: 1.0.0
description: >
  This skill should be used when the user asks to "examine and implement",
  "research then implement", "zbadaj i zaimplementuj", "workflow", "pipeline",
  "examine → research → implement", "full workflow", "ERi pipeline", "ERi",
  "plan and implement", "analyze then build", "structured implementation",
  "przebadaj repo i zaimplementuj", or describes a task that requires
  understanding code structure before making changes. Orchestrates a
  three-phase pipeline: Examine (loctree), Research (Brave Search / web),
  Implement (subagents). Each phase feeds context to the next.
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-workflow` (launcher `workflow`)**
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
> | 1. Worker użytkownika | `vibecrafted workflow <agent>`                                                                                                  |
> | 2. Interactive        | `/vc-workflow` — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                      |
>
> **Uwaga:** ERi pipeline only. Other skills are not ERi by paste.

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Workflow — pipeline ERi

## Wejście operatora

### Reguła Living Tree / Worktree

Ten workflow działa w bieżącym checkoucie i na bieżącej gałęzi operatora. Nie twórz worktree gita, nie przełączaj się na niego ani nie przenoś do niego wykonania, chyba że operator wprost poprosi o worktree w tym prompcie. Ogólne słowa w stylu „isolate", „parallel" czy „clean branch" to za mało. Jedyny usankcjonowany drugi tryb to dispatch Fleet Worktrees (pisany plan, zacommitowane wcześniej verifiery, rozłączne domeny plików, jednowątkowy integrator — patrz Reguła Living Tree, Tryb B); poza tą formacją zostań we wspólnym drzewie. Czytaj pliki ponownie przed edycją, dostosowuj się do równoległych zmian i zgłoś awarię podłoża (substrate failure), jeśli bieżące drzewo jest zbyt zatrute, by bezpiecznie kontynuować.

Zobacz [Reguła Living Tree](../LIVING_TREE_RULE.md).

## Checkpoint orientacji

Zanim ten workflow wykona analizę specyficzną dla repo, planowanie, implementację, review, release lub delegację, MUSI uruchomić lub skonsumować procedurę `vc-init` dla przydzielonego repo. Jeśli brakuje świeżych dowodów (evidence) z `vc-init`, najpierw wykonaj przebieg init i traktuj pracę specyficzną dla workflow jako zablokowaną, dopóki nie ma aktualnej prawdy repo.

`Loctree:loctree` to domyślny skill do mapowania struktury repo dla tego przebiegu. Użyj Loctree przed grepem lub twierdzeniami z dokumentacji, aby wyprodukować lub odświeżyć Mapę Aplikacji Wyprowadzoną z Kodu (Code-Derived Application Map): repo-view, focus, slice, impact, find i follow w odpowiednim zakresie. Szukaj istniejących symboli i kontraktów, zanim utworzysz nowe; uruchom impact przed delete lub dużym refaktorem; uruchom slice przed edycją.

Chodzi o znalezienie zaczepów: węzłów nośnych, twins (duplikaty), martwego kodu, dryfu, entrypointów runtime'u oraz pułapek o dużym zasięgu zmiany. Jeśli task jest jawnie nie-repo lub no-code, zadeklaruj w raporcie wyjątek no-repo. W przeciwnym razie brak dowodów (evidence) z `vc-init`/Loctree to awaria procesu.

Standardowy launcher (`vibecrafted start` / `vc-start`, następnie `vc-<launcher> <agent> [--prompt|--file ...]`).

```bash
vibecrafted workflow claude --prompt 'Examine auth surface and implement fixes'
vc-workflow codex --prompt 'Research SSO options then implement the best fit'
vibecrafted workflow agy --file /path/to/research-plan.md  # gemini deprecated
```

Zależności fundamentowe (ładowane wraz z frameworkiem): `vc-loctree`, `vc-aicx`.

**Examine. Research. Implement.** Trzyfazowy pipeline, który łańcuchuje strukturalną
inteligencję kodu, research na twardych faktach (ground truth) i równoległą delegację
agentów. Każda faza akumuluje kontekst dla następnej — żadnej ślepej implementacji.

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## Pozycja w pipelinie

```
scaffold → init → [WORKFLOW] → followup → marbles → dou → decorate → hydrate → release
```

## Przegląd pipeline'u

```
 EXAMINE (loctree)         RESEARCH (web)          IMPLEMENT (agents)      CONVERGE (marbles+polarize)
 ┌────────────────┐        ┌────────────────┐      ┌────────────────┐      ┌────────────────┐
 │ repo-view      │        │ Brave Search   │      │ write plans    │      │ marbles: fix   │
 │ focus 1-3 dirs │ ─────▸ │ WebFetch docs  │ ───▸ │ spawn agents   │ ───▸ │ gates (P0=0)   │
 │ slice + impact │        │ Context7 libs  │      │ collect reports│      │ polarize: align│
 │ find symbols   │        │ curate         │      │ review + merge │      │ docs & product │
 └────────────────┘        └────────────────┘      └────────────────┘      └────────────────┘
        ↓                          ↓                       ↓                       ↓
   CONTEXT.md                 RESEARCH.md             REPORTS/*.md            THESIS.md
```

Kanoniczny katalog główny artefaktów: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/{plans,reports,tmp}/`.
Finalne artefakty Markdown używają `%Y-%m-%d_<org>_<repo>_<full_session_id>-<kind>.md`
(`kind=report,plan,tracker,research,...`) z pasującymi sidecarami `.transcript.log` i
`.meta.json`. `CONTEXT.md` i `RESEARCH.md` żyją w `plans/` jako
`<ts>_<slug>_CONTEXT.md` i `<ts>_<slug>_RESEARCH.md`. `../../runtime/scripts/common.sh`
`spawn_prepare_paths()` to źródło prawdy dla rozwiązywania korzenia dnia (day-root).
Lokalne dla repo `.vibecrafted/plans` i `.vibecrafted/reports` to tylko wygodne
symlinki.

## Faza 1 — EXAMINE

Zmapuj bazę kodu, zanim czegokolwiek dotkniesz. Skille fundamentowe to główna
warstwa sensoryczna.

1. **Skonsumuj wyjścia `vc-init`** — przeczytaj `AGENTS.md` i raport
   sytuacyjny. Jeśli `vc-init` nie był uruchomiony, uruchom go najpierw.
2. **Pogłęb mapę (loctree)** poza bazową linię z initu:
   - `slice(file)` dla każdego pliku, który prawdopodobnie się zmieni (zależności + konsumenci)
   - `impact(file)` dla plików będących węzłami nośnymi lub kandydatami do usunięcia
   - `find(name)` przed utworzeniem jakichkolwiek nowych typów/funkcji
3. **AICX (intencje)** — `aicx extract`, jeśli wyjście poprzedniej sesji jest zbyt duże
   lub w surowym JSONL.
4. **PRView** — wygeneruj najpierw artefakty, jeśli workflow jest częścią review PR-a.
5. **Screenscribe** — skonsumuj findingi, jeśli task wziął się z wizualnego dema.

### Wyjście: CONTEXT.md

Zapisz do `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<ts>_<slug>_CONTEXT.md`:

```markdown
---
run_id: <id>
agent: <claude|codex|gemini>
skill: vc-workflow
project: <repo>
status: completed
created: <ISO-8601>
---

# Examination: <slug>

## Repo Health

- <3-5 bullets from repo-view>

## Scope

- Target dirs: <list>
- Why: <rationale>

## Critical Files

| File | Consumers | Risk | Notes |

## Symbols Found

- <existing symbols relevant to task>

## Risk Map

- <high-impact files + mitigation>

## Decision

- [ ] Research needed (unknown APIs/patterns)
- [ ] Skip to Implement (well-understood domain)
```

### Bramka fazy

Przedstaw podsumowanie CONTEXT.md. Zapytaj: **Research czy Implement?** Jeśli domena jest
dobrze zrozumiana, pomiń Fazę 2.

## Faza 2 — RESEARCH

Dla głębokich niewiadomych architektonicznych lub dużych dochodzeń **NIE prowadź
ad-hoc researchu samodzielnie.** Przekaż pytania wyprowadzone z Examination do
`vc-research` (rój triple-agent) i skonsumuj jego raport.

Dla prostych lookupów (pojedynczy parametr API, składnia pliku) użyj Brave Search / Context7 /
WebFetch bezpośrednio: zapytaj `"<API> usage example <year>"`, pobierz standardową dokumentację.

### Wyjście: RESEARCH.md

```markdown
---
run_id: <id>
agent: <claude|codex|gemini>
skill: vc-workflow
project: <repo>
status: completed
created: <ISO-8601>
---

# Research: <slug>

## Questions (from Examination)

1. <question>

## Findings

### Q1: <question>

- **Source**: <URL or Context7 lib>
- **Answer**: <concise>
- **Code example**: <if applicable>

## Architectural Decision

- Chosen: <decision>
- Why: <findings-based>
- Alternatives rejected: <reasons>

## Implementation Notes

- <concrete guidance for agents>
```

### Bramka fazy

Przedstaw podsumowanie RESEARCH.md. Zapytaj: **Przejść do Implement?**

## Faza 3 — IMPLEMENT

Uzbrojony w CONTEXT.md + RESEARCH.md, deleguj do równoległych agentów.

### Szablon planu agenta

Każdy plan MUSI zawierać:

1. **Obowiązkowy frontmatter** — `run_id`, `agent`, `skill (vc-workflow/vc-agents)`, itd.
2. **Kontekst pipeline'u** — wklej odpowiednie sekcje z CONTEXT.md + RESEARCH.md.
3. **Preambuła z instrukcją loctree** (sprawdzona kompletność 98% vs 85%):
   ```
   Use loctree MCP tools as your primary exploration layer:
   - repo-view(project) first for overview
   - slice(file) before modifying any file
   - find(name) before creating new symbols
   - impact(file) before deleting
   Never edit code without mapping it first.
   ```
4. **Reguła living tree** — standardowa preambuła 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.
5. **Bramka jakości** — komendy testów/lintu specyficzne dla repo.

### Wzorzec spawnowania

Postępuj wg `vc-agents` po komendy spawnowania (preferowane skrypty przenośne). Plany →
domyślnie `plans/`, raporty → domyślnie `reports/` pod
`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`. Lokalne dla repo
`.vibecrafted/plans` i `.vibecrafted/reports` to tylko wygodne symlinki.

### Faza 4 — CONVERGE (Marbles & Polarize)

Po ukończeniu pracy przez agentów implementacyjnych kod istnieje, ale może nie być prawdziwy ani gotowy do dowiezienia.
Nie zatrzymuj się na implementacji. Przejdź przez granicę zbieżności:

1. **Sprawdzenie bramek** — Przeczytaj wszystkie raporty, uruchom bramki jakości (`make check`), zweryfikuj mapę ryzyka.
2. **Prawda kodu (`vc-marbles`)** — Jeśli bramki padają, testy są czerwone lub ścieżka runtime'u jest krucha:
   - **NIE ZATRZYMUJ SIĘ.** Nie przedstawiaj podsumowania diffa z zepsutymi testami lub znanymi lukami.
   - Wywołaj `vc-marbles`, aby zapętlić, dopóki bramki nie staną się zielone (P0=0), a baza kodu nie przestanie kłamać.
3. **Prawda produktu (`vc-polarize`)** — Gdy kod jest stabilny (bramki przechodzą), sprawdź „rozmaz koncepcyjny" (np. sprzeczna dokumentacja, niejednoznaczne publiczne interfejsy lub architektoniczne „split-brain", gdzie dwie poprawne ścieżki ze sobą rywalizują).
   - Jeśli koncepcja jest rozmazana, uruchom `vc-polarize --task <concept>` i pozwól, by prism band-action contract zadecydował: `0..4 abort`, `5..8 memo`, `9..12 full pass`, `13..15 doctrine pass with regression contract`.
4. **Przekazanie** — Przedstaw finalne podsumowanie diffa i/lub `THESIS.md` gotowe dla `dou` i Release.

### Kadencja commitów

Jeden commit na rundę (marbles: jedna runda = jeden commit), commitowany lokalnie na bieżącej
gałęzi, dobrze sformowany wg hooka commit-msg — dowieziona praca nigdy nie zostaje niezacommitowana.
Przebieg vc-workflow produkuje **do 3 commitów** (fazy zapisu — Implement, Marbles, Polarize
— każda commituje swoją rundę). Niedestruktywny remote push tej gałęzi feature to obowiązek po tych commitach. Force-push, push na trunk, merge i deploy zostają przyciskami operatora.

## Szybka ściąga

| Faza      | Narzędzie                          | Wyjście                         |
| --------- | ---------------------------------- | ------------------------------- |
| Examine   | loctree MCP                        | `plans/<ts>_<slug>_CONTEXT.md`  |
| Research  | brave-search + Context7 + WebFetch | `plans/<ts>_<slug>_RESEARCH.md` |
| Implement | vc-agents (portable scripts)       | `reports/*.md`                  |

## Pomijanie faz

- Mała poprawka, znana domena → tylko Examine, implementuj bezpośrednio
- Integracja nowego API/biblioteki → wszystkie trzy fazy
- Refaktor → Examine + Implement (bez zewnętrznego researchu)
- Tylko research → Examine + Research (jeszcze bez implementacji)

Na starcie pipeline'u zadeklaruj, które fazy mają zastosowanie.

## Notatki

- Obowiązkowe dla nietrywialnej, wieloplikowej pracy nad funkcją.
- Jeśli loctree MCP jest niedostępne, zobacz `references/phase-examine.md` po fallback na grepa.
- Brave Search pochodzi z powierzchni narzędziowej runtime'u lub fallbacku web search, nie z lokalnego katalogu wrappera.

## Dodatkowe zasoby

- `references/phase-examine.md` — wzorce głębokiej analizy loctree
- `references/phase-research.md` — metodologia researchu, ranking źródeł
- `references/phase-implement.md` — delegacja agentów z akumulowanym kontekstem
- `scripts/pipeline-init.sh` — inicjalizacja domyślnych ścieżek artefaktów

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
