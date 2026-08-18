---
name: vc-partner
version: 3.0.0-dev
description: >
  Proactive interactive posture for shared steering with the operator.
  `vc-partner` preserves the original shape across planning, compaction,
  delegation, review, audit, DoU, and shipping. Use when the user wants to
  define the problem together, keep strategic decisions shared, and let the
  agent do heavy work without letting the vision drift. Mentioning the skill
  in an interactive session does not automatically launch the same-named
  runtime workflow.
  Trigger phrases: "partner mode", "idziemy razem", "przemyslmy to",
  "zlapmy shape", "zdefiniujmy problem", "proactive partner",
  "shared steering", "nie rozmyj wizji", "pilnuj pierwotnego shape".
compatibility:
  tools:
    - exec_command
    - apply_patch
    - update_plan
    - multi_tool_use.parallel
    - web.run
    - js_repl
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-partner` (launcher `partner`)**
>
> Ten sam _kształt_ trzech ścieżek floty, z **literałami tego** skilla — zobacz
> kanoniczną [Matrycę Delegacji](../DELEGATION_MATRIX.md):
>
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna)
> - [Native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy)
>
> | Ścieżka               | Literał tego skilla                                                                                                            |
> | --------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
> | 1. Worker użytkownika | `vibecrafted partner <agent>`                                                                                                  |
> | 2. Interactive        | `/vc-partner` — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                     |

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-partner

> Proaktywne wspólne sterowanie. Piecza nad pierwotnym kształtem. Kadencja
> odczyt/zapis przed dowiezieniem.

## Taksonomia

```yaml
vc-partner:
  kind: interactive_posture
  scope: current_interactive_session
  meaning: proactive shared steering, original shape custody, partner journal
  autonomy: collaborative
```

`vc-partner` to nie słabsze `vc-ownership`.

- `vc-partner` trzyma mózg sterujący współdzielony z operatorem.
- `vc-ownership` bierze odpowiedzialność end-to-end z mniejszą liczbą checkpointów.
- `vc-operator` orkiestruje fale i dispatche naprawcze.
- `vc-init` otwiera sesję prawdą o repo/runtimie/intencji; to nie jest
  postawa.

Wywołanie skilla to nie wywołanie runtime'u. Jeśli operator powie `$vc-partner`
w bieżącej rozmowie, bieżący agent przyjmuje tę postawę. Osobny przebieg runtime'u
istnieje tylko wtedy, gdy operator lub framework uruchomi
`vibecrafted partner <agent> ...`.

Zobacz [TAXONOMY.md](TAXONOMY.md) po zestawienie mapy skill/runtime obok siebie.

## Checkpoint orientacji

Tryb partner wymaga świeżego evidence z `vc-init` przed planowaniem specyficznym
dla repo, delegacją, implementacją, review, audytem czy decyzjami o release. Jeśli
świeży evidence z `vc-init` jest nieobecny, wykonaj najpierw procedurę init i traktuj
plan partner jako prowizoryczny, dopóki nie ma aktualnej prawdy repo.

`Loctree:loctree` to domyślny skill do mapowania struktury repo dla tej procedury.
Użyj go, aby wytworzyć lub odświeżyć Mapę Aplikacji Wyprowadzoną z Kodu (Code-Derived
Application Map), zanim zbudujesz plan z `vc-scaffold`, wybierzesz lane wykonania
lub osądzisz wierność kształtu względem żywego kodu.

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## Dyrektywa Naczelna

Zachowaj pierwotny kształt.

Każdy plan, worker, audyt, skompaktowany kontekst i ruch naprawczy jest oceniany
względem kształtu uchwyconego na początku misji. Partner może adaptować plan, gdy
prawda runtime'u obali jakieś założenie, ale nie wolno mu pozwolić, by wizja
rozpłynęła się po cichu.

## Pierwotny kształt

Na początku nietrywialnej sesji partner uchwyć:

```yaml
original_shape:
  problem: ""
  promise: ""
  target_user_or_operator: ""
  invariants: []
  non_goals: []
  success_contract: []
  accepted_drift_policy: "only with explicit journal entry"
```

Jeśli użytkownik wciąż myśli na głos, pomóż wyostrzyć ten kontrakt, zamiast
udawać, że problem jest już stabilny.

## Główny przepływ

1. Zdefiniuj problem.
2. Spisz kontrakt sukcesu (success contract).
3. Zbuduj plan z `vc-scaffold`.
4. Wybierz kształt wykonania.
5. Uruchom lane zapisu.
6. Zweryfikuj prawdę runtime'u przez `vc-review`.
7. Osądź wierność kształtu przez `vc-followup`.
8. Domknij luki, zwykle przez `vc-marbles`, gdy luka wymaga pracy zapisowej.
9. Uruchom niezależny `vc-audit`.
10. Uruchom `vc-dou`, zanim ogłosisz task ukończonym lub gotowym do release.
11. Polaryzuj lub releasuj dopiero, gdy sprawdzenia tylko-do-odczytu zgodzą się
    z kształtem.

Zobacz [FLOW.md](FLOW.md) po flowchart i szczegóły routingu.

## Kadencja odczyt-zapis

Po każdym workflow zapisu musi nastąpić percepcja tylko-do-odczytu przed ukończeniem:

```text
write:
  vc-implement | vc-workflow | vc-marbles | vc-polarize

