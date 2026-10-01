# Fleet Config Map — canonical locations and key semantics

Jedno miejsce odpowiada na pytanie „gdzie CLI trzyma konfigurację i co oznacza
klucz”, żeby agent nie używał `find` na ślepo w katalogu domowym. Katalogi projektu
(`.claude/`, `.codex/`, `.grok/` w repo) przechowują pamięć i raporty repo;
**nośnikiem postawy jest konfiguracja użytkownika** wymieniona niżej.

## Locations

| CLI     | User-level config                                    | Format      | Notes                                                                    |
| ------- | ---------------------------------------------------- | ----------- | ------------------------------------------------------------------------ |
| Claude  | `~/.claude/settings.json`                            | JSON        | hooki, uprawnienia, model i motyw w jednym pliku                         |
| Codex   | `~/.codex/config.toml`                               | TOML        | duży; serwery MCP mają bloki `env` (sekrety — nie kopiuj)                |
| Grok    | `~/.grok/config.toml`                                | TOML        | sekcje `[ui]` / `[models]` / `[cli]`                                     |
| Kimi    | `~/.kimi-code/config.toml` + `tui.toml` + `mcp.json` | TOML + JSON | rozdział runtime i TUI; MCP żyje w `mcp.json`, **nie** config.toml       |
| Copilot | `~/.copilot/hooks/vibecrafted-fleet.json`            | JSON        | tylko hooki — bez kluczy uprawnień/effort/motywu; te osie zostają `None` |

Ustawione `KIMI_CODE_HOME` nadpisuje katalog kimi; rozwiąż je najpierw, nie zakładaj ścieżki.

## Posture axes — how each CLI spells them

### Permission posture (fleet consensus: full-auto)

| CLI    | Key                                                             | full-auto spelling                           |
| ------ | --------------------------------------------------------------- | -------------------------------------------- |
| Claude | `permissions.defaultMode` + `skipDangerousModePermissionPrompt` | `"auto"` + `true` (or `bypassPermissions`)   |
| Codex  | `approval_policy` + `sandbox_mode`                              | `"never"` + `"danger-full-access"`           |
| Grok   | `ui.permission_mode` (or `ui.yolo`)                             | `"always-approve"`                           |
| Kimi   | `default_permission_mode`                                       | `"yolo"` (tryby: `manual` / `auto` / `yolo`) |

### Effort (fleet consensus: top tier)

| CLI    | Key                               | Notes                                                             |
| ------ | --------------------------------- | ----------------------------------------------------------------- |
| Claude | `effortLevel`                     | zaobserwowano `xhigh`                                             |
| Codex  | `model_reasoning_effort`          | zaobserwowano `low` — odstępstwo od floty                         |
| Grok   | `models.default_reasoning_effort` | `xhigh`                                                           |
| Kimi   | `[thinking].effort`               | musi być w `support_efforts` modelu; k3 kończy na `max` (≈ xhigh) |

### Theme / language / auto-update

| Axis        | Claude                         | Codex                       | Grok                          | Kimi                                                        |
| ----------- | ------------------------------ | --------------------------- | ----------------------------- | ----------------------------------------------------------- |
| Theme       | `theme: "dark"`                | `tui.theme: "gruvbox-dark"` | — (brak klucza)               | `tui.toml theme: "dark"`                                    |
| Language    | `language: "Polish"`           | — (brak klucza)             | `ui.voice_stt_language: "pl"` | — (brak klucza; behawioralnie dopasowuje język użytkownika) |
| Auto-update | `autoUpdatesChannel: "latest"` | — (brak klucza)             | `cli.auto_update: true`       | `tui.toml [upgrade].auto_install: true`                     |

## MCP servers

- Claude: `mcpServers` (JSON) + system pluginów (`enabledPlugins`, e.g. context7, playwright).
- Codex: `[mcp_servers.*]` tables; niektóre mają `[mcp_servers.*.env]` z kluczami API — **pomiń + opisz, nie kopiuj**.
- Kimi: `~/.kimi-code/mcp.json`, `{ "mcpServers": { name: { command|url } } }`; stdio albo HTTP wynika z `command` albo `url`.
- Wspólne dla ≥2 członków (stan 2026-09): `aicx`, `context7`, `loctree`, `playwright`.

## Hooks — what carries over and what does not

Protokół hooków Kimi celowo ma kształt Claude (`tool_input.command`,
`cwd` najwyższego poziomu; exit 2 + stderr blokuje; respektuje
`hookSpecificOutput.permissionDecision`), więc guardy Claude działają bez zmian.
Hooki Codex żyją w `~/.codex/hooks.json` + wpisach zaufania `hooks.state` w config.toml.

| Hook (family)                                   | Claude              | Codex        | Kimi | Carries?                                                                                             |
| ----------------------------------------------- | ------------------- | ------------ | ---- | ---------------------------------------------------------------------------------------------------- |
| `loctree-first-guard` (PreToolUse Bash)         | tak                 | —            | tak  | tak — te same pola payloadu i semantyka exit 2                                                       |
| `aicx-compact` (PreCompact extract)             | tak                 | tak (plugin) | tak  | tak — fail-open, czyta `session_id` ze stdin                                                         |
| `strip-redirect`                                | tak                 | —            | nie  | nie — używa `updatedInput` tylko z Claude; poza nim martwy ciężar                                    |
| `cc-status` (iTerm2)                            | tak (wiele zdarzeń) | —            | nie  | nie — binarka dostrojona do payloadów Claude                                                         |
| post-compact recall via `SessionStart(compact)` | tak                 | tak          | nie  | nie — kimi `SessionStart` nie ma źródła `compact`; `PostCompact` tylko obserwuje (znana luka parity) |

Copilot podłącza te same skrypty w formacie Claude (`aicx-sessionstart.sh`,
`loct-context-card.sh`, `aicx-precompact.sh`, `loctree-first-guard.py`) przez
`hook_bridge.py` (`python3 hook_bridge.py --to copilot --timeout N -- <wrapped
command>`), bo jego harness traktuje każdy niezerowy exit hooka jako hard deny.
Bridge zapewnia timeout + fail-open, których skrypty nie mają natywnie.
**Każda komenda hooka musi wskazywać rzeczywisty plik** — `fleet_scan.py check`
sprawdza ścieżkę bridge i opakowanego skryptu. Brak jednego odmawiał każdemu
wywołaniu bash przez noc 2026-09-28/29, zanim to zauważono (naprawa 2026-09-30,
detekcja dodana 2026-10-01).

## Validation gates per CLI

- Kimi: `kimi doctor config <file>` / `kimi doctor tui <file>` — autorytatywny schemat; uruchamiaj na **kandydacie** przed nadpisaniem.
- Codex: konfiguracja ładuje się przy starcie; składnię TOML sprawdź przez `python3 -c "import tomllib; tomllib.load(open(...))"` gdy brak natywnego walidatora.
- Claude: parsowanie JSON + start sesji; hooki fail-open.
- Grok: parsowanie TOML; kanał `alpha` szybko zmienia klucze — czytaj dokumentację w każdym przebiegu.
