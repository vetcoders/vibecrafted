#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
  cat <<EOF_USAGE
Usage: junie_spawn.sh [--mode <mode>] [--runtime <terminal|visible|headless|background|detached>] [--model <model>] [--root <repo-root>] [--dry-run] <plan.md>

Portable JetBrains Junie spawn wrapper.
Defaults to headless. Pass --runtime terminal for a visible vc-frame worker pane.
EOF_USAGE
}

mode="implement"
runtime="headless"
model="${JUNIE_MODEL:-}"
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
spawn_prepare_paths junie "$plan_file" "$root" "$mode" "$dry_run"
spawn_scan_active "${SPAWN_LOG_DIR:-$SPAWN_REPORT_DIR}"
runtime_input="$SPAWN_TMP_DIR/${SPAWN_TS}_${SPAWN_RUN_ID}_${SPAWN_SLUG}_junie_prompt.md"
spawn_build_runtime_prompt "$SPAWN_PLAN" "$runtime_input" "$SPAWN_REPORT" junie "$model"
spawn_write_meta "$SPAWN_META" "launching" "junie" "$mode" "$SPAWN_ROOT" "$SPAWN_PLAN" "$SPAWN_REPORT" "$SPAWN_TRANSCRIPT" "$SPAWN_LAUNCHER" "$model"

if (( !dry_run )); then
  spawn_require_command junie
fi

qroot="$(spawn_shell_quote "$SPAWN_ROOT")"
qruntime="$(spawn_shell_quote "$runtime_input")"
qreport="$(spawn_shell_quote "$SPAWN_REPORT")"
qtranscript="$(spawn_shell_quote "$SPAWN_TRANSCRIPT")"
qlast_message="$(spawn_shell_quote "${SPAWN_TRANSCRIPT%.log}.last-message.md")"
qmodel="$(spawn_shell_quote "$model")"

# shellcheck disable=SC2016  # hook source is expanded by the launcher when the hook runs
junie_success_hook='
  if [[ ! -s "$report" ]]; then
    spawn_write_frontmatter "$report" "$SPAWN_AGENT" "${SPAWN_MODEL:-unknown}" "completed"
    cat >> "$report" <<TXT
Junie completed without writing a standalone report file, and no final message was captured.
See transcript for the full event stream:
$transcript
Last message path checked:
${transcript%.log}.last-message.md
TXT
  fi'

# shellcheck disable=SC2016  # hook source is expanded by the launcher when the hook runs
junie_failure_hook='
  if [[ ! -s "$report" ]]; then
    spawn_write_frontmatter "$report" "$SPAWN_AGENT" "${SPAWN_MODEL:-unknown}" "failed"
    cat >> "$report" <<TXT
Junie failed before writing a standalone report file, and no final message was captured.
See transcript for the full event stream:
$transcript
Last message path checked:
${transcript%.log}.last-message.md
TXT
  fi'

