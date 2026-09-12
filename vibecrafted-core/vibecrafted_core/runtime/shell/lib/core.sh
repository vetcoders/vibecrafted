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
    if [[ -e "$owner_root/.git" && ! -f "$owner_root/runtime-manifest.json" ]]; then
      # Source checkout ownership is not an immutable Runtime Pack generation.
      unset VIBECRAFTED_RUNTIME_ROOT
    else
      export VIBECRAFTED_RUNTIME_ROOT="$owner_root"
    fi
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
# Capture the physical vibecrafted-core import root now. `_vetcoders_script_dir`
# and `_vetcoders_shell_lib_dir` are both unset after facade load; later
# resolvers must not rediscover the owner through empty zsh BASH_SOURCE.
_vetcoders_loaded_core_dir="$(
  _vetcoders_lib_dir="$(_vetcoders_script_dir)" || exit 1
  cd -P "$_vetcoders_lib_dir/../../../.." && pwd -P
)" || _vetcoders_loaded_core_dir=""
unset _vetcoders_lib_dir
unset -f _vetcoders_script_dir \
  _vetcoders_runtime_owner_root \
  _vetcoders_runtime_helper_candidates \
  _vetcoders_source_runtime_helpers
unset _vetcoders_runtime_source_status
_vetcoders_default_runtime() {
  printf '%s\n' "${VETCODERS_SPAWN_RUNTIME:-headless}"
}

# Owned runtime roots — the anchor for private-carrier sanitation.
#
# A generation's own bin (aicx, loct, prview, screenscribe, vc-*, python3) is
# the PRIVATE carrier.  Internal execution reaches it through explicit owner
# paths only: VIBECRAFTED_RUNTIME_BIN / VIBECRAFTED_PYTHON exported by
# _vetcoders_product_entry_prepare, and _vetcoders_vc_frame_bin for the engine.
# It must never participate in ambient PATH lookup, or a stale generation
# answers for a public foundation or a provider CLI.
#
# Anchoring on real owned roots (not a floating */vibecrafted/releases/*/bin
# glob) is what makes a custom VIBECRAFTED_RUNTIME_HOME sanitize correctly
# while an unrelated lookalike user directory is preserved.
#
# Keep this grammar in lockstep with:
#   runtime/scripts/lib/util.sh:spawn_prepend_agent_tool_paths
#   runtime_paths.py:agent_tool_search_path
#   scripts/vetcoders_install.py:_runtime_launcher_body (public wrapper)
_vetcoders_owned_runtime_homes() {
  local xdg_data_home="${XDG_DATA_HOME:-${HOME:+$HOME/.local/share}}"
  local candidate
  for candidate in \
    "${VIBECRAFTED_RUNTIME_HOME:-}" \
    "${xdg_data_home:+$xdg_data_home/vibecrafted}"
  do
    [[ -n "$candidate" ]] && printf '%s\n' "${candidate%/}"
  done
  return 0
}

