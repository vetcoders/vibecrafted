# docs/public — authoring contract (not published; underscore files are skipped by sync)

Canonical source tree for the public documentation catalog rendered at
`vibecrafted.io/docs` (mirrored into `vibecrafted-io/site/src/content/docs/`;
never edit the mirror).

> **Publication gap — known.** `vibecrafted.dev` does not resolve, and the
> `scripts/sync-docs.sh` mirror script named by earlier revisions of this
> contract does not exist in the repository. Today only `/docs/`,
> `/docs/contributing-skills/` and `/docs/docker/` are live; the remaining
> pages in this tree are authored but unpublished. Restoring the sync path is
> tracked as a Definition of Undone gap. Author against this contract anyway —
> the catalog is the source of truth whether or not the mirror is running.

## Frontmatter (required, exact keys)

```yaml
---
title: "Human-readable page title"
description: "One sentence, plain text, <=180 chars, no markdown."
section: getting-started | concepts | cli | lifecycle | dispatch | server | skills | configuration | troubleshooting | reference
order: 10 # sort key within the section, steps of 10
---
```

The URL slug is the file basename (`install.md` → `/docs/install/`). Basenames
MUST be unique across the whole tree, lowercase, dash-separated.

## Voice and shape (SDK-grade)

- English only. Second person ("you install", "you run"). Present tense.
- Professional, technical, calm. NO hype: never "production ready",
  "blazingly fast", "revolutionary". State what the tool does and how to
  verify it did it.
- Every claim that can be verified gets a command the reader can run.
- Structure per page: 1 short lead paragraph → task-oriented H2 sections →
  code blocks with runnable commands → reference tables where enumerable.
- Cross-link with relative doc links: `[Install](/docs/install/)`.
- Start body with the H1 matching `title`. No footers, no signatures,
  no "synced" lines — the renderer adds chrome.
- Fenced code blocks always carry a language tag (`bash`, `toml`, `json`).

## Deprivatization (HARD RULES — a page violating any of these is rejected)

Never emit:

- Absolute private paths: `/Users/<anyone>`, `/Volumes/<workspace>`, `~/Libraxis`.
  Use `~/.vibecrafted`, `~/.local/share/vibecrafted`, `~/projects/my-app`.
- Hostnames/IPs of private infra: `dragon`, Tailscale `100.x.x.x`, `localhost:3025`
  (the canonical local server example is `http://127.0.0.1:3024`).
- Personal names, GitHub handles, or emails of operators/founders/agent personas.
- Real run ids, session ids, lifecycle ids from internal transcripts. Use
  placeholders: `impl-<timestamp>-<id>`, `life-ship-<timestamp>-<id>`.
- Internal artifact store coordinates with real org/repo: write
  `~/.vibecrafted/artifacts/<org>/<repo>/<date>/…`.
- Internal repo names other than the product itself. Examples use `<org>/<repo>`
  or `my-app`.
- Polish. All prose EN (the site handles i18n separately).

Allowed product vocabulary (these are public concepts, keep them): Living Tree,
Read–Write cadence, control plane, omni-observer, baton, marbles, polarize,
Definition of Undone (DoU), delivery receipt, runtime generation/capsule,
settlement ledger (f/x/n), vc-frame, vc-start, agents: claude · codex · agy ·
junie · grok.

## Truth discipline

Content must be derived from the repository as it IS (docs/, code, CLI help
output) — not from memory. When a behavior is uncertain, verify against source
or omit. Prefer under-promising: document the stable surface, mark evolving
surfaces with "subject to change".
