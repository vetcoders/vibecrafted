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
it and does not write the installation itself. Regression coverage:
`tests/tui/test_installer_uninstall.py`, `tests/tui/test_installer_restore.py`.

## 1. The required set

Every row is load-bearing: delete it and the named flow stops working.

| Surface           | Required path                                                                                                           | Needed for                                                                                              |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Launcher shims    | `~/.local/bin/{vibecrafted, vc-*}` — one file per `PYTHON_ENTRYPOINT_LAUNCHERS` entry, plus the compat pack             | every CLI entrypoint; they are the uv-receipt entrypoints and resolve into the current tools generation |
| PATH wiring       | the `_launcher_path_line()` guard in `~/.zshrc` / `~/.bashrc`                                                           | the shims being on `PATH` in a fresh shell                                                              |
| Stable pointer    | `~/.local/share/vibecrafted/tools/vibecrafted-current` (symlink)                                                        | every shim: paths are resolved through the pointer, never through a version directory                   |
| Tools generation  | exactly one `tools/vibecrafted-generation-<version>-<pid>-<nonce>/` — the one `vibecrafted-current` points at           | the Python package tree behind the pointer                                                              |
| uv environments   | `<uv tool dir>/{vibecrafted, vibecrafted-mcp}`, `<uv tool dir>/vibecrafted-iterm2` where the iTerm2 plugin is installed | the interpreters the shims exec; owned by uv, not by us                                                 |
| Active release    | `~/.local/share/vibecrafted/active.json` + the one `releases/<version>/` it names                                       | app/runtime handoff — `active.json` carries `runtime_root` and `app_root`                               |
| Ownership receipt | `~/.local/share/vibecrafted/install-receipt.json`                                                                       | deterministic reset, collision restore, and locally-modified-file refusal                               |
| Runtime installer | `<release>/scripts/vetcoders_install.py` plus its bundled import closure                                                | the same install/uninstall implementation for App and CLI                                               |
| Provider          | `~/.local/share/vibecrafted/providers/vc-slack-agent/current` (symlink) + the one generation it names                   | `vc-slack` and the Slack bridge                                                                         |
| Server assets     | `~/.local/share/vibecrafted/server/site/`                                                                               | the local dashboard/server surface                                                                      |
| Skills store      | `<tools/vibecrafted-current>/vibecrafted-core/vibecrafted_core/skills/`                                                 | the one canonical copy of every skill                                                                   |
| Skill projections | `~/.<runtime>/skills/<skill>` symlinks into the store, per installed runtime                                            | agents seeing the skills at all                                                                         |
| Install state     | `~/.vibecrafted/.vc-install.json` (legacy installs: the same file next to the store)                                    | update/uninstall knowing what this install registered                                                   |
| Required tools    | `loct`, `loctree-mcp`, `aicx`, `prview`, `screenscribe` plus the `vc-*` projections                                     | complete agent product; missing Loctree/AICX is fail-closed, missing PRView/ScreenScribe is warned      |
| Frame config      | `~/.config/vibecrafted/vc-frame/`, `~/.config/vetcoders/frontier/`                                                      | `vc-frame` / `vc-start` cockpit; no private top-level `~/.config/vc-frame`                              |
| App bundle        | `/Applications/Vibecrafted.app` when the DMG channel is used                                                            | optional native transport/onboarding shell; CLI runtime must remain first-class without it              |

Anything not in this table is disposable. In particular: **second and later
generations are never required.** One tools generation, one release, one provider
generation. Every other generation is retained history with no consumer.

## 2. Discovery patterns uninstall removes

