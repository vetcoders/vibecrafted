# VC Operator 23: Agent-Operator Dashboard

- Repo: `~/vc-workspace/vetcoders/vc-operator`
- Branch: `main`
- Baseline commit: `c8bb3d2`
- Generated: `2026-05-16`
- Planning mode: forward-plan for the dashboard surface inside the
  existing `tui-agent` cockpit
- Reference shape: `PLAN_22_NEXT_OPERATOR_MISSION_CONTROL.md` (sibling)
- Doctrine: [`../../../vibecrafted/skills/vc-operator/DASHBOARD.md`](../../../vibecrafted/skills/vc-operator/DASHBOARD.md)
  (the _why_ and the panel shape, codified in the agent-side charter)

---

## 1) Cel główny (1:1)

> Agent-Operator pracuje ślepo. Wszystkie dane są na miejscu — AICX
> extracts, `~/.vibecrafted/artifacts/`, `/tmp/claude-<uid>/`, git logs —
> ale **nikt ich nie składa w jeden widok**. Operator widzi pojedyncze
> raporty po fakcie, agent pamięta ostatnie kilka run_id, ale "ile
> dispatch'y miało peer-tier compliance? które skille są martwe? co
> stoi w kolejce 'wciśnij guzik'?" — to dziś zgaduje. Panel admina
> Agent-Operatora załatwia ten brak: jeden ekran, siedem paneli, dane
> z plików które już istnieją.

Operator declaration 2026-05-16: _"potrzebujemy panelu admina i statsów
dla agenta-operatora. Wszystko jest na miejscu!"_

---

## 2) Kontekst wstępny

- Sibling charter: [`vibecrafted/skills/vc-operator/DASHBOARD.md`](../../../vibecrafted/skills/vc-operator/DASHBOARD.md)
  describes the seven panels (Active dispatches, Wave atlas, Per-agent
  stats, Per-skill stats, Fleet health, Failure board, Operator action
  queue) and the data-source → panel mapping.
- `tui-agent/` is the existing terminal cockpit (Rust workspace, Ratatui,
  edition 2024). Dashboard naturally lives as a new tab inside it.
- Authoritative single source for per-dispatch attribution:
  `~/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/<workflow>/reports/*.meta.json`.
- Live state source: `/tmp/claude-<uid>/<encoded-cwd>/<session-uuid>/tasks/<task-id>.output`
  (JSONL, streaming-aware reader required).
- AICX corroboration: `aicx steer --json --agent <x>`, `aicx health --json`,
  `aicx intents --emit json --unresolved`.
- Existing in-house pattern: `aicx dashboard --serve --port 9478` (HTML
  view of AICX corpus — different scope, reusable conventions).

---

## 3) Two upstream data gaps to fix at write time

> The dashboard is only as honest as the data it reads. Two known gaps in
> `meta.json` make per-agent stats lie. Fix at the dispatcher (one-line
> patches) before the dashboard goes live.

- [ ] `model` field is often `unknown` in `meta.json` — dispatcher should
      write the actual model identity. Without it, MODEL PARITY audit panel
      shows garbage.
- [ ] `duration_s` is often `null` in `meta.json` — dispatcher should
      compute on completion. Without it, the wave atlas can't render timing
      bars.

These two fixes are **Wave 0** — they precede every Wave A→D below.
Without them the dashboard ships pretty + wrong.

---

## 4) Target shape

A new tab inside `tui-agent/` titled "Mission Control" (or, if the
operator prefers, a dedicated `vc-admin` / `vco` binary that the
existing cockpit launches). Seven Ratatui panels arranged in a
responsive grid:

