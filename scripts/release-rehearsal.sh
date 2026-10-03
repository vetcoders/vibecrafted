#!/usr/bin/env bash
# In-flight release rehearsal: the delivery verifier that does not need a
# publish button.
#
# Prints OLD vs CURRENT identity, dry-runs the real release recipes (`make -n`),
# runs the portable inventory and (when asked) payload-hygiene against bytes
# already on disk, and fail-closes if a publish/tag/upload/release-build
# command would be invoked. It does not build a DMG, does not notarize, and
# does not call cargo --release.
#
#   make release-rehearsal
#   make release-rehearsal ARTIFACT=dist/Vibecrafted.app
#   scripts/release-rehearsal.sh dist/Vibecrafted.app
#
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by VetCoders (c)2024-2026 LibraxisAI
set -euo pipefail

die() { printf 'FATAL: release-rehearsal: %s\n' "$*" >&2; exit 1; }
log() { printf '\n==> %s\n' "$*"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DIST_DIR="${VIBECRAFTED_RELEASE_DIR:-$REPO_ROOT/dist}"
VERSION_FILE="${VERSION_FILE:-$REPO_ROOT/VERSION}"
PAYLOAD_HYGIENE_SCRIPT="$REPO_ROOT/scripts/payload-hygiene-artifact.sh"
PORTABLE_SCRIPT="$REPO_ROOT/scripts/build-portable-release.sh"
CHECK_PORTABLE_SCRIPT="$REPO_ROOT/scripts/check-portable.sh"
PUBLISH_SCRIPT="$REPO_ROOT/scripts/publish-vibecrafted-release.sh"
RELEASE_SCRIPT="$REPO_ROOT/scripts/build-vibecrafted-release.sh"
MANIFEST="$REPO_ROOT/scripts/distribution_manifest.py"

# Fail-close: the only make this script may exec is a dry-run. The only bash
# helpers it may exec for real are `bash -n <script>` and payload-hygiene on an
# artifact that already exists. Everything else that tags, uploads, notarizes,
# or cargo --release's is a publish button and must die here, not later.
command_is_forbidden() {
  local joined="$1"
  local -a words
  read -r -a words <<<"$joined"
  local prog="${words[0]##*/}"

  case "$prog" in
    cargo|notarytool|stapler|gh|npm|twine|altool)
      return 0
      ;;
    make)
      case "$joined" in
        make\ --no-print-directory\ -n\ *) return 1 ;;
        *) return 0 ;;
      esac
      ;;
    git)
      case "$joined" in
        *' tag '*|*' tag'|git\ tag*|*' push '*|*' push'|git\ push*)
          return 0
          ;;
      esac
      return 1
      ;;
    bash)
      case "$joined" in
        bash\ -n\ *) return 1 ;;
        *payload-hygiene-artifact.sh*) return 1 ;;
        *publish-vibecrafted-release.sh*|*build-vibecrafted-release.sh*|*build-portable-release.sh|*check-portable.sh)
          return 0
          ;;
      esac
      return 1
      ;;
  esac
  case "$joined" in
    *notarytool*|*stapler\ *|*'--notarize'*|*'cargo --release'*|*'gh release'*|*'git tag'*)
      return 0
      ;;
  esac
  return 1
}

run_allowed() {
  local joined
  joined="$*"
  if command_is_forbidden "$joined"; then
    die "refuse: would invoke a publish/tag/upload/release-build command: $joined"
  fi
  "$@"
}

latest_artifact() {
  local dist="$1"
  local candidate="" newest="" newest_epoch=0 epoch=0
  [[ -d "$dist" ]] || return 0
  shopt -s nullglob
  local files=(
    "$dist"/Vibecrafted.app
    "$dist"/Vibecrafted_*.dmg
    "$dist"/Vibecrafted_*-portable.tar.gz
  )
  shopt -u nullglob
  for candidate in "${files[@]}"; do
    [[ -e "$candidate" ]] || continue
    epoch="$(stat -f '%m' "$candidate" 2>/dev/null || stat -c '%Y' "$candidate")"
    if [[ "$epoch" -gt "$newest_epoch" ]]; then
      newest_epoch="$epoch"
      newest="$candidate"
    fi
  done
  [[ -n "$newest" ]] || return 0
  printf '%s\n' "$newest"
}

artifact_identity() {
  local path="$1"
  local size mtime
  if [[ -f "$path" ]]; then
    size="$(wc -c < "$path" | tr -d '[:space:]') bytes"
  else
    size="$(du -sk "$path" | awk '{print $1 "k"}')"
  fi
  mtime="$(date -u -r "$path" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null \
    || date -u -d "@$(stat -c '%Y' "$path")" '+%Y-%m-%dT%H:%M:%SZ')"
  printf '%s  (%s, %s)\n' "$path" "$size" "$mtime"
}

