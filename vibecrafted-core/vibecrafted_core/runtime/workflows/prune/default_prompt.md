---
Description: >-
  Repository health / prune ACTION run. The repository investigation is real and
  the cuts are real. Loctree MCP + loct CLI are the sensory layer exercised on
  this repository. This is not a test-only run and not a report-only run.

Task target: $REPO_ROOT / current repository.

Tooling proof target: loctree-mcp and loct CLI.

Key distinction:
  - Task target = the repository to investigate AND cut.
  - Tooling proof target = the Loctree tools to exercise while investigating.
  - Do not treat loctree-mcp / loct CLI as repository scope unless the current
    repo actually contains those surfaces.

Mode: DISCOVER -> PROVE -> CUT -> COMMIT.
  Discovery and classification are means, not the deliverable. The deliverable is
  real removals of proven-dead surface, committed — or a proven "nothing to prune".
---

# ACTION DIRECTIVES — read first, obey literally

You are not here to produce a findings report. You are here to **cut proven-dead
weight out of the repository and commit it.**

This run has exactly TWO acceptable terminal states:

1. **CUTS LANDED** — at least one commit that removes proven-dead surface
   (`git rm` of files/dirs, or deletion of dead symbols/exports), each with
   evidence, gates green.
2. **NOTHING TO PRUNE** — an explicit verdict that no removal is safe this run,
   with per-candidate evidence showing _why each candidate was kept_ (live ref,
   hidden gem, insufficient proof).

A discovery report that lists deletable surfaces but does **not** cut them is a
**FAILED run**. If something is provably dead, you delete it this run. Do not
defer obvious mechanical cleanup to "a follow-up". Do not leave the tree messy.

You may be bold when evidence is strong: cut whole dead vertical slices, not just
symbolic leaves. Boldness is earned by proof, never by vibes.

# The cut loop (repeat until the tree is clean or only unsafe candidates remain)

```
discover  -> find candidates with Loctree
prove     -> zero live references + runtime-cone + hidden-gem check
cut       -> git rm / delete dead symbols
verify    -> build/test/lint/semgrep green on the cut
commit    -> scoped, canonical message (1-2 commits per run, not dozens)
```

## Loctree first — the real command deck (loct 0.13.0-dev)

Loctree is the sensory layer. Grep is a magnifier of last resort, never the plan.

```bash
loct context --full --markdown      # repo map + risk + next moves (read it whole)
loct doctor                         # snapshot identity/health; record it
loct health                         # aggregate dead/twins/cycles
loct dead                           # unused exports / dead code
loct twins                          # dead parrots (0 imports) + duplicate exports
loct cycles                         # circular import chains
loct follow all                     # dead/cycles/twins/hotspots/trace/commands/events
loct suppressions                   # silencer inventory (allow/nosemgrep/ts-ignore/noqa/unsafe)
loct env-truth                      # env declaration drift
loct hotspots                       # import-frequency hubs (what must NOT break)
loct focus <dir>                    # module ownership + internal/external edges
loct slice <file>                   # deps + consumers (before edit)
loct impact <file>                  # blast radius (before delete)
loct query who-imports <file>       # reverse deps
loct find --literal <text>          # exact identifier-boundary scan over indexed source
loct occurrences <identifier>       # literal exact-identifier truth scan
loct body <symbol>                  # bounded source body/range of a symbol
loct diff --since <rev>             # drift since a base
```

Loctree's primary power is the structural map and its embedded findings — `dead`,
`twins`, `cycles`, `impact`, `slice`, `focus`, `hotspots`, `suppressions`,
`env-truth`. Lead with those; they are what loctree is for. The literal trio
(`loct find --literal`, `loct occurrences`, `loct body`) came last, out of
necessity: so a raw identifier check stays _inside_ the map instead of dropping to
grep. Each literal hit still carries map context — `occurrence_kind` (identifier
vs string vs comment vs ...), symbol identity, file role, authority — which for
prune is what separates a real call from a comment/string/doc mention (DELETE-NOW
vs KEEP). It is also the completeness backstop where the edge-graph undercounts
cross-module imports. Use grep only when `loct` itself is unavailable, and record
that in the journal.

## PROOF GATE — pass this before every `git rm`

A deletion is allowed ONLY when all hold:

1. **Zero live references** — confirmed by `loct dead`/`loct twins`/`loct impact`
   **AND cross-checked with `loct find --literal <symbol>`**.
   ⚠️ The structural edge-graph (`impact`/`slice`/`query who-imports`) can
   **undercount** consumers in some languages — notably Rust `use crate::a::b::Sym`
   fully-qualified imports resolve through the module tree, so `impact = 0` is NOT
   sufficient on its own. The **literal scan is the authoritative layer** for
   "is this name referenced anywhere". Trust `find --literal`; verify `impact`.
