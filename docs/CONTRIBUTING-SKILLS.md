# Contributing a New Vibecrafted Skill

This guide walks an operator (human or agent) through authoring a new `vc-*`
skill from scratch and getting it merged into vibecrafted.

If you have not read
[`vibecrafted-core/vibecrafted_core/skills/LIVING_TREE_RULE.md`](../vibecrafted-core/vibecrafted_core/skills/LIVING_TREE_RULE.md)
yet, do that first. Every contribution lands on a Living Tree branch under
concurrent agent activity, and the skill you author will run inside that same
discipline.

**Also read**
[`vibecrafted-core/vibecrafted_core/skills/DELEGATION_MATRIX.md`](../vibecrafted-core/vibecrafted_core/skills/DELEGATION_MATRIX.md)
before writing invocation rails. The matrix is the operator canon for
**per-launcher** literals (`vibecrafted review <agent>`, not a generic
`vibecrafted <workflow> <agent>` placeholder). Progressive disclosure:
catalogue + rules live in the matrix; each skill carries only a short
fleet-imperative rail with **its** literals.

---

## What is a "skill"?

A vibecrafted skill is a self-contained operator move. One trigger phrase or
operator situation maps to one named workflow that produces one decisive
outcome. The shape on disk:

```
vibecrafted-core/vibecrafted_core/skills/<skill-name>/
├── SKILL.md         # default doc, YAML frontmatter at top
├── README.md        # optional operator-facing overview
├── examples/        # at least one realistic trigger + expected behavior
│   └── *.md
├── scripts/         # optional shipped scripts (chmod +x, set -euo pipefail)
└── references/      # optional deeper docs the agent loads on demand
```

`SKILL.md` is the contract. Everything else is supporting material.

Look at three shipped skills to calibrate scope:

- [`vibecrafted-core/vibecrafted_core/skills/vc-init/SKILL.md`](../vibecrafted-core/vibecrafted_core/skills/vc-init/SKILL.md) — full-shape default
  reference, multi-sense pipeline, foundation deps, deep authority labels.
- [`vibecrafted-core/vibecrafted_core/skills/vc-marbles/SKILL.md`](../vibecrafted-core/vibecrafted_core/skills/vc-marbles/SKILL.md) — execution-shape
  reference: tight loop, single deliverable per round, falsifier-driven.
- [`vibecrafted-core/vibecrafted_core/skills/vc-polarize/SKILL.md`](../vibecrafted-core/vibecrafted_core/skills/vc-polarize/SKILL.md) — minimal-shape
  reference, narrow purpose, leans on a sibling foundation tool.

If your draft starts looking longer than `vc-init`, you are probably packing
two skills into one. Split.

---

## Scaffolding a new skill

Use the scaffolder. Do not copy an existing skill dir by hand — placeholder
substitution, exec bits, and date-stamping all happen automatically.

```bash
# From the repo root:
make skill-new NAME=vc-my-new-skill

# Or call the script directly:
tools/vc-skill-new.sh vc-my-new-skill
```

The scaffolder enforces:

- name starts with `vc-`
- lowercase letters, digits, single hyphens only
- no collision with an existing package-owned skills entry
- the default template at `vibecrafted-core/vibecrafted_core/skills/_template/` is the source of truth

On success it copies the package-owned template to
`vibecrafted-core/vibecrafted_core/skills/vc-my-new-skill/` and
substitutes:

| Placeholder                | Becomes                            |
| -------------------------- | ---------------------------------- |
| `{{SKILL_NAME}}`           | `vc-my-new-skill`                  |
| `{{SKILL_NAME_NO_PREFIX}}` | `my-new-skill`                     |
| `{{LAUNCHER}}`             | same as no-prefix (`my-new-skill`) |
| `{{CREATED_DATE}}`         | today's date in `YYYY-MM-DD` (UTC) |

It also prints operator next-steps and the discoverability commands.

The template inserts a **Goal** proposal immediately after **Purpose**. That
paragraph is agent-authored draft text: it names `{{SKILL_NAME}}`, a
`TODO concrete result`, and a `TODO verifiable endpoint`. Founder confirms or
rewrites the paragraph in human language before the skill is canonical. Leave
**Acceptance Criteria** as a separate falsifier list — the Goal is one breath
of "what done looks like", not a second checklist.

---

## Goal (Founder-owned)

Every skill has one sharp Purpose and, directly under it, a single-paragraph
**Goal**. The Goal is the entry point of the skill: one result, one
independently checkable endpoint, no bullets.

- **Agent** proposes the paragraph while scaffolding or authoring.
- **Founder** owns the final wording. Until Founder confirms or rewrites it,
  keep the proposal marker. Do not silently promote agent prose to canon.
- Refine by tightening the result and the endpoint, not by adding a second
  paragraph or merging the Goal into Acceptance Criteria.

---

## Delegation Matrix gate (invocation craft)

