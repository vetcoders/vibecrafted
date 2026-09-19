# VC Operator 22-Next: Mission Control From Shell To Product

- Repo: `/Users/Shared/vc-workspace/vetcoders/vc-operator`
- Branch: `main`
- Baseline commit: `a2a0a51`
- Generated: `2026-05-12`
- Planning mode: standalone `vc-operator` roadmap after extraction from `vibecrafted/operator`
- Reference shape: `vibecrafted/docs/plans/META_22_SCAFFOLD_TO_RELEASE_ONGOING.md`

## Product Thesis

`vc-operator` should become the local Mission Control surface for Vibecrafted
work: the place where the operator sees repos, agent runs, mux health, reports,
gates, and release readiness without spelunking through terminals.

The first product promise is intentionally narrow:

> Open the Operator and know what is running, what is blocked, what passed, what
> failed, and what the next useful action is.

This repo should not become a generic dashboard framework. It should expose
runtime truth for the Vetcoders agent workflow.

## Current Truth

The extraction is real and shippable enough to build on:

- Root `Cargo.toml` is now a Rust workspace, not a single TUI crate.
- `mux-agent/` owns MCP lifecycle, config discovery, IPC, status snapshots, and
  wizard/security flows.
- `tui-agent/` owns the terminal operator cockpit and remains the console
  source of truth.
- `tray-agent/` owns the menu bar sentinel and IPC client surface.
- `shell-agent/` owns UniFFI, Swift/Xcode, `.app`, and DMG packaging.
- Root `Makefile` exposes `gates`, `app`, `dmg`, and `dmg-signed`.
- Latest verified gates from the extraction cut: `make gates`, all-feature
  check, no-default-features check, `make app`, launch probe, and `make dmg`.

Loctree current state:

- Operator CLI full-context scan supplied during planning: 34 files, 37 import
  edges, clean worktree.
- MCP repo-view scan around the same baseline saw the wider generated/source
  surface: 72 files, 23,297 LOC, Rust/Make/Shell.
- Treat the exact file count as scanner-surface dependent; treat the repeated
  hub list and build gates as the durable evidence.
- Top hubs: `mux-agent/src/config.rs`, `mux-agent/src/state.rs`,
  `mux-agent/src/scan.rs`, `tray-agent/src/types.rs`, `mux-agent/src/multi.rs`.
- Health signals: 3 cycles, 13 twins, 0 high-confidence dead exports.
- Worktree baseline was clean at plan time.

Task-scoped Loctree query note:

- `loct context --full --markdown --task 'budowa panelu operatora'` matched 0
  files. That is itself useful: the repo does not yet expose a semantic spine
  named "operator panel". Phase 1 should create that spine explicitly through
  `OperatorSnapshot` and typed shell-facing APIs.

## Definition Of Done For 22-Next

22-next is done when `Vibecrafted.app` is no longer only a wrapper. It must
launch into a useful local Mission Control screen backed by real repo/runtime
data, and it must preserve the terminal/TUI path as an equal first-class
fallback.

Acceptance:

- `make gates` passes.
- `make app` passes.
- `.app` launch probe stays alive for at least 5 seconds.
- The app home screen shows real workspace, git, gate, mux, and recent report
  state via Rust/UniFFI, not mocked text.
- At least one safe action can be launched from the app and recorded with a
  typed result.
- `make dmg` succeeds or prints a typed blocker.
- The TUI and shell app agree on the same core status model.

## Non-Negotiables

- Local-first. No cloud backend, login, accounts, or database in 22-next.
- Runtime truth over decorative UI. Empty or stale data must say so clearly.
- No hidden shell magic. Every action names the command it will run and where.
- No silent rewriting of AI client configs outside explicit mux danger paths.
- No root-level TUI crate resurrection. `tui-agent/` remains the source of
  truth.
- No build artifacts, `.loctree`, DerivedData, DMGs, or daily artifact symlinks
  in git.
