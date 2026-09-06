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

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
host="$root/libexec/vc-terminal"
config="$HOME/.config/vibecrafted/vc-terminal/vc-terminal.toml"

export VIBECRAFTED_RUNTIME_ROOT="$root"
export VIBECRAFTED_ROOT="$root"
export VIBECRAFTED_TERMINAL_HOST="$host"
export XDG_CONFIG_HOME="$HOME/.config"
export VIBECRAFTED_PYTHON="$root/bin/python3"
export VIBECRAFTED_VC_FRAME_BIN="$root/libexec/vc-frame"
export VC_FRAME_CONFIG_DIR="$HOME/.config/vibecrafted/vc-frame"
unset VC_FRAME_CONFIG_FILE PYTHONPATH PYTHONHOME

if [[ "$host" != /* || ! -x "$host" || -L "$host" || -L "$root/libexec" ]]; then
  printf 'vc-terminal: native host missing: %s\n' "$host" >&2
  exit 127
fi
if [[ ! -f "$config" || -L "$config" || -L "$HOME/.config" \
  || -L "$HOME/.config/vibecrafted" || -L "$HOME/.config/vibecrafted/vc-terminal" ]]; then
  printf 'vc-terminal: product config missing: %s\n' "$config" >&2
  printf 'Vibecrafted does not read ~/.config/alacritty. Launch via the app or PATH vc-terminal after runtime-install.\n' >&2
  exit 2
fi

# Alacritty's -e/--command consumes all remaining argv, including hyphens.
# Consume values of terminal options so a title such as "-e" is not a boundary.
value_pending=false
for argument in "$@"; do
  if $value_pending; then
    value_pending=false
    continue
  fi
  # clap accepts short flag clusters: -veCOMMAND has the same boundary as -e.
  if [[ "$argument" =~ ^-[qv]*e ]]; then
    break
  fi
  if [[ "$argument" =~ ^-[qv]*[tTo]$ ]]; then
    value_pending=true
    continue
  fi
  case "$argument" in
    -e* | --command | --command=* | --)
      break
      ;;
    --config-file | --config-file=*)
      printf 'vc-terminal: --config-file is product-owned: %s\n' "$config" >&2
      exit 2
      ;;
    --embed | --socket | --working-directory | --title | -T | -t | --class | -o | --option | -s)
      value_pending=true
      ;;
  esac
done
exec "$host" --config-file "$config" "$@"
