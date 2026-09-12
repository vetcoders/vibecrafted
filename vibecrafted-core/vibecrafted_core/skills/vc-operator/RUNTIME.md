# `vc-operator` Runtime

`vc-operator` as an interactive skill does not automatically launch runtime.

Runtime begins only when the operator chooses a live supervisor or workflow
lane:

```bash
vibecrafted dispatch plan.dispatch.toml --doctor
vibecrafted dispatch plan.dispatch.toml --dry-run --json
vibecrafted dispatch run --run-id <id> --root . --report report.md --transcript trace.log -- <worker>
vibecrafted workflow claude --file /path/to/plan.md
vibecrafted implement codex --prompt '<bounded slice>'
```

## Runtime Responsibilities

The operator posture creates or consumes durable state for fleet orchestration:

- run metadata
- transcript
- wave tracker
- the repository-local, append-only Operator Journal
- worker briefs
- launch cards and run IDs
- per-wave close-outs
- final stop-point handoff
- plan mutation and security guardrail entries in the operator journal

## Artifact Layout

```text
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/
  plans/
  reports/
  tmp/
  dispatch-result.json or run-specific result files
  <timestamp>_<slug>.transcript.log
  <timestamp>_<slug>.meta.json
```

Dated reports, trackers, transcripts, briefs, close-outs, and run metadata are
run projections/evidence. They do not become a second journal system. The one
canonical permanent operator journal is
`<repo-root>/.vibecrafted/THE_JOURNAL.md` (gitignored; former name `JOURNAL.md` is
retired). The repo-local `.vibecrafted/` directory remains ignored runtime state.

## Runtime Lanes

| Need                        | Runtime lane                       |
| --------------------------- | ---------------------------------- |
| Plan is fuzzy               | `vibecrafted scaffold <agent>`     |
| One worker slice            | `vibecrafted implement <agent>`    |
| Strict ERi slice            | `vibecrafted workflow <agent>`     |
| Truth-drift convergence     | `vibecrafted marbles <agent>`      |
| A to Z polish for one slice | `vibecrafted ownership <agent>`    |
| Shared strategy pause       | `vibecrafted partner <agent>`      |
| Independent verification    | `vibecrafted audit <agent>`        |
| Deterministic supervisor    | `vibecrafted dispatch <file.toml>` |
| Outward ship                | `vibecrafted release <agent>`      |

## Headless runtime is the worker default

Runtime mode is `headless | terminal | visible` (`SUPPORTED_RUNTIMES`). The
selector resolves like this:

- CLI and MCP workflow workers default to `headless`, whether or not a
  `VC_FRAME_SESSION_NAME` is live.
- Headless execution starts the worker in a detached process session. Durable
  run state, transcript, Guardian settlement, `observe`, and `await` are the
  observation contract.
- vc-frame may render a transcript or run-state projection. The projection is
  not process ownership, and closing it must not stop the worker.
- `terminal` / `visible` is an explicit compatibility lane for a provider path
  proven to require a TTY. Until a daemon-owned PTY broker exists, it stays
  coupled to the terminal.
- `init`, `operator`, and bare interactive `resume` remain true PTY-backed User
  Sessions.

## Terminal States

```yaml
terminal_state:
  stopped_at_operator_button:
    requires:
      - wave tracker updated
      - repository JOURNAL.md updated with material decisions
      - reports and SHAs named
      - remaining unpermitted human action named
  completed_with_plan_permission:
    requires:
      - permission source named
      - tracker updated
      - repository JOURNAL.md updated with material decisions
      - reports and SHAs named
  blocked_with_evidence:
    requires:
      - blocker classification
      - attempted recovery
      - nearest safe next action
  escalated:
    requires:
      - target skill
      - reason
      - handoff state
```

## Non-Goals

- Do not use runtime to hide decisions from the operator.
- Do not make a projection tab the owner or liveness signal of a worker.
- Do not bypass launch telemetry.
- Do not turn stop-point handoff into push/merge/deploy unless the written
  plan or current session explicitly permitted that action.
