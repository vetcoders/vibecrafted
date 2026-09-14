---
name: vc-review
version: 2.0.0
description: >
  READ-ONLY bounded code review. Generates structured artifacts with
  prview-rs, then runs findings-max analysis with falsification-first
  discipline. Every finding carries an explicit evidence grade
  (STRONG / MEDIUM / WEAK / NONE) and either passes or fails an
  adversarial pass. Stage-aware verdicts prevent mid-stage PRs from
  being judged as fully-staged. Per-implementation perception step
  in the pipeline; for per-plan post-marbles falsification use
  `vc-audit` instead; for trajectory direction checking use
  `vc-followup`. Trigger phrases: "review PR", "analyze branch",
  "run prview", "sprawdź PR", "zrób review", "daj findings",
  "zbadaj branch", "artifact pack", "PR quality check", "merge gate",
  "findings-max", "deep review".
default: vc-review
aliases:
  - vc-pr
compatibility:
  tools:
    - Skill
    - TaskCreate
    - TaskUpdate
    - Bash
    - Read
    - Write
requires:
  - vc-init
  - loctree
  - prview
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-review` (launcher `review`)**
>
> Same three-path _shape_ as the fleet, with **this** skill's literals — see the
> canonical [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                                                                  |
> | ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | `vibecrafted review <agent>`                                                                                                            |
> | 2. Interactive          | `/vc-review` — execute **in this session**; use native subagents when required; do **not** externalize merely because a launcher exists |
> | 3. Agent-operator       | may dispatch the worker form above via `vc-dispatch` / operator lines while preserving this skill's identity                            |

> Freer native on some runs ≠ abandon external fleet. `vc-dispatch` and `vc-ship` keep their own identities.

<!-- /fleet-imperative -->

# vc-review — READ-ONLY Bounded Code Review

> The per-implementation perception charter. Where `vc-audit` says
> **"falsify the spec claim"** and `vc-followup` says **"is the
> trajectory healthy?"**, this one says **"findings-max on a bounded
> diff, every claim defaults to UNVERIFIED, the reviewer never
> touches the code"**.

---

## Operator Entry

### Living Tree / Worktree Rule

Runs in the operator's current checkout and current branch. Do not
move into a worktree unless explicitly asked. Re-read files before
judging final state. The one sanctioned second mode is a Fleet Worktree dispatch (written plan, pre-committed verifiers, disjoint domains, single-thread integrator — see Living Tree Rule, Mode B); outside that formation, stay in the shared tree. See [Living Tree Rule](../LIVING_TREE_RULE.md).

## Canonical Orientation Gate

Before review, consume fresh `vc-init` evidence for the repo. If
absent, run `vc-init` first. Use `Loctree:loctree` (repo-view, focus,
slice, impact, find, follow) to refresh the
Code-Derived Application Map before grep / docs / memory claims.
Loctree-side questions (importer graphs, blast radius, dead code,
symbol locations) bypassed via grep = process failure.

Standard launcher:

```bash
vibecrafted start
vibecrafted review claude --prompt 'Review PR #4'
vc-review codex --prompt 'Deep review of release/v1.2.1 vs main'
vibecrafted review codex --prompt 'Review HEAD~10..HEAD'
vibecrafted review gemini --file /path/to/pr-artifacts-pack.md
```

`vc-review` needs a **bounded target**: PR, branch diff, commit range,
or generated artifact pack. Prefer `--pr` or other review-specific
inputs.

---

## Repository Work Doctrine

For repository work, start with Loctree as the map: use `loct context`,
`loct occurrences`, `loct body`, and `loct find --literal` before broad manual
search. Use AICX for intent and session context. Use rg/grep as fallback or
local magnifier, not as a replacement for structural mapping. If Loctree fails
or misses a surface, append feedback to `~/.vibecrafted/loctree/loctree-fail.md`.

## Purpose

Use this skill when a bounded diff (PR, branch, commit range, artifact
pack) needs evidence-graded findings before merge. Output: P-leveled
findings with evidence grade + Before-Merge TODO checklist.

This skill **never modifies code**. It produces a verdict + a findings
list — nothing else. Code modification belongs downstream in
`vc-marbles` (over-write) and `vc-polarize` (decisive cut).

---

## When To Use It

Use `vc-review` when:

- a PR / branch / commit-range needs gating before merge
- prview artifacts need findings-max extraction
- a multi-commit PR needs commit-progression analysis
- the target is **bounded diff**, not "the whole repo"

Do **not** use this skill when:

- the target is a multi-task plan claiming completion — that's `vc-audit`
- the question is "is the implementation direction healthy?" — that's
  `vc-followup`
- the operator wants gaps fixed during the pass — that's `vc-marbles`
  (review is READ-ONLY)

---

## Pipeline Position

`vc-review` is the **per-implementation perception** step:

```
... → implement (WRITE) → followup (READ) → [REVIEW: READ-ONLY] → marbles (WRITE) → audit (READ) → ...
```

READ-ONLY: produces verdict + findings + report, never modifies code.

---

## Default Stance: Falsification

Default verdict for every spec-claim the diff makes is **UNVERIFIED**.
PR descriptions, commit messages, "fixes #123" markers, and prior agent
reports are _claims_, not evidence. `vc-review` converts those claims
to evidence by inspecting code + tests.

### Hard Non-Trust Rules

You MUST NOT trust PR description bullets, commit messages naming the
fix, `// done` / `# implemented` inline comments, prior `vc-followup`
or `vc-review` reports, frontmatter status on linked task files,
AICX / chronicle / memory slices, or "fixes #123" / "closes #456"
annotations — unless **independently confirmed in current code/tests**.

