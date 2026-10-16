---
title: "Update and rollback"
description: "Source runtime and App update owners, receipt-bound recovery, and clean uninstall."
section: getting-started
order: 40
---

# Update and rollback

Installers publish immutable runtime generations. The source-runtime lane uses
the `tools/vibecrafted-current` selector described below; the macOS product
updates its signed App and matching Runtime Pack together. Each lane must use
its installer and recovery receipts.

## Update

```bash
vibecrafted update
```

The CLI updates the source runtime through its installer. Its `Launcher`
version identifies the command deck executing this request; it is not proof of
which App or service process is running. A matching channel version skips the
installer unless `--force` is supplied, and does not attest installation health.

After the installer returns successfully, the CLI reports `Installer completed;
installed runtime acceptance pending`. Use `vibecrafted doctor` and
`vibecrafted receipt` to inspect the selected installed generation. The command
deck performs no cleanup inside that immutable generation: publication and
receipt-owned cleanup belong to the installer.

From a local checkout, `make update` fast-forwards only when the checkout is on
the requested branch. A failed fetch or fast-forward stops before installation.
On another branch it installs the current checkout without switching branches.

For the macOS product, use **Check for Updates** in Vibecrafted.app. That path
verifies the signed release feed and candidate, publishes the Runtime Pack
through its installer, and replaces the App through the sole bundle mutation
helper. CLI source-runtime Update does not replace the App. See
[the App update contract](../../installer/IN_APP_UPDATE.md) for receipts and
recovery requirements.

Install and update also reconcile **skill-copy shadows**: real directory copies of bundled skills that a pre-3.x installer left in per-runtime skill dirs such as `~/.junie/skills`. A copy whose Vibecrafted provenance is proven is moved into `~/.vibecrafted/backups/installer/shadowed-views-<timestamp>/` and then removed, so the canonical `~/.agents/skills` view is the only truth. Provenance is proven from content only: either the copy is byte-identical to the store copy, or the release manifest (`SKILL_PROVENANCE.json`, shipped inside the skill store) proves both halves of it — its `SKILL.md` is a release Vibecrafted shipped, and every file it carries is, byte for byte, a version we shipped at that same path. A file you edited, a file we never shipped, a symlink, or a hand-edited `SKILL.md` all withdraw the claim, and the warning names the file. Every runtime is reconciled, `~/.claude/skills` and `~/.codex/skills` included: install links the canonical `~/.agents/skills` view first — reconciliation needs it in place before it may remove anything — then reconciles, then links the remaining runtime views. A `vc-*` directory whose provenance cannot be proven is only reported — never removed, and neither is anything reached through a symlinked skill directory. The view writer itself no longer removes anything real to make room for a link — a directory or a plain file under a `vc-*` name is kept, and named in the output. The same proof guards **orphans**, the `vc-*` directories whose names have left the bundle: one that cannot be proven is kept and reported instead of removed, even in a non-interactive install. `vibecrafted doctor` names these cases; see [Doctor](../troubleshooting/doctor.md).

## Clean reinstall with resurrection

```bash
vibecrafted reinstall --clean --dry-run   # snapshot + plan, changes nothing
vibecrafted reinstall --clean             # stop, install, bring everything back
```

An in-place update leaves running Frame sessions and agents on the generation
they started from. `reinstall --clean` replaces the runtime from a clean slate
and rebuilds your workspace from the new generation:

1. **Snapshot** — every live Frame session, its current layout and panes, and
   for each agent pane the provider, working directory and native session id.
   An id counts only when a named recipe proves it (an explicit
   `--session-id`/`resume <id>` the provider acknowledged, the run ledger, or
   a provider transcript born with that process); otherwise it is recorded as
   unknown.
2. **Kill clean** — the persistent service is stopped (not uninstalled), then
   agents started by the runtime, then every runtime-owned process, each one
   re-verified by birth time and argv before it is signaled. Other products'
   processes are left alone, even when they borrow the runtime's interpreter.
3. **Install** — `make install` from your checkout, or
   `--pack <Runtime-Pack.tar.gz>` for an explicit pack.
4. **Resurrect** — run by the newly installed launcher: sessions are recreated
   from their layouts, chrome and shells start from the new generation, and
   each agent comes back through the interactive spawn surface — with a native
   resume when its session id is proven, as a fresh session with a continuity
   pack when it is not. A pane is reported as live only after its provider
   process identity and ancestry match the admitted run and Frame server.
   Failed starts produce a partial receipt with diagnostics. Headless runs
   spared by the kill phase are recorded as `left-running`; other proven
   headless sessions continue under their original run id.
   The service and the App start again when they were
   running before.

The executor detaches from the terminal that started it, so it can stop the
Frame session you typed the command in. Every phase writes a receipt under
`~/.vibecrafted/artifacts/vetcoders/vibecrafted/<day>/reports/reinstall-clean/`;
`vibecrafted reinstall --resurrect <run-dir>` replays phase 4 from them.
The phase-4 receipt includes `front_door`. The detached executor cannot attach
a terminal client, so `executor.log` prints the exact `vc-frame attach` command
to enter the restored workspace from a terminal. Opening the App alone does
not prove that workspace attachment succeeded.
Frame's serialized resurrection layouts receive the same generation rewrite
as the snapshot layouts; their originals are saved under `frame-cache-backups/`
in the run directory. Relative `--pack` paths are resolved before detachment.
Scrollback and programs running inside panes (an open editor) are not
replayed: such panes come back suspended, one keypress from running again.

