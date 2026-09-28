# Usage from local harness evidence

The close path (`supervisor_async` for workflow children and
`spawn.finalize_artifacts` for launcher runs) calls `telemetry.build_run_telemetry`.
Observed stream usage wins, including an explicit zero. Only a silent stream
falls back to local harness evidence. The resolver never writes provider logs,
run metadata, or a second ledger. The runtime owns persistence.

The existing Costs & usage projection invokes the same Python resolver for
historical unknowns. It may also estimate previously unpriced known usage after
a price-table update. This read-only recovery does not backfill historical files.
Provider-reported cost always wins. Updated estimates correct cache accounting
using the provider's input semantics, rather than comparing count magnitudes.

## Evidence contract

A provider session ID and both ends of the run window are required. Older
metadata may recover the start from its canonical control-plane snapshot only
when both records name the same provider session. New settlements persist it. Parent/runtime
session IDs are refused before reading evidence. Missing timestamps are not
replaced with file modification times. Wrong-session events and events after the
run are excluded. Logs without attributable measurements stay unknown.

`vibecrafted.usage.v1` remains the envelope. New harness records carry
`source: harness_log`, `counting_version: 2`, and `input_semantics: includes_cache`.
The flat `tokens_input` includes fresh input, cache reads, and cache creation;
`tokens_total` is that input plus output. `model_usage` contains disjoint
`fresh_input`, `cache_read`, `cache_creation`, and `output` buckets per model.
`reasoning` is informational, already included in the provider's billed counts.
Consumers must not add cache or reasoning to `tokens_total` again. Existing
stream records retain their original version and values.

Counts must be nonnegative integers, excluding booleans, fractions, strings,
and values above `2**53 - 1`. Present invalid optional fields reject the record.
Both component sums and aggregate sums are checked for overflow. Partial final
JSONL lines are ignored; each line read is capped at 8 MiB. Duplicate event IDs
use component maxima, so repeated streaming updates add only their increase.
When native records lack IDs, their complete timestamp-bearing JSON is hashed;
this does not infer identity from token counts alone.

## Provider coverage

| Provider         | Local evidence                                                    | Semantics and limits                                                                                                                                                                                                                       |
| ---------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Codex            | `$CODEX_HOME/sessions/YYYY/MM/DD/*<id>.jsonl`, default `~/.codex` | First `session_meta` owns the file; cumulative component maxima, minus the baseline before the run. Cache is a subset of input; reasoning is a subset of output. A resumed session without a baseline stays unknown.                       |
| Claude           | `~/.claude/projects/*/<id>.jsonl`                                 | `message.usage`, maxima per `message.id`; input, cache read and cache creation are disjoint. Models are attributed per message.                                                                                                            |
| Kimi             | `~/.kimi-code/sessions/*/session_<id>/[agents/*/]wire.jsonl`      | Only `usage.record` with `usageScope: turn`; `inputOther`, `inputCacheRead`, `inputCacheCreation`, `output`. Session-scoped child-agent wires are included. Claude-shaped Kimi messages use the same message parser and model attribution. |
| Grok             | `~/.grok/logs/unified*.jsonl`                                     | `msg: shell.turn.inference_done`, exact `sid`, timestamp `ts`, counts in `ctx`. Cache is a subset of prompt; reasoning is not added to total.                                                                                              |
| Junie            | `~/.junie/sessions/<id>/events.jsonl`                             | `event.agentEvent.kind: LlmResponseMetadataEvent`, per-model `modelUsage` components, timestamp `timestampMs`. Newer transcript-only sessions without these records remain unknown.                                                        |
| Cursor           | `~/.cursor/projects/*/agent-transcripts/<id>[/<id>].jsonl`        | Timestamped `result.usage` counts when present. Inspected local chat transcripts carried no usage. Text length is never substituted.                                                                                                       |
| Agy / Gemini CLI | `~/.gemini/tmp/*/chats/session-*.json`                            | Exact `sessionId`, message IDs, measured `tokens`. Output includes `thoughts` once. Nonzero tool-token shapes whose billing semantics are not established are refused. Antigravity IDE protobuf is not decoded or estimated.               |
| Copilot          | `~/.copilot/session-state/<id>/events.jsonl`                      | `session.shutdown.data.modelMetrics.*.usage`, only when the whole session fits the run window. Whole-session summaries cannot attribute resumed run usage. `currentTokens` and context limits are not consumption.                         |

Empirical shapes were checked locally on 2026-09-28. Tests contain redacted
reconstructions, not private transcripts. Missing supported files and
unmeasured formats are expected reasons for residual unknowns, not zeros.

## Prices

Prices are API-equivalent estimates, not subscription invoices. Rates are USD
per million tokens. `_PRICES` entries carry a dated source label. Unrecognized
models appear in `unpricedModels`; a mixed-model run with an unpriced model
has unknown cost rather than a misleading partial dollar sum.

Sources checked 2026-09-28:

- [OpenAI API pricing](https://platform.openai.com/pricing): GPT-6 Astra,
  input 10, cache read 1, output 50.
- [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing):
  version-specific Fable, Mythos, Opus, Sonnet and Haiku rates. Cache creation uses the
  standard five-minute rate. One-hour writes, fast mode, geography, and other
  billing modifiers are not reconstructed; these remain baseline estimates.
- [Grok 4.6](https://docs.x.ai/developers/models/grok-4.6) and
  [Grok 4.7](https://docs.x.ai/developers/models/grok-4.7): input 2,
  cache read 0.50, output 6. Long-context premium is not reconstructed.
  [Grok 4.5](https://docs.x.ai/developers/pricing) uses 2 / 0.30 / 6.
  Explicit Cursor and reasoning-effort spellings map to the base model for
  this API-equivalent estimate; subscription and fast-tier modifiers are excluded.
- [Kimi K3 announcement](https://forum.moonshot.ai/t/kimi-k3-is-here-our-most-capable-model/480):
  input 3, cache read 0.30, output 15; Kimi creation input uses the input rate.

## Acceptance boundary

Source tests cover process exit through supervisor settlement and the existing
Rust dashboard projection. Installation/admission remains an integrator action.
No test result or read-only historical projection proves the installed desktop
runtime uses this branch. No existing run metadata is rewritten by this change.
