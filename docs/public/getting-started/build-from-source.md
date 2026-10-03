---
title: "Build from source"
description: "Clone the repository, install from the checkout, run the gates, and build your own signed desktop artifact."
section: getting-started
order: 25
---

# Build from source

A source checkout installs the same runtime the bootstrap installs, and adds
every build, test and release target on top of it. Take this path when you want
to modify Vibecrafted, run its gates, or produce your own signed artifact.

## Prerequisites

| Tool                     | Why it is needed                                               |
| ------------------------ | -------------------------------------------------------------- |
| `git`                    | checkout                                                       |
| `bash` 4 or newer        | installer and command deck                                     |
| `uv`                     | Python toolchain and the pinned `vibecrafted` tool environment |
| Rust toolchain           | `voc`, `vc-admin`, `vc-server`, `vc-terminal`, `vc-frame`      |
| `make`                   | target surface                                                 |
| Xcode command line tools | macOS only, for `codesign` and `notarytool`                    |

The installer reports every missing prerequisite in one pass, so you do not have
to discover them one failure at a time.

## Clone and install

```bash
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
make install
```

| Target              | Behavior                                                                                  |
| ------------------- | ----------------------------------------------------------------------------------------- |
| `make install`      | guided install                                                                            |
| `make install-auto` | non-interactive install; this is what CI runs                                             |
| `make install-all`  | adds the Rust binaries (`voc`, `vc-admin`, `vc-server`) as real files into `~/.local/bin` |
| `make wizard`       | browser-guided install surface                                                            |
| `make dry-run`      | show what an install would do without doing it                                            |

## The target surface

```bash
make help        # everyday targets
make help-dev    # the full inventory
```

| Group   | Targets                                                                                                                                                                                                                                                                                                 |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| install | `install` · `install-auto` · `install-all` · `install-python-tools` · `install-vendored-binaries` · `install-app-binaries` · `install-server` · `install-server-service` · `skills` · `helpers` · `setup-dev` · `wizard` · `gui-install` · `dry-run` · `restore` · `migrate` · `foundations` · `bundle` |
| tests   | `test` · `test-core` · `test-skills` · `test-install` · `test-parity` · `test-vc-frame` · `test-memex` · `test-aicx-sync` · `dispatch-test` · `test-race-protection` · `check` · `semgrep`                                                                                                              |
| server  | `server` · `server-build` · `server-check` · `server-test` · `server-smoke`                                                                                                                                                                                                                             |
| release | `app` · `dmg` · `dmg-signed` · `release-local` · `notarize` · `release` · `publish-release`                                                                                                                                                                                                             |
| version | `version` · `version-show` · `version-bump` · `bump-patch` · `bump-minor` · `bump-major`                                                                                                                                                                                                                |
| hooks   | `init-hooks` · `seed-commit-msg-hooks` · `commit-safe`                                                                                                                                                                                                                                                  |

## Run the gates

```bash
make test        # the full suite
make check       # shell lint
make semgrep     # security gate
```

The repository holds two test trees whose `conftest.py` files collide if you run
them in a single pytest invocation. Run them separately:

```bash
uv run --project vibecrafted-core pytest vibecrafted-core/tests
uv run --project vibecrafted-core pytest tests/tui
```

## Build the desktop artifact

Producing a distributable DMG requires Apple Developer ID signing material. The
release script reads it from `$KEYS`, which defaults to `~/.keys`:

| File                      | Purpose                       |
| ------------------------- | ----------------------------- |
| `signing-identity.txt`    | Developer ID identity         |
| `Certificates.p12`        | signing certificate           |
| `cert_password.txt`       | certificate password          |
| `vibecrafted-signing.key` | detached artifact signing key |
| Keychain profile/API key  | notarytool credentials        |

```bash
make app             # build Vibecrafted.app only
make dmg             # build a DMG without notarizing
make release         # build, sign and notarize the canonical versioned DMG
make publish-release # cold-verify the built DMG and publish it
```

The artifact is named `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg`, where the
version comes from `VERSION`, the date from `VIBECRAFTED_RELEASE_DATE` (defaults
to today, UTC), and the short SHA from the checkout's `HEAD`.

The release build pins `MACOSX_DEPLOYMENT_TARGET=14.0` and remaps Rust path
prefixes, so release payloads never carry the building machine's home directory,
Cargo registry paths, or checkout location in panic and debug metadata.

Without signing material, `make app` and `make dmg` still work for local
testing. `make release` does not.

## Environment overrides

| Variable                    | Effect                                |
| --------------------------- | ------------------------------------- |
| `KEYS`                      | directory holding signing material    |
| `VIBECRAFTED_RELEASE_DATE`  | `YYYYMMDD` stamp in the artifact name |
| `VIBECRAFTED_RELEASE_DIR`   | output directory, defaults to `dist/` |
| `VIBECRAFTED_TERMINAL_REPO` | path to the `vc-terminal` checkout    |
| `VIBECRAFTED_FRAME_REPO`    | path to the `vc-frame` checkout       |

## Next

- [First run](/docs/first-run/) — the entry experience and what `doctor` tells you.
- [Update and rollback](/docs/update/) — how runtime generations are published.
- [Contributing skills](/docs/contributing-skills/) — adding to the skill catalog.
