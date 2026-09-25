#!/bin/sh

# Single source of truth for the local macOS release build toolchain. Keep the
# Rust compiler and its cross targets separate from Xcode: Swift, codesign and
# notarytool follow DEVELOPER_DIR, while native Rust links with one of two
# measured linker pairs:
#
# - classic: the CLT 26 clang/ld-classic pair. Apple ld64 1230.1 and 27037.1
#   assert in makeSymbolStringInPlace on the Vibecrafted Server entry object;
#   ld-classic 956.6 links the captured input (bc25aa6e, measured on div0).
# - xcode: Command Line Tools 27 ship no ld-classic at all, so a host on CLT 27
#   links with the selected Xcode's own clang/ld pair instead. MEASURED
#   2026-09-25 on dragon (CLT 27 beta, Xcode 27 beta): `make build-server-release`
#   links vibecrafted-server-web with clang-2100.3.27.1 + ld-27036.1 and the
#   binary answers --version. The CLT 27 ld-27037.1 stays outside the contract.
#
# A present ld-classic of another version is drift and stops the release; only
# an absent ld-classic selects the xcode pair, and that pair is version-exact.
# shellcheck disable=SC2034 # exported contract values are consumed by sourcers
readonly VIBECRAFTED_RELEASE_RUSTUP_TOOLCHAIN='1.96.0'
readonly VIBECRAFTED_RELEASE_RUST_TARGETS='wasm32-wasip1 wasm32-unknown-unknown'
readonly VIBECRAFTED_RELEASE_DARWIN_CLANG='/Library/Developer/CommandLineTools/usr/bin/clang'
readonly VIBECRAFTED_RELEASE_DARWIN_CLANG_VERSION='Apple clang version 17.0.0 (clang-1700.6.3.2)'
readonly VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC='/Library/Developer/CommandLineTools/usr/bin/ld-classic'
readonly VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC_VERSION='@(#)PROGRAM:ld-classic  PROJECT:ld64-956.6'
readonly VIBECRAFTED_RELEASE_DARWIN_XCODE_CLANG_VERSION='Apple clang version 21.0.0 (clang-2100.3.27.1)'
readonly VIBECRAFTED_RELEASE_DARWIN_XCODE_LD_VERSION='@(#)PROGRAM:ld PROJECT:ld-27036.1'

vibecrafted_release_verify_darwin_linker() {
  # Sets VIBECRAFTED_RELEASE_DARWIN_LINKER_MODE (classic|xcode) and the clang/ld
  # the Rust linker shim must use: VIBECRAFTED_RELEASE_DARWIN_RUST_CLANG and
  # VIBECRAFTED_RELEASE_DARWIN_RUST_LD.
  if [ ! -e "$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC" ]; then
    vibecrafted_release_verify_xcode_linker
    return
  fi
  [ -x "$VIBECRAFTED_RELEASE_DARWIN_CLANG" ] || {
    printf 'FATAL: pinned release clang is missing: %s\n' \
      "$VIBECRAFTED_RELEASE_DARWIN_CLANG" >&2
    return 1
  }
  [ -x "$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC" ] || {
    printf 'FATAL: pinned release ld-classic is not executable: %s\n' \
      "$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC" >&2
    return 1
  }

  actual_clang="$("$VIBECRAFTED_RELEASE_DARWIN_CLANG" --version | sed -n '1p')"
  actual_ld="$("$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC" -v </dev/null 2>&1 | sed -n '1p')"
  [ "$actual_clang" = "$VIBECRAFTED_RELEASE_DARWIN_CLANG_VERSION" ] || {
    printf 'FATAL: release clang drift: expected [%s], got [%s]\n' \
      "$VIBECRAFTED_RELEASE_DARWIN_CLANG_VERSION" "$actual_clang" >&2
    return 1
  }
  [ "$actual_ld" = "$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC_VERSION" ] || {
    printf 'FATAL: release ld-classic drift: expected [%s], got [%s]\n' \
      "$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC_VERSION" "$actual_ld" >&2
    return 1
  }
  VIBECRAFTED_RELEASE_DARWIN_LINKER_MODE=classic
  VIBECRAFTED_RELEASE_DARWIN_RUST_CLANG="$VIBECRAFTED_RELEASE_DARWIN_CLANG"
  VIBECRAFTED_RELEASE_DARWIN_RUST_LD="$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC"
}

vibecrafted_release_verify_xcode_linker() {
  # xcrun follows DEVELOPER_DIR, the same Xcode that builds Swift and signs.
  xcode_clang="$(xcrun --find clang 2>/dev/null || true)"
  xcode_ld="$(xcrun --find ld 2>/dev/null || true)"
  if [ -z "$xcode_clang" ] || [ ! -x "$xcode_clang" ] \
    || [ -z "$xcode_ld" ] || [ ! -x "$xcode_ld" ]; then
    printf 'FATAL: no pinned release linker: %s is absent and xcrun resolves no clang/ld (DEVELOPER_DIR=%s)\n' \
      "$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC" "${DEVELOPER_DIR:-unset}" >&2
    return 1
  fi
  actual_clang="$("$xcode_clang" --version | sed -n '1p')"
  actual_ld="$("$xcode_ld" -v </dev/null 2>&1 | sed -n '1p')"
  [ "$actual_clang" = "$VIBECRAFTED_RELEASE_DARWIN_XCODE_CLANG_VERSION" ] || {
    printf 'FATAL: release xcode clang drift: expected [%s], got [%s] from %s\n' \
      "$VIBECRAFTED_RELEASE_DARWIN_XCODE_CLANG_VERSION" "$actual_clang" "$xcode_clang" >&2
    printf '       no ld-classic here; point DEVELOPER_DIR at the measured Xcode\n' >&2
    return 1
  }
  [ "$actual_ld" = "$VIBECRAFTED_RELEASE_DARWIN_XCODE_LD_VERSION" ] || {
    printf 'FATAL: release xcode ld drift: expected [%s], got [%s] from %s\n' \
      "$VIBECRAFTED_RELEASE_DARWIN_XCODE_LD_VERSION" "$actual_ld" "$xcode_ld" >&2
    return 1
  }
  VIBECRAFTED_RELEASE_DARWIN_LINKER_MODE=xcode
  VIBECRAFTED_RELEASE_DARWIN_RUST_CLANG="$xcode_clang"
  VIBECRAFTED_RELEASE_DARWIN_RUST_LD="$xcode_ld"
}
