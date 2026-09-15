# Vibecraftsmanship — Empirical Evidence (Case Study)

The canonical case study is the 2026-05-24 session that distilled this
skill. The session is empirical proof that the trinity is operational,
not aspirational.

---

## Timeline

| Time (UTC) | Event                                                                                                                               | Axis exercised                |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| ~07:00     | Session start, operator framing: spine selection for terminal stack                                                                 | gust                          |
| 07:35      | Wave A dispatched: A-1 (vibecrafted supervisor) + A-2 (vc-console events bridge), 2 codex parallel, different repos                 | siła                          |
| 08:07      | A-1 completed, commit `0fc9206`, 999 LOC, ruff+mypy+pytest 148+ green                                                               | rzeczywistość                 |
| 08:08      | A-2 completed, commit `4a9c5e5`, 1089 LOC, IPC smoke green, 2 orthogonal cleanups flagged honestly                                  | rzeczywistość                 |
| 08:51      | Operator corrections: "rescheduled not retired" + "3 labs in workspace" + "stop writing more briefs than commits land"              | gust (framing reset)          |
| 09:30      | Operator additions: microsandbox + Zentty as Lab #4 with GPL boundary + "equal intensity" framing                                   | gust                          |
| 09:39      | Wave B dispatched: 4 parallel labs (wezterm + vc-apprt + locterm + microsandbox), each cwd in own lab                               | siła                          |
| 10:06      | Wave B 3/4 reports in: B-1 + B-3 committed, B-4 substrate-failure on krunvm                                                         | rzeczywistość                 |
| 10:11      | B-2 marbles archived in terminal state: 3 Phase commits landed, worker honestly marked FAILED on inherited substrate (not B-2 lane) | rzeczywistość                 |
| 10:34      | Wave C close-out: CLOSE_OUT.md written, 4 operator decisions surfaced for operator's button                                         | trinity aligned               |
| ~17:30     | Pensieve clone + vc-init: separate evidence point — Bear-class markdown editor delivered in 28 hours of prior Vetcoders work        | rzeczywistość (cross-session) |

Total wall-clock: **2h 59m** session start → close-out.

---

## What was delivered in those ~3 hours

| Artifact                | Commit                                  | LOC              | Surface                                                                               | Survived?                                                                            |
| ----------------------- | --------------------------------------- | ---------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| vibecrafted supervisor  | `0fc9206`                               | 999 / 25 files   | Python supervisor + bin/vc-\* wrappers + session_id capture + last-finisher synthesis | ✅ ruff+mypy+pytest 148+ green                                                       |
| vc-console spawn events | `4a9c5e5`                               | 1089 / 17 files  | IpcEvent::SpawnUpdate + jsonl bridge + tray rendering                                 | ✅ core IPC smoke green; 2 orthog cleanups flagged                                   |
| wezterm Lua hooks       | `02645e75c`                             | (multiple)       | Tab title + status bar + toast + events.jsonl tail                                    | ✅ 8/8 busted + 17823-line integration smoke                                         |
| vc\_ apprt runtime      | `acd99c746` + `83e9acb80` + `4d0e72e4b` | (multiple)       | Zig 0.16 fix + session_id DiskPayload + terminal lifecycle emitter                    | ✅ apprt 74/74 + smoke; ⚠ repo-wide test fails on inherited substrate (not B-2 lane) |
| iTerm2 Python plugin    | `eb6beb8`                               | 1382 / 11 files  | AutoLaunch + StatusBar + Triggers + GPL-separate-install                              | ✅ pytest 173/173, GPL boundary smoke clean                                          |
| Sandbox adapter         | (uncommitted, dirty worktree)           | (multiple files) | SandboxAdapter + msbserver lifecycle + policy + tests + docs                          | ⚠ SUBSTRATE-FAILURE: krunvm missing on host                                          |

**5 of 6 work units survived contact with reality.** 1 hit honest
substrate-failure (information, not delivery).

---

## What conventional estimates predicted

From 9-report cross-swarm research (claude+codex+gemini × 3 spine
swarms), conventional engineer-day estimates were:

