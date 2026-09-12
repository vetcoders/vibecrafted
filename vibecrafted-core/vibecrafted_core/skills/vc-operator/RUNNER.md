---
name: vc-operator-runner
version: 2.0.0
role: deterministic entrypoint
absorbs:
  - REC-1 (7-step deterministic runner)
  - REC-2 (categorical no native subagents as fleet dispatch)
  - REC-3 (/loop primary cadence)
  - REC-4 (repository-local JOURNAL.md append-only convention)
  - REC-11 (vc-scaffold auto-chain on fuzzy plans)
---

# vc-operator — RUNNER

> **Read this file first. Execute one flow.** Companion docs (`./EMIL.md`,
> `./DISPATCH.md`, `./AWAIT.md`, `./AUTONOMY.md`, `./FRAME.md`, `./GUIDE.md`,
> `./FLOW.md`, `./DASHBOARD.md`) are reference material. They do not gate
> the runner. They explain individual steps when you need depth.

**Framing-shift declaration (mandatory, single line):**

```text
Operator mode active — <plan-name>
```

No 12-line template. One line. Then run the seven steps.

---

## The seven steps

### 1. Read inputs

Consume, in order, every input the operator gave you:

- the operator's prompt itself (the message that invoked `/vc-operator`)
- every plan / report / idea file the operator cited verbatim (read
  full file, not summaries — use Read with offset/limit spans if
  truncated; see `vc-implement` Layered Reading Discipline)
- the active artifact dir for this run:
  `~/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/<plan-slug>/`
- the repository's canonical Operator Journal:
  `<repo-root>/.vibecrafted/JOURNAL.md` — continuity over re-derivation

Dated reports, trackers, transcripts, and run metadata in the artifact dir are
run projections and evidence. They are not another journal system.

Tool calls:

- `Read` on each cited path, full coverage
- `mcp__aicx-mcp__aicx_search` if the operator referenced a prior
  agent session by name or topic
- `Bash` for `ls` of the artifact dir to enumerate existing reports

Output of this step: one paragraph back to the operator naming the
plan, the artifact dir, and the wave count detected.

### 2. Reshape via `vc-scaffold` if the plan is not dispatchable

Categorical trigger — invoke `vc-scaffold` (no inference, no
ad-hoc tightening) when **any** of these holds:

- plan has more than 5 prompts and no wave grouping
- plan has no dependency graph (`depends_on` / `parallel_with` /
  `blocks` missing on any prompt)
- plan has no trackable cuts (acceptance criteria absent or fuzzy)

Tool call:

```text
Skill: vc-scaffold
Args: --input <plan-path> --output <artifact-dir>/master-dispatch.md
```

The scaffold pass produces a normalized `master-dispatch.md` with
wave atlas + per-prompt body skeletons. That becomes your working
plan from step 3 onward. The raw input plan is archived under
`<artifact-dir>/inputs/`.

If none of the three triggers fire, skip to step 3 — the plan is
already dispatchable.

### 3. Verify each cut maps to repo shape via Loctree

For every prompt in the (now-dispatchable) plan, walk its `Files
to create / edit` list (per `./DISPATCH.md` Section 4) and verify
each file via Loctree:

Tool calls:

- `mcp__loctree-mcp__context` once on the project root (atlas
  materialization — first move every operator session)
- `mcp__loctree-mcp__slice` on each file in the cut scope (file
  exists, what depends on it, what it imports)
- `mcp__loctree-mcp__impact` on files marked for delete / rename
  / heavy refactor (blast radius)
- `mcp__loctree-mcp__find` mode `where-symbol` on every shared
  type or contract the plan names (no phantom imports)

If a cut references a file that does not exist and is not in the
`Create:` group → flag back to the operator before dispatch. Plan
is lying about repo shape.

Output: a per-prompt `cut-verified: true|false` annotation appended
to `master-dispatch.md` frontmatter.

