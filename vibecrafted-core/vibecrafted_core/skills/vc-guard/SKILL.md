---
name: vc-guard
version: 1.0.0
description: >
  In-flight enforcer sibling of vc-trust. Inventories existing gates
  (commit-msg, pre-commit, pre-push, loctree-first, classifier hard-stops,
  commit-msg-diff advisory, trust-block dispatch refuse) and ships one
  fail-closed proof path: refuse workflow continuation when the trust journal
  records block for HEAD. Guard never invents settlement letters or re-judges
  claims. Trigger: "guard", "vc-guard", "gate inventory", "refuse on trust block".
loctree_value: "blast radius of gate surfaces and dispatch choke points"
aicx_value: "why gates exist and which race modes they cover"
dogfooding: "required"
---

# vc-guard — the gate enforcer (not the judge)

`vc-guard` is the **strażnik** sibling of `vc-trust`. Trust observes and
judges after the fact on the Living Tree. Guard **enforces at the gate**.

| Role          | Skill             | When      | Mutates code? | Blocks dispatch?           | Writes settlement?             |
| ------------- | ----------------- | --------- | ------------- | -------------------------- | ------------------------------ |
| Judge         | `vc-trust`        | post-hoc  | never         | never                      | yes (only via explicit `note`) |
| Enforcer      | `vc-guard`        | in-flight | never         | **yes** (on trust `block`) | **never**                      |
| Message shape | `commit-msg` hook | at commit | never         | commit only                | never                          |

Do **not** mix these roles. Guard does not re-run falsification. Trust does not
refuse launch.

## Canonical Structural Gate

Before a `vc-guard` skill worker inventories gates or reports an enforcement
decision, run or consume the `vc-init` procedure for the assigned repository.
Pin the branch, HEAD, dirty state, runtime owner, and trust-journal path. A bare
`guard check` is a narrow journal decision; it is not a substitute for
repository orientation.

Use `Loctree:loctree` as the default structural layer to produce or refresh the
Code-Derived Application Map of the real enforcement line: hook entrypoints,
`enforce_continuation` callers, journal readers, dispatch choke points, and
possible bypasses. Use `slice`, `find`, and `follow` to prove those paths, and
`impact` before claiming gate coverage. Guard remains non-mutating: this map
supports truthful inventory and refusal evidence, never an implementation
detour.

## Invocation

```bash
vibecrafted guard <agent> --prompt 'Audit gate inventory and remedium paths'
python -m vibecrafted_core.guard inventory
python -m vibecrafted_core.guard check            # HEAD
python -m vibecrafted_core.guard check --sha <sha>
```

`launch_workflow` calls `enforce_continuation` unless `VIBECRAFTED_GUARD=0`.
Authorized repair/admission uses the public flags
`--remediate-trust-block --remediation-task repair|admission --remediation-reason TEXT`.
That override is recorded on the launch receipt and never rewrites the trust journal.
`python -m vibecrafted_core.guard check` stays BLOCK.

## Doctrine (hard)

1. **Fail-closed** — when a trust `block` is recorded for the target commit,
   continuation is refused. Missing journal ⇒ no block yet ⇒ allow (trust is
   the judge; absence of judgment is not a block).
2. **Remedium required** — every refuse prints a human-readable fix path
   (which journal, which claims, how to re-inspect and re-note).
3. **Non-interactive safe** — no TTY prompts; exit codes only.
4. **No settlement invention** — f/x/n letters are written solely by
   `vc-trust note` through the existing settlement API
   (`pass→f`, `pass-with-gaps→n`, `block→x`). Guard only reads journal
   verdicts.
5. **No AUTONOMY.md fork** — operator buttons (push/merge/deploy) stay in
   the autonomy charter; guard does not redefine them.
6. **Agent fairness** — commit-msg enforces Authored-By **shape**; trust
   falsifies fairness **truth**; guard may refuse when trust has blocked
   the line for fairness or completeness failures.

## Gate inventory (named, not reimplemented)

| Gate                       | Phase    | Mode                                          |
| -------------------------- | -------- | --------------------------------------------- |
| `commit-msg`               | commit   | hard — format + trailers + ban vendor footers |
| `prepare-commit-msg`       | commit   | helper — fill trailers                        |
| `pre-commit`               | commit   | hard — ruff/prettier/semgrep family           |
| `pre-push`                 | push     | hard — push-time gates                        |
| `loctree-first`            | agent    | policy — map before rummage                   |
| `classifier-hard-stops`    | dispatch | policy — AUTONOMY buttons                     |
| `commit-msg-diff-advisory` | commit   | advisory seed — claim vs staged pack          |
| `trust-block-dispatch`     | dispatch | hard — this skill's proof path                |

Coverage gaps (honest): fleet-wide hook seeding, elevating diff-gate to hard,
PATH/install drift, per-branch line policy beyond HEAD default.

## Relation to agent fairness and f/x/n completeness

- **Living Tree** allows parallel commits; fairness claims must still hold.
- **commit-msg** makes the message machine-legal.
- **vc-trust** falsifies whether the legal message is true (fairness +
  completeness + runtime claims) and writes settlement.
- **vc-guard** stops the fleet from continuing on a line trust has blocked.

## Hard boundary

Guard may write only:

- its own report/transcript when run as a skill worker;
- stderr remedium text on refuse.

Guard never edits code, amends/reverts, pushes, merges, or rewrites trust
journals. It never upgrades a missing trust note into a pass.

## Report contract

- inventory snapshot + coverage gaps;
- enforcement decision for HEAD (allowed/refused) with journal path;
- explicit reminder that settlement letters come only from trust notes;
- residual gaps.

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
