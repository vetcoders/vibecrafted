# AICX retrieval grammar for intent hunting

Read this before Phase 1. It is the difference between a sweep that covers a
repo's history and one that covers a third of it.

## Contents

- [Project identity — the highest-leverage filter](#project-identity)
- [The two entry commands](#the-two-entry-commands)
- [Filters that matter, and what each one is for](#filters)
- [Voice separation and the lane pipeline](#voice-separation)
- [Reading sources](#reading-sources)
- [Continuity and the Loctree bridge](#continuity-and-the-loctree-bridge)
- [Failure modes](#failure-modes)

## Project identity

`-p` accepts four forms, case-insensitive and repeatable. Multiple `-p` flags or
a comma list form a union.

| Form            | Meaning                               | Use for                        |
| --------------- | ------------------------------------- | ------------------------------ |
| `-p owner/repo` | strict slug match                     | one known identity             |
| `-p owner/`     | every repo under that owner           | org sweep                      |
| `-p /repo`      | **same repo name across every owner** | **default for intent hunting** |
| `-p name`       | unique exact owner or repo name       | only when unambiguous          |

Substring matching is intentionally unsupported: `-p vista` does not match
`vista-portal`. Ambiguity fails closed with a candidate list rather than
returning silence — treat that error as the answer to "what identities exist".

`--project-fuzzy` opts into project-family matching. Reach for it only after the
exact forms come back empty, and say in the report that you widened.

**Why the leading slash matters.** A repo that moved owner, was renamed, or was
worked on from a second machine has several catalog identities. Observed live:

```
$ aicx intents -p /screenscribe --hours 8764 --score 50 --sort newest
project: 01_deployed_libraxis_vm/screenscribe, screenscribe, vetcoders/screenscribe
```

Three identities, one query. `-p vetcoders/screenscribe` would have returned one
of them and looked perfectly healthy while dropping the other two. Always read
the `project:` header line and record it — it is your coverage denominator.

## The two entry commands

### `aicx intents` — the roadmap surface

Structured intents from the durable catalog plus allowlisted session sources.
Default window 720h; **unlimited results by default** (full roadmap), unlike
search which defaults to 10.

```bash
aicx intents -p /<repo> --hours 8764 --score 50 --sort newest --emit json
```

`--emit json` includes `oracle_status`. Markdown is easier to skim, JSON is what
you feed the ledger.

### `aicx search` — the topic probe

Lexical-first over the CURRENT index, with a recency prior. `--hours 0` means
all time (note the difference from `intents`, where 0 is not the all-time
spelling — use a large window such as 8764).

```bash
aicx search '<theme>' -p /<repo> --hours 8764 --score 50 --sort newest
```

Useful modifiers:

- `--deep` — dense re-rank (hybrid RRF). Slower, loads the embedder. Use when
  lexical misses a paraphrased theme.
- `--evidence` — returns an evidence packet with source sections and
  diagnostics instead of plain hits.
- `--session <id>` — searches _inside_ one session instead of ranking sessions.
  With `--literal` it does exact identifier-boundary matching, and `--context N`
  widens the window. This is the right tool for "where in this conversation did
  they say it".
- `--dialog` — renders delayed human speech as speech with its seals. Helps when
  the Founder's words arrived through a queue and look like tool output.
- `--result head=N|full` — shells out the body of `tool_call` hits, which are
  stubbed by default.
- `--kind conversations|plans|reports|other` — the indexed document kind.

## Filters

These apply to both commands unless noted.

| Filter                                          | What it buys you in an intent hunt                                                                                                                                                                                                                                  |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--frame-kind user_msg`                         | **The Founder's own words.** The primary voice filter — but see the warning below: on `search` it returns zero.                                                                                                                                                     |
| `--frame-kind agent_reply` / `internal_thought` | Agent proposals and reasoning — candidates, never requirements.                                                                                                                                                                                                     |
| `--frame-kind tool_call`                        | Execution traces — audit targets, never delivery evidence.                                                                                                                                                                                                          |
| `--kind decision\|intent\|outcome\|task`        | (intents) The entry type. `decision` is where reversals live.                                                                                                                                                                                                       |
| `--sort oldest`                                 | Chronological rebuild. This is how a changed decision becomes visible.                                                                                                                                                                                              |
| `--sort newest`                                 | Current standing word first.                                                                                                                                                                                                                                        |
| `--score <0-100>`                               | Match confidence floor. 50 is the working default; drop it when a theme is phrased unusually.                                                                                                                                                                       |
| `--min-confidence 1..5`                         | (intents) Extraction confidence, distinct from match score. `--strict` is the blunt version.                                                                                                                                                                        |
| `--since` / `--until` / `-d`                    | Date bounds. `--since 2026-04-23..` and ranges `2026-03-20..2026-03-28` both work.                                                                                                                                                                                  |
| `--agent claude\|codex\|gemini\|junie`          | Which agent's sessions. Useful to check whether a promise exists only in one agent's memory. **`--agent codescribe` is a filter without a producer**: 0 rows, exit 0, looks like an empty history — the dictated corpus is not in aicx, sweep it as `dir:` instead. |
| `--unresolved --unresolved-mode intent`         | **Per-intent roadmap closure** — intents with no matching outcome. The session mode is coarser: it keeps entries from sessions with no outcome at all.                                                                                                              |
| `--collapse-session`                            | One entry per session. Good for a first census, bad for detail.                                                                                                                                                                                                     |
| `--limit`                                       | Bound the page. Remember `intents` is unlimited by default.                                                                                                                                                                                                         |
| `--live` / `--no-live`                          | Windows ≤48h scan live sources automatically; force or suppress it.                                                                                                                                                                                                 |

## Voice separation

The lane pipeline exists because agents claim completion and those claims land
in the same transcript as the Founder's requirements.

```bash
aicx sessions report <session-id> --repo "$PWD" --format markdown
```

One render, five lanes:

- **Lane 1** — human intents
- **Lanes 2–3** — agent claims and their evidence verification
- **Lane 4** — contract fractures
- **Lane 5** — clarify decisions

The lanes are also addressable individually:

```bash
aicx claims  extract --session <id> --format summary                 # Lane 2
aicx results collect --session <id> --repo "$PWD" --format summary   # Lane 3
aicx clarify --session <id> --repo "$PWD" --max 5                    # Lane 5
```

`claims extract` finds statements of the form "I implemented X". `results
collect` checks the repo for the artifacts those claims imply and folds the
outcome into verification statuses. A claim that survives `results collect` is
still only _artifact existence_ — reachability is Loctree's job, not aicx's.

## Reading sources

Retrieval ranks; it does not read. Every intent you plan to act on gets its
source opened.

```bash
aicx sessions list --cwd
aicx sessions show <id>
aicx extract claude --session "$SESSION" --conversation
aicx extract codex  --file <path/to/rollout.jsonl> --conversation -o <out.md>
```

Agent subcommands: `codex`, `claude`, `gemini`, `grok`, `junie`. `--session`
resolves the catalog first (bounded identity headers, no body parse) and then
parses exactly one source; `--file` builds a direct handle with no catalog scan.
Default output lands in `~/.aicx/extracts/<agent>/<session_id>[_conversation].md`.

Shell note: `SESSION=$(aicx sessions current)` on its own line, or inline
`--session "$(aicx sessions current)"`. The form `SESSION=x aicx … --session
$SESSION` fails, because the shell expands `$SESSION` before the process that
would receive the assignment exists.

## Continuity and the Loctree bridge

```bash
aicx continuity show -p <owner>/<repo> --hours 168 --for-inject
aicx continuity write -p <owner>/<repo> --hours 168   # to a file
```

NOW / PEERS / DECISIONS / TASKS / SOURCES / INDEX HEALTH for a project window.
Live parse first, census second — never blocked on the embedder. `--for-inject`
bounds it to roughly a 6k-token prompt budget. This is how you learn what other
agents did to the repo between your turns.

```bash
aicx overlay --repo /path/to/repo --format json
```

Joins catalog typed intents to the repo's current `loct anchors` catalog and
emits `loctree.overlay.intent.v1` — the machine-readable version of the join
this skill performs by hand. Its feed is the extract-era catalog, so if it comes
back thin: `aicx catalog rebuild`, then `aicx intents -p <owner/repo>`, then
re-run overlay. `--rebuild` re-evaluates every typed claim while preserving
persisted intent ids.

## Failure modes

- **Index gap is a reported blocker, not an excuse.** `aicx health`,
  `aicx doctor`, `aicx index` and `aicx catalog rebuild` are the repair path; if
  the gap survives them, record the affected window as an unavailable source and
  keep it open.
- **Empty result under a strict filter is not absence.** Re-probe with a wider
  identity form, a lower `--score`, and `--project-fuzzy` before concluding a
  theme was never discussed.
- **`--frame-kind` is broken on `search` (verified 2026-09-18, aicx 0.13.0).**
  `aicx search 'review' -p /screenscribe --hours 8764 --limit 50` returns 297
  lines; adding `--frame-kind user_msg` returns 0. The same filter on `intents`
  works (5419 vs 1236 lines). Voice separation on the search path therefore goes
  through `aicx sessions report` Lane 1 or through reading the source. Logged in
  `~/.vibecrafted/aicx/aicx-fail.md`.
- **`--limit` is respected on `intents`** — `--limit 2000` sets
  `per_section_limit: 2000` in the report header. A report that looks capped at
  20 is a different invocation, not a hard cap.
- **`--collapse-session` hides reversals.** A session that changed its mind
  twice collapses to one entry.
- **A high-score hit on an agent's own summary is not a Founder requirement.**
  Score measures match quality, not authority. Voice is set by `--frame-kind`,
  never by rank.
