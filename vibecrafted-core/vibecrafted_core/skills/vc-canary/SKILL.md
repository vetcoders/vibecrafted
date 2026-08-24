---
name: canary
version: 2.0.0
description: >
  Truth-competition radar for a repo: detect components competing for the
  same class of truth (identity, authorship, reduction, finality, delivery,
  configuration) before they poison the runtime. Four phases: authority &
  freshness, structural candidates, parallel-implementation radar,
  ownership polarization. Evidence and classification only — never
  refactors. Use when the user asks to "canary", "truth radar",
  "parallel implementation radar", "truth collision", "semantic twins",
  "kto jest właścicielem prawdy", "skataloguj repo", "ownership catalog",
  or runs /vc-canary / vibecrafted canary.
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-canary` (launcher `canary`)**
>
> Same three-path _shape_ as the fleet — see
> [DELEGATION_MATRIX.md](../DELEGATION_MATRIX.md):
>
> | Path                    | Literal                                    |
> | ----------------------- | ------------------------------------------ |
> | 1. User-launched worker | `vibecrafted canary <agent>`               |
> | 2. Interactive          | `/vc-canary` — execute **in this session** |
> | 3. Agent-operator       | `vibecrafted canary <agent>` via dispatch  |
>
> Root defaults to **`$PWD`**. Do not invent `vibecrafted workflow` as a stand-in.

<!-- /fleet-imperative -->

# vc-canary — truth-competition radar

## Mission

Agent-generated software fails in a specific way: **locally correct modules
appear faster than the system establishes global ownership of semantics.**
The result is not duplicate code — it is components competing for the same
class of truth: five identity concepts, two document reducers, five sources
of configuration, twenty active participants between input and delivery.

Canary answers one question, per decision axis:

> **How many places in this repo can answer the same runtime question —
> and which of them is the writer, the arbiter, the observer, the projection?**

Canary **detects** multi-authority. It does not presume multi-authority is
wrong: two implementations can be legal (runtime vs replay) — the agent
must **prove** that boundary, never assume it. Canary produces evidence and
classification, **never refactors and never proposes throne decisions as
findings** — implementation comes after QC, in cut skills, on the operator's
word.

Empirical basis: two independent production systems built by agents showed
the same disease (one: 5 identity concepts, 5 settings sources, 2 document
reducers, prism 11/12), found by the same protocol.

## Canonical Orientation Gate

Consume fresh `vc-init` evidence for the repo; if absent, run `vc-init`
first. Sensing planes via raw grep, docs, or "I remember this repo"
instead of Loctree organs is a process failure.

**Forbidden as inventory:** `loct context --full` `structural.files` (hub
ranking only). **Forbidden:** loading raw multi‑MB `snapshot.json` into the
model context.

## Phase 0 — Authority & freshness

No radar on a stale tree. Record, with receipts:

- exact repo, root, HEAD SHA; dirty-tree status; worktrees and nested repos;
- Loctree snapshot coverage: `canary_cli atlas --refresh` →
  `./.loctree/atlas/` (`repo-atlas.json`, `inventory.jsonl`,
  `coverage.json`); coverage `pass: true` required to proceed;
- commits reachable locally but absent from HEAD genealogy (`git cherry`,
  local/remote-only branches) — orphaned semantics is a radar input, not
  trivia.

## Phase I — Structural semantic candidates

High-scale pass: `loct repo-view`, tree, `focus`, `twins`, `crowd`,
`hotspots`. Goal is **not** findings — it is the list of candidate
**decision axes**: classes of truth this repo decides, and the files whose
names/roles suggest more than one component decides them.

Axis classes that recur (derive the repo's own; do not copy this list
blindly): identity, correction/text authorship, engine/orchestration
selection, document/state reduction, formatting, finality/seal, delivery,
configuration precedence, epistemic competitors (verifiers/replayers/reports
that judge similar truth without writing daily runtime).

## Phase II — Parallel Implementation Radar

For **each** axis, descend with receipts:

```
find --discover → exact occurrences (literal coverage) → body
→ slice / consumers → follow (trace / pipelines / events) → impact (when needed)
```

**Falsification of absence is mandatory.** Broad/semantic search yields
candidates; only the literal-coverage line ("scanned X of Y files") proves
where a decision does _not_ live. "I searched semantically and it looks
like one place" is an unproven claim — the exact failure mode this phase
exists to prevent.

Classify every competing pair:

| Verdict                | Meaning                                                   |
| ---------------------- | --------------------------------------------------------- |
| `SAME_SOURCE_OF_TRUTH` | both resolve to one authority; no competition             |
| `INTENTIONAL_VARIANT`  | legal parallel (e.g. runtime vs replay) — boundary proven |
| `DRIFTED_DUPLICATE`    | started equal, semantics diverged                         |
| `BYPASS_PATH`          | second path skips the authority on some inputs            |
| `FALSE_PARALLEL`       | looked parallel structurally; evidence dissolved it       |

Mark runtime weight with the legend:

- 🔥 direct collision in daily runtime
- ⚠ same responsibility in another stage or mode
- ◌ offline/test/alternate competitor

## Phase III — Ownership polarization

`loct prism` on the axis framings + manual adjudication. For each axis:
how many places can answer the runtime question; who is **writer**, who
**arbiter**, who **observer**, who **projection**. Findings are born here —
nowhere earlier — and each carries a classification:

| Class          | Meaning                                             |
| -------------- | --------------------------------------------------- |
| `CUT_BLOCKER`  | poisons daily runtime; blocks planned cuts          |
| `CUT_COHERENT` | fix folds naturally into an already-planned cut     |
| `FOLLOW_UP`    | real, not urgent; goes to backlog with evidence     |
| `OBSERVATION`  | multi-authority proven legal or dormant; watch only |

## Evidence schema (per finding)

Axis · competitors (`file:line`, LOC) · symbols at war · pair verdict ·
legend mark · classification · proof (loct outputs cited by organ) ·
absence-falsification receipt (the literal coverage line). A finding
missing any element is a candidate, not a finding.

## Journal contract

Append-only `./.loctree/canary/JOURNAL.md` in the target repo. Each run
appends: HEAD SHA, Phase 0 receipts, axes examined, findings with evidence,
prism scores. Runs never overwrite history — the journal shows what the
radar saw _at that SHA_, even when a later run knows better.

## Report → discuss → decide (QC stop)

Canary mutates **no code** and seeds no memex/aicx silently. Output is the
journal entry + a report for the operator. Throne decisions, cut plans and
deletions happen after discussion, in their own skills, with canary's
evidence attached. Acceptance test for any canary change: re-run on a known
case study and reproduce previously manual findings **without being told
where to look**.

## Fleet mode (large repos)

One agent per axis (or per scope feeding axes), variable N from Phase I —
never a fixed count. Hybrid: N≤8 native; N>8 external via
[await-arming](../vc-dispatch/references/await-arming.md). Per-scope brief
template: [references/canary-agent-brief.md](references/canary-agent-brief.md).
Agent pin: user; else whichever launcher is live, in order
`claude` · `codex` · `grok`.

## Utility scripts

```bash
CLI="uv run --python 3.12 …/vc-canary/scripts/canary_cli.py"

