#!/usr/bin/env bash
# vc-quick-cmd.sh — the ❯_ Quick cmd shell behind the compact-bar chip
#
# One shot unless this pane is pinned. Quick cmd is where short, often
# emergency commands are typed (decyzja Macieja 2026-10-05), so the loop is a
# real interactive zsh speaking the SAME completion and syntax-highlighting
# base as the product shell — not a bash `read -e` imitation that can only
# complete filenames. The pane sets VC_QUICK_CMD=1 and the product profile's
# Quick lane then skips the tool chrome: no Atuin daemon, no Starship prompt,
# no zoxide hooks; built-in Up/Down recall and Ctrl+R incremental search stay.
#
# The one-shot rule lives in zsh hooks: preexec arms after a real command,
# precmd prints [exit N] on failure, probes pin state and closes an unpinned
# pane by id; `exit` and Ctrl-D close through zshexit. A completion or
# autosuggestion helper inherits that hook. Only the original interactive
# process may close the pane.
# Ctrl-C stops only the running command (or clears an empty prompt); it does
# not kill this shell.
#
# Never `close-pane` without `--pane-id`. A bare close-pane follows focus and
# would kill whichever pane holds it — including a durable Agent the command
# just spawned in another tab. Missing pane-id: leave without guessing.
# Pin state is `vc-frame action list-panes --json --state`: the entry whose
# id matches this pane, field `is_pinned`. A repeated id prefers the terminal
# pane over a plugin. Missing field, CLI, parser, or pane-id means unpinned.
set -euo pipefail

export VIBECRAFTED_QUIET_START=1
export VC_QUICK_CMD=1

_pane="${VC_FRAME_PANE_ID:-${ZELLIJ_PANE_ID:-}}"
export VC_QUICK_PANE_ID="${_pane}"

# Whole lines, exactly as the Founder wrote the first one. The second line
# used to promise "Ctrl+N anytime", which does not open a tab — the truthful
# affordance is the [+] button in the tab bar (zgłoszenie Macieja 2026-10-05).
printf '%s\n' \
  'This is one shot ephemeral shell unless you PIN ● it. Type command and forget.' \
  'You can open a real shell by pressing [+] in the tab bar anytime.'

# Locate and source canonical Vetcoders core authority for runtime and
# interpreter resolution. Sourcing core.sh establishes _vetcoders_owned_core_dir
# and _vetcoders_owned_python_bin with installed-generation failclosed policy.
_vc_quick_core_sh=""
_vc_quick_is_installed_gen=0
if [[ -n "${VIBECRAFTED_RUNTIME_ROOT:-}" ]]; then
  _vc_quick_selected_root="${VIBECRAFTED_RUNTIME_ROOT%/}"
  if [[ "$(basename "$(dirname "$_vc_quick_selected_root")")" == "releases" || -f "$_vc_quick_selected_root/runtime-manifest.json" ]]; then
    _vc_quick_is_installed_gen=1
  fi
  _vc_quick_expected_core_sh="${_vc_quick_selected_root}/vibecrafted-core/vibecrafted_core/runtime/shell/lib/core.sh"
  if [[ -r "$_vc_quick_expected_core_sh" ]]; then
    _vc_quick_core_sh="$_vc_quick_expected_core_sh"
    if (( _vc_quick_is_installed_gen )); then
      # Bind physical core directory to the selected installed generation. Stale
      # or foreign ambient VIBECRAFTED_CORE_DIR must not redirect import authority.
      export VIBECRAFTED_CORE_DIR="${_vc_quick_selected_root}/vibecrafted-core"
      # Ensure conflicting ambient VIBECRAFTED_PYTHON does not outrank the owned
      # generation interpreter or expand authority.
      if [[ -x "${_vc_quick_selected_root}/bin/python3" ]]; then
        export VIBECRAFTED_PYTHON="${_vc_quick_selected_root}/bin/python3"
      else
        unset VIBECRAFTED_PYTHON
      fi
    fi
  elif (( _vc_quick_is_installed_gen )); then
    printf 'vc-quick-cmd: selected runtime core is missing: %s\n' "$_vc_quick_expected_core_sh" >&2
    unset VIBECRAFTED_PYTHON
  fi
  unset _vc_quick_selected_root _vc_quick_expected_core_sh
