# Handoff — Cursor fleet adapter (Cut B) · work/cursor-260829

**Date:** 2026-08-29  
**Branch:** `work/cursor-260829`  
**Baseline HEAD (pre-cut):** `4180ea61e25f9a7021c8817810fc09d14222bbea`  
**Worktree:** `~/.vibecrafted/worktrees/vetcoders/vibecrafted/2026_0829/cursor-260829`  
**Agent:** cursor (Kimi)  
**Codescribe ACK:** speech for sealed session `704b436e-689d-4ed6-98a9-45b2846338bb`

## Mandate (Founder, sealed)

> Wpięcie adaptera kursor agent do Vibecrafted oraz wpięcie parsera kursor do AICX.

This Fleet Worktree owns **Cut B only** (Vibecrafted fleet adapter).  
Cut A (AICX parser) lives in sibling worktree  
`~/.vibecrafted/worktrees/Loctree/aicx/2026_0829/cursor-on-throne` (`cut/cursor-on-throne`) — not integrated here.

## Design sealed in this cut

| Decision               | Choice                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------- |
| Fleet key              | `cursor` (`vibecrafted implement cursor`)                                                   |
| Binary                 | `cursor-agent` via `AGENT_BINARY_NAMES` / `agent_cli_name()`                                |
| Prompt transport       | `stdin` (argv also works; stdin is the headless lane)                                       |
| Stream format          | `--output-format stream-json` (claude-shaped init/assistant/result)                         |
| Interactive resume     | `supported` (`--resume [chatId]`)                                                           |
| Headless native resume | `unverified` — **fail-closed** (not in `NATIVE_RESUME_AGENTS`)                              |
| Native fork            | `unsupported` — interactive bare-fork raises                                                |
| Permissions            | bypass=`--force --trust`; auto=`--trust`; accept-edits=none; read-only=`--mode ask --trust` |

## What changed

- Continuity capability registry entry for `cursor` (probe CLI = `cursor-agent`)
- Spawn: policy contract, `_stdin_command` / `_default_command`, interactive argv, binary pin
- Agent stream: cursor routed through claude-family formatter + thinking deltas + token aliases
- Fleet allowlists: workflow/cli/wrappers/ship/research/dispatch/loop/await/supervisor/workshop
- Docs: `docs/public/concepts/agents.md`, `docs/runtime/CONTRACT.md`, `install.toml` diagnostics list
- Tests: `tests/test_cursor_fleet_adapter.py` + registry/parity updates

## Verification performed

```text
env -u PYTHONPATH python -m pytest \
  tests/test_cursor_fleet_adapter.py \
  tests/test_capability_probe.py \
  tests/test_continuity_kernel.py \
  tests/test_provider_policy.py::test_every_runtime_permission_provider_mode_cell_is_explicit
→ 32 passed, 8 skipped

ruff check (changed spawn/agent_stream/capabilities/test_cursor_fleet_adapter) → clean
```

Host probe (earlier same day, no live services touched):  
`cursor-agent` 2026.08.25-3e8eec8 — argv and stdin `-p` both return; stream-json init carries `session_id`.

## Verification not performed

- Live `vibecrafted workflow cursor --prompt …` against control_plane (would touch runtime)
- Headless `-p --resume <id>` proof (left `UNVERIFIED` on purpose)
- Full `make check` / unified-product-contract-gate
- Living Tree merge / install / service restart
- Cut A (AICX) final commit/gates — sibling worktree may still be dirty

## Safety

- No Living Tree integrate
- No live install, session, or service mutation
- No push

## Next for integrator / Founder

1. Review + merge Cut B when ready (do not auto-integrate).
2. Finish/commit Cut A on `cut/cursor-on-throne`; dogfood: `aicx` sees Cursor transcripts.
3. Optional follow-up: prove headless resume, then flip `noninteractive_resume` to `supported` and add `native_resume_argv`.
4. Optional: installer_gui / vetcoders_install agent lists (left alone this cut to avoid install-surface blast).

## Session compare (resume intake)

| Surface               | State                                                       |
| --------------------- | ----------------------------------------------------------- |
| This Cursor chat      | Fresh resume; no prior durable commit in worktree           |
| Parent Cursor session | Created worktree + ran Cut A; Cut B designed, not committed |
| Worktree at resume    | Clean `@4180ea61`; this cut adds the durable commit below   |
| Living Tree           | Untouched                                                   |
