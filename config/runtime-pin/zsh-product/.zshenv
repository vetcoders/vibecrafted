# Vibecrafted product shell login stage: .zshenv.
#
# Product shells (vc-terminal, Frame panes, Quick cmd) run `zsh -d` with the
# product ZDOTDIR, so zsh itself reads neither the global login files
# (/etc/zprofile et al.) nor the user's own startup. This stage restores the
# native composition for the environment layer:
#
#   1. the user's own .zshenv exactly as zsh would run it without
#      Vibecrafted (the runtime-pin guest stage: ZDOTDIR points at the
#      user's directory while the file runs, a ZDOTDIR move is followed and
#      remembered in VIBECRAFTED_USER_ZDOTDIR),
#   2. the pinned generation's python door back in front of every prepend.
#
# /etc/zshenv is deliberately NOT composed. zsh reads it on every startup
# before any ZDOTDIR file, and `-d` (GLOBAL_RCS) does not gate it — only
# `-f` (RCS) does, and that suppresses this stage too (zshoptions(1)). So
# whenever this file runs, /etc/zshenv already ran; sourcing it here could
# only replay it. Stock macOS ships no /etc/zshenv, which is why the replay
# was never measurable; the contract is pinned structurally by
# tests/tui/test_shell_login_composition.py instead.
#
# The user's interactive ~/.zshrc layer is composed by the .zshrc stage in
# this directory; personal-shell stays the explicit release of the product's
# interactive pins. The guest stage's own pin lookup (${ZDOTDIR:h}/pin.zsh)
# names this ZDOTDIR's parent and finds nothing; the explicit pin source
# below is the product shell's door.
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/zsh/.zshenv" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/zsh/.zshenv"
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/pin.zsh" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/pin.zsh"
unset _vc_guest_zdotdir _vc_user_zdotdir
