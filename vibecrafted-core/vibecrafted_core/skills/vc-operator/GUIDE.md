# vc-operator — GUIDE: Wave A/B/C/D Framework

> The composition guide for arranging a multi-prompt plan into dispatch waves.
> Source playbook: 2026-05-05 II Polar.sh delivery (8 dispatches in 4 waves);
> refined by 2026-05-16 TextForge orchestration (10 prompts, 4 waves, full
> autonomous chain). Read alongside [`SKILL.md`](SKILL.md), [`DISPATCH.md`](DISPATCH.md),
> and [`EMIL.md`](EMIL.md).

---

## Why waves, not flat dispatch

A 10-prompt plan fired flat (all 10 simultaneously) loses on three axes:

1. **Shared-state collisions**: prompts that touch the same provider /
   context / shell can overwrite each other's work or create living-tree drift
   that no agent owns.
2. **Recovery surface**: a single stall in the middle of a flat fire taints
   every downstream prompt with stale baseline assumptions.
3. **Operator legibility**: the operator cannot audit "is this on track"
   when 10 agents are mid-flight; they need a wave-shaped progress
   narrative.

The wave framework solves all three by making **dependency topology
explicit**: each prompt declares `depends_on` and `parallel_with`, and the
operator-agent groups prompts into waves where every member of a wave can
safely run together (or strictly sequenced).

---

## The four wave shapes

### Wave A — Foundation

**Pattern**: one prompt, one agent, sequential. The slice that unblocks
everything else. Usually a shell skeleton, a schema, a provider context
extension, or a baseline contract.

**Rules**:

- Wave A is **always** sequential (size 1).
- It starts from the operator's current baseline/head and names that baseline
  in the brief.
- Its acceptance bar is whether the next wave can safely use its report and
  resulting head as the baseline.
- If Wave A fails, the whole plan stalls. Recovery target = a fresh agent
  with sharper acceptance criteria, _not_ the same prompt to the same agent.

**Example** (TextForge): `textforge-shell` — `TextForgeShell.tsx` +
`TextForgeProvider.tsx` + five region placeholders. Acceptance: kingdom
appears in sidebar, regions render in both themes, provider exports the
contracts that Waves B+ will plug into.

### Wave B — Sequential chain

**Pattern**: N prompts, each touching shared state from the prior. Chain
agents through them — claude → gemini → codex → claude — to honour AGENT
FAIRNESS rotation while keeping each step's baseline current.

**Rules**:

- Each prompt starts from the prior prompt's verified report/head, not from a
  stale plan assumption.
- Living Tree warning **VERBATIM** in every brief: _"re-read shared file
  IMMEDIATELY before edit; append-only fields; don't delete other agents'
  lines."_
- Verify green commit + reports between every step. If a step stalls,
  recovery dispatch on _that_ step before advancing.
- Wait for green before firing the next. No flat parallel inside Wave B.

**Example** (TextForge): B-1 editor-core (claude) → B-2 tool-rail (gemini)
→ B-3 stylize (codex) → B-4 inspectors (claude). All chain through
`TextForgeProvider.tsx` extensions + `TextForgeCanvas.tsx` mechanics.

### Wave C — Parallel disjoint

**Pattern**: 2–3 prompts whose file scopes are provably disjoint. Fire
simultaneously, await all, synthesize together.

**Rules**:

- File-scope disjointness is the operator-agent's responsibility to verify
  before grouping. If two prompts both mutate `TextForgeProvider.tsx`, they
  belong in a Wave B chain, not a Wave C parallel.
- Start every prompt from the **same verified baseline/head**. Operator-side
  integration happens between waves, not inside.
- Use telemetry-backed fleet dispatch for deliverable workers. Native
  subagents are for bounded scouting/review sidecars, not substitutes for wave
  dispatch. Await all completions before firing the next wave.
- For dual-mutation prompts that can't be split, prefer **append-only +
  manual merge** explicitly in both briefs, with the operator's blessing
  that conflicts will be resolved operator-side.

**Example** (TextForge): Wave C = topbar (gemini) ‖ statusbar (gemini) ‖
diacritics-audit (codex). Topbar + statusbar both touch
`TextForgeProvider.tsx` _workspaces_ vs _lastAppliedStyle_ fields — explicit
append-only in both briefs. Diacritics is backend-only (`src/tools/*.js`),
zero collision risk.

### Wave D — Final close-out

**Pattern**: sequential prompts that require Wave B+C merge on trunk first.
Usually integration tests, docs, e2e harness, packaging.

**Rules**:

- **Operator-side trunk integration happens before Wave D fires**, not
  inside it. The operator-agent surfaces a "wystarczy wcisnąć guzik"
  stop point requesting Wave B+C merge.
- After merge, Wave D fires sequentially (size 1–3, almost always
  sequential).
- Last prompt in Wave D writes the final stop-point handoff and the
  close-out backlog entry.

**Example** (TextForge): D-1 input-parity (codex) — wires Wave A through
Wave C into keyboard / right-click flows. D-2 e2e-docs (claude) — Playwright
smoke in both themes + README/GUIDELINES update + close-out backlog entry.

---

## Building the wave atlas — concrete steps

1. **List every prompt** with its declared `depends_on` and shared-file
   surfaces. Use the plan's `master-dispatch.md` as ground truth.
2. **Group by dependency**: prompts with no deps go in Wave A; prompts
   that only depend on Wave A and don't share state with siblings → Wave C
   candidates; prompts that chain through shared state → Wave B.
