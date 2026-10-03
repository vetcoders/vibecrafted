# Agent Control Contract

This reference defines the minimum mechanics a Vibecrafted skill must expose
when it is expected to steer an agent, dispatch another agent, or decide whether
work is complete.

It is not a style guide. It is the control surface.

## Required Blocks

Every control-grade skill should include these blocks.

### Iron Law

One sentence that cannot be bypassed.

Examples:

- No fix before root cause evidence.
- No pass without task, code, test, and negative-check evidence.
- No `done` verdict from memory-only evidence.
- No worker dispatch without scope, gates, artifacts, and stop buttons.

### Gate Function

The exact decision rule that lets the agent proceed, stop, downgrade a claim, or
escalate.

The gate must name:

- required inputs
- required evidence
- legal statuses
- illegal shortcuts
- command or artifact that proves the status

### Allowed Statuses

Use finite status vocabulary. Do not let prose invent new states.

Recommended base statuses:

```text
pending
running
reported
verified
blocked
recovery
stop
```

Skill-specific statuses are allowed only when they are defined in the skill.

### Red Flags / Stop

List concrete situations that force a pause, status downgrade, recovery step, or
handoff.

Good red flags are observable:

- target file does not exist
- launcher cannot start
- test failure changed category
- Loctree snapshot is stale and fallback was used
- agent lacks required artifact
- user-facing install path cannot be proven

Weak red flags are moods:

- feels risky
- probably bad
- maybe enough

### Output Contract

Define the required final artifact.

The contract should specify:

- file path or artifact destination
- fields or headings
- allowed verdicts
- evidence format
- next legal skill or phase

If the skill dispatches agents, the worker output should be machine-checkable
enough for the operator to integrate without rereading the whole transcript.

### Acceptance Criteria

Acceptance criteria must be verifier-backed.

Use:

- exact command
- exact file path
- exact artifact path
- exact runtime action
- exact screenshot or transcript requirement

Avoid:

- "looks good"
- "should work"
- "probably enough"
- "best effort"

## Workflow Prompt Shape

Use this shape when generating a worker prompt or workflow prompt.

```yaml
---
workflow_prompt_version: 1
run_id: <id>
parent_run_id: <id|null>
skill: <vc-workflow|vc-marbles|vc-audit|...>
phase: <scaffold|implement|review|workflow|followup|marbles|audit|polarize|dou|hydrate|release>
mode: <READ|WRITE|META>
agent: <codex|claude|gemini|junie|agy|grok|cursor>
project_root: <abs-path>
branch_head: <branch@sha>
artifact_root: <abs-path>
report_path: <abs-path>
upstream_artifacts: [<paths>]
downstream_consumer: <next skill/phase>
wave: <id|null>
position: <n|null>
vector: <stabilize|implement|recon|e2e|research|release>
state: <pending|running|reported|verified|blocked|recovery|stop>
depends_on: []
parallel_with: []
blocks: []
permissions:
  source_write: <true|false>
  git_commit: <true|false>
  push_pr_deploy: false
gates:
  - <exact command>
stop_buttons:
  - push
  - merge
  - deploy
  - public promise
---
```

Then write the body in this order:

1. Role and mode.
2. Mission.
3. Inputs to read, in order.
4. Baseline truth.
5. Scope, out-of-scope, forbidden changes.
6. Target surfaces.
7. Acceptance criteria.
8. Cadence contract.
9. Tool order.
10. Execution protocol.
11. Failure and recovery.
12. Artifact contract.
13. Completion condition.
14. Imperative call to action.

## Mode Rules

### READ

- May inspect code, docs, reports, logs, and runtime output.
- Must not edit source files.
- Must not commit.
- Default verdict is `unverified` until evidence proves otherwise.
- Final output must separate evidence, inference, and open risk.

### WRITE

- May edit scoped source or artifact surfaces.
- Must re-read touched files in a living tree before editing.
- Must run the closest real gate.
- Must report verification and non-verification.
- Must not push, deploy, or publish unless the skill explicitly grants that
  button.

### META

- Dispatches or coordinates agents.
- Must not silently become the worker.
- Must write or update tracker state.
- Must wait for required artifacts before verification.
- Must stop at push, merge, deploy, and public-promise buttons unless the
  operator explicitly grants them.

## Skill Lint Targets

A control-grade skill should fail review when:

- it has no Iron Law
- it has no Gate Function
- it has no finite status vocabulary
- it uses `maybe` as a policy state
- it claims completion without an Output Contract
- it tells workers to be "careful" without naming evidence
- it mentions a migration TODO in a plugin manifest description
- it delegates without acceptance criteria
- it dispatches agents without artifact paths
- it uses broad autonomy without stop buttons

## Language Policy

Strong control language is good:

- Do not hide uncertainty.
- Report the failed gate.
- Block repo-specific work until repo truth exists.
- Downgrade the claim to unverified.

Weak or apologetic control language should be rewritten:

- "maybe"
- "probably"
- "best effort" without a bounded fallback
- "if the operator asks for evidence"
- "does not expose that same hook" when the policy can be framed positively

The goal is not bland tone. The goal is executable truth.
