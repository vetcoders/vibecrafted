#!/usr/bin/env bash
# vc-quick-cmd.sh — non-ephemeral mini console for the ❯_ Quick cmd chip
#
# Spec 1.2 §C:
#   ┌─ ❯_ Quick cmd ────────────────────────────────────── PIN ◉ ┐
#   │ operator@host-a in ~/.vibecrafted                              │
#   │ $ cargo check                                              │
#   └──────────────────────────────────────────────────────────┘
#
# Prints a one-line host@cwd banner, then hands the pane to a login shell.
# The shell stays open so the operator can inspect command output.
set -euo pipefail

user="${USER:-op}"
host="$(hostname -s 2>/dev/null || uname -n 2>/dev/null || echo host)"
# Collapse $HOME to ~ for a short path.
cwd="${PWD}"
if [[ -n "${HOME:-}" && "$cwd" == "$HOME"* ]]; then
  cwd="~${cwd#"$HOME"}"
fi

printf '\n  %s@%s in %s\n\n' "$user" "$host" "$cwd"

# Login shell — vibecrafted CLI and operator PATH come from the profile.
exec "${SHELL:-/bin/zsh}" -l
