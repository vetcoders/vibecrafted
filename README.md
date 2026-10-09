<p align="center">
  <img width="1536" height="677" alt="𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍." src="https://github.com/user-attachments/assets/4c238bf2-3087-472a-a420-1f68f717f5ad" />
</p>

<h1 align="center">Ship AI-built software without the vibe hangover</h1>

<p align="center">
  <em>The release engine for AI-built software.</em><br>
  <em>It mapped itself, fixed itself, packaged itself, and built its own distribution path.</em>
</p>

<p align="center">
  <a href="https://vibecrafted.io/">Website</a> ·
  <a href="docs/QUICK_START.md">Quick Start</a> ·
  <a href="docs/DOCUMENTATION_MAP.md">Docs Map</a> ·
  <a href="docs/DOCKER.md">Docker</a> ·
  <a href="docs/runtime/MANIFESTO_EN.md">Manifesto</a> ·
  <a href="docs/FAQ.md">FAQ</a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: BUSL-1.1" src="https://img.shields.io/badge/license-BUSL--1.1-blue.svg"></a>
  <a href="VERSION"><img alt="Version 4.3.3" src="https://img.shields.io/badge/version-4.3.3-informational.svg"></a>
  <a href="docs/INSTALL.md"><img alt="Platform: macOS, Linux, Windows native + WSL2" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows%20native%20%2B%20WSL2-lightgrey.svg"></a>
</p>

---

## The Weekend Hangover

**We are AI-native. AI generates code, but it doesn't deliver it.**

Most AI tools finish their job at the first draft. They leave you with a codebase that looks like it works, but falls apart when you try to ship it. You get hit with the [**Vibe Hangover**](docs/THE_VIBE_HANGOVER.md):

- **Auth held together with tape** that kills your enterprise deals during technical reviews.
- **God tables** with 35 columns that cause timeouts and massive serverless bills.
- **Silent failures** where a crashed Stripe webhook loses 8% of your revenue and you never get an alert.
- **Deploy and pray** strategies that take down the app on a Friday afternoon.

_(Read the full use case: [The 4 ways AI-coded MVPs break in production](docs/THE_VIBE_HANGOVER.md))_

---

## The Promise

**We ship AI-built software.**

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. is not another code generator. It is the release engine you run after AI has produced a repo and before a real user touches it. It forces that repo through perception, verification, convergence, install truth, packaging, and launch-readiness checks until a stranger can install it, trust it, and actually use it.

---

## The Vetcoders Axioms

1. **AI-Native, not AI-assisted:** We don't write the code. We craft the delivery.
2. **Perception over Memory:** The agent must see the structural truth now, not rely on stale summaries.
3. **Code Mapping over Green Quality Gates:** Passing tests on broken architecture is just a faster train on the wrong tracks.
4. **Intentions over RAG:** Retrieve _why_ we built it, not just a blind vector search of _how_.
5. **Move On over Backward Compatibility:** If the abstraction is rotting, cut it. Don't preserve garbage "just in case."

---

## The Hero Loop

**It's obvious AI will generate code. 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. asks: _what is still wrong?_**

The system finds the problems, fixes them, and repeats the loop until nothing important is left.

**1. The Draft:** You build an MVP using Cursor, Copilot, or Claude.
**2. The Finding:** Quality gates and structural maps locate the exact failures.
**3. The Fix:** The agent eliminates the counterexamples.
**4. The Close:** We run the loop. We do not call it done until the remaining
risks are named, verified, or deliberately handed off.

The public ship cycle today is:

```text
workflow -> implement -> marbles -> review -> dou -> release
```

The deeper lifecycle and read/write cadence live in
[docs/runtime/LIFECYCLE.md](docs/runtime/LIFECYCLE.md). The map that keeps the
docs aligned with the live command deck lives in
[docs/DOCUMENTATION_MAP.md](docs/DOCUMENTATION_MAP.md).

---

## The System Under The Hood

Behind this simple effect is an architecture built to orchestrate, map, and execute.
_(No longer guessing the architecture, but seeing it)._

