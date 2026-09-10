# Required Set — what a working install actually needs

Teardown used to be a manifest problem: `vibecrafted uninstall` removed what
`.vc-install.json` remembered, and everything else stayed. On a machine that
has been upgraded since 3.7.x that manifest never saw `releases/`, `providers/`,
`server/`, the config trees, or any macOS Library state — 3.6 G under
`~/.local/share/vibecrafted` alone survived every teardown
(catalog: `2026-08-19_vibecrafted_framework-disk-catalog.md`).

This document inverts the question. Instead of listing what uninstall removes,
it fixes **the required set**: the smallest collection of paths that must exist
for each launcher and flow to work. Current Runtime Pack installs use a closed,
hashed ownership receipt. Legacy/source installs still need discovery as a
fallback because their historical manifests did not see the full product.

Implementation: `cmd_runtime_install`, `cmd_runtime_uninstall`,
`_build_uninstall_inventory`, and `_managed_tools_entry` in
`scripts/vetcoders_install.py`. The exact same installer is embedded under
`Vibecrafted.app/Contents/Resources/runtime/scripts/`; AppDelegate delegates to
it and does not write the installation itself. In-app Check for Updates
(`docs/installer/IN_APP_UPDATE.md`) reuses that same installer for same-App
pack repair. A newer App is replaced by `Contents/Helpers/vc-app-update`, then
the new App publishes the matching pack. The running process does not publish a
newer pack under the old App.
Regression coverage:
`tests/tui/test_installer_uninstall.py`, `tests/tui/test_installer_restore.py`.

## 1. The required set

Every row is load-bearing: delete it and the named flow stops working.

| Surface           | Required path                                                                                                           | Needed for                                                                                                |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Launcher shims    | `~/.local/bin/{vibecrafted, vc-*}` — one file per `PYTHON_ENTRYPOINT_LAUNCHERS` entry, plus the compat pack             | every CLI entrypoint; they are the uv-receipt entrypoints and resolve into the current tools generation   |
| PATH wiring       | the `_launcher_path_line()` guard in `~/.zshrc` / `~/.bashrc`                                                           | the shims being on `PATH` in a fresh shell                                                                |
| Stable pointer    | `~/.local/share/vibecrafted/tools/vibecrafted-current` (symlink) → the active `releases/<version>/`                     | foundations, doctor, CLI and agent projections resolving the same immutable generation as the App         |
| uv environments   | `<uv tool dir>/{vibecrafted, vibecrafted-mcp}`, `<uv tool dir>/vibecrafted-iterm2` where the iTerm2 plugin is installed | the interpreters the shims exec; owned by uv, not by us                                                   |
| Active release    | `~/.local/share/vibecrafted/active.json` + the one `releases/<version>/` named by both pointers                         | app/runtime handoff — `active.json` carries `runtime_root` and `app_root`                                 |
| Ownership receipt | `~/.local/share/vibecrafted/install-receipt.json`                                                                       | deterministic reset, collision restore, and locally-modified-file refusal                                 |
| Runtime installer | `<release>/scripts/vetcoders_install.py` plus its bundled import closure                                                | the same install/uninstall implementation for App and CLI                                                 |
| Provider          | `~/.local/share/vibecrafted/providers/vc-slack-agent/current` (symlink) + the one generation it names                   | `vc-slack` and the Slack bridge                                                                           |
| Server assets     | `~/.local/share/vibecrafted/server/site/`                                                                               | the local dashboard/server surface                                                                        |
| Skills store      | `<tools/vibecrafted-current>/vibecrafted-core/vibecrafted_core/skills/`                                                 | the one canonical copy of every skill                                                                     |
| Skill projections | `~/.<runtime>/skills/<skill>` symlinks into the store, per installed runtime                                            | agents seeing the skills at all                                                                           |
| Install state     | `~/.vibecrafted/.vc-install.json` (legacy installs: the same file next to the store)                                    | update/uninstall knowing what this install registered                                                     |
| Required tools    | `loct`, `loctree-mcp`, `aicx`, `prview`, `screenscribe` plus the `vc-*` projections                                     | complete agent product; every named tool belongs to the product payload, with no optional-product fiction |
| Frame config      | `~/.config/vibecrafted/vc-frame/`, `~/.config/vetcoders/frontier/`                                                      | `vc-frame` / `vc-start` cockpit; no private top-level `~/.config/vc-frame`                                |
| App bundle        | `/Applications/Vibecrafted.app` when the DMG channel is used                                                            | optional native transport/onboarding shell; CLI runtime must remain first-class without it                |

