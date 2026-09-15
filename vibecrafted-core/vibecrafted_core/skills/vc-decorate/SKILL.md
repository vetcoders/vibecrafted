---
name: vc-decorate
version: 2.1.0
description: >
  Late-stage visual finishing and experience coherence skill. Detects the user's
  existing design language, audits system consistency, distinguishes identity
  from drift, upgrades weak patterns, and proposes tasteful polish that works
  WITHIN the user's system. Never imposes the agent's taste. Never decorates
  chaos. First make the system coherent. Then make it feel premium.
  Trigger phrases: "decorate", "make it look good", "add polish", "smaczki",
  "micro-interactions", "udekoruj", "dopracuj wizualnie", "curb appeal",
  "premium pass", "finish the experience", "make it feel intentional",
  "coherence audit", "design system cleanup", "interactive demo", "animate",
  "add hover effects", "make it feel nice", "visual polish".
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-decorate` (launcher `decorate`)**
>
> Same three-path _shape_ as the fleet, with **this** skill's literals — see the
> canonical [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                                                                    |
> | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | `vibecrafted decorate <agent>`                                                                                                            |
> | 2. Interactive          | `/vc-decorate` — execute **in this session**; use native subagents when required; do **not** externalize merely because a launcher exists |
> | 3. Agent-operator       | may dispatch the worker form above via `vc-dispatch` / operator lines while preserving this skill's identity                              |

> Freer native on some runs ≠ abandon external fleet. `vc-dispatch` and `vc-ship` keep their own identities.

<!-- /fleet-imperative -->

# vc-decorate — Coherence First. Premium Second.

## Human Operator Entry

### Living Tree / Worktree Rule

This workflow runs in the operator's current checkout and current branch. Do not create, switch to, or move execution
into a git worktree unless the operator explicitly asks for a worktree in this prompt. Generic words like "isolate", "
parallel", or "clean branch" are not enough. Re-read files before editing, adapt to concurrent changes, and report a
substrate failure if the current tree is too poisoned to continue safely. The one sanctioned second mode is a Fleet Worktree dispatch (written plan, pre-committed verifiers, disjoint domains, single-thread integrator — see Living Tree Rule, Mode B); outside that formation, stay in the shared tree.

See [Living Tree Rule](../LIVING_TREE_RULE.md).

## Canonical Orientation Gate

Before this workflow performs repo-specific analysis, planning, implementation, review, release, or delegation, it MUST
run or consume the `vc-init` procedure for the assigned repo. If fresh `vc-init` evidence is absent, perform the init
pass first and treat workflow-specific work as blocked until repo truth exists.

`Loctree:loctree` is the default structural perception skill for that pass. Use Loctree before grep or docs-driven
claims to produce or refresh the Code-Derived Application Map: repo-view, focus, slice, impact, find, and follow as
relevant. Search for existing symbols and contracts before creating new ones; run impact before delete or major
refactor; run slice before editing.

The point is to find the hooks: load-bearing hubs, twins, dead code, drift, runtime entrypoints, and blast-radius traps.
If the task is explicitly non-repo or no-code, state the no-repo exception in the report. Otherwise, missing `vc-init`
/Loctree evidence is a process failure.

Standard launcher: `vibecrafted start` / `vc-start`, then `vibecrafted decorate <agent>` / `vc-decorate` (see [Delegation Matrix](../DELEGATION_MATRIX.md)).

```bash
vibecrafted decorate agy --prompt 'Polish the landing page'
vc-decorate claude --prompt 'Coherence audit on the CLI output surface'
vibecrafted decorate codex --file /path/to/decorate-plan.md
```

> "Do not decorate chaos. First make the system coherent. Then make it feel premium."

Decorate is **not** a "make it pretty" skill. It is a **late-stage product
finishing** skill. Its job: take a working product and turn it into a
**coherent, intentional, premium experience**.

That means:

- Detect the user's real design language (colors, fonts, theme, spacing, interaction rhythm)
- Separate identity from drift
- Preserve what is distinctive
- Upgrade what is weak, dated, or inconsistent
- Verify the end-to-end feel
- **Only then** add tasteful visual polish and micro-interactions

Decorate does **not** impose the agent's taste, overwrite the user's brand, or
add random blur, glow, parallax, or "AI prettiness." Its job is to make the
existing system feel: more deliberate, more modern, more stable, more precise,
more complete.

**Premium is not ornament. Premium is coherence.**

---

## Repository Work Doctrine

For repository work, start with Loctree as the map: use `loct context`,
`loct occurrences`, `loct body`, and `loct find --literal` before broad manual
search. Use AICX for intent and session context. Use rg/grep as fallback or
local magnifier, not as a replacement for structural mapping. If Loctree fails
or misses a surface, append feedback to `~/.vibecrafted/loctree/loctree-fail.md`.

## Core Rule: Detect, Don't Dictate

Before decorating anything, run style detection and system audit:

```text
1. SCAN existing CSS variables, theme files, brand colors, fonts, spacing, components
2. IDENTIFY palette, font stack, theme mode, surface logic, interaction rhythm
3. AUDIT for visual drift, weak patterns, inconsistent states, prototype-feel leftovers
4. SEPARATE identity from drift:
   - preserve what is distinctive
   - improve what is weak, stale, or incoherent
   - remove the style diffusion, competing truths
   - eliminate the conflicting areas by hard prune duplicates or races
