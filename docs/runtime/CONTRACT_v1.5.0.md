# Vibecrafted Runtime Contract v1.5.0

This document defines the v1.5.0 runtime direction for Vibecrafted.

It does not replace the existing runtime contract yet. It names the next
operational contract: keep shell as the launch primitive, make state and failure
management explicit, and make every operator surface read the same truth.

The core premise is simple:

> Launching an agent is cheap. Operating what happens after launch is the
> product.

## Status

- Version target: `1.5.0`
- Scope: runtime, spawn, state, operator, observability, and marbles
- Non-scope: full rewrite, mandatory Rust core, mandatory Ghostty migration,
  vc-frame fork, or replacing working shell launchers for its own sake

## Starting Point

The current system already has real mechanisms:

- `scripts/vibecrafted` is the command deck.
- `../../runtime/shell/vetcoders.sh` exposes interactive shell wrappers.
- `runtime/scripts/*_spawn.sh` perform agent-specific launches.
- `runtime/scripts/lib/*.sh` provide shared spawn, meta, lock, prompt,
  session, vc_frame, and terminal helpers.
- `*.meta.json`, reports, transcripts, and locks are already produced.
- `vibecrafted_core.control_plane` already normalizes artifacts into a
  control-plane read model.
- `await.sh`, `observe.sh`, `marbles_ctl.sh`, `marbles_watcher.sh`, and
  `marbles_next.sh` already provide operational surfaces.
- `vc-operator` already reads control-plane state.
- `vc-board` already experiments with a future operator/runtime surface.

The v1.5.0 task is not to invent a ledger. The ledger exists.

The task is to make the existing ledgers default, reduce overlap, and define
which layer writes which truth.

## What To Start With

Start with a contract audit and a small state-plane hardening pass.

Do not start by rewriting launcher code into another language. The existing
launcher shape is fundamentally valid: it is a shell-oriented process spawn
with a prompt, cwd, environment, transcript, and report path.

The first v1.5.0 work should:

1. Name the runtime planes and their ownership.
2. Define the default state fields and status transitions.
3. Make `control_plane` the official read model for humans and agents.
4. Normalize failure kinds.
5. Add smoke tests proving that meta, locks, control-plane, and operator
   commands agree.

Only after that should marbles internals or operator UI ergonomics be changed.

## Where To End

By v1.5.0, Vibecrafted should have this shape:

```text
human/agent operator
    |
    v
command deck / wrappers
    |
    v
shell launch primitive
    |
    v
write truth: meta + lock + report + transcript + events
    |
    v
normalized read model: control_plane/runs + events.jsonl
    |
    v
operator surfaces: CLI JSON, await, observe, TUI, vc-frame, Ghostty/board
```

Any operator, human or agent, must be able to answer:

- What is running?
- Where is its transcript?
- What is its latest report?
- Did it fail, stall, finish, or become a ghost?
- Can it be resumed?
- Is this a child of a marbles loop or a standalone run?
- What exact command/path/state should I inspect next?

## Runtime Planes

### 1. Launch Plane

The launch plane remains shell-native.

Responsibilities:

- Parse the operator-facing command.
- Resolve repo root and skill.
- Prepare prompt input.
- Prepare report, transcript, meta, lock, and tmp paths.
- Spawn the agent program.
- Capture stdout/stderr.
- Mark the run as completed, failed, or ghosted.

Non-responsibilities:

- Long-term UI state.
- Marbles loop decisions.
- Rich observability.
- Policy decisions about resume vs abandon.
- Reconstructing previous runs.

The launch plane should stay boring.

Canonical examples:

```bash
vc-implement codex --prompt "..."
vibecrafted implement codex --file /path/to/plan.md
vc-marbles claude --count 8 --prompt "..."
```

Internally, these may continue to route through the existing shell helpers and
spawn scripts.

### 2. Write-Truth Plane

The write-truth plane is the durable artifact layer.

Canonical write artifacts:

```text
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/
  plans/
  reports/
  tmp/

$VIBECRAFTED_HOME/locks/<org>/<repo>/<run_id>.lock
```

For normal agent runs:

```text
reports/<stamp>_<slug>_<agent>.md
reports/<stamp>_<slug>_<agent>.transcript.log
reports/<stamp>_<slug>_<agent>.meta.json
tmp/<stamp>_<run_id>_<slug>_<agent>_launch.sh
```

For marbles:

```text
$VIBECRAFTED_HOME/marbles/<run_id>/state.json
$artifact_root/marbles/
```

Rules:

- Spawn scripts write meta, lock, report, and transcript truth.
- Control-plane code may normalize this truth but must not invent new truth.
- UI surfaces must not scrape random artifacts when the control-plane read
  model can answer the same question.
- Convenience symlinks or repo-local `.vibecrafted/*` paths are secondary.

### 3. State Plane

The state plane turns write-truth artifacts into an operator read model.

