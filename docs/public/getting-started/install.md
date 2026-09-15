---
title: "Install"
description: "Install Vibecrafted on macOS, Linux, or native Windows (win32-x64 Runtime Pack), and verify the result with doctor."
section: getting-started
order: 20
---

# Install

Vibecrafted runs on macOS, Linux, and native Windows (win32-x64 Runtime Pack).
Pick the channel that matches your platform, then verify the result with
`vibecrafted doctor`.

## Channels

| Channel                      | Platform             | What you get                                             | Status                                   |
| ---------------------------- | -------------------- | -------------------------------------------------------- | ---------------------------------------- |
| Signed `Vibecrafted.app` DMG | macOS 14+, arm64     | Full desktop product: terminal, frame, runtime, server   | Build path complete; publication pending |
| Signed Runtime Pack          | macOS 14+, per-arch  | Same prebuilt runtime without DMG/App                    | Built and signed with the DMG            |
| Native Runtime Pack          | Windows win32-x64    | Command deck, pack Python, foundations, vc-server       | Built from checkout; publication pending |
| Bootstrap `install.sh`       | macOS, Linux, WSL2   | Command deck, runtime, control plane, skills             | Published; CI-gated                      |
| Source checkout              | macOS, Linux, WSL2   | Development tree and targets — not a native Runtime Pack | Published                                |
| Container                    | anywhere Docker runs | Isolated operator runtime                                | Published                                |

On macOS and Linux, use the bootstrap today. On Windows, install the win32-x64
Runtime Pack with `install.ps1 -Pack`.

## macOS and Linux

```bash
curl -fsSL https://vibecrafted.io/install.sh | bash
```

The installer detects your platform and Linux distribution family first, reports
every missing prerequisite in one pass instead of failing one tool at a time,
and stages a versioned runtime generation under
`~/.local/share/vibecrafted/tools/`.

To read the script before running it:

```bash
curl -fsSL https://vibecrafted.io/install.sh -o install.sh
less install.sh
bash install.sh
```

### Linux is a first-class runtime

The Linux install path is gated on every push and pull request. Two jobs run:
Ubuntu on a hosted runner, which exercises real `/etc/os-release` detection and
the apt-family prerequisite hints, and `debian:bookworm-slim` in a container,
which exercises the bare-minimum case with no pre-baked tooling. Both assert
that `vibecrafted doctor` reports green afterwards.

The macOS-only surfaces are the desktop app, notarization, and the `locterm`
runtime. The command deck, control plane, dispatch, skills, and settlement
ledger all run on Linux.

## Windows

The native Windows product is the win32-x64 Runtime Pack. It does not require
WSL. From a checkout:

```powershell
powershell -NoProfile -File .\scripts\build-windows-x64-runtime-pack.ps1
powershell -NoProfile -File .\install.ps1 -Pack .\build\Vibecrafted_RuntimePack_<version>-win32-x64.tar.gz
```

Runtime home is `%LOCALAPPDATA%\Vibecrafted`. Launchers are `*.cmd` under
`%LOCALAPPDATA%\Vibecrafted\bin`. `tools/vibecrafted-current` is a directory
junction. Rescue/flock recovery is POSIX-only and is not claimed here.

`install.ps1` delegates to `scripts/install-runtime-pack.ps1` when a pack is
present. Without a pack it prints the exact next command and exits non-zero.

WSL2 remains a POSIX alternative: install WSL2, then use `install.sh` inside
the distro. That is not the native Windows product.

## macOS desktop app

The intended shape of the end-user product is one Developer ID signed and
notarized artifact carrying matching builds of `vc-terminal`, `vc-frame`,
`vc-start` and the complete Vibecrafted runtime. No companion repository
installer is required.

