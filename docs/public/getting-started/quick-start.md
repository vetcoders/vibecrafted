---
title: "Quick start"
description: "Your first five minutes: orient an agent, run a workflow, watch the run, and find the durable report."
section: getting-started
order: 30
---

# Quick start

You have an AI-built repo and a working [install](/docs/install/). This page takes you from an empty terminal to your first verified workflow report in about five minutes.

## 1. Verify the engine

```bash
vibecrafted doctor
vibecrafted version
```

Green means ready. Anything else, the doctor tells you what is weak and what to check next — see [Doctor](/docs/doctor/).

## 2. Orient your agent

Go to any git repository and run init with the agent you use:

```bash
cd ~/projects/my-app
vibecrafted init claude
```

Init is the front door for `vc-init`. Your agent gets three things before touching anything:

- **Intentions** — what was done before (indexed session history via AICX)
- **Sight** — what the code looks like now (structural map via Loctree)
- **Ground truth** — whether quality gates actually pass

The agent now has orientation instead of assumptions.

## 3. Run your first workflow

```bash
vibecrafted workflow claude --prompt "Plan and implement input validation on the signup form"
```

`workflow` runs the examine → research → implement pipeline. The general shape is:

```bash
vibecrafted <skill> <agent> [-p <prompt> | -f <file>]
```

Other everyday entries:

```bash
vibecrafted implement codex --prompt "Add JWT authentication"   # ship WRITE stage
vibecrafted review codex --prompt "Audit the auth changes"      # read-only review
vibecrafted marbles claude --prompt "Loop until clean"          # convergence loop
```

## 4. Watch the run

```bash
vibecrafted status          # today's agent activity
```

If you launched from inside a vc-frame session, the run streams in its own tab — every tab is a control-plane run with a `run_id`, a report, a transcript, and a settlement verdict. The status bar counts settlements as **f / x / n** (Finalized · Failed · Needs-attention).

To continue a previous session with the same agent:

```bash
vibecrafted resume claude
```

## 5. Read the report

Runs do not end in scrollback. Every workflow writes a durable report under the artifacts store:

```text
~/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/
```

List today's reports for your repo:

```bash
ls ~/.vibecrafted/artifacts/<org>/<repo>/"$(date +%Y_%m%d)"/reports/
```

Plans and temporary files land beside them in `plans/` and `tmp/` under the same date directory. The report is the contract: what changed, what was verified, and the next truthful move.

## The command deck

The deck stays small on purpose:

```bash
vibecrafted help            # the deck
vibecrafted help --all      # full workflow reference
```

| Command           | Purpose                                       |
| ----------------- | --------------------------------------------- |
| `init [agent]`    | Orient an agent in this repo                  |
| `<skill> <agent>` | Run a workflow with an agent                  |
| `resume <agent>`  | Continue a stopped run or a provider session  |
| `status`          | Today's agent activity                        |
| `doctor`          | Installation health — pass/fail               |
| `receipt`         | Delivery/runtime receipt (source ↔ installed) |
| `settlements`     | Read-only f/x/n ledger query                  |
| `update`          | Update to the latest release                  |

The full ship cycle when you want to walk it end to end:

```text
scaffold → implement → review → workflow → followup → marbles → audit → polarize → dou → hydrate → release
```

## Next

- [Update and rollback](/docs/update/) — keeping the runtime current.
- [Configuration](/docs/configuration/) — where state and config live.
- [Common issues](/docs/common-issues/) — when something looks off.
