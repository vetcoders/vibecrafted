# ADR-0002 — Unified operator runtime: one owner per truth domain

- **Status:** Accepted (plan `vc-unified-operator-runtime` W0-A, polarize cut 2026-07-29). This source amendment, dated 2026-09-06, is proposed for integrator admission; it does not claim that the isolated worktree has landed or that pending implementation seams are installed.
- **Machine contract:** [`docs/adr/ownership-matrix.json`](ownership-matrix.json), enforced by
  `tests/test_ownership_contract.py` (deterministic gate — rejects a second owner for any domain).
- **Supersedes as doctrine:** the implicit, per-plan ownership statements in `vc-single-brain`,
  `vc-server-mcp-slack-gateway` and `vibecrafted-fail-period-ownership`. Their evidence and state
  remain valid; this ADR is the single place the ownership rules live from now on.

## Polarized thesis

Every runtime, context, structure, terminal, messaging and billing truth in Vibecrafted has exactly
one owner with named read/write projections; every other surface is a projection, and a projection
never writes. Runtime components own data, state, and actions; agents maintain code under bounded
code-care allocations.

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
is one canonical entry, a gate that stops later waves (W1–W5) from creating parallel truths, and an
explicit code-care matrix assigning maintenance responsibilities to named agent providers.

## Decision — ownership matrix

One owner per truth domain. Owners write through their named write surface; everyone else reads
through named projections.

| Truth domain             | Sole owner                     | Write surface                                                                                                                 | Read projections                                                                                 |
| ------------------------ | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `installed-runtime`      | `vibecrafted-runtime-manifest` | installer generation build + atomic generation switch (installer alone publishes; ordinary startup consumes without delivery) | host-shell path helper, `vc-start` env, `vc-terminal` env, `vibecrafted-app` env, `doctor`       |
| `run-lifecycle`          | `control-plane`                | `vibecrafted_core` dispatcher + typed control-plane routes                                                                    | server NOW/FLEET/METRICS, `vibecrafted-mcp` board tools, Slack threads, `vc-frame` session views |
| `session-intention`      | `aicx`                         | aicx extract/index pipeline                                                                                                   | operator-shell CONTEXT, loctree AICX overlay, continuity-packet excerpts                         |
| `repository-structure`   | `loctree`                      | `loct scan` / loctree report generation                                                                                       | operator-shell STRUCTURE, report deep links, `loctree-mcp` tools                                 |
| `terminal-substrate`     | `vc-terminal-alacritty`        | installed vc-terminal profile from the runtime manifest                                                                       | WezTerm compatibility adapter                                                                    |
| `session-composition`    | `vc-frame`                     | vc-frame layout/session commands                                                                                              | vc-terminal windows, operator-shell FLEET links                                                  |
| `a2a-envelopes`          | `vibecrafted-slack-bus`        | persisted outbox/inbox with cursors, ACK, dedup                                                                               | `#agents-room` threads, FLEET handoff evidence, control-plane run annotations                    |
| `commercial-entitlement` | `polar-tenant-service`         | Polar webhooks + tenant service (loctree-com tier ladder)                                                                     | onboarding, feature gates, team views                                                            |
| `plan-artifacts`         | `plan-filesystem`              | direct file writes in the artifact tree                                                                                       | scaffold API/editor views, changes/checkpoints ledger (derived), operator-shell plan views       |

### Plan artifacts — files are the owner (operator decision 2026-07-29)

Every write path for plan artifacts (mission, atlas, briefs, tracker, reports) terminates in the
files under `/Users/polyversai/.vibecrafted/artifacts/<org>/<repo>/<day>/plans/`. The scaffold API/editor is a
convenience **client** of that write surface — and the only path for remote integrations without
filesystem access — never a required mediator. The scaffold store's changes/checkpoints ledger is a
derived, best-effort observation of the files; it is never authoritative over them. Rationale: all
agents already write files; forcing API mediation would add a failure plane (server must be up for
a write to count) and would require retraining the entire fleet for zero truth gain.

### Component boundaries (explicit)

- **Alacritty (`vc-terminal`)** owns only the terminal substrate: rendering, fonts, window,
  branding. It must not implement run/session lifecycle, must not duplicate vc-frame state,
  and must not own run recovery. Alacritty is VC Terminal because VC Frame owns multiplexing,
  sessions, and UI composition; a richer terminal would duplicate that responsibility. Closing
  the terminal window must never terminate supervised sessions.
- **`vc-frame`** owns tabs, layouts, panes, and session composition. It projects run-lifecycle
  truth from the control plane; it does not own it. It does not own the terminal substrate.
  One shared rail, tabs strip, and status strip throughout VC Frame is the intended contract
  (implementation in progress, not claimed done).
