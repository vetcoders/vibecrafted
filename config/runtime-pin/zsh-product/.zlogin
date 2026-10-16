# Vibecrafted product shell login stage: .zlogin.
#
# Native zsh reads .zlogin after .zshrc. The product shell keeps its
# interactive layer product-owned (the personal ~/.zshrc stays behind the
# explicit personal-shell command), so a personal .zlogin — which may rely on
# that interactive layer — is not composed here. This stage only restores the
# pinned generation's python door, like every product login stage.
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/pin.zsh" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/pin.zsh"
