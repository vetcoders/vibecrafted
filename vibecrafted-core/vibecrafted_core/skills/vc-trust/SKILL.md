---
name: vc-trust
version: 1.0.0
description: >
  Post-hoc falsification of commit claims on a Living Tree, including agent
  fairness (Authored-By matches subject agent; no vendor footers; no foreign
  envelope lies) and completeness claims. Produces pass, pass-with-gaps, or
  block verdicts, appends evidence to the trust journal, and projects explicit
  verdicts onto the canonical f/x/n settlement axis. Trust observes and judges;
  it never blocks dispatch or mutates code. Enforcement is vc-guard.
loctree_value: "commit scope, consumers, blast radius, and runtime paths"
aicx_value: "why the commit exists and which prior attempts shaped it"
dogfooding: "required"
---

# vc-trust — the judge after the fact

`vc-trust` is a calm, post-hoc judge for commits made on the shared Living
Tree. Commit messages are hypotheses. Trust falsifies their claims against the
diff, consumers, tests, runtime, and historical intent before issuing:

- `pass` — every material claim survived falsification **with strong evidence**
  (format/trailer legality alone is never enough).
- `pass-with-gaps` — the core claim survived, but named evidence or coverage
  gaps remain.
- `block` — a material claim is false, contradicted, unsafe, or cannot meet its
  required evidence bar (including agent-fairness breaches).

### Agent fairness (first-class claim axis)

On every commit, treat at least these as material claims:

1. Subject matches `[<agent>/<runtime>] <type>: …`.
2. `Authored-By: <agent> <agents@vetcoders.io>` is present and **equals** the
   subject agent (Agent Fairness — executor authenticity).
3. No vendor `Co-Authored-By` / `noreply@` / vendor emails.
4. Explanatory body exists before trailers (shape is necessary, not sufficient).
5. Commit envelope lists real files; unearned “done/fixed” language without
   named tests/gates is a gap, not a pass.

Mechanical extractors:

```bash
python -m vibecrafted_core.trust inspect <sha>
```

Inspect never auto-notes and never implies pass. Only explicit `note` writes
the journal and settlement.

The settlement mapping is closed and canonical (do not reopen letters):

| Trust verdict    | Settlement      | TUI |
| ---------------- | --------------- | --- |
| `pass`           | Finalized       | `f` |
| `pass-with-gaps` | Needs attention | `n` |
| `block`          | Failed          | `x` |

## Canonical Orientation Gate

Before inspecting, judging, or noting a commit, run or consume the `vc-init`
procedure for the assigned repository. Pin the repository, branch, baseline,
review range, dirty state, and trust-journal path. `trust inspect` is a
mechanical extractor, not a substitute for this orientation and never evidence
enough for a verdict.

Use `Loctree:loctree` to produce or refresh the Code-Derived Application Map for
the reviewed range: changed files, consumers, runtime entrypoints, tests,
twins, and blast radius. Use `slice`, `impact`, `find`, and `follow` where each
claim demands them, then independently inspect the diff and real runtime path.
The map bounds falsification; it does not award trust. Trust stays read-only
with respect to code and writes only the explicit journal/settlement surfaces
named below.

## Invocation

- Worker: `vibecrafted trust <claude|codex|agy|junie|grok|cursor> --prompt ...`
- Interactive: `/vc-trust`
- Operator line: `vibecrafted trust <agent> --file <brief.md>`

The structured helper is:

```bash
python -m vibecrafted_core.trust --help
```

## Hard boundary

Trust is READ-only with respect to the repository. It may write only:

- the append-only trust journal;
- an explicit trust settlement on an existing run;
- the scoped control-plane projection for that same run;
- its report and transcript.

Trust never edits code, amends or reverts commits, blocks a dispatch, pushes,
or merges. Enforcement belongs to `vc-guard` (gate inventory + refuse on trust
`block`). Do not implement guard behavior here.

