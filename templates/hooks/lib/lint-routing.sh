# shellcheck shell=bash
# vibecrafted-husky-template :: lib/lint-routing.sh
#
# Routes staged / full-repo files to the appropriate formatter / linter.
# Each function returns 0 on success or no-op, non-zero on hard failure.

# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

# husky_lint_staged_files_by_glob <ext-glob>
# Emits one filename per line, NUL-separated when used with `xargs -0`.
husky_lint_staged_files_by_glob() {
  local pattern="$1"
  git diff --cached --name-only --diff-filter=ACMR | grep -E "$pattern" || true
}

husky_lint_materialize_index() {
  local tmp
  tmp="$(mktemp -d -t husky-index.XXXXXX)"
  git checkout-index --all --prefix="$tmp/" >/dev/null
  printf '%s\n' "$tmp"
}

husky_lint_write_projected_files_to_index() {
  local root="$1"
  shift
  local file entry mode oid
  for file in "$@"; do
    entry="$(git ls-files -s -- "$file")"
    mode="${entry%% *}"
    [ -n "$mode" ] || { husky_err "Cannot resolve staged mode for $file"; return 1; }
    oid="$(git hash-object -w -- "$root/$file")" || return 1
    git update-index --cacheinfo "$mode" "$oid" "$file" || return 1
  done
}

# ---------------------------------------------------------------------------
# Prettier
# ---------------------------------------------------------------------------

husky_lint_prettier_staged() {
  local files root rc=0
  files="$(husky_lint_staged_files_by_glob '\.(ts|tsx|js|jsx|json|css|md|yaml|yml)$')"
  if [ -z "$files" ]; then
    husky_info "No staged files for Prettier."
    return 0
  fi
  local paths=() projected=() file
  while IFS= read -r file; do paths+=("$file"); done <<< "$files"
  root="$(husky_lint_materialize_index)"
  for file in "${paths[@]}"; do projected+=("$root/$file"); done
  npx --no-install prettier --write -- "${projected[@]}" || rc=$?
  [ "$rc" -eq 0 ] && husky_lint_write_projected_files_to_index "$root" "${paths[@]}" || rc=$?
  rm -rf "$root"
  [ "$rc" -eq 0 ] || { husky_err "Prettier staged-index format failed."; return "$rc"; }
}

husky_lint_prettier_full() {
  local root="${HUSKY_GATE_ROOT:-.}"
  local ignore=()
  [ ! -f "$root/.prettierignore" ] || ignore=(--ignore-path "$root/.prettierignore")
  npx --no-install prettier --check "${ignore[@]}" "$root" \
    || { husky_err "Prettier full-repo check failed."; return 1; }
}

# ---------------------------------------------------------------------------
# ESLint
# ---------------------------------------------------------------------------

husky_lint_eslint_staged() {
  local files root rc=0
  files="$(husky_lint_staged_files_by_glob '\.(ts|tsx|js|jsx)$')"
  if [ -z "$files" ]; then
    husky_info "No staged files for ESLint."
    return 0
  fi
  local paths=() projected=() file
  while IFS= read -r file; do paths+=("$file"); done <<< "$files"
  root="$(husky_lint_materialize_index)"
  for file in "${paths[@]}"; do projected+=("$root/$file"); done
  npx --no-install eslint --fix --max-warnings=0 -- "${projected[@]}" || rc=$?
  [ "$rc" -eq 0 ] && husky_lint_write_projected_files_to_index "$root" "${paths[@]}" || rc=$?
  rm -rf "$root"
  [ "$rc" -eq 0 ] || { husky_err "ESLint staged-index fix failed."; return "$rc"; }
}

# ---------------------------------------------------------------------------
# Stylelint
# ---------------------------------------------------------------------------

