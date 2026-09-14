# shellcheck shell=bash
# vibecrafted-husky-template :: lib/core.sh
#
# Shared utilities sourced by every hook. Provides:
#   - husky_load_config       : load .husky/config.env into env (no-op if missing)
#   - husky_log / husky_warn / husky_err : structured logging with emoji prefix
#   - husky_is_protected_branch : returns 0 if current branch matches protected regex
#   - husky_warn_mode_active  : returns 0 if WARN mode applies to current run
#   - husky_run_step          : runs a step with WARN-mode awareness (demote → warn)
#   - husky_hash_file         : sha256 of a file (failure signature)
#   - husky_warns_dir         : path to .husky/warns/ (created on demand)
#   - husky_warns_rotate      : keep $HUSKY_WARN_RETENTION newest per hook
#   - husky_warns_signature_pending : returns 0 if same signature was seen recently
#   - husky_warns_clear_for_hook : remove all pending warns for a given hook
#
# Hooks set HUSKY_HOOK_NAME before sourcing this file.

set -euo pipefail

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

husky_repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

HUSKY_REPO_ROOT="$(husky_repo_root)"
HUSKY_DIR="$HUSKY_REPO_ROOT/.husky"
HUSKY_LIB_DIR="$HUSKY_DIR/lib"
HUSKY_SCRIPTS_DIR="$HUSKY_DIR/scripts"
HUSKY_WARNS_DIR="$HUSKY_DIR/warns"
HUSKY_LOCAL_HOOK_DIR_BASE="$HUSKY_DIR/local"

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

husky_load_config() {
  local config="$HUSKY_DIR/config.env"
  if [ -f "$config" ]; then
    # shellcheck disable=SC1090
    set -a
    . "$config"
    set +a
  fi
}

