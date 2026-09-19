# Onboarding Stack — new human or agent into Vibecrafted

Hand this file (with the skill) to a new team member — human or agent. Order
matters: posture first, tools second, parity last.

## 1. Posture before tools

Read the charter skill: [vibecraftsmanship](../../vibecraftsmanship/SKILL.md)
(the 5th charter — how to _think about acting_). The short version a new member
must be able to repeat:

- Runtime truth beats theoretical correctness; product truth beats local elegance.
- Loctree is the structural map; AICX is the intent memory; Vibecrafted is the proof discipline.
- Done is a market condition (DoU), not a green gate.
- One throne per truth — no parallel systems, no wrappers around competitors.

## 2. Canonical upstream installers

Per the "Own Only Your Namespace" doctrine: foundations come from **their own**
canonical releases, never vendored copies or PATH wrappers.

| Tool           | Canonical install                                                                      | Verify                  |
| -------------- | -------------------------------------------------------------------------------------- | ----------------------- |
| loct / loctree | `curl -fsSL https://loct.io/install.sh \| sh`                                          | `loct --version`        |
| vibecrafted    | clone `vetcoders/vibecrafted`, `make install` (or the release DMG for the app surface) | `vibecrafted --version` |
| aicx           | shipped with the vibecrafted stack (`aicx-mcp` on PATH)                                | `aicx-mcp --help`       |
| agent CLIs     | each vendor's installer (Claude Code, Codex, Grok, Kimi Code)                          | `<cli> --version`       |

Secrets live in the macOS Keychain or each CLI's own credential store — never in
shared config files, never copied between configs "for parity".

## 3. Config parity (this skill's core)

Once the CLI runs, bring it to fleet posture:

```bash
uv run tools/fleet_scan.py scan --output ./posture.json
uv run tools/fleet_scan.py diff --target <cli> --output ./drift.json
```

Then follow the SKILL.md onboarding workflow: docs → map → smoke-test →
validated apply → parity report. The known equivalences live in
[fleet-config-map.md](fleet-config-map.md).

## 4. First-run checklist for the new member

- [ ] `loct --version`, `vibecrafted --version`, `aicx-mcp --help` all answer
- [ ] the agent CLI starts and its config passes its native validator
- [ ] `diff --target <cli>` shows no `divergent` axes (or each divergence has a recorded reason)
- [ ] doctrinal hooks fire: run a standalone `rg` inside the working repo and watch `loctree-first-guard` block it
- [ ] the member can name the fleet posture: full-auto permissions, top-tier effort, dark theme, Polish discovery language, shared MCP (aicx / loctree / playwright / context7)

## Roadmap

Full unattended install of the stack for a brand-new machine is the declared
next cut of `vc-hello` — today this file is the curated, doctrine-safe manual path.