```text
┌─ Active dispatches (live) ────┬─ Wave atlas (current plan) ─┐
│ Wave B-4 claude inspectors    │ Wave A ✓ shell  (f6b02744)  │
│   run_id just-185156          │ Wave B 3/4 [x][x][x][ ]     │
│   ETA ~12min · t+04:21        │ Wave C ⏳ parallel slot      │
└───────────────────────────────┴─────────────────────────────┘
┌─ Per-agent stats (last 30d) ──────────────────────────────────┐
│ claude   72  ✓94% ⌀14min  peer-tier 100%  $XX                │
│ codex    48  ✓91% ⌀11min  peer-tier 100%  $XX                │
│ gemini   31  ✓88% ⌀19min  peer-tier 100%  $XX                │
└───────────────────────────────────────────────────────────────┘
┌─ Per-skill invocations ────┬─ Fleet health ───────────────────┐
│ vc-ownership  ████ 42      │ disk host-a  ▓▓▓▓▓░ 78%          │
│ vc-marbles    ███  31      │ disk host-b  ▓▓▓░░░ 42%          │
│ vc-decorate   ██   18      │ aicx index   stale ⚠ (94h lag)   │
│ vc-partner    ▏     2  ⚠   │ vc-agents up · MCP servers OK    │
│ vc-workflow   ██   15      └──────────────────────────────────┘
└────────────────────────────┐
┌─ Failure board (24h) ─────┐│ Operator action queue:           │
│ - just-184... gemini stall││ ⌨ push feat/textforge-editor-…   │
│   recovery: just-185...   ││ ⌨ review prompt body 04-...      │
│                           ││ ⌨ merge Wave B → trunk           │
└───────────────────────────┘└──────────────────────────────────┘
```

CLI surface (whether a tab or standalone binary):

```bash
vco status                  # static snapshot, all panels
vco watch                   # live-refresh, full panel set
vco wave --plan <id>        # focus on wave atlas
vco agent claude            # per-agent panel deep dive
vco skill vc-partner        # per-skill panel deep dive
vco failures --since 24h    # failure board
vco button                  # operator action queue
vco health                  # fleet health only
```

---

## 5) Reusable pieces from the existing tree

| Surface              | Reuse from                                                                       | Notes                                                       |
| -------------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Ratatui panel layout | `tui-agent/src/ui/mod.rs`                                                        | Existing grid + tab pattern                                 |
| File watcher infra   | `tui-agent/` (existing `notify` crate usage)                                     | For `/tmp/.../tasks/` + `~/.vibecrafted/artifacts/` tailing |
| JSON parsing         | `serde` / `serde_json` already in workspace                                      | meta.json + aicx health JSON                                |
| AICX query           | `aicx serve` MCP endpoint OR `Command::new("aicx").args(["steer", "--json", …])` | Two integration paths; pick at Prompt 3                     |
| Color / theming      | existing `tui-agent` palette (mid-light / mid-dark)                              | No new tokens                                               |
| Git log parsing      | `git2` crate or `Command::new("git")`                                            | Per-author + `[agent/workflow]` prefix extraction           |
| Disk health          | `Command::new("df").args(["-h"])` over Tailscale ssh                             | One impl, two hosts (host-a + host-b)                       |

---

## 6) Out of scope for this plan

- [ ] Web UI / browser dashboard (TUI-first per `DASHBOARD.md` Section
      "Why CLI / TUI before web")
- [ ] Auto-firing actions from the dashboard (push, merge) — dashboard
      _surfaces_ the button queue, operator _presses_ the buttons
- [ ] Multi-operator support (one operator today)
- [ ] Mobile / iOS shell extension (`shell-agent/` stays Mac-only for now)
- [ ] Replacing `aicx dashboard --serve` (different scope; complementary)

---

## 7) Dispatch plan (Wave 0 → Wave D)

### Wave 0 — Upstream data fixes (parallel-safe, backend only)

- [ ] **0-1** `vc-justdo codex --file 00-dispatcher-model-and-duration.md`
  - Mission: patch the dispatcher (wherever `meta.json` is written) so
    `model` carries the actual model identity and `duration_s` is
    computed at completion. One-line fixes in two spots probably.
  - Agent: codex (backend-only, surgical patch)
  - Baseline: `vibecrafted` repo or wherever the dispatcher lives —
    confirm via `grep -rIn 'meta.json' vibecrafted/` first
  - Branch: `fix/dispatcher-meta-model-duration`
  - Acceptance: next dispatched run produces `meta.json` with non-null
    `model` and `duration_s`.

### Wave A — Foundation (sequential, single agent)

- [ ] **A-1** `vc-justdo claude --file 01-a-mission-control-tab-skeleton.md`
  - Mission: add a new "Mission Control" tab to `tui-agent/` with
    seven empty Ratatui panels in the grid layout shown in Section 4.
    No data wiring yet — just the shell.
  - Agent: claude (UI + Rust, owns the cockpit shape)
  - Baseline: `main@c8bb3d2`
  - Branch: `feat/mission-control-shell`
  - Acceptance: tab opens, all seven panels render with "loading…"
    placeholders, navigable via Tab key.

### Wave B — Sequential data wiring (shared `MissionControlState` struct)

