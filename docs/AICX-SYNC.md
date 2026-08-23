# AICX cross-machine sync v2

_Plan 08 (META_22) — bidirectional sync substrate with authority-tier conflict resolution. Ships in v1.8.0._

The AICX corpus is the operator's cross-session memory layer for AI agents — every Codex / Claude / Gemini session writes its outcome into `~/.aicx/store/<org>/<project>/<YYYY_MMDD>/conversations/<agent>/*.md`. On a single machine that is enough. On the **Vetcoders mesh** (host-a ↔ host-c ↔ host-d ↔ host-b, per doctrine 2026-05-05) the same corpus must converge across hosts so an agent dispatched on `host-c` sees what `host-a` learned thirty minutes earlier.

This document covers the **v2 bidirectional engine** (`vibecrafted_core.aicx_sync`) and its operator CLI (`scripts/aicx-sync.sh`). The transport layer (rsync, staging, nightly launchd) is owned by the operator's existing `~/.scripts/sync-tool.py` (doctrine 2026-05-05); this engine plugs in on top of that.

## Why bidirectional

The existing one-directional tool (guardian mode — never propagate deletes) was the right v1: when in doubt, never lose operator work. v2 keeps that exact guarantee for **deletes** (still never propagated automatically) but allows **adds** and **content edits** to flow both ways.

The authority tier table is what makes bidirectional safe. Every AICX chunk carries an authority label; when both sides have the same `chunk_id` and disagree, the higher-authority version wins deterministically. Same-authority conflicts are surfaced as ties, the operator decides once, the decision is logged, and every subsequent sync honours it automatically.

## Authority tier table

Resolution order — top wins.

| Tier               | Source                                            | Trust level              |
| ------------------ | ------------------------------------------------- | ------------------------ |
| `repo_verified`    | Loctree snapshot fact (hard ground truth).        | Highest.                 |
| `aicx_operator`    | Sticky operator intent (steered preference).      | Strong.                  |
| `loctree_derived`  | Analyzer inference (importer counts, dead, etc.). | Strong.                  |
| `aicx_agent`       | Prior agent outcome (other agents, recent runs).  | Medium.                  |
| `aicx_failure`     | Recorded failed attempt (anti-recommendation).    | Medium — read carefully. |
| `semantic_guess`   | Heuristic. Verify before acting.                  | Low.                     |
| `memex_derived`    | Plan 09 cross-session retrieval.                  | Low.                     |
| `stale_or_unknown` | Re-check; treat as untrusted.                     | Lowest.                  |

Equal tiers on both sides → tie → operator decides → logged.

**Worked examples.**

| Local              | Remote           | Result                            |
| ------------------ | ---------------- | --------------------------------- |
| `aicx_agent`       | `repo_verified`  | Remote wins (snapshot fact).      |
| `aicx_operator`    | `aicx_agent`     | Local wins (operator preference). |
| `aicx_agent`       | `aicx_agent`     | **Tie** → operator decides.       |
| `semantic_guess`   | `memex_derived`  | Local wins (rank 300 > 250).      |
| `stale_or_unknown` | `semantic_guess` | Remote wins (rank 300 > 100).     |

## How to invoke

### Dry-run first — always

```bash
scripts/aicx-sync.sh dry-run --remote host-c --namespace vetcoders/vibecrafted
```

Prints a JSON `SyncResult` describing adds, conflicts, ties, and corrupted chunks. **No filesystem mutation.** Operator reviews the preview before pushing further.

### Apply after review

```bash
scripts/aicx-sync.sh apply --remote host-c --namespace vetcoders/vibecrafted
```

Executes the same plan. Adds copy in both directions; conflicts apply the authority-tier winner; ties are surfaced and the run exits non-zero so the operator records a decision before re-running.

### Inspect the decision log

```bash
scripts/aicx-sync.sh log-show
```

Prints all logged decisions from `~/.frontier-vault/conflict-log.jsonl`. Each record carries timestamp, chunk_id, both sides' authorities, the decision (`local` or `remote`), and a free-form reason.

### Config-file defaults

```bash
mkdir -p ~/.config/vetcoders
cp config/aicx-sync.toml.example ~/.config/vetcoders/aicx-sync.toml
$EDITOR ~/.config/vetcoders/aicx-sync.toml
```

Fields:

| Field             | Default         | Notes                                                                         |
| ----------------- | --------------- | ----------------------------------------------------------------------------- |
| `local_store`     | `~/.aicx/store` | Override only if AICX corpus lives outside XDG default.                       |
| `remote_host`     | (none)          | A tailnet hostname from your `mesh.conf` (e.g. `host-a`, `host-b`).           |
| `namespace`       | (all)           | Scope to `<org>/<project>` (e.g. `vetcoders/vibecrafted`).                    |
| `dry_run_default` | `true`          | Informational; the wrapper always honours dry-run unless `apply` is explicit. |
| `prompt_on_tie`   | `true`          | When `false`, ties are silently logged as unresolved.                         |

