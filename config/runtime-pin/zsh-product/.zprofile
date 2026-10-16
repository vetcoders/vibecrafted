# Vibecrafted product shell login stage: .zprofile.
#
# Same composition as .zshenv in this directory: the global baseline first
# (/etc/zprofile carries macOS path_helper — the login PATH floor that
# `zsh -d` skips), then the user's own .zprofile via the runtime-pin guest
# stage, then the pinned generation's python door back in front. Homebrew's
# `brew shellenv` and every other user-declared login PATH entry arrive with
# the user's file; no path list is hard-coded here.
[[ ! -r /etc/zprofile ]] || builtin source /etc/zprofile
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/zsh/.zprofile" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/zsh/.zprofile"
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/pin.zsh" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/pin.zsh"
unset _vc_guest_zdotdir _vc_user_zdotdir
