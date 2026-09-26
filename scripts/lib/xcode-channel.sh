#!/bin/sh
# Active Xcode channel. One question: is the selected developer dir beta or stable?
# Callers decide nothing about what is being built.

vibecrafted_xcode_require_stable() {
  if [ "$(uname -s)" != Darwin ]; then
    return 0
  fi
  dir="${DEVELOPER_DIR:-}"
  if [ -z "$dir" ]; then
    dir="$(xcode-select -p 2>/dev/null || true)"
  fi
  if [ -z "$dir" ] || [ ! -d "$dir" ]; then
    printf 'FATAL: no usable Xcode developer dir (xcode-select -p / DEVELOPER_DIR)\n' >&2
    return 1
  fi
  channel=stable
  case "$dir" in
    *[Bb]eta*) channel=beta ;;
  esac
  printf 'Xcode: %s (%s)\n' "$channel" "$dir"
  if [ "$channel" = beta ] && [ -z "${VIBECRAFTED_ALLOW_BETA_XCODE:-}" ]; then
    stable=/Applications/Xcode.app/Contents/Developer
    if [ -d "$stable" ]; then
      printf 'stable is %s\n' "$stable" >&2
    fi
    printf 'FATAL: refusing beta Xcode\n' >&2
    return 1
  fi
  export DEVELOPER_DIR="$dir"
}