- Signed/notarized distribution remains operator-controlled; app code can
  detect and explain blockers, not fake certification.

## Architecture Decisions

### Decision 1: Introduce A Shared Operator Snapshot Model

Create a small shared model surface that can be consumed by TUI, tray, and
shell:

- workspace identity
- git status
- control-plane run summaries
- recent reports
- mux health summaries
- gate/build status
- release readiness

Preferred location: `shell-agent/ffi` initially exposes the UniFFI API, but the
model types should live where TUI and shell can share them cleanly. If this
starts to thicken, split a `operator-core` crate rather than duplicating structs
again.

Trade-off: a new crate is cleaner but may be premature. Start inside existing
crate boundaries and extract only when duplication appears.

### Decision 2: Keep Actions Typed And Previewable

The shell app should not run arbitrary strings from buttons. Actions become
typed commands:

- `RunGates`
- `BuildApp`
- `BuildDmg`
- `OpenReport`
- `AttachRun`
- `StartWorkflow`
- `MuxHealth`

Each action returns:

- command preview
- working directory
- start/end timestamp
- exit status
- stdout/stderr tail
- artifact paths

Trade-off: slower than raw shell buttons, but safer and auditable.

### Decision 3: Shell App Reads First, Writes Later

Phase 1 should make the app trustworthy as an observer before it becomes a
control surface. Start with live status and report inspection, then add
commands.

Trade-off: less flashy first demo, but much lower risk of a GUI that launches
things without understanding failures.

### Decision 4: Tray Is A Sentinel, Not A Second App

Tray state should be tiny:

- healthy
- running
- blocked
- attention needed

Tray can offer quick actions, but the detailed surface lives in TUI or shell.

Trade-off: avoids three competing UIs.

### Decision 5: Release Readiness Is A Product Pane

The app should show packaging truth:

- `make app`
- `make dmg`
- signing identity available/missing
- notarization profile status
- last artifact path

Trade-off: release concerns become visible early, which is exactly the point.

## Phase Map

### Phase 0: Baseline Guardrails

Goal: preserve the extracted workspace shape.

Tasks:

1. Add a `make doctor` target that prints workspace members, git state, xcodegen
   availability, Xcode version, and last app/DMG artifacts.
2. Add a lightweight `scripts/verify-repo-boundaries.sh` guard for ignored
   build artifacts and forbidden tracked paths.
3. Add a README section that names the 22-next product promise and the current
   source-of-truth crates.

Acceptance:

- `make doctor` exits 0 and prints actionable host state.
- Boundary guard fails if `.loctree`, DerivedData, `.DS_Store`, `target`, or
  DMG files are staged.
- `make gates` still passes.

### Phase 1: Operator Snapshot API

Goal: one Rust call returns the home-screen truth.

Tasks:

1. Define `OperatorSnapshot` with workspace, git, mux, reports, gates, and
   release readiness sections.
2. Implement snapshot collection from local filesystem and existing mux/TUI
   readers.
3. Expose snapshot through UniFFI.
4. Add fixture-backed tests for empty, healthy, dirty, and blocked states.

Acceptance:

- Swift can call `load_operator_snapshot()` from the app process.
- Empty/missing inputs return typed empty states, not panics.
- TUI and shell use compatible status language.

### Phase 2: Shell Home Screen

Goal: `Vibecrafted.app` opens to a useful Mission Control home.

Tasks:

1. Replace placeholder Swift panes with a real home layout:
   workspace rail, status strip, run/report list, inspector.
2. Bind the UI to `OperatorSnapshot`.
3. Add refresh state and visible error states.
4. Add app-level smoke probe for launch and snapshot load.

Acceptance:

- App launches and shows current repo identity, git state, mux summary, recent
  reports, and release readiness.
- No mocked success state appears when data is missing.
- Launch probe remains green.

### Phase 3: Report And Transcript Inspection

