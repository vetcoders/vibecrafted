#!/usr/bin/env bash
set -euo pipefail

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

# A --remap-path-prefix whose prefix still contains `..` never matches the path
# the compiler actually sees, because the match is textual. The donor roots used
# to be plain concatenations ("$REPO_ROOT/../vc-terminal"), so both donor remaps
# silently missed every file: measured on the shipped 4.1.0 payload
# (Vibecrafted_4.1.0-20260817-237d2814.dmg, roadmap 4.2.0 cut W0-a), the strings
# `/usr/src/vc-frame` and `/usr/src/vc-terminal` are ABSENT from every binary
# while `/Volumes/<...>/vc-frame` and `/Volumes/<...>/vc-terminal` are present in
# Contents/Helpers/vc-frame, Contents/MacOS/Vibecrafted, Contents/MacOS/voc and
# the bundled alacritty. Resolve the donor roots; never concatenate them.
canonical_dir() {
  local target="$1"
  (cd "$target" >/dev/null 2>&1 && pwd) || die "missing donor directory: $target"
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE="release"
SNAPSHOT_DONORS=0
for argument in "$@"; do
  case "$argument" in
    --app-only) MODE="app" ;;
    --no-notarize) MODE="dmg" ;;
    --notarize-only) MODE="notarize" ;;
    --snapshot-donors) SNAPSHOT_DONORS=1 ;;
    *)
      echo "usage: $0 [--app-only|--no-notarize|--notarize-only] [--snapshot-donors]" >&2
      exit 2
      ;;
  esac
done

# The donor is where the source lives; the repo is what we compile. They differ
# only under --snapshot-donors, where the repo becomes a detached worktree at the
# donor HEAD so a dirty Living Tree donor can still produce an honest receipt.
TERMINAL_DONOR="$(canonical_dir "${VIBECRAFTED_TERMINAL_REPO:-$REPO_ROOT/../vc-terminal}")"
FRAME_DONOR="$(canonical_dir "${VIBECRAFTED_FRAME_REPO:-$REPO_ROOT/../vc-frame}")"
DONOR_SNAPSHOT_ROOT="$REPO_ROOT/build/unified-release/donor-snapshots"
if (( SNAPSHOT_DONORS )); then
  TERMINAL_REPO="$DONOR_SNAPSHOT_ROOT/vc-terminal"
  FRAME_REPO="$DONOR_SNAPSHOT_ROOT/vc-frame"
else
  TERMINAL_REPO="$TERMINAL_DONOR"
  FRAME_REPO="$FRAME_DONOR"
fi
ICON_SOURCE="${VIBECRAFTED_ICON_SOURCE:-$TERMINAL_REPO/assets/icon/vc-terminal-icon.png}"
ICON_REFERENCE="${VIBECRAFTED_ICON_REFERENCE:-$TERMINAL_REPO/assets/icon/terminal.png}"
DIST_DIR="${VIBECRAFTED_RELEASE_DIR:-$REPO_ROOT/dist}"
BUILD_DIR="$REPO_ROOT/build/unified-release"
APP="$DIST_DIR/Vibecrafted.app"
VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"
RELEASE_DATE="${VIBECRAFTED_RELEASE_DATE:-$(date -u +%Y%m%d)}"
ROOT_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
RUNTIME_VERSION="${VERSION}+g${ROOT_SHA:0:8}"
[[ "$RELEASE_DATE" =~ ^[0-9]{8}$ ]] || {
  printf 'FATAL: VIBECRAFTED_RELEASE_DATE must be YYYYMMDD\n' >&2
  exit 1
}
DMG_NAME="Vibecrafted_${VERSION}-${RELEASE_DATE}-${ROOT_SHA:0:8}.dmg"
DMG="$DIST_DIR/$DMG_NAME"
DMG_CHECKSUM="$DMG.sha256"
LEGACY_DMG="$DIST_DIR/Vibecrafted.dmg"
KEYS="${KEYS:-$HOME/.keys}"
SPOT_MONO_FONT="${VIBECRAFTED_SPOT_MONO_FONT:-$KEYS/fonts/SpotMono.ttc}"
SIGNING_IDENTITY_FILE="$KEYS/signing-identity.txt"
CERT_P12="$KEYS/Certificates.p12"
CERT_PASSWORD_FILE="$KEYS/cert_password.txt"
SIGNING_KEY="$KEYS/vibecrafted-signing.key"
NOTARY_ENV="$KEYS/.notary.env"
BUILD_NUMBER="${BUILD_NUMBER:-$(date -u +%Y%m%d%H%M%S)}"
SIGNING_IDENTITY=""
TEMP_KEYCHAIN_PATH=""
SIGNING_KEYCHAIN_LABEL="vibecrafted-signing-$$"
CODESIGN_KEYCHAIN_ARGS=()
export MACOSX_DEPLOYMENT_TARGET=14.0
# Release payloads must not remember the operator account, Cargo registry, or
# living checkout locations through Rust panic/debug metadata.
export RUSTFLAGS="--remap-path-prefix=$REPO_ROOT=/usr/src/vibecrafted --remap-path-prefix=$TERMINAL_DONOR=/usr/src/vc-terminal --remap-path-prefix=$FRAME_DONOR=/usr/src/vc-frame --remap-path-prefix=$TERMINAL_REPO=/usr/src/vc-terminal --remap-path-prefix=$FRAME_REPO=/usr/src/vc-frame --remap-path-prefix=$HOME=/usr/src/operator-home"

