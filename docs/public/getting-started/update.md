---
title: "Update and rollback"
description: "How vibecrafted update publishes atomic runtime generations, how to roll back by pointer, and how to uninstall cleanly."
section: getting-started
order: 40
---

# Update and rollback

The installed runtime is a chain of immutable generations with one atomic pointer. Updating publishes a new generation; rolling back moves the pointer. Nothing is edited in place.

## Update

```bash
vibecrafted update
```

Update checks the installed version against the latest release and reinstalls when a newer one exists:

```text
⚒  Vibecrafted Update
  Installed: 3.6.0
  Available: 3.7.0
```

When you are already current, update says so and does nothing else; pass `--force` to reinstall the same version — which is also how you run the reconciliations below on a host that is already on the latest version. From a local checkout, `make update` pulls latest and reinstalls.

Install and update also reconcile **skill-copy shadows**: real directory copies of bundled skills that a pre-3.x installer left in per-runtime skill dirs such as `~/.junie/skills`. A copy whose Vibecrafted provenance is proven is moved into `~/.vibecrafted/backups/installer/shadowed-views-<timestamp>/` and then removed, so the canonical `~/.agents/skills` view is the only truth. Provenance is proven from content only: either the copy is byte-identical to the store copy, or the release manifest (`SKILL_PROVENANCE.json`, shipped inside the skill store) proves both halves of it — its `SKILL.md` is a release Vibecrafted shipped, and every file it carries is, byte for byte, a version we shipped at that same path. A file you edited, a file we never shipped, a symlink, or a hand-edited `SKILL.md` all withdraw the claim, and the warning names the file. Every runtime is reconciled, `~/.claude/skills` and `~/.codex/skills` included: install links the canonical `~/.agents/skills` view first — reconciliation needs it in place before it may remove anything — then reconciles, then links the remaining runtime views. A `vc-*` directory whose provenance cannot be proven is only reported — never removed, and neither is anything reached through a symlinked skill directory. The view writer itself no longer removes anything real to make room for a link — a directory or a plain file under a `vc-*` name is kept, and named in the output. The same proof guards **orphans**, the `vc-*` directories whose names have left the bundle: one that cannot be proven is kept and reported instead of removed, even in a non-interactive install. `vibecrafted doctor` names these cases; see [Doctor](../troubleshooting/doctor.md).

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

## Rollback by pointer

Because generations are immutable, rollback is a pointer move to a previous generation directory, followed by the health gate:

```bash
ls -d ~/.local/share/vibecrafted/tools/vibecrafted-generation-*
ln -sfn ~/.local/share/vibecrafted/tools/<previous-generation> \
        ~/.local/share/vibecrafted/tools/vibecrafted-current
vibecrafted doctor
```

`doctor` re-audits the manifest and hashes of whatever generation the pointer names, so a bad rollback target is caught immediately.

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
