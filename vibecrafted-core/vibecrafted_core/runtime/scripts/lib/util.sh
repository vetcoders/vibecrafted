#!/usr/bin/env bash

spawn_die() {
  printf 'Error: %s\n' "$*" >&2
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

spawn_prepend_agent_tool_paths() {
  local home="${HOME:-}"
  local xdg_data_home="${XDG_DATA_HOME:-${home:+$home/.local/share}}"
  local runtime_home="${VIBECRAFTED_RUNTIME_HOME:-${xdg_data_home:+$xdg_data_home/vibecrafted}}"
  local runtime_bin="${VIBECRAFTED_RUNTIME_BIN:-${runtime_home:+$runtime_home/bin}}"
  local entry found joined
  local -a contract final

  # Mirror the workstation runtime contract for detached agent launchers whose parent
  # process may not have gone through zsh startup files. This is intentionally
  # an allowlist: inherited PATH entries do not participate in agent command
  # resolution.
  contract=(
    "${runtime_bin:-}"
    "${home:+$home/.local/bin}"
    "${home:+$home/.cargo/bin}"
    "${home:+$home/tools/scripts}"
    /opt/homebrew/bin
    /opt/homebrew/sbin
    /usr/local/bin
    /usr/bin
    /bin
    /usr/sbin
    /sbin
  )

  final=()
  for dir in "${contract[@]}"; do
    [[ -n "$dir" && -d "$dir" ]] || continue
    found=0
    if (( ${#final[@]} > 0 )); then
      for entry in "${final[@]}"; do
        [[ "$entry" == "$dir" ]] || continue
        found=1
        break
      done
    fi
    (( found )) || final+=("$dir")
  done

  joined=""
  for entry in "${final[@]}"; do
    if [[ -z "$joined" ]]; then
      joined="$entry"
    else
      joined="$joined:$entry"
    fi
  done
  export PATH="$joined"
}

spawn_require_positive_int() {
  local value="${1:-}"
  local flag_name="${2:-value}"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || spawn_die "${flag_name} must be a positive integer"
}

spawn_shell_quote() {
  local value="${1-}"
  # printf '%q' can emit byte sequences that break vc_frame's UTF-8 validation.
  python3 - "$value" <<'PY'
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
      python3 - "$state_file" <<'PY'
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
