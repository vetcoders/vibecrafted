# Vibecrafted 4.2.0 roadmap — measured truths, finished seams

Status: planned (scaffolded 2026-08-18). Not part of the 4.1.0 release contract.

Plan package (atlas · falsification · tracker · DRIVER · 9 briefs · manifest):
`/Users/polyversai/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0818/plans/roadmap-4.2.0/`
Drive it from `DRIVER.md` there; this file is the repo-facing summary.

## Thesis

4.1.0 shipped two channels (DMG + portable) and a durable Workspace identity, but
several truths are still asserted rather than measured, and three product seams
are visibly unfinished. 4.2.0 turns each into a verifier-earned `[x]` or an honest
`[?]`. Only a delivery-verifier flips `[~]→[x]`.

## Waves

| Wave | Cut  | Title                                           | Vector    | Repo                   |
| ---- | ---- | ----------------------------------------------- | --------- | ---------------------- |
| W0   | W0-a | Verify 4.1.0 payloads symlink/.env/HOME-free    | recon     | vibecrafted            |
| W0   | W0-b | `resume --run-id` e2e on the installed build    | e2e       | vibecrafted            |
| W0   | W0-c | LIVE RUNS dashboard runtime acceptance          | e2e       | vibecrafted            |
| W1   | W1-a | Symlink-free tree: guard + Windows-clone smoke  | stabilize | vibecrafted            |
| W1   | W1-b | Donor snapshots as a release feature            | implement | vibecrafted            |
| W1   | W1-c | Serve `install.ps1`                             | implement | vibecrafted-io         |
| W2   | W2-a | Workspaces surface in the vc-frame session rail | implement | vc-frame               |
| W2   | W2-b | Vibecrafted.app boundary + chrome polish        | implement | vibecrafted + vc-frame |
| W3   | W3-a | Core `__init__` import direction                | stabilize | vibecrafted            |

Order: W0 (parallel, read-only) → W1 (parallel, disjoint files) → W2 (parallel) →
W3 (after W1-a). Every wave ends at an operator button (merge / deploy / install).

## Decisions

1. Repo tree is symlink-free (landed in #47); a contract test + Windows-clone smoke guard it; projections are produced by installer/packers, never linked.
2. Dirty donors are a release feature (`--snapshot-donors`), not an operator ritual.
3. Windows gets a served entry point (`/install.ps1`, WSL2 hand-off), not a native install.
4. vc-frame shows Workspaces (catalog, `workspace_id`), not physical session names.
5. Runtime acceptance on the installed build is a cut (W0), not a footnote.

## Implement stage — what landed, 2026-08-18

Stage `implement` ran as a single worker (no fleet), so every cut below carries the
executing agent's own authorship, not the brief's planned assignee.

| Cut  | State | Landed SHA(s)          | Measured result                                                                                                                                                                                                                           |
| ---- | ----- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| W0-a | `[!]` | recon only, no commit  | Portable payload clean (0 symlinks, 0 `.env`, no operator identity). **DMG is not:** `Contents/Helpers/vc-frame` and `Contents/MacOS/Vibecrafted` carry `/Users/<operator>/.cargo/...`, and four binaries carry the living checkout path. |
| W0-b | `[~]` | recon only, no commit  | Installed runtime `4.1.0+g237d2814` contains `18dea346`; `resume --run-id <missing>` fails loudly (exit 1, names the id). Live resume of a real run not exercised from a headless worker.                                                 |
| W0-c | `[!]` | recon only, no commit  | `catalog.json` present, schema valid, 48 workspaces — but **22 of 48 ids are UUIDv4, including this repo's** (`bda366e0-…-45f1-…`). The plan's UUIDv7 premise is false.                                                                   |
| W1-a | `[~]` | `838165d6`             | Guard added; the clone smoke found a real break and fixed it (see below).                                                                                                                                                                 |
| W1-b | `[~]` | `cd13e1ca`             | `--snapshot-donors` + reaper; proved on the real donors, failure path included.                                                                                                                                                           |
| W1-c | `[~]` | `e9f47da1`             | Parity guarded. The 404 is a stale deploy branch, not a pipeline limit.                                                                                                                                                                   |
| W2-a | `[ ]` | —                      | Not implemented; premise falsified (see W0-c) and acceptance is GUI-only.                                                                                                                                                                 |
| W2-b | `[~]` | —                      | Chrome asks already landed in vc-frame `76048ca54`; the menu question is answered below.                                                                                                                                                  |
| W3-a | `[~]` | `01e5e18a`, `4918c7fb` | Import cycles 4 → **0**; loctree health 74 → **80**.                                                                                                                                                                                      |

### Measured findings that changed the plan

1. **`docs/install.sh` died with exit 126 on every fresh clone.** After #47 it became a
   real shim that `exec`s `../install.sh`, but the repository file carries no executable
   bit by design — `scripts/build-portable-release.sh:86-88` states exactly that. The shim
   now execs `bash` explicitly.
2. **The 4.1.0 DMG leaks operator paths.** `--remap-path-prefix` ran (`/usr/src/operator-home`
   is present) yet `/usr/src/vc-frame` and `/usr/src/vc-terminal` are present in _no_ binary:
   the donor prefixes were built as `"$REPO_ROOT/../vc-frame"`, and a prefix containing `..`
   never matches textually. Fixed in `cd13e1ca`; the remaining `.cargo/registry` leak in
   `Contents/Helpers/vc-frame` is unexplained and needs its own cut.
3. **`keychain-session.sh` silently dropped the caller's cleanup on any failed release.**
   `_ks_trap_cleanup` returns the triggering status by design, and under `set -e` a non-zero
   command inside a trap tears the shell down before the chained handler runs. Measured on a
   real failed release; fixed in `cd13e1ca`.
4. **`vibecrafted.io` is serving 3.7.0.** `https://vibecrafted.io/VERSION` answers `3.7.0`
   and the served `install.sh` is the 3.7.0-era script. The site repo's deploy branch
   (`origin/main`) is at 2026-04-14; the 4.1.0 hydration and `install.ps1` live only on an
   unmerged branch. The `/install.ps1` 404 is one symptom of that, not the problem.
5. **"Open Console" is not a mislabelled terminal.** The tray menu already carries both
   `Open Console` (the Swift main window) and `Open vc-terminal`. Renaming the first would
   produce two terminal-sounding items. Kept; a clearer word than "Console" for the Swift
   window is a naming call for the operator.
6. **`session-manager` requests no plugin permissions at all** (`request_permission` appears
   only in `status-bar` and the test fixture), so W2-a cannot read the catalog from inside the
   WASM sandbox without a new consent prompt. The smaller blast radius is a host-side
   projection onto `SessionInfo` (`zellij-utils/src/data.rs:1824`).
7. **Five tests were already red before this stage** and stayed red: three
   `test_research_launcher.py` settle timeouts, `test_vibecrafted_launcher.py::test_dashboard_subcommand_launches_repo_owned_vc_frame_layout`,
   and `vibecrafted-core/tests/test_aicx_session_chain.py::test_resume_pack_never_selects_native_even_with_same_agent`.
   All four/one confirmed against a clean `HEAD`.

## Explicit non-goals

Native Windows runtime · a second control plane · new vc-frame features beyond the
rail and the 2026-08-16 chrome asks · rewriting the release scripts · merges into trunk,
deploys, or host installs performed by an agent (branch pushes and PR creation are the supervisor's; canonical list: vc-operator/AUTONOMY.md).
