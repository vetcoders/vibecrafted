<!-- loctree-advise: v1 -->

# Loctree + AICX + Vibecrafted Agent Operating Guide

> Loctree gives **sight**.
>
> AICX gives **insight**.
>
> 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. gives **hands to craft products**.

This repository should be treated as a living system, not as a loose pile of files.

Before making structural assumptions, inspect the map.

Before changing behavior, understand impact.

Before creating new symbols, check whether the shape already exists.

Loctree is the default structural map for repository work. Skipping it usually costs time: dependencies, blast radius, symbols, runtime entry points, dead surfaces, duplicates, and exact occurrences are visible faster through Loctree than through manual rummaging.

AICX preserves intent history and decision context.

Vibecrafted closes the loop with proof, discipline, and shipping pressure.

---

# Operating Rule

For structural questions, start with Loctree (**NEW!** `v0.11.3` Now also for
literal occurrences).

### Preferred order

1. Loctree MCP
2. Loct CLI
3. Local tools (`rg`, `grep`, `awk`, `sed`, `cat`)
4. Manual inspection

### Before editing

| Action        | Preferred Tool |
| ------------- | -------------- |
| Edit file     | `slice(file)`  |
| Delete file   | `impact(file)` |
| Rename file   | `impact(file)` |
| Refactor      | `impact(file)` |
| Create symbol | `find(name)`   |

### Literal truth before assumptions

Use:

- `find --literal`
- `loct occurrences IDENTIFIER`
- `loct body SYMBOL`

before broad text search.

### Fallbacks are allowed

Use:

- `rg`
- `grep`
- `awk`
- `sed`
- `cat`

when Loctree cannot answer cleanly.

---

# Loctree Feedback Loop

If Loctree is:

- wrong
- stale
- slow
- awkward
- missing language support
- missing an important surface
- suggesting an obvious improvement

append a note to:

`~/.vibecrafted/loctree/loctree-fail.md`

### Rules

- Never recreate the file.
- Never overwrite the file.
- Always append.

### Valid entries

- bugs
- missing features
- UX problems
- performance issues
- language coverage issues
- fallback situations

Repeated reports are signal, not noise.

---

# Why This Matters

Loctree changes agent work from:

> text rummaging

into:

> map-first engineering

The goal is not obedience.

The goal is:

- fewer wrong edits
- better blast-radius judgement
- faster recovery
- more honest runtime decisions

<!-- /loctree-advise -->

---

# Agent Behavior Standard

## Work From Structure Before Text

Start with `vc-init`. Do not assume repository shape from filenames alone.

Always identify:

- subsystem
- entry points
- symbols
- ownership boundaries
- likely blast radius

Prefer structural inspection over broad search whenever the question is about:

- dependency
- ownership
- impact
- location

You can use raw text search even when:

- the question is literal
- the question is local

You gain beautiufly curated context around your search. If `loctree-mcp` or
`loct` cli fail, report it honestly and fall back into `rg`, `grep`, `awk`,
`sed` or any tool you are familiar with.

---

## Do Not Edit Blind

Before modifying code:

1. Locate the target.
2. Inspect local implementation.
3. Inspect callers and dependents.
4. Check nearby tests, examples, and docs.
5. Make the smallest coherent change.
6. Verify through the closest runtime path.

If verification cannot be run:

> Say so explicitly.

---

## Evidence Checkpoints Are Not Ceremony

Procedures that capture state are attribution infrastructure. Do not treat
`vc-init`, pre-change baselines, verification gates, reports, or handoff notes
as decorative process.

Every handoff across agents or workflow phases must preserve enough evidence to
answer:

- what was the repo state before ownership changed?
- what changed during this ownership segment?
- what was verified, and what was not?
- what should the next agent re-read before acting?

Skipping a checkpoint is not efficiency. It is regression laundering: once the
baseline is gone, a later failure can no longer be attributed to a lifecycle
segment.

---

## Pre-Handoff Baseline

Before handing work to another agent, capture a pre-handoff baseline:

- branch and `HEAD` SHA
- `git status --short`
- files changed by this segment
- tests, lint, or runtime checks run, with result
- known failures and unverified surfaces
- current intent and scope boundary
- exact next instruction or report path for the receiving agent

The receiving agent performs handoff intake before editing: re-read the
baseline, re-read the live repo state, compare drift, then proceed or report
substrate failure.

No handoff without baseline. If there is no pre-handoff baseline, regression
attribution is guesswork.

---

## Do Not Create Parallel Systems Casually

Before introducing:

- abstractions
- helpers
- parsers
- services
- commands
- components
- config paths

check whether one already exists.

If you introduce a new path:

