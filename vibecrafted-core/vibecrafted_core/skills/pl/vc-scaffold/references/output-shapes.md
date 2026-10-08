# Output shapes — one gate, three shapes by scale

Scaffold to jedna bramka z trzema kształtami wyjścia wybieranymi wg scope'u. Wybierz najmniejszy, który pasuje; nie
emituj wave-atlasu dla pojedynczego cięcia ani pojedynczego briefu dla całego projektu. Każdy kształt
nadal emituje obowiązkową parę `SCAFFOLD.md` + `<plan-id>.dispatch.toml`; skala zmienia artefakty
pomocnicze, nigdy kontrakt wykonania czytelny dla supervisora.

Używaj kanonicznego root planu pod
`${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<plan-id>/`.
Każdy scaffold zachowuje `manifest.json`, DRIVER, briefy per cięcie i walidację
scaffold-doctor. `vc-ship` jest normalną klamrą lifecycle; **ograniczony dispatch
zarządzony przez Foundera** może przekazać ten sam zwalidowany TOML do
`vibecrafted dispatch <plan>` bez wymagania wszystkich etapów lifecycle.

## 1. Single cut → one brief

Jeden `SCAFFOLD.md` plus jednocięciowy `<plan-id>.dispatch.toml` (zobacz `plan-template.md`). Jeden Vector, garść cięć, każde z
kolumną `state` i delivery-verifierem. Tracker niepotrzebny.

## 2. Multiple cuts → wave-atlas + briefs + tracker

- **Atlas** (`00_ATLAS.md`): mapa fal — czym jest każda fala, zależności, faza cadence, którą każda
  zajmuje, oraz inwarianty cross-wave (bezpieczeństwo hosta, kontrakty, mina cadence).
- **Briefy per fala** (12-sekcyjny szablon dispatchu, poniżej), jeden na falę.
- **Tracker** (`tracker.md`): tabela statusu fal z kolumną `state`, run_id, baseline SHA, commit
  SHA, bramka, raport — widoczność-przez-artefakty dla nieobecnego Operatora.
- **Dispatch** (`<plan-id>.dispatch.toml`): pełny DAG cięć, dozwolona równoległość, ścieżki briefów
  i bramki verifierów przekazywane do `/vc-ship` i konsumowane przez jego deterministyczny dispatcher.

## 3. Whole project → read/write pipeline with phases

Pełen cadence VC-ship (`cadence.md`): Scaffold→Implement→Review→…→Release, każda faza to WRITE albo
READ, każda zostawia artefakt, który konsumuje następna. Plan deklaruje łańcuch faz, profile bramek
per Vector oraz recovery-vectory dla stanów STOP.

## 12-section dispatch brief template (per wave / per agent)

```markdown
---
prompt_id: <slug>
plan_id: <plan-id>
session_id: <session-id>
role: brief
agent: <claude|codex|gemini|cursor>
date: <YYYY-MM-DD>
project: <org>/<repo>
skill: <vc-implement|...>
model: <explicit-pin>
wave: <Wn>
target_repo: <repo>
runtime: <selected-runtime>
baseline_branch: <assigned-branch>
baseline_sha: <full-sha>
authored_by: <agent> <agents@vetcoders.io>
report_path: <launcher-supplied-path>
vector: <stabilize|implement|recon|e2e>
---

# <Wn> — <title>

## 1. Identity (agent/model pin, selected runtime, parent/effective roots, baseline branch/full SHA)

## 2. Mission (one paragraph: the WRITE this wave delivers)

## 3. Context (read-before-editing: files, contracts, landmines)

## 4. Files to create/edit (+ "Do not edit" list)

## 5. Acceptance (each item carries state [ ]/[~]/[?]/[!]/[x] + a delivery-verifier)

## 6. Verification / Gates (exact commands, non-empty counts, red-before/green-after, source vs live evidence)

## 7. Out of scope

## 8. Living Tree etiquette / selected substrate (honor local-worktrees or local-native; inherit supervisor cut/<cut-id>; re-read; stage owned hunks; halt on overlap)

## 9. Loctree first (context → slice/impact → find --literal; grep only on loct-miss + hak)

## 10. Recovery hint (substrate stall vs scope stall → what artifact to leave, what exit code)

## 11. Branch + commit ([<cut-id>] [<agent>/<workflow>] title; Authored-By; owned staging; current wave push fence; Founder buttons)

## 12. Report (launcher-supplied path/identity; terminal SHA; foreground completion; Worker claims vs Operator/Founder approval; installed/live residuals)
```

### Files section: new vs existing paths

C4 (`named_path_missing`) wymaga, żeby każda ścieżka z sekcji Files istniała na HEAD. Gdy cięcie
**tworzy** plik, którego jeszcze nie ma na HEAD, dopisz sufiks ` (new)` albo ` (nowy)` po ścieżce:

```markdown
- `tests/x_new.py` (new)
- `src/foo.rs` (nowy)
```

scaffold-doctor wtedy sprawdza, że **katalog nadrzędny** istnieje na HEAD, a nie sam plik.
Literówka w katalogu nadal wpada jako `named_path_parent_missing`. Nie oznaczaj tak edycji
istniejących plików — nieoznaczona brakująca ścieżka nadal kończy się `named_path_missing`.

## tracker.md schema

```markdown
| Wave | Plan file | Agent | Depends | state | run_id | baseline SHA | commit SHA | Gate    | Report |
| ---- | --------- | ----- | ------- | ----- | ------ | ------------ | ---------- | ------- | ------ |
| W0   | 10_W0.md  | codex | —       | [ ]   | —      | —            | —          | ☐ build | —      |
```

legenda state: `[ ]` pending · `[~]` claimed · `[?]` unknown/unverifiable · `[!]` refuted · `[x]` delivered.
Recovery log dopisuje zdarzenia substrate-failure / scope-overflow / wrong-cut z falą + run_id + ścieżką artefaktu.
Tylko Operator dopisuje istotne decyzje do ignored
`<repo-root>/.vibecrafted/THE_JOURNAL.md`; workerzy zwracają raporty i nie piszą go.
Nie twórz ani nie trackuj wycofanego `.vibecrafted/JOURNAL.md`.

Pola Acceptance workera są claimami; tylko evidence verifierów przesuwa tracker.
Izolowany commit nie jest integracją ani installed/live acceptance. Zapisz dokładny
admission disposition i sprawdzony stan celu. Nigdy nie podpisuj pól Operatora ani
Foundera za workera. Uzbrój supervisor-side await po launchu; headless workerzy
kończą bramki na pierwszym planie, raport i commit przed końcem tury. Zachowaj
jawne piny; bez cichej podmiany modelu.

Przyciski Foundera: trunk merge, force-push, PR merge/close, kasowanie tagów/gałęzi
oraz deploy. Fast-forward push własnej feature branch jest dozwolony, chyba że
bieżąca fala go zabrania. Install i czynności cross-boundary podlegają jawnemu
kontraktowi zgód repo/plan. Zmiany bajtów skillów regenerują
`skills/SKILL_PROVENANCE.json` przez istniejący generator
(`scripts/gen_skill_provenance.py`, `make skills-check UPDATE=1`); szanuj własność
cuta generatora. Integrator regeneruje wspólną historię po admission.
