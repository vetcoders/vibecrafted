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

> **Wywołanie `vc-agents` (launcher `agents`)**
>
> Trzy ścieżki zachowują tożsamość skilla według
> [Matrycy Delegacji](../DELEGATION_MATRIX.md):
> [wspólne ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki),
> [katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime),
> [reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna),
> [native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy).
>
> | Ścieżka            | Wywołanie                                                                                                                                           |
> | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
> | Worker użytkownika | kontrakt floty — tryby zewnętrzne przez udokumentowane ścieżki spawnu                                                                               |
> | Interactive        | załaduj `vc-agents` jako doktrynę i wykonaj w tej sesji; używaj natywnych subagentów, gdy trzeba; sam launcher nie uzasadnia zewnętrznego dispatchu |
> | Agent-Operator     | może dispatchować workera przez `vc-dispatch` lub linie Operatora, zachowując tożsamość skilla                                                      |
>
> To kontrakt floty zewnętrznej; skille interaktywne nadal działają w sesji.
> Swobodniejszy native nie zastępuje floty external. `vc-dispatch` i `vc-ship`
> zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-agents — zewnętrzna flota wykonawcza

## Wejście Operatora

### Living Tree / Worktree

Pracuj w bieżącym checkoucie i na bieżącej gałęzi Operatora. Nie twórz worktree
bez jawnego wyboru; drugim usankcjonowanym trybem jest dispatch Fleet Worktrees:
plan, wcześniej zacommitowane verifiery, rozłączne domeny, jednowątkowy integrator.
Czytaj ponownie przed edycją; zgłoś substrate failure, jeśli drzewo jest zatrute.
Pełna reguła: [Living Tree](../LIVING_TREE_RULE.md).

## Checkpoint orientacji

Przed analizą repo, planowaniem, implementacją, review, release lub delegacją
uruchom albo skonsumuj `vc-init`. Brak świeżych dowodów blokuje pracę.
`Loctree:loctree` buduje Mapę Aplikacji Wyprowadzoną z Kodu
(Code-Derived Application Map): repo-view/focus/slice/impact/find/follow.
Szukaj przed tworzeniem, sprawdzaj impact przed usuwaniem i slice przed edycją.
Brak dowodów jest błędem procesu. Pełny checkpoint: [vc-init](../vc-init/SKILL.md).
Mapę trzeba przeczytać; sama obecność atlasu nie dowodzi przeczytania.

Operator wchodzi do sesji frameworka przez:

```bash
vibecrafted start
# or
vc-start
# same default board as: vc-start operator
```

`vc-agents` to kontrakt delegacji za aktywnymi workflow, nie pierwsza komenda
Foundera. Wejście dla Operatora:

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
ale nie jest hostem procesu. `vc-agents` określa fan-out na zewnętrznych workerów.

### Przykłady dispatchu

```bash
vibecrafted implement codex /path/to/plan.md
vibecrafted implement claude /path/to/plan.md
vibecrafted implement gemini /path/to/plan.md
```

Nie zlecamy myślenia na zewnątrz: rozdzielamy równie zdolne umysły na równoległe
ścieżki wykonania, aby chronić główny bufor kontekstu. Każdy drobny rewrite,
śledztwo lub skok strukturalny wykonywany w jednym wątku powiększa prompt
i rozmywa skupienie. Rozpoznaj lukę, wybierz profil z `vc-why-matrix`, uruchom
zewnętrznego workera i wróć do orkiestracji. Ten skill dotyczy wyłącznie floty
zewnętrznej; delegacja natywna w procesie należy do `vc-delegate`.

## Doktryna pracy z repo

Najpierw Loctree (`loct context/occurrences/body/find --literal`), AICX dla
historii intencji, rg/grep jako lokalna lupa. Braki Loctree zgłaszaj przez append
w `~/.vibecrafted/loctree/loctree-fail.md`.

## vc-why-matrix

Wybieraj profil poznawczy potrzebny do cutu, nie losowego agenta.
Historyczna trójka ([diagram](references/why-matrix.md)): Codex — precyzja
i chirurgia; Claude — forensics i research; Gemini/agy — radykalne
przeformułowanie i domyślnie tekst. Bieżący skład to OŚMIU agentów: wybór wynika
z ekonomii zadeklarowanej przez Foundera na dany dzień, nie z przyzwyczajenia.

Słowa mają własny profil poznawczy. Proza, dokumentacja, narracja, copy skilli
lub marketingu, tłumaczenia i tekst dla ludzi trafiają domyślnie do Gemini
lub Claude. Atut Codexa to precyzyjna praca nad kodem i kontraktami. W zadaniu
mieszanym rozdziel mechanikę/kod od tekstu; to dopasowanie do pracy, nie ocena agenta.

## Doktryna delegacji

- Deleguj zamiast mikrozarządzać. Plan określa Goal, Scope i Acceptance Criteria;
  agent sam ustala sposób realizacji. Nie pisz 15-punktowej biurokracji.
- Plan podaje, że drzewo żyje, równoległe zmiany są oczekiwane i trzeba się dostosować.
- Daj prawo zastąpienia zepsutej abstrakcji: pełna wymiana bywa czystsza niż łaty prototypu.

## Prawo do eskalacji

