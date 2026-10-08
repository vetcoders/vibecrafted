# vc-agents — plan template

```markdown
---
run_id: <generated-unique-id>
agent: <claude|codex|gemini|agy|junie|grok|cursor|kimi|copilot>
skill: vc-agents
project: <repo-name>
status: <pending|in-progress|completed|failed>
loops_completed: <number>
---

# Task: <short title>

Goal:

- <1-3 bullets>

Scope:

- In scope: <files/areas> as high-level suggestions
- Out of scope: <explicit>

Constraints:

- No `--no-verify` outside a declared Founder-authorized local
  compile-embargo checkpoint; workers never push with it
- Follow repo conventions

Acceptance:

- [ ] <objective outcome>
- [ ] <objective outcome>

Test gate:

- <command(s)>

Context:

- <very short summary>

Living tree note:

- You work on a living tree with 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚜𝚖𝚊𝚗𝚜𝚑𝚒𝚙 methodology, so concurrent changes are expected.
- Adapt proactively and continue, but this is never permission to skip quality, security, or test gates.
- Run required checks. If something is blocked, report the exact blocker and run the closest safe equivalent.
- Coordination mode: <solo on this stage / parallel with other agents on this stage>
- You do not need to inspect other agents' plans unless this plan explicitly tells you to.
- **Commit is an obligation, not a checkpoint option: ONE commit per round** (marbles — one round = one commit), well-formed per the commit-msg hook, on the current branch. Do NOT leave delivered work uncommitted. Non-destructive remote push of the current feature branch (`git push -u origin HEAD`, not force, not trunk) is a duty after that commit. Force-push, trunk push, merge, and deploy stay operator buttons. When the mission spans multiple rounds/units, multi-commit per dispatch is expected.
- You are an execution unit, not orchestration authority: do not invoke `vc-agents`, do not reopen frontier selection, and do not reinterpret the `vc-why-matrix`. **Your own native subagents are exempt**: fan out in-process (Task tool / swarm / native sub-sessions) whenever this plan contains disjoint subcuts, picking tiers by subtask economics (`vc-delegate` → Native Delegation Policy).
- If the mission reveals a wider unresolved surface, report that boundary clearly and leave orchestration changes to the operator.
```
