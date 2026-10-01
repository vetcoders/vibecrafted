# Authoring & pre-flighting a `.dispatch.toml` plan (field-learned)

Evidence base: sessions-rail-live-buckets line, 2026-08-09 (3-cut sequential
line, claude workers, deployed CLI 3.7.0). Every rule below was hit live.

## Schema authority

- The reference is `docs/public/dispatch/dispatch-schema.md` + `--doctor`.
  The parser fails closed; do not author fields from memory. Validate with
  `vibecrafted dispatch <plan> --doctor` BEFORE anything else.
- `--doctor` emits **informational warnings** for model pins ("pin will be
  forwarded; provider/account availability is not validated") — warnings are
  not errors; pin per cut class anyway (mechanical → cheap tier, surgical /
  decision-bearing → strong tier).

## Renderer truth (braces)

`_format_known` (`dispatch/schema.py`) substitutes **only known** `{name}`
placeholders (`{repo}` `{id}` `{agent}` `{workflow}` `{resolved_workflow}`
`{reports_dir}` `{tracker}` `{baton}` — prompts only for baton). Unknown
braces pass through untouched. Consequences:

- `env=dict()`-style Python in verify `run` commands is safe; so is `{}`.
- **`{baton}` renders as JSON — rendered prompts legitimately contain
  braces.** A naive `grep -c '{'` unrendered-placeholder gate false-positives
  on every prompt that carries a baton. The correct gate greps for the
  _known placeholder tokens_ still present after rendering:

  ```bash
  grep -nE '\{(repo|id|agent|workflow|resolved_workflow|reports_dir|tracker|baton)\}' \
    <reports_dir>/dry-run/prompts/*.md   # expect: no output
  ```

## Verify gates: two techniques that make them non-trivial

1. **Prove `-k` selections are non-empty** before the line moves — a gate
   matching 0 tests is trivially green:

   ```bash
   uv run pytest <file> -k '<expr>' --collect-only -q   # expect ≥1 collected
   ```

2. **Semantic probe verifiers**: a deterministic `python -c` probe that
   returns the OLD value today and MUST return the NEW value after the cut.
   Pre-flight it live: today's output proves the command is syntactically
   valid AND that the gate cannot pass without the work landing. Example
   pair from the sessions-rail line:

   ```toml
   [[cuts.verify]]
   run = '''cd {repo} && uv run python -c "from vibecrafted_core.workflow import _effective_operator_session as f; print(f(root='/x/demo', run_id='r', env=dict()))"'''
   expect = { equals = "demo workers", exit_code = 0 }   # today prints "demo"
   ```

   For env-sensitive probes, force non-TTY with `</dev/null` and inject env
   inline (`env KEY=val …`) so the probe is hermetic.

## TOML escaping

- Verify `run` commands mixing single and double quotes: use multi-line
  literal strings `'''…'''` (fine on one line) — zero escaping.
- Keep **prompts** brace-free except real placeholders; put brace-bearing
  code only in `run` commands (renderer passes them through).

## Dry-run layout

`--dry-run` writes under `reports_dir/dry-run/`: `prompts/<cut-id>.md`,
`tracker.md`, `validated-dispatch.toml`, `dispatch-result.json`. Inspect the
rendered prompts (placeholder gate above) before the real launch.

## Deployed CLI vs checkout (push ≠ install, line edition)

The supervisor and its workers run from the **deployed tools home**
(`vibecrafted --version` → `X.Y.Z+g<sha>`), not from the checkout the cuts
edit. A line whose cuts change runtime/dispatch behavior does NOT change the
behavior of the very line executing it — expect the old behavior for the
whole flight, and leave `make install` as the operator's post-line button.
Corollary: a cut can demonstrably _reproduce_ the bug it fixes while flying.

## Selected substrate and owned staging

Declare the Founder/plan/launcher-selected runtime in `[common]`: Living Tree /
`local-native`, Fleet Worktrees / `local-worktrees`, Fleet VM local, or Fleet VM
cloud. Preserve an explicit choice; do not infer it from old worktrees.
For typed dispatch the supervisor assigns every non-integrator a dedicated
worktree and `cut/<cut-id>` branch from the resolved baseline. `{repo}` resolves
to the worker checkout, not the parent Living Tree. Workers do not create an
additional branch/worktree or integrate themselves. Use `base = "cut:<cut-id>"`
with the matching dependency when a cut needs predecessor bytes, not only order.

Name concurrent work and untouchable paths; re-read before editing. Stage only
owned files/hunks with `git add -- <owned-path>` or hunk selection. Never sweep
with `git add -A` or `git add .`, stash/discard foreign changes, or commit them.
If overlap prevents honest isolation, preserve the diff and report the boundary.

## Launch shape

```bash
bash -c 'ulimit -f unlimited; exec vibecrafted dispatch <plan> --json'   # detached/background
```

Receipt = supervisor-written tracker and control-plane run_id plus runtime class,
parent/effective roots, baseline branch/full SHA, worker branch, and artifact paths.
Artifacts live under `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/artifacts`; preserve
the launcher report's machine-owned identity. Arm supervisor-side
`vibecrafted await <agent> --run-id <id>` immediately; artifacts are diagnostic,
not wake signals. No pane staring or hedge pollers. Headless workers finish their
foreground gates, report and commit before ending the turn; they cannot await a
background task wakeup. Pins ride verbatim; unavailable pins require an honest
failure, never silent model substitution.

## Substrate contract pair + recovery (field-learned, flights 2–4)

`require_commit` WRITE cuts sit between **two symmetric gates**
(`dispatch/supervisor.py::_run_cut`): the cut refuses to START from a dirty
worktree, and refuses to END with uncommitted changes. A worker that edits,
passes verification, then dies before committing (gate-nap: waiting on a
Monitor/wakeup instead of committing — Class 3, `AGENT_OPS.md`) therefore
deadlocks the line: the orphan delivery blocks every refire.

- **Dispatcher recovery**: review the orphan diff against the brief (the
  orphan often delivers), commit it yourself with the cut id in the subject
  and the orphan run's provenance in the body, THEN resume. Never discard.
- **`repair_rounds` does not fire** on `CellContractError` — repair covers
  red verifiers, not substrate contract breaks.
- **Resume from durable evidence**: current typed dispatch uses receipts and
  Git ancestry; a missing directory or matching commit subject is not admission.
  The Operator records material recovery in ignored
  `<repo-root>/.vibecrafted/THE_JOURNAL.md`; workers return reports and never
  write that journal. Preserve delivered SHAs and integration disposition.
- **Idempotent settle needs explicit proof**
  (`supervisor.py::_existing_delivery_commit`): a worker that finds the work
  already landed must put a standalone line `commit: <sha>` in its report;
  the supervisor accepts it only if the sha resolves, is an ancestor of
  HEAD, and the commit message identifies the cut (keep `[<cut-id>]` in
  delivery commit subjects). Bake this clause into `[common]` from the
  start — "nothing to do" without the proof line is a contract failure.

Skill-byte changes regenerate `vibecrafted-core/vibecrafted_core/skills/SKILL_PROVENANCE.json`
through `make skills-check UPDATE=1` (owner: `scripts/gen_skill_provenance.py`).
Respect a separate generator cut and preserve historical entries. The integrator
regenerates the combined manifest after admission; worker source-green never
claims installation, live acceptance, or Founder approval.
