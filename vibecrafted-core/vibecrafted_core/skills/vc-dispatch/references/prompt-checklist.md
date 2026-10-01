# Prompt assembly — the reverse checklist

vc-dispatch carries **no canonical template**. It is an executive skill: it
senses the embedding context and verifies that the assembled prompt COVERS
the required fields. The parent flow's plans, the repo's CLAUDE.md/AGENTS.md,
and vc-init evidence are the source material; the checklist below is the
gate.

## Context sensing (before composing anything)

1. What is the parent flow? (vc-workflow phase, vc-ship line, ad-hoc operator
   order) — its artifacts dictate brief shape and report destinations.
2. Where do this line's artifacts live?
   (`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/{plans,reports}` —
   note: case-insensitive APFS may show two spellings of one directory).
3. What does the repo contract demand? (CLAUDE.md: commit format, hooks,
   untouchable paths, config precedence, language/edition footguns.)
4. What moved on the Living Tree since the briefs were written? (`git log`
   since baseline — this becomes EXTRA and BATON content.)

## The four layers (one .md file, in this order)

### 1. COMMON — environment contract

Must cover (assembled FROM context, not copied from a template):

- [ ] selected runtime + parent/effective roots + baseline branch/full SHA;
      respect explicit Founder `local-worktrees` versus `local-native`/Living
      Tree or VM selection. Typed dispatch supervisor supplies the dedicated
      worker checkout on `cut/<cut-id>`; no worker-created second worktree,
      branch switching, parent-tree writes or self-integration
- [ ] re-read before edit; narrow staging (`git add -- <owned-path>` or owned
      hunks); never `git add -A`, `git add .`, stash/discard or commit foreign work
- [ ] structural-truth tool order (loctree-first; fallback report path for
      misses)
- [ ] architecture invariants (e.g. presentation in app/ never core/) and
      UNTOUCHABLE paths/values, config precedence
- [ ] language/toolchain footguns relevant to the repo (e.g. Rust 2024
      if-let temp scope: snapshot-into-let before `if let` on locks)
- [ ] current wave's push/PR/install/release boundaries; Charter permits an
      authored feature-branch fast-forward push unless the wave forbids it.
      Trunk merges, force-push, PR merge/close, tag/branch deletion and deploy
      are Founder buttons. `--no-verify` only for a
      declared Founder-authorized local compile-embargo checkpoint with a
      skipped-gate receipt; push with it is Founder-only; repo
      linter taboos (no unwrap(), no sleep() in tests, …)
- [ ] commit contract: format + trailers the hook enforces (worker's own
      agent/runtime identity, real session id, date command)
- [ ] focused gates before commit run synchronously by the headless worker;
      supervisor runs declared verifiers, integrator owns broad integration gates.
      Finish report and commit this turn; background completions cannot wake workers
- [ ] SUBSTRATE_FAILURE escape hatch: poisoned tree → no half-commit, report
      the failure line instead
- [ ] REPORT path: launcher-supplied `VIBECRAFTED_REPORT_PATH` beneath the
      canonical artifact root; preserve machine-owned `run_id`/`session_id`,
      finalized/status/claim semantics + required sections
      (files, gate evidence, acceptance [x]/[?]/[!], unverified, next step,
      commit SHA + 3 facts)
- [ ] Operator alone writes ignored `<repo-root>/.vibecrafted/THE_JOURNAL.md`;
      workers return reports. Worker checkboxes are claims, never signatures for
      Operator/Founder; source gates do not prove installed/live acceptance
- [ ] generated skill manifest owner (`scripts/gen_skill_provenance.py`),
      `make skills-check UPDATE=1` after skill-byte edits, and dependency admission
      for any generator cut; integrator regenerates combined history after admission

### 2. BRIEF — the full cut brief

- [ ] pasted COMPLETE, never summarized (the brief is the spec)
- [ ] anchors (file:line) understood as hints — live tree is truth

### 3. EXTRA — corrections vs the brief's HEAD

- [ ] "brief written at <SHA>, tree moved — trust the live tree" with the
      concrete deltas that touch this cut's files
- [ ] gate hardening from pre-flight (≥1 new non-trivial test where baseline
      was 0; replaced flaky verifies)
- [ ] safety bolts: DIVERGED-STOP, scope fences ("do not enter cut X's
      files"), idempotency clause for refire ("if already delivered on the
      tree: verify acceptance and stop — do not duplicate")
- [ ] phasing for big cuts: commit a working subset + honest report rather
      than a half-product across N files

### 4. BATON — line state from the dispatcher

- [ ] which cuts are [x], their commit SHAs, which files they touched
- [ ] explicit "HEAD may advance while you work; operator tests the live
      app in parallel — re-read before editing"
- [ ] pre-handoff baseline for the receiving worker: branch, HEAD SHA,
      `git status --short`, changed files, gates already run, known failures,
      unverified surfaces, current intent, scope fence, and exact next
      instruction/report path
- [ ] for recovery-dispatch: what the previous run did/did not leave behind
      ("you inherit nothing" or the exact WIP description), with evidence
- [ ] what comes after this cut (so the worker fences its scope)

## Mechanical gates before launch

```bash
grep -c '{repo}\|{id}\|{reports_dir}\|{[a-z_]*}' prompt.md   # MUST be 0
wc -l prompt.md                                              # sanity: full brief present
```

- **Model pin present and consistent with the cut's class**: the cut carries a
  `model` pin (a cheaper, faster tier for a mechanical, fully-briefed cut; a
  stronger tier for a surgical or decision-bearing one). A missing pin means
  the account default — a non-decision — so resolve it before launch.

Launch only via file:

```bash
bash -c 'ulimit -f unlimited; vibecrafted <skill> <agent> --file <prompt.md>'
```

Pass the declared pin with `--model <pin>` for direct worker launch. For a typed
plan, use `cuts[].model`; do not silently substitute a different model. Arm the
supervisor-side `vibecrafted await <agent> --run-id <id>` immediately after launch.

## Idempotency rule (refire-readiness)

Every prompt must remain safe to re-fire verbatim: acceptance criteria are
checkable against the tree, EXTRA contains the "verify-and-stop if done"
clause, and BATON's inheritance statement stays true after a partial round
(refire reads the tree, not your memory). If a prompt cannot be safely
re-fired, it is not finished.

## Evidence checkpoint rule

Do not let worker prompts treat baseline capture, gates, reports, or handoff
notes as ceremony. They are regression attribution boundaries. Skipping them is
regression laundering: a later failure loses its owner, time, and lifecycle
segment.
