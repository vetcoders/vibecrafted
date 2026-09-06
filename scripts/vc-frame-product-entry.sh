#!/usr/bin/env bash
# Generation-local product entry. The installer publishes this at bin/vc-frame
# and the native engine at libexec/vc-frame. Startup only consumes configuration;
# standalone engine/developer tooling is a separate, explicit entrypoint.
set -euo pipefail

resolve_real_bin() {
  local candidate="$root/libexec/vc-frame"
  if [[ -f "$candidate" && ! -L "$candidate" && -x "$candidate" ]] \
    && file -Lb "$candidate" 2>/dev/null | grep -Eqi 'Mach-O|ELF'; then
    printf '%s\n' "$candidate"
    return 0
  fi
  return 1
}

pin_darwin_socket_dir() {
  # Keep the existing short socket namespace and explicit session overrides.
  case "$(uname -s 2>/dev/null || true)" in
    Darwin)
      if [[ -z "${VC_FRAME_SOCKET_DIR:-}" && -z "${ZELLIJ_SOCKET_DIR:-}" ]]; then
        local socket_uid
        socket_uid="$(id -u)"
        export VC_FRAME_SOCKET_DIR="/tmp/vc-frame-$socket_uid"
        export ZELLIJ_SOCKET_DIR="$VC_FRAME_SOCKET_DIR"
      fi
      ;;
  esac
}

pin_product_config() {
  local view="$HOME/.config/vibecrafted/vc-frame"
  if [[ ! -L "$view" && ! -L "$view/config.kdl" && -f "$view/config.kdl" && -r "$view/config.kdl" \
    && ! -L "$view/layouts" && -d "$view/layouts" ]]; then
    unset ZELLIJ_CONFIG_DIR ZELLIJ_CONFIG_FILE
    # Shipped key bindings also address scripts through XDG_CONFIG_HOME.
    export XDG_CONFIG_HOME="$HOME/.config"
    export VC_FRAME_CONFIG_DIR="$view"
    export VC_FRAME_CONFIG_FILE="$view/config.kdl"
    return 0
  fi
  printf 'vc-frame: installed product config/layouts missing or symlinked: %s\n' "$view" >&2
  return 1
}

