---
title: "Skills catalog"
description: "Every shipped skill grouped by pipeline position, with phase, purpose, and an example invocation."
section: skills
order: 20
---

# Skills catalog

This catalog lists the shipped `vc-*` skills grouped by where they sit in the
lifecycle. Phase marks the Read–Write cadence: READ skills observe and judge,
WRITE skills mutate the tree or its packaging, and posture/meta/foundation
skills shape how the others run. Example invocations use the skill-first
grammar `vibecrafted <skill> <agent>` with any fleet agent
(claude · codex · agy · junie · grok · cursor); each core launcher also has an
interactive `/vc-<skill>` form.

## Pipeline stages

The `vc-ship` umbrella chains these eleven stages as one supervised run:
scaffold → implement → review → workflow → followup → marbles → audit →
polarize → dou → hydrate → release.

| Skill          | Phase | Purpose                                                                     | Example                       |
| -------------- | ----- | --------------------------------------------------------------------------- | ----------------------------- |
| `vc-scaffold`  | WRITE | Founder-first planning: turn vague intent into a measurable execution plan. | `vibecrafted scaffold claude` |
| `vc-implement` | WRITE | Autonomous end-to-end delivery with followup and marbles built in.          | `vibecrafted implement codex` |
| `vc-review`    | READ  | Bounded findings-first code review with evidence-graded findings.           | `vibecrafted review claude`   |
| `vc-workflow`  | WRITE | Examine → Research → Implement pipeline for structure-first changes.        | `vibecrafted workflow codex`  |
| `vc-followup`  | READ  | Post-implementation trajectory audit: gaps, drift, next leverage.           | `vibecrafted followup claude` |
| `vc-marbles`   | WRITE | Truth-convergence loop: deliberate over-correction of every crack.          | `vibecrafted marbles codex`   |
| `vc-audit`     | READ  | Per-plan falsification: prove or refuse each task claim against evidence.   | `vibecrafted audit claude`    |
| `vc-polarize`  | WRITE | Strip the marbles excess back to one axis; align code, tests, docs.         | `vibecrafted polarize claude` |
| `vc-dou`       | READ  | Definition of Undone: gap analysis across the entire product surface.       | `vibecrafted dou claude`      |
| `vc-hydrate`   | WRITE | Packaging and go-to-market work that closes the DoU gaps.                   | `vibecrafted hydrate codex`   |
| `vc-release`   | WRITE | Outward ship: release mechanics, deployment truth, launch checks.           | `vibecrafted release claude`  |

## Session and posture

| Skill          | Phase   | Purpose                                                                                                | Example                        |
| -------------- | ------- | ------------------------------------------------------------------------------------------------------ | ------------------------------ |
| `vc-init`      | READ    | Context bootstrap at session start: structure, history, ground truth.                                  | `vibecrafted init claude`      |
| `vc-justdo`    | WRITE   | No-ceremony delivery; the prompt defines the task type. Run-id prefix `just-` (distinct from `impl-`). | `vibecrafted justdo codex`     |
| `vc-partner`   | Posture | Shared steering: user and agent plan together, vision stays fixed.                                     | `vibecrafted partner claude`   |
| `vc-ownership` | Posture | Agent takes full operational ownership from architecture to ship.                                      | `vibecrafted ownership claude` |
| `vc-operator`  | Posture | Conduct a fleet through a planned multi-wave dispatch chain.                                           | `/vc-operator` (interactive)   |

## Fleet and delegation

| Skill         | Phase | Purpose                                                               | Example                               |
| ------------- | ----- | --------------------------------------------------------------------- | ------------------------------------- |
| `vc-ship`     | Meta  | The full lifecycle umbrella: all eleven stages as one supervised run. | `/vc-ship` (interactive)              |
| `vc-dispatch` | Meta  | Operate external fleet lines: prompt assembly, await, recovery.       | `vibecrafted dispatch`                |
| `vc-agents`   | Meta  | External-fleet dispatch contract and action-first grammar.            | `vibecrafted implement codex plan.md` |
| `vc-delegate` | Meta  | Decide native in-session subagents vs. external escalation.           | `vibecrafted delegate claude`         |

## Verification and curation

| Skill         | Phase | Purpose                                                                | Example                                   |
| ------------- | ----- | ---------------------------------------------------------------------- | ----------------------------------------- |
| `vc-intents`  | READ  | Planned-vs-landed audit: which intentions actually exist in code.      | `vibecrafted intents claude`              |
| `vc-research` | READ  | Triple-agent research swarm; three independent reports, one synthesis. | `vibecrafted research --prompt "<topic>"` |
| `vc-trust`    | READ  | Post-hoc falsification of commit claims and authorship fairness.       | `vibecrafted trust claude`                |
| `vc-guard`    | Gate  | In-flight enforcement: refuse continuation on a recorded trust block.  | `vibecrafted guard claude`                |
| `vc-prview`   | READ  | Bounded PR/branch/commit-range review over generated diff artifacts.   | `/vc-prview` (interactive)                |
| `vc-prune`    | WRITE | Repository curation: dead-surface removal and the silencer strip.      | `vibecrafted prune`                       |
| `vc-decorate` | WRITE | Late-stage visual finishing within the product's own design language.  | `vibecrafted decorate claude`             |

## Foundations and utilities

Foundation skills have no worker launcher by design — they load inside other
skills as perception and memory senses.

| Skill             | Phase      | Purpose                                                           |
| ----------------- | ---------- | ----------------------------------------------------------------- |
| `vc-loctree`      | Foundation | Structural repository perception: scope, consumers, blast radius. |
| `vc-aicx`         | Foundation | Intention retrieval over past agent sessions.                     |
| `vc-skillaunch`   | Meta       | Distill a completed workflow into a new reusable skill.           |
| `vc-screenscribe` | Utility    | Screencast-recording analysis workflow.                           |

## Verifying the inventory

The catalog above is a snapshot; the installed truth is on your machine:

```bash
vibecrafted help          # launcher grammar and available skills
ls ~/.vibecrafted         # runtime home: artifacts, control plane, locks
```

Launchers and skill ids map one-to-one — `justdo` is not an alias of
`implement`, and no launcher silently renames another skill.
