#!/usr/bin/env bash
# Runtime roots — the one shell definition of where Vibecrafted lives.
#
# Python owns this contract in vibecrafted_core.runtime_paths; this file is the
# shell reading of the same env grammar and nothing else:
#
#   VIBECRAFTED_HOME          control-plane root        default ~/.vibecrafted
#   VIBECRAFTED_RUNTIME_HOME  runtime generations       default $XDG_DATA_HOME/vibecrafted
#                                                       (~/.local/share/vibecrafted)
#   VIBECRAFTED_TOOLS_HOME    staged installs           default <runtime_home>/tools
#   VIBECRAFTED_LAUNCHER_BIN  launcher symlinks/scripts default ~/.local/bin
#
# VIBECRAFTED_ROOT is the *release generation* root exported by every launcher.
# It is never a home and never a prefix for one.
#
# install.sh is the curl|bash bootstrap and cannot source this file before it
# has a checkout, so it carries a verbatim copy between the markers
# `# >>> scripts/lib/runtime-roots.sh` / `# <<< scripts/lib/runtime-roots.sh`;
# tests/tui/test_runtime_roots_parity.py pins that copy to this file.

is_interactive_session() {
  [[ -t 0 && -t 1 ]]
}

default_vibecrafted_home() {
  if [[ -n "${VIBECRAFTED_HOME:-}" ]]; then
    printf '%s\n' "$VIBECRAFTED_HOME"
    return
  fi
  printf '%s\n' "$HOME/.vibecrafted"
}

default_vibecrafted_runtime_home() {
  if [[ -n "${VIBECRAFTED_RUNTIME_HOME:-}" ]]; then
    printf '%s\n' "$VIBECRAFTED_RUNTIME_HOME"
    return
  fi
  if [[ -n "${XDG_DATA_HOME:-}" ]]; then
    printf '%s\n' "$XDG_DATA_HOME/vibecrafted"
    return
  fi
  printf '%s\n' "$HOME/.local/share/vibecrafted"
}

default_vibecrafted_tools_home() {
  if [[ -n "${VIBECRAFTED_TOOLS_HOME:-}" ]]; then
    printf '%s\n' "$VIBECRAFTED_TOOLS_HOME"
    return
  fi
  printf '%s/tools\n' "$(default_vibecrafted_runtime_home)"
}

default_vibecrafted_launcher_bin() {
  if [[ -n "${VIBECRAFTED_LAUNCHER_BIN:-}" ]]; then
    printf '%s\n' "$VIBECRAFTED_LAUNCHER_BIN"
    return
  fi
  printf '%s\n' "$HOME/.local/bin"
}

canonical_vibecrafted_home() {
  printf '%s\n' "$HOME/.vibecrafted"
}

canonical_vibecrafted_runtime_home() {
  printf '%s\n' "$HOME/.local/share/vibecrafted"
}

canonical_vibecrafted_launcher_bin() {
  printf '%s\n' "$HOME/.local/bin"
}

pause_runtime_contract_failure() {
  printf '  → fix: vibecrafted doctor --fix-legacy-bootstrap --fix-launchers\n' >&2
  if [[ "${VIBECRAFTED_INSTALL_NONINTERACTIVE:-0}" == "1" ]] || ! is_interactive_session; then
    return
  fi
  printf 'Press Enter to continue, or Ctrl-C to abort: ' >&2
  read -r _ || true
}

enforce_runtime_root_contract() {
  local expected_store expected_runtime expected_launcher
  local resolved_store resolved_runtime resolved_launcher
  local failed=0

  expected_store="$(canonical_vibecrafted_home)"
  expected_runtime="$(canonical_vibecrafted_runtime_home)"
  expected_launcher="$(canonical_vibecrafted_launcher_bin)"

  resolved_store="$(default_vibecrafted_home)"
  resolved_runtime="$(default_vibecrafted_runtime_home)"
  resolved_launcher="$(default_vibecrafted_launcher_bin)"

  if [[ "$resolved_store" != "$expected_store" ]]; then
    printf '✗ store root drift: %s ≠ %s\n' "$resolved_store" "$expected_store" >&2
    failed=1
  fi

  if [[ "$resolved_runtime" != "$expected_runtime" ]]; then
    printf '✗ runtime root drift: %s ≠ %s\n' "$resolved_runtime" "$expected_runtime" >&2
    failed=1
  fi

  if [[ "$resolved_launcher" != "$expected_launcher" ]]; then
    printf '✗ launcher root drift: %s ≠ %s\n' "$resolved_launcher" "$expected_launcher" >&2
    failed=1
  fi

  if [[ "$failed" == "1" ]]; then
    pause_runtime_contract_failure
    return 1
  fi

  return 0
}
