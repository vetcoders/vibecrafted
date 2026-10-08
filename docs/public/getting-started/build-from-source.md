---
title: "Build from source"
description: "Source-backed Linux and native Windows development recipes, carrier installation, gates, and explicit platform limits."
section: getting-started
order: 25
---

# Build from source

These recipes follow the checkout's Makefile, installers and CI jobs. Each
numbered step names its executable source. Run commands from the repository
root unless a step changes directory. For a cold machine and the first session,
use the [Entry book](../../ENTRY_BOOK.md) ([Polski](../../pl/ENTRY_BOOK.md));
for daily operation, use the [Runbook](../../RUNBOOK.md).

## Read the boundary first

- **Full Linux baseline: Ubuntu 24.04, glibc ≥ 2.39.** The owned Linux pack is
  built on Ubuntu 22.04 for an older ABI, but the public Loctree and PRView
  binaries do not start on glibc 2.35/2.36 (Ubuntu 22.04/jammy, Debian
  12/bookworm). A successful npm install followed by `missing or broken`
  is an ABI failure. Today the choices are a newer system or the explicit
  `REQUIRE_FOUNDATIONS=0` waiver and the absence of those tools.
- **Python ≥ 3.11** supplies `tomllib`. Linux CI uses 3.11; Windows CI uses
  3.12 and the Windows pack embeds 3.12.10. **Node 22** is the container
  baseline; `ensure_node` downloads 22.16.0 when Node/npm are absent.
- Rust is stable on Windows. The Linux assembler and its CI build pin the
  stable release **1.97.0**, including both WASM targets on that toolchain.
- **`make install` consumes a Runtime Pack; it does not compile one.**
  `make install-source` is the retained maintainer spelling, currently defined
  as `install-source: install`. The local compilation sequence is
  `make runtime-pack` followed by `make install-source`. Calling the latter
  alone is not a build. A supplied `RUNTIME_PACK` overrides build selection.
- Foundations are external products: npm `@loctree/loctree`, npm
  `@loctree/aicx`, GitHub releases `vetcoders/prview-rs`, PyPI `screenscribe`.
  They are never vendored into the pack. `REQUIRE_FOUNDATIONS` defaults to
  **1**: a missing/broken requested foundation fails POSIX installation.
  `=0` is an explicit waiver, not a repair. Doctor warnings do not undo that
  install gate. Native `install.ps1` does not run the POSIX foundation script.

Sources: [Makefile](../../../Makefile) → `install`, `install-source`,
`runtime-pack`; [Linux workflow](../../../.github/workflows/install-linux.yml)
→ `linux-runtime-pack`, `ubuntu-native`, `debian-bookworm-container`;
[Windows workflow](../../../.github/workflows/install-windows.yml) →
`windows-runtime-pack`; [foundation installer](../../../scripts/install-foundations.sh)
→ `ensure_node`, `foundation_channel_fail`;
[Dockerfile](../../../Dockerfile) → `FROM`, build arguments;
[Windows builder](../../../scripts/build-windows-x64-runtime-pack.ps1) → embedded Python.

## Linux development — Ubuntu 24.04

### L1. Install the system prerequisites

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential cmake libclang-dev libprotobuf-dev protobuf-compiler rsync \
  libgtk-3-dev libxdo-dev libayatana-appindicator3-dev \
  bash ca-certificates curl file git make python3 python3-venv \
  shellcheck tar zsh openssl
