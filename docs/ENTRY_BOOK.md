# Vibecrafted Entry book — Linux and Windows

From a clean system to an installed `vibecrafted doctor` and the first agent
session. Commands below are source-backed; expected results are acceptance
checks, not a claim that your machine has already passed. Continue daily work
with the [Runbook](RUNBOOK.md). [Polski](pl/ENTRY_BOOK.md).

Use a Runtime Pack supplied with its `.sha256` and `.sig` plus the matching
checkout. If you have no pack, build one with
[Build from source](public/getting-started/build-from-source.md): Linux L1–L6
includes an independent developer signing lane; Windows W1–W4 includes a local
rehearsal key. A clone alone is not an installed runtime. The repository's
native Windows installer is `install.ps1`; do not substitute an older website
WSL installer. Check actual release assets before choosing a download.

## Linux — Ubuntu 24.04 / glibc ≥ 2.39

### L1. Prepare the machine

```bash
sudo apt-get update
sudo apt-get install -y bash ca-certificates curl file git make \
  python3 python3-venv tar zsh openssl
python3 --version
```

**Expect:** apt exit 0 and Python ≥ 3.11 (`tomllib` is required).
**Failure → fix:** Python 3.10 is below the floor; use a supported interpreter
before installation. For local compilation, install the development packages
and toolchains from Build from source L1–L5 as well.

Source: `.github/workflows/install-linux.yml` → Debian prerequisites / Python
3.11 step; `Dockerfile` → apt list;
`scripts/build-linux-runtime-pack.sh` → OpenSSL preflight.

### L2. Get the matching source and Python installer tooling

```bash
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
curl -LsSf https://astral.sh/uv/install.sh -o uv-install.sh
sh uv-install.sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
```

**Expect:** a checkout and executable `uv`. Select the revision supplied with
your pack. For this development handoff, use
`git switch port/windows-setup-wizard-fix` while that branch exists.
**Failure → fix:** a pack/source revision mismatch requires the matching
checkout or a rebuilt carrier; it is not a reason to disable verification.

Source: `docs/QUICK_START.md` → “1. Install”;
`.github/workflows/install-linux.yml` → Debian “Install uv”;
`scripts/distribution_manifest.py` → source provenance.

### L3. Install the external foundation spine

```bash
bash scripts/install-foundations.sh loctree aicx prview screenscribe
export PATH="$HOME/.vibecrafted/tools/node/bin:$HOME/.local/bin:$PATH"
node --version
```

**Expect:** executable Loctree/AICX/PRView/ScreenScribe. Missing Node/npm invokes
`ensure_node`, downloading Node 22.16.0; an existing Node is accepted, so use
Node 22. Channels: npm `@loctree/loctree`, npm `@loctree/aicx`, GitHub releases
`vetcoders/prview-rs`, PyPI `screenscribe` through uv/pipx.
**Failure → fix:** a channel/network failure stops installation. Repair access
and retry the same command. `REQUIRE_FOUNDATIONS` defaults to **1**.

**Jammy/bookworm:** glibc 2.35/2.36 cannot run the prebuilt Loctree/PRView
binaries. Successful npm output followed by `missing or broken` is that ABI
failure. Upgrade to Ubuntu 24.04/glibc ≥ 2.39, or explicitly accept a reduced
installation with the waiver below. Those tools remain absent.

```bash
REQUIRE_FOUNDATIONS=0 bash scripts/install-foundations.sh loctree aicx prview screenscribe
```

Source: `scripts/install-foundations.sh` → `ensure_node`, four product installers,
`foundation_channel_fail`; `.github/workflows/install-linux.yml` → named 22.04
and Debian waivers, unwaived 24.04.

### L4. Install the carrier

Replace the example path with the exact supplied pack. Keep both sidecars beside it.

```bash
make install RUNTIME_PACK=/absolute/path/to/Vibecrafted_RuntimePack_linux-x64.tar.gz
```

**Expect:** successful checksum/signature/provenance checks and runtime
publication. `make install` does not compile; `install-source` currently aliases
it. Local compilation requires the separate Build from source recipe.
**Failure → fix:** missing signature or wrong trust anchor means the carrier
cannot be admitted. Obtain the complete authenticated tuple. For your own
locally signed build, use the `VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY` from Linux
L6 in Build from source. For a deliberate old-glibc waiver, pass
`REQUIRE_FOUNDATIONS=0` to **this command too**. Do not bypass an active-run
installation guard: wait for the running work to finish.

Source: `Makefile` → `install`, `install-source`;
`scripts/install-runtime-pack.sh` → verification and runtime publication.

### L5. Verify the installed generation

```bash
export PATH="$HOME/.vibecrafted/bin:$HOME/.cargo/bin:$HOME/.local/bin:$HOME/.local/share/vibecrafted/bin:$PATH"
cd /tmp
vibecrafted doctor
vibecrafted version
```

**Expect:** doctor exits 0, no failures; version names the installed build.
**Failure → fix:** `command not found` means PATH is missing the directories
above. An unstamped-launcher finding inside the checkout requires testing from
this neutral directory. Yellow foundation findings identify missing tools;
a waived install does not become full because doctor exits 0.

Source: `.github/workflows/install-linux.yml` → “Run vibecrafted doctor”.

### L6. Open the first agent session

Return to the repository where you want to work:

```bash
cd /path/to/your/repo
npm install -g @openai/codex
codex
```

