#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
  cat <<EOF
Usage: marbles_spawn.sh --agent <agent> [--depth <n>|--file <file>|--prompt <text>] [--count <n>] [--rotation <mode>] [--runtime <rt>] [--root <dir>]

Marbles convergence loop orchestrator.
Runs <agent> in a loop of <count> iterations against a live ancestor plan.
Convergence happens through code state, not report chaining.

Options:
  --agent <name>      claude, codex, agy, junie, or grok (required)
  --depth <n>         Crawl last n plan files as context (default: 3 when no source is given)
  --file <file>       Use specific plan/input file
  --prompt <text>     Inline prompt string; captures the rest of the command line
  --count <n>         Number of loops (default: 3)
  --rotation <mode>   single, duo, trio, or multi (default: single)
  --runtime <rt>      terminal, headless (default: headless)
  --root <dir>        Repository root
  --no-watch          Skip watcher UI and run chaining directly
  --skill-name <name> Loop skill name (default: marbles)
  --skill-code <code> Loop skill run-id prefix (default: derived from skill)
  --loop-label <text> Human label for receipts (default: Marbles)
EOF
}

agent=""
depth=""
task=""
prompt=""
count=3
rotation="single"
runtime="headless"
root=""
use_watcher=1
loop_skill_name="${VIBECRAFTED_LOOP_SKILL_NAME:-marbles}"
loop_skill_code="${VIBECRAFTED_LOOP_SKILL_CODE:-}"
loop_label="${VIBECRAFTED_LOOP_LABEL:-Marbles}"
loop_file_prefix="${VIBECRAFTED_LOOP_FILE_PREFIX:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)   shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --agent"; agent="$1" ;;
    --depth)   shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --depth"; depth="$1" ;;
    --task|--file|-f) shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --file"; task="$1" ;;
    --prompt|-p) shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --prompt"; prompt="$*"; break ;;
    --count)   shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --count"; count="$1" ;;
    --rotation) shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --rotation"; rotation="$1" ;;
    --runtime) shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --runtime"; runtime="$1" ;;
    --root)    shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --root"; root="$1" ;;
    --no-watch) use_watcher=0 ;;
    --skill-name) shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --skill-name"; loop_skill_name="$1" ;;
    --skill-code) shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --skill-code"; loop_skill_code="$1" ;;
    --loop-label) shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --loop-label"; loop_label="$1" ;;
    --loop-file-prefix) shift; [[ $# -gt 0 ]] || spawn_die "Missing value for --loop-file-prefix"; loop_file_prefix="$1" ;;
    -h|--help) usage; exit 0 ;;
    *) spawn_die "Unknown argument: $1" ;;
  esac
  shift
done

[[ -n "$agent" ]] || spawn_die "Missing --agent"
[[ "$agent" =~ ^(claude|codex|agy|junie|grok|cursor)$ ]] || spawn_die "Invalid agent: $agent"
spawn_validate_runtime "$runtime"
spawn_require_positive_int "$count" "--count"
spawn_rotation_validate_mode "$rotation"
[[ -z "$depth" ]] || spawn_require_positive_int "$depth" "--depth"
[[ "$loop_skill_name" =~ ^[A-Za-z0-9_-]+$ ]] || spawn_die "Invalid --skill-name: $loop_skill_name"
[[ -n "$loop_skill_code" ]] || loop_skill_code="$(spawn_skill_prefix "$loop_skill_name")"
[[ "$loop_skill_code" =~ ^[A-Za-z0-9_-]+$ ]] || spawn_die "Invalid --skill-code: $loop_skill_code"
[[ -n "$loop_file_prefix" ]] || loop_file_prefix="$loop_skill_name"
loop_file_prefix="$(printf '%s' "$loop_file_prefix" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9_-]+/-/g; s/^-+//; s/-+$//')"
[[ -n "$loop_file_prefix" ]] || loop_file_prefix="$loop_skill_name"

spawn_require_shell_syntax "$SCRIPT_DIR/common.sh" "shared spawn library"
spawn_require_shell_syntax "$SCRIPT_DIR/${agent}_spawn.sh" "${agent} spawn"
spawn_require_shell_syntax "$SCRIPT_DIR/marbles_next.sh" "marbles next hop"
if (( use_watcher )); then
  spawn_require_shell_syntax "$SCRIPT_DIR/marbles_watcher.sh" "marbles watcher"