```

Expected: apt completes with exit 0; `protoc` and the GTK/pkg-config development
files exist. `libgtk-3-dev`, `libxdo-dev` and appindicator cover the Linux
`voc`/tray dependency graph. Missing `protoc` fails `prost-build`; missing
`glib-2.0`/GTK means the development packages above are absent.

Source: [Linux workflow](../../../.github/workflows/install-linux.yml) →
“Install Runtime Pack build prerequisites”, Ubuntu “Install make + python3”,
Debian “Install prerequisites”;
[Dockerfile](../../../Dockerfile) → apt list;
[Linux builder](../../../scripts/build-linux-runtime-pack.sh) → tool preflight.
This list is the union of those recipes; it is not the shorter runtime-only list.

### L2. Clone and record the revision

```bash
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
git branch --show-current
git rev-parse HEAD
git status --short
```

Expected: a full source SHA and no dirty tracked files before carrier assembly.
For the development handoff on this branch, select
`port/windows-setup-wizard-fix` explicitly with
`git switch port/windows-setup-wizard-fix` while that branch exists. The builders
bind provenance to HEAD; do not mix a pack from one revision with another source
archive. Commit your changes before a maintainer pack build.

Source: [Quick Start](../../QUICK_START.md) → “1. Install”;
[Linux builder](../../../scripts/build-linux-runtime-pack.sh) → clean-tree preflight;
[distribution writer](../../../scripts/distribution_manifest.py) → `resolve_source_provenance`.

### L3. Provision Python tooling and Node 22

```bash
curl -LsSf https://astral.sh/uv/install.sh -o uv-install.sh
sh uv-install.sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
bash scripts/install-foundations.sh loctree aicx prview screenscribe
export PATH="$HOME/.vibecrafted/tools/node/bin:$HOME/.local/bin:$PATH"
python3 --version
node --version
uv --version
```

Expected: Python 3.11+, Node v22.x and executable foundations. The foundation
script bootstraps Node 22.16.0 if Node/npm are absent; it accepts an existing
Node, so replace an older host Node before continuing. It installs ScreenScribe
through `uv tool install` (pipx fallback), not into the system Python. If npm
reports `EACCES`, use a user-writable global npm installation rather than running
the entire build as root. On glibc 2.35/2.36, see “Older Linux” below.

Source: [Linux workflow](../../../.github/workflows/install-linux.yml) → Debian
“Install uv”; [foundation installer](../../../scripts/install-foundations.sh) →
`ensure_node`, `install_loctree`, `install_aicx`, `install_prview`, `install_screenscribe`.

### L4. Pin Rust and install both WASM targets

If rustup is absent, use the workflow's bootstrap first:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o rustup-init.sh
sh rustup-init.sh -y --default-toolchain stable --profile minimal
export PATH="$HOME/.cargo/bin:$PATH"
export RUSTUP_TOOLCHAIN=1.97.0
rustup toolchain install "$RUSTUP_TOOLCHAIN" --profile minimal
rustup target add --toolchain "$RUSTUP_TOOLCHAIN" \
  wasm32-unknown-unknown wasm32-wasip1
rustup target list --installed --toolchain "$RUSTUP_TOOLCHAIN"
```

Expected: the installed-target list contains both names above. A target installed
on an ambient default toolchain does not satisfy a build selected by
`RUSTUP_TOOLCHAIN`. Add it to **1.97.0**, not to a different default.

Source: [Linux workflow](../../../.github/workflows/install-linux.yml) →
“Install Rust toolchain”, “Install Runtime Pack build prerequisites”;
[assembler](../../../scripts/build-linux-arm64-runtime-pack.sh) → toolchain provisioning.

### L5. Install cargo-leptos and the lock-matched wasm-bindgen CLI

```bash
curl -L --proto '=https' --tlsv1.2 -sSf \
  -o /tmp/install-binstall.sh \
  https://raw.githubusercontent.com/cargo-bins/cargo-binstall/main/install-from-binstall-release.sh
bash /tmp/install-binstall.sh
cargo binstall -y cargo-leptos
lock_version="$(cargo tree --locked --manifest-path vibecrafted-server/Cargo.toml -p wasm-bindgen --depth 0 --prefix none | awk 'NR == 1 { sub(/^v/, "", $2); print $2 }')"
cargo binstall -y "wasm-bindgen-cli@${lock_version}"
```

Expected: both tools install; `lock_version` comes from the checked-in dependency
lock, not from the latest wasm-bindgen release. A wasm-bindgen version mismatch
is fixed by rerunning the lock query and installing that exact CLI version.

Source: [Linux workflow](../../../.github/workflows/install-linux.yml) →
“Install Runtime Pack build prerequisites”.

### L6. Build, sign and install one exact carrier

Maintainers with the product release key in `~/.keys/vibecrafted-signing.key`:

```bash
make runtime-pack
make install-source
```

Expected: `make runtime-pack` prints a signed pack path and writes
`build/runtime-pack-selection.json`; the second command installs that completed
carrier. Missing product credentials stop this lane before compilation.
An interrupted build leaves selection pending and installation refuses it.

For an independent developer, use the assembler lane and your own trust anchor.
Do not request or copy the product's private key:

```bash
export VIBECRAFTED_SOURCE_OWNER_REPO=vetcoders/vibecrafted
export VIBECRAFTED_SOURCE_REVISION="$(git rev-parse HEAD)"
pack="$PWD/build/Vibecrafted_RuntimePack_linux-x64.tar.gz"
bash scripts/build-linux-runtime-pack.sh "$pack"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out build/dev-signing.key
openssl pkey -in build/dev-signing.key -pubout -out build/dev-signing.pub
openssl dgst -sha256 -sign build/dev-signing.key -out "$pack.sig" "$pack"
openssl dgst -sha256 -verify build/dev-signing.pub -signature "$pack.sig" "$pack"
VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY="$PWD/build/dev-signing.pub" \
  make install RUNTIME_PACK="$pack"
```

Expected: `Verified OK`, then a successful install. This x64 example requires
an x86_64 Linux host; use `linux-arm64` in the filename on an arm64 host (the
assembler chooses the host architecture). Keep the development private key
local and untracked. This is a development signature, not a product release.
The assembler produces the sibling `.sha256`; the commands add `.sig`.

Source: [Makefile](../../../Makefile) → `runtime-pack`, `install`, `install-source`;
[Linux builder](../../../scripts/build-linux-runtime-pack.sh) → two lanes / missing-key
instructions; [Linux workflow](../../../.github/workflows/install-linux.yml) →
“Build and sign the exact-source CI carrier”.

### L7. Verify the installed runtime outside the checkout

```bash
export PATH="$HOME/.vibecrafted/bin:$HOME/.cargo/bin:$HOME/.local/bin:$HOME/.local/share/vibecrafted/bin:$PATH"
cd /tmp
vibecrafted doctor
vibecrafted version
```

Expected: doctor exits 0 with no failures; version carries the installed source
stamp. A checkout cwd can shadow the installed Python tree and produce an
unstamped-launcher finding. Use a neutral cwd as CI does. A foundation warning
is still a missing capability; it is not evidence that a waived install is full.

Source: [Linux workflow](../../../.github/workflows/install-linux.yml) → “Run vibecrafted doctor”.

### L8. Run development gates and build the portable source artifact

Return to the checkout:

```bash
make check
make test
make portable
```

Expected: `Check complete.`, a pytest summary, then a provenance-verified portable
source archive under `dist/`. `make test` runs the TUI tree plus its prerequisite
keychain check; it is not every test in the monorepo. `make test-core` is separate.
Never combine `tests/tui` and `vibecrafted-core/tests` in one pytest process.
`make portable` packages source; it does not replace the binary Runtime Pack.
Use a clean committed tree for provenance-bound artifacts.

Source: [Makefile](../../../Makefile) → `check`, `test`, `test-core`, `portable`;
[portable builder](../../../scripts/build-portable-release.sh) → provenance validation.

## Older Linux and Debian 12 container reference

The exact Debian apt recipe is in [install-linux.yml](../../../.github/workflows/install-linux.yml)
→ `debian-bookworm-container` / “Install prerequisites”. It adds `file`, `shellcheck`,
`sudo`, `tar`, `python3-venv` and the same build/GTK libraries; `rsync` is in
the separate pack-build job. The container is `debian:bookworm-slim`.
[Dockerfile](../../../Dockerfile) uses **`node:22-bookworm-slim`** and defaults
`INSTALL_FOUNDATIONS=false`, `INSTALL_RUST=false`, `INSTALL_AGENT_CLIS=false`.
It is a reference Linux base, not proof of full external-tool support.

Jammy/bookworm have glibc 2.35/2.36. CI explicitly waives foundations there;
Ubuntu 24.04 retains the hard gate. To accept that same reduced capability:

```bash
REQUIRE_FOUNDATIONS=0 bash scripts/install-foundations.sh loctree aicx prview screenscribe
REQUIRE_FOUNDATIONS=0 make install RUNTIME_PACK=/absolute/path/to/your-pack.tar.gz
```

Loctree/PRView remain unavailable. Reinstalling the same npm package does not
repair the libc ABI. Upgrade to Ubuntu 24.04/glibc ≥ 2.39 for the full spine.
Source: workflow `REQUIRE_FOUNDATIONS` matrix and Debian environment;
foundation installer → `foundation_channel_fail`.

