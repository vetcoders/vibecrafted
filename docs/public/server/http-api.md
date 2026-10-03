---
title: "HTTP API"
description: "Read-model reference for the local server: health, control-plane state, runs, lifecycle runs, and the SSE event stream."
section: server
order: 20
---

# HTTP API

The server exposes the control plane as a small JSON API on
`http://127.0.0.1:3024`. Every control route is a read over
`~/.vibecrafted/control_plane/` (or `$VIBECRAFTED_HOME`). `vc-server` never
writes durable run state. Observation may ask the canonical Python writer to
revalidate qualified process identity; only that writer may issue a receipted
`active -> failed` transition.
The claim door likewise delegates to the canonical Python dispatch writer;
HTTP acceptance never certifies a cut or updates the tracker.

## Endpoints

| Method | Path                                 | Purpose                                                                                                               |
| ------ | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/health`                        | Constant-time process readiness. Never scans the control plane.                                                       |
| GET    | `/api/control/state`                 | Cached state view: active/recent runs, warnings, event tail, settlement.                                              |
| GET    | `/api/control/runs`                  | Every run snapshot, newest-first.                                                                                     |
| GET    | `/api/control/runs/{run_id}`         | One run by id, or a `404` JSON body.                                                                                  |
| GET    | `/api/control/runs/{run_id}/observe` | Versioned one-shot observation; never arms a monitor.                                                                 |
| GET    | `/api/control/runs/{run_id}/await`   | Shared blocking subscription for the run.                                                                             |
| GET    | `/api/control/transcripts?q=`        | Host-wide human-log search. Paginated (`offset`, `limit`, `has_more`, `total`); each file is streamed from the start. |
| POST   | `/api/structure/report`              | Local-peer: run `loct report --output .loctree/report.html` in a known control-plane workspace root.                  |
| POST   | `/api/dispatch/claim`                | Loopback headless peer: hand an unverified worker claim to the canonical Python writer.                               |
| GET    | `/api/control/lifecycle`             | Lifecycle run summaries, newest-first.                                                                                |
| GET    | `/api/control/lifecycle/{run_id}`    | Full nested lifecycle state with per-run and per-stage axes.                                                          |
| GET    | `/api/control/events`                | Server-Sent Events stream of the control-plane event log.                                                             |
| GET    | `/api/usage`                         | Read-only `vibecrafted.usage-report.v1` usage and cost projection.                                                    |

Run payloads serialise the delivery-proof axes (`execution_state`,
`proof_state`, `delivery_state`) and `seal` only when the snapshot or kernel
receipt carries them. Absent axes stay absent — a `completed` state is never
promoted into a delivery claim.

## Dispatch claim door

`POST /api/dispatch/claim` rings the dispatch doorbell. Send JSON with the
dispatch `run_id`, `cut_id`, exact `commit_sha`, registered absolute
`report_path`, and a nonempty `measurements` array describing the worker's
self-checks. The Python writer validates the registered dispatch/cut and
claimed SHA in its runtime root before recording `[~]` in existing dispatch
receipts. Rust transports the request; it never writes those receipts or the
tracker.

```bash
curl --fail-with-body -X POST http://127.0.0.1:3024/api/dispatch/claim \
  -H 'content-type: application/json' \
  -d '{"run_id":"disp-example","cut_id":"w1-01","commit_sha":"<full-SHA>","report_path":"/absolute/registered/report.md","measurements":["Owned scope self-check passed"]}'
