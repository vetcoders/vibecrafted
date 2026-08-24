---
title: "loctree-canary: Vibecrafted — truth thrones and their competitors"
author: "Claude, the Coding Agent"
version: "0.1.0 (2026-08-23)"
description: "Append-only inventory of every power in this repo that has more than one owner, with the throne decided per power and the competitors cut to zero — no shims, no compat layers."
session_id: "48924e5f-ecb3-4b18-9a67-3647ecc09312"
summary: "Vibecrafted looked healthy on the operator's machine for weeks only because each reinstall re-applied the projections that the DMG path never produced. A clean first run on 2026-08-23 (wipe manifest of 58 entries, then open -a Vibecrafted 4.2.4) exposed the pattern: the same truth — where the runtime lives, which skills an agent has, whether a run is alive — is defined in several places and each definition is locally reasonable. This journal names the thrones and removes the competitors."
reference_journal: "vetcoders/codescribe .loctree/canary/JOURNAL.md (Codex, 2026-08-23)"
repo: vetcoders/vibecrafted
base: "deprivatize/vibecrafted @ 73c474df"
---

# LOCTREE CANARY JOURNAL — vibecrafted

## Introduction

This is not a docstring sweep. `vc-canary` 0.1.0 in this repo catalogs roles
(2049 units on 2026-08-09, `artifacts/…/2026_0809/reports/canary-catalog.json`);
nobody reads that catalog at agent start, and the only consumer of its output
is a marketing playground asset copied byte-for-byte into three abandoned
vibecrafted-io worktrees. Coverage is not the problem. Ownership is.

The method is the one Codex applied to Codescribe the same day: for every
power that matters at runtime, list every file that exercises it, mark the
throne, and cut the rest. A competitor is not wrapped, adapted or delegated
to — it is deleted. A fix that needs "one more type to synchronize two existing
ones" is a sixth layer, not a fix.

Sense organ: `loct twins` (279 exact twins, 138 outside the deck/scripts
mirror), `loct find --where-symbol`, `loct find --who-imports`, `loct follow`.
No grep. No memory.

Legend:

- 🔥 direct collision in the daily runtime (two definitions can disagree today)
- ⚠ same responsibility in another stage, language or install path
- ◌ test-only / offline / compatibility competitor

## Phase I — thrones and competitors

### 1. Runtime roots: where Vibecrafted lives on a machine

The power: resolve `VIBECRAFTED_HOME`, `VIBECRAFTED_RUNTIME_HOME`,
`VIBECRAFTED_TOOLS_HOME`, launcher bin. Every installer, launcher, reader and
reaper starts here. Today: **nine definitions in four languages**, and they do
not agree.