husky_lint_stylelint_staged() {
  local files root rc=0
  files="$(husky_lint_staged_files_by_glob '\.(css|scss)$')"
  if [ -z "$files" ]; then
    husky_info "No staged files for Stylelint."
    return 0
  fi
  local paths=() projected=() file
  while IFS= read -r file; do paths+=("$file"); done <<< "$files"
  root="$(husky_lint_materialize_index)"
  for file in "${paths[@]}"; do projected+=("$root/$file"); done
  npx --no-install stylelint --fix --allow-empty-input -- "${projected[@]}" || rc=$?
  [ "$rc" -eq 0 ] && husky_lint_write_projected_files_to_index "$root" "${paths[@]}" || rc=$?
  rm -rf "$root"
  [ "$rc" -eq 0 ] || { husky_err "Stylelint staged-index fix failed."; return "$rc"; }
}

# ---------------------------------------------------------------------------
# TypeScript
# ---------------------------------------------------------------------------

husky_lint_tsc_full() {
  if [ -f tsconfig.json ]; then
    npx --no-install tsc --noEmit --skipLibCheck \
      || { husky_err "tsc --noEmit failed."; return 1; }
  else
    husky_info "No tsconfig.json — skipping tsc."
  fi
}

# ---------------------------------------------------------------------------
# Semgrep
# ---------------------------------------------------------------------------

husky_lint_semgrep_staged() {
  local files
  files="$(git diff --cached --name-only --diff-filter=ACMR)"
  [ -z "$files" ] && { husky_info "No staged files for Semgrep."; return 0; }
  if ! command -v semgrep >/dev/null 2>&1; then
    husky_warn "semgrep not installed — skipping (install: pipx install semgrep)."
    return 0
  fi
  # Build NUL-separated list and pipe to semgrep. `env -u PYTHONPATH
  # -u PYTHONHOME` because a caller that exports a runtime's own python-site
  # (Vibecrafted does) shadows pysemgrep's jsonschema/rpds and the scanner dies
  # on import — a crash the WARN-mode counter reports as a warning, which is
  # how a security gate quietly stops being one.
  printf '%s\n' "$files" | tr '\n' '\0' \
    | xargs -0 env -u PYTHONPATH -u PYTHONHOME semgrep scan --config auto --quiet --error \
    || { husky_err "Semgrep found issues on staged files."; return 1; }
}

husky_lint_semgrep_full() {
  if ! command -v semgrep >/dev/null 2>&1; then
    husky_warn "semgrep not installed — skipping full scan."
    return 0
  fi
  env -u PYTHONPATH -u PYTHONHOME semgrep scan --config auto --quiet --error -- "${HUSKY_GATE_ROOT:-.}" \
    || { husky_err "Semgrep full-repo scan failed."; return 1; }
}

# ---------------------------------------------------------------------------
# Loctree
# ---------------------------------------------------------------------------

husky_loct_bin() {
  if command -v loct >/dev/null 2>&1; then echo "loct"
  elif command -v loctree >/dev/null 2>&1; then echo "loctree"
  else echo ""; fi
}

husky_lint_loct_health() {
  local bin
  bin="$(husky_loct_bin)"
  if [ -z "$bin" ]; then
    husky_warn "loct/loctree not installed — skipping health check."
    return 0
  fi
  "$bin" health --project "$HUSKY_REPO_ROOT" >/dev/null 2>&1 \
    || husky_warn "loctree reported structural concerns. Run \`$bin health\` for detail."
  return 0
}

husky_lint_loct_suppressions() {
  local bin
  bin="$(husky_loct_bin)"
  if [ -z "$bin" ]; then
    husky_warn "loct/loctree not installed — skipping suppressions check."
    return 0
  fi
  local total
  total="$("$bin" suppressions --json 2>/dev/null \
    | node -e 'const d=JSON.parse(require("fs").readFileSync(0)); console.log(Array.isArray(d)?d.length:0)' 2>/dev/null \
    || echo 0)"
  if [ "$total" -gt "$HUSKY_PRECOMMIT_LOCT_SUPPRESSIONS_BUDGET" ]; then
    husky_err "Silencer budget exceeded: $total > $HUSKY_PRECOMMIT_LOCT_SUPPRESSIONS_BUDGET"
    husky_err "Run \`$bin suppressions --summary\` to see the inventory."
    return 1
  fi
  husky_info "Silencer budget OK: $total / $HUSKY_PRECOMMIT_LOCT_SUPPRESSIONS_BUDGET"
}