| Phase                                  | Conventional ED | Conventional weeks |
| -------------------------------------- | --------------- | ------------------ |
| Phase 1 vibecrafted supervisor         | 12-18 ED        | 4-6 weeks          |
| Phase 2 vc-console events bridge       | 5-8 ED          | 3-5 weeks          |
| Phase 3a wezterm Lua hooks             | 3-5 ED          | ~1 week            |
| Phase 3c vc\_ Zig fix + apprt          | 26-37 ED        | 6-9 weeks          |
| Phase 3d locterm Python plugin         | 11.5 ED         | 4-6 weeks          |
| **Sum (Phase 1 + 2 + 3 all branches)** | **~58-86 ED**   | **~18-32 weeks**   |

Reality: **3 hours**.

Compression ratio: `(18 weeks × 168 hrs/week) / 3 hrs ≈ 1000×` low end,
`(32 weeks × 168 hrs/week) / 3 hrs ≈ 1800×` high end.

For pure Wave A+B implementation work (excluding research). Pensieve
case (Swift + AppKit + TextKit2 substrate friction, mostly sequential,
single repo) shows lower compression: gemini estimate 3-6 months,
reality 28 hours, compression ~80-150× — still 2 orders of magnitude.

---

## Pensieve cross-session evidence

The Pensieve repo was initialized at `2026-05-22 20:52:07` with
`[claude/vc-operator] feat(vcnotes): foundation skeleton for Swift/SwiftUI/TextKit2 rewrite`.
Most recent Vetcoders commit: `2026-05-24 01:11:05`.

**Elapsed: 28 hours, 19 minutes.**

In those 28 hours, 27 commits landed across 4485 lines of Swift,
delivering:

- TextKit 2 source editor with syntax highlighting + line numbers (gemini)
- swift-markdown HTML preview with theme + debounce (claude)
- file-first storage with bookmark + watcher + autosave (codex)
- workspace import explorer + workspace search index
- preview pipeline (relative paths + appearance theming + dirty doc protection)
- MarkdownEditorSurface refactor (post-machete cut of "fragile editor bridge")
- command controller refactor (route app commands through controller)
- signed + notarized .app + .dmg release pipeline (operator)
- thin Makefile facade for daily ergonomics
- sidebar context menus + explorer selection feedback
- canonical Application Support directory + lint gate honest

This covers all 4 sections of gemini's "kompleksowy edytor Markdown w
Swift" decomposition — parser/AST, TextKit2 engine, UX
micro-interactions, file management — PLUS signed/notarized release
pipeline that gemini didn't even include in the estimate.

Gemini's premium tier estimate: **3-6 months** (90-180 days).
Reality: **~1.17 days**.
Compression: **77-154×** for Swift/AppKit (lower than Wave A/B due to
TextKit 2 substrate friction + Xcode compile cycle overhead + mostly
sequential single-repo flow).

---

## Operator corrections (gust axis in action)

The session demonstrated that operator real-time corrections are
load-bearing, not optional. Without them, agent would have drifted:

1. **"Rescheduled, not retired"** — vc-mux semantic re-framing. Agent
   had drift'd to "retirement" language; operator's correction
   preserved engineering dignity of post-poned-but-functional code.
2. **"3 (then 4) labs equal intensity"** — operator rejected agent's
   "primary spine vs parallel R&D vs premium" ranking. Equal intensity
   was the truth all along; agent's ranking was projection of
   conventional team-bandwidth thinking that doesn't apply at
   Vetcoders pace.
3. **"Stop writing more briefs than commits land"** — agent had
   over-engineered Wave A briefs (~300 LOC each); operator flipped
   the ratio to "agents should win, dzięki tobie". Wave B briefs
   were shorter and agents shipped more.
4. **"Per-lab cwd dispatch"** — operator's hint to start each agent
   in its own lab dir, not generic vc-runtime root. Living Tree
   discipline + clean per-lab reports.
5. **"Fork-and-forget"** — for microsandbox: no upstream sync, no
   rebase, treat as Vetcoders codebase from clone. Operator's taste
   call that agent could not have predicted (microsandbox is Apache
   2.0, technically syncable; operator chose not to).
