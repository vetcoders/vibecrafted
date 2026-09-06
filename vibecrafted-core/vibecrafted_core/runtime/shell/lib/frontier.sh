# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_frontier_candidates() {
  local repo_root crafted_sidecar candidate seen=""
  repo_root="$(_vetcoders_repo_root)"
  crafted_sidecar="${VIBECRAFTED_TOOLS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools}/vibecrafted-current/config"

  for candidate in \
    "${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/frontier" \
    "$crafted_sidecar" \
    "${VIBECRAFTED_ROOT:+$VIBECRAFTED_ROOT/config}" \
    "${VIBECRAFTED_ROOT:+$VIBECRAFTED_ROOT/vibecrafted-core/vibecrafted_core/config}" \
    "$repo_root/config" \
    "$repo_root/vibecrafted-core/vibecrafted_core/config"
  do
    [[ -n "$candidate" && -d "$candidate" ]] || continue
    case ":$seen:" in
      *":$candidate:"*) continue ;;
    esac
    seen="${seen:+$seen:}$candidate"
    printf '%s\n' "$candidate"
  done
}

_vetcoders_frontier_root() {
  local candidate
  while IFS= read -r candidate; do
    if [[ -f "$candidate/starship.toml" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done < <(_vetcoders_frontier_candidates)

  echo "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. frontier config not found. Run vc-frontier-install from the repo checkout." >&2
  return 1
}

# Optional prompt/history presets retain their independent frontier resolution.
# Frame assets all come from the selected product view (or explicit source entry).
_vetcoders_frontier_file() {
  local relative_path="$1"
  local candidate
  case "$relative_path" in
    vc-frame/*)
      candidate="$(_vetcoders_vc_frame_config_dir)" || return 1
      candidate="$candidate/${relative_path#vc-frame/}"
      [[ -f "$candidate" ]] || return 1
      printf '%s\n' "$candidate"
      return 0
      ;;
  esac
  while IFS= read -r candidate; do
    if [[ -f "$candidate/$relative_path" ]]; then
      printf '%s/%s\n' "$candidate" "$relative_path"
      return 0
    fi
  done < <(_vetcoders_frontier_candidates)
  return 1
}

_vetcoders_frontier_source_root() {
  local repo_root crafted_root candidate seen=""
  repo_root="$(_vetcoders_repo_root)"
  crafted_root="${VIBECRAFTED_TOOLS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools}/vibecrafted-current"

  for candidate in \
    "${VIBECRAFTED_ROOT:-}" \
    "$repo_root" \
    "$crafted_root"
  do
    [[ -n "$candidate" ]] || continue
    case ":$seen:" in
      *":$candidate:"*) continue ;;
    esac
    seen="${seen:+$seen:}$candidate"
    if [[ -f "$candidate/config/starship.toml" ]] \
      || [[ -f "$candidate/vibecrafted-core/vibecrafted_core/config/starship.toml" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

_vetcoders_vc_frame_config_dir() {
  local owner_root
  if _vetcoders_vc_frame_developer_mode; then
    owner_root="$(_vetcoders_vc_frame_owner_root)" || return 1
    printf '%s/vibecrafted-core/vibecrafted_core/config/vc-frame\n' "$owner_root"
  else
    printf '%s/.config/vibecrafted/vc-frame\n' "$HOME"
  fi
}

_vetcoders_pin_vc_frame_config_dir() {
  local config_dir
  config_dir="$(_vetcoders_vc_frame_config_dir)" || return 1
  # Pin even when absent: startup validates it, never searches or repairs it.
  unset ZELLIJ_CONFIG_DIR ZELLIJ_CONFIG_FILE
  export VC_FRAME_CONFIG_DIR="$config_dir"
  export VC_FRAME_CONFIG_FILE="$config_dir/config.kdl"
}

_vetcoders_load_frontier_sidecars() {
  local starship_config atuin_config
  local xdg_config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
  starship_config="$(_vetcoders_frontier_file "starship.toml" 2>/dev/null || true)"
  atuin_config="$(_vetcoders_frontier_file "atuin/config.toml" 2>/dev/null || true)"

  # Frontier tools (starship, atuin) are suggested for the runtime,
  # not required. Never override a user's existing config — only set env vars
  # when the user has no config of their own. Users opt in explicitly.
  if command -v starship >/dev/null 2>&1 \
    && [[ -n "$starship_config" && -z "${STARSHIP_CONFIG:-}" ]] \
    && [[ ! -e "$xdg_config_home/starship.toml" ]]; then
    export STARSHIP_CONFIG="$starship_config"
  fi

  if command -v atuin >/dev/null 2>&1 \
    && [[ -n "$atuin_config" && -z "${ATUIN_CONFIG:-}" ]] \
    && [[ ! -e "$xdg_config_home/atuin/config.toml" ]]; then
    export ATUIN_CONFIG="$atuin_config"
  fi

  # vc-frame must never inherit the user's stock ~/.config/vc-frame namespace.
  _vetcoders_pin_vc_frame_config_dir
}

_vetcoders_load_frontier_sidecars

_vetcoders_normalize_ambient_context

_VETCODERS_ATUIN_BIN="$(_vetcoders_atuin_bin 2>/dev/null || true)"
