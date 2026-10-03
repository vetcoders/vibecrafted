# Intent ledger — schema and continuity contract

Read this when you touch the ledger. `scripts/intents_cli.py` owns the schema so
that every run, every host and every agent writes the same shape; hand-editing
the JSON is legal but the script is the reference implementation.

## Location

```
~/.vibecrafted/artifacts/<owner>/<repo>/intents/
  sweep.json               coverage per source inside the window
  shards/sNN/              transcript copies for extraction lanes
  intents.json             verified catalog (verbatim quotes)
  lanes/<lane>.json        confrontation input per lane
  LEDGER.json              state — schema vc-intents.ledger.v2
  LEDGER.md                rendered, regenerated on every write
  overrides.jsonl          append-only reclassification layer
  replication-report.json  agreement between hosts
  queue.json · STABLE-QUEUE.md
  intents.html             review surface
```

Outside the repo on purpose: the ledger tracks intents that predate the current
checkout, span branches, and are confronted on more than one machine.

## Record: intent (`LEDGER.json → intents[]`)

| Field                            | Meaning                                                                                                                                                                                                                                      |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                             | Stable handle, `<shard>-NNN`. Never reused or renumbered — `after`, `superseded_by`, overrides cite it.                                                                                                                                      |
| `date`, `source`                 | Day and source file of the statement (`<YYYY-MM-DD>_<rest>` resolves to `<root>/<date>/<rest>` or flat).                                                                                                                                     |
| `quote`                          | **Verbatim** substring of the source file. `verify-quotes` enforces it by containment; a paraphrase is rejected, never repaired.                                                                                                             |
| `topic`                          | Normalized 3–8 words in the Founder's vocabulary. Normalization lives here, never in `quote`.                                                                                                                                                |
| `kind`                           | `task` · `constraint` · `preference` · `decision` · `complaint` · `question` — what the Founder was doing. Drives rule 1 below.                                                                                                              |
| `subject`                        | Area key; lanes are built from it.                                                                                                                                                                                                           |
| `strength`                       | 1–5, how strongly expressed. Queue threshold (`--min-strength`, default 4).                                                                                                                                                                  |
| `verdicts`                       | `{host: {disposition, evidence, gap, superseded_by, conflicts_with}}` — the fleet's original, never overwritten.                                                                                                                             |
| `effective_by_host`              | Each host's verdict after rule overrides.                                                                                                                                                                                                    |
| `stable`                         | True when every host has a verdict and they agree after rules. Only stable (or human-decided) intents enter the queue.                                                                                                                       |
| `disposition`                    | Effective: human override › rule override › primary host's verdict.                                                                                                                                                                          |
| `decided_by`                     | `rule`, or the human who decided, or null.                                                                                                                                                                                                   |
| `runtime_verified`               | `yes` · `no` · `unknown`. Whether someone exercised the product path, not just found the code. Every fleet verdict starts `unknown`; only a human or a runtime probe flips it. `status` keeps the goal open while any `landed` is not `yes`. |
| `after`, `risk`, `flow`, `notes` | Ordering inputs and free text, preserved across `merge`.                                                                                                                                                                                     |

## Record: source (`sweep.json → sources[]`)

`source` (`dir:<path>` or `aicx`), `state` (`read` · `empty` · `unavailable`),
`files`/`rows`, `truncated` (aicx reports corpus truncation on stderr only; the
script captures and flags it), `reason` for `unavailable`. An unreadable source
keeps the goal open — a known unknown, not an absence.

## Dispositions

Fleet verdicts: `landed` · `partial` · `absent` · `superseded` · `contradiction` · `unclear`.
Derived by rule: `landed-contested` · `landed-by-doc`. Human only: `drop`.

**Closing:** `landed` (with `runtime_verified = yes`) · `superseded` (with `superseded_by`) · `drop`

**Open — these are what `status` counts against you:**

- `partial` — shape exists, promise unmet; `gap` says what is missing
- `absent` — no trace; evidence is the question that returned zero
- `contradiction` — two Founder statements conflict, none later resolves them (`conflicts_with`)
- `unclear` — cannot be decided without the Founder; `gap` holds the question
- `landed-contested` — code exists, but the Founder's own statement says it does not work (`kind=complaint`). Runtime unverified; the complaint outranks the symbol.
- `landed-by-doc` — the only evidence is a document (`docs/*.md`, `*.md` contract), not code. A document written by an agent is an execution claim in a document's clothes.

`drop` is the Founder's "this was never my intent": model voice pasted into a
dictation file, another project, a test phrase. It closes the row without
pretending the code did anything.

## `landed` is a lower bound, not a verdict — mechanical reclassification

Field result, 2026-09-19, two independent fleets on two hosts, same inputs and
commit: **43–45% of `landed` verdicts collapsed** under two rules that need no
judgment, only the record itself:

