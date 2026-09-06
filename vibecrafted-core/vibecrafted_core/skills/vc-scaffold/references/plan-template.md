# SCAFFOLD.md Template

Use this template for planning output. Strip out the comments in your actual output.

```markdown
---
run_id: <generated-unique-id>
agent: <claude|codex|gemini|cursor>
skill: <vc-scaffold|vc-workflow|vc-implement>
project: <repo-name>
status: pending
vector: <stabilize|implement|recon|e2e> # selects the gate profile = what counts as delivery
created: <ISO-8601 timestamp>
founder_interview_evidence: <journal path | AICX session/extract | current-conversation answers>
dispatch_artifact: <absolute plan root>/<plan-id>.dispatch.toml
---

# Architecture Plan: [Project Name]

## Problem Statement

[1-2 sentences. What problem are we solving? Why does it matter?]

Example: "The monolith is becoming unmaintainable. We need to extract the payment service into its own service so teams can ship independently without coordinating deploys."

## Key Architectural Decisions

### Decision 1: [Name]

**Choice:** [What we're doing]
**Trade-off:** [What we're giving up]
**Why:** [Why this is better than the alternative]

### Decision 2: [Name]

**Choice:** [What we're doing]
**Trade-off:** [What we're giving up]
**Why:** [Why this is better than the alternative]

(Keep to 3-5 decisions. Not every technical detail.)

## Scope Boundaries

### Phase 1: MVP (This Sprint/Cycle)

**In scope:**

- Feature/component A
- Feature/component B
- Test infrastructure

**Out of scope:**

- Feature X (nice to have, ships phase 2)
- Optimization Y (not blocking MVP)

**Explicitly out of scope:**

- Rewrite of the old system (not happening)
- Migrate to language Z (out of bounds)

## Architecture Overview

[ASCII diagram or brief description]

Example:
```

User → API Gateway → Auth Service → Payment Service → Stripe
↓
Cache Layer
↓
Database

```

## Task Breakdown

Each task is agent-ready. Agents execute in parallel when dependencies allow. Each task carries a
`state` marker `[ ] [~] [?] [!] [x]` (see references/measure-core.md); only a delivery-verifier flips
`[~]→[x]`. vc-operator reads the `state` column to trigger/stop.

### Task 1: [Imperative title]   `state: [ ]`
**Vector:** [stabilize|implement|recon|e2e]
**Produces:** [What code/config/tests get created]
**Depends on:** [Task X, infrastructure ready]
**Owner:** [Agent skill or human role]
**Delivery-verifier:** [the non-fakeable test that flips [~]→[x]; without it the task ships as [?]]
**Acceptance:** [intent vs baseline — what proves delivery ≈ claim, not just "agent said so"]
**Pre-handoff baseline:** [branch + HEAD + git status + changed files + gates/known failures + exact next instruction]

Example:
```

Task: Build authentication middleware state: [ ]
Vector: implement
Produces: /middleware/auth.ts, /tests/auth.test.ts
Depends on: Infrastructure up, database schema
Owner: Core backend agent
Delivery-verifier: `pnpm test auth` green — rejects invalid tokens, passes valid; flips [~]→[x]
Acceptance: intent (auth enforced on all routes) vs baseline (routes open); delivery proven by the verifier, not "agent said so"
Pre-handoff baseline: branch, HEAD, git status, changed files, verifier output, known failures, next instruction

````

## Dispatch Contract

The plan root contains `<plan-id>.dispatch.toml` with `schema = "vibecrafted.dispatch.v1"`. It maps every
task above to one `[[cuts]]` entry with its dependencies, agent/workflow, brief-backed prompt, and
delivery-verifier. `vibecrafted dispatch <absolute-plan-root>/<plan-id>.dispatch.toml --doctor`
must pass before handoff. Multi-cut execution belongs to `/vc-ship` A→Z.

If the plan uses compile embargo, include the explicit Founder authorization, phase marker,
deferred-gate list, temporary structural evidence, checkpoint procedure, named release attestation,
and local worker-commit report required by `references/compile-embargo.md`. A selective
repository-owned hook policy is preferred when available; its absence does not block the embargo
or require a new policy system first. The worker report must state what ran and what was skipped.
The plan must distinguish the local checkpoint, integrator structural admission (exact SHA/scope,
Semgrep, and secret/security review; deferred compile/lint/type/test gates still skipped), and
verified delivery after named closure and the full language-appropriate gate set.

## Test Gates (per Vector profile)

Each phase has a delivery-gate selected by its `Vector` (see references/measure-core.md) — the gate
defines what counts as delivery, so it differs by Vector. Don't advance a phase until its gate flips
every cut `[~]→[x]`.

- **implement** → feature works + tests green on core paths
- **stabilize** → the bleeding stops + a regression/canary gate green (busy ≠ dead)
- **recon** → map/answer delivered with evidence refs
- **e2e** → the full path runs end-to-end
- **always** → no exposed secrets; integrator structural admission records Semgrep and
  secret/security review, while verified delivery after closure records the full language-appropriate gates

## Living Tree Note

This plan is alive. It changes as we learn. When you change the plan:

1. **Date** the change
2. **Explain why** (new constraint, discovered dependency, market shift)
3. **Re-run task breakdown** if scope changed
4. **Update acceptance criteria** if definitions shifted

Document the reasoning. Future engineers will thank you.

---

## Running This Plan

`<plan-id>.dispatch.toml` is the only execution contract. Validate it, then hand that exact artifact
to `/vc-ship`; do not launch cuts manually:

```bash
vibecrafted dispatch <absolute-plan-root>/<plan-id>.dispatch.toml --doctor
vibecrafted dispatch <absolute-plan-root>/<plan-id>.dispatch.toml --dry-run --json
````

`/vc-ship` owns A→Z start, supervision, resume/recovery, verifier gates, and completion through the
deterministic dispatcher. This section must contain no direct start/resume recipe and no per-cut
`vibecrafted workflow ... --prompt` recipe.

### Emergency manual fallback

Only when `/vc-ship` or its supervisor is demonstrably unavailable, record the exact failure and
why the fallback is necessary before giving a bounded direct-dispatch or per-cut recovery command.
Record how control returns to `/vc-ship`; never let the fallback become a second execution path.

No handwaving. Clear work. Clear criteria. That's how founders ship.

```

```