### 4. Pick the agent via `WHY_MATRIX_TABLE.md` lookup

For every prompt, resolve `recommended_agent` by lookup, not by
prose-mermaid inference.

Tool call:

- `Read` on `./WHY_MATRIX_TABLE.md` (forward dependency — lands in
  W2-A of this reform wave; until it ships, fall back to the
  mermaid in `./GUIDE.md` and annotate `agent-lookup: provisional`)
- Lookup key: `(task_kind, sensitivity) → agent`
- Apply AGENT FAIRNESS rotation as the tiebreaker — equal
  candidates → rotate Claude → Gemini → Codex across the wave
- Apply AGENT MODEL PARITY as the hard floor — every worker runs
  the operator-agent's tier (Opus parent → Opus worker, no
  exceptions, no "cheap parallel scans")

Output: each prompt in `master-dispatch.md` has a non-empty
`recommended_agent` with a one-line lookup rationale.

### 5. Build the Iter-3 dispatch body via `DISPATCH_TEMPLATE.md`

For every prompt, materialize the twelve-section Iter-3 body by
substituting placeholders in the template.

Tool calls:

- `Read` on `./DISPATCH_TEMPLATE.md` (forward dependency — lands
  in W2-B of this reform wave; until it ships, hand-author per
  `./DISPATCH.md` "The twelve sections" verbatim and annotate
  `template: hand-authored`)
- `Write` each rendered body to
  `<artifact-dir>/briefs/<wave>-<position>_<slug>.md`
- Verify Section 8 (Living Tree etiquette) is the verbatim block
  from `./DISPATCH.md` — never paraphrased
- Verify Section 12 closing rail carries the three required pieces
  (anti-debt one-liner + kaomoji + suchar)

Closing rail is **mandatory for worker-facing briefs**. Operator-
side artifacts (tracker, journal, close-out, stop-point handoff)
do not carry the rail — see `./EMIL.md` Rule 5.

### 6. Fire each prompt via `vibecrafted <mode> <agent> --file <brief>`

Every spawn goes through the framework launcher. No exceptions.

Tool call shape:

```bash
vibecrafted <mode> <agent> --file <artifact-dir>/briefs/<wave>-<position>_<slug>.md
```

Where `<mode>` is the dispatched skill (`implement`, `marbles`,
`research`, `polarize`, `audit`, `dou`, `hydrate`, `decorate`,
`scaffold`, `init`) and `<agent>` is the resolved agent from
step 4.

**Anti-pattern (categorical, REC-2):** never use native subagents
(`Task` tool, `vc-delegate`) as substitutes for dispatched worker
slices in operator mode. Every fleet worker spawn must go through the
framework launcher. Native subagents remain allowed for parallel recon
or small bounded research inside the operator session.

**Rationale (do not rationalize around it):**

- telemetry — every launcher fire writes `meta.json` + transcript
  - report path, native subagents do not
- observability — the receipt, control-plane state, transcript, and report
  remain available while detached workers run headless; vc-frame may project
  those surfaces but does not host the worker. Native subagents do not register
  the same fleet run contract
- recovery — a stalled launcher dispatch has a known recovery
  doctrine in `./AWAIT.md`; a native subagent stall is invisible

Before firing, scan each prompt body for insecure commands and hard-stop
triggers. If a prompt asks for an unpermitted hard-stop action, refuse the
dispatch and record the material fork in
`<repo-root>/.vibecrafted/JOURNAL.md`.

Wave shape per `./GUIDE.md` (A foundation / B sequential / C
parallel / D close-out). Fire one wave at a time. Within a wave,
fire all parallel prompts in a single batch; sequential prompts
wait for the prior commit to land.

### 7. Enter the canonical loop runtime and append `JOURNAL.md`

Use `vibecrafted loop` as the canonical interactive continuation surface
when the operator-agent must keep state across replies:

