#!/usr/bin/env bash
# Skill-loader integration test gate.
#
# Verifies that every shipped skill loads cleanly from a fresh ~/.vibecrafted/
# baseline: SKILL.md presence, YAML frontmatter sanity, foundations wrappers,
# cross-shell helper sourcing (bash + zsh), and `make doctor` health output.
#
# Exit 0 on full pass. Nonzero with explicit failure summary on any check fail.
#
# Usage:
#   tests/skill_loader_smoke.sh                       # full smoke
#   tests/skill_loader_smoke.sh --negative-fixture-only  # negative path only
#   tests/skill_loader_smoke.sh --doctor-preverified  # owning workflow already ran doctor
#
# The negative-fixture mode verifies the smoke catches a deliberately corrupt
# SKILL.md (missing closing frontmatter delimiter) — the falsifier per
# audit-22 Phase 4B discipline.

set -euo pipefail

# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILLS_DIR="$REPO_ROOT/vibecrafted-core/vibecrafted_core/skills"
EXPERIMENTAL_DIR="$SKILLS_DIR/experimental"
FOUNDATIONS_DIR="$SKILLS_DIR/foundations"
FIXTURES_DIR="$SCRIPT_DIR/fixtures"
HELPER_SHIM="${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/shell/vc-skills.sh"

EXPECTED_HELPER_FUNCTIONS=(vc-init vc-help vc-research vc-agents)

NEGATIVE_ONLY=0
DOCTOR_PREVERIFIED=0
for arg in "$@"; do
  case "$arg" in
    --negative-fixture-only) NEGATIVE_ONLY=1 ;;
    --doctor-preverified) DOCTOR_PREVERIFIED=1 ;;
    *)
      printf 'unknown option: %s\n' "$arg" >&2
      exit 2
      ;;
  esac
done

FAILURES=()
WARNINGS=()
PASSES=0

red() { printf '\033[31m%s\033[0m' "$*"; }
green() { printf '\033[32m%s\033[0m' "$*"; }
yellow() { printf '\033[33m%s\033[0m' "$*"; }
dim() { printf '\033[2m%s\033[0m' "$*"; }

log_pass() {
  PASSES=$((PASSES + 1))
  printf '  [%s] %s\n' "$(green ok)" "$1"
}

log_warn() {
  WARNINGS+=("$1")
  printf '  [%s] %s\n' "$(yellow warn)" "$1"
}

log_fail() {
  FAILURES+=("$1")
  printf '  [%s] %s\n' "$(red FAIL)" "$1"
}

# -----------------------------------------------------------------------------
# Frontmatter check (returns 0 if valid, 1 otherwise; prints reason on stderr)
# -----------------------------------------------------------------------------

check_frontmatter() {
  local skill_md="$1"
  local first_line
  if [[ ! -f "$skill_md" ]]; then
    echo "missing file" >&2
    return 1
  fi
  first_line="$(head -n 1 "$skill_md")"
  if [[ "$first_line" != "---" ]]; then
    echo "no opening --- delimiter" >&2
    return 1
  fi
  # Find a closing --- after line 1. Must exist somewhere in the file.
  local closing_line
  closing_line="$(awk 'NR==1 && $0 == "---" { next } $0 == "---" { print NR; exit }' "$skill_md")"
  if [[ -z "$closing_line" ]]; then
    echo "no closing --- delimiter" >&2
    return 1
  fi
  # Extract the YAML frontmatter block (between the delimiters).
  local frontmatter
  frontmatter="$(awk -v end="$closing_line" 'NR>1 && NR<end' "$skill_md")"
  if ! printf '%s\n' "$frontmatter" | grep -qE '^name:[[:space:]]*[^[:space:]]'; then
    echo "missing or empty 'name:' key" >&2
    return 1
  fi
  # description: may be multi-line (folded scalar with >), so just check key.
  if ! printf '%s\n' "$frontmatter" | grep -qE '^description:'; then
    echo "missing 'description:' key" >&2
    return 1
  fi
  return 0
}

# -----------------------------------------------------------------------------
# Negative-fixture mode: verify smoke catches a corrupt SKILL.md
# -----------------------------------------------------------------------------

run_negative_fixture() {
  local fixture="$FIXTURES_DIR/corrupt_skill/SKILL.md"
  printf '\n%s\n' "$(dim '─── negative fixture (falsifier) ───')"
  if [[ ! -f "$fixture" ]]; then
    log_fail "negative fixture missing at $fixture"
    return 1
  fi
  local reason
  if reason="$(check_frontmatter "$fixture" 2>&1 >/dev/null)"; then
    log_fail "negative fixture passed frontmatter check — smoke is BROKEN, can't catch malformed skills"
    return 1
  else
    log_pass "negative fixture correctly rejected: $reason"
    return 0
  fi
}

