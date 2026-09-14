# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_spawn_plan() {
  local tool="$1"
  local mode="$2"
  local plan_file="$3"
  shift 3
  local script root arg prev_arg=""
  local runtime
  runtime="$(_vetcoders_default_runtime)"
  for arg in "$@"; do
    if [[ "$prev_arg" == "--runtime" ]]; then
      runtime="$arg"
      break
    fi
    prev_arg="$arg"
  done
  root="$(_vetcoders_spawn_root_arg "$@" 2>/dev/null || true)"
  [[ -n "$root" ]] || root="$(_vetcoders_repo_root)"
  _vetcoders_ensure_run_context "$tool" "$mode" "$root"
  _vetcoders_prepare_operator_runtime "$runtime" || return 1
  script="$(_vetcoders_spawn_script "$tool" "${tool}_spawn.sh")" || return 1
  bash "$script" --mode "$mode" "$plan_file" "$@"
}

_vetcoders_prompt_text() {
  local tool="$1"
  local mode="$2"
  local prompt_text="$3"
  shift 3
  local prompt_file
  prompt_file="$(_vetcoders_prompt_file "$tool" "$prompt_text")" || return 1
  _vetcoders_spawn_plan "$tool" "$mode" "$prompt_file" "$@"
}

_vetcoders_prompt() {
  local tool="$1"
  local mode="$2"
  shift 2
  local prompt_file
  prompt_file="$(_vetcoders_prompt_file "$tool" "$@")" || return 1
  _vetcoders_spawn_plan "$tool" "$mode" "$prompt_file" --runtime "$(_vetcoders_default_runtime)"
}

_vetcoders_dispatch_skill_prompt() {
  local tool="$1"
  local skill="$2"
  local skill_code="$3"
  local loop_nr="$4"
  local run_id="$5"
  local run_lock="$6"
  local prompt="$7"
  shift 7

  (
    export VIBECRAFTED_RUN_ID="$run_id"
    export VIBECRAFTED_RUN_LOCK="$run_lock"
    export VIBECRAFTED_SKILL_CODE="$skill_code"
    export VIBECRAFTED_LOOP_NR="$loop_nr"
    export VIBECRAFTED_SKILL_NAME="$skill"
    _vetcoders_prompt_text "$tool" implement "$prompt" "$@"
  )
}

