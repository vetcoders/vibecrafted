---
name: vc-dispatch
description: "Operate external Vibecrafted fleet lines with prompt assembly, await/observe, reports, and recovery."
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-dispatch` (launcher `dispatch`)**
>
> Same three-path _shape_ as the fleet, with **this** skill's literals — see the
> canonical [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                                                                                                |
> | ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | `vibecrafted dispatch …` / `vc-dispatch`                                                                                                                              |
> | 2. Interactive          | load `vc-dispatch` (operator method skill) — execute **in this session**; use native subagents when required; do **not** externalize merely because a launcher exists |
> | 3. Agent-operator       | may dispatch the worker form above via `vc-dispatch` / operator lines while preserving this skill's identity                                                          |
>
> **Note:** External fleet **dyspozytura** — runs lines/plans; does not become implement/workflow.

> Freer native on some runs ≠ abandon external fleet. `vc-dispatch` and `vc-ship` keep their own identities.

<!-- /fleet-imperative -->

# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Dispatch — the dyspozytura

**You are the dispatcher (vc-operator role), NOT a worker.** Fleet: external
agents (codex, agy, …) launched via the `vibecrafted` launcher. One brain,
many hands. This skill defines the _method and rigor_ of running a line of
cuts — it owns no pipeline phase and can be invoked from any point of any
workflow.

`vc-ship` remains the normal lifecycle umbrella. A **Founder-ordered bounded
dispatch** is also a supported invocation: run the shaped repair wave through
`vibecrafted dispatch <plan.dispatch.toml>`, with current orientation, complete
briefs, doctor/dry-run, pinned models, and verifiers. It does not require an
eleven-stage lifecycle or a fictitious failure of `vc-ship` first. If consuming
scaffold output, preserve its artifact, DRIVER, manifest, and brief requirements.

## Posture

- Interactive session → **vc-partner**: narrate state transitions to the
  Founder, surface decisions, accept in-flight corrections.
- Non-interactive session → **vc-ownership**: same loop, decisions logged to
  the Operator journal within the authorized scope. Founder buttons still apply.
- In BOTH postures: stall-kill and recovery are autonomous, no ceremony (see
  below). Responsibility for delivery outranks politeness toward a process.

## Boundary contracts

Current `AGENTS.md` and `docs/public/dispatch/dispatch-schema.md` outrank historical
reference advice when substrate, ownership, journals, or approval semantics disagree.

- **Input**: briefs + tracker produced upstream (vc-scaffold / parent
  workflow) — OR, in a **fast wave** (see below), authored by the dispatcher
  in-session from live findings. Hand back to the scaffold flow only when the
  work is genuinely unshaped; a Founder ordering a wave on evidence you
  already hold is not that case.
- **Context sensing**: this skill carries no canonical prompt template.
  Before composing prompts, sense the embedding context — parent skill,
  repo CLAUDE.md / AGENTS.md, vc-init evidence, existing plan artifacts —
  and verify coverage with the reverse checklist
  (`references/prompt-checklist.md`).
- **Output**: scoped worker commits and reports, supervisor verifier receipts,
  tracker state, and explicit integration disposition. Isolated delivery is not
  admission to the Living Tree. The designated integrator proves ancestry,
  merge-parent identity, or exact patch equivalence and rechecks the destination.
  Workers run their focused gates; the supervisor executes declared verifiers.
  Broad integration gates belong to the integrator. Formal `vc-audit` is invoked
  when the Founder requests it, not automatically for every bounded dispatch.
- **Journal and artifacts**: the Operator alone appends material decisions to
  `<repo-root>/.vibecrafted/THE_JOURNAL.md` (ignored, private, never committed).
  Workers return reports, not journal writes. Plans, briefs, trackers and reports
  live under `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/artifacts/<org>/<repo>/<YYYY_MMDD>/`.
  Use the launcher-supplied `VIBECRAFTED_REPORT_PATH`; preserve machine-owned
  `run_id` and `session_id` and honest report status.

## Canonical Orientation Gate

`vc-dispatch` requires current `vc-init` evidence before it conducts a line.
No dispatcher should fire a worker, reshape a wave, or flip a tracker state
from stale repository memory.

`Loctree:loctree` is the default structural perception layer for that
orientation. Use it to produce or refresh the Code-Derived Application Map
before building wave order, composing worker briefs, judging file overlap, or
accepting a baton from a previous cut. Missing Loctree evidence means the line
is blind, not merely under-documented.

## Repository Work Doctrine

For repository work, start with Loctree as the map: use `loct context`,
`loct occurrences`, `loct body`, and `loct find --literal` before broad manual
search. Use AICX for intent and session context. Use rg/grep as fallback or
local magnifier, not as a replacement for structural mapping. If Loctree fails
or misses a surface, append feedback to `~/.vibecrafted/loctree/loctree-fail.md`.

## The loop

```
pre-flight → DISPATCH → SPANKO → SPRAWDZENIE → FLIP → BATON → next cut
                ↑           |  (pulse ticks; stall → recovery-dispatch)
                └── refire ←┘  (partial delivery / convergence pressure)
