#!/usr/bin/env bash
# vc-frame-product-entry.sh — product choke point for bare `vc-frame`
#
# Installed as ~/.local/bin/vc-frame (wrapper). The authoritative binary lives
# in the product environment, the Cargo prefix, or Vibecrafted's data root.
#
# Policy (goal: bare frame is backyard-safe):
#   1. Always pin VC_FRAME_CONFIG_DIR to the canonical product config
#      so bare attach gets the same Super binds, layouts, and scripts as vc-start.
#   2. Product operator session names (vibecrafted / operator / default operator
#      session) never launch without the product config root.
#   3. Everything else execs the real binary with that env — no partial chrome.
set -euo pipefail

resolve_real_bin() {
  if [[ -n "${VIBECRAFTED_VC_FRAME_BIN:-}" && -x "${VIBECRAFTED_VC_FRAME_BIN}" ]]; then
    printf '%s\n' "$VIBECRAFTED_VC_FRAME_BIN"
    return 0
  fi

  local wrapper_dir
  wrapper_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  for candidate in \
    "$wrapper_dir/../libexec/vc-frame" \
    "${HOME}/.cargo/bin/vc-frame" \
    "${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/bin/vc-frame" \
    "${HOME}/.local/share/vibecrafted/bin/vc-frame"
  do
    # Ambient shell wrappers are never a real vc-frame. In particular,
    # ~/.local/share/vibecrafted/bin/vc-frame may resolve back to this product
    # entry and recurse forever. Follow symlinks, but accept native code only.
    if [[ -x "$candidate" ]] && file -Lb "$candidate" 2>/dev/null | grep -Eqi 'Mach-O|ELF'; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

pin_darwin_socket_dir() {
  # Claude / CLI / any path that does not inherit AppDelegate must still land
  # on the short macOS socket root. TMPDIR + contract_version_N already fills
  # sockaddr_un (104) before a worker host name is appended.
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
  local xdg="${XDG_CONFIG_HOME:-$HOME/.config}"
  local view="$xdg/vibecrafted/vc-frame"
  if [[ -f "$view/config.kdl" ]]; then
    export VC_FRAME_CONFIG_DIR="$view"
    return 0
  fi
  return 1
}

is_product_session_name() {
  case "${1:-}" in
    vibecrafted|operator|vibecrafted-console|"vibecrafted console") return 0 ;;
    *) return 1 ;;
  esac
}

real="$(resolve_real_bin)" || {
  printf 'vc-frame product entry: real binary not found.\n' >&2
  printf 'Install with: make -C <vc-frame-checkout> install\n' >&2
  printf 'Or set VIBECRAFTED_VC_FRAME_BIN to the Mach-O/ELF binary.\n' >&2
  exit 127
}

# Always prefer product config when available (closes bare-vs-vc-start chrome split).
pin_product_config || true
pin_darwin_socket_dir

# If operator intentionally wants product cockpit, prefer vc-start when present.
# Bare `vc-frame` with no args often creates an anonymous session — nudge.
if [[ $# -eq 0 ]] && command -v vc-start >/dev/null 2>&1; then
  # No args: product entry is vc-start (Start here). Bare open would fight slots.
  exec vc-start
fi

# attach/create of product session names → require product config or refuse.
# Flags: attach|a, -s|--session (new session name), --new-session-with-layout.
args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
  a="${args[$i]}"
  case "$a" in
    attach|a|-s|--session)
      next="${args[$((i + 1))]:-}"
      if is_product_session_name "$next"; then
        if ! pin_product_config; then
          printf 'Refusing bare attach to product session %q without product config.\n' "$next" >&2
          printf 'Run: vc-start   # projects config + Start here layout\n' >&2
          exit 2
        fi
      fi
      ;;
    --session=*|-s=*)
      next="${a#*=}"
      if is_product_session_name "$next"; then
        if ! pin_product_config; then
          printf 'Refusing bare attach to product session %q without product config.\n' "$next" >&2
          printf 'Run: vc-start   # projects config + Start here layout\n' >&2
          exit 2
        fi
      fi
      ;;
    -n|--new-session-with-layout)
      if ! pin_product_config; then
        printf 'Refusing bare new-session-with-layout without product config.\n' >&2
        printf 'Run: vc-start\n' >&2
        exit 2
      fi
      ;;
  esac
  i=$((i + 1))
done

exec "$real" "$@"
