#!/usr/bin/env bash
set -euo pipefail

# Stage the Runtime Pack's external agent foundations from published artifacts.
# AICX, Loctree and PRView are consumed as ready npm / GitHub-release binaries.
# This script must never cargo-build, cargo-install, fetch source archives, or
# byte-patch those products. ScreenScribe is a PyPI wheel staged by the release
# builder, not here.
#
# Upstream binaries may still name the publisher's CI paths. That is artifact
# provenance, not this build host's privacy leak. Payload hygiene continues to
# forbid THIS host's HOME/checkout in first-party bytes; pinned published
# Mach-O digests (see scripts/lib/published-foundation-digests.json) are scoped
# as upstream debug/provenance, not locally authored private payload.
# Darwin PRView is relocated onto vendored OpenSSL dylibs; it is not rebuilt.

die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$SCRIPT_DIR/lib/darwin-relocate-openssl.sh"

[[ $# -eq 1 ]] || die "usage: $0 OUTPUT_BIN_DIR"
OUTPUT_BIN_DIR="$1"
LOCTREE_VERSION="0.14.4"
LOCTREE_REVISION="3e9eb0a74cb3c043d740de5fe7d8c93985d0a876"
AICX_VERSION="0.13.0"
AICX_REVISION="91b2fe121e97e92df7fffbc12b81abbf68a34fc1"
PRVIEW_VERSION="0.7.0"
PRVIEW_REVISION="2e11cc6d6e90a606a17d71d0d093a1e2f564bc80"

case "$(uname -s):$(uname -m)" in
  Darwin:arm64)
    LOCTREE_PACKAGE="@loctree/loctree-darwin-arm64"
    LOCTREE_INTEGRITY="sha512-iNi7BOm/hvvnWwwKc1//hvuGEXIn5vf7wHBKOBAsWFcmNTu9D7zOubPZb4KO1TKsvI/wsCUdzGIfBf4IUOUvoA=="
    AICX_PACKAGE="@loctree/aicx-darwin-arm64"
    AICX_INTEGRITY="sha512-tRU0jYR3MLHmmXaiQMdb52OkplNrjqbrtk70vIeERMKpoWJEbRbLPJtWzMCZGBP3pSTrDw5hQ+CBV5vm9BmRMw=="
    PRVIEW_URL="https://github.com/vetcoders/prview-rs/releases/download/v0.7.0/prview-aarch64-apple-darwin.tar.gz"
    PRVIEW_SHA256="f37729af60f21ba8d29621cc5e139b907cae0264776f44030087d65dc24261f0"
    EXE_SUFFIX=""
    ;;
  Linux:x86_64)
    LOCTREE_PACKAGE="@loctree/loctree-linux-x64-gnu"
    LOCTREE_INTEGRITY="sha512-NoHdKOy9UBNDhiuckuMpdhjesUXgozTzOt3GdCTNU85ffwJB8yrhl7B+eLO+G46flUs3XvuhGtjPafDe1zv5Ow=="
    AICX_PACKAGE="@loctree/aicx-linux-x64-gnu"
    AICX_INTEGRITY="sha512-Goghck4GbB+pRAZt39fNITqntJi5FBNQcsnDH7KSYre/116EuUyYGObB6HiNYa/meTXynoVrw+l91gaV6mHOrA=="
    PRVIEW_URL="https://github.com/vetcoders/prview-rs/releases/download/v0.7.0/prview-x86_64-unknown-linux-gnu.tar.gz"
    PRVIEW_SHA256="a5d2024a6c4c8aeecf97771780c15a13b45046c6c3f10e88307ead07ac88a053"
    EXE_SUFFIX=""
    ;;
  Linux:aarch64|Linux:arm64)
    die "no published Runtime Foundations payload for Linux/aarch64 (missing npm platform packages for Loctree and AICX, and no PRView GitHub-release asset)"
    ;;
  MINGW*:x86_64|MSYS*:x86_64|CYGWIN*:x86_64)
    die "no published Runtime Foundations payload for Windows/x86_64 (PRView GitHub releases ship no Windows binary)"
    ;;
  *) die "no published Runtime Foundations payload for $(uname -s)/$(uname -m)" ;;
esac

[[ "${VIBECRAFTED_FOUNDATIONS_TARGET_PROBE:-0}" == 1 ]] && exit 0

for tool in curl npm python3; do require "$tool"; done

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

verify_npm_tarball() {
  local archive="$1" expected="$2"
  python3 - "$archive" "$expected" <<'PY'
import base64
import hashlib
import pathlib
import sys

archive = pathlib.Path(sys.argv[1])
expected = sys.argv[2]
algorithm, _, encoded = expected.partition("-")
if algorithm != "sha512" or not encoded:
    raise SystemExit(f"unsupported npm integrity: {expected}")
digest = hashlib.sha512(archive.read_bytes()).digest()
observed = "sha512-" + base64.b64encode(digest).decode("ascii")
if observed != expected:
    raise SystemExit(f"npm integrity mismatch for {archive.name}")
print(hashlib.sha256(archive.read_bytes()).hexdigest())
PY
}

