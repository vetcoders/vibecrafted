#!/usr/bin/env bash
set -euo pipefail

die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

[[ $# -eq 1 ]] || die "usage: $0 OUTPUT_BIN_DIR"
OUTPUT_BIN_DIR="$1"
LOCTREE_VERSION="0.14.4"
AICX_VERSION="0.13.0"
PRVIEW_VERSION="0.7.0"
LOCTREE_PACKAGE=""
AICX_PACKAGE=""
LOCTREE_TARBALL_SHA256=""
AICX_TARBALL_SHA256=""
EXE_SUFFIX=""

# Prebuilt-first: loctree and aicx come from npm platform packages.
# screenscribe is PyPI (`pipx install screenscribe`) and is staged by the
# pack assembler, not here. The only cargo donor in this script is prview
# from crates.io.
case "$(uname -s):$(uname -m)" in
  Darwin:arm64)
    LOCTREE_PACKAGE="@loctree/loctree-darwin-arm64"
    AICX_PACKAGE="@loctree/aicx-darwin-arm64"
    ;;
  Linux:x86_64)
    LOCTREE_PACKAGE="@loctree/loctree-linux-x64-gnu"
    AICX_PACKAGE="@loctree/aicx-linux-x64-gnu"
    # Measured 2026-09-11 from registry.npmjs.org tarballs.
    LOCTREE_TARBALL_SHA256="acb455446fe3bd40b6c832004e9892b250e7dad57180ed197cbbe3a2e39c32f5"
    AICX_TARBALL_SHA256="1ac9b9e0d056b9871cc2140474611e41af85a4601415bdd1d29cba657f306e2d"
    ;;
  Linux:aarch64|Linux:arm64)
    die "no npm platform package for loctree/aicx on Linux/arm64; will not cargo-build those donors"
    ;;
  MINGW*:x86_64|MSYS*:x86_64|CYGWIN*:x86_64)
    LOCTREE_PACKAGE="@loctree/loctree-win32-x64-msvc"
    AICX_PACKAGE="@loctree/aicx-win32-x64-gnu"
    EXE_SUFFIX=".exe"
    ;;
  *) die "no complete Runtime Foundations payload for $(uname -s)/$(uname -m)" ;;
esac

[[ "${VIBECRAFTED_FOUNDATIONS_TARGET_PROBE:-0}" == 1 ]] && exit 0

for tool in npm cargo python3; do require "$tool"; done

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

# npm verifies registry integrity. Extract only the native runtime files.
# Prints the tarball sha256 on stdout for the foundations manifest.
stage_npm_bins() {
  local package="$1" version="$2" expected_sha="$3"
  shift 3
  local work tarball digest name
  work="$WORK/npm-${package##*/}"
  mkdir -p "$work"
  npm pack "${package}@${version}" --pack-destination "$work" >/dev/null
  tarball="$(echo "$work"/*.tgz)"
  [[ -f "$tarball" ]] || die "npm pack produced no tarball: ${package}@${version}"
  digest="$(sha256_file "$tarball")"
  if [[ -n "$expected_sha" && "$digest" != "$expected_sha" ]]; then
    die "npm tarball checksum mismatch: ${package}@${version} (${digest})"
  fi
  tar -xzf "$tarball" -C "$work"
  for name in "$@"; do
    install -m 0755 "$work/package/bin/${name}${EXE_SUFFIX}" \
      "$OUTPUT_BIN_DIR/${name}${EXE_SUFFIX}"
  done
  rm -rf "$work" 2>/dev/null || true
  printf '%s\n' "$digest"
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-foundations.XXXXXX")"
# Cleanup must never turn an otherwise complete carrier build into a release
# failure. Finder/metadata services can recreate .DS_Store while rm is walking
# a temporary tree on macOS, making rm report ENOTEMPTY after every binary has
# already been staged successfully. The tree is disposable and remains under
# the OS temporary root, so preserve the build result if best-effort cleanup
# loses that race.
trap 'rm -rf "$WORK" 2>/dev/null || true' EXIT
mkdir -p "$OUTPUT_BIN_DIR" "$WORK/prview"

LOCTREE_ARCHIVE_SHA256="$(
  stage_npm_bins "$LOCTREE_PACKAGE" "$LOCTREE_VERSION" "$LOCTREE_TARBALL_SHA256" \
    loct loctree loctree-mcp loctree-lsp
)"
AICX_ARCHIVE_SHA256="$(
  stage_npm_bins "$AICX_PACKAGE" "$AICX_VERSION" "$AICX_TARBALL_SHA256" \
    aicx aicx-mcp
)"

