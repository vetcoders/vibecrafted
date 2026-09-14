# Handoff — Cursor parser for AICX (Cut A) · cut/cursor-on-throne

**Date:** 2026-09-01
**Branch:** `cut/cursor-on-throne` (worktree `~/.vibecrafted/worktrees/Loctree/aicx/2026_0829/cursor-on-throne`)
**Agent:** cursor (Kimi)
**Sibling cut:** Cut B (fleet adapter) — `work/cursor-260829`, commit `d88133b9` (see `2026-08-29_cursor_fleet-adapter_handoff.md`)

## Mandate (Founder, sealed via Codescribe)

> Wpięcie adaptera kursor agent do Vibecrafted oraz wpięcie parsera kursor do AICX.

## Commits

| SHA       | Scope                                                                                                                                                                       |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0c6e16e` | Parser kernel: `CursorAdapter` (cursor-native-v1), frames rules, `ProviderConversationRef::Cursor`, adversarial closure for 6 adapters, fixtures + oracle golden + manifest |
| `565a794` | (unrelated git-env isolation fix, same session lane)                                                                                                                        |
| `5e0c281` | App wiring: catalog 6th source, discovery, extract CLI, source_index/source_path allowlists, mcp_session, intents, continuity env key                                       |

## Design sealed in this cut

| Decision                 | Choice                                                                                                                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Source layout            | `~/.cursor/projects/<slug>/agent-transcripts/<uuid>/<uuid>.jsonl`                                                                           |
| Identity                 | filename UUID (== parent dir); no in-band session id                                                                                        |
| cwd                      | slug decode, **lossy** (`.`/`/` both collapse to `-`) → `Association::Inferred`, same idiom as Claude fallback                              |
| Timestamps               | harness `<timestamp>` wrapper parsed fail-closed to RFC 3339; absent → `Known::unknown`                                                     |
| Operator speech          | `<user_query>` unwrapped; `system_notification` / `manually_attached_skills` → inject frames, never speech                                  |
| turn_ended               | consumed control row (coverage-honest, no turn)                                                                                             |
| Legacy watermark         | key frozen (`claude+codex+gemini+junie+grok+codescribe`); cursor joined the default `all` set without renaming it — no operator state reset |
| Multi-workspace sessions | one catalog entry per (agent, session_id); live file wins attribution (session moves follow the workspace)                                  |

## Verification performed

- `cargo test --workspace` — green (incl. `cursor_adapter` 5 tests, adversarial contract 6 adapters, `oracle_assertions` suite with 4 new cursor rows, `session_catalog` 12)
- `cargo clippy --workspace --all-targets` clean, `cargo fmt --check` clean
- Oracle golden: `compare.py --case cursor_minimal` PASS via real SUT (`oracle_envelope --agent cursor`)
- **Dogfood (isolated `AICX_HOME`, zero operator-state mutation):** catalog rebuild admits **21 cursor sessions**; `sessions list --agent cursor` renders them with parsed timestamps; `continuity show -p 3more/studio -H 96` lists session `004ffd2e-…` (this very conversation) as `open: cursor` with its intents/decisions; `extract cursor --file` renders wrapper-free operator speech

## Verification not performed

- `tests/parser_oracle/compare.py --all` donor aggregate (donor repo at `/Volumes/LibraxisShare/…` not mounted on this host; cursor case is `rust_golden` and passed individually)
- Living Tree merge / install of the worktree binary into `~/.local` (operator decision)
- Semantic reindex of the operator home with cursor content (needs the installed binary upgraded first)

## Known limitations (honest list)

- Cursor rows carry no model/usage/cwd in-band: provenance reports `Known::unknown` (fail-closed, never guessed)
- Slug decode can fabricate paths for hyphen/underscore-heavy cwds (`2026_0829` → `2026/0829`); attribution is `Inferred`, never `Exact`
- Same-session transcripts across workspaces are deduped to one catalog entry; per-file segments are not merged (the live full-history file wins)

## Next for integrator / Founder

1. Review + merge `cut/cursor-on-throne` (2 commits) when ready — do not auto-integrate.
2. After merge + install: `aicx catalog rebuild --with-chunks && aicx index --semantic` on the real home to bring cursor history into retrieval.
3. Optional: TB (transcript-builder) cursor adapter — TB is the differential oracle donor; adding cursor there upgrades `cursor_minimal` from `rust_golden` to donor-compared.
