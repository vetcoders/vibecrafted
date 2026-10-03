#!/usr/bin/env bash
set -euo pipefail

runtime_root="${VIBECRAFTED_RUNTIME_ROOT:-/opt/vibecrafted-runtime}"
# Loctree, AICX, PRView and ScreenScribe come from their own channels, not
# from the Runtime Pack (no linux-arm64 npm/GitHub artifacts are published yet).
for binary in vibecrafted vc-server vc-frame vc-terminal voc; do
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
