---
name: vc-intents
version: 2.0.0
description: >
  Hunt the Founder's intents for one repository inside a chosen window, settle
  every one of them against the live code with two independent fleets, and hand
  back a stable, dependency-ordered plan of cuts for vc-dispatch. Use whenever
  the team asks "zbierz intencje", "rozlicz intencje", "czego chciał Founder",
  "co obiecaliśmy i nie dowieźliśmy", "co z planu siedzi w kodzie", "what did we
  agree to build", "what is still owed", "intent coverage", "planned vs code",
  "highest truth", "checklist from intents" — or when scattered decisions from
  transcripts and sessions must become an implementation queue that survives
  compactions and machines. Reach for this instead of a bare `aicx search`
  whenever the Founder's own words must be separated from agent proposals and
  agent completion claims, and the answer must end in a plan, not a catalogue.
loctree_value: "structural questions decide whether a promise has a runtime shape"
aicx_value: "indexed intents, session context; one source among several"
dogfooding: "required — this skill is the join between corpus, aicx, Loctree and dispatch"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-intents` (launcher `intents`)** — see the
> [Delegation Matrix](../DELEGATION_MATRIX.md).
>
> | Path                    | Literal for this skill                                                                                |
> | ----------------------- | ----------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | `vibecrafted intents <agent>` — one lane of extraction or confrontation, READ, writes only its JSON   |
> | 2. Interactive          | `/vc-intents` — the hunt itself, **in this session**: sweep, lanes, plans, merge, queue, render       |
> | 3. Agent-operator       | dispatches the worker form through `vc-dispatch` with a plan written by `scripts/intents_cli.py plan` |
>
> Cadence stays **read**: this skill never edits the repo. Delivery is a
> separate `implement` plan it writes for the fleet.

<!-- /fleet-imperative -->

# vc-intents — from what the Founder said to a plan of cuts

A long-lived project accumulates intent faster than code. The Founder says
something in March, corrects it in May, an agent proposes another shape in June
and claims it shipped in July. By September nobody can say which of those still
binds — and the only person who could is the one paying for the answer.

This skill owns the whole path: **collect → separate voices → verbatim catalog →
confront with two fleets → reclassify what "landed" hides → stable queue → a
dispatch plan the fleet can run**. `vc-canary` finds truth collisions and never
refactors; `vc-implement` cuts code but does not reconstruct what was promised;
`vc-audit` falsifies a plan against code. `vc-intents` reconstructs the promise,
proves what is left of it, and hands the rest over as cuts.

## Goal

`vc-intents` produces a ledger in which every Founder intent from the chosen
window carries one explicit disposition, an agreement score between two
independent fleets, and a human-editable override layer — and it is done when
`intents_cli.py <workdir> status` names what is still open and
`plan --stage implement` has written a dispatch plan for it. A catalogue does
not close the goal. An audit does not close the goal. The plan of cuts does.

## Canonical Orientation Gate

Before any repo-specific step — sweep against a repo, confrontation, plan —
run or consume the `vc-init` procedure for the assigned repo. `Loctree:loctree`
is the default structural perception skill for that pass and must produce or
refresh the Code-Derived Application Map (`context`, `repo-view`, `focus`,
`slice`, `impact`, `find`, `follow`). Confrontation lanes inherit this: the
brief makes loctree-mcp the first move, and a `landed` without a structural
path behind it is not a verdict. If fresh `vc-init` evidence is absent, perform
the init pass first and treat the hunt as blocked until repo truth exists. A
sweep of a transcript corpus with no repo attached states the no-repo exception
in its report.

## Scope is a window, not a history

Bound the hunt before sweeping: `--since/--until`, or a theme. Coverage is
reported against that window. The whole history is one legal window among
others — not a precondition of closure. What the window does not contain is
recorded as _not in scope_, never as _absent_.

## The tool

```bash
CLI=~/.claude/skills/vc-intents/scripts/intents_cli.py   # or the store path
W=~/.vibecrafted/artifacts/<owner>/<repo>/intents         # one workdir per hunt
uv run "$CLI" "$W" <command> …
```

One working directory holds everything that must survive a compaction or a
second host: `sweep.json`, `shards/`, `intents.json`, `lanes/`, `LEDGER.json`,
`overrides.jsonl`, `queue.json`, `STABLE-QUEUE.md`, `LEDGER.md`,
`intents.html`. Agents read and judge; the script keeps the books, performs the
falsifications that need no judgment, and writes the plans that hand the
judging to a fleet.

## Procedure

### 0 · Orientation

Run or consume `vc-init` for the repo. Resolve catalog identity with
`aicx intents -p /<repo>` (leading slash — every historical owner). Read the
`project:` header line: that is your aicx denominator. Then the negative
control: newest intent date vs newest commit date. A gap means a missing
identity, not a quiet quarter.

### 1 · Sweep — account for every source in the window

```bash
uv run "$CLI" "$W" sweep --since 2026-06-01 --until 2026-09-18 \
  --source dir:~/.codescribe/transcriptions --source aicx --repo <repo> --shard-size 120