Classify the skill **before** filling the fleet-imperative block:

| Class             | When                                                    | Worker form                  | Interactive              |
| ----------------- | ------------------------------------------------------- | ---------------------------- | ------------------------ |
| **Core launcher** | Will be `vibecrafted <name> <agent>` in `cli.LAUNCHERS` | `vibecrafted <name> <agent>` | `/vc-<name>`             |
| **Meta**          | `init`, `ship`, `dispatch`, operator posture            | documented special form      | load skill / slash       |
| **Foundation**    | Perception/doctrine only (loctree, aicx, …)             | **none** — no fake worker    | load inside other skills |

Rules (same semantic delta as the workflow revolution):

1. Name **this** launcher in every path. Never paste `workflow` literals onto a non-workflow skill.
2. Interactive `/vc-<name>` executes **in session**; freer native subagents when depth is required; do **not** externalize merely because a CLI worker exists.
3. External fleet stays first-party. Freer native ≠ abandon `vc-dispatch` / worker launchers.
4. `vc-dispatch` stays dyspozytura; `vc-ship` stays lifecycle umbrella; do not rewrite identity.
5. Keep the SKILL.md rail short; link the matrix for catalogue and shared mandate.
6. Progressive disclosure (skill-development): lean `SKILL.md` body; put long procedures in `references/`; do not duplicate the full matrix into the skill.

See worked examples in the matrix: workflow vs review vs ship.

---

## Frontmatter contract

Every `SKILL.md` opens with YAML frontmatter between two `---` delimiters.
Required keys (gated by `make test-skills`):

| Key           | Required             | Notes                                                                           |
| ------------- | -------------------- | ------------------------------------------------------------------------------- |
| `name`        | yes                  | Must match the directory name exactly.                                          |
| `description` | yes                  | One paragraph. Folded scalar (`>`) is fine. Include trigger phrases at the end. |
| `version`     | strongly recommended | Semver. Start at `0.1.0`. Bump on every PR.                                     |

Optional but encouraged:

| Key             | When to use                                                                        |
| --------------- | ---------------------------------------------------------------------------------- |
| `requires:`     | Foundation tools or sibling skills this depends on.                                |
| `agent_target:` | If the skill is biased toward one agent (claude / codex / agy).                    |
| `triggers:`     | Explicit operator phrases as a YAML list, when the description prose is too dense. |

The smoke gate at `tests/skill_loader_smoke.sh` only checks structural
validity (delimiters, `name`, `description`). Stylistic discipline is on you.

---

## Trigger phrase discipline

The `description` field is read by the launcher when the operator types a
freeform request. Include trigger phrases in **both English and Polish** when
reasonable. Bias toward phrases an operator would actually type at 23:47 when
they are tired and the deploy is broken, not bookish formal commands.

Good: `"polarize", "wyostrz", "one sharp truth", "code smear"`

Bad: `"invoke polarization workflow", "execute conceptual disambiguation"`

If your trigger set overlaps with an existing skill, choose: either absorb your
draft into the existing skill, or sharpen the boundary so the operator
unambiguously knows which one to reach for.

---

## Authoring checklist

Before opening a PR:

- [ ] Replace every `TODO` marker in `SKILL.md`, `README.md`, and every file in
      `examples/`.
- [ ] **Goal** sits directly after Purpose as one paragraph naming a concrete
      result and a verifiable endpoint; Founder has confirmed or rewritten it
      (agent text stays a proposal until then). Acceptance Criteria remain a
      separate falsifier list.
- [ ] At least one realistic `examples/*.md` pair (trigger phrase +
      expected agent behavior).
- [ ] **Matrix class chosen** (core launcher / meta / foundation) and
      fleet-imperative rail uses **this** skill's literals only.
- [ ] No residual `vibecrafted <workflow> <agent>` or `/vc-<workflow>` as a
      fake universal placeholder (use `<launcher>` only in meta-docs, or the
      concrete name in the skill body).
- [ ] Description is third-person with concrete trigger phrases (skill-development).
- [ ] Body stays lean; heavy procedure lives under `references/` when needed.
- [ ] Closing path points at [`VERIFICATION_RULE.md`](../vibecrafted-core/vibecrafted_core/skills/VERIFICATION_RULE.md)
      when the skill can claim "done".
- [ ] `make test-skills` passes green from a clean checkout.
- [ ] `make doctor` lists your skill cleanly.
- [ ] If you added executable scripts under `scripts/`, they are `chmod +x`,
      start with `#!/usr/bin/env bash` + `set -euo pipefail`, and pass
      `shellcheck` cleanly.
- [ ] Cross-link to adjacent vc-\* skills in the **When To Use** section so the
      operator knows the boundary.
- [ ] Anti-Patterns section enumerates at least two ways the skill is likely
      to be misused.

---

## Verifying discoverability

```bash
make test-skills              # frontmatter + helper sourcing + doctor gate
make doctor | grep vc-my-new-skill   # operator-facing discovery surface
```