`vc-agents` jest warstwą orkiestracji Operatora. Wybrany model i profil poznawczy
kodują intencję `vc-why-matrix`. Workerzy floty nie wywołują ponownie `vc-agents`,
nie otwierają wyboru modeli, nie uruchamiają drugiej floty zewnętrznej i nie
reinterpretują macierzy. Eskalacja należy wyłącznie do Operatora.

Gdy misja okaże się szersza lub mniej ograniczona, worker wykonuje ją tak daleko,
jak uczciwie może, zapisuje granicę, nazywa nierozstrzygniętą powierzchnię
w raporcie i pozostawia zmianę orkiestracji Operatorowi. Może ujawnić presję,
ale nie może sam eskalować.

Zakaz dotyczy WYŁĄCZNIE floty zewnętrznej. Natywne subagenty w procesie
(Claude Task przez [vc-delegate](../vc-delegate/SKILL.md), swarm Kimi,
natywne podsesje runtime'u) są prawem workera, a przy równoległym planie — jego
obowiązkiem. Rola workera ogranicza zakres i lifecycle, nie native fan-out
(Matryca Delegacji → Native vs external). Jednostka wykonawcza nie oznacza
jednostki szeregowej: rozłączne subcuty wykonywane kolejno na modelu frontier
są kosztownym sposobem na wolne wykonanie.

## Pętla integratora snap-dispatch (wzorzec Foundera, 2026-10-03)

Pełny [runbook](references/runbook.md): (1) gęsty plan zawiera ZMIERZONE ścieżki,
kształty rekordów, liczby kontrolne i kotwice z liniami; (2) kanon Foundera
odzyskany z AICX oznacz jako „implement exactly, never reinterpret”;
(3) jeden snap launchera na cut, prefiks tekstowy przed planem, czyste env,
await uzbrojony od razu; (4) integrator powtarza testy w worktree, bada żywą
powierzchnię, przypisuje każdy czerwony wynik dopiero po BASELINE-DIFF na czystym
HEAD, integruje przez `--no-ff`, push i raportuje koszt providera;
(5) model i effort są guzikami kosztowymi Foundera — provider 400 oznacza STOP
i pytanie, nigdy podmianę.

## Szablon planu

Użyj [szablonu](references/plan-template.md): frontmatter run_id/agent/skill/project/status,
Goal, Scope, Constraints, Acceptance, Test gate, Context i reguła żywego drzewa.
Równoległe zmiany są oczekiwane, jeden commit na rundę jest obowiązkiem,
native fan-out jest wyjątkiem od zakazu orkiestracji; tiery dobieraj według
vc-delegate → Native Delegation Policy.

## Runbook — bieżąca gramatyka launchera

Agenci: `claude · codex · agy · junie · grok · cursor · kimi · copilot`.
Pełna gramatyka, prefiks przed planem z pliku, wstrzyknięcie CLI `--help`,
usuwanie zmiennych sesji Claude oraz await/observe/stop/usage są w
[runbooku](references/runbook.md). Zapamiętaj kształt:

```bash
vibecrafted <launcher> <agent> --repo "$(pwd)" --model <founder's cost pick> \
  [--effort <tier>] --worktree true --prompt "Plan follows.\n\n$(cat "$PLAN")"
```

Natychmiast po dispatchu uzbrój `vibecrafted await <agent> --run-id <id>`.
Każde rozliczenie podaje `vibecrafted usage --run-id <id>` i koszt providera.
Jeśli narzędzia są niedostępne, jawnie zgłoś brak poprawnej konfiguracji spawnu.

## Artefakty i obserwacja

Ścieżki pod `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`, karta launchera
i pełna doktryna await/trzech sygnałów żywotności są w runbooku.
Każdy spawn pokazuje kartę; supervisor od razu uzbraja await. Doraźny poller
obok await jest naruszeniem klasy 3. Dwa zgodne sygnały wystarczą do działania,
trzy do uznania zakończenia.

## Bramki jakości

- loctree-mcp jako pierwsze narzędzie odkrywania i wyszukiwania; fail-fast przy braku dostępu
- Semgrep jako pierwszy guard bezpieczeństwa, gdy dostępny
- Rust: `cargo clippy -- -D warnings`; poza Rust najbliższy lint/type/test
- Uruchamiaj testy przy review, dodawaj dla nowego zachowania; preferuj e2e rzeczywistego pipeline'u
- Zablokowana bramka wymaga dokładnej przyczyny i najbliższego bezpiecznego odpowiednika

## Bezpieczeństwo

- Nie loguj sekretów ani nie commituj `.env`.
- `--no-verify` wyłącznie dla jawnego, autoryzowanego przez Foundera lokalnego
  checkpointu compile-embargo, z receiptem pominiętych hooków/bramek.
  Worker nigdy z tym nie pushuje; taki push należy wyłącznie do Foundera.
- Nie przepisuj historii Git bez jawnego żądania użytkownika.
- Równoległe edycje są normalne, ale przed nadpisaniem weryfikuj stan.
- Uruchom ścisłą bramkę repo, np. make check, albo wyjaśnij blokadę.

## Zasada końcowa

Flota służy wdrażaniu równie zdolnych agentów przez ścisłą domyślną ścieżkę
launchera, nie outsourcingowi myślenia. Używaj jej do implementacji,
nie tylko komentowania implementacji.