1. `kind == complaint` ∧ `disposition == landed` → `landed-contested`.
   A complaint says "it does not work"; `landed` says "it does". The fleet
   resolved in favor of the code because code is checkable and runtime is not.
   The Founder's statement wins until a runtime probe says otherwise.
2. `evidence` contains no code file (`.rs .swift .py .ts .js .toml .sh …`) →
   `landed-by-doc`. `docs/HOTKEYS_CONTRACT.md:37` proves a contract was written,
   not that it is kept.

Apply both **before** counting anything closed. Keep the fleet's original verdict
untouched and record the reclassification separately:

```
overrides.jsonl   # one JSON object per line, append-only
{"id":"s4-017","host":"div0","from":"landed","to":"landed-contested","rule":"complaint∧landed","by":"rule","reason":"…","at":"2026-09-19"}
{"id":"s2-052","host":"*","from":"landed","to":"absent","by":"founder","reason":"double control nie działa na drugiej maszynie","at":"2026-09-19"}
```

`by` is `rule` or the human who decided; `host` is `*` when the override applies
to every fleet's verdict. The rendered ledger shows the override and the
original side by side — reclassification is a layer, never an overwrite. This is
also how the Founder edits the catalog: a line here, not a hand-edit of a verdict
file. Both `landed-*` dispositions **count as open**.

## Record: source

| Field     | Meaning                                                                       |
| --------- | ----------------------------------------------------------------------------- |
| `session` | Session id or source path from the sweep.                                     |
| `state`   | `unread` · `read` · `unavailable`.                                            |
| `reason`  | Required in spirit for `unavailable`: index gap, missing rollout, permission. |

Every session the sweep surfaced gets a row. This is what makes "reconciled
source coverage" an auditable number rather than a feeling, and it is why
`unavailable` keeps the goal open — an unreadable source is a known unknown,
not an absence.

## Ordering

`queue` runs Kahn's algorithm over the `after` edges across the actionable
open dispositions (`partial`, `absent`, `landed-contested`, `landed-by-doc`),
breaking ties by `risk`, then `flow`, then strength (desc), then date (asc),
then `id`. Dependency wins over risk: a data-loss item that cannot compile yet
is still second. Only `stable` intents — or ones a human decided — enter.

A cycle is reported by id on stderr and its members are left out of the queue.
Cycles are real information — usually two intents that were never actually
separable, and should be merged into one.

## Commands

```bash
CLI=~/.claude/skills/vc-intents/scripts/intents_cli.py
W=~/.vibecrafted/artifacts/<owner>/<repo>/intents

uv run "$CLI" "$W" sweep --since … --until … --source dir:<transcripts> --source aicx --repo <repo>
uv run "$CLI" "$W" plan --stage extract --repo <repo> --agent '*=junie:gemini-3.8-flash'
uv run "$CLI" "$W" verify-quotes --extractions "$W/extractions" --transcripts <dir> --strict
uv run "$CLI" "$W" lanes [--lane NAME=subject,subject …]
uv run "$CLI" "$W" plan --stage confront --repo <repo> --host <host> --agent 'LANE=agent:model' …
uv run "$CLI" "$W" merge --verdicts <host>=<dir> [--verdicts <host2>=<dir>] --primary <host> --project <owner>/<repo>
uv run "$CLI" "$W" reclassify
uv run "$CLI" "$W" render --html --transcripts <dir> --blob-base <github blob base>
uv run "$CLI" "$W" decide --import-file decisions.jsonl --by founder   # or --id … --to … --reason …
uv run "$CLI" "$W" queue --min-strength 4
uv run "$CLI" "$W" plan --stage implement --repo <repo> --limit N --gate '<repo gate>'
uv run "$CLI" "$W" status      # exit 0 = CLOSED, 1 = OPEN — usable as a gate
```

Every command writes files and prints paths and counts; nothing large goes to
stdout. `merge` preserves `after`, `risk`, `flow`, `notes` and
`runtime_verified` from the previous ledger, so re-merging after a re-dispatched
lane loses no human input.

## Re-entry after a cut or a compaction

1. `intents_cli.py "$W" status` — where the goal stands and why it is open.
2. `intents_cli.py "$W" queue` — the head of `STABLE-QUEUE.md` is the next cut.
3. Read `LEDGER.md` for the quotes, verdicts and overrides behind that entry.
4. `aicx continuity show -p <owner>/<repo> --hours <N> --for-inject` if other
   agents may have touched the repo since your last turn.

**Do not re-run the sweep or the extraction on re-entry.** Both are expensive and
their results are already on disk. Re-sweep only when the Founder adds new material, or when
the window has moved far enough that new sessions exist.
