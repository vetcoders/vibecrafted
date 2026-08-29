# Handoff — Cursor fleet parity cut (attempt A) · cursor/workflow/server-caretaker

**Date:** 2026-08-29
**Branch:** `cursor/workflow/server-caretaker`
**Baseline HEAD (pre-cut):** `12f3031d8afcea1316b3df1b0c57a1c81a5f977f` (tip of `origin/fix/v430-dispatcher-shutdown-race-v5` + two landed control-plane commits)
**Worktree:** `~/.vibecrafted/worktrees/vetcoders/vibecrafted/2026_0829/server-caretaker`
**Agent:** cursor (Kimi), attempt A of best-of-3

## Mandate

Full parity of the `cursor` agent with `codex`/`claude` across every vibecrafted
workflow surface, on top of the merged Cut B (PR #72). Semantic audit, smallest
coherent change per surface, parity pinned by tests.

## Living-tree note

This worktree was actively worked by a concurrent cursor agent during the cut
(control-plane revalidate commits `28d858bd`/`12f3031d` landed; a parallel set of
uncommitted parity edits appeared mid-flight: deck agent lists, `help_surface`
registry-derived selector, `schema.py` policy marker, `supervisor_async` binary
fold, `wrappers` usage, `cursor_spawn.sh`, marbles/observe/await/meta scripts,
SKILL.md fleet enumerations, mux/tray/FFI `ClientKind::Cursor`, and the umbrella
test `test_cursor_parity.py`). Those edits were inspected, found correct, adopted,
and completed here. Unrelated in-flight work (`AGENTS.md` namespace doctrine,
`scripts/vetcoders_install.py` rework, `tests/tui/test_installer_{doctor,uninstall}.py`)
was deliberately left uncommitted for its owner.

## Parity matrix

### Python core

| Surface                                                                                                       | Status                  | Evidence                                                                                                        |
| ------------------------------------------------------------------------------------------------------------- | ----------------------- | --------------------------------------------------------------------------------------------------------------- |
| `workflow.py` / `cli.py` / `wrappers.py` / `ship.py` agent registries                                         | already-done (Cut B)    | `test_cursor_parity.py::test_python_registries_accept_cursor`                                                   |
| `spawn.py` policy, binary pin (`cursor-agent`), stdin transport                                               | already-done (Cut B)    | `test_cursor_fleet_adapter.py`                                                                                  |
| `agent_stream.py` claude-shaped stream parsing                                                                | already-done (Cut B)    | fleet adapter tests                                                                                             |
| `research_config.py`, `agent_dispatch.py`, `process_control.py`, `loop.py`, `supervisor_async.py` fleet lists | already-done (Cut B)    | parity umbrella                                                                                                 |
| `supervisor_async._infer_agent` `cursor-agent` → `cursor` fold                                                | fixed-in-this-cut       | `test_supervisor_infers_cursor_key_from_cursor_agent_binary`                                                    |
| `help_surface.py` `AGENT_SELECTOR` / fleet line derived from `SUPPORTED_AGENTS`                               | fixed-in-this-cut       | `test_help_surface_agent_selector_includes_cursor`, `test_every_workflow_help_renders_cursor_in_selector`       |
| `wrappers.py` vc-* usage line                                                                                 | fixed-in-this-cut       | `test_wrapper_usage_line_includes_cursor`                                                                       |
| `dispatch/schema.py` `/.cursor/` provider-root policy marker                                                  | fixed-in-this-cut       | `test_dispatch_policy_rejects_cursor_runtime_root` (source + behavioral)                                        |
| `workflow_runtime.NATIVE_RESUME_AGENTS`                                                                       | not-applicable (sealed) | headless `-p --resume` is UNVERIFIED → fail-closed; pinned by `test_native_resume_stays_fail_closed_for_cursor` |
| `dispatch/supervisor.py` legacy `.claude/worktrees/<id>` recovery                                             | not-applicable          | historical storage path; renaming breaks recovery lookups                                                       |
| `lifecycle_runner.py`                                                                                         | already-done            | choices derive from `SUPPORTED_AGENTS` (Cut B)                                                                  |
| `run_board.py` hint examples, `stage_cast.py` docstrings, `workflows/model.py` `default_agent="claude"`       | not-applicable          | illustrative prose / single product default, not agent enumerations                                             |

### Shell deck + runtime

| Surface                                                                                                                                                      | Status            | Evidence                                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/vibecrafted` + `vibecrafted_core/deck/vibecrafted` (byte-identical) `_agents`, `_has_agent`, help texts, agent-first grammar, `help cursor` routing | fixed-in-this-cut | `test_deck_*` (5 tests incl. behavioral `init cursor` gate)                                                                                     |
| deck `_require_agent_cli` probes `cursor-agent` (not the editor CLI `cursor`) + install hint                                                                 | fixed-in-this-cut | `test_deck_probes_cursor_agent_binary_not_editor_cli`, `test_deck_init_accepts_cursor_and_names_cursor_agent_binary`                            |
| `runtime/scripts/cursor_spawn.sh` (new) — headless `-p` stdin, stream-json tee, `--force --trust`, salvage hooks, agent_stream filter                        | fixed-in-this-cut | `test_agy_junie_pipeline.py::test_cursor_spawn_dry_run_uses_stream_json_stdin_contract`                                                         |
| `runtime/shell/lib/dispatch.sh` — 26 `cursor-*` / `cursor-skill-*` helpers, `_vetcoders_has_agent`, usage strings                                            | fixed-in-this-cut | shellcheck via `make check`; helper resolution is by-name (`${agent}-skill-${skill}`)                                                           |
| `runtime/shell/lib/dispatch_wrappers.sh` — cursor review/plan/implement/research/prompt/observe/await                                                        | fixed-in-this-cut | `make check`                                                                                                                                    |
| `runtime/shell/lib/skill_shortcuts.sh` — `cursor-dou`, `cursor-hydrate`                                                                                      | fixed-in-this-cut | `make check`                                                                                                                                    |
| `runtime/shell/lib/marbles.sh` — cursor fresh-session command (headless + interactive), vc-resume usage                                                      | fixed-in-this-cut | `make check`                                                                                                                                    |
| `runtime/scripts/marbles_next.sh` — agent validation, verification-resume guard (cursor fail-closed with explicit warning)                                   | fixed-in-this-cut | parity umbrella deck/script gates                                                                                                               |
| `runtime/scripts/marbles_spawn.sh` agent regex                                                                                                               | fixed-in-this-cut | `make check`                                                                                                                                    |
| `runtime/scripts/observe.sh`, `await.sh` usage + case                                                                                                        | fixed-in-this-cut | `make check`                                                                                                                                    |
| `runtime/scripts/lib/meta.sh` `CURSOR_MODEL` identity candidate                                                                                              | fixed-in-this-cut | `make check`                                                                                                                                    |
| `runtime/vc-research/shell/research.sh` agent cases + supported-agents messages + `--synthesizer` gate                                                       | fixed-in-this-cut | `make check`                                                                                                                                    |
| `runtime/scripts/skills_sync.sh` default tool set + `~/.cursor/skills` view                                                                                  | fixed-in-this-cut | `make check`                                                                                                                                    |
| `runtime/scripts/lib/rotation.sh` marbles rotation pool `[codex, claude, agy]`                                                                               | not-applicable    | default cycle membership is a product decision (same class as research default trio); cursor is accepted as seed/agent everywhere rotation runs |
| `runtime/scripts/lib/prompt.sh` codex report contract                                                                                                        | not-applicable    | provider-specific contract; cursor emits claude-shaped events                                                                                   |
| `runtime/vc-marbles/orchestrator/*` stop-hook                                                                                                                | not-applicable    | Claude Code-specific integration                                                                                                                |
| `claude_spawn.sh`, `vc_frame.sh`, `prompts.sh`, `util.sh` mentions                                                                                           | not-applicable    | provider-specific file / illustrative comments                                                                                                  |

### Rust (vibecrafted-app workspace)

| Surface                                                                                          | Status               | Evidence                                                   |
| ------------------------------------------------------------------------------------------------ | -------------------- | ---------------------------------------------------------- |
| `tui-agent/src/app.rs` `agents()` picker                                                         | fixed-in-this-cut    | `test_tui_agent_picker_offers_cursor`                      |
| `tui-agent/src/skills_catalog.rs` `SkillAgent::Cursor` (label/from_cli_token/resolved_cli_token) | fixed-in-this-cut    | `test_tui_skills_catalog_resolves_cursor_token`            |
| `tui-agent/src/procs/model.rs` `FamilyTag::Cursor` + classify + unit test                        | fixed-in-this-cut    | `test_tui_process_family_tags_cursor`, `cargo test -p voc` |
| `tui-agent/src/lib.rs` launch verify-gate mapping                                                | fixed-in-this-cut    | `test_mux_ipc_client_kind_has_cursor_variant`              |
| `mux-agent` `ClientKind::Cursor` + handlers → `HostKind::Cursor`                                 | already-done (Cut B) | parity umbrella source gate                                |
| `tray-agent/src/ipc_client.rs` Cursor arm                                                        | fixed-in-this-cut    | `cargo clippy -D warnings`, `cargo test -p tray-agent`     |
| `shell-agent/ffi` `FfiClientKind::Cursor` + conversions                                          | fixed-in-this-cut    | `cargo test -p vibecrafted-shell-ffi`                      |
| remaining `"claude"` hits in `mission_control.rs`/`observe.rs`/`ui.rs`/`control-core/read.rs`    | not-applicable       | test fixtures, not enumerations                            |

### Install / GUI / VM / containers

| Surface                                                                               | Status            | Evidence                                                                                                                                                               |
| ------------------------------------------------------------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `install.toml` diagnostics probe `cursor-agent`                                       | fixed-in-this-cut | `test_install_diagnostics_probe_cursor_agent_binary`                                                                                                                   |
| `scripts/installer_gui.py` `AGENT_COMMANDS` + launcher agent dropdown                 | fixed-in-this-cut | `test_installer_gui_launcher_offers_cursor`, `tests/tui/test_installer_gui.py` (104 passed with doctor)                                                                |
| `scripts/install-foundations.sh` cursor-agent install hint                            | fixed-in-this-cut | `make check`                                                                                                                                                           |
| `vibecrafted-vm/wizard` `cursor_sessions` mount (~/.cursor, EN+PL labels, default ON) | fixed-in-this-cut | `py_compile`, `strings.json` parse                                                                                                                                     |
| `vibecrafted-vm/entry.sh` readiness probe `cursor-agent`                              | fixed-in-this-cut | `make check`                                                                                                                                                           |
| `docker/entrypoint.sh` exec allowlist `cursor-agent`                                  | fixed-in-this-cut | `make check`                                                                                                                                                           |
| `plugins/iterm2` triggers                                                             | not-applicable    | triggers match the retired `<Agent> <skill> started` banner nothing prints anymore; set already lacks agy/junie/grok — needs a banner-format rewrite, not a cursor row |

### Skills + docs enumerations (EN + PL)

| Surface                                                                                                                                                                                                                                             | Status                                                            |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `vc-agents`, `vc-workflow`, `vc-research`, `vc-trust`, `vc-canary`, `vc-dispatch` SKILL.md fleet enumerations                                                                                                                                       | fixed-in-this-cut                                                 |
| `vc-operator/references/agent-control-contract.md`                                                                                                                                                                                                  | fixed-in-this-cut                                                 |
| `vc-release/references/release-report-template.md`                                                                                                                                                                                                  | fixed-in-this-cut                                                 |
| `vc-research/references/synthesis-template.md` (incl. prose example line)                                                                                                                                                                           | fixed-in-this-cut                                                 |
| `vc-scaffold` plan-template / output-shapes / plans/HOWTO                                                                                                                                                                                           | fixed-in-this-cut                                                 |
| docs: CLI_PRODUCT_SPEC, FOUNDATION, RUNBOOK, _CONTRACT, cli-overview (+ cursor `--model` override), getting-started overview + first-run (+ install table), security, skills-catalog, faq, katalog-launcherow.html, operator/FLEET_DISPATCH_RUNBOOK | fixed-in-this-cut                                                 |
| research default trio `claude codex agy` (SKILL.md prose, research.sh builtin, research.yaml.example)                                                                                                                                               | not-applicable — product default, cursor selectable in every lane |

All of the above pinned by `test_skill_agent_enumerations_include_cursor` (20 files)
and `test_docs_fleet_enumerations_include_cursor` (9 files).

## Verification performed

- `make check` — green (ruff/prettier/semgrep on changed files, shellcheck 172 files, zsh -n)
- `pytest vibecrafted-core/tests/test_cursor_parity.py` — **52 passed** (isolated `VIBECRAFTED_HOME`, `env -u PYTHONPATH`)
- Focused core suite (parity + fleet adapter + help_surface + wrappers + supervisor_async + workflow + workflow_runtime + capabilities + dispatch/) — 405 passed, 4 failed **pre-existing** (fail identically on pristine `git archive HEAD`: `test_async_supervisor_reads_operator_stop_from_its_event_range`, `test_explicit_transport_retry_replays_across_processes`, `test_launch_workflow_preseeds_machine_owned_claim_digest`, `test_launch_workflow_artifact_paths_are_terminal_truth`)
- `pytest tests/tui/test_installer_gui.py tests/tui/test_installer_doctor.py` — 104 passed (separate invocation, isolated HOME)
- `pytest tests/tui/test_agy_junie_pipeline.py` — 9 passed (incl. new cursor spawn dry-run contract)
- `cargo clippy --workspace --all-features -- -D warnings` — clean
- `cargo test -p voc -p rmcp-mux -p tray-agent -p vibecrafted-shell-ffi` — all green (incl. new `FamilyTag::Cursor` classify assertion)
- `vibecrafted-vm/wizard`: `py_compile` + `strings.json` JSON parse

## Verification not performed

- Live `cursor-agent` end-to-end run (headless implement + interactive resume) on a real repo — requires the installed CLI and a Founder-gated workspace; the dry-run launcher contract is pinned instead.
- Swift consumer of the regenerated UniFFI bindings (`FfiClientKind::Cursor`) — bindings regenerate at app build time; no Swift source change needed for an added enum variant.
- Full `vibecrafted-core/tests` suite (only focused modules per cut scope).

## Risks / follow-up

- `NATIVE_RESUME_AGENTS` stays fail-closed for cursor until headless `-p --resume <chatId>` is proven on host; proving it flips one frozenset + one test.
- iTerm2 trigger pack targets a retired banner format for ALL agents — rewrite against the current `𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. · <agent>-<mode>` banner is a separate cut.
- Marbles rotation pool and research default trio remain `codex/claude/agy` by product default; promoting cursor into default cycles is a Founder decision, one line each.
- 4 pre-existing red tests listed above are unattributed to this cut (identical on pristine HEAD) — recommend a HAK ticket.
- Concurrent worker's unrelated WIP (install doctrine: `AGENTS.md`, `vetcoders_install.py`, `test_installer_{doctor,uninstall}.py`) left uncommitted in the worktree by design.
