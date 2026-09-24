#!/usr/bin/env bash
# ==============================================================================
# Kimi Code Quota Monitor & Statusline Installer
# ==============================================================================
set -euo pipefail

# ANSI styling
BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
RED="\033[31m"
DIM="\033[2m"
RESET="\033[0m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIMI_HOME="${KIMI_HOME:-$HOME/.kimi-code}"
LOCAL_BIN="$HOME/.local/bin"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
FORCE=0

for arg in "$@"; do
    case "$arg" in
        --force|-f) FORCE=1 ;;
        --help|-h)
            echo "Usage: ./install.sh [--force]"
            echo "  --force    Overwrite existing ~/.kimi-code/kimi-monitor.toml config"
            exit 0
            ;;
    esac
done

echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  Kimi Code Quota Monitor & Statusline Installer${RESET}"
echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

# 1. Check Python 3 (>= 3.11 for tomllib)
echo -e "\n${BOLD}[1/6] Sprawdzanie środowiska Python...${RESET}"
PYTHON_BIN=""
CANDIDATES=(
    "$(command -v python3 2>/dev/null || true)"
    "/opt/homebrew/bin/python3"
    "/usr/local/bin/python3"
    "/usr/bin/python3"
)

for cand in "${CANDIDATES[@]}"; do
    if [[ -n "$cand" && -x "$cand" ]]; then
        if "$cand" -c 'import sys, tomllib; assert sys.version_info >= (3, 11)' 2>/dev/null; then
            PYTHON_BIN="$cand"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    echo -e "${RED}Błąd: Nie znaleziono Python >= 3.11 (wymagany moduł tomllib).${RESET}"
    echo "Zainstaluj Python 3.11+ (np. przez 'brew install python3') i spróbuj ponownie."
    exit 1
fi
echo -e "  ${GREEN}✓${RESET} Wykryto Python: ${BOLD}$PYTHON_BIN${RESET} ($("$PYTHON_BIN" --version))"

# 2. Prepare directories
echo -e "\n${BOLD}[2/6] Tworzenie katalogów docelowych...${RESET}"
mkdir -p "$KIMI_HOME/runtime"
mkdir -p "$KIMI_HOME/logs"
mkdir -p "$LOCAL_BIN"
echo -e "  ${GREEN}✓${RESET} $KIMI_HOME/runtime"
echo -e "  ${GREEN}✓${RESET} $KIMI_HOME/logs"
echo -e "  ${GREEN}✓${RESET} $LOCAL_BIN"

# 3. Copy scripts and configuration
echo -e "\n${BOLD}[3/6] Kopiowanie plików...${RESET}"
cp "$SCRIPT_DIR/kimi_monitor.py" "$KIMI_HOME/kimi_monitor.py"
chmod +x "$KIMI_HOME/kimi_monitor.py"
echo -e "  ${GREEN}✓${RESET} Zainstalowano $KIMI_HOME/kimi_monitor.py"

cp "$SCRIPT_DIR/statusline.py" "$KIMI_HOME/statusline.sh"
chmod +x "$KIMI_HOME/statusline.sh"
echo -e "  ${GREEN}✓${RESET} Zainstalowano $KIMI_HOME/statusline.sh"

if [[ -f "$KIMI_HOME/kimi-monitor.toml" && "$FORCE" -eq 0 ]]; then
    echo -e "  ${YELLOW}!${RESET} Istniejący $KIMI_HOME/kimi-monitor.toml zachowany (użyj --force, aby nadpisać)"
else
    if [[ -f "$KIMI_HOME/kimi-monitor.toml" ]]; then
        cp "$KIMI_HOME/kimi-monitor.toml" "$KIMI_HOME/kimi-monitor.toml.$(date +%Y%m%d%H%M%S).bak"
    fi
    cp "$SCRIPT_DIR/kimi-monitor.toml" "$KIMI_HOME/kimi-monitor.toml"
    echo -e "  ${GREEN}✓${RESET} Zainstalowano $KIMI_HOME/kimi-monitor.toml"
