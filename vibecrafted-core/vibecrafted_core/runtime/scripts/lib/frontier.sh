#!/usr/bin/env bash

spawn_preferred_shell() {
  if command -v zsh >/dev/null 2>&1;
 then
    command -v zsh
  elif [[ -n "${SHELL:-}" ]] && command -v "${SHELL##*/}" >/dev/null 2>&1;
 then
    printf '%s\n' "$SHELL"
  else
    command -v bash
  fi
}

spawn_frontier_root() {
  local candidate
  while IFS= read -r candidate;
 do
    if [[ -f "$candidate/starship.toml" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(spawn_frontier_candidates)

  return 1
}

# Installer-owned product config first, then the immutable defaults shipped in
# the selected generation or source checkout. Product configuration lives only
# in ~/.config/vibecrafted.
spawn_frontier_candidates() {
  local script_root candidate seen=""
  script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." 2>/dev/null && pwd || true)"
  if [[ ! -f "$script_root/VERSION" && -f "$(dirname "${BASH_SOURCE[0]}")/../../../VERSION" ]]; then
    script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" 2>/dev/null && pwd || true)"
  fi

  for candidate in \
    "$HOME/.config/vibecrafted" \
    "${VIBECRAFTED_TOOLS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools}/vibecrafted-current/config" \
    "${VIBECRAFTED_ROOT:+$VIBECRAFTED_ROOT/config}" \
    "${SPAWN_ROOT:+$SPAWN_ROOT/config}" \
    "${script_root:+$script_root/config}"
  do
    [[ -n "$candidate" && -d "$candidate" ]] || continue
    case ":$seen:" in
      *":$candidate:"*) continue ;;
    esac
    seen="${seen:+$seen:}$candidate"
    printf '%s\n' "$candidate"
  done

  return 0
}

# Resolve each frontier asset independently so repo-owned prompt/history presets
# can coexist with an external session companion repo.
spawn_frontier_file() {
  local relative_path="$1"
  local candidate
  while IFS= read -r candidate;
 do
    if [[ -f "$candidate/$relative_path" ]]; then
      printf '%s/%s\n' "$candidate" "$relative_path"
      return 0
    fi
  done < <(spawn_frontier_candidates)
  return 1
}

spawn_export_frontier_sidecars() {
  local starship_config atuin_config vc_frame_config vc_frame_config_dir
  starship_config="$(spawn_frontier_file "starship.toml" 2>/dev/null || true)"
  atuin_config="$(spawn_frontier_file "atuin/config.toml" 2>/dev/null || true)"
  vc_frame_config="$(spawn_frontier_file "vc-frame/config.kdl" 2>/dev/null || true)"

  # Re-pin the active frontier assets every time so spawned sessions do not
  # inherit stale shell config from an unrelated install or repo. An explicit
  # STARSHIP_CONFIG / ATUIN_CONFIG wins; private files in the user's own config
  # directory are never consulted.
  if command -v starship >/dev/null 2>&1 \
    && [[ -n "$starship_config" ]] \
    && [[ -z "${STARSHIP_CONFIG:-}" ]]; then
    export STARSHIP_CONFIG="$starship_config"
  fi

  if command -v atuin >/dev/null 2>&1 \
    && [[ -n "$atuin_config" ]] \
    && [[ -z "${ATUIN_CONFIG:-}" ]]; then
    export ATUIN_CONFIG="$atuin_config"
  fi

  if spawn_vc_frame_bin >/dev/null 2>&1 && [[ -n "$vc_frame_config" ]]; then
    vc_frame_config_dir="$(dirname "$vc_frame_config")"
    export VC_FRAME_CONFIG_DIR="$vc_frame_config_dir"
  fi
}
