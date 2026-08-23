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
4. `wrappers.repo_root` (invocation cwd) ↔ `loop.repo_root` (git toplevel) —
   two meanings under one name; rename is the honest fix, tests patch the
   former.
5. `delivery/doctor.py` ↔ `dispatch/doctor.py`, `capabilities.py` ↔
   `continuity/capabilities.py` — namesakes across domains.
6. The integrator (§7) and the orientation pack (§6) — product features, next.
