# Changelog

All notable changes to 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

## 4.2.4 — 2026-08-22

### Changed

- Public commands and every generated follow-up command use action-first,
  agent-last grammar. The removed `vibecrafted <agent> <action>` form is rejected
  with an exact migration command instead of remaining as compatibility syntax.

### Fixed

- `vibecrafted resume <agent> --run-id ...` can recover a run settled as
  `stalled`, preserving the recorded provider session and run lineage in the
  newly tracked continuation.
- LIVE viewer scripts, resume receipts, and rejection hints no longer teach the
  removed agent-first grammar.
- The interactive `vc-resume --help` contract test now provisions its own zsh
  startup, checkout launcher, HOME, and control plane instead of depending on a
  preinstalled host runtime.
- The Polish `vc-workflow` doctrine mirror includes the canonical await and
  liveness contract and uses action-first commands.

## 4.2.1 — 2026-08-21

### Fixed

- macOS release builds now compile `vc-frame` reliably with Rust 1.95, pin its
  release provenance to a stable source identity, and strip linker object paths
  before payload hygiene and signing.
- The full source gate no longer depends on the wall clock or on provider
  executables installed on the hosted runner.

## 4.2.0 — 2026-08-21

> **4.2.0 scope — measured truths, finished seams.** Release integrity from the
> donor snapshot through to the payload a stranger downloads, and one identity
> order shared by every surface that reads a run.

### Added

- `vibecrafted init` carries unfinished work into every session. Each pass
  projects this checkout's needs-attention settlement bucket, classifies every
  run, and prints the exact command that continues it — newest first, with the
  truncated remainder counted out loud. Silent on a clean checkout; an
  unreadable ledger degrades to an honest `UNKNOWN` rather than bricking init.
  Guardian-owned runs are listed without a command, because each holds a single
  automatic attempt that a hand resume would burn.
- `--snapshot-donors` on `scripts/build-vibecrafted-release.sh` builds a release
  from a detached worktree at each donor's `HEAD`, so a dirty donor no longer
  blocks a cut and never leaves a ghost worktree registration behind. A reaper
  folded into the release cleanup trap removes and prunes them.
- `--silence-timeout` on the dispatcher, and a supervisor bound on worker
  **stdout silence** rather than wall-clock time. A worker that is slow but
  talking is untouched; one blocked in `wait4` now settles through the existing
  stall handler, which records `stall_kind=silence|wall_clock` so triage never
  has to guess which bound fired.
- `make payload-hygiene` refuses any release payload that names the build host,
  reporting the topmost still-host-specific ancestor instead of only the exact
  checkout string.

### Changed

- Public CLI guidance now uses one canonical grammar: action first, agent last
  (`vibecrafted init codex`, `vibecrafted implement codex`,
  `vibecrafted stop codex`). Agent-first mode calls remain accepted only as a
  compatibility surface and are labelled as such instead of being taught to
  first-time users.
- The standalone brand spelling is `Vetcoders`; repository and URL identity
  remains lowercase `vetcoders/<repo>`.
- Bare `vibecrafted resume <agent>` (and `--root`) opens a new interactive
  session plus an AICX continuity pack. It no longer native-attaches the last
  same-agent AICX candidate. Native provider resume requires `--session <id>`.
- Resume pack assembler lives in `aicx_session_chain` (CLI transport + MCP
  session-chain contract). Catalog rows are evidence, not a Tinder picker.
  Empty-with-`-p` is `empty_project`, not a silent scanned=0.
- `vibecrafted doctor` judges an installed owner by **where it lands**, not by
  what its path is called. Ownership is containment in
  `$VIBECRAFTED_RUNTIME_HOME`, resolved directly or through a wrapper's `exec`
  target. A new cross-check compares the reported install identity against the
  `VERSION` of the root the launcher actually enters, and warns — naming both —
  when they disagree, instead of reporting `ok` for a generation nothing runs.
- `workspace_id` mints UUIDv7 and accepts **any** canonical UUID. The wire
  contract previously read as a validation rule; a reader that enforced it would
  have dropped this repository's own v4 workspace entry from its dashboard.
  Chronology comes from `created_at`, never from the id bits.
- The vc-frame default server is the canonical product origin `127.0.0.1:3024`
  instead of a tailnet address compiled into the shipped binary. `VC_SERVER_URL`
  and `--server` still route to any remote; that is now an operator choice
  rather than a build-time one.
- `vibecrafted_core` imports siblings by module path instead of through the
  package barrel. Runtime behaviour and lazy exports are unchanged; the importer
  graph now names the module that owns each symbol — measured 0 breaking /
  0 structural / 0 diamond cycles, repository health 74 to 80.
- Non-destructive branch push is a duty, not a stop. Force, trunk, delete, and
  tag pushes remain the only git-push hard stops, and Mode B worktree workers
  stay off remotes entirely.

### Fixed

- The direct Python lifecycle test now owns an isolated `VIBECRAFTED_HOME` and
  clears inherited workspace/session identity before writing metadata. Test
  runs can no longer register `test_write_meta*` sessions in the operator's
  live workspace rail.
- The repository `loop` launcher now derives `PYTHONPATH` from its actual
  package root. It no longer depends on an ambient installed core when invoked
  from a different working directory.
- Product entry resolves workspace identity and server ownership through the
  core shipped beside the active deck, never through an older `vibecrafted`
  found earlier on `PATH`; vc-frame session attachment follows the same owner.
- The delivery proof kernel could not run its own verification subject. The
  executor scrubs the environment to `_SAFE_ENV_KEYS` — correctly dropping
  `PYTHONPATH` — while the subject was declared as a `-m` module invocation
  resolved through the `sys.path` that scrub had just removed. It died with
  `ModuleNotFoundError`, the kernel wrote `proof.failed`, and **every run it
  judged settled `failed` regardless of the worker's real outcome.** The package
  location is now a contract-declared argument instead of ambient state.
