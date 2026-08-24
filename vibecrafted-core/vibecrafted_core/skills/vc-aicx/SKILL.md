---
name: aicx
version: 3.0.0
description: >
  An Intention Retrieval Engine for Agents' sessions. aicx (formerly
  ai-contexters) is a sophisticated parser tool that recovers and keeps the
  central history of agents' sessions in both human- and agent-readable format.
  Additionally it provides ad-hoc mode to recover agent output that is too large to
  read or is unreadable. Works on any Claude Code, OpenAI Codex, Gemini JSON,
  JSONL-format file regardless of extension (.jsonl, .txt, .output). Generates
  output path automatically — no -o flag needed.
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

## When To Use

Pull historical context from previous AI sessions for this project. We are looking
for the _why_, not just a blind dump of _how_.

## Canonical Orientation Gate

For repo-specific work, run or consume the `vc-init` procedure before turning
AICX memory into a recommendation. `Loctree:loctree` is the default structural
perception skill for that pass; use it to produce or refresh the
Code-Derived Application Map before trusting older intent.

If fresh `vc-init` evidence is absent, perform the init pass first and treat
repo-specific recommendations as blocked until repo truth exists.

AICX explains why prior agents moved. Loctree and current repo gates decide
whether that intent is still true.

## Repository Work Doctrine

For repository work, start with Loctree as the map: use `loct context`,
`loct occurrences`, `loct body`, and `loct find --literal` before broad manual
search. Use AICX for intent and session context. Use rg/grep as fallback or
local magnifier, not as a replacement for structural mapping. If Loctree fails
or misses a surface, append feedback to `~/.vibecrafted/loctree/loctree-fail.md`.

## The toolset:

1. `aicx` (cli) and `aicx-mcp` (stdio and streamable-http):
   a)the mcp reference: - `mcp_aicx_aicx_rank`
   Rank stored AI session chunks by content quality. Shows signal density, noise ratio, and quality labels (HIGH/MEDIUM/LOW/NOISE) per chunk. Use --strict
   to filter noise. - `mcp_aicx_aicx_search`
   Fuzzy search across stored AI session chunks. Returns quality-scored results
   with matched lines. Supports Polish diacritics normalization and optional
   project filtering. - `mcp_aicx_aicx_steer`
   Retrieve stored chunks by steering metadata (frontmatter fields).
   Filters by run_id, prompt_id, agent, kind, project, and/or date range using
   sidecar metadata — no filesystem grep needed. Returns chunk paths with their
   sidecar metadata for selective re-entry.
   b) The cli reference: - the full reference can be retrieved by calling `aicx --help`.
   c) The older methods - **`aicx_refs(hours=<retrieval_hours>, project="<project>", strict=true)`** — list stored context files - **`aicx_rank(project=<project>, hours=168, strict=true, top=5)`** — prioritize densest chunks

   > These are older entry points. Prefer `aicx_search`; it provides the same functionality and more.

2. `aicx intents` (cli):
   Extracts project intents, outcomes, tasks, and architectural decisions from session histories into structured formats.
   - Example extraction: `aicx intents -p <ProjectName> --emit json | tee intents.json`
   - Summarize with jq: `jq 'map(.kind) | group_by(.) | map({kind: .[0], count: length})' intents.json`
   - List recent intents: `jq -r '.[] | select(.kind == "intent") | "[\\(.date)] \(.agent): \(.summary[0:150])..." ' intents.json | sort -r | head -n 15`

## What to understand:

- What was the original intention behind the architecture?
- What duct-tape was applied late at night to "just make it work"?

## The discipline:

AICX is an intention-retrieval engine, not a blind RAG cannon.
Retrieve the context of the decisions, then verify their current truth in Sense 2.

## The output structure:

```
[1-100/100 <score_range>] <org>/<repo> | <agent> | <date>
session(s): <session_id>
cwd: <cwd>
search result:
  > <result>
  > - <file_path>
  > [HH:MM:SS] assistant: <result>
  > [HH:MM:SS] user: <result>
source file(s):
$HOME/.aicx/store/<org>/<repo>/<date>/<type>/<agent>/<session_id>.md
```

## The extract tool use when you cannot read an agent's result directly:

- Output too large for Read tool (>10k tokens)
- Tool-results file is raw JSONL, not human-readable
- Subagent crashed but left a partial log
- Previous session context needed before starting work

1.  The Command

```bash
aicx extract --format {claude,codex,gemini,ollama} <INPUT_FILE> -o /tmp/aicx-extract-<basename>.md
```

`--format claude` parses Claude Code JSONL as well as Gemini json structure.
File extension does not matter — `.jsonl`, `.txt`, `.output` all
work the same.

**Output path**: Derive from input filename. Use the input file's basename (without extension) as the output name:
`/tmp/aicx-extract-<basename>.md`. Never ask the user for an output path.

## Where To Find Input Files

```
$HOME/.claude/projects/<project>/<session-id>/tool-results/<hash>.txt     # Agent result (most common)
$HOME/.claude/projects/<project>/<session-id>/subagents/agent-<id>.jsonl  # Subagent session
/private/tmp/claude-<uid>/<project>/tasks/<task-id>.output               # Background task
$HOME/.claude/projects/<project>/<uuid>.jsonl                             # Full session
```

## Useful Flags

| Flag                       | Effect                             |
| -------------------------- | ---------------------------------- |
| `--conversation`           | User/assistant only, no tool noise |
| `--max-message-chars 8000` | Truncate long messages             |
| `--user-only`              | Only user messages                 |

## Example Recovery Flow

```bash
# 1. Extract (output path derived automatically from input basename)
aicx extract --format claude \
  $HOME/.claude/projects/-Users-foo-myrepo/abc123/tool-results/xy9z.txt \
  -o /tmp/aicx-extract-xy9z.md

# 2. Read the result
Read /tmp/aicx-extract-xy9z.md
```

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
