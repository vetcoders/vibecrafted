#!/usr/bin/env bash
# Inventory gate: interactive zsh `vc-* --help` must not side-effect and must
# match the canonical `command vibecrafted <verb>` surface for mappable names.
#
# Usage:
#   bash tests/shell/vc_alias_matrix.sh [output-path]
# Default output: stdout (+ optional path for CI capture).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-}"

# Mappable: public skill-family shortcuts → vibecrafted <verb>
# (exact name or documented 1:1 map)
MAPPABLE=(
  agents audit canary cron decorate delegate dispatch dou followup fork
  guard help hydrate implement init intents justdo loop marbles operator
  ownership partner polarize prune release research resume review
  scaffold ship start trust workflow dashboard
)

# Intentional standalones — no vibecrafted <command> twin (or different product)
STANDALONE=(
  admin frame guardian research-await research-synthesize
  sandbox server server-supervisor slack paste
)

# Map vc-name → vibecrafted verb when they differ
verb_for() {
  case "$1" in
    dashboard) echo dashboard ;;
    help) echo help ;;
    start) echo start ;;
    *) echo "$1" ;;
  esac
}

report() {
  if [[ -n "$OUT" ]]; then
    printf '%s\n' "$*" | tee -a "$OUT"
  else
    printf '%s\n' "$*"
  fi
}

if [[ -n "$OUT" ]]; then
  : >"$OUT"
fi

failures=0
report "=== vc-* alias matrix (zsh -ic) ==="
report "root=$ROOT"
report "date=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

for name in "${MAPPABLE[@]}"; do
  verb="$(verb_for "$name")"
  vc_out="$(mktemp)"
  deck_out="$(mktemp)"
  vc_err="$(mktemp)"
  deck_err="$(mktemp)"
  # shellcheck disable=SC2016
  zsh -ic "vc-${name} --help" >"$vc_out" 2>"$vc_err" && vc_rc=0 || vc_rc=$?
  # shellcheck disable=SC2016
  zsh -ic "command vibecrafted ${verb} --help" >"$deck_out" 2>"$deck_err" && deck_rc=0 || deck_rc=$?

  status=OK
  notes=()
  if [[ "$vc_rc" -ne 0 ]]; then
    status=FAIL
    notes+=("vc_rc=$vc_rc")
  fi
  # Agent-first skills without a deck verb (operator) only need safe vc-* help;
  # comparing to vibecrafted operator is a false twin.
  if [[ "$name" != "operator" && "$name" != "agents" ]]; then
    if [[ "$deck_rc" -ne 0 ]]; then
      status=FAIL
      notes+=("deck_rc=$deck_rc")
    fi
  fi
  # Side-effect markers that must never appear from --help.
  # Note: skill help text often says "Machine-readable launch receipt" for --json;
  # that is documentation, not a live launch. Match only real receipts / launches.
  if grep -Eiq 'Research swarm launched|={10,} VIBECRAFTED LAUNCH RECEIPT|run_id:[[:space:]]*rsme-|status:[[:space:]]*launching[[:space:]]*$' "$vc_out" "$vc_err" 2>/dev/null; then
    status=FAIL
    notes+=("help_launched_work")
  fi
  # Canonical research flags regression
  if [[ "$name" == "research" ]]; then
    for tok in --json --model --prompt-stdin --synthesizer-model; do
      if ! grep -Fq -- "$tok" "$vc_out"; then
        status=FAIL
        notes+=("missing_$tok")
      fi
    done
  fi
  # justdo must not claim implement-only
  if [[ "$name" == "justdo" ]]; then
    if grep -Eiq 'alias for.*implement|same skill as implement' "$vc_out"; then
      status=FAIL
      notes+=("justdo_maps_to_implement_help")
    fi
    if ! grep -Eiq 'justdo|Just Do' "$vc_out"; then
      status=FAIL
      notes+=("justdo_help_missing_identity")
    fi
  fi
  # resume help must document help-ish usage, not treat as resume op
  if [[ "$name" == "resume" ]]; then
    if grep -Eiq 'LAUNCH RECEIPT|status:[[:space:]]*launching' "$vc_out" "$vc_err"; then
      status=FAIL
      notes+=("resume_help_launched")
    fi
    if ! grep -Eiq 'Resume|resume' "$vc_out"; then
      status=FAIL
      notes+=("resume_help_empty")
    fi
  fi
  # server is NOT in mappable (standalone rust); start/dashboard should mention host session not raw frame-only if deck documents start
  if [[ "$name" == "start" ]]; then
    if ! grep -Eiq 'start|operator|vc-frame|dashboard' "$vc_out"; then
      status=FAIL
      notes+=("start_help_empty")
    fi
  fi

  if [[ "$status" == "OK" ]]; then
    report "OK   vc-${name} --help → vibecrafted ${verb}"
  else
    report "FAIL vc-${name} --help notes=${notes[*]}"
    report "     vc_rc=$vc_rc deck_rc=$deck_rc"
    report "     vc_err_head=$(head -c 200 "$vc_err" | tr '\n' ' ')"
    failures=$((failures + 1))
  fi
  rm -f "$vc_out" "$deck_out" "$vc_err" "$deck_err"
done

report ""
report "=== standalone inventory (no forced vibecrafted twin) ==="
for name in "${STANDALONE[@]}"; do
  report "STANDALONE vc-${name}"
done

report ""
report "failures=$failures"
exit "$failures"
