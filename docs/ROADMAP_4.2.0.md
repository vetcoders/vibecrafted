# Vibecrafted 4.2.0 roadmap — measured truths, finished seams

Status: planned (scaffolded 2026-08-18). Not part of the 4.1.0 release contract.

Plan package (atlas · falsification · tracker · DRIVER · 9 briefs · manifest):
`~/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0818/plans/roadmap-4.2.0/`
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

## Polarize stage, loop 2 — the installed owner, 2026-08-18

**Chosen axis — an installed owner is where the launcher _lands_, not what its
directory is _named_. `Vibecrafted.app` is a first-class installed owner.**

`vibecrafted doctor` graded three subsystems against one binary and got three
different answers on this host:

| Subsystem                               | Verdict on `~/.local/bin/vibecrafted`                              |
| --------------------------------------- | ------------------------------------------------------------------ |
| `_launcher_shim_findings`               | `fail` — "checkout/legacy bash deck … Reinstall"                   |
| `server_supervisor` (via `active.json`) | authoritative generation for the launchd plist                     |
| delivery receipt                        | `[CLEAN]`, `installed: …/releases/4.1.0+g237d2814/bin/vibecrafted` |

The cause is not two _ages_ of one install — it is two _layouts_. `make install`
stages `tools/vibecrafted-generation-*` behind the `vibecrafted-current`
symlink; `Vibecrafted.app` (`AppDelegate.swift`) publishes
`releases/<version>/` and writes `~/.local/bin` wrappers that `exec` into it.
The doctor recognised only two owners — a uv-tool Python shim, and a bash deck
whose _path string_ contains `vibecrafted-generation-`. The app's own install
matched neither, so the shipped product was told to "reinstall so an installed
owner wins PATH" — advice that reproduces the identical layout and can never be
satisfied.

Measured, not assumed: `releases/4.1.0+g237d2814/bin/vibecrafted` is a 203 KB
regular file, `resolve()` stays inside itself, and **0** symlinks under that
root escape to the checkout. It is an installed runtime by every property the
check claims to care about.

The repository's own documentation already stated the correct rule. The
"Checkout-free gate" section of `docs/runtime/INSTALLED_RUNTIME_CAPSULE.md`
says doctor fails "when the public launcher resolves outside
`~/.local/share/vibecrafted`" — containment, not naming. The code implemented a
narrower rule than the doc it was written against, and the doc's _opening_
paragraph had since drifted the other way ("enter **only** … `vibecrafted-current`").
This cut restores one rule and makes both surfaces state it.

**Rejected alternatives**

| Rejected                                                                                                             | Why it loses                                                                                                                                                                                                                                                    |
| -------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **The capsule layout is the only installed owner; the app must be repackaged into `tools/vibecrafted-generation-*`** | That is a packaging rewrite of the shipped product to satisfy a string test. The app already audits every runtime entry for executability and refuses symlinks before publishing; it is not less installed for choosing a different directory.                  |
| **The app channel wins and the capsule is legacy**                                                                   | Refuted on this host: `tools/vibecrafted-current` is live at `4.1.0+ga7f262d9`, `make install` still writes it, and the generation manifest's digest closure has no counterpart in the app channel. Declaring it legacy would delete a real integrity boundary. |
| **Leave the `fail` and let operators reinstall**                                                                     | The instruction is false. Reinstalling from the app reproduces byte-for-byte the layout being rejected. A gate whose remedy cannot satisfy it is noise that trains operators to ignore doctor.                                                                  |
| **Unify the two pointers (`active.json` and `vibecrafted-current`) into one**                                        | The right end state, and out of scope for a cut. It needs the app and the shell installer to agree on a write protocol — implement/marbles work. Polarize names the disagreement and makes doctor surface it; it does not invent the merged pointer.            |
| **Also re-point `__version__` resolution at the entered runtime root**                                               | Rewrites version resolution across `staged_tools_sync` (268 tests). The lie is closed by _reporting_ the disagreement, not by silently switching which generation answers.                                                                                      |