fi

sources=0
[[ -n "$depth" ]]  && ((sources++)) || true
[[ -n "$task" ]]   && ((sources++)) || true
[[ -n "$prompt" ]] && ((sources++)) || true
[[ $sources -le 1 ]] || spawn_die "Use at most one source: --depth, --file, or --prompt"
if [[ $sources -eq 0 ]]; then
  depth=3
fi

root_dir="${root:-$(spawn_repo_root)}"
store="$(spawn_marbles_store_dir "$root_dir")"
mkdir -p "$store/plans" "$store/reports"

# Spawn-time GC — before this dispatch touches any lock, reap every meta.json
# in this repo's store whose launcher_pid is dead. Keeps control plane honest
# and prevents "laptop exploding" from zombie accumulation. Paired with the
# retirement of restore-orphaned.sh: dead runs end at the gate, not reanimate.
spawn_gc_dead_runs "$(dirname "$store")" 2>/dev/null || true

# Honour inherited run_id ONLY when a parent spawn placed it deliberately.
# A leaked env var from a previous vc_frame window/session must not clobber a
# fresh spawn with a recycled id.
#
# Four refusal reasons invalidate the inherited run_id and force fresh mint:
#   1. state_dir exists and RESUME flag absent   (original gate)
#   2. stop marker present                       (user killed prior watcher)
#   3. god.md is read-only (chmod 0444)          (prior seed finalized)
#   4. state.json status is terminal             (done/stopped/failed/ghost)
# Even VIBECRAFTED_MARBLES_RESUME=1 cannot recycle a terminal/immutable slot —
# RESUME means "continue a paused run," not "overwrite a finished one."
marbles_run_id="${VIBECRAFTED_MARBLES_RUN_ID:-}"
if [[ -n "$marbles_run_id" ]]; then
  candidate_state_dir="$(spawn_marbles_state_dir "$marbles_run_id")"
  refuse_reason=""
  if [[ -e "$candidate_state_dir" ]]; then
    if [[ -z "${VIBECRAFTED_MARBLES_RESUME:-}" ]]; then
      refuse_reason="no VIBECRAFTED_MARBLES_RESUME flag"
    elif [[ -f "$candidate_state_dir/stop" ]]; then
      refuse_reason="stop marker present (prior watcher was killed)"
    elif [[ -f "$candidate_state_dir/god.md" && ! -w "$candidate_state_dir/god.md" ]]; then
      refuse_reason="god.md is read-only (prior dispatch finalized the seed)"
    elif [[ -f "$candidate_state_dir/state.json" ]]; then
      terminal_status="$(python3 -c 'import json,sys
try:
  with open(sys.argv[1]) as fh:
    print(json.load(fh).get("status",""))
except Exception:
  pass' "$candidate_state_dir/state.json" 2>/dev/null || true)"
      case "$terminal_status" in
        done|stopped|failed|ghost|completed)
          refuse_reason="state.json status is terminal ($terminal_status)"
          ;;
      esac
    fi
  fi
  if [[ -n "$refuse_reason" ]]; then
    printf 'warn: VIBECRAFTED_MARBLES_RUN_ID=%s unusable: %s — minting fresh id\n' \
      "$marbles_run_id" "$refuse_reason" >&2
    marbles_run_id=""
    unset VIBECRAFTED_MARBLES_RUN_ID
    unset VIBECRAFTED_MARBLES_RESUME
  fi
fi
if [[ -z "$marbles_run_id" ]]; then
  # Same PID-suffixed format as _vetcoders_generate_run_id; keep shapes identical across entry points.
  marbles_run_id="${loop_skill_code}-$(date +%H%M%S)-$$"
fi
state_dir="$(spawn_marbles_state_dir "$marbles_run_id")"
state_file="$state_dir/state.json"
god_plan="$state_dir/god.md"
ancestor_plan="$state_dir/ancestor.md"
mkdir -p "$state_dir"

seed_source_file=""
input_kind=""
if [[ -n "$task" ]]; then
  seed_source_file="$(spawn_abspath "$task")"
  spawn_require_file "$seed_source_file"
  input_kind="file"
