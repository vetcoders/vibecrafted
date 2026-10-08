---
title: "Glossary"
description: "Definitions of the Vibecrafted vocabulary: baton, control plane, marbles, settlement, waves, and the rest."
section: reference
order: 10
---

# Glossary

Short definitions of the terms used across this documentation, in
alphabetical order.

**Agent** — one of the supported coding CLIs the framework can dispatch:
`claude`, `codex`, `agy`, `junie`, `grok`, `cursor`. Agents are peers; any of them
can execute any skill.

**Artifact store** — the durable home for plans, reports, transcripts, and
metadata: `~/.vibecrafted/artifacts/<org>/<repo>/<date>/{plans,reports,tmp}/`.
No orphaned artifacts: everything a worker produces lands here.

**Baton** — the relay token of a lifecycle run. It moves from stage to
stage carrying its report cargo; the supervisor advances it with verbs
(`approve`, `fallback`, `interrupt`) and never by manual state surgery.

**Control plane** — the single durable truth for run lifecycle on your
machine, under `~/.vibecrafted/control_plane` (events, run state, the
settlement ledger). Every UI is a read projection of it.

**Cut** — one bounded, truth-forcing unit of implementation work assigned
to one worker: one scope, one verification, one commit (or commit pack).

**Delivery receipt** — a machine-readable provenance record (schema
`vibecrafted.delivery_receipt.v1`) binding a tool's source checkout, its
installed binary on PATH, and named drift classes between them.

**Delivery seal** — the content-addressed, immutable record that a proven
change was delivered in its promised scope. Only the shipping authority
(`vc-ship`) issues seals; without one, a run is `delivery.unverified`.

**Dispatch line** — an ordered sequence of worker dispatches executed and
supervised as one line: prompts assembled, workers awaited, reports
verified, recovery dispatched on stalls.

**DoU (Definition of Undone)** — the READ stage and audit that measures
the distance between "implemented" and "shippable" across the whole
product surface: repo health, install path, docs, discoverability.

**f / x / n** — the three settlement buckets for finished runs: finalized,
failed, needs attention. Derived only from the settlement ledger; zero is
a verdict, not a default.

**Foundation** — an infrastructure binary the skills depend on: Loctree
(structure), AICX (session intentions), PRView (review artifacts),
Screenscribe (narrated-demo ingestion).

**Generation** — see _Runtime generation_.

**Living Tree** — the shared-checkout doctrine: agents work concurrently
in one checkout, re-read before editing, never revert concurrent work, and
never use worktrees for active implementation. See
[Living Tree](/docs/living-tree/).

**Marbles** — the WRITE stage of deliberate over-correction: a swarm of
isolated workers floods every crack with fixes in intentional excess, so
that audit and polarize have something to verify and cut back.

**Mission** — the durable plan file that grounds a lifecycle run,
composed under the artifact store and delivered verbatim to every stage
worker.

**Polarize** — the WRITE stage that strips the marbles excess back to one
axis of truth: one framing wins, competing surfaces are rejected, and
runtime, tests, docs, and promises are aligned to agree.

**Read–Write cadence** — the alternation of read-only perception stages
and write action stages that drives convergence. See
[Read–Write cadence](/docs/read-write-cadence/).

**Receipt** — any durable, machine-readable record the runtime emits about
an action it took (a launch receipt, an install receipt, a delivery
receipt), designed to be verified later instead of trusted at the time.

**Runtime generation** — one immutable installed build of the framework
under `~/.local/share/vibecrafted/tools/`, selected by the atomic
`vibecrafted-current` pointer and described by its `runtime-manifest.json`.
See [Runtime capsule](/docs/runtime-capsule/).

**Settlement** — the final, ledger-recorded verdict on a finished run
(`f`, `x`, or `n`), produced by triangulating the agent's claim against
exit code, artifacts, and delivery evidence.

**Settlement ledger** — the immutable, append-only, hash-chained history
of settlement events at
`~/.vibecrafted/control_plane/settlement_ledger.jsonl`, queried with
`vibecrafted settlements`.

**Skill** — a packaged workflow protocol (`vc-*`) that tells an agent how
to behave for a specific kind of task. The agent is the runtime; the skill
is the instruction set.

**Stage worker** — the headless agent process that executes exactly one
lifecycle stage of a run, produces a frontmatter-carrying report, and
hands the baton back to the supervisor.

**vc-frame** — the operator cockpit that owns terminal session
composition: tabs, layouts, panes, and viewer rails. It projects run truth
from the control plane; it never owns worker processes.

**Wave** — one planned group of dispatches inside a multi-wave plan
(W0, W1, …). Waves let a supervisor land a large change as a sequence of
verifiable dispatch lines instead of one unreviewable push.
