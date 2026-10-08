#!/usr/bin/env bash
# spawn.sh — Plan 06 model-parity enforcement for native subagent dispatch.
#
# Captures doctrine 2026-04-10 axiom:
#
#   "Every native delegation must pass the parent's model tier. Mixed-tier
#   dispatch (Opus parent -> Sonnet child) breaks the Anthropic prompt cache
#   AND poisons the parent's reasoning chain with shallower subagent output."
#
# Two compounding costs of a downgrade:
#   1. Cache miss — prompt cache is keyed per model. A child on a different
#      tier re-reads context uncached, paying more for slower turnaround.
#   2. Quality regression — subagent output feeds back into the parent's
#      chain. Lower tier means shallower research, weaker patches, less
#      reliable verdicts → poisons every downstream decision.
#
# This file is library-only. Source it from any spawn primitive that wants
# automated enforcement of the parity rule. It exposes three functions:
#
#   spawn_detect_parent_model
#       Print the best-effort parent model identifier to stdout. Inspects,
#       in order, VIBECRAFTED_PARENT_MODEL, CLAUDE_MODEL, CODEX_MODEL,
#       GEMINI_MODEL. Returns nonzero (silently) when nothing is set.
#
#   spawn_check_parity <parent_model> <child_model>
#       Returns 0 if child is at-or-above parent's tier (within the same
#       family). Returns 1 with a one-line diagnostic on stderr otherwise.
#       Cross-family pairings (e.g. opus parent, gpt-5.3 child) are treated
#       as ALLOWED — the parity axiom is about intra-family downgrade, and
#       the operator explicitly chose a cross-family agent in vc-why-matrix.
#
#   spawn_require_parity <parent_model> <child_model>
#       Hard gate: calls spawn_check_parity; on failure, prints diagnostic
#       and exits 1 UNLESS VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE=1 is set, in
#       which case it prints a warning and returns 0. Intended to be used
#       at the top of any concrete spawn primitive in this repo.
#
# Override env var:
#   VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE=1
#       Operator-explicit acknowledgement that the downgrade is intentional
#       (e.g. Codex gpt-5.3 -> gpt-5.3-codex-spark for speed, per vc-delegate
#       documented exception). Emits a stderr warning so it stays auditable.
#
# This file is append-only. Do not refactor existing spawn callers in this
# plan — additive integration belongs to a follow-up.

# Note: this library uses `return` (not `exit`) for failure so it composes
# safely when sourced. Callers wrap with `|| exit $?` when fatal.

# ---------------------------------------------------------------------------
# spawn_normalize_model
#
# Lowercase + strip vendor noise so comparisons are robust.
# Input:  "Opus 4.7" / "claude-opus-4-7[1m]" / "gpt-5.3-codex-spark"
# Output: "opus" / "opus" / "spark"
#
# The function returns the *tier token*, not the full model id — that's the
# unit of comparison the parity rule actually cares about.
# ---------------------------------------------------------------------------
spawn_normalize_model() {
    local raw=${1-}
    if [[ -z $raw ]]; then
        return 1
    fi

    local lower
    lower=$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')

    # Anthropic family
    case $lower in
        *opus*)   printf 'opus\n';   return 0 ;;
        *sonnet*) printf 'sonnet\n'; return 0 ;;
        *haiku*)  printf 'haiku\n';  return 0 ;;
    esac

    # Codex / OpenAI family — Spark is the documented speed-tier exception
    # in vc-delegate; treat it as its own tier below gpt-5.x mainline.
    case $lower in
        *spark*)               printf 'spark\n';  return 0 ;;
        *gpt-5.3*|*gpt-5-3*)   printf 'gpt-5.3\n'; return 0 ;;
        *gpt-5*)               printf 'gpt-5\n';   return 0 ;;
        *gpt-4*)               printf 'gpt-4\n';   return 0 ;;
    esac

    # Gemini family
    case $lower in
        *gemini-3*pro*|*3.1-pro*|*3-pro*) printf 'gemini-3-pro\n';   return 0 ;;
        *gemini-3*flash*|*3-flash*)       printf 'gemini-3-flash\n'; return 0 ;;
        *auto-gemini-3*|*gemini-3-auto*)  printf 'gemini-3-auto\n';  return 0 ;;
        *gemini*)                         printf 'gemini\n';         return 0 ;;
    esac

    # Unknown — return the raw lowercased token so callers can still log it,
    # but signal "unknown family" via nonzero return.
    printf '%s\n' "$lower"
    return 2
}

# ---------------------------------------------------------------------------
# spawn_tier_rank <tier-token>
#
# Maps normalized tier tokens to a numeric rank within their family.
# Higher number = stronger tier. Cross-family comparison is meaningless and
# the parity checker handles that case separately.
# ---------------------------------------------------------------------------
spawn_tier_rank() {
    local token=${1-}
    case $token in
        # Anthropic
        opus)            printf '300\n' ;;
        sonnet)          printf '200\n' ;;
        haiku)           printf '100\n' ;;
        # Codex
        gpt-5.3)         printf '530\n' ;;
        gpt-5)           printf '500\n' ;;
        spark)           printf '450\n' ;;
        gpt-4)           printf '400\n' ;;
        # Gemini
        gemini-3-pro)    printf '730\n' ;;
        gemini-3-auto)   printf '720\n' ;;
        gemini-3-flash)  printf '710\n' ;;
        gemini)          printf '700\n' ;;
        *)               printf '0\n' ;;
    esac
}

