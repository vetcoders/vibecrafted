# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Lifecycle — Read-Write Cadence

> Canon source: operator-dictated, 2026-06-08 (doc_id `intents_2026_0506`),
> persisted into the repo by cut `VC-vbcr-canon-034`. The phase table below is
> substantively 1:1 with the dictated canon; only linguistic redaction was
> applied. Status is earned by the runtime, not announced by the spawner.

This document is the canonical description of the full 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. product
lifecycle: the read/write cadence of the `vc-ship` pipeline, the component
architecture it runs on, and the async supervision model that closes the loop.
The executable truth lives in `vibecrafted_core.workflows.registry` and
`vibecrafted_core.lifecycle_runner`. This document explains the operator
contract; the Python manifest is the runtime source of truth.

---

## vc-ship — the meta-skill

`vc-ship` is a meta-skill, usually launched in the `vc-operator` formula.
The pipeline alternates READ and WRITE phases — perception before mutation,
proof after pressure.

## Phase cadence (vc-ship pipeline order)

| #   | Phase        | Cadence | Tooling                                                                                             | Notes                                                              |
| --- | ------------ | ------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| 1   | VC Scaffold  | READ    | `vc-init`, `vc-loctree`, `vc-research`                                                              |                                                                    |
| 2   | VC Implement | WRITE   | `vc-init`, `vc-operator`, `vc-agents`                                                               |                                                                    |
| 3   | VC Review    | READ    | `vc-init`, `vc-loctree`, `vc-review`, `vc-screenscribe`, `vc-prview`                                | Deviation from "tests always last" — Review is test-heavy          |
| 4   | VC Workflow  | WRITE   | `vc-init`, `vc-research`, `vc-justdo`                                                               |                                                                    |
| 5   | Follow-up    | READ    | `vc-init`, `vc-intents` (main intention engine), `vc-loctree`, TDD                                  | Tests always at the end                                            |
| 6   | VC Marbles   | WRITE   | `vc-init` + `vc-marbles` (unique runtime)                                                           | ⬆ entropy — we fill every crack                                    |
| 7   | VC Audit     | READ    | `vc-init`, `vc-loctree`, `vc-aicx`, `vc-research`                                                   |                                                                    |
| 8   | VC Polarize  | WRITE   | `vc-init` + `vc-polarize` (marbles runtime)                                                         | ⬇ entropy — we cut the excess, choose one truth — without scruples |
| 9   | VC DoU       | READ    | `vc-init`, `vc-intents`, `vc-loctree`; TDD irrelevant (assumed green)                               | Detects gaps before the release procedure                          |
| 10  | VC Hydrate   | WRITE   | Preflight Hard Job — `vc-init`, `vc-operator`, `vc-decorate`                                        |                                                                    |
| 11  | VC Release   | WRITE   | SEO, deployment, Docker, publishing, codesigning and everything else agents can't do _totallissimo_ |                                                                    |
| +   | **Fanfary**  | —       | —                                                                                                   | At the very end                                                    |

## Invocation

`vibecrafted <phase> <agent>` is the public command grammar; `vc-<phase>`
wrappers remain installed shell shortcuts. The agent starts after the Context
Atlas is loaded (`loct context`). Separate runtime, separate agent. Target
state: every workflow launchable the same way.

`vc-ship <agent>` starts the umbrella lifecycle runner. `--prompt ...` or
`--file ...` can narrow the task, but a bare `vc-ship codex` is valid and uses a
default full-lifecycle repository prompt after Context Atlas loads. By default it
launches the first stage (`scaffold`) and writes lifecycle state under
`$VIBECRAFTED_HOME/control_plane/lifecycle_runs/<run_id>/`. Passing
`--await-stages` lets `LifecycleSupervisor` wait for each stage, observe exit
truth through the existing workflow/control-plane runtime, record commits and
changed files, and hand the baton to the next stage. `--start-stage <stage>` or
`--checkpoint <stage>` can resume from a specific stage.

