---
name: vc-agents
version: 3.1.0
description: >
  Spawn external specialized AI agents from the user's fleet (Codex, Claude, Gemini).
  Use this when you need parallel execution, deep isolation, or task-specific cognitive 
  strengths that surpass generic in-thread delegation.
  Trigger: "vc-agents", "/vc-agents", "delegate to agents", "spawn".
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-agents` (launcher `agents`)**
>
> Ten sam _kształt_ trzech ścieżek floty, z **literałami tego** skilla — zobacz
> kanoniczną [Matrycę Delegacji](../DELEGATION_MATRIX.md):
>
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna)
> - [Native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy)
>
> | Ścieżka               | Literał tego skilla                                                                                                                                |
> | --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. Worker użytkownika | (kontrakt floty — tryby external wg udokumentowanych spawn path)                                                                                   |
> | 2. Interactive        | załaduj `vc-agents` jako doktrynę — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                                         |
>
> **Uwaga:** External fleet **contract**; interactive skills still execute in-session.

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-agents — Zewnętrzna flota wykonawcza

## Wejście operatora

### Reguła Living Tree / Worktree

Ten workflow działa w bieżącym checkoucie i na bieżącej gałęzi operatora. Nie twórz worktree gita, nie przełączaj się na niego ani nie przenoś do niego wykonania, chyba że operator wprost poprosi o worktree w tym prompcie. Ogólne słowa w stylu „isolate", „parallel" czy „clean branch" to za mało. Jedyny usankcjonowany drugi tryb to dispatch Fleet Worktrees (pisany plan, zacommitowane wcześniej verifiery, rozłączne domeny plików, jednowątkowy integrator — patrz Reguła Living Tree, Tryb B); poza tą formacją zostań we wspólnym drzewie. Czytaj pliki ponownie przed edycją, dostosowuj się do równoległych zmian i zgłoś awarię podłoża (substrate failure), jeśli bieżące drzewo jest zbyt zatrute, by bezpiecznie kontynuować.

Zobacz [Reguła Living Tree](../LIVING_TREE_RULE.md).

## Checkpoint orientacji

Zanim ten workflow wykona analizę specyficzną dla repo, planowanie, implementację, przegląd, release lub delegowanie, MUSI uruchomić lub skonsumować procedurę `vc-init` dla przypisanego repo. Jeśli brakuje świeżych dowodów z `vc-init`, najpierw wykonaj przebieg init i traktuj pracę specyficzną dla workflow jako zablokowaną, dopóki nie ma aktualnej prawdy repo.

`Loctree:loctree` to domyślny skill do mapowania struktury repo dla tego przebiegu. Używaj Loctree przed grepem lub twierdzeniami opartymi na dokumentacji, aby wygenerować lub odświeżyć Mapę Aplikacji Wyprowadzoną z Kodu (Code-Derived Application Map): repo-view, focus, slice, impact, find i follow w odpowiednim zakresie. Szukaj istniejących symboli i kontraktów, zanim utworzysz nowe; uruchom impact przed usunięciem lub dużym refactorem; uruchom slice przed edycją.

Chodzi o znalezienie zaczepów: węzłów nośnych, twins (duplikaty), martwego kodu, dryfu, entrypointów runtime'u oraz pułapek o dużym zasięgu zmiany. Jeśli zadanie jawnie nie dotyczy repo lub nie dotyczy kodu, odnotuj w raporcie wyjątek „bez repo". W przeciwnym razie brak dowodów z `vc-init`/Loctree to błąd procesu.

Operator wchodzi do sesji frameworka przez:

```bash
vibecrafted start
# or
vc-start
# same default board as: vc-start operator
```

`vc-agents` to kontrakt delegacji stojący za aktywnymi workflow, a nie podstawowa komenda
operatora, którą founder wpisuje jako pierwszą. Entrypoint dla operatora pozostaje:

```bash
vibecrafted <launcher> <agent> \
  --<options> <values> \
  --<parameters> <values> \
  --file '/path/to/plan.md'
```

```bash
vc-<launcher> <agent> \
  --<options> <values> \
  --<parameters> <values> \
  --prompt '<prompt>'
