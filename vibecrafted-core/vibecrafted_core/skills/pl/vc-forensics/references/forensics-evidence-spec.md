# Evidence contract

A finding needs a trigger, a violated product contract and a reachable causal
path. Tool confidence, a dead-export suggestion or a duplicate name is a lead.
Prioritize data loss, unauthorized mutation, delivery corruption, races,
crashes/deadlocks, resource growth and broken core user journeys. Record cosmetic
or speculative observations separately instead of padding the proven bug count.

## Finding record

Use a stable id, title and recorded state. Preserve additional evidence fields;
the notebook imports them losslessly. Example shape (replace example values):

```json
{
  "id": "F-01",
  "title": "Tail visible in preview disappears from delivery",
  "state": "proven",
  "severity": "major",
  "path": "app/controller/delivery.rs:120",
  "mechanism": "Stop closes the preview before pending evidence reaches the committed projection.",
  "trigger": "Release the key immediately after the last word.",
  "expected": "All authenticated source spans reach delivery exactly once.",
  "actual": "The preview tail is closed without a delivery receipt.",
  "evidence": ["repro.json", "trace.md", "loct-slice.json"],
  "falsification": "Matched audio and artifact generation ruled out a different input or stale install.",
  "verification": {
    "regression": {
      "baseline_sha": "full SHA",
      "candidate_sha": "full SHA",
      "selected": 8,
      "before": "3 intended RED",
      "after": "8 PASS",
      "log": "gate.log"
    },
    "integration": "STILL_ISOLATED",
    "installation": "NOT_ASSESSED",
    "runtime": "NOT_ASSESSED"
  }
}
```

States: `hypothesis`, `proven`, `source_fixed`, `verified`, `integrated`,
`installed`, `live_accepted`, `refuted`. They are recorded claims, not a single
completion ladder: tests, integration, installation and real acceptance remain
separate fields with receipts. A source repair must not erase the original
symptom, provenance or unverified live obligation.

## Falsification and proof

- Pin exact input identity, source SHA, runtime generation and consumer. Compare
  matched inputs; word counts are descriptive, not a reference transcript or a
  proof that every source span survived. Use provenance relations for split/merge
  operations and receipt chains for delivery.
- Trace actual production callers and mutation authority. Distinguish preview,
  diagnostics, committed projections and delivered output. Do not hide failed
  delivery behind a successful preview close or intermediate acknowledgment.
- Test alternative explanations, including legal mode/offline splits. State scan
  completeness and limits for absence claims; count actual call sites, not just
  declarations. Incomplete coverage cannot establish that a live path is absent.
- Tests belong to the designated integrator in compile-embargo workflows. Record
  the exact same test's behavioral failure before and pass after the repair,
  nonzero selected counts and positive controls. A compiler error or broken
  fixture is an unsuccessful experiment, not proof of the intended defect.
- Performance: instrument actual costly work after cache guards; unchanged work
  should not traverse all historical owners. Preserve valid predecessor edits,
  new/late evidence, same-revision derived events and exactly-once terminal paths.
  Counts establish work scaling; latency/thermal claims need device measurements
  and explicit absolute budgets. A hot baseline does not authorize further heat.
- Verify worker-to-destination integration through exact ancestry, merge-parent
  identity or explicit patch equivalence. Reports, clean trees and matching
  subjects alone prove neither admission nor installed behavior.

## Evidence hygiene

Preserve immutable source reports and log paths/digests. Export only the bounded
material needed to assess findings; do not import full private prompts, audio,
credentials, customer data or huge live event buses into the notebook. Respect
capture/device permissions and current repository ownership.
