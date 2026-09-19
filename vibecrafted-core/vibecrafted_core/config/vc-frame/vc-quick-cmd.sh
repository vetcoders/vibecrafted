#!/usr/bin/env bash
# vc-quick-cmd.sh — one-shot composer for the ❯_ Quick cmd chip
#
# Spec 1.2 §C: a clean prompt, no banner. Help stays on request
# (`vibecrafted --help`). This wrapper is the temporary composer: it reads
# one command, runs it in a login shell so PATH/product init load, then
# closes THIS pane by pane-id.
#
# Never `close-pane` without `--pane-id`. A bare close-pane follows
# focus and would kill whichever pane holds it — including a durable Agent the
# command just spawned in another tab. Missing pane-id: exit without
# guessing. Focus-loss close is a Frame event; compact-bar open_quick_cmd
# has none, so this script does not claim blur behavior.
set -euo pipefail

export VIBECRAFTED_QUIET_START=1

_pane="${VC_FRAME_PANE_ID:-${ZELLIJ_PANE_ID:-}}"

_close_self() {
  if [[ -z "${_pane}" ]]; then
    return 0
  fi
  if ! command -v vc-frame >/dev/null 2>&1; then
    return 0
  fi
  vc-frame action close-pane --pane-id "${_pane}" >/dev/null 2>&1 || true
}

if [[ -t 0 ]]; then
  printf '❯_ '
fi

cmd=""
if ! IFS= read -r cmd; then
  _close_self
  exit 0
fi

cmd="${cmd#"${cmd%%[![:space:]]*}"}"
cmd="${cmd%"${cmd##*[![:space:]]}"}"
if [[ -z "${cmd}" ]]; then
  _close_self
  exit 0
fi

set +e
"${SHELL:-/bin/zsh}" -l -c "${cmd}"
status=$?
set -e
_close_self
exit "${status}"
