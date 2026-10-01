# App capture settlement

`vc-app-update` remains the sole App mutation owner. Replacement keeps the
rollback App while the matching Runtime Pack is pending. Settlement uses the
same `.vc-update.lock/held` inode and requires the installer's actual
`active.json` and `install-receipt.json`, a matching generation/source/module
tuple, no pending rescue/configuration/publication, strict codesign validity, and the exact replacement transaction. The signed carrier's existing read-only `runtime-resolve` validates destination hashes, selectors, native helpers, configuration and launcher lineage before disposal.

UI replacement and `make install-app` save unique receipt, admission, journal,
and capture creation records under
`$VIBECRAFTED_HOME/vibecrafted-product-update/transactions/<transaction>/`.
`install-app-latest.json` and the helper's `app-capture-latest.json` are atomic projections, never deletion authority. The latter also lets same-generation UI repair discover a raw CLI bootstrap without inventing a pending handoff.
Successful CLI installation publishes the bundled pack, relaunches the App,
and settles the capture. A pack or relaunch failure returns failure and retains
recovery. Same-generation UI pack repair can finish a retained local
transaction. Startup replacement adoption settles only after successful pack
publication and retains its handoff on cleanup failure.

Settlement first persists an immutable disposal plan outside the payload. It
binds the parent/capture inode and the signed rollback tree. Deletion uses
no-follow directory descriptors and checks every remaining entry against that
plan. Failure returns exit 20; rerunning the exact settlement retries the
remaining entries, including after partial deletion. A compact immutable
settlement receipt records `disposed` only after the capture directory is gone.
`.DS_Store` is host metadata; a symlink or unknown payload is refused.
Original replacement evidence remains after disposal. Recovery archives the
original replacement journal before advancing its own journal.

For a retained transaction, after the canonical installer has completed:

```bash
transaction='<exact transaction>'
records='<unique transaction directory>'
app='/Applications/Vibecrafted.app'
helper="$app/Contents/Helpers/vc-app-update"
/bin/bash "$helper" --mode settle --source "$app" --destination "$app" \
  --receipt "$records/receipt.json" --admission "$records/admission.json" \
  --journal "$records/journal.json" --transaction "$transaction" --plan-only
```

Review the generated plan, then run the same command without `--plan-only`.
UI transaction filenames are `replacement-receipt.json`,
`replacement-receipt.json.admission.json`, and
`replacement-receipt.json.journal.json`. Do not reconstruct a UI handoff for a
CLI transaction. Replacement resume with `--complete` also runs publication
and settlement; a raw replacement receipt alone never means installation
completed.

## Historical captures

An old filename or codesign display is insufficient authority. The existing
owner supports explicit `--historical-evidence FILE` on the settlement command.
Pass the original archived replacement receipt, READY admission, and terminal
replacement journal without rewriting their internal bindings. A journal that
already advanced to terminal restore/recover is also admitted when the exact
recovery receipt and journal prove the same transaction and original prior
identity. An incomplete or failed journal is refused. The historical
evidence object has this shape:

```json
{
  "schema": "io.vetcoders.vibecrafted.app-capture-history.v1",
  "transaction": "exact-original-transaction",
  "capture": "/Applications/.vc-update-capture-exact-original-transaction",
  "capture_inode": [123, 456],
  "parent_inode": [123, 789],
  "successor_settlements": [
    "/absolute/path/to/verified-successor-settlement.json"
  ]
}
```

Read the device/inode values from the physical paths before planning. Every
successor must already be settled and its prior identity must equal the
previous candidate identity; the chain must reach the strictly verified
installed App and its matching published runtime. The historical capture's
rollback Apps must strictly verify against the original prior CDHash. If a
completed restore/recover left `failed-new.app`, also supply `recovery_receipt`
and `recovery_journal` paths. These must prove the same transaction, destination,
prior identity, and terminal recovery; the quarantined App must match the
original candidate CDHash. The chain then starts at the recovered prior
identity. All other entries fail closed.

Start with `--plan-only`. Missing original records, a missing recovery record,
a gap in the successor chain, incomplete publication, signature damage, or
changed inodes returns a specific refusal. Root gathers that missing evidence
and executes admitted host cleanup; workers verify this owner in fixtures.
Runtime release retirement and live native session management are separate
owners.

## Explicit legacy inventory admission

When singleton evidence was overwritten or the original attempt failed, Root can
admit exactly one reviewed capture through the same settlement owner. This is
explicit reconciliation, never automatic discovery by folder name. It does not
mint a successful replacement receipt or invent a causal successor chain.

Use `--historical-evidence` with schema
`io.vetcoders.vibecrafted.app-capture-legacy-admission.v1` and these fields:

- `transaction`, `capture`, `destination`: exact canonical original paths/id.
- `identifier`, `team_id`: independently verified signed product ownership.
- `parent_inode`, `capture_inode`: `[device, inode]` from fresh physical paths.
- `parent_uid`, `uid`: actual parent and capture ownership; capture must be owned
  by the invoking uid. `/Applications` normally has a different owner.
- `apps`: exact child names from `prior.app`, `displaced.app`, `failed-new.app`,
  each containing `inode: [device, inode]` and `identity: "cdhash:<verified>"`.
- `entries`: exact relative recursive inventory. Each value is
  `[device, inode, stat.S_IFMT(mode), uid]` for directories, and additionally
  `[size, mtime_ns]` for regular files. Exclude regular `.DS_Store`; reject
  symlinks and other entry types. The helper independently checks this inventory
  and strict signature validity.
- `history`: `state` is `failed-before-adoption`, `restored`, `missing`, or
  `replaced`; `records` preserves available original evidence as objects with
  absolute `path` and raw-byte `sha256`. Never rewrite their internal bindings.

Use a fresh receipt output path outside the capture, with an existing physical
parent directory. It may name a missing original receipt: this admission branch
uses the reviewed inventory as its authority and records that attribution.

Run the settlement command with `--plan-only`. It produces an immutable plan and
prints `plan_sha256` (SHA256 of sorted compact JSON, excluding the final newline).
Root reviews the plan, then applies the exact command with
`--admit-plan <plan_sha256>` instead of `--plan-only`. An absent or wrong digest
refuses disposal. Current signed App identity and canonical runtime publication
must still match the reviewed plan. Both planning and applying check `lsof` for
open/mapped/cwd/executable references and `ps` for process references. Inability
to establish those observations refuses disposal. No referenced native owner is
killed or restarted.

Partial deletion remains retryable with the same inventory, plan and admitted
digest. The final compact receipt retains `authority: reviewed-legacy-inventory`
and the honest `history`; it never sets `replaced: true`. Root must obtain a new
plan if destination publication changes after review. Workers only exercise this
path in isolated fixtures; Root owns concrete host review and execution.