Single-stage lifecycle commands such as `vc-dou <agent>`, `vc-audit <agent>`,
`vc-marbles <agent>`, `vc-polarize <agent>`, and `vc-hydrate <agent>` launch
through `vibecrafted_core.lifecycle_runner` as one-stage manifests. The runner
loads Context Atlas once, writes lifecycle run state, prepares the stage prompt,
then delegates the actual agent process to `vibecrafted_core.workflow`.
`vibecrafted <skill> <agent>` remains the compatibility direct launcher for
ordinary skill dispatch.

## READ and WRITE semantics

READ phases may create reports, cache, transcripts, and run-state artifacts.
They must not mutate project code unless the operator explicitly changes the
stage contract. In awaited lifecycle runs the runner fingerprints the dirty
worktree before and after every stage; a READ stage that changes code paths is
marked as a lifecycle failure.

WRITE phases may modify code, remove legacy, refactor, integrate, and generate
missing runtime pieces. The lifecycle runner records changed files after an
awaited stage so the handoff shows what moved.

Each manifest stage exports:

- `phase`: `read` or `write`;
- `can_modify_code`: derived from `phase`;
- `allowed_artifacts`: artifact classes the stage may touch;
- `transition_conditions`: conditions required for handoff;
- `next_stage`, `fallback_stage`, and `audit_after`;
- `tooling`: the VC tools expected by the stage.

The manifest also exports `human_controls`, so approval, forced audit,
interruption, DoU acceptance, and fallback selection are explicit runtime
capabilities rather than side-channel custom.

## Human participation

The operator is part of the lifecycle, but operator actions must leave a trace.
The runtime should make the project feel steerable without turning manual
intervention into invisible state mutation.

Allowed operator moves — each is a real CLI verb on every lifecycle command
(`vc-ship`, `vc-dou`, `vc-audit`, `vc-marbles`, …), validated against the run's
manifest `human_controls` and recorded as a timestamped `operator_actions`
entry in `state.json`, `report.md`, and the transcript
(`vibecrafted_core.lifecycle_control`):

- `<cmd> approve [run_id] [--force]` — approve transition to the next
  lifecycle stage: launches the baton's pending `next_stage` with the baton
  holder as a parent-linked continuation run (`parent_run_id`). Approve first
  verifies the baton's report files exist and are non-empty (the worker may
  still be writing in no-await mode) and refuses with the missing paths
  otherwise; `--force` is the conscious override and is traced
  (`forced_missing_reports`);
- `<cmd> interrupt [run_id]` — interrupt a workflow: stops the live stage run
  and leaves the lifecycle run in `interrupted` state;
- `<cmd> force-audit [run_id]` — force an audit when the current evidence
  feels weak: re-steers the baton to the manifest's audit stage, or dispatches
  a parent-linked standalone `vc-audit` run when the manifest has none;
- `<cmd> accept-dou [run_id] --finding <text>` — mark a DoU finding as
  consciously accepted for this release;
- `<cmd> fallback [run_id] --stage <id>` — choose a fallback or earlier stage
  when audit evidence says the run should move backwards (manifest-validated).

Observability verbs (the same surface the future server wires into):

- `<cmd> status [run_id] [--json]` — one run's truth via
  `LifecycleSupervisor.read_state`/`status`;
- `<cmd> runs [--all] [--json]` — lifecycle runs, newest first (scoped to the
  invoking workflow unless `--all`).

Omitting `run_id` targets the newest run of the invoking workflow. Steering
verbs (`force-audit`, `fallback`) mutate the baton; `approve` fires it.

## Worker steering and the DoU index

Stage workers steer the lifecycle through their report YAML frontmatter
(read by `await_launch_truth`, validated by the runner):

- `next_stage: <stage-id>` — steer the umbrella forward or backward; unknown
  stage ids are ignored (manifest-validated); no key = manifest order
  (fallback / audit_after / next);
- `next_agent: <agent-id>` — hand the baton: the named agent runs the
  following stages until re-steered; unknown agents are ignored; per-stage
  registry pins override the holder for their own stage only;
- `dou_index: <int>` — DoU stages report the count of open
  Definition-of-Undone findings; `0` is the launch-ready target
  (**ZERO DoU index**). The runner records the latest value in `state.json`
  (`dou_index: {value, stage, report}`), on the baton, and in the run
  report; `status` surfaces it next to `accepted_dou` (the count of
  operator-accepted gaps) and reads the live report frontmatter in no-await
  mode. Absent or invalid values read as unknown, never as a fake zero.

