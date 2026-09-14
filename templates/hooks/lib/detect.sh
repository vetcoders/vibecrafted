# shellcheck shell=bash
# vibecrafted-hooks-template :: lib/detect.sh
#
# Repository introspection for auto-configuring config.env. Each helper
# either echoes a value/marker to stdout or returns 0/1 — never writes
# config directly. install.sh composes the results into a config.env that
# matches the repo's actual shape, so the operator never has to toggle
# flags by hand.
#
# Design rule: a positive detection (e.g. "rust present") only flips a gate
# ON when the corresponding tool is ALSO installed. Detecting a Cargo.toml
# but having no rustfmt on PATH yields a config that mentions the gap, not
# one that enables a broken hook.
#
# Vibecrafted with AI Agents by Vetcoders (c)2024-2026 LibraxisAI

set -euo pipefail

# ---------------------------------------------------------------------------
# Language detection (returns 0 if present, 1 otherwise)
# ---------------------------------------------------------------------------

husky_detect_rust() {
  local root="$1"
  [ -f "$root/Cargo.toml" ] || [ -f "$root/src-tauri/Cargo.toml" ]
}

husky_detect_python() {
  local root="$1"
  [ -f "$root/pyproject.toml" ] \
    || [ -f "$root/setup.py" ] \
    || [ -f "$root/requirements.txt" ] \
    || [ -f "$root/Pipfile" ] \
    || [ -f "$root/uv.lock" ]
}

husky_detect_typescript() {
  local root="$1"
  [ -f "$root/tsconfig.json" ] || \
    { [ -f "$root/package.json" ] && grep -q '"typescript"' "$root/package.json" 2>/dev/null; } || \
    [ -n "$(find "$root" -maxdepth 3 -name tsconfig.json -not -path '*/node_modules/*' 2>/dev/null | head -1)" ]
}

husky_detect_javascript() {
  local root="$1"
  [ -f "$root/package.json" ]
}

husky_detect_shell_scripts() {
  local root="$1"
  [ -n "$(find "$root" -maxdepth 3 -name '*.sh' -not -path '*/node_modules/*' -not -path '*/.git/*' 2>/dev/null | head -1)" ]
}

# ---------------------------------------------------------------------------
# Workspace / repo-shape detection
# ---------------------------------------------------------------------------

husky_detect_pnpm_workspace() {
  local root="$1"
  [ -f "$root/pnpm-workspace.yaml" ]
}

husky_detect_npm_workspaces() {
  local root="$1"
  [ -f "$root/package.json" ] && grep -q '"workspaces"' "$root/package.json" 2>/dev/null
}

husky_detect_cargo_workspace() {
  local root="$1"
  [ -f "$root/Cargo.toml" ] && grep -q '\[workspace\]' "$root/Cargo.toml" 2>/dev/null
}

# Returns the path to a Cargo.toml suitable for `cd && cargo`. Prefers
# src-tauri/Cargo.toml (Tauri repos), falls back to root Cargo.toml.
husky_detect_cargo_dir() {
  local root="$1"
  if [ -f "$root/src-tauri/Cargo.toml" ]; then
    echo "src-tauri"
  elif [ -f "$root/Cargo.toml" ]; then
    echo "."
  else
    echo ""
  fi
}

# ---------------------------------------------------------------------------
# Tool availability (returns 0 if on PATH)
# ---------------------------------------------------------------------------

husky_have_tool() { command -v "$1" >/dev/null 2>&1; }

husky_have_lefthook()   { husky_have_tool lefthook; }
husky_have_husky_pkg()  { local root="$1"; [ -d "$root/node_modules/husky" ]; }
husky_have_precommit()  { husky_have_tool pre-commit; }
husky_have_semgrep()    { husky_have_tool semgrep || husky_have_tool uvx; }
husky_have_loct()       { husky_have_tool loct || husky_have_tool loctree; }
husky_have_rustfmt()    { husky_have_tool rustfmt; }
husky_have_clippy()     { husky_have_tool cargo && cargo clippy --version >/dev/null 2>&1; }
husky_have_ruff()       { husky_have_tool ruff || husky_have_tool uvx; }
husky_have_shellcheck() { husky_have_tool shellcheck; }
husky_have_prettier() {
  local root="$1"
  husky_have_tool prettier && return 0
  [ -d "$root/node_modules/prettier" ]
}
husky_have_eslint() {
  local root="$1"
  husky_have_tool eslint && return 0
  [ -d "$root/node_modules/eslint" ]
}
husky_have_stylelint() {
  local root="$1"
  husky_have_tool stylelint && return 0
  [ -d "$root/node_modules/stylelint" ]
}
husky_have_vitest() {
  local root="$1"
  [ -d "$root/node_modules/vitest" ] || [ -f "$root/vitest.config.ts" ] || [ -f "$root/vitest.config.js" ]
}

