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
  local token_budget="${5:-safe}"
  local python_spec py import_root
  python_spec="$(_vetcoders_core_python_spec)" || return 1
  py="${python_spec%%$'\t'*}"
  import_root="${python_spec#*$'\t'}"
  if [[ -n "$import_root" ]]; then
    printf '%s' "$init_prompt" | VIBECRAFTED_INTERACTIVE_IMPORT_ROOT="$import_root" \
      PYTHONPATH="$import_root${PYTHONPATH:+:$PYTHONPATH}" \
      "$py" -m vibecrafted_core.spawn interactive-command "$tool" --runtime "$policy_runtime" --permissions "$permissions" --token-budget "$token_budget" --root "${_vetcoders_contract_root:-$(_vetcoders_repo_root)}"
  else
    printf '%s' "$init_prompt" | "$py" -m vibecrafted_core.spawn interactive-command "$tool" --runtime "$policy_runtime" --permissions "$permissions" --token-budget "$token_budget" --root "${_vetcoders_contract_root:-$(_vetcoders_repo_root)}"
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
  _vetcoders_init_command_text "$tool" "$operator_prompt" "${3:-local-native}" "${4:-bypass}"
}
