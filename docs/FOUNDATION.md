# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Foundation

Foundation packages are infrastructure that all skills depend on.
They are not skills — they are the senses and memory of the agent layer.

## The Stack

```
 Skills (vc-workflow, vc-marbles, vc-followup, ...)
    |           |           |            |
    v           v           v            v
 Loctree     AICX      PRView     Screenscribe
  (eyes)    (intentions)   (review)     (ears)
    |           |           |            |
    +--------- Foundation Layer ---------+
```

## Loctree — Eyes

**What it does**: Structural code intelligence. Maps files, imports,
dependencies, hubs, dead code, and blast radius.

**Why it matters**: Without loctree, agents guess based on filenames.
With it, they see the dependency graph before touching anything.

**Tools**: `loct` CLI, `loctree-mcp` server

**Used by**: Every skill that reads or modifies code. `vc-init` runs
`repo-view` as its first action. `vc-workflow` runs `slice` before editing.
`vc-followup` runs `impact` before deleting.

**Install**: `make foundations` or the current
[Loctree/Loctree release](https://github.com/Loctree/Loctree/releases). Do not
copy a pinned version from this page; `make doctor` and the installer are the
runtime truth.

## AICX — intentions

**What it does**: Deterministic decision retrieval. Stores and indexes
prior agent sessions, decisions, and context chunks by project and time.

**Why it matters**: Agents are ephemeral. AICX gives them access to what
happened in previous sessions without relying on fuzzy recall or
multi-gigabyte context windows.

**Tools**: `aicx` CLI, `aicx-mcp` server

**Used by**: `vc-init` (history baseline), `vc-followup` (prior decisions),
`vc-research` (what was already researched), `vc-partner` (session context).

**Install**: `make foundations` (binary or cargo fallback) or
[Loctree/aicx releases](https://github.com/Loctree/aicx/releases)

## PRView — Review

**What it does**: Generates structured review artifacts from code changes.
Produces findings as JSON/markdown that other agents can consume.

**Why it matters**: Terminal output is lost. PRView creates persistent
reports that feed into followup agents and convergence loops.

**Tools**: `prview` CLI

**Used by**: `vc-review`, `vc-followup`, `vc-marbles` (as review gate).

**Install**: Binary from
[vetcoders/prview releases](https://github.com/vetcoders/prview/releases)

## Screenscribe — Ears

**What it does**: Turns screen recordings with narration into structured
engineering findings. Bridges the gap between "it's broken" shown on
screen and a formal bug report.

**Why it matters**: Some bugs are easier to show than to type. Screenscribe
converts narrated demos into actionable input for agent workflows.

**Tools**: `screenscribe` CLI

**Used by**: `vc-decorate` (visual verification), `vc-followup` (UI audit),
`vc-dou` (product surface check).

**Install**: use the current Screenscribe release or source install path checked
by `make doctor`. Treat this document as role guidance, not a package registry.

## Foundation acquisition order (prebuilt-first)

Operator mandate: strangers and CI must get working foundations **without
building from source by default**. Source builds remain available; they are
not the first path.

**Acquisition order (every foundation binary):**

1. **Prebuilt product channel (preferred)**
   - **aicx** — npm `@loctree/aicx` and signed GitHub release assets
   - **loctree / loct** — npm `loctree` and signed GitHub release assets
   - **prview** — crates.io published crates / release binaries
   - **screenscribe** — PyPI `screenscribe`
   - **vc-frame** — deterministic donor binary embedded in `Vibecrafted.app`;
     never a separately installed release product
2. **Package manager** — brew / apt / dnf / pacman when the product publishes
   an official formula/package for that host (never a random third-party fork
   as the default).
3. **`cargo build` / source install — last fallback only**
   - Run a **preflight** first: Rust toolchain present, supported target triple,
     disk/network budget, and an explicit operator or CI opt-in
     (`INSTALL_RUST=true` or equivalent).
   - Fail closed with a clear message if preflight fails — do not silently
     compile for ten minutes on a laptop that only needed `npm i -g`.
   - Never replace a product-managed prebuilt with a stale local `target/release`
     copy without an explicit force path.

| Foundation     | Prebuilt channel                               | Fallback                          |
| -------------- | ---------------------------------------------- | --------------------------------- |
| loctree / loct | npm + GitHub releases                          | cargo (preflighted)               |
| aicx           | npm `@loctree/aicx` + GitHub releases          | cargo (preflighted)               |
| prview         | crates.io / release binaries                   | cargo (preflighted)               |
| screenscribe   | PyPI                                           | source install with doctor check  |
| vc-frame       | embedded in the single canonical versioned DMG | local donor build for integrators |

## Foundation in the Installer

`make doctor` checks foundation binaries and reports their status.
`loctree` and `aicx` are required foundations. Skills may still be readable
without them, but the framework is not operating in its intended mode: it has
no structural perception and no durable intention recovery.

`prview` and Screenscribe are evidence layers. They are strongly recommended
for review and runtime proof, but they do not replace the required foundation
pair: `loctree-mcp` plus `aicx-mcp`.

The recommended install order:

1. `Vibecrafted.app` from the signed and notarized canonical versioned DMG
2. Foundation binaries via **prebuilt-first** (`make foundations` — prefers
   release/npm/crates/PyPI paths; cargo only after preflight)
3. Agent CLIs (claude, codex, agy, junie, grok, cursor)
4. PRView (recommended for review workflows)
5. Screenscribe (recommended for visual verification)

## Foundation vs Skills

|                | Foundation            | Skills                                   |
| -------------- | --------------------- | ---------------------------------------- |
| **What**       | Infrastructure binary | Instruction set (SKILL.md)               |
| **Where**      | System PATH           | `$VIBECRAFTED_ROOT/.vibecrafted/skills/` |
| **Updates**    | Binary releases       | `make install` or `skills-sync`          |
| **Without it** | Runtime truth is weak | Agent doesn't know the workflow          |
| **Example**    | loctree-mcp           | vc-workflow                              |

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. by Vetcoders_