stage_npm_binaries() {
  local package="$1" version="$2" integrity="$3" work="$4"
  local names=("${@:5}")
  mkdir -p "$work"
  npm pack "${package}@${version}" --pack-destination "$work" >/dev/null
  local archive
  archive="$(find "$work" -maxdepth 1 -name '*.tgz' -print)"
  [[ -n "$archive" && -f "$archive" ]] || die "npm pack produced no tarball for ${package}@${version}"
  [[ "$(printf '%s\n' "$archive" | wc -l | tr -d ' ')" == 1 ]] \
    || die "npm pack produced multiple tarballs for ${package}@${version}"
  local sha256
  sha256="$(verify_npm_tarball "$archive" "$integrity")" \
    || die "published package failed integrity: ${package}@${version}"
  tar -xzf "$archive" -C "$work"
  local name source_path
  for name in "${names[@]}"; do
    source_path="$work/package/bin/${name}${EXE_SUFFIX}"
    [[ -f "$source_path" ]] || die "${package}@${version} contains no bin/${name}${EXE_SUFFIX}"
    install -m 0755 "$source_path" "$OUTPUT_BIN_DIR/${name}${EXE_SUFFIX}"
  done
  printf '%s\n' "$sha256"
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

LOCTREE_ARCHIVE_SHA256="$(
  stage_npm_binaries \
    "$LOCTREE_PACKAGE" "$LOCTREE_VERSION" "$LOCTREE_INTEGRITY" "$WORK/loctree" \
    loct loctree loctree-mcp loctree-lsp
)"
LOCTREE_ARCHIVE_URL="https://registry.npmjs.org/${LOCTREE_PACKAGE}/-/${LOCTREE_PACKAGE##*/}-${LOCTREE_VERSION}.tgz"
rm -rf "$WORK/loctree" 2>/dev/null || true

AICX_ARCHIVE_SHA256="$(
  stage_npm_binaries \
    "$AICX_PACKAGE" "$AICX_VERSION" "$AICX_INTEGRITY" "$WORK/aicx" \
    aicx aicx-mcp
)"
AICX_ARCHIVE_URL="https://registry.npmjs.org/${AICX_PACKAGE}/-/${AICX_PACKAGE##*/}-${AICX_VERSION}.tgz"
rm -rf "$WORK/aicx" 2>/dev/null || true

curl -fL --proto '=https' --tlsv1.2 "$PRVIEW_URL" -o "$WORK/prview/prview.tar.gz"
[[ "$(sha256_file "$WORK/prview/prview.tar.gz")" == "$PRVIEW_SHA256" ]] \
  || die "published PRView checksum mismatch: $PRVIEW_URL"
mkdir -p "$WORK/prview/extract"
tar -xzf "$WORK/prview/prview.tar.gz" -C "$WORK/prview/extract"
PRVIEW_BIN=""
if [[ -f "$WORK/prview/extract/prview${EXE_SUFFIX}" ]]; then
  PRVIEW_BIN="$WORK/prview/extract/prview${EXE_SUFFIX}"
else
  PRVIEW_BIN="$(find "$WORK/prview/extract" -type f -name "prview${EXE_SUFFIX}" -print | head -n 1)"
fi
[[ -n "$PRVIEW_BIN" && -f "$PRVIEW_BIN" ]] || die "published PRView archive contains no prview${EXE_SUFFIX}"
install -m 0755 "$PRVIEW_BIN" "$OUTPUT_BIN_DIR/prview${EXE_SUFFIX}"
rm -rf "$WORK/prview" 2>/dev/null || true