Anything not in this table is disposable. In particular: **there is no separate
tools generation in a Runtime Pack install.** `active.json` and
`vibecrafted-current` select one release generation; the latter is only a stable
filesystem projection for consumers that cannot read JSON. One active release
and one provider generation are required. Every other generation is retained
history with no consumer.

## 2. Discovery patterns uninstall removes

| Surface                  | Discovery pattern                                                                                                                           | Why it is removable                                                                                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Legacy tools generations | `tools/vibecrafted-generation-*`                                                                                                            | superseded source-installer generations; Runtime Pack installs point `vibecrafted-current` at `releases/<version>`       |
| Incoming payloads        | `tools/.incoming-*`                                                                                                                         | interrupted download staging                                                                                             |
| Atomic staging           | `tools/..vibecrafted-*`                                                                                                                     | half-published generations left by an interrupted publish                                                                |
| Handoff receipt          | `tools/.vibecrafted-current-handoff.json`                                                                                                   | per-publish marker                                                                                                       |
| Install lease            | `tools/.vibecrafted-install.lock`                                                                                                           | transient cross-process lock; **the teardown itself creates it**, so it is registered in the inventory up front (see §4) |
| Finder metadata          | `tools/.DS_Store`, `<runtime home>/.DS_Store`                                                                                               | inert metadata inside directories we own end to end; left behind it keeps the parent unprunable                          |
| Releases                 | `<runtime home>/releases/`                                                                                                                  | rebuilt from the payload on the next install                                                                             |
| Providers                | `<runtime home>/providers/`                                                                                                                 | rebuilt from the payload on the next install                                                                             |
| Server assets            | `<runtime home>/server/`                                                                                                                    | shipped inside the payload                                                                                               |
| Active pointer           | `<runtime home>/active.json`                                                                                                                | meaningless once the release it names is gone                                                                            |
| Runtime receipt          | `<runtime home>/install-receipt.json`, after its plan has been applied                                                                      | per-install ownership evidence, not durable Founder data                                                                 |
| Framework config         | children of `~/.config/vibecrafted/` except `*.env`                                                                                         | generated: themes, shell fragments, plists                                                                               |
| Frame config trees       | `~/.config/vibecrafted/vc-frame/`, legacy `~/.config/vc-frame/`, `~/.config/vetcoders/frontier/`                                            | generated config/symlink farms plus their own `.bak*` / `.stale*` snapshots                                              |
| Server LaunchAgent       | `~/Library/LaunchAgents/io.vetcoders.vibecrafted.server.plist`                                                                              | product-owned supervisor definition; booted out before removal                                                           |
| Launchd job (macOS)      | `~/Library/LaunchAgents/com.vetcoders.vibecrafted-slack-bridge.plist`                                                                       | provider service definition; a loaded job ends at logout or explicit bootout                                             |
| iTerm2 profiles (macOS)  | `~/Library/Application Support/iTerm2/DynamicProfiles/vibecrafted*.json`                                                                    | written by the iTerm2 plugin                                                                                             |
| App support (macOS)      | `~/Library/Application Support/{io.vetcoders.vc-frame, com.vibecrafted.vc-board, com.vibecrafted.vc-term}`                                  | framework runtime state                                                                                                  |
| Caches (macOS)           | `~/Library/Caches/io.vetcoders.vc-frame`                                                                                                    | cache                                                                                                                    |
| Preferences (macOS)      | `~/Library/Preferences/{io.vetcoders.vibecrafted, com.vibecrafted.vc-board, com.vibecrafted.vc-board.debug, com.vibecrafted.vc-term}.plist` | framework preference domains                                                                                             |
| Shell rc lines           | the marked Vibecrafted block in `~/.zshrc` / `~/.bashrc`                                                                                    | edited in place, never truncated                                                                                         |

