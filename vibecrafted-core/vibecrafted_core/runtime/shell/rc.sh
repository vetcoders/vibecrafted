# Vibecrafted shell integration — a guest in the user's own shell.
#
# The installer writes ONE managed line into the user's rc file pointing here;
# every other Vibecrafted setting lives under ~/.config/vibecrafted/. The
# managed line (POSIX-safe for bash, zsh and sh):
#
#   [ -r "${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/shell/rc.sh" ] &&
#     . "${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/shell/rc.sh"
#
# This file composes, never restricts: it does not move ZDOTDIR, export
# PYTHONHOME/PYTHONPATH, unset, alias, or reorder anything the user already
# has. The only mutation is the launcher directory joining PATH when missing —
# at the END, with lower priority than every entry the user already has, so a
# tool resolves through Vibecrafted only when the user's own PATH cannot
# resolve it. The user's own tools keep their names and their order, and
# `python3`, `uv` and friends still resolve exactly where they did without
# Vibecrafted.

# shellcheck shell=sh

case ":${PATH}:" in
*":${HOME}/.local/bin:"*) ;;
*)
  PATH="${PATH:+${PATH}:}${HOME}/.local/bin"
  export PATH
  ;;
esac