```bash
vibecrafted loop start --file <artifact-dir>/master-dispatch.md --max-iterations <n> --completion-promise "<done-condition>"
vibecrafted loop next
vibecrafted loop complete --promise "<done-condition>"
```

The default loop state is repo-root anchored at
`<repo-root>/.vibecrafted/operator-loop.local.md`, which is local runtime
state and must not be committed. The loop runtime is a continuation and
await-chain mechanism; it does not replace the operator decision doctrine
in this runner.

After firing the wave, enter `vibecrafted loop` / `/loop` as the **primary** post-
dispatch cadence (REC-3). `ScheduleWakeup` heartbeat is the
**fallback** safety net only — see `./AWAIT.md` for delay table.

Tool calls per wake:

- read the worker's report via `Read` (full file, not the raw
  task output transcript)
- verify the commit landed on `result_branch` via
  `Bash: git log -1 <result-branch>`
- verify gates green by reading the report's gate-output section
- flip `[ ]` → `[x]` in the wave tracker per `./EMIL.md` Rule 1

Append to `<repo-root>/.vibecrafted/JOURNAL.md` only when the wake produces a
material action, decision, evidence change, risk, recovery, integration, or
required acceptance gap. Runtime telemetry already proves routine wakes and
unchanged state; do not restate routine negative work. The journal is
Git-tracked repository truth and only the Operator writes it.

Journal entry shape (per wake):

```markdown
## <ISO-timestamp> — <event-kind>

- run_id: <run-id>
- wave: <wave>-<position>
- agent: <agent>
- decision or action: <material change>
- evidence: <report, gate, SHA, or falsifiable finding>
- risk or acceptance gap: <if present>
- next move: <one-line>
```

Loop cadence: `/loop 25m` is the default for Wave B steps and
Wave C parallels (per `./AWAIT.md` heartbeat table); operator
overrides as needed. `/loop` exits cleanly when the wave-tracker
hits all `[x]` or when step 7 reaches the stop point.

---

## Stop condition

```text
Operator stops at the operator's button for actions not already permitted
by the written plan or current session — force-push, trunk push, merge,
public release, deploy, paid action.
```

See `./AUTONOMY.md` for the hard-stop schedule (git surface +
external surfaces + trust/security/billing + skill/convention
surface) and the stop-point handoff template. Soft stops
(dispatch-shape change, scope skip, scope add, rebase,
cherry-pick) may proceed without a new button when they do not change
the final goal; every such mutation must be recorded in
`<repo-root>/.vibecrafted/JOURNAL.md`.
Scope-changing mutations still require the button.

When the wave tracker is all `[x]` and the next move is operator-side
without written plan/session permission, write the stop-point handoff per
`./AUTONOMY.md` "The stop-point handoff" and exit. Do not force-push, push
trunk, merge, or deploy unless the written plan or current session
explicitly permits it. Non-destructive feature-branch push is already
permitted — do it after authored commits.

---

## Acceptance — the runner is done when

- [ ] step 1 inputs read and acknowledged
- [ ] step 2 either invoked `vc-scaffold` or confirmed plan is
      dispatchable
- [ ] step 3 every cut has `cut-verified: true` annotation
- [ ] step 4 every prompt has `recommended_agent` from lookup
- [ ] step 5 every brief rendered to `<artifact-dir>/briefs/`
- [ ] step 6 every worker fire went through `vibecrafted` launcher
      (native subagents only for recon/research, not fleet dispatch)
- [ ] step 7 `<repo-root>/.vibecrafted/JOURNAL.md` contains every material
      Operator decision; wave tracker is current
- [ ] stop-point handoff written and operator notified

---

## Closing Rail

```text
=======================
Seven steps. No surprises. Read one file, execute one flow.
Companions stand by as reference, never as gate. The operator
owns the button — the runner just walks the agents to it.
(งಠ_ಠ)ง
=======================

Suchar: Why does RUNNER.md never start with "It depends"?
Because the next agent already does. (._.)
```

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