Canonical read model:

```text
$VIBECRAFTED_HOME/control_plane/
  runs/*.json
  events.jsonl
```

`python -m vibecrafted_core.control_plane sync` is the v1.5.0 default normalizer unless
and until it is replaced by an explicitly equivalent implementation.

Required normalized run fields:

```json
{
  "run_id": "impl-123456-999",
  "parent_run_id": null,
  "state": "running",
  "health": "active",
  "agent": "codex",
  "skill": "implement",
  "mode": "terminal",
  "root": "/path/to/repo",
  "operator_session": "repo-impl-123456-999",
  "latest_report": "/path/to/report.md",
  "latest_transcript": "/path/to/transcript.log",
  "last_error": "",
  "failure_kind": null,
  "started_at": "2026-04-25T00:00:00Z",
  "updated_at": "2026-04-25T00:00:00Z",
  "lock_present": true,
  "source": "agent-meta"
}
```

`parent_run_id` is required for marbles child runs and future nested
orchestration. If older artifacts do not contain it, the normalizer may infer it
when the relationship is unambiguous.

### 4. Operator Plane

The operator plane is for both humans and agents.

Human surfaces:

- `vibecrafted dashboard`
- `vc-start`
- `vibecrafted tui`
- vc-frame layouts
- future Ghostty/`vc-board`

Agent surfaces:

- JSON CLI commands
- `await`
- `observe`
- `tail`
- `inspect`
- transcript/report paths
- control-plane files

Rule:

> Every human UI action should have an equivalent agent-readable command.

Minimum v1.5.0 operator commands:

```bash
vibecrafted status
vibecrafted runs --json
vibecrafted inspect <run_id> --json
vibecrafted tail <run_id>
vibecrafted await <run_id> --json
vibecrafted observe <agent> --last
vibecrafted reap --stale --dry-run
```

These commands may be wrappers over existing scripts. The contract is the stable
operator surface, not the implementation language.

### 5. Failure Plane

The failure plane names what went wrong and what the operator can do next.

Failure kinds:

```text
spawn_failed
agent_missing
prompt_missing
startup_timeout
vc_frame_route_failed
terminal_route_failed
no_pid
dead_pid
nonzero_exit
no_report
no_transcript
stalled_no_heartbeat
orphan_lock
orphan_meta
ghost
user_stopped
resume_failed
marbles_child_failed
marbles_state_invalid
```

Failure policy:

| Failure kind            | Default state                     | Resume?         | Operator action                 |
| ----------------------- | --------------------------------- | --------------- | ------------------------------- |
| `agent_missing`         | `failed`                          | no              | install/configure agent binary  |
| `spawn_failed`          | `failed`                          | maybe           | inspect launcher and transcript |
| `startup_timeout`       | `stalled`                         | maybe           | inspect transcript and routing  |
| `vc_frame_route_failed` | `failed` or `headless_fallback`   | maybe           | reroute or run headless         |
| `nonzero_exit`          | `failed`                          | agent-dependent | inspect transcript/report       |
| `no_report`             | `completed_no_report` or `failed` | maybe           | inspect transcript              |
| `dead_pid`              | `ghost`                           | no              | reap, then decide manually      |
| `orphan_lock`           | `orphaned`                        | no              | reap after grace period         |
| `marbles_child_failed`  | `marbles_failed`                  | maybe           | stop loop or resume child       |

Every failure visible in a launcher, watcher, control-plane sync, or operator UI
should map to one of these names.

### 6. Marbles Plane

Marbles is not just launch. Marbles is orchestration.

v1.5.0 keeps the current marbles shell implementation but clarifies the contract:

- A marbles run is a parent state machine.
- Each loop is a child run.
- The watcher observes.
- The orchestrator decides.

Target state model:

```text
initialized
launching_loop
loop_running
loop_reported
deciding
converged
failed
stopped
paused
ghost
```

Required parent fields:

```json
{
  "run_id": "marb-123456-999",
  "agent": "codex",
  "root": "/path/to/repo",
  "status": "loop_running",
  "total_loops": 8,
  "current_loop": 3,
  "god_plan": ".../god.md",
  "ancestor_plan": ".../ancestor.md",
  "children": [
    "marb-123456-999-001",
    "marb-123456-999-002",
    "marb-123456-999-003"
  ]
}
```

Rules:

- The watcher must not be the sole decision-maker.
- Success/failure hooks may remain as implementation detail, but the durable
  state must be enough to reconstruct the loop.
- A failed child must produce a named failure kind.
- A stopped loop must be distinguishable from a failed loop.
- Marbles tabs are UI placement, not truth.

## Language Policy

v1.5.0 does not mandate a Rust rewrite.

Use shell where shell is the shortest honest path:

- invoking agent binaries
- composing small command wrappers
- integrating with user shells
- routing into vc-frame or terminal sessions

Use Python where it is already ergonomic:

