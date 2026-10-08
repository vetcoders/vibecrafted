#!/usr/bin/env bash
# Replaceable Vibecrafted.app, ad-hoc signed.
# make install-app stays the Developer ID transaction in scripts/install-app.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINATION="${INSTALL_APP_DESTINATION:-/Applications/Vibecrafted.app}"
DERIVED="$REPO_ROOT/build/dev-app/DerivedData"

if [[ "$(uname -s)" != Darwin ]]; then
  echo "install-dev-app: Darwin only" >&2
  exit 1
fi

make -C "$REPO_ROOT/vibecrafted-app/shell-agent" bindings xcode
rm -rf "$DERIVED"
xcodebuild \
  -project "$REPO_ROOT/vibecrafted-app/shell-agent/app/Vibecrafted.xcodeproj" \
  -scheme Vibecrafted -configuration Release \
  -derivedDataPath "$DERIVED" \
  CODE_SIGNING_ALLOWED=YES \
  CODE_SIGN_IDENTITY="-" \
  build

built="$(find "$DERIVED" -type d -name Vibecrafted.app -print -quit)"
[[ -n "$built" ]] || { echo "install-dev-app: xcodebuild produced no app" >&2; exit 1; }
codesign --force --deep --sign - "$built"

if pid="$(pgrep -x Vibecrafted | head -1)" && [[ -n "$pid" ]]; then
  echo "install-dev-app: quitting running Vibecrafted (pid $pid)"
  osascript -e 'tell application id "io.vetcoders.vibecrafted" to quit' >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
fi

rm -rf "$DESTINATION"
ditto "$built" "$DESTINATION"
echo "install-dev-app: $DESTINATION (ad-hoc)"
