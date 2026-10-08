---
title: "MCP server"
description: "The vibecrafted-mcp stdio server: board, run, and lifecycle tools that give agents the same control-plane truth as the HTTP API."
section: server
order: 30
---

# MCP server

`vibecrafted-mcp` exposes the runtime to agents as a Model Context Protocol
server. It reads the same control plane as the HTTP API — MCP and HTTP are
two eyes on one truth, not two databases. Envelopes may differ in shape, but
run identity, counts, and warnings agree because both re-read the same disk.

## Connecting

The server speaks stdio. Register it in your agent client's MCP
configuration:

```json
{
  "mcpServers": {
    "vibecrafted": {
      "command": "vibecrafted-mcp"
    }
  }
}
```

The process reads `$VIBECRAFTED_HOME` (default `~/.vibecrafted`) for the
control plane. Most tools also accept a `home` argument to probe a specific
installation without mutating your shell.

## Tool catalogue

Read-only tools carry the MCP `readOnlyHint` annotation; mutating tools are
marked below and should stay permissioned in your client.

### Ground truth and bootstrap

| Tool                   | Purpose                                                                  |
| ---------------------- | ------------------------------------------------------------------------ |
| `vc_init`              | Cold-start synthesis: git ground truth + doctor health + board snapshot. |
| `vc_repo_full`         | Git state for a project: branch, ahead/behind, dirt, stashes, worktrees. |
| `vc_doctor`            | Runtime health summary from the installer doctor.                        |
| `vc_loct_capabilities` | Live capability discovery for the perception/intent foundation tools.    |

### Board and runs

| Tool              | Purpose                                                                         |
| ----------------- | ------------------------------------------------------------------------------- |
| `vc_board_status` | Control-plane snapshot: active runs, recent runs, events, warnings.             |
| `vc_run_status`   | Look up one run by id from synced control-plane state.                          |
| `vc_await_run`    | Join the shared vc-server run monitor; idle timeout and hard cap stay distinct. |
| `vc_run_observe`  | Bounded cursor pull of run events and transcript deltas (capped at 64 KiB).     |
| `vc_launch`       | **Mutating.** Launch a workflow; spawns an agent process.                       |
| `vc_run_launch`   | **Mutating.** Alias of `vc_launch` for run-lifecycle naming symmetry.           |
| `vc_run_stop`     | **Mutating.** Request graceful stop of an active run, with an audit event.      |
| `vc_run_retry`    | **Mutating.** Retry a run from its stored launch metadata.                      |
| `vc_run_blocked`  | **Mutating.** Mark an active run as blocked, with an audit trail.               |

### Lifecycle (vc-ship supervision)

| Tool                       | Purpose                                                                     |
| -------------------------- | --------------------------------------------------------------------------- |
| `vc_lifecycle_runs`        | List lifecycle runs, newest first; filter by workflow id.                   |
| `vc_lifecycle_status`      | One lifecycle run's status: stage, baton, controls, cargo.                  |
| `vc_lifecycle_approve`     | **Mutating.** Approve the transition — launch the baton's next stage.       |
| `vc_lifecycle_interrupt`   | **Mutating.** Stop the live stage and mark the run interrupted.             |
| `vc_lifecycle_force_audit` | **Mutating.** Make an audit the next lifecycle move.                        |
| `vc_lifecycle_accept_dou`  | **Mutating.** Consciously accept a Definition of Undone gap, with a trace.  |
| `vc_lifecycle_fallback`    | **Mutating.** Steer the baton back to an earlier, manifest-validated stage. |

`vc_lifecycle_approve` refuses while baton cargo (previous-stage reports) is
missing, unless forced — and the override is traced in the run's operator
actions.

## Resources

Beyond tools, the server publishes MCP resources for cheap reads:

| Resource URI                                  | Content                                       |
| --------------------------------------------- | --------------------------------------------- |
| `vibecrafted://board/runs`                    | Active + recent runs snapshot.                |
| `vibecrafted://runs/{run_id}/status`          | Current projection for one run.               |
| `vibecrafted://runs/{run_id}/events`          | Bounded event read for one run.               |
| `vibecrafted://runs/{run_id}/transcript`      | Bounded transcript read for one run.          |
| `vibecrafted://runs/{run_id}/report`          | Bounded report read for one run.              |
| `vibecrafted://control-plane/events/{run_id}` | Last 50 operator-stream events for a run.     |
| `vibecrafted://lifecycle/schema`              | JSON Schema for the lifecycle state contract. |
| `vibecrafted://capabilities/foundations`      | Live foundation-tool capability discovery.    |

## MCP or HTTP?

Use stdio MCP from agents and IDEs; use the [HTTP API](/docs/http-api/) from
scripts, bots, and anything that already speaks HTTP. Both read the board
from disk. The board parity contract is logical, not byte-identical: the
shared keys are `active_runs`, `recent_runs`, `warnings`, `events`,
`generated_at`, and `settlement_counts`. If projections ever look
inconsistent, the fix is to re-read the control plane — never to build a
reconciling store.
