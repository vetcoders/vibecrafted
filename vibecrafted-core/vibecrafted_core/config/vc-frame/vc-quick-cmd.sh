#!/usr/bin/env bash
# vc-quick-cmd.sh — the ❯_ Quick cmd shell behind the compact-bar chip
#
# One shot unless this pane is pinned. The two-line banner prints once, above
# the first prompt; help stays on request (`vibecrafted --help`). Each command
# runs in a login shell. After it returns, an unpinned pane closes itself by
# pane-id and this script exits. A pinned pane loops. `exit` and Ctrl-D always
# close. Ctrl-C stops only the running command (or an empty read); it does not
# kill this shell. A failing command prints [exit N] and still counts.
#
# Never `close-pane` without `--pane-id`. A bare close-pane follows focus and
# would kill whichever pane holds it — including a durable Agent the command
# just spawned in another tab. Missing pane-id: leave without guessing.
# Pin state is `vc-frame action list-panes --json --state`: the entry whose
# id matches this pane, field `is_pinned`. A repeated id prefers the terminal
# pane over a plugin. Missing field, CLI, parser, or pane-id means unpinned.
set -euo pipefail

export VIBECRAFTED_QUIET_START=1

_pane="${VC_FRAME_PANE_ID:-${ZELLIJ_PANE_ID:-}}"

_banner() {
  printf '%s\n' \
    'This is one shot ephemeral shell unless you PIN ● it. Type command and forget.' \
    'You can open a real shell by pressing [+] in the tab bar or using a Ctrl+N anytime.'
}

_close_self() {
  if [[ -z "${_pane}" ]]; then
    return 0
  fi
  if ! command -v vc-frame >/dev/null 2>&1; then
    return 0
  fi
  vc-frame action close-pane --pane-id "${_pane}" >/dev/null 2>&1 || true
}

# Prints "pinned" or "unpinned". Never fails the caller.
_pin_state() {
  if [[ -z "${_pane}" ]]; then
    printf '%s\n' unpinned
    return 0
  fi
  if ! command -v vc-frame >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' unpinned
    return 0
  fi
  local raw
  if ! raw="$(vc-frame action list-panes --json --state 2>/dev/null)"; then
    printf '%s\n' unpinned
    return 0
  fi
  # The heredoc is python's program (stdin). The JSON rides in argv so it
  # is not swallowed by that redirection.
  VC_QUICK_PANE_ID="${_pane}" python3 - "${raw}" <<'PY'
import json
import os
import sys

target = os.environ.get("VC_QUICK_PANE_ID", "")
raw = sys.argv[1] if len(sys.argv) > 1 else ""
raw = raw.strip()


def pane_id(value):
    if isinstance(value, bool) or value is None:
        return ""
    return str(value)


def pinned_flag(node):
    if "is_pinned" not in node:
        return None
    value = node.get("is_pinned")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False


matches = []


def walk(node):
    if isinstance(node, dict):
        if pane_id(node.get("id")) == target:
            matches.append(node)
        for value in node.values():
            walk(value)
    elif isinstance(node, list):
        for value in node:
            walk(value)


try:
    payload = json.loads(raw) if raw else None
except Exception:
    payload = None
if payload is not None and target:
    walk(payload)

chosen = [node for node in matches if node.get("is_plugin") is not True]
if not chosen:
    chosen = matches
flag = None
for node in chosen:
    state = pinned_flag(node)
    if state is True:
        flag = True
        break
    if state is False:
        flag = False
print("pinned" if flag is True else "unpinned")
PY
}

# Ctrl-C reaches the whole foreground group. The running command still gets
# the default SIGINT (a trap is not inherited by children); this shell only
# notes it, so an unpinned close is the one-shot rule, not the signal.
trap ':' INT

_banner
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
  state="unpinned"
  state="$(_pin_state)" || state="unpinned"
  if [[ "${state}" != "pinned" ]]; then
    _close_self
    exit 0
  fi
done
_close_self
exit 0
