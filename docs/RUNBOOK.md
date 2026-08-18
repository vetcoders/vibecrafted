# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Operator Runbook

Terminal-first, in order, from a cold iTerm2 window to a supervised release.
This is the _operational_ companion to the canon: what to type, what you will
see, and what to do when it breaks. For the phase doctrine see
[`runtime/LIFECYCLE.md`](runtime/LIFECYCLE.md); for runtime failure classes see
[`runtime/AGENT_OPS.md`](runtime/AGENT_OPS.md). If this document disagrees with
`vibecrafted help --all`, the live deck wins.

---

## 0. The one thing nobody tells you

**Vibecrafted is not a chat.** There is no mode where you launch it and start
talking. Every skill launch requires work up front:

```
$ vibecrafted partner
error: Launch requires either --prompt text or --file path.
```

Conversation happens in the agent CLI itself (Claude Code, Codex). Vibecrafted
is the engine you use to _send agents away with a defined task_ and to
supervise what comes back. The division of labor:

| You want to…                         | Use                                         |
| ------------------------------------ | ------------------------------------------- |
| Talk to an agent, think out loud     | `claude` / `codex` directly, in the repo    |
| Orient an agent before anything else | `vibecrafted init <agent>`                  |
| Send an agent off with a task        | `vibecrafted <skill> <agent> --prompt "…"`  |
| Execute a prepared brief             | `vibecrafted <agent> <mode> --file <brief>` |
| Watch / steer the fleet              | `status`, `observe`, `await`, dashboard     |

`--prompt` takes plain text typed on the spot. `--file` is for briefs written
in advance. You never need a prepared `.md` just to start working.

## 1. Cold start (new terminal window)

```bash
cd /path/to/your/repo          # 1. stand where the work is
vibecrafted doctor             # 2. optional: install health, pass/fail
vibecrafted init claude        # 3. orient the agent in this repo
claude                         # 4. talk. no prompt file needed.
```

When the conversation produces a concrete task, dispatch it:

```bash
vibecrafted workflow claude --prompt "Plan and implement <task>"
vibecrafted implement codex --prompt "Ship <task>"
```

Notes that save time:

- `vibecrafted start` is an **alias for `vibecrafted dashboard`** (vc-frame
  operator layout). It is optional and is _not_ the entry point.
- Agents: `claude · codex · agy · junie · grok`. Model override exists only
  for claude (`--model`) and codex (`-m`); grok always runs its default.
- Every skill also installs a `vc-<skill>` shortcut.

## 2. Dispatch grammar (two shapes, one engine)

```bash
# skill-first (public grammar — use this in docs and muscle memory)
vibecrafted <skill> <agent> --prompt "text" | --file brief.md

# agent-first modes (brief execution)
vibecrafted <agent> implement <brief.md>   # execute a plan file
vibecrafted <agent> research  <brief.md>
vibecrafted <agent> review    <brief.md>
vibecrafted <agent> observe --last         # last report/transcript
```

Each dispatch creates a **run** (`impl-…`, `scaf-…`, `work-…`) in the control
plane. The run — not the terminal tab — is the unit of truth.

## 3. Supervision: where truth lives

```bash
vibecrafted status                          # today's runs at a glance
vibecrafted <agent> await --run-id <id>     # block until the run lands (arm early)
vibecrafted <agent> observe --last          # read the last report
vibecrafted settlements list                # read-only f/x/n ledger
vibecrafted server status                   # local control-plane viewer (web)
vibecrafted tui                             # Rust operator console
```

On-disk truth (when the CLI is not enough):

| Path                                              | What it holds                                      |
| ------------------------------------------------- | -------------------------------------------------- |
| `~/.vibecrafted/control_plane/runs/<id>.json`     | run state, liveness, exit code, three axes         |
| `~/.vibecrafted/control_plane/runs/archive/`      | settled runs (moved out of the hot dir)            |
| `~/.vibecrafted/control_plane/runtime_runs/<id>/` | transcript.log and runtime artifacts               |
| `~/.vibecrafted/control_plane/launches/*.log`     | launcher stderr — **first stop for silent deaths** |
| `~/.vibecrafted/artifacts/<org>/<repo>/<day>/`    | plans, briefs, reports                             |

A run is judged on **three axes** (`execution_state` / `proof_state` /
`delivery_state`), not on exit code alone. `completed` + `artifact_ok` is not
delivery; only a verifier flips a tracker entry to `[x]`. See
[`runtime/DELIVERY_PROOF_KERNEL_v1.md`](runtime/DELIVERY_PROOF_KERNEL_v1.md).

## 4. The full lifecycle (vc-ship)

The 11-phase read/write cadence is canon in
[`runtime/LIFECYCLE.md`](runtime/LIFECYCLE.md). Operationally:

```bash
vc-ship codex --prompt "Run the full lifecycle for <goal>"   # umbrella launch
vibecrafted ship                                             # VC-Ship loop + checkpoint
```

The supervising operator (human or agent) drives the baton relay with the
human-controls verbs: `approve` · `interrupt` · `fallback` · `accept-dou` ·
`force-audit` (exposed via the `vibecrafted` MCP surface and `vibecrafted
dispatch run …`). One phase's report is the next phase's input; the operator
reads reports between phases, not transcripts during them.

