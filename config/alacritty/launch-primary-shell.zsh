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
  # An interactive intermediate zsh takes the terminal foreground group. When
  # it exits, the final shell below can no longer read the orphaned PTY (EIO).
  # The command keeps its controlling terminal without an interactive parent.
  /bin/zsh -lc '"$0" "$@"' "$product_entry" "$@"
  product_entry_status=$?
  # vc-frame owns its own alternate-buffer lifecycle; clean sticky smcup.
  leave_alt_screen
  if (( product_entry_status != 0 )); then
    report_start_failure "workspace entry failed (exit $product_entry_status)"
  fi
}

report_start_failure() {
  local reason="$1" log_dir="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/logs" log=""
  log="$log_dir/terminal-startup.log"
  if ! (umask 077; mkdir -p "$log_dir" && printf '%s\n' "$reason" >>"$log"); then
    log="unavailable (cannot write $log)"
  fi
  printf '\nVibecrafted: %s. Your terminal is still available.\nRecovery: vc-frame attach %q\nList sessions: vc-frame list-sessions\nLog: %s\n\n' \
    "$reason" "${VIBECRAFTED_FRAME_HOST_SESSION:-vc-host}" "$log" >&2
}

# Explicit product argv (vc-terminal -e … vc-start …) keeps its command.
# Top-level VC Terminal with no argv enters the operator workspace via
# `vc-start resume` (create if missing, return if live). Failure leaves a
# usable shell. Nested/quiet shells skip the auto-attach.
product_entry=""
recovery_shell=false
if [[ -n "${VIBECRAFTED_TERMINAL_STARTUP_ERROR:-}" ]]; then
  report_start_failure "$VIBECRAFTED_TERMINAL_STARTUP_ERROR; repair: vc-terminal --doctor"
  unset VIBECRAFTED_TERMINAL_STARTUP_ERROR
  # Keep the diagnosis visible; do not immediately cover it with a Frame attach.
  recovery_shell=true
fi
case "${1##*/}" in
  vc-*|vibecrafted|vibecrafted-*) product_entry="$1" ;;
esac

if [[ -n "$product_entry" ]]; then
  shift
  run_product_entry "$product_entry" "$@"
elif ! $recovery_shell && ! skip_auto_workspace; then
  if product_entry="$(resolve_vc_start)"; then
    run_product_entry "$product_entry" resume
  else
    report_start_failure "vc-start is unavailable; repair: open Vibecrafted.app and select Repair Runtime"
  fi
fi

exec /bin/zsh -l
