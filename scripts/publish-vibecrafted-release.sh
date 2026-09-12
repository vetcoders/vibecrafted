#!/usr/bin/env bash
# Publish the installable Vibecrafted artifacts after a cold verification of the
# exact bytes downloaded back from a draft GitHub Release.
#
# Three carriers, one release, one commit:
#   macOS desktop -> the signed, notarized, stapled DMG
#   macOS CLI     -> the signed binary Runtime Pack embedded in that App
#   other systems -> the provenance-bound portable source tarball
# Each channel is verified against the bytes GitHub hands back, never against
# the bytes this machine still has in dist/. The asset allowlist below stays
# exact: a release that grew an asset nobody named is a release nobody audited.
set -euo pipefail

REPO="${VIBECRAFTED_RELEASE_REPO:-vetcoders/vibecrafted}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/dist"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
TAG="v$VERSION"
RELEASE_OUTPUT="$DIST/release-output.json"
RELEASE_SIGNATURE="$DIST/release-output.json.sig"
PORTABLE_OUTPUT="$DIST/portable-output.json"
DISTRIBUTION_MANIFEST="$ROOT/scripts/distribution_manifest.py"
REPORT_DATE="$(date +%Y_%m%d)"
REPORT_ROOT="${VIBECRAFTED_ARTIFACT_ROOT:-$HOME/.vibecrafted/artifacts}"
REPORT_DIR="$REPORT_ROOT/vetcoders/vibecrafted/$REPORT_DATE/reports"
REPORT="$REPORT_DIR/release-$TAG.md"

die() {
  printf 'publish-release: %s\n' "$*" >&2
  exit 1
}

for command_name in git gh uv shasum openssl xcrun spctl hdiutil; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing command: $command_name"
done
test "$(uname -s)" = "Darwin" || die "the notarized DMG publisher must run on macOS"
test -n "${GH_TOKEN:-}" || die "GH_TOKEN must name an authenticated release publisher"
test -s "$RELEASE_OUTPUT" || die "missing $RELEASE_OUTPUT"
test -s "$RELEASE_SIGNATURE" || die "missing $RELEASE_SIGNATURE"
test -s "$PORTABLE_OUTPUT" || die "missing $PORTABLE_OUTPUT; run make portable first"

cd "$ROOT"
test -z "$(git status --porcelain)" || die "source tree is dirty"
HEAD_SHA="$(git rev-parse HEAD)"
test "$(git cat-file -t "$TAG" 2>/dev/null || true)" = "tag" || die "$TAG must be an annotated tag"
test "$(git rev-list -n 1 "$TAG")" = "$HEAD_SHA" || die "$TAG does not point at HEAD"
REMOTE_TAG_SHA="$(git ls-remote origin "refs/tags/$TAG^{}" | awk '{print $1}')"
test "$REMOTE_TAG_SHA" = "$HEAD_SHA" || die "$TAG is not published at the exact HEAD"
uv run --project vibecrafted-core verify-vibecrafted-walkaround verify-release \
  --release-output "$RELEASE_OUTPUT" \
  --signature "$RELEASE_SIGNATURE"
test "$(uv run python3 -c 'import json; print(json.load(open("dist/release-output.json"))["source_revisions"]["vibecrafted"])')" = "$HEAD_SHA" \
  || die "release-output does not name the exact root revision"
DMG_NAME="$(uv run python3 -c 'import json; print(json.load(open("dist/release-output.json"))["dmg"]["path"])')"
DMG="$DIST/$DMG_NAME"
DMG_CHECKSUM="$DMG.sha256"
test -s "$DMG" || die "missing $DMG; run make release first"
test -s "$DMG_CHECKSUM" || die "missing $DMG_CHECKSUM"
(
  cd "$DIST"
  shasum -a 256 -c "$(basename "$DMG_CHECKSUM")"
)
xcrun stapler validate "$DMG"
spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"

RUNTIME_PACK_NAME="$(uv run python3 -c 'import json; print(json.load(open("dist/release-output.json"))["runtime_pack"]["path"])')"
RUNTIME_PACK="$DIST/$RUNTIME_PACK_NAME"
RUNTIME_PACK_CHECKSUM="$RUNTIME_PACK.sha256"
RUNTIME_PACK_SIGNATURE="$RUNTIME_PACK.sig"
RUNTIME_PACK_PUBLIC_KEY="$ROOT/vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub"
test -s "$RUNTIME_PACK" || die "missing $RUNTIME_PACK; run make release first"
test -s "$RUNTIME_PACK_CHECKSUM" || die "missing $RUNTIME_PACK_CHECKSUM"
test -s "$RUNTIME_PACK_SIGNATURE" || die "missing $RUNTIME_PACK_SIGNATURE"
test -s "$RUNTIME_PACK_PUBLIC_KEY" || die "missing trusted Runtime Pack public key"
(
  cd "$DIST"
  shasum -a 256 -c "$(basename "$RUNTIME_PACK_CHECKSUM")"
)
openssl dgst -sha256 -verify "$RUNTIME_PACK_PUBLIC_KEY" \
  -signature "$RUNTIME_PACK_SIGNATURE" "$RUNTIME_PACK" >/dev/null \
  || die "Runtime Pack signature verification failed"