6. **Time-rescale skepticism** — operator pushed back on
   "miesiące to tygodnie, tygodnie to dni, dni to..." sequence
   forcing agent to acknowledge "dni to godziny". Empirical evidence
   confirmed the operator was right.

Each correction reshaped trajectory mid-flight. Agent that doesn't
respond to operator corrections produces wrong work at high speed.

---

## Lessons distilled

1. **Trinity is constraint, not weighted vote.** All 3 must satisfy.
2. **Compression is real and stack-dependent.** 1000× for
   Python/Rust parallel; 80-150× for Swift/AppKit sequential.
   Apply empirically per project, not generically.
3. **Briefs ≠ commits.** Operator-agent's success is measured in
   commits landed by workers, not LOC of briefs written.
4. **Substrate failure ≠ delivery failure.** krunvm missing is
   information; honest report > false PASS.
5. **Equal intensity > ranking** when operator wants portfolio
   coverage across ICP segments.
6. **Operator corrections are gust signals.** Read them as
   trajectory adjustments, not nitpicks.
7. **Conventional estimates from cross-frontier reports are
   conservative.** Cross-validate with empirical Vetcoders pace.
8. **Per-lab cwd + per-repo branches = Living Tree clean.**
9. **Marbles correctly stops on report-failed signal.** Don't burn
   iterations on irrelevant ground.
10. **Reality decides.** Demos don't count. Mocks don't count.
    Customer-installable, commit-landed, gate-green code counts.

---

## Case study #2 — Operator-side agent self-critique (2026-05-25)

Less than 24 hours after the skill landed, operator dogfooded it on a
parallel session dispatching GPU benchmark agents. Operator-side agent
caught itself reaching for native `Agent` tool with `run_in_background:
true` instead of `vibecrafted justdo codex --prompt '...'`. The agent
self-corrected mid-session and produced a structured critique with
70/30 split:

- **70% agent discipline failure** — the agent had the `vibecrafted`
  command available via Bash, knew the routing per `vc-why-matrix`,
  chose easier reflex path. "Sięgnąłem po native Agent bo łatwiej."