Shortest honest paths (from `~/.vibecrafted/START_HERE.md`, which is
generated — do not edit it by hand):

```bash
# build path
vibecrafted init claude
vibecrafted workflow claude --prompt "Plan and implement <task>"
vibecrafted implement codex --prompt "Ship <task>"

# ship path
vibecrafted dou claude --prompt "Audit launch readiness"
vibecrafted decorate codex --prompt "Polish the release surface"
vibecrafted hydrate codex --prompt "Package the product"
vibecrafted release codex --prompt "Prepare release steps"
```

## 5. Sessions, tabs, and the operator's view (vc-frame)

- Worker tabs are hosted in **per-project sessions** named
  `<repo basename> workers` — never in the operator's own session, which is
  the bare `<repo basename>` card. `VIBECRAFTED_WORKER_SESSION` overrides;
  nothing else (the dispatcher's seat name included) changes the host.
  The launch log's `operator_session` field records the _actual_ host.
- Missing host session → created on demand via `attach --create-background`;
  a double failure is loud (exit ≠ 0, `last_error` set), never silent.
- Finished runs settle into the `f`/`x`/`n` drawers. The rail is clickable
  (click a session or tab to jump there); `action new-tab --no-focus` spawns
  tabs without stealing focus.
- If the hosting session dies, every subsequent spawn dies with only
  `control_plane/launches/*.log` as evidence. Resurrect:

```bash
vc-frame delete-session <name> 2>/dev/null
script -q /tmp/anchor.log vc-frame --session <name> \
  --new-session-with-layout ~/.config/vetcoders/frontier/vc-frame/layouts/operator.kdl &
```

## 6. Event bus and the Slack bridge

Inter-agent and operator-away communication runs on a thin bus, not on humans
relaying messages:

- **In-repo signal**: control-plane events (run start / blocked / landed) are
  the source; the vc-server exposes them as a read projection + SSE stream
  (`/api/control/runs`, event stream endpoint). Rust server is a **typed read
  projection only** — Python stays the canonical writer.
- **Slack**: the `vibecrafted-slack-agent` repo (separate checkout) bridges
  the bus to the Libraxis workspace via Socket Mode — bot `@Vibecrafted`,
  channel `#agents-room`. Fleet posts handoffs with `vc-slack post "…"`,
  peer signals with `vc-slack signal "…"`, operators query `/vc status` from
  a phone. Lifecycle hook: launch / finalize / blocked → one-line post with
  `run_id`, agent, skill.
- **Secrets**: the Slack app credentials live in
  `~/.keys/.vibecrafted.slack.app` and are mapped at runtime. They are never
  committed, never echoed into reports.
- **App-side**: the macOS shell-agent (`vibecrafted-app/shell-agent`) talks to
  the runtime over a UniFFI/socket bridge — same bus, native surface.

## 7. Recovery playbook (verified incidents, not theory)

| Symptom                                                               | Diagnosis                                        | Move                                                                                                                                           |
| --------------------------------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Run stuck `process_spawned` → `stalled`, `pid_gone`, empty transcript | launcher died (often dead hosting session)       | read `launches/*.log`, resurrect session (§5), stop run, refire the same brief — briefs are idempotent                                         |
| Transcript frozen mid-generation, pid gone                            | worker died mid-flight                           | stop + refire same brief; the run record stays as evidence                                                                                     |
| `ControlPlaneLockBusy` (>15 s on `.sync.lock`) during parallel awaits | transient lock contention, **not** a run failure | re-arm the await; touch nothing                                                                                                                |
| Worker exits leaving uncommitted partial work                         | Living Tree: work exists, run looks red          | refire with a tree-state addendum: _adopt your own partial work, do not fake a red run_                                                        |
| Report file overwritten by a sibling run                              | report slugs collide within a plan               | copy each report to a unique name immediately after landing; fix generator to use full `prompt_id`                                             |
| Worker's report lost but commits exist                                | artifact wound                                   | dispatcher may re-run the brief's verify gates itself — the only case where the dispatcher substitutes for the verifier, and it must be logged |
| A dead interactive session you need back                              | snapshot layer holds it                          | `vibecrafted resume <agent>` / `resume-session` — restore from the preserved session without destroying the source                             |

Two standing rules underneath all of the above:

1. **Living Tree** — one shared checkout, no worktrees. Re-read files before
   editing, never revert others' changes, commit only your own paths in small
   packs, `[<agent>/<workflow>]` titles, `Authored-By: <agent>
<agents@vetcoders.io>`.
2. **Buttons** — force-push, trunk push, merge, deploy, and anything
   outward-facing belongs to the human operator. A non-destructive
   `git push` of the current feature branch is not a button. Workers stop
   at the remaining buttons and write a one-step handoff instead.

## 8. When lost

```bash
vibecrafted help --all      # the live deck (wins over any doc, this one included)
vibecrafted doctor          # health with pass/fail
vibecrafted receipt         # source ↔ installed drift
cat ~/.vibecrafted/START_HERE.md
```

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