```

Sources are first-class and unequal. Field result (Codescribe, 2026-09): aicx
held **~10 %** of the Founder's voice and **0 %** of the dictated corpus; the
transcript directory held 2 842 files across two hosts. A directory that lives
on another machine is fetched (`rsync` from that host) before the sweep, or it
is `unavailable` in coverage — a known unknown that keeps the goal open. aicx
truncation is reported on **stderr only**; the script captures it to a file and
flags `truncated`.

`sweep` writes chronological shards for extraction. Nothing has been read yet.

### 2 · Extract — the fleet reads, verbatim

```bash
uv run "$CLI" "$W" plan --stage extract --repo <repo> --agent '*=junie:gemini-3.8-flash'
vibecrafted dispatch <plan> --doctor --json && vibecrafted dispatch <plan> --json
uv run "$CLI" "$W" verify-quotes --extractions "$W/extractions" --transcripts <dir> --strict
```

The brief is `references/extraction-brief.md`. Its one hard rule: **`quote` is a
verbatim substring of the source file.** `verify-quotes` rejects anything else
by string containment — a paraphrase presented as the Founder's voice is the
worst error this work can make, because it assigns a human a decision they did
not take. Whisper misspellings stay; normalization lives in `topic`.

Agent choice is **throughput**, not rank: mass reading of short files is a
375-tps job (`junie`/`gemini-3.8-flash`), not a frontier-model job. Eight native
Opus subagents once burned a weekly budget on what a flash fleet does in
minutes. See `references/replication.md`.

### 3 · Separate voices

Only the Founder's own words bind. Agent proposals are candidates; execution
claims are audit targets; agent-authored documents (CHANGELOG, ADR, roadmap,
report) are claims in a document's clothes. A transcript corpus is not
automatically clean either: agents pasted model replies into dictation files in
~30 % of one month's files. The extraction brief lists what to skip; the
`kind` field records what the Founder was doing (`complaint`, `task`,
`constraint`, `decision`, `preference`, `question`).

### 4 · Chronology

Later Founder words supersede earlier ones **only with a named successor**
(`superseded_by`). Two statements that conflict with no later resolution are a
`contradiction` — the instrument for that is `aicx clarify`, then the Founder.
Deduplicate statements, never provenance: one intent, many citations.

### 5 · Confront — two fleets, two hosts

```bash
uv run "$CLI" "$W" lanes                     # by subject; default six Codescribe lanes
uv run "$CLI" "$W" plan --stage confront --repo <repo> --host div0 \
  --agent 'L1-overlay-ui=kimi:kimi-code/k3' --agent 'L4-quality-lexicon=junie:gemini-3.8-flash' …
