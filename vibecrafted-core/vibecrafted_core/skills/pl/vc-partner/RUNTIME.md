# Runtime `vc-partner`

`vc-partner` jako skill interaktywny nie uruchamia runtime'u automatycznie.

Runtime startuje tylko z twarzą TTY (ta sama rodzina co `vc-init`):

```bash
vibecrafted partner <agent>
vibecrafted partner <agent> --runtime plain
vc-start   # potem [Nowy] z rytuałem partner, potem /vc-partner
```

`vc-partner` bez TTY odmawia. `--prompt` / `--file` na
`vibecrafted partner` to dodatkowy kontekst seed, nigdy headless worker.

## Odpowiedzialności runtime'u

Runtime partnera tworzy trwały stan dla wspólnego sterowania:

- metadane przebiegu
- transcript
- raport partnera
- dziennik tylko-do-dopisywania
- linki do delegowanych runtime'ów
- podsumowanie zamknięcia

## Układ artefaktów

```text
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/partner/
  journal.md
  reports/
    <timestamp>_<slug>_partner.md
    <timestamp>_<slug>_partner.transcript.log
    <timestamp>_<slug>_partner.meta.json
```

Dziennik to kręgosłup pamięci. Raporty to migawki.

## Lane runtime'u

| Potrzeba                                   | Lane runtime'u                                                    |
| ------------------------------------------ | ----------------------------------------------------------------- |
| Pojedynczy bounded build                   | `vibecrafted implement <agent>`                                   |
| Ścisły Examine -> Research -> Implement    | `vibecrafted workflow <agent>`                                    |
| Wiele zespołów polowych                    | postawa `$vc-operator` + `vibecrafted dispatch` lub lane workflow |
| Pełne przejęcie                            | `vibecrafted ownership <agent>`                                   |
| Review implementacji                       | `vibecrafted review <agent>`                                      |
| Sprawdzenie kształtu/trajektorii           | `vibecrafted followup <agent>`                                    |
| Niezależna falsyfikacja                    | `vibecrafted audit <agent>`                                       |
| Sprawdzenie undone na powierzchni produktu | `vibecrafted dou <agent>`                                         |
| Zbieżność luk                              | `vibecrafted marbles <agent>`                                     |
| Redukcja entropii po marbles               | `vibecrafted polarize <agent>`                                    |
| Powierzchnia release                       | `vibecrafted release <agent>`                                     |

## Zamknięcie runtime'u

Runtime partnera może zostać zamknięty tylko wtedy, gdy prawdziwy jest jeden
stan terminalny:

```yaml
terminal_state:
  shipped:
    requires:
      - original_shape preserved or intentionally changed
      - gates recorded
      - review/followup/audit/dou findings handled or deferred explicitly
      - next move named
  escalated_to_ownership:
    requires:
      - reason for takeover
      - current original_shape
      - handoff state
  blocked_with_evidence:
    requires:
      - blocker
      - attempted checks
      - nearest safe next action
```

## Nie-cele

- Nie używaj runtime'u, by uniknąć wspólnego podejmowania decyzji.
- Nie uruchamiaj zespołów polowych, zanim nie istnieje kontrakt sukcesu (success contract).
- Nie pozwalaj workerom runtime'u zmieniać pierwotnego kształtu bez wpisu w dzienniku.
