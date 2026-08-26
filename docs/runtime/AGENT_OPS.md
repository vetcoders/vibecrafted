# Agent Ops — Runtime Failure Classes

The agent-ops canon: failure classes of multi-agent runtimes, observed in
anger, with confirmed remediations. This is runtime mechanics, not identity or
naming — it documents how agents fail _inside this machinery_ (dispatcher,
no-await lifecycle, subagents, watchers) and what actually fixes it.

## Supervisor Quickstart

After dispatch, arm `vibecrafted await <agent> --run-id <id>` immediately,
supervisor-side. Control-plane JSON, report files, transcripts, panes, and
scheduled wakeups are diagnostic only, not wake signals. Hedging await with
ad-hoc pollers/watchers is a Class 3 violation; fix the `vc-server` await hub,
do not normalize the hedge. `--timeout` is an idle window; add `--hard-cap`
when the caller requires an absolute deadline.

Liveness is always a 3-signal decision before declaring a run done: confirm (1)
the await verdict, (2) terminal state in run meta, and (3) worker pid dead; when
a report path is promised, also confirm the report file exists and is non-empty.
Two agreeing signals are enough to act cautiously, three are required to declare
done. Any disagreement means "treat as live, re-arm await."

Every class here earned its entry with at least two confirmed real-world
cases. Speculative classes do not belong in this file.

## The shared principle

> **Neither side may rely on a signal whose channel does not guarantee
> delivery.**

Both classes below are the same root cause seen from opposite ends of the
relay: a worker waiting for a wake-up that will never come, and a supervisor
assuming a death notice that is never sent. The cure is always the same shape:
replace passive waiting with active verification on the side that can act.

---

## Class 1 — Gate-nap („Drzemka na bramce")

_3 confirmed cases, prview-rs session 2026-07-02/03 (operator). Canonical
description by Monika._

### Symptom

A worker-subagent gets a task with a quality gate (cargo test / build /
release gate, 2–8 min). Instead of waiting for the result, it launches the
gate in the background (`run_in_background` / monitor pattern) and ends its
turn with "waiting for the completion signal". The dispatcher receives
`task-notification: completed` with that "waiting" as the result — the work
hangs half-done (commits exist, but no report/push/replies), and the worker
never comes back on its own.

### Mechanism: wake asymmetry in the harness

The main loop is automatically re-invoked when its background task completes.
A subagent is NOT. For a subagent, end of turn = end of its decision process —
the signal from its own background task has nobody left to wake. The worker
imitates a pattern it sees the orchestrator use (and which is correct _for the
orchestrator_), not knowing that for a subagent it is a trap with no exit.

### Why a ban in the brief is not enough

Case #3 had the explicit ban ("gates synchronously, never in background") and
broke it anyway. Affordance beats prohibition: the flag is visible in the
tool, the gate is long, and the pattern looks like good practice.

### Confirmed remediation (3/3)

Resume the stopped agent with an EXPLANATION OF THE MECHANICS, not a bare
"continue":

> "The gate signal will NEVER reach you — you are a subagent, background
> notifications do not wake you. Read the result directly now (Read the
> output file / re-run the command in the foreground) and finish everything
> in this turn."

Context is preserved; the worker finishes without loss. A soft "finish up" is
too weak — case #2 fell asleep again after one; only the explanation of _why
waiting is futile_ worked.

### Prevention (strongest first)

1. **Harness/hook**: PreToolUse hook on subagents' `run_in_background=true` —
   preferably **auto-degrade to foreground with a message** ("degraded to
   foreground: you are a subagent"), not a hard block. A hard block on an
   8-minute gate can kill the worker on turn timeouts instead; degradation
   teaches the mechanics as a side effect (levels 1 and 3 in one shot).
2. **Dispatcher watchdog**: a `completed` notification whose result matches
   `/czekam|wait.*(monitor|event)/i` → automatic resume with the standard
   mechanics message.
3. **Template preamble** in dispatch/skill worker contracts: "You are a
   subagent: background completions will never wake you. Never end your turn
   waiting." Explanation of mechanics > bare prohibition (evidence: case #3
   vs. the effective resume).

