# Output shapes — one gate, three shapes by scale

Scaffold is one gate with three output shapes chosen by scope. Pick the smallest that fits; do not
emit a wave-atlas for a single cut, nor a single brief for a whole project. Every shape still emits
the mandatory pair `SCAFFOLD.md` + `<plan-id>.dispatch.toml`; scale changes supporting artifacts,
never the supervisor-readable execution contract.

Use the canonical plan root under
`${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<plan-id>/`.
Every scaffold retains `manifest.json`, DRIVER, per-cut briefs and scaffold-doctor validation.
`vc-ship` is the normal lifecycle umbrella; a **Founder-ordered bounded dispatch** may hand
the same validated TOML to `vibecrafted dispatch <plan>` without requiring all lifecycle stages.

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

C4 (`named_path_missing`) requires every Files-section path to exist on HEAD. When the cut
**creates** a file that is not on HEAD yet, suffix the path with ` (new)` or ` (nowy)`:

```markdown
- `tests/x_new.py` (new)
- `src/foo.rs` (nowy)
```

scaffold-doctor then checks that the **parent directory** exists on HEAD instead of the file.
A typo in the directory still fails, as `named_path_parent_missing`. Do not mark edits to
existing files this way — unmarked missing paths still fail as `named_path_missing`.

## tracker.md schema

```markdown
| Wave | Plan file | Agent | Depends | state | run_id | baseline SHA | commit SHA | Gate    | Report |
| ---- | --------- | ----- | ------- | ----- | ------ | ------------ | ---------- | ------- | ------ |
| W0   | 10_W0.md  | codex | —       | [ ]   | —      | —            | —          | ☐ build | —      |
```

state legend: `[ ]` pending · `[~]` claimed · `[?]` unknown/unverifiable · `[!]` refuted · `[x]` delivered.
Recovery log appends substrate-failure / scope-overflow / wrong-cut events with wave + run_id + artifact path.
The Operator alone appends material decisions to ignored
`<repo-root>/.vibecrafted/THE_JOURNAL.md`; workers return reports and do not write it.
Do not create or track the retired `.vibecrafted/JOURNAL.md`.

Worker Acceptance boxes are claims; only verifier evidence advances the tracker. An isolated
commit is not integration or installed/live acceptance. Record exact admission disposition
and checked destination state. Never sign Operator or Founder boxes on a worker's behalf.
Arm supervisor-side await after launch; headless workers finish foreground gates, report and
commit before ending the turn. Preserve explicit pins; no silent model substitution.

Founder buttons: trunk merge, force-push, PR merge/close, tag/branch deletion and deploy.
An authored feature-branch fast-forward push is permitted unless the current wave forbids it.
Install and cross-boundary actions follow the explicit repository/plan approval contract.
Skill-byte changes regenerate `skills/SKILL_PROVENANCE.json` through its existing generator
(`scripts/gen_skill_provenance.py`, `make skills-check UPDATE=1`); respect generator-cut
ownership. The integrator regenerates combined history after admission.