- **App and tray (`vibecrafted-app`)** has the required target contract of an AppKit presentation
  shell, tray status display, current-runtime reader/launcher, and canonical service-control
  client. It must consume the Python caretaker verdict and selected Runtime Pack identity, and
  ensure/control the canonical shared supervisor through that service interface. Opening or
  reattaching a terminal must not make the App a server, background supervisor, or run-state owner.
  Pending admission of the canonical installer-resolver seam, ordinary App startup must resolve the
  verified active generation from `~/.local/share/vibecrafted` and consume configuration from
  `~/.config/vibecrafted/**` without delivering or reinstalling carriers. Reinstall and repair
  remain explicit onboarding/repair commands. Terminal window closure from the App must preserve
  supervised background sessions.
- **Shared system supervisor and server caretaker (`shared-supervisor`)** owns the identity-verified
  server+guardian pair under launchd (`RunAtLoad`/`KeepAlive`) and derives the single authoritative
  server-health verdict via `vibecrafted_core.caretaker`. It keeps worker agents, runs, PTYs, and
  VC Frame sessions alive after terminal window closure. Rust `web/src/control/caretaker.rs` serves
  published bytes without re-deriving health. Tray/App controls invoke the canonical service
  interface (`vibecrafted_core.server_supervisor`).
- **Control plane (`control-plane`)** owns event/meta reconciliation, `runs/*.json` projections,
  and `AsyncSupervisor` agent child-process orchestration. Python remains the single liveness and
  settlement projection owner. Rust/web reads published projections and must not act as a second
  reducer or snapshot writer.
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

### Terminal, App and supervisor boundary / Granica Alacritty, App i supervisora

**Polish (Wyjaśnienie granic architektonicznych):**
Dlaczego Alacritty jest VC Terminalem: VC Frame odpowiada za multipleksowanie, sesje i kompozycję
interfejsu; bogatszy emulator terminala niepotrzebnie duplikowałby te odpowiedzialności. VC Terminal
(Alacritty) pozostaje ściśle wymiennym substratem graficznym (szybki rendering GPU, okno, typografia).
Aplikacja (App/tray) stanowi natywną powłokę prezentacyjną, wygodny launcher bieżącego środowiska
wykonawczego oraz kanoniczny punkt kontroli usług; nie jest procesem supervisora ani właścicielem stanu
runów. Współdzielony supervisor systemowy (`server_supervisor`) wraz z serwerem utrzymują agentów,
przebiegi robocze (runs), PTY oraz sesje VC Frame przy życiu po zamknięciu okna terminala. Zwykłe
uruchomienie Aplikacji ma konsumować zweryfikowaną aktywną generację i konfigurację, bez publikowania
ani reinstalowania środowiska w tle; to wymagany kontrakt docelowy, a nie twierdzenie o zintegrowanym
lub uruchomionym resolverze.

**English (Canonical architectural boundary):**
Why Alacritty is VC Terminal: VC Frame owns multiplexing, sessions, and UI composition; a richer
terminal emulator would duplicate that responsibility. VC Terminal (Alacritty) remains strictly a
replaceable presentation substrate (GPU rendering, window management, typography). The App (tray) is
required to be a presentation shell, a convenient current-runtime reader/launcher, and canonical
service-control client; it must ensure/control the shared supervisor through that interface, but is not
the supervisor process or run-state owner. The shared system supervisor (`server_supervisor`) and server
keep agents, runs, PTYs, and VC Frame sessions alive after terminal closure. Ordinary App launch must
only consume the verified active generation and configuration without delivering or reinstalling
carriers. This is a required target contract pending installer-resolver seam admission, not a claim of
installed or live behavior.

### Active configuration and installed runtime paths

Three storage roots govern the runtime layout:

1. `~/.config/vibecrafted/**`: Active product configuration truth (XDG canonical configuration tree).
2. `~/.local/share/vibecrafted`: Immutable runtime generations and selectors.
3. `~/.vibecrafted`: Durable control plane state (`control_plane`), artifacts (`artifacts/`), and history.

Delivery boundary: The Runtime Pack installer alone publishes and switches generations. Ordinary
startup consumes the selected runtime and configuration without delivery; it must never reinstall
a carrier as a side effect of opening a window.

### Code-care matrix: agent maintenance responsibilities

Runtime components own data and runtime truth domains; agents maintain code. Code-care allocations
govern repository-qualified, bounded maintenance responsibilities and review pairing, not runtime
authority. A review role may recur across rows; it does not confer source scope or exclusive authorship.

