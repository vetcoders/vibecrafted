---
title: "Agents"
description: "The fleet model: five equal agent CLIs, one launcher shape, honest attribution, and headless workers that survive terminal loss."
section: concepts
order: 60
---

# Agents

Vibecrafted orchestrates a fleet of coding agents from different vendors
through one launcher shape, one artifact contract, and one attribution
rule. The framework treats agents as interchangeable front-line
implementers — equally capable, equally accountable — not as one "main"
model with helpers.

## The fleet

| Agent    | Backing CLI                                               |
| -------- | --------------------------------------------------------- |
| `claude` | Anthropic Claude Code                                     |
| `codex`  | OpenAI Codex                                              |
| `agy`    | Google Antigravity (successor to the retired Gemini lane) |
| `junie`  | JetBrains Junie                                           |
| `grok`   | xAI Grok                                                  |
| `cursor` | Cursor Agent CLI (`cursor-agent`)                         |

Agent CLIs are installed and authenticated separately from the framework;
`vibecrafted doctor` reports which ones are available on your machine. The
Gemini CLI lane is hard-removed from active launchers — `agy` is the
Google-side successor.

## One launcher shape

Every skill launches on any agent with the same command form:

```bash
vibecrafted <skill> <agent> --file <plan.md>

# examples
vibecrafted implement codex --file ~/.vibecrafted/artifacts/<org>/<repo>/<date>/plans/plan.md
vibecrafted research claude --file questions.md
vibecrafted justdo agy --file task.md
```

The plan file is delivered verbatim to the worker. Agent selection is
fail-closed: an unknown agent name is rejected, never silently swapped for
a default.

## Agent equality

Differences in observed output between vendors come mostly from tooling
friction around the model, not from raw capability. Vibecrafted therefore
enforces peer parity:

- No agent is the privileged "brain"; any of them can hold any stage.
- Dispatch plans name the agent that will execute, and reports name the
  agent that did.
- A thinner report is evidence of a smaller evidence base, not grounds to
  dismiss the agent.

## Attribution

The commit trailer names the actual executor of the work:

```text
Authored-By: <agent> <agents@vetcoders.io>
```

where `<agent>` is `claude`, `codex`, `agy`, `junie`, `grok`, or `cursor` — one line
per agent for collaborative commits. The rules:

- The signature belongs to the agent that **executed** the plan, not the
  agent that wrote or dispatched it.
- If a plan is re-dispatched to a different agent after a failure, the
  signature changes to the new executor.
- Coordinators do not append their own trailer to work other agents
  performed.
- Vendor-branded footers and default tool signatures are not used.

Combined with the Living Tree commit title convention
(`[<agent>/<workflow>] <description>`), every change in history is
attributable to a specific agent and workflow at a glance.

## Headless workers

Ordinary workflow and fleet workers launch as **detached headless
processes** by default — regardless of TTY presence or any open dashboard.
Consequences:

- Closing a terminal, a dashboard tab, or an SSH session does not kill a
  worker. Viewers are projections; the worker owns its own process
  session.
- You observe progress through durable artifacts — control-plane state,
  receipts, transcripts, and reports under
  `~/.vibecrafted/artifacts/<org>/<repo>/<date>/reports/` — not by
  watching a pane.
- A true PTY is reserved for the interactive user session or an explicit
  `--runtime terminal` compatibility path.

```bash
vibecrafted await <agent> --run-id <run_id>   # canonical wait for a worker
vibecrafted settlements inspect <run_id>      # settled truth afterwards
```

## Resume preserves lineage

A resume is a lineage-preserving attempt: it keeps the run identity and
attempt history of the worker it resumes. Silently replacing the worker
with a different agent presented as the same run is forbidden — if claude
takes over a failed codex run, that is a new attribution, recorded as
such. This keeps handoff evidence verifiable end to end.

## Related pages

- [Architecture](/docs/architecture/) — where worker truth lives.
- [Delivery proof](/docs/delivery-proof/) — how worker claims settle.
