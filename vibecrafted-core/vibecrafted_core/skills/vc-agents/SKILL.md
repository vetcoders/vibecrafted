---
name: vc-agents
version: 3.1.0
description: >
  Spawn external specialized AI agents from the user's fleet (Codex, Claude, Gemini).
  Use this when you need parallel execution, deep isolation, or task-specific cognitive
  strengths that surpass generic in-thread delegation.
  Trigger: "vc-agents", "/vc-agents", "delegate to agents", "spawn".
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-agents` (launcher `agents`)**
>
> Same three-path _shape_ as the fleet, with **this** skill's literals — see the
> canonical [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                                                                                  |
> | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | (fleet contract — external modes via documented spawn paths)                                                                                            |
> | 2. Interactive          | load `vc-agents` as doctrine — execute **in this session**; use native subagents when required; do **not** externalize merely because a launcher exists |
> | 3. Agent-operator       | may dispatch the worker form above via `vc-dispatch` / operator lines while preserving this skill's identity                                            |
>
> **Note:** External fleet **contract**; interactive skills still execute in-session.

> Freer native on some runs ≠ abandon external fleet. `vc-dispatch` and `vc-ship` keep their own identities.

<!-- /fleet-imperative -->

# vc-agents — The External Execution Fleet

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
the work is blocked. `Loctree:loctree` builds the Code-Derived Application Map
(repo-view/focus/slice/impact/find/follow; search before creating, impact
before deleting, slice before editing). Missing evidence is a process failure;
full gate: [../vc-init/SKILL.md](../vc-init/SKILL.md).

Operator enters the framework session through:

```bash
vibecrafted start
# or
vc-start
# same default board as: vc-start operator
```

`vc-agents` is the delegation contract behind active workflows, not the primary
operator command a founder types first. The operator-facing entrypoint stays:

```bash
vibecrafted <launcher> <agent> \
  --<options> <values> \
  --<parameters> <values> \
  --file '/path/to/plan.md'
```

```bash
vc-<launcher> <agent> \
  --<options> <values> \
  --<parameters> <values> \
  --prompt '<prompt>'
