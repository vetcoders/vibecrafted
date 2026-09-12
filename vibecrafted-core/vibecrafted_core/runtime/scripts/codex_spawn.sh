#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
  cat <<EOF_USAGE
Usage: codex_spawn.sh [--mode <mode>] [--runtime <terminal|visible|headless|background|detached>] [--model <model>] [--root <repo-root>] [--dry-run] <plan.md>

Modes are labels for the artifact metadata, e.g. implement, review, or plan.
Runtime modes:
- terminal / visible: launch in a vc-frame worker pane
- headless / background / detached: run launcher as detached background process
- default: headless
EOF_USAGE
}

mode="implement"
runtime="headless"
model="${CODEX_MODEL:-}"
root=""
plan_file=""
dry_run=0
success_hook_extra=""
failure_hook_extra=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      shift
      [[ $# -gt 0 ]] || spawn_die "Missing value for --mode"
      mode="$1"
      ;;
    --runtime)
      shift
      [[ $# -gt 0 ]] || spawn_die "Missing value for --runtime"
      runtime="$1"
      ;;
    --model)
      shift
      [[ $# -gt 0 ]] || spawn_die "Missing value for --model"
      model="$1"
      ;;
    --root)
      shift
      [[ $# -gt 0 ]] || spawn_die "Missing value for --root"
      root="$1"
      ;;
    --dry-run)
      dry_run=1
      ;;
    --success-hook)
      shift
      [[ $# -gt 0 ]] || spawn_die "Missing value for --success-hook"
      success_hook_extra="$1"
      ;;
    --failure-hook)
      shift
      [[ $# -gt 0 ]] || spawn_die "Missing value for --failure-hook"
      failure_hook_extra="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      [[ -z "$plan_file" ]] || spawn_die "Unexpected argument: $1"
      plan_file="$1"
      ;;
  esac
  shift
done

[[ -n "$plan_file" ]] || {
  usage
  exit 1
}
spawn_require_file "$plan_file"
spawn_validate_runtime "$runtime"
spawn_prepare_paths codex "$plan_file" "$root" "$mode" "$dry_run"
spawn_scan_active "${SPAWN_LOG_DIR:-$SPAWN_REPORT_DIR}"
runtime_input="$SPAWN_TMP_DIR/${SPAWN_TS}_${SPAWN_RUN_ID}_${SPAWN_SLUG}_codex_prompt.md"
model="$(spawn_clean_model "$model")"
spawn_build_runtime_prompt "$SPAWN_PLAN" "$runtime_input" "$SPAWN_REPORT" codex "$model"
spawn_write_meta "$SPAWN_META" "launching" "codex" "$mode" "$SPAWN_ROOT" "$SPAWN_PLAN" "$SPAWN_REPORT" "$SPAWN_TRANSCRIPT" "$SPAWN_LAUNCHER" "$model"

if (( !dry_run )); then
  spawn_require_command codex
fi

qroot="$(spawn_shell_quote "$SPAWN_ROOT")"
qruntime="$(spawn_shell_quote "$runtime_input")"
qreport="$(spawn_shell_quote "$SPAWN_REPORT")"
qtranscript="$(spawn_shell_quote "$SPAWN_TRANSCRIPT")"
qlast_message="$(spawn_shell_quote "${SPAWN_TRANSCRIPT%.log}.last-message.md")"
qbridge="$(spawn_shell_quote "$SCRIPT_DIR/codex_stream_bridge.py")"
# The bridge is ours, so its interpreter is ours too — even though the command
# line around it is generated and `codex exec` beside it is the user's tool.
qpython="$(spawn_shell_quote "$(spawn_python_bin)")"
qmodel="$(spawn_shell_quote "$model")"
model_flag=""
[[ -n "$model" ]] && model_flag="--model $qmodel"
bridge_flags=""
case "$runtime" in
  terminal|visible)
    bridge_flags="--echo-stdout"
    ;;
esac
last_message_fallback=""
missing_report_guard=""
if [[ "$mode" == "research" || "${VIBECRAFTED_SKILL_NAME:-}" == "research" || "${VIBECRAFTED_SKILL_CODE:-}" == "rsch" || "${VIBECRAFTED_RESEARCH_MODE:-0}" == "1" ]]; then
  # Research reports must be written as first-class artifacts by the worker.
  # Copying Codex's final handoff message into the report path creates a false
  # "completed" report when Codex only says "see the report path".
  missing_report_guard="if [[ \$pipeline_status -eq 0 && ! -s $qreport ]]; then pipeline_status=65; fi;"
else
  last_message_fallback="if [[ \$pipeline_status -eq 0 && ! -s $qreport && -s $qlast_message ]]; then cp $qlast_message $qreport || pipeline_status=\$?; fi;"
fi
failure_report_fallback="if [[ \$pipeline_status -ne 0 && ! -s $qreport ]]; then { printf '%s\n' '---'; printf 'run_id: %s\n' \"\${SPAWN_RUN_ID:-unknown}\"; printf 'prompt_id: %s\n' \"\${SPAWN_PROMPT_ID:-unknown}\"; printf 'agent: %s\n' \"\${SPAWN_AGENT:-codex}\"; printf 'status: failed\n'; printf '%s\n\n' '---'; printf '%s\n' 'Codex failed before writing a standalone report file.'; printf '%s\n' 'See transcript for the full event stream:'; printf '%s\n' $qtranscript; printf '%s\n' 'Last message, if present:'; printf '%s\n' $qlast_message; } > $qreport; fi;"
# Failure fallback is emitted inside the child shell before it exits so meta
# finalization cannot race ahead of the minimal failure report.
launch_cmd="set -o pipefail && cd $qroot && { rm -f $qlast_message; codex exec -C $qroot --json --dangerously-bypass-approvals-and-sandbox $model_flag --output-last-message $qlast_message - < $qruntime 2>&1 | $qpython $qbridge --transcript $qtranscript ${bridge_flags}; pipeline_status=\$?; $last_message_fallback $missing_report_guard $failure_report_fallback echo; { grep -oE '\\[[0-9]{2}:[0-9]{2}:[0-9]{2}\\] session: [[:alnum:]-]+' $qtranscript 2>/dev/null | tail -1 | awk '{print \$3}' | xargs -I{} printf '\\n\\033[33m━━━ session: {} ━━━\\033[0m\\n'; } || true; exit \$pipeline_status; }"

# shellcheck disable=SC2016
codex_success_hook='
  if [[ ! -s "$report" ]] || ! awk "BEGIN { body=0; in_front=0 } NR==1 && \$0==\"---\" { in_front=1; next } in_front && \$0==\"---\" { in_front=0; next } in_front { next } NF { body=1 } END { exit body ? 0 : 1 }" "$report"; then
    spawn_write_frontmatter "$report" "$SPAWN_AGENT" "${SPAWN_MODEL:-unknown}" "completed"
    {
      printf "Codex completed without writing a standalone report file.\n"
      printf "See transcript for the full event stream:\n"
      printf "%s\n" "$transcript"
    } >> "$report"
  fi'

# shellcheck disable=SC2016
codex_failure_hook='
  if [[ ! -s "$report" ]] || ! awk "BEGIN { body=0; in_front=0 } NR==1 && \$0==\"---\" { in_front=1; next } in_front && \$0==\"---\" { in_front=0; next } in_front { next } NF { body=1 } END { exit body ? 0 : 1 }" "$report"; then
    spawn_write_frontmatter "$report" "$SPAWN_AGENT" "${SPAWN_MODEL:-unknown}" "failed"
    {
      printf "Codex failed before writing a standalone report file.\n"
      printf "See transcript for the full event stream:\n"
      printf "%s\n" "$transcript"
      printf "Last message, if present:\n"
      printf "%s\n" "${transcript%.log}.last-message.md"
    } >> "$report"
  fi'

combined_success="${codex_success_hook}${success_hook_extra:+
$success_hook_extra}"
combined_failure="${codex_failure_hook}${failure_hook_extra:+
$failure_hook_extra}"

spawn_generate_launcher "$SPAWN_LAUNCHER" \
  "$SPAWN_META" \
  "$SPAWN_REPORT" \
  "$SPAWN_TRANSCRIPT" \
  "$SCRIPT_DIR/common.sh" \
  "$launch_cmd" \
  "" \
  "$combined_success" \
  "$combined_failure"


chmod +x "$SPAWN_LAUNCHER"
spawn_print_launch codex "$mode" "$runtime" "$dry_run"
[[ -n "$model" ]] && printf '  model:  %s\n' "$model" || printf '  model:  (CLI default)\n'
spawn_launch "$SPAWN_LAUNCHER" "$runtime" "$dry_run" "codex-${VIBECRAFTED_SKILL_NAME:-$mode}"
if [[ "${VIBECRAFTED_SUPPRESS_REPORT_HINT:-0}" != "1" ]]; then
  if (( dry_run )); then
    printf 'Dry run: agent not launched.\n'
  else
    printf 'Agent launched.\n'
    bash "$SCRIPT_DIR/await.sh" codex --describe "$SPAWN_LAUNCHER" 2>/dev/null || true
    printf '\nAwait:\n\n'
    printf 'vibecrafted await codex --run-id %s\n' "$SPAWN_RUN_ID"
  fi
fi
