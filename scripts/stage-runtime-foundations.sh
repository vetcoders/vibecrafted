#!/usr/bin/env bash
set -euo pipefail

die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

[[ $# -eq 1 ]] || die "usage: $0 OUTPUT_BIN_DIR"
OUTPUT_BIN_DIR="$1"
LOCTREE_VERSION="0.14.4"
AICX_VERSION="0.12.5"
PRVIEW_VERSION="0.6.0"

case "$(uname -s):$(uname -m)" in
  Darwin:arm64)
    LOCTREE_PACKAGE="@loctree/loctree-darwin-arm64"
    AICX_ASSET="aicx-v${AICX_VERSION}-aarch64-apple-darwin-slim.zip"
    AICX_ARCHIVE_TYPE="zip"
    EXE_SUFFIX=""
    ;;
  Linux:x86_64)
    LOCTREE_PACKAGE="@loctree/loctree-linux-x64-gnu"
    AICX_ASSET="aicx-v${AICX_VERSION}-x86_64-linux-gnu-slim.tar.gz"
    AICX_ARCHIVE_TYPE="tar.gz"
    EXE_SUFFIX=""
    ;;
  MINGW*:x86_64|MSYS*:x86_64|CYGWIN*:x86_64)
    LOCTREE_PACKAGE="@loctree/loctree-win32-x64-msvc"
    AICX_ASSET="aicx-v${AICX_VERSION}-x86_64-pc-windows-msvc-slim.zip"
    AICX_ARCHIVE_TYPE="zip"
    EXE_SUFFIX=".exe"
    ;;
  *) die "no complete Runtime Foundations payload for $(uname -s)/$(uname -m)" ;;
esac

for tool in curl npm cargo python3; do require "$tool"; done
[[ "$AICX_ARCHIVE_TYPE" != "zip" ]] || require unzip

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

# AICX release assets publish a checksum beside every platform archive. The
# npm platform package uses the same assets; downloading them directly keeps
# the Runtime Pack independent of Node at customer install time.
AICX_BASE="https://github.com/Loctree/aicx/releases/download/v${AICX_VERSION}"
curl -fsSL "$AICX_BASE/$AICX_ASSET" -o "$WORK/aicx/$AICX_ASSET"
curl -fsSL "$AICX_BASE/$AICX_ASSET.sha256" -o "$WORK/aicx/$AICX_ASSET.sha256"
(
  cd "$WORK/aicx"
  shasum -a 256 -c "$AICX_ASSET.sha256" >/dev/null 2>&1 \
    || sha256sum -c "$AICX_ASSET.sha256" >/dev/null
)
if [[ "$AICX_ARCHIVE_TYPE" == "zip" ]]; then
  unzip -q "$WORK/aicx/$AICX_ASSET" -d "$WORK/aicx/unpacked"
else
  mkdir -p "$WORK/aicx/unpacked"
  tar -xzf "$WORK/aicx/$AICX_ASSET" -C "$WORK/aicx/unpacked"
fi
for name in aicx aicx-mcp; do
  source_path="$(find "$WORK/aicx/unpacked" -type f -name "${name}${EXE_SUFFIX}" -print -quit)"
  [[ -n "$source_path" ]] || die "$AICX_ASSET contains no ${name}${EXE_SUFFIX}"
  install -m 0755 "$source_path" "$OUTPUT_BIN_DIR/${name}${EXE_SUFFIX}"
done

# PRView documents GitHub release binaries, but its release page currently has
# no assets. Build the exact published crate once, during carrier assembly, so
# customers still receive a ready binary and never need Rust or Cargo.
cargo install --locked --version "$PRVIEW_VERSION" --root "$WORK/prview" prview
install -m 0755 "$WORK/prview/bin/prview${EXE_SUFFIX}" \
  "$OUTPUT_BIN_DIR/prview${EXE_SUFFIX}"

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
    "files": files,
}
(root.parent / "runtime-foundations.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