Installer CI may pass `--doctor-preverified` to the smoke only after its
immediately preceding installed-runtime doctor gate has passed. This avoids a
second source-tree doctor changing ownership while preserving the same proof.

If `make doctor` does not list your skill, the install path did not register it.
Re-run the installer in dev mode (`make setup-dev`) and re-check.

For deeper verification, the launcher (`vibecrafted start`) should accept
`vc-<your-name>` as a valid argument once the skill is on disk in the
package-owned skills directory
and the helper shim is reinstalled.

---

## Submitting the PR

vibecrafted commits ship with the `[<agent>/<workflow>]` prefix per the
[Vetcoders Global Agent Charter](../AGENTS.md):

```
[claude/skill-authoring] feat(skills): add vc-my-new-skill

- vibecrafted-core/vibecrafted_core/skills/vc-my-new-skill/SKILL.md: trigger + acceptance + anti-patterns
- vibecrafted-core/vibecrafted_core/skills/vc-my-new-skill/README.md: operator overview
- vibecrafted-core/vibecrafted_core/skills/vc-my-new-skill/examples/example-prompt.md: realistic trigger pair

Authored-By: claude <agents@vetcoders.io>
session_id: 019e93be-379d-7303-9ad4-ffae468db99f
time: 2026-06-05T12:52:47-06:00
runtime: iterm2
```

Use `make commit-safe` (race-protected) under any active agent session:

```bash
cat >/tmp/vc-skill-commit.txt <<'MSG'
[claude/skill-authoring] feat(skills): add vc-my-new-skill

Adds a focused operator workflow with examples and discoverability notes.

Authored-By: claude <agents@vetcoders.io>
session_id: 019e93be-379d-7303-9ad4-ffae468db99f
time: 2026-06-05T12:52:47-06:00
runtime: iterm2
MSG

make commit-safe \
  MSG_FILE=/tmp/vc-skill-commit.txt \
  FILES="vibecrafted-core/vibecrafted_core/skills/vc-my-new-skill/SKILL.md \
         vibecrafted-core/vibecrafted_core/skills/vc-my-new-skill/README.md \
         vibecrafted-core/vibecrafted_core/skills/vc-my-new-skill/examples/example-prompt.md"
```

The CI gate at `.github/workflows/skill-loader.yml` will run `make test-skills`
across ubuntu + macos. Open the PR against `develop`.

---

## Anti-Patterns for skill authoring

- **Cargo-cult cloning** — copying `vc-init` and find-replacing the name.
  `vc-init` is the most complex skill in the repo. Use the scaffolder.
- **Workflow paste** — copying the `vc-workflow` fleet rail or ERi pipeline into
  a skill that is not workflow. Use the matrix per-launcher rule instead.
- **Fake universal CLI** — documenting `vibecrafted <workflow> <agent>` as if
  every skill were named "workflow". Write the real launcher name.
- **Mass matrix dump** — pasting the full DELEGATION_MATRIX into every skill
  body. Progressive disclosure: short rail + link.
- **Native-only flip** — removing external worker paths because interactive
  native is freer. Freer native is optional depth, not fleet abandonment.
- **Multi-purpose skills** — if your `When To Use` section has more than three
  bullets describing fundamentally different situations, split the skill.
- **Vague triggers** — `"do the thing"` is not a trigger phrase. Be specific.
  The launcher's matcher is keyword-driven; ambiguous triggers route to the
  wrong skill.
- **No falsifier** — if the skill claims an outcome but nothing in its
  acceptance criteria can be checked from outside, the skill is unfalsifiable.
  Rewrite the acceptance section.
- **Skipping examples** — agents pick up new skills cold by reading
  `examples/`. Empty examples directory = your skill will be misused.
- **Stealing branding** — the default footer is
  `𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI` (or
  omitted entirely). No personal Co-Authored-By signatures in skill files.

---

## Where the wiring lives

| Surface              | Path                                                                                                                              |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Scaffolder           | [`tools/vc-skill-new.sh`](../tools/vc-skill-new.sh)                                                                               |
| Template source      | [`vibecrafted-core/vibecrafted_core/skills/_template/`](../vibecrafted-core/vibecrafted_core/skills/_template)                    |
| Frontmatter gate     | [`tests/skill_loader_smoke.sh`](../tests/skill_loader_smoke.sh)                                                                   |
| Living Tree Rule     | [`vibecrafted-core/vibecrafted_core/skills/LIVING_TREE_RULE.md`](../vibecrafted-core/vibecrafted_core/skills/LIVING_TREE_RULE.md) |
| Commit safety helper | [`scripts/lib/living-tree-commit.sh`](../scripts/lib/living-tree-commit.sh)                                                       |
| CI gate              | [`.github/workflows/skill-loader.yml`](../.github/workflows/skill-loader.yml)                                                     |

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
