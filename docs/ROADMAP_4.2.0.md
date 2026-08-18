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

| Cut  | State | Landed SHA(s)          | Measured result                                                                                                                                                                                                               |
| ---- | ----- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| W0-a | `[!]` | recon only, no commit  | Portable payload clean of the operator's **account**; it still carried the **checkout root** in 5 tracked files. **DMG is not clean:** 8 of 2955 files name the build host. Both addressed in the workflow stage — see below. |
| W0-b | `[~]` | recon only, no commit  | Installed runtime `4.1.0+g237d2814` contains `18dea346`; `resume --run-id <missing>` fails loudly (exit 1, names the id). Live resume of a real run not exercised from a headless worker.                                     |
| W0-c | `[!]` | recon only, no commit  | `catalog.json` present, schema valid, 48 workspaces — but **22 of 48 ids are UUIDv4, including this repo's** (`bda366e0-…-45f1-…`). The plan's UUIDv7 premise is false.                                                       |
| W1-a | `[~]` | `838165d6`             | Guard added; the clone smoke found a real break and fixed it (see below).                                                                                                                                                     |
| W1-b | `[~]` | `cd13e1ca`             | `--snapshot-donors` + reaper; proved on the real donors, failure path included.                                                                                                                                               |
| W1-c | `[~]` | `e9f47da1`             | Parity guarded. The 404 is a stale deploy branch, not a pipeline limit.                                                                                                                                                       |
| W2-a | `[ ]` | —                      | Not implemented; premise falsified (see W0-c) and acceptance is GUI-only.                                                                                                                                                     |
| W2-b | `[~]` | —                      | Chrome asks already landed in vc-frame `76048ca54`; the menu question is answered below.                                                                                                                                      |
| W3-a | `[~]` | `01e5e18a`, `4918c7fb` | Import cycles 4 → **0**; loctree health 74 → **80**.                                                                                                                                                                          |

### Measured findings that changed the plan

1. **`docs/install.sh` died with exit 126 on every fresh clone.** After #47 it became a
   real shim that `exec`s `../install.sh`, but the repository file carries no executable
   bit by design — `scripts/build-portable-release.sh:86-88` states exactly that. The shim
   now execs `bash` explicitly.
2. **The 4.1.0 DMG leaks operator paths.** `--remap-path-prefix` ran (`/usr/src/operator-home`
   is present) yet `/usr/src/vc-frame` and `/usr/src/vc-terminal` are present in _no_ binary:
   the donor prefixes were built as `"$REPO_ROOT/../vc-frame"`, and a prefix containing `..`
   never matches textually. Fixed in `cd13e1ca`. **The rest is explained as of the workflow
   stage** — see "Workflow stage" below. It was never one leak.
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
   All four/one confirmed against a clean `HEAD`. The dashboard one is green as of the
   workflow stage; the three research timeouts are diagnosed there and remain red.

## Workflow stage — what landed, 2026-08-18

Stage `workflow` consumed the review report (`revi-260818-160222-61808`) and its
Before-Merge TODO. Three commits: `85cebab5`, `f08e8076`, and the one carrying this
section.

### W0-a is now a mechanism, not a mystery

The review attributed the surviving `.cargo/registry` leak to vc-frame's git-tracked
WASM blobs. Measuring the shipped `Vibecrafted_4.1.0-20260817-237d2814.dmg` file by file
found **five** producers, of which `--remap-path-prefix` reaches exactly one:

| Where                                                            |             Count | Producer                                                 | Lever                          |
| ---------------------------------------------------------------- | ----------------: | -------------------------------------------------------- | ------------------------------ |
| `Contents/Helpers/vc-frame`                                      |     411 × `$HOME` | git-tracked `assets/plugins/*.wasm` via `include_bytes!` | rebuild the plugins            |
| `Contents/Helpers/vc-frame`                                      |     17 × checkout | same blobs                                               | same                           |
| `Contents/Helpers/vc-terminal.app/…/alacritty`                   |  277 × donor root | the `..` prefix bug                                      | `canonical_dir`, already fixed |
| `Contents/MacOS/Vibecrafted`                                     |      21 × `$HOME` | cc-rs C sources (`ring`)                                 | `CFLAGS=-ffile-prefix-map`     |
| `Contents/MacOS/Vibecrafted`                                     |     51 × checkout | Swift sources + xcodebuild DerivedData                   | `-debug-prefix-map`            |
| `runtime/python/lib/python3.12/_sysconfigdata__darwin_darwin.py` |     27 × checkout | uv's CPython recording its seed prefix                   | rewrite the literal            |
| `runtime/python-site/bin/jsonschema`                             |      1 × checkout | pip console-script shebang                               | delete the directory           |
| `Contents/MacOS/voc`, `Contents/MacOS/vc-mux-daemon`             | 1 × checkout each | `env!("CARGO_MANIFEST_DIR")` probed at runtime           | `#[cfg(debug_assertions)]`     |

Two of these are worse than leaks:

- `runtime/python-site/bin/*` shipped **scripts that cannot run** — their shebang names a
  `mktemp` directory the build deletes on its way out. Nothing invokes them (python-site
  is on `PYTHONPATH`, never on `PATH`), so the directory is now removed outright.
- `default_command_deck()` and `find_tray_icon()` probed the build machine's checkout at
  runtime, and on that machine the path **exists**. The shipped binary therefore preferred
  the developer's living checkout — on the one machine where a release gets walked around
  before it goes out.

### The primary defence is a gate, not another flag

Five producers with five different levers, and the set grows with every new kind of
bundled artifact. `scripts/payload_hygiene.py` reads the finished payload and knows
nothing about how it was made; both release channels run it before they sign or publish,
and `make payload-hygiene ARTIFACT=<path>` asks the same question of anything already on
disk (`.app`, `.dmg`, `.tar.gz`). No allowlist. 2955 files in 1.7 s.

Its first run found its own weakness: without the donor roots it certified a payload that
carries 277 occurrences of the vc-terminal donor path. A test pins that now.

### Measured, closed-loop

- **Plugin rebuild.** All 14 blobs rebuilt inside a real donor snapshot under the release
  remaps: `$HOME` 276 → **0**, checkout root 14 → **0**. 60 s. The snapshot's resulting
  dirty set is exactly `zellij-utils/assets/plugins/`, which is the whole of the new
  `require_clean_repo` allowance. Donor restored: 2 worktrees before and after, 8 stashes
  untouched, `git status` clean.
- **Remap precedence.** rustc applies the **last** matching `--remap-path-prefix`
  (measured with two overlapping prefixes). `$HOME` was last, so on any host whose
  checkout lives under `$HOME` every specific root was dead. Order is now broadest-first.
- **Keychain harness.** `run_child` now runs `set -euo pipefail`; all 61 existing cases
  stay green, which is the finding — the suite was blind, not wrong. Two cases added for
  the missing cell. Reverting `cd13e1ca` turns exactly one case red: the new one.
- **Barrel self-cycle.** Reproducing ruff PLR0402's rewrite turns the new AST test red at
  the exact line, and `loct audit` agrees (`structural: 1`, node `__init__.py`).
- **RELEASE_FLAGS injection.** The vector is command substitution, not `;` — a `;` lands
  after `exec` and never runs, while `$(...)` is evaluated while zsh builds the argv.
  Both are inert now.

### Corrections to the review

- **P2-05 does not survive measurement.** `VIBECRAFTED_HOME` was already redirected to
  `tmp_path`; the newest `rese-*` in the operator's real control plane is from 2026-08-13,
  not from any test run. The three reds are a **product hang**: the dispatcher blocks in
  `wait4` and its `workflow_runtime research` child blocks in the asyncio `kevent` loop,
  with no timeout, both reparented to init and alive six minutes later. The stage fixed
  the damage (the timeout path now reaps the recorded pgid — 0 orphans after a run, was 6)
  and reports the hang rather than smuggling a supervisor rewrite into a review-fix stage.