| Collision | Definition                                                                                                                                       | Competitor of                                                                                                                                                               | Disagreement                                                                                                                                                                                                                                                                           |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 👑        | `vibecrafted-core/vibecrafted_core/runtime_paths.py:75` `vibecrafted_home` (10 importers)                                                        | —                                                                                                                                                                           | throne: truthy env → expanduser, else `~/.vibecrafted`                                                                                                                                                                                                                                 |
| 🔥        | `vibecrafted-core/vibecrafted_core/dispatch/worktrees.py:37` `vibecrafted_home`                                                                  | runtime_paths                                                                                                                                                               | `os.environ.get(..., "~/.vibecrafted")` — an **empty** `VIBECRAFTED_HOME` resolves to `.` (cwd); the throne resolves to `~/.vibecrafted`. `dispatch/doctor.py:16` imports this one, not the throne.                                                                                    |
| ⚠         | `plugins/iterm2/vibecrafted_iterm2/vc_launcher.py:58` `vibecrafted_home`                                                                         | runtime_paths                                                                                                                                                               | identical semantics, separate copy "because the AutoLaunch sandbox cannot import vibecrafted_core" — a boundary mirror with no parity test                                                                                                                                             |
| ⚠         | `vibecrafted-server/control-core/src/read.rs:49` `vibecrafted_home`                                                                              | runtime_paths                                                                                                                                                               | Rust mirror, documented as such; empty env → default (matches throne)                                                                                                                                                                                                                  |
| 🔥        | `scripts/install-foundations.sh:55` `default_vibecrafted_home`                                                                                   | install.sh:905                                                                                                                                                              | honors `VIBECRAFTED_ROOT/.vibecrafted` as a home — but `VIBECRAFTED_ROOT` is the **release generation root** exported by every launcher (`~/.local/share/vibecrafted/releases/<gen>`), so on a DMG machine foundations would put the control plane **inside the immutable generation** |
| 🔥        | `scripts/migrate_agents_workspace.sh:16` `default_vibecrafted_home`                                                                              | install.sh                                                                                                                                                                  | same `VIBECRAFTED_ROOT/.vibecrafted` reality                                                                                                                                                                                                                                           |
| ⚠         | `install.sh:905` `default_vibecrafted_home`, `:913` `default_vibecrafted_runtime_home`, `canonical_vibecrafted_{home,runtime_home,launcher_bin}` | scripts/install-foundations.sh (byte-identical copies of the three `canonical_*`, diverging `default_*`, `enforce_runtime_root_contract`, `pause_runtime_contract_failure`) | install.sh is the `curl \| bash` bootstrap and must be self-contained; foundations/runtime/migrate are in-repo and simply copied the functions                                                                                                                                         |
| ⚠         | `scripts/install-runtime.sh:49` `vibecrafted_home`                                                                                               | install.sh                                                                                                                                                                  | third shell spelling of the same function                                                                                                                                                                                                                                              |
| ⚠         | `vibecrafted-app/tui-agent/src/config.rs:173` `default_vibecrafted_home`                                                                         | runtime_paths                                                                                                                                                               | Rust launcher mirror                                                                                                                                                                                                                                                                   |
| ◌         | `scripts/runtime_paths.py` (34 LOC)                                                                                                              | runtime_paths                                                                                                                                                               | PEP 562 shim that loads the canonical module **as a second module object** (`_vibecrafted_canonical_runtime_paths`) — two `vibecrafted_home` functions in `sys.modules`; `scripts/control_plane_state.py:_sync_overrides` exists only to re-bind one to the other                      |

Decision: throne = `vibecrafted_core.runtime_paths` for Python; one sourced
shell library for every in-repo script; `install.sh` keeps its own copy as the
bootstrap and a parity test pins it to the library (the same contract the
deck/scripts mirror already has). `VIBECRAFTED_ROOT/.vibecrafted` is not a
home and dies. Rust and the iterm2 plugin are boundary mirrors: one per
language, documented against the env contract, no third copy.

### 2. Control-plane state: what is running, what settled

| Collision | Definition                                                                                                                                                                                                                                                                                               | Competitor of                                 | Disagreement                                                                                                                                                                                                                                                                                                                                                      |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 👑        | `vibecrafted-core/vibecrafted_core/control_plane.py` (4136 LOC): `sync_state:3350`, `cli:4093`, `lookup_run`, `run_snapshot_dir`, `event_stream_path`, `RunStatus`, `Event`, `DeliveryAxes`                                                                                                              | —                                             | throne (writer)                                                                                                                                                                                                                                                                                                                                                   |
| ◌         | `scripts/control_plane_state.py` (53 LOC)                                                                                                                                                                                                                                                                | control_plane                                 | "Legacy scripts/ shim" + `_sync_overrides()` that rebinds `vibecrafted_home` between two module objects. Resolved by a **three-candidate file-path chain** in `deck/vibecrafted:393 _control_plane_script` and `runtime/scripts/lib/meta.sh:3 spawn_control_plane_script` (repo source → installed tools → script root) — which copy runs depends on which exists |
| ◌         | `scripts/control_plane_launch.py` (27 LOC)                                                                                                                                                                                                                                                               | workflow                                      | same shim pattern for `launch_workflow`/`normalize_launch_spec`                                                                                                                                                                                                                                                                                                   |
| ⚠         | `vibecrafted-server/control-core/src/read.rs:2300`, `model.rs:1653` — `lookup_run`, `run_snapshot_dir`, `event_stream_path`, `control_plane_home`, `RunStatus`, `Event`, `DeliveryAxes`, `DeliveryState`, `ExecutionState`, `ProofState`, `SettlementVerdict`, `TrustReceiptV1`, `operator_session_name` | control_plane.py                              | Rust **reader** of the same on-disk state. Two implementations of the liveness/settlement grammar; parity only by convention                                                                                                                                                                                                                                      |
| ⚠         | `vibecrafted-app/tui-agent/src/state.rs:793 classify_run`                                                                                                                                                                                                                                                | `run_triage.py:3177 classify_run`             | third classifier of "is this run alive / what kind of failure"                                                                                                                                                                                                                                                                                                    |
| ⚠         | `workspace_catalog.py:860 operator_session_name`                                                                                                                                                                                                                                                         | `control_plane.py:1423 operator_session_name` | two Python spellings of session identity, plus `model.rs:436`                                                                                                                                                                                                                                                                                                     |

