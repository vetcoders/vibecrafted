# ADR-0002 — Unified operator runtime: one owner per truth domain

- **Status:** Accepted (plan `vc-unified-operator-runtime` W0-A, polarize cut 2026-07-29).
- **Machine contract:** [`docs/adr/ownership-matrix.json`](ownership-matrix.json), enforced by
  `tests/test_ownership_contract.py` (deterministic gate — rejects a second owner for any domain).
- **Supersedes as doctrine:** the implicit, per-plan ownership statements in `vc-single-brain`,
  `vc-server-mcp-slack-gateway` and `vibecrafted-fail-period-ownership`. Their evidence and state
  remain valid; this ADR is the single place the ownership rules live from now on.

## Polarized thesis

Every runtime, context, structure, terminal, messaging and billing truth in Vibecrafted has exactly
one owner with named read/write projections; every other surface is a projection, and a projection
never writes.

## Context

The 2026-07-28 umbrella plan (`vc-unified-operator-runtime`) turns Vibecrafted from a pile of
launchers into one crafted environment: installed capsule (`vc-start`), branded terminal
(`vc-terminal` on Alacritty), multiplexing (`vc-frame`), one Leptos operator shell
(NOW / CONTEXT / STRUCTURE / FLEET / METRICS), a durable Slack A2A bus, and a Polar-backed
SaaS ladder. Three prior plans already converged on the same axis independently:

- `vc-single-brain` — installed-runtime authority, thin aliases.
- `vc-server-mcp-slack-gateway` — `~/.vibecrafted/control_plane` as single source of runtime truth;
  server = eye, Slack bot = mouth/ear, workers = hands.
- `vibecrafted-fail-period-ownership` — Loctree owns STRUCTURE, AICX owns CONTEXT, control plane
  owns FLEET, Polar tiers own billing.

No contradiction table is needed: the predecessors disagree on nothing structural. What was missing
is one canonical entry and a gate that stops later waves (W1–W5) from creating parallel truths.

## Decision — ownership matrix

One owner per truth domain. Owners write through their named write surface; everyone else reads
through named projections.

| Truth domain             | Sole owner                     | Write surface                                              | Read projections                                                                                 |
| ------------------------ | ------------------------------ | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `installed-runtime`      | `vibecrafted-runtime-manifest` | installer generation build + atomic generation switch      | host-shell path helper, `vc-start` env, `vc-terminal` env, `doctor`                              |
| `run-lifecycle`          | `control-plane`                | `vibecrafted_core` dispatcher + typed control-plane routes | server NOW/FLEET/METRICS, `vibecrafted-mcp` board tools, Slack threads, `vc-frame` session views |
| `session-intention`      | `aicx`                         | aicx extract/index pipeline                                | operator-shell CONTEXT, loctree AICX overlay, continuity-packet excerpts                         |
| `repository-structure`   | `loctree`                      | `loct scan` / loctree report generation                    | operator-shell STRUCTURE, report deep links, `loctree-mcp` tools                                 |
| `terminal-substrate`     | `vc-terminal-alacritty`        | installed vc-terminal profile from the runtime manifest    | WezTerm compatibility adapter                                                                    |
| `session-composition`    | `vc-frame`                     | vc-frame layout/session commands                           | vc-terminal windows, operator-shell FLEET links                                                  |
| `a2a-envelopes`          | `vibecrafted-slack-bus`        | persisted outbox/inbox with cursors, ACK, dedup            | `#agents-room` threads, FLEET handoff evidence, control-plane run annotations                    |
| `commercial-entitlement` | `polar-tenant-service`         | Polar webhooks + tenant service (loctree-com tier ladder)  | onboarding, feature gates, team views                                                            |
| `plan-artifacts`         | `plan-filesystem`              | direct file writes in the artifact tree                    | scaffold API/editor views, changes/checkpoints ledger (derived), operator-shell plan views       |

### Plan artifacts — files are the owner (operator decision 2026-07-29)

Every write path for plan artifacts (mission, atlas, briefs, tracker, reports) terminates in the
files under `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<day>/plans/`. The scaffold API/editor is a
convenience **client** of that write surface — and the only path for remote integrations without
filesystem access — never a required mediator. The scaffold store's changes/checkpoints ledger is a
derived, best-effort observation of the files; it is never authoritative over them. Rationale: all
agents already write files; forcing API mediation would add a failure plane (server must be up for
a write to count) and would require retraining the entire fleet for zero truth gain.

### Component boundaries (explicit)

- **Alacritty (`vc-terminal`)** owns only the terminal substrate: rendering, fonts, window,
  branding. It must not implement run/session lifecycle and must not duplicate vc-frame state.
