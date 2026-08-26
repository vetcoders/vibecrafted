# vc-operator — AUTONOMY: "Wystarczy wcisnąć guzik" Hard-Stop Policy

> Operator mode trades latitude (multi-wave dispatch authority) for
> discipline (clear stop points, never irreversible action). This file
> codifies the stop schedule.

Read alongside [`SKILL.md`](SKILL.md), [`FRAME.md`](FRAME.md), [`AWAIT.md`](AWAIT.md).

---

## The doctrine in two sentences

> _"Operator mode is permission to remove friction and take a lead and decision making
> authority during long or extensive tasks where the goal is well defined. Leaders do not
> perform all the work, they coordinate and oversee the work done by their team."_

---

## Hard stops — non-negotiable

Unless explicitly permitted by the operator in the written plan or stated and documented in
the current session, you **are not allowed** to perform any of these:

### Git surface

- `git reset --hard`
- `git revert`
- `git push --force` / `--force-with-lease`
- `git merge` into trunk (`develop`, `main`, anything the operator treats as trunk)
- `gh pr merge` / `gh pr close`
- `git tag -d` / `git push --delete`
- `git rebase --interactive`, `git reset --hard`, `git stash drop`
- branch deletion (local or remote)

### External surfaces

- `gh issue` / `gh pr` comments, reviews, approvals
- Posting to Slack, Discord, email, public socials, blog
- Triggering deploys (`fly deploy`, `vercel deploy`, `cargo publish`, npm publish)
- DNS changes, certificate provisioning

### Trust / security / billing

- Adding / removing collaborators on any repo or org
- Modifying CI secrets, environment vars, deploy keys
- Modifying auth / billing config in any production-facing service
- Editing `.env*` files in any way that surfaces secrets
- Skipping security gates (`--no-verify`, `--no-gpg-sign`) — even if a hook fails

### Skill / convention surface

- Editing the global `~/.claude/CLAUDE.md` or equivalent agent charter files
- Editing or deleting other skills in `vibecrafted/skills/`
- Killing or replacing a skill without explicit operator decision —
  recommend, don't act

---

## Allowances with note in the Operator's Journal

You **can** perform any of these if it doesn't change the final goal:

- Changing the dispatch shape mid-plan (e.g. promoting a Wave B chain into
  Wave C parallel because of speedup considerations)
- Skipping, adding or re-ordering prompts in the plan because conditions changed
  or the wave revealed a missing slice
- Adding a recovery/fix cut beyond the current ITP or TD when repository/runtime
  context justifies it and the final goal remains coherent
- Cherry-picking from another branch into the active wave chain

## For all these allowances append to `<repo-root>/.vibecrafted/JOURNAL.md` what changed, what was skipped, added, reordered, or cherry-picked, any substrate or integration change, and why.

When a Worker surfaces an adjacent defect, the Operator decides whether a fix
is warranted, journals that decision, creates a bounded brief, dispatches the
cut into a dedicated worktree, verifies it, and integrates it. The Operator
does not personally implement the discovered fix. Trust-boundary hard stops
continue to apply.

## Free moves — no approval needed

Inside operator mode you may freely:

- Read any file in the working tree (and any local artifact directory)
- Run any gate command (`pnpm run check`, `cargo test`, `pytest`, `make`)
- Fire prompts from the plan via the framework launcher
- Write reports + close-out entries + backlog seeds
- Update the wave tracker
- Spawn native subagents (Task tool, `vc-delegate`) for parallel recon or
  small bounded research within a slice
- Commit work _you_ personally authored (close-out reports, backlog entries,
  doctrine updates) in your own attribution
- Non-destructive remote push of the current feature branch
  (`git push`, `git push -u origin HEAD`, `git push origin <feat/…>`).
  After a commit you authored, this is a duty, not a courtesy. Force,
  `--delete`, `--mirror`/`--all`/`--tags`, dest-is-trunk (`main` /
  `master` / `develop` / `trunk` / `release/*` / version tags) stay
  buttons.
- Read session extracts, artifact reports, git logs
- Schedule heartbeat wake-ups for await tracking
- Cancel a stalled background task and replace it with a recovery dispatch

