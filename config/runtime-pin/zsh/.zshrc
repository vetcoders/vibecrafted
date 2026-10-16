# Vibecrafted runtime guest ZDOTDIR, .zshrc stage: the user's own .zshrc
# with ZDOTDIR pointing at it, then the runtime python door back in front.
# See .zshenv in this directory.
_vc_guest_zdotdir="${ZDOTDIR}"
_vc_user_zdotdir="${VIBECRAFTED_USER_ZDOTDIR:-$HOME}"
if [[ "$_vc_user_zdotdir" != "$_vc_guest_zdotdir" && -r "$_vc_user_zdotdir/.zshrc" ]]; then
  ZDOTDIR="$_vc_user_zdotdir"
  builtin source "$_vc_user_zdotdir/.zshrc"
  # Only retain the guest when the personal stage left its directory alone.
  # An explicit move (including unset) owns native zsh's later stage lookup.
  export VIBECRAFTED_USER_ZDOTDIR="${ZDOTDIR-$HOME}"
  if [[ ${ZDOTDIR+x} == x && "$ZDOTDIR" == "$_vc_user_zdotdir" ]]; then
    ZDOTDIR="$_vc_guest_zdotdir"
  fi
fi
[[ ! -r "${_vc_guest_zdotdir:h}/pin.zsh" ]] || builtin source "${_vc_guest_zdotdir:h}/pin.zsh"
# Interactive hooks (mise, pyenv, direnv) rewrite PATH before each prompt.
# Ours is added last, so it runs after them.
if [[ -o interactive ]] && (( $+functions[vc_runtime_pin_path] )); then
  autoload -Uz add-zsh-hook
  add-zsh-hook precmd vc_runtime_pin_path
fi