if (( NEGATIVE_ONLY )); then
  printf '%s\n' "$(dim '── skill-loader smoke (negative-fixture-only) ──')"
  if run_negative_fixture; then
    printf '\n%s — falsifier proves the smoke is real.\n' "$(green PASS)"
    exit 0
  else
    printf '\n%s — falsifier failed.\n' "$(red FAIL)"
    exit 1
  fi
fi

# -----------------------------------------------------------------------------
# Phase 1 — vc-* skills
# -----------------------------------------------------------------------------

printf '%s\n' "$(dim '── skill-loader smoke ──')"
printf '\n%s\n' "$(dim '─── phase 1: vc-* skills ───')"

shopt -s nullglob
skill_count=0
for skill_dir in "$SKILLS_DIR"/vc-*/; do
  skill_count=$((skill_count + 1))
  skill_name="$(basename "$skill_dir")"
  skill_md="$skill_dir/SKILL.md"
  if [[ ! -f "$skill_md" ]]; then
    log_fail "skill:$skill_name: SKILL.md missing"
    continue
  fi
  if reason="$(check_frontmatter "$skill_md" 2>&1 >/dev/null)"; then
    log_pass "skill:$skill_name: SKILL.md frontmatter valid"
  else
    log_fail "skill:$skill_name: $reason"
  fi
done
shopt -u nullglob

if (( skill_count == 0 )); then
  log_fail "phase 1: no vc-* skill directories discovered under $SKILLS_DIR"
elif (( skill_count < 20 )); then
  log_warn "phase 1: only $skill_count skills discovered (expected ≥20)"
fi

# -----------------------------------------------------------------------------
# Phase 2 — experimental skills (skip-with-warn if empty)
# -----------------------------------------------------------------------------

printf '\n%s\n' "$(dim '─── phase 2: experimental skills ───')"

if [[ ! -d "$EXPERIMENTAL_DIR" ]]; then
  log_warn "experimental dir missing — skipping"
else
  shopt -s nullglob
  exp_count=0
  for skill_dir in "$EXPERIMENTAL_DIR"/*/; do
    exp_count=$((exp_count + 1))
    skill_name="experimental/$(basename "$skill_dir")"
    skill_md="$skill_dir/SKILL.md"
    if [[ ! -f "$skill_md" ]]; then
      log_fail "skill:$skill_name: SKILL.md missing"
      continue
    fi
    if reason="$(check_frontmatter "$skill_md" 2>&1 >/dev/null)"; then
      log_pass "skill:$skill_name: SKILL.md frontmatter valid"
    else
      log_fail "skill:$skill_name: $reason"
    fi
  done
  shopt -u nullglob
  if (( exp_count == 0 )); then
    log_warn "experimental dir empty — skip-with-warn"
  fi
fi

# -----------------------------------------------------------------------------
# Phase 3 — foundations wrappers
# -----------------------------------------------------------------------------

printf '\n%s\n' "$(dim '─── phase 3: foundation wrappers ───')"

tracked_foundations="$(git -C "$REPO_ROOT" ls-files "$FOUNDATIONS_DIR" 2>/dev/null || true)"
if [[ -z "$tracked_foundations" ]]; then
  log_pass "foundation wrappers: repo no longer ships skills/foundations; installer/doctor owns foundation checks"
elif [[ ! -d "$FOUNDATIONS_DIR" ]]; then
  log_fail "tracked foundations dir missing at $FOUNDATIONS_DIR"
else
  shopt -s nullglob
  foundation_count=0
  for foundation_dir in "$FOUNDATIONS_DIR"/*/; do
    foundation_count=$((foundation_count + 1))
    name="foundation/$(basename "$foundation_dir")"
    if [[ -d "$foundation_dir" ]]; then
      log_pass "$name: wrapper dir present"
    else
      log_fail "$name: missing"
    fi
  done
  shopt -u nullglob
  if (( foundation_count < 4 )); then
    log_warn "foundations: only $foundation_count discovered (expected ≥4)"
  fi
fi

# -----------------------------------------------------------------------------
# Phase 4 — cross-shell helper sourcing
# -----------------------------------------------------------------------------

printf '\n%s\n' "$(dim '─── phase 4: helper sourcing (bash + zsh) ───')"

if [[ ! -f "$HELPER_SHIM" ]]; then
  log_warn "helper shim missing at $HELPER_SHIM — install may not have run; skipping cross-shell check"
