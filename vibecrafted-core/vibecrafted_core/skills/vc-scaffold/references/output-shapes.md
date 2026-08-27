# Output shapes — one gate, three shapes by scale

Scaffold is one gate with three output shapes chosen by scope. Pick the smallest that fits; do not
emit a wave-atlas for a single cut, nor a single brief for a whole project. Every shape still emits
the mandatory pair `SCAFFOLD.md` + `<plan-id>.dispatch.toml`; scale changes supporting artifacts,
never the supervisor-readable execution contract.

## 1. Single cut → one brief

A single `SCAFFOLD.md` plus its one-cut `<plan-id>.dispatch.toml` (see `plan-template.md`). One Vector, a handful of cuts, each with a
`state` column and a delivery-verifier. No tracker needed.

## 2. Multiple cuts → wave-atlas + briefs + tracker

- **Atlas** (`00_ATLAS.md`): the wave map — what each wave is, dependencies, the cadence phase each
  occupies, and the cross-wave invariants (host safety, contracts, the cadence landmine).
- **Per-wave briefs** (12-section dispatch template, below), one per wave.
- **Tracker** (`tracker.md`): wave status table with the `state` column, run_id, baseline SHA, commit
  SHA, gate, report — visibility-through-artifacts for the absent operator.
- **Dispatch** (`<plan-id>.dispatch.toml`): the complete cut DAG, allowed concurrency, brief paths,
  and verifier gates handed to `/vc-ship` and consumed by its deterministic dispatcher.

## 3. Whole project → read/write pipeline with phases

The full VC-ship cadence (`cadence.md`): Scaffold→Implement→Review→…→Release, each phase a WRITE or
READ, each leaving an artifact the next consumes. The plan declares the phase chain, the gate profiles
per Vector, and the recovery-vectors for STOP states.

## 12-section dispatch brief template (per wave / per agent)

```markdown
---
prompt_id: <slug>
agent: <claude|codex|gemini>
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

## tracker.md schema

```markdown
| Wave | Plan file | Agent | Depends | state | run_id | baseline SHA | commit SHA | Gate    | Report |
| ---- | --------- | ----- | ------- | ----- | ------ | ------------ | ---------- | ------- | ------ |
| W0   | 10_W0.md  | codex | —       | [ ]   | —      | —            | —          | ☐ build | —      |
```

state legend: `[ ]` pending · `[~]` claimed · `[?]` unknown/unverifiable · `[!]` refuted · `[x]` delivered.
Recovery log appends substrate-failure / scope-overflow / wrong-cut events with wave + run_id + artifact path.
Stop points (operator-owned): push / PR / install / cross-boundary edits.
