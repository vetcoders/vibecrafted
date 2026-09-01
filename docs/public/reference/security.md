---
title: "Security"
description: "Security posture: local-first data, verified installs, secret hygiene gates, known boundaries, and how to report a vulnerability."
section: reference
order: 30
---

# Security

Vibecrafted runs AI agents with real shell access on your machine, so its
security posture is deliberately conservative where it counts: data stays
local, installs are verified against hashes, secret hygiene is gated
before commits, and the sharp edges are documented instead of hidden.

## Reporting a vulnerability

If you discover a security vulnerability, report it responsibly.
**Do not open a public issue.**

Email: **hello@vetcoders.io**

Include:

- a description of the vulnerability;
- steps to reproduce;
- an impact assessment (what can an attacker do?);
- the affected skill(s) or script(s).

You will receive an acknowledgement within 48 hours and an initial
assessment within 7 days.

In scope: skill definitions (`vc-*/SKILL.md`), install and spawn scripts,
installed shell helpers, and CI workflows. Out of scope: the foundation
binaries (Loctree, AICX, PRView) and the vendor agent CLIs — report those
to their respective projects.

## Local-first data

The framework's state lives on your machine, not on a vendor server:

| Data                           | Location                                               |
| ------------------------------ | ------------------------------------------------------ |
| Run lifecycle events and state | `~/.vibecrafted/control_plane/`                        |
| Settlement ledger              | `~/.vibecrafted/control_plane/settlement_ledger.jsonl` |
| Plans, reports, transcripts    | `~/.vibecrafted/artifacts/<org>/<repo>/<date>/`        |
| Installed runtime              | `~/.local/share/vibecrafted/`                          |

The agent CLIs themselves (claude, codex, agy, junie, grok, cursor) talk to their
vendors' APIs under their own terms; Vibecrafted orchestrates them but
adds no additional telemetry backend of its own.

## Verified installs

Foundation binaries are acquired prebuilt-first — npm packages, signed
GitHub release assets, crates.io, PyPI — with source builds as an explicit
last fallback. The installed framework runtime is bound to a manifest with
required SHA-256 digests, and `vibecrafted doctor` fails closed when a
manifest-bound file has drifted or a launcher resolves outside the
installed tree. See [Runtime capsule](/docs/runtime-capsule/).

```bash
vibecrafted doctor
```

## Secret hygiene gates

- **Semgrep is the first security guard.** The default invocation is
  `make semgrep`, mirrored by the repository's `pre-commit` and `pre-push`
  hooks, so a secret-shaped string or a dangerous pattern is caught before
  it leaves your machine.
- **`--no-verify` is forbidden** for commits and pushes in every worker
  plan. A gate that can be skipped silently is not a gate.
- **No secrets in the repo.** Skills read credentials from environment
  variables only; `.env` files are never committed, and logs must not
  contain secrets.

## Known security boundaries

These are by design and worth understanding before you deploy the
framework in a shared environment:

- **Spawn scripts load your full shell environment.** Agent workers need
  the real environment (PATH, credentials-by-env, toolchains). Do not run
  spawns in untrusted environments.
- **External agents run with elevated permission flags.** Dispatching an
  external agent CLI headlessly requires that vendor's
  skip-permission-prompt flag. This is documented and intentional; the
  in-process delegation skill exists as the safer alternative when you
  want tighter sandboxing.
- **Untrusted text is never executed.** The messaging gateway treats chat
  as transport plus envelope store; it never executes untrusted text
  through a shell.

## Operational guardrails

Beyond the gates, the runtime doctrine keeps destructive authority with
the human:

- Workers commit locally; **push, merge, and release are operator
  buttons**, never taken autonomously by a dispatched worker.
- Git history is never rewritten unless the user explicitly asks.
- Release reports require security-gate evidence and an exposed-surface
  inventory before a release is considered done.

## Supported versions

| Version          | Supported   |
| ---------------- | ----------- |
| Latest on `main` | Yes         |
| Older commits    | Best effort |
