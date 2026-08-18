#!/usr/bin/env bash
# Build the portable Vibecrafted release channel: one provenance-bound source
# distribution tarball that installs on every system the notarized macOS DMG
# does not serve (Linux, WSL2, and any macOS operator who wants the CLI runtime
# without the desktop app).
#
# The DMG channel ships signed Mach-O bytes. This channel cannot: there is no
# code-signing authority for Linux, and a bare tarball of a git checkout proves
# nothing. So the trust carrier here is the closed `source-provenance.json`
# written by scripts/distribution_manifest.py: an allowlisted projection of the
# repository whose distribution-tree digest is bound to one exact commit. The
# builder does not merely emit that carrier — it extracts what it wrote and
# re-validates it against the claimed revision before the bytes are allowed to
# leave this machine.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${VIBECRAFTED_RELEASE_DIR:-$REPO_ROOT/dist}"
OWNER_REPO="${VIBECRAFTED_RELEASE_REPO:-vetcoders/vibecrafted}"
VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"
RELEASE_DATE="${VIBECRAFTED_RELEASE_DATE:-$(date -u +%Y%m%d)}"
ROOT_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
PORTABLE_NAME="Vibecrafted_${VERSION}-${RELEASE_DATE}-${ROOT_SHA:0:8}-portable.tar.gz"
PORTABLE="$DIST_DIR/$PORTABLE_NAME"
PORTABLE_CHECKSUM="$PORTABLE.sha256"
PORTABLE_OUTPUT="$DIST_DIR/portable-output.json"
ARCHIVE_ROOT_NAME="vibecrafted-${VERSION}"
MANIFEST="$REPO_ROOT/scripts/distribution_manifest.py"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

[[ "$RELEASE_DATE" =~ ^[0-9]{8}$ ]] || die "VIBECRAFTED_RELEASE_DATE must be YYYYMMDD"
require git
require python3
require tar

# The distribution writer already refuses to include a byte it cannot prove
# against the claimed commit, so a dirty tree would fail deep inside the packer
# with a per-file message. Fail here instead, where the operator can read why.
test -z "$(git -C "$REPO_ROOT" status --porcelain)" \
  || die "source tree is dirty; the portable carrier must name one exact commit"

mkdir -p "$DIST_DIR"
rm -f "$PORTABLE" "$PORTABLE_CHECKSUM" "$PORTABLE_OUTPUT"

# The packer publishes the candidate with os.replace(), which cannot cross a
# filesystem boundary — and on this project the checkout commonly lives on a
# different volume than TMPDIR. Stage beside the repository instead: outside the
# source root (the packer refuses to observe its own bytes) but on the same
# device as dist/. Override for hosts where the parent is not writable.
WORK_PARENT="${VIBECRAFTED_PORTABLE_WORKDIR:-$(dirname "$REPO_ROOT")}"
test -w "$WORK_PARENT" \
  || die "portable work parent is not writable: $WORK_PARENT (set VIBECRAFTED_PORTABLE_WORKDIR)"
WORK_DIR="$(mktemp -d "$WORK_PARENT/.vibecrafted-portable.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
VERIFY_DIR="$WORK_DIR/verify"
mkdir -p "$VERIFY_DIR"

# The packer refuses to write into the tree it is packing — an archive that can
# observe its own bytes is not a projection of a commit. Build outside, then let
# the packer's own publisher move the candidate into dist/ under its rules.
log "packing $PORTABLE_NAME from $ROOT_SHA"
python3 "$MANIFEST" archive \
  --source "$REPO_ROOT" \
  --output "$WORK_DIR/$PORTABLE_NAME" \
  --publish-output "$PORTABLE" \
  --root-name "$ARCHIVE_ROOT_NAME" \
  --owner-repo "$OWNER_REPO" \
  --source-revision "$ROOT_SHA" >/dev/null

test -s "$PORTABLE" || die "the packer produced no archive at $PORTABLE"

