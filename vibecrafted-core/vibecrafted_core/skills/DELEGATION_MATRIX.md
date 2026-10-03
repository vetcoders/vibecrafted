---
title: Delegation Matrix
kind: doctrine_matrix
version: 3.2.0
description: "Canonical invocation, execution, and delegation model for Vibecrafted runtime launchers and their skills."
scope: framework
status: active
---

# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Delegation Matrix

> Invocation, execution, and delegation for the fleet — **per launcher**, not as one generic blur.

<!-- fleet-imperative: v3 -->

## Shared three paths

Every **core runtime launcher** (a `vibecrafted <launcher> <agent>` skill backed by the core runtime) is invoked through the same _shape_ of three paths. The **literals** change per launcher; the authority rules do not.

### 1. User-Launched Worker

The user runs the **named** launcher through the CLI. That creates a separate, non-interactive worker run responsible for executing **that skill’s** full pipeline.

```text
vibecrafted <launcher> <agent> [-p|--prompt … | -f|--file …]
```

Example (workflow only — do not generalize the word `workflow` to every skill):

```bash
vibecrafted workflow claude --prompt 'Examine auth surface and implement fixes'
```

### 2. Interactive Skill Invocation

The user invokes `/vc-<launcher>` or loads that skill inside an existing agent session. The current agent **must** load and execute the complete skill **in that same session**. It must **not** externalize the run to a separate `vibecrafted` worker merely because external dispatch exists. It may — and when required **must** — use its **native in-process** subagent fleet to finish thoroughly.

Example:

```text
/vc-workflow
```

### 3. Agent-Operator Delegation

While conducting broader orchestration, an agent-operator may launch the **same named launcher** as the user would — typically through `vc-dispatch` / operator lines — so a separate workflow session runs under the Vibecrafted runtime and owns that skill’s pipeline.

```bash
vibecrafted <launcher> <agent> --file <brief.md>
```

---

## What this matrix is not

- **Not** a claim that every skill is `vibecrafted workflow <agent>`.
- **Not** a mass paste of one block into every `SKILL.md` without naming the launcher.
- **Not** a flip of the fleet to native-only. External workers remain first-party product surface.
- **Not** an identity wipe: `vc-dispatch` stays dispatch; `vc-ship` stays scaffold→release delivery; each skill keeps its own mandate.

The operator revolution for `vc-workflow` was **precision of literals + freer native under that skill**. The rest of the runtime gets the **same precision**, each on its own name.

---

## Execution mandate and lifecycles

Whether interactive or non-interactive, the agent under a launcher has the **same mandate**: execute that skill’s pipeline comprehensively and use available native subagents when necessary.

The difference is only:

- **where** the skill executes
- **whose attention** it occupies

Nonetheless:

- A **headless worker** retains authority to spawn and coordinate its own native subagents — workerhood constrains run scope and lifecycle, not native delegation rights.
- An **agent that receives the skill interactively** must execute it locally in the current session, using native subagents when appropriate.
- **Freer native** on some runs means: when interactive (or when a worker needs depth), prefer completing the skill with native subagents rather than reflexively re-dispatching external. It does **not** mean abandon external launchers.

---

## Native subagents vs external workers

| Kind                 | Lifecycle                               | Context                                                           |
| -------------------- | --------------------------------------- | ----------------------------------------------------------------- |
| **Native subagents** | Same process as the orchestrating agent | Shared memory, config, conversation                               |
| **External workers** | Separate `vibecrafted` processes        | Control-plane / report / transcript / meta; independent lifecycle |

**Rule:** execution authority for a skill stays with the agent that holds the skill unless explicitly delegated through defined channels (`vc-dispatch`, operator ship lines, user CLI worker).

---

## Launcher catalogue (core runtime)

Grounded in `vibecrafted_core.cli.LAUNCHERS` + shell wrappers + lifecycle meta. Skill directory is `vc-<launcher>` unless noted.

### Ship-cycle launchers (canonical order)

