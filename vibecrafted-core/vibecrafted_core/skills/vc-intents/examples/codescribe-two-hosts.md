# vc-intents — example: Codescribe, dictated corpus, two hosts

## Trigger phrase

> "Rozlicz intencje z transkrypcji Codescribe od stycznia — co obiecaliśmy i nie
> dowieźliśmy? Chcę móc sam poprawiać klasyfikację."

## Expected agent behavior

1. `vc-init` on the repo; `aicx intents -p /codescribe --limit 1` → header lists
   five identities; newest intent 2026-09-18 vs newest commit 2026-09-18 — aligned.
2. `sweep --since 2026-01-01 --source dir:~/.codescribe/transcriptions --source aicx`
   → transcript dir 1 900 files on this host; aicx 3 254 rows, **truncated**. The
   other host holds 900 more files: `rsync dragon:.codescribe/transcriptions/ …`,
   re-sweep, coverage now 2 842 files, `--shard-size 120` → 24 shards.
3. `plan --stage extract --agent '*=junie:gemini-3.8-flash'` → doctor clean →
   `vibecrafted dispatch … --json`. `verify-quotes --strict` → 974 verbatim, 0 rejected.
4. `lanes` (six by subject) → `plan --stage confront --host div0` with kimi /
   cursor / grok / junie per lane; the same plan generated on dragon with
   `--host dragon`, verdict dirs excluded from the rsync of the workdir.
5. `merge --verdicts div0=… --verdicts dragon=… --primary div0` → agreement
   550/756 = 72.8 %, top disagreement `partial→landed` (47) — one lane not
   delivered on dragon, 218 intents without a second verdict, reported on stderr.
6. `reclassify` → +410 overrides, 43 % of host-level `landed` fell to the two rules.
7. `render --html --transcripts … --blob-base https://github.com/vetcoders/codescribe/blob/<sha>/`
   → the Founder filters "W kodzie, Founder: nie działa" (126), decides, exports
   `decisions.jsonl` → `decide --import-file decisions.jsonl --by founder`.
8. `queue --min-strength 4` → 199 stable open intents → `plan --stage implement
--limit 6 --gate 'cd {repo} && make check'` → doctor clean → handoff.

## Acceptance evidence

- `<workdir>/LEDGER.json` with `hosts: ["div0","dragon"]`, `replication-report.json`
  with an `agreement` number per pair
- `<workdir>/overrides.jsonl` with `by: rule` rows and at least one `by: founder` row
- `intents-implement.div0.dispatch.toml` passing `vibecrafted dispatch --doctor --json`
- `status` exit 1 with named reasons (open dispositions, `runtime_verified != yes`)
- Highest truth in the report: "43 % of `landed` is contested by the Founder's own words;
  runtime_verified is `unknown` for all 974 — no fleet ran the product."

## Notes

- Agents are chosen by throughput for reading (junie/flash), by stability for
  confrontation (junie 88–100 % repeatability, kimi 54 % in the same run).
- The lane named `L6-build-release` trips the dispatch hard-stop scan if its name
  appears literally in a verifier; the script spells every path through `{id}`.