VC_FRAME_SHA="$(uv run python3 -c 'import json; print(json.load(open("dist/release-output.json"))["source_revisions"]["vc-frame"])')"
VC_TERMINAL_SHA="$(uv run python3 -c 'import json; print(json.load(open("dist/release-output.json"))["source_revisions"]["vc-terminal"])')"
VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY="$RUNTIME_PACK_PUBLIC_KEY" \
  bash "$ROOT/scripts/install-runtime-pack.sh" \
    --pack "$RUNTIME_PACK" \
    --verify-only \
    --expected-source-revision "$HEAD_SHA" \
    --expected-terminal-revision "$VC_TERMINAL_SHA" \
    --expected-frame-revision "$VC_FRAME_SHA" >/dev/null

# The portable channel carries no Apple ticket, so its identity claim is the
# closed source-provenance carrier: an allowlisted tree whose digest names one
# commit. Bind that claim to the same HEAD the DMG names, or the release would
# ship two channels built from two different truths.
PORTABLE_NAME="$(uv run python3 -c 'import json; print(json.load(open("dist/portable-output.json"))["archive"]["path"])')"
PORTABLE="$DIST/$PORTABLE_NAME"
PORTABLE_CHECKSUM="$PORTABLE.sha256"
test -s "$PORTABLE" || die "missing $PORTABLE; run make portable first"
test -s "$PORTABLE_CHECKSUM" || die "missing $PORTABLE_CHECKSUM"
test "$(uv run python3 -c 'import json; print(json.load(open("dist/portable-output.json"))["source_revisions"]["vibecrafted"])')" = "$HEAD_SHA" \
  || die "portable-output does not name the exact root revision"
(
  cd "$DIST"
  shasum -a 256 -c "$(basename "$PORTABLE_CHECKSUM")"
)

RUN_ID="$(gh run list --repo "$REPO" --workflow release.yml --commit "$HEAD_SHA" \
  --json databaseId,status,conclusion --jq 'map(select(.status == "completed" and .conclusion == "success"))[0].databaseId // empty')"
test -n "$RUN_ID" || die "no successful Release source gate exists for $HEAD_SHA"

# Publication needs a boolean zero-open-alert gate, not a full alert census.
# Asking for one result avoids gh(1)'s incompatible --slurp/--jq combination
# while still proving that the filtered result set is empty.
OPEN_ALERTS="$(gh api "/repos/$REPO/code-scanning/alerts?state=open&ref=refs/heads/main&per_page=1" \
  --jq 'length')"
test "$OPEN_ALERTS" = "0" || die "$OPEN_ALERTS open CodeQL alert(s) remain on main"

if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  test "$(gh release view "$TAG" --repo "$REPO" --json isDraft --jq .isDraft)" = "true" \
    || die "refusing to mutate already-published release $TAG"
else
  gh release create "$TAG" --repo "$REPO" --target "$HEAD_SHA" \
    --title "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. $TAG" --notes "Verification in progress." \
    --draft --verify-tag
fi

gh release upload "$TAG" --repo "$REPO" \
  "$DMG" \
  "$DMG_CHECKSUM" \
  "$RUNTIME_PACK" \
  "$RUNTIME_PACK_CHECKSUM" \
  "$RUNTIME_PACK_SIGNATURE" \
  "$PORTABLE" \
  "$PORTABLE_CHECKSUM" \
  "$RELEASE_OUTPUT#release-output.json" \
  "$RELEASE_SIGNATURE#release-output.json.sig" \
  --clobber

DOWNLOAD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-published.XXXXXX")"
trap 'rm -rf "$DOWNLOAD_DIR"' EXIT
gh release download "$TAG" --repo "$REPO" --dir "$DOWNLOAD_DIR"