RUNTIME_ROOT="$(cd "$OUTPUT_BIN_DIR/.." && pwd)"
OPENSSL_STAGED_VERSION=""
OPENSSL_STAGED_LIBSSL=""
OPENSSL_STAGED_LIBCRYPTO=""
if [[ "$(uname -s)" == "Darwin" ]]; then
  for tool in otool install_name_tool codesign vtool; do require "$tool"; done
  mkdir -p "$RUNTIME_ROOT/lib" "$RUNTIME_ROOT/libexec" \
    "$RUNTIME_ROOT/share/licenses/openssl"
  install -m 0755 "$OUTPUT_BIN_DIR/prview" "$RUNTIME_ROOT/libexec/prview"
  rm -f "$OUTPUT_BIN_DIR/prview"
  stage_relocatable_openssl \
    "$RUNTIME_ROOT/lib" \
    "$RUNTIME_ROOT/share/licenses/openssl"
  relocate_binary_openssl \
    "$RUNTIME_ROOT/libexec/prview" \
    "@loader_path/../lib/libssl.3.dylib" \
    "@loader_path/../lib/libcrypto.3.dylib"
  assert_no_homebrew_load_commands "$RUNTIME_ROOT/libexec/prview"
  assert_no_homebrew_load_commands "$RUNTIME_ROOT/lib/libssl.3.dylib"
  assert_no_homebrew_load_commands "$RUNTIME_ROOT/lib/libcrypto.3.dylib"
  write_prview_wrapper "$OUTPUT_BIN_DIR/prview" "$RUNTIME_ROOT/libexec/prview"
  OPENSSL_STAGED_VERSION="$OPENSSL_REDIS_VERSION"
  OPENSSL_STAGED_LIBSSL="$OPENSSL_LIBSSL_SHA256"
  OPENSSL_STAGED_LIBCRYPTO="$OPENSSL_LIBCRYPTO_SHA256"
  python3 - "$SCRIPT_DIR/lib/published-foundation-digests.json" \
    "$OUTPUT_BIN_DIR/aicx" "$OUTPUT_BIN_DIR/aicx-mcp" <<'PY'
import hashlib
import json
import pathlib
import sys

pins = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {item["name"]: item["sha256"] for item in pins["artifacts"]}
for path in (pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    want = expected.get(path.name)
    if want is None:
        raise SystemExit(f"no published digest pin for {path.name}")
    if digest != want:
        raise SystemExit(
            f"published {path.name} digest {digest} is not the pinned upstream bytes"
        )
PY
fi

"$OUTPUT_BIN_DIR/loct${EXE_SUFFIX}" --version | grep -F "$LOCTREE_VERSION" >/dev/null
"$OUTPUT_BIN_DIR/aicx${EXE_SUFFIX}" --version | grep -F "$AICX_VERSION" >/dev/null
"$OUTPUT_BIN_DIR/prview${EXE_SUFFIX}" --version | grep -F "$PRVIEW_VERSION" >/dev/null

python3 - "$OUTPUT_BIN_DIR" "$LOCTREE_VERSION" "$AICX_VERSION" "$PRVIEW_VERSION" \
  "$LOCTREE_REVISION" "$AICX_REVISION" "$PRVIEW_REVISION" \
  "$LOCTREE_ARCHIVE_URL" "$LOCTREE_ARCHIVE_SHA256" \
  "$AICX_ARCHIVE_URL" "$AICX_ARCHIVE_SHA256" \
  "$PRVIEW_URL" "$PRVIEW_SHA256" \
  "$LOCTREE_PACKAGE" "$AICX_PACKAGE" \
  "$OPENSSL_STAGED_VERSION" "$OPENSSL_STAGED_LIBSSL" "$OPENSSL_STAGED_LIBCRYPTO" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
versions = {"loctree": sys.argv[2], "aicx": sys.argv[3], "prview": sys.argv[4]}
loctree_revision, aicx_revision, prview_revision = sys.argv[5:8]
loctree_url, loctree_sha256 = sys.argv[8:10]
aicx_url, aicx_sha256 = sys.argv[10:12]
prview_url, prview_sha256 = sys.argv[12:14]
loctree_package, aicx_package = sys.argv[14:16]
openssl_version, openssl_libssl, openssl_libcrypto = sys.argv[16:19]
files = {}
for path in sorted(root.iterdir()):
    if path.is_file() and os.access(path, os.X_OK):
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
source_archives = {
    "loctree": {
        "channel": "npm",
        "package": loctree_package,
        "url": loctree_url,
        "sha256": loctree_sha256,
    },
    "aicx": {
        "channel": "npm",
        "package": aicx_package,
        "url": aicx_url,
        "sha256": aicx_sha256,
    },
    "prview": {
        "channel": "github-release",
        "url": prview_url,
        "sha256": prview_sha256,
    },
}
licenses = {
    "loctree": "BUSL-1.1",
    "aicx": "BUSL-1.1",
    "prview": "BUSL-1.1",
}
if openssl_version:
    source_archives["openssl"] = {
        "channel": "homebrew-bottle-dylib",
        "version": openssl_version,
        "libssl_sha256": openssl_libssl,
        "libcrypto_sha256": openssl_libcrypto,
        "license": "Apache-2.0",
    }
    licenses["openssl"] = "Apache-2.0"
payload = {
    "schema": "io.vetcoders.vibecrafted.runtime-foundations.v1",
    "versions": versions,
    "source_revisions": {
        "loctree": loctree_revision,
        "aicx": aicx_revision,
        "prview": prview_revision,
    },
    "source_archives": source_archives,
    "licenses": licenses,
    "files": files,
}
(root.parent / "runtime-foundations.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
