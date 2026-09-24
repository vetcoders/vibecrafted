#!/usr/bin/env bash
# Honest name for the multi-arch Linux Runtime Pack assembler.
# Historical filename `build-linux-arm64-runtime-pack.sh` stays in the portable
# REQUIRED_FILES inventory; this wrapper is what CI and docs should call.
#
# Two lanes:
#   build-linux-runtime-pack.sh <out.tar.gz>
#       CI lane: assemble only. The caller exports VIBECRAFTED_SOURCE_REVISION
#       and signs the carrier with its own key (install-linux.yml).
#   build-linux-runtime-pack.sh --for-install
#       Local lane behind `make runtime-pack` on Linux: claim the build
#       selection record, assemble from HEAD, sign with the release key the
#       macOS builder uses, and publish the record bare `make install` reads.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
assembler="$here/build-linux-arm64-runtime-pack.sh"
[[ "${1:-}" == "--for-install" ]] || exec "$assembler" "$@"
(($# == 1)) || { printf 'usage: %s --for-install\n' "$0" >&2; exit 2; }

die() { printf 'Linux Runtime Pack build failed: %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Linux" ]] \
  || die "--for-install runs natively on Linux; on macOS \`make runtime-pack\` uses the release builder"
repo_root="$(cd "$here/.." && pwd -P)"

# The pack records HEAD as its source revision and the assembler compiles the
# working tree, so a modified tracked file would ship bytes HEAD never had.
git -C "$repo_root" diff --quiet HEAD -- \
  || die "tracked files differ from HEAD; commit or stash them so the pack's source revision is true"
source_revision="$(git -C "$repo_root" rev-parse HEAD)"

# Everything that can refuse must refuse before the build spends its hour.
keys="${KEYS:-$HOME/.keys}"
signing_key="$keys/vibecrafted-signing.key"
public_key="$repo_root/vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub"
[[ -f "$signing_key" ]] || die "release signing key missing: $signing_key
  The installer verifies every pack against $public_key.
  Either copy the release key to this host (KEYS=<dir> overrides ~/.keys), or
  build with \`bash scripts/build-linux-runtime-pack.sh <out.tar.gz>\`, sign it
  with a key of your own, and install with
  \`VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY=<your.pub> make install RUNTIME_PACK=<out.tar.gz>\`."

missing=()
for tool in cargo curl make npm openssl python3 rustup sha256sum tar uv cargo-leptos wasm-bindgen; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if ((${#missing[@]})); then
  die "missing build tools: ${missing[*]}
  The CI recipe (.github/workflows/install-linux.yml) installs them with:
    sudo apt-get install -y build-essential cmake libclang-dev libprotobuf-dev libgtk-3-dev libxdo-dev libayatana-appindicator3-dev protobuf-compiler rsync
    cargo binstall -y cargo-leptos wasm-bindgen-cli@<version in vibecrafted-server/Cargo.lock>
  plus rustup (https://rustup.rs) and uv (https://astral.sh/uv)."
fi

# shellcheck source=scripts/lib/runtime-pack-selection.sh
. "$here/lib/runtime-pack-selection.sh"
attempt="$(runtime_pack_selection_attempt_id)"
runtime_pack_selection_begin "$repo_root" "$attempt" "$source_revision" \
  || die "could not mark the Runtime Pack build attempt as pending"

case "$(uname -m)" in
  aarch64|arm64) platform="linux-arm64" ;;
  x86_64) platform="linux-x64" ;;
  *) die "unsupported Linux architecture: $(uname -m)" ;;
esac
version="$(tr -d '[:space:]' < "$repo_root/VERSION")"
output="$repo_root/dist/Vibecrafted_RuntimePack_${version}-$(date -u +%Y%m%d)-${source_revision:0:8}-${platform}.tar.gz"
mkdir -p "$repo_root/dist"

VIBECRAFTED_SOURCE_REVISION="$source_revision" \
VIBECRAFTED_RUNTIME_PACK_SELECTION_ATTEMPT="$attempt" \
VIBECRAFTED_RUNTIME_PACK_SIGNING_KEY="$signing_key" \
  "$assembler" "$output"