### Evidence Taxonomy

Every finding carries an explicit evidence grade:

| Grade  | Criteria                                                                         |
| ------ | -------------------------------------------------------------------------------- |
| STRONG | Code in diff + test asserting exact behavior + negative check (old path removed) |
| MEDIUM | Code in diff + weak/general test, or type-system-enforced                        |
| WEAK   | Code in diff only, no test, no negative check                                    |
| NONE   | No direct evidence — finding tagged `[VERIFY]`, severity capped at P3            |

A "ready to merge" recommendation is only valid if every P0/P1
candidate finding scored STRONG or MEDIUM. WEAK on a P0 candidate
means the review itself is UNVERIFIED on that axis.

### Stage-Aware Verdicts

Most PRs are mid-stage. A PR landing Stage 1 of 3 must NOT be marked
P1-blocking because Stage 2 is queued. Tag each finding explicitly:

- `[STAGE-OK-DEFERRED]` — gap is explicitly out of scope for this PR
- `[STAGE-PARTIAL]` — landed stage has a real gap inside its scope
- `[STAGE-DRIFT]` — PR mixes deferred and landed scope without saying so

Stage tags ride alongside P-level: `[P2][STAGE-OK-DEFERRED]` is a
hygiene note, not a merge blocker.

### P-Level Scale

| P-level | Definition                                           | Examples                                              |
| ------- | ---------------------------------------------------- | ----------------------------------------------------- |
| **P0**  | Blocker merge / security / data loss / failing gate  | Failing tsc, leaked credentials, missing artifacts    |
| **P1**  | High regression risk in core flow, breaking contract | Breaking API, large untested changes, critical cycles |
| **P2**  | Medium: edge cases, a11y, telemetry, partial tests   | Missing i18n keys, hardcoded URLs, no error handling  |
| **P3**  | Low risk / hygiene / minor drift                     | Empty doc titles, test setup duplication, naming      |

---

## Operating Model

Two phases. Each detailed in companion files.

### Phase 1 — Generate Artifacts ([PRVIEW.md](PRVIEW.md))

Most common dispatches:

```bash
prview --pr <NUMBER>                          # local branch vs develop/main
prview -R --remote-only <branch> <base>       # remote branch (no checkout)
prview --pr <NUMBER> --with-tests --with-lint # GitHub PR by number
prview --deep                                 # all gates
```