elif [[ -n "$prompt" ]]; then
  input_kind="prompt"
elif [[ -n "$depth" ]]; then
  seed_source_file="$(VIBECRAFTED_STORE_DIR="$store" VIBECRAFTED_STORE_ROOT="$root_dir" bash "$SCRIPT_DIR/marbles_plan.sh" --agent "$agent" --run-id "$marbles_run_id" --depth "$depth" --root "$root_dir")"
  spawn_require_file "$seed_source_file"
  input_kind="depth"
fi

body_file="$state_dir/.seed-body.md"
if [[ -n "$seed_source_file" ]]; then
  spawn_strip_frontmatter_to_file "$seed_source_file" "$body_file"
else
  printf '%s\n' "$prompt" > "$body_file"
fi

ancestor_focus=""
ancestor_priority=""
ancestor_model=""
if [[ -n "$seed_source_file" ]]; then
  ancestor_focus="$(spawn_frontmatter_field "$seed_source_file" "focus")"
  ancestor_priority="$(spawn_frontmatter_field "$seed_source_file" "priority")"
  ancestor_model="$(spawn_frontmatter_field "$seed_source_file" "model")"
fi
[[ -n "$ancestor_focus" ]] || ancestor_focus="initial prompt"
[[ -n "$ancestor_priority" ]] || ancestor_priority="P0"

created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
{
  cat <<EOF
---
kind: god
run_id: $marbles_run_id
created_at: $created_at
input_kind: $input_kind
EOF
  if [[ -n "$seed_source_file" ]]; then
    printf 'source_path: %s\n' "$seed_source_file"
  fi
  cat <<'EOF'
---

EOF
  cat "$body_file"
} > "$god_plan"
chmod 0444 "$god_plan"

{
  cat <<EOF
---
agent: $agent
skill_name: $loop_skill_name
focus: $ancestor_focus
priority: $ancestor_priority
EOF
  if [[ -n "$ancestor_model" ]]; then
    printf 'model: %s\n' "$ancestor_model"
  fi
  cat <<'EOF'
---

EOF
  cat "$body_file"
} > "$ancestor_plan"
rm -f "$body_file"

ancestor_mtime="$(spawn_ancestor_mtime_iso "$ancestor_plan")"
rotation_pool_json="$(spawn_rotation_pool_json)"

cat > "$state_file" <<EOF
{
  "run_id": "$marbles_run_id",
  "agent": "$agent",
  "skill_name": "$loop_skill_name",
  "skill_code": "$loop_skill_code",
  "loop_label": "$loop_label",
  "loop_file_prefix": "$loop_file_prefix",
  "mode": "steered",
  "rotation": "$rotation",
  "rotation_pool": $rotation_pool_json,
  "ancestor_mtime": "$ancestor_mtime",
  "plan": "$ancestor_plan",
  "god_plan": "$god_plan",
  "ancestor_plan": "$ancestor_plan",
  "root": "$root_dir",
  "runtime": "$runtime",
  "total_loops": $count,
  "current_loop": 0,
  "status": "initialized",
  "started_at": "$created_at",
  "loops": [],
  "trajectory": []
}
EOF

org_repo=""
if cd "$root_dir" && git remote get-url origin >/dev/null 2>&1; then
  org_repo="$(git remote get-url origin | sed -E 's|.*[:/]([^/]+)/([^/.]+)(\.git)?$|\1/\2|')"
fi
[[ -n "$org_repo" ]] || org_repo="$(basename "$root_dir")"
lock_dir="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/locks/$org_repo"
mkdir -p "$lock_dir"

session_lock="$lock_dir/${marbles_run_id}.lock"
cat > "$session_lock" <<LOCK
run_id=$marbles_run_id
agent=$agent
skill_name=$loop_skill_name
skill_code=$loop_skill_code
loop_label=$loop_label
plan=$ancestor_plan
count=$count
current=1
runtime=$runtime
root=$root_dir
state_dir=$state_dir
started=$created_at
status=running
LOCK

