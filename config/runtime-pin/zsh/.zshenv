# Vibecrafted runtime guest ZDOTDIR.
#
# The runtime points ZDOTDIR here for the processes it starts (dispatched
# workers and their tools), so a nested zsh -- login, interactive or neither --
# keeps the runtime python pin. It is a guest, not a profile: every stage runs
# the user's own file from VIBECRAFTED_USER_ZDOTDIR (default $HOME) at top
# level with ZDOTDIR pointing there, exactly as zsh would without Vibecrafted,
# and only then moves the runtime python door back to the front of PATH.
# Sourced at top level on purpose: inside a function a personal `typeset`
# would become local and vanish.
_vc_guest_zdotdir="${ZDOTDIR}"
_vc_user_zdotdir="${VIBECRAFTED_USER_ZDOTDIR:-$HOME}"
if [[ "$_vc_user_zdotdir" != "$_vc_guest_zdotdir" && -r "$_vc_user_zdotdir/.zshenv" ]]; then
  ZDOTDIR="$_vc_user_zdotdir"
  builtin source "$_vc_user_zdotdir/.zshenv"
  # A personal .zshenv may move ZDOTDIR; the later stages follow it.
  _vc_user_zdotdir="${ZDOTDIR:-$HOME}"
  export VIBECRAFTED_USER_ZDOTDIR="$_vc_user_zdotdir"
  ZDOTDIR="$_vc_guest_zdotdir"
fi
[[ ! -r "${_vc_guest_zdotdir:h}/pin.zsh" ]] || builtin source "${_vc_guest_zdotdir:h}/pin.zsh"
