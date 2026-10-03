# Run observability ownership

Supervised run state is owned by `vc-server` and the control plane. A run's
canonical evidence remains its metadata, report, transcript, settlement and
failure records. Terminal sessions are workspaces; they are not a second run
database.

## Product surfaces

The server exposes the canonical browsing surfaces:

- `/runs` — run index;
- `/run/{run_id}` — run detail;
- `/api/control/runs` — machine-readable run census;
- `/api/control/runs/{run_id}` — canonical run detail;
- `/api/control/runs/{run_id}/transcript` — transcript stream.

VOC and the Frame `Agent Workspaces` canvas may project these server-owned
routes. Consumers must discover the configured server through the existing
control-plane configuration; they must not guess a localhost port.

## Lifecycle boundary

Workflow launchers, the Python dispatcher and Guardian finalize canonical
artifacts only. They do not create, move, rename or close terminal sessions or
tabs for run presentation. In particular, supervised lifecycle code does not
materialize `Live runs`, `Finalized runs`, `Failed runs` or `Needs attention`
sessions.

The Frame left rail lists physical sessions/workspaces and their live process
tabs. It has no F/X/N controls, bucket counters or synthetic live-run row.

## Historical migration boundary

Existing sessions are never migrated, killed, renamed or filtered by this
change. A user workspace may legitimately have one of the historical names;
name matching alone is therefore not evidence that a session is synthetic.
After upgrade those sessions appear as ordinary workspace rows until the user
chooses what to do with them.

## Manual compatibility

`vibecrafted_core.run_triage` and `vc-frame triage-run` remain available as
legacy/manual compatibility tools for explicit forensic or migration work.
They preserve the durable classifier and its receipts, but no supervised
launcher, dispatcher or Guardian startup path invokes them automatically.
New product code must use server/VOC discovery instead of creating bucket
sessions.

## Verification obligations

For lifecycle changes, prove all of the following:

1. canonical meta/report/transcript finalization still happens on success and
   failure;
2. no supervised caller invokes terminal triage or creates a viewer tab;
3. server run index, detail and transcript routes remain reachable through the
   configured server ownership path;
4. Frame rail rows are derived only from physical sessions and their live
   process tabs;
5. historical same-name sessions remain visible and untouched.
