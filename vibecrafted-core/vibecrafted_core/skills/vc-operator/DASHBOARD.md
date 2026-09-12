# vc-operator — DASHBOARD: Admin Panel for the Agent-Operator

> The Agent-Operator works blind without an admin panel. All data sources
> exist; only the view layer is missing. This file is the **doctrine** —
> why the dashboard matters, what panels it must surface, what
> authoritative sources back each panel. The **concrete build plan** ships
> as a sibling `PLAN_23` inside the `vc-operator` product workspace.

Read alongside [`SKILL.md`](SKILL.md), [`AWAIT.md`](AWAIT.md). Source recon
delivered 2026-05-16.

---

## Why this matters

Today the Agent-Operator orchestrates a fleet from memory:

- "Did Wave B-2 land green?" — re-read your own session log
- "Has Gemini been pulling its weight this week?" — guess
- "Is `vc-partner` actually invoked?" — run an empirical recon every time
- "Is the host-a host disk under 80%?" — `ssh host-a df -h` and hope
- "Which prompt is in flight right now?" — remember the run_id

Every one of those data points lives on disk. The operator-agent and the
operator (the human) both work without a single view that joins them.

The dashboard turns operator mode from **"agent remembers"** into
**"operator and agent both see"**.

---

## The seven panels

### 1. Active dispatches (live)

What's running _right now_. Each row:

- run_id
- agent (claude / codex / gemini)
- skill (vc-implement / vc-ownership / vc-marbles / etc.)
- wave + position in plan
- elapsed wall-clock + ETA
- live link to the transcript/state viewer; never a process-host tab

**Authoritative sources**:

- `/tmp/<runtime>/<encoded-cwd>/<session-uuid>/tasks/<task-id>.output`
  for live state (JSONL stream)
- `~/.vibecrafted/artifacts/<...>/<workflow>/tmp/vc-spawn-cmd.<…>.LOCK`
  pidfiles for active spawn detection
- Join key: `(cwd, session_id, prompt_id)` triple

### 2. Wave atlas (current plan)

Status grid of the active master-dispatch plan. Each prompt:

- wave letter + position
- prompt_id
- status: `pending` / `firing` / `await` / `green` / `failed` / `recovered`
- SHA when green
- assigned agent
- branch
- dependency arrows (rendered as wave columns)

**Authoritative source**: the master dispatch atlas's tracker section,
updated by the operator-agent after every wave close-out. Mirrors
[`GUIDE.md`](GUIDE.md) wave structure 1:1.

### 3. Per-agent stats (last N days)

Per-agent table:

- invocations count
- success rate (% with `status: completed` and `gate: pass`)
- avg wall-clock per dispatch
- peer-tier compliance (% with `model: opus` when parent was Opus)
- token / cost rollup if available
- recent failure recap

**Authoritative source**: `~/.vibecrafted/artifacts/<...>/<workflow>/reports/*.meta.json`
aggregated by `agent` field. Cross-check via
`aicx steer --json --agent <agent>` for session corroboration.

**Known data gap**: `meta.json` often has `model: unknown` and
`duration_s: null`. Fix at write time — dispatcher one-line patch. The
dashboard surfaces the gap (counts of unknown-model dispatches) so the
fix lands fast.

### 4. Per-skill stats

Skills are invocation surfaces (vc-ownership, vc-marbles, vc-decorate,
vc-partner, vc-init, etc.). Stats per skill:

- invocations last 7 / 30 / 90 days
- last invocation timestamp
- average success rate
- skills with **zero** invocations in 30 days → "quiet" warning (e.g.
  the vc-partner concern made visible)

**Authoritative source**: `meta.json` `skill` field aggregated. Session
shard activity (`~/.aicx/store/<org>/<repo>/<date>/`) for invocation
cadence corroboration.

### 5. Fleet health

System-side panel:

- disk usage per host
- session index freshness (`semantic lag` from `aicx health --json`)
- session corpus health (missing sidecars count, anomalous bucket names)
- vc-agents endpoint up/down
- MCP server liveness (`loctree-mcp`, `aicx-mcp`, project-specific MCPs)
- Tailscale link health between hosts

**Authoritative sources**:

- `aicx health --json` (built-in, JSON-emit, severity-tagged)
- `df -h` over Tailscale ssh for disk
- MCP `/health` endpoints where exposed

### 6. Failure board (last 24 / 48 / 168 hours)