strip_executable() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    /usr/bin/strip -S "$1"
  else
    strip --strip-debug "$1"
  fi
}

# The least-critical donor, and the only one this script builds: prview from
# crates.io. GitHub release pages currently have no assets.
if [[ "$(uname -s)" == "Darwin" ]]; then
  require brew
  OPENSSL_PREFIX="$(brew --prefix openssl@3)"
  [[ -f "$OPENSSL_PREFIX/lib/libssl.a" && -f "$OPENSSL_PREFIX/lib/libcrypto.a" ]] \
    || die "static OpenSSL archives are required to build portable PRView"
  OPENSSL_DIR="$OPENSSL_PREFIX" OPENSSL_STATIC=1 CARGO_PROFILE_RELEASE_STRIP=false \
    cargo install --locked --version "$PRVIEW_VERSION" --root "$WORK/prview" prview
else
  CARGO_PROFILE_RELEASE_STRIP=false \
    cargo install --locked --version "$PRVIEW_VERSION" --root "$WORK/prview" prview
fi
install -m 0755 "$WORK/prview/bin/prview${EXE_SUFFIX}" \
  "$OUTPUT_BIN_DIR/prview${EXE_SUFFIX}"
strip_executable "$OUTPUT_BIN_DIR/prview${EXE_SUFFIX}"

if [[ "$(uname -s)" == "Darwin" ]] && \
  otool -L "$OUTPUT_BIN_DIR/prview" | grep -Eq '^[[:space:]]+/(opt|usr/local)/'; then
  otool -L "$OUTPUT_BIN_DIR/prview" >&2
  die "PRView retains a non-system dynamic library dependency"
fi

"$OUTPUT_BIN_DIR/loct${EXE_SUFFIX}" --version | grep -F "$LOCTREE_VERSION" >/dev/null
"$OUTPUT_BIN_DIR/aicx${EXE_SUFFIX}" --version | grep -F "$AICX_VERSION" >/dev/null
"$OUTPUT_BIN_DIR/prview${EXE_SUFFIX}" --version | grep -F "$PRVIEW_VERSION" >/dev/null

python3 - "$OUTPUT_BIN_DIR" "$LOCTREE_VERSION" "$AICX_VERSION" "$PRVIEW_VERSION" \
  "$LOCTREE_PACKAGE" "$LOCTREE_ARCHIVE_SHA256" \
  "$AICX_PACKAGE" "$AICX_ARCHIVE_SHA256" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
versions = {"loctree": sys.argv[2], "aicx": sys.argv[3], "prview": sys.argv[4]}
loctree_package, loctree_archive_sha256 = sys.argv[5:7]
aicx_package, aicx_archive_sha256 = sys.argv[7:9]


def npm_tarball_url(package: str, version: str) -> str:
    filename = f"{package.rsplit('/', 1)[-1]}-{version}.tgz"
    return f"https://registry.npmjs.org/{package}/-/{filename}"


files = {}
for path in sorted(root.iterdir()):
    if path.is_file() and os.access(path, os.X_OK):
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
payload = {
    "schema": "io.vetcoders.vibecrafted.runtime-foundations.v1",
    "versions": versions,
    "source_revisions": {
        "loctree": versions["loctree"],
        "aicx": versions["aicx"],
    },
    "source_archives": {
        "loctree": {
            "url": npm_tarball_url(loctree_package, versions["loctree"]),
            "sha256": loctree_archive_sha256,
        },
        "aicx": {
            "url": npm_tarball_url(aicx_package, versions["aicx"]),
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
