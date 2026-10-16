#!/usr/bin/env bash
set -euo pipefail

export VIBECRAFTED_OPERATOR_MODE=1

shell_bin="${SHELL:-}"
if [[ -z "$shell_bin" || ! -x "$shell_bin" ]]; then
  if command -v zsh >/dev/null 2>&1; then
    shell_bin="$(command -v zsh)"
  else
    shell_bin="$(command -v bash)"
  fi
fi

printf 'Vibecrafted Operator\n'
printf '\n'
printf 'Session\n'
printf '  entrypoint: vc-start\n'
printf '  agent tabs: new runs open in this session and stay visible in the tab bar\n'
printf '\n'
printf 'Launchpad\n'
printf '  vibecrafted workflow codex --prompt "..."\n'
printf '  vibecrafted implement codex --file path/to/brief.md\n'
printf '  vibecrafted research --prompt "..."\n'
printf '\n'
printf 'Watch runs\n'
printf '  vibecrafted observe codex --run-id <run_id>\n'
printf '  vibecrafted await codex --run-id <run_id>\n'
printf '\n'
printf 'Artifacts\n'
printf '  ~/.vibecrafted/artifacts\n'
printf '  explorer pane opens beside this shell when the terminal is wide enough\n'
printf '\n'
printf 'Leaving\n'
printf '  close terminal: detach\n'
printf '  Ctrl+q: quit intentionally\n'
printf '\n'

# restore-orphaned path retired 2026-04-22 — it reanimated zombie runs without
# PID validation and burned the laptop. Dead sessions stay as evidence until the
# operator explicitly runs `vibecrafted dashboard gc [--apply]`.

exec "$shell_bin" -l
