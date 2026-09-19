# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

# Prompt/history presets resolve per asset: the installer-owned product config
# first, then the immutable defaults shipped in the selected generation or
# source checkout. Product configuration lives only in ~/.config/vibecrafted.
_vetcoders_frontier_candidates() {
  local repo_root crafted_sidecar candidate seen=""
  repo_root="$(_vetcoders_repo_root)"
  crafted_sidecar="${VIBECRAFTED_TOOLS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools}/vibecrafted-current/config"

  for candidate in \
    "$HOME/.config/vibecrafted" \
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
  starship_config="$(_vetcoders_frontier_file "starship.toml" 2>/dev/null || true)"
  atuin_config="$(_vetcoders_frontier_file "atuin/config.toml" 2>/dev/null || true)"

  # Frontier tools (starship, atuin) are suggested for the runtime, not
  # required. An explicit STARSHIP_CONFIG / ATUIN_CONFIG in the environment
  # wins; private files in the user's own config directory are never consulted.
  if command -v starship >/dev/null 2>&1 \
    && [[ -n "$starship_config" && -z "${STARSHIP_CONFIG:-}" ]]; then
    export STARSHIP_CONFIG="$starship_config"
  fi

  if command -v atuin >/dev/null 2>&1 \
    && [[ -n "$atuin_config" && -z "${ATUIN_CONFIG:-}" ]]; then
    export ATUIN_CONFIG="$atuin_config"
  fi

  # vc-frame never inherits the stock vc-frame config namespace.
  _vetcoders_pin_vc_frame_config_dir
}

_vetcoders_load_frontier_sidecars

_vetcoders_normalize_ambient_context

_VETCODERS_ATUIN_BIN="$(_vetcoders_atuin_bin 2>/dev/null || true)"