- **`vc-frame`** owns tabs, layouts, panes and session composition. It projects run-lifecycle
  truth from the control plane; it does not own it. It does not own the terminal substrate.
- **WezTerm** is a compatibility/dev adapter until explicitly retired. It owns nothing.
- **Vibecrafted Server** is a projection shell. NOW, CONTEXT, STRUCTURE, FLEET and METRICS render
  owned truths through typed routes. The UI must not invent a parallel state model, crawl arbitrary
  files, reconstruct liveness heuristically, or render a control without an authorized backend
  transition (every visible control performs a real action or is absent).
- **AICX** owns session intention/history. It is not a run-state store and not a structure store.
- **Loctree** owns repository structure and report artifacts. It is not a session-memory store.
- **`vibecrafted-slack`** owns the A2A envelope truth (directed envelopes, ACK-after-persist,
  correlation, dedup, heartbeat, handoff evidence). Slack is transport plus envelope store — it is
  never the source of run-lifecycle truth, and it never executes untrusted text through a shell.
- **Polar tenant service** owns tenants, auth state and entitlement. There is no second billing
  system; entitlement gates product features, never runtime truth.

### Resume doctrine

A resume is a **lineage-preserving attempt**: it keeps the run lineage, identity and attempt
history of the worker it resumes. Silently replacing the worker with a different persona (different
agent identity presented as the same run) is forbidden. Continuity across
precompact/postcompact/session-less resume flows through the mission-level continuity packet
(run-lifecycle domain, control-plane owned); an agent transcript is a bounded delta, not the
primary shared memory.

### Checkout-free installed artifacts

Installed artifacts (symlinks, configs, KDL, generated profiles under
`~/.local/share/vibecrafted` and `~/.vibecrafted`) must never resolve to a repository checkout
(`/Volumes/<workspace>`, `~/Libraxis` or any git checkout path). Repository paths may appear only
in development receipts, never in runtime links. The host shell receives one reversible path
helper and no product logic.

### Artifact-verified gates

Runtime gates are verified through the artifact the user actually runs (installed binary, staged
tools home), not only through source-tree tests.

## Rejected alternatives

- **WezTerm as terminal product identity** — rejected: two lifecycle-capable terminals means two
  owners of the terminal substrate; WezTerm stays an adapter.
- **`vc-terminal` absorbing multiplexing** — rejected: it would duplicate vc-frame state and make
  the terminal a lifecycle owner.
- **Server keeping its own state model** (crawling files, deriving liveness) — rejected: proven
  split-brain (`smoke-nonexistent` in ACTIVE, UI counts diverging from `/api/control/state`).
- **Slack as run-status truth** — rejected: the bus carries envelopes about runs; the control plane
  owns the runs. A second status store is how the billboard died.
- **A second billing/entitlement system beside Polar** — rejected: the loctree-com tier ladder is
  already live; a parallel ladder splits the customer truth.
- **Resume as fresh dispatch with a new persona** — rejected: it destroys handoff evidence and
  makes A2A ACK/lineage unverifiable.
- **Averaging: "each UI keeps some local state for convenience"** — rejected: convenience caches
  that answer user-visible questions are parallel truths by definition.
- **Scaffold API as required write mediator for plan artifacts** — rejected (operator, 2026-07-29):
  every agent already writes files; API mediation adds a failure plane (server up = precondition
  for a write to count) and would require retraining the whole fleet. The API stays a convenience
  client and the remote-integration path; its ledger observes, it never gates. Accepted costs,
  named: the observation path (statuses/ledger) can lag or gap when the watcher is down, and
  concurrent file writers remain last-writer-wins (standard Living Tree risk).

## Enforcement

- Canonical machine matrix: `docs/adr/ownership-matrix.json` (schema `vibecrafted.ownership.v1`).
- Deterministic gate: `pytest tests/test_ownership_contract.py -q` — validates exactly-one-owner
  per domain, bidirectional owner↔component consistency, presence of the resume-lineage and
  checkout-free rules, and proves the validator rejects a second-owner fixture
  (`tests/fixtures/ownership/second_owner_invalid.json`).
- Later waves (W1–W5) extend this matrix by editing the JSON in the same commit as the surface
  they add; the gate keeps a second owner from ever landing silently.

## Acceptance mapping (W0-A)

- Each truth domain has exactly one owner and named read/write projections — table above + gate.
- Alacritty, vc-frame, server, AICX, Loctree, Slack, Polar boundaries — explicit section above.
- Resume = lineage-preserving attempt, never silent persona replacement — resume doctrine + rule.
- Checkout paths forbidden in installed artifacts — rule + forbidden path patterns in the matrix.
- A deterministic gate rejects a second owner for the same domain — `test_ownership_contract.py`.