- The supervisor heartbeat pulsed identically at 20 seconds and at 3 hours, so a
  worker blocked in `wait4` held the supervisor open forever and the finished
  `RunState.STALLED` handler was unreachable in production.
- The live dashboard resolved its workspace identity from the repository root
  alone, while the runtime stamps runs from the exported
  `VIBECRAFTED_WORKSPACE_ID` first. Two implementations of one question, free to
  disagree — and a dispatched worker in a worktree, whose root can never equal
  its dispatcher's, was structurally invisible to the LIVE RUNS filter.
- `docs/install.sh` exec'd `../install.sh` directly, but that file carries no
  executable bit by design, so the shim died with exit 126 on every fresh clone.
  It now execs `bash` explicitly, matching the packer contract.
- Chained keychain traps under `set -e`: `_ks_trap_cleanup` returns the
  triggering status on purpose, and that non-zero return tore the shell down
  before the caller's chained handler ran. Measured on a real failed release
  that skipped its own reaper and left two worktree registrations behind.
- Four gates that guarded something real while being structurally unable to see
  it break are now capable of failing — including the keychain regression suite,
  which ran every child without `set -e`, the exact condition its target bug
  requires.

### Known gaps

- Workspace Cut B remains deferred: workspace selection, workspace-scoped
  F/X/N projections, and build-isolated vc-frame runtime are not part of 4.2.0.
- Runtime generations integrity-pin the complete shipped tree, and each skill
  may declare its own semantic version, but 4.2.0 does not yet publish a
  queryable framework → launcher → skill id/version/checksum compatibility
  manifest. That contract belongs in the packer, install receipt, and doctor as
  one system rather than as release-note-only metadata.

### Security

- Signed artifacts no longer carry the operator's disk layout. `runtime_receipt`
  dropped two hardcoded build-host absolutes (duplicates where they resolved,
  dead entries shipping a private path everywhere else), Rust test-module
  fixtures that the packer's `tests/` directory exclusion could not see were
  neutralised, and the payload-hygiene gate now fails the build rather than the
  reviewer. Measured on the 4.1.0 portable tarball: 5 offenders naming the
  checkout, 12 naming the workshop one level above it.
- `install.ps1` is guarded byte-for-byte against the site repository's served
  copy, so the two cannot drift apart unnoticed.
- `aicx` runs with a sanitized `PATH` (absolute, non-empty entries, system
  fallback), so an implicit-cwd lookup can never pick up a stray binary.

## 4.1.0 — 2026-08-16

> **One app, one real terminal.** `Vibecrafted.app` owns the macOS product
> identity and lifecycle without flashing its unfinished control-centre window;
> the signed nested `vc-terminal.app` is the first visible workspace and starts
> the bundled, generation-bound `vc-start operator` flow.

### Added

- Tray-first macOS carrier (`LSUIElement`) with explicit `Open Console`,
  `Open vc-terminal`, and `Quit Vibecrafted` actions; the native console remains
  available on demand but never flashes during ordinary startup.
- Signed nested `vc-terminal.app` and primary-shell receipt bind the visible
  terminal to the exact runtime generation carried by the DMG.
- `doctor --fix-server-service` repairs stale macOS server LaunchAgent state
  through the same public CLI surface that diagnoses it.

### Changed

- Composer and Quick cmd remain clickable label-only actions in the top chrome;
  their `⌘E` and `⇧⌘.` teaching lane is permanently rendered in the
  bottom status bar across vc-frame modes.
- The terminal theme control keeps one trailing column away from the borderless
  window edge, and light mode changes both host and active vc-frame palettes.
- Release identity moves coherently from 4.0.1 to 4.1.0 across Python packages,
  Rust manifests, Cargo locks, runtime VERSION files, and the app bundle.

### Fixed

- The app no longer creates a native Mission Control window and then covers it
  with vc-terminal at launch.
- Runtime launch uses the app's embedded generation instead of an ambient PATH
  lookup, preserving install and provenance truth.
- Release signing uses a temporary keychain session, signs the nested helper
  closure, and validates the immutable annotated-tag source contract.
- The Ghostty/terminal palette and bundled vc-frame assets preserve readable
  light-mode foregrounds instead of inheriting dark-mode text colors.
- Product entry reconciles the one configured macOS server LaunchAgent when it
  is unhealthy, so Live Runs reads the configured control-plane donor instead
  of waiting forever while no server owns the port.
- The DMG builder derives missing Python launchers from the package's canonical
  `[project.scripts]` manifest. Public commands such as `vc-git` can no longer
  be green in source yet absent from the installed app.
- Mounted-DMG verification canonicalizes equivalent `/tmp` and `/private/tmp`
  paths while still requiring the exact signed app and runtime generation.
- AICX fallback resume uses the exact cross-organization `-p /repo` filter,
  retaining history through organization renames without ambiguous bare-name
  failures or an unsafe fallback that mixes unrelated projects into the pack.

### Security

- Semgrep, unified-product, package, installer, and mounted-artifact gates remain
  fail closed; signing/notary credentials stay operator-owned under
  `$HOME/.keys` and are never embedded in the product.

## 3.7.1 — 2026-08-14

> **One Vibecrafted.** The first release whose installable boundary is exactly
> one signed and notarized `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg`.
>
> Derived from `git log` after the 3.7.1 bump (`ef700e52` … `545aa1d2`).
> `v3.7.1` is not tagged yet; latest published GitHub Release is still
> `v3.5.0`.

### Added

- `Vibecrafted.app` now carries the exact matching `vc-terminal`, `vc-frame`
  and complete runtime, with signed source/module receipts.
- Durable `workspace_id` and automatic bundled `vc-start` entry for every new
  or restored workspace.
