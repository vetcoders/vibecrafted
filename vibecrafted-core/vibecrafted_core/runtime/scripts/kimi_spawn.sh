#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
  cat <<EOF_USAGE
Usage: kimi_spawn.sh [--mode <mode>] [--runtime <terminal|visible|headless|background|detached>] [--root <repo-root>] [--dry-run] <plan.md>

Portable Kimi Code CLI (kimi) spawn wrapper.
Defaults to headless. Pass --runtime terminal for a visible vc-frame worker pane.
kimi 0.42.0 has no stdin prompt lane: -p/--prompt is argv-only and there is no
--prompt-file, so the launcher inlines the prompt as argv ("kimi -p \"\$(cat ...)\"").
That exposes the prompt to ps(1) on the local host and bounds it by ARG_MAX
(~1MiB on macOS); this wrapper refuses prompts >= 900KiB. Print mode never asks
for permission (never-ask), so no permission flags are passed. Model comes from
KIMI_MODEL at spawn time or SPAWN_MODEL inside the launcher.
EOF_USAGE
}

mode="implement"
runtime="headless"
model="${KIMI_MODEL:-}"
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
spawn_prepare_paths kimi "$plan_file" "$root" "$mode" "$dry_run"
spawn_scan_active "${SPAWN_LOG_DIR:-$SPAWN_REPORT_DIR}"
runtime_input="$SPAWN_TMP_DIR/${SPAWN_TS}_${SPAWN_RUN_ID}_${SPAWN_SLUG}_kimi_prompt.md"
spawn_build_runtime_prompt "$SPAWN_PLAN" "$runtime_input" "$SPAWN_REPORT" kimi
# kimi reads its prompt from argv only (-p "$(cat ...)"). The launcher expands
# the private 0600 prompt file at run time; nothing is written to stdin and no
# NDJSON carrier is needed. Guard the argv ceiling: ARG_MAX is ~1MiB on macOS
# and the rest of argv/env also lives in that budget.
prompt_bytes="$(wc -c < "$runtime_input" | tr -d '[:space:]')"
if (( prompt_bytes >= 900 * 1024 )); then
  spawn_die "Prompt is ${prompt_bytes} bytes; kimi takes the prompt on argv (-p) and ARG_MAX would be exceeded. Split the plan into smaller cuts."
fi
if (( prompt_bytes >= 200 * 1024 )); then
  printf 'warning: prompt is %s bytes; kimi takes the prompt on argv (-p), visible to ps(1) and bounded by ARG_MAX.\n' "$prompt_bytes" >&2
fi
spawn_write_meta "$SPAWN_META" "launching" "kimi" "$mode" "$SPAWN_ROOT" "$SPAWN_PLAN" "$SPAWN_REPORT" "$SPAWN_TRANSCRIPT" "$SPAWN_LAUNCHER"

if (( !dry_run )); then
  spawn_require_command kimi
fi

qroot="$(spawn_shell_quote "$SPAWN_ROOT")"
qprompt="$(spawn_shell_quote "$runtime_input")"
qreport="$(spawn_shell_quote "$SPAWN_REPORT")"
qtranscript="$(spawn_shell_quote "$SPAWN_TRANSCRIPT")"
qlast_message="$(spawn_shell_quote "${SPAWN_TRANSCRIPT%.log}.last-message.md")"
qmodel="$(spawn_shell_quote "$model")"
filter_core="$(spawn_python_core_path 2>/dev/null || { cd "$SCRIPT_DIR/../../.." && pwd; })"

# shellcheck disable=SC2016
kimi_success_hook='
  if [[ ! -s "$report" ]]; then
    spawn_write_frontmatter "$report" "$SPAWN_AGENT" "unknown" "completed"
    cat >> "$report" <<TXT
Kimi completed without writing a standalone report file, and no final message was captured.
See transcript for the full event stream:
$transcript
Last message path checked:
${transcript%.log}.last-message.md
TXT
  fi'

# shellcheck disable=SC2016
kimi_failure_hook='
  if [[ ! -s "$report" ]]; then
    spawn_write_frontmatter "$report" "$SPAWN_AGENT" "unknown" "failed"
    cat >> "$report" <<TXT
Kimi failed before writing a standalone report file, and no final message was captured.
See transcript for the full event stream:
$transcript
Last message path checked:
${transcript%.log}.last-message.md
TXT
  fi'

# Static --model when the spawn caller pinned one; otherwise the launcher's
# SPAWN_MODEL (exported by launcher.sh) decides at run time.
model_flag=""
if [[ -n "$model" ]]; then
  model_flag="--model $qmodel"
else
  # Expanded by the launcher at run time, on purpose (SPAWN_MODEL is exported there).
  # shellcheck disable=SC2016
  model_flag='${SPAWN_MODEL:+--model "$SPAWN_MODEL"}'
