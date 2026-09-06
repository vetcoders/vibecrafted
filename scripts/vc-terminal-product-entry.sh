#!/bin/bash
# vc-terminal-product-entry.sh — generation-local choke point for the host
# terminal (Alacritty branded as vc-terminal).
#
# The Mach-O/ELF lives at ../libexec/vc-terminal. This wrapper always pins
# --config-file to the product vc-terminal.toml so a raw
#   $VIBECRAFTED_RUNTIME_HOME/releases/<ver>/bin/vc-terminal
# never falls through to the operator's private ~/.config/alacritty/.
# Alacritty does not expand ${HOME} in [terminal].shell.program; the private
# config is not a product surface.
#
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
host="${VIBECRAFTED_TERMINAL_HOST:-$root/libexec/vc-terminal}"
xdg="${XDG_CONFIG_HOME:-$HOME/.config}"
config="$xdg/vibecrafted/vc-terminal/vc-terminal.toml"

export VIBECRAFTED_RUNTIME_ROOT="${VIBECRAFTED_RUNTIME_ROOT:-$root}"
export VIBECRAFTED_ROOT="${VIBECRAFTED_ROOT:-$root}"

if [[ "$host" != /* || ! -x "$host" ]]; then
  printf 'vc-terminal: native host missing: %s\n' "$host" >&2
  exit 127
fi
if [[ ! -f "$config" ]]; then
  printf 'vc-terminal: product config missing: %s\n' "$config" >&2
  printf 'Vibecrafted does not read ~/.config/alacritty. Launch via the app or PATH vc-terminal after runtime-install.\n' >&2
  exit 2
fi

for argument in "$@"; do
  case "$argument" in
    --config-file | --config-file=*)
      printf 'vc-terminal: --config-file is product-owned: %s\n' "$config" >&2
      exit 2
      ;;
  esac
done
exec "$host" --config-file "$config" "$@"