fi

if [[ -z "$_vc_quick_core_sh" ]] && (( ! _vc_quick_is_installed_gen )); then
  _vc_quick_tools_home="${VIBECRAFTED_TOOLS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools}"
  for _c in \
    "${VIBECRAFTED_ROOT:+${VIBECRAFTED_ROOT}/vibecrafted-core/vibecrafted_core/runtime/shell/lib/core.sh}" \
    "${VIBECRAFTED_CORE_DIR:+${VIBECRAFTED_CORE_DIR}/vibecrafted_core/runtime/shell/lib/core.sh}" \
    "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../runtime/shell/lib" 2>/dev/null && pwd)/core.sh" \
    "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../shell/lib" 2>/dev/null && pwd)/core.sh" \
    "${_vc_quick_tools_home}/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/shell/lib/core.sh"; do
    if [[ -n "$_c" && -r "$_c" ]]; then
      _vc_quick_core_sh="$_c"
      break
    fi
  done
  unset _vc_quick_tools_home
fi
unset _vc_quick_is_installed_gen

# Quick cmd profile selection: default is "product". An explicit preference
# in ~/.config/vibecrafted/config.toml under [terminal] quick_cmd_profile='personal'
# loads the user's ~/.zshrc via personal-shell once before hooks become active.
# Unknown or malformed settings are reported honestly to stderr and fall back safely.
_vc_quick_saved_runtime_root="${VIBECRAFTED_RUNTIME_ROOT-}"
_vc_quick_saved_runtime_root_set="${VIBECRAFTED_RUNTIME_ROOT+1}"

_vc_quick_profile_preference="product"
_vc_quick_owned_py=""
# Sourcing core.sh resolves the canonical Vetcoders core and python interpreter.
# shellcheck disable=SC1090
if [[ -n "$_vc_quick_core_sh" ]] && source "$_vc_quick_core_sh" 2>/dev/null; then
  _py="$(_vetcoders_owned_python_bin 2>/dev/null || true)"
  _core_dir="$(_vetcoders_owned_core_dir 2>/dev/null || true)"
  if [[ ! -d "${_core_dir:-}/vibecrafted_core" && -n "${_vetcoders_loaded_core_dir:-}" && -d "${_vetcoders_loaded_core_dir}/vibecrafted_core" ]]; then
    _core_dir="$_vetcoders_loaded_core_dir"
  fi
  if [[ -n "$_py" && -x "$_py" ]]; then
    _vc_quick_owned_py="$_py"
  fi
  if [[ -n "$_py" && -x "$_py" && -n "$_core_dir" && -d "$_core_dir" ]]; then
    _pref_err=""
    if command -v mktemp >/dev/null 2>&1; then
      _pref_err="$(mktemp "${TMPDIR:-/tmp}/vc_quick_pref.XXXXXX" 2>/dev/null || true)"
    fi
    _pref_out=""
    _pref_rc=0
    if [[ -n "$_pref_err" ]]; then
      _pref_out="$("$_py" -I -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); from vibecrafted_core.server_config import _main_cli; raise SystemExit(_main_cli(sys.argv[1:]))' "$_core_dir" quick-cmd-profile 2>"$_pref_err")" || _pref_rc=$?
    else
      _pref_out="$("$_py" -I -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); from vibecrafted_core.server_config import _main_cli; raise SystemExit(_main_cli(sys.argv[1:]))' "$_core_dir" quick-cmd-profile 2>/dev/null)" || _pref_rc=$?
    fi
    if (( _pref_rc == 0 )) && [[ -n "$_pref_out" ]]; then
      _vc_quick_profile_preference="$_pref_out"
    elif (( _pref_rc != 0 )) && [[ -n "$_pref_err" && -s "$_pref_err" ]]; then
      cat "$_pref_err" >&2
    fi
    if [[ -n "$_pref_err" && -f "$_pref_err" ]]; then
      rm -f "$_pref_err"
    fi
    unset _pref_err _pref_out _pref_rc
  fi
  unset _py _core_dir
