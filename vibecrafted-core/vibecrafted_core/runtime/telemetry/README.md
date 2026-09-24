# Telemetry — agent quota engines

Owned PATH name: **`telemetry`** (see AGENTS.md: `vc-*` / `vibecrafted*` / `vibecraft` / `telemetry`).

This directory vendors the local quota / statusline engines:

| Engine          | Agent                      | Writes                                     |
| --------------- | -------------------------- | ------------------------------------------ |
| `agy-monitor/`  | Google Antigravity (`agy`) | `~/.gemini/agy-monitor/runtime/quota.json` |
| `kimi-monitor/` | Kimi Code CLI              | `~/.kimi-code/runtime/quota.json`          |

Both engines are **local-only**. They do not send usage to a Vibecrafted backend.
Shadow prices are labeled `api-equiv` so they cannot be mistaken for a
subscription charge.

## Command surface

```text
telemetry                 # help
telemetry smoke …         # existing marbles smoke (unchanged)
telemetry agy line|once|sessions|daemon
telemetry kimi line|once|daemon
telemetry line            # both statuslines, one per present engine
telemetry once            # JSON union { "agy": …, "kimi": … }
```

`agy-monitor` / `kimi-monitor` are **not** published onto PATH. Standalone
`install.sh` in each engine dir remains for operators who already installed
those names; the product launcher is `telemetry`.

## voc

Mission Control fleet health reads the quota JSON files on-read (no daemon
required for the panel). Missing files are silent — the operator may not run
that agent. Daemons keep the files fresh.

## LaunchAgents (optional)

Templates:

- `com.vetcoders.telemetry.agy.plist.template`
- `com.vetcoders.telemetry.kimi.plist.template`

ProgramArguments exec `telemetry <agent> daemon`. Do not ship Google's or
Moonshot's reverse-DNS.
