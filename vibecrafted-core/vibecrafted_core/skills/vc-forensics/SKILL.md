---
name: forensics
version: 1.1.0
description: >
  Investigate product failures through real execution paths, falsify root-cause
  hypotheses and coordinate independently verified repairs. Produces a portable
  evidence notebook joining Loctree, PRView and manual findings.
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
  short-description: Execution-path investigation and independently verified repairs.
  trigger phrases:
    - "run forensics"
    - "vc-forensics"
    - "forensic preflight"
    - "investigate and fix bugs"
    - "find races and leaks"
    - "eliminate multi-authority"
---

# vc-forensics — investigation with independent proof

## Routes and scope

- `/vc-forensics` runs in this session.
- `vibecrafted workflow <agent> --file <brief.md>` dispatches a bounded brief
  explicitly loading this skill. There is no dedicated `vibecrafted forensics`
  launcher; skill installation does not register a CLI verb.
- Honor the current repository, runtime, requested paths and permissions.
  Diagnosis alone does not authorize repair, capture, installation or deployment.
  When investigation and repair are requested, carry the authorized work through
  independent verification and admission. Honor an explicit dispatch batch rule;
  do not silently turn it into a default for every invocation.

## Canonical Orientation Gate

Consume fresh `vc-init` evidence for the investigated repository; refresh only
what drifted. `Loctree:loctree` provides the Code-Derived Application Map:
entry points, dependencies, owners and blast radius. Use slice before edits,
impact before removal and find before new symbols. A zero-consumer result is a
candidate, not deletion proof: check scan coverage and actual runtime call sites.
If mapping is incomplete, record the limitation and use bounded source inspection.
AICX retrieves intent history; distinguish Founder decisions from agent proposals
and verify older statements against current source/runtime evidence.

## Trace the failure, not the test suite

1. Pin repository root, branch, full SHA, dirty ownership and the reported artifact
   or process version. Source, build, installed artifact and running process may
   describe different generations.
2. Record the trigger, expected behavior and actual symptom. Trace input → state
   mutation → committed result → delivery → cleanup through the real callers.
   Inspect late replies, stop/drain boundaries, mode changes and concurrent paths.
   A preview is not proof of committed or delivered data; retain provenance across
   each boundary. A successful receipt is not proof of the user's resulting text.
3. Try the strongest alternative explanation: intentional mode split, stale
   installation, mismatched input, missing fixture, test instrumentation error or
   unsupported scan surface. Preserve falsified hypotheses instead of reporting
   them as additional bugs. Duplicate symbols alone do not prove competing owners.
4. State the smallest causal defect supported by evidence. Separate hypotheses,
   proven defects, source repairs, verification, integration and live acceptance.
   Read [the evidence contract](references/forensics-evidence-spec.md) for details.

For performance, count the actual expensive work and bound the affected range;
measure latency/resource use separately on the stated device and workload. Fewer
calls or green tests alone do not prove cooler hardware. Compare matched inputs
and include positive controls: a repair must preserve valid edits, late evidence,
terminal delivery and provenance, not merely suppress the failing event.

## Investigator, worker and integrator

Follow the repository's assigned roles. In a source-only worker / compile-embargo
workflow, the investigator maps and narrows defects; a bounded worker writes only
its source cut; the integrator owns independent acceptance tests, builds and
admission. Workers must not run gates or inspect private acceptance fixtures.
Do not personally implement a discovered repair when the current Operator contract
requires dispatch. Outside that contract, authorized single-agent repair is valid.

- The integrator designs tests from the product contract and raw evidence, not
  from the worker implementation. Keep private tests out of worker briefs while
  making the required behavior clear. A missing dependency or compile failure is
  not the intended behavioral RED; selected test counts must be nonzero.
- Run the same acceptance case on the pinned baseline and candidate. Add positive
  controls for adjacent valid behavior. Record logs, selected counts and SHAs.
- A terminal worker report or provider success is delivery, not integration.
  Record runtime class, parent/effective roots, baseline branch/SHA, worker branch,
  terminal tip and report path. Verify the destination independently through exact
  ancestry, merge-parent identity or explicit patch equivalence.
- An await/observe timeout is not worker termination. Resume the existing run;
  verify identity before retrying, restarting or launching a replacement.

## Repair and acceptance

Choose the coherent root-cause repair. If two live authorities compete, remove
or rewire the unauthorized owner rather than adding a synchronization layer.
Use `git rm` for tracked dead files after impact and caller checks. Do not force a
rewrite, deletion or vocabulary ban when a bounded repair solves the defect.
If the choice requires an unresolved product decision, expose the evidence and
request that decision while continuing independent work.

Run applicable security, lint and test gates as the designated integrator.
Separate new failures from clean-baseline failures and unrelated concurrent edits.
Stage only authored files/hunks. A commit is source progress; source admission,
installation and real product acceptance need their own receipts. Use the actual
repository install contract only within current authorization, preserve active
sessions and verify artifact/launch identity before any success ping. Record any
required acceptance still open; do not certify completion from source tests alone.

## Durable handoff and notebook

The Operator appends material decisions to `<repo-root>/.vibecrafted/THE_JOURNAL.md`
(private, Git-ignored). Workers return reports to the Operator; they do not create
a second journal. Use [the entry template](references/forensics-journal-template.md).
Before ownership transfer record exact roots/SHA/status, authored changes, checks,
known failures, active run handles, remaining acceptance and the next instruction.

Produce a portable HTML notebook combining actual Loctree/PRView JSON and manually
adjudicated findings. Read [the notebook contract](references/report-notebook.md)
and run `scripts/render_report.py` from this skill directory. Missing inputs stay
NOT_ASSESSED; tool signals do not become proven bugs automatically. The HTML is a
projection of imported receipts, not a second control plane or verification engine.
Manual edits require JSON export; regenerating does not silently retain browser state.

Report concisely: defect and trigger, causal evidence, repair/admission SHA, actual
verification, remaining install/runtime acceptance, and artifact links.