Goal: operator can inspect agent output without hunting paths.

Tasks:

1. Index report roots under `.vibecrafted`/artifact conventions and current
   repo-local paths.
2. Render latest reports with status, agent, prompt id, run id, and file path.
3. Add "open in editor/finder" actions where safe.
4. Add transcript tail preview when transcript/log exists.

Acceptance:

- Latest reports appear in both TUI and shell surfaces from the same discovery
  rules.
- Broken symlinks or missing files appear as typed warnings.

### Phase 4: Safe Action Runner

Goal: actions can run from shell without becoming unsafe shell buttons.

Tasks:

1. Define typed `OperatorAction` and `ActionResult`.
2. Implement safe actions for `make gates`, `make app`, `make dmg`, and mux
   health.
3. Stream or poll status into the snapshot.
4. Persist recent action results under local operator state.

Acceptance:

- App can launch `make gates` and show exit status plus tail output.
- Failed commands surface typed blocker text.
- No arbitrary command execution API is exposed over UniFFI.

### Phase 5: Mux Health And Drift Panel

Goal: make mux status an operator-first surface.

Tasks:

1. Reuse `rmcp-mux` status snapshot schema.
2. Show per-service health, clients, pending count, restarts, and failure
   reason.
3. Surface client drift and suggested fix action.
4. Keep dangerous rewrites preview-first and explicit-confirm only.

Acceptance:

- Healthy and unhealthy mux states are visible in shell and TUI.
- Drift states name the affected client config paths.
- No non-danger strategy rewrites host configs.

### Phase 6: Release Readiness Pane

Goal: app can tell whether a desktop release is locally buildable.

Tasks:

1. Read Xcode/xcodegen/toolchain presence.
2. Read Developer ID signing identity presence without mutating keychain.
3. Detect notarization keychain profile availability or mark unknown.
4. Show last `.app` and DMG artifact metadata.
5. Link to exact commands for signed DMG and notarization.

Acceptance:

- Missing signing identity is named as a blocker.
- Unsigned local DMG path remains available.
- Signed/notarized state is never implied without evidence.

### Phase 7: Tray Sentinel

Goal: tray becomes a small, trustworthy signal.

Tasks:

1. Reduce tray status into four states: healthy, running, blocked, attention.
2. Connect tray to the same snapshot/action result model.
3. Add menu items for open app, open TUI, run mux health, run gates.
4. Add tests around state mapping and menu actions.

Acceptance:

- Tray does not duplicate the full app UI.
- Tray state changes when mux/gates/release readiness changes.

### Phase 8: First-User Packaging

Goal: a human can install and understand the operator.

Tasks:

1. Add `docs/INSTALL.md` for local build, unsigned DMG, signed DMG, and
   notarization.
2. Add `docs/OPERATOR_FIRST_RUN.md` with first run expectations and common
   blockers.
3. Add `make release-local` as a read-only wrapper over gates/app/dmg.
4. Add release artifact manifest output for `.app`, DMG, signatures, and host
   prerequisites.

Acceptance:

- A fresh macOS machine with prerequisites can run the documented local build.
- Missing prerequisites produce typed blocker messages.

## Agent-Sized Cuts

