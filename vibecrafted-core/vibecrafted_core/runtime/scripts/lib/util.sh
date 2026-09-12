#!/usr/bin/env bash

spawn_die() {
  printf 'Error: %s\n' "$*" >&2
  if declare -F spawn_settle_early_failure >/dev/null 2>&1; then
    spawn_settle_early_failure "$*" || true
  fi
  exit 1
}

spawn_require_file() {
  local path="${1:-}"
  [[ -n "$path" ]] || spawn_die "Missing required file path."
  [[ -f "$path" ]] || spawn_die "File not found: $path"
}

spawn_require_command() {
  local cmd="${1:-}"
  [[ -n "$cmd" ]] || spawn_die "Missing required command name."
  spawn_prepend_agent_tool_paths
  command -v "$cmd" >/dev/null 2>&1 || spawn_die "Required command not found: $cmd"
}

# Owned runtime roots — see runtime/shell/lib/core.sh for the full rationale.
# This launcher tree is sourced independently of the interactive shell facade,
# so the grammar is duplicated here on purpose and must stay in lockstep with:
#   runtime/shell/lib/core.sh:_vetcoders_is_owned_generation_bin
#   runtime_paths.py:agent_tool_search_path
#   scripts/vetcoders_install.py:_runtime_launcher_body (public wrapper)
spawn_owned_runtime_homes() {
  local home="${HOME:-}"
  local xdg_data_home="${XDG_DATA_HOME:-${home:+$home/.local/share}}"
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
# or exactly <owned runtime home>/releases/<generation>/bin.
spawn_is_owned_generation_bin() {
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
  done < <(spawn_owned_runtime_homes)
  return 1
}

# Host CLIs and public launcher shims, appended as a discovery suffix.
# Keep this list in lockstep with runtime_paths.agent_tool_search_path and
# runtime/shell/lib/core.sh:_vetcoders_host_agent_bin_dirs.
spawn_host_agent_bin_dirs() {
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

# PATH for a detached agent child (provider CLI + Founder foundations).
#
# A launchd/App parent may not have gone through zsh startup files, so the
# host discovery suffix is appended to guarantee the provider is reachable.
# The inherited PATH is NOT discarded: the Founder's own tools and unrelated
# custom entries keep their identity and relative order, because a public
# child must resolve public aicx/loct/prview/screenscribe from user paths.
#
# The one thing removed is a Vibecrafted-owned generation bin.  The private
# carrier is reached through VIBECRAFTED_RUNTIME_BIN / VIBECRAFTED_PYTHON and
# other explicit owner paths, so a missing host tool stays missing instead of
# silently resolving to a bundled — possibly stale — private copy.
spawn_prepend_agent_tool_paths() {
  local remainder="${PATH:-}"
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
    spawn_is_owned_generation_bin "$entry" && continue
    case ":$result:" in
      *":$entry:"*) continue ;;
    esac
    result="${result:+$result:}$entry"
  done

  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    spawn_is_owned_generation_bin "$dir" && continue
    case ":$result:" in
      *":$dir:"*) continue ;;
    esac
    result="${result:+$result:}$dir"
  done < <(spawn_host_agent_bin_dirs)

  export PATH="$result"
}

# RESOLVER TRUTH: This resolver is kept exclusively for runtime-side/split-brain
# ./runtime execution where the uv shim might not be directly in the execution chain.
# This is NOT a deck-level plaster.
#
# Resolve an interpreter that can import vibecrafted_core. The package needs
# tomllib (Python 3.11+); bare `python3` on macOS is often /usr/bin/python3 3.9.6
# which lacks tomllib, so vibecrafted_core dies with ModuleNotFoundError. Prefer
# the explicit owner env (VIBECRAFTED_PYTHON, then the selected generation bin),
# then the uv tool venv python (has the package + deps), then any 3.11+ python.
#
# INTERNAL EXECUTION OWNER: it sits beside spawn_prepend_agent_tool_paths on
# purpose. That sanitizer drops the owned generation bin from PATH, so a bare
# `python3` in this launcher tree now resolves to whatever the Founder's PATH
# offers — a 3.9.6 host interpreter, or in the worst case an unrelated shim.
# Public tools must keep resolving that way; internal runtime Python must not.
# Every internal call below therefore names its interpreter through this owner.
# It lives in util.sh (the no-deps layer, sourced first and also sourced
# standalone by the capability-probe tests) so no module has to guard on load
# order to reach it.
spawn_python_bin() {
  local candidate
  for candidate in \
    "${VIBECRAFTED_PYTHON:-}" \
    "${VIBECRAFTED_RUNTIME_BIN:+$VIBECRAFTED_RUNTIME_BIN/python3}" \
    "${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools/vibecrafted/bin/python3" \
    "${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools/vibecrafted-core/bin/python3" \
    python3.14 python3.13 python3.12 python3.11 python3; do
    [[ -n "$candidate" ]] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  # macOS 15+ keeps /usr/bin/python3 at 3.9.6 with no tomllib. Returning that
  # name lets every internal caller exec a host interpreter the product cannot
  # run. Fail closed; do not advertise it as the runtime python.
  printf 'Vibecrafted requires Python >=3.11 with stdlib tomllib; host python3 (macOS 3.9.6) is not a runtime interpreter.\n' >&2
  return 1
}