- normalizing artifact stores
- installer surfaces
- migrations
- JSON aggregation
- compatibility scripts

Use Rust or Zig where long-lived state, UI, or process supervision genuinely
benefits from stronger structure:

- `vc-operator`
- future `vc-board`
- optional supervisor components
- richer observability

The contract is more important than the language. A shell implementation that
honors the contract is better than a Rust implementation that creates a second
truth.

## Ownership Boundaries

### Shell wrappers

Own:

- user-facing command aliases
- compatibility
- agent binary invocation
- prompt handoff

Do not own:

- independent read model
- independent failure taxonomy
- independent marbles truth

### Spawn libraries

Own:

- path creation
- lock creation
- meta writes
- transcript/report capture
- terminal/vc_frame routing helpers

Do not own:

- UI rendering
- business-level marbles decisions beyond the current implementation bridge

### Control-plane normalizer

Own:

- default read model
- health classification
- stale/ghost detection
- event stream synthesis

Do not own:

- spawning agents
- mutating agent reports
- hiding failures

### Operator UIs

Own:

- rendering
- filtering
- attach/resume/tail affordances
- operator ergonomics

Do not own:

- bespoke state discovery if `control_plane` contains the answer
- separate launch semantics

## Status Contract

Allowed run states:

```text
initialized
launching
running
paused
stalled
completed
completed_no_report
failed
stopped
timed_out
ghost
orphaned
gc
```

Allowed health values:

```text
active
final
stalled
failed
ghost
unknown
```

State transition sketch:

```text
initialized -> launching -> running -> completed
initialized -> launching -> failed
running -> failed
running -> stalled -> running
running -> stalled -> ghost
running -> stopped
running -> completed_no_report
orphaned -> gc
ghost -> gc
```

## Event Contract

Every meaningful operator transition should be appendable to:

```text
$VIBECRAFTED_HOME/control_plane/events.jsonl
```

Event shape:

```json
{
  "ts": "2026-04-25T00:00:00Z",
  "run_id": "impl-123456-999",
  "parent_run_id": null,
  "kind": "state_changed",
  "state": "running",
  "agent": "codex",
  "skill": "implement",
  "message": "Run marked running",
  "path": "/path/to/meta.json"
}
```

The event stream is append-only. If an event is wrong, emit a corrective event.
Do not rewrite history as the normal path.

## Compatibility Rules

v1.5.0 must preserve these entry points:

```bash
vibecrafted implement <agent>
vibecrafted justdo <agent>
vc-implement <agent>
vc-justdo <agent>
vibecrafted marbles <agent>
vc-marbles <agent>
vibecrafted implement <agent> <plan.md>
<agent>-implement <plan.md>
```

`justdo` is a **standalone** skill id (Just Do posture; non-pipeline). `implement`
is the ship WRITE stage. They are not aliases of each other (ADR-0001 Accepted).

## First Implementation Slice

The first concrete slice should be small and testable:

1. Document current write-truth and read-model fields.
2. Add or adjust tests around `vibecrafted_core.control_plane` so sample meta, lock,
   transcript, report, and marbles state normalize into one expected run JSON.
3. Add failure-kind normalization for dead pid, orphan lock, missing report, and
   nonzero exit.
4. Ensure `vibecrafted status` or equivalent reads from the normalized
   control-plane read model.
5. Add one smoke path:

```bash
vc-implement codex --runtime headless --prompt "fake/test prompt"
vibecrafted runs --json
vibecrafted inspect <run_id> --json
vibecrafted await <run_id> --json
```

The smoke may use fake agent binaries in tests. It must not require real Codex,
Claude, Gemini, vc-frame, or Ghostty.

## Acceptance Criteria

v1.5.0 is acceptable when:

- Existing shell launchers still work.
- `implement` and `justdo` remain distinct skill ids (not collapsed aliases).
- A human can inspect all active/recent runs through the operator surface.
- An agent can inspect all active/recent runs through JSON commands.
- Control-plane state is the preferred read model for UIs.
- Failure kinds are named and stable.
- Marbles parent and child runs can be related without scraping terminal panes.
- Tests prove meta/lock/report/transcript/control-plane agreement.
- No UI creates a second runtime truth.

## Non-Goals

- No mandatory full rewrite into Rust.
- No mandatory full rewrite into Python.
- No mandatory replacement of vc-frame.
- No mandatory Ghostty dependency.
- No hard dependency on a TUI for agent operation.
- No hidden state only visible to human dashboards.
- No marbles behavior that can only be understood from live terminal panes.

## Design Rule

Prefer boring runtime primitives:

- files over hidden memory
- JSON over terminal scraping
- append-only events over mutable narrative
- shell process launch over bespoke launch daemons
- one read model over five partial truths
- operator commands over UI-only affordances

The runtime should stay Unix-shaped. The ergonomics should improve because the
truth is easier to inspect, not because the launch path became more elaborate.
