# Documentation Map

This is the spine for keeping Vibecrafted docs honest.

If a document disagrees with the live command deck, the command deck wins. If a
skill doc disagrees with the live launcher, the live launcher wins. If a roadmap
claims something is done but `doctor`, `help`, or a real run cannot prove it, the
roadmap is aspirational.

## Current Product Truth

Vibecrafted is the release engine for AI-built repos. It sits after agents have
already produced code and before real users have to trust the result.

The product has six working layers:

| Layer                 | Current source of truth                                                                                       |
| --------------------- | ------------------------------------------------------------------------------------------------------------- |
| Public promise        | `README.md`, `docs/QUICK_START.md`, `docs/FAQ.md`                                                             |
| Operator runbook      | `docs/RUNBOOK.md` — terminal-first: cold start, dispatch, supervision, recovery                               |
| Install and support   | `docs/INSTALL.md` (channel matrix + status), `docs/DOCKER.md`, `make help`, `make help-dev`                   |
| Release cut           | `docs/RELEASE_KICKOFF.md` (identity), `docs/RELEASE_CHECKLIST.md` (4.1.0 DMG command sequence)                |
| Next release          | `docs/ROADMAP_4.1.1.md` — planned shared, digestible `vc-init pack` for every provider                        |
| Package-manager stage | `packaging/` (Homebrew formula + cask; winget skipped — no native Windows build)                              |
| Install (public docs) | `docs/public/getting-started/`: `install.md` · `build-from-source.md` · `first-run.md` · `update.md`          |
| Command deck          | `scripts/vibecrafted`, `docs/WORKFLOWS.md`, `docs/SKILLS.md`                                                  |
| Runtime and artifacts | `runtime/README.md`, `docs/runtime/README.md`, `docs/runtime/TOPOLOGY.md`                                     |
| Skill behavior        | `skills/<skill>/SKILL.md` plus `FLOW.md` and nearby contracts                                                 |
| Architecture doctrine | `docs/adr/` — ADR-0002 ownership matrix (`ownership-matrix.json`) gated by `tests/test_ownership_contract.py` |

Use the current launcher as the quick reality check:

```bash
vibecrafted help
vibecrafted help --all
make help
make help-dev
vibecrafted doctor
```

## Command Grammar

Use skill-first commands in public docs:

```bash
vibecrafted init claude
vibecrafted workflow claude --prompt "Plan and implement auth"
vibecrafted implement codex --prompt "Ship the bounded fix"
vibecrafted review codex --prompt "Review HEAD~5..HEAD"
vibecrafted marbles codex --count 3 --depth 3
vibecrafted dou claude --prompt "Audit launch readiness"
```

Compatibility aliases may be mentioned once, never taught as the primary path:

- `vibecrafted justdo` -> `vibecrafted implement`
- `vc-justdo` -> `vc-implement`
- `vc-<skill>` wrappers -> installed shell shortcuts for people who already live
  in the operator shell

Agent-mode grammar also exists and is intentionally power-user material:

```bash
vibecrafted implement codex .vibecrafted/plans/my-plan.md
vibecrafted observe claude --last
```

Keep it in `help --all`, runtime docs, or `vc-agents` docs. Do not make it the
first path a founder sees.

## Runtime Truth

Current runtime is not a future scaffold. It is active.

| Surface                                 | Status                                                                                 |
| --------------------------------------- | -------------------------------------------------------------------------------------- |
| `scripts/vibecrafted`                   | Live command deck and routing layer                                                    |
| `runtime/scripts/`                      | Active spawn, await, meta, watcher, marbles, and installer scripts                     |
| `runtime/scripts/lib/`                  | Shared launcher/session/path/meta library                                              |
| `runtime/shell/lib/`                    | Installed shell facade modules                                                         |
| `runtime/vc-marbles/`                   | Extracted per-workflow runtime pattern                                                 |
| `runtime/vc-research/`                  | Extracted research shell runtime                                                       |
| `runtime/vc-operator/`                  | Mission-control helpers, not a public `vibecrafted operator` command                   |
| `vibecrafted dispatch`                  | Deterministic dispatch supervisor and async lifecycle lane                             |
| `vibecrafted gui` / `tui` / `dashboard` | Operator surfaces, second-visit tools                                                  |
| Run observability ownership             | Server/VOC canonical browsing; terminal triage is manual compatibility — see `docs/runtime/TRIAGE_AND_SESSIONS.md` |
| Tools home vs checkout                  | Daily CLI runs staged `vibecrafted-current`, not floating git HEAD                     |

