# Replication — two fleets, two hosts, one input

Przeczytaj przed `plan --stage confront` i zanim uznasz pojedynczy werdykt za fakt.

## Why replicate

Pojedyncza flota zwraca wynik, którego stabilności nie ocenisz od środka.
Najtańszym falsyfikatorem jest druga flota na drugim hoście z **identycznymi
pinami**: ten sam `intents.json`, tory, agent i model każdego toru oraz commit
baseline. Wtedy dla każdego id:

- **agree** → fakt o kodzie (albo wspólna ślepa plamka; zobacz reguły niżej)
- **disagree** → szum agenta; klasyfikacja jest niestabilna i nie wchodzi do kolejki

Wynik terenowy (Codescribe, 2026-09-18/19, 974 intencje, sześć torów):

| pair          | compared | agree | agreement  |
| ------------- | -------- | ----- | ---------- |
| div0 · dragon | 756      | 550   | **72.8 %** |

Ten sam agent i model na obu hostach: junie/gemini-3.8-flash 100 % i 88 %;
grok/grok-4.6 67 %; cursor/cursor-grok-4.6-xhigh 61 %; kimi/kimi-code/k3 54 %.
Dominowała rozbieżność `partial ↔ landed` (76 z 206): granica „częściowo” i „done”
jest semantyczna, nie faktyczna. Dlatego brief wymaga `path:line` dla `landed`
i `gap` dla `partial`.

## Contamination check

Kopiując workdir na drugi host przez `rsync`, **wyklucz katalogi werdyktów**
(`--exclude 'verdicts*/'`). Druga flota czytająca pierwsze odpowiedzi daje
bezsensowne 100 %. Przy 100 % sprawdź, że pliki są bajtowo różne, a transkrypty
nie odwołują się do ścieżek werdyktów drugiego hosta. W sprawdzonym przypadku
była to zbieżność, nie kopia.

## Missing lanes

Niedostarczony tor pozostawia swoje id bez werdyktu na tym hoście. `merge`
raportuje liczbę na stderr; te id nie mogą być `stable`, więc nie wchodzą
do kolejki. Wyślij tor ponownie; nie wypełniaj luki ręcznie.

## Agent choice

Dwie osie, żadna nie jest „rankingiem modelu”:

- **Przepustowość** masowego czytania (shardy ekstrakcji, setki krótkich plików):
  `junie`/`gemini-3.8-flash` przy ~375 tps zrobił w minuty to, co osiem native
  subagentów Opus zrobiło zużywając budżet tygodniowy.
- **Powtarzalność** konfrontacji: preferuj agenta z najwyższą zgodnością między
  hostami w poprzednim runie. Rotuj providerów między torami, aby nie zużyć limitu
  jednej interaktywnej sesji.

Dokładne identyfikatory modeli pochodzą od Foundera lub z aktualnej listy
(`agy models`, `junie --model <x>` wypisuje pulę przy złej nazwie).
Nigdy nie zgaduj model id z pamięci.

## Plans

`plan --stage confront --host <name>` zapisuje jeden plik `vibecrafted.dispatch.v1`
na host. Ścieżki w promptach i verifierach są zapisywane przez `{id}` — parser
dispatch skanuje raw text hard-stopem, a tor `L6-build-release` zawiera zakazane
słowo. `reports_dir` musi być pod `$VIBECRAFTED_HOME/artifacts/`; każdy cut biegnie
we własnym worktree przypiętym do baseline, więc repo w briefie nadpisuje `{repo}`.
