#!/usr/bin/env bash
# Cheap `make check` gate for the deck owner/mirror pair.
#
# vibecrafted-core/vibecrafted_core/deck/vibecrafted is the packaged owner
# (the runtime-generation entrypoint); scripts/vibecrafted is a checkout-facing
# compatibility mirror (see docs/runtime/UNIFIED_LAUNCH_CONTRACT.md §"One
# semantic owner"). 2026-09-30: a worker landed a new verb in the mirror only;
# `make check` stayed green and the verb never reached the product. Nothing
# before this gate compared the two files at `make check` speed — the
# byte-identity assertion in vibecrafted-core/tests/test_cursor_parity.py only
# runs under `make test`, not `make check`.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
owner="$repo_root/vibecrafted-core/vibecrafted_core/deck/vibecrafted"
mirror="$repo_root/scripts/vibecrafted"

for path in "$owner" "$mirror"; do
  [[ -f "$path" ]] || {
    printf 'FATAL: deck-mirror-check cannot find %s\n' "$path" >&2
    exit 1
  }
done

cmp -s "$owner" "$mirror" || {
  printf 'FATAL: scripts/vibecrafted (mirror) has drifted from vibecrafted-core/vibecrafted_core/deck/vibecrafted (owner).\n' >&2
  printf 'Edit the owner, then repair the mirror:\n' >&2
  printf '  cp vibecrafted-core/vibecrafted_core/deck/vibecrafted scripts/vibecrafted\n' >&2
  exit 1
}

printf 'deck mirror OK: scripts/vibecrafted == vibecrafted-core/vibecrafted_core/deck/vibecrafted\n'
