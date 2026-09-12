#!/usr/bin/env bash


# Maintain exactly one scan-only loctree watcher per root. MCP transport is a
# separate client/supervisor concern; a repository watcher must never spawn
# its own MCP server. loctree's scan.lock keeps this single-instance and
# vibecrafted_core.perception handles contention gently (exit 75 -> skip), so
# this is best-effort and never blocks a spawn. Opt out with
# VIBECRAFTED_PERCEPTION_WATCH=0.
spawn_ensure_perception() {
  local root="${1:-${SPAWN_ROOT:-$PWD}}"
  [[ "${VIBECRAFTED_PERCEPTION_WATCH:-1}" != "0" ]] || return 0
  command -v loct >/dev/null 2>&1 || return 0
  declare -F spawn_python_module >/dev/null 2>&1 || return 0
  (
    spawn_python_module vibecrafted_core.perception ensure-watch \
      --root "$root" >/dev/null 2>&1 || true
  ) &
  disown 2>/dev/null || true
  return 0
}

spawn_session_base_name() {
  local root base
  root="$(spawn_repo_root)"
  base="$(basename "$root" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g; s/^-*//; s/-*$//')"
  [[ -n "$base" ]] || base="vibecrafted"
  printf '%s\n' "$base"
}

spawn_operator_session_name_for_run_id() {
  local run_id="${1:-}"
  local base
  base="$(spawn_session_base_name)"
  if [[ "${VIBECRAFTED_VC_FRAME_GROUP_BY_CWD:-0}" == "1" ]]; then
    printf '%s\n' "$base"
    return 0
  fi
  if [[ -n "$run_id" ]]; then
    printf '%s-%s\n' "$base" "$run_id"
  else
    printf '%s\n' "$base"
  fi
}

spawn_skill_prefix() {
  local name="${1:-}"
  case "$name" in
    agents) printf 'agnt\n' ;; 
    decorate) printf 'deco\n' ;; 
    delegate) printf 'delg\n' ;; 
    dou) printf 'vdou\n' ;; 
    followup) printf 'fwup\n' ;; 
    hydrate) printf 'hydr\n' ;; 
    implement|prompt) printf 'impl\n' ;; 
    init) printf 'init\n' ;; 
    justdo) printf 'just\n' ;; 
    marbles) printf 'marb\n' ;; 
    partner) printf 'prtn\n' ;; 
    polarize) printf 'polr\n' ;;
    plan) printf 'plan\n' ;; 
    prune) printf 'prun\n' ;; 
    release) printf 'rels\n' ;; 
    research) printf 'rsch\n' ;; 
    review) printf 'rvew\n' ;; 
    scaffold) printf 'scaf\n' ;; 
    workflow) printf 'wflw\n' ;; 
    *) 
      if [[ -n "$name" ]]; then
        printf '%.4s\n' "$name"
      else
        printf 'impl\n'
      fi
      ;;
  esac
}

spawn_generate_run_id() {
  local prefix="${1:-impl}"
  local entropy=""
  entropy="$("$(spawn_python_bin)" - <<'PY' 2>/dev/null || true
import os
print(f"{int.from_bytes(os.urandom(3), 'big') % 100000:05d}")
PY
)"
  [[ -n "$entropy" ]] || entropy="$(printf '%05d' "$(( (${RANDOM:-0} * 32768 + ${RANDOM:-0}) % 100000 ))")"
  # Canonical run-id grammar — identical to
  # vibecrafted_core.workflow.reserve_run_id ({code}-{%y%m%d-%H%M%S}-{entropy}).
  # ONE id shape across the Python dispatcher and every shell fallback keeps
  # control-plane records, observe/await resolution, and report frontmatter on a
  # single identity grammar; the old prefix-HHMMSS-<pid><entropy> shape produced
  # ids the canonical resolvers could not reconcile, orphaning a phantom
  # control-plane record beside the real run. Entropy (time_ns % 100000) defuses
  # same-second collisions exactly as the Python allocator does; the date segment
  # disambiguates across days. Kept identical to _vetcoders_generate_run_id.
  printf '%s-%s-%s\n' "$prefix" "$(date +%y%m%d-%H%M%S)" "$entropy"
}