fi

# 4. CLI Symlink
echo -e "\n${BOLD}[4/6] Tworzenie symlinka CLI w PATH...${RESET}"
ln -sf "$KIMI_HOME/kimi_monitor.py" "$LOCAL_BIN/kimi-monitor"
echo -e "  ${GREEN}✓${RESET} $LOCAL_BIN/kimi-monitor -> $KIMI_HOME/kimi_monitor.py"

if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    echo -e "  ${YELLOW}! Uwaga:${RESET} $LOCAL_BIN nie znajduje się w zmiennej PATH."
    echo -e "    Dodaj do ~/.zshrc lub ~/.bashrc: ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
fi

# 5. Background Daemon (macOS LaunchAgent)
echo -e "\n${BOLD}[5/6] Konfiguracja daemona tła...${RESET}"
if [[ "$(uname -s)" == "Darwin" ]]; then
    mkdir -p "$LAUNCH_AGENTS_DIR"
    PLIST_PATH="$LAUNCH_AGENTS_DIR/com.kimi.monitor.plist"

    # Unload previous service if running
    launchctl unload "$PLIST_PATH" 2>/dev/null || true

    # Render template
    sed -e "s|__PYTHON3_BIN__|$PYTHON_BIN|g" \
        -e "s|__KIMI_HOME__|$KIMI_HOME|g" \
        -e "s|__USER_HOME__|$HOME|g" \
        "$SCRIPT_DIR/com.kimi.monitor.plist.template" > "$PLIST_PATH"

    chmod 644 "$PLIST_PATH"
    launchctl load "$PLIST_PATH"
    echo -e "  ${GREEN}✓${RESET} Załadowano LaunchAgent: $PLIST_PATH"
    sleep 1

    if launchctl list | grep -q "com.kimi.monitor"; then
        echo -e "  ${GREEN}✓${RESET} Daemon 'com.kimi.monitor' działa w tle."
    else
        echo -e "  ${YELLOW}!${RESET} Usługa zarejestrowana, sprawdź logi: $KIMI_HOME/logs/kimi-monitor.log"
    fi
else
    echo -e "  ${DIM}(Pominięto LaunchAgent — system inny niż macOS)${RESET}"
    echo -e "  Uruchom w tle ręcznie lub przez systemd: ${BOLD}kimi-monitor daemon &${RESET}"
fi

# 6. Verification
echo -e "\n${BOLD}[6/6] Weryfikacja działania...${RESET}"
if "$PYTHON_BIN" "$KIMI_HOME/kimi_monitor.py" once >/dev/null 2>&1; then
    echo -e "  ${GREEN}✓${RESET} Próbnik API Kimi (once): OK"
else
    echo -e "  ${YELLOW}!${RESET} Próbnik API Kimi zgłosił błąd (może wymagać uruchomionego kimi lub autoryzacji)"
fi

echo -en "  ${GREEN}✓${RESET} Przykładowy statusline: "
"$PYTHON_BIN" "$KIMI_HOME/kimi_monitor.py" line 2>/dev/null || echo "(brak danych)"


echo -e "\n${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}${GREEN}  Instalacja zakończona pomyślnie!${RESET}"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "Przydatne komendy:"
echo -e "  ${BOLD}kimi-monitor once${RESET}    - jednorazowe pobranie limitów i zapis do quota.json"
echo -e "  ${BOLD}kimi-monitor line${RESET}    - render statusline z kalkulacją tokenów i kosztów"
echo -e "  ${BOLD}kimi-monitor daemon${RESET}  - poller w trybie ciągłym (co 30s)"
echo -e "\nLogi daemona: ${BOLD}$KIMI_HOME/logs/kimi-monitor.log${RESET}"
echo -e "Plik stanu:   ${BOLD}$KIMI_HOME/runtime/quota.json${RESET}"
