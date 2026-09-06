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

# CliArgs root options are not clap globals: the first unconsumed positional
# token transfers ownership to the native subcommand (including every alias).
# Only layout-bearing actions and Options need an additional product guard.
# All other subcommands, values and execution payloads stay native argv.
pin_product_layout() {
  local layout="$1" layout_dir product_layout_dir
  if [[ "$layout" != */* ]]; then
    layout="$VC_FRAME_CONFIG_DIR/layouts/${layout%.kdl}.kdl"
  fi
  layout_dir="$(cd -P "$(dirname "$layout")" 2>/dev/null && pwd -P)" || layout_dir=""
  product_layout_dir="$(cd -P "$VC_FRAME_CONFIG_DIR/layouts" && pwd -P)"
  if [[ "$layout" != /* || "$layout_dir" != "$product_layout_dir" || -L "$layout" || ! -f "$layout" || ! -r "$layout" ]]; then
    printf 'vc-frame: layout must be an installed file in %s/layouts: %s\n' "$VC_FRAME_CONFIG_DIR" "$layout" >&2
    exit 2
  fi
  printf '%s\n' "$layout"
}

args=()
context=root
while [[ $# -gt 0 ]]; do
  argument="$1"
  shift
  layout_flag=""
  layout_value=""
  case "$context:$argument" in
    root:--|attach:--|options:--|new-tab:--|switch-session:--)
      # Native validation owns whether a delimiter is legal here; never scan
      # command/text/session data behind it for product flags.
      args+=("$argument" "$@")
      break
      ;;
    root:action|root:ac)
      args+=("$argument")
      [[ $# -gt 0 ]] || break
      context="$1"
      args+=("$1")
      shift
      case "$context" in
        new-tab|switch-session|override-layout) continue ;;
        *) args+=("$@"); break ;;
      esac
      ;;
    root:attach|root:a) context=attach; args+=("$argument"); continue ;;
    root:options|attach:options) context=options; args+=("$argument"); continue ;;
    root:--config|root:--config=*|root:--config-dir|root:--config-dir=*|root:-c|root:-c?*|root:--data-dir|root:--data-dir=*|root:--layout-string|root:--layout-string=*|options:--layout-dir|options:--layout-dir=*|options:--theme-dir|options:--theme-dir=*|new-tab:--layout-dir|new-tab:--layout-dir=*|new-tab:--layout-string|new-tab:--layout-string=*|switch-session:--layout-dir|switch-session:--layout-dir=*|switch-session:--layout-string|switch-session:--layout-string=*|override-layout:--layout-dir|override-layout:--layout-dir=*|override-layout:--layout-string|override-layout:--layout-string=*)
      printf 'vc-frame: configuration and assets are product-owned: %s\n' "$VC_FRAME_CONFIG_DIR" >&2
      exit 2
      ;;
    root:-l|root:--layout|root:-n|root:--new-session-with-layout|options:--default-layout|new-tab:-l|new-tab:--layout|switch-session:-l|switch-session:--layout)
      [[ $# -gt 0 ]] || { printf 'vc-frame: missing layout value for %s\n' "$argument" >&2; exit 2; }
      layout_flag="$argument"
      layout_value="$1"
      shift
      ;;
    root:--layout=*|root:--new-session-with-layout=*|options:--default-layout=*|new-tab:--layout=*|switch-session:--layout=*)
      layout_flag="${argument%%=*}"
      layout_value="${argument#*=}"
      ;;
    root:-l?*|root:-n?*|new-tab:-l?*|switch-session:-l?*)
      layout_flag="${argument:0:2}"
      layout_value="${argument:2}"
      layout_value="${layout_value#=}"
      ;;
    root:-s|root:--session|root:--server|root:--max-panes|attach:--index|attach:--token|attach:-t|attach:--ca-cert|new-tab:-n|new-tab:--name|new-tab:-c|new-tab:--cwd|new-tab:--initial-plugin|switch-session:--tab-position|switch-session:--pane-id|switch-session:-c|switch-session:--cwd)
      args+=("$argument")
      [[ $# -gt 0 ]] || break
      args+=("$1")
      shift
      continue
      ;;
    root:-s?*|root:-d|root:-h|root:-V)
      args+=("$argument"); continue ;;
    root:-[!-]*)
      # Root clusters could hide -c/-l/-n. Native subcommand clusters are
      # untouched. Keep this explicit, bounded restriction at product entry.
      printf 'vc-frame: use separate root short options: %s\n' "$argument" >&2
      exit 2
      ;;
    root:--*) args+=("$argument"); continue ;;
    root:*) args+=("$argument" "$@"); break ;;
    options:--*=*|options:--help|options:-h)
      args+=("$argument"); continue ;;
    options:--*)
      # Options contains long value options. Preserve the following value
      # even if it resembles a product switch; clap validates it as before.
      args+=("$argument")
      [[ $# -gt 0 ]] || break
      args+=("$1")
      shift
      continue
      ;;
    override-layout:--)
      # Here the positional tail is a layout, not an execution payload.
      args+=("$argument")
      [[ $# -gt 0 ]] || break
      layout_value="$(pin_product_layout "$1")" || exit $?
      args+=("$layout_value")
      shift
      args+=("$@")
      break
      ;;
    override-layout:-*) args+=("$argument"); continue ;;
    override-layout:*)
      layout_value="$(pin_product_layout "$argument")" || exit $?
      args+=("$layout_value")
      continue
      ;;
    attach:*)
      args+=("$argument")
      # Attach's only short value option is -t, which may follow booleans in
      # a native cluster (-ct TOKEN). Do not mistake its value for `options`.
      if [[ "$argument" =~ ^-[cbfr]*t$ && $# -gt 0 ]]; then
        args+=("$1")
        shift
      fi
      continue
      ;;
    *) args+=("$argument"); continue ;;
  esac
  layout_value="$(pin_product_layout "$layout_value")" || exit $?
  args+=("$layout_flag" "$layout_value")
done

exec "$real" --config-dir "$VC_FRAME_CONFIG_DIR" --config "$VC_FRAME_CONFIG_FILE" "${args[@]}"
