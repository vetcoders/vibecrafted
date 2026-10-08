# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Operator Runbook

Terminal-first, in order, from a cold terminal to a supervised release.
Linux/WSL2 use this POSIX deck; native Windows supports a smaller surface.
Start with the [Entry book](ENTRY_BOOK.md) for installation and platform limits.
[Polski](pl/RUNBOOK.md). Verified against the source deck on 2026-10-06.
This is the _operational_ companion to the canon: what to type, what you will
see, and what to do when it breaks. For the phase doctrine see
[`runtime/LIFECYCLE.md`](runtime/LIFECYCLE.md); for runtime failure classes see
[`runtime/AGENT_OPS.md`](runtime/AGENT_OPS.md). If this document disagrees with
`vibecrafted help --all`, the live deck wins.

---

## 0. The one thing nobody tells you

Conversation happens in the agent CLI itself (Claude Code, Codex). `init` and
`partner` open interactive agent sessions; `init --runtime plain` uses this
terminal without a cockpit. Task launchers such as `implement` require input:

```
$ vibecrafted implement codex
error: Launch requires either --prompt text or --file path.
```

Use an interactive session to discover the task, then dispatch the defined work.
The division of labor:

| You want to…                         | Use                                          |
| ------------------------------------ | -------------------------------------------- |
| Talk to an agent, think out loud     | `claude` / `codex` directly, in the repo     |
| Orient an agent before anything else | `vibecrafted init <agent>`                   |
| Send an agent off with a task        | `vibecrafted <skill> <agent> --prompt "…"`   |
| Execute a prepared brief             | `vibecrafted <skill> <agent> --file <brief>` |
| Watch / steer the fleet              | `status`, `observe`, `await`, dashboard      |

`--prompt` takes plain text typed on the spot. `--file` is for briefs written
in advance. You never need a prepared `.md` just to start working.

## 1. Cold start (new terminal window)

```bash
cd /path/to/your/repo          # 1. stand where the work is
vibecrafted doctor             # 2. install health; read failures and warnings
vibecrafted init claude --runtime plain  # 3. orient and talk here; no cockpit needed
# Or launch claude / codex directly when you only want the provider CLI.
```

When the conversation produces a concrete task, dispatch it:

```bash
vibecrafted workflow claude --prompt "Plan and implement <task>"
vibecrafted implement codex --prompt "Ship <task>"
```

Notes that save time:

- `vibecrafted start --repo /path/to/repo` creates and enters a new workspace.
  It is **create-only**, not a dashboard alias. Exit 3 means the name exists;
  `vibecrafted start resume` deliberately re-enters an existing workspace.
- Agents: `claude · codex · agy · junie · grok · cursor · kimi · copilot`.
  `--model` support and permission/sandbox support are provider-specific;
  unsupported combinations fail before launch. Read `vibecrafted capabilities --json`
  and the selected skill's `--help` instead of assuming flag parity.
- Every skill installs a `vc-<skill>` shortcut. `justdo` is its own posture
  and skill identity, not an alias of `implement`.
- Native Windows: `doctor` and `server` have native owners. `start`, `dashboard`,
  `init` and other POSIX deck verbs return exit 2 and name WSL2. For a native
  provider session, run its CLI in PowerShell. No POSIX workspace is implied.

<!-- Sources: scripts/vibecrafted cmd_start_help / cmd_init_help / help_all;
     vibecrafted-core/vibecrafted_core/cli.py win32 lifecycle refusal;
     vibecrafted-core/vibecrafted_core/help_surface.py capabilities. -->

## 2. Dispatch grammar (one public shape, one engine)

```bash
# skill-first (public grammar — use this in docs and muscle memory)
vibecrafted <skill> <agent> --prompt "text" | --file brief.md

# prepared-brief execution
vibecrafted implement <agent> <brief.md>   # execute a plan file
vibecrafted research <agent>  <brief.md>
vibecrafted review <agent>    <brief.md>
vibecrafted observe <agent> --last         # last report/transcript
```

For an explicit terminal-free task:

```bash
vibecrafted implement codex --runtime headless --prompt "Ship <task>"
```

Expect a tracked run and artifact paths. `--await` joins that run after the
receipt; the default headless launch returns while work continues.
Missing provider CLI is a named prerequisite failure, not a successful dispatch.

<!-- Source: scripts/vibecrafted implement --help;
     vibecrafted-core/vibecrafted_core/help_surface.py workflow flags. -->

Each dispatch creates a **run** (`impl-…`, `scaf-…`, `work-…`) in the control
plane. The run — not the terminal tab — is the unit of truth.

## 3. Supervision: where truth lives

```bash
vibecrafted status                          # today's runs at a glance
vibecrafted await <agent> --run-id <id>     # join monitor; completion is not delivery proof
vibecrafted observe <agent> --last          # read the last report
vibecrafted settlements list                # read-only f/x/n ledger
vibecrafted server status                   # local control-plane viewer (web)
vibecrafted tui                             # Rust operator console
```

On-disk truth (POSIX defaults for `VIBECRAFTED_HOME`; native Windows paths
are in the Entry book):

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
[control-plane axes](../vibecrafted-core/vibecrafted_core/control_plane.py).

## 4. The full lifecycle (vc-ship)

The 11-phase read/write cadence is canon in
[`runtime/LIFECYCLE.md`](runtime/LIFECYCLE.md). Operationally:

```bash
vc-ship codex --prompt "Run the full lifecycle for <goal>"   # umbrella launch
vibecrafted ship                                             # VC-Ship loop + checkpoint
```

The Founder sets direction and owns approval buttons; the Agent-Operator
drives the baton relay. Lifecycle controls include: `approve` · `interrupt` · `fallback` · `accept-dou` ·
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

