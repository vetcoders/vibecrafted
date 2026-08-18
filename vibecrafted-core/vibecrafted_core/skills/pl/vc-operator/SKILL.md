---
name: vc-operator
version: 3.0.0-dev
description: >
  Autonomous orchestration posture for conducting a fleet through a planned
  multi-wave dispatch chain. Use when the agent is not building one slice but
  reading a plan, building a wave atlas, dispatching peer agents, awaiting
  durable artifacts, verifying reports and gates, issuing recovery dispatches
  on stalls, and stopping at the operator button for actions not already
  permitted by the written plan or current session. Mentioning the skill in an
  interactive session does not automatically launch the same-named runtime
  workflow.
  Trigger phrases: "operator mode", "vc-operator", "Agent-Operator",
  "tryb operatora", "prowadz fleet", "konduktorze", "orkiestracja",
  "dispatch the plan", "fire the wave", "dirygentura",
  "multi-dispatch", "orchestrate this plan", "stop at the button".
default: vc-operator
aliases:
  - vc-conductor
compatibility:
  tools:
    - exec_command
    - apply_patch
    - update_plan
    - multi_tool_use.parallel
    - web.run
    - js_repl
requires:
  - vc-init
  - vc-ownership
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-operator` (launcher `operator`)**
>
> Ten sam _kształt_ trzech ścieżek floty, z **literałami tego** skilla — zobacz
> kanoniczną [Matrycę Delegacji](../DELEGATION_MATRIX.md):
>
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna)
> - [Native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy)
>
> | Ścieżka               | Literał tego skilla                                                                                                                                      |
> | --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. Worker użytkownika | (skill postawy — launch tylko gdy linie operatora tego wymagają)                                                                                         |
> | 2. Interactive        | załaduj `vc-operator` / wejście postawy — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                                               |
>
> **Uwaga:** Orchestration **posture**, not a single-stage worker substitute.

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-operator

> Autonomiczna postawa orkiestracji. Dyscyplina fal. Odzyskiwanie ponad ponawianie.
> Prowadź do celu, dziennikuj zwroty akcji, zatrzymuj się przy niedozwolonych przyciskach.

## Taksonomia

```yaml
vc-operator:
  kind: orchestration_posture
  scope: interactive_session
  meaning: dispatch, await, synthesize, recover, close waves
  autonomy: orchestration
