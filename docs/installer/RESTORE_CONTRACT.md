# Restore Contract (honest)

Product promise for **update / reinstall / detach** — what comes back, and what does not.

This document exists so installer copy, update prompts, and marketing never
outrun the engine (audit SF-6).

## Layers

| Layer                  | Identity                    | Survivability                                                                                     |
| ---------------------- | --------------------------- | ------------------------------------------------------------------------------------------------- |
| **Workspace**          | vc-frame session name       | Detach + session serialization can resurrect **layout, tabs, panes, cwd, command line to re-run** |
| **Pane**               | `PaneId` / terminal pane id | Slot in the workspace; process may need re-exec after host reboot                                 |
| **Run**                | vibecrafted `run_id`        | Control-plane ledger + report + transcript on disk                                                |
| **Agent conversation** | provider session / AICX     | Durable JSONL / catalog — **not** the same as a live pane process                                 |

## Guaranteed (when features are enabled)

With product config:

- `on_force_close "detach"`
- `session_serialization true`
- `serialize_pane_viewport true` (bounded scrollback)

Then:

1. **Close the client terminal** → sessions detach; reattach with `vc-start` / `vc-frame attach <name>`.
2. **Reinstall framework tools under lease** → launcher + skills update; **existing frame sessions are not deliberately killed** by config install alone.
3. **After binary swap of vc-frame** → resurrectable sessions may reappear via frame cache; **verify** with `vc-frame list` / doctor. Cross-version resurrection is **best-effort**, not a legal guarantee until e2e green.

## Explicitly NOT guaranteed

- Live **in-flight** LLM tool calls mid-token after host reboot.
- Agent **RAM** / open network sockets after kill -9 or power loss.
- Identity of a pane process after force-kill of the frame server.
- “Exactly the same scrollback forever” beyond `scrollback_lines_to_serialize`.

## Allowed product copy

**OK:**

> Your vc-frame session layout will be restored where the engine supports resurrection. Control-plane runs keep their `run_id`, reports, and transcripts on disk. Detach is safe; kill-session is deliberate.

**Not OK (until e2e proves otherwise):**

> All agent sessions return to exactly the same state you see now.

## Installer / update UX

- Post-install launch offers **Start here** cockpit (`vc-start`) — onboarding content, not restore magic.
- Update prompts must cite this contract or the narrower sentence above.
- Auto-update must not silent-swap binaries while claiming full agent restore.

## Verification checklist (before widening copy)

1. Two sessions (operator + agent room) → `vibecrafted update` → both listed with layout.
2. Detach client → reattach → panes present.
3. Host reboot → resurrect list non-empty OR honest “none” with next steps.
4. Kill-session → does **not** auto-return (operator intent).

## Reviewed legacy runtime-copy retirement

The existing installer owns this path under its install lease. It preserves
live processes, current/rollback generations, pending transactions and canonical
provider configuration references. Missing provider configs are normal; unreadable,
aliased or ambiguous configs refuse retirement. Provider contents are never printed.
The two historical empty `skills/pl/vc-canary/{plugins,scripts}` leaves are admitted
only inside an otherwise verified generation; hidden children and pointers refuse.

Root gathers and reviews one exact legacy publication copy at a time. These
commands assume the admitted source checkout and a compatible Python interpreter:

```sh
runtime="$HOME/.local/share/vibecrafted"
copy="$runtime/.installer-backups/publication-EXACT"
evidence="/absolute/private-review/legacy-evidence.json"

# Both commands are read-only, including the installer receipt and lease file.
env -u PYTHONPATH python3 scripts/vetcoders_install.py runtime-repair \
  --runtime-home "$runtime" --retire --plan --legacy-inventory "$copy" --json \
  > /absolute/private-review/inventory-envelope.json
# Root extracts the envelope's evidence object, verifies roles/history and saves
# it to $evidence. Do not feed the envelope itself as the evidence document.
env -u PYTHONPATH python3 scripts/vetcoders_install.py runtime-repair \
  --runtime-home "$runtime" --retire --plan --legacy-evidence "$evidence" --json \
  > /absolute/private-review/plan-envelope.json

# Root reviews the exact plan and uses its plan_sha256; this is the mutation.
env -u PYTHONPATH python3 scripts/vetcoders_install.py runtime-repair \
  --runtime-home "$runtime" --retire --legacy-evidence "$evidence" \
  --admit-plan EXACT_REVIEWED_SHA256 --json
```

The evidence schema is `vibecrafted.runtime-retirement-legacy.v1`. Required fields:

| Field             | Contract                                                                                                                       |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `path`            | Exact physical publication-copy root under runtime `.installer-backups`                                                        |
| `parent_identity` | Fresh `[device, inode]` of its physical parent                                                                                 |
| `proof`           | Closed observed `identity`, `entries`, `bytes`, `pointers: true` from inventory                                                |
| `roles`           | Exact top-level name → `{role, destination}`; independently checked against canonical receipt/archive bindings                 |
| `history`         | Honest `state`: `failed`, `restored`, `missing`, or `published`; nonempty `records` with absolute `path` and raw-byte `sha256` |
| `leaves`          | Every regular/pointer leaf except regular `.DS_Store` → `preserve`; directories are structural                                 |

Entry records are `[kind, hash_or_pointer, mode, [device,inode]]`; regular files
also carry byte size. The inventory is observed evidence, never a reconstructed
historical preimage. Root supplies original receipt records from the canonical
current receipt or a rescue attempt's `original-receipt.json` with its matching
`original-receipt.sha256`. The current receipt alone is an honest current-only
binding; it does not establish an archived original or successful old publication.
The inventory template defaults to `history.state: missing`; Root must correct
that attribution from available records. Unknown top-level paths, unbound roles,
escaping pointer topology, changed identities/hashes or stale review refuse.

This conservative path preserves all leaf bytes, including scripts/defaults
whose historical shipped lineage has not been independently classified. It does
not recursively copy the whole directory. The drift owner archives unique file
bytes and opaque pointer-target bytes by full SHA256, records original kind/mode,
and verifies each archive before deletion. Repeated bytes across copies share one
archive. It never writes captured preferences back over current preferences.
Durable archives permit retry after interrupted preservation; bounded receipt
checkpoints avoid per-leaf full-receipt writes. A fresh locked reference census
and verified current publication precede disposal.

Apply saves an immutable plan, exact original receipt bytes, a resumable deletion
state and a compact final receipt in `.installer-backups/retirement`. Planning
writes none of them. Missing/wrong `--admit-plan` refuses every mutation. Repeat
with the same evidence/digest resumes partial deletion or returns its settlement;
original failed/restored/missing history remains intact. Publication drift requires
a new inventory and review. Source/fixture success is not host cleanup acceptance.

The old rescue `pre-rescue` capture remains a specific residual. Its directory
labels have aggregate hashes but no historical per-leaf hashes. The existing
rescue owner must authenticate each original capture token, separate the 21
generation-directory payloads from four configuration and three agent-skills directory
captures, validate owned generation contents through carrier lineage, and preserve
all config/state and unproven leaves through the drift owner before proposing
disposal. The three changed generations need separate proof/preservation; neither
`.DS_Store` speculation nor a new aggregate inventory closes their ownership.
This admission path refuses rescue captures and never rewrites `label.json`.
