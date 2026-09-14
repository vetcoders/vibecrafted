<!-- Provenance: designed by W8-A research run rsch-100746-49793
     (claude, 2026-06-11), landed verbatim by the operator.
     Mission report: intents-zero/reports/W8-A_fleet-health-probes-design.md -->

# W8-A — Fleet-health probes (PLAN_23 C-3) — DESIGN (research mode)

> **Mode note.** Dispatched under the Research Safety Contract: read-only on
> the source repo, **no git writes, no source mutation**. The original W8-A
> brief asked to create `runtime/docs/fleet-health-probes.md` and commit it;
> the research wrapper overrides that. **This report IS the deliverable** —
> it carries the full design a future implement-wave (or the operator) can
> land verbatim as `runtime/docs/fleet-health-probes.md`. No file was created
> in-repo; no commit was made. Working tree left unchanged (`marbles_watcher.sh`
> is W6-B's pre-existing edit, not touched here).

---

## Current state — what the fleet already knows about its own health

Three health surfaces exist. None of them measure the **substrate** the fleet
stands on. The gap is confirmed by runtime truth, not assumed.

### Inventory (file:line, with non-duplication argument)

| #   | Surface                                  | Evidence                                                                                                                                                                                                                                                         | What it measures                                                                                                                                      | Why it does NOT cover the four probes                                                                                                                                                                                                                                   |
| --- | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Mission Control "Fleet health" panel** | `vibecrafted-app/tui-agent/src/mission_control.rs:890` `fleet_health_from_inputs()`; struct `FleetHealthSignal{label,status,detail}` at `:116`; enum `FleetHealthStatus{Ok,Warn,Blocked,Unknown}` at `:122`                                                      | Pushes exactly 5 signals: `control-plane`, `artifact-root`, `meta scan`, `model parity`, `duration parity` (`:902`–`:977`)                            | All five are **about the dashboard's own data quality** (is the control-plane dir present, did meta.json scan, are model/duration fields populated). Zero substrate signals — no disk, no aicx index, no MCP reachability, no mesh. This is the seam the probes extend. |
| 2   | **Control-plane run record**             | `vibecrafted-core/vibecrafted_core/control_plane.py:100` `health` field; `:237` `_state_health()`; `:365` `_reconcile_dead_launcher()` (reaper, commit `eddef2e`). Consts: `RUN_STALL_SECONDS = 20*60` (`:78`), `LIVENESS_STALE_HEARTBEAT_SECONDS = 120` (`:79`) | **Per-run** liveness: `final`/`stalled`/`active`/`unknown`, reconciled on read when there is no live launcher proof and heartbeat/start time is stale | Scoped to a single run's lifecycle (pid alive? heartbeat fresh?). Says nothing about whether the _machine under all runs_ is out of disk or off the mesh. Sample record confirms fields: `health`, `liveness`, `heartbeat_at`, `transcript_bytes`, `launcher_pid`.      |
| 3   | **Installer doctor**                     | `scripts/vetcoders_install.py:5008` `cmd_doctor()`; `run_doctor()`; MCP `verify_cmd` specs `loctree-mcp --version`/`aicx-mcp --version` at `:279`–`:312`; `DoctorFinding` levels `ok`/`warn`/`fail` (`:549`–`:551`)                                              | **Install-time** binary presence + version + skill-symlink integrity                                                                                  | Doctor proves the MCP _binary is installed at the right version once_. It never asks "is that server **reachable right now**, mid-session?" — which is the MCP probe. Different time axis (install vs runtime).                                                         |

### The gap, stated by runtime truth (not inference)

`skills/vc-operator/DASHBOARD.md:167` — the PLAN_23 close-out "Landed vs
planned" table — marks the Fleet-health panel **PARTIAL** and names the
unwired columns literally:

```
| Fleet health | PARTIAL | control-plane, artifact-root, meta-scan,
  model/duration parity | disk per host; aicx health; MCP liveness;
  Tailscale link |
```

So the four probe families are not my invention — they are the explicit
"Still planned (not wired)" cell. `PLAN_23` §7 C-3
(`vibecrafted-app/docs/plans/PLAN_23_AGENT_OPERATOR_DASHBOARD.md:196`) named
the same shape: "Fleet health (aicx health + df -h + MCP pings)". **This design
honors the plan's named shapes; it does not silently diverge.** Two corrections
to plan assumptions are flagged explicitly below (aicx flag, run-record
coupling).

---

## Proposal — four probes that rhyme with reconcile-on-read

**Design spine (the one rule).** Probes mirror the reaper (`eddef2e`):
**cheap on read, no daemons unless a cost forces it.** The dashboard already
ticks ~250ms and "must not stall the operator on disk IO"
(`mission_control.rs:12`), and already load-sheds via `META_SCAN_CAP`
(`:15`). Probes obey the same discipline: pure-syscall probes run on every
read; the one genuinely expensive probe (aicx) is TTL-cached, never daemonized.

All four reuse the **existing** `FleetHealthSignal{label,status,detail}` +
`FleetHealthStatus{Ok,Warn,Blocked,Unknown}` types. **No new types, no new
panel** — they append signals into the same `Vec` that
`fleet_health_from_inputs()` already returns. Status mapping:
`Ok→✓`, `Warn→!`, `Blocked→✗`, `Unknown→?` (markers already defined at `:131`).
The doctrine triad ok/warn/fail maps to `Ok`/`Warn`/`Blocked`; `Unknown` is
the honest "couldn't probe" fallback (probe binary absent, permission denied).

### Probe 1 — DISK (the funeral we already had)

- **Measured:** free bytes + free-% on each substrate filesystem
  (`~/.vibecrafted/control_plane`, `~/.codex`, `~/.aicx`,
  `~/.vibecrafted/artifacts`), **and the inherited `ulimit -f` soft cap**
  (`getrlimit(RLIMIT_FSIZE)`), plus the largest single log/db file vs that cap.
- **Threshold semantics:**
  - `Ok` — free-% > 15% **and** `ulimit -f` unlimited.
  - `Warn` — free-% 5–15% **or** `ulimit -f` finite but > 256 MB-equiv.
  - `Fail` (`Blocked`) — free-% < 5% **or** `ulimit -f` finite ≤ 64 MB-equiv
    (the SIGXFSZ trap zone) **or** any tracked file ≥ 80% of a finite cap.
- **Collection cost:** ~µs. `statvfs(path)` per mount + one `getrlimit`.
  **No subprocess. On-read every tick.**
- **Surface:** `FleetHealthSignal{label:"disk <mount>", …}` and a dedicated
  `FleetHealthSignal{label:"ulimit -f", …}`. Rendered in the existing Fleet
  health panel and `vc-admin health`. **Not** a run-record field (see Open Q3).
- **Motivating incident (test-case):** 182 MB `~/.codex/logs_2.sqlite` +
  inherited `ulimit -f 65336` → **SIGXFSZ (exit 153)** killed workers in <10 s
  (fixed `5c6c439`). The fix removed the cap (`launcher.sh:47`
  `ulimit -f unlimited`), but **the condition was invisible until forensics**.
- **How it surfaces EARLIER:** a finite `ulimit -f` is itself a `Fail` signal —
  it _is_ the trap. The operator would read `ulimit -f = 65336 blk (≈32 MB) ✗`
  on the panel **before any worker dies**, not from a SIGXFSZ obituary. Disk-%
  catches the slower sibling failure (sqlite growth filling the volume).

### Probe 2 — AICX (silent recall rot)

- **Measured:** semantic-index staleness (lag hours), missing extracts/sidecar
  count, extractor failures — parsed from `aicx health` JSON severity.
- **Threshold semantics:**
  - `Ok` — lag < 24 h **and** missing-sidecars == 0.
  - `Warn` — lag 24–72 h **or** a small missing-sidecar count.
  - `Fail` — lag > 72 h **or** extractor failures present (postcompact broke).
- **Collection cost:** **medium — the one expensive probe.** `aicx health`
  walks the corpus (subprocess, not µs). → **TTL-cached** (refresh every
  60–120 s, store last result + timestamp). Never daemonized.
- **Surface:** `FleetHealthSignal{label:"aicx index", …}` →
  `"aicx index stale ⚠ (94h lag)"` (matches PLAN_23 §4 mock literally,
  `:89`).
- **Motivating incident:** postcompact hooks fail when extracts are missing;
  index staleness silently degrades agent recall.
- **How it surfaces EARLIER:** today staleness is _silent_ — agents just recall
  worse. The probe turns a 94 h lag into a visible `Warn`/`Fail` line, so the
  operator runs `aicx index` _before_ the next wave dispatches on a stale brain.
- **⚠ Plan correction (runtime truth):** DASHBOARD.md `:117` and `PLAN_23` say
  `aicx health --json`. **That flag does not exist** — `aicx health` rejects
  `--json` (`error: unexpected argument '--json'`) because it emits JSON
  **unconditionally** ("Emit the full AICX health report as JSON for
  automation"). The implement-wave must call `aicx health` with **no flag**.
  `aicx doctor` is the repair sibling.

### Probe 3 — MCP (stale structural sight mid-session)

- **Measured:** liveness/reachability of each configured MCP server
  (`loctree-mcp`, `aicx-mcp`, `vibecrafted-mcp`, project MCPs) **right now**,
  plus loctree snapshot staleness vs git HEAD (the SessionStart card already
  computes "snapshot stale").
- **Threshold semantics:**
  - `Ok` — process alive **and** (for loctree) snapshot fresh.
  - `Warn` — alive but loctree snapshot stale (rescan needed).
  - `Fail` (`Blocked`) — a _critical_ server (loctree-mcp, aicx-mcp) not
    responding. Non-critical servers down → `Warn` (see Open Q4).
- **Collection cost:** cheap. Process-alive = pid check (µs). Snapshot-stale =
  compare snapshot mtime vs git HEAD (already derived by `loct`). A _real
  handshake ping_ is costlier → describe-only, gate behind cadence if added.
  **No network calls in scope** — describe, don't probe (per brief).
- **Surface:** `FleetHealthSignal{label:"mcp <server>", …}`.
- **Motivating incident:** loctree-mcp snapshot staleness mid-session; servers
  dying between rounds.
- **How it surfaces EARLIER:** distinct from installer doctor (which checks the
  _binary version once_). This probe catches the server that was fine at
  install but **died between rounds** or whose snapshot drifted — surfacing
  `loctree-mcp snapshot stale` _before_ an agent acts on stale structural facts.

### Probe 4 — TAILSCALE (dispatching into the void)

- **Measured:** mesh peer reachability — which fleet hosts (host-a, host-b, …)
  are online in the tailnet, from `tailscale status --json` (local daemon
  query).
- **Threshold semantics:**
  - `Ok` — all expected dispatch-target peers online.
  - `Warn` — a non-critical peer offline.
  - `Fail` (`Blocked`) — a dispatch-target peer (host-a/host-b) offline.
- **Collection cost:** cheap-medium. `tailscale status --json` hits the
  **local** daemon (no remote round-trip). On-read or short cadence.
  Confirmed available: `/opt/homebrew/bin/tailscale`, `tailscale status` lists
  peers (`host-b 100.64.0.11`, `host-f`, …).
- **Surface:** `FleetHealthSignal{label:"tailscale <peer>", …}`.
- **Motivating incident:** remote fleet dispatch depends on mesh reachability;
  a down peer = a silently-failed remote dispatch.
- **How it surfaces EARLIER:** the operator reads `host-b unreachable ✗` _before_
  firing a remote wave at it, instead of discovering it through a dead
  dispatch. **Gating note:** the remote half of the DISK probe (`PLAN_23` §5
  `df -h over Tailscale ssh`) depends on this probe — Tailscale must be `Ok`
  before remote-disk is even attempted. So this probe sequences ahead of any
  remote-disk extension.

---

## Execution — implementation sketch (for the implement-wave)

### Smallest first wave: DISK, end-to-end

**Why disk first:** (a) cheapest — pure `statvfs`/`getrlimit`, no subprocess;
(b) highest-severity incident (SIGXFSZ killed workers — the literal funeral);
(c) zero new dependencies; (d) fully self-contained (needs neither tailscale
nor aicx wiring).

**Named seam (one function, append-only into the existing Vec):**

```
vibecrafted-app/tui-agent/src/mission_control.rs
  + fn disk_health_signals() -> Vec<FleetHealthSignal>
      // statvfs over a fixed substrate-path list + getrlimit(RLIMIT_FSIZE)
  ~ fn fleet_health_from_inputs(...)        // :890 — APPEND, do not rewrite
      signals.extend(disk_health_signals()); // after the existing 5 pushes
```

Reuses `FleetHealthSignal` / `FleetHealthStatus` verbatim. A new snapshot case
in `tui-agent/tests/mission_control_snapshots.rs` freezes the disk lines
(content + color map) — but `tests/**` is out of _this_ design's edit scope;
the implement-wave owns it. End-to-end means: signal renders in both the `voc`
tab and `vc-admin health`, with the named-ANSI invariant intact.

### Sequencing for the rest (cheapest/most-self-contained → most-external)

```
W1  DISK       syscall, on-read           → disk_health_signals()
W2  TAILSCALE  local daemon, on-read       → tailscale_health_signals()
W3  MCP        pidcheck on-read + loct snapshot reuse → mcp_health_signals()
W4  AICX       subprocess, TTL-cached      → aicx_health_signals()
               + introduces ProbeCache{last_run, ttl, result} — ONLY here,
                 when the first expensive probe actually needs it
```

All four are `-> Vec<FleetHealthSignal>`, all called from
`fleet_health_from_inputs()` by **appending** to `signals`. The TTL-cache
scaffolding is deferred to W4 (YAGNI: the syscall probes don't need it).

---

## Open risks / Open questions (operator decisions)

1. **Daemon vs on-read for aicx (the one expensive probe).** Recommend
   **on-read with TTL cache** (no daemon) — rhymes with reconcile-on-read.
   Alternative: a background refresh task (more moving parts).
2. **Where thresholds live.** The repo already has both patterns: hardcoded
   consts (`META_SCAN_CAP`, `STALL_AFTER_MINUTES` in `mission_control.rs`) and
   env-overridable (`LIVENESS_STALE_HEARTBEAT_ENV` in `control_plane.py`).
   Recommend **consts + env override for the SIGXFSZ-critical `ulimit -f`
   threshold** specifically (it's the one with a body count).
3. **Run-record `substrate` field — yes or no?** Recommend **no.** Substrate
   is **fleet-global**, not per-run; coupling it into every run snapshot
   (`_artifact_projection`) bloats records with redundant copies. Keep probes
   in the **dashboard read path**, mirroring reconcile-on-read. (Plan §5
   implied a run-side surface; this is an explicit, argued divergence.)
4. **Which MCP servers are "critical" (Fail) vs "optional" (Warn)?** Recommend
   loctree-mcp + aicx-mcp = critical; project MCPs = warn.
5. **Remote-host disk.** Probe local-only first; remote `df -h over Tailscale
ssh` (PLAN_23 §5) is a network call → out of first-wave scope, and gated by
   the Tailscale probe being `Ok`.
6. **aicx flag discrepancy** (already corrected above): implement-wave calls
   `aicx health` (no `--json`).

## Next move

Operator lands this design as `runtime/docs/fleet-health-probes.md` (or hands
it to an implement-wave). First implementable cut = **DISK probe**, seam
`disk_health_signals()` appended into `mission_control.rs:890`. One probe,
one commit, snapshot-armored.

---

## Gate results (`make check`)

```
Running shellcheck on 137 shell files...
Check complete.
EXIT=0
```

Docs-only research round — `make test` deliberately NOT run (suite mid-repair
by W6-B; not my signal, per brief). Working tree unchanged by this run
(`git status --porcelain` shows only `M runtime/scripts/marbles_watcher.sh`,
which is W6-B's pre-existing edit — untouched here).

## Files changed

None — research read-only mode (no git writes, no source mutation per the
Research Safety Contract). The design lives in this report. `git diff --stat`
would be empty for this worker.

## Acceptance verification (brief Section "Acceptance", flipped)

- [x] Design doc covers four probe families (disk, aicx, MCP, Tailscale):
      measured signal, ok/warn/fail semantics, collection cost, surface.
- [x] Each probe cites its real incident and states how it surfaces EARLIER.
- [x] Explicit inventory of existing health surfaces (file:line refs) + a
      non-duplication argument per surface.
- [x] Implementation sketch: smallest first wave (DISK e2e) + sequencing for
      the rest; named seams (`disk_/tailscale_/mcp_/aicx_health_signals()` into
      `fleet_health_from_inputs()` at `mission_control.rs:890`).
- [x] Open questions section for operator decisions (daemon-vs-on-read,
      threshold location, run-record coupling, MCP criticality, remote disk).
- [x] Gates green (`make check` EXIT=0, docs/research-only).
- [~] PLAN_23 C-3 named shapes honored — two corrections flagged explicitly
  (aicx `--json` flag does not exist; run-record coupling argued against),
  never silently diverged.

## PROBE LEDGER

| Probe family  | Measured signal                                                                   | Threshold (ok / warn / fail)                                                        | Surface                                                                                  | Motivating incident                                                                         |
| ------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| **DISK**      | free-% per substrate mount + `ulimit -f` (RLIMIT_FSIZE) + largest file vs cap     | free>15% & unlimited / free 5–15% or cap>256MB / free<5% or cap≤64MB or file≥80%cap | Fleet health panel + `vc-admin health` (`FleetHealthSignal` "disk <mount>", "ulimit -f") | 182MB `logs_2.sqlite` + `ulimit -f 65336` → SIGXFSZ exit 153, workers dead <10s (`5c6c439`) |
| **AICX**      | index lag hours + missing-sidecar count + extractor failures (`aicx health` JSON) | lag<24h & 0 missing / lag 24–72h or few missing / lag>72h or extractor fail         | `FleetHealthSignal` "aicx index", TTL-cached                                             | postcompact hooks fail on missing extracts; staleness silently degrades recall              |
| **MCP**       | per-server process-alive + loctree snapshot-vs-HEAD staleness                     | alive & snapshot fresh / alive but snapshot stale / critical server down            | `FleetHealthSignal` "mcp <server>"                                                       | loctree-mcp snapshot staleness mid-session; servers dying between rounds                    |
| **TAILSCALE** | peer reachability (`tailscale status --json`, local daemon)                       | all targets online / non-critical peer down / dispatch-target (host-a/host-b) down  | `FleetHealthSignal` "tailscale <peer>"                                                   | remote fleet dispatch depends on mesh reachability; down peer = silent dispatch failure     |

---

_Research worker `claude` · rsch-100746-49793 · 2026-06-11. Loctree-first
perception (context → find health/liveness → focus mission_control → occurrences
doctor), runtime-truth verified (ran `aicx health`, `tailscale status`,
`make check`; read live run-record JSON + control_plane.py + mission_control.rs).
A fleet that can't feel its own disk filling up learns about it from a SIGXFSZ
obituary — these are the nerves, designed before the next funeral._