fi
unset _vc_quick_core_sh _c

if [[ -n "${_vc_quick_owned_py:-}" ]]; then
  export _VC_QUICK_PYTHON="${_vc_quick_owned_py}"
  export VIBECRAFTED_PYTHON="${_vc_quick_owned_py}"
else
  unset _VC_QUICK_PYTHON VIBECRAFTED_PYTHON
fi
unset _vc_quick_owned_py

if [[ -n "${_vc_quick_saved_runtime_root_set:-}" ]]; then
  export VIBECRAFTED_RUNTIME_ROOT="${_vc_quick_saved_runtime_root}"
else
  unset VIBECRAFTED_RUNTIME_ROOT
fi
unset _vc_quick_saved_runtime_root _vc_quick_saved_runtime_root_set

export _VC_QUICK_CMD_PROFILE="${_vc_quick_profile_preference}"

# The Quick lane ZDOTDIR is product state, regenerated on every launch so an
# updated runtime never leaves a stale hook behind.
_zdot="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/quick-cmd"
mkdir -p "${_zdot}"
cat > "${_zdot}/.zshrc" <<'ZSHRC'
# Generated by vc-quick-cmd.sh on every launch. Do not edit.

# Same base as the product terminal: completions, autosuggestions, syntax
# highlighting, aliases. The VC_QUICK_CMD lane inside the profile skips
# Atuin/Starship/zoxide init for this pane.
_vc_quick_profile="$HOME/.config/vibecrafted/vc-terminal/interactive.zsh"
[[ ! -r "${_vc_quick_profile}" ]] || source "${_vc_quick_profile}"

# Load personal home profile if selected via [terminal] quick_cmd_profile='personal'.
# Loaded exactly once before Quick cmd hooks and prompt become active.
if [[ "${_VC_QUICK_CMD_PROFILE:-product}" == "personal" ]] && [[ -z "${_VC_TERMINAL_PERSONAL_SHELL:-}" ]]; then
  if (( $+functions[personal-shell] )); then
    personal-shell
  fi
fi

# Quick cmd identity prompt; the profile's offline fallback is for full panes.
PROMPT='❯_ '
RPROMPT=''

