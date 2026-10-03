---
title: "Briefs and Reports: The Worker Contract"
description: "The worker contract: brief files as mission input, the report path environment contract, report frontmatter fields, and where artifacts land."
section: dispatch
order: 30
---

# Briefs and Reports: The Worker Contract

A worker's life is simple by design: it receives a brief, does the work, and
writes one report to a path the runtime hands it. A dispatch worker then POSTs
a claim to the local API. Report frontmatter remains self-attestation and
lifecycle steering; it cannot settle a dispatch cut. This page is the contract
from the worker's side.

## Briefs: mission input

A brief is a Markdown mission file. In a dispatch plan it is referenced per
cut (`brief = "cut-01.md"`, resolved relative to the plan file); in direct
launches it arrives via `--file`. The runtime composes the worker's full
prompt as: shared plan text, then the brief body, then any `extra` text,
then the current baton state and generated claim contract — with placeholders such as `{repo}` and `{id}`
already rendered (see [Dispatch schema](/docs/dispatch-schema/)).

A good brief states the goal, the scope fence, the acceptance criteria, and
the test gate. Briefs are idempotent by convention: a supervisor may refire
the same brief after a worker death.

## The report path contract

The runtime tells the worker where to report through environment variables:

| Variable                   | Meaning                                              |
| -------------------------- | ---------------------------------------------------- |
| `VIBECRAFTED_REPORT_PATH`  | Absolute path where the worker must write its report |
| `VIBECRAFTED_AGENT`        | The agent identity the runtime launched              |
| `VIBECRAFTED_CLAIM_DIGEST` | Launcher-issued digest echoed back in frontmatter    |

The rule: write the report to `VIBECRAFTED_REPORT_PATH`, exactly there,
before exiting. A non-empty report at that path is the delivery signal —
`await` treats it as `report_delivered` even when process metadata lags. A
worker that finishes its work but never writes the report has, from the
supervisor's perspective, not delivered.

## Dispatch claim: the doorbell

After writing the report, POST to `http://127.0.0.1:3024/api/dispatch/claim`
with this JSON body, constructed with a JSON serializer:

```json
{
  "run_id": "<dispatch run id supplied in the rendered prompt>",
  "cut_id": "<cut id>",
  "commit_sha": "<exact full SHA>",
  "report_path": "<value of VIBECRAFTED_REPORT_PATH>",
  "measurements": ["<what the worker actually measured>"]
}
```

The dispatch run id differs from the worker runtime run id in its environment
and report frontmatter. Preserve those machine-owned identities; use the
dispatch id from the prompt for this POST. Measure using tools the worker is
allowed to execute and describe missing evidence honestly.

The HTTP handler delegates to the canonical Python writer. That writer records
the claim as `[~]` in existing dispatch receipts; the handler does not write
durable run state. HTTP success acknowledges a claim, not verified delivery.
Workers, report text, and brief Acceptance checkboxes cannot write tracker `[x]`.
An unchanged `[ ]` never skips verifiers and never becomes a failed test.

A normal worker first performs the full applicable
`vibecrafted_core/skills/VERIFICATION_RULE.md`: run the real artifact and runtime
path, re-verify upstream evidence, and check that the instrument can fail.
This qualifies the claim. The writer then independently runs every declared
verifier in its own shell, in the cut runtime root, against the claimed SHA.
Only that full verification with every matcher green settles tracker `[x]`.
A red matcher remains `[!]`, with the verifier cwd in the journal. Verifier
commands default to 600 seconds and receive a sanitized environment;
`DEVELOPER_DIR`, for example, is dropped unless the command inlines it.

If no claim arrives, the journal says **claim not received, verifiers were not
run** and gives `vibecrafted dispatch <plan.toml> --resume <run_id>`. A red
baseline admitted through `--allow-red-baseline` does not count as delivery.

### Compile embargo checkpoints

Compile embargo is the only deferral. A plan-owned `compile_embargo = true`
structural cut follows `vc-scaffold/references/compile-embargo.md`: workers do
not compile, build, format, lint, type-check, test, or choose gates. They POST
the same claim with its checkpoint SHA and add:

```json
{
  "checkpoint": {
    "owned_scope": ["<owned paths>"],
    "skipped_controls": [
      "<exact declared verifier command for each skipped hook/control>"
    ]
  }
}
```

