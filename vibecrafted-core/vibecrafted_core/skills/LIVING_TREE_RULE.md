---
title: Living Tree Rule
kind: core_rule
version: 3.0.0
description: "Two sanctioned modes: Living Tree (interactive default, zero worktrees) and Fleet Worktrees (verifier-gated parallel dispatch with a single-thread integrator)."
scope: framework
status: active
---

# Living Tree Rule

Vetcoders work in one shared repository checkout — **by default**. Since
2026-08-10 the doctrine has two sanctioned modes, and everything between them
stays forbidden.

## Mode A — Living Tree (interactive default)

Interactive sessions, single-seat workflows, and any work without a
pre-committed verifier run in the shared checkout. Worktrees are not a
harmless implementation detail here: an isolated tree without a measured exit
path splits runtime truth, hides concurrent edits, and turns fast
Vibecraftsmanship into branch archaeology.

Hard rules (unchanged):

- Work in the current checkout and current branch.
- Do not run `git worktree add`, create a side checkout, or relocate execution
  into another lane.
- Do not switch branches during active workflow execution.
- Do not create branches unless the operator explicitly asks for that git move.
- Re-read files before editing when time has passed or concurrent agents may be
  active.
- Treat local changes as shared work. Never stash, discard, reset, or overwrite
  changes you did not make.

Generic requests like "isolate this", "work in parallel", "make a clean
branch", or "avoid conflicts" do **not** switch modes. If the current substrate
is too poisoned to continue safely, stop and report the substrate failure —
never solve substrate invalidity by escaping into a worktree.

## Mode B — Fleet Worktrees (dispatch formation)

A written multi-agent dispatch MAY put each cut in its own worktree — and at
2+ concurrently-writing workers it SHOULD — when **all four** conditions hold:

1. **Verifiers first.** Delivery-verifiers (RED tests or equivalent
   non-fakeable checks) are committed on the base branch BEFORE dispatch, and
   the supervisor's verify commands run them. Weakening, renaming or deleting
   a committed assertion requires operator sign-off.
2. **Disjoint domains.** Cuts are planned on non-overlapping file domains;
   where domains collide, cuts are sequenced, never parallelized. Hub files
   are sequence zones by definition.
3. **One integrator.** A named coordinator owns integration: merges cut
   branches back single-threaded after green verifiers, runs full gates on
   the integrated tree, and journals every mid-plan change. Mode B workers
   NEVER push, NEVER merge, NEVER touch the main checkout — the integrator
   owns remotes. Mode A / shared-checkout workers treat a non-destructive
   feature-branch push as a free move after their own commits (see
   `vc-operator/AUTONOMY.md`).
4. **Standard geometry.** The dispatcher owns provider-neutral worktrees at
   `~/.vibecrafted/worktrees/<org>/<repo>/YYYY_MMDD/<cut-id>` on
   `cut/<cut-id>` branches. Every cut owns a real ignored
   `<worktree>/target`; sharing or symlinking Cargo targets is forbidden.
   Durable evidence stays under `~/.vibecrafted/artifacts`, ephemeral runtime
   state under `~/.vibecrafted/control_plane`, and only settled worktrees are
   eligible for explicit cleanup. No provider-specific roots and no orphan
   trees.

Mode B is operator-explicit by construction: it exists only inside a written
plan (dispatch TOML + briefs) that passed its doctors. An agent may not enter
Mode B ad hoc.

## Reach — what the runtime can and cannot do today (measured 2026-08-18)

The two modes above describe the doctrine. This section describes the
_mechanism_, so that nobody reads Mode B as a capability the everyday launcher
already has. Measured against this tree, not remembered:

**Mode B is real, and only in the dispatch plane.** `dispatch/worktrees.py`
owns the canonical geometry end to end: `WorktreeManager.prepare` refuses an
ambiguous reuse, `_validate_reuse` refuses a dirty or unregistered checkout,
`_validate_target` refuses a symlinked or escaping Cargo target, and
`cleanup` refuses anything that is not settled. `_validate_integrator` enforces
the integrator's own contract — main checkout, clean tree, baseline SHA match.
`dispatch/supervisor.py` drives it, `dispatch/doctor.py` preflights it,
`scripts/smoke-dispatch-worktrees.py` exercises two concurrent workers plus an
exclusive join on real linked checkouts. This part is finished work.