**Aligned surfaces**

- `vibecrafted_core/doctor.py` — ownership is containment in
  `$VIBECRAFTED_RUNTIME_HOME`, resolved either directly or through a wrapper's
  `exec` target. The target is believed only after `is_file()` + `X_OK` +
  `resolve(strict=True)`, so a wrapper cannot talk its way into a root it does
  not enter. New cross-check: the reported install identity is compared against
  the `VERSION` of the root the launcher actually enters, and a mismatch is a
  `warn` naming both.
- `docs/runtime/INSTALLED_RUNTIME_CAPSULE.md` — the opening paragraph now states
  the boundary and both channels instead of contradicting the app.
- `vibecrafted-core/tests/test_doctor.py` — five regression tests: the
  app-shaped wrapper is `ok`; a wrapper execing a checkout is `fail`; an `exec`
  target that does not exist is `fail`; the version cross-check warns on
  disagreement and stays quiet on agreement.

**Proof the guards bite.** Against the pre-cut doctor,
`test_launcher_shim_finding_ok_for_app_installed_release_launcher` fails
`assert 'fail' == 'ok'`. With the cross-check branch mutated to `elif False`,
`test_launcher_version_warns_…` fails `assert 'ok' == 'warn'`. Both green on
restore. On the live host the check flips `fail` → `ok` and raises the
previously invisible `warn: doctor resolves install identity 4.1.0+ga7f262d9,
but the PATH launcher enters …/releases/4.1.0+g237d2814`.

**What this does not fix.** One install channel still does not know about the
other. `make install` and the app both claim `~/.local/bin`; last writer wins.
Doctor now reports that instead of hiding it, which is the honest state — the
merge is a wave, not a cut.

## Polarize stage, loop 3 — one identity resolution order, 2026-08-18

**Chosen axis — every surface answers "which workspace is this process?" in the
same order: the exported `VIBECRAFTED_WORKSPACE_ID` first, then the one
canonical catalog by `canonical_root`, both arbitrated by that catalog.**

The audit filed the LIVE RUNS filter under "silent dashboard fallback". Measured
fresh this stage, it is not a fallback problem — it is a **twin**. The runtime
stamps a run through `resolve_run_workspace_identity`, whose first step is the
exported `VIBECRAFTED_WORKSPACE_ID`. The dashboard resolved its own identity
through `live_dashboard.resolve_workspace_id`, which read only the catalog by
root and never looked at the environment. Two implementations of one question,
free to disagree — and on this host they did.

**Measured, live, while this stage ran.** `VIBECRAFTED_WORKSPACE_ID` in the
flight's shell is `01a00d7b-3964-77a8-bc53-2f41e4b4e509`; the catalog roots that
workspace at a **pytest temp directory** that no longer exists. 27 run metas
carry a `workspace_id` the catalog does not root where the run ran. The
dashboard, opened in this repository, computed `bda366e0-…` from the root and
showed **1 of 2** live rows: it hid `pola-260818-192800-88430` — the parent run
of this very polarize flight — and it hid `scaf-260818-202208-26610`, a Mode B
worker in a worktree. After the cut both are visible.

The worktree row is what settles the axis. A worker's worktree root can never
equal its dispatcher's root, so a root-only reader is **structurally** unable to
see a dispatched worker. `workspace_id` is documented as _not_ derived from
root; the dashboard was the surface that had forgotten it.

**Rejected alternatives.**

- _Make the dashboard's root lookup win and treat the stamp as advisory._ Kills
  Mode B visibility outright and contradicts the wire contract's own line that
  `workspace_id` is not derived from root.
- _Trust any exported id without asking the catalog._ Then a variable left in a
  shell renames the workspace. The catalog is the sole durable writer; a bare id
  is evidence, not identity.
