# Fleet Dispatch Runbook

This is the Founder-facing path for one typed Fleet Worktree dispatch. It is
deliberately narrow: one explicit project root, three independent write cuts,
two providers, and Root Agent-Operator as the only integrator.

The commands below were checked against the source parser and the installed
`vibecrafted 4.3.0+g76c6c25f` help surface on 2026-09-08. Source and installed
generations are distinct evidence: a source test does not install a new
launcher, and an installed help page does not prove the source checkout.

## 1. Enter the project once and declare the typed fleet

Choose the project root once; do not rely on the terminal's inherited cwd.

```bash
PROJECT_ROOT="$(git -C /absolute/path/to/project rev-parse --show-toplevel)"
PLAN="$PROJECT_ROOT/fleet-acceptance.dispatch.toml"
```

Create `"$PLAN"` from
[`examples/dispatch/ship-acceptance-fleet.dispatch.toml`](../../examples/dispatch/ship-acceptance-fleet.dispatch.toml),
replacing only its placeholder `meta.repo` with the exact value of
`$PROJECT_ROOT` and replacing the three cut prompts/verifiers with the three
real, disjoint acceptance cuts. Keep this topology:

| Cut    | Provider | Role                  |
| ------ | -------- | --------------------- |
| `W0-a` | `codex`  | independent owned cut |
| `W0-b` | `claude` | independent owned cut |
| `W0-c` | `codex`  | independent owned cut |

The plan must retain `schema = "vibecrafted.dispatch.v1"`, one declared
`Acceptance` phase, `concurrency = 3`, `allow_concurrency = true`, and
`require_commit = true`. Do not add an `integrator = true` cut to this fleet:
the three workers stay isolated and Root integrates separately.

Validate, render, then launch the same declared plan:

```bash
vibecrafted dispatch "$PLAN" --doctor --json
vibecrafted dispatch "$PLAN" --dry-run --json
vibecrafted dispatch "$PLAN"
```

Expected evidence before launch: doctor returns `ok: true`; dry-run renders
three prompts. On launch record the printed dispatch `run_id`, tracker path,
and journal path. The dispatcher creates one branch and worktree per
non-integrator cut under its canonical worktree plane, and one isolated target
directory per worktree. Workers must not select another checkout, branch, or
target directory.

## 2. Observe from the server, not from a pane

Use the dispatch-provided tracker and journal as live fleet evidence. The
dispatcher is intentionally quiet on stdout after admission until settlement.
Use the general server board for provider-run observations:

```bash
vibecrafted status --all --json
vibecrafted await codex --run-id <owned-provider-run-id>
vibecrafted await claude --run-id <owned-provider-run-id>
vibecrafted observe codex --run-id <owned-provider-run-id>
```

`await` is qualified by provider run identity, not by a PID. A completed
worker needs all of: its report, its commit, its verifier receipt, and its
dispatch receipt state. The receipt ledger is the authority for cut state and
worktree/branch/baseline/tip identity; do not hand-edit `state.json`, a
tracker, or a receipt.

`vibecrafted start` may open or re-open the terminal/App projection:

```bash
vibecrafted start
```

Closing the terminal or App projection does not grant permission to relaunch
the fleet. Re-open it with `vibecrafted start`, then query `status` and the
same tracker. The server/control plane owns the run; the UI is an attachment.

### Founder driving the dispatch personally

Keep launch/resume and observation in separate terminals. The dispatch id and
provider-run id are different namespaces: pass the dispatch id only to
`dispatch --resume`, and take the current provider-run id from the dispatch
receipt before using `observe` or `await`.

```bash
# Terminal A: admit or resume exactly the declared plan
vibecrafted dispatch "$PLAN" --doctor --json
vibecrafted dispatch "$PLAN" --resume <dispatch-run-id>

# Terminal B: watch without taking ownership away from the dispatcher
vibecrafted status --all --json
vibecrafted observe <provider> --run-id <provider-run-id>
vibecrafted await <provider> --run-id <provider-run-id>
```

Use three independent liveness signals before declaring a worker dead:

1. receipt/meta state and its timestamp;
2. transcript or report size/mtime movement;
3. the recorded process identity still being alive.

One stale signal is not a restart instruction. Two agreeing signals justify
investigation; all three are required before declaring settlement or death.
Do not interrupt a quiet test, compiler, signer, or notary process merely
because no chat text appeared. If every signal is unchanged for ten minutes,
inspect the report and receipt for an orphan delivery before resuming. Never
edit the receipt ledger to make a retry possible.

The Founder may close every terminal projection and later return with
`vibecrafted start`; the server-owned run survives. Conversely, a visible pane
is not proof that the worker is still admitted. The receipt plus the three
signals decide.

## 3. One owned failure: stop and retry only that cut

Suppose only `W0-b` fails and its receipt identifies an owned lifecycle run
`<life-ship-run-id>`. First confirm the failure by its provider/run identity
and report, not a similarly named PID. The lifecycle control verb is nested
under `ship`:

```bash
vibecrafted ship interrupt <life-ship-run-id> --json
```

Then resume the declared dispatch, never a sibling by hand:

```bash
vibecrafted dispatch "$PLAN" --resume <dispatch-run-id>
```

The dispatcher consumes its receipt ledger and Git ancestry: it awaits a
still-live cut rather than duplicating it, and retries from the first
non-verified cut. Preserve the original failed report, interruption evidence,
and retry receipt. Do not invoke `vibecrafted resume` with a dispatch id:
that command resumes a provider/control-plane agent run, whereas Fleet retry
is `dispatch "$PLAN" --resume <dispatch-run-id>`.

Important public-surface boundary: `vibecrafted interrupt` is not a command in
the installed command deck. Use the proven `vibecrafted ship interrupt …`
shape for a lifecycle run. If the failing fleet cut has no lifecycle run that
accepts this control, the public CLI has no documented per-cut interrupt
command; stop there, preserve receipts, and report the concrete gap rather
than editing state or signalling an unrelated process.

## 4. Close the fleet; Root alone integrates

For each cut, record the dispatcher-created worktree root, branch, baseline
SHA, terminal tip SHA, report path, commit SHA, verifier output, and receipt
state. `verified` means the supervisor verified that cut; it is not proof that
its branch reached the project root.

Root Agent-Operator manually reviews all three reports and their independent
verifiers, checks exact ancestry or patch equivalence into the destination
tree, and only then performs the separate integration decision. Workers do not
merge, push, deploy, clean worktrees, or claim integration. A clean worktree,
an agent success message, or a copied report is not an integration receipt.

After settlement, cleanup is explicit and only removes settled worktree
payloads; it retains branches and durable evidence:

```bash
vibecrafted dispatch "$PLAN" --cleanup-settled <dispatch-run-id>
```

Run this only after Root has recorded the integration disposition. It never
substitutes for Root's review.
