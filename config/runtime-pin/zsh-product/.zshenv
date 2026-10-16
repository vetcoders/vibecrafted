# Vibecrafted product shell login stage: .zshenv.
#
# Product shells (vc-terminal, Frame panes, Quick cmd) run `zsh -d` with the
# product ZDOTDIR, so zsh itself reads neither the global /etc stages nor the
# user's own startup. This stage restores the native login composition:
#
#   1. the global .zshenv baseline that `-d` skipped,
#   2. the user's own .zshenv exactly as zsh would run it without Vibecrafted
#      (the runtime-pin guest stage: ZDOTDIR points at the user's directory
#      while the file runs, a ZDOTDIR move is followed and remembered in
#      VIBECRAFTED_USER_ZDOTDIR),
#   3. the pinned generation's python door back in front of every prepend.
#
# The user's interactive ~/.zshrc is not part of the product shell;
# personal-shell stays the explicit opt-in for that layer. The guest stage's
# own pin lookup (${ZDOTDIR:h}/pin.zsh) names this ZDOTDIR's parent and finds
# nothing; the explicit pin source below is the product shell's door.
[[ ! -r /etc/zshenv ]] || builtin source /etc/zshenv
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/zsh/.zshenv" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/zsh/.zshenv"
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/pin.zsh" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/pin.zsh"
unset _vc_guest_zdotdir _vc_user_zdotdir
