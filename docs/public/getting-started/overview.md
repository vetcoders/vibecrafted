---
title: "What is Vibecrafted"
description: "A local-first release engine that runs coding agents through a disciplined lifecycle, from first draft to shippable product."
section: getting-started
order: 10
---

# What is Vibecrafted

Vibecrafted is the release engine for AI-developed software. You run it after an AI has produced a repository and before a real user touches it. It drives coding agents through a disciplined lifecycle — perception, verification, convergence, install truth, packaging, launch readiness — until a stranger can install the product, trust it, and use it.

## What it does

Vibecrafted does not generate your first draft. It takes the repository you already have and asks one question in a loop: _what is still wrong?_ Quality gates and structural maps locate failures, an agent fixes them, and the loop repeats until the remaining risks are named, verified, or deliberately handed off.

The public ship cycle is:

```text
workflow -> implement -> marbles -> review -> dou -> release
```

Each stage is a workflow you run against a repository with an agent of your choice:

```bash
vibecrafted implement codex --prompt "Add JWT authentication"
vibecrafted marbles claude --prompt "Loop until clean"
vibecrafted dou claude --prompt "Audit launch readiness"
```

## Product surfaces

| Surface             | Command                | What it is                                                                  |
| ------------------- | ---------------------- | --------------------------------------------------------------------------- |
| CLI command deck    | `vibecrafted help`     | The primary interface: workflows, status, doctor, receipt, update           |
| vc-frame cockpit    | ships with the runtime | Terminal cockpit: session rail, per-run tabs, settlement counters f / x / n |
| TUI                 | `vibecrafted tui`      | Terminal UI for operating runs                                              |
| GUI                 | `vibecrafted gui`      | Browser-guided surface                                                      |
| Local control plane | runs on your machine   | Tracks every run: `run_id`, report, transcript, settlement verdict          |

Every workflow run is a first-class control-plane run. The truth of a run lives in durable artifacts under `~/.vibecrafted/artifacts/`, not in terminal scrollback — close the laptop, come back, and resume.

## Supported agents

Vibecrafted orchestrates the agent CLIs you already have installed and authenticated:

```text
claude · codex · agy · junie · grok · cursor
```

You pick the agent per invocation (`vibecrafted review codex …`, `vibecrafted scaffold claude …`). Multi-agent research sends the same question to several agents independently and lets you synthesize the strongest answer.

## Foundations

The framework stands on product-managed foundation tools, verified on every `vibecrafted doctor` run:

| Foundation       | What it does                                        |
| ---------------- | --------------------------------------------------- |
| Loctree (`loct`) | Structural code perception — maps, impact, findings |
| AICX (`aicx`)    | Agent-session memory — catalog, search, intents     |
| prview           | PR review artifact generator                        |
| screenscribe     | Screencast to structured engineering findings       |
| vc-frame         | Operator cockpit (session rail, layouts)            |

Foundation binaries are acquired prebuilt-first: npm, signed release assets, crates.io, or PyPI before any source build.

## What Vibecrafted is NOT

- **Not a hosted SaaS.** Everything runs local-first on your machine. State lives in `~/.vibecrafted/`, the installed runtime lives under `~/.local/share/vibecrafted`, and the control plane is a local server. No cloud account is required to operate it.
- **Not an agent.** Vibecrafted writes no code itself. It orchestrates the agent CLIs you bring — with their own credentials — through a lifecycle with verification at every stage.
- **Not a code generator.** It assumes AI already produced the draft. Its job is delivery: finding what is wrong, converging on fixes, and proving the result is installable and shippable.

## Verify it yourself

After [installing](/docs/install/), confirm the engine is healthy:

```bash
vibecrafted doctor
vibecrafted version
vibecrafted help
```

Then take the [Quick start](/docs/quick-start/) path to your first workflow run.
