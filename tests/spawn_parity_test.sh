#!/usr/bin/env bash
# spawn_parity_test.sh — Plan 06 verification.
#
# Exercises scripts/lib/spawn.sh against the AGENT MODEL PARITY axiom
# (doctrine 2026-04-10). Four scenarios:
#
#   1. Positive: Opus parent -> Opus child = OK
#   2. Negative: Opus parent -> Sonnet child = REJECT with doctrine diagnostic
#   3. Override: Opus parent -> Sonnet child + VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE=1
#                = ALLOW with stderr warning
#   4. Codex Spark exception: gpt-5.3 parent -> gpt-5.3-codex-spark child
#                = REJECT without override (Spark is documented exception in
#                vc-delegate, but the override env var is what the operator
#                must use to invoke it — the gate stays strict by default).
#
# Scope note: this guards the AGENT MODEL PARITY axiom only. EXECUTION-PATH
# state parity (both launch paths projecting equivalent control_plane/runs/<id>
# .json) is a separate gate, owned by
# vibecrafted-core/tests/test_run_state_parity.py::test_execution_path_state_parity
# (W1-05). See runtime/docs/launcher-migration.md "Execution-Path Doctrine".
#
# This script is self-contained: it sources the library file under test
# and assertion-driven. shellcheck-clean.

set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)
LIB="$REPO_ROOT/scripts/lib/spawn.sh"

if [[ ! -r $LIB ]]; then
    echo "spawn_parity_test: library not readable: $LIB" >&2
    exit 2
fi

# shellcheck disable=SC1090
source "$LIB"

PASS=0
FAIL=0
# Counters persist across subshell captures via on-disk tally files. PASS/FAIL
# global variables are re-aggregated from these files at end-of-run.
TALLY_DIR=$(mktemp -d -t spawn-parity-tally.XXXXXX)
trap 'rm -rf "$TALLY_DIR"' EXIT

# ----- assertion helpers ------------------------------------------------------

ok() {
    # Appends a marker file; aggregator counts them at end.
    : > "$TALLY_DIR/pass.$$.$RANDOM.$RANDOM"
    printf '  [ok] %s\n' "$1"
}

bad() {
    : > "$TALLY_DIR/fail.$$.$RANDOM.$RANDOM"
    printf '  [FAIL] %s\n' "$1" >&2
}