spawn_marbles_state_dir() {
  local run_id="$1"
  printf '%s/marbles/%s\n' "${VIBECRAFTED_HOME:-$HOME/.vibecrafted}" "$run_id"
}

spawn_archive_marbles_state_dir() {
  local run_id="$1"
  local status="${2:-archived}"
  local state_dir target_date target_parent target_dir state_file py

  [[ -n "$run_id" ]] || return 1
  state_dir="$(spawn_marbles_state_dir "$run_id")"
  [[ -d "$state_dir" ]] || return 0

  target_date="$(date -u +%F)"
  target_parent="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/marbles/_archived/$target_date"
  target_dir="$target_parent/$run_id"
  [[ ! -e "$target_dir" ]] || return 1

  mkdir -p "$target_parent"
  mv "$state_dir" "$target_dir"

  state_file="$target_dir/state.json"
  py="$(spawn_python_bin)"
  if [[ -f "$state_file" ]] && command -v "$py" >/dev/null 2>&1; then
    "$py" - "$state_file" "$status" "$state_dir" "$target_dir" <<'PY' || true
import datetime
import json
import sys

state_path, status, archived_from, target_dir = sys.argv[1:5]
archived_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

try:
    with open(state_path, encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    payload = {}

previous_status = payload.get("status", "")
payload["previous_status"] = previous_status
payload["status"] = status
payload["archived_from"] = archived_from
payload["archived_at"] = archived_at
payload["updated_at"] = archived_at
for key in ("plan", "god_plan", "ancestor_plan"):
    value = payload.get(key)
    if isinstance(value, str) and value.startswith(archived_from + "/"):
        payload[key] = target_dir + value[len(archived_from):]

with open(state_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
PY
  fi

  printf '%s\n' "$target_dir"
}

spawn_marbles_child_plan_path() {
  local store_dir="$1"
  local ancestor_plan="$2"
  local loop_nr="$3"
  local loop_file_prefix="${4:-marbles}"
  local ancestor_slug
  ancestor_slug="$(spawn_slug_from_path "$ancestor_plan")"
  printf '%s/plans/%s-%s_L%s.md\n' "$store_dir" "$loop_file_prefix" "$ancestor_slug" "$loop_nr"
}

spawn_marbles_write_child_plan() {
  local ancestor_plan="$1"
  local child_plan="$2"
  local loop_skill_name="${3:-marbles}"

  mkdir -p "$(dirname "$child_plan")"
  cp "$ancestor_plan" "$child_plan"
  {
    cat <<'ROUND_CONTRACT_HEAD'

---
## Worker Contract

### Hard Rule
- The worker must remain on the operator-assigned substrate.
- The worker is expected to maneuver intelligently within the assigned surface, inside the living tree, and around concurrent edits by others. That maneuverability is part of the craft.
ROUND_CONTRACT_HEAD
    printf -- '- But vc-%s loop runtime does not permit escaping the assigned substrate.\n' "$loop_skill_name"
    cat <<'ROUND_CONTRACT_TAIL'
- Do not switch branches.
- Do not create or move to a worktree.
- Do not relocate execution to another lane or clone.
- If the substrate is too poisoned to operate on, return control to the operator/runtime layer. Do not solve substrate invalidity by moving sideways.

## Exit Contract
- **REPORT**: mandatory. Write to the report path given at the end of this prompt.
  Filesystem artifact (report.md + meta.json + transcript.log) is the closure marker.
- **COMMIT**: only if you produced staged changes that match the dispatched scope.
  NO empty commits. NO `--allow-empty`. NO chore stamps.
- **SCOPE**: do your work, write report, optionally commit if real changes, stop.
ROUND_CONTRACT_TAIL
  } >> "$child_plan"
}

spawn_prepare_paths() {
  local agent="$1"
  local prompt_file="$2"
  local root="${3:-}"
  local mode="${4:-${VIBECRAFTED_SKILL_NAME:-}}"
  local dry_run="${5:-0}"
  local skill_name="${VIBECRAFTED_SKILL_NAME:-$mode}"
  local lock_file=""
  local discovered_session=""
  local ambient_store_root="${VIBECRAFTED_STORE_ROOT:-${SPAWN_ROOT:-}}"
  local requested_loop_nr="${VIBECRAFTED_LOOP_NR:-${SPAWN_LOOP_NR:-0}}"

  case "$requested_loop_nr" in
    ''|*[!0-9]*)
      requested_loop_nr=0
      ;;
  esac

  if [[ -n "$root" ]]; then
    SPAWN_ROOT="$(spawn_abspath "$root")"
    [[ -d "$SPAWN_ROOT" ]] || spawn_die "Root directory not found: $SPAWN_ROOT"
  else
    SPAWN_ROOT="$(spawn_repo_root)"
  fi

  if [[ "$dry_run" != "1" ]]; then
    spawn_ensure_perception "$SPAWN_ROOT"
  fi

  if [[ -z "${VIBECRAFTED_OPERATOR_SESSION:-}" ]]; then
    discovered_session="$(spawn_effective_operator_session 2>/dev/null || true)"
    if [[ -n "$discovered_session" ]]; then
      export VIBECRAFTED_OPERATOR_SESSION="$discovered_session"
    fi
  fi

  SPAWN_PLAN="$(spawn_abspath "$prompt_file")"
  SPAWN_SLUG="$(spawn_slug_from_path "$prompt_file")"
  SPAWN_TS="$(spawn_timestamp)"
  SPAWN_AGENT="$agent"
  SPAWN_PROMPT_ID="${SPAWN_SLUG}_${SPAWN_TS%%_*}"
  SPAWN_SKILL_CODE="$(spawn_effective_skill_code 2>/dev/null || true)"
  if [[ -z "$SPAWN_SKILL_CODE" && -n "$skill_name" ]]; then
    SPAWN_SKILL_CODE="$(spawn_skill_prefix "$skill_name")"
  fi
  SPAWN_SKILL_NAME="$skill_name"
  SPAWN_LOOP_NR="$requested_loop_nr"
  SPAWN_RUN_ID="$(spawn_effective_run_id 2>/dev/null || true)"
  if [[ -n "$SPAWN_RUN_ID" ]]; then
    : 
  else
    SPAWN_RUN_ID="$(spawn_generate_run_id "${SPAWN_SKILL_CODE:-impl}")"
  fi
  lock_file="$(spawn_effective_run_lock 2>/dev/null || true)"
  if [[ -z "$lock_file" || ! -f "$lock_file" ]]; then
    lock_file="$VIBECRAFTED_HOME/locks/$(spawn_org_repo "$SPAWN_ROOT")/${SPAWN_RUN_ID}.lock"
  fi
  if [[ ! -f "$lock_file" ]]; then
    lock_file="$(spawn_create_run_lock "$SPAWN_RUN_ID" "$agent" "${skill_name:-${mode:-implement}}" "$SPAWN_ROOT")"
  fi
  SPAWN_RUN_LOCK="$lock_file"

  # Central store path (falls back to per-repo if no git remote)
  local store_base
  store_base="$(spawn_effective_store_dir "$SPAWN_ROOT" "$ambient_store_root")"

  SPAWN_PLAN_DIR="$store_base/plans"
  SPAWN_REPORT_DIR="$store_base/reports"
  SPAWN_TMP_DIR="$store_base/tmp"
  SPAWN_LOG_DIR="$SPAWN_REPORT_DIR"

  local research_run_dir_abs="" store_base_abs=""
  if [[ -n "${VIBECRAFTED_RESEARCH_RUN_DIR:-}" ]]; then
    research_run_dir_abs="$(spawn_abspath "$VIBECRAFTED_RESEARCH_RUN_DIR" 2>/dev/null || true)"
    store_base_abs="$(spawn_abspath "$store_base" 2>/dev/null || true)"
  fi

  if [[ -n "$research_run_dir_abs" ]] \
    && [[ "$research_run_dir_abs" == "$store_base_abs" ]] \
    && [[ "${SPAWN_SKILL_NAME:-}" == "research" || "${SPAWN_SKILL_CODE:-}" == "rsch" ]]; then
    SPAWN_LOG_DIR="$store_base/logs"
    SPAWN_BASE="$SPAWN_REPORT_DIR/${agent}"
    SPAWN_REPORT="$SPAWN_REPORT_DIR/${agent}.md"
    SPAWN_TRANSCRIPT="$SPAWN_LOG_DIR/${agent}.transcript.log"
    SPAWN_META="$SPAWN_LOG_DIR/${agent}.meta.json"
    SPAWN_LAUNCHER="$SPAWN_TMP_DIR/${agent}_launch.sh"
    mkdir -p "$SPAWN_PLAN_DIR" "$SPAWN_REPORT_DIR" "$SPAWN_LOG_DIR" "$SPAWN_TMP_DIR"
  else
    SPAWN_BASE="$SPAWN_REPORT_DIR/${SPAWN_TS}_${SPAWN_RUN_ID}_${SPAWN_SLUG}_${agent}"
    SPAWN_REPORT="${SPAWN_BASE}.md"
    SPAWN_TRANSCRIPT="${SPAWN_BASE}.transcript.log"
    SPAWN_META="${SPAWN_BASE}.meta.json"
    # Include run_id in every durable launch artifact. The generated launcher
    # removes its report/transcript before writing, so timestamp+slug naming is
    # not safe after failed or prematurely closed terminal sessions.
    SPAWN_LAUNCHER="$SPAWN_TMP_DIR/${SPAWN_TS}_${SPAWN_RUN_ID}_${SPAWN_SLUG}_${agent}_launch.sh"
    mkdir -p "$SPAWN_PLAN_DIR" "$SPAWN_REPORT_DIR" "$SPAWN_TMP_DIR"
  fi

  spawn_link_repo_artifacts "$store_base" "$SPAWN_ROOT"
  export SPAWN_ROOT SPAWN_PLAN SPAWN_SLUG SPAWN_TS SPAWN_AGENT SPAWN_PROMPT_ID SPAWN_RUN_ID SPAWN_RUN_LOCK SPAWN_SKILL_CODE SPAWN_SKILL_NAME SPAWN_LOOP_NR
  export SPAWN_PLAN_DIR SPAWN_REPORT_DIR SPAWN_TMP_DIR SPAWN_LOG_DIR SPAWN_BASE SPAWN_REPORT SPAWN_TRANSCRIPT SPAWN_META SPAWN_LAUNCHER
}

spawn_scan_active() {
  local reports_dir="$1"
  local tmp_root="${TMPDIR:-/tmp}"
  local marker=""
  local recent_active=""
  local py=""

  tmp_root="${tmp_root%/}"
  marker="${tmp_root}/.vibecrafted-scan-marker"

  [[ -d "$reports_dir" ]] || {
    touch "$marker"
    return 0
  }

  # Resolved once per scan, not once per meta file: the loops below run in a
  # command-substitution subshell and inherit this value.
  py="$(spawn_python_bin)"

  if [[ -e "$marker" ]]; then
    recent_active="$(
      find "$reports_dir" -name '*.meta.json' -newer "$marker" 2>/dev/null | sort | while read -r f; do
        "$py" - "$f" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)
if payload.get("status") in ("launching", "running"):
    print(f"  {payload.get('run_id', '?'):10s} {payload.get('agent', '?'):8s} {payload.get('status', '?'):10s} {payload.get('mode', '?')}")
PY
      done
    )"
  else
    recent_active="$(
      find "$reports_dir" -name '*.meta.json' 2>/dev/null | sort | while read -r f; do
        "$py" - "$f" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)
if payload.get("status") in ("launching", "running"):
    print(f"  {payload.get('run_id', '?'):10s} {payload.get('agent', '?'):8s} {payload.get('status', '?'):10s} {payload.get('mode', '?')}")
PY
      done
    )"
  fi

  touch "$marker"
  [[ -n "$recent_active" ]] || return 0

  printf '\033[2mRecent active 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. runs:\n%s\033[0m\n' "$recent_active"
}

spawn_normalize_ambient_context