```

Successful recording returns HTTP `202` with `status: "claim_received"`,
`marker: "[~]"`, `verification: "unverified"`, and
`writer: "vibecrafted_core.dispatch.claims"`. This acknowledges recording
only. The canonical writer subsequently runs the cut's full
`VERIFICATION_RULE` and declared verifiers in the registered runtime root
against the claimed SHA. Only every matcher passing permits tracker `[x]`;
a red matcher stays `[!]` and journal evidence includes the verifier cwd.
Missing claims leave verification unrun and require dispatch resume, not a
synthetic failed-test verdict.
The worker measures with the tools its own shell permits; the writer executes
declared verifier commands on its separate shell. Verifiers default to a
600-second timeout and a sanitized environment, so keys such as
`DEVELOPER_DIR` are absent unless the declared command inlines them.

Under a compile embargo, include
`"checkpoint":{"owned_scope":["src/editor.rs"],"skipped_controls":["cargo test","semgrep"]}`,
enumerating every actually skipped control, including security hooks. The
checkpoint stays unverified. `W2_STRUCTURALLY_CLOSED` means assembled and
ready to check; the integrator must run all gates and skipped controls on
the assembled SHA before settlement. The POST cannot close an embargo.

The transport requires verified loopback `ConnectInfo`, JSON content type,
and a body of at most 64 KiB. Browser `Origin` headers (including `null`)
and cross-site fetches are refused. The server selects Python from its own
`VIBECRAFTED_PYTHON` or `VIBECRAFTED_RUNTIME_ROOT/bin/python3`, requires an
absolute executable, clears `PYTHONPATH`, and invokes the module in isolated
Python mode. Runtime packs use a fixed bootstrap adding only their selected
generation's `vibecrafted-core` and `python-site` directories. There is no
request-selected executable, module root, or shell command.
Recording has a 30-second timeout; a timeout may follow a durable write,
so inspect the receipt before retrying. Writer rejection returns `400`,
unavailable generation `503`, and transport failure `502`/`504`.

## Run observation v1

`GET /api/control/runs/{run_id}/observe` returns
`vibecrafted.run-observation.v1`: identity, canonical control-plane home,
terminality, qualified process truth, the typed run projection, report and
transcript references, and explicit monitor/AICX witness availability.
Repeated or concurrent observe calls remain one-shot and do not grow the await
registry.

`GET /api/control/runs/{run_id}/await` returns
`vibecrafted.run-await-verdict.v1`. Subscribers fan into one ephemeral monitor
per control-plane home plus run id. Terminal settlement wakes every subscriber
with the same observation; the monitor then closes. Last-subscriber disconnect
starts a short cleanup grace and never mutates the run.

Query parameters:

- `idle_timeout`: resettable idle window in seconds (default `300`);
- `hard_cap`: optional absolute wall-clock cap in seconds;
- `interval`: accepted for client grammar compatibility; the server owns the
  shared polling cadence.

Structured outcomes are `terminal`, `not_found`, `idle_stall`, `hard_cap`,
`evidence_disagreement`, and `server_unavailable`. Timeout and conflict
responses retain their JSON verdict even when the HTTP status is `408` or
`409`.

## Health

```bash
curl -s http://127.0.0.1:3024/api/health
```

```json
{ "schema": "vibecrafted.health.v1", "status": "ok" }
```

Health deliberately does not read the control plane: a long retained history
can make the state projection expensive without making the process unhealthy.

## Board state

`/api/control/state` is the board slice shared by the dashboard, the MCP
`vc_board_status` tool, and the Slack `/vc status` command. The response is
cached in-process for a short TTL; a stale cache is returned immediately
while one background refresh re-reads the durable snapshots.

```bash
curl -s http://127.0.0.1:3024/api/control/state | python3 -m json.tool
```

Truncated example:

```json
{
  "control_plane": "~/.vibecrafted/control_plane",
  "generated_at": "2026-07-30T12:00:00+00:00",
  "active_runs": [
    {
      "run_id": "impl-20260730-a1b2",
      "state": "running",
      "agent": "codex",
      "skill": "implement",
      "root": "~/projects/my-app",
      "health": "healthy",
      "started_at": "2026-07-30T11:41:02+00:00",
      "latest_report": "",
      "lock_present": true
    }
  ],
  "recent_runs": [],
  "warnings": [],
  "events": [
    {
      "ts": "2026-07-30T11:41:02+00:00",
      "run_id": "impl-20260730-a1b2",
      "kind": "launch",
      "message": "worker launched",
      "cursor": 42
    }
  ],
  "settlement_counts": {
    "active": 1,
    "f": 12,
    "x": 1,
    "n": 3,
    "invalid": 0,
    "unclassified": 0,
    "total_settled": 16
  }
}
```

## Runs and lifecycle

```bash
# every retained run snapshot
curl -s http://127.0.0.1:3024/api/control/runs | python3 -m json.tool | head

# one run (404 JSON when unknown)
curl -s http://127.0.0.1:3024/api/control/runs/impl-20260730-a1b2

# search human transcripts (needle may live anywhere in the log; page with offset/limit)
curl -s "http://127.0.0.1:3024/api/control/transcripts?q=needle&offset=0&limit=50"

# generate a Loctree report for a control-plane-known workspace (local peer only)
curl -s -X POST http://127.0.0.1:3024/api/structure/report \
  -H 'content-type: application/json' \
  -d '{"root":"/absolute/workspace"}'

# lifecycle list, then one lifecycle run in full
curl -s http://127.0.0.1:3024/api/control/lifecycle
curl -s http://127.0.0.1:3024/api/control/lifecycle/life-ship-20260730-c3d4
```

List responses carry a `count` and the resolved `control_plane` path, so you
can verify which home the server is reading.

## Usage and costs

`GET /api/usage` reads canonical `runtime_runs/*/meta.json` records and returns
token totals, costs grouped by unit, failures, and provider, agent, and model
dimensions. Missing measurements remain explicit unknowns; USD, credits, and
other provider units are never added together.

Query parameters:

- `window`: `24h` (default), `7d`, `30d`, or `all`;
- `provider`: exact provider filter;
- `agent`: exact agent filter;
- `model`: exact model filter.

The human dashboard is available at `/usage` and applies the same filters.
Both surfaces are read-only and send `Cache-Control: no-store`.

## Event stream (SSE)

`/api/control/events` streams the control-plane event log as Server-Sent
Events, with `: ping` keepalives. Resume from a cursor with `?since=` or the
standard `Last-Event-ID` header:

```bash
curl -N "http://127.0.0.1:3024/api/control/events?since=42"
```

Each event's `cursor` value is the id to resume from after a disconnect.

## Scaffold editor surface

The server also hosts a plan-editor surface for scaffold artifacts
(`/scaffold/editor`, `GET /api/scaffold/plans|artifacts|changes`,
`POST /api/scaffold/artifact|checkpoint|status`). The POST routes save plan
artifacts into the artifact store — they do not touch run status. This
surface is subject to change; the control routes above are the stable API.
