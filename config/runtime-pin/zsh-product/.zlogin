# Vibecrafted product shell login stage: .zlogin.
#
# Native zsh reads .zlogin after .zshrc. The interactive stage composes the
# user's .zshrc before the product profile; personal-shell explicitly releases
# the interactive pins. This stage does not compose the user's .zlogin: it
# only restores the pinned generation's python door.
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/pin.zsh" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/pin.zsh"
