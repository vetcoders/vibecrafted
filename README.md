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
  <a href="VERSION"><img alt="Version 3.7.1" src="https://img.shields.io/badge/version-3.7.1-informational.svg"></a>
  <a href="docs/INSTALL.md"><img alt="Platform: macOS, Linux, Windows (WSL2)" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows%20(WSL2)-lightgrey.svg"></a>
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
| **Loctree** (`loct`) | Structural code perception — maps, impact, findings | `0.14.x` · [npm](https://www.npmjs.com/package/loctree) · GitHub releases            |
| **AICX** (`aicx`)    | Agent-session memory — catalog, search, intents     | [npm `@loctree/aicx`](https://www.npmjs.com/package/@loctree/aicx) · GitHub releases |
| **prview**           | PR review artifact generator                        | [crates.io](https://crates.io/crates/prview)                                         |
| **screenscribe**     | Screencast → structured engineering findings        | [PyPI](https://pypi.org/project/screenscribe/)                                       |
| **vc-frame**         | Operator cockpit (session rail, layouts)            | Embedded inside `Vibecrafted.app`; no separate installer or update channel           |

The installer verifies these foundations on every run (`vibecrafted doctor`)
and never silently replaces a product-managed binary with a stale copy.

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

**macOS and Linux:**

```bash
curl -fsSL https://vibecrafted.io/install.sh | bash
```

**Windows:** install WSL2 once, then use the same bootstrap inside it:

```powershell
wsl --install
wsl bash -c 'curl -fsSL https://vibecrafted.io/install.sh | bash'
```

**macOS CLI Runtime Pack** (power users who do not want the App): download the
signed binary carrier and both sidecars from the latest release, then point the
checkout front door at it:

```bash
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
make install RUNTIME_PACK=../Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz
make uninstall  # same installer, same receipt
```

Maintainers who intentionally want local compilation use the explicit source
lane:

```bash
make install-source
make help-dev   # the full target surface
```

`make install` never compiles a product for a stranger. It selects a closed
Runtime Pack for the current platform and architecture; Linux and WSL2 use the
Linux x86_64 or arm64 carrier. `make install-source` is the explicit maintainer
lane and may require the full build toolchain.

A Runtime Pack install gives you the complete headless runtime — `vibecrafted
doctor`, every skill launcher, `observe`/`await`, reports and transcripts under
`~/.vibecrafted`. The visual cockpit (`vc-frame`, `vc-start`) is not part of it:
it ships inside the desktop app below, and `vibecrafted init <agent>` falls back
to your current terminal until it is present. First run after install:

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
Download it and its adjacent `.dmg.sha256` from the
[latest release](https://github.com/vetcoders/vibecrafted/releases/latest),
verify the checksum, then open the DMG. The build path (`make release`) is
exercised and produces a Developer ID signed, notarized and stapled artifact;
until the release carrying it is published, use the bootstrap.

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
cd $VIBECRAFTED_ROOT/your-project
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
[`skills/vc-release/SKILL.md`](skills/vc-release/SKILL.md) and the
default template lives in
[`skills/vc-release/references/release-report-template.md`](skills/vc-release/references/release-report-template.md).

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