| Launcher    | Skill                                   | Worker CLI                      | Interactive     | Notes                                                                  |
| ----------- | --------------------------------------- | ------------------------------- | --------------- | ---------------------------------------------------------------------- |
| `scaffold`  | [`vc-scaffold`](vc-scaffold/SKILL.md)   | `vibecrafted scaffold <agent>`  | `/vc-scaffold`  | Plan / briefs from intent                                              |
| `implement` | [`vc-implement`](vc-implement/SKILL.md) | `vibecrafted implement <agent>` | `/vc-implement` | **Ship WRITE stage** — structured e2e delivery with followup + marbles |
| `review`    | [`vc-review`](vc-review/SKILL.md)       | `vibecrafted review <agent>`    | `/vc-review`    | READ; bounded review                                                   |
| `workflow`  | [`vc-workflow`](vc-workflow/SKILL.md)   | `vibecrafted workflow <agent>`  | `/vc-workflow`  | ERi: Examine → Research → Implement                                    |
| `followup`  | [`vc-followup`](vc-followup/SKILL.md)   | `vibecrafted followup <agent>`  | `/vc-followup`  | Trajectory / gap audit                                                 |
| `marbles`   | [`vc-marbles`](vc-marbles/SKILL.md)     | `vibecrafted marbles <agent>`   | `/vc-marbles`   | WRITE convergence; `--count` / `--depth`                               |
| `audit`     | [`vc-audit`](vc-audit/SKILL.md)         | `vibecrafted audit <agent>`     | `/vc-audit`     | Plan-vs-code falsification                                             |
| `polarize`  | [`vc-polarize`](vc-polarize/SKILL.md)   | `vibecrafted polarize <agent>`  | `/vc-polarize`  | One-axis product truth                                                 |
| `dou`       | [`vc-dou`](vc-dou/SKILL.md)             | `vibecrafted dou <agent>`       | `/vc-dou`       | Definition of Undone                                                   |
| `decorate`  | [`vc-decorate`](vc-decorate/SKILL.md)   | `vibecrafted decorate <agent>`  | `/vc-decorate`  | Visual / UX finish                                                     |
| `hydrate`   | [`vc-hydrate`](vc-hydrate/SKILL.md)     | `vibecrafted hydrate <agent>`   | `/vc-hydrate`   | Packaging / GTM                                                        |
| `release`   | [`vc-release`](vc-release/SKILL.md)     | `vibecrafted release <agent>`   | `/vc-release`   | Outward ship mechanics                                                 |

### Additional skill launchers

| Launcher    | Skill                                   | Worker CLI                      | Interactive     | Notes                                                                                           |
| ----------- | --------------------------------------- | ------------------------------- | --------------- | ----------------------------------------------------------------------------------------------- |
| `justdo`    | [`vc-justdo`](vc-justdo/SKILL.md)       | `vibecrafted justdo <agent>`    | `/vc-justdo`    | **Standalone posture** — not a ship stage; not `implement`. Task type from the prompt. ADR-0001 |
| `canary`    | [`vc-canary`](vc-canary/SKILL.md)       | `vibecrafted canary <agent>`    | `/vc-canary`    | Ownership catalog: atlas sense → 1 agent/scope → one commit → findings report                   |
| `research`  | [`vc-research`](vc-research/SKILL.md)   | `vibecrafted research …`        | `/vc-research`  | Swarm-capable; default multi-agent research                                                     |
| `ownership` | [`vc-ownership`](vc-ownership/SKILL.md) | `vibecrafted ownership <agent>` | `/vc-ownership` | Full-spectrum ownership delivery                                                                |
| `partner`   | [`vc-partner`](vc-partner/SKILL.md)     | `vibecrafted partner <agent>`   | `/vc-partner`   | Shared steering with operator                                                                   |
| `prune`     | [`vc-prune`](vc-prune/SKILL.md)         | `vibecrafted prune <agent>`     | `/vc-prune`     | Runtime cone / silencer strip                                                                   |
| `intents`   | [`vc-intents`](vc-intents/SKILL.md)     | `vibecrafted intents <agent>`   | `/vc-intents`   | Intent hunt in a window → two-fleet ledger → Founder overrides → stable plan of cuts (READ)     |
| `delegate`  | [`vc-delegate`](vc-delegate/SKILL.md)   | `vibecrafted delegate <agent>`  | `/vc-delegate`  | **Native** subagent doctrine (bounded)                                                          |
| `trust`     | [`vc-trust`](vc-trust/SKILL.md)         | `vibecrafted trust <agent>`     | `/vc-trust`     | READ; post-hoc commit-claim falsification (agent fairness + completeness) + settlement f/x/n    |
| `guard`     | [`vc-guard`](vc-guard/SKILL.md)         | `vibecrafted guard <agent>`     | `/vc-guard`     | READ; gate inventory + refuse continuation on trust `block` (never invents settlement)          |
| `paste`     | (runtime helper)                        | `vibecrafted paste …`           | —               | Prompt/paste helper; not a full ERi skill                                                       |

### Meta and orientation (not the same shape as skill workers)

| Surface      | Skill / surface                       | Invocation                                        | Role                                                                           |
| ------------ | ------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------ |
| **init**     | [`vc-init`](vc-init/SKILL.md)         | `vibecrafted init [agent]`, `vc-init`, `/vc-init` | Session orientation — not a write pipeline worker                              |
| **ship**     | [`vc-ship`](vc-ship/SKILL.md)         | `vibecrafted ship <agent>`, `vc-ship`, `/vc-ship` | **Lifecycle umbrella** (scaffold→release), not a single-stage launcher         |
| **dispatch** | [`vc-dispatch`](vc-dispatch/SKILL.md) | `vibecrafted dispatch …`, `vc-dispatch`           | **External fleet dyspozytura** — runs plans/lines; does not become “implement” |
| **operator** | [`vc-operator`](vc-operator/SKILL.md) | interactive / posture skill                       | Multi-wave orchestration posture                                               |
| **agents**   | [`vc-agents`](vc-agents/SKILL.md)     | doctrine + fleet modes                            | External fleet contract; not a substitute for interactive skill load           |