spawn_require_positive_int() {
  local value="${1:-}"
  local flag_name="${2:-value}"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || spawn_die "${flag_name} must be a positive integer"
}

spawn_shell_quote() {
  local value="${1-}"
  # printf '%q' can emit byte sequences that break vc_frame's UTF-8 validation.
  "$(spawn_python_bin)" - "$value" <<'PY'
import shlex
import sys

print(shlex.quote(sys.argv[1]), end="")
PY
}

spawn_org_repo() {
  local root="${1:-$(spawn_repo_root)}"
  local fallback_to_basename="${2:-1}"
  local org_repo=""
  org_repo="$(cd "$root" && git remote get-url origin 2>/dev/null | sed -E 's|.*[:/]([^/]+)/([^/.]+)(\.git)?$|\1/\2|' || true)"
  if [[ -n "$org_repo" ]]; then
    printf '%s\n' "$org_repo"
  elif [[ "$fallback_to_basename" == "1" ]]; then
    printf '%s\n' "$(basename "$root")"
  else
    printf '\n'
  fi
}

spawn_timestamp() {
  if [[ -n "${VIBECRAFTED_SPAWN_TS:-}" ]]; then
    printf '%s\n' "${VIBECRAFTED_SPAWN_TS}"
  else
    # Seconds resolution — without them parallel spawns in the same minute
    # collide on SPAWN_LAUNCHER path and concurrent `cat >>` calls interleave
    # blocks into a single broken script (observed 2026-04-22 with 3× gemini
    # spawns at 16:56 producing one launcher with duplicated if/else bodies).
    date +%Y%m%d_%H%M%S
  fi
}

spawn_framework_version() {
  local script_root=""
  local candidate=""
  local state_file=""
  local state_version=""

  script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." 2>/dev/null && pwd || true)"
  if [[ ! -f "$script_root/VERSION" && -f "$(dirname "${BASH_SOURCE[0]}")/../../../VERSION" ]]; then
    script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" 2>/dev/null && pwd || true)"
  fi

  for candidate in \
    "${VIBECRAFTED_ROOT:+$VIBECRAFTED_ROOT/VERSION}" \
    "${SPAWN_ROOT:+$SPAWN_ROOT/VERSION}" \
    "${script_root:+$script_root/VERSION}" \
    "${VIBECRAFTED_TOOLS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools}/vibecrafted-current/VERSION"
  do
    [[ -n "$candidate" ]] || continue
    if [[ -f "$candidate" ]]; then
      tr -d '\r\n' < "$candidate"
      return 0
    fi
  done

  for state_file in \
    "${script_root:+$script_root/skills/.vc-install.json}" \
    "${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/skills/.vc-install.json"
  do
    [[ -n "$state_file" ]] || continue
    [[ -f "$state_file" ]] || continue
    state_version="$(
      "$(spawn_python_bin)" - "$state_file" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)
print(payload.get("framework_version", ""))
PY
    )"
    if [[ -n "$state_version" ]]; then
      printf '%s\n' "$state_version"
      return 0
    fi
  done

  printf 'unknown\n'
}

spawn_validate_runtime() {
  local runtime="${1:-headless}"
  case "$runtime" in
    terminal|visible|headless|background|detached)
      return 0
      ;;
    *)
      spawn_die "Invalid runtime '$runtime'. Valid values: terminal, visible, headless, background, detached."
      ;;
  esac
}

# Normalize a model frontmatter value: empty string for placeholders
# (`pending`, `unknown`, `null`), pass-through otherwise. Used by every
# marbles dispatch site so a single point of truth gates which model values
# reach `claude_spawn.sh --model` / `codex_spawn.sh --model`. Adding a new
# placeholder token here propagates to every consumer automatically.
spawn_clean_model() {
  local raw="${1:-}"
  case "$raw" in
    pending|unknown|null) printf '' ;;
    *) printf '%s' "$raw" ;;
  esac
}

spawn_check_shell_syntax() {
  local path="${1:-}"
  local label="${2:-shell script}"
  local output=""

  spawn_require_file "$path"

  if output="$(bash -n "$path" 2>&1)"; then
    return 0
  fi

  printf 'Shell syntax error in %s: %s\n' "$label" "$path" >&2
  [[ -n "$output" ]] && printf '%s\n' "$output" >&2
  return 1
}

spawn_require_shell_syntax() {
  local path="${1:-}"
  local label="${2:-shell script}"

  spawn_check_shell_syntax "$path" "$label" || spawn_die "Shell syntax check failed: $path"
}