- **P3-05 declined, with a reason.** Setting `CARGO_TARGET_DIR` to survive the reaper would
  break vc-frame's own asset producer: `scripts/plugins-parity.zsh` hardcodes
  `$REPO/target/wasm32-wasip1/release`. That is a vc-frame change and belongs to a cut that
  can verify it there.

### Not verified

- The **Swift** and **cc-rs** prefix maps are wired but need a full signed release to
  confirm. The payload gate is what makes that non-optional: the next release fails loudly
  if they did not work.
- The plugin rebuild is proven on the blobs and on the snapshot's dirty set, **not** end to
  end through a complete `--snapshot-donors` release (cold cargo ×2, signing, notarization).
- Nothing in this stage was pushed, merged, tagged or deployed.

## Polarize stage — the one truth, 2026-08-18

The audit named three candidate axes. Only one of them was a live contradiction
_inside this repository_, so only one was cut.

**Chosen axis — `workspace_id` is minted as UUIDv7 and accepted as any canonical
UUID.** No consumer may validate, filter, or sort on the version.

The runtime already implemented this rule and was never wrong: `new_uuid7()` is
the single minting point, `require_uuid()` the single acceptance chokepoint
(fan-in 6, 35 callsites), and it is version-agnostic by construction. What
disagreed was the prose. `docs/runtime/WORKSPACE_IDENTITY.md` — the wire
contract a vc-frame Cut B reader consumes — declared the _kind_ of the three id
fields to be `UUIDv7`, and the `workspace_catalog` module docstring said the
same. Read as a validation rule, that prose is what turns W2-a into the top-2
risk of this flight.

Measured this stage, against the live catalog: **57 workspaces, 35 UUIDv7 and 22
UUIDv4** — and this repository's own entry is
`bda366e0-519f-45f1-8d10-449058491a94`, **version 4**. A v7-only rail drops
Vibecrafted from its own dashboard.

**Rejected alternatives.**

- _Migrate the v4 ids to v7 so the doc becomes true._ Rejected: the catalog is
  the sole durable identity store. Rewriting durable ids to satisfy a sentence
  invalidates every projection keyed on them, for cosmetic gain.
- _Mint v7 **and** validate v7 (the "average" of the two axes)._ Rejected — this
  is the exact shape of the failure. Averaging two viable-looking rules here
  produces the bug.
- _Cut the cycle-gate axis (`loct audit --json` vs `loct follow` diamonds)._
  Rejected as out of scope for a repo cut: `loct follow` appears nowhere in this
  tree as a gate. That conflict lives between two sentences of the W3-a brief,
  not between two surfaces of the product. `loct audit --json` stands as the
  instrument.
- _Cut the release-scope axis (4.2.0 = integrity spine, W2 deferred)._ Rejected
  as **not an agent's call**: whether 4.2.0 waits for W2 is an operator button,
  and the audit files it under "needs a human".

**Aligned surfaces.** `docs/runtime/WORKSPACE_IDENTITY.md` (identity table now
reads `UUID`, plus an explicit _Accepted id rule_ section) · the
`workspace_catalog` module docstring · two regression tests in
`vibecrafted-core/tests/test_workspace_catalog.py` that put a real legacy v4 id
through create → show → select → list and assert list order comes from
`created_at`, never from the id bits.

**Proof the gate can fail.** `is_uuid` was mutated to require version 7; both new
tests went red with `workspace_id must be a canonical UUID`, and green again on
restore. The guard reproduces the exact regression it exists to stop.

**Unblocked by this cut.** W2-a may now implement the host-side `SessionInfo`
projection against a stated, tested rule. It remains unimplemented.

## Explicit non-goals

Native Windows runtime · a second control plane · new vc-frame features beyond the
rail and the 2026-08-16 chrome asks · rewriting the release scripts · merges into trunk,
deploys, or host installs performed by an agent (branch pushes and PR creation are the supervisor's; canonical list: vc-operator/AUTONOMY.md).
