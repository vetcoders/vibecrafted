---
name: vc-delegate
version: 2.0.0
description: >
  Native operator-side delegation doctrine for small bounded native cuts.
  Use this when the operator agent must decide whether work should stay
  in-process through native subagents or be escalated upward into vc-agents.
  Trigger phrases: "implement with agents", "delegate to subagents", "zaimplementuj",
  "run agents", "parallel tasks", "delegate safely", "native agents",
  "Task tool agents", "implement plan", "uruchom agentów", "subagenty natywne",
  "bezpieczne agenty", "implement without externals", "no osascript".
compatibility:
  tools: []
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-delegate` (launcher `delegate`)**
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
> | 1. Worker użytkownika | `vibecrafted delegate <agent>`                                                                                                  |
> | 2. Interactive        | `/vc-delegate` — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                      |
>
> **Uwaga:** Native subagent **bounds**; freer native under other skills still points here for limits.

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-delegate

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

Nie uruchamiaj `vc-delegate` bezpośrednio. Jego zamiennik od strony operatora to:

```bash
vibecrafted <launcher> <agent> --file '/path/to/plan.md'
```

```bash
vc-<launcher> <agent> --prompt '<prompt>'
```

Ten skill nie jest samą zewnętrzną flotą. To doktryna operatora dla
natywnej delegacji: kiedy trzymać cięcie lokalnie, kiedy przestać udawać, że natywne
cięcie jest wciąż bounded, i kiedy operator powinien eskalować do `vc-agents`.

### Konkretne przykłady dispatchu

```bash
vibecrafted partner codex --prompt 'Split this into one small native cut'
vibecrafted implement claude --file /path/to/plan.md
vibecrafted workflow agy --prompt 'Keep this local unless it clearly wants the external fleet'  # gemini deprecated
```

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## Polityka natywnej delegacji

Używając natywnych subagentów, domyślnie trzymaj ten sam frontier co agent rodzic.

Dlaczego:

- Delegacja natywna do tak samo nazwanego modelu zachowuje styl rozumowania najbliższy rodzicowi.
- Maksymalizuje lokalność kontekstu i szanse na ponowne użycie cache'u.
- Na tym samym repo i tej samej rodzinie tasków to zwykle najlepszy domyślny stosunek kosztu do jakości.

Domyślnie:

- Model rodzica -> dokładnie ten sam natywny model, gdy dostępny.
- Jeśli dokładny model jest niedostępny, użyj najbliższego natywnego odpowiednika i powiedz to wprost.

> „Model rodzica" oznacza tę samą konkretną tożsamość modelu, nie po prostu tego samego dostawcę czy rodzinę.

Wyjątki:

- Tylko Codex: Ten wyjątek zastępuje dla Codexa domyślne reguły same-parent powyżej i same-named-first poniżej. Przestrzegaj zatwierdzonego przez operatora routingu modelu lub poziomu rozumowania. Jeśli go nie ma, wybierz model o możliwościach wystarczających do wymaganego poziomu jakości, z uzasadnionym marginesem, a następnie najbardziej ekonomiczną opcję spośród modeli spełniających ten próg. Od razu użyj mocniejszego modelu, gdy uzasadniają to niejednoznaczność, głębokość zależności lub konsekwencje błędu; nie wymuszaj próby cheap-first ani nie broń słabego wyniku jego niższą ceną. Eskaluj po wykazanym ograniczeniu, bez automatycznego wymagania, by mocniejszy model powtarzał każdy wynik modelu ekonomicznego. Sama etykieta roli subagenta nie dowodzi faktycznego modelu ani poziomu rozumowania; potwierdzając routing, użyj dostępnych metadanych runtime'u, bez obowiązkowego sprawdzania statusu po każdym uruchomieniu subagenta.
- Claude: Do rozległych, długotrwałych tasków preferuj `opus[1m]`; do łatwiejszych lub lżejszych preferuj `sonnet[1m]`.
- Gemini: Jeśli `gemini-3.1-pro-preview` jest niedostępny lub niestabilny w szczycie obciążenia, fallback delegacji natywnej na `auto-gemini-3`.

Reguła:

- Domyślnie najpierw natywni agenci o tej samej nazwie.
- Wyjątki cross-model stosuj intencjonalnie, nigdy od niechcenia.
- Jeśli schodzisz w dół dla szybkości lub dostępności, odzyskaj jakość w przebiegu orkiestracji rodzica.

## Kierunek eskalacji

`vc-delegate` to bounded narzędzie delegacji natywnej dla agenta operatora.

Jego rola to pomóc operatorowi zejść głębiej lokalnie albo przyznać, że task
przerósł delegację natywną.

Jeśli natywnie delegowany task staje się zbyt rozległy, zbyt przekrojowy lub zbyt
zależny od orkiestracji specyficznej dla modelu, nie powinien udawać ukończenia.

Zamiast tego musi:

- zgłosić, że task przekroczył scope delegacji natywnej, albo
- wrócić do operatora rodzica, albo
- eskalować do `vc-agents`.

Eskalacja do `vc-agents`:

- z zasady `vc-agents` nie jest generycznym mechanizmem rekurencji.
- to świadoma decyzja operatora oparta na `vc-why-matrix`.
- gdy agent floty zostanie już wybrany, ten wybór musi pozostać stabilny, chyba że operator wprost go zmieni.

## Granica scope

Ta doktryna jest dla warstwy operatora.

Nie jest przekazywana jako polityka wykonawcza samym malutkim natywnym subagentom.
Natywni subagenci to pomocnicy wykonawczy, nie aktorzy orkiestracji.

Przeczytaj `skills/vc-agents/SKILL.md` razem z tym plikiem, gdy operator potrzebuje
pełnej zewnętrznej floty oraz `vc-why-matrix`.