**The everyday launcher plane has no worktree at all.** `workflow.py` is the
launcher for all 24 registered workflows, and it contains zero worktree
references; `WorkflowLaunchSpec` carries no cut id, no worktree, no integrator
field. So `vibecrafted implement claude`, `vibecrafted marbles codex`, and
every other `vibecrafted <skill> <agent>` run in the shared main checkout by
construction. **Mode B is unreachable from the daily command surface.** That
is the gap — not the doctrine.

**Where concurrency actually comes from.** Inside one launch the runtime is
single-writer: `workflow_runtime._run_loop` runs marbles/polarize `--count`
iterations _sequentially_, and research runs its lanes concurrently but under
read cadence. Concurrent _writers_ appear when the operator fires several
launches against the same checkout — the ordinary daily case. Nothing in the
control plane makes a checkout exclusive to one writing run, so that case lands
in Mode A whether or not it satisfies Mode B's four conditions.

**Consequences, stated plainly.**

- An agent that "should" be in Mode B under condition 3 cannot get there
  without a written dispatch plan. Asking for a worktree from a workflow
  launch is not a discipline failure; the surface does not exist.
- `integrator = true` is a dispatch-TOML field. There is no integrator surface
  outside a plan: no launcher flag, no control-plane role, no join primitive.
- Until that reach closes, "parallel work happens in worktrees" describes the
  dispatch plane only. Saying it about the whole runtime would be a claim the
  code does not support.

Closing the reach is an architecture cut, not a doctrine edit: it needs a
worktree-aware `WorkflowLaunchSpec`, a launcher path through `WorktreeManager`,
and an integrator role the control plane can name. Until then this section is
the honest boundary of the rule.

## Why two modes (measured, 2026-08-10)

The original single-mode rule was a prosthesis for the pre-measurement era:
with no verifiers, isolation was a place where unverifiable claims hid until
merge — so the doctrine forced truth **by proximity** (everyone sees
everything immediately). Three things matured and were measured on the
stt-live-first-v2 dispatch:

1. **Truth by measurement replaced truth by proximity.** Pre-committed RED
   tests + supervisor verify made an isolated worker's claim falsifiable
   before merge — isolation stopped being a hiding place.
2. **Concurrent-write cost crossed the threshold.** At 3+ agents writing hub
   files, Living Tree coordination overhead (stash/restore races in hooks,
   partial clobbers, quiet-window commit ceremonies) grows faster than
   linearly and exceeds the cost of planned isolation.
3. **The integrator role exists.** Worktrees without an owner rot into
   orphan-branch graveyards; worktrees with a single-thread integrator are an
   assembly line.

Vibecrafting still optimizes for rapid convergence on runtime truth. Mode B is
not a retreat from that — it is the same convergence at fleet scale, with the
verifier as the truth boundary instead of the shared working directory.

Training-data defaults about worktrees remain subordinate to this doctrine in
both directions: no reflexive worktree in Mode A, no reflexive single-tree
martyrdom where Mode B's conditions are met.

## Pre-handoff baseline

Living Tree coordination needs a measured baton pass. Before one agent hands
work to another agent, another skill phase, or a recovery dispatch, it must
capture a pre-handoff baseline:

- branch and `HEAD` SHA
- `git status --short`
- files changed by the segment
- verification commands run, with result
- known failures, unverified surfaces, and runtime gaps
- current intent, scope fence, and the exact next instruction/report path

The receiving agent performs handoff intake before editing:

1. Read the pre-handoff baseline.
2. Re-read the live repo state.
3. Compare drift between the baseline and current tree.
4. Proceed only if the scope still holds; otherwise report substrate failure.

No handoff without baseline. Without this checkpoint, regression attribution is
guesswork.

## Evidence checkpoints are not ceremony

`vc-init`, re-read-before-edit, pre-change baseline, gates, reports, and
pre-handoff baseline are regression attribution boundaries. Skipping them is not
efficiency; it is regression laundering. A later failure must be attributable to
a lifecycle segment, not smeared across "some agent did something".

## Race-protection helper (added 2026-05-12, Plan 07)

Living Tree disciplines parallel work but does not by itself make
`git commit --only path1 path2` atomic against another agent's
simultaneous commit on the same branch. Kronika 2026-04-16/17 captured the
exact failure mode: under concurrent activity, one agent's commit message
can land under another agent's tree envelope.

Plan 07 ships a reusable primitive that detects this race after the fact
and refuses to silently accept the unsafe commit.

**Operator-facing entry point**:

```
make commit-safe MSG="<commit message>" FILES="path1 path2 ..."
```

**Direct shell invocation**:

```
scripts/lib/living-tree-commit.sh "<commit message>" -- path1 path2 ...
```

The helper captures pre-flight `HEAD`, stages only the named files, snapshots
the staged tree, then commits. After the commit it cross-checks three
invariants:

1. The new commit's parent equals the pre-flight `HEAD` (no concurrent
   commit slipped in via ref update).
2. The new commit's tree matches the staged-tree fingerprint (no foreign
   index mutation rode in on the commit).
3. The set of files changed by the commit matches the staged-files
   snapshot exactly (no foreign files in the envelope).

On race the helper prints both commit SHAs plus the foreign-file list,
offers two operator-driven recovery options, and exits nonzero. It does
**not** auto-amend, auto-reset, or auto-rebase. Recovery is intentionally
operator-driven, consistent with the rest of this rule.

The helper enforces the existing safety rule against wildcard staging:
arguments like `.`, `-A`, `--all`, `-a` are rejected. Name the files.

Verification:

```
make test-race-protection
```

The test suite at `tests/race_protection_test.sh` exercises both the
clean-commit path and two synthetic race injections (concurrent ref update
and foreign-index mutation).

## Plan 07-b helper limitations closure (2026-05-12)

Plan 07's first cut shipped the race detector with two known limitations
that were confirmed across four follow-up marble rounds. Plan 07-b closes
both. Cross-references: marble reports for Plan 04 (Cut D), Plan 03
(Cut F), and Plan 06 (Cut H) document the false-positives that prompted
this work; the Plan 07-b report at
`.vibecrafted/reports/marbles/2026_0512/plan-07b-helper-limitations-fix.md`
captures the closure evidence.

### Limitation #1 — pre-commit hook false-positive (3 confirmations)

The repo's `scripts/hooks/pre-commit` runs `prettier --write` followed by
`git add` on `.md`/`.yaml` files. That happens AFTER the helper's
`git write-tree` snapshot but BEFORE the commit's final tree is sealed.
The original tree-hash detector tripped on the cosmetic content change
and reported a race even though the commit was correct. Operators saw
exit code 3 + "RACE DETECTED" diagnostics on perfectly legitimate
commits.

**Fix**: tree-hash mismatch alone is no longer a race signal. The race
detector now treats the three primitives asymmetrically:

- **HEAD shift** — hard race signal (another commit landed via ref update).
- **Foreign files** — hard race signal (extra files in the commit envelope).
- **Tree-hash mismatch** — informational. Only contributes to a race
  diagnostic when one of the hard signals also fires. With clean HEAD
  and matching file set, the helper now emits `notice — pre-commit hooks
rewrote content; not a race` and exits 0.

**Trade-off**: a hypothetical race that mutates ONLY the content of staged
files (without adding/removing files and without shifting HEAD) would
now slip through. We accept this — no such race has been observed in
four plan rounds, and the original kronika 2026-04-16/17 incident is
caught by the foreign-file detector (which remains strict).

### Limitation #2 — multi-line MSG quoting (1 confirmation in Plan 06)

`make commit-safe MSG="..."` failed on multi-line message bodies due to
Makefile `$$` escaping vs. shell expansion. Plan 06 worked around by
calling `scripts/lib/living-tree-commit.sh` directly with a heredoc.

**Fix**: the helper now accepts `--message-file <path>` as an alternative
to the positional message argument. The Makefile target gains a
`MSG_FILE=<path>` parameter that maps to it. Both invocation modes work;
they are mutually exclusive per invocation.

**Multi-line usage**:

```
cat >/tmp/commit.msg <<'EOF'
plan-XX subject line

Body paragraph one with "quotes" and $shell-style references intact.

- bullet one
- bullet two
EOF

make commit-safe MSG_FILE=/tmp/commit.msg FILES="path1 path2"
```

**Direct shell**:

```
scripts/lib/living-tree-commit.sh --message-file /tmp/commit.msg -- path1 path2
```

Single-line `MSG="..."` continues to work unchanged. Plans 04/03/06
fallback paths that called the helper directly are not affected.

### Verification

The expanded `tests/race_protection_test.sh` adds two positive cases:

- `[positive-C]` simulates a pre-commit hook that prettier-style rewrites
  staged `.md` content. Helper must exit 0 and emit the hook-modified
  notice.
- `[positive-D]` exercises `--message-file` with a body containing
  embedded newlines, single/double quotes, `$shell` references, and
  backticks. All preserved verbatim in the committed body.

Existing 10 assertions (clean-commit + 2 race injections) preserved.
