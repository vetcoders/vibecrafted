# Runtime `vc-operator`

`vc-operator` jako skill interaktywny nie uruchamia runtime'u automatycznie.

Runtime zaczyna się dopiero, gdy operator wybierze żywy pas supervisora lub workflow:

```bash
vibecrafted dispatch plan.dispatch.toml --doctor
vibecrafted dispatch plan.dispatch.toml --dry-run --json
vibecrafted dispatch run --run-id <id> --root . --report report.md --transcript trace.log -- <worker>
vibecrafted workflow claude --file /path/to/plan.md
vibecrafted implement codex --prompt '<bounded slice>'
```

## Obowiązki runtime'u

Postawa operatora tworzy lub konsumuje trwały stan dla orkiestracji floty:

- metadane runu
- transkrypt
- tracker fal
- repozytoryjny Dziennik Operatora append-only
- briefy workerów
- karty uruchomienia i run ID
- zamknięcia per fala
- finalny handoff w punkcie stopu
- wpisy mutacji planu i guardraili bezpieczeństwa w dzienniku operatora

## Układ artefaktów

```text
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/
  plans/
  reports/
  tmp/
  dispatch-result.json or run-specific result files
  <timestamp>_<slug>.transcript.log
  <timestamp>_<slug>.meta.json
```

Datowane raporty, trackery, transkrypty, briefy, zamknięcia i metadane runu to
projekcje/evidence runu. Nie stają się drugim systemem dziennika. Jedynym
kanonicznym stałym dziennikiem operatora jest plik
`<repo-root>/.vibecrafted/THE_JOURNAL.md` (gitignored; dawna nazwa `JOURNAL.md` jest
wycofana). Repozytoryjny katalog `.vibecrafted/` pozostaje ignorowanym stanem runtime'u.

## Pasy runtime'u

| Potrzeba                      | Pas runtime'u                      |
| ----------------------------- | ---------------------------------- |
| Plan jest mglisty             | `vibecrafted scaffold <agent>`     |
| Jeden slice workera           | `vibecrafted implement <agent>`    |
| Ścisły slice ERi              | `vibecrafted workflow <agent>`     |
| Zbieżność na dryfie prawdy    | `vibecrafted marbles <agent>`      |
| Dowiezienie slice'a od A do Z | `vibecrafted ownership <agent>`    |
| Pauza na wspólną strategię    | `vibecrafted partner <agent>`      |
| Niezależna weryfikacja        | `vibecrafted audit <agent>`        |
| Deterministyczny supervisor   | `vibecrafted dispatch <file.toml>` |
| Dowiezienie na zewnątrz       | `vibecrafted release <agent>`      |

## Headless to domyślny runtime workera

Tryb runtime'u to `headless | terminal | visible` (`SUPPORTED_RUNTIMES`). Selektor
rozstrzyga tak:

- Workery workflow z CLI i z MCP domyślnie idą jako `headless`, niezależnie od tego,
  czy `VC_FRAME_SESSION_NAME` jest żywe.
- Wykonanie headless startuje workera w odłączonej sesji procesu. Kontraktem
  obserwacji są: trwały stan runu, transkrypt, settlement Guardiana, `observe` i
  `await`.
- vc-frame może wyrenderować projekcję transkryptu albo stanu runu. Projekcja to nie
  własność procesu i jej zamknięcie nie może zatrzymać workera.
- `terminal` / `visible` to jawny pas kompatybilności dla ścieżki providera, o której
  wiadomo, że wymaga TTY. Dopóki nie ma brokera PTY prowadzonego przez daemona,
  zostaje przywiązany do terminala.
- `init`, `operator` i samo interaktywne `resume` pozostają prawdziwymi User Session
  opartymi o PTY.

## Stany terminalne

```yaml
terminal_state:
  stopped_at_operator_button:
    requires:
      - wave tracker updated
      - repozytoryjny JOURNAL.md zaktualizowany o materialne decyzje
      - reports and SHAs named
      - remaining unpermitted human action named
  completed_with_plan_permission:
    requires:
      - permission source named
      - tracker updated
      - repozytoryjny JOURNAL.md zaktualizowany o materialne decyzje
      - reports and SHAs named
  blocked_with_evidence:
    requires:
      - blocker classification
      - attempted recovery
      - nearest safe next action
  escalated:
    requires:
      - target skill
      - reason
      - handoff state
```

## Nie-cele

- Nie używaj runtime'u do ukrywania decyzji przed operatorem.
- Nie czyń zakładki-projekcji właścicielem ani sygnałem życia workera.
- Nie obchodź telemetrii uruchomień.
- Nie zamieniaj handoffu w punkcie stopu w push/merge/deploy, chyba że spisany plan
  lub bieżąca sesja jawnie dopuściły to działanie.
