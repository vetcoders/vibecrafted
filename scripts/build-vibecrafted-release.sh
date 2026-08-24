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
# living checkout locations through compiler metadata.
#
# ORDER IS LOAD-BEARING. rustc applies the LAST matching --remap-path-prefix.
# MEASURED 2026-08-18:
#   rustc --remap-path-prefix=$T=/usr/src/OUTER \
#         --remap-path-prefix=$T/inner=/usr/src/INNER  $T/inner/main.rs
# reports /usr/src/INNER/main.rs, and swapping the two arguments reports
# /usr/src/OUTER/inner/main.rs. So the list runs BROADEST FIRST:
#   * $HOME must precede the checkout and the donors. It used to be last, which
#     is correct only by accident on this host — every repository happens to
#     live on /Volumes. On any operator whose checkout sits under $HOME, the
#     trailing $HOME entry would win and every specific root would be dead.
#   * the donor snapshots live under $REPO_ROOT/build/..., so they must follow
#     $REPO_ROOT or they would be rewritten as /usr/src/vibecrafted/build/...
#
# The snapshot pair is emitted only when it exists. Without --snapshot-donors
# TERMINAL_REPO IS TERMINAL_DONOR, and the duplicate pair merely pinned its own
# redundancy into the contract test.
PATH_REMAPS=(
  "$HOME=/usr/src/operator-home"
  "$REPO_ROOT=/usr/src/vibecrafted"
  "$TERMINAL_DONOR=/usr/src/vc-terminal"
  "$FRAME_DONOR=/usr/src/vc-frame"
)
if (( SNAPSHOT_DONORS )); then
  PATH_REMAPS+=(
    "$TERMINAL_REPO=/usr/src/vc-terminal"
    "$FRAME_REPO=/usr/src/vc-frame"
  )
fi
RUSTFLAGS=""
FILE_PREFIX_MAP=""
SWIFT_PREFIX_MAP=""
for mapping in "${PATH_REMAPS[@]}"; do
  RUSTFLAGS+="${RUSTFLAGS:+ }--remap-path-prefix=$mapping"
  FILE_PREFIX_MAP+="${FILE_PREFIX_MAP:+ }-ffile-prefix-map=$mapping"
  SWIFT_PREFIX_MAP+="${SWIFT_PREFIX_MAP:+ }-debug-prefix-map $mapping"
done
export RUSTFLAGS
# cc-rs compiles the C half of crates such as `ring`, and rustc's remap never
# sees those translation units. MEASURED on the shipped 4.1.0 DMG:
# Contents/MacOS/Vibecrafted carried 21 occurrences of
# $HOME/.cargo/registry/src/.../ring-0.17.14/crypto/... clang's
# -ffile-prefix-map is the same instrument on the C side.
export CFLAGS="${CFLAGS:+$CFLAGS }$FILE_PREFIX_MAP"
export CXXFLAGS="${CXXFLAGS:+$CXXFLAGS }$FILE_PREFIX_MAP"
# The Swift host is built by xcodebuild, which reads none of the above. Same
# payload, 51 occurrences of the checkout root from Swift source locations and
# DerivedData intermediates. Passed to xcodebuild as build settings below.
export SWIFT_PREFIX_MAP

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
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/lib/payload-hygiene.sh"