fi
# Human pane: AgentStreamParser --agent kimi. Raw stream-json teed to the
# transcript for await/meta (grok parity). The last message is the final
# assistant content of the stream; the whole transcript is the fallback.
qfilter_py="$(spawn_shell_quote "$(spawn_python_bin)")"
qfilter_core="$(spawn_shell_quote "$filter_core")"
qfilter_cmd="PYTHONPATH=$qfilter_core $qfilter_py -m vibecrafted_core.agent_stream --agent kimi --last-message $qlast_message"
# The filter writes the final assistant content to the last-message path; a
# stream that ended without one falls back to the raw transcript.
last_message_extract="if [[ ! -s $qlast_message && -s $qtranscript ]]; then cp $qtranscript $qlast_message 2>/dev/null || rm -f $qlast_message; fi; [[ -s $qlast_message ]] || rm -f $qlast_message;"
salvage_success_report="if [[ \$pipeline_status -eq 0 && ! -s $qreport && -s $qlast_message ]]; then { printf '%s\n' '---'; printf 'run_id: %s\n' \"\${SPAWN_RUN_ID:-unknown}\"; printf 'prompt_id: %s\n' \"\${SPAWN_PROMPT_ID:-unknown}\"; printf 'agent: %s\n' \"\${SPAWN_AGENT:-kimi}\"; printf 'skill: %s\n' \"\${SPAWN_SKILL_CODE:-unknown}\"; printf 'model: %s\n' \"\${SPAWN_MODEL:-unknown}\"; printf 'status: completed\n'; printf 'session_id: %s\n' \"\${SPAWN_SESSION_ID:-pending}\"; printf 'repo_path: %s\n' \"\${SPAWN_ROOT:-unknown}\"; printf 'tokens_input: 0\n'; printf 'tokens_output: 0\n'; printf 'tokens_total: 0\n'; printf 'cost_usd: unknown\n'; printf '%s\n\n' '---'; cat $qlast_message; } > $qreport || pipeline_status=\$?; fi;"
salvage_failure_report="if [[ \$pipeline_status -ne 0 && ! -s $qreport ]]; then { printf '%s\n' '---'; printf 'run_id: %s\n' \"\${SPAWN_RUN_ID:-unknown}\"; printf 'prompt_id: %s\n' \"\${SPAWN_PROMPT_ID:-unknown}\"; printf 'agent: %s\n' \"\${SPAWN_AGENT:-kimi}\"; printf 'skill: %s\n' \"\${SPAWN_SKILL_CODE:-unknown}\"; printf 'model: %s\n' \"\${SPAWN_MODEL:-unknown}\"; printf 'status: failed\n'; printf 'session_id: %s\n' \"\${SPAWN_SESSION_ID:-pending}\"; printf 'repo_path: %s\n' \"\${SPAWN_ROOT:-unknown}\"; printf 'tokens_input: 0\n'; printf 'tokens_output: 0\n'; printf 'tokens_total: 0\n'; printf 'cost_usd: unknown\n'; printf '%s\n\n' '---'; if [[ -s $qlast_message ]]; then cat $qlast_message; else printf '%s\n' 'Kimi failed before writing a standalone report file, and no final message was captured.'; printf '%s\n' 'See transcript for the full event stream:'; printf '%s\n' $qtranscript; printf '%s\n' 'Last message path checked:'; printf '%s\n' $qlast_message; fi; } > $qreport; fi;"
# kimi -p implies print mode (never-ask): no permission flags exist that
# combine with --prompt. The prompt is inlined on argv from the private file.
# shellcheck disable=SC2016
launch_cmd="set -o pipefail && cd $qroot && { rm -f $qlast_message; kimi -p \"\$(cat $qprompt)\" --output-format stream-json $model_flag 2>&1 | tee -a $qtranscript | $qfilter_cmd; pipeline_status=\$?; $last_message_extract $salvage_success_report $salvage_failure_report exit \$pipeline_status; }"

combined_success="${kimi_success_hook}${success_hook_extra:+
$success_hook_extra}"
combined_failure="${kimi_failure_hook}${failure_hook_extra:+
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
spawn_print_launch kimi "$mode" "$runtime" "$dry_run"
[[ -n "$model" ]] && printf '  model:  %s\n' "$model" || printf '  model:  (SPAWN_MODEL or CLI default)\n'
spawn_launch "$SPAWN_LAUNCHER" "$runtime" "$dry_run" "kimi-${VIBECRAFTED_SKILL_NAME:-$mode}"
if [[ "${VIBECRAFTED_SUPPRESS_REPORT_HINT:-0}" != "1" ]]; then
  if (( dry_run )); then
    printf 'Dry run: agent not launched.\n'
  else
    printf 'Agent launched.\n'
    bash "$SCRIPT_DIR/await.sh" kimi --describe "$SPAWN_LAUNCHER" 2>/dev/null || true
    printf '\nAwait:\n\n'
    printf 'vibecrafted await kimi --run-id %s\n' "$SPAWN_RUN_ID"
  fi
fi
