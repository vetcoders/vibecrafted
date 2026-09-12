#!/usr/bin/env bash
# Plan 03 (META_22) — install.sh cross-platform smoke.
#
# Lightweight, host-only assertions about install.sh:
#
#   - `--help` exits 0 and prints usage banner (no platform side-effects).
#   - `bash -n install.sh` parses clean.
#   - Detection helpers (detect_platform / detect_linux_distro /
#     platform_banner / preflight_pkg_hint) exist and can be sourced in
#     isolation.
#   - On the host running this smoke, detect_platform produces one of the
#     supported values: macos | linux | wsl.
#   - preflight_pkg_hint emits a non-empty hint string for each supported
#     platform/pkg-manager combination.
#
# This smoke does NOT run the full install path. The full path is exercised
# by:
#   - .github/workflows/install-linux.yml  (ubuntu-22.04, ubuntu-24.04,
#     debian-12 container, docker build smoke)
#   - .github/workflows/portable.yml        (ubuntu-latest, macos-latest)
#
# Falsifier: comment out detect_platform inside install.sh and re-run this
# smoke — phase 3 will fail because the helper won't be exported.
#
# Usage:
#   tests/install_smoke.sh

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_SH="$REPO_ROOT/install.sh"

red()    { printf '\033[31m%s\033[0m' "$*"; }
green()  { printf '\033[32m%s\033[0m' "$*"; }
yellow() { printf '\033[33m%s\033[0m' "$*"; }
dim()    { printf '\033[2m%s\033[0m' "$*"; }

PASSES=0
FAILURES=()

ok()   { PASSES=$((PASSES + 1)); printf '  [%s] %s\n' "$(green ok)" "$1"; }
fail() { FAILURES+=("$1"); printf '  [%s] %s\n' "$(red fail)" "$1"; }
phase(){ printf '\n%s\n' "$(dim "─── $1 ───")"; }

# -----------------------------------------------------------------------------
# Phase 1 — install.sh exists and parses
# -----------------------------------------------------------------------------
phase "phase 1: install.sh exists and parses"

if [[ -f "$INSTALL_SH" ]]; then
  ok "install.sh exists at $INSTALL_SH"
else
  fail "install.sh missing at $INSTALL_SH"
  printf '\nFATAL: cannot continue without install.sh\n' >&2
  exit 1
fi

if bash -n "$INSTALL_SH" 2>/dev/null; then
  ok "bash -n install.sh is clean"
else
  fail "bash -n install.sh reported syntax errors"
fi

# -----------------------------------------------------------------------------
# Phase 2 — --help exits 0 with usage banner (no platform side-effects)
# -----------------------------------------------------------------------------
phase "phase 2: --help is side-effect-free"

if bash "$INSTALL_SH" --help >/dev/null 2>&1; then
  ok "install.sh --help exits 0"
else
  fail "install.sh --help did not exit 0"
fi

help_output="$(bash "$INSTALL_SH" --help 2>&1 || true)"

if printf '%s' "$help_output" | grep -q "Usage: install.sh"; then
  ok "install.sh --help prints usage banner"
else
  fail "install.sh --help missing usage banner"
fi

if printf '%s' "$help_output" | grep -q "Platform:"; then
  fail "install.sh --help leaked platform banner (should be side-effect-free)"
else
  ok "install.sh --help is platform-detection-free"
fi

# -----------------------------------------------------------------------------
# Phase 3 — detection helpers are extractable and callable in isolation
# -----------------------------------------------------------------------------
phase "phase 3: detection helpers source cleanly"

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/install-smoke.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

# Pull the platform-detection function block out of install.sh. The block
# starts at the canonical comment header and ends after platform_banner()'s
# closing brace.
awk '
  /^# Platform detection/ { capture=1 }
  capture { print }
  /^platform_banner\(\) \{/ { in_banner=1; next }
  in_banner && /^\}$/ { capture=0; exit }
' "$INSTALL_SH" > "$tmpdir/detection-lib.sh"

# install.sh defines info() before the detection helpers; for our isolation
# probe we need to provide a minimal shim so platform_banner can call info.
{
  printf 'info() { printf "%%s\\n" "$*"; }\n'
  cat "$tmpdir/detection-lib.sh"
} > "$tmpdir/detection-lib-full.sh"

if bash -n "$tmpdir/detection-lib-full.sh" 2>/dev/null; then
  ok "extracted detection-lib parses"
else
  fail "extracted detection-lib failed bash -n"
fi

# Source + invoke
detect_output="$(
  bash -c '
    set -euo pipefail
    # shellcheck disable=SC1091
    source "'"$tmpdir"'/detection-lib-full.sh"
    detect_platform
    detect_linux_distro
    platform_banner
    printf "RESOLVED:%s|%s|%s\n" "$PLATFORM_OS" "$LINUX_DISTRO_ID" "$LINUX_PKG_MGR"
  ' 2>&1
)"

