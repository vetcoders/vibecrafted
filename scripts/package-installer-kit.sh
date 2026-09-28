#!/usr/bin/env bash
# One installer closure for the app's runtime-pack resources and standalone kit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ $# -ne 2 || ( "$1" != --directory && "$1" != --output ) ]]; then
  printf 'usage: %s --directory <bundle-resources> | --output <kit.tar.gz>\n' "$0" >&2
  exit 2
fi

temporary=""
trap '[[ -z "$temporary" ]] || rm -rf "$temporary"' EXIT
if [[ "$1" == --directory ]]; then
  destination="$2"
else
  temporary="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-installer-kit.XXXXXX")"
  destination="$temporary/Vibecrafted_InstallerKit"
fi

mkdir -p "$destination/lib"
install -m 0755 "$SCRIPT_DIR/install-runtime-pack.sh" "$destination/install-runtime-pack.sh"
# Carry the whole library tree, including helpers added in future releases.
# Source snapshots need not contain Git metadata. Preserve executable modes.
COPYFILE_DISABLE=1 tar --exclude=.DS_Store -cf - -C "$SCRIPT_DIR" lib \
  | tar -xf - -C "$destination"
install -m 0644 \
  "$REPO_ROOT/vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub" \
  "$destination/vibecrafted-signing-v1.pub"
install -m 0644 "$REPO_ROOT/docs/INSTALLER_KIT.md" "$destination/README.md"

if [[ "$1" == --output ]]; then
  mkdir -p "$(dirname "$2")"
  COPYFILE_DISABLE=1 tar -czf "$2" -C "$temporary" Vibecrafted_InstallerKit
  printf '%s\n' "$2"
fi