2. **Outside the runtime cone** — not reachable from a product entrypoint, CLI
   command, route, event handler, build/release/installer path, generated-artifact
   path, or a test that genuinely proves runtime behavior.
3. **Not a hidden gem** — dormant-but-valuable code is preserved, not pruned.
4. **Not dynamically loaded** — or the dynamic-loading exception is explained.

If any check is uncertain, the verdict is `VERIFY-FIRST` or `SCAFFOLD`, not delete.

## What to hunt (categories)

1. Dead parrots / orphans — dead code, unused tools/scripts, unreachable commands,
   stale entrypoints, abandoned generated files, docs describing removed behavior.
2. Silly exports — self/pointless reexports, exports with no runtime or test use,
   cargo-cult public surfaces, components/functions without real callers.
3. Missing handlers / callers — declared commands without handlers, unreachable
   handlers, routes/actions/events without callers, UI actions without
   target/action mapping, config keys with no consumer (and vice versa).
4. Twins — duplicated functions, near-identical modules, old/new side by side.
5. Cycles & races — dependency/control-plane cycles, async/state races, stale
   state vs runtime truth, lock/heartbeat/pid divergence.
6. Crowds — ambiguous names, too many similar commands/components, overloaded
   concepts that confuse humans or agents.
7. Shadows — code called but shadowed by a newer path, fallback paths silently
   dominating primary, old impls masked by wrappers, features unreachable in flow.
8. Linter silencers — catalog via `loct suppressions`; classify each as justified,
   stale, dangerous, test-theater, forgotten-gem, or follow-up. Do not blind-remove.
9. Monoliths — 1500+ LOC / too many responsibilities / collision hotspots →
   usually `SCAFFOLD`, not immediate deletion.
10. Forgotten local state — branches, stashes, worktrees, untracked WIP. Inspect
    READ-ONLY. NEVER delete branches/stashes/worktrees without operator approval.
11. Hidden gems — useful-but-unreachable code, dormant capabilities. Preserve;
    classify as preserve / scaffold / revive.

## Verdicts

`DELETE-NOW` · `ARCHIVE-THEN-DELETE` · `REVIVE` · `SCAFFOLD` · `VERIFY-FIRST` ·
`KEEP-RUNTIME` · `KEEP-BUILD` · `FORGOTTEN-GEM`.

`DELETE-NOW` and small `ARCHIVE-THEN-DELETE` get cut THIS run. `FORGOTTEN-GEM` is
**never auto-deleted** — preserve and surface to the operator.

## Hard rules

- No deletion on vibes. Prove every cut.
- `impact = 0` alone never justifies a delete — cross-check `find --literal`.
- Never delete a hidden gem, branch, stash, worktree, or hidden WIP.
- Never delete live build/release/test scaffolding just because product code does
  not import it.
- If the repo is dirty at start, record it and do NOT sweep unrelated WIP into a
  prune commit (commit your cuts by explicit pathspec).
- `--no-verify` only for a declared Founder-authorized local compile-embargo
  checkpoint with a skipped-gate receipt. Never worker `git push`; push with
  `--no-verify` is an exclusive Founder button.
- Commit canonical: `[<agent>/vc-prune] <type>(prune): <subject>` with an
  explanatory body. One coherent vertical deletion per commit; 1-2 commits/run.

## Journal (the byproduct, not the product)

Append ONE journal — cuts are the deliverable, the journal documents them:

`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/prune/reports/${RUN_ID}_JOURNAL.md`

(If RUN_ID/paths are unavailable, use the closest equivalent under the current
Vibecrafted artifacts tree and report the exact path used.)

The journal must record, in-line (sections, not a dozen separate files):

- repo inspected; branch / HEAD / dirty state at start
- loctree-mcp + loct CLI evidence (commands run + key output); any failures/fallbacks
- candidates found, grouped by category, each with its verdict + evidence
- **candidates CUT**: commit SHA(s), files changed, deletion count, blast-radius proof
- candidates kept (and why) — especially every `FORGOTTEN-GEM`
- branches / stashes / worktrees observed (read-only)
- `SCAFFOLD` follow-ups worth a future cut (named, small, fleet-executable)
- if no cut was safe: "NOTHING TO PRUNE" + per-candidate keep-evidence

A run that ends without either committed cuts or a proven "nothing to prune" is
not finished.
