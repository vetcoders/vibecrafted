---
name: vc-dou
version: 2.2.0
description: >
  Definition of Undone audit skill. Runs a systematic gap analysis across the
  ENTIRE product surface — not just code. Crawls public URLs, audits repo
  governance, verifies install paths, checks SEO/discoverability, audits
  representation surfaces for non-web products, and measures the gap between
  internal capability and external visibility.
  Trigger phrases: "definition of undone", "dou audit", "co jest niedokończone",
  "what's undone", "product surface audit", "completion audit", "plague check",
  "hydration check", "are we shippable", "czy jesteśmy gotowi", "gap analysis",
  "co brakuje do launchu", "readiness audit", "packaging gap".
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-dou` (launcher `dou`)**
>
> Same three-path _shape_ as the fleet, with **this** skill's literals — see the
> canonical [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                                                               |
> | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
> | 1. User-launched worker | `vibecrafted dou <agent>`                                                                                                            |
> | 2. Interactive          | `/vc-dou` — execute **in this session**; use native subagents when required; do **not** externalize merely because a launcher exists |
> | 3. Agent-operator       | may dispatch the worker form above via `vc-dispatch` / operator lines while preserving this skill's identity                         |

> Freer native on some runs ≠ abandon external fleet. `vc-dispatch` and `vc-ship` keep their own identities.

<!-- /fleet-imperative -->

# `vc-dou` — AUDIT-FIRST Definition of Undone

> AUDIT-FIRST tool-driven shipping-readiness analysis. Where `vc-followup` asks
> **"is the trajectory healthy?"** and `vc-audit` asks **"did the spec
> land?"**, DoU asks **"how far are we from someone being able to find,
> trust, try, and buy this?"** — across code, governance, install,
> SEO, distribution, and representation surfaces. Produces a gap
> report and a remediation list. Never modifies code or surfaces.

## Pipeline Position

`vc-dou` sits in the **shipping-readiness perception** slot:

```
... → polarize (WRITE: cut) → [DOU: AUDIT-FIRST] → hydrate (WRITE) → decorate (WRITE) → release (WRITE) → ...
```

DoU is AUDIT-FIRST: it produces a gap inventory and remediation list
that downstream WRITE steps (`vc-hydrate`, `vc-decorate`, `vc-release`)
consume. Fixing the gaps belongs to those downstream skills, not to
DoU itself.

## Public-surface floor

Before the prose audit, run the executable floor. It compares VERSION, git
tags, GitHub Releases, npm, crates.io, PyPI, Homebrew, the installer channel,
README install sections, site metadata, and brand spelling. A red floor is a
red audit. Do not report the product as shippable, and do not recommend an
install call to action, when the report says `install_cta: no` or `floor: FAIL`.

```bash
vc-dou --floor
vc-dou --floor --product loctree
vc-dou --floor --product aicx
```

Copy the report into the DoU findings. Warnings are part of the result; an
`unproven` line is not a pass. Contract and per-product config:
[`references/public-surface-floor.md`](references/public-surface-floor.md).

## Operator Entry

### Living Tree / Worktree Rule

This workflow runs in the operator's current checkout and current branch. Do not create, switch to, or move execution into a git worktree unless the operator explicitly asks for a worktree in this prompt. Generic words like "isolate", "parallel", or "clean branch" are not enough. The one sanctioned second mode is a Fleet Worktree dispatch (written plan, pre-committed verifiers, disjoint domains, single-thread integrator — see Living Tree Rule, Mode B); outside that formation, stay in the shared tree. Re-read files before editing, adapt to concurrent changes, and report a substrate failure if the current tree is too poisoned to continue safely.

See [Living Tree Rule](../LIVING_TREE_RULE.md).

## Canonical Orientation Gate

Before this workflow performs repo-specific analysis, planning, implementation, review, release, or delegation, it MUST run or consume the `vc-init` procedure for the assigned repo. If fresh `vc-init` evidence is absent, perform the init pass first and treat workflow-specific work as blocked until repo truth exists.

`Loctree:loctree` is the default structural perception skill for that pass. Use Loctree before grep or docs-driven claims to produce or refresh the Code-Derived Application Map: repo-view, focus, slice, impact, find, and follow as relevant. Search for existing symbols and contracts before creating new ones; run impact before delete or major refactor; run slice before editing.

The point is to find the hooks: load-bearing hubs, twins, dead code, drift, runtime entrypoints, and blast-radius traps. If the task is explicitly non-repo or no-code, state the no-repo exception in the report. Otherwise, missing `vc-init`/Loctree evidence is a process failure.

Standard launcher: `vibecrafted dou <agent>` / `vc-dou` (see
[Delegation Matrix](../DELEGATION_MATRIX.md)). The worker defaults to detached
headless execution; `vibecrafted start` / `vc-start` is optional observation
and interactive User Session setup, not a process host requirement.

```bash
vibecrafted dou claude --prompt 'Audit launch readiness'
vc-dou codex --prompt 'Full product surface audit for loctree'
vibecrafted dou gemini --file /path/to/previous-dou-report.md
```

Foundation deps (loaded with framework): `vc-loctree`, `vc-aicx`.

> "Audit skills are dead. Work is taking initiative, not just pointing out flaws."
> "The engineering is done. The packaging is not."

DoU answers the question no agent asks by default:
**"What remains incomplete across the entire product surface, and how do we fix it right now?"**

This is the completion engine. Not a passive checklist generator — an **active**
engine that measures the gap between "it runs on my machine" and "someone can
buy this", then immediately starts patching gaps: missing CI scripts, missing
representation layer, missing docs.

**Critical rule:** A product does not need to be a web app to need a public face.
Desktop apps, CLI tools, agents, MCP servers, internal runtimes — all need a
representation surface (landing page, showcase, one-pager, explainer, screenshots).
If a product can only be understood by opening the repo or talking to its
creators, that is Definition of Undone.

## Repository Work Doctrine

For repository work, start with Loctree as the map: use `loct context`,
`loct occurrences`, `loct body`, and `loct find --literal` before broad manual
search. Use AICX for intent and session context. Use rg/grep as fallback or
local magnifier, not as a replacement for structural mapping. If Loctree fails
or misses a surface, append feedback to `~/.vibecrafted/loctree/loctree-fail.md`.

## Pipeline Position

```
scaffold → init → workflow → followup → marbles → [DOU] → decorate → hydrate → release
```

## When To Use

- Before any launch, marketplace submission, or PR announcement
- After major implementation cycles (post `vc-followup`)
- When the team asks "are we ready?" / "co jeszcze brakuje?"
- Periodic health check (every ~2 weeks)
- When the feeling of progress exceeds the reality of completion

If `screenscribe` is available, vc-dou can consume a screencast of the install
path or first-run experience as audit evidence.

## The Undone Matrix

| Axis                      | Question                            | Tools                        |
| ------------------------- | ----------------------------------- | ---------------------------- |
| Repo Health               | Does the code work?                 | loctree, cargo/npm, CI       |
| Presence / Representation | Can someone find and understand it? | WebFetch, brave-search, curl |
| Commercial Readiness      | Can someone adopt or buy it?        | Manual checklist + probes    |

Scoring: `[OK]` ready · `[PARTIAL]` exists but incomplete · `[MISSING]` absent.

## Audit Sequence

### Phase 1 — Repo Governance

Required files: `LICENSE`, `README.md` (install/usage/contributing), `CONTRIBUTING.md`,
`CHANGELOG.md`, `.github/workflows/`, `.github/ISSUE_TEMPLATE/`, `SECURITY.md`.

```bash
for f in LICENSE README.md CONTRIBUTING.md CHANGELOG.md SECURITY.md; do
  [ -f "$ROOT/$f" ] && echo "[PASS] $f" || echo "[FAIL] $f MISSING"
