# Fleet Dispatch Runbook — for an operator `claude` in ultra mode

> How to dispatch the Vetcoders fleet effectively. Hand this to any operator agent.

## 0. The ultra-mode trap (read first)

Ultra / ultracode mode nudges you toward the native **`Workflow` tool** (the `vc-delegate`
native-subagent pattern). For **fleet** work that is the WRONG reflex. The doctrine: dispatch the
REAL external fleet via **`vc-agents`** (`vibecrafted <workflow> <agent>`), not the native Workflow.
Native Workflow / `vc-delegate` is for _in-process bounded_ cuts only. When the operator says
"fleet", "dispatch", or `/vc-agents` — launch external agents through the framework launcher.

If you catch yourself reaching for `Workflow({...})` to run codex/claude/gemini: STOP. Use the launcher.

> **The harness will say it out loud.** In ultracode the runtime injects _"Ultracode is on — use the
> Workflow tool on every substantive task."_ That directive governs **in-process** work and does NOT
> override this doctrine for **fleet** dispatch. The native `Workflow` tool spawns in-thread subagents
> only — it physically cannot launch a real external agent (codex/claude/gemini) as its own process,
> with its own context, branch, commits, and report artifacts. Fleet = `vibecrafted` launcher (Bash,
> degrades to headless in-repo); native `Workflow` = bounded in-process analysis/cuts. Operator
> doctrine > harness default.

## 1. Always start with `/vc-scaffold` (the commandment)

**No fleet dispatch without a scaffold.** `/vc-scaffold` is the WRITE entry of the read/write cadence:
it produces a numbered **master plan** (wave atlas + dependency graph + a `state` column) and a
**12-section brief for EVERY cut** (hard-gate: a cut without a brief does not exist).

- **Reality-check FIRST** (Orient gate): dogfood loct — `loct context --scope 'path:<dir>' --markdown`
  (slim; `--full` is too big for routine), `loct find --literal`, `loct slice/impact`. Map what
  ACTUALLY landed before you plan or claim "done". Overclaiming "all waves done" is a fireable sin.

## 2. Numbering (sequential LP — never reuse)

- **Master / Atlas** = `#N` (e.g. `#12`, `#24`), strictly consecutive.
- **Cuts / waves** = `<N>-<Wave><slot>` (e.g. `12-A1`, `24.B-1`) or the operator's `<N><wave>-<slot>`
  shorthand (e.g. `12d-e`). The wave letter groups parallelizable cuts; the slot is the cut index.
- Every cut row carries: a **Vector** (stabilize/implement/recon/e2e), the four-term delta
  `intent | baseline | claim | delivery`, a `state` marker `[ ] [~] [?] [!] [x]`, and a
  **delivery-verifier**. Only the verifier flips `[~]→[x]`; a claim never reaches `[x]` alone.

## 3. Dispatch (the proven shape)

```bash
vibecrafted <workflow> <agent> --file briefs/<N>-<cut>_<slug>.md   # e.g. vibecrafted implement codex --file briefs/24-A1_mcp.md
vibecrafted <workflow> <agent> --prompt '<inline intent>'
```

- `agent` ∈ {codex, claude, agy, junie, grok, cursor}. Pick via the **why-matrix** (gemini deprecated; agy for Google-family)
  **codex = precision/surgery** (contract-gated refactors, exact edits), **claude = forensics/audit**
  (deep unknowns, bug hunts), **agy = Google-family (antigravity rewire; gemini deprecated)** (architecture leaps, simplification).
- Headless (non-TTY agent bash) **degrades to in-repo dispatch automatically**. A launch receipt
  prints `run_id` + report/transcript/meta paths — capture them.
- **Disjoint file-domains → safe parallel dispatch** (Living Tree). Overlapping domains → sequence them.

## 4. Commit doctrine (per dispatched agent — encode it in the brief)

- **ONE commit per round** (marbles: one round = one commit), local, on the current branch,
  well-formed per the commit-msg hook. **Never leave delivered work uncommitted.**
- **Multi-commit per dispatch** is expected when a mission spans rounds; a `vc-workflow` run produces
  **up to 3 commits** (Implement / Marbles / Polarize each commit their round).
- **NO push / merge / PR / deploy** — that is the operator's button only. No `--no-verify`, ever.

## 5. Observe (metadata-first, not pane-first)

- Ordinary fleet workers are detached and headless by default. vc-frame is an
  optional projection of state and transcripts, never the process owner. A true
  PTY is reserved for the User Session, a bare resume, or an explicit
  `--runtime terminal` for a proven TTY-required path.
- After dispatch, arm `vibecrafted await <agent> --run-id <id>` immediately,
  supervisor-side. Control-plane JSON, report files, transcripts, viewer panes, and
  scheduled wakeups are diagnostic only, not wake signals. Hedging await with
  ad-hoc pollers/watchers is a Class 3 violation; fix `control_plane.await_run`,
  do not normalize the hedge. See `docs/runtime/AGENT_OPS.md`.
- Liveness is always 3-signal: await verdict, terminal-state run meta, worker pid
  dead, plus promised report presence. Two agreeing signals are enough to act;
  three are required to declare done. Any disagreement means treat as live and
  re-arm await. Known skew: rc=0-on-live and meta stuck `active`/`stalled` after
  real completion.
- Durable artifacts (`*.meta.json`, `*.transcript.log`, report paths) are
  diagnostic drilldowns after await is armed. The fleet stays operable from
  artifacts even with no panes open, but artifact polling does not replace the
  canonical await.
- **Verify each cut** against its brief acceptance + run its gates (tests / clippy -D warnings /
  `make check`). Confirm the agent **committed its round**; if it left work uncommitted, flag the
  doctrine regression. **STOP is recovery, not surrender** — on stall/fail, issue a focused
  recovery-dispatch; do not 502-and-die.

## 6. Dogfood the tools (non-negotiable — it's also the product)

- **Loctree first** for structure + repo-literal: `loct context`, `loct find --literal`,
  `loct occurrences`, `loct body`, `loct slice`/`impact`. rg/grep = fallback / local magnifier only.
- **AICX** for intent + session memory: `aicx intents -p <project>`, `aicx search`.
- If loct is wrong/stale/awkward/misses a surface → append (never overwrite) a dated line to
  `~/.vibecrafted/loctree/loctree-fail.md`.

## 7. Tend the loop

After dispatch: **observe → verify → next wave**. Do NOT drift onto side-quests and abandon the
loop. Report honestly: `state`, SHAs, what is `[x]` vs `[?]` — nothing faked. Stop at the operator's
button.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. — Loctree gives sight · AICX gives insight · Vibecrafted gives hands._
