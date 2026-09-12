#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
  cat <<EOF_USAGE
Usage: cursor_spawn.sh [--mode <mode>] [--runtime <terminal|visible|headless|background|detached>] [--model <model>] [--root <repo-root>] [--dry-run] <plan.md>

Portable Cursor (cursor-agent) spawn wrapper.
Defaults to headless. Pass --runtime terminal for a visible vc-frame worker pane.
EOF_USAGE
}

mode="implement"
runtime="headless"
model="${CURSOR_MODEL:-}"
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
spawn_prepare_paths cursor "$plan_file" "$root" "$mode" "$dry_run"
spawn_scan_active "${SPAWN_LOG_DIR:-$SPAWN_REPORT_DIR}"
runtime_input="$SPAWN_TMP_DIR/${SPAWN_TS}_${SPAWN_RUN_ID}_${SPAWN_SLUG}_cursor_prompt.md"
spawn_build_runtime_prompt "$SPAWN_PLAN" "$runtime_input" "$SPAWN_REPORT" cursor "$model"
spawn_write_meta "$SPAWN_META" "launching" "cursor" "$mode" "$SPAWN_ROOT" "$SPAWN_PLAN" "$SPAWN_REPORT" "$SPAWN_TRANSCRIPT" "$SPAWN_LAUNCHER" "$model"

if (( !dry_run )); then
  spawn_require_command cursor-agent
fi

qroot="$(spawn_shell_quote "$SPAWN_ROOT")"
qruntime="$(spawn_shell_quote "$runtime_input")"
qreport="$(spawn_shell_quote "$SPAWN_REPORT")"
qtranscript="$(spawn_shell_quote "$SPAWN_TRANSCRIPT")"
qlast_message="$(spawn_shell_quote "${SPAWN_TRANSCRIPT%.log}.last-message.md")"
qmodel="$(spawn_shell_quote "$model")"

# shellcheck disable=SC2016
cursor_success_hook='
  if [[ ! -s "$report" ]]; then
    spawn_write_frontmatter "$report" "$SPAWN_AGENT" "${SPAWN_MODEL:-unknown}" "completed"
    cat >> "$report" <<TXT
Cursor completed without writing a standalone report file, and no final message was captured.
See transcript for the full event stream:
$transcript
Last message path checked:
${transcript%.log}.last-message.md
TXT
  fi'

# shellcheck disable=SC2016
cursor_failure_hook='
  if [[ ! -s "$report" ]]; then
    spawn_write_frontmatter "$report" "$SPAWN_AGENT" "${SPAWN_MODEL:-unknown}" "failed"
    cat >> "$report" <<TXT
Cursor failed before writing a standalone report file, and no final message was captured.
See transcript for the full event stream:
$transcript
Last message path checked:
${transcript%.log}.last-message.md
TXT
  fi'

