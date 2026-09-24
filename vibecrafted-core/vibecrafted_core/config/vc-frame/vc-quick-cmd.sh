#!/usr/bin/env bash
# vc-quick-cmd.sh — the ❯_ Quick cmd shell behind the compact-bar chip
#
# Spec 1.2 §C: a clean prompt, no banner. Help stays on request
# (`vibecrafted --help`). Each command runs in a login shell so PATH/product
# init load; its output stays on screen and the prompt comes back for the next
# one. The pane closes itself only when the operator leaves (`exit` or
# Ctrl-D). "One Quick cmd, not many" is the compact-bar's job: it focuses the
# existing pane instead of opening another, and the Panels list reaches it.
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

# Ctrl-C reaches the whole foreground group. The running command still gets
# the default SIGINT (a trap is not inherited by children); this shell only
# notes it, so the pane and its output survive for the next command.
trap ':' INT

while :; do
  if [[ -t 0 ]]; then
    printf '❯_ '
  fi
  cmd=""
  if IFS= read -r cmd; then
    :
  else
    # >128: Ctrl-C at the prompt clears the line; anything else is EOF.
    read_status=$?
    if (( read_status > 128 )); then
      printf '\n'
      continue
    fi
    break
  fi
  cmd="${cmd#"${cmd%%[![:space:]]*}"}"
  cmd="${cmd%"${cmd##*[![:space:]]}"}"
  if [[ -z "${cmd}" ]]; then
    continue
  fi
  if [[ "${cmd}" == "exit" ]]; then
    break
  fi
  set +e
  "${SHELL:-/bin/zsh}" -l -c "${cmd}"
  status=$?
  set -e
  if (( status != 0 )); then
    printf '[exit %d]\n' "${status}"
  fi
done
_close_self
exit 0