- [ ] **B-1** `vc-justdo claude --file 02-b-state-machine-and-watchers.md`
  - Mission: introduce `MissionControlState` struct, wire `notify`
    watchers for `~/.vibecrafted/artifacts/` and `/tmp/claude-<uid>/`,
    set up the async runtime tasks that hydrate state from disk.
  - Agent: claude (Rust state machine is its sweet spot)
- [ ] **B-2** `vc-justdo gemini --file 03-b-active-dispatches-panel.md`
  - Mission: wire panel "Active dispatches" — read pidfiles + tasks
    output, render run_id / agent / skill / wave / ETA per row.
  - Agent: gemini (rotation)
- [ ] **B-3** `vc-justdo codex --file 04-b-wave-atlas-panel.md`
  - Mission: wire panel "Wave atlas" — read tracker.md or master
    dispatch index, render `[ ]`/`[x]` per prompt with SHA on green.
  - Agent: codex (rotation, plus checkbox parser is natural for codex)
- [ ] **B-4** `vc-justdo claude --file 05-b-operator-action-queue.md`
  - Mission: wire panel "Operator action queue" — tail
    `reports/<ts>_stop-point_operator.md` files, surface unchecked
    items from "What's NOT done (deliberately)" sections.
  - Agent: claude (rotation)

### Wave C — Parallel disjoint panels

- [ ] **C-1** `vc-justdo gemini --file 06-c-per-agent-stats.md`
  - Mission: panel "Per-agent stats" from `meta.json` aggregation
    over last 7 / 30 / 90 days. AICX corroboration optional.
- [ ] **C-2** `vc-justdo gemini --file 07-c-per-skill-stats.md`
  - Mission: panel "Per-skill stats" + "quiet skill" warning flag.
- [ ] **C-3** `vc-justdo codex --file 08-c-fleet-health-and-failures.md`
  - Mission: panels "Fleet health" (aicx health + df -h + MCP pings)
    and "Failure board" (meta.json filter). Two panels because they
    share the same `Command::new` infrastructure.

### Wave D — Final close-out (sequential, after Wave B+C merge)

- [ ] **D-1** `vc-justdo codex --file 09-d-cli-surface-vco-binary.md`
  - Mission: expose the dashboard via standalone `vco` binary (or
    `tui-agent` subcommand) — `vco status`, `vco watch`, `vco wave`,
    `vco agent`, `vco skill`, `vco failures`, `vco button`,
    `vco health`. Same data layer as the tab, different rendering
    surface.
- [ ] **D-2** `vc-justdo claude --file 10-d-e2e-and-docs.md`
  - Mission: end-to-end test (Insta snapshots of all seven panels in
    both themes), README update in `vc-operator/`, backlog close-out
    entry in `unicode-puzzles-portal/docs/backlog/` (since the
    dispatcher fix from Wave 0 lives there), final stop-point
    handoff.

---

## 8) Wave dependencies

```text
Wave 0 (parallel-safe, backend only)
  ↓
Wave A (foundation, sequential, 1 prompt)
  ↓
Wave B (sequential chain, 4 prompts)
  ↓
[operator-side merge of Wave B → main]
  ↓
Wave C (parallel, 3 prompts)
  ↓
[operator-side merge of Wave C → main]
  ↓
Wave D (sequential, 2 prompts)
  ↓
close-out + handoff
```

Wave 0 is parallel-safe with every other wave (backend-only, different
repo) — fire it first or alongside Wave A; doesn't matter as long as
it lands before Wave C-1 (Per-agent stats reads `model` field).

---

## 9) Operator handoff

The plan ships as `~/vc-deliveries/PLAN_23_AGENT_OPERATOR_DASHBOARD.md`
on host-a. Operator-agent loads via:

```bash
vc-operator claude --file ~/vc-deliveries/PLAN_23_AGENT_OPERATOR_DASHBOARD.md
```

The per-prompt bodies (`00-dispatcher-…`, `01-a-…`, etc.) get authored
by the operator-agent during plan onboarding (Phase 1 of `vc-operator`
SKILL.md) — they're not written here because data sources may shift
between now and dispatch.

Wave 0 fires first against `vibecrafted/` (dispatcher patch). Waves
A→D fire against `vc-operator/` (this repo).

The operator presses every push / PR / merge button per
`vc-operator/AUTONOMY.md` hard-stop policy.

---

