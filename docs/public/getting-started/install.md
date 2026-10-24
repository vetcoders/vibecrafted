---
title: "Install"
description: "Install Vibecrafted on macOS (one command, no Xcode), Linux, or native Windows (win32-x64 Runtime Pack), and verify the result with doctor."
section: getting-started
order: 20
---

# Install

Vibecrafted runs on macOS, Linux, and native Windows (win32-x64 Runtime Pack).
Pick the section for your platform, then verify the result with
`vibecrafted doctor`.

The canonical download page is
[vibecrafted.io/en/install](https://vibecrafted.io/en/install/)
([PL](https://vibecrafted.io/pl/install/)). The release it serves is the one
named by `https://vibecrafted.io/channel/main.json`; that manifest, not this
page, is the authority for the current version.

## macOS (14+, Apple Silicon) — the ordinary path

You do not need Xcode, Command Line Tools, Swift, Make, a Python of your own, or
a source checkout. Open Terminal and run one command:

```bash
curl -fsSL https://vibecrafted.io/install.sh | bash
```

On a supported Mac this installs the signed `Vibecrafted.app` **and** the
matching runtime together, so you do not need to download the DMG first. The
installer asks for consent before it changes anything and keeps your existing
shell, Terminal and Frame configuration and any running sessions.

When it finishes, open a new terminal and check the result:

```bash
vibecrafted --version
vibecrafted doctor
```

To read the script before running it:

```bash
curl -fsSL https://vibecrafted.io/install.sh -o install.sh
less install.sh
bash install.sh
```

### Prefer the app? Download the DMG

1. Open [vibecrafted.io/en/install](https://vibecrafted.io/en/install/) and use
   **Download macOS app**. The DMG is Developer ID signed and notarized.
2. Open the DMG and drag `Vibecrafted.app` to Applications, then launch it.
3. If the app offers to install or upgrade the Vibecrafted tools on first
   start, accept. Otherwise publish them from the menu bar: **Vibecrafted →
   Republish anyway…** and confirm.

Dragging the app into Applications does **not** by itself install the
`vibecrafted` command or the runtime. Until step 3 has run you can have a
current app next to an older (or absent) command line. `Republish anyway…`
installs the runtime generation carried inside the app you just opened; it does
not download a newer app and does not need a source checkout or Xcode.

### Updating on macOS

Re-running the one-command install pairs the app and the runtime again. The
in-app **Check for Updates** works only once a newer release is actually
published on the update feed; if none is, nothing is fetched. `Republish
anyway…` is not an update check — it reinstalls what the app already carries.

## Linux and WSL2

```bash
curl -fsSL https://vibecrafted.io/install.sh | bash
```

The bootstrap installs the release bound by the website channel manifest; it
does not follow a Git branch. Confirm what you received with
`vibecrafted --version`.

The installer detects your platform and Linux distribution family first, reports
every missing prerequisite in one pass instead of failing one tool at a time,
and stages a versioned runtime generation under
`~/.local/share/vibecrafted/tools/`.

The no-toolchain macOS path above is a darwin-arm64 carrier. It does not cover
Intel Macs or Linux, and the signed App exists only for macOS. Those platforms
keep their own requirements — a platform-matching Runtime Pack or the source
toolchain described in [Build from source](/docs/build-from-source/).

### Linux is a first-class runtime

The Linux install path is gated on pushes and pull requests to `main`,
`develop` and `release/*`. `.github/workflows/install-linux.yml` builds a Linux
x64 Runtime Pack on Ubuntu 22.04, then installs it with this tree's
`install.sh` on Ubuntu 22.04, Ubuntu 24.04 and `debian:bookworm-slim`, and runs
`vibecrafted doctor`. The 22.04 and Debian jobs explicitly waive foundations;
24.04 keeps the hard foundation gate. CI installs from the checkout, not from
the published website channel.

The macOS-only surfaces are the desktop app, notarization, and the `locterm`
runtime. The command deck, control plane, dispatch, skills, and settlement
ledger all run on Linux.

## Windows

The native Windows product is the win32-x64 Runtime Pack. It does not require
WSL. From a checkout:

```powershell
powershell -NoProfile -File .\scripts\build-windows-x64-runtime-pack.ps1
powershell -NoProfile -File .\install.ps1 -Pack .\build\Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-win32-x64.tar.gz
```

Canonical MSI/EXE names look like
`Vibecrafted_<version>-<YYYYMMDD>-<sha8>-windows-x64.msi` (unsigned
Authenticode — SmartScreen will warn; trust is `.sha256` + `.sig`).

Runtime home is `%LOCALAPPDATA%\Vibecrafted`. Launchers are `*.cmd` under
`%LOCALAPPDATA%\Vibecrafted\bin`. `tools/vibecrafted-current` is a directory
junction. PTY/zsh, flock/rescue, and voc are not supported on native Windows
(`vibecrafted doctor` declares them).

`install.ps1` delegates to `scripts/install-runtime-pack.ps1` when a pack is
present. Without a pack it prints the exact next command and exits non-zero.
First-run: `vibecrafted doctor` then `vibecrafted init`.

WSL2 remains a POSIX alternative: install WSL2, then use `install.sh` inside
the distro. That is not the native Windows product.

## Advanced: App and Runtime Pack by hand

Ordinary users do not need this section. The one-command install and the DMG
above already carry everything.

The signed App is one Developer ID signed and notarized artifact carrying
matching builds of `vc-terminal`, `vc-frame`, `vc-start` and the complete
Vibecrafted runtime; no companion repository installer is required. The App
and its Runtime Pack are paired — a fixed host needs both from the same
release, not one without the other.

To verify a downloaded DMG yourself, fetch the `.dmg` and its adjacent
`.dmg.sha256` from the carrier named by the website channel manifest (GitHub
Releases is a separate publisher and its `latest` is not that carrier), then:

```bash
shasum -a 256 -c Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg.sha256
```

The same release carries
`Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz`, its
`.sha256`, and detached `.sig`. It is built from the exact Runtime Pack inside
the signed App and adds only the bundled terminal and native `vc-frame` helper.
From a source checkout:

```bash
make install RUNTIME_PACK=../Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz
make uninstall
```

Both commands use the pack-owned Python and `vetcoders_install.py`. Installation
writes one ownership receipt; uninstall refuses modified managed files, restores
pre-existing collisions, and prunes only directories that receipt proves the
installer created.

## Portable source channel — Linux, WSL2, source fallback

Apple notarization cannot reach these systems, so the same release carries a
second canonically named artifact:
`Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz` plus its adjacent
`.sha256`.

```bash
curl -fsSLO https://github.com/vetcoders/vibecrafted/releases/download/v<version>/Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz
curl -fsSLO https://github.com/vetcoders/vibecrafted/releases/download/v<version>/Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz.sha256
sha256sum -c Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz.sha256
tar -xzf Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz
bash vibecrafted-<version>/install.sh --runtime-pack-file /absolute/path/to/matching-pack.tar.gz install
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
  `make release`. Vibecrafted does not ship a systemd unit; start the server
  and guardian with `vibecrafted server start`.
- On Windows, use the native win32-x64 Runtime Pack. The portable Linux
  tarball is the POSIX alternative inside WSL2.

The checksum proves the bytes survived the wire. The provenance carrier proves
they are the distribution they claim to be, pinned to one commit — the part
`curl | bash`, which follows the website channel, cannot give you.

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