## Lifecycle contract: `vibecrafted.lifecycle.v1`

Lifecycle `state.json` is now a versioned external contract, not an internal
accident. Every fresh state document carries:

```json
{ "schema": "vibecrafted.lifecycle.v1" }
```

The machine-readable JSON Schema ships inside the core package at
`vibecrafted_core/schemas/lifecycle.schema.v1.json` and is exposed to MCP
clients as the `vibecrafted://lifecycle/schema` resource.

Within v1, changes are additive only: existing keys keep their names, types,
and semantics. A breaking lifecycle state or worker-report frontmatter change
requires a `vibecrafted.lifecycle.v2` schema and a parallel compatibility
period. The single writer remains Python
(`vibecrafted_core.lifecycle_runner` / `lifecycle_control`); Rust
`control-core`, HTTP endpoints, MCP, Codescribe, and Pensieve are readers or
operator-command surfaces only.

The v1 state contract covers:

- top-level run identity and paths: `schema`, `run_id`, `workflow`, `agent`,
  `root`, `status`, `state_path`, `report_path`, `transcript_path`;
- execution shape: `await_stages`, `parent_run_id`, `spec`, `manifest`,
  `context_atlas`, `supervisor`, `human_controls`;
- lifecycle state: `operator_actions`, `baton`, `stages`, `next_stage`,
  `error`;
- DoU state: `dou_index`, `accepted_dou`, `accepted_dou_findings`.

Worker report frontmatter is the steering side of the same contract:

- `next_stage: <stage-id>` — requested next manifest stage;
- `next_agent: <agent-id>` — requested baton holder;
- `dou_index: <int>` — open DoU findings, where `0` is launch-ready;
- `status: <string>` — worker report metadata. Current runtime steering reads
  `next_stage`, `next_agent`, and `dou_index`; do not treat `status` as a
  transition control until the runtime explicitly does.

### Codescribe consumer recipe

Codescribe drives lifecycle runs through the umbrella MCP verbs:
`vc_lifecycle_runs`, `vc_lifecycle_status`, `vc_lifecycle_approve`,
`vc_lifecycle_interrupt`, `vc_lifecycle_force_audit`,
`vc_lifecycle_accept_dou`, and `vc_lifecycle_fallback`. Read
`vc_lifecycle_status.result.schema` before relying on fields, and fetch
`vibecrafted://lifecycle/schema` when the client needs the full contract.
Use the operator verbs for mutation; never write `state.json` directly.

### Pensieve consumer recipe

Pensieve reads lifecycle truth through the Rust read-model and HTTP endpoints:
`/api/control/lifecycle` for summaries and
`/api/control/lifecycle/{run_id}` for the full nested state. Branch on
`schema == "vibecrafted.lifecycle.v1"` before interpreting lifecycle-specific
fields. The server is a reader/projection layer; it must not become a second
lifecycle writer.

### Consumer proof packet

Use this packet when handing the v1 contract to Codescribe, Pensieve, or another
reader. It is repo-local proof, not live external acceptance: release still
needs one captured read from each real consumer environment.

Contract identifiers:

- State schema id: `vibecrafted.lifecycle.v1`
- Packaged schema path: `vibecrafted_core/schemas/lifecycle.schema.v1.json`
- MCP schema resource: `vibecrafted://lifecycle/schema`
- HTTP summaries: `GET /api/control/lifecycle`
- HTTP detail: `GET /api/control/lifecycle/{run_id}`

MCP status payload shape:

```json
{
  "result": {
    "run_id": "smoke-life",
    "schema": "vibecrafted.lifecycle.v1",
    "workflow": "vc-ship",
    "status": "completed",
    "next_stage": "release"
  }
}
```

HTTP summary payload shape:

```json
{
  "count": 1,
  "lifecycle_runs": [
    {
      "run_id": "smoke-life",
      "schema": "vibecrafted.lifecycle.v1",
      "workflow": "vc-ship",
      "current_stage": "hydrate",
      "next_stage": "release",
      "dou_readiness": "zero"
    }
  ]
}
```

HTTP detail payload shape:

