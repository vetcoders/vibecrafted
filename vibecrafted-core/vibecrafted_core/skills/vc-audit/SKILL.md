---
name: vc-audit
version: 1.0.0
description: >
  READ-ONLY falsification of a completed plan or multi-task
  implementation. Builds a per-task requirements matrix, then proves
  or refuses each claim against code + tests evidence. Default verdict
  is UNVERIFIED — PASS is earned, never assumed. Runs whenever a
  written plan claims completion, regardless of upstream — workflow,
  implement, marbles, human work, or a mix. Trigger phrases: "audit",
  "vc-audit", "task-by-task audit", "verify implementation plan",
  "spec falsification", "post-marbles audit", "did this plan actually
  land", "weryfikuj implementację", "audyt planu", "co naprawdę
  wylądowało", "falsyfikacja completion".
default: vc-audit
aliases:
  - vc-verify
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
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-audit` (launcher `audit`)**
>
> Same three-path _shape_ as the fleet, with **this** skill's literals — see the
> canonical [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                                                                 |
> | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | `vibecrafted audit <agent>`                                                                                                            |
> | 2. Interactive          | `/vc-audit` — execute **in this session**; use native subagents when required; do **not** externalize merely because a launcher exists |
> | 3. Agent-operator       | may dispatch the worker form above via `vc-dispatch` / operator lines while preserving this skill's identity                           |

> Freer native on some runs ≠ abandon external fleet. `vc-dispatch` and `vc-ship` keep their own identities.

<!-- /fleet-imperative -->

# vc-audit — READ-ONLY Plan-vs.-Code Falsifier

> The falsification charter. Where `vc-review` says **"findings-max on
> a diff"** and `vc-followup` says **"is that codebase going in the desired
> direction"**, this one says \*\*"default UNVERIFIED — PASS is earned, but
> Agents reports are just claims that vere not verified for real task completion.
> Noone ever tested this code in real life scenarios
> Noone ever

---

## Operator Entry

### Living Tree / Worktree Rule

Runs in the operator's current checkout and current branch. Do not
create, switch to, or move execution into a git worktree unless the
operator explicitly asks for one. Generic words like "isolate",
"parallel", or "clean branch" are not enough. Re-read files before
judging final state, adapt to concurrent changes, and report a
substrate failure if the tree is too poisoned to continue safely. The one sanctioned second mode is a Fleet Worktree dispatch (written plan, pre-committed verifiers, disjoint domains, single-thread integrator — see Living Tree Rule, Mode B); outside that formation, stay in the shared tree.

See [Living Tree Rule](../LIVING_TREE_RULE.md).

### Canonical Orientation Gate

Before this workflow performs any audit, it MUST consume fresh
`vc-init` evidence for the assigned repo. If absent, run `vc-init`
first; treat audit as blocked until repo truth exists.

`Loctree:loctree` is the default structural perception layer. Use
Loctree before grep / docs / "I remember" claims to materialize the
Code-Derived Application Map (repo-view, focus, slice, impact, find,
follow). Audit decisions that bypass Loctree on questions Loctree
handles (importer graphs, blast radius, dead code, symbol locations)
are process failures.

Standard launcher:

```bash
vibecrafted start
vc-audit claude --prompt 'Audit the 22-task plan in plans/2026Q2-loctree/'
vc-audit codex  --prompt 'Verify post-marbles surface against acceptance criteria'
vc-audit gemini --file /path/to/plan-and-target.md
```

---

## Repository Work Doctrine

For repository work, start with Loctree as the map: use `loct context`,
`loct occurrences`, `loct body`, and `loct find --literal` before broad manual
search. Use AICX for intent and session context. Use rg/grep as fallback or
local magnifier, not as a replacement for structural mapping. If Loctree fails
or misses a surface, append feedback to `~/.vibecrafted/loctree/loctree-fail.md`.

## Purpose

Use this skill when a written plan, spec, or multitask brief **claims
completion**. The implementation may have come from `vc-workflow`,
`vc-implement`, `vc-marbles`, human work, or any combination — audit
refuses to take the completion claim at face value regardless of
upstream. It rebuilds the plan's requirements atomically, then forces
each one to defend itself with code + test evidence. Whatever cannot
defend itself remains `UNVERIFIED`.

This skill **never modifies code**. Editing, refactoring, "fixing
while auditing", and committing during audit are all forbidden. The
output is a verdict matrix, a report, and a trace — nothing else.

---

## When To Use It

Use `vc-audit` when:

- a written plan / spec / multitask brief claims completion
- the operator hands over a directory of task files plus a checkout
- `vc-marbles` finished a round and the codebase claims to satisfy
  the brief; audit checks what actually landed
- a PR + written spec pair needs spec-vs.-code falsification (not just
  diff hygiene — that's `vc-review`)

Do **not** use this skill when:

- the target is a bare PR without a written spec — that's `vc-review`
- the target is "this repo, is the direction healthy?" — that's
  `vc-followup`
- the operator wants the gaps fixed during the pass — that's
  `vc-marbles` (audit never touches code)
- the question is "which truth wins?" — that's `vc-polarize`

---

## Pipeline Position

`vc-audit` sits in the **plan-vs-code falsification slot**. Common
upstream paths feed into it:

```
[workflow] ┐
[implement]├──► [AUDIT: READ-ONLY] ──► next decision
[marbles]  │
[mixed]    ┘
```

Downstream depends on verdict:

- PASS / PASS_WITH_GAPS → `vc-polarize`, `vc-dou`, or `vc-release`
- PARTIAL / UNVERIFIED → operator decides: another `vc-marbles` round,
  back to `vc-implement` for gaps, or scope cut via `vc-polarize`
- FAIL → operator escalates: spec rewrite or rebuild from `vc-scaffold`

Audit is **never** the terminal step. Output always feeds the next
operator decision.

---

## Default Stance: Falsification

Default verdict for every requirement is **UNVERIFIED**. A requirement
earns PASS only with all four:

1. **Task evidence** — quoted acceptance criterion or non-goal
2. **Code evidence** — file path, function/type/test name, line range
3. **Test evidence** — test name + run output, or justified test-gap
4. **Negative check** — old/forbidden behavior not still present

### Hard Non-Trust Rules

You MUST NOT trust task frontmatter status, prior agent reports,
commit messages, AICX entries, memory slices, chronicle notes,
"completed" annotations, PR descriptions, inline `// done` comments,
or prior `vc-followup` / `vc-review` reports — unless **independently
confirmed in current code/tests**. Each of those is a _claim_, not
evidence. Audit converts claims into evidence by checking code.

