# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_init_runtime() {
  local runtime="${1:-terminal}"
  case "$runtime" in
    terminal|visible|plain)
      printf '%s\n' "$runtime"
      ;;
    *)
      echo "vc-init is interactive-only: use --runtime terminal, visible, or plain (no vc-frame)." >&2
      return 1
      ;;
  esac
}

# Resume payload for the checkout init is about to open. Prints nothing when
# there is no unfinished work, and nothing at all when core is unreachable:
# init must never fail because a convenience could not be computed.
_vetcoders_init_resume_block() {
  local python_spec py import_root
  command -v _vetcoders_core_python_spec >/dev/null 2>&1 || return 0
  python_spec="$(_vetcoders_core_python_spec 2>/dev/null)" || return 0
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  [[ -n "$py" ]] || return 0
  if [[ -n "$import_root" ]]; then
    PYTHONPATH="$import_root${PYTHONPATH:+:$PYTHONPATH}" \
      "$py" -m vibecrafted_core.init_resume --root . 2>/dev/null || true
  else
    "$py" -m vibecrafted_core.init_resume --root . 2>/dev/null || true
  fi
}

_vetcoders_compose_init_prompt() {
  local prompt_text="${1:-}"
  local file_path="${2:-}"
  local init_prompt="/vc-init"
  local extra resume_block

  # Resume is a payload of init, not a verb the operator has to remember.
  resume_block="$(_vetcoders_init_resume_block)"
  if [[ -n "$resume_block" ]]; then
    init_prompt+=$'\n\n'
    init_prompt+="$resume_block"
  fi

  extra="$(_vetcoders_compose_input_context "$prompt_text" "$file_path")" || return 1
  if [[ -n "$extra" ]]; then
    init_prompt+=$'\n\n'
    init_prompt+="$extra"
  fi

  printf '%s' "$init_prompt"
}

_vetcoders_init_command_text() {
  local tool="$1"
  local init_prompt="$2"
  local policy_runtime="${3:-local-native}"
  local permissions="${4:-bypass}"
  local token_budget="${5:-unmetered}"
  local operator_policy="${6:-none}"
  local continuity="${7:-fresh}"
  local parent_session="${8:-}"
  local continuity_parent="${9:-}"
  local python_spec py import_root
  local -a continuity_args=(--continuity "$continuity" --skill "${_vetcoders_interactive_skill:-init}")
  [[ -z "$parent_session" ]] || continuity_args+=(--parent-session "$parent_session")
  [[ -z "$continuity_parent" ]] || continuity_args+=(--continuity-parent "$continuity_parent")
  [[ -z "${_vetcoders_contract_file:-}" ]] || continuity_args+=(--file "$_vetcoders_contract_file")
  [[ -z "${_vetcoders_contract_model:-}" ]] || continuity_args+=(--model "$_vetcoders_contract_model")
  [[ -z "${_vetcoders_contract_base:-}" ]] || continuity_args+=(--base "$_vetcoders_contract_base")
  [[ -z "${_vetcoders_contract_execution_runtime:-}" ]] || continuity_args+=(--execution-runtime "$_vetcoders_contract_execution_runtime")
  [[ -z "${_vetcoders_contract_worktree:-}" ]] || continuity_args+=(--worktree "$_vetcoders_contract_worktree")
  [[ -z "${_vetcoders_contract_session:-}" ]] || continuity_args+=(--session "$_vetcoders_contract_session")
  [[ -z "${_vetcoders_contract_run_id:-}" ]] || continuity_args+=(--resume-run-id "$_vetcoders_contract_run_id")
  [[ -z "${_vetcoders_contract_last:-}" ]] || continuity_args+=(--resume-last)
  [[ -z "${_vetcoders_contract_parent_run_id:-}" ]] || continuity_args+=(--parent-run-id "$_vetcoders_contract_parent_run_id")
  python_spec="$(_vetcoders_core_python_spec)" || return 1
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  local root_arg="${_vetcoders_contract_repo:-${_vetcoders_contract_root:-$PWD}}"
  [[ -z "${_vetcoders_contract_run_id:-}${_vetcoders_contract_last:-}" ]] || root_arg="${_vetcoders_contract_root:-}"
  local -a command=("$py" -m vibecrafted_core.spawn interactive-command "$tool" --runtime "$policy_runtime" --permissions "$permissions" --token-budget "$token_budget" --operator "$operator_policy" "${continuity_args[@]}" --root "$root_arg")
  if [[ -n "$import_root" ]]; then
    printf '%s' "$init_prompt" | VIBECRAFTED_INTERACTIVE_IMPORT_ROOT="$import_root" \
      PYTHONPATH="$import_root${PYTHONPATH:+:$PYTHONPATH}" "${command[@]}"
  else
    printf '%s' "$init_prompt" | "${command[@]}"
  fi
}

