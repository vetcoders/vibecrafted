# docs/public — authoring standard

Not published: underscore-prefixed files are skipped by the sync. This tree is the
canonical source for the public documentation catalog rendered at `vibecrafted.io/docs`
and mirrored into `vibecrafted-io/site/src/content/docs/`. **Never edit the mirror.**

Sync status and the open publication gap are tracked in
[`docs/runtime/OPEN_THREADS.md`](../runtime/OPEN_THREADS.md).

## Frontmatter (required, exact keys)

```yaml
---
title: "Human-readable page title"
description: "One sentence, plain text, <=180 chars, no markdown."
section: getting-started | concepts | cli | lifecycle | dispatch | server | skills | configuration | troubleshooting | reference
order: 10 # sort key within the section, steps of 10
---
```

The URL slug is the file basename (`install.md` becomes `/docs/install/`). Basenames must
be unique across the whole tree, lowercase, dash-separated.

## Voice and shape

- English only. Second person ("you install", "you run"). Present tense.
- Professional, technical, calm. No hype: never "production ready", "blazingly fast",
  "revolutionary". State what the tool does and how to verify it did it.
- Every claim that can be verified gets a command the reader can run.
- Page structure: one short lead paragraph, then task-oriented H2 sections, then code
  blocks with runnable commands, then reference tables where the set is enumerable.
- Start the body with an H1 matching `title`. No footers, no signatures, no "synced"
  lines — the renderer adds chrome.
- Cross-link with relative doc links: `[Install](/docs/install/)`.
- Fenced code blocks always carry a language tag (`bash`, `toml`, `json`).

## Truth discipline

Content is derived from the repository as it IS — `docs/`, code, CLI help output — not
from memory. When a behavior is uncertain, verify against source or omit. Prefer
under-promising: document the stable surface, mark evolving surfaces "subject to change".

## Privacy

Leak rules are not prose here; they are enforced by the `vc-deprivatize` skill, which
classifies findings by severity (private paths, personal emails and handles, real run and
session ids, private hostnames, internal repo names, placeholder drift) and ships a
deterministic scanner. Run it before any page goes public.