else
  for shell_name in bash zsh; do
    if ! command -v "$shell_name" >/dev/null 2>&1; then
      log_warn "helper:$shell_name: shell binary not found — skipping"
      continue
    fi
    # Source the helper in a sub-shell and verify at least one expected function
    # is registered. Use -c so the sub-shell stays non-interactive and clean.
    local_check="source \"$HELPER_SHIM\" >/dev/null 2>&1; ok=0; for fn in ${EXPECTED_HELPER_FUNCTIONS[*]}; do if typeset -f \"\$fn\" >/dev/null 2>&1 || type -t \"\$fn\" 2>/dev/null | grep -q function; then ok=1; break; fi; done; exit \$(( ok ? 0 : 1 ))"
    if "$shell_name" -c "$local_check"; then
      log_pass "helper:$shell_name: shim sourced and at least one vc-* function exported"
    else
      log_fail "helper:$shell_name: shim failed to register any of: ${EXPECTED_HELPER_FUNCTIONS[*]}"
    fi
  done
fi

# -----------------------------------------------------------------------------
# Phase 5 — make doctor health gate
# -----------------------------------------------------------------------------

printf '\n%s\n' "$(dim '─── phase 5: make doctor health ───')"

doctor_log=""
if (( DOCTOR_PREVERIFIED )); then
  log_pass "doctor: preverified by the owning install workflow"
else
  doctor_log="$(mktemp -t vc-doctor.XXXXXX)"
  trap 'rm -f "$doctor_log"' EXIT

  doctor_exit=0
  if (cd "$REPO_ROOT" && make doctor) >"$doctor_log" 2>&1; then
    doctor_exit=0
  else
    doctor_exit=$?
  fi

# Doctor is summary-first (CLI_PRODUCT_SPEC §6.4): parse the verdict-line
# counts ("N ok   M warnings   K failures") instead of counting bracket tags.
# The ok/warning split is host-dependent: a clean CI runner reports optional
# install surfaces as warnings, while an installed workstation reports them as
# passing checks. The stable health contract is a parseable verdict with at
# least one passing check, zero failures, and a zero command exit.
  summary_line="$(grep -m1 -E '[0-9]+ ok.*[0-9]+ warnings.*[0-9]+ failures' "$doctor_log" || true)"
  ok_count="$(grep -oE '[0-9]+ ok' "$doctor_log" | head -n1 | grep -oE '[0-9]+' || true)"
  warn_count="$(grep -oE '[0-9]+ warnings' "$doctor_log" | head -n1 | grep -oE '[0-9]+' || true)"
  fail_count="$(grep -oE '[0-9]+ failures' "$doctor_log" | head -n1 | grep -oE '[0-9]+' || true)"
  ok_count="${ok_count:-0}"
  warn_count="${warn_count:-0}"
  fail_count="${fail_count:-0}"

  if [[ -z "$summary_line" ]]; then
    log_fail "doctor: summary line missing or unparseable"
  elif (( fail_count > 0 )); then
    log_fail "doctor: $fail_count failures reported"
    tail -n 80 "$doctor_log" >&2
  elif (( doctor_exit != 0 )); then
    log_fail "doctor: exited $doctor_exit despite a zero-failure summary"
  elif (( ok_count == 0 )); then
    log_fail "doctor: summary reports no passing checks"
  else
    log_pass "doctor: $ok_count ok / $warn_count warnings / $fail_count failures"
  fi

  if (( warn_count > 0 )); then
    log_warn "doctor reports $warn_count warning(s) — review $doctor_log"
  fi
fi

# -----------------------------------------------------------------------------
# Phase 6 — negative fixture as part of full run
# -----------------------------------------------------------------------------

printf '\n%s\n' "$(dim '─── phase 6: negative fixture (falsifier) ───')"
if [[ -f "$FIXTURES_DIR/corrupt_skill/SKILL.md" ]]; then
  if check_frontmatter "$FIXTURES_DIR/corrupt_skill/SKILL.md" >/dev/null 2>&1; then
    log_fail "falsifier: corrupt SKILL.md was accepted — smoke is BROKEN"
  else
    log_pass "falsifier: corrupt SKILL.md correctly rejected"
  fi
else
  log_warn "falsifier: fixture missing at $FIXTURES_DIR/corrupt_skill/SKILL.md"
fi

# -----------------------------------------------------------------------------
# Phase 7 — scaffolder (Plan 04) positive + negative
#
# Verifies tools/vc-skill-new.sh:
#   (a) scaffolds a temp skill whose SKILL.md passes the same frontmatter
#       check as every shipped skill,
#   (b) rejects an invalid name with nonzero exit and emits no skill dir.
#
# The phase-1 glob is the package-owned `skills/vc-*/` — that means the
# `_template/` scaffold source is auto-skipped (it does not match
# the prefix). The phase below is the explicit gate on the scaffolder
# tooling itself rather than on its output as cargo-cult.
# -----------------------------------------------------------------------------

printf '\n%s\n' "$(dim '─── phase 7: scaffolder (vc-skill-new.sh) ───')"

SCAFFOLDER="$REPO_ROOT/tools/vc-skill-new.sh"
SCAFFOLD_TEMPLATE_DIR="$SKILLS_DIR/_template"

