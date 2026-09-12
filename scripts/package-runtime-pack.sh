#!/usr/bin/env bash
set -euo pipefail

die() { printf 'Runtime Pack packaging failed: %s\n' "$*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/lib/macho-signing.sh"

payload_root=""
output=""
source_revision=""
terminal_revision=""
frame_revision=""
version=""
platform=""
architecture=""
codesign_identity=""
codesign_keychain=""
while (($#)); do
  case "$1" in
    --payload-root)
      (($# >= 2)) || die "--payload-root requires a path"
      payload_root="$2"
      shift 2
      ;;
    --output)
      (($# >= 2)) || die "--output requires a path"
      output="$2"
      shift 2
      ;;
    --source-revision|--terminal-revision|--frame-revision|--version|--platform|--architecture)
      (($# >= 2)) || die "$1 requires a value"
      case "$1" in
        --source-revision) source_revision="$2" ;;
        --terminal-revision) terminal_revision="$2" ;;
        --frame-revision) frame_revision="$2" ;;
        --version) version="$2" ;;
        --platform) platform="$2" ;;
        --architecture) architecture="$2" ;;
      esac
      shift 2
      ;;
    --codesign-identity)
      (($# >= 2)) || die "--codesign-identity requires a value"
      codesign_identity="$2"
      shift 2
      ;;
    --codesign-keychain)
      (($# >= 2)) || die "--codesign-keychain requires a value"
      codesign_keychain="$2"
      shift 2
      ;;
    --help|-h)
      printf 'usage: %s --payload-root <dir> --output <RuntimePack.tar.gz> --source-revision <sha> --terminal-revision <sha> --frame-revision <sha> --version <version> --platform <platform> --architecture <arch> [--codesign-identity <identity> [--codesign-keychain <path>]]\n' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

for required_value in output source_revision terminal_revision frame_revision version platform architecture; do
  [[ -n "${!required_value}" ]] || die "--$required_value is required"
done
[[ -n "$payload_root" ]] || die "--payload-root is required"

work="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-runtime-pack-build.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT INT TERM HUP
root="$work/VibecraftedRuntime"
mkdir -p "$root"
payload_root="$(cd "$payload_root" 2>/dev/null && pwd -P)" \
  || die "cannot resolve payload root"
cp -R "$payload_root/." "$root/"

# Finder metadata is mutable host state, not product input. Remove it at the
# final byte-owner boundary; the closed provenance writer below rejects a race
# that recreates it before inventory, and tar excludes a later race.
find "$root" -type f -name '.DS_Store' -delete

if find "$root" -type l -print -quit | grep -q .; then
  die "standalone Runtime Pack contains symlinks"
fi
for required in \
  VERSION bin/python3 bin/vibecrafted bin/vc-start bin/vc-terminal bin/vc-frame \
  bin/scaffold-doctor \
  libexec/vc-frame scripts/vibecrafted scripts/vetcoders_install.py \
  vibecrafted-core/vibecrafted_core/runtime_pack_contract.py; do
  [[ -e "$root/$required" ]] || die "standalone Runtime Pack is missing $required"
done

# The pack staging tree is the final byte owner. Sign only after every helper
# has reached its canonical Runtime Pack path, then verify those exact bytes
# before provenance captures them and before tar creates the carrier.
if [[ -n "$codesign_identity" ]]; then
  [[ "$platform" == "darwin-arm64" ]] \
    || die "--codesign-identity is valid only for a Darwin Runtime Pack"
  # The static linter cannot see that the sourced helper consumes these globals.
  # shellcheck disable=SC2034
  SIGNING_IDENTITY="$codesign_identity"
  # shellcheck disable=SC2034
  CODESIGN_KEYCHAIN_ARGS=()
  if [[ -n "$codesign_keychain" ]]; then
    # shellcheck disable=SC2034
    CODESIGN_KEYCHAIN_ARGS=(--keychain "$codesign_keychain")
  fi
  sign_macho_tree "$root" || die "could not sign final Runtime Pack Mach-O payload"
  verify_macho_tree "$root" 1 \
    || die "final Runtime Pack Mach-O signature preflight failed"
elif [[ -n "$codesign_keychain" ]]; then
  die "--codesign-keychain requires --codesign-identity"
fi

# Signing above takes minutes; Finder writes .DS_Store into any staging
# directory the operator happens to have open. That metadata is never a
# product input, so sweep it once more at the last sanctioned mutation point.
# Any file that appears after this line still fails the closed inventory.
find "$root" -type f -name '.DS_Store' -delete

# stage-runtime-foundations records source-built bytes before the release
# builder signs Mach-O files. This pack staging tree is the final byte owner,
# so rebind the manifest only after the last signing mutation and fail closed
# if anything changes again before provenance captures the payload.
PYTHONPATH="$root/vibecrafted-core" "$root/bin/python3" \
  -m vibecrafted_core.runtime_pack_contract refresh-foundations \
  --root "$root" >/dev/null

PYTHONPATH="$root/vibecrafted-core" "$root/bin/python3" \
  -m vibecrafted_core.runtime_pack_contract write \
  --root "$root" \
  --carrier-basename "$(basename "$output")" \
  --version "$version" \
  --platform "$platform" \
  --architecture "$architecture" \
  --source-revision "$source_revision" \
  --terminal-revision "$terminal_revision" \
  --frame-revision "$frame_revision" >/dev/null

mkdir -p "$(dirname "$output")"
candidate="$work/$(basename "$output")"
COPYFILE_DISABLE=1 tar \
  --exclude='.DS_Store' --exclude='*/.DS_Store' \
  -czf "$candidate" -C "$work" VibecraftedRuntime
mv "$candidate" "$output"
(
  cd "$(dirname "$output")"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$(basename "$output")" > "$(basename "$output").sha256"
  else
    sha256sum "$(basename "$output")" > "$(basename "$output").sha256"
  fi
)
printf '%s\n' "$output"