[[ "${1:-}" != "-h" && "${1:-}" != "--help" ]] || {
  printf 'usage: %s [ARTIFACT]\n' "$0"
  printf '  In-flight release verifier. Does not tag, notarize, upload, or cargo --release.\n'
  exit 0
}

ARTIFACT_ARG="${1:-${ARTIFACT:-}}"

log "identity (OLD / CURRENT)"
current_version="$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null || true)"
[[ -n "$current_version" ]] || die "VERSION file missing or empty: $VERSION_FILE"
old_tag="$(run_allowed git describe --tags --abbrev=0 2>/dev/null || true)"
[[ -n "$old_tag" ]] || old_tag="(no local tag)"
head_sha="$(run_allowed git rev-parse HEAD)"
head_sha8="${head_sha:0:8}"
if run_allowed git rev-parse --verify "refs/tags/v${current_version}" >/dev/null 2>&1; then
  tag_state="exists"
else
  tag_state="missing"
fi
tree_state="clean"
test -z "$(run_allowed git status --porcelain)" || tree_state="dirty"

printf 'OLD:       %s\n' "$old_tag"
printf 'CURRENT:   %s\n' "$current_version"
printf 'HEAD:      %s\n' "$head_sha"
printf 'tag-state: v%s %s\n' "$current_version" "$tag_state"
printf 'tree:      %s\n' "$tree_state"

log "latest local artifact"
latest="$(latest_artifact "$DIST_DIR" || true)"
if [[ -n "$latest" ]]; then
  printf 'artifact:  %s' "$(artifact_identity "$latest")"
else
  printf 'artifact:  none\n'
fi

log "dry-run composition (NOT executed)"
# Labelled make -n of the real buttons. Printing them is the rehearsal;
# executing them is the fail-close this script exists to prevent.
run_allowed make --no-print-directory -n publish-release
run_allowed make --no-print-directory -n release
run_allowed make --no-print-directory -n portable
if [[ -n "${ARTIFACT_ARG:-$latest}" ]]; then
  run_allowed make --no-print-directory -n payload-hygiene \
    ARTIFACT="${ARTIFACT_ARG:-$latest}"
else
  printf 'payload-hygiene: skipped (no artifact)\n'
fi

log "syntax of the existing release helpers"
run_allowed bash -n "$PAYLOAD_HYGIENE_SCRIPT"
run_allowed bash -n "$PORTABLE_SCRIPT"
run_allowed bash -n "$CHECK_PORTABLE_SCRIPT"
run_allowed bash -n "$PUBLISH_SCRIPT"
run_allowed bash -n "$RELEASE_SCRIPT"
run_allowed bash -n "$0"

log "portable dry inventory (distribution_manifest REQUIRED_*)"
run_allowed python3 - "$REPO_ROOT" "$MANIFEST" <<'PY'
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

repo = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("distribution_manifest", manifest_path)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load {manifest_path}")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

missing: list[str] = []
for relative in module.REQUIRED_FILES:
    path = repo / relative
    if not path.is_file():
        missing.append(f"file:{relative}")
for relative in module.REQUIRED_DIRECTORIES:
    path = repo / relative
    if not path.is_dir():
        missing.append(f"dir:{relative}")
for relative in module.REQUIRED_SURFACE_FILES.values():
    path = repo / relative
    if not path.exists():
        missing.append(f"surface:{relative}")
if missing:
    print("portable inventory: MISSING", file=sys.stderr)
    for item in missing:
        print(f"  {item}", file=sys.stderr)
    raise SystemExit(1)
print(
    "portable inventory: "
    f"{len(module.REQUIRED_FILES)} files, "
    f"{len(module.REQUIRED_DIRECTORIES)} dirs, "
    f"{len(module.REQUIRED_SURFACE_FILES)} surfaces present"
)
PY

if [[ -n "$ARTIFACT_ARG" ]]; then
  log "payload-hygiene on ${ARTIFACT_ARG}"
  run_allowed bash "$PAYLOAD_HYGIENE_SCRIPT" "$ARTIFACT_ARG"
else
  log "payload-hygiene skipped (pass ARTIFACT=<path> to scan bytes already on disk)"
  printf 'note: latest artifact was not scanned; rehearsal does not mount a DMG by default.\n'
fi

printf '\nrelease-rehearsal: pass (no publish)\n'
printf 'CURRENT %s (%s)  OLD %s\n' "$current_version" "$head_sha8" "$old_tag"
