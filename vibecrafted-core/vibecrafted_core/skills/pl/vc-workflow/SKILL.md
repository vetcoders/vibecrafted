---
name: vc-workflow
version: 3.6.0
description: >
  This skill should be used when the user asks to "examine and implement",
  "research then implement", "workflow", "pipeline", "examine → research → implement", "full workflow", "ERi pipeline", "native fleet workflow",
  "plan and implement", "analyze then build", "structured implementation"
  or describes a task that requires understanding code structure before making changes. Orchestrates a three-phase pipeline: Examine (loctree), Research (Brave Search / web), Implement (subagents). Each phase feeds context to the next.
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
native_fleet: "use native fleet delegation widely"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie `vc-workflow` (launcher `workflow`)**
>
> Trzy ścieżki według [Matrycy Delegacji](../DELEGATION_MATRIX.md):
> [wspólne ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki),
> [katalog](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime),
> [reguła launchera](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna),
> [native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy).
>
> | Ścieżka            | Wywołanie                                                                                      |
> | ------------------ | ---------------------------------------------------------------------------------------------- |
> | Worker użytkownika | `vibecrafted workflow <agent>`                                                                 |
> | Interactive        | `/vc-workflow` w tej sesji; natywni subagenci, gdy trzeba; sam launcher nie uzasadnia external |
> | Agent-Operator     | dispatch przez vc-dispatch/linie Operatora z zachowaniem tożsamości skilla                     |
>
> To pipeline ERi. Inne skille nie stają się ERi przez wklejenie jego tekstu.
> Swobodniejszy native nie zastępuje floty external; vc-dispatch/vc-ship
> zachowują tożsamość.

<!-- /fleet-imperative -->

# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Workflow — pipeline ERi

## Wejście Operatora

### Living Tree / Worktree

Pracuj w bieżącym checkoucie i na bieżącej gałęzi; bez worktree, chyba że został
jawnie wybrany. Drugim usankcjonowanym trybem jest dispatch Fleet Worktrees:
plan, wcześniej zacommitowane verifiery, rozłączne domeny, jednowątkowy integrator.
Czytaj ponownie przed edycją; przy zatrutym drzewie zgłoś substrate failure.
Pełna [reguła Living Tree](../LIVING_TREE_RULE.md).

## Checkpoint orientacji

Przed analizą repo, planowaniem, implementacją, review, release i delegacją
uruchom albo skonsumuj vc-init; brak świeżych dowodów blokuje pracę.
`Loctree:loctree` buduje Mapę Aplikacji Wyprowadzoną z Kodu
(Code-Derived Application Map): repo-view/focus/slice/impact/find/follow.
Szukaj przed tworzeniem, impact przed usuwaniem, slice przed edycją.
Brak dowodów jest błędem procesu. Pełny checkpoint: [vc-init](../vc-init/SKILL.md).
Mapę przeczytaj, nie poprzestawaj na obecności atlasu.

Standardowe wejście: vibecrafted start/vc-start, potem
`vc-<launcher> <agent> [--prompt|--file ...]`.

```bash
vibecrafted workflow claude --prompt 'Examine auth surface and implement fixes'
vc-workflow codex --prompt 'Research SSO options then implement the best fit'
vibecrafted workflow agy --file /path/to/research-plan.md   # gemini deprecated; agy is Google replacement
```

Fundamenty ładowane z frameworkiem: vc-loctree, vc-aicx i
[vc-delegate](../vc-delegate/SKILL.md) — polityka native fan-out w fazie 3.

Examine. Research. Implement. Pipeline łączy mapę strukturalną kodu, research
oparty na faktach i równoległą delegację. Każda faza przekazuje kontekst dalej;
implementacja nie jest ślepa.

## Doktryna repo

Najpierw Loctree (`loct context/occurrences/body/find --literal`), AICX dla
historii intencji, rg/grep jako lokalna lupa. Braki Loctree zgłaszaj przez append
w `~/.vibecrafted/loctree/loctree-fail.md`.

