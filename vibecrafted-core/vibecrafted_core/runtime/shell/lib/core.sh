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
  local current_dir
  current_dir="$(cd "$(dirname "$script_path")" && pwd)"
  printf '%s\n' "$current_dir"
}

_vetcoders_runtime_repo_root() {
  local cursor
  cursor="$(_vetcoders_script_dir)"

  while [[ "$cursor" != "/" && -n "$cursor" ]]; do
    if [[ -f "$cursor/VERSION" && -f "$cursor/scripts/vibecrafted" ]]; then
      printf '%s\n' "$cursor"
      return 0
    fi
    cursor="$(cd "$cursor/.." && pwd)"
  done

  return 1
}

_vetcoders_runtime_helper_candidates() {
  local module_runtime_root
  module_runtime_root="$(_vetcoders_script_dir)"
  module_runtime_root="${module_runtime_root%/shell/lib}"
  if [[ -n "${VIBECRAFTED_ROOT:-}" ]]; then
    printf '%s/runtime/helpers/vetcoders-runtime-core.sh\n' "${VIBECRAFTED_ROOT}"
    printf '%s/vibecrafted-core/vibecrafted_core/runtime/helpers/vetcoders-runtime-core.sh\n' "${VIBECRAFTED_ROOT}"
    printf '%s/helpers/vetcoders-runtime-core.sh\n' "${VIBECRAFTED_ROOT}"
  fi
  printf '%s/helpers/vetcoders-runtime-core.sh\n' "$module_runtime_root"
  local repo_root
  repo_root="$(_vetcoders_runtime_repo_root 2>/dev/null || true)"
  if [[ -n "$repo_root" ]]; then
    printf '%s/runtime/helpers/vetcoders-runtime-core.sh\n' "$repo_root"
    printf '%s/vibecrafted-core/vibecrafted_core/runtime/helpers/vetcoders-runtime-core.sh\n' "$repo_root"
  fi
  printf '%s/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/helpers/vetcoders-runtime-core.sh\n' "${VIBECRAFTED_TOOLS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools}"
}

_vetcoders_source_runtime_helpers() {
  local helper candidates=()
  # Drain the enumerator fully before sourcing: returning out of a
  # `while read < <(...)` loop closes the pipe under the producer and every
  # later candidate printf dies with "write error: Broken pipe" on stderr.
  # (No mapfile: /bin/bash on macOS is 3.2.)
  while IFS= read -r helper; do
    candidates+=("$helper")
  done < <(_vetcoders_runtime_helper_candidates)
  for helper in ${candidates[@]+"${candidates[@]}"}; do
    [[ -n "$helper" && -r "$helper" ]] || continue
    # shellcheck disable=SC1090
    source "$helper"
    return 0
  done

  printf '%s\n' "Missing Vetcoders runtime helpers in:" >&2
  _vetcoders_runtime_helper_candidates >&2
  return 1
}

_vetcoders_runtime_source_status=0
_vetcoders_source_runtime_helpers || {
  _vetcoders_runtime_source_status=$?
  unset -f _vetcoders_script_dir \
    _vetcoders_runtime_repo_root \
    _vetcoders_runtime_helper_candidates \
    _vetcoders_source_runtime_helpers
  if (return 0 2>/dev/null); then
    return "${_vetcoders_runtime_source_status}"
  fi
  exit "${_vetcoders_runtime_source_status}"
}
unset -f _vetcoders_script_dir \
  _vetcoders_runtime_repo_root \
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
