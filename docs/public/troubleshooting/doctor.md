---
title: "Doctor"
description: "vibecrafted doctor is the canonical health gate: what it audits, how to read the output, and how receipt adds provenance."
section: troubleshooting
order: 10
---

# Doctor

`vibecrafted doctor` is the canonical health gate for an install. It audits the installed runtime — not your checkout — and answers one question with pass/fail discipline: is the thing on your `PATH` exactly the thing that was published?

```bash
vibecrafted doctor
vibecrafted doctor --verbose      # list every check, including passing ones
vibecrafted doctor --release      # VERSION vs GitHub Latest + last source gate
```

## What doctor audits

| Audit               | What it proves                                                                                                                                                                                           |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Generation manifest | `runtime-manifest.json` (schema `vibecrafted.runtime-generation.v2`) exists, carries the v2 source-payload identity, and is valid for the current generation                                             |
| Content hashes      | SHA-256 digests for `VERSION`, launcher/deck, generated vc-frame config, and verifier engine/runner/schema/policy/key still match — any drift fails                                                      |
| Launcher binding    | The public launcher resolves to the exact current generation entrypoint inside `~/.local/share/vibecrafted` — a launcher resolving outside the installed root fails                                      |
| Checkout-link scan  | No active config, KDL, helper, or command-deck content references a source checkout                                                                                                                      |
| Symlink census      | No installed symlink is broken or resolves outside its generation                                                                                                                                        |
| Foundations         | Product-managed foundation binaries (loct, aicx, prview, screenscribe) are present and are never silently replaced with stale copies                                                                     |
| Skill-copy shadows  | No per-runtime skill directory (`~/.junie/skills`, `~/.agy/skills`, `~/.grok/skills`, `~/.cursor/skills`) holds a real directory copy of a bundled skill shadowing the canonical `~/.agents/skills` view |

## Skill-copy shadows (`shadow-dir:<runtime>/<skill>`)

Installers before 3.x materialized **real directory copies** of `vc-*` skills into per-runtime skill dirs instead of views. Those copies survive next to the canonical `~/.agents/skills` symlink view, drift silently, and an agent that reads both directories (Junie does) sees a stale duplicate. Neither install, update nor doctor used to notice: shadow pruning covered only `claude`/`codex` and only symlinks, and orphan pruning covered only names that had left the bundle.

The same proof governs **orphans** — a `vc-*` directory whose name is no longer in the bundle. A retired skill like `vc-canvas` is also a name you could put your own work under, and orphan pruning used to remove the directory either way (with the prompt defaulting to yes, and returning that default in a non-interactive install). A real orphan directory now has to prove the same provenance before it is quarantined and removed; one that cannot is kept, reported with the `mv` command, and never offered at the prompt. Pointers and stray files are still removed as the leftovers of views we wrote.

Doctor now reports one `shadow-dir:<runtime>/<skill>` warning per copy, with the exact path and its provenance class:

| Class               | What is proven                                                                                                                                                                                                                        | What install/update does                                   |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `managed_identical` | The copy's file tree and content hashes match the store copy of that skill                                                                                                                                                            | Copied into a quarantine dir, then removed                 |
| `managed_stale`     | The copy's `SKILL.md` bytes are a release we shipped (sha256), and every file in it is byte for byte a version we shipped at that same path (git blob id), under a directory that really held a `SKILL.md`                            | Copied into a quarantine dir, then removed                 |
| `unknown`           | Anything less — an edited file, a path we never shipped, a symlink, or a `SKILL.md` we never released. A real **file** under a skill name is always this: a view is a link and a legacy copy is a directory, so a file there is yours | **Never touched** — reported with a manual `mv` suggestion |

Reconciliation happens during install and update, so the fix doctor names is `vibecrafted update --force`: a plain `vibecrafted update` prints `up to date` and returns without reinstalling once the installed version already matches the channel, and the reconciliation never runs.

Provenance is proven from content, never from the `vc-` name: your own skill parked under a `vc-*` name is reported and left alone. Two proofs are tried, in order:

1. **Identical tree** — the copy's file tree and content hashes match the store copy of that skill. Nothing can be lost: the store holds the same bytes, and the copy is quarantined before it is removed.
2. **Release history** — `SKILL_PROVENANCE.json`, shipped inside the skill store, records per skill the sha256 of every `SKILL.md` Vibecrafted has ever released, and for every relative path under a real skill directory the **git blob id of every version of that file we ever committed**. The copy is claimed only when both halves hold: its `SKILL.md` hashes into the released set, and every entry in it is a plain file whose bytes are one of the versions we shipped at that exact path. Editor litter (`__pycache__/`, `*.pyc`, `.DS_Store`) is skipped.

   A path is deliberately not evidence on its own. A name says nothing about content, so your own edited `scripts/await.sh` sitting at a path we do ship is `unknown`, and the warning names the file. A symlink inside the copy is `unknown` too: a materialized copy is files, and a link points somewhere nobody can vouch for.

   "Real skill directory" is just as deliberate. Only a directory that has ever held a `SKILL.md`, under a root where one has actually lived, counts. `runtime/vc-marbles/` never held one, so `orchestrator/commands/help.md` is not a `vc-marbles` file and does not widen what the installer may delete — while the identically-named path under `skills/vc-marbles/` is, because that root did.

   One shape needs naming: we have shipped a file as a _symlink_ (`skills/vc-agents/shell/vetcoders.zsh -> vetcoders.sh`), and an installer that copies a tree dereferences it, so the copy holds the target's bytes under the link's name. Those bytes are still ours, so the manifest records the target's blob ids under the link's path as well. Without that, the largest real copy on the affected host is unprovable for one file out of 54.

A missing, corrupt or older-schema manifest disables that second proof only; the identical-tree proof still applies, and everything else is reported as `unknown`. An older schema counts as missing: v1 recorded `SKILL.md` hashes alone and v2 added bare paths, and neither can say whether a file's bytes are ours.

Maintainers regenerate the manifest from the repository history after editing, adding or removing any skill file:

```bash
scripts/gen_skill_provenance.py            # merge history + working tree into the manifest
scripts/gen_skill_provenance.py --check     # gate: fails when the committed manifest is not what a regeneration renders
```

`--check` runs inside `make check`, so an unrecorded file fails Portable Checks rather than shipping a bundle that cannot prove its own skills. It compares the whole rendered document (`generated_at` aside), so an unsorted or duplicated entry fails too, not only a missing one.

The merge is additive and idempotent, so regenerating in a shallow clone never shrinks the proof set — which is also why a well-formed entry for a skill this clone cannot see is kept rather than flagged: that is how a retired skill's proof survives.

A runtime skill directory reached through a symlink or a Windows directory junction, or that resolves into the skill store, is skipped entirely (a junction reports `is_symlink() == False` while `rmtree` still walks through it): comparing the store with itself would classify every skill as an identical copy, and removing it would take the canonical store with it. Runtimes that carry a managed view (`agents`, `claude`, `codex`, plus anything the manifest recorded) are audited by the symlink checks instead of `shadow-dir:`, so nothing is double-reported — a real directory there is reported as `symlink:<runtime>/<skill>` "is a COPY, not a symlink". Reconciliation itself covers **every** runtime, those included, and runs before install writes the views. Quarantined copies land in `~/.vibecrafted/backups/installer/shadowed-views-<timestamp>/<runtime>/<skill>` and are never removed by the installer; the canonical `~/.agents/skills` view is never modified by this reconciliation.

Reconciliation refuses to remove a copy unless the skill stays readable afterwards: either `~/.agents/skills/<skill>` already points at the store, or the copy's own runtime is one this pass is about to link. So install links the canonical `agents` view first, reconciles shadows next, and links the remaining runtimes last. A first install therefore clears its own shadows in one pass; it used to leave them for a later run, which a plain `vibecrafted update` never performed. The second condition matters for an advanced or `--tool` selection that leaves `agents` out entirely — without it the precondition would be unmeetable and every proven copy would stay on the host for good.

The view writer never removes anything real — not a directory and not a file. It replaces a symlink or a junction with the correct link — removing the pointer itself, never the tree behind it — and leaves everything else where it is, with a `Keeping real directory …` or `Keeping real file …` line. By the time it runs, every copy whose provenance could be proven has already been quarantined, so whatever is still there is something nobody could vouch for, and overwriting it with a symlink would destroy an operator's own work with no backup. A plain file needs that protection most, because nothing else offers it any: shadow detection only ever examines directories, and it skips a runtime whose skills dir is reached through a symlink — so with `~/.grok/skills -> ~/notes`, a note named `vc-research` had nothing between it and the writer.