Default for vc-review: **do not use `--quick`**. Use `--quick` only
for explicit fast triage or when heavy gates are impossible.

Add `--gh-repo owner/repo` if origin is ambiguous. Full flag reference,
mode table, profile detection, policy system, and tooling-special-cases
in [`PRVIEW.md`](PRVIEW.md).

### Phase 2 — Analyze Artifacts ([FINDINGS.md](FINDINGS.md))

Findings-max philosophy. Reading order, mandatory pattern scans,
adversarial pass, minimum coverage requirements, and output format
all in [`FINDINGS.md`](FINDINGS.md).

---

## Output Contract

Three mandatory sections, in this order:

1. **Findings (P0/P1/P2/P3 with evidence grade)** — see
   [`FINDINGS.md`](FINDINGS.md) for full template
2. **Before-Merge TODO** — markdown checkboxes cross-referencing
   finding IDs (`P1-01`, `P1-02`, ...) with verification commands
3. **Self-Attack Pass + Model Check** — attack every STRONG verdict,
   downgrade if a falsifier exists; emit `model_confidence: high |
medium | low`. If confidence ≠ high, cannot recommend "merge as-is"
   — only "merge after operator verifies X"

Optional sections (add when they provide value): Executive Summary,
Architecture Context, Scope / What Changed, Commit Progression, Test
Coverage Matrix, Security & Privacy Check, QA Plan, Evidence Index.

---

## Composition with adjacent skills

`vc-review` composes with — does not replace — these:

- **`vc-init`** — required gate before review.
- **`vc-audit`** — sibling READ-ONLY role at per-plan scope (not per-diff).
- **`vc-followup`** — sibling READ-ONLY role at trajectory scope.
- **`vc-marbles`** — downstream WRITE step that fixes what review finds.
- **`vc-polarize`** — downstream WRITE step that cuts to one truth.

---

## Anti-Patterns

Tool usage:

- Using `--quick` as default for PR review (drops test/lint/security signal)
- Running `--deep` on every PR when `--with-tests --with-lint` suffices
- Reading `full.patch` entirely for large PRs (use `per-file-diffs/`)
- Ignoring `report.json` / `MERGE_GATE.json` (parse structured first)
- Not using `--update` after amend/force-push (duplicate artifact sets)

Analysis:

- Stopping at 5 findings when 25 are visible (findings-max = exhaustive)
- Findings without evidence grade (STRONG / MEDIUM / WEAK / NONE mandatory)
- Findings without negative check ("old path removed" must be verified)
- Mixing separate problems into one finding (one point = one problem)
- Skipping pattern scans (`.unwrap()` / `any` / PII checklist mandatory)
- Skipping the adversarial pass (Phase 2.5 is mandatory)
- Skipping self-attack on STRONG verdicts
- Modifying code during review (READ-ONLY — fixes belong in marbles)
- Trusting PR description / commits / `// done` without code verification
- Recommending "merge" while `model_confidence` ≠ `high`
- Treating mid-stage PRs as fully-staged (use `[STAGE-OK-DEFERRED]`)

---

## Call to Action

Read [`PRVIEW.md`](PRVIEW.md) before your first dispatch — it carries
the prview flag reference and artifact pack layout. Read
[`FINDINGS.md`](FINDINGS.md) before your first findings pass — it
carries the reading order, mandatory pattern scans, adversarial pass,
and full output template. Then default every claim to UNVERIFIED and
earn each finding's evidence grade.

---

## Closing Rail

```text
=======================
Remember: review mode is permission to refuse a merge, not permission
to fix the diff. You read prview, you grade evidence, you tag stage,
you attack your own verdict, you stop. The operator owns the merge
button.
(•_•)つ━☆
=======================

Suchar: Why does a reviewer with WEAK evidence sleep poorly?
Because the diff still owns the receipt.  (._.)
```

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
