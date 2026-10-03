#!/usr/bin/env bash
# dev-entry — personal-dev container entrypoint wrapper.
#
# Bootstraps the vibecrafted runtime from the mounted /workspace repo when that
# repo is the vibecrafted checkout (idempotent), then hands off to the shared
# entry.sh (optional tailnet + readiness probe + shell). Agent/session state is
# persisted via named volumes, so this only prepares per-boot runtime bits.
set -euo pipefail

export PATH="/usr/local/cargo/bin:/root/.local/bin:/usr/local/bin:${PATH}"

if [ -f /workspace/scripts/vibecrafted ] && [ -f /workspace/pyproject.toml ]; then
  ln -sf /workspace/scripts/vibecrafted /usr/local/bin/vibecrafted 2>/dev/null || true
  if [ "${VC_DEV_SKIP_SYNC:-0}" != "1" ] && [ ! -d /workspace/.venv ]; then
    echo "[dev-entry] first boot: running 'uv sync' in /workspace (set VC_DEV_SKIP_SYNC=1 to skip)…"
    (cd /workspace && uv sync) || echo "[dev-entry] uv sync failed — continuing without it"
  fi
fi

exec /usr/local/bin/entry "$@"
