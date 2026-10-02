---
title: "Versioning"
description: "Release conventions: MAJOR.MINOR.PATCH versions, +g<sha> build provenance, changelog discipline, and how to verify what you run."
section: reference
order: 40
---

# Versioning

Vibecrafted versions the framework with a semver-shaped
`MAJOR.MINOR.PATCH` line and stamps every installed build with the exact
source commit it came from. This page covers the conventions and the
commands that let you verify — rather than assume — which version you are
actually running.

## Version scheme

Releases follow `MAJOR.MINOR.PATCH` (for example `3.6.0`, `3.7.0`). The
canonical version lives in the `VERSION` file at the repository root and
is stamped into the installed runtime generation at install time.

```bash
vibecrafted version      # also: vibecrafted --version, vibecrafted -v
```

## Build provenance: `+g<sha>`

Installed builds carry a build-provenance suffix in the form `+g<sha>`,
where `<sha>` is the short git commit the generation was built from:

```text
<MAJOR>.<MINOR>.<PATCH>+g<sha>
```

To confirm your install matches a given checkout state:

```bash
vibecrafted --version        # e.g. 3.7.0+g<sha>
git rev-parse --short HEAD   # should match the +g suffix after an install
```

**Push does not equal install.** Pulling new commits into a checkout never
updates the runtime you run daily — the CLI executes the staged tools home
(`~/.local/share/vibecrafted/tools/vibecrafted-current/`), not the
floating checkout. After runtime changes, re-run the installer and confirm
the `+g<sha>` moved to the intended commit.

## Installed generations

Each install publishes an immutable generation directory named with the
version and a unique token:

```text
~/.local/share/vibecrafted/tools/vibecrafted-generation-<version>-<token>/
```

and atomically repoints the `vibecrafted-current` symlink at it. The
generation's `runtime-manifest.json` (schema
`vibecrafted.runtime-generation.v2`) records the installed version, the verified
source-payload tree identity, and the SHA-256 digests that bind its critical
runtime files — so version truth is auditable, not declarative. See
[Runtime capsule](/docs/runtime-capsule/) for the full mechanism.

## The delivery receipt

For the surrounding fleet tools, the delivery receipt (schema
`vibecrafted.delivery_receipt.v1`) binds one chain of provenance per tool:

```text
owner/repo → branch → checkout SHA → dirty state
→ installed SHA on PATH → ahead/behind vs origin
```

Instead of prose, drift is reported as named classes:

| Drift class                 | Meaning                                             |
| --------------------------- | --------------------------------------------------- |
| `SOURCE_AHEAD_OF_INSTALLED` | The checkout has commits the installed binary lacks |
| `INSTALLED_NOT_ON_PATH`     | The binary on PATH is not the managed install       |
| `UNPUSHED`                  | Local commits are not on the remote                 |
| `DIRTY_BUILD_PROVENANCE`    | The build came from a dirty source tree             |
| `INDEX_STALE`               | A derived index lags the source                     |
| `CLEAN`                     | Source, install, and remote agree                   |

Any link that cannot be established is reported as `unknown` with an
explicit reason — the receipt refuses to guess.

## Changelog discipline

`CHANGELOG.md` follows the Keep a Changelog format, one entry per release,
newest first. House rules visible in recent entries:

- **Facts over hype.** Claims are stated with their evidence bar — the
  test suite, the gate, or the probe that backs them.
- **History is append-only.** Behavior changes are described as new
  entries; past entries are never edited to match the present.
- **Gates at release are recorded** — test counts, lint/security stacks,
  and the install smoke path — so a release claim can be re-checked later.

## Verifying a fresh install

```bash
open https://github.com/vetcoders/vibecrafted/releases/latest
# Download and verify Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg, then open it.
vibecrafted doctor      # audits the installed generation
vibecrafted --version   # version + build provenance
```

A matching `+g<sha>` identifies the launcher build. `doctor` and the delivery
receipt establish only the install/runtime surfaces they actually verify.
A signed release, an installed generation and a running App or service process
are separate observations: verify the installed App identity, signature and
launch before claiming that the desktop product runs that release.
