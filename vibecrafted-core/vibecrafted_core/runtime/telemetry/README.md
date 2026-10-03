# Telemetry — agent quota engines

Owned PATH name: **`telemetry`** (see AGENTS.md: `vc-*` / `vibecrafted*` / `vibecraft` / `telemetry`).

This directory vendors the local quota / statusline engines:

| Engine             | Agent                      | Writes                                           |
| ------------------ | -------------------------- | ------------------------------------------------ |
| `agy-monitor/`     | Google Antigravity (`agy`) | `~/.gemini/agy-monitor/runtime/quota.json`       |
| `kimi-monitor/`    | Kimi Code CLI              | `~/.kimi-code/runtime/quota.json`                |
| `codex-monitor/`   | Codex                      | `$VIBECRAFTED_HOME/telemetry/codex/quota.json`   |
| `claude-monitor/`  | Claude                     | `$VIBECRAFTED_HOME/telemetry/claude/quota.json`  |
| `grok-monitor/`    | Grok                       | `$VIBECRAFTED_HOME/telemetry/grok/quota.json`    |
| `junie-monitor/`   | Junie                      | `$VIBECRAFTED_HOME/telemetry/junie/quota.json`   |
| `copilot-monitor/` | Copilot                    | `$VIBECRAFTED_HOME/telemetry/copilot/quota.json` |
| `cursor-monitor/`  | Cursor                     | `$VIBECRAFTED_HOME/telemetry/cursor/quota.json`  |

Codex, Claude, Grok, Junie, and Copilot read the local session store and do not
modify it. Each snapshot declares `cache_semantics` (`subset_of_input` or
`separate_buckets`) and a `processed_total` that does not add a cache bucket
twice. Cursor has no local token ledger: the engine reports `status:
unavailable` and the reason `usage lives in Cursor cloud`. It does not invent
zeros. A missing engine script is `status: absent` inside `telemetry once`.

Both engines are **local-only**. They do not send usage to a Vibecrafted backend.
Shadow prices are labeled `api-equiv` so they cannot be mistaken for a
subscription charge.

## Command surface

```text
telemetry                 # help
telemetry smoke …         # existing marbles smoke (unchanged)
telemetry agy line|once|sessions|daemon
telemetry kimi line|once|daemon
telemetry codex line|once|sessions|daemon
telemetry claude line|once|sessions|daemon
telemetry grok line|once|sessions|daemon
telemetry junie line|once|sessions|daemon
telemetry copilot line|once|sessions|daemon
telemetry cursor line|once|sessions|daemon
telemetry line            # statuslines of present engines
telemetry once            # JSON union of all eight agents
```

`agy-monitor` / `kimi-monitor` are **not** published onto PATH. Standalone
`install.sh` in each engine dir remains for operators who already installed
those names; the product launcher is `telemetry`.

## voc

Mission Control fleet health reads the quota JSON files on-read (no daemon
required for the panel). Missing files are silent — the operator may not run
that agent. Daemons keep the files fresh.

## Web

`vc-server` `/usage` leads with the same files: two cards (Antigravity, Kimi)
above Cost & usage. `GET /api/usage/quota`. Shadow prices stay `api-equiv`.
A missing file is a quiet card, not an alarm.

## LaunchAgents (optional)

Templates:

- `com.vetcoders.telemetry.agy.plist.template`
- `com.vetcoders.telemetry.kimi.plist.template`

ProgramArguments exec `telemetry <agent> daemon`. Do not ship Google's or
Moonshot's reverse-DNS.