## Conflict log schema

JSONL — one record per line at `~/.frontier-vault/conflict-log.jsonl`.

```json
{
  "timestamp": "2026-05-12T14:23:55+00:00",
  "chunk_id": "aicx-2026_0510-codex-1432-plan08",
  "local_authority": "aicx_agent",
  "remote_authority": "aicx_agent",
  "decision": "local",
  "decided_by": "operator",
  "reason": "local run was the post-incident retry; remote was the failed first attempt"
}
```

Records are append-only. Re-deciding the same chunk_id appends a new line; the engine reads the most recent timestamp for each chunk_id (last-write-wins on the **decision** record, never on the **chunk content**). The full history is preserved for forensics.

`decided_by` is one of `operator` (interactive choice) or `auto` (reserved for future automation — Plan 08 always logs `operator` for human decisions). `reason` is optional but strongly recommended for the audit trail.

## Performance

Per doctrine 2026-05-05 SF→PL benchmark (15 Mbps uplink):

| Corpus size | Initial sync | Subsequent delta |
| ----------- | ------------ | ---------------- |
| 5 GB        | 60–90 min    | < 10 min         |
| 500 MB      | 6–9 min      | < 1 min          |
| 50 MB       | 30–50 s      | a few seconds    |

These are transport-layer numbers (rsync over ssh). The in-process engine adds a small overhead — on a 5 GB corpus the discovery walk completes in single-digit seconds; conflict resolution scales with the number of overlapping chunks (typically a few hundred per sync after the initial seed).

## Relationship to the existing sync-tool

`~/.scripts/sync-tool.py` (1518 LOC, operator-owned, **not** in this repo) handles the rsync + state-journal + JSONL-merge transport layer in guardian mode (never propagate deletes). v2 keeps that tool as the transport substrate and stacks on top of it:

```
┌───────────────────────────────────────────────────────────┐
│  ~/.scripts/sync-tool.py                                  │
│    rsync remote → ~/.frontier-vault/<host>/staging/       │
│    state-journal + JSONL merge (guardian mode for deletes)│
└──────────────────────────┬────────────────────────────────┘
                           │ (staged remote corpus on local fs)
                           ▼
┌───────────────────────────────────────────────────────────┐
│  vibecrafted_core.aicx_sync (Plan 08 — this module)       │
│    discover_chunks(local, staging) → SyncPlan             │
│    resolve_conflict(local, remote) → winner | ConflictTie │
│    apply_plan(plan, dry_run=True/False) → SyncResult      │
└──────────────────────────┬────────────────────────────────┘
                           │ (winning chunks on local fs)
                           ▼
┌───────────────────────────────────────────────────────────┐
│  rsync local → remote (reverse direction)                 │
└───────────────────────────────────────────────────────────┘
```

Nothing in this repo modifies `~/.scripts/sync-tool.py`. The operator's transport layer is intentionally untouched.

## Wiring into the nightly launchd job

A nightly one-directional sync job preceded this engine (doctrine 2026-05-05). Plan 08 ships the engine but **does not change the launchd plist** — that is an operator decision pending verification on real mesh hardware. To opt in once the dry-run preview looks clean on real cross-mesh data:

```xml
<!-- ~/Library/LaunchAgents/io.vetcoders.aicx-sync.plist (operator-owned) -->
<key>ProgramArguments</key>
<array>
  <string>/bin/bash</string>
  <string>/path/to/vibecrafted/scripts/aicx-sync.sh</string>
  <string>apply</string>
  <string>--remote</string>
  <string>host-c</string>
</array>
```

(Or wire it as a post-hook on the existing sync-tool.py launchd job — operator's call.)

## Test surface

| Suite                                      | What it covers                                                                                                             |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| `vibecrafted-core/tests/test_aicx_sync.py` | Unit tests for `Authority`, `AicxChunk`, `discover_chunks`, `resolve_conflict`, `apply_plan` dry/apply, `record_decision`. |
| `tests/aicx_sync_smoke.sh`                 | End-to-end smoke against two-machine fixture; corrupted-chunk handling; authority-tie scenario.                            |
| `make test-aicx-sync`                      | Runs both layers.                                                                                                          |

## Related surfaces

- **Plan 09 (memex retrieval integration)** — adds `memex_derived` chunks to the same corpus.
- **`~/.scripts/sync-tool.py`** — transport layer (operator-owned, not in repo).
- **doctrine 2026-05-05** — original SF→PL mesh sync design + benchmark.
- **`skills/vc-init/references/loct-context-engine.md`** — default authority tier registry.

---

_Plan 08 (META_22) — Wave 3 / agent orchestration cut._
_Vibecrafted with AI Agents by Vetcoders (c)2024-2026 LibraxisAI._
