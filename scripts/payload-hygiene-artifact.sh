#!/usr/bin/env bash
# Run the payload anonymity gate against an artifact that already exists.
#
# The two release scripts gate themselves before they sign or publish, but a
# release costs half an hour of machine time and every artifact built before
# this gate existed is still sitting in dist/. An operator must be able to ask
# "does this shipped file name me?" of the bytes in hand, not only of bytes
# about to be produced.
#
#   scripts/payload-hygiene-artifact.sh dist/Vibecrafted.app
#   scripts/payload-hygiene-artifact.sh dist/Vibecrafted_4.1.0-20260817-237d2814.dmg
#   scripts/payload-hygiene-artifact.sh dist/Vibecrafted_4.1.0-...-portable.tar.gz
#
# Disk images are attached read-only and non-browsable and are detached again
# on every exit path, including a signal. Archives are extracted into a temp
# directory that is removed the same way. Nothing is ever written to the
# artifact itself.
#
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by VetCoders (c)2024-2026 LibraxisAI
set -euo pipefail

die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/lib/payload-hygiene.sh"

ARTIFACT="${1:-}"
[[ -n "$ARTIFACT" ]] || die "usage: $0 <path to .app, .dmg, .tar.gz or directory>"
[[ -e "$ARTIFACT" ]] || die "no such artifact: $ARTIFACT"
ARTIFACT="$(cd "$(dirname "$ARTIFACT")" && printf '%s/%s' "$(pwd)" "$(basename "$ARTIFACT")")"

MOUNT_POINT=""
SCRATCH=""
cleanup() {
  # Detach first: a mounted image is a host-wide resource, a temp directory is
  # not. If the removal below ever hangs, the volume must already be gone.
  [[ -z "$MOUNT_POINT" ]] || hdiutil detach "$MOUNT_POINT" -quiet 2>/dev/null || true
  [[ -z "$SCRATCH" ]] || rm -rf "$SCRATCH"
}
trap cleanup EXIT INT TERM HUP

ROOT=""
LABEL="$(basename "$ARTIFACT")"
case "$ARTIFACT" in
  *.dmg)
    command -v hdiutil >/dev/null 2>&1 || die "hdiutil is required to inspect a .dmg"
    SCRATCH="$(mktemp -d)"
    MOUNT_POINT="$SCRATCH/mnt"
    mkdir -p "$MOUNT_POINT"
    hdiutil attach -readonly -nobrowse -mountpoint "$MOUNT_POINT" "$ARTIFACT" >/dev/null
    ROOT="$MOUNT_POINT"
    ;;
  *.tar.gz|*.tgz)
    SCRATCH="$(mktemp -d)"
    tar -xzf "$ARTIFACT" -C "$SCRATCH"
    ROOT="$SCRATCH"
    ;;
  *)
    [[ -d "$ARTIFACT" ]] || die "unsupported artifact kind: $ARTIFACT"
    ROOT="$ARTIFACT"
    ;;
esac

# The donors must be named here, exactly as the builder names them. MEASURED on
# the first run of this script against the 4.1.0 DMG: without them the gate
# reported 7 offending files and called the rest clean, while the same payload
# carries 277 occurrences of the vc-terminal donor root inside the bundled
# alacritty. A standalone gate that is weaker than the in-build gate does not
# merely miss things — it certifies them.
TERMINAL_DONOR="${VIBECRAFTED_TERMINAL_REPO:-$REPO_ROOT/../vc-terminal}"
FRAME_DONOR="${VIBECRAFTED_FRAME_REPO:-$REPO_ROOT/../vc-frame}"
# Resolve, never concatenate: a prefix that still contains `..` matches nothing,
# which is the 4.1.0 W0-a defect reproduced inside its own detector.
for donor in TERMINAL_DONOR FRAME_DONOR; do
  resolved="$(cd "${!donor}" >/dev/null 2>&1 && pwd || true)"
  if [[ -n "$resolved" ]]; then
    printf -v "$donor" '%s' "$resolved"
  else
    printf 'note: donor %s is not checked out; its paths cannot be gated\n' \
      "${!donor}" >&2
    printf -v "$donor" '%s' ""
  fi
done
export TERMINAL_DONOR FRAME_DONOR

# The literal set comes from the environment of whoever runs this, which is the
# right question for a locally built artifact: it asks whether THIS host is
# named. An artifact built elsewhere is a different question and this gate does
# not pretend to answer it.
assert_payload_is_anonymous "$ROOT" "$LABEL"
printf '\n%s does not name this build host.\n' "$LABEL"
