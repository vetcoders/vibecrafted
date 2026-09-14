#!/usr/bin/env bash
# vc-quick-cmd.sh — non-ephemeral mini console for the ❯_ Quick cmd chip
#
# Spec 1.2 §C: a clean prompt in the pane. Help stays on request
# (`vibecrafted --help`); this wrapper does not dump a banner or the
# product command list on every open. User dotfiles and product-owned
# shell init still load.
set -euo pipefail

export VIBECRAFTED_QUIET_START=1

# Login shell — vibecrafted CLI and operator PATH come from the profile.
exec "${SHELL:-/bin/zsh}" -l