```

Then the same plan on a second machine with the same pins and models, verdict
directories excluded from any rsync of the workdir (the second fleet must not
see the first's answers). The brief is `references/confrontation-brief.md`:
loctree-mcp first, `landed` needs `path:line`, `absent` needs the question that
returned zero, no edits, all ids preserved.

Verification is structural: "X starts automatically" is _an edge from an
entrypoint to X_ (`loct follow trace`), not a grep hit. The promise → question
→ command table is `references/loctree-questions.md`.

```bash
uv run "$CLI" "$W" merge --verdicts div0=<dir> --verdicts dragon=<dir> --primary div0 --project <owner>/<repo>
```

`merge` writes `LEDGER.json` and `replication-report.json`. Agreement per id
with the same agent and model was **72.8 %**; the top disagreement was
`partial ↔ landed`. Read that as: no single verdict is evidence — an agreeing
pair is. Only stable pairs enter the queue.

### 6 · Reclassify — `landed` is a lower bound

```bash
uv run "$CLI" "$W" reclassify
```

Two rules that need no judgment, applied before anything is counted closed:
`kind = complaint ∧ landed → landed-contested` (the Founder says it does not
work; the fleet found code; runtime unverified — the complaint wins until a
probe says otherwise), and `evidence without a code file → landed-by-doc` (a
document proves a contract was written, not kept). Field result: **43–45 % of
`landed` fell** on both hosts. Both derived dispositions count as open. The
rules append to `overrides.jsonl`; the fleet's verdict is never overwritten.
Schema and rule text: `references/ledger-schema.md`.

### 7 · The human layer

```bash
uv run "$CLI" "$W" render --html --transcripts <dir> --blob-base https://github.com/<o>/<r>/blob/<sha>/
open "$W/intents.html"
uv run "$CLI" "$W" decide --import-file decisions.jsonl --by founder      # or --id … --to … --reason …
```

The page shows every intent with its verbatim quote, the wider transcript
excerpt, both hosts' verdicts with evidence linked to the pinned commit, the
mechanical overrides, and a decision form: agree, reclassify, drop as a false
lead, comment. Decisions export as JSONL and come back through `decide`. This is
the Founder's editing surface — a line in `overrides.jsonl`, never a hand-edit
of a verdict file. Published as a claude.ai artifact with the `db` capability,
the same page saves decisions live; the file is the canon either way. See
`references/review-surface.md`.

### 8 · Queue and plan of cuts

```bash
uv run "$CLI" "$W" queue --min-strength 4
uv run "$CLI" "$W" plan --stage implement --repo <repo> --limit 6 --gate 'cd {repo} && make check'
```

The queue holds intents that are **open, stable across hosts or decided by a
human, and strong enough**, ordered by dependency (`after`), then risk, then
core-flow impact, then strength and age. The implement plan gives each one a
cut: the Founder's quote as the brief, the fleet's evidence and gap, a repo
gate as verifier, `mode = write`, `require_commit = true`. `codex` is the
default worker. Anything that needs a Founder's button (merge, deploy, secrets,
deleting data) is described in the cut and never pressed.

### 9 · Closure

`status` exits 0 only when no open disposition remains, no source is
unavailable or truncated, and every `landed` carries `runtime_verified = yes`.
Until then the goal is open — truthfully. On re-entry after a compaction: read
`LEDGER.md`, run `status`, then `queue`; **do not re-sweep**, the result is on
disk.

## Output contract

1. **Coverage** — window, sources with state (`read`/`empty`/`unavailable`/
   `truncated`), files and rows per source.
2. **Catalog** — verified intents, rejected quotes with reasons.
3. **Ledger** — per-host verdicts, agreement per pair, effective disposition,
   overrides (rule and human), `runtime_verified`.
4. **Queue** — stable open intents with the ordering rationale.
5. **Plan** — `intents-implement.<host>.dispatch.toml`, doctor-clean.
6. **Highest truth** — the one unresolved reality that should hurt a little.
   "43 % of what two fleets called landed is contested by the Founder's own
   words" is a highest truth; "some items are partial" is not.

## Anti-patterns

Treating a ranked aicx hit as a read source · a paraphrase in `quote` · one
fleet's verdict as evidence · `landed` from `docs/*.md` · `superseded` without a
successor · re-running the sweep on re-entry · frontier models for mass reading
· the verifier `run` naming a lane literal (`release` inside `L6-build-release`
trips the dispatch hard-stop scan; the script spells paths through `{id}`) ·
pressing a hard-stop because the queue said so · reporting a green audit as
closure while the queue is non-empty.

## Related skills

`vc-init` before everything · `vc-canary` when two modules compete for the
truth an intent needs · `vc-dispatch` runs every plan this skill writes ·
`vc-implement` / `vc-ownership` are the cuts · `vc-trust` falsifies a worker's
completion claim afterwards · `vc-audit` for a written plan rather than a
corpus.

## Verify before the handoff

Walk around the truck — [Verification Rule](../VERIFICATION_RULE.md): the
ledger exists and `status` says OPEN with reasons you can name; the plan passed
`vibecrafted dispatch --doctor`; the HTML opens with the right host names and
working code links; every number in the report comes from a file in the
workdir, not from memory.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
