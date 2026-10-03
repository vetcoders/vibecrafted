#!/usr/bin/env bash
# install-hammerspoon.sh — Plan 11 (META_22) operator install entry.
#
# Copies config/hammerspoon/init.lua to ~/.hammerspoon/init.lua, offering
# a .bak overwrite when an existing config is present. Triggers a
# Hammerspoon reload via pkill + open -a (avoids the chicken-and-egg
# AppleScript-permission problem documented in doctrine 2026-05-08:
# `osascript reload` requires `hs.allowAppleScript(true)` which itself
# needs the new init.lua to be live).
#
# Usage:
#   scripts/install-hammerspoon.sh             # interactive on overwrite
#   scripts/install-hammerspoon.sh --force     # overwrite without prompt
#   scripts/install-hammerspoon.sh --no-reload # do not pkill Hammerspoon
#   scripts/install-hammerspoon.sh --help
#
# Vibecrafted with AI Agents (c)2024-2026 LibraxisAI

set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)

SRC="$REPO_ROOT/config/hammerspoon/init.lua"
DST_DIR="${HAMMERSPOON_DIR:-$HOME/.hammerspoon}"
DST="$DST_DIR/init.lua"
BAK="$DST.bak"

FORCE=0
NO_RELOAD=0

usage() {
    cat <<'USAGE'
install-hammerspoon.sh — Plan 11 (META_22) Hammerspoon config installer

Copies the Vetcoders Hammerspoon template (config/hammerspoon/init.lua)
to ~/.hammerspoon/init.lua so the vc-* URL handlers (vc-ping, vc-loct,
vc-aicx, vc-open-file, vc-atlas, vc-prism, vc-marbles, vc-followup) are
registered with macOS Launch Services.

Usage:
  scripts/install-hammerspoon.sh             # interactive on overwrite
  scripts/install-hammerspoon.sh --force     # overwrite without prompt
  scripts/install-hammerspoon.sh --no-reload # do not pkill Hammerspoon
  scripts/install-hammerspoon.sh --help

Environment overrides:
  HAMMERSPOON_DIR   target dir (default: ~/.hammerspoon)

Idempotent: rerunning with identical source is a no-op; otherwise the
existing init.lua is backed up to init.lua.bak before overwrite.
USAGE
}

log() {
    printf '[install-hammerspoon] %s\n' "$*"
}

warn() {
    printf '[install-hammerspoon] WARN: %s\n' "$*" >&2
}

err() {
    printf '[install-hammerspoon] ERROR: %s\n' "$*" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)     FORCE=1; shift ;;
        --no-reload) NO_RELOAD=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        *) err "unknown flag: $1 (try --help)" ;;
    esac
done

# Pre-flight: source must exist.
if [[ ! -f "$SRC" ]]; then
    err "source not found: $SRC"
fi

# Pre-flight: macOS only. On Linux/CI we exit cleanly with a notice so the
# script can be smoke-tested in a non-macOS environment.
case "$(uname -s)" in
    Darwin) : ;;
    *)
        log "non-macOS host ($(uname -s)) — Hammerspoon is macOS-only; nothing to install."
        exit 0
        ;;
esac

# Ensure target dir exists.
mkdir -p "$DST_DIR"

# Idempotency: if dst matches src byte-for-byte, skip.
if [[ -f "$DST" ]] && cmp -s "$SRC" "$DST"; then
    log "init.lua already current at $DST — nothing to do."
    exit 0
fi

# Existing file present and different → offer .bak overwrite.
if [[ -f "$DST" ]] && [[ "$FORCE" -ne 1 ]]; then
    log "existing config detected: $DST"
    log "backup will be written to: $BAK"
    if [[ -t 0 ]]; then
        printf '  proceed with overwrite? [y/N] '
        read -r reply
        case "$reply" in
            y|Y|yes|YES) : ;;
            *) log "aborted by operator (use --force to skip prompt)"; exit 0 ;;
        esac
    else
        warn "non-interactive stdin and no --force — aborting to avoid surprising overwrite"
        warn "rerun with --force to overwrite ($DST → $BAK)"
        exit 0
    fi
fi

# Back up if existing.
if [[ -f "$DST" ]]; then
    cp "$DST" "$BAK"
    log "backed up existing init.lua → $BAK"
fi

# Copy.
cp "$SRC" "$DST"
log "installed: $DST"

# Verify the copied file contains hs.allowAppleScript(true) (required for
# any future `osascript reload` flow — doctrine 2026-05-08).
if ! grep -q 'hs.allowAppleScript(true)' "$DST"; then
    warn "$DST does not declare hs.allowAppleScript(true) — reload via osascript will be denied"
fi

# Reload Hammerspoon. We use pkill + open -a rather than osascript because
# the AppleScript permission may not be live yet (chicken-and-egg on first
# install).
if [[ "$NO_RELOAD" -eq 1 ]]; then
    log "skipping Hammerspoon reload (--no-reload)"
    log "to activate the new config: open -a Hammerspoon (or reload via menu bar)"
    exit 0
fi

if pgrep -x Hammerspoon >/dev/null 2>&1; then
    log "Hammerspoon running — restarting to load new init.lua"
    pkill -x Hammerspoon || true
    # tiny settling delay so Launch Services releases the URL handler
    sleep 0.5
fi

if command -v open >/dev/null 2>&1; then
    open -a Hammerspoon || warn "Hammerspoon launch failed — start it manually from /Applications"
else
    warn "'open' not available on this system — start Hammerspoon manually"
fi

log "done. test with:  open 'hammerspoon://vc-ping?msg=hello'"