# The ephemeral signing keychain is owned by scripts/lib/keychain-session.sh,
# which arms its own EXIT/INT/TERM/HUP traps and chains onto whatever this
# script already had. See that file's header for the 2026-08-15 incident this
# replaces: the block that used to live here restored the DEFAULT keychain and
# deleted the temp keychain, but never put the search LIST back — it trusted
# `delete-keychain` to unlist, which only holds while the keychain file still
# exists. It also took the login session's default keychain, which is a
# host-wide side effect for the entire duration of the release.
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/lib/keychain-session.sh"
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/lib/donor-snapshot.sh"

cleanup() {
  donor_snapshot_reap || true
  keychain_session_end "$SIGNING_KEYCHAIN_LABEL" || true
}
trap cleanup EXIT INT TERM HUP

read_trimmed_file() {
  sed -e 's/[[:space:]]*$//' -e '/^$/d' "$1" | head -n1
}

prepare_signing_identity() {
  SIGNING_IDENTITY="$(read_trimmed_file "$SIGNING_IDENTITY_FILE")"
  [[ -n "$SIGNING_IDENTITY" ]] || die "signing identity is empty"
  if [[ -f "$CERT_P12" && -f "$CERT_PASSWORD_FILE" ]]; then
    local cert_password temp_password
    cert_password="$(read_trimmed_file "$CERT_PASSWORD_FILE")"
    [[ -n "$cert_password" ]] || die "certificate password is empty"

    # The ephemeral keychain lives in its own per-process state directory and
    # is always addressed explicitly. It is never registered in the user's
    # global search list: doing so changes keychain lookup for Codescribe and
    # every other application on the host while a release is running.
    #
    # It deliberately does NOT make this the login session's default keychain.
    # Nothing below needs that: every call names the keychain explicitly. The
    # old `security default-keychain -d user -s "$TEMP_KEYCHAIN_PATH"` is what
    # made Codescribe (and everything else on the host) prompt for a uuidgen
    # password for the whole length of the release.
    KEYCHAIN_SESSION_REGISTER_SEARCH_LIST=0 \
      keychain_session_begin "$SIGNING_KEYCHAIN_LABEL"
    TEMP_KEYCHAIN_PATH="$KEYCHAIN_SESSION_PATH"
    temp_password="$(cat "$(keychain_session_password_file)")"

    security import "$CERT_P12" -k "$TEMP_KEYCHAIN_PATH" -P "$cert_password" \
      -T /usr/bin/codesign >/dev/null
    security set-key-partition-list -S apple-tool:,apple:,codesign: -s \
      -k "$temp_password" "$TEMP_KEYCHAIN_PATH" >/dev/null
    security find-identity -v -p codesigning "$TEMP_KEYCHAIN_PATH" \
      | grep -Fq "$SIGNING_IDENTITY" \
      || die "Developer ID identity is absent from temporary keychain"
    CODESIGN_KEYCHAIN_ARGS=(--keychain "$TEMP_KEYCHAIN_PATH")
    return
  fi
  security find-identity -v -p codesigning | grep -Fq "$SIGNING_IDENTITY" \
    || die "Developer ID identity is not available in the keychain"
}

for command in cargo codesign file git hdiutil install_name_tool make otool uv xcodebuild xcodegen xcrun; do
  require "$command"