| Surface                 | Discovery pattern                                                                                                                           | Why it is removable                                                                                                      |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Tools generations       | `tools/vibecrafted-*`, `tools/vibecrafted-current`                                                                                          | only the pointer target is required; the rest are old generations                                                        |
| Incoming payloads       | `tools/.incoming-*`                                                                                                                         | interrupted download staging                                                                                             |
| Atomic staging          | `tools/..vibecrafted-*`                                                                                                                     | half-published generations left by an interrupted publish                                                                |
| Handoff receipt         | `tools/.vibecrafted-current-handoff.json`                                                                                                   | per-publish marker                                                                                                       |
| Install lease           | `tools/.vibecrafted-install.lock`                                                                                                           | transient cross-process lock; **the teardown itself creates it**, so it is registered in the inventory up front (see §4) |
| Finder metadata         | `tools/.DS_Store`, `<runtime home>/.DS_Store`                                                                                               | inert metadata inside directories we own end to end; left behind it keeps the parent unprunable                          |
| Releases                | `<runtime home>/releases/`                                                                                                                  | rebuilt from the payload on the next install                                                                             |
| Providers               | `<runtime home>/providers/`                                                                                                                 | rebuilt from the payload on the next install                                                                             |
| Server assets           | `<runtime home>/server/`                                                                                                                    | shipped inside the payload                                                                                               |
| Active pointer          | `<runtime home>/active.json`                                                                                                                | meaningless once the release it names is gone                                                                            |
| Runtime receipt         | `<runtime home>/install-receipt.json`, after its plan has been applied                                                                      | per-install ownership evidence, not durable operator data                                                                |
| Framework config        | children of `~/.config/vibecrafted/` except `*.env`                                                                                         | generated: themes, shell fragments, plists                                                                               |
| Frame config trees      | `~/.config/vibecrafted/vc-frame/`, legacy `~/.config/vc-frame/`, `~/.config/vetcoders/frontier/`                                            | generated config/symlink farms plus their own `.bak*` / `.stale*` snapshots                                              |
| Server LaunchAgent      | `~/Library/LaunchAgents/io.vetcoders.vibecrafted.server.plist`                                                                              | product-owned supervisor definition; booted out before removal                                                           |
| Launchd job (macOS)     | `~/Library/LaunchAgents/com.vetcoders.vibecrafted-slack-bridge.plist`                                                                       | provider service definition; a loaded job ends at logout or explicit bootout                                             |
| iTerm2 profiles (macOS) | `~/Library/Application Support/iTerm2/DynamicProfiles/vibecrafted*.json`                                                                    | written by the iTerm2 plugin                                                                                             |
| App support (macOS)     | `~/Library/Application Support/{io.vetcoders.vc-frame, com.vibecrafted.vc-board, com.vibecrafted.vc-term}`                                  | framework runtime state                                                                                                  |
| Caches (macOS)          | `~/Library/Caches/io.vetcoders.vc-frame`                                                                                                    | cache                                                                                                                    |
| Preferences (macOS)     | `~/Library/Preferences/{io.vetcoders.vibecrafted, com.vibecrafted.vc-board, com.vibecrafted.vc-board.debug, com.vibecrafted.vc-term}.plist` | framework preference domains                                                                                             |
| Shell rc lines          | the marked Vibecrafted block in `~/.zshrc` / `~/.bashrc`                                                                                    | edited in place, never truncated                                                                                         |

Empty parents (`tools/`, the runtime home, `~/.config/vibecrafted/`) are removed
**only if empty** after their children are gone. A single preserved stranger keeps
the directory, and the inventory says so in its reason line.

## 3. Preserved, and why

| Surface                                                            | Action   | Reason                                                                                 |
| ------------------------------------------------------------------ | -------- | -------------------------------------------------------------------------------------- |
| `~/.config/vibecrafted/*.env`                                      | preserve | operator secrets (Slack tokens and friends); never removed, never copied into a backup |
| pre-existing `~/.vibecrafted/{artifacts, control_plane, logs}`     | preserve | operator data outside a receipted clean-profile install                                |
| `<runtime home>/bin/*`                                             | preserve | binary ownership is product-managed outside installer state                            |
| Unrecognized `tools/` siblings                                     | preserve | not a Vibecrafted-managed payload name                                                 |
| Unrecognized runtime-home children                                 | preserve | discovery has no evidence they are ours                                                |
| `<uv tool dir>/{vibecrafted, vibecrafted-mcp, vibecrafted-iterm2}` | preserve | uv owns them; the printed plan tells the operator to run `uv tool uninstall`           |
| `/Applications/Vibecrafted.app`                                    | preserve | installed from the DMG; removed by dragging to Trash                                   |

Rule: an unrecognized name is preserved. Discovery widens ownership by adding
known names, never by claiming whatever it finds.

For a clean profile where the Runtime Pack receipt says the installer created
`~/.vibecrafted`, reset removes that whole root, including runtime state created
after installation. If the root pre-existed, only proven owned children are
removed; unrelated operator state remains.

## 4. Invariants

**Receipt installs refuse drift before remove.** Runtime Pack uninstall hashes
every owned regular file before teardown. A locally modified launcher/config is
a conflict and stops the operation before the service or generation is removed.
Pre-install collisions are copied under `<runtime home>/.installer-backups/`
and restored during a successful reset.

**Legacy backup before remove.** Every discovery `remove` record passes through
`create_teardown_backup`, which snapshots each present path into
`~/.vibecrafted/backups/installer/<timestamp>/` with a `restore-manifest.json`
and a self-contained `restore.py`. `vibecrafted restore` replays that manifest by
absolute path. New surfaces added to the inventory inherit this for free — there
is no removal path that bypasses the backup pass. Preserved paths (secrets,
operator data) are never copied into a backup, so a teardown kit never becomes a
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
- Fetch/install adapters for the required third-party payloads. The required set
  is now explicit, but bundling/installing Loctree, AICX, PRView and ScreenScribe
  is the next payload cut after deterministic uninstall; a DMG lacking them is
  still incomplete and must not be described as the full product.
- `$TMPDIR` test scratch. Owned by the test suite, not by the installer.

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