- Public `vc-git` operator command exposes branch, dirt, recent commits and
  every active worktree without requiring an MCP client.
- App-owned XDG/runtime environment: the product does not overwrite user
  terminal, shell or vc-frame configuration.
- Fail-closed macOS publication target that downloads draft assets back from
  GitHub, byte-compares them, verifies signed release-output, mounts the DMG,
  runs the walk-around probes and only then publishes.
- Hermetic in-app runtime layout: the desktop product carries its own
  generation instead of projecting a checkout `runtime/` symlink
  (`45f29fbf`).
- Honest install channel matrix: macOS/Linux bootstrap today; signed DMG
  when a release actually carries one; Windows is WSL2 plus that same
  bootstrap (`fb341fb7`).

### Changed

- `vibecrafted` is the sole owner of app/DMG/install/update.
- Quick cmd uses `Shift+Cmd+.` and the top bar advertises both entry chords:
  `✍ Composer ⌘E` and `❯_ Quick cmd ⇧⌘.`.
- `vc-terminal` is a deterministic binary donor; its standalone App/DMG/MSI,
  signing, notarization and install surfaces were retired.
- `vc-frame` is the session interior; its standalone installer, release assets,
  packaging workflow and update channel were retired.
- Tag CI is read-only source validation. Apple signing/notarization and release
  publication remain on the explicit macOS operator boundary.
- First-use copy on `vc-start` and `doctor` names what is missing instead of
  scaring a plain install (`89b9c42b`, `4ffcf789`, `14579808`).
- Windows refuse path now talks about WSL2 in v3 language, not a leftover
  v1.x claim (`545aa1d2`).

### Fixed

- Docker image seeds skills from the canonical package and keeps executable
  modes on staged scripts (`f6d70400`, `2162a71e`).
- Docs launcher is shellcheck-clean (`be37f10b`).
- Installer preserves verified archive modes (`680a3261`).
- Guardian bounds retry reasons by UTF-8 bytes and bounds terminal-triage
  reconciliation (`b58c8d8a`, `16f5b685`).
- App binds the canonical icon and failure dates; Mission Control date
  formatters are reused (`6d4c0377`, `4162aaf6`).
- CodeQL publication gate is actually executable (`bb0ca1a0`).

### Security

- Tailscale auth keys are injected only into the launch process and are never
  stored by the onboarding wizard or generated `.env` files.
- Mermaid SVG links use a positive URL-scheme allowlist.
- All GitHub workflows declare least-privilege permissions.

## 3.7.0 — 2026-07-27

> **Runtime truth recovery.** The release where a killed supervisor, a stale
> lock, a torn journal, or a lying "healthy" receipt can no longer fake the
> board. 114 commits across 202 files, every recovery path proof-gated.

### Highlights

- **Durable settlement V2** — immutable, append-only, hash-chained
  `SettlementEventV2` ledger (`settlement_ledger` / `settlement_history`),
  published as durable revisions and read back by triage, guardian, and the
  control plane. Zero on the f/x/n rail is now a verdict, not a default.
- **Read-only settlements CLI** — `vibecrafted settlements`
  (summary / list / inspect / revalidatable) over the canonical ledger,
  schema `vibecrafted.settlements-query.v1`.
- **Untriaged-run sweep** — `run_triage --sweep-control-plane` retro-triages
  every finished run the dispatcher never came back for (the 418-runs-of-
  silence class), idempotent and bounded, with rotating candidates.
- **Guardian recovery** — event-driven recovery guardian with server-owned
  sidecar lifecycle, exact argv identity, trust-outbox recovery before
  attach, reconciled recovery lineage, and race-closed native resume.
- **Supervised service truth** — canonical pair health proof before any
  healthy receipt; foreign live PIDs and minimal identity JSON are rejected;
  doctor fails closed on dead supervision; malformed service plists are
  contained; stale lifecycle locks recover after SIGKILL.
- **Trust journal crash-safety** — torn/partial JSONL tails after a
  prepared-outbox crash are repaired to the exact prefix; newline crash
  recovery gaps closed; post-hoc commit verdicts.
- **Scaffold control room** — plan library + picker with a deterministic
  machine-checked `scaffold-doctor` gate (R1–R11), bounded catalog
  discovery, and health checks that stay cheap and isolated.
- **Install/runtime handoff** — crash-atomic tool handoff, supervision
  enabled only after payload verification, terminal failure truth preserved,
  and dirfd-anchored restore publication with digest checks.
- **Test-suite hermeticity vs live operator runtime** — TUI/core suites and
  the portable gate now strip ambient frame/session env (`ZELLIJ*`,
  `VC_FRAME*`, prepared-session vars, `PYTHONPATH`), so green in CI can no
  longer redden on an operator machine with a live cockpit.

## 3.6.0 — 2026-07-24

> **Ship AI-built software without the vibe hangover.**
>
> 3.6.0 is the release where the board stops lying, the cockpit stays honest,
> the installer survives a hostile filesystem, and a stranger can still install
> with one command. Not a list of commits — a product story with gates behind it.

### The story

For months Mission Control could show activity while the settlement strip said
`0 · 0 · 0`. Operators learned to distrust the board. Agents finished work,
reports claimed success, and the retained control plane still had no canonical
f/x/n truth to hand to the next session.

3.6.0 closes that gap. **Retained control-plane settlement is the single run-level
source of truth for f/x/n.** Mission Control, vc-server API, and SSR read the same
board. vc-frame's rail still reports **tab inventory**, not fleet settlement —
two surfaces, two jobs, no false equivalence.

Around that board sits a **delivery-proof kernel**
(`vibecrafted_core.delivery.*`): execution envelopes, proof contracts, reconstructable
seals. Dispatch no longer treats a completion label as proof. A run is final when the
envelope, the proof, and the seal agree — or it is not final.