| Component Area                             | Runtime Component              | Code-Care Lead     | Review / Admission                            | Repository              | Scope Paths (relative to that repository)                                                                                                                                                                                                                                                              | Authority & Decision Provenance                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------ | ------------------------------ | ------------------ | --------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **App and tray**                           | `vibecrafted-app`              | **Claude**         | **Codex** review                              | `vetcoders/vibecrafted` | `vibecrafted-app/shell-agent/app/Vibecrafted/AppDelegate.swift`, `vibecrafted-app/shell-agent/app/Vibecrafted/ServerMenuPolicy.swift`                                                                                                                                                                  | **Founder requirement** (App is presentation/current-runtime reader and service-control client, not supervisor or run-state owner; terminal close survives) + **Agent-Operator allocation** (Claude lead / Codex review). Agi allocation is launcher `agy`, requested model `gemini-3.8-flash-medium`, and is limited to icons, copy, docs; it is not actual-model evidence. |
| **System supervisor and caretaker**        | `shared-supervisor`            | **Codex**          | **Claude** review                             | `vetcoders/vibecrafted` | `vibecrafted-core/vibecrafted_core/server_supervisor.py`, `vibecrafted-core/vibecrafted_core/caretaker.py`, `vibecrafted-server/web/src/control/caretaker.rs`                                                                                                                                          | **Founder requirement** (shared supervisor keeps agents/runs/PTYs alive across terminal exits) + **Agent-Operator allocation** (Codex lead / Claude review; integrator admits).                                                                                                                                                                                              |
| **Control plane and Live Runs**            | `control-plane`                | **Codex**          | **Claude** review                             | `vetcoders/vibecrafted` | `vibecrafted-core/vibecrafted_core/control_plane.py`, `vibecrafted-core/vibecrafted_core/supervisor_async.py`, `vibecrafted-server/control-core/src/read.rs`                                                                                                                                           | **Historical ADR-0002 doctrine** (control-plane sole owner of run-lifecycle) + **Agent-Operator allocation** (Codex lead / Claude review). Python publishes; Rust reads.                                                                                                                                                                                                     |
| **VC Frame PTY/multiplexer core**          | `vc-frame`                     | **Codex**          | **Claude** review                             | `vetcoders/vc-frame`    | `zellij-server/src/pty.rs`, `zellij-server/src/route.rs`, `zellij-server/src/tab/layout_applier.rs`, `zellij-client/src/`                                                                                                                                                                              | **Founder requirement** (multiplexing and session composition belong in VC Frame) + **Agent-Operator allocation** (Codex lead / Claude review). Long-lived server survives client exit.                                                                                                                                                                                      |
| **Snapshot / resurrect within Frame**      | `vc-frame`                     | **Claude**         | **Codex** review                              | `vetcoders/vc-frame`    | `zellij-utils/src/sessions.rs`, `zellij-server/src/background_jobs.rs`                                                                                                                                                                                                                                 | **Agent-Operator allocation** (Claude lead / Codex review). Stable session/lease identity across socket rebound; rejects age heuristics.                                                                                                                                                                                                                                     |
| **VC Terminal / Alacritty**                | `vc-terminal-alacritty`        | **Claude**         | **Codex** review                              | `vetcoders/vc-terminal` | `alacritty/`, `alacritty_terminal/`                                                                                                                                                                                                                                                                    | **Founder explicit decision** (Alacritty is VC Terminal because VC Frame owns multiplexing and UI composition; richer terminal duplicates state) + **Agent-Operator allocation** (Claude lead / Codex review). Agi visual assets/copy only.                                                                                                                                  |
| **Installer and configuration**            | `vibecrafted-runtime-manifest` | **Codex**          | **Claude** review                             | `vetcoders/vibecrafted` | `scripts/install-runtime-pack.sh`, `scripts/vetcoders_install.py`, `scripts/unified_product_manifest.py`, `scripts/lib/runtime-roots.sh`, `scripts/vc-terminal-product-entry.sh`, `config/vc-terminal/vibecrafted.toml`, `vibecrafted-core/vibecrafted_core/runtime/helpers/vetcoders-runtime-core.sh` | **Founder requirement** (ordinary startup consumes without delivery; installer alone publishes) + **Agent-Operator allocation** (Codex lead / Claude review). Agi prose/art only.                                                                                                                                                                                            |
| **Plugin rendering and browser coherence** | `vc-frame`                     | **Grok**           | **Claude** review                             | `vetcoders/vc-frame`    | `default-plugins/`, `zellij-client/assets/`                                                                                                                                                                                                                                                            | **Founder explicit requirement** (adding Grok participation for plugin rendering, browser behavior, and interface coherence) + **Agent-Operator allocation** (Grok lead / Claude review).                                                                                                                                                                                    |
| **Static layouts, assets and UI copy**     | `vc-frame`                     | **Agi** (`agy`)    | **Grok** review, **Agent-Operator** admission | `vetcoders/vc-frame`    | `assets/operator-layouts/`, `zellij-utils/assets/layouts/`, `docs/VC_FRAME_OPERATOR_SURFACE.md`                                                                                                                                                                                                        | **Founder requirement** (Agi allocation uses launcher `agy` with requested model `gemini-3.8-flash-medium`; legacy `gemini` CLI deprecated) + **Agent-Operator allocation** (Agi lead / Grok review / Agent-Operator admission). This allocation is not evidence of actual model execution.                                                                                  |
| **Fleet dispatcher and adapters**          | `control-plane`                | **Codex**          | **Claude** review                             | `vetcoders/vibecrafted` | `vibecrafted-core/vibecrafted_core/dispatch/`                                                                                                                                                                                                                                                          | **Historical doctrine** + **Agent-Operator allocation** (Codex lead / Claude review). Provider adapters normalize into one control-plane run contract.                                                                                                                                                                                                                       |
| **Integration and admission**              | none — admission role          | **Agent-Operator** | **Claude** review                             | —                       | none                                                                                                                                                                                                                                                                                                   | **Founder governance doctrine** + **Agent-Operator role assignment**. This is evidence/admission authority for scoped results, never repository-wide source ownership; integration is a separate proven act, never a worker claim.                                                                                                                                           |

