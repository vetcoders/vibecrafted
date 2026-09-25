#!/usr/bin/env bash
set -euo pipefail

# Cargo linker shim for native Apple targets only. The release builder wires
# this through CARGO_TARGET_AARCH64_APPLE_DARWIN_LINKER, so WASM targets never
# receive the Darwin-only flag. The beta Xcode clang forwards -ld_classic to
# beta ld, which ignores it; the pinned CLT pair still dispatches ld-classic.
# Without ld-classic (CLT 27) the contract selects the measured Xcode pair and
# the shim links with that clang unchanged.
contract="${VIBECRAFTED_RELEASE_TOOLCHAIN_CONTRACT:-$(dirname "${BASH_SOURCE[0]}")/release-toolchain-contract.sh}"
# shellcheck disable=SC1090 # runtime-selected contract; release pins its path
. "$contract"
vibecrafted_release_verify_darwin_linker

if [[ "$VIBECRAFTED_RELEASE_DARWIN_LINKER_MODE" == classic ]]; then
  exec "$VIBECRAFTED_RELEASE_DARWIN_CLANG" -Wl,-ld_classic "$@"
fi
exec "$VIBECRAFTED_RELEASE_DARWIN_RUST_CLANG" "$@"
