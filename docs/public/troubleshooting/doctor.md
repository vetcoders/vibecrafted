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

| Audit               | What it proves                                                                                                                                                      |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Generation manifest | `runtime-manifest.json` (schema `vibecrafted.runtime-generation.v2`) exists, carries the v2 source-payload identity, and is valid for the current generation        |
| Content hashes      | SHA-256 digests for `VERSION`, launcher/deck, generated vc-frame config, and verifier engine/runner/schema/policy/key still match — any drift fails                 |
| Launcher binding    | The public launcher resolves to the exact current generation entrypoint inside `~/.local/share/vibecrafted` — a launcher resolving outside the installed root fails |
| Checkout-link scan  | No active config, KDL, helper, or command-deck content references a source checkout                                                                                 |
| Symlink census      | No installed symlink is broken or resolves outside its generation                                                                                                   |
| Foundations         | Product-managed foundation binaries (loct, aicx, prview, screenscribe) are present and are never silently replaced with stale copies                                |

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
