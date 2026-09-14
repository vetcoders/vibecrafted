# Vibecrafted Operator Console

This crate is the Rust TUI operator console for Vibecrafted.

It is intentionally separate from the Python installer surfaces and only reads
the shared control-plane state under `VIBECRAFTED_HOME`.

## Interface model

The operator surface is split into three tabs so the console reads like a
dispatcher station instead of a crowded single dashboard:

- `Monitor`: live runs, selected run detail, and recent events
- `Dispatch`: the launch declaration — mission kind, agent, model,
  execution environment, presentation, permissions, sandbox and prompt —
  plus the launcher's answer to the last one
- `Controls`: attach/resume/report/transcript actions for the selected run

Use `Tab` / `Shift+Tab` to switch tabs. Arrow keys stay local to the active
tab, so `Monitor` moves through runs, `Dispatch` moves through launch fields,
and `Controls` moves through deep actions.

## Expected state layout

The console reads the local control-plane contract:

```text
$VIBECRAFTED_HOME/control_plane/
  runs/*.json
  events.jsonl
```

The writer is `vibecrafted_core.control_plane` (`python -m vibecrafted_core.control_plane sync`). The reader is strict about
that shape: it does not follow symlinks out of the root and ignores anything
outside the control-plane directory. `config::default_state_root` falls back to
historical variants (`state/control-plane`, `state`, `control-plane`) if the
canonical `control_plane` path is missing, so older layouts keep loading.

## Refresh cadence

The 250 ms UI tick redraws cached state only. Expensive control-plane
projection and Polarize prism discovery are invalidated by filesystem events on
the projection files (`events.jsonl`, `runs/*.json`) and `artifacts/`,
debounced for 100 ms, and skipped when the projection revision is unchanged.
Transcript appends under `runtime_runs/` do not recompute the board. Observe
polling (Observe view only) starts at two seconds and backs off to 30 seconds
when the server is offline. If a filesystem watcher cannot start, its affected
surface falls back to a 30-second refresh. Pressing `r` always bypasses the
scheduler and forces a complete refresh.

For an isolated performance proof, set `VOC_REFRESH_TRACE_PATH` to record one
JSONL row after each control-plane projection and Polarize discovery. Leave it
unset in normal use; tracing is opt-in and does no filesystem IO otherwise.

## Launching workflows

VOC does not launch anything itself. It assembles a declaration and hands it to
the canonical launcher (`vibecrafted <skill> <agent> ... --json`), which owns
session creation, worktree preparation, the agent environment and the run
lifecycle. VOC keeps no session, worktree or readiness machinery of its own, and
composes no `vc-frame` layout: inside a frame the launcher opens the preview
through the existing workspace, and outside one the shared launcher owns the
terminal.

**The receipt is the proof.** A spawned process and a PID say nothing. VOC reads
the launcher's `vibecrafted.launch_receipt.v1` answer and shows the confirmed
result: run id, effective repo and worktree, and the chosen parameters. A
refusal, a non-zero exit, or a zero exit without a receipt are all reported as
failures — never as a start. When the receipt is accepted but does not confirm a
declared choice (a Fleet Worktrees run with no worktree, a skipped model pin),
the confirmation names the mismatch instead of hiding it.

**The catalog has one owner.** Agents, model-pin support, permission/sandbox
cells and environment availability come from `vibecrafted capabilities --json`
(schema `vibecrafted.workflow_capabilities.v1`). VOC carries no agent list of
its own, so a retired launcher cannot reappear here, and an unavailable choice
is shown with the launcher's own reason rather than silently falling back. Until
the catalog loads, no launch is offered; `r` retries a failed probe.

**Environment and presentation are separate choices.** The execution
environment is Living Tree, Fleet Worktrees, Fleet VM local or Fleet VM cloud;
the presentation is headless or an interactive view. Unsupported combinations —
an agent that exposes no model flag, a permission/sandbox cell the provider
cannot enforce, an environment with no launcher entrypoint, the home directory
as a repository — are refused before any process is created.

**The prompt stays private.** It rides `--prompt-stdin`, so it never reaches
public argv. The command preview corresponds to what actually runs but states
only the size of the prompt (`<stdin: prompt, N chars>`), never its content.

**Launching never blocks the interface.** The launcher runs on a background
thread; the console stays responsive and shows `launching: ... — waiting for the
launcher receipt` until the answer arrives.

Shortcuts on the Dispatch tab: `a` agent, `M` model, `n` environment,
`v` presentation, `p` permissions, `s` sandbox, `e` prompt, `Enter` launch.
`d` on a selected run opens deep controls for attach / resume / report /
transcript actions.

### Polarize

`vc-polarize` is highlighted in the Dispatch skill catalog because it is the
post-marbles gate for choosing one sharp product truth. When recent prism
payloads exist, the Controls tab also surfaces Polarize intents from
`$VIBECRAFTED_HOME/artifacts/**/polarize/*/prism.json`, using Loctree's
`band_action` (`abort`, `memo`, `pass`, `doctrine`) as the canonical launch
decision. See the Vibecrafted `skills/vc-polarize/SKILL.md` doctrine for the
runner contract.

## MCP daemon visibility (rmcp-mux)

The console surfaces live status from the
[`rmcp-mux`](https://github.com/Loctree/rmcp-mux) MCP transport multiplexer
inside the Monitor tab. When `rmcp-mux` writes JSON status snapshots to its
`--status-file`, the operator gets a `rmcp-mux (N)` panel between the stat
strip and the run table:

```text
┌─ rmcp-mux (2) ───────────────────────────────────────────────────────────┐
│ MCP daemons (1/2 need attention):                                        │
│   • general-memory: Running clients=1/3 pending=0 queue=0 restarts=0 …  │
│   ! brave-search: Failed clients=0/0 pending=0 queue=0 restarts=5 …     │
└──────────────────────────────────────────────────────────────────────────┘
```

The panel header turns red when any service is unhealthy or unreadable.
Healthy rows render with a green `•`, unhealthy / unreadable rows render with
a red `!`. The Controls tab gains one `Health-check MCP daemon: rmcp-mux
health --service <name>` action per known service, available even when no
agent run is selected (so the operator can health-check the supervisor when
nothing else is up).

### Discovery

Status files are discovered in this order:

1. `VIBECRAFTED_MUX_STATUS_PATHS` (colon-separated list of explicit paths,
   missing entries are still surfaced so misconfiguration is visible).
2. `~/.rmcp_servers/rmcp_mux/status.json` if present.
3. Every other `*.json` under `~/.rmcp_servers/rmcp_mux/`, sorted
   lexicographically.

The reader mirrors the public `rmcp_mux::state::StatusSnapshot` schema and
ignores unknown fields, so a newer rmcp-mux release that adds fields will not
break this surface.

## Run

```bash
cargo run -- --state-root "$VIBECRAFTED_HOME/control_plane"
# optional:
#   --presentation terminal
#   --repo /path/to/repo      # legacy spelling: --root
```

The package is `voc` (crate under `tui-agent/`). The supported CLI entrypoint
is `voc` (also launched via `vibecrafted tui`; legacy wrapper name `vc-tui`).