done
[[ -f "$SIGNING_IDENTITY_FILE" ]] || die "missing $SIGNING_IDENTITY_FILE"
[[ -f "$SPOT_MONO_FONT" ]] || die "missing licensed Spot Mono input: $SPOT_MONO_FONT"
LC_ALL=C file -b "$SPOT_MONO_FONT" \
  | grep -Eq '(OpenType|TrueType) font collection data' \
  || die "Spot Mono input is not an OpenType/TrueType font collection"
prepare_signing_identity

git_sha() { git -C "$1" rev-parse HEAD; }
require_clean_repo() {
  local repo="$1" label="$2"
  [[ -z "$(git -C "$repo" status --porcelain --untracked-files=normal)" ]] \
    || die "$label is dirty; release receipts refuse moving source"
}

run_bundled_verifier() {
  local verifier="$APP/Contents/Resources/runtime/bin/python3"
  [[ -x "$verifier" ]] || die "bundled product verifier is missing: $verifier"
  PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
    "$verifier" -m vibecrafted_core.product_contract "$@"
}

notary_submit() {
  local artifact="$1"
  if [[ -n "${NOTARY_PROFILE:-}" ]]; then
    xcrun notarytool submit "$artifact" --keychain-profile "$NOTARY_PROFILE" \
      --wait --timeout 30m
    return
  fi
  [[ -f "$NOTARY_ENV" ]] || die "NOTARY_PROFILE is unset and $NOTARY_ENV is missing"
  # shellcheck disable=SC1090
  source "$NOTARY_ENV"
  : "${NOTARY_APPLE_ID:?NOTARY_APPLE_ID missing}"
  : "${NOTARY_TEAM_ID:?NOTARY_TEAM_ID missing}"
  : "${NOTARY_PASSWORD:?NOTARY_PASSWORD missing}"
  xcrun notarytool submit "$artifact" --apple-id "$NOTARY_APPLE_ID" \
    --team-id "$NOTARY_TEAM_ID" --password "$NOTARY_PASSWORD" \
    --wait --timeout 30m
}

sign_macho_tree() {
  local outer="$APP/Contents/MacOS/Vibecrafted" candidate
  while IFS= read -r -d '' candidate; do
    [[ "$candidate" != "$outer" ]] || continue
    if /usr/bin/file -b "$candidate" | grep -q 'Mach-O'; then
      codesign --force --options runtime --timestamp --sign "$SIGNING_IDENTITY" \
        "${CODESIGN_KEYCHAIN_ARGS[@]}" "$candidate"
    fi
  done < <(find "$APP/Contents" -type f -print0)
}

sign_nested_app_bundles() {
  local nested_app
  while IFS= read -r -d '' nested_app; do
    codesign --force --options runtime --timestamp --sign "$SIGNING_IDENTITY" \
      "${CODESIGN_KEYCHAIN_ARGS[@]}" "$nested_app"
  done < <(find "$APP/Contents" -mindepth 2 -type d -name '*.app' -print0)
}

remove_ambient_swift_rpath() {
  local executable="$APP/Contents/MacOS/Vibecrafted"
  local rpaths
  rpaths="$(otool -l "$executable" | awk '
    $1 == "cmd" && $2 == "LC_RPATH" { in_rpath = 1; next }
    in_rpath && $1 == "path" { print $2; in_rpath = 0 }
  ')"
  if grep -Fxq '/usr/lib/swift' <<<"$rpaths"; then
    install_name_tool -delete_rpath /usr/lib/swift "$executable"
  fi
  rpaths="$(otool -l "$executable" | awk '
    $1 == "cmd" && $2 == "LC_RPATH" { in_rpath = 1; next }
    in_rpath && $1 == "path" { print $2; in_rpath = 0 }
  ')"
  if grep -Eq '^/' <<<"$rpaths"; then
    die "Swift host contains an ambient absolute LC_RPATH"
  fi
}

# Snapshots are materialised here, not at parse time: --notarize-only reuses an
# already assembled app and must not touch the donors at all.
materialize_donor_snapshots() {
  (( SNAPSHOT_DONORS )) || return 0
  require git
  log "Snapshotting donors at HEAD; their dirty working trees stay untouched"
  # No command substitution here: it would run the snapshot in a subshell and
  # the reaper would lose the record. See scripts/lib/donor-snapshot.sh.
  local terminal_head frame_head
  donor_snapshot_create "$TERMINAL_DONOR" "$TERMINAL_REPO"
  terminal_head="$DONOR_SNAPSHOT_HEAD"
  donor_snapshot_create "$FRAME_DONOR" "$FRAME_REPO"
  frame_head="$DONOR_SNAPSHOT_HEAD"
  log "vc-terminal snapshot at $terminal_head"
  log "vc-frame snapshot at $frame_head"
  # Every snapshot build starts from a cold target directory. That is the price
  # of a receipt that binds a SHA nobody edited mid-build.
  [[ -z "${VIBECRAFTED_RELEASE_FAIL_AFTER_SNAPSHOT:-}" ]] \
    || die "VIBECRAFTED_RELEASE_FAIL_AFTER_SNAPSHOT is set; failing on purpose so the reaper is exercised"
}

