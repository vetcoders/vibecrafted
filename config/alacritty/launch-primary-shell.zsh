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
#   $HOME/.config/vibecrafted/vc-terminal/launch-primary-shell.zsh
# Private ~/.config/alacritty is not a product surface.
#
# Source of truth in this repo: config/alacritty/launch-primary-shell.zsh
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI

# The private .zshrc sources this entry; return instead of replacing that shell.
if [[ -n "${ZSH_VERSION:-}" && "${ZSH_EVAL_CONTEXT:-}" == *:file ]]; then
  source "$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"
  return $?
fi

# Isolate all interactive startup from the user's .zshrc/.bashrc.
export ZDOTDIR="$HOME/.config/vibecrafted/vc-terminal"

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

# With no command, open a normal login shell. Explicit product entries retain
# their argv and run in a login+interactive shell; their failure must leave the
# terminal usable with the error visible in scrollback.
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
    printf '\nVibecrafted could not start the requested workspace (exit %s).\nYour terminal is still available; correct the command and try again.\n\n' "$product_entry_status" >&2
  fi
fi

exec /bin/zsh -l