| Layer               | How it works                                                                                                                                                                                        |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Seeing it All**   | The agent stops guessing architecture. It uses **Loctree** to see the entire project structure, dead code, and dependencies before it changes anything.                                             |
| **Convergence**     | `vc-marbles` runs the loop. It is not trying to "prove correctness." It only asks "what is still wrong?" and fixes it.                                                                              |
| **Multi-Agent**     | `vc-agents` lets you spin up Claude, Codex, and Gemini in parallel right in your terminal. Compare their results or have them tackle different architectural slices at the same time.               |
| **Agent Surface**   | iTerm2 OSC primitives + dynamic profiles (GA since v1.8.0) — colored mesh-host profiles, clickable OSC 8 hyperlinks, tab progress bars driven from any agent. See [docs/ITERM2.md](docs/ITERM2.md). |
| **The Final Check** | `vc-dou` (Definition of Undone) asks if it's shippable: Can you install it? Can someone trust it? Is there an onboarding page?                                                                      |

---

## The Operator Cockpit — vc-frame

The runtime ships with **vc-frame**, the terminal cockpit every launcher and
lifecycle runs inside. One window is one session with a fixed chrome:

<p align="center">
  <img alt="vc-frame anatomy — Start here map of the workspace" src="docs/assets/vc-frame-anatomy.png" width="900" />
</p>

- **TOP** — tabs of this session (`Start here` · `Shell` · one tab per worker run)
- **LEFT** — the sessions rail: other sessions and agent rooms, click to jump
- **CENTER** — the work surface (guide, shell, live worker streams)
- **BOTTOM** — status bar with modes (`Ctrl+t` TAB · `Ctrl+p` PANE · `Ctrl+o` SESSION)
  and the settlement counters **f / x / n** (Finalized · Failed · Needs-attention)
  fed straight from the control plane

Under load it looks like this — parallel agent rooms, a grok worker streaming a
review, monitors armed, and voice dictation feeding the prompt line:

<p align="center">
  <img alt="vc-frame live operator cockpit — multi-session, streaming workers" src="docs/assets/vc-frame-cockpit.png" width="900" />
</p>

Every tab is a first-class control-plane run: it has a `run_id`, a report, a
transcript, and a settlement verdict. Close the laptop, come back, resume —
the truth lives in artifacts, not in the terminal scrollback.

---

## Foundations & Where They Ship

The framework stands on product-managed foundations, each with its own public
distribution channel. **Acquisition is prebuilt-first**: npm / signed release
assets / crates.io / PyPI first, package manager second, `cargo build` only as a
preflighted last fallback. Full doctrine: [docs/FOUNDATION.md](docs/FOUNDATION.md).

