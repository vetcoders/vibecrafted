# Google Antigravity (AGY) Monitor & Statusline

Monitor stanu, rozliczanie tokenów, estymacja kosztów (shadow pricing) oraz szybki statusline dla **Google Antigravity IDE** oraz **Antigravity CLI (`agy`)**.

---

## Najważniejsze funkcje

1. **`agy_monitor.py`**
   - **`line`**: Błyskawiczny (<30ms) renderer stopki terminala / promptu shella. Czyta stan sesji i transkrypty lokalnie, bez narzutu sieciowego i bez ryzyka blokowania promptu.
   - **`once`**: Pełny snapshot aktywnej sesji AGY (model, workspace, branch, tury, narzędzia, tokeny, koszty, quota, stan procesów) w formacie JSON oraz atomowy zapis do `runtime/quota.json`.
   - **`sessions`**: Czytelna tabela podsumowująca ostatnie 15 konwersacji w IDE i CLI (powierzchnia, tury, narzędzia, estymowane tokeny i ekwiwalent kosztowy API).
   - **`daemon`**: Demon działający w tle (domyślnie co 15s), monitorujący aktywne sesje i odświeżający plik stanu `runtime/quota.json`.
2. **Quota & Rate-Limit Detector (429 Tracking)**:
   - Wychwytuje zdarzenia `RESOURCE_EXHAUSTED (code 429)` z transkryptów AGY (`ERROR_MESSAGE`).
   - Wyciąga dokładny czas resetu (`Resets in 166h...`), śledzi czas od wystąpienia błędu i alarmuje w statusline (`⚠429: Individual quota reached...`).
   - Automatycznie czyści flagę błędu, gdy kolejna tura zakończy się sukcesem (`last_success_ts >= last_429_ts`).
3. **Shadow Pricing (`≈$X.XXX api-equiv`)**:
   - Wylicza równoważnik kosztu API według oficjalnych stawek Gemini (Flash, Pro) i modeli partnerskich (Claude 3.7/Sonnet, GPT-OSS).
   - Jasne oznaczenie `api-equiv` zapobiega myleniu kosztu z abonamentem.
4. **Pojedyncze źródło prawdy (`agy-monitor.toml`)**:
   - Centralna konfiguracja stawek cennika, progów alarmowych i interwałów.

---

## Zawartość pakietu

- `agy_monitor.py` — silnik monitora i statusline (czysty Python >= 3.11, standard library only).
- `agy-monitor.toml` — cennik per model i konfiguracja ścieżek.
- `install.sh` — instalator z wykrywaniem środowiska Python, backupem TOML, symlinkiem do PATH i opcjonalnym LaunchAgentem.
- `uninstall.sh` — bezpieczny deinstalator (usuwa symlink i wyłącza LaunchAgent).
- `com.google.antigravity.monitor.plist.template` — szablon usługi systemowej macOS LaunchAgent.

---

## Szybka instalacja

Rozpakuj i uruchom:

```bash
tar -xzf agy-monitor-v1.0.0.tar.gz
cd agy-monitor
./install.sh
```

Instalator automatycznie:
1. Sprawdza obecność interpretera Python >= 3.11 (ze wsparciem `tomllib`).
2. Tworzy strukturę `~/.gemini/agy-monitor/{runtime,logs}`.
3. Kopiuje silnik oraz konfigurację (zachowując istniejący plik `.toml`, chyba że podano `--force`).
4. Tworzy symlink `~/.local/bin/agy-monitor`.
5. Na systemie macOS rejestruje i uruchamia usługę w tle `com.google.antigravity.monitor.plist`.
6. Wykonuje testowy przebieg `once` i wyświetla przykładowy statusline.

Opcje instalatora:
- `--force`, `-f` — nadpisuje istniejącą konfigurację `agy-monitor.toml` (robiąc wcześniej kopię `.bak`).
- `--no-daemon` — pomija instalację LaunchAgenta w tle.

---

## Przykłady wyjścia

### Statusline (`agy-monitor line`)

Normalny stan:
```text
agy[ide] (3.8-flash-high) · vibecrafted:stack/command-bridge-on-pr-96 · turn 67 (821 tools) · ~573k toks · ≈$0.137 api-equiv · quota: OK
```

Gdy limit 429 zostanie osiągnięty:
```text
agy[ide] (3.8-flash-high) · vibecrafted:main · turn 12 · ~120k toks · ≈$0.029 api-equiv · ⚠429: Individual quota reached. Resets in 166h (4m temu)
```

### Podsumowanie sesji (`agy-monitor sessions`)

```text
======================================================================================================================================================
Google Antigravity (AGY) — Ostatnie konwersacje
======================================================================================================================================================
Surface  Conversation ID                        Model                Turns   Tools  Tokens (est.)   API Equiv  Last Activity        Workspace
------------------------------------------------------------------------------------------------------------------------------------------------------
IDE      ff6e1812-4f51-4da3-aee0-bd0854fdd263  gemini-3.8-flash-...     67     821        573,420     $0.1374  2026-09-21 14:34:25  vibecrafted
IDE      537437c1-7e60-475b-b3c8-82575993c1de  gemini-3.8-flash-...     42     318        312,150     $0.0749  2026-09-17 11:20:00  vibecrafted
CLI      8a9b2c3d-1234-4567-890a-bcdef0123456  gemini-3.8-flash-...     15      60        105,800     $0.0254  2026-09-16 09:12:44  dotfiles
======================================================================================================================================================
```

### Pełny snapshot JSON (`agy-monitor once`)

Zwraca kompletny obiekt JSON zawierający m.in.:
- `timestamp`
- `active_session` (id, surface, model, workspace_path, workspace_name, git_branch, turns, tool_calls, tokens, shadow_cost_usd)
- `quota` (status, last_429_at, error_snippet, reset_duration, reset_at_est)
- `processes` (antigravity-ide, agy CLI)

---

## Integracja ze statusem powłoki

### Starship (`~/.config/starship.toml`)

```toml
[custom.agy]
command = "agy-monitor line"
when = "test -d ~/.gemini"
format = "[$output]($style) "
style = "bold cyan"
```

### Tmux (`~/.tmux.conf`)

```tmux
set -g status-right '#(agy-monitor line) | %H:%M '
```

### Zsh Prompt (`~/.zshrc`)

```bash
agy_prompt() {
    agy-monitor line 2>/dev/null
}
RPROMPT='$(agy_prompt)'
```

---

## Zarządzanie usługą daemon (macOS)

```bash
# Sprawdzenie statusu
launchctl list | grep com.google.antigravity.monitor

# Podgląd logów daemona
tail -f ~/.gemini/agy-monitor/logs/agy-monitor.log

# Ręczny restart usługi
launchctl unload ~/Library/LaunchAgents/com.google.antigravity.monitor.plist
launchctl load ~/Library/LaunchAgents/com.google.antigravity.monitor.plist
```

---

## Deinstalacja

Aby odinstalować usługę i usunąć symlink:

```bash
cd agy-monitor   # lub ~/.gemini/agy-monitor
./uninstall.sh
```
