# Quick Start

For source-backed cold-machine instructions, use the [Entry book](ENTRY_BOOK.md)
([Polski](pl/ENTRY_BOOK.md)). Developer builds: [Build from source](public/getting-started/build-from-source.md).

## 1. Install

Check the assets actually attached to
[GitHub Releases](https://github.com/vetcoders/vibecrafted/releases/latest).
As verified on 2026-10-05, latest is the legacy `v3.5.0` release; it has no
4.x DMG, Runtime Pack or Windows MSI/EXE carriers. The versioned carrier
instructions below describe the pending publication. Each new carrier must
have an adjacent checksum; Runtime Packs also require detached signatures.
The POSIX bootstrap below is currently public.

**macOS and Linux:**

```bash
curl -fsSL https://vibecrafted.io/install.sh | bash
```

**Windows (native)** — win32-x64 Runtime Pack via `install.ps1` (no WSL
required). Artifact names look like
`Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-win32-x64.tar.gz`. The
MSI/EXE carriers are unsigned; SmartScreen will warn; trust is `.sha256` +
`.sig`. See [INSTALL.md](INSTALL.md#windows--native-runtime-pack).
Until those carriers are published, this requires a locally built pack and
the checkout installer. The public `https://vibecrafted.io/install.ps1`
still serves the older WSL2-only launcher.

**Windows (POSIX alternative)** — install WSL2 once, then use the same
bootstrap inside it:

```powershell
wsl --install
wsl bash -c 'curl -fsSL https://vibecrafted.io/install.sh | bash'
```

**macOS CLI, without the App:** download the Runtime Pack plus `.sha256` and
`.sig` from the latest release, then:

```bash
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
make install RUNTIME_PACK=../Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz
make uninstall  # deterministic reset from the same receipt
```

On macOS the intended end-user artifact is one signed and notarized
`Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg`, an artifact of releases from
4.3.1 on, verified against its adjacent `.dmg.sha256`. Until a published
release carries it, use the bootstrap above (see [INSTALL.md](INSTALL.md)).

Power users can skip the DMG and App entirely. The adjacent
`Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz` is
the same signed binary runtime that onboarding installs from the App.

The portable source distribution is
`Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz`. Pair it with the
matching Linux or macOS Runtime Pack; it does not replace that binary carrier. It pins one exact commit through a closed `source-provenance.json`,
which `curl | bash` cannot do:

```bash
shasum -a 256 -c Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz.sha256
tar -xzf Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz
bash vibecrafted-<version>/install.sh --runtime-pack-file /absolute/path/to/matching-pack.tar.gz install
```

Source: `install.sh` → local source-carrier preflight / `--runtime-pack-file`;
`Makefile` → `install`.

Every channel and its status: [INSTALL.md](INSTALL.md).

## 2. Verify

```bash
vibecrafted doctor
vibecrafted version
```

`doctor` separates failures from missing capabilities. POSIX installation requires
Loctree/AICX/PRView/ScreenScribe by default (`REQUIRE_FOUNDATIONS=1`); doctor
warnings are not an install waiver. Full Linux baseline is Ubuntu 24.04 /
glibc ≥ 2.39. On jammy/bookworm, prebuilt Loctree/PRView cannot start; use a
newer system or explicitly accept their absence with `REQUIRE_FOUNDATIONS=0`.
Native Windows doctor declares unsupported POSIX surfaces. Red means act.

Source: `scripts/install-foundations.sh` → `foundation_channel_fail`;
`.github/workflows/install-linux.yml` → foundation waivers;
core `cli.py` → Windows declarations.

## 3. Orient your agent

From any repository:

```bash
vibecrafted init claude
# or
vibecrafted init codex
```

These orientation commands run on POSIX (Linux/WSL2/macOS). Native Windows uses
the provider CLI directly; `init` returns exit 2 with a WSL2 next step.

Both forms recover intentions through AICX, map the living tree through Loctree,
and check runtime truth before work begins.

If the agent CLI is not installed, `init` names the gap and prints the exact
install command rather than exiting silently. Vibecrafted drives agent CLIs; it
does not bundle them. See [First run](public/getting-started/first-run.md).

## 4. Build something

```bash
vibecrafted implement codex --prompt "Add user authentication with JWT"
```

No terminal UI required — dispatch, then observe:

```bash
vibecrafted observe <run-id>
```

Use `vibecrafted help` for the full operator surface.

## Developer checkout path

`make install` consumes a closed Runtime Pack selected for macOS or Linux and
the host architecture. WSL2 consumes the matching Linux carrier and no default
install silently compiles. `make install-source` is the retained maintainer spelling;
in this Makefile it aliases `install`. The explicit local compilation sequence
is `make runtime-pack` followed by `make install-source`. A developer checkout also exposes the
build, test and release targets.
Run `make help-dev` for the full inventory, or read
[Build from source](public/getting-started/build-from-source.md).