The operator cockpit is **vc-frame 0.46**. Every dispatch layout (dashboard, marbles,
operator, research, workflow) welds the session-manager rail into the default tab
template so a reinstall cannot strip the sidebar the operator hand-welded last week.
Layouts passed parser dump and live `new-tab` load probes before this line shipped.

The **installer container lane** earned its scars on a real Vetcoders container
mount: sshfs that dropped executable bits, broke symlinks, and corrupted bytecode
on copy. The lane is resilient under those conditions — stage, verify, refuse to
pretend a half-copied tree is an install. That is not marketing; it is wartime
filesystem engineering.

**ACP adapter (MVP + P2)** is the gate to IDE-shaped clients. Thin glue over the
existing control plane and workflow APIs — launch, observe, await, stream, stop —
without inventing a parallel runtime truth. IDE integration starts from evidence the
CLI already trusts.

**Prune / health** moved the structural health readout from the low-70s into the
low-80s range on the release candidate path (78 → 81 on the measured prune pass).
Not "perfect architecture" — a cleaner cone for the next cut.

### What landed (facts, not hype)

| Surface                  | Claim                                                        | Evidence bar                            |
| ------------------------ | ------------------------------------------------------------ | --------------------------------------- |
| Settlement board f/x/n   | Canonical retained control-plane counts                      | Python core suite; API/SSR parity tests |
| Delivery-proof kernel    | Envelopes + proof + seal layout                              | Delivery unit + e2e tests               |
| vc-frame 0.46 cockpit    | Rail on every default layout; honest inventory vs settlement | Layout dump + live new-tab probes       |
| Installer container lane | Survives symlink / +x / bytecode damage on hostile mounts    | Installer + delivery path tests         |
| ACP adapter MVP+P2       | stdio ACP over control_plane                                 | `tests/acp/`                            |
| Research agent pick      | Fail-closed arity + announced source                         | Research launcher suite (16)            |
| Gemini lane              | Hard-removed; **agy** is the Google successor                | Dispatch/marbles/research rotation      |
| Version truth            | Framework stamps **3.6.0**                                   | `VERSION`, package metadata             |

### Gates at merge of PR #20 (release candidate head)

- Full Python core suite: **910 passed**, 8 skipped
- Research launcher suite: **16 passed**
- Rust workspace: `cargo check --all-targets --all-features`, clippy `-D warnings`, 47 tests + 1 doctest
- All five vc-frame layouts: parser dump + live `new-tab` load
- Local quality stack: Semgrep, Ruff, mypy, ShellCheck, Prettier, diff-check, pre-commit, pre-push

### Install (stranger path)

```bash
curl -fsSL https://vibecrafted.io/install.sh | bash
vibecrafted doctor
```

Foundations are **prebuilt-first** (npm / curl release assets / crates.io / PyPI),
then package-manager, then `cargo build` only as a last fallback with preflight.
See [docs/FOUNDATION.md](docs/FOUNDATION.md).

### Compatibility notes

- Canonical delivery artifact filenames replace non-canonical seal defaults;
  `vc-ship` consumes the same layout owner as reconstruction.
- `.gemini` remains discoverable only as a legacy data-directory compatibility
  view; active research/sync targets use **agy**.
- This changelog entry does not itself create a git tag. Tag and public deploy
  follow the published-artifact smoke path on vibecrafted.io.

---

## 2.0.0 — 2026-05-20

> Quality-layer reform. The pipeline epistemic rhythm
> (READ-ONLY perception ↔ WRITE action) is now first-class in the
> manifest and across every skill. `vc-audit` is added as a new
> READ-ONLY falsification step. `vc-marbles` is reframed from
> "truth convergence" to "deliberate over-write whose excess is the
> point of polarize stripping it back". All edited skills land under
> the 12k marketplace cap with companion files for detail.

### vc-operator 2.0.0 reform (2026-05-21)

- **Operator mode shipped as a coherent reform batch**: `skills/vc-operator/RUNNER.md`,
  `skills/vc-operator/WHY_MATRIX_TABLE.md`,
  `skills/vc-operator/DISPATCH_TEMPLATE.md`,
  `skills/vc-operator/FEEDBACK_2026-05-20_claude.md`, and
  `skills/vc-operator/FEEDBACK_2026-05-20_claude_runtime.md` are the
  durable operator references instead of transcript-only doctrine.
- **Wave 5 await/watch rail landed**: `runtime/scripts/vibecrafted-await-watch.sh`
  plus the `spawn_await_watch_pane` hook in
  `runtime/scripts/lib/vc_frame.sh` give long-running dispatches a
  visible watch surface.
- **`skills/vc-operator/SKILL.md` patched from 0.1.0 to 2.0.0** with the
  runner contract, why-matrix dispatch discipline, feedback intake, and
  operator-facing closure rails.
- **Sharp-move recommendations absorbed**: REC-1/2/3/4/6/7/8/9/10/11 are
  now represented across the operator contract, modes table mandate, dispatch
  template, runner loop, and feedback files.
- **17 live runtime pains recorded** in
  `skills/vc-operator/FEEDBACK_2026-05-20_claude.md` and
  `skills/vc-operator/FEEDBACK_2026-05-20_claude_runtime.md`, including the
  REC-10 modes-table requirement and dispatcher/runtime pain catalog that made
  the reform necessary.

### Added

- **`docs/runtime/MANIFESTO_PL.md` + `MANIFESTO_EN.md`**: new
  **Pipeline (Sculpting Pattern)** section with the explicit
  WRITE → READ → WRITE → READ rhythm diagram and the **carve-from-
  marble** pattern at the centre of the quality cycle. Tooling
  ontology table grows a **Mode** column (READ / READ-ONLY / WRITE /
  meta / infra) and splits the **Quality** layer into perception
  (`vc-followup` + `vc-review`, READ) and falsification (`vc-audit`,
  READ-ONLY).