# Follow the invoked entry to its physical generation before consulting assets.
# An inherited root, cargo binary, PATH entry or moving current selector cannot
# replace this already-selected payload.
entry="${BASH_SOURCE[0]}"
while [[ -L "$entry" ]]; do
  entry_dir="$(cd -P "$(dirname "$entry")" && pwd -P)"
  target="$(readlink "$entry")"
  if [[ "$target" == /* ]]; then
    entry="$target"
  else
    entry="$entry_dir/$target"
  fi
done
root="$(cd -P "$(dirname "$entry")/.." && pwd -P)"
real="$(resolve_real_bin)" || {
  printf 'vc-frame: native engine missing from selected generation: %s/libexec/vc-frame\n' "$root" >&2
  printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
  exit 127
}
pin_product_config || {
  printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
  exit 2
}
export VIBECRAFTED_RUNTIME_ROOT="$root"
export VIBECRAFTED_ROOT="$root"
export VIBECRAFTED_RUNTIME_BIN="$root/bin"
export VIBECRAFTED_CORE_DIR="$root/vibecrafted-core"
export VIBECRAFTED_PYTHON="$root/bin/python3"
export VIBECRAFTED_VC_FRAME_BIN="$real"
unset VIBECRAFTED_PREFER_REPO_VC_FRAME VIBECRAFTED_PREFER_REPO_SPAWN
pin_darwin_socket_dir

if [[ $# -eq 0 ]]; then
  # Preserve the framework Start here/Operator surface through this generation.
  if [[ ! -x "$root/bin/vc-start" ]]; then
    printf 'vc-frame: product start missing: %s/bin/vc-start\n' "$root" >&2
    printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
    exit 127
  fi
  exec "$root/bin/vc-start"
fi

# Startup config and asset roots cannot be overridden by native CLI switches.
# Startup/new-tab/switch-session layouts must name an installed product file.
args=()
startup_options=1
while [[ $# -gt 0 ]]; do
  argument="$1"
  shift
  layout=""
  case "$argument" in
    action)
      args+=("$argument")
      [[ "$startup_options" == "1" ]] || continue
      [[ $# -gt 0 ]] || break
      action="$1"
      shift
      args+=("$action")
      startup_options=0
      case "$action" in
        new-tab|switch-session) continue ;;
        *)
          # Action payload (eg. write-chars or a pane command) is opaque data,
          # not a second set of startup flags.
          args+=("$@")
          break
          ;;
      esac
      ;;
    attach|a)
      startup_options=0
      args+=("$argument")
      continue
      ;;
    -s|--session|--server|--name|--cwd|-c|-n)
      if [[ "$startup_options" == "1" && "$argument" == "-c" ]]; then
        printf 'vc-frame: configuration is product-owned: %s\n' "$VC_FRAME_CONFIG_DIR" >&2
        exit 2
      fi
      if [[ "$startup_options" == "1" && "$argument" == "-n" ]]; then
        # Global -n is a layout; action new-tab -n is a tab name.
        argument="--new-session-with-layout"
        [[ $# -gt 0 ]] || { printf 'vc-frame: missing layout value\n' >&2; exit 2; }
        layout="$1"
        shift
      else
        args+=("$argument")
        # attach -c is a boolean; action new-tab -c is a cwd value.
        if [[ "$argument" != "-c" || "${action:-}" == "new-tab" || "${action:-}" == "switch-session" ]]; then
          [[ $# -gt 0 ]] || break
          args+=("$1")
          shift
        fi
        continue
      fi
      ;;
    --)
      args+=("$argument" "$@")
      break
      ;;
    --config|--config=*|--config-dir|--config-dir=*|--layout-dir|--layout-dir=*|--theme-dir|--theme-dir=*|--data-dir|--data-dir=*|--layout-string|--layout-string=*)
      printf 'vc-frame: configuration and assets are product-owned: %s\n' "$VC_FRAME_CONFIG_DIR" >&2
      exit 2
      ;;
    -c?*)
      if [[ "$startup_options" == "1" ]]; then
        printf 'vc-frame: configuration is product-owned: %s\n' "$VC_FRAME_CONFIG_DIR" >&2
        exit 2
      fi
      args+=("$argument")
      continue
      ;;
    -l|--layout|--new-session-with-layout)
      if [[ $# -eq 0 ]]; then
        printf 'vc-frame: missing layout value for %s\n' "$argument" >&2
        exit 2
      fi
      layout="$1"
      shift
      ;;
    --layout=*|--new-session-with-layout=*)
      layout="${argument#*=}"
      argument="${argument%%=*}"
      ;;
    -l?*|-n?*)
      if [[ "$startup_options" == "0" && "$argument" == -n* ]]; then
        args+=("$argument")
        continue
      fi
      layout="${argument:2}"
      layout="${layout#=}"
      argument="${argument:0:2}"
      ;;
    -s?*|-d|-h|-V)
      args+=("$argument")
      continue
      ;;
    -[!-]*)
      if [[ "$startup_options" == "1" ]]; then
        printf 'vc-frame: use separate startup flags; combined short options cannot override product configuration: %s\n' "$argument" >&2
        exit 2
      fi
      args+=("$argument")
      continue
      ;;
    *)
      args+=("$argument")
      continue
      ;;
  esac
  if [[ "$layout" != */* ]]; then
    layout="$VC_FRAME_CONFIG_DIR/layouts/${layout%.kdl}.kdl"
  fi
  layout_dir="$(cd -P "$(dirname "$layout")" 2>/dev/null && pwd -P)" || layout_dir=""
  product_layout_dir="$(cd -P "$VC_FRAME_CONFIG_DIR/layouts" && pwd -P)"
  if [[ "$layout" != /* || "$layout_dir" != "$product_layout_dir" || -L "$layout" || ! -f "$layout" || ! -r "$layout" ]]; then
    printf 'vc-frame: layout must be an installed file in %s/layouts: %s\n' "$VC_FRAME_CONFIG_DIR" "$layout" >&2
    printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
    exit 2
  fi
  args+=("$argument" "$layout")
done

exec "$real" --config-dir "$VC_FRAME_CONFIG_DIR" --config "$VC_FRAME_CONFIG_FILE" "${args[@]}"