model_flag=""
[[ -n "$model" ]] && model_flag="--model=$qmodel"
review_flag=""
[[ "$mode" == "review" ]] && review_flag="--review"
timeout_flag=""
[[ -n "${JUNIE_TIMEOUT_MS:-}" ]] && timeout_flag="--timeout=${JUNIE_TIMEOUT_MS}"
last_message_extract="if [[ -s $qtranscript ]]; then cp $qtranscript $qlast_message 2>/dev/null || rm -f $qlast_message; [[ -s $qlast_message ]] || rm -f $qlast_message; fi;"
salvage_success_report="if [[ \$pipeline_status -eq 0 && ! -s $qreport && -s $qlast_message ]]; then { printf '%s\n' '---'; printf 'run_id: %s\n' \"\${SPAWN_RUN_ID:-unknown}\"; printf 'prompt_id: %s\n' \"\${SPAWN_PROMPT_ID:-unknown}\"; printf 'agent: %s\n' \"\${SPAWN_AGENT:-junie}\"; printf 'skill: %s\n' \"\${SPAWN_SKILL_CODE:-unknown}\"; printf 'model: %s\n' \"\${SPAWN_MODEL:-unknown}\"; printf 'status: completed\n'; printf 'session_id: %s\n' \"\${SPAWN_SESSION_ID:-pending}\"; printf 'repo_path: %s\n' \"\${SPAWN_ROOT:-unknown}\"; printf 'tokens_input: 0\n'; printf 'tokens_output: 0\n'; printf 'tokens_total: 0\n'; printf 'cost_usd: unknown\n'; printf '%s\n\n' '---'; cat $qlast_message; } > $qreport || pipeline_status=\$?; fi;"
salvage_failure_report="if [[ \$pipeline_status -ne 0 && ! -s $qreport ]]; then { printf '%s\n' '---'; printf 'run_id: %s\n' \"\${SPAWN_RUN_ID:-unknown}\"; printf 'prompt_id: %s\n' \"\${SPAWN_PROMPT_ID:-unknown}\"; printf 'agent: %s\n' \"\${SPAWN_AGENT:-junie}\"; printf 'skill: %s\n' \"\${SPAWN_SKILL_CODE:-unknown}\"; printf 'model: %s\n' \"\${SPAWN_MODEL:-unknown}\"; printf 'status: failed\n'; printf 'session_id: %s\n' \"\${SPAWN_SESSION_ID:-pending}\"; printf 'repo_path: %s\n' \"\${SPAWN_ROOT:-unknown}\"; printf 'tokens_input: 0\n'; printf 'tokens_output: 0\n'; printf 'tokens_total: 0\n'; printf 'cost_usd: unknown\n'; printf '%s\n\n' '---'; if [[ -s $qlast_message ]]; then cat $qlast_message; else printf '%s\n' 'Junie failed before writing a standalone report file, and no final message was captured.'; printf '%s\n' 'See transcript for the full event stream:'; printf '%s\n' $qtranscript; printf '%s\n' 'Last message path checked:'; printf '%s\n' $qlast_message; fi; } > $qreport; fi;"
junie_invocation="junie --project=$qroot --task=\"\$(cat $qruntime)\" --skip-update-check --use-local-cache --input-format=text --output-format=json-stream $model_flag $review_flag $timeout_flag 2>&1 | tee -a $qtranscript"
launch_cmd="set -o pipefail && cd $qroot && { rm -f $qlast_message; pipeline_status=0; for junie_attempt in 1 2; do $junie_invocation; pipeline_status=\$?; [[ \$pipeline_status -eq 0 ]] && break; if [[ \$junie_attempt -eq 1 ]] && grep -Fq \"Failed to build 'issue.md.junie_standalone'\" $qtranscript; then printf '%s\n' 'vibecrafted: transient Junie issue-template failure; retrying once' | tee -a $qtranscript; sleep \"\${JUNIE_ISSUE_RETRY_DELAY_SECONDS:-2}\"; continue; fi; break; done; $last_message_extract $salvage_success_report $salvage_failure_report exit \$pipeline_status; }"

combined_success="${junie_success_hook}${success_hook_extra:+
$success_hook_extra}"
combined_failure="${junie_failure_hook}${failure_hook_extra:+
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
spawn_print_launch junie "$mode" "$runtime"
[[ -n "$model" ]] && printf '  model:  %s\n' "$model" || printf '  model:  (CLI default)\n'
spawn_launch "$SPAWN_LAUNCHER" "$runtime" "$dry_run" "junie-${VIBECRAFTED_SKILL_NAME:-$mode}"
if [[ "${VIBECRAFTED_SUPPRESS_REPORT_HINT:-0}" != "1" ]]; then
  printf 'Agent launched.\n'
  bash "$SCRIPT_DIR/await.sh" junie --describe "$SPAWN_LAUNCHER" 2>/dev/null || true
  printf '\nAwait:\n\n'
  printf 'vibecrafted await junie --run-id %s\n' "$SPAWN_RUN_ID"
fi