- **`skills/vc-audit/`** (NEW): READ-ONLY plan-vs-code falsification
  skill. Default verdict UNVERIFIED, PASS earned via code + test +
  negative check. Eight-phase procedure (Context Receipt → Task
  Ingestion → Atomic Requirements → Positive + Negative Verification →
  Adversarial Pass → Stage-Aware Verdict → Per-Task Table → Self-
  Attack + Model Check). Output contract: `audit_report.md`,
  `audit_requirements_matrix.jsonl`, `audit_trace.log`. Companions:
  `PHASES.md`, `DISPATCH.md`. Plugin manifest registered.
- **Pipeline-position section** added to every reformed skill
  (`vc-review`, `vc-marbles`, `vc-polarize`, `vc-audit`,
  `vc-followup`, `vc-dou`) so READ-ONLY vs WRITE membership is
  explicit at the top of each skill.

### Changed

- **`skills/vc-review/SKILL.md` → version 2.0.0**. Explicit READ-ONLY
  framing. Default-stance section: every spec-claim defaults to
  UNVERIFIED until proven by code/tests. Hard non-trust rules: PR
  descriptions, commit messages, `// done` comments, AICX entries,
  and `fixes #N` annotations are claims, not evidence. New evidence
  taxonomy (STRONG / MEDIUM / WEAK / NONE) on every finding. New
  adversarial pass between pattern scans and output. Stage-aware
  finding tags (`[STAGE-OK-DEFERRED]`, `[STAGE-PARTIAL]`,
  `[STAGE-DRIFT]`) prevent mid-stage PRs from being mis-blocked. New
  self-attack + model check section in output. Heavy detail moved to
  companion files `PRVIEW.md` (Phase 1 artifact generation) and
  `FINDINGS.md` (Phase 2 reading order, pattern scans, output
  template). SKILL.md trimmed from 12.4k to 10.7k.
- **`skills/vc-marbles/SKILL.md` → version 7.0.0**. Epistemic reframe
  from "Truth Convergence Rounds" to **"Deliberate Excess (Worker-
  Blind, Swarm-Wide)"**. Individual worker discipline preserved (one
  round, one commit, up to 3 targets) — but the swarm-level intent is
  now explicit: marbles in every crack, deliberate over-application,
  `vc-polarize` strips back. Pipeline-position diagram added. Worker
  blindness + reception remembers section condensed. Detail kept in
  existing `FLOW.md` and `RECEPTION.md` companions. SKILL.md trimmed
  to 11.8k.
- **`skills/vc-polarize/SKILL.md` → version 2.0.0**. Explicit
  framing as the **decisive cut** WRITE step that strips back the
  marbles excess. Pipeline-position diagram added. Heavy detail
  (full lifecycle, prism axis scoring criteria, context-corpus
  retention contract, minimum-gates list, failure-mode playbook)
  moved to new companion `PROCEDURE.md`. SKILL.md trimmed from 12.8k
  to 10.0k. Closing rail + suchar + canonical signature added.
- **`skills/vc-followup/SKILL.md` → version 2.2.0**. Explicit READ-
  ONLY framing in description and body. New "Pipeline Position"
  section locates it in the trajectory-perception slot.
- **`skills/vc-dou/SKILL.md` → version 2.0.0**. Explicit READ-ONLY
  framing in description and body. New "Pipeline Position" section
  locates it in the shipping-readiness slot between polarize and
  hydrate / decorate / release.
- **Marketplace cap discipline**: every reformed SKILL.md is now
  under the 12 000-character marketplace cap. Heavy detail lives in
  companion files at the same level as SKILL.md (no `references/`
  subdir), matching the `vc-operator` reference pattern.

### Removed

- Earlier "Unreleased" entries from 1.x cycle (Prism → Polarize gate,
  release-report contract, marketplace plugin stubs) are kept inline
  below for historical continuity; the 2.0.0 reform is the first
  named cycle.

---

## Unreleased (legacy 1.x — folded into 2.0.0 release scope)

### Added

- **Prism → Polarize gate (Plan 01)**: `vc-polarize` runner (`runtime/shell/vetcoders.sh:1168-1186`) now parses `loct prism --json` output, reads `total_score`, and routes to the default action band: `0..4` abort (no polarize, no memo), `5..8` memo (capture local Loctree tag / context-corpus entry, do not dispatch), `9..12` pass (run full `vc-polarize` agent dispatch), `13..15` doctrine (write default decision into context corpus). The runner also emits a prism preflight that injects the band/score into the polarize prompt so the dispatched agent can cite structural evidence rather than re-deriving it. The same threshold mapping is consumed independently by `vc-operator` (`src/polarize.rs:18-23 PolarizeBand::from_score`) — single source of truth at the boundaries `5 / 9 / 13`.
- New `.claude-plugin/plugin.json` stub manifests for `skills/vc-polarize/`, `skills/vc-intents/`, and `skills/vc-ownership/` to bring them in line with the rest of the framework's marketplace surface (vc-marbles / vc-init / vc-implement / vc-followup / vc-decorate / vc-hydrate / vc-dou / vc-prune / vc-research / vc-review / vc-release / vc-scaffold / vc-workflow / vc-agents / vc-delegate / vc-partner all already shipped manifests).
- `vc-release` Release Report Contract: every release report now requires
  four mandatory sections — security gate (Semgrep), exposed surface
  inventory (ports, proxies, auth, headers, secrets), deployment mode
  decision, and post-release install smoke from the **published**
  artefact (not the working tree). Canonical template lives at
  `skills/vc-release/references/release-report-template.md`.
- `skills/vc-release/references/deployment-reality.md` gains an
  "Exposed Surface Inventory" matrix (process, bind, port, proxy, TLS,
  auth, edge headers, secret materialization) so the inventory has a
  doctrine to reference.
