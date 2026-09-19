# Replication — two fleets, two hosts, one input

Read this before `plan --stage confront` and before reading any single verdict
as a fact.

## Why replicate

A single fleet returns a result whose stability cannot be judged from inside.
The cheapest falsifier is a second fleet on a second machine with **identical
pins**: same `intents.json`, same lanes, same agent and model per lane, same
baseline commit. Then per id:

- **agree** → a fact about the code (or a shared blind spot; see the rules below)
- **disagree** → agent noise; the disposition is unstable and stays out of the queue

Field result (Codescribe, 2026-09-18/19, 974 intents, six lanes):

| pair          | compared | agree | agreement  |
| ------------- | -------- | ----- | ---------- |
| div0 · dragon | 756      | 550   | **72.8 %** |

Per lane, same agent and model on both hosts: junie/gemini-3.8-flash 100 % and
88 %; grok/grok-4.6 67 %; cursor/cursor-grok-4.6-xhigh 61 %; kimi/kimi-code/k3
54 %. The dominant disagreement was `partial ↔ landed` (76 of 206): the line
between "partly" and "done" is semantic, not factual, which is why the
confrontation brief demands `path:line` for `landed` and a `gap` for `partial`.

## Contamination check

If the workdir is copied to the second host with `rsync`, **exclude the verdict
directories** (`--exclude 'verdicts*/'`). A second fleet that can read the
first's answers produces a 100 % that means nothing. When a lane does come back
at 100 %, check that the verdict files are byte-different and that the
transcripts never reference the other host's verdict paths — done once, it was
convergence, not a copy.

## Missing lanes

A lane that does not deliver leaves its ids without a verdict on that host.
`merge` reports the count on stderr; those ids cannot be `stable` and therefore
cannot enter the queue. Re-dispatch the lane; do not fill the gap by hand.

## Agent choice

Two axes, neither of them "model rank":

- **Throughput** for mass reading (extraction shards, hundreds of short files):
  `junie`/`gemini-3.8-flash` at ~375 tps did in minutes what eight native Opus
  subagents did while exhausting a weekly budget.
- **Repeatability** for confrontation: prefer the agent whose two-host agreement
  was highest on the previous run. Rotate providers across lanes so no single
  interactive session limit is touched.

Exact model identifiers come from the Founder or from a live list
(`agy models`, `junie --model <x>` prints the pool on a bad name). Never guess a
model id from memory.

## Plans

`plan --stage confront --host <name>` writes one `vibecrafted.dispatch.v1` file
per host. Paths inside prompts and verifiers are spelled through `{id}` — the
dispatch parser hard-stop-scans raw text and a lane called `L6-build-release`
contains a forbidden word. `reports_dir` must live under
`$VIBECRAFTED_HOME/artifacts/`; each cut runs in its own worktree pinned to the
baseline, so the repo path in the brief is overridden by `{repo}`.