# Set conservative defaults — any value already in env (from config.env or
# shell) wins because we use `${VAR:=default}`.
husky_apply_defaults() {
  : "${HUSKY_WARN_MODE_ON_FEATURE:=1}"
  : "${HUSKY_WARN_PROTECTED_BRANCHES:=^(main|develop|release/.*|hotfix/.*)$}"
  : "${HUSKY_WARN_RETENTION:=5}"

  : "${HUSKY_PRECOMMIT_SECRETS:=0}"
  : "${HUSKY_PRECOMMIT_CLAIMS:=1}"
  : "${HUSKY_PRECOMMIT_ENV_FILES:=0}"
  : "${HUSKY_PRECOMMIT_LINT_STAGED:=0}"
  : "${HUSKY_PRECOMMIT_PRETTIER_STAGED:=0}"
  : "${HUSKY_PRECOMMIT_ESLINT_STAGED:=0}"
  : "${HUSKY_PRECOMMIT_STYLELINT_STAGED:=0}"
  : "${HUSKY_PRECOMMIT_TSC:=0}"
  : "${HUSKY_PRECOMMIT_SEMGREP_STAGED:=0}"
  : "${HUSKY_PRECOMMIT_LOCT_HEALTH:=0}"
  : "${HUSKY_PRECOMMIT_LOCT_SUPPRESSIONS:=0}"
  : "${HUSKY_PRECOMMIT_LOCT_SUPPRESSIONS_BUDGET:=30}"
  : "${HUSKY_PRECOMMIT_RUST_CARGO_CHECK:=0}"
  : "${HUSKY_PRECOMMIT_RUSTFMT_STAGED:=0}"
  : "${HUSKY_PRECOMMIT_PY_RUFF:=0}"
  : "${HUSKY_PRECOMMIT_PY_BLACK:=0}"
  : "${HUSKY_PRECOMMIT_SH_SHELLCHECK:=0}"
  : "${HUSKY_RUST_CARGO_DIR:=.}"

  : "${HUSKY_PREPUSH_PRETTIER_FULL:=0}"
  : "${HUSKY_PREPUSH_RUFF_FULL:=0}"
  : "${HUSKY_PREPUSH_SEMGREP_FULL:=0}"
  : "${HUSKY_PREPUSH_TSC:=0}"
  : "${HUSKY_PREPUSH_LOCT_CYCLES:=0}"
  : "${HUSKY_PREPUSH_LOCT_COMMANDS:=0}"
  : "${HUSKY_PREPUSH_VITEST:=0}"
  : "${HUSKY_PREPUSH_CARGO_CLIPPY:=0}"
  : "${HUSKY_PREPUSH_CARGO_TEST:=0}"
  : "${HUSKY_PREPUSH_SECRETS:=1}"

  : "${HUSKY_COMMIT_MSG_CONVENTIONAL:=0}"
  : "${HUSKY_COMMIT_MSG_ALLOW_AGENT_PREFIX:=1}"
  : "${HUSKY_COMMIT_MSG_REQUIRE_AGENT_RUNTIME:=0}"
  : "${HUSKY_COMMIT_MSG_SUBJECT_MAX:=100}"

  : "${HUSKY_PREMERGE_CLEAN_CODEX_AGENT:=0}"
  : "${HUSKY_PREMERGE_CLEAN_PATHS:=}"

  : "${HUSKY_POSTCOMMIT_CLAUDE_ARTIFACT_WARN:=0}"

  : "${HUSKY_EXCLUDE_PATHS:=node_modules/\ndist/\n.loctree/\ntarget/}"
  : "${HUSKY_BLOCKED_PATHS:=.env\n.env.local}"
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

husky_log()  { printf '%s\n' "$*"; }
husky_info() { printf 'ℹ️  %s\n' "$*"; }
husky_ok()   { printf '✅ %s\n' "$*"; }
husky_warn() { printf '⚠️  %s\n' "$*" >&2; }
husky_err()  { printf '❌ %s\n' "$*" >&2; }
husky_step() { printf '\n▸ %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Branch / mode detection
# ---------------------------------------------------------------------------

husky_current_branch() {
  git rev-parse --abbrev-ref HEAD 2>/dev/null || echo ""
}

husky_is_protected_branch() {
  local branch
  branch="$(husky_current_branch)"
  [ -n "$branch" ] || return 1
  echo "$branch" | grep -Eq "$HUSKY_WARN_PROTECTED_BRANCHES"
}

# WARN mode decision tree:
#   HUSKY_STRICT=1       → strict
#   HUSKY_WARN_FORCE=1   → warn
#   protected branch     → strict
#   HUSKY_WARN_MODE_ON_FEATURE=1 → warn
#   else                 → strict
husky_warn_mode_active() {
  if [ "${HUSKY_STRICT:-0}" = "1" ]; then return 1; fi
  if [ "${HUSKY_WARN_FORCE:-0}" = "1" ]; then return 0; fi
  if husky_is_protected_branch; then return 1; fi
  [ "${HUSKY_WARN_MODE_ON_FEATURE:-0}" = "1" ]
}

# ---------------------------------------------------------------------------
# Warns archive
# ---------------------------------------------------------------------------

husky_warns_dir() {
  mkdir -p "$HUSKY_WARNS_DIR"
  printf '%s' "$HUSKY_WARNS_DIR"
}

husky_hash_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    echo "no-sha256-tool"
  fi
}

husky_warns_archive() {
  local hook="$1"
  local log="$2"
  local signature
  local stamp

  signature="$(husky_hash_file "$log")"
  stamp="$(date +%Y%m%d-%H%M%S)"
  local archive
  archive="$(husky_warns_dir)/${hook}-${stamp}.log"

  {
    echo "hook=${hook}"
    echo "signature=${signature}"
    echo "captured_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "git_head=$(git rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "git_branch=$(husky_current_branch)"
    echo "---"
    cat "$log"
  } > "$archive"

  husky_warns_rotate "$hook"
  printf '%s' "$signature"
}

husky_warns_rotate() {
  local hook="$1"
  local dir
  dir="$(husky_warns_dir)"
  local count
  # shellcheck disable=SC2012  # hook names are alphanumeric+hyphen — ls is safe and faster than find -printf
  count="$(ls -1t "$dir"/"${hook}"-*.log 2>/dev/null | wc -l | tr -d ' ')"
  if [ "$count" -gt "$HUSKY_WARN_RETENTION" ]; then
    # shellcheck disable=SC2012
    ls -1t "$dir"/"${hook}"-*.log 2>/dev/null \
      | tail -n +$((HUSKY_WARN_RETENTION + 1)) \
      | while IFS= read -r old; do
          rm -f "$old"
        done
  fi
}

husky_warns_signature_pending() {
  # Returns 0 if the given signature appears in any pending warn log for this hook.
  local hook="$1"
  local signature="$2"
  local dir
  dir="$(husky_warns_dir)"
  if ! ls -1 "$dir"/"${hook}"-*.log >/dev/null 2>&1; then
    return 1
  fi
  grep -lq "^signature=${signature}$" "$dir"/"${hook}"-*.log 2>/dev/null
}

husky_warns_clear_for_hook() {
  local hook="$1"
  local dir
  dir="$(husky_warns_dir)"
  rm -f "$dir"/"${hook}"-*.log 2>/dev/null || true
}

husky_warns_print_backlog() {
  local hook="$1"
  local dir
  dir="$(husky_warns_dir)"
  if ! ls -1 "$dir"/"${hook}"-*.log >/dev/null 2>&1; then
    return 0
  fi
  husky_info "Pending warns for $hook (latest):"
  # shellcheck disable=SC2012
  ls -1t "$dir"/"${hook}"-*.log 2>/dev/null | head -3 | while IFS= read -r f; do
    local sig
    sig="$(grep '^signature=' "$f" | head -1 | cut -d= -f2-)"
    husky_log "    • $(basename "$f")  sig=${sig:0:12}…"
  done
}

# ---------------------------------------------------------------------------
# Step runner
# ---------------------------------------------------------------------------

# husky_run_step <label> <cmd> [args...]
# Returns 0 on success or warn-demoted failure, non-zero on strict failure.
# Tracks STEP_LAST_FAILED + STEP_FAILURE_COUNT for the caller.
STEP_LAST_FAILED=0
STEP_FAILURE_COUNT=0
# IMPORTANT bash gotchas — two of them, both burned us once:
#
#   1. After a non-taken `if cmd; then ... fi` (no else branch), `$?`
#      reflects the exit of `fi` itself (always 0), NOT of `cmd`. Don't
#      refactor to `if "$@"; then ... fi; rc=$?` — that silently makes
#      every step appear successful.
#
#   2. With `set -e` active in the enclosing subshell, `"$@"` returning
#      non-zero immediately exits the shell, BEFORE the next statement
#      (the `rc=$?` capture) can run. We use `"$@" || rc=$?` so that the
#      non-zero return is suppressed at this site (via `||`) while still
#      being captured into `rc` — set -e does not trigger for the
#      left-hand side of `||`.
husky_run_step() {
  local label="$1"
  shift
  husky_step "$label"
  local rc=0
  "$@" || rc=$?
  if [ "$rc" -eq 0 ]; then
    STEP_LAST_FAILED=0
    return 0
  fi
  STEP_LAST_FAILED=1
  STEP_FAILURE_COUNT=$((STEP_FAILURE_COUNT + 1))
  if husky_warn_mode_active; then
    husky_warn "[$label] failed with exit $rc — demoted to warning (WARN mode)."
    return 0
  fi
  husky_err "[$label] failed with exit $rc — blocking commit/push (strict mode)."
  return "$rc"
}

# Strict variant — ALWAYS fails the hook, regardless of WARN mode.
#
# Reserved for steps where demotion to a warning would be unsafe: a leaked
# secret or a staged .env file cannot be "warned about and let through" on
# a feature branch — the credential is in the commit object either way, and
# branches get force-pushed / cherry-picked / merged without re-running the
# guard. Use this for guards where the failure mode is permanent damage,
# not just a quality regression.
#
# Communication with the hook tail logic: this function runs inside the
# subshell that pipes through redact + tee, so an exported variable would
# not propagate back to the outer hook. Instead we write a marker to
# $HUSKY_STRICT_FAILED_FILE (tempfile created by the hook entry point).
# The tail logic reads that file and refuses WARN-mode override when set.
husky_run_strict_step() {
  local label="$1"
  shift
  husky_step "$label (strict — non-demotable)"
  # See husky_run_step for why we use `|| rc=$?` instead of capturing
  # `$?` directly — set -e would otherwise exit the subshell before the
  # marker file gets written, defeating the whole point of strict-mode.
  local rc=0
  "$@" || rc=$?
  if [ "$rc" -eq 0 ]; then
    STEP_LAST_FAILED=0
    return 0
  fi
  STEP_LAST_FAILED=1
  STEP_FAILURE_COUNT=$((STEP_FAILURE_COUNT + 1))
  if [ -n "${HUSKY_STRICT_FAILED_FILE:-}" ]; then
    echo 1 > "$HUSKY_STRICT_FAILED_FILE"
  fi
  husky_err "[$label] failed with exit $rc — STRICT step, no WARN-mode demotion."
  return "$rc"
}

# Read the strict-failure marker. Returns 0 (true) if any strict step
# failed during this hook run, 1 (false) otherwise.
husky_strict_failed() {
  [ -n "${HUSKY_STRICT_FAILED_FILE:-}" ] || return 1
  [ -s "$HUSKY_STRICT_FAILED_FILE" ] || return 1
  local val
  val="$(cat "$HUSKY_STRICT_FAILED_FILE" 2>/dev/null)"
  [ "$val" = "1" ]
}

# Step that is allowed to fail with a warning regardless of mode.
husky_run_advisory() {
  local label="$1"
  shift
  husky_step "$label (advisory)"
  if "$@"; then
    return 0
  fi
  husky_warn "[$label] non-zero exit — informational, continuing."
  return 0
}

# ---------------------------------------------------------------------------
# Exclude / blocklist predicates
# ---------------------------------------------------------------------------

husky_is_excluded() {
  local path="$1"
  printf '%s\n' "${HUSKY_EXCLUDE_PATHS}" | while IFS= read -r pattern; do
    pattern="$(printf '%s' "$pattern" | sed 's/[[:space:]]*$//')"
    [ -z "$pattern" ] && continue
    case "$path" in
      $pattern*) return 1 ;;
    esac
  done
  return 1
}

# Returns 0 if path matches any blocklist glob.
husky_is_blocked() {
  local path="$1"
  local pattern
  while IFS= read -r pattern; do
    pattern="$(printf '%s' "$pattern" | sed 's/[[:space:]]*$//')"
    [ -z "$pattern" ] && continue
    # shellcheck disable=SC2254  # patterns are intentionally globbed
    case "$path" in
      $pattern) return 0 ;;
    esac
  done <<< "${HUSKY_BLOCKED_PATHS}"
  return 1
}

# ---------------------------------------------------------------------------
# Local hook extension dispatch
# ---------------------------------------------------------------------------

# husky_run_local_extensions <hook-name>
# Runs any `.husky/local/<hook-name>.d/*.sh` scripts (sorted lexicographically).
# Each script is run with `bash`, stops the chain on non-zero unless WARN mode.
husky_run_local_extensions() {
  local hook="$1"
  local dir="$HUSKY_LOCAL_HOOK_DIR_BASE/${hook}.d"
  [ -d "$dir" ] || return 0
  local script
  for script in "$dir"/*.sh; do
    [ -f "$script" ] || continue
    husky_run_step "local:$(basename "$script")" bash "$script" || return $?
  done
}

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

husky_init() {
  husky_load_config
  husky_apply_defaults
  cd "$HUSKY_REPO_ROOT"
}
