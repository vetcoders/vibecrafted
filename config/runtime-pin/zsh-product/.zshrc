# Vibecrafted product shell interactive stage: .zshrc.
#
# Closes the Founder's original defect: a tool whose PATH is declared only
# in the user's ~/.zshrc stayed invisible in the product shell until
# personal-shell. Same guest composition as the login stages in this
# directory: the user's own .zshrc first — exactly as zsh would run it
# without Vibecrafted (the runtime-pin guest stage: ZDOTDIR points at the
# user's directory while the file runs, a ZDOTDIR move is followed and
# remembered in VIBECRAFTED_USER_ZDOTDIR) — then the pinned generation's
# python door back in front of every prepend, then the product interactive
# profile (interactive.zsh, staged beside this file) on top. Composition,
# never takeover: nothing here reorders the user's own entries; only the
# declared python door stays in front, and personal-shell remains the
# explicit release of the product's interactive pins.
#
# /etc/zshrc is never composed: the product entry starts `zsh -d` so the
# distribution interactive file stays out by decision (it may compinit-
# prompt on insecure host completions and consume the first terminal
# command); a plain `zsh -l` on this ZDOTDIR runs it natively. Either way,
# sourcing it here could only replay or override that choice.
_vc_product_zdotdir="${ZDOTDIR}"
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/zsh/.zshrc" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/zsh/.zshrc"
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/pin.zsh" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/pin.zsh"
[[ ! -r "${_vc_product_zdotdir:-}/interactive.zsh" ]] \
  || builtin source "${_vc_product_zdotdir}/interactive.zsh"
unset _vc_product_zdotdir _vc_guest_zdotdir _vc_user_zdotdir