When a release carries a DMG, open the
[latest release](https://github.com/vetcoders/vibecrafted/releases/latest),
download `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg` and its adjacent
`.dmg.sha256`, verify the bytes, then open it:

```bash
shasum -a 256 -c Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg.sha256
```

Drag `Vibecrafted.app` to Applications and launch it.

## macOS CLI Runtime Pack

The App is optional. The same release carries
`Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz`, its
`.sha256`, and detached `.sig`. It is built from the exact Runtime Pack inside
the signed App and adds only the bundled terminal and native `vc-frame` helper.

From a checkout:

```bash
make install RUNTIME_PACK=../Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz
make uninstall
```

Both commands use the pack-owned Python and `vetcoders_install.py`. Installation
writes one ownership receipt; uninstall refuses modified managed files, restores
pre-existing collisions, and prunes only directories that receipt proves the
installer created.

Check what a given release actually carries:

```bash
gh release view --json assets -q '.assets[].name'
```

The build path is exercised end to end and its shape is gated by contract
tests: `make release` produces a Developer ID signed, notarized and stapled
DMG with a signed `release-output.json`. Until the release carrying it is
published, use the bootstrap channel above.

## Portable source channel — Linux, WSL2, source fallback

Apple notarization cannot reach these systems, so the same release carries a
second canonically named artifact:
`Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz` plus its adjacent
`.sha256`.

```bash
curl -fsSLO https://github.com/vetcoders/vibecrafted/releases/latest/download/Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz
curl -fsSLO https://github.com/vetcoders/vibecrafted/releases/latest/download/Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz.sha256
sha256sum -c Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz.sha256
tar -xzf Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz
bash vibecrafted-<version>/install.sh
```

What the tarball is, and what it is not:

- It is an **allowlisted projection** of one exact commit, not a `git archive`
  of a working tree. Development artifacts are excluded by construction, and
  every required runtime path must be present or the packer refuses to write.
- It carries a closed `source-provenance.json`: schema
  `vibecrafted.source-provenance.v2`, a `vibecrafted.distribution-tree.v1`
  digest over every entry, bound to the commit the release was cut from.
  `install.sh` re-validates that carrier before it stages anything.
- It is **not** a prebuilt-binary bundle. Product launchers and native
  `vc-terminal` / `vc-frame` hosts stay on the Runtime Pack. `--skills-only`
  installs skill views from source; it does not claim a complete native
  runtime.
- Linux prebuilt packs (`linux-x64`, `linux-arm64`) are built natively by
  `scripts/build-linux-runtime-pack.sh` and are not produced by macOS
  `make release`. 4.3.1 does not ship a systemd unit; start the server
  and guardian with `vibecrafted server start`.
- On Windows, use the native win32-x64 Runtime Pack. The portable Linux
  tarball is the POSIX alternative inside WSL2.

The checksum proves the bytes survived the wire. The provenance carrier proves
they are the distribution they claim to be — that is the part `curl | bash`
from a branch cannot give you.

### Runtime boundary

Every new or restored workspace has a durable `workspace_id` and enters through
the bundled `vc-start`. The app sources an app-owned XDG/runtime environment and
does not overwrite your terminal, shell, Zellij or vc-frame configuration.
`vc-terminal` and `vc-frame` do not have independent app, DMG, installer or
update channels.

The server endpoint is read from Vibecrafted settings and may be any host:port,
for example `http://127.0.0.1:3024`. It is never baked into the app.

## Container

```bash
docker build -t vetcoders/vibecrafted:local .
docker run --rm -it -v "$PWD:/workspace" vetcoders/vibecrafted:local version
```

## Verify

```bash
vibecrafted doctor
vibecrafted version
```

`doctor` distinguishes what is broken from what is merely absent. On a plain
install, externally managed foundations you have not installed are reported as
warnings rather than failures. Anything reported red is genuinely wrong.

## Next

- [First run](/docs/first-run/) — what happens on your first command, and what
  to do when an agent CLI is missing.
- [Build from source](/docs/build-from-source/) — the checkout path, the target
  surface, and building your own artifact.
- [Quick start](/docs/quick-start/) — your first workflow.
- [Update and rollback](/docs/update/) — runtime generations and pointer
  rollback.