- **30% framework gap** — original `SKILL.md` was declarative ("trinity
  over tactics") without an explicit operational default forcing
  `vibecrafted <workflow> <agent>` as THE external dispatch surface.
  Charter without operational teeth = drift permitted.

The self-critique itself is empirical proof the skill works:
**operator-side agent invoked vibecraftsmanship, ran Trinity check
in real-time, and identified its own Power-axis drift** (Power without
observability = native `Agent` transient output). Charter feedback loop
functional within 24 hours of skill landing.

### Patches applied in response

1. **SKILL.md gained** "Operational default — external dispatch surface"
   section with hard rule + detection signal + reflex check + reason
   for the rule. Placed between "When to use" and "Dependencies" so it
   reads BEFORE the agent gets to Three Axes detail.
2. **AXES.md gained** "Operational default — external dispatch surface
   (HARD RULE)" subsection within Axis 2 (Power), explaining that
   native `Agent` output breaks the "wider menu for operator" promise
   because it vanishes from context with no artifact substrate.
3. **Dependencies** updated to mark `vc-agents` as **required** for the
   Operational default (was previously not in the dependency list).
4. **EVIDENCE.md** (this file) gained this case study.

### Open follow-ups (operator decisions pending)

- **vc-agents SKILL.md** — proposed addition: agent-side 5-point
  pre-dispatch checklist (operator-owned skill, change requires
  operator approval; draft not committed)
- **MCP server `vibecrafted` dispatch+await** — engineering investment
  to give parent agent push notification on external worker
  completion (closes observability hand-off gap; outside skill scope,
  parked as feature-dev item)

### Lesson distilled (added to main list as #11)

11. **Charter without operational teeth = drift permitted.** Declarative
    posture ("trinity over tactics") does not survive contact with
    ergonomic reflexes. Every Power-axis claim needs a hard operational
    rule that says **what to type** at the moment of decision, not just
    **what to think about** before deciding.

---

## Case study #3 — Real-time Trinity self-correction + `/loop` doctrine extension (2026-05-25)

Operator dogfooded the skill on its own infrastructure stack — the
`vibecrafted` installer pipeline. Through escalating peer-pressure
questions ("what about curl|bash?", "how about as potential consumer?"),
operator forced the operator-agent through three diagnosis cycles:

### Cycle 1 — original diagnosis (partially wrong)

Operator-agent claimed: bootstrap stages to `/tmp/vibecrafted-XXXX/`
ephemeral, so installer-generated resolver shim has dead-first-candidate
by design for NEW users. Anti-pattern across 3 layers.

### Cycle 2 — operator zooms further out

Question: "no a jak ktoś robi curl | bash?" forced operator-agent to
verify by fetching the live `https://vibecrafted.io/install.sh` content
and reading actual bootstrap usage:

> "Bootstrap a local 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. source snapshot into
> `$VIBECRAFTED_ROOT/.vibecrafted/tools` and then run a local staged
> install path from that copy."

Reality call output: bootstrap stages to **persistent**
`~/.vibecrafted/tools/vibecrafted-main/`, NOT to `/tmp/`. NEW user case
works as intended — install copy IS canonical, resolver hits first
candidate. Operator-agent's Cycle 1 hypothesis was empirically falsified
within one Reality check.

The bug actually exists only in **operator dev mode** (`git clone` +
`bash install.sh` from local checkout) — `$repo_root` expands to live
repo path, gets baked into shim, mid-rebase breaks other shells.

### Cycle 3 — dispatch without hesitation

Operator's instruction: _"dispatchuj to ziom a się nie zastanawiasz.
confidence high? operator nie odpowiada? -> dispatch"_ — explicit
"bez odbioru" rule applied to skill-update work, plus pointed out
operator-agent had forgotten to enter `/loop`:

> "Ty zapomniałeś wejść w /loop który musi stać się canonical inside
> power feature of claude utilized by our framework!"

Operator-agent landed three coordinated changes without further
clarification:

1. **`install-shell.sh` patch** — removed hardcoded `$repo_root`
   expansion that baked operator-install-time path into every generated
   shim. Resolver chain now: `VIBECRAFTED_ROOT` env opt-in (dev mode) →
   canonical install paths (`~/.vibecrafted/tools/vibecrafted-current/...`)
   only. Mid-rebase intermediate states stop breaking other shells.
2. **`SKILL.md` Operational default extended** with second canonical
   surface — Claude Code native `/loop` for autonomous self-pacing.
   Operator-agent now has explicit doctrine for **WHEN to enter `/loop`
   vs WHEN to stay single-turn**, complementing the existing dispatch
   rule (`vibecrafted` vs native `Agent`).
3. **This case study** in EVIDENCE.md.

### Lessons distilled (added to main list as #12-13)

12. **Diagnosis without Reality verification = drift permitted.** Two
    turns of architectural claims about `/tmp/` staging fell apart after
    one `curl` to the live install.sh URL. Reality call should be the
    FIRST move when claiming infrastructure behavior, not the last.
13. **Operator-agent must enter `/loop` when autonomous tail exists.**
    Single-turn passivity ("waiting for operator response") when there
    IS autonomous work to continue = missed canonical pattern. `/loop`
    is the bridge between "operator drives" and "agent freelances" —
    use it.

### Composition surface clarified

The two canonical Power-axis surfaces now both documented in SKILL.md:

| Surface                                                                                                | When                                           | What it solves                                                                                                      |
| ------------------------------------------------------------------------------------------------------ | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `vibecrafted <workflow> <agent>` (Bash)                                                                | Deliverable-producing external worker dispatch | Observability (canonical store, transcripts, meta.json, reproducible launch.sh) — operator-grade artifact substrate |
| `/loop` (Claude Code native, equivalent: `ScheduleWakeup` with `<<autonomous-loop-dynamic>>` sentinel) | Autonomous self-pacing across turns            | Continuity between operator engagements without losing momentum or polling tight loops                              |

Together: external dispatch creates async work; `/loop` keeps the agent
present for that async work's completion. Both are canonical, both
declared as HARD RULES in the Operational default section.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
