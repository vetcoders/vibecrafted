# Vibecrafted product shell login stage: .zprofile.
#
# Native login composition for the product shell: the global baseline first
# (/etc/zprofile carries macOS path_helper — the login PATH floor), then the
# user's own .zprofile via the runtime-pin guest stage, then the pinned
# generation's python door back in front. Homebrew's `brew shellenv` and
# every other user-declared login PATH entry arrive with the user's file; no
# path list is hard-coded here.
#
# The global baseline runs ONLY when native zsh skipped it. The product
# entry starts `zsh -d` (GLOBAL_RCS off), which bypasses /etc/zprofile, so
# this stage supplies it; a plain `zsh -l` on this ZDOTDIR already ran
# /etc/zprofile natively and this stage must not replay it (measured before
# this guard: path_helper fired 1x under `zsh -d -l`, 2x under plain
# `zsh -l`). Tracking the option instead of the -d flag keeps the count at
# exactly one even when an earlier user stage flips GLOBAL_RCS itself.
[[ -o globalrcs || ! -r /etc/zprofile ]] || builtin source /etc/zprofile
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/zsh/.zprofile" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/zsh/.zprofile"
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/pin.zsh" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/pin.zsh"
unset _vc_guest_zdotdir _vc_user_zdotdir