## Miejsce w pipeline

```
scaffold → init → [WORKFLOW] → followup → marbles → dou → decorate → hydrate → release
```

## Przegląd pipeline

EXAMINE (loctree repo-view → focus → slice/impact → find) ⇒ CONTEXT.md →
RESEARCH (brave/WebFetch/Context7, wybrane źródła) ⇒ RESEARCH.md →
IMPLEMENT (fan-out, raporty, review+merge) ⇒ reports/*.md →
CONVERGE (marbles P0=0 → polarize align) ⇒ THESIS.md.

Kanoniczny root artefaktów:
`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/{plans,reports,tmp}/`.
`spawn_prepare_paths()` w `../../runtime/scripts/common.sh` jest właścicielem
nazewnictwa/day-root; repo-local `.vibecrafted/{plans,reports}` są tylko symlinkami.

## Faza 1 — EXAMINE

Mapuj przed edycją. Fundamenty są podstawową warstwą percepcji.

1. Skonsumuj vc-init: AGENTS.md i raport sytuacyjny; jeśli go brak, najpierw init.
2. Pogłęb mapę: slice dla każdego kandydata do edycji (deps+consumers), impact
   dla hubów i kandydatów do usunięcia, find przed nowym typem/funkcją.
3. AICX: aicx extract, gdy poprzednia sesja jest za duża lub w surowym JSONL.
4. PRView: przy review PR najpierw generuj artefakty.
5. Screenscribe: skonsumuj ustalenia, jeśli zadanie pochodzi z demo wizualnego.

### Wyjście: CONTEXT.md

Zapisz `plans/<ts>_<slug>_CONTEXT.md` według
[szablonu](references/output-templates.md): frontmatter, zdrowie repo, scope,
pliki krytyczne, symbole, ryzyko i decyzja research/implementacja.

### Bramka fazy

Przedstaw syntezę CONTEXT.md i pytanie Research czy Implement?
Przy dobrze znanej domenie pomiń fazę 2.

## Faza 2 — RESEARCH

Głębokie niewiadome architektoniczne i duże śledztwa przekazuj z pytaniami z
Examination do vc-research (trzyagentowy swarm), zamiast własnego ad-hoc researchu.
Skonsumuj raport. Proste lookupy (parametr API, składnia pliku) wykonuj przez
Brave Search/Context7/WebFetch: zapytanie `<API> usage example <year>`,
potem dokumentacja standardu.

### Wyjście: RESEARCH.md

Zapisz `plans/<ts>_<slug>_RESEARCH.md` według
[szablonu](references/output-templates.md): pytania z examination, ustalenia
ze źródłami, decyzja architektoniczna i wskazówki implementacji.

### Bramka fazy

Przedstaw syntezę RESEARCH.md i pytanie, czy przejść do implementacji.

## Faza 3 — IMPLEMENT

Kontekst CONTEXT.md + RESEARCH.md pozwala rozdzielić implementację.

- Operator uruchamia zewnętrznych workerów według vc-agents (Spawn Pattern).
- Worker dispatchowany jako `vibecrafted workflow <agent>`: external należy tylko
  do Operatora, ale nie oznacza to zakazu pracy równoległej. Rozdzielaj rozłączne
  subcuty przez native runtime'u: Claude Task przez vc-delegate, swarm Kimi,
  natywna ścieżka pozostałych runtime'ów. Rola workera ogranicza zakres,
  nie prawa do native fan-out. Dobieraj tiery według ekonomii podzadania
  (vc-delegate → Native Delegation Policy). Szeregowe wykonanie równoległego
  planu pogarsza szybkość dostawy; nie daje samo z siebie bezpieczeństwa.

### Szablon planu agenta

Każdy plan zawiera:

1. Obowiązkowy frontmatter: run_id, agent, skill (vc-workflow/vc-agents), itd.
2. Kontekst pipeline: odpowiednie części CONTEXT.md + RESEARCH.md.
3. Preambułę Loctree (zmierzona kompletność 98% vs 85%) z
   [phase-implement](references/phase-implement.md): repo-view przed pracą,
   slice przed edycją, find przed tworzeniem, impact przed usuwaniem,
   bez edycji niezmapowanego kodu.
4. Standardową preambułę żywego drzewa.
5. Bramkę jakości: test/lint właściwy dla repo.

### Spawn Pattern

Komendy spawnu są w vc-agents; preferuj przenośne skrypty.
Plany i raporty trafiają do plans/reports pod kanonicznym rootem artefaktów,
repo-local .vibecrafted/plans i .vibecrafted/reports są tylko symlinkami.
Supervisor natychmiast uzbraja `vibecrafted await <agent> --run-id <id>`.
Doktryna trzech sygnałów, znanego skew i naruszeń klasy 3 jest w
docs/runtime/AGENT_OPS.md oraz vc-agents → references/runbook.md.

## Faza 4 — CONVERGE (Marbles & Polarize)

Istniejąca implementacja nie dowodzi prawdziwości ani gotowości do wydania.
(1) Przeczytaj raporty, make check i mapę ryzyka.
(2) Czerwone gate'y lub kruchy runtime: kontynuuj, nie pokazuj diffu z
wiadomymi lukami jako zamkniętej pracy — vc-marbles do P0=0.
(3) Kod stabilny, lecz koncepcja rozmyta/konkurujące ścieżki: `vc-polarize --task <concept>`; pasma prism: 0..4 abort, 5..8 memo, 9..12 pass, 13..15 doctrine.
(4) Przekaż syntezę diffu/THESIS.md gotową do dou i Release.

### Rytm commitów

Jeden commit na rundę (marbles: runda = commit), zgodny z hookiem commit-msg.
Nie pozostawiaj dostarczonej pracy bez commitu; run daje do trzech commitów
(Implement, Marbles, Polarize). Potem obowiązuje niedestrukcyjny push feature
brancha zgodnie z autoryzacją misji. Force-push, trunk, merge i deploy pozostają
guzikami Foundera.

## Szybka mapa

| Faza      | Narzędzie                          | Wyjście                         |
| --------- | ---------------------------------- | ------------------------------- |
| Examine   | loctree MCP                        | `plans/<ts>_<slug>_CONTEXT.md`  |
| Research  | brave-search + Context7 + WebFetch | `plans/<ts>_<slug>_RESEARCH.md` |
| Implement | vc-agents (przenośne skrypty)      | reports/*.md                    |

## Pomijanie faz

- Mała poprawka, znana domena: Examine i bezpośrednia implementacja.
- Nowa biblioteka/API: wszystkie trzy fazy.
- Refactor: Examine + Implement, bez zewnętrznego researchu.
- Tylko research: Examine + Research, bez implementacji.

Na początku podaj, które fazy stosujesz. Pipeline jest obowiązkowy dla nietrywialnej
pracy funkcjonalnej w wielu plikach. Jeśli MCP Loctree jest niedostępne, fallback
jest w references/phase-examine.md. Brave Search pochodzi z narzędzi runtime'u
lub fallbacku web, nie z lokalnego katalogu wrapperów.

## Materiały

- references/phase-examine.md — pogłębione wzorce mapowania
- references/phase-research.md — metodologia i ranking źródeł
- references/phase-implement.md — delegacja ze skumulowanym kontekstem
- scripts/pipeline-init.sh — domyślne ścieżki artefaktów

---

## Weryfikacja przed handoffem

Przed „done” obejdź ciężarówkę według [Verification Rule](../VERIFICATION_RULE.md):
uruchom PRAWDZIWY artefakt (app/binarium, nie samo --version), zweryfikuj runtime,
nie traktuj upstream jako dowodu i sprawdź własne sprawdzenie. Zielone gate'y
nie dowodzą działania. Przenieś regułę do stopki prompta implementera.

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