EXPECTED_ASSETS="$(printf '%s\n' \
  "$DMG_NAME" \
  "$DMG_NAME.sha256" \
  "$RUNTIME_PACK_NAME" \
  "$RUNTIME_PACK_NAME.sha256" \
  "$RUNTIME_PACK_NAME.sig" \
  "$PORTABLE_NAME" \
  "$PORTABLE_NAME.sha256" \
  "release-output.json" \
  "release-output.json.sig" | LC_ALL=C sort)"
ACTUAL_ASSETS="$(find "$DOWNLOAD_DIR" -maxdepth 1 -type f -exec basename {} \; | LC_ALL=C sort)"
test "$ACTUAL_ASSETS" = "$EXPECTED_ASSETS" || die "draft release contains unexpected assets"
cmp "$DMG" "$DOWNLOAD_DIR/$DMG_NAME"
cmp "$DMG_CHECKSUM" "$DOWNLOAD_DIR/$DMG_NAME.sha256"
cmp "$RUNTIME_PACK" "$DOWNLOAD_DIR/$RUNTIME_PACK_NAME"
cmp "$RUNTIME_PACK_CHECKSUM" "$DOWNLOAD_DIR/$RUNTIME_PACK_NAME.sha256"
cmp "$RUNTIME_PACK_SIGNATURE" "$DOWNLOAD_DIR/$RUNTIME_PACK_NAME.sig"
cmp "$PORTABLE" "$DOWNLOAD_DIR/$PORTABLE_NAME"
cmp "$PORTABLE_CHECKSUM" "$DOWNLOAD_DIR/$PORTABLE_NAME.sha256"
cmp "$RELEASE_OUTPUT" "$DOWNLOAD_DIR/release-output.json"
cmp "$RELEASE_SIGNATURE" "$DOWNLOAD_DIR/release-output.json.sig"
(
  cd "$DOWNLOAD_DIR"
  shasum -a 256 -c "$DMG_NAME.sha256"
  shasum -a 256 -c "$RUNTIME_PACK_NAME.sha256"
  shasum -a 256 -c "$PORTABLE_NAME.sha256"
)
openssl dgst -sha256 -verify "$RUNTIME_PACK_PUBLIC_KEY" \
  -signature "$DOWNLOAD_DIR/$RUNTIME_PACK_NAME.sig" \
  "$DOWNLOAD_DIR/$RUNTIME_PACK_NAME" >/dev/null \
  || die "downloaded Runtime Pack signature verification failed"
VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY="$RUNTIME_PACK_PUBLIC_KEY" \
  bash "$ROOT/scripts/install-runtime-pack.sh" \
    --pack "$DOWNLOAD_DIR/$RUNTIME_PACK_NAME" \
    --verify-only \
    --expected-source-revision "$HEAD_SHA" \
    --expected-terminal-revision "$VC_TERMINAL_SHA" \
    --expected-frame-revision "$VC_FRAME_SHA" >/dev/null

uv run --project vibecrafted-core verify-vibecrafted-walkaround verify-release \
  --release-output "$DOWNLOAD_DIR/release-output.json" \
  --signature "$DOWNLOAD_DIR/release-output.json.sig"
uv run --project vibecrafted-core verify-vibecrafted-walkaround walkaround \
  --release-output "$DOWNLOAD_DIR/release-output.json" \
  --signature "$DOWNLOAD_DIR/release-output.json.sig" \
  --output "$DOWNLOAD_DIR/walkaround.json"
xcrun stapler validate "$DOWNLOAD_DIR/$DMG_NAME"
spctl --assess --type open --context context:primary-signature --verbose=2 \
  "$DOWNLOAD_DIR/$DMG_NAME"

# Walk around the portable channel the way a stranger on Linux will: unpack the
# downloaded bytes into a fresh root and make the payload answer for its own
# provenance and its own installer. A checksum only proves the bytes survived
# the wire; this proves they are the distribution they claim to be.
PORTABLE_UNPACK_DIR="$DOWNLOAD_DIR/portable-unpack"
PORTABLE_ROOT_NAME="$(uv run python3 -c 'import json; print(json.load(open("dist/portable-output.json"))["archive"]["root_name"])')"
mkdir -p "$PORTABLE_UNPACK_DIR"
tar -xzf "$DOWNLOAD_DIR/$PORTABLE_NAME" -C "$PORTABLE_UNPACK_DIR"
test -d "$PORTABLE_UNPACK_DIR/$PORTABLE_ROOT_NAME" \
  || die "portable archive does not unpack into $PORTABLE_ROOT_NAME"
uv run python3 "$DISTRIBUTION_MANIFEST" check \
  --root "$PORTABLE_UNPACK_DIR/$PORTABLE_ROOT_NAME" \
  --expected-owner-repo "$REPO" \
  --expected-source-revision "$HEAD_SHA"
