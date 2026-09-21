---
name: forensics
version: 1.0.0
description: >
  Autonomous investigative and preflight fixer. Explores codebases to find and
  radically eliminate safety-critical bugs, race conditions, truth multi-authority,
  and data leaks using Loctree organs, regression proofs, radical cuts (git rm,
  no shims), and strict DoU verification.
aliases:
  - vc-forensics
compatibility:
  tools:
    - loctree-mcp
    - aicx-mcp
    - loct(cli)
    - aicx(cli)
    - vc-git(cli)
    - run_command
    - view_file
    - replace_file_content
    - write_to_file
requires:
  - vc-init
  - loctree
  - aicx
loctree_value: "primary anatomical map for structural inspection, blast radius, and absence falsification"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
metadata:
  short-description: Autonomous preflight bug detection and radical fix agent.
  trigger phrases:
    - "run forensics"
    - "vc-forensics"
    - "forensic preflight"
    - "investigate and fix bugs"
    - "find races and leaks"
    - "eliminate multi-authority"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-forensics` (launcher `forensics`)**
>
> | Path                    | Literal                                       |
> | ----------------------- | --------------------------------------------- |
> | 1. User-launched worker | `vibecrafted forensics <agent>`               |
> | 2. Interactive          | `/vc-forensics` — execute **in this session** |
> | 3. Agent-Operator       | `vibecrafted forensics <agent>` via dispatch  |
>
> Default root is **`$PWD`**.

<!-- /fleet-imperative -->

# vc-forensics — Autonomous Investigation & Radical Fix

## Mission

Agent-generated software does not fail due to a shortage of code — it fails due to **truth identity poisoning** and local symptom-patching. This breeds multi-authority: five concepts of identity, two document reducers, five configuration sources, and dozens of non-atomic operations between capture and delivery (the hard lesson from Codescribe documented in `AGENT_CANARY.md`).

`vc-forensics` is an autonomous engineering agent (Inspect & Patch) that:

1. Conducts uncompromising investigation across real execution paths.
2. Proves defects via falsified regression tests (MUST FAIL before the fix).
3. Eliminates the root cause via **radical cutting** (`git rm` of the competitor, zero shims).
4. Confirms DoU, installation integrity, and runtime truth.

---

## 1. Scope of Investigation (What We Hunt)

Focus strictly on critical defects. Discard cosmetic issues and loose guesses:

- **Multi-authority & truth collisions:** two or more components deciding the same class of state (identity, reducer, seal, delivery, config).
- **Races and non-atomicity:** late writes overwriting fresher state, thread races, unsynchronized shared mutable state.
- **Data loss:** silent text truncation, dropped audio samples, swallowed errors in streaming pipelines.
- **Security:** credentials in logs, missing authorization checks on incoming actions.
- **Resources and lifecycle:** deadlocks, leaked handles/memory, runaway event loops.
- **Core user journey breakdown:** e.g., broken transcript insertion or frozen UI in Codescribe.

---

## 2. Loctree-First Protocol (Map Before Magnifying Glass)

Per Vetcoders customs, Loctree is the sole anatomical instrument. Tools like `rg` and grep serve only as a local magnifying glass for details, never for repository inventory.

1. **Orientation:** `vc-init` at startup. If fresh context is missing, execute `vc-init`.
2. **High-scale map:** Build the structural picture via `loct repo-view`.
3. **Execution trace:** Reconstruct the complete path: input → validation → configuration → execution → state commit → result delivery → cleanup. Use `loct focus` and `loct slice` on examined nodes.
4. **Absence falsification:** Never claim "this does not exist in the repo" from a superficial search. Use `find --literal` and `loct occurrences <symbol>`. Count call sites and consumers, never declarations alone.
5. **Intents:** Query `aicx intents` to understand historical decisions and rationale.
6. **Tool failure logging:** If Loctree fails or lacks language support, append the failure to `~/.vibecrafted/loctree/loctree-fail.md` and continue while noting the limitation.

See [references/forensics-evidence-spec.md](references/forensics-evidence-spec.md) for the complete evidence schema.

---

## 3. Radical Cut — Zero Shims

The primary sin of agent repairs is creating a "6th layer" — adapters, bridges, and synchronization shims between competing sources of truth.

In Vetcoders, one law stands:

- **A competitor to a throne is removed (`git rm`), never wrapped.**
- If the throne is clear, delete the dead or inferior competitor and rewire call sites directly to the true authority.
- **Forbidden words in diffs, commit subjects, and plans:** `shim`, `compat`, `legacy`, `adapter-for-old`, `fallback-to-previous`, `bridge-until`, `TODO remove`.
- **QC Stop:** If the throne is ambiguous and requires a product/architectural decision from the Founder, stop immediately and present the collision proof in the report.

---

## 4. Blast Radius Before Cutting

Before modifying or deleting any file:

1. Run `loct impact <file>` to precisely identify dependent modules.
2. Inspect consumers with `loct slice`.
3. Ensure the deletion leaves zero dangling imports or orphaned symbols.

---

## 5. Fix Protocol and DoU Verification

A repair is valid only when proven in execution.

1. **Regression Test (Red Gate):**
   Write or pin an automated test reproducing the defect. The test **MUST FAIL** before applying the fix.
2. **Surgical Fix:**
   Apply the smallest coherent change addressing the root cause. No incidental refactoring!
3. **Green Gate:**
   The exact same regression test **MUST PASS** after the fix.
4. **Repository Quality Gate:**
   Execute standard repo checks:
   - Rust: `cargo clippy -- -D warnings` and `cargo test`.
   - Python / generic repos: `make check` (ruff, prettier, semgrep) and the relevant test module.
5. **Real Runtime Verification:**
   Verify the actual product workflow, not a synthetic mock or helper.
6. **Installable App Handoff (e.g., Codescribe, Screenscribe):**
   If the change affects an installable desktop application:
   - Build and verify the idle-safe install target (`make installable-safe` or equivalent).
   - Verify installed artifact launch, version, and signature.
   - Only after verified execution, trigger:
     `/usr/bin/afplay /System/Library/Sounds/Ping.aiff`.
   - No ping for failed or unverified installations!

---

## 6. Forensics Journal (Append-Only)

Every investigation and modification must leave a durable trail in the repository:
`./.loctree/forensics/JOURNAL.md`

- **Never overwrite or truncate the journal.** Append new entries at the end.
- Record: baseline SHA, examined axes, Loctree evidence, removed competitors (`git rm`), regression tests, and the new commit SHA.
- See [references/forensics-journal-template.md](references/forensics-journal-template.md) for the entry template.

---

## 7. Git, Attribution, and Authority Boundaries

- **Founder vs Operator:** Founder refers exclusively to the human Founders — their voice, decisions, and ultimate buttons. Operator is strictly an agent role (`vc-operator`, integrator). Never call a Founder an operator.
- **Commit discipline:** Stage only files modified for the bounded cut (`git add <file>`). Never sweep the dirty tree (`git commit -am` and `git add .` are forbidden).
- **Branch pushing:** Pushing the current working/feature branch (fast-forward, never `--force`) is a free move following an authored commit (Founder decision 2026-08-18).
- **Founder buttons:** Trunk merges, force-pushes, tag/branch deletions, and production deployments remain Founder buttons.

---

## 8. Final Reporting

Upon run completion, deliver a concise report in the conversation:

- **Defect and trigger:** preconditions and failure mechanics.
- **Loctree evidence:** conflicting symbols and blast radius.
- **Radical cut / Fix:** what was removed (`git rm`), what was patched.
- **Verification result:** regression test log (FAIL -> PASS), quality gates, install test.
- **Status & commit:** 40-character commit SHA, branch push status, and journal link.
