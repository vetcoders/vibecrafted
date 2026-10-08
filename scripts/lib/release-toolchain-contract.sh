#!/bin/sh

# Single source of truth for local and hosted macOS release toolchains. Keep the
# Rust compiler and its cross targets separate from Xcode: Swift, codesign and
# notarytool follow DEVELOPER_DIR, while native Rust links with one of two
# measured linker pairs:
#
# - classic: the CLT 26 clang/ld-classic pair. Apple ld64 1230.1 and 27037.1
#   assert in makeSymbolStringInPlace on the Vibecrafted Server entry object;
#   ld-classic 956.6 links the captured input (bc25aa6e, measured on div0).
# - xcode: Command Line Tools 27 ship no ld-classic at all, so a host on CLT 27
#   links with the selected Xcode's own clang/ld pair instead. MEASURED
#   2026-09-25 on dragon (Xcode 27 beta: clang-2100.3.27.1 + ld-27036.1): the
#   assertion is a symbol-name limit. Rust 1.96 mangles crate symbols legacy
#   style and spells the whole Leptos view type into drop glue (111k-character
#   names); ld-27036.1 asserts on them exactly as 1230.1/27037.1 did. With v0
#   mangling, which the release applies to the server build, the pair links
#   vibecrafted-server-web. RE-MEASURED 2026-09-29 on dragon after Apple
#   promoted Xcode 27 to stable (27A266a): the stable pair clang-2100.3.34.2 +
#   ld-27037.1 links vibecrafted-server-web with v0 mangling (build-server-release
#   leg, DEVELOPER_DIR=/Applications/Xcode.app, PROBE-LINK-OK in 2m56s), so the
#   pin moves to the stable pair and the beta channel stays refused.
#
# A present ld-classic of another version is drift and stops the release; only
# an absent ld-classic selects the xcode pair, and that pair is version-exact.
# The explicit hosted-macos15-xcode26.3 profile never falls back: the selected
# Xcode classic pair links native Rust and the same Xcode builds Swift/signs.
# Unknown profiles and unmeasured versions are refused before provisioning.
# This sourced helper publishes a caller-visible result or status; its consumer lives outside this
# file, so deleting the binding would break the shared helper contract.
# shellcheck disable=SC2034
readonly VIBECRAFTED_RELEASE_RUSTUP_TOOLCHAIN='1.96.0'
readonly VIBECRAFTED_RELEASE_RUST_TARGETS='wasm32-wasip1 wasm32-unknown-unknown'
readonly VIBECRAFTED_RELEASE_DARWIN_CLANG='/Library/Developer/CommandLineTools/usr/bin/clang'
readonly VIBECRAFTED_RELEASE_DARWIN_CLANG_VERSION='Apple clang version 17.0.0 (clang-1700.6.3.2)'
readonly VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC='/Library/Developer/CommandLineTools/usr/bin/ld-classic'
readonly VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC_VERSION='@(#)PROGRAM:ld-classic  PROJECT:ld64-956.6'
readonly VIBECRAFTED_RELEASE_DARWIN_XCODE_CLANG_VERSION='Apple clang version 21.0.0 (clang-2100.3.34.2)'
readonly VIBECRAFTED_RELEASE_DARWIN_XCODE_LD_VERSION='@(#)PROGRAM:ld PROJECT:ld-27037.1'

readonly VIBECRAFTED_RELEASE_DARWIN_HOSTED_CLANG_VERSION='Apple clang version 17.0.0 (clang-1700.6.4.2)'
# Measured 2026-10-08 in hosted probe run 37802780433, image 20260907.0337.1.
# Xcode 26.3 uses the classic linker version already proven on local server input;
# CLT clang-1700.0.13.5 / ld-classic-955.13 is observed but not admitted.
readonly VIBECRAFTED_RELEASE_DARWIN_HOSTED_LD_CLASSIC_VERSION='@(#)PROGRAM:ld-classic  PROJECT:ld64-956.6'
readonly VIBECRAFTED_RELEASE_HOSTED_DEVELOPER_DIR='/Applications/Xcode_26.3.0.app/Contents/Developer'
readonly VIBECRAFTED_RELEASE_HOSTED_XCODE_VERSION='Xcode 26.3
Build version 17C529'

vibecrafted_release_verify_darwin_linker() {
  # Sets VIBECRAFTED_RELEASE_DARWIN_LINKER_MODE (classic|xcode) and the clang/ld
  # the Rust linker shim must use: VIBECRAFTED_RELEASE_DARWIN_RUST_CLANG and
  # VIBECRAFTED_RELEASE_DARWIN_RUST_LD.
  VIBECRAFTED_RELEASE_TOOLCHAIN_PROFILE="${VIBECRAFTED_RELEASE_TOOLCHAIN_PROFILE:-local}"
  export VIBECRAFTED_RELEASE_TOOLCHAIN_PROFILE
  case "$VIBECRAFTED_RELEASE_TOOLCHAIN_PROFILE" in
    local)
      if [ ! -e "$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC" ]; then
        vibecrafted_release_verify_xcode_linker
        return
      fi
      ;;
    local-classic) ;;
    local-xcode27)
      vibecrafted_release_verify_xcode_linker
      return
      ;;
    hosted-macos15-xcode26.3)
      vibecrafted_release_verify_hosted_xcode
      return
      ;;
    *)
      printf 'FATAL: unknown release toolchain profile [%s]; supported: local, local-classic, local-xcode27, hosted-macos15-xcode26.3\n' \
        "$VIBECRAFTED_RELEASE_TOOLCHAIN_PROFILE" >&2
      return 1
      ;;
  esac
  vibecrafted_release_verify_classic_linker \
    "$VIBECRAFTED_RELEASE_DARWIN_CLANG_VERSION" \
    "$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC_VERSION"
}