# Run a function under test capturing stdout+stderr; expect a specific exit
# status. Args: <expected-status> <description> <function-and-args...>
#
# Increments tallies via the on-disk markers so subshell calls (out=$(...))
# still contribute to the final pass/fail count.
expect_status() {
    local expected=$1
    local desc=$2
    shift 2

    local actual=0
    local output
    # Sub-shell so `set -e` inside callers does not propagate to outer test.
    output=$(
        set +e
        "$@" 2>&1
        echo "__EXIT_STATUS__=$?"
    )
    actual=${output##*__EXIT_STATUS__=}
    output=${output%__EXIT_STATUS__=*}

    if [[ $actual == "$expected" ]]; then
        ok "$desc (exit=$actual)"
    else
        bad "$desc (expected exit=$expected, got=$actual)"
        printf '       output:\n%s\n' "$output" >&2
    fi

    # Echo captured output so callers can inspect content if needed.
    printf '%s' "$output"
}

# ----- scenario 1: positive ---------------------------------------------------

printf '\nscenario 1: Opus parent -> Opus child = OK\n'
expect_status 0 "spawn_check_parity opus->opus" \
    spawn_check_parity "claude-opus-4-7" "opus" > /dev/null

expect_status 0 "spawn_require_parity opus->opus" \
    spawn_require_parity "claude-opus-4-7" "opus" > /dev/null

# ----- scenario 2: negative (downgrade rejection) -----------------------------

printf '\nscenario 2: Opus parent -> Sonnet child = REJECT\n'
unset VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE 2>/dev/null || true

out=$(expect_status 1 "spawn_check_parity opus->sonnet rejected" \
    spawn_check_parity "claude-opus-4-7" "claude-sonnet-4-7")
if printf '%s' "$out" | grep -q "downgrade rejected"; then
    ok "rejection diagnostic mentions 'downgrade rejected'"
else
    bad "rejection diagnostic missing 'downgrade rejected' phrase"
    printf '       got: %s\n' "$out" >&2
fi

out=$(expect_status 1 "spawn_require_parity opus->sonnet rejected" \
    spawn_require_parity "claude-opus-4-7" "claude-sonnet-4-7")
if printf '%s' "$out" | grep -q "doctrine 2026-04-10"; then
    ok "rejection diagnostic cites doctrine 2026-04-10"
else
    bad "rejection diagnostic missing doctrine citation"
    printf '       got: %s\n' "$out" >&2
fi
if printf '%s' "$out" | grep -q "VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE"; then
    ok "rejection diagnostic surfaces override env var"
else
    bad "rejection diagnostic missing override env-var hint"
fi

# ----- scenario 3: override allows (with warning) -----------------------------

printf '\nscenario 3: Opus parent -> Sonnet child + override = ALLOW with warning\n'
out=$(VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE=1 \
    expect_status 0 "spawn_require_parity allowed under override" \
    spawn_require_parity "claude-opus-4-7" "claude-sonnet-4-7")
if printf '%s' "$out" | grep -qi "warning"; then
    ok "override path emits warning"
else
    bad "override path missing warning"
    printf '       got: %s\n' "$out" >&2
fi

# ----- scenario 4: Codex parity --------------------------------------------

printf '\nscenario 4: Codex parity matrix\n'

# Same-tier Codex = OK
expect_status 0 "spawn_require_parity gpt-5.3->gpt-5.3 ok" \
    spawn_require_parity "gpt-5.3-codex" "gpt-5.3-codex" > /dev/null

# gpt-5.3 -> Spark (downgrade within Codex family) = REJECT by default
unset VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE 2>/dev/null || true
out=$(expect_status 1 "spawn_require_parity gpt-5.3->spark rejected by default" \
    spawn_require_parity "gpt-5.3-codex" "gpt-5.3-codex-spark")
if printf '%s' "$out" | grep -qi "downgrade rejected\|BLOCKED"; then
    ok "Spark downgrade rejected without override"
else
    bad "Spark downgrade not rejected"
    printf '       got: %s\n' "$out" >&2
fi

# gpt-5.3 -> Spark with override = ALLOW with warning (documented exception)
out=$(VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE=1 \
    expect_status 0 "spawn_require_parity gpt-5.3->spark allowed under override" \
    spawn_require_parity "gpt-5.3-codex" "gpt-5.3-codex-spark")
if printf '%s' "$out" | grep -qi "warning"; then
    ok "Spark override path emits warning"
else
    bad "Spark override path missing warning"
fi

# ----- scenario 5: cross-family delegation is allowed -------------------------

printf '\nscenario 5: cross-family delegation (Opus parent -> Codex child) = OK\n'
unset VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE 2>/dev/null || true
expect_status 0 "spawn_check_parity opus->gpt-5.3 cross-family ok" \
    spawn_check_parity "claude-opus-4-7" "gpt-5.3-codex" > /dev/null

# ----- scenario 6: detect_parent_model env probe ------------------------------

printf '\nscenario 6: spawn_detect_parent_model env ladder\n'
unset VIBECRAFTED_PARENT_MODEL CLAUDE_MODEL CODEX_MODEL GEMINI_MODEL 2>/dev/null || true

if [[ -z $(spawn_detect_parent_model 2>/dev/null || true) ]]; then
    ok "no env set -> empty detection"
else
    bad "expected empty detection when no env vars set"
fi

result=$(CLAUDE_MODEL="claude-opus-4-7" spawn_detect_parent_model)
if [[ $result == "claude-opus-4-7" ]]; then
    ok "CLAUDE_MODEL probed correctly"
else
    bad "CLAUDE_MODEL probe returned '$result'"
fi

result=$(VIBECRAFTED_PARENT_MODEL="opus" CLAUDE_MODEL="sonnet" spawn_detect_parent_model)
if [[ $result == "opus" ]]; then
    ok "VIBECRAFTED_PARENT_MODEL wins over CLAUDE_MODEL"
else
    bad "env precedence wrong; got '$result'"
fi

# ----- summary ----------------------------------------------------------------

# Aggregate tallies (including those produced inside $() captures).
PASS=$(find "$TALLY_DIR" -name 'pass.*' -type f 2>/dev/null | wc -l | tr -d ' ')
FAIL=$(find "$TALLY_DIR" -name 'fail.*' -type f 2>/dev/null | wc -l | tr -d ' ')

printf '\n--- spawn_parity_test summary ---\n'
printf '  passed: %d\n' "$PASS"
printf '  failed: %d\n' "$FAIL"

if (( FAIL > 0 )); then
    exit 1
fi
exit 0
