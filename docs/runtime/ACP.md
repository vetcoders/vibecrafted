# Vibecrafted ACP runtime

`vibecrafted acp` is a thin ACP v1 stdio adapter over the existing
Vibecrafted workflow, lifecycle, and control-plane APIs. It is not a second
agent runtime. The implemented wire contract tracks
[`schema-v1.20.0`](https://github.com/agentclientprotocol/agent-client-protocol/releases/tag/schema-v1.20.0).

## Scope

- Transport: newline-delimited JSON-RPC 2.0 over stdio only.
- Protocol version: ACP `1`.
- Session methods: `session/new`, `session/load`, `session/resume`,
  `session/prompt`, and `session/cancel`.
- Session updates: agent message chunks, tool calls, plans, and available
  commands.
- MCP passthrough: not advertised and rejected when `mcpServers` is non-empty.
- Registry publication and non-stdio transports are outside this adapter.

## Identity and lifecycle ownership

`session/new` reserves the ACP session identity before execution.

For a direct workflow, the first run keeps the MVP invariant:

```text
ACP sessionId == Vibecrafted run_id
```

For `/ship`, the ACP session is the parent lifecycle run. Each lifecycle stage
is launched through the canonical workflow launcher with its own reserved
control-plane `run_id`. Child runs remain first-class entries in
`control_plane/runtime_runs`; the adapter does not collapse or hide fan-out.
The parent receipt is stored in:

```text
control_plane/lifecycle_runs/<sessionId>/
  state.json
  report.md
  transcript.log
```

Lifecycle responses and updates carry:

```json
{
  "_meta": {
    "vibecrafted": {
      "parent_run_id": "<ACP sessionId>",
      "child_run_ids": ["<stage run id>", "..."],
      "stage": "<current stage id>"
    }
  }
}
```

The core `LifecycleRunSpec.run_id` field is optional. Empty preserves the
historical lifecycle allocator; ACP supplies the already-published parent
identity.

## Resume contract

Initialization advertises `loadSession: true` and
`sessionCapabilities.resume` only when the bridge supports artifact restore.
Both load paths require a non-empty runtime-owned report and transcript:

- `session/load` restores metadata, publishes the slash catalog and plan, then
  replays transcript text as `agent_message_chunk` updates.
- `session/resume` restores the same execution context without replay.
- The next prompt becomes a new, first-class child run attached to the restored
  parent session.

Restore never infers success from a missing artifact. Missing state, report, or
transcript returns JSON-RPC error `-32004`. ACP load/resume responses do not
have a `stopReason` field in the stable schema, so gaps are reported as request
errors rather than an invented stop reason. Prompt execution returns
`end_turn`, `cancelled`, or fail-closed `refusal`.

At most 1 MiB of transcript is replayed. Truncation is declared as
`_meta.vibecrafted.transcript_truncated`.

## Plans

`/ship` maps the canonical `vc-ship` stages to ACP agent-plan updates. The wire
discriminator is:

```json
{ "sessionUpdate": "plan", "entries": [] }
```

Every update contains the complete ordered stage list. The current stage is
`in_progress`, successful stages are `completed`, and future stages are
`pending`. A failed lifecycle does not mark unfinished stages completed.

## Slash catalog

`available_commands_update` is compiled from
`WORKFLOW_DEFINITIONS` plus the `vc-ship` manifest. Every advertised verb
includes its canonical argv in metadata:

```json
{
  "name": "implement",
  "_meta": {
    "vibecrafted": {
      "argv": ["vibecrafted", "implement"]
    }
  }
}
```

Text after the slash verb is passed unchanged as the workflow prompt. The ACP
adapter does not parse workflow flags or maintain a parallel command registry.
Unknown slash commands fail closed with `stopReason: refusal`.

## Permissions and cancellation

The hard-stop classifier treats force-push, trunk push, merge, publish, and
deploy as operator buttons. A non-destructive feature-branch `git push`
does not request permission. Remaining hard-stop intents require an ACP
permission request and only explicit `allow_once` continues. Denial,
timeout, and missing decisions fail closed. Accepted overrides are written
through the existing audit event path.

`session/cancel` signals the session and stops the active child run through the
canonical workflow stop API.

## Deterministic smoke

The fake worker is available only when explicitly enabled:

```sh
VIBECRAFTED_ACP_DRY_RUN=1 vibecrafted acp
```

For a `/ship` fixture, `_meta.vibecrafted.dryStages` selects how many leading
stages to exercise (default: `2`). The fixture still produces a parent
state/report/transcript and distinct child identities, which makes cold-load
and resume behavior testable without launching an external agent.