5. PROPOSE improvements using THEIR tokens, THEIR language, THEIR stack
6. ASK which changes should be applied
7. IMPLEMENT only approved changes
8. VERIFY the experience end-to-end
```

If no existing style detected, offer to scaffold a suitable design system —
present options, don't assume taste, don't force a visual identity.

---

## CLI Is Also an Interface

A terminal is not a dumping ground. CLI output is a UI. It deserves the same
coherence, rhythm, and intentionality as a web page. Nasty, raw, unformatted
terminal output is not "developer-friendly" — it is offensive to the operator.

Decorate applies to CLI surfaces too. If the product has a terminal interface, that interface is part of the product
surface. Decorate it. The obvious areas include:

- installer output (alignment, colors, progress signals)
- main runtime (branded, compact, informative)
- post-execution (spinners, progress bars, per step summaries)
- help/--help outputs (structured sections, colors, columns)
- doctor/health checks (clear pass/fail summaries, readable minimal logs)
- error messages (machine/human-readable separation)

If `screenscribe` is available, vc-decorate can consume a narrated UI
screencast to detect drift, awkward transitions, and coherence breaks across a
real flow — useful when static screenshots are too thin.

### Unicode Toolkit for CLI

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. ships a Unicode database (2601 chars, 13 categories) and a
`unicode-puzzles-mcp` server. Use them for CLI decoration instead of guessing
code points or hardcoding ANSI escape sequences.

**Pipeline: plain text → unicode transform → decorate_text**

1. Write the plain text content first
2. Transform labels/titles via `rewrite_using_unicode` (choose style)
3. Wrap final layout with `decorate_text` for box art (if needed)

Never hand-pick code points by memory. Use the MCP — it returns verified,
consistent characters from the same Unicode block.

**Available styles** (`rewrite_using_unicode`):

| Style          | Look     | Best for                        |
| -------------- | -------- | ------------------------------- |
| `squared`      | 🄵🅁🄰🄼🄴    | Branding stamps, footer badges  |
| `vaporwave`    | Ｖｉｂｅ | Spaced-out headers              |
| `monospace`    | 𝚟𝚒𝚋𝚎     | CLI subheaders, version strings |
| `smallCaps`    | Vɪʙᴇ     | Inline emphasis                 |
| `fraktur`      | 𝔙𝔦𝔟𝔢     | Decorative section titles       |
| `doubleStruck` | 𝕍𝕚𝕓𝕖     | Mathematical / formal labels    |
| `bubble`       | Ⓥⓘⓑⓔ     | Status badges, tags             |

**CLI decoration elements** (Unicode DB):

| Need       | Characters          | Source                   |
| ---------- | ------------------- | ------------------------ |
| Box frames | `╭─╮│╰─╯`           | Box Drawing              |
| Separators | `·` `─` `━` `┄`     | Box Drawing, Punctuation |
| Checkmarks | `✓` `✗` `⚠`         | Dingbats                 |
| Bullets    | `▸` `▪` `◆` `›`     | Geometric Shapes         |
| Progress   | `⣿⣶⣤⣀` `█▓▒░`       | Braille, Block Elements  |
| Sparklines | `⣀⣤⣶⣿` (8px/cell)   | Braille (256 combos)     |
| Arrows     | `→` `←` `↑` `↓` `⟶` | Arrows                   |
| Status     | `⚒` `⚡` `⚙` `⟳`    | Misc Symbols             |
| Brands     | `🄵·🅁·🄰·🄼·🄴·🅆·🄾·🅁·🄺` | Enclosed Alphanumerics   |

**Braille sparklines** deserve attention. A single Braille char encodes 8 dots
in a 2×4 grid (256 combos) — 40 chars = 320-point convergence curve in the
terminal, no graphics library. Use for: token usage over time · P0/P1/P2
findings across marbles loops · agent activity timelines · any trend data.

**Rules:**

- Zero ANSI escape codes for text styling — pure unicode renders everywhere.
- ANSI colors (`\033[32m` etc.) acceptable for status coloring only.
- Never mix Unicode blocks within one label (squared F next to negative squared R
  looks like a bug, not a choice — unless deliberately a signature mark like
  `🅵·🅁·🄰·🄼·🄴·🅆·🄾·🅁·🄺`).
- Test rendering on at least two terminals (macOS Terminal + Linux default).
- Use `search_unicode` to find a specific symbol — don't guess.

---

## When To Use

- Product works but feels flat, prototype-ish, unfinished
- User asks for visual polish, smaczki, curb appeal, premium feel
- UI is functionally correct but lacks coherence across surfaces
- Good ingredients, weak system feel
- Inconsistent cards, buttons, spacing, focus states, animation timings
- Showcase page, demo, landing page, or app needs a finishing pass
- Team wants the product to feel intentional, not just decorated
- **CLI output is functional but ugly, unbranded, hard to scan**

---

## Pipeline Position

```text
scaffold → init → workflow → followup → marbles → dou → [DECORATE] → hydrate → release
```

Decorate sits after `dou`, ensuring the now-complete product surface is
visually coherent before final packaging (`hydrate`) and shipping (`release`).

---

## Identity vs Drift

One of decorate's most important jobs:

- **Identity** — the user's actual visual language: chosen palette, typography,
  spacing rhythm, component forms, interaction style.
- **Drift** — things that merely accumulated: inconsistent border radii,
  mismatched spacing, conflicting button styles, random hover behaviors,
  prototype artifacts.

Preserve identity. Reduce drift.

---

## Implementation Pattern

```text
1. Detect  — scan tokens, stylesheets, framework config, component patterns
2. Audit   — identify identity vs drift and weak patterns
3. Propose — present coherence fixes, premium upgrades, smaczki
4. Implement — apply approved changes using user's tokens and structure
5. Verify  — review before/after for experience integrity
```

---

## Anti-Patterns

- Decorating a broken structure
- Keeping bad patterns because "the user already had them"
- Replacing their style with ours
- Adding motion without interaction purpose
- Adding blur/glow because "premium"

---

_Phase 3 — Ship (dou → decorate → hydrate → release)_

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