build_product() {
  materialize_donor_snapshots
  require_clean_repo "$REPO_ROOT" vibecrafted
  require_clean_repo "$TERMINAL_REPO" vc-terminal
  require_clean_repo "$FRAME_REPO" vc-frame

  log "Building vc-terminal through its release binary target"
  make -C "$TERMINAL_REPO" \
    DEPLOYMENT_TARGET='MACOSX_DEPLOYMENT_TARGET=14.0' release-bins
  local terminal_source="$TERMINAL_REPO/target/release/alacritty"
  [[ -x "$terminal_source" ]] || die "vc-terminal release binary is missing"
  chmod 0755 "$terminal_source"

  log "Building vc-frame through its provenance-stable donor target"
  make -C "$FRAME_REPO" release-binary
  local frame_source="$FRAME_REPO/target/release/vc-frame"
  [[ -x "$frame_source" ]] || die "vc-frame release binary is missing"
  chmod 0755 "$frame_source"

  log "Building the native hermetic vc-start"
  (cd "$REPO_ROOT/vibecrafted-app" && cargo build -p voc --bin vc-start --release)
  local start_source="$REPO_ROOT/vibecrafted-app/target/release/vc-start"
  [[ -x "$start_source" ]] || die "vc-start release binary is missing"
  chmod 0755 "$start_source"

  log "Building the bundled Vibecrafted Server and hydrated site"
  local server_build_root="$BUILD_DIR/cargo"
  make -C "$REPO_ROOT" CARGO_BUILD_ROOT="$server_build_root" build-server-release
  local server_source="$server_build_root/vibecrafted-server/release/vibecrafted-server-web"
  local server_site="$server_build_root/vibecrafted-server/site"
  [[ -x "$server_source" ]] || die "Vibecrafted Server release binary is missing"
  [[ -d "$server_site/pkg" ]] || die "Vibecrafted Server hydrated site is missing"

  log "Building the single Swift host app"
  make -C "$REPO_ROOT/vibecrafted-app/shell-agent" bindings xcode
  rm -rf "$BUILD_DIR/DerivedData" "$APP"
  mkdir -p "$BUILD_DIR" "$DIST_DIR"
  xcodebuild \
    -project "$REPO_ROOT/vibecrafted-app/shell-agent/app/Vibecrafted.xcodeproj" \
    -scheme Vibecrafted -configuration Release \
    -derivedDataPath "$BUILD_DIR/DerivedData" \
    CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO build
  local built_app
  built_app="$(find "$BUILD_DIR/DerivedData" -type d -name Vibecrafted.app -print -quit)"
  [[ -n "$built_app" ]] || die "xcodebuild did not produce Vibecrafted.app"
  /usr/bin/ditto "$built_app" "$APP"
  local resources="$APP/Contents/Resources"
  mkdir -p "$resources"
  log "Binding the canonical vc-terminal icon to Vibecrafted.app"
  "$REPO_ROOT/scripts/build-vibecrafted-icon.sh" \
    "$ICON_SOURCE" "$resources/Vibecrafted.icns" "$ICON_REFERENCE"
  if find "$resources" -maxdepth 1 -type f -name '*.icns' \
      ! -name 'Vibecrafted.icns' -print -quit | grep -q .; then
    die "assembled app contains a non-canonical application icon"
  fi
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile Vibecrafted.icns" \
    "$APP/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string Vibecrafted.icns" \
      "$APP/Contents/Info.plist"
  log "Embedding the canonical Spot Mono terminal family"
  mkdir -p "$resources/fonts"
  install -m 0644 "$SPOT_MONO_FONT" "$resources/fonts/SpotMono.ttc"
  remove_ambient_swift_rpath

  log "Embedding product modules and the checkout-free runtime"
  local runtime="$resources/runtime"
  local terminal_app="$APP/Contents/Helpers/vc-terminal.app"
  mkdir -p "$APP/Contents/Helpers" "$resources/terminal" "$runtime/bin"
  /usr/bin/ditto "$TERMINAL_REPO/extra/osx/vc-terminal.app" "$terminal_app"
  mkdir -p "$terminal_app/Contents/MacOS" "$terminal_app/Contents/Resources"
  install -m 0755 "$terminal_source" "$terminal_app/Contents/MacOS/alacritty"
  install -m 0644 "$resources/Vibecrafted.icns" \
    "$terminal_app/Contents/Resources/alacritty.icns"
  [[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' \
    "$terminal_app/Contents/Info.plist")" == "alacritty" ]] \
    || die "vc-terminal helper bundle executable contract is invalid"
  [[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' \
    "$terminal_app/Contents/Info.plist")" == "alacritty.icns" ]] \
    || die "vc-terminal helper bundle icon contract is invalid"
  install -m 0755 "$frame_source" "$APP/Contents/Helpers/vc-frame"
  install -m 0755 "$start_source" "$runtime/bin/vc-start"
  install -m 0755 "$server_source" "$runtime/bin/vc-server"
  install -m 0755 "$server_source" "$runtime/bin/vibecrafted-server-web"
  install -m 0644 "$REPO_ROOT/config/vc-terminal/vibecrafted.toml" \
    "$resources/terminal/vibecrafted.toml"
  printf '%s\n' "$RUNTIME_VERSION" > "$runtime/VERSION"
  mkdir -p "$runtime/scripts" "$runtime/vibecrafted-core" "$runtime/config"
  local canonical_deck="$REPO_ROOT/vibecrafted-core/vibecrafted_core/deck/vibecrafted"
  install -m 0755 "$canonical_deck" "$runtime/scripts/vibecrafted"
  install -m 0755 "$canonical_deck" "$runtime/bin/vibecrafted"
  /bin/cp -R "$REPO_ROOT/bin/." "$runtime/bin/"
  /bin/cp -R "$REPO_ROOT/vibecrafted-core/vibecrafted_core" \
    "$runtime/vibecrafted-core/"
  printf '%s\n' "$RUNTIME_VERSION" \
    > "$runtime/vibecrafted-core/vibecrafted_core/VERSION"
  /bin/cp -R "$REPO_ROOT/config/." "$runtime/config/"
  mkdir -p "$runtime/server/site"
  /bin/cp -R "$server_site/." "$runtime/server/site/"
  # The Living Tree may contain ignored interpreter caches. They are never
  # product inputs: adjacent verifier bytecode could shadow the signed source.
  find "$runtime/vibecrafted-core" \
    -type d -name __pycache__ -prune -exec rm -rf {} +
  find "$runtime/vibecrafted-core" \
    -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

  log "Embedding a private Python runtime; no shell profile or host Python is used"
  local python_seed seed_python python_home
  python_seed="$(mktemp -d "$BUILD_DIR/python-seed.XXXXXX")"
  uv python install 3.12.3 --install-dir "$python_seed" --no-bin
  seed_python="$(find "$python_seed" -type f -path '*/bin/python3.12' -print -quit)"
  [[ -n "$seed_python" ]] || die "uv did not produce the requested CPython"
  python_home="$(cd "$(dirname "$seed_python")/.." && pwd)"
  mkdir -p "$runtime/python" "$runtime/python-site"
  /bin/cp -RL "$python_home/." "$runtime/python/"
  uv pip install --python "$seed_python" --target "$runtime/python-site" \
    'jsonschema>=4.23,<5' 'PyYAML>=6.0,<7'
  install_name_tool -id '@loader_path/libpython3.12.dylib' \
    "$runtime/python/lib/libpython3.12.dylib"
  find "$runtime" -type f -name '*.pyc' -delete
  find "$runtime" -depth -type d -name __pycache__ -empty -delete
  find "$runtime" -type f -name '.DS_Store' -delete
  # These literals are the relocatable wrapper payload and expand only when
  # the installed wrapper runs inside Vibecrafted.app.
  # shellcheck disable=SC2016
  printf '%s\n' \
    '#!/bin/bash' \
    'set -euo pipefail' \
    'runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' \
    'export PYTHONNOUSERSITE=1' \
    'export PYTHONDONTWRITEBYTECODE=1' \
    'export PYTHONPATH="$runtime_root/vibecrafted-core:$runtime_root/python-site"' \
    'exec "$runtime_root/python/bin/python3.12" "$@"' \
    > "$runtime/bin/python3"
  chmod 0755 "$runtime/bin/python3"
  # pyproject.toml is the one public Python-command manifest. Preserve curated
  # native/shell implementations already present in bin and fill every missing
  # console script from that manifest so the app bootstrap cannot silently
  # omit a shipped command such as vc-git.
  "$REPO_ROOT/scripts/project-python" \
    "$REPO_ROOT/scripts/render-python-entrypoint-launchers.py" \
    --pyproject "$REPO_ROOT/vibecrafted-core/pyproject.toml" \
    --bin-dir "$runtime/bin"

  if find "$APP" -type l -print -quit | grep -q .; then
    die "assembled app contains symlinks"
  fi

  log "Signing nested code and binding exact source receipts"
  sign_macho_tree
  sign_nested_app_bundles
  require_clean_repo "$REPO_ROOT" vibecrafted
  require_clean_repo "$TERMINAL_REPO" vc-terminal
  require_clean_repo "$FRAME_REPO" vc-frame
  PYTHONPATH="$REPO_ROOT/vibecrafted-core" "$REPO_ROOT/scripts/project-python" \
    "$REPO_ROOT/scripts/unified_product_manifest.py" app \
    --app "$APP" --terminal-source "$terminal_source" --frame-source "$frame_source" \
    --version "$VERSION" --build "$BUILD_NUMBER" \
    --vibecrafted-sha "$(git_sha "$REPO_ROOT")" \
    --terminal-sha "$(git_sha "$TERMINAL_REPO")" \
    --frame-sha "$(git_sha "$FRAME_REPO")"
  codesign --force --options runtime --timestamp --sign "$SIGNING_IDENTITY" \
    "${CODESIGN_KEYCHAIN_ARGS[@]}" "$APP"
  log "Probing the signed bundled Python without mutating the app seal"
  "$runtime/bin/python3" -c 'import jsonschema, yaml, vibecrafted_core.product_contract'
  if find "$APP/Contents" \( -type d -name __pycache__ -o -type f -name '*.py[co]' \) \
      -print -quit | grep -q .; then
    die "bundled Python mutated the signed application payload"
  fi
  codesign --verify --deep --strict --verbose=2 "$APP"
  run_bundled_verifier app "$APP" --require-clean
}

create_dmg() {
  local staging="$BUILD_DIR/dmg-staging"
  rm -rf "$staging" "$DMG"
  rm -f "$DMG_CHECKSUM" "$LEGACY_DMG"
  mkdir -p "$staging"
  /usr/bin/ditto "$APP" "$staging/Vibecrafted.app"
  ln -s /Applications "$staging/Applications"
  hdiutil create -volname Vibecrafted -srcfolder "$staging" -ov -format UDZO "$DMG"
  codesign --force --timestamp --sign "$SIGNING_IDENTITY" \
    "${CODESIGN_KEYCHAIN_ARGS[@]}" "$DMG"
}

notarize_product() {
  local app_zip="$BUILD_DIR/Vibecrafted.app.zip"
  rm -f "$app_zip"
  /usr/bin/ditto -c -k --keepParent "$APP" "$app_zip"
  notary_submit "$app_zip"
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP"
  spctl --assess --type execute --verbose=2 "$APP"
  create_dmg
  notary_submit "$DMG"
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
  spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
}

emit_release_tuple() {
  PYTHONPATH="$REPO_ROOT/vibecrafted-core" "$REPO_ROOT/scripts/project-python" \
    "$REPO_ROOT/scripts/unified_product_manifest.py" release \
    --app "$APP" --dmg "$DMG" --output "$DIST_DIR/release-output.json"
  /usr/bin/openssl dgst -sha256 -sign "$SIGNING_KEY" \
    -out "$DIST_DIR/release-output.json.sig" "$DIST_DIR/release-output.json"
  run_bundled_verifier release-output \
    "$DIST_DIR/release-output.json" "$DIST_DIR/release-output.json.sig"
  (
    cd "$DIST_DIR"
    /usr/bin/shasum -a 256 "$DMG_NAME" > "$(basename "$DMG_CHECKSUM")"
  )
}

if [[ "$MODE" == "notarize" ]]; then
  [[ -d "$APP" ]] || die "missing $APP; run make dmg-signed first"
  notarize_product
  emit_release_tuple
  exit 0
fi

build_product
[[ "$MODE" == "app" ]] && exit 0
if [[ "$MODE" == "dmg" ]]; then
  create_dmg
  exit 0
fi
notarize_product
emit_release_tuple
log "Release complete: $DMG"