```

`vc-<launcher> <agent>` launches a detached headless worker whether or not
vc-frame is live. The User Session may project its transcript and state, but it
does not host the process. `vc-agents` defines how that launcher run fans out
into external workers.

### Concrete dispatch examples

```bash
vibecrafted implement codex /path/to/plan.md
vibecrafted implement claude /path/to/plan.md
vibecrafted implement gemini /path/to/plan.md
```

> We do not outsource thought. We deploy equally capable minds on parallel execution paths to protect the main context buffer.

A single agent session carries immense context. Attempting to execute every small rewrite, forensic deep-dive, or radical structural shift in-thread causes prompt bloat and dilutes your focus.

`vc-agents` is the external delegation layer. You identify the structural gap,
pick the right mind for the job from the **`vc-why-matrix`**, spawn the
autonomous external worker, and return to your main orchestration.

This skill is only for external workers. Native in-process delegation belongs to
`vc-delegate`, not here.

## Repository Work Doctrine

Loctree first (`loct context/occurrences/body/find --literal`), AICX for
intent history, rg/grep as local magnifier only; Loctree gaps go to
`~/.vibecrafted/loctree/loctree-fail.md`.

## The `vc-why-matrix`

You do not spawn agents blindly. You pick the cognitive profile required for the cut.

Historical core trio (diagram in [references/why-matrix.md](references/why-matrix.md)):
Codex = precision & surgery · Claude = forensics & research · Gemini/agy =
radical reframing + text default. Current roster is EIGHT agents; pick per the
Founder's declared economics for the day, never by habit.

**Words are their own cognitive profile.** Prose, docs, narrative, skill / marketing copy, translation, and
human-facing wording → **Gemini** (the text default) or **Claude**. Codex's edge is precision surgery on code
and contracts — register and voice aren't its lane, so for a mixed cut split the work: Codex takes the
mechanical / code part, Gemini or Claude take the words. This is matching the mind to the work, never a verdict
on any agent.

## Delegation Doctrine

- **Delegate, do not micromanage:** Do not produce 15-point bureaucratic checklists for the spawned agent. Write a high-level plan with `Goal`, `Scope`, and `Acceptance Criteria`. Let them figure out the _how_.
- **The Living Tree:** Agents must know they operate in a live system. Ensure your spawn plan states: _"You are working on a living tree. Concurrent changes are expected. Adapt proactively."_
- **Full Replacement over Scar Tissue:** Tell your agents they are empowered to rewrite broken abstractions. Sometimes a full replacement is cleaner than patching over bad prototype code.

## Escalation Authority

`vc-agents` is an operator-level orchestration layer.

The decision to use `vc-agents` already encodes `vc-why-matrix` intent:
the operator selected a specific model family and cognitive profile for the
mission.

Because of that:

- spawned fleet agents must not call `vc-agents` again on their own
- spawned fleet agents must not re-open model selection or launch a second external fleet
- spawned fleet agents must not reinterpret the `vc-why-matrix`
- escalation into `vc-agents` belongs exclusively to the operator agent

If a spawned worker discovers that the mission surface is wider, more parallel,
or less bounded than expected, it should not self-escalate outward.

Instead it must:

- complete the assigned mission as far as honestly possible
- record the boundary it encountered
- name the unresolved surface clearly in its report
- leave any orchestration change to the operator

A fleet worker may reveal orchestration pressure.
It may not act on it.

**This prohibition covers the EXTERNAL fleet only.** A worker's native
in-process subagents (Claude's Task tool via
[`vc-delegate`](../vc-delegate/SKILL.md), Kimi's swarm, a runtime's own
sub-session lane) are its right and — when the plan parallelizes — its duty.
Workerhood constrains run scope and lifecycle, not native delegation rights
(Delegation Matrix → Native vs external). Do not read "execution unit" as
"serial unit": a plan with disjoint subcuts executed one-by-one on a frontier
model is the most expensive possible way to be slow.

## Snap-dispatch integrator loop (wzorzec Foundera, 2026-10-03)

Highest-throughput operator pattern (full text:
[references/runbook.md](references/runbook.md)): (1) integrator authors a
dense plan carrying MEASURED evidence — paths, record shapes, control
numbers, line-pinned anchors — so the worker rediscovers nothing; (2) Founder
canon recovered from AICX is marked "implement exactly, never reinterpret";
(3) one launcher snap per cut, text prefix before the plan body, env scrub,
await armed immediately; (4) integrator gate on return: re-run tests in the
worktree, live-probe the surface, settle every red by BASELINE-DIFF on clean
HEAD before attributing; merge `--no-ff`, push, report provider cost per
settle; (5) model/effort are the Founder's cost buttons — a provider 400
means STOP and ask, never substitute.

## Plan template

Use the canonical template in [references/plan-template.md](references/plan-template.md) —
frontmatter (`run_id`, `agent`, `skill`, `project`, `status`), then `Goal`,
`Scope`, `Constraints`, `Acceptance`, `Test gate`, `Context`, and the living
tree note (concurrent changes expected; one commit per round is an
obligation; native in-process fan-out is exempt from the no-orchestration
rule — tiers per `vc-delegate` → Native Delegation Policy).

## Runbook — current launcher grammar

Agents: `claude · codex · agy · junie · grok · cursor · kimi · copilot`.
Full grammar, composed-prompt idioms (plan-from-file with a text prefix,
injecting a live CLI's `--help` into the mission), the Claude-session env
scrub, and companion verbs (`await`/`observe`/`stop`/`usage`) live in
[references/runbook.md](references/runbook.md). The shape to remember:

```bash
vibecrafted <launcher> <agent> --repo "$(pwd)" --model <founder's cost pick> \
  [--effort <tier>] --worktree true --prompt "Plan follows.\n\n$(cat "$PLAN")"
```

Arm `vibecrafted await <agent> --run-id <id>` immediately after dispatch and
quote `vibecrafted usage --run-id <id>` (provider-reported cost) in every
settle. If these tools are unavailable, stop pretending spawn is correctly
configured and say so explicitly.

## Output convention & observation

Artifact paths (plans/reports/transcripts/meta under
`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`), the launch-card
contract, and the full await/3-signal liveness doctrine are in
[references/runbook.md](references/runbook.md). Non-negotiables: every spawn
surfaces a launch card; await is armed supervisor-side immediately; hedging
await with ad-hoc pollers is a Class 3 violation; two agreeing liveness
signals act, three declare done.

## Quality gate expectations

Keep the standard 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. quality bar:

- loctree-mcp as first-choice exploration and search tool with fail-fast if inaccessible
- semgrep as first-choice security guard when available
- Rust repos: `cargo clippy -- -D warnings`
- Non-Rust repos: choose the closest equivalent lint/type/test gate
- Tests: run if reviewing; write if implementing new behavior; prefer real e2e coverage for the actual pipeline
- If a gate is blocked, report the exact blocker and run the closest safe equivalent

## Safety rules

- Do not log secrets or commit `.env` files.
- Use `--no-verify` only for a declared Founder-authorized compile-embargo
  local checkpoint whose receipt names skipped hooks and gates. Workers never
  push with it; a push using `--no-verify` is Founder-only.
- Do not rewrite git history unless the user explicitly asks.
- Treat concurrent edits as normal, but still verify before overwriting.
- If a repo has a strict command such as `make check`, run it or explain why not.

## Final principle

Fleet is not for outsourcing thought.
Fleet is for deploying equally capable front-line agents through a strict, default launch path.
Use them to implement, not merely to comment on implementation.