if [[ ! -x "$SCAFFOLDER" ]]; then
  log_fail "scaffolder: $SCAFFOLDER missing or not executable"
elif [[ ! -d "$SCAFFOLD_TEMPLATE_DIR" ]]; then
  log_fail "scaffolder: template missing at $SCAFFOLD_TEMPLATE_DIR"
else
  # Pick a unique throwaway name. Suffix uses $$ + epoch so concurrent
  # smoke runs don't collide.
  SCAFFOLD_TMP_NAME="vc-smoke-tmp-$$-$(date +%s)"
  SCAFFOLD_TMP_DIR="$SKILLS_DIR/$SCAFFOLD_TMP_NAME"

  # Cleanup: always remove the scaffolded throwaway, even on early exit.
  cleanup_scaffold_tmp() {
    if [[ -d "$SCAFFOLD_TMP_DIR" ]]; then
      rm -rf "$SCAFFOLD_TMP_DIR"
    fi
  }
  # Chain the cleanup onto the existing trap target (doctor log) so neither
  # handler clobbers the other.
  trap '[[ -z "$doctor_log" ]] || rm -f "$doctor_log"; cleanup_scaffold_tmp' EXIT

  # (a) Positive path: scaffold + verify frontmatter parses.
  if scaffold_output="$("$SCAFFOLDER" "$SCAFFOLD_TMP_NAME" 2>&1)"; then
    if [[ -f "$SCAFFOLD_TMP_DIR/SKILL.md" ]]; then
      if reason="$(check_frontmatter "$SCAFFOLD_TMP_DIR/SKILL.md" 2>&1 >/dev/null)"; then
        log_pass "scaffolder: scaffolded $SCAFFOLD_TMP_NAME and SKILL.md frontmatter valid"
      else
        log_fail "scaffolder: scaffolded SKILL.md failed frontmatter check: $reason"
      fi
      # Verify placeholders were substituted (no stray '{{' tokens left).
      if grep -rq '{{' "$SCAFFOLD_TMP_DIR" 2>/dev/null; then
        log_fail "scaffolder: placeholders remain in $SCAFFOLD_TMP_NAME — substitution incomplete"
      else
        log_pass "scaffolder: all placeholders substituted in $SCAFFOLD_TMP_NAME"
      fi
    else
      log_fail "scaffolder: positive path reported success but $SCAFFOLD_TMP_DIR/SKILL.md missing"
      printf '%s\n' "$scaffold_output" | sed 's/^/    /'
    fi
  else
    log_fail "scaffolder: positive path failed unexpectedly (exit=$?)"
    printf '%s\n' "$scaffold_output" | sed 's/^/    /'
  fi

  # (b) Negative path: invalid name must exit nonzero and leave no dir.
  BAD_NAME="not-vc-prefix-$$"
  if "$SCAFFOLDER" "$BAD_NAME" >/dev/null 2>&1; then
    log_fail "scaffolder: invalid name '$BAD_NAME' was ACCEPTED — validator is broken"
    # Defensive cleanup if the validator silently created the dir.
    rm -rf "${SKILLS_DIR:?}/${BAD_NAME:?}" 2>/dev/null || true
  else
    if [[ -e "$SKILLS_DIR/$BAD_NAME" ]]; then
      log_fail "scaffolder: rejected '$BAD_NAME' but created a stray dir anyway"
      rm -rf "${SKILLS_DIR:?}/${BAD_NAME:?}" 2>/dev/null || true
    else
      log_pass "scaffolder: invalid name '$BAD_NAME' correctly rejected, no stray dir"
    fi
  fi

  # (c) Collision path: scaffolding the same name twice must fail the
  # second time without touching the first scaffolded copy.
  if "$SCAFFOLDER" "$SCAFFOLD_TMP_NAME" >/dev/null 2>&1; then
    log_fail "scaffolder: collision on existing skill '$SCAFFOLD_TMP_NAME' was ACCEPTED"
  else
    log_pass "scaffolder: collision on existing skill '$SCAFFOLD_TMP_NAME' correctly rejected"
  fi

  # Tear down the throwaway so the next smoke run starts clean.
  cleanup_scaffold_tmp
fi

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------

printf '\n%s\n' "$(dim '─── summary ───')"
printf '  passes:   %d\n' "$PASSES"
printf '  warnings: %d\n' "${#WARNINGS[@]}"
printf '  failures: %d\n' "${#FAILURES[@]}"

if (( ${#FAILURES[@]} > 0 )); then
  printf '\n%s\n' "$(red FAIL)"
  for f in "${FAILURES[@]}"; do
    printf '  - %s\n' "$f"
  done
  exit 1
fi

printf '\n%s — skill-loader smoke clean.\n' "$(green PASS)"
exit 0