> Explain why reuse was incorrect.

Avoid duplicate systems created only because the agent did not look hard enough.

---

## Prefer Runtime Truth

Static structure matters.

Runtime behavior decides.

When changing:

- execution
- configuration
- packaging
- CLI behavior
- API contracts
- generated artifacts

verify against the real execution path whenever possible.

Passing type checks is useful.

It is not the same thing as product readiness.

---

## Keep The Repository Legible

Prefer changes that improve understanding.

Avoid cleverness that hides shape.

Preserve naming consistency.

Do not bury important behavior inside glue code.

If a file becomes a dumping ground:

> Call it out.

---

## Respect Existing Work

Do not:

- revert
- delete
- rewrite

code you do not understand.

Do not assume unfamiliar changes are safe to discard.

If the repository is moving:

> Re-read before acting.

Treat concurrent agents or human work as part of the system.

---

## Use Direct Language In Handoffs

Always state:

- what changed
- why it changed
- what was verified
- what was not verified
- what remains risky
- what should be checked next

Do not hide uncertainty.

Do not claim confidence you have not earned.

---

# Release And Install Doctrine

## Never Ship Non-Notarized

Operator standing order (2026-08-26). Every macOS build handed to a person is
notarized and stapled — `--no-notarize` exists only for pipeline debugging,
never for an artifact anyone installs. Headless notarization uses a Keychain
profile (`NOTARY_PROFILE`, e.g. `vibecrafted-notary`); raw Apple-ID
credentials are rejected without a TTY. An unnotarized app makes Gatekeeper
re-assess thousands of runtime binaries on first execution (observed:
`syspolicyd` at 192% CPU).

## Own Only Your Namespace

The runtime install publishes launchers only for names Vibecrafted owns
(`vc-*`, `vibecrafted*`, `vibecraft`, `telemetry`). Bundled public tools
(loct, loctree*, aicx*, prview, screenscribe) stay generation-private and
surface globally only as `vibecrafted-<name>`. Never overwrite another
product's command; reclaim old bare-name shims strictly by receipt
path+digest. A vendored foundation fills a gap — a pre-existing PATH install
always wins.

## Host Metadata Is Not Payload

`.DS_Store` is stamped by the host faster than any sweep can win the race.
The carrier tar excludes it, the closed inventory skips it on write and
verify; every other rejection (symlinks, digest/size/mode drift) stays
fail-closed. Do not reintroduce a hard reject for names that can never ship.

## Upgrades Preserve The Operator

An install must never lose operator-owned state: `[server]` config
(`~/.config/vibecrafted/config.toml`, e.g. a Tailscale bind), live frame
sessions (the frame server must outlive its client — detached, reattachable),
and running agents. Replacing the app bundle under a live workspace is an
operator-gated action.

# The Vibecrafted Manifesto

## We Do Not Treat AI Like Magic

We treat it as a stochastic engine that can:

- accelerate craft
- multiply leverage
- generate noise
- has ability to self-correct
- converge if

if left without structure.

Vibecrafted exists because fragile prompting is not a development methodology.

Shipping requires:

- shape
- taste
- pressure against chaos

---

## Code Is Craft

Code is not paperwork.

Code is not a byproduct of tickets.

Code is not done because a narrow check turned green.

Code is craft.

Good systems are:

- shaped
- refined
- tested against reality
- made legible

Local elegance is not enough.

Runtime truth matters more than theoretical correctness.

Product truth matters more than internal neatness.

---

## Real Builders Can Come From Anywhere

Vibecrafted was built by people outside the traditional software priesthood.

That is not an apology.

That is evidence.

The point was never pedigree.

The point was whether the thing could be made real.

We respect:

- clear thinking
- real execution
- systems that survive contact with reality

Everything else is costume.

---

## Vibecrafting Is An Engineering Mode

Vibecrafting is:

> structured human–AI collaborative engineering

It is not:

- blind prompting
- random generation
- post-rationalized hope

Human taste sets direction.

Agentic force expands the search space.

Reality decides what survives.

---

## Native Discovery Before Delivery Language

The operator's fastest discovery language is the language in which the thought
arrives. For this team, Polish is often the shortest path from intuition to
shape. Do not force premature English polish while the idea is still forming.

The preferred pipeline is:

```text
native-language thought -> thesis -> evidence obligations -> delivery prompt/spec
```

Only translate into English delivery text once the shape and proof obligations
are clear. Product intuition is not less professional because it arrived in
Polish; it is discovery signal.

## Operator Echo Packets

The default conversation is the chat. Do not invent a permanent second channel.
When the operator explicitly sends `!echo '<text>'`, however, treat the echoed
text as operator input, not as shell-log noise.

