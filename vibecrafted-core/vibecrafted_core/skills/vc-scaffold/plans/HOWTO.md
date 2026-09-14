# vc-scaffold — `plans/HOWTO.md`: MASTER + plans + TRACKER Convention

> When the scaffolded idea is too big for one prompt, it becomes a plan.
> When a plan is too big for one agent, it becomes a wave-shaped dispatch
> chain. This HOWTO codifies the artifact layout that connects
> `vc-scaffold` (brainstorm → plan) with `vc-operator` (plan → dispatch
> → close-out).

Read alongside the [vc-scaffold SKILL](../SKILL.md), [vc-operator EMIL](../../vc-operator/EMIL.md),
[vc-operator GUIDE](../../vc-operator/GUIDE.md),
[vc-operator DISPATCH](../../vc-operator/DISPATCH.md).

---

## 1) The manifest-backed plan root

Every robust plan ships as one canonical directory with an explicit manifest:

| Artifact     | Filename                          | Role                                                                        |
| ------------ | --------------------------------- | --------------------------------------------------------------------------- |
| **MANIFEST** | `manifest.json`                   | Ordered IDs, explicit roles, paths, editability, requiredness, dependencies |
| **DRIVER**   | `DRIVER.md`                       | Cold-operator executable handoff                                            |
| **MASTER**   | `00_ATLAS.md`                     | Atlas: why-we-build + wave structure + dispatch protocol + recovery rules   |
| **plans/**   | `01-<slug>.md` … `0N-<slug>.md`   | One Iter-3 prompt body per dispatched prompt                                |
| **TRACKER**  | `tracker.md` (single living file) | Append-only wave-by-wave checkbox state                                     |

All three live under the default artifact path:

```text
~/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<plan_id>/
├── manifest.json
├── DRIVER.md
├── 00_ATLAS.md
├── briefs/01-<wave>-<slug>.md
├── briefs/02-<wave>-<slug>.md
├── ...
├── briefs/0N-<wave>-<slug>.md
└── tracker.md
```

`manifest.json` is mandatory and is the only inventory. Roles are never inferred from filenames.
Do not create an `operator/` mirror, duplicate, or symlink.

The numbering is dispatch-order (top to bottom = fire order). The filename
slug carries the wave letter so `ls` is human-scannable.

---

## 2) MASTER atlas shape

```markdown
# Plan: <Title> — <one-line tagline>

> Captured <YYYY-MM-DD>. <Operator-voice paragraph: why the current shape
> fails, what the target shape is, why now.>

Reference baseline branch: `<branch>@<sha>`.
Dispatch target: `vc-operator` on `<host>`.
Mandate: `<skill>` for every prompt — `<one-line scope-bound>`.

## 1) Why the current shape fails (1:1)

[Operator's spoken/typed diagnosis, verbatim where possible.]

## 2) Target shape

[ASCII diagram of the end-state architecture / layout / flow.]

## 3) Reusable pieces from the existing tree

| Surface | Reuse from | Notes |
| ------- | ---------- | ----- |
| ...     | ...        | ...   |

## 4) Out of scope for this plan

- [ ] [explicitly NOT in scope item 1]
- [ ] [explicitly NOT in scope item 2]

## 5) N prompts for `<dispatcher>`

[Brief outline — one section per prompt, plus link to its
`0N-<slug>.md` body file.]

### Prompt 1 — `<slug>` (`<wave>`)

**Mission**: [one paragraph].
**Files**: see `01-<slug>.md`.
**Agent**: `<recommended-agent>`.
**Acceptance bar**: [one-line summary].

### Prompt 2 — `<slug>` (`<wave>`)

...

## 6) Dispatch order + dependencies

[Mermaid graph or ASCII tree showing wave structure and the
sequential/parallel arrows.]

## 7) Operator handoff

[One paragraph describing how the operator hands the plan to the
operator-agent: which file to pass via `--file`, which trigger to use,
who pushes the resulting branches.]
```

Operator-voice + `(1:1)` seal on Section 1 = "this is the operator's
diagnosis, not the agent's editorial". Drop the seal in Section 2+
where you've synthesized.

---

## 3) Per-prompt body shape (`0N-<slug>.md`)

Twelve sections per [`vc-operator/DISPATCH.md`](../../vc-operator/DISPATCH.md).
Summary:

````markdown
---
prompt_id: <slug>-<YYYYMMDD>
wave: <A|B|C|D>
position: <1..N within wave>
mandate: /<skill>
recommended_agent: <claude|codex|gemini|cursor>
parent_branch: <branch>@<sha>
result_branch: feat/<slug>
depends_on: [<prompt_ids>]
parallel_with: [<prompt_ids>]
blocks: [<prompt_ids>]
report_path: ~/.vibecrafted/artifacts/<...>/reports/<slug>_<ts>_<agent>.md
authored_by: <agent> <agents@vetcoders.io>
---

# Prompt <N> — <slug>

[Mission paragraph — what lands when this prompt succeeds.]

## 1) Context

[Bullets pointing at files / SHAs / contracts to read first.]

## 2) Files to create / edit

[Grouped list, with APPEND-ONLY markers on shared files.]

## 3) Acceptance

- [ ] [observable outcome 1]
- [ ] [observable outcome 2]
- [ ] All existing tests stay green.

## 4) Gates

```bash
<exact commands>
```
````

## 5) Out of scope

- [DO NOT touch] [item 1]
- [DO NOT touch] [item 2]

## 6) Living Tree etiquette (verbatim)

[Standard block from vc-operator/DISPATCH.md Section 8.]

## 7) Loctree first

[Standard block from vc-operator/DISPATCH.md Section 9.]

## 8) Recovery hint

[Standard block — substrate / scope / implementation stall.]

## 9) Branch + commit convention

[Branch name, commit title template, Authored-By, do-not-push.]

## 10) Report path + Call to Action + closing rail

[Canonical path + report sections + Emil rail block: anti-debt one-liner
(งಠ_ಠ)ง + Call to Action (sequential imperative) + Suchar (._.).]

````

The closing rail tells the worker *"you are the agent now, and the
operator is reading the report, not the diff"* — see
[`../../vc-operator/DISPATCH.md`](../../vc-operator/DISPATCH.md) Section
"Closing rail — the Emil default block" for the required shape and
suchar bank.

---

## 4) TRACKER shape

A single living file, append-only at the wave level. Each wave gets one
section; rows transition `- [ ]` → `- [x]` as commits land.

```markdown
# Tracker — <plan title>

Plan: `00-master-dispatch.md`
Started: <YYYY-MM-DD HH:MM Z>
Operator-agent session: `<session-uuid>`

## Wave A (foundation)
- [x] A-1 <slug> (<agent>) — `<sha>` on `<branch>` · report: `<path>`

## Wave B (sequential, <coordination note>)
- [x] B-1 <slug> (<agent>) — `<sha>` on `<branch>` · report: `<path>`
- [x] B-2 <slug> (<agent>) — `<sha>` on `<branch>` · report: `<path>`
- [ ] B-3 <slug> (<agent>) — 🔄 firing, await `<task-id>`, ETA `<minutes>`
- [ ] B-4 <slug> (<agent>) — pending

## Wave C (parallel, file-scope disjoint)
- [ ] C-1 <slug> (<agent>) — pending
- [ ] C-2 <slug> (<agent>) — pending
- [ ] C-3 <slug> (<agent>) — pending

## Wave D (final, sequential)
- [ ] D-1 <slug> (<agent>) — blocked by Wave B+C merge
- [ ] D-2 <slug> (<agent>) — blocked by D-1

## Operator action queue ("wystarczy wcisnąć guzik")

- [ ] Push `<branch>` to origin
- [ ] Open PR `<branch>` → `develop`
- [ ] Merge wave B into trunk before firing wave C (if applicable)

## Recovery dispatches (if any)

- [x] `<prompt-id>-recovery-<ts>` recovers `<original-id>` — `<sha>`
- [ ] ...
````

**Update discipline**: the operator-agent edits this file after every wave
close-out, not after every commit. One edit per wave keeps the diff history
clean and the file scannable.

---

## 5) Naming conventions

### Filename slug

- kebab-case lowercase
- starts with the wave letter for ls-scannability: `01-a-shell.md`,
  `02-b-editor-core.md`, …
- ends with a slug that matches the eventual branch name minus the
  `feat/` prefix: branch `feat/textforge-editor-core` → file
  `02-b-textforge-editor-core.md`

### prompt_id

Same slug + date stamp: `textforge-editor-core-20260516`. Used as
cross-walk into session retrieval. Stable across recovery dispatches —
recovery uses `<original-id>-recovery-<ts>` so retrieval can still join.

### Branch name

`feat/<slug>` for feature work, `chore/<slug>` for housekeeping,
`fix/<slug>` for hotfixes.

---

## 6) Cross-skill handoff points

`vc-scaffold` writes the MASTER + per-prompt bodies + initial TRACKER.
`vc-operator` consumes them and dispatches. The handoff is the
default artifact path — operator-agent loads from
`~/.vibecrafted/artifacts/<...>/dispatch/`, no further coordination
needed.

When the plan reaches close-out:

- The last prompt in the plan writes a close-out backlog entry under
  the consumer repo's `docs/backlog/` (per
  [`vc-init/backlog/HOWTO.md`](../../vc-init/backlog/HOWTO.md)).
- The TRACKER's "Operator action queue" lists the remaining buttons.
- The operator-agent writes the stop-point handoff (per
  [`vc-operator/AUTONOMY.md`](../../vc-operator/AUTONOMY.md) Section
  "The stop-point handoff").

---

## 7) Anti-patterns

- **One file for everything**: merging MASTER + bodies into a single
  Markdown file. The bodies must be loadable individually via
  `vc-justdo <agent> --file 02-b-editor-core.md`.
- **Stale TRACKER**: forgetting to flip `[ ]` → `[x]` after wave green.
  Operator can't audit progress; future agents re-derive state.
- **Numbering by import time instead of dispatch order**: 01- 02- 03-
  must reflect the order the operator-agent fires, not the order the
  plan author thought of them.
- **Missing `parent_branch` in frontmatter**: worker doesn't know
  where to branch off; guesses; corrupts the chain.
- **Plan in a Google Doc**: it's not in `~/.vibecrafted/artifacts/`, so
  retrieval won't find it, so future agents won't find it. Plans live
  on disk, in default paths.

---

## 8) Example: TextForge plan as case study

Plan: `~/.vibecrafted/artifacts/<org>/<repo>/2026_0516/dispatch/`

- `00-master-dispatch.md` — Wave A→D atlas
- `01-textforge-editor-core.md` — Prompt 2 body (Wave B-1)
- `02-textforge-tool-rail.md` — Prompt 3 body (Wave B-2)
- ...
- `09-textforge-e2e-docs.md` — Prompt 10 body (Wave D-2)
- `tracker.md` — append-only wave checkbox state

Dispatched 2026-05-16. Wave B 3/4 green at time of writing this HOWTO —
B-1 `304791be` claude, B-2 `ba60ef66` gemini, B-3 `ab32a848` codex,
B-4 firing. Live proof the convention scales.

---

## Call to Action

Before declaring a plan complete, verify three files exist:
`00-master-dispatch.md`, at least one `0N-<slug>.md`, and `tracker.md`.
Then hand the path to the operator-agent — they will fire Wave A and
schedule the heartbeat without further prompting.

---

## Closing Rail

```text
=======================
A plan is not a wish list. It is a contract between the brainstormer
and the conductor — three files, one default path, one tracker that
flips from [ ] to [x]. Honour the layout and the dispatch becomes
boring in the best possible way. (งಠ_ಠ)ง
=======================

Suchar: Why do plans without a tracker always run late? Because nobody
remembers which checkbox to flip when the SHA finally lands. (._.)
```

---

_Vibecrafted. with AI Agents (c)2024–2026_
