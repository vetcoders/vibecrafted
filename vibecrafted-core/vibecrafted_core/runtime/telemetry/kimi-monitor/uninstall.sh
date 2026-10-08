#!/usr/bin/env bash
# ==============================================================================
# Kimi Code Quota Monitor & Statusline Uninstaller
# ==============================================================================
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
RESET="\033[0m"

KIMI_HOME="${KIMI_HOME:-$HOME/.kimi-code}"
LOCAL_BIN="$HOME/.local/bin"
PLIST_PATH="$HOME/Library/LaunchAgents/com.kimi.monitor.plist"

echo -e "${BOLD}Odinstalowywanie Kimi Monitor...${RESET}"

# 1. Unload LaunchAgent
if [[ -f "$PLIST_PATH" ]]; then
    echo -e "Zatrzymywanie i usuwanie LaunchAgent..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo -e "  ${GREEN}✓${RESET} Usunięto $PLIST_PATH"
fi

# 2. Remove symlink
if [[ -L "$LOCAL_BIN/kimi-monitor" || -f "$LOCAL_BIN/kimi-monitor" ]]; then
    rm -f "$LOCAL_BIN/kimi-monitor"
    echo -e "  ${GREEN}✓${RESET} Usunięto $LOCAL_BIN/kimi-monitor"
fi

# 3. Clean files (optional prompt or keep config)
echo -e "Pliki konfiguracyjne i logi w ${BOLD}$KIMI_HOME${RESET} zostały zachowane."
echo -e "${GREEN}Odinstalowano pomyślnie.${RESET}"
