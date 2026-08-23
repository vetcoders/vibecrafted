# Vibecrafted Operator Console

This crate is the Rust TUI operator console for Vibecrafted.

It is intentionally separate from the Python installer surfaces and only reads
the shared control-plane state under `VIBECRAFTED_HOME`.

## Interface model

The operator surface is split into three tabs so the console reads like a
dispatcher station instead of a crowded single dashboard:

- `Monitor`: live runs, selected run detail, and recent events
- `Dispatch`: mission kind, agent, runtime, prompt, and launch history
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

## Launching workflows

The TUI shells out to the existing `vibecrafted` command deck when you launch a
workflow, research, review, or marbles run. Launches carry an explicit runtime
and repo root so the Rust surface stays aligned with the shared control-plane
launcher contract instead of inheriting whatever shell state happened to start
the console. Terminal and visible launches get a stable vc_frame session name and
an immediate readiness probe against `vc-frame list-sessions --short
--no-formatting`, so a launch that exits before its named session appears is
reported as a failure instead of a false success. The probe inherits the same
`--config-dir` namespace the launch uses, so a repo-local vc_frame config under
`<root>/config/vc-frame/` is healthchecked against the same socket directory the
launcher actually wrote to. After a 2-second deadline the launch is killed and
reported as `did not appear within the readiness window`, including any probe
diagnostic surfaced through `LaunchRunError.probe_error`.

Use `v` to cycle `terminal` / `visible` / `headless` launch modes and `d` on a
selected run to enter deep controls for attach / resume / report / transcript
actions.

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
#   --runtime terminal
#   --root /path/to/repo
```

The package is `voc` (crate under `tui-agent/`). The supported CLI entrypoint
is `voc` (also launched via `vibecrafted tui`; legacy wrapper name `vc-tui`).
