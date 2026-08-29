---
title: "CLI Overview"
description: "Command anatomy of the vibecrafted CLI: management commands, skill launchers, agent modes, uniform flags, and aliases."
section: cli
order: 10
---

# CLI Overview

The `vibecrafted` CLI has one grammar with two shapes: management commands
(`vibecrafted <command>`) and skill launchers (`vibecrafted <skill> <agent>`).
Every launcher dispatches a tracked run for one of the fleet agents —
claude · codex · agy · junie · grok · cursor — and every run leaves a report,
a transcript, and control-plane state you can query afterwards.

## Command anatomy

```bash
vibecrafted <command> [args]
vibecrafted <skill> <agent> [-p <prompt> | -f <file>]
```

Examples of both shapes:

```bash
vibecrafted doctor                                  # management command
vibecrafted implement codex -p "Ship dark mode"     # skill launcher
vibecrafted marbles claude -p "Loop until clean"    # skill launcher
```

The main deck (`vibecrafted help`) shows the management commands and the ship
cycle. The full reference, including operator surfaces and additional skills,
lives behind `vibecrafted help --all`. Per-topic help is
`vibecrafted help <topic>`.

## Skill launchers

Skills are workflow launchers. The ship cycle is the canonical order:

```text
scaffold → implement → review → workflow → followup → marbles
        → audit → polarize → dou → hydrate → release
```

See [Workflow launchers](/docs/workflow-launchers/) for the full catalogue and
[Lifecycle overview](/docs/lifecycle-overview/) for running all stages as one
supervised run.

## Uniform skill flags

Every skill launcher accepts the same flag contract:

| Flag                   | Meaning                                    |
| ---------------------- | ------------------------------------------ |
| `-p, --prompt <text>`  | Inline prompt                              |
| `-f, --file <path.md>` | Input file as prompt context               |
| `--count <n>`          | Marbles / Polarize loop count (default: 3) |
| `--depth <n>`          | Marbles plan crawl depth (default: 3)      |
| `--session <id>`       | Session ID for `vibecrafted resume`        |

`--verbose` is a global option for detailed output. Model overrides exist for
claude (`--model`), codex (`-m`) and cursor (`--model`); other agents run
their defaults.

## Action-first agent modes

The same action-first grammar executes prepared plan files directly:

```bash
vibecrafted implement <agent> <plan.md>   # execute a plan file
vibecrafted research <agent>  <plan.md>   # single-agent research mode
vibecrafted review <agent>    <plan.md>   # review bounded code artifacts
vibecrafted plan <agent>      <plan.md>   # generate an implementation plan
vibecrafted prompt <agent>    <plan.md>   # free-form prompt with context
vibecrafted observe <agent>   --last      # check last agent report/transcript
```

All actions run through the same engine and produce the same run records.
Agent-first commands are rejected with a migration hint.

## `vc-<skill>` shortcuts

Every skill also installs a `vc-<skill>` shell shortcut, so these are
equivalent:

```bash
vibecrafted implement codex -p "Ship the feature"
vc-implement codex -p "Ship the feature"
```

The shortcut layer is convenience only; it forwards to the same launcher.

## Aliases

Silent aliases exist for muscle memory. They work but are not the documented
surface:

| Alias    | Canonical   |
| -------- | ----------- |
| `stats`  | `status`    |
| `check`  | `doctor`    |
| `remove` | `uninstall` |
| `start`  | `dashboard` |

`justdo` (and `vc-justdo`) is its own skill — a "take the task and deliver"
posture where the prompt defines the task type. It is not an alias of
`implement`.

## First five minutes

```bash
cd ~/projects/my-app
vibecrafted doctor                # install health — pass/fail
vibecrafted init claude           # orient the agent in this repo
vibecrafted workflow claude --prompt "Plan and implement <task>"
vibecrafted implement codex --prompt "Ship <task>"
vibecrafted status                # today's agent activity
```

From there, follow runs with [observe and await](/docs/observe-await/), and
read the management command reference in [Commands](/docs/commands/).
