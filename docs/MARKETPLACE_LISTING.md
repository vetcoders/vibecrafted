# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.

Release engine for AI-built software.

## What it does

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. starts where code generation stops. It takes the repo your AI
agents already produced and pushes it through structural mapping, convergence
loops, install truth, packaging, and launch-readiness checks until the product
is fit to ship.

The framework asks one question, relentlessly: _What is still wrong?_
And keeps going until the answer is: _Nothing._

## When to use

- You want AI agents to work in a live repository, not isolated toy contexts
- You need a repeatable path from AI draft to release readiness
- You want Claude, Codex, and Gemini to collaborate with durable artifacts
- You need convergence to quality, not just more generated code
- You need the product surface finished: install, docs, presence, and trust

## How it works

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. breaks engineering into three phases:

**Craft** — orient, examine, research, implement. Skills like `vc-init`,
`vc-workflow`, and `vc-scaffold` give agents structured protocols for
understanding the codebase before touching it.

**Converge** — find what is wrong, fix it, observe the cascade, repeat.
`vc-marbles` runs adaptive convergence loops (solo, duo, trio, multi-agent)
until no tool can generate a single accusation against the code.

**Ship** — check the product surface, not just the code. `vc-dou` measures
what is undone. `vc-decorate` adds brand polish. `vc-hydrate` packages for
market. `vc-release` deploys.

## Requirements

- macOS, Linux, native Windows (win32-x64 Runtime Pack), or Windows through WSL2
- Today: bootstrap installer on macOS/Linux/WSL2
  (`curl -fsSL https://vibecrafted.io/install.sh | bash`). It installs the
  version named in `https://vibecrafted.io/channel/main.json`.
  Signed `Vibecrafted.app` DMG when a GitHub Release actually attaches one.
  Native Windows: the win32-x64 Runtime Pack via `install.ps1 -Pack`; until a
  release publishes that carrier, build it from a checkout. WSL2 remains the
  POSIX alternative.
- Git, Python 3.11+, `make` for the source-checkout path
- One or more agent CLIs: Claude Code, Codex, or Gemini CLI
- Required foundations: Loctree (structural analysis), AICX (session history)

## Pricing

Free for personal use and startups. No limits on repos or agents.
Enterprise: info@vibecrafted.io

## Part of

Vetcoders 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Suite — structured AI-guided engineering,
convergence, packaging, and release.

## Registry Metadata

- description: Release engine for AI-built software. Structural mapping, convergence loops, install truth, and launch-ready packaging for AI-generated repos.
- keywords: ai-agent-engineering, release-engineering, developer-tools, codex, claude, gemini, workflow, multi-agent, ai-native
- homepage: https://vibecrafted.io/
- repository: https://github.com/vetcoders/vibecrafted
- documentation: https://vibecrafted.io/en/quickstart/
- faq: https://vibecrafted.io/en/faq/
- license: Business Source License 1.1 (SPDX: BUSL-1.1) with Additional Use Grant for non-competitive small-team production use

---

`//𝚟𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.`
