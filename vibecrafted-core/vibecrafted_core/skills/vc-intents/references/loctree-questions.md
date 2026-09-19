# Loctree: structural questions that settle an intent

Read this before Phase 5.

## Contents

- [The errata that this file exists for](#the-errata)
- [Translation table: promise → structural question → call](#translation-table)
- [The instrument, command by command](#the-instrument)
- [Inventory prohibitions](#inventory-prohibitions)
- [When Loctree cannot answer](#when-loctree-cannot-answer)

## The errata

> Loctree is not grep and it was never the intention that it become one.
> Loctree is **structure**. `loct --help-full` is the signpost: read it without
> truncation, understand it, and try it. Literal mode and regex mode are 130% of
> grep's coverage — everything grep finds, plus structural hints — but they are
> not where the boost is. **Targeted, explicit structural questions to Loctree
> can save tens of minutes of grepping.**

The practical consequence for an intent hunt: an intent is a _promise about
behaviour_. Text search can only tell you whether a name appears. Structure
tells you whether the behaviour has a path. Those are different questions, and
only the second one can close an intent.

Two failure shapes this prevents:

- **False `landed`.** The symbol exists, the string matches, so the intent is
  marked done — while nothing calls it. `loct dead`, `loct twins` and
  `loct query who-imports` catch this in one call; grep never will.
- **False `missing`.** The promise was delivered under a different name, or
  through a different layer, so the literal probe comes back empty and the
  intent is re-queued for work that already exists. `loct find --discover`,
  `loct crowd` and `loct follow pipelines` catch this.

Literal tools (`loct find`, `loct occurrences`) stay in the kit — but as the
**confirmation** layer after a structural answer, not as the discovery layer
before it.

## Translation table

| The promise sounds like                  | The structural question                                      | The call                                                                                    |
| ---------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| "X runs automatically / on startup"      | Is there a path from a live entrypoint to X?                 | `loct follow trace --handler X` · `loct follow pipelines` · `loct query who-imports <file>` |
| "Y is configurable"                      | Does a declaration site exist _and_ does code read it?       | `loct env-truth --name Y` · `loct env-truth --fail-on orphan-code-reference`                |
| "Z replaced W"                           | Is W orphaned, or do two thrones coexist?                    | `loct impact <W>` · `loct twins` · `loct dead`                                              |
| "the two paths were unified"             | Do two modules still answer the same question?               | `loct twins` · `loct crowd <keyword>` · `vc-canary`                                         |
| "we removed the hack / silenced nothing" | Was it fixed, or suppressed?                                 | `loct suppressions --type nosemgrep\|allow\|type-ignore\|noqa\|ts-ignore`                   |
| "it's covered by tests"                  | Is there a test-bearing path?                                | `loct coverage`                                                                             |
| "the command/route exists"               | Is the surface registered?                                   | `loct commands` · `loct routes` · `loct trace <handler>`                                    |
| "events flow from A to B"                | Do emit and listen pair up?                                  | `loct follow events`                                                                        |
| "it ships in the bundle"                 | Does the manifest/tree-shake agree?                          | `loct manifests` · `loct dist`                                                              |
| "this module is the core"                | Is it actually a hub?                                        | `loct hotspots` · `loct focus <dir>`                                                        |
| "safe to change"                         | What is the blast radius?                                    | `loct impact <file>` · `loct slice <file>`                                                  |
| "the shape changed since we agreed"      | What moved structurally since then?                          | `loct diff --since <ref>` · `loct diff --since <ref> --problems-only`                       |
| "symbol S is the owner"                  | Where is it defined, who imports it, what does it look like? | `loct query where-symbol S` · `loct query who-imports <file>` · `loct body S`               |
| "that identifier is gone"                | Literal truth over the indexed universe                      | `loct occurrences <ident> --count-only`                                                     |

## The instrument

Grouped by the question each answers. `loct <command> --help` has the full flag
set; `loct --help-full` is the map.

**Orientation**

- `loct context` — markdown pill plus artifacts; `--full` for the pack.
- `loct repo-view` — files, LOC, languages, health, top hubs.
- `loct tree` — directory structure with LOC counts.
- `loct focus <dir>` — one module: files, internal edges, external deps.
- `loct atlas` — sense / inventory / signals pack.

**Before editing or deleting**

- `loct slice <file>` — the file plus its dependencies and consumers.
- `loct impact <file>` — direct and transitive consumers. Run before any delete
  or major refactor, and to break ties in the queue.

**Symbols and reachability**

- `loct query where-symbol <S>` — where it is defined or exported.
- `loct query who-imports <file>` — reverse dependencies.
- `loct query component-of <file>` — which module owns it.
- `loct body <S>` — bounded source body with extent and truncation metadata.
  Use instead of reading a whole file into context.
- `loct find <ident>` — literal, identifier-boundary. `--regex` for real
  patterns. `--discover` opts into the broad symbol/parameter/fuzzy engine.
- `loct occurrences <ident>` — the literal truth layer beneath `find`; sees
  locals inside large function bodies that symbol search misses. `--count-only`
  when you just need presence, `--group-by-file` for a rollup.

**Signals**

- `loct follow [all|dead|cycles|twins|hotspots|trace|commands|events|pipelines]`
  — the unified follower; `--handler <name>` for `trace`.
- `loct dead` — unused exports.
- `loct twins` — dead parrots (0 imports) and duplicate exports. The
  two-implementations detector.
- `loct cycles` — circular import chains.
- `loct crowd <kw>` — functional clustering around a keyword. Good for finding
  a promise delivered under a different vocabulary.
- `loct tagmap <kw>` — files + crowd + dead in one view.

**Contracts and drift**

- `loct env-truth` — env declaration drift across dotenv, docker, k8s, helm,
  GHA, npm scripts. `--name <VAR>` for a deep dive; `--fail-on <kind>` as a
  gate. Sealed payloads are surfaced by format marker only and never decoded.
- `loct suppressions` — source-side silencers: `allow`, `dead-code`,
  `nosemgrep`, `ts-ignore`, `noqa`, `type-ignore`, `shellcheck`, `unsafe` and
  more. An intent "closed" by a silencer is not closed.
- `loct coverage` — structural test gaps.
- `loct diff --since <ref>` — structural difference: new/removed symbols, graph
  changes, new dead code, new cycles. `--problems-only` for regressions,
  `--changed-files` for the summary.
- `loct anchors` — deterministic anchor catalog; this is what `aicx overlay`
  attributes intents against.

**Ordering the queue**

- `loct hotspots` — import-frequency heatmap; a hub is a high-risk cut.
- `loct health` / `loct findings` — aggregate dead/twins/cycles for a gate.
- `loct prism --task <a> <b>` — scores conceptual smear across task framings.
  Useful when two intents may be the same intent in different words.

## Inventory prohibitions

These exist because they have burned runs before:

- **Do not** use `loct context --full` `structural.files` as an inventory. It is
  hub ranking, not a file list.
- **Do not** load raw multi-megabyte `snapshot.json` into model context. Query
  it: `loct '.files | length'`, `loct '.summary.health_score' --artifact agent`,
  `loct '.dead_parrots | length' --artifact findings`.
- **Do not** use `grep` / `rg` / filesystem `find` as evidence of _absence_. A
  literal miss is not proof about ignored, generated, unsupported or otherwise
  unindexed files — `loct occurrences` states its coverage per query precisely
  because absence needs a stated scope.
- `--include-ignored` surfaces `.loctignore`-excluded files (tests, scripts,
  docs) marked `ignored`, for `find` / `slice` / `impact`. It is an ephemeral
  scan and does not touch the cache. Reach for it when an intent's evidence
  would live in a test or a script.

## When Loctree cannot answer

Append the exact failure to `~/.vibecrafted/loctree/loctree-fail.md`.
**Append, never overwrite.** A repeated hook is a signal that the tool has a gap
worth closing, not a duplicate to suppress. The sibling surface for AICX is
`~/.vibecrafted/aicx/aicx-fail.md`.

Follow the shape the file already uses — it is what makes a hook actionable
rather than a complaint:

```markdown
---

## <YYYY-MM-DD> — <one-line title>

- **Repo:** <path>
- **Command:** `<exact invocation>`
- **Observed:** <what came back, with counts>
- **Fallback:** <what you used instead>
- **Improvement:** <the capability that would have answered it>
```

Then fall back to literal search — and say in the report that you did, so the
evidence carries its own confidence level.