husky_lint_loct_cycles() {
  local bin
  bin="$(husky_loct_bin)"
  if [ -z "$bin" ]; then
    husky_warn "loct/loctree not installed — skipping cycles check."
    return 0
  fi
  "$bin" cycles \
    || { husky_err "Circular imports detected — run \`$bin cycles\` for detail."; return 1; }
}

husky_lint_loct_commands() {
  local bin
  bin="$(husky_loct_bin)"
  if [ -z "$bin" ]; then
    husky_warn "loct/loctree not installed — skipping commands check."
    return 0
  fi
  "$bin" commands || husky_warn "FE↔BE contract issues (informational, non-blocking)."
  return 0
}

# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

husky_lint_rustfmt_staged() {
  local files root rc=0
  files="$(husky_lint_staged_files_by_glob '\.rs$')"
  if [ -z "$files" ]; then
    husky_info "No staged Rust files."
    return 0
  fi
  if ! command -v rustfmt >/dev/null 2>&1; then
    husky_warn "rustfmt not installed — skipping."
    return 0
  fi
  local paths=() f
  while IFS= read -r f; do paths+=("$f"); done <<< "$files"
  root="$(husky_lint_materialize_index)"
  for f in "${paths[@]}"; do
    [ -z "$f" ] && continue
    rustfmt --edition 2024 "$root/$f" || rustfmt "$root/$f" || { rc=1; break; }
  done
  [ "$rc" -eq 0 ] && husky_lint_write_projected_files_to_index "$root" "${paths[@]}" || rc=$?
  rm -rf "$root"
  return "$rc"
}

husky_lint_cargo_check() {
  if [ ! -f "$HUSKY_RUST_CARGO_DIR/Cargo.toml" ]; then
    husky_info "No Cargo.toml at $HUSKY_RUST_CARGO_DIR — skipping cargo check."
    return 0
  fi
  ( cd "$HUSKY_RUST_CARGO_DIR" && cargo check --quiet ) \
    || { husky_err "cargo check failed."; return 1; }
}

husky_lint_cargo_clippy() {
  if [ ! -f "$HUSKY_RUST_CARGO_DIR/Cargo.toml" ]; then
    husky_info "No Cargo.toml at $HUSKY_RUST_CARGO_DIR — skipping clippy."
    return 0
  fi
  ( cd "$HUSKY_RUST_CARGO_DIR" && cargo clippy --quiet -- -D warnings ) \
    || { husky_err "cargo clippy failed."; return 1; }
}

husky_lint_cargo_test() {
  if [ ! -f "$HUSKY_RUST_CARGO_DIR/Cargo.toml" ]; then
    husky_info "No Cargo.toml at $HUSKY_RUST_CARGO_DIR — skipping cargo test."
    return 0
  fi
  ( cd "$HUSKY_RUST_CARGO_DIR" && cargo test --quiet ) \
    || { husky_err "cargo test failed."; return 1; }
}

# ---------------------------------------------------------------------------
# Python (ruff / black)
# ---------------------------------------------------------------------------