if printf '%s' "$detect_output" | grep -qE '^RESOLVED:(macos|linux|wsl)\|'; then
  ok "detect_platform resolved to a supported value: $(printf '%s' "$detect_output" | grep '^RESOLVED:' | head -1)"
else
  fail "detect_platform produced unexpected value: $detect_output"
fi

if printf '%s' "$detect_output" | grep -q '^Platform: '; then
  ok "platform_banner emitted Platform: line"
else
  fail "platform_banner did not emit Platform: line"
fi

# -----------------------------------------------------------------------------
# Phase 4 — preflight_pkg_hint emits non-empty hints per platform
# -----------------------------------------------------------------------------
phase "phase 4: preflight_pkg_hint emits hints"

for combo in \
  "macos::brew install" \
  "linux:apt:sudo apt-get" \
  "linux:dnf:sudo dnf install" \
  "linux:pacman:sudo pacman -S" \
  "wsl:apt:sudo apt-get"
do
  os_part="${combo%%:*}"
  rest="${combo#*:}"
  pkg_mgr="${rest%%:*}"
  expected="${rest#*:}"

  hint_output="$(
    bash -c '
      set -euo pipefail
      source "'"$tmpdir"'/detection-lib-full.sh"
      PLATFORM_OS="'"$os_part"'"
      LINUX_PKG_MGR="'"$pkg_mgr"'"
      preflight_pkg_hint smoke-test-tool 2>&1
    '
  )"

  if printf '%s' "$hint_output" | grep -qF "$expected"; then
    ok "preflight_pkg_hint ($os_part / ${pkg_mgr:-n/a}) emits expected '$expected'"
  else
    fail "preflight_pkg_hint ($os_part / ${pkg_mgr:-n/a}) missing '$expected'; got: $hint_output"
  fi
done

# Unknown distro fallback
unknown_hint="$(
  bash -c '
    set -euo pipefail
    source "'"$tmpdir"'/detection-lib-full.sh"
    PLATFORM_OS="linux"
    LINUX_PKG_MGR=""
    preflight_pkg_hint smoke-test-tool 2>&1
  '
)"
if printf '%s' "$unknown_hint" | grep -qF "distro package manager"; then
  ok "preflight_pkg_hint (linux / unknown) falls back gracefully"
else
  fail "preflight_pkg_hint (linux / unknown) missing graceful fallback: $unknown_hint"
fi

# -----------------------------------------------------------------------------
# Phase 5 — install.ps1 exists and is parse-ready (skip pwsh check by default)
# -----------------------------------------------------------------------------
phase "phase 5: install.ps1 entry exists"

if [[ -f "$REPO_ROOT/install.ps1" ]]; then
  ok "install.ps1 exists"
else
  fail "install.ps1 missing"
fi

if grep -q "Vibecrafted" "$REPO_ROOT/install.ps1" 2>/dev/null; then
  ok "install.ps1 has expected brand string"
else
  fail "install.ps1 missing brand string"
fi

if grep -qE 'v1\.x|v2\.x' "$REPO_ROOT/install.ps1" 2>/dev/null; then
  fail "install.ps1 still names a v1.x/v2.x Windows product that does not exist"
else
  ok "install.ps1 does not invent a native Windows version lane"
fi

if grep -q "WSL2 is the supported path" "$REPO_ROOT/install.ps1" 2>/dev/null; then
  ok "install.ps1 states WSL2 as the supported Windows path"
else
  fail "install.ps1 missing WSL2 supported-path sentence"
fi

if grep -q "Test-WslAvailable" "$REPO_ROOT/install.ps1" 2>/dev/null; then
  ok "install.ps1 has WSL detection helper"
else
  fail "install.ps1 missing WSL detection helper"
fi

# Optional: parse-check via pwsh if available (locally on macOS or in CI).
if command -v pwsh >/dev/null 2>&1; then
  if pwsh -NoLogo -NoProfile -Command "
    \$tokens = \$null; \$errors = \$null
    \$null = [System.Management.Automation.Language.Parser]::ParseFile('$REPO_ROOT/install.ps1', [ref]\$tokens, [ref]\$errors)
    if (\$errors.Count -gt 0) { exit 1 } else { exit 0 }
  " >/dev/null 2>&1; then
    ok "install.ps1 parses cleanly under pwsh"
  else
    fail "install.ps1 has pwsh parse errors"
  fi
else
  printf '  [%s] pwsh not installed — skipping ps1 parse check (no Windows runner is gated)\n' "$(yellow skip)"
fi

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
phase "summary"
printf '  passes:   %d\n' "$PASSES"
printf '  failures: %d\n' "${#FAILURES[@]}"

if [[ "${#FAILURES[@]}" -gt 0 ]]; then
  printf '\n%s — install smoke detected issues:\n' "$(red FAIL)"
  for entry in "${FAILURES[@]}"; do
    printf '  - %s\n' "$entry"
  done
  exit 1
fi

printf '\n%s — install.sh / install.ps1 smoke clean.\n' "$(green PASS)"
exit 0