- `tests/tui/test_release_contract.py` adds two locks: the four
  mandatory sections in `skills/vc-release/SKILL.md` plus the surface
  inventory tokens in `deployment-reality.md`. Future drift fails the
  pytest gate.

### Changed

- `skills/vc-release/SKILL.md` Semgrep release gate now points at the
  default `make semgrep` (mirrored by `scripts/hooks/pre-commit` and
  `scripts/hooks/pre-push`), classifies findings by dataflow boundary
  (path / regex / merge / shell / auth / other), and treats silent
  unavailability as a release block.
- `skills/vc-release/SKILL.md` Post-release smoke now requires a cold
  install from the published artefact source (registry URL, tag,
  digest, or download URL) and forbids using the local checkout as the
  witness.
- `docs/runtime/CONTRACT.md` quality gate section references the
  default `make semgrep` invocation and links the Release Report
  Contract.
- `docs/RELEASE_KICKOFF.md` adds `make semgrep` to the kickoff gate
  block and links the release report template plus the deployment
  reality matrix.
- `README.md` release-flow paragraph names the four-section release
  report and links the doctrine + template.

## 1.4.1 — 2026-04-22

### Added

- `tools/bin/` bundled toolchain drop-in for pre-notarized binaries: installer
  resolves local bundle first, then falls back to remote fetch. Enables
  fully-offline first-install UX for cold users.
  - `scripts/build_marketplace_bundle.py` now collects `tools/bin/**` into the
    plugin bundle artifact
  - `scripts/install-foundations.sh` gains `bundled_bin_root()` and
    `install_from_bundled()` with explicit fallback order
  - `installer_gui.py` surfaces bundled diagnostics as a new category in the
    pre-flight doctor
  - Documented resolution order and notarization expectations in
    `tools/bin/README.md`
- `runtime/scripts/marbles_verify_watch.sh` — standalone detached
  verification poller that waits for `*_verified.md`, updates `state.json` under
  lock, and marks verification as `completed` or `timed_out`. Decouples
  verification from the main watcher process and eliminates watcher holding
  PIDs for long periods.
- `vc-research` swarm launcher + worker charter for research passes that need
  multiple parallel agents converging on one plan.
- `vc-intents` skill for retrieval of past decisions from AI Chronicles /
  session history (complements `vc-init`).
- `await` helper for synchronous wait-on-agent flows in orchestration scripts.
- Help overlay in operator-tui launch flow.
- `--echo-stdout` flag in codex stream bridge for visibility into headless
  runs without losing machine-readable frames.
- Agent telemetry captured into loop `state.json` (dispatch time, completion
  time, exit code, session_id) so marbles state is the single source of truth
  for multi-loop runs.
- New skill **`vc-implement`** becomes the default end-to-end implementation
  skill. The `vc-justdo` name stays in-tree as a **backward-compatible
  alias** (frontmatter: `default: vc-implement`) so agents already wired to
  the old name keep working. Every public surface (START_HERE `Simplest path`,
  install banner, skill registry in `vetcoders_install.py`) now shows
  `vibecrafted implement ...`; the `justdo` command still executes but is no
  longer advertised. Full trigger-phrase inventory — including Polish triggers
  ("zrób to", "dowiez to", "od pomyslu do realizacji") — migrated to
  `vc-implement`.

### Changed

- Operator TUI refactored into a tabbed console — three tabs: **Monitor**
  (live runs + recent events), **Dispatch** (mission kind / agent / runtime /
  prompt), **Controls** (attach / resume / report / transcript for selected
  run). Tab navigation contracts stabilized (`Tab` / `Shift+Tab`, arrow keys
  scoped to active tab), direct tab switches normalized.
- Operator TUI split into dedicated **`vc-operator`** crate at the
  `vc-runtime` workspace root. The crate owns its versioning (`vibecrafted-
operator v0.1.1`) and release cycle. `scripts/vibecrafted` launcher gracefully
  falls back between in-source operator-tui and the installed `vc-operator`
  binary, with a clear error if neither is available.
- Marbles spawn now **honors `VIBECRAFTED_MARBLES_RUN_ID` only when it doesn't
  conflict with existing state** (unless `VIBECRAFTED_MARBLES_RESUME=1` is set
  explicitly). Otherwise it mints a **PID-suffixed** run id (`$$` appended to
  the timestamp) so parallel spawns cannot collide on the same second.
- Marbles watcher decoupled from verification polling — instead of holding PIDs
  and polling inline, it marks loops as `pending` and hands off to
  `marbles_verify_watch.sh` via `nohup`. Summary logic simplified; configurable
  verification grace period added.
- vc-frame spawn uses **tab/pane IDs** (not just names) for targeting marbles
  panes — non-disruptive spawn that doesn't steal focus from operator's
  active pane; tab index noise suppressed in marbles spawn output.
- vc-frame layouts renamed (`operator` / `vc-marbles` / `vc-workflow` / `vc-
research` / `vc-dashboard`) with matching launcher and test updates.
- Uninstall now removes **only manifest-tracked entries** + framework
  artifacts — no broad filesystem sweeps that could clobber user files.
- Operator TUI launches **Ghostty** natively (via `vc-frame`) as the terminal
  surface when running in `terminal` / `visible` runtime.
- Marbles active-only run filter in operator-tui so Monitor tab stops showing
  cold runs from previous sessions.
- System-wide docs refresh: README, FAQ, FAQ-ANSWERED, QUICK_START, SKILLS,
  WORKFLOWS, installer/DESIGN, workflows/MARBLES — copy brought in line with
  the default command set (`vibecrafted implement`) and the current 1.4.1
  surface.
- FLOW + SKILL polish across `vc-delegate`, `vc-init`, `vc-justdo` (alias),
  `vc-partner`, `vc-research`, `vc-scaffold`, `vc-workflow`.

### Fixed

