#!/usr/bin/env bash
# Test-only driver for release single-flight ordering.
#
# This is not a production entrypoint and it is not a success bypass.
# It sources the real lock, selection, snapshot and keychain libraries and
# follows the production order: acquire → output lock → claim → materialize →
# require_clean_repo SOURCE_ROOT → handshake → keychain/reap/unlock.
# It never calls runtime_pack_selection_publish.

set -euo pipefail

die() {
  printf 'FATAL: %s\n' "$*" >&2
  exit 1
}

MODE="runtime-pack"
for argument in "$@"; do
  case "$argument" in
    --runtime-pack-only) MODE="runtime-pack" ;;
    --notarize-only) MODE="notarize" ;;
    --help|-h)
      echo "usage: $0 [--runtime-pack-only|--notarize-only]" >&2
      exit 0
      ;;
    *)
      echo "usage: $0 [--runtime-pack-only|--notarize-only]" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(pwd)"
STAGE="${RELEASE_SINGLE_FLIGHT_STAGE:?RELEASE_SINGLE_FLIGHT_STAGE is required}"
PRODUCTION_BUILDER="${PRODUCTION_BUILDER:-}"

. "$REPO_ROOT/scripts/lib/runtime-pack-selection.sh"
. "$REPO_ROOT/scripts/lib/release-single-flight.sh"
. "$REPO_ROOT/scripts/lib/donor-snapshot.sh"
. "$REPO_ROOT/scripts/lib/keychain-session.sh"

# Exact production cleanliness gate, extracted without executing the builder.
if [[ -z "$PRODUCTION_BUILDER" || ! -f "$PRODUCTION_BUILDER" ]]; then
  die "PRODUCTION_BUILDER must name the tracked build-vibecrafted-release.sh"
fi
eval "$(awk '/^require_clean_repo\(\)/,/^}/' "$PRODUCTION_BUILDER")"
declare -F require_clean_repo >/dev/null \
  || die "could not extract require_clean_repo from $PRODUCTION_BUILDER"

ROOT_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
SIGNING_KEYCHAIN_LABEL="vibecrafted-signing-$$"

release_single_flight_acquire "$REPO_ROOT" \
  || die "another release already owns this checkout's shared build state"

cleanup() {
  if declare -F keychain_session_end >/dev/null 2>&1; then
    keychain_session_end "$SIGNING_KEYCHAIN_LABEL" || true
  fi
  if declare -F donor_snapshot_reap >/dev/null 2>&1; then
    donor_snapshot_reap || true
  fi
  release_single_flight_release
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 129' HUP

DIST_DIR="${VIBECRAFTED_RELEASE_DIR:-$REPO_ROOT/dist}"
case "$DIST_DIR" in
  /*) ;;
  *) DIST_DIR="$PWD/$DIST_DIR" ;;
esac
release_single_flight_acquire_output "$REPO_ROOT" "$DIST_DIR" \
  || die "another release already owns this output directory"

SOURCE_ROOT="$REPO_ROOT/build/unified-release/donor-snapshots/vibecrafted"
RUNTIME_PACK_SELECTION_ATTEMPT=""

mkdir -p "$STAGE"
printf '%s\n' "$$" > "$STAGE/locked"
printf '%s\n' "${RELEASE_SINGLE_FLIGHT_LOCK_FD:-}" > "$STAGE/lock-fd"

if [[ "$MODE" != "notarize" ]]; then
  RUNTIME_PACK_SELECTION_ATTEMPT="$(runtime_pack_selection_attempt_id)"
  runtime_pack_selection_begin "$REPO_ROOT" "$RUNTIME_PACK_SELECTION_ATTEMPT" \
    "$ROOT_SHA" \
    || die "could not mark the Runtime Pack build attempt as pending"
  DONOR_SNAPSHOT_OWNER="$RUNTIME_PACK_SELECTION_ATTEMPT"
  export DONOR_SNAPSHOT_OWNER
  require_clean_repo "$REPO_ROOT" vibecrafted
  donor_snapshot_create "$REPO_ROOT" "$SOURCE_ROOT" "$ROOT_SHA"
  [[ "$DONOR_SNAPSHOT_HEAD" == "$ROOT_SHA" ]] \
    || die "main snapshot HEAD $DONOR_SNAPSHOT_HEAD is not the bound revision"
  require_clean_repo "$SOURCE_ROOT" vibecrafted
  printf '%s\n' "$DONOR_SNAPSHOT_HEAD" > "$STAGE/snapshot"
  printf '%s\n' "$SOURCE_ROOT" > "$STAGE/snapshot-path"
  printf '%s\n' "$(donor_snapshot_owner_stamp_file "$SOURCE_ROOT")" \
    > "$STAGE/owner-stamp-path"
  cp "$(runtime_pack_selection_file "$REPO_ROOT")" "$STAGE/selection"
else
  : > "$STAGE/notarize-locked"
fi

if [[ -n "${RELEASE_SINGLE_FLIGHT_KEYCHAIN:-}" ]]; then
  keychain_session_begin "$SIGNING_KEYCHAIN_LABEL"
  printf '%s\n' "${KEYCHAIN_SESSION_PATH:-}" > "$STAGE/keychain-path"
  trap -p EXIT > "$STAGE/trap-exit" || true
fi

waited=0
while [[ ! -e "$STAGE/continue" ]]; do
  sleep 0.05
  waited=$((waited + 1))
  (( waited < 600 )) || die "driver: timed out waiting for continue"
done
: > "$STAGE/continued-after-wait"
