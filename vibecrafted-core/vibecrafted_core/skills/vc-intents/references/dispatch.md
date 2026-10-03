# Dispatch — the hunt is a fan-out shape

Read this before Phase 3, 5 or 9 when the unit of work is larger than one
session or one cut.

## Why this phase exists

Three phases of this skill are embarrassingly parallel, and one of them is the
slowest thing here:

| Phase                   | The parallel unit       | Why serial hurts                                    |
| ----------------------- | ----------------------- | --------------------------------------------------- |
| 3 — read sources        | N independent sessions  | reading is the wall-clock floor of the whole hunt   |
| 5 — verify structurally | M independent questions | each is a bounded Loctree call with no shared state |
| 9 — deliver             | K cuts                  | disjoint domains, if the queue was ordered honestly |

## The surface is Vibecrafted

Reaching past the framework for a bare native subagent is the failure mode the
framework exists to prevent: no run record, no report, no transcript under
`~/.vibecrafted/control_plane/runtime_runs/`, and nothing the next agent can
resume or audit. A hunt that cannot be resumed contradicts its own ledger.

```bash
# Cold worker — a self-contained unit of work, started without this hunt's context
vibecrafted <skill> <agent> --prompt '<brief>'
vibecrafted <skill> <agent> --file  '/path/to/brief.md'

# Fork — branches THIS session into a new one; the source session stays untouched
vibecrafted fork claude --session current -p '<what this branch should chase>'
vibecrafted fork codex  --session current --runtime headless --file <plan.md>
vc-fork grok --session <provider-session-id> --placement floating -p '<...>'
```

Flags worth knowing (`vibecrafted fork --help` is the reference):

- `--session <id|current|last>` — the provider session to branch from.
  **Never a `work-*` run id**; that is what `--run-id` takes.
  `"$(aicx sessions current)"` resolves to the same thing as `current`.
- `--runtime visible|terminal` for a bare interactive fork, `headless` for a task
  fork with `--prompt`/`--file` (headless is the default once input is given).
- `--permissions bypass|auto|accept-edits|read-only` — an analysis fan-out should
  be `read-only`; codex has no `accept-edits`.
- `--repo <path|org/name>`, `--base <ref>`, `--model <name>`,
  `--execution-runtime living-tree|local-worktrees`, `--worktree [true|false]`.

Native fork support is **claude, codex and grok**. For cursor, agy and junie
there is no verified native fork adapter: `vibecrafted resume <agent> --session
<id>` continues the **original** session and must be reported as a resume, never
presented as a fork.

## Fork or cold worker — the decision that matters

**Fork when the worker needs what this hunt already knows**: the shortlist, which
correction won, the ledger state, why an intent was marked `claim-only`. A fork
inherits the conversation, so you are not re-explaining a reconstruction that
took an hour to build. Delivery (Phase 9) is usually this.

**Cold-dispatch when the unit of work is self-describing**: "read session X,
report every Founder statement about theme Y, with citations". Cheaper — and it
cannot inherit your bias about what the answer should be.

That second point is not a cost argument, it is an evidence argument. When the
question is _"did the Founder actually say this"_, inherited context is a
**defect**: the worker receives your hypothesis along with the task and has a
ready-made track to confirm it. Phases 2–3 should therefore run **cold**, and
Phase 9 should run **forked** — the opposite of the intuition that says
"important work deserves more context".

## Escalation

`vc-delegate` is the doctrine for when a cut stops being bounded and belongs to
the external fleet rather than in-process delegation. Its model rule applies
here: same frontier as the parent; for Claude, `opus[1m]` on long hunts and
`sonnet[1m]` on light ones. A subagent's role label does not prove its actual
model or reasoning effort — confirm from runtime metadata when it matters.

## The ledger has one writer

Workers return findings; the integrator folds them in single-threaded. Parallel
ledger writers lose entries exactly the way parallel commit writers lose files —
two runs each rewrite the file from their own last-read state and the later write
wins silently.

Analysis fan-out stays read-only. Implementation fan-out is the Fleet Worktrees
formation — written plan, pre-committed verifiers, disjoint domains, one
integrator — and nothing short of that formation counts as sanction for parallel
writes.

## Benchmark note

When fanning out to _evaluate_ this skill rather than to use it, bound the
workers explicitly: read-only, no checkout, no installers, writes confined to an
output directory, and stop before Phase 9. Six agents implementing in parallel
against live repos is not a benchmark, it is an incident.
