#!/usr/bin/env bash
# post-install-launch.sh — backyard product spine end of install (SF-3 / SF-4)
#
# After foundations + tools are on disk: offer to open the operator session
# with the Start here layout (operator.kdl / default_layout vibecrafted).
#
# Does NOT claim full agent-process restore — see docs/installer/RESTORE_CONTRACT.md.
set -euo pipefail

info() { printf '\033[36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }

NONINTERACTIVE="${VIBECRAFTED_INSTALL_NONINTERACTIVE:-0}"
YES="${VIBECRAFTED_LAUNCH_YES:-0}"
NO_LAUNCH="${VIBECRAFTED_NO_LAUNCH:-0}"
FORCE_YES=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) FORCE_YES=1 ;;
    --no-launch) NO_LAUNCH=1 ;;
  esac
done

if [[ "$NO_LAUNCH" == "1" ]]; then
  info "Launch skipped (--no-launch / VIBECRAFTED_NO_LAUNCH=1)."
  info "When ready: vc-start    # operator session · Start here tab"
  exit 0
fi

if ! command -v vc-frame >/dev/null 2>&1; then
  warn "vc-frame not on PATH — cannot launch cockpit."
  warn "Install the verified Runtime Pack, then: vc-start"
  exit 0
fi

# Consume the installed physical config. Launch must never deliver or repair it.
frame_config="$HOME/.config/vibecrafted/vc-frame"
if [[ ! -d "$frame_config" || -L "$frame_config" \
  || ! -f "$frame_config/config.kdl" || -L "$frame_config/config.kdl" \
  || ! -d "$frame_config/layouts" || -L "$frame_config/layouts" \
  || ! -f "$frame_config/layouts/operator.kdl" || -L "$frame_config/layouts/operator.kdl" ]]; then
  warn "Installed product configuration is missing or misrouted: $frame_config"
  warn "Repair: run make install from the Vibecrafted checkout with your verified Runtime Pack."
  exit 1
fi
export VC_FRAME_CONFIG_DIR="$frame_config"

answer="y"
if [[ "$FORCE_YES" == "1" || "$YES" == "1" ]]; then
  answer="y"
elif [[ "$NONINTERACTIVE" == "1" ]] || [[ ! -t 0 ]]; then
  info "Non-interactive install: not auto-launching the cockpit."
  info "Start when ready:"
  info "  vc-start"
  info "  # or: vibecrafted start"
  exit 0
else
  printf '\n'
  printf 'Install complete.\n'
  printf '  Frame:     %s\n' "$(command -v vc-frame)"
  printf '  Cockpit:   operator session with tab "Start here" (map of the workspace)\n'
  printf '  Restore:   layout/session resurrection is frame-level — not a full agent\n'
  printf '             process freeze. See docs/installer/RESTORE_CONTRACT.md\n'
  printf '\n'
  printf 'Launch Vibecrafted now? [Y/n] '
  read -r answer || answer="y"
  answer="$(printf '%s' "${answer:-y}" | tr '[:upper:]' '[:lower:]')"
fi

case "$answer" in
  n|no)
    info "OK. Later:"
    info "  vc-start              # open / attach operator session"
    info "  vibecrafted doctor    # health"
    info "  vibecrafted help      # command deck"
    exit 0
    ;;
esac

# Invoke one installed entry once, preserving its result for the installer.
if command -v vc-start >/dev/null 2>&1; then
  info "Opening operator session via vc-start…"
  vc-start operator
  exit $?
fi

if command -v vibecrafted >/dev/null 2>&1; then
  info "Opening operator session via vibecrafted start…"
  vibecrafted start operator
  exit $?
fi

warn "Installed product launcher is missing; repair the verified Runtime Pack."
exit 1