bash "$PORTABLE_UNPACK_DIR/$PORTABLE_ROOT_NAME/install.sh" --help >/dev/null

# Exercise the exact downloaded binary carrier through both public CLI buttons
# in an isolated HOME. The second button consumes the receipt written by the
# first and must leave no private XDG/agent residue behind.
RUNTIME_PACK_SMOKE_HOME="$DOWNLOAD_DIR/runtime-pack-home"
mkdir -p "$RUNTIME_PACK_SMOKE_HOME"
RUNTIME_PACK_SMOKE_ENV=(
  HOME="$RUNTIME_PACK_SMOKE_HOME"
  XDG_CONFIG_HOME="$RUNTIME_PACK_SMOKE_HOME/.config"
  XDG_DATA_HOME="$RUNTIME_PACK_SMOKE_HOME/.local/share"
  VIBECRAFTED_HOME="$RUNTIME_PACK_SMOKE_HOME/.vibecrafted"
  VIBECRAFTED_RUNTIME_HOME="$RUNTIME_PACK_SMOKE_HOME/.local/share/vibecrafted"
  VIBECRAFTED_LAUNCHER_BIN="$RUNTIME_PACK_SMOKE_HOME/.local/bin"
)
env "${RUNTIME_PACK_SMOKE_ENV[@]}" \
  make --no-print-directory install RUNTIME_PACK="$DOWNLOAD_DIR/$RUNTIME_PACK_NAME"
env "${RUNTIME_PACK_SMOKE_ENV[@]}" \
  make --no-print-directory uninstall
test -z "$(find "$RUNTIME_PACK_SMOKE_HOME" -mindepth 1 -print -quit)" \
  || die "Runtime Pack install/uninstall left residue in isolated HOME"

DMG_SHA="$(shasum -a 256 "$DOWNLOAD_DIR/$DMG_NAME" | awk '{print $1}')"
DMG_SIZE="$(stat -f %z "$DOWNLOAD_DIR/$DMG_NAME")"
RUNTIME_PACK_SHA="$(shasum -a 256 "$DOWNLOAD_DIR/$RUNTIME_PACK_NAME" | awk '{print $1}')"
RUNTIME_PACK_SIZE="$(stat -f %z "$DOWNLOAD_DIR/$RUNTIME_PACK_NAME")"
PORTABLE_SHA="$(shasum -a 256 "$DOWNLOAD_DIR/$PORTABLE_NAME" | awk '{print $1}')"
PORTABLE_SIZE="$(stat -f %z "$DOWNLOAD_DIR/$PORTABLE_NAME")"
PORTABLE_TREE_SHA="$(uv run python3 -c 'import json; print(json.load(open("dist/portable-output.json"))["provenance"]["tree_sha256"])')"
DOWNLOAD_URL="https://github.com/$REPO/releases/download/$TAG/$DMG_NAME"
RUNTIME_PACK_URL="https://github.com/$REPO/releases/download/$TAG/$RUNTIME_PACK_NAME"
PORTABLE_URL="https://github.com/$REPO/releases/download/$TAG/$PORTABLE_NAME"

mkdir -p "$REPORT_DIR"
umask 022
cat > "$REPORT" <<EOF
# Vibecrafted $TAG release report

## 1. Security gate

