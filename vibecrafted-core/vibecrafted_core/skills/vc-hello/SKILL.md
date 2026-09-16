---
name: vc-hello
version: 0.1.0
description: >
  Fleet onboarding and config parity for agent CLIs. This skill should be used
  when the user asks to "dodaj nowego agenta do floty", "wyrównaj config CLI z
  zespołem", "onboard a new agent CLI", "skonfiguruj swoje cli jak reszta",
  "sprawdź drift configu", or when a new human or agent joins Vibecrafted.
  Scans the fleet's configs (Claude / Codex / Grok / Kimi / …), extracts the
  shared posture, maps it onto a target CLI using documented keys only,
  validates through the target's native doctor, and reports parity and gaps.
loctree_value: "not structural — operates on CLI config files, not repo code"
aicx_value: "intent history for why a fleet posture decision was made"
dogfooding: "runs on the operator's own machine config"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-hello` (meta skill)**
>
> Same three-path _shape_ as the fleet, with **this** skill's literals — see the
> canonical [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                            |
> | ----------------------- | ------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | none — meta surface, no `vibecrafted hello <agent>` worker                                        |
> | 2. Interactive          | `vc-hello` — execute **in this session** (Skill tool / slash); the whole run is local config work |
> | 3. Agent-operator       | may load it for a new agent it is onboarding, preserving this skill's identity                    |
>
> **Note:** Onboarding gate, not a write pipeline. Runs on the operator's own
> machine configs; edits land through the target CLI's validated apply flow.

<!-- /fleet-imperative -->

# vc-hello — Fleet Onboarding & Config Parity

A new agent CLI joins the fleet the way a person joins a team: it gets the
shared posture on day one, not after three hours of archaeology. `vc-hello` is
the welcome mat and the alignment pass: scan what the fleet already agrees on
(full-auto permissions, top-tier effort, dark theme, shared MCP servers,
doctrinal hooks), map that posture onto the new CLI with its own documented
keys, prove the result through the CLI's native validator, and report parity
with the gaps named honestly.

It is also the entry point for a new **human or agent** into Vibecrafted: hand
them this skill and it carries the posture ([vibecraftsmanship](../vibecraftsmanship/SKILL.md)),
the canonical upstream installers, and the parity workflow in one pack — see
[references/onboarding-stack.md](references/onboarding-stack.md).

## Goal

**Proposal — Founder owns the final wording.** `vc-hello` produces a target CLI
running at fleet parity (or a drift report naming every divergence) and is done
when the target's own schema validator passes the applied config and the parity
report lists each axis as match / divergent / no-key with evidence. Founder
confirms or rewrites this paragraph before the skill is canonical.

## When To Use

- A new agent CLI is added to the fleet and must inherit the shared posture.
- Recurring drift audit: "sprawdź, czy mój config się nie rozjechał z flotą".
- Onboarding a new human or agent into Vibecrafted (posture + install map).

**When NOT to use:**

- Repo-level orientation — that is `vc-init` (vc-hello touches CLI configs, not code).
- Editing one kimi-code setting — that is the built-in `update-config` skill;
  vc-hello **delegates** the kimi apply stage to it.

## Pipeline Position

- Upstream: none required — operates on home-directory configs, not a repo
  (no-repo exception: no `vc-init` gate).
- Downstream: parity gaps that need code (missing hook script, missing MCP
  server install) hand off to `vc-implement`; fleet posture disputes escalate
  to the Founder.

## Workflow

### Mode A — Onboarding (full parity)

1. **Scan the fleet.** `uv run tools/fleet_scan.py scan --output <dir>/posture.json`.
   Read the consensus section, not raw configs — the scanner normalizes axes
   (permission posture, effort, theme, language, auto-update, MCP servers,
   hook families) and redacts secrets structurally.
2. **Fetch the target CLI's official config docs** before touching any key.
   Docs unreachable → proceed from model knowledge, mark every unverified key
   in the report, and lean on the target's native validator as the falsifier.
3. **Map consensus → target keys.** Use
   [references/fleet-config-map.md](references/fleet-config-map.md) for known
   equivalences (`never`/`always-approve`/`yolo` → full-auto; `xhigh` → the
   target's top supported tier). Judge per item: replicable, CLI-specific
   (skip + report), or secret-bearing (skip + report — never copy secrets
   between files). Fleet conflicts resolve by majority, outlier noted.
4. **Smoke-test before wiring.** Any carried-over hook or script gets run once
   against a payload in the target's format (exit 2 blocks, exit 0 allows,
   fail-open is clean). A hook that does nothing in the target (e.g. relies on
   an unsupported `updatedInput`) is dead weight — do not wire it.
5. **Apply through the validated path.** Copy → edit candidate → target-native
   validation (`kimi doctor`, `codex --validate`, …) → timestamped backup →
   overwrite. For kimi-code targets this stage **is** the `update-config`
   skill — follow it, do not reimplement. Finish with the parity report:
   per-axis match / divergent / no-key / skipped-with-reason, plus how to
   reload.

### Mode B — Drift audit (read-only)

`uv run tools/fleet_scan.py diff --target <cli> --output <dir>/drift.json`
(`--posture <file>` reuses a previous scan). Report divergences; edit nothing
unless the user asks for the fix.

## Utility Scripts

`tools/fleet_scan.py` (stdlib only, `uv run`):

- `scan --output FILE` — fleet posture JSON: per-CLI normalized axes +
  `consensus` (majority per axis with conflicts; MCP servers and hook families
  shared by ≥2 members). Secrets never enter the posture by construction; a
  defensive scrub redacts residual secret-shaped strings.
- `diff --target {claude,codex,grok,kimi} --output FILE [--posture FILE]` —
  per-axis drift report with equivalence classes (top-tier effort
  `xhigh`≈`max`, full-auto across vendor spellings).

Exit 1 with the error on stderr on any failure; stdout stays a one-line status.

## Dependencies

- **vibecraftsmanship** — the fleet's posture charter; vc-hello references it
  as the stance new members inherit, it does not restate it.
- **update-config** (built-in, kimi targets) — owns the kimi apply flow.
- **VERIFICATION_RULE** — the apply stage is not done on green schema alone;
  smoke evidence rides along.

## Error Handling

| Situation                                 | Policy                                                                          |
| ----------------------------------------- | ------------------------------------------------------------------------------- |
| Target docs unreachable                   | proceed from knowledge, mark unverified keys, native validator decides          |
| Fleet element carries a secret            | skip + report; redaction enforced mechanically in `scan`                        |
| Fleet conflict (e.g. effort low vs xhigh) | majority wins + outlier noted                                                   |
| Hook incompatible with target             | smoke-test first; dead hooks are not wired                                      |
| Validation fails on candidate             | fix the candidate and re-validate; never overwrite without a timestamped backup |

## Acceptance Criteria

The skill run is **done** when:

- [ ] posture JSON exists and every fleet member present is either scanned or listed as `absent`
- [ ] each consensus axis lands in the target as match, or is named divergent / no-key / skipped-with-reason
- [ ] the target's native validator passes the applied config (Mode A)
- [ ] the parity report states what was **not** verified (Verification Rule)

If any acceptance bullet cannot be ticked with evidence, the skill has not
completed — say so explicitly in the final report.

## Anti-Patterns

- Reading raw 600-line configs (and leaking an API key into context) instead of running `scan`.
- Copying a secret "for parity" — secrets never travel between files.
- Wiring a hook that cannot fire or cannot inject in the target — ceremony without effect.
- Guessing config keys without the target's docs or validator as the falsifier.
- Silent majority votes — every fleet conflict surfaces in the report.
- Pasting `workflow` / ERi rails — vc-hello is not `vc-workflow`.

## Roadmap

- Full vibecrafted-stack install automation for a brand-new human (today:
  curated canonical-upstream reference in `references/onboarding-stack.md`).
- `diff` adapters for claude/codex/grok targets (v0.1 ships `kimi`, the proven path).

## Verify before the handoff

Before claiming "done", walk around the truck — see
[Verification Rule](../VERIFICATION_RULE.md): the applied config passing the
target's own doctor after `/reload` is the artifact; a clean diff report is not
proof the fleet member behaves the same until one real run confirms it.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
