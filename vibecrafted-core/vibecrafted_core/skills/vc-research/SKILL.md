---
name: vc-research
version: 1.3.0
description: >
  Standalone triple-agent research skill. Co-define the problem with the user,
  write a research plan, then spawn claude + codex + agy simultaneously on the
  same questions. Three independent reports come back. Synthesize into one
  gap-free research document ready for implementation. Use whenever the team
  needs ground truth before coding: unknown APIs, architecture decisions, library
  assessment, protocol research, best-practice survey, competitive analysis,
  or any situation where one agent's perspective is not enough. Trigger phrases:
  "research this", "zbadaj to", "triple research", "research swarm", "3 agenty
  research", "gap-free research", "zbadaj przed implementacją", "co mówi
  dokumentacja", "state of the art", "SoTA research", "porównaj podejścia",
  "analyze options", "research plan", "plan researchu".
compatibility:
  tools:
    - Bash
    - Read
    - Write
    - Agent
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-research` (launcher `research`)**
>
> Same three-path _shape_ as the fleet, with **this** skill's literals — see the
> canonical [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                                                                    |
> | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | `vibecrafted research <agent(s)>`                                                                                                         |
> | 2. Interactive          | `/vc-research` — execute **in this session**; use native subagents when required; do **not** externalize merely because a launcher exists |
> | 3. Agent-operator       | may dispatch the worker form above via `vc-dispatch` / operator lines while preserving this skill's identity                              |
>
> **Note:** May default to multi-agent swarm; interactive still must not no-op into empty re-dispatch.

> Freer native on some runs ≠ abandon external fleet. `vc-dispatch` and `vc-ship` keep their own identities.

<!-- /fleet-imperative -->

# vc-research — Triple-Agent Research Swarm

> One perspective is an opinion. Three perspectives are evidence.

## Operator Entry

### Living Tree / Worktree Rule

This workflow runs in the operator's current checkout and current branch. Do not create, switch to, or move execution into a git worktree unless the operator explicitly asks for a worktree in this prompt. Generic words like "isolate", "parallel", or "clean branch" are not enough. The one sanctioned second mode is a Fleet Worktree dispatch (written plan, pre-committed verifiers, disjoint domains, single-thread integrator — see Living Tree Rule, Mode B); outside that formation, stay in the shared tree. Re-read files before editing, adapt to concurrent changes, and report a substrate failure if the current tree is too poisoned to continue safely.

See [Living Tree Rule](../LIVING_TREE_RULE.md).

## Canonical Orientation Gate

Before this workflow performs repo-specific analysis, planning, implementation, review, release, or delegation, it MUST run or consume the `vc-init` procedure for the assigned repo. If fresh `vc-init` evidence is absent, perform the init pass first and treat workflow-specific work as blocked until repo truth exists.

`Loctree:loctree` is the default structural perception skill for that pass. Use Loctree before grep or docs-driven claims to produce or refresh the Code-Derived Application Map: repo-view, focus, slice, impact, find, and follow as relevant. Search for existing symbols and contracts before creating new ones; run impact before delete or major refactor; run slice before editing.

The point is to find the hooks: load-bearing hubs, twins, dead code, drift, runtime entrypoints, and blast-radius traps. If the task is explicitly non-repo or no-code, state the no-repo exception in the report. Otherwise, missing `vc-init`/Loctree evidence is a process failure.

Enter the framework session via `vibecrafted start` (or `vc-start`). Then launch through the command deck — never raw `skills/.../*.sh` paths:

```bash
vibecrafted research --prompt 'Compare auth libraries for Tauri desktop'
vc-research --prompt 'State of the art for MCP streaming transports'
vibecrafted research --file /path/to/research-plan.md
vc-research codex agy --prompt 'Override lanes for this run'
```

Research defaults to detached headless lanes both inside and outside vc-frame.
Prefer `--file` for an existing plan and `--prompt` for inline intent. Use an
explicit terminal runtime only for a TTY-required compatibility lane.

**Critical swarm semantics:** one invocation launches the research swarm. Do not call the command once per agent. `vibecrafted research <agent>` is the backward-compatible synthesizer-pick form; it does not mean "research only with this agent." `vc-research <agent1> [agent2 agent3] --prompt/--file ...` is the explicit lane override form for one to three lanes; the first listed agent synthesizes unless `--synthesizer <agent>` is provided.

Runtime lane defaults are read at launch time from `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/config/research.yaml`. Missing file means built-in defaults: `claude`, `codex`, `agy`, with no model pins and last-survivor synthesis. The packaged commented example lives at `runtime/vc-research/research.yaml.example`; copy it into the runtime config dir to edit operator policy without touching the repo or reinstalling.

<details>
<summary>Foundation Dependencies</summary>

- [vc-loctree](../foundations/vc-loctree/SKILL.md) — structural awareness
- [vc-aicx](../foundations/vc-aicx/SKILL.md) — intentions and steerability

</details>

## Repository Work Doctrine

For repository work, start with Loctree as the map: use `loct context`,
`loct occurrences`, `loct body`, and `loct find --literal` before broad manual
search. Use AICX for intent and session context. Use rg/grep as fallback or
local magnifier, not as a replacement for structural mapping. If Loctree fails
or misses a surface, append feedback to `~/.vibecrafted/loctree/loctree-fail.md`.

## Purpose

Research a problem from three independent angles before writing code. The orchestrating agent co-defines the problem with the user, writes a plan, spawns claude + codex + agy on the same questions, then synthesizes findings into one gap-free document. This is the Research phase from `vc-workflow`, extracted as a standalone skill and upgraded with triple-agent triangulation.

## When To Use

- Unknown API, protocol, or library
- Architecture decision with multiple valid approaches
- "What is the current best practice for X?"
- Library assessment (A vs B vs C)
- Integration research (how does X talk to Y?)
- Any moment where guessing would be cheaper than being wrong

**Do NOT use for:**

- Questions answerable by reading one file in the repo
- Problems where loctree slice + grep gives the answer in 30 seconds
- Pure implementation tasks (use `vc-workflow` via `vc-agents`; `vc-delegate` only for small model-agnostic work)

## Research Safety

Research mode is **read-only** for the source repository.

- **Closure marker = filesystem artifacts**, not git. The run directory under `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/research/<run_id>/` with `report.md` + `meta.json` + `transcript.log` is the deterministic anchor. Operator verifies via `ls`, `cat meta.json | jq .status`. No git commits needed.
- **No source mutation.** Do not edit repo source, config, `.gitignore`, or generated files unless the operator plan explicitly asks.
- **No git writes.** No stage, commit, amend, tag, branch, merge, rebase, push, stash, clean, reset, checkout, switch. Working tree unchanged at end. Empty commits / `--allow-empty` / chore stamps — forbidden.
- If research discovers an obvious fix, write the proposed fix and file references to the report artifact instead of applying it.
- Codex workers must write the full markdown report to the given report path through the filesystem before exiting. The `codex exec --output-last-message` final message is only a completion note, not the durable report.

## The 6-Step Research Flow

### Step 1 — Co-define the problem

Talk with the user. Do not write a plan yet. Establish:

- **What we need to know** — the actual question, not the symptom
- **Why** — what decision depends on this answer
- **What we already know** — priors, prior art in the repo
- **Boundaries** — what is out of scope

Output: a 3-5 sentence problem statement agreed with the user.

### Step 2 — Write the research plan

Create one plan file. Every agent receives this plan:

```markdown
---
run_id: <generated-unique-id>
agent: <claude|codex|agy|cursor>
skill: vc-research
project: <repo-name>
status: in-progress
---

# Research Plan: <title>

## Problem

<co-defined problem statement>

## Questions

1. <specific, answerable question>
2. ...

## Mandatory tools

- loctree MCP (repo-view, slice, find, impact) — for codebase questions
- Brave Search or WebSearch — for external ground truth

## Encouraged tools (agent's choice)

- Context7 (resolve-library-id → query-docs) — for library docs
- WebFetch — for URLs found via search
- Codebase grep — for internal patterns (only after loctree mapping)

## Report format

Each question answered with: **Sources**, **Finding**, **Confidence** (high/medium/low), **Evidence**.
Conclude with **Synthesis**: recommended approach, alternatives, open questions, implementation notes.

## Constraints

- Append current year to search queries for freshness
- Prefer primary sources (official docs, RFCs, source code) over blog posts
- If two sources disagree, note the disagreement explicitly
- Do not hallucinate API signatures — verify them
```

`vc-research` records the effective plan under `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/research/<run_id>/plans/<ts>_<slug>_research-plan.md`. Plans can be split for separable domains, but each agent gets ALL plans — they are independent researchers, not specialists.

### Step 3 — Spawn the research swarm

```bash
PLAN="$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<ts>_<slug>_research-plan.md"
vc-research --file "$PLAN"
```

Repo-owned spawn scripts remain the internal engine. Do not document raw `bash skills/...spawn.sh` paths as the operator entrypoint.

The launcher keeps one common `run_id` and starts the configured headless lanes
against the same plan. Defaults are claude + codex + agy. vc-frame may project
the run; the explicit terminal compatibility lane uses one shared
`research.kdl` tab. Divergence between reports reveals blind spots.

Supported invocation forms:

```bash
# Canonical agentless form: full configured swarm.
vibecrafted research --prompt "State of the art for MCP streaming"
vibecrafted research --file "$PLAN"

# Backward-compatible core form: full configured swarm, claude synthesizes.
vibecrafted research claude --file "$PLAN"

# Shell override form: exactly these lanes for this run; codex synthesizes.
vc-research codex agy --prompt "Compare two toolchains"

# Explicit synthesizer override.
vc-research claude codex agy --synthesizer claude --file "$PLAN"
```

Runtime YAML schema:

```yaml
lanes:
  - agent: claude
    model: claude-opus-4-6
    enabled: true
  - agent: codex
    model: gpt-5.5
    enabled: true
  - agent: agy
    # Unsupported model flags are reported honestly in receipts.
    model: gemini-pro
    enabled: true
lane_count: 3
synthesizer:
  agent: claude
  model: claude-sonnet-4-6
```

Precedence is: built-in defaults < legacy TOML fallback < runtime YAML < env/positional override < per-run `--model` flags. Unsupported model flags are not silently dropped; the receipt records `model_override_supported: false`, `model_override_skipped: true`, and `model_override_skip_reason: unsupported_agent_model_flag`.

Immediately after spawn, the operator gets a launch card with shared `run_id`, run directory, reports directory, summary path, and the exact await command. **The launch card is the default surface.** `observe --last` is a drilldown tool, not the primary source of truth.

### Step 4 — Collect reports

Reports land in:

```
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/research/<run_id>/reports/{claude,codex,agy}.md
```

Launch card lives at `research/<run_id>/summary.md`. Metadata, transcripts, raw streams, prompts, launchers, vc-frame layout stay inside `research/<run_id>/logs/` and `research/<run_id>/tmp/`.

Wait for all three through the dedicated runtime helper:

```bash
vc-research-await --run-id <run_id>
vc-research-await --last     # newest swarm
```

For transcript-level inspection while the swarm is running:

```bash
vibecrafted observe claude --last
vibecrafted observe codex --last
vibecrafted observe agy --last
```

Do not treat manual `observe --last` calls as sufficient observability. Workflow state goes through launch metadata, the await helper, and durable report paths by default.

### Step 5 — Synthesize

**Before citing a single line of any source report, you MUST have read each report in full via layered slicing.** Non-negotiable.

Most reports run 30-100KB. Tools cap output at ~25KB and dump the rest to a file with a "see path: ..." warning. Skipping that file because it's "long" or working only from the warning text is the failure mode this skill exists to prevent. A synthesis built from truncation warnings is a hallucination wearing the costume of expertise.

Per source report:

1. Read in full via offset/limit slicing in spans of ~1500-2000 lines (or ~80,000 chars).
2. Record coverage in synthesis section "0. Coverage statement" — lines/bytes per source report.
3. If a report is too large for the available budget, **HALT** and report the boundary. Do NOT cite line ranges you have not actually read.

**Synthesis = operator's expert opinion built ON the three reports, NOT a copy.** Two sections: **A. Convergent (deduplicated)** and **B. Signals (single-agent findings — potentially key insights)**. Voting/majority rules explicitly rejected.

- **A. Convergent** — findings where two or three reports overlap, reduced to one statement. Cite agreeing reports with file:line. If one didn't address the question, note explicitly (silence ≠ disagreement).
- **B. Signals** — findings surfaced by only one agent. NOT lower-priority. Often the actual direction the work needed. Per signal: what (file:line) + why others missed it + operator verdict (amplify / flag / acknowledge & reject) + reasoning.

### Step 6 — Produce the synthesis document

Write the synthesis to `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/research/<run_id>/synthesis.md` in the run directory. **The three source reports remain as individual files in the same directory — DO NOT inline them.**

> See [references/synthesis-template.md](references/synthesis-template.md) for the full document template, frontmatter, section structure, and operator imperatives.

Operator non-negotiables:

1. The synthesis does NOT contain verbatim content from the reports — only file:line citations to them.
2. The reports remain as separate files in the run directory. Immutable expert testimony.
3. Every nontrivial thesis in the synthesis MUST have a file:line reference to at least one report.
4. Dissent is cited with file:line to both/all sides + reasoned judgment.
5. The synthesis is short (usually 3–8KB). Its value = quality of interpretation + precision of citation.

Present the synthesis to the user. This is the input for `vc-workflow` Phase 3 (Implement) or standalone implementation.

## Pipeline Integration

vc-research can be used:

- **Standalone** — research without a full ERi pipeline
- **As workflow Phase 2** — `vc-workflow` delegates here instead of single-agent research
- **Before vc-partner** — when partner mode needs ground truth before debug
- **Before vc-runtime/vc-delegate** — research feeds implementation plans

```
         ┌─── claude ──→ report ───┐
research │                         │
  plan ──├─── codex  ──→ report ───├──→ synthesis.md
         │                         │
         └────── agy ──→ report ───┘
```

## Anti-Patterns

- Passing `claude|codex|agy` to `vc-research` (defeats the purpose — the launcher is the swarm)
- Giving each agent different questions (they must answer the SAME questions for triangulation)
- Running `vc-research` three times for claude/codex/agy; one invocation already launches the swarm
- Skipping synthesis and concatenating reports (the value is in the delta)
- Researching things you can verify by reading one file (use loctree slice)
- Writing the research plan without the user (Step 1 is collaborative)
- Trusting blog posts over official documentation
- Letting agents research without loctree context (they ask wrong questions)
- Jumping to raw `*_spawn.sh` invocations when `*-research` exists in the real shell helper surface
- Patchwork meta-artifact synthesis (verbatim concat of 3 reports)
- Compressed-view synthesis (operator paraphrase only, no file:line refs)

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
