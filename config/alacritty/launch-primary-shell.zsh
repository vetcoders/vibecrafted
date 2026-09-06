#!/usr/bin/env bash
# shellcheck shell=bash
# Host-shell entrypoint for Alacritty / vc-terminal.
# (zsh-compatible; written as bash for shellcheck + portable /bin/sh callers.)
#
# Keep the interactive shell on the PRIMARY buffer so Alacritty scrollback
# works and mouse-wheel (~Alt) browses output instead of sending Up/Down.
# TUIs (Atuin, less, vim, vc-frame panes) enter/leave the alternate buffer
# themselves — do not smcup the whole session.
#
# Product install path (installer-owned):
#   ${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/vc-terminal/launch-primary-shell.zsh
# Private ~/.config/alacritty is not a product surface.
#
# Source of truth in this repo: config/alacritty/launch-primary-shell.zsh
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI

tty_path="/dev/tty"

leave_alt_screen() {
  if [[ -w "$tty_path" ]]; then
    if command -v tput >/dev/null 2>&1; then
      tput rmcup >"$tty_path" 2>/dev/null || printf '\e[?1049l' >"$tty_path"
    else
      printf '\e[?1049l' >"$tty_path"
    fi
  fi
}

# Ensure we start on the primary buffer (scrollback + ~Alt wheel bindings).
leave_alt_screen

# Product entries hosted by this terminal. `vc-start operator` is the window
# Vibecrafted.app opens; the other verbs arrive when a public VC entry had no
# controlling terminal and asked the product terminal host for a real PTY
# (_vetcoders_open_entry_in_vc_terminal). All of them need the SAME treatment:
# a login+interactive zsh so the runtime shell functions exist, then a clean
# alternate-buffer handoff. Matching only vc-start silently dropped the argv of
# every other verb into a bare login shell.
product_entry=""
case "${1##*/}" in
  vc-*|vibecrafted|vibecrafted-*) product_entry="$1" ;;
esac

if [[ -n "$product_entry" ]]; then
  shift
  /bin/zsh -lic '"$0" "$@"' "$product_entry" "$@"
  product_entry_status=$?
  # vc-frame owns its own alternate-buffer lifecycle; clean sticky smcup.
  leave_alt_screen
  if (( product_entry_status != 0 )); then
    # A failed vc-frame launch no longer owns a usable PTY. Do not drop the
    # user into a login shell on that broken frontend; let vc-terminal close
    # cleanly so Vibecrafted.app can open a fresh window.
    exit "$product_entry_status"
  fi
fi

exec /bin/zsh -l