Launcher audits are scoped by **ownership, not naming**: doctor judges only the launchers Vibecrafted publishes itself (the installer's wrappers and Python entrypoints, the legacy packs, and the provider-published `vc-slack`). Another product that shares `~/.local/bin` and the `vc-` prefix — and legitimately links into its own checkout — keeps its own installation contract and is left alone.

This is the same audit that gates publication of a new generation: what fails a publish also fails doctor afterward.

## Release valve (`--release`)

Default doctor does **not** ask GitHub anything. That is why VERSION could sit at 4.1.0 while GitHub Latest and the last successful `Release source gate` stayed on v3.5.0, and every local gate still printed green.

`vibecrafted doctor --release` is the named probe:

- local `VERSION` (the checkout file, stamp stripped to `vX.Y.Z`)
- `gh release view --json tagName` (GitHub Latest)
- latest `gh run list --workflow "Release source gate" --limit 1` conclusion

Mismatch, a missing release, or a non-success source-gate conclusion is **red** and names the operator button: **tag/publish**. Missing `gh` is a loud **warn**, never a silent skip and never a fake green. The probe is off the public network in unit tests; it only talks to GitHub when you actually run `--release`.

## Reading the output

- **Pass (green / ok)** — the install is bound, hashed, and checkout-free. A healthy install reports on the order of 100+ ok with 0 failures.
- **Warn (yellow)** — something is weak but operable; the doctor names what to check next. Typical warns: an optional surface not installed, an environment nicety missing.
- **Fail (red)** — the runtime contract is broken: stale launcher, drifted manifest-bound file, checkout-linked config, or a broken symlink. Treat any fail as "do not trust this install until fixed".

### Running doctor from inside the source checkout

Doctor audits the installed runtime, but the Python process that runs it imports whatever is first on `sys.path`. With the working directory inside a Vibecrafted checkout — or with an editable `.pth` pointing at one — the process loads the unstamped living tree instead of the installed package. Doctor names that case explicitly: a `warn` on `launcher` saying the loaded tree is the living checkout, with the stamped identity the `PATH` launcher itself resolves. It is a working-directory artefact, not a broken install, and nothing should be uninstalled because of it.

```bash
cd ~ && vibecrafted doctor      # verify the installed launcher, free of the checkout
```

An unstamped tree loaded from **outside** any checkout is a different verdict: that is a genuine editable/Homebrew shadow winning `PATH`, and it still fails.

Doctor ships targeted repair flags for the most common launcher and shell-config failures:

```bash
vibecrafted doctor --fix-rc                 # repair old shell startup lines, restore helper/PATH hints
vibecrafted doctor --fix-launchers          # refresh vibecrafted, vc-help, and vc-* wrappers, then verify
vibecrafted doctor --fix-legacy-bootstrap   # neutralize retired bootstrap roots (comments out, never deletes)
vibecrafted doctor --fix-server-service     # reconcile the LaunchAgent with the current signed launcher, then verify
```

Each fix flag re-verifies after repairing, so a clean exit means the repair actually held.

## Receipt — provenance on top of health

Doctor proves the install is internally consistent. `vibecrafted receipt` proves where it **came from**:

```bash
vibecrafted receipt
vibecrafted receipt --json
```

The receipt (schema `vibecrafted.delivery_receipt.v1`) covers the fleet tools `vc-frame`, `vibecrafted`, `scaffold-doctor`, `loct`, and `aicx`. Each row binds owner/repo → branch → checkout SHA → dirty state → installed SHA → ahead/behind, and yields one drift verdict:

```text
CLEAN | SOURCE_AHEAD_OF_INSTALLED | INSTALLED_NOT_ON_PATH |
UNPUSHED | DIRTY_BUILD_PROVENANCE | INDEX_STALE
```

Policy: the receipt never uses the process working directory to identify a tool's source — resolution goes env override → binary path → verified candidate only. When auto-discovery cannot find a source checkout, set the `*_SOURCE` variables described in [Environment](/docs/environment/).

Use `--json` when you want to gate automation on provenance:

```bash
vibecrafted receipt --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d)'
```

## When to run it

- After every install, update, or rollback — non-negotiable.
- Before filing a bug: attach `vibecrafted doctor --verbose` output.
- Whenever behavior does not match the code you think is installed — that is almost always a drift verdict, not a mystery. Start with [Common issues](/docs/common-issues/).