husky_lint_py_ruff_staged() {
  local files root rc=0
  files="$(husky_lint_staged_files_by_glob '\.py$')"
  [ -z "$files" ] && { husky_info "No staged Python files."; return 0; }
  local ruff
  if command -v ruff >/dev/null 2>&1; then ruff="ruff"
  elif command -v uvx >/dev/null 2>&1; then ruff="uvx ruff"
  else husky_warn "ruff/uvx not installed — skipping."; return 0
  fi
  local paths=() projected=() file
  while IFS= read -r file; do paths+=("$file"); done <<< "$files"
  root="$(husky_lint_materialize_index)"
  for file in "${paths[@]}"; do projected+=("$root/$file"); done
  # shellcheck disable=SC2086  # $ruff intentionally splits "uvx ruff"
  $ruff check --fix -- "${projected[@]}" || rc=$?
  # shellcheck disable=SC2086
  [ "$rc" -ne 0 ] || $ruff format -- "${projected[@]}" || rc=$?
  [ "$rc" -eq 0 ] && husky_lint_write_projected_files_to_index "$root" "${paths[@]}" || rc=$?
  rm -rf "$root"
  [ "$rc" -eq 0 ] || { husky_err "ruff staged-index format failed."; return "$rc"; }
}

husky_lint_py_ruff_full() {
  local root="${HUSKY_GATE_ROOT:-.}"
  local ruff
  if command -v ruff >/dev/null 2>&1; then ruff="ruff"
  elif command -v uvx >/dev/null 2>&1; then ruff="uvx ruff"
  else husky_warn "ruff/uvx not installed — skipping."; return 0
  fi
  # shellcheck disable=SC2086
  $ruff check -- "$root" || return 1
  # shellcheck disable=SC2086
  $ruff format --check -- "$root"
}

husky_lint_push_object_gates() {
  local commit="$1" root rc=0
  root="$(mktemp -d -t husky-push-tree.XXXXXX)"
  git archive "$commit" | tar -xf - -C "$root" || rc=$?
  if [ "$rc" -eq 0 ]; then
    HUSKY_GATE_ROOT="$root"
    export HUSKY_GATE_ROOT
    [ "$HUSKY_PREPUSH_PRETTIER_FULL" != "1" ] || husky_lint_prettier_full || rc=$?
    [ "$rc" -ne 0 ] || [ "$HUSKY_PREPUSH_RUFF_FULL" != "1" ] || husky_lint_py_ruff_full || rc=$?
    [ "$rc" -ne 0 ] || [ "$HUSKY_PREPUSH_SEMGREP_FULL" != "1" ] || husky_lint_semgrep_full || rc=$?
    unset HUSKY_GATE_ROOT
  fi
  rm -rf "$root"
  return "$rc"
}

# ---------------------------------------------------------------------------
# Shell (shellcheck)
# ---------------------------------------------------------------------------

husky_lint_sh_shellcheck_staged() {
  local files
  files="$(husky_lint_staged_files_by_glob '\.(sh|bash|zsh)$')"
  [ -z "$files" ] && { husky_info "No staged shell files."; return 0; }
  if ! command -v shellcheck >/dev/null 2>&1; then
    husky_warn "shellcheck not installed — skipping."
    return 0
  fi
  printf '%s\n' "$files" | while IFS= read -r f; do
    [ -z "$f" ] && continue
    shellcheck "$f" || return 1
  done
}

# ---------------------------------------------------------------------------
# Vitest
# ---------------------------------------------------------------------------

husky_lint_vitest() {
  if [ ! -f vitest.config.ts ] && [ ! -f vitest.config.js ] && ! grep -q '"vitest"' package.json 2>/dev/null; then
    husky_info "No vitest config — skipping."
    return 0
  fi
  npx --no-install vitest run --reporter=dot \
    || { husky_err "vitest failed."; return 1; }
}

# ---------------------------------------------------------------------------
# lint-staged
# ---------------------------------------------------------------------------

husky_lint_lint_staged() {
  if [ ! -f package.json ]; then return 0; fi
  if ! grep -q '"lint-staged"' package.json 2>/dev/null; then
    husky_info "No lint-staged config in package.json — skipping."
    return 0
  fi
  husky_err "lint-staged is disabled on a Living Tree because arbitrary tasks mutate the working copy. Enable the individual staged-index formatters instead."
  return 1
}
