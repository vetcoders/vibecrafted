---
title: Matryca Delegacji
kind: doctrine_matrix
version: 3.2.0
description: "Kanoniczny model wywoływania, wykonywania i delegacji dla launcherów runtime Vibecrafted i odpowiadających skilli."
scope: framework
status: active
language: pl
---

# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Matryca Delegacji (Delegation Matrix)

> Wywoływanie, wykonanie i delegacja floty — **per launcher**, nie jako jeden rozmyty szablon.

<!-- fleet-imperative: v3 -->

## Wspólne trzy ścieżki

Każdy **core runtime launcher** (`vibecrafted <launcher> <agent>`, skill oparty o core runtime) wywołuje się w tym samym _kształcie_ trzech ścieżek. **Literały** zmieniają się per launcher; reguły władzy — nie.

### 1. User-Launched Worker

Użytkownik uruchamia **nazwany** launcher z CLI. Tworzy to osobny, nieinteraktywny przebieg workera odpowiedzialny za pełny pipeline **tego** skilla.

```text
vibecrafted <launcher> <agent> [-p|--prompt … | -f|--file …]
```

Przykład (tylko workflow — nie uogólniaj słowa `workflow` na każdy skill):

```bash
vibecrafted workflow claude --prompt 'Examine auth surface and implement fixes'
```

### 2. Interactive Skill Invocation

Użytkownik wywołuje `/vc-<launcher>` albo ładuje ten skill w istniejącej sesji. Bieżący agent **musi** załadować i wykonać pełny skill **w tej samej sesji**. **Nie wolno** zewnętrzniać przebiegu do osobnego workera `vibecrafted` tylko dlatego, że external dispatch istnieje. Może — a gdy wymagane **musi** — użyć **natywnej floty subagentów w procesie**.

Przykład:

```text
/vc-workflow
```

### 3. Agent-Operator Delegation

Przy szerszej orkiestracji agent-operator może odpalić **ten sam nazwany launcher** co użytkownik — zwykle przez `vc-dispatch` / linie operatora — tak by osobna sesja pod runtime Vibecrafted wykonała pipeline tego skilla.

```bash
vibecrafted <launcher> <agent> --file <brief.md>
```

---

## Czym ta matryca nie jest

- **Nie** twierdzi, że każdy skill to `vibecrafted workflow <agent>`.
- **Nie** jest masowym wklejeniem jednego bloku do każdego `SKILL.md` bez nazwania launchera.
- **Nie** jest flipem floty na native-only. Zewnętrzni workerzy zostają first-party.
- **Nie** kasuje tożsamości: `vc-dispatch` zostaje dyspozyturą; `vc-ship` zostaje scaffold→release; każdy skill ma swój mandat.

Rewolucja operatora przy `vc-workflow` to **precyzja literałów + swobodniejszy native pod tym skillem**. Reszta runtime dostaje **tę samą precyzję**, każdy pod własną nazwą.

---

## Mandat wykonawczy i cykle życia

Niezależnie od interactive vs worker, agent pod launcherem ma **ten sam mandat**: kompleksowo wykonać pipeline skilla i użyć natywnych subagentów, gdy trzeba.

Różnica to tylko:

- **gdzie** skill się wykonuje
- **czyją uwagę** zajmuje

Niemniej:

- **Headless worker** zachowuje prawo do własnych natywnych subagentów — bycie workerem ogranicza zakres i cykl życia przebiegu, nie prawo do natywnej delegacji.
- **Agent z interaktywnym skillem** wykonuje go lokalnie w sesji, z native gdy wypada.
- **Swobodniejszy native** na niektórych biegach = gdy pracujesz interaktywnie (albo worker potrzebuje głębi) dokończ skill native zamiast odruchowo re-dispatchować external. To **nie** znaczy porzucić external launchery.

---

## Natywne subagenty vs zewnętrzni workerzy