# ---------------------------------------------------------------------------
# spawn_tier_family <tier-token>
# ---------------------------------------------------------------------------
spawn_tier_family() {
    local token=${1-}
    case $token in
        opus|sonnet|haiku) printf 'anthropic\n' ;;
        gpt-5.3|gpt-5|spark|gpt-4) printf 'codex\n' ;;
        gemini-3-pro|gemini-3-auto|gemini-3-flash|gemini) printf 'gemini\n' ;;
        *) printf 'unknown\n' ;;
    esac
}

# ---------------------------------------------------------------------------
# spawn_detect_parent_model
#
# Walks the documented env-var ladder for parent identity. Echoes the first
# non-empty value found. Returns 1 if nothing is set so callers can decide
# whether to safe-reject (recommended) or treat as parity-OK.
# ---------------------------------------------------------------------------
spawn_detect_parent_model() {
    local v
    for v in "${VIBECRAFTED_PARENT_MODEL-}" "${CLAUDE_MODEL-}" "${CODEX_MODEL-}" "${GEMINI_MODEL-}"; do
        if [[ -n $v ]]; then
            printf '%s\n' "$v"
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# spawn_check_parity <parent_model> <child_model>
#
# Returns 0 (silent) when the child is at or above the parent's tier within
# the same family. Returns 1 with a single-line diagnostic on stderr when
# the child is a downgrade in the same family.
#
# Cross-family child is treated as ALLOWED (operator made an explicit
# vc-why-matrix choice — different cognitive profile, not a downgrade).
# Unknown tokens are treated as REJECTED to be safe; operator can override.
# ---------------------------------------------------------------------------
spawn_check_parity() {
    local parent_raw=${1-}
    local child_raw=${2-}

    if [[ -z $parent_raw || -z $child_raw ]]; then
        echo "spawn_check_parity: missing argument (parent='$parent_raw' child='$child_raw')" >&2
        return 1
    fi

    local parent_tier child_tier
    if ! parent_tier=$(spawn_normalize_model "$parent_raw"); then
        echo "spawn_check_parity: cannot classify parent model '$parent_raw'" >&2
        return 1
    fi
    if ! child_tier=$(spawn_normalize_model "$child_raw"); then
        echo "spawn_check_parity: cannot classify child model '$child_raw'" >&2
        return 1
    fi

    local parent_family child_family
    parent_family=$(spawn_tier_family "$parent_tier")
    child_family=$(spawn_tier_family "$child_tier")

    # Cross-family delegation is intentional vc-why-matrix selection.
    if [[ $parent_family != "$child_family" ]]; then
        return 0
    fi

    local parent_rank child_rank
    parent_rank=$(spawn_tier_rank "$parent_tier")
    child_rank=$(spawn_tier_rank "$child_tier")

    if (( child_rank >= parent_rank )); then
        return 0
    fi

    echo "spawn_check_parity: downgrade rejected — parent='$parent_raw' (tier=$parent_tier) child='$child_raw' (tier=$child_tier); see doctrine 2026-04-10 (AGENT MODEL PARITY)" >&2
    return 1
}

# ---------------------------------------------------------------------------
# spawn_require_parity <parent_model> <child_model>
#
# Hard gate intended for use at the top of concrete spawn primitives.
# Returns 0 on parity pass.
# Returns 0 with stderr warning when VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE=1.
# Returns 1 with full diagnostic on downgrade without override.
# ---------------------------------------------------------------------------
spawn_require_parity() {
    local parent_raw=${1-}
    local child_raw=${2-}

    if spawn_check_parity "$parent_raw" "$child_raw"; then
        return 0
    fi

    if [[ ${VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE-} == "1" ]]; then
        echo "spawn_require_parity: WARNING — downgrade explicitly allowed by VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE=1 (parent='$parent_raw' child='$child_raw')" >&2
        return 0
    fi

    cat >&2 <<EOF
spawn_require_parity: BLOCKED.

Native delegation from a higher tier to a lower tier within the same model
family violates the AGENT MODEL PARITY axiom (doctrine 2026-04-10):

  - Anthropic prompt cache is keyed per model. Mixed-tier dispatch breaks
    cache sharing — the subagent re-reads context uncached.
  - Lower-tier output feeds back into parent's reasoning chain, producing
    shallower research, weaker code, and less reliable verdicts.

Parent model: $parent_raw
Child model:  $child_raw

If this downgrade is intentional (e.g. Codex Spark for speed, documented
in vc-delegate as an allowed exception), re-run with:

  VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE=1 ...

Override emits a stderr warning so it stays auditable.
EOF
    return 1
}
