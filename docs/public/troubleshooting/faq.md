---
title: "FAQ"
description: "Answers to the questions engineers ask before they trust the runtime — installation, agents, workflows, and licensing."
section: troubleshooting
order: 30
---

# FAQ

Direct answers to the questions people ask before adopting Vibecrafted. For
task-oriented help, start with [Quick start](/docs/quick-start/); for failure
diagnosis, see [Doctor](/docs/doctor/) and
[Common issues](/docs/common-issues/).

## Installation

**Why does Vibecrafted install into `~/.vibecrafted/` and
`~/.local/share/vibecrafted/`?**
`~/.vibecrafted/` is the state root — the control plane, artifact store, and
run metadata. `~/.local/share/vibecrafted/` holds the installed runtime
generations the CLI actually executes. Agent-specific directories are views
onto that store, never a second truth.

**Can I install without editing my shell config?**
Yes. Opt out of shell-helper installation and source
`${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh` manually in the
sessions where you want the helpers.

**Is there a guided install path?**
Yes — the installer ships a browser-based wizard
(`curl … | bash -s -- --gui`) and a terminal-native wizard (`make install`
from a checkout). Both stage the same runtime; the GUI is a front-end over
the identical install truth. See [Install](/docs/install/).

**Which install path should I use in CI?**
The non-interactive path: `make install-auto`, or the installer script with
`--non-interactive` when you need full CLI control.

**I pulled new commits — is my install updated?**
No. The daily CLI runs the installed runtime generation, not your floating
git checkout. Re-run the installer and confirm `vibecrafted version` and
`vibecrafted receipt` report the `+g<sha>` you expect. See
[Update & rollback](/docs/update/).

## Agents and skills

**What is the difference between a skill and an agent?**
An agent is the runtime that does the work (claude, codex, agy, junie, grok, cursor).
A skill is the workflow protocol that tells the agent how to behave for a
specific engineering phase.

**Why not just use one giant prompt?**
Vibecrafted targets system-shaping, not chat convenience: structural
perception of the repository, decision retrieval, convergence loops, and
shipping audits — each is a separate, verifiable stage rather than a hope
embedded in a prompt.

**What is marbles?**
The convergence loop: implement, follow up, measure, repeat — until the
classes of findings that matter reach zero. See
[Workflow launchers](/docs/workflow-launchers/).

**What is Definition of Undone?**
The audit that checks whether people can discover, install, understand, and
trust the product — not only whether the codebase is healthy. It runs as the
`dou` stage of the [ship lifecycle](/docs/lifecycle-overview/).

## Workflows and operations

**When do I use `implement` vs `justdo`?**
`implement` is the structured ship-stage WRITE cut: end-to-end delivery with
follow-up and marbles built in. `justdo` is a standalone posture — the prompt
defines the task type, with no lifecycle ceremony. They are distinct skills,
not aliases.

**When do I use `review` vs `followup`?**
`review` takes a bounded target — a PR, branch, commit range, or artifact
pack. `followup` audits direction after an implementation: gaps, drift, and
the next highest-leverage move.

**Can I run Vibecrafted in CI/CD?**
Yes. Installation has a non-interactive path, and the review, follow-up, and
release flows are shaped as repeatable gates.

**What lives in `~/.vibecrafted/artifacts/`?**
Plans, reports, transcripts, and run metadata, keyed by
`<org>/<repo>/<date>`. The artifact store exists so agent work leaves durable
evidence a human or another agent can verify later.

## Licensing

**Is Vibecrafted open source?**
It is distributed under the Business Source License 1.1: source-visible and
usable, but not a permissive OSS license.

**Can small teams use it in production?**
Yes. The Additional Use Grant covers individual developers and teams smaller
than five people, provided they are not offering a competitive hosted or
embedded product.

**What if I need broader commercial rights?**
The LICENSE file in the repository carries the exact terms and the contact
path for alternative licensing arrangements.
