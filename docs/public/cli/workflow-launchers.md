---
title: "Workflow Launchers"
description: "The skill launcher catalogue: the canonical pipeline order and every additional skill with its purpose and an example invocation."
section: cli
order: 30
---

# Workflow Launchers

Every skill is a launcher: `vibecrafted <skill> <agent>` dispatches one agent
into one workflow and produces a tracked run with a report. Skills share the
[uniform flag contract](/docs/cli-overview/) (`--prompt`, `--file`, `--repo`,
`--worktree`, `--model`, `--count`, `--depth`, `--session`), and each also
installs a `vc-<skill>` shortcut. `--repo <path>` selects the repository from
any working directory; `--worktree true` runs the worker in a fresh linked
checkout of that repository.

## Canonical pipeline order

```text
scaffold → init → workflow → followup → marbles → audit
        → dou → decorate → hydrate → release
```

This is the practical build-then-ship order. The full eleven-stage ship cycle
(adding `implement`, `review`, and `polarize`) runs as one supervised
lifecycle — see [Lifecycle overview](/docs/lifecycle-overview/).

### scaffold

Plan architecture from a vague idea. Turns an intention into a measurable,
self-sufficient plan that later stages (or a fleet) can execute.

```bash
vibecrafted scaffold claude --prompt "I want offline sync for my-app"
```

### init

Orient: history, structural perception, verification. The first context
handoff in every repository session — run it before dispatching work.

```bash
vibecrafted init claude
```

### workflow

Examine → Research → Implement: a three-phase pipeline that maps the code
first, gathers ground truth second, and implements third.

```bash
vibecrafted workflow claude --prompt "Plan and implement auth"
```

### followup

Audit post-implementation direction, gaps, and drift. Answers "is this
heading the right way and what still feels unfinished" after a delivery.

```bash
vibecrafted followup claude --prompt "Check the auth implementation"
```

### marbles

Convergence loop with counterexample elimination: repeated bounded rounds
that flood remaining cracks until the gates hold. `--count` sets the loop
count, `--depth` the plan crawl depth. Control verbs:
`vibecrafted marbles <pause|stop|resume|session|inspect|delete>`.

```bash
vibecrafted marbles codex --count 3 --depth 3
```

### audit

Plan-vs-code falsification with a requirements matrix. Read-only: proves or
refuses each completion claim against code and test evidence.

```bash
vibecrafted audit claude --file plan.md
```

### dou

Definition of Undone audit: a gap analysis across the whole product surface —
repo health, install path, docs, discoverability — before release.

```bash
vibecrafted dou claude --prompt "Audit launch readiness"
```

### decorate

Visual finishing and UX coherence: detects the existing design language and
polishes within it.

```bash
vibecrafted decorate codex --prompt "Polish the release surface"
```

### hydrate

Packaging and go-to-market: executes the non-code work that DoU findings
surfaced — listings, onboarding, distribution artifacts.

```bash
vibecrafted hydrate codex --prompt "Package the product"
```

### release

Ship to production: deployment, publishing, signing, and post-release smoke
checks.

```bash
vibecrafted release codex --prompt "Prepare release steps"
```

## Additional skills

| Skill       | Purpose                                                   |
| ----------- | --------------------------------------------------------- |
| `agents`    | External fleet delegation contract via agent modes        |
| `intents`   | Plan-to-runtime truth audit                               |
| `implement` | Autonomous end-to-end implementation                      |
| `partner`   | Shared steering and executive reasoning with the user     |
| `ownership` | Full-spectrum operational ownership and delivery          |
| `delegate`  | In-session native subagent delegation                     |
| `research`  | Triple-agent research swarm                               |
| `review`    | Bounded PR, branch, commit-range, or artifact-pack review |
| `prune`     | Runtime/publish cone extraction                           |

Examples:

```bash
vibecrafted implement codex --prompt "Ship the feature"
vibecrafted intents codex --prompt "Audit what from the plan really landed"
vibecrafted review claude --prompt "Review HEAD~10..HEAD"
vibecrafted ownership codex --prompt "Take the repo from diagnosis to finished surface"
vibecrafted prune codex --prompt "Map what participates in runtime truth"
```

`research` is a swarm launcher rather than a single-agent skill: it fires
claude + codex + junie on the same questions and collects three independent
reports.

```bash
vibecrafted research --prompt "Compare storage engines for offline sync"
```

Use `review` for a bounded target (a PR, a commit range); use `followup` when
the question is broader direction; use `audit` when a written plan claims
completion and you want it falsified.