### Evidence Taxonomy

| Grade  | Criteria                                        |
| ------ | ----------------------------------------------- |
| STRONG | Code + targeted test + negative check OK        |
| MEDIUM | Code + weak/general test + negative check OK    |
| WEAK   | Code only, no test or no negative check         |
| NONE   | No direct evidence — verdict must be UNVERIFIED |

**PASS requires STRONG or MEDIUM on all core requirements.**

---

## Operating Model

Audit proceeds in **eight phases**. Sequential, not optional. Full
phase detail in [`PHASES.md`](PHASES.md).

1. **Context Receipt** — Loctree pack, dirty_worktree, hotspots, authority caveats
2. **Task Ingestion Receipt** — full-read every task; emit `Tasks Loaded` table
3. **Atomic Requirements Extraction** — testable items into `audit_requirements_matrix.jsonl`
4. **Positive + Negative Code Verification** — loctree-first, both checks
5. **Adversarial Pass** — actively prove implementation incomplete (five sub-checks)
6. **Stage-Aware Verdict** — landed vs. deferred scope
7. **Per-Task Verdict Table** — one row per task, no narrative collapse
8. **Self-Attack Pass + Model Check** — attack PASS verdicts; emit `model_confidence`

Verdicts: `PASS`, `PASS_WITH_GAPS`, `PARTIAL`, `FAIL`, `UNVERIFIED`,
`STAGE_PASS`, `STAGE_PASS_WITH_GAPS`, `STAGE_PARTIAL`,
`FULL_PLAN_INCOMPLETE_BY_DESIGN`.

