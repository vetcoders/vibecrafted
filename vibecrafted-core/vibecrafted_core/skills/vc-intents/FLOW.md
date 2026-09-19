# `vc-intents` Flow

## Flow

```mermaid
flowchart TD
    A[/vc-intents — 'co obiecaliśmy i nie dowieźliśmy?' + window/] --> B[vc-init: repo truth]
    B --> C[0 · identity: aicx intents -p /repo; newest intent vs newest commit]
    C --> D[1 · sweep: transcript dirs + aicx inside the window → coverage, shards]
    D --> E[2 · plan --stage extract → vc-dispatch fleet reads shards]
    E --> F[verify-quotes: verbatim or rejected → intents.json]
    F --> G[3–4 · voices and chronology: kind, superseded_by, contradiction]
    G --> H[5 · lanes by subject → plan --stage confront on host A and host B]
    H --> I[merge: LEDGER.json + replication-report.json]
    I --> J[6 · reclassify: complaint∧landed, docs-only evidence → overrides.jsonl]
    J --> K[7 · render --html → Founder reviews → decide --import-file]
    K --> L[8 · queue: stable ∧ open ∧ strong, ordered]
    L --> M[plan --stage implement → vc-dispatch codex cuts]
    M --> N{status}
    N -->|OPEN| K
    N -->|CLOSED| O[Goal closed: coverage reconciled, nothing open, landed runtime-verified]
```

## Routes

| Entry                                     | Args                                             | Produces                                                  | Exit                  |
| ----------------------------------------- | ------------------------------------------------ | --------------------------------------------------------- | --------------------- |
| `/vc-intents`                             | window (`--since/--until` or theme), repo by cwd | workdir with ledger, queue, plans, review page            | interactive           |
| `vibecrafted intents <agent>`             | one lane: shard or lane JSON, brief path         | `<shard>.json` or `<lane>.verdicts.json`, report          | `0` on dispatch       |
| `intents_cli.py <workdir> plan --stage …` | `extract` · `confront` · `implement`             | `vibecrafted.dispatch.v1` plan under `<artifacts>/plans/` | `0`                   |
| `intents_cli.py <workdir> status`         | —                                                | counts, open reasons                                      | `0` CLOSED · `1` OPEN |

### Escalation edges

- Two modules compete for the truth an intent needs → `vibecrafted canary <agent>` (confirm in code; canary never refactors)
- The queue is written → `vibecrafted dispatch <intents-implement.*.dispatch.toml>` (codex by default)
- A worker claims a cut landed → `vibecrafted trust <agent>` before the ledger says `landed`
- A contradiction between two Founder statements → `aicx clarify`, then the Founder
- A written plan rather than a corpus is the input → `vibecrafted audit <agent>`

### Session artifacts

- Workdir: `~/.vibecrafted/artifacts/<owner>/<repo>/intents/` — `sweep.json`, `shards/`, `intents.json`,
  `lanes/`, `LEDGER.json`, `LEDGER.md`, `overrides.jsonl`, `replication-report.json`, `queue.json`,
  `STABLE-QUEUE.md`, `intents.html`
- Plans: `~/.vibecrafted/artifacts/<owner>/<repo>/<YYYY_MMDD>/plans/intents-{extract,confront,implement}.<host>.dispatch.toml`
- Fleet verdicts: `<artifacts>/reports/verdicts-<host>/<lane>.verdicts.json`
- Tool hooks: `~/.vibecrafted/loctree/loctree-fail.md`, `~/.vibecrafted/aicx/aicx-fail.md` — append-only; a hook is a claim about a tool, confirm before appending

### Re-entry after a cut or a compaction

`status` → read `LEDGER.md` → `queue` → `plan --stage implement`. Do not re-run
the sweep or the extraction; both are on disk and expensive.
