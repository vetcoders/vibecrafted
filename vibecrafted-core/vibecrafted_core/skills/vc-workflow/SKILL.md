---
name: vc-workflow
version: 3.6.0
description: >
  This skill should be used when the user asks to "examine and implement",
  "research then implement", "workflow", "pipeline", "examine → research → implement", "full workflow", "ERi pipeline", "native fleet workflow",
  "plan and implement", "analyze then build", "structured implementation"
  or describes a task that requires understanding code structure before making changes. Orchestrates a three-phase pipeline: Examine (loctree), Research (Brave Search / web), Implement (subagents). Each phase feeds context to the next.
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
native_fleet: "use native fleet delegation widely"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-workflow` (launcher `workflow`)**
>
> Same three-path _shape_ as the fleet, with **this** skill's literals — see the
> canonical [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                                                                    |
> | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | `vibecrafted workflow <agent>`                                                                                                            |
> | 2. Interactive          | `/vc-workflow` — execute **in this session**; use native subagents when required; do **not** externalize merely because a launcher exists |
> | 3. Agent-operator       | may dispatch the worker form above via `vc-dispatch` / operator lines while preserving this skill's identity                              |
>
> **Note:** ERi pipeline only. Other skills are not ERi by paste.

> Freer native on some runs ≠ abandon external fleet. `vc-dispatch` and `vc-ship` keep their own identities.

<!-- /fleet-imperative -->

# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Workflow — ERi Pipeline

## Operator Entry

### Living Tree / Worktree Rule

Run in the operator's current checkout and branch; no worktree unless the
operator explicitly asks (the one sanctioned second mode is a Fleet Worktree
dispatch — plan, pre-committed verifiers, disjoint domains, single-thread
integrator). Re-read before editing; report substrate failure if the tree is
poisoned. Full rule: [../LIVING_TREE_RULE.md](../LIVING_TREE_RULE.md).

## Canonical Orientation Gate

Before repo-specific analysis, planning, implementation, review, release, or
delegation, run or consume `vc-init` for the assigned repo — fresh evidence or
the work is blocked. Loctree is the perception layer (repo-view/focus/slice/
impact/find/follow; search before creating, impact before deleting, slice
before editing). Missing `vc-init`/Loctree evidence is a process failure; the
full gate text lives in [../vc-init/SKILL.md](../vc-init/SKILL.md).

Standard launcher (`vibecrafted start` / `vc-start`, then `vc-<launcher> <agent> [--prompt|--file ...]`).

```bash
vibecrafted workflow claude --prompt 'Examine auth surface and implement fixes'
vc-workflow codex --prompt 'Research SSO options then implement the best fit'
vibecrafted workflow agy --file /path/to/research-plan.md   # gemini deprecated; agy is Google replacement
```

Foundation deps (loaded with framework): `vc-loctree`, `vc-aicx`,
[`vc-delegate`](../vc-delegate/SKILL.md) (native fan-out policy for Phase 3).

**Examine. Research. Implement.** Three-phase pipeline that chains structural
code intelligence, ground truth research, and parallel agent delegation. Each
phase accumulates context for the next — no blind implementation.

## Repository Work Doctrine

Loctree first (`loct context/occurrences/body/find --literal`), AICX for
intent history, rg/grep as local magnifier only; Loctree gaps go to
`~/.vibecrafted/loctree/loctree-fail.md`.

## Pipeline Position

```
scaffold → init → [WORKFLOW] → followup → marbles → dou → decorate → hydrate → release
```

## Pipeline Overview

`EXAMINE (loctree: repo-view → focus → slice/impact → find) ⇒ CONTEXT.md` →
`RESEARCH (brave/WebFetch/Context7, curated) ⇒ RESEARCH.md` →
`IMPLEMENT (fan-out agents, collect reports, review+merge) ⇒ reports/*.md` →
`CONVERGE (marbles P0=0 → polarize align) ⇒ THESIS.md`.

Canonical artifact root: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/{plans,reports,tmp}/`;
`spawn_prepare_paths()` in `../../runtime/scripts/common.sh` is the source of
truth for day-root and naming; repo-local `.vibecrafted/{plans,reports}` are
convenience symlinks only.

## Phase 1 — EXAMINE

Map the codebase before touching anything. Foundation skills are the primary
sensory layer.

1. **Consume `vc-init` outputs** — read `AGENTS.md` and the
   situational report. If `vc-init` was not run, run it first.
2. **Deepen the map (loctree)** beyond init baseline:
   - `slice(file)` for every file likely to change (deps + consumers)
   - `impact(file)` for high-hub or deletion-candidate files
   - `find(name)` before creating any new types/functions
3. **AICX (intentions)** — `aicx extract` if previous session output is too large
   or in raw JSONL.
4. **PRView** — generate artifacts first if the workflow is part of a PR review.
5. **Screenscribe** — consume findings if the task originated from a visual demo.

### Output: CONTEXT.md

Write to `plans/<ts>_<slug>_CONTEXT.md` using the template in
[references/output-templates.md](references/output-templates.md)
(frontmatter + Repo Health, Scope, Critical Files, Symbols Found, Risk Map,
Decision: research needed vs skip to implement).

### Phase Gate

Present CONTEXT.md summary. Ask: **Research or Implement?** If domain is
well-understood, skip Phase 2.

## Phase 2 — RESEARCH

For deep architectural unknowns or major investigations, **DO NOT run ad-hoc
research yourself.** Hand the questions derived from Examination off to
`vc-research` (triple-agent swarm) and consume its report.

For simple lookups (single API param, file syntax) use Brave Search / Context7 /
WebFetch directly: query `"<API> usage example <year>"`, fetch standard docs.

### Output: RESEARCH.md

Write to `plans/<ts>_<slug>_RESEARCH.md` using the template in
[references/output-templates.md](references/output-templates.md)
(questions from examination, per-question findings with sources,
architectural decision, implementation notes).

### Phase Gate

Present RESEARCH.md summary. Ask: **Proceed to Implement?**

## Phase 3 — IMPLEMENT

Armed with CONTEXT.md + RESEARCH.md, parallelize the implementation:

- **Operator seat**: spawn external workers per `vc-agents` (Spawn Pattern).
- **Worker seat** (a dispatched `vibecrafted workflow <agent>` run): external
  fleet is operator-only, but that is NOT a sentence to serial execution —
  **fan out with your runtime's native subagents** (Claude: Task tool via
  [`vc-delegate`](../vc-delegate/SKILL.md); Kimi: swarm; other runtimes:
  their native lane). Workerhood constrains run scope, not native delegation
  rights. Split disjoint subcuts; pick tiers by subtask economics
  (`vc-delegate` → Native Delegation Policy). A plan that parallelizes but is
  executed serially is a slower, worse delivery, not a safer one.

### Agent Plan Template

Every plan MUST include:

1. **Mandatory frontmatter** — `run_id`, `agent`, `skill (vc-workflow/vc-agents)`, etc.
2. **Pipeline context** — paste relevant sections from CONTEXT.md + RESEARCH.md.
3. **Loctree instruction preamble** (proven 98% vs 85% completeness) — the
   canonical block lives in
   [references/phase-implement.md](references/phase-implement.md): repo-view
   first, slice before modify, find before create, impact before delete,
   never edit unmapped code.
4. **Living tree rule** — standard 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. preamble.
5. **Quality gate** — repo-specific test/lint commands.

### Spawn Pattern

Follow `vc-agents` for spawn commands (portable scripts preferred). Plans →
default `plans/`, reports → default `reports/` under
`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`. Repo-local
`.vibecrafted/plans` and `.vibecrafted/reports` are convenience symlinks only.

After dispatch, arm `vibecrafted await <agent> --run-id <id>` immediately,
supervisor-side. Full liveness doctrine (3 signals, known skew, Class 3
hedging violation) is canonical in `docs/runtime/AGENT_OPS.md` and
`vc-agents` → references/runbook.md.

### Phase 4 — CONVERGE (Marbles & Polarize)

Implementation existing ≠ true or shippable. (1) Gate check: read reports,
run `make check`, verify the risk map. (2) Gates red or runtime fragile →
**do not stop and do not present a diff with known gaps** — invoke
`vc-marbles` until P0=0. (3) Code stable but concept smeared (conflicting
docs, competing paths) → `vc-polarize --task <concept>`; prism bands decide
(`0..4 abort · 5..8 memo · 9..12 pass · 13..15 doctrine`). (4) Handoff:
final diff summary / `THESIS.md` ready for `dou` and Release.

### Commit cadence

One commit per round (marbles: one round = one commit), well-formed per the
commit-msg hook — delivered work is never left uncommitted; a run yields up
to 3 commits (Implement, Marbles, Polarize). Non-destructive push of the
feature branch is a duty afterwards. Force-push, trunk push, merge, and
deploy stay operator buttons.

## Quick Reference

| Phase     | Tool                               | Output                          |
| --------- | ---------------------------------- | ------------------------------- |
| Examine   | loctree MCP                        | `plans/<ts>_<slug>_CONTEXT.md`  |
| Research  | brave-search + Context7 + WebFetch | `plans/<ts>_<slug>_RESEARCH.md` |
| Implement | vc-agents (portable scripts)       | `reports/*.md`                  |

## Phase Skipping

- Small fix, known domain → Examine only, implement directly
- New API/library integration → all three phases
- Refactor → Examine + Implement (no external research)
- Research only → Examine + Research (no implementation yet)

State which phases apply at pipeline start.

## Notes

- Mandatory for non-trivial multi-file feature work.
- If loctree MCP unavailable, see `references/phase-examine.md` for grep fallback.
- Brave Search comes from runtime tool surface or web search fallback, not a local wrapper directory.

## Additional Resources

- `references/phase-examine.md` — deep loctree examination patterns
- `references/phase-research.md` — research methodology, source ranking
- `references/phase-implement.md` — agent delegation with accumulated context
- `scripts/pipeline-init.sh` — initialize default artifact paths

---

## Verify before the handoff

Before you report "done", walk around the truck — see [Verification Rule](../VERIFICATION_RULE.md): run the REAL artifact (launch the app/binary, not just `--version`), re-verify runtime, never trust upstream verification as proof, and check your own check. Gates green ≠ works. When composing implement-agent prompts, carry it into the dispatch footer.

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
