# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Runbook Operatora

Terminal-first: od nowego terminala do nadzorowanego release'u. Linux i WSL2
korzystają z decka POSIX; natywny Windows ma mniejszą powierzchnię.
Instalację i granice platform opisuje [Entry book](ENTRY_BOOK.md).
[English](../RUNBOOK.md). Komendy porównane ze źródłowym deckiem 2026-10-06.

To praktyczny towarzysz kanonu: co wpisać, czego oczekiwać i co zrobić po błędzie.
Doktryna faz: [LIFECYCLE](../runtime/LIFECYCLE.md); klasy awarii:
[AGENT_OPS](../runtime/AGENT_OPS.md). Jeśli dokument przeczy
`vibecrafted help --all`, wygrywa żywy deck.

## 0. Rozmowa i zadanie to dwa wejścia

Rozmawiasz w CLI agenta: Claude Code, Codex. `init` i `partner` otwierają
interaktywną sesję; `init --runtime plain` korzysta z bieżącego terminala bez
kokpitu. Launchery zadań, takie jak `implement`, wymagają wejścia:

```
$ vibecrafted implement codex
error: Launch requires either --prompt text or --file path.
```

W sesji interaktywnej ustalasz zadanie. Gotową pracę wysyłasz do wykonania.

| Cel                             | Wejście                                      |
| ------------------------------- | -------------------------------------------- |
| Rozmowa z agentem w repo        | `claude` / `codex`                           |
| Orientacja przed pracą          | `vibecrafted init <agent>`                   |
| Wysłanie konkretnego zadania    | `vibecrafted <skill> <agent> --prompt "…"`   |
| Wykonanie przygotowanego briefu | `vibecrafted <skill> <agent> --file <brief>` |
| Nadzór nad runami               | `status`, `observe`, `await`, dashboard      |

`--prompt` przyjmuje tekst wpisany teraz. `--file` — przygotowany brief.
Nie potrzebujesz pliku `.md`, żeby rozpocząć rozmowę.

## 1. Start w nowym terminalu

```bash
cd /path/to/your/repo
vibecrafted doctor
vibecrafted init claude --runtime plain
# Albo samo claude / codex, jeśli chcesz wejść bezpośrednio do CLI providera.
```

**Wynik:** diagnoza instalacji, potem interaktywna orientacja w tym repo.
**Błąd → naprawa:** brak CLI providera wymaga jego instalacji i logowania;
brak kokpitu nie blokuje `--runtime plain`. Czerwone pozycje doctora wymagają
naprawy, żółte nazywają brakujące możliwości.

Kiedy masz konkretne zadanie:

```bash
vibecrafted workflow claude --prompt "Plan and implement <task>"
vibecrafted implement codex --prompt "Ship <task>"
```

- `vibecrafted start --repo /path/to/repo` tworzy i otwiera nowy workspace.
  Nie jest aliasem dashboardu. Kod 3 oznacza istniejącą nazwę;
  `vibecrafted start resume` służy do świadomego powrotu.
- Agenci: `claude · codex · agy · junie · grok · cursor · kimi · copilot`.
  Obsługa `--model`, uprawnień i sandboxa zależy od providera. Nieobsługiwany
  zestaw jest odrzucany przed startem. Czytaj `vibecrafted capabilities --json`
  i `--help` wybranego skilla.
- `vc-<skill>` to skrót instalowany dla skilla. `justdo` ma własną tożsamość
  i postawę; nie jest aliasem `implement`.
- Natywny Windows ma własne `doctor` i `server`. `start`, `dashboard`, `init`
  i pozostałe komendy decka POSIX kończą z kodem 2 i wskazują WSL2. Do sesji
  natywnej uruchom CLI providera w PowerShellu.

<!-- Sources: scripts/vibecrafted cmd_start_help / cmd_init_help / help_all;
     vibecrafted-core/vibecrafted_core/cli.py win32 lifecycle refusal;
     vibecrafted-core/vibecrafted_core/help_surface.py capabilities. -->

## 2. Składnia dispatchu

```bash
vibecrafted <skill> <agent> --prompt "text" | --file brief.md
vibecrafted implement <agent> <brief.md>
vibecrafted research <agent> <brief.md>
vibecrafted review <agent> <brief.md>
vibecrafted observe <agent> --last
```

Jawne zadanie bez terminalowego UI:

```bash
vibecrafted implement codex --runtime headless --prompt "Ship <task>"
```

**Wynik:** zapisany run i ścieżki artefaktów. `--await` po wypisaniu receiptu
czeka na ten run; domyślny launch headless wraca, gdy praca trwa dalej.
**Błąd → naprawa:** brak CLI providera to błąd wymagania wstępnego, nie udany
launch. Każdy dispatch tworzy run (`impl-…`, `scaf-…`, `work-…`) w control plane.
Jednostką prawdy jest run, nie zakładka terminala.

