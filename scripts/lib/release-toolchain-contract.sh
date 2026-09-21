#!/bin/sh

# Single source of truth for the local macOS release build toolchain. Keep the
# Rust compiler and its cross targets separate from Xcode: Swift, codesign and
# notarytool follow DEVELOPER_DIR, while native Rust links with this measured
# CLT clang/ld-classic pair.
# shellcheck disable=SC2034 # exported contract values are consumed by sourcers
readonly VIBECRAFTED_RELEASE_RUSTUP_TOOLCHAIN='1.96.0'
readonly VIBECRAFTED_RELEASE_RUST_TARGETS='wasm32-wasip1 wasm32-unknown-unknown'
readonly VIBECRAFTED_RELEASE_DARWIN_CLANG='/Library/Developer/CommandLineTools/usr/bin/clang'
readonly VIBECRAFTED_RELEASE_DARWIN_CLANG_VERSION='Apple clang version 17.0.0 (clang-1700.6.3.2)'
readonly VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC='/Library/Developer/CommandLineTools/usr/bin/ld-classic'
readonly VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC_VERSION='@(#)PROGRAM:ld-classic  PROJECT:ld64-956.6'

vibecrafted_release_verify_darwin_linker() {
  [ -x "$VIBECRAFTED_RELEASE_DARWIN_CLANG" ] || {
    printf 'FATAL: pinned release clang is missing: %s\n' \
      "$VIBECRAFTED_RELEASE_DARWIN_CLANG" >&2
    return 1
  }
  [ -x "$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC" ] || {
    printf 'FATAL: pinned release ld-classic is missing: %s\n' \
      "$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC" >&2
    return 1
  }

  actual_clang="$($VIBECRAFTED_RELEASE_DARWIN_CLANG --version | head -n 1)"
  actual_ld="$($VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC -v </dev/null 2>&1 | head -n 1)"
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
}