# Operator-mode launcher helpers — parallel to init helpers above.
# vc-operator is NOT a dispatchable Iter-3 worker mode; it is an
# interactive session entry point per the vc-init pattern. Invocation
# opens the operator's primary tab in vc_frame with the agent of choice
# preloaded with the /vc-operator skill prompt.

_vetcoders_operator_runtime() {
  local runtime="${1:-terminal}"
  case "$runtime" in
    terminal|visible)
      printf '%s\n' "$runtime"
      ;;
    *)
      echo "vc-operator is interactive-only: use --runtime terminal or visible." >&2
      return 1
      ;;
  esac
}

_vetcoders_compose_operator_prompt() {
  local prompt_text="${1:-}"
  local file_path="${2:-}"
  local operator_prompt="/vc-operator"
  local extra

  extra="$(_vetcoders_compose_input_context "$prompt_text" "$file_path")" || return 1
  if [[ -n "$extra" ]]; then
    operator_prompt+=$'\n\n'
    operator_prompt+="$extra"
  fi

  printf '%s' "$operator_prompt"
}

_vetcoders_operator_command_text() {
  local tool="$1"
  local operator_prompt="$2"
  _vetcoders_init_command_text "$tool" "$operator_prompt" "${3:-local-native}" "${4:-bypass}" "${5:-unmetered}" "${6:-none}" "${7:-fresh}" "${8:-}" "${9:-}"
}

_vetcoders_partner_runtime() {
  _vetcoders_init_runtime "${1:-terminal}"
}

_vetcoders_compose_partner_prompt() {
  local prompt_text="${1:-}"
  local file_path="${2:-}"
  local partner_prompt="/vc-partner"
  local extra

  extra="$(_vetcoders_compose_input_context "$prompt_text" "$file_path")" || return 1
  if [[ -n "$extra" ]]; then
    partner_prompt+=$'\n\n'
    partner_prompt+="$extra"
  fi

  printf '%s' "$partner_prompt"
}

_vetcoders_partner_command_text() {
  local tool="$1"
  local partner_prompt="$2"
  _vetcoders_init_command_text "$tool" "$partner_prompt" "${3:-local-native}" "${4:-bypass}" "${5:-unmetered}" "${6:-none}" "${7:-fresh}" "${8:-}" "${9:-}"
}


# Carry only the admitted command (private file references) across a new window.
# 0: terminal handoff accepted; 2: caller already has a surface; 1: failed.
_vetcoders_enter_admitted_interactive() {
  local verb="$1" command_text="$2" python_spec py import_root
  python_spec="$(_vetcoders_core_python_spec)" || return 1
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  local -a command=("$py" -m vibecrafted_core.spawn interactive-handoff --command "$command_text")
  if [[ -n "$import_root" ]]; then
    command=(/usr/bin/env "PYTHONPATH=$import_root" "${command[@]}")
  fi
  _vetcoders_contract_root="$("${command[@]}" --root-only)" || return 1
  if _vetcoders_needs_vc_terminal_entry; then
    # Array slicing differs between bash and zsh; shift a positional copy.
    set -- "${command[@]}"
    local front_door="$1"
    shift
    _vetcoders_open_entry_in_vc_terminal "$front_door" "$_vetcoders_contract_root" "$@" || return 1
    return 0
  fi
  return 2
}

_vetcoders_exec_admitted_interactive() {
  local command_text="$1" python_spec py import_root
  python_spec="$(_vetcoders_core_python_spec)" || return 1
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  if [[ -n "$import_root" ]]; then
    PYTHONPATH="$import_root" "$py" -m vibecrafted_core.spawn interactive-handoff --command "$command_text"
  else
    "$py" -m vibecrafted_core.spawn interactive-handoff --command "$command_text"
  fi
}
