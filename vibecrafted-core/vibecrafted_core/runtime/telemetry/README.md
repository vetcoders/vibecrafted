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
plan's preflight (`refresh=preflight&agents=a,b`). Providers with a provider-owned source answer: Codex reads native local
rate-limit observations, Kimi uses its existing explicit probe, and Agy reads
native 429 evidence. Other providers report `unknown` — quota exhaustion is
never inferred from token totals. Codex quota refresh stays local and does not
backfill token histories or the usage archive. Agy keeps estimated transcripts only, so its local usage stays
`unavailable` rather than becoming a zero.

## Native quota observations

Codex `event_msg` / `token_count` records can carry `payload.rate_limits`
independently of token usage. The collector preserves the native percentage,
window duration, reset epoch and observation timestamp under `quota.windows`:

```json
{
  "id": "primary",
  "used_ratio": 0.25,
  "remaining_ratio": 0.75,
  "window_minutes": 10080,
  "resets_at": 1893456000,
  "observed_at": "2029-12-25T00:00:00Z",
  "source": "codex.native.token_count.rate_limits",
  "scope": { "pool": "openai", "limit_id": "codex", "plan_type": "pro" }
}
```

`remaining_ratio` is the complement of the native used percentage; no absolute
token allowance is invented. A 10080-minute window is seven days, not a daily
or monthly allowance. Account/model scope is included only when explicitly
reported by the native quota record. `CODEX_HOME` and `CLAUDE_CONFIG_DIR` select
the native local stores when set.

The adapter reads at most eight recent rollout tails of 512 KiB and keeps its
versioned observations in the existing `scan-cache.json`. An unchanged source
is not reopened. A known event moved beyond the tail by appended tool output
is retained only after checking that its original bytes still match; its
original timestamp remains. Truncation/replacement cannot inherit that event.
The native timestamp, rather than collection `generated_at`, determines age;
an elapsed reset or observation older than five minutes is stale. An explicit
null secondary removes the old secondary window for that native limit.

`refresh_native_quota` is the same collector's local-only quota refresh entry;
it writes the existing projection and cache, starts no provider and creates no
separate scheduler or store. All fleet publication paths share a nonblocking
per-projection lease, acquired before reading the cache and released before a
daemon sleeps. A concurrent quota refresh reports `TelemetryBusy`, not a false
unavailable measurement; the CLI returns busy/75. Cache and quota writes use
unique private temporary files. Already running pre-lease legacy daemons need
an ordinary upgrade before they can participate in this ownership contract.
An older token-only cache is inspected once for
quota without invalidating all token history. Missing or malformed native
observations remain unavailable, distinct from a measured exhausted window.

Claude, Grok, Junie, Copilot and OMP adapters currently expose local token
usage, not remaining subscription capacity. Cursor has no local usage source.
Kimi's existing adapter can expose 5h/month windows from its explicit source;
Agy's existing adapter can report observed exhaustion/reset evidence. Absence
of either native snapshot remains unavailable. No extra network or credential
flow is introduced by the Codex adapter.

## Web

`vc-server` `/usage` leads with measured totals and usage charts. Fresh measured
quota windows from `GET /api/usage/quota` follow them; unavailable or stale
providers and source diagnostics remain in expandable details. The API keeps
`state` (`ok`, `low`, `exhausted`, `stale`, `unknown`, `absent`, `malformed`),
source, age and pool/account/model scope. `exhausted` is only ever a fresh
observation; an old one is `stale`. Model/account filters apply to native windows
before severity is aggregated. Background transcript recovery reports pending,
unavailable and retained previous measurements separately. The
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