### Foundation (no core `vibecrafted <name> <agent>` worker of their own)

These skills load **inside** other skills or interactive sessions. They do **not** get a fake `vibecrafted loctree claude` worker invented for symmetry.

| Skill                                             | Role                            |
| ------------------------------------------------- | ------------------------------- |
| [`vc-loctree`](vc-loctree/SKILL.md)               | Structural perception           |
| [`vc-aicx`](vc-aicx/SKILL.md)                     | Intent / session retrieval      |
| [`vc-prview`](vc-prview/SKILL.md)                 | PR artifact generation          |
| [`vc-screenscribe`](vc-screenscribe/SKILL.md)     | Screencast → findings           |
| [`vc-skillaunch`](vc-skillaunch/SKILL.md)         | Package a workflow into a skill |
| [`vibecraftsmanship`](vibecraftsmanship/SKILL.md) | Craft doctrine                  |

---

## Per-launcher rule (the semantic delta)

For each launcher `L` with skill `vc-L`:

1. **Worker:** only `vibecrafted L <agent>` (or documented alias). Never write `vibecrafted <workflow> <agent>` as if `workflow` were a placeholder for all skills.
2. **Interactive:** only `/vc-L` (or load `vc-L/SKILL.md`). Execute in-session; freer native when the run needs depth.
3. **Operator dispatch:** may launch `vibecrafted L <agent>` on a line; skill identity of `L` is preserved inside the worker brief.
4. **Do not** externalize interactive `/vc-L` solely because a launcher exists.
5. **Do not** turn every skill into workflow-ERi; only `workflow` is ERi.

### Worked example: workflow (operator canon)

| Path          | Literal                                              |
| ------------- | ---------------------------------------------------- |
| 1 Worker      | `vibecrafted workflow <agent>`                       |
| 2 Interactive | `/vc-workflow`                                       |
| 3 Operator    | `vibecrafted workflow <agent>` via dispatch/operator |

### Worked example: review (same shape, different name)

| Path          | Literal                                            |
| ------------- | -------------------------------------------------- |
| 1 Worker      | `vibecrafted review <agent>`                       |
| 2 Interactive | `/vc-review`                                       |
| 3 Operator    | `vibecrafted review <agent>` via dispatch/operator |

### Worked example: implement vs justdo (precision — not the same cell)

|             | `implement`                                                 | `justdo`                                                                                            |
| ----------- | ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Matrix cell | **Ship-cycle** WRITE stage                                  | **Additional** posture launcher                                                                     |
| Skill id    | `implement` / `vc-implement`                                | `justdo` / `vc-justdo`                                                                              |
| Mandate     | Structured e2e implementation (followup + marbles built in) | No-ceremony delivery; **task type is the prompt** (implement / review / audit / research / fix / …) |
| Pipeline    | Yes — VC-ship stage                                         | **No** — stands beside ship (ADR-0001)                                                              |
| Worker      | `vibecrafted implement <agent>`                             | `vibecrafted justdo <agent>`                                                                        |
| Interactive | `/vc-implement`                                             | `/vc-justdo`                                                                                        |
| Not         | Not a posture alias for everything                          | Not an alias of `implement`                                                                         |

Pick the cell by intent: ship-stage delivery → `implement`. Daily rescue / prompt-typed work with ownership posture → `justdo`.

### Worked example: ship (meta — different product)

| Path          | Literal                                                                      |
| ------------- | ---------------------------------------------------------------------------- |
| 1 Worker      | `vibecrafted ship <agent>` (lifecycle run)                                   |
| 2 Interactive | `/vc-ship` — load ship umbrella; stages keep their own launchers             |
| 3 Operator    | ship as baton carrier; stages still `vibecrafted scaffold \| implement \| …` |

---

## Exceptions and references

- **Native subagent bounds:** [`vc-delegate`](vc-delegate/SKILL.md)
- **External fleet dispatch:** [`vc-dispatch`](vc-dispatch/SKILL.md)
- **Operator multi-wave orchestration:** [`vc-operator`](vc-operator/SKILL.md)
- **External fleet modes:** [`vc-agents`](vc-agents/SKILL.md)
- **Verification (walk-around):** [`VERIFICATION_RULE.md`](VERIFICATION_RULE.md)
- **Living Tree:** [`LIVING_TREE_RULE.md`](LIVING_TREE_RULE.md)
- **Runtime feedback ledger (per-command corrections):** [`RUNTIME_FEEDBACK.md`](RUNTIME_FEEDBACK.md)

<!-- /fleet-imperative -->
