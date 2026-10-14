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

Codex, Claude, Grok, Junie, Copilot, and Kimi read the local session store and
do not modify it. Each snapshot declares `cache_semantics` (`subset_of_input`
or `separate_buckets`) and a `processed_total` that does not add a cache
bucket twice. A day once observed is appended to
`$VIBECRAFTED_HOME/telemetry/archive/<agent>.jsonl`. Reading that file is
last-wins per `(day, model)`; the file is not compacted. A rotated source
stays in the scan sidecar, so a shared day does not shrink to the files still
on disk. A session that only moved paths is not counted twice. `live_total`
is the live store. `archived_total` is the archive. Cursor has no local token ledger: the engine reports `status:
unavailable` and the reason `usage lives in Cursor cloud`. It does not invent
zeros and it does not grow an archive. Agy transcripts are estimated tokens,
so `telemetry agy archive` is the same honest unavailable. A missing engine
script is `status: absent` inside `telemetry once`. Gemini has no engine here.

Both engines are **local-only**. They do not send usage to a Vibecrafted backend.
Shadow prices are labeled `api-equiv` so they cannot be mistaken for a
subscription charge.

## Command surface

```text
telemetry                 # help
telemetry smoke …         # existing marbles smoke (unchanged)
telemetry agy line|once|sessions|daemon|archive
telemetry kimi line|once|daemon|archive
telemetry codex line|once|sessions|daemon|archive
telemetry claude line|once|sessions|daemon|archive
telemetry grok line|once|sessions|daemon|archive
telemetry junie line|once|sessions|daemon|archive
telemetry copilot line|once|sessions|daemon|archive
telemetry cursor line|once|sessions|daemon|archive
telemetry line            # statuslines of present engines
telemetry once            # JSON union; each engine adds live_total and archived_total
telemetry archive         # backfill every present engine into the daily archive
```

`agy-monitor` / `kimi-monitor` are **not** published onto PATH. Standalone
`install.sh` in each engine dir remains for operators who already installed
those names; the product launcher is `telemetry`.

## Who collects

`vc-server` owns collection. On its own schedule (every 300 s by default;
`VIBECRAFTED_USAGE_COLLECT_INTERVAL_S`, `0` disables) it runs one bounded
one-shot of these same engines — `fleet_engine` for six providers, the kimi
harvest — which refreshes `$VIBECRAFTED_HOME/telemetry/<agent>/` (`scan-cache.json`,
`quota.json`) and `archive/<agent>.jsonl`. Nothing stays resident: no per-provider
daemon and no Python monitor child is needed for the numbers to move, and a
second collection inside the interval is a no-op. Everything on that schedule
reads local files only.

The network is touched solely by a quota refresh, on explicit request
(`GET /api/usage/quota?refresh=network`, the "Refresh quota" button) or as a
plan's preflight (`refresh=preflight&agents=a,b`). Only providers with a
provider-owned source answer (kimi probe, agy transcript 429 scan); every other
provider reports `unknown` — quota exhaustion is never inferred from token
totals. Agy keeps estimated transcripts only, so its local usage stays
`unavailable` rather than becoming a zero.

## Web

`vc-server` `/usage` shows one card per provider (all eight, alphabetical, no
provider first) from `GET /api/usage/quota`: `state` (`ok`, `low`, `exhausted`,
`stale`, `unknown`, `absent`, `malformed`), source, age and pool/account/model
scope. `exhausted` is only ever a fresh observation; an old one is `stale`. The
usage table merges Vibecrafted runs (`origin: vc`) with all other local sessions
(`origin: local`); a session a VC run already carries is counted once. Shadow
prices stay `api-equiv`.

## LaunchAgents (legacy, optional)

The server-owned collector replaces these; they remain only for operators who
already installed the standalone engines. Templates:

- `com.vetcoders.telemetry.agy.plist.template`
- `com.vetcoders.telemetry.kimi.plist.template`

ProgramArguments exec `telemetry <agent> daemon`. Do not ship Google's or
Moonshot's reverse-DNS.
