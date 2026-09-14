# Kształty wyjścia — jedna bramka, trzy kształty wg skali

Scaffold to jedna bramka z trzema kształtami wyjścia wybieranymi wg scope'u. Wybierz najmniejszy, który pasuje; nie
emituj wave-atlasu dla pojedynczego cięcia ani pojedynczego briefu dla całego projektu. Każdy kształt
nadal emituje obowiązkową parę `SCAFFOLD.md` + `<plan-id>.dispatch.toml`; skala zmienia artefakty
pomocnicze, nigdy kontrakt wykonania czytelny dla supervisora.

## 1. Pojedyncze cięcie → jeden brief

Jeden `SCAFFOLD.md` plus jednocięciowy `<plan-id>.dispatch.toml` (zobacz `plan-template.md`). Jeden Vector, garść cięć, każde z
kolumną `state` i delivery-verifierem. Tracker niepotrzebny.

## 2. Wiele cięć → wave-atlas + briefy + tracker

- **Atlas** (`00_ATLAS.md`): mapa fal — czym jest każda fala, zależności, faza cadence, którą każda
  zajmuje, oraz inwarianty cross-wave (bezpieczeństwo hosta, kontrakty, mina cadence).
- **Briefy per fala** (12-sekcyjny szablon dispatchu, poniżej), jeden na falę.
- **Tracker** (`tracker.md`): tabela statusu fal z kolumną `state`, run_id, baseline SHA, commit
  SHA, bramka, raport — widoczność-przez-artefakty dla nieobecnego operatora.
- **Dispatch** (`<plan-id>.dispatch.toml`): pełny DAG cięć, dozwolona równoległość, ścieżki briefów
  i bramki verifierów przekazywane do `/vc-ship` i konsumowane przez jego deterministyczny dispatcher.

## 3. Cały projekt → pipeline read/write z fazami

Pełen cadence VC-ship (`cadence.md`): Scaffold→Implement→Review→…→Release, każda faza to WRITE albo
READ, każda zostawia artefakt, który konsumuje następna. Plan deklaruje łańcuch faz, profile bramek
per Vector oraz recovery-vectory dla stanów STOP.

## 12-sekcyjny szablon briefu dispatchu (per fala / per agent)

```markdown
---
prompt_id: <slug>
agent: <claude|codex|gemini|cursor>
skill: <vc-implement|...>
wave: <Wn>            target_repo: <repo>      baseline_branch: <living-tree>
authored_by: <agent> <agents@vetcoders.io>     report_path: <path>
vector: <stabilize|implement|recon|e2e>
---

# <Wn> — <title>

## 2. Mission (one paragraph: the WRITE this wave delivers)

## 3. Context (read-before-editing: files, contracts, landmines)

## 4. Files to create/edit (+ "Do not edit" list)

## 5. Acceptance (each item carries state [ ]/[~]/[?]/[!]/[x] + a delivery-verifier)

## 6. Gates (the exact commands that flip [~]→[x])

## 7. Out of scope

## 8. Living Tree etiquette (re-read before edit; append-only shared files; halt on substrate failure)

## 9. Loctree first (context → slice/impact → find --literal; grep only on loct-miss + hak)

## 10. Recovery hint (substrate stall vs scope stall → what artifact to leave, what exit code)

## 11. Branch + commit ([<agent>/<workflow>] title; Authored-By; NO push/PR — operator owns)

## 12. Report (sections + honest handoff: proven [x] vs runtime-pending [?])
```

## Schemat tracker.md

```markdown
| Wave | Plan file | Agent | Depends | state | run_id | baseline SHA | commit SHA | Gate    | Report |
| ---- | --------- | ----- | ------- | ----- | ------ | ------------ | ---------- | ------- | ------ |
| W0   | 10_W0.md  | codex | —       | [ ]   | —      | —            | —          | ☐ build | —      |
```

legenda state: `[ ]` pending · `[~]` claimed · `[?]` unknown/unverifiable · `[!]` refuted · `[x]` delivered.
Recovery log dopisuje zdarzenia substrate-failure / scope-overflow / wrong-cut z falą + run_id + ścieżką artefaktu.
Punkty stop (operator-owned): push / PR / install / edycje cross-boundary.
