# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_shell_quote() {
  local value="${1-}"
  # printf '%q' can emit invalid UTF-8 byte sequences for multibyte input.
  "$(_vetcoders_internal_python)" - "$value" <<'PY'
import shlex
import sys

print(shlex.quote(sys.argv[1]), end="")
PY
}

_vetcoders_shell_quote_join() {
  local quoted=()
  local arg
  for arg in "$@"; do
    quoted+=("$(_vetcoders_shell_quote "$arg")")
  done
  printf '%s' "${quoted[*]}"
}

_vetcoders_write_command_script() {
  local script_path="$1"
  local command_text="$2"
  local shell_bin

  if command -v zsh >/dev/null 2>&1; then
    shell_bin="$(command -v zsh)"
  else
    shell_bin="$(command -v bash)"
  fi

  # Keep the temp script stable on disk: vc_frame can re-run or resurrect panes
  # against the original command path, so self-deleting wrappers break attach
  # and respawn semantics.
  mkdir -p "$(dirname "$script_path")"
  # shellcheck disable=SC2016
  printf '#!/usr/bin/env bash\nset -euo pipefail\n%s -lc %s\n' \
    "$(_vetcoders_shell_quote "$shell_bin")" \
    "$(_vetcoders_shell_quote "$command_text")" \
    > "$script_path"
  chmod +x "$script_path"
}

