#!/usr/bin/env bash
# ==============================================================================
# Google Antigravity (AGY) Quota Monitor & Statusline Uninstaller
# ==============================================================================
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RESET="\033[0m"

AGY_HOME="${AGY_HOME:-$HOME/.gemini/agy-monitor}"
LOCAL_BIN="${LOCAL_BIN:-$HOME/.local/bin}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST_PATH="$LAUNCH_AGENTS_DIR/com.google.antigravity.monitor.plist"

echo -e "${BOLD}Odinstalowywanie Google Antigravity (AGY) Monitor...${RESET}"

# 1. Unload and remove LaunchAgent
if [[ -f "$PLIST_PATH" ]]; then
    echo -e "Zatrzymywanie i usuwanie LaunchAgent..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo -e "  ${GREEN}✓${RESET} Usunięto $PLIST_PATH"
fi

# 2. Remove symlink
if [[ -L "$LOCAL_BIN/agy-monitor" || -f "$LOCAL_BIN/agy-monitor" ]]; then
    rm -f "$LOCAL_BIN/agy-monitor"
    echo -e "  ${GREEN}✓${RESET} Usunięto $LOCAL_BIN/agy-monitor"
fi

# 3. Inform about remaining files
echo -e "Pliki konfiguracyjne i logi w ${BOLD}$AGY_HOME${RESET} zostały zachowane."
echo -e "${YELLOW}Uwaga:${RESET} Jeśli chcesz usunąć katalog z konfiguracją, wykonaj: rm -rf \"$AGY_HOME\""
echo -e "${GREEN}Odinstalowano pomyślnie.${RESET}"
