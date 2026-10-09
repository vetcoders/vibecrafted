# Vibecrafted runtime guest ZDOTDIR, .zprofile stage: the user's own .zprofile
# with ZDOTDIR pointing at it, then the runtime python door back in front.
# See .zshenv in this directory.
_vc_guest_zdotdir="${ZDOTDIR}"
_vc_user_zdotdir="${VIBECRAFTED_USER_ZDOTDIR:-$HOME}"
if [[ "$_vc_user_zdotdir" != "$_vc_guest_zdotdir" && -r "$_vc_user_zdotdir/.zprofile" ]]; then
  ZDOTDIR="$_vc_user_zdotdir"
  builtin source "$_vc_user_zdotdir/.zprofile"
  ZDOTDIR="$_vc_guest_zdotdir"
fi
[[ ! -r "${_vc_guest_zdotdir:h}/pin.zsh" ]] || builtin source "${_vc_guest_zdotdir:h}/pin.zsh"
