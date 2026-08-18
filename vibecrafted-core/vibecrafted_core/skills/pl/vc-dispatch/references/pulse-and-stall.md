# Puls i stall — doktryna autonomicznego liveness workera

Żywy worker dostaje ZERO ingerencji. Martwy zostaje zabity i zastąpiony bez
ceremonii — w OBU postawach (vc-partner i vc-ownership). Nie możesz ryzykować
batcha na niedowiezionym briefie: późniejsze cięcia zwykle od niego zależą.
Recovery to odpowiedzialność za dowiezienie i akt wzajemnego zaufania, nie
zdarzenie eskalacyjne.

## Najpierw automatyzacja frameworka

vibecrafted dostarcza heartbeat — sięgnij po niego, zanim ręcznie sklecisz timery:

| Potrzeba                            | Mechanizm                                                                                                                                                                                                                  |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| await dyspozytorski rangi komendy   | `vibecrafted loop spanko --run-id <id> --agent <a> --verify '<cmd>' --tracker <tracker.md> --cut-id <cut> --then '<next dispatch>'`                                                                                        |
| niższopoziomowy prymityw await      | `vibecrafted loop await-run --run-id <id> --agent <a> --then-cmd '<next dispatch>'`                                                                                                                                        |
| maszyna stanów linii                | `vibecrafted loop start/next/status/cancel/complete` (`--max-iterations`, `--completion-promise`, `--state-file`)                                                                                                          |
| heartbeat na crontabie z kontekstem | `vibecrafted cron line --root <repo> --every-minutes 10 --then-cmd 'vibecrafted loop next'` (łapie Loctree + AICX na każdy tick)                                                                                           |
| wznów po oknie bezczynności         | `vibecrafted cron tick --after-idle-minutes 10 --then-cmd <cmd>`                                                                                                                                                           |
| auto-await pane per-dispatch        | `vibecrafted-await-watch.sh --meta <meta.json>` — tailuje transcript, obserwuje status meta + deltę rozmiaru + liveness procesu, sam się terminuje (tunable'e: `VIBECRAFTED_AWAIT_IDLE_TIMEOUT`, `VIBECRAFTED_AWAIT_POLL`) |

Kanoniczny kontrakt supervisora (zobacz `docs/runtime/AGENT_OPS.md`): po dispatchu
od razu uzbrój `vibecrafted <agent> await --run-id <id>` po stronie supervisora.
JSON control plane'u, pliki raportów, transkrypty, pane'y i zaplanowane wybudzenia
są wyłącznie diagnostyczne — to nie są sygnały wybudzenia. Hedge'owanie awaita
doraźnymi pollerami/watcherami to naruszenie Class 3; napraw
`control_plane.await_run`, nie normalizuj hedge'a.

Liveness na 3 sygnałach: verdict awaita, terminalne meta runu, martwy pid workera,
plus obecność obiecanego raportu. Dwa zgodne sygnały wystarczą, żeby działać, trzy —
żeby ogłosić done; każda niezgodność oznacza traktuj jako żywy i uzbrój await
ponownie. Znany skew: rc=0 przy żywym runie i meta zawieszone na `active`/`stalled`
po faktycznym zakończeniu.

Prowadź await dedykowaną komendą (NASZ vc-loop / cron) jako STANDARD nawet z
sesji interaktywnej — dispatchowany run MA CLI. Pętla na poziomie harnessa
(Claude `/loop 15m <watch prompt>`) to prawdziwy last-resort, tylko gdy CLI
vibecrafted jest faktycznie niedostępne. Manualny puls poniżej to warstwa
DIAGNOSTYCZNA — co inspekcjonuje tick i forensyka, którą uruchamiasz, gdy
automatyzacja mówi „still running", a nic się nie rusza.

## Puls (na każdy watch tick, kadencja ~15 min)

Trzy NIEZALEŻNE sygnały — nigdy nie oceniaj po jednym:

```bash
# 1. Control plane: run status
grep -E '"status"|"exit_code"|"liveness"' \
  ~/.vibecrafted/control_plane/runs/<run_id>.json

# 2. Agent session file: is the model actually producing events?
#    (codex example; adjust the path per agent)
S=$(ls -t ~/.codex/sessions/<YYYY>/<MM>/<DD>/*.jsonl | head -1)
stat -f "mtime=%Sm size=%z" "$S"      # frozen mtime + static size = no events

# 3. Tree truth: is anything being written?
git log --oneline -1 && git status --short
```

Zdrowe wzorce:

- jsonl rosnący o setki KB między próbkami → faza reconu, zostaw w spokoju.
- pliki WIP matchujące scope cięcia w `git status` → faza edycji.
- commit wylądował, ale run wciąż `running` → worker pisze swój raport
  (normalne; NIE flipuj jeszcze, czekaj na exit).

## Werdykt stall (twarda reguła)

**≥10 minut ciszy na WSZYSTKICH TRZECH sygnałach** — control plane stale, plik
sesji agenta zamrożony (mtime + size), zero delt drzewa — ORAZ czas CPU procesu
płaski (np. `ps -o etime,time,%cpu -p <pid>`: minuty elapsed, sekundy CPU)
→ run jest dead-in-the-water (typowo zawieszone pierwsze wywołanie modelu).

Sygnał matchujący znaną awarię ≠ ta awaria: różnicuj przed działaniem, ale gdy
wszystkie sygnały się zgadzają, działaj bez pytania. Nie czekaj „uprzejmego"
dodatkowego ticka — batch jest ofiarą.

## Procedura recovery

1. **Zabij całe drzewo launchera** (pid launchera + shell + binarka agenta +
   stream bridge): `kill <pids>`; zweryfikuj przez `ps`/`pgrep`.
2. **Sprawdź orphany** — supervisor zabity w połowie pętli mógł już odpalić
   workera, który DOWOZI: `pgrep -f '<agent> exec'` + `git log` +
   `git status`. Jeśli orphan pracuje, traktuj go jako żywy run.
3. **Inwentaryzuj resztki**: co martwy run zostawił na drzewie? Zwykle nic
   (czysty stall) — stwierdź to wprost tak czy siak.
4. **Aktualizacja BATON w TYM SAMYM pliku promptu** (dopisz datowaną sekcję):
   co się zatrzymało, evidence (czas elapsed kontra CPU, zamrożony plik sesji,
   zero edycji), co dziedziczy nowy worker („nic — zacznij od zera na żywym
   HEAD" albo dokładny opis WIP), plus wszelkie commity, które wylądowały od tego
   czasu.
5. **Re-dispatch** — ten sam agent albo inny (decyzja dyspozytora; drugi
   identyczny stall to mocna wskazówka, by zmienić agenta). Nigdy ślepy restart
   bez evidence wpisanego w prompt.
6. **Ledger**: evidence w trackerze dostaje ślad stall + recovery (run id,
   diagnoza, kto zabił); journal dostaje pełny wpis.

## Refire kontra recovery

- **Recovery-dispatch** = run UMARŁ; aktualizacja BATON opisuje zwłoki.
- **Refire (mini-marbles)** = run SKOŃCZYŁ, ale powierzchnia potrzebuje kolejnej
  rundy (częściowa dostawa, „B<n> not done" w raporcie, presja zbieżności na
  kruchym obszarze). Ten sam prompt verbatim — klauzula idempotencji w EXTRA
  czyni to bezpiecznym; gorące podłoże czyni to tanim (vc-marbles: cache heat).
  `<ENTER> re-run` vc-frame na pane spawnu to kanoniczny refire jednym
  klawiszem; operator może refire'ować za twoimi plecami — traktuj nieoczekiwany
  świeży run znanego promptu jako refire, nie anomalię.

## Mechanika await

- Zbackgrounduj await (`vibecrafted <agent> await --run-id <id>`) i pozwól jego
  ukończeniu cię obudzić; puls tick to fallbackowy heartbeat.
- Pliki raportów mogą pojawić się pod nazwą `pending-report-*` przed kanoniczną
  — szukaj w katalogu raportów po mtime, nie po zgadywanej nazwie pliku.
- Artefakty mogą mieszkać pod katalogami org o wariantach wielkości liter (APFS
  case-insensitive): jeden katalog, dwie pisownie — nie rozjazd ścieżki.