Empty parents (`tools/`, the runtime home, `~/.config/vibecrafted/`) are removed
**only if empty** after their children are gone. A single preserved stranger keeps
the directory, and the inventory says so in its reason line.

## 3. Preserved, and why

| Surface                                                            | Action   | Reason                                                                                |
| ------------------------------------------------------------------ | -------- | ------------------------------------------------------------------------------------- |
| `~/.config/vibecrafted/*.env`                                      | preserve | Founder secrets (Slack tokens and friends); never removed, never copied into a backup |
| pre-existing Founder-data children listed below                    | preserve | durable Founder work and generated artifacts                                          |
| `<runtime home>/bin/*`                                             | preserve | binary ownership is product-managed outside installer state                           |
| Unrecognized `tools/` siblings                                     | preserve | not a Vibecrafted-managed payload name                                                |
| Unrecognized runtime-home children                                 | preserve | discovery has no evidence they are ours                                               |
| `<uv tool dir>/{vibecrafted, vibecrafted-mcp, vibecrafted-iterm2}` | preserve | uv owns them; the printed plan tells the Founder to run `uv tool uninstall`           |
| `/Applications/Vibecrafted.app`                                    | preserve | installed from the DMG; removed by dragging to Trash                                  |

Rule: an unrecognized name is preserved and printed. Discovery widens ownership
by adding known names, never by claiming whatever it finds.

For a clean profile where the Runtime Pack receipt says the installer created
`~/.vibecrafted`, reset removes that whole root, including runtime state created
after installation. If the root pre-existed, its direct children are classified
by the canonical grammar below.

### Runtime state under `~/.vibecrafted`

`vibecrafted_core/runtime_paths.py` is the only source of truth for these
classes. The host-Python installer loads that file directly; it does not import
`vibecrafted_core/__init__.py` and does not keep a second name list.

| Class             | Direct child names                                                                                                                                                                       | Uninstall action                         |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| **runtime-state** | `control_plane/`, `server/`, `foundation/`, `locks/`, `runtime/`, `install-transactions/`, `recovery/`, `tmp/`, `logs/`, `.vc-install.json`, `START_HERE.md`, `install.log`, `.DS_Store` | remove after backup and active-run guard |
| **founder-data**  | `artifacts/`, `inbox/`, `reports/`, `plans/`, `prompts/`, `specs/`, `backups/`, `worktrees/`, `monitor/`, `charter/`, `trust/`, `loctree/`, `vibecrafted/`, `.git/`, `.loctree/`         | preserve                                 |
| **unknown**       | every other direct child, including loose Founder files such as PDF/PNG exports                                                                                                          | preserve and print in the plan           |

Before any wet mutation, the product reads `GET /api/control/state` and accepts
the response only when its `control_plane` identity matches this exact
`VIBECRAFTED_HOME`. Active run IDs refuse a normal uninstall. `--drain` asks the
public `vibecrafted stop <agent> --run-id <id>` verb to stop each qualified run,
waits for the same board to report zero active runs, and only then enters runtime
quiesce and filesystem teardown. A mismatched server response is never allowed
to drain another home.

## 4. Invariants

**Receipt installs refuse drift before remove.** Runtime Pack uninstall hashes
every owned regular file and verifies every owned symlink target before teardown.
A locally modified launcher/config/projection is a conflict and stops the
operation before the service or generation is removed. Pre-install collisions,
including agent-native skill paths, are copied under
`<runtime home>/.installer-backups/` and restored during a successful reset.
Only projection directories created by this installer are candidates for
cleanup, and they are removed with `rmdir` semantics only when empty.

**Legacy backup before remove.** Every discovery `remove` record passes through
`create_teardown_backup`, which snapshots each present path into
`~/.vibecrafted/backups/installer/<timestamp>/` with a `restore-manifest.json`
and a self-contained `restore.py`. `vibecrafted restore` replays that manifest by
absolute path. New surfaces added to the inventory inherit this for free — there
is no removal path that bypasses the backup pass. Preserved paths (secrets,
Founder data) are never copied into a backup, so a teardown kit never becomes a
secret leak.

**Uninstall converges.** A second run over a torn-down install must print
`Nothing to uninstall`. Two things make that true:

- `has_work` counts a `remove`/`edit` record only when its path is actually
  present, and a `remove-if-empty` record only when the directory exists and is
  empty.