vibecrafted_release_verify_hosted_xcode() {
  [ "${DEVELOPER_DIR:-}" = "$VIBECRAFTED_RELEASE_HOSTED_DEVELOPER_DIR" ] || {
    printf 'FATAL: hosted release Xcode path drift: expected [%s], got [%s]\n' \
      "$VIBECRAFTED_RELEASE_HOSTED_DEVELOPER_DIR" "${DEVELOPER_DIR:-unset}" >&2
    return 1
  }
  actual_xcode="$(xcodebuild -version 2>&1)" || {
    printf 'FATAL: hosted release Xcode cannot run: %s\n' "$actual_xcode" >&2
    return 1
  }
  [ "$actual_xcode" = "$VIBECRAFTED_RELEASE_HOSTED_XCODE_VERSION" ] || {
    printf 'FATAL: hosted release Xcode version drift: expected [%s], got [%s]\n' \
      "$VIBECRAFTED_RELEASE_HOSTED_XCODE_VERSION" "$actual_xcode" >&2
    return 1
  }
  # The runner's .0 bundle is an alias. Bind the resolved xcrun paths to the
  # selected bundle, not merely to versions that another toolchain could share.
  hosted_developer_real="$(cd "$DEVELOPER_DIR" && pwd -P)" || return 1
  hosted_bin="$hosted_developer_real/Toolchains/XcodeDefault.xctoolchain/usr/bin"
  hosted_clang="$(xcrun --find clang 2>/dev/null || true)"
  hosted_ld="$(xcrun --find ld-classic 2>/dev/null || true)"
  hosted_clang_real="$(cd "$(dirname "$hosted_clang")" 2>/dev/null && pwd -P)/$(basename "$hosted_clang")"
  hosted_ld_real="$(cd "$(dirname "$hosted_ld")" 2>/dev/null && pwd -P)/$(basename "$hosted_ld")"
  if [ "$hosted_clang_real" != "$hosted_bin/clang" ] \
    || [ "$hosted_ld_real" != "$hosted_bin/ld-classic" ]; then
    printf 'FATAL: hosted release tool resolution drift: expected clang/ld-classic in [%s], got [%s] and [%s]\n' \
      "$hosted_bin" "$hosted_clang" "$hosted_ld" >&2
    return 1
  fi
  vibecrafted_release_verify_classic_linker \
    "$VIBECRAFTED_RELEASE_DARWIN_HOSTED_CLANG_VERSION" \
    "$VIBECRAFTED_RELEASE_DARWIN_HOSTED_LD_CLASSIC_VERSION" \
    "$hosted_clang_real" "$hosted_ld_real"
}

vibecrafted_release_verify_classic_linker() {
  expected_clang="$1"
  expected_ld="$2"
  release_clang="${3:-$VIBECRAFTED_RELEASE_DARWIN_CLANG}"
  release_ld="${4:-$VIBECRAFTED_RELEASE_DARWIN_LD_CLASSIC}"
  [ -x "$release_clang" ] || {
    printf 'FATAL: pinned release clang is missing: %s\n' \
      "$release_clang" >&2
    return 1
  }
  [ -x "$release_ld" ] || {
    printf 'FATAL: pinned release ld-classic is not executable: %s\n' \
      "$release_ld" >&2
    return 1
  }

  actual_clang="$("$release_clang" --version | sed -n '1p')"
  actual_ld="$("$release_ld" -v </dev/null 2>&1 | sed -n '1p')"
  [ "$actual_clang" = "$expected_clang" ] || {
    printf 'FATAL: release clang drift (profile=%s, path=%s): expected [%s], got [%s]\n' \
      "$VIBECRAFTED_RELEASE_TOOLCHAIN_PROFILE" "$release_clang" \
      "$expected_clang" "$actual_clang" >&2
    return 1
  }
  [ "$actual_ld" = "$expected_ld" ] || {
    printf 'FATAL: release ld-classic drift (profile=%s, path=%s): expected [%s], got [%s]\n' \
      "$VIBECRAFTED_RELEASE_TOOLCHAIN_PROFILE" "$release_ld" \
      "$expected_ld" "$actual_ld" >&2
    return 1
  }
  VIBECRAFTED_RELEASE_DARWIN_LINKER_MODE=classic
  VIBECRAFTED_RELEASE_DARWIN_RUST_CLANG="$release_clang"
  VIBECRAFTED_RELEASE_DARWIN_RUST_LD="$release_ld"
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