# True when $1 is a Vibecrafted-owned generation bin: the selected root's bin,
# or exactly <owned runtime home>/releases/<generation>/bin.  A user directory
# that merely looks similar (~/dev/vibecrafted/releases/1.0/bin) is not owned.
_vetcoders_is_owned_generation_bin() {
  local entry="${1:-}"
  local runtime_home generation leaf selected
  [[ -n "$entry" ]] || return 1
  entry="${entry%/}"
  [[ "$entry" == */bin ]] || return 1
  generation="${entry%/bin}"

  selected="${VIBECRAFTED_RUNTIME_ROOT:-}"
  if [[ -n "$selected" && "$generation" == "${selected%/}" ]]; then
    return 0
  fi

  while IFS= read -r runtime_home; do
    [[ -n "$runtime_home" ]] || continue
    [[ "$generation" == "$runtime_home/releases/"* ]] || continue
    leaf="${generation#"$runtime_home/releases/"}"
    if [[ -n "$leaf" && "$leaf" != */* ]]; then
      return 0
    fi
  done < <(_vetcoders_owned_runtime_homes)
  return 1
}

# Host CLIs (node/codex/claude) and the public launcher shims live outside any
# generation.  A detached or App-launched parent can arrive with a minimal
# launchd PATH, so these are APPENDED as a discovery suffix — never prepended.
# Prepending is what let /opt/homebrew/bin/python3 outrank the generation
# interpreter during product preparation (R8 missing-python trace), and it is
# also how the Founder's own tool order got overridden.
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
    /usr/local/bin \
    /usr/bin \
    /bin \
    /usr/sbin \
    /sbin
  do
    [[ -n "$dir" && -d "$dir" ]] && printf '%s\n' "$dir"
  done
  return 0
}

# Public/interactive PATH preparation for the product entry.  Two invariants:
#
#   1. Every inherited entry the Founder owns keeps its identity and relative
#      order, including unrelated custom directories.  Only empty entries
#      (implicit CWD) and later duplicates are dropped.
#   2. No Vibecrafted-owned generation bin participates.  A missing public
#      foundation therefore stays missing and the caller prints canonical
#      install guidance, instead of silently resolving a bundled private copy.
#
# The host discovery suffix is appended, so a minimal launchd PATH still finds
# provider CLIs while public/user ordering remains authoritative.
_vetcoders_path_with_bundled_bin_priority() {
  local current_path="${1:-}"
  local remainder="$current_path"
  local entry dir result="" consumed=0

  while (( ! consumed )); do
    if [[ "$remainder" == *:* ]]; then
      entry="${remainder%%:*}"
      remainder="${remainder#*:}"
    else
      entry="$remainder"
      consumed=1
    fi
    [[ -n "$entry" ]] || continue
    _vetcoders_is_owned_generation_bin "$entry" && continue
    case ":$result:" in
      *":$entry:"*) continue ;;
    esac
    result="${result:+$result:}$entry"
  done

  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    _vetcoders_is_owned_generation_bin "$dir" && continue
    case ":$result:" in
      *":$dir:"*) continue ;;
    esac
    result="${result:+$result:}$dir"
  done < <(_vetcoders_host_agent_bin_dirs)

  printf '%s\n' "$result"
}

# Physical vibecrafted-core import root for this loaded facade. VIBECRAFTED_CORE_DIR
# wins at call time (tests pin a generation); otherwise the path captured at
# source time. Never rediscover through BASH_SOURCE — it is empty under zsh.
_vetcoders_owned_core_dir() {
  if [[ -n "${VIBECRAFTED_CORE_DIR:-}" ]]; then
    (cd -P "${VIBECRAFTED_CORE_DIR}" && pwd -P)
    return $?
  fi
  [[ -n "${_vetcoders_loaded_core_dir:-}" ]] || return 1
  printf '%s\n' "$_vetcoders_loaded_core_dir"
}

# Installed generations live at <runtime-home>/releases/<generation> — the same
# physical shape _vetcoders_product_core_cli already used to fail closed.
_vetcoders_is_installed_generation_product() {
  local product_root="${1:-}"
  [[ -n "$product_root" ]] || return 1
  [[ "$(basename "$(dirname "$product_root")")" == "releases" ]]
}

# One owned-interpreter selector for the facade.
#
# Installed generation: only <generation>/bin/python3, never execute inherited
# VIBECRAFTED_PYTHON or a public PATH python. Missing owned interpreter is a
# real refusal.
#
# Source checkout: documented development fallback (VIBECRAFTED_PYTHON, .venv,
# embedded, project-python, then public PATH >=3.11). Public PATH of foreign
# products is only a last-resort lookup, never overwritten.
_vetcoders_owned_python_bin() {
  local core_dir product_root embedded checkout_python project_python candidate
  core_dir="$(_vetcoders_owned_core_dir)" || return 1
  product_root="$(cd -P "$core_dir/.." && pwd -P)" || return 1
  embedded="$product_root/bin/python3"
  if _vetcoders_is_installed_generation_product "$product_root"; then
    if [[ -x "$embedded" ]]; then
      printf '%s\n' "$embedded"
      return 0
    fi
    printf 'installed runtime is missing its own interpreter: %s\n' "$embedded" >&2
    printf 'refusing to substitute a host python3; explicit upgrade/repair required\n' >&2
    return 1
  fi
  checkout_python="$product_root/.venv/bin/python3"
  project_python="$product_root/scripts/project-python"
  for candidate in \
    "${VIBECRAFTED_PYTHON:-}" \
    "$checkout_python" \
    "$embedded" \
    "$project_python"
  do
    [[ -n "$candidate" && -x "$candidate" ]] || continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
      >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  for candidate in \
    "${VIBECRAFTED_RUNTIME_BIN:+$VIBECRAFTED_RUNTIME_BIN/python3}" \
    python3.13 python3.12 python3.11 python3
  do
    [[ -n "$candidate" ]] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
      >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  printf 'Vibecrafted core requires Python >=3.11; no eligible interpreter found.\n' >&2
  return 1
}

# Internal runtime Python for this shell facade — the same owned selector as
# `_vetcoders_core_python_spec`, without proving `import vibecrafted_core`.
# Helpers that only need stdlib (shlex, json, re) must not inherit the import
# proof, but they must not run a foreign interpreter before ownership either.
_vetcoders_internal_python() {
  _vetcoders_owned_python_bin
}

_vetcoders_aicx_bin() {
  local xdg_data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  local runtime_bin="${VIBECRAFTED_RUNTIME_ROOT:+$VIBECRAFTED_RUNTIME_ROOT/bin}"
  runtime_bin="${runtime_bin:-${VIBECRAFTED_RUNTIME_BIN:-${VIBECRAFTED_RUNTIME_HOME:-$xdg_data_home/vibecrafted}/bin}}"
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
