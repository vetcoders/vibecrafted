# Cursor Fleet Parity — Attempt C Handoff

Date: 2026-08-29
Branch: cursor/workflow/server-caretaker (rebased onto `origin/fix/v430-dispatcher-shutdown-race-v5` = PR #72 merge `0a5eaaea`)
Gate: `vibecrafted-core/tests/test_cursor_parity.py` — **52/52 green**

## Method

Test-first: a parity gate enumerating every workflow/agent-acceptance surface
was written red, then each surface was flipped green with the smallest
semantic change. Audit rule per surface: hardcoded list → add cursor;
registry-derived → fix at registry; prose/docs → extend the enumeration;
example/default/storage-path → leave.

This cut shares the branch with a concurrent parity worker (deck, help
surface, installer, runtime scripts, VM wizard lanes). Surfaces below are
marked with the lane that landed them; the gate pins the combined tree.

## Parity matrix

### Python acceptance registries

| Surface                                      | Status                  | Evidence                                                                                                             |
| -------------------------------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `workflow.SUPPORTED_AGENTS`                  | already-done (Cut B)    | `test_python_registries_accept_cursor`                                                                               |
| `ship.SUPPORTED_AGENTS`                      | already-done (Cut B)    | same                                                                                                                 |
| `wrappers.AGENTS`                            | already-done (Cut B)    | same                                                                                                                 |
| `cli.AGENTS`                                 | already-done (Cut B)    | same                                                                                                                 |
| `spawn.POLICY_PROVIDERS`                     | already-done (Cut B)    | same                                                                                                                 |
| `agent_dispatch.sandbox_supported("cursor")` | already-done (Cut B)    | same                                                                                                                 |
| `research_config.SUPPORTED_RESEARCH_AGENTS`  | already-done (Cut B)    | same                                                                                                                 |
| `supervisor_async._infer_agent` binary fold  | fixed-in-this-cut       | `cursor-agent` argv[0] now folds to fleet key `cursor`; `test_supervisor_infers_cursor_key_from_cursor_agent_binary` |
| `workflow_runtime.NATIVE_RESUME_AGENTS`      | not-applicable (sealed) | headless `-p --resume` UNVERIFIED → fail-closed; pinned by `test_native_resume_stays_fail_closed_for_cursor`         |

### Help surfaces

| Surface                                       | Status                          | Evidence                                                                                                     |
| --------------------------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `help_surface.AGENT_SELECTOR` / `AGENTS_LINE` | fixed-in-this-cut (worker lane) | derived from `SUPPORTED_AGENTS` via `_FLEET_AGENT_ORDER`; `test_help_surface_agent_selector_includes_cursor` |
| Every `WORKFLOW_HELP` topic render            | fixed-in-this-cut (worker lane) | `test_every_workflow_help_renders_cursor_in_selector` (research/paste are agent-free by design)              |
| `wrappers` usage line (`vc-*` entry points)   | fixed-in-this-cut               | `test_wrapper_usage_line_includes_cursor`                                                                    |

### Shell deck (canonical + mirror, byte-identical)

| Surface                                              | Status                          | Evidence                                                                                                        |
| ---------------------------------------------------- | ------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `_agents` acceptance gate + `_has_agent`             | fixed-in-this-cut (worker lane) | `test_deck_agent_acceptance_gate_lists_cursor`                                                                  |
| CLI probe maps `cursor` → `cursor-agent` binary      | fixed-in-this-cut (worker lane) | `test_deck_probes_cursor_agent_binary_not_editor_cli` (`command -v cursor` is the editor, not the fleet binary) |
| Missing-CLI error names the probed binary            | fixed-in-this-cut               | `test_deck_init_accepts_cursor_and_names_cursor_agent_binary` (behavioral, restricted PATH)                     |
| Help texts / fleet dotted line / `help cursor` topic | fixed-in-this-cut (worker lane) | `test_deck_help_*` (behavioral)                                                                                 |
| Canonical ↔ mirror byte identity                     | fixed-in-this-cut               | `test_deck_mirror_is_byte_identical_to_canonical`                                                               |

### Dispatch policy

| Surface                                          | Status            | Evidence                                                                                                                             |
| ------------------------------------------------ | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `dispatch/schema.py` recovery-only runtime roots | fixed-in-this-cut | `/.cursor/` joins `/.claude/` `/.codex/` `/.gemini/`; behavioral doctor rejects `$HOME/.cursor/reports` like `$HOME/.claude/reports` |

### Rust operator surfaces

| Surface                                                              | Status                          | Evidence                                                          |
| -------------------------------------------------------------------- | ------------------------------- | ----------------------------------------------------------------- |
| `mux-agent` `ClientKind::Cursor` + handler map to `HostKind::Cursor` | fixed-in-this-cut (worker lane) | `test_mux_ipc_client_kind_has_cursor_variant` + cargo clippy/test |
| `tui-agent` agent picker `agents()`                                  | fixed-in-this-cut (worker lane) | `test_tui_agent_picker_offers_cursor`                             |
| `tui-agent` `SkillAgent::Cursor` token round-trip                    | fixed-in-this-cut (worker lane) | `test_tui_skills_catalog_resolves_cursor_token`                   |
| `tui-agent` process `FamilyTag::Cursor` classify                     | fixed-in-this-cut (worker lane) | `test_tui_process_family_tags_cursor`                             |
| `tui-agent` `launch_selected` cursor → `ClientKind::Cursor`          | fixed-in-this-cut               | duplicate arm from concurrent edit removed; clippy clean          |
| `tray-agent` `client_label` Cursor arm                               | fixed-in-this-cut               | exhaustive-match compile gate                                     |
| `shell-agent` FFI `FfiClientKind::Cursor` + both `From` directions   | fixed-in-this-cut               | uniffi enum; exhaustive-match compile gate                        |

### Runtime scripts

| Surface                                                                                                     | Status                          | Evidence                                                                                                                                                                                                                      |
| ----------------------------------------------------------------------------------------------------------- | ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cursor_spawn.sh` headless wrapper                                                                          | fixed-in-this-cut (worker lane) | `cursor-agent -p --output-format stream-json --force --trust`, stdin prompt, transcript tee, salvage reports; pinned by `test_cursor_spawn_dry_run_uses_stream_json_stdin_contract` in `tests/tui/test_agy_junie_pipeline.py` |
| `await.sh` / `observe.sh` / `marbles_next.sh` / `meta.sh` / `marbles_spawn.sh` agent lists + `CURSOR_MODEL` | fixed-in-this-cut (worker lane) | deck/help behavioral tests + pipeline dry-run meta test now loops cursor                                                                                                                                                      |
| `shell/lib/dispatch.sh`, `dispatch_wrappers.sh`, `skill_shortcuts.sh`, `core.sh`                            | fixed-in-this-cut (worker lane) | `make check` shellcheck + pipeline tests                                                                                                                                                                                      |

### Skills fleet enumerations (EN + PL)

| Surface                                                        | Status            | Evidence                                                                |
| -------------------------------------------------------------- | ----------------- | ----------------------------------------------------------------------- |
| `vc-agents`, `vc-operator/agent-control-contract`, `vc-trust`  | fixed-in-this-cut | `test_skill_agent_enumerations_include_cursor` (20 files parameterized) |
| `vc-release` report template, `vc-research` synthesis template | fixed-in-this-cut | same                                                                    |
| `vc-scaffold` plan-template / output-shapes / HOWTO            | fixed-in-this-cut | same                                                                    |
| `vc-workflow`, `vc-research` SKILL.md frontmatter enums        | fixed-in-this-cut | same                                                                    |

### Docs fleet enumerations

| Surface                                                                                                                    | Status               | Evidence                                                                                                                                                  |
| -------------------------------------------------------------------------------------------------------------------------- | -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `docs/CLI_PRODUCT_SPEC.md`, `FOUNDATION.md`, `RUNBOOK.md`                                                                  | fixed-in-this-cut    | `test_docs_fleet_enumerations_include_cursor`                                                                                                             |
| `docs/public/{cli/cli-overview, getting-started/overview, reference/security, skills/skills-catalog, troubleshooting/faq}` | fixed-in-this-cut    | same                                                                                                                                                      |
| `docs/katalog-launcherow.html` (PL)                                                                                        | fixed-in-this-cut    | same                                                                                                                                                      |
| `docs/runtime/CONTRACT.md`, `docs/public/concepts/agents.md`                                                               | already-done (Cut B) | same                                                                                                                                                      |
| `docs/adr/0003-*`, `docs/runtime/OPERATOR_LANE.md`, `docs/operations/RUNBOOK.md`                                           | not-applicable       | existed only on the pre-rebase integration tip; absent from the PR #72 base                                                                               |
| RUNBOOK model-override sentence                                                                                            | fixed-in-this-cut    | reworded to flag-granularity truth ("other agents run their defaults"), matching cli-overview phrasing; cursor override is `CURSOR_MODEL` env, not a flag |

### Install / diagnostics / VM

| Surface                                                                                       | Status                          | Evidence                                             |
| --------------------------------------------------------------------------------------------- | ------------------------------- | ---------------------------------------------------- |
| `install.toml` diagnostics probe `cursor-agent`                                               | fixed-in-this-cut (worker lane) | `test_install_diagnostics_probe_cursor_agent_binary` |
| `installer_gui.AGENT_COMMANDS` + launcher defaults offer cursor                               | fixed-in-this-cut (worker lane) | `test_installer_gui_launcher_offers_cursor`          |
| `vetcoders_install.py` `AGENT_RUNTIMES` + binary map                                          | fixed-in-this-cut (worker lane) | doctor tests                                         |
| `vibecrafted-vm` wizard: `cursor-agent` probe, `~/.cursor` session persistence (EN+PL), mount | fixed-in-this-cut (worker lane) | diff review; VM boot not run                         |

### Not acceptance lists (audited, intentionally untouched)

- `dispatch/supervisor.py` `.claude/worktrees` — storage layout for Mode B cuts, all agents.
- `stage_cast.py`, `lifecycle_runner.py` docstring examples (`audit=claude,grok`).
- `run_board.py` getting-started hint lines (single-agent examples).
- `workflows/model.py` `default_agent = "claude"` — a default, not a gate.
- `plugins/iterm2` triggers — no in-repo emission of Cursor-specific patterns.
- Skills/docs lines using `claude` alone as the example agent (`vibecrafted init claude` etc.) — examples, not enumerations.
- `CLI_PRODUCT_SPEC.md` v3.1.0 sample installer output block — historical sample, refreshing it would falsify what that installer printed.

## Verification performed

- `test_cursor_parity.py`: **52/52 passed** (isolated `VIBECRAFTED_HOME`, `env -u PYTHONPATH`).
- `tests/tui/test_agy_junie_pipeline.py`: **9/9 passed**, incl. new cursor dry-run contract + cursor added to deck-help and dry-run-meta loops.
- `make check`: green (ruff, prettier, semgrep, shellcheck on 172 shell files).
- Focused `vibecrafted-core` modules (`test_wrappers`, `test_help_surface`, `test_supervisor_async`, `test_cursor_fleet_adapter`, `test_cli`, parity): **205 passed, 1 pre-existing red** (below).
- Focused `tests/tui` installer modules: **155 passed, 13 pre-existing reds** (below).
- `cargo clippy --workspace --all-features -- -D warnings`: clean.
- `cargo test --workspace`: all suites `ok`, 0 failed.

## Pre-existing reds (attributed against pristine HEAD via `git archive` snapshot)

- `test_supervisor_async.py::test_async_supervisor_reads_operator_stop_from_its_event_range` — fails identically at HEAD on this host (child process exit 1); untouched by this cut (`_infer_agent` fold is unreachable from that path).
- `test_installer_uninstall.py` — 13 failures present identically at HEAD; the concurrent installer's rework additionally _fixed_ 2 further HEAD reds (`..._forgets_already_removed_old_bare_shim`, `..._restores_public_owner_when_retiring_old_bare_shim`).

## Verification not performed

- No live `cursor-agent` headless spawn from this worktree's code (binary gate is probed, spawn contract pinned via dry-run only). Live dogfood runs by the operator harness were observed in flight against this branch during the cut.
- VM wizard boot (`vibecrafted-vm`) — diff-reviewed only.
- DMG/signing/notarization, release gates — Founder buttons per doctrine.

## Risks / follow-up

- `NATIVE_RESUME_AGENTS` stays fail-closed for cursor until headless `-p --resume` is proven on host; the parity gate pins the exception so it cannot silently flip.
- `FfiClientKind` gained a variant — Swift shell bindings regenerate from uniffi; next app build picks it up.
- A sibling stash (`stash@{0}`, server-caretaker control-plane-revalidate WIP) belongs to the concurrent worker's other lane and was deliberately left untouched.