model_flag=""
[[ -n "$model" ]] && model_flag="--model $qmodel"
last_message_extract="if [[ -s $qtranscript ]]; then cp $qtranscript $qlast_message 2>/dev/null || rm -f $qlast_message; [[ -s $qlast_message ]] || rm -f $qlast_message; fi;"
salvage_success_report="if [[ \$pipeline_status -eq 0 && ! -s $qreport && -s $qlast_message ]]; then { printf '%s\n' '---'; printf 'run_id: %s\n' \"\${SPAWN_RUN_ID:-unknown}\"; printf 'prompt_id: %s\n' \"\${SPAWN_PROMPT_ID:-unknown}\"; printf 'agent: %s\n' \"\${SPAWN_AGENT:-cursor}\"; printf 'skill: %s\n' \"\${SPAWN_SKILL_CODE:-unknown}\"; printf 'model: %s\n' \"\${SPAWN_MODEL:-unknown}\"; printf 'status: completed\n'; printf 'session_id: %s\n' \"\${SPAWN_SESSION_ID:-pending}\"; printf 'repo_path: %s\n' \"\${SPAWN_ROOT:-unknown}\"; printf 'tokens_input: 0\n'; printf 'tokens_output: 0\n'; printf 'tokens_total: 0\n'; printf 'cost_usd: unknown\n'; printf '%s\n\n' '---'; cat $qlast_message; } > $qreport || pipeline_status=\$?; fi;"
salvage_failure_report="if [[ \$pipeline_status -ne 0 && ! -s $qreport ]]; then { printf '%s\n' '---'; printf 'run_id: %s\n' \"\${SPAWN_RUN_ID:-unknown}\"; printf 'prompt_id: %s\n' \"\${SPAWN_PROMPT_ID:-unknown}\"; printf 'agent: %s\n' \"\${SPAWN_AGENT:-cursor}\"; printf 'skill: %s\n' \"\${SPAWN_SKILL_CODE:-unknown}\"; printf 'model: %s\n' \"\${SPAWN_MODEL:-unknown}\"; printf 'status: failed\n'; printf 'session_id: %s\n' \"\${SPAWN_SESSION_ID:-pending}\"; printf 'repo_path: %s\n' \"\${SPAWN_ROOT:-unknown}\"; printf 'tokens_input: 0\n'; printf 'tokens_output: 0\n'; printf 'tokens_total: 0\n'; printf 'cost_usd: unknown\n'; printf '%s\n\n' '---'; if [[ -s $qlast_message ]]; then cat $qlast_message; else printf '%s\n' 'Cursor failed before writing a standalone report file, and no final message was captured.'; printf '%s\n' 'See transcript for the full event stream:'; printf '%s\n' $qtranscript; printf '%s\n' 'Last message path checked:'; printf '%s\n' $qlast_message; fi; } > $qreport; fi;"
# Human pane: AgentStreamParser. Raw stream-json teed to transcript for await.
# cursor-agent emits claude-shaped init/assistant/result events; `-p` reads the
# prompt from stdin. `--force --trust` mirror the headless bypass policy in
# spawn.py (PERMISSION_POLICIES cursor lane).
# Prefer the runtime's shared resolver because checkout launchers live at
# <repo>/runtime/scripts while wheel launchers live under vibecrafted_core.
filter_core="$(spawn_python_core_path 2>/dev/null || { cd "$SCRIPT_DIR/../../.." && pwd; })"
qfilter_py="$(spawn_shell_quote "$(spawn_python_bin)")"
qfilter_core="$(spawn_shell_quote "$filter_core")"
qfilter_cmd="PYTHONPATH=$qfilter_core $qfilter_py -m vibecrafted_core.agent_stream --agent cursor"
launch_cmd="set -o pipefail && cd $qroot && { rm -f $qlast_message; cursor-agent -p --output-format stream-json --force --trust $model_flag < $qruntime 2>&1 | tee -a $qtranscript | $qfilter_cmd; pipeline_status=\$?; $last_message_extract $salvage_success_report $salvage_failure_report exit \$pipeline_status; }"

combined_success="${cursor_success_hook}${success_hook_extra:+
$success_hook_extra}"
combined_failure="${cursor_failure_hook}${failure_hook_extra:+
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
spawn_print_launch cursor "$mode" "$runtime"
[[ -n "$model" ]] && printf '  model:  %s\n' "$model" || printf '  model:  (CLI default)\n'
spawn_launch "$SPAWN_LAUNCHER" "$runtime" "$dry_run" "cursor-${VIBECRAFTED_SKILL_NAME:-$mode}"
if [[ "${VIBECRAFTED_SUPPRESS_REPORT_HINT:-0}" != "1" ]]; then
  printf 'Agent launched.\n'
  bash "$SCRIPT_DIR/await.sh" cursor --describe "$SPAWN_LAUNCHER" 2>/dev/null || true
  printf '\nAwait:\n\n'
  printf 'vibecrafted await cursor --run-id %s\n' "$SPAWN_RUN_ID"
fi
