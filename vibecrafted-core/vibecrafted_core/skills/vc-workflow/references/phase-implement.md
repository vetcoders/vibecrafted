# Phase 3: IMPLEMENT — Agent Delegation with Context

## Purpose

Translate Examination + Research into precise agent plans.
Spawn agents that inherit full pipeline context.
The key differentiator: agents receive structural intelligence, not just task descriptions.

## The Loctree Instruction Rule

**Benchmark-proven**: Agents instructed to use loctree MCP achieve 98% task completeness
vs 85% without instruction. This is NOT optional.

Every agent plan MUST include this preamble:

```
## Structural Intelligence (loctree MCP)

Use loctree MCP tools as your primary exploration layer:
- `repo-view(project)` first for codebase overview
- `slice(file)` before modifying any file — understand dependencies + consumers
- `find(name)` before creating new types/functions — avoid duplicates
- `impact(file)` before deleting or major refactoring — know the blast radius
- `focus(directory)` to understand a module before changing it

Never edit code without mapping it first.
Grep/rg is for local detail only — after structural mapping.
```

## Agent Plan Construction

### From Pipeline Artifacts

Each agent plan should include relevant sections from:

1. **CONTEXT.md** (Examination):

   - Critical files relevant to agent's scope
   - Risk map for files agent will touch
   - Existing symbols to reuse

2. **RESEARCH.md** (Research):
   - Implementation guidance section
   - Code examples from authoritative sources
   - Dependencies to add
   - Pitfalls to avoid

### Plan Template (ERi-enhanced)

```markdown
# Task: <short title>

## Structural Intelligence (loctree MCP)

[loctree preamble — always include]

## Pipeline Context

### From Examination (CONTEXT.md):

- Critical files: <relevant subset>
- Risk: <relevant risk items>
- Existing patterns: <symbols to reuse>

### From Research (RESEARCH.md):

- Chosen approach: <architectural decision>
- Key API: <usage pattern from research>
- Pitfalls: <what to avoid>

## Goal

- <1-3 bullets>

## Scope

- In scope: <files/areas>
- Out of scope: <explicit boundaries>

## Acceptance

- [ ] <objective, testable outcome>
- [ ] <objective, testable outcome>
- [ ] Refinement: review changed files with `slice(file)` to verify no broken consumers

## Test Gate

- <repo-specific commands: make check, cargo clippy, etc.>

## Living Tree Note

- Work on a living tree with 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚜𝚖𝚊𝚗𝚜𝚑𝚒𝚙 methodology — concurrent changes expected.
- Adapt proactively, but never skip quality, security, or test gates.
- If blocked, report exact blocker and run closest safe equivalent.
```

## Delegation Strategy

### Task Splitting

Split implementation into independent, parallel-safe units:

| Pattern             | Split By             | Example                                    |
| ------------------- | -------------------- | ------------------------------------------ |
| Feature layers      | core → app → tests   | Backend types, UI integration, E2E tests   |
| Independent modules | module boundary      | auth changes, API changes separately       |
| Read/Write          | research → implement | One agent researches, another implements   |
| Risk levels         | safe → risky         | Safe refactors first, risky changes second |

### Agent Count Heuristics

- **1 agent**: Simple fix, single module, <200 LOC change
- **2 agents**: Feature with backend + frontend, or implementation + tests
- **3+ agents**: Large refactor, multi-module feature, complex migration

### Canonical Artifact Structure

```
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/
├── plans/
│   ├── <ts>_<slug>_CONTEXT.md
│   ├── <ts>_<slug>_RESEARCH.md
│   ├── <ts>_<agent-task>.md
│   └── ...
├── reports/
│   ├── <ts>_<agent-task>_<launcher>_<agent>.md
│   └── ...
└── tmp/
```

Repo-local `.vibecrafted/plans` and `.vibecrafted/reports` are convenience symlinks only.

## Spawn Commands

Use the portable scripts from `runtime/scripts/`. These handle artifact
generation, launch mode selection (headless by default; terminal only when
explicitly requested), and execution
environment setup automatically.

### Codex (default for implementation)

```bash
ARTIFACT_DAY="$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>"
PLAN="$ARTIFACT_DAY/plans/<ts>_<agent-task>.md"

bash runtime/scripts/codex_spawn.sh "$PLAN" --mode implement
```

### Claude (for complex reasoning tasks)

```bash
bash agents//claude_spawn.sh "$PLAN" --mode review
```

### Gemini

```bash
bash agents//agy_spawn.sh "$PLAN" --mode implement  # gemini deprecated; agy is the Google-family runtime
```

> The scripts default to detached headless execution on every platform.
> Pass `--runtime terminal` only for a provider path proven to require a TTY.

If the optional zsh helper layer is installed, the same actions become:

```bash
codex-implement "$PLAN"
claude-review "$PLAN"
gemini-implement "$PLAN"
```

## Review Protocol

After agents complete:

### 1. Collect Reports

Read all reports from
`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/`.

### 2. Quality Gate

Run repo quality commands:

- Rust: `cargo clippy -- -D warnings && cargo test`
- General: `make check` or equivalent

### 3. Structural Verification

For each changed file:

- `slice(file)` — verify no broken consumers
- `impact(file)` — confirm blast radius acceptable
- Cross-reference with CONTEXT.md risk map

### 4. Research Conformance

Verify implementation matches RESEARCH.md decisions:

- Correct API patterns used?
- Dependencies added as specified?
- Pitfalls avoided?

### 5. Present to User

Structured summary:

- Changed files (count + LOC delta)
- Tests passing / failing
- Risk items from CONTEXT.md: addressed / remaining
- Research decisions: followed / deviated

## Iteration

If review finds issues:

1. Update the default `plans/<ts>_<slug>_CONTEXT.md` with new findings
2. Write targeted fix plans
3. Spawn fix agents with same pipeline context
4. Re-run quality gate

Do not accumulate more than 2 iteration rounds without user consultation.

## Anti-Patterns

- Spawning agents without pipeline context (they'll waste time rediscovering)
- Omitting loctree instruction (proven 37% quality drop)
- Not splitting by risk level (one risky change breaks safe work)
- Skipping structural verification after agents complete
- More than 5 parallel agents (coordination cost exceeds benefit)
