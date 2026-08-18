---
name: "vc-ship"
version: 1.0.0
description: >
  Meta-skill: the full Vibecrafted lifecycle umbrella. Launches the 11-stage
  Read-Write cadence (scaffold → implement → review → workflow → followup →
  marbles → audit → polarize → dou → hydrate → release) as ONE supervised
  lifecycle run, then turns the invoking agent into the supervising operator
  driving the baton relay with the human-controls verbs. Usually invoked in
  the vc-operator formula. Trigger phrases: "vc-ship", "/vc-ship",
  "ship it through the lifecycle", "parasol", "umbrella flight", "pełny lot",
  "lifecycle run", "od scaffoldu po release".
default: vc-ship
compatibility:
  tools:
    - Skill
    - Bash
    - Read
    - Write
    - TaskCreate
    - TaskUpdate
requires:
  - vc-init
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-ship` (launcher `ship`)**
>
> Ten sam _kształt_ trzech ścieżek floty, z **literałami tego** skilla — zobacz
> kanoniczną [Matrycę Delegacji](../DELEGATION_MATRIX.md):
>
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna)
> - [Native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy)
>
> | Ścieżka               | Literał tego skilla                                                                                                         |
> | --------------------- | --------------------------------------------------------------------------------------------------------------------------- |
> | 1. Worker użytkownika | `vibecrafted ship <agent>` / `vc-ship`                                                                                      |
> | 2. Interactive        | `/vc-ship` — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                  |
>
> **Uwaga:** **Parasol** cyklu życia (scaffold→release). Etapy zachowują własne launchery.

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-ship — parasol cyklu życia: jedna misja, jedenaście etapów, jedna pałeczka

---

## Wejście operatora

### Reguła Living Tree / Worktree

Ten workflow działa w bieżącym checkoucie i na bieżącej gałęzi operatora. Nie
twórz worktree, nie przełączaj się do niego i nie przenoś tam wykonania, chyba
że operator wprost o to poprosi w tym promptcie. Czytaj pliki ponownie przed
edycją, dostosowuj się do równoległych zmian i zgłoś awarię podłoża, jeśli
drzewo jest zbyt zatrute, żeby bezpiecznie kontynuować. Jedyny usankcjonowany
drugi tryb to dispatch Fleet Worktrees (pisany plan, zacommitowane wcześniej verifiery, rozłączne domeny plików, jednowątkowy integrator — patrz Reguła Living Tree, Tryb B); poza tą formacją zostań we wspólnym drzewie.

Zobacz [Reguła Living Tree](../LIVING_TREE_RULE.md).

## Checkpoint orientacji

Zanim ten parasol cyklu życia odpali pierwszy etap — i zanim każdy nadzorowany
później etap WRITE dotknie źródeł — MUSI uruchomić lub skonsumować procedurę
`vc-init` dla repo tej misji. Sam plik misji jest ważny wyłącznie wtedy, gdy
stoi na prawdziwym przebiegu `vc-init`; jeśli brakuje świeżego evidence z
`vc-init`, wykonaj najpierw przebieg initu i traktuj cały lot jako zablokowany,
dopóki prawda o repo nie istnieje. Prompt jest hipotezą, nigdy ground truth.

`Loctree:loctree` to domyślny skill percepcji strukturalnej dla tego przebiegu.
Użyj Loctree przed grepem i przed twierdzeniami opartymi na docsach, żeby
wyprodukować lub odświeżyć Code-Derived Application Map: repo-view, focus,
slice, impact, find i follow — na ile są istotne. Szukaj istniejących symboli i
kontraktów, zanim stworzysz nowe; uruchom impact przed usunięciem lub większym
refactorem; uruchom slice przed edycją. Jako nadzorca niesiesz tę mapę jako
ładunek pałeczki, więc worker każdego etapu dziedziczy tę samą prawdę
strukturalną, zamiast odkrywać repo od zera.

Chodzi o to, żeby znaleźć haki, zanim jedenaście etapów wystartuje: nośne huby,
twinsy, martwy kod, dryf, entrypointy runtime'u i pułapki zasięgu zmiany. Jeśli
misja jawnie nie dotyczy repo albo kodu, zapisz w raporcie wyjątek no-repo. W
przeciwnym razie brak evidence z `vc-init`/Loctree to błąd procesu, a lot nie
zaczął się uczciwie.

## Doktryna pracy z repozytorium

Przy pracy z repozytorium zaczynaj od Loctree jako mapy: używaj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim
przeszukiwaniem ręcznym. Do intencji i kontekstu sesji używaj AICX. rg/grep
traktuj jako fallback albo lokalną lupę, nie jako zamiennik mapowania
strukturalnego. Jeśli Loctree zawiedzie albo przeoczy jakąś powierzchnię,
dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

Standardowy launcher:

```bash
vibecrafted ship <agent> --file /path/to/mission.md     # canonical: mission file
vibecrafted ship codex --prompt 'one-cut mission text'  # short missions
vc-ship claude --file mission.md                        # shell shortcut
vibecrafted ship <agent> --start-stage review --file m.md  # resume mid-pipeline
```

Niezmiennik runtime'u: workery etapów lecą **headless**, więc cykl życia
przeżywa utratę vc-frame i User Session. Obserwuj przez stan cyklu życia,
transcripty, `observe` i `await`. `--runtime terminal` to jawny wyjątek
kompatybilnościowy dla ścieżki providera, o której dowiedziono, że wymaga TTY.

---

## Cel

Przeprowadź jedną misję produktową przez pełną kadencję Read-Write jako
pojedynczy nadzorowany run cyklu życia i uczyń wywołującego agenta
**nadzorcą** tego runu: weryfikuje raport każdego etapu, steruje czasownikami
human-controls i niesie pałeczkę (razem z ładunkiem raportów) od scaffoldu aż
po release. Cel każdego lotu jest podyktowany: **duża wygrana i ZEROWY indeks
DoU** — albo uczciwe, otrasowane `accept-dou` dla tego, co zostało
niedokończone.

## Pipeline (kadencja Read-Write)

| #   | Etap      | Faza  | Narzędzia discovery / dowozu                                       |
| --- | --------- | ----- | ------------------------------------------------------------------ |
| 1   | scaffold  | READ  | vc-init, vc-loctree, vc-research                                   |
| 2   | implement | WRITE | vc-init, vc-operator, vc-agents                                    |
| 3   | review    | READ  | vc-init, vc-loctree, vc-review, vc-prview (z założenia test-heavy) |
| 4   | workflow  | WRITE | vc-init, vc-research, vc-justdo                                    |
| 5   | followup  | READ  | vc-init, vc-intents, vc-loctree, TDD                               |
| 6   | marbles   | WRITE | runtime vc-marbles — entropia W GÓRĘ, zalej każde pęknięcie        |
| 7   | audit     | READ  | vc-init, vc-loctree, vc-aicx, vc-research                          |
| 8   | polarize  | WRITE | runtime marbles — entropia W DÓŁ, jedna prawda, bez litości        |
| 9   | dou       | READ  | Definition of Undone: znajdź luki przed release'em                 |
| 10  | hydrate   | WRITE | vc-init, vc-operator, vc-decorate                                  |
| 11  | release   | —     | deployment/publikacja/podpisywanie — etap płaszczyzny operatora    |

Etapy READ nie mogą pisać po źródłach (naruszenie jest trasowane jako
`read_phase_violation`); etapy WRITE muszą pokazać commity i zielone bramki.

## Jak lecieć (protokół nadzorcy)

1. **Najpierw misja.** Ułóż misję jako trwały plik `.md` pod
   `~/.vibecrafted/artifacts/<org>/<repo>/<date>/plans/` — osadzoną w
   prawdziwym przebiegu vc-init (atlas Loctree + intencje AICX + prawda o
   git/ryzyku), z jawnymi deliverables, twardymi ograniczeniami i bramkami.
   `--file` dostarcza ją verbatim każdemu workerowi etapu.
2. **Odpal** standardowym launcherem powyżej. Zweryfikuj potwierdzenie startu:
   run_id (`life-ship-…`), wczytany atlas kontekstu, etap 1 przyjęty.
3. **Uzbrój await natychmiast, po stronie nadzorcy** (nigdy wewnątrz subagenta —
   patrz `docs/runtime/AGENT_OPS.md`): po dispatchu uzbrój
   `vibecrafted <agent> await --run-id <id>` natychmiast, po stronie nadzorcy.
   JSON control plane'u, pliki raportów, transcripty, pane'y i zaplanowane
   pobudki są wyłącznie diagnostyczne, nie są sygnałem wybudzenia.
   Asekurowanie awaita doraźnymi pollerami/watcherami to naruszenie klasy 3;
   napraw `control_plane.await_run`, nie normalizuj asekuracji. Sprawdzanie
   raportów etapów i `ship status --json` to diagnostyka podporządkowana temu
   kanonicznemu awaitowi; `ship status --json` wystawia `stage_worker` z
   `worker_dead_without_report` — sygnał śmierci, na który da się zareagować;
   dispatcher zapisuje też `worker_exit` / `stage_worker_exit` do `state.json`
   po stronie push, gdy worker umiera.
   Żywotność na 3 sygnałach: zanim ogłosisz zakończenie, uzgodnij werdykt
   awaita, meta runu z terminala i martwy pid workera; jeśli obiecano raport,
   potwierdź, że istnieje. Dwa zgodne sygnały wystarczą do działania, trzy do
   ogłoszenia końca; niezgodność oznacza „traktuj jak żywy" i ponowne uzbrojenie
   awaita. Znany rozjazd: rc=0 przy żywym oraz meta zawieszona na
   `active`/`stalled` po faktycznym zakończeniu.
4. **Weryfikuj przed każdym guzikiem.** Przeczytaj raport; dla etapów WRITE
   potwierdź, że commity i bramki naprawdę istnieją; dla etapów READ potwierdź
   brak `read_phase_violation`. Uszanuj sterowanie workera z frontmattera
   raportu (`next_stage`, `next_agent`, `dou_index`), chyba że to bzdura —
   wtedy nadpisz czasownikiem.
5. **Steruj czasownikami, nigdy ręczną operacją na stanie:**

   ```bash
   vibecrafted ship runs                       # list lifecycle runs
   vibecrafted ship status <run_id> --json     # truth before any button
   vibecrafted ship approve <run_id>           # baton → next stage (cargo-gated)
   vibecrafted ship approve <run_id> --force   # traced override of the cargo gate
   vibecrafted ship interrupt <run_id>         # stop a blind/dead continuation
   vibecrafted ship fallback <run_id> --stage <s>  # rewind the baton WITH cargo
   vibecrafted ship force-audit <run_id>       # suspicious WRITE output
   vibecrafted ship accept-dou <run_id> --finding "…"  # conscious, traced gap
   ```

   Odzyskiwanie martwego workera zawsze wygląda tak: `interrupt` →
   `fallback --stage <stage>` → `approve [--force]`. Żaden ładunek pałeczki nie
   ginie.

6. **Raportuj na końcu, nie po drodze** (chyba że operator prosi inaczej):
   przelecione etapy, wprowadzone korekty, commity, kolory bramek, dou_index
   oraz to, czego release uczciwie NIE zweryfikował.

## Granice (co zostaje przy człowieku)

- Pałeczka to sztafeta **agent↔agent**; nadzorujący agent jest operatorem runu.
  Człowiek zostaje człowiekiem: mandaty, pushe w świat i merge należą do niego,
  chyba że jawnie je zdeleguje.
- Nigdy nie merguj własnego PR-a bez jawnego, jednorazowego mandatu.
- „Production ready" to werdykt zakazany. Raportuj evidence, `file:line`, kolory
  bramek; werdykt należy do etapu release i do człowieka — uczciwe wyniki w
  rodzaju `repo_contract_green_external_release_blocked` biją pewne siebie
  kłamstwo.

## Kiedy używać

- Misja potrzebuje pełnej kadencji: discovery, dowóz, adwersarialny review,
  stabilizacja entropia-w-górę / entropia-w-dół, DoU i bramka release'u — jako
  jeden nadzorowany, audytowalny run.
- Operator mówi „ship it", „pełny lot", „parasol" dla obciętego zakresowo cięcia
  produktowego w DOWOLNYM repo (runtime jest repo-agnostyczny; plik misji
  nazywa roota).

**Kiedy NIE używać:**

- Pojedyncze cięcie o znanym kształcie → `vc-implement` albo `vc-justdo`.
- Sama stabilizacja → `vc-marbles` (potem `vc-polarize`).
- Samo discovery → bezpośrednio `vc-init` / `vc-research` / `vc-scaffold`.

## Pozycja w pipelinie

- Upstream: `vc-operator` (typowy wywołujący), plan misji klasy scaffold.
- Downstream: nic — vc-ship JEST pipeline'em; jego etap release emituje handoff
  (raport release'u + ślad DoU), na którym działa człowiek.

## Kryteria akceptacji

Przebieg skilla jest **skończony**, gdy:

- [ ] Run cyklu życia doszedł do `release` (albo do stopu decyzją operatora), a
      każde przejście jest otrasowane w `operator_actions`.
- [ ] Każdy etap WRITE ma weryfikowalne commity + zielone bramki; każdy etap
      READ jest wolny od naruszeń.
- [ ] `dou_index` wynosi 0 — albo każda pozostała luka jest jawnym,
      otrasowanym `accept-dou` z nazwanym followupem.
- [ ] Dostarczony raport końcowy: etapy, korekty, commity, kolory bramek i to,
      czego NIE zweryfikowano.

Jeśli któregokolwiek punktu akceptacji nie da się odhaczyć z evidence, lot się
nie zakończył — powiedz to wprost w raporcie końcowym.

## Antywzorce

- Odpalanie bez pliku misji osadzonego w prawdzie z vc-init (prompt jest
  hipotezą, nie ground truth).
- Pilnowanie bramek albo workerów z wnętrza subagenta — awaria klasy gate-nap
  (`docs/runtime/AGENT_OPS.md`, klasa 1); watchery mieszkają przy nadzorcy.
- Ufanie ciszy: brakujący raport jest nie do odróżnienia od martwego workera,
  dopóki nie sprawdzisz żywotności (klasa 2) — użyj `status`/`stage_worker`, nie
  wysiaduj budżetów przy zwłokach.
- Ręczne edycje `state.json` zamiast czasowników — ślad JEST produktem.
- Zatwierdzanie etapu bez przeczytania jego raportu albo samodzielne ogłaszanie
  „gotowe" zamiast oddania werdyktu bramce.

## Przykłady

Zobacz [`examples/example-prompt.md`](examples/example-prompt.md) — minimalna
para: fraza wyzwalająca + oczekiwane zachowanie.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
