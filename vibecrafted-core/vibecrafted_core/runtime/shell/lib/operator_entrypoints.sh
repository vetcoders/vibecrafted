# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_skill_init() {
  local tool="$1"
  shift
  local runtime init_prompt command_text permissions

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

  runtime="$(_vetcoders_init_runtime "${_vetcoders_contract_runtime:-terminal}")" || return 1
  init_prompt="$(_vetcoders_compose_init_prompt "$_vetcoders_contract_prompt" "$_vetcoders_contract_file")" || return 1
  permissions="${_vetcoders_contract_permissions:-}"
  [[ -n "$permissions" ]] || { [[ "$tool" == "junie" ]] && permissions="auto" || permissions="bypass"; }
  command_text="$(_vetcoders_init_command_text "$tool" "$init_prompt" "${_vetcoders_contract_policy_runtime:-local-native}" "$permissions" "${_vetcoders_contract_token_budget:-safe}" "${_vetcoders_contract_operator:-none}" "${_vetcoders_contract_continuity:-fresh}" "${_vetcoders_contract_parent_session:-}" "${_vetcoders_contract_continuity_parent:-}")" || return 1

  # No cockpit, or an explicit `--runtime plain`: the orientation session is
  # the agent itself, so run it right here in the caller's terminal. A fresh
  # install without vc-frame must not dead-end on "run vc-start first" when
  # vc-start needs the very same binary.
  if [[ "$runtime" == "plain" ]] || ! _vetcoders_vc_frame_bin >/dev/null ; then
    _vetcoders_init_in_current_terminal "$tool" "$command_text" "$runtime" "init"
    return
  fi

  _vetcoders_prepare_operator_runtime "$runtime" || return 1
  _vetcoders_spawn_into_operator_session "$(_vetcoders_operator_face_tab "$tool")" "$command_text"
}

# Plain-terminal init: no vc-frame tab, no layout — the agent starts in this
# shell at the repository root with the /vc-init prompt preloaded.
_vetcoders_init_in_current_terminal() {
  local tool="$1"
  local command_text="$2"
  local runtime="${3:-plain}"
  local verb="${4:-init}"
  local root_dir="${_vetcoders_contract_root:-$(_vetcoders_repo_root)}"
  if [[ "$runtime" != "plain" ]]; then
    printf 'vc-frame cockpit not installed — starting %s in this terminal instead (vibecrafted %s %s --runtime plain does the same explicitly).\n' "$tool" "$verb" "$tool" >&2
  fi
  if [[ ! -t 0 || ! -t 1 ]]; then
    if [[ "$verb" == "partner" ]]; then
      printf '%s\n' "\`vc-partner\` is available from interactive agent session. Use vc-init first, and then trigger the skill from the active session" >&2
    else
      printf 'vibecrafted %s needs an interactive terminal for %s; for a non-interactive run use: vibecrafted %s %s --prompt "<task>"\n' "$verb" "$tool" "$verb" "$tool" >&2
    fi
    return 1
  fi
  ( cd "$root_dir" && eval "$command_text" )
}

# vc-operator launcher — interactive operator session entry point.
# Behaves like _vetcoders_skill_init: spawns a vc_frame session with the
# selected agent preloaded with the /vc-operator skill prompt. NOT a
# background Iter-3 dispatchable mode.
_vetcoders_skill_operator() {
  local tool="$1"
  shift
  local runtime operator_prompt command_text permissions

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
  permissions="${_vetcoders_contract_permissions:-}"
  [[ -n "$permissions" ]] || { [[ "$tool" == "junie" ]] && permissions="auto" || permissions="bypass"; }
  command_text="$(_vetcoders_operator_command_text "$tool" "$operator_prompt" "${_vetcoders_contract_policy_runtime:-local-native}" "$permissions" "${_vetcoders_contract_token_budget:-safe}" "${_vetcoders_contract_operator:-none}" "${_vetcoders_contract_continuity:-fresh}" "${_vetcoders_contract_parent_session:-}" "${_vetcoders_contract_continuity_parent:-}")" || return 1

  _vetcoders_prepare_operator_runtime "$runtime" || return 1
  _vetcoders_spawn_into_operator_session "$(_vetcoders_operator_face_tab "$tool")" "$command_text"
}

# vc-partner launcher — interactive partner session, same family as init.
# --prompt/--file append extra seed context; they never select a headless worker.
_vetcoders_skill_partner() {
  local tool="$1"
  shift
  local runtime partner_prompt command_text permissions

  _vetcoders_parse_contract "$@" || return 1
  [[ -z "$_vetcoders_contract_count" ]] || {
    echo "--count is not supported by vibecrafted partner." >&2
    return 1
  }
  [[ -z "$_vetcoders_contract_depth" ]] || {
    echo "--depth is not supported by vibecrafted partner." >&2
    return 1
  }
  [[ -z "$_vetcoders_contract_session" ]] || {
    echo "--session is not supported by vibecrafted partner." >&2
    return 1
  }

  runtime="$(_vetcoders_partner_runtime "${_vetcoders_contract_runtime:-terminal}")" || return 1
  partner_prompt="$(_vetcoders_compose_partner_prompt "$_vetcoders_contract_prompt" "$_vetcoders_contract_file")" || return 1
  permissions="${_vetcoders_contract_permissions:-}"
  [[ -n "$permissions" ]] || { [[ "$tool" == "junie" ]] && permissions="auto" || permissions="bypass"; }
  command_text="$(_vetcoders_partner_command_text "$tool" "$partner_prompt" "${_vetcoders_contract_policy_runtime:-local-native}" "$permissions" "${_vetcoders_contract_token_budget:-safe}" "${_vetcoders_contract_operator:-none}" "${_vetcoders_contract_continuity:-fresh}" "${_vetcoders_contract_parent_session:-}" "${_vetcoders_contract_continuity_parent:-}")" || return 1

  if [[ "$runtime" == "plain" ]] || ! _vetcoders_vc_frame_bin >/dev/null ; then
    _vetcoders_init_in_current_terminal "$tool" "$command_text" "$runtime" "partner"
    return
  fi

  _vetcoders_prepare_operator_runtime "$runtime" || return 1
  _vetcoders_spawn_into_operator_session "$(_vetcoders_operator_face_tab "$tool")" "$command_text"
}