**Implemented in this runtime**: level 3 is wired mechanically — every worker
prompt the runtime composes carries `WORKER_SIGNAL_DISCIPLINE`
(`workflow_runtime.py`, injected by both `workflow._runtime_prompt` and
`workflow_runtime._child_prompt`, so dispatched workers AND supervised
marbles/polarize/research children get the mechanics explanation in their
contract). Levels 1–2 remain harness-side proposals.

### Cost when it hits

~3 × 10–20 min delay per case plus manual intervention; zero lost work
(commits were always on disk — only the final leg hung: report/push/replies).

---

## Class 2 — Report-on-death gap

_3 confirmed cases, vibecrafted vc-ship flights 2026-07-02/03 (claude
supervising codex workers)._

### Symptom

A dispatched worker dies silently at or near startup; the dispatcher never
notices. In no-await lifecycle mode the run's meta stays in a live-looking
state and the supervisor (or its watcher) waits on a report that will never
be written. Observed death modes:

- `codex exec` dies right after `task_started` (rollout truncated at ~22 KB;
  transport failure, not disk) — dispatcher hung blind for 37 minutes.
- `codex` dies on `failed to refresh available models: timeout` (transcript
  ~244 bytes) — a 3-hour watcher budget expired on a corpse.

### Mechanism

In no-await mode nothing owns the child's death: the ephemeral spawn launcher
exits by design, the dispatcher tracks the report file, and process death
emits no report. The absence of a signal is indistinguishable from slow work
unless someone actively checks liveness.

### Confirmed remediation (3/3)

Operator-verb sequence, no manual state surgery:

```
vibecrafted ship interrupt <run_id>          # trace: stop_accepted
vibecrafted ship fallback <run_id> --stage <stage>   # baton rewinds WITH cargo
vibecrafted ship approve <run_id> [--force]  # --force only when the cargo gate
                                             # correctly flags the unwritten report
```

No baton cargo is lost; the stage relaunches with the full report trail.

### Prevention

1. **Supervisor-side watchers, never worker-side** (see patterns below) with
   explicit early-death detection: transcript < 5 KB and silent for 10 min =
   startup death; do not wait out the full stage budget.
2. **Terminal-state check** alongside the report poll: read `meta.json` state
   (`failed` / `process_dead` / `contract_failed` / `ghost`), don't infer from
   transcript growth alone.
3. **Systemic cut (implemented, on-read)**: `control_plane.run_liveness()`
   runs the dispatch reconciler for one run and answers with OS-level truth;
   `LifecycleSupervisor.status()` joins it for the current stage as
   `stage_worker` with the actionable flag `worker_dead_without_report` —
   surfaced by `vibecrafted ship status`, `vc_lifecycle_status` (MCP), and
   anything else reading the status projection.
4. **Systemic cut (implemented, push-side)**: every lifecycle stage launch
   hands the dispatcher its `state.json` address (`--lifecycle-state`, wired
   through `WorkflowLaunchSpec.lifecycle_state_path`). On worker failure
   (nonzero exit or broken artifact contract) the dispatcher calls
   `lifecycle_runner.record_stage_worker_exit`, which annotates the matching
   stage with `worker_exit` and — for the current stage of a still-`launching`
   run — mirrors it top-level as `stage_worker_exit`. Purely passive readers
   of raw `state.json` (the Rust server, dashboards) now see the death with
   no status verb in the loop. Additive within `lifecycle.schema.v1`; healthy
   exits write nothing, which also keeps the write too rare to race operator
   verbs on the same file.

### Launch-time variant — dead hosting session (G3, explicit terminal compatibility)

Class 2 above covers death **mid-run**. This variant covers death **at
launch**, before any worker process exists. Ordinary workers now default to
headless and do not depend on a hosting session; this contract applies only
when `terminal` / `visible` was explicitly selected:

