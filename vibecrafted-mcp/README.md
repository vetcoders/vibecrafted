# vibecrafted-mcp

FastMCP server for the Vibecrafted operator framework. Closes the third
sense of the cold-start contract:

```text
mcp__loctree-mcp__context()        # perception (external)
mcp__aicx-mcp__aicx_intents()      # intentions (external)
mcp__vibecrafted__vc_repo_full()   # ground truth (this server)
```

A thin synthesis layer (`vc_init`) composes the three signals plus a
small set of v0.1 stubs (live failure score, unmade decisions,
unverified claims). The full synthesis brain lands in v0.2.

## Install

From the repository root, in a fresh Python 3.10+ environment:

```bash
pip install -e ./vibecrafted-core ./vibecrafted-mcp
```

The package depends on `vibecrafted-core` as a sibling library and on
`fastmcp>=2.0`.

## Run

```bash
vibecrafted-mcp           # speaks MCP over stdio
vibecrafted-mcp --version # print the package version and exit
vibecrafted-mcp --help
```

## Wire into an agent

```jsonc
{
  "mcpServers": {
    "vibecrafted": {
      "command": "vibecrafted-mcp",
    },
  },
}
```

## Tools

| Tool                                     | Purpose                                                                                                      | Mutates? |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------ | -------- |
| `vc_repo_full(project=".")`              | Full git ground truth (branch, ahead/behind, dirt, recent commits, worktrees, remotes).                      | No       |
| `vc_worktrees(project=".", target=None)` | Read-only worktree inventory, canonical owner receipts and conservative reception triage.                    | No       |
| `vc_doctor(project=None)`                | Vibecrafted runtime health summary (ok / warnings / failures).                                               | No       |
| `vc_board_status(home=None)`             | Operator control-plane snapshot (active runs, recent runs, event tail, warnings).                            | No       |
| `vc_init(project=".", slim=True)`        | Cold-start synthesis: composes the three senses + insight stubs. Slim default keeps the response under ~5KB. | No       |
| `vc_launch(...)`                         | Launch a workflow through the Vibecrafted core runtime. Spawns an agent and writes control-plane artifacts.  | Yes      |
| `vc_run_launch(...)`                     | Lifecycle-name alias of `vc_launch`. Spawns an agent and writes control-plane artifacts.                     | Yes      |
| `vc_run_status(run_id, home=None)`       | Lookup one run from the synced control-plane projection.                                                     | No       |
| `vc_await_run(...)`                      | Bounded wait for a run to reach terminal state. This is not a transcript transport.                          | No       |
| `vc_run_observe(...)`                    | Cursor-pull run events and transcript deltas. Transcript text is capped to 64 KiB per response.              | No       |
| `vc_run_stop(...)`                       | Request graceful stop of an active run and write an audit event.                                             | Yes      |
| `vc_run_retry(...)`                      | Retry a run from stored launch metadata and write retry control-plane artifacts.                             | Yes      |
| `vc_run_blocked(...)`                    | Mark an active run blocked/needs-intervention and write an audit event.                                      | Yes      |
| `vc_loct_capabilities(...)`              | Probe installed Loctree/AICX foundation capabilities.                                                        | No       |

`vc_worktrees` delegates to the same core inventory as `vc-git worktrees list|triage`.
Cleanup, pruning, merge preparation and apply endpoints are deferred and unavailable.

MCP launch is experimental but real. The lifecycle tools are mutating:
they can spawn agents, stop or retry runs, and write control-plane
artifacts under `$VIBECRAFTED_HOME`. Operators should expose them only
through a permissioned MCP client or trusted local agent session.

## Resources

| URI                                           | Returns                                                                           |
| --------------------------------------------- | --------------------------------------------------------------------------------- |
| `vibecrafted://board/runs`                    | `{generated_at, active_runs, recent_runs, warnings}` from the live control plane. |
| `vibecrafted://control-plane/events/{run_id}` | Last 50 events for `run_id` from the operator event stream.                       |
| `vibecrafted://runs/{run_id}/transcript`      | Bounded transcript text envelope for `run_id`.                                    |
| `vibecrafted://runs/{run_id}/events`          | Bounded event delta envelope for `run_id`.                                        |
| `vibecrafted://runs/{run_id}/status`          | Current control-plane projection for `run_id`.                                    |
| `vibecrafted://runs/{run_id}/report`          | Bounded report text envelope for `run_id`.                                        |

## Constraints

- Launch/stop/retry/block tools mutate runtime state and must be
  treated as permissioned operator actions.
- `vc_await_run` waits for terminal state only. Do not use it to
  stream transcripts or reports.
- Transcript and report reads must stay bounded; cursor-based
  observation is the baseline for live UIs.
- Each tool response should remain bounded by FastMCP's 25K token cap;
  defaults aim for <=20K to leave a safety margin.
- The doctor surface degrades gracefully (returns `unavailable=true`)
  when the package is consumed outside a vibecrafted source checkout.

## Development

```bash
pip install -e ./vibecrafted-core ./vibecrafted-mcp
pip install pytest
pytest vibecrafted-mcp/tests/
```

## License

`𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.` with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