$CLI snapshot-path --root .
$CLI repo-view --root .
$CLI atlas --root . --refresh
$CLI coverage --root .
```

All writes go under `./.loctree/` (atlas + canary). Status on stdout; data
in files.

## Dependencies

| Skill / tool                | Why                                  |
| --------------------------- | ------------------------------------ |
| `loct` / loctree            | snapshot, organs, prism, occurrences |
| `vc-loctree`                | structural doctrine                  |
| `vc-dispatch` await-arming  | fleet wake, not log files            |
| `vc-delegate` / `vc-agents` | hybrid native vs external            |

## Common mistakes

- Treating every multi-authority as a defect (an `INTENTIONAL_VARIANT`
  with a proven boundary is a healthy answer)
- Proposing refactors or thrones inside the canary run
- Claiming absence from semantic search without literal coverage
- Producing a report on a stale snapshot or ignoring dirty/worktree state
- Overwriting the journal instead of appending
- Using `loct-context-full.json` as the file inventory
- Fixed agent count instead of axes from Phase I

## Verify before the handoff

See [VERIFICATION_RULE.md](../VERIFICATION_RULE.md). Green gates ≠ true
radar. Coverage `pass: true`, every finding carrying its full evidence
schema, and an appended (never rewritten) journal entry — required.

---

_v2 field evidence: 5 identity concepts, 5 settings sources, 20 active
participants between capture and delivery — found in a production
agent-built system by this protocol, 2026-08._
