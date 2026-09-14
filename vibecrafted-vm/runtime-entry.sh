#!/usr/bin/env bash
set -euo pipefail

runtime_root="${VIBECRAFTED_RUNTIME_ROOT:-/opt/vibecrafted-runtime}"
for binary in vibecrafted vc-server loct loctree loctree-mcp loctree-lsp \
  aicx aicx-mcp prview screenscribe vc-frame vc-terminal voc; do
  [[ -x "$runtime_root/bin/$binary" ]] || {
    printf 'Runtime Pack inventory failure: %s is missing or not executable\n' "$binary" >&2
    exit 70
  }
done

[[ "$(id -u)" != 0 ]] || {
  printf 'Runtime Pack refuses to run providers as root\n' >&2
  exit 70
}

exec "$@"