This remains unverified `[~]`. The writer rejects checkpoints unless the plan
registered the cut with `compile_embargo = true`; a POST cannot declare or close embargo.
Include every skipped security and secret control. Each `skipped_controls`
item must map to an exact plan-declared verifier command; the writer executes
the plan's commands, never commands supplied by a payload. Record an unknown
hook obligation honestly: closure stays unverified until the plan declares
that control. The writer runs the integrator's declared verifiers together
with every checkpoint's declared verifiers against the assembled SHA.
The plan names the WRITE integrator and its `closes_embargo` dependencies.
`W2_STRUCTURALLY_CLOSED` records the exact assembled SHA as ready to check.
The integrator restores and runs the full applicable gates, including all
skipped controls; the writer independently verifies that assembled SHA before
closure and tracker `[x]`. No structural checkpoint certifies correctness.

## Report frontmatter

Contract id: `vibecrafted.report-frontmatter.v1`. Every report is Markdown
with a leading YAML frontmatter block:

```yaml
---
run_id: impl-<timestamp>-<id>
agent: codex
skill: vc-implement
project: <repo>
status: completed
claim_status: completed
finalized: true
claim: "Feature X landed; make test green (42 passed)"
claim_digest: <digest from VIBECRAFTED_CLAIM_DIGEST>
code_mutation: true
date: 2026-07-30T12:00:00+00:00
---
```

Required keys — missing any of them is an artifact contract error:

| Key      | Meaning                                                                         |
| -------- | ------------------------------------------------------------------------------- |
| `run_id` | The run this report belongs to                                                  |
| `agent`  | `claude` \| `codex` \| `agy` \| `junie` \| `grok` \| `system`                   |
| `skill`  | The workflow/skill name                                                         |
| `status` | `pending` \| `in-progress` \| `completed` \| `failed` \| `blocked` \| `partial` |

Recommended claim fields:

| Key                                                   | Meaning                                                       |
| ----------------------------------------------------- | ------------------------------------------------------------- |
| `claim_status`                                        | Normalized claim; wins over `status` when set                 |
| `claim_kind`                                          | Kind of claim; defaults to `skill`                            |
| `finalized`                                           | `true` = deliberate self-attestation of successful completion |
| `claim`                                               | One-line human-readable statement of what was delivered       |
| `claim_digest`                                        | Echo of the launcher-issued digest                            |
| `project`, `date`, `session_id`, `repo_path`, `model` | Identity and provenance                                       |

A claim is a claim, not a verdict. `finalized: true` plus a non-empty
`claim` is an explicit self-attestation tier — the runtime still
triangulates it against exit code, report and transcript artifacts, and
verifier evidence. Contradictions settle as needs-attention, never as
finalized.

## Mutation attestation and lifecycle steering

Workers in lifecycle stages carry additional frontmatter duties:

| Key             | Who emits it       | Meaning                                                                                                                                                                                                                                                                                            |
| --------------- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `code_mutation` | every stage worker | `false` when the worker stayed read-only; `true` if it changed code at any point. On a READ stage, `true` or an invalid value is a hard violation. An undeclared value leaves observed repository changes unattributed — on a shared Living Tree, another actor may have moved files while you ran |
| `next_stage`    | any stage worker   | Steer the umbrella to a specific manifest stage                                                                                                                                                                                                                                                    |
| `next_agent`    | any stage worker   | Hand the baton to another agent                                                                                                                                                                                                                                                                    |
| `dou_index`     | DoU stage worker   | Count of open Definition-of-Undone findings; `0` is launch-ready                                                                                                                                                                                                                                   |

See [Supervision](/docs/supervision/) for how the runner validates and
applies these.

## Where artifacts land

All durable artifacts live in the central store — no orphaned artifacts:

```text
~/.vibecrafted/artifacts/<org>/<repo>/<date>/
├── plans/       # briefs and plans
├── reports/     # worker reports
└── tmp/
```

The date directory uses the form `YYYY_MMDD`. Final report filenames follow:

```text
<YYYY-MM-DD>_<org>_<repo>_<session-id>-report.md
```

with `plan`, `tracker`, and `research` as other valid kinds. Each report
basename has two sidecars with the same stem:

| Sidecar           | Holds                                                  |
| ----------------- | ------------------------------------------------------ |
| `.transcript.log` | The worker's full transcript                           |
| `.meta.json`      | Run metadata: state, exit code, timing, triage receipt |

`.vibecrafted/plans` and `.vibecrafted/reports` inside a repository are
convenience links only; the store above is canonical. Supervisors read the
store — see [Observe and await](/docs/observe-await/) for how reports,
metadata, and liveness combine into a settled verdict.