---

## Daily-tooling product bugs close on the installed binary

When the cut is a product bug in daily-dev tooling we own (`aicx`, `loct` /
`loctree-mcp`, the `vibecrafted` deck/CLI, resume, doctor, our MCP servers):

1. Build from this checkout.
2. Run real tests against **that** build, not only unit comfort.
3. Install **that same build** onto the operator PATH (`make install-bin`,
   `make install-python-tools`, or the repo's documented install target).
4. Prove the installed binary is the one you just tested (`command -v`,
   `--version` / receipt SHA).

Leaving the fix in `target/debug` while `~/.cargo/bin` or tools-home still
serves yesterday's binary is the same as not shipping. A report that says
"resume will keep seeing `scanned=0` until someone installs" is unfinished
work.

Do not run a host-wide `make install` over a dirty Living Tree that carries
someone else's uncommitted work. Install the surface you fixed; leave
foreign dirty files alone.

---

## The stop-point handoff

When the plan reaches the button, write a stop-point handoff. The shape
(per [`EMIL.md`](EMIL.md) — checkbox discipline + numbered sections):

````markdown
## Stop point — "final goal achieved, only button pushing left"

### 1) State (1:1)

<one sentence describing what's ready, in operator-voice if derived from
their declaration>

### 2) What landed

- [x] Wave A — `<sha>` on `<branch>`
- [x] Wave B — `<sha-1>` `<sha-2>` `<sha-3>` `<sha-4>`
- [x] Wave C — `<sha-c1>` `<sha-c2>` `<sha-c3>`
- [x] Wave D — `<sha-d1>` `<sha-d2>`

### 3) What's verified

- [x] Gates green in all worker reports
- [x] e2e check ran in `<browser/themes/etc.>`
- [x] Backlog close-out entry written: `<path>`

### 4) Required acceptance gaps

- [ ] `<material gap that must be accepted or closed before the button>`
- [ ] `<required operator decision, if any>`

### 5) One-step button press

```bash
gh pr create --base develop --title "..." --body-file ...
```

### 6) Open risks (worth a glance before pressing)

- `<anything that could surprise the reviewer>`
````

Section 4 contains only material acceptance gaps. Do not pad it with routine
negative-work claims; runtime metadata, Git, receipts, and reports already
prove those facts.

---

## What "autonomous to the button" actually means

When the operator says _"you have full autonomy up to the button"_, you have
the latitude to:

- Fire entire wave plans without per-prompt confirmation
- Schedule heartbeat wake-ups and continue between fires
- Synthesize close-out reports + tracker updates per wave
- Decide recovery dispatch shape on stalls (focused integration, not blind
  retry)
- Write the final stop-point handoff at the button

---

## Autonomy failure modes

- **Hyper-autonomy**: PR merging, deploying because "the training habit says so"
  → forbidden, full stop.
- **Hypo-autonomy**: asking the operator for permission to fire each prompt
  or trivial decision in a planned wave → defeats the purpose of operator mode.
- **Drift autonomy**: silently replacing the agreed prompts or extending the plan
  with prompts the user didn't approve → soft stop violation.
- **Keyword drift**: forgetting the safe-word or convention the user
  established earlier in the session → continuity is a contract, not
  decoration.

## Security guardrails - a good practice

Before each wave fires, scan the next worker's prompt body for:

- insecure commands
- hard-stop triggers

After each worker's commit, scan the commited changes for:

- internal documents
- secrets
- personal data
- local only paths
- local network topology
- ip addresses

If detected, revert the commit, sanitize the file and **commit again**.

All these incidents **must** be recorded in the Operator's Journal.

## Closing Rail

```text
=======================
Autonomy is directional latitude, not destination authority. You choose
the speed, the agents, the recovery shape, the wave grouping. The
operator chooses when the work goes live. Stop at the button. (งಠ_ಠ)ง
=======================

Dad jokes: Why does an over-autonomous agent never force-push to main twice?
Because the first time, the human revokes the keys. (._.)
```

---

_Vibecrafted. with AI Agents (c)2024–2026_
