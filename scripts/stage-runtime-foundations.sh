#!/usr/bin/env bash
set -euo pipefail

die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

[[ $# -eq 1 ]] || die "usage: $0 OUTPUT_BIN_DIR"
OUTPUT_BIN_DIR="$1"
LOCTREE_VERSION="0.14.4"
LOCTREE_REVISION="3e9eb0a74cb3c043d740de5fe7d8c93985d0a876"
LOCTREE_ARCHIVE_SHA256="cdf37cff13b423d9be916f74bb43bc5857729e64380d7bc2f16462568d74a5cb"
AICX_VERSION="0.12.6"
AICX_REVISION="215b8060fc56f3968e5a9a83a85cba845149a8bf"
AICX_ARCHIVE_SHA256="6a207d9c8ef82de919eb62db3d50294613e394416c3e28f1b7c5ac44a0151fb9"
PRVIEW_VERSION="0.7.0"
LOCTREE_SOURCE_BUILD=0

case "$(uname -s):$(uname -m)" in
  Darwin:arm64)
    LOCTREE_PACKAGE="@loctree/loctree-darwin-arm64"
    EXE_SUFFIX=""
    ;;
  Linux:x86_64)
    LOCTREE_PACKAGE="@loctree/loctree-linux-x64-gnu"
    EXE_SUFFIX=""
    ;;
  Linux:aarch64|Linux:arm64)
    # npm has no Linux arm64 platform package. Build the exact public release
    # commit from a digest-pinned source archive instead of falling back to a
    # sibling checkout or a mutable branch.
    LOCTREE_PACKAGE=""
    LOCTREE_SOURCE_BUILD=1
    EXE_SUFFIX=""
    ;;
  MINGW*:x86_64|MSYS*:x86_64|CYGWIN*:x86_64)
    LOCTREE_PACKAGE="@loctree/loctree-win32-x64-msvc"
    EXE_SUFFIX=".exe"
    ;;
  *) die "no complete Runtime Foundations payload for $(uname -s)/$(uname -m)" ;;
esac

[[ "${VIBECRAFTED_FOUNDATIONS_TARGET_PROBE:-0}" == 1 ]] && exit 0

for tool in curl npm cargo python3; do require "$tool"; done

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

