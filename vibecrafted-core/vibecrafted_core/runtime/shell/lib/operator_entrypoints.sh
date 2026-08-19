# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_skill_init() {
  local tool="$1"
  shift
  local runtime init_prompt command_text

  _vetcoders_parse_contract "$@" || return 1
  [[ -z "$_vetcoders_contract_count" ]] || {
    echo "--count is not supported by vibecrafted init." >&2
    return 1
  }
  [[ -z "$_vetcoders_contract_depth" ]] || {
    echo "--depth is not supported by vibecrafted init." >&2
    return 1
  }
  [[ -z "$_vetcoders_contract_session" ]] || {
    echo "--session is not supported by vibecrafted init." >&2
    return 1
  }

  _vetcoders_require_vc_frame || return 1

  runtime="$(_vetcoders_init_runtime "${_vetcoders_contract_runtime:-terminal}")" || return 1
  init_prompt="$(_vetcoders_compose_init_prompt "$_vetcoders_contract_prompt" "$_vetcoders_contract_file")" || return 1
  command_text="$(_vetcoders_init_command_text "$tool" "$init_prompt")" || return 1

  _vetcoders_prepare_operator_runtime "$runtime" || return 1
  _vetcoders_spawn_into_operator_session "$(_vetcoders_operator_face_tab "$tool")" "$command_text"
}

# vc-operator launcher — interactive operator session entry point.
# Behaves like _vetcoders_skill_init: spawns a vc_frame session with the
# selected agent preloaded with the /vc-operator skill prompt. NOT a
# background Iter-3 dispatchable mode.
_vetcoders_skill_operator() {
  local tool="$1"
  shift
  local runtime operator_prompt command_text

  _vetcoders_parse_contract "$@" || return 1
  [[ -z "$_vetcoders_contract_count" ]] || {
    echo "--count is not supported by vibecrafted operator." >&2
    return 1
  }
  [[ -z "$_vetcoders_contract_depth" ]] || {
    echo "--depth is not supported by vibecrafted operator." >&2
    return 1
  }
  [[ -z "$_vetcoders_contract_session" ]] || {
    echo "--session is not supported by vibecrafted operator." >&2
    return 1
  }

  _vetcoders_require_vc_frame || return 1

  runtime="$(_vetcoders_operator_runtime "${_vetcoders_contract_runtime:-terminal}")" || return 1
  operator_prompt="$(_vetcoders_compose_operator_prompt "$_vetcoders_contract_prompt" "$_vetcoders_contract_file")" || return 1
  command_text="$(_vetcoders_operator_command_text "$tool" "$operator_prompt")" || return 1

  _vetcoders_prepare_operator_runtime "$runtime" || return 1
  _vetcoders_spawn_into_operator_session "$(_vetcoders_operator_face_tab "$tool")" "$command_text"
}