<!-- Source: scripts/vibecrafted implement --help;
     vibecrafted-core/vibecrafted_core/help_surface.py workflow flags. -->

## 3. Nadzór: gdzie jest prawda

```bash
vibecrafted status
vibecrafted await <agent> --run-id <id>
vibecrafted observe <agent> --last
vibecrafted settlements list
vibecrafted server status
vibecrafted tui
```

**Wynik:** stan runów, monitor, raport, ledger f/x/n oraz stan serwera.
Zakończenie procesu nie jest dowodem dostarczenia. `tui` to konsola POSIX;
nie uruchamiaj jej jako natywnej możliwości Windows.

Ścieżki POSIX (domyślne `VIBECRAFTED_HOME`; Windows używa lokalizacji z Entry book):

| Ścieżka                                           | Zawartość                                            |
| ------------------------------------------------- | ---------------------------------------------------- |
| `~/.vibecrafted/control_plane/runs/<id>.json`     | stan, żywotność, kod wyjścia, trzy osie              |
| `~/.vibecrafted/control_plane/runs/archive/`      | zarchiwizowane runy                                  |
| `~/.vibecrafted/control_plane/runtime_runs/<id>/` | transkrypt i artefakty runtime'u                     |
| `~/.vibecrafted/control_plane/launches/*.log`     | stderr launchera — pierwszy trop przy cichej śmierci |
| `~/.vibecrafted/artifacts/<org>/<repo>/<day>/`    | plany, briefy, raporty                               |

Trzy osie: `execution_state`, `proof_state`, `delivery_state`. Samo
`completed` + `artifact_ok` nie jest odbiorem. Dopiero weryfikator może zmienić
tracker na `[x]`: [osie control plane](../../vibecrafted-core/vibecrafted_core/control_plane.py).

## 4. Pełny lifecycle

Kanon 11 faz odczytu i zapisu: [LIFECYCLE](../runtime/LIFECYCLE.md).

```bash
vc-ship codex --prompt "Run the full lifecycle for <goal>"
vibecrafted ship
```

Founder nadaje kierunek i trzyma przyciski akceptacji. Agent-Operator prowadzi
przekazanie batonu. Sterowanie lifecycle obejmuje `approve`, `interrupt`,
`fallback`, `accept-dou`, `force-audit` przez powierzchnię MCP i
`vibecrafted dispatch run …`. Raport jednej fazy jest wejściem następnej.

Najkrótsze ścieżki z generowanego `~/.vibecrafted/START_HERE.md` (nie edytuj ręcznie):

```bash
vibecrafted init claude
vibecrafted workflow claude --prompt "Plan and implement <task>"
vibecrafted implement codex --prompt "Ship <task>"

vibecrafted dou claude --prompt "Audit launch readiness"
vibecrafted decorate codex --prompt "Polish the release surface"
vibecrafted hydrate codex --prompt "Package the product"
vibecrafted release codex --prompt "Prepare release steps"
```

## 5. Sesje i zakładki vc-frame

- Zakładki workerów trafiają do hosta związanego z workspace:
  `<label>-<workspace_short> workers`. Jawny override to
  `VIBECRAFTED_WORKER_SESSION`. Sama nazwa katalogu jest awaryjnym fallbackiem,
  gdy nie da się otworzyć katalogu workspace'ów. `operator_session` w logu
  launchera wskazuje rzeczywistego hosta workera.
- Brakujący host powstaje na żądanie; błąd tworzenia jest jawny. Workspace
  projektu i host jego workerów są różnymi miejscami.
- Sesja nie jest runem. Zamknięte okno nie dowodzi końca workera, a zielony
  raport nie dowodzi integracji commita.

Świadomy powrót:

```bash
vibecrafted dashboard ls
vibecrafted start resume
# Albo dokładna nazwa z wyniku ls:
vibecrafted dashboard switch <name>
```

**Wynik:** przełączenie lub dołączenie do wybranego workspace.
**Błąd → naprawa:** martwy host wymaga odczytu `control_plane/launches/*.log`
i zbadania konkretnego runu. Nie kasuj sesji ani nie odpalaj ponownie briefu
wyłącznie dlatego, że zniknęło okno.

<!-- Sources: vibecrafted-core/vibecrafted_core/workflow.py
     _effective_operator_session; scripts/vibecrafted cmd_start_help /
     cmd_dashboard_help. -->

## 6. Bus zdarzeń i Slack