done
[ -d "$ROOT/.github/workflows" ] && echo "[PASS] CI" || echo "[FAIL] No CI"
```

Loctree structural check via `repo-view(project)`: dead exports (0 for release),
cycles (0 or documented), health score.

### Phase 2 — Install Path

The "can a stranger use this" test starts with `vc-dou --floor` (see
[the public-surface floor](references/public-surface-floor.md)). The checks
below are the rest of the path, not a substitute for that probe.

- **CLI:** published to registry, version badge matches, `cargo install`/`npm i -g`/`pip install` works,
  binary runs without dev toolchain, `--help` and `--version` work.
- **Desktop:** DMG/MSI/AppImage available, Homebrew formula or equivalent, signed/notarized (macOS).
- **Web:** URL accessible, loads <3s, mobile responsive, graceful no-JS fallback.

### Phase 3 — Presence and Discoverability

For each public URL:

```
1. WebFetch(url, "title, meta description, h1, content summary, CTAs, pricing.
   Report if page appears empty or JS-only.")
2. SSR check: curl -s <url> | grep -c '<h1\|<p\|<main' (< 3 → invisible to crawlers)
3. Security headers: curl -sI <url> | grep -i 'strict-transport\|x-frame\|content-security'
4. OG/Twitter: curl -s <url> | grep -i 'og:\|twitter:card'
```

SEO basics: descriptive title (not "React App"), meta description, H1, no-JS
content, robots.txt, sitemap.xml, no duplicate content across domains.

Search presence:

```
brave-search("<product>")            # appears in top 20?
brave-search("<product> <category>") # category ranking
brave-search("site:<domain>")        # indexed page count
```

**No public web app?** Run a Representation Surface Audit instead:
landing/showcase/one-pager · 30-second explainer · screenshots/diagrams/demos ·
visible install path · narrative not requiring repo access · stranger-comprehensible
without founders. All "no" → `[MISSING]` Presence.

**On `[MISSING]` Presence — generate `./presence/`** (do not just mark and move on):

- `index.html` — name, one-liner, what it does, install command, 3-5 features,
  links (GitHub, docs, registry).
- `styles.css` — dark theme, monospace chrome, clean typography, palette from scaffold or neutral.
- `app.js` — copy button, smooth scroll, fade-up observer. Nothing more.

Rules: minimal but not poor · no frameworks · GitHub Pages deployable · meta tags
(OG/Twitter), favicon, robots.txt, sitemap.xml · no animations beyond fade-up,
no particles, no glow. DoU creates these when nothing exists; decorate polishes;
hydrate packages.

### Phase 4 — Commercial Surface

```
Discovery → Landing → Understanding → Trial → Adoption → Payment
```

Verify each stage. Missing stages = funnel holes. For non-web products, "Landing"
means a landing page, showcase, docs explainer, or explicit representation layer.
No such layer = funnel broken at Landing/Understanding even if the product works.

### Phase 5 — Marketplace Readiness

**Claude Skills Marketplace:** SKILL.md frontmatter (name, version, description) ·
trigger phrases (EN+PL) · `references/` · no hardcoded paths · graceful
optional-dep fallback · clean-install tested.

**GitHub / crates.io / npm:** package metadata complete (description, keywords,
homepage, repo) · categories/tags · screenshots or demo GIF in README ·
license compatible.

## Output Format

```markdown
# Definition of Undone: <project/ecosystem>

Date: <YYYY-MM-DD>
Auditor: <agent>

## Executive Summary

<2-3 sentences: gap between code and market>

## Undone Matrix

| Project | Repo | Web | Commercial | Critical Gap |
| ------- | ---- | --- | ---------- | ------------ |

## Findings by Severity

### P0 — Ship Blockers

### P1 — Credibility Gaps

### P2 — Polish

## The Funnel Test

Discovery → Landing → Understanding → Trial → Adoption → Payment
<for each product, mark where the funnel breaks>

## Hydration Priorities

<ordered fix list with effort estimates>

## Plague Score

0 = fully shipped and discoverable | 100 = brilliant, commercially invisible
```

## Pipeline Integration

```
Phase 1 — Craft:    scaffold → init → workflow → followup
Phase 2 — Converge: marbles ↻ (loop until P0=P1=P2=0)
Phase 3 — Ship:     dou → decorate → hydrate → release
```

DoU findings feed `vc-decorate` (coherence) and `vc-hydrate` (packaging).
After hydration, `vc-release` handles deployment and launch.

## Anti-Patterns

- Running DoU only on code (it's a PRODUCT surface audit)
- Treating `[OK]` repo health as proof of readiness
- Auditing without crawling URLs
- Assuming non-web products need no representation surface
- Skipping the install path test
- Reporting without severity ranking
- Not re-running after hydration

## The Plague Diagnostic

Plague pattern:

1. Tests pass `[PASS]`
2. Architecture sound `[PASS]`
3. README exists `[PASS]`
4. Nobody can find it from Google `[FAIL]`
5. Nobody can install without a toolchain `[FAIL]`
6. Nobody can pay for it `[FAIL]`

Items 4-6 are the Definition of Undone.

---

_"The antidote is not more tools. It is not another framework._
_It is a decision: choose what ships, and finish it. All of it. Not just the code."_

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
