# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Runtime Contract

This document defines the execution engine, session contracts, telemetry structures,
plan conventions, and spawn mechanics of the 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. framework.

For the delegation doctrine and the `vc-why-matrix`, see
[`skills/vc-agents/SKILL.md`](../../skills/vc-agents/SKILL.md).
For the framework overview and pillar descriptions, see
[`docs/runtime/README.md`](./README.md).
For the canonical product lifecycle (read/write cadence of `vc-ship`,
component architecture, async supervision), see
[`docs/runtime/LIFECYCLE.md`](./LIFECYCLE.md).

---

## Under the Hood

**𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.** is a framework designed by Vetcoders and **`vc-agents`**
is a part of it. It provides telemetry, default store with durable artifacts,
portable spawn helpers, telemetry driven context and intentions retrieval
for straightforward, robust and measurable work in the AI-human teams.

As the 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚜𝚖𝚊𝚗𝚜𝚑𝚒𝚙. methodology is **strict**, spawning through the
𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. method requires a **strict** execution pattern. The framework
provides all the necessary tools to follow this pattern.

### The Four Pillars

1. **Foundations:**
   - [loctree](https://loct.io) — Codebase mapping and architectural perception.
   - [aicx](https://github.com/Loctree/aicx) — Context boundaries and intentions retrieval.
   - [prview](https://github.com/Loctree/prview) — Continuous review pipelines.
   - [Screenscribe](https://github.com/vetcoders/Screenscribe) — Voice-to-text context ingestion.

   The main Vetcoders native framework drivers, designed to make non-programmers
   capable of production-grade implementation of complex development tasks.

2. **`vc-workflows`** (technically `skills`) are the specialized instructions
   based on the Vetcoders team experience and are used to optimize the
   delegation of work to the AI agents.

3. **`vc-runtime`:**
   - `vibecrafted` — Ultimate shell helper and the entry point for `vc-workflows`.
     Used as the main framework launcher.
   - `vc-term` — A custom alacritty implementation providing a terminal emulator.
   - `vc-panes` — A vc_frame-powered operator panel for `vc-term`. Compatible with
     standard terminal emulators.
   - `vc-metrics` — A full frontmatter and `aicx` metadata-driven session tracker
     using the `session_id`+`run_id` as the primary key.

4. **`vc-agents`:**
   The skill that spawns external specialized AI agents from the user's fleet
   (Codex, Claude, Gemini) using the `vc-why-matrix` picker, `vibecrafted` helper
   and durable runtime receipts, transcripts, telemetry, and reports. vc-frame
   may project those surfaces for the operator; its panes do not host or own
   worker processes.

---

## Runtime Contract

- User Session ownership is repo-bound. Its name is derived from the current
  repo root, not a global shared session.
- Ordinary workflow and fleet workers launch as detached headless processes by
  default, regardless of TTY presence or an existing repo session.
- In the `vc-panes` mode, the operator pane reserves the upper `3/5`
  for the User Session and the lower `2/5` for observation surfaces.
- Outside `vc-panes`, observe workers through receipts, control-plane state,
  transcripts, and reports; do not create a process-host tab implicitly.
- A true PTY is reserved for the interactive User Session, a bare resume, or
  an explicit `--runtime terminal` for a provider path proven to require it.
  Explicit terminal mode must not replace or mutate the focused User Session.
- `.vibecrafted/plans` and `.vibecrafted/reports` inside the repo are
  convenience links only. The default store remains
  `$VIBECRAFTED_ROOT/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/`.

### Installer Layout Transfer

During the `agents/scripts` → `runtime/scripts` transition the
installer owns a reversible transfer surface:

```bash
python3 scripts/vetcoders_install.py layout status
python3 scripts/vetcoders_install.py layout migrate   # legacy store -> current tools
python3 scripts/vetcoders_install.py layout rollback  # current tools -> legacy store
```

The transfer is conservative by default. It copies only Vibecrafted framework
payload between `$HOME/.vibecrafted/skills/vc-agents` and
`$HOME/.local/share/vibecrafted/tools/vibecrafted-current/agents`, writes a
ledger entry to `.vc-install.json`, and refuses to overwrite a differing target
file unless the operator explicitly passes `--force`. Product dependencies such
as `loct`, `aicx`, and `vc_frame` stay external PATH discoveries; the layout
transfer must not re-home or replace those binaries.

---

## Mandatory Plan Rules

Every subagent plan should:

- be high level, decisive, and test-gated
- include reason and context
- include a clear checkbox todo list
- include acceptance criteria
- include required checks
- end with a short call to action

---

## Living Tree Rule

Always include this exact preamble in every subagent plan or prompt:

```text
You work on a living tree with 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚜𝚖𝚊𝚗𝚜𝚑𝚒𝚙 methodology, so concurrent changes are expected.
Adapt proactively and continue, but this is never permission to skip quality, security, or test gates.
Run required checks. If something is blocked, report the exact blocker and run the closest safe equivalent.
```

Keep this preamble repo-agnostic.

Add this living-tree coordination note below the preamble whenever the plan
needs it:

- State explicitly whether the agent is working solo at that stage or alongside
  other agents in parallel.
- The agent needs to know whether the stage is solo or shared, but does not
  need to read other agents' plan files unless the plan explicitly requires it.
- If the original plan clearly calls for a stabilization checkpoint, the agent
  must preserve its tranche of work with a local commit, without push.
- During active `decorate` rounds, prefer incremental local commits over one
  giant end-of-task snapshot. Use numbered subjects in the form
  `decorate 1: ...`, `decorate 2: ...`, and continue upward as the round
  hardens distinct seams.
- Never change branches during active work. The intent is to stay on the
  current working branch and keep building inside that living tree.
- Never create, switch to, or move execution into a git worktree unless the
  operator explicitly asks for a worktree. Generic "isolate this" or "work in
  parallel" language is not enough.
- If the current checkout is too poisoned to continue safely, report the
  substrate failure to the operator/runtime layer instead of escaping into a
  side tree.
- Plans may explicitly instruct the agent to finish and harden one seam, spawn
  another `vc-agents` worker for the next plan, commit locally for
  preservation, and continue.
- Before any handoff to another agent, phase, or recovery dispatch, capture a
  pre-handoff baseline: branch, `HEAD`, `git status --short`, changed files,
  gates run, known failures, unverified surfaces, current intent, scope fence,
  and exact next instruction/report path. The receiving agent must compare that
  baseline with the live tree before editing.
- Evidence checkpoints are not ceremony. They are regression attribution
  boundaries; skipping them launders failures into "some agent did something".

---

## Artifact Contract & Frontmatter Telemetry

Central Store Axiom: **NO ORPHANED ARTIFACTS.**
All artifacts (plans, reports, context docs, research) MUST be written to the central store:
`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/{plans,reports,tmp}/`

Final Markdown artifact filenames MUST use:
`%Y-%m-%d_<org>_<repo>_<full_session_id>-<kind>.md`

Allowed `kind` values are artifact categories such as `report`, `plan`,
`tracker`, and `research`. Matching transcript and metadata files use the same
stem with `.transcript.log` and `.meta.json`.

Frontmatter Rule: **To be measurable, it must be steerable.**
Every Markdown artifact generated by ANY skill (`vc-agents`, `vc-scaffold`, `vc-workflow`, `vc-review`, `vc-research`, `vc-dou`, `vc-marbles`, etc.) MUST include a YAML frontmatter block for `aicx steer` indexing.

### Mandatory Frontmatter Template

Contract id: `vibecrafted.report-frontmatter.v1`
Enforced by `vibecrafted_core.report_contract` + `artifacts.validate_artifacts`
and read by `run_triage` for SESSIONS board f/x/n triangulation.

```yaml
---
run_id: <generated-unique-id>
agent: <claude|codex|agy|junie|grok|system>
skill: <vc-skill-name>
project: <repo-name>
status: <pending|in-progress|completed|failed|blocked|partial>
claim_status: <completed|failed|blocked|partial> # agent claim; optional alias of status
claim_kind: <scaffold|implement|audit|…> # optional; defaults to skill
date: <YYYY-MM-DDTHH:MM:SS±HH:MM>
session_id: <full agent session id when known>
# optional proof list for dashboard (comma-separated absolute paths)
# artifacts: /path/to/plan.md,/path/to/brief.md
---
```

**Required keys:** `run_id`, `agent`, `skill`, `status`.
Missing frontmatter block or required keys → artifact contract error
`report_frontmatter_*` (not Finalized).

**Claim is not a self-seal for `f`:**
`claim_status: completed` is triangulated against exit code, report/transcript,
and delivery-kernel axes. Contradictions land in Needs attention (`n`).

Full SESSIONS rail contract (bucket session names, origin stamp, when triage
runs, push≠install): [`TRIAGE_AND_SESSIONS.md`](./TRIAGE_AND_SESSIONS.md).

## Plan Template

Use this structure for execution plans:

```text
---
run_id: <generated-unique-id>
agent: <agent-name>
skill: <vc-skill-name>
project: <repo-name>
status: <pending|completed>
---

# Task: <short title>

Goal:
- <1-3 bullets>

Scope:
- In scope: <files/areas> as high-level suggestions
- Out of scope: <explicit>

Constraints:
- No --no-verify
- Follow repo conventions

Acceptance:
- [ ] <objective outcome>
- [ ] <objective outcome>

Test gate:
- <command(s)>

Context:
- <very short summary>

Living tree note:
- You work on a living tree with 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚜𝚖𝚊𝚗𝚜𝚑𝚒𝚙 methodology, so concurrent changes are expected.
- Adapt proactively and continue, but this is never permission to skip quality, security, or test gates.
- Run required checks. If something is blocked, report the exact blocker and run the closest safe equivalent.
- Coordination mode: <solo on this stage / parallel with other agents on this stage>
- You do not need to inspect other agents' plans unless this plan explicitly tells you to.
- Commit is an obligation, not a checkpoint option: ONE commit per round (marbles — one round = one commit), well-formed per the commit-msg hook, on the current branch. Do NOT leave delivered work uncommitted. Non-destructive remote push of the current feature branch (`git push -u origin HEAD`, not force, not trunk) is a duty after that commit. Force-push, trunk push, merge, and deploy stay operator buttons. When the mission spans multiple rounds/units, multi-commit per dispatch is expected.
- Pre-handoff baseline is mandatory before handing this plan to another agent:
  branch, HEAD, git status, changed files, verification result, known failures,
  unverified surfaces, and exact next instruction/report path.
```

---

## Spawn Commands

The default launch path for agent-to-agent delegation is through the portable spawn scripts.

If the environment has optional shell aliases (like `codex-implement`), those are just convenience wrappers around these
exact same scripts. Always use the portable scripts to ensure maximum compatibility.

Portable spawns default to detached headless execution on every platform.
Pass `--runtime terminal` only for an explicitly selected TTY-required
compatibility path; vc-frame remains an optional observer.

### Codex

```bash
PLAN="$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<plan>.md"
bash $VIBECRAFTED_ROOT/runtime/scripts/codex_spawn.sh "$PLAN" --mode implement
```

### Claude

```bash
PLAN="$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<plan>.md"
bash $VIBECRAFTED_ROOT/runtime/scripts/claude_spawn.sh "$PLAN" --mode implement
```

### Agy (Google Antigravity; gemini deprecated)

```bash
PLAN="$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<plan>.md"
bash $VIBECRAFTED_ROOT/runtime/scripts/agy_spawn.sh "$PLAN" --mode implement
```

Gemini CLI paths are deprecated and removed from active launchers. Existing historical references preserved only in marked docs/audits.

If these tools are unavailable, stop pretending spawn is correctly configured and say so explicitly.

---

## Output Convention

- Plans: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<timestamp>_<slug>.md` or another stable per-task
  filename
- Final reports: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/%Y-%m-%d_<org>_<repo>_<full_session_id>-report.md`
- Transcripts: same report stem with `.transcript.log`
- Metadata: same report stem with `.meta.json`

---

## Observation

Observe progress through durable artifacts in `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/`.
vc-frame may render the same run state and transcript, but closing that viewer
must not affect worker liveness.

If your environment exposes the observer helper, the standard check is:

```bash
bash $VIBECRAFTED_ROOT/runtime/scripts/observe.sh codex --last
```

Use the equivalent agent observer when needed.

---

## Quality Gate Expectations

Keep the standard 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. quality bar:

- loctree-mcp as first-choice exploration and search tool with fail-fast if inaccessible
- semgrep as first-choice security guard when available; the default
  invocation in this repo is `make semgrep`, mirrored by the hooks under
  `scripts/hooks/pre-commit` and `scripts/hooks/pre-push`
- Rust repos: `cargo clippy -- -D warnings`
- Non-Rust repos: choose the closest equivalent lint/type/test gate
- Tests: run if reviewing; write if implementing new behavior; prefer real e2e coverage for the actual pipeline
- If a gate is blocked, report the exact blocker and run the closest safe equivalent

### Release report contract

`vc-release` runs are not finished by passing gates alone. Every release
report must contain four mandatory sections — security gate evidence,
exposed surface inventory, deployment mode decision, and post-release
install smoke from the published artefact (not the working tree). The
default template lives at
[`skills/vc-release/references/release-report-template.md`](../../skills/vc-release/references/release-report-template.md)
and the doctrine sits in [`skills/vc-release/SKILL.md`](../../skills/vc-release/SKILL.md).

---

## Safety Rules

- Do not log secrets or commit `.env` files.
- Never use `--no-verify` for `commit` or `push`.
- Do not rewrite git history unless the user explicitly asks.
- Treat concurrent edits as normal, but still verify before overwriting.
- If a repo has a strict command such as `make check`, run it or explain why not.

---

## 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Doctrine

- Do not treat agents like couriers or report printers. Treat them like artists and implementers.
- Do not over-restrict them into tiny bureaucratic slices when the task wants a real rewrite.
- Sometimes a full replacement is cleaner than patching scar tissue.
- 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. builders ship real products through vibeguiding. Agents should be trusted to do the same.

Fleet is not for outsourcing thought.
Fleet is for deploying equally capable front-line agents through a strict, default launch path.
Use them to implement, not merely to comment on implementation.