| Rodzaj                  | Cykl życia                   | Kontekst                                                      |
| ----------------------- | ---------------------------- | ------------------------------------------------------------- |
| **Natywne subagenty**   | Ten sam proces               | Wspólna pamięć, config, rozmowa                               |
| **Zewnętrzni workerzy** | Osobne procesy `vibecrafted` | Control-plane / report / transcript / meta; własny cykl życia |

**Reguła:** władza wykonania skilla zostaje u agenta, który trzyma skill, chyba że jawnie oddelegowano zdefiniowanymi kanałami (`vc-dispatch`, linie ship operatora, worker z CLI użytkownika).

---

## Katalog launcherów (core runtime)

Oparty o `vibecrafted_core.cli.LAUNCHERS` + shell wrappers + meta lifecycle. Katalog skilla to `vc-<launcher>`, o ile nie zaznaczono inaczej.

### Launchery cyklu ship (kolejność kanoniczna)

| Launcher    | Skill                                      | Worker CLI                      | Interactive     | Notatki                                                         |
| ----------- | ------------------------------------------ | ------------------------------- | --------------- | --------------------------------------------------------------- |
| `scaffold`  | [`vc-scaffold`](../vc-scaffold/SKILL.md)   | `vibecrafted scaffold <agent>`  | `/vc-scaffold`  | Plan / briefy                                                   |
| `implement` | [`vc-implement`](../vc-implement/SKILL.md) | `vibecrafted implement <agent>` | `/vc-implement` | **Faza WRITE ship** — ustrukturyzowane e2e z followup + marbles |
| `review`    | [`vc-review`](../vc-review/SKILL.md)       | `vibecrafted review <agent>`    | `/vc-review`    | READ                                                            |
| `workflow`  | [`vc-workflow`](../vc-workflow/SKILL.md)   | `vibecrafted workflow <agent>`  | `/vc-workflow`  | ERi                                                             |
| `followup`  | [`vc-followup`](../vc-followup/SKILL.md)   | `vibecrafted followup <agent>`  | `/vc-followup`  | Trajektoria                                                     |
| `marbles`   | [`vc-marbles`](../vc-marbles/SKILL.md)     | `vibecrafted marbles <agent>`   | `/vc-marbles`   | WRITE; `--count`/`--depth`                                      |
| `audit`     | [`vc-audit`](../vc-audit/SKILL.md)         | `vibecrafted audit <agent>`     | `/vc-audit`     | Falsyfikacja planu                                              |
| `polarize`  | [`vc-polarize`](../vc-polarize/SKILL.md)   | `vibecrafted polarize <agent>`  | `/vc-polarize`  | Jedna oś                                                        |
| `dou`       | [`vc-dou`](../vc-dou/SKILL.md)             | `vibecrafted dou <agent>`       | `/vc-dou`       | Definition of Undone                                            |
| `decorate`  | [`vc-decorate`](../vc-decorate/SKILL.md)   | `vibecrafted decorate <agent>`  | `/vc-decorate`  | Wykończenie UX                                                  |
| `hydrate`   | [`vc-hydrate`](../vc-hydrate/SKILL.md)     | `vibecrafted hydrate <agent>`   | `/vc-hydrate`   | Packaging / GTM                                                 |
| `release`   | [`vc-release`](../vc-release/SKILL.md)     | `vibecrafted release <agent>`   | `/vc-release`   | Outward ship                                                    |

### Dodatkowe launchery skilli