- Marbles wrapper publication drift: wrappers are now force-republished on
  every dispatch so stale files can't point at removed scripts.
- Launcher drift repair in place — `doctor` now fixes broken launcher
  configurations without requiring a clean install.
- `doctor` rc repair path for launcher rc files.
- Operator TUI control-plane wiring — pane names + control-plane state
  aligned, polish pass on tab surfaces.
- Operator TUI terminal agnosticism — no longer hard-codes vc-frame or any
  single terminal assumption.
- Operator TUI vc-frame env isolation in tests (previously leaked
  `VC_FRAME_CONFIG_DIR` between parallel test runs).
- Flaky CI expectations for marbles statuses and Makefile dry-run output.
- `uv` bootstrap assertion messages now align with export `PATH` checks.

### Removed

- `operator-tui/` directory from vibecrafted (moved to dedicated `vc-
operator` crate — see **Changed** above).
- Stale research docs (`docs/MODULARIZATION_PLAN_2026_04_16.md`,
  `docs/REPO_GROUND_TRUTH_2026_04_13.md`, multiple `docs/research/*.md`
  artifacts, `docs/FAQ-ANSWERED.md` noise).
- `scripts/mission-control/restore-orphaned.sh` (superseded by
  `marbles_verify_watch.sh` + ghost reaping path in the watcher).

## 1.4.0 — 2026-04-18

### Added

- Marbles `delete` subcommand for cleaning up finished / abandoned runs.
- Installer TUI textual wizard flow with real keybindings, sticky layout,
  dynamic interpolation, and manifest-driven step rendering — implements the
  `docs/installer/` mockups 1-for-1.
- Worker contract in generated child plans so sub-agents know the exact
  constraints of their slice (scope, artifacts, gates).
- Shell syntax checks for spawn scripts in the pre-commit path.
- Attended bootstrap confirmation + `--yes` flag in `install.sh` — humans get
  a "what's about to happen" pause by default; CI / automation pipelines get
  a clean non-interactive path.
- Regression tests for installer manifest / branding + codex_stream_bridge.
- `vc-frame` panes for marbles dispatch (in place of bare new-tab).

### Changed

- **Installer TUI-first swap**: terminal front-door defaults to the guided
  TUI wizard; the GUI stays available via `--gui`. Sticky-bottom streaming
  log, unified `make install` entrypoint.
- vc-frame orchestration hardened: tab isolation, spawn probe before every
  dispatch, session GC for stale vc-frame daemons.
- Framework bumped **1.3.0 → 1.4.0**; VERSION truth propagated across all
  installer surfaces (no more "1.3.0 in README, 1.4.0 in bundle"
  disagreements).
- Installer GUI converted to single-page no-scroll layout; branding + docs
  polished; FRAMEWORK tag squared-block unicode restored (reverted accidental
  normalization).

### Fixed

- `uv` bootstrap shell boundary: installer now correctly propagates the
  ephemeral uv install into the downstream shell instead of losing it to
  subshell isolation.
- Marbles spawn failures no longer masked by codex stream: pipeline status is
  read from pipefail, not inferred from "did we get some stdout?".
- Marbles next-hook contract: child loops only advance after the real
  handoff, not after tmp prompt file arrives.
- Arrow keys in installer TUI now scroll within a step instead of switching
  steps.
- Truthful landing page: no fake URLs, no fake commands, copy matches actual
  install flow.
- Marbles ancestor steering: mtime race fixed, spawn fallback hardened, run-
  id / spawn prefs respected under concurrent dispatches.
- Watcher state race, doctor dashboard smoke, session semantics, update
  path, commit labels — a marble convergence sweep closed these as one loop.

## 1.3.0 — 2026-04-11

### Added

- Browser-based guided installer: `scripts/installer_gui.py`
- `install.sh --gui` bootstrap path for the guided installer
- `make gui-install` for launching the guided installer from source
- Marketplace submission pack in `docs/SUBMISSION_FORMS.md`
- Release kickoff docs now ship inside the marketplace bundle artifact
- Release-contract pytest guard for promise / CTA drift across public surfaces

### Changed

- Product positioning now leads with the release-engine promise instead of generic framework language
- Public install docs now explicitly show the guided GUI path for founders and non-terminal operators
- `install.sh` help text now matches the actual bootstrap paths instead of promising a TUI that was not wired in
- `install.sh` fallback now prefers the live GitHub source snapshot when the channel manifest is missing, instead of pinning a stale tarball URL
- Submission forms now cite current adjacent-tool directory evidence and official launch surfaces
- Frontier / installer copy now talks about the current framework surface instead of a stale frozen version string

## 1.2.1 — 2026-04-01

### Added

- `make foundations` — portable installer for historical loctree / ai-contexters binaries
  - Superseded by the product foundation contract: do not use this entry as a current install source
  - Current runtime foundations are validated on `$PATH`; missing AICX/Loctree should point at the canonical Loctree installer
  - `make foundations-check` for dry-run preview
  - `scripts/install-foundations.sh` works standalone or via Make
- Python-native `shutil.copytree` fallback when `rsync` is not available
  - `rsync` downgraded from critical to recommended dependency
  - `make install` now succeeds on systems without rsync (fresh containers, Windows WSL)

### Fixed

- **Python 3.11 compatibility**: f-string backslash escapes in `vetcoders_install.py`
  caused `SyntaxError` on Python < 3.12 (the `\U` unicode escapes inside f-string
  expressions). Extracted to variables.
- `rsync` no longer blocks installation — `make install` uses pure-Python copy as fallback

## 1.2.0 — 2026-03-29

### Added