| Foundation           | What it does                                        | Channel                                                                              |
| -------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------ |
| **Loctree** (`loct`) | Structural code perception — maps, impact, findings | [npm `@loctree/loctree`](https://www.npmjs.com/package/@loctree/loctree)             |
| **AICX** (`aicx`)    | Agent-session memory — catalog, search, intents     | [npm `@loctree/aicx`](https://www.npmjs.com/package/@loctree/aicx) · GitHub releases |
| **prview**           | PR review artifact generator                        | [GitHub Releases](https://github.com/vetcoders/prview-rs/releases/latest)            |
| **screenscribe**     | Screencast → structured engineering findings        | [PyPI](https://pypi.org/project/screenscribe/)                                       |
| **vc-frame**         | Operator cockpit (session rail, layouts)            | Embedded inside `Vibecrafted.app`; no separate installer or update channel           |

Foundations are external: the Runtime Pack does not carry `loct`, `aicx`,
`prview` or `screenscribe`. Each installs through its own channel (npm, GitHub
releases, PyPI — `scripts/install-foundations.sh` drives them), and
your own PATH install always wins. `vibecrafted doctor` verifies the ones it
finds and never silently replaces a product-managed binary with a stale copy.

---

## The Three Marks

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. has three typographic signatures — one for each layer of craft:

| Mark                      | Layer              | When to use                              |
| ------------------------- | ------------------ | ---------------------------------------- |
| `⚒🅅·🄸·🄱·🄴·🄲·🅡·🄰·🄵·🅃·🄴·🄳·` | **Produced with**  | Full product built through the framework |
| `𝓥𝓲𝓫𝓮𝓬𝓻𝓪𝓯𝓽𝓮𝓭`             | **Designed with**  | Design, UI, visual identity, brand work  |
| `//𝚟𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.`          | **Developed with** | Source code, engineering, infrastructure |

The `//` is not decoration. It is the mark.

---

## Install

Check the [latest GitHub Release](https://github.com/vetcoders/vibecrafted/releases/latest)
for the assets it actually carries. The latest public release is `v3.5.0` and
does not include the 4.x DMG, Runtime Packs, portable archive, or native Windows
installers. For the current public install path, use the bootstrap on macOS or
Linux, and WSL2 on Windows. Versioned carrier names below describe the 4.x
release contract and become usable when those assets are published:

- macOS desktop: `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg`
- macOS CLI: `Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz`
- Portable source: `Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz`
- Windows native: `Vibecrafted_<version>-<YYYYMMDD>-<sha8>-windows-x64.msi` or `.exe`, with the matching `Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-win32-x64.tar.gz`

Use the adjacent checksums and detached Runtime Pack signatures to verify downloads.

**macOS and Linux:**

```bash
curl -fsSL https://vibecrafted.io/install.sh | bash
```

The desktop app requires macOS 14+ on Apple Silicon (arm64); every other
system uses the bootstrap above or the portable tarball below.

**Windows (native):** build or download the win32-x64 Runtime Pack, then:

```powershell
powershell -NoProfile -File .\install.ps1 -Pack .\build\Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-win32-x64.tar.gz
```

The MSI/EXE siblings are unsigned Authenticode — SmartScreen will warn; trust
is `.sha256` + `.sig` provenance (same idea as the portable tarball not being
Apple-notarized). `install.ps1` updates User PATH or prints the exact
`%LOCALAPPDATA%\Vibecrafted\bin` directory to add. Then:

```powershell
vibecrafted doctor
vibecrafted init
```

**Windows (POSIX alternative):** install WSL2 once, then use the Linux
bootstrap inside it:

```powershell
wsl --install
wsl bash -c 'curl -fsSL https://vibecrafted.io/install.sh | bash'
```

Native Windows and WSL2 are both supported; they are not the same product
surface. PTY/zsh, flock/rescue, and voc stay on the WSL2/POSIX path.

**macOS CLI Runtime Pack** (power users who do not want the App): download the
signed binary carrier and both sidecars from the latest release, then point the
checkout front door at it:

```bash
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
make install RUNTIME_PACK=../Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz
make uninstall  # same installer, same receipt
```

An ordinary upgrade preserves local preferences and additional Frame themes
and layouts. Missing historical generation or backup bytes are recorded as
unavailable history; the installer captures the present state before replacing
the selected generation. A modified managed asset or an ambiguous preference
remains a visible conflict.

For an **existing signed archive with an older installer**, recovery can
explicitly select a reviewed installer from a trusted checkout. Obtain its SHA
from the reviewed commit or handoff receipt; do not guess it or modify the
archive:

```bash
make install RUNTIME_PACK=/absolute/path/RuntimePack.tar.gz \
  RUNTIME_PACK_BOOTSTRAP_INSTALLER=/absolute/trusted/checkout/scripts/vetcoders_install.py \
  RUNTIME_PACK_BOOTSTRAP_SHA256=<reviewed-installer-sha256>
```

The wrapper verifies the signed payload first, reads and executes only the
hash-bound installer bytes, and reports the installer path, SHA and signed
archive SHA alongside the selected runtime root. This is an explicit recovery
operation. The App uses its delivered installer and does not search checkouts.
A bootstrap SHA identifies that installer file; its trusted checkout still
owns relative dependencies. Future packs include the updated installer.

If a browser renamed the archive or sidecars, pass their exact paths using
`RUNTIME_PACK_CHECKSUM` and `RUNTIME_PACK_SIGNATURE`, plus
`RUNTIME_PACK_CARRIER_BASENAME` containing the original release archive name.
The explicit name must agree with the signed payload provenance. These are
also available as wrapper flags `--checksum`, `--signature`, and
`--carrier-basename`. Neither checksum nor signature validation is disabled.
For a read-only recovery plan, invoke the same wrapper and add
`--rescue --plan`; apply the reported plan with `--rescue --apply
--plan-digest <digest>` using the same archive and bootstrap identity.

From a committed source checkout, build and install the selected generation:

```bash
make install
make help-dev   # the full target surface
```

`make install` builds this checkout into a closed Runtime Pack, then installs
that exact carrier. On macOS, Frame and Terminal come from the full revisions
in `config/source-components.json`; available checkout objects are reused
without switching branches or reading dirty files. Missing objects are fetched
into temporary repositories. An explicit `RUNTIME_PACK=/absolute/path` selects
a previously built, signed carrier without compiling it.

A Runtime Pack install gives you the headless Vibecrafted runtime — `vibecrafted
doctor`, every skill launcher, `observe`/`await`, reports and transcripts under
`~/.vibecrafted`. Foundations (`loct`, `aicx`, `prview`, `screenscribe`) and agent
CLIs are not part of the pack; install them through their own channels. The
Runtime Pack includes `vc-frame`, `vc-terminal` and `vc-start`; the desktop app
provides their native host as well. First run after install:

```bash
export PATH="$HOME/.local/bin:$PATH"
vibecrafted doctor
vibecrafted implement claude --prompt "describe this repo"
vibecrafted await claude --last      # waits, then prints the report path
vibecrafted status                   # today's runs
```

**macOS desktop app:** the intended end-user shape is one Developer ID signed
and notarized `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg` carrying matching
builds of `vc-terminal`, `vc-frame`, `vc-start` and the complete runtime.
The DMG is an artifact of releases from 4.3.1 on. Check what a release
actually carries (`gh release view --json assets -q '.assets[].name'`); if it
lists a DMG, download it and its adjacent `.dmg.sha256`, verify the checksum,
then open the DMG. The latest published release may not carry one yet — until
a release with the DMG is published, use the bootstrap above.

The same release also carries
`Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz`, its
`.sha256`, and detached `.sig`. It contains the exact runtime embedded in the
App plus the same terminal/frame helpers; the DMG is an optional onboarding
overlay, not a second runtime authority.

**Every other system** (Linux, WSL2, or macOS without the desktop app): the
same release carries `Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz`
and its adjacent `.sha256`. It is not a convenience copy of the repository — it
is an allowlisted projection of one exact commit, carrying a closed
`source-provenance.json` whose distribution-tree digest names that commit. The
installer refuses a payload whose provenance does not close:

```bash
tar -xzf Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz
bash vibecrafted-<version>/install.sh
```

Unlike `curl | bash`, that pins you to a version instead of to whatever a
branch happens to hold today.

Every new or restored `workspace_id` enters through the bundled `vc-start`.
Vibecrafted sources its own XDG/runtime environment and does not overwrite your
Alacritty, Zellij, vc-frame or shell configuration. `vc-terminal` and `vc-frame`
are internal donors, not additional products to install.

When a browser-guided install is the better human surface, run `make wizard` or
`make gui-install`.

Verify the installed product:

```bash
vibecrafted doctor
```

Full matrix, per-platform detail and troubleshooting: [docs/INSTALL.md](docs/INSTALL.md).

Prefer a containerized operator runtime when you want the framework isolated
from the host toolchain:

```bash
docker build -t vetcoders/vibecrafted:local .
docker run --rm -it -v "$PWD:/workspace" vetcoders/vibecrafted:local version
```

See [Docker Runtime](docs/DOCKER.md).

---

## Quick Start

```bash
cd /path/to/your-project   # any git repository
vibecrafted init claude
vibecrafted implement codex --prompt "Add JWT authentication"
```

`vibecrafted justdo` / `vc-justdo` is a **standalone** Just Do posture launcher
(task type from the prompt; not a ship stage). Use `implement` for the VC-ship
WRITE stage. They are not aliases (ADR-0001).

Type `vibecrafted help` for the command deck, or `vc-` and hit tab once the shell helpers are installed.

When you want to walk the release surface explicitly, run:

```bash
vibecrafted dou claude --prompt "Audit launch readiness"
vibecrafted decorate codex --prompt "Polish the release surface"
vibecrafted hydrate codex --prompt "Package the product"
vibecrafted release codex --prompt "Prepare release steps"
```

`vibecrafted release` enforces a four-section release report — Semgrep
security gate (`make semgrep`), exposed surface inventory, deployment
mode decision, and post-release install smoke from the **published**
artifact. The doctrine lives in
[`vibecrafted-core/vibecrafted_core/skills/vc-release/SKILL.md`](vibecrafted-core/vibecrafted_core/skills/vc-release/SKILL.md) and the
default template lives in
[`vibecrafted-core/vibecrafted_core/skills/vc-release/references/release-report-template.md`](vibecrafted-core/vibecrafted_core/skills/vc-release/references/release-report-template.md).

---

## For Founders

Free for personal use and for startups. No limits on repos or agents.

For enterprise: **info@vibecrafted.io**

---

<p align="center">
  <em>Move fast, but with taste.</em><br>
  <em>Finish the whole thing, not just the code.</em>
</p>

<p align="center">
  <code>//𝚟𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.</code>
</p>

<p align="center">
  <sub>(c)2024-2026 Vetcoders · <a href="https://vibecrafted.io">vibecrafted.io</a></sub>
</p>
