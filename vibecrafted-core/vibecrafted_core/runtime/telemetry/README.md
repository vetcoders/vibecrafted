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
is the live store. `archived_total` is the archive. The standalone Cursor archive remains unavailable. The central collector can
read exact usage in local Cursor agent transcripts, while cloud-only activity
remains unknown. Agy transcripts are estimated tokens,
so `telemetry agy archive` is the same honest unavailable. A missing engine
script is `status: absent` inside `telemetry once`. Gemini has no engine here.

The local usage parsers are **local-only**. They do not send usage to a Vibecrafted backend.
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

## voc

Mission Control fleet health reads the quota JSON files on-read (no daemon
required for the panel). Missing files are silent — the operator may not run
that agent. Daemons keep the files fresh.

## Web

`vc-server` `/usage` gives all eight providers equal quota cards and
Provider/Agent/Model pickers backed by `GET /api/usage`. The server owns
central local collection. `GET /api/usage/quota` reads scoped provider
observations; missing authority stays unknown and stale remains visible.

## LaunchAgents (optional)

Templates:

- `com.vetcoders.telemetry.agy.plist.template`
- `com.vetcoders.telemetry.kimi.plist.template`

These legacy optional templates execute `telemetry <agent> daemon`. The
central server collector does not launch them or depend on them for usage.
Existing host service ownership and migration remain with the runtime owner.

## Central local collection and quota admission

`vc-server` owns one local collection task. It runs a bounded, disposable
parser sweep every 60 seconds, calls the existing fleet/harness parsers, and
publishes `telemetry/local-usage.json`. It does not start provider tasks,
make network quota requests in this loop, or keep Python/provider monitor
children alive. A failed sweep retains the previous projection, which ages
to `stale` after 300 seconds. `GET /api/usage` exposes collection time,
coverage, unknown measurements and local/VC origin.

Local session identity is scoped by agent. Repeated imports are counted once.
A local session that includes VC activity carries the matching `vc_run_ids`;
its full observed session spend replaces those run slices. A session file
alone does not establish completion or exit code. Day archive rows lack
session attribution and are excluded from session totals to avoid adding
VC spend twice; this limitation is explicit in the report.

Provider, Agent and Model pickers use the same identifiers as API dimensions.
They preserve unknown and currently unobserved selections. Quota checks run
on explicit page entry/refresh and task preflight, without idle network polling.
The shared quota endpoint accepts `agent`, `model`, `provider`, `account` and
`pool`. It reads all eight source adapters equally. Native Kimi ratios remain supported. Legacy Agy log-derived status is
unconfirmed: its monitor republishes snapshot time even for an old error,
so only an explicit authoritative observation can refuse admission. Neither
monitor is required for local usage collection.

A provider snapshot may publish a scoped observation (or `observations` array):

```json
{
  "authoritative": true,
  "status": "exhausted",
  "source": "provider quota response",
  "observed_at": "2026-10-12T20:00:00Z",
  "account": "default",
  "pool": "provider-shared-pool",
  "model": null
}
```

`model: null` means a provider-wide pool observation. A named model only
covers that exact effective selection; a different account is not assumed
to be active. A reading with no authoritative evidence or observation time
is unknown. An old exhaustion reading is stale; 95% use is a warning, not
confirmed exhaustion. Usage token totals never prove quota exhaustion.

`vibecrafted dispatch <plan.toml> --doctor` and actual batch dispatch consume
these shared observations using models already resolved by the canonical
frontmatter/plan/config selection owner. Fresh confirmed exhaustion refuses
the whole new batch before its supervisor starts. To explicitly bypass only
quota admission, use `--ignore-usage-quota-warnings`. Diagnosis remains
visible, and `quota-override-receipt.json` contains the exact observation,
selection, dispatch identity and source digest. Failure to publish that
receipt refuses admission. Schema, baseline and other admission gates remain
active. Existing work is not stopped or automatically resumed, and providers
or models are not automatically switched.

External Copilot provider profiles reuse the canonical config/environment
model selection. A native GitHub quota reading does not describe a BYOK
provider account. Without a canonical mapping to that external account/pool,
preflight reports unknown and retains the shared reading as diagnosis; it
never uses an unrelated GitHub limit to refuse the task.