- The install lease is registered in the inventory _before_ it exists, because
  `_teardown_owned_runtime_for_uninstall` takes the cross-process lease after the
  inventory is built. Pure discovery never saw the lockfile it creates, so the
  file survived, kept `tools/` non-empty, and made every later uninstall claim
  work forever. Registering it ahead of time is safe: both the backup pass and
  the removal pass re-check presence, so an absent path is never deleted unbacked
  and never printed as planned work.

**A dry run is dry.** `--dry-run` still acquires the install lease to observe the
service plane safely, and removes the lockfile again if it was the one that
created it.

**Secrets never move.** `*.env` under `~/.config/vibecrafted/` is preserved, not
removed and not backed up.

## 5. What this does not cover

- Retention _during_ normal operation. Uninstall now removes all generations, but
  nothing prunes them on a live machine — 25 provider generations and 6 releases
  still accumulate. That is a separate cut.
- Publishing upstream PRView release archives. The carrier currently prebuilds
  the exact crates.io release because the documented GitHub Release channel has
  no assets. Customers still receive a ready binary and never need Cargo; the
  upstream release channel should be repaired so a future carrier can verify
  and embed its archive directly.
- Rebuilding upstream AICX release archives without CI host paths. Version
  `0.12.5` archives are checksum-correct but name their macOS builder under
  `/Users`; the carrier therefore prebuilds exact commit `ced57997` with path
  remaps rather than weakening payload hygiene.
- `$TMPDIR` test scratch. Owned by the test suite, not by the installer.

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_

## 5. Two guards added 2026-08-28 (dragon incident)

- **Socket namespace is uid-keyed.** `/tmp/vc-frame-<uid>` belongs to the
  machine, not to `$HOME`. Uninstall resolves it through
  `_vc_frame_socket_dir()`, which honours `VC_FRAME_SOCKET_DIR`; every sandboxed
  round-trip (`tests/runtime_uninstall_roundtrip.sh`) sets that variable so a
  test never unlinks the founder's live sessions.
- **An upgrade never takes tools backwards.** `runtime-install` compares the
  candidate's `runtime-pack-provenance.json` build date with the active
  generation's and refuses an older pack, naming every component revision that
  would regress (`vc-frame: f7755692 -> 915ca04e` is the 2026-08-27 case that
  orphaned 12 live sessions). `--allow-older-runtime` makes a downgrade explicit.

## 6. Explicit rescue when historical rollback bytes are missing

Normal `runtime-install` / uninstall / rollback stay strict: every receipted
backup path is `lstat`ed before publication. Missing historical rollback
preimages therefore block a regular upgrade. That is not a license to edit
the receipt.

The single supported owner is still
`scripts/vetcoders_install.py runtime-install --rescue`. The public carrier
that must present the same verified input on plan, apply, and interrupted
resume is:

```text
scripts/install-runtime-pack.sh --pack SAME.tar.gz --rescue --plan
scripts/install-runtime-pack.sh --pack SAME.tar.gz --rescue --apply --plan-digest <sha256>
```

Direct payload-root remains valid when the caller already has a stable
verified tree (the prior owner proofs). The public wrapper cannot use a
one-shot `mktemp` extract: `payload_root` is part of `input_digest` /
`plan_digest` and of the interrupted journal binding, and EXIT cleanup
deletes that path. Installed-state trees (`.installer-backups`,
`tools/.incoming-*`) are not extract owners — `--plan` must not mutate
selectors, receipt, or product config.

Rescue therefore extracts into a private content-addressed slot under the
existing installer cache namespace:

`${XDG_CACHE_HOME:-$HOME/.cache}/vibecrafted/runtime-pack-rescue/<archive-sha256>/`

The directory name is the verified digest of the original signed bytes.
The leaf is `0700`, euid-owned, not a symlink, and locked with an adjacent
`mkdir` lock for the invocation. Reuse re-checks identity + a sibling file
manifest and refuses a stale, tampered, foreign, or concurrent tree.
`--plan` retains the slot; a failed or interrupted `--apply` retains it
for resume; a successful `--apply` removes it. This is one slot per unique
signed archive, not an unbounded cache and not a shared `/tmp` name.
A 79001 pack whose embedded installer lacks `--rescue` still bootstraps
the source installer against that same verified extract. Do not rewrite
the signed payload.