## Native Windows development — win32-x64, without WSL

### W1. Prepare the builder host and clone

Use an x64 Windows host with PowerShell 5.1+, Git for Windows, the MSVC x64 C++
build tools/Windows SDK, Rust **stable**, Python **3.12** on PATH and OpenSSL.
The workflow uses `windows-latest` with its preinstalled MSVC toolchain;
the repository does not contain a clean-host MSVC installer. Install those
build tools before invoking Cargo. Node 22 is for external npm products;
`uv`/Git Bash are builder tools, not customer-install prerequisites.

```powershell
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
rustup toolchain install stable --profile minimal
rustup default stable
python --version
cargo --version
```

Expected: Python 3.12 and a stable Rust toolchain. ARM64 is refused by this
x64 builder. `link.exe` missing means the MSVC build environment is absent.

Source: [Windows workflow](../../../.github/workflows/install-windows.yml) →
“Install Rust toolchain”, “Setup Python”;
[Windows builder](../../../scripts/build-windows-x64-runtime-pack.ps1) → host guards,
`Install-CargoBin`, `x86_64-pc-windows-msvc`;
[Quick Start](../../QUICK_START.md) → “1. Install” clone recipe.

### W2. Provision OpenSSL for vc-frame

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ci-install-openssl-win64.ps1
```

Run this builder prerequisite from an elevated PowerShell when OpenSSL is
absent: the helper installs to Program Files and adjusts system PATH. Reopen
the builder shell after installation so `openssl` is visible for signing.
Expected: the helper installs the pinned FireDaemon OpenSSL x64 distribution.
The pack builder discovers `OPENSSL_DIR` in Program Files, or accepts an explicit
installation directory with headers, import libraries and the two shared DLLs.
Missing headers/DLLs are fatal; do not remove vc-frame from the payload to pass.
The builder fetches protoc 29.3 when a working `PROTOC`/`protoc` is absent.

Source: [Windows workflow](../../../.github/workflows/install-windows.yml) →
“Install OpenSSL (vc-frame)”;
[builder](../../../scripts/build-windows-x64-runtime-pack.ps1) → protoc/OpenSSL preflight.

### W3. Build the native carrier

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-windows-x64-runtime-pack.ps1
$pack = Get-ChildItem -LiteralPath build -Filter "Vibecrafted_RuntimePack_*-win32-x64.tar.gz" |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $pack) { throw "Windows Runtime Pack missing under build" }
```

Continue only after the builder exits 0; do not select a historical pack after
a failed build. Expected: a version/date/SHA-named pack, sibling `.sha256` and `.sig`.
The builder includes Python, vc-server, vc-terminal and vc-frame; foundations
come from their own channels. It fetches donors pinned by revision and archive
hash. If cargo-leptos is present it builds the web assets; otherwise the builder
uses the native SSR Cargo fallback.

Source: [Windows workflow](../../../.github/workflows/install-windows.yml) →
“Build Windows Runtime Pack”; [builder](../../../scripts/build-windows-x64-runtime-pack.ps1)
→ server build, donors, embedded Python, signing.

### W4. Select the development trust anchor, then install

A local build without the product key creates a rehearsal signature and
`$pack.FullName.rehearsal.pub`. Select that anchor **only for your local build**:

```powershell
$env:VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY = "$($pack.FullName).rehearsal.pub"
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Pack $pack.FullName
```

Expected: a human install summary and the per-user launcher directory
`%LOCALAPPDATA%\Vibecrafted\bin`. Published product packs use the committed
`vibecrafted-signing-v1.pub` instead; leave the override unset for those.
Missing `.sha256`, `.sig` or trusted key fails before extraction.
Never treat a rehearsal key next to a downloaded artifact as release trust.

Source: [Windows workflow](../../../.github/workflows/install-windows.yml) →
“Cold install into disposable LOCALAPPDATA” (CI trust override);
[builder](../../../scripts/build-windows-x64-runtime-pack.ps1) → rehearsal signing;
[install.ps1](../../../install.ps1) → preflight and User PATH.

### W5. Verify from the installed launcher

```powershell
$bin = Join-Path $env:LOCALAPPDATA "Vibecrafted\bin"
$env:Path = "$bin;$env:Path"
& (Join-Path $bin "vibecrafted.cmd") doctor
```