```json
{
  "run_id": "smoke-life",
  "schema": "vibecrafted.lifecycle.v1",
  "baton": {
    "next_stage": "release"
  },
  "stages": [
    {
      "id": "hydrate",
      "status": "completed"
    }
  ],
  "dou_index": {
    "value": 0
  }
}
```

Repo gates that currently prove the contract surface:

```bash
.venv/bin/pytest vibecrafted-core/tests -q
.venv/bin/pytest vibecrafted-mcp/tests -q
make server-check
make server-test
make server-smoke
```

Release-time live acceptance checklist:

- Codescribe reads `vibecrafted://lifecycle/schema` and records
  `vc_lifecycle_status.result.schema == "vibecrafted.lifecycle.v1"` from its
  own MCP client runtime.
- Pensieve reads `/api/control/lifecycle` and
  `/api/control/lifecycle/{run_id}` from its own control-core or HTTP runtime
  and records `schema == "vibecrafted.lifecycle.v1"` in both payloads.
- Neither consumer writes `state.json`; all mutations go through lifecycle
  operator verbs.

Boundaries:

- do not edit `state.json` by hand without recording why in the report or
  transcript;
- do not mix READ and WRITE behavior silently; if a READ stage needs to mutate
  code, change the phase contract explicitly first;
- do not treat a passing stage as product truth when install, docs, runtime, or
  release surfaces remain unverified;
- prefer baton handoff through lifecycle state and reports over side-channel
  chat instructions.

## Notes (operator)

- It must be clearly defined what the human can and cannot do.
- Human involvement in the process is crucial — to "feel" the project.
- Final goal: a big win and **ZERO DoU index**.

## Lifecycle — Architecture (components, all in this repo)

| Component     | Role                                                                                        |
| ------------- | ------------------------------------------------------------------------------------------- |
| Core (Engine) | Execution engine                                                                            |
| MCP           | The agent's eyes and hands (invokes agents, observes the environment)                       |
| VM            | Docker, isolated working environment                                                        |
| Server        | Remote observability + future control                                                       |
| App           | Full Swift application (branch `longtime/integration`)                                      |
| TUI           | Terminal agent `vc-tui` (separate from vc-frame, but launched INSIDE vc_frame / `vc-frame`) |

## Lifecycle — Operation

- **Marble** — launched with or without a prompt.
- **Async supervisor (Python)** — dispatches consecutive runs; observes
  commits, process completion, reports, and changed files; automatically
  triggers Audit after Marble when running a supervised lifecycle.
- **LifecycleSupervisor** — the MVP async facade in
  `vibecrafted_core.lifecycle_runner`; exposes `start`, `read_state`, and
  `status` for future server observability without moving lifecycle ownership
  into the TUI, app, or shell.
- **Audit agent** — has its own lifecycle and helper; connects to the next
  agent after the audit; fires the "baton" to the next agent.
- **Umbrella mode** — processes can move backwards or forwards.

## Summary

The project closes within the Lifecycle — complete automation from A to Z;
all components wired into one system with agents and an async supervisor;
simplicity in UX, flexibility in the backend (code, servers, VM).

---

## Runtime truth hooks (where the cadence is enforced today)

- Workflow manifest: `vibecrafted_core.workflows.registry.WORKFLOW_MANIFESTS`.
- Single-stage launcher: `vibecrafted_core.workflow.launch_workflow`.
- Umbrella runner: `vibecrafted_core.lifecycle_runner.LifecycleRunner`.
- Async supervisor facade:
  `vibecrafted_core.lifecycle_runner.LifecycleSupervisor`.
- Human controls runtime: `vibecrafted_core.lifecycle_control` — the
  `runs`/`status`/`approve`/`interrupt`/`force-audit`/`accept-dou`/`fallback`
  verbs shared by `vc-ship` and every single-stage lifecycle wrapper.
- Run state: `$VIBECRAFTED_HOME/control_plane/lifecycle_runs/<run_id>/state.json`.
- Final lifecycle report:
  `$VIBECRAFTED_HOME/control_plane/lifecycle_runs/<run_id>/report.md`.
- Agent run truth still comes from the existing control plane runtime runs,
  reports, transcripts, and metadata written by `launch_workflow` and the async
  dispatcher.