- **Symptom**: `vc-frame --session <host> action new-tab ...` prints
  `Session '<host>' not found` (and some builds still exit 0). The launcher
  recorded `process_spawned` and later reconciles to `stalled` / `pid_gone`
  with an empty `last_error`. Observed after a host `vc-frame` session
  restart: three dispatches died this way with no receipt trail.
- **Mechanism**: the spawn path treated the short-lived `vc-frame action`
  process as success (or swallowed the diagnostic), so control-plane never
  received a terminal `failed` event. Heartbeat was the only detector.
- **Caller-side contract** (vibecrafted launcher — not a vc-frame change):
  1. Every `vc-frame ... action ...` on the spawn path checks **exit code
     and stderr** for the session-not-found diagnostic (exit code alone is
     insufficient).
  2. On not-found: exactly **one**
     `vc-frame attach --create-background <operator_session>` (the same
     host name already used as `operator_session` in the launch log), then
     retry the action once.
  3. On second failure: control-plane run lands `state=failed` with
     `last_error` containing the vc-frame stderr, **immediately** (≤5 s) —
     no wait for heartbeat stale. Bash surfaces this via
     `SPAWN_VC_FRAME_LAST_ERROR` + `spawn_record_host_session_failure`;
     Python via `_vc_frame_run_host_action` + a failed `append_event`.
  4. Happy path (host session live): zero extra vc-frame calls beyond the
     previous action surface.
- **Surfaces**: `runtime/scripts/lib/vc_frame.sh` (and the shell twin
  `runtime/shell/lib/vc_frame.sh`), `workflow.launch_workflow` for the
  Python terminal transport. Hosting session name is never hardcoded —
  it is the resolved `operator_session` already written to the launch log.

### Launch-time variant — ambiguous NewTab ACK (G3b)

Class 2 / G3 cover death of the **host**. This variant covers a **live**
host where `action new-tab` applies the mutation but the oneshot completion
channel times out under load (cold wasm plugin load on layout activation):

- **Symptom**: `vc-frame --session <host> action new-tab ...` prints
  `action 'NewTab' did not acknowledge completion within 25s` (or
  `completion channel closed before acknowledgement` / outer `timed out
after`). Observed first on `agy` terminal launches under a busy cockpit;
  the same path is shared by `claude` / `codex` / `junie` / `grok`.
- **Mechanism**: vc-frame server `CRITICAL_ACTION_COMPLETION_TIMEOUT` is
  25s. The tab often _did_ open; the client still exits non-zero. A blind
  retry would spawn a **second** worker tab for the same run.
- **Caller-side contract** (central launcher — not agent-specific):
  1. Detect ambiguous-ACK diagnostics on stderr (same predicates as
     vc-frame triage `is_ambiguous_new_tab_failure`).
  2. If the action carried `--name NAME`: `list-tabs` probe; if present →
     treat as **success** (no second new-tab).
  3. Else (or absent): brief backoff, **one** retry of the same argv.
  4. After a second ACK failure: probe `--name` again before failing
     loud into `SPAWN_VC_FRAME_LAST_ERROR` / control-plane `failed`.
- **Surfaces**: `spawn_vc_frame_session_action` (bash scripts/lib + shell
  twin), `_vc_frame_run_host_action` (Python). All `*_spawn.sh` agents
  funnel through `spawn_launch` → this helper — no per-agent fork.

### Explicit terminal hosting — per-project worker sessions (G7)

Ordinary workers have no host tab: they launch headless in their own process
session. If a provider path is explicitly forced into `terminal` / `visible`,
its compatibility tab must **never** open in the human operator's interactive
seat. It lands in a project-named worker host visible in the vc-frame rail.
Finished viewer placement remains independent of the source session.

Canonical settlement and optional terminal projection contract (`f·x·n`
ledger counts, origin stamp, classification, push≠install):
[`TRIAGE_AND_SESSIONS.md`](./TRIAGE_AND_SESSIONS.md).

