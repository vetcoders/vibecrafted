# shellcheck shell=bash
# Loader for the shared launcher rlimit helper. The implementation lives in
# runtime/scripts/lib/ulimits.sh so shell facades and worker launchers use one
# policy.

_vetcoders_source_launcher_ulimits() {
  local candidate
  # shellcheck disable=SC2154  # _vetcoders_shell_lib_dir is set by the vetcoders.sh facade loader
  for candidate in \
    "${_vetcoders_shell_lib_dir%/shell/lib}/scripts/lib/ulimits.sh" \
    "${VIBECRAFTED_ROOT:-}/vibecrafted-core/vibecrafted_core/runtime/scripts/lib/ulimits.sh" \
    "${VIBECRAFTED_TOOLS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools}/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/scripts/lib/ulimits.sh" \
    "${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/runtime/scripts/lib/ulimits.sh"; do
    [[ -n "$candidate" && -r "$candidate" ]] || continue
    source "$candidate"
    vc_raise_launcher_limits
    return 0
  done
  printf '[warn] launcher ulimit helper not found; continuing without rlimit raise\n' >&2
  return 0
}

_vetcoders_source_launcher_ulimits
unset -f _vetcoders_source_launcher_ulimits