- _Repair the 27 mis-stamped metas / delete the leaked pytest workspace._ Host
  state, not repository truth, and rewriting durable run history to make a
  reader agree is the wrong direction. The reader was wrong.
- _Change the writer to fall through on a stale export instead of raising._ A
  writer creating durable state should refuse loudly; only the reader must
  degrade. The role boundary is now stated in the wire contract rather than left
  to each surface.
- _Send it back to marbles as a behaviour change._ Loops 1 and 2 both deferred
  it on that reasoning. It is a choice between two coexisting identities, which
  is precisely the cut polarize owns.

**Proof the guards bite.** With the env branch disabled,
`test_dashboard_identity_honours_the_exported_workspace_id` fails
`assert 'ws-rooted-here' == 'ws-exported'` and
`test_worktree_worker_stays_visible_in_the_dispatching_workspace` fails
`assert [] == ['pola-…', 'scaf-…']` — the whole flight hidden, exactly the live
shape. With the catalog corroboration dropped, the unknown-id and buried-id
refusals both fail. All green on restore; no probe residue in 1167 scanned files.

**What this does not fix.** The leaked pytest workspace and the 27 mis-stamped
metas are still in the operator's control plane. Settlement scoping still counts
membership from the raw stamp (it projects zero on this host today, so nothing
is provably misattributed). Neither is repository truth; both are named for DoU.

## Hydrate stage — the landed ledger, 2026-08-18

Stage 10 of the flight. The DoU audit found the engineering sound and the outward
channel dead, so hydrate did the two things that are repository truth — write down
what landed, and stop the payload from contradicting itself — and left every
outward valve as a named operator button.

### What landed, per cut

| Cut  | State at hydrate | Landing commits                             | What still stands between it and `[x]`                                                                                                                                         |
| ---- | ---------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| W0-a | `[?]`            | `85cebab5` `25fe62b3` `f08e8076` `48b3d9a9` | Portable payload proven clean from `48b3d9a9` (993 files, 0 leaks). No DMG has been rebuilt through the repaired remap and the new gate.                                       |
| W0-b | `[?]`            | `0e26b077`                                  | Help and missing-id `rc=1` verified; the live start → stop → `resume --run-id` → `--last` walk needs an installed build newer than `18dea346`.                                 |
| W0-c | `[ ]`            | `8e872b17` `289cff14` `fabf0e21`            | One identity resolution order landed with a 5-test contract; GUI acceptance and screenshots need a live vc-frame session.                                                      |
| W1-a | `[x]`            | `838165d6`                                  | Guard plus Windows-clone smoke; re-earned independently by the audit stage.                                                                                                    |
| W1-b | `[x]`            | `cd13e1ca` `1f6d36c3`                       | `--snapshot-donors`, the reaper, and the release-script hardening that followed it.                                                                                            |
| W1-c | `[?]`            | `e9f47da1`                                  | Byte-parity guard green and the docs honest; `https://vibecrafted.io/install.ps1` still answers 404 until `feat/saas-portal-merge` is merged and deployed in `vibecrafted-io`. |
| W2-a | `[ ]` deferred   | —                                           | `workspace_id` reaches 0 of 556 sites in vc-frame; a host-side `SessionInfo` projection is the agreed shape. Deferral is traced but not yet an operator `accept-dou`.          |
| W2-b | `[ ]` deferred   | —                                           | The tray already carries both `Open Console` and `Open vc-terminal`; the proposed rename would collide. Same deferral.                                                         |
| W3-a | `[x]`            | `01e5e18a` `4918c7fb`                       | 0 breaking / 0 structural / 0 diamond cycles, health 74 to 80; re-earned by the audit stage.                                                                                   |