# Prints "pinned" or "unpinned". Never fails the caller.
_vc_quick_pin_state() {
  if [[ -z "${VC_QUICK_PANE_ID:-}" ]]; then
    print -r -- unpinned
    return 0
  fi
  if ! command -v vc-frame >/dev/null 2>&1 || [[ -z "${_VC_QUICK_PYTHON:-}" || ! -x "${_VC_QUICK_PYTHON}" ]]; then
    print -r -- unpinned
    return 0
  fi
  local raw
  if ! raw="$(vc-frame action list-panes --json --state 2>/dev/null)"; then
    print -r -- unpinned
    return 0
  fi
  # The heredoc is python's program (stdin). The JSON rides in argv so it
  # is not swallowed by that redirection.
  "${_VC_QUICK_PYTHON}" -I - "${raw}" <<'PY'
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

# Closed exactly once, and only by the original interactive process. The
# one-shot path closes explicitly (zsh on Linux does not reliably run
# zshexit for an `exit` issued inside a precmd hook), and the root zshexit
# then finds the pane already released. A helper inherits that hook.
# A parenthesized subshell has ZSH_SUBSHELL > 0. A root-started zpty helper
# does not: ZSH_SUBSHELL stays 0 and $$ is that child, not the pane. $$
# inside an ordinary subshell is still the parent, so it cannot name the
# owner either. zsh/system sysparams[pid] is this process in both cases.
# Capture it once when this invocation's zsh starts. The wrapper clears any
# inherited value before exec, so a new Quick cmd records its own pid. A
# helper that later sources this file keeps the exported value instead of
# adopting its own pid. Either mismatch returns before the closed flag or
# close-pane.
zmodload zsh/system
if [[ -z ${_vc_quick_owner_pid:-} ]]; then
  typeset -g _vc_quick_owner_pid=$sysparams[pid]
fi
export _vc_quick_owner_pid
typeset -g _vc_quick_closed=0
_vc_quick_close_self() {
  if (( ZSH_SUBSHELL )); then
    return 0
  fi
  if [[ ${sysparams[pid]} != ${_vc_quick_owner_pid} ]]; then
    return 0
  fi
  if [[ -z "${VC_QUICK_PANE_ID:-}" ]]; then
    return 0
  fi
  (( _vc_quick_closed )) && return 0
  _vc_quick_closed=1
  command -v vc-frame >/dev/null 2>&1 || return 0
  vc-frame action close-pane --pane-id "${VC_QUICK_PANE_ID}" >/dev/null 2>&1 || true
}

# Ctrl-C clears the prompt line or stops the running child; it must never
# kill this shell (also when stdin is not a tty, where zsh's default would).
TRAPINT() { return $(( 128 + $1 )) }

# One-shot rule. preexec arms only for an accepted, non-empty command line,
# so Ctrl-C or Enter at an empty prompt never counts as the one shot.
typeset -g _vc_quick_ran=0
_vc_quick_preexec() { _vc_quick_ran=1 }
_vc_quick_precmd() {
  # `status` is zsh's read-only alias of $?; capture under our own name.
  local _vc_quick_last=$?
  (( _vc_quick_ran )) || return 0
  _vc_quick_ran=0
  if (( _vc_quick_last != 0 )); then
    printf '[exit %d]\n' "${_vc_quick_last}"
  fi
  local state
  state="$(_vc_quick_pin_state)" || state=unpinned
  if [[ "${state}" != pinned ]]; then
    _vc_quick_close_self
    exit 0
  fi
}
autoload -Uz add-zsh-hook
add-zsh-hook preexec _vc_quick_preexec
add-zsh-hook precmd _vc_quick_precmd

# Original process only — one-shot close, typed `exit`, Ctrl-D. Helper
# exits hit the same function and return at the ownership gate.
zshexit() { _vc_quick_close_self }
ZSHRC

# A zsh started from this lane (`zsh -c`, `zsh -lc`) reads the same ZDOTDIR.
# The login stages are the generation's zsh-product files: they compose the
# user's own login startup (global baseline, then the user's files) and put
# the pinned generation's python door back in front. A degraded generation
# without them keeps the pin-only stage.
for _stage in .zshenv .zprofile .zlogin; do
  _stage_source="${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/zsh-product/${_stage}"
  if [[ -n "${VIBECRAFTED_RUNTIME_ROOT:-}" && -r "$_stage_source" ]]; then
    cat "$_stage_source" > "${_zdot}/${_stage}"
  else
    cat > "${_zdot}/${_stage}" <<'ZSTAGE'
# Generated by vc-quick-cmd.sh on every launch. Do not edit.
[[ ! -r "${VIBECRAFTED_RUNTIME_ROOT:-}/config/runtime-pin/pin.zsh" ]] \
  || builtin source "${VIBECRAFTED_RUNTIME_ROOT}/config/runtime-pin/pin.zsh"
ZSTAGE
  fi
done
unset _stage _stage_source

# -d skips distribution /etc zsh files (same decision as launch-primary-shell):
# a global compinit may prompt about insecure host completions and consume the
# first keystrokes of the ephemeral lane. ZDOTDIR startup still loads.
# Drop a parent invocation's owner pid here. This exec is the new owner.
# The generated rc exports the pid it captures, and a helper that sources
# that rc keeps it.
unset _vc_quick_owner_pid
ZDOTDIR="${_zdot}" exec zsh -d -l -i