_bold='\033[1m' _copper='\033[38;5;173m' _steel='\033[38;5;247m' _reset='\033[0m'
printf '\n%b ⚒  %s Loop · %s × %s%b\n' "$_bold$_copper" "$loop_label" "$agent" "$count" "$_reset"
printf '%b──────────────────────────────────%b\n' "$_steel" "$_reset"
printf '%b  god:     %b%s\n'   "$_steel" "$_reset" "$god_plan"
printf '%b  ancestor:%b%s\n'   "$_steel" "$_reset" "$ancestor_plan"
printf '%b  loops:   %b%s\n'   "$_steel" "$_reset" "$count"
printf '%b  run_id:  %b%s\n'   "$_steel" "$_reset" "$marbles_run_id"
printf '%b  lock:    %b%s\n'   "$_steel" "$_reset" "$session_lock"
printf '%b──────────────────────────────────%b\n' "$_steel" "$_reset"

l1_plan="$(spawn_marbles_child_plan_path "$store" "$ancestor_plan" 1 "$loop_file_prefix")"
spawn_marbles_write_child_plan "$ancestor_plan" "$l1_plan" "$loop_skill_name"

q_state="$(spawn_shell_quote "$state_dir")"
q_root="$(spawn_shell_quote "$root_dir")"
q_runtime="$(spawn_shell_quote "$runtime")"
q_scripts="$(spawn_shell_quote "$SCRIPT_DIR")"
q_lock="$(spawn_shell_quote "$session_lock")"
q_store="$(spawn_shell_quote "$store")"

success_hook="bash $q_scripts/marbles_next.sh $q_state $count 1 $marbles_run_id $q_root $q_runtime $q_scripts $q_lock $q_store"
failure_hook="bash $q_scripts/marbles_next.sh --failed $q_state $count 1 $marbles_run_id $q_root $q_runtime $q_scripts $q_lock $q_store"

export SPAWN_LOOP_NR=1
export VIBECRAFTED_LOOP_NR=1
export VIBECRAFTED_RUN_ID="${marbles_run_id}-001"
export VIBECRAFTED_SKILL_CODE="$loop_skill_code"
export VIBECRAFTED_SKILL_NAME="$loop_skill_name"
# One run_id = one tab, name "marbles-<run_id>". Subsequent loops
# (marbles_next.sh) inherit this via env and stay in the same tab, so the
# dispatch history of a single run never spills across tabs and distinct
# runs never merge into one. The "marbles-" prefix distinguishes these tabs
# from workflow/research tabs which also carry run_ids.
# Canonical since 2026-04-12 marbles tab isolation spec; regressed 2026-04-14
# to shared "marbles" tab; restored 2026-04-22.
export VIBECRAFTED_MARBLES_TAB_NAME="${loop_file_prefix}-${marbles_run_id}"

spawn_args=(
  --mode implement
  --runtime "$runtime"
  --root "$root_dir"
  --success-hook "$success_hook"
  --failure-hook "$failure_hook"
)
ancestor_model="$(spawn_clean_model "$ancestor_model")"
if [[ -n "$ancestor_model" ]]; then
  spawn_args+=(--model "$ancestor_model")
fi

if (( use_watcher )); then
  # The watcher redraws the last three status lines in place. Suppress the
  # extra report-path hint from the child spawn so long paths do not get
  # interleaved into that redraw surface.
  VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION=right \
    SPAWN_LOOP_NR=1 \
    VIBECRAFTED_STORE_DIR="$store" \
    VIBECRAFTED_STORE_ROOT="$root_dir" \
    VIBECRAFTED_MARBLES_WATCHER=1 \
    VIBECRAFTED_SUPPRESS_REPORT_HINT=1 \
    bash "$SCRIPT_DIR/${agent}_spawn.sh" "${spawn_args[@]}" "$l1_plan" &

  exec bash "$SCRIPT_DIR/marbles_watcher.sh" \
    "$marbles_run_id" "$state_dir" "$count" \
    "$root_dir" "$runtime" "$store" "$session_lock"
else
  VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION=right SPAWN_LOOP_NR=1 VIBECRAFTED_STORE_DIR="$store" VIBECRAFTED_STORE_ROOT="$root_dir" bash "$SCRIPT_DIR/${agent}_spawn.sh" "${spawn_args[@]}" "$l1_plan"
fi