log "self-verifying the packed bytes"
tar -xzf "$PORTABLE" -C "$VERIFY_DIR"
test -d "$VERIFY_DIR/$ARCHIVE_ROOT_NAME" \
  || die "archive does not unpack into a single $ARCHIVE_ROOT_NAME root"
python3 "$MANIFEST" check \
  --root "$VERIFY_DIR/$ARCHIVE_ROOT_NAME" \
  --expected-owner-repo "$OWNER_REPO" \
  --expected-source-revision "$ROOT_SHA" >/dev/null

# install.sh is the only supported consumer of this artifact. If the packed tree
# cannot answer for itself, the tarball is not installable and must not ship.
# The entrypoint is `bash install.sh`, not `./install.sh`: the packer canonicalises
# modes and the repository file carries no executable bit, so do not test for one.
test -f "$VERIFY_DIR/$ARCHIVE_ROOT_NAME/install.sh" \
  || die "packed payload has no install.sh"
bash -n "$VERIFY_DIR/$ARCHIVE_ROOT_NAME/install.sh"
bash "$VERIFY_DIR/$ARCHIVE_ROOT_NAME/install.sh" --help >/dev/null \
  || die "packed install.sh cannot print its own usage"

# This channel ships a projection of a commit rather than compiled bytes, so it
# has always been the cleaner of the two — measured on the 4.1.0 tarball, the
# only `/Users|/home` matches were documentation placeholders. That is a fact
# about one build, not a property of the channel: the packer's allowlist can
# grow, and a generated file can arrive carrying an absolute path. Ask the
# extracted tree the same question the DMG channel is asked.
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/lib/payload-hygiene.sh"
log "asserting the packed payload does not name the build host"
assert_payload_is_anonymous "$VERIFY_DIR/$ARCHIVE_ROOT_NAME" "$PORTABLE_NAME"

(
  cd "$DIST_DIR"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$PORTABLE_NAME" > "$PORTABLE_NAME.sha256"
  else
    sha256sum "$PORTABLE_NAME" > "$PORTABLE_NAME.sha256"
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -c "$PORTABLE_NAME.sha256" >/dev/null
  else
    sha256sum -c "$PORTABLE_NAME.sha256" >/dev/null
  fi
)

log "writing $PORTABLE_OUTPUT"
PROVENANCE="$VERIFY_DIR/$ARCHIVE_ROOT_NAME/source-provenance.json"
export PORTABLE_NAME PORTABLE PORTABLE_OUTPUT PROVENANCE ARCHIVE_ROOT_NAME
export OWNER_REPO VERSION ROOT_SHA
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

archive = Path(os.environ["PORTABLE"])
provenance = json.loads(Path(os.environ["PROVENANCE"]).read_text(encoding="utf-8"))
payload = provenance["payload"]

digest = hashlib.sha256()
with archive.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)

document = {
    "schema": "io.vetcoders.vibecrafted.portable-output.v1",
    "archive": {
        "path": os.environ["PORTABLE_NAME"],
        "root_name": os.environ["ARCHIVE_ROOT_NAME"],
        "sha256": digest.hexdigest(),
        "size": archive.stat().st_size,
    },
    "product": {
        "channel": "portable",
        "install_command": "bash install.sh",
        "supported": ["linux", "wsl2", "macos-cli"],
        "version": os.environ["VERSION"],
    },
    "provenance": {
        "algorithm": payload["algorithm"],
        "entry_count": payload["entry_count"],
        "owner_repo": provenance["owner_repo"],
        "schema": provenance["schema"],
        "tree_sha256": payload["tree_sha256"],
    },
    "source_revisions": {"vibecrafted": os.environ["ROOT_SHA"]},
}
Path(os.environ["PORTABLE_OUTPUT"]).write_text(
    json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

printf '\nPortable channel built\n  archive:  %s\n  checksum: %s\n  manifest: %s\n' \
  "$PORTABLE" "$PORTABLE_CHECKSUM" "$PORTABLE_OUTPUT"
