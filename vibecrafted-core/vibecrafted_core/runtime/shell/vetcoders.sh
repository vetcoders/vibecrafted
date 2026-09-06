# shellcheck shell=bash
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. shell helpers (bash/zsh compatible)
# Compatibility facade. Public callers source this file; implementation lives in lib/*.sh.
# Keep the load order explicit and acyclic: modules do not source each other.

_vetcoders_shell_facade_dir() {
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

_vetcoders_resolve_shell_lib_dir() {
  local facade_dir
  facade_dir="$(_vetcoders_shell_facade_dir)" || return $?
  # Installed and explicitly sourced checkout facades both own their adjacent
  # modules. An incomplete tree is never permission to load another generation.
  if [[ -L "$facade_dir/lib" || ! -d "$facade_dir/lib" ]]; then
    printf 'Missing or symlinked Vibecrafted shell module directory: %s/lib\n' "$facade_dir" >&2
    return 1
  fi
  printf '%s/lib\n' "$facade_dir"
}

_vetcoders_source_shell_module() {
  local module_name="$1"
  # NOTE: do NOT name this `module_path` — that is a reserved zsh special
  # parameter (the zmodload .so search path). Shadowing it with a file path
  # corrupts the autoload of `zsh/rlimits` (which provides `ulimit` in zsh),
  # spamming dlopen errors on every shell start.
  local module_file="${_vetcoders_shell_lib_dir}/${module_name}.sh"
  [[ -f "$module_file" && -r "$module_file" && ! -L "$module_file" ]] || {
    printf 'Missing Vibecrafted shell module: %s\n' "$module_file" >&2
    return 1
  }
  # shellcheck disable=SC1090
  source "$module_file" || return $?
}

_vetcoders_source_workflow_module() {
  # Workflow-owned modules live in runtime/<workflow>/shell/, not in the
  # shared lib/. The lib dir always ends in /shell/lib, so its grandparent
  # is the runtime root that hosts the per-workflow dirs.
  local workflow_name="$1"
  local module_name="$2"
  # See note in _vetcoders_source_shell_module: `module_path` is a reserved zsh
  # special parameter; never shadow it with a plain file path.
  local module_file="${_vetcoders_shell_lib_dir%/shell/lib}/${workflow_name}/shell/${module_name}.sh"
  if [[ -L "${_vetcoders_shell_lib_dir%/shell/lib}/${workflow_name}" || -L "${module_file%/*}" ]]; then
    printf 'Symlinked Vibecrafted workflow module directory: %s\n' "${module_file%/*}" >&2
    return 1
  fi
  [[ -f "$module_file" && -r "$module_file" && ! -L "$module_file" ]] || {
    printf 'Missing Vibecrafted workflow shell module: %s\n' "$module_file" >&2
    return 1
  }
  # shellcheck disable=SC1090
  source "$module_file" || return $?
}

_vetcoders_shell_lib_dir="$(_vetcoders_resolve_shell_lib_dir)" || return $?

# Load order: core -> runtime substrates -> workflow helpers -> public dispatch.
_vetcoders_source_shell_module core || return $?
_vetcoders_source_shell_module ulimits || return $?
_vetcoders_source_shell_module vc_frame || return $?
_vetcoders_source_shell_module frontier || return $?
_vetcoders_source_shell_module atuin || return $?
_vetcoders_source_shell_module dashboard || return $?
_vetcoders_source_shell_module prompts || return $?
_vetcoders_source_shell_module quote || return $?
_vetcoders_source_shell_module polarize || return $?
_vetcoders_source_workflow_module vc-research research_prompts || return $?
_vetcoders_source_shell_module operator || return $?
_vetcoders_source_shell_module dispatch_core || return $?
_vetcoders_source_shell_module observe || return $?
_vetcoders_source_shell_module dispatch_wrappers || return $?
_vetcoders_source_workflow_module vc-research research || return $?
_vetcoders_source_shell_module operator_entrypoints || return $?
_vetcoders_source_shell_module skill_shortcuts || return $?
_vetcoders_source_shell_module marbles || return $?
_vetcoders_source_shell_module dispatch || return $?

unset -f _vetcoders_shell_facade_dir _vetcoders_resolve_shell_lib_dir _vetcoders_source_shell_module _vetcoders_source_workflow_module
unset _vetcoders_shell_lib_dir
