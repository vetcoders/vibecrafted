# Main-candidate ledger — host `linux`, 2026-09-12

Branch: `main-candidate/linux-2026-09-12`, based on `main` @ `ba6b104` (Release 4.2.4).
Program: each host merges every open PR into one local candidate that is mergeable to `main`, opens it as a
**draft** PR, and stops. Merging the candidate and closing the superseded PRs remain the Founder's decision.

Inputs: 16 open PRs on 2026-09-12 (#65, #66, #69, #70, #71, #73, #74, #75, #77, #80, #81, #82, #83, #84, #85,
#87) plus draft #86, which was merged into #80's branch at 14:37 UTC the same day. Every PR was examined
commit-by-commit by a separate recon agent (Sonnet 5); their reports are summarised in §3.

## 1. Finding that shaped everything: one line of history, not two

The clone on this host was **shallow at `7ba93c1`** (the first commit of the 4.3.1 work, 2026-09-10). With that
boundary, every branch from #80 upward looked like an orphan history with no ancestor in `main`, and a three-way
merge against `main` produced 158 conflicts. That is exactly what the stalled `git rebase` found in `/workspace` at
session start had run into (467 "both added" entries).

After `git fetch --unshallow`, the truth is simple:

- `main` → #75 (297 commits, VERSION 4.3.0) → #77 (5 commits) → **#80's branch (466 further commits, VERSION 4.3.1)**.
- #77's head `319b2fc` is a direct ancestor of #80's tip `7bf9aca`. Merging #80 into `main`+#75+#77 is
  conflict-free.
- #75 already contains #69, #70, #71, #73 and #74 as ancestors (their commits partition its 297 exactly:
  69 unique + 58 + 112 + 46 + 2 + 10).
- #66's code is byte-identical inside #75/#80; #65 is a stale twin of #66 with 34 rebase-drift commits.

Consequence for the "500k lines" hypotheses: the size growth is real work on one line (4.2.4 → 4.3.1), not
duplicated lineages. Duplication inside the tree is measured in §5.

## 2. Merge sequence (exact SHAs on the candidate)