For Codex, an echo packet is the reliable realtime operator transport. If it
appears, Codex can trust that the operator deliberately sent that packet now,
even when the packet quotes or comments on earlier chat. Other Codex
interactive channels can arrive late, be replayed after compaction, or be
surfaced only when the agent returns from await/observe; they do not carry the
same realtime certainty.

Do not generalize this guarantee across the fleet. For Claude, Gemini, Agy,
Junie, Grok, or any other agent runtime, treat echo realtime certainty as
unproven unless that runtime's harness has been verified to preserve the same
delivery semantics.

Still read the content. An echo can be a command, correction, quote, delayed
commentary on an earlier chat message, or confirmation. But the transport itself
is not random send time: `echo` is the low-latency operator lane.

---

## Marbles Turns Noise Into Product

Every AI system introduces variance.

Every generation adds noise.

That is physics.

The answer is convergence.

We:

- loop
- inspect
- add counterexamples
- reduce entropy

until the system stops lying about being done.

Marbles is:

> counterexample-guided stochastic convergence

Early loops remove breakage.

Late loops remove polish debt.

---

## Structure Comes Before Output

Large models:

- lose the middle
- hallucinate continuity
- break global shape

Architecture must therefore be externalized.

Loctree is not an accessory.

It is a memory prosthetic.

Without structure:

> generation becomes imitation

With structure:

> generation becomes engineering

---

## Done Is A Market Condition

A passing test suite is good.

A healthy repository is good.

A clean architecture is good.

None of that is enough.

If nobody can:

- find it
- install it
- trust it
- understand it
- buy it

then it is not done.

This is the Definition of Undone.

Most unfinished products fail because:

- onboarding breaks
- docs break
- install paths break
- discoverability breaks
- credibility breaks

Shipping begins where self-congratulation ends.

---

## Prefer The Better Shape

Do not preserve bad architecture out of politeness.

Do not worship compatibility when the shape is harmful.

If a patch is enough:

> patch

If the shape is wrong:

> rewrite

If the code should not exist:

> remove it

A clean cut is often kinder than indefinite maintenance.

---

## Work In Living Systems

Real product trees are alive.

People edit.

Context shifts.

Assumptions go stale.

The repository is never a museum.

Re-read.

Adapt.

Avoid stale certainty.

Do not revert what you do not understand.

Work with movement.

---

## Optimize For First Real Users

Early products do not need theatrical optionality.

They need:

- one sharp use case
- one believable promise
- one path that works

Prefer:

- clarity over coverage
- one working funnel over many half-ideas
- the smallest surface that proves truth

---

## Reject False Reassurance

Reject:

- green CI as proof of readiness
- tiny diffs as proof of wisdom
- compatibility by reflex
- fake abstractions
- framework rituals
- internal capability mistaken for completion
- parallel systems created to avoid cleanup
- generated code nobody understands
- confident answers without verification

A system can:

- compile
- be elegant
- be technically impressive

and still be dead.

---

## Vibecrafted Is Not Anti-Science

We do not choose between intuition and rigor.

We use:

- intuition to discover shape
- rigor to prove it

SHACE, Marbles, Loctree Mapping, and PSCD are first-party Vibecrafted concepts.

---

## The Job Is To Ship

The job is not:

- to impress
- to preserve myths
- to collect elegant fragments

The job is:

- diagnose
- reframe
- cut dead weight
- implement decisively
- verify reality
- surface the next truth

That is the work.

---

# Final Line

Move fast, but with taste.

Be radical when radical is cleaner.

Be practical when practical wins.

Finish the whole thing, not just the code.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. is for builders who are done pretending chaos is a process.

We craft.

We converge.

We ship.

---

# Repository-Specific Instructions

## Project Identity

| Field                      | Value |
| -------------------------- | ----- |
| Name                       |       |
| Purpose                    |       |
| Primary language and stack |       |
| Runtime surfaces           |       |
| Build command              |       |
| Test command               |       |
| Lint command               |       |
| Release command            |       |
| Generated artifacts        |       |

---

## Structural Map

| Area                       | Description |
| -------------------------- | ----------- |
| Primary source directories |             |
| Runtime entry points       |             |
| Public APIs                |             |
| Internal-only modules      |             |
| Configuration files        |             |
| Persistence or state       |             |
| External integrations      |             |

---

## Agent Rules For This Repository

### Before Editing

-

### Before Refactoring

-

### Before Deleting

-

### Before Adding Dependencies

-

### Before Changing Public APIs

-

### Before Changing Distribution Artifacts

-

### Before Changing Generated Files

- ***