Every dispatch with `status != completed` or `exit_code != 0` or
`gate: fail` or empty `/tmp/.../tasks/<id>.output`. Each row:

- timestamp
- agent + skill + prompt_id
- failure modality (substrate / scope / implementation / hang / notify-lost)
- recovery dispatch link if one fired (joined via `recovers: <id>` in
  frontmatter)
- one-line excerpt of the worker's failure reason

**Authoritative source**: same `meta.json` filtered. Cross-walk to session
chunk via `(session_id, project, agent)`.

### 7. Operator action queue

The "wystarczy wcisnąć guzik" inbox. Each entry:

- one-line description of what needs the button (push branch X / review
  prompt body Y / merge wave Z into trunk / approve recovery dispatch shape)
- which operator-agent session is waiting
- timestamp queued
- one-click action where the operator can fulfill from terminal

**Authoritative source**: the operator-agent's own stop-point handoff
files (Section 5 of [`AUTONOMY.md`](AUTONOMY.md) shape). Dashboard tails
the `reports/<ts>_stop-point_operator.md` files and surfaces them as
queue items.

---

## Landed vs planned (close-out audit, 2026-06-10 — PLAN_23 Wave D-2)

The seven panels above are doctrine; this table is runtime truth as of the
PLAN_23 close-out. "Landed" means the panel renders in the `voc` Mission
Control tab AND the standalone `vc-admin` renderer (`b534103`, scope fix
`75bc7f5`, binary rename `65c5072`), with Insta snapshot armor in
`vibecrafted-app/tui-agent/tests/mission_control_snapshots.rs`.

| Panel                 | Status  | Landed shape                                                        | Still planned (not wired)                                           |
| --------------------- | ------- | ------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Active dispatches     | LANDED  | control-plane live runs, all roots + root labels, age/ETA, wave     | pidfile + `tasks/*.output` JSONL join; transcript/state viewer link |
| Wave atlas            | LANDED  | `prompt_id` grouping from `meta.json` + live runs, state glyphs     | tracker.md parsing; SHA-on-green; branch; dependency arrows         |
| Per-agent stats       | LANDED  | 30d `meta.json` aggregation: runs/✓/✗/⌀dur/model-known rate         | peer-tier compliance; token/cost rollup; `aicx steer` cross-check   |
| Per-skill stats       | LANDED  | 30d invocations/✓/✗/⌀dur + quiet-skill ⚠ flag                       | 7/90d windows; last-invocation timestamp                            |
| Fleet health          | PARTIAL | control-plane, artifact-root, meta-scan, model/duration parity      | disk per host; `aicx health`; MCP liveness; Tailscale link          |
| Failure board         | LANDED  | 24h window from `meta.json` + live failed runs, reason + age        | failure modality classes; `recovers:` recovery links                |
| Operator action queue | LANDED  | derived: stalled runs + failures + polarize intents + fresh reports | stop-point `.md` tailing; one-click fulfil actions                  |

Cross-cutting close-out notes:

- CLI surface landed as **`vc-admin`** (`vco` renamed in `65c5072`):
  `status` / `wave` / `agent` / `skill` / `failures` / `button` /
  `health` / `watch`.
- DataQuality receipt (scanned/capped/missing-model/missing-duration/
  parse-failures) renders under the panels — the "surface the gap" rule
  from the Known data gap note is live. Wave 0 telemetry fix landed in
  `40935d5` (mislabeled subject; `runtime/scripts/lib/meta.sh`).
- Theme truth: there is no in-app light/dark palette — the dashboard
  emits named ANSI colors only and the vc_frame mesh themes resolve them
  terminal-side. Snapshot suite freezes content + color placement and
  guards the named-ANSI invariant.

---

## Data shape (mapping panel → file)

| Panel                 | Primary file                                               | Secondary                           | Notes                            |
| --------------------- | ---------------------------------------------------------- | ----------------------------------- | -------------------------------- |
| Active dispatches     | `/tmp/<runtime>/<cwd>/<session>/tasks/*.output` + pidfiles | `meta.json` once landed             | live + recent-completion join    |
| Wave atlas            | master-dispatch `tracker.md`                               | git log `[agent/workflow]` prefixes | one tracker per active plan      |
| Per-agent stats       | `~/.vibecrafted/artifacts/*/reports/*.meta.json`           | `aicx steer --json`                 | aggregate over date range        |
| Per-skill stats       | `meta.json` `skill` field                                  | session shard activity              | low-cadence skills flagged       |
| Fleet health          | `aicx health --json`                                       | `df -h` + MCP `/health`             | refresh every N minutes          |
| Failure board         | `meta.json` filtered                                       | session chunks via session join     | recoveries linked via `recovers` |
| Operator action queue | `reports/<ts>_stop-point_operator.md`                      | none                                | tail-driven                      |

