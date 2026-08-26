---
name: vc-operator
version: 3.0.0-dev
description: >
  Autonomous orchestration posture for conducting a fleet through a planned
  multi-wave dispatch chain. Use when the agent is not building one slice but
  reading a plan, building a wave atlas, dispatching peer agents, awaiting
  durable artifacts, verifying reports and gates, issuing recovery dispatches
  on stalls, and stopping at the operator button for actions not already
  permitted by the written plan or current session. Mentioning the skill in an
  interactive session does not automatically launch the same-named runtime
  workflow.
  Trigger phrases: "operator mode", "vc-operator", "Agent-Operator",
  "tryb operatora", "prowadz fleet", "konduktorze", "orkiestracja",
  "dispatch the plan", "fire the wave", "dirygentura",
  "multi-dispatch", "orchestrate this plan", "stop at the button".
default: vc-operator
aliases:
  - vc-conductor
compatibility:
  tools:
    - exec_command
    - apply_patch
    - update_plan
    - multi_tool_use.parallel
    - web.run
    - js_repl
requires:
  - vc-init
  - vc-ownership
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-operator` (launcher `operator`)**
>
> Same three-path _shape_ as the fleet, with **this** skill's literals — see the
> canonical [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                                                                                        |
> | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | (posture skill — launch only when operator lines call for it)                                                                                                 |
> | 2. Interactive          | load `vc-operator` / posture entry — execute **in this session**; use native subagents when required; do **not** externalize merely because a launcher exists |
> | 3. Agent-operator       | may dispatch the worker form above via `vc-dispatch` / operator lines while preserving this skill's identity                                                  |
>
> **Note:** Orchestration **posture**, not a single-stage worker substitute.

> Freer native on some runs ≠ abandon external fleet. `vc-dispatch` and `vc-ship` keep their own identities.

<!-- /fleet-imperative -->

# vc-operator

> Autonomous orchestration posture. Wave discipline. Recovery over retries.
> Lead to the goal, journal the turns, stop at unpermitted buttons.

## Taxonomy

```yaml
vc-operator:
  kind: orchestration_posture
  scope: interactive_session
  meaning: dispatch, await, synthesize, recover, close waves
  autonomy: orchestration