fetch_source() {
  local url="$1" expected="$2" archive="$3" destination="$4"
  curl -fL --proto '=https' --tlsv1.2 "$url" -o "$archive"
  [[ "$(sha256_file "$archive")" == "$expected" ]] \
    || die "source archive checksum mismatch: $url"
  mkdir -p "$destination"
  tar -xzf "$archive" --strip-components=1 -C "$destination"
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-foundations.XXXXXX")"
# Cleanup must never turn an otherwise complete carrier build into a release
# failure. Finder/metadata services can recreate .DS_Store while rm is walking
# a temporary tree on macOS, making rm report ENOTEMPTY after every binary has
# already been staged successfully. The tree is disposable and remains under
# the OS temporary root, so preserve the build result if best-effort cleanup
# loses that race.
trap 'rm -rf "$WORK" 2>/dev/null || true' EXIT
mkdir -p "$OUTPUT_BIN_DIR" "$WORK/loctree" "$WORK/aicx" "$WORK/prview"

# npm verifies the registry integrity for the exact platform package. Extract
# only the native runtime files; Node and its global package tree are not part
# of the installed product.
if [[ "$LOCTREE_SOURCE_BUILD" == 1 ]]; then
  fetch_source \
    "https://codeload.github.com/Loctree/loctree/tar.gz/${LOCTREE_REVISION}" \
    "$LOCTREE_ARCHIVE_SHA256" "$WORK/loctree/source.tar.gz" "$WORK/loctree/source"
  LOCTREE_TARGET="$WORK/loctree/target"
  CARGO_TARGET_DIR="$LOCTREE_TARGET" cargo build \
    --manifest-path "$WORK/loctree/source/Cargo.toml" --release --locked \
    -p loctree --bin loct --bin loctree
  for package in loctree-mcp loctree-lsp; do
    CARGO_TARGET_DIR="$LOCTREE_TARGET" cargo build \
      --manifest-path "$WORK/loctree/source/Cargo.toml" --release --locked \
      -p "$package"
  done
  for name in loct loctree loctree-mcp loctree-lsp; do
    install -m 0755 "$LOCTREE_TARGET/release/$name" "$OUTPUT_BIN_DIR/$name"
  done
else
  npm pack "${LOCTREE_PACKAGE}@${LOCTREE_VERSION}" \
    --pack-destination "$WORK/loctree" >/dev/null
  tar -xzf "$WORK/loctree"/*.tgz -C "$WORK/loctree"
  for name in loct loctree loctree-mcp loctree-lsp; do
    install -m 0755 "$WORK/loctree/package/bin/${name}${EXE_SUFFIX}" \
      "$OUTPUT_BIN_DIR/${name}${EXE_SUFFIX}"
  done
fi
rm -rf "$WORK/loctree" 2>/dev/null || true

# The published AICX binaries retain their CI builder's /Users path in both
# native binaries. Build the exact digest-pinned source revision with path
# remaps instead of weakening payload hygiene or byte-patching upstream
# artifacts. Customers still receive ready binaries and need no Rust.
fetch_source \
  "https://codeload.github.com/Loctree/aicx/tar.gz/${AICX_REVISION}" \
  "$AICX_ARCHIVE_SHA256" "$WORK/aicx/source.tar.gz" "$WORK/aicx/source"
AICX_TARGET="$WORK/aicx/target"
NATIVE_REMAP_FLAGS="-ffile-prefix-map=$HOME=/usr/src/operator-home -ffile-prefix-map=$WORK/aicx/source=/usr/src/aicx"
RUSTFLAGS="--remap-path-prefix=$HOME=/usr/src/operator-home --remap-path-prefix=$WORK/aicx/source=/usr/src/aicx" \
  CFLAGS="$NATIVE_REMAP_FLAGS" \
  CXXFLAGS="$NATIVE_REMAP_FLAGS" \
  OBJCFLAGS="$NATIVE_REMAP_FLAGS" \
  OBJCXXFLAGS="$NATIVE_REMAP_FLAGS" \
  CARGO_TARGET_DIR="$AICX_TARGET" \
  cargo build --manifest-path "$WORK/aicx/source/Cargo.toml" \
    --release --locked --bin aicx --bin aicx-mcp
for name in aicx aicx-mcp; do
  source_path="$AICX_TARGET/release/${name}${EXE_SUFFIX}"
  [[ -f "$source_path" ]] || die "AICX build contains no ${name}${EXE_SUFFIX}"
  install -m 0755 "$source_path" "$OUTPUT_BIN_DIR/${name}${EXE_SUFFIX}"
done
rm -rf "$WORK/aicx" 2>/dev/null || true

# PRView documents GitHub release binaries, but its release page currently has
# no assets. Build the exact published crate once, during carrier assembly, so
# customers still receive a ready binary and never need Rust or Cargo. On macOS
# the git2 dependency otherwise records the build machine's Homebrew OpenSSL
# paths, which would make the signed binary unusable on a clean Mac.
if [[ "$(uname -s)" == "Darwin" ]]; then
  require brew
  OPENSSL_PREFIX="$(brew --prefix openssl@3)"
  [[ -f "$OPENSSL_PREFIX/lib/libssl.a" && -f "$OPENSSL_PREFIX/lib/libcrypto.a" ]] \
    || die "static OpenSSL archives are required to build portable PRView"
  OPENSSL_DIR="$OPENSSL_PREFIX" OPENSSL_STATIC=1 \
    cargo install --locked --version "$PRVIEW_VERSION" --root "$WORK/prview" prview
else
  cargo install --locked --version "$PRVIEW_VERSION" --root "$WORK/prview" prview
fi
install -m 0755 "$WORK/prview/bin/prview${EXE_SUFFIX}" \
  "$OUTPUT_BIN_DIR/prview${EXE_SUFFIX}"

if [[ "$(uname -s)" == "Darwin" ]] && \
  otool -L "$OUTPUT_BIN_DIR/prview" | grep -Eq '^[[:space:]]+/(opt|usr/local)/'; then
  otool -L "$OUTPUT_BIN_DIR/prview" >&2
  die "PRView retains a non-system dynamic library dependency"
fi

"$OUTPUT_BIN_DIR/loct${EXE_SUFFIX}" --version | grep -F "$LOCTREE_VERSION" >/dev/null
"$OUTPUT_BIN_DIR/aicx${EXE_SUFFIX}" --version | grep -F "$AICX_VERSION" >/dev/null
"$OUTPUT_BIN_DIR/prview${EXE_SUFFIX}" --version | grep -F "$PRVIEW_VERSION" >/dev/null

python3 - "$OUTPUT_BIN_DIR" "$LOCTREE_VERSION" "$AICX_VERSION" "$PRVIEW_VERSION" \
  "$LOCTREE_REVISION" "$LOCTREE_ARCHIVE_SHA256" \
  "$AICX_REVISION" "$AICX_ARCHIVE_SHA256" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
versions = {"loctree": sys.argv[2], "aicx": sys.argv[3], "prview": sys.argv[4]}
loctree_revision, loctree_archive_sha256 = sys.argv[5:7]
aicx_revision, aicx_archive_sha256 = sys.argv[7:9]
files = {}
for path in sorted(root.iterdir()):
    if path.is_file() and os.access(path, os.X_OK):
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
payload = {
    "schema": "io.vetcoders.vibecrafted.runtime-foundations.v1",
    "versions": versions,
    "source_revisions": {
        "loctree": loctree_revision,
        "aicx": aicx_revision,
    },
    "source_archives": {
        "loctree": {
            "url": f"https://codeload.github.com/Loctree/loctree/tar.gz/{loctree_revision}",
            "sha256": loctree_archive_sha256,
        },
        "aicx": {
            "url": f"https://codeload.github.com/Loctree/aicx/tar.gz/{aicx_revision}",
            "sha256": aicx_archive_sha256,
        },
    },
    "licenses": {
        "loctree": "BUSL-1.1",
        "aicx": "BUSL-1.1",
        "prview": "BUSL-1.1",
    },
    "files": files,
}
(root.parent / "runtime-foundations.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