cleanup() {
  # Host-wide resources first. The keychain session mutates state that outlives
  # this process and affects every application on the machine; the donor
  # snapshots are directories under this repo's own build/ and a stale one is
  # merely untidy. Reaping first meant a hung `git worktree remove` — an index
  # lock on a busy donor is enough — would strand the keychain instead.
  #
  # In practice keychain_session_begin also arms its own EXIT handler which
  # chains ahead of this one, so the keychain is usually already released by the
  # time we arrive. That path does not exist when no signing certificate was
  # present, which is exactly when this ordering is the only ordering.
  keychain_session_end "$SIGNING_KEYCHAIN_LABEL" || true
  donor_snapshot_reap || true
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
    #
    # A hosted runner is the one place where registering it is right: nothing
    # else runs there, and codesign resolves the Developer ID chain through the
    # search list, not through --keychain alone (run 32597029908: identity
    # present, "The specified item could not be found in the keychain").
    KEYCHAIN_SESSION_REGISTER_SEARCH_LIST="${VIBECRAFTED_KEYCHAIN_SEARCH_LIST:-0}" \
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

for command in cargo codesign file git hdiutil install_name_tool make otool strip uv xcodebuild xcodegen xcrun; do
  require "$command"
done
[[ -f "$SIGNING_IDENTITY_FILE" ]] || die "missing $SIGNING_IDENTITY_FILE"
[[ -f "$SPOT_MONO_FONT" ]] || die "missing licensed Spot Mono input: $SPOT_MONO_FONT"
LC_ALL=C file -b "$SPOT_MONO_FONT" \
  | grep -Eq '(OpenType|TrueType) font collection data' \
  || die "Spot Mono input is not an OpenType/TrueType font collection"
prepare_signing_identity

git_sha() { git -C "$1" rev-parse HEAD; }

# require_clean_repo <repo> <label> [allowed-path-prefix...]
#
# The allowance exists for exactly one case and is empty everywhere else: under
# --snapshot-donors this script regenerates vc-frame's bundled plugin assets
# INSIDE the snapshot, so that tree legitimately differs from its HEAD. That is
# derived output of the very commit the receipt binds — `plugins-parity
# double-rebuild` is what asserts it is a function of the source and nothing
# else — and the snapshot is a detached worktree this script created and will
# reap. Every other difference still refuses, including an unexpected file
# under the allowed directory's sibling.
require_clean_repo() {
  local repo="$1" label="$2"
  shift 2
  local status line path prefix allowed
  local -a offending=()
  status="$(git -C "$repo" status --porcelain --untracked-files=normal)"
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    path="${line:3}"
    allowed=0
    for prefix in ${1+"$@"}; do
      if [[ "$path" == "$prefix"* ]]; then
        allowed=1
        break
      fi
    done
    (( allowed )) || offending+=("$line")
  done <<< "$status"
  (( ${#offending[@]} == 0 )) \
    || die "$label is dirty; release receipts refuse moving source: ${offending[*]}"
}

# Empty unless the frame repo is a snapshot we are entitled to regenerate into.
FRAME_DERIVED=()
if (( SNAPSHOT_DONORS )); then
  FRAME_DERIVED=("zellij-utils/assets/plugins/")
fi

# normalize_embedded_python_paths <runtime-root> <seed-dir>
#
# Replace every textual mention of the ephemeral CPython seed directory with a
# stable placeholder. Binary files are reported and skipped: shortening a string
# inside a Mach-O would corrupt it, and the payload gate is the backstop that
# refuses the build if one ever appears.
normalize_embedded_python_paths() {
  local runtime="$1" seed="$2"
  python3 - "$runtime" "$seed" <<'PY'
import pathlib
import sys

runtime = pathlib.Path(sys.argv[1])
seed = sys.argv[2].rstrip("/").encode()
placeholder = b"/usr/src/python-seed"

rewritten = 0
binary = []
for path in runtime.rglob("*"):
    if path.is_symlink() or not path.is_file():
        continue
    data = path.read_bytes()
    if seed not in data:
        continue
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        binary.append(str(path.relative_to(runtime)))
        continue
    path.write_text(text.replace(seed.decode(), placeholder.decode()), encoding="utf-8")
    rewritten += 1

print(f"normalized the embedded interpreter seed path in {rewritten} text file(s)")
if binary:
    print(f"binary files still naming the seed: {binary}", file=sys.stderr)
    raise SystemExit(1)
PY
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

# Debug stabs are the producer no compiler flag reaches. rustc's
# --remap-path-prefix rewrites SOURCE paths (SO stabs read /usr/src/...), but
# the linker still records every OBJECT file it consumed as an N_OSO stab —
# .../target/release/deps/alacritty-*.o and DerivedData/Intermediates.noindex/...
# — verbatim. MEASURED 2026-08-19 on a fresh af98ebfe build: alacritty carried
# 249 such stabs and Contents/MacOS/Vibecrafted 40, all naming the build root.
# `strip -S` removes debugging symbol table entries only; code, exports and
# the indirect symbol table are untouched, and it runs before any signature.
strip_debug_stabs() {
  local candidate
  while IFS= read -r -d '' candidate; do
    if /usr/bin/file -b "$candidate" | grep -q 'Mach-O'; then
      strip -S "$candidate" 2>/dev/null \
        || die "strip -S failed on ${candidate#"$APP"/}"
    fi
  done < <(find "$APP/Contents/MacOS" "$APP/Contents/Helpers" -type f -print0)
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

  # vc-frame's `release-binary` target builds --no-plugins and the binary
  # embeds zellij-utils/assets/plugins/*.wasm through include_bytes!. Those
  # blobs are GIT-TRACKED build output: they carry whatever paths the machine
  # that last ran `make plugins-assets` happened to have. MEASURED 2026-08-18:
  # 276 occurrences of $HOME/.cargo/registry across the 14 tracked blobs, of
  # which 411 reached Contents/Helpers/vc-frame in the shipped 4.1.0 DMG. No
  # compiler flag rewrites bytes that were compiled somewhere else, so the
  # release has to compile them again under its own remaps. Closed loop,
  # measured on default-plugins/link: 16 -> 0 occurrences of $HOME.
  #
  # Only ever against a SNAPSHOT. This rebuild rewrites assets/plugins/ and its
  # SHA256SUMS; doing that to the operator's living donor would trample work
  # another agent may be mid-edit on. The snapshot is a detached worktree this
  # script created and will reap, so it is ours to dirty.
  if (( SNAPSHOT_DONORS )); then
    log "Rebuilding vc-frame's bundled WASM plugins under the release remaps"
    CARGO_PROFILE_RELEASE_STRIP=false make -C "$FRAME_REPO" plugins-assets
  else
    log "NOTE: --snapshot-donors is off, so the tracked WASM plugin blobs ship"
    log "      as they are. If they name this host the payload gate refuses"
    log "      the build; rerun with --snapshot-donors."
  fi

  log "Building vc-frame through its provenance-stable donor target"
  # zellij-utils/build.rs bakes CARGO_MANIFEST_DIR into the binary for
  # install-freshness (dev builds compare themselves with their checkout). A
  # release build's checkout is a donor snapshot that is reaped minutes later,
  # so the baked path would be both useless and a host-path leak (measured
  # 2026-08-19: 1 hit in Contents/Helpers/vc-frame). Pin it to the same root
  # the source remap advertises; the freshness probe then reports NoCheckout.
  CARGO_PROFILE_RELEASE_STRIP=false \
    VC_FRAME_SOURCE_MANIFEST_DIR=/usr/src/vc-frame/zellij-utils \
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
  # `$(inherited)` is xcodebuild's own build-setting syntax, not a shell
  # command substitution: it must reach xcodebuild verbatim, so the single
  # quotes are the point. The prefix maps beside it ARE expanded, which is why
  # each setting is spliced from a quoted and an unquoted half.
  # shellcheck disable=SC2016
  xcodebuild \
    -project "$REPO_ROOT/vibecrafted-app/shell-agent/app/Vibecrafted.xcodeproj" \
    -scheme Vibecrafted -configuration Release \
    -derivedDataPath "$BUILD_DIR/DerivedData" \
    OTHER_SWIFT_FLAGS='$(inherited) '"$SWIFT_PREFIX_MAP" \
    OTHER_CFLAGS='$(inherited) '"$FILE_PREFIX_MAP" \
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

  # Rust 1.95 applies profile `strip = true` while compiling host proc-macros,
  # which makes crates such as include_dir and vte_generate_state_changes
  # disappear before their dependants are compiled. Build vc-frame unstripped
  # above, then strip the finished Mach-O products here. This also removes the
  # linker object-file table that otherwise preserves the snapshot/DerivedData
  # checkout path even when compiler source paths were prefix-mapped.
  log "Stripping local object-file paths from final Mach-O products"
  /usr/bin/strip -S \
    "$APP/Contents/MacOS/Vibecrafted" \
    "$terminal_app/Contents/MacOS/alacritty" \
    "$APP/Contents/Helpers/vc-frame"
  install -m 0644 "$REPO_ROOT/config/vc-terminal/vibecrafted.toml" \
    "$resources/terminal/vibecrafted.toml"
  printf '%s\n' "$RUNTIME_VERSION" > "$runtime/VERSION"
  mkdir -p "$runtime/scripts" "$runtime/vibecrafted-core" "$runtime/config"
  local canonical_deck="$REPO_ROOT/vibecrafted-core/vibecrafted_core/deck/vibecrafted"
  install -m 0755 "$canonical_deck" "$runtime/scripts/vibecrafted"
  install -m 0755 "$canonical_deck" "$runtime/bin/vibecrafted"
  # The DMG carries the same installer used by source/CLI channels. The native
  # app invokes its Runtime Pack mode; installed launchers invoke its uninstall
  # mode. Keep the small import closure beside it so it runs under the bundled
  # interpreter without reaching back into a checkout or the host Python.
  install -m 0755 "$REPO_ROOT/scripts/vetcoders_install.py" \
    "$runtime/scripts/vetcoders_install.py"
  install -m 0644 "$REPO_ROOT/scripts/distribution_manifest.py" \
    "$runtime/scripts/distribution_manifest.py"
  install -m 0644 "$REPO_ROOT/scripts/installer_brand.py" \
    "$runtime/scripts/installer_brand.py"
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

  # pip writes each console script with a shebang naming the interpreter it
  # installed against — here, the mktemp seed under $BUILD_DIR that this build
  # deletes on its way out. MEASURED on the 4.1.0 payload:
  # runtime/python-site/bin/jsonschema opens with an exec line pointing at
  # .../python-seed.ZOVHSX/.../bin/python3.12, a path no customer has. So this
  # directory was never merely a leak: it shipped scripts that cannot run.
  #
  # Deleted rather than rewritten, because nothing invokes it. python-site goes
  # on PYTHONPATH and never on PATH, and every shipped command is rendered into
  # runtime/bin from pyproject.toml a few lines below.
  rm -rf "$runtime/python-site/bin"

  # CPython records the prefix it was installed under in `sysconfig`, and uv's
  # distribution is no exception: 27 occurrences of the same ephemeral seed
  # directory in runtime/python/lib/python3.12/_sysconfigdata__darwin_darwin.py.
  # Those values are consulted only when compiling a C extension, which the
  # shipped runtime never does, so rewriting the literal is honest — and the
  # rewrite is confined to files that decode as UTF-8. A Mach-O naming the seed
  # would need a length-preserving patch, so it is deliberately left alone and
  # left for the payload gate to catch; measured on 4.1.0, no binary does.
  normalize_embedded_python_paths "$runtime" "$python_seed"

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

  # Before a signature is spent, and before notarization makes these bytes
  # permanent, ask the finished bundle who built it. This gate exists because
  # every compiler-side answer is partial: measured on the shipped 4.1.0 DMG,
  # eight files named the operator across five producers rustc cannot all
  # reach. See scripts/payload_hygiene.py.
  log "Stripping linker debug stabs from the binaries this build produced"
  strip_debug_stabs

  log "Asserting the assembled app does not name the build host"
  assert_payload_is_anonymous "$APP" "Vibecrafted.app"

  log "Signing nested code and binding exact source receipts"
  sign_macho_tree
  sign_nested_app_bundles
  require_clean_repo "$REPO_ROOT" vibecrafted
  require_clean_repo "$TERMINAL_REPO" vc-terminal
  require_clean_repo "$FRAME_REPO" vc-frame ${FRAME_DERIVED+"${FRAME_DERIVED[@]}"}
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