| Launcher    | Skill                                      | Worker CLI                      | Interactive     | Notatki                                                                                        |
| ----------- | ------------------------------------------ | ------------------------------- | --------------- | ---------------------------------------------------------------------------------------------- |
| `justdo`    | [`vc-justdo`](../vc-justdo/SKILL.md)       | `vibecrafted justdo <agent>`    | `/vc-justdo`    | **Samodzielna postawa** — nie faza ship; nie `implement`. Typ zadania z promptu. ADR-0001      |
| `canary`    | [`vc-canary`](../vc-canary/SKILL.md)       | `vibecrafted canary <agent>`    | `/vc-canary`    | Katalog ownership: wyczucie atlasu → 1 agent/scope → jeden commit → raport z ustaleniami       |
| `research`  | [`vc-research`](../vc-research/SKILL.md)   | `vibecrafted research …`        | `/vc-research`  | Swarm                                                                                          |
| `ownership` | [`vc-ownership`](../vc-ownership/SKILL.md) | `vibecrafted ownership <agent>` | `/vc-ownership` | Ownership delivery                                                                             |
| `partner`   | [`vc-partner`](../vc-partner/SKILL.md)     | `vibecrafted partner <agent>`   | `/vc-partner`   | Wspólne sterowanie                                                                             |
| `prune`     | [`vc-prune`](../vc-prune/SKILL.md)         | `vibecrafted prune <agent>`     | `/vc-prune`     | Runtime cone                                                                                   |
| `intents`   | [`vc-intents`](../vc-intents/SKILL.md)     | `vibecrafted intents <agent>`   | `/vc-intents`   | Plan→runtime                                                                                   |
| `delegate`  | [`vc-delegate`](../vc-delegate/SKILL.md)   | `vibecrafted delegate <agent>`  | `/vc-delegate`  | Doktryna **native**                                                                            |
| `trust`     | [`vc-trust`](../vc-trust/SKILL.md)         | `vibecrafted trust <agent>`     | `/vc-trust`     | READ; post-hoc falsyfikacja claimów commitów (agent fairness + kompletność) + settlement f/x/n |
| `guard`     | [`vc-guard`](../vc-guard/SKILL.md)         | `vibecrafted guard <agent>`     | `/vc-guard`     | READ; inwentarz gates + odmowa kontynuacji przy trust `block` (nigdy nie zmyśla settlementu)   |
| `paste`     | (helper)                                   | `vibecrafted paste …`           | —               | Nie pełny ERi                                                                                  |

### Meta i orientacja (inny kształt niż workery skillowe)

| Powierzchnia | Skill / powierzchnia                     | Wywołanie                                         | Rola                                                                             |
| ------------ | ---------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------- |
| **init**     | [`vc-init`](../vc-init/SKILL.md)         | `vibecrafted init [agent]`, `vc-init`, `/vc-init` | Orientacja sesji — nie worker pipeline'u WRITE                                   |
| **ship**     | [`vc-ship`](../vc-ship/SKILL.md)         | `vibecrafted ship <agent>`, `vc-ship`, `/vc-ship` | **Parasol lifecycle** (scaffold→release), nie single-stage                       |
| **dispatch** | [`vc-dispatch`](../vc-dispatch/SKILL.md) | `vibecrafted dispatch …`, `vc-dispatch`           | **Dyspozytura floty external** — prowadzi plany/linie; nie staje się „implement” |
| **operator** | [`vc-operator`](../vc-operator/SKILL.md) | interactive / postawa                             | Postawa orkiestracji multi-wave                                                  |
| **agents**   | [`vc-agents`](../vc-agents/SKILL.md)     | doktryna + tryby floty                            | Kontrakt floty external; nie zastępuje interaktywnego skilla                     |

### Fundament (bez własnego workera `vibecrafted <name> <agent>`)

Te skille ładują się **wewnątrz** innych skilli albo sesji interaktywnych. Nie wymyślamy im fałszywego workera `vibecrafted loctree claude` dla symetrii.

| Skill                                                | Rola                         |
| ---------------------------------------------------- | ---------------------------- |
| [`vc-loctree`](../vc-loctree/SKILL.md)               | Percepcja strukturalna       |
| [`vc-aicx`](../vc-aicx/SKILL.md)                     | Retrieval intencji / sesji   |
| [`vc-prview`](../vc-prview/SKILL.md)                 | Generowanie artefaktów PR    |
| [`vc-screenscribe`](../vc-screenscribe/SKILL.md)     | Screencast → ustalenia       |
| [`vc-skillaunch`](../vc-skillaunch/SKILL.md)         | Zapakowanie workflow w skill |
| [`vibecraftsmanship`](../vibecraftsmanship/SKILL.md) | Doktryna rzemiosła           |

