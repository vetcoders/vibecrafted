# `vc-operator` Contract

This document is the binding contract behind the readable operator flow.

## Layer Contract

```yaml
interactive_skill:
  name: vc-operator
  kind: orchestration_posture
  activates_when:
    - user names "$vc-operator"
    - user asks to conduct a plan or fleet
    - user asks for multi-wave dispatch
  does_not_automatically:
    - launch "vibecrafted dispatch"
    - create a run_id
    - create transcript/meta artifacts
    - fire workers

runtime_supervisor:
  name: vibecrafted dispatch
  kind: runtime_supervisor
  activates_when:
    - operator launches "vibecrafted dispatch <file.toml>"
    - operator launches "vibecrafted dispatch run ..."
    - framework dispatches a supervisor run explicitly
  creates:
    - run_id
    - dispatch tracker/result artifacts
    - reports
    - briefs
    - transcript.log
    - meta.json
```

## Posture Contract

```yaml
vc-operator:
  owns:
    - plan_intake
    - wave_atlas
    - agent_selection
    - dispatch_briefs
    - await_and_recovery
    - tracker
    - repository_operator_journal
    - wave_close_outs
    - stop_point_handoff
  may_launch_runtime:
    - vc-scaffold
    - vc-implement
    - vc-workflow
    - vc-marbles
    - vc-audit
    - vc-review
    - vc-followup
    - vc-release
    - vc-ownership
    - vc-partner
  must_not:
    - silently become a worker
    - dispatch without vc-init evidence
    - dispatch without a wave atlas
    - use native subagents as substitutes for fleet dispatch
    - blind-restart stalled workers
    - push_merge_deploy_or_publish_without_plan_or_session_permission
    - personally_implement_discovered_adjacent_fixes

worker:
  on_adjacent_finding:
    - stay_inside_brief
    - surface_falsifiable_finding_to_active_operator
    - append_redacted_framework_finding_to_central_intake
  must_not:
    - patch_adjacent_scope
    - write_repository_operator_journal
```

## Binding Artifacts

```yaml
operator_run:
  plan_name: ""
  artifact_root: ""
  framing_shift: ""
  init_evidence: ""
wave_atlas:
  waves: []
  dependencies: []
  parallel_groups: []
dispatches:
  briefs: []
  run_ids: []
  agents: []
verification:
  reports: []
  gates: []
  branches: []
  shas: []
recovery:
  stalls: []
  recovery_dispatches: []
plan_mutations:
  skipped: []
  added: []
  reordered: []
  cherry_picks: []
security_guardrails:
  prompt_scans: []
  commit_scans: []
close_out:
  tracker: ""
  journal: "<repo-root>/.vibecrafted/JOURNAL.md"
  stop_point_handoff: ""
```

## Stop Button Policy

Operator mode stops before any unpermitted:

- push
- force-push
- merge
- deploy
- public message
- paid action
- irreversible state change
- trust-boundary action

An action is permitted only when it is explicitly allowed in the written plan or
stated and documented in the current session. If the permission is ambiguous,
stop and hand off the button.

The final handoff should make the remaining button obvious.

## Plan Mutation Policy

Repository/runtime context may justify a recovery/fix cut beyond the current
ITP or TD. The Operator may change dispatch shape without a new button when the
final goal remains coherent:

- regroup waves
- skip, add, or reorder prompts
- cherry-pick between active wave branches

Each material mutation must be appended to
`<repo-root>/.vibecrafted/JOURNAL.md` with what changed, why, and what goal
invariant remains unchanged. This includes added/skipped/reordered cuts,
substrate changes, recovery shape, cherry-picks/integration, and security
guardrails. Existing trust-boundary stop points still apply.

## Security Guardrail Policy

Before each wave, scan worker briefs for insecure commands and hard-stop
triggers. After each worker commit, scan committed changes for secrets,
personal data, local-only paths, local network topology, IP addresses, and
internal documents. If detected, revert the offending commit, sanitize the
surface, commit again, and record the incident in
`<repo-root>/.vibecrafted/JOURNAL.md`.

## Recovery Policy

Stall does not mean restart.

Allowed recovery requires:

1. read the stalled worker's report/transcript/meta if present
2. classify the failure
3. issue a focused recovery dispatch or escalate to marbles/ownership
4. append the recovery decision to `<repo-root>/.vibecrafted/JOURNAL.md`

Blind re-fire is a process failure.

## Journal Ownership Policy

`<repo-root>/.vibecrafted/JOURNAL.md` is the one permanent, Git-tracked journal
for the repository. Dated artifact reports, trackers, transcripts, and run
metadata remain evidence projections, not alternative journals. Only the
Operator writes the journal. It records material actions, decisions, evidence,
risks, and required acceptance gaps—not routine negative-work reporting.

Downstream agents append redacted framework findings to
`~/.vibecrafted/vibecrafted/vibecrafted-fail.md`. A dispatched Worker surfaces a
falsifiable adjacent finding to the active Operator and stays inside its brief.
The Operator decides, journals, briefs, actively dispatches the fix into a
dedicated worktree, verifies it, and integrates it; the Operator does not
personally implement the discovered fix.
