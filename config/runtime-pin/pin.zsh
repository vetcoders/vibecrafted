# Vibecrafted runtime python pin, zsh side.
#
# zsh startup reorders PATH before any command runs: /etc/zprofile runs
# path_helper for login shells, and personal ~/.zshenv / ~/.zshrc prepend
# their own directories. An inherited PATH whose first entry was the runtime
# python door therefore loses it in every nested `zsh -c`, `zsh -l` and
# `zsh -i`. The runtime's guest ZDOTDIR (zsh/) and the product shell ZDOTDIR
# source this file after their startup files; vc_runtime_pin_path moves the
# door of the generation that carries this file back to the front. It only
# reorders PATH: the interpreter itself stays VIBECRAFTED_PYTHON, inherited.
typeset -g _vc_runtime_pin_door="${${(%):-%x}:A:h}/bin"

vc_runtime_pin_path() {
  [[ -x "$_vc_runtime_pin_door/python3" ]] || return 0
  path=("$_vc_runtime_pin_door" ${path:#$_vc_runtime_pin_door})
}

vc_runtime_pin_path
