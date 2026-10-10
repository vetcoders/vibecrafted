# Install Vibecrafted

Vibecrafted runs on macOS, Linux, and native Windows (win32-x64 Runtime Pack).
WSL2 remains a POSIX alternative. Cold-machine steps: [Entry book](ENTRY_BOOK.md)
([Polski](pl/ENTRY_BOOK.md)); developer recipes: [Build from source](public/getting-started/build-from-source.md). The channels differ in what they give you
and in how finished they are, so this page states both.

Native Windows supports Python-owned commands such as `doctor` and `server`.
POSIX command-deck surfaces such as `dashboard`, `start`, and `telemetry`
return exit code 2 with a WSL2 next step on native Windows. Run those commands
inside WSL2; the native CLI does not start WSL2 automatically.

## Channel matrix

| Channel                      | Platform             | What you get                                                        | Status                                         |
| ---------------------------- | -------------------- | ------------------------------------------------------------------- | ---------------------------------------------- |
| Signed `Vibecrafted.app` DMG | macOS 14+, arm64     | Full desktop product: terminal, frame, runtime, server              | Build path complete; publication pending       |
| Portable tarball             | Linux, WSL2, macOS   | Command deck, runtime, control plane, skills — pinned to one commit | Build path complete; publication pending       |
| Native Runtime Pack          | Windows win32-x64    | Command deck, pack Python, foundations, vc-server; no WSL           | Built from checkout; publication pending       |
| Bootstrap `install.sh`       | macOS, Linux, WSL2   | Command deck, runtime, control plane, skills                        | Published; CI-gated                            |
| Source checkout              | macOS, Linux, WSL2   | Development tree and targets — not a native Runtime Pack            | Published                                      |
| Container                    | anywhere Docker runs | Isolated operator runtime                                           | Published                                      |
| `install.ps1`                | Windows              | Native Runtime Pack entry (delegates to `install-runtime-pack.ps1`) | In repo; public URL still serves WSL2 launcher |

If you want one sentence: **on macOS and Linux use the bootstrap today; on
Windows install the win32-x64 Runtime Pack with `install.ps1 -Pack`.**

---

## macOS — the signed desktop app

This is the intended shape of the end-user product: one Developer ID signed and
notarized artifact that carries matching builds of `vc-terminal`, `vc-frame`,
`vc-start` and the complete Vibecrafted runtime. No companion repository
installer is required.

When a DMG is attached to a release, install it like this:

1. Open the [latest release](https://github.com/vetcoders/vibecrafted/releases/latest).
2. Download `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg` and the adjacent
   `.dmg.sha256`.
3. Verify the bytes before you open them:

   ```bash
   shasum -a 256 -c Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg.sha256
   ```

4. Open the DMG and drag `Vibecrafted.app` to Applications.

Check what a given release actually carries before you plan around it:

```bash
gh release view --json assets -q '.assets[].name'
```

> **Release status.** Use the assets actually attached to a published release.
> `make release` builds a signed, notarized and stapled DMG plus a signed
> `release-output.json`; a local build does not establish publication or a
> successful cold installation. The publisher checks downloaded carriers before
> making the release public. If the latest release has no DMG, use a channel
> below.
> Maintainers building the DMG locally: see
> [Build from source](#build-from-source-power-users).

---

## Every other system — the portable tarball

Apple notarization is a macOS-only trust anchor. Linux and WSL2 get their own
canonically named artifact on the same release, cut from the same commit:
`Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz` plus `.sha256`.

```bash
curl -fsSLO https://github.com/vetcoders/vibecrafted/releases/latest/download/Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz
curl -fsSLO https://github.com/vetcoders/vibecrafted/releases/latest/download/Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz.sha256
sha256sum -c Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz.sha256
tar -xzf Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz
bash vibecrafted-<version>/install.sh --runtime-pack-file /absolute/path/to/matching-pack.tar.gz install
```

Supply the matching binary Runtime Pack with its checksum and signature; this
source archive is not the native carrier. Source: `install.sh` → local portable
carrier preflight / `--runtime-pack-file`.

Why this exists next to the bootstrap: `curl | bash` pins you to whatever a
branch holds at the moment you run it. The tarball pins you to one commit and
proves it. Its `source-provenance.json` carries a
`vibecrafted.distribution-tree.v1` digest over every packed entry, bound to that
commit, and `install.sh` re-validates the carrier before staging anything —
which is also why `--archive-url` and `--archive-file` refuse an archive that
does not carry one.

Scope, stated plainly:

- It is a **source distribution**, not a prebuilt-binary bundle. Product
  launchers and native `vc-terminal` / `vc-frame` hosts stay on the Runtime
  Pack. A source checkout can install skill views with `--skills-only`; it
  cannot pretend to be a complete native runtime.
- Linux prebuilt packs (`linux-x64`, `linux-arm64`) are a separate carrier,
  built natively by `scripts/build-linux-runtime-pack.sh`. They are not
  produced by macOS `make release`. 4.3.1 does not ship a systemd unit;
  on Linux the process pair is `vibecrafted server start` (server +
  guardian), not `systemctl`.
- On Windows, use the native win32-x64 Runtime Pack (`install.ps1 -Pack`).
  The portable Linux tarball is what you install _inside_ WSL2 if you choose
  the POSIX alternative.

Maintainers build it with `make portable`. It needs no signing identity and no
notary account — only `git` and `python3` — so it builds on Linux too.

> **Current status.** Build path complete and self-verifying (the builder
> unpacks and re-validates what it just wrote before the bytes may leave the
> machine). It is not published yet; it will be attached to the first release that
> carries it.

### Runtime boundary

Every new or restored workspace has a durable `workspace_id` and enters through
the bundled `vc-start`. The app sources an app-owned XDG/runtime environment; it
does not rewrite your terminal, shell, Zellij or vc-frame configuration.

The server endpoint is read from Vibecrafted settings and may be any host:port —
for example `http://127.0.0.1:3024`. It is never baked into the app and never
inferred from a local checkout.

---

## macOS and Linux — the bootstrap installer

This is the path the website advertises and the path CI exercises on every push.

```bash
curl -fsSL https://vibecrafted.io/install.sh | bash
```

The installer detects your platform before it does anything else
(`detect_platform` resolves to `macos`, `linux`, `wsl` or `unsupported`),
detects your distribution family on Linux, reports every missing prerequisite in
one pass rather than failing one tool at a time, and stages a versioned runtime
generation under `~/.local/share/vibecrafted/tools/`.

If you prefer to read before you run — always reasonable for a piped installer:

```bash
curl -fsSL https://vibecrafted.io/install.sh -o install.sh
less install.sh
bash install.sh
```

### Linux support

**Full foundation baseline: Ubuntu 24.04 / glibc ≥ 2.39.** On glibc 2.35/2.36
(Ubuntu 22.04/jammy, Debian 12/bookworm), public prebuilt Loctree and PRView
binaries do not start. npm can succeed and the executable still reports
`missing or broken`. Today use a newer system or explicitly waive foundations:

```bash
REQUIRE_FOUNDATIONS=0 make install RUNTIME_PACK=/absolute/path/to/your-pack.tar.gz
```

That accepts the absence of those tools; it does not fix their ABI.
`scripts/install-foundations.sh` defaults `REQUIRE_FOUNDATIONS` to **1**, so
missing/broken requested foundations hard-fail POSIX installation. Channels:
npm `@loctree/loctree`, npm `@loctree/aicx`, GitHub releases
`vetcoders/prview-rs`, PyPI `screenscribe`. `=0` is a caller-owned waiver.

`.github/workflows/install-linux.yml` builds the owned pack on Ubuntu 22.04,
then installs on 22.04, 24.04 and `debian:bookworm-slim`. The 22.04/Debian jobs
explicitly waive foundations; 24.04 retains the hard gate. A green doctor with
warnings on an old-libc host is not full foundation acceptance.
`Dockerfile` uses `node:22-bookworm-slim`, with foundations off by default.

Python must be ≥ 3.11 (`tomllib`); Linux CI uses 3.11. Node 22 and the Linux
stable Rust pin 1.97.0, its WASM targets and exact apt development dependencies
are covered step-by-step in Build from source. Runtime-only installs consume
prebuilt binaries; do not infer a compiler requirement from stale CI comments.

<!-- Sources: install-linux.yml linux-runtime-pack, ubuntu-native,
     debian-bookworm-container; install-foundations.sh foundation_channel_fail;
     Dockerfile FROM/ARG; Makefile install. -->

The macOS-only pieces are the desktop app, notarization, and the `locterm`
runtime. Everything else — command deck, control plane, dispatch, skills,
settlement ledger — runs on Linux.

---

## Windows — native Runtime Pack

The native Windows product is the **win32-x64 Runtime Pack**. It does not
require WSL. Layout:

- Runtime home: `%LOCALAPPDATA%\Vibecrafted` (`active.json` + `releases/<version>`)
- Launchers: `%LOCALAPPDATA%\Vibecrafted\bin\*.cmd`
- Control plane: `%LOCALAPPDATA%\Vibecrafted\home`
- Product config: `%APPDATA%\Vibecrafted`
- `tools/vibecrafted-current` is a directory **junction**, not a unix symlink.

Mandatory payload: pack-owned `python.exe`, `vc-server`, `vc-terminal`, and
`vc-frame`. Foundations (`loct`, `aicx`, `prview`, `screenscribe`) ship through
their own channels. `voc` / `vc-o` / `vc-admin` / `vc-procs` / `vc-start` stay
out of this pack until a Windows AF_UNIX mux transport exists. Rescue/flock,
PTY/zsh shells, and those omitted radios are **not supported on Windows** —
`vibecrafted doctor` declares them explicitly; use WSL2 for the POSIX path.

Canonical artifact names (ProductCode is version-derived by
`scripts/windows_product_code.py`; repeatable builds of one version share it):

- Pack: `Vibecrafted_RuntimePack_<ver>-<YYYYMMDD>-<sha8>-win32-x64.tar.gz` (+ `.sha256` + `.sig`)
- MSI/EXE: `Vibecrafted_<ver>-<YYYYMMDD>-<sha8>-windows-x64.{msi,exe}` (+ `.sha256`)

**Unsigned Authenticode.** The MSI/EXE carriers are not Microsoft-signed.
SmartScreen will warn. Trust is the same provenance model as the portable
tarball (checksum + detached signature), not Apple notarization and not a
self-signed distribution cert.

From a checkout:

```powershell
powershell -NoProfile -File .\scripts\build-windows-x64-runtime-pack.ps1
powershell -NoProfile -File .\install.ps1 -Pack .\build\Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-win32-x64.tar.gz
```

`install.ps1` preflights every missing prerequisite in one pass, does not
require a developer toolchain, updates User PATH for
`%LOCALAPPDATA%\Vibecrafted\bin` (or prints that exact directory), and prints a
human summary — never silent success and never a raw JSON dump as the success
face. First-run verification after install:

```powershell
vibecrafted doctor
```

Native `init` is a POSIX deck surface and returns exit 2. For the first native
agent session, use its CLI in PowerShell; for a managed workspace, use WSL2.
See the Entry book.

Or call the verifier/installer directly:

```powershell
powershell -NoProfile -File .\scripts\install-runtime-pack.ps1 -Pack <RuntimePack.tar.gz>
```

Checksum (`.sha256`) and detached signature (`.sig`) are checked **before**
extract. System32 `tar.exe` unpacks the carrier. Pack Python then runs
`runtime-install`.

### POSIX alternative — WSL2

WSL2 remains available for the Linux bootstrap. It is not the native Windows
product. From an elevated PowerShell prompt:

```powershell
wsl --install
```

Reboot when prompted. Verify:

```powershell
wsl --status
```

### 2. Install Vibecrafted inside your distro

```powershell
wsl bash -c 'curl -fsSL https://vibecrafted.io/install.sh | bash'
```

Or open your WSL shell and run the ordinary Linux one-liner.

`install.sh` detects WSL explicitly — it reads `/proc/sys/kernel/osrelease` and
`/proc/version` for a `microsoft`/`wsl` marker — and treats it as Linux for
runtime purposes. The WSL banner changes the reported platform line, not the
install layout.

### What `install.ps1` is for

`install.ps1` is the native Windows entry. With `-Pack` (or
`VIBECRAFTED_RUNTIME_PACK`, or a single `dist/*-win32-x64.tar.gz`) it
delegates to `scripts/install-runtime-pack.ps1`. Without a pack it prints
the exact build/install commands and **exits non-zero**.

```powershell
.\install.ps1 -Pack .\build\Vibecrafted_RuntimePack_<version>-win32-x64.tar.gz
```

> **Current status (verified 2026-10-05).**
> `https://vibecrafted.io/install.ps1` serves the older WSL2-only launcher,
> not this native Runtime Pack installer. Do not `iwr | iex` it for a native
> installation. Use the
> checkout form. Publication of signed win32-x64 carriers is still pending;
> the builder and installer are in-tree.

---

## Container

Use a container when you want the framework isolated from the host toolchain.

```bash
docker build -t vetcoders/vibecrafted:local .
docker run --rm -it -v "$PWD:/workspace" vetcoders/vibecrafted:local version
```

See [Docker Runtime](DOCKER.md) for the full topology, volume layout and
control-plane wiring.

---

## Build from source (power users)

A committed source checkout carries the build, test and release targets.
Plain `make install` builds a signed Runtime Pack from this revision and the
Frame/Terminal revisions in `config/source-components.json`, then installs
through the same verified Runtime Pack publisher used by release carriers.
`make install-source` is a retained alias. Run:

```bash
make install
```

To install an existing carrier without rebuilding, pass
`RUNTIME_PACK=/absolute/path/to/RuntimePack.tar.gz` to `make install`.

Source: `Makefile` → `runtime-pack`, `install`, `install-source`.
The Linux local build lane requires a clean tracked tree and the product release
key; an independent developer can build/sign with a local key and explicitly
select its public anchor. The complete Linux and native Windows recipes,
prerequisites, expected output and failure repairs live in
[Build from source](public/getting-started/build-from-source.md).

### Building a Runtime Pack and installing that exact one

```bash
make runtime-pack && make install
```

The builder records which carrier it completed, and `make install` installs
those bytes. `make runtime-pack` prints the same path it recorded.

The record lives in `build/runtime-pack-selection.json`. It is build state, not
configuration: it names one absolute path with its digest and the source,
terminal and frame revisions the carrier is required to claim. Historical packs
in `dist/` stay where they are — selection never ranks them by modification time
or glob order, and never deletes one to make an answer unambiguous.

The attempt is marked pending before the build starts and published only after
the archive is packaged, verified and signed. So:

- an interrupted or failed build — including a retry at the same commit —
  refuses to install, instead of quietly reinstalling the last success;
- a record from a different checkout, a different platform, or one whose
  carrier no longer matches its digest fails visibly rather than falling back
  to some other archive;
- `RUNTIME_PACK=/path/to/pack.tar.gz` still wins outright, which is how you
  deliberately install another signed generation.

Selection chooses an artifact; it never authenticates one. The checksum,
detached signature, archive topology and internal provenance checks are
unchanged, and the recorded identities are fed into them.

For a browser-guided install surface instead of the terminal one:

```bash
make wizard      # or: make gui-install
```

### The target surface

`make help` shows the everyday targets. `make help-dev` shows everything:

```bash
make help        # install · doctor · update · uninstall · test · check · release
make help-dev    # the full inventory
```

Grouped, that inventory is:

| Group   | Targets                                                                                                                                                                                                                                                                                                                         |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| install | `install` · `install-auto` · `install-all` · `install-python-tools` · `install-vendored-binaries` · `install-app-binaries` · `install-server` · `install-server-service` · `install-hammerspoon` · `skills` · `helpers` · `setup-dev` · `wizard` · `gui-install` · `dry-run` · `restore` · `migrate` · `foundations` · `bundle` |
| tests   | `test` · `test-core` · `test-skills` · `test-install` · `test-parity` · `test-vc-frame` · `test-memex` · `test-aicx-sync` · `test-hammerspoon` · `dispatch-test` · `test-race-protection` · `check` · `semgrep`                                                                                                                 |
| server  | `server` · `server-build` · `server-check` · `server-test` · `server-smoke`                                                                                                                                                                                                                                                     |
| release | `app` · `dmg` · `dmg-signed` · `release-local` · `notarize` · `release` · `publish-release`                                                                                                                                                                                                                                     |
| version | `version` · `version-show` · `version-bump` · `bump-patch` · `bump-minor` · `bump-major`                                                                                                                                                                                                                                        |
| iterm2  | `iterm-plugin` · `iterm-plugin-refresh` · `iterm-plugin-show` · `iterm-plugin-uninstall`                                                                                                                                                                                                                                        |
| hooks   | `init-hooks` · `seed-commit-msg-hooks` · `commit-safe`                                                                                                                                                                                                                                                                          |

### Run the gates

```bash
make test        # the full suite
make check       # shell lint
make semgrep     # security gate
```

Two test trees exist and must not share a single pytest invocation — their
`conftest.py` files collide under one run. Invoke them separately:

```bash
uv run --project vibecrafted-core pytest vibecrafted-core/tests
uv run --project vibecrafted-core pytest tests/tui
```

### Build the desktop artifact (maintainers)

Building a distributable DMG requires Apple Developer ID signing material. The
release script reads it from `$KEYS` (default `~/.keys`):

| File                      | Purpose                   |
| ------------------------- | ------------------------- |
| `signing-identity.txt`    | Developer ID identity     |
| `Certificates.p12`        | signing certificate       |
| `cert_password.txt`       | certificate password      |
| `vibecrafted-signing.key` | detached artifact signing |
| Keychain profile/API key  | notarytool credentials    |

```bash
make app             # build Vibecrafted.app only
make dmg             # build an unsigned/un-notarized DMG
make release         # build, sign and notarize the canonical versioned DMG
make publish-release # cold-verify the built DMG and publish it
```

The release build sets `MACOSX_DEPLOYMENT_TARGET=14.0` and remaps Rust path
prefixes so release payloads never carry the operator's home directory, Cargo
registry paths, or checkout location in panic and debug metadata.

Without signing material, `make app` and `make dmg` still work for local
testing; `make release` will not.

---

## First run

The first command a stranger runs is the one that decides whether the product
feels finished. Vibecrafted's entry points are built to name what is missing
rather than to fail silently.

### Orient an agent

```bash
vibecrafted init claude
# or
vibecrafted init codex
```

`init` recovers intent through AICX, maps the living tree through Loctree, and
checks runtime truth before any work begins.

### When an agent CLI is missing

Vibecrafted drives agent CLIs; it does not bundle them. If the one you asked for
is not installed, `init` names the gap and hands you the command that closes it
rather than exiting with a bare failure:

```
✗ claude CLI is not available.
  Install it, then re-run this command:
    npm install -g @anthropic-ai/claude-code
  Or check the whole fleet: vibecrafted doctor
```

The known install commands are:

| Agent    | Install                                            |
| -------- | -------------------------------------------------- |
| `claude` | `npm install -g @anthropic-ai/claude-code`         |
| `codex`  | `npm install -g @openai/codex`                     |
| `junie`  | `npm install -g @jetbrains/junie`                  |
| `grok`   | `npm install -g @xai-official/grok`                |
| `agy`    | install Google Antigravity CLI, then `agy install` |

If the CLI is staged in Vibecrafted's own agent bin but is not executable, the
error says so specifically and gives you the `chmod +x` line for that exact
path — a different problem gets a different answer.

Vibecrafted appends its own `tools/node/bin` to `PATH` (when that directory
exists) rather than prepending it, so a CLI you installed yourself always wins.
Agent CLIs are never bundled.

### Verify

```bash
vibecrafted doctor
vibecrafted version
```

`doctor` distinguishes what is broken from what is merely absent. On a plain
install, externally-managed foundations that you have not installed are reported
as warnings, not failures — a fresh install should not look alarming. Anything
`doctor` reports red is genuinely wrong.

### Start working

```bash
vibecrafted implement codex --prompt "Add user authentication with JWT"
vibecrafted help
```

---

## Update and rollback

```bash
vibecrafted update
```

Updates publish atomic runtime generations under
`~/.local/share/vibecrafted/tools/` and move a pointer. Rolling back means
moving the pointer to the previous generation — sessions already running keep
owning their live state.

For the desktop app, install a newer `Vibecrafted.app` from the new DMG. The
app binary can be replaced while session processes continue running. Opening
the new app does **not** upgrade the runtime: on launch the app adopts the
already-active runtime generation and publishes its bundled Runtime Pack only
when no runtime is installed at all (deliberate protection against a silent
downgrade). To raise the runtime after replacing the app, install the Runtime
Pack — the in-app "Repair Runtime…" action or
`Contents/Resources/runtime-pack/install-runtime-pack.sh` — and the app adopts
the new generation on its next launch. Roll back by replacing the app with the
prior notarized release; runtime rollback is the pointer move described above.

See [Update and rollback](public/getting-started/update.md) for the pointer
mechanics in detail.

### Runtime Pack publication retirement

Successful Runtime Pack publication automatically retires obsolete owned release
payloads under `releases/` and completed publication/rescue copies. It preserves
the current generation, live executable/dependency/session/service references,
pending recovery and one verified previous generation with its configuration
preimage. It never signals a process to make a generation disposable. The older
`tools/vibecrafted-generation-*` namespace has a separate owner.

Retirement requires receipt ownership, physical containment and closed content
identity; foreign children, pointers and identity drift produce exact residuals.
Missing historical release claims and mistakenly tracked zsh session files are
reconciled in the receipt; shell session/history bytes remain intact. Deletion
intents support retry after partial removal, and compact attribution receipts
remain in `.installer-backups/retirement/`. Original rescue receipts survive
after their completed snapshot payload is retired. Overwritten human edits stay
as individual files/pointers in the existing drift archive; retirement does not
retain an entire publication copy to preserve those edits.

Inspect or retry the existing publication owner's cleanup without rebuilding,
reinstalling or restarting the server:

```bash
make runtime-cleanup-plan
make runtime-cleanup
```

An isolated installation can be selected explicitly with
`RUNTIME_HOME=/absolute/path` on either target. The plan lists each generation
and copy as `retire`, `pinned` or `residual`, with its reasons. A pinned release
is still needed by a live process, provider configuration, pending worker or
rollback; cleanup never kills its owner. Settle that owner through its normal
lifecycle, then repeat the plan. A residual requires repair of its specific
ownership/content evidence, not manual deletion of a version-named directory.

Both targets call the existing installer owner directly:

```bash
python3 -B scripts/vetcoders_install.py runtime-repair --retire --plan --json
python3 -B scripts/vetcoders_install.py runtime-repair --retire --json
```

The plan is read-only. Apply rechecks pins and identity under the publication
lease and requires a verified current installation. A pending/failed publication
retains recovery; cleanup errors report residual/retry without rolling back a
successful publication. macOS uses stable process identity and open/mapped file
references; Linux uses open-file references or its process filesystem. Unsupported census platforms
retain payload and report the missing evidence rather than infer safety.

### Uninstall

```bash
make uninstall
```

---

## Troubleshooting

| Symptom                                            | Cause and fix                                                                                                                     |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `Unsupported platform: … Re-run inside WSL2.`      | Native Windows shell. Install WSL2 and use the `wsl bash -c …` form above.                                                        |
| `canonical runtime at … is incomplete; missing: …` | The staged runtime generation is partial. Re-run the installer; if it repeats, file an issue with the full path from the message. |
| `✗ <agent> CLI is not available.`                  | Expected on a fresh machine. Run the install command the error prints.                                                            |
| `locterm is macOS-only`                            | Pass `--runtime wezterm` or `--runtime microsandbox`.                                                                             |
| `microsandbox requires macOS HVF or Linux KVM`     | No hardware virtualization available. Use `--runtime wezterm`.                                                                    |
| `doctor` shows yellow on a fresh install           | Not an error. Absent optional foundations are warnings; install them or ignore.                                                   |
| Install log needed                                 | `~/.vibecrafted/install.log`                                                                                                      |

More: [Doctor](public/troubleshooting/doctor.md) ·
[Common issues](public/troubleshooting/common-issues.md) ·
[FAQ](FAQ.md)

---

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
