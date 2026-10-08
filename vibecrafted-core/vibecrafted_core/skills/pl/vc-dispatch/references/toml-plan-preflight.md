# Authoring & pre-flighting a `.dispatch.toml` plan (field-learned)

Baza evidence: linia sessions-rail-live-buckets, 2026-08-09 (sekwencyjna linia
3 cięć, workerzy claude, wdrożone CLI 3.7.0). Każda reguła poniżej została
trafiona na żywo.

## Schema authority

- Referencją jest `docs/public/dispatch/dispatch-schema.md` + `--doctor`.
  Parser działa fail closed; nie pisz pól z pamięci. Waliduj przez
  `vibecrafted dispatch <plan> --doctor` ZANIM zrobisz cokolwiek innego.
- `--doctor` emituje **ostrzeżenia informacyjne** przy pinowaniu modeli („pin
  will be forwarded; provider/account availability is not validated") —
  ostrzeżenia to nie błędy; pinuj i tak wg klasy cięcia (mechaniczne → tańszy
  tier, chirurgiczne / niosące decyzję → mocny tier).

## Renderer truth (braces)

`_format_known` (`dispatch/schema.py`) podstawia **wyłącznie znane**
placeholdery `{name}` (`{repo}` `{id}` `{agent}` `{workflow}`
`{resolved_workflow}` `{reports_dir}` `{tracker}` `{baton}` — `{baton}` tylko w
promptach). Nieznane klamry przechodzą nietknięte. Konsekwencje:

- Python w stylu `env=dict()` w komendach `run` w verify jest bezpieczny; tak
  samo `{}`.
- **`{baton}` renderuje się jako JSON — wyrenderowane prompty legalnie zawierają
  klamry.** Naiwna bramka na niewyrenderowane placeholdery typu `grep -c '{'`
  daje false positive na każdym prompcie, który niesie baton. Właściwa bramka
  grepuje _znane tokeny placeholderów_ nadal obecne po renderowaniu:

  ```bash
  grep -nE '\{(repo|id|agent|workflow|resolved_workflow|reports_dir|tracker|baton)\}' \
    <reports_dir>/dry-run/prompts/*.md   # expect: no output
  ```

## Verify gates: two techniques that make them non-trivial

1. **Udowodnij, że selekcje `-k` nie są puste**, zanim linia ruszy — bramka
   matchująca 0 testów jest trywialnie zielona:

   ```bash
   uv run pytest <file> -k '<expr>' --collect-only -q   # expect ≥1 collected
   ```

2. **Verifiery z sondą semantyczną**: deterministyczna sonda `python -c`, która
   dziś zwraca STARĄ wartość i MUSI zwrócić NOWĄ po cięciu. Zrób pre-flight na
   żywo: dzisiejszy output dowodzi, że komenda jest składniowo poprawna ORAZ że
   bramka nie może przejść bez wylądowania pracy. Przykładowa para z linii
   sessions-rail:

   ```toml
   [[cuts.verify]]
   run = '''cd {repo} && uv run python -c "from vibecrafted_core.workflow import _effective_operator_session as f; print(f(root='/x/demo', run_id='r', env=dict()))"'''
   expect = { equals = "demo workers", exit_code = 0 }   # today prints "demo"
   ```

   Przy sondach wrażliwych na env wymuś non-TTY przez `</dev/null` i wstrzyknij
   env inline (`env KEY=val …`), żeby sonda była hermetyczna.

## TOML escaping

- Komendy `run` w verify mieszające apostrofy i cudzysłowy: użyj wieloliniowych
  stringów literalnych `'''…'''` (w jednej linii też działa) — zero escapowania.
- Trzymaj **prompty** wolne od klamer poza prawdziwymi placeholderami; kod z
  klamrami umieszczaj tylko w komendach `run` (renderer przepuszcza je bez
  zmian).

## Dry-run layout

`--dry-run` zapisuje pod `reports_dir/dry-run/`: `prompts/<cut-id>.md`,
`tracker.md`, `validated-dispatch.toml`, `dispatch-result.json`. Obejrzyj
wyrenderowane prompty (bramka na placeholdery wyżej) przed prawdziwym launchem.

## Deployed CLI vs checkout (push ≠ install, line edition)

Supervisor i jego workerzy działają z **wdrożonego tools home**
(`vibecrafted --version` → `X.Y.Z+g<sha>`), a nie z checkoutu, który cięcia
edytują. Linia, której cięcia zmieniają zachowanie runtime'u/dispatchu, NIE
zmienia zachowania tej samej linii, która ją wykonuje — spodziewaj się starego
zachowania przez cały lot, a `make install` zostaw jako poliniowy guzik
operatora. Wniosek: cięcie może w locie w sposób jawny _reprodukować_ bug, który
naprawia.

## Selected substrate and owned staging

Zadeklaruj runtime wybrany przez Foundera/plan/launcher w `[common]`: Living Tree /
`local-native`, Fleet Worktrees / `local-worktrees`, Fleet VM local lub Fleet VM
cloud. Zachowaj jawny wybór; nie wnioskuj go ze starych worktree.
Przy typowanym dispatchu supervisor przydziela każdemu nie-integratorowi dedykowany
worktree i gałąź `cut/<cut-id>` z rozwiązanego baseline'u. `{repo}` wskazuje checkout
workera, nie nadrzędny Living Tree. Workerzy nie tworzą kolejnej gałęzi/worktree
ani nie integrują siebie. Użyj `base = "cut:<cut-id>"` z pasującą zależnością,
gdy cut potrzebuje bajtów poprzednika, nie tylko kolejności.

Nazwij współbieżną pracę i nietykalne ścieżki; czytaj ponownie przed edycją.
Stage'uj wyłącznie własne pliki/hunki przez `git add -- <owned-path>` albo selekcję
hunków. Nigdy sweep przez `git add -A` lub `git add .`, stash/discard ani commit
cudzych zmian. Gdy overlap uniemożliwia uczciwą izolację, zachowaj diff i zgłoś granicę.

## Launch shape

```bash
bash -c 'ulimit -f unlimited; exec vibecrafted dispatch <plan> --json'   # detached/background
```

Receipt = tracker napisany przez supervisora i control-plane run_id plus runtime
class, parent/effective roots, baseline branch/full SHA, worker branch i ścieżki
artefaktów. Artefakty żyją pod `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/artifacts`;
zachowaj maszynową tożsamość raportu launchera. Uzbrój supervisor-side
`vibecrafted await <agent> --run-id <id>` natychmiast; artefakty są diagnostyczne,
nie są sygnałem wybudzenia. Bez gapienia się w pane i hedge pollerów. Headless
workerzy kończą bramki na pierwszym planie, raport i commit przed końcem tury;
nie mogą czekać na wybudzenie przez zadanie w tle. Piny jadą dosłownie;
niedostępne wymagają uczciwej porażki, nigdy cichej podmiany modelu.

## Substrate contract pair + recovery (field-learned, flights 2–4)

Cięcia WRITE z `require_commit` siedzą między **dwiema symetrycznymi bramkami**
(`dispatch/supervisor.py::_run_cut`): cięcie odmawia STARTU z dirty worktree i
odmawia ZAKOŃCZENIA z niezacommitowanymi zmianami. Worker, który edytuje,
przechodzi weryfikację, a potem umiera przed commitem (gate-nap: czekanie na
Monitor/wakeup zamiast commitowania — Klasa 3, `AGENT_OPS.md`), zakleszcza więc
linię: osierocona dostawa blokuje każdy refire.

- **Recovery dyspozytora**: zrób review osieroconego diffa względem briefu
  (osierocony często dowozi), zacommituj go sam z id cięcia w tytule i
  pochodzeniem osieroconego runu w body, DOPIERO potem wznów. Nigdy nie
  wyrzucaj.
- **`repair_rounds` nie odpala** przy `CellContractError` — repair pokrywa
  czerwone verifiery, nie złamania kontraktu podłoża.
- **Resume z trwałych dowodów**: aktualny typowany dispatch używa receiptów
  i ancestry Gita; brak katalogu lub pasujący temat commita nie dowodzi admission.
  Operator zapisuje istotne recovery w ignored
  `<repo-root>/.vibecrafted/THE_JOURNAL.md`; workerzy zwracają raporty i nigdy
  nie piszą tego journala. Zachowaj dostarczone SHA oraz integration disposition.

- **Idempotentny settle wymaga jawnego dowodu**
  (`supervisor.py::_existing_delivery_commit`): worker, który zastaje pracę już
  wylądowaną, musi umieścić w raporcie samodzielną linię `commit: <sha>`;
  supervisor przyjmuje ją tylko wtedy, gdy sha się rozwiązuje, jest przodkiem
  HEAD, a wiadomość commita identyfikuje cięcie (trzymaj `[<cut-id>]` w tytułach
  commitów dostawczych). Wpisz tę klauzulę do `[common]` od początku —
  „nothing to do" bez linii dowodowej to złamanie kontraktu.

Zmiany bajtów skillów regenerują `vibecrafted-core/vibecrafted_core/skills/SKILL_PROVENANCE.json`
przez `make skills-check UPDATE=1` (właściciel: `scripts/gen_skill_provenance.py`).
Szanuj osobny cut generatora i zachowaj historyczne wpisy. Integrator regeneruje
wspólny manifest po admission; source-green workera nigdy nie twierdzi, że dowodzi
instalacji, live acceptance lub zgody Foundera.
