# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_launch_receipt_field() {
  local json_path="$1"
  local field_name="$2"
  [[ -f "$json_path" ]] || return 0
  "$(_vetcoders_internal_python)" - "$json_path" "$field_name" <<'PY'
import json
import sys

path, field = sys.argv[1:3]
try:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)

value = payload.get(field, "")
if value is None:
    value = ""
print(value, end="")
PY
}

_vetcoders_print_launch_receipt() {
  local tool="$1"
  local skill="$2"
  local run_id="$3"
  local root="$4"
  local dispatch_rc="${5:-0}"
  local crafted_home control_json receipt_status report transcript launcher

  [[ "${VIBECRAFTED_SUPPRESS_LAUNCH_RECEIPT:-0}" != "1" ]] || return 0
  [[ -n "$run_id" ]] || return 0

  crafted_home="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}"
  control_json="$crafted_home/control_plane/runs/${run_id}.json"

  receipt_status="$(_vetcoders_launch_receipt_field "$control_json" "state")"
  [[ -n "$receipt_status" ]] || receipt_status="$(_vetcoders_launch_receipt_field "$control_json" "status")"
  report="$(_vetcoders_launch_receipt_field "$control_json" "latest_report")"
  [[ -n "$report" ]] || report="$(_vetcoders_launch_receipt_field "$control_json" "report")"
  transcript="$(_vetcoders_launch_receipt_field "$control_json" "latest_transcript")"
  [[ -n "$transcript" ]] || transcript="$(_vetcoders_launch_receipt_field "$control_json" "transcript")"
  launcher="$(_vetcoders_launch_receipt_field "$control_json" "launcher")"

  printf '\n'
  printf '==================== VIBECRAFTED LAUNCH RECEIPT ====================\n'
  printf 'run_id:     %s\n' "$run_id"
  printf 'agent:      %s\n' "$tool"
  printf 'skill:      %s\n' "$skill"
  printf 'root:       %s\n' "$root"
  printf 'dispatch:   %s\n' "$dispatch_rc"
  [[ -z "$receipt_status" ]] || printf 'status:     %s\n' "$receipt_status"
  printf 'control:    %s\n' "$control_json"
  [[ -z "$report" ]] || printf 'report:     %s\n' "$report"
  [[ -z "$transcript" ]] || printf 'transcript: %s\n' "$transcript"
  # Human rendering appears once the worker starts streaming; the raw .log
  # stays the machine contract (await / session-id extraction).
  if [[ -n "$transcript" && -s "${transcript%.log}.human.log" ]]; then
    printf 'human:      %s\n' "${transcript%.log}.human.log"
  fi
  [[ -z "$launcher" ]] || printf 'launcher:   %s\n' "$launcher"
  printf 'observe:    vibecrafted observe %s --run-id %s\n' "$tool" "$run_id"
  printf 'await (ARM NOW, supervisor-side): vibecrafted await %s --run-id %s\n' "$tool" "$run_id"
  printf '=====================================================================\n'
}

_vetcoders_observe() {
  local tool="$1"
  shift
  local script
  script="$(_vetcoders_spawn_script "$tool" "observe.sh")" || return 1
  bash "$script" "$tool" "$@"
}

_vetcoders_await() {
  local tool="${1:-}"
  shift || true
  local script
  script="$(_vetcoders_spawn_script "${tool:-codex}" "await.sh")" || return 1
  if [[ -n "$tool" ]]; then
    bash "$script" "$tool" "$@"
  else
    bash "$script" "$@"
  fi
}

_vetcoders_loop() {
  local script
  script="$(_vetcoders_frontier_file "runtime/scripts/vibecrafted-loop.sh" 2>/dev/null || true)"
  if [[ -z "$script" && -n "${VIBECRAFTED_ROOT:-}" ]]; then
    script="${VIBECRAFTED_ROOT}/runtime/scripts/vibecrafted-loop.sh"
  fi
  [[ -n "$script" && -f "$script" ]] || {
    echo "vibecrafted loop runtime script not found." >&2
    return 1
  }
  bash "$script" "$@"
}
