---
title: "Dispatch Overview"
description: "Deterministic dispatch lines: what vibecrafted dispatch does, when to use it instead of ship, and the doctor, dry-run, and resume flags."
section: dispatch
order: 10
---

# Dispatch Overview

`vibecrafted dispatch` runs a `vibecrafted.dispatch.v1` TOML plan through a
deterministic, dependency-aware supervisor. It launches all ready cuts up to
the declared concurrency limit, runs machine-checkable verifiers after each,
and applies explicit repair and failure policies. Where the
[lifecycle](/docs/lifecycle-overview/) relays one mission through eleven
generic stages, dispatch executes a plan you already decomposed — every cut
named, every success condition written down before anything launches.

## Pilot — the only four invocations

The CLI takes a **plan path plus flags** — there are no subcommands. Verbs
like `dispatch preflight <plan>` or `dispatch launch <plan>` do not exist and
are refused with a pointer back to this pilot:

```bash
vibecrafted dispatch plan.dispatch.toml --doctor            # validate and verify baseline
vibecrafted dispatch plan.dispatch.toml --dry-run [--json]  # render prompts, launch nothing
vibecrafted dispatch plan.dispatch.toml                     # launch the plan
vibecrafted dispatch plan.dispatch.toml --resume <run-id>   # resume a recorded run
```

| Flag                         | Effect                                                                       |
| ---------------------------- | ---------------------------------------------------------------------------- |
| `--doctor`                   | Validate and run baseline verifiers; exit non-zero on errors                 |
| `--allow-red-baseline`       | Admit failed baseline verifiers with override and evidence in the tracker    |
| `--dry-run`                  | Render prompts in the canonical artifact plane without launching             |
| `--json`                     | Machine-readable output                                                      |
| `--resume <run-id>`          | Reconcile receipts/Git and continue without duplicating live or settled cuts |
| `--cleanup-settled <run-id>` | Remove settled worker checkouts/targets; retain branches and evidence        |

Run `--doctor` before every real launch: it parses the plan, checks the
schema, and enforces the policy rules (for example, READ cuts must declare a
`mutation` policy, and verifier commands must not contain hard-stop commands
like `git push --force` or `git push origin main`). `--dry-run` then shows you the exact prompt each worker
would receive — placeholders rendered, briefs inlined, baton attached.

Doctor and real launch both snapshot the repository's current HEAD and run
the plan's unique `cuts.verify.run` commands in a disposable detached worktree
under the canonical worktree plane. The Founder's checkout and dirty files
remain untouched. Each command starts from a clean baseline; identical
commands run once, with all declared expectations retained. The executor
uses the same sanitized environment, matchers and ten-minute command timeout
as worker verification. Progress goes to stderr; `--json` stays machine-readable.

A failed or timed-out baseline verifier refuses admission before any worker
starts, naming the command, baseline SHA and output tail. `--doctor --json`
includes the baseline and verifier evidence. An intentional
`--allow-red-baseline` override permits failed verifiers and records the flag
and their results in the launch tracker; isolation/setup failures still refuse.
Use this explicitly when the cut is meant to make a currently red test pass:

```bash
vibecrafted dispatch plan.dispatch.toml --doctor --allow-red-baseline --json
vibecrafted dispatch plan.dispatch.toml --allow-red-baseline
```

`--dry-run` renders without executing baseline verifiers. Cleanup also skips
this gate so a red baseline cannot prevent removal of settled checkouts.

## What the supervisor does per cut

For each ready `[[cut]]`, the supervisor:

1. Resolves the cut's dependency SHA and creates or validates its canonical
   linked checkout. Only a named integrator receives the main checkout.
2. Renders the cut's prompt: shared `[common]` text, then the cut's brief
   file or inline prompt, then `extra`, then the current baton state as JSON.
3. Launches the cut's agent through the named workflow as a tracked run, with
   `CARGO_TARGET_DIR=<worker-checkout>/target`.
4. Awaits the worker (poll and timeout from `[policy.await]`).
5. Runs the cut's verifiers in that same checkout and matches their output
   against the declared expectations (`contains`, `equals`, `matches`, `not_contains`,
   `exit_code`).
6. Records a verdict with verifier evidence, appends it to the baton, and
   applies policy: repair rounds on failure, `recovery` jumps when declared,
   and `on_critical_fail` / `on_timeout` behavior.

**Branch canon:** the dispatcher's contract branch for every worker cut is
`cut/<id>` — receipts, recovery, delivery-head resolution and cleanup key on
it. Per-agent branch names (`<agent>/workflow/<slug>` and similar) may exist
only as additional refs; if a worker switched its checkout to one, recovery
re-adopts the worktree onto `cut/<id>` at the same commit (authenticated by
SHA ancestry, never by branch name) and leaves the extra ref untouched.

Independent ready cuts overlap. A join waits until every `depends_on` cut
settles successfully; integrators are exclusive. The baton accumulates one
state per cut (`[x]` verified, `[!]` failed,
`[~]` worker done but unverified, `[ ]` pending) — later cuts see the full
history in their prompt, so an audit cut can read what actually happened.

## Artifacts

A full dispatch writes durable evidence under
`~/.vibecrafted/artifacts/<org>/<repo>/YYYY_MMDD` and runtime receipts under
`~/.vibecrafted/control_plane/dispatches/<run-id>`:

```text
tracker.md              # per-cut state line
journal.md              # supervisor journal
handoff.md              # operator handoff
dispatch-result.json    # machine-readable result
receipts.json           # scheduler/worktree/runtime ledger (control plane)
```

The CLI exits zero only when every cut is supervisor-verified; any failed,
stopped, or unknown cut returns non-zero.

## Dispatch vs ship

| Question                                                                | Use                    |
| ----------------------------------------------------------------------- | ---------------------- |
| The mission is one goal and stages should shape themselves              | `vibecrafted ship`     |
| The work is already a list of bounded cuts with known success commands  | `vibecrafted dispatch` |
| You want workers to steer transitions via report frontmatter            | `ship`                 |
| You want the supervisor to enforce written verifiers, deterministically | `dispatch`             |

Both produce the same run records per worker; dispatch adds the verifier
layer and removes stage-level improvisation.

## Single-worker async supervision

The same command also owns one-worker async lifecycle supervision:

```bash
vibecrafted dispatch run --run-id <id> --root . \
  --report .vibecrafted/report.md --transcript .vibecrafted/trace.log \
  -- <command> [args]
```

`dispatch run` owns process spawn, transcript capture, artifact validation,
and exit status for a single supervised worker process. It is plumbing for
runtimes and scripts; the TOML plan form is the operator surface.

Next: the full TOML reference in [Dispatch schema](/docs/dispatch-schema/)
and the worker-side contract in
[Briefs and reports](/docs/briefs-and-reports/).
