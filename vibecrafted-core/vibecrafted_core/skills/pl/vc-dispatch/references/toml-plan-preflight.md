# Pisanie i pre-flight planu `.dispatch.toml` (wyuczone w polu)

Baza evidence: linia sessions-rail-live-buckets, 2026-08-09 (sekwencyjna linia
3 cięć, workerzy claude, wdrożone CLI 3.7.0). Każda reguła poniżej została
trafiona na żywo.

## Autorytet schematu

- Referencją jest `docs/public/dispatch/dispatch-schema.md` + `--doctor`.
  Parser działa fail closed; nie pisz pól z pamięci. Waliduj przez
  `vibecrafted dispatch <plan> --doctor` ZANIM zrobisz cokolwiek innego.
- `--doctor` emituje **ostrzeżenia informacyjne** przy pinowaniu modeli („pin
  will be forwarded; provider/account availability is not validated") —
  ostrzeżenia to nie błędy; pinuj i tak wg klasy cięcia (mechaniczne → tańszy
  tier, chirurgiczne / niosące decyzję → mocny tier).

## Prawda renderera (klamry)

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

## Bramki verify: dwie techniki, które czynią je nietrywialnymi

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

## Escapowanie w TOML

- Komendy `run` w verify mieszające apostrofy i cudzysłowy: użyj wieloliniowych
  stringów literalnych `'''…'''` (w jednej linii też działa) — zero escapowania.
- Trzymaj **prompty** wolne od klamer poza prawdziwymi placeholderami; kod z
  klamrami umieszczaj tylko w komendach `run` (renderer przepuszcza je bez
  zmian).

## Układ dry-run

`--dry-run` zapisuje pod `reports_dir/dry-run/`: `prompts/<cut-id>.md`,
`tracker.md`, `validated-dispatch.toml`, `dispatch-result.json`. Obejrzyj
wyrenderowane prompty (bramka na placeholdery wyżej) przed prawdziwym launchem.

## Wdrożone CLI vs checkout (push ≠ install, odsłona liniowa)

Supervisor i jego workerzy działają z **wdrożonego tools home**
(`vibecrafted --version` → `X.Y.Z+g<sha>`), a nie z checkoutu, który cięcia
edytują. Linia, której cięcia zmieniają zachowanie runtime'u/dispatchu, NIE
zmienia zachowania tej samej linii, która ją wykonuje — spodziewaj się starego
zachowania przez cały lot, a `make install` zostaw jako poliniowy guzik
operatora. Wniosek: cięcie może w locie w sposób jawny _reprodukować_ bug, który
naprawia.

## Klauzula współbieżności Living Tree

Gdy inne sesje edytują ten sam checkout w trakcie linii, napisz to wprost w
`[common]`: nazwij równoległą pracę, wymagaj ponownego przeczytania bieżącego
stanu każdego pliku przed edycją i zaznacz, że zgarnianie przez `git add -A` to
zacommitowanie, nie zniszczenie. Workerzy nie mogą „chronić" się worktree'ami
ani przełączaniem branchy.

## Kształt launchu

```bash
bash -c 'ulimit -f unlimited; exec vibecrafted dispatch <plan> --json'   # detached/background
```

Receipt = `tracker.md` napisany przez supervisora (jeden pisarz, baseline branch

- head) plus run_id pierwszego workera w control plane. Potem spanko: czekaj
  przez artefakty i notyfikację task/await — żadnego gapienia się w pane, żadnych
  asekuracyjnych pollerów.

## Para kontraktów podłoża + recovery (wyuczone w polu, loty 2–4)

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
- **Nieudany resume zaorywa tracker**: każdy run dispatchu przepisuje
  `tracker.md` na starcie, więc resume, który umiera na bramce podłoża, kasuje
  wcześniejsze stany `[x]`, a KOLEJNY resume startuje od pierwszego cięcia.
  Trzymaj SHA dostarczonych commitów w journalu/notatkach — będą potrzebne.
- **Idempotentny settle wymaga jawnego dowodu**
  (`supervisor.py::_existing_delivery_commit`): worker, który zastaje pracę już
  wylądowaną, musi umieścić w raporcie samodzielną linię `commit: <sha>`;
  supervisor przyjmuje ją tylko wtedy, gdy sha się rozwiązuje, jest przodkiem
  HEAD, a wiadomość commita identyfikuje cięcie (trzymaj `[<cut-id>]` w tytułach
  commitów dostawczych). Wpisz tę klauzulę do `[common]` od początku —
  „nothing to do" bez linii dowodowej to złamanie kontraktu, a idempotentny
  resume całej linii jest też czystym sposobem na ponowne zasettlowanie
  zaoranego trackera.
