# Vibecrafted Housekeeping

This is a Vista-style reset surface: categories, plan first, explicit execute,
background operation, and durable receipts. It is not a recursive wipe of
`~/.vibecrafted`.

## Plan in the background

```bash
scripts/vibecrafted-housekeeping --background
```

The launcher prints a PID and log path. The detached process writes its plan
below `~/.vibecrafted/store/housekeeping/plans/`. `store/` is durable Founder
data and is never scanned as a cleanup candidate.

Default retention is seven days. The plan covers:

- old children of `tmp`, `logs`, `install-transactions`, and `recovery`;
- old build caches inside fleet worktrees (`target`, `node_modules`, `.venv`,
  Python test/cache directories).

Recent candidates, symlinks, and paths overlapping a live process working
directory are retained with a reason. Founder data and unknown top-level paths
are inventoried but never deleted.

## Read the plan

```bash
scripts/vibecrafted-housekeeping --json
```

The summary reports candidate count, eligible count, and reclaimable bytes.
The JSON item list records every protected path and why it was protected.

## Execute the same policy

Execution rescans immediately, so a stale plan cannot authorize deletion.
The confirmation token is deliberately awkward:

```bash
scripts/vibecrafted-housekeeping \
  --execute \
  --confirm DELETE-REGENERABLE-VIBECRAFTED-STATE
```

To detach the destructive pass, add `--background`. Every result is written
below `store/housekeeping/executions/` before the command returns.

## Settled fleet worktrees

The housekeeping script removes caches inside old worktrees, not worktrees
themselves. Worktree deletion remains receipt-owned:

```bash
vibecrafted dispatch "$PLAN" --cleanup-settled <dispatch-run-id>
```

That command validates settled state, retains branches/evidence, and records
the cleanup outcome. Do not replace it with `rm -rf`.

## Runtime rescue footprint

Runtime releases and app Update captures belong to the existing installer/Update
lifecycle, outside this state-home cache policy. Rescue captures publication's
mutation set rather than every historically owned release. Physical mutable
parent trees are copied once; existing immutable generations remain in place.
A new candidate's absence is recorded so rollback can remove that candidate
after restoring the prior configuration and selector.

Rescue checks a conservative copy budget on the runtime filesystem before
allocating evidence and before resumed publication. This is not a reservation
against concurrent disk consumers or a guarantee for separate mounts. A failed
capture discards only its newly allocated, unchanged, unjournaled attempt;
older recovery evidence stays intact. Successful rollback seals the journal
as `rolled_back`, distinct from interrupted `captured` publication.

Automatic retirement of superseded releases and app captures remains unfinished.
The installer and Update completion owners need receipt-bound identity,
complete live/service/detached-session references, one verified healthy rollback,
in-flight protection, and disposal receipts under their existing locks. Neither
size, age, nor an absent process match authorizes deleting this history. The
bounded rescue repair avoids recopying that history; it does not claim that the
accumulated payloads or captures have been reclaimed.