**Expect:** the provider's interactive session, with authentication if this is
its first use. Authenticate in that CLI; Vibecrafted does not own credentials.
**Failure → fix:** `codex` missing after npm success means the npm global bin
is absent from PATH. Fix that installation before invoking Vibecrafted.

Source: `scripts/install-foundations.sh` → `AGENT_PACKAGES` / `install_agents`;
`docs/RUNBOOK.md` → cold start (conversation in the provider CLI).

### L7. Enter the managed workspace and orientation

From a terminal, after the previous session has exited:

```bash
vibecrafted start --repo /path/to/your/repo
vibecrafted init codex
```

**Expect:** a new repository workspace in vc-frame, then an interactive
orientation session. `start` is create-only, not a dashboard alias.
**Failure → fix:** exit 3 means the workspace already exists; use
`vibecrafted start resume` for deliberate re-entry. Exit 4 identifies inventory
or creation failure; inspect the diagnostic, then `vibecrafted doctor`.
A desktop host is required for the terminal window. On a server, retain the
plain-terminal `codex` session; GUI creation is not headless acceptance.

Source: `scripts/vibecrafted` → `cmd_start_help`, init routing;
`docs/RUNBOOK.md` → cold start.

## Windows — native

### W1. Prepare PowerShell and the carrier

Use x64 Windows with **PowerShell 5.1+** and System32 `tar.exe`. Install Git for
Windows to obtain the checkout, then run in PowerShell:

```powershell
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
```

**Expect:** `install.ps1` and `scripts\install-runtime-pack.ps1` in the checkout.
Use the revision supplied with the pack (the current development handoff is
`port/windows-setup-wizard-fix`). The prebuilt pack includes Python; a customer
install does not require Cargo, Rust, npm or uv. Local builders need Windows
W1–W4 in Build from source instead.
**Failure → fix:** missing `tar.exe`, sidecars, trusted public key or pack are
all named by the preflight. Complete the list and rerun; there is no silent
success without a pack.

Source: `install.ps1` → `Invoke-Preflight`, `Get-VibecraftedVersion`;
`.github/workflows/install-windows.yml` → `windows-cold-install`.

### W2. Install without WSL

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Pack .\build\<exact-win32-x64-pack>.tar.gz
```

**Expect:** a human install summary; runtime in `%LOCALAPPDATA%\Vibecrafted`,
launchers in its `bin`, product configuration in `%APPDATA%\Vibecrafted`.
User PATH is updated or the installer prints the exact directory to add.
**Failure → fix:** `.sha256` and `.sig` are mandatory and checked before extract.
A development rehearsal pack needs its explicitly selected local public key
(Windows W4 in Build from source). Published product packs use the committed
product anchor. Never trust an arbitrary downloaded rehearsal key.

**MSI/EXE are unsigned. SmartScreen warns.** Trust comes from `.sha256` plus
`.sig` on the embedded Runtime Pack and its trusted anchor. Wrapper checksums
alone are not publisher authentication. Use Build from source W6 for their
silent install/doctor/uninstall commands.

Source: `install.ps1` → install delegate and summary;
`scripts/install-runtime-pack.ps1` → verification;
`.github/workflows/install-windows.yml` → unsigned installer jobs.

### W3. Run doctor through the installed launcher

```powershell
$bin = Join-Path $env:LOCALAPPDATA "Vibecrafted\bin"
$env:Path = "$bin;$env:Path"
& (Join-Path $bin "vibecrafted.cmd") doctor
```

**Expect:** exit 0 and explicit `not supported on Windows` lines.
**Failure → fix:** missing launcher means install did not publish a generation;
repair the named install error. Native `start`, `dashboard`, `init` and other
POSIX deck verbs exit 2 and point to WSL2. `voc`/`vc-o`, `vc-admin`, `vc-procs`,
`vc-start`, rescue/flock and PTY/zsh shells are not native capabilities.

Source: `.github/workflows/install-windows.yml` → cold doctor check;
`vibecrafted-core/vibecrafted_core/cli.py` → win32 lifecycle refusal.

### W4. Open the first native agent session

Install **Node 22** from its own distribution and ensure npm is on PATH; then,
from your working repository in PowerShell:

```powershell
npm install -g @openai/codex
codex
```

**Expect:** the provider's session and its authentication flow. This is a
provider session alongside the installed Vibecrafted runtime. Native Windows
has no admitted Vibecrafted-managed POSIX workspace path; use WSL2 below for it.
**Failure → fix:** missing npm means the Node installation/PATH is incomplete.
Authentication is handled in the provider CLI. The native Runtime Pack does not
install external foundations through the POSIX foundation script.

Source: `scripts/install-foundations.sh` → `AGENT_PACKAGES`;
`Dockerfile` → Node 22 baseline;
`docs/RUNBOOK.md` → conversation in the provider CLI;
core CLI → Windows boundary.

### W5. Use WSL2 for the full managed session

In elevated PowerShell:

```powershell
wsl --install
wsl --status
```

**Expect:** an installed distribution; reboot when requested, then a WSL status
report. Open that distribution and follow Linux L1–L7; use Ubuntu 24.04 and check
the foundation ABI boundary before installing. WSL is an explicit alternative,
not automatically started by the native CLI.
**Failure → fix:** an older WSL distribution hits the same glibc restriction as
older native Linux. Choose the newer baseline or the named waiver.

Source: `docs/INSTALL.md` → “POSIX alternative — WSL2”;
`install.sh` → WSL detection; Linux workflow → foundation waivers.
