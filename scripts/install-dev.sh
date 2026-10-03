#!/usr/bin/env bash
# Local donor install. Ad-hoc signed and replaceable.
# make install stays the receipted Runtime Pack. make install-app stays the
# Developer ID app transaction. This door copies the binaries just built
# from the living checkouts into the installed generation.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/runtime-roots.sh
. "$REPO_ROOT/scripts/lib/runtime-roots.sh"

FRAME_REPO="${VIBECRAFTED_FRAME_REPO:-$REPO_ROOT/../vc-frame}"
TERMINAL_REPO="${VIBECRAFTED_TERMINAL_REPO:-$REPO_ROOT/../vc-terminal}"
TOOLS_HOME="$(default_vibecrafted_tools_home)"
RUNTIME_ROOT="$TOOLS_HOME/vibecrafted-current"
BIN_DIR="$RUNTIME_ROOT/bin"
LIBEXEC_DIR="$RUNTIME_ROOT/libexec"

if [[ ! -d "$BIN_DIR" ]]; then
  BIN_DIR="$(default_vibecrafted_launcher_bin)"
  LIBEXEC_DIR=""
  mkdir -p "$BIN_DIR"
fi

adhoc_sign() {
  if [[ "$(uname -s)" == Darwin ]]; then
    codesign --force --sign - "$1"
  fi
}

install_bin() {
  local src="$1" dest="$2"
  [[ -x "$src" ]] || { echo "install-dev: missing $src" >&2; exit 1; }
  adhoc_sign "$src"
  mkdir -p "$(dirname "$dest")"
  rm -f "$dest"
  install -m 0755 "$src" "$dest"
  echo "install-dev: $dest"
}

export RUSTFLAGS="${RUSTFLAGS:+$RUSTFLAGS }--remap-path-prefix=${HOME}=/usr/src/operator-home --remap-path-prefix=${REPO_ROOT}=/usr/src/vibecrafted"
export CARGO_PROFILE_RELEASE_STRIP=false

echo "install-dev: voc, vc-admin, vc-procs, vc-start"
(
  cd "$REPO_ROOT/vibecrafted-app"
  cargo build --locked --release -p voc --bin voc --bin vc-admin --bin vc-procs --bin vc-start
)
APP_TARGET="$REPO_ROOT/vibecrafted-app/target/release"
install_bin "$APP_TARGET/voc" "$BIN_DIR/voc"
install_bin "$APP_TARGET/voc" "$BIN_DIR/vc-o"
install_bin "$APP_TARGET/vc-admin" "$BIN_DIR/vc-admin"
install_bin "$APP_TARGET/vc-procs" "$BIN_DIR/vc-procs"
install_bin "$APP_TARGET/vc-start" "$BIN_DIR/vc-start"

echo "install-dev: vc-server"
(
  cd "$REPO_ROOT/vibecrafted-server"
  cargo build --locked --release -p vibecrafted-server-web --bin vibecrafted-server-web
)
SERVER_BIN="$REPO_ROOT/vibecrafted-server/target/release/vibecrafted-server-web"
install_bin "$SERVER_BIN" "$BIN_DIR/vc-server"
install_bin "$SERVER_BIN" "$BIN_DIR/vibecrafted-server-web"

if [[ -d "$FRAME_REPO" ]]; then
  echo "install-dev: vc-frame"
  make -C "$FRAME_REPO" release-binary
  frame_src="$FRAME_REPO/target/release/vc-frame"
  if [[ -n "$LIBEXEC_DIR" ]]; then
    install_bin "$frame_src" "$LIBEXEC_DIR/vc-frame"
  fi
  install_bin "$frame_src" "$BIN_DIR/vc-frame"
else
  echo "install-dev: no vc-frame sibling at $FRAME_REPO — skipped" >&2
fi

if [[ -d "$TERMINAL_REPO" ]]; then
  echo "install-dev: vc-terminal"
  make -C "$TERMINAL_REPO" DEPLOYMENT_TARGET='MACOSX_DEPLOYMENT_TARGET=14.0' release-bins
  term_src="$TERMINAL_REPO/target/release/alacritty"
  if [[ -n "$LIBEXEC_DIR" ]]; then
    install_bin "$term_src" "$LIBEXEC_DIR/vc-terminal"
  fi
  install_bin "$term_src" "$BIN_DIR/vc-terminal"
else
  echo "install-dev: no vc-terminal sibling at $TERMINAL_REPO — skipped" >&2
fi

echo "install-dev: done ($BIN_DIR)"