# ---------------------------------------------------------------------------
# Existing-config detection
# ---------------------------------------------------------------------------

husky_have_lint_staged_config() {
  local root="$1"
  [ -f "$root/.lintstagedrc" ] \
    || [ -f "$root/.lintstagedrc.json" ] \
    || [ -f "$root/.lintstagedrc.js" ] \
    || { [ -f "$root/package.json" ] && grep -q '"lint-staged"' "$root/package.json" 2>/dev/null; }
}

husky_have_existing_husky() {
  local root="$1"
  [ -d "$root/.husky" ] && [ -d "$root/node_modules/husky" ]
}

# Suggested activator based on what the repo already uses.
#   lefthook (default for polyglot / new repos)
#   husky    (when husky is already wired and the repo is npm/pnpm only)
#   pre-commit (when .pre-commit-config.yaml already exists)
husky_suggest_activator() {
  local root="$1"
  if [ -f "$root/lefthook.yml" ] || [ -f "$root/.lefthook.yml" ]; then
    echo "lefthook"
    return
  fi
  if [ -f "$root/.pre-commit-config.yaml" ]; then
    echo "pre-commit"
    return
  fi
  # Default: lefthook for everyone. Husky is a downgrade for polyglot repos.
  echo "lefthook"
}

# ---------------------------------------------------------------------------
# Profile builder — used by install.sh to summarise findings
# ---------------------------------------------------------------------------