- Marbles loop orchestrator: `marbles_spawn.sh`, `marbles_next.sh`, `marbles_plan.sh`
  - `<agent>-marbles --depth <n> --count <y>` — crawl recent sessions, run convergence loops
  - `<agent>-marbles --task <plan.md> --count <y>` — loop against a plan file
  - `<agent>-marbles --prompt "text" --count <y>` — inline prompt loops
  - Filesystem-based loop chaining via `success_hook` — no cron, no watcher
  - Convergence through CODE STATE, not report chaining — each loop gets the same plan, sees improved repo
  - `CONVERGENCE.md` written after final loop (or on failure)
  - Lock files in `$VIBECRAFTED_ROOT/.vibecrafted/locks/<org>/<repo>/`
- `--success-hook` and `--failure-hook` flags for all spawn scripts (claude, codex, gemini)
- Landing page: 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. → 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. rebrand, sprite caching for Safari performance
- Installer TUI wizard (in progress): Rich-based step-by-step flow from docs/installer/ mockups

### Changed

- Product name: **𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.** (the product), **𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚜𝚖𝚊𝚗𝚜𝚑𝚒𝚙** (the methodology)

### Fixed

- **Clarified `zsh -ic` requirement for shell helpers**: The 1.0.3 changelog stated
  "removes zsh runtime dependency" which is true for spawn SCRIPTS (`eval "$SPAWN_CMD"`
  works in bash). However, operator-facing shell helpers (`codex-implement`,
  `claude-research`, etc.) are functions sourced from `.zshrc`/`.bashrc` and require
  an interactive shell to load. The default agent-to-agent invocation remains
  `zsh -ic "codex-implement $PLAN"` (or `bash -ic` on zsh-less systems).
  Skill documentation (vc-agents SKILL.md) updated to reflect this.
- Marbles board animation: sprite pre-rendering (was creating new canvas per marble per frame — Chrome hid the cost,
  Safari showed 5fps)
- `init-hooks` Makefile target: guard with `git rev-parse --git-dir` for non-git bootstrap contexts
- Portable test: marbles helper uses new `--prompt` interface, flexible `run_id` check

## 1.0.4 — 2026-03-29

### Added

- 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. framework overview and README branding
- Marbles orchestration skill and hook/runtime fixes
- AICX extract skill documentation
- Mission-control layout for vc-frame
- Compact install mode and enhanced logging
- Screenscribe foundation setup
- GitHub Pages onboarding pages for Quick Start and answered FAQ
- Marketplace listing draft for the framework
- GitHub issue templates for bugs and workflow requests

### Changed

- Refactored installer UI and polished docs
- Reset Gemini plan dir on install
- Uses `VIBECRAFTED_HOME` with Gemini include dir

### Fixed

- Canonical URL, sitemap, and robots alignment for the public presence surface
- Public docs updated to match the current shell-agnostic helper path and non-interactive install flow
- Installer issues and UI
- Gemini and MCP stream filters

## 1.0.3 — 2026-03-27

### Added

- Framework version tracking (`VERSION` file, installer + doctor report it)
- Bash shell helper support — helpers work in bash and zsh, not zsh-only
- Dual rcfile installation (`.bashrc` + `.zshrc`)
- Release CI workflow (tag `v*` builds archive without `presence/`, GitHub Release with SHA256)
- `curl-bootstrap` CI job for install.sh end-to-end smoke testing
- Stream filters: Claude jq, Codex jq, Gemini awk — clean readable agent terminal output
- Codex `--json` JSONL streaming with structured event parsing
- Spawn telemetry: `framework_version`, `prompt_id`, `run_id`, `loop_nr`, `skill_code`, `duration_s`
- Skill helpers: `<agent>-dou`, `<agent>-hydrate`, `<agent>-marbles`, `<agent>-scaffold`, etc.
- `vc-dashboard` for vc-frame Mission Control layout
- Active spawn scan before each launch
- Material palette: copper/patina/timber/steel/stone

### Changed

- Spawn launcher: `zsh -ic` -> `eval` — removes zsh runtime dependency
- Terminal.app spawn: `zsh -ic` -> `bash`
- Shell helpers renamed `vetcoders.zsh` -> `vetcoders.sh` (compat symlink kept)
- Helper install path: `$HOME/.config/vetcoders/vc-skills.sh` (was `$HOME/.config/zsh/vc-skills.zsh`)
- CI no longer requires zsh on Ubuntu
- Installer: zsh downgraded from required to optional dependency
- No hardcoded model flags in spawn scripts — agents choose their own

### Fixed

- Headless spawn failing in CI (zsh -ic in nohup context)
- Codex spawn exit code 1 from session grep with pipefail
- Loctree release URL (Loctree-Repos -> Loctree/Loctree)

### Removed

- Judgmental/condescending language from presence copy and FAQ
- zsh as runtime dependency for agent spawns

## 1.0.2 — 2026-03-27

### Added

- `LICENSE` — Business Source License 1.1
- `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`
- Skill taxonomy refactor: 17 skills with coherent pipeline references
- `vc-justdo`, `vc-scaffold`, `vc-release` skills
- FAQ-ANSWERED.md
- Centralized artifacts under `$VIBECRAFTED_ROOT/.vibecrafted/`
- OG image and social card meta tags
- GitHub issue templates

### Fixed

- Hardcoded paths in skill files replaced with portable references

### Removed

- `vc-ship`, `vc-ownership` (absorbed into other skills)
- 60-file taxonomy cleanup

### Skills (as of 1.0.2)

- vc-agents 1.4.1, vc-decorate 1.1.0, vc-delegate 1.0.0, vc-dou 1.0.0
- vc-followup 1.0.0, vc-hydrate 1.0.0, vc-init 2.2.0, vc-justdo 2.0.0
- vc-marbles 1.1.0, vc-partner 2.0.0, vc-prune 2.0.0, vc-release 0.1.0
- vc-research 1.2.0, vc-review 1.0.0, vc-scaffold 0.1.0, vc-screenscribe 1.2.1
- vc-workflow 1.0.0
