# `vc-operator` Journal

The Operator Journal is the permanent, append-only repository decision record
for orchestration.

It records material actions, decisions, evidence, risks, recoveries,
integrations, and required acceptance gaps. It complements dated run evidence
without duplicating it.

## Path

```text
<repo-root>/.vibecrafted/JOURNAL.md
```

This is the one canonical journal per repository. It is deliberately tracked
by Git. Other files under repo-local `.vibecrafted/` remain ignored runtime
state.

Related artifact:

```text
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/<run>/tracker.md
```

## Journal vs Tracker

| Artifact                | Purpose                                                          |
| ----------------------- | ---------------------------------------------------------------- |
| dated tracker/report    | run state, run IDs, branches, SHAs, gates, transcripts, metadata |
| repository `JOURNAL.md` | material Operator decisions and repository mission history       |

The tracker answers "what landed?".
The journal answers "why did the operator do that next?".

## Rules

- Append only; corrections are new entries.
- Only the active Operator writes this journal.
- Record material dispatches, decisions, evidence, risks, recoveries,
  integrations, security guardrails, and required acceptance gaps.
- Record every material deviation from the current ITP or TD: an added,
  skipped, or reordered cut; substrate change; recovery shape; cherry-pick or
  integration; and its reason.
- A justified recovery/fix cut may extend beyond the current ITP or TD when the
  repository/runtime context supports it and the final goal remains coherent.
- Existing trust-boundary stop points still apply.
- Dated artifact reports, trackers, transcripts, and run metadata are
  projections/evidence, not alternative journals.
- Downstream agents append redacted framework findings to
  `~/.vibecrafted/vibecrafted/vibecrafted-fail.md`.
- A dispatched Worker stays inside its brief, gives the active Operator a
  falsifiable finding, does not patch adjacent scope, and does not write this
  journal.
- The Operator judges the finding, records the decision, creates a bounded
  brief, actively dispatches the fix into a dedicated worktree, verifies it,
  and integrates it. The Operator does not personally implement a discovered
  fix.
- Do not record routine negative-work claims. Git, runtime metadata, receipts,
  and reports already prove them.
- Worker-facing closing rails do not appear in operator journal entries.
- Do not collapse separate worker states into a vague wave status.

## First Entry

````md
## <timestamp> - operator mode active

```yaml
operator_run:
  plan_name: ""
  repository: ""
  source_plan: ""
  init_evidence: ""
  stop_point: "operator button"
```

- State:
- Wave atlas:
- Next:
````

## Dispatch Entry

```md
## <timestamp> - fire wave <n>

- Wave:
- Briefs:
- Agents:
- Run IDs:
- Dependency state:
- Await path:
```

## Recovery Entry

```md
## <timestamp> - recovery dispatch

- Stalled run:
- Failure class:
- Evidence:
- Recovery brief:
- Recovery agent:
- Expected close condition:
```

## Plan Mutation Entry

```md
## <timestamp> - plan mutation

- Changed:
- Why:
- Final goal unchanged because:
- Evidence:
- Next:
```

Use this shape for added, skipped, or reordered cuts, substrate changes,
recovery shape, and cherry-pick/integration decisions.

## Discovered-Fix Entry

```md
## <timestamp> - discovered fix decision

- Worker finding:
- Operator decision and reason:
- Bounded brief:
- Dedicated worktree dispatch:
- Verification and integration evidence:
- Risk or acceptance gap:
```

## Security Guardrail Entry

```md
## <timestamp> - security guardrail

- Surface: prompt | commit
- Detected:
- Action taken:
- Recommit SHA, if applicable:
- Next:
```

## Close-Out Entry

```md
## <timestamp> - wave <n> close-out

- Landed:
- Branches:
- SHAs:
- Gates:
- Risks:
- Next wave or stop reason:
```

## Stop-Point Entry

```md
## <timestamp> - stop at operator button

- Completed waves:
- Remaining human button:
- Evidence:
- Risks:
- Recommended next action:
```