```

**Model pin per cut (pre-flight):** every cut declares a `model` pin that
matches its class — a mechanical, fully-briefed cut runs on a cheaper, faster
tier, a surgical or decision-bearing cut on a stronger tier. The pin
rides the plan into the launcher (`Cut.model` → `WorkflowLaunchSpec.model` →
the agent's model flag: `--model` for claude and cursor, `-m` for codex). An unpinned
cut runs on the account default — which is a non-decision, not a safe default:
pin deliberately, and treat a missing pin as a smell to resolve before launch.

1. **Pre-flight (once per line)**: test the verify commands from the briefs
   before the line moves — a gate that matches 0 tests is trivially green;
   demand ≥1 new non-trivial test in EXTRA. `grep -c` exits 1 on 0 hits
   (`|| true`); count ALL `test result:` lines (multiple binaries — `tail -1`
   lies); `cargo test` takes ONE positional filter, not two. For pytest
   gates, prove the `-k` selection is non-empty (`--collect-only -q` ≥1);
   prefer **semantic probe** verifiers that print the OLD value today and
   the NEW value only after the cut — run them live pre-flight. For a
   `.dispatch.toml` line: `--doctor` → probe/collect pre-flight → `--dry-run`
   with rendered-prompt placeholder gate → launch. Full field-learned rules:
   `references/toml-plan-preflight.md`.
2. **Dispatch**: one prompt file (never argv — `ps`-public, ARG_MAX, broken
   newlines), four layers per the checklist. Launch:
   `bash -c 'ulimit -f unlimited; vibecrafted <skill> <agent> --model <pin> --file <p.md>'`
   (shells may carry soft `ulimit -f` → SIGXFSZ/exit 153). **Headless by
   default:** CLI and MCP workers run in a detached process session even when a
   vc-frame User Session is live. Observe them through run state, transcript,
   `observe`, and `await`; a vc-frame tab may project those surfaces but must not
   own the worker process. Use `--runtime terminal` / `runtime="visible"` only
   for an explicit provider TTY exception, with the known compatibility cost
   that the worker remains coupled to that terminal. Record the receipt
   (run_id, report, transcript, meta) in the tracker.
   Never substitute a Founder model pin silently. A headless Worker runs gates
   in the foreground and finishes work, report and commit in its current turn;
   background completions cannot wake it. Supervisor-side await is a separate role.
3. **Spanko**: await through artifacts, never by staring at a pane. Use the
   dedicated command as the standard dispatcher loop. Canonical supervisor
   contract (see `docs/runtime/AGENT_OPS.md`): After dispatch, arm
   `vibecrafted await <agent> --run-id <id>` immediately, supervisor-side.
   Control-plane JSON, report files, transcripts, panes, and scheduled wakeups
   are diagnostic only, not wake signals. Hedging await with ad-hoc
   pollers/watchers is a Class 3 violation; fix `control_plane.await_run`, do
   not normalize the hedge.
   3-signal liveness: before declaring done, reconcile await verdict, terminal
   run meta, and worker pid dead; if a report is promised, confirm it exists.
   Two agreeing signals are enough to act, three to declare done; disagreement
   means treat as live and re-arm await. Known skew: rc=0-on-live and meta stuck
   `active`/`stalled` after real completion.
   `vibecrafted loop spanko --run-id <id> --agent <a> --verify '<cmd>' --tracker <tracker.md> --cut-id <cut> --then '<next dispatch>'`
   (framework cron heartbeat → control-plane await → verify → flip → baton),
   the lower-level `vibecrafted loop await-run --run-id <id> --agent <a> --then-cmd '<next>'`,
   or the await-watch probe
   (`vibecrafted-await-watch.sh --meta <meta.json>` — tail-await-die) as a
   visibility aid subordinate to the canonical await. A living worker gets ZERO
   interference; interrupting during its gate phase is pure loss.
4. **Sprawdzenie** (on worker exit): commit SHA exists → diff essence matches
   the brief → worker report's gate results and acceptance read → declared
   supervisor verifiers run. Avoid incidental duplicate builds, but a worker
   report or hook result never substitutes for required independent verification.
5. **Flip**: `[~]→[x]` only by the dispatcher (single writer), evidence =
   SHA + verifier receipts + who verified + integration disposition. Worker
   Acceptance checkboxes are a claim; they never sign for Operator or Founder.
   Source-green is not installed/live acceptance. Ledger rules: `references/ledger.md`.
6. **Baton**: next cut's prompt carries the line state — which cuts landed,
   which commits, which files moved, what the next worker must re-read.

## Fast wave (blitz) — Founder-ordered bounded dispatch

When the Founder points at N verified findings and orders a wave ("dispatchuj
falę na te pakiety", "blitzkrieg, nie partyzantka"), the dispatcher IS the
brief author. Do not route through vc-scaffold, do not build a DRIVER, do not
ask clarifying rounds — the findings-with-evidence are the plan.

Shape (field-proven, loctree-suite findings-wave-2, 2026-08-22):

1. **Pre-flight stays**: fresh baseline SHA (fetch — HEAD moves between your
   diagnosis and the order), disjoint file domains per cut, shared-file
   regions declared explicitly in the briefs, sibling pending branches listed
   as do-not-touch.
2. **Lean brief per cut**, written by the dispatcher in minutes, not hours:
   frontmatter + mission + verbatim evidence (repro commands, line anchors) +
   files + acceptance (≥2 non-trivial tests) + gates + out-of-scope +
   substrate block + commit/trailer rules + report path + idempotency clause.
   Checklist of field-learned mines: `references/fast-wave-brief.md`.
3. **Founder pins ride verbatim**: agents and models named by the Founder
   (`gpt-5.6-sol | grok-4.6 | claude-opus-5`) go straight into the launcher
   `--model` flags and the tracker table. No silent substitution.
4. **Launch all cuts in parallel**, arm supervisor-side awaits immediately,
   write the lean tracker (cut | worker@model | run_id | branch | state).
5. **Dispatcher integrates**: single-thread, after settle, by diffs — the
   fast wave changes brief authorship, never the SPRAWDZENIE/FLIP rigor.

A fast wave is still a line: tracker, ledger evidence, declared verifiers and
integration receipts remain required. Complete the bounded acceptance without
claiming completion of a whole lifecycle.

## Parallel waves are an obligation

When cuts occupy independent code areas, you MUST plan waves for maximum
multi-worker parallelism — running one worker at a time out of conflict fear
is contraindicated. Sequence ONLY hard file overlaps (same file/region).

**Honor the explicit substrate.** The Founder, plan, or launcher selection wins:
Living Tree / `local-native`, Fleet Worktrees / `local-worktrees`, Fleet VM local,
and Fleet VM cloud are distinct runtimes. Never move a worker between them for
convenience. An interactive single lane defaults to the Living Tree when no
different runtime was selected.

For typed `.dispatch.toml`, the supervisor creates every non-integrator's dedicated
worktree under `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/worktrees/<org>/<repo>/<YYYY_MMDD>/<cut-id>`
on `cut/<cut-id>` from the resolved baseline. `{repo}` is that effective worker
root. Workers inherit it; they do not create another branch or worktree, touch
the parent checkout, or integrate themselves. `depends_on` orders time;
`base = "cut:<cut-id>"` supplies predecessor ancestry when needed. Only the
designated integrator admits commits. See `docs/public/dispatch/dispatch-schema.md`.

In every runtime, re-read before editing and stage only owned paths or hunks:
`git add -- <owned-path>` (or select owned hunks). Never use `git add -A` or
`git add .`, stash, discard, or commit foreign work. If overlap prevents honest
isolation, preserve the diff and report substrate failure.

Record runtime class, parent/effective roots, baseline branch/full SHA, worker
branch/tip, artifacts, integration disposition, and checked destination state.
Feature-branch fast-forward push after an authored commit is allowed by the
Charter unless the current wave forbids it. Trunk merge, force-push, PR merge/close,
tag/branch deletion and deploy remain Founder buttons.

Skill-byte changes require regenerating `skills/SKILL_PROVENANCE.json` via
`make skills-check UPDATE=1`; its tracked owner is `scripts/gen_skill_provenance.py`.
Preserve historical entries. Respect any generator-owner cut; consume its admitted
dependency without editing its code. After admission the integrator regenerates
the combined manifest once, rather than choosing a sibling's manifest wholesale.

## Refire = mini-marbles

Re-running the SAME prompt (vc-frame: `<ENTER> re-run` on the spawn pane, or
re-launching `--file` with the same path) is the cheapest convergence
primitive — hot substrate, the worker pays less for archaeology and spends
budget on deltas (vc-marbles: "Marbles exploits cache heat").

- **Precondition**: briefs must be IDEMPOTENT — written so a re-run on a tree
  where the work already landed verifies and stops ("nothing to do"), never
  duplicates.
- **Use refire when**: the task may be too huge for one worker round; the
  report says a sub-item was not done; you want marbles-style convergence
  pressure on a fragile surface.
- Prefer launcher dispatch over inline work precisely BECAUSE refire makes
  partial progress cumulative.

## Read/Write cadence

- Reads (pulse, artifacts, loct) are cheap and continuous; writes happen at
  loop boundaries: tracker/journal after each transition, prompt files before
  dispatch, own commits immediately (one unit = one commit, hook-formed,
  real session_id trailer).
- During waves: no incidental duplicate lint/test runs by the dispatcher, NO drive-by fixes in
  worker scope. Declared verifiers and integrator admission settle delivery;
  invoke formal audit when the Founder requests it.
- Dispatcher's own hands touch the repo only for: line bookkeeping, bounded bookkeeping repairs
  the Founder assigns directly (then: own commit obligatory), and recovery
  evidence gathering.

## Pulse & stall (hard rule, both postures)

Heartbeat is FRAMEWORK-FIRST — the loop/cron mechanics are already automated
in vibecrafted; do not hand-roll timers when these exist:

- `vibecrafted loop spanko --run-id <id> --agent <a> --verify '<cmd>'
--tracker <tracker.md> --cut-id <cut> --then '<next dispatch>'` — the
  command-rank dispatcher await loop: SPANKO → SPRAWDZENIE → FLIP → BATON;
- `vibecrafted loop start|next|status|complete` — line state machine with
  `--max-iterations` and `--completion-promise`;
- `vibecrafted cron line --root <repo> --every-minutes 10 --then-cmd
'vibecrafted loop next'` — real-crontab heartbeat that captures Loctree +
  AICX context per tick;
- `vibecrafted cron tick --after-idle-minutes 10 --then-cmd <cmd>` — resume
  an approved next command after an idle window.

Drive the await with the dedicated command (OUR vc-loop / cron) as the
STANDARD even from an interactive session — a dispatched run HAS the CLI. The
harness `/loop` is a true last-resort, only when the vibecrafted CLI is
genuinely unavailable.

On each tick, judge liveness by three independent signals per
`references/pulse-and-stall.md`: control-plane status, agent session-file
mtime+size, `git status` deltas. **≥10 min of silence on all three → kill the
launcher tree, check for orphans (an orphan often DELIVERS), then
recovery-dispatch with the evidence written into the BATON update** —
possibly a different agent. Never blind-restart; never kill on one signal
(a signal matching a known failure ≠ that failure).

## In-flight corrections

Founder overturns a delivered cut's policy mid-line → write a correction
brief (suffix `b`, e.g. C2→C2b), queue it respecting file overlaps, carry the
Founder's decision verbatim-spirit in the BATON. The mechanics of the old
cut stay; only the policy is corrected. Post-line findings (smoke bugs,
feature wishes) go to a backtracker file with code-truth anchors, become
backlog cuts on the Founder's button.

## Failure patterns (do not repeat)

- Prompt in argv; placeholders left unrendered. Gate on the **known
  placeholder tokens** (`grep -E '\{(repo|id|agent|workflow|resolved_workflow|reports_dir|tracker|baton)\}'`
  over the dry-run prompts, expect empty) — a naive `grep -c '{'` gate
  false-positives on rendered `{baton}` JSON, which legitimately carries
  braces (see `references/toml-plan-preflight.md`).
- Killing a supervisor racing its own loop — check `ps` children and
  `git log` after; the orphan often delivers.
- Founder heredoc typed into chat instead of shell — verify the file exists
  before referencing it.
- Re-running worker gates "to be sure" — claim vs proof is settled by SHA +
  hooks + audit layer, not by your duplicate build.
- Treating a teammate's hands in "your" file as a hazard — on the Living
  Tree a landed commit is the line's, not yours; divergence across rounds is
  signal for vc-polarize, not damage.

## Dependencies

Delivery-proof semantics live in `vibecrafted_core.delivery`; see
`docs/runtime/DELIVERY_PROOF_KERNEL_v1.md`.

vc-marbles (Living Tree, cache heat, one round = one commit) ·
vc-scaffold (brief/tracker shape) · vc-init (orientation evidence) ·
vc-followup / vc-audit / vc-dou (settlement) · vc-polarize (product smear
from wave divergence) · Loctree (structural truth before text search).

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