**Decision Provenance Distinction:**

- **Founder requirements:**
  1. Alacritty is VC Terminal because VC Frame owns multiplexing and session composition; a richer terminal duplicates that responsibility.
  2. The App is presentation/launcher/service control; it is not the supervisor or run-state owner.
  3. The shared supervisor/server keeps background runs, PTYs, and frame sessions alive across terminal exit.
  4. Grok is the code-care lead for plugin rendering, plugin-browser behavior, flicker fix, and interface coherence.
  5. Agi allocation uses launcher `agy` with requested model `gemini-3.8-flash-medium` (legacy `gemini` CLI is deprecated). Other model pins are per-cut decisions; allocation is not execution evidence.
  6. VC Frame contract is one shared rail, tabs strip, and status strip across all views (not claimed done).
- **Agent-Operator allocation choices:**
  1. Lead and review assignments among providers based on architectural domain suitability (e.g. Codex for Python/Rust core and installer; Claude for Swift AppKit and Rust state machines; Grok for plugins/layouts).
- **Historical ADR decisions:**
  1. Exactly one owner per truth domain (`vibecrafted.ownership.v1`).
  2. Filesystem truth for plan artifacts (scaffold API is client, not gate).
  3. Checkout-free installed artifacts.
  4. Lineage-preserving resumes.

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
(`//Volumes/<workspace>`, `~/Libraxis` or any git checkout path). Repository paths may appear only
in development receipts, never in runtime links. The host shell receives one reversible path
helper and no product logic.

### Artifact-verified gates

Runtime gates are verified through the artifact the user actually runs (installed binary, staged
tools home), not only through source-tree tests.

## Rejected alternatives

- **WezTerm as terminal product identity** — rejected: two lifecycle-capable terminals means two
  owners of the terminal substrate; WezTerm stays an adapter.
- **Rich terminal emulator (e.g. WezTerm/Ghostty) as multiplexer** — rejected (Founder requirement):
  duplicates VC Frame's session, pane, and layout authority. VC Terminal (Alacritty) remains the
  lightweight, GPU-accelerated presentation substrate.
- **`vc-terminal` absorbing multiplexing** — rejected: it would duplicate vc-frame state and make
  the terminal a lifecycle owner.
- **App as supervisor or run-state owner** — rejected (Founder requirement): closing or crashing
  the UI tray would terminate background agent runs; supervisor must remain a detached LaunchAgent service.
- **Ordinary App start reinstalling or delivering runtime** — rejected (Founder requirement):
  normal start must only resolve and consume the verified active generation without running installer
  payloads or mutating configuration.
- **Separate machine schema for code care** — rejected: code-care assignments are governance metadata,
  not runtime truth domains; embedding them into the existing canonical ADR and matrix preserves
  single-source simplicity without creating competing authorities.
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
- Alacritty, vc-frame, App, shared supervisor, server, AICX, Loctree, Slack, Polar boundaries — explicit sections above.
- Resume = lineage-preserving attempt, never silent persona replacement — resume doctrine + rule.
- Checkout paths forbidden in installed artifacts — rule + forbidden path patterns in the matrix.
- A deterministic gate rejects a second owner for the same domain — `test_ownership_contract.py`.
- Code-care maintenance assignments mapped for all 11 component areas with explicit provenance distinctions.