**Surface**: explicit terminal skill-worker path
(`runtime/scripts/lib/vc_frame.sh` + `spawn_launch` + Python
`workflow.launch_workflow`). Operator-UI entrypoints
(`vc-init`, interactive operator agent, shell twin
`_vetcoders_spawn_into_operator_session`) still land in the prepared human
seat unless `VIBECRAFTED_WORKER_SESSION` is set.

**Resolution order** (bash `spawn_effective_operator_session` + Python
`_effective_operator_session` — same rules):

1. `VIBECRAFTED_WORKER_SESSION` if set — explicit override wins (any name,
   including one that matches the operator seat).
2. Else the workspace-bound worker host `"{label}-{workspace_short8}-w"`
   resolved through the workspace catalog (`worker_host_session_name` in
   `workspace_catalog.py`, `WORKER_HOST_SUFFIX = "-w"`); emergency
   fallback `"<basename(--root)>-w"` only when the catalog cannot open
   (SPAWN_ROOT / VIBECRAFTED_ROOT / cwd). **Always** suffixed. The suffix is a
   short dash-joined token so the name stays one argv element across shell
   quoting and session-listing matches AND fits the macOS `sockaddr_un`
   budget (104 bytes; the older `{label}-{short} workers` form overflowed it —
   `legacy_worker_host_session_name()` keeps that token for WES attach only).
   See `docs/runtime/WORKSPACE_IDENTITY.md`. Bare `basename(--root)` is the
   operator's own interactive card in the rail and is never a worker target,
   so the dispatcher seat plays no part in host resolution.

_2026-08-09 — the suffix used to be conditional on `basename(--root)` matching
the dispatcher seat (`VC_FRAME_SESSION_NAME` / `ZELLIJ_SESSION_NAME`). That
guarded only the seat==repo case: a dispatch fired from any other seat routed
worker tabs straight into the operator's interactive card (field proof: 20 runs
`impl-260809-16*` stamped `operator_session: "vibecrafted"`). The invariant
above was always unconditional; now the code is too._

**Missing host**: G3 contract applies — one
`vc-frame attach --create-background <host>`, then the `new-tab` action.
**Receipt truth**: launch-log / control-plane field `operator_session` is
the **actual worker host** after the rules above, not the human seat name.

**Out of scope for this cut**: sidebar UI grouping chrome inside the vc-frame
repo beyond the existing session-manager rail; migrating live PTYs without
recreate (vc-frame always recreates for triage); forcing marbles shell-entrypoint
off the operator seat (primary fleet path is scripts/lib).

**In scope (landed runtime wire)**: caller-side `triage_finished_run` /
`spawn_triage_run` → `vc-frame triage-run`, origin stamp in meta, conjunction
classifier, fail-open receipts. See
[`TRIAGE_AND_SESSIONS.md`](./TRIAGE_AND_SESSIONS.md). If tools home lags the
checkout that contains the wire, terminal viewer tabs may stay in the work
session. That projection failure must not change settlement-ledger `f·x·n`
counts — install, do not assume git alone refreshed the daily driver.

---

## Class 3 — Premature/untrusted await (observability contract drift)

### Symptom

Supervising agents stop trusting `await` and hedge: they run the verb in the
background AND keep a manual sleep/ps/git monitor "because await can return
early". Every hedge is a doctrine violation (ad-hoc watchers) caused by a real
contract gap, not by agent paranoia.

### Mechanism (five confirmed gaps, all fixed at the source)

1. **A private await loop per client**: `cli._agent_await`'s human path had its own
   inline loop treating `--timeout` as an ABSOLUTE wall clock — it abandoned
   demonstrably-working runs at 300 s. A later 2026-08-26 incident multiplied
   the canonical Python function into fourteen independent processes for one
   run. Fixed: CLI and MCP now subscribe to the existing `vc-server` hub, which
   owns exactly one ephemeral monitor per canonical home plus run id. Python
   remains the sole durable writer and settlement authority. A new client-side
   loop is this class reborn.
