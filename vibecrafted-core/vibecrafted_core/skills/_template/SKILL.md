---
name: "{{SKILL_NAME}}"
version: 0.1.0
description: "Template for a new Vibecrafted skill; replace before shipping."
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Invocation (template)** — replace `{{LAUNCHER}}` with the real launcher from the
> [Delegation Matrix](../DELEGATION_MATRIX.md). Do **not** paste `workflow` literals
> unless this skill is `vc-workflow`.
>
> | Path           | Literal                                               |
> | -------------- | ----------------------------------------------------- |
> | 1. Worker      | `vibecrafted {{LAUNCHER}} <agent>`                    |
> | 2. Interactive | `/vc-{{LAUNCHER}}` — in-session; native when required |
> | 3. Operator    | same worker form via `vc-dispatch` / operator lines   |

<!-- /fleet-imperative -->

# {{SKILL_NAME}} — TODO one-line tagline

> Scaffolded {{CREATED_DATE}} via `tools/vc-skill-new.sh`.
> Replace every TODO marker before opening a PR.

---

## Operator Entry

### Living Tree / Worktree Rule

This workflow runs in the operator's current checkout and current branch. Do not
create, switch to, or move execution into a git worktree unless the operator
explicitly asks for one in this prompt. Re-read files before editing, adapt to
concurrent changes, and report substrate failure if the tree is too poisoned to
continue safely. The one sanctioned second mode is a Fleet Worktree dispatch (written plan, pre-committed verifiers, disjoint domains, single-thread integrator — see Living Tree Rule, Mode B); outside that formation, stay in the shared tree.

See [Living Tree Rule](../LIVING_TREE_RULE.md).

## Repository Work Doctrine

For repository work, start with Loctree as the map: use `loct context`,
`loct occurrences`, `loct body`, and `loct find --literal` before broad manual
search. Use AICX for intent and session context. Use rg/grep as fallback or
local magnifier, not as a replacement for structural mapping. If Loctree fails
or misses a surface, append feedback to `~/.vibecrafted/loctree/loctree-fail.md`.

Standard launcher:

```bash
vibecrafted {{SKILL_NAME_NO_PREFIX}} claude --prompt 'TODO concrete operator example'
vc-{{SKILL_NAME_NO_PREFIX}} codex --prompt 'TODO shell-shortcut example'
```

---

## Purpose

TODO — Replace this section. State the **one** outcome this skill produces.
Skills exist to compress a recurring operator move into a named, repeatable
surface. If this section reads like a list of capabilities, narrow it.

The bar from `CONTRIBUTING-SKILLS.md`: one sharp axis, not a Swiss-army knife.

---

## Goal

**Proposal — Founder owns the final wording.** {{SKILL_NAME}} produces TODO concrete result and is done when TODO verifiable endpoint can be checked independently of this run. The agent drafts this one-paragraph goal as the entry point after Purpose; Founder confirms or rewrites it in human language before the skill is canonical. Do not treat this paragraph as a second checklist — keep Acceptance Criteria below as the separate falsifier.

---

## When To Use

Trigger conditions (replace all bullets):

- TODO — primary operator situation where this skill is the right call
- TODO — secondary situation, if any
- TODO — explicit non-overlap with existing vc-\* skills

**When NOT to use:**

- TODO — adjacent skill that handles a similar-but-distinct situation
- TODO — situation that should escalate to `vc-implement` or `vc-marbles` instead

---

## Pipeline Position

Where does this fit in the Vetcoders workflow chain?

- Upstream: TODO (e.g. follows `vc-init`, runs after `vc-research`)
- Downstream: TODO (e.g. emits handoff for `vc-release` or `vc-dou`)

---

## Acceptance Criteria

The skill run is **done** when:

- [ ] TODO — concrete, falsifiable check #1
- [ ] TODO — concrete, falsifiable check #2
- [ ] TODO — operator-visible deliverable (file, report, commit)

If any acceptance bullet cannot be ticked with evidence, the skill has not
completed — say so explicitly in the final report.

---

## Anti-Patterns

- TODO — common failure mode #1 (e.g. running this skill before `vc-init`)
- TODO — common failure mode #2 (e.g. expanding scope beyond the one sharp axis)
- Pasting `workflow` / ERi rails when this skill is not `vc-workflow`
- Inventing a fake `vibecrafted <name> <agent>` worker for a foundation skill
- Skipping the Living Tree re-read before edit when concurrent agents are active
- Claiming "done" without ticking the acceptance criteria above

---

## Examples

See [`examples/example-prompt.md`](examples/example-prompt.md) for a minimal
trigger phrase + expected behavior pair.

---

## Verify before the handoff

Before claiming "done", walk around the truck — see
[Verification Rule](../VERIFICATION_RULE.md): real artifact, not green gates only;
never trust upstream verification as proof; check your own instrument.

Progressive disclosure: keep this SKILL.md lean; put long procedures in
`references/` and load them only when the run needs them
([Delegation Matrix](../DELEGATION_MATRIX.md), CONTRIBUTING-SKILLS).

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