```

`vc-<launcher> <agent>` uruchamia odłączonego workera headless niezależnie od
tego, czy vc-frame działa. User Session może wyświetlać jego transkrypt i stan,
ale nie hostuje procesu. `vc-agents` definiuje, jak ten run launchera rozkłada
się na zewnętrznych workerów.

### Konkretne przykłady dispatchu

```bash
vibecrafted implement codex /path/to/plan.md
vibecrafted implement claude /path/to/plan.md
vibecrafted implement gemini /path/to/plan.md
```

> Nie outsourcujemy myślenia. Wdrażamy równie zdolne umysły na równoległych ścieżkach wykonania, aby chronić główny bufor kontekstu.

Pojedyncza sesja agenta niesie ogromny kontekst. Próba wykonania każdego małego rewrite'u, forensycznego deep-dive'u czy radykalnej zmiany strukturalnej in-thread powoduje rozdęcie promptu i rozcieńcza twój fokus.

`vc-agents` to warstwa delegacji zewnętrznej. Identyfikujesz lukę strukturalną, dobierasz
właściwy umysł do zadania z **`vc-why-matrix`**, spawnu­jesz autonomicznego zewnętrznego
workera i wracasz do swojej głównej orkiestracji.

Ten skill służy wyłącznie do zewnętrznych workerów. Natywna delegacja in-process należy do
`vc-delegate`, nie tutaj.

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## `vc-why-matrix`

Nie spawnu­jesz agentów na ślepo. Dobierasz profil poznawczy wymagany do danego cięcia.

```mermaid
  graph TD
    subgraph Codex
        CodexDesc[Precision & Surgery]
        CodexBest[Best for:\n\n– Critical implementations\n– Exact refactors\n– Contract-gated execution]
        Codex --> CodexDesc
        Codex --> CodexBest
    end

    subgraph Claude
        ClaudeDesc[Forensics & Research]
        ClaudeBest[Best for:\n\n– Bug hunts across deep layers\n– Architecture audits\n– Assessing unknown paths]
        Claude --> ClaudeDesc
        Claude --> ClaudeBest
    end

    subgraph Gemini
        GeminiDesc[Radical Reframing]
        GeminiBest[Best for:\n\n– Architecture leaps\n– Fearless simplification\n– Stripping dead scaffolding]
        Gemini --> GeminiDesc
        Gemini --> GeminiBest
    end
```

## Doktryna delegacji

- **Deleguj, nie mikrozarządzaj:** Nie produkuj 15-punktowych biurokratycznych checklist dla zespawnowanego agenta. Napisz wysokopoziomowy plan z `Goal`, `Scope` i `Acceptance Criteria`. Pozwól mu samemu rozkminić _jak_.
- **Żywe Drzewo (Living Tree):** Agenci muszą wiedzieć, że działają w żywym systemie. Zadbaj, by twój plan spawnu stwierdzał: _„Pracujesz na żywym drzewie. Równoległe zmiany są spodziewane. Dostosowuj się proaktywnie."_
- **Pełna wymiana ponad tkankę bliznowatą:** Powiedz swoim agentom, że mają mandat do przepisywania zepsutych abstrakcji. Czasem pełna wymiana jest czystsza niż łatanie kiepskiego kodu prototypu.

## Autorytet eskalacji

`vc-agents` to warstwa orkiestracji na poziomie operatora.

Decyzja o użyciu `vc-agents` już koduje intencję `vc-why-matrix`:
operator wybrał konkretną rodzinę modeli i profil poznawczy do tej
misji.

Z tego powodu:

- zespawnowani agenci floty nie mogą sami wywołać `vc-agents` ponownie
- zespawnowani agenci floty nie mogą ponownie otwierać selekcji modelu ani uruchamiać drugiej zewnętrznej floty
- zespawnowani agenci floty nie mogą reinterpretować `vc-why-matrix`
- eskalacja do `vc-agents` należy wyłącznie do agenta-operatora

Jeśli zespawnowany worker odkryje, że powierzchnia misji jest szersza, bardziej równoległa
lub mniej bounded, niż się spodziewano, nie powinien eskalować sam na zewnątrz.

Zamiast tego musi:

- ukończyć przydzieloną misję na tyle, na ile to uczciwie możliwe
- zarejestrować napotkaną granicę
- jasno nazwać nierozwiązaną powierzchnię w swoim raporcie
- pozostawić wszelkie zmiany orkiestracji operatorowi

Worker floty może ujawnić presję orkiestracyjną.
Nie może na nią działać.

## Szablon planu

```markdown
---
run_id: <generated-unique-id>
agent: <claude|codex|gemini|agy|junie|grok|cursor>
skill: vc-agents
project: <repo-name>
status: <pending|in-progress|completed|failed>
loops_completed: <number>
---

# Task: <short title>

Goal:

- <1-3 bullets>

Scope:

- In scope: <files/areas> as high-level suggestions
- Out of scope: <explicit>

Constraints:

- No --no-verify
- Follow repo conventions

Acceptance:

- [ ] <objective outcome>
- [ ] <objective outcome>

Test gate:

- <command(s)>

Context:

- <very short summary>

Living tree note:

- You work on a living tree with 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚜𝚖𝚊𝚗𝚜𝚑𝚒𝚙 methodology, so concurrent changes are expected.
- Adapt proactively and continue, but this is never permission to skip quality, security, or test gates.
- Run required checks. If something is blocked, report the exact blocker and run the closest safe equivalent.
- Coordination mode: <solo on this stage / parallel with other agents on this stage>
- You do not need to inspect other agents' plans unless this plan explicitly tells you to.
- **Commit is an obligation, not a checkpoint option: ONE commit per round** (marbles — one round = one commit), well-formed per the commit-msg hook, on the current branch. Do NOT leave delivered work uncommitted. Non-destructive remote push of the current feature branch (`git push -u origin HEAD`, not force, not trunk) is a duty after that commit. Force-push, trunk push, merge, and deploy stay operator buttons. When the mission spans multiple rounds/units, multi-commit per dispatch is expected.
- You are an execution unit, not orchestration authority: do not invoke `vc-agents`, do not reopen frontier selection, and do not reinterpret the `vc-why-matrix`.
- If the mission reveals a wider unresolved surface, report that boundary clearly and leave orchestration changes to the operator.
```

## Komendy spawnu

Ścieżka launchu dla operatora przy delegacji out-of-process prowadzi przez
command deck `vibecrafted` lub helper `vc-<launcher>`. Skrypty spawnu należące do
repo pozostają wewnętrznym silnikiem stojącym za tą ścieżką.

### Codex

```bash
PLAN="$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<plan-slug>.md"
vibecrafted implement codex "$PLAN"
```

### Claude

```bash
PLAN="$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<plan>.md"
vibecrafted implement claude "$PLAN"
```

### Gemini

```bash
PLAN="$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<plan>.md"
vibecrafted implement gemini "$PLAN"
```

Jeśli te narzędzia są niedostępne, przestań udawać, że spawn jest poprawnie skonfigurowany, i powiedz to wprost.

## Konwencja outputu

- Plany: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<timestamp>_<slug>.md` lub inna stabilna
  nazwa pliku per task
- Raporty: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.md`
- Transkrypty: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.transcript.log`
- Metadane: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.meta.json`

Każdy spawn powinien wystawić launch card od razu po dispatchu.
Ta karta powinna ujawniać co najmniej:

- `run_id`
- wybranego agenta / rodzinę modeli
- ścieżkę planu
- ścieżkę raportu
- ścieżkę transkryptu
- ścieżkę metadanych

Jeśli operator nie widzi tych ścieżek, obserwowalność jest niekompletna, nawet jeśli
agent technicznie działa.

## Obserwacja

Obserwuj postęp przez trwałe artefakty w `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/`.

Domyślne sprawdzenie jest metadata-first, nie pane-first.
Użyj dedykowanego helpera runtime'u, aby poczekać na ukończenie metadanych i wypisać
finalne podsumowanie:

```bash
vibecrafted await codex --run-id <run_id>
```

Dla najnowszego runu danego agenta:

```bash
vibecrafted await codex --last
```

Przy wielu zespawnowanych workerach przekaż ich ścieżki launchera lub metadanych wprost do
helpera i pozwól mu poczekać na wszystkie naraz.

Jeśli twoje środowisko udostępnia helper obserwatora, użyj go do inspekcji na poziomie
transkryptu lub do debugowania:

```bash
vibecrafted observe codex --last
```

Użyj odpowiedniego obserwatora agenta, gdy trzeba, ale nie polegaj na `observe` jako
jedynej powierzchni statusu. `vc-agents` powinien pozostać operowalny z trwałych
artefaktów nawet wtedy, gdy operator nie wpatruje się w żywe pane'y.

## Oczekiwania bramki jakości

Trzymaj standardowy poziom jakości 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.:

- loctree-mcp jako narzędzie odkrywania i wyszukiwania pierwszego wyboru, z fail-fast, gdy niedostępne
- semgrep jako strażnik bezpieczeństwa pierwszego wyboru, gdy dostępny
- repozytoria Rust: `cargo clippy -- -D warnings`
- repozytoria nie-Rust: wybierz najbliższy równoważny gate lint/typów/testów
- Testy: uruchom, jeśli robisz review; napisz, jeśli implementujesz nowe zachowanie; preferuj realne pokrycie e2e dla rzeczywistego pipeline'u
- Jeśli bramka jest zablokowana, zgłoś dokładny blocker i uruchom najbliższy bezpieczny odpowiednik

## Reguły bezpieczeństwa

- Nie loguj sekretów ani nie commituj plików `.env`.
- Nigdy nie używaj `--no-verify` przy `commit` ani `push`.
- Nie przepisuj historii gita, chyba że użytkownik wprost o to poprosi.
- Traktuj równoległe edycje jako normalne, ale i tak weryfikuj przed nadpisaniem.
- Jeśli repo ma ścisłą komendę w stylu `make check`, uruchom ją albo wyjaśnij, dlaczego nie.

## Zasada końcowa

Flota nie służy do outsourcingu myślenia.
Flota służy do wdrażania równie zdolnych agentów pierwszej linii przez ścisłą, domyślną ścieżkę launchu.
Używaj ich, żeby implementować, a nie tylko komentować implementację.
