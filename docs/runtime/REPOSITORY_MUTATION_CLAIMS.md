# Repository mutation claims

Vibecrafted prevents two Living Tree sessions from silently editing the same
logical repository paths. The authority is the local registry at
`$VIBECRAFTED_HOME/control_plane/repository_claims/registry.json`; server, MCP,
UI, and Slack are projections and are not required for a claim to count.

Claims are atomic across every requested path. Overlap is hierarchical:
`vibecrafted-core/` conflicts with `vibecrafted-core/vibecrafted_core/cli.py`.
Git's common directory identifies sibling worktrees as the same repository,
while separate clones have independent registries.

## Interactive and hook use

Acquire the complete intended edit set before the first mutation:

```sh
vibecrafted claims --json acquire \
  --repo "$PWD" \
  --run-id "$VIBECRAFTED_RUN_ID" \
  --session-id "$VIBECRAFTED_SESSION_ID" \
  --agent codex \
  vibecrafted-core/vibecrafted_core/cli.py \
  vibecrafted-core/tests/test_cli.py
```

The returned `claim.claim_id` is the stable token for hooks and projections.
Use it for `heartbeat`, `status`, and `release`. `release` is immediate and
idempotent. `list` reports owner liveness, stale claims, reclaimability, and
any impossible stored overlaps in stable JSON.

A claim is never stolen because its heartbeat is old. Automatic reclaim needs
positive owner-death evidence plus the bounded grace. Operator override is
explicit and audited:

```sh
vibecrafted claims --json force-release CLAIM_ID \
  --reason "operator confirmed abandoned session after host restart"
```

Acquired, conflict, released, and reclaimed transitions are appended to the
existing control-plane event stream as `repository_mutation.*` events.

## Dispatch

Dispatch consumes the existing `ExecutionEnvelope.owned_paths`. It acquires
the full multi-path claim before any worker launcher is called, writes the
claim or conflict into the dispatch receipt ledger, keeps the heartbeat fresh
while awaiting the worker, and releases after the dispatch settles or fails.
Conflict rejection does not reset, stash, delete, or rewrite either tree.