2. **Loop parents look dead while children work**: marbles/polarize rounds are
   sequenced deterministically (next round fires on the previous child
   PROCESS EXIT + artifact validation in `workflow_runtime.run_marbles`), but
   each round is a separate run record (`<parent>-<kind>-L<n>`) linked only by
   id prefix. The parent record freezes between rounds, so a parent-only
   fingerprint fired false `idle_stall` mid-loop. Fixed: `await_run`
   aggregates child-run movement and liveness into the parent's idle window.
3. **Delivered report ≠ return**: a no-await stage worker writes its report
   and exits; `await_run` knew nothing about reports, so `ship await` idled a
   full window on the corpse and returned a misleading
   `timed_out: idle_stall` + `report_written: true`. Fixed: a non-empty
   report (`report_path` argument, else the run's `latest_report`) returns
   `completed` with `reason: report_delivered` on the first poll. The
   launcher-owned identity template is explicitly excluded: bytes written
   before the worker starts are transport scaffolding, not delivery.
4. **rc=0-on-live / inverse meta lag**: field evidence from 2026-07-10 showed
   both directions of signal skew. `await` could return rc=0 while the worker
   was still alive, and completed workers could leave run meta stuck
   `active`/`stalled` with `exit_code: null` after writing the report and
   exiting. Fixed: success requires worker-dead terminal evidence — terminal
   meta state or delivered report — and live-worker disagreement keeps await
   armed. A finalized report with a completed top-level `status` also closes a
   stale projection when an unrecognized evidence adjective appears in
   `claim_status`; recognized blocked/failed/partial claims still veto success.
   Known failure mode if it recurs: rc=0-on-live or meta-active-after-completion
   is a Class 3 bug; use the 3-signal rule and re-arm await.
5. **stdout silence looked like worker death**: the supervisor heartbeat was
   refreshed only after `readline()` produced another token. A ten-minute
   build/test/install therefore emitted no pulse at exactly the moment the
   operator needed liveness proof. Fixed: the pending stdout read is polled on
   the heartbeat interval, and a still-live worker emits a lifecycle pulse
   without fabricating `first_output_seen` or mutating state history. A
   transient `stalled` projection remains non-terminal; canonical `await_run`
   stays armed and accepts a later `active`/terminal transition.

### The rule

The runtime hands out a run id; `await`/`observe` on that id must ALWAYS be
the whole answer. If an agent feels the need to double-guard await with a
manual monitor, treat that as a Class 3 bug report against the runtime — fix
the contract, do not normalize the hedge.

The operational verdict is deliberately redundant: await verdict, terminal run
meta, and worker pid death must converge before "done" leaves the supervisor.
Report presence is a fourth promised-artifact check, not a replacement for
liveness. Two agreeing signals can justify recovery or cautious next action;
three signals are the bar for done.

---

## Supervisor watcher patterns (battle-tested, two full vc-ship flights)

- Poll the stage **report file** (`[ -s "$REPORT" ]`), paired with the
  `meta.json` terminal-state check above. Report file appearing = stage done;
  terminal state = stage dead. Waiting on anything else is guesswork.
- **Marbles parent runs have NO transcript.log** — the loop spawns children
  with their own transcripts under `reports/marbles/<run>-children/`. Watch
  children-dir growth or the report file; a parent-transcript stall check is
  a false alarm (confirmed twice).
- Watchers live with the supervisor (main loop), never inside a subagent —
  that is Class 1 waiting to happen.
- Budget every watcher, and on silent expiry verify liveness before declaring
  a stall; on noisy expiry check for the early-death signature first.

## Provenance

- Class 1: prview-rs, session 2026-07-02/03, cases #1–#3, remediation and
  canonical description by the operator.
- Class 2: vibecrafted, vc-ship flights `life-ship-260702-123238-24000`
  (v3.3.0) and `life-ship-260702-202338-58000` (lifecycle.schema.v1),
  supervision by claude, session `2603026d-0c40-4ca9-af91-e2ab74256926`.
- Class 3: operator report 2026-07-05 (agents hedging
  `codex await --run-id marb-260705-164001-84000` with parallel manual
  monitors) + live evidence from the vc-frame redesign flight (`ship await`
  idle-stalling on a delivered review report); fixes by claude, same session.