When docs need to discuss what is planned, say "planned" or "partial." Do not
leave old design language that says a live directory is reserved for later.

**Install discipline:** `git push` / merge alone does not refresh the staged
tools home. After runtime wire changes, `make install` (or the install path you
actually use) must stamp `VERSION` to the intended `+g<sha>`.

## Skill Runtime Split

Skill-loading and runtime invocation are different things.

| Layer                                      | Meaning                                                      |
| ------------------------------------------ | ------------------------------------------------------------ |
| `$vc-ownership` in a chat prompt           | Current agent adopts ownership posture                       |
| `vibecrafted ownership codex --prompt ...` | Framework launches an ownership run                          |
| `$vc-delegate`                             | Native in-process delegation is allowed for bounded sidecars |
| `vibecrafted delegate <agent>`             | Runtime lane for explicit delegate work                      |
| `$vc-operator`                             | Current agent conducts a multi-wave plan                     |
| `vibecrafted dispatch <file.toml>`         | Live deterministic supervisor path                           |

Do not document `vibecrafted operator <agent>` as a live public command unless
the launcher exposes it again.

## Sweep Ledger

This pass covered every tracked markdown/html doc family:

- top-level docs: `README`, `CHANGELOG`, `CONTRIBUTING`, `SECURITY`, `AGENTS`
- public docs: `docs/*.md`, `docs/*.html`, `docs/pl`, `docs/presence`
- installer docs: `docs/installer/*`
- runtime docs: `docs/runtime/*`, `runtime/**/*.md`
- skill docs: `skills/**/*.md`, including templates and references
- app/package docs: `vibecrafted-app`, `vibecrafted-core`, `vibecrafted-mcp`,
  `vibecrafted-server`, `vibecrafted-vm`, `tools`, `templates`, `workflows`

### 2026-07-22 — triage / SESSIONS / install stamp

Field: many completed implement runs + clogged work-session rail + `f·x·n=0`
while checkout already had triage wire and tools home lagged one commit.

| Doc                                          | Change                                                                                                 |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `docs/runtime/TRIAGE_AND_SESSIONS.md`        | **New** canonical contract (product order: finalize→reap→triage; EXIT footer often only on skip/error) |
| `docs/VC-FRAME.md`                           | SESSIONS rail, triage, research multi-pane vs buckets; operator tabs **Start here** + **Shell**        |
| `docs/runtime/AGENT_OPS.md`                  | Link + “triage in scope”; push≠install failure mode                                                    |
| `docs/runtime/CONTRACT.md`                   | Link from frontmatter claim rule                                                                       |
| `docs/runtime/README.md`                     | Index row for triage + AGENT_OPS                                                                       |
| `docs/INSTALL.md`                            | Verify stamp, push≠install, rail-zero troubleshooting                                                  |
| `docs/FAQ.md`                                | pull≠install + f·x·n=0 answers                                                                         |
| `docs/FAQ-ANSWERED.md`                       | Parity with short FAQ (pull≠install + rail zeros)                                                      |
| `docs/QUICK_START.md`                        | Verify + tools-home stamp + link to triage canon                                                       |
| `docs/WORKFLOWS.md`                          | Runtime contract bullets + next reading                                                                |
| `docs/runtime/AGENT_INTERACTIVE_CONTRACT.md` | Finish → bucket sessions pointer after G7                                                              |
| `docs/runtime/EXECUTION_SURFACES.md`         | Checkout override ≠ staged daily driver                                                                |
| `docs/DOCUMENTATION_MAP.md`                  | This ledger entry + runtime truth rows                                                                 |

Not in this pass (deliberate): skill-body rewrites, `docs/pl/*` mirror, product
marketing pages, implementing the research-generator session-rail code fix
(docs name the gap; code change is separate).

This file is not a replacement for those docs. It is the map that keeps them
from drifting into separate religions.
