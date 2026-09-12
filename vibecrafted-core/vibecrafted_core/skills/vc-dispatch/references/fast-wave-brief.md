# Fast-wave brief — field-learned mine checklist

A fast-wave (blitz) brief is authored by the dispatcher in-session. It is lean,
not thin: every item below earned its place by burning a real wave. Include
each one that applies, verbatim in spirit.

## Skeleton (all sections, in order)

frontmatter (`plan_id`, `role: brief`, `agent`, `date`, `project`) · Mission ·
Context/evidence · Files · Acceptance · Gates · Out-of-scope · Substrate ·
Loctree-first · Branch/commits/report.

## The mines (loctree-suite field runs, 2026-08)

- **Evidence is verbatim, not summarized.** Paste the failing command, its
  output, the line anchors (`cache.rs:453`), and the machine it happened on.
  A worker re-deriving your diagnosis burns its budget on archaeology.
- **Baseline SHA pinned in the brief** — and fetched fresh at dispatch time.
  HEAD moves between diagnosis and order (observed: 788400c0 → 826e88ae
  within one conversation).
- **`export CARGO_TARGET_DIR="$PWD/target"`** in every Rust brief — the
  shared target dir silently swaps binaries between concurrent worktrees.
- **Count ALL `test result:` lines** — `cargo test … | tail -1` measures the
  last binary, not the one with your test (false "ok. 0 passed").
- **`PYTHONPATH=` before any semgrep gate** — Homebrew semgrep dies under the
  Vibecrafted python-site overlay.
- **Do-not-touch list**: name the files owned by pending sibling branches
  (unmerged waves) and by sibling cuts of THIS wave. Shared hub files get
  region assignments ("locks ~200–300 are w2-b's; stay out").
- **Hub files are additive-only**: on a high-fan-in file (`types.rs`, 84
  importers) demand new serde-defaulted fields, no renames, no signature
  changes.
- **Idempotency clause**: "if re-run on a tree where this already landed,
  verify and stop" — refire is the cheapest convergence primitive and must
  not duplicate work.
- **≥2 non-trivial new tests** in acceptance; a gate matching 0 tests is
  trivially green.
- **Trailer block spelled out**: `[<agent>/workflow] type(scope): subject`,
  `Authored-By: <agent> <agents@vetcoders.io>`, `session_id:`, `date:`
  (ISO+TZ), `runtime:` — the commit-msg hook rejects anything less.
- **Report to `$VIBECRAFTED_REPORT_PATH`**, push branch on green, NO trunk
  merge — merge is the integrator's, PR is the operator's.

## Substrate block (operator-ordered worktrees)

```
git -C <main-checkout> worktree add \
  ~/.vibecrafted/worktrees/<org>/<repo>/<YYYY_MMDD>/<slug> \
  -b <agent>/workflow/<slug> <baseline-sha>
cd ~/.vibecrafted/worktrees/<org>/<repo>/<YYYY_MMDD>/<slug>
```

Work ONLY there; never touch the main checkout; baseline SHA named.

## Model pins

Operator-named pins ride verbatim into `--model` and the tracker table
(`cut | worker@model | run_id | branch | state`). An operator pin is not a
suggestion; silent substitution is false attribution of a decision.
