# Quick Start

## 1. Install

**macOS and Linux:**

```bash
curl -fsSL https://vibecrafted.io/install.sh | bash
```

**Windows** — install WSL2 once, then use the same bootstrap inside it:

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
`Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg` from the
[latest release](https://github.com/vetcoders/vibecrafted/releases/latest),
verified against its adjacent `.dmg.sha256`. The build path is exercised and
produces a signed, notarized, stapled artifact; until the release carrying it is
published, use the bootstrap.

Power users can skip the DMG and App entirely. The adjacent
`Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz` is
the same signed binary runtime that onboarding installs from the App.

Everywhere else — Linux, WSL2, or macOS without the desktop app — take
`Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz` from the same release
instead. It pins one exact commit through a closed `source-provenance.json`,
which `curl | bash` cannot do:

```bash
shasum -a 256 -c Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz.sha256
tar -xzf Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz
bash vibecrafted-<version>/install.sh
```

Every channel and its status: [INSTALL.md](INSTALL.md).

## 2. Verify

```bash
vibecrafted doctor
vibecrafted version
```

`doctor` separates broken from merely absent. Yellow on a fresh install usually
means an optional foundation you have not installed yet — expected, not a
defect. Red means act.

## 3. Orient your agent

From any repository:

```bash
vibecrafted init claude
# or
vibecrafted init codex
```

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
install silently compiles. `make install-source` is the explicit maintainer
compiler lane. A developer checkout also exposes the
build, test and release targets.
Run `make help-dev` for the full inventory, or read
[Build from source](public/getting-started/build-from-source.md).