```text
python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack> --rescue --plan
python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack> --rescue --apply --plan-digest <sha256>
```

`--plan` is read-only file evidence. It does not execute user startup
files. It inventories receipt digest, generation, ownership
hashes/targets (including nonregular current type, symlink target, and
directory listing), observed target payload bytes plus canonical
`runtime-pack-provenance.json` admission (`payload.files`
path/sha256/size/mode; hashing whatever exists is not verification),
host-shell `--fix-rc` stanza hashes, and backup classes (`present`,
`missing_historical`, `live_damage`, `unsafe`). Historical rollback is
reported unavailable when preimages are gone. Original receipt bytes are
preserved as evidence. Plan digest does not include a startup returncode.

`--apply` binds to that plan digest, revalidates the live payload inventory
under the existing install lease, snapshots every path publication can
mutate as `damaged-pre-rescue` (never a healthy restorepoint; evidence is
hashed before restore), archives the original receipt, drops only
missing-historical backup map entries, and reuses the existing publication
transaction. The journal stores the original target/inventory/payload/source
binding and the current attempt's publication phase. A leftover completed
journal is historical evidence, not this attempt's rollback source.
Rollback is allowed only after this attempt owns a captured publication
(or a validated in-flight resume). Pre-publication planning or snapshot
failures preserve current files, receipt, and historical evidence. Each
apply attempt allocates a unique evidence directory; a same-second retry
must not reuse or overwrite the prior attempt's snapshot. Interrupted
resume revalidates that input identity and journal phase before every
mutation and refuses a changed pack, mode, path, or completed journal.
Live installed partial-state from this rescue is not treated as input
drift. A path publication will touch that cannot be captured refuses
before mutation. Missing, unreadable, malformed, or mismatched
snapshot/receipt evidence is a residual; destinations are not deleted or
restored from it, and a failed rollback is never reported as complete.
Preference conflicts keep pending journal state recoverable. Success
requires the selected generation, receipt, and active identity to match
the requested target version and content identity. Version plus
source-provenance identity is not exact requested content. Destination
comparison reuses the canonical pack inventory owner
(`runtime-pack-provenance.json` / `_payload_files` path/sha256/size/mode)
for requested files and ignores installer-generated generation surfaces
(`runtime-manifest.json`, host-adapted `runtime/generated`, rewritten
product wrappers). The live generation tree is not hashed as a whole.
A healthy older generation is not `rescued` and still publishes a new
immutable `releases/<version>`. A published immutable generation whose
inventory is not the requested content is not `rescued` and is not
overwritten: apply refuses and preserves the current healthy generation.
Repeat apply of the exact same target remains
a no-op once that identity verifies. Destination verification covers
selectors, active identity, every receipted file/symlink/dir, every
expected launcher/skill/config projection, static user-rc inspection, and
a product-owned interactive `zsh -i` surface written into a temporary
ZDOTDIR. Substituting HOME/ZDOTDIR is not process or filesystem isolation
and does not execute copied user startup files. Actual user-shell
acceptance is separate evidence. A healthy receipt is not a healthy
shell. The exact current rescue's `rescue_pending` marker,
matched to the validated journal/binding/plan identity, is the owned
verification phase after publication closes `install_pending`; it is not
treated as a competing publication. Unrelated or mismatched pending
markers, and install/config/uninstall transitions, still refuse.
Verification failure keeps `rescue_pending` recoverable and does not
write a healthy restorepoint. Only a successful destination and
product-owned shell check pops the marker and finalizes the rescue
record. Unsafe or unknown-ownership paths refuse. Same-type
receipted files with a different hash, and symlinks with a foreign live
target, are not republished from receipt path alone: preference drift
uses the existing preserving merge; foreign command/skill replacements
are preserved or refuse. User config,
foreign commands, and unplanned rc content stay.
A target pack whose embedded installer lacks `--rescue` is not rewritten;
bootstrap with this source installer against the verified payload-root.
Compatibility: `tests/tui/test_runtime_pack_rescue.py` (owner) and
`tests/tui/test_runtime_pack_rescue_wrapper.py` (public carrier).