- Semgrep: PASS via the tag source gate and local release gate.
- CodeQL: PASS; zero open alerts on \`main\` immediately before publication.
- Signed release-output verification: PASS.
- Apple notarization ticket, Gatekeeper assessment and staple validation: PASS on local and downloaded bytes.
- Runtime Pack checksum, detached signature and isolated install/uninstall: PASS on downloaded bytes.
- Portable channel source-provenance validation against \`$HEAD_SHA\`: PASS on downloaded bytes.

## 2. Exposed surface inventory

| Surface | Bind / endpoint | Proxy / TLS | Auth boundary | Secret materialization |
| --- | --- | --- | --- | --- |
| Vibecrafted.app desktop UI | local process | none | logged-in macOS user | app-owned runtime environment |
| Runtime Pack install (macOS CLI) | none; same immutable payload as the App | not applicable | invoking user | one receipted runtime/config layout |
| Portable source install (Linux / WSL2 / explicit source fallback) | none; \`install.sh\` publishes into the user-owned runtime home | not applicable | invoking user | user-owned runtime home, no host config rewrites |
| vc-server service | typed Vibecrafted settings; default loopback, operator may choose any host:port such as \`100.82.232.70:3025\` | operator-owned for non-loopback exposure | configured service policy | runtime service environment, never host config rewrites |
| vc-frame web client | disabled unless explicitly configured | operator-owned | vc-frame auth boundary | runtime-only |

## 3. Deployment mode decision

The shipped topology is one runtime product with three carriers: a signed and notarized macOS desktop DMG, the same signed binary Runtime Pack for macOS CLI users, and one portable source distribution for systems Apple notarization cannot reach. \`Vibecrafted.app\` owns app/DMG/onboarding/update but does not own a second runtime. \`vc-terminal\` is a deterministic embedded terminal substrate and \`vc-frame\` is the embedded session interior. App onboarding and \`make install\` invoke the same receipted installer. Rollback is deterministic uninstall plus installation of the prior carrier; live session state remains separate runtime state.

The portable channel is not a second product: it is the same commit, projected through the allowlisted distribution writer, carrying a closed \`source-provenance.json\` whose distribution-tree digest names that commit. It installs through \`install.sh --archive-file\`, which refuses a payload whose provenance does not close. Rollback is re-running the installer from the prior release asset.

Source tuple:

- vibecrafted: \`$HEAD_SHA\`
- vc-frame: \`$VC_FRAME_SHA\`
- vc-terminal: \`$VC_TERMINAL_SHA\`

## 4. Post-release install smoke

### macOS desktop channel

- Source: [$DOWNLOAD_URL]($DOWNLOAD_URL)
- SHA-256: \`$DMG_SHA\`
- Size: \`$DMG_SIZE\` bytes
- Draft assets downloaded into a fresh temporary directory and byte-compared: PASS.
- Signed release-output verified against the downloaded DMG: PASS.
- Mounted-DMG walk-around probes from downloaded bytes: PASS.
- Stapler and Gatekeeper validation on downloaded bytes: PASS.

### macOS CLI Runtime Pack

- Source: [$RUNTIME_PACK_URL]($RUNTIME_PACK_URL)
- SHA-256: \`$RUNTIME_PACK_SHA\`
- Size: \`$RUNTIME_PACK_SIZE\` bytes
- Downloaded tarball, checksum and detached signature byte-compared: PASS.
- Signature verified with the bundled Vibecrafted release public key: PASS.
- Isolated \`make install\` -> \`make uninstall\` left an empty HOME: PASS.

Install from a checkout without installing the App:

\`\`\`bash
curl -fsSLO $RUNTIME_PACK_URL
curl -fsSLO $RUNTIME_PACK_URL.sha256
curl -fsSLO $RUNTIME_PACK_URL.sig
make install RUNTIME_PACK=$RUNTIME_PACK_NAME
\`\`\`

### Portable source channel (Linux / WSL2 / source fallback)

- Source: [$PORTABLE_URL]($PORTABLE_URL)
- SHA-256: \`$PORTABLE_SHA\`
- Size: \`$PORTABLE_SIZE\` bytes
- Distribution-tree digest: \`$PORTABLE_TREE_SHA\`
- Downloaded tarball unpacked into a fresh root and validated against \`$HEAD_SHA\`: PASS.
- Packed \`install.sh\` answers for itself from the downloaded bytes: PASS.

Install from the published asset:

\`\`\`bash
curl -fsSLO $PORTABLE_URL
curl -fsSLO $PORTABLE_URL.sha256
shasum -a 256 -c $PORTABLE_NAME.sha256 || sha256sum -c $PORTABLE_NAME.sha256
tar -xzf $PORTABLE_NAME
bash $PORTABLE_ROOT_NAME/install.sh
\`\`\`

## Sign-off

PASS — the release has exactly three canonically named installable carriers built from one commit: \`$DMG_NAME\` for macOS desktop, \`$RUNTIME_PACK_NAME\` for macOS CLI, and \`$PORTABLE_NAME\` as the cross-platform source fallback. App and CLI consume one Runtime Pack authority; no donor repo owns a competing app, installer or update channel.
EOF

gh release edit "$TAG" --repo "$REPO" --notes-file "$REPORT" --draft=false --latest
test "$(gh release view "$TAG" --repo "$REPO" --json isDraft --jq .isDraft)" = "false"

printf 'Published %s\nReport: %s\nDMG: %s bytes / %s\nRuntime Pack: %s\n  %s bytes / %s\nPortable: %s\n  %s bytes / %s\n' \
  "$DOWNLOAD_URL" "$REPORT" "$DMG_SIZE" "$DMG_SHA" \
  "$RUNTIME_PACK_URL" "$RUNTIME_PACK_SIZE" "$RUNTIME_PACK_SHA" \
  "$PORTABLE_URL" "$PORTABLE_SIZE" "$PORTABLE_SHA"