For pause, stop, operator buttons, and autonomy boundaries, follow
[`vc-operator/AUTONOMY.md`](../vc-operator/AUTONOMY.md); do not fork that
contract.

## Protocol

### 1. Orient and bound the stream

Run the `vc-init` gate. Read the complete Loctree atlas and AICX intent history.
Capture branch, HEAD, dirty state, and the exact commit range. On a Living Tree,
never attribute a dirty file or concurrent commit from timing alone.

List unjudged candidates:

```bash
python -m vibecrafted_core.trust enumerate <author> --since <sha-or-ISO-time>
```

### 2. Turn prose into falsifiable claims

For each commit, extract every material claim from its subject/body and changed
surface. Rewrite vague prose into checks such as:

- named test or gate exists and fails when the behavior is broken;
- runtime path reaches the changed code;
- claimed fail-closed behavior has no bypass or silencer;
- docs and launcher surface describe the behavior that actually ships;
- diff scope matches the message and contains no unclaimed foreign files.

Absence of a claim in the message does not hide a material regression in the
diff.

### 3. Grade evidence per claim

- `strong` — direct runtime reproduction, adversarial test, exact artifact
  inspection, or an independently failing-then-passing gate.
- `medium` — focused unit/integration test plus structural consumer proof.
- `weak` — static prose, inferred intent, happy-path-only evidence, or an
  upstream report not re-run by the judge.

A `pass` requires every material claim to have sufficient direct evidence.
Weak evidence can support context, never a material pass by itself.

### 4. Falsify, do not replay ceremony

Use Loctree `slice` for changed files, `impact` for high-blast-radius changes,
literal find/body for exact claims, and `follow` for relevant dead/cycle/twin
signals. Read the commit diff and its parents. Run the nearest tests and the
real user path. Check that the verification command itself can fail.

`vc-review` and `vc-audit` remain different:

- `vc-review` judges bounded diff/PR quality.
- `vc-audit` falsifies a completed plan or multi-task implementation.
- `vc-trust` judges a commit stream on the live shared tree and records a
  durable per-commit verdict.

### 5. Record exactly one explicit verdict

Each `--claim` must have one matching `--grade` and `--evidence`:

```bash
python -m vibecrafted_core.trust note <sha> pass \
  --claim "the blocking lane rejects insecure code" \
  --grade strong \
  --evidence "negative fixture failed before the fix and passed after it"
```

When judging the commit(s) produced by a run, add `--run-id <id>`. Only this
explicit `note` writes the canonical settlement. Exit code, report presence,
or await completion never imply a trust pass.

The journal defaults to
`$VIBECRAFTED_HOME/trust/journal.jsonl` and uses
`vibecrafted.trust-journal.v1`. Override it with
`VIBECRAFTED_TRUST_JOURNAL` or `--journal`.

### 6. Await at the run boundary

The primary lifecycle mode is named `await-primary`, not `guard=await`:

```bash
python -m vibecrafted_core.trust await-primary <run-id> \
  --author <agent-author> \
  --since <baseline-sha>
```

It waits synchronously through the canonical control plane, then lists
unjudged candidate commits. It does not auto-pass, auto-note, poll in the
background, or act as a persistent monitor. Persistent monitoring is only an
interactive convenience and is not a durable wake mechanism.

### 7. Roll up

```bash
python -m vibecrafted_core.trust triage [--run-id <id>]
```

Triage uses the latest append-only record per repo+commit and reports canonical
`f/x/n` counts. It does not recompute settlement from Git or exit codes.

## Report contract

The final report must include:

- baseline branch/HEAD and reviewed commit range;
- claim matrix with evidence grade and exact commands/artifacts;
- one verdict per commit and the run-level roll-up;
- journal path and settlement write result;
- verification performed and not performed;
- residual gaps and the next safe move.

Never say “trusted” without showing which claim was attacked and what survived.