**Single most authoritative file across panels**: `meta.json` sidecar in
`~/.vibecrafted/artifacts/`. It is the only file that joins (run_id,
prompt_id, agent, skill, project, branch, commit, status, gate, exit_code,
timestamps, transcript-path) in one place. Live state must come from
`/tmp/<runtime>/`; everything else is corroboration.

---

## Implementation envelope

The dashboard is **not** written here. Two strong candidates for the build
target:

1. **Extend `vc-operator/` Rust workspace** (existing sibling repo with
   `tui-agent` cockpit, mux + tray + shell agents). Add a new crate or
   panel to `tui-agent` that consumes the data sources above. Most
   natural fit — the cockpit already exists, dashboard becomes a tab.
2. **Standalone TUI binary** (new `vc-admin` (formerly `vco`) crate or
   Python `textual` app) reading the same files. Lighter footprint, no
   dependency on the existing cockpit's roadmap.

The dispatch plan for the build itself lives as a `PLAN_23` (sibling to
`PLAN_22_NEXT_OPERATOR_MISSION_CONTROL`) inside
`vc-operator/docs/plans/`. That plan is wave-shaped and gets dispatched
through this very skill — dogfooding: the Agent-Operator dashboard gets
built by an Agent-Operator using vc-operator doctrine.

---

## Why CLI / TUI before web

The operator works in terminal (vc-frame + ssh + git + AICX CLI). A web
dashboard means context-switch to a browser tab. TUI keeps the operator
in their existing flow:

```bash
vc-admin status                  # static snapshot, all panels
vc-admin watch                   # live-refresh, full panel set
vc-admin wave --plan <id>        # focus on wave atlas
vc-admin agent claude            # per-agent panel deep dive
vc-admin failures --since 24h    # failure board
vc-admin button                  # operator action queue
```

Web view comes second, as an `aicx dashboard --serve`-style extension if
the team grows past one operator.

---

## Anti-patterns

- Building the dashboard as a pretty visualization without resolving the
  `model: unknown` + `duration_s: null` gaps at write time → garbage in,
  garbage out.
- Reading `/tmp/.../tasks/<id>.output` for live state via `cat` /
  `tail -f` from the dashboard process → context overflow risk on large
  workers; use streaming-aware reader.
- Polling every panel every second → same anti-pattern as the await
  doctrine. Tail file watchers + `aicx tail --follow` mode.
- Surfacing all skills uniformly without flagging quiet ones → the
  whole reason this exists is to make "alive but quiet" visible (the
  `vc-partner` case).
- Auto-firing actions from the dashboard (push, merge, etc.) → the
  dashboard _surfaces_ the button queue, the operator _presses_ the
  buttons.

---

## Build plan handoff

The concrete build plan ships as:

- `<vc-operator-repo>/docs/plans/PLAN_23_AGENT_OPERATOR_DASHBOARD.md`
  (forward-plan shape per [`vc-init/backlog/HOWTO.md`](../vc-init/backlog/HOWTO.md))

The dashboard ships from the `vc-operator/` Rust workspace after one or
more Wave-shaped dispatches through this skill.

---

## Call to Action

Read PLAN_23 before authoring any dashboard-related dispatch body. Fire
Wave 0 (dispatcher meta.json gap fix) before any data panel hydrates —
otherwise per-agent stats lie. Then Wave A skeleton, then sequential
Wave B data wiring, then parallel Wave C panels, then sequential Wave D
close-out. The dispatch chain is the dogfood.

---

## Closing Rail

```text
=======================
A dashboard that surfaces nothing the operator can't already remember
isn't a dashboard — it's wallpaper. Build for the question the operator
hasn't asked yet but is about to, and surface the data that makes the
next decision trivially safe. (งಠ_ಠ)ง
=======================

Suchar: Why is the operator-action queue the most-watched panel? Because
the only thing harder than firing a wave is remembering you owe a push.
(._.)
```

---

_Vibecrafted. with AI Agents (c)2024–2026_