## Verification Expectations

| Scenario                  | Minimum Verification                                                                                                                                                                                                                                                      |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Small edits               | `make check` (ruff · prettier · semgrep on changed files) + the nearest pytest module, run with `env -u PYTHONPATH` until the runtime stops exporting it (HAK-32)                                                                                                         |
| Behavior changes          | focused test that fails without the change + `make unified-product-contract-gate` when `vibecrafted_core/` or `scripts/` are touched; Rust: `cargo clippy --workspace --all-features -- -D warnings` + `cargo test`                                                       |
| Release-impacting changes | `scripts/build-portable-release.sh` from a clean commit (payload-hygiene gate) and one `make dmg RELEASE_FLAGS=--snapshot-donors`; then `gh run list --workflow "Release source gate" --limit 1` on the tag — the gate failed on every tag from v3.7.0 to v4.0.0 (HAK-30) |

### Known Slow Or Flaky Checks

- `tests/tui/test_research_launcher.py` ×3 (settle timeouts) and `test_dashboard_subcommand_launches_repo_owned_vc_frame_layout` are pre-existing red on some hosts — confirm against a clean HEAD before attributing.
- `tests/tui/*` and `vibecrafted-core/tests/*` must run as separate pytest invocations (conftest collision).
- pytest can leak a real workspace into `~/.vibecrafted/control_plane` and export `VIBECRAFTED_WORKSPACE_ID` (HAK-31) — run with an isolated `VIBECRAFTED_HOME` until the fixture is default.

### Checks Requiring Secrets Or External Services

- DMG signing/notarization (Developer ID + notary credentials), `gh` for the release-gate probe, vibecrafted-io deploy — operator buttons, never run by workers. \*\*\*

## Safety Boundaries

Do not modify:

-

Do not delete:

-

Do not globally reformat unless explicitly requested.

Do not change licensing headers or notices without explicit instruction.

Do not add telemetry, network calls, or external services without explicit instruction.

Do not introduce new dependencies without checking:

- license
- maintenance state
- necessity

---

## Scaffold editor IA (LAW — no 30m scroll)

The `/scaffold/editor` surface is a **single-document studio**, not a
stacked dump of every artifact. Reference shape:
`unicode-puzzles-portal` / GlyphPulse (`app-shell`: fixed chrome, one canvas).

Required layout for any scaffold review UI work:

| Region     | Role                                             |
| ---------- | ------------------------------------------------ |
| **Left**   | Artifact nav (tabs) — keep as the plan index     |
| **Center** | One active document only (topbar + canvas)       |
| **Right**  | Inspector: status, checkpoint, tools/endpoints   |
| **Bottom** | Stats bar (mode, chars/lines, checkpoint counts) |

Rules:

- **One `.artifact-panel.is-active` at a time.** Hash + left tabs switch;
  never render every panel in a scrollable column.
- **Edit/Save** is a pill on the same plane as `needs-checkpoint` /
  `checkpointed` (shared radius/border/padding) — not a ghost text link.
- Default view is **rich** markdown; Edit opens mono source; Save returns
  to rich (and posts if dirty).
- Briefs and ownership cuts that touch this surface **must** preserve the
  studio shell. "List every file full-height down the page" is a doctrine
  violation, same class as overlay replacement in Codescribe.

Implementation lives in `vibecrafted-server/web/src/scaffold/mod.rs`
(`review-shell`, `panel_nav_script`, `editor_ships_single_document_studio_shell`).

---

## Handoff Format

Every completed task should end with:

### Summary

### Files Changed

### Verification Performed

### Verification Not Performed

### Risks Or Follow-Up

---

# Influences

This operating guide is influenced by:

- human–agent software loops
- context engineering
- structure-aware code modeling
- technical debt research
- counterexample-guided refinement
- practical product shipping

SHACE, Marbles, Loctree Mapping, PSCD, and the Vibecrafted operating language are first-party concepts from Vibecrafted / Vetcoders practice.

## Omni-observer + Slack gateway

See `docs/runtime/OMNI_OBSERVER_SLACK_GATEWAY.md` — **polarized (L3 sealed)**:
control_plane sole durable truth; server+MCP = eyes (projections);
bot = mouth/ear; workers = hands. Unit green ≠ Slack green (STALE
bridge / empty allowlist = operator residual, not architecture debt).
Hydrate residual pack (slack-agent): `deploy/OPERATOR_SMOKE_CARD.md`,
`npm run doctor`, `npm run install:launchagent`.
Parent: `docs/adr/0002-unified-operator-ownership.md` (`run-lifecycle` →
`control-plane`; Slack owns `a2a-envelopes` only).
