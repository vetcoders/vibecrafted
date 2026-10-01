#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
  cat <<EOF_USAGE
Usage: copilot_spawn.sh [--mode <mode>] [--runtime <terminal|visible|headless|background|detached>] [--model <model>] [--root <repo-root>] [--dry-run] <plan.md>

Portable GitHub Copilot CLI spawn wrapper.
Defaults to headless. Pass --runtime terminal for a visible vc-frame worker pane.
EOF_USAGE
}

mode="implement"
runtime="headless"
# Operator BYOK pin: ~/.config/vibecrafted/config.toml [agents.copilot.provider]
# (Founder decision 2026-09-14: the one config.toml). Only fills vars the
# environment does not already set, so an explicit export always wins. No
# section present => prints nothing => today's behavior (Copilot's default
# model). Never echoed: these are export statements fed straight to eval.
eval "$(spawn_python_module vibecrafted_core.server_config copilot-provider-env 2>/dev/null || true)"
model="${COPILOT_MODEL:-}"
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
spawn_prepare_paths copilot "$plan_file" "$root" "$mode" "$dry_run"
spawn_scan_active "${SPAWN_LOG_DIR:-$SPAWN_REPORT_DIR}"
runtime_input="$SPAWN_TMP_DIR/${SPAWN_TS}_${SPAWN_RUN_ID}_${SPAWN_SLUG}_copilot_prompt.md"
spawn_build_runtime_prompt "$SPAWN_PLAN" "$runtime_input" "$SPAWN_REPORT" copilot "$model"
spawn_write_meta "$SPAWN_META" "launching" "copilot" "$mode" "$SPAWN_ROOT" "$SPAWN_PLAN" "$SPAWN_REPORT" "$SPAWN_TRANSCRIPT" "$SPAWN_LAUNCHER" "$model"

if (( !dry_run )); then
  spawn_require_command copilot
fi

qroot="$(spawn_shell_quote "$SPAWN_ROOT")"
qruntime="$(spawn_shell_quote "$runtime_input")"
qtranscript="$(spawn_shell_quote "$SPAWN_TRANSCRIPT")"
qlast_message="$(spawn_shell_quote "${SPAWN_TRANSCRIPT%.log}.last-message.md")"
qmodel="$(spawn_shell_quote "$model")"

# shellcheck disable=SC2016  # hook source is expanded by the launcher when the hook runs
copilot_success_hook='
  if [[ ! -s "$report" ]]; then
    spawn_write_frontmatter "$report" "$SPAWN_AGENT" "${SPAWN_MODEL:-unknown}" "completed"
    cat >> "$report" <<TXT
Copilot completed without writing a standalone report file, and no final message was captured.
See transcript for the full event stream:
$transcript
Last message path checked:
${transcript%.log}.last-message.md
TXT
  fi'

# shellcheck disable=SC2016  # hook source is expanded by the launcher when the hook runs
copilot_failure_hook='
  if [[ ! -s "$report" ]]; then
    spawn_write_frontmatter "$report" "$SPAWN_AGENT" "${SPAWN_MODEL:-unknown}" "failed"
    cat >> "$report" <<TXT
Copilot failed before writing a standalone report file, and no final message was captured.
See transcript for the full event stream:
$transcript
Last message path checked:
${transcript%.log}.last-message.md
TXT
  fi'

model_flag=""
[[ -n "$model" ]] && model_flag="--model $qmodel"
# Copilot accepts piped prompt text in non-interactive mode. Never pass -p:
# with -p, stdin is ignored and the prompt would be visible in the process list.
filter_core="$(spawn_python_core_path 2>/dev/null || { cd "$SCRIPT_DIR/../../.." && pwd; })"
qfilter_py="$(spawn_shell_quote "$(spawn_python_bin)")"
qfilter_core="$(spawn_shell_quote "$filter_core")"
qfilter_cmd="PYTHONPATH=$qfilter_core $qfilter_py -m vibecrafted_core.agent_stream --agent copilot --last-message $qlast_message"
launch_cmd="set -o pipefail && cd $qroot && { rm -f $qlast_message; copilot --allow-all --no-ask-user --no-auto-update --output-format json $model_flag < $qruntime 2>&1 | tee -a $qtranscript | $qfilter_cmd; pipeline_status=\$?; exit \$pipeline_status; }"

combined_success="${copilot_success_hook}${success_hook_extra:+
$success_hook_extra}"
combined_failure="${copilot_failure_hook}${failure_hook_extra:+
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
spawn_print_launch copilot "$mode" "$runtime"
[[ -n "$model" ]] && printf '  model:  %s\n' "$model" || printf '  model:  (CLI default)\n'
spawn_launch "$SPAWN_LAUNCHER" "$runtime" "$dry_run" "copilot-${VIBECRAFTED_SKILL_NAME:-$mode}"
if [[ "${VIBECRAFTED_SUPPRESS_REPORT_HINT:-0}" != "1" ]]; then
  printf 'Agent launched.\n'
  bash "$SCRIPT_DIR/await.sh" copilot --describe "$SPAWN_LAUNCHER" 2>/dev/null || true
  printf '\nAwait:\n\n'
  printf 'vibecrafted await copilot --run-id %s\n' "$SPAWN_RUN_ID"
fi
