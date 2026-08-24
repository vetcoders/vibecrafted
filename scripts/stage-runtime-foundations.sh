#!/usr/bin/env bash
set -euo pipefail

die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

[[ $# -eq 1 ]] || die "usage: $0 OUTPUT_BIN_DIR"
OUTPUT_BIN_DIR="$1"
LOCTREE_VERSION="0.14.4"
AICX_VERSION="0.12.5"
AICX_REVISION="ced57997dd97a2b08960f35e3a657d7b0c49a200"
PRVIEW_VERSION="0.6.0"

case "$(uname -s):$(uname -m)" in
  Darwin:arm64)
    LOCTREE_PACKAGE="@loctree/loctree-darwin-arm64"
    EXE_SUFFIX=""
    ;;
  Linux:x86_64)
    LOCTREE_PACKAGE="@loctree/loctree-linux-x64-gnu"
    EXE_SUFFIX=""
    ;;
  MINGW*:x86_64|MSYS*:x86_64|CYGWIN*:x86_64)
    LOCTREE_PACKAGE="@loctree/loctree-win32-x64-msvc"
    EXE_SUFFIX=".exe"
    ;;
  *) die "no complete Runtime Foundations payload for $(uname -s)/$(uname -m)" ;;
esac

for tool in git npm cargo python3; do require "$tool"; done

WORK="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-foundations.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUTPUT_BIN_DIR" "$WORK/loctree" "$WORK/aicx" "$WORK/prview"

# npm verifies the registry integrity for the exact platform package. Extract
# only the native runtime files; Node and its global package tree are not part
# of the installed product.
npm pack "${LOCTREE_PACKAGE}@${LOCTREE_VERSION}" \
  --pack-destination "$WORK/loctree" >/dev/null
tar -xzf "$WORK/loctree"/*.tgz -C "$WORK/loctree"
for name in loct loctree loctree-mcp loctree-lsp; do
  install -m 0755 "$WORK/loctree/package/bin/${name}${EXE_SUFFIX}" \
    "$OUTPUT_BIN_DIR/${name}${EXE_SUFFIX}"
done

# The published 0.12.5 AICX archives are checksum-correct but retain their CI
# builder's /Users path in both native binaries. Build the exact release commit
# with path remaps instead of weakening payload hygiene or byte-patching signed
# upstream artifacts. Customers still receive ready binaries and need no Rust.
git clone --quiet --depth 1 --branch "v${AICX_VERSION}" \
  https://github.com/Loctree/aicx.git "$WORK/aicx/source"
[[ "$(git -C "$WORK/aicx/source" rev-parse HEAD)" == "$AICX_REVISION" ]] \
  || die "AICX v${AICX_VERSION} does not resolve to pinned $AICX_REVISION"
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

python3 - "$OUTPUT_BIN_DIR" "$LOCTREE_VERSION" "$AICX_VERSION" "$PRVIEW_VERSION" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
versions = {"loctree": sys.argv[2], "aicx": sys.argv[3], "prview": sys.argv[4]}
files = {}
for path in sorted(root.iterdir()):
    if path.is_file() and os.access(path, os.X_OK):
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
payload = {
    "schema": "io.vetcoders.vibecrafted.runtime-foundations.v1",
    "versions": versions,
    "source_revisions": {"aicx": "ced57997dd97a2b08960f35e3a657d7b0c49a200"},
    "files": files,
}
(root.parent / "runtime-foundations.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
