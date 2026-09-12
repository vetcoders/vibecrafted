# Install Vibecrafted

Vibecrafted runs on macOS, Linux and Windows-through-WSL2. The channels differ
in what they give you and in how finished they are, so this page states both.

## Channel matrix

| Channel                      | Platform             | What you get                                                        | Status                                   |
| ---------------------------- | -------------------- | ------------------------------------------------------------------- | ---------------------------------------- |
| Signed `Vibecrafted.app` DMG | macOS 14+, arm64     | Full desktop product: terminal, frame, runtime, server              | Build path complete; publication pending |
| Portable tarball             | Linux, WSL2, macOS   | Command deck, runtime, control plane, skills — pinned to one commit | Build path complete; publication pending |
| Bootstrap `install.sh`       | macOS, Linux, WSL2   | Command deck, runtime, control plane, skills                        | Published; CI-gated                      |
| Source checkout              | macOS, Linux, WSL2   | Everything above plus build, test and release targets               | Published                                |
| Container                    | anywhere Docker runs | Isolated operator runtime                                           | Published                                |
| `install.ps1`                | Windows              | WSL2 detection and handoff — not a native install                   | In repo; not yet served over HTTP        |

If you want one sentence: **on macOS and Linux use the bootstrap today; on
Windows install WSL2 first and then use the same bootstrap.**

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

> **Current status.** The build path is exercised end to end
> (`make release` → codesign → notarytool → `make publish-release`) and its
> shape is gated by contract tests; 4.1.0 produces a signed, notarized and
> stapled DMG with a signed `release-output.json`. No _published_ release
> carries it yet — until the v4.1.0 release goes out, use a channel below.
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
bash vibecrafted-<version>/install.sh
```

Why this exists next to the bootstrap: `curl | bash` pins you to whatever a
branch holds at the moment you run it. The tarball pins you to one commit and
proves it. Its `source-provenance.json` carries a
`vibecrafted.distribution-tree.v1` digest over every packed entry, bound to that
commit, and `install.sh` re-validates the carrier before staging anything —
which is also why `--archive-url` and `--archive-file` refuse an archive that
does not carry one.

Scope, stated plainly:

- It is a **source distribution**, not a prebuilt-binary bundle. `voc`,
  `vc-admin` and `vc-server` are compiled locally by `make install`, so the
  Rust toolchain listed under the Linux prerequisites is still required.
- Prebuilt per-architecture binaries are not part of this channel and are not
  claimed to be.
- On Windows this is what you install _inside_ WSL2. There is no native
  Windows build; see the `install.ps1` section.

Maintainers build it with `make portable`. It needs no signing identity and no
notary account — only `git` and `python3` — so it builds on Linux too.

> **Current status.** Build path complete and self-verifying (the builder
> unpacks and re-validates what it just wrote before the bytes may leave the
> machine). Publication lands with the v4.1.0 release.

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

Linux is a first-class runtime, not a side effect. The install path is gated in
CI on every push and pull request by `.github/workflows/install-linux.yml`,
which runs two deliberately different jobs:

- **Ubuntu on the GitHub-hosted runner** — exercises real `/etc/os-release`
  detection and the apt-family prerequisite hints.
- **`debian:bookworm-slim` in a container** — exercises the bare-minimum case
  with no pre-baked tooling, which is the failure mode a real Debian user hits.

Both jobs assert that `vibecrafted doctor` reports green afterwards. In headless
CI, externally-managed foundations (loctree, aicx, vc-frame) that are absent are
reported as warnings rather than failures, so a green doctor on a minimal box is
a real signal and not a relaxed one.

The macOS-only pieces are the desktop app, notarization, and the `locterm`
runtime. Everything else — command deck, control plane, dispatch, skills,
settlement ledger — runs on Linux.

---

## Windows — WSL2

Vibecrafted has no native Windows build. The installer is POSIX shell, and the
runtime assumes a POSIX process model. On Windows you install WSL2 once and then
use the ordinary Linux path inside it.

### 1. Install WSL2

From an elevated PowerShell prompt:

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

The repository ships `install.ps1` as an honest Windows entry point. It is not a
native installer and does not pretend to be one. It:

1. requires PowerShell 5.1 or newer,
2. probes whether WSL is installed and healthy (`wsl --status`),
3. if WSL is available, prints the exact one-liner to bootstrap inside your
   default distro,
4. if WSL is missing, prints the canonical WSL2 install path and **exits
   non-zero** so no caller mistakes the outcome for success.

It never silently succeeds. Either it tells you exactly what to run next, or it
tells you what is missing.

Run it from a checkout:

```powershell
.\install.ps1
```

> **Current status.** `install.ps1` is not yet served from
> `https://vibecrafted.io/install.ps1`, so the `iwr -useb … | iex` form in its
> own header does not work yet. Use the checkout form above, or run the `wsl`
> one-liner directly. Native Windows binaries are not on the near roadmap;
> WSL2 is the supported answer.

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

A source checkout is the complete surface: it installs the same runtime the
bootstrap installs, and it additionally carries every build, test and release
target. This is the path to take if you want to modify Vibecrafted, run the
gates, or produce your own signed artifact.

### Prerequisites

| Tool            | Why                                                       |
| --------------- | --------------------------------------------------------- |
| `git`           | checkout                                                  |
| `bash` 4+       | installer and command deck                                |
| `uv`            | Python toolchain and the pinned `vibecrafted` tool env    |
| Rust toolchain  | `voc`, `vc-admin`, `vc-server`, `vc-terminal`, `vc-frame` |
| `make`          | target surface                                            |
| Xcode CLI tools | macOS only — codesign, notarytool                         |

### Checkout and install

```bash
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
make install
```

`make install` runs the guided install. `make install-auto` runs it
non-interactively — this is what CI uses. `make install-all` additionally builds
the Rust binaries (`voc`, `vc-admin`, `vc-server`) as real files into
`~/.local/bin`.

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

Vibecrafted appends its bundled agent bin to `PATH` rather than prepending it,
so a CLI you installed yourself always wins over the bundled copy.

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
app binary can be replaced while session processes continue running; restored
workspaces re-enter through the new bundled `vc-start`. Roll back by replacing
the app with the prior notarized release.

See [Update and rollback](public/getting-started/update.md) for the pointer
mechanics in detail.

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