| Step | Commit | What |
| --- | --- | --- |
| 1 | `79ffb24` | merge #75 `fix/v430-dispatcher-shutdown-race-v5` @ `81ced43` (clean) |
| 2 | `4cdc5a6` | merge #77 `agent/plist-decode-errors-all-sites` @ `319b2fc` (clean) |
| 3 | `945272e` | merge #80 `agent/fix-supervisor-in-process-probe` @ `7bf9aca`, incl. merged draft #86 (clean after unshallow) |
| 4 | `a8787be` | merge #82 `cursor/linux-431-platform-close-89f6` @ `b2c692a`, contains all 5 commits of #81 — 8 conflicts, resolved in §4 |
| 5 | `a1df62a` | merge #83 `cursor/cloud-env-debian13-b3ef` @ `7fc4210` (clean) |
| 6 | `a8a7417` | merge #85 `cursor/vibecrafted-vm-persistent-dev-b3ef` @ `2b44811` (clean) |
| 7 | `2117994`, `1109c82` | cherry-pick #84 `d93b0f5`, `d9b3303` (branch was 27 commits stale vs #80; merging it would drag reverted drift across 55 files) |
| 8 | `371eb2c` | cherry-pick #87 `b757fe4` (same reason; single 2-line commit) |
| 9 | `7a277d5` | hygiene: re-scrub private host name and `/Users/<login>` paths the 4.3.1 line reintroduced (found by the #70 recon) |
| 10 | `ae6059f` | fix(voc): projection revision hashes file size next to mtime — `projection_revision_ignores_transcript_appends` failed deterministically on Linux (two writes inside one clock tick); file identical to #80, so this is a 4.3.1-line bug, not a merge artefact |
| 11 | `e51bb64` | test(tui): align two #82 test expectations with the #80 foundations ruling (`stage_npm_bins`/cargo assertions; `require_native_host` on the installer test double) — the only two TUI failures caused by the merge |

## 3. Per-PR disposition

| PR | Title (short) | Disposition | Evidence |
| --- | --- | --- | --- |
| #75 | release 4.3.0 line | KEEP-WHOLE | clean fast-forward off main; 69 unique commits + five sub-PRs |
| #77 | plist-decode error set | KEEP-WHOLE | 8/9 files byte-identical in #80, 9th is a strict superset there |
| #80 | in-process supervisor probe (+ 4.3.1 line, + #86) | KEEP-WHOLE | title is stale: the probe landed via #77/#78; the branch's real content is bash-3.2 fixes, `--version` gates, CI source provenance, codesigning, agy stream-json transport |
| #81 | Linux source-vs-pack, Windows entry honesty | KEEP via #82 | all 5 commits are a prefix of #82 |
| #82 | 4.3.1 platform close | KEEP-PARTIAL | x64 architecture-slug fix, GNU `stat` rescue fix, supervisor resume fix, RSA CI signing kept; cargo-era foundations stager and its two paired tests superseded by #80's cargo-free rewrite (see §4) |
| #83 | Debian 13 Cloud Agent env | KEEP-WHOLE | 3 new files under `.cursor/`, zero overlap |
| #84 | navbar chips on one plane (draft) | KEEP-WHOLE (cherry-pick) | not present in #80's tip; 2 files |
| #85 | persistent dev container (Kimi) | KEEP-WHOLE | additive under `vibecrafted-vm/` |
| #86 | keep host Python 3.9 off product door (draft) | already in #80 | merged into #80's branch 2026-09-12 14:37 UTC |
| #87 | Codex-only model routing policy | KEEP-WHOLE (cherry-pick) | 2 files; not in #80's tip |
| #69 | DMG on hosted macOS runner | SUPERSEDED-BY #75 | ancestor; #75 only extended its files |
| #70 | de-privatize | SUPERSEDED-BY #75 | ancestor; regression on the 4.3.1 line fixed in step 9 |
| #71 | reversible Runtime Pack install | SUPERSEDED-BY #75 | ancestor; 76/142 files untouched since, rest extended |
| #73 | relocate | SUPERSEDED-BY #75 | ancestor; relocate module identical in #75 and #80 |
| #74 | server caretaker | SUPERSEDED-BY #75 | ancestor; no reversion in the 58 files #75 changed on top |
| #66 | vc-canary doctrine (clean) | SUPERSEDED (content already in #75/#80) | code byte-identical; SKILL prose deliberately rewritten later (`version: 2.1.0`) |
| #65 | vc-canary doctrine (original) | SUPERSEDED-BY #66 | same two patches byte-identical; one drift commit regresses `cli.py` verb order |

## 4. The eight #82 conflicts and how they were settled

| File | Ruling | Why |
| --- | --- | --- |
| `scripts/stage-runtime-foundations.sh` | #80 (ours) | #80 removed cargo entirely (npm with sha512, prebuilt prview); #82's cargo/g++/rustc-pin fixes target a path that no longer exists |
| `tests/tui/test_runtime_foundation_pins.py` | #80 | paired with the stager above |
| `tests/tui/test_release_contract.py` | #80 assertions + #82's `INSTALL_PS1_SHA256` (auto-merged) | digest verified against the merged `install.ps1` (`d23aad1c…`) |
| `vibecrafted-core/tests/test_git.py` | #80 | #80 pins `-b main` on both bare inits, #82 on one |
| `.github/workflows/install-linux.yml` | combined | #80's `VIBECRAFTED_SOURCE_OWNER_REPO` + #82's real x64 build script and RSA-2048 signing (Ed25519 cannot `dgst -sha256 -sign` on OpenSSL 3) |
| `scripts/vc-frame-product-entry.sh` | combined | #82's `case` passthrough for `--version/-V/--help/-h` under #80's rationale comment |
| `scripts/vc-terminal-product-entry.sh` | combined | same, on top of #80's bundle-host selection |
| `scripts/vetcoders_install.py` | combined | #80's three bundle-host helpers kept; #82's `require_native_host` keyword on `_materialize_runtime_generation_vc_terminal_entry` kept (caller at source preflight passes `False`) |

The hygiene step (9) replaced the Founder's build-host name in four comments/docs and `/Users/<login>` in two ADR
files, one TUI fixture and the relocate test's synthetic paths. Copyright notices and the iterm2 test that forbids
the host name were left as-is.

## 5. Size and structure

| Tree | Files | Lines | Bytes |
| --- | --- | --- | --- |
| `main` @ ba6b104 (4.2.4) | 1,301 | 408,901 | 21,975,231 |
| #77 head (4.3.0 line) | 1,391 | 453,646 | 24,177,838 |
| #80 head (4.3.1 line) | 1,501 | 546,761 | 27,920,456 |
| candidate @ 7a277d5 (4.3.1) | 1,510 | 547,625 | 27,957,844 |

Loctree (`loct findings --summary`): pre-merge 4.3.0 tree 1,278 source files, 432,464 LOC, health 93, 59 duplicate
groups, 28 dead symbols, 0 breaking cycles; candidate 1,393 source files, 525,984 LOC, health 86, 65 duplicate
groups, 88 dead symbols, 2 breaking cycles, barrel chaos 121. The 4.3.1 line adds ~93k LOC and lowers structural
health; the duplicate-group count is the input for hypothesis (b).

## 6. Validation on this host (Linux, Python 3.12, Rust 1.97.0 pinned)

| Check | Result |
| --- | --- |
| `make check` (189 shell files, syntax-only: shellcheck absent) | pass |
| `tests/skill_loader_smoke.sh` | pass (39 ok, 3 warnings, 0 failures) |
| `tests/install_smoke.sh` | pass (19 ok; `pwsh` parse skipped) |
| `pytest tests/tui` (35 min) | 2,004 pass, 189 skip, 197 fail before step 11; 195 after. Control: the three worst files (`test_declaration_entry`, `test_runtime_regressions`, `test_operator_mode`) fail with the **same 91 test ids on #80's tip** in a clean worktree; 27 of the 29 failing files are byte-identical to #80. Dominant causes are host-environmental on Linux: no TTY / no installed front door for `vc-resume`, missing signed DMG/pack fixtures, GNU `stat` output on overlayfs, `bash -lc` sourcing an uninstalled `launch-primary-shell.zsh`. Pre-existing on the 4.3.1 line; not candidate defects. The two merge-induced failures were fixed in step 11 |
| `pytest vibecrafted-core/tests` (13 min) | 2,498 pass, 13 skip, 1 fail: `test_cli.py::test_apply_live_liveness_flags_dead_launcher` hardcodes pid 999999 as dead; passes in isolation; test and module identical to #80 → pre-existing flake, out of scope |
| `cargo clippy -D warnings` control-core + web (ssr); `cargo test -p control-core` | pass; 102 tests pass |
| `cargo +1.97.0 check --workspace --all-targets`; `cargo test --workspace` | check pass; tests pass after step 10 (voc 146/146, others 132/132). `clippy -D warnings` on `voc` fails in `mission_control.rs` and `procs/gpu.rs`, both identical to #80 → pre-existing, out of scope. Default cargo 1.83 cannot build this workspace (edition 2024); the pinned 1.97.0 is required |
| macOS-only surfaces (Swift app, DMG, codesign) | not runnable here; CI on the draft PR is the gate |

## 7. Left out / needs Founder decision

- Nothing from any PR was dropped except content the same PR's successor had already replaced (§4).
- #80's PR title and body no longer describe its branch; the release notes should not credit it with the
  supervisor probe.
- CHANGELOG has no `## 4.3.1` heading yet; the 4.3.1 material sits under `## Unreleased`.
- `docs/RELEASE_CHECKLIST.md` still carries a `git tag -a v4.3.1 -m "Vibecrafted 4.3.0"` typo from the 4.3.1 line.
- The Windows `install.ps1` digest constant now points at #81's copy; the served copy in vibecrafted-io must
  be updated in lockstep.
- Closing #65–#87 as superseded and merging this candidate are not done here.