---

## Reguła per-launcher (delta semantyczna)

Dla launchera `L` i skilla `vc-L`:

1. **Worker:** tylko `vibecrafted L <agent>` (lub udokumentowany alias). Nigdy `vibecrafted <workflow> <agent>` jako placeholder na wszystkie skille.
2. **Interactive:** tylko `/vc-L` (albo załadowanie `vc-L/SKILL.md`). W sesji; swobodniejszy native gdy bieg tego wymaga.
3. **Operator dispatch:** może odpalić `vibecrafted L <agent>` na linii; tożsamość skilla `L` w briefie workera zostaje.
4. **Nie** zewnętrzniaj interaktywnego `/vc-L` tylko dlatego, że launcher istnieje.
5. **Nie** zamieniaj każdego skilla w workflow-ERi; ERi ma tylko `workflow`.

### Przykład: workflow (kanon operatora)

| Ścieżka       | Literał                                                 |
| ------------- | ------------------------------------------------------- |
| 1 Worker      | `vibecrafted workflow <agent>`                          |
| 2 Interactive | `/vc-workflow`                                          |
| 3 Operator    | `vibecrafted workflow <agent>` przez dispatch/operatora |

### Przykład: review (ten sam kształt, inna nazwa)

| Ścieżka       | Literał                                               |
| ------------- | ----------------------------------------------------- |
| 1 Worker      | `vibecrafted review <agent>`                          |
| 2 Interactive | `/vc-review`                                          |
| 3 Operator    | `vibecrafted review <agent>` przez dispatch/operatora |

### Przykład: implement vs justdo (precyzja — nie ta sama komórka)

|                 | `implement`                                        | `justdo`                                                                                  |
| --------------- | -------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Komórka matrycy | Cykl **ship** — faza WRITE                         | **Dodatkowy** launcher postawy                                                            |
| Skill id        | `implement` / `vc-implement`                       | `justdo` / `vc-justdo`                                                                    |
| Mandat          | Ustrukturyzowane e2e (followup + marbles w środku) | Bez ceremonii; **typ zadania = prompt** (implement / review / audit / research / fix / …) |
| Pipeline        | Tak — faza VC-ship                                 | **Nie** — obok ship (ADR-0001)                                                            |
| Worker          | `vibecrafted implement <agent>`                    | `vibecrafted justdo <agent>`                                                              |
| Interactive     | `/vc-implement`                                    | `/vc-justdo`                                                                              |
| Nie jest        | Aliasem postawy na „wszystko”                      | Aliasem `implement`                                                                       |

Komórkę wybiera intencja: dostawa w fazie ship → `implement`. Daily rescue / zadanie zdefiniowane promptem, z postawą ownership → `justdo`.

### Przykład: ship (meta — inny produkt)

| Ścieżka       | Literał                                                                         |
| ------------- | ------------------------------------------------------------------------------- |
| 1 Worker      | `vibecrafted ship <agent>` (przebieg lifecycle)                                 |
| 2 Interactive | `/vc-ship` — ładujesz parasol ship; fazy trzymają własne launchery              |
| 3 Operator    | ship jako niosący pałeczkę; fazy nadal `vibecrafted scaffold \| implement \| …` |

---

## Wyjątki i odnośniki

- **Granice native:** [`vc-delegate`](../vc-delegate/SKILL.md)
- **Dyspozytura:** [`vc-dispatch`](../vc-dispatch/SKILL.md)
- **Operator:** [`vc-operator`](../vc-operator/SKILL.md)
- **Flota:** [`vc-agents`](../vc-agents/SKILL.md)
- **Weryfikacja:** [`../VERIFICATION_RULE.md`](../VERIFICATION_RULE.md)
- **Living Tree:** [`../LIVING_TREE_RULE.md`](../LIVING_TREE_RULE.md)
- **Ledger feedbacku runtime'u (poprawki per komenda):** [`../RUNTIME_FEEDBACK.md`](../RUNTIME_FEEDBACK.md)

<!-- /fleet-imperative -->