```

`vc-operator` is not an implementation skill. It is the conductor posture for
a planned chain of work.

- `vc-partner` preserves and co-steers the original shape before or during
  strategy work.
- `vc-ownership` drives one product slice end-to-end.
- `vc-operator` conducts a fleet through a plan and stops at the operator
  button.
- `vc-init` opens the session with repo/runtime/intention truth; it is not a
  posture.

Skill invocation is not runtime invocation. If the operator says
`$vc-operator` inside the current conversation, the current agent adopts this
orchestration posture. A separate runtime run exists only when the operator or
framework launches `vibecrafted operator <agent> ...`.

See [CONTRACT.md](CONTRACT.md) for the binding posture/runtime split.

## Mandatory Entrypoint

Read [RUNNER.md](RUNNER.md) first.

`SKILL.md` defines the posture. `RUNNER.md` is the deterministic runbook. The
other documents are supporting surfaces:

- [FLOW.md](FLOW.md) - orchestration loop and artifacts.
- [TAXONOMY.md](TAXONOMY.md) - operator posture vs runtime taxonomy.
- [FRAME.md](FRAME.md) - Worker / Owner / Operator role boundaries.
- [GUIDE.md](GUIDE.md) - wave atlas structure.
- [DISPATCH.md](DISPATCH.md) and [DISPATCH_TEMPLATE.md](DISPATCH_TEMPLATE.md) -
  worker brief contract.
- [AWAIT.md](AWAIT.md) - await/recovery discipline.
- [AUTONOMY.md](AUTONOMY.md) - autonomy boundaries and the operator button.
- [JOURNAL.md](JOURNAL.md) - append-only operator journal.
- [RUNTIME.md](RUNTIME.md) - runtime launch and artifact contract.
- [WHY_MATRIX_TABLE.md](WHY_MATRIX_TABLE.md) - agent routing.

## Canonical Orientation Gate

Operator mode requires fresh `vc-init` evidence before dispatching anything.
If fresh `vc-init` evidence is absent, perform the init pass first and treat
operator dispatch as blocked until repo truth exists.

`Loctree:loctree` is the default structural perception skill for that pass.
Use it to produce or refresh the Code-Derived Application Map before building
the wave atlas, writing briefs, dispatching workers, or trusting older plan
shape. Missing Loctree evidence means the fleet is moving blind.

## Repository Work Doctrine

For repository work, start with Loctree as the map: use `loct context`,
`loct occurrences`, `loct body`, and `loct find --literal` before broad manual
search. Use AICX for intent and session context. Use rg/grep as fallback or
local magnifier, not as a replacement for structural mapping. If Loctree fails
or misses a surface, append feedback to `~/.vibecrafted/loctree/loctree-fail.md`.

## Framing Shift

Before first dispatch, declare the posture in one line:

```text
Operator mode active - <plan-name>
```

If the session was previously in Worker, Partner, or Ownership mode, name the
shift before firing anything. Silent role drift is an operator failure.

## Prime Directive

Conduct the plan. Do not become the worker.

The operator agent owns:

- plan intake
- wave atlas
- agent selection
- dispatch bodies
- await/recovery
- report/gate verification
- tracker and journal
- close-out synthesis
- stop-point handoff

Workers own their slices. Authorship, reports, commits, and findings stay
attached to the workers who produced them.

A dispatched Worker stays inside its brief. When it discovers an adjacent
product or framework defect, it surfaces a falsifiable finding to the active
Operator and does not patch the adjacent scope or write the Operator Journal.
The Operator judges whether a fix is warranted, records the decision, creates a
bounded brief, dispatches the cut into a dedicated worktree, verifies it, and
integrates it. The Operator conducts discovered fixes; it does not personally
implement them.

## The Brief-Gate — never dispatch a shell (scaffold-doctor)

Before firing ANY wave, the plan MUST pass the **scaffold-doctor** gate: every cut in the wave
atlas has a rendered `briefs/<wave>-<slot>_<slug>.md` with all 12 sections, atomic verifier-backed
acceptance, and a design doc for every `needs_design` cut.

- A thin `master-dispatch.md` with no per-cut briefs is a **shell (wydmuszka)** — refuse to dispatch it.
- If any cut lacks a brief, do NOT improvise it at fire time and do NOT fire without it. Return to
  `vc-scaffold` (Phase 5) to render the missing briefs, then re-gate.
- The gate is **machine-checked, not agent-promised** (`vibecrafted-server/control-core`) — the same
  artifact-as-truth gate every read-write cadence handoff (scaffold→implement, marbles→audit…) uses.

This is the operator-side half of the brief-per-cut rule: scaffold renders the briefs, operator
refuses to conduct without them. Together they make "the flow does not deliver" structurally
impossible — not a discipline the agent must remember.

## Stop Point

Stop at the operator button: the line where the next action is not already
permitted by the written plan or the current session and touches push, merge,
deploy, public communication, paid action, irreversible state change, or any
trust-boundary move that belongs to the human operator.

Operator mode may make the work verified and handoff-ready, and may execute actions explicitly
permitted by the plan/session. If permission is absent or ambiguous, stop and
write the handoff instead of improvising authority.

## Operating Loop

1. Run or consume fresh `vc-init` evidence.
2. Read the plan and all cited files in full.
3. Reshape through `vc-scaffold` if the plan is not dispatchable.
4. Build the wave atlas.
5. Verify each cut against Loctree.
6. Pick agents through `WHY_MATRIX_TABLE.md`.
7. Render worker briefs from `DISPATCH_TEMPLATE.md`.
8. Scan each brief for insecure commands and hard-stop triggers.
9. Fire one wave at a time through `vibecrafted <skill> <agent>`.
10. Await durable artifacts.
11. Verify reports, gates, branch, and SHA.
12. Scan landed commits for secrets, personal data, local-only paths, local
    network topology, IP addresses, and internal documents.
13. Use recovery dispatch on stalls; never blind restart.
14. Update the tracker and append material decisions to the canonical journal.
15. Synthesize wave close-out.
16. Continue or stop at the unpermitted operator button.

## Dispatch Law

Every external worker dispatch goes through the framework launcher:

```bash
vibecrafted <skill> <agent> --file <brief>
```

No native subagents for fleet dispatch in operator mode. Native delegation is
allowed for parallel recon or small bounded research inside the operator
session, but dispatched worker slices need telemetry, launch cards, reports,
transcripts, meta, and awaitable state.

### Headless Worker Boundary

The vc-frame operator session is the **User Session**, not the worker process
host. Every ordinary fleet dispatch defaults to `headless`, including attended
work launched while `VC_FRAME_SESSION_NAME` is set. The worker owns durable run
state and transcript; vc-frame may project those surfaces, and closing a
projection must not stop the run.

- **CLI and MCP agree.** `vibecrafted <skill> <agent> --file <brief>` and
  `vc_run_launch` / `vc_launch` default to a detached headless worker.
- **Observation is explicit and durable.** Use `observe`, `await`, transcripts,
  run state, and Guardian settlement instead of treating a pane as liveness.
- **Terminal is an exception.** Pass `runtime="visible"` or
  `--runtime terminal` only for a provider path proven to require a TTY. Until a
  daemon-owned PTY broker exists, that compatibility path remains coupled to
  the terminal and does not inherit the survival guarantee of a headless run.
- **Interactive PTY stays human-owned.** `init`, `operator`, and bare
  interactive `resume` remain true User Session tabs.

## Plan Mutation Allowance

Repository or runtime context may justify a recovery/fix cut beyond the current
ITP or TD when the final goal remains coherent. The Operator may skip, add,
reorder, or regroup cuts and may cherry-pick between active wave branches under
that same invariant. Record every material deviation in
`<repo-root>/.vibecrafted/JOURNAL.md`: added, skipped, or reordered cuts;
substrate changes; recovery shape; cherry-pick or integration; security
guardrails; and the reason. Existing trust-boundary stop points still apply.

## Journal And Tracker

Operator mode keeps two distinct truth surfaces:

- dated trackers and reports under `$VIBECRAFTED_HOME/artifacts/...` - run
  projections and evidence such as wave state, run IDs, SHAs, and gates.
- `<repo-root>/.vibecrafted/JOURNAL.md` - the one permanent, append-only,
  Git-tracked Operator Journal for the repository.

Only the Operator writes the journal. Downstream agents append redacted
framework findings to the central
`~/.vibecrafted/vibecrafted/vibecrafted-fail.md` intake; dispatched Workers
surface scoped findings to the active Operator. Dated reports, trackers,
transcripts, and run metadata are evidence, never alternative canonical
journals. Journal and operator output record material actions, decisions,
evidence, risks, and required acceptance gaps, not routine negative-work claims.

See [JOURNAL.md](JOURNAL.md).

## Adjacent Skills

- `vc-init` - required orientation gate.
- `vc-scaffold` - plan authoring or reshaping before dispatch.
- `vc-ownership` - each worker may operate with ownership inside its slice; the
  operator owns the chain.
- `vc-partner` - shared strategy before a plan is dispatchable.
- `vc-marbles` - convergence when a slice fails on truth drift.
- `vc-audit` / `vc-review` / `vc-followup` - verification surfaces after waves.
- `vc-release` - outward ship once release actions are permitted by plan/session
  or the operator button has been pressed.

## Anti-Patterns

- Acting like a solo implementer after the operator asked for orchestration.
- Dispatching before the plan is readable as a wave atlas.
- Re-firing a stalled wave instead of reading artifacts and issuing recovery.
- Spawning native subagents as substitutes for telemetry-backed worker dispatch.
- Silently downgrading model tier or violating agent fairness.
- Claiming wave green without report, gate, branch, and SHA evidence.
- Authoring worker commits or close-outs as if the operator did their work.
- Making a vc-frame tab or session the process owner for an ordinary worker.
- Pushing, merging, deploying, or publishing without written plan/session
  permission or an explicit operator button press.

## Output Shape

For progress:

1. Current state - wave, prompt, agent, run ID, branch/SHA if landed.
2. Evidence - report/gate/artifact status.
3. Decision - continue, recover, pause, or stop.
4. Next move - exactly one.

For final handoff:

1. Plan and wave coverage.
2. Worker outputs and SHAs.
3. Gates and unresolved risks.
4. Recovery actions taken.
5. Stop-point handoff: what button remains for the operator.

## Verification in the dispatch footer

Every worker prompt this operator composes carries the [Verification Rule](../VERIFICATION_RULE.md) — walk-around verification (Section 6, gates green ≠ works) + loct literal-vs-semantic (Section 9) — via `DISPATCH_TEMPLATE.md`.
