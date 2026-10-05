# vc-agents — runbook launcherów (bieżący deck)

Operatorską ścieżką uruchamiania jest deck komend `vibecrafted` (lub helper
`vc-<launcher>`). Agenci: `claude · codex · agy · junie · grok ·
cursor · kimi · copilot`. Pełna powierzchnia flag, nie tylko `--prompt`:

```bash
vibecrafted <launcher> <agent> \
  --repo "$(pwd)" \                 # target checkout (worktree parent)
  --model <id> \                    # Founder's cost button — never substitute
  --effort <low|medium|high|xhigh> \# per-provider mapping; receipted skip where unsupported
  --worktree true \                 # Fleet Worktree mode (isolated branch + worktree)
  --permissions <bypass|auto|accept-edits|read-only> \
  --prompt "..."                    # or --file /path/to/plan.md
```

**Kompozycja promptu to idiom powłoki, nie string.** Składaj prompt z żywego
wyjścia komend, aby worker dostał prawdę gruntu zamiast zadania domowego:

```bash
# plan-from-file with a text prefix (frontmatter at prompt start trips the
# provider-conflict guard — always prefix):
vibecrafted workflow grok --repo "$(pwd)" --model grok-4.7-build-fast \
  --worktree true \
  --prompt "Implementation plan follows. Execute it end to end.

$(cat "$PLAN")"

# inject a living CLI's --help straight into the mission:
vibecrafted workflow codex --repo "$(pwd)" --model gpt-6-sol --worktree true \
  --prompt "$(echo 'Zaprojektuj i wdroż obsługę copilot CLI zgodnie z:' \
             && echo && copilot --help \
             && echo 'Agent ma być wszędzie wpisem kolejnym po kimi.')"
```

Przy dispatchu z wnętrza sesji Claude wyczyść odziedziczone env sesji, inaczej
dziecko błędnie przypisze atrybucję:

```bash
env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION \
    -u CLAUDE_CODE_MESSAGING_SOCKET -u CLAUDE_CODE_MESSAGING_TOKEN \
    -u CLAUDE_PID -u CLAUDE_JOB_DIR \
  zsh -lc 'vibecrafted workflow <agent> ...'
```

Czasowniki towarzyszące: `vibecrafted await <agent> --run-id <id>` (uzbrój
natychmiast), `observe` (ogon transkryptu), `stop` (TERM po grupie procesów),
`usage [--run-id ...]` (tokeny per run + koszt raportowany przez providera —
cytuj go przy każdym settle). Jeśli tych narzędzi brak, przestań udawać, że spawn
jest poprawnie skonfigurowany, i powiedz to wprost.

## Konwencja wyjść

- Plany: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<timestamp>_<slug>.md` lub inna stabilna
  nazwa per zadanie
- Raporty: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.md`
- Transkrypty: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.transcript.log`
- Metadane: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.meta.json`

Każdy spawn powinien pokazać kartę startową zaraz po dispatchu.
Karta ma ujawniać co najmniej:

- `run_id`
- wybranego agenta / rodzinę modelu
- ścieżkę planu
- ścieżkę raportu
- ścieżkę transkryptu
- ścieżkę metadanych
- dokładną komendę await

Jeśli operator nie widzi tych ścieżek, obserwowalność jest niepełna, nawet gdy
agent technicznie działa.

## Obserwacja

Kanoniczny kontrakt supervisora (zob. `docs/runtime/AGENT_OPS.md`): po
dispatchu uzbrój `vibecrafted await <agent> --run-id <id>` natychmiast, po
stronie supervisora. JSON control-plane, pliki raportów, transkrypty, panele i
zaplanowane wybudzenia są wyłącznie diagnostyczne, nie są sygnałami wybudzenia.
Zabezpieczanie awaita doraźnymi pollerami/watcherami to naruszenie Klasy 3;
napraw `control_plane.await_run`, nie normalizuj zabezpieczenia.

Żywotność po 3 sygnałach: werdykt await, terminalne meta runu, martwy pid
workera oraz obecność obiecanego raportu. Dwa zgodne sygnały wystarczą do
działania, trzy do ogłoszenia końca; każda niezgodność oznacza: traktuj jako
żywy i uzbrój await ponownie. Znany skos: rc=0 przy żywym runie oraz meta
zawieszone na `active`/`stalled` po faktycznym zakończeniu.

Obserwuj postęp przez trwałe artefakty w
`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/`, ale czekanie
i końcowe podsumowanie zostaw dedykowanemu helperowi runtime:

```bash
vibecrafted await codex --run-id <run_id>
```

Dla ostatniego runu danego agenta:

```bash
vibecrafted await codex --last
```

Dla wielu uruchomionych workerów przekaż helperowi bezpośrednio ścieżki
launcherów lub metadanych i pozwól mu czekać na wszystkich naraz.

Jeśli środowisko udostępnia helper obserwatora, użyj go do inspekcji na poziomie
transkryptu lub debugowania:

```bash
vibecrafted observe codex --last
```

W razie potrzeby użyj odpowiedniego obserwatora agenta, ale nie polegaj na
`observe` jako jedynej powierzchni statusu. `vc-agents` ma pozostać operowalne
z trwałych artefaktów także wtedy, gdy operator nie patrzy na żywe panele.

## Pętla integratora snap-dispatch (wzorzec Foundera, 2026-10-03)

Najwydajniejszy zmierzony w praktyce wzorzec operatora: integrator pisze gęsty
plan, odpala JEDEN launcher na cięcie i bramkuje powrót. Szybkość bierze się z
JAKOŚCI PLANU, nie z ceremonii:

1. **Plan niesie zmierzone dowody, nie zadanie domowe.** Dokładne ścieżki
   store'ów, kształty rekordów, liczby kontrolne, kotwice kodu z numerami linii —
   wszystko, co integrator już sprawdził. Worker nie traci minut na ponowne
   odkrywanie formatów. (Zmierzony efekt: cięcie telemetrii sześciu silników
   weszło w jednym runie.)
2. **Kanon Foundera jest oznaczony jako wiążący.** Decyzje projektowe odzyskane
   z AICX trafiają do planu pod jawnym nagłówkiem „CANON/INWARIANT — implement
   exactly, never reinterpret". Prefiks promptu dispatchu powtarza go.
3. **Jeden snap, jedno cięcie**: `vibecrafted workflow <agent> --model <founder's
cost pick> --worktree true --prompt "Implementation plan follows. …
$(cat plan.md)"` — tekstowy prefiks PRZED treścią planu (frontmatter na
   początku promptu uruchamia guard konfliktu providera), env oczyszczone ze
   zmiennych sesji CLAUDE_*. Uzbrój `vibecrafted await` natychmiast.
4. **Bramka integratora na powrocie**: sam uruchom ponownie testy workera w jego
   worktree (czysty PATH), przetestuj na żywo powierzchnię produktu binarką z
   worktree i rozlicz każdy czerwony test przez BASELINE-DIFF na czystym HEAD,
   zanim komuś przypiszesz winę (czerwień zastana ≠ czerwień cięcia). Merge
   `--no-ff`, push, raportuj koszt raportowany przez providera przy każdym settle.
5. **Model i effort to guziki kosztowe Foundera** — nigdy nie podmieniaj ich w
   locie; 400 od providera oznacza STOP i pytanie, nie zamianę.
