#!/usr/bin/env bash
set -euo pipefail

# Cargo linker shim for native Apple targets only. The release builder wires
# this through CARGO_TARGET_AARCH64_APPLE_DARWIN_LINKER, so WASM targets never
# receive the Darwin-only flag. Pair clang with ld-classic: the Xcode beta
# clang forwards -ld_classic to the beta ld, which ignores it, while the clang
# next to ld-classic still dispatches the classic linker.
classic_ld="$(xcrun --find ld-classic 2>/dev/null || true)"
[[ -n "$classic_ld" && -x "$classic_ld" ]] || {
  printf '%s\n' 'rust-linker-darwin-classic: selected toolchain has no ld-classic' >&2
  exit 1
}
clang="${VIBECRAFTED_DARWIN_CLANG:-$(dirname "$classic_ld")/clang}"
[[ -n "$clang" && -x "$clang" ]] || {
  printf '%s\n' "rust-linker-darwin-classic: no clang next to $classic_ld" >&2
  exit 1
}

exec "$clang" -Wl,-ld_classic "$@"
