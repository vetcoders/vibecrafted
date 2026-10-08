# vibecrafted-hooks-template

> One git-hook stack for every Vetcoders / LibraxisAI repository,
> across every language — TypeScript, Rust, Python, Shell.

Modular shell hooks unified from the four legacy snowflakes we accumulated
(`example-app`, `sample-portal`, `vetcoders-tools`, `unicode-puzzles-portal`).
Single source of truth for pre-commit / pre-push gates with **pluggable
activators** so each repo can stay native to its language ecosystem.

## What it gives you

- **Six hooks** with shared utility lib — `pre-commit`, `pre-push`,
  `pre-merge-commit`, `prepare-commit-msg`, `post-commit`, `commit-msg`.
- **Four activators** — pick the one that fits the repo:
  - `lefthook` (default) — Go binary, single install, parallel-capable
  - `husky` — npm-based, fits TS/JS repos
  - `pre-commit` — Python framework, fits Python repos
  - `manual` — bare `git config core.hooksPath .husky`
- **Opt-in steps** — each gate (secrets, lint-staged, tsc, semgrep, loctree,
  cargo, vitest, …) is a flag in `.husky/config.env`. Repos turn on what
  they have; the rest are no-ops.
- **WARN mode** (example-app pattern) — feature branches default to non-blocking
  with `warns/` retention. Protected branches (main / develop / release/\*)
  stay strict.
- **Secret-redaction in hook logs** — anything matching the secret regex is
  replaced with `<REDACTED>` before being written to `.husky/warns/`.
- **Conventional commits** with `[<agent>/<workflow>]` prefix support — same
  regex contract across every repo so commit history stays groupable.
- **Per-repo extension** — `.husky/local/<hook>.d/*.sh` scripts run in
  addition to template steps; lets repos keep one-off concerns without
  forking the template.
- **Living Tree integrity** — pre-commit rejects paths claimed by another live
  session and formats the Git index through a temporary projection, leaving
  unstaged hunks untouched. Pre-push checks commit objects, not ambient files.

## Install into a target repo

From the target repo root:

```bash
bash /path/to/vibecrafted/templates/hooks/install.sh --activator lefthook
```

or, with vibecrafted-current installed:

```bash
bash "$VIBECRAFTED_ROOT/templates/hooks/install.sh" --activator lefthook
```

The installer:

1. Copies `hooks/*` → `.husky/` (the directory name stays `.husky/` for
   continuity across activators — it's a path, not a tool dependency)
2. Copies `lib/*` → `.husky/lib/` (sourced by each hook)
3. Copies `scripts/*` → `.husky/scripts/` (node helpers)
4. Drops `config/template.husky.env` → `.husky/config.env` if not present
5. Marks hooks executable
6. Adds `.husky/warns/` to `.gitignore` if missing
7. Wires the chosen activator (writes `lefthook.yml` /
   `.pre-commit-config.yaml`, runs `lefthook install` / `husky` /
   `pre-commit install`, or sets `core.hooksPath=.husky` for manual)

Re-run is idempotent — existing `.husky/config.env`, `lefthook.yml`,
`.pre-commit-config.yaml`, and `.husky/local/` are preserved unless
`--force` is passed.

## Picking an activator

| Activator      | Best fit                                                                 | Install                                     |
| -------------- | ------------------------------------------------------------------------ | ------------------------------------------- |
| **lefthook**   | Polyglot repos (Rust+TS, Python+TS, mixed).                              | `brew install lefthook`                     |
| **husky**      | TS/JS repos that already use npm/pnpm/yarn.                              | `pnpm add -D husky && pnpm exec husky`      |
| **pre-commit** | Python-first repos, repos consuming the huge ready-made hooks ecosystem. | `pip install pre-commit` / `brew install …` |
| **manual**     | Tiny repos without a package manager.                                    | (nothing — installer sets `core.hooksPath`) |

All four activators run the **same** hook scripts in `.husky/` — the
choice only affects how git discovers them and how new contributors get
them activated when they clone the repo.

## Configuration — `.husky/config.env`

Single file controls every step. Defaults are conservative (most steps off);
turn on what your repo actually has. Full reference: see
`config/template.husky.env`.

Key knobs:

```bash
# WARN mode — non-blocking on feature branches, strict on protected.
HUSKY_WARN_MODE_ON_FEATURE=1
HUSKY_WARN_PROTECTED_BRANCHES='^(main|develop|release/.*|hotfix/.*)$'
HUSKY_WARN_RETENTION=5

# Pre-commit steps
HUSKY_PRECOMMIT_SECRETS=1
HUSKY_PRECOMMIT_CLAIMS=1
HUSKY_PRECOMMIT_ENV_FILES=1
HUSKY_PRECOMMIT_LINT_STAGED=0 # arbitrary tasks mutate the shared worktree
HUSKY_PRECOMMIT_PRETTIER_STAGED=1
HUSKY_PRECOMMIT_ESLINT_STAGED=1
HUSKY_PRECOMMIT_TSC=0
HUSKY_PRECOMMIT_SEMGREP_STAGED=1
HUSKY_PRECOMMIT_LOCT_HEALTH=0
HUSKY_PRECOMMIT_LOCT_SUPPRESSIONS=0
HUSKY_PRECOMMIT_RUST_CARGO_CHECK=0
HUSKY_PRECOMMIT_RUSTFMT_STAGED=0
HUSKY_PRECOMMIT_PY_RUFF=0
HUSKY_PRECOMMIT_SH_SHELLCHECK=0

# Pre-push gates
HUSKY_PREPUSH_PRETTIER_FULL=1
HUSKY_PREPUSH_RUFF_FULL=1
HUSKY_PREPUSH_SEMGREP_FULL=1
HUSKY_PREPUSH_TSC=1
HUSKY_PREPUSH_LOCT_CYCLES=0
HUSKY_PREPUSH_LOCT_COMMANDS=0
HUSKY_PREPUSH_VITEST=0
HUSKY_PREPUSH_CARGO_CLIPPY=0
HUSKY_PREPUSH_CARGO_TEST=0

# Commit-msg
HUSKY_COMMIT_MSG_CONVENTIONAL=1
HUSKY_COMMIT_MSG_ALLOW_AGENT_PREFIX=1
HUSKY_COMMIT_MSG_REQUIRE_AGENT_RUNTIME=1
HUSKY_COMMIT_MSG_SUBJECT_MAX=100

# Custom exclude paths (one per line)
HUSKY_EXCLUDE_PATHS='
node_modules/
dist/
.loctree/
target/
vendor/
'
```

## Hook responsibilities

| Hook                 | Purpose                                                                                                                                                   |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pre-commit`         | Strict foreign-claim fence plus stage-scoped checks. Formatters rewrite only index blobs from a temporary projection; unstaged hunks stay byte-identical. |
| `pre-push`           | Ruff, Prettier, and Semgrep run on a temporary tree projected from each pushed tip. Other optional repo gates retain their documented semantics.          |
| `pre-merge-commit`   | Codex-agent / vendored-path cleanup before merge commit.                                                                                                  |
| `prepare-commit-msg` | Adds missing mechanical agent/runtime trailers before `commit-msg`, then appends `Vibecrafted-Warn-Signature` if WARN mode demoted pre-commit.            |
| `post-commit`        | Warns if agent-artifact filenames (`RAPORT_*`, `_SESSION_*`, etc.) landed in commit.                                                                      |
| `commit-msg`         | Conventional commit regex with optional `[agent/workflow]` prefix.                                                                                        |

## WARN mode — how it works

On feature/local branches, hook failures are demoted to warnings: the commit
proceeds but the log is archived to `.husky/warns/<hook>-<timestamp>.log`
(retention `$HUSKY_WARN_RETENTION` rolling). Two consecutive warnings with
the same signature (hash of failure output) escalate back to strict mode —
prevents the same regression sliding through unchecked.

Protected branches (default regex: `^(main|develop|release/.*|hotfix/.*)$`)
always run strict. Override with `HUSKY_STRICT=1` for one-off forced strict
on any branch, or `HUSKY_WARN_FORCE=1` to override into WARN mode.

## Migration from snowflake hooks

To replace a legacy hook setup:

```bash
# 1. Backup current .husky
mv .husky .husky.bak.$(date +%s)
rm -f lefthook.yml .pre-commit-config.yaml  # if any

# 2. Install template (pick activator)
bash $VIBECRAFTED_ROOT/templates/hooks/install.sh --activator lefthook

# 3. Port any repo-specific steps to .husky/config.env or to
#    .husky/local/{pre-commit,pre-push}.d/<step>.sh
#    Scripts in .husky/local/<hook>.d/ run in addition to template steps,
#    skipped automatically if the directory does not exist.

# 4. Verify
git commit --allow-empty -m "chore(hooks): smoke test"
```

## Switching activator

```bash
# Remove the old activator config
rm -f lefthook.yml .pre-commit-config.yaml

# Re-run installer with new activator
bash $VIBECRAFTED_ROOT/templates/hooks/install.sh --activator pre-commit --force
```

The template files in `.husky/` are unchanged — only the activator config
at repo root is swapped.

## License

BUSL — same as the parent vibecrafted project. Free for use inside any
Vetcoders / LibraxisAI repository, third-party usage subject to the BUSL
conversion timeline.

---

_Vibecrafted with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