Carried under the flight without belonging to a single cut: `b18b1483` and
`bcbfc776` (PR #54 review threads and the formatter settling after them),
`e9348458` (autonomy: non-destructive push classified as a duty, authored by
grok), `df9b6337` and the scaffold records `cf20aa4e` `833f770e` `63e5e8d3`
`69101f2c`, and this stage's own commits.

### What hydrate changed

- `plugin.json` declared `2.0.0` and `Apache-2.0` while `VERSION` read `4.1.0`
  and `LICENSE` opened with `SPDX-License-Identifier: BUSL-1.1`. That file is in
  `ALLOWED_TOP_LEVEL` in both `install.sh` and `scripts/distribution_manifest.py`,
  so the contradiction shipped to every installed host and into every
  distribution archive, sitting next to the LICENSE it disagreed with. It now
  states the version and the SPDX identifier it actually ships with, and carries
  the same author, homepage, and repository as the generated marketplace
  manifest. Two contract tests in `tests/tui/test_distribution_manifest.py` bind
  it to `VERSION` and to `LICENSE`; restoring the pre-cut values turns the first
  red with `plugin.json version '2.0.0' != VERSION '4.1.0'`.
- `CHANGELOG.md` carried two bullets under `## Unreleased` against 23 commits of
  landed work. The section now describes the 4.2.0 scope in Added / Changed /
  Fixed / Security. It deliberately stays `Unreleased`: `v4.1.0` was never
  tagged, so dating a `## 4.2.0` heading here would repeat exactly the kind of
  claim this flight exists to retire. The release stage promotes it with the tag.

### Corrections to the DoU audit

- **P1-8, the docs half, is not true of this repository.** The audit reported the
  install page never mentioning the desktop product (`dmg`, `--gui`, `Desktop`,
  `notariz` all at zero hits). Measured here, `docs/INSTALL.md` opens with a
  channel matrix naming the signed `Vibecrafted.app` DMG and its publication
  status, carries a `## macOS — the signed desktop app` section with the
  `shasum -a 256 -c` walk, documents `make app` and the desktop update path;
  `README.md` and `docs/RELEASE_CHECKLIST.md` both enumerate the DMG assets. The
  measurement was of the **deployed site**, which is three months stale and lives
  in `vibecrafted-io`. The gap is a deploy, not a docs gap — same button as W1-c.
- **P2-10, the brand half, is declined.** The audit called `"Vetcoders"` a
  drift from the canonical `VetCoders`. In this repository `Vetcoders` is the
  prevailing form at 272 occurrences against 6, it is hardcoded in
  `build_marketplace_bundle.plugin_manifest`, and it matches the `vetcoders`
  org slug. A 272-site rename is a branding decision for the operator, not a
  hydration cut; hydrate changed the version and the licence, and left the
  spelling alone.

### Not verified by hydrate

No DMG rebuilt, no payload re-scanned, no tag pushed, no site deployed, no
`make install`, no live vc-frame session, no full pytest roots, and no
`make unified-product-contract-gate` — this stage touched neither
`vibecrafted_core` nor `scripts/`, so the gate's trigger condition did not fire.
The tests that do cover the changed surface were run and are green.

## Release stage — the gate's second missing tool, 2026-08-18

The DoU named the top risk precisely: 4.2.0 could be tagged and fail exactly as
4.0.0 did, because the `runs-on: macos-15` cure (`54a98b23`) has never run
against a real tag. Release went looking for what else that untested path would
hit, and found the next mine on it.

**The final step of the source gate called a tool its own runner does not have.**
`Confirm publication boundary for both channels` invoked `command rg` — which
forces a lookup of a real `rg` binary on `PATH`. The GitHub `macos-15` image
ships no ripgrep: measured 2026-08-18 against the published image manifest
(`actions/runner-images`, `images/macos/macos-15-Readme.md`), zero occurrences,
alongside zero for shellcheck — which this same workflow independently confirms
by having to `brew install shellcheck` before it can lint. In this repository
`rg` exists only inside our own container images (`Dockerfile:40`,
`vibecrafted-vm/Containerfile:118`), never on the runner.

Under `set -euo pipefail` that step ends the job. So curing `xcrun` would have
moved the failure four steps later, not removed it: every test green, every
build done, and then the gate dies on a missing binary — the exact shape of the
v4.0.0 death. The step arrived in `ef700e52` (3.7.1) and **has never once
executed**, because every tag since died earlier. No amount of "the last release
worked" could surface it.

### What landed

| File                                  | What changed                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.github/workflows/release.yml`       | The two publication-boundary assertions moved from `command rg -n` to `grep -nE`. Same patterns, same files, same fail-on-no-match semantics — verified locally to still match on both channels — with no tool that has to be installed first.                                                                                                                 |
| `tests/tui/test_release_contract.py`  | Two new tests. One refuses any `run:` line in the tag gate that calls a binary absent from the runner image and not `brew install`ed, and separately requires the shellcheck install wherever `make check` runs. The other pins the boundary step's two patterns and all four files it covers, so rewriting the matcher cannot quietly shrink what it matches. |
| `scripts/hooks/pre-push`              | Semgrep now runs under `env -u PYTHONPATH -u PYTHONHOME`, the isolation `pre-commit` has carried for a while. Hydrate measured this one: inside a worker the scanner dies with `ModuleNotFoundError: No module named 'rpds.rpds'` and the push fails on a broken gate rather than on a finding.                                                                |
| `templates/hooks/lib/lint-routing.sh` | The same isolation for the shipped husky template's staged and full semgrep helpers. There the crash is worse-behaved, not better: the WARN-mode step counter reports it as a warning, so the gate stops gating without anyone noticing.                                                                                                                       |
| `docs/RELEASE_CHECKLIST.md`           | Section 5 now carries the gate's real run history and both tool gaps, and says plainly that the next tag is an experiment.                                                                                                                                                                                                                                     |

The shellcheck half of the tooling test is the quieter finding.
`scripts/check_shell.py` falls back to `bash -n` when shellcheck is missing, so
dropping that `brew install` would not fail the release gate — it would keep
reporting green while silently degrading from a linter to a syntax check.

### Why the version was not bumped and the CHANGELOG stays `Unreleased`

Hydrate left `## Unreleased` deliberately and release agrees, for a sharper
reason than symmetry: `v4.1.0` has no tag at all while `VERSION` and
`CHANGELOG.md` both call it released (DoU P0-2). Writing a dated `## 4.2.0`
heading on top of that would add a third unanchored version claim to a flight
whose whole purpose is retiring that class of claim. `VERSION` stays `4.1.0`
until the tag that makes it true exists.

### Mutation evidence

Both new assertions were driven red before they were trusted, and both first
drafts passed for the wrong reason — worth recording, because the failure mode
generalises. Restoring `command rg` turns the tooling test red naming both
offending lines; removing `brew install shellcheck` turns it red on the
`make check` allowance. The first draft of that second case stayed **green**:
the test read `brew install` out of the raw workflow text, and the explanatory
comment this same cut added to `release.yml` contains that phrase. A test that
reads a whole file also reads the comments written about it. Installs are now
parsed only out of `run:` lines.

### Not verified by release

No tag pushed, no release published, no site deployed, no `make install`, no DMG
built or re-scanned, no live vc-frame session, no `resume --run-id` walk. All
operator buttons, and all still open. The `grep -nE` step is proven to match
locally on macOS; it is **not** proven on a GitHub runner, because proving that
requires the tag push this stage does not make. The runner-image measurement is
a live read of one published manifest, not an execution on the image itself.

## Explicit non-goals

Native Windows runtime · a second control plane · new vc-frame features beyond the
rail and the 2026-08-16 chrome asks · rewriting the release scripts · merges into trunk,
deploys, or host installs performed by an agent (branch pushes and PR creation are the supervisor's; canonical list: vc-operator/AUTONOMY.md).