- Zdarzenia control plane są źródłem prawdy. Serwer i MCP odczytują ich
  projekcje, m.in. `/api/control/runs`; Python pozostaje właścicielem zapisu.
- Osobne repo `vibecrafted-slack-agent` jest ustami/uchem nad tym samym stanem.
  Zielone testy nie dowodzą działającego Slacka: trzeba osobno potwierdzić
  allowlistę i świeży bridge Socket Mode. [Kontrakt gatewaya](../runtime/OMNI_OBSERVER_SLACK_GATEWAY.md).
  Kanał i credentials należą do konfiguracji wdrożenia.
- Używaj skonfigurowanego lokalnego mechanizmu credentials. Nie wypisuj sekretów
  do poleceń, transkryptów ani raportów.
- Appka macOS (`vibecrafted-app/shell-agent`) komunikuje się z runtime'em przez
  UniFFI/socket: ten sam bus, natywna powierzchnia.

## 7. Recovery po konkretnym objawie

| Objaw                                                       | Diagnoza                          | Następny ruch                                                                      |
| ----------------------------------------------------------- | --------------------------------- | ---------------------------------------------------------------------------------- |
| `process_spawned` → `stalled`, `pid_gone`, pusty transkrypt | umarł launcher lub host           | log launchera, żywotność i tożsamość runu; potem recovery hosta i resume tego runu |
| transkrypt zamarł, PID nie istnieje                         | worker umarł w trakcie            | zbadaj drzewo i raport, wznów konkretny run; zachowaj stary zapis                  |
| `ControlPlaneLockBusy` przy równoległych awaitach           | kontencja blokady                 | ponów await, nie zmieniaj stanu na podstawie samej blokady                         |
| worker zostawił niecommitowaną pracę                        | częściowy zapis w Living Tree     | ustal własność zmian, wznów z opisem stanu; cudze zmiany zostaw                    |
| raport nadpisany przez sąsiedni run                         | kolizja nazw                      | zachowaj oddzielne kopie; przekaż generatorowi pełną tożsamość promptu             |
| brak raportu, są commity                                    | uszkodzona powierzchnia artefaktu | integrator może wykonać bramki briefu; odstępstwo musi mieć zapis                  |
| potrzebna dawna sesja interaktywna                          | zachowana historia providera      | `resume <agent> --session <provider-id>`; samo AICX nie dowodzi native attach      |

```bash
vibecrafted resume codex --run-id <id> --prompt "Continue from the recorded failure"
vibecrafted resume codex --session <provider-id>
```

`--run-id` tworzy nową śledzoną kontynuację, nie restart dawnej grupy procesów.
`--session` wybiera historię providera; ID runu z control plane nie jest ID sesji
providera. Bare resume odzyskuje kontekst AICX, bez dowodu natywnego wznowienia.
Sprawdź częściowe zapisy przed kontynuacją: brief nie jest z definicji idempotentny.

Dwie reguły:

1. **Substrat:** Living Tree, Fleet Worktrees, lokalna VM i cloud VM są osobne.
   Pracuj w wybranym. Living Tree wymaga ponownego odczytu i commitowania tylko
   własnych ścieżek. Worker izolowany oddaje baton; integrator dowodzi przyjęcia.
   Sukces gościa VM nie jest integracją na hoście.
2. **Przyciski Foundera:** force-push, merge trunka, merge/close PR, deploy i
   usuwanie wymagają decyzji Foundera. Fast-forward push autorskiego commita na
   bieżącej gałęzi roboczej jest dozwolony. Operator to rola agenta;
   ludzie są Founderami.

Commit: `[<agent>/<workflow>] ...`, stopka
`Authored-By: <agent> <agents@vetcoders.io>`. Prywatny journal Operatora:
`.vibecrafted/THE_JOURNAL.md`, poza Gitem. Worker raportuje swojemu Operatorowi.

<!-- Sources: scripts/vibecrafted cmd_resume_help; AGENTS.md Runtime Topology,
     Living Tree/Fleet discipline, naming and push rule. -->

## 8. Gdy stracisz orientację

```bash
vibecrafted help --all
vibecrafted doctor
vibecrafted receipt
cat ~/.vibecrafted/START_HERE.md
```

**Wynik:** żywy deck, diagnostyka i receipt runtime'u/źródeł. Zdrowie instalacji,
pochodzenie artefaktu i odbiór sesji to osobne dowody. Na starszym Linuxie brak
Loctree/PRView po udanym npm oznacza granicę glibc. Na natywnym Windowsie jawne
odmowy POSIX są częścią kontraktu. Szczegóły i naprawy: Entry book.

<!-- Sources: scripts/vibecrafted help_all; core help_surface.py receipt;
     install-linux.yml foundation waivers; cli.py Windows declarations. -->

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