# husky_profile_summary <repo-root>
# Prints a human-readable block describing the detected profile. install.sh
# echoes this to the operator so they can sanity-check before files land.
husky_profile_summary() {
  local root="$1"
  local langs=()
  husky_detect_rust "$root"        && langs+=("rust")
  husky_detect_python "$root"      && langs+=("python")
  husky_detect_typescript "$root"  && langs+=("typescript")
  if husky_detect_javascript "$root"; then
    if [ ${#langs[@]} -eq 0 ] || [ "${langs[-1]}" != "typescript" ]; then
      langs+=("javascript")
    fi
  fi
  husky_detect_shell_scripts "$root" && langs+=("shell")

  local tools=()
  husky_have_semgrep              && tools+=("semgrep")
  husky_have_loct                 && tools+=("loct")
  husky_have_rustfmt              && tools+=("rustfmt")
  husky_have_clippy               && tools+=("clippy")
  husky_have_ruff                 && tools+=("ruff")
  husky_have_shellcheck           && tools+=("shellcheck")
  husky_have_prettier "$root"     && tools+=("prettier")
  husky_have_eslint "$root"       && tools+=("eslint")
  husky_have_stylelint "$root"    && tools+=("stylelint")
  husky_have_vitest "$root"       && tools+=("vitest")

  local shape=()
  husky_detect_pnpm_workspace "$root"  && shape+=("pnpm-workspace")
  husky_detect_npm_workspaces "$root"  && shape+=("npm-workspaces")
  husky_detect_cargo_workspace "$root" && shape+=("cargo-workspace")

  local existing=()
  husky_have_lint_staged_config "$root" && existing+=("lint-staged")
  husky_have_existing_husky "$root"     && existing+=("husky")
  [ -f "$root/lefthook.yml" ]           && existing+=("lefthook.yml")
  [ -f "$root/.pre-commit-config.yaml" ] && existing+=("pre-commit-config")

  echo "Repository profile (${root}):"
  printf '  Languages:      %s\n' "${langs[*]:-none-detected}"
  printf '  Tools:          %s\n' "${tools[*]:-none}"
  printf '  Shape:          %s\n' "${shape[*]:-single-package}"
  printf '  Existing hooks: %s\n' "${existing[*]:-none}"
  printf '  Activator:      %s\n' "$(husky_suggest_activator "$root")"
}

# ---------------------------------------------------------------------------
# Auto config.env generator
# ---------------------------------------------------------------------------

# husky_write_auto_config <repo-root> <output-config-path>
#
# Writes a config.env with gates flipped ON based on what the repo HAS and
# what tools ARE installed. Stays conservative: only enables a step when
# both the language AND the tool exist locally. Falsy detection (no tool)
# leaves the gate at 0 with a one-line comment explaining why.
husky_write_auto_config() {
  local root="$1"
  local out="$2"

  # --- per-detection flags
  # Note: TypeScript detection is consumed indirectly — workspace-aware
  # tsc isn't solved in the template yet, so we don't gate on g_ts. The
  # detect call is still made (signalled in profile summary) so the
  # operator can see what was found, but no flag flips based on it.
  local g_rust=0;       husky_detect_rust "$root"       && g_rust=1
  local g_python=0;     husky_detect_python "$root"     && g_python=1
  local g_shell=0;      husky_detect_shell_scripts "$root" && g_shell=1

  local g_semgrep=0;    husky_have_semgrep && g_semgrep=1
  local g_loct=0;       husky_have_loct && g_loct=1
  local g_rustfmt=0;    husky_have_rustfmt && g_rustfmt=1
  local g_clippy=0;     husky_have_clippy && g_clippy=1
  local g_ruff=0;       husky_have_ruff && g_ruff=1
  local g_shellcheck=0; husky_have_shellcheck && g_shellcheck=1
  local g_prettier=0;   husky_have_prettier "$root" && g_prettier=1
  local g_eslint=0;     husky_have_eslint "$root" && g_eslint=1
  local g_stylelint=0;  husky_have_stylelint "$root" && g_stylelint=1
  # Vitest detection captured for the profile summary but vitest-on-push
  # is intentionally opt-in (default OFF) — multi-second test runs at
  # push time are user-hostile. Operator flips HUSKY_PREPUSH_VITEST=1
  # manually in repos where the test suite is fast enough.

  local cargo_dir; cargo_dir="$(husky_detect_cargo_dir "$root")"
  [ -z "$cargo_dir" ] && cargo_dir="."

  # --- gate selection logic
  # Pre-commit (stage-scoped, fast):
  #   - Security ALWAYS strict (handled by hook entry, not config flag)
  #   - arbitrary lint-staged tasks are never run on the shared working copy
  #   - tsc disabled by default — workspace repos break flat tsc invocation
  local pc_secrets=1
  local pc_env=1
  local pc_lintstaged=0
  local pc_prettier=$(( g_prettier ))
  local pc_eslint=$(( g_eslint ))
  local pc_stylelint=$(( g_stylelint ))
  local pc_semgrep=$(( g_semgrep ))
  local pc_loct_health=$(( g_loct ))
  local pc_loct_supp=0   # opt-in only — budgets need per-repo tuning
  local pc_rustfmt=$(( g_rust && g_rustfmt ))
  local pc_cargocheck=$(( g_rust ))
  local pc_ruff=$(( g_python && g_ruff ))
  local pc_shellcheck=$(( g_shell && g_shellcheck ))

  # Pre-push (full-repo, slower):
  #   - Defaults skew higher coverage; user already paying push latency
  local pp_prettier_full=0  # often noisy on existing repos — opt-in
  local pp_ruff_full=$(( g_python && g_ruff ))
  local pp_semgrep_full=$(( g_semgrep ))
  local pp_tsc=0            # workspace-aware tsc not solved in template
  local pp_loct_cycles=$(( g_loct ))
  local pp_loct_cmd=0
  local pp_vitest=0         # vitest at push-time is slow; opt-in
  local pp_clippy=$(( g_rust && g_clippy ))
  local pp_cargotest=0      # slow; opt-in
  local pp_secrets=1

  # --- write config.env
  cat > "$out" <<EOF
# vibecrafted-hooks-template — repo-local configuration (auto-generated)
#
# Generated by install.sh --auto on $(date -u +%Y-%m-%dT%H:%M:%SZ).
# Detection summary:
$(husky_profile_summary "$root" | sed 's/^/#   /')
#
# Edit values freely — re-running install.sh preserves this file unless
# --force is passed.

# ── WARN mode ────────────────────────────────────────────────────────
HUSKY_WARN_MODE_ON_FEATURE=1
HUSKY_WARN_PROTECTED_BRANCHES='^(main|develop|release/.*|hotfix/.*)$'
HUSKY_WARN_RETENTION=5

# ── Pre-commit (stage-scoped) ───────────────────────────────────────
# Security guards (secrets, env files) are run by hook entry as STRICT,
# never demoted to WARN. Flags 0/1 here just enable/disable the checks
# themselves — when on, they are non-negotiable.
HUSKY_PRECOMMIT_SECRETS=${pc_secrets}
HUSKY_PRECOMMIT_ENV_FILES=${pc_env}
HUSKY_PRECOMMIT_CLAIMS=1

HUSKY_PRECOMMIT_LINT_STAGED=${pc_lintstaged}
HUSKY_PRECOMMIT_PRETTIER_STAGED=${pc_prettier}
HUSKY_PRECOMMIT_ESLINT_STAGED=${pc_eslint}
HUSKY_PRECOMMIT_STYLELINT_STAGED=${pc_stylelint}
HUSKY_PRECOMMIT_TSC=0    # workspace-aware tsc not solved in template
HUSKY_PRECOMMIT_SEMGREP_STAGED=${pc_semgrep}
HUSKY_PRECOMMIT_LOCT_HEALTH=${pc_loct_health}
HUSKY_PRECOMMIT_LOCT_SUPPRESSIONS=${pc_loct_supp}
HUSKY_PRECOMMIT_LOCT_SUPPRESSIONS_BUDGET=30

HUSKY_PRECOMMIT_RUSTFMT_STAGED=${pc_rustfmt}
HUSKY_PRECOMMIT_RUST_CARGO_CHECK=${pc_cargocheck}
HUSKY_RUST_CARGO_DIR='${cargo_dir}'

HUSKY_PRECOMMIT_PY_RUFF=${pc_ruff}
HUSKY_PRECOMMIT_PY_BLACK=0
HUSKY_PRECOMMIT_SH_SHELLCHECK=${pc_shellcheck}

# ── Pre-push (full-repo, slower) ───────────────────────────────────
HUSKY_PREPUSH_PRETTIER_FULL=${pp_prettier_full}
HUSKY_PREPUSH_RUFF_FULL=${pp_ruff_full}
HUSKY_PREPUSH_SEMGREP_FULL=${pp_semgrep_full}
HUSKY_PREPUSH_TSC=${pp_tsc}
HUSKY_PREPUSH_LOCT_CYCLES=${pp_loct_cycles}
HUSKY_PREPUSH_LOCT_COMMANDS=${pp_loct_cmd}
HUSKY_PREPUSH_VITEST=${pp_vitest}
HUSKY_PREPUSH_CARGO_CLIPPY=${pp_clippy}
HUSKY_PREPUSH_CARGO_TEST=${pp_cargotest}
HUSKY_PREPUSH_SECRETS=${pp_secrets}

# ── Commit-msg ──────────────────────────────────────────────────────
HUSKY_COMMIT_MSG_CONVENTIONAL=1
HUSKY_COMMIT_MSG_ALLOW_AGENT_PREFIX=1
HUSKY_COMMIT_MSG_REQUIRE_AGENT_RUNTIME=1
HUSKY_COMMIT_MSG_SUBJECT_MAX=100

# ── Pre-merge-commit ───────────────────────────────────────────────
HUSKY_PREMERGE_CLEAN_CODEX_AGENT=1
HUSKY_PREMERGE_CLEAN_PATHS=''

# ── Post-commit ────────────────────────────────────────────────────
HUSKY_POSTCOMMIT_CLAUDE_ARTIFACT_WARN=1

# ── Excludes / blocklists ──────────────────────────────────────────
HUSKY_EXCLUDE_PATHS='
node_modules/
dist/
.loctree/
target/
vendor/
.next/
build/
.husky/_/
'

HUSKY_BLOCKED_PATHS='
.env
.env.local
.env.production
.env.staging
LibraxisAI-*.md
CLAUDE.local.md
'
EOF
}