Expected: exit 0 and explicit **`not supported on Windows`** declarations.
Native `dashboard`, `start`, `init` and other POSIX deck commands return exit 2
with a WSL2 next step. `voc`, `vc-o`, `vc-admin`, `vc-procs`, `vc-start`,
rescue/flock and PTY/zsh shells are not a native managed-workspace path.
A doctor pass does not establish those capabilities. For a first native agent
session or WSL2 workspace, follow the [Entry book](../../ENTRY_BOOK.md#windows--native).

Source: [Windows workflow](../../../.github/workflows/install-windows.yml) →
“Cold install into disposable LOCALAPPDATA”, “vibecrafted doctor from installed launcher”;
[core CLI](../../../vibecrafted-core/vibecrafted_core/cli.py) → win32 lifecycle refusal.

### W6. Build and exercise the unsigned MSI/EXE carriers

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-windows-installers.ps1
```

Expected: carriers under `packaging\windows\out` with adjacent checksums.
**MSI/EXE are unsigned Authenticode; SmartScreen warns.** Trust is `.sha256`
plus the pack's `.sig` and trusted key. The wrapper checksums alone do not
authenticate a publisher; the workflow only lists `.sha256` for MSI/EXE, not
standalone wrapper `.sig` files. Their embedded Runtime Pack is signature-checked.

For disposable per-user install testing, substitute the exact paths emitted
above and run **one** installer at a time. The install command is the default
EXE action; do not add `/install`.

```powershell
$msi = (Resolve-Path "packaging\windows\out\<exact-name>.msi").Path
$proc = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /l*v `"$PWD\msi-install.log`" VC_SKIP_TERMINAL_LAUNCH=1" -Wait -PassThru
$proc.ExitCode
vibecrafted doctor
$stable = (& python scripts/windows_product_code.py).Trim()
$proc = Start-Process msiexec.exe -ArgumentList "/x `"{$stable}`" /qn /l*v `"$PWD\msi-uninstall.log`"" -Wait -PassThru
$proc.ExitCode

$exe = (Resolve-Path "packaging\windows\out\<exact-name>.exe").Path
$proc = Start-Process $exe -ArgumentList "/quiet /norestart /log `"$PWD\exe-install.log`" VC_SKIP_TERMINAL_LAUNCH=1" -Wait -PassThru
$proc.ExitCode
vibecrafted doctor
$proc = Start-Process $exe -ArgumentList "/uninstall /quiet /norestart /log `"$PWD\exe-uninstall.log`"" -Wait -PassThru
$proc.ExitCode
```

Expected: each process exits 0; doctor has no failures; uninstall removes the
product-owned per-user PATH entry and installed payload. Use logs to diagnose a
nonzero exit. MSI install logs contain `Skipping action: LaunchVcTerminal
(condition is false)`; quiet EXE also skips terminal launch. The workflow runs
MSI and EXE round trips on separate runners to avoid their shared ProductCode.
These commands uninstall the test installation; do not run them over an active
workspace. For script-installed packs, use `install.ps1 -Uninstall` instead.

Source: [Windows workflow](../../../.github/workflows/install-windows.yml) →
`windows-installers`, `windows-msi-install`, `windows-exe-install` / silent
install, doctor and silent uninstall steps;
[installer builder](../../../scripts/build-windows-installers.ps1).

### W7. Choose the Windows gate surface

```powershell
python -m pytest tests/tui/test_windows_installers.py -q
```

Expected: a nonempty pytest summary. Native PowerShell does not turn POSIX
`make check`, Bash tests or zsh entry into native equivalents. Run the POSIX
source gates in WSL2 (Linux steps L1–L8); the Windows workflow is the actual
native carrier install/doctor/uninstall gate.

Source: [Windows installer tests](../../../tests/tui/test_windows_installers.py);
[Makefile](../../../Makefile) → `check`, `test`;
[Windows workflow](../../../.github/workflows/install-windows.yml).

## macOS maintainers

The Linux/Windows recipes above do not build the macOS App. For the signed,
notarized and stapled desktop handoff, follow
[INSTALL → desktop artifact](../../INSTALL.md#build-the-desktop-artifact-maintainers)
and the [release checklist](../../RELEASE_CHECKLIST.md). Signing credentials
remain local; a successful local build does not establish publication.