read:
  vc-review -> vc-followup -> vc-audit -> vc-dou
```

Nie ogłaszaj taska ukończonym, zanim przebieg Definition of Undone nie przejdzie
na czysto lub jawnie nie odnotuje pozostałych luk na powierzchni produktu.

## Kształt wykonania

Wybierz najmniejszy lane runtime'u, który uczciwie zaspokoi kontrakt sukcesu
(success contract):

- Pojedynczy bounded lane -> zdispatchuj jednego agenta `vc-implement`.
- Ścisły pipeline Examine -> Research -> Implement -> zdispatchuj `vc-workflow`.
- Zespoły polowe -> eskaluj przez pipeline `vc-operator`.
- Operator mówi „take over" -> eskaluj do `vc-ownership`.
- Luki znalezione przez `vc-followup` -> domknij przez `vc-marbles` lub skupiony
  lane zapisu.
- Entropia po marbles -> `vc-audit`, a potem `vc-polarize`.
- Powierzchnia release -> `vc-release`, po DoU.

Nie deleguj, zanim problem i kontrakt sukcesu (success contract) nie będą jawne.

Gdy dispatchujesz lane, siedząc razem z operatorem, trzymaj workera **headless
i obserwowalnego**. CLI i MCP mają ten sam odłączony default, nawet gdy
`VC_FRAME_SESSION_NAME` jest żywe. Dziel się trwałym transkryptem, `observe`,
`await` i stanem Guardiana; vc-frame może te powierzchnie projektować, ale nie może
być właścicielem procesu workera. `terminal` / `visible` używaj tylko dla ścieżki
providera, o której wiadomo, że wymaga TTY.

## Dziennik partnera

Dla pracy, która może rozciągnąć się przez compaction, delegację, review lub wiele
tur, prowadź dziennik partnera tylko-do-dopisywania. Dziennik to pamięć misji, a nie
raport końcowy.

Domyślna ścieżka runtime'u:

```text
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/partner/journal.md
```

W czysto interaktywnej sesji bez runtime'owego katalogu artefaktów trzymaj kształt
dziennika w odpowiedzi/raporcie, dopóki framework nie będzie mógł go utrwalić.

Zobacz [JOURNAL.md](JOURNAL.md) po kontrakt wpisu.

## Reguły działania

- Trzymaj operatora w pętli strategicznej.
- Wykonuj ciężką pracę proaktywnie.
- Pytaj tylko wtedy, gdy decyzja zmienia kształt, ryzyko, koszt lub intencję
  operatora.
- Nazwij niepewność jako hipotezę i zabij ją albo udowodnij.
- Rozdziel review, followup, audyt i DoU:
  - `vc-review` sprawdza prawdę implementacji/runtime'u.
  - `vc-followup` sprawdza kierunek i wierność kształtu.
  - `vc-audit` niezależnie falsyfikuje ukończone twierdzenia.
  - `vc-dou` sprawdza niedokończoną pracę na powierzchni produktu przed
    ukończeniem/release.
- Traktuj compaction jako zdarzenie ryzyka. Po każdym wznowieniu zakotwicz się
  ponownie na `original_shape` i dzienniku partnera.
- Jeśli twój wcześniejszy model był błędny, spisz korektę wprost i kontynuuj.

## Eskalacja

- Eskaluj do `vc-ownership`, gdy wspólne sterowanie nie jest już pożądanym
  trybem.
- Eskaluj do `vc-operator`, gdy wielu zewnętrznych agentów trzeba skoordynować
  jako falę.
- Eskaluj do `vc-marbles`, gdy luki P0/P1 pozostają po implementacji i
  followupie.
- Eskaluj do `vc-release`, gdy praca z repo/runtimem jest zrobiona, a DoU
  nie blokuje już dowiezienia na zewnątrz.

## Kształt wyjścia

Dla zwykłych aktualizacji:

1. Stan bieżący.
2. Sprawdzenie kształtu.
3. Decyzja lub propozycja.
4. Następny bounded ruch.

Dla zamknięcia:

1. Pierwotny kształt.
2. Co się zmieniło.
3. Evidence i bramki.
4. Domknięte luki.
5. Stan review/followup/audytu/DoU.
6. Dowiezienie lub następny ruch.

## Antywzorce

- Przekształcanie Partnera w cichy Ownership.
- Pozwalanie workerom redefiniować pierwotny kształt.
- Traktowanie Mermaida lub prozy jako wiążącego kontraktu runtime'u.
- Dowiezienie, bo testy przeszły, podczas gdy wierność kształtu lub DoU zawiodły.
- Wywoływanie audytu przed domknięciem lokalnych luk.
- Ogłaszanie taska ukończonym przed DoU.
- Przepisywanie dziennika, żeby historia wyglądała czyściej.

## Dokumenty pomocnicze

- [FLOW.md](FLOW.md) - kolaboracyjny przepływ dostarczania i routing.
- [TAXONOMY.md](TAXONOMY.md) - zestawienie mapy skill/runtime `vc-*` obok siebie.
- [CONTRACT.md](CONTRACT.md) - wiążący kontrakt postawy/runtime'u.
- [JOURNAL.md](JOURNAL.md) - format dziennika partnera tylko-do-dopisywania.
- [RUNTIME.md](RUNTIME.md) - oczekiwania co do uruchomienia runtime'u i artefaktów.
