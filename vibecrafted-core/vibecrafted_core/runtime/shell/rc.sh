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
# the user's own tools keep their names and their order, and `python3`, `uv`
# and friends still resolve exactly where they did without Vibecrafted.

# shellcheck shell=sh

case ":${PATH}:" in
*":${HOME}/.local/bin:"*) ;;
*)
  PATH="${HOME}/.local/bin${PATH:+:${PATH}}"
  export PATH
  ;;
esac
