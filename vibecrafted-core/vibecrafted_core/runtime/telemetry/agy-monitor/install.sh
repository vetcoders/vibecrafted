#!/usr/bin/env bash
# ==============================================================================
# Google Antigravity (AGY) Monitor & Statusline Installer
# ==============================================================================
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
RED="\033[31m"
RESET="\033[0m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGY_HOME="${AGY_HOME:-$HOME/.gemini/agy-monitor}"
LOCAL_BIN="${LOCAL_BIN:-$HOME/.local/bin}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
FORCE=0
ENABLE_DAEMON=1

for arg in "$@"; do
    case "$arg" in
        --force|-f) FORCE=1 ;;
        --no-daemon) ENABLE_DAEMON=0 ;;
        --help|-h)
            echo "Usage: ./install.sh [--force] [--no-daemon]"
            echo "  --force        Overwrite existing ~/.gemini/agy-monitor/agy-monitor.toml config"
            echo "  --no-daemon    Skip LaunchAgent daemon setup"
            exit 0
            ;;
    esac
done

echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  Google Antigravity (AGY) Monitor & Statusline Installer${RESET}"
echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

# 1. Check Python 3 (>= 3.11 for tomllib)
echo -e "\n${BOLD}[1/5] Sprawdzanie środowiska Python...${RESET}"
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
    exit 1
fi
echo -e "  ${GREEN}✓${RESET} Wykryto Python: ${BOLD}$PYTHON_BIN${RESET} ($("$PYTHON_BIN" --version))"

# 2. Prepare directories
echo -e "\n${BOLD}[2/5] Tworzenie katalogów docelowych...${RESET}"
mkdir -p "$AGY_HOME/runtime"
mkdir -p "$AGY_HOME/logs"
mkdir -p "$LOCAL_BIN"
echo -e "  ${GREEN}✓${RESET} $AGY_HOME/runtime"
echo -e "  ${GREEN}✓${RESET} $AGY_HOME/logs"
echo -e "  ${GREEN}✓${RESET} $LOCAL_BIN"

# 3. Copy scripts and configuration
echo -e "\n${BOLD}[3/5] Kopiowanie plików...${RESET}"
if [[ "$SCRIPT_DIR" != "$AGY_HOME" ]]; then
    cp "$SCRIPT_DIR/agy_monitor.py" "$AGY_HOME/agy_monitor.py"
    cp "$SCRIPT_DIR/uninstall.sh" "$AGY_HOME/uninstall.sh"
    cp "$SCRIPT_DIR/README.md" "$AGY_HOME/README.md"
    cp "$SCRIPT_DIR/com.google.antigravity.monitor.plist.template" "$AGY_HOME/com.google.antigravity.monitor.plist.template"
    echo -e "  ${GREEN}✓${RESET} Zainstalowano $AGY_HOME/agy_monitor.py"
    echo -e "  ${GREEN}✓${RESET} Zainstalowano $AGY_HOME/uninstall.sh"
    echo -e "  ${GREEN}✓${RESET} Zainstalowano $AGY_HOME/README.md"
    if [[ -f "$AGY_HOME/agy-monitor.toml" && "$FORCE" -eq 0 ]]; then
        echo -e "  ${YELLOW}!${RESET} Istniejący $AGY_HOME/agy-monitor.toml zachowany (użyj --force, aby nadpisać)"
    else
        if [[ -f "$AGY_HOME/agy-monitor.toml" ]]; then
            cp "$AGY_HOME/agy-monitor.toml" "$AGY_HOME/agy-monitor.toml.$(date +%Y%m%d%H%M%S).bak"
        fi
        cp "$SCRIPT_DIR/agy-monitor.toml" "$AGY_HOME/agy-monitor.toml"
        echo -e "  ${GREEN}✓${RESET} Zainstalowano $AGY_HOME/agy-monitor.toml"
    fi
else
    echo -e "  ${GREEN}✓${RESET} Instalacja in-place w $AGY_HOME"
fi
chmod +x "$AGY_HOME/agy_monitor.py"
chmod +x "$AGY_HOME/uninstall.sh"

# 4. CLI Symlink
echo -e "\n${BOLD}[4/5] Tworzenie symlinka CLI w PATH...${RESET}"
ln -sf "$AGY_HOME/agy_monitor.py" "$LOCAL_BIN/agy-monitor"
echo -e "  ${GREEN}✓${RESET} $LOCAL_BIN/agy-monitor -> $AGY_HOME/agy_monitor.py"

if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    echo -e "  ${YELLOW}! Uwaga:${RESET} $LOCAL_BIN nie znajduje się w zmiennej PATH."
    echo -e "    Dodaj do ~/.zshrc: ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
fi

# 5. Background Daemon (macOS LaunchAgent)
if [[ "$ENABLE_DAEMON" -eq 1 && "$(uname -s)" == "Darwin" ]]; then
    mkdir -p "$LAUNCH_AGENTS_DIR"
    PLIST_PATH="$LAUNCH_AGENTS_DIR/com.google.antigravity.monitor.plist"
    TEMPLATE_SRC="$SCRIPT_DIR/com.google.antigravity.monitor.plist.template"
    if [[ ! -f "$TEMPLATE_SRC" ]]; then
        TEMPLATE_SRC="$AGY_HOME/com.google.antigravity.monitor.plist.template"
    fi

    launchctl unload "$PLIST_PATH" 2>/dev/null || true

    sed -e "s|__PYTHON3_BIN__|$PYTHON_BIN|g" \
        -e "s|__AGY_HOME__|$AGY_HOME|g" \
        -e "s|__USER_HOME__|$HOME|g" \
        "$TEMPLATE_SRC" > "$PLIST_PATH"

    chmod 644 "$PLIST_PATH"
    launchctl load "$PLIST_PATH"
    echo -e "  ${GREEN}✓${RESET} Załadowano LaunchAgent: $PLIST_PATH"
fi

# 6. Verification
echo -e "\n${BOLD}[5/5] Weryfikacja działania...${RESET}"
if "$PYTHON_BIN" "$AGY_HOME/agy_monitor.py" once >/dev/null 2>&1; then
    echo -e "  ${GREEN}✓${RESET} Snapshot sesji AGY (once): OK"
else
    echo -e "  ${YELLOW}!${RESET} Snapshot sesji AGY zgłosił błąd (brak aktywnych sesji w ~/.gemini)"
fi

echo -en "  ${GREEN}✓${RESET} Przykładowy statusline: "
"$PYTHON_BIN" "$AGY_HOME/agy_monitor.py" line 2>/dev/null || echo "(brak danych)"

echo -e "\n${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}${GREEN}  Instalacja agy-monitor zakończona pomyślnie!${RESET}"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "Przydatne komendy:"
echo -e "  ${BOLD}agy-monitor line${RESET}      - natychmiastowy statusline aktywnej sesji"
echo -e "  ${BOLD}agy-monitor once${RESET}      - pełny JSON ze stanem, tokenami, procesami i quotą"
echo -e "  ${BOLD}agy-monitor sessions${RESET}  - lista ostatnich konwersacji z liczbą tokenów i kosztami"
echo -e "  ${BOLD}agy-monitor daemon${RESET}    - watcher w tle"
echo -e "\nPlik stanu:   ${BOLD}$AGY_HOME/runtime/quota.json${RESET}"