- Worker tabs use a workspace-bound host `<label>-<workspace_short> workers`.
  `VIBECRAFTED_WORKER_SESSION` is the explicit override. Basename-only naming
  is an emergency fallback when the workspace catalog cannot be opened.
  The launch log's `operator_session` records the actual worker host.
- Missing hosts are created on demand; creation failure is loud. The bare
  project workspace and its worker host are different seats.
- A session is not a run. A closed window does not prove the worker exited,
  and a green report does not prove that a commit was integrated.

For deliberate workspace re-entry:

```bash
vibecrafted dashboard ls
vibecrafted start resume
# Or select the exact session name returned by ls:
vibecrafted dashboard switch <name>
```

Expect attachment/switching to the selected workspace. If a host is dead, read
`control_plane/launches/*.log` and inspect the exact run before recovery.
Do not delete sessions or replay a brief just because a window vanished.

<!-- Sources: vibecrafted-core/vibecrafted_core/workflow.py
     _effective_operator_session; scripts/vibecrafted cmd_start_help /
     cmd_dashboard_help. -->

## 6. Event bus and the Slack bridge

Inter-agent and operator-away communication runs on a thin bus, not on humans
relaying messages:

- **In-repo signal**: control-plane events (run start / blocked / landed) are
  the source; the vc-server exposes them as a read projection + SSE stream
  (`/api/control/runs`, event stream endpoint). Rust server is a **typed read
  projection only** — Python stays the canonical writer.
- **Slack**: the separate `vibecrafted-slack-agent` checkout provides mouth/ear
  over the same control plane. A unit-green bridge is not a live-green Slack
  path: the allowlist and fresh Socket Mode bridge must be proven separately.
  See [gateway contract](runtime/OMNI_OBSERVER_SLACK_GATEWAY.md). Its channel
  and credentials belong to deployment configuration, not this runbook.
- **Secrets**: use the configured local credential mechanism; never echo
  credentials into commands, transcripts or reports.
- **App-side**: the macOS shell-agent (`vibecrafted-app/shell-agent`) talks to
  the runtime over a UniFFI/socket bridge — same bus, native surface.

## 7. Recovery playbook (verified incidents, not theory)

| Symptom                                                               | Diagnosis                                        | Move                                                                                                                                           |
| --------------------------------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Run stuck `process_spawned` → `stalled`, `pid_gone`, empty transcript | launcher died (often dead hosting session)       | read `launches/*.log`, verify liveness and the exact run, then recover the host (§5) and resume that run                                       |
| Transcript frozen mid-generation, pid gone                            | worker died mid-flight                           | inspect the tree and report; use exact-run resume with a bounded continuation; preserve the old record                                         |
| `ControlPlaneLockBusy` (>15 s on `.sync.lock`) during parallel awaits | transient lock contention, **not** a run failure | re-arm the await; touch nothing                                                                                                                |
| Worker exits leaving uncommitted partial work                         | Living Tree: work exists, run looks red          | identify owned partial work, then resume with that tree-state addendum; leave foreign changes alone                                            |
| Report file overwritten by a sibling run                              | report slugs collide within a plan               | copy each report to a unique name immediately after landing; fix generator to use full `prompt_id`                                             |
| Worker's report lost but commits exist                                | artifact wound                                   | dispatcher may re-run the brief's verify gates itself — the only case where the dispatcher substitutes for the verifier, and it must be logged |
| A dead interactive session you need back                              | snapshot layer holds it                          | `vibecrafted resume <agent> --session <provider-id>`; a bare resume recovers AICX context and is not native attach                             |

Resume deliberately:

```bash
vibecrafted resume codex --run-id <id> --prompt "Continue from the recorded failure"
vibecrafted resume codex --session <provider-id>
```

`--run-id` creates a new tracked continuation, not a restart of the old process
group. `--session` selects provider history; a control-plane run ID is not a
provider session ID. Bare resume hydrates AICX context without proving native
resume. Inspect partial writes before continuing: briefs are not assumed idempotent.

Two standing rules underneath all of the above:

1. **Execution substrate** — Living Tree, Fleet Worktrees, local VM and cloud VM
   are distinct. Use the selected substrate. In the Living Tree, re-read before
   editing and commit only owned paths. Isolated workers return a baton; the
   designated integrator proves admission. VM success is not host integration.
2. **Founder buttons** — force-push, trunk merge, PR merge/close, deploy and
   deletion need Founder direction. A fast-forward push of an authored commit
   on the current feature branch is allowed. Agent-Operator is an agent role;
   the humans are Founders.

Commit shape: `[<agent>/<workflow>] ...` with
`Authored-By: <agent> <agents@vetcoders.io>`. The private Operator journal is
`.vibecrafted/THE_JOURNAL.md`, ignored by Git; Workers report to their Operator.

<!-- Sources: scripts/vibecrafted cmd_resume_help; AGENTS.md Runtime Topology,
     Living Tree/Fleet discipline, naming and push rule. -->

## 8. When lost

```bash
vibecrafted help --all      # the live deck (wins over any doc, this one included)
vibecrafted doctor          # health: failure count plus named warnings
vibecrafted receipt         # source ↔ installed drift
cat ~/.vibecrafted/START_HERE.md
```

Expect the live command reference, diagnostics and runtime/source receipt.
`doctor` and `receipt` answer different questions: install health and provenance
are separate from user-visible session acceptance. For older Linux, missing
Loctree/PRView after npm success means the glibc boundary; for native Windows,
unsupported POSIX declarations are intentional. See the Entry book.

<!-- Sources: scripts/vibecrafted help_all; core help_surface.py receipt;
     install-linux.yml foundation waivers; cli.py Windows declarations. -->

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