Severity: P0 (contradicts task / breaks dependents / violates non-goal),
P1 (key criterion missing), P2 (test/report/process gap), P3 (cosmetic).

---

## Output Contract

`vc-audit` produces exactly three files in the report directory:

1. **`audit_report.md`** — executive verdict first, per-task table,
   self-attack pass, model check
2. **`audit_requirements_matrix.jsonl`** — one JSON record per
   requirement: verdict, evidence grade, code locations, test
   evidence, negative check result
3. **`audit_trace.log`** — compact per-phase trace (`BEGIN`,
   `READ_CONTEXT_PACK`, `READ_TASK`, `EXTRACT_REQUIREMENTS`,
   `INSPECT_CODE`, `VERIFY_TESTS`, `NEGATIVE_CHECK`, `DEPENDENCY_CHECK`,
   `STAGE_CHECK`, `CLASSIFY`, `SELF_ATTACK`, `WRITE_REPORT`, `END`)

Executive verdict MUST include task counts per verdict, P0/P1/P2/P3
counts, top 5 risks, next five actions, and `model_confidence: high |
medium | low`.

Operator dispatch template lives in [`DISPATCH.md`](DISPATCH.md).

---

## Composition with adjacent skills

`vc-audit` composes with — does not replace — these:

- **`vc-init`** — required gate. Without fresh init evidence, audit
  is blind.
- **`vc-review`** — sibling READ-ONLY role at per-implementation diff
  scope. Use review for "did this PR look clean?", audit for "did the
  written spec actually land in code?".
- **`vc-followup`** — sibling READ-ONLY role at trajectory scope. Use
  followup for "is the direction healthy?", audit for "did the spec
  ship?".
- **`vc-marbles`** — common upstream. Marbles plasters cracks in
  excess; audit checks what survived.
- **`vc-polarize`** — common downstream. Polarize consumes the audit
  verdict to decide which truth wins.

---

## Anti-Patterns

Do not in audit mode:

- fix code during audit ("just a small refactor while I'm here")
- mark PASS based on commit messages, frontmatter, or prior reports
- collapse all tasks into a general summary
- skip the negative check ("new code is there, that's enough")
- skip the adversarial pass or self-attack
- treat Stage 1 landed as full-plan PASS
- treat Stage 2 deferred as full-plan FAIL
- produce only the report without a matrix + trace
- trust AICX / chronicle / memory slices as repo truth
- bypass Loctree on importer-graph / blast-radius / dead-code questions
- protect your first verdict during self-attack instead of downgrading

---

## Acceptance Criteria

The audit run is **done** when:

- [ ] Every task / plan file has `task_read_status: FULL_READ`
- [ ] Every requirement has evidence grade + verdict
- [ ] Every requirement has positive + negative check results
- [ ] Self-attack executed on every PASS / PASS_WITH_GAPS
- [ ] Model check emitted with confidence rating
- [ ] All three output files written
- [ ] `git diff` empty for non-report paths (no code touched)
- [ ] Executive verdict references concrete next move

---

## Call to Action

Read [`PHASES.md`](PHASES.md) before running your first audit — it
carries the per-phase detail and the loctree-first negative-check
patterns. Read [`DISPATCH.md`](DISPATCH.md) before writing your first
operator-dispatch body — it carries the canonical 22-task audit prompt
shape. Then default every claim to UNVERIFIED and earn each PASS.

---

## Closing Rail

Delivery-proof semantics live in `vibecrafted_core.delivery`; see
`docs/runtime/DELIVERY_PROOF_KERNEL_v1.md`.

```text
=======================
Remember: audit mode is permission to refuse a claim, not permission
to fix it. You read the spec, you read the code, you grade the
evidence, you stop. The operator owns the next move.
(•̀ᴗ•́)و
=======================

Suchar: Why does the auditor never say PASS at first? Because
UNVERIFIED is the only mood that ages well.  (._.)
```

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders ©2024–2026 LibraxisAI_
