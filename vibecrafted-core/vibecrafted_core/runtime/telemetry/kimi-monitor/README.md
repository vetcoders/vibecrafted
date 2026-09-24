# Kimi Code Quota Monitor & Statusline

Standalone quota monitor, token accounting engine, and low-latency statusline runner for **Kimi Code CLI**.

## Co zawiera pakiet?

1. **`kimi_monitor.py`**
   - **`daemon`**: Działa w tle (co 30s), sonduje lokalny `kimi web` przez wewnętrzne API (`/api/v1/oauth/usage`), atomowo odświeża `~/.kimi-code/runtime/quota.json`.
   - **`once`**: Jednorazowy snapshot limitów 5h i miesięcznych, zwraca sformatowany JSON i zapisuje do `quota.json`.
   - **`line`**: Superszybki (<80ms) renderer stopki TUI czytający stan z plików lokalnych (`wire.jsonl` + `quota.json`) bez zapytań sieciowych.
2. **`kimi-monitor.toml`** — pojedyncze źródło prawdy (Single Source of Truth) dla stawek cennika per model (k3, kimi-k3, kimi-for-coding, highspeed), progów alarmowych i interwałów odpytywania.
3. **`statusline.py`** (instalowany jako `~/.kimi-code/statusline.sh`) — runner dla Kimi TUI (`tui.toml`), który:
   - Dynamicznie czyta cennik z `kimi-monitor.toml` przez `tomllib` (brak redundancji stawek).
   - Oznacza estymowany koszt API jako shadow pricing (`≈$X.XXX api-equiv`), uniemożliwiając pomylenie go z opłatą subskrypcyjną.
   - Posiada wbudowany **Quota & Desync Detector**: wychwytuje błędy 403 z `turn.ended` przy niskim użyciu i natychmiast alarmuje `⚠QUOTA-DESYNC` (a także `⚠NEAR-5H-LIMIT`, `⚠MONTHLY-HIGH`, `⚠STALE-QUOTA`).
   - Pokazuje precyzyjny termometr cache: `% cache` z sesji.
4. **`install.sh` / `uninstall.sh`** — automatyczny instalator i deinstalator konfigurujący daemon LaunchAgent na macOS.

---

## Szybka instalacja

Rozpakuj i uruchom:

```bash
tar -xzf kimi-monitor.tar.gz
cd kimi-monitor
./install.sh
```

Instalator automatycznie:

- Sprawdza dostępność Python >= 3.11.
- Kopiuje skrypty do `~/.kimi-code/`.
- Tworzy symlink `~/.local/bin/kimi-monitor`.
- Rejestruje i startuje LaunchAgent `com.kimi.monitor.plist` na macOS.
- Weryfikuje działanie próbnika i renderera.

---

## Konfiguracja Kimi TUI (`~/.kimi-code/tui.toml`)

Aby włączyć custom statusline w Kimi Code TUI, upewnij się, że w `~/.kimi-code/tui.toml` znajduje się:

```toml
[status_line]
items = ["mode","goal","model","tasks","cwd","git","tips"]
command = "~/.kimi-code/statusline.sh"
```

Wynik w stopce terminala:

```text
kimi-k3 (manual) · git:main · ctx: 686k/1.0M (65.4%) · 620.9M toks (95% cache) · ≈$296.919 api-equiv · 5h 0%↻3:34 · M 0%
```

_(Gdy wystąpi desync lub limit, pojawi się np. `⚠QUOTA-DESYNC` lub `⚠NEAR-5H-LIMIT`)_

---

## Użycie CLI

```bash
# Jednorazowy odczyt limitów (wypisuje JSON na stdout)
kimi-monitor once

# Szybki render statusline (lokalny, bez sieci)
kimi-monitor line

# Ręczne uruchomienie daemona (jeśli nie używasz LaunchAgent)
kimi-monitor daemon
```

---

## Zarządzanie usługą w tle (macOS)

```bash
# Sprawdzenie statusu usługi
launchctl list | grep com.kimi.monitor

# Podgląd logów daemona
tail -f ~/.kimi-code/logs/kimi-monitor.log

# Ręczny restart usługi
launchctl unload ~/Library/LaunchAgents/com.kimi.monitor.plist
launchctl load ~/Library/LaunchAgents/com.kimi.monitor.plist
```
