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

inside_vc_frame() {
  # Trusted attached-context only (same contract as _vetcoders_in_vc_frame).
  [[ -n "${VC_FRAME_PANE_ID:-}" && -n "${VC_FRAME_SESSION_NAME:-}" ]]
}

skip_auto_workspace() {
  # Nested product shells (Quick cmd, Frame panes, sourced .zshrc, the
  # terminal child that already ran vc-start) must not spawn another attach.
  [[ "${VIBECRAFTED_QUIET_START:-0}" == "1" ]] && return 0
  [[ "${VIBECRAFTED_PRIMARY_SHELL_ATTACHED:-0}" == "1" ]] && return 0
  inside_vc_frame && return 0
  return 1
}

resolve_vc_start() {
  local candidate=""
  if [[ -n "${VIBECRAFTED_RUNTIME_BIN:-}" && -x "${VIBECRAFTED_RUNTIME_BIN}/vc-start" ]]; then
    printf '%s\n' "${VIBECRAFTED_RUNTIME_BIN}/vc-start"
    return 0
  fi
  candidate="$(command -v vc-start 2>/dev/null || true)"
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  return 1
}

run_product_entry() {
  local product_entry="$1"
  local product_entry_status=0
  shift
  export VIBECRAFTED_PRIMARY_SHELL_ATTACHED=1
  /bin/zsh -lic '"$0" "$@"' "$product_entry" "$@"
  product_entry_status=$?
  # vc-frame owns its own alternate-buffer lifecycle; clean sticky smcup.
  leave_alt_screen
  if (( product_entry_status != 0 )); then
    printf '\nVibecrafted could not start the requested workspace (exit %s).\nYour terminal is still available; correct the command and try again.\n\n' "$product_entry_status" >&2
  fi
}

# Explicit product argv (vc-terminal -e … vc-start …) keeps its command.
# Top-level VC Terminal with no argv enters the operator workspace via
# `vc-start resume` (create if missing, return if live). Failure leaves a
# usable shell. Nested/quiet shells skip the auto-attach.
product_entry=""
case "${1##*/}" in
  vc-*|vibecrafted|vibecrafted-*) product_entry="$1" ;;
esac

if [[ -n "$product_entry" ]]; then
  shift
  run_product_entry "$product_entry" "$@"
elif ! skip_auto_workspace && product_entry="$(resolve_vc_start)"; then
  run_product_entry "$product_entry" resume
fi

exec /bin/zsh -l
