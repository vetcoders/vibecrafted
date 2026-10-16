# Vibecrafted Housekeeping

`vc-server-supervisor` owns recovery and housekeeping scheduling in-process.
`vc-server` owns the API and local metrics. Frame hosts sessions. There is no
permanent Guardian, housekeeping or metrics child in this service contract.

## Inspect before changing a plan

```bash
scripts/vibecrafted-housekeeping --json
vibecrafted housekeeping schedule status
vibecrafted server status
```

The planner reports each actual filesystem (`device`, free bytes, roots and
missing roots). Capacities are not added across APFS volumes. Missing process,
open-file, lock, identity or capacity observations defer cleanup. Estimated
candidate bytes do not guarantee physically reclaimed disk space.

Use those facts to consciously reduce concurrency, correct the plan, wait, or
choose an explicitly authorized override. There is no automatic capacity waiter
or batch relaunch mechanism. Quota and provider choice belong to the common
server/dispatch contract; this guide does not invent a quota CLI.

## Hold a build chain lease

Source-checkout command (installed availability requires the new Runtime Pack):

```bash
scripts/vc-build-lease --root "$CARGO_TARGET_DIR" -- \
  /bin/bash -c 'cargo check && cargo test'
```

The regular `.vc-build-lease` stays shared-locked across both commands and the
idle gap between them. The child inherits its descriptor, so a surviving child
still protects the cache if the wrapper exits. `--lease-root /another/target`
can protect additional actual output roots in the same chain. The release
builder wraps its entire build tree and donor targets with this mechanism.
It never removes or expires another owner's lease by PID or age.

For a new task-owned reconstructible output under the tracked task's root:

```bash
scripts/vc-build-lease --root "$PWD/target" \
  --home "$VIBECRAFTED_HOME" --run-id '<exact-tracked-run-id>' -- \
  /bin/bash -c 'cargo check && cargo test'
```

Registration requires a newly created output directory and records its exact
inode, filesystem and owning run. A pre-existing unregistered target stays
unclaimed. Shared caches can be protected with a lease without granting this
task ownership. Source manifests, source trees, sessions, branches, reports,
unintegrated results and durable receipts are never this cache.

Default dispatch records an immutable creation receipt only when its worktree
owner publishes a new target in a newly managed worker checkout. Creation uses
private random staging, a held directory descriptor and atomic exclusive
publication; it never claims whichever object first appears after a public
`mkdir`. macOS uses `renameatx_np(RENAME_EXCL)` and Linux uses
`renameat2(RENAME_NOREPLACE)`. Unsupported exclusive publication retains an
unclaimed target. Cleanup staging and restoration also refuse replacement.
The first launch attempt consumes that proof; an accepted exact provider run
binds ownership. Missing or lagging run observations, refused launches, lost
proof, raced-in targets, reused checkouts and integrators retain their caches.
A repair or later launch permanently fences the previous cache acceptance;
it does not inherit or transfer ownership. Those retained paths need a separate
explicit lifecycle decision. The original creation/attempt/registration
receipts remain durable under `store/housekeeping/`.

## Completion and acceptance

A green verifier or an exited worker is insufficient. Dispatch's successful
`settle_verified` boundary only marks an existing exact ownership registration.
Deletion separately requires the control plane's current trust-finalized
settlement, matching hashed trust receipt and revision, non-waived acceptance,
a dead owning worker, and the current cache inode. Dispatch-owned cache also
requires the current exact dispatch cut to remain admitted and settled for
that provider run. Launch acceptance alone cannot authorize cleanup. Policy B retires accepted
owned cache; it does not retain a default warm-cache warehouse.

Both Founder settings remain effective:

```toml
[housekeeping]
enabled = true
auto_execute = false
interval_hours = 24
retention_days = 7
```

`enabled = false` disables scheduling. `auto_execute = false` produces plans
only. When both are true, the supervisor also performs a bounded retirement
pass for newly accepted registered caches. Age and names discover candidates;
they never confer deletion authority.

`vibecrafted housekeeping schedule install` registers the script's exact digest
for the supervisor. It creates no additional LaunchAgent. A changed script
requires re-registration. The separate housekeeping plist renderer is retired;
`--background` is retired because the supervisor owns execution. Explicit
manual cleanup remains:

```bash
scripts/vibecrafted-housekeeping --execute \
  --confirm DELETE-REGENERABLE-VIBECRAFTED-STATE
```

Plans and execution receipts stay under `store/housekeeping/`. Cleanup retains
exclusive nonblocking leases on the cache and containing targets through
staging, rechecks after rename, and restores a vetoed tree when its original
path is still vacant. A recreated path is retained alongside the staging tree.
Failed staging paths are appended to `leftovers.txt`. No automatic recursive
retry of retained payload is allowed. One metadata-only retry is bounded to
128 directories and only regular `.DS_Store` files plus empty directories.

## Migration and native acceptance

The release builders materialize real named native hosts for VC-owned Python
manifest entrypoints, including `vc-server-supervisor`, and a `vc-python` door
for internal Python helpers. They embed the pinned CPython library. The
ordinary development uv console scripts remain development interpreter doors. A plist label, shebang or altered
argv is not a process-identity receipt. The macOS terminal executable lives in
one fixed app at `/Applications/Vibecrafted.app/Contents/MacOS/vc-terminal`.
Generations are internal payloads; the launcher refuses to open an alternate
generation app. A standalone macOS pack requires the reviewed notarized fixed
app before activation: missing/malformed/symlinked hosts and a mismatch between
the fixed host and candidate CodeDirectory/Team identity refuse publication
with a pending paired-app handoff. Prior active/config state stays preserved. Linux uses its flat native host.

Parent/integrator migration must preserve config.toml, active Frame sessions,
running agents, Guardian cursor/outbox/deadletters and durable receipts. Stop
an old Guardian only by its verified managed identity. A still-held Guardian
lock prevents embedding and stays visibly degraded; it is never forcibly
reclaimed. Retire a legacy housekeeping plist only with the exact trusted
installer digest recorded for that exact path in a canonical runtime-install
ownership receipt through `retire_legacy_schedule`; an ownership mismatch or
unconfirmed bootout retains it. Per-provider telemetry plist retirement belongs
to the collector migration and likewise requires ownership receipts. Names
alone never authorize deleting foreign files.

After source admission, the parent must perform the authorized idle-safe,
notarized install and inspect actual executable paths, signatures, app bundle
identity, two infrastructure roles, embedded SSE recovery, config switches,
terminal launch command handoff and agent-start confirmation. Isolated process
fixtures do not prove that installed Dock or agent-delivery behavior.

Whole worktree removal remains the separate receipt-owned
`vibecrafted dispatch "$PLAN" --cleanup-settled <dispatch-run-id>` contract.
Cache retirement does not remove worktrees or branches.

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