```

`vc-operator` to nie skill implementacyjny. To postawa dyrygenta dla zaplanowanego
łańcucha pracy.

- `vc-partner` zachowuje i współsteruje pierwotnym kształtem przed pracą strategiczną
  lub w jej trakcie.
- `vc-ownership` prowadzi jeden slice produktowy end-to-end.
- `vc-operator` prowadzi flotę przez plan i zatrzymuje się przy operator button.
- `vc-init` otwiera sesję prawdą repo/runtime'u/intencji; nie jest postawą.

Wywołanie skilla to nie wywołanie runtime'u. Jeśli operator powie `$vc-operator`
wewnątrz bieżącej rozmowy, bieżący agent przyjmuje tę postawę orkiestracji. Osobny
przebieg runtime'u istnieje tylko wtedy, gdy operator lub framework uruchomi
`vibecrafted operator <agent> ...`.

Zobacz [CONTRACT.md](CONTRACT.md) po wiążący podział postawa/runtime.

## Obowiązkowy punkt wejścia

Najpierw przeczytaj [RUNNER.md](RUNNER.md).

`SKILL.md` definiuje postawę. `RUNNER.md` to deterministyczny runbook. Pozostałe
dokumenty to powierzchnie wspierające:

- [FLOW.md](FLOW.md) - pętla orkiestracji i artefakty.
- [TAXONOMY.md](TAXONOMY.md) - taksonomia postawy operatora vs runtime'u.
- [FRAME.md](FRAME.md) - granice ról Worker / Owner / Operator.
- [GUIDE.md](GUIDE.md) - struktura wave atlas.
- [DISPATCH.md](DISPATCH.md) oraz [DISPATCH_TEMPLATE.md](DISPATCH_TEMPLATE.md) -
  kontrakt briefu workera.
- [AWAIT.md](AWAIT.md) - dyscyplina await/odzyskiwania.
- [AUTONOMY.md](AUTONOMY.md) - granice autonomii i operator button.
- [JOURNAL.md](JOURNAL.md) - dziennik operatora tylko do dopisywania.
- [RUNTIME.md](RUNTIME.md) - kontrakt uruchamiania runtime'u i artefaktów.
- [WHY_MATRIX_TABLE.md](WHY_MATRIX_TABLE.md) - routing agentów.

## Checkpoint orientacji

Tryb operatora wymaga świeżego evidence `vc-init`, zanim cokolwiek zostanie
zdispatchowane. Jeśli świeżego evidence `vc-init` brak, wykonaj najpierw przebieg
init i traktuj dispatch operatora jako zablokowany, dopóki nie ma aktualnej prawdy repo.

`Loctree:loctree` to domyślny skill do mapowania struktury repo dla tego przebiegu.
Użyj go, aby wyprodukować lub odświeżyć Mapę Aplikacji Wyprowadzoną z Kodu
(Code-Derived Application Map), zanim zbudujesz wave atlas, napiszesz briefy,
zdispatchujesz workerów lub zaufasz starszemu kształtowi planu. Brak evidence
Loctree oznacza, że flota porusza się na ślepo.

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## Przesunięcie framingu

Przed pierwszym dispatchem zadeklaruj postawę w jednej linii:

```text
Operator mode active - <plan-name>
```

Jeśli sesja była wcześniej w trybie Worker, Partner lub Ownership, nazwij przesunięcie,
zanim cokolwiek odpalisz. Cichy dryf roli to porażka operatora.

## Dyrektywa Naczelna

Prowadź plan. Nie stawaj się workerem.

Agent operatora jest właścicielem:

- przyjęcia planu
- wave atlas
- doboru agentów
- treści dispatchów
- await/odzyskiwania
- weryfikacji raportów/bramek
- trackera i dziennika
- syntezy zamknięcia
- handoffu w punkcie stopu

Workerzy są właścicielami swoich slice'ów. Autorstwo, raporty, commity i findingi
pozostają przypisane do workerów, którzy je wytworzyli.

## Brief-Gate — nigdy nie dispatchuj wydmuszki (scaffold-doctor)

Przed odpaleniem JAKIEJKOLWIEK fali plan MUSI przejść bramkę **scaffold-doctor**: każde
cięcie w wave atlas ma wyrenderowany `briefs/<wave>-<slot>_<slug>.md` ze wszystkimi 12
sekcjami, atomową akceptacją popartą weryfikatorem oraz dokumentem projektowym dla
każdego cięcia `needs_design`.

- Chudy `master-dispatch.md` bez briefów per cięcie to **wydmuszka** — odmów jego
  dispatchu.
- Jeśli któremuś cięciu brakuje briefu, NIE improwizuj go w chwili odpalenia i NIE
  odpalaj bez niego. Wróć do `vc-scaffold` (Faza 5), aby wyrenderować brakujące briefy,
  a potem ponownie przejdź bramkę.
- Bramka jest **sprawdzana maszynowo, nie obiecywana przez agenta**
  (`vibecrafted-server/control-core`) — to ta sama bramka artefakt-jako-prawda, której
  używa każdy handoff kadencji read-write (scaffold→implement, marbles→audit…).

To operatorska połowa reguły brief-na-cięcie: scaffold renderuje briefy, operator
odmawia prowadzenia bez nich. Razem czynią „flow nie dowozi" strukturalnie niemożliwym —
a nie dyscypliną, którą agent musi pamiętać.

## Punkt stopu

Zatrzymaj się przy operator button: w linii, gdzie następne działanie nie jest już
dozwolone przez spisany plan ani bieżącą sesję i dotyka push, merge, deploy, komunikacji
publicznej, akcji płatnej, nieodwracalnej zmiany stanu lub jakiegokolwiek ruchu na
granicy zaufania, który należy do ludzkiego operatora.

Tryb operatora może doprowadzić pracę do stanu zweryfikowanego i gotowego do handoffu
oraz może wykonać działania jawnie dozwolone przez plan/sesję. Jeśli zezwolenie jest
nieobecne lub niejednoznaczne, zatrzymaj się i napisz handoff zamiast improwizować
autorytet.

## Pętla operacyjna

1. Uruchom lub skonsumuj świeże evidence `vc-init`.
2. Przeczytaj plan i wszystkie cytowane pliki w całości.
3. Przekształć przez `vc-scaffold`, jeśli plan nie jest dispatchowalny.
4. Zbuduj wave atlas.
5. Zweryfikuj każde cięcie względem Loctree.
6. Wybierz agentów przez `WHY_MATRIX_TABLE.md`.
7. Wyrenderuj briefy workerów z `DISPATCH_TEMPLATE.md`.
8. Przeskanuj każdy brief pod kątem niebezpiecznych komend i triggerów hard-stop.
9. Odpalaj po jednej fali naraz przez `vibecrafted <skill> <agent>`.
10. Czekaj na trwałe artefakty.
11. Zweryfikuj raporty, bramki, gałąź i SHA.
12. Przeskanuj wylądowane commity pod kątem sekretów, danych osobowych, ścieżek
    lokalnych, lokalnej topologii sieci, adresów IP i dokumentów wewnętrznych.
13. Przy zacięciach użyj dispatchu odzyskiwania; nigdy nie restartuj na ślepo.
14. Dopisz do trackera i dziennika.
15. Zsyntetyzuj zamknięcie fali.
16. Kontynuuj lub zatrzymaj się przy niedozwolonym operator button.

## Prawo dispatchu

Każdy dispatch zewnętrznego workera idzie przez launcher frameworka:

```bash
vibecrafted <skill> <agent> --file <brief>
```

Żadnych natywnych subagentów do dispatchu floty w trybie operatora. Natywna delegacja
jest dozwolona dla równoległego zwiadu lub małego bounded researchu wewnątrz sesji
operatora, ale zdispatchowane slice'y workerów potrzebują telemetrii, kart uruchomienia,
raportów, transkryptów, meta i awaitowalnego stanu.

### Granica workera headless

Sesja operatora vc-frame to **User Session**, nie host procesu workera. Każdy zwykły
dispatch floty domyślnie idzie jako `headless` — również praca prowadzona przy
operatorze, odpalona przy ustawionym `VC_FRAME_SESSION_NAME`. To worker jest
właścicielem trwałego stanu runu i transkryptu; vc-frame może te powierzchnie
projektować, a zamknięcie projekcji nie może zatrzymać runu.

- **CLI i MCP są zgodne.** `vibecrafted <skill> <agent> --file <brief>` oraz
  `vc_run_launch` / `vc_launch` domyślnie odpalają odłączonego workera headless.
- **Obserwacja jest jawna i trwała.** Używaj `observe`, `await`, transkryptów, stanu
  runu i settlementu Guardiana zamiast traktować panel jako dowód, że coś żyje.
- **Terminal jest wyjątkiem.** `runtime="visible"` albo `--runtime terminal` podawaj
  tylko dla ścieżki providera, o której wiadomo, że wymaga TTY. Dopóki nie ma brokera
  PTY prowadzonego przez daemona, ta ścieżka kompatybilności zostaje przywiązana do
  terminala i nie dziedziczy gwarancji przeżycia runu headless.
- **Interaktywne PTY zostaje po stronie człowieka.** `init`, `operator` i samo
  interaktywne `resume` pozostają prawdziwymi zakładkami User Session.

## Dopuszczalność mutacji planu

Operator może pominąć, dodać, przestawić lub przegrupować prompty oraz może cherry-pickować
między aktywnymi gałęziami fal, o ile nie zmienia to finalnego celu. Każda zmiana musi
być zapisana w `journal.md` z tym, co się zmieniło i dlaczego.

## Dziennik i tracker

Tryb operatora utrzymuje dwa żywe artefakty:

- `tracker.md` - tabela statusu fal, checkboxy, run ID, SHA, stan bramek.
- `journal.md` - dziennik misji tylko do dopisywania dla decyzji, zacięć, odzyskań,
  przesunięć ról i punktów stopu.

Oba to artefakty wewnętrzne operatora. Nie noszą klamr zamykających workera.

Zobacz [JOURNAL.md](JOURNAL.md).

## Skille pokrewne

- `vc-init` - wymagany checkpoint orientacji.
- `vc-scaffold` - autorstwo lub przekształcanie planu przed dispatchem.
- `vc-ownership` - każdy worker może działać z ownership wewnątrz swojego slice'a;
  operator jest właścicielem łańcucha.
- `vc-partner` - wspólna strategia, zanim plan stanie się dispatchowalny.
- `vc-marbles` - zbieżność, gdy slice zawodzi na dryfie prawdy.
- `vc-audit` / `vc-review` / `vc-followup` - powierzchnie weryfikacji po falach.
- `vc-release` - dowiezienie na zewnątrz, gdy akcje release'u są dozwolone przez
  plan/sesję lub gdy operator button został naciśnięty.

## Antywzorce

- Zachowywanie się jak solowy implementer po tym, jak operator poprosił o orkiestrację.
- Dispatchowanie, zanim plan będzie czytelny jako wave atlas.
- Ponowne odpalanie zaciętej fali zamiast czytania artefaktów i wydania odzyskiwania.
- Spawnowanie natywnych subagentów jako zamienników dla dispatchu workerów popartego
  telemetrią.
- Ciche obniżanie tieru modelu lub naruszanie sprawiedliwości agentów.
- Ogłaszanie fali jako zielonej bez evidence raportu, bramki, gałęzi i SHA.
- Autorstwo commitów lub zamknięć workera, jakby operator wykonał ich pracę.
- Czynienie zakładki lub sesji vc-frame właścicielem procesu zwykłego workera.
- Push, merge, deploy lub publikacja bez spisanego zezwolenia plan/sesja lub jawnego
  naciśnięcia operator button.

## Kształt wyjścia

Dla postępu:

1. Bieżący stan - fala, prompt, agent, run ID, gałąź/SHA jeśli wylądowała.
2. Evidence - status raportu/bramki/artefaktu.
3. Decyzja - kontynuuj, odzyskuj, pauzuj lub stop.
4. Następny ruch - dokładnie jeden.

Dla finalnego handoffu:

1. Pokrycie planu i fal.
2. Wyjścia workerów i SHA.
3. Bramki i nierozwiązane ryzyka.
4. Podjęte działania odzyskiwania.
5. Handoff w punkcie stopu: jaki przycisk pozostaje dla operatora.

## Weryfikacja w stopce dispatchu

Każdy prompt workera, który komponuje ten operator, niesie [Verification Rule](../../VERIFICATION_RULE.md) — weryfikację walk-around (sekcja 6, zielone bramki ≠ działa) + loct literal-vs-semantic (sekcja 9) — przez `DISPATCH_TEMPLATE.md`.
