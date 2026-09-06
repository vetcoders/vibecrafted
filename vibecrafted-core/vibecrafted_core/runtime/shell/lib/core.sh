# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_script_dir() {
  local script_path=""
  if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    script_path="${BASH_SOURCE[0]}"
  elif [[ -n "${ZSH_VERSION:-}" ]]; then
    script_path="$(eval 'printf "%s\n" "${(%):-%x}"')"
  else
    script_path="$0"
  fi

  local script_dir link_target
  while [[ -L "$script_path" ]]; do
    script_dir="$(cd -P "$(dirname "$script_path")" && pwd -P)" || return $?
    link_target="$(readlink "$script_path")" || return $?
    if [[ "$link_target" == /* ]]; then
      script_path="$link_target"
    else
      script_path="$script_dir/$link_target"
    fi
  done
  cd -P "$(dirname "$script_path")" && pwd -P
}

_vetcoders_runtime_owner_root() {
  local module_dir owner_root
  module_dir="$(_vetcoders_script_dir)" || return $?
  case "$module_dir" in
    */vibecrafted-core/vibecrafted_core/runtime/shell/lib)
      owner_root="${module_dir%/vibecrafted-core/vibecrafted_core/runtime/shell/lib}"
      [[ -n "$owner_root" ]] || owner_root="/"
      printf '%s\n' "$owner_root"
      ;;
    *)
      printf 'Vetcoders shell core is outside a physical runtime owner: %s\n' "$module_dir" >&2
      return 1
      ;;
  esac
}

_vetcoders_runtime_helper_candidates() {
  local owner_root helper_dir helper
  owner_root="$(_vetcoders_runtime_owner_root)" || return $?
  helper_dir="$owner_root/vibecrafted-core/vibecrafted_core/runtime/helpers"
  helper="$helper_dir/vetcoders-runtime-core.sh"

  if [[ -L "$helper_dir" || ! -d "$helper_dir" || -L "$helper" || ! -f "$helper" || ! -r "$helper" ]]; then
    printf 'Missing or unsafe adjacent Vetcoders runtime helper: %s\n' "$helper" >&2
    return 1
  fi

  printf '%s\n' "$helper"
}

_vetcoders_source_runtime_helpers() {
  local helper owner_root source_status
  owner_root="$(_vetcoders_runtime_owner_root)" || return $?
  helper="$(_vetcoders_runtime_helper_candidates)" || return $?

  # shellcheck disable=SC1090
  if source "$helper"; then
    # The sourced helper resolves later runtime scripts through VIBECRAFTED_ROOT.
    # Bind it to this helper's physical owner only after the source succeeds.
    export VIBECRAFTED_ROOT="$owner_root"
    export VIBECRAFTED_RUNTIME_ROOT="$owner_root"
    return 0
  else
    source_status=$?
  fi

  printf 'Failed to source adjacent Vetcoders runtime helper: %s\n' "$helper" >&2
  return "$source_status"
}

_vetcoders_runtime_source_status=0
_vetcoders_source_runtime_helpers || {
  _vetcoders_runtime_source_status=$?
  unset -f _vetcoders_script_dir \
    _vetcoders_runtime_owner_root \
    _vetcoders_runtime_helper_candidates \
    _vetcoders_source_runtime_helpers
  if (return 0 2>/dev/null); then
    return "${_vetcoders_runtime_source_status}"
  fi
  exit "${_vetcoders_runtime_source_status}"
}
unset -f _vetcoders_script_dir \
  _vetcoders_runtime_owner_root \
  _vetcoders_runtime_helper_candidates \
  _vetcoders_source_runtime_helpers
unset _vetcoders_runtime_source_status
_vetcoders_default_runtime() {
  printf '%s\n' "${VETCODERS_SPAWN_RUNTIME:-headless}"
}

_vetcoders_bundled_bin_dirs() {
  local xdg_data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  local runtime_bin="${VIBECRAFTED_RUNTIME_BIN:-${VIBECRAFTED_RUNTIME_HOME:-$xdg_data_home/vibecrafted}/bin}"
  [[ -d "$runtime_bin" ]] && printf '%s\n' "$runtime_bin"
}

# Host CLIs (node/codex/claude) live outside the hermetic system PATH.
# Keep this list in lockstep with runtime_paths.agent_tool_search_path.
_vetcoders_host_agent_bin_dirs() {
  local home="${HOME:-}"
  local dir
  for dir in \
    "${home:+$home/.local/bin}" \
    "${home:+$home/.cargo/bin}" \
    "${home:+$home/tools/scripts}" \
    /opt/homebrew/bin \
    /opt/homebrew/sbin \
    /usr/local/bin
  do
    [[ -n "$dir" && -d "$dir" ]] && printf '%s\n' "$dir"
  done
}

_vetcoders_path_with_bundled_bin_priority() {
  local current_path="${1:-}"
  local bundled_path=""
  local dir
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    case ":$current_path:" in
      *":$dir:"*) ;;
      *) bundled_path="${bundled_path:+$bundled_path:}$dir" ;;
    esac
  done < <({ _vetcoders_bundled_bin_dirs; _vetcoders_host_agent_bin_dirs; })
  printf '%s\n' "${bundled_path:+$bundled_path${current_path:+:}}$current_path"
}

_vetcoders_aicx_bin() {
  local xdg_data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  local runtime_bin="${VIBECRAFTED_RUNTIME_BIN:-${VIBECRAFTED_RUNTIME_HOME:-$xdg_data_home/vibecrafted}/bin}"
  local candidate=""

  # Foundation discovery is deterministic and independent of interactive
  # shell startup. Explicit/operator and Vibecrafted-owned paths win; the
  # Cargo location is retained for source installs during the transition.
  for candidate in \
    "${VIBECRAFTED_AICX_BIN:-}" \
    "$runtime_bin/aicx" \
    "$HOME/.local/bin/aicx" \
    "$HOME/.cargo/bin/aicx"
  do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  command -v aicx 2>/dev/null
}
