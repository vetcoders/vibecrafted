#!/usr/bin/env bash
# Relocate Homebrew-linked OpenSSL 3 onto @loader_path for a Darwin payload.
#
# This is the same install_name_tool pattern the release builder already uses
# for libpython3.12.dylib. It copies pinned redistributable dylibs (Homebrew
# bottle bytes, Apache-2.0) and rewrites load commands. It never compiles
# OpenSSL, AICX, Loctree, ScreenScribe or PRView.
#
# Published PRView Darwin names:
#   /opt/homebrew/opt/openssl@3/lib/libssl.3.dylib
#   /opt/homebrew/opt/openssl@3/lib/libcrypto.3.dylib
# Those are PRView's own load commands, not `brew --prefix`.

if ! declare -F die >/dev/null 2>&1; then
  die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }
fi

OPENSSL_LIBSSL_SHA256="ffd8ac6981000def0928367924b6cb1e7a98712efbc06e2a2f3f750138bd89ca"
OPENSSL_LIBCRYPTO_SHA256="a12805a18cd5e4f733fa8727b91afa08b587f9da5a760517cd79cb508a3a3f71"
OPENSSL_REDIS_VERSION="3.6.3"
# The published PRView v0.7.0 Darwin asset names these exact install names.
OPENSSL_LIBSSL_ID="/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib"
OPENSSL_LIBCRYPTO_ID="/opt/homebrew/opt/openssl@3/lib/libcrypto.3.dylib"

_darwin_sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
}

_darwin_otool_deps() {
  otool -L "$1" | awk 'NR>1 { print $1 }'
}

_darwin_adhoc_sign() {
  codesign --force --sign - --timestamp=none "$1" >/dev/null
}

# Copy the pinned OpenSSL 3 dylibs into <lib_dir> and rewrite their ids and
# mutual references to @loader_path. Copies LICENSE.txt into <license_dir>.
stage_relocatable_openssl() {
  local lib_dir="$1" license_dir="$2"
  local ssl_src crypto_src license_src
  ssl_src="$OPENSSL_LIBSSL_ID"
  crypto_src="$OPENSSL_LIBCRYPTO_ID"
  [[ -f "$ssl_src" ]] || die "pinned OpenSSL libssl is missing: $ssl_src"
  [[ -f "$crypto_src" ]] || die "pinned OpenSSL libcrypto is missing: $crypto_src"
  [[ "$(_darwin_sha256 "$ssl_src")" == "$OPENSSL_LIBSSL_SHA256" ]] \
    || die "OpenSSL libssl digest is not the pinned Homebrew bottle bytes (${OPENSSL_REDIS_VERSION})"
  [[ "$(_darwin_sha256 "$crypto_src")" == "$OPENSSL_LIBCRYPTO_SHA256" ]] \
    || die "OpenSSL libcrypto digest is not the pinned Homebrew bottle bytes (${OPENSSL_REDIS_VERSION})"

  mkdir -p "$lib_dir" "$license_dir"
  # Real files only: the Runtime Pack forbids symlinks.
  cp -f "$ssl_src" "$lib_dir/libssl.3.dylib"
  cp -f "$crypto_src" "$lib_dir/libcrypto.3.dylib"
  chmod 0755 "$lib_dir/libssl.3.dylib" "$lib_dir/libcrypto.3.dylib"

  install_name_tool -id '@loader_path/libssl.3.dylib' "$lib_dir/libssl.3.dylib"
  install_name_tool -id '@loader_path/libcrypto.3.dylib' "$lib_dir/libcrypto.3.dylib"
  relocate_binary_openssl \
    "$lib_dir/libssl.3.dylib" \
    "@loader_path/libssl.3.dylib" \
    "@loader_path/libcrypto.3.dylib"
  relocate_binary_openssl \
    "$lib_dir/libcrypto.3.dylib" \
    "@loader_path/libssl.3.dylib" \
    "@loader_path/libcrypto.3.dylib"

  license_src="$(dirname "$ssl_src")/../LICENSE.txt"
  [[ -f "$license_src" ]] || license_src="/opt/homebrew/opt/openssl@3/LICENSE.txt"
  [[ -f "$license_src" ]] || die "OpenSSL Apache-2.0 LICENSE.txt is missing"
  install -m 0644 "$license_src" "$license_dir/LICENSE.txt"
}

# Rewrite <binary>'s Homebrew OpenSSL load commands to <loader_relative>
# (e.g. @loader_path/../lib/libssl.3.dylib).
relocate_binary_openssl() {
  local binary="$1" ssl_dest="$2" crypto_dest="$3"
  local dep dest
  while IFS= read -r dep; do
    [[ -n "$dep" ]] || continue
    case "$dep" in
      /usr/lib/*|/System/Library/*|@loader_path/*|@rpath/*|@executable_path/*) continue ;;
    esac
    dest=""
    case "$dep" in
      *libssl.3.dylib) dest="$ssl_dest" ;;
      *libcrypto.3.dylib) dest="$crypto_dest" ;;
      *) die "$binary has an unexpected non-system dependency: $dep" ;;
    esac
    install_name_tool -change "$dep" "$dest" "$binary"
  done < <(_darwin_otool_deps "$binary")
  _darwin_adhoc_sign "$binary"
}

assert_no_homebrew_load_commands() {
  local binary="$1"
  if _darwin_otool_deps "$binary" | grep -Eq '^/(opt|usr/local)/'; then
    otool -L "$binary" >&2
    die "still has Homebrew or /usr/local load commands: $binary"
  fi
}

write_prview_wrapper() {
  local wrapper="$1" macho="$2"
  # SSL_CERT_FILE points at the macOS system CA bundle. The vendored libcrypto
  # still names Homebrew OPENSSLDIR in non-executable compiled-in strings;
  # without this, TLS would look for /opt/homebrew/etc/openssl@3/cert.pem.
  cat > "$wrapper" <<'EOF'
#!/bin/bash
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/cert.pem}"
exec "$runtime_root/libexec/prview" "$@"
EOF
  chmod 0755 "$wrapper"
  [[ -x "$macho" ]] || die "prview Mach-O is missing: $macho"
}