| Plan  | Title                          | Primary files                                                     | Dependencies        | Acceptance                          |
| ----- | ------------------------------ | ----------------------------------------------------------------- | ------------------- | ----------------------------------- |
| 22-01 | Add doctor and boundary guards | `Makefile`, `scripts/`, `.gitignore`                              | none                | `make doctor`, boundary test        |
| 22-02 | Define snapshot model          | `shell-agent/ffi`, maybe `tui-agent/src`                          | 22-01               | unit tests pass                     |
| 22-03 | Expose snapshot over UniFFI    | `shell-agent/ffi`, `shell-agent/app`                              | 22-02               | Swift can call snapshot             |
| 22-04 | Build shell home screen        | `shell-agent/app/Vibecrafted/Views`                               | 22-03               | app shows real repo state           |
| 22-05 | Index reports/transcripts      | `tui-agent/src`, `shell-agent/ffi`                                | 22-02               | fixtures cover missing/broken paths |
| 22-06 | Implement safe action runner   | `shell-agent/ffi`, `tui-agent/src/launch.rs`                      | 22-02               | action result records exit status   |
| 22-07 | Wire gates/app/dmg actions     | `Makefile`, `shell-agent/ffi`, Swift views                        | 22-06               | app runs `make gates` safely        |
| 22-08 | Build mux health panel         | `mux-agent`, `tui-agent`, `shell-agent`                           | 22-02               | unhealthy mux state visible         |
| 22-09 | Build release readiness pane   | `shell-agent/scripts`, Swift views                                | 22-04               | signing/notary blockers typed       |
| 22-10 | Simplify tray sentinel         | `tray-agent`                                                      | 22-02               | tray maps snapshot to 4 states      |
| 22-11 | Break top structural cycles    | `mux-agent/src/ipc`, `mux-agent/src/multi.rs`, `tray-agent/src/*` | after UI/API stable | Loctree cycles reduced              |
| 22-12 | First-user docs and manifest   | `docs/`, `Makefile`                                               | 22-07, 22-09        | local release runbook works         |

## Structural Cleanup Backlog

These are not Phase 1 blockers, but they are real:

- Break `mux-agent/src/ipc/server.rs` <-> `mux-agent/src/ipc/handlers.rs`.
- Break `mux-agent/src/multi.rs` <-> `mux-agent/src/state.rs`.
- Break the tray cycle through `icons -> types -> menu -> state -> ipc_client`.
- Consolidate `HostKind`/`HostFormat` style twins in `mux-agent/src/common.rs`
  and `mux-agent/src/scan.rs`.
- Review `tui-agent/src/app.rs` and `tui-agent/src/lib.rs` for remaining
  duplicate action/detail rendering logic.

Do not do these as drive-by refactors during the first shell home build unless
they directly block the slice.

## Verification Gates

Every implementation cut should run:

```bash
make gates
cargo check --workspace --all-features
cargo check --workspace --no-default-features
```

App/release cuts additionally run:

```bash
make app
find shell-agent -name '*.app' -type d
codesign --verify --deep --strict shell-agent/build/Vibecrafted.app
make dmg
```

For shell UI cuts, add:

```bash
APP=shell-agent/build/Vibecrafted.app
"$APP/Contents/MacOS/Vibecrafted" >/tmp/vc-operator-launch.log 2>&1 &
```

Then verify it stays alive for at least 5 seconds or prints a typed launch
blocker.

## Explicitly Out Of 22-Next

- Cloud sync, accounts, teams, billing, or web backend.
- Marketplace/public installer polish beyond local DMG and documented signed
  path.
- Automatic notarization submission without operator-owned credentials.
- Replacing the TUI with Swift.
- Rewriting mux config security boundaries.
- Creating a second generic workflow framework inside `vc-operator`.

## Suggested First Dispatch

Start with plans 22-01 through 22-04 as one controlled wave:

1. `22-01` gives the extracted repo guardrails.
2. `22-02` gives the product a shared truth model.
3. `22-03` proves Rust-to-Swift data flow.
4. `22-04` turns the `.app` into a real Mission Control home.

This wave is the smallest product-validating move. It makes the shell useful
without giving it dangerous powers too early.

## Operator Decision Needed

Pick the first shell-home bias:

1. **Run/Report first**: latest agent runs, reports, transcripts, next action.
2. **Release first**: gates, app, DMG, signing, notarization blockers.
3. **Mux first**: daemon health, client drift, rewire previews.

Recommendation: choose **Run/Report first**, with release readiness as the
right-side secondary panel. That makes the app useful every day, not only on
release day.