3. **Verify Wave C disjointness**: for each parallel group, list the files
   each prompt touches. Any overlap → demote to sequential Wave B chain
   (or add explicit append-only coordination notes).
4. **Assign agents**: rotate Claude/Codex/Gemini across waves for AGENT
   FAIRNESS. Within a wave, agent choice is per-prompt (see `DISPATCH.md`
   `recommended_agent` field).
5. **Pick branch start points**: Wave A off trunk; Wave B chain off prior
   green; Wave C off trunk (post-Wave-B-merge); Wave D off trunk
   (post-Wave-B+C-merge).
6. **Write the tracker scaffold**: checkbox list grouped by wave (per
   [`EMIL.md`](EMIL.md) Rule 1). Each prompt is one bullet that transitions
   `- [ ]` → `- [x]` on green commit. Append SHA + branch when known.

   ```markdown
   ## Wave A (foundation)

   - [x] A-1 textforge-shell (claude) — `f6b02744` on `feat/text-context-menu`

   ## Wave B (sequential, shared canvas/provider)

   - [x] B-1 editor-core (claude) — `304791be` on `feat/textforge-editor-core`
   - [x] B-2 tool-rail (gemini) — `ba60ef66` on `feat/textforge-tool-rail`
   - [x] B-3 stylize (codex) — `ab32a848` on `feat/textforge-stylize`
   - [ ] B-4 inspectors (claude) — firing now, await `bc2zb970r`

   ## Wave C (parallel, file-scope disjoint)

   - [ ] C-1 topbar (gemini)
   - [ ] C-2 statusbar (gemini)
   - [ ] C-3 diacritics-audit (codex)

   ## Wave D (final, sequential)

   - [ ] D-1 input-parity (codex) — requires Wave B+C merge
   - [ ] D-2 e2e-docs (claude) — requires D-1
   ```

   Tracker statuses beyond `[ ]` / `[x]`: prefix with annotations when needed.

   - `- [ ] 🔄 ...` — currently firing / await in flight
   - `- [ ] ⚠ ...` — recovery dispatch fired (paired with the recovered prompt id)
   - `- [x] ↻ ...` — landed via recovery dispatch, not original

---

## Decision tree: which wave does this prompt belong in?

```text
                     Does this prompt depend on
                     another prompt's green commit?
                              │
                ┌──────No─────┴────Yes─────┐
                ▼                          ▼
    Does it share file scope         Does it depend on >1 prompt
    with any sibling prompt?         (multi-merge required)?
        │                                  │
   ┌─Yes─┴─No─┐                  ┌──No─────┴────Yes──┐
   ▼          ▼                  ▼                   ▼
 Wave B    Wave A           Wave B chain          Wave D
 (or       (if first;       (sequential off       (sequential off
 explicit  promote          prior green)          trunk after merge)
 append-   one prompt)
 only;
 Wave C
 with
 warning)

                                  Wave C
                          (parallel off trunk)
                          for any prompt that's
                          file-scope disjoint
                          from siblings AND
                          only depends on
                          completed waves
```

---

## Recovery doctrine inside a wave

When a wave member stalls or fails the gate:

1. **Read the failed worker's report** in full (layered if needed).
   Do not summarize from the truncation warning.
2. **Diagnose**: substrate failure (Living Tree poisoned, missing session
   continuity, broken dependency) vs scope failure (prompt was over-scoped
   or under-specified) vs implementation failure (worker took a wrong cut).
3. **Pick the recovery shape**:
   - Substrate → operator-side fix first, then re-dispatch.
   - Scope → write a _tighter_ brief with reduced acceptance, dispatch
     fresh agent (NOT the failed one — peer-tier, but different rotation).
   - Implementation → focused integration agent: same scope, sharper hints
     about the wrong cut to avoid.
4. **Recovery is a first-class dispatch**, not a retry. It has its own
   prompt body, its own run_id, its own report. Tracker shows the original
   prompt as `failed` and the recovery as a new row with
   `recovers <original-id>`.
5. **Two failures on the same prompt → stop the wave**, write a stop-point
   handoff asking the operator to triage. Three failures is fleet stall —
   surface an honest "I need operator-side guidance" message.

---

## Anti-patterns

- Grouping non-disjoint prompts into a Wave C because "they look small enough"
  → manual merge cost > parallel speedup.
- Firing Wave D before operator-side Wave B+C merge → workers see stale trunk.
- Skipping the tracker update between prompts → operator can't audit progress.
- Bringing your own playbook instead of reading the plan's wave structure →
  the plan author already made these decisions; respect them.
- Treating recovery dispatch as "just fire again" → it's a different brief.

---

## Call to Action

Map every prompt in your plan onto exactly one wave using the decision tree
above. Refuse to fire Wave A until you've assigned every prompt — half-mapped
plans produce half-shaped waves. Then write the tracker scaffold and post it
to the operator before firing. The tracker is the contract.

---

## Closing Rail

```text
=======================
Waves are not a calendar trick. They are how the operator stays able to read
the plan while the agents are still mid-step. Honour the grouping, honour
the merge boundaries, honour the recovery doctrine. The plan does not
forgive shortcuts. (งಠ_ಠ)ง
=======================

Suchar: Why did Wave C never make it to the trunk? Because somebody forgot
to merge Wave B first and the agents threw a tantrum in their pull requests.
(._.)
```

---

_Vibecrafted. with AI Agents (c)2024–2026_