## 10) Acceptance for the whole plan

- [ ] Wave 0 lands: next 5 dispatches produce `meta.json` with non-null
      `model` and `duration_s`.
- [ ] Wave A lands: Mission Control tab opens, seven empty panels render
      in both themes.
- [ ] Wave B lands: top three panels (Active / Wave atlas / Action queue)
      hydrate from disk on tab open and refresh on file change.
- [ ] Wave C lands: four data panels (Per-agent / Per-skill / Fleet
      health / Failures) hydrate and refresh on schedule.
- [ ] Wave D lands: `vco` CLI commands work standalone; Insta snapshots
      green in both themes; README + backlog close-out written.
- [ ] Operator can `cargo install --path tui-agent` (or `vco` crate) and
      see real fleet state in <2s after launching.

---

## 11) Close-out (filled 2026-06-10, Wave D-2)

> Naming note: the `vco` binary named throughout this plan shipped and was
> then renamed to **`vc-admin`** in `65c5072` (the `voc` cockpit binary
> keeps its name; only the standalone snapshot renderer was renamed).
> Wherever this plan says `vco <cmd>`, runtime truth is `vc-admin <cmd>`.

- [x] Wave 0 → `40935d5` — dispatcher telemetry (`model` + `duration_s`
      written into `meta.json` via `runtime/scripts/lib/meta.sh`).
      NOTE: the commit subject is mislabeled (`feat(install): storytelling
output`) — the telemetry cut rode along in the same landing; the
      stat shows `lib/meta.sh +114` as the actual Wave 0 surface.
- [x] Wave A → `b534103` — Mission Control tab skeleton landed inside the
      same cut as Wave B (single landing, see below).
- [x] Wave B → `b534103` — `MissionControlState` + panel wiring landed as
      one commit (`feat(vibecrafted-app): land voc overlay + Mission
Control dashboard`), not four sequential prompts as planned.
- [x] Wave C → `b534103` (data panels landed in the same cut) + scope fix
      `75bc7f5` (Monitor/Active-dispatches default scope widened to all
      live runs across roots, with root labels).
- [x] Wave D → D-1 `65c5072` (standalone snapshot renderer binary,
      `vco` → `vc-admin` rename) · D-2 = the commit introducing this
      close-out (`test(mission-control): snapshot all panels in both
themes + PLAN_23 close-out`) — Insta snapshot suite in
      `tui-agent/tests/mission_control_snapshots.rs` freezing all seven
      panels (TUI buffer content + color map + empty state) and the
      `vc-admin status` text surface end-to-end.
- [x] Theme close-out truth: the "mid-light / mid-dark tui-agent palette"
      named in §5 does not exist as an in-app switch. `voc` emits named
      ANSI colors only; light/dark resolution happens terminal-side via
      the vc_frame themes (`config/vc-frame/themes/vetcoders-mesh.kdl`), so
      both themes consume one identical buffer. The snapshot suite
      freezes that buffer (content + color placement) and the
      `mission_control_palette_is_terminal_theme_adaptive` test guards
      the named-ANSI invariant that keeps it theme-correct.
- [ ] Retro entry in `docs/backlog/` for any pattern that emerges from
      the build (likely: notify-driven Rust dashboard pattern, since this
      is the first one we're building this shape)
- [ ] Known observed quirk (snapshot evidence, not fixed in D-2 — D-2
      observes, it does not redesign): `wave_atlas_from_meta` counts a
      run with BOTH `exit_code != 0` AND a `status` containing
      "fail"/"error" twice in `failed`, so a wave can render
      `failed > total` (see `vc_admin_status` snapshot: wave-a
      TOTAL 2 / FAILED 2 with one actual failure). Needs a follow-up cut.

---

_Plan szyty z duchem Emila (`[ ]` → `[x]`, numbered, voiced, sealed (1:1)).
Latarka po obu stronach świeci._

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_

---

Additional notes:

## CSI u

There is also a Kitty key reporting protocol, which is a more modern and powerful alternative to CSI u.

CSI u mode is no longer recommended. Applications should implement the Kitty key reporting protocol instead.

The specification may be found at:

- [Fix Keyboard Input on Terminals - Please](https://www.leonerd.org.uk/hacks/fixterms/).
- [Kitty Key Reporting Protocol](https://github.com/kovidgoyal/kitty/blob/master/docs/key-protocol.md)

The protocol is sometimes referred to as libtermkey or libtickit.