## Runtime generations

The public launcher (`~/.local/bin/vibecrafted` and its `vc-*` aliases) enters only the command deck under:

```text
~/.local/share/vibecrafted/tools/vibecrafted-current/
```

`vibecrafted-current` is an atomic symlink to one immutable `vibecrafted-generation-*` directory. The installer refuses to point the public launcher at a uv tool shim or a repository checkout.

Every published generation carries `runtime-manifest.json` (schema
`vibecrafted.runtime-generation.v2`) which binds:

- the installed version;
- the canonical command-deck entrypoint;
- a one-way fingerprint of the source root — never the checkout path itself;
- the canonical source-payload tree identity transported by the v2 source
  carrier;
- SHA-256 digests for `VERSION`, the launcher and command deck, generated vc-frame
  configuration, and the release verifier engine, runner, schema, policy, and key.

Older four-hash generation manifests fail closed and require reinstall; they are
not silently treated as current.

The manifest and runtime files are created and audited **before** the single pointer swap. A failed audit leaves the previous generation live and rollbackable — a broken update cannot take down a working install.

The public release-verifier launcher validates this manifest and every bound
file before loading its runner or verifier engine. Post-install drift cannot
execute first and report failure afterward.

Downloaded and local bootstrap archives must also carry the closed v2
`source-provenance.json` carrier. The canonical archive writer proves the
included tree against one owner repository and full commit SHA before it writes
that carrier; archives built from dirty included source are rejected. Bootstrap
then recomputes the carrier's tree digest before extraction, after extraction,
and after candidate staging without trusting archive-supplied Python.

The carrier detects unchanged-carrier payload substitution, but it is not a
signature: coordinated payload-and-carrier rewriting remains W4 release
authentication work. Create a local archive through
`scripts/distribution_manifest.py archive`, not with a raw `tar` command.

Inspect what you are running:

```bash
readlink ~/.local/share/vibecrafted/tools/vibecrafted-current
cat ~/.local/share/vibecrafted/tools/vibecrafted-current/VERSION
python3 -m json.tool ~/.local/share/vibecrafted/tools/vibecrafted-current/runtime-manifest.json
```

## Recovery through the installer

A failed candidate audit before publication preserves the previous generation.
Do not repoint `vibecrafted-current` with `ln -sfn`: that bypasses the publisher's
pre-swap validation and service handoff, and does not restore a matching App.
Running `doctor` afterward cannot make an unvalidated pointer swap atomic.

For macOS product recovery, retain the exact Update transaction's admission,
journal, receipt and prior App/Runtime Pack. The existing replacement helper
owns restore/recover as well as replacement; use the
[App update recovery contract](../../installer/IN_APP_UPDATE.md) and
[capture lifecycle contract](../../installer/APP_CAPTURE_LIFECYCLE.md). Missing
or mismatched evidence is a refusal, not a successful rollback.

After uninstall, use the self-contained restore command printed by that
installer's receipt, described below. Source reinstall, App replacement and
teardown restore are different actions; choose the owner for the action rather
than editing a selector by hand.

## Compare source and installed

```bash
vibecrafted receipt
vibecrafted receipt --json
```

The delivery/runtime receipt (schema `vibecrafted.delivery_receipt.v1`) binds, for each fleet tool (`vc-frame`, `vibecrafted`, `scaffold-doctor`, `loct`, `aicx`): owner/repo → branch → checkout SHA → dirty state → installed SHA → ahead/behind. Drift verdicts:

| Drift                       | Meaning                                                            |
| --------------------------- | ------------------------------------------------------------------ |
| `CLEAN`                     | Installed matches a pushed, clean source state                     |
| `SOURCE_AHEAD_OF_INSTALLED` | Your checkout moved past what is installed — reinstall to catch up |
| `INSTALLED_NOT_ON_PATH`     | The installed binary is not what `PATH` resolves                   |
| `UNPUSHED`                  | Installed from commits that are not on the remote                  |
| `DIRTY_BUILD_PROVENANCE`    | Installed artifact was built from a dirty tree                     |
| `INDEX_STALE`               | Tool index generation is behind                                    |

The receipt never uses the process working directory to identify a tool's source. See [Doctor](/docs/doctor/) for the full provenance workflow.

## Uninstall

```bash
vibecrafted uninstall
```

Before consent, uninstall prints one inventory of every managed path it will remove or edit and every path it will intentionally preserve. It removes staged `vibecrafted-*` payloads, the `vibecrafted-current` link, launchers, managed skills and views, helpers, shell lines, the start guide, and the installer log. Unknown siblings in the runtime tools directory are retained.

A restore kit survives teardown, and the final receipt prints its exact self-contained command:

```bash
python3 ~/.vibecrafted/backups/installer/<timestamp>/restore.py
```

Operator artifacts, control-plane history, and logs under `~/.vibecrafted/` are retained intentionally and listed in the receipt.
