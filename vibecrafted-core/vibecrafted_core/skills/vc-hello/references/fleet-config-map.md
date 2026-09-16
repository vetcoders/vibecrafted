# Fleet Config Map — canonical locations and key semantics

The one place that answers "where does this CLI keep its config and what does
the key mean" — so no agent ever `find`s blindly across the home directory.
Project-local dirs (`.claude/`, `.codex/`, `.grok/` in a repo) hold per-repo
memory and reports; the **posture carrier is the user-level config** listed
below.

## Locations

| CLI    | User-level config                                    | Format      | Notes                                                              |
| ------ | ---------------------------------------------------- | ----------- | ------------------------------------------------------------------ |
| Claude | `~/.claude/settings.json`                            | JSON        | hooks, permissions, model, theme all in one file                   |
| Codex  | `~/.codex/config.toml`                               | TOML        | large; MCP servers carry `env` blocks (secrets — never copy)       |
| Grok   | `~/.grok/config.toml`                                | TOML        | `[ui]` / `[models]` / `[cli]` sections                             |
| Kimi   | `~/.kimi-code/config.toml` + `tui.toml` + `mcp.json` | TOML + JSON | runtime vs TUI split; MCP lives in `mcp.json`, **not** config.toml |

`KIMI_CODE_HOME` overrides the kimi dir when set; resolve it first, never assume.

## Posture axes — how each CLI spells them

### Permission posture (fleet consensus: full-auto)

| CLI    | Key                                                             | full-auto spelling                           |
| ------ | --------------------------------------------------------------- | -------------------------------------------- |
| Claude | `permissions.defaultMode` + `skipDangerousModePermissionPrompt` | `"auto"` + `true` (or `bypassPermissions`)   |
| Codex  | `approval_policy` + `sandbox_mode`                              | `"never"` + `"danger-full-access"`           |
| Grok   | `ui.permission_mode` (or `ui.yolo`)                             | `"always-approve"`                           |
| Kimi   | `default_permission_mode`                                       | `"yolo"` (modes: `manual` / `auto` / `yolo`) |

### Effort (fleet consensus: top tier)

| CLI    | Key                               | Notes                                                                |
| ------ | --------------------------------- | -------------------------------------------------------------------- |
| Claude | `effortLevel`                     | `xhigh` observed                                                     |
| Codex  | `model_reasoning_effort`          | `low` observed — the fleet outlier                                   |
| Grok   | `models.default_reasoning_effort` | `xhigh`                                                              |
| Kimi   | `[thinking].effort`               | must be in the model's `support_efforts`; k3 tops at `max` (≈ xhigh) |

### Theme / language / auto-update

| Axis        | Claude                         | Codex                       | Grok                          | Kimi                                           |
| ----------- | ------------------------------ | --------------------------- | ----------------------------- | ---------------------------------------------- |
| Theme       | `theme: "dark"`                | `tui.theme: "gruvbox-dark"` | — (no key)                    | `tui.toml theme: "dark"`                       |
| Language    | `language: "Polish"`           | — (no key)                  | `ui.voice_stt_language: "pl"` | — (no key; matches user language behaviorally) |
| Auto-update | `autoUpdatesChannel: "latest"` | — (no key)                  | `cli.auto_update: true`       | `tui.toml [upgrade].auto_install: true`        |

## MCP servers

- Claude: `mcpServers` (JSON) + plugin system (`enabledPlugins`, e.g. context7, playwright).
- Codex: `[mcp_servers.*]` tables; some carry `[mcp_servers.*.env]` with API keys — **skip + report, never copy**.
- Kimi: `~/.kimi-code/mcp.json`, `{ "mcpServers": { name: { command|url } } }`; stdio vs HTTP inferred from `command` vs `url`.
- Shared by ≥2 members (as of 2026-09): `aicx`, `context7`, `loctree`, `playwright`.

## Hooks — what carries over and what does not

Kimi's hook protocol is deliberately Claude-shaped (`tool_input.command`,
top-level `cwd`; exit 2 + stderr blocks; `hookSpecificOutput.permissionDecision`
honored), so Claude guard scripts run unchanged. Codex hooks live in
`~/.codex/hooks.json` + `hooks.state` trust entries in config.toml.

| Hook (family)                                   | Claude            | Codex        | Kimi | Carries?                                                                                            |
| ----------------------------------------------- | ----------------- | ------------ | ---- | --------------------------------------------------------------------------------------------------- |
| `loctree-first-guard` (PreToolUse Bash)         | yes               | —            | yes  | yes — same payload fields, exit-2 semantics                                                         |
| `aicx-compact` (PreCompact extract)             | yes               | yes (plugin) | yes  | yes — fail-open, reads `session_id` from stdin                                                      |
| `strip-redirect`                                | yes               | —            | no   | no — uses Claude-only `updatedInput`, dead weight elsewhere                                         |
| `cc-status` (iTerm2)                            | yes (many events) | —            | no   | no — compiled binary tuned to Claude payloads                                                       |
| post-compact recall via `SessionStart(compact)` | yes               | yes          | no   | no — kimi `SessionStart` has no `compact` source; `PostCompact` is observer-only (known parity gap) |

## Validation gates per CLI

- Kimi: `kimi doctor config <file>` / `kimi doctor tui <file>` — schema-authoritative, run on the **candidate** before overwrite.
- Codex: config loads at startup; TOML syntax check via `python3 -c "import tomllib; tomllib.load(open(...))"` when no native validator is exposed.
- Claude: JSON parse + session start; hooks fail open.
- Grok: TOML parse; channel `alpha` moves keys fast — re-check docs each run.
