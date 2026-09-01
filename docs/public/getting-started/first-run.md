---
title: "First run"
description: "What happens on your first Vibecrafted command, how missing agent CLIs are reported, and how to read doctor on a fresh install."
section: getting-started
order: 35
---

# First run

The first command you run decides whether the product feels finished. Vibecrafted
entry points are built on one rule: **name the gap and hand over the command that
closes it.** A missing dependency should never produce a silent non-zero exit.

## Orient an agent

```bash
vibecrafted init claude
```

`init` recovers intent through AICX, maps the living tree through Loctree, and
checks runtime truth before any work begins. Substitute any agent you have
installed: `claude`, `codex`, `agy`, `junie`, `grok`, `cursor`.

## When an agent CLI is missing

Vibecrafted drives agent CLIs. It does not bundle or vendor them, because the
credentials, update cadence and terms belong to you and the vendor rather than
to Vibecrafted.

So on a fresh machine, the first `init` usually finds nothing to drive. Instead
of exiting silently, it prints the gap and the fix:

```
✗ claude CLI is not available.
  Install it, then re-run this command:
    npm install -g @anthropic-ai/claude-code
  Or check the whole fleet: vibecrafted doctor
```

The known install commands are:

| Agent    | Install                                            |
| -------- | -------------------------------------------------- |
| `claude` | `npm install -g @anthropic-ai/claude-code`         |
| `codex`  | `npm install -g @openai/codex`                     |
| `junie`  | `npm install -g @jetbrains/junie`                  |
| `grok`   | `npm install -g @xai-official/grok`                |
| `agy`    | install Google Antigravity CLI, then `agy install` |
| `cursor` | `curl https://cursor.com/install -fsS \| bash`     |

### Two failures, two answers

A CLI that is absent and a CLI that is present but not executable are different
problems, and they get different messages. If the agent is staged in
Vibecrafted's own agent bin but cannot run, the error says so and gives you the
`chmod +x` line for that exact path rather than telling you to install something
you already have.

### Your CLI wins

Vibecrafted **appends** its bundled agent bin to `PATH` rather than prepending
it. An agent CLI you installed and authenticated yourself always takes
precedence over a bundled copy. Vibecrafted extends your environment; it does
not shadow it.

## Reading doctor on a plain install

```bash
vibecrafted doctor
```

`doctor` separates what is broken from what is merely absent.

- **Red** means something is genuinely wrong. Act on it.
- **Yellow** on a fresh install usually means an optional, externally managed
  foundation is not present. That is expected, not a defect.

A plain install has no source checkout by design. `doctor` reports that as one
honest line — `installed-only — normal unless you develop this tool` — rather
than repeating a source-drift warning for every field of every tool. What is
known still prints: installed path, SHA, version and index. The `--json` payload
is unchanged, so tooling loses nothing when the human-facing output gets
quieter.

## The dashboard is optional

`vibecrafted dashboard` needs `vc-frame`, the operator TUI. On platforms where
the installer ships no `vc-frame` build, the dashboard is unavailable — and that
is not a broken install, because the dashboard is an optional operator surface
rather than the product.

The headless path needs no TUI at all:

```bash
vibecrafted workflow claude --prompt "Plan and implement auth"
vibecrafted observe <run-id>
```

Dispatch a run, then observe it. Every run leaves a durable report regardless of
whether any terminal UI was attached.

## Start working

```bash
vibecrafted implement codex --prompt "Add user authentication with JWT"
```

Use `vibecrafted help` for the operator surface and `vibecrafted help --all` for
everything.

## Next

- [Quick start](/docs/quick-start/) — the first full loop.
- [Doctor](/docs/doctor/) — reading every field.
- [Common issues](/docs/common-issues/) — symptoms and fixes.