Decision tonight: delete the two shims and the path chains (Python throne is
reachable as `-m vibecrafted_core.control_plane` from the deck and from
`spawn_python_module` in meta.sh already). The Rust reader stays as the
server/app boundary mirror; the cross-language grammar needs a contract test
before any cut there — recorded, not cut.

### 3. Install: how Vibecrafted gets onto a machine

| Collision | Surface                                                                                                                                                                               | Competitor of                                       | Disagreement                                                                                                                                                                                                                                                                                                                                                                                           |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 🔥        | `scripts/vetcoders_install.py` (14091 LOC): tools/, skill projections, launchd, rc; `cmd_doctor:13113`, `cmd_uninstall:13598`                                                         | `AppDelegate.swift:230-431 installCanonicalRuntime` | DMG hydrates `releases/<gen>` + launchers and **does not** project skills, install MCP or the server site; installer does. The first clean run on 2026-08-23 proved the DMG alone leaves 37/38 skills missing and the server in permanent backoff (site path computed by the installer's layout). Fixes live on `feat/dmg-first-run`, `foundations/fail-closed` (fork f3cc3c, 2026-08-23) — not merged |
| ⚠         | `install.sh` (1410 LOC) · `scripts/install-runtime.sh` (466) · `scripts/install-foundations.sh` (1187) · `scripts/installer_gui.py` (2788) · `scripts/installer/vetcoders_installer/` | each other                                          | five entry points; `usage`, `die`, `detect_platform`, `is_interactive`, `bundled_bin_root`, `start_here_path` defined 2–3× with differing bodies                                                                                                                                                                                                                                                       |
| ⚠         | deck `cmd_doctor:4727` / `cmd_uninstall:5046`                                                                                                                                         | `vetcoders_install.py`                              | deck forwards, installer owns — acceptable only while the deck is a pure forwarder                                                                                                                                                                                                                                                                                                                     |

Decision: shell roots consolidated in §1 tonight. The install-layer split
(installer vs DMG) is the fork's open cut; this journal records it so the
integrator does not start a third one.

### 4. Shell libraries: two `lib/` trees

| Collision | A                                            | B                                         | Disagreement                                                                                                                                                          |
| --------- | -------------------------------------------- | ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 🔥        | `runtime/scripts/lib/vc_frame.sh` (1091 LOC) | `runtime/shell/lib/vc_frame.sh` (880 LOC) | 1553 differing lines; both drive vc-frame sessions — one from the spawn launcher, one from the operator shell                                                         |
| 🔥        | `runtime/scripts/lib/frontier.sh` (95)       | `runtime/shell/lib/frontier.sh` (128)     | 143 differing lines                                                                                                                                                   |
| ⚠         | `runtime/scripts/lib/ulimits.sh` (82)        | `runtime/shell/lib/ulimits.sh` (24)       |                                                                                                                                                                       |
| ⚠         | `runtime/scripts/lib/*` 3561 LOC total       | `runtime/shell/lib/*` 4900 LOC total      | `docs/launcher-migration.md` declares Python the target owner of spawn; bash "stays as compatibility" — the compatibility layer is now larger than the thing it wraps |

Decision: too coupled to TTY behaviour for a night cut without a live
vc-frame verifier. Recorded with numbers; next cut after §1–§2 land.

### 5. Small Python twins (one owner each)

| Symbol                                       | Definitions                                                                  | Throne                                             |
| -------------------------------------------- | ---------------------------------------------------------------------------- | -------------------------------------------------- |
| `utc_now`                                    | `compact_hooks.py:22` (str), `cron.py:32` (**datetime**), `loop.py:20` (str) | three spellings, two return types                  |
| `default_state_file`                         | `cron.py:98` (requires root), `loop.py:40` (optional root)                   | loop owns the operator-loop state file             |
| `repo_root`                                  | `loop.py:25` (git toplevel), `wrappers.py:35` (`Path.cwd()`)                 | different answers for the same name                |
| `deck_path`                                  | `package_resources.py:42`, `wrappers.py:44` (forwarder)                      | package_resources                                  |
| `DoctorReport`/`DoctorError`/`diagnose_file` | `delivery/doctor.py`, `dispatch/doctor.py`                                   | different domains, same shape — namesake, recorded |
| `ProbeResult`/`_now_iso`/`_default_runner`   | `capabilities.py`, `continuity/capabilities.py`                              | foundations vs providers — namesake, recorded      |

### 6. Agent orientation: who am I, where am I, what is hot

Not a code twin — an absence. The things an agent needed on 2026-08-23 13:35
to not dispatch a duplicate of the fork's work were all on disk or on origin:
`~/.vibecrafted/reports/vibecrafted-first-run-4.2.4-2026-08-23.md`, four
remote-only branches over `main`, PR #70 stacked on #69, 16 orphan worktrees
(11 of them already squash-merged via #55 on 2026-08-19 and invisible to
`git cherry`). None of `vc-init`, the resume pack, `vc-git`, or the context
atlas surfaced any of it. The atlas core card (`00-core-map.md`) lists
`loct find vis` as a safe next command.

Decision: the canary role catalog becomes an input to the orientation pack
(single canonical file in this directory, refreshed by canary, read by init)
— after the ownership cuts, not before.

### 7. Liveness and orphans: no integrator

`git worktree list` on 2026-08-23: 17 worktrees, 13 of them for branches
whose code is already in `main` (squash #55, #64); one real loss
(`cut/fp-C5` → `3bf9c8af` scaffold-doctor "execute verifiers or refuse");
one open PR (#66) and its superseded twin (#65). Nobody owns "merge landed →
close its worktrees". `dispatch.sh` still creates worktrees by default
(fork left it).

Decision: operator buttons listed in the session report (deletion is theirs).
The integrator is a product feature, not a cut — recorded as the next throne.

## What competes daily (shortest true list)

1. `runtime_paths.py` ↔ `dispatch/worktrees.py` ↔ `install-foundations.sh` ↔ `install.sh` ↔ `install-runtime.sh` ↔ `migrate_agents_workspace.sh` (roots)
2. `control_plane.py` ↔ `scripts/control_plane_state.py` ↔ `read.rs`/`model.rs` ↔ `state.rs` (liveness)
3. `vetcoders_install.py` ↔ `AppDelegate.installCanonicalRuntime` (install layers)
4. `runtime/scripts/lib` ↔ `runtime/shell/lib` (shell)

## Cause

No single `RuntimeRoots` contract that every language and every script reads
from one place, and no single owner of "this run / this worktree / this
install is alive". Each subsystem keeps a partial copy; each copy is
reasonable; the machine looks healthy only while the last reinstall's residue
papers over the gaps.

## Phase II — cuts (appended as they land)

### Cut 1 — runtime roots (commits 173c88d9, 84c093bf, c6571a9f)

Throne: `vibecrafted_core.runtime_paths` (Python), `scripts/lib/runtime-roots.sh`
(shell). Removed:

| Competitor                                    | LOC | What it did wrong                                                                                                                      |
| --------------------------------------------- | --: | -------------------------------------------------------------------------------------------------------------------------------------- |
| `dispatch/worktrees.py:vibecrafted_home`      |   3 | empty `VIBECRAFTED_HOME` → cwd                                                                                                         |
| `scripts/install-foundations.sh` root block   |  84 | `VIBECRAFTED_ROOT/.vibecrafted` as a home (the release generation)                                                                     |
| `scripts/install-runtime.sh:vibecrafted_home` |   7 | third spelling                                                                                                                         |
| `scripts/migrate_agents_workspace.sh`         | 229 | `.ai-agents/` migration from March with its own `VIBECRAFTED_ROOT` meaning; Makefile `migrate`/`migrate-dry` and its test went with it |
| `install.sh` own copies of 7 root functions   |  85 | now a verbatim embed of the library between markers                                                                                    |

Added: `tests/tui/test_runtime_roots_parity.py` — the bootstrap copy is pinned
byte-for-byte to the library; library and bootstrap resolve identical roots
for four env shapes (`VIBECRAFTED_ROOT` never a home prefix); the contract
fails closed on drift with the one-line `✗ … drift` + `→ fix` wording from
`docs/CLI_PRODUCT_SPEC.md`.

Still two by necessity (recorded, not cut): `read.rs:vibecrafted_home` (Rust
reader), `plugins/iterm2/…/vc_launcher.py:vibecrafted_home` (AutoLaunch
sandbox), `deck/vibecrafted:33-44` (bash, shipped without `scripts/lib`),
`tui-agent/src/config.rs:default_vibecrafted_home`. One per language boundary,
each documented against the same env grammar; a cross-language contract test
is the next proof.

### Cut 2 — control-plane entry (commits a2469bb0, 6ef2386b, 6d39bb01)

Throne: `python -m vibecrafted_core.control_plane`. Removed:

| Competitor                                                                                        | LOC | What it did wrong                                                                           |
| ------------------------------------------------------------------------------------------------- | --: | ------------------------------------------------------------------------------------------- |
| `scripts/control_plane_state.py`                                                                  |  53 | loaded the throne, then rebound `vibecrafted_home` between two module objects on every call |
| `scripts/control_plane_launch.py`                                                                 |  27 | forwarder for `workflow`                                                                    |
| `scripts/runtime_paths.py`                                                                        |  34 | executed the canonical file under a second module name                                      |
| `deck/vibecrafted:_control_plane_script`                                                          |  12 | three-candidate file-path chain choosing which copy syncs                                   |
| `runtime/scripts/lib/meta.sh:spawn_control_plane_script`                                          |  15 | same chain, `VIBECRAFTED_ROOT` first                                                        |
| `tests/tui/test_meta_lifecycle.py::test_control_plane_script_prefers_explicit_root_then_checkout` |  30 | pinned the chain                                                                            |

`vetcoders_install.py` runs on the host's python3 before the product
interpreter exists and `vibecrafted_core/__init__` needs 3.11+, so it executes
`runtime_paths.py` from its file — one definition, not imported through the
package. `installer_gui.py` imports the package directly.

Found on the way: every `tests/tui` test copied `os.environ`, so on an
operator machine `VIBECRAFTED_RUNTIME_HOME`/`…_PYTHON` from the live launcher
pointed the deck at the real runtime and the step under test silently did
nothing. `conftest.py` now deletes the eight root variables for every test.

### Cut 3 — the clock (commit: see `feat(core): one clock`)

Throne: `vibecrafted_core/clock.py`. Removed eleven readers of the wall
clock: `utc_now` ×3, `_now_iso` ×7, and `control_plane._now`'s own
`datetime.now` (kept as the injectable seam tests patch, delegating to the
clock). `lifecycle_control` stamped receipts in **local time with a numeric
offset**; it is UTC now. Also: `cron.default_state_file` (duplicate of
`loop`'s), `wrappers.deck_path` (forwarder).

### Measured after the cuts

`loct prism` over four framings (roots · control-plane sync · install ·
timestamps): **13/15**, union 60 files, 16 shared by all four, mean Jaccard
0.463 — band "doctrine". The number is the baseline for the next cuts; it has
to fall.

### Not cut tonight, by name

1. `runtime/scripts/lib` ↔ `runtime/shell/lib` — two `vc_frame.sh` (1091/880
   LOC, 1553 differing lines). Needs a live vc-frame verifier.
2. `control_plane.py` ↔ `control-core/src/{read,model}.rs` — reader/writer
   grammar parity by convention. Needs a cross-language contract test on the
   on-disk state before either side loses anything.
3. `vetcoders_install.py` ↔ `AppDelegate.installCanonicalRuntime` — the fork's
   open cut (`feat/dmg-first-run`, `foundations/fail-closed`).
4. ~~`wrappers.repo_root` ↔ `loop.repo_root`~~ — renamed `invocation_root`
   (955e24b0); `loop.repo_root` is the only `repo_root`.
5. `delivery/doctor.py` ↔ `dispatch/doctor.py`, `capabilities.py` ↔
   `continuity/capabilities.py` — namesakes across domains.
6. The integrator (§7) and the orientation pack (§6) — product features, next.

### Gates (2026-08-23, after 0b508a5e)

`make test-core` 1668 passed / 12 skipped · `make test` 1294 passed / 24 skipped /
1 failed (PL-mirror freshness, red since `de3a017b` — fixed in 0b508a5e, module
re-run 4 passed) · `make check` passed · pre-commit and pre-push green on every
commit · focused suites for each cut listed in the commit bodies.

---

## Phase III: the roots cut was not finished (2026-08-24, Codex audit)

Codex audited the twelve `[claude/canary]` commits under run
`work-260824-034006-84440` and returned **PR #70 BLOCKED**. Two of the three
blockers are mine. Recorded here because a cut without its failures written
down is a report, not a journal.

### P0 — the throne cut left two callers behind

`6ef2386b` deleted `scripts/runtime_paths.py` as a competitor of
`vibecrafted_core.runtime_paths`. Two Make expressions still import it:

| Line           | Expression                                                                                                                           |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `Makefile:242` | `"vc-frame config"` step: `$(PYTHON) -c 'sys.path.insert(0, "$(SOURCE)/scripts"); from runtime_paths import vibecrafted_tools_home'` |
| `Makefile:313` | `install-tools`: the same expression                                                                                                 |

Fresh installation and the skill-loader matrix fail. Nothing in this repo's
gates could see it: `make test` and `make check` never run `make install`.
Green gates, broken product — the DoU case in its purest form.

Worse, the test pinned the corpse. `tests/tui/test_makefile_installer_contract.py`
asserts the literal string `"from runtime_paths import vibecrafted_tools_home"
in install_block` — string truth, not execution truth. The assertion stayed
green precisely _because_ the dead import was still written down.

And the same file already carried a **third** way to compute the same root:
`Makefile:241` spells `${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools/vibecrafted-current`
literally. One power, three spellings, in one file.

**The throne for a Makefile is the shell library**, `scripts/lib/runtime-roots.sh`
:: `default_vibecrafted_tools_home` — not the Python module (which needs 3.11+,
while `$(PYTHON)` is `scripts/project-python`) and not a by-path loader, which
would be the sixth layer, not a fix. Reviving `scripts/runtime_paths.py` in any
form is forbidden: it is a competitor that was already cut.

Scope of the disease beyond this cut:
`loct find --regex '(XDG_DATA_HOME|runtime_paths|vibecrafted_tools_home|vibecrafted/tools)'`
= **283 occurrences across 99 files**. One power, ninety-nine addresses.

### P1 — the env scrub proved less than it claimed

`tests/tui/conftest.py` deletes the eight root variables, but the operator-mode
test reaches the host through `bash -lc`, which re-reads the login profile.
Locally 44/44 green; on a clean runner it fails. A local green that a clean
machine cannot reproduce is a false green, and I reported it as evidence.

### P1 inherited (not mine)

macOS portable still leaks `/Users/runner` — pre-existing, listed in
`dmg-leak-comes-from-prebuilt-wasm`.

### Verdict

The roots cut is **not finished**. It removed the competitors and left two
callers pointing at the grave. Recorded as an open cut, not as a closed one.
Plan with the falsification contract:
`~/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0824/plans/canary-install-contract-closure-v1`
(W0-01 repairs this without recreating the shim; the test must _execute_ both
Make expressions instead of spelling them).

### Host evidence (2026-08-24, operator machine after the DMG 4.2.4 reinstall)

"Which runtime is current" has **two owners**, and on this machine one of them
is a ghost:

| Owner                                                                                                                            | State on dragon                                                                                                     |
| -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `active.json` (`vibecrafted.active-runtime.v1`) → `releases/4.2.4+g8d041616`, wrappers in `~/.local/bin` hardcode the generation | **alive** — everything runs from here; `vibecrafted receipt` CLEAN                                                  |
| `tools/vibecrafted-current` (Makefile's "STABLE runtime home", uv-tool editable source, `install.toml`)                          | **does not exist** — `tools/` holds one empty `.vibecrafted-install.lock`; `uv tool list` has no vibecrafted at all |

So the P0 is deeper than the dead import: after W0-01 repairs the expression,
the `"vc-frame config"` and `install-tools` steps still aim at a root that a
DMG-installed machine never creates. Two grammars for one question — "where is
the current runtime" — and the machine works only because the capsule grammar
answers while the Makefile grammar points at a grave. This is the operator's
"another DMG reinstall and everything would work again" made concrete: reinstall
residue as the only thing standing between green and broken.

Belongs to the installer ↔ DMG power (§ Not cut, item 3 — the fork's cut).
W1-01's clean-runner rehearsal must decide the throne: either `active.json`
is the one answer and the Makefile consumes it, or `vibecrafted-current` is
real and the capsule maintains it. Not both.

Side receipts from the same probe: `scaffold-doctor` INSTALLED_NOT_ON_PATH;
`vc-frame` DIRTY_BUILD_PROVENANCE (0.47.3+g017e3839.dirty); `voc` absent.

---

## Phase IV: the transplant doctrine and the shell-library reframe (2026-08-24)

### Doctrine amendment (operator decision, 2026-08-24, morning)

The operator ruled — citing the Sol precedent from the Night of the French
Connection (9200 lines of an old engine under `git rm`, eleven minutes from
order to commit) — that demolition cuts are gated **structurally, not by
runtime greenness**:

1. Before the cut: freeze the oracle/RED (public failing evidence + prism
   baseline). Not after.
2. Demolition (W0-C) does not run builds or runtime falsifiers. The gate is
   negative and structural: the `git rm` manifest, zero old symbols/writers/
   routes by the Loctree map, no unlisted competitors, an adequate margin.
3. Dangling references and a red build are an allowed, documented state
   ("compile-driven salvage" is forbidden in demolition briefs — the rush to
   compile re-attaches the wrong falsifiers and risks leaving cancer or
   cutting without margin).
4. Only the rewire wave (W1) restores compilation and runtime tests. Machine
   assembly order: parts first, wiring after; structure verified A→Z with the
   Loctree map.

This retroactively corrects Phase II's caution: refusing the shell-library cut
"for lack of a live verifier" was compile-driven-salvage thinking.

### The shell-library reframe (evidence, not the old thesis)

Phase I called `runtime/scripts/lib` ↔ `runtime/shell/lib` whole-tree twins.
The map says otherwise:

- The trees serve two surfaces: `scripts/lib` = spawn/launcher machinery
  (sourced by `runtime/scripts/common.sh`, consumed by the Python core;
  `launcher-migration.md` is actively draining it into Python — three
  responsibilities already moved). `shell/lib` = the operator-terminal
  facade (self-described: "sourced only by the compatibility facade";
  consumers: `vetcoders.sh`, `install-foundations.sh`,
  `sync-vc-alias-runtime.sh`, the vc-* verbs).
- `frontier.sh` ×2 is a **FALSE_PARALLEL**: same name, different powers
  (spawn shell selection vs frontier config resolution). Rename candidate,
  not a cut.
- `ulimits.sh` is already resolved by delegation — but through a four-path
  candidate chain (`shell/lib/ulimits.sh:9-12`), the same path-chain disease
  cut from the deck in Phase II. Residual.
- `vc_frame.sh` ×2 is the real patient: 1091 vs 880 lines, diff 1735,
  **zero shared function names** (30 `spawn_*` vs 31 `_vetcoders_*`). Not
  copies — two grammars for one power ("talk to vc-frame sessions/tabs"),
  with the twinning documented inside: `shell/lib/vc_frame.sh:330` "G7 twin
  of spawn_effective_operator_session", `:641` "G3 + G3b twin of
  spawn_vc_frame_session_action", `:810` names the split (operator seat =
  facade; skill workers = scripts/lib `spawn_launch`, G7 per-project host).

Classification: ⚠ same responsibility in two modes with deliberately paired
sub-behaviours (G3/G7). The demolition manifest is therefore **symbol-level,
not file-level**: for each G-pair one side becomes the only implementation
and the other calls it; the pair comments and the four-path chains go to
zero. Oracle first: golden tests for G3 (session action + stderr
disambiguation) and G7 (worker/operator session naming) — both currently
exist only as comments pointing at each other.

### Corrections issued to the install-contract plan (in komityw with Codex)

`canary-install-contract-closure-v1` gets two operator-mandated corrections
(appended to the briefs, attributed): W0-01 must not presume
`tools/vibecrafted-current` exists on end-user machines (host evidence above)
and must fail closed with the one-line contract error, not compute a path to
a grave; W1-01 must decide the single owner of "which runtime is current"
(`active.json` vs `vibecrafted-current`) and produce the loser's demolition
manifest. Dispatch of W0-01 ∥ W0-02 is delegated to me by the operator
("niczego nie będę robił, po to was mam"); the ⛔ STOP (push/merge/release)
stays the operator's.

### Consumer contract points (operator ask, 2026-08-24; evidence-checked)

Fixed reference frame for every root/install cut — the consumer's seat, not
the maintainer's:

**Linux (tarball consumer)** — "the tarball works with no dump":

- L1. Clean-machine truth: `tar xf` + install on a fresh Debian/Alpine(musl)
  runner with no macOS-isms — no `xcrun`, no BSD `stat -f`/`sed -i ''`.
  (Memory: the release gate has never executed its last step on either OS —
  xcrun killed it on Linux, missing ripgrep on macos-15.)
- L2. XDG grammar honored end-to-end: everything lands under
  `${XDG_DATA_HOME:-~/.local/share}/vibecrafted` + `~/.local/bin`, and the
  launchers are reachable from a bare `$PATH` without any GUI or capsule.
- L3. **The throne must have a Linux writer.** `active.json` is written today
  only by `AppDelegate.swift` (macOS DMG). If W1-01 crowns `active.json`,
  the tarball/Makefile install must write it too; if it crowns
  `vibecrafted-current`, the capsule must maintain it. A throne only one OS
  can write is a competitor factory.
- L4. Payload hygiene on the tarball equal to the DMG (`/Users/runner` leak
  class), and binaries for gnu **and musl** (the lost-musl-target failure
  from the Loctree worktrees must not repeat here).

**Windows consumer** — "everything lands in their paths and is reachable":

- W1. Production truth today: zero Windows path grammar (`APPDATA`/
  `LOCALAPPDATA`/`USERPROFILE` — 28 regex hits, all docs/tests). Any claim of
  Windows support is currently false; docs must say WSL2 explicitly or say
  nothing.
- W2. When native lands: data/runtime → `%LOCALAPPDATA%\vibecrafted`, config
  → `%APPDATA%\vibecrafted`, launchers as `.cmd`/`.exe` on the user PATH —
  `~/.local/bin` bash wrappers are unreachable there, and the whole bash
  library layer does not exist. Which is one more proof for the
  launcher-migration doctrine: Python/Rust must carry spawn alone.
- W3. Interim honest path: WSL2 = the Linux tarball contract (L1–L4) plus a
  documented `\\wsl$` reachability note. No pretending beyond that.

These points bind W1-01 (rehearsal + throne decision) and every later root
cut. A cut that satisfies the maintainer and fails L3 or W1 is not finished.
